"""星标和手动分组 —— 用户自己的数据。

核心约束就一条：**用户整理出来的东西不跟着同步走。** 自动分组每轮推倒重建，
用户的星标和分组一行都不能动。这个文件盯的是这条约束的几个具体后果：

- 文件改名了 → 用户那边跟着改（收藏的是文件，不是名字）
- 文件从 Canvas 上没了 → 用户的记录**原样留着**，只打一个标记
- 课程被取消同步 → 这才是唯一会真删用户数据的情况
"""

from sqlalchemy import select

from app.models.course import Course
from app.models.file import CourseFile
from app.models.userdata import STARRED_GROUP_ID, UserFile, UserGroup
from app.services import userdata


async def _course(db, canvas_id: int, name: str) -> Course:
    existing = (
        await db.execute(select(Course).where(Course.canvas_id == canvas_id))
    ).scalar_one_or_none()
    if existing:
        return existing
    course = Course(canvas_id=canvas_id, name=name)
    db.add(course)
    await db.commit()
    return course


async def _file(db, course: Course, canvas_id: int, name: str) -> CourseFile:
    existing = (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == canvas_id))
    ).scalar_one_or_none()
    if existing:
        return existing
    f = CourseFile(canvas_id=canvas_id, course_id=course.id, display_name=name)
    db.add(f)
    await db.commit()
    return f


async def _fresh(db) -> None:
    """丢掉这个 session 的快照，重新从库里读 —— 接口是在另一个 session 里写的。

    注意：这之后 session 里所有 ORM 对象都过期了，再碰 `obj.id` 会触发一次
    同步的懒加载，在 async 下直接抛 MissingGreenlet。所以用例要先把需要的 id
    存成普通 int，之后只用 id。
    """
    await db.rollback()
    db.expire_all()


# ---------- 星标 ----------


async def test_star_shows_up_everywhere_not_just_in_the_group_view(client, db):
    """星标要在**任何**视图上都看得见 —— 不能切到「我的分组」才知道标过。"""
    course = await _course(db, 6001, "星标测试")
    f1 = await _file(db, course, 60010, "讲义.pdf")
    await _file(db, course, 60011, "作业.pdf")

    await client.post(f"/api/v1/courses/{course.id}/star", json={"file_ids": [f1.id]})

    everything = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    marks = {f["canvas_id"]: f["is_starred"] for f in everything}
    assert marks[60010] is True
    assert marks[60011] is False


async def test_starred_pseudo_group_lists_starred_files(client, db):
    course = await _course(db, 6002, "星标分组")
    f1 = await _file(db, course, 60020, "a.pdf")
    await _file(db, course, 60021, "b.pdf")
    await client.post(f"/api/v1/courses/{course.id}/star", json={"file_ids": [f1.id]})

    groups = (await client.get(f"/api/v1/courses/{course.id}/user-groups")).json()
    starred = groups[0]
    assert starred["id"] == STARRED_GROUP_ID
    assert starred["is_starred_group"] is True
    assert starred["file_count"] == 1
    # 星标永远排第一，且不算在用户自建的分组里
    assert all(not g["is_starred_group"] for g in groups[1:])

    listed = (
        await client.get(
            f"/api/v1/courses/{course.id}/files?user_group_id={STARRED_GROUP_ID}"
        )
    ).json()
    assert [f["canvas_id"] for f in listed] == [60020]


async def test_unstarring_cleans_up_the_row(client, db):
    """既没星标又不在任何分组里的记录要回收，否则库里攒一堆什么都不表示的空行。"""
    course = await _course(db, 6003, "回收测试")
    f1 = await _file(db, course, 60030, "a.pdf")
    cid, fid = course.id, f1.id

    await client.post(f"/api/v1/courses/{cid}/star", json={"file_ids": [fid]})
    await _fresh(db)
    assert (
        await db.execute(select(UserFile).where(UserFile.resource_key == "file:60030"))
    ).scalar_one()

    await client.post(f"/api/v1/courses/{cid}/star", json={"file_ids": [fid], "starred": False})
    await _fresh(db)
    assert (
        await db.execute(select(UserFile).where(UserFile.resource_key == "file:60030"))
    ).scalar_one_or_none() is None


async def test_file_in_a_group_survives_unstarring(client, db):
    """还在分组里的文件不能因为取消星标就被回收。"""
    course = await _course(db, 6004, "回收边界")
    f1 = await _file(db, course, 60040, "a.pdf")
    cid, fid = course.id, f1.id
    group = (
        await client.post(f"/api/v1/courses/{cid}/user-groups", json={"name": "组 A"})
    ).json()

    await client.post(f"/api/v1/courses/{cid}/star", json={"file_ids": [fid]})
    await client.post(f"/api/v1/user-groups/{group['id']}/files", json={"file_ids": [fid]})
    await client.post(f"/api/v1/courses/{cid}/star", json={"file_ids": [fid], "starred": False})

    await _fresh(db)
    entry = (
        await db.execute(select(UserFile).where(UserFile.resource_key == "file:60040"))
    ).scalar_one()
    assert entry.is_starred is False
    listed = (
        await client.get(f"/api/v1/courses/{cid}/files?user_group_id={group['id']}")
    ).json()
    assert len(listed) == 1


# ---------- 分组的增删改 ----------


async def test_group_rename_and_duplicate_name_is_rejected(client, db):
    course = await _course(db, 6005, "改名测试")
    a = (
        await client.post(f"/api/v1/courses/{course.id}/user-groups", json={"name": "组 A"})
    ).json()
    await client.post(f"/api/v1/courses/{course.id}/user-groups", json={"name": "组 B"})

    renamed = (
        await client.patch(f"/api/v1/user-groups/{a['id']}", json={"name": "复习资料"})
    ).json()
    assert renamed["name"] == "复习资料"

    # 重名要挡住，否则侧边栏出现两个一模一样的组，用户没法分辨
    clash = await client.patch(f"/api/v1/user-groups/{a['id']}", json={"name": "组 B"})
    assert clash.status_code == 409
    dup = await client.post(
        f"/api/v1/courses/{course.id}/user-groups", json={"name": "组 B"}
    )
    assert dup.status_code == 409


async def test_empty_group_name_is_rejected(client, db):
    course = await _course(db, 6006, "空名字")
    resp = await client.post(
        f"/api/v1/courses/{course.id}/user-groups", json={"name": "   "}
    )
    assert resp.status_code == 400


async def test_deleting_a_group_keeps_the_files_and_other_marks(client, db):
    """删组只删这个组。文件的星标、以及它在别的组里的归属都不受影响。"""
    course = await _course(db, 6007, "删组测试")
    f1 = await _file(db, course, 60070, "a.pdf")
    cid, fid = course.id, f1.id
    a = (
        await client.post(f"/api/v1/courses/{cid}/user-groups", json={"name": "组 A"})
    ).json()
    b = (
        await client.post(f"/api/v1/courses/{cid}/user-groups", json={"name": "组 B"})
    ).json()
    await client.post(f"/api/v1/user-groups/{a['id']}/files", json={"file_ids": [fid]})
    await client.post(f"/api/v1/user-groups/{b['id']}/files", json={"file_ids": [fid]})
    await client.post(f"/api/v1/courses/{cid}/star", json={"file_ids": [fid]})

    assert (await client.delete(f"/api/v1/user-groups/{a['id']}")).status_code == 204

    await _fresh(db)
    entry = (
        await db.execute(select(UserFile).where(UserFile.resource_key == "file:60070"))
    ).scalar_one()
    assert entry.is_starred is True
    still = (
        await client.get(f"/api/v1/courses/{cid}/files?user_group_id={b['id']}")
    ).json()
    assert len(still) == 1
    # 文件本身当然还在
    assert (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 60070))
    ).scalar_one_or_none() is not None


async def test_group_count_matches_what_the_list_actually_returns(client, db):
    """侧边栏的数字必须等于点进去看到的条数。

    两边口径不一样就会出现"显示 2 个文件，点进去只有 1 个" —— 数字一旦不可信，
    整个分组功能就没法用了。断了活指针的记录列表里出不来，计数也不能算。
    """
    course = await _course(db, 6017, "计数一致")
    f1 = await _file(db, course, 60170, "在的.pdf")
    f2 = await _file(db, course, 60171, "会断链的.pdf")
    cid = course.id
    group = (
        await client.post(f"/api/v1/courses/{cid}/user-groups", json={"name": "组"})
    ).json()
    await client.post(
        f"/api/v1/user-groups/{group['id']}/files", json={"file_ids": [f1.id, f2.id]}
    )

    listed = (
        await client.get(f"/api/v1/courses/{cid}/files?user_group_id={group['id']}")
    ).json()
    groups = (await client.get(f"/api/v1/courses/{cid}/user-groups")).json()
    assert next(g for g in groups if g["id"] == group["id"])["file_count"] == len(listed) == 2

    # 让其中一条断链（文件行没了、用户记录留着）
    await db.rollback()
    stale = (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 60171))
    ).scalar_one()
    await db.delete(stale)
    await db.commit()

    listed = (
        await client.get(f"/api/v1/courses/{cid}/files?user_group_id={group['id']}")
    ).json()
    groups = (await client.get(f"/api/v1/courses/{cid}/user-groups")).json()
    assert next(g for g in groups if g["id"] == group["id"])["file_count"] == len(listed) == 1


async def test_group_membership_is_reported_on_every_file(client, db):
    """文件所属的用户分组要跟着文件一起返回 —— 列表和详情都靠它显示。"""
    course = await _course(db, 6008, "归属显示")
    f1 = await _file(db, course, 60080, "a.pdf")
    a = (
        await client.post(f"/api/v1/courses/{course.id}/user-groups", json={"name": "组 A"})
    ).json()
    await client.post(f"/api/v1/user-groups/{a['id']}/files", json={"file_ids": [f1.id]})

    listed = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    row = next(f for f in listed if f["canvas_id"] == 60080)
    assert [g["name"] for g in row["user_groups"]] == ["组 A"]


async def test_cannot_add_a_file_from_another_course(client, db):
    """跨课程的 file_id 要挡住，否则一门课的分组里会混进别门课的文件。"""
    a = await _course(db, 6009, "课 A")
    b = await _course(db, 6010, "课 B")
    other = await _file(db, b, 60100, "别人的.pdf")
    group = (
        await client.post(f"/api/v1/courses/{a.id}/user-groups", json={"name": "组"})
    ).json()

    resp = await client.post(
        f"/api/v1/user-groups/{group['id']}/files", json={"file_ids": [other.id]}
    )
    assert resp.status_code == 400


# ---------- 同步后的对齐 ----------


async def test_renamed_file_updates_the_user_entry(client, db):
    """文件改名了，用户那边跟着改 —— 收藏的是这个文件，不是这个名字。"""
    course = await _course(db, 6011, "改名同步")
    f1 = await _file(db, course, 60110, "旧名字.pdf")
    await client.post(f"/api/v1/courses/{course.id}/star", json={"file_ids": [f1.id]})

    f1.display_name = "新名字.pdf"
    await db.commit()
    await userdata.relink_after_sync(db, course)
    await db.commit()

    await _fresh(db)
    entry = (
        await db.execute(select(UserFile).where(UserFile.resource_key == "file:60110"))
    ).scalar_one()
    assert entry.display_name == "新名字.pdf"


async def test_user_entry_survives_the_file_row_disappearing(client, db):
    """文件行没了，用户的记录必须活下来 —— 这是整块设计的目的。"""
    course = await _course(db, 6012, "文件消失")
    f1 = await _file(db, course, 60120, "会没的.pdf")
    await client.post(f"/api/v1/courses/{course.id}/star", json={"file_ids": [f1.id]})

    await db.delete(f1)
    await db.commit()
    await userdata.relink_after_sync(db, course)
    await db.commit()

    await _fresh(db)
    entry = (
        await db.execute(select(UserFile).where(UserFile.resource_key == "file:60120"))
    ).scalar_one_or_none()
    assert entry is not None, "用户的星标不能因为文件行没了就消失"
    assert entry.is_starred is True
    assert entry.file_id is None, "活指针要断开"
    assert entry.display_name == "会没的.pdf", "快照要留着，用户至少知道自己标的是什么"


async def test_relink_reconnects_when_the_file_comes_back(client, db):
    """文件重新同步回来（换了新的行 id）时要自动接上。"""
    course = await _course(db, 6013, "断了再接")
    f1 = await _file(db, course, 60130, "回来的.pdf")
    cid = course.id
    await client.post(f"/api/v1/courses/{cid}/star", json={"file_ids": [f1.id]})
    await db.delete(f1)
    await db.commit()
    await userdata.relink_after_sync(db, course)
    await db.commit()

    # 同一个 canvas file id，新的一行
    again = CourseFile(canvas_id=60130, course_id=cid, display_name="回来的.pdf")
    db.add(again)
    await db.commit()
    await userdata.relink_after_sync(db, course)
    await db.commit()
    again_id = again.id

    await _fresh(db)
    entry = (
        await db.execute(select(UserFile).where(UserFile.resource_key == "file:60130"))
    ).scalar_one()
    assert entry.file_id == again_id, "canvas file id 才是锚点，本地行 id 会变"


async def test_inline_image_purge_keeps_user_marked_files(client, db):
    """关掉内联图片时，用户星标过的那张不能真删 —— 改成打标记留着。"""
    from app.integrations.canvas.sync import CanvasSyncService

    course = await _course(db, 6014, "内联图保留")
    banner = CourseFile(
        canvas_id=60140, course_id=course.id, display_name="banner.png", is_inline_image=True
    )
    plain = CourseFile(
        canvas_id=60141, course_id=course.id, display_name="logo.png", is_inline_image=True
    )
    db.add_all([banner, plain])
    await db.commit()
    await client.post(f"/api/v1/courses/{course.id}/star", json={"file_ids": [banner.id]})

    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]
    await service._purge_inline_images(course, set())

    await _fresh(db)
    kept = (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 60140))
    ).scalar_one_or_none()
    assert kept is not None, "用户标过的文件不能被清理掉"
    assert kept.is_deleted_on_canvas is True, "要打上已从 Canvas 删除的标记"
    assert (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 60141))
    ).scalar_one_or_none() is None, "没被标记过的照常删"


async def test_deleted_file_still_listed_in_the_user_group(client, db):
    """打了删除标记的文件，在用户自己的分组里照常出现（带标记），
    但不出现在按 Canvas 位置看的普通视图里。"""
    course = await _course(db, 6015, "删除后仍可见")
    f1 = await _file(db, course, 60150, "没了的.pdf")
    group = (
        await client.post(f"/api/v1/courses/{course.id}/user-groups", json={"name": "组"})
    ).json()
    await client.post(f"/api/v1/user-groups/{group['id']}/files", json={"file_ids": [f1.id]})

    f1.is_deleted_on_canvas = True
    await db.commit()

    in_group = (
        await client.get(f"/api/v1/courses/{course.id}/files?user_group_id={group['id']}")
    ).json()
    assert [f["canvas_id"] for f in in_group] == [60150]
    assert in_group[0]["is_deleted_on_canvas"] is True

    normal = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    assert 60150 not in {f["canvas_id"] for f in normal}


# ---------- 课程被取消同步 ----------


async def test_unselecting_a_course_deletes_its_user_data(client, db):
    """唯一会真删用户数据的情况：用户自己说这门课不要了。"""
    from app.services import purge

    course = await _course(db, 6016, "取消同步")
    f1 = await _file(db, course, 60160, "a.pdf")
    cid, fid = course.id, f1.id
    group = (
        await client.post(f"/api/v1/courses/{cid}/user-groups", json={"name": "组"})
    ).json()
    await client.post(f"/api/v1/user-groups/{group['id']}/files", json={"file_ids": [fid]})
    await client.post(f"/api/v1/courses/{cid}/star", json={"file_ids": [fid]})

    await db.rollback()  # 接口在别的 session 里写过库了
    course = await db.get(Course, cid)
    await purge.purge_course_content(db, course)
    await db.commit()

    await _fresh(db)
    assert (
        await db.execute(select(UserGroup).where(UserGroup.course_id == cid))
    ).scalar_one_or_none() is None
    assert (
        await db.execute(select(UserFile).where(UserFile.course_id == cid))
    ).scalar_one_or_none() is None
