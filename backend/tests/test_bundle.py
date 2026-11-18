"""批量打包下载。

用户选中十几个文件想一次拿走。麻烦在于**本地通常没有文件本体** ——
默认不开自动下载，所以打包时要现去 Canvas 抓。由此来的三条约束：

- 一个文件失败不能让整包失败（链接过期、权限变了、文件被删都很常见）
- 失败的要让用户知道，否则解压出来少几个文件，不知道是漏选了还是下载失败
- 同名文件不能互相覆盖（Week 1/notes.pdf 和 Week 2/notes.pdf 都叫 notes.pdf）
"""

import io
import re
import zipfile

import pytest
from sqlalchemy import select

from app.models.course import Course
from app.models.file import CourseFile
from app.services import bundle


async def _course(db, canvas_id: int) -> Course:
    existing = (
        await db.execute(select(Course).where(Course.canvas_id == canvas_id))
    ).scalar_one_or_none()
    if existing:
        return existing
    course = Course(canvas_id=canvas_id, name=f"打包测试 {canvas_id}")
    db.add(course)
    await db.commit()
    return course


def _local_file(db, course: Course, canvas_id: int, name: str, body: str) -> CourseFile:
    """造一个"本地已有副本"的文件 —— 打包时应该直接用，不发网络请求。"""
    from app.services import storage

    path = storage.settings.storage_path / f"bundle-{canvas_id}" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    f = CourseFile(
        canvas_id=canvas_id,
        course_id=course.id,
        display_name=name,
        local_path=storage.relative_to_root(path),
    )
    db.add(f)
    return f


def test_duplicate_names_get_suffixed():
    """同名条目在 zip 里会互相覆盖，用户解压出来少一个还不知道。"""
    used: set[str] = set()
    assert bundle._safe_entry_name("notes.pdf", used) == "notes.pdf"
    assert bundle._safe_entry_name("notes.pdf", used) == "notes (2).pdf"
    assert bundle._safe_entry_name("notes.pdf", used) == "notes (3).pdf"
    # 没有扩展名的也要能处理
    assert bundle._safe_entry_name("README", used) == "README"
    assert bundle._safe_entry_name("README", used) == "README (2)"


def test_path_separators_are_stripped_from_entry_names():
    """文件名里带斜杠会在 zip 里变成目录结构，甚至能写到解压目录外面。"""
    used: set[str] = set()
    assert "/" not in bundle._safe_entry_name("a/b/c.pdf", used)
    assert bundle._safe_entry_name("../../etc/passwd", used).count("..") <= 1


async def test_bundle_packs_local_files(client, db, tmp_path):
    course = await _course(db, 5001)
    _local_file(db, course, 50010, "讲义.pdf", "AAA")
    _local_file(db, course, 50011, "作业.pdf", "BBB")
    await db.commit()

    files = (
        (await db.execute(select(CourseFile).where(CourseFile.course_id == course.id)))
        .scalars()
        .all()
    )
    zip_path = tmp_path / "out.zip"
    result = await bundle.build_zip(list(files), zip_path)

    assert result.written == 2
    assert result.failures == []
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted(zf.namelist()) == ["作业.pdf", "讲义.pdf"]
        assert zf.read("讲义.pdf") == b"AAA"


async def test_one_bad_file_does_not_sink_the_whole_zip(client, db, tmp_path):
    """一个文件挂掉，其余照常打包，失败原因写进包里的说明文件。"""
    course = await _course(db, 5002)
    _local_file(db, course, 50020, "好的.pdf", "OK")
    # 已从 Canvas 删除、又没有本地副本 —— 抓不到
    db.add(
        CourseFile(
            canvas_id=50021,
            course_id=course.id,
            display_name="没了的.pdf",
            is_deleted_on_canvas=True,
        )
    )
    await db.commit()

    files = (
        (await db.execute(select(CourseFile).where(CourseFile.course_id == course.id)))
        .scalars()
        .all()
    )
    zip_path = tmp_path / "out.zip"
    result = await bundle.build_zip(list(files), zip_path)

    assert result.written == 1
    assert len(result.failures) == 1 and "没了的.pdf" in result.failures[0]
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "好的.pdf" in names
        # 说明文件必须在，否则用户不知道少了什么
        assert bundle.NOTES_ENTRY in names
        assert "没了的.pdf" in zf.read(bundle.NOTES_ENTRY).decode()


async def test_endpoint_rejects_empty_and_oversized_requests(client, db):
    course = await _course(db, 5003)
    await db.commit()

    empty = await client.post(
        f"/api/v1/courses/{course.id}/files/bundle", json={"file_ids": []}
    )
    assert empty.status_code == 400

    too_many = await client.post(
        f"/api/v1/courses/{course.id}/files/bundle",
        json={"file_ids": list(range(1, 500))},
    )
    # 先撞上"文件不存在"或"超过上限"都算挡住了，不该真去打包 499 个
    assert too_many.status_code == 400


async def test_endpoint_returns_a_zip(client, db):
    course = await _course(db, 5004)
    _local_file(db, course, 50040, "a.pdf", "AAA")
    await db.commit()
    files = (
        (await db.execute(select(CourseFile).where(CourseFile.course_id == course.id)))
        .scalars()
        .all()
    )

    resp = await client.post(
        f"/api/v1/courses/{course.id}/files/bundle",
        json={"file_ids": [f.id for f in files]},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.content[:2] == b"PK"  # zip 魔数


async def test_response_reports_failures_and_a_timestamped_name(client, db):
    """两件事都是给用户看的：

    - 失败条数走 header —— 清单在包里的说明文件里，但用户不会先解压才发现少了东西，
      所以前端要靠这个数字在右下角弹提示。
    - 文件名带时间戳 + 课程 id —— 同一门课打好几次包时不会变成 course (1).zip。
    """
    course = await _course(db, 5007)
    _local_file(db, course, 50070, "好的.pdf", "OK")
    db.add(
        CourseFile(
            canvas_id=50071,
            course_id=course.id,
            display_name="没了的.pdf",
            is_deleted_on_canvas=True,
        )
    )
    await db.commit()
    files = (
        (await db.execute(select(CourseFile).where(CourseFile.course_id == course.id)))
        .scalars()
        .all()
    )

    resp = await client.post(
        f"/api/v1/courses/{course.id}/files/bundle",
        json={"file_ids": [f.id for f in files]},
    )
    assert resp.status_code == 200
    assert resp.headers["x-bundle-written"] == "1"
    assert resp.headers["x-bundle-failed"] == "1"

    name = resp.headers["content-disposition"].split("filename=")[-1].strip('"')
    assert re.fullmatch(rf"\d{{8}}-\d{{6}}-{course.id}\.zip", name), name


async def test_endpoint_rejects_files_from_another_course(client, db):
    """跨课程的 id 要挡住，否则能借这个接口打包别门课的文件。"""
    a = await _course(db, 5005)
    b = await _course(db, 5006)
    _local_file(db, b, 50060, "别人的.pdf", "X")
    await db.commit()
    other = (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 50060))
    ).scalar_one()

    resp = await client.post(
        f"/api/v1/courses/{a.id}/files/bundle", json={"file_ids": [other.id]}
    )
    assert resp.status_code == 400


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("bundle")


async def _link_row(db, course: Course, canvas_id: int, name: str, url: str) -> CourseFile:
    from app.models.file import Medium
    from app.services import links

    f = CourseFile(
        canvas_id=0,
        course_id=course.id,
        resource_key=links.resource_key(links.normalize(url)),
        medium=Medium.LINK,
        display_name=name,
        external_url=url,
    )
    db.add(f)
    await db.commit()
    return f


async def test_links_are_listed_in_the_notes_not_downloaded(client, db, tmp_path):
    """用户多选里混进链接是常态。链接没有本体可下，但他是**有意**选中的 ——
    不能默默丢掉，也不能去请求那个 URL 把一堆 HTML 塞进包里。"""
    course = await _course(db, 5008)
    _local_file(db, course, 50080, "讲义.pdf", "PDF")
    await db.commit()
    await _link_row(db, course, 50081, "Lecture 3 录播", "https://youtu.be/abc")

    files = (
        (await db.execute(select(CourseFile).where(CourseFile.course_id == course.id)))
        .scalars()
        .all()
    )
    zip_path = tmp_path / "out.zip"
    result = await bundle.build_zip(list(files), zip_path)

    assert result.written == 1
    assert result.failures == []  # 链接不算"下载失败"，那是两件事
    assert result.links == [("Lecture 3 录播", "https://youtu.be/abc")]
    with zipfile.ZipFile(zip_path) as zf:
        notes = zf.read(bundle.NOTES_ENTRY).decode()
        assert "Lecture 3 录播" in notes
        assert "https://youtu.be/abc" in notes  # 地址要给出来，否则说明没有用


async def test_no_notes_file_when_everything_worked(client, db, tmp_path):
    """全都下下来了就别往包里塞一个空说明。"""
    course = await _course(db, 5009)
    _local_file(db, course, 50090, "a.pdf", "A")
    await db.commit()
    files = (
        (await db.execute(select(CourseFile).where(CourseFile.course_id == course.id)))
        .scalars()
        .all()
    )
    zip_path = tmp_path / "out.zip"
    await bundle.build_zip(list(files), zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ["a.pdf"]


async def test_selecting_only_links_says_so_instead_of_download_failed(client, db):
    """全选中链接是很正常的误操作，别报成"一个都没下载成功"。"""
    course = await _course(db, 5010)
    a = await _link_row(db, course, 50100, "录播", "https://youtu.be/x")
    b = await _link_row(db, course, 50101, "大纲", "https://docs.google.com/y")

    resp = await client.post(
        f"/api/v1/courses/{course.id}/files/bundle", json={"file_ids": [a.id, b.id]}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "bundle_all_links"


async def test_link_count_comes_back_in_a_header(client, db):
    course = await _course(db, 5011)
    _local_file(db, course, 50110, "a.pdf", "A")
    await db.commit()
    link = await _link_row(db, course, 50111, "录播", "https://youtu.be/z")
    files = (
        (await db.execute(select(CourseFile).where(CourseFile.course_id == course.id)))
        .scalars()
        .all()
    )

    resp = await client.post(
        f"/api/v1/courses/{course.id}/files/bundle",
        json={"file_ids": [f.id for f in files]},
    )
    assert resp.status_code == 200
    assert resp.headers["x-bundle-written"] == "1"
    assert resp.headers["x-bundle-links"] == "1"
    assert resp.headers["x-bundle-failed"] == "0"
    assert link.id  # 用到它，避免 lint 抱怨未使用


# ---------- 跨课程打包（全局资源页） ----------
#
# 走的是**同一段实现**（`files.py:bundle_response` → `services/bundle.py`）：
# 怎么抓、失败怎么报、包名怎么起都不能有第二套。唯一的区别是按课程分目录。


async def test_cross_course_bundle_puts_each_course_in_its_own_folder(client, db):
    """六门课各有一份 syllabus.pdf 是常态。平铺在一个包里会变成
    `syllabus.pdf` / `syllabus (2).pdf`，用户认不出哪个是哪门课的。"""
    a = await _course(db, 5020)
    b = await _course(db, 5021)
    # 课程代码里带 `/` 是真的（STAT200H1 S LEC0101/LEC0201），不洗掉会多一层目录
    a.course_code = "AAA/101"
    b.course_code = "BBB202"
    _local_file(db, a, 50200, "syllabus.pdf", "A")
    _local_file(db, b, 50210, "syllabus.pdf", "B")
    await db.commit()
    files = (
        (
            await db.execute(
                select(CourseFile).where(CourseFile.course_id.in_([a.id, b.id]))
            )
        )
        .scalars()
        .all()
    )

    resp = await client.post(
        "/api/v1/resources/bundle", json={"file_ids": [f.id for f in files]}
    )
    assert resp.status_code == 200
    assert resp.headers["x-bundle-written"] == "2"

    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert sorted(names) == ["AAA_101/syllabus.pdf", "BBB202/syllabus.pdf"]


async def test_single_course_selection_stays_flat(client, db):
    """只选了一门课的东西就不分目录 —— 那时目录名只会是同一个词，白加一层。"""
    course = await _course(db, 5022)
    _local_file(db, course, 50220, "only.pdf", "X")
    await db.commit()
    files = (
        (await db.execute(select(CourseFile).where(CourseFile.course_id == course.id)))
        .scalars()
        .all()
    )

    resp = await client.post(
        "/api/v1/resources/bundle", json={"file_ids": [f.id for f in files]}
    )
    assert resp.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(resp.content)).namelist() == ["only.pdf"]


async def test_cross_course_bundle_name_is_just_the_timestamp(client, db):
    """课程页的包名带课程 id，全局页没有"那一门课"可带，就只有时间。"""
    course = await _course(db, 5023)
    _local_file(db, course, 50230, "a.pdf", "A")
    await db.commit()
    file = (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 50230))
    ).scalar_one()

    resp = await client.post("/api/v1/resources/bundle", json={"file_ids": [file.id]})
    name = resp.headers["content-disposition"].split("filename=")[-1].strip('"')
    assert re.fullmatch(r"\d{8}-\d{6}\.zip", name), name


async def test_cross_course_bundle_reports_links_and_failures_the_same_way(client, db):
    """同一个按钮在两个页面上的行为必须一致：链接和失败分开报，一个都不能少。"""
    course = await _course(db, 5024)
    _local_file(db, course, 50240, "好的.pdf", "OK")
    db.add(
        CourseFile(
            canvas_id=50241,
            course_id=course.id,
            display_name="没了的.pdf",
            is_deleted_on_canvas=True,
        )
    )
    await db.commit()
    await _link_row(db, course, 50242, "录播", "https://youtu.be/q")
    files = (
        (await db.execute(select(CourseFile).where(CourseFile.course_id == course.id)))
        .scalars()
        .all()
    )

    resp = await client.post(
        "/api/v1/resources/bundle", json={"file_ids": [f.id for f in files]}
    )
    assert resp.status_code == 200
    assert resp.headers["x-bundle-written"] == "1"
    assert resp.headers["x-bundle-failed"] == "1"
    assert resp.headers["x-bundle-links"] == "1"
    # 说明文件也照样在包里
    assert bundle.notes_entry("zh") in zipfile.ZipFile(io.BytesIO(resp.content)).namelist()


async def test_cross_course_bundle_rejects_unknown_ids(client, db):
    """课程页挡的是"别门课的 id"，全局页没有课程边界，但不存在的 id 一样要挡 ——
    静默成功会让用户以为下到了东西。"""
    resp = await client.post("/api/v1/resources/bundle", json={"file_ids": [10**9]})
    assert resp.status_code == 404
    empty = await client.post("/api/v1/resources/bundle", json={"file_ids": []})
    assert empty.status_code == 400
