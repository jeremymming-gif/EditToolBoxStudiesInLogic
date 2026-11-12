r"""一校核查：把《排校手册》与《流程化经验书》里的规范逐条变成可执行的检查（第 92 条）。

**它解决什么**：初排稿经过编辑**第一次人工校对**之后，正文里会出现一批只有人手才会
引入的排版问题（脚注跑到逗号后面、引文括号位置、半角标点混进中文、顿号后紧跟引号…）。
《排校手册》把这些规范写成了文字，《流程化经验书》的「预校正」一节甚至直接给了一串
正则检索式 —— 但都是**人手在编辑器里搜**。本模块把那套东西固化成规则，可重复、可回归。

**两条纪律**（与 `bibcheck` 一致）：

  1. **默认只报不改**：`check()` 只产出报告。经验书原话「并非修改所有匹配项，稍作观察」——
     很多命中要人判断（半角括号到底是英文括注还是漏改）。
  2. **可自动改的只有一小撮**：凡「改动无歧义、且不会碰到数学/命令/注释」的才允许
     `--fix`（`fixable=True`），而且一律写到**新文件**，绝不动输入。

规则的依据（basis）逐条注明出处：`手册 §校对规范·常规 4.1` / `经验书 预校正·中文` 之类，
便于编辑回查原文。

**屏蔽区**：所有文本规则都在 `_visible_mask()` 上匹配 —— 注释、`\verb`、verbatim、
数学环境、宏名、以及 `\cite/\ref/\label/\url` 的键都在屏蔽之内。屏蔽的做法是**等长替换成
空格**（不是删除），这样命中位置与原文偏移一一对应，报行号、做定点替换都不会错位。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

CJK = r"\u4e00-\u9fff"

#: quotation / quote 在 SLCN 模板里被 `\kaishu` 重定义过（楷体环境）
KAISHU_ENVS = ("quotation", "quote")
_MATH_ENVS = ("equation", "align", "gather", "multline", "eqnarray",
              "array", "matrix", "pmatrix", "bmatrix", "cases", "displaymath")
#: 这些命令的花括号参数是**键**或**路径**，文本规则一律不碰
_KEY_ARGS = ("cite", "parencite", "textcite", "footcite", "nocite", "ref", "eqref",
             "label", "pageref", "url", "href", "includegraphics", "bibitem", "input",
             "include", "usepackage", "documentclass", "bibliography", "addbibresource")


#: 数学区三种形态：环境、`\[..\]`/`\(..\)`、行内 `$..$`（允许 `$$..$$`）。
#: 行内用**单一正则一次配对**，不能先用 `\$\$.*?\$\$` 再 `\$[^$]*\$`：紧邻的
#: `$\lnot$$\Box$` 会被 `$$` 规则错认成行间公式，进而把后面整段正文当成数学区
#: （批 112 收尾：英文稿 9 行弯引号因此漏修，见 `tests/test_b312_post_en_rules.py`）。
_MATH_ENV_RE = re.compile(r"\\begin\{(" + "|".join(_MATH_ENVS) + r")\*?\}.*?\\end\{\1\*?\}", re.S)
_MATH_PAREN_RE = re.compile(r"\\\[.*?\\\]|\\\(.*?\\\)", re.S)
_MATH_INLINE_RE = re.compile(r"(?<!\\)(\${1,2})(?:(?!\1).)*?\1", re.S)


def math_spans(text: str) -> list[tuple[int, int]]:
    r"""所有数学区 `(start, end)`：环境 / 行间 `\[..\]` / 行内 `$..$`（含 `$$..$$`）。"""
    spans: list[tuple[int, int]] = []
    for pat in (_MATH_ENV_RE, _MATH_PAREN_RE, _MATH_INLINE_RE):
        spans.extend((m.start(), m.end()) for m in pat.finditer(text))
    return spans


# ── 屏蔽层 ───────────────────────────────────────────────────────────────

def visible_mask(text: str) -> str:
    r"""返回与 `text` **等长**的字符串：可做文本检查的字符保留，其余换成空格。"""
    buf = list(text)

    def blank(a: int, b: int) -> None:
        # 用 `\x00` 而不是空格：空格会被 `\s*` 吃掉，规则就会**跨过被屏蔽的数学区**去匹配
        # （实测：`且 $\vdash\bot,…$` 被 ZH-02 误报成「中文之间的半角句号」）。
        for i in range(a, b):
            if buf[i] != "\n":          # 换行保留，行号才对得上
                buf[i] = "\x00"

    def blank_match(m: re.Match) -> None:
        blank(m.start(), m.end())

    # 顺序有讲究：先挖掉「大块」的（verbatim / 数学），再处理命令与注释
    for pat in (r"\\begin\{(?:verbatim|lstlisting)\}.*?\\end\{(?:verbatim|lstlisting)\}",
                r"\\verb\*?\|[^|]*\|", r"\\verb\*?(.)(.*?)\1"):
        for m in re.finditer(pat, text, re.S):
            blank_match(m)
    for a, b in math_spans(text):             # 环境/行间/行内数学一次挖干净
        blank(a, b)
    masked = "".join(buf)                     # 中间结果：先按上面的块屏蔽一次
    # 注释：`%` 到行尾（`\%` 是字面百分号，不算注释）
    for m in re.finditer(r"(?<!\\)%.*", masked):
        blank_match(m)
    masked = "".join(buf)
    # 键参数：`\cite{...}` 之类。允许可选参数 `[...]`
    for cmd in _KEY_ARGS:
        for m in re.finditer(r"\\" + cmd + r"\*?(?:\[[^\]]*\])?\{[^{}]*\}", masked):
            blank_match(m)
    masked = "".join(buf)
    # 自带 `\textup` 的命令：它们的内容本来就是正体，不该被判「未调正」
    for m in re.finditer(r"\\(?:textup|ct|pct|pcp|pcpp)\*?(?:\{[^{}]*\})+", masked):
        blank_match(m)
    masked = "".join(buf)
    # 宏名（`\hfill` / `\textbf` 的**名字**不是正文）—— 只挖名字，不动它们的参数文本
    for m in re.finditer(r"\\[a-zA-Z@]+\*?", masked):
        blank_match(m)
    return "".join(buf)


def math_mask(text: str) -> str:
    r"""只保留**数学区**（行内/行间/数学环境），其余换成空格。

    为什么要单独一层：省略号 `...`、集合竖线 `|`、`\mathbf` 这些规则的**判据就在数学里**，
    用文本层（把数学挖空）去扫永远扫不到 —— 第一版就栽在这里。
    """
    buf = [" "] * len(text)
    for i, ch in enumerate(text):
        if ch == "\n":
            buf[i] = ch

    def keep(a: int, b: int) -> None:
        for i in range(a, b):
            buf[i] = text[i]

    for a, b in math_spans(text):
        keep(a, b)
    return "".join(buf)


def raw_mask(text: str) -> str:
    r"""只挖掉**注释与 verbatim**，命令名与参数都留着。

    命令型规则（`\caption{…\\…}`、`表\ref{…}`、`\includegraphics{…jpg}`）必须用这一层：
    文本层会把命令名和键参数一起挖空，规则就永远匹配不到了。
    """
    buf = list(text)

    def blank(a: int, b: int) -> None:
        for i in range(a, b):
            if buf[i] != "\n":
                buf[i] = " "

    for pat in (r"\\begin\{verbatim\}.*?\\end\{verbatim\}",
                r"\\begin\{lstlisting\}.*?\\end\{lstlisting\}",
                r"\\verb\*?\|[^|]*\|"):
        for m in re.finditer(pat, text, re.S):
            blank(m.start(), m.end())
    masked = "".join(buf)
    for m in re.finditer(r"(?<!\\)%.*", masked):
        blank(m.start(), m.end())
    return "".join(buf)


def _line_col(text: str, at: int) -> tuple[int, int]:
    line = text.count("\n", 0, at) + 1
    col = at - (text.rfind("\n", 0, at) + 1) + 1
    return line, col


# ── 规则与命中 ───────────────────────────────────────────────────────────

@dataclass
class Hit:
    rule_id: str
    title: str
    basis: str
    line: int
    col: int
    start: int
    end: int
    text: str
    advice: str
    fixable: bool = False
    replacement: str | None = None


@dataclass
class Report:
    hits: list[Hit] = field(default_factory=list)
    lang: str = "zh"

    @property
    def n(self) -> int:
        return len(self.hits)

    def by_rule(self) -> dict[str, list[Hit]]:
        out: dict[str, list[Hit]] = {}
        for h in self.hits:
            out.setdefault(h.rule_id, []).append(h)
        return out


@dataclass
class Rule:
    id: str
    title: str
    basis: str
    pattern: str
    advice: str
    langs: tuple[str, ...] = ("zh", "en")
    #: `text`（默认，扫正文文本）/ `math`（只扫数学区）/ `raw`（命令型规则，不挖命令）
    scope: str = "text"
    #: 命中量大、需逐条判断（经验书原话「并非修改所有匹配项，稍作观察」）—— 报告里单独提示
    noisy: bool = False
    #: 更细的判据：`(m, tex) -> bool`，为假时这条命中**只报不改**（如脚注里的标点）
    fixable_when: object = None
    fixable: bool = False
    #: 由匹配串算出替换文本（可自动改的规则才需要）
    replace: object = None
    #: 命中后再过一道判据（返回 True 才算命中），用于压噪声
    accept: object = None
    #: 只在楷体区间（quotation / quote 环境，或 `\kaishu` 开关作用域）内算命中
    within_kaishu: bool = False


def default_action(rule: "Rule") -> str:
    """这条规则的**默认动作**（纯函数；B-288 任务卡「免报清单」用）。

    返回值与 `docs/PROOF_RULES.md` §二「默认动作」列的**首个词**同义，共五种：

    * `初排自动改` —— 在初排白名单 `INITIAL_LAYOUT_FIXABLE` 里（14 条）；
    * `初排自动搬位` —— ZH-10/ZH-11：不是替换而是按命中 span 搬标点
      （`build._PROOF_AUTOFIX_IDS` 另含这两条；它们**不在** `fixable`/白名单里，
      所以必须单列，不能按 `fixable` 推）；
    * `仅 --fix` —— 可修但默认初排不启用（当前为空集，保留分支给后批）；
    * `只报（噪声大）` —— `noisy=True` 且不在白名单里（ZH-07）；
    * `只报` —— 其余（含 `fixable=False` 的语义类规则）。

    判据顺序不能换：ZH-08 同时 `noisy=True` 又在初排白名单里，表里是「初排自动改」。
    `INITIAL_LAYOUT_FIXABLE` 定义在本模块后半段，函数体内按全局名读取（调用时已注册）。
    """
    if rule.id in ("ZH-10", "ZH-11"):
        return "初排自动搬位"
    if rule.id in INITIAL_LAYOUT_FIXABLE:
        return "初排自动改"
    if rule.noisy:
        return "只报（噪声大）"
    if rule.fixable:
        return "仅 --fix"
    return "只报"


# 半角 → 全角（只在「两侧都是中文」时用，所以不会被用在英文/数字语境里）
_HALF2FULL = {",": "，", ".": "。", ":": "：", ";": "；", "?": "？", "!": "！"}


def _adjacent_cjk_replace(pat_half: str):
    r"""造一个「中文+半角标点+中文」→ 全角的替换函数。

    ⚠️ 两侧的 ASCII 空格要**一起吃掉**：半角改全角之后留一个空格会变成「集合， 公式集」
    这种多余间隔（实测 2023_05_Ming_Tu 就是这样）。中文之间本来不需要空格。
    """
    def _rep(m: re.Match) -> str:
        s = m.group(0)
        i = s.index(pat_half)
        left = s[:i].rstrip(" \t")
        right = s[i + 1:].lstrip(" \t")
        return left + _HALF2FULL[pat_half] + right
    return _rep


#: 英文稿里的中文/印刷体标点 → 本刊（LaTeX）写法。手册 §英文文章 2：
#: 英文引号用数字键 1 左边那个键（`` ` `` 与 `'`），破折号用三条短横，省略号用 \ldots。
#: 实测（2026-10-15，13 份英文稿）：`’` 1107 个 / `“`+`”` 各 439 / `‘` 197 / `…` 16 / `—` 16。
EN_TYPO_MAP = {
    "’": "'",        # 弯撇号（所有格、缩写）—— 手册点名「he’s 这种间隔便是明显的错误」
    "‘": "`",        # 左单引号
    "“": "``",       # 左双引号
    "”": "''",       # 右双引号
    "—": "---",      # 长破折号 → 三条短横
    "…": "\\ldots{}",  # 省略号 → \ldots{}（带空组，免得与后面的空格叠成两个）
}


def _en_typo_replace(m: re.Match) -> str:
    return EN_TYPO_MAP.get(m.group(0), m.group(0))


def _footnote_spans(text: str) -> list[tuple[int, int]]:
    r"""所有 `\footnote{…}` 的区间（花括号配对，能应付嵌套）。"""
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\\footnote\{", text):
        brace = m.end() - 1
        end = _match_brace(text, brace)
        if end > 0:
            spans.append((m.start(), end))
    return spans


def _in_any(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


#: 常见**固定缩写**（不是人名首字母）：`U.S.` / `Q.E.D.` / `P.S.` 这类不该按 ZH-19 报。
#: 依据：经验书原话「若为英文缩写如 e.g. 则忽略」；只读实测（2026-10-18，rules-docs Phase0）——
#: 稿件仓约稿成品层仍有 48 处 `[A-Z].[A-Z]` 残留，其中 `U.S` / `M.A` / `Q.E` 反复出现，
#: 而 `H.T.`（Colebrooke）/`H.P.`（Grice）才是要报的人名首字母。
ZH19_ABBREV_DENY = frozenset({
    "U.S", "U.K", "U.N", "A.D", "B.C", "M.A", "B.A", "Q.E.D", "P.S", "P.R.C",
})


def _is_cjk(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch <= "\u9fff"


def _optional_arg_spans(tex: str) -> list[tuple[int, int]]:
    r"""命令的可选参数 `[...]` 区间（如 `\item[模仿-1：]`）。

    ZH-08 的自动改要跳过这些位置：`模仿-1` 是 enumerate 标签，不是中文连接号；
    期 87 sub4 人工成品在 `\item[模仿-1：]` 处保留 ASCII 短杠（2/2）。
    """
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\\[a-zA-Z@]+\*?\s*\[[^\]]*\]", tex):
        open_at = tex.find("[", m.start(), m.end())
        if open_at >= 0:
            spans.append((open_at, m.end()))
    return spans


#: 这些命令的花括号参数是**技术值**（长度/字号/日期/表格线/键），ZH-08 一律不碰。
#: 与 `_KEY_ARGS` 有重叠是有意的：`_KEY_ARGS` 只服务 visible_mask，这里给 ZH-08 的
#: 判据单独一份（数学/URL/日期字段的语义见 2026-10-28 冻结 spec §2）。
_ZH08_TECH_ARGS = frozenset({
    "vspace", "hspace", "cline", "zihao", "receiveddate", "date",
    "setlength", "addtolength", "rule", "raisebox", "scalebox", "resizebox",
    "fontsize", "linespread", "setcounter", "addtocounter", "arraystretch",
    "tabcolsep", "parindent", "textwidth", "linewidth", "columnwidth",
    "baselineskip", "includegraphics", "input", "include", "bibliography",
    "addbibresource", "url", "href", "cite", "parencite", "textcite",
    "footcite", "nocite", "ref", "eqref", "label", "pageref", "bibitem",
    "documentclass", "usepackage",
})

#: 裸 URL / DOI / 邮箱 / 文件名 / 英文页码（`pp.130-133`）/ ISBN：保持 ASCII、不报。
_ZH08_PROTECT = (
    re.compile(r"https?://[^\s{}]+"),
    re.compile(r"\b(?:doi:\s*)?10\.\d{4,9}/[^\s{}]+", re.I),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b[A-Za-z0-9_./-]+\.(?:bib|tex|pdf|png|jpe?g|gif|svg|eps|"
               r"docx?|xlsx?|csv|py|json|ya?ml|md|cls|sty|bst)\b"),
    re.compile(r"\bpp?\.\s*\d+[ \t]*-[ \t]*\d+"),
    re.compile(r"\bISBN(?:-1[03])?:?\s*[\d-]+"),
)

#: 宏定义 `\newcommand{\receiveddate}{2024-12-04}`：日期是模板字段，整条定义保持。
_ZH08_DEFS = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)"
    r"\*?\s*\{?\\[A-Za-z@]+\}?\s*(?:\[[^\]]*\])?\s*\{|"
    r"\\def\s*\\[A-Za-z@]+\s*\{")

#: 纯数字日期 `2021-06-16`（手册：日期用短横 `\edash`；中文稿里仍要改）。
_ZH08_DATE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[ \t]{0,3}-[ \t]{0,3}(?:0[1-9]|1[0-2])"
    r"[ \t]{0,3}-[ \t]{0,3}(?:0[1-9]|[12]\d|3[01])(?!\d)")

#: ZH-09 的页码区间（单/双短杠都算）：ZH-08 不与之抢同一根短杠。
_ZH09_PAGE = re.compile(
    r"第[ \t]{0,3}(\d+)[ \t]{0,3}-{1,2}[ \t]{0,3}(\d+)[ \t]{0,3}页")


@lru_cache(maxsize=32)
def _zh08_spans(tex: str) -> tuple[tuple[int, int], ...]:
    r"""ZH-08 一律**不碰**的区间：可选参数 / 技术参数 / URL / DOI / 文件名 / 页码。"""
    spans: list[tuple[int, int]] = list(_optional_arg_spans(tex))
    for m in re.finditer(r"\\([A-Za-z@]+)\*?\s*\{", tex):
        if m.group(1) not in _ZH08_TECH_ARGS:
            continue
        end = _match_brace(tex, m.end() - 1)
        if end > 0:
            spans.append((m.start(), end))
    for m in _ZH08_DEFS.finditer(tex):
        end = _match_brace(tex, m.end() - 1)
        if end > 0:
            spans.append((m.start(), end))
    for pat in _ZH08_PROTECT:
        spans.extend(m.span() for m in pat.finditer(tex))
    spans.extend(m.span() for m in _ZH09_PAGE.finditer(tex))
    return tuple(spans)


@lru_cache(maxsize=16)
def _zh08_visible(tex: str) -> str:
    """缓存 `visible_mask`：只用于判断短杠是否紧邻数学/被屏蔽命令。"""
    return visible_mask(tex)


def _zh08_left(tex: str, at: int) -> str:
    """`at` 往左跳过 0–3 个空格/制表符后的字符；超过 3 个或到行首返回空串。"""
    i, skipped = at, 0
    while i >= 0 and tex[i] in " \t" and skipped < 3:
        i -= 1
        skipped += 1
    if i < 0 or tex[i] in " \t\r\n":
        return ""
    return tex[i]


def _zh08_run(tex: str, hy: int) -> int:
    """短杠所在连续 `-` 游程的长度（`--`/`---`/`-->` 都不是 ZH-08 的地盘）。"""
    n, i = 1, hy - 1
    while i >= 0 and tex[i] == "-":
        n += 1
        i -= 1
    i = hy + 1
    while i < len(tex) and tex[i] == "-":
        n += 1
        i += 1
    return n


def _zh08_cjk_line_near(tex: str, hy: int, radius: int = 15) -> bool:
    r"""同一行、短杠前后 `radius` 字内是否有汉字（纯日期/编号的语境判据）。"""
    a = tex.rfind("\n", 0, hy) + 1
    b = tex.find("\n", hy)
    if b < 0:
        b = len(tex)
    return any(_is_cjk(c) for c in tex[max(a, hy - radius):min(b, hy + radius + 1)])


def _zh08_accept(m: re.Match, tex: str) -> bool:
    r"""ZH-08 新口径（2026-10-28 用户拍板 A）的「报不报」判据。

    口径（冻结 spec §2，`lang=zh` 才跑）：
    - `-` 左右任一紧邻（0–3 空格/制表符）小写 ASCII 字母 → 保持、不报；
    - `-` 两侧都是汉字 → 不动、不报（用 accept 静默；人名/复合词无法可靠区分）；
    - 其余（大写/缩略语、数字、日期、编号、汉字+数字）→ `\edash `；
    - 数学区/`raw_mask` 屏蔽区、可选参数、`\vspace`/`\cline`/`\receiveddate` 等技术参数、
      URL/DOI/文件名/`pp.130-133` 页码 → 保持、不报；
    - `--`（游程 ≥2）不归 ZH-08：中文页码范围由 ZH-09 扩 `-{1,2}` 处理，其余一律不动。
    """
    hy = m.start()
    if hy < 0 or hy >= len(tex) or tex[hy] != "-":
        return False
    if _in_any(hy, _zh08_spans(tex)) or _zh08_run(tex, hy) != 1:
        return False
    left = _zh08_left(tex, hy - 1)
    right = tex[m.end()] if m.end() < len(tex) and tex[m.end()] not in " \t\r\n" else ""
    if not left or not right:
        return False
    if ("a" <= left <= "z") or ("a" <= right <= "z"):
        return False
    if _is_cjk(left) and _is_cjk(right):
        return False
    if _is_cjk(left) or _is_cjk(right):
        return True
    if any(a <= hy < b for a, b in (x.span() for x in _ZH08_DATE.finditer(tex))):
        return True
    if left.isdigit() and right.isdigit():
        return _zh08_cjk_line_near(tex, hy)
    if left.isupper() and right.isdigit():      # `A-105` / `2021-06-16` 的邻接形态
        return True
    if left.isdigit() and right.isupper():      # `L101-L110` 一类
        return _zh08_cjk_line_near(tex, hy)
    return False


def _zh08_math_adjacent(m: re.Match, tex: str) -> bool:
    r"""紧邻数学区/被 `visible_mask` 屏蔽的命令（`$\phi$-世界`、`\ref{x}-式`）。

    这类命中的语义要人看（spec §2-4：数学模式内保持 ASCII），所以**只报不改**。
    """
    vis = _zh08_visible(tex)
    hy = m.start()
    left = _zh08_left(vis, hy - 1)
    right = vis[m.end()] if m.end() < len(vis) and vis[m.end()] not in " \t\r\n" else ""
    return left == "\x00" or right == "\x00"


def _zh08_fixable(m: re.Match, tex: str) -> bool:
    return _zh08_accept(m, tex) and not _zh08_math_adjacent(m, tex)


def _zh08_connector_replace(m: re.Match) -> str:
    r"""把命中的半角短杠（连同其后 0–3 个空格）换成**短横 `\edash `**。

    2026-10-28 口径：短横 `\edash` 用于人名/编号/日期/字母序号；双短横 `\cndash`
    **只给页码范围**（ZH-09）；一字线 `—` 永不改。`\edash` 后保留一个空格（LaTeX 控制词
    会吞空格，视觉与手册源码 `表2\edash 9` 一致）。旧实现只返回 `group(1)`、右锚定匹配
    会吞掉右侧汉字（`2-群胚` → `2\edash 胚`）；现在 pattern 只匹配短杠本身，不再有分组。
    """
    return "\\edash "


def _zh19_abbrev_token(tex: str, start: int, end: int) -> str:
    """命中所在的整串大写缩写（只含 `A-Z` 与 `.`），如 `U.S` / `Q.E.D` / `J.N`。"""
    a, b = start, end
    while a > 0 and tex[a - 1].isascii() and (tex[a - 1].isupper() or tex[a - 1] == "."):
        a -= 1
    while b < len(tex) and tex[b].isascii() and (tex[b].isupper() or tex[b] == "."):
        b += 1
    return tex[a:b].rstrip(".")


def _zh19_accept(m: re.Match, tex: str) -> bool:
    r"""ZH-19 的降噪判据：固定缩写（`U.S.`/`Q.E.D.`/`P.S.` 等）**不报**。

    人名缩写（`J.N.` / `H.T.` / `H.P.`）才报。判不准的一律保留（报出来让人看）：
    本规则**只报不改**，多报的代价小于漏报。
    """
    token = _zh19_abbrev_token(tex, m.start(), m.end())
    if token in ZH19_ABBREV_DENY:
        return False
    # 「缩写 + 括号数字」是标号（`P.S.(9)`），不是人名首字母
    if tex[m.end():m.end() + 1] in ("(", "（"):
        return False
    return True


def _line_is_ascii(m: re.Match, tex: str, threshold: float = 0.70) -> bool:
    r"""命中所在的**那一行**是否以 ASCII 字母/空白为主。

    英文规则的用途是管英文正文；可英文模板里还会打印**中文题名与摘要**
    （`\mytitleCN` / `\myabstractCN`），那些位置出现中文标点是**设计如此**。
    不加这条判据，EN-03「英文里混入中文标点」会在 13 份英文稿上报出 1809 处假阳性
    （2026-10-15 实测）。
    """
    start = tex.rfind("\n", 0, m.start()) + 1
    end = tex.find("\n", m.end())
    if end < 0:
        end = len(tex)
    line = tex[start:end]
    letters = sum(1 for c in line if c.isascii() and (c.isalpha() or c.isspace()))
    return letters / max(len(line), 1) >= threshold


def _zh11_closer(mask: str, opener: int, math: str = "") -> int:
    r"""`mask[opener]` 是开括号时，返回与它配对的右括号位置；没有则 -1。

    `math` 非空时只看**数学区外**的括号（`math_mask` 上非空白处即数学区），
    避免正文的 `，\cite` 被数学区里的 `（` 误豁免（B-273 fix round）。
    """
    depth = 0
    for k in range(opener + 1, len(mask)):
        if math and math[k] not in " \n":
            continue
        ch = mask[k]
        if ch in _ZH11_PAREN_OPEN:
            depth += 1
        elif ch in _ZH11_PAREN_CLOSE:
            if depth == 0:
                return k
            depth -= 1
    return -1


def _zh11_accept(m: re.Match, tex: str) -> bool:
    return _zh11_accept_at(tex, m.start(), m.end())


def _zh11_accept_at(tex: str, pos: int, cmd_end: int) -> bool:
    r"""ZH-11 括号豁免（手册 §常规 5.2 正例 `（\ct{a6}，第8\cndash 12页）`）。

    逗号与后面紧跟的 `\cite` 族命令如果**整体处于同一对**中文/英文括号内，说明是
    「作者（年份，\cite{…}）」式的括注写法，不该报；正文句读照报。
    括号配对在 `raw_mask`（注释/verbatim 已挖空）上算，嵌套按最近未闭合开括号；
    括号未闭合时不豁免（避免把漏括号的正文静默掉）。`--fix` 的 mover 复用同一判据。
    """
    mask = raw_mask(tex)
    math = math_mask(tex)
    if _zh12_in_math(mask, pos):        # 数学区里的 `，\cite` 不适用这条中文口径
        return True
    depth = 0
    opener = -1
    k = pos - 1
    while k >= 0:
        if math[k] not in " \n":         # 数学区里的括号不参与配对（fix round）
            k -= 1
            continue
        ch = mask[k]
        if ch in _ZH11_PAREN_CLOSE:
            depth += 1
        elif ch in _ZH11_PAREN_OPEN:
            if depth == 0:
                opener = k
                break
            depth -= 1
        k -= 1
    if opener < 0:
        return True
    closer = _zh11_closer(mask, opener, math)
    return not (closer > 0 and closer > cmd_end)


#: ZH-11 括号豁免用的中文/英文圆括号。
_ZH11_PAREN_OPEN = "(（"
_ZH11_PAREN_CLOSE = ")）"


# ── ZH-14（B-278）：集合里只改**单个**竖线；`\|` 与成对 `|…|`（绝对值）不动 ──
def _zh14_accept(m: re.Match, tex: str) -> bool:
    r"""只有**恰好一个**未转义竖线时才可自动改；成对竖线/`\|` 一律跳过（宁可不改）。"""
    return len(re.findall(r"(?<!\\)\|", m.group(0))) == 1


def _zh14_replace(m: re.Match) -> str:
    return re.sub(r"(?<!\\)\|", r"\\mid ", m.group(0), count=1)


# ── ZH-23（B-278）：带括号的引用组，句末标点应放在括号之前 ─────────────────
#: 引用组里的 cite 族命令；`\pct/\pcp/\pcpp` 由模板自带括号（`（\ct{…}）`）。
_ZH23_CITE_CMD = r"\\(?:ct|pct|pcp|pcpp|parencite|cite|footcite|textcite)\{"
_ZH23_GROUP_RE = re.compile(
    r"（[^（）]*?" + _ZH23_CITE_CMD + r"[^{}]*\}[^（）]*?）([。；])")
_ZH23_BARE_RE = re.compile(r"\\(?:pct|pcp|pcpp)\{[^{}]*\}([。；])")

#: B-280 守卫 A（ZH-23）：组前若以这些及物动词/引导语收尾，引用组是它们的宾语
#: （`见（…cite…）。`），句末标点不能前移 → 跳过不报。按 token 精确后缀匹配。
_ZH23_GUARD_A_WORDS: tuple[str, ...] = ("参见", "详见", "例如", "譬如", "诸如", "即", "见")
#: 回看窗口：组左括号前最多取 6 个非空白字符（允许其间夹顿号/空格）。
_ZH23_GUARD_A_WINDOW = 6


def _zh23_guard_a(mask: str, start: int) -> bool:
    r"""B-280 守卫 A：组前 ≤6 个非空白字符以「见/参见/详见/例如/譬如/诸如/即」收尾。

    「意见（…）」里的「见」是名词、不是及物动词，不触发守卫。
    """
    chars: list[str] = []
    k = start - 1
    while k >= 0 and len(chars) < _ZH23_GUARD_A_WINDOW:
        if not mask[k].isspace():
            chars.append(mask[k])
        k -= 1
    before = "".join(reversed(chars))
    for word in _ZH23_GUARD_A_WORDS:
        if not before.endswith(word):
            continue
        if word == "见" and before.endswith("意见"):
            continue
        return True
    return False


def _zh23_sentence_bounds(mask: str, pos: int,
                          spans: list[tuple[int, int]]) -> tuple[int, int]:
    r"""`pos` 所在句的 `[起, 止)`（句读按 。！？与换行，跳过脚注内部）。"""
    start = 0
    k = pos - 1
    while k >= 0:
        if not _in_any(k, spans) and mask[k] in "。！？\n":
            start = k + 1
            break
        k -= 1
    end = len(mask)
    k = pos
    while k < len(mask):
        if not _in_any(k, spans) and mask[k] in "。！？\n":
            end = k + 1
            break
        k += 1
    return start, end


def _zh23_hits(tex: str, mask: str) -> list[tuple[int, int]]:
    r"""ZH-23（B-278）「带括号引用组的句末标点应在括号之前」扫描。

    `（…\cite{…}…）` / `（…\ct{…}…）`（中文全角括号）、以及自带括号形态的
    `\pct{…}` / `\pcp{…}{…}` / `\pcpp{…}{…}{…}` **紧跟 `。`/`；`** → 命中，
    `replacement` 把该标点搬到整组之前（`甲说（\parencite{k}）。` → `甲说。（\parencite{k}）`）。
    排除：英文半角 `(...)`、不加括号的 `\cite{…}。`、`：` 场景、组内已有句末句读、
    同句出现两组及以上（整句跳过，与手册 4.2「同句两处脚注」同一纪律）、
    行首/句读后的独立括注（前面没有正文可承接搬过来的标点）。正文与脚注内同判。

    B-280 守卫：A=组前 ≤6 字以「见/参见/详见/例如/譬如/诸如/即」收尾（引用组作宾语）→ 跳过；
    B=`；` 收尾（分句归属会滑向下一分句）→ 跳过。ZH-23 自 B-280 起**只报不改**。
    """
    spans = _footnote_spans(mask)
    cands: list[tuple[int, int, str]] = []
    for m in _ZH23_GROUP_RE.finditer(mask):
        cands.append((m.start(), m.end(), "group"))
    for m in _ZH23_BARE_RE.finditer(mask):
        cands.append((m.start(), m.end(), "bare"))
    cands.sort()
    if not cands:
        return []
    # 同句多组：整句跳过（先按句聚合再逐条筛）。注意要数**全部**带括号引用组，
    # 不能只数「后面紧跟句读」的候选：`甲（\ct{a}）说（\ct{b}）。` 只有后一组是候选，
    # 但同句有两组 → 按手册 4.2 的保守口径整句不动。
    group_any = re.compile(r"（[^（）]*?" + _ZH23_CITE_CMD + r"[^{}]*\}[^（）]*?）")
    group_spans = [(m.start(), m.end()) for m in group_any.finditer(mask)]
    counts: dict[tuple[int, int], int] = {}
    for gs, _ge in group_spans:
        bounds = _zh23_sentence_bounds(mask, gs, spans)
        counts[bounds] = counts.get(bounds, 0) + 1
    for start, _end, kind in cands:
        if kind == "bare" and not any(gs <= start < ge for gs, ge in group_spans):
            bounds = _zh23_sentence_bounds(mask, start, spans)
            counts[bounds] = counts.get(bounds, 0) + 1
    out: list[tuple[int, int]] = []
    for start, end, _kind in cands:
        if counts[_zh23_sentence_bounds(mask, start, spans)] > 1:
            continue
        # 守卫 B（B-280）：`；` 收尾 → 跳过（引用归属会滑向下一分句）。
        if mask[end - 1] == "；":
            continue
        body = mask[start:end - 1]
        if "。" in body or "！" in body or "？" in body:   # 组内已有句末句读
            continue
        left = start - 1
        while left >= 0 and mask[left] in " \t":
            left -= 1
        if left < 0 or mask[left] in "\n。！？；：":
            continue
        # 守卫 A（B-280）：组前是「见/参见/详见/例如/即…」→ 跳过（引用组作宾语）。
        if _zh23_guard_a(mask, start):
            continue
        out.append((start, end))
    return out

RULES: list[Rule] = [
    # ── 中文稿件：半角标点混入（经验书「预校正·中文」逐条）──────────────
    Rule("ZH-01", "中文之间的半角逗号", "经验书 预校正·中文（检索英文句号逗号）",
         rf"[{CJK}][ \t]{{0,3}},[ \t]{{0,3}}[{CJK}]", "改为全角「，」", langs=("zh",), fixable=True, fixable_when=lambda mm, tx: not _in_any(mm.start(), _footnote_spans(tx)),
         replace=_adjacent_cjk_replace(",")),
    Rule("ZH-02", "中文之间的半角句号", "经验书 预校正·中文",
         rf"[{CJK}][ \t]{{0,3}}\.[ \t]{{0,3}}[{CJK}]", "改为全角「。」", langs=("zh",), fixable=True, fixable_when=lambda mm, tx: not _in_any(mm.start(), _footnote_spans(tx)),
         replace=_adjacent_cjk_replace(".")),
    Rule("ZH-03", "中文之间的半角冒号", "经验书 预校正·中文（检索 : 等英文符号）",
         rf"[{CJK}][ \t]{{0,3}}:[ \t]{{0,3}}[{CJK}]", "改为全角「：」", langs=("zh",), fixable=True, fixable_when=lambda mm, tx: not _in_any(mm.start(), _footnote_spans(tx)),
         replace=_adjacent_cjk_replace(":")),
    Rule("ZH-04", "中文之间的半角分号", "经验书 预校正·中文",
         rf"[{CJK}][ \t]{{0,3}};[ \t]{{0,3}}[{CJK}]", "改为全角「；」", langs=("zh",), fixable=True, fixable_when=lambda mm, tx: not _in_any(mm.start(), _footnote_spans(tx)),
         replace=_adjacent_cjk_replace(";")),
    Rule("ZH-05", "中文之间的半角问号/叹号", "经验书 预校正·中文",
         rf"[{CJK}][ \t]{{0,3}}[?!][ \t]{{0,3}}[{CJK}]", "改为全角「？/！」", langs=("zh",), fixable=True, fixable_when=lambda mm, tx: not _in_any(mm.start(), _footnote_spans(tx)),
         replace=lambda m: m.group(0).replace("?", "？").replace("!", "！")),
    Rule("ZH-06", "顿号后紧跟引号", "经验书 预校正·中文（检索 、‘ 、\" 、“）",
         r"、[ \t]{0,3}(?=[‘“\"'])", "顿号与引号之间不该有顿号：删掉顿号", langs=("zh",),
         fixable=True, replace=lambda m: ""),
    Rule("ZH-07", "中文旁的半角括号", "经验书 预校正·中文（检索英文括号）",
         rf"(?:[{CJK}][ \t]{{0,3}}\(|\)[ \t]{{0,3}}[{CJK}])",
         "确认是英文括注（保留）还是漏改的中文括号（改全角）", langs=("zh",), noisy=True),
    Rule("ZH-08", "中文旁的半角短杠", "经验书 预校正·中文（检索英文短杠）＋手册·连字符",
         r"-(?:[ \t]{0,3})",
         "按手册分类：短横 `\\edash`（人名/编号/日期/字母序号）、双短横 `\\cndash`（**只有页码范围**）"
         "或一字线「—」；小写英文邻、汉字-汉字、数学/命令参数保持 ASCII。"
         "2026-10-28 起默认初排按此口径自动改",
         langs=("zh",), noisy=True, fixable=True, accept=_zh08_accept,
         fixable_when=_zh08_fixable, replace=_zh08_connector_replace),
    Rule("ZH-09", "「第N-N页」缺双短横命令", "经验书 预校正·中文（检索 第\\d*-\\d*页）＋手册·连字符",
         r"第[ \t]{0,3}(\d+)[ \t]{0,3}-{1,2}[ \t]{0,3}(\d+)[ \t]{0,3}页", "范围页码用双短横：第8\\cndash 12页",
         langs=("zh",), fixable=True,
         replace=lambda m: f"第{m.group(1)}\\cndash {m.group(2)}页"),

    # ── 脚注与引文的位置（手册 §校对规范·常规 4 / 5）────────────────────
    Rule("ZH-10", "脚注在逗号/句号之后（应在标点之前）",
         "手册 §校对规范·常规 4.1（脚注在逗号处放逗号前、句号处放句号后）",
         r"([，,])[ \t]{0,3}(\\footnote\{)", "把脚注移到该标点之前", langs=("zh",), scope="raw"),
    Rule("ZH-11", "引文在逗号之后（应在标点之前）",
         "手册 §校对规范·常规 5.1–5.2（文献引用位置与脚注同理；括注写法如"
         "「（Sarah Moss，\\parencite{…}）」不报）",
         r"([，,])[ \t]{0,3}(\\(?:parencite|cite|footcite|textcite)\{)",
         "把引用移到该标点之前（中文括号内的括注已豁免）", langs=("zh",), scope="raw",
         accept=_zh11_accept),
    # ZH-12 用专用扫描（pattern 留空）：`\footnote{…}` 的花括号可能嵌套，
    # 正则 `[^}]*` 会在第一个 `}` 处截断；判据与替换见 `_zh12_hits()` / `check()`。
    Rule("ZH-12", "句末后的脚注紧跟汉字（间隔过小）",
         "手册 §校对规范·常规 4.3（需要 \\, 调间隔）",
         "", "在脚注与汉字之间加 \\,", langs=("zh",), scope="raw", fixable=True),

    # ── 数学环境（手册 §数学环境）──────────────────────────────────────
    Rule("ZH-13", "省略号用三个英文点", "手册 §数学环境·省略号（按高度选 \\ldots / \\cdots）",
         r"(?<!\.)\.\.\.(?!\.)", "数学里用 \\ldots（下标）或 \\cdots（运算符之间）",
         langs=("zh", "en"), scope="math"),
    Rule("ZH-14", "集合里的竖线应使用 \\mid", "手册 §数学环境·竖线的间隔",
         r"\\\{[^{}]*\|[^{}]*\\\}", "集合中的竖线用 \\mid", langs=("zh", "en"),
         fixable=True, accept=_zh14_accept, replace=_zh14_replace, scope="math"),
    Rule("ZH-15", "\\mathbf 加粗数学", "手册 §数学环境·加粗（建议 \\pmb / \\boldsymbol）",
         r"\\mathbf\{", "改用 \\pmb{} 或 \\boldsymbol{}", langs=("zh", "en"), scope="math"),

    # ── 引用与版式（手册 §排版指南）────────────────────────────────────
    Rule("ZH-16", "图表/式/中文序号引用缺波浪号间隔",
         "手册 §间隔 4（如「表~\\ref{table1}~所示」）＋手册·章节/定理序号再提及"
         "（「第~\\ref{…}~节」，中文文本里的序号引用需要波浪号增加间隔）",
         r"(?:(?:表|图|式|第|章|节|条|定理|引理|命题|定义|推论)[ \t]{0,3}\\(?:eq)?ref\{"
         r"|(?<![图表式第章节条理题义论])\\(?:eq)?ref\{[^}]*\}(?=节|条|页|所示))",
         "补波浪号：表~\\ref{...}~所示、第~\\ref{...}~节、式~\\eqref{...}",
         langs=("zh",), scope="raw"),
    Rule("ZH-17", "\\caption 里用 \\\\ 换行", "手册 §冷门问题汇总 1（应使用 \\newline）",
         r"\\caption(?:\[[^\]]*\])?\{[^{}]*\\\\", "caption 内换行用 \\newline",
         langs=("zh", "en"), scope="raw"),
    Rule("ZH-18", "楷体环境里的英文/数字未调正",
         "手册 §校对规范·常规 3（楷体环境中的英文、数字、标点用 \\textup 调正）",
         r"", "用 \\textup{} 包住（\\ct 等命令自带）", langs=("zh",)),
    Rule("ZH-19", "人名缩写之间缺空格", "经验书 预校正·共通（检索 \\.[A-Z]，人名中要有空格）",
         r"[A-Z]\.[A-Z]", "人名缩写之间加空格：J. N. （固定缩写 U.S./Q.E.D. 等不报；本规则只报不改）",
         langs=("zh", "en"), accept=_zh19_accept),

    Rule("ZH-20", "楷体环境里用 \\textbf 加粗", "手册 §校对规范·常规 3（楷体环境下的加粗用 \\textsf）",
         r"\\textbf\{", "楷体环境（quotation / quote / \\kaishu 开关）内改用 \\textsf{}",
         langs=("zh",), scope="raw", within_kaishu=True),
    # ZH-21 用专用扫描（pattern 留空）：`\footnote{…}` 可能嵌套，且要把句末标点搬到
    # 命令之前，判据与替换见 `_zh21_hits()` / `check()`。
    Rule("ZH-21", "脚注在句末标点前（应放标点后）",
         "手册 §校对规范·常规 4.1（脚注若在句号处则放于句号后；`；` 视同 `。`）",
         "", "把句末标点移到脚注之前：文本。\\footnote{注}", langs=("zh",),
         scope="raw", fixable=True),
    # ZH-22 用专用扫描（pattern 留空）：要看环境配对，见 `_zh22_hits()`。
    Rule("ZH-22", "表格/浮动体内的 \\footnote 会被吞",
         "手册 §校对规范·常规 4.4（表格脚注需 \\footnotemark + 环境后 \\footnotetext）",
         "", "改用 \\footnotemark{} + 环境后的 \\footnotetext{}", langs=("zh", "en"),
         scope="raw"),
    # ZH-23 用专用扫描（pattern 留空）：要按括号配对、按句聚合，见 `_zh23_hits()`。
    # Q4（FIX-20261112）v2：由 B-280 的**只报**升级为**初排自动改**（守卫 A/B 原样保留：
    # 见/参见/详见/例如/譬如/诸如/即 前置跳过、`；` 收尾跳过、同句两组整句跳过、
    # 组内已有句末句读不动、冒号不作句末标点）。替换 = 句末标点搬到整组括号之前。
    Rule("ZH-23", "带括号引用组的句末标点在括号之后",
         "手册 §校对规范·常规 4.1/5.1–5.2（脚注与文献引用都放在逗号前、句号后）",
         "", "把句末标点移到括号之前：文本。（\\parencite{…}）", langs=("zh",),
         scope="raw", fixable=True),

    # ── 英文稿件（手册 §英文文章的特殊规范；经验书 预校正·英文）──────────
    Rule("EN-01", "年份前的半角括号缺空格", "经验书 预校正·英文（检索 [a-zA-Z]\\(\\d）",
         r"[a-zA-Z]\(\d", "作者与年份之间加空格：John (2009)", langs=("en",),
         fixable=True, replace=lambda m: m.group(0)[0] + " (" + m.group(0)[2]),
    Rule("EN-02", "英文旁的冒号缺空格", "经验书 预校正·英文（检索 :[a-zA-Z]）",
         r":[a-zA-Z]", "冒号后加空格", langs=("en",), fixable=True,
         replace=lambda m: ": " + m.group(0)[1]),
    Rule("EN-03", "英文里的弯引号/中文标点（Word 粘贴的痕迹）",
         "手册 §英文文章 2（英文引号用 ` 与 '；破折号 ---）＋经验书 预校正·英文",
         r"[“”‘’—…]",
         "按本刊写法替换：’→'  “→``  ”→''  ‘→`  —→---  …→\\ldots",
         langs=("en",), fixable=True, replace=_en_typo_replace, accept=_line_is_ascii),
    Rule("EN-05", "页码缺 \\, 间隔", "经验书 预校正·英文（pp.\\,x--x / p.\\,x）",
         r"\b(pp?\.)[ \t]+(\d)", "缩小间隔：\\1\\,\\2", langs=("en",)),
    Rule("EN-06", "章节标题大小写待核",
         "手册 §英文文章 3（Section 实词大写；subsection 及以下仅句首大写）",
         "", "逐条核对标题大小写（section 实词大写；subsection 走句首式 fixer）",
         langs=("en",), scope="raw"),

    # ── 通用 ───────────────────────────────────────────────────────────
    Rule("ALL-01", "位图插图（非矢量）", "手册 §插入图片（核心思想是插入矢量图 pdf）",
         r"\\includegraphics(?:\[[^\]]*\])?\{[^}]*\.(?:jpg|jpeg|png)\}",
         "尽量换成矢量图（pdf）", langs=("zh", "en"), scope="raw"),
]

#: **初排转换可以默认自动套用的 fixable 白名单**（engine-core 在 `slcn/build.py` 接线时用）。
#: 2026-10-28 用户拍板（m01116/m01132，选项 A）：ZH-08 连接号按新口径**默认启用**；
#: 汉字-汉字、小写英文邻、数学/命令参数/URL 等仍在 ZH-08 内部静默，不会改产物。
#: 2026-10-30（B-272）ZH-12 句末脚注后补 `\,` 也进入默认白名单（判据见 `_zh12_hits()`）。
#: 2026-10-30（B-280）ZH-23 在守卫 A+B 观察期降为**只报**，从本白名单移除（15→14）；
#: Q4（FIX-20261112）v2 起 ZH-23 守卫判据复核通过，**放回**初排自动改（14→15）。
INITIAL_LAYOUT_FIXABLE: tuple[str, ...] = (
    "ZH-01", "ZH-02", "ZH-03", "ZH-04", "ZH-05", "ZH-06", "ZH-08",
    "ZH-09", "ZH-12", "ZH-14", "ZH-21", "ZH-23", "EN-01", "EN-02", "EN-03",
)

#: 只在人工「一校核查 --fix」时才该自动改的规则（默认初排**不**启用）。
#: 2026-10-28 起 ZH-08 已进入初排白名单，此集合暂时为空（保留常量供后批使用）。
OPT_IN_FIXABLE: tuple[str, ...] = ()


# ── 检查 ─────────────────────────────────────────────────────────────────

#: ZH-12 判据里的句末标点（手册 §校对规范·常规 4.3）：命令左侧可以是它，
#: 也可以是 `\ldots`/`\dots` 或 `……` 的末字。
_ZH12_END_PUNCT = "。！？…”’）】》」"
#: 句末标点与 `\footnote{` 之间允许夹的空格/制表符（`。 \footnote` 仍算）。
_ZH12_GAP = " \t"
#: 右锚：紧贴的汉字，或中文开引号/开括号（`\footnote{…}“下一句` 也要调）。
_ZH12_RIGHT_OPEN = "“‘（《「"
#: 中文稿里的英文句末（右锚是汉字时收；中文稿的「汉字.汉字」已在 ⑤ 被 ZH-02 改全角）。
_ZH12_END_HALF = ".!?"


def _zh12_in_math(mask: str, pos: int) -> bool:
    r"""`pos` 是否在**同一行**的行内数学区里（`$…$` / `\(…\)` / `\[…\]`）。

    ⚠️ 不用 `math_mask`：它的 `$…$$…$` 相邻美元符会把后面几千字正文连成一段
    （实测 corpus `Lanshang_Hu.tex` 的 `$\alpha$$\beta$`），按掩码段判会把正文误杀。
    行内数学只看本行：未转义 `$` 的奇偶，或最近一个 `\(`/`\[` 尚未闭合。
    `mask` 用 `raw_mask` 的文本（注释/verbatim 已挖空），避免把它里面的美元符算进来。
    """
    line_start = mask.rfind("\n", 0, pos) + 1
    seg = mask[line_start:pos]
    if len(re.findall(r"(?<!\\)\$", seg)) % 2 == 1:
        return True
    for open_d, close_d in (("\\(", "\\)"), ("\\[", "\\]")):
        if seg.rfind(open_d) > seg.rfind(close_d):
            return True
    return False


def _zh12_left_kind(mask: str, i: int) -> str | None:
    r"""ZH-12 左锚：`\footnote{` 之前的字符是否是句末。

    返回 `"full"`（全角句末/`\ldots`/`……`）、`"half"`（中文稿里的英文 `. ! ?`）
    或 `None`。允许句末与 `\footnote{` 之间夹空格/制表符（`。 \footnote`）。
    """
    k = i - 1
    while k >= 0 and mask[k] in _ZH12_GAP:
        k -= 1
    if k < 0:
        return None
    prefix = mask[:k + 1]
    if prefix.endswith("\\ldots") or prefix.endswith("\\dots"):
        return "full"
    ch = mask[k]
    if ch in _ZH12_END_PUNCT:
        return "full"
    if ch in _ZH12_END_HALF:
        return "half"
    return None


def _zh12_hits(tex: str, mask: str) -> list[tuple[int, int]]:
    r"""ZH-12 专用扫描：句末标点后紧跟 `\footnote{…}`、右锚紧贴汉字/中文开引号。

    返回 `(start, end)`：`start` 是反斜杠位置，`end` 是配对右花括号之后的位置
    （可直接用于 `Hit.start/end` 和 `apply_fixes` 的整段替换）。
    用 `mask`（`raw_mask`：注释/verbatim 已挖空、长度不变）定位与配对，保证注释
    里的命令不误报；数学区判据见 `_zh12_in_math`。

    判据（spec `b272-zh12-footnote-space` §4；分析 §B）：
    - 左锚：往前吃掉空格后是 `。！？…”’）】》」` 或 `\ldots`/`\dots`（英文 `. ! ?`
      仅在右锚是汉字时收）；
    - 右锚：`end` 处**紧贴**汉字或 `“ ‘ （ 《 「`；若是空格/换行/`~`/`%`、已有
      `\,`、反斜杠（命令/数学/环境）或文件尾，一律不算（幂等：已插 `\,` 不重复插）。
    """
    out: list[tuple[int, int]] = []
    pos = 0
    while True:
        i = mask.find("\\footnote{", pos)
        if i < 0:
            break
        end = _match_brace(mask, i + len("\\footnote{") - 1)
        pos = i + 1
        if end < 0 or end >= len(mask):
            continue
        if _zh12_in_math(mask, i):      # 数学区里的 `\footnote` 不按这条判
            continue
        if mask[end:end + 2] == "\\,":
            continue
        nxt = mask[end]
        right_cjk = _is_cjk(nxt)
        if not right_cjk and nxt not in _ZH12_RIGHT_OPEN:
            continue
        kind = _zh12_left_kind(mask, i)
        if kind is None:
            continue
        if kind == "half" and not right_cjk:
            continue
        out.append((i, end))
    return out



#: ZH-21 认句号/问号/叹号/分号（手册 §常规 4.1；B-278 起 `；` 视同 `。`）；冒号不动。
_ZH21_PUNCT = "。！？；"

#: 左邻这些标点时 ZH-21 不适用（脚注本就该位于该标点之前；逗号场景另归 ZH-10）
_ZH21_LEFT_SKIP = "，,、；;：:。！？…—"


def _zh21_sentence_start(mask: str, pos: int,
                         spans: list[tuple[int, int]]) -> int:
    r"""从 `pos` 往前找同一句的起点（跳过脚注内部的标点）。"""
    k = pos - 1
    while k >= 0:
        if not _in_any(k, spans) and mask[k] in "。！？\n":
            return k + 1
        k -= 1
    return 0


def _zh21_hits(tex: str, mask: str) -> list[tuple[int, int]]:
    r"""ZH-21（B-273）「脚注在句号前（应放句号后）」的专用扫描。

    `\footnote{…}`（花括号配对、支持嵌套）右花括号**紧邻** `。！？` → 命中；
    返回 `(start, end)`，`end` 是标点之后的位置，`replacement` 可把该标点直接
    搬到脚注之前（`文本\footnote{注}。` → `文本。\footnote{注}`）。
    排除：注释/数学区、脚注内容里再嵌的 `\footnote`、可选参数里（`\item[…]` 等）、
    行首/段首没有前文、以及手册 §常规 4.2 的例外——同一句里已有另一处脚注时整句不报。
    """
    spans = _footnote_spans(mask)
    opt = _optional_arg_spans(tex)
    out: list[tuple[int, int]] = []
    pos = 0
    while True:
        i = mask.find("\\footnote{", pos)
        if i < 0:
            break
        pos = i + 1
        end = _match_brace(mask, i + len("\\footnote{") - 1)
        if end < 0 or end >= len(mask):      # end 是右花括号之后的位置；标点为末字符时 end == len-1
            continue
        if mask[end] not in _ZH21_PUNCT:
            continue
        left = i - 1
        while left >= 0 and mask[left] in _ZH12_GAP:
            left -= 1
        if left < 0 or mask[left] in _ZH21_LEFT_SKIP:
            continue
        if _zh12_in_math(mask, i) or any(a < i < b for a, b in spans) or _in_any(i, opt):
            continue
        sent = _zh21_sentence_start(mask, i, spans)
        if not mask[sent:i].strip():        # 行首/段首：没有前文
            continue
        if mask.find("\\footnote{", sent, i) >= 0:   # 手册 4.2：同句两处脚注都放标点前
            continue
        out.append((i, end + 1))
    return out


#: ZH-22 判定的浮动体/表格环境（`\footnote` 在这些环境里会被吞掉）。
_FLOAT_ENVS = ("table", "tabular", "figure", "longtable")


def _env_spans(mask: str, env: str) -> list[tuple[int, int]]:
    r"""`\begin{env}`…`\end{env}` 的整段区间（`mask` 上算，注释/verbatim 不误报）。"""
    spans: list[tuple[int, int]] = []
    begin = "\\begin{" + env + "}"
    end_mark = "\\end{" + env + "}"
    for m in re.finditer(re.escape(begin), mask):
        e = mask.find(end_mark, m.end())
        if e > 0:
            spans.append((m.start(), e + len(end_mark)))
    return spans


def _zh22_hits(tex: str, mask: str) -> list[tuple[int, int]]:
    r"""ZH-22（B-273）「表格/浮动体内的 `\footnote` 会被吞」：环境内出现即报（只报不改）。

    `\footnotemark` / `\footnotetext` 不匹配（本扫描只找 `\footnote{`）；注释/verbatim/数学区不算。
    """
    spans: list[tuple[int, int]] = []
    for env in _FLOAT_ENVS:
        spans.extend(_env_spans(mask, env))
    if not spans:
        return []
    out: list[tuple[int, int]] = []
    pos = 0
    while True:
        i = mask.find("\\footnote{", pos)
        if i < 0:
            break
        pos = i + 1
        end = _match_brace(mask, i + len("\\footnote{") - 1)
        if end < 0:
            continue
        if not _in_any(i, spans) or _zh12_in_math(mask, i):
            continue
        out.append((i, end))
    return out


def _kaishu_spans(text: str) -> list[tuple[int, int]]:
    r"""楷体区间：quotation / quote 环境内容，以及 `\kaishu` 开关的作用域（B-278）。

    `\kaishu` 是字体开关，作用到所在花括号组结束；写在顶层时按段落（空行）收口。
    环境区间不含 `\begin`/`\end` 标记本身；开关区间不含 `\kaishu` 命令本身。
    """
    spans: list[tuple[int, int]] = []
    for env in KAISHU_ENVS:
        for m in re.finditer(r"\\begin\{" + env + r"\}(.*?)\\end\{" + env + r"\}",
                             text, re.S):
            spans.append((m.start(1), m.end(1)))
    # `\kaishu` 开关：向前扫出所在组（忽略 `\{` `\}`），区间到该组右花括号之前
    for m in re.finditer(r"\\kaishu(?![A-Za-z])", text):
        stack: list[int] = []
        i = 0
        while i < m.start():
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if c == "{":
                stack.append(i)
            elif c == "}" and stack:
                stack.pop()
            i += 1
        if stack:
            end = _match_brace(text, stack[-1])
            if end > 0:
                spans.append((m.end(), end - 1))
        else:
            para = re.search(r"\n[ \t]*\n", text[m.end():])
            end = m.end() + (para.start() if para else len(text))
            spans.append((m.end(), end))
    return spans


def check(tex: str, lang: str = "zh") -> Report:
    r"""按规则逐条检查 `tex`，返回报告（**只报不改**）。"""
    rep = Report(lang=lang)
    # 三层屏蔽：文本（挖数学与命令）/ 数学（只显数学）/ 原始（只挖注释）
    masks = {"text": visible_mask(tex), "math": math_mask(tex), "raw": raw_mask(tex)}
    kaishu = _kaishu_spans(tex)

    for rule in RULES:
        if lang not in rule.langs:
            continue
        if not rule.pattern:
            continue
        for m in re.finditer(rule.pattern, masks[rule.scope]):
            if rule.within_kaishu and not _in_any(m.start(), kaishu):
                continue
            if rule.accept and not rule.accept(m, tex):
                continue
            line, col = _line_col(tex, m.start())
            fix_ok = rule.fixable
            if fix_ok and rule.fixable_when is not None:
                fix_ok = bool(rule.fixable_when(m, tex))
            rep.hits.append(Hit(
                rule_id=rule.id, title=rule.title, basis=rule.basis,
                line=line, col=col, start=m.start(), end=m.end(),
                text=tex[m.start():m.end()].replace("\n", "⏎"),
                advice=rule.advice, fixable=fix_ok,
                replacement=(rule.replace(m) if (fix_ok and callable(rule.replace)) else None),
            ))

    # ZH-12（B-272）：句末标点后的脚注与紧随汉字之间要插 `\,`。专用扫描而非正则：
    # 脚注内容可能嵌套花括号，`[^}]*` 会截断；这里按花括号配对求整段区间再替换。
    if lang == "zh":
        for start, end in _zh12_hits(tex, masks["raw"]):
            line, col = _line_col(tex, start)
            seg = tex[start:end]
            rep.hits.append(Hit(
                rule_id="ZH-12", title="句末后的脚注紧跟汉字（间隔过小）",
                basis="手册 §校对规范·常规 4.3（需要 \\, 调间隔）",
                line=line, col=col, start=start, end=end,
                text=seg.replace("\n", "⏎"),
                advice="在脚注与汉字之间加 \\,", fixable=True,
                replacement=seg + "\\,",
            ))

    # ZH-21（B-273）：脚注在句末标点前 → 把标点搬到脚注之前（手册 §常规 4.1）。
    # 专用扫描：花括号配对、可嵌套；同句两处脚注的手册 4.2 例外整句跳过。
    if lang == "zh":
        for start, end in _zh21_hits(tex, masks["raw"]):
            line, col = _line_col(tex, start)
            seg = tex[start:end]
            rep.hits.append(Hit(
                rule_id="ZH-21", title="脚注在句末标点前（应放标点后）",
                basis="手册 §校对规范·常规 4.1（脚注在句号处放于句号后；`；` 视同 `。`）",
                line=line, col=col, start=start, end=end,
                text=seg.replace("\n", "⏎"),
                advice="把句末标点移到脚注之前", fixable=True,
                replacement=seg[-1] + seg[:-1],
            ))

    # ZH-23（B-278）：带括号的引用组，句末标点应在括号之前（正文与脚注内同判）。
    if lang == "zh":
        for start, end in _zh23_hits(tex, masks["raw"]):
            line, col = _line_col(tex, start)
            seg = tex[start:end]
            rep.hits.append(Hit(
                rule_id="ZH-23", title="带括号引用组的句末标点在括号之后",
                basis="手册 §校对规范·常规 4.1/5.1–5.2（引用与脚注都放在句号后、逗号前）",
                line=line, col=col, start=start, end=end,
                text=seg.replace("\n", "⏎"),
                advice="把句末标点移到括号之前", fixable=True,
                replacement=seg[-1] + seg[:-1],
            ))

    # ZH-22（B-273）：表格/浮动体里的 `\footnote` 会被吞掉（手册 §常规 4.4）；只报不改。
    for start, end in _zh22_hits(tex, masks["raw"]):
        line, col = _line_col(tex, start)
        seg = tex[start:end]
        rep.hits.append(Hit(
            rule_id="ZH-22", title="表格/浮动体内的 \\footnote 会被吞",
            basis="手册 §校对规范·常规 4.4（改用 \\footnotemark + \\footnotetext）",
            line=line, col=col, start=start, end=end,
            text=seg.replace("\n", "⏎"),
            advice="改用 \\footnotemark{} + 环境后的 \\footnotetext{}", fixable=False,
        ))

    # 楷体环境里的英文/数字（单独扫：要在区间内，且不在 \textup{} / \ct 等命令的参数里）
    if lang == "zh":
        for a, b in kaishu:
            seg, seg_mask = tex[a:b], masks["text"][a:b]
            for m in re.finditer(r"[A-Za-z0-9](?:[A-Za-z0-9 .,;:()\-]*[A-Za-z0-9)])?", seg_mask):
                raw = seg[m.start():m.end()]
                # 单字符数字（`第1章` 的 1）也要报；单个字母仍算噪声
                if not re.search(r"[A-Za-z0-9]", raw):
                    continue
                if len(raw.strip()) <= 1 and not raw.strip().isdigit():
                    continue
                # `\textup{...}` 之内会被掩成空格 → 不会命中；这里再排掉纯标点
                line, col = _line_col(tex, a + m.start())
                rep.hits.append(Hit(
                    rule_id="ZH-18", title="楷体环境里的英文/数字未调正",
                    basis="手册 §校对规范·常规 3", line=line, col=col,
                    start=a + m.start(), end=a + m.end(), text=raw,
                    advice="用 \\textup{} 包住（\\ct 等命令自带）", fixable=False,
                ))
    # Q8（FIX-20261112 EN-06）：标题大小写专用扫描（pattern 留空）。
    # 命中文本 = 整条命令（`\section{…}` / `\subsection{…}`），section 与 subsection
    # 分开报；合规 section 不报。`\subsection` 的实际改动由
    # `apply_en_subsection_case()`（初排 / `--fix`）完成，不依赖这条规则的 fixable。
    if lang == "en":
        rep.hits.extend(_en06_hits(tex))

    rep.hits.sort(key=lambda h: (h.start, h.rule_id))
    return rep


# ── 自动改（白名单：fixable 且替换无歧义）────────────────────────────────

def apply_fixes(tex: str, rep: Report,
                rule_ids: tuple[str, ...] | None = None) -> tuple[str, list[Hit]]:
    r"""把 `fixable` 的命中改掉，返回 (新文本, 实际改动的命中)。

    从后往前替换（偏移不变）；与已改区间**重叠**的命中跳过，避免连锁错位。
    位置类规则（脚注/引文移到标点前）不在这里做 —— 需要括号配对，见 `move_marks_before_punct()`。

    `rule_ids`：只动这些规则（`None` = 全部 fixable，即 `--fix` 的默认口径）。
    初排转换接线时应传 `INITIAL_LAYOUT_FIXABLE`（2026-10-28 起含 ZH-08 新口径、
    2026-10-30 B-272 起含 ZH-12 句末脚注补 `\,`、B-273 起含 ZH-21 脚注句号换位；
    B-280 起 ZH-23 降为只报、不在该集合内）；
    `OPT_IN_FIXABLE` 目前为空，供后批把「暂不默认启用」的规则单独列出。
    """
    todo = [h for h in rep.hits if h.fixable and h.replacement is not None
            and (rule_ids is None or h.rule_id in rule_ids)]
    applied: list[Hit] = []
    out = tex
    last_start = len(tex) + 1
    for h in sorted(todo, key=lambda x: x.start, reverse=True):
        if h.end > last_start:          # 与已改区间重叠
            continue
        out = out[:h.start] + h.replacement + out[h.end:]
        last_start = h.start
        applied.append(h)
    return out, list(reversed(applied))


def _match_brace(text: str, open_at: int) -> int:
    r"""`open_at` 是 `{` 的位置，返回配对 `}` 之后的位置（找不到返回 -1）。"""
    depth = 0
    i = open_at
    while i < len(text):
        c = text[i]
        if c == "\\":                   # 跳过被转义的字符（\{ \}）
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


_POSITION_RULES = {
    "footnote": re.compile(r"\\footnote\{"),
    "cite": re.compile(r"\\(?:parencite|cite|footcite|textcite)\{"),
}


def move_marks_before_punct(tex: str, kinds: tuple[str, ...] = ("footnote", "cite"),
                            spans: list[tuple[int, int]] | None = None
                            ) -> tuple[str, list[str]]:
    r"""把「标点 + \\footnote{…} / \\cite{…}」改成「\\footnote{…} + 标点」。

    依据：手册 §校对规范·常规 4.1 / 5.1（脚注与文献引用都放在逗号前、句号后）。
    ⚠️ 只处理**紧邻**的情形（标点与命令之间只有空白），不动跨句子的；
    括号配对失败的一律跳过并记入日志（宁可不改，也不能改坏）。

    `spans`（B-278）：只搬这些**命中区间**里的标点（初排/`--fix` 传 ZH-10/ZH-11 的
    `Hit.start`）；为 `None` 时保持旧口径（全量扫，供单独调用/旧测试）。
    cite 一律复用 `_zh11_accept_at`：同一对中文括注内的「逗号 + 引用」不搬。
    """
    allowed = None if spans is None else {s for s, _e in spans}
    logs: list[str] = []
    out = tex
    changed = True
    while changed:
        changed = False
        for kind in kinds:
            pat = _POSITION_RULES[kind]
            for m in pat.finditer(out):
                # 往前看：允许空白，然后是「，」或「,」
                k = m.start()
                while k > 0 and out[k - 1] in " \t":
                    k -= 1
                if k == 0 or out[k - 1] not in "，,":
                    continue
                if allowed is not None and (k - 1) not in allowed:
                    continue
                brace = out.index("{", m.start())
                end = _match_brace(out, brace)
                if end < 0:
                    logs.append(f"跳过：{out[m.start():m.start()+24]}… 花括号不配对")
                    continue
                if kind == "cite" and not _zh11_accept_at(out, k - 1, end):
                    continue
                mark = out[k - 1]
                cmd = out[m.start():end]
                new = out[:k - 1] + cmd + mark + out[end:]
                # 搬位可能吃掉「标点与命令之间」的空白（长度变化），
                # 之后命中区间的绝对坐标随之平移；allowed 必须同步，否则同一轮里
                # 后面的命中会被位置过期误跳过（B-278 实测：一句 4 处只搬 3 处）。
                if allowed is not None and len(new) != len(out):
                    delta = len(new) - len(out)
                    allowed = {p + delta if p > k - 1 else p for p in allowed}
                logs.append(f"第 {_line_col(out, k - 1)[0]} 行：把 {kind} 移到「{mark}」之前")
                out = new
                changed = True
                break
            if changed:
                break
    return out, logs


# ── 英文稿：引用外层括号 + 脚注/引用位序（B-312）─────────────────────────────
#
# 依据：手册 §校对规范·常规 4.1（脚注在逗号前、句号后）、5.1–5.2（文献引用的位置与
# 脚注同理）、§英文文章特殊规范（英文稿一律半角标点；正文里的括号式引用外面**再包
# 一层字面括号**，成品形态 `(\parencite[p.~33]{k1,k2})`）。
#
# **为什么单独一个 pass**：zh 侧这三条位序由 ZH-10/ZH-11/ZH-21 只报 + 初排 mover 实现，
# 但它们 `langs = ("zh",)`、判据是中文标点「，。！？」，英文稿只有半角 `, .` —— 直接
# 复用会把 zh 报告搞脏。也不该为英文**新增规则 id**：`proof29+fmt11+v1`（29 条）是锁定
# 契约，`docs/PROOF_RULES.md` 表恰好 29 行、taskbook golden 里带 RULESET_ID + 摘要，
# 加一条就全线漂移。所以做成初排**内置 pass**：只在 `lang == "en"` 时由
# `build.apply_proof_autofix` 调用，不进 `check()` 报告、不改 RULESET_ID；改动计数用
# `EN-10`（外层括号）/`EN-11`（位序）两个 **pass 键**（不是规则 id）记在 `rule_hits` 里。
#
# 纪律与 zh mover 一致：只搬**紧邻**的、括号配对失败一律跳过、幂等。

#: 只包 `\parencite`：`\textcite` 是「作者（年）」的行文引用，加括号会改意思。
_EN_CITE_RE = re.compile(r"\\parencite(?![a-zA-Z])")
_EN_MARK_RE = re.compile(r"\\(?:footnote|parencite)(?![a-zA-Z])")


def _en_mark_spans(tex: str) -> list[tuple[str, int, int]]:
    r"""`(kind, start, end)`：英文稿里 `\footnote{…}` / `\parencite[opt]{…}` 的整段。"""
    spans: list[tuple[str, int, int]] = []
    for m in _EN_MARK_RE.finditer(tex):
        kind = "footnote" if m.group(0) == "\\footnote" else "cite"
        i = m.end()
        while i < len(tex) and tex[i] in " \t*":
            i += 1
        if kind == "cite" and i < len(tex) and tex[i] == "[":
            j = tex.find("]", i)
            if j < 0:
                continue
            i = j + 1
            while i < len(tex) and tex[i] in " \t":
                i += 1
        if i >= len(tex) or tex[i] != "{":
            continue
        e = _match_brace(tex, i)
        if e < 0:
            continue
        spans.append((kind, m.start(), e))
    return spans


def _en_group(tex: str, s: int, e: int) -> tuple[int, int]:
    r"""已经把外层括号包好的 `(\parencite{…})` → 连括号一起算作「标点标记」的区间。"""
    if s > 0 and e < len(tex) and tex[s - 1] == "(" and tex[e] == ")":
        return s - 1, e + 1
    return s, e


def en_outer_parens(tex: str) -> tuple[str, int]:
    r"""给英文稿里**裸写**的 `\parencite[postnote]{keys}` 补一层字面括号（手册 §常规 5.2）。

    成品形态 `(\parencite[p.~33]{k1,k2})`。跳过：脚注体、可选参数、数学区、注释里的
    引用（脚注里是句子，不是正文括注）；左边已经有左括号、或右边紧跟右括号的也不再包
    （幂等）。前一个字符不是空白/左括号时补一个空格，避免 `word(\parencite{…})`。
    """
    raw = raw_mask(tex)
    mathm = math_mask(tex)
    fn = _footnote_spans(tex)
    opt = _optional_arg_spans(tex)
    out, n = tex, 0
    for kind, s, e in reversed(_en_mark_spans(tex)):
        if kind != "cite":
            continue
        if _in_any(s, fn) or _in_any(s, opt):
            continue
        if _zh12_in_math(mathm, s):
            continue
        if raw[s] == " " and tex[s] != " ":        # 注释里
            continue
        j = s - 1
        while j >= 0 and tex[j] in " \t":
            j -= 1
        k = e
        while k < len(tex) and tex[k] in " \t":
            k += 1
        if (j >= 0 and tex[j] == "(") or (k < len(tex) and tex[k] == ")"):
            continue                                   # 已在括号里（含 `( … )` 留白）
        pre = "" if s > 0 and tex[s - 1] in " \t\n(" else " "
        out = out[:s] + pre + "(" + out[s:e] + ")" + out[e:]
        n += 1
    return out, n


def en_move_marks(tex: str) -> tuple[str, int]:
    r"""英文稿的脚注/引用位序：放到**逗号前、句号后**（手册 §常规 4.1 / 5.1–5.2）。

      · `text, (\parencite{k})` → `text (\parencite{k}),`
      · `text (\parencite{k}). More` → `text. (\parencite{k}) More`

    只搬紧邻的情形（标点与标记之间只有空白）；`;` / `:` / 省略号 `...` 不碰 ——
    手册第 4.1/5.1 只钉了逗号与句号，冒号分号两侧都有人写，宁可不改。
    """
    out, n = tex, 0
    for _kind, s, e in reversed(_en_mark_spans(tex)):
        gs, ge = _en_group(tex, s, e)
        # ① 逗号后 → 逗号前：空白跟着标记一起往前搬
        k = gs
        while k > 0 and tex[k - 1] in " \t":
            k -= 1
        if k > 0 and tex[k - 1] == ",":
            gap = tex[k:gs]
            mark = tex[gs:ge]
            out = out[:k - 1] + gap + mark + "," + out[ge:]
            n += 1
            continue
        # ② 句号前 → 句号后：句号前面是空格时，标记跟过去
        po = ge
        if po >= len(tex) or tex[po] != ".":
            continue
        if po + 1 < len(tex) and tex[po + 1] not in " \t\n":   # `.5` / `...` / `.)` 不动
            continue
        left = tex[:gs].rstrip(" \t")
        gap = tex[len(left):gs] or " "
        mark = tex[gs:ge]
        # Q4（FIX-20261112 EN11-MOVER-01）：这里必须接 `out` 的尾巴，不能接原始 `tex`。
        # 本函数从右往左处理命中，前面的每一次修改都落在**当前命中右侧**（起点 ≥ ge），
        # 所以 `out[0:po+1]` 与 `tex[0:po+1]` 逐字相同，`out[po+1:]` 才保留那些修改；
        # 用 `tex[po+1:]` 会把右侧所有已改内容整段回滚 → 每次调用只剩最左一处生效
        # （Su_Wu 实测 43 处只落 12 处，自报计数却按 450 累加）。
        out = out[:len(left)] + "." + gap + mark + out[po + 1:]
        n += 1
    return out, n


def fix_en_marks(tex: str) -> tuple[str, dict[str, int]]:
    r"""英文稿初排内置 pass 的总入口：外层括号（`EN-10`）+ 位序（`EN-11`）。

    顺序有讲究：先包括号、再搬位序 —— mover 的「句号后」判据看的是标记**外侧**的
    `)`，裸命令先包起来才能一次判对。两趟各自幂等，最多迭代 3 轮。
    """
    hits: dict[str, int] = {}
    out = tex
    for _ in range(3):
        new, n_wrap = en_outer_parens(out)
        new, n_pos = en_move_marks(new)
        hits["EN-10"] = hits.get("EN-10", 0) + n_wrap
        hits["EN-11"] = hits.get("EN-11", 0) + n_pos
        if new == out:
            break
        out = new
    return out, {k: v for k, v in hits.items() if v}


# ── Q8（FIX-20261112 EN-06）：`\subsection` 句首式大小写 fixer ────────────────
#
# 依据：手册 §英文文章 3（Section 标题实词大写；subsection 及以下仅句首大写）。
#
# 只改 `\subsection` 的**纯文本词**：命令（`\emph{}`/`\cite{}`/`\ref{}`…）、`$…$`、
# ``…'' 与 `…' 引号内的内容全部原样保留。冒号以后也原样（副题首字母不动，避免把
# 「Argumentation: Cultural Accumulation…」的副题句首改成小写）。
#
# 保护（保留原样，并进「待人工」清单）：
#   ① 全大写缩写（AI / U.S. / II…）；② 含内部大写的混合词（McDowell / U.S.-China）；
#   ③ 本文**正文里以大写出现过**的词（论文自用术语：Generalized Argumentation Theory）；
#   ④ 内置常见专名表（English / Chinese / LaTeX / Oxford…）。
# 连字符复合词按句首式处理（Dual-Level → Dual-level / Second-Personal → Second-personal），
# 但其中任一段含内部大写则整词保护。
#
# 幂等：只把非首词小写化、首词首字母大写；第二遍 no-op。`mode`：
#   auto   —— 真改并返回改动表；report —— 只返回候选（不动文本）；off —— 不做。
_EN06_TITLE_RE = re.compile(
    r"\\(?P<cmd>section|subsection)\*?(?:\s*\[[^\]]*\])?\s*\{")
_EN06_PROTECT_RE = re.compile(
    r"\\(?:[A-Za-z]+)\*?(?:\s*\[[^\]]*\])?"
    r"(?:\s*\{(?:[^{}]|\{[^{}]*\})*\})*"
    r"|\$[^$]*\$"
    r"|``.*?''"
    r"|`[^`]*'"
)
_EN06_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’.\-]*")
_EN06_MINOR = frozenset((
    "a", "an", "the", "and", "but", "or", "nor", "for", "so", "yet",
    "of", "in", "on", "at", "to", "by", "with", "from", "as", "into", "onto",
    "over", "under", "between", "within", "through", "via", "per", "than",
    "that", "which", "whose", "when", "where", "while", "if", "then", "vs",
    "is", "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "has", "have", "had", "not",
))
_EN06_PROPER = frozenset((
    "English", "Chinese", "French", "German", "Greek", "Latin", "Japanese",
    "LaTeX", "TeX", "BibTeX", "Word", "Microsoft", "Apple", "Google",
    "Oxford", "Cambridge", "Harvard", "Chicago", "Princeton", "Routledge",
    "Springer", "Wiley", "Blackwell", "APA", "MLA", "Chicago", "PDF", "HTML",
    "URL", "DOI", "ISBN", "ISSN", "Internet", "World", "War", "God",
    # 哲学/论证理论常见专名（启发式；漏保护的仍进「待人工」清单兜底）
    "Plato", "Aristotle", "Socrates", "Kant", "Hegel", "Marx", "Nietzsche",
    "Wittgenstein", "Heidegger", "Husserl", "Gadamer", "Habermas", "Rawls",
    "Nozick", "Dennett", "Davidson", "Brandom", "McDowell", "Sellars",
    "Quine", "Putnam", "Kripke", "Frege", "Russell", "Carnap", "Austin",
    "Searle", "Grice", "Mill", "Hume", "Locke", "Hobbes", "Rousseau",
    "Descartes", "Leibniz", "Spinoza", "Aquinas", "Augustine", "Confucius",
    "Mencius", "Zhuangzi", "Laozi", "Xunzi", "Mozi", "Dewey", "James",
    "Peirce", "Mead", "Goffman", "Rorty", "Derrida", "Foucault", "Bourdieu",
    "Luhmann", "Toulmin", "Perelman", "Kantian", "Hegelian", "Marxist",
    "Rawlsian", "Habermasian", "Wittgensteinian", "Aristotelian",
    "Confucian", "China", "Europe", "America", "Asian", "Western", "Eastern",
))


def _en06_tokens(text: str) -> list[tuple[str, str, int, int]]:
    """把标题切成 (kind, text, start, end)；kind ∈ {prot, word, char}。"""
    toks: list[tuple[str, str, int, int]] = []
    i = 0
    while i < len(text):
        m = _EN06_PROTECT_RE.match(text, i)
        if m and m.end() > i:
            toks.append(("prot", m.group(0), i, m.end()))
            i = m.end()
            continue
        m = _EN06_WORD_RE.match(text, i)
        if m and m.end() > i:
            toks.append(("word", m.group(0), i, m.end()))
            i = m.end()
            continue
        toks.append(("char", text[i], i, i + 1))
        i += 1
    return toks


def _en06_classify(word: str) -> str:
    """词形：abbr（全大写缩写）/ mixed（内部大写）/ hyphen（连字符复合）/ plain / other。"""
    letters = [c for c in word if c.isalpha()]
    if not letters:
        return "other"
    if word.isupper() and len(letters) >= 2:
        return "abbr"
    if "-" in word:
        segs = [s for s in word.split("-") if s]
        if all(not re.search(r"[A-Z]", s[1:]) for s in segs):
            return "hyphen"
        return "mixed"
    if re.search(r"[A-Z]", word[1:]):
        return "mixed"
    return "plain"


def _en06_scan(tex: str) -> list[dict]:
    r"""所有 `\section` / `\subsection` 标题：cmd / start / end / brace / title。"""
    out: list[dict] = []
    for m in _EN06_TITLE_RE.finditer(tex):
        brace = m.end() - 1
        end = _match_brace(tex, brace)
        if end < 0:
            continue
        out.append({"cmd": m.group("cmd"), "start": m.start(), "end": end,
                    "brace": brace, "title": tex[brace + 1:end - 1]})
    return out


_EN06_TITLE_MACRO_RE = re.compile(
    r"\\(?:mytitle|myrunningtitle|mytitleCN|mytitleEN|title)(?![a-zA-Z])\s*\{")


def _en06_body_words(tex: str) -> set[str]:
    """正文里（非句首位置）以大写出现过的词：本文术语不降到小写。

    排除三类会污染保护集的来源：
    ① `\\section`/`\\subsection` 标题本身；
    ② 模板宏里的**题名**（`\\mytitle` 等按 Title Case 书写，不是正文术语）；
    ③ 段首/句首大写（`。`/`.`/`\n` 之后）——那是句法大写，不是术语大写。
    """
    chars = list(tex)
    for t in _en06_scan(tex):
        for p in range(t["start"], t["end"]):
            if chars[p] != "\n":
                chars[p] = " "
    for m in _EN06_TITLE_MACRO_RE.finditer(tex):
        end = _match_brace(tex, m.end() - 1)
        if end < 0:
            continue
        for p in range(m.start(), end):
            if chars[p] != "\n":
                chars[p] = " "
    body = "".join(chars)
    words: set[str] = set()
    for m in re.finditer(r"[A-Za-z][A-Za-z'’.\-]*", body):
        if not m.group(0)[:1].isupper():
            continue
        j = m.start() - 1
        while j >= 0 and body[j] in " \t":
            j -= 1
        if j >= 0 and body[j] in ".!?:;\n":
            continue                      # 句首/段首大写不算「术语大写」
        words.add(m.group(0))
    return words


def _en06_possessive_stem(word: str) -> str:
    r"""英语所有格 token 的词干；非所有格返回空串。

    覆盖 `X's` / `X’s` / `Xs'` / `X’`（弯直引号都算）：剥掉所有格后缀后返回词干，
    如 `Quine's` → `Quine`、`Kant’s` → `Kant`、`Jones'` → `Jones`。
    """
    if word.endswith(("'s", "’s")):
        stem = word[:-2]
    elif word.endswith(("'", "’")):
        stem = word[:-1]
    else:
        return ""
    return stem if stem and stem[-1].isalpha() else ""


def _en06_sentence_case(title: str, body_words: set[str]
                        ) -> tuple[str, list[tuple[str, str]]]:
    """把标题改成句首式；返回 (新标题, [(保留词, 建议小写形式)])。

    冒号处只**保留下一词的首字母原样**（副题句首的 S/A… 不动），其后各词仍按句首式
    处理——手册要求 subsection 句首式，不是「副题整段保持 Title Case」。
    """
    out: list[str] = []
    manual: list[tuple[str, str]] = []
    start = True          # 标题第一个词
    cap_next = False      # 紧跟冒号的词：保留首字母大小写
    for kind, t, _s, _e in _en06_tokens(title):
        if kind != "word":
            out.append(t)
            if kind == "char" and t == ":":
                start = False
                cap_next = True
            continue
        keep = (_en06_classify(t) in ("abbr", "mixed")
                or t in _EN06_PROPER or t in body_words)
        # 所有格专名保护回落到词干（V OBS-1）：`Quine's`/`Kant’s` 整体不在专名表，
        # 但词干是内置专名或正文大写词，故不能被当普通词降成 `quine's`。
        # 卡 Q8 的保护范围＝「内置常见专名表 / 正文大写词」；首词的所有格仍走
        # start 分支还原首字母大写（`quine's` → `Quine's`），语义不变。
        if not keep:
            _stem = _en06_possessive_stem(t)
            if _stem and (_stem in _EN06_PROPER or _stem in body_words):
                keep = True
        # 英文代词 I（也是罗马数字 I）永远保持大写，且不算「保留待人工」噪声。
        if t == "I":
            out.append(t)
            start = False
            cap_next = False
            continue
        if keep:
            out.append(t)
            if not start:
                low = t.lower()
                if low != t:
                    manual.append((t, low))
            start = False
            cap_next = False
            continue
        if start:
            out.append(t[:1].upper() + t[1:].lower())
            start = False
        elif cap_next:
            out.append(t[0] + t[1:].lower())
            cap_next = False
        else:
            out.append(t.lower())
    return "".join(out), manual


def _en06_section_case_ok(title: str) -> bool:
    """Section 标题是否已符合「实词大写」；合规的 section 不进 EN-06 报告。"""
    words = [t for kind, t, _s, _e in _en06_tokens(title) if kind == "word"]
    for idx, w in enumerate(words):
        if _en06_classify(w) in ("abbr", "mixed") or w in _EN06_PROPER:
            continue
        if idx == 0:
            if not w[:1].isupper():
                return False
            continue
        if w.lower() in _EN06_MINOR:
            continue
        if not w[:1].isupper():
            return False
    return True


def _en06_hits(tex: str) -> list[Hit]:
    r"""EN-06 专用扫描：命中文本 = **整条命令**（`\section{…}` / `\subsection{…}`）。

    只报「真有大小写问题」的标题：合规 section 不报；subsection 报「会改 / 有待人工词」。
    section 与 subsection 在报告里分开列（不混排）。
    """
    body_words = _en06_body_words(tex)
    hits: list[Hit] = []
    for t in _en06_scan(tex):
        title = t["title"]
        if t["cmd"] == "section":
            if _en06_section_case_ok(title):
                continue
            advice = "Section 标题实词应大写：逐条核对（只报不改）"
        else:
            new, manual = _en06_sentence_case(title, body_words)
            if new == title and not manual:
                continue
            if new != title:
                advice = f"可自动改（初排默认/--fix）：`{new}`"
            else:
                advice = "专名/缩写已保留，人工核对是否仍需改"
        line, col = _line_col(tex, t["start"])
        hits.append(Hit(
            rule_id="EN-06", title="章节标题大小写待核",
            basis="手册 §英文文章 3（Section 实词大写；subsection 及以下仅句首大写）",
            line=line, col=col, start=t["start"], end=t["end"],
            text=tex[t["start"]:t["end"]].replace("\n", "⏎"),
            advice=advice, fixable=False,
        ))
    return hits


def apply_en_subsection_case(tex: str, mode: str = "auto",
                             body_words: set[str] | None = None
                             ) -> tuple[str, dict]:
    r"""Q8 入口：英文稿 `\subsection` 句首式大小写。

    返回 `(新文本, info)`；`info` = `{"fixed":[{line,old,new}], "candidates":[...], "manual":[{old,new}]}`。
    `mode="auto"` 真改（幂等）；`"report"` 只算候选；`"off"` 原样返回。
    """
    info: dict = {"fixed": [], "candidates": [], "manual": []}
    if mode not in ("auto", "report"):
        return tex, info
    bw = body_words if body_words is not None else _en06_body_words(tex)
    edits: list[tuple[dict, str, str]] = []
    manual: list[dict] = []
    seen: set[str] = set()
    for t in _en06_scan(tex):
        if t["cmd"] != "subsection":
            continue
        old = t["title"]
        new, m = _en06_sentence_case(old, bw)
        for a, b in m:
            if a in seen:
                continue
            seen.add(a)
            manual.append({"old": a, "new": b})
        if new != old:
            edits.append((t, old, new))
    info["manual"] = manual
    if mode == "report":
        info["candidates"] = [{"line": _line_col(tex, t["start"])[0],
                               "old": old, "new": new} for t, old, new in edits]
        return tex, info
    out = tex
    for t, _old, new in reversed(edits):
        out = out[:t["brace"] + 1] + new + out[t["end"] - 1:]
    info["fixed"] = [{"line": _line_col(tex, t["start"])[0],
                      "old": old, "new": new} for t, old, new in edits]
    return out, info
