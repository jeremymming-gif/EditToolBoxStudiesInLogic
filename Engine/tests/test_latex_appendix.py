#!/usr/bin/env python3.14
r"""第四十六批②：LaTeX 稿「参考文献之后的附录」迁移（86-05 PAGE_DIFF 实测）。

缺陷：`sources.extract_body_text()` 截到**参考文献之前**（第 121 条），作者把附录写在
`\printbibliography` 之后时整段附录被静默丢掉——86-05 WeiNong_Li 丢 3 个附录小节，
生成 20 页 vs 人工 26 页（文本仅 66.7%）。
修法：`build._latex_post_bibliography_tail()` 取出该段，`_insert_post_bibliography()`
插到模板 `\printbibliography` 之后；只在确有非注释内容时生效（Word 通道与非附录稿不变）。
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

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


_SRC = (
    "\\documentclass{article}\n\\begin{document}\n"
    "\\printtitlepage\n\\section{正文}\n正文内容。\n"
    "\\vspace{2ex} \n\\printbibliography\n"
    "% 注释不算\n\\appendix\n\\section*{附录}\n附录正文与公式 $x^2$。\n"
    "\\end{document}\n"
)
_tail = build._latex_post_bibliography_tail(_SRC)
check("① 取到 \\printbibliography 之后的附录内容（含 \\appendix 与小节）",
      "\\appendix" in _tail and "附录正文" in _tail and "\\printbibliography" not in _tail,
      repr(_tail[:80]))
check("② 反例：没有参考文献标记 / 标记后为空 → 不迁移",
      build._latex_post_bibliography_tail("\\begin{document}x\\end{document}") == ""
      and build._latex_post_bibliography_tail(
          "\\begin{document}\n\\printbibliography\n% 只有注释\n\\end{document}") == "")
# ④c 第四十六批回归（88-04/87-1/89-05 实测）：作者「完整模板稿」的**模板尾部块**
#     （`\ifthenelse{\equal{\myarticletype}…}` / 作者信息 / 英文题名块）不是附录：
#     迁进产物会与模板尾部重复 → `\mytitleCN`/`\printtitlepageCN` undefined、rc 2。
#     判据：只有含 `\appendix` 的 tail 才是真附录。
_SRC_TEMPLATE_TAIL = (
    "\\documentclass{article}\n\\begin{document}\n\\section{正文}\n正文。\n"
    "\\printbibliography\n"
    "\\ifthenelse{\\equal{\\myarticletype}{original}}{}{%\n"
    "\\vspace*{4ex}\\noindent\\myfirstauthor\\\\\n}\n"
    "\\end{document}\n"
)
check("④ 反例：模板尾部块（无 \\appendix）不迁移（否则与模板重复、rc 2）",
      build._latex_post_bibliography_tail(_SRC_TEMPLATE_TAIL) == "")
check("④ 反例：89-05 型英文题名/摘要块（无 \\appendix）不迁移",
      build._latex_post_bibliography_tail(
          "\\begin{document}\n正文。\n\\begin{thebibliography}{9}\n\\bibitem{a} X.\n"
          "\\end{thebibliography}\n\\pagebreak\\begin{center}{\\large English Title}\\end{center}\n"
          "\\end{document}\n") == "")

# ④a 第四十六批回归（86-05 实测）：**注释里的 biblatex 迁移示例**不能当参考文献标记。
#     源码注释里贴了 `%\bibliographystyle` / `%\begin{thebibliography}` / `%\bibitem`；
#     旧实现用 body.find() 命中注释 → 起点错位 → 防御性 \bibitem 检查把附录整段判空
#     （页数从 30 回退到 20）。修法：只认**非注释行**的标记（_active_find）。
_SRC_COMMENTED = (
    "\\documentclass{article}\n\\begin{document}\n\\section{正文}\n正文内容。\n"
    "% 迁移说明（注释，不是真标记）：\n"
    "%\\bibliographystyle{plain}\n"
    "%\\begin{thebibliography}{9}\n"
    "%\\bibitem{x} X. Author, 2020.\n"
    "%\\end{thebibliography}\n"
    "\\printbibliography\n"
    "\\appendix\n\\section*{附录}\n注释示例之后的附录正文内容。\n"
    "\\end{document}\n"
)
_tail_c = build._latex_post_bibliography_tail(_SRC_COMMENTED)
check("④ 注释里的 bib 示例不干扰：tail 含附录、不含 \\bibitem",
      "附录正文内容" in _tail_c and "\\bibitem" not in _tail_c,
      repr(_tail_c[:120]))

# ④ 第四十六批回归（89-05 实测）：**手写 thebibliography 整环境跳过**。
#    旧实现把 \begin{thebibliography} 当命令 → 只丢 begin 行、\bibitem 体被整体迁移 →
#    产物有 \bibitem 无环境 → `! Lonely \item`（47 错、rc 2）。修法：tail 只从
#    \end{thebibliography} 之后取；找不到 \end 或 tail 里仍有 \bibitem → 不迁移。
_SRC_MANUAL = (
    "\\documentclass{article}\n\\begin{document}\n\\section{正文}\n正文内容。\n"
    "\\begin{thebibliography}{99}\n"
    "\\bibitem{a} A. Author, 2020. Title.\n"
    "\\bibitem{b} B. Author, 2021. Title.\n"
    "\\end{thebibliography}\n"
    "\\appendix\n\\section*{附录}\n手写 bib 之后的附录内容 $y$。\n"
    "\\end{document}\n"
)
_tail_manual = build._latex_post_bibliography_tail(_SRC_MANUAL)
check("④ 手写 thebibliography：tail 只含 \\end{thebibliography} 之后内容，不含任何 \\bibitem",
      "\\bibitem" not in _tail_manual and "\\begin{thebibliography}" not in _tail_manual
      and "附录内容" in _tail_manual, repr(_tail_manual[:120]))
check("④ 反例：thebibliography 不完整（无 \\end）→ 不迁移（不搬 bibitem 体）",
      build._latex_post_bibliography_tail(
          "\\begin{document}\n\\begin{thebibliography}{9}\n\\bibitem{a} X.\n"
          "\\appendix\n\\section*{附}\n附录。\n\\end{document}\n") == "")

_tpl = "A\n\\printbibliography\nB\n\\end{document}\n"
_out = build._insert_post_bibliography(_tpl, "\\appendix\n附录正文")
check("③ 插到模板 \\printbibliography **之前**（与人工成品顺序一致；不重复）",
      _out.count("\\printbibliography") == 1
      and _out.index("附录正文") < _out.index("\\printbibliography"), repr(_out))

# ④b 端到端（手写 bib + 附录混排）：bib 环境不迁移、附录迁移、无坏 \bibitem
_tmp_manual = tempfile.mkdtemp()
_body_manual = os.path.join(_tmp_manual, "body.tex")
open(_body_manual, "w", encoding="utf-8").write(_SRC_MANUAL)
try:
    with tempfile.TemporaryDirectory() as out:
        _res_m = build.build_latex(_body_manual, out, basename="M", lang="zh", verbose=False)
        _tex_m = open(_res_m.tex_path, encoding="utf-8").read()
    check("④ 端到端：手写 bib 稿 → 附录迁移、产物无 \\bibitem、只有一处 \\printbibliography",
          "附录内容" in _tex_m and "\\bibitem" not in _tex_m
          and _tex_m.count("\\printbibliography") == 1, repr(_tex_m[-300:]))
finally:
    try:
        os.unlink(_body_manual); os.rmdir(_tmp_manual)
    except OSError:
        pass

# ④ 端到端：合成源稿 → 产物含附录；无附录稿不含多余内容
_tmp = tempfile.mkdtemp()
_body = os.path.join(_tmp, "body.tex")
open(_body, "w", encoding="utf-8").write(_SRC)
try:
    with tempfile.TemporaryDirectory() as out:
        res = build.build_latex(_body, out, basename="L", lang="zh", verbose=False)
        tex = open(res.tex_path, encoding="utf-8").read()
    check("④ 端到端：产物含附录内容且只有一处 \\printbibliography",
          "附录正文" in tex and tex.count("\\printbibliography") == 1)
finally:
    try:
        os.unlink(_body); os.rmdir(_tmp)
    except OSError:
        pass

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
