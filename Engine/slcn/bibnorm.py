"""参考文献（.bib）规范化。

输入可能是：
  1. 作者自带的 BibLaTeX/BibTeX 库（.bib）——需按 SLCN 模板惯例清理
  2. 正文里的 \\bibitem 列表      ——需转成 .bib
  3. Word 文档末尾的人年制参考文献 ——需结构化（最难，PoC 先做中文条目）

清理规则来自对 03 Jie_Zhou 与 10 Lei_Li 两份人工成品的实测比对。

**第 133 条（2026-10-18）：整行 `%` 注释不算条目**。作者 `.bib` 里常见注释掉的模板/
重复条目（`% @article{,`、`% @misc{z16,`）；`split_entries()` 从前全文匹配 `@\\w+\\s*\\{`、
不跳注释，产物 `reference.bib` 会带空键/重复键，biber 语法报错**整份 bib 报废**
（2023(71)04：115 处 undefined citation、参考文献区未生成）。现在先 `strip_bib_comments()`
（只清「行首可选空白 + `%` 到行尾」，行内/值内 `%` 不动）再拆条目。

**⚠️ 改规范化规则（`DROP_FIELDS` / `DROP_FIELD_PREFIXES` / `_clean_field_value` 的值清理）
必须同时迁移引用库**：库记录的 `content_sha256` 由本模块的清理规则推导（见
`slcn/bibdb.py:canonical_fields()`），规则一改，旧库摘要立刻全部 stale、`bibdb.py lint`
会报 `content_sha256 与字段内容不一致`。改动时**必须**：
  1. 对真实库跑 `python3 Engine/bibdb.py rehash --db <库> --apply`（只改
     `content_sha256` + `updated_at`，原子写、可 git 回滚）；
  2. 更新冻结 fixture `Engine/tests/test_bibdb_rehash.py` 里的期望 sha（它存在的目的
     就是让「改了规则忘了迁移」立刻红）。
批45 `6a6ff4e` 改了 `DROP_FIELDS` 却没迁移 10-18 冻结库，6 条 stale-sha 潜伏到批71
才被真实库 lint 发现——本条就是那次事故立的规矩。

**B-189（2026-10-22 用户拍板）：正式发表条目的 url/doi 渲染规范**。规则见
`Engine/docs/RULES.md` §四.10：正式发表条目不印/不写 `url`+`doi`，未出版/在线资源
（online/unpublished/arXiv/SEP 等）保留并打印网址。实现是 `strip_published_urls_fields()`
（字段级）/ `strip_published_urls()`（文本级，Word 通道用）；**这是渲染层政策，
不是库规范化规则**——`normalize_bib()` 默认 `strip_urls=False`（库预览/回读校验的
输出逐字节不变），只有初排产物写出（`slcn/build.py` 两通道）才显式开启。
所以它**不改变** `bibdb.canonical_fields()` / `content_sha256()`，不需要 rehash。
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
        #    早先转成 `11`/`07` 是错的（Jie_Zhou 的 Yalcin2007、Ninan2016 都对不上）。
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
        # 人工做法是只留出版商。实测 Jie_Zhou 的 Dorr2013 / Mandelkern2019 都是这样。
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
#   · 而**编辑的实际成品**是另一回事：`2026年第5期(90)/01 Meimei_Han/reference.bib`
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
        （成品 `10 Lei_Li` 的 `Steinberger2022` 用的是 `url = {…}`）；
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
    于是同一部书走 LaTeX 通道会被搬、走 Word 通道不会——实测 `2026_02_Ning_Jiang`（英文 Word 稿）
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


# ── B-189：正式发表条目的 url/doi 渲染规范（2026-10-22 用户拍板） ────────────
#
# 权威口径 = `Engine/docs/RULES.md` §四.10；本处只实现判据与清洗，不另立口径。
#   · 正式发表（entry_type ∈ PUBLISHED_ENTRY_TYPES 且有出版信息字段）→
#     **不印** DOI/网址，产物 `reference.bib` 里连 `url`/`doi` 字段一起删掉；
#   · 未出版 / 在线资源（online/unpublished/misc/arXiv/SEP 等）→ 保留并打印网址；
#   · 这是**渲染层政策**：不算库规范化（见模块 docstring 末段），默认不启用。

#: 可能「正式发表」的条目类型（`conference` = inproceedings 的别名；博硕论文 = thesis 的别名，
#: 见 `slcn/bibcheck.py: TYPE_ALIASES` —— 两边判据不同源会分叉，所以这里按同一张表列）。
PUBLISHED_ENTRY_TYPES = frozenset({
    "article", "book", "incollection", "inproceedings", "inbook", "proceedings",
    "collection", "manual", "periodical", "thesis", "phdthesis", "mastersthesis",
    "report", "techreport", "booklet", "conference",
})
#: 「有出版信息」的字段（任一个非空即算 —— 与设计卡 §3 写死的口径一致）。
PUBLICATION_FIELDS = ("journal", "journaltitle", "booktitle", "publisher",
                      "institution", "school")
#: 恒保留网址的类型（设计卡写死；`misc` 是 SEP/预印本常用类型）。
ONLINE_RETAIN_ENTRY_TYPES = frozenset({"online", "unpublished", "misc"})

#: 可能夹带网址（不是 `url` 字段）的字段。正式条目全清；在线资源只从
#: `howpublished`/`publisher` 抽出来搬进 `url`（`title`/`note` 不动：那里夹带的
#: 网址本来就是条目正文的一部分，搬走会留下「Retrieved from」尾巴）。
_EMBEDDED_URL_FIELDS = ("publisher", "address", "note", "howpublished", "title",
                        "journal", "journaltitle", "booktitle")
_ONLINE_MOVE_URL_FIELDS = ("howpublished", "publisher")

_URL_IN_TEXT = re.compile(r"https?://[^\s{}]+")
_SEP_ANY = re.compile(r"stanford\s+encyclopedia\s+of\s+philosophy", re.I)
#: 在线参考工具线索（B-264 v2）：词典/百科/在线词书。见 `_is_reference_work_like`。
_REFERENCE_WORK_ANY = re.compile(
    r"\b(?:Dictionaries|Dictionary|Encyclopaedias|Encyclopaedia|"
    r"Encyclopedias|Encyclopedia|Online)\b", re.I)


def _plain_field(value: str) -> str:
    """字段值 → 去保护花括号、压空白的可比较形态（只用于判据，不改字段）。"""
    return re.sub(r"\s+", " ", re.sub(r"[{}]", "", value or "")).strip()


def _is_sep_like(fields: dict[str, str]) -> bool:
    """是不是斯坦福哲学百科（SEP）词条 —— booktitle / journal 任一含 SEP 全名即算。

    为什么不能只复用 `is_sep_entry()`（只看 booktitle）：41 篇实测里有 SEP 词条被
    作者写成 `@article` + `journal = {The Stanford Encyclopedia of Philosophy}`
    （Fang_Liu `hansson:sep`），只认 booktitle 会把它当正式期刊文章把网址删掉。
    `is_sep_entry()` 的既有语义（bibcheck 判据）**不动**，这里只做渲染判据。
    """
    for name in ("booktitle", "journal", "journaltitle"):
        if _SEP_ANY.search(_plain_field(fields.get(name, ""))):
            return True
    return False


def _is_reference_work_like(fields: dict[str, str]) -> bool:
    """是不是「在线参考工具」词条（词典/百科）—— B-264 v2 新增，同 `_is_sep_like` 的窄口径。

    为什么需要（期 87 sub9 实测）：APA 的在线词典引用被写成 `@article` +
    `journal = {Online Etymology Dictionary}` / `{An Anglo-Saxon Dictionary Online}`
    / `{Oxford English Dictionary}`。按第 3 条判据「article + 有刊名 = 正式发表」会把
    网址当 DOI 删掉，可这几条的**出处就是网址**（`Retrieved … from https://…`），
    删掉等于丢数据。发布版 .bib（人工定稿）也保留了这些网址。

    判据只认三个字段（booktitle / journal / journaltitle）里出现
    `Dictionary` / `Encyclopaedia` / `Encyclopedia` / `Online` 词——口径刻意窄：
    普通刊名（`Journal of Logic` 等）不命中，照旧按正式发表处理。
    这是对 B-189 渲染政策的一处**行为扩展**（宁可多留一处网址，也不误删出处），
    已在 B-264 v2 的 impl.md 里按 D-xx 披露；`Engine/docs/RULES.md` §四.10 的
    权威口径未改（本函数只是判据实现）。
    """
    for name in ("booktitle", "journal", "journaltitle"):
        if _REFERENCE_WORK_ANY.search(_plain_field(fields.get(name, ""))):
            return True
    return False


def is_online_resource(etype: str, fields: dict[str, str]) -> bool:
    """该条目是不是「未出版 / 在线资源」（→ 保留并打印网址）。

    判据（设计卡 §3 写死，顺序即优先级）：
      1. `online` / `unpublished` / `misc` → 恒保留；
      2. SEP 词条（任意类型）→ 保留（用户规范点名 SEP 要能看出处）；
      2b. 在线参考工具（词典/百科，B-264 v2）→ 保留（出处就是网址）；
      3. 类型 ∈ PUBLISHED_ENTRY_TYPES 且任一 PUBLICATION_FIELDS 非空 → 正式发表；
      4. 其余（未知类型 / 正式类型但缺出版信息）→ 保留（宁可不删，不误删数据）。
    """
    e = (etype or "").strip().lower()
    if e in ONLINE_RETAIN_ENTRY_TYPES:
        return True
    if _is_sep_like(fields):
        return True
    if _is_reference_work_like(fields):
        return True
    if e in PUBLISHED_ENTRY_TYPES and any(
            str(fields.get(name) or "").strip() for name in PUBLICATION_FIELDS):
        return False
    return True


def _extract_urls_from_value(value: str) -> tuple[str, list[str]]:
    """从字段值里抽出 `http(s)://…`，返回 (清掉网址并收尾的文本, 网址列表)。

    收尾规则（实测形状）：
      · `{John Wiley \\& Sons. https://doi.org/…}` → `John Wiley \\& Sons`；
      · `{Accessed: 2025-10-18 https://…}` → `Accessed: 2025-10-18`；
      · 网址后紧跟的中文/英文句读不吞掉相邻正文（只与网址一起匹配的尾标点才丢）。
    """
    urls: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        urls.append(m.group(0).rstrip(".,;:，。；："))
        return ""

    cleaned = _URL_IN_TEXT.sub(_repl, value)
    if not urls:
        return value, []
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([,;:，；：])", r"\1", cleaned)
    cleaned = re.sub(r"([,;，；])\s*([,;，；])", r"\1", cleaned)
    # 网址前那个句点通常是作者拿来分隔「出版者/注记」与网址的：`Sons. https://…`
    # → 只留 `Sons`（否则印出来是 `Sons., Chichester` 这种双标点）。
    cleaned = re.sub(r"[.。]\s*$", "", cleaned).strip()
    cleaned = re.sub(r"[\s,;:，；：]+$", "", cleaned)
    return cleaned, urls


def _blank_after_url_strip(value: str) -> bool:
    """清掉网址后只剩 LaTeX 空壳（`\\url{}` / `\\href{}{}`）或标点时，字段该整项删除。"""
    v = re.sub(r"\\(?:url|href)\s*\{[^{}]*\}(\s*\{[^{}]*\})?", "", value)
    return not re.sub(r"[\s,;:.。，；：]+", "", v)


#: note 里的网址 token：`\url{…}` / `\href{…}{…}` / 裸 http(s) 地址（B-268 S4）。
_URL_TOKEN_RE = re.compile(
    r"\\url\s*\{\s*([^{}]*?)\s*\}|\\href\s*\{\s*([^{}]*?)\s*\}\s*\{[^{}]*\}",
    re.I)
_BARE_URL_RE = re.compile(r"https?://[^\s{}]+", re.I)


def _url_identity(url: str) -> str:
    """「同一条链接」的归一判据（只用于 note 去重，不参与取舍/回填判据）。

    归一：去 `\\url{}` 包装 → DOI 归一为 `doi:<id>` → arXiv 的 `/abs/` 与 `/pdf/`
    归一为同一 id → 去协议/`www.`/尾斜杠并小写。实测的两类形态：
      · `\\url{https://…/sorites-paradox}` 与 `https://…/sorites-paradox/`（库 Hyde2018）；
      · `https://arxiv.org/pdf/1706.03762.pdf` 与 `http://arxiv.org/abs/1706.03762`（库 Vaswani2017）。
    """
    u = (url or "").strip()
    m = re.match(r"\\(?:url|href)\s*\{\s*([^{}]*?)\s*\}", u, re.I)
    if m:
        u = m.group(1).strip()
    m = re.match(r"(?i)^https?://(?:dx\.)?doi\.org/(.+)$", u)
    if m:
        return "doi:" + m.group(1).strip().lower().rstrip("/")
    m = re.match(r"(?i)^(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf)/([^/?#]+?)(?:\.pdf)?$", u)
    if m:
        return "arxiv:" + m.group(1).strip().lower()
    u = re.sub(r"(?i)^https?://", "", u)
    u = re.sub(r"(?i)^www\.", "", u)
    return u.rstrip("/").lower()


def _drop_duplicate_urls(value: str, url_keys: set[str]) -> tuple[str, list[str]]:
    """把 `value` 里与 url/doi 字段**同一条链接**的网址删掉；其余文本/空白不动。

    只删命中的那个 token（含 `\\url{}`/`\\href{}` 包装），不重排其它内容；
    删完只剩标点/空壳的字段由调用方按 `_blank_after_url_strip()` 整项删除。
    """
    removed: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        raw = m.group(0)
        inner = (m.group(1) or m.group(2) or raw).strip()
        if _url_identity(inner) in url_keys:
            removed.append(inner)
            return ""
        return raw

    cleaned = _URL_TOKEN_RE.sub(_repl, value or "")
    if not removed:
        return value, []
    cleaned = _BARE_URL_RE.sub(_repl, cleaned)
    cleaned = re.sub(r"[（(]\s*[)）]", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"[\s,;:，；：]+$", "", cleaned)
    return cleaned, removed


def strip_published_urls_fields(key: str, etype: str,
                                fields: list[tuple[str, str]],
                                notes: list[str]) -> list[tuple[str, str]]:
    """B-189 字段级清洗：按 `is_online_resource()` 删/留 `url`+`doi`。

    纯函数（不改入参）；逐条改动写进 `notes`（与 `normalize_bib` 的说明同一格式）。
    正式条目还会清掉夹带在 publisher/address/note/… 里的网址；在线资源只把
    `howpublished`/`publisher` 里的网址搬进 `url` 字段（已有 `url` 时去重丢弃）。
    """
    flat: dict[str, str] = {}
    for name, value in fields:
        flat.setdefault(name, value)
    online = is_online_resource(etype, flat)
    has_url = bool((flat.get("url") or "").strip())
    # B-268 S4：note 里与 url/doi 字段同一条链接的网址要在输出层去重（保留 url 字段）。
    url_keys: set[str] = set()
    if has_url:
        url_keys.add(_url_identity(flat.get("url") or ""))
    if (flat.get("doi") or "").strip():
        url_keys.add(_url_identity("https://doi.org/" + flat["doi"].strip()))
    url_keys.discard("")
    embedded: list[tuple[str, str]] = []
    out: list[tuple[str, str]] = []

    for name, value in fields:
        if name in ("url", "doi"):
            if online:
                out.append((name, value))
            elif value.strip():
                notes.append(f"{key}: 正式发表条目不写 {name}（已删除）")
            continue
        if name == "note" and online and url_keys:
            cleaned, dups = _drop_duplicate_urls(value, url_keys)
            if dups:
                notes.append(
                    f"{key}: note 内网址与 url/doi 重复（去重，保留 url 字段）：{dups[0]}")
                value = cleaned
                if _blank_after_url_strip(value):
                    continue
        if name in _EMBEDDED_URL_FIELDS:
            cleaned, urls = _extract_urls_from_value(value)
            if urls:
                if online:
                    if name in _ONLINE_MOVE_URL_FIELDS:
                        embedded.extend((name, u) for u in urls)
                        notes.append(f"{key}: 在线资源 {name} 内嵌网址保留")
                        value = cleaned
                        if _blank_after_url_strip(value):
                            continue
                    # 其它字段（title/note/…）原样保留：网址本就是条目正文的一部分
                else:
                    notes.append(f"{key}: 正式发表条目删除 {name} 内嵌网址 {urls[0]}")
                    value = cleaned
                    if _blank_after_url_strip(value):
                        continue
        out.append((name, value))

    if online and embedded:
        if not has_url:
            field, url = embedded[0]
            out.append(("url", url))
            notes.append(f"{key}: {field} 内嵌网址 → url 字段（{url}）")
        elif len(embedded) >= 1:
            notes.append(f"{key}: 已有 url，删除 {embedded[0][0]} 内嵌网址（去重）")
    return out


def strip_published_urls(text: str) -> tuple[str, list[str]]:
    """B-189 文本级清洗（Word 通道产物用）：逐条判定，**只重写命中条目**。

    为什么要有文本级版本：Word 通道的 `reference.bib` 是
    `docxsource.refs_to_bib()` / `footnotes.entry_to_bib()` 直接渲染的，不经过
    `normalize_bib()`（那是 LaTeX 通道）；不补这一步就会「同一篇稿、换个通道
    DOI 就印出来」（实测 Min_Cai 86-08 的 1 处 doi.org 就是这样漏的）。

    ⚠️ 只对**命中条目**重排字段（未命中的条目与条目间文本逐字节不动）；
    这是给机器生成的 bib 用的，不承担「任意作者 bib 的格式归一」。
    """
    notes: list[str] = []
    out: list[str] = []
    last = 0
    pos = 0
    for m in re.finditer(r"@\w+\s*\{", text):
        start = m.start()
        if start < pos:
            continue
        i = text.index("{", start)
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
        if j >= len(text):
            break                                   # 花括号不配平：剩余文本原样保留
        body = text[i + 1:j]
        k = body.find(",")
        if k < 0:                                   # 无字段（`@misc{key}` 形态）：不动
            pos = j + 1
            continue
        raw_head = text[start:i + 1]
        hm = re.match(r"@(\w+)\s*\{\s*([^,]+?)\s*,", raw_head + body[:k + 1])
        pos = j + 1
        if not hm:
            continue
        etype, key = hm.group(1), hm.group(2).strip()
        try:
            pairs = parse_fields(body[k + 1:])
        except ValueError:
            continue                                # 引号未闭合：交给上游报错，这里不猜
        if not pairs:
            continue
        new_pairs = strip_published_urls_fields(key, etype.lower(), pairs, notes)
        if new_pairs == pairs:
            continue
        out.append(text[last:start])
        last = j + 1
        if not new_pairs:                           # 全删空：保留原条目（宁可多留不乱造）
            out.append(text[start:j + 1])
            notes.append(f"{key}: 删除后无字段，条目原样保留（需人工核对）")
            continue
        width = max((len(n) for n, _ in new_pairs), default=4)
        body_new = ",\n".join(f"  {n.ljust(width)} = {{{v}}}" for n, v in new_pairs)
        out.append(f"@{etype}{{{key},\n{body_new}\n}}")
    out.append(text[last:])
    return "".join(out), notes


def normalize_bib(text: str, *, strip_urls: bool = False) -> tuple[str, list[str]]:
    """规范化一份 .bib，返回 (新文本, 改动说明列表)。

    `strip_urls=True` 时同时执行 B-189 渲染规范（正式发表条目删 url/doi；
    只给初排产物写出用，见模块 docstring 末段）。默认 False = 旧行为逐字节不变。
    """
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

        # B-189：正式发表条目不写 url/doi（渲染层政策；仅在产物写出时启用）。
        if strip_urls:
            new_fields = strip_published_urls_fields(key, etype, new_fields, notes)

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
            # B-262：解析不出的复杂文献 → @misc，源稿原文逐字符进 author（花括号保护），
            # 不再重复放 note；misc 驱动只印 author，正好原样排印，便于人工再订正。
            from .footnotes import _bib_escape_verbatim

            raw = f["note"].strip().replace("\n", " ")
            out.append("@misc{%s,\n  author    = {{%s}}\n}"
                       % (key, _bib_escape_verbatim(raw)))
            notes.append(f"{key}: 解析不出，原文逐字符进 author")
            continue
        for i2, p in enumerate(parsed, 1):
            k = key if len(parsed) == 1 else f"{key}{chr(96 + i2)}"
            vals = {fld: getattr(p, fld) for fld in _BIB_FIELDS if getattr(p, fld)}
            vals["note"] = f["note"].strip().replace("\n", " ")
            # 第 141 条（2026-10-18，期 87 子案例 9 / 10 Jie_Chen）：写回 .bib 前必须
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
#     author={徐强：《…》} / title={期刊名} / title={译者}）；
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


#: 英文编号清单里的**书**形态（86-02 实测）：
#: `Van Emde Boas P, …, Zhao B. Title[M]. Singapore: Springer Nature Singapore Pte Ltd. 2022.`
#: 只认 `[M]` 类型标识 + 行尾年份 + `城市: 出版社` 三种行内证据，不猜其它形态。
_EN_NUMBERED_BOOK_RE = re.compile(
    r"^(?P<author>[^\[\]]{2,240}?)[.。]\s+"
    r"(?P<title>[^\[\]]{2,300}?)\s*\[M\]\.?\s*"
    r"(?P<rest>.+)$", re.S)
_EN_BOOK_TAIL_YEAR_RE = re.compile(r"(?P<year>(?:19|20)\d{2})[.。]?\s*$")

#: 非书的出版信息（期刊卷期/年份混进「出版社」位）→ 不当 plain book，保留原条目。
_NOT_BOOK_PUB_RE = re.compile(r"(?:19|20)\d{2}|第\s*\d+\s*[期卷]")


def _parse_en_numbered_book(s: str) -> dict | None:
    """英文编号书：`作者列表. 题名[M]. 城市: 出版社, 年.`（86-02 实测，行内证据）。"""
    if not s or _has_cjk(s):
        return None
    m = _EN_NUMBERED_BOOK_RE.match(s.strip())
    if not m:
        return None
    rest = m.group("rest").strip()
    ym = _EN_BOOK_TAIL_YEAR_RE.search(rest)
    if not ym:
        return None
    addr, sep, pub = rest[: ym.start()].strip().rstrip(",，;；").partition(":")
    if not sep:
        return None
    addr = addr.strip().strip(",，;；")
    pub = pub.strip()
    if not (2 <= len(addr) <= 60 and 2 <= len(pub) <= 100):
        return None
    # 作者串只按**源顺序**保留、把逗号换成 BibTeX 的 `and`（不拆/不补姓名部件）。
    # 单个 token / 以缩写开头的作者（`J. Smith. …`）几乎都是被首个句点切错 → 不猜。
    author = m.group("author").strip().strip(",，;；")
    if " " not in author or re.match(r"^[A-Za-z]\.", author):
        return None
    author = re.sub(r"\s*[,，]\s*", " and ", author)
    return {
        "_type": "book", "author": author, "title": m.group("title").strip(),
        "year": ym.group("year"), "address": addr, "publisher": pub,
    }


def _parse_cn_plain_book_no_angle(s: str) -> dict | None:
    """中文 plain book（题名无《》）：`作者：书名，年，出版地：出版社`（86-02 实测）。

    只认「作者冒号 + 书名列 + 行内年份 + 显式 `出版地: 出版社`」四件行内证据；
    任何一件缺失（尤其中文期刊行没有 `地: 社`）→ None，保留原条目。
    """
    m = re.match(
        r"^(?P<author>[^：:《，,。]{2,30})\s*[：:]\s*"
        r"(?P<title>[^，,。：《》“”]{2,120})\s*[，,]\s*(?P<rest>.+)$", s)
    if not m:
        return None
    years = re.findall(r"(?:19|20)\d{2}", s)
    if not years:
        return None
    addr, pub = _split_address_publisher(m.group("rest").strip())
    if not addr or not pub or _NOT_BOOK_PUB_RE.search(pub):
        return None
    return {
        "_type": "book", "author": m.group("author").strip(),
        "title": m.group("title").strip(), "year": years[-1],
        "address": addr, "publisher": pub,
    }


def _has_en_numbered_book(raw: str) -> bool:
    """`repair_word_refs` ② 的**路由**判据：该行是否是英文编号书形态（不参与解析）。"""
    m = _NUMBERED_HEAD_RE.match(raw or "")
    return _parse_en_numbered_book(
        (m.group(2) if m else (raw or "")).strip()) is not None


def _parse_cn_numbered_line(raw: str) -> dict | None:
    """解析中文编号条目：`作者：《题名》，《期刊》，年` / `作者：《书名》，译者译，地：社，年`。

    只认**行内证据**（题名来自《》、年份来自行内 4 位数字、期刊/出版社来自显式
    标点段）；① 译著、② 期刊论文、③ 编著章节 三种形态之外一律返回 None（保留原条目）。

    B-107（86-02 实测）在同一判据内补两种 **plain book**：④ 中文 `《题名》，[年，]地：社`
    与 ⑤ 题名不带《》的 `作者：书名，[年，]地：社`；另加 ⑥ 英文编号书 `…[M]. 地: 社, 年`。
    旧三形态判据与顺序一字未动（新分支只在其后/在其不可达处生效）。
    """
    m = _NUMBERED_HEAD_RE.match(raw or "")
    s = (m.group(2) if m else (raw or "")).strip()
    if not _has_cjk(s):
        return _parse_en_numbered_book(s)
    if "《" not in s:
        return _parse_cn_plain_book_no_angle(s)
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
    # ④ 中文 plain book：`《题名》，[年，]出版地：出版社[，年]`（86-02 实测）
    addr, pub = _split_address_publisher(tail)
    if addr and pub and not _NOT_BOOK_PUB_RE.search(pub):
        d["_type"] = "book"
        d["address"] = addr
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


# ── B-001（批70）：脚注条目 ↔ 文末人年制条目的「同作品」判据零件 ─────────────
#
# 为什么不在 docxsource 的并集处做：那里用的是**指纹**（作者+题名+年+类型），
# 脚注条目的题名常被解析器塞进半行出版信息（90 的 `Wolf1987`：题名 =「Sanity and
# the Metaphysics of Responsibility, ” in Responsibility, Character, …」），
# 归一后与文末条目的干净题名不是「相等」而是「包含」→ 指纹不同 → 永不合并。
# 这里改用**题名相似度**（`footnotes._title_match_tier`，包含关系 0.95）+ 同姓 +
# 同年 + **候选唯一** 的保守判据：宁可漏合并（保留 + warning），不做无证据的合并。

_VOL_ED_VOLUME = re.compile(r"\bvol(?:ume)?\.?\s*(\d{1,3})\b", re.I)
_VOL_ED_VOLUME_CN = re.compile(r"第\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*卷")
_VOL_ED_EDITION = re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)\s+ed(?:ition)?\b", re.I)
_VOL_ED_EDITION_CN = re.compile(r"第\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*版")


def _volume_edition_of_text(text: str) -> tuple[str, str]:
    """从原文串里保守抽「卷号 / 版次」（只认明确写法；抽不到 → 空串）。

    只为「卷号/版次冲突就不合并」这条闸门服务：抽不到 = 不知道 = 不算冲突。
    """
    s = str(text or "")
    vol = ""
    m = _VOL_ED_VOLUME.search(s)
    if m:
        vol = m.group(1)
    else:
        m = _VOL_ED_VOLUME_CN.search(s)
        if m:
            from .footnotes import _norm_volume

            vol = _norm_volume(m.group(1))
    edition = ""
    m = _VOL_ED_EDITION.search(s)
    if m:
        edition = m.group(1)
    else:
        m = _VOL_ED_EDITION_CN.search(s)
        if m:
            from .footnotes import _norm_edition

            edition = _norm_edition(m.group(1))
    return vol, edition


def _entry_volume_edition(entry: dict, raw: str) -> tuple[str, str]:
    """脚注 bib 条目（dict）的卷号/版次：先看字段，再从题名/出版信息原文里抽。"""
    from .footnotes import _norm_edition, _norm_volume

    vol = ""
    if str(entry.get("volume") or "").strip():
        vol = _norm_volume(entry.get("volume"))
    edition = ""
    if str(entry.get("edition") or "").strip():
        edition = _norm_edition(entry.get("edition"))
    if vol and edition:
        return vol, edition
    text = " ".join(str(entry.get(k) or "") for k in
                    ("title", "booktitle", "journal", "note")) + " " + str(raw or "")
    vol2, ed2 = _volume_edition_of_text(text)
    return vol or vol2, edition or ed2


def _ref_volume_edition(r) -> tuple[str, str]:
    """文末人年制条目（RefEntry）的卷号/版次：从 raw/title/rest 原文里抽。"""
    text = " ".join(str(getattr(r, f, "") or "") for f in ("raw", "title", "rest"))
    return _volume_edition_of_text(text)


def _year4(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))[:4]


# ── B-001（批70）：⑥ 未链文末条目 warning（公开、可单测；纯日志零产物变化）──────
#
# 必须在**引用生成之后**调用（build 的 `link_citations` / `link_numbered_citations`
# 之后）。人年制 Word 稿在 parse/repair 时点正文引用还是**纯文本**（`（Wolf 1987）`
# 要等 `link_citations` 才变 `\parencite{Wolf1987}`）——在 repair 里提前统计会把整份
# 清单误报成「未引用」（批70 retest 实测：86-07/88-01/88-06 分别误报 25/37/39 条）。
# 文末键集合 = 人年制 `refs` + 编号清单 `ref_key_by_num`（**不含**脚注生成的
# `bib_entries` 键：脚注条目本来就不必出现在正文引用里）。
# 条件（spec §2.4）：文末有条目 + 正文已有引用命令 + 存在未引条目 → 才提示；
# 正文 0 引用沿用 build 的既有提示（不重复告警）；`\nocite{*}` 打印全部条目 → 不提示。
def end_list_uncited_warning(doc, body: str) -> str | None:
    r"""⑥ 未链文末条目 warning 文本；不该提示时返回 `None`（调用方只写日志/warning）。

    `body` 是**引用已经替换完成**的正文 LaTeX（build 里那个 `body` 变量）。纯读，
    不改 doc、不写文件；内部异常一律回落 `None`（提示失败不得影响出稿）。
    """
    try:
        refs = list(getattr(doc, "refs", []) or [])
        num2key = dict(getattr(doc, "ref_key_by_num", {}) or {})
        entries = list(getattr(doc, "bib_entries", []) or [])
        body = str(body or "")
        end_keys: list[str] = []
        for r in refs:
            k = str(getattr(r, "key", "") or "")
            if k and k not in end_keys:
                end_keys.append(k)
        for k in num2key.values():
            k = str(k or "")
            if k and k not in end_keys:
                end_keys.append(k)
        if not end_keys:
            return None
        if re.search(r"\\nocite\s*\{\s*\*\s*\}", body):
            return None      # \nocite{*} → biblatex 打印全部条目，不存在「不上版」
        from .bibcheck import cited_keys

        cited = cited_keys(body)
        uncited = [k for k in end_keys if k not in cited]
        if not cited or not uncited:
            return None
        title_by_key: dict[str, str] = {}
        for r in refs:
            _k = str(getattr(r, "key", "") or "")
            _t = str(getattr(r, "title", "") or "").strip()
            if _k and _t:
                title_by_key.setdefault(_k, _t)
        for k, e, _raw in entries:
            _t = str((e or {}).get("title") or "").strip()
            if k and _t:
                title_by_key.setdefault(str(k), _t)
        shown = []
        for k in uncited[:8]:
            t = title_by_key.get(k, "")
            shown.append(f"{k}（{t[:40]}）" if t else k)
        return (f"文末有 {len(uncited)} 条未被正文链路引用（biblatex 不会打印，"
                "PDF 参考文献区会少）："
                + "、".join(shown) + ("…" if len(uncited) > 8 else ""))
    except Exception:  # noqa: BLE001  纯提示，失败回落 None
        return None


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

    # ── ② 编号条目字段归位（90-07 / 86-02 型）───────────────────────────
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
            # B-107：英文编号书（86-02 Boas2022）旧判据全部不命中，但同一行内证据
            # 足以归位；这里只**多路由**这一种形态（判据仍在 `_parse_*` 单点）。
            or _has_en_numbered_book(full)
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

    # ── ⑤ 同作品脚注条目 ↔ 文末人年制条目去重（B-001，期90 实测）──────────
    # 现象：脚注引注 `Wolf1987`（author「Cf. Susan Wolf」）与文末人年制条目
    # `Susan1987` 是**同一作品**，但题名一个带出版信息、一个是干净题名 → 指纹
    # 不同 → 两条都印进 reference.bib（11 条 vs 人工 10 条），正文只引前者。
    # 判据（全部满足才合并，不猜）：脚注条目键不在文末清单键集合内 + 作者姓归一
    # 相同（「Cf.」已由 `_author_surname` 剥离）+ 年份相等 + 题名相似度 ≥ 0.90
    # + 卷号/版次无冲突 + **候选唯一**。命中 → 删 bib 条目、正文键改写为文末键、
    # 文末字段优先；多候选/不足阈值/卷版冲突 → 保留原条目 + warning（人工核对）。
    end_list_keys = {getattr(r, "key", "") for r in refs if getattr(r, "key", "")}
    end_list_keys |= numbered_keys
    if refs and entries:
        from .footnotes import (
            _author_surname, _norm_fp, _title_match_tier,
        )

        ref_targets: list[dict] = []
        for r in refs:
            _rk = getattr(r, "key", "")
            if not _rk:
                continue
            _rvol, _red = _ref_volume_edition(r)
            ref_targets.append({
                "key": _rk,
                "surname": _author_surname(getattr(r, "author", "") or ""),
                "year": _year4(getattr(r, "year", "")),
                "title": str(getattr(r, "title", "") or ""),
                "vol": _rvol,
                "ed": _red,
            })
        kept_same: list[tuple[str, dict, str]] = []
        keymap_same: dict[str, str] = {}
        for key, ent, raw in entries:
            e_author = str(ent.get("author") or ent.get("editor") or "").strip()
            e_title = str(ent.get("title") or "").strip()
            e_year = _year4(ent.get("year"))
            e_surname = _author_surname(e_author) if e_author else ""
            if (key in end_list_keys or not e_surname or not e_year
                    or len(_norm_fp(e_title)) < 4):
                # 文末编号清单条目不在本条范围；身份证据不足（缺作者/年/题名太短）
                # 的不参与匹配，也不告警（空题名碎片由 ④ 负责、自有 warning）。
                kept_same.append((key, ent, raw))
                continue
            candidates = [t for t in ref_targets
                          if t["surname"] == e_surname and t["year"] == e_year]
            if not candidates:
                kept_same.append((key, ent, raw))
                continue
            if len(candidates) > 1:
                warnings.append(
                    f"脚注条目 {key} 与文末条目有多条同姓同年候选（"
                    + "、".join(t["key"] for t in candidates[:6])
                    + "），疑似同作品重复但无法唯一确定，已保留原条目（不猜），请人工核对")
                kept_same.append((key, ent, raw))
                continue
            target = candidates[0]
            tier = _title_match_tier(e_title, target["title"])
            e_vol, e_ed = _entry_volume_edition(ent, raw)
            vol_conflict = bool(e_vol and target["vol"] and e_vol != target["vol"])
            ed_conflict = bool(e_ed and target["ed"] and e_ed != target["ed"])
            if vol_conflict or ed_conflict:
                warnings.append(
                    f"脚注条目 {key} 与文末条目 {target['key']} 同姓同年、题名相似度 "
                    f"{tier:.2f}，但卷号/版次冲突（脚注 "
                    f"{'卷 ' + e_vol if vol_conflict else '版次 ' + e_ed} vs 文末 "
                    f"{'卷 ' + target['vol'] if vol_conflict else '版次 ' + target['ed']}"
                    "），疑似同作品但证据冲突，已保留原条目（不猜），请人工核对")
                kept_same.append((key, ent, raw))
                continue
            if tier < 0.9:
                warnings.append(
                    f"脚注条目 {key} 与文末条目 {target['key']} 同姓同年，但题名相似度 "
                    f"{tier:.2f} < 0.90，疑似同作品重复但不足阈值，已保留原条目"
                    "（不猜），请人工核对")
                kept_same.append((key, ent, raw))
                continue
            # 合并：删脚注条目、正文键改写为文末键、文末字段优先（surviving 条目
            # 就是文末条目本身，其字段一字不改；脚注条目不再进 bib）。
            keymap_same[key] = target["key"]
            raw_by_key.pop(key, None)
            notes.append(
                f"{key}: 脚注条目与文末条目同作品 → 并入 {target['key']}"
                f"（《{target['title'][:40]}》；文末字段优先，正文引用键同步改写）")
        if keymap_same:
            body = _rewrite_body_cites(body, keymap_same)
            entries = kept_same
            notes.append(
                "脚注↔文末同作品合并："
                + "、".join(f"{o}→{n}" for o, n in keymap_same.items())
                + "（正文引用键改写为文末键）")

    # ⑥ 未链文末条目 warning 不在这里做：repair 跑在 parse_docx 之后、build 的引用
    # 替换之前，此时人年制正文引用还是纯文本 → 提前统计会把整份清单误报成「未引用」。
    # 由 build 在引用生成后调用 `end_list_uncited_warning(doc, body)`（纯日志）。

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
    r"""Word 文献区解析结果修复（批68 B-021 + 批70 B-001；保守、最小侵入）。

    批68 B-021 四条：① 同段多文献拆分 ② 中文编号条目字段归位 ③ 译著拆条归位
    ④ 空题名脚注碎片合并（86-10 / 90-06 / 90-07 三条真实缺陷）。
    批70 B-001 一条：⑤ 同作品脚注条目 ↔ 文末人年制条目去重（期90：`Wolf1987` /
    `Susan1987` 是一篇文献的两个键；命中 → 删 bib 条目 + 正文键改写为文末键，判据
    见 ⑤ 注释）。⑥ 未链文末条目 warning 不在本函数内：它必须等 build 的引用替换
    之后才能统计（否则人年制正文引用还是纯文本 → 整份清单误报），由 build 调用
    公开函数 `end_list_uncited_warning(doc, body)`（纯日志）。

    参数 `doc` 是 `docxsource.parse_docx()` 的返回值（只用到 `refs` / `bib_entries`
    / `body` / `ref_raw_by_key` / `ref_key_by_num`）。**原地修改**上述属性，
    返回 `(改动说明, 告警)` 两组字符串给调用方写日志/warning。

    纪律：
      · 默认零变化——各条判据都不命中的文档，属性一个字节都不动；
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
