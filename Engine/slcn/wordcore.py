"""Word 文稿解析的共享原语（B-207 从 docxsource 纯搬运）：零宽字符、LaTeX 转义、RefEntry/DocxDoc。"""

from __future__ import annotations

import re
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
    # B-130（批73）：`ref_key_by_num` 由 **Word 自动编号安全闸（B-022）成功物化**的条数
    # （0 = 没走该闸；映射可能来自源稿里**显式写着** `[N]` 的清单文本）。
    # 只给「脚注裸编号 → \parencite」通道做 gate 用（见 `_footnote_num2key_gate`）：
    # B-022 条件不满足时按 spec「不猜」；不改正文渲染/既有产物。
    ref_auto_materialized: int = 0
    # 第四十九批（C 类新口径）：混合型脚注（说明文字+引用）清单 → build 汇总
    # 「请人工核对」warning；元素 (fid, 片段)。纯日志，不影响产物。
    mixed_footnotes: list[tuple[int, str]] = field(default_factory=list)
    # 第四十九批 C 类新口径：FootnoteWarning 列表（mixed_manual_check /
    # citation_unmatched / field_conflict），build 汇总成中文 warning。
    c_class_warnings: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 批71 B-118：作者信息脚注解析出的**作者级**元数据（多作者/中英文姓名/单位/邮箱）。
    # 元素：{"name", "name_en", "affiliation_cn": [...], "affiliation_en": [...],
    #        "email"}。build 用它**只填空不覆盖**地补第二作者起的 CN 单位/邮箱
    # （旧实现只有英文合著者槽位有来源）。空列表 = 与从前逐字节相同。
    author_meta: list[dict] = field(default_factory=list)
    # 批71 B-118：作者信息脚注里的收稿日期/基金项目（build 只填空不覆盖 CLI 值）。
    received: str = ""
    grants: str = ""


def _has_cjk(t: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in t)
