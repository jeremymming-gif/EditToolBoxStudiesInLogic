#!/usr/bin/env python3.14
r"""Springer **sn-jnl** 完整期刊类来稿的两处迁移修复（第 142 条补丁，2026-11-16）。

缺陷
----
作者交来的是「完整模板稿」——`\documentclass{sn-jnl}` 加一个同目录的 `sn-jnl.cls`。
这个类不是作者的自定义宏集合，而是一整套期刊版式：它成批 `\renewcommand` 了
LaTeX 内核/标准类已经定义的名字（字号 `\small`、分节 `\section`/`\subsection`/
`\subsubsection`、标题页 `\maketitle`…），还用 `\RequirePackage{natbib}` 装了
参考文献宏包（前言里一个字都没有）。旧引擎把它们当「作者的本地旧模板定义」搬：

  ① 迁 `\renewcommand{\small}` 等 → 套进本刊类后语义全错 / 降为 `\newcommand`
     则 `Command \X already defined` 致命错，整篇无 PDF；
  ② 作者的 `\citep` 原样留在正文（触发条件从前只看作者前言有没有
     `\usepackage{natbib}`）→ biblatex 下 `Undefined control sequence`；
  ③ sn-jnl 专有的前置命令 `\affil` / `\keywords` / `\pacs` 留在正文里
     → 本刊模板没有这些定义，编译即炸。

本套件钉住修复后的判据（纯离线；**不跑 xelatex**，只到 build_latex 产出 `.tex`）：

  ① `KERNEL_DEFINED_NAMES` 跳过表本身（成员与规模）；
  ② 内核/类内已定义命令**不迁**：不进 `definitions`、不进 `lines`，
     只记 `kernel_skipped` + `skipped` 日志（**防回归的关键**：搬回去就编译失败）；
  ③ **防过度跳过**：真·新名照旧迁，`\renewcommand` 照旧降级为 `\newcommand`；
  ④ natbib 触发条件收窄：正文真出现 `\citep/\citet/\citealt/\citealp` 且模板走
     biblatex 才登记（负向环视排除 `\citestyle`/`\citepunct`/`\citetext`；`\cite` 不触发）；
  ⑤ `extract_body_text` 的守卫切除：`\title`→`\maketitle` 段内出现别家专有命令时整段删；
  ⑥ 守卫反例：纯 `article` 稿的 `\title/\author/\maketitle` 段**整段保留**（单位/邮箱
     只写在 `\author{}` 里，盲删会丢信息）；
  ⑦ 边界：跨行花括号体、`\maketitle` 之后的前置信息、无 `\section` 短稿、正文区里的
     `\affil`（不碰正文）；
  ⑧ 端到端：`build_latex()` 产物里上述三类残渣都不出现。

夹具全部用 `tempfile` 现造（**不读** `~/Downloads/sn-article-template/`、不读稿件库、
不联网）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 \
        tests/test_sn_jnl_migration_20261116.py
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build  # noqa: E402
from slcn import preamble as PRE  # noqa: E402
from slcn import sources as SRC  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ══════════════════════════════════════════════════════════════════════════
# 夹具（全部自包含）
# ══════════════════════════════════════════════════════════════════════════
TMP = tempfile.mkdtemp(prefix="slcn_snjnl_")
SRC_DIR = os.path.join(TMP, "src")
os.makedirs(SRC_DIR)

# 简化版 sn-jnl.cls：结构与真实类同形（\renewcommand 内核命令 + \RequirePackage{natbib}
# + 真·作者自定义宏）。`\footnotesize` 刻意定义但正文**不用**——钉住「种子筛选在前」。
SN_CLS = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{sn-jnl}
\LoadClass{article}
\renewcommand{\small}{\fontsize{8}{10}\selectfont}
\renewcommand{\footnotesize}{\fontsize{7}{9}\selectfont}
\renewcommand{\section}{\@startsection{section}{1}{\z@}}
\renewcommand{\subsection}{\@startsection{subsection}{2}{\z@}}
\renewcommand{\subsubsection}{\@startsection{subsubsection}{3}{\z@}}
\newcommand{\brandnewcmd}[1]{\textbf{#1}}
\renewcommand{\renewonly}[1]{\emph{#1}}
\RequirePackage{natbib}
"""


def write(name, text):
    path = os.path.join(SRC_DIR, name)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


write("sn-jnl.cls", SN_CLS)

# 模板骨架：本刊两套模板都在导言区加载 biblatex（`template_has_biblatex` 的判据来源）。
SKEL_BIBLATEX = "\\documentclass{article}\n\\usepackage{biblatex}\n"
SKEL_NO_BIBLATEX = "\\documentclass{article}\n\\usepackage{amsmath}\n"

# sn 风格正文：简体版真实稿形态（两个 \affil、\keywords、\maketitle 之后才 \section）。
SN_BODY = r"""\title{标题}
\author{张三}
\author[2]{李四}
\affil[1]{某某大学数学系}
\affil[2]{另一大学物理系}
\keywords{范畴论, 模型论}
\maketitle
\section{引言}
正文用小字 \small 排，引用见 \citep{key1}，叙述式见 \citet{key2}。
\subsection{小节}
\subsubsection{小小节}
新命令 \brandnewcmd{x}，重定义 \renewonly{y}。
"""

# 内核/类内已定义、本套件钉住「不迁」的四个名字（真实类都会重定义这批）。
KERNEL_CASE = ("small", "section", "subsection", "subsubsection")

SKIP_MARK = "LaTeX 内核/文档类已有，未搬"


def sn_source(body=SN_BODY, extra_pre=""):
    """sn 风格整份 .tex（前言里 `\\documentclass{sn-jnl}`，正文即 body）。"""
    return ("\\documentclass{sn-jnl}\n" + extra_pre
            + "\\begin{document}\n" + body + "\n\\end{document}\n")


def plan(body=SN_BODY, skel=SKEL_BIBLATEX, source_dir=SRC_DIR, src_text=None,
         sty=""):
    """跑一遍 plan_migration（source_dir 默认指向含 sn-jnl.cls 的临时目录）。"""
    return PRE.plan_migration(
        src_text if src_text is not None else sn_source(body),
        skel, sty, "SLCN-MIX", body_text=body, source_dir=source_dir,
    )


# ══════════════════════════════════════════════════════════════════════════
# ① 跳过表本身
# ══════════════════════════════════════════════════════════════════════════
print("\n── ① KERNEL_DEFINED_NAMES 跳过表 ──")
check("① 跳过表存在且是 frozenset（常量，不许运行期被改）",
      isinstance(PRE.KERNEL_DEFINED_NAMES, frozenset), type(PRE.KERNEL_DEFINED_NAMES).__name__)
check("① 跳过表规模 = 29 个名字（改表要同步改本断言与文档）",
      len(PRE.KERNEL_DEFINED_NAMES) == 29, f"实际 {len(PRE.KERNEL_DEFINED_NAMES)}")
_missing_kernel = [n for n in KERNEL_CASE if n not in PRE.KERNEL_DEFINED_NAMES]
check("① 跳过表含本批四个名字（small/section/subsection/subsubsection）",
      not _missing_kernel, f"缺：{_missing_kernel}" if _missing_kernel else "四个都在")
for _grp, _names in (
    ("字号", ("tiny", "scriptsize", "footnotesize", "normalsize", "large",
              "Large", "LARGE", "huge", "Huge")),
    ("分节", ("part", "chapter", "paragraph", "subparagraph")),
    ("标题页", ("title", "author", "date", "thanks", "maketitle", "abstract",
                "abstractname")),
):
    _lack = [n for n in _names if n not in PRE.KERNEL_DEFINED_NAMES]
    check(f"① 跳过表覆盖「{_grp}」类内核命令（{len(_names)} 个）",
          not _lack, f"缺：{_lack}" if _lack else "")
check("① 真·作者自定义宏不在跳过表里（否则会误跳过）",
      "brandnewcmd" not in PRE.KERNEL_DEFINED_NAMES
      and "renewonly" not in PRE.KERNEL_DEFINED_NAMES)

# ══════════════════════════════════════════════════════════════════════════
# ② 内核/类内已定义命令不迁（第 142 条补丁的主判据）
# ══════════════════════════════════════════════════════════════════════════
print("\n── ② 内核/类内已定义命令不迁（本地 .cls 的 \\renewcommand）──")
PLAN = plan()
check("② 本地支撑文件认出来了（sn-jnl.cls）",
      PLAN.support_files == ["sn-jnl.cls"], str(PLAN.support_files))
_missing_skip = [n for n in KERNEL_CASE
                 if not any(entry.startswith(n + "（") for entry in PLAN.kernel_skipped)]
check("② kernel_skipped 记下四个内核名（正文都用到）",
      not _missing_skip, f"实际 {PLAN.kernel_skipped}")
check("② kernel_skipped 条目格式 = 「名（来源文件）」",
      "small（sn-jnl.cls）" in PLAN.kernel_skipped, str(PLAN.kernel_skipped))
_leaked = [n for n in KERNEL_CASE if n in PLAN.definitions]
check("② 四个内核名都不进 definitions（不迁入）", not _leaked,
      f"混进 definitions：{_leaked}" if _leaked else str(PLAN.definitions))
for _n in KERNEL_CASE:
    check(f"② lines 里没有 \\renewcommand{{\\{_n}}}（搬回去就 already defined）",
          not any((("\\renewcommand{\\" + _n + "}") in ln) for ln in PLAN.lines),
          str(PLAN.lines))
check("② 四个内核名都不进 converted_renews（不许降级迁入）",
      not any(n in PLAN.converted_renews for n in KERNEL_CASE),
      str(PLAN.converted_renews))
_bad_skip = [e for e in PLAN.skipped if any(e.startswith(n + "（") for n in KERNEL_CASE)
             and SKIP_MARK not in e]
check("② skipped 日志对四个内核名都写明「LaTeX 内核/文档类已有，未搬」（不静默）",
      not _bad_skip, f"缺说明：{_bad_skip}" if _bad_skip else str(PLAN.skipped))
check("② 正文没用到 \\footnotesize → 不记 kernel_skipped（种子筛选在前，不是整表扫）",
      not any(e.startswith("footnotesize（") for e in PLAN.kernel_skipped),
      str(PLAN.kernel_skipped))
_pp = plan(body="\\smallskip 只是一段空行。")
check("② 边界：正文只写 \\smallskip 时 small 不记 kernel_skipped（按 \\名字 判，不按前缀）",
      not any(e.startswith("small（") for e in _pp.kernel_skipped),
      str(_pp.kernel_skipped))

# ══════════════════════════════════════════════════════════════════════════
# ③ 防过度跳过：真·新名仍迁、\renewcommand 仍降级
# ══════════════════════════════════════════════════════════════════════════
print("\n── ③ 防过度跳过：真·新名仍迁（本地 .cls 的 \\newcommand / \\renewcommand）──")
check("③ 真·新名 brandnewcmd 照旧迁入 definitions",
      "brandnewcmd" in PLAN.definitions, str(PLAN.definitions))
check("③ lines 里保留 brandnewcmd 的完整定义体",
      "\\newcommand{\\brandnewcmd}[1]{\\textbf{#1}}" in PLAN.lines, str(PLAN.lines))
check("③ 模板缺对应命令的 \\renewcommand 仍降为 \\newcommand 迁入（renewonly）",
      "renewonly" in PLAN.converted_renews, str(PLAN.converted_renews))
check("③ 降级后的行写成 \\newcommand{\\renewonly}[1]{\\emph{#1}}",
      "\\newcommand{\\renewonly}[1]{\\emph{#1}}" in PLAN.lines, str(PLAN.lines))
check("③ 降级后的行没留原样 \\renewcommand{\\renewonly}",
      not any("\\renewcommand{\\renewonly}" in ln for ln in PLAN.lines), str(PLAN.lines))
check("③ 内核表没有把整批定义都吞掉（definitions 非空）",
      len(PLAN.definitions) == 2, str(PLAN.definitions))

# ══════════════════════════════════════════════════════════════════════════
# ④ natbib 触发条件收窄（第 142 条补丁：从「前言有没有 natbib」改为「正文有没有命令」）
# ══════════════════════════════════════════════════════════════════════════
print("\n── ④ natbib 触发条件（正文命令 + 模板 biblatex 两条件同时成立）──")
check("④ 正文有 \\citep、模板走 biblatex → replaced_packages 含 natbib",
      "natbib" in PLAN.replaced_packages, str(PLAN.replaced_packages))
for _cmd in ("\\citep", "\\citet", "\\citealt", "\\citealp", "\\citep*"):
    _pp = plan(body=f"引用见 {_cmd}{{key1}}。", source_dir="")
    check(f"④ 正文 {_cmd} 触发 natbib 登记",
          "natbib" in _pp.replaced_packages, str(_pp.replaced_packages))
_pp = plan(body="\\citestyle{numbers} \\citepunct{x} \\citetext{y}", source_dir="")
check("④ 负向环视：\\citestyle/\\citepunct/\\citetext 不触发（同前缀排版命令）",
      _pp.replaced_packages == [], str(_pp.replaced_packages))
_pp = plan(body="只有 \\cite{key1}。", source_dir="")
check("④ 只有 \\cite 不触发（biblatex 本来就定义 \\cite，不改写）",
      _pp.replaced_packages == [], str(_pp.replaced_packages))
_pp = plan(body="见 \\citep{key1}。", skel=SKEL_NO_BIBLATEX, source_dir="")
check("④ 模板不走 biblatex 时 \\citep 不触发（不能一刀切）",
      _pp.replaced_packages == [], str(_pp.replaced_packages))
_pp = PRE.plan_migration(sn_source("见 \\citep{key1}。"), SKEL_BIBLATEX, "",
                         "SLCN-MIX", body_text="", source_dir="")
check("④ body_text 为空时不触发（没有正文就无从判断）",
      _pp.replaced_packages == [], str(_pp.replaced_packages))
_pp = plan(body="正文没有引用命令。", skel=SKEL_BIBLATEX, source_dir="",
           src_text=sn_source("正文没有引用命令。", extra_pre="\\usepackage{natbib}\n"))
check("④ 作者前言写了 \\usepackage{natbib} + biblatex 模板 → 登记 natbib",
      "natbib" in _pp.replaced_packages, str(_pp.replaced_packages))
check("④ 作者前言里的 \\usepackage{natbib} 包行不迁（biblatex 与它互斥）",
      not any("usepackage" in ln and "natbib" in ln for ln in _pp.lines), str(_pp.lines))
_pp = plan(body="\\citep{k1} \\citet{k2} \\citealt{k3} \\citealp{k4}", source_dir="")
_txt, _n = PRE.rewrite_natbib_commands("\\citep{k1} \\citet{k2} \\citealt{k3} "
                                       "\\citealp{k4} \\cite{k5}")
check("④ rewrite_natbib_commands 把四个命令改写、\\cite 原样（4 处）",
      _n == 4 and "\\parencite{k1}" in _txt and "\\textcite{k2}" in _txt
      and "\\cite{k5}" in _txt, f"n={_n} {_txt}")
_txt2, _n2 = PRE.rewrite_natbib_commands("\\citestyle{numbers} \\citepunct{x} "
                                         "\\citetext{y}")
check("④ rewrite_natbib_commands 不误伤 \\citestyle/\\citepunct/\\citetext（0 处）",
      _n2 == 0 and _txt2 == "\\citestyle{numbers} \\citepunct{x} \\citetext{y}",
      f"n={_n2} {_txt2}")

# ══════════════════════════════════════════════════════════════════════════
# ⑤ extract_body_text：别家文档类前置区整段切除
# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑤ extract_body_text 守卫切除（sn 风格前置区）──")
SN_DOC = r"""\begin{document}
\title{标题}
\author{张三}
\author[2]{李四}
\affil[1]{某某大学数学系}
\affil[2]{另一大学物理系}
\keywords{范畴论, 模型论}
\maketitle
\section{引言}
正文用 \citep{key1}。
\end{document}
"""
SN_OUT = SRC.extract_body_text(SN_DOC)
check("⑤ sn 前置区整段删：输出无 \\affil",
      "\\affil" not in SN_OUT, repr(SN_OUT))
check("⑤ sn 前置区整段删：输出无 \\keywords",
      "\\keywords" not in SN_OUT, repr(SN_OUT))
check("⑤ sn 前置区整段删：输出无 \\maketitle（否则产物会有两个标题页）",
      "\\maketitle" not in SN_OUT, repr(SN_OUT))
check("⑤ 正文完好：\\section 与 \\citep 都留着（守卫只动前置区）",
      "\\section{引言}" in SN_OUT and "\\citep{key1}" in SN_OUT, repr(SN_OUT))
check("⑤ 输出以正文首行为第一行（前置区不是被留白，是整段切掉）",
      SN_OUT.startswith("\\section{引言}"), repr(SN_OUT[:40]))

_MULTI = r"""\begin{document}
\title{标题}
\author{张三}
\affil[1]{某某大学数学系,
  概率统计教研室 \textit{概率组}}
\keywords{范畴论}
\maketitle
\section{引言}
正文。
\end{document}
"""
_MULTI_OUT = SRC.extract_body_text(_MULTI)
check("⑤ 跨行花括号体整块切除（不留「概率统计教研室」半截）",
      "教研室" not in _MULTI_OUT and "\\affil" not in _MULTI_OUT, repr(_MULTI_OUT))
_ESC = r"""\begin{document}
\title{标题}
\affil[1]{某某大学 \{A\} 学院}
\maketitle
\section{引言}
正文。
\end{document}
"""
check("⑤ 转义花括号 \\{ \\} 不破坏深度计数（整块切除）",
      "\\affil" not in SRC.extract_body_text(_ESC), repr(SRC.extract_body_text(_ESC)))
_AFTER = r"""\begin{document}
\title{标题}
\author{张三}
\maketitle
\keywords{范畴论}
\pacs{11.10.-z}
\section{引言}
正文。
\end{document}
"""
_AFTER_OUT = SRC.extract_body_text(_AFTER)
check("⑤ \\maketitle 之后的前置信息被删（\\keywords / \\pacs）",
      "\\keywords" not in _AFTER_OUT and "\\pacs" not in _AFTER_OUT, repr(_AFTER_OUT))
check("⑤ 但 \\title/\\author/\\maketitle 保留（它们是通用命令，模板认识）",
      "\\title{标题}" in _AFTER_OUT and "\\author{张三}" in _AFTER_OUT
      and "\\maketitle" in _AFTER_OUT, repr(_AFTER_OUT))
_LOOSE = r"""\begin{document}
\keywords{范畴论}
\section{引言}
正文。
\end{document}
"""
check("⑤ 无 \\title 的散装前置命令走逐行删（退化路径仍有效）",
      "\\keywords" not in SRC.extract_body_text(_LOOSE),
      repr(SRC.extract_body_text(_LOOSE)))
_NOSEC = r"""\begin{document}
\title{短稿}
\author{张三}
\affil[1]{某某大学}
\maketitle
短稿正文，没有 \\section。
\end{document}
"""
_NOSEC_OUT = SRC.extract_body_text(_NOSEC)
check("⑤ 无 \\section 的短稿仍按守卫判（limit 退化为全文长度）",
      "\\affil" not in _NOSEC_OUT and "短稿正文" in _NOSEC_OUT, repr(_NOSEC_OUT))

# ══════════════════════════════════════════════════════════════════════════
# ⑥ 守卫反例：纯 article 稿整段保留（信息不能丢）
# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑥ 守卫反例：纯 article 稿的标题页整段保留 ──")
ART_DOC = r"""\begin{document}
\title{标题}
\author{张三\\ 某某大学 \texttt{zs@example.edu}}
\date{}
\maketitle
\section{引言}
正文。
\end{document}
"""
ART_OUT = SRC.extract_body_text(ART_DOC)
check("⑥ 纯 article 稿：\\title 保留（段内无别家专有命令，不整段删）",
      "\\title{标题}" in ART_OUT, repr(ART_OUT))
check("⑥ 纯 article 稿：\\author 里的单位/邮箱不丢（盲删会丢信息，语料 4 篇文章 / 5 个文件会中招——含同一篇的两份副本）",
      "某某大学" in ART_OUT and "zs@example.edu" in ART_OUT, repr(ART_OUT))
check("⑥ 纯 article 稿：\\date{} 与 \\maketitle 都保留",
      "\\date{}" in ART_OUT and "\\maketitle" in ART_OUT, repr(ART_OUT))
_NOFOREIGN = r"""\begin{document}
\affil[1]{只在前置区之外}
\title{标题}
\author{张三}
\maketitle
\section{引言}
正文。
\end{document}
"""
check("⑥ 反例：\\title 之前的前置命令不在 title→maketitle 段内 → 走逐行删（不整段删）",
      "\\affil" not in SRC.extract_body_text(_NOFOREIGN)
      and "\\title{标题}" in SRC.extract_body_text(_NOFOREIGN),
      repr(SRC.extract_body_text(_NOFOREIGN)))
_SEC_AFFIL = r"""\begin{document}
\section{引言}
正文里的 \affil[1]{不是前置区} 保留。
\end{document}
"""
check("⑥ 边界：第一个 \\section 之后的 \\affil 属正文 → 一个字都不动",
      "\\affil[1]{不是前置区}" in SRC.extract_body_text(_SEC_AFFIL),
      repr(SRC.extract_body_text(_SEC_AFFIL)))
_SAMELINE = r"""\begin{document}
\title{标题}
\author{张三}\affil[1]{某某大学}
\maketitle
\section{引言}
正文。
\end{document}
"""
check("⑥ 已知局限（锁现状）：\\affil 与 \\author 同一行时行首不匹配 → 前置区不删",
      "\\affil" in SRC.extract_body_text(_SAMELINE),
      "锁的是当前行为；要修得改成「段内子串扫描」，见回报「未覆盖场景」")

# ══════════════════════════════════════════════════════════════════════════
# ⑦ 辅助判据：花括号深度
# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑦ _brace_depth（跨行切除的唯一依据）──")
check("⑦ 平衡的花括号 → 0（不会误判为跨行续行）",
      SRC._brace_depth("\\affil[1]{某某大学}") == 0,
      str(SRC._brace_depth("\\affil[1]{某某大学}")))
check("⑦ 开而未闭 → 1（触发跨行续删）",
      SRC._brace_depth("\\affil[1]{某某大学,") == 1,
      str(SRC._brace_depth("\\affil[1]{某某大学,")))
check("⑦ 转义花括号 \\{ \\} 不计深度（按 TeX 语义跳过）",
      SRC._brace_depth("\\{a\\}") == 0, str(SRC._brace_depth("\\{a\\}")))

# ══════════════════════════════════════════════════════════════════════════
# ⑧ 端到端：build_latex() 产物里三类残渣都不出现（不跑 xelatex）
# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑧ 端到端 build_latex() 产物（只到 .tex，不跑 xelatex）──")
_E2E_SRC = sn_source()
_E2E_MAIN = write("main.tex", _E2E_SRC)
_res = build.build_latex(_E2E_MAIN, os.path.join(TMP, "out"), basename="main",
                         verbose=False)
_tex = open(_res.tex_path, encoding="utf-8").read()
for _n in KERNEL_CASE:
    check(f"⑧ 产物无 \\renewcommand{{\\{_n}}}（内核命令没搬进产物）",
          ("\\renewcommand{\\" + _n + "}") not in _tex)
check("⑧ 产物无 \\usepackage{natbib}（与 biblatex 互斥，包行不迁）",
      "\\usepackage{natbib}" not in _tex)
check("⑧ 产物正文是 \\parencite{k1}，\\citep 已改写",
      "\\parencite{key1}" in _tex and "\\citep{" not in _tex)
check("⑧ 产物正文是 \\textcite{k2}（\\citet → biblatex 叙述式）",
      "\\textcite{key2}" in _tex)
check("⑧ 产物无别家前置命令（\\affil / \\keywords）",
      "\\affil" not in _tex and "\\keywords" not in _tex)
check("⑧ 产物无作者的 \\maketitle（模板自己打标题页）", "\\maketitle" not in _tex)
check("⑧ 真·新名照旧进产物（\\newcommand{\\brandnewcmd}）",
      "\\newcommand{\\brandnewcmd}" in _tex)
check("⑧ warnings 说明 biblatex 已取代 natbib、命令已改写（不静默）",
      any("biblatex" in w and "natbib" in w and "改写" in w for w in _res.warnings),
      str([w for w in _res.warnings if "natbib" in w]))

# ── 清理 ──────────────────────────────────────────────────────────────────
import shutil  # noqa: E402  （放末尾：与既有套件同风格，失败时临时目录留着排查）

shutil.rmtree(TMP, ignore_errors=True)


print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
