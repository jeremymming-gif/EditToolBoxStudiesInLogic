r"""脚注处理：分类、拆分、页码定位、生成 bib 条目。

背景（实测 Lu_Jiang，90 条脚注）
--------------------------------
作者用**脚注式引用**，把参考文献全写在脚注里。逐条核对人工成品后得到四类处理：

  ① 纯文献引用（65 条）
     例：`Catarina Dutilh Novaes and Stephen Read, "Introduction", in The Cambridge
          Companion to Medieval Logic, ... p. 14.`
     人工：**从脚注提升到正文**，变成 `(\parencite[14]{c05})`

  ② 混合：文献 + 注释（4 条）
     例（用户指出的脚注 6）：
         `James W. Garson, Modal Logic for Philosophers, Cambridge: Cambridge
          University Press, 2nd ed., 2013, pp. 57-58, pp. 63-64.
          The formal definition of the modality □ is on page 64.`
     人工：`\\footnote{cf. \\parencite[63--64]{c04}. The formal definition of the
            modality $\\Box$ is on page 64. }`
     ── **文献部分剥离成 \\parencite，注释部分原样保留，合并在同一个 \\footnote 内**

  ③ 纯注释（21 条）
     例：`Here we are talking about propositonal modal logic, ...`
     人工：原样 `\\footnote{...}`

  ④ 古典文献（Ockham 等一手文献）
     例：`Ockham, Exp. in libr. Perih. II, 5, OP II, 465.`
     人工：**原样保留在脚注里**，不进 bib（`\\footnote{Ockham, \\textit{Exp. in
            libr. Perih.} II, 5, \\textit{OP} II, 465.}`）
     判据：没有 4 位年份、没有出版社 —— 不是现代出版物条目。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── 文献成分识别 ──────────────────────────────────────────────────────────

# 出现这些说明是**现代出版物条目**（可以进 bib）
BIB_MARKERS = [
    (r"\b(?:19|20)\d{2}\b", "年份"),
    (r"\bpp?\.\s*\d+", "页码缩写"),
    (r"\b(?:Press|Publishing|Verlag|Routledge|Blackwell|Wiley|Springer)\b", "出版社"),
    (r"出版社|印书馆|译文出版社|人民出版社|大学出版社", "中文出版社"),
    (r"\b(?:Vol|vol|no|No)\.\s*\d+", "卷期"),
    (r"\d+\s*\(\d+\)", "卷(期)"),
    (r"\[[MJC]\]", "文献类型标识"),
]

_SENT_END = re.compile(r"[.。]\s+|$")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


@dataclass
class Footnote:
    fid: int
    text: str
    kind: str = ""  # 'pure_ref' | 'mixed' | 'pure_note' | 'classical'
    ref: str = ""  # 文献部分
    note: str = ""  # 注释部分
    locator: str = ""  # \parencite[...] 里的页码定位
    # 第 121 条：英文纯注释的「已转义 + 引用已替换」版本（空 = 渲染时按 text 转义）
    note_latex: str = ""
    key: str = ""  # bib 键（生成后回填）
    entry: dict = field(default_factory=dict)  # 解析出的 bib 字段


def ref_span(text: str) -> int:
    """找出「文献部分」的结束位置；-1 表示整体不像文献条目。"""
    last = -1
    for pat, _ in BIB_MARKERS:
        for m in re.finditer(pat, text):
            if m.end() > last:
                last = m.end()
    if last < 0:
        return -1
    m = _SENT_END.search(text, last)
    return m.end() if m else len(text)


def looks_classical(ref: str) -> bool:
    """判断是不是古典/一手文献（Ockham, SL II, 1, OP I, 242.）。

    这类引用人工是**原样留在脚注里**的，不进参考文献表。
    """
    if _YEAR.search(ref):
        return False
    if any(re.search(p, ref) for p, _ in BIB_MARKERS if "出版社" in _ or "Press" in _):
        return False
    # 含罗马数字卷次 + 短缩写（SL / OP / Exp.）是典型的古典文献引用
    roman = re.search(r"\b[IVX]+\b\s*[,.]", ref)
    abbrev = re.search(r"\b(?:SL|OP|Exp|Summa|Sent|Quodl|Ord)\b", ref)
    return bool(roman or abbrev)


def classify(text: str) -> Footnote:
    """给一条脚注分类并拆分。"""
    t = re.sub(r"\s+", " ", text).strip()
    fn = Footnote(fid=-1, text=t)
    end = ref_span(t)
    if end < 0:
        fn.kind = "pure_note"
        fn.note = t
        return fn
    head, tail = t[:end].strip(), t[end:].strip()
    if looks_classical(head):
        fn.kind = "classical"
        fn.note = t
        return fn
    fn.ref = head
    fn.note = tail
    fn.kind = "mixed" if tail else "pure_ref"
    fn.locator = extract_locator(head)
    return fn


# ── 页码定位 ──────────────────────────────────────────────────────────────

# `pp. 57-58, pp. 63-64` → 人工取了**最后一个**区间（63--64），因为那才是被引的具体页
_PAGE_RANGE = re.compile(r"\bpp?\.\s*(\d+)\s*[-–—]\s*(\d+)")
_PAGE_SINGLE = re.compile(r"\bpp?\.\s*(\d+)(?!\s*[-–—])")
_PAGE_CN = re.compile(r"[:：]\s*(\d+)\s*[-–—]\s*(\d+)")


def extract_locator(ref: str) -> str:
    """从文献条目里取 \\parencite[...] 的页码定位。

    人工惯例：多个页码区间时取**最后一个**（对应正文实际引用的那几页）。
    """
    ranges = _PAGE_RANGE.findall(ref)
    if ranges:
        a, b = ranges[-1]
        return f"{a}--{b}"
    singles = _PAGE_SINGLE.findall(ref)
    if singles:
        return singles[-1]
    cn = _PAGE_CN.findall(ref)
    if cn:
        a, b = cn[-1]
        return f"{a}--{b}"
    return ""


# ── 文献条目 → bib 字段 ───────────────────────────────────────────────────

# 期刊文章：Author, "Title", Journal vol (year), pp. X-Y.
_ART_EN = re.compile(
    r"^(?P<author>.+?),?\s*[“\"](?P<title>[^”\"]+)[”\"]\s*,?\s*"
    r"(?:in\s+)?(?P<journal>[^,]+?)\s*(?:(?P<vol>\d+)\s*)?\((?P<year>(?:19|20)\d{2})\)"
    r"\s*,?\s*(?P<pages>pp?\.\s*[\d\-–—]+)?",
)
# 书：Author, Author2, Title, Place: Publisher, year.
# ⚠️ 作者可能有多个且用逗号分隔（`Nino B. Cocchiarella, Max A. Freund, Modal Logic...`），
#    单纯按第一个逗号切会把第二作者当成书名——实测就是这么错的。
#    这里先切出「连续的姓名段」，遇到明显像书名的成分才停。
_NAME_SEG = re.compile(r"^[A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*)*$")


def _split_authors(s: str) -> tuple[str, str]:
    """把 `作者1, 作者2, 书名, 出版地: 出版社, 年.` 切成 (作者串, 其余)。"""
    parts = [p.strip() for p in s.split(",")]
    authors = []
    for i, p in enumerate(parts):
        if not p:
            continue
        # 姓名段：全部由 2~4 个首字母大写的词组成，且不含冒号/句点结尾的句子特征
        words = p.split()
        # 关键判据：人名的每个词都首字母大写；书名里通常夹着小写虚词
        # （`Modal Logic for Philosophers` 的 for、`An Introduction to Its Syntax`
        # 的 to）。少了这一条，书名会被误判成第二作者。
        looks_name = (
            1 <= len(words) <= 4
            and all(w[:1].isupper() for w in words)
            and not p.endswith(".")
            and ":" not in p
        )
        if looks_name and len(p) <= 42 and i < 6:
            authors.append(p)
        else:
            return ", ".join(authors), ", ".join(parts[i:])
    return ", ".join(authors), ""


# 在「书名, 出版地: 出版社, [版次,] 年」里**搜索**出版信息段；
# 它前面的一切就是书名。用 search 而不是 match——书名在出版信息之前，
# 锚定开头会直接匹配失败（实测三条都掉进了 misc）。
_BOOK_EN = re.compile(
    r"(?P<place>[^:,]{2,40}?):\s*(?P<publisher>[^,]{2,60}?),\s*"
    r"(?:(?P<edition>\d+(?:st|nd|rd|th)\s+ed\.?,?\s*))?"
    r"(?P<year>(?:19|20)\d{2})",
)
# 中文期刊：作者. 年份. 题名[J]. 期刊, 年(期): 页.
_ART_CN = re.compile(
    r"^(?P<author>.+?)[.。]\s*(?P<year>(?:19|20)\d{2})[a-z]?[.。]\s*"
    r"(?P<title>.+?)\[(?P<kind>[MJC])\][.。]\s*(?P<rest>.+)$",
)


def parse_entry(ref: str) -> dict:
    """把一条文献解析成 bib 字段。解析不出来就整条塞进 note，绝不丢内容。"""
    s = re.sub(r"\s+", " ", ref).strip().rstrip(".")

    m = _ART_CN.match(s)
    if m:
        d = {
            "author": m.group("author"),
            "title": m.group("title"),
            "year": m.group("year"),
        }
        kind = {"M": "book", "J": "article", "C": "incollection"}.get(m.group("kind"), "book")
        rest = m.group("rest")
        jm = re.match(r"\s*([^,，]+)[,，]\s*\d{4}\s*\((?P<n>[^)]*)\)\s*[:：]\s*(?P<p>[\d\-–—]+)", rest)
        if jm and kind == "article":
            d["journal"] = jm.group(1).strip()
            d["number"] = jm.group("n")
            d["pages"] = jm.group("p").replace("-", "--")
        else:
            d["note"] = rest
        d["_type"] = kind
        return d

    m = _ART_EN.match(s)
    if m and m.group("journal"):
        return {
            "_type": "article",
            "author": m.group("author").strip(),
            "title": m.group("title").strip(),
            "journal": m.group("journal").strip(),
            "year": m.group("year"),
            "pages": (m.group("pages") or "").replace("pp.", "").replace("p.", "").strip().replace("-", "--"),
        }

    # 先切作者，再在**剩余部分**上匹配书名与出版信息。
    # 若反过来（让 _BOOK_EN 的 .+? 自己找断点），多作者条目会把第二作者
    # 当成书名的一部分——实测 `Nino B. Cocchiarella, Max A. Freund, Modal Logic...`
    # 的 address 被填成了 `Max A. Freund, Modal Logic. An Introduction...`。
    authors, remainder = _split_authors(s)
    if authors and remainder:
        m = _BOOK_EN.search(remainder)
        if m:
            title = remainder[: m.start()].strip().rstrip(",").strip()
            return {
                "_type": "book",
                "author": authors,
                "title": title,
                "address": m.group("place").strip(),
                "publisher": m.group("publisher").strip(),
                "year": m.group("year"),
                "edition": (m.group("edition") or "").strip().rstrip(","),
            }

    # 解析不出来：保留原文，人工/AI 再订正
    return {"_type": "misc", "note": s}


# ── 生成键 ────────────────────────────────────────────────────────────────

_CN_SURNAME = re.compile(r"^([\u4e00-\u9fff])")


def make_key(entry: dict, seq: int, used: set[str]) -> str:
    """生成 bib 键。

    本刊两种惯例并存（`reference.bib` 83 篇 vs 与作者同名 60 篇），键的**具体形式
    并不重要**——因为工具同时产出 .tex 和 .bib，只要两边自洽即可。
    这里用「首作者姓 + 年份」，重复则追加 a/b/c。
    """
    author = entry.get("author", "")
    year = entry.get("year", "")
    stem = ""
    if author:
        # 先切出第一作者。注意 "Cocchiarella, Nino B." 是「姓, 名」，
        # 而 "Nino B. Cocchiarella" 是「名 姓」——搞反了就会得到 Nino2008 这种键。
        first = re.split(r"\s+and\s+|&", author.strip())[0].strip()
        m = _CN_SURNAME.match(first)
        if m:
            from .naming import surname_pinyin

            stem = surname_pinyin(m.group(1)).capitalize()
        elif "," in first:
            stem = re.sub(r"[^A-Za-z]", "", first.split(",", 1)[0])[:14]
        else:
            words = [w for w in re.split(r"\s+", first) if w]
            # 跳过缩写（Nino B. → 取最后一个完整词）
            words = [w for w in words if not re.fullmatch(r"[A-Za-z]\.?", w)] or words
            stem = re.sub(r"[^A-Za-z]", "", words[-1])[:14] if words else ""
    if not stem:
        stem = "ref"
    stem = stem.strip()
    base = f"{stem}{year}" if year else f"{stem}{seq:02d}"
    key = base
    n = 0
    while key in used:
        n += 1
        key = f"{base}{chr(ord('a') + n - 1)}" if n <= 26 else f"{base}{n}"
    used.add(key)
    return key


# ── bib 字段 → .bib 文本 ──────────────────────────────────────────────────

_TYPE_ORDER = {
    "book": ["author", "title", "edition", "publisher", "address", "year", "userf", "note"],
    "article": ["author", "title", "journal", "volume", "number", "year", "pages", "userf", "note"],
    "incollection": ["author", "title", "booktitle", "publisher", "address", "year", "pages", "userf", "note"],
    "misc": ["author", "title", "year", "userf", "note"],
}


# ── 作者串 → BibTeX 的 `and` 分隔 ────────────────────────────────────────
#
# BibTeX 里**逗号是「姓, 名」的倒装符**：`author = {J. P. Dillard, C. Segrin and J. M. Harden}`
# 会被读成两个人，第一个人的姓变成 `C. Segrin J. P.`，编译出来是
# 「C. S. J. P. Dillard and J. M. Harden」（编辑实测 2026-10-14）。
# 源稿里这种「逗号 + and」混用很常见，必须规范化。
#
# ⚠️ 但**真正的倒装名不能动**：人工 bib 里倒装式占 13%（1573/12577），
#    如 `Folina, J.`、`Cocchiarella, Nino B.`、`van Eemeren, F. H.`。
_AUTHOR_INITIAL = re.compile(r"^[A-Za-z]\.?$")
_AUTHOR_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "phd", "dr", "prof"}


def _author_has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def _author_strip_note(s: str) -> str:
    """剥掉作者名后的方括号/圆括号注记（实测形如 `Li, Jinxia [李晋霞]`），只用于判断。"""
    return re.sub(r"\s*[\[（(][^\]）)]*[\]）)]\s*", " ", s).strip()


def _author_given_like(part: str) -> bool:
    """逗号右边这一截像不像「名」（倒装名的后半段）。

    要认得出这些形态（都是实测踩到的）：
      `J. P.`、`Nino B.`、`J.-Y.`（连字符缩写）、`B.~F.`（波浪号）、`III`（后缀）
    """
    # `~` 是不换行空格（手册要求写 `van~Fraassen`），分析时当空格看，输出仍保留
    toks = [t for t in _author_strip_note(part).replace("~", " ").split() if t]
    if not toks or len(toks) > 4:
        return False
    for t in toks:
        if t.lower().rstrip(".") in _AUTHOR_SUFFIX:
            continue
        if _AUTHOR_INITIAL.match(t):
            continue
        # 缩写可带连字符与句点：J.-Y. / H.-G.；或就是一个首字母大写的词：Nino
        if t[:1].isupper() and all(c.isalpha() or c in ".-" for c in t):
            continue
        return False
    return True


def _author_is_inverted(group: str) -> bool:
    """`Dillard, J. P.` / `Cocchiarella, Nino B.` / `van Eemeren, F. H.` → 倒装，保持原样。

    而 `J. P. Dillard, C. Segrin` **不是**倒装——逗号两边各是一位作者的全名，
    左边带缩写就是判据。
    """
    if "," not in group and "，" not in group:
        return False
    if _author_has_cjk(_author_strip_note(group)):
        return False  # 中文不用倒装式（注记里的汉字不算，如 `Li, Jinxia [李晋霞]`）
    left, right = re.split(r"[,，]", _author_strip_note(group), maxsplit=1)
    left, right = left.strip(), right.strip()
    if not left or not right:
        return False
    if any(_AUTHOR_INITIAL.match(t) for t in left.split()):
        return False
    return _author_given_like(right)


def _split_names(g: str) -> list[str]:
    """按 `,` `，` `、` 拆作者，但**不碰方括号/圆括号里的注记**。

    实测 `Sixu Li [韩笑、李思旭]`：括号里的顿号不是作者分隔符，
    直接 re.split 会把名字拆成 `Sixu Li [韩笑` 和 `李思旭]`。
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in g:
        if ch in "[（(":
            depth += 1
        elif ch in "]）)":
            depth = max(0, depth - 1)
        if ch in ",，、" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [x.strip() for x in parts if x.strip()]


def normalize_authors(a: str) -> str:
    r"""把作者（或编者）串规范成 BibTeX 的 `and` 分隔。

    · `A, B and C` → `A and B and C`
    · `A，B`（中文）→ `A and B`
    · 倒装名保持：`Dillard, J. P.`、`Cocchiarella, Nino B.`、`van Eemeren, F. H.`
    """
    s = re.sub(r"\s+", " ", (a or "").replace("\\&", " & ").replace("＆", " & ").strip())
    if not s:
        return ""
    groups = [g.strip() for g in re.split(r"\s+and\s+|\s*&\s*", s, flags=re.I) if g.strip()]
    out: list[str] = []
    for g in groups:
        if _author_is_inverted(g):
            out.append(g)
            continue
        # ⚠️ 带花括号/LaTeX 命令的**一律不拆**：实测 `Melli{\`{e}}s, Paul{-}Andr{\'{e}}`
        #    被拆成 `Melli{\`{e}}s and Paul{-}Andr{\'{e}}`，姓就丢了。
        #    宁可漏改（留着逗号），也不能改坏数据。
        if "{" in g or "\\" in g:
            out.append(g)
            continue
        # ⚠️ 中文著/译体例（2026-10-15，第 64 条）：`（著）`/`（译）`/`（点校）`/`（编）`
        #    是**姓名的一部分**，段里的中文逗号与顿号**不是**作者分隔符。
        #    实测最刺眼的一例：`阿斯海姆（著），张建军、万林（译）` 会被拆成
        #    `阿斯海姆（著） and 张建军 and 万林（译）`——著/译标记就此毁掉，
        #    而 `{沃斯、费布鲁赫、库伊（著），黄海婷（译）}` 正是规范里写明的写法
        #    （《排校手册》源码的实例是 `焦循（著），沈文倬（点校）`）。
        #    判据复用 `bibcheck.author_has_role_mark()`（与离线检查器同源）。
        from .bibcheck import author_has_role_mark

        if author_has_role_mark(g):
            out.append(g)
            continue
        out.extend(_split_names(g))
    return " and ".join(out) if out else s


def entry_to_bib(key: str, entry: dict, raw: str = "") -> str:
    """把解析出的字段写成 .bib 条目。"""
    etype = entry.get("_type", "misc")
    order = _TYPE_ORDER.get(etype, _TYPE_ORDER["misc"])
    # 中文条目补 userf={cn}（SLCN-MIX.sty 据此改用中文标点）
    if not entry.get("userf"):
        from .bibnorm import detect_userf

        uf = detect_userf(str(entry.get("author", "")), str(entry.get("title", "")))
        if uf:
            entry = dict(entry, userf=uf)
    lines = [f"@{etype}{{{key},"]
    width = max((len(k) for k in order if entry.get(k)), default=4)
    wrote = False
    for k in order:
        v = entry.get(k)
        if not v:
            continue
        if k in ("author", "editor"):
            v = normalize_authors(str(v))
        lines.append(f"  {k.ljust(width)} = {{{_bib_escape(str(v))}}},")
        wrote = True
    if not wrote and raw:
        lines.append(f"  note      = {{{_bib_escape(raw)}}},")
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("}")
    return "\n".join(lines) + "\n"


def _bib_escape(s: str) -> str:
    """转义 bib 值里的 LaTeX 特殊字符，保留已有的转义。"""
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            out.append(s[i : i + 2])
            i += 2
            continue
        if ch in "&%$#_":
            out.append("\\" + ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)
