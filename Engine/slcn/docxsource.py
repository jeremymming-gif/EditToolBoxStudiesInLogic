"""Word 文稿（.docx）解析。

实测 10 Bo_Chen 的原稿：《陈波 当代逻辑哲学鸟瞰.docx》104 个非空段落，
**全部是 Normal 样式**，0 表格 0 图片，14 处 OMML 公式。所以章节层级不能靠
样式判断，只能靠「一、」「（一）」这种编号模式。

正文引用是人年制 `（Haack 1974）`、`（Martin 2025，31-44）`、`（参见陈波 2022）`。

关于引用键的一个重要认识
------------------------
本刊惯例其实**自相矛盾**：中文作者取拼音（陈波→Chen、刘靖贤→LiuJingxian），
译名作者却取回英文原名（罗素→Russell、克里普克→Kripke），而哈瑞又用了拼音
（→HaRui，而非 Harre）。硬猜「正确的键」命中率只有 48%。

但工具**同时产出 .tex 和 .bib**，所以键只需**内部自洽**——正文替换与 bib 生成
共用同一个键生成器即可。这把最难的一步变成了最简单的一步。
"""

from __future__ import annotations

import os
import re
import zlib
from dataclasses import dataclass, field

# 零宽 / BOM 字符，与 `rules.py: _ZERO_WIDTH` **同一组**（案例 74 与案例 75 共用一份）。
# ⚠️ 它们**不可见**：`'\ufeffS. Blackburn'` 在终端里与 `'S. Blackburn'` 长得一模一样，
#    用肉眼查稿永远查不出来，只能靠代码剥。
#    实测：全库 101 篇正文 docx 里 U+FEFF 出现 126 次（14 篇）、U+200B 102 次（51 篇），
#    U+200C / U+200D 出现 0 次（所以把这一组一起剥在本库是零风险的）。
_ZERO_WIDTH = "\ufeff\u200b\u200c\u200d"


def _strip_zero_width(s: str) -> str:
    """剥掉 BOM / 零宽字符（U+FEFF、U+200B、U+200C、U+200D）。"""
    for ch in _ZERO_WIDTH:
        if ch in s:
            s = s.replace(ch, "")
    return s


# 常见中文姓氏 → 拼音（PoC 阶段先覆盖高频项；正式版应引入 pypinyin）
SURNAME_PINYIN = {
    "陈": "Chen", "刘": "Liu", "王": "Wang", "李": "Li", "张": "Zhang", "杨": "Yang",
    "黄": "Huang", "赵": "Zhao", "吴": "Wu", "周": "Zhou", "徐": "Xu", "孙": "Sun",
    "马": "Ma", "朱": "Zhu", "胡": "Hu", "郭": "Guo", "何": "He", "高": "Gao",
    "林": "Lin", "罗": "Luo", "郑": "Zheng", "梁": "Liang", "谢": "Xie", "宋": "Song",
    "唐": "Tang", "许": "Xu", "韩": "Han", "冯": "Feng", "邓": "Deng", "曹": "Cao",
    "彭": "Peng", "曾": "Zeng", "肖": "Xiao", "田": "Tian", "董": "Dong", "袁": "Yuan",
    "潘": "Pan", "蒋": "Jiang", "蔡": "Cai", "余": "Yu", "杜": "Du", "叶": "Ye",
}

# 译名 → 英文原名（编辑部实际用法，哈瑞保留拼音是原样照搬，故不列入）
TRANSLATED_NAMES = {
    "罗素": "Russell", "克里普克": "Kripke", "塔斯基": "Tarski", "蒯因": "Quine",
    "维特根斯坦": "Wittgenstein", "弗雷格": "Frege", "康德": "Kant", "休谟": "Hume",
    "莱布尼茨": "Leibniz", "艾耶尔": "Ayer", "威廉姆森": "Williamson", "麦蒂": "Maddy",
    "普利斯特": "Priest", "哈克": "Haack", "马丁": "Martin", "谢尔": "Sher",
}

# 参考文献类型标识 → BibLaTeX 条目类型
TYPE_MAP = {"M": "book", "J": "article", "C": "incollection", "P": "inproceedings",
            "D": "phdthesis", "N": "newspaper", "R": "report"}

PUNCT_TO_STRIP = " \t\u3000.,，。;；:：、"

# LaTeX 特殊字符。Word 文稿里出现 `&`（如 "Zalta, E N. & Nodelman, U"）、
# `%`（"50%"）、`_`、`#`、`$` 时必须转义，否则编译会直接报
# "Misplaced alignment tab character &" 之类。实测 10 Bo_Chen 就栽在
# 参考文献 note 字段的一个 & 上。
_LATEX_ESCAPES = [
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
]
_ESCAPE_RE = re.compile("|".join(re.escape(ch) for ch, _ in _LATEX_ESCAPES))


def escape_latex(s: str) -> str:
    """转义 LaTeX 特殊字符。用于来自 Word 的**纯文本**内容。

    注意顺序与幂等性：先把反斜杠换掉，其余字符一次性替换，避免二次转义。
    """
    s = s.replace("\\", "\x00")
    s = _ESCAPE_RE.sub(lambda m: dict(_LATEX_ESCAPES)[m.group(0)], s)
    return s.replace("\x00", r"\textbackslash{}")


def _footnote_latex(segs) -> str:
    r"""脚注片段 → LaTeX（**含公式的脚注**专用）：文字转义、公式原样 `$…$`。

    第四十四批①（83/05、87/06 实测）：旧路径先把脚注片段 `segs_to_text()` 成
    `$…$` 文本，再在渲染时 `escape_latex(fn.text)` → `$` 变 `\$`、`\phi` 变
    `\textbackslash{}phi`，编译报 `Missing $ inserted`（83/05 rc=2 的真因）。
    这里按片段原样渲染；**只有含公式的脚注**走这条路径（其余脚注逐字节不变）。
    """
    parts: list[str] = []
    for sg in segs:
        if sg.kind == "text":
            parts.append(escape_latex(sg.text))
        elif sg.kind == "math":
            parts.append(f"${sg.text}$")
    return "".join(parts)


def escape_bib_value(s: str) -> str:
    """转义 bib 字段值里的 LaTeX 特殊字符（保留已有的 \\& 等）。"""
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            out.append(s[i : i + 2])  # 已转义，原样保留
            i += 2
            continue
        if ch in "&%$#_":
            out.append("\\" + ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


@dataclass
class RefEntry:
    raw: str
    author: str = ""
    year: str = ""
    title: str = ""
    kind: str = "M"
    rest: str = ""
    key: str = ""

    @property
    def surname(self) -> str:
        """用于比对的姓：中文取姓，西文取最后一个词。"""
        a = self.author.strip()
        if not a:
            return ""
        if re.match(r"^[\u4e00-\u9fff]", a):
            return a[0]
        # "Haack, S." / "Beall, J. C." / "Quine, W V O"
        head = re.split(r"[,，]", a)[0].strip()
        return head
    @property
    def surname_cn(self) -> str:
        a = self.author.strip()
        m = re.match(r"^[\u4e00-\u9fff]{2,4}", a)
        return m.group(0) if m else ""


@dataclass
class DocxDoc:
    path: str
    title: str = ""
    author: str = ""
    abstract: str = ""
    keywords: str = ""
    # 中英双栏：英文文章的原稿常常先放英文题名/摘要/关键词，再放中文一套。
    # 若只取「第一个 Keywords 之后」当正文，就会把中文题名和中文摘要当成正文——
    # 实测这让 02 Lu_Jiang 的内容准确率掉到 8.7%。
    title_cn: str = ""
    title_en: str = ""
    abstract_cn: str = ""
    abstract_en: str = ""
    keywords_cn: str = ""
    keywords_en: str = ""
    # 副标题（原稿里另起一段、以破折号开头）。人工成品把它并进 `\mytitle`
    # 并用 `\\` 断行——见 README 第 16 条与 2026-10 的 09 Jiawei_Chu 实测。
    subtitle: str = ""
    subtitle_en: str = ""
    # 文末中英元数据块里的其余内容（作者英文名、单位、邮箱）
    author_en: str = ""
    affiliation_cn: str = ""
    affiliation_en: str = ""
    email: str = ""
    body: str = ""
    refs: list[RefEntry] = field(default_factory=list)
    # 文末参考文献区的**原文行**（未解析，按段落原样、按顺序）。
    #
    # 为什么要多留一份原文：全库有 14 篇稿件明明有文献区却 `n_bib = 0`，其中 9 篇的
    # 文献区里**确实有 10–27 行候选条目**（8 篇人年制无编号 + 1 篇日文 `[N]` 清单）。
    # 编辑部已把文献核实整体交给**编辑各自的 AI**（README 第 41 条），所以工具不再
    # 自己硬写第二种清单解析器（「编错的卷号比空缺更糟」），而是把这些行原样交出去，
    # 由 AI 按同一份规范结构化——见 `slcn/refmark.py` 第四节。
    # ⚠️ 纯附加字段：`refs` / `bib_entries` 与任何既有行为都不受影响。
    ref_raw_lines: list[str] = field(default_factory=list)
    # 「条目 → 源稿原文行」的**逐条**映射：{最终 bib 键: 这一条被解析时所依据的原文}。
    #
    # 为什么要有它（2026-10-15，第 68 条）：`ref_raw_lines` 只在「一条都没解析出来」时兜底，
    # 解析成功但**丢字段**的条目（实测第 66 条 `Dang2022` 丢了刊名）拿不到原文，于是
    # 任务书与 AI 预跑都只能给出残缺件，模型再「如实」报告我们丢的信息。这里把每条
    # 源稿原文按**最终 bib 键**挂上去，任务书与 ⑪ 的每条请求都带上它。
    #
    # 三条通道都填（键都是最终 bib 键，见各自的注释）：
    #   · 文末编号清单 `[N] …` → 整条 `[N] …` 原文（`_numbered_refs`）；
    #   · 脚注体例 → 该条脚注里被解析的那段文献文字（`classify().ref`）；
    #   · 文末人年制区 → `RefEntry.raw`（`parse_reference_line` 收下的那一行）。
    # 拿不到原文的条目（例如作者自带的 `.bib`、AI 重排后新拆出的键）**直接不出现**，
    # 下游也不写占位符——「没有原文」与「原文为空」是两回事。
    ref_raw_by_key: dict[str, str] = field(default_factory=dict)
    headings: list[tuple[int, str]] = field(default_factory=list)
    n_math: int = 0
    n_footnotes: int = 0
    footnote_notes: int = 0
    # [(key, 字段 dict, 原始条目文本), ...] —— 从脚注里的文献生成的 bib 条目
    bib_entries: list[tuple[str, dict, str]] = field(default_factory=list)
    # [(docx 内的原文件名, 导出后的文件名), ...]
    images: list[tuple[str, str]] = field(default_factory=list)
    # 正文里的纯文本编号引用：{编号 → bib 键}（来自文末那份 `[N] …` 清单）
    ref_key_by_num: dict[int, str] = field(default_factory=dict)
    # 第四十九批（C 类新口径）：混合型脚注（说明文字+引用）清单 → build 汇总
    # 「请人工核对」warning；元素 (fid, 片段)。纯日志，不影响产物。
    mixed_footnotes: list[tuple[int, str]] = field(default_factory=list)
    # 第四十九批 C 类新口径：FootnoteWarning 列表（mixed_manual_check /
    # citation_unmatched / field_conflict），build 汇总成中文 warning。
    c_class_warnings: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── 参考文献条目解析 ──────────────────────────────────────────────────────

_REF_RE = re.compile(
    r"^(?P<author>.+?)[\.。]\s*"           # 作者
    r"(?P<year>\d{4}[a-z]?)[\.。]\s*"      # 年份
    r"(?P<title>.*?)"                       # 题名
    r"\[(?P<kind>[A-Z])\]\s*"               # 类型标识 [M]/[J]/[C]
    r"(?://)?\s*(?P<rest>.*)$"              # 出版信息
)


def parse_reference_line(line: str) -> RefEntry | None:
    """解析一条人年制参考文献，如：
    Quine, W V O. 1970. Philosophy of Logic[M]. Englewood: Prentice Hall.
    陈波. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50.
    """
    s = re.sub(r"\s+", " ", line.strip())
    if len(s) < 8:
        return None
    m = _REF_RE.match(s)
    if not m:
        # 没有 [M]/[J] 标识的，退化为「作者. 年份. 其余」
        m2 = re.match(r"^(?P<author>.+?)[\.。]\s*(?P<year>\d{4}[a-z]?)[\.。]\s*(?P<rest>.*)$", s)
        if not m2:
            # 2026-10-17 校准：英文条目走专用分支，中文条目走中文分支，互不抢
            return (_parse_reference_line_en(s)
                    or _parse_reference_line_en_fullname(s)   # 英文全名 + 年份在后（06 型）
                    or _parse_reference_line_cn2(s)     # 中文、年份在第 2 段
                    or _parse_reference_line_cn(s)      # 中文、年份在其余部分
                    # 第四十五批 A2：个人年制鲁棒性（5 期 BIB_COUNT 缺口实测）
                    or _parse_reference_line_apa(s)       # APA：作者 (年份). 题名…
                    or _parse_reference_line_cn_colon(s)  # 中文冒号式：作者：书名，年，出版
                    or _parse_reference_line_cn_apa(s)   # 中文 APA：作者. (年). 题名. 刊名
                    or _parse_reference_line_year_end(s)  # 英文、年份在行尾（旧版 APA/德式）
                    or _parse_reference_line_loose(s))    # 引号题名 + URL/无卷期（SEP/philarchive）
        e = RefEntry(raw=s, author=m2.group("author").strip(), year=m2.group("year"))
        rest = m2.group("rest")
        # 从出版信息里猜类型：含「出版社/ Press/ University」→ 书，含卷期 → 期刊
        e.kind = "J" if re.search(r"\[\s*J\s*\]|\d+\s*\(\d+\)", rest) else "M"
        e.title = rest.split("[")[0].strip()
        e.rest = rest
        e.key = make_key(e)
        return e
    e = RefEntry(
        raw=s,
        author=m.group("author").strip(),
        year=m.group("year"),
        title=re.sub(r"\[[MJC]\].*$", "", m.group("title")).strip(),
        kind=m.group("kind"),
        rest=m.group("rest").strip(),
    )
    e.key = make_key(e)
    return e


#: 中文逗号风格（本刊作者来稿里极常见）：
#:   `陈清泚，“论证研究的认知社会学路径”，《自然辩证法研究》，2020年第2期，第103–108页。`
#:   `张建军，逻辑悖论研究引论，北京：人民出版社，2014年。`
#:   `王雪君、郭佳宏，“对‘意外考试’悖论的一种非确定论解释”，世界哲学，2022年第1期，第152-159页。`
#: 特征：作者到第一个**中文逗号**，题名到第二个，年份在后面的部分。英文/GB 风格走上面的 `_REF_RE`。
_REF_CN_RE = re.compile(
    r"^(?P<author>[^，。；;]{2,60})[，,]\s*"
    r"(?P<title>.+?)[，,]\s*"
    r"(?P<rest>.*?(?:19|20)\d{2}.*)$"
)


#: 英文条目在中文列表里的常见风格（本刊作者来稿实测）：
#:   `A. Aldini, S.A. Alexander, P. Graziani, 2022, “Knowledge-of-own-factivity, …”, Journal, 1(2): 3-4.`
#:   `T. Williamson, 2000, Knowledge and Its Limits, New York: Oxford University Press.`
#: 特征：**作者列表**（逗号分隔），然后 `, YYYY,`（年份是第 2 段），其后是题名与出版信息。
_REF_EN_RE = re.compile(
    r"^(?P<authors>.+?)\s*,\s*(?P<year>(?:19|20)\d{2}[a-z]?)\s*,\s*(?P<rest>.+)$"
)
_QUOTED = re.compile(r"^[“\"「『'](?P<title>.+?)[”\"」』'']")


def _parse_reference_line_en(s: str) -> RefEntry | None:
    """英文逗号风格的单条文献（只在**不含中文**的行上调用）。"""
    if re.search(r"[\u4e00-\u9fa5]", s):
        return None
    # ⚠️ 编号清单（`[12] …` / `12. …`）有专门通道 `_numbered_refs`，英文分支同样必须让路。
    #    2026-10-17 校准第一版没让路 → `test_cn_journal_numbered` 4 条端到端断言转红。
    if re.match(r"^\s*(?:\[\d+\]|（\d+）|\(\d+\)|\d+[.、])\s*", s):
        return None
    m = _REF_EN_RE.match(s)
    if not m:
        return None
    authors = m.group("authors").strip().rstrip(",").strip()
    year = m.group("year")
    rest = m.group("rest").strip()
    qm = _QUOTED.match(rest)
    if qm:                                   # 有引号 → 题名在引号里，出版信息在引号之后
        title = qm.group("title").strip()
        after = rest[qm.end():].lstrip(",，. ")
    else:                                    # 无引号 → 题名到下一个逗号（书常用）
        parts = [x.strip() for x in re.split(r"[,，]", rest, maxsplit=1)]
        title = parts[0]
        after = parts[1] if len(parts) > 1 else ""
    # ⚠️ P0-2（期 87 sub5/sub2 实测）：`rest` 必须是**题名之后**的出版信息；旧实现
    #    存的是**含引号题名的整段 rest**，而 `refs_to_bib()` 的期刊正则要求 rest 以刊名
    #    开头 → journal/volume/number/pages 永不命中（22 条里丢 18 条 pages）。
    # T13（2026-10-18）：类型不再用「卷期正则命中」猜，统一交给 `_finalize_en_entry()`
    # （`footref.classify_entry_type()` 同源判据 + author 统一倒装）。
    e = RefEntry(raw=s, author=authors, year=year, title=title.strip().strip("《》"),
                 kind="M", rest=after)
    e.key = make_key(e)          # 键在倒装**之前**生成：key 方案不变（产物差异最小）
    _finalize_en_entry(e)
    return e


def _looks_short_bibliographic_note(text: str) -> bool:
    """短英文脚注里「像文献条目」的（有 p./pp. 页码或出版社词）仍按文献条目处理。

    第 121 条回归：中文稿里常夹英文文献脚注（`H. Reichenbach, The Theory of
    Probability, p. 445.`），它们 parse_entry 只能得到 note，但**是文献**、必须保留
    原有「脚注条目 → bib」行为，不能按散文注释降级（否则正文引用全丢）。
    """
    t = text.strip()
    # 强信号（多条目文献列表常常很长，长度上限只对下面的弱信号生效）：
    # GB/T 7714 文献类型标识、网络地址。
    if re.search(r"\[[MJC]\]", t) or re.search(r"https?://|www\.", t):
        return True
    if len(t) > 400:
        return False
    if re.search(r"\bpp?\.\s*\d", t):
        return True
    if re.search(r"\b(?:Press|Publishing|Verlag|Routledge|Blackwell|Wiley|Springer|University)\b", t):
        return True
    # 带引号的题名（`“Deconstructing the Laws of Logic,” Philosophy 83 (323)…`）
    if len(re.findall(r"[“”]", t)) >= 2 or t.count('"') >= 2:
        return True
    # 期刊卷期写法（`83 (323)` / `Vol. 68 (1)`）
    if re.search(r"\b(?:Vol|vol|No|no)\.?\s*\d+|\d+\s*\(\d+\)", t):
        return True
    # P0-6（期 87 sub9 实测）：`M. Bolduc & David A. Frank (eds.), The Intellectual and
    # Cultural Origins of … 1933-1958, Brill Academic Pub, 2023.` —— 出版社写作
    # `Brill Academic Pub`，既没有页码/引号/卷期，旧信号全落空 → 被当成散文注释、
    # 整条不进 bib。补三个「像文献」信号（都要求**同时**出现年份，避免误伤散文）。
    if re.search(r"\(eds?\.\)|\beds?\.|\bEdited by\b", t, re.I):
        return True
    if (re.search(r"\b(?:19|20)\d{2}\b", t)
            and re.search(r"\b(?:Pub|Press|Publishing|Verlag|Books?|University|"
                          r"Academic|Brill|Routledge|Blackwell|Springer|Wiley)\b", t)):
        return True
    return False


def _entry_is_note_only(entry: dict | None) -> bool:
    """parse_entry() 的结果是否只有 note（= 没能结构化出作者/题名，不是文献条目）。"""
    if not entry:
        return True
    return not any(str(entry.get(k) or "").strip() for k in ("author", "title"))


#: 英文「全名 + 年份在后」的参考文献区格式（期 88 / 06 Zhixi_Chen 真稿实测）：
#:   `Jacobs, Scott, Jackson, Sally, 1989. Building a model of conversational argument. In …`
#:   `Van Eemeren, Frans et al. 2014. Handbook of Argumentation Theory. …`
#: 特征：作者是「姓, 名」成对用逗号连写（可带 et al./&），**年份在作者后、后跟句点**。
#: 注意与 `_REF_EN_RE`（`作者, 年份, 其余`）区分：这条的年、作者之间用**句点**收尾。
_REF_EN_FULLNAME_RE = re.compile(
    r"^(?P<pre>.+?)\s*(?P<year>(?:19|20)\d{2})\.\s+(?P<rest>.+)$"
)
_EN_AUTHOR_SPLIT = re.compile(r"\s*(?:,|&|\band\b)\s*")


def _initials(given: str) -> str:
    """Given name → 缩写（`Jean-Claude` → `J.-C.`，`Merce` → `M.`）。"""
    out = []
    for part in given.split():
        part = part.strip(".")
        if not part:
            continue
        if "-" in part:
            out.append("-".join(p[0].upper() + "." for p in part.split("-") if p))
        else:
            out.append(part[0].upper() + ".")
    return " ".join(out)


def _parse_reference_line_en_fullname(s: str) -> RefEntry | None:
    """解析 `Surname, Given, Surname2, Given2, YYYY. Title…` 英文条目（06 Zhixi_Chen 型）。"""
    if re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if re.match(r"^\s*(?:\[\d+\]|（\d+）|\(\d+\)|\d+[.、])\s*", s):
        return None
    m = _REF_EN_FULLNAME_RE.match(s)
    if not m:
        return None
    pre, year, rest = m.group("pre").strip(), m.group("year"), m.group("rest").strip()
    if not (5 <= len(pre) <= 240) or re.search(r"\d{4}", pre):
        return None
    if "," not in pre and "et al" not in pre:
        return None
    pre = re.sub(r"\bet al\.?$", "", pre).strip().rstrip(",.").strip()
    if not pre:
        return None
    tokens = [x for x in _EN_AUTHOR_SPLIT.split(pre) if x and x.lower() != "et al"]
    if len(tokens) < 2:
        return None
    # 「姓, 名」成对；奇数尾项当只有姓
    pairs: list[tuple[str, str]] = []
    for i in range(0, len(tokens), 2):
        surname = tokens[i].strip(" .")
        given = tokens[i + 1].strip(" .") if i + 1 < len(tokens) else ""
        if not surname or not surname[0].isupper():
            return None
        if len(surname.split()) > 3 or len(given.split()) > 4:
            return None
        pairs.append((surname, given))
    if not pairs:
        return None
    authors = " and ".join(
        f"{sn}, {_initials(gv)}" if gv else sn for sn, gv in pairs
    )
    # 题名 = 第一个句点+空格+大写 之前（没有就取全部 rest）
    tm = re.split(r"\.\s+(?=[A-Z])", rest, maxsplit=1)
    title = tm[0].rstrip(".").strip()
    after = tm[1] if len(tm) > 1 else ""
    # T13：类型走 `footref.classify_entry_type()`（出版信息特征），author 出口统一倒装
    e = RefEntry(raw=s, author=authors, year=year, title=title, kind="M", rest=after)
    e.key = make_key(e)          # 键在倒装**之前**生成：key 方案不变（产物差异最小）
    _finalize_en_entry(e)
    return e


#: 中文逗号风格但**年份在第 2 段**（实例测试里也出现）：
#:   `李四, 2021, 语用学导论, 北京: 商务印书馆.`
#: 与 `_parse_reference_line_cn`（年份在"其余部分"）互补；两者都只处理含中文的行。
_REF_CN2_RE = re.compile(
    r"^(?P<author>[^，。；;]{2,60})[，,]\s*"
    r"(?P<year>(?:19|20)\d{2}[a-z]?)\s*[，,]\s*"
    r"(?P<rest>.+)$"
)


def _parse_reference_line_cn2(s: str) -> RefEntry | None:
    """中文逗号风格、**年份在第 2 段**的条目。"""
    if not re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if re.match(r"^\s*(?:\[\d+\]|（\d+）|\(\d+\)|\d+[.、])\s*", s):
        return None
    m = _REF_CN2_RE.match(s)
    if not m:
        return None
    rest = m.group("rest").strip()
    parts = re.split(r"[，,]", rest, maxsplit=1)
    title = parts[0].strip().strip("《》")
    kind = "M"
    if "学位论文" in s or "博士论文" in s or "硕士论文" in s:
        kind = "D"
    elif "《" in s or re.search(r"第?\d+[期卷]", s):
        kind = "J"
    e = RefEntry(raw=s, author=m.group("author").strip(), year=m.group("year"),
                 title=title, kind=kind, rest=rest)
    e.key = make_key(e)
    return e


def _parse_reference_line_cn(s: str) -> RefEntry | None:
    """中文逗号风格的单条文献（2026-10-17 实例测试：用户 10 篇里 2 篇整份列表都这种格式）。

    ⚠️ 只应在**参考文献区**的行上调用（正文段落也含逗号+年份，直接全篇跑会误判）。
    """
    # ⚠️ 编号清单（`[12] 作者，题名，…` / `12. 作者…`）有**专门的通道**（`_numbered_refs`），
    #    这里必须让路：2026-10-17 第一版没让路，把 `test_cn_journal_numbered` 的 4 条端到端
    #    断言打红了（编号条目被这里当成普通中文条目，通道就收不到了）。
    if re.match(r"^\s*(?:\[\d+\]|（\d+）|\(\d+\)|\d+[.、])\s*", s):
        return None
    # ⚠️ 只处理**含中文**的行：英文条目走 `_parse_reference_line_en`。
    #    2026-10-17 校准前，英文条目（`A. Aldini, S.A. Alexander, P. Graziani, 2022, “题名”, …`）
    #    被这里按"作者，题名，其余"切分，**合著者名被当成题名**（title=P. Graziani）。
    if not re.search(r"[\u4e00-\u9fa5]", s):
        return None
    m = _REF_CN_RE.match(s)
    if not m:
        return None
    rest = m.group("rest")
    ym = re.search(r"(?:19|20)\d{2}", rest)
    if not ym:
        return None
    title = m.group("title").strip().strip("《》")
    kind = "M"
    if "学位论文" in s or "博士论文" in s or "硕士论文" in s:
        kind = "D"
    elif "《" in s or re.search(r"第?\d+[期卷]", s):
        kind = "J"
    e = RefEntry(raw=s, author=m.group("author").strip(), year=ym.group(0),
                 title=title, kind=kind, rest=rest.strip())
    e.key = make_key(e)
    return e


#: 编号清单行前缀（含**写歪的** `[2 Author…` 少括号）：人年制新兜底一律让路，
#: 由 `_numbered_refs()` 收（案例 116 的 ① 契约：编号清单 4 条必须进 bib_entries）。
_NUMBERED_PREFIX_RE = re.compile(
    r"^\s*(?:\[\s*\d+|（\s*\d+|\(\s*\d+|\d+\s*[.、])"
)


# ── 第四十五批 A2：个人年制解析鲁棒性（5 期 BIB_COUNT 6 篇实测） ────────────
# 失败行样例（bib-refs 报回 /tmp/audit_lead/batch45-bib.md）：
#   86-07/86-08（英文 APA 清单 38/31 行整份 0 条）：
#     `Beall, J. C., & Restall, G. (2000). Logical pluralism. Australasian Journal of
#      Philosophy, 78(4), 475–493.`
#   86-02（中文冒号式 4 条）：`王先谦：荀子集解，1988年，北京：中华书局。`
#   87-5（2 条）：`A. Hájek, “Most counterfactuals are false”, (2019-08-28)[…]. https://…`
#     `P. Rysiew, “Epistemic Contextualism”, in … (eds.), The Stanford Encyclopedia of
#      Philosophy, https://…/win2023/…`
#   88-01（4 条）：年份区间（`1929-1931`）、年份在行尾（德式）、APA 编辑行、残行。
# 纪律：**只按行内证据解析**（作者/年份/题名都要在行里出现）；解析失败保留原文，
# 由调用方 warning + `ref_raw_lines` 交人工/AI。全部为**新增兜底**，排在既有解析器
# 之后——已能解析的行不会走到这里，产物逐字节不变。
_APA_YEAR_RE = re.compile(
    r"^(?P<authors>.+?)\s*[(（]\s*(?P<year>(?:19|20)\d{2})"
    r"(?:\s*[-–—]\s*(?:19|20)\d{2})?[a-z]?\s*[)）]\s*[.。]?\s*(?P<rest>.+)$"
)
_APA_YEAR_RANGE_RE = re.compile(
    r"^(?P<authors>.+?)[.。]\s*(?P<year>(?:19|20)\d{2})"
    r"\s*[-–—]\s*(?:19|20)\d{2}[.。]\s*(?P<rest>.+)$"
)
_EN_SENTENCE_END = re.compile(r"(?<=[.?!])[\s\n]+(?=[A-Z“\"(\[])")


def _clean_apa_authors(pre: str) -> str:
    """APA 作者串 → bib author 字段（`&` → `and`，去尾标点）。"""
    x = pre.strip()
    x = re.sub(r"[,，]\s*&\s*", " and ", x)      # `Beall, J. C., & Restall, G.`
    x = re.sub(r"\s+&\s+", " and ", x)          # `A & B`
    x = re.sub(r",\s*,", ",", x)
    return x.strip(" ,;.")


def _apa_kind(after: str) -> str:
    """按行内出版信息判类型：有卷(期)/页码像期刊，`In …(Eds.)`/出版社像书。"""
    a = after.strip()
    if re.search(r"\(\s*Eds?\.\s*\)|^\s*In\s+", a):
        return "M"
    if re.search(r",\s*\d+\s*(?:\(\d+\))?\s*,\s*\d+", a) or re.search(r"\d+\s*\(\d+\)\s*[,:]", a):
        return "J"
    return "M"


def _parse_reference_line_apa(s: str) -> RefEntry | None:
    r"""APA 风格：`作者列表 (年份). 题名. 出版信息`（英文；86-07/86-08 整份清单）。"""
    if re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if _NUMBERED_PREFIX_RE.match(s):
        return None
    m = _APA_YEAR_RE.match(s) or _APA_YEAR_RANGE_RE.match(s)
    if not m:
        return None
    authors = _clean_apa_authors(m.group("authors"))
    if not authors or len(authors) > 240 or not re.search(r"[A-Za-z]", authors):
        return None
    rest = m.group("rest").strip()
    if len(rest) < 3:
        return None
    chunks = _EN_SENTENCE_END.split(rest, maxsplit=1)
    title = chunks[0].rstrip(". ").strip()
    after = chunks[1].strip() if len(chunks) > 1 else ""
    if not title or len(title) > 300:
        return None
    kind = _apa_kind(after)
    e = RefEntry(raw=s, author=authors, year=m.group("year"),
                 title=title, kind=kind, rest=after)
    e.key = make_key(e)
    _finalize_en_entry(e)
    e.kind = kind          # `_finalize_en_entry` 的通用分类会把期刊判成书，这里按行内证据保留
    return e


_CN_COLON_RE = re.compile(r"^(?P<author>[^：:]{2,60}?)\s*[：:]\s*(?P<rest>.+)$")


def _parse_reference_line_cn_colon(s: str) -> RefEntry | None:
    r"""中文冒号式：`作者（编/撰）：《书名》，年份，出版地：出版社。`（86-02 实测）。"""
    if not re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if _NUMBERED_PREFIX_RE.match(s):
        return None
    m = _CN_COLON_RE.match(s)
    if not m:
        return None
    author = m.group("author").strip()
    rest = m.group("rest").strip()
    ym = re.search(r"((?:19|20)\d{2})", rest)
    if not ym or len(rest) < 6:
        return None
    qm = re.search(r"《([^》]+)》", rest)
    if qm:
        title = qm.group(1).strip()
    else:
        title = re.split(r"[，,。]", rest, maxsplit=1)[0].strip()
    if not title or len(title) > 200:
        return None
    kind = "M"
    if re.search(r"学位论文|博士论文|硕士论文", rest):
        kind = "D"
    elif re.search(r"学报|期刊|杂志|第?\s*\d+\s*[期卷]", rest):
        kind = "J"
    e = RefEntry(raw=s, author=author, year=ym.group(1), title=title, kind=kind, rest=rest)
    e.key = make_key(e)
    return e


_EN_YEAR_END_RE = re.compile(
    r"^(?P<author>[A-Z][^,]{1,40}(?:,\s*[^,]{1,60})?),\s*"
    r"(?P<rest>.+?(?P<year>(?:19|20)\d{2})[^\d]*)$"
)


def _parse_reference_line_year_end(s: str) -> RefEntry | None:
    r"""英文、**年份在行尾**（旧版 APA / 德式书目，88-01 Osma 实测）。

    `Osma, Wilhelm von, De Consequentiis — Über die Folgerungen, hrsg. F. Schupp,
     Meiner, Hamburg 1991.` → author=Osma, Wilhelm von / year=1991。
    """
    if re.search(r"[\u4e00-\u9fa5]", s) or len(s) < 20:
        return None
    if _NUMBERED_PREFIX_RE.match(s):
        return None
    # 引号题名的行交给 loose 解析（作者/题名切分更可靠；本解析器会把题名当成作者一部分）
    if _QUOTED_TITLE_RE.match(s):
        return None
    m = _EN_YEAR_END_RE.match(s)
    if not m:
        return None
    rest = m.group("rest").strip()
    if len(rest) < 8:
        return None
    parts = [x.strip() for x in re.split(r"[,，]", rest, maxsplit=1)]
    title = parts[0]
    after = parts[1] if len(parts) > 1 else ""
    if not title:
        return None
    e = RefEntry(raw=s, author=m.group("author").strip(), year=m.group("year"),
                 title=title, kind="M", rest=after)
    e.key = make_key(e)
    _finalize_en_entry(e)
    return e


_CN_APA_RE = re.compile(
    r"^(?P<author>[\u4e00-\u9fa5、，,A-Za-z][^.。]{0,40}?)[.。]\s*"
    r"[(（](?P<year>(?:19|20)\d{2})[)）]\s*[.。]\s*(?P<rest>.+)$"
)


def _parse_reference_line_cn_apa(s: str) -> RefEntry | None:
    r"""中文 APA 风格：`作者. (年份). 题名. 刊名, 卷(期), 页码.`（86-07 实测）。"""
    if not re.search(r"[\u4e00-\u9fa5]", s):
        return None
    if _NUMBERED_PREFIX_RE.match(s):
        return None
    m = _CN_APA_RE.match(s)
    if not m:
        return None
    rest = m.group("rest").strip()
    if len(rest) < 4:
        return None
    title = re.split(r"[.。]", rest, maxsplit=1)[0].strip()
    if not title or len(title) > 200:
        return None
    kind = "J" if re.search(r"学报|期刊|杂志|,\s*\d+", rest) else "M"
    e = RefEntry(raw=s, author=m.group("author").strip(), year=m.group("year"),
                 title=title, kind=kind, rest=rest)
    e.key = make_key(e)
    return e


_QUOTED_TITLE_RE = re.compile(
    r'^(?P<author>.{2,160}?)[,，]\s*[“"「『](?P<title>.+?)[”"」』]\s*[,，]?\s*(?P<rest>.*)$'
)


def _parse_reference_line_loose(s: str) -> RefEntry | None:
    r"""引号题名 + URL/无常规卷期的行（87-5 SEP / philarchive 实测）。

    `A. Hájek, “Most counterfactuals are false”, (2019-08-28)[2015-05-15]. https://…`
    `P. Rysiew, “Epistemic Contextualism”, in … (eds.), The Stanford Encyclopedia of
     Philosophy, https://…/win2023/…`（年份取行内 `win2023` 的 2023）
    """
    if _NUMBERED_PREFIX_RE.match(s):     # 编号清单行走 `_numbered_refs()`（案例 116 契约）
        return None
    m = _QUOTED_TITLE_RE.match(s)
    if not m:
        return None
    author = m.group("author").strip().rstrip(",，")
    title = m.group("title").strip()
    rest = m.group("rest").strip()
    if not author or not title or len(title) > 300:
        return None
    ym = re.search(r"((?:19|20)\d{2})", rest) or re.search(r"(?:19|20)\d{2}", s)
    if not ym:
        return None
    year = ym.group(1) if ym.groups() else ym.group(0)
    kind = "J" if re.search(r"Journal|Review|Studies|Proceedings|Quarterly", rest) else "M"
    e = RefEntry(raw=s, author=author, year=year, title=title, kind=kind, rest=rest)
    e.key = make_key(e)
    _finalize_en_entry(e)
    e.kind = kind          # 同 APA：类型以行内证据为准
    return e


def _numbered_refs(
    lines: list[str],
) -> tuple[list[tuple[str, dict, str]], dict[int, str], dict[str, str]]:
    """解析 `[N] 作者, 年份, "题名", 刊名, 卷(期): 页` 这种编号清单。

    返回 `(条目列表, {N: bib 键}, {bib 键: 整条 `[N] …` 原文})`。第二个返回值是给
    **正文里的纯文本引用**用的：正文写 `（[21]，第33页）`，编号 21 指的就是这份清单的第 21 条。
    第三个返回值是「条目 → 源稿原文」的映射（第 68 条），由 `parse_docx` 收进
    `doc.ref_raw_by_key`，再交给任务书与 AI 预跑——**只给被留下的条目**（跳过的不出现）。

    与「参考文献附件」用的是同一套解析器（`footref`，README 第 28 条）。
    条目形如 `(key, 字段 dict, 原文)`——与脚注文献那条通道**同形**，
    因此直接并进 `doc.bib_entries`，由 `footnotes.entry_to_bib` 写成 bib。

    实测 09 Jiawei_Chu：文末 18 条全是这种体例，旧实现只认人年制（`作者. 年. …[J]. …`），
    于是「参考文献 0 条」、bib 里一条都没有。
    """
    from . import footref
    from .bibcheck import author_has_role_mark as _has_role_mark
    from .footnotes import make_key as _fn_make_key

    out: list[tuple[str, dict, str]] = []
    num2key: dict[int, str] = {}
    raw_by_key: dict[str, str] = {}
    used: set[str] = set()
    # ⚠️ 用 `split_numbered_raw`（与 `parse_numbered_list` 同一套切分）：编号与原文**同源**，
    #    不会出现「这条原文配到另一条条目上」。`split_numbered_raw` 已按同一口径过滤短段。
    def _entry_dict(it) -> dict:
        return {
            "_type": it.etype,
            "author": it.author, "title": it.title,
            "journal": it.journal, "booktitle": it.booktitle,
            "volume": it.volume, "number": it.number, "pages": it.pages,
            "publisher": it.publisher, "address": it.address,
            "year": it.year, "note": it.note,
        }

    # P0-5a（期 87 sub7 实测）：编号清单**第一个标记之前**的无编号条目（作者漏编号）
    # 旧实现整段丢弃。这里把标题行去掉后、首标记之前的内容按行补成第 1..n 条；
    # 这类条目没有编号，**不写 num2key**（不能影响正文编号→键映射）。
    text_all = "\n".join(lines)
    first_mark = re.search(r"(?:^|\n)\s*[\[\(（]\s*\d{1,3}\s*[\]\)）]", text_all)
    # ⚠️ 只有「确实存在编号标记」时，首个标记前那一行才是「漏编号的第 1 条」；
    #    整份没有编号的（人年制英文/中文清单）必须交给 `parse_reference_line` 通道，
    #    否则会被当成 N 条无编号条目、覆盖掉正确解析（期 88 Tuomo_Aho 实测）。
    prefix = text_all[: first_mark.start()] if first_mark else ""
    prefix_lines = [
        line.strip() for line in prefix.splitlines()
        if line.strip() and not re.match(r"^\s*(?:参考文献|References?|参考书目)\s*$", line, re.I)
    ]
    for raw_line in prefix_lines:
        if len(raw_line) <= 12:
            continue
        it0 = footref.parse_numbered_item(raw_line)
        if not it0.title or not (it0.year or it0.author):
            continue
        entry0 = _entry_dict(it0)
        key0 = _fn_make_key(entry0, len(out) + 1, used)
        out.append((key0, entry0, raw_line))
        raw_by_key[key0] = raw_line

    for seq, (raw, seg) in enumerate(footref.split_numbered_raw("\n".join(lines)), 1):
        # 编号取**原文标号本身**（`[2]` → 2），不能用 enumerate 的位置：
        # 清单允许首条无编号（P0-5）时，位置与正文里的编号会差 1。
        _nm = re.match(r"^\s*[\[\(（]\s*(\d{1,3})\s*[\]\)）]", raw)
        num = int(_nm.group(1)) if _nm else seq
        it = footref.parse_numbered_item(seg)
        # ⚠️ 题名短于 4 字通常是「切错了」（把译者/刊名切成了题名）。
        #    但中文著/译体例是**例外**（第 64 条）：`亚里士多德（著），张竹明（译），工具论，…`
        #    的真题名可能只有 3 个字——此时作者段带「（著）/（译）」标记、题名是**下一**段，
        #    按长度丢掉整条会让正文的 `\parencite` 悬空（实测 1 篇：反推数学与无穷 kxj.docx）。
        #    P0-4b（期 87 sub8）：**不再静默丢**——切错的条目保留并写 note，交人工核对。
        if not it.title:
            continue
        short_title = (len(it.title) < 4
                       and not _has_role_mark(it.author, both=True))
        # 只有单侧著/译标记（如「（著）」没有「（译）」）时，短题名几乎肯定是把
        # 译者/刊名切错了——维持旧行为**丢弃**（测试 test_authornorm ⑤c /
        # test_ref_raw_lines 反例）；P0-4 的「保留+note」只针对无角色标记的情形。
        if short_title and _has_role_mark(it.author):
            continue
        entry = _entry_dict(it)
        if short_title:
            entry["note"] = (f"题名解析可疑（{it.title}），已保留原条目，"
                             "请按源稿原文核对题名")
        key = _fn_make_key(entry, len(out) + 1, used)
        out.append((key, entry, it.title))
        if _nm:                       # 只有带标号的条目才进「编号→键」映射
            num2key[num] = key
        raw_by_key[key] = raw
    return out, num2key, raw_by_key


def _make_key_core(a: str, year: str, disambiguate: str = "") -> str | None:
    """按「译名表 → 中文全拼音 → 西文姓」分类生成 bib 键。

    分不出来返回 `None`（由 `make_key` 决定是「剥零宽重判」还是「走兜底键」）——
    把「分类」与「兜底」拆开，才能保证 `make_key` 里那条
    「先按原串判、失败了再剥零宽重判」的顺序写出来（案例 74）。
    """
    for cn, en in TRANSLATED_NAMES.items():
        if a.startswith(cn):
            return f"{en}{year}{disambiguate}"
    m = re.match(r"^([\u4e00-\u9fff]{2,4})", a)
    if m:
        # 中文姓名取**全拼音**：陈波 → ChenBo、刘靖贤 → LiuJingxian、哈瑞 → HaRui。
        # 早先只对姓查表、名留汉字，生成了 `Chen波2005` 这种混着中文的键
        # （BibLaTeX 虽能容忍，但难看且易出问题）。实测人工成品用的是全拼音。
        name = m.group(1)
        try:
            from .naming import chinese_to_folder

            folder = chinese_to_folder(name)   # Bo_Chen
            given_py, sur_py = folder.split("_", 1)
            return f"{sur_py}{given_py}{year}{disambiguate}"
        except Exception:  # noqa: BLE001  pypinyin 缺失时退回旧逻辑
            sur = SURNAME_PINYIN.get(name[0], name[0])
            return f"{sur}{name[1:]}{year}{disambiguate}"
    if re.match(r"^[A-Za-z]", a):
        # 多作者：Beall, J. C. and Restall, G. → BeallRestall
        parts = re.split(r"\s+and\s+|&", a)
        names = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            w = re.match(r"([A-Za-z][A-Za-z'\-]+)", p)
            if w:
                names.append(w.group(1))
        stem = "".join(names[:2]) or re.sub(r"[^A-Za-z]", "", a)[:12]
        return f"{stem}{year}{disambiguate}"
    return None


def make_key(e: RefEntry, disambiguate: str = "") -> str:
    """生成 bib 键。优先中文译名表，其次中文姓取拼音，再次西文姓。

    案例 74（2024_01_Wenwen_Jiao）：**分类之前先剥零宽/BOM**。
    这批西文文献的作者串以不可见字符开头（`'\\ufeffS. Blackburn'`、`'\\ufeffR. Audi'`），
    于是 `re.match(r"^[A-Za-z]", a)` 失配，本可以正常生成 `SBlackburn1998` 的条目
    只好走兜底键。实测该篇 16 条、全库 5 篇共 39 条落到兜底。

    ⚠️ 顺序是「**先按原串判，判不出来再剥零宽重判**」，不是「先剥再判」：
    只有这样才能保证**已经能分类的条目键逐字不变**（`ChenBo2005`、`Beall2006`、
    `Xing1984` …）。逐字不变这条不变量是本缺陷的验收核心——先剥再判会顺手改掉
    「名字中间夹零宽」的条目：`'Jo\\ufeffhn Smith'` 今天走西文分支取到 `Jo2005`
    （`re.match` 在零宽处截断），先剥再判会变成 `John2005`。
    """
    a = e.author.strip()
    key = _make_key_core(a, e.year, disambiguate)
    if key is not None:
        return key
    a_clean = _strip_zero_width(a).strip()
    if a_clean and a_clean != a:
        key = _make_key_core(a_clean, e.year, disambiguate)
        if key is not None:
            return key
    # 兜底键必须**确定**（案例 74 的第二半）：
    # ⚠️ 不能用内置 `hash()`。CPython 对 str 的 `hash()` 受 **PYTHONHASHSEED 随机化**
    #    影响（每个进程都不同），因此同一个 `docxsource.py` 对同一份稿子在两个进程里
    #    会给出**不同的 bib 键**——实测同一篇 2024_01_Wenwen_Jiao：
    #      进程 1：ref56162006, ref30821998, ref10261987 …
    #      进程 2：ref36242006, ref46911998, ref64241987 …
    #    后果是正文里的 `\parencite{ref…}` 与 `reference.bib` 一起抖，产物不可复现，
    #    「`.ai前` 与合并后逐字节比对」与 corpus 回归在这类稿子上永远失败。
    #    `zlib.crc32` 是纯函数：同一串在任何进程、任何 PYTHONHASHSEED 下都相同。
    #    取 8 位十六进制（定宽，便于肉眼比对），后面仍接年份以保持旧键的形状。
    return f"ref{zlib.crc32(a.encode('utf-8')):08x}{e.year}"


def _dedupe_keys(refs: list[RefEntry]) -> None:
    """同年同作者的条目加 a/b/c 后缀（本刊惯例，如 Chen2014a）。

    案例 74：分组名先剥零宽。`make_key` 修好之后，`'\\ufeffS. Blackburn'` 与
    `'S. Blackburn'` 同年会生成**同一个**键 `SBlackburn1998`；而 `RefEntry.surname`
    取的是原串，两者分组名不同、加不上 a/b 后缀，于是 bib 里出现两个同名条目。
    分组名归一（剥零宽）就把这个洞补上了。
    实测全库 101 篇里「归一后会改变分组的条目」= **0** 条，所以既有键一条都没动。
    """
    groups: dict[tuple[str, str], list[RefEntry]] = {}
    for e in refs:
        name = _strip_zero_width(e.surname_cn or e.surname)
        groups.setdefault((name, e.year), []).append(e)
    for entries in groups.values():
        if len(entries) > 1:
            for i, e in enumerate(entries):
                e.key = make_key(e, chr(ord("a") + i))


#: 期刊出版信息正则（refs_to_bib 与 T13 分类共用同一份——判据只有一处实现）。
_EN_JOURNAL_RE = re.compile(
    r"^(?P<journal>[^,，]+?)\s*[,，]\s*"
    r"(?:(?P<year>(?:19|20)\d{2})\s*[,，]?\s*)?"
    r"(?P<volume>\d+)\s*\((?P<number>[^)]*)\)\s*[:：]\s*"
    r"(?P<pages>[\d\-\u2013\u2014]+)"
)
#: 无卷号形态：`Mind 85: 112-119`
_EN_JOURNAL_RE2 = re.compile(
    r"^(?P<journal>[^,，]+?)\s*[,，]?\s*(?P<volume>\d+)?\s*[:：]\s*"
    r"(?P<pages>[\d\-\u2013\u2014]+)\s*[.。]?\s*$"
)

# ── T13（2026-10-18）：英文人年制条目接 T12 同源判据 ──────────────────────────
#
# 旧实现用「卷期正则是否命中 / 有没有 Press 词」猜 kind（只有 M/J/C 三种），
# 实测期 87 sub2/sub5/sub6/sub8 的类型错（@book→@misc、@article→@incollection、
# @misc→@book 等）。T13 把**唯一判据**接过来：
#   · 类型：`slcn/footref.py: classify_entry_type(fields, raw=原文)`（与编号清单/脚注通道同源）；
#   · author：出口统一 `normalize_authors → normalize_author_field`（与 T12 同一 helper，
#     也即 `_bib_author()` 的同源顺序），只改英文路径。
_KIND_BY_ETYPE = {"book": "M", "article": "J", "incollection": "C",
                  "inproceedings": "P", "phdthesis": "D", "report": "R"}


def _en_entry_fields(e: RefEntry) -> dict:
    """英文 RefEntry 的出版信息 → `classify_entry_type()` 需要的 fields。

    复用 `refs_to_bib()` 的同一批提取器（期刊正则 / `_split_publisher()` /
    `_incollection_book_editor()`），**不新写解析逻辑**；判据本身在 footref。
    """
    from . import footref

    rest = re.sub(r"^[.。]\s*", "", (e.rest or "").strip())
    fields: dict[str, str] = {}
    jm = _EN_JOURNAL_RE.match(rest) or _EN_JOURNAL_RE2.match(rest)
    if jm:
        gd = jm.groupdict()
        fields["journal"] = (gd.get("journal") or "").strip()
        fields["volume"] = gd.get("volume") or ""
        fields["number"] = gd.get("number") or ""
        fields["pages"] = (gd.get("pages") or "").replace("-", "--")
    else:
        # 页码带字母前缀/期号带英文词的期刊形态（`66(Supplement): S362-S378`，
        # 期 87 sub2 Fitelson1999 实测）`_EN_JOURNAL_RE` 的纯数字页匹配不上；
        # 判**类型**时仍要认出这是期刊（字段提取仍走 refs_to_bib 原口径，不扩范围）。
        mj = re.match(
            r"^(?P<journal>[^,，]+?)\s*[,，]\s*\d+\s*\([^)]*\)\s*[:：]\s*"
            r"(?P<pages>[A-Za-z]?\d[A-Za-z0-9\-–—]*)\s*[.。]?\s*$", rest)
        if mj:
            fields["journal"] = mj.group("journal").strip()
            fields["pages"] = mj.group("pages").replace("-", "--")
    pub, addr = _split_publisher(rest)

    def _plausible_pub(v: str) -> bool:
        """出版社/出版地不可是 `S362-S378` / `66(Supplement)` 这类页码/卷号（期 87 Fitelson 实测）。"""
        return bool(v) and not re.search(r"\d", v) and bool(re.search(r"[A-Za-z\u4e00-\u9fff]", v))

    if _plausible_pub(pub):
        fields["publisher"] = pub
    if _plausible_pub(addr):
        fields["address"] = addr
    bt, _ed = _incollection_book_editor(rest)
    if bt:
        fields["booktitle"] = bt
    if not fields.get("publisher"):
        # 「出版地: 出版社」形态（`Oxford: Clarendon.` / `New York, NY: Basic Books`）——
        # `_split_publisher()` 只认出版社线索词，Clarendon 这类会漏；这里用 T12 的
        # `looks_like_place_publisher()`（同一判据）补一次。
        pp = footref.looks_like_place_publisher(rest.strip().rstrip("."))
        if pp:
            fields["address"], fields["publisher"] = pp
        else:
            m = re.search(
                r"([A-Z][A-Za-z.\- ]{1,28}(?:,\s*[A-Z]{2})?)\s*[:：]\s*"
                r"([A-Z][A-Za-z&.'\- ]{2,60})\s*[.。]?\s*$", rest)
            if m:
                fields["address"], fields["publisher"] = (
                    m.group(1).strip(), m.group(2).strip())
    if re.search(r"\bin\b|\(eds?\.?\)|\(Eds?\.?\)|edited\s+by", rest, re.I):
        fields["note"] = rest
    return fields


#: 机构/组织作者（不推断倒装；人名里一般不会出现）
_EN_ORG_RE = re.compile(
    r"\b(?:Organization|Organisation|Association|Society|Institute|University|College|"
    r"Department|Faculty|Center|Centre|Committee|Council|Group|Consortium|Network|"
    r"Agency|Office|Bureau|Board|Panel|Project|Foundation|Corporation|Company|Inc|Ltd|"
    r"Press|Publishing|Ministry|Government|Laboratory|Commission|Federation|Union|"
    r"Academy|WHO|UNESCO|ISO)\b", re.I)


def _finalize_en_entry(e: RefEntry) -> RefEntry:
    """英文人年制条目**出口一次**：按出版信息特征重判类型 + author 统一倒装（T13）。

    author 倒装走 T12 同源 helper（`normalize_authors → normalize_author_field`）；
    **机构/组织作者不倒装**（反例：`World Health Organization` 保持原样）。
    """
    from . import footref
    from .bibnorm import normalize_author_field
    from .footnotes import normalize_authors

    etype, _reason = footref.classify_entry_type(_en_entry_fields(e), raw=e.raw or "")
    e.kind = _KIND_BY_ETYPE.get(etype, "M")
    if e.author and not _EN_ORG_RE.search(e.author):
        e.author = normalize_author_field(normalize_authors(e.author))
    return e


def refs_to_bib(refs: list[RefEntry]) -> str:
    """把条目列表转成 .bib 文本。

    按用户要求尽力补齐期刊的卷号/期号/页码、书籍的出版地/出版商。
    解析不出来的字段**不编造**，整条塞进 note 留给人工核对。
    """
    from .bibnorm import detect_userf

    out = ["% 由 Word 参考文献自动生成，请核对类型与字段。", ""]
    for e in refs:
        etype = TYPE_MAP.get(e.kind, "book")
        out.append(f"@{etype}{{{e.key},")
        fields: list[tuple[str, str]] = []
        if e.author:
            fields.append(("author", _bib_author(e.author)))
        if e.title:
            fields.append(("title", escape_bib_value(e.title)))

        # rest 常以「. 」开头（`. Mind, 85(337): 112-119.`），必须先剥掉，
        # 否则下面的期刊正则匹配不到——实测所有期刊条目都因此丢了卷/期/页。
        rest = re.sub(r"^[.。]\s*", "", e.rest.strip())
        jm = _EN_JOURNAL_RE.match(rest)
        if jm is None:  # 无卷号形态：`Mind 85: 112-119`
            jm = _EN_JOURNAL_RE2.match(rest)
        if jm and etype == "article":
            gd = jm.groupdict()
            fields.append(("journal", escape_bib_value(gd["journal"].strip())))
            # 中文期刊常写成「逻辑学研究, 2022(5): 38-50」——括号前那个数是**年份**
            # 而不是卷号。若它等于条目的年份，就当卷号不填。
            vol = gd.get("volume") or ""
            if vol and re.fullmatch(r"(19|20)\d{2}", vol) and vol == e.year:
                vol = ""
            if vol:
                fields.append(("volume", vol))
            if gd.get("number"):
                fields.append(("number", gd["number"]))
            if gd.get("pages"):
                fields.append(("pages", gd["pages"].replace("-", "--")))
            fields.append(("year", gd.get("year") or e.year))
        else:
            # ⚠️ `pub` / `addr` 仍是「**note 判据**」用的那一份（`_split_publisher`），
            #    一个字没动 —— 这样 book/article 的 note 行为逐字不变（案例 81）。
            pub, addr = _split_publisher(rest)
            if etype == "book":
                if addr:
                    fields.append(("address", escape_bib_value(addr)))
                if pub:
                    fields.append(("publisher", escape_bib_value(pub)))
            elif etype == "incollection":
                # ── 案例 81：`[C]//` 条目已解析到手的字段被静默丢掉 ──────────
                # 旧字段表里从来没有 booktitle/editor，而 address/publisher 又被
                # `etype == "book"` 挡住，于是 `Tarski2019` 的
                # `马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.` 解析出来的
                # 商务印书馆/北京一个字都没写（回读校验报告却还写「❗必补：booktitle」，
                # 而它下一行贴出的源稿原文里明明有 —— 与第 66/68 条同一型事故）。
                # 口径见 `_incollection_book_editor()` 的 docstring：宁缺勿编。
                bt, ed = _incollection_book_editor(rest)
                cpub, caddr = _incollection_pub_addr(rest)
                if bt:
                    fields.append(("booktitle", escape_bib_value(bt)))
                if ed:
                    fields.append(("editor", _bib_author(ed)))
                # 字段序照本项目规范：title → booktitle → editor → publisher → address
                # （见 docs/RULES.md 与 slcn/aibib.py 顶部的固定顺序）。
                if cpub:
                    fields.append(("publisher", escape_bib_value(cpub)))
                if caddr:
                    fields.append(("address", escape_bib_value(caddr)))
            fields.append(("year", e.year))
            # P0-2 兜底：@article 但期刊卷期页没解析出来时，把 rest 原文塞进 note，
            # 不再静默丢（期 87 sub5 有 18 条 pages 丢失且无任何痕迹）。
            if etype == "article" and not any(k == "journal" for k, _ in fields) and rest:
                fields.append(("note", escape_bib_value(rest)))
            # ⚠️ 案例 81 的**第二半：本轮故意不动**（明写在此，不悄悄丢）。
            #     `rest` 里那些「版次 / 中译加注」现在仍只在这条 note 判据下兜底：
            #     `pub or addr` 一真就 `note = ""`，于是
            #       · `Haack1974` 的 `Second, expanded edition: … 1996`（@book）
            #       · `Quine1970` 的 `2nd edition, Cambridge, MA: Harvard … 1986`（@book）
            #       · `Williamson2007` 的 `Second and enlarged edition, … Wiley Blackwell, 2021`（@book）
            #       · `Haack1978` / `Kripke2024` / `Maddy2002` 括号里的中译注记（@book/@article）
            #     全都进不了 bib —— 与缺陷所述一致。**本轮不修**的理由是硬的：
            #     把它们放进 note 必须改 @book / @article 两个分支的输出，而本轮验收
            #     明令「`@book` 的既有输出逐字不变」「同一条目已有的字段值不许改坏」，
            #     全库量噪声也要求「改前有、改后没有 = 0、值被改坏 = 0」。
            #     这是一个**尚未修**的数据损失（不是已修），独立于第 81 条的
            #     `[C]//` 字段丢失；留给下一步（改 note 口径时需同步 book/article 分支
            #     与冻结基线 `out/docx_audit.csv` 的字段统计）。
            note = rest if not (pub or addr) else ""
            if note:
                fields.append(("note", escape_bib_value(note)))

        uf = detect_userf(e.author, e.title)
        if uf:
            fields.append(("userf", uf))

        width = max((len(k) for k, _ in fields), default=4)
        for k, v in fields:
            out.append(f"  {k.ljust(width)} = {{{v}}},")
        if out[-1].endswith(","):
            out[-1] = out[-1][:-1]
        out.append("}")
        out.append("")
    return "\n".join(out)


def _split_publisher(rest: str) -> tuple[str, str]:
    """从 `出版地: 出版商` 里拆出两者。中文条目常见「北京: 商务印书馆」。"""
    m = re.search(r"([^:：,，]{2,30})[:：]\s*([^,，.。]{2,60})", rest)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    pub = _extract_publisher(rest)
    return pub, ""


def _bib_author(a: str) -> str:
    """规范成 `and` 分隔后**倒装**，再转义。

    ⚠️ 不能原样照抄：源稿常见「逗号 + and」混用（`J. P. Dillard, C. Segrin and J. M. Harden`），
    而 BibTeX 里**逗号是倒装符**，照抄会编译成「C. S. J. P. Dillard and J. M. Harden」。
    倒装名（`Haack, S.`）由 `normalize_authors` 识别并保持原样。

    ⚠️ 顺序不能反：**先** `normalize_authors`（拆「逗号 + and」混用、统一 `and`），
    **再** `normalize_author_field`（英文一律倒装成 `姓, 名`）。反过来的话，混用串里
    有逗号会被倒装器当成「已经倒装」而不动，拆完仍是名前姓后（实测就是这么漏的）。
    """
    from .bibnorm import normalize_author_field
    from .footnotes import normalize_authors

    return escape_bib_value(normalize_author_field(normalize_authors(a)))


def _extract_publisher(rest: str) -> str:
    m = re.search(r"[:：]\s*([^:：,，\.。]+(?:出版社|Press|University|Publishing|Blackwell|Routledge|商务印书馆|人民出版社|译文出版社|教育出版社)[^,，\.。]*)", rest)
    return m.group(1).strip() if m else ""


# ── 案例 81：`@incollection`（`[C]//`）的 booktitle / editor / 出版信息 ──────
#
# `[C]//` 是 GB/T 7714 的「析出文献」形态：
#     析出文献主要责任者. 析出文献题名[C]// 专著主要责任者. 专著题名[M]. 出版地: 出版者, 年: 页.
# 实测（2026年第5期(90)/10 Bo_Chen）：
#     塔斯基. 2019. 真理的语义概念和语义学的基础[C]// 马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.
# 解析器把 rest 拿全了（`马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.`），
# 旧字段表却从来没有 booktitle/editor，而 address/publisher 又被 `etype == "book"` 挡住
# —— 于是**已经解析到手**的「商务印书馆 / 北京」被静默丢掉（`refs_to_bib` 里有接线）。
# 回读校验报告因此在这条上写「❗必补：booktitle」，而它下一行贴出的源稿原文里明明写着书名
# —— 与第 66 / 68 条同一型事故：喂给 AI 的本来就是残缺件。
#
# 三条判据（**宁缺勿编**，第 61 条口径「编错的字段比空缺更糟」）：
#   ① booktitle 只认两种**结构清楚**的形态，认不出就不写：
#        · 有 `[M]`/`[A]`/`[D]` 收尾 → 取 `[C]//` 到该标记之间、最后一个句读后的那一段；
#        · 源稿**明确**写了编者标记 → 取标记之后到下一个逗号/句号前的那一段。
#   ② editor 只在源稿明确给出编者标记（`(eds.)` / `（编）` / `主编`）时才写；
#      `[C]//` 后那个不带标记的「专著主要责任者」（如 `马蒂尼奇`）**不写**
#      —— 它按体例也可能是专著的作者，分不清就不猜。
#   ③ 出版地/出版者另走 `_incollection_pub_addr()`：通用 `_split_publisher()` 在这两种
#      真实形态上会给出**错值**（见该函数 docstring），所以只认能确定的那一对。
_BT_STOP_RE = re.compile(r"\[\s*[MAD]\s*\]")
# 源稿里**明确**的编者标记：`(eds.)` / `(ed.)` / `(editors)` / `（编）` / `主编`
_EDITOR_MARK_RE = re.compile(r"\(\s*eds?\.?\s*\)|\(\s*editors?\s*\)|（\s*编\s*）|主编")
# 版次 / 卷次等「不是书名」的片段
_BT_NON_TITLE_RE = re.compile(
    r"^(?:第\s*[\d一二三四五六七八九十]+\s*版"
    r"|(?:\d+\s*)?(?:st|nd|rd|th)?\s*ed(?:ition)?\.?"
    r"|(?:revised|enlarged|expanded|second|third|fourth|fifth|first)\s+ed(?:ition)?.*"
    r"|\d+\s*卷)$",
    re.I,
)
# 出版信息候选：冒号前是地名、冒号后是出版者（沿用 `_split_publisher` 的正则形状）
_PUB_COLON_RE = re.compile(r"([^:：,，]{1,40})[:：]\s*([^,，.。\[\]]{2,60})")


def _bt_candidate_ok(s: str) -> bool:
    """一个片段够不够格当书名（案例 81）：够格才写，拿不准返回 `False`。"""
    s = s.strip(" \t\u3000.。，,;；、")
    if not (1 <= len(s) <= 120):
        return False
    # 出版信息 / URL / 类型标记 / 逗号冒号混进来 = 切错了，不是书名
    if re.search(r"[\[\]{}]|https?|://|[,，;；:：]", s):
        return False
    if _BT_NON_TITLE_RE.match(s):
        return False
    if re.search(r"译|著|主编", s):        # 译者/著者标记 = 切到了责任者，不是书名
        return False
    if len(re.findall(r"[\u4e00-\u9fff]", s)) >= 2:
        return True
    return bool(re.search(r"[A-Za-z]{4,}", s))


def _editor_candidate_ok(s: str) -> bool:
    """一个片段够不够格当编者串（案例 81）。"""
    s = s.strip(" \t\u3000.。，,;；、")
    if not (2 <= len(s) <= 120):
        return False
    if re.search(r"[\[\]{}]|https?|://|\d|译|著", s):
        return False
    if len(re.findall(r"[\u4e00-\u9fff]", s)) >= 2:
        return True
    return bool(re.search(r"[A-Za-z]{2,}", s))


def _incollection_book_editor(rest: str) -> tuple[str, str]:
    """从 `[C]//` 之后的 `rest` 里取 `(booktitle, editor)`（案例 81）。

    只认**结构清楚**的两种形态，其余一律 `("", "")`（宁缺勿编）：

      `马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.`
          → booktitle=`语言哲学`、editor=``（`马蒂尼奇` 没有编者标记，不写）
      `Zalta, E N. & Nodelman, U (eds.), The Stanford Encyclopedia of Philosophy, https:…`
          → booktitle=`The Stanford Encyclopedia of Philosophy`、
            editor=`Zalta, E N. & Nodelman, U`（`(eds.)` 是明确编者标记）

    拿不准的一律不写，例如 `马蒂尼奇 语言哲学[M]`（无句读分隔，分不清责任者与书名）、
    `马蒂尼奇. 语言哲学. 第2版[M]`（末段是版次）。
    """
    stop = _BT_STOP_RE.search(rest)
    zone = (rest[: stop.start()] if stop else rest).strip(" \t\u3000")
    if not zone:
        return "", ""
    # `[C]// 编者.[M]`：句读之后没有书名（形态残缺）→ 不写，别把责任者当书名
    if stop and re.search(r"[.。]\s*$", zone):
        return "", ""

    # ① 源稿**明确**写了编者标记 → 编者 = 标记前，书名 = 标记后到下一个句读
    em = _EDITOR_MARK_RE.search(zone)
    if em:
        ed = zone[: em.start()].strip(" \t\u3000.。，,;；、")
        if _editor_candidate_ok(ed):
            tail = zone[em.end():].strip(" \t\u3000.。，,;；、")
            bt = re.split(r"[,，;；.。]", tail)[0].strip(" \t\u3000.。，,;；、")
            return (bt if _bt_candidate_ok(bt) else ""), ed
        return "", ""

    # ② `专著主要责任者. 专著题名[M]`：书名是最后一个句读后的那一段
    if stop:
        segs = [s for s in (x.strip(" \t\u3000.。，,;；、") for x in re.split(r"[.。]", zone)) if s]
        if not segs:
            return "", ""
        last = segs[-1]
        # 只有一段、且中间夹着空白（`甲 乙`）→ 分不清责任者与书名，不写
        if len(segs) == 1 and re.search(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", last):
            return "", ""
        return (last if _bt_candidate_ok(last) else ""), ""

    # ③ 既没有 `[M]`/`[A]`/`[D]`、也没有编者标记 → 拿不准，不写
    return "", ""


def _incollection_pub_addr(rest: str) -> tuple[str, str]:
    """`@incollection` 的 `(出版者, 出版地)`（案例 81）。认不出返回 `("", "")`。

    为什么不能直接用 `_split_publisher()`（它服务 book 分支，逐字不能动）——两条实测：
      · `…牟博, 译. 北京: 商务印书馆.` → 通用正则把「译. 北京」当地址
        （`[^:：,，]` 被逗号截断，`译. 北京` 整段当了地名）；
      · `…, https://plato.stanford.edu/…` → 通用正则拿 `https` 当地址、`//plato` 当出版者。
    所以这里只认第一对「冒号前是干净地名、冒号后是干净出版者」，并把地址收窄到最后一个
    句读之后（`译. 北京` → `北京`）；`://` 直接跳过。
    """
    for m in _PUB_COLON_RE.finditer(rest):
        raw_addr, pub = m.group(1), m.group(2).strip(" \t\u3000.。")
        if pub.startswith("//") or "http" in pub.lower():
            continue                        # URL 的冒号，不是出版信息
        addr = re.split(r"[.。]", raw_addr)[-1].strip(" \t\u3000,，、;；")
        if not (1 <= len(addr) <= 30) or not (2 <= len(pub) <= 60):
            continue
        if re.search(r"译|著|编|校|https?|www|\d", addr):
            continue                        # 「译. 北京」「2012」这类不是地名
        if re.fullmatch(r"[\d\-–—\s]+", pub):
            continue                        # `2012: 812-817` 是年:页，不是出版信息
        return pub, addr
    return "", ""


# ── 文档解析 ──────────────────────────────────────────────────────────────

_HEAD1 = re.compile(r"^[一二三四五六七八九十]+[、\.]\s*")
# 访谈体/无顿号稿的「一 标题」型（87-9 实测：`一 非形式逻辑` / `二 修辞论证理论`）。
# 与 `一、` 同族，但作者用空格分隔；只在**短行、无句末标点、后接汉字标题**时认，
# 避免把正文里的「一 个…」误判成标题（判据在 ⑥ 的 branch 里）。
_HEAD_CN_SPACE = re.compile(r"^([一二三四五六七八九十]{1,2})\s+(?P<t>[\u4e00-\u9fff][^。！？；]{1,38})$")
_HEAD2 = re.compile(r"^（[一二三四五六七八九十]+）\s*")

# 新式中文标题：`1 引言` / `2 论证行动生成问题研究现状`（阿拉伯数字 + 空格，**不带点**）。
#
# ⚠️ 同一篇稿子里「标题」和「列表项」长得很像，必须分开：
#       [5]  '1 引言'                        ← 标题
#       [19] '1. 尚未形成对论证行动完整生成过程的统一解释。三条径路…'  ← 列表项（带点 + 整句）
#   实测 09 Jiawei_Chu：6 个节标题全是前者，旧规则只认「一、引言」那种老式编号，
#   于是标题原样留在正文里，产物里**一个 \section 都没有**（人工成品有 6 个）。
# ⚠️ 数字与标题之间**可以没有空格**：实测 2026_05_Jingru_Kou 写的是
#    `1可知性悖论的逻辑结构与严格性问题`（`4  结语` 那种带空格的也有），
#    旧规则要求 `\s+`，于是 6 个节标题只认出 1 个。
_HEAD_CN_ARABIC = re.compile(r"^\d{1,2}(?:\s+|(?=[\u4e00-\u9fff]))(?P<t>\S.*)$")
# `N. 标题`（阿拉伯数字 + **点**）—— 与 `N 标题` 同族，只是编号后多一个点。
# 实测 04 Bo_Chen：一篇 6 个小节全写成 `1. 莱布尼茨` / `5. 维特根斯坦`（人名小节），
# 旧判据那一句 `rest[:1].isupper()` 是为英文 `1. Introduction` 写的，于是 6 个标题
# 全留在正文里，产物**一个 \section 都没有**（人工成品 Bo_Chen.tex 里 6 个都是
# `\section` —— 所以它们是标题，不是正文里的名单）。
# ⚠️ 点后必须直接是**汉字**：英文 `1. Introduction` 仍走 _HEAD_EN1，行为逐字不变；
#    `N.M`（`2.2 标题`）也仍在 _HEAD_EN3 之前被挡住（点后是数字，这里不匹配）。
_HEAD_CN_DOT = re.compile(r"^\d{1,2}\.\s*(?P<t>[\u4e00-\u9fff]\S*)$")
# 数字后紧跟这些字的多半不是标题（`20世纪…`、`2022年第6期…`），排除掉
_HEAD_CN_BAD_TAIL = ("年", "月", "日", "世纪", "年代", "页", "卷", "期", "个", "种", "位", "条")
_CN_HEAD_PUNCT = "。；，、：；！？,;:!?"


def _is_cn_numbered_heading(t: str) -> bool:
    """`N 标题` 里 N 后面那截像不像标题（而不是句子或列表项）。"""
    body = t.strip()
    if not (2 <= len(body) <= 40):
        return False
    if any(ch in body for ch in _CN_HEAD_PUNCT):
        return False
    if body[-1].isdigit():
        return False
    if body.startswith(_HEAD_CN_BAD_TAIL):
        return False
    # 至少要有一个汉字或字母，避免把「3 个」这类残句当标题
    return any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in body)


def _is_cn_dot_heading(t: str) -> bool:
    """`N. 中文标题` 的形态判据 —— **复用** `_is_cn_numbered_heading`，不另写一套。

    ⚠️ 这一层就是防「编号列表项被当成标题」的闸门。正文里与标题同形的列表项实测有：
        `1. 尚未形成对论证行动完整生成过程的统一解释。三条径路…`（09 Jiawei_Chu）
        `1.命题（宗）：“此山有火。”`（08 Ran_Ren 的因明五支）
        `1. 第一页第二段，提出周延问题的文献建议列出原始文献，…`（04 Yanjing_Zhang）
    它们全部被「2–40 字 + 无句读 + 不像 20 世纪」挡住——判据与 `N 标题` 那条路径
    是**同一套**，所以两种写法的误判边界一致，不会出现「同样一句话在 `1 引言` 里
    挡住了、在 `1. 引言` 里却放行」。
    """
    m = _HEAD_CN_DOT.match(t)
    if not m:
        return False
    rest = m.group("t")
    # 与 _HEAD_EN1 分支同一条：句中带句点的多半是文献条目/编号说明，不是标题
    return "." not in rest and _is_cn_numbered_heading(rest)

# 结构标记必须**中英双认**。实测 01 Wenfang_Wang 是英文稿，用的是
# `Abstract:` / `Keywords:`；只认中文标记会让正文区直接取空（相似度掉到 1%）。
#
# ⚠️ 段首允许有空白（`\s` 在 Python 3 里本来就含全角空格 U+3000，这里显式写出来只是
#    让判据一眼可读）：实测 2023年第5期(72) 03 Ziming_Song 的摘要写成
#    `    摘要：决策论哲学中的…`（段首四个半角空格），旧规则要求标签紧贴段首，
#    于是中文摘要 fail。**只放宽段首空白**——`^` 仍锚定段首，句中出现的
#    「摘要/Abstract」一律不算标签（这是这条正则的语义，不是附加条件）。
_ABS_LEAD = r"^[\s\u3000]*"
# ⚠️ 「摘要」与正文之间**可以没有分隔符**（实测 2025年第6期(85) 04 YingJin_Xu 的汉语
#    摘要写成 `摘要理想的人工通用智能系统…`，全库仅此 1 处），所以不能一律要求冒号；
#    但也不能无条件认：`摘要式阅读`/`摘要性…`/`摘要化…` 里的「摘要」只是**构词语素**，
#    不是标签（全库实测这三个后缀字在标签位置 0 命中，这条纯粹是反例防线；
#    万一认错，代价是 fail——不会把正文写进 `\myabstractCN`）。
_ABS_RE = re.compile(_ABS_LEAD + r"(?:摘\s*要(?![式性化])|Abstract)\s*[:：]?\s*", re.I)
# 关键词标签**不**跟着放宽段首空白：关键词有 7 篇 fail，不在本轮验收范围内，而
# `kw_blocks` 里「先出现的空正文块」会把后面有正文的块挡掉（`next()` 取第一个 cjk 块），
# 放宽它可能凭空造出 ok→fail。摘要这条路另写了「向下取正文」的判据（见 _abs_body_cn）。
_KW_RE = re.compile(r"^(?:关键词|Keywords?|Key\s*words?)\s*[:：]?\s*", re.I)
_REF_HEADS = {"参考文献", "references", "参考书目", "bibliography", "works cited"}

# ── 「摘要」标签独占一段：向下取正文（判据见 _abs_body_cn）─────────────────
#
# 实测 11 篇源稿把标签单独写一段（`摘 要 ` / `摘    要` / `摘  要` / `摘要` / `摘要：`），
# 正文在**下一段**。旧实现 `_label_body` 对中文标签**故意**不向下看（怕把中英两套摘要
# 里那套英文写进 `\myabstractCN`），于是这些稿子的 `\myabstractCN` 是空的
# （普查 abstract_cn fail 16 篇里的 11 篇）。
# 判据（宁 fail 不误判，绝不凭空造正文）：
#   ① 空段跳过，最多取 _ABS_LOOKAHEAD 段，且最多往后看 _ABS_LOOKAHEAD*2 段；
#   ② 只取**含汉字**的段落——语言判据与 _label_body / parse_tail_metadata ① 同一套。
#      实测 2026年第1期(86) 08 LingXin_Cai 的补充材料是 `摘要：` 后跟**英文**正文
#      （那一段在「英文标题及摘要」小标题之下，是英文块的标签），这条专门挡它抢中文槽；
#   ③ 撞上下一个标签（关键词/摘要/题名/作者/单位/邮箱…）就收手，绝不把关键词吞成摘要；
#   ④ 每段都要像正文（够长 + 有成句的标点）——节标题（`一、引言`）不是摘要正文。
_ABS_LOOKAHEAD = 3
_ABS_MIN_CHARS = 20
_ABS_END_CHARS = "。；！？，,.;!?"
# ③ 的「下一个元数据标签」清单（摘要/关键词那两类由 _ABS_RE/_KW_RE 判，不重复）
_META_LABEL_RE = re.compile(
    _ABS_LEAD + r"(?:标题|题目|题名|副标题|作者|单位|所属单位|邮箱|电子邮箱|通讯作者|"
    r"通信作者|作者简介|作者信息|中图分类号|文献标识码|基金项目|收稿日期|"
    r"Title|Author|Affiliation|E-?mail)\s*(?:[:：]|$)", re.I)

# 低于这个字符数就不可能是正文（例如剩下的只是一张缩写对照表）
MIN_BODY_CHARS = 1500

# 英文稿的章节编号：`1. Title` 是节，`1）Title` 是全角右括号的子节。
# 实测 02 Lu_Jiang：docx 用 `1. Modern Approaches…`，人工成品是
# `\section{Modern Approaches…}`（去掉编号）。识别不出来会让整篇 token 对齐错位——
# 一个缺失的 \section 能凭空造出 1400 多个假的「实质差异」。
_HEAD_EN1 = re.compile(r"^\d{1,2}\.\s+(?P<t>\S.*)$")
_HEAD_EN2 = re.compile(r"^\d{1,2}）\s*(?P<t>\S.*)$")
# 第 121 条（期 88 / 02 Lu_Jiang 真稿）：英文子标题写 `2) Problems with …`（半角右括号）。
_HEAD_EN2_HALF = re.compile(r"^\d{1,2}\)\s*(?P<t>[A-Z].*)$")
_HEAD_EN3 = re.compile(r"^\d{1,2}\.\d{1,2}\s+(?P<t>\S.*)$")  # 1.2 Title

# 元数据块只可能出现在开头这么多段之内
META_SCAN = 20

# ── 副标题 / 文末中英元数据块 ─────────────────────────────────────────────
#
# 实测 09 Jiawei_Chu（2026年第5期）：原稿结构是
#     0 论证行动如何生成？          ← 题名
#     1 ——一个基于广义论证理论的生成模型 ← 副标题（另起一段！）
#     2 鉏佳威                      ← 作者
#     …
#     96 参考文献 + 97–114 编号清单（`[N] 作者, 年, "题名", 刊名, 卷(期): 页`）
#     115–119 英文题名/副题/作者/摘要/关键词   ← 在文末！
#     120–125 邮箱 + 中英文所属单位
# 旧实现把「第 1 段」无条件当作者，于是副标题变成 `\myfirstauthor`；
# 元数据只扫前 20 段，于是英文题名/摘要永远是模板占位符 `Title`/`Abstract`；
# 参考文献只认人年制，于是那 18 条编号清单一条都没进来。
_SUB_RE = re.compile(r"^\s*(?:—{1,3}|–{1,2}|―{1,2}|-{1,2})\s*")
_EMAIL_RE = re.compile(r"(?:邮箱|E-?mail)\s*[:：]\s*([^\s，,；;]+@[^\s，,；;]+)", re.I)
# 裸邮箱（不带「邮箱：」标签）——实测 41 篇源稿的地址就这么写（README 第 45 条④）
_EMAIL_ANY_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_AFFIL_HEAD_RE = re.compile(r"(?:所属单位|单位|affiliation)", re.I)

# ── 文末英文元数据块的兜底定位（见 locate_tail_block）──────────────────────
#
# 实测 16 篇源稿把英文题名/摘要/关键词块写在**文末**，却没有「参考文献」标题
# （或标题排在英文块**之后**，01 Lili_Lu 就是这样）。旧实现要求 `ref_start >= 0`
# 才去回溯这个块，于是这些稿子的 `tail_start` 永远是 -1：英文题名 11 行、
# 英文摘要 15 行 fail，连写明的「电子邮箱：」都用不上（README 第 45 条④）。
#
# 兜底判据（宁 fail 不误判）：
#   ① 标签只在**文末 _TAIL_BLOCK_SCAN 段**里找。英文块贴着文末是它的定义；
#      正文中段的 `Abstract` 是双语稿的开头元数据，归开头那套管，不该被当文末块。
#   ② 标签必须**带冒号或独占一段**（`Abstract:` / `Abstract` / `Key words：…`），
#      所以文献条目里的 `Abstracts of…`、正文里的 `Abstract art in…` 命不中。
#   ③ 标签段本身不含汉字（英文块；中文那一套在开头，不走这里）。
_TAIL_BLOCK_SCAN = 30
_TAIL_LABEL_RE = re.compile(r"^(?:Abstract|Keywords?|Key\s*words?)\s*(?:[:：]|$)", re.I)

# ── 裸邮箱的行形态判据（见 collect_emails）────────────────────────────────
#
# 地址本体沿用 _EMAIL_ANY_RE，免得两处正则各自演化。实测地址的写法有六类：
#   ① 独占一段 `hooeyrong@163.com`；② 一行两个地址（06 Liwu Rong）；
#   ③ 作者信息/单位行内（03 Yuanchang_He 的 `作者简介：…563481898@qq.com`）；
#   ④ 作者名与地址同行（06 Zhixi_Chen `Zhixi Chen  202140007@oamail.gdufs.edu.cn`）；
#   ⑤ `Name (addr):`（05 Niu_Ju）；⑥ 短行以地址结尾（04 YingJin_Xu 的通信地址行）。
# 判据本身要求**完整地址**（`local@domain.tld`），所以正文里当符号用的 `@`
# 无论怎么贴都命不中；范围限制在 collect_emails 里。
_ADDR = _EMAIL_ANY_RE.pattern
# ① 整行就是一个地址（可带括号、可带尾随标点）
_ADDR_ALONE_RE = re.compile(
    r"^[\s\u3000]*[（(【\[<]?\s*(?P<e>" + _ADDR + r")\s*[）)】\]>]?[\s\u3000]*[。.，,；;]?$")
# ② 整行只有地址（空白/顿号/分号分隔多个）
_ADDRS_ONLY_RE = re.compile(
    r"^[\s\u3000]*(?:" + _ADDR + r"[\s\u3000]*[/、;；,，]?[\s\u3000]*)+$")
# ③「作者名 + 地址」同行且地址在行尾（前缀还要过 _looks_like_name_line）
_NAME_ADDR_RE = re.compile(
    r"^(?P<name>.{1,40}?)[\s\u3000]+(?P<e>" + _ADDR + r")[\s\u3000]*$")
# ④ MDPI 式 `Name (addr):`
_PAREN_ADDR_RE = re.compile(
    r"(?<=[A-Za-z\u4e00-\u9fff])\s*[（(]\s*(?P<e>" + _ADDR + r")\s*[）)]\s*[:：]")
# ⑤ 行内有这些词 = 作者信息/单位行，行内**第一个**地址就是作者邮箱
_AUTHOR_MARK_RE = re.compile(
    r"作者信息|作者简介|通讯作者|通信作者|Corresponding|所属单位|单位|"
    r"[Uu]niversity|College|Institute|Department|大学|学院|研究院|研究所")
# 地址后常紧跟中文句号（实测 `chi_cr@qq.com。`、`15135215246@163.com。`）：_EMAIL_RE
# 的字符类没排除它，值会带脏尾，所以取值一律先剥掉包裹括号与尾标点。
_TRAIL_PUNCT = "。．.，,；;：:）)】」》>"
# 模板占位符不是真邮箱（实测 02 Zhiguang_Zhao 的 `xxx@xxx.xxx`）：宁可不填，
# 也不能把假地址印进 `\myfirstemail`。黑名单**故意比任务要求宽**（连带拒
# `@example.*` / `@test.*` / `@yourdomain`）——全库 0 命中，宁 fail 不误判。
_PLACEHOLDER_EMAIL_RE = re.compile(
    r"(?:xxx|yyy|zzz|abc|your|name|example|test)@|"
    r"@(?:xxx|yyy|zzz|example|test|yourdomain)\b", re.I)
# 文末块没被定位（tail_start < 0）时，只在文末这几段里找邮箱
_EMAIL_TAIL_FALLBACK = 6

# ── 单位（机构）识别 ──────────────────────────────────────────────────────
#
# 旧实现只认「文末元数据块里带 `单位：` 标签的行」，于是全库 132 份源稿只拿到 1 份
# （docx_audit 第 44 条：单位 1/132、fail 54）。实测体例有四类：
#   ① 题目/作者附近独立一行：`浙江大学哲学学院`、`（兰州大学 哲学社会学院，兰州 730000）`
#   ② 作者与单位同一行：`焦雯雯 中山大学哲学系`、`甘肃政法大学 卢俐利`
#   ③ 文末中英元数据块（无标签）：`作者信息：周振忠 中山大学逻辑与认知研究所`
#   ④ 脚注里的作者信息：`作者信息：黄海婷  南京大学哲学学院`、`Jingru Kou  College of …`
#
# 精度优先（宁可 fail 不误判）：行内出现句号/分号/书名号/网址/日期/编号一律不算；
# 中文行按标点分组、组内剔掉「人名」「城市+邮编」这类碎片；西文行只剥行首作者名。
_AFFIL_WORD_RE = re.compile(
    r"大学|学院|研究院|研究所|实验室|科学院|社会科学院|哲学系|"
    r"Department|University|College|Institute|School\s+of|Academy|Center|Centre",
    re.I,
)
# 机构名开头词（西文剥作者名时用来刹车：`School of …` 的 `School` 不能当人名剥掉）
_AFFIL_HEAD_WORDS = {
    "department", "school", "institute", "college", "university", "academy",
    "center", "centre", "faculty", "laboratory", "lab", "chair", "division",
    "graduate", "program", "programme", "research", "group",
}
# 带标签的作者信息行：把标签剥掉再取单位
_AFFIL_LABEL_RE = re.compile(
    r"^\s*(?:作者信息|作者简介|作者单位|单位信息|所属单位|通信作者|通讯作者|单位|"
    r"中文|英文|Affiliation|affiliation)\s*[:：]\s*"
)
# 这些标签行本身就是题名/摘要/关键词，不是单位（实测踩到：
# `英文题目：A Historical Review … Huazhong University of Science and Technology …`）
_AFFIL_NEVER_RE = re.compile(
    r"^\s*(?:(?:英文|中文|English|Chinese)\s*)?"
    r"(?:题目|标题|题名|摘要|关键词|Abstract|Keywords?|Key\s+words?)\s*[:：.]", re.I
)
# 正文口吻的词：机构行里不会出现（`研究所` 的「研究」不算，故用整词列表）
_AFFIL_PROSE_RE = re.compile(
    r"作者|学者|笔者|认为|指出|提出|本文|我们|因此|然而|但是|例如|如果|参见|所谓|"
    r"探讨|表明|显示|值得|应该|可能|一般|通常|往往|不仅|而且|因为|所以|"
    r"关于|对于|通过|根据|介绍|说明"
)
_AFFIL_CITY_ZIP_RE = re.compile(r"^[\u4e00-\u9fff]{2,8}[\s\d]*\d{6}$|^\d{6}$|^[\u4e00-\u9fff]{2,6}$")
_AFFIL_LIST_ITEM_RE = re.compile(r"^\s*(?:\[\s*\d{1,3}\s*\]|\d{1,3}\s*[.、）)])")
# 中文单位行必须**以机构名起头、以机构后缀收尾**（`华中科技大学逻辑教育二十年回顾与展望`
# 这样的标题行因此被挡掉：它起头像机构、结尾是「展望」）。
_AFFIL_UNIT_START_RE = re.compile(
    r"^[\u4e00-\u9fff·]{2,10}(?:大学|学院|研究院|研究所|实验室|科学院|社会科学院|哲学系)"
)
# 机构名的收尾后缀。**只此一份**，两个正则共用：
#   · `_AFFIL_UNIT_END_RE`（`$` 锚定）——「整行/整段必须以机构名收尾」，历史口径；
#   · `_AFFIL_UNIT_END_ANY_RE`（不锚定）——案例 75 的「句内取最长的机构名」，
#     要在 `武汉大学哲学系教授` 里把机构名的**结束位置**找出来。
_AFFIL_UNIT_TAIL = r"大学|学院|研究院|研究所|实验室|科学院|社会科学院|学系|系|中心"
_AFFIL_UNIT_END_RE = re.compile(r"(?:" + _AFFIL_UNIT_TAIL + r")$")
_AFFIL_UNIT_END_ANY_RE = re.compile(r"(?:" + _AFFIL_UNIT_TAIL + r")")
# 案例 75：机构名后面常紧跟**职衔**（`武汉大学哲学系教授`、`武汉大学哲学学院博士生`）。
# 职衔**剥离**（单位字段是给排版用的，人工成品里没有职衔），只认下列**学术职衔**：
#   一级/二级/三级/四级/特聘/客座/兼职/终身/名誉/助理/副 + 教授|研究员|讲师|助教
#   博士|硕士（生）（研究生）（导师）| 博导 | 硕导
# **行政职务故意不收**（主任/院长/所长/处长/秘书…）。理由：实测 05 Guoping_Du 的作者简介
# 里有 `中国社会科学院智能与逻辑实验室主任`——那个实验室是作者**主持的平台**，
# 而他的既有单位（开头括号行）是「中国社会科学院大学哲学院 + 中国社会科学院哲学研究所」。
# 把「主任」也算职衔，就会凭空多出一个单位、把一个本来正确的值改坏。宁 fail 不误判。
_AFFIL_TITLE_TAIL_RE = re.compile(
    r"^(?:[（(][^）)]{0,16}[）)])?"                      # 可选的整体注记「（博士生导师）」
    r"(?:一级|二级|三级|四级|特聘|客座|兼职|终身|名誉|助理|副)?"
    r"(?:(?:博士|硕士)(?:研究生|生)?(?:导师)?"
    r"|(?:教授|研究员|讲师|助教)"
    r"|博导|硕导)?"
    r"[）)】\]]?$"                                       # 切句残留的右括号
)
# 行首是这些词 → 是正文/引述，不是单位行
_AFFIL_NEVER_START_RE = re.compile(
    r"^(?:参见|参阅|引自|转引自|关于|对于|本文|作者|学者|根据|通过|例如|如果|所谓|"
    r"因此|然而|但是|我们|他们|其中|主要|一般|通常|往往|不仅|而且|以及|同时|"
    r"此外|后来|此后|当时|目前|如今|现在|已经|曾经|由于|为了|按照|依据|上述|以下|如下|详见)"
)
# 西文剥作者名时的刹车词：`See University of Chicago Press for details.` 的 `See`
# 不能被当成人名剥掉，否则剩下的串起头就是机构词、会被误收。
_LATIN_STOPWORDS = {
    "see", "cf", "ibid", "in", "for", "the", "a", "an", "this", "that", "these",
    "those", "on", "of", "to", "and", "or", "but", "however", "also", "as", "at",
    "by", "with", "from", "about", "according", "press", "journal", "vol", "no",
    "pp", "ed", "eds",
}
# 西文单位行必须以机构词起头（剥掉行首作者名之后）
_AFFIL_LATIN_START_RE = re.compile(
    r"^(?:Department|School|Institute|College|University|Academy|Center|Centre|"
    r"Faculty|Laboratory|Lab|Research|Graduate|Division|Chair|Program)\b",
    re.I,
)
# 英文单位的多行连接（末尾那个空格是 PDF 里的 1.88pt，见 README 第 40 条②）
AFFIL_EN_JOIN = "\\\\　\\hspace{1em}\\phantom{\\myfirstauthorEN}\\hspace{1em} "


def _clean_affil_line(line: str) -> str:
    """从一行文字里抽出单位名；不像单位行则返回空串。

    两道：
      **stage ①** `_clean_affil_line_strict` —— 历史口径的**整行**判据，一字不改。
      **stage ②** `_clean_affil_by_sentence` —— 案例 75 新增：整行被否掉之后，
      按 `，。；、` 切句、在**句内**找机构名。

    ⚠️ 顺序是「先 stage ①，它没结果才 stage ②」，这是为了守住一条不变量：
      **stage ① 本来能认出来的行，结果逐字不变。**
    因此本缺陷**只可能**让「原来是空串」的行变成非空（收益），不可能把既有值改掉或清掉
    （风险）；全库逐篇对照见 `tests/test_affil_from_bio.py` 的 ③ 组。
    """
    # 去 BOM / 零宽字符：Word 第一段常带 `\ufeff`（实测 2024_02_Kai_Qu 的脚注）
    t = _strip_zero_width(line or "")
    t = re.sub(r"\s+", " ", t.strip())
    if not t or len(t) > 160:
        return ""
    got = _clean_affil_line_strict(t)
    if got:
        return got
    return _clean_affil_by_sentence(t)


def _clean_affil_line_strict(t: str) -> str:
    """stage ①：**整行**判据（历史口径，案例 75 一个字都没动）。

    实测量的正例：
      浙江大学哲学学院 / 北京大学外国哲学研究所
      （兰州大学 哲学社会学院，兰州 730000） / 广东第二师范学院马克思主义学院 广州 510303
      焦雯雯 中山大学哲学系 / 甘肃政法大学 卢俐利 / 作者信息：周振忠 中山大学逻辑与认知研究所
      School of Philosophy and Social Development, Shandong University
      Jingru Kou   College of Philosophy, Nankai University
    实测量的反例（必须返回空串）：
      山西大学，2024年8月12-13日（会议信息，含日期）
      相关地址信息：For information, address State University of New York Press…网址：www…
      英文题目：A Historical Review … Huazhong University of Science and Technology …
      1. 尚未形成对论证行动完整生成过程的统一解释。（列表项）
      20世纪逻辑学在中山大学得到发展（以数字起头的正文）
      华中科技大学逻辑教育二十年回顾与展望（题名本身含机构词）

    ⚠️ 它是**坏掉的三道闸门**的所在地（案例 75 的根因）：
      ① 行内有 `。` → 整行 `return ""`（第 18 行）；
      ② 「作者简介」句里的单字碎片（`男`）落进 `_clean_affil_cjk` 的 `else: return ""`；
      ③ 机构名以职衔收尾（`…哲学系教授`）不满足 `_AFFIL_UNIT_END_RE` 的 `$`。
    """
    if not t or len(t) > 160:
        return ""
    if _AFFIL_NEVER_RE.match(t):
        return ""
    if not _AFFIL_WORD_RE.search(t):
        return ""
    # 句号/书名号/网址/日期/编号行一律不算；行尾的分号也是残句
    if re.search(r"[。．]|《|》|网址|https?://|www\.", t) or t.endswith((";", "；")):
        return ""
    if re.search(r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日", t):
        return ""
    if _AFFIL_LIST_ITEM_RE.match(t) or t[0].isdigit():
        return ""
    # 标签先剥（`作者信息：周振忠 中山大学…` 的「作者」不是正文口吻），再判行首词
    t = _AFFIL_LABEL_RE.sub("", t)
    if _AFFIL_NEVER_START_RE.match(t):
        return ""
    # 方括号/圆括号里的角色注记（`[通讯作者]`）不是单位
    t = re.sub(r"[\[（(]\s*(?:通讯作者|通信作者|责任编辑|corresponding author)\s*[\]）)]",
               "", t, flags=re.I).strip()
    if len(t) >= 2 and (t[0], t[-1]) in (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]")):
        t = t[1:-1].strip()
    if not t:
        return ""
    if _AFFIL_PROSE_RE.search(t):
        return ""
    return _clean_affil_cjk(t) if _has_cjk(t) else _clean_affil_latin(t)


def _clean_affil_by_sentence(t: str) -> str:
    r"""stage ②（案例 75）：整行否掉后，按句切分 + 在**句内**取机构名。

    缺陷（案例 75，2026_05_Bo_Chen 的 `word/footnotes.xml`）
    ------------------------------------------------------
    源稿脚注里明明写着单位：

        作者简介: 陈波，男，湖南常德人，武汉大学哲学系教授。电子邮箱：chen-bo@whu.edu.cn

    产物却写 `【未识别：第一作者单位，请手工填写】`。根因是 stage ① 的**整行连坐**，
    三道各自独立致命（已实测）：
        `_clean_affil_line_strict('作者简介: 陈波，男，湖南常德人，武汉大学哲学系教授。电子邮箱：…')` → ''
        `_clean_affil_line_strict('作者简介: 陈波，武汉大学哲学系')` → '武汉大学哲学系'  ← 去掉「男」就能解
        `_clean_affil_line_strict('作者简介: 陈波，男，武汉大学哲学系')` → ''           ← 留着「男」就整行丢
    即：**是「能解但判据太严」，不是「没解析出来」。**

    判据（按 `，,。．；;、` 切句，逐句独立判定；句内任何一条不过就丢**这一句**，不连坐）
    ------------------------------------------------------------------------------
      ⓪ 整行级闸门（题目/摘要/关键词标签、书名号/网址、日期、编号、正文口吻、超长）
         仍需先过。这里**故意重抄**而不抽公共函数：stage ① 是冻结的历史口径，
         抽函数会让「已能识别的行逐字不变」这条不变量难以验证。
         ⚠️ 只有 `。` 从整行级**降到句级**（那正是案例 75 的致命闸门①）；
         `《》` 与网址**必须留在整行级**——实测把它们也降级会放进正文段落与
         文献条目：`人文学院、公管学院、…等学院将《逻辑学》…纳入培养计划`、
         `桑木严翼（1874-1946），哲学家，东京帝国大学名誉教授。著有《哲学概论》…`
         （前者是正文里的单位罗列，后者是文献里被研究的学者，都不是作者单位）。
      ① 句内必须有机构词（`_AFFIL_WORD_RE`）：电话/邮编/籍贯/人名句因此天然不可达；
      ② 句内不许有正文口吻词（`本文/参见/认为…`）、书名号、网址、日期、邮箱；
      ③ 句首必须是 `_AFFIL_UNIT_START_RE` 形态，取**以机构后缀收尾的最长前缀**为单位名
         （`武汉大学哲学系教授` → `武汉大学哲学系`）；
      ④ 单位名后面**只允许**跟 `_AFFIL_TITLE_TAIL_RE` 的学术职衔（可剥离；行政职务不收，
         理由见该正则处的注释）。后面跟着别的东西就丢这一句——这是「宁 fail 不误判」的闸门：
         `…哲学系563481898@qq.com`（邮箱）、`…实验室主任`（行政职务）、`…大学任教`（残句）。
    多句命中就按出现顺序用 `\x00` 连接（与 `_clean_affil_cjk` 的多单位口径一致，
    由 `_affil_lines_to_units` 再拆开、剥作者名、按中/英分桶）。
    """
    if not t or len(t) > 160:
        return ""
    if _AFFIL_NEVER_RE.match(t):
        return ""
    if not _AFFIL_WORD_RE.search(t):
        return ""
    if re.search(r"《|》|网址|https?://|www\.", t):
        return ""
    if t.endswith((";", "；")):
        return ""
    if re.search(r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日", t):
        return ""
    if _AFFIL_LIST_ITEM_RE.match(t) or t[0].isdigit():
        return ""
    t = _AFFIL_LABEL_RE.sub("", t)
    if _AFFIL_NEVER_START_RE.match(t):
        return ""
    t = re.sub(r"[\[（(]\s*(?:通讯作者|通信作者|责任编辑|corresponding author)\s*[\]）)]",
               "", t, flags=re.I).strip()
    if len(t) >= 2 and (t[0], t[-1]) in (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]")):
        t = t[1:-1].strip()
    if not t:
        return ""

    out: list[str] = []
    for seg in re.split(r"[，,。．；;、]", t):
        s = seg.strip().lstrip("（(【[ \u3000").strip()
        if not s or not _AFFIL_WORD_RE.search(s):
            continue
        if _AFFIL_PROSE_RE.search(s):
            continue
        if re.search(r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日", s):
            continue
        if _EMAIL_ANY_RE.search(s):          # `作者简介：…563481898@qq.com` 不许当单位
            continue
        if not _AFFIL_UNIT_START_RE.match(s):
            continue
        end = 0
        for m in _AFFIL_UNIT_END_ANY_RE.finditer(s):
            if m.end() > end:
                end = m.end()                # 以机构后缀收尾的**最长**前缀
        if not end:
            continue
        # 机构名后面紧跟的圆括号：内容**不是**职衔时算机构名的一部分——
        # `中山大学哲学系（珠海）助理教授` → `中山大学哲学系（珠海）`
        # （人工成品 2024_05_HaoJian_Xiao 写的就是带（珠海）的）。
        # 内容是职衔时（`武汉大学哲学系（教授）`）留给下一步的职衔剥离。
        m = re.match(r"[（(]([^）)]{1,16})[）)]", s[end:])
        if m and not _AFFIL_TITLE_TAIL_RE.match(m.group(1)):
            end += m.end()
        unit, tail = s[:end].strip(" 　·"), s[end:].strip()
        if not unit or (tail and not _AFFIL_TITLE_TAIL_RE.match(tail)):
            continue
        if unit not in out:
            out.append(unit)
    return "\x00".join(out)



def _clean_affil_cjk(t: str) -> str:
    """中文行：按「，；、」分组（= 多个单位），组内剔掉人名与城市邮编碎片。"""
    groups: list[str] = []
    for g in re.split(r"[，,；;、]", t):
        frags: list[str] = []
        for f in re.split(r"[ \t\u3000]+", g.strip()):
            f = f.strip(" 　·")
            if not f:
                continue
            if _AFFIL_WORD_RE.search(f):
                if not _AFFIL_UNIT_START_RE.match(f):
                    return ""               # 机构词在中间（`…在中山大学…`）→ 不是单位行
                frags.append(f)
            elif _AFFIL_CITY_ZIP_RE.match(f):
                continue                    # 人名 / 城市 / 邮编 / 纯数字
            else:
                return ""                   # 有说不清的碎片 → 整行放弃（宁 fail 不误判）
        if frags:
            one = " ".join(frags)
            if not _AFFIL_UNIT_END_RE.search(one):
                return ""                   # 结尾不是机构后缀（`…回顾与展望`）→ 不是单位行
            groups.append(one)
    if not groups:
        return ""
    # 组内碎片是同一个单位的组成部分（`兰州大学 哲学社会学院`），用一个空格连接；
    # 组与组之间才是两个单位，交给调用方用 `\\ ` 分行。
    return "\x00".join(groups)


def _clean_affil_latin(t: str) -> str:
    """西文行：剥掉行首的作者名（`Jingru Kou  College of …`），其余原样保留。"""
    t = re.sub(r"^The\s+(?=[A-Z])", "", t)      # `The University of Hong Kong`
    words = t.split()
    n = 0
    # 最多剥 2 个词（`Given Family Inst…`）。剥更多会把校名开头的词吃掉——
    # 实测 2025_05_Wutai_Xia 的 `Shawotar Qinghai Minzu University, Research Center…`
    # 会被剥成 `University, Research Center…`（错的），所以宁可整行放弃。
    while n < 2 and n < len(words) - 1:
        w = words[n].strip(",.&;:。")
        if w.lower() in _LATIN_STOPWORDS:
            break
        if not re.fullmatch(r"(?:[A-Z][A-Za-z'’\-]*\.?|[A-Z]\.)", w):
            break
        if w.lower() in _AFFIL_HEAD_WORDS or _AFFIL_WORD_RE.search(w):
            break
        n += 1
    # 只剥**成对**的「名 姓」（≥2 个词）：单剥一个词会把 `Tibet University, Research Center…`
    # 剥成 `University, Research Center…`（实测 2025_05_Wutai_Xia），宁可整行放弃。
    if n >= 2:
        rest = " ".join(words[n:])
        if rest and _AFFIL_WORD_RE.search(rest):
            t = rest
    if not _AFFIL_LATIN_START_RE.match(t):
        return ""
    if t.endswith(".") and not re.search(r"\b[A-Z]\.$", t):
        t = t[:-1].strip()              # 脚注里的单位行常以句号收尾，不是缩写点
    return t


def _affil_same_unit(one: str, bucket: list[str]) -> bool:
    """同一单位的不同**空白写法**算同一个单位。

    案例 75 的连带修正：实测 2025_04_Yanjing_Zhang 的开头括号行写
    `（河北大学 哲学与社会学学院，河北 保定 071002）`（Word 两个 run，读出来带空格），
    文末作者简介写 `河北大学哲学与社会学学院`（不带空格）。单纯放宽判据会让新解出来的
    第二条并进列表，`\\myfirstaffiliation` 就印出**两行同一个单位**——把「改前非空」的值改坏。

    ⚠️ 只在**同一次调用内**按空白归一（`_affil_lines_to_units` 的桶）；跨调用（`collect_affiliations`
    的 `extend`）仍然是精确匹配——那是有意为之：`2024_02_Kai_Qu` 开头行与脚注行本就
    重复印着同一个单位，那是**历史行为**，本轮不许顺手改掉（改了就是一个「改前非空后变了」）。
    """
    key = re.sub(r"\s+", "", one)
    return any(re.sub(r"\s+", "", x) == key for x in bucket)


def _affil_lines_to_units(lines: list[str], names: list[str] | None = None) -> tuple[list[str], list[str]]:
    """把若干行整理成 (中文单位列表, 英文单位列表)，去重且保持出现顺序。

    去重按**空白归一**后的形态（见 `_affil_same_unit`）。
    """
    names = names or []
    cn: list[str] = []
    en: list[str] = []
    for ln in lines:
        got = _clean_affil_line(ln)
        if not got:
            continue
        for one in got.split("\x00"):
            one = _strip_author_names(one.strip(), names)
            if not one:
                continue
            bucket = cn if _has_cjk(one) else en
            if not _affil_same_unit(one, bucket):
                bucket.append(one)
    return cn, en


def _strip_author_names(one: str, names: list[str]) -> str:
    """剥掉**粘在单位上的作者名**。

    实测 2026_05_Zixin_Luo 原稿写的是 `雒自新西安交通大学马克思主义学院`
    （Word 里是两个 run，纯文本读出来没有分隔符），不剥会得到带人名的单位。
    只用**已知作者名**去剥（doc.author），所以不会误伤 `南开大学哲学院` 这种。
    """
    for nm in names:
        if not (2 <= len(nm) <= 4) or not one.startswith(nm):
            continue
        if _AFFIL_WORD_RE.search(nm):
            continue          # 名字片段里带「大学/学院」→ 是校名的一部分，不能剥
        if len(nm) == 2 and nm[0] not in SURNAME_PINYIN:
            continue          # 两字前缀必须是常见姓氏，避免把「南开」「中国」当人名
        rest = one[len(nm):].strip()
        if not rest:
            continue
        if _has_cjk(one) and _AFFIL_UNIT_START_RE.match(rest):
            return rest
    return one


def _affil_block_hits(lines: list[str], min_run: int = 2) -> list[str]:
    """从「连续若干行都是单位行/人名行/邮箱行」的块里取单位。

    实测两类来路：① 英文摘要之后的作者信息块
    （2022_06A_Zhaoqing_Xu：`Xu Department of … / 邮箱 / Ouyang Department of … / 邮箱`）；
    ② 脚注里的作者信息（`Jingru Kou  College of … / 邮箱 / …`）。
    正文脚注里顺口提到某个大学不会形成这样的连续块，因此不会被误收。
    """
    out: list[str] = []
    run_hits: list[str] = []
    run_len = 0

    def flush() -> None:
        nonlocal run_hits, run_len
        if run_len >= min_run:
            out.extend(run_hits)
        run_hits, run_len = [], 0

    for ln in lines:
        ln = ln.strip()          # Word 里邮箱段常以全角空格收尾/起头（实测 Zhaoqing_Xu）
        got = _clean_affil_line(ln)
        if got or _looks_like_name_line(ln) or _EMAIL_ANY_RE.fullmatch(ln.rstrip(".")):
            run_len += 1
            if got:
                run_hits.append(got)
        else:
            flush()
    flush()
    return out


def _affil_head_zone(plains: list[str]) -> range:
    """开头元数据区：题名之后 → 第一处摘要/关键词标签（没有标签时取前 7 段）。"""
    first = next((i for i in range(1, min(len(plains), META_SCAN))
                  if _ABS_RE.match(plains[i]) or _KW_RE.match(plains[i])), -1)
    end = first if first >= 0 else min(len(plains), 7)
    return range(1, max(end, 1))


def _looks_like_name_line(t: str) -> bool:
    """整行就是一个（或几个）人名——脚注「无标签但整条都是作者信息」判据用。"""
    t = t.strip()
    if not t or len(t) > 40:
        return False
    if re.fullmatch(r"[\u4e00-\u9fff·]{2,4}(?:[ 、,，]{1,2}[\u4e00-\u9fff·]{2,4})*", t):
        return True
    toks = [x for x in re.split(r"[ \u3000]+", t) if x]
    return bool(toks) and all(
        re.fullmatch(r"[A-Z][A-Za-z'’\-]*\.?|[A-Z]\.", x) for x in toks
    )


def collect_affiliations(
    plains: list[str], tail_start: int, fn_texts: list[str], names: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """收集单位：开头元数据区 + 摘要后的作者信息块 + 文末元数据块 + 脚注（README 第 45 条）。"""
    hz = _affil_head_zone(plains)
    lines = [plains[i] for i in hz]
    if tail_start >= 0:
        lines.extend(plains[tail_start:])
    cn, en = _affil_lines_to_units(lines, names)

    # 摘要之后的作者信息块（连续 ≥2 行都是单位/人名/邮箱才收）
    after = [plains[i] for i in range(hz.stop, min(len(plains), hz.stop + 16)) if plains[i].strip()]
    more_cn, more_en = _affil_lines_to_units(_affil_block_hits(after), names)
    cn.extend(x for x in more_cn if x not in cn)
    en.extend(x for x in more_en if x not in en)

    for text in fn_texts:
        if not text.strip():
            continue
        fn_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not fn_lines:
            continue
        # 带标签（作者信息：/作者简介：/单位：）的一行就够；没有标签的要连续 ≥2 行。
        labeled = any(_AFFIL_LABEL_RE.match(ln) for ln in fn_lines)
        hits = _affil_block_hits(fn_lines, min_run=1 if labeled else 2)
        if not hits:
            continue
        more_cn, more_en = _affil_lines_to_units(hits, names)
        cn.extend(x for x in more_cn if x not in cn)
        en.extend(x for x in more_en if x not in en)
    return cn, en


def _norm_email(tok: str) -> str:
    """剥掉地址外包裹的括号与尾随标点（实测 `chi_cr@qq.com。`、`<taho@…>`）。"""
    return tok.strip().strip("<>（）()【】[]").rstrip(_TRAIL_PUNCT)


def _emails_in_text(text: str) -> list[str]:
    """按字符位置（= 文档顺序）收出一段/一条脚注里的地址，判据见上面的行形态。"""
    found: list[tuple[int, str]] = []
    for m in _EMAIL_RE.finditer(text):        # 带标签：邮箱：/E-mail:
        found.append((m.start(1), _norm_email(m.group(1))))
    base = 0
    for raw in text.splitlines():
        start = text.index(raw, base)         # splitlines 也会在 \x0b 等处断行，故重新定位
        base = start + len(raw)
        line = raw.strip()
        if "@" not in line:
            continue
        off = start + len(raw) - len(raw.lstrip())
        m = _ADDR_ALONE_RE.match(line)
        if m:
            found.append((off + m.start("e"), _norm_email(m.group("e"))))
            continue
        if _ADDRS_ONLY_RE.match(line):
            found.extend((off + t.start(), _norm_email(t.group(0)))
                         for t in _EMAIL_ANY_RE.finditer(line))
            continue
        if _AUTHOR_MARK_RE.search(line):
            t = _EMAIL_ANY_RE.search(line)
            if t:
                found.append((off + t.start(), _norm_email(t.group(0))))
                continue
        m = _NAME_ADDR_RE.match(line)
        if m and _looks_like_name_line(m.group("name")):
            found.append((off + m.start("e"), _norm_email(m.group("e"))))
            continue
        m = _PAREN_ADDR_RE.search(line)
        if m:
            found.append((off + m.start("e"), _norm_email(m.group("e"))))
            continue
        # ⑥ 短行以地址结尾（实测 04 YingJin_Xu：`Shanghai 200433, P. R. China yjxu@…`）。
        # 地址前出现句末标点说明这是正文句子，不收。
        if len(line) <= 80:
            m = re.search(r"(?P<e>" + _ADDR + r")[\s\u3000]*[。.]?$", line)
            if m and not re.search(r"[。；！？]", line[: m.start("e")]):
                found.append((off + m.start("e"), _norm_email(m.group("e"))))
    return [tok for _, tok in sorted(found, key=lambda x: x[0])]


def collect_emails(plains: list[str], tail_start: int, fn_texts: list[str]) -> list[str]:
    """收集作者邮箱：开头元数据区 + 文末元数据块 + 脚注（README 第 45 条④）。

    旧路径（parse_tail_metadata 的 `邮箱：` 标签）只在「文末块被定位」时可用，而实测
    41 篇 fail 源稿里 25 篇的地址只在脚注、12 篇文末块根本没被定位（`tail_start = -1`，
    其中 4 篇连标签都白写了）、11 篇在开头元数据区。

    ⚠️ 只扫这三段是**结构性防护**，不要为了多救几行放宽：
      · 参考文献区（ref_start..tail_start）与正文中段不在任何一段里——01 Jingru_Kou
        正文 9 处「现实世界 @」这种当符号用的 @ 因此不可达；
      · 表格（w:tbl）在 parse_docx 里就被滤掉、从不进 plains——04 Bo_Xianyu 表格
        单元格里的审稿人邮箱因此不可达；
      · 伪脚注（`w:type` = `separator` / `continuationSeparator`）由 read_footnotes 丢掉；
        而**真实的 `w:id="0"` 脚注会读到**（本库 23 篇是这种，正文确实引用它）——
        所以这里的输入口径与单位解析（`collect_affiliations`）完全一致。
    返回按文档顺序去重；第一个即第一作者（模板字段就是「第一作者邮箱」）。
    """
    lines = [plains[i] for i in range(1, min(len(plains), META_SCAN))]
    if tail_start >= 0:
        lines.extend(plains[i] for i in range(max(tail_start, META_SCAN), len(plains)))
    else:
        lines.extend(plains[max(META_SCAN, len(plains) - _EMAIL_TAIL_FALLBACK):])

    out: list[str] = []
    for text in lines + list(fn_texts):
        if not text.strip():
            continue
        for tok in _emails_in_text(text):
            if _PLACEHOLDER_EMAIL_RE.search(tok) or tok in out:
                continue      # 占位符宁可留空让人工填，也不能印出 xxx@xxx.xxx
            out.append(tok)
    return out


def _is_subtitle(t: str) -> bool:
    return bool(_SUB_RE.match(t))


def _title_with_break(title: str, subtitle: str) -> str:
    """题名 + 副标题写成 LaTeX 字段值（人工成品用 `\\\\` 断行）。"""
    if not subtitle:
        return title
    return f"{title}\\\\{subtitle}"


def _looks_like_person(t: str) -> bool:
    """英文作者行：短、无句末标点、无逗号（`Jiawei Chu`）。"""
    return (
        0 < len(t) <= 40
        and not re.search(r"[.?!。？！:：]", t)
        and "," not in t
        and not _has_cjk(t)
    )


def _label_body(lines: list[str], i: int, label_re) -> str:
    """元数据标签的正文：同一段冒号之后的内容；标签**独占一段**时取下一段。

    ⚠️ 实测英文元数据常把标签单独写一段（2024_07_Wenwen_Jiao 的 `Abstract`、
    2025_06_YingJin_Xu 的 `Abstract` 与 `Keywords:`）。旧实现只取 `label_re.sub`
    的结果，正文就丢了，`\\myabstractEN` 退回模板占位符 `Abstract`。
    判据与 parse_tail_metadata ① 同一套：下一段要**同语言**（英文标签 → 不含汉字）
    且不是另一个标签。

    ⚠️ 中文标签（`摘要`/`关键词`）**不**向下补：后面跟英文段的是「中英两套摘要」的
    另一套（实测 2026_01_LingXin_Cai 的补充材料），补进来就把英文写进 `\\myabstractCN`
    了——宁 fail 不误判。
    段首的 BOM（U+FEFF，实测 2024_02_Kai_Qu 的英文摘要段）不是正文，去掉。
    """
    body = label_re.sub("", lines[i])
    if body.strip() or i + 1 >= len(lines) or _has_cjk(lines[i]):
        return body.lstrip("\ufeff")
    nxt = lines[i + 1].strip()
    if nxt and not _has_cjk(nxt) and not _ABS_RE.match(nxt) and not _KW_RE.match(nxt):
        return nxt.lstrip("\ufeff")
    return ""


def _looks_like_abs_prose(t: str) -> bool:
    """像摘要正文（而不是节标题/列表项/标签行）：够长，且有成句的标点。"""
    return len(t) >= _ABS_MIN_CHARS and any(c in t for c in _ABS_END_CHARS)


def _abs_body_cn(lines: list[str], i: int) -> str:
    """摘要标签的正文（中文槽专用）；标签**独占一段**时向下取（判据见 _ABS_LOOKAHEAD 处）。

    ⚠️ 无汉字的标签段（英文 `Abstract`）原样交给 `_label_body`——它的「向下看一段、
    要同语言」判据本来就在那里，这里一个字都不改（**逐字节**同旧行为）。

    ⚠️ 标签与正文同段时**逐字节保持改动前的取值**（就是 `_label_body` 对中文标签的
    原样输出：`_ABS_RE.sub` 之后不做 strip，尾随空白也不动）——普查里那 68 篇
    `abstract_cn=ok` 全是这条路径的既成事实，动一个字节都可能翻状态。
    只有**新放宽**的段首缩进路径才追加「正文必须含汉字」：实测 10 Min Xu 的
    `摘要： The logical education…`（无缩进、正文是英文）现在就算中文摘要，
    缩进版不能因为放宽形态而把英文正文写进 `\\myabstractCN`（宁 fail 不误判）。
    """
    raw = lines[i]
    if not _ABS_RE.match(raw):
        return ""                      # 不是标签段（调用方都会先 match，这里只是安全网）
    if not _has_cjk(raw):
        return _label_body(lines, i, _ABS_RE)
    body = _ABS_RE.sub("", raw).lstrip("\ufeff")
    if body.strip():
        if not raw[:1].isspace() or _has_cjk(body):
            return body
        return ""
    out: list[str] = []
    j = i + 1
    # 空段跳过（不占额度），但最多往后看 _ABS_LOOKAHEAD*2 段，免得一路扫进正文
    while j < len(lines) and len(out) < _ABS_LOOKAHEAD and j - i <= _ABS_LOOKAHEAD * 2:
        t = lines[j].strip()
        j += 1
        if not t:
            continue
        if _ABS_RE.match(t) or _KW_RE.match(t) or _META_LABEL_RE.match(t):
            break                      # ③ 撞上下一个标签就收手（关键词绝不吞）
        if not (_has_cjk(t) and _looks_like_abs_prose(t)):
            break                      # ②④ 不同语言 / 不像正文 → 宁 fail 不误判
        out.append(t)
    return " ".join(out)


def _backtrack_block_start(lines: list[str], abs_idx: int, stop: int) -> int:
    """从英文摘要往上回溯到块首（题名）：连续的非空、非中文、≤160 字的行。

    `stop` 是下界，回溯到它为止（不含）：原有路径是「参考文献」标题段，
    兜底路径是文档头（-1）。遇到汉字、空段或超长行也停——那是正文/文献区。
    """
    j = abs_idx - 1
    while j > stop and lines[j].strip() and not _has_cjk(lines[j]) and len(lines[j]) <= 160:
        j -= 1
    return j + 1


def locate_tail_block(plains: list[str], ref_start: int) -> int:
    """文末中英元数据块的起点段落下标；找不到返回 -1。

    ① 原有路径（先用，行为不变）：「参考文献」标题之后的第一处英文 Abstract，
       再向上回溯，让题名也落进这一块；
    ② 兜底路径：文末 _TAIL_BLOCK_SCAN 段内的英文 Abstract/Keywords 标签——
       「参考文献」标题不存在、或英文块排在它之前的稿子走这条（判据见常量处）。
    """
    tail_start = -1
    if ref_start >= 0:
        tail_start = next(
            (i for i in range(ref_start + 1, len(plains))
             if _ABS_RE.match(plains[i]) and not _has_cjk(plains[i])), -1)
        if tail_start >= 0:
            tail_start = _backtrack_block_start(plains, tail_start, ref_start)
    if tail_start >= 0:
        return tail_start
    lo = max(0, len(plains) - _TAIL_BLOCK_SCAN)
    hit = next((i for i in range(lo, len(plains))
                if _TAIL_LABEL_RE.match(plains[i]) and not _has_cjk(plains[i])), -1)
    if hit < 0:
        return -1
    return _backtrack_block_start(plains, hit, -1)


def _tail_abstract_cn(lines: list[str], ref_start: int) -> str:
    """**参考文献区之后**的中文摘要；没有则返回 ""（正文全英文、中文摘要另附文末的稿子）。

    实测 7 篇：2023年第6期(73) 06 Xiaoqi_Zhang ×2、2025年第6期(85) 06 Jin_Gan、
    2026年第3期(88) 06 Zhixi_Chen ×3、2024年第6期(79) 01 Wenfang_Wang——它们的
    `摘    要` / `摘  要` / `摘要：…` 都不在开头 META_SCAN 段之内，而文末那条路径
    （parse_tail_metadata）**只看非中文标签**（见该函数 ①），于是中文摘要一直 fail。

    这是同一个标签形态判据的**第二处位置**，不是新判据：段首标签 + 正文必须含汉字
    （所以文末的英文块不会被误当中文摘要，实测 LingXin_Cai 那种 `摘要：`+英文 也挡住）。
    只在开头那套**空手而归**时才跑（调用处判断），只补空槽、绝不覆盖已有取值。
    """
    lo = ref_start + 1 if ref_start >= 0 else max(0, len(lines) - _TAIL_BLOCK_SCAN)
    for i in range(lo, len(lines)):
        if not _ABS_RE.match(lines[i]):
            continue
        body = _abs_body_cn(lines, i)
        if body and _has_cjk(body):
            return body
    return ""


def parse_tail_metadata(lines: list[str], start: int) -> dict:
    """解析文末的中英元数据块（英文题名/摘要/关键词、作者、单位、邮箱）。

    只认「参考文献区之后」的那一块——中文摘要/关键词在开头，不要重扫。
    """
    out: dict[str, str] = {}
    if start < 0:
        return out
    seg = [(i, t.strip()) for i, t in enumerate(lines) if i >= start and t.strip()]

    # ① 英文摘要 / 英文关键词（内容判据同开头那套：只看有没有汉字）
    # ⚠️ 标签可能**独占一段**（实测 2026_05_Jingru_Kou：`[111]` 只有 `Abstract`，
    #    正文在下一段），这时取下一段的正文，别把模板占位符 `Abstract` 留在产物里。
    for pos, (_i, t) in enumerate(seg):
        if _ABS_RE.match(t) and not _has_cjk(t) and not out.get("abstract_en"):
            body = _ABS_RE.sub("", t).strip()
            if not body and pos + 1 < len(seg):
                nxt = seg[pos + 1][1]
                if not _has_cjk(nxt) and not _ABS_RE.match(nxt) and not _KW_RE.match(nxt):
                    body = nxt
            out["abstract_en"] = body.lstrip("\ufeff")
        elif _KW_RE.match(t) and not _has_cjk(t) and not out.get("keywords_en"):
            body = _KW_RE.sub("", t).strip()
            if not body and pos + 1 < len(seg):
                nxt = seg[pos + 1][1]
                if not _has_cjk(nxt) and not _ABS_RE.match(nxt) and not _KW_RE.match(nxt):
                    body = nxt
            out["keywords_en"] = body.lstrip("\ufeff")

    # ② 英文题名 + 副题 + 作者：从英文摘要往上回溯连续的非中文短行
    abs_idx = next((i for i, t in seg if _ABS_RE.match(t) and not _has_cjk(t)), None)
    if abs_idx is not None:
        back: list[tuple[int, str]] = []
        for i, t in seg:
            if i >= abs_idx:
                break
            if _has_cjk(t) or len(t) > 160:
                back.clear()
                continue
            back.append((i, t))
        # ⚠️ 作者行下面常紧跟一行**括号包起来的英文单位**（实测 05 Guoping_Du：
        #    `Du Guo-ping` / `(Institute of Philosophy, …)`）。它既不像人名也不是题名；
        #    旧逻辑只看「像不像人名」，于是把单位印成了 `\mytitleEN`。
        while back and back[-1][1].lstrip().startswith(("(", "（")):
            back = back[:-1]
        # 紧邻摘要的那一行若像人名，就是英文作者
        if back and _looks_like_person(back[-1][1]):
            out["author_en"] = back[-1][1]
            back = back[:-1]
        if back:
            # 最后一行是题名，其后若以破折号开头则为副题
            title = back[-1][1]
            sub = ""
            if len(back) >= 2 and _is_subtitle(title):
                sub = title
                title = back[-2][1]
            out["title_en"] = title
            out["subtitle_en"] = sub

    # ③ 邮箱 + 中英文所属单位
    for _i, t in seg:
        m = _EMAIL_RE.search(t)
        if m and not out.get("email"):
            out["email"] = m.group(1)
    aff_idx = next((i for i, t in seg if _AFFIL_HEAD_RE.search(t) and len(t) <= 30), None)
    if aff_idx is not None:
        cn, en = [], []
        for i, t in seg:
            if i <= aff_idx or len(t) > 120:
                continue
            if _EMAIL_RE.search(t):
                continue
            # ⚠️ 单位标签后面常紧跟同一份「作者简介」的**其它**标签行（实测 05 Guoping_Du：
            #    `联系电话：…` / `通信地址：…` / `邮政编码：…`）。旧逻辑把它们全当单位，
            #    `\myaffiliation` 会印成电话号码和家庭住址。带标签的行只认「单位/机构/
            #    affiliation」这一种；没有标签的行（单位名本身）照收。
            lab = re.match(r"^([\u4e00-\u9fffA-Za-z]{2,8})\s*[:：]", t)
            if lab and "单位" not in lab.group(1) and "affiliation" not in lab.group(1).lower():
                continue
            if _has_cjk(t):
                cn.append(t)
            elif t:
                en.append(t)
        # 中文单位：人工成品用 `\\ ` 换行（`中山大学逻辑与认知研究所\\ 中山大学哲学系`）
        out["affiliation_cn"] = "\\\\ ".join(cn)
        # ⚠️ 英文单位：不能只用 `\\`。人工成品与模板的写法是
        #   `\\　\hspace{1em}\phantom{\myfirstauthorEN}\hspace{1em} ` ← **末尾有个空格**
        # 即第二行用「全角空格 + 作者名宽度的 phantom + 空格 + 一个普通空格」缩进。
        # 末尾那个空格不能省：它正好是 PDF 里的 1.88pt（实测 09 Jiawei_Chu：
        # 人工第二机构 x=38.64，漏了空格就是 x=36.76，首字母 D 看着偏左一点点）。
        out["affiliation_en"] = AFFIL_EN_JOIN.join(en)
    return out


#: 无样式英文小标题里允许的小写词（Title Case 判断用）
_TITLE_SMALL_WORDS = {
    "a", "an", "the", "and", "or", "nor", "but", "for", "of", "on", "in", "to",
    "at", "by", "with", "from", "as", "into", "about", "over", "under", "after",
    "before", "between", "through", "during", "without", "within", "against",
    "upon", "via", "per", "its",
}


def _looks_plain_en_title(t: str) -> bool:
    """无编号、无样式的一段英文短句，像不像章节标题（Title Case、无句末标点）。

    第 121 条（07 Hu_Liu 真稿）：访谈稿的 9 个小标题在 docx 里是**无样式独立短段**
    （`On Logic Pluralism`…），旧判据只认编号标题 → 产物一个 `\\section` 都没有。
    这里只做「像标题」的文本判据；是否真是标题由调用方结合上下文（前后段落）决定。
    """
    if not t or len(t) > 80 or _has_cjk(t):
        return False
    if re.search(r"[.?!;:。；：\u2019\u2018]", t):
        return False
    words = t.split()
    if not (2 <= len(words) <= 10):
        return False
    if not words[0][:1].isupper():
        return False
    for w in words:
        core = re.sub(r"[^A-Za-z'\-]", "", w)
        if not core:
            return False
        if core[0].isupper() or core.lower() in _TITLE_SMALL_WORDS:
            continue
        return False
    return True


#: 无样式英文小标题里允许的小写词（Title Case 判断用）
def classify_en_heading(t: str) -> tuple[int, str]:
    """判断一段英文文本是不是章节标题，返回 (层级, 标题)。层级 0 表示不是标题。

    保守起见，编号列表项不算标题：要求够短、不以句末标点结尾、且句中不含句点。
    """
    m = _HEAD_EN2_HALF.match(t)
    if m and len(t) < 130 and "." not in m.group("t"):
        return 2, m.group("t").strip()
    m = _HEAD_EN2.match(t)
    if m and len(t) < 130 and "。" not in t:
        return 2, m.group("t").strip()
    m = _HEAD_EN3.match(t)
    if m and len(t) < 130 and "." not in m.group("t"):
        return 2, m.group("t").strip()
    m = _HEAD_EN1.match(t)
    if m:
        rest = m.group("t")
        if (len(t) < 130 and "." not in rest
                and not rest.rstrip().endswith((".", "。", ";", "；", ":", "：", ","))
                and rest[:1].isupper()):
            return 1, rest.strip()
    return 0, t


def _has_cjk(t: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in t)


def find_char_diagrams(paras, min_run: int = 8, max_len: int = 12) -> list[tuple[int, int]]:
    """找出「用一堆单字符段落拼出的图」。

    返回 [(起始下标, 连续段数), ...]。判据：连续 min_run 段以上，每段可见长度
    都不超过 max_len 且以单个字符为主。
    """
    runs: list[tuple[int, int]] = []
    i, n = 0, len(paras)
    while i < n:
        # 含内嵌图片的段落绝不能算进「字符拼图」——
        # 实测 01 Yanjin_Chen 的段落 77（3 张图、无文字）被相邻的单字符段
        # 吞掉，导致那一组图完全没有输出。
        if any(sg.kind == "image" for sg in paras[i].segs):
            i += 1
            continue
        t = paras[i].plain.strip()
        if t and len(t) <= max_len:
            j = i
            tiny = 0
            while j < n:
                if any(sg.kind == "image" for sg in paras[j].segs):
                    break  # 图片段切开连续区间
                tj = paras[j].plain.strip()
                if not tj:
                    j += 1
                    continue
                if len(tj) > max_len:
                    break
                if len(tj) <= 3:
                    tiny += 1
                j += 1
            if j - i >= min_run and tiny >= (j - i) * 0.7:
                runs.append((i, j - i))
                i = j
                continue
        i += 1
    return runs


# 矢量形状占位。页码在转换阶段由 slcn/office.py 定位后回填。
SHAPE_PLACEHOLDER = (
    "\n% {marker}\n"
    "% 【需人工截图】原稿此处有 {n} 个矢量图形（Word 绘图工具所画，无内嵌图片可提取）。\n"
    "% 打开原稿 PDF 找到对应位置，截图或重画后替换本段。\n"
    "\\begin{{center}}\n"
    "  \\fbox{{\\parbox{{0.8\\textwidth}}{{\\centering "
    "\\textbf{{【需人工截图：{n} 个矢量图形{page_hint}】}}}}}}\n"
    "\\end{{center}}\n"
)


def table_env(tbl, idx: int, img_names: dict[str, str] | None = None) -> str:
    """把 Word 表格转成 LaTeX table + tabular。

    格式对齐人工成品惯例（见 Haiting_Huang / Yingying_Tang）：
        \\begin{table}[htbp]
          \\centering
          \\begin{tabular}{|c|c|c|}
          \\hline
          ... \\\\
          \\hline
          \\end{tabular}
          \\caption{...}
          \\label{tab:...}
        \\end{table}
    """
    ncol = max(tbl.ncols, 1)
    # 单元格文字长（>18 字）的列改用 p{} 定宽，否则 \\hline 之间会被撑爆版心
    long_col = [
        any(
            len(r[i].text) > 18
            or any(sg.kind == "image" for sg in r[i].segs)   # 含图列用 p{} 定宽
            for r in tbl.rows if i < len(r)
        )
        for i in range(ncol)
    ]
    any_long = any(long_col)
    # 第四十六批②续：含图片的表格必须能**跨页**（图片纵向排列后单行会超过一页，
    # 普通 tabular 不能跨页 → 图被裁掉、页数不增；86-01 实测 17 张图全挤在 p8 被切）。
    # 人工成品正是跨页表格（表头每页重复）。模板已加载 longtable（SLCN-MIX/SLEN）。
    has_img = any(
        any(sg.kind == "image" and (img_names or {}).get(sg.rid) for sg in c.segs)
        for r in tbl.rows for c in r
    )

    if any_long:
        w = round(0.92 / ncol, 3)
        colspec = "|" + "|".join("p{" + str(w) + "\\textwidth}" for _ in range(ncol)) + "|"
    else:
        colspec = "|" + "|".join("c" for _ in range(ncol)) + "|"

    def _row_tex(r) -> str:
        cells = []
        used = 0
        for c in r:
            txt = render_cell(c, img_names)
            if c.colspan > 1:
                cells.append(f"\\multicolumn{{{c.colspan}}}{{|c|}}{{{txt}}}")
                used += c.colspan
            else:
                cells.append(txt)
                used += 1
        while used < ncol:  # 补齐缺格，否则表格会错位
            cells.append("")
            used += 1
        return "  " + " & ".join(cells) + " \\\\"

    if has_img:
        head = _row_tex(tbl.rows[0]) if tbl.rows else ""
        lines = ["\n\\begin{longtable}{" + colspec + "}",
                 f"  \\caption{{【需补充表题】}}\\label{{tab:{idx}}}\\\\",
                 "  \\hline", head, "  \\hline", "  \\endfirsthead",
                 "  \\hline", head, "  \\hline", "  \\endhead"]
        for r in tbl.rows[1:]:
            lines.append(_row_tex(r))
            lines.append("  \\hline")
        lines.append("\\end{longtable}\n")
        return "\n".join(lines)

    lines = ["\n\\begin{table}[htbp]", "  \\centering"]
    lines.append(f"  \\begin{{tabular}}{{{colspec}}}")
    lines.append("  \\hline")
    for r in tbl.rows:
        lines.append(_row_tex(r))
        lines.append("  \\hline")
    lines.append("  \\end{tabular}")
    lines.append("  \\caption{【需补充表题】}")
    lines.append(f"  \\label{{tab:{idx}}}")
    lines.append("\\end{table}\n")
    return "\n".join(lines)


def render_cell(cell, img_names: dict[str, str] | None = None) -> str:
    """渲染一个单元格（文字 / 公式 / **图片**）。

    第四十六批②续：表格单元格内嵌图片（86-01 实测 18 张）旧实现直接丢弃；这里按
    `img_names`（rId → figN.ext）输出 `\\includegraphics`。**无图单元格**的输出与
    从前逐字节相同（只有 text/math 分支）。
    """
    parts = []
    for sg in cell.segs:
        if sg.kind == "text":
            parts.append(escape_latex(sg.text).replace("\n", " ").strip())
        elif sg.kind == "math":
            parts.append(f"${sg.text}$")
        elif sg.kind == "image":
            _name = (img_names or {}).get(sg.rid)
            if _name:
                _h = getattr(sg, "h_pt", 0.0)
                if 0 < _h <= 60:
                    # 第四十七批：表内 VML 公式图 → 行内原尺寸 + 缺文件占位
                    parts.append(
                        "\\IfFileExists{" + _name + "}{\\includegraphics[height="
                        + f"{_h:.2f}" + "pt]{" + _name + "}}"
                        "{\\fbox{\\scriptsize 公式图缺失}}")
                else:
                    # 每张图独占一行（`\par`）：原稿图在单元格里是**纵向排列**的
                    # （86-01 实测人工 PDF 把 17 张图分摊在 3 页；inline 塞进一段会全挤在
                    #  一页、页面高度不增）。`width=\linewidth` 按图像列宽排。
                    parts.append(
                        "\\IfFileExists{" + _name + "}{\\includegraphics"
                        "[width=0.8\\linewidth]{" + _name + "}}"
                        "{\\fbox{\\scriptsize 公式图缺失}}\\par")
    return " ".join(p for p in parts if p).strip()


def figure_env(names: list[str], idx: int) -> str:
    """生成 figure 环境。人工惯例见 07 Liangying_Zhao。

    同一段落里连续的图片**合成一个 figure**——实测 01 Yanjin_Chen 的 docx 里有
    5 张分开的图，人工是把它们合成一张 `p1.png` 放进一个 figure 的。
    """
    if not names:
        return ""
    lines = ["\n\\begin{figure}[htbp]", "\\centering"]
    if len(names) == 1:
        lines.append(f"\\includegraphics[width=0.85\\textwidth]{{{names[0]}}}")
    else:
        # 多张：两两一行，宽度按张数分摊
        w = 0.9 / min(len(names), 2)
        for i in range(0, len(names), 2):
            pair = names[i : i + 2]
            lines.append("".join(
                f"\\includegraphics[width={w:.2f}\\textwidth]{{{n}}}" for n in pair
            ))
    lines.append("\\caption{【需补充图题】}")
    lines.append(f"\\label{{fig:{idx}}}")
    lines.append("\\end{figure}\n")
    return "\n".join(lines)


def hoist_quote_fallback(latex: str, quoted: str) -> tuple[str, bool]:
    """引文被行内标记（斜体 / 公式 / 脚注）切开时的退化替换：只替换第一处长引文。

    `render_para` 正常路径是精确替换 `“<content>”`；内容被 `\textit{}` 等标记切开时
    精确串对不上，才走这里。

    ⚠️ 第一处匹配里若含 `\\footnote`，说明**脚注标记插在引文中间**（Word 稿很常见：
       脚注挂在引文第 20 个字后面）。这时整段替换会把脚注正文一起吃掉，而正则实际
       匹配到的是脚注正文里的一对**短引号** → 引文被重复一遍、多出一个 `}`、
       整篇编译失败（`! Too many }'s`）。实测期 87 子案例 3（04 Fayang_Ye，案例 141）。
       此时**放弃提升**（保持原文内联）——人工成品也是这么处理的。

    返回 (新文本, 是否替换)。替换必须用字符串切片而不是 `re.sub` 的替换串：
    `quoted` 里含 `\\hfill`，而 `\\h` 在 re 替换串里是非法转义（实测 2 篇抛
    bad escape \\h）。
    """
    m = re.search(r"“[^”]{20,}”", latex)
    if not m:
        return latex, False
    if "\\footnote" in m.group(0):
        return latex, False
    return latex[: m.start()] + quoted + latex[m.end():], True


def render_para(
    para, fn_objs: dict, bib_sink: list, keymap: dict,
    fn_pages: dict | None = None, quote_threshold: int = 105,
    img_names: dict | None = None,
    fig_counter: list[int] | None = None,
    c_class_union: bool = False,
) -> tuple[str, list[str]]:
    """把一段 XML 片段渲染成 LaTeX。

    - 普通文字 → 转义；带斜体 → \\textit{}
    - OMML 公式 → $...$
    - 脚注 → 按分类分别处理（见 slcn/footnotes.py）
    - 超过三行的引文 → 提升为 quotation 环境（见 slcn/quotations.py）

    fig_counter  **文档级图序号计数器**（P2-0）：`parse_docx()` 在整篇范围内持有同一个
                 `[0]` 并传进来，图 `\\label{fig:N}` 的 N 才是文档内唯一的（旧实现把
                 `fig_seq` 放在本函数内、每个段落从 1 重来 → 多段有图时 label 全叫
                 `fig:1`，实测 corpus 12 篇有图 label 的产物里 6 篇重复，
                 `2025_04_YingJin_Xu` 6 个全是 fig:1）。默认 None = 保留段落内计数
                 （只给不传计数器的旧调用方/单测用，行为与从前逐字节相同）。

    返回 (LaTeX, 提示列表)。
    """
    from .quotations import build_quotation, find_quotes

    fn_pages = fn_pages or {}
    img_names = img_names or {}
    notes: list[str] = []

    # 先判断本段是否有「超过三行的引文」。若有，它挂着的第一个参考文献脚注
    # 会被这条引文消费掉（人工惯例：引文 + \hfill（\parencite{}，第 N 页）），
    # 不能再作为普通脚注输出一遍。
    plain = "".join(sg.text for sg in para.segs if sg.kind == "text")
    # 提升判据（满足其一即可）：① 够长；② 含 ≥2 个句末标点（完整陈述）。
    # ⚠️ 这里先前只判长度，于是 Bo_Chen 那段 92 字、含 3 个「。」的引文
    #    （人工放进了 quotation 环境）被漏掉了。
    #   注：slcn/quotations.py 里的 hoist_quotes() 也有一套判据，但**全项目无人调用**，
    #   是死代码；真正生效的是下面这一行。两处判据要一起改，别只改一处。
    from .quotations import _sentence_ends as _se

    long_quotes = [
        q for q in find_quotes(plain)
        if q.visible_len >= quote_threshold or _se(q.content) >= 2
    ]
    hoist = long_quotes[0] if long_quotes else None
    consumed_fid = None
    if hoist is not None:
        for fid in para.footnote_ids:
            if fid in keymap:
                consumed_fid = fid
                break

    out: list[str] = []
    fig_seq = 0

    def flush_figs(buf: list[str]) -> None:
        nonlocal fig_seq
        if buf:
            # P2-0：优先用**文档级**计数器（parse_docx 传同一个 [0]）→ 图 label 全局唯一；
            # 没传（旧调用方/单测）才退回段落内计数，行为与从前一致。
            if fig_counter is not None:
                fig_counter[0] += 1
                seq = fig_counter[0]
            else:
                fig_seq += 1
                seq = fig_seq
            out.append(figure_env(list(buf), seq))
            buf.clear()

    fig_buf: list[str] = []
    shape_buf: list[int] = []
    for seg in para.segs:
        if seg.kind == "text":
            t = escape_latex(seg.text)
            if not t.strip():
                out.append(t)
                continue
            if seg.italic:
                # 只把有内容的斜体片段包起来，避免产生空的 \textit{}
                lead = t[: len(t) - len(t.lstrip())]
                trail = t[len(t.rstrip()) :]
                out.append(f"{lead}\\textit{{{t.strip()}}}{trail}")
            else:
                out.append(t)
        elif seg.kind == "math":
            # 第四十四批①：行间公式（m:oMathPara）→ `\[...\]`，行内仍 `$...$`
            if getattr(seg, "display", False):
                out.append(f"\\[{seg.text}\\]")
            else:
                out.append(f"${seg.text}$")
        elif seg.kind == "shape":
            shape_buf.append(1)
        elif seg.kind == "image":
            name = img_names.get(seg.rid, "")
            if not name:
                continue
            _h = getattr(seg, "h_pt", 0.0)
            if 0 < _h <= 60:
                # 第四十七批：VML 公式图（MathType 预览图）→ **行内**按原尺寸渲染。
                # `\IfFileExists`：soffice 缺失/转换失败时显示「公式图缺失」占位，
                # 不阻断出稿（外部可选依赖，见 docxxml._soffice_bin）。
                out.append(
                    "\\IfFileExists{" + name + "}{\\raisebox{-0.22\\height}"
                    "{\\includegraphics[height=" + f"{_h:.2f}" + "pt]{" + name + "}}}"
                    "{\\fbox{\\scriptsize 公式图缺失}}"
                )
            else:
                fig_buf.append(name)
        elif seg.kind == "footnote":
            flush_figs(fig_buf)  # 图片与脚注之间断开
            if seg.fid == consumed_fid:
                continue  # 已被引文消费
            fn = fn_objs.get(seg.fid)
            if fn is None:
                continue
            if fn.kind in ("pure_ref", "pure_citation", "pure_citation_matched"):
                # 第四十九批（C 类新口径，**开关默认 OFF**）：ON 时纯引用脚注 →
                # 正文 \parencite，脚注删除；页码进可选参数 `[p.13]`（biblatex
                # 样式自己产生括号）。OFF 时保持旧行为（中文括号包裹）逐字节不变。
                key = keymap.get(fn.fid, "")
                loc = f"[{fn.locator}]" if fn.locator else ""
                if c_class_union:
                    out.append(f"\\parencite{loc}{{{key}}}")
                else:
                    out.append(f"（\\parencite{loc}{{{key}}}）")
            elif fn.kind in ("citation_unmatched", "mixed_manual"):
                # C 类新口径：未匹配短引用/混合型 → 保留脚注原文（不编造条目不丢内容）
                body_note = (getattr(fn, "note_latex", "")
                             or escape_latex(fn.note or getattr(fn, "ref_raw", "")
                                             or fn.text))
                out.append(f"\\footnote{{{body_note}}}")
            elif fn.kind in ("mixed", "mixed_citation"):
                if c_class_union:
                    # ON：混合型（说明文字+引用）→ 保留脚注，说明文字原样
                    #（引用已由 link_citations 替换），build 汇总「请人工核对」warning。
                    body_note = (getattr(fn, "note_latex", "")
                                 or escape_latex(fn.note or fn.text))
                    out.append(f"\\footnote{{{body_note}}}")
                else:
                    # OFF：旧行为（逐字节不变）
                    key = keymap.get(fn.fid, "")
                    loc = f"[{fn.locator}]" if fn.locator else ""
                    note = escape_latex(fn.note)
                    out.append(f"\\footnote{{cf. \\parencite{loc}{{{key}}}. {note} }}")
            else:
                # 纯注释 / 古典文献：原样保留（英文注释已预先转义+替换引用）
                body_note = getattr(fn, "note_latex", "") or escape_latex(fn.text)
                out.append(f"\\footnote{{{body_note}}}")

    flush_figs(fig_buf)
    if shape_buf:
        notes.append(f"矢量形状 {len(shape_buf)} 个（需截图）")
        out.append(SHAPE_PLACEHOLDER.format(n=len(shape_buf), marker="需人工截图", page_hint=""))
    latex = "".join(out)

    # 把超长引文提升为引用环境
    if hoist is not None:
        key = keymap.get(consumed_fid, "") if consumed_fid else ""
        page = fn_pages.get(consumed_fid, "") if consumed_fid else ""
        quoted, note = build_quotation(hoist.content, key, page)
        if note:
            notes.append(f"{note}（引文 {hoist.visible_len} 字）")
        target = f"“{hoist.content}”"
        if target in latex:
            latex = latex.replace(target, quoted, 1)
        else:
            # 引文被斜体/公式/脚注标记切开时，退化匹配（见 hoist_quote_fallback 注释）
            latex, _hoisted = hoist_quote_fallback(latex, quoted)
            if not _hoisted:
                notes.append(
                    "引文内含脚注标记，未提升为 quotation 环境（保持原文内联，避免脚注被拆）"
                )
        if consumed_fid:
            keymap.pop(consumed_fid, None)  # 已产出，避免 bib 重复

    return latex, notes


def parse_docx(path: str, quote_threshold: int = 105,
               c_class_union: bool = False) -> DocxDoc:
    """解析 Word 文稿。

    ⚠️ 用 slcn/docxxml 直接读 XML，**不用 python-docx 的 paragraph.text**。
    后者会静默丢掉 OMML 公式、run 级格式（斜体）和整个 footnotes.xml，
    实测让 Lu_Jiang 一篇丢掉 17.4% 内容和全部 72 处引用。
    """
    from .docxxml import Table, read_blocks, read_footnotes, segs_to_text
    from .footnotes import classify, make_key, parse_entry

    doc = DocxDoc(path=path)
    blocks = read_blocks(path)
    paras = [b for b in blocks if not isinstance(b, Table)]
    n_tables = sum(1 for b in blocks if isinstance(b, Table))
    if not paras:
        doc.warnings.append("文档没有可读段落")
        return doc

    with __import__("zipfile").ZipFile(path) as z:
        _docxml = z.read("word/document.xml").decode("utf-8")
        doc.n_math = len(re.findall(r"<m:oMath(?![A-Za-z])[^>]*>", _docxml))
        # 期 87 子案例 2（03 Wenjing_Du）实测：作者用 MathType 写公式，docx 里是
        # `<w:object>` + `word/embeddings/*.bin`，不是 OMML → 解析不到、**静默丢失**，
        # PDF 里公式位置全空、页数少 2 页。这里如实计数并警告（不阻断出稿）。
        from .docxxml import ole_object_stats, symbol_run_stats

        n_ole, n_embed = ole_object_stats(_docxml, z.namelist())
        # 期 87 子案例 3（04 Fayang_Ye 的 .doc）实测：用 LogicA 等「逻辑符号字体」插入
        # 的 →/∼/∧ 是 `<w:sym>` run，既不是文字也不是 OMML → 静默丢失。
        # 字体的 char→Unicode 映射是字体私有的，不能硬猜；只计数并警告人工核对。
        _font = symbol_run_stats(_docxml)
        try:
            _font_fn = symbol_run_stats(z.read("word/footnotes.xml").decode("utf-8"))
        except KeyError:
            _font_fn = {}
        for k, v in _font_fn.items():
            _font[k] = _font.get(k, 0) + v
    if n_ole:
        doc.warnings.append(
            f"发现 {n_ole} 处 MathType/OLE 公式对象（word/embeddings 共 {n_embed} 个）："
            "它们是 OLE 包不是 OMML，Word 通道不解析 OLE 本体，但已自动取**预览图**"
            "（WMF/EMF）转 PDF 后按原尺寸出图；若本机没有 LibreOffice（soffice），"
            "公式图位置会显示「公式图缺失」占位，安装 LibreOffice 后重跑即可；"
            "如需公式可编辑，请让作者另存为 OMML（或提供 MathType→TeX 版）"
        )
    if _font:
        _n_sym = sum(_font.values())
        _fonts = "、".join(f"{k} {v} 处" for k, v in sorted(_font.items(), key=lambda kv: -kv[1]))
        doc.warnings.append(
            f"发现 {_n_sym} 处符号字体字符（<w:sym>：{_fonts}）：Word 通道不解析这类"
            "字符（既非文字也非 OMML，字体 char→Unicode 映射是字体私有的），"
            "→/∼/∧ 等逻辑符号可能从产物中丢失；请人工核对正文符号"
        )

    plains = [p.plain for p in paras]

    # ① 题名、副标题与作者
    #
    # 题名后面常另起一段放副标题（以破折号开头），再下一段才是作者。
    # 直接取「第 1 段」当作者会把副标题写进 `\myfirstauthor`——实测踩到
    # （09 Jiawei_Chu：`\kaishu ——一个基于广义论证理论的生成模型`）。
    doc.title = plains[0].strip()
    author_idx = 1
    if len(plains) > 1 and _is_subtitle(plains[1]):
        doc.subtitle = plains[1].strip()
        doc.title = f"{doc.title}{doc.subtitle}"
        author_idx = 2
    # ⚠️ 题名与作者之间**可能是空段落**（实测 2026_05_Jingru_Kou：`[1]` 是空行，
    #    作者在 `[2]`），所以不能只看 author_idx 那一段，要往后找到第一个非空段。
    for k in range(author_idx, min(len(plains), author_idx + 4)):
        cand = plains[k].strip()
        if not cand:
            continue
        if len(cand) <= 40 and not cand.endswith(("。", "：")):
            doc.author = re.sub(r"\s+", " ", cand).strip()
        break

    # ② 参考文献区（中英双认）
    ref_start = next(
        (i for i, t in enumerate(plains) if t.strip().lower().rstrip(":：") in _REF_HEADS), -1
    )

    # ②.5 文末中英元数据块（英文题名/摘要/关键词、作者、单位、邮箱）
    #
    # ⚠️ 它排在参考文献区**之后**（09 Jiawei_Chu：第 115–125 段）。所以：
    #   · 参考文献区必须在它之前收口，否则英文摘要会被当成最后一条文献；
    #   · 元数据块只在这里扫，不能拿它去改「正文起点」（那是开头那套的事）。
    # ⚠️ 但**不能要求「参考文献」标题存在**：实测 16 篇源稿的英文块附近根本没有这个
    #    标题（或标题排在块之后），于是整条文末路径被跳过。定位判据见 locate_tail_block。
    tail_start = locate_tail_block(plains, ref_start)
    tail = parse_tail_metadata(plains, tail_start) if tail_start >= 0 else {}

    if ref_start >= 0:
        ref_end = tail_start if tail_start > ref_start else len(plains)
        section = [t.strip() for t in plains[ref_start + 1 : ref_end] if t.strip()]
        # 第四十五批 A2：① 纯「参考文献/References」标题行不是条目，剔掉（86-08 实测）；
        # ② 文末元数据块没被 locate_tail_block 认出来时（88-06 实测：标题/作者/摘要
        #    紧跟在文献区后且无「参考文献」标题），从「摘要/Abstract」标签行开始截断，
        #    否则题名、作者、摘要会被当成文献行（全是解析失败 + 污染 ref_raw_lines）。
        section = [t for t in section if t.strip().lower().rstrip(":：") not in _REF_HEADS]
        _tail_cut = next(
            (i for i, t in enumerate(section)
             if re.match(r"^\s*(?:摘\s*要|Abstract)\s*[:：]?", t) and i > 0),
            None,
        )
        if _tail_cut:
            # 从「摘要」标签往前吃掉紧邻的**元数据行**（中文题名 / 作者姓名）：
            # 短、含中文、不含年份、不是以文献年份/出版信息结尾——文献条目一定有年份或
            # 卷期/页码，这类行是文末块的表头（88-06 实测：`论证能力研究：…` / `陈志喜`）。
            _cut = _tail_cut
            while _cut > 0:
                _prev = section[_cut - 1].strip()
                if (len(_prev) <= 80 and re.search(r"[\u4e00-\u9fa5]", _prev)
                        and not re.search(r"(?:19|20)\d{2}", _prev)
                        and not re.search(r"\d+\s*[（(]\d", _prev)):
                    _cut -= 1
                    continue
                break
            section = section[:_cut]
        # 原文留档（解析器吃掉的那份 `section`，同一顺序、同一口径）：一条都没解析
        # 出来时，refmark 第四节会把这份原文交给编辑的 AI 结构化——**不是**在这里
        # 再写一个解析器。解析成功时它也照样留着，但下游用不到。
        doc.ref_raw_lines = list(section)
        # 两条体例：`[N] 作者, 年份, "题名", 刊名…`（编号清单通道 `_numbered_refs`）
        # 与「作者. 年份. 题名[M]. …」/中文逗号风格（人年制通道 `parse_reference_line`）。
        # ⚠️ 取舍必须**按覆盖条数取优**，不能只看"人年制有没有解析出东西"：
        #    2026-10-17 实例测试回归（案例 116）——Beibei_Gao 的文末 22 条编号清单里有一条
        #    编号写歪了（`[13 J. Ross…` 少了 `]`），逗号兜底把它误吃成 1 条 → `doc.refs` 非空
        #    → 旧逻辑「if not doc.refs」就**再也不敢走编号通道** → 整份 22 条只剩 1 条、
        #    正文引用全部没替换。裸的"先来先得"在两种格式混排/写歪时都会踩这个坑。
        entries, num2key, raw_by_key = _numbered_refs(section)
        for ln in section:
            e = parse_reference_line(ln)
            if e:
                doc.refs.append(e)
        _dedupe_keys(doc.refs)
        # 第四十五批 A2：没解析出来的文献行**保留原文 + warning**（不猜、不丢）。
        _unparsed = [ln for ln in section if parse_reference_line(ln) is None]
        if _unparsed and (doc.refs or entries):
            doc.warnings.append(
                f"文末参考文献区有 {len(_unparsed)} 行未能按行内证据解析"
                f"（原文已保留、交人工/AI 核对）：首行「{_unparsed[0][:80]}」"
            )
        if len(entries) > len(doc.refs):
            # 编号清单解释力更强 → 以它为准；逗号兜底误吃的那几条丢掉（它们本就是清单行）
            doc.refs = []
            doc.bib_entries.extend(entries)
            doc.ref_key_by_num = num2key
            # 编号清单通道的「条目 → 原文」（整条 `[N] …`），见 `_numbered_refs`。
            doc.ref_raw_by_key.update(raw_by_key)
        else:
            # 「条目 → 源稿原文」：文末**人年制**区（第 68 条）。`RefEntry.raw` 就是
            # `parse_reference_line` 收下的那一行（已把连续空白压成单空格）；
            # 键在 `_dedupe_keys` 之后才是最终键，所以放在这里取。
            for e in doc.refs:
                if e.raw:
                    doc.ref_raw_by_key[e.key] = e.raw
            if not doc.refs and entries:
                # 编号清单只收下 1–2 条时上面取优不成立（例如真的只有一条文献）：
                # 人年制一条都没有就还是用编号通道的结果，别把唯一一条丢掉。
                doc.bib_entries.extend(entries)
                doc.ref_key_by_num = num2key
                doc.ref_raw_by_key.update(raw_by_key)

    # ③ 元数据块：中英各一套
    abs_blocks: list[tuple[int, bool, str]] = []
    kw_blocks: list[tuple[int, bool, str]] = []
    for i, t in enumerate(plains[:META_SCAN]):
        if _ABS_RE.match(t):
            # 中文摘要走 _abs_body_cn（多一条「标签独占一段 → 向下取正文」的放宽，
            # 见 _ABS_LOOKAHEAD 处）；英文标签由它内部转交 _label_body，行为不变。
            abs_blocks.append((i, _has_cjk(t), _abs_body_cn(plains, i)))
        elif _KW_RE.match(t):
            kw_blocks.append((i, _has_cjk(t), _label_body(plains, i, _KW_RE)))

    doc.abstract_cn = next((t for _, cjk, t in abs_blocks if cjk), "")
    doc.abstract_en = next((t for _, cjk, t in abs_blocks if not cjk), "")
    doc.keywords_cn = next((t for _, cjk, t in kw_blocks if cjk), "")
    doc.keywords_en = next((t for _, cjk, t in kw_blocks if not cjk), "")

    # ③.2 中文摘要的第二处位置：正文全英文、中文题名+摘要另附在**参考文献之后**的稿子
    #      （实测 7 篇，见 _tail_abstract_cn）。只在开头那套空手时才跑；它**不写**
    #      abs_blocks，所以 meta_end / 正文起点一个字都不受影响。
    if not doc.abstract_cn:
        doc.abstract_cn = _tail_abstract_cn(plains, ref_start)

    doc.title_cn = doc.title if _has_cjk(doc.title) else ""
    doc.title_en = "" if _has_cjk(doc.title) else doc.title
    # ⚠️ `meta_end` 仍按**改动前**的口径算（只数紧贴段首的标签）：它管的是「正文起点」
    #    与「开头英文题名的扫描范围」，都是另外的判据，本轮只放宽摘要的**取值**，
    #    不该顺带把这两处也动了。实测 2022年第4期(65) 康孝军 的稿子：缩进的
    #    `    摘 要：…` 一旦计入 meta_end，扫描范围就从第 2 段涨到第 5 段，
    #    段首的邮箱行 ` kxj319@hotmail.com` 会被当成英文题名（title_en 变脏）。
    #    `_KW_RE` 本来就要求紧贴段首，所以这个过滤只对摘要标签生效。
    meta_end = max([i for i, _, _ in abs_blocks + kw_blocks if not plains[i][:1].isspace()],
                   default=-1)
    for i in range(1, meta_end + 1):
        t = plains[i]
        # ⚠️ 空白段不是题名：实测 07 Guangwu_Feng 的英文题名前面有一个只有空格的段，
        #    旧逻辑把 `' '` 当成了英文题名，于是 `\mytitleEN` 是空的、文末的真题名
        #    也因为「title_en 非空」不再来覆盖。
        if len(t) > 80 or _ABS_RE.match(t) or _KW_RE.match(t) or not t.strip():
            continue
        if _has_cjk(t) and not doc.title_cn:
            doc.title_cn = t
        elif not _has_cjk(t) and not doc.title_en and t != doc.author:
            doc.title_en = t

    # ③.5 文末那一套：开头没有的（英文题名/摘要/关键词/作者/单位/邮箱）在这里补
    #
    # 中文稿常见布局：英文题名、英文摘要、中英文单位、邮箱全放在**参考文献之后**。
    # 早先只扫开头 20 段，于是 `\mytitleEN` / `\myabstractEN` 一直是模板占位符
    # `Title` / `Abstract`（实测 09 Jiawei_Chu 就是这样，编辑只能自己填）。
    #
    # ⚠️ title_cn / title_en 只放**主标题**，副标题留在 doc.subtitle / doc.subtitle_en，
    #    由 build.py 用 `\\` 拼起来。两边都带上副标题就会重复一次（实测踩到）。
    for attr in ("title_cn", "title_en"):
        v = getattr(doc, attr)
        if v and doc.subtitle and v.endswith(doc.subtitle):
            setattr(doc, attr, v[: -len(doc.subtitle)].strip())

    if tail:
        if not doc.abstract_en:
            doc.abstract_en = tail.get("abstract_en", "")
        if not doc.keywords_en:
            doc.keywords_en = tail.get("keywords_en", "")
        if not doc.title_en.strip():
            doc.title_en = tail.get("title_en", "")
            doc.subtitle_en = tail.get("subtitle_en", "")
        doc.author_en = tail.get("author_en", "")
        doc.email = tail.get("email", "")

    # ③.6 单位：不再只认文末块里的「单位：」标签（旧实现全库只拿到 1/132）。
    #     开头元数据区、文末块、脚注里的作者信息都要看；判据见 collect_affiliations
    #     与 README 第 45 条①。带标签的旧路径优先（那是人工写明的），其余按下述顺序兜底。
    fn_segs_early = read_footnotes(path)
    fn_texts = [segs_to_text(s) for s in fn_segs_early.values()]
    # 邮箱：旧路径只认文末块里的「邮箱：」标签，41 篇 fail 里它只救得了 2 篇；
    # 裸地址要在这三段里另找（范围与判据见 collect_emails）。文档顺序第一个 = 第一作者，
    # 覆盖旧值；`tail["email"]`（上面 ③.5 写的）只在 collect_emails 空手而归时兜底。
    emails = collect_emails(plains, tail_start, fn_texts)
    if emails:
        doc.email = emails[0]
        if len(emails) > 1:
            doc.warnings.append(
                "检出多个邮箱候选（已取文档顺序第一个）：" + "、".join(emails[:4]))
    author_names = [x for x in re.split(r"[\s、,，·]+", doc.author) if len(x) >= 2]
    aff_cn, aff_en = collect_affiliations(plains, tail_start, fn_texts, author_names)
    doc.affiliation_cn = tail.get("affiliation_cn", "") or "\\\\ ".join(aff_cn)
    doc.affiliation_en = tail.get("affiliation_en", "") or AFFIL_EN_JOIN.join(aff_en)
    doc.abstract = doc.abstract_cn or doc.abstract_en
    doc.keywords = doc.keywords_cn or doc.keywords_en

    # 中文关键词的分隔符按**刊物惯例**归一：人工成品一律用分号
    # （全库实测 142 篇用「；」、0 篇用「、」），模板注释也写着「以中文分号分隔」，
    # 而作者原稿常用顿号。实测 09 Jiawei_Chu：源稿「、」→ 人工「；」。
    if doc.keywords_cn:
        doc.keywords_cn = re.sub(r"\s*[、，,]\s*", "；", doc.keywords_cn).strip("；")

    # ④ 正文起点：必须在**所有**元数据块之后
    start = meta_end + 1 if meta_end >= 0 else 2
    for i in range(start, min(start + 8, len(paras))):
        if plains[i] == doc.title:
            j = i + 1
            while j < len(paras) and (
                plains[j] == plains[i]
                or plains[j] == doc.author
                or plains[j].startswith(("School", "Department", "University", "Institute"))
            ):
                j += 1
            start = j
            break
    end = ref_start if ref_start > start else len(paras)
    body_paras = paras[start:end]

    # 第 121 条：全英文稿（正文前 120 段无汉字）里，文末有参考文献区时，
    # 脚注里的散文/引注一律按注释处理（01 Tuomo_Aho 型）；中文稿/混合稿不受这条影响。
    doc_is_en = not any(_has_cjk(p.plain) for p in body_paras[:120] if p.plain.strip())

    # ⑤ 脚注：分类 → 生成 bib → 建立 id→key 映射
    #    （footnotes.xml 在 ③.6 已经读过一次，这里复用，不重复解压）
    fn_segs = fn_segs_early
    doc.n_footnotes = len(fn_segs)
    fn_objs = {fid: classify(segs_to_text(segs)) for fid, segs in fn_segs.items()}
    for fid, f in fn_objs.items():
        f.fid = fid  # classify() 不知道 id，这里回填，否则 keymap 查不到
    # 第四十四批①：含公式的脚注按 segs 渲染 LaTeX（渲染分支优先用 note_latex），
    # 避免 `$…$` 被后面的 escape_latex(fn.text) 二次转义（83/05、87/06 编译失败真因）。
    for _fid, _segs in fn_segs.items():
        if any(sg.kind == "math" for sg in _segs):
            fn_objs[_fid].note_latex = _footnote_latex(_segs)
    # 第四十七批（86-07 实测）：中文**论述性脚注**（长散文 + 行内引用）被 classify()
    # 误判成 pure_ref → 渲染时被提升成行内 `\parencite`，脚注散文整段丢失
    # （86-07：29 条脚注人工 PDF 全印、引擎只印 11 条，丢 18 条/2 页）。
    # 判据：含中文、汉字 ≥15、总长 ≥40 → 按**纯注释**处理（保留原文；引用在
    # ⑤.3b 用 link_citations 就地替换），且**不生成 bib 条目**（人工成品同样不把
    # 这些论述脚注当 bib 条目）。英文论述性脚注已有第 121 条同类降级。
    for _fid, _f in ([] if c_class_union else list(fn_objs.items())):
        if _f.kind != "pure_ref":
            continue
        _txt = _f.text or ""
        _cjk_n = len(re.findall(r"[\u4e00-\u9fa5]", _txt))
        # 必须同时有「行内作者-年引用」——这才是**论述性脚注**的特征；
        # 中文文献条目型脚注（《…》，出版社，年）不含括号引用，保持 pure_ref 进 bib
        # （否则 86-03 的 21 条中文文献脚注会被误降级、bib 从 22 掉到 1，实测回归）。
        # 论述性特征：① 行内**作者-年引用**（括号里任意位置含年份，覆盖
        # `(Cook, 2010)` / `(陈波, 2018，第三节)` / `(Burgess, 2009, p. 129)`）；
        # 或 ② 多句散文（≥2 个句号）。中文文献条目型脚注通常两者都不满足
        # （86-03 的 21 条文献脚注实测不满足 → 不会被误降级、bib 保持 22）。
        _inline_cite = re.search(
            r"[（(][^（）()]{0,100}?(?:19|20)\d{2}[^（）()]{0,40}?[）)]", _txt)
        _multi_sentence = (_txt.count("。") >= 2)
        # 中文文献条目还要认「书名号」与「年份+期号」形态：86-03 有 1 条脚注把
        # **两条中文文献**合在一起（`…，逻辑学研究，2018 年第 1 期。麦劲恒：《…》…`），
        # 旧标记漏了书名号 → 被误当论述脚注降级、bib 22→21。这里补上（86-07 的
        # 论述脚注实测都不含书名号/期号，不受影响）。
        _ref_marker = re.search(
            r"出版社|学报|期刊|\[[MJDC]\]|博士论文|硕士论文|《[^》]{2,}》|"
            r"(?:19|20)\d{2}\s*年?\s*第\s*\d+\s*[期卷]", _txt)
        # 第四十八批（86-07 实测）：短论述脚注（如「详细的证明过程可参见(Schurz,
        # 2022)第三节。」CJK 仅 10 字）此前 CJK≥15 门槛漏掉 → 散文被丢弃。
        # 放宽只对**半角括号 + 拉丁作者-年**的引用生效（86-07 型）；中文文献脚注
        # （90-06/90-07 的人年制条目、全角括号/中文作者）保持 pure_ref 进 bib，
        # 避免把文献脚注误降级（实测 90-06 bib 38→25、90-07 51→26 的回归）。
        _short_ascii_cite = (_cjk_n < 15 and re.search(
            r"\([^()]*[A-Za-z][^()]{0,80}?(?:19|20)\d{2}[^()]{0,40}\)", _txt))
        if _cjk_n >= 5 and len(_txt) >= 12 and (
                (_cjk_n >= 15 and (_inline_cite or (_multi_sentence and not _ref_marker)))
                or _short_ascii_cite):
            _f.kind = "pure_note"
            _f.note = _txt
            _f.ref = ""
            _f.entry = None
            setattr(_f, "_discursive_cjk", True)

    # 第四十九批（C 类新口径，**开关默认 OFF**）：ON 时用 bib-refs 新分类器
    # `classify_citation()` 重分类，并做短引用匹配（不编造）。映射结果复用旧渲染/条目
    # 机制：reference_entry → pure_ref；pure_citation 命中 → pure_citation_matched
    #（正文 parencite）；未命中 → citation_unmatched（保留脚注 + warning）；mixed →
    # mixed_manual（保留脚注 + 核对 warning）；note_only → pure_note。
    if c_class_union:
        from .footnotes import (CITATION_ENTRY, CITATION_MIXED, CITATION_NOTE,
                                CITATION_PURE, FootnoteWarning,
                                classify_citation, resolve_citation,
                                union_entries)
        _candidates: list[tuple[str, dict, str]] = []
        for _k, _e, _raw in doc.bib_entries:
            _candidates.append((_k, _e, _raw))
        for _r in doc.refs:
            _candidates.append((_r.key, {"author": _r.author, "year": _r.year,
                                         "title": _r.title, "type": _r.kind}, _r.raw))
        _guard_entries: list[int] = []   # 中文文献条目型兜底 fid（union 前按指纹会被合并）
        for _fid, _fn in list(fn_objs.items()):
            _new = classify_citation(_fn.text)
            _new.fid = int(_fid)
            _kind = _new.kind_new
            if _kind == CITATION_ENTRY:
                _new.kind = "pure_ref"
                _new.ref = _new.ref_raw or _new.ref
                _new.entry = _new.entry or {}
            elif _kind == CITATION_PURE:
                _key, _warn = resolve_citation(_new, _candidates)
                if _key:
                    _new.kind = "pure_citation_matched"
                    _new.key = _key
                else:
                    _new.kind = "citation_unmatched"
                    doc.c_class_warnings.append(_warn)
                    doc.footnote_notes += 1
            elif _kind == CITATION_MIXED:
                _new.kind = "mixed_manual"
                try:
                    _new.note_latex = link_citations(
                        escape_latex(_new.note or _new.ref_raw or _new.text),
                        doc.refs)[0]
                except Exception:  # noqa: BLE001
                    _new.note_latex = ""
                doc.c_class_warnings.append(FootnoteWarning(
                    "mixed_manual_check", fid=int(_fid),
                    text=(_new.text or "")[:200],
                    reason="说明文字+引用：脚注保留，请人工核对引用与说明是否对应",
                    author=str((_new.entry or {}).get("author", "")),
                    title=str((_new.entry or {}).get("title", "")),
                    year=str((_new.entry or {}).get("year", "")),
                    locator=_new.locator))
            elif _kind == CITATION_NOTE:
                _new.kind = "pure_note"
                _new.note = _new.note or _new.text
            # 第四十九批引擎侧边界保护（86-03 实测）：中文**文献条目型**脚注
            # （源稿形如 `[] 胡炳年、陈彦瑾，“…”，逻辑学研究，2018 年第 1 期。`）会被
            # 新分类器判成 mixed/note_only → bib 22→1。带 `[编号]` 开头 + 文献标记
            # （书名号/出版社/学报/期号/年份）时按 reference_entry 处理，进并集 bib。
            # 只在带编号前缀时兜底，不影响 86-07 论述脚注 / 90-06/07 短引用。
            if (_new.kind in ("mixed", "mixed_manual", "pure_note")
                    and re.match(r"^\s*\[\s*[^\]]*\]", _new.text or "")
                    and re.search(r"《[^》]{2,}》|出版社|学报|期刊|\[[MJDC]\]|"
                                  r"(?:19|20)\d{2}\s*年?\s*第\s*\d+\s*[期卷]",
                                  _new.text or "")):
                _txt_clean = re.sub(r"^\s*\[\s*[^\]]*\]\s*", "",
                                    _new.text or "").strip()
                try:
                    _e = parse_entry(_txt_clean)
                except Exception:  # noqa: BLE001
                    _e = {}
                # 与旧口径一致：解析字段为空也照样进 bib（旧行为 86-03 就是 22 条），
                # 空字段由 bibnorm/编辑侧兜底，不能因为 entry 空就丢条目。
                _new.kind = "pure_ref"
                _new.ref = _txt_clean
                _new.entry = _e or {}
                _guard_entries.append(int(_fid))
            fn_objs[_fid] = _new

    # 第四十九批：混合型脚注清单（渲染保留脚注 + build 汇总「请人工核对」warning）。
    # **仅新口径开关 ON 时**收集：默认 OFF 时旧行为/产物/warning 逐字节不变。
    if c_class_union:
        doc.mixed_footnotes = [
            (int(fid), (fn.text or "").strip()[:40])
            for fid, fn in fn_objs.items()
            if fn.kind in ("mixed", "mixed_manual")
        ]

    _end_entries_snapshot: list[tuple[str, dict, str]] = list(doc.bib_entries)
    used_keys: set[str] = set()
    keymap: dict[int, str] = {}
    if c_class_union:
        # 短引用命中 → keymap（正文渲染用 `\\parencite[locator]{key}`）
        for _fid, _fn in fn_objs.items():
            if _fn.kind == "pure_citation_matched" and getattr(_fn, "key", ""):
                keymap[_fid] = _fn.key
    # 第四十七批（88-02 实测）：同一文献被**多条脚注反复引用**时逐条提升 → bib 重复
    # （88-02 有 19 条重复：同一作品不同页码各生成一条），参考文献区多 2 页。
    # 按「作者+年+题名前 30 字」指纹去重：重复引用复用已有键，不再新增 bib 条目。
    _bib_fp: dict[tuple[str, str, str], str] = {}
    _bib_loose: dict[tuple[str, str], tuple[str, str]] = {}
    seq = 0
    for fid in sorted(fn_objs):
        f = fn_objs[fid]
        if f.kind in ("pure_ref", "mixed"):
            # 第 121 条（期 88 实测）：英语**论述性脚注**（含年份/see 的散文）会被
            # classify() 误判成 pure_ref；parse_entry() 只能给出 note（没有 author/title），
            # 照旧当文献条目会 ① 生成垃圾 bib、② 把脚注散文从正文抽走。
            # 条目不成立且**不含中文**时降级为纯注释（中文脚注体例不动，避免回归）。
            _e0 = parse_entry(f.ref)
            # 「有文末参考文献区」的英文稿（refs 非空）里，脚注的散文/引注一律不算条目；
            # 没有文末区的短引注（`Cf. X 2012.`，中文稿里的英文脚注常见）保留旧行为，
            # 避免正文引用全丢。
            _downgrade = (_entry_is_note_only(_e0) and not _has_cjk(f.text)
                          and ((doc_is_en and bool(doc.refs))
                               or (not _looks_short_bibliographic_note(f.text)
                                   and len(f.text.strip()) >= 90)))
            if _downgrade:
                f.kind = "pure_note"
                f.note = f.text
                f.ref = ""
                f.entry = None
                doc.footnote_notes += 1
                # P0-6b：降级不能静默——把「被判成注释、没进 bib」的脚注写进 warning，
                # 编辑/报告才看得见（sub9 少 1 条就是这么漏掉的）。
                doc.warnings.append(
                    f"脚注 {fid} 的文献条目未进 bib（parse_entry 只能得到 note，"
                    f"按注释处理）：{f.text.strip()[:80]}")
                continue
            seq += 1
            entry = _e0
            f.entry = entry
            # 第四十七批：重复引用同一文献 → 复用已有键（不新增 bib 条目）。
            # 只在 author/year/title 三者都有时去重（信息不足的不猜）。
            _fp = None
            # 优先级：**清洗后的原文**（去掉页码定位后比较）——同一作品被不同页码引用时
            # 只有 `p. 13` / `p. 6` 不同，归一到同一指纹；parsed 字段做兜底。
            _raw_norm = re.sub(
                r"\bpp?\.?\s*\d+(?:\s*[-–—]\s*\d+)?|第\s*\d+(?:\s*[-–—]\s*\d+)?\s*页",
                "", (f.ref or "").lower())
            _raw_norm = re.sub(r"\s+", "", _raw_norm)
            if _raw_norm:
                _fp = ("raw", _raw_norm, "")
            elif entry.get("author") and entry.get("title"):
                _fp = (
                    "fields",
                    re.sub(r"\W+", "", str(entry.get("author", "")).lower()),
                    re.sub(r"\W+", "", str(entry.get("title", "")).lower())[:30]
                    + "|" + str(entry.get("year", "") or ""),
                )
            # 第四十八批（88-02 实测 57 vs 人工 43）：版本/年份缺失导致同一作品指纹不同。
            # 宽松指纹 = 作者姓氏 + 题名（不含年份/版本），**仅当一方缺年份**时归并；
            # 两边年份都不同（不同版次/不同作品）不合并——不猜题名/不改条目内容。
            _loose = None
            _author = str(entry.get("author", "") or "")
            _title = str(entry.get("title", "") or "")
            _year = str(entry.get("year", "") or "")
            if _author and _title:
                _loose = (
                    re.sub(r"[^\w\u4e00-\u9fa5]+", "", _author.lower())[:40],
                    re.sub(r"[^\w\u4e00-\u9fa5]+", "", _title.lower())[:50],
                )
            if _fp and _fp in _bib_fp:
                f.key = _bib_fp[_fp]
                keymap[fid] = f.key
                if f.ref:
                    doc.ref_raw_by_key[f.key] = f.ref
                continue
            if _loose and _loose in _bib_loose:
                # 同作者+同题名（前后去标点/大小写归一，**不改题名、不猜年份**）视为
                # 同一作品的不同版次/页码引用 → 归并（不改变已保留条目内容）。
                _key0, _year0 = _bib_loose[_loose]
                f.key = _key0
                keymap[fid] = f.key
                if f.ref:
                    doc.ref_raw_by_key[f.key] = f.ref
                continue
            f.key = make_key(entry, seq, used_keys)
            if _fp:
                _bib_fp[_fp] = f.key
            if _loose and _loose not in _bib_loose:
                _bib_loose[_loose] = (f.key, _year)
            keymap[fid] = f.key
            doc.bib_entries.append((f.key, entry, f.ref))
            # 「条目 → 源稿原文」：脚注体例（第 68 条）。原文取 `f.ref` = `classify()`
            # 从脚注文字里切出来、**真正喂给解析器**的那段文献文字（作者的论述在
            # `f.note` 里，不混进来）；键是上面刚定的最终键。
            if f.ref:
                doc.ref_raw_by_key[f.key] = f.ref
        else:
            doc.footnote_notes += 1

    if c_class_union:
        # 第四十九批 C 类新口径：并集 = 文末清单（编号清单条目）∪ 脚注条目，
        # 由 bib-refs `union_entries()` 按「作者+题名+年份+类型」指纹合并（忽略页码/
        # 版次；文末版本优先、缺字段互补；冲突进 warning）。**开关默认 OFF**。
        from .footnotes import union_entries as _union_entries

        _new_entries = doc.bib_entries[len(_end_entries_snapshot):]
        doc.bib_entries = _union_entries(
            list(_end_entries_snapshot), list(_new_entries),
            warnings=doc.c_class_warnings)
        # 中文文献条目型兜底（86-03）：解析字段可能为空 → 指纹相同被 union 合并成 1 条；
        # 这里把丢掉的 guard 条目按原键补回（内容来自原文，仍由 entry_to_bib 兜底渲染）。
        _now_keys = {k for k, _e, _r in doc.bib_entries}
        for _gfid in _guard_entries:
            _gf = fn_objs.get(_gfid)
            if _gf is None or not getattr(_gf, "key", ""):
                continue
            if _gf.key in _now_keys:
                continue
            doc.bib_entries.append((_gf.key, _gf.entry or {}, getattr(_gf, "ref", "")))
            _now_keys.add(_gf.key)
        if doc.bib_entries:
            say_union = (f"   并集参考文献（C 类新口径）：文末清单 "
                         f"{len(_end_entries_snapshot)} 条 + 脚注条目 "
                         f"{len(_new_entries)} 条 → {len(doc.bib_entries)} 条")
            doc.warnings.append(say_union)

    # ⑤.3b 第 121 条：英文纯注释（含上一步由英文散文脚注降级来的）里的
    # 人年制引用也替换成 \parencite——人工成品在脚注里同样做了引用替换。
    # 只处理**不含中文**的注释，中文脚注体例保持逐字节不变。
    for _f in fn_objs.values():
        if (_f.kind in ("pure_note", "mixed") and not getattr(_f, "note_latex", "")
                and (not _has_cjk(_f.text) or getattr(_f, "_discursive_cjk", False))):
            # 第四十七批：英文注释（第 121 条既有）与**中文论述性脚注**（86-07：
            # classify 误判 pure_ref 后降级）都做行内引用替换，保留散文原文。
            try:
                _esc = escape_latex(_f.text)
                _f.note_latex = link_citations(_esc, doc.refs)[0]
            except Exception:  # noqa: BLE001 —— 注释替换失败不影响主流程
                _f.note_latex = ""

    # ⑤.4 内嵌图片：docx 里就存着原图，直接取出（比截图质量高）
    from .docxxml import read_media

    media_map = read_media(path)
    img_names: dict[str, str] = {}
    seq_img = 0
    # 第四十六批②续（86-01 实测）：图片不只在正文段落里——**表格单元格里也可能有**
    # （`<w:drawing>` 全在 `<w:tc>` 内时，旧实现只遍历 body_paras（不含 Table）→
    #  18 张全丢、只剩正文 1 张，而人工成品是 18 条 \includegraphics）。
    # 按 blocks 的文档顺序收集（含 Table 的 cell.segs）；fig 序号与 extract_media()
    # 的导出顺序一致（导出按 doc.images 顺序，二者同源）。
    from .docxxml import Table as _Table

    def _iter_block_segs(_b):
        if isinstance(_b, _Table):
            for _row in _b.rows:
                for _cell in _row:
                    yield from _cell.segs
        else:
            yield from _b.segs

    _pos = {id(b): k for k, b in enumerate(blocks)}
    _bstart = _pos.get(id(body_paras[0]), 0) if body_paras else len(blocks)
    _bend = (_pos.get(id(body_paras[-1]), len(blocks) - 1) + 1) if body_paras else len(blocks)
    for _b in blocks[_bstart:_bend]:
        for sg in _iter_block_segs(_b):
            if sg.kind == "image" and sg.rid in media_map and sg.rid not in img_names:
                seq_img += 1
                ext = os.path.splitext(media_map[sg.rid])[1].lower() or ".png"
                if ext in (".wmf", ".emf"):
                    # 第四十七批（87-2 实测）：MathType 公式预览图是 WMF/EMF，XeLaTeX
                    # 不能直接嵌入；`extract_media()` 会用 soffice 转成同名 figN.pdf。
                    ext = ".pdf"
                img_names[sg.rid] = f"fig{seq_img}{ext}"
    doc.images = [(media_map[r], n) for r, n in img_names.items() if r in media_map]

    # ⑤.5 检出「用字符拼的图」（见 find_char_diagrams）
    fig_marks: dict[int, int] = {st: c for st, c in find_char_diagrams(body_paras)}

    # ⑤.6 `N. 中文标题`（见 _HEAD_CN_DOT）的两条**上下文**判据。
    #
    # ① 层级：同篇还有 `一、`/`二、` 式一级编号时（`_HEAD1` → `\section`），`1.` 是**子节**。
    #    实测 03 Ziming_Song：`一、引言`…`六、结语` 是一级，`1. 决策论与愿望/信念`、
    #    `2. 表征定理` 挂在 `二、` 底下；人工成品 Ziming_Song.tex 给的正是
    #    `\subsection{决策论与愿望/信念}`。67A 邱德钧稿同构（`二、…` 下挂 `1.`–`5.`）。
    #    没有 `一、` 的稿子（04 Bo_Chen、07 Long_Chen、07 Guangwu_Feng…）里 `N.` 本身就是
    #    一级标题，人工成品同样排 `\section` —— 所以这一层判定与人工做法一致。
    #    ⚠️ 只作用于**新增**的点式分支：`N 空格`/`N.M` 等既有分支的层级一字不改。
    # ② 列表：`1. 莱布尼茨` / `2. 休谟` / `3. 康德` 这种**连排**短行是名单，不是一串小节。
    #    判据是「前后邻居里只要还有一个同形行，整串都按列表处理」。语料 132 篇里这种
    #    连排 0 处（04 Bo_Chen 那 6 行每行后面都跟着成段正文，照旧认成标题），
    #    这条纯粹是防「名单被拆成一堆 \section」的反例防线——只会少认，不会多认。
    cn_num_top = any(
        _HEAD1.match(escape_latex(p.plain).strip()) for p in body_paras if p.plain.strip()
    )
    nonempty_paras = [p for p in body_paras if p.plain.strip()]
    lone_cn_dot: set[int] = set()
    for k, p in enumerate(nonempty_paras):
        prev_t = escape_latex(nonempty_paras[k - 1].plain).strip() if k else ""
        next_t = (escape_latex(nonempty_paras[k + 1].plain).strip()
                  if k + 1 < len(nonempty_paras) else "")
        if not _is_cn_dot_heading(prev_t) and not _is_cn_dot_heading(next_t):
            lone_cn_dot.add(id(p))

    # ⑤.7 无样式英文小标题（第 121 条，07 Hu_Liu 型）：只对**全英文**稿件启用，
    # 且要求「前面是成段正文、后面是成段正文」，避免把正文里的短句误判成标题。
    plain_en_headings: dict[int, str] = {}
    if nonempty_paras and not any(_has_cjk(x.plain) for x in body_paras[:120] if x.plain.strip()):
        for k, p in enumerate(nonempty_paras):
            if k < 0:                     # （占位，实际判据见下：前 3 段多半是元数据）
                continue
            t = escape_latex(p.plain).strip()
            if _HEAD_EN1.match(t) or _HEAD_EN2.match(t) or _HEAD_EN2_HALF.match(t) \
                    or _HEAD_EN3.match(t) or not _looks_plain_en_title(t):
                continue
            prev_t = (escape_latex(nonempty_paras[k - 1].plain).strip() if k else "")
            next_t = (escape_latex(nonempty_paras[k + 1].plain).strip()
                      if k + 1 < len(nonempty_paras) else "")
            prev_ok = (
                k <= 3                                   # 稿子开头的题名/副题/作者区之后
                or len(prev_t) > 80                       # 两段正文之间
                or prev_t.endswith((".", "。", "!", "?", ":", "："))
            )
            if prev_ok and len(next_t) > 50:
                plain_en_headings[id(p)] = t

    # ⑥ 渲染正文（段落与表格按原顺序混排）
    fn_pages = {fid: f.locator for fid, f in fn_objs.items()}
    out: list[str] = []
    tab_idx = 0
    fig_counter = [0]   # P2-0：文档级图序号（整篇唯一；表序号 tab_idx 一直是文档级）
    # body_paras 不含表格，需换回 blocks 的下标才能让表格落到正确位置
    pos_in_blocks = {id(b): k for k, b in enumerate(blocks) if not isinstance(b, Table)}
    start_block = pos_in_blocks.get(id(body_paras[0]), 0) if body_paras else len(blocks)
    end_block = pos_in_blocks.get(id(body_paras[-1]), len(blocks) - 1) + 1 if body_paras else len(blocks)
    seq_blocks = blocks[start_block:end_block]
    idx = -1
    for para in seq_blocks:
        idx += 1
        if isinstance(para, Table):
            tab_idx += 1
            out.append(table_env(para, tab_idx, img_names))
            doc.warnings.append(
                f"表格 {tab_idx}：{para.nrows} 行 × {para.ncols} 列已转为 tabular"
                + ("（含合并单元格，请核对）" if para.merged else "")
            )
            continue
        if idx in fig_marks:
            n = fig_marks[idx]
            doc.warnings.append(f"【需人工制图】原稿此处用 {n} 个字符拼成图示，已置为占位")
            out.append(
                "\n% " + "=" * 62 + "\n"
                f"% 【需人工制图】原稿此处用 {n} 个单字符段落拼出一张图，\n"
                "% Word 里没有内嵌图片对象，无法自动导出。请参照原稿重画后\n"
                "% 换成 figure 环境，例如：\n"
                "%   \\begin{figure}[htbp]\n"
                "%   \\centering\n"
                "%   \\includegraphics[width=0.85\\textwidth]{figX.pdf}\n"
                "%   \\caption{【需补充图题】}\\label{fig:X}\n"
                "%   \\end{figure}\n"
                "% " + "=" * 62 + "\n"
                "\n\\begin{center}\n"
                "  \\fbox{\\parbox{0.8\\textwidth}{\\centering "
                # 需要 3 个右花括号：关 textbf、关 parbox、关 fbox；
                # f-string 里每个字面 } 要写成 }}，所以这里写 6 个
                                                "\\textbf{【需人工制图：原稿此处为 %d 个字符拼成的图示】}}}" % n + "\n"
                "\\end{center}\n"
            )
            continue
        if any(idx > k and idx < k + c for k, c in fig_marks.items()):
            continue  # 已被上一张图的占位吸收
        latex, notes = render_para(para, fn_objs, doc.bib_entries, keymap, fn_pages,
                                   quote_threshold, img_names, fig_counter=fig_counter,
                                   c_class_union=c_class_union)
        doc.warnings.extend(notes)
        if not latex.strip():
            continue
        plain = escape_latex(para.plain).strip()
        if _HEAD1.match(plain):
            h = _HEAD1.sub("", plain).strip()
            doc.headings.append((1, h))
            out.append(f"\n\\section{{{h}}}\n")
        elif (m_sp := _HEAD_CN_SPACE.match(plain)) and not plain.rstrip().endswith(
                ("。", "，", ",", "；", ";", "：", ":")) and not m_sp.group("t").startswith(
                ("个", "些", "种", "般", "旦", "直", "定", "方", "部", "边", "时",
                 "系", "起", "次", "年", "月", "日", "人", "事", "项", "条", "段",
                 "步", "层", "类", "是", "为", "般", "切")):
            # 87-9 访谈体：`一 非形式逻辑`（一/二/三 + 空格 + 短标题）
            h = m_sp.group("t").strip()
            doc.headings.append((1, h))
            out.append(f"\n\\section{{{h}}}\n")
        elif _HEAD2.match(plain):
            h = _HEAD2.sub("", plain).strip()
            doc.headings.append((2, h))
            out.append(f"\n\\subsection{{{h}}}\n")
        elif (m_cn := _HEAD_CN_ARABIC.match(plain)) and _is_cn_numbered_heading(m_cn.group("t")):
            # 新式中文标题 `1 引言`：编号要去掉（人工成品就是 `\section{引言}`）
            h = m_cn.group("t").strip()
            doc.headings.append((1, h))
            out.append(f"\n\\section{{{h}}}\n")
        elif _is_cn_dot_heading(plain) and id(para) in lone_cn_dot:
            # `1. 引言` / `1.引言`（点式编号 + 汉字）：与 `1 引言` 同族，判据共用
            # `_is_cn_numbered_heading`（反例见 _is_cn_dot_heading）。层级与「连排名单」
            # 两条上下文判据见 ⑤.6；`id(para)` 落选 = 这一行属于连排名单，留在正文里。
            h = _HEAD_CN_DOT.match(plain).group("t").strip()
            lv = 2 if cn_num_top else 1
            doc.headings.append((lv, h))
            out.append(f"\n\\subsection{{{h}}}\n" if lv == 2 else f"\n\\section{{{h}}}\n")
        elif id(para) in plain_en_headings:
            h = plain_en_headings[id(para)]
            doc.headings.append((1, h))
            out.append(f"\n\\section{{{h}}}\n")
        else:
            lv, h = classify_en_heading(plain)
            if lv == 1:
                doc.headings.append((1, h))
                out.append(f"\n\\section{{{h}}}\n")
            elif lv == 2:
                doc.headings.append((2, h))
                out.append(f"\n\\subsection{{{h}}}\n")
            else:
                out.append(latex + "\n")
    doc.body = "\n".join(out).strip()
    return doc


# ── 正文引用替换 ──────────────────────────────────────────────────────────

# （Haack 1974） （Martin 2025，31-44） （参见陈波 2022） （例\parencite{Wen2025}）
_CITE_RE = re.compile(
    r"[（(]\s*(?:参见|见|例如|如|引自|转引自)?\s*"
    r"(?P<names>[^（）()\d]{1,40}?)\s*"
    r"(?P<year>\d{4}[a-z]?)"
    r"(?P<tail>[^（）()]{0,24}?)"
    r"[）)]"
)


def _normalize_locator(tail: str) -> str:
    r"""把引用尾巴里的页码统一成中文惯用式。

    人工成品的写法：`第3页`、`第632页`、`第31\cndash 44页`。
    源稿里则是 `p.632`、`P632`、`632-635`、`第 3 页` 各种混着来。

    ⚠️ 还要**去掉前导的逗号**：tail 常以 `，` 开头，而模板里已经有一个 `，`，
    不去掉就会输出 `（\parencite{key}，，p.632）`——实测踩到过。
    """
    t = tail.strip().strip("，,、;；:：").strip()
    if not t:
        return ""
    # 已经是「第 N 页」的形式
    m = re.fullmatch(r"第\s*(\d{1,5})\s*[-–—~]\s*(\d{1,5})\s*页?", t)
    if m:
        return f"第{m.group(1)}\\cndash {m.group(2)}页"
    m = re.fullmatch(r"第\s*(\d{1,5})\s*页", t)
    if m:
        return f"第{m.group(1)}页"
    # p.632 / pp.632-635 / P632
    m = re.fullmatch(r"[Pp]{1,2}\.?\s*(\d{1,5})\s*[-–—~]\s*(\d{1,5})", t)
    if m:
        return f"第{m.group(1)}\\cndash {m.group(2)}页"
    m = re.fullmatch(r"[Pp]{1,2}\.?\s*(\d{1,5})", t)
    if m:
        return f"第{m.group(1)}页"
    # 裸数字 / 数字区间
    m = re.fullmatch(r"(\d{1,5})\s*[-–—~]\s*(\d{1,5})", t)
    if m:
        return f"第{m.group(1)}\\cndash {m.group(2)}页"
    m = re.fullmatch(r"(\d{1,5})", t)
    if m:
        return f"第{m.group(1)}页"
    # 其他（章节号之类）原样保留
    return t


# 正文里的纯文本编号引用：`（[21]，第33页）` / `([21], p. 33)` / `（见[3]）`
# ⚠️ 一个方括号里可以有**多个编号**：实测 `（[45，46，47]）`、`([1, 2])`。
_NUM_GROUP = re.compile(r"\[\s*(\d{1,3}(?:\s*[，,、]\s*\d{1,3})*)\s*\]")


def _cite_nums(text: str) -> list[int]:
    """取出文本里所有 `[N]` / `[N，M]` 的编号。"""
    out: list[int] = []
    for grp in _NUM_GROUP.findall(text):
        out.extend(int(x) for x in re.split(r"[，,、]", grp) if x.strip())
    return out


# 整组：括号里含至少一个 `[N]`（不跨括号，避免吞掉整段）
_GRP_CN = re.compile(r"[（(](?P<inner>[^（()）]{0,100}?\[[^（()）]{0,100}?)[）)]")
_GRP_EN = re.compile(r"\((?P<inner>[^()]{0,100}?\[[^()]{0,100}?)\)")


def link_numbered_citations(
    body: str, num2key: dict[int, str], lang: str = "zh"
) -> tuple[str, int, list[str]]:
    r"""把正文里的 `（[N]，第M页）` 这类**纯文本编号引用**换成 `\parencite`。

    实测（全库 91 份 Word 源稿）：**24 份**用这种写法，形态是
    `（[N]）` 261 处、`（[N]，第M页）` 608 处、多引用 `（[45，46，47]）`。
    人工成品的写法（139 篇中文稿实测）：`（\parencite{键}，第21页）` **1919 处**、
    `（\parencite{键}）` 1357 处，页码区间用 `\cndash`（**1245 处**，手册规范）；
    `\parencite[页码]{键}` 那种可选参数形式中文稿里只有 12 处（例外）。

    ⚠️ **英文稿完全不同**（48 篇实测）：`\parencite[p.~332]{键}` **207 处**，
    而中文式「（…，第N页）」**0 处**。所以按 lang 分两套：
      · 中文：`（\parencite{键}，第21页）`——定位写在括号里、命令外面；
      · 英文：`\parencite[p.~332]{键}`——定位写进命令的可选参数，`~` 是不换行空格。

    多条目按手册第 33 条第 4 款：**只用一个引用命令**（`\parencite{a,b}`）；
    但**各自带页码时不能合并**，人工成品的写法是
    `（\parencite{a26}；\parencite{a27}，第245--253页）`。
    编号前的引导语（`（见[3]）`）保留在命令前面。

    返回 (新正文, 转换处数, 问题清单)。编号超出清单范围的原样保留并记一条问题。
    """
    if not num2key or not body:
        return body, 0, []

    issues: list[str] = []
    n_done = 0
    is_en = lang == "en"

    def resolve(nums: list[int]) -> list[str] | None:
        keys: list[str] = []
        for n in nums:
            k = num2key.get(n)
            if not k:
                return None
            if k not in keys:
                keys.append(k)
        return keys

    def repl(m: re.Match) -> str:
        nonlocal n_done
        inner = m.group("inner")
        # 每段：([编号…], 编号前的引导语, 编号后的定位)
        segs: list[tuple[list[int], str, str]] = []
        for raw in re.split(r"[；;]", inner):
            nums = _cite_nums(raw)
            if not nums:
                segs.append(([], "", raw.strip()))
                continue
            first = _NUM_GROUP.search(raw)
            pre = raw[: first.start()].strip().strip("，,、:：").strip()
            post = _NUM_GROUP.sub("", raw[first.end():]).strip().strip("，,、:：").strip()
            segs.append((nums, pre, post))
        nums_all = [n for nums, _p, _q in segs for n in nums]
        if not nums_all:
            return m.group(0)
        if resolve(nums_all) is None:
            issues.append(f"正文引用的编号 {nums_all} 超出文末清单范围，已原样保留")
            return m.group(0)
        has_loc = any(q for _n, _p, q in segs) or (is_en and any(p for _n, p, _q in segs))
        if not has_loc:
            keys = resolve(nums_all) or []
            n_done += 1
            # 手册第 33 条第 4 款：多个参考文献只用一个引用命令
            cmd = "\\parencite{" + ",".join(keys) + "}"
            pre = next((p for _n, p, _q in segs if p), "")
            if is_en:
                return f"({cmd})" if not pre else f"{pre} ({cmd})"
            return f"（{pre}{cmd}）" if pre else f"（{cmd}）"
        parts: list[str] = []
        tail_pre = ""
        for nums, pre, post in segs:
            keys = resolve(nums) or []
            if not keys:                      # 纯定位段（如「前言」）→ 并到上一段
                if parts and post:
                    parts[-1] += f"，{post}"
                continue
            cmd = "\\parencite{" + ",".join(keys) + "}"
            if is_en:
                parts.append(_en_locator_cmd(cmd, post) if post else cmd)
                if pre:
                    tail_pre = pre
            else:
                if pre:
                    tail_pre = pre
                norm = _normalize_locator(post) if post else ""
                parts.append(f"{cmd}，{norm}" if norm else cmd)
        if not parts:
            return m.group(0)
        n_done += 1
        if is_en:
            if len(parts) == 1 and parts[0].startswith("\\parencite["):
                return (f"{tail_pre} " if tail_pre else "") + parts[0]
            return (f"{tail_pre} " if tail_pre else "") + "(" + "；".join(parts) + ")"
        return f"（{tail_pre}" + "；".join(parts) + "）"

    grp = _GRP_EN if is_en else _GRP_CN
    return grp.sub(repl, body), n_done, issues


def _en_locator_cmd(cmd: str, loc: str) -> str:
    r"""英文引用的定位：`p. 33` / `pp. 33-40` / `Sec. 7.1` → 命令的可选参数。

    人工成品实测（48 篇英文稿）：`\parencite[p.~332]{键}` 207 处，`~` 是不换行空格；
    区间形式全库没有先例，这里按同一路子写 `pp.~33--40`（双短横）。
    """
    t = loc.strip().strip("，,;；:：").strip()
    keys = cmd[len("\\parencite"):]
    m = re.fullmatch(r"[Pp]{1,2}\.?\s*~?\s*(\d{1,5})\s*[-–—~]{1,2}\s*(\d{1,5})", t)
    if m:
        return f"\\parencite[pp.~{m.group(1)}--{m.group(2)}]{keys}"
    m = re.fullmatch(r"[Pp]{1,2}\.?\s*~?\s*(\d{1,5})", t)
    if m:
        return f"\\parencite[p.~{m.group(1)}]{keys}"
    m = re.fullmatch(r"(\d{1,5})\s*[-–—~]{1,2}\s*(\d{1,5})", t)
    if m:
        return f"\\parencite[pp.~{m.group(1)}--{m.group(2)}]{keys}"
    if re.fullmatch(r"\d{1,5}", t):
        return f"\\parencite[p.~{t}]{keys}"
    return f"\\parencite[{t}]{keys}" if t else cmd


# ── 年份唯一兜底前的「姓名证据」（第 73 条） ────────────────────────────────
# 年份唯一**只是候选范围，不是匹配依据**。早先这里不看姓名就直接
# `hit = by_year[year][0]`，实测全库 23 次触发里 5 次是硬配
# （`（Williamson 2017）`→Hjortland2017、`（Kripke 2007: 301）`→Recanati2007、
# `（何朝安，2021）`→AGregory2021、`（IPCAS，1955年正式成立）`→Shen1955），
# 印进 PDF 就是引错文献，而且真正的 Williamson2007 从未被引用。
# 另 18 次姓名其实**对得上**，只是 `RefEntry.surname` 把西文名的首字母算进了姓
# （`S. Blackburn` / `H.Kelsen` / `J.Woods`），或中文名写在方括号里
# （`Xing, Fuyi [邢福义]` / `Ma, Mingyan [马明艳]`），精确匹配才落空——这些得留住。
# 所以兜底只在**有硬证据**时才配，并把证据写进 logs 留档。

_LATEX_CMD_RE = re.compile(r"\\[A-Za-z]+")
_NAME_TOKEN_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def _cite_name_tokens(s: str) -> list[str]:
    r"""引用名/作者串 → 比对用词元：去掉 LaTeX 命令与标点，全部小写。

    `\textit{Peter of Spain` → ['peter', 'of', 'spain']；`邢福义` → ['邢福义']。
    """
    s = _LATEX_CMD_RE.sub(" ", s)
    return [t.lower() for t in _NAME_TOKEN_RE.split(s) if t]


def _author_surname_token(entry: RefEntry) -> str:
    """条目作者里最可能当「姓」的那个词。

    西文取**第一个逗号段**的最后一个词（位置取末，不排序）：
    `S. Blackburn`→blackburn、`H.Kelsen`→kelsen、`D. N. Walton`→walton、
    `Recanati, F`→recanati、`Hjortland, O`→hjortland；中文姓由 surname_cn 给。
    """
    if entry.surname_cn:
        return entry.surname_cn
    head = re.split(r"[,，]", entry.author)[0]
    toks = _cite_name_tokens(head)
    return toks[-1] if toks else ""


def _year_fallback_evidence(names: str, cands: list[str], entry: RefEntry) -> str:
    r"""年份唯一兜底前的姓名证据：引用名与条目作者必须真能对上。

    返回证据描述（进日志留档）或空串（＝姓名完全对不上，**不许**替换）。
    只认三类硬证据：
      ① 条目作者的姓出现在引用名词元里（西文按**词边界**，避免 `Li` 命中 `Little`）；
      ② 条目作者整串出现在引用名里（机构作者 `Qinghua xuexiao (清华学校)`）；
      ③ 引用名出现在条目作者串里（`Xing, Fuyi [邢福义]` 的方括号译名、
         `S. Blackburn` 这类「首字母+姓」写法）；中文按子串、西文按词边界。
    """
    name_toks = set(_cite_name_tokens(names))
    name_norm = " ".join(_cite_name_tokens(names))
    au_norm = " ".join(_cite_name_tokens(entry.author))
    sur = _author_surname_token(entry)
    if sur and sur.lower() in name_toks:
        return f"引用名含条目姓「{sur}」"
    if au_norm and re.search(
        r"(?<![A-Za-z])" + re.escape(au_norm) + r"(?![A-Za-z])", name_norm
    ):
        return f"引用名含条目作者「{entry.author}」"
    for c in cands:
        c = c.strip()
        if not c:
            continue
        if re.search(r"[\u4e00-\u9fff]", c):
            if c in entry.author:
                return f"条目作者含引用名「{c}」"
        elif re.search(
            r"(?<![A-Za-z])" + re.escape(c.lower()) + r"(?![A-Za-z])", entry.author.lower()
        ):
            return f"条目作者含引用名「{c}」"
    return ""


def link_citations(body: str, refs: list[RefEntry]) -> tuple[str, list[str], list[str]]:
    """把正文里的人年制引用换成 \\parencite{key}。

    返回 (新正文, 未匹配上的引用, 日志)。
    匹配策略：先用「姓+年」精确匹配；再试译名表；最后**只在该年唯一且有姓名证据**
    时按年兜底（证据写进日志）；仍然落空就保留原文并报告，**绝不按年份硬配**。
    """
    by_sur_year: dict[tuple[str, str], RefEntry] = {}
    by_sur_cn_year: dict[tuple[str, str], RefEntry] = {}
    by_year: dict[str, list[RefEntry]] = {}
    for e in refs:
        if e.surname:
            by_sur_year.setdefault((e.surname.lower(), e.year), e)
        if e.surname_cn:
            by_sur_cn_year.setdefault((e.surname_cn, e.year), e)
        by_year.setdefault(e.year, []).append(e)

    unmatched: list[str] = []
    logs: list[str] = []
    used: set[str] = set()

    def repl(m: re.Match) -> str:
        names = re.sub(r"\\[A-Za-z]+\*?|[{}]", " ", m.group("names")).strip()
        year = m.group("year")
        tail = m.group("tail").strip()
        # 第 121 条（01 Tuomo_Aho 真稿）：括号里带 LaTeX 标记的（如
        # `(edited in \textit{Peter of Spain 1992})`）不是规范引用点；照替换会把
        # 收尾花括号当页码塞进 `\parencite{…，}）`，产物直接 `Too many }'s`。
        # 这种**保留原文**并进 unmatched，交给编辑处理。
        _probe_t = re.sub(r"\\([&%_#$])", r"\1", tail)
        if re.search(r"[\\{}]", _probe_t):
            # tail 里带 LaTeX 花括号/命令 → 页码不干净，保留原文（否则会出
            # `\parencite{…，}）` 这类不平衡产物；01 Tuomo_Aho 实测）。
            unmatched.append(m.group(0))
            return m.group(0)
        # names 可能是「苏珊·哈克」「陈波」「Haack」「Beall 和 Restall」
        cands = re.split(r"[、,，和与&\s]+", names)
        cands = [c for c in cands if c]
        hit = None
        for c in cands:
            c2 = re.sub(r"^[\u4e00-\u9fff]{0,3}·", "", c)  # 去掉「苏珊·」这类译名
            if (c, year) in by_sur_cn_year:
                hit = by_sur_cn_year[(c, year)]
                break
            if (c2, year) in by_sur_cn_year:
                hit = by_sur_cn_year[(c2, year)]
                break
            if (c.lower(), year) in by_sur_year:
                hit = by_sur_year[(c.lower(), year)]
                break
            if c in TRANSLATED_NAMES:
                pass
        if hit is None:
            for c in cands:
                for cn, en in TRANSLATED_NAMES.items():
                    if c.startswith(cn) or cn.endswith(c):
                        for e in refs:
                            if e.key.startswith(en) and e.year == year:
                                hit = e
                                break
                        if hit:
                            break
                if hit:
                    break
        if hit is None and len(by_year.get(year, [])) == 1:
            # ⚠️ 第 73 条：年份唯一只是候选范围，**不是**匹配依据。这里必须拿到姓名证据
            #    才配（见 _year_fallback_evidence），否则让该引用点进入 unmatched，
            #    而不是静默替换成同年的另一条文献。
            ev = _year_fallback_evidence(names, cands, by_year[year][0])
            if ev:
                hit = by_year[year][0]
                logs.append(
                    f"『{names} {year}』按年份唯一兜底到 {hit.key}"
                    f"（姓+年精确匹配落空；证据：{ev}，请核对）"
                )

        if hit is None:
            unmatched.append(m.group(0))
            return m.group(0)
        used.add(hit.key)
        # 页码：统一成中文惯用式「第N页」「第N\\cndash M页」。
        # 源稿里常见 `p.632`、`P632`、`632-635`，人工成品一律写作「第632页」。
        loc = _normalize_locator(tail)
        if loc:
            return f"（\\parencite{{{hit.key}}}，{loc}）"
        # ⚠️ **没有页码时也必须包外层括号**。_CITE_RE 要求源稿里本来就带
        # `（`/`(`，所以输出永远应该有括号——早先只写 `\\parencite{key}`，
        # 结果正文里出现「……《逻辑的认识论》\\parencite{Martin2025}一书中」，
        # 与人工成品的「（\\parencite{Martin2025}）」不一致。
        return f"（\\parencite{{{hit.key}}}）"

    new_body = _CITE_RE.sub(repl, body)
    logs.append(f"引用替换命中 {len(used)} 个不同条目；未匹配 {len(unmatched)} 处")
    return new_body, unmatched, logs
