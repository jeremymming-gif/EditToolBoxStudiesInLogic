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

CJK = r"\u4e00-\u9fff"

#: quotation / quote 在 SLCN 模板里被 `\kaishu` 重定义过（楷体环境）
KAISHU_ENVS = ("quotation", "quote")
_MATH_ENVS = ("equation", "align", "gather", "multline", "eqnarray",
              "array", "matrix", "pmatrix", "bmatrix", "cases", "displaymath")
#: 这些命令的花括号参数是**键**或**路径**，文本规则一律不碰
_KEY_ARGS = ("cite", "parencite", "textcite", "footcite", "nocite", "ref", "eqref",
             "label", "pageref", "url", "href", "includegraphics", "bibitem", "input",
             "include", "usepackage", "documentclass", "bibliography", "addbibresource")


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
                r"\\begin\{(" + "|".join(_MATH_ENVS) + r")\*?\}.*?\\end\{\1\*?\}",
                r"\\\[.*?\\\]", r"\$\$.*?\$\$", r"\\\(.*?\\\)", r"\$[^$]*\$",
                r"\\verb\*?\|[^|]*\|", r"\\verb\*?(.)(.*?)\1"):
        for m in re.finditer(pat, text, re.S):
            blank_match(m)
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

    env_pat = "\\\\begin\\{(" + "|".join(_MATH_ENVS) + ")\\*?\\}.*?\\\\end\\{\\1\\*?\\}"
    for pat in (env_pat, "\\\\\\[.*?\\\\\\]", "\\$\\$.*?\\$\\$", "\\\\\\(.*?\\\\\\)"):
        for m in re.finditer(pat, text, re.S):
            keep(m.start(), m.end())
    for m in re.finditer(r"(?<!\\)\$[^$]*?\$", text):      # 行内 $...$
        keep(m.start(), m.end())
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


# 半角 → 全角（只在「两侧都是中文」时用，所以不会被用在英文/数字语境里）
_HALF2FULL = {",": "，", ".": "。", ":": "：", ";": "；", "?": "？", "!": "！"}


def _adjacent_cjk_replace(pat_half: str):
    r"""造一个「中文+半角标点+中文」→ 全角的替换函数。

    ⚠️ 两侧的 ASCII 空格要**一起吃掉**：半角改全角之后留一个空格会变成「集合， 公式集」
    这种多余间隔（实测 2023_05_Baoxun_Tu 就是这样）。中文之间本来不需要空格。
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


def _zh08_narrow(m: re.Match, tex: str) -> bool:
    r"""ZH-08 的**窄子集**判据：短杠左右紧邻都是汉字，且不在命令可选参数里。

    为什么只做这一窄类（2026-10-18 rules-docs Phase0 实测）：期 87 人工成品里
    `马丁-洛夫` / `斯托奈克-刘易斯` / `语用-辩证` 一类 CJK-CJK 复合词 76/76 改成了连接号
    （旧口径 `\cndash `；按手册应为 `\edash `，见 `_zh08_connector_replace`）；
    而同一条规则命中的 latin-CJK（`would-反事实`）、math-CJK
    （`$\phi$-世界`/`$\infty$-群胚`）、digit-CJK（`2-群胚`）、CJK-digit（`\item[模仿-1：]`）
    人工成品**一律保留 ASCII 短杠**（24/24、16/16、3/3、2/2）——自动改这些就是制造新错。
    """
    if any(a <= m.start() < b for a, b in _optional_arg_spans(tex)):
        return False
    if m.group(1) is not None:                 # 命中「汉字-」：右侧紧邻必须是汉字
        return m.end() < len(tex) and _is_cjk(tex[m.end()])
    if m.start() == 0:                         # 命中「-汉字」：左侧紧邻必须是汉字
        return False
    return _is_cjk(tex[m.start() - 1])


def _zh08_connector_replace(m: re.Match) -> str:
    r"""窄子集替换：`汉字-汉字` → `汉字\edash 汉字`（**短横 `\edash`**，手册口径）。

    依据（用户 2026-10-18 拍板「按手册规范」，手册原文
    `编辑笔记/排版手册/排版手册源码/edit_handbook.tex` 第 317-321 行）：
    - **短横 `\edash`**：人名与编号（`表2\edash 9`、`2022\edash 10\edash 08`、`3-a`、
      `A\edash 级别`、`让\edash 雅克·卢梭`）；
    - **双短横 `\cndash`**：**只用于页码范围**（`第8\cndash 12页`；见 ZH-09 / `zh-pagerange-*`）；
    - **一字线 `—`**：半个破折号（`北京—上海`）。
    所以 ZH-08 的中文复合词/人名用 `\edash`，**不是** `\cndash`（期 87 人工成品的
    `马丁\cndash 洛夫` 属旧口径，已按拍板改正）。`\edash` 后保留一个空格：LaTeX 控制词后
    的空格被吞掉、视觉与手册源码 `让\edash 雅克·卢梭` 一致；不写成 `{}` 以免多出空组。
    本函数只在 `_zh08_narrow` 为真时被调用，此时 `group(1)`（命中「汉字-」方向）必非空。
    """
    return (m.group(1) or "") + "\\edash "


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
         rf"(?:([{CJK}])[ \t]{{0,3}}-|-[ \t]{{0,3}}([{CJK}]))",
         "判断是「短横」（\\edash：人名/编号/日期）、「双短横」（\\cndash：**只有页码范围**）"
         "还是一字线「—」；汉字-汉字 这类中文复合词/人名可由 --fix 自动改 \\edash（默认初排不启用）",
         langs=("zh",), noisy=True, fixable=True,
         fixable_when=_zh08_narrow, replace=_zh08_connector_replace),
    Rule("ZH-09", "「第N-N页」缺双短横命令", "经验书 预校正·中文（检索 第\\d*-\\d*页）＋手册·连字符",
         r"第[ \t]{0,3}(\d+)[ \t]{0,3}-[ \t]{0,3}(\d+)[ \t]{0,3}页", "范围页码用双短横：第8\\cndash 12页",
         langs=("zh",), fixable=True,
         replace=lambda m: f"第{m.group(1)}\\cndash {m.group(2)}页"),

    # ── 脚注与引文的位置（手册 §校对规范·常规 4 / 5）────────────────────
    Rule("ZH-10", "脚注在逗号/句号之后（应在标点之前）",
         "手册 §校对规范·常规 4.1（脚注在逗号处放逗号前、句号处放句号后）",
         r"([，,])[ \t]{0,3}(\\footnote\{)", "把脚注移到该标点之前", langs=("zh",), scope="raw"),
    Rule("ZH-11", "引文在逗号之后（应在标点之前）",
         "手册 §校对规范·常规 5.1（文献引用位置与脚注同理）",
         r"([，,])[ \t]{0,3}(\\(?:parencite|cite|footcite|textcite)\{)",
         "把引用移到该标点之前", langs=("zh",), scope="raw"),
    Rule("ZH-12", "句号后的脚注紧跟汉字（间隔过小）",
         "手册 §校对规范·常规 4.3（需要 \\, 调间隔）",
         rf"(。\\footnote\{{[^}}]*\}})[ \t]{{0,3}}(?=[{CJK}])",
         "在脚注与汉字之间加 \\,", langs=("zh",), scope="raw"),

    # ── 数学环境（手册 §数学环境）──────────────────────────────────────
    Rule("ZH-13", "省略号用三个英文点", "手册 §数学环境·省略号（按高度选 \\ldots / \\cdots）",
         r"(?<!\.)\.\.\.(?!\.)", "数学里用 \\ldots（下标）或 \\cdots（运算符之间）",
         langs=("zh", "en"), scope="math"),
    Rule("ZH-14", "集合里的竖线应使用 \\mid", "手册 §数学环境·竖线的间隔",
         r"\\\{[^{}]*\|[^{}]*\\\}", "集合中的竖线用 \\mid", langs=("zh", "en"),
         fixable=True, replace=lambda m: m.group(0).replace("|", "\\mid "), scope="math"),
    Rule("ZH-15", "\\mathbf 加粗数学", "手册 §数学环境·加粗（建议 \\pmb / \\boldsymbol）",
         r"\\mathbf\{", "改用 \\pmb{} 或 \\boldsymbol{}", langs=("zh", "en"), scope="math"),

    # ── 引用与版式（手册 §排版指南）────────────────────────────────────
    Rule("ZH-16", "图表/式引用缺波浪号间隔", "手册 §间隔 4（如「表~\\ref{table1}~所示」）",
         r"[图表式][ \t]{0,3}\\ref\{", "补波浪号：表~\\ref{...}~所示", langs=("zh",), scope="raw"),
    Rule("ZH-17", "\\caption 里用 \\\\ 换行", "手册 §冷门问题汇总 1（应使用 \\newline）",
         r"\\caption\{[^{}]*\\\\", "caption 内换行用 \\newline", langs=("zh", "en"), scope="raw"),
    Rule("ZH-18", "楷体环境里的英文/数字未调正",
         "手册 §校对规范·常规 3（楷体环境中的英文、数字、标点用 \\textup 调正）",
         r"", "用 \\textup{} 包住（\\ct 等命令自带）", langs=("zh",)),
    Rule("ZH-19", "人名缩写之间缺空格", "经验书 预校正·共通（检索 \\.[A-Z]，人名中要有空格）",
         r"[A-Z]\.[A-Z]", "人名缩写之间加空格：J. N. （固定缩写 U.S./Q.E.D. 等不报；本规则只报不改）",
         langs=("zh", "en"), accept=_zh19_accept),

    Rule("ZH-20", "楷体环境里用 \\textbf 加粗", "手册 §校对规范·常规 3（楷体环境下的加粗用 \\textsf）",
         r"\\textbf\{", "楷体环境（quotation / quote）内改用 \\textsf{}", langs=("zh",), scope="raw"),

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
         r"\\(?:section|subsection|subsubsection)\{", "逐条核对标题大小写", langs=("en",), scope="raw"),

    # ── 通用 ───────────────────────────────────────────────────────────
    Rule("ALL-01", "位图插图（非矢量）", "手册 §插入图片（核心思想是插入矢量图 pdf）",
         r"\\includegraphics(?:\[[^\]]*\])?\{[^}]*\.(?:jpg|jpeg|png)\}",
         "尽量换成矢量图（pdf）", langs=("zh", "en"), scope="raw"),
]

#: **初排转换可以默认自动套用的 fixable 白名单**（engine-core 在 `slcn/build.py` 接线时用）。
#: ⚠️ 故意**不含 ZH-08**：连接号口径已按手册拍板（人名/编号用短横 `\edash`，双短横
#: `\cndash` **只给页码范围**；用户 2026-10-18 决定），但默认初排是否启用仍待本批回归 +
#: 顾问意见（Lead 决定）—— 在拍板前默认初排不得改产物，ZH-08 的窄子集只在人工点
#: 「一校核查 --fix」时生效。
INITIAL_LAYOUT_FIXABLE: tuple[str, ...] = (
    "ZH-01", "ZH-02", "ZH-03", "ZH-04", "ZH-05", "ZH-06",
    "ZH-09", "ZH-14", "EN-01", "EN-02", "EN-03",
)

#: 只在人工「一校核查 --fix」时才该自动改的规则（默认初排**不**启用）。
OPT_IN_FIXABLE: tuple[str, ...] = ("ZH-08",)


# ── 检查 ─────────────────────────────────────────────────────────────────

def _kaishu_spans(text: str) -> list[tuple[int, int]]:
    r"""楷体环境（quotation / quote）在原文里的区间（不含环境标记本身）。"""
    spans: list[tuple[int, int]] = []
    for env in KAISHU_ENVS:
        for m in re.finditer(r"\\begin\{" + env + r"\}(.*?)\\end\{" + env + r"\}",
                             text, re.S):
            spans.append((m.start(1), m.end(1)))
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

    # 楷体环境里的英文/数字（单独扫：要在区间内，且不在 \textup{} / \ct 等命令的参数里）
    if lang == "zh":
        for a, b in kaishu:
            seg, seg_mask = tex[a:b], masks["text"][a:b]
            for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9 .,;:()\-]*[A-Za-z0-9)]", seg_mask):
                raw = seg[m.start():m.end()]
                if not re.search(r"[A-Za-z]", raw):
                    continue
                if len(raw.strip()) <= 1:
                    continue
                # `\textup{...}` 之内会被掩成空格 → 不会命中；这里再排掉纯标点
                line, col = _line_col(tex, a + m.start())
                rep.hits.append(Hit(
                    rule_id="ZH-18", title="楷体环境里的英文/数字未调正",
                    basis="手册 §校对规范·常规 3", line=line, col=col,
                    start=a + m.start(), end=a + m.end(), text=raw,
                    advice="用 \\textup{} 包住（\\ct 等命令自带）", fixable=False,
                ))
    rep.hits.sort(key=lambda h: (h.start, h.rule_id))
    return rep


# ── 自动改（白名单：fixable 且替换无歧义）────────────────────────────────

def apply_fixes(tex: str, rep: Report,
                rule_ids: tuple[str, ...] | None = None) -> tuple[str, list[Hit]]:
    r"""把 `fixable` 的命中改掉，返回 (新文本, 实际改动的命中)。

    从后往前替换（偏移不变）；与已改区间**重叠**的命中跳过，避免连锁错位。
    位置类规则（脚注/引文移到标点前）不在这里做 —— 需要括号配对，见 `move_marks_before_punct()`。

    `rule_ids`：只动这些规则（`None` = 全部 fixable，即 `--fix` 的默认口径）。
    初排转换接线时必须传 `INITIAL_LAYOUT_FIXABLE` —— 那里的默认产物不许被
    `OPT_IN_FIXABLE`（ZH-08 连接号）改掉（口径未拍板，见该常量注释）。
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


def move_marks_before_punct(tex: str, kinds: tuple[str, ...] = ("footnote", "cite")) -> tuple[str, list[str]]:
    r"""把「标点 + \\footnote{…} / \\cite{…}」改成「\\footnote{…} + 标点」。

    依据：手册 §校对规范·常规 4.1 / 5.1（脚注与文献引用都放在逗号前、句号后）。
    ⚠️ 只处理**紧邻**的情形（标点与命令之间只有空白），不动跨句子的；
    括号配对失败的一律跳过并记入日志（宁可不改，也不能改坏）。
    """
    logs: list[str] = []
    out = tex
    changed = True
    while changed:
        changed = False
        for kind in kinds:
            pat = _POSITION_RULES[kind]
            for m in pat.finditer(out):
                brace = out.index("{", m.start())
                end = _match_brace(out, brace)
                if end < 0:
                    logs.append(f"跳过：{out[m.start():m.start()+24]}… 花括号不配对")
                    continue
                # 往前看：允许空白，然后是「，」或「,」
                j = m.start()
                k = j
                while k > 0 and out[k - 1] in " \t":
                    k -= 1
                if k == 0 or out[k - 1] not in "，,":
                    continue
                mark = out[k - 1]
                cmd = out[m.start():end]
                new = out[:k - 1] + cmd + mark + out[end:]
                logs.append(f"第 {_line_col(out, k - 1)[0]} 行：把 {kind} 移到「{mark}」之前")
                out = new
                changed = True
                break
            if changed:
                break
    return out, logs
