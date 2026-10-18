r"""参考文献核对：字段完整性 + 类型统一。

字段要求来自两处，不是凭空定的：

  ① 用户明确要求
     · 期刊文章 → 期号(number)、卷号(volume)、页码(pages)
     · 书籍章节 → 页码(pages)、出版地(address)、出版商(publisher)
     · 书籍     → 出版地(address)、出版商(publisher)
     · 学位论文 / 会议论文 → 按往期写法统一样式

  ② 全库 193 个 bib（约 9000 条）实测的出现率
     article       4719 条：author 100% title 100% year 100%
                            journal 98% pages 94% volume 87% number 75%
     book          2410 条：title 100% author 99% year 99% publisher 99% address 61%
     incollection   911 条：title/author/booktitle 100% year 99%
                            publisher 95% editor 81% pages 68%
     inproceedings  736 条：author/title 100% year 90% booktitle 88% pages 78%
     thesis          37 条：author/title 100% type 97% institution 92%
     phdthesis       82 条：author/title/year 100% school 93%

类型统一（往期写法）
--------------------
  中文学位论文 → @thesis + {author, title, type, institution, date}
                 type 写「硕士学位论文」/「博士论文」/「博士学位论文」
  英文学位论文 → @phdthesis / @mastersthesis + {author, title, school, year}
  会议论文     → @inproceedings + {author, title, booktitle, pages, year}
                 （@conference 统一改成 @inproceedings）

结果写成人可读的核对报告，**不擅自删改内容**——缺什么就报什么，由编辑补。

两个**独立的**入口（2026-10-15 加，见文件里「编辑部硬规则」一节的注释）：
  · `author_form_issues()`        —— 英文 author/editor 未倒装 = **must**
  · `translation_marking_issues()` —— 中文译著缺「（著）/（译）」标记 = **nice 提示**
它们**不进** `check()`：`check()` 的口径被冻结基线 `out/docx_audit.csv` 用着，不能动。
"""

from __future__ import annotations

import re

# 第 84 条：SEP 词条的判据与常量只有一份（在 bibnorm 里，规范化也用同一份）
# 第 133 条：整行 `%` 注释的口径也只有一份（`strip_bib_comments`；2 个 split_entries 共用）
from .bibnorm import is_sep_entry, strip_bib_comments  # noqa: E402
from dataclasses import dataclass, field

# ── 各类型的必备字段 ──────────────────────────────────────────────────────
#
# 分两档：
#   must —— 缺了必须补（用户明确要求 / 人工惯例里出现率很高）
#   nice —— 建议补（人工也经常没有，补上更好）
REQUIRED: dict[str, dict[str, list[str]]] = {    "article": {
        "must": ["author", "title", "year", "journal", "volume", "number", "pages"],
        # doi 不收（编辑部 2026-10-14：只用于核实，不写进 bib）
        "nice": [],
    },
    "book": {
        "must": ["author", "title", "year", "publisher", "address"],
        # isbn 同理不收
        "nice": ["edition"],
    },
    "incollection": {
        "must": ["author", "title", "booktitle", "year", "publisher", "pages"],
        "nice": ["editor", "address"],
    },
    "inbook": {
        "must": ["author", "title", "booktitle", "year", "publisher", "pages"],
        "nice": ["editor", "address"],
    },
    "inproceedings": {
        "must": ["author", "title", "booktitle", "year", "pages"],
        "nice": ["publisher", "address", "editor"],
    },
    "proceedings": {
        "must": ["title", "year", "publisher"],
        "nice": ["editor", "address"],
    },
    "thesis": {  # 中文学位论文写法
        "must": ["author", "title", "type", "institution"],
        "nice": ["date", "year"],
    },
    "phdthesis": {
        "must": ["author", "title", "school", "year"],
        "nice": ["address", "type"],
    },
    "mastersthesis": {
        "must": ["author", "title", "school", "year"],
        "nice": ["address", "type"],
    },
    "techreport": {"must": ["author", "title", "institution", "year"], "nice": ["number"]},
    "report": {"must": ["author", "title", "year"], "nice": ["institution", "number"]},
    "online": {"must": ["title", "url", "urldate"], "nice": ["author", "year"]},
    "unpublished": {"must": ["author", "title", "note"], "nice": ["year"]},
    "misc": {"must": ["author", "title", "year"], "nice": []},
}

# 类型名统一（往期写法）
TYPE_ALIASES = {
    "conference": "inproceedings",
    "phdthesis": "phdthesis",
    "mastersthesis": "mastersthesis",
    "masters thesis": "mastersthesis",
    "electronic": "online",
    "www": "online",
    "webpage": "online",
}

# 无 year 时可用 date 代替的类型
_YEAR_LIKE = ("year", "date")

_ENT = re.compile(r"@(\w+)\s*\{\s*([^,]+),")


def split_entries(bib: str) -> list[tuple[str, str, str]]:
    r"""切成 [(类型, 键, 字段体), ...]，花括号配平感知。

    第 133 条（2026-10-18）：**整行 `%` 注释先剥掉**再拆（复用 `bibnorm.strip_bib_comments`，
    与 `bibnorm.split_entries()` 同一口径）——`% @misc{z16,` 这类注释掉的旧条目不再被算成
    条目；从前 checkbib / 核实范围 / 内容指纹 都会把不存在的条目当真。行内/字段值内的 `%`
    （`note={50\%}`）不受影响。
    """
    bib = strip_bib_comments(bib)
    from .bibnorm import _strip_non_bib_blocks
    bib = _strip_non_bib_blocks(bib)
    out = []
    for m in _ENT.finditer(bib):
        i = m.end()
        depth = 1
        j = i
        while j < len(bib) and depth > 0:
            if bib[j] == "{":
                depth += 1
            elif bib[j] == "}":
                depth -= 1
            j += 1
        out.append((m.group(1), m.group(2).strip(), bib[i : j - 1]))
    return out


def fields_of(body: str) -> dict[str, str]:
    """取条目的字段名 → 值（值可能被截断，只看有无与是否为空）。"""
    out: dict[str, str] = {}
    for m in re.finditer(r"(\w+)\s*=\s*", body):
        name = m.group(1).lower()
        i = m.end()
        while i < len(body) and body[i] not in "{'\"":
            if body[i] == ",":
                break
            i += 1
        if i >= len(body) or body[i] == ",":
            out.setdefault(name, "")
            continue
        if body[i] == "{":
            depth = 0
            j = i
            while j < len(body):
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out[name] = body[i + 1 : j].strip()
        else:
            q = body.index(body[i], i + 1) if body[i] in body[i + 1 :] else len(body) - 1
            out[name] = body[i + 1 : q].strip()
    return out


@dataclass
class Issue:
    key: str
    etype: str
    kind: str  # 'missing' | 'empty' | 'type' | 'style' | 'unstructured'
    detail: str
    severity: str = "must"  # 'must' | 'nice' | 'unstructured'


@dataclass
class Report:
    total: int = 0
    issues: list[Issue] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "must" for i in self.issues)

    @property
    def n_must(self) -> int:
        return sum(1 for i in self.issues if i.severity == "must")

    @property
    def n_unstructured(self) -> int:
        return sum(1 for i in self.issues if i.severity == "unstructured")

    @property
    def n_nice(self) -> int:
        return sum(1 for i in self.issues if i.severity == "nice")


def check(bib: str) -> Report:
    """核对一份 bib，返回报告。"""
    rep = Report()
    for etype_raw, key, body in split_entries(bib):
        rep.total += 1
        etype = TYPE_ALIASES.get(etype_raw.lower(), etype_raw.lower())
        rep.by_type[etype] = rep.by_type.get(etype, 0) + 1

        if etype != etype_raw.lower():
            rep.issues.append(Issue(key, etype, "type", f"@{etype_raw} → @{etype}", "nice"))

        f = fields_of(body)

        # ── 先判「未结构化」────────────────────────────────────────────
        #
        # 脚注体例的参考文献（「参阅 R. Camap, "The Aim of Inductive Logic"in
        # Logic, Methodology..., Stanford: Stanford University Press, 1962」）
        # 解析不出来时，按设计整条原样塞进 note。这类条目**不是「缺 author/title」**，
        # 而是「还没被拆成字段」——两者要分开报，否则数字会被严重高估。
        # 实测 2026_07_Liangying_Zhao 44 条因此报出 84 项「必补」，全是虚的。
        if not f.get("author") and not f.get("title") and len(f.get("note", "")) > 40:
            rep.issues.append(Issue(key, etype, "unstructured", "整条在 note 里，需人工拆分", "unstructured"))
            continue

        spec = REQUIRED.get(etype)
        if spec is None:
            rep.issues.append(Issue(key, etype, "type", f"未登记的类型 @{etype_raw}，请人工确认", "nice"))
            continue

        f = fields_of(body)
        must_fields = list(spec["must"])
        # 第 84 条：**斯坦福哲学百科（SEP）词条没有页码**——它的「版次」是存档季节 + 年份
        # （`edition = {Winter 2022}`），页码那一栏本来就不存在。
        # 不特判的话，每一条 SEP 都会永远报「必补 pages」，编辑照着报错去补只会去编一个页码。
        if is_sep_entry(f) and "pages" in must_fields:
            must_fields.remove("pages")
        for sev, names in (("must", must_fields), ("nice", spec["nice"])):
            for n in names:
                if n in f and f[n]:
                    continue
                # year 缺失但 date 有值，不算缺
                if n == "year" and any(f.get(k) for k in _YEAR_LIKE):
                    continue
                # 中文条目的 address 常用 publisher 里带出版地（如「北京: 商务印书馆」）
                if n == "address" and re.search(r"[:：]", f.get("publisher", "")):
                    continue
                kind = "missing" if n not in f else "empty"
                rep.issues.append(Issue(key, etype, kind, n, sev))
    return rep


# ── 编辑部硬规则：英文作者 / 编者一律倒装（2026-10-15）─────────────────────
#
# 决定与依据（《排校手册》没写「必须倒装」，但编辑实际成品 20/20 全倒装）见
# `slcn/bibnorm.py` 的 `normalize_author_field` 注释与 `docs/cases/62+.md` 第 63 条。
#
# ⚠️ **为什么不并进 `check()`**：`docx_audit.py` 用 `check()` 量「解析出来的条目有
#    多少必补字段」，它的输出 `out/docx_audit.csv` 是**冻结基线**（README AUTO 块钉着
#    sha256）。把一条只关乎「值怎么写」的规则塞进 must 档，会让 132 行的 `bib_must`
#    整列跳动，与「解析器能不能拿到字段」这件事混在一起，基线就失去了意义。
#    所以这里**另开入口**，由 `checkbib.py`（编辑交回的 bib 的离线核对）与
#    `refmark.py`（任务书）显式调用；`check()` 的口径一个字没变。
#
# 判据与规范化器**同源**：`bibnorm.author_is_inverted()` 就是「倒装了也不会变」，
# 所以不会出现「检查器报错、但引擎改不动」的假阳性（中/日文姓名、`JC Beall` 这类
# 判不准的英文名都算合规）。


# ── 编辑部固定写法：保护花括号 / 版次 / 编者（第 85 条，2026-10-15）──────────
#
# 与 `author_form_issues()` 同一个理由**不并进 `check()`**（那条注释在这里同样成立：
# `check()` 喂的是冻结基线 `out/docx_audit.csv`，不能加「值该怎么写」的规则）。
#
# 判据的机制出处（**不是风格偏好，是排版后果**）：
#   `编辑笔记/模板文件/SLCN-MIX.sty:733`
#     \DeclareFieldFormat[article,inproceedings,incollection,misc]{title}
#         {\cegen{“#1”}{``\MakeSentenceCase{#1}''}}
#   `\cegen{中文}{英文}` 按 `userf` 选分支 → **只有不带 `userf = {cn}` 的条目**
#   才会被 `\MakeSentenceCase` 处理：没被 `{}` 保护的大写字母会**被压成小写**。
#   成品 PDF 实证：`{Two Dogmas of {E}mpiricism}` 漏掉 `{E}` 后印成 `…of empiricism`。
#
#: 会被句首大写处理的类型（与 sty 里那行的类型列表**同源**，改一处要同时改两处）。
_SENTENCE_CASED = ("article", "inproceedings", "incollection", "misc")
#: 冒号后紧跟一个大写字母（且不是全大写缩写）→ 很可能需要保护成 `: {A}n`
_SUBTITLE_CAP = re.compile(r"[:：]\s+([A-Z])(?![A-Z])")
#: 已被保护的样子（`: {A}n`）
_SUBTITLE_GUARDED = re.compile(r"[:：]\s+\{[A-Z]")
#: `Part I` / `Vol. 2` 这类编号
_NUMBERED = re.compile(r"\b(?:Part|Chapter|Section|Vol\.?|No\.?)\s+([IVXLCDM]+|\d+)\b")


def style_form_issues(bib: str) -> list[Issue]:
    """本刊几处**固定写法**的提示（都是 nice 级；只报不改）。

    1. 英文标题里**副标题首字母没保护** → 排版时会变小写；
    2. 英文标题里 **`Part I` 这类编号没保护** → 同上；
    3. **英文书籍单列 `edition`** → 本刊口径是写进 `title`（`…, (Updated Edition)`）；
    4. **只有编者、没有作者的书** → 编者应写进 `author` 并加 `(eds.)`（外层再套一层花括号）。
    """
    out: list[Issue] = []
    for etype_raw, key, body in split_entries(bib):
        etype = TYPE_ALIASES.get(etype_raw.lower(), etype_raw.lower())
        f = fields_of(body)
        # 中文条目（`userf = {cn}`）不做句首大写，版次/编者的习惯也不同 → 整条跳过。
        if (f.get("userf") or "").strip().lower() == "cn":
            continue
        ti = (f.get("title") or "").strip()
        if etype in _SENTENCE_CASED and ti:
            if _SUBTITLE_CAP.search(ti) and not _SUBTITLE_GUARDED.search(ti):
                out.append(Issue(
                    key, etype, "style",
                    "标题里冒号后的首字母没保护，排版时会变成小写——"
                    "按本刊口径写成 `…: {A}n …`（任务书第一节第 2 条）", "nice"))
            m = _NUMBERED.search(ti)
            if m and ("{" + m.group(1) + "}") not in ti:
                out.append(Issue(
                    key, etype, "style",
                    f"标题里的编号 `{m.group(0)}` 没保护，排版时会变小写——"
                    f"写成 `Part {{{m.group(1)}}}` 这种（任务书第一节第 2 条）", "nice"))
        if etype == "book":
            # ⚠️ 口径与自动搬的判据**同源**（`bibnorm.is_english_book` / `is_sep_entry`）：
            #    工具自己产出时已经把英文书的版次并进了 title（第 86 条），
            #    这条提示主要留给「编辑交回的 bib / 手工写的条目」。
            from . import bibnorm as _bn
            if f.get("edition") and not is_sep_entry(f) and _bn.is_english_book(f):
                out.append(Issue(
                    key, etype, "style",
                    "英文书的版次按本刊口径写进 `title`（`… 书名, (Updated Edition)`），"
                    "不要单列 `edition`（任务书第一节第 2 条；SEP 词条例外）", "nice"))
            if not (f.get("author") or "").strip() and (f.get("editor") or "").strip():
                out.append(Issue(
                    key, etype, "style",
                    "这本书只有编者：按本刊口径把编者写进 `author`，外层多套一层花括号并加 `(eds.)`，"
                    "例如 `author = {{F. H. van Eemeren and P. Wu~(eds.)}}`"
                    "（编者 > 3 人时缩为 2–3 人，任务书第一节第 2 条）", "nice"))
    return out


def author_form_issues(bib: str) -> list[Issue]:
    """英文 `author` / `editor` 未倒装的条目 → must 级问题（含改法建议）。"""
    from . import bibnorm

    out: list[Issue] = []
    for etype_raw, key, body in split_entries(bib):
        etype = TYPE_ALIASES.get(etype_raw.lower(), etype_raw.lower())
        f = fields_of(body)
        for name in ("author", "editor"):
            raw = (f.get(name) or "").strip()
            if not raw or not re.search(r"[A-Za-z]", raw):
                continue
            if bibnorm.author_is_inverted(raw):
                continue
            out.append(Issue(
                key, etype, "style",
                f"{name} 未按编辑部体例倒装：{raw} → 应为 {bibnorm.normalize_author_field(raw)}",
                "must",
            ))
    return out


# 中文译著：`原作者（著），译者（译）`（《排校手册》源码 reference.bib 的实例：
# `焦循（著），沈文倬（点校）`、`黑格尔（著），杨一之（译）`）。
_MARK_SRC = re.compile(r"[（(](?:著|原著)[)）]")
_MARK_TRANS = re.compile(r"[（(](?:译|点校|校注|校|编译|注释)[)）]")
# 「角色标记」的**全集**：只要值里出现其中一个，这个值就走「中文著/译/整理者体例」，
# 而不是普通中文多作者体例（见 `author_has_role_mark()`）。
# 为什么要单独一个更宽的集合（2026-10-15，第 64 条）：`{阿斯海姆（著），张建军、万林（译）}`
# 里的中文逗号/顿号**是姓名的一部分**，按普通多作者口径会被建议改成
# `{阿斯海姆（著） and 张建军 and 万林（译）}` —— 著/译标记当场毁掉。`（编）`（点校）同理：
# 它们后面跟的也是**合作者**而不是「另一位作者」，同样不该被 `and` 拆开。
_MARK_ANY = re.compile(
    r"[（(](?:著|原著|译|译者|点校|校注|校|编|编译|主编|编选|注释|整理|辑|译注)[)）]"
)
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")


def author_has_role_mark(value: str, *, both: bool = False) -> bool:
    r"""作者的写法里有没有「（著）/（译）/（点校）/（编）…」这类**角色标记**。

    有的话，这个字段就**不是**普通的多作者列表：中文逗号与顿号是姓名/角色的一部分，
    按「多作者用 `and` 分隔」的口径去改会毁掉著/译标记（第 64 条）。
    调用方：`slcn/bibreport.py: check_spec()`（豁免 `and` 提示）、
    `slcn/footnotes.py: normalize_authors()`、`slcn/docxsource.py` 的编号清单入口。

    `both=True` 时要求「（著）」与「（译）/（点校）」**同时**出现——即**完整**的
    `原作者（著），译者（译）` 形态。docxsource 用它来放行「题名只有 3 个字」的中文译著
    （题名短于 4 字本来一律当切错丢掉；但完整的著/译形态下，题名确实可能是《工具论》
    这种 3 字书名，丢掉整条会让正文 `\parencite` 悬空）。
    """
    if both:
        v = value or ""
        return bool(_MARK_SRC.search(v) and _MARK_TRANS.search(v))
    return bool(_MARK_ANY.search(value or ""))


def translation_marking_issues(bib: str) -> list[Issue]:
    """中文条目像是译著 / 整理本、作者写法不合体例 → nice 级提示（不挡退出码）。

    规范写法 `原作者（著），译者（译）`（《排校手册》源码的实例：
    `黑格尔（著），杨一之（译）`、`焦循（著），沈文倬（点校）`），
    **中间保留中文逗号与顿号**（那两者是姓名的一部分，不是多作者分隔符）。

    三条判据（互相独立，一条命中就报一条）：
      ① 用了 ` and ` —— 著/译/整理标记被当成多作者分隔；
      ② 有「（著）」缺「（译）」；③ 有「（译）」缺「（著）」。

    ⚠️ **只作提示**：全库实测（`out/corpus/**/reference.bib`）命中数见
    `docs/cases/62+.md` 第 63 条（原始口径）与第 64 条（判据放宽后的重测）。
    一个角色标记都没有的中文条目**不报**——那可能只是普通中文专著，没有译者，
    不能凭猜测说它缺译者。
    """
    out: list[Issue] = []
    for etype_raw, key, body in split_entries(bib):
        etype = TYPE_ALIASES.get(etype_raw.lower(), etype_raw.lower())
        f = fields_of(body)
        author = (f.get("author") or f.get("editor") or "").strip()
        if not author or not _CJK_CHAR.search(author):
            continue
        if not _MARK_ANY.search(author):
            continue
        # 「原作者（著），译者（译）」里的那个中文逗号**不是**多作者分隔符，
        # 不许按中文多作者的口径改成 `and`（见任务书第一节第 2 条最后两条）。
        if re.search(r"\s+and\s+", author):
            out.append(Issue(
                key, etype, "style",
                f"中文著/译体例不该用 `and` 分隔（{author}）——规范写法 "
                "`原作者（著），译者（译）`，中间保留中文逗号",
                "nice",
            ))
        has_src, has_trans = bool(_MARK_SRC.search(author)), bool(_MARK_TRANS.search(author))
        if has_src and not has_trans:
            out.append(Issue(
                key, etype, "style",
                f"疑似中文译著：作者只写了「（著）」（{author}），缺「（译）译者」——"
                "规范写法 `原作者（著），译者（译）`（保留中文逗号/顿号，不要改成 and）",
                "nice",
            ))
        elif has_trans and not has_src:
            out.append(Issue(
                key, etype, "style",
                f"疑似中文译著：作者只写了「（译）」（{author}），缺「（著）原作者」——"
                "规范写法 `原作者（著），译者（译）`",
                "nice",
            ))
    return out


def unify_types(bib: str) -> tuple[str, list[str]]:
    """统一类型名（如 @conference → @inproceedings）。"""
    notes = []

    def repl(m: re.Match) -> str:
        raw = m.group(1)
        new = TYPE_ALIASES.get(raw.lower())
        if new and new != raw.lower():
            notes.append(f"@{raw} → @{new}（{m.group(2).strip()}）")
            return f"@{new}{{{m.group(2)},"
        return m.group(0)

    return _ENT.sub(repl, bib), notes


def format_report(rep: Report, title: str = "参考文献核对报告") -> str:
    """生成人可读报告。"""
    L = [
        f"══ {title} ══",
        f"  条目总数：{rep.total}",
        f"  必补项  ：{rep.n_must}",
        f"  建议补项：{rep.n_nice}",
        f"  未结构化：{rep.n_unstructured}（整条在 note 里，非缺字段）",
        "",
    ]
    if not rep.issues:
        L.append("  ✅ 未发现问题。")
        return "\n".join(L)

    unstruct = [i for i in rep.issues if i.severity == "unstructured"]
    if unstruct:
        L.append(f"  ── 未结构化条目（{len(unstruct)} 条，建议人工拆分）──")
        for i in unstruct[:40]:
            L.append(f"      [{i.etype}] {i.key}")
        if len(unstruct) > 40:
            L.append(f"      …另有 {len(unstruct) - 40} 条")
        L.append("")

    by_key: dict[str, list[Issue]] = {}
    for i in rep.issues:
        if i.severity == "unstructured":
            continue
        by_key.setdefault(i.key, []).append(i)

    L.append(f"  ── 按条目列出（{len(by_key)} 条需处理）──")
    for k, items in by_key.items():
        must = [i for i in items if i.severity == "must"]
        nice = [i for i in items if i.severity == "nice"]
        head = f"  [{items[0].etype}] {k}"
        L.append(head)
        if must:
            L.append("      ❗ 必补：" + "、".join(sorted({i.detail for i in must})))
        if nice:
            L.append("      · 建议：" + "、".join(sorted({i.detail for i in nice})))
    return "\n".join(L)


# ── 引用与条目的双向核对 ──────────────────────────────────────────────────

_CITE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^{}]*)\}")


def cited_keys(latex: str) -> set[str]:
    """取正文里引用到的所有 bib 键。"""
    keys: set[str] = set()
    for m in _CITE.finditer(latex):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                keys.add(k.split("=")[-1].strip())
    return keys


def entry_keys(bib: str) -> set[str]:
    return {k for _t, k, _b in split_entries(bib)}


# ── 第 80 条：「条目真多余」还是「正文那处引用没转成 cite」───────────────────
#
# 症状（2026年第5期(90)/10 Bo_Chen 实测）：正文写 `（Beal  Restall 2006）`
# （源稿把 `Beall` 少写了一个 l），`link_citations()` 的「姓+年」精确匹配落空
# → 那处引用**原样留在正文里**，bib 里对应的 `Beall2006` 于是被 ② 报成
# 「冗余条目」。**这是误诊**：条目不多余，是那处引用没转；编辑照报告去删条目就删错了。
#
# 判据只用**原文里查得到**的证据，不猜（四条都要满足才提示）：
#   ① 调用方确实给了 `unmatched` 清单（= `link_citations()` 报出来的那批引用原文）；
#   ② 该引用原文**逐字**出现在传进来的正文（`latex`）里——「能在原文里找到片段」；
#   ③ 引用点里有一个数字 token 与本条 `year`/`date` **逐字相同**；
#   ④ 引用点里有一个姓名 token 与本条作者姓氏（或 bib 键的字母段）**共有前缀**：
#      ASCII ≥ 4 个字符（`Beal` vs `Beall`）、CJK ≥ 2 个汉字。
# 命中的证据（引用原文、年份、姓名、共有前缀长度）**原样写进提示**，由编辑核对——
# 工具不替编辑下结论，② 的原文案一个字不动，只在后面追加提示。
_REF_TOKEN_SPLIT = re.compile(r"[、,，和与&;；:：\s（）()\[\]{}“”\"'’]+")
_HINT_MIN_ASCII = 4
_HINT_MIN_CJK = 2


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _ref_tokens(text: str) -> list[str]:
    """把引用原文切成 token（`（Beal  Restall 2006）` → Beal / Restall / 2006）。

    ⚠️ **数字 token 要留着**：年份判据（③）就是在这些 token 里找 `year`。
    `_prefix_evidence()` 对数字 token 自然给 0（bib 键的字母段不含数字），不会误配姓名。
    """
    out: list[str] = []
    for t in _REF_TOKEN_SPLIT.split(text or ""):
        t = t.strip("．.·—-")
        if t:
            out.append(t)
    return out


def _prefix_evidence(tok: str, ref: str) -> int:
    """token 与参考名共有前缀的**有效**长度（不够长算 0 = 没有证据）。"""
    a, b = (tok or "").strip(), (ref or "").strip()
    if not a or not b:
        return 0
    n = _common_prefix_len(a.lower(), b.lower())
    need = _HINT_MIN_ASCII if (a.isascii() and b.isascii()) else _HINT_MIN_CJK
    return n if n >= need else 0


def unmatched_hint(key: str, body: str, unmatched: list[str], latex: str) -> str:
    r"""哪一处**没转成 cite** 的引用可能就是这个条目？给不出可核证据就返回空串。

    返回值是一句给编辑的提示（含全部证据）；调用方把它追加在 ② 的原文案之后。
    判据见上面「第 80 条」注释，四条缺一不可。
    """
    f = fields_of(body)
    year = (f.get("year") or f.get("date") or "").strip()
    au = (f.get("author") or f.get("editor") or "").strip()
    if not year or not unmatched:
        return ""
    surname = re.split(r"[,，]|\s+", au)[0] if au else ""
    m = re.match(r"[A-Za-z]+", key or "")
    key_alpha = m.group(0) if m else ""
    for raw in unmatched:
        # ② 「能在原文里找到对应片段」：引用原文必须逐字出现在这份正文里
        if raw not in latex:
            continue
        toks = _ref_tokens(raw)
        if not any(t == year for t in toks):
            continue
        for t in toks:
            for ref, src in ((surname, "作者姓氏"), (key_alpha, "bib 键的字母段")):
                n = _prefix_evidence(t, ref)
                if n:
                    return (f"但正文里有一处**没转成引用**的引用点「{raw}」——年份 {year} "
                            f"与本条一致、姓名「{t}」与本条{src}「{ref}」同前 {n} 个字符；"
                            r"更可能是这处引用没转成 \parencite，**不是条目多余**，请核对")
    return ""


def check_citations(latex: str, bib: str, window: int = 80,
                    unmatched: list[str] | None = None) -> list[str]:
    r"""核对正文引用与 bib 条目是否对得上，返回问题清单。

    三类问题：
      ① 悬空引用：正文引了 \parencite{key}，但 bib 里没有该条目
      ② 冗余条目：bib 里有，正文从没引用
      ③ **正文提到作者名却没有引用** —— 实测 10 Bo_Chen 的 `陈波提出社会历史
         因果描述论` 源稿没写引用，编辑凭领域知识补了 `（\parencite{Chen2015}）`
         和对应条目。这属于编辑判断，工具做不了，但**必须报出来让人看**。

    `unmatched`（**可选、纯附加**，第 80 条）：`link_citations()` 报出来的那批
    「看着像人年制引用、但没能匹配到任何条目、于是原样留在正文里」的引用原文
    （形如 `（Beal  Restall 2006）`）。给出来时，② 会在**确有证据**的条目后面
    追加一句「更可能是这处引用没转」的提示（判据见 `unmatched_hint()`）。
    不给（`None`，所有既有调用方都是这样）时，返回值与从前**逐字相同**。
    """
    problems: list[str] = []
    ck, ek = cited_keys(latex), entry_keys(bib)
    body_by_key: dict[str, str] = {}
    for _t, _k, _b in split_entries(bib):
        body_by_key.setdefault(_k, _b)

    for k in sorted(ck - ek):
        problems.append(f"① 悬空引用：正文引用了 {{{k}}}，但参考文献里没有这一条")
    if ek and not ck:
        # P2-4（2026-10-18）：bib 有条目、正文 0 引用命令 → **内容缺失**（PDF 参考文献区
        # 会是空的），从逐条 hint 升为一条 problem；有引用时下面逐条提示保持不变。
        problems.append(
            f"② 正文 0 引用：参考文献有 {len(ek)} 条，但正文没有任何 `\\parencite` / `\\cite` "
            f"命令 —— **PDF 参考文献区会是空的**（这不是「条目多余」，是正文引用没接上，"
            f"必须人工补引用或删条目）"
        )
    for k in sorted(ek - ck):
        line = f"② 冗余条目：{{{k}}} 在参考文献里，但正文从未引用"
        if unmatched:
            hint = unmatched_hint(k, body_by_key.get(k, ""), unmatched, latex)
            if hint:
                line += "  ⚠ " + hint
        problems.append(line)

    # ③ 正文**把某个主张归给某作者**却没有引用。
    #
    # 判据收得很紧：只在「作者名 + 归因动词」时才报（陈波提出…、Haack认为…）。
    # 早期版本只要出现作者名就报，误报极多——正文讨论某学者观点时天然会提名字。
    # 实测目标案例：10 Bo_Chen 源稿写「陈波提出社会历史因果描述论」但没写引用，
    # 编辑凭领域知识补了 `（\parencite{Chen2015}）` 和对应条目。
    body_plain = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?", "", latex)
    attrib = "提出|认为|指出|主张|论证|证明|批评|反驳|发展|创立|发现|强调|注意到|表明|所说|所言|谈到|解释道"
    seen_phrase: set[tuple[str, int]] = set()   # 同一处只报一次，别按条目重复
    for _t, k, body in split_entries(bib):
        f = fields_of(body)
        au = f.get("author", "") or f.get("editor", "")
        if not au:
            continue
        cn = re.match(r"^([\u4e00-\u9fff]{2,4})", au.strip())
        names = [cn.group(1)] if cn else [re.split(r"[,，]|\s+", au.strip())[0]]
        for nm in names:
            if len(nm) < 2:
                continue
            for m in re.finditer(re.escape(nm) + r"\s*(?:" + attrib + r")", body_plain):
                if (nm, m.start()) in seen_phrase:
                    break
                seg = body_plain[max(0, m.start() - window) : m.end() + window]
                if not _CITE.search(seg):
                    seen_phrase.add((nm, m.start()))
                    problems.append(
                        f"③ 疑似漏引：正文写「{m.group(0)}」，附近没有引用命令，"
                        fr"请核对是否需要补 \\parencite{{{k}}}"
                    )
                    break
    return problems
