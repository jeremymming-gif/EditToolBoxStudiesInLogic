"""参考文献（.bib）规范化。

输入可能是：
  1. 作者自带的 BibLaTeX/BibTeX 库（.bib）——需按 SLCN 模板惯例清理
  2. 正文里的 \\bibitem 列表      ——需转成 .bib
  3. Word 文档末尾的人年制参考文献 ——需结构化（最难，PoC 先做中文条目）

清理规则来自对 03 Liang_Zhang 与 10 Bo_Chen 两份人工成品的实测比对。

**第 133 条（2026-10-18）：整行 `%` 注释不算条目**。作者 `.bib` 里常见注释掉的模板/
重复条目（`% @article{,`、`% @misc{z16,`）；`split_entries()` 从前全文匹配 `@\\w+\\s*\\{`、
不跳注释，产物 `reference.bib` 会带空键/重复键，biber 语法报错**整份 bib 报废**
（2023(71)04：115 处 undefined citation、参考文献区未生成）。现在先 `strip_bib_comments()`
（只清「行首可选空白 + `%` 到行尾」，行内/值内 `%` 不动）再拆条目。
"""

from __future__ import annotations

import re

# 期刊别名 → BibLaTeX 标准字段
FIELD_ALIASES = {
    "issue": "number",
    "journaltitle": "journal",
    "location": "address",
    "urlstatus": "urldate",
}

# ── userf：控制该条参考文献用中文还是英文标点 ────────────────────────────
#
# SLCN-MIX.sty 第 702~711 行：
#     \providecommand{\cecomma}{\cegen{\cncomma}{\addcomma\space}}  % 根据 userf 输出逗号
#     \providecommand{\ceperiod}{\cegen{\cnperiod}{\addperiod\space}}% 根据 userf 输出句号
# 即 **userf={cn} 让该条用中文标点（、。）**，不填则用英文（, .）。
#
# 全库实测（132 个含 userf 的 bib，约 4600 条）：
#     cn    ← 全中文 1000 条 / 中文作者+英文标题 38 条
#     (无)  ← 纯英文 3551 条
#     jp / jps ← 日文（含假名）45 条
# 判据：**内容含中文 → cn；含假名 → jp；纯英文 → 不填**。

_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_CJK = re.compile(r"[\u4e00-\u9fff]")

# 应从条目里删除的字段（编辑部惯例）
DROP_FIELDS = {
    "urldate", "file", "abstract", "keywords", "timestamp", "owner", "groups",
    # Web of Science 导出（89-06 实测）：人工成品全部删除；不删会以 `2013 = {18}` /
    # `usage-count-since-2013` 之类字段进产物（前者是非法字段名，biber 整份报废）
    "affiliation", "affiliations", "research-areas", "web-of-science-categories",
    "web-of-science-index", "orcid-numbers", "number-of-cited-references",
    "times-cited", "usage-count-last-180-days", "usage-count-since-2013",
    "journal-iso", "doc-delivery-number", "unique-id", "da",
}
# 前缀型垃圾字段（Zotero/Mendeley 的 bdsk-*：bdsk-url-1 等，人工成品不保留）
DROP_FIELD_PREFIXES = ("bdsk-",)

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _clean_field_value(name: str, value: str) -> str | None:
    """清理单个字段值，返回 None 表示应删除该字段。"""
    v = value.strip()
    if name in DROP_FIELDS or name.startswith(DROP_FIELD_PREFIXES):
        return None
    if name == "month":
        # ⚠️ 不要转成数字！实测人工成品保留 `nov` / `jul` 这种写法，
        #    早先转成 `11`/`07` 是错的（Liang_Zhang 的 Yalcin2007、Ninan2016 都对不上）。
        return v.lower() if v.isalpha() else v
    if name == "pages":
        # {pp. 1--61} → {1--61}；{983-1026} → {983--1026}
        v = re.sub(r"^\s*pp?\.\s*", "", v)
        v = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "--", v)
        return v
    if name == "publisher":
        # 去掉期刊名被误填进 publisher 的情况：{[Oxford University Press, Mind Association]}
        v = re.sub(r"^\[(.*)\]$", r"\1", v)
        # 出版商后面常跟着期刊名（`Oxford University Press, Mind Association`），
        # 人工做法是只留出版商。实测 Liang_Zhang 的 Dorr2013 / Mandelkern2019 都是这样。
        parts = [x.strip() for x in v.split(",")]
        if len(parts) > 1 and re.search(
            r"(Press|University|Verlag|Publishing|Routledge|Blackwell|Wiley|Springer)$", parts[0]
        ):
            return parts[0]
        return v
    if name == "year":
        v = re.sub(r"[^\d]", "", v) or v
        return v
    if name in ("number", "issue", "volume"):
        # 期号/卷号去前导零：实测人工把 `03` 写成 `3`、`01` 写成 `1`
        return re.sub(r"^0+(?=\d)", "", v.strip())
    if name in ("author", "editor"):
        v = re.sub(r"\s+", " ", v).strip()
        # 中文多作者用顿号/逗号分隔 → 统一为 `and`（BibLaTeX 的多作者分隔符）。
        # 实测人工：`刘淘宁 and 顿新国`（源稿是 `刘淘宁，顿新国`）
        if re.search(r"[\u4e00-\u9fff]", v):
            parts = [p.strip() for p in re.split(r"[，,、]", v) if p.strip()]
            if len(parts) > 1:
                return " and ".join(parts)
            return v
        v = re.sub(r"\s*,\s*", ", ", v)  # 统一 "Last, First" 的逗号后空格
        return v
    return v


def detect_userf(author: str, title: str, extra: str = "") -> str:
    """判断条目该用哪个 userf 值。

    中文 → 'cn'；日文（含假名）→ 'jp'；纯英文 → ''（不填）。
    """
    blob = f"{author} {title} {extra}"
    if _KANA.search(blob):
        return "jp"
    if _CJK.search(blob):
        return "cn"
    return ""


# ── 英文作者倒装：`T. Burge` → `Burge, T.`（2026-10-15 编辑部决定）──────────
#
# **决定**（用户 2026-10-15）：英文 `author` / `editor` **一律倒装**成「姓, 名缩写」。
#
# 先查了权威文本《排校手册》（`编辑笔记/排版手册/排版手册源码/edit_handbook.tex`
# 的「参考文献」一节，第 482–494 行），结论要说清楚：
#   · 手册**没有**规定「必须倒装」——它自己给出的写法反而是名前姓后
#     （`{D. Lewis and others}`、`B. C. van Fraassen`）。手册明确的是另外三件事：
#       ① 虚词（`van`/`van der`）**小写**、随其后的姓氏参与排序（按 F/D/H，不按 V）；
#          要按 V 排就在 bib 里写波浪号 `van~Fraassen`；
#       ② 作者超过 4 个 / 编者超过 3 个写成 `{D. Lewis and others}`；
#       ③ 「同一篇文章要保持标准统一。非必要不修改。」
#   · 而**编辑的实际成品**是另一回事：`2026年第5期(90)/01 Jingru_Kou/reference.bib`
#     20 条英文条目 **20/20 全倒装**（`Burge, T.` / `Chalmers, D. J.` / `Kvanvig, J.`）。
#     编辑部据此把「一律倒装」定为**硬规则**（用户 2026-10-15）。
#   · 「要不要把全名缩成缩写」手册只在 JC Beall 一节侧面提过（关掉 firstinits 时
#     手动把 `{David Lewis}` 改成 `{D. Lewis}`）——可见**缩写才是目标形态**。
#     所以这里把全名缩成首字母，并**保留全部中名首字母**：`Jonathan L. Kvanvig`
#     → `Kvanvig, J. L.`（成品里的 `Ayer, A. J.` / `Fitch, F. B.` 正是这样保留的）。
#     手册没说「只留第一个首字母」——那一点上不发明，取保守解。
#
# **三条硬边界**（宁可漏改，也绝不改错——文献作者是学术事实）：
#   ① 含汉字/假名 → **原样返回**（中文、日文姓名不倒装）；
#   ② 已经倒装的（含逗号）、带花括号或 LaTeX 命令的 → 原样返回，因此**幂等**；
#   ③ 判不准的整条原样返回，绝不产生「一半倒装一半不倒装」的混合形态
#      （手册要求「同一条里不能混用」；`JC Beall` 手册点名是唯一正确写法、要特殊处理，
#      故 2 字母缩写一律不推断）。

_CJK_OR_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]")
_AUTHOR_SEP = re.compile(r"(\s+and\s+|\s*&\s*)", re.I)
_ONE_INITIAL = re.compile(r"^[A-Z]\.?$")
_GIVEN_WORD = re.compile(r"^[A-Z][a-zA-Z'’\-]*$")
_GLUED_INITIALS = re.compile(r"(?<=[A-Za-z]\.)(?=[A-Za-z])")  # `M.Dummett` → `M. Dummett`
_AUTHOR_TAIL_MARK = re.compile(r"^(.*?)\s*(\([^)]*(?:eds?|Ed|编|著|译)[^)]*\))$")
# 姓名后缀：`Sammy Davis Jr.` 的 `Jr.` 不是姓，判到它就整条不动
_NAME_SUFFIXES = {"jr.", "jr", "sr.", "sr", "ii", "iii", "iv"}


def _given_token_ok(t: str) -> bool:
    """这个 token 能不能当「名」用。

    ⚠️ 两个字母的缩写（`Jc` / `JC` / `Al`）**一律不认**：《排校手册》点名
    「JC Beall 是唯一正确写法，需特殊处理（关掉 firstinits）」，把 `Jc` 缩成 `J.`
    会直接改错一个真实的人名。宁可这一条不倒装。
    """
    if _ONE_INITIAL.match(t):
        return True
    if not _GIVEN_WORD.match(t):
        return False
    return len(t) > 2 and not t.isupper()


def _initials_of(tokens: list[str]) -> str:
    # `L`（漏写句点）也要补成 `L.` —— 实测成品里 `Jerry L Morgan` → `Morgan, J. L.`
    return " ".join(t if t.endswith(".") else t[0] + "." for t in tokens)


def _invert_one(name: str) -> str:
    """单个英文姓名：名前姓后 → 姓, 名。判不准就原样返回。"""
    g = name.strip()
    if not g or _CJK_OR_KANA.search(g) or "{" in g or "\\" in g or "," in g:
        return name
    mark = ""
    m = _AUTHOR_TAIL_MARK.match(g)
    if m and m.group(1).strip():
        g, mark = m.group(1).strip(), m.group(2)
    toks = _GLUED_INITIALS.sub(" ", g).split()
    if len(toks) < 2:
        return name

    # 形态 A：`Kahneman D.` / `van der Hoek W.` —— 姓在前、名缩写在后（缺逗号）。
    # 判据：存在切点 k ≥ 1，使 toks[k:] 全是首字母、toks[:k] 全是姓名词/虚词。
    # 真实人名不会「以首字母结尾」，所以这个形态是**无歧义**的，补一个逗号即可。
    split_k = -1
    for k in range(1, len(toks)):
        if not all(_ONE_INITIAL.match(t) for t in toks[k:]):
            continue
        head = toks[:k]
        if all(_GIVEN_WORD.match(t) or t[:1].islower() for t in head):
            split_k = k
            break
    if split_k > 0:
        sur, given = toks[:split_k], toks[split_k:]
        if sur[-1].lower() in _NAME_SUFFIXES or not re.match(r"^[A-Z]", sur[-1]):
            return name
    else:
        # 形态 B：`T. Burge` —— 名（缩写或全名）在前，姓在最后（含小写虚词前缀）
        i = len(toks) - 1
        while i - 1 >= 0 and toks[i - 1][:1].islower():
            i -= 1
        given, sur = toks[:i], toks[i:]
        if not given or not sur:
            return name
        if not all(_given_token_ok(t) for t in given):
            return name
        if _ONE_INITIAL.match(sur[-1]) or sur[-1].lower() in _NAME_SUFFIXES:
            return name          # 判成 `Sammy Davis Jr.` / `T.` 这种，不动
        if not re.match(r"^[A-Z]", sur[-1]):   # 姓（虚词之后的那个词）必须大写起头
            return name
    given_s = _initials_of(given)
    if not given_s:
        return name
    surname = " ".join(sur)
    if mark:
        return f"{{{surname} {mark}}}, {given_s}"
    return f"{surname}, {given_s}"


def normalize_author_field(value: str) -> str:
    """把英文 `author`/`editor` 值倒装；中文日文与判不准的一律原样返回（幂等）。

    多作者（` and ` / `&` 分隔）**每一个**都倒装：
        `J. P. Dillard and C. Segrin` → `Dillard, J. P. and Segrin, C.`
    含虚词的名字保留原样、把虚词并进姓：
        `B. C. van Fraassen` → `van Fraassen, B. C.`；`van~Fraassen` 的波浪号不动。
    带 `(ed.)` 的编者按编辑部写法规避 BibTeX 的姓解析：
        `J. Salerno (ed.)` → `{Salerno (ed.)}, J.`
    """
    v = (value or "").strip()
    # 逗号 = 已经是「姓, 名」倒装式（或「逗号 + and」混写）→ 整条不动。
    # 前者是幂等要求，后者是「宁可漏改也不产出半倒装的混合条」。
    if not v or _CJK_OR_KANA.search(v) or "," in v:
        return value
    parts = _AUTHOR_SEP.split(v)
    # ⚠️ 全有或全无：只要有一段判不出来，整条不动 —— 手册明令「同一条里不能混用」。
    for i, p in enumerate(parts):
        if i % 2:
            continue
        s = p.strip()
        if not s or s.lower() == "others":
            continue
        if _invert_one(s) == s:
            return value
    out: list[str] = []
    for i, p in enumerate(parts):
        out.append(p if i % 2 else (_invert_one(p.strip()) if p.strip() else p))
    return "".join(out)


def author_is_inverted(value: str) -> bool:
    """`author`/`editor` 是否已是规范形态（= 倒了也不会变）。

    判据就是「`normalize_author_field` 会不会改动它」——**检查器与规范化器同源**，
    因此不会出现「检查器报错、但规范化器改不动」的假阳性。
    中文/日文姓名、以及判不准的英文名（`JC Beall` 等）都算合规。
    """
    v = value or ""
    return normalize_author_field(v) == v


#: 第 84 条：识别「斯坦福哲学百科（SEP）词条」的唯一判据 —— 只看 `booktitle`。
#: 为什么不看 key：SEP 官网的 key 是 `sep-…`，本刊的 key 是「作者姓+年」，
#: 两种都可能出现（编辑从官网抄来的 / 我们生成的），key 不能当判据。
#: ⚠️ 放在 bibnorm 里是因为**规范化与判据都要用同一份**（`bibcheck` 从这里 import，
#:    不让两处各写一个字面量——那种重复迟早会分叉）。
SEP_BOOKTITLE = "stanford encyclopedia of philosophy"


def _sep_key(value: str) -> str:
    """把 `booktitle` 归一成可比较的形态：**去掉保护花括号**、压缩空白、转小写。

    ⚠️ 必须去花括号：SEP 官网给的是 `The {Stanford} Encyclopedia of Philosophy`
    （`{S}` 是 biblatex 的大小写保护），直接子串匹配会**认不出来**——
    这正是本条第一次实现时踩到的坑（测试 (b) 的带花括号用例把它钉住了）。
    """
    return re.sub(r"\s+", " ", re.sub(r"[{}]", "", value or "")).strip().lower()


def is_sep_entry(fields: dict[str, str]) -> bool:
    """这一条是不是《斯坦福哲学百科》的词条（大小写、保护花括号都不影响判定）。"""
    return SEP_BOOKTITLE in _sep_key(fields.get("booktitle", ""))


def _sep_url_from_howpublished(value: str) -> str:
    """从官网的 `howpublished = {\\url{…}}` 里取出 URL（第 84 条）。

    只做「取出来」这一件事，不做任何猜测：取不到就返回空串，调用方原样保留。
    """
    m = re.search(r"\\url\s*\{([^{}]+)\}", value)
    if m:
        return m.group(1).strip()
    m = re.search(r"https?://\S+", value)
    if m:
        return m.group(0).rstrip(".,;").strip()
    return ""


def _sep_fixup(key: str, etype: str, new_fields: list[tuple[str, str]],
               notes: list[str]) -> list[tuple[str, str]]:
    """《斯坦福哲学百科》条目的字段修正（第 84 条）。

    为什么需要它：编辑部 2026-10-15 定的口径是「SEP 词条直接照官网的 BibTeX 抄」
    （见 `slcn/refmark.py` 第一节第 2 条）。可**官网那份是 biblatex 形状**，直接并进
    `reference.bib` 会带来两个问题：
      · `howpublished = {\\url{…}}` —— 我们的样式只认 `url`，这一项**印不出来**
        （成品 `10 Bo_Chen` 的 `Steinberger2022` 用的是 `url = {…}`）；
      · `urldate` —— 本刊不写访问日期（`DROP_FIELDS` 本来就会删，这里再确认一次）。

    ⚠️ 判据**只看 `booktitle`**（不是 SEP 的条目一个字都不动，逐字节保持原样）；
    ⚠️ **只在没有 `url` 时**才把 `howpublished` 搬过去（已有 `url` 时以 `url` 为准，不覆盖）；
    ⚠️ 保护花括号（`{The {Stanford} Encyclopedia…}` / `{{F}all 2025}`）**不在这里剥**：
    那属于「照规范写」的范畴（refmark 那张表教 AI/编辑怎么写），工具静默改写反而可能
    改变印出来的大小写——宁可留给人。
    """
    names = {n for n, _ in new_fields}
    if "howpublished" not in names:
        return new_fields
    has_url = any(n == "url" and v.strip() for n, v in new_fields)
    out: list[tuple[str, str]] = []
    for n, v in new_fields:
        if n != "howpublished":
            out.append((n, v))
            continue
        if has_url:
            notes.append(f"{key}: SEP 条目删除 howpublished（已有 url，以 url 为准）")
            continue
        url = _sep_url_from_howpublished(v)
        if url:
            out.append(("url", url))
            notes.append(f"{key}: SEP 条目 howpublished → url（{url[:60]}…）"
                         if len(url) > 60 else f"{key}: SEP 条目 howpublished → url（{url}）")
        else:
            out.append((n, v))          # 取不出 URL：原样留着，绝不猜
    return out


def is_english_book(fields: dict[str, str]) -> bool:
    """英文书籍？（书名非空、且没有汉字/假名 —— 只看书名，与字段清理口径无关）"""
    ti = fields.get("title") or ""
    return bool(ti.strip()) and not re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", ti)


def _strip_outer_parens(s: str) -> str:
    """`(Updated Edition)` → `Updated Edition`（只剥最外层那一对，且必须配对）。"""
    t = s.strip()
    if t.startswith("(") and t.endswith(")") and t.count("(") == 1 and t.count(")") == 1:
        return t[1:-1].strip()
    if t.startswith("（") and t.endswith("）"):
        return t[1:-1].strip()
    return t


def book_edition_into_title(key: str, etype: str, new_fields: list[tuple[str, str]],
                            notes: list[str]) -> list[tuple[str, str]]:
    """第 86 条：**英文书籍的版次并进 `title`**（编辑部 2026-10-15 定，用户要求自动化）。

    为什么：本刊模板的 `reference.bib` 里**一个 `edition` 字段都没有**，版次跟着书名写：
    `title = {The Uses of Argument, (Updated Edition)}`。来稿自带的 bib 却常单列
    `edition = {2nd}`（全库扫到 275 条），照原样编出来版次会跑到别的位置去。

    ⚠️ 只在**能安全判定**时动手，其余一律原样留着（宁缺勿编）：
      · 只对 `@book`；
      · **SEP 词条除外**（它的 `edition` 是存档季节，见第 84 条）；
      · 书名必须**没有汉字/假名**（这条口径说的是「英文书籍」；中文书的版次写法没定，别乱搬）；
      · `title` 与 `edition` 都要有值；书名里**已经**出现这个版次的字样 → 当成已经写好，不重复搬。
    """
    if etype != "book":
        return new_fields
    f = dict(new_fields)
    if is_sep_entry(f) or not is_english_book(f):
        return new_fields
    ed = (f.get("edition") or "").strip()
    ti = (f.get("title") or "").strip()
    if not ed or not ti:
        return new_fields
    ed_inner = _strip_outer_parens(ed)
    if not ed_inner or ed_inner.lower() in ti.lower():
        return new_fields

    new_title = f"{ti.rstrip(' ,，')}, ({ed_inner})"
    out: list[tuple[str, str]] = []
    for n, v in new_fields:
        if n == "title":
            out.append(("title", new_title))
        elif n == "edition":
            notes.append(f"{key}: 英文书版次并入 title（edition = {{{ed}}} → 书名末尾 `, ({ed_inner})`）")
            continue
        else:
            out.append((n, v))
    return out


def move_book_editions(text: str) -> tuple[str, list[str]]:
    """只做**一件事**：把英文 `@book` 单列的 `edition` 并进 `title`（第 86 条）。

    为什么要有这个「窄版本」：`normalize_bib()` 只跑在 **LaTeX 通道**
    （`slcn/build.py: build_latex`）；**Word 通道**的 `reference.bib` 是
    `docxsource.refs_to_bib()` / `footnotes.entry_to_bib()` 直接渲染出来的，没经过规范化。
    于是同一部书走 LaTeX 通道会被搬、走 Word 通道不会——实测 `2026_02_Lu_Jiang`（英文 Word 稿）
    的三条 `Garson2013*` 就是这样漏下来的。

    ⚠️ 与 `normalize_bib()` 的区别（**故意**的）：这里**只改命中的那一条**，而且是**文本级**地
    只替换 `title` 的值、只删掉 `edition` 那一项 —— 其余条目、其余字段、缩进与对齐**逐字节不动**。
    """
    notes: list[str] = []
    out: list[str] = []
    for head, fields, tail in split_entries(text):
        m = re.match(r"@(\w+)\s*\{\s*([^,]+)\s*,", head)
        if not m:
            out.append(head + fields + tail)
            continue
        etype, key = m.group(1).lower(), m.group(2).strip()
        flat = {n: v for n, v in parse_fields(fields)}
        if etype != "book" or is_sep_entry(flat) or not is_english_book(flat):
            out.append(head + fields + tail)
            continue
        ed = (flat.get("edition") or "").strip()
        ti = (flat.get("title") or "").strip()
        inner = _strip_outer_parens(ed)
        if not ed or not ti or not inner or inner.lower() in ti.lower():
            out.append(head + fields + tail)
            continue
        # ① 只替换 title 的值（单行值；Word 通道生成的书名本来就是单行）
        tm = re.search(r"(?m)^([ \t]*title[ \t]*=[ \t]*\{)([^\n]*?)(\}[ \t]*,?[ \t]*)$", fields)
        if not tm:
            out.append(head + fields + tail)
            continue
        new_title = f"{ti.rstrip(' ,，')}, ({inner})"
        fields = fields[:tm.start(2)] + new_title + fields[tm.end(2):]
        # ② 删掉 edition 那一项（连它的逗号与行尾换行一起）
        em = re.search(r"(?m)^[ \t]*edition[ \t]*=[ \t]*\{[^\n]*\}[ \t]*,?[ \t]*\n", fields)
        if em:
            fields = fields[:em.start()] + fields[em.end():]
        notes.append(f"{key}: 英文书版次并入 title（edition = {{{ed}}} → 书名末尾 `, ({inner})`）")
        out.append(head + fields + tail)
    joined = "\n".join(out)
    # `split_entries()` 的 tail **不含条目后的换行**，而 `"\n".join` 只在条目之间补 ——
    # 于是文件末尾那个（或那几个）换行会被吃掉。逐字节不变量要求保住它，这里补回来。
    tail_nl = len(text) - len(text.rstrip("\n"))
    out_nl = len(joined) - len(joined.rstrip("\n"))
    if tail_nl > out_nl:
        joined += "\n" * (tail_nl - out_nl)
    return joined, notes


def normalize_bib(text: str) -> tuple[str, list[str]]:
    """规范化一份 .bib，返回 (新文本, 改动说明列表)。"""
    notes: list[str] = []
    entries = split_entries(text)
    out: list[str] = []
    for head, fields, tail in entries:
        m = re.match(r"@(\w+)\s*\{\s*([^,]+)\s*,", head)
        if not m:
            out.append(head + fields + tail)
            continue
        etype, key = m.group(1), m.group(2).strip()

        new_fields: list[tuple[str, str]] = []
        seen: dict[str, int] = {}
        for name, value in parse_fields(fields):
            canon = FIELD_ALIASES.get(name.lower(), name.lower())
            if canon != name.lower():
                notes.append(f"{key}: 字段 {name} → {canon}")
            cleaned = _clean_field_value(canon, value)
            if cleaned is None:
                notes.append(f"{key}: 删除字段 {name}")
                continue
            if canon in seen:  # 同一字段重复（如 issue→number 撞车）
                idx = seen[canon]
                if len(cleaned) > len(new_fields[idx][1]):
                    new_fields[idx] = (canon, cleaned)
                notes.append(f"{key}: 合并重复字段 {canon}")
                continue
            seen[canon] = len(new_fields)
            if cleaned != value.strip():
                notes.append(f"{key}: 字段 {canon} 值已清理")
            new_fields.append((canon, cleaned))

        # 自动补 userf：中文条目没填就补 cn（实测全库有 82 条中文漏填）
        if not any(n == "userf" for n, _ in new_fields):
            au = next((v for n, v in new_fields if n in ("author", "editor")), "")
            ti = next((v for n, v in new_fields if n == "title"), "")
            uf = detect_userf(au, ti)
            if uf:
                new_fields.append(("userf", uf))
                notes.append(f"{key}: 补 userf = {{{uf}}}")

        # 第 84 条：《斯坦福哲学百科》条目照官网 bib 抄进来时的两处形状修正
        # （只在 booktitle 命中时生效；其它条目一个字节都不动）。
        if any(n == "booktitle" and is_sep_entry({"booktitle": v})
               for n, v in new_fields):
            new_fields = _sep_fixup(key, etype, new_fields, notes)

        # 第 86 条：英文书籍的版次并进 title（来稿自带 bib 常见 `edition = {2nd}`）。
        # 只对能安全判定的条目动手，判据见函数注释。
        new_fields = book_edition_into_title(key, etype, new_fields, notes)

        width = max((len(n) for n, _ in new_fields), default=4)
        body = ",\n".join(f"  {n.ljust(width)} = {{{v}}}" for n, v in new_fields)
        out.append(f"@{etype}{{{key},\n{body}\n}}\n")

    return "\n".join(out), notes


def strip_bib_comments(text: str) -> str:
    r"""把 **整行注释**（行首可选空白后以 `%` 开头，到行尾）替换成空串，换行原样保留。

    为什么只认整行（第 133 条，2026-10-18）：BibTeX/biber 的注释语义是「`%` 起到行尾」。
    作者交来的 `.bib` 里常见**整行注释掉的旧模板/重复条目**（如 `% @article{,`、
    `% @misc{z16,`）。`split_entries()` 用全文正则 `@\w+\s*\{` 会把注释里的 `@` 当成
    真条目（而且匹配从 `@` 起、`% ` 前缀被丢掉）→ 产物 `reference.bib` 出现空键
    `@article{,` 或重复键 → biber 语法报错、**整份 bib 报废**（2023(71)04 实测：
    115 处 undefined citation、参考文献区未生成）。

    **不做行内截断**（不猜 `%` 在行中算什么）：`note = {50\%}`、`title = {A % B}` 这类
    行内/值内 `%` 原样保留。代价是「字段值里整行以 `%` 开头」的极端写法会被当注释删掉
    ——与 TeX 的整行注释口径一致，已写进文档边界。
    """
    return re.sub(r"(?m)^[ \t]*%[^\r\n]*", "", text)


_NON_BIB_ENTRY = re.compile(r"@(?:comment|string|preamble)\s*\{", re.I)


def _strip_non_bib_blocks(text: str) -> str:
    r"""删掉 `@comment{…}` / `@string{…}` / `@preamble{…}` 块（花括号配平）。

    88-05 实测：JabRef 在文件末尾写 `@Comment{jabref-meta: databaseType:bibtex;}`，
    该条目**没有逗号**；旧 `split_entries` 用「body.find(',')」会把紧随其后的
    `@InProceedings{Mkrtychev1997_LP,` 的键一起吞进 head → 整条文献丢失、
    正文 `\cite{Mkrtychev1997_LP}` 变成悬空引用。非文献块不是条目，直接删除。
    """
    out: list[str] = []
    pos = 0
    for m in _NON_BIB_ENTRY.finditer(text):
        if m.start() < pos:
            continue
        out.append(text[pos:m.start()])
        depth = 0
        j = m.end() - 1
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        pos = min(len(text), j + 1)
    out.append(text[pos:])
    return "".join(out)


def split_entries(text: str) -> list[tuple[str, str, str]]:
    """把 bib 拆成 [(条目头, 字段体, 尾), ...]，非条目文本不随条目返回。

    第 133 条：先剥掉**整行 `%` 注释**（`strip_bib_comments()`），注释掉的旧条目不会被
    当成真条目；返回值结构与顺序不变（正常 bib 的结果与从前逐字节相同）。
    """
    text = _strip_non_bib_blocks(strip_bib_comments(text))
    result: list[tuple[str, str, str]] = []
    for m in re.finditer(r"@\w+\s*\{", text):
        start = m.start()
        i = text.index("{", start)
        # 配平花括号找条目结尾
        depth = 0
        j = i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = text[i + 1 : j]
        # 头 = '@type{key,'  体 = 中间  尾 = '}'
        k = body.find(",")
        head = text[start : i + 1] + body[: k + 1]
        result.append((head, body[k + 1 :], "}"))
    return result


def parse_fields(fields: str) -> list[tuple[str, str]]:
    """解析 'name = {value}' 序列。"""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"(?<![A-Za-z0-9_-])([A-Za-z][\w-]*)\s*=\s*", fields):
        name = m.group(1)
        i = m.end()
        while i < len(fields) and fields[i] not in "{\"":
            if fields[i] == ",":
                break
            i += 1
        if i >= len(fields) or fields[i] == ",":
            continue
        if fields[i] == "{":
            depth = 0
            j = i
            while j < len(fields):
                if fields[j] == "{":
                    depth += 1
                elif fields[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append((name, fields[i + 1 : j]))
        else:  # "..." 形式
            # ⚠️ 引号值里允许 `{...}` 组（如 `G{\"a}rdenfors`）与 `\"` 转义引号：
            #    不能简单找下一个 `"`，否则 `Alchourr\'{o}n, C. and G{\"a}rdenfors, P.` 会被
            #    截成 `G{\`（第 45 批 89-01 的 biber syntax error 根因）。
            j = i + 1
            depth = 0
            while j < len(fields):
                char = fields[j]
                if char == "\\":
                    j += 2
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth = max(0, depth - 1)
                elif char == '"' and depth == 0:
                    break
                j += 1
            if j >= len(fields):
                # 引号未闭合：保持旧契约（norm 命令要把它标成「异常」而不是静默截断）
                raise ValueError(f"字段 {name} 的引号值未闭合")
            out.append((name, fields[i + 1 : j]))
    return out


# ── \\bibitem → .bib ──────────────────────────────────────────────────────


def bibitems_to_bib(text: str) -> tuple[str, list[str]]:
    """把 \\bibitem{key} ... 列表转成粗略的 .bib 条目。

    只做结构骨架：类型默认 @article，author/title/year 尽量拆出来，
    其余整条塞进 note 留待人工或 AI 订正。
    """
    notes: list[str] = []
    items = re.findall(
        r"\\bibitem(?:\[[^\]]*\])?\{([^}]*)\}\s*(.*?)(?=\\bibitem|\Z)", text, re.S
    )
    if not items:
        return "", ["未找到 \\bibitem 条目"]

    out = ["% 由 \\bibitem 自动转换，请逐条核对类型与字段。", ""]
    for key, body in items:
        body = re.sub(r"\s+", " ", body).strip()
        year = ""
        ym = re.search(r"\((\d{4})\)", body)
        if ym:
            year = ym.group(1)
        title = ""
        tm = re.search(r"[.,]\s*([^.]{6,120}?)\.\s", body)
        if tm:
            title = tm.group(1).strip()
        out.append(f"@article{{{key},")
        out.append(f"  title  = {{{title}}},")
        out.append(f"  year   = {{{year}}},")
        out.append(f"  note   = {{{body}}}")
        out.append("}")
        out.append("")
        notes.append(f"{key}: 骨架已生成，note 字段待人工拆分")
    return "\n".join(out), notes


# ── 把「整条在 note 里」的脚注体例文献拆成结构化条目 ──────────────────────

_BIB_FIELDS = ("author", "title", "journal", "booktitle", "volume", "number",
               "pages", "publisher", "address", "year")


def expand_note_entries(bib: str) -> tuple[str, list[str]]:
    """对只含 note 的条目，尝试拆成结构化字段。

    全库实测 244 条这类条目（占 10%）：
      · **188 条**可拆出 author/title/year（title 100%、year 99%、author 60%）
      · **56 条**其实是脚注正文而非文献，保持原样

    ⚠️ **保留原文**：拆出来的条目仍把原始文本留在 `note` 里。这样即使拆错了，
    编辑也能对照原文改回来——参考文献是学术事实，不能因为解析器的判断就丢掉依据。

    ⚠️ 注意：`bibcheck.split_entries` 返回 `(类型, 键, 字段体)`，
    而本模块自己的 `split_entries` 返回 `(条目头, 字段体, 尾)`——**签名不同，别混用**。
    """
    from . import footref
    from .footnotes import _bib_escape, normalize_authors
    from .bibcheck import fields_of, split_entries as split3

    out: list[str] = []
    notes: list[str] = []
    for et, key, body in split3(bib):
        f = fields_of(body)
        if f.get("author") or f.get("title") or len(f.get("note", "")) <= 40:
            out.append(f"@{et}{{{key},{body}\n}}")
            continue
        parsed = footref.parse_note(f["note"])
        if not parsed:
            out.append(f"@{et}{{{key},{body}\n}}")  # 判为非文献，原样保留
            continue
        for i2, p in enumerate(parsed, 1):
            k = key if len(parsed) == 1 else f"{key}{chr(96 + i2)}"
            vals = {fld: getattr(p, fld) for fld in _BIB_FIELDS if getattr(p, fld)}
            vals["note"] = f["note"].strip().replace("\n", " ")
            # 第 141 条（2026-10-18，期 87 子案例 9 / 10 Wei_Chen）：写回 .bib 前必须
            # ① 把 author 里的 `&` 规范成 BibTeX 的 `and`（否则 biblatex 在
            #    `\namepartsuffix` 里遇到 `&` 报 Misplaced alignment tab，整篇编译失败）；
            # ② 转义 LaTeX 特殊字符（`& % $ # _`）。从前的实现两件都没做，
            #    解析出的原值直接落进产物。
            if vals.get("author"):
                vals["author"] = normalize_authors(vals["author"])
            if vals.get("editor"):
                vals["editor"] = normalize_authors(vals["editor"])
            vals = {n: _bib_escape(str(v)) for n, v in vals.items()}
            lines = "\n".join(f"  {n.ljust(9)} = {{{v}}}," for n, v in vals.items())
            out.append(f"@{p.etype}{{{k},\n{lines}\n}}")
        notes.append(f"{key}: 从 note 拆出 {len(parsed)} 条（原文保留在 note）")
    return "\n\n".join(out) + "\n", notes


# ══════════════════════════════════════════════════════════════════════════
# 批68 B-021：Word 通道文献区解析修复（86-10 / 90-06 / 90-07 三条真实缺陷）
# ══════════════════════════════════════════════════════════════════════════
#
# 三条缺陷的**根因都在 `docxsource` 的文献区行级解析**（一批 68 暂不下沉，登记为
# 技术债；本批与 B-022 并行、文件互斥，故只在 bib-refs 自己的模块里做**解析结果
# 修复**）：
#   · `_numbered_refs()` 一条编号 = 一条条目 → 同一行里的第 2 条起被吞并（86-10：
#     编号 [4] 一行 3 条、[13] 一行 3 条 → 生成 15 vs 人工 18）；
#   · 人年制行解析按逗号切段 → `X著，Y译，《书名》，出版社，年。` 的**题名位被译者
#     占用**（90-06：title={周煦良、汤永宽译}，rest={《存在主义…》…}）；
#   · 编号清单里中文条目 `作者：《题名》，《期刊》，年` 的字段被切错（90-07：
#     author={陈晓平：《…》} / title={期刊名} / title={译者}）；
#   · 只有 note 的脚注引注条目（题名/作者空）仍进了 bib → 参考文献区出现**空题名
#     碎片**（90-06 的 ref01–03、90-07 的 ref03–05）。
#
# 修法纪律（spec §4）：**不重写解析器**；只做形状识别 + 有证据的合并；任何一步判据
# 不满足 → **保留原条目 + warning**（不猜、不丢、不编造）。四条判据互相独立，
# 未命中的文档三个属性（`doc.refs`/`doc.bib_entries`/`doc.body`）逐字节不变。
#
# ⚠️ 调用点在 `build.build_docx()`（`parse_docx()` 之后、正文引用替换与 bib 写出之前）；
#    `docx_audit` / `parse_docx` 原路径不经过这里，故冻结基线不受影响。

_ENTRY_KIND_TO_TYPE = {
    "M": "book", "J": "article", "C": "incollection", "P": "inproceedings",
    "D": "phdthesis", "N": "newspaper", "R": "report",
}

#: 号码行 `[12] …`（也接受全角括号/圆括号；与 docxsource 的编号口径一致）
_NUMBERED_HEAD_RE = re.compile(r"^\s*[\[\(（]\s*(\d{1,3})\s*[\]\)）]\s*(.+)$", re.S)

#: 编号清单里「作者,YYYY,」形态的年份位（用于找同段多文献的段边界）
_YEAR_IN_LINE_RE = re.compile(r",\s*((?:19|20)\d{2})\s*[,，]")

_NAME_SEG = r"[A-Z][A-Za-z.'\-]*(?:\s*,\s*[A-Z][A-Za-z.'\-]*)*"
_NAME_RE = re.compile(rf"^{_NAME_SEG}(?:\s*(?:&|and)\s*{_NAME_SEG})*$")

_CN_VOL_RE = re.compile(
    r"^(第[一二三四五六七八九十百\d]+[卷版册]|[上下中]卷|修订版|增订版)")

_CN_CITE_RE = re.compile(r"^\s*(?:Cf\.?|参见|见)\s*", re.I)


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fa5]", s or ""))


def _norm_key(s: str) -> str:
    """比较用的归一：只留字母/数字/汉字（题名/作者模糊匹配）。"""
    return re.sub(r"[^\w\u4e00-\u9fa5]+", "", (s or "").lower())


def _looks_like_name(s: str) -> bool:
    """同段多文献的段首：`Surname, I.` / `Surname.I` / `A, B` / `A & B` 形态。"""
    s = s.strip()
    if not s or len(s) > 60 or not re.match(r"^[A-Z]", s):
        return False
    if len(re.sub(r"[^A-Za-z]", "", s)) < 2:
        return False
    return bool(_NAME_RE.fullmatch(s))


def _multi_ref_anchor(rest: str, end: int) -> int | None:
    """从年份位 `,YYYY,` 往回找该段的段首（最近的 `;；.。` 之后若像姓名）。"""
    for b in range(end - 1, max(0, end - 90) - 1, -1):
        if rest[b] in ";；.。":
            if _looks_like_name(rest[b + 1:end]):
                return b + 1
    return None


def _looks_multi_ref(rest: str) -> bool:
    """行内是否有 ≥2 个 `姓名,YYYY,` 形态（用于「疑似多文献」的 warning 判据）。"""
    toks = list(_YEAR_IN_LINE_RE.finditer(rest))
    return len(toks) >= 2 and _looks_like_name(rest[:toks[0].start()])


def _split_multi_ref_line(rest: str) -> list[str] | None:
    """把一条编号行正文按 `姓名,YYYY` 锚点切成多段；不像多文献则返回 None。

    只认**英文式** `Surname, Initials, YYYY,` 锚点（中文多文献同段不会用这种混排，
    宁可不拆也不误拆）；首段必须从行首起、每段至少 12 字符，否则整体放弃。
    """
    toks = list(_YEAR_IN_LINE_RE.finditer(rest))
    if len(toks) < 2:
        return None
    if not _looks_like_name(rest[:toks[0].start()]):
        return None
    starts = [0]
    for tok in toks[1:]:
        a = _multi_ref_anchor(rest, tok.start())
        if a is None or a <= starts[-1]:
            return None
        starts.append(a)
    bounds = starts + [len(rest)]
    chunks: list[str] = []
    for i in range(len(starts)):
        chunk = rest[bounds[i]:bounds[i + 1]].strip()
        chunk = re.sub(r"[\s;；.。，,]+$", "", chunk)
        if len(chunk) < 12:
            return None
        chunks.append(chunk)
    return chunks


def _split_address_publisher(rest: str) -> tuple[str, str]:
    """从 `出版地: 出版社` / `出版社，年` 里拆出地址与出版社（拆不出返回空串）。"""
    rest = re.sub(r"^[.。]\s*", "", rest or "").strip()
    m = re.search(r"([^:：,，]{2,30})[:：]\s*([^,，.。；;]{2,60})", rest)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    parts = [p.strip(" .，,。") for p in re.split(r"[，,]", rest) if p.strip()]
    parts = [p for p in parts if not re.fullmatch(r"(?:19|20)\d{2}[年.]?", p)]
    if parts:
        cand = parts[0]
        if cand and not re.search(r"\d", cand) and len(cand) <= 60:
            return "", cand
    return "", ""


def _strip_title_quotes(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r'^[“"\'‘]+', "", t)
    t = re.sub(r'[”"\'’]+$', "", t)
    return t.strip().rstrip(",，。.")


def _entry_from_refentry(e) -> dict:
    """把行解析器给的 `RefEntry` 转成 bib 条目 dict（编号清单通道同形）。"""
    etype = _ENTRY_KIND_TO_TYPE.get(str(getattr(e, "kind", "") or "").upper(), "book")
    d: dict = {
        "_type": etype,
        "author": (getattr(e, "author", "") or "").strip(),
        "title": _strip_title_quotes(getattr(e, "title", "") or ""),
        "year": str(getattr(e, "year", "") or ""),
    }
    rest = re.sub(r"^[.。]\s*", "", (getattr(e, "rest", "") or "").strip())
    if etype == "book":
        addr, pub = _split_address_publisher(rest)
        if addr:
            d["address"] = addr
        if pub:
            d["publisher"] = pub
    elif etype == "article":
        m = re.search(
            r"^(?P<j>[^,，]+?)\s*[,，]\s*(?P<v>\d+)\s*"
            r"(?:\((?P<n>[^)]*)\))?\s*[:：]\s*(?P<p>[A-Za-z]?\d[\w\-–—]*)\s*[.。]?\s*$",
            rest)
        if m:
            d["journal"] = m.group("j").strip()
            d["volume"] = m.group("v")
            if m.group("n"):
                d["number"] = m.group("n")
            d["pages"] = m.group("p").replace("-", "--")
        elif rest:
            d["note"] = rest
    elif rest:
        d["note"] = rest
    return d


def _parse_cn_numbered_line(raw: str) -> dict | None:
    """解析中文编号条目：`作者：《题名》，《期刊》，年` / `作者：《书名》，译者译，地：社，年`。

    只认**行内证据**（题名来自《》、年份来自行内 4 位数字、期刊/出版社来自显式
    标点段）；① 译著、② 期刊论文、③ 编著章节 三种形态之外一律返回 None（保留原条目）。
    """
    m = _NUMBERED_HEAD_RE.match(raw or "")
    s = (m.group(2) if m else (raw or "")).strip()
    if not _has_cjk(s) or "《" not in s:
        return None
    colon = re.search(r"[：:]", s)
    if not colon:
        return None
    author = s[:colon.start()].strip()
    if not author or len(author) > 30 or "《" in author:
        return None
    body = s[colon.end():].strip()
    tm = re.search(r"《([^》]{1,120})》", body)
    if not tm:
        return None
    title = tm.group(1).strip()
    tail = body[tm.end():].strip()
    # 题名后的卷次/版次：`（第四版）` 或直接 `第三卷`
    vm = re.match(r"^[（(]([^）)]{1,20})[）)]", tail)
    if vm:
        title += f"（{vm.group(1).strip()}）"
        tail = tail[vm.end():].strip()
    else:
        vm = _CN_VOL_RE.match(tail)
        if vm:
            title += f"（{vm.group(1)}）"
            tail = tail[vm.end():].strip()
    tail = tail.lstrip("，,。.；;、 ")
    years = re.findall(r"(?:19|20)\d{2}", s)
    if not years:
        return None
    d: dict = {"author": author, "title": title, "year": years[-1]}
    # ① 译著：`译者译，出版地：出版社，年`
    mt = re.match(r"^([\u4e00-\u9fa5•·、\s]{1,40}?(?:等)?译)\s*[，,]\s*(.+)$", tail)
    if mt:
        trans = re.sub(r"\s+", "", mt.group(1))
        d["_type"] = "book"
        d["author"] = f"{author}（著），{trans[:-1]}（译）"
        addr, pub = _split_address_publisher(mt.group(2))
        if addr:
            d["address"] = addr
        if pub:
            d["publisher"] = pub
        return d
    # ② 期刊论文：`《刊名》年/期`
    mj = re.match(r"^《([^》]{1,60})》(.*)$", tail)
    if mj:
        d["_type"] = "article"
        d["journal"] = mj.group(1).strip()
        rest = mj.group(2).strip().lstrip("，,。. ")
        mn = re.search(r"第\s*(\d+)\s*期", rest)
        if mn:
            d["number"] = mn.group(1)
        mv = re.search(r"第\s*(\d+)\s*卷", rest)
        if mv:
            d["volume"] = mv.group(1)
        return d
    # ③ 编著章节：`编者（主编）：《书名》卷次，出版信息`
    me = re.match(
        r"^([^：:《，,]{1,20})\s*(?:（主编）|\(主编\)|主编)?\s*[：:]\s*《([^》]{1,80})》(.*)$",
        tail)
    if me:
        d["_type"] = "incollection"
        editor, booktitle, rest = me.group(1).strip(), me.group(2).strip(), me.group(3)
        mv = _CN_VOL_RE.match(rest.strip())
        if mv:
            booktitle += f"（{mv.group(1)}）"
            rest = rest.strip()[mv.end():]
        d["booktitle"] = booktitle
        if editor:
            d["note"] = f"{editor}（主编）"
        addr, pub = _split_address_publisher(rest.lstrip("，,。. "))
        if addr:
            d["address"] = addr
        if pub:
            d["publisher"] = pub
        return d
    return None


def _author_surname(a: str) -> str:
    s = _CN_CITE_RE.sub("", (a or "").strip())
    s = re.sub(r"[（(][^）)]*[）)]", "", s).strip(" ,，.。;；")
    if not s:
        return ""
    if "," in s:
        return _norm_key(s.split(",", 1)[0])
    if _has_cjk(s):
        return _norm_key(s[:4])
    toks = [t for t in re.split(r"\s+", s) if t]
    return _norm_key(toks[-1]) if toks else ""


def _fragment_identity(text: str) -> tuple[str, str] | None:
    """从「只有 note 的脚注条目」正文里抽 (作者, 题名)；抽不出返回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    t = _CN_CITE_RE.sub("", t)
    if _has_cjk(t) and "《" in t:
        d = _parse_cn_numbered_line(t)
        if d and d.get("title"):
            return str(d.get("author") or ""), str(d["title"])
        m = re.search(r"^(.{1,30}?)[：:]\s*《([^》]{2,120})》", t)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    # 引号题名形态优先（`Surname, I., “Title,”…`）：比整行解析器更能扛缺年份的短引注
    m = re.search(
        r'^(.{2,40}?),\s*(?:[A-Z][\w.\- ]{0,20},\s*)?[“"]([^”"]{4,150})[”"]', t)
    if m:
        return m.group(1).strip(), m.group(2).strip().rstrip(",，")
    try:
        from .docxsource import parse_reference_line

        e = parse_reference_line(t)
        if e is not None and (e.title or "").strip() and (e.author or "").strip():
            return e.author.strip(), _strip_title_quotes(e.title)
    except Exception:  # noqa: BLE001  解析器异常只是「这条修不了」，不外抛
        pass
    return None


def _looks_like_ref_text(t: str) -> bool:
    """note 文本是否**像**文献条目（用来区分「真碎片」与普通说明性脚注）。"""
    return bool(re.search(r"(?:19|20)\d{2}|《|Vol\.|pp?\.\s*\d|[“”\"]|\[[MJDC]\]",
                          t or ""))


def _match_target(author: str, title: str,
                  candidates: list[tuple[str, str, str]]) -> str | None:
    """按「题名相似度 + 作者姓氏」在既有条目里找碎片应并入的目标；找不到返回 None。"""
    import difflib

    tt = _norm_key(title)
    if len(tt) < 4:
        return None
    an = _author_surname(author)
    best: str | None = None
    best_score = 0.0
    for key, a2, t2 in candidates:
        nt = _norm_key(t2)
        if len(nt) < 4:
            continue
        if tt == nt:
            score = 1.0
        elif (len(tt) >= 6 and len(nt) >= 6
              and (tt in nt or nt in tt)):
            score = 0.96 if min(len(tt), len(nt)) >= 8 else 0.9
        else:
            score = difflib.SequenceMatcher(None, tt, nt).ratio()
        a2n = _author_surname(a2)
        if an and a2n and an != a2n:
            if tt != nt:      # 作者不同时只有「题名完全相同」才敢并
                continue
            score -= 0.05
        if score > best_score:
            best, best_score = key, score
    return best if best_score >= 0.88 else None


def _entry_author_title(entry: dict) -> tuple[str, str]:
    return str(entry.get("author") or ""), str(entry.get("title") or "")


def _rewrite_body_cites(body: str, keymap: dict[str, str]) -> str:
    """把正文 `\\parencite[loc]{旧键}` 里的旧键改写成并集后的仍存活键。"""
    for old, new in keymap.items():
        pat = re.compile(
            r"\\(parencite|cite|textcite|footcite|autocite)((?:\[[^\]]*\])*)\{"
            + re.escape(old) + r"\}")
        body = pat.sub(
            lambda m, _new=new: "\\" + m.group(1) + m.group(2) + "{" + _new + "}", body)
    return body


def _repair_word_refs_impl(doc) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    warnings: list[str] = []
    refs = list(getattr(doc, "refs", []) or [])
    entries = list(getattr(doc, "bib_entries", []) or [])
    body = getattr(doc, "body", "") or ""
    raw_by_key = dict(getattr(doc, "ref_raw_by_key", {}) or {})
    num2key = dict(getattr(doc, "ref_key_by_num", {}) or {})
    if not refs and not entries:
        return notes, warnings
    numbered_keys = set(num2key.values())

    # ── ① 同段多文献：编号行里 ≥2 条文献 → 逐段解析（86-10 型）──────────
    existing_titles = {_norm_key(_entry_author_title(e)[1]) for _k, e, _r in entries}
    existing_titles |= {_norm_key(getattr(r, "title", "")) for r in refs}
    existing_titles.discard("")
    out_entries: list[tuple[str, dict, str]] = []
    used_keys = {k for k, _e, _r in entries} | {getattr(r, "key", "") for r in refs}
    extra_added = 0
    for key, ent, raw in entries:
        out_entries.append((key, ent, raw))
        full = raw_by_key.get(key, "")
        if key not in numbered_keys and not full:
            continue
        hm = _NUMBERED_HEAD_RE.match(full)
        if not hm:
            continue
        chunks = _split_multi_ref_line(hm.group(2))
        if not chunks or len(chunks) < 2:
            if _looks_multi_ref(hm.group(2)):
                warnings.append(
                    f"同段多文献行按判据拆不动（已保留原条目，请人工核对）：{full[:60]}…")
            continue
        parsed: list[dict] = []
        try:
            from .docxsource import parse_reference_line
        except Exception:  # noqa: BLE001
            parse_reference_line = None  # type: ignore[assignment]
        for chunk in chunks:
            e = parse_reference_line(chunk) if parse_reference_line else None
            if e is None or not (getattr(e, "title", "") or "").strip():
                parsed = []
                break
            parsed.append(_entry_from_refentry(e))
        if len(parsed) != len(chunks) or not parsed:
            warnings.append(
                f"同段多文献行按判据拆不动（已保留原条目，请人工核对）：{full[:60]}…")
            continue
        # 首段是本来就存在的那条 → 用行内证据把字段归位（题名/出版信息不再串行）
        old_ent = out_entries[-1][1]
        new_first = dict(parsed[0])
        new_first.setdefault("note", old_ent.get("note", ""))
        out_entries[-1] = (key, new_first, raw)
        n_new = 0
        for extra in parsed[1:]:
            tkey = _norm_key(extra.get("title", ""))
            if not tkey or tkey in existing_titles:
                continue      # 同一行里重复引用的同一文献（如 [13] 里的已有条目）→ 不重复加
            try:
                from .footnotes import make_key as _mk

                ekey = _mk(extra, len(out_entries) + 1, used_keys)
            except Exception:  # noqa: BLE001
                ekey = f"repair{len(out_entries) + 1}"
                while ekey in used_keys:
                    ekey += "x"
                used_keys.add(ekey)
            out_entries.append((ekey, extra, ""))
            existing_titles.add(tkey)
            n_new += 1
            notes.append(
                f"{key}: 同段多文献 → 拆出新条目 {ekey}"
                f"（《{str(extra.get('title', ''))[:40]}》）")
        if n_new:
            extra_added += n_new
    entries = out_entries
    if extra_added:
        notes.append(f"同段多文献：编号清单共拆出 {extra_added} 条被吞并的文献")

    # ── ② 中文编号条目字段归位（90-07 型）───────────────────────────────
    fixed_cn = 0
    fixed_keys: list[str] = []
    for i, (key, ent, raw) in enumerate(entries):
        if key not in numbered_keys:
            continue
        full = raw_by_key.get(key, "")
        if not full:
            continue
        title = str(ent.get("title") or "")
        author = str(ent.get("author") or "")
        fragment = (
            not title
            or "》" in title
            or title.strip().endswith("译")
            or "：" in author
            or "《" in author
        )
        if not fragment:
            continue
        parsed = _parse_cn_numbered_line(full)
        if parsed is None:
            continue
        entries[i] = (key, parsed, raw)
        fixed_cn += 1
        fixed_keys.append(key)
    if fixed_cn:
        notes.append(
            f"中文编号条目字段归位：{fixed_cn} 条"
            + ("（" + "、".join(fixed_keys[:6]) + ("…" if len(fixed_keys) > 6 else "") + "）"
               if fixed_keys else ""))

    # ── ③ 译著拆条归位：`X著，Y译，《书名》，…`（90-06 型）────────────
    fixed_tr = 0
    for r in refs:
        title = str(getattr(r, "title", "") or "").strip()
        author = str(getattr(r, "author", "") or "").strip()
        rest = str(getattr(r, "rest", "") or "").strip()
        if not (title.endswith("译") and author.endswith("著") and "《" not in title):
            continue
        tm = re.match(r"^《([^》]{1,120})》\s*(.*)$", rest.lstrip("，,。. "))
        if tm is None:
            continue
        r.title = tm.group(1).strip()
        r.author = author[:-1].strip(" ，,、")
        r.kind = "M"
        r.rest = tm.group(2).strip(" ，,。.；;")
        fixed_tr += 1
        notes.append(
            f"{getattr(r, 'key', '')}: 译著拆条归位（题名←《{r.title[:40]}》，译者串不再占题名位）")
    if fixed_tr:
        notes.append(f"译著拆条：{fixed_tr} 条题名/作者归位")

    # ── ④ 空题名脚注碎片合并（90-06 / 90-07 型）────────────────────────
    # 只在**存在文末清单**（人年制 refs 或编号清单）时启用：碎片是「脚注引注没并回
    # 文末书目」，没有文末清单的文档（全脚注体例）里的重复条目属于另一类缺陷
    # （脚注指纹去重），不在 B-021 三条根因内 —— 保持默认零变化。
    if not refs and not num2key:
        return notes, warnings
    candidates: list[tuple[str, str, str]] = []
    cand_keys: set[str] = set()
    for r in refs:
        k = getattr(r, "key", "")
        a, t = _entry_author_title(
            {"author": getattr(r, "author", ""), "title": getattr(r, "title", "")})
        if k and t:
            candidates.append((k, a, t))
            cand_keys.add(k)
    for k, e, _r in entries:
        a, t = _entry_author_title(e)
        if t:
            candidates.append((k, a, t))
            cand_keys.add(k)
    keymap: dict[str, str] = {}
    kept: list[tuple[str, dict, str]] = []
    for key, ent, raw in entries:
        title = str(ent.get("title") or "").strip()
        if title:
            kept.append((key, ent, raw))
            continue
        ftext = str(ent.get("note") or raw or "")
        ident = _fragment_identity(ftext)
        if ident is None:
            if _looks_like_ref_text(ftext):
                warnings.append(
                    f"空题名脚注条目 {key} 无法从原文抽取作者/题名，已保留原条目："
                    f"{ftext[:60]}")
            kept.append((key, ent, raw))
            continue
        target = _match_target(ident[0], ident[1], candidates)
        if target is None or target == key:
            warnings.append(
                f"空题名脚注条目 {key} 未找到对应书目条目，已保留原条目（不猜）："
                f"{str(ent.get('note') or raw or '')[:60]}")
            kept.append((key, ent, raw))
            continue
        keymap[key] = target
        raw_by_key.pop(key, None)
        notes.append(
            f"{key}: 空题名碎片并入 {target}"
            f"（《{ident[1][:40]}》）")
    if keymap:
        body = _rewrite_body_cites(body, keymap)
        entries = kept
        notes.append(
            "空题名碎片合并："
            + "、".join(f"{o}→{n}" for o, n in keymap.items())
            + "（正文引用键同步改写）")

    if notes:
        try:
            doc.refs = refs
        except Exception:  # noqa: BLE001
            pass
        try:
            doc.bib_entries = entries
        except Exception:  # noqa: BLE001
            pass
        try:
            doc.body = body
        except Exception:  # noqa: BLE001
            pass
        try:
            doc.ref_raw_by_key = raw_by_key
        except Exception:  # noqa: BLE001
            pass
    return notes, warnings


def repair_word_refs(doc) -> tuple[list[str], list[str]]:
    r"""Word 文献区解析结果修复（批68 B-021；三条真实缺陷的最小侵入修法）。

    参数 `doc` 是 `docxsource.parse_docx()` 的返回值（只用到 `refs` / `bib_entries`
    / `body` / `ref_raw_by_key` / `ref_key_by_num`）。**原地修改**上述三个属性，
    返回 `(改动说明, 告警)` 两组字符串给调用方写日志/warning。

    纪律：
      · 默认零变化——四类判据都不命中的文档，三个属性一个字节都不动；
      · fail-soft——内部异常时回滚到调用前快照，只返回一条 warning，**不阻断出稿**；
      · 判据失败 → 保留原条目 + warning（不猜、不丢原文）。
    """
    import copy as _copy

    refs = getattr(doc, "refs", None)
    entries = getattr(doc, "bib_entries", None)
    if not refs and not entries:
        return [], []
    snap_refs = _copy.deepcopy(refs) if refs is not None else None
    snap_entries = _copy.deepcopy(entries) if entries is not None else None
    snap_body = getattr(doc, "body", "")
    snap_raw = dict(getattr(doc, "ref_raw_by_key", {}) or {})
    try:
        return _repair_word_refs_impl(doc)
    except Exception as e:  # noqa: BLE001  fail-soft：修复失败不得阻断出稿
        try:
            doc.refs = snap_refs
            doc.bib_entries = snap_entries
            doc.body = snap_body
            doc.ref_raw_by_key = snap_raw
        except Exception:  # noqa: BLE001
            pass
        return [], [f"文献解析修复未执行（不影响出稿）：{type(e).__name__}: {e}"]
