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

只写临时目录；不联网、不读稿件库、不跑 corpus.py / build.sh。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_latex_preamble.py
"""
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build  # noqa: E402
from slcn import preamble as PRE  # noqa: E402

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

shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
