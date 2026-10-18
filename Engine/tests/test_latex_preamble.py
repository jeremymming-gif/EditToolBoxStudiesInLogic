#!/usr/bin/env python3.14
r"""LaTeX 前言依赖迁移 + 多文件正文（**第 120 条**，2026-10-17 macCaseTest89 实测）。

缺陷
----
期 89 的 5 份真实 LaTeX 来稿用 1.1.0 初排：**.tex 5/5 产出、编译 0/5 通过**——
① 多文件稿 `\input{}` 没展开（正文只剩 1.9 KB）；② 作者前言的宏包 / tikz 库 /
`\DeclareMathOperator` / `\newcolumntype` 等定义没搬；③ 搬过去的宏名与模板
（`\newtheorem{prop}` 占住 `\prop`）或宏包（xy 的 `\ar`）撞名。

本套件钉住修复后的判据（纯离线；**不跑 xelatex**）：

  ① `preamble` 基础判据：包名 / tikz 库 / 定义 / 已定义名扫描；注释不算。
  ② `plan_migration()` 差集：模板已提供的不重复搬；模板管理项（ctex/geometry/
     hyperref/本刊 sty）不搬；带选项的包原样搬；`\newtheorem` 同名去重；
     正文符号依赖自动补包（`\llbracket` → stmaryrd，02 Xiaoxuan_Fu 场景）。
  ③ `build_latex()` 端到端：`\input` 展开（`expanded_inputs` + 正文内容）；
     包/库/定义进产物且只进一次；宏按「正文可达」过滤（未用到不搬）；传递闭包；
     宏名冲突自动改名（正文与定义同步）；元数据源前言同样参与迁移。
  ④ 默认行为：没有额外依赖的稿子，产物与从前一样（无新增包、无改名、无展开标记）。

之后追加（只增不改）：

  ⑤ 评审补钉（2026-10-17 独立评审发现的问题逐条钉住）。
  ⑥ 本地旧 .sty 的定理环境迁移（第 127 条，期 87 02 Kun_Xing）。
  ⑦ **定义位置加固**（第 130 条，2026-10-18）：本地 .sty 的六类定义 + 普通宏、
     本地 .cls / 嵌套 .def、正文区定义提示（不迁移）、定义缺失预检、Word 通道零变化；
     补丁：`\newcounter` 依赖迁移与顺序、本地非标准命名 .sty 的包行不迁（按差集搬定义）。

只写临时目录；不联网、不读稿件库、不跑 corpus.py / build.sh。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_latex_preamble.py
"""
import inspect
import os
import shutil
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build  # noqa: E402
from slcn import preamble as PRE  # noqa: E402
from slcn.latex import expand_inputs  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


TMP = tempfile.mkdtemp(prefix="slcn_latexpre_")
SRC_DIR = os.path.join(TMP, "src")
os.makedirs(SRC_DIR)


def write(name, text):
    path = os.path.join(SRC_DIR, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def build_one(body_name, out_name, **kwargs):
    out = os.path.join(TMP, "out_" + out_name)
    res = build.build_latex(
        os.path.join(SRC_DIR, body_name), out, basename=out_name,
        verbose=False, **kwargs,
    )
    tex = open(res.tex_path, encoding="utf-8").read()
    return res, tex


# ══════════════════════════════════════════════════════════════════════════
# ① preamble 基础判据
# ══════════════════════════════════════════════════════════════════════════
PRE_TEXT = r"""
\documentclass[b5paper]{article}
\usepackage{SLCN}
\usepackage{amsmath}
\usepackage[most]{tcolorbox}
\usepackage{tikz}
\usetikzlibrary{arrows.meta,positioning}
\DeclareMathOperator{\sPa}{\mathsf{sPa}}
\DeclareMathOperator*{\argmax}{arg\,max}
\newcolumntype{Y}{>{\raggedright\arraybackslash}X}
\definecolor{mygray}{gray}{0.9}
\newtheorem{conjecture}{猜想}[section]
\newtheorem{definition}{定义}[section]
\newenvironment{myblock}{\begin{quote}}{\end{quote}}
\newcommand{\usedbody}{U}
\newcommand{\unusedmacro}{X}
% \newcommand{\commentedout}{Y}
\begin{document}
\section{A}
这里用 $\llbracket$ 和 $\usedbody$。
\end{document}
"""

check("① package_names() 认逗号分隔 / 带选项的包",
      {"amsmath"} <= PRE.package_names(PRE_TEXT) and "tcolorbox" in PRE.package_names(PRE_TEXT))
check("① tikz_lib_names() 认多个库",
      PRE.tikz_lib_names(PRE_TEXT) == {"arrows.meta", "positioning"})
_n = PRE.defined_names(PRE_TEXT)
check("① defined_names() 认 \\newtheorem / \\newenvironment / 算子 / 列类型 / 颜色",
      {"conjecture", "definition", "myblock", "sPa", "argmax", "Y", "mygray"} <= _n)
check("① defined_names() 认 \\newcommand", {"usedbody", "unusedmacro"} <= _n)
check("① 注释掉的 \\newcommand 不算已定义", "commentedout" not in _n)

_scan = PRE.scan_preamble(PRE.source_preamble(PRE_TEXT))
_kinds = {(d.kind, d.name) for d in _scan}
check("① scan_preamble() 五类定义都收（theorem/环境/算子/列类型/颜色）",
      {("newtheorem", "conjecture"), ("newenvironment", "myblock"),
       ("operator", "sPa"), ("operator", "argmax"), ("columntype", "Y"),
       ("color", "mygray")} <= _kinds, f"{sorted(_kinds)}")
check("① 同一名字的定义只留一条（去重）",
      len([d for d in _scan if d.kind == "newtheorem"]) == 2)

# ══════════════════════════════════════════════════════════════════════════
# ② plan_migration() 差集
# ══════════════════════════════════════════════════════════════════════════
STY = (
    "\\usepackage{ctex}\n\\usepackage{amsmath}\n\\usepackage{tikz}\n"
    "\\usetikzlibrary{calc}\n\\newtheorem{prop}{命题}[section]\n"
    "\\newtheorem{definition}[prop]{定义}\n\\usepackage[pdfborder=0]{hyperref}\n"
)
SKEL = "\\documentclass{article}\n\\usepackage{SLCN-MIX}\n\\begin{document}\n\\end{document}\n"

_plan = PRE.plan_migration(PRE_TEXT, SKEL, STY, "SLCN-MIX",
                           body_text=r"$\llbracket$ $\usedbody$")
_pkgs = set(_plan.packages)
check("② 新宏包搬（tcolorbox 带选项）", "tcolorbox" in _pkgs)
check("② 带选项的包行保留选项",
      any("\\usepackage[most]{tcolorbox}" == ln for ln in _plan.lines))
check("② 模板已加载的包不重复搬（amsmath / tikz）", "amsmath" not in _pkgs and "tikz" not in _pkgs)
check("② 模板管理项不搬（SLCN / ctex / hyperref）",
      not ({"SLCN", "ctex", "hyperref"} & _pkgs))
check("② tikz 库只搬模板没有的（arrows.meta/positioning 搬，calc 不搬）",
      set(_plan.tikz_libs) == {"arrows.meta", "positioning"})
check("② 同一定义不重复搬（模板已有 definition，只搬 conjecture）",
      _plan.definitions.count("definition") == 0 and "conjecture" in _plan.definitions)
check("② 算子 / 列类型 / 颜色定义都进计划",
      {"sPa", "argmax", "Y", "mygray"} <= set(_plan.definitions))
check("② 环境定义进计划（myblock 模板没有）", "myblock" in _plan.definitions)
check("② 正文符号依赖自动补宏包（正文用 \\llbracket → stmaryrd，02 场景）",
      "stmaryrd" in _plan.auto_packages)
_plan2 = PRE.plan_migration(SKEL, SKEL, STY, "SLCN-MIX", body_text="没有特殊符号")
check("② 没有额外依赖时计划为空", _plan2.empty and not _plan2.auto_packages)

# ══════════════════════════════════════════════════════════════════════════
# ③ build_latex() 端到端
# ══════════════════════════════════════════════════════════════════════════
# 多文件稿：主线里 \input 一个分节文件
write("multi/main.tex", r"""\documentclass{article}
\newcommand{\usedbody}{U}
\newcommand{\unusedmacro}{X}
\newcommand{\A}{\B}
\newcommand{\B}{b}
\begin{document}
\section{第一节}
正文用 \usedbody 与 $\llbracket x \rrbracket$，还有 \A。
\input{sec}
\printbibliography
\end{document}
""")
write("multi/sec.tex", r"\section{第二节}" + "\n" + "分节文件的正文内容：甲乙丙。\n")
_res, _tex = build_one("multi/main.tex", "multi")
check("③ 多文件稿：expanded_inputs 计数 = 1", _res.expanded_inputs == 1,
      str(_res.expanded_inputs))
check("③ 多文件稿：分节文件内容真的进了正文", "分节文件的正文内容：甲乙丙。" in _tex)
check("③ 多文件稿：产物里没有残留 \\input{sec}", "\\input{sec}" not in _tex)
check("③ 多文件稿：日志说明已展开", any("已展开 1 处" in ln for ln in _res.log),
      str(_res.log[-3:]))
check("③ 宏按正文可达过滤：用到的搬、没用到的（unusedmacro）不搬",
      "\\newcommand{\\usedbody}" in _tex and "unusedmacro" not in _tex)
check("③ 传递闭包：正文用 \\A，\\A 定义里用的 \\B 也搬",
      "\\newcommand{\\B}" in _tex and "\\newcommand{\\A}" in _tex)
check("③ 正文符号依赖自动补包（正文用 \\llbracket，stmaryrd 自动进产物）",
      "\\usepackage{stmaryrd}" in _tex)
check("③ 前言包只插一次", _tex.count("\\usepackage{stmaryrd}") == 1)

# 宏名冲突：模板 sty 有 \newtheorem{prop}（占住 \prop），源稿又定义 \prop
write("conflict/main.tex", r"""\documentclass{article}
\newcommand{\prop}{\mathsf{Prop}}
\begin{document}
令 $\prop$ 为命题变元集合。
\end{document}
""")
_res2, _tex2 = build_one("conflict/main.tex", "conflict")
check("③ 宏名冲突：定义改名（\\prop → \\slcnprop）",
      "\\newcommand{\\slcnprop}" in _tex2 and "\\newcommand{\\prop}" not in _tex2)
check("③ 宏名冲突：正文同步改名（$\\slcnprop$）",
      "$\\slcnprop$" in _tex2 and "$\\prop$" not in _tex2)
check("③ 宏名冲突：warning 里明确写出改名",
      any("宏名冲突" in w and "slcnprop" in w for w in _res2.warnings),
      f"{_res2.warnings[:2]}")

# 元数据源前言也参与迁移（LaTeX 通道「正文来自新稿 + 依赖来自旧模板」）
write("meta/body.tex", r"""\begin{document}
正文用 \oldcmd 与 $\llbracket$。
\end{document}
""")
write("meta/old.tex", r"""\documentclass{article}
\usepackage{stmaryrd}
\newcommand{\oldcmd}{O}
\begin{document}
旧模板稿。
\end{document}
""")
_res3, _tex3 = build_one(
    "meta/body.tex", "meta", metadata_path=os.path.join(SRC_DIR, "meta/old.tex")
)
check("③ 元数据源前言参与依赖迁移（stmaryrd 来自旧模板稿）",
      "\\usepackage{stmaryrd}" in _tex3)
check("③ 元数据源里的自定义命令照旧搬", "\\newcommand{\\oldcmd}" in _tex3)

# 默认行为：无额外依赖 / 无宏 / 无 \input 的稿子，产物不夹带新东西
write("plain/main.tex", r"""\documentclass{article}
\begin{document}
\section{平凡的稿子}
只有正文，没有任何自定义依赖。
\end{document}
""")
_res4, _tex4 = build_one("plain/main.tex", "plain")
check("④ 无依赖稿：不新增宏包 / 不改名 / 不展开",
      "stmaryrd" not in _tex4 and "slcn" not in _tex4 and _res4.expanded_inputs == 0)
check("④ 无依赖稿：日志明确写「无需迁移」", any("无需迁移" in ln for ln in _res4.log))
# 同一输入两次构建 → 产物逐字节一致（含插入顺序稳定）
_out5 = os.path.join(TMP, "out_plain2")
build.build_latex(os.path.join(SRC_DIR, "plain/main.tex"), _out5,
                  basename="plain", verbose=False)
check("④ 同一输入两次构建产物逐字节一致",
      open(os.path.join(_out5, "plain.tex"), encoding="utf-8").read() == _tex4)

# `--compile` 只影响 PDF：本套件不跑 TeX，但确认产物 .tex 不含编译中间产物
check("④ 产物目录里只有契约文件（.tex/.sty）",
      sorted(os.listdir(os.path.join(TMP, "out_plain"))) == ["SLCN-MIX.sty", "plain.tex"],
      str(sorted(os.listdir(os.path.join(TMP, "out_plain")))))

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 评审补钉（2026-10-17 独立评审发现的问题，逐条钉住）
# ══════════════════════════════════════════════════════════════════════════
# P1：作者自己定义了 \\llbracket 时，不能自动补 stmaryrd（否则 already defined）
write("symdef/main.tex", r"""\documentclass{article}
\newcommand{\llbracket}{\left[\kern-0.15em\left[}
\newcommand{\rrbracket}{\right]\kern-0.15em\right]}
\begin{document}
用 $\llbracket x \rrbracket$。
\end{document}
""")
_res5, _tex5 = build_one("symdef/main.tex", "symdef")
check("⑤ P1：作者已定义 \\llbracket 时不自动补 stmaryrd",
      "\\usepackage{stmaryrd}" not in _tex5 and "\\newcommand{\\llbracket}" in _tex5)

# P2-1：宏只出现在元数据字段（摘要）里 → 也必须搬（可达文本包含字段值）
write("reach/body.tex", "\\begin{document}\n正文不提那个宏。\n\\end{document}\n")
write("reach/meta.tex", r"""\documentclass{article}
\newcommand{\foo}{F}
\newcommand{\myabstract}{摘要里用到 \foo。}
\begin{document}
正文。
\end{document}
""")
_res6, _tex6 = build_one(
    "reach/body.tex", "reach", metadata_path=os.path.join(SRC_DIR, "reach/meta.tex")
)
check("⑤ P2-1：只用在元数据字段里的宏也搬（摘要里 \\foo）",
      "\\newcommand{\\foo}" in _tex6)

# P2-2：注释掉的 \\input 不展开、不计数
write("cmt/main.tex", r"""\documentclass{article}
\begin{document}
正文开始。
% \input{extra}
\printbibliography
\end{document}
""")
write("cmt/extra.tex", "SECRET_LEAK 不应出现。\n")
_res7, _tex7 = build_one("cmt/main.tex", "cmt")
check("⑤ P2-2：注释掉的 \\input 不展开（内容不泄漏、计数 0）",
      "SECRET_LEAK" not in _tex7 and _res7.expanded_inputs == 0)
# 缺失目标不计入「已展开」
write("miss/main.tex", r"""\documentclass{article}
\begin{document}
\input{nope}
\end{document}
""")
_res8, _tex8 = build_one("miss/main.tex", "miss")
check("⑤ P2-2：缺失的 \\input 不计入 expanded_inputs（只算成功展开）",
      _res8.expanded_inputs == 0 and "\\input{nope}" in _tex8)

# P2-4：EN 模板的 hyperref 写法带空格，依赖仍要插在它之前
write("en/main.tex", r"""\documentclass{article}
\usepackage{stmaryrd}
\newtheorem{conj}{Conjecture}
\begin{document}
Text with $\llbracket x \rrbracket$.
\end{document}
""")
_res9, _tex9 = build_one("en/main.tex", "en", lang="en")
_elines = _tex9.split("\n")
try:
    _i_dep = next(i for i, ln in enumerate(_elines) if "usepackage{stmaryrd}" in ln)
    _i_hyp = next(i for i, ln in enumerate(_elines)
                  if "\\usepackage" in ln and "hyperref" in ln)
    _en_ok = _i_dep < _i_hyp
except StopIteration:
    _en_ok = False
check("⑤ P2-4：EN 模板下迁移的依赖插在 hyperref 之前（不靠精确字符串碰运气）", _en_ok)

# P3-1：\\renewcommand 撞名改名后要降为 \\newcommand（新名字此时还没定义）
write("renew/main.tex", r"""\documentclass{article}
\renewcommand{\prop}{\mathsf{Prop}}
\begin{document}
用 $\prop$。
\end{document}
""")
_res10, _tex10 = build_one("renew/main.tex", "renew")
check("⑤ P3-1：\\renewcommand 撞名改名后降为 \\newcommand",
      "\\newcommand{\\slcnprop}" in _tex10
      and "\\renewcommand{\\slcnprop}" not in _tex10)

# P3-2：带可选参数的 \\newcolumntype 也要迁移
write("coltype/main.tex", r"""\documentclass{article}
\usepackage{array}
\newcolumntype{P}[1]{>{\raggedright\arraybackslash}p{#1}}
\begin{document}
正文。
\end{document}
""")
_res11, _tex11 = build_one("coltype/main.tex", "coltype")
check("⑤ P3-2：带可选参数的 \\newcolumntype 迁移",
      "\\newcolumntype{P}[1]" in _tex11)

# P3-3：前言里的 \\input 先展开，再参与依赖/宏迁移
write("prein/main.tex", r"""\documentclass{article}
\input{defs}
\begin{document}
用 \foo。
\end{document}
""")
write("prein/defs.tex", r"\newcommand{\foo}{F}" + "\n")
_res12, _tex12 = build_one("prein/main.tex", "prein")
check("⑤ P3-3：前言 \\input 展开后参与宏迁移", "\\newcommand{\\foo}" in _tex12)

# P2-3：stage_original 遇到「源目录本来就在 original/ 内」不许删源
import convert  # noqa: E402

_guard_out = os.path.join(TMP, "guard_out")
_guard_src_dir = os.path.join(_guard_out, "original", "Author")
os.makedirs(_guard_src_dir)
_guard_src = os.path.join(_guard_src_dir, "main.tex")
with open(_guard_src, "w", encoding="utf-8") as f:
    f.write("\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n")
try:
    convert.stage_original(_guard_out, [(_guard_src, "正文源")],
                           copy_dir=True, verbose=False)
    _guard_ok = os.path.exists(_guard_src)
    _guard_err = ""
except Exception as e:  # noqa: BLE001
    _guard_ok = False
    _guard_err = f"{type(e).__name__}: {e}"
check("⑤ P2-3：stage_original 源在 original/ 内时跳过整目录归档、不删源",
      _guard_ok, _guard_err)

# 无 .bib 而正文有引用 → 明确警告（05 Zhen_Liang 场景）
write("nobib/main.tex", r"""\documentclass{article}
\begin{document}
引用 \cite{somekey}。
\end{document}
""")
_res13, _tex13 = build_one("nobib/main.tex", "nobib")
check("⑤ 无 .bib 而正文有引用时明确警告（不再静默）",
      any("没有 .bib" in w for w in _res13.warnings), str(_res13.warnings[-2:]))

# Word 通道 / 默认值：BuildResult 的 expanded_inputs 默认 0（build_docx 不传它）
check("⑤ BuildResult 默认 expanded_inputs=0（Word 通道不受影响）",
      build.BuildResult("t", None, None).expanded_inputs == 0)

# ══════════════════════════════════════════════════════════════════════════
# ⑥ 本地旧 .sty 的定理环境迁移（第 127 条，期 87 02 Kun_Xing 实测）
# ══════════════════════════════════════════════════════════════════════════
# 作者交来的是旧版本刊模板稿：`\usepackage{SLCN}` 的同目录 `SLCN.sty` 里定义了
# 一批定理环境（模板 SLCN-MIX 没有 fact）。旧 sty 被当「模板管理的包」整份跳过 →
# 正文 `\begin{fact}` 编译失败（Environment fact undefined）。修法：从本地旧 sty
# 里**只搬正文用到、模板又缺的定理/环境定义**；旧 sty 对版式的重定义不许搬。
write("oldsty/SLCN.sty", r"""\newtheoremstyle{mythm}{3pt}{3pt}{}{}{\bfseries}{}{5mm}{}
\theoremstyle{mythm}
\newtheorem{definition}{\hspace{2em}{\bf{定义}}}
\newtheorem{fact}{\hspace{2em}{\bf{事实}}}
\newtheorem{unusedenv}{\hspace{2em}{\bf{没用}}}
\renewenvironment{proof}[1][证明]{\textbf{#1:~~~}}{\hfill$\square$}
""")
write("oldsty/main.tex", r"""\documentclass{article}
\usepackage{SLCN}
\begin{document}
\begin{fact}\label{F1}
事实正文。
\end{fact}
\end{document}
""")
_res6, _tex6 = build_one("oldsty/main.tex", "oldsty")
check("⑥ 本地旧 .sty：正文用到的 fact 定义被搬进产物",
      "\\newtheorem{fact}" in _tex6)
check("⑥ 本地旧 .sty：未用到的定义不搬（unusedenv）",
      "unusedenv" not in _tex6)
check("⑥ 本地旧 .sty：模板已有的 definition 不重复搬（模板那份在 SLCN-MIX.sty 里，.tex 里应为 0）",
      _tex6.count("\\newtheorem{definition}") == 0,
      str(_tex6.count("\\newtheorem{definition}")))
check("⑥ 本地旧 .sty：旧 sty 的版式重定义不搬（proof）",
      "\\renewenvironment{proof}" not in _tex6)
check("⑥ 本地旧 .sty：旧 sty 不作为宏包搬（不出现 \\usepackage{SLCN}）",
      "\\usepackage{SLCN}" not in _tex6)

# ══════════════════════════════════════════════════════════════════════════
# ⑦ 本地支撑文件 + 定义位置加固（第 130 条，2026-10-18）
# ══════════════════════════════════════════════════════════════════════════
# 缺口：定义不在 .tex 导言区，而在作者同目录的旧 .sty（普通宏/算子/列类型/颜色）、
# 本地 .cls / .def / .cfg，或干脆写在正文区。真稿形态：
#   · 2023 年第 4 期 04：本地 SLCN.sty 的 \definitionnamechange，正文 8 处；
#   · 2024 年第 2 期 05：本地 SLCN-MIX.sty 的 \cp/\cpp，正文 12/15 处。
# 判据：仍只搬「正文用到 + 模板没有」；\renewcommand 覆盖模板命令时不许静默。

# ── ⑦-1 本地 .sty 的五类定义 + 普通宏（正文可达 + 模板缺 + 传递闭包）──────
write("defscope/SLCN-MIX.sty", r"""% 旧版本刊模板 sty（第 130 条合成夹具）
\newcommand{\definitionnamechange}[1]{%
  \renewcommand{\definitionname}{#1}%
}
\DeclareMathOperator{\varr}{\mathrm{Var}}
\newcolumntype{Z}{>{\centering\arraybackslash}X}
\definecolor{deepblue}{RGB}{0,0,139}
\newtheorem{fact}{\hspace{2em}{\bf{事实}}}
\newenvironment{myquote}{\begin{quote}\small}{\end{quote}}
\newcommand{\outercmd}{\innercmd}
\newcommand{\innercmd}{I}
\newcommand{\unusedcmd}{U}
\newcommand{\cndash}{--}
\renewcommand{\edash}{-}
""")
write("defscope/main.tex", r"""\documentclass{article}
\usepackage{SLCN-MIX}
\usepackage{array}
\usepackage{xcolor}
\begin{document}
\definitionnamechange{(5)\textsf{定义}}
$\varr(x)$，\outercmd，\cndash，\edash。
\begin{fact}事实正文。\end{fact}
\begin{myquote}引用。\end{myquote}
\begin{tabular}{Z}
a
\end{tabular}
\textcolor{deepblue}{蓝}
\end{document}
""")
_res7, _tex7 = build_one("defscope/main.tex", "defscope")
check("⑦ 本地 .sty：普通宏按正文可达搬（真稿形态 \\definitionnamechange）",
      "\\newcommand{\\definitionnamechange}" in _tex7)
check("⑦ 本地 .sty：\\DeclareMathOperator 搬", "\\DeclareMathOperator{\\varr}" in _tex7)
check("⑦ 本地 .sty：\\newcolumntype 搬（正文 tabular 列格式用到 Z）",
      "\\newcolumntype{Z}" in _tex7)
check("⑦ 本地 .sty：\\definecolor 搬（正文 \\textcolor 用到 deepblue）",
      "\\definecolor{deepblue}" in _tex7)
check("⑦ 本地 .sty：定理环境搬（fact）", "\\newtheorem{fact}" in _tex7)
check("⑦ 本地 .sty：普通环境搬（myquote）", "\\newenvironment{myquote}" in _tex7)
check("⑦ 本地 .sty：传递闭包（\\outercmd 用到 \\innercmd，两条都搬）",
      "\\newcommand{\\outercmd}" in _tex7 and "\\newcommand{\\innercmd}" in _tex7)
check("⑦ 本地 .sty：正文没用到的不搬（unusedcmd）", "unusedcmd" not in _tex7)
check("⑦ 本地 .sty：模板已有的普通定义不重复搬、日志明列（cndash）",
      "\\newcommand{\\cndash}" not in _tex7
      and any("cndash" in ln and "模板已提供" in ln for ln in _res7.log),
      str([ln for ln in _res7.log if "模板已提供" in ln])[:200])
check("⑦ 本地 .sty：\\renewcommand 覆盖模板命令时不静默（edash → 醒目警告、未搬）",
      not any("\\renewcommand{\\edash}" in ln for ln in _res7.log)
      and "\\renewcommand{\\edash}" not in _tex7
      and any("重定义了模板命令" in w and "edash" in w for w in _res7.warnings),
      str([w for w in _res7.warnings if "重定义" in w])[:200])
check("⑦ 本地 .sty：宏体里的嵌套 \\renewcommand 不算覆盖（definitionname 不误报）",
      not any("definitionname" in w and "重定义" in w for w in _res7.warnings))
check("⑦ 旧接口 local_sty_text() 仍可用（第 127 条兼容）",
      "\\definitionnamechange" in PRE.local_sty_text(
          open(os.path.join(SRC_DIR, "defscope/main.tex"), encoding="utf-8").read(),
          os.path.join(SRC_DIR, "defscope")))
_nested = PRE.scan_support_definitions(
    "\\newcommand{\\definitionnamechange}[1]{%\n"
    "  \\renewcommand{\\definitionname}{#1}%\n}\n")
check("⑦ scan_support_definitions()：只收顶层宏（嵌套定义被过滤）",
      [(d.kind, d.name) for d in _nested] == [("newcommand", "definitionnamechange")],
      str([(d.kind, d.name) for d in _nested]))

# ── ⑦-2 共享计数器闭包 + 插入顺序（[axmx] 计数器排在定理前）─────────────
write("defscope/counter.sty", r"""\newtheorem{thmx}[axmx]{定理X}
\newtheorem{axmx}{公理X}
""")
write("defscope/counter.tex", r"""\documentclass{article}
\usepackage{counter}
\begin{document}
\begin{thmx}定理正文。\end{thmx}
\end{document}
""")
_res8, _tex8 = build_one("defscope/counter.tex", "counter")
check("⑦ 定理共享计数器：正文只用 thmx，但 [axmx] 跟着搬",
      "\\newtheorem{axmx}" in _tex8 and "\\newtheorem{thmx}" in _tex8)
check("⑦ 定理共享计数器：计数器定义排在定理定义之前（否则 [axmx] 找不到）",
      _tex8.find("\\newtheorem{axmx}") < _tex8.find("\\newtheorem{thmx}"),
      f"{_tex8.find('\\newtheorem{axmx}')} / {_tex8.find('\\newtheorem{thmx}')}")

# ── ⑦-3 模板缺的 \renewcommand → 降为 \newcommand 迁入（显式警告）─────────
write("defscope/renewconv.sty", r"""\renewcommand{\brandnewcmd}{N}
""")
write("defscope/renewconv.tex", r"""\documentclass{article}
\usepackage{renewconv}
\begin{document}
正文用 \brandnewcmd。
\end{document}
""")
_res9, _tex9 = build_one("defscope/renewconv.tex", "renewconv")
check("⑦ 模板缺的 \\renewcommand：降为 \\newcommand 迁入",
      "\\newcommand{\\brandnewcmd}" in _tex9
      and "\\renewcommand{\\brandnewcmd}" not in _tex9)
check("⑦ 模板缺的 \\renewcommand：日志与警告都写明（不静默）",
      any("降为 \\newcommand" in ln for ln in _res9.log)
      and any("降为 \\newcommand" in w and "brandnewcmd" in w for w in _res9.warnings))

# 作者前言里的 \renewcommand 覆盖本地旧 sty 的同名定义 → 不算「撞名」，按 TeX 顺序覆盖
# （否则会被改名成没人用的新名字：旧稿编译时本来就是 sty 先定义、前言后覆盖）。
write("defscope/override2/SLCN-MIX.sty", r"""\newcommand{\sharedcmd}{S}
""")
write("defscope/override2/main.tex", r"""\documentclass{article}
\usepackage{SLCN-MIX}
\renewcommand{\sharedcmd}{T}
\begin{document}
正文用 \sharedcmd。
\end{document}
""")
_res_ov, _tex_ov = build_one("defscope/override2/main.tex", "override2")
check("⑦ 前言 \\renewcommand 覆盖本地旧 sty 定义：不误判撞名、不丢语义（\\sharedcmd）",
      "\\renewcommand{\\sharedcmd}" in _tex_ov
      and "\\newcommand{\\slcnsharedcmd}" not in _tex_ov
      and not any("宏名冲突" in w for w in _res_ov.warnings),
      str([w for w in _res_ov.warnings if "宏名冲突" in w])[:160])
check("⑦ 前言 \\renewcommand 覆盖本地旧 sty 定义：sty 定义在前、前言覆盖在后",
      _tex_ov.find("\\newcommand{\\sharedcmd}") < _tex_ov.find("\\renewcommand{\\sharedcmd}"),
      f"{_tex_ov.find('\\newcommand{\\sharedcmd}')} / "
      f"{_tex_ov.find('\\renewcommand{\\sharedcmd}')}")

# ── ⑦-4 本地 .cls + 类里 \input 的 .def（同规则：正文用到 + 模板缺）─────
write("defscope/MyJournal.cls", r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{MyJournal}
\LoadClass{article}
\input{classdefs.def}
\newcommand{\classcmd}{C}
\newtheorem{lemma2}{引理}
\newcommand{\classunused}{X}
""")
write("defscope/classdefs.def", r"""\newcommand{\deffilecmd}{D}
\DeclareMathOperator{\rank}{rank}
""")
write("defscope/extra.cfg", r"""\newcommand{\cfgcmd}{F}
""")
write("defscope/macrodefs.sty", r"""\newcommand{\styfilecmd}{S}
""")
write("defscope/classmain.tex", r"""\documentclass[b5paper,10.5t]{MyJournal}
\input{extra.cfg}
\input{macrodefs.sty}
\begin{document}
正文用 \classcmd、\deffilecmd、\cfgcmd、\styfilecmd，$\rank(A)$。
\begin{lemma2}引理。\end{lemma2}
\end{document}
""")
_class_path = os.path.join(SRC_DIR, "defscope/classmain.tex")
_class_src = open(_class_path, encoding="utf-8").read()
_class_exp = expand_inputs(_class_path, _class_src)
check("⑦ 前言 \\input{*.cfg} / \\input{*.sty} 由 expand_inputs 展开",
      "\\newcommand{\\cfgcmd}" in _class_exp
      and "\\newcommand{\\styfilecmd}" in _class_exp
      and "\\input{extra.cfg}" not in _class_exp
      and "\\input{macrodefs.sty}" not in _class_exp)
_res10, _tex10 = build_one("defscope/classmain.tex", "classmain")
check("⑦ 本地 .cls：\\newcommand 定义迁入（classcmd）", "\\newcommand{\\classcmd}" in _tex10)
check("⑦ 本地 .cls：定理环境定义迁入（lemma2）", "\\newtheorem{lemma2}" in _tex10)
check("⑦ 本地 .cls：类里 \\input 的 .def 定义迁入（deffilecmd / rank）",
      "\\newcommand{\\deffilecmd}" in _tex10
      and "\\DeclareMathOperator{\\rank}" in _tex10)
check("⑦ 本地 .cls：正文没用到的不搬（classunused）", "classunused" not in _tex10)
check("⑦ 前言 \\input 的 .cfg/.sty 里的定义参与迁移（cfgcmd / styfilecmd）",
      "\\newcommand{\\cfgcmd}" in _tex10 and "\\newcommand{\\styfilecmd}" in _tex10)

# ── ⑦-5 正文区定义：不迁移、只提示（正文整段照搬已保留）─────────────────
write("defscope/bodydef.tex", r"""\documentclass{article}
\begin{document}
\newcommand{\inbodycmd}{B}
\newenvironment{bodyenv}{}{\relax}
正文用 \inbodycmd。
\begin{bodyenv}正文里定义的环境。\end{bodyenv}
\end{document}
""")
_res11, _tex11 = build_one("defscope/bodydef.tex", "bodydef")
check("⑦ 正文区定义：日志打一行「正文里出现 N 处…建议移到导言区」",
      any("正文里出现 2 处自定义定义（保留原样，建议移到导言区）" in ln
          for ln in _res11.log),
      str([ln for ln in _res11.log if "正文里出现" in ln]))
check("⑦ 正文区定义：不迁移（产物里只出现正文那一份，不重复定义）",
      _tex11.count("\\newcommand{\\inbodycmd}") == 1
      and _tex11.count("\\newenvironment{bodyenv}") == 1,
      f"{_tex11.count('\\newcommand{\\inbodycmd}')} / "
      f"{_tex11.count('\\newenvironment{bodyenv}')}")
check("⑦ 正文区自身定义的环境不算缺失（bodyenv 不进预检警告）",
      not any("bodyenv" in w for w in _res11.warnings))

# ── ⑦-6 定义缺失预检：醒目警告、不阻断出稿、基础环境不误报 ────────────────
write("defscope/missenv.tex", r"""\documentclass{article}
\begin{document}
\begin{itemize}\item 列表。\end{itemize}
\begin{equation}x\end{equation}
\begin{theorem}模板定理环境。\end{theorem}
\begin{nosuchenv}没定义的环境。\end{nosuchenv}
\end{document}
""")
_res12, _tex12 = build_one("defscope/missenv.tex", "missenv")
check("⑦ 缺失预检：正文用到的未知环境进警告（nosuchenv）",
      any("定义缺失预检" in w and "nosuchenv" in w for w in _res12.warnings),
      str([w for w in _res12.warnings if "预检" in w])[:220])
check("⑦ 缺失预检：日志有醒目 ⚠ 行",
      any("⚠ 定义缺失预检" in ln for ln in _res12.log))
_miss_warns = [w for w in _res12.warnings if "定义缺失预检" in w]
check("⑦ 缺失预检：内核/模板环境不误报（itemize / equation / theorem 不在缺失清单）",
      bool(_miss_warns)
      and all("itemize" not in w and "equation" not in w and "theorem" not in w
              for w in _miss_warns),
      str(_miss_warns)[:220])
check("⑦ 缺失预检不阻断出稿：.tex 照常写出",
      os.path.isfile(_res12.tex_path) and len(_tex12) > 1000,
      f"{os.path.isfile(_res12.tex_path)} / {len(_tex12)}")
check("⑦ 预检函数：正文定义可当覆盖（extra_names）",
      PRE.missing_environments(
          "\\begin{bodyenv}x\\end{bodyenv}", "\\begin{document}",
          {"bodyenv"}) == [])
check("⑦ 预检白名单：模板用 \\renewenvironment 定义的 proof 不误报（英文模板形态）",
      PRE.missing_environments(
          "\\begin{proof}x\\end{proof}",
          "\\renewenvironment{proof}[1][Proof.]{}{}") == [])

# ── ⑦-6b 撞名改名复用既有逻辑（本地支撑文件在场时照样生效）─────────────────
write("defscope/rename/SLCN-MIX.sty", r"""\newcommand{\localnewcmd}{L}
""")
write("defscope/rename/main.tex", r"""\documentclass{article}
\usepackage{SLCN-MIX}
\newcommand{\prop}{\mathsf{Prop}}
\begin{document}
正文用 \localnewcmd 与 $\prop$。
\end{document}
""")
_res_rn, _tex_rn = build_one("defscope/rename/main.tex", "rename")
check("⑦ 撞名改名复用既有逻辑：本地支撑文件在场时，作者宏 \\prop → \\slcnprop 照旧",
      "\\newcommand{\\slcnprop}" in _tex_rn
      and "\\newcommand{\\prop}" not in _tex_rn
      and "$\\slcnprop$" in _tex_rn,
      str([w for w in _res_rn.warnings if "宏名冲突" in w])[:160])
check("⑦ 撞名改名与本地定义迁移共存（localnewcmd 也搬）",
      "\\newcommand{\\localnewcmd}" in _tex_rn)

# ── ⑦-9 \newcounter 迁移（第 130 条补丁，2023(71)04 的 liju/userinput 残留）─────
# 判据：只在「正文或已迁移定义体用到」时搬（\theX / \setcounter{X} / \value{X}…）；
# 顺序在使用它的环境定义之前；`\newcounter{X}[Y]` 的父计数器一起搬且排在前面。
write("defscope/counter2/main.tex", r"""\documentclass{article}
\newcounter{userinput}
\newcounter{myctr}
\newcounter{unusedctr}
\newcounter{parentonly}
\newcounter{child}[parentonly]
\newcounter{macroctr}
\newenvironment{liju}[1]{\setcounter{userinput}{#1}（\theuserinput）}{}
\newcommand{\macroctruse}{\themacroctr}
\begin{document}
\begin{liju}{3}例句。\end{liju}
正文计数 \themyctr，还有 \thechild，以及 \macroctruse。
\end{document}
""")
_res_ct, _tex_ct = build_one("defscope/counter2/main.tex", "counter2")
check("⑦ \\newcounter：正文/已迁环境体用到的计数器搬（userinput / myctr）",
      "\\newcounter{userinput}" in _tex_ct and "\\newcounter{myctr}" in _tex_ct)
check("⑦ \\newcounter：正文没用到的不搬（unusedctr）", "unusedctr" not in _tex_ct)
check("⑦ \\newcounter：`[父计数器]` 的父计数器一起搬（parentonly）",
      "\\newcounter{parentonly}" in _tex_ct
      and _tex_ct.find("\\newcounter{parentonly}") < _tex_ct.find("\\newcounter{child}"),
      f"{_tex_ct.find('\\newcounter{parentonly}')} / {_tex_ct.find('\\newcounter{child}')}")
check("⑦ \\newcounter：计数器排在用它的环境定义之前（userinput < liju）",
      _tex_ct.find("\\newcounter{userinput}") < _tex_ct.find("\\newenvironment{liju}"),
      f"{_tex_ct.find('\\newcounter{userinput}')} / {_tex_ct.find('\\newenvironment{liju}')}")
check("⑦ \\newcounter：日志明列（计数器迁移 5 个）",
      any("计数器迁移 5 个" in ln for ln in _res_ct.log),
      str([ln.strip() for ln in _res_ct.log if "计数器" in ln]))
check("⑦ \\newcounter：作者宏定义体里用到的计数器也搬（macroctr，与 ④ 的宏一起）",
      "\\newcounter{macroctr}" in _tex_ct
      and "\\newcommand{\\macroctruse}" in _tex_ct)
write("defscope/ctrdup/main.tex", r"""\documentclass{article}
\newcounter{prop}
\begin{document}
正文 \theprop。
\end{document}
""")
_res_cd, _tex_cd = build_one("defscope/ctrdup/main.tex", "ctrdup")
check("⑦ \\newcounter：模板已有同名计数器（prop 定理计数器）不重复搬",
      "\\newcounter{prop}" not in _tex_cd)
# 本地支撑文件里的计数器：与正文可达判据同一套（localenv 体里 stepcounter）
write("defscope/localctr/localctr.sty", r"""\newcounter{localctr}
\newcounter{localunused}
\newcounter{localparent}
\newcounter{localchild}[localparent]
\newenvironment{localenv}{\stepcounter{localctr}（\thelocalctr）\thelocalchild}{}
""")
write("defscope/localctr/main.tex", r"""\documentclass{article}
\usepackage{localctr}
\begin{document}
\begin{localenv}例子。\end{localenv}
\end{document}
""")
_res_lc, _tex_lc = build_one("defscope/localctr/main.tex", "localctr")
check("⑦ 本地 .sty 里的 \\newcounter：被本地环境体用到 → 与定义一起搬、顺序在前",
      "\\newcounter{localctr}" in _tex_lc
      and "\\newenvironment{localenv}" in _tex_lc
      and _tex_lc.find("\\newcounter{localctr}") < _tex_lc.find("\\newenvironment{localenv}"))
check("⑦ 本地 .sty 里的 \\newcounter：没用到的不搬（localunused）",
      "localunused" not in _tex_lc)
check("⑦ 本地 .sty 里的 \\newcounter：`[父计数器]` 也搬且排在前面（localparent < localchild）",
      "\\newcounter{localparent}" in _tex_lc
      and "\\newcounter{localchild}" in _tex_lc
      and _tex_lc.find("\\newcounter{localparent}") < _tex_lc.find("\\newcounter{localchild}"))

# ── ⑦-10 本地非标准命名 .sty：不迁包行、按支撑文件差集搬定义（77/04 SLCN2）──
write("defscope/localpkg/SLCN2.sty", r"""\newcommand{\localpkgcmd}{S}
\newcounter{localpkgctr}
\newcommand{\unusedlocalpkg}{U}
\renewcommand{\cndash}{--}
""")
write("defscope/localpkg/command.sty", r"""\newcommand{\cmdA}{\rightarrow}
""")
write("defscope/localpkg/main.tex", r"""\documentclass{article}
\usepackage{SLCN2}
\usepackage{multirow}
\input{command.sty}
\begin{document}
正文用 \localpkgcmd、\cmdA、\cndash，计数 \thelocalpkgctr。
\end{document}
""")
_res_lp, _tex_lp = build_one("defscope/localpkg/main.tex", "localpkg")
check("⑦ 本地非标准 .sty：\\usepackage{SLCN2} 的包行**不迁**（模板负责顶部加载）",
      "\\usepackage{SLCN2}" not in _tex_lp)
check("⑦ 本地非标准 .sty：日志写明包行不迁、只按差集搬定义",
      any("本地旧模板 .sty" in ln and "SLCN2" in ln and "包行不迁" in ln
          for ln in _res_lp.log),
      str([ln.strip() for ln in _res_lp.log if "包行不迁" in ln])[:160])
check("⑦ 本地非标准 .sty：定义仍按差集迁入（localpkgcmd / 计数器）",
      "\\newcommand{\\localpkgcmd}" in _tex_lp
      and "\\newcounter{localpkgctr}" in _tex_lp
      and "unusedlocalpkg" not in _tex_lp)
check("⑦ 本地非标准 .sty：\\renewcommand 覆盖模板命令的判据照旧（cndash 模板优先+告警）",
      not any("\\renewcommand{\\cndash}" in ln for ln in _res_lp.log)
      and any("重定义了模板命令" in w and "cndash" in w for w in _res_lp.warnings),
      str([w for w in _res_lp.warnings if "重定义" in w])[:160])
check("⑦ 本地非标准 .sty：非本地宏包照旧迁移（multirow）",
      "\\usepackage{multirow}" in _tex_lp)
check("⑦ 本地非标准 .sty：\\input{command.sty} 的本地文件支持未回退（cmdA 迁入）",
      "\\newcommand{\\cmdA}" in _tex_lp)

# ── ⑦-7 Word 通道零变化（新增逻辑只在 LaTeX 通道；Word 产物/日志不夹带）──
_docx_src = inspect.getsource(build.build_docx)
check("⑦ Word 通道零变化：build_docx 不经过第 130 条的新逻辑",
      not any(k in _docx_src for k in
              ("scan_support_definitions", "missing_environments", "local_support_files")))
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _make_docx(paras):
    ps = "".join(f"<w:p><w:r><w:t>{escape(t)}</w:t></w:r></w:p>" for t in paras)
    xml = f'<w:document xmlns:w="{_W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(prefix="slcn_word130_", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return path


_docx_path = _make_docx(
    ["论一个平凡的题目", "张三", "中山大学哲学系", "author@univ.edu.cn",
     "摘要：本文讨论一个平凡的问题。", "关键词：逻辑", "正文第一段。", "正文第二段。"])
_docx_out = os.path.join(TMP, "out_word130")
_docx_res = build.build_docx(_docx_path, _docx_out, basename="word", verbose=False)
_docx_tex = open(_docx_res.tex_path, encoding="utf-8").read()
check("⑦ Word 通道零变化：日志里没有正文定义提示 / 本地支撑文件 / 缺失预检",
      not any(k in ln for ln in _docx_res.log
              for k in ("正文里出现", "本地支撑文件", "定义缺失预检")))
check("⑦ Word 通道零变化：warnings 里没有定义缺失预检",
      not any("定义缺失预检" in w for w in _docx_res.warnings))
check("⑦ Word 通道零变化：产物照常（含正文与模板字段）",
      "正文第一段。" in _docx_tex and "\\begin{document}" in _docx_tex)

# ══════════════════════════════════════════════════════════════════════════
# ⑧ natbib 与模板 biblatex 冲突（第 141 条，2026-10-18；期 87 子案例 4 实测）
# ══════════════════════════════════════════════════════════════════════════
# 症状：作者 LaTeX 稿前言写 `\usepackage[numbers,sort&compress]{natbib}`，正文用
# `\cite`；引擎把 natbib 当普通宏包照搬进产物 → 模板的 biblatex 报
# `Package biblatex Error: Incompatible package 'natbib'` + `\bibhang already defined`
# → 整篇编译失败（05 Pengfei_Song）。
# 修法：模板已用 biblatex 时，natbib/gbt7714 包行不迁；正文 natbib 专用引用命令
# 改写成 biblatex 等价命令。
_natbib_src = r"""\documentclass{article}
\usepackage[numbers,sort&compress]{natbib}
\citestyle{numbers}
\begin{document}
正文\citep{a1}与\citet[p.~3]{a2}，还有\citep*{a3}；\citestyle{numbers} 不应被改写。
\end{document}
"""
write("natbib/main.tex", _natbib_src)
_plan = PRE.plan_migration(
    _natbib_src,
    "\\documentclass{article}\n\\usepackage{biblatex}\n",
    "\\usepackage{biblatex}\n",
    "SLCN-MIX",
    body_text="正文\\citep{a1}与\\citet[p.~3]{a2}",
)
check("⑧ 模板用 biblatex 时 natbib 包行不迁（replaced_packages 记录）",
      _plan.replaced_packages == ["natbib"]
      and not any("natbib" in ln for ln in _plan.lines), str(_plan.lines))
_rw, _n = PRE.rewrite_natbib_commands(_natbib_src)
check("⑧ natbib 引用命令改写：\\citep→\\parencite、\\citet→\\textcite、星级保留",
      "\\parencite{a1}" in _rw and "\\textcite[p.~3]{a2}" in _rw
      and "\\parencite*{a3}" in _rw and _n == 3, f"n={_n}")
check("⑧ 前缀相同的 natbib 排版命令不被误伤（\\citestyle 原样）",
      _rw.count("\\citestyle{numbers}") == 2)
# 反例：模板不用 biblatex 时，natbib 仍按原逻辑迁移（不能一刀切全丢）
_plan2 = PRE.plan_migration(
    _natbib_src, "\\documentclass{article}\n", "", "othertemplate", body_text="")
check("⑧ 反例：模板不用 biblatex 时 natbib 照旧迁移",
      not _plan2.replaced_packages
      and any("natbib" in ln for ln in _plan2.lines), str(_plan2.lines))
# 端到端：产物里没有 natbib 包行，引用命令是 biblatex 的
_res8, _tex8 = build_one("natbib/main.tex", "natbib")
check("⑧ 端到端：产物不出现 natbib 包行",
      "usepackage" not in "".join(
          ln for ln in _tex8.splitlines() if "natbib" in ln and not ln.lstrip().startswith("%")))
check("⑧ 端到端：正文引用已改写为 biblatex 命令",
      "\\parencite{a1}" in _tex8 and "\\textcite[p.~3]{a2}" in _tex8)
check("⑧ 端到端：日志/warnings 明确说明 biblatex 取代 natbib",
      any("biblatex 取代" in ln for ln in _res8.log)
      and any("biblatex" in w for w in _res8.warnings))

# ── 第 141 条附：bbding 与模板 marvosym 的 \Cross 重名 ─────────────────────
# 症状：模板 SLCN-MIX/SLEN 都 `\usepackage{marvosym}`（定义 `\Cross`= \mvchr{134}）；
# 作者前言 `\usepackage{bbding}` 也被迁过去 → 加载 bbding 时报
# `Command \Cross already defined`，整篇编译失败（05 Pengfei_Song）。
_bbding_src = r"""\documentclass{article}
\usepackage{bbding}
\begin{document}
正文。
\end{document}
"""
write("bbding/main.tex", _bbding_src)
_plan_c = PRE.plan_migration(
    _bbding_src, "\\usepackage{marvosym}\n", "\\usepackage{marvosym}\n",
    "SLCN-MIX", body_text="正文。",
)
check("⑧ 模板加载 marvosym 时 bbding 整包不迁（conflicts 记录原因）",
      any("bbding" in c for c in _plan_c.conflicts)
      and not any("bbding" in ln for ln in _plan_c.lines), str(_plan_c.conflicts))
_res_c, _tex_c = build_one("bbding/main.tex", "bbding")
check("⑧ 端到端：产物不出现 bbding 包行", "\\usepackage{bbding}" not in _tex_c)
check("⑧ 端到端：warnings 说明与模板宏包冲突",
      any("重合同名命令" in w and "bbding" in w for w in _res_c.warnings),
      str(_res_c.warnings))
# 反例：模板没加载 marvosym 时 bbding 照旧迁移（不能一刀切）
_plan_c2 = PRE.plan_migration(
    _bbding_src, "\\documentclass{article}\n", "", "othertemplate", body_text="正文。")
check("⑧ 反例：模板无 marvosym 时 bbding 照旧迁移",
      not _plan_c2.conflicts and any("bbding" in ln for ln in _plan_c2.lines),
      str(_plan_c2.lines))

shutil.rmtree(TMP, ignore_errors=True)


print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
