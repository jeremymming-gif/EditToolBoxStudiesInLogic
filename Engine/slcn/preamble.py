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
5. 正文**没用到**的 `\\newcommand` 不搬（如 07 Yiyan_Wang 前言里的 `\\ar`，正文根本
   没引用，搬过去反而与 xy 的 `\\ar` 冲突）——只搬正文可达的那些，并做传递闭包。

**不做什么**：不改 `.bib`；不搬 `\\documentclass` / `\\setmainfont` 等版面级设置；
不解析 TeX 发行版里宏包自带的定义（那是编译期信息，纯离线拿不到）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .latex import match_brace, parse_macros, skip_ws_comments, strip_comments

# 模板自己管理的宏包：作者稿里即使写了也不搬（选项/顺序与模板冲突，且模板是权威）。
# 当前 .sty 的名字（如 SLCN-MIX）由调用方追加进来。
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

# 常用「符号宏 → 宏包」对照（第 120 条，2026-10-17）。只收**符号类、不改变版面**的窄口
# 映射：作者正文用了这些宏、但作者前言与模板都没加载对应宏包时自动补上。实测 02
# Xiaoxuan_Fu 正文用了 \llbracket / \rrbracket（来自 stmaryrd），而它们只藏在作者同目录
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


@dataclass
class DefItem:
    """作者前言里可安全搬运的一条定义。"""

    kind: str  # newtheorem / newenvironment / operator / columntype / color
    name: str
    source: str  # 原始定义文本（含注释行之外的部分）


@dataclass
class PreamblePlan:
    """一次迁移计划：要插入的原始行 + 分类统计 + 被跳过的项（只用于日志）。"""

    lines: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    tikz_libs: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    auto_packages: list[str] = field(default_factory=list)  # 按正文符号依赖自动补的宏包

    @property
    def empty(self) -> bool:
        return not self.lines


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

    只认这五类**纯定义**：`\\newtheorem` / `\\newenvironment`（按环境名）、
    `\\DeclareMathOperator`（按宏名）、`\\newcolumntype`（按列类型）、`\\definecolor`。
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
            found.append((m.start(), DefItem("newtheorem", name, pre[m.start() : i])))

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
            found.append((m.start(), DefItem("newenvironment", name, pre[m.start() : i])))

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
            found.append((m.start(), DefItem("operator", name, pre[m.start() : i])))

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
            found.append((m.start(), DefItem("columntype", name, pre[m.start() : i])))

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
            found.append((m.start(), DefItem("color", name, pre[m.start() : i])))

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


def local_sty_text(source_text: str, source_dir: str) -> str:
    """作者稿同目录下、被 `\\usepackage` 引用的本地 .sty 文件的文本。

    第 127 条（期 87 02 Kun_Xing 实测）：作者交来的是**旧版本刊模板稿**，
    `\\usepackage{SLCN}` 引用的同目录 `SLCN.sty` 里定义了一批定理环境
    （`\\newtheorem{fact}{事实}` 等），而现行模板 SLCN-MIX 里没有 `fact`。
    旧 .sty 被当成「模板管理的包」整份跳过 → 正文 `\\begin{fact}` 编译失败。
    这里只取文本，由 `plan_migration` **只搬正文真正用到、模板又缺的定义**；
    旧 sty 里对版式的重定义（如 `\\renewenvironment{proof}`）不搬。
    """
    if not source_dir or not os.path.isdir(source_dir):
        return ""
    pre = source_preamble(source_text)
    chunks: list[str] = []
    seen: set[str] = set()
    for pkg in sorted(package_names(pre)):
        for cand in (f"{pkg}.sty", f"{pkg.lower()}.sty"):
            path = os.path.join(source_dir, cand)
            if os.path.isfile(path) and path not in seen:
                seen.add(path)
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        chunks.append(f.read())
                except OSError:
                    pass
                break
    return "\n".join(chunks)


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
    skipped: list[str] = []

    for raw in pre.splitlines():
        clean = _clean_line(raw)
        if not clean.strip():
            continue
        m = _PKG_LINE_RE.match(clean)
        if m:
            opts = m.group(1) or ""
            pkgs = [x.strip() for x in m.group(2).split(",") if x.strip()]
            need = [p for p in pkgs if p not in loaded]
            skipped.extend(p for p in pkgs if p in loaded)
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

    for item in scan_preamble(pre):
        if item.name in names:
            skipped.append(item.name)
            continue
        plan.lines.append(item.source.strip())
        plan.definitions.append(item.name)
        names.add(item.name)

    # 本地旧 .sty 里的定理/环境定义：只搬「正文确实 \\begin{...} 用到、模板又没有」的
    # （第 127 条）。旧 sty 往往还把版式重定义了一遍，整份搬会破坏现行模板。
    if source_dir:
        env_used = set(re.findall(
            r"\\begin\s*\{\s*([A-Za-z@][A-Za-z@0-9]*)\s*\}", body_text or ""))
        for item in scan_preamble(local_sty_text(source_text, source_dir)):
            if item.kind not in ("newtheorem", "newenvironment"):
                continue
            if item.name in names or item.name not in env_used:
                continue
            plan.lines.append(item.source.strip())
            plan.definitions.append(item.name)
            names.add(item.name)

    # 正文里用到、但作者前言/模板都没加载对应宏包的**符号类**宏 → 自动补宏包。
    # ⚠️ 作者自己 `\newcommand{\llbracket}` 时不能再补 stmaryrd（会 already defined；
    # 2026-10-17 评审在 89 期成品稿上实测到这条冲突）。
    if body_text:
        used = macro_uses(body_text)
        src_defined = defined_names(pre)
        for syms, pkg in SYMBOL_PACKAGES:
            if pkg in loaded:
                continue
            hit = used.intersection(syms)
            if not hit:
                continue
            if hit & src_defined:
                plan.skipped.append(
                    f"{pkg}（作者已自定义 {'、'.join(sorted(hit & src_defined))}，不补包）"
                )
                continue
            plan.lines.append(f"\\usepackage{{{pkg}}}")
            plan.packages.append(pkg)
            plan.auto_packages.append(pkg)
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
    Yiyan_Wang 的 `\\newcommand{\\ar}`），照搬会与模板加载的宏包（xy 的 `\\ar`）
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
