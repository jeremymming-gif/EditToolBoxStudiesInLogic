#!/usr/bin/env python3.14
"""P2-2：正文「表N / 图N」→「表~\\ref{tab:N}」/「图~\\ref{fig:N}」（图表交叉引用接进初排）。

缺陷
-----
Word 通道只把表格/图片渲染成 `\\caption` + `\\label`（`docxsource.table_env` /
`figure_env`），**正文里的「表1/图1」仍是纯文本**。人工成品会改成 `表~\\ref{tb1}`
（期 87 子案例 2 人工 11 处、生成 0 处；一校 ZH-16 因此自然不触发，笔记仓期87 §P2-2）。
P2-0（图 label 全局唯一）已先修，否则图 `\\ref` 会指错图。

修法
----
`slcn/build.py::_add_xrefs(tex)`，在 Word 通道写文件前调用。**保守四原则**：
  1. 只在**存在对应 `\\label{tab:N}`/`\\label{fig:N}`** 时才替换 → 绝不产生 undefined reference；
  2. 屏蔽数学/注释/verbatim/命令名与键参数（复用 `proofcheck.visible_mask`，等长掩码）；
  3. 行首题注「表N：/图N：」（Word 题注段落残留，期 87 sub2 有 2 处）不碰；
  4. 已写成 `表~\\ref{…}` 的跳过（幂等）；「式(N)」不做（Word 通道无 equation 编号）。

跑法
----
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_xref_autoref.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.build import _add_xrefs  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ══════════════════════════════════════════════════════════════════════════
# ① 有 label 才替换：表1 → 表~\ref{tab:1}；计数 (1, 0)
# ══════════════════════════════════════════════════════════════════════════
doc = "\\begin{table}\n  \\caption{题}\n  \\label{tab:1}\n\\end{table}\n正文如表1所示，见表2。\n"
out, n_tab, n_fig = _add_xrefs(doc)
check("① 有 label 的「表1」→「表~\\ref{tab:1}」", "表~\\ref{tab:1}所示" in out, repr(out))
check("① 无 label 的「表2」保持纯文本（不产生 undefined reference）",
      "表2" in out and "\\ref{tab:2}" not in out, repr(out))
check("① 计数 = (表1, 图0)", (n_tab, n_fig) == (1, 0), f"{n_tab},{n_fig}")

# ══════════════════════════════════════════════════════════════════════════
# ② 无 label 的稿 → 逐字节不变（兼容：LaTeX 通道/无图表稿）
# ══════════════════════════════════════════════════════════════════════════
plain = "讨论见表1、图3、式(2)。\n"
out2, a, b2 = _add_xrefs(plain)
check("② 无 label 时逐字节不变 + 计数 (0,0)", out2 == plain and (a, b2) == (0, 0), repr(out2))

# ══════════════════════════════════════════════════════════════════════════
# ③ 行首题注「表N：」不误伤；正文里的「表N」照改（期 87 sub2 形态）
# ══════════════════════════════════════════════════════════════════════════
sub2 = (
    "\\begin{table}\n  \\caption{【需补充表题】}\n  \\label{tab:1}\n\\end{table}\n"
    "表1：根据数学直觉获得的12个概率确证测度\n"
    "基于此，表1所列的确证测度显得非常直观。\n"
    "\\begin{table}\n  \\caption{【需补充表题】}\n  \\label{tab:2}\n\\end{table}\n"
    "表2：9个常见的互不排序等价的确证测度\n"
    "表2汇总了这9个测度的计算公式。\n"
)
out3, n3, _ = _add_xrefs(sub2)
check("③ 行首题注「表1：/表2：」原样保留（题注两处不误伤）",
      "表1：根据数学直觉" in out3 and "表2：9个常见的" in out3, repr(out3[:400]))
_cnt3 = out3.count("\\ref{tab:")
check("③ 正文指代两处改成 \\ref", _cnt3 == 2 and n3 == 2, f"count={_cnt3} n={n3}")

# ══════════════════════════════════════════════════════════════════════════
# ④ 注释 / verbatim / 数学区 不碰
# ══════════════════════════════════════════════════════════════════════════
masked = (
    "\\label{tab:1}\n"
    "% 表1 是注释\n"
    "\\begin{verbatim}\n表1 verbatim\n\\end{verbatim}\n"
    "公式 $表1$ 不碰。\n"
    "正文见表1。\n"
)
out4, n4, _ = _add_xrefs(masked)
check("④ 注释里的「表1」不变", "% 表1 是注释" in out4, repr(out4))
check("④ verbatim 里的「表1」不变", "表1 verbatim" in out4)
check("④ 数学区 `$表1$` 不变", "$表1$" in out4)
check("④ 只有正文那处被替换（计数 1）", "正文见表~\\ref{tab:1}。" in out4 and n4 == 1, repr(out4))

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 幂等：跑两遍结果相同（已写成 表~\ref 的不重复替换）
# ══════════════════════════════════════════════════════════════════════════
once, _, _ = _add_xrefs("\\label{tab:1}\n表1 与表1。\n")
twice, n5, _ = _add_xrefs(once)
check("⑤ 幂等：第二遍不再改动、计数 0", twice == once and n5 == 0, repr(twice))
check("⑤ 已写成 表~\\ref 的形态不重复包", "表~\\ref{tab:1} 与表~\\ref{tab:1}。" in once, repr(once))

# ══════════════════════════════════════════════════════════════════════════
# ⑥ 图：图2 + label{fig:2} → 图~\ref{fig:2}（依赖 P2-0 唯一化）
# ══════════════════════════════════════════════════════════════════════════
fig = "\\begin{figure}\n\\caption{图题}\n\\label{fig:2}\n\\end{figure}\n如图2所示。\n"
out6, nt6, nf6 = _add_xrefs(fig)
check("⑥ 图2 → 图~\\ref{fig:2}，计数 (0,1)",
      "图~\\ref{fig:2}所示" in out6 and (nt6, nf6) == (0, 1), repr(out6))

# ══════════════════════════════════════════════════════════════════════════
# ⑦ 混合：表/图同时存在，各按自己的 label 集合替换（互不串号）
# ══════════════════════════════════════════════════════════════════════════
mix = ("\\label{tab:1}\\label{fig:1}\n"
       "见表1与图1：表2 无 label 保留，图2 无 label 保留。\n")
out7, nt7, nf7 = _add_xrefs(mix)
check("⑦ 表/图各自替换、无 label 的不动，计数 (1,1)",
      "表~\\ref{tab:1}" in out7 and "图~\\ref{fig:1}" in out7
      and "表2" in out7 and "图2" in out7 and (nt7, nf7) == (1, 1), repr(out7))

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
