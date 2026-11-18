"""内联图片的判定。

扫富文本找文件必然连带把页面 banner、logo 收进来。要把它们和"教授贴出来的
图片资料"分开，唯一可靠的信号是 **file_id 出现在什么标签里**：
`<img src>` 是渲染出来的装饰图，`<a href>` 是要用户点开的资料。

真实库上的验证（19 门课 2064 个文件）：248 个文件只出现在 `<img>` 里，
且全部是图片；1622 个正文链接文件没有一个被误判成内联图。
"""

from app.models.file import SourceKind
from app.services.file_collector import (
    CollectedFile,
    CollectedSource,
    FileCollector,
    scan_body_file_ids,
)


def test_img_tag_and_link_are_separated():
    body = """
      <p><img src="/courses/1/files/100/preview" alt="banner"></p>
      <p><a href="/courses/1/files/200">Week 1 讲义</a></p>
    """
    inline, other = scan_body_file_ids(body)
    assert inline == {100}
    assert other == {200}


def test_file_in_both_img_and_link_counts_as_material():
    """"点开看大图"是常见写法 —— 缩略图外面包一个超链接。这类不算装饰图。"""
    body = '<a href="/files/300"><img src="/files/300/preview"></a>'
    inline, other = scan_body_file_ids(body)
    assert inline == set()
    assert other == {300}


def test_bare_file_link_without_course_prefix():
    inline, other = scan_body_file_ids('见 <a href="/files/42">附件</a>')
    assert (inline, other) == (set(), {42})


def test_empty_body_scans_clean():
    assert scan_body_file_ids("") == (set(), set())
    assert scan_body_file_ids("<p>这周没有材料</p>") == (set(), set())


def _collector(include_inline: bool) -> FileCollector:
    return FileCollector(
        client=None,  # 这几个用例只走本地判定，不发请求
        course_canvas_id=1,
        course_name="TEST101",
        include_inline_images=include_inline,
    )


def _entry(file_id: int, *via_img: bool) -> CollectedFile:
    return CollectedFile(
        canvas_file_id=file_id,
        sources=[
            CollectedSource(source_kind=SourceKind.PAGE, source_key=f"{file_id}:{i}", via_img=flag)
            for i, flag in enumerate(via_img)
        ],
    )


def test_only_all_img_sources_count_as_inline():
    """一个文件只要在别处以资料形式露过面，就不是装饰图 —— 哪怕它同时是某页的 banner。"""
    c = _collector(include_inline=True)
    c.files = {
        1: _entry(1, True),  # 纯 banner
        2: _entry(2, True, True),  # 在两个页面里都是 banner
        3: _entry(3, True, False),  # 一处 banner，一处是正文链接
        4: _entry(4, False),  # 正文链接
    }
    c._flag_inline_images()
    assert c.inline_image_ids == {1, 2}
    # 开着开关时只做标记，不丢文件
    assert set(c.files) == {1, 2, 3, 4}


def test_inline_images_dropped_when_setting_off():
    c = _collector(include_inline=False)
    c.files = {1: _entry(1, True), 2: _entry(2, True, False)}
    c._flag_inline_images()
    assert c.inline_image_ids == {1}
    assert set(c.files) == {2}


def test_file_without_sources_is_not_inline():
    """来源为空是异常状态，不能因为"所有来源都是 img"这个空真而被误删。"""
    c = _collector(include_inline=False)
    c.files = {1: CollectedFile(canvas_file_id=1, sources=[])}
    c._flag_inline_images()
    assert c.inline_image_ids == set()
    assert set(c.files) == {1}
