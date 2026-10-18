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
DROP_FIELDS = {"urldate", "file", "abstract", "keywords", "timestamp", "owner", "groups"}

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _clean_field_value(name: str, value: str) -> str | None:
    """清理单个字段值，返回 None 表示应删除该字段。"""
    v = value.strip()
    if name in DROP_FIELDS:
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


def split_entries(text: str) -> list[tuple[str, str, str]]:
    """把 bib 拆成 [(条目头, 字段体, 尾), ...]，非条目文本不随条目返回。

    第 133 条：先剥掉**整行 `%` 注释**（`strip_bib_comments()`），注释掉的旧条目不会被
    当成真条目；返回值结构与顺序不变（正常 bib 的结果与从前逐字节相同）。
    """
    text = strip_bib_comments(text)
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
    for m in re.finditer(r"(\w+)\s*=\s*", fields):
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
            j = fields.index('"', i + 1)
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
