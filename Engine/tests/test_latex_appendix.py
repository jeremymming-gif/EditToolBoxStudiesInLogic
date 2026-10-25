#!/usr/bin/env python3.14
r"""第四十六批②：LaTeX 稿「参考文献之后的附录」迁移（86-05 PAGE_DIFF 实测）。

缺陷：`sources.extract_body_text()` 截到**参考文献之前**（第 121 条），作者把附录写在
`\printbibliography` 之后时整段附录被静默丢掉——86-05 WeiNong_Li 丢 3 个附录小节，
生成 20 页 vs 人工 26 页（文本仅 66.7%）。
修法：`build._latex_post_bibliography_tail()` 取出该段，`_insert_post_bibliography()`
插到模板 `\printbibliography` 之后；只在确有非注释内容时生效（Word 通道与非附录稿不变）。
"""
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibitem_convert, build, convert_bib  # noqa: E402

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

# ── B-233：无可用 .bib 的手写 thebibliography → 自动生成 reference.bib ──────
# 夹具全合成（零真实作者/内容）：正文引用 + 4 条 \bibitem（期刊/学位论文/章节/
# 专著）+ 注释里的幽灵条目。验收：条数=源、key 原样、author/title/year 有值、类型
# 按显式标记正确、产物无 \bibitem 残留、注释条目不误吃；有可用 .bib 时旧路径优先。
_SRC_BIBITEM = (
    "\\documentclass{article}\n\\begin{document}\n"
    "\\section{正文}\n合成正文引用 \\cite{art2020} 与 \\cite{thesis2021}。\n"
    "\\begin{thebibliography}{99}\n"
    "% \\bibitem{ghostkey} Ghost, A.: Ghost title. \\textit{Ghost Journal} (1999)\n"
    "\\bibitem{art2020} Smith, J.: A synthetic journal article. "
    "\\textit{Journal of Synthetic Tests} \\textbf{12}(3), 45--67 (2020)\n"
    "\\bibitem{thesis2021} Doe, A.: \\textit{A Synthetic Dissertation}. "
    "PhD thesis, Synthetic University (2021)\n"
    "\\bibitem{inc2022} Roe, B.: A synthetic chapter. In: "
    "\\textit{Collected Synthetic Papers}, pp.~10--20. Sample Press, Sample City (2022)\n"
    "\\bibitem{book2023} Poe, C.: \\textit{A Synthetic Book}. "
    "Sample Press, Sample City (2023)\n"
    "\\end{thebibliography}\n\\end{document}\n"
)
_ENTRY_RE = re.compile(r"@(\w+)\{([^,]+),")
_FIELD_RE = re.compile(r"\b(author|title|year)\s*=\s*\{(.+?)\}")


def _bib_entries(path: str) -> tuple[list[tuple[str, str]], str]:
    text = open(path, encoding="utf-8").read()
    return _ENTRY_RE.findall(text), text


_tmp_b = tempfile.mkdtemp()
_body_b = os.path.join(_tmp_b, "body.tex")
open(_body_b, "w", encoding="utf-8").write(_SRC_BIBITEM)
try:
    with tempfile.TemporaryDirectory() as out:
        _res_b = build.build_latex(_body_b, out, basename="B", lang="zh", verbose=False)
        _tex_b = open(_res_b.tex_path, encoding="utf-8").read()
        _bib_ok = bool(_res_b.bib_path) and os.path.isfile(_res_b.bib_path)
        _types_b, _bib_b = _bib_entries(_res_b.bib_path) if _bib_ok else ([], "")
    _warn_b = " ".join(_res_b.warnings)
    check("B-233① 无 .bib 手写 \\bibitem → 生成 reference.bib：条数=源 4、key 原样、顺序一致",
          _bib_ok and [k for _t, k in _types_b] == [
              "art2020", "thesis2021", "inc2022", "book2023"], repr(_types_b))
    check("B-233② 类型按显式标记：期刊→@article、PhD thesis→@phdthesis、"
          "In:→@incollection、出版社+城市→@book",
          [t for t, _k in _types_b] == ["article", "phdthesis", "incollection", "book"],
          repr([t for t, _k in _types_b]))
    _fields_b = _FIELD_RE.findall(_bib_b)
    _n_by = {f: len([v for ff, v in _fields_b if ff == f]) for f in ("author", "title", "year")}
    check("B-233③ 4/4 条 author/title/year 有值", _n_by == {"author": 4, "title": 4, "year": 4},
          repr(_n_by))
    check("B-233④ 产物无 \\bibitem 残留；注释里的 \\bibitem 未被吃成条目；提示进 warnings",
          "\\bibitem" not in _tex_b and "ghostkey" not in _bib_b
          and "由 \\bibitem 自动转换" in _warn_b, repr(_bib_b[:200]))
finally:
    try:
        os.unlink(_body_b); os.rmdir(_tmp_b)
    except OSError:
        pass

# B-233⑤ 有可用 .bib 时旧路径优先（find_bib 命中）：完全不转换、无自动转换 warning
_tmp_c = tempfile.mkdtemp()
_body_c = os.path.join(_tmp_c, "body.tex")
_bib_c = os.path.join(_tmp_c, "reference.bib")
open(_body_c, "w", encoding="utf-8").write(_SRC_BIBITEM)
open(_bib_c, "w", encoding="utf-8").write(
    "@article{frombib,\n  author = {Doe, J.},\n  title = {Synthetic entry from bib},\n"
    "  journal = {J},\n  year = {2019}\n}\n")
try:
    _found = convert_bib.find_bib(_body_c, None)
    with tempfile.TemporaryDirectory() as out:
        _res_c = build.build_latex(_body_c, out, basename="C", lang="zh",
                                   verbose=False, bib_path=_found)
        _types_c, _bib_c_txt = _bib_entries(_res_c.bib_path)
    check("B-233⑤ 有可用 .bib 时旧路径优先：find_bib 命中 → 不转换、不产生自动转换 warning",
          _found == _bib_c and [k for _t, k in _types_c] == ["frombib"]
          and "art2020" not in _bib_c_txt
          and "由 \\bibitem 自动转换" not in " ".join(_res_c.warnings), repr(_types_c))
finally:
    try:
        os.unlink(_body_c); os.unlink(_bib_c); os.rmdir(_tmp_c)
    except OSError:
        pass

# B-233⑥ 开关关闭（CLI --no-bibitem-convert 的底层语义）→ 回旧行为、不写 reference.bib
_tmp_d = tempfile.mkdtemp()
_body_d = os.path.join(_tmp_d, "body.tex")
open(_body_d, "w", encoding="utf-8").write(_SRC_BIBITEM)
try:
    bibitem_convert.set_enabled(False)
    try:
        with tempfile.TemporaryDirectory() as out:
            _res_d = build.build_latex(_body_d, out, basename="D", lang="zh", verbose=False)
    finally:
        bibitem_convert.set_enabled(True)
    check("B-233⑥ 转换开关关闭 → 不生成 reference.bib，保留旧告警（可回退）",
          _res_d.bib_path is None
          and any("未生成 reference.bib" in w for w in _res_d.warnings), repr(_res_d.warnings))
finally:
    try:
        os.unlink(_body_d); os.rmdir(_tmp_d)
    except OSError:
        pass

# B-233⑦ 反例：只有注释里的 \bibitem → 不触发转换（不误吃）
_tmp_e = tempfile.mkdtemp()
_body_e = os.path.join(_tmp_e, "body.tex")
open(_body_e, "w", encoding="utf-8").write(
    "\\documentclass{article}\n\\begin{document}\n正文。\n"
    "\\begin{thebibliography}{9}\n% \\bibitem{ghost} Ghost, A.: T (1999)\n"
    "\\end{thebibliography}\n\\end{document}\n")
try:
    with tempfile.TemporaryDirectory() as out:
        _res_e = build.build_latex(_body_e, out, basename="E", lang="zh", verbose=False)
    check("B-233⑦ 反例：注释里的 \\bibitem 不触发转换（不生成 reference.bib）",
          _res_e.bib_path is None)
finally:
    try:
        os.unlink(_body_e); os.rmdir(_tmp_e)
    except OSError:
        pass

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
