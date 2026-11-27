#!/usr/bin/env python3.14
r"""第 145 条：初排导言区迁移的两类缺陷（2026-11-27）。

① `\usepackage{cleveref}` 被插到 hyperref 之前 → `Package cleveref Error:
   cleveref must be loaded after hyperref!`。修复：`insert_lines()` 把块里的
   cleveref 行拆出来，插到 hyperref 行**之后**（仍在 `\begin{document}` 前）。

② 作者前言里的 `\tikzset{...}` / `\tikzstyle{...}` 自定义样式键没迁 →
   正文 `[modal]` / `[world]` / `edge[reflexive left]` 编译报
   `I do not know the key '/tikz/...'`。修复：`plan_migration()` 扫描并迁移
   这些多行花括号定义，位置在依赖的 tikz 库之后、`\begin{document}` 之前。

只用合成夹具 + `/tmp`；不读稿件库、不联网、不写产物仓。
用法：
    cd Engine && PYTHONDONTWRITEBYTECODE=1 SLCN_MANUSCRIPTS=/dev/null \
      PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 -B \
      tests/test_cleveref_tikz_migration_20261127.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import preamble as PRE  # noqa: E402

HYPERREF = r"\usepackage[pdfborder=0,CJKbookmarks=true]{hyperref}"


def _tpl(*, hyperref=True, document=True):
    rows = [
        r"\documentclass{article}",
        r"\usepackage{tikz}",
        r"\usepackage{amsmath}",
    ]
    if hyperref:
        rows.append(HYPERREF)
    if document:
        rows += [r"\begin{document}", "正文。", r"\end{document}"]
    return "\n".join(rows) + "\n"


class CleverefInsertionTests(unittest.TestCase):
    def test_cleveref_moves_after_hyperref(self):
        tpl = _tpl()
        lines = [
            r"\usepackage{amsmath}",
            r"\usepackage{cleveref}",
            r"\newtheorem{conjecture}{猜想}[section]",
        ]
        got = PRE.insert_lines(tpl, lines, HYPERREF)
        rows = got.split("\n")
        self.assertLess(rows.index(r"\usepackage{amsmath}"), rows.index(HYPERREF))
        self.assertLess(rows.index(r"\newtheorem{conjecture}{猜想}[section]"),
                        rows.index(HYPERREF))
        self.assertGreater(rows.index(r"\usepackage{cleveref}"), rows.index(HYPERREF))
        self.assertLess(rows.index(r"\usepackage{cleveref}"),
                        rows.index(r"\begin{document}"))

    def test_cleveref_with_options_and_multiple_lines(self):
        tpl = _tpl()
        lines = [r"\usepackage[capitalize]{cleveref}", r"\usepackage{amsmath}",
                 r"\usepackage{cleveref}"]
        got = PRE.insert_lines(tpl, lines, HYPERREF)
        rows = got.split("\n")
        self.assertLess(rows.index(r"\usepackage{amsmath}"), rows.index(HYPERREF))
        for ln in (r"\usepackage[capitalize]{cleveref}", r"\usepackage{cleveref}"):
            self.assertGreater(rows.index(ln), rows.index(HYPERREF))
            self.assertLess(rows.index(ln), rows.index(r"\begin{document}"))

    def test_no_cleveref_bytes_unchanged(self):
        lines = [r"\usepackage{amsmath}", r"\newtheorem{conjecture}{猜想}[section]"]
        for tpl in (_tpl(), _tpl(hyperref=False), _tpl(document=False)):
            with self.subTest(tpl=tpl):
                expected = tpl.replace(HYPERREF + "\n", "\n".join(lines) + "\n" + HYPERREF + "\n") \
                    if HYPERREF in tpl else tpl
                if expected == tpl and r"\begin{document}" in tpl:
                    expected = tpl.replace(r"\begin{document}",
                                           "\n".join(lines) + "\n" + r"\begin{document}")
                self.assertEqual(PRE.insert_lines(tpl, lines, HYPERREF), expected)

    def test_empty_block_unchanged(self):
        tpl = _tpl()
        self.assertEqual(PRE.insert_lines(tpl, [], HYPERREF), tpl)

    def test_cleveref_no_anchor_falls_back_before_document(self):
        tpl = _tpl(hyperref=False)
        lines = [r"\usepackage{cleveref}", r"\usepackage{amsmath}"]
        got = PRE.insert_lines(tpl, lines, HYPERREF)
        rows = got.split("\n")
        self.assertLess(rows.index(r"\usepackage{cleveref}"),
                        rows.index(r"\begin{document}"))
        self.assertLess(rows.index(r"\usepackage{amsmath}"),
                        rows.index(r"\begin{document}"))

    def test_cleveref_no_anchor_no_document_appends(self):
        tpl = _tpl(hyperref=False, document=False)
        lines = [r"\usepackage{cleveref}", r"\usepackage{amsmath}"]
        got = PRE.insert_lines(tpl, lines, HYPERREF)
        self.assertEqual(got, tpl.rstrip("\n") + "\n" + "\n".join(lines) + "\n")

    def test_cleveref_restores_source_relative_order_around_hyperref(self):
        """源里 cleveref 在 hyperref 之后；块整体插到锚点前不得破坏该相对顺序。"""
        tpl = _tpl()
        lines = [r"\usepackage{cleveref}", r"\usepackage{amsmath}",
                 r"\usepackage{xcolor}"]
        got = PRE.insert_lines(tpl, lines, HYPERREF)
        rows = got.split("\n")
        self.assertLess(rows.index(HYPERREF), rows.index(r"\usepackage{cleveref}"))
        self.assertLess(rows.index(r"\usepackage{cleveref}"),
                        rows.index(r"\begin{document}"))

    def test_two_anchor_like_lines_only_split_on_first_hyperref(self):
        tpl = "\n".join([
            r"\usepackage[pdfborder=0, CJKbookmarks=true]{hyperref}",
            r"\usepackage[pdfborder=0]{hyperref}",
            r"\begin{document}", r"正文。", r"\end{document}", "",
        ])
        got = PRE.insert_lines(tpl, [r"\usepackage{cleveref}"], HYPERREF)
        rows = got.split("\n")
        self.assertEqual(rows.index(r"\usepackage{cleveref}"), 2)
        self.assertGreater(rows.index(r"\usepackage{cleveref}"),
                           rows.index(r"\usepackage[pdfborder=0]{hyperref}"))
        self.assertEqual(got.count(r"\usepackage[pdfborder=0, CJKbookmarks=true]{hyperref}"), 1)
        self.assertEqual(got.count(r"\usepackage[pdfborder=0]{hyperref}"), 1)


AUTHOR_STYLES = r"""\documentclass{article}
\usepackage{tikz}
\usepackage[pdfborder=0, CJKbookmarks=true]{hyperref}
\usepackage[pdfborder=0]{hyperref}
\usepackage{cleveref}
\usepackage{tikz}
\usetikzlibrary{arrows.meta}
\usetikzlibrary{positioning}
\tikzset{
modal/.style={draw,rectangle,minimum width=2em,semithick},
world/.style={circle,draw,minimum size=.5cm,fill=gray!15},
point/.style={circle,draw,fill=black,inner sep=0.5mm},
reflexive/.style={-,in=120,out=60,loop,looseness=#1},
reflexive/.default={5},
reflexive point/.style={-,in=135,out=45,loop,looseness=#1},
reflexive point/.default={25},
reflexive above/.style={-,loop,in=120,out=60,looseness=#1},
reflexive above/.default={7},
reflexive below/.style={-,loop,in=240,out=300,looseness=#1},
reflexive below/.default={7},
reflexive left/.style={-,loop,in=150,out=210,looseness=#1},
reflexive left/.default={7},
reflexive right/.style={-,loop,in=30,out=330,looseness=#1},
reflexive right/.default={7}
}
% \tikzset{commented/.style={draw}}
\tikzstyle{edge}=[-{Stealth}]
\begin{document}
\begin{tikzpicture}[modal]
\node[world] (w) {w};
\draw (w) edge[reflexive left] (w);
\end{tikzpicture}
\end{document}
"""
BODY = r"""\begin{tikzpicture}[modal]
\node[world] (w) {w};
\draw (w) edge[reflexive left] (w);
\end{tikzpicture}"""
SKELETON = "\n".join([
    r"\documentclass{article}",
    r"\usepackage{tikz}",
    HYPERREF,
    r"\begin{document}",
    BODY,
    r"\end{document}",
    "",
])


class TikzStyleMigrationTests(unittest.TestCase):
    def test_plan_migrates_multiline_tikzset_and_tikzstyle(self):
        plan = PRE.plan_migration(AUTHOR_STYLES, SKELETON, "", "", body_text=BODY)
        joined = "\n".join(plan.lines)
        self.assertIn("modal/.style={draw,rectangle,minimum width=2em,semithick}", joined)
        self.assertIn("world/.style={circle,draw,minimum size=.5cm,fill=gray!15}", joined)
        self.assertIn("reflexive left/.style={-,loop,in=150,out=210,looseness=#1}", joined)
        self.assertIn("reflexive left/.default={7}", joined)
        self.assertIn(r"\tikzstyle{edge}=[-{Stealth}]", joined)
        self.assertNotIn("commented/.style", joined)
        # #1 必须原样保留，不得被 Python 格式化吞掉
        self.assertEqual(joined.count("looseness=#1"), 6)
        lib_idx = max(i for i, ln in enumerate(plan.lines)
                      if ln.startswith(r"\usetikzlibrary{"))
        set_idx = next(i for i, ln in enumerate(plan.lines) if ln.startswith(r"\tikzset{"))
        self.assertLess(lib_idx, set_idx, "样式定义必须在依赖的 tikz 库之后")
        self.assertIn(r"\usepackage{cleveref}", plan.lines)

    def test_plan_migrates_multiline_tikzstyle(self):
        """\tikzstyle 的值是跨行花括号时也要按配对整块迁移。"""
        src = r"""\documentclass{article}
\usepackage{tikz}
\tikzstyle{boxed}={
  draw,
  rectangle,
  minimum size=1em
}
\tikzstyle{plain}=[circle]
\begin{document}
x
\end{document}
"""
        plan = PRE.plan_migration(src, r"\documentclass{article}", "", "")
        joined = "\n".join(plan.lines)
        self.assertIn(r"\tikzstyle{boxed}={", joined)
        self.assertIn("minimum size=1em", joined)
        self.assertIn(r"\tikzstyle{plain}=[circle]", joined)
        # 跨行块内的三行都必须被收走，且顺序与源码一致
        self.assertLess(joined.index(r"\tikzstyle{boxed}={"),
                        joined.index(r"\tikzstyle{plain}=[circle]"))

    def test_candidate_places_styles_in_preamble_and_compiles(self):
        plan = PRE.plan_migration(AUTHOR_STYLES, SKELETON, "", "", body_text=BODY)
        candidate = PRE.insert_lines(SKELETON, plan.lines, HYPERREF)
        rows = candidate.split("\n")
        self.assertLess(
            next(i for i, ln in enumerate(rows) if ln.startswith(r"\tikzset{")),
            rows.index(r"\begin{document}"),
        )
        self.assertGreater(rows.index(r"\usepackage{cleveref}"), rows.index(HYPERREF))
        xelatex = shutil.which("xelatex", path="/Library/TeX/texbin:/usr/local/bin:/usr/bin")
        if xelatex is None:
            self.skipTest("xelatex 不可用")
        tmp = tempfile.mkdtemp(prefix="slcn-cleveref-tikz-")
        try:
            tex = os.path.join(tmp, "main.tex")
            with open(tex, "w", encoding="utf-8") as f:
                f.write(candidate)
            proc = subprocess.run(
                [xelatex, "-interaction=nonstopmode", "-halt-on-error",
                 "-output-directory", tmp, tex],
                capture_output=True, timeout=180, check=False,
            )
            log = ""
            log_path = os.path.join(tmp, "main.log")
            if os.path.isfile(log_path):
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    log = f.read()
            self.assertEqual(proc.returncode, 0, log[-3000:] + proc.stderr.decode(errors="replace"))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "main.pdf")), log[-1500:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_tikzset_preserves_plan_bytes_and_observation(self):
        src = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{enumitem}
\newtheorem{conjecture}{猜想}[section]
\newcommand{\used}{U}
\begin{document}
$\used$ \begin{conjecture} X \end{conjecture}
\end{document}
"""
        body = r"$\used$ \begin{conjecture} X \end{conjecture}"
        obs = PRE.PlanningObservation()
        plan = PRE.plan_migration(src, r"\documentclass{article}", "", "SLCN-MIX",
                                  body_text=body, observation=obs)
        self.assertEqual(
            plan.lines,
            [r"\usepackage{amsmath}", r"\usepackage{enumitem}",
             r"\newtheorem{conjecture}{猜想}[section]"],
        )
        self.assertEqual(obs.to_display()["counts"]["declarations_seen"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
