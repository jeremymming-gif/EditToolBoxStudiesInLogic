"""LaTeX 前言依赖迁移（第 120 条，2026-10-17 macCaseTest89 实测）。

**为什么需要这一层**：作者交来的 LaTeX 常常是「完整模板稿」——正文依赖前言里的
宏包 / tikz 库 / 定义（`\\DeclareMathOperator`、`\\newcolumntype`、`\\definecolor`、
`\\newtheorem`…）。初排稿换成新版模板骨架后，只搬正文与 `\\newcommand` 是不够的：
这些依赖不跟着走就会编译失败，而且报错离真正原因很远（`\\llbracket` undefined、
`Unknown arrow tip kind 'Stealth'` 之类）。

本模块只做**纯文本、离线**的判据与改写，不依赖 TeX 发行版：

1. `PreambleDeps` / `scan_preamble()`：从作者稿前梳理出可安全搬运的依赖清单；
2. `plan_migration()`：与模板（骨架 + 当前 .sty）做差集——模板已提供的、模板自己
   管理的（ctex / fontspec / geometry / hyperref / biblatex / 本刊 sty）一律不搬；
3. `insert_lines()`：整块插到 `hyperref` 之前（模板把 hyperref 放在最后）；
4. `defined_names()` / `select_custom_macros()`：宏名冲突判据——模板用
   `\\newtheorem{prop}` 定义的 `\\prop`、或 `\\usepackage[all]{xy}` 里的 `\\ar`，
   文本扫描 `\\newcommand` 是看不到的，这里补上；
5. 正文**没用到**的 `\\newcommand` 不搬（如 07 Fang_Wang 前言里的 `\\ar`，正文根本
   没引用，搬过去反而与 xy 的 `\\ar` 冲突）——只搬正文可达的那些，并做传递闭包。

**不做什么**：不改 `.bib`；不搬 `\\documentclass` / `\\setmainfont` 等版面级设置；
不解析 TeX 发行版里宏包自带的定义（那是编译期信息，纯离线拿不到）。

**第 130 条（2026-10-18）：定义"放在哪"也要兼容**。作者的定义不只在 `.tex` 导言区：

1. **本地 `.sty`**：不止定理环境（第 127 条），普通宏（`\\newcommand`）、算子、列类型、
   颜色也要按「正文真正用到 + 模板没有」搬（2023(71) 04、2024(75) 05 的真实旧模板稿）；
2. **本地 `.cls` / `.def` / `.cfg`**：`\\documentclass{本地类}` 时读同目录 `<类>.cls`，
   本地支撑文件里 `\\input` / `\\RequirePackage` 的同目录文件递归读；前言里
   `\\input{*.sty}` / `\\input{*.def}` 由 `latex.expand_inputs()` 先展开（`build.py`）；
3. **正文区定义**：不迁移（正文整段照搬已保留），只在日志里提示「建议移到导言区」；
4. **定义缺失预检**：编译前核对正文 `\\begin{env}` 是否被「模板 + 迁移结果 + 正文自身定义」
   覆盖，缺失只警告、不阻断出稿。

**第 130 条补丁（2026-10-18）**：① 源目录有同名 `.sty` 时 `\\usepackage{名字}` 的包行
**不迁**（它是作者的旧模板/本地包，顶部加载交给现行模板；文件按支撑文件差集搬定义）；
② `\\newcounter{X}` 纳入迁移，但只在「正文或已迁移定义体用到」时搬
（`\\theX` / `\\value{X}` / `\\setcounter{X}`…），并先于用它的环境定义。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .latex import (
    Macro,
    find_all_macros,
    match_brace,
    parse_macros,
    skip_ws_comments,
    strip_comments,
)

# 模板自己管理的宏包：作者稿里即使写了也不搬（选项/顺序与模板冲突，且模板是权威）。
# 当前 .sty 的名字（如 SLCN-MIX）由调用方追加进来。
# 第四十五批 A（86-05 Wei_Qiu 实测）：正文环境 → 提供该环境的宏包。
# 作者常把宏包行写进同目录旧 .sty 而不是主前言；换用新模板后环境就没了。
# 只收「已实测需要 + 与模板无冲突」的环境；其余仍走定义缺失预检 warning。
ENV_PACKAGE_HINTS = {
    "algorithm": ("algorithm",),
    "algorithmic": ("algpseudocode", "algorithmic"),
}

TEMPLATE_MANAGED_PACKAGES = frozenset({
    "SLCN",
    "SLCN-MIX",
    "SLEN",
    "ctex",
    "CTEX",
    "fontspec",
    "geometry",
    "hyperref",
    "biblatex",
})

# 与模板的 biblatex **互斥**的参考文献宏包（第 141 条，2026-10-18）。
# 模板 SLCN-MIX / SLEN 都在导言区 `\usepackage[backend=biber,...]{biblatex}`；
# 作者若写 natbib / gbt7714（前者最常见），把包行照搬过来就是
# `Package biblatex Error: Incompatible package 'natbib'` + `\bibhang already defined`
# + 编译失败（期 87 子案例 4 / 05 Tao_Song 实测）。
# 处理：包行不迁；正文里的 natbib 专用引用命令改写成 biblatex 的等价命令，
# 作者用的裸 `\cite` 在 biblatex 里本来就有定义，不用改。
BIBLATEX_REPLACED_PACKAGES = frozenset({"natbib", "gbt7714"})

# natbib 专用命令 → biblatex 等价命令（只收语义一一对应的；`\cite` 不在此列）。
NATBIB_TO_BIBLATEX = {
    "citep": "parencite",      # (Author, Year) 圆括号引用
    "citet": "textcite",       # Author (Year) 叙述式引用
    "citealt": "cite",         # Author, Year（无外层括号）
    "citealp": "parencite",    # 无外层括号场景，退化为圆括号引用（人工复核）
}

# 与**模板已加载宏包**重合同名命令的作者宏包（第 141 条，2026-10-18）。
# 判据：只要模板里出现 `trigger` 里的任一宏包名，就说明该命令已由模板定义，
# 作者再 `\usepackage` 这个包会在加载时报 `Command \X already defined` → 整篇失败。
# 实测：期 87 子案例 4（05 Tao_Song）作者前言有 `\usepackage{bbding}`，而模板
# SLCN-MIX/SLEN 都加载 marvosym（`\Cross` 由它定义）→ `Command \Cross already defined`。
# 处理：整包不迁 + 明确警告（作者若真用到 bbding 专有符号，编译会在正文处报
# Undefined control sequence，编辑按警告补即可；这比「加载阶段直接炸掉」可诊断）。
PACKAGE_COMMAND_CONFLICTS: dict[str, tuple[str, str]] = {
    # 作者包 → (原因, 模板里触发冲突的宏包名)
    "bbding": ("模板已通过 marvosym 定义了 \\Cross 等命令", "marvosym"),
}

# 常用「符号宏 → 宏包」对照（第 120 条，2026-10-17）。只收**符号类、不改变版面**的窄口
# 映射：作者正文用了这些宏、但作者前言与模板都没加载对应宏包时自动补上。实测 02
# Chen_Fu 正文用了 \llbracket / \rrbracket（来自 stmaryrd），而它们只藏在作者同目录
# 的旧版 SLCN.sty 里——模板差集看不到，编译就报 Undefined control sequence。
# 不碰 times / geometry / authblk 这类**版面级**宏包（那应由模板决定，不能从作者旧稿搬）。
SYMBOL_PACKAGES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("llbracket", "rrbracket", "llparenthesis", "rrparenthesis",
      "shortleftarrow", "shortrightarrow", "bigsqcap"), "stmaryrd"),
    (("mathscr",), "mathrsfs"),
    (("bm",), "bm"),
    (("multirow",), "multirow"),
    (("cancel",), "cancel"),
    (("mathbbm",), "bbm"),
    (("textcolor",), "xcolor"),
    (("cancelto",), "cancel"),
)

_PKG_LINE_RE = re.compile(
    r"^\s*\\(?:usepackage|RequirePackage)\s*(\[[^\]]*\])?\s*\{([^}]*)\}"
)
_TIKZ_LINE_RE = re.compile(r"^\s*\\usetikzlibrary\s*\{([^}]*)\}")

# ── 第 130 条：本地支撑文件与定义位置加固 ────────────────────────────────────
# 作者稿同目录里被前言加载的**非 .tex** 文件：本地旧模板 .sty、本地类 .cls，
# 以及被它们 \input / \RequirePackage 带进来的 .def / .cfg。只读同目录（不查
# TeX 发行版——那需要编译期信息，纯离线拿不到）。
SUPPORT_EXTENSIONS = (".sty", ".cls", ".def", ".cfg")
_DOCCLASS_RE = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_INPUT_NAME_RE = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")
_ANY_PKG_RE = re.compile(
    r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}"
)

# 宏类定义（\\newcommand 家族 + 算子）：都占「控制序列」这一个命名空间。
_MACRO_KINDS = frozenset({"newcommand", "renewcommand", "providecommand", "operator"})
_ENV_KINDS = frozenset({"newtheorem", "newenvironment"})

# 编译期才知道的环境（LaTeX 内核 / 模板类 / 常用宏包自带）：文本扫描看不到它们，
# 定义缺失预检里必须免报，否则每篇都会报 itemize/center/equation 无法解析。
# ⚠️ 只收「一定不是作者自定义定理环境」的：字体大小环境、版式环境、常见宏包环境。
#    作者自定义环境（fact / liju / 定理类缩写）**刻意不在白名单里**——那正是预检要抓的。
BASE_ENVIRONMENTS = frozenset({
    # 文档结构 / 列表 / 版式（LaTeX 内核与 article/book 类）
    "document", "itemize", "enumerate", "description", "list", "trivlist",
    "center", "flushleft", "flushright", "quote", "quotation", "verse",
    "verbatim", "minipage", "picture", "figure", "table", "titlepage",
    "tabular", "tabularx", "longtable", "array", "tabbing", "tblr",
    "thebibliography", "bibliography", "abstract", "keywords",
    "sloppypar", "adjustwidth", "multicols", "subfigure", "sideways",
    "lyxlist", "comment", "math", "displaymath",
    # 字体大小 / 中文字体（LaTeX 类与 ctex 提供）
    "tiny", "scriptsize", "footnotesize", "small", "normalsize",
    "large", "Large", "LARGE", "huge", "Huge", "centering", "kaishu",
    "onehalfspacing", "singlespacing", "doublespacing",
    # amsmath / amsthm（`proof` 在本刊两套模板里是 `\newenvironment` / `\renewenvironment`，
    # 而 `renewenvironment` 不在文本扫描口径内 → 必须靠白名单兜住，否则英文稿每篇误报）
    "proof", "equation", "eqnarray", "align", "alignat", "aligned", "alignedat",
    "gather", "gathered", "multline", "split", "flalign", "subequations",
    "cases", "dcases", "rcases", "matrix", "pmatrix", "bmatrix", "Bmatrix",
    "vmatrix", "Vmatrix", "smallmatrix",
    # tikz / 图表 / 算法类宏包
    "tikzpicture", "scope", "tikzcd", "axis",
    "algorithm", "algorithmic", "breakablealgorithm",
    "tcolorbox", "mdframed", "prooftree",
})



@dataclass
class DefItem:
    """可安全搬运的一条定义（作者前言 / 本地支撑文件 / 正文区都用同一形状）。"""

    kind: str  # newtheorem / newenvironment / operator / columntype / color
    #           / newcommand / renewcommand / providecommand / counter
    name: str
    source: str  # 原始定义文本（含注释行之外的部分）
    pos: int = 0  # 在**来源文本**里的起始下标（多文件合并后排序/日志用；0 = 未记）
    origin: str = ""  # 来源文件名（本地支撑文件）；作者前言 / 正文区为空


@dataclass
class PreamblePlan:
    """一次迁移计划：要插入的原始行 + 分类统计 + 被跳过的项（只用于日志）。"""

    lines: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    tikz_libs: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    auto_packages: list[str] = field(default_factory=list)  # 按正文符号依赖自动补的宏包
    # 第四十五批 A：按**正文环境依赖**从源稿本地支撑文件补的宏包（86-05 algorithm）
    env_packages: list[str] = field(default_factory=list)
    # 第 130 条：本地支撑文件（.cls/.sty/.def/.cfg）的读取与迁移明细
    support_files: list[str] = field(default_factory=list)      # 读到的本地文件名
    local_definitions: list[tuple[str, str]] = field(default_factory=list)  # (名, 来源)
    overrides: list[str] = field(default_factory=list)  # 正文用到、但模板已占用的本地重定义（未搬）
    converted_renews: list[str] = field(default_factory=list)  # 模板缺 → 由 \renewcommand 降为 \newcommand
    local_packages: list[str] = field(default_factory=list)  # 源目录有同名 .sty：不迁包行，只按差集搬定义
    counter_definitions: list[str] = field(default_factory=list)  # 第 130 条补丁：迁入的 \newcounter
    # 第 141 条：被模板 biblatex 取代、未迁移的参考文献宏包（natbib / gbt7714）
    replaced_packages: list[str] = field(default_factory=list)
    # 第 141 条：与模板已加载宏包重合同名命令、未迁移的作者宏包（人类可读说明）
    conflicts: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.lines


def rewrite_natbib_commands(text: str) -> tuple[str, int]:
    """把 natbib 专用引用命令改写成 biblatex 等价命令（第 141 条）。

    只在「模板用 biblatex、作者用了 natbib/gbt7714」时由 `build.py` 调用；
    映射见 `NATBIB_TO_BIBLATEX`（`\\cite` 不在其中——biblatex 本来就定义它）。

    返回 (新文本, 改写处数)。`\\citestyle{numbers}` / `\\setcitestyle` 这类**排版设置**
    命令不在此处理：它们只在作者导言区，包行不迁时整行已被丢掉。
    """
    total = 0
    out = text
    for natbib, biblatex in NATBIB_TO_BIBLATEX.items():
        # 负向环视避免误伤 \citestyle / \citepunct / \citetext 这类前缀相同的命令；
        # 星号形式（\citep*）一并保留（biblatex 对应命令同样有星号语义）。
        pat = re.compile(r"\\" + natbib + r"(?![A-Za-z])(\*?)")
        out, n = pat.subn(lambda m, cmd=biblatex: "\\" + cmd + m.group(1), out)
        total += n
    return out, total


def _line_comment_pos(text: str, pos: int) -> int | None:
    """pos 所在行内、位于 pos 之前的第一个未转义 `%` 下标（无则 None）。"""
    start = text.rfind("\n", 0, pos) + 1
    i = start
    while i <= pos:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "%":
            return i
        i += 1
    return None


def _clean_line(line: str) -> str:
    """去掉一行里未转义的注释部分。"""
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            out.append(line[i : i + 2])
            i += 2
            continue
        if ch == "%":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def source_preamble(text: str) -> str:
    """取 `.tex` 源稿 `\\begin{document}` 之前的部分。

    没有 `\\begin{document}` 的文件按「纯正文稿」处理（前言为空）——
    与 `sources.extract_body()` 的判据一致，避免把正文当前言搬进产物。
    """
    i = text.find("\\begin{document}")
    return text[:i] if i >= 0 else ""


def _read_group(text: str, i: int) -> tuple[str, int]:
    """读一个 `{...}` 组，返回（组内文本, 收尾后下标）。"""
    i = skip_ws_comments(text, i)
    if i >= len(text) or text[i] != "{":
        raise ValueError("不是 {")
    end = match_brace(text, i)
    return text[i + 1 : end], end + 1


def _skip_optional(text: str, i: int) -> int:
    """跳过可选的 `[...]` 组（跳过空白/注释后不是 `[` 就原样返回）。"""
    i = skip_ws_comments(text, i)
    if i < len(text) and text[i] == "[":
        j = text.find("]", i)
        if j >= 0:
            return j + 1
    return i


def scan_preamble(pre: str) -> list[DefItem]:
    """扫出可安全搬运的前言定义（保持原文顺序，同名同类去重取第一条）。

    认这六类**纯定义**：`\\newtheorem` / `\\newenvironment`（按环境名）、
    `\\DeclareMathOperator`（按宏名）、`\\newcolumntype`（按列类型）、`\\definecolor`、
    `\\newcounter`（第 130 条补丁：按计数器名；是否真搬由 `plan_migration` 按「用到」判）。
    其它内容（`\\setlength`、`\\setmainfont`、`\\geometry`…）属版面设置，不搬。
    """
    found: list[tuple[int, DefItem]] = []

    def usable(pos: int) -> bool:
        cp = _line_comment_pos(pre, pos)
        return cp is None or cp > pos

    for m in re.finditer(r"\\newtheorem\*?", pre):
        if not usable(m.start()):
            continue
        try:
            name, i = _read_group(pre, m.end())
            i = _skip_optional(pre, i)
            _title, i = _read_group(pre, i)
            i = _skip_optional(pre, i)
        except ValueError:
            continue
        name = name.strip()
        if re.fullmatch(r"[A-Za-z@][A-Za-z@0-9]*", name):
            found.append((m.start(), DefItem("newtheorem", name, pre[m.start() : i], pos=m.start())))

    for m in re.finditer(r"\\newenvironment\*?", pre):
        if not usable(m.start()):
            continue
        try:
            name, i = _read_group(pre, m.end())
            i = _skip_optional(pre, i)
            i = _skip_optional(pre, i)
            _begin, i = _read_group(pre, i)
            _end, i = _read_group(pre, i)
        except ValueError:
            continue
        name = name.strip()
        if re.fullmatch(r"[A-Za-z@][A-Za-z@0-9]*", name):
            found.append((m.start(), DefItem("newenvironment", name, pre[m.start() : i], pos=m.start())))

    for m in re.finditer(r"\\DeclareMathOperator\*?\s*", pre):
        if not usable(m.start()):
            continue
        try:
            raw, i = _read_group(pre, m.end())
            _value, i = _read_group(pre, i)
        except ValueError:
            continue
        name = raw.strip().lstrip("\\").strip()
        if re.fullmatch(r"[A-Za-z@]+", name):
            found.append((m.start(), DefItem("operator", name, pre[m.start() : i], pos=m.start())))

    for m in re.finditer(r"\\newcolumntype\s*", pre):
        if not usable(m.start()):
            continue
        try:
            name, i = _read_group(pre, m.end())
            i = _skip_optional(pre, i)
            _value, i = _read_group(pre, i)
        except ValueError:
            continue
        name = name.strip()
        if re.fullmatch(r"[A-Za-z@][A-Za-z@0-9]*", name):
            found.append((m.start(), DefItem("columntype", name, pre[m.start() : i], pos=m.start())))

    for m in re.finditer(r"\\definecolor\s*", pre):
        if not usable(m.start()):
            continue
        try:
            name, i = _read_group(pre, m.end())
            _model, i = _read_group(pre, i)
            _spec, i = _read_group(pre, i)
        except ValueError:
            continue
        name = name.strip()
        if re.fullmatch(r"[A-Za-z@][A-Za-z@0-9]*", name):
            found.append((m.start(), DefItem("color", name, pre[m.start() : i], pos=m.start())))

    # 第 130 条补丁：`\newcounter{X}[within]`。计数器名不占宏命名空间，且模板可能用
    # `\newtheorem` 隐式定义同名计数器（那由 defined_names 覆盖）——这里只收声明，
    # 搬不搬留给 plan_migration 的「正文/已迁定义体用到」判据。
    for m in re.finditer(r"\\newcounter\s*", pre):
        if not usable(m.start()):
            continue
        try:
            name, i = _read_group(pre, m.end())
            i = _skip_optional(pre, i)
        except ValueError:
            continue
        name = name.strip()
        if re.fullmatch(r"[A-Za-z@][A-Za-z@0-9]*", name):
            found.append((m.start(), DefItem("counter", name, pre[m.start() : i], pos=m.start())))

    seen: set[tuple[str, str]] = set()
    out: list[DefItem] = []
    for _pos, item in sorted(found, key=lambda x: x[0]):
        key = (item.kind, item.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


_NAME_PATTERNS = (
    r"\\newtheorem\*?\s*\{\s*([A-Za-z@][A-Za-z@0-9]*)\s*\}",
    r"\\newenvironment\*?\s*\{\s*([A-Za-z@][A-Za-z@0-9]*)\s*\}",
    r"\\DeclareMathOperator\*?\s*\{\s*\\([A-Za-z@]+)\s*\}",
    r"\\newcolumntype\s*\{\s*([A-Za-z@][A-Za-z@0-9]*)\s*\}",
    r"\\definecolor\s*\{\s*([A-Za-z@][A-Za-z@0-9]*)\s*\}",
    r"\\def\s*\\([A-Za-z@]+)",
    r"\\let\s*\\?([A-Za-z@]+)",
    r"\\newif\s*\\?(if[A-Za-z@]+)",
)


def defined_names(text: str) -> set[str]:
    """全文里已定义的宏/环境名（`\\newcommand` 之外还认模板会用到的各式定义）。

    注释掉的定义不算（`strip_comments` 后再扫）：模板里大量「可选行」是注释，
    把它们当成已定义会白白触发改名。
    """
    clean = strip_comments(text)
    names = set(parse_macros(clean))
    for pat in _NAME_PATTERNS:
        names.update(re.findall(pat, clean))
    return names


def package_names(text: str) -> set[str]:
    """全文加载过的宏包名（注释不算）。"""
    out: set[str] = set()
    for m in re.finditer(
        r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}",
        strip_comments(text),
    ):
        out.update(x.strip() for x in m.group(1).split(",") if x.strip())
    return out


def tikz_lib_names(text: str) -> set[str]:
    """全文加载过的 tikz 库（注释不算）。"""
    out: set[str] = set()
    for m in re.finditer(r"\\usetikzlibrary\s*\{([^}]*)\}", strip_comments(text)):
        out.update(x.strip() for x in m.group(1).split(",") if x.strip())
    return out


def _resolve_local_file(
    source_dir: str, name: str, exts: tuple[str, ...] = SUPPORT_EXTENSIONS
) -> str | None:
    """在 `source_dir` 里找 `name`（带扩展名按原样；不带则依次试 `exts`）。"""
    name = name.strip()
    if not name:
        return None
    if os.path.splitext(name)[1]:
        for cand in (name, name.lower()):
            path = os.path.join(source_dir, cand)
            if os.path.isfile(path):
                return path
        return None
    for ext in exts:
        for cand in (f"{name}{ext}", f"{name.lower()}{ext}"):
            path = os.path.join(source_dir, cand)
            if os.path.isfile(path):
                return path
    return None


def _read_support_files(
    paths: list[str], source_dir: str, _depth: int = 0, _seen: set[str] | None = None
) -> list[tuple[str, str]]:
    """读本地支撑文件，并递归读它们 `\\input` / `\\RequirePackage` 的**同目录**文件。

    只读同目录，不查 TeX 发行版；读不了就跳过（不静默报错——与引擎其它
    「找不到就不搬」的判据一致，日志另由调用方汇总）。
    """
    seen = _seen if _seen is not None else set()
    out: list[tuple[str, str]] = []
    for path in paths:
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        out.append((os.path.basename(path), text))
        if _depth >= 4:
            continue
        nested: list[str] = []
        clean = strip_comments(text)
        for m in _INPUT_NAME_RE.finditer(clean):
            name = m.group(1).strip()
            if not name or os.path.isabs(name):
                continue
            # \input{foo} 的 TeX 口径是 foo.tex；.def/.cfg/.sty 一般写全扩展名，这里都试
            found = _resolve_local_file(
                source_dir, name,
                exts=(".tex", ".sty", ".def", ".cfg") if not os.path.splitext(name)[1]
                else SUPPORT_EXTENSIONS,
            )
            if found:
                nested.append(found)
        for m in _ANY_PKG_RE.finditer(clean):
            for pkg in m.group(1).split(","):
                found = _resolve_local_file(source_dir, pkg, exts=(".sty",))
                if found:
                    nested.append(found)
        out.extend(_read_support_files(nested, source_dir, _depth + 1, seen))
    return out


def local_support_files(source_text: str, source_dir: str) -> list[tuple[str, str]]:
    """作者稿同目录下、由前言加载的本地支撑文件：`[(文件名, 文本), ...]`（第 130 条）。

    来源（保持前言里的出现顺序）：
    - `\\documentclass{本地类}` → 同目录 `<类>.cls`（找不到 = 系统类，忽略）；
    - `\\usepackage{...}` → 同目录 `<包>.sty`（第 127 条原有口径）；
    - 前言里直接 `\\input{*.sty|*.def|*.cfg}`（build 会先 `expand_inputs` 展开；
      这里兜底直接调用 `plan_migration` 的情形）；
    - 上述文件内部再 `\\input` / `\\RequirePackage` 的**同目录**文件（.def/.cfg 常这样被带进来）。

    只取文本，由 `plan_migration` 按「正文真正用到 + 模板没有」筛选。旧文件往往
    同时是旧版完整模板，整份搬会破坏现行模板（第 127 条教训）。
    """
    if not source_dir or not os.path.isdir(source_dir):
        return []
    pre = source_preamble(source_text)
    roots: list[str] = []
    for m in _DOCCLASS_RE.finditer(strip_comments(pre)):
        found = _resolve_local_file(source_dir, m.group(1), exts=(".cls",))
        if found:
            roots.append(found)
    for m in _ANY_PKG_RE.finditer(strip_comments(pre)):
        for pkg in m.group(1).split(","):
            found = _resolve_local_file(source_dir, pkg, exts=(".sty",))
            if found:
                roots.append(found)
    for m in _INPUT_NAME_RE.finditer(strip_comments(pre)):
        name = m.group(1).strip()
        if os.path.splitext(name)[1].lower() not in (".sty", ".def", ".cfg"):
            continue
        found = _resolve_local_file(source_dir, name)
        if found:
            roots.append(found)
    return _read_support_files(roots, source_dir)


_PKG_LINE_SCAN_RE = re.compile(
    r"\\(?:usepackage|RequirePackage)\s*(\[[^\]]*\])?\s*\{([^}]*)\}"
)


def env_package_lines(source_text: str, source_dir: str, body_text: str) -> list[str]:
    r"""正文用到、模板缺环境时，从**源稿确实写过**的本地支撑文件里取宏包行。

    第四十五批 A（86-05 Wei_Qiu 实测）：正文用 `algorithm`/`algorithmic`，宏包行
    写在作者同目录的旧 `SLCN.sty`（`\usepackage{algorithm}` / `\usepackage{algpseudocode}`）
    而不是主前言；初排换成现行模板后编译报
    `Environment algorithm undefined` / `\caption outside float`，整篇 rc=2。

    判据：① 环境在 `ENV_PACKAGE_HINTS` 里有实测对应；② 该包行**源稿里确实存在**
    （主前言或本地 .sty）；③ 过滤交给 `plan_migration`（模板已提供/冲突/同名本地包）。
    源稿没有 → 返回空列表，由 `build.py` 的定义缺失预检给 warning + 建议，**不加包**。
    """
    want: set[str] = set()
    for env in used_environments(body_text):
        want.update(ENV_PACKAGE_HINTS.get(env, ()))
    if not want:
        return []
    texts = [source_preamble(source_text)]
    if source_dir:
        texts.extend(text for _n, text in local_support_files(source_text, source_dir))
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for m in _PKG_LINE_SCAN_RE.finditer(strip_comments(text)):
            opts = m.group(1) or ""
            for p in [x.strip() for x in m.group(2).split(",") if x.strip()]:
                if p in want and p not in seen:
                    seen.add(p)
                    found.append(f"\\usepackage{opts}{{{p}}}")
    return found


def local_sty_text(source_text: str, source_dir: str) -> str:
    """（保留旧接口，第 127 条）作者稿同目录下、被 `\\usepackage` 引用的本地 .sty 文本。

    第 130 条起由 `local_support_files()` 统一承担（还含 .cls/.def/.cfg 与递归
    `\\input`）；这里只取 `.sty` 拼成文本，给还按「一份文本」用的调用方/测试。
    """
    return "\n".join(
        text for name, text in local_support_files(source_text, source_dir)
        if name.lower().endswith(".sty")
    )


def _top_level_macros(
    text: str, containers: list[tuple[int, int]]
) -> list[Macro]:
    """只取**顶层**的 `\\newcommand` 家族定义（第 130 条）。

    为什么必须过滤：旧 SLCN.sty 的 `\\definitionnamechange` 体里有一行
    `\\renewcommand{\\definitionname}{#1}`——按字面扫会把它误当成顶层重定义，
    于是「正文用到 + 模板已有」时白记一条 `\\renewcommand` 覆盖告警。
    `containers` 是其它定义（五类声明）的 [起, 止) 区间，落在里面的宏也算嵌套。
    """
    macros = find_all_macros(text)
    spans = [m.span for m in macros] + containers
    out = []
    for m in macros:
        nested = any(
            a <= m.span[0] and m.span[1] <= b and (a, b) != m.span for a, b in spans
        )
        if not nested:
            out.append(m)
    return out


def scan_support_definitions(text: str) -> list[DefItem]:
    """本地支撑文件 / 正文区里的**全部**可搬运定义（第 130 条，保持原文顺序）。

    = 六类声明（`\\newtheorem` / `\\newenvironment` / `\\DeclareMathOperator` /
    `\\newcolumntype` / `\\definecolor` / `\\newcounter`，与 `scan_preamble` 同口径；
    计数器只在 plan_migration 里按「用到」筛）
    + 顶层 `\\newcommand` / `\\renewcommand` / `\\providecommand`。

    - 嵌套在另一个定义体里的宏不算（见 `_top_level_macros`）；
    - 同名宏只留**最后一次**（作者常先 `\\newcommand` 再 `\\renewcommand`，正文用的是最后一次）；
    - `\\def` / `\\let` / `\\newif` 这类底层定义**不搬**（改语义风险高，见文档边界）。
    """
    decls = scan_preamble(text)
    containers = [(d.pos, d.pos + len(d.source)) for d in decls]
    last: dict[str, Macro] = {}
    for m in _top_level_macros(text, containers):
        last[m.name] = m  # find_all_macros 按出现顺序返回 → 后出现的覆盖先出现的
    items: list[DefItem] = list(decls)
    for m in last.values():
        items.append(
            DefItem(m.kind, m.name, text[m.span[0] : m.span[1]], pos=m.span[0])
        )
    items.sort(key=lambda d: d.pos)
    return items


# ── 「正文用到」判据（第 130 条）────────────────────────────────────────────
_COLOR_ARG_RE = re.compile(
    r"\\(?:textcolor|color|colorbox|fcolorbox|rowcolor|cellcolor|arrayrulecolor"
    r"|pagecolor|mathcolor)\*?\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}(?:\s*\{([^}]*)\})?"
)
_COLSPEC_ENV_RE = re.compile(
    r"\\begin\{(?:tabular|tabularx|array|longtable|tabulary|tabu)\*?\}"
    r"(?:\[[^\]]*\])?\s*\{([^{}]*)\}(?:\s*\{([^{}]*)\})?"
)
_MULTICOLUMN_RE = re.compile(r"\\multicolumn\s*\{\s*\d+\s*\}\s*\{([^{}]*)\}")
_BEGIN_ENV_RE = re.compile(r"\\begin\s*\{\s*([A-Za-z@][A-Za-z@0-9]*)\s*\}")


def used_environments(text: str) -> set[str]:
    """文本里 `\\begin{env}` 用到的环境名（注释不算）。"""
    return set(_BEGIN_ENV_RE.findall(strip_comments(text or "")))


def _color_arguments(text: str) -> str:
    """正文里颜色命令的参数（`\\textcolor{mygray}` / `\\colorbox{a}{b}` …）拼成一串。"""
    parts: list[str] = []
    for m in _COLOR_ARG_RE.finditer(text or ""):
        parts.extend(g for g in m.groups() if g)
    return "\n".join(parts)


def _column_specs(text: str) -> str:
    """正文里表格列格式串（`\\begin{tabular}{...}` / `\\multicolumn{n}{...}`）拼成一串。"""
    parts: list[str] = []
    for m in _COLSPEC_ENV_RE.finditer(text or ""):
        parts.extend(g for g in m.groups() if g)
    parts.extend(_MULTICOLUMN_RE.findall(text or ""))
    return "\n".join(parts)


def _name_in_arg_text(name: str, text: str) -> bool:
    """名字是否作为独立 token 出现在参数文本里（`Y` 不碰 `YY` / `mygray` 不碰 `mygray2`）。"""
    if not text:
        return False
    return re.search(
        r"(?<![A-Za-z@0-9])" + re.escape(name) + r"(?![A-Za-z@0-9])", text
    ) is not None


# 计数器名**不是控制序列**：`\theuserinput` 是「the + 计数名」的宏，`\setcounter{userinput}`
# 里名字是参数。所以「正文用到某计数器」要按这些形态认（第 130 条补丁）。
_COUNTER_ARG_RE = re.compile(
    r"\\(?:value|stepcounter|setcounter|addtocounter|refstepcounter|usecounter"
    r"|arabic|roman|Roman|alph|Alph|fnsymbol)\s*\{([^{}]*)\}"
)
_THE_COUNTER_RE = re.compile(r"\\the([A-Za-z@]+)")


def counter_uses(text: str) -> set[str]:
    r"""文本里用到的计数器名（`\theX` / `\value{X}` / `\setcounter{X}` / `\arabic{X}`…）。

    `\theX` 是「前缀 + 计数名」的控制序列（`\theuserinput` → `userinput`）。调用方会把
    结果与真实的 `\newcounter{X}` 候选名取交集，所以 `\theorem` / `\thepage` 这类普通过
    控制序列不会误命中不存在的计数器。
    """
    out: set[str] = set()
    for m in _COUNTER_ARG_RE.finditer(text or ""):
        name = m.group(1).strip()
        if name:
            out.add(name)
    out.update(_THE_COUNTER_RE.findall(text or ""))
    return out


def counter_names(text: str) -> set[str]:
    """文本里显式 `\\newcounter{X}` 声明过的计数器名（注释不算）。"""
    out: set[str] = set()
    for m in re.finditer(
        r"\\newcounter\s*\{\s*([A-Za-z@][A-Za-z@0-9]*)\s*\}", strip_comments(text)
    ):
        out.add(m.group(1))
    return out


def _counter_within(source: str) -> str:
    """`\\newcounter{X}[Y]` 里的父计数器 Y（没有则空串）。"""
    m = re.match(r"\\newcounter\s*\{[^{}]*\}\s*\[([^\]]*)\]", source.strip())
    return m.group(1).strip() if m else ""


def _order_counters(items: list[DefItem]) -> list[DefItem]:
    """计数器声明排序：`\\newcounter{X}[Y]` 要求父计数器 Y 先定义（其余保持原文顺序）。"""
    by_name = {it.name: it for it in items}
    out: list[DefItem] = []
    placed: set[str] = set()

    def place(it: DefItem) -> None:
        if it.name in placed:
            return
        parent = _counter_within(it.source)
        dep = by_name.get(parent) if parent else None
        if dep is not None:
            place(dep)
        placed.add(it.name)
        out.append(it)

    for it in items:
        place(it)
    return out


def _def_key(item: DefItem) -> tuple[str, str]:
    """定义去重用的命名空间键：环境 / 宏 / 列类型 / 颜色 / 计数器各自独立。"""
    if item.kind in _ENV_KINDS:
        return ("env", item.name)
    if item.kind == "columntype":
        return ("columntype", item.name)
    if item.kind == "color":
        return ("color", item.name)
    if item.kind == "counter":
        return ("counter", item.name)
    return ("macro", item.name)


def _shared_theorem_counter(source: str) -> str:
    """`\\newtheorem{X}[Y]{标题}` 里共享计数器环境名 Y（没有则空串）。"""
    m = re.match(
        r"^\s*\\newtheorem\*?\s*\{\s*[A-Za-z@][A-Za-z@0-9]*\s*\}\s*\[([^\]]*)\]",
        source,
    )
    return m.group(1).strip() if m else ""


def _item_used_by_body(
    item: DefItem,
    used_macros: set[str],
    used_envs: set[str],
    color_args: str,
    colspecs: str,
    used_counters: set[str] | None = None,
) -> bool:
    """「正文真正用到」的逐类判据（第 130 条）。

    宏/算子按 `\\名字` 出现；定理/环境按 `\\begin{名}`；颜色按颜色命令的参数；
    列类型按 tabular 列格式串；计数器按 `\\theX` / `\\setcounter{X}` 等形态
    （`\\newcounter` 的名字**不是**控制序列，不能按 `\\名字` 判）。
    """
    if item.kind in _ENV_KINDS:
        return item.name in used_envs
    if item.kind == "columntype":
        return _name_in_arg_text(item.name, colspecs)
    if item.kind == "color":
        return _name_in_arg_text(item.name, color_args)
    if item.kind == "counter":
        return item.name in (used_counters or set())
    return item.name in used_macros  # newcommand 家族 + operator


def missing_environments(
    body_text: str,
    provided_text: str = "",
    extra_names: set[str] | None = None,
) -> list[str]:
    """编译前的「定义缺失预检」（第 130 条）：正文 `\\begin{env}` 是否有着落。

    覆盖 = 模板/迁移结果/正文自身定义（`provided_text` + `extra_names`）+ 编译期才知道的
    基础/宏包环境（`BASE_ENVIRONMENTS`）。返回缺失环境名（排序）。

    **边界**：纯离线看不到宏包内部定义，`BASE_ENVIRONMENTS` 之外的宏包环境仍可能
    被报成缺失——所以这只是**提示**，不阻断出稿；日志会写明这一点。
    """
    used = used_environments(body_text)
    have = defined_names(provided_text or "") | BASE_ENVIRONMENTS | set(extra_names or ())
    return sorted(e for e in used if e not in have)


def _plan_local_definitions(
    plan: PreamblePlan,
    names: set[str],
    source_text: str,
    source_dir: str,
    body_text: str,
    skipped: list[str],
) -> None:
    r"""本地支撑文件里的定义 → 迁移计划（第 127 条环境 → 第 130 条六类 + 普通宏）。

    作者交来的旧模板稿常把定义放在同目录的 .sty/.cls/.def/.cfg 里：定理环境
    （第 127 条，期 87）只是其中一类；普通宏（`\definitionnamechange`、`\cp`/`\cpp`）、
    算子、列类型、颜色也在那里（2023(71) 04、2024(75) 05 真稿）。

    判据仍是「正文真正用到 + 模板没有」：旧文件往往同时是旧版完整模板，整份搬
    会破坏现行模板；模板已有的名字一律不搬（模板权威）。两种"不搬"必须说清楚：

      · 正文用到、模板已有的普通定义 → 记进 skipped（日志「模板已提供、未重复搬」）；
      · 正文用到、模板已有的 `\renewcommand`（作者明确想覆盖模板命令）→ plan.overrides，
        build 会单独打一行醒目警告（不许静默改变语义）。

    传递闭包覆盖「宏 A 定义里用到 B」「定理共享计数器 [Y]」以及「定义体用到的
    `\newcounter{X}`」；插入顺序按原文，共享计数器/依赖的计数器若排在后面则先插。
    """
    # ── 本地支撑文件里的定义（第 127 条环境 → 第 130 条扩到六类 + 普通宏）──────
    # 作者交来的旧模板稿常把定义放在同目录的 .sty/.cls/.def/.cfg 里：定理环境
    # （第 127 条，期 87 02 Hai_Xing）只是其中一类；普通宏（\definitionnamechange、
    # \cp/\cpp）、算子、列类型、颜色也在那里（2023(71) 04、2024(75) 05 真稿）。
    # 判据仍是「正文真正用到 + 模板没有」：旧文件往往同时是旧版完整模板，整份搬
    # 会破坏现行模板；模板已有的名字一律不搬（模板权威）。两种"不搬"必须说清楚：
    #   · 正文用到、模板已有的普通定义 → 记进 skipped（日志「模板已提供、未重复搬」）；
    #   · 正文用到、模板已有的 \renewcommand（作者明确想覆盖模板命令）→ overrides，
    #     build 会单独打一行醒目警告（不许静默改变语义）。
    local_items: list[DefItem] = []
    if source_dir:
        support_files = local_support_files(source_text, source_dir)
        plan.support_files = [name for name, _text in support_files]
        for fname, ftext in support_files:
            for item in scan_support_definitions(ftext):
                item.origin = fname
                local_items.append(item)
        # 同名只留最后一次（\newcommand 之后再来 \renewcommand 时正文用的是最后一次）
        last_at: dict[tuple[str, str], int] = {}
        for idx, item in enumerate(local_items):
            last_at[_def_key(item)] = idx
        local_items = [
            item for idx, item in enumerate(local_items)
            if last_at[_def_key(item)] == idx
        ]
        env_by = {it.name: it for it in local_items if it.kind in _ENV_KINDS}
        macro_by = {it.name: it for it in local_items if it.kind in _MACRO_KINDS}
        counter_by = {it.name: it for it in local_items if it.kind == "counter"}

        used_macros = macro_uses(body_text or "")
        used_envs = used_environments(body_text)
        color_args = _color_arguments(body_text)
        colspecs = _column_specs(body_text)
        used_counters = counter_uses(body_text or "")

        # 种子 = 正文用到、模板没有的定义；正文用到但模板已有的显式记账
        seed: list[DefItem] = []
        for item in local_items:
            if not _item_used_by_body(
                item, used_macros, used_envs, color_args, colspecs, used_counters
            ):
                continue
            if item.name in names:
                if item.kind == "renewcommand":
                    plan.overrides.append(item.name)
                else:
                    skipped.append(f"{item.name}（本地 {item.origin}，模板已有）")
                continue
            seed.append(item)

        # 传递闭包：宏 A 的定义里用到 B、定理 [共享计数器] 指向 Y、定义体用到计数器 X
        # → B / Y / X 也要搬（第 130 条补丁：\newcounter 依赖）
        needed: set[tuple[str, str]] = set()
        queue = list(seed)
        while queue:
            item = queue.pop(0)
            key = _def_key(item)
            if key in needed:
                continue
            needed.add(key)
            for dep in macro_uses(item.source):
                cand = macro_by.get(dep)
                if cand is not None and cand.name not in names and _def_key(cand) not in needed:
                    queue.append(cand)
            for cname in counter_uses(item.source):
                cand = counter_by.get(cname)
                if cand is not None and cand.name not in names and _def_key(cand) not in needed:
                    queue.append(cand)
            if item.kind == "newtheorem":
                counter = _shared_theorem_counter(item.source)
                cand = env_by.get(counter) if counter else None
                if cand is not None and cand.name not in names and _def_key(cand) not in needed:
                    queue.append(cand)
            if item.kind == "counter":
                parent = _counter_within(item.source)
                cand = counter_by.get(parent) if parent else None
                if cand is not None and cand.name not in names and _def_key(cand) not in needed:
                    queue.append(cand)

        # 插入顺序 = 原文顺序；定理共享计数器/定义体用到的计数器若排在后面，先插它们
        # （`\newcounter{X}[Y]` 要求 Y 先在；任务书要求计数器排在用它的环境定义之前）
        pending = {_def_key(it) for it in local_items if _def_key(it) in needed}
        ordered: list[DefItem] = []
        placed: set[tuple[str, str]] = set()

        def _emit(it: DefItem) -> None:
            key = _def_key(it)
            if key in placed:
                return
            for cname in counter_uses(it.source):
                dep = counter_by.get(cname)
                if dep is not None and _def_key(dep) in pending:
                    _emit(dep)
            if it.kind == "counter":
                parent = _counter_within(it.source)
                dep = counter_by.get(parent) if parent else None
                if dep is not None and _def_key(dep) in pending:
                    _emit(dep)
            if it.kind == "newtheorem":
                counter = _shared_theorem_counter(it.source)
                dep = env_by.get(counter) if counter else None
                if dep is not None and _def_key(dep) in pending:
                    _emit(dep)
            placed.add(key)
            ordered.append(it)

        for item in local_items:
            if _def_key(item) in pending:
                _emit(item)

        for item in ordered:
            if item.name in names:  # 闭包/去重后的双保险
                continue
            if item.kind == "renewcommand":
                # 模板没有这个名字：\renewcommand 在产物里会 undefined → 降为 \newcommand
                snippet = re.sub(
                    r"^(\s*)\\renewcommand", r"\1\\newcommand", item.source, count=1
                )
                plan.converted_renews.append(item.name)
            else:
                snippet = item.source
            plan.lines.append(snippet.strip())
            plan.definitions.append(item.name)
            if item.kind == "counter":
                plan.counter_definitions.append(item.name)
            plan.local_definitions.append((item.name, item.origin))
            names.add(item.name)


def plan_migration(
    source_text: str,
    skeleton_text: str,
    sty_text: str,
    template_sty_name: str = "",
    body_text: str = "",
    source_dir: str = "",
) -> PreamblePlan:
    """把作者稿前言与模板做差集，返回要插入的依赖行。

    - 宏包：模板（骨架 + .sty）已加载的同名包不重复；模板管理的包（ctex/geometry/
      hyperref/biblatex/本刊 sty…）一律不搬；带选项的按原样搬、不带选项的逐包拆行。
    - tikz 库：只搬模板没加载过的。
    - 定义：只搬模板里还没有同名宏/环境的（如模板已有 `definition` 定理环境，
      就不再搬作者的 `\\newtheorem{definition}`）。
    - `source_dir`：给了就读同目录的本地支撑文件（.cls/.sty/.def/.cfg，第 130 条），
      其定义同样只搬「正文用到 + 模板没有」的；`body_text` 是「正文用到」的唯一依据
      （空 = 本地支撑文件里的定义一个都不搬）。
    """
    plan = PreamblePlan()
    pre = source_preamble(source_text)
    if not pre.strip():
        return plan

    loaded = package_names(skeleton_text) | package_names(sty_text) | set(TEMPLATE_MANAGED_PACKAGES)
    if template_sty_name:
        loaded.add(template_sty_name)
    libs = tikz_lib_names(skeleton_text) | tikz_lib_names(sty_text)
    names = defined_names(skeleton_text) | defined_names(sty_text)
    # 第 130 条补丁：模板显式 `\newcounter{X}` 也算「模板已有」（`\newtheorem` 隐式定义的
    # 计数器已由 defined_names 的定理环境名覆盖）。当前两套模板都没有显式 newcounter。
    names |= counter_names(skeleton_text) | counter_names(sty_text)
    skipped: list[str] = []
    template_has_biblatex = bool(
        {"biblatex"} & (package_names(skeleton_text) | package_names(sty_text))
    )

    for raw in pre.splitlines():
        clean = _clean_line(raw)
        if not clean.strip():
            continue
        m = _PKG_LINE_RE.match(clean)
        if m:
            opts = m.group(1) or ""
            pkgs = [x.strip() for x in m.group(2).split(",") if x.strip()]
            need: list[str] = []
            for p in pkgs:
                if p in loaded:
                    skipped.append(p)
                    continue
                # 第 141 条：模板已用 biblatex 时，natbib / gbt7714 与它互斥 → 包行不迁。
                # 正文里的 natbib 专用引用命令由 rewrite_natbib_commands() 改写。
                if p in BIBLATEX_REPLACED_PACKAGES and template_has_biblatex:
                    if p not in plan.replaced_packages:
                        plan.replaced_packages.append(p)
                    continue
                # 第 141 条：与模板已加载宏包重合同名命令 → 整包不迁（否则加载阶段
                # 就报 `Command \X already defined`，整篇编译失败）。
                if p in PACKAGE_COMMAND_CONFLICTS:
                    reason, trigger = PACKAGE_COMMAND_CONFLICTS[p]
                    tpl_pkgs = package_names(skeleton_text) | package_names(sty_text)
                    if trigger in tpl_pkgs:
                        plan.conflicts.append(f"{p}（{reason}）")
                        continue
                # 第 130 条补丁：源目录里就有同名 .sty → 它是作者的旧模板/本地包，
                # **不迁包行**（顶部加载交给模板），文件按「本地支撑文件」差集搬定义。
                # 否则产物会 \usepackage 一个没被复制进产物目录的文件（File not found）。
                if source_dir and _resolve_local_file(source_dir, p, exts=(".sty",)):
                    if p not in plan.local_packages:
                        plan.local_packages.append(p)
                    continue
                need.append(p)
            if not need:
                continue
            if opts:
                # 带选项的包：按原选项搬，但只保留模板缺的那几个
                # （把模板已加载的包再带选项加载一次会触发 Option clash）
                plan.lines.append(f"\\usepackage{opts}{{{','.join(need)}}}")
                plan.packages.extend(need)
            else:
                for p in need:
                    plan.lines.append(f"\\usepackage{{{p}}}")
                    plan.packages.append(p)
            loaded.update(need)
            continue
        m = _TIKZ_LINE_RE.match(clean)
        if m:
            names_in_line = [x.strip() for x in m.group(1).split(",") if x.strip()]
            need = [x for x in names_in_line if x not in libs]
            skipped.extend(x for x in names_in_line if x in libs)
            if need:
                plan.lines.append(f"\\usetikzlibrary{{{','.join(need)}}}")
                plan.tikz_libs.extend(need)
                libs.update(need)
            continue

    # 作者前言里的定义：五类声明（模板没有就搬）在前；`\newcounter` 是第 130 条补丁新增
    # 的第六类，**只搬真正被用到的**——正文里直接用了（`\theX` 等），或已迁移的定义体里
    # 用了（如 `\newenvironment{liju}` 的 body 里 `\setcounter{userinput}`）。计数器统一
    # 排在定义块最前：`\newcounter`/`\setcounter` 在使用方之前就绪（任务书要求）。
    pre_items = scan_preamble(pre)
    pre_defs = [it for it in pre_items if it.kind != "counter"]
    pre_counters = [it for it in pre_items if it.kind == "counter"]
    author_counter_by = {it.name: it for it in pre_counters}
    to_migrate = [it for it in pre_defs if it.name not in names]
    skipped.extend(it.name for it in pre_defs if it.name in names)
    used_counters = counter_uses(body_text or "")
    for it in to_migrate:
        used_counters |= counter_uses(it.source)
    # ④ 里的作者 `\newcommand` 在正文可达时也会迁入；它们定义体里用到的计数器同样要
    # 跟着搬（否则「宏搬了、宏里用的计数器没搬」还是 undefined）。可达口径近似 ④：
    # 正文 + 已迁移定义体（元数据字段值在 plan 里拿不到，属已知小缺口）。
    reach_macros = macro_uses(body_text or "") | macro_uses("\n".join(plan.lines))
    for m in find_all_macros(pre):
        if m.name in reach_macros:
            used_counters |= counter_uses(m.value)
    # 计数器闭包：被用到的计数器 + 它的 `[父计数器]`（`\newcounter{X}[Y]` 要求 Y 先在）
    selected_counters: list[DefItem] = []
    seen_counter_names: set[str] = set()
    queue = [it for it in pre_counters if it.name in used_counters]
    while queue:
        it = queue.pop(0)
        if it.name in seen_counter_names or it.name in names:
            continue
        seen_counter_names.add(it.name)
        selected_counters.append(it)
        parent = _counter_within(it.source)
        cand = author_counter_by.get(parent) if parent else None
        if cand is not None:
            queue.append(cand)
    for it in _order_counters(selected_counters):
        plan.lines.append(it.source.strip())
        plan.definitions.append(it.name)
        plan.counter_definitions.append(it.name)
        names.add(it.name)
    for it in to_migrate:
        plan.lines.append(it.source.strip())
        plan.definitions.append(it.name)
        names.add(it.name)

    # 本地支撑文件（.cls/.sty/.def/.cfg）里的定义：只搬「正文用到 + 模板没有」的
    _plan_local_definitions(plan, names, source_text, source_dir, body_text, skipped)

    # 正文里用到、但作者前言/模板都没加载对应宏包的**符号类**宏 → 自动补宏包。
    # ⚠️ 作者自己 `\newcommand{\llbracket}` 时不能再补 stmaryrd（会 already defined；
    # 2026-10-17 评审在 89 期成品稿上实测到这条冲突）。
    if body_text:
        used = macro_uses(body_text)
        src_defined = defined_names(pre)
        # 第 130 条：本地支撑文件里正文可达的定义同样算「作者已自定义」（否则会补重复包）
        src_defined |= {name for name, _origin in plan.local_definitions}
        for syms, pkg in SYMBOL_PACKAGES:
            if pkg in loaded:
                continue
            hit = used.intersection(syms)
            if not hit:
                continue
            if hit & src_defined:
                skipped.append(
                    f"{pkg}（作者已自定义 {'、'.join(sorted(hit & src_defined))}，不补包）"
                )
                continue
            plan.lines.append(f"\\usepackage{{{pkg}}}")
            plan.packages.append(pkg)
            plan.auto_packages.append(pkg)
            loaded.add(pkg)

    # 第四十五批 A：正文环境用到的宏包写在**本地支撑文件**里时补迁移（86-05 实测）。
    if body_text and source_dir:
        for line in env_package_lines(source_text, source_dir, body_text):
            m = _PKG_LINE_RE.match(line)
            if not m:
                continue
            pkg = (m.group(2) or "").split(",")[0].strip()
            if not pkg or pkg in loaded:
                skipped.append(pkg)
                continue
            if pkg in BIBLATEX_REPLACED_PACKAGES and template_has_biblatex:
                if pkg not in plan.replaced_packages:
                    plan.replaced_packages.append(pkg)
                continue
            if pkg in PACKAGE_COMMAND_CONFLICTS:
                reason, trigger = PACKAGE_COMMAND_CONFLICTS[pkg]
                tpl_pkgs = package_names(skeleton_text) | package_names(sty_text)
                if trigger in tpl_pkgs:
                    plan.conflicts.append(f"{pkg}（{reason}）")
                    continue
            if _resolve_local_file(source_dir, pkg, exts=(".sty",)):
                if pkg not in plan.local_packages:
                    plan.local_packages.append(pkg)
                continue
            plan.lines.append(line)
            plan.packages.append(pkg)
            plan.env_packages.append(pkg)
            loaded.add(pkg)

    plan.skipped = sorted(set(skipped))
    return plan


def is_hyperref_line(line: str) -> bool:
    """是不是加载 hyperref 的那一行（CN/EN 模板的选项写法与空格不同）。"""
    s = line.strip()
    return s.startswith("%") is False and "\\usepackage" in s and "hyperref" in s


def insert_lines(template_text: str, lines: list[str], anchor: str) -> str:
    """把依赖块插到 `anchor` 行之前（模板约定 hyperref 是最后一个包）。

    找不到 anchor 就退到 `\\begin{document}` 之前；再找不到就追加到文件末尾
    （不静默丢弃——宁可多一行，也不要「计划了却没插进去」）。
    """
    if not lines:
        return template_text
    block = "\n".join(lines)
    rows = template_text.split("\n")
    for idx, ln in enumerate(rows):
        if (anchor and anchor in ln) or is_hyperref_line(ln):
            rows.insert(idx, block)
            return "\n".join(rows)
    for idx, ln in enumerate(rows):
        if ln.strip().startswith("\\begin{document}"):
            rows.insert(idx, block)
            return "\n".join(rows)
    return template_text.rstrip("\n") + "\n" + block + "\n"


_MACRO_USE_RE = re.compile(r"\\([A-Za-z@]+)")


def macro_uses(text: str) -> set[str]:
    """文本里出现的控制序列名（`\\foo` → foo）。"""
    return set(_MACRO_USE_RE.findall(text))


def select_custom_macros(
    macros: list[tuple[str, str, str]],
    body: str,
    existing_names: set[str],
) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    """挑出正文真正用得到的 `\\newcommand`，并给与模板/宏包重名的宏改名。

    入参/返回都是 `[(kind, name, 完整定义源码), ...]`（与
    `latex.collect_custom_macros()` 同形，保持调用方零适配）；返回值第二项是
    `{旧名: 新名}`（无冲突时为空）。

    为什么按「正文可达」过滤：作者前言里常有**正文根本没用**的定义（实测 07
    Fang_Wang 的 `\\newcommand{\\ar}`），照搬会与模板加载的宏包（xy 的 `\\ar`）
    撞名而编译失败。传递闭包保证「A 用到 B、B 用到 C」时 B、C 也会搬。
    为什么改名而不是跳过：正文确实用到了、名字又被模板占了（如模板
    `\\newtheorem{prop}` 占住 `\\prop`），跳过会让正文里那几处直接变成未定义命令。
    """
    by_name: dict[str, tuple[str, str, str]] = {
        name: (kind, name, src) for kind, name, src in macros
    }
    needed: set[str] = set()
    stack = [n for n in macro_uses(body) if n in by_name]
    while stack:
        n = stack.pop()
        if n in needed:
            continue
        needed.add(n)
        _kind, _name, src = by_name[n]
        value = parse_macros(src).get(n)
        if value is not None:
            stack.extend(
                r for r in macro_uses(value.value) if r in by_name and r not in needed
            )

    keep = [m for m in macros if m[1] in needed]
    renames: dict[str, str] = {}
    taken = set(existing_names) | set(by_name) | macro_uses(body)
    for _kind, name, _src in keep:
        if name in existing_names:
            new = _fresh_name(name, taken)
            renames[name] = new
            taken.add(new)
    return keep, renames


def _fresh_name(old: str, taken: set[str]) -> str:
    """给冲突宏取一个不撞名的别名（`\\prop` → `\\slcnprop`，再撞就加数字）。"""
    base = "slcn" + old
    cand = base
    i = 2
    while cand in taken:
        cand = f"{base}{i}"
        i += 1
    return cand


def apply_renames(text: str, renames: dict[str, str]) -> str:
    """按 `{旧名: 新名}` 把文本里的控制序列改名（词边界：`\\prop` 不碰 `\\proposal`）。"""
    for old, new in renames.items():
        text = re.sub(
            r"\\" + re.escape(old) + r"(?![A-Za-z@])",
            lambda _m, n=new: "\\" + n,
            text,
        )
    return text
