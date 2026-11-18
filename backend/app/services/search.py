"""资源搜索：容错的匹配 + 命中在哪儿。

**为什么不能只做 `display_name LIKE '%q%'`。** 每门课每个教授的命名习惯都不一样，
同一份东西可能叫 `PS1.pdf`、`Problem Set 1.pdf`、`ps-1-fall2026.pdf`、`PSet01.pdf`。
用户记得的是"第一次作业"，敲进去的是 `ps1`，而 LIKE 要求他先猜对标点和大小写。
所以这里把两边都**规范化**之后再比：大小写、标点、全角半角、重音、驼峰、
字母数字之间的边界、数字前导零，全部抹平。

**还要搜文件名以外的东西。** 教授经常把一整周的资料挂在一个叫 "Week 3" 的模块下、
或者贴在一个叫 "Lecture Notes" 的页面里，而文件本身叫 `l3.pdf` —— 用户记得的是
那个页面/文件夹的名字，不是文件名。所以文件夹路径、模块名、页面标题、公告标题、
作业标题、链接地址全都进搜索范围，并且**把命中在哪儿一起返回** ——
搜 `week 3` 出来一个叫 `l3.pdf` 的文件，得有个说法，不然像是搜错了。

匹配在 Python 里做而不是 SQL：一次只处理一门课（实测每门课几十到几百条），
而 SQLite 里没有可用的规范化和模糊匹配。想在 SQL 里做就得再维护一列
规范化文本，而它满足不了"命中在哪个字段"这个需求。
"""

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

# ---------- 字段 ----------
#
# 一次命中落在哪种东西上。前端按这个词给出"为什么这条会出现在结果里"，
# 所以它必须是用户在界面上认得的概念（文件夹 / 页面 / 模块 / 公告…）

NAME = "name"
FILENAME = "filename"
FOLDER = "folder"
MODULE = "module"
PAGE = "page"
ANNOUNCEMENT = "announcement"
ASSIGNMENT = "assignment"
QUIZ = "quiz"
SYLLABUS = "syllabus"
DISCUSSION = "discussion"
NAV = "nav"
URL = "url"

#: 各字段的权重。名字命中远比"它挂在某个模块下"值钱 —— 否则搜 `week 3` 时，
#: 一个叫 `Week 3 Slides.pdf` 的文件会和同模块下另外二十份资料排成一样的分数
WEIGHTS: dict[str, float] = {
    NAME: 3.0,
    FILENAME: 2.4,
    FOLDER: 1.6,
    PAGE: 1.6,
    MODULE: 1.4,
    ANNOUNCEMENT: 1.2,
    ASSIGNMENT: 1.2,
    QUIZ: 1.2,
    SYLLABUS: 1.2,
    DISCUSSION: 1.2,
    NAV: 1.2,
    URL: 1.0,
}

#: 每种来源的 (容器名算哪个字段, 条目标题算哪个字段)。
#: 页面这一路两者是两种东西：容器是模块名、标题是页面名，混成一个词就没法
#: 告诉用户"命中的是页面还是模块"
_SOURCE_FIELDS: dict[str, tuple[str, str]] = {
    "files": (FOLDER, FOLDER),
    "module": (MODULE, MODULE),
    "page": (MODULE, PAGE),
    "announcement": (ANNOUNCEMENT, ANNOUNCEMENT),
    "assignment": (ASSIGNMENT, ASSIGNMENT),
    "quiz": (QUIZ, QUIZ),
    "syllabus": (SYLLABUS, SYLLABUS),
    "discussion": (DISCUSSION, DISCUSSION),
    "nav": (NAV, NAV),
}

#: `folder:week3` 这种限定词认哪些写法。中文也收：界面是双语的，
#: 用中文界面的人不该被迫敲英文关键字
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": (NAME, FILENAME),
    "名称": (NAME, FILENAME),
    "file": (FILENAME, NAME),
    "文件名": (FILENAME, NAME),
    "folder": (FOLDER,),
    "文件夹": (FOLDER,),
    "path": (FOLDER,),
    "路径": (FOLDER,),
    "page": (PAGE,),
    "页面": (PAGE,),
    "module": (MODULE,),
    "模块": (MODULE,),
    "announcement": (ANNOUNCEMENT,),
    "公告": (ANNOUNCEMENT,),
    "assignment": (ASSIGNMENT,),
    "作业": (ASSIGNMENT,),
    "link": (URL,),
    "url": (URL,),
    "链接": (URL,),
    # "来源" = 文件名之外的一切位置。记得"在某个页面/模块/公告里见过"但
    # 想不起是哪一种的时候用这个
    "source": (FOLDER, PAGE, MODULE, ANNOUNCEMENT, ASSIGNMENT, QUIZ, SYLLABUS, DISCUSSION, NAV),
    "来源": (FOLDER, PAGE, MODULE, ANNOUNCEMENT, ASSIGNMENT, QUIZ, SYLLABUS, DISCUSSION, NAV),
}

# ---------- 规范化 ----------

#: 驼峰的接缝：`MidtermReview` -> `midterm review`，这样搜 `midterm review` 能命中。
#: 连续大写不切（`PDF`、`CS101` 不该被拆开）
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
#: 字母和数字的接缝：`PS1` -> `ps 1`、`Week03` -> `week 03`。
#: 反过来搜 `ps 1` 也能命中 `PS1` —— 两边用的是同一套规则
_ALNUM_EDGE = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
#: 数字的前导零：`week 03` 和 `week 3` 是同一个意思
_LEADING_ZEROS = re.compile(r"\b0+(\d)")


@dataclass(frozen=True)
class Analyzed:
    """一段文本的几种可比形式。

    三种形式对应三种"用户可能怎么敲"：
    - `norm` 抹平大小写/标点/驼峰之后的样子，带空格
    - `slim` 再抹掉数字前导零（`week 03` -> `week 3`）
    - `compact` 连空格都去掉（`ps1` 要能命中 `P.S. 1`，中文也靠它 ——
      中文词之间本来就没有空格）
    """

    norm: str
    slim: str
    compact: str
    words: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not self.norm


_EMPTY = Analyzed("", "", "", ())


@lru_cache(maxsize=16384)
def analyze(text: str, *, split_alnum: bool = True) -> Analyzed:
    """规范化一段文本。

    带缓存：模块名、文件夹路径在一门课里会重复几十上百次，而这个函数是纯的。

    `split_alnum=False` 给链接地址用。URL 里的随机串（`/watch/6f3a1b2c`）
    一旦在字母数字之间断开就凭空多出一堆假词首，搜 `a1` 会命中
    `6f3a1b2c` —— 而 URL 上本来就没有"驼峰命名"这回事，拆它没有收益。
    """
    if not text:
        return _EMPTY
    # NFKD + 去掉组合记号：全角半角、`ﬁ` 这类合字、`é` -> `e` 一起解决
    s = unicodedata.normalize("NFKD", text)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _CAMEL.sub(" ", s)
    if split_alnum:
        s = _ALNUM_EDGE.sub(" ", s)
    s = s.casefold()
    # 标点、下划线、连字符、各种括号统统当分隔符。`isalnum()` 而不是白名单：
    # 中日韩文字、希腊字母都得留着
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    norm = " ".join(s.split())
    if not norm:
        return _EMPTY
    slim = _LEADING_ZEROS.sub(r"\1", norm)
    return Analyzed(norm, slim, norm.replace(" ", ""), tuple(norm.split()))


# ---------- 查询 ----------


@dataclass(frozen=True)
class Term:
    """查询里的一项。

    `-week1` 是排除，`folder:lectures` 只在文件夹路径上找，`"problem set"`
    是词组（不做模糊、不做去空格匹配，用户加引号就是想要精确一点）。
    """

    text: Analyzed
    fields: tuple[str, ...] | None = None  # None = 所有字段
    negated: bool = False
    phrase: bool = False


#: 加引号的词组，或一段不带空格的普通词（可带 `-` 前缀和 `字段:` 前缀）
_TOKEN = re.compile(r'(-?)(?:([A-Za-z一-鿿]+):)?(?:"([^"]*)"|(\S+))')


def parse_query(q: str | None) -> list[Term]:
    """把用户敲的一行字拆成若干项。**项之间是并且** ——
    搜 `week 3 slides` 要的是同时满足的，不是"有任意一个词"的一大堆。

    语法认不出来（`foo:bar` 里 `foo` 不是字段名）就整段当普通词，
    不报错：用户敲的可能真的是 `http://…` 或者 `10:30`。
    """
    if not q or not q.strip():
        return []
    terms: list[Term] = []
    for neg, prefix, quoted, bare in _TOKEN.findall(q):
        raw = quoted if quoted else bare
        fields: tuple[str, ...] | None = None
        if prefix:
            fields = FIELD_ALIASES.get(prefix.casefold())
            if fields is None:
                # 不是字段名，那 `prefix:raw` 整体就是用户要搜的东西
                raw = f"{prefix}:{raw}"
        text = analyze(raw)
        if text.empty:
            continue
        terms.append(
            Term(text=text, fields=fields, negated=bool(neg), phrase=bool(quoted))
        )
    return terms


# ---------- 匹配 ----------


@dataclass(frozen=True)
class Field:
    """文件身上一处可搜的文本。`raw` 留着原样，命中时要显示给用户看。"""

    kind: str
    raw: str
    text: Analyzed


@dataclass(frozen=True)
class Hit:
    """一次命中落在哪儿。前端拿它在行上写一句"为什么这条在这儿"。"""

    field: str
    text: str


@dataclass
class Match:
    score: float
    hits: list[Hit] = field(default_factory=list)


def fields_of(file, sources) -> list[Field]:
    """一个文件的全部可搜文本，按字段去重。

    一份资料同时挂在十几个页面下是常态，而那些页面的模块名往往是同一个 ——
    去重之后分数不会被"挂得多"这件事带偏。
    """
    out: list[Field] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, raw: str | None) -> None:
        if not raw:
            return
        text = analyze(raw, split_alnum=kind != URL)
        if text.empty or (kind, text.norm) in seen:
            return
        seen.add((kind, text.norm))
        out.append(Field(kind=kind, raw=raw, text=text))

    add(NAME, file.display_name)
    # 文件名和显示名常常是同一个东西，重复一遍只会让它多算一份权重
    if file.filename and analyze(file.filename).norm != analyze(file.display_name).norm:
        add(FILENAME, file.filename)
    add(FOLDER, file.canvas_folder_path)
    add(URL, file.external_url)
    for s in sources:
        container_kind, item_kind = _SOURCE_FIELDS.get(s.source_kind, (MODULE, MODULE))
        add(container_kind, s.container_name)
        add(item_kind, s.item_title)
    return out


def _bounded_distance(a: str, b: str, limit: int) -> int:
    """限定上界的编辑距离（含相邻换位）。超过 limit 就返回 limit + 1。

    换位要算一次而不是两次：`lectrue` -> `lecture` 是最常见的手误，
    按普通编辑距离它是 2，和"错了两个字母"同罪就太严了。
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        lo, hi = max(1, i - limit), min(len(b), i + limit)
        # 窗口外的格子填成"不可达"，省掉大半个矩阵
        for j in range(1, lo):
            cur[j] = limit + 1
        for j in range(hi + 1, len(b) + 1):
            cur[j] = limit + 1
        for j in range(lo, hi + 1):
            cost = 0 if ca == b[j - 1] else 1
            best = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == b[j - 1]:
                best = min(best, prev2[j - 2] + 1)
            cur[j] = best
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[len(b)]


def _fuzzy_limit(n: int) -> int:
    """多长的词容许错几个。短词不容错 —— `ps` 和 `cs` 差一个字母，
    但它们是两门完全不同的课。"""
    if n < 4:
        return 0
    return 1 if n < 8 else 2


def _at_word_start(hay: str, needle: str) -> bool:
    return hay.startswith(needle) or f" {needle}" in hay


def _score(term: Term, text: Analyzed, *, strict: bool = False) -> float:
    """一项在一段文本上的得分。0 = 没命中。

    分数的高低顺序就是"用户有多可能是在找这个"：整段就是它 > 从词首开始 >
    出现在中间 > 抹掉空格才对上 > 拼错了一个字母。

    `strict` 给链接地址用：URL 里有大段随机串（`/watch/6f3a1b2c…`），
    在里面找两三个字母必然撞上，所以那一路只认词首、也不做模糊。
    """
    if text.empty:
        return 0.0
    q, t = term.text, text
    if q.norm == t.norm:
        return 18.0
    # 一两个字母的英文词出现在别的词**中间**基本都是噪声（搜 `1` 命中 `2013`），
    # 所以只认词首。中文不受此限：中文词之间本来就没有空格，
    # "讲义" 出现在 "第三周讲义" 中间正是它该被找到的样子
    short = len(q.compact) <= 2 and q.compact.isascii()
    best = 0.0
    # 两种形式各比一次：`week 3` 要能命中 `Week 03`（抹掉前导零之后才在词首）
    for hay, needle, base in ((t.norm, q.norm, 10.0), (t.slim, q.slim, 9.0)):
        if needle not in hay:
            continue
        if _at_word_start(hay, needle):
            # 从词首开始的命中更可能是用户要的：搜 `lec` 时 `Lecture 1` 该排在
            # `Syllabus (relec).pdf` 前面
            best = max(best, base + 2.0)
        elif not (short or strict):
            best = max(best, base)
    if strict:
        # 链接地址：`cs101` 这种带数字的词在 URL 里是**一个**词（上面没拆它），
        # 而查询里的同一个词已经被拆成 `cs 101` —— 拿去空格的形式对一次词首，
        # 两边才说得上同一件事
        if any(w.startswith(q.compact) for w in t.words):
            best = max(best, 11.0)
        return best
    if best:
        return best
    if term.phrase:
        # 加了引号就到此为止：用户明确要求精确一点
        return 0.0
    if not short and q.compact in t.compact:
        return 7.0
    limit = _fuzzy_limit(len(q.compact))
    if limit == 0:
        return 0.0
    # 逐词比，而不是拿整段比：一个词拼错了不该被整段的长度淹掉
    for word in t.words:
        if abs(len(word) - len(q.compact)) <= limit and (
            _bounded_distance(q.compact, word, limit) <= limit
        ):
            return 5.0
    # 多词查询里的一项也可能跨词（`problemset` vs `problem set`），
    # 所以再拿去空格的整段兜一次底
    if len(q.compact) >= 6 and _bounded_distance(q.compact, t.compact, limit) <= limit:
        return 4.0
    return 0.0


def match(fields: list[Field], terms: list[Term]) -> Match | None:
    """整份资料对整条查询的匹配结果。不命中返回 None。

    **每一项都要有着落**（并且），但**落在哪个字段不限**：搜 `week 3 slides`
    时，"week 3" 命中模块名、"slides" 命中文件名，这显然应该算命中 ——
    要求两项落在同一个字段上反而会把最常见的情形排除掉。
    """
    total = 0.0
    hits: list[Hit] = []
    seen: set[tuple[str, str]] = set()
    for term in terms:
        allowed = term.fields
        best = 0.0
        best_field: Field | None = None
        for f in fields:
            if allowed is not None and f.kind not in allowed:
                continue
            s = _score(term, f.text, strict=f.kind == URL)
            if s <= 0:
                continue
            # 限定了字段时按字段顺序给权重：`name:` 优先看显示名，回落到文件名
            weight = WEIGHTS.get(f.kind, 1.0)
            if allowed is not None and len(allowed) > 1 and f.kind in allowed:
                weight += (len(allowed) - allowed.index(f.kind) - 1) * 0.1
            if s * weight > best:
                best, best_field = s * weight, f
        if term.negated:
            if best > 0:
                return None
            continue
        if best == 0:
            return None
        total += best
        # 名字命中不用解释 —— 名字就在行上摆着。其余的要说清楚，
        # 否则搜 `week 3` 出来一个 `l3.pdf` 看着像是搜错了
        if best_field is not None and best_field.kind not in (NAME, FILENAME):
            key = (best_field.kind, best_field.raw)
            if key not in seen:
                seen.add(key)
                hits.append(Hit(field=best_field.kind, text=best_field.raw))
    if not terms:
        return None
    return Match(score=round(total, 3), hits=hits)
