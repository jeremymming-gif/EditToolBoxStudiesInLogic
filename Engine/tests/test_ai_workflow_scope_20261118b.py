#!/usr/bin/env python3
"""R3 第一段「范围裁剪」独立回归（2026-11-18b，B-222 A 包）。

钉死的红线（逐条对应验收项 a–f）：
  a) 作者额外 Bib 条目（存在于 .bib、正文从未引用）不得出现在任务书任何节；
  b) `\\nocite{*}` 不得扩大暴露面（它可以只是「不必逐条核实」的信号）；
  c) LaTeX 注释里被注释掉的 `\\cite` 不计入引用范围；
  d) DOCX 未被正文引用的尾部参考文献不得整段倾倒；
  e) 第一段完全不读共享引用库（库路径不可用也照样出任务书）；
  f) LaTeX 与 DOCX 两条入口都成立；
  g) 正文编号引用的各种写法（区间/分号/全角/跨方括号/混写）都要判得出范围，
     既不许「判不出→保守全量」泄漏未引用条目，也不许把已引用条目漏出委派集合；
  h) 正文有编号标记但编号→条目映射判不出时，显式标记 scope_unresolved，
     不得退回保守全量。

全部夹具自造且匿名（K0x / Alpha / Beta / Gamma），不读真实稿件、不联网、不调模型。

用法：
  cd Engine && PYTHONPATH=. /Users/zhangwenming/miniconda3/bin/python3 \
      tests/test_ai_workflow_scope_20261118b.py
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.ai_workflow import read_taskbook  # noqa: E402
from slcn.ai_workflow_taskbook import build_context  # noqa: E402


# ── 匿名 Bib 夹具 ─────────────────────────────────────────────────────────
# K01 被正文引用；K02 只存在于 .bib（作者额外条目），正文从未引用。
BIB_SCOPE = """@string{acm = {ACM Press}}

@article{K01,
  author  = {A. Alpha},
  title   = {A cited study},
  journal = {Journal of Examples},
  year    = {2020},
  pages   = {1--10},
}

@article{K02,
  author  = {B. Beta},
  title   = {AN-UNCITED-EXTRA-ENTRY},
  journal = {Journal of Examples},
  year    = {2021},
  pages   = {11--20},
}
"""


def _state(*, exec_cats=("bib_verify", "typeset", "text"), cite_keys=(), nocite=False,
           allowed=None):
    return {
        "task_id": "AIW-R3SCOPE00001",
        "entry": "convert",
        "scope": {"exec": list(exec_cats), "adopt": list(exec_cats)},
        "safe_hit_keys": [],
        "allowed_keys": list(allowed or []),
        "cite_keys": list(cite_keys),
        "nocite_all": bool(nocite),
    }


def _build(bib=BIB_SCOPE, tex="", state=None, evidence=None):
    return build_context(
        state=state if state is not None else _state(),
        bib_text=bib,
        tex_text=tex,
        source_evidence=evidence or {},
        rule_sections=[],
    )


def _keys(seq):
    if isinstance(seq, dict):
        return {str(k) for k in seq.keys()}
    return {str(k) for k in (seq or [])}


# ── 最小 docx（只含 word/document.xml；与 tests/test_cn_journal_numbered.py 同法）──
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# 正文只引用 [1]；[2]/[3] 是作者尾部多挂的、正文从未引用的参考文献。
DOCX_PARAS = [
    "A Fictional Example",
    "Alice Example",
    "摘要：本文是一个匿名合成样例，用于验证范围裁剪。",
    "关键词：范围裁剪；合成样例",
    "Body paragraph one with a numbered citation [1].",
    "Body paragraph two, no citation here.",
    "参考文献",
    "[1] Alpha, A. 2020. A cited work. Journal of Examples.",
    "[2] Beta, B. 2021. DOCX-UNCITED-TAIL-BETA.",
    "[3] Gamma, C. 2022. DOCX-UNCITED-TAIL-GAMMA.",
]


def _make_docx(path: Path, paras):
    ps = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>' for t in paras
    )
    xml = f'<w:document xmlns:w="{_W_NS}"><w:body>{ps}</w:body></w:document>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return path


def _convert_argv(source: Path, root: Path, *, work: str = "task"):
    return [
        "--source", str(source), "--out", str(root / "out"), "--no-original",
        "--ai-taskbook-version", "3", "--ai-execute", "typeset,text,bib_verify",
        "--ai-work-dir", str(root / work),
    ]


def _run_convert(source: Path, root: Path, *, work: str = "task"):
    env = dict(os.environ, SLCN_MANUSCRIPTS=str(root / "absent-manuscripts"),
               SLCN_NOTES=str(root / "absent-notes"))
    proc = subprocess.run(
        [sys.executable, "-B", str(Path(_ENGINE) / "convert.py"),
         *_convert_argv(source, root, work=work)],
        capture_output=True, text=True, env=env,
    )
    return proc, read_taskbook(work_dir=str(root / work))


# ══ (a) 作者额外 Bib 条目不得进入任务书任何节 ═══════════════════════════════
class UncitedBibEntryTest(unittest.TestCase):
    """存在于 .bib、正文从未引用的条目：集合与渲染两面都不得出现。"""

    def test_context_sets_exclude_uncited_bib_entry(self):
        ctx = _build(tex=r"\cite{K01}", state=_state(cite_keys=("K01",)))
        self.assertEqual(_keys(ctx["sets"]["C"]), {"K01"})
        self.assertEqual(_keys(ctx["sets"]["R"]), {"K01"})
        self.assertEqual(_keys(ctx["sets"]["T"]), {"K01"})
        self.assertNotIn("K02", _keys(ctx["sets"]["R"]))
        self.assertNotIn("K02", _keys(ctx["sets"]["T"]))
        self.assertIn("K02", _keys(ctx["sets"]["O"]))   # 仍在原 Bib 里（保全语义不变）
        self.assertIn("K02", _keys(ctx["sets"]["U"]))   # 只是「其他未委派」

    def test_uncited_bib_entry_raw_never_rendered(self):
        from slcn.ai_workflow_taskbook import render_taskbook
        ctx = _build(tex=r"\cite{K01}", state=_state(cite_keys=("K01",)))
        text = render_taskbook(context=ctx)
        self.assertIn("A cited study", text)
        self.assertNotIn("AN-UNCITED-EXTRA-ENTRY", text)

    def test_latex_entry_point_hides_uncited_bib_entry(self):
        """端到端：真实 convert 入口（LaTeX），.bib 里的额外条目不出现在任务书。"""
        with tempfile.TemporaryDirectory(prefix="slcn-scope-latex-") as raw:
            root = Path(raw)
            source = root / "Anonymous.tex"
            source.write_text(r"""\documentclass{article}
\title{A Fictional Example}
\author{Alice Example}
\begin{document}
\maketitle
\section{A sample heading}
A concrete sample paragraph with one citation \cite{K01}.
\bibliographystyle{plain}
\bibliography{Anonymous}
\end{document}
""", encoding="utf-8")
            (root / "Anonymous.bib").write_text(BIB_SCOPE, encoding="utf-8")
            proc, taskbook = _run_convert(source, root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(taskbook["ok"], proc.stdout + proc.stderr)
            text = taskbook["text"]
        self.assertIn("REF-SOURCE", text)
        self.assertIn("K01", text)
        self.assertNotIn("AN-UNCITED-EXTRA-ENTRY", text)
        self.assertNotIn("@article{K02", text)


# ══ (b) \nocite{*} 不得扩大暴露面 ══════════════════════════════════════════
class NociteStarScopeTest(unittest.TestCase):
    def test_nocite_star_keeps_body_scope(self):
        ctx = _build(tex=r"\nocite{*}" + "\n" + r"\cite{K01}",
                     state=_state(cite_keys=("K01",), nocite=True))
        self.assertEqual(_keys(ctx["sets"]["R"]), {"K01"})
        self.assertEqual(_keys(ctx["sets"]["T"]), {"K01"})
        self.assertNotIn("K02", _keys(ctx["sets"]["T"]))
        self.assertIn("nocite{*}", ctx["coverage"]["citation_scope"])

    def test_nocite_star_alone_does_not_delegate_everything(self):
        """只有 `\\nocite{*}`、正文一个 cite 都没有：不把整份 Bib 送进委派集合。"""
        ctx = _build(tex=r"\nocite{*}", state=_state(nocite=True))
        self.assertEqual(_keys(ctx["sets"]["T"]), set())
        self.assertEqual(_keys(ctx["sets"]["R"]), set())

    def test_latex_entry_point_nocite_star_does_not_leak(self):
        with tempfile.TemporaryDirectory(prefix="slcn-scope-nocite-") as raw:
            root = Path(raw)
            source = root / "Anonymous.tex"
            source.write_text(r"""\documentclass{article}
\title{A Fictional Example}
\author{Alice Example}
\begin{document}
\maketitle
\nocite{*}
A concrete sample paragraph with one citation \cite{K01}.
\bibliographystyle{plain}
\bibliography{Anonymous}
\end{document}
""", encoding="utf-8")
            (root / "Anonymous.bib").write_text(BIB_SCOPE, encoding="utf-8")
            proc, taskbook = _run_convert(source, root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(taskbook["ok"], proc.stdout + proc.stderr)
            text = taskbook["text"]
        self.assertIn("K01", text)
        self.assertNotIn("AN-UNCITED-EXTRA-ENTRY", text)


# ══ (c) 注释掉的 \cite 不计入 ═══════════════════════════════════════════════
class CommentedCiteTest(unittest.TestCase):
    def test_commented_cite_not_counted(self):
        tex = "\\cite{K01}\n% \\cite{K02} 这一行被注释掉了\n"
        ctx = _build(tex=tex, state=_state(cite_keys=("K01",)))
        self.assertEqual(_keys(ctx["sets"]["C"]), {"K01"})
        self.assertEqual(_keys(ctx["sets"]["T"]), {"K01"})
        self.assertNotIn("K02", _keys(ctx["sets"]["R"]))

    def test_commented_cite_in_multiline_comment_not_counted(self):
        tex = ("\\cite{K01}\n"
               "% 下面整段都是注释掉的旧引用：\n"
               "% \\cite{K02}\n"
               "% \\nocite{K02}\n")
        ctx = _build(tex=tex, state=_state(cite_keys=("K01",)))
        self.assertEqual(_keys(ctx["sets"]["C"]), {"K01"})
        self.assertEqual(_keys(ctx["sets"]["T"]), {"K01"})


# ══ (d) DOCX 未被引用的尾部参考文献不得整段倾倒 ═════════════════════════════
class DocxTailReferenceTest(unittest.TestCase):
    def test_docx_uncited_tail_references_hidden(self):
        with tempfile.TemporaryDirectory(prefix="slcn-scope-docx-") as raw:
            root = Path(raw)
            source = _make_docx(root / "Anonymous.docx", DOCX_PARAS)
            proc, taskbook = _run_convert(source, root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(taskbook["ok"], proc.stdout + proc.stderr)
            text = taskbook["text"]
        # 被引用的 [1] 照常给（REF-SOURCE 只列它）
        self.assertIn("REF-SOURCE", text)
        self.assertIn("A cited work", text)
        # 未引用的 [2]/[3] 一个字节都不出现
        self.assertNotIn("DOCX-UNCITED-TAIL-BETA", text)
        self.assertNotIn("DOCX-UNCITED-TAIL-GAMMA", text)
        # 也不得退回「整段文献区倾倒」的旧行为
        self.assertNotIn("源文献区片段", text)


    def test_docx_author_year_uncited_tail_not_in_warnings(self):
        """人年制通道：未引用尾条会进「转换警告」，明细不得夹带条目原文。"""
        paras = [
            "A Fictional Example",
            "Alice Example",
            "An anonymous abstract.",
            "Body paragraph one citing an author and year (Alpha, 2020).",
            "Body paragraph two, no citation here.",
            "References",
            "Alpha, A. 2020. A cited work. Journal of Examples.",
            "Beta, B. 2021. DOCX-AY-UNCITED-TAIL-BETA. Nowhere.",
        ]
        with tempfile.TemporaryDirectory(prefix="slcn-scope-docx-ay-") as raw:
            root = Path(raw)
            source = _make_docx(root / "Anonymous.docx", paras)
            proc, taskbook = _run_convert(source, root, work="task-ay")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(taskbook["ok"], proc.stdout + proc.stderr)
            text = taskbook["text"]
        self.assertIn("A cited work", text)                     # 被引用的照常给
        self.assertNotIn("DOCX-AY-UNCITED-TAIL-BETA", text)     # 未引用尾条的原文不进任务书
        self.assertIn("未被正文链路引用", text)                  # 线索仍保留


# ══ (e) 第一段完全不读共享引用库 ═══════════════════════════════════════════
class FirstStageDoesNotReadBibdbTest(unittest.TestCase):
    """把共享库通道打成「一调用就炸」，第一段仍须成功出任务书。"""

    _PATCHED = ("bibdb_readonly_scan", "resolve_bibdb_apply_store",
                "_bibdb_apply_backfill", "resolve_bibdb")

    def _run_in_process(self, source: Path, root: Path, *, work: str):
        import convert as convert_mod  # noqa: PLC0415 — 只在用例内导入产品入口
        calls = []

        def _boom(name):
            def _fail(*_a, **_k):
                calls.append(name)
                raise AssertionError(f"R3 第一段不该调用 {name}（读了共享引用库）")
            return _fail

        saved_argv = sys.argv
        saved_attrs = {name: getattr(convert_mod, name) for name in self._PATCHED}
        out, err = io.StringIO(), io.StringIO()
        try:
            for name in self._PATCHED:
                setattr(convert_mod, name, _boom(name))
            sys.argv = ["convert.py", *_convert_argv(source, root, work=work)]
            os.environ["SLCN_MANUSCRIPTS"] = str(root / "absent-manuscripts")
            os.environ["SLCN_NOTES"] = str(root / "absent-notes")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = convert_mod.main()
                except SystemExit as exc:   # 产品入口用 SystemExit 报错；照实收下
                    code = exc.code if isinstance(exc.code, int) else 2
        finally:
            sys.argv = saved_argv
            for name, value in saved_attrs.items():
                setattr(convert_mod, name, value)
        return code, calls, out.getvalue() + err.getvalue()

    def _check(self, source: Path, root: Path, *, work: str):
        code, calls, log = self._run_in_process(source, root, work=work)
        self.assertEqual(calls, [], f"共享库通道被调用：{calls}\n{log}")
        self.assertEqual(code, 0, log)
        taskbook = read_taskbook(work_dir=str(root / work))
        self.assertTrue(taskbook["ok"], log)
        self.assertEqual(taskbook["template_version"], 3)
        return taskbook["text"]

    def test_latex_first_stage_does_not_touch_bibdb(self):
        with tempfile.TemporaryDirectory(prefix="slcn-scope-nobibdb-tex-") as raw:
            root = Path(raw)
            source = root / "Anonymous.tex"
            source.write_text(r"""\documentclass{article}
\title{A Fictional Example}
\author{Alice Example}
\begin{document}
\maketitle
A concrete sample paragraph with one citation \cite{K01}.
\bibliographystyle{plain}
\bibliography{Anonymous}
\end{document}
""", encoding="utf-8")
            (root / "Anonymous.bib").write_text(BIB_SCOPE, encoding="utf-8")
            text = self._check(source, root, work="task-tex")
        self.assertNotIn("AN-UNCITED-EXTRA-ENTRY", text)

    def test_docx_first_stage_does_not_touch_bibdb(self):
        with tempfile.TemporaryDirectory(prefix="slcn-scope-nobibdb-docx-") as raw:
            root = Path(raw)
            source = _make_docx(root / "Anonymous.docx", DOCX_PARAS)
            text = self._check(source, root, work="task-docx")
        self.assertNotIn("DOCX-UNCITED-TAIL-BETA", text)


# ══ (g) 编号引用写法：区间/分号/全角/跨方括号/混写 ═══════════════════════════
class NumberCitationFormTest(unittest.TestCase):
    r"""正文编号引用的各种写法都必须判得出范围（B-222 A 返工，V-A 红 2/红 4）。

    两种坏结果都要挡住：
      · 判不出 → 保守全量 → 未引用的第 4 条原文泄漏（红线 2）；
      · 只认第一段 → 已引用的第 2、3 条漏出 R/T → 第二段被判 dropped（红线 4）。
    """

    #: 4 条尾部参考文献；第 4 条正文从不引用，marker 用于检测泄漏。
    TAIL = [
        "参考文献",
        "[1] Alpha, A. 2020. First cited work. Journal of Examples.",
        "[2] Beta, B. 2021. Second cited work. Journal of Examples.",
        "[3] Gamma, C. 2022. Third cited work. Journal of Examples.",
        "[4] Delta, D. 2023. RANGE-UNCITED-DELTA. Nowhere.",
    ]
    MARKER = "RANGE-UNCITED-DELTA"

    #: label -> 正文里的引用写法（每一种都表示「引用了 1、2、3 三条」）
    FORMS = {
        "list_comma": "[1,2,3]",
        "list_space": "[1, 2, 3]",
        "list_cn_comma": "[1，2，3]",
        "list_ideographic": "[1、2、3]",
        "list_semicolon": "[1;2;3]",
        "range_hyphen": "[1-3]",
        "range_endash": "[1\u20133]",
        "range_emdash": "[1\u20143]",
        "range_tilde": "[1~3]",
        "range_fullwidth_tilde": "[1\uff5e3]",
        "range_fullwidth_minus": "[1\uff0d3]",
        "range_minus_sign": "[1\u22123]",
        "range_cn_bracket": "\uff3b1-3\uff3d",
        "range_tex_endash": "[1--3]",
        "range_cn_word": "[1\u81f33]",
        "range_cn_word2": "[1\u52303]",
        "mixed_and_range": "[1] and [2-3]",
        "mixed_bracket_span": "[1]-[3]",
        "mixed_endash_span": "[1]\u2013[3]",
        "mixed_list_range": "[1, 2-3]",
    }

    #: 只做单元面复核的写法：`[1, 2-4]` 明确含第 4 条，端到端夹具里第 4 条正是
    #: 「未引用」的那条，会与 marker 断言冲突，故只验集合展开、不进端到端循环。
    UNIT_ONLY_FORMS = {"mixed_list_range_full": "[1, 2-4]"}

    def _convert(self, root: Path, label: str, form: str):
        paras = [
            "A Fictional Example",
            "Alice Example",
            "摘要：匿名合成样例，用于验证编号引用写法。",
            "关键词：编号引用；范围裁剪",
            f"Body paragraph one citing {form}.",
            "Body paragraph two, no citation here.",
            *self.TAIL,
        ]
        source = _make_docx(root / f"{label}.docx", paras)
        proc, taskbook = _run_convert(source, root, work=f"task-{label}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(taskbook["ok"], proc.stdout + proc.stderr)
        return taskbook["text"]

    def test_every_form_scopes_exactly_the_cited_entries(self):
        with tempfile.TemporaryDirectory(prefix="slcn-scope-forms-") as raw:
            for label, form in self.FORMS.items():
                with self.subTest(form=label):
                    root = Path(raw) / label
                    root.mkdir()
                    text = self._convert(root, label, form)
                    # 红线 2：未引用的第 4 条一个字节都不许出现
                    self.assertNotIn(self.MARKER, text,
                                     f"{label}（{form}）泄漏了未引用条目原文")
                    # 红线 4：正文确实引用的 1、2、3 条都要在
                    for title in ("First cited work", "Second cited work",
                                  "Third cited work"):
                        self.assertIn(title, text,
                                      f"{label}（{form}）漏了正文引用的条目：{title}")
                    # 判不出时不得退回保守全量
                    self.assertNotIn("正文未识别到明确的引用键", text,
                                     f"{label}（{form}）退回了保守全量")

    def test_span_and_list_forms_put_all_cited_keys_in_sets(self):
        """集合面复核：区间展开成编号集合，不许只取首尾。"""
        from slcn import ai_workflow_convert as conv

        raw_by_key = {f"K{num:02d}": f"[{num}] Title {num}." for num in range(1, 5)}
        for label, form in {**self.FORMS, **self.UNIT_ONLY_FORMS}.items():
            with self.subTest(form=label):
                cited, unmatched = conv._number_cited_keys(
                    raw_by_key, f"Body cites {form}.")
                expected = {"K01", "K02", "K03"}
                if label == "mixed_list_range_full":
                    expected.add("K04")   # 该写法语义上就含第 4 条
                self.assertEqual(cited, expected, f"{label}（{form}）")
                self.assertEqual(unmatched, set(), f"{label}（{form}）")

    def test_locator_form_takes_number_never_page(self):
        """`[1, p. 398]`：取编号 `1`，**绝不**把页码 398 当编号（红线 4 防丢）。

        定位符与编号共存是真实源稿的常见形态（`[1, p. 398]` / `[1，第 398 页]`）。
        整段判不出会让正文确实引用的第 1 条漏出 C/R/T，第二段据此判它「未引用」
        并 `ST_DROPPED`——正文明明引用却被删，是比泄漏更坏的失败。
        """
        from slcn import ai_workflow_convert as conv

        raw_by_key = {"K01": "[1] One.", "K18": "[18] Eighteen.",
                      "K398": "[398] Three ninety-eight."}
        for body in ("Body cites [1, p. 398].", "Body cites [1，第 398 页].",
                     "Body cites [1 p. 398].", "Body cites [1] and [2, p. 5]."):
            with self.subTest(body=body):
                cited, _unmatched = conv._number_cited_keys(raw_by_key, body)
                self.assertIn("K01", cited, f"{body} 的第 1 条必须被识别")
                self.assertNotIn("K398", cited, "页码绝不能当编号")
                self.assertNotIn("K18", cited, "定位符里的其它数字不得被当编号")
        self.assertTrue(conv._has_number_marks("Body cites [1, p. 398]."))

    def test_nonbracket_marks_are_seen_but_never_guessed(self):
        """`(1)(2)(3)` / `①②③`：松判据要看见，但**不猜**映射（判据松、委派严）。"""
        from slcn import ai_workflow_convert as conv

        raw_by_key = {"K01": "[1] One.", "K02": "[2] Two.", "K03": "[3] Three."}
        for body in ("Body cites (1)(2)(3).", "Body cites ①②③."):
            with self.subTest(body=body):
                self.assertTrue(conv._has_number_marks(body), "松判据必须看见非方括号编号")
                self.assertTrue(conv._has_unresolvable_marks(body))
                cited, _unmatched = conv._number_cited_keys(raw_by_key, body)
                self.assertEqual(cited, set(), "不许把普通括号/圈码当可确认引用")
        self.assertFalse(conv._has_number_marks("Body has no marks at all."))
        self.assertFalse(conv._has_number_marks("See [sic] and [Ed.] here."),
                         "`[sic]`/`[Ed.]` 不是编号，不得误判")


# ══ (h) 有编号标记但判不出 → 显式标记，不得保守全量 ═════════════════════════
class ScopeUnresolvedTest(unittest.TestCase):
    """第二道闸：即使还有漏网写法，也不许把全部条目倒给 AI。"""

    def test_build_context_honours_scope_unresolved(self):
        ctx = _build(tex="", state=_state(), evidence={
            "scope_unresolved": True,
            "cited_keys": ["K01"],
        })
        self.assertEqual(_keys(ctx["sets"]["T"]), {"K01"})
        self.assertNotIn("K02", _keys(ctx["sets"]["T"]))
        self.assertIn("判不出", ctx["coverage"]["citation_scope"])

    def test_scope_unresolved_without_any_key_delegates_nothing(self):
        ctx = _build(tex="", state=_state(), evidence={"scope_unresolved": True})
        self.assertEqual(_keys(ctx["sets"]["T"]), set())
        self.assertEqual(_keys(ctx["sets"]["R"]), set())
        self.assertIn("未识别部分不在此披露", ctx["coverage"]["citation_scope"])

    def test_docx_unknown_number_marks_do_not_leak(self):
        """正文写了编号、清单里却没有对应条目 → 判不出，但未引用条目仍不泄漏。"""
        paras = [
            "A Fictional Example",
            "Alice Example",
            "摘要：匿名合成样例。",
            "关键词：编号引用",
            "Body paragraph one citing [7] which has no matching entry.",
            "Body paragraph two, no citation here.",
            *NumberCitationFormTest.TAIL,
        ]
        with tempfile.TemporaryDirectory(prefix="slcn-scope-unresolved-") as raw:
            root = Path(raw)
            source = _make_docx(root / "Anonymous.docx", paras)
            proc, taskbook = _run_convert(source, root, work="task-unresolved")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(taskbook["ok"], proc.stdout + proc.stderr)
            text = taskbook["text"]
        self.assertNotIn(NumberCitationFormTest.MARKER, text)
        self.assertIn("判不出", text)


# ══ (i) 零可靠引用：零条核查任务 + 原 Bib 与交付候选保全 ══════════════════
TAIL4 = [
    "参考文献",
    "[1] Alpha, A. 2020. First cited work. Journal of Examples.",
    "[2] Beta, B. 2021. Second cited work. Journal of Examples.",
    "[3] Gamma, C. 2022. Third cited work. Journal of Examples.",
    "[4] Delta, D. 2023. ZERO-UNCITED-DELTA. Nowhere.",
]
ZERO_MARKER = "ZERO-UNCITED-DELTA"
TAIL4_KEYS = ["ref3c108c032020", "ref1e11eab42021", "ref5a62a29c2022", "ref3151271a2023"]


def _has_entry(bib_text: str, key: str) -> bool:
    import re as _re
    return _re.search(r"@\w+\s*\{\s*" + _re.escape(key) + r"\s*,", bib_text) is not None


def _input_bib_keys(work: Path) -> list[str]:
    found = sorted(work.glob(".aiw/*/input.bib"))
    if len(found) != 1:
        return []
    text = found[0].read_text(encoding="utf-8")
    return [key for key in TAIL4_KEYS if _has_entry(text, key)]


def _candidate_keys(work: Path) -> tuple[list[str], str]:
    """交付候选里的键（无回件时 preview 会 blocked，但候选文本仍可取）。"""
    import os as _os
    previous = _os.getcwd()
    sys.path.insert(0, _ENGINE)
    try:
        _os.chdir(_ENGINE)
        from slcn import ai_workflow as workflow
        preview = workflow.preview(work_dir=str(work), adopt={workflow.CAT_BIB_VERIFY})
        text = str((preview.bib or {}).get("text") or "")
        return [key for key in TAIL4_KEYS if _has_entry(text, key)], text
    except Exception as exc:  # noqa: BLE001 - 探针用例，异常如实记
        return [], f"<preview 异常：{type(exc).__name__}: {exc}>"
    finally:
        _os.chdir(previous)


def _second_stage(work: Path, external: str):
    import os as _os
    previous = _os.getcwd()
    sys.path.insert(0, _ENGINE)
    try:
        _os.chdir(_ENGINE)
        from slcn import ai_workflow as workflow
        workflow.import_reply(work_dir=str(work), text=external,
                              source_kind="complete_external", replace_active=True)
        preview = workflow.preview(work_dir=str(work), adopt={workflow.CAT_BIB_VERIFY})
        return preview, str((preview.bib or {}).get("text") or "")
    finally:
        _os.chdir(previous)


class ZeroScopePreservationTest(unittest.TestCase):
    """正文零引用：零条核查任务（不泄漏），但原 Bib 与交付候选一条不删。"""

    def test_zero_citation_delegates_nothing_and_preserves_all(self):
        paras = [
            "A Fictional Example",
            "Alice Example",
            "摘要：匿名合成样例，正文不含任何引用标记。",
            "关键词：零引用",
            "Body paragraph one, no citation at all.",
            "Body paragraph two, no citation here.",
            *TAIL4,
        ]
        with tempfile.TemporaryDirectory(prefix="slcn-scope-zero-") as raw:
            root = Path(raw)
            source = _make_docx(root / "Anonymous.docx", paras)
            proc, taskbook = _run_convert(source, root, work="task-zero")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(taskbook["ok"], proc.stdout + proc.stderr)
            text = taskbook["text"]
            work = root / "task-zero"
            state = json.loads((work / ".aiw" / "state.json").read_text(encoding="utf-8"))
            sets = state["context"]["sets"]
            scope_note = str(state["context"]["coverage"]["citation_scope"])
            snapshot = _input_bib_keys(work)
            candidate, _raw = _candidate_keys(work)
        self.assertNotIn(ZERO_MARKER, text, "未引用条目原文不得进任务书任何章节")
        self.assertEqual(sets["T"], [], "零可靠引用 → 委派集合必须为空")
        self.assertEqual(sets["R"], [], "零可靠引用 → 必保集合不得退回全量")
        self.assertIn("零条文献核查任务", scope_note)
        self.assertIn("保全", scope_note)
        self.assertEqual(snapshot, TAIL4_KEYS, "input.bib 快照必须保全全部条目")
        self.assertEqual(candidate, TAIL4_KEYS, "交付候选必须保全全部条目")


class LocatorDeliveryTest(unittest.TestCase):
    """定位符形态：正文引用条目一条都不能在第二段被删。"""

    def test_locator_citations_survive_second_stage(self):
        paras = [
            "A Fictional Example",
            "Alice Example",
            "摘要：匿名合成样例。",
            "关键词：定位符引用",
            "Body cites [1, p. 398] and [2].",
            "Body paragraph two, no citation here.",
            *TAIL4,
        ]
        with tempfile.TemporaryDirectory(prefix="slcn-scope-loc-") as raw:
            root = Path(raw)
            source = _make_docx(root / "Anonymous.docx", paras)
            proc, taskbook = _run_convert(source, root, work="task-loc")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(taskbook["ok"], proc.stdout + proc.stderr)
            work = root / "task-loc"
            state = json.loads((work / ".aiw" / "state.json").read_text(encoding="utf-8"))
            sets = state["context"]["sets"]
            self.assertIn(TAIL4_KEYS[0], sets["C"], "定位符形态的第 1 条必须进引用集合")
            self.assertIn(TAIL4_KEYS[0], sets["R"])
            self.assertIn(TAIL4_KEYS[0], sets["T"])
            # 第二段：外部 Bib 只含第 2 条 → 第 1 条绝不能被判「未引用」删掉
            external = ("@article{%s,\n  title = {Second cited work},\n  year = {2021}\n}\n"
                        % TAIL4_KEYS[1])
            preview, delivered = _second_stage(work, external)
            statuses = {item["key"]: item["status"] for item in preview.bib["items"]}
        self.assertTrue(_has_entry(delivered, TAIL4_KEYS[0]),
                        "正文引用的第 1 条必须留在交付 bib 里")
        self.assertNotEqual(statuses.get(TAIL4_KEYS[0]), "dropped",
                            "正文引用的第 1 条不得被判 dropped")


class FootnoteOnlyCitationTest(unittest.TestCase):
    """正文只在脚注里引用：脚注里的作者-年必须进引用范围，不许退回全量。"""

    def test_footnote_only_citation_scopes_correctly(self):
        paras = [
            "A Fictional Example",
            "Alice Example",
            "摘要：匿名合成样例，引用只在脚注里。",
            "关键词：脚注引用",
            "Body paragraph one without any marker.",
            "Body paragraph two, no citation here.",
            *TAIL4,
        ]
        with tempfile.TemporaryDirectory(prefix="slcn-scope-fn-") as raw:
            root = Path(raw)
            source = root / "Anonymous.docx"
            ps = []
            for index, text in enumerate(paras):
                run = '<w:r><w:t xml:space="preserve">%s</w:t></w:r>' % escape(text)
                if index == 4:
                    run += '<w:r><w:footnoteReference w:id="1"/></w:r>'
                ps.append("<w:p>%s</w:p>" % run)
            document = ('<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>'
                        % (_W_NS, "".join(ps)))
            footnotes = ('<w:footnotes xmlns:w="%s">'
                         '<w:footnote w:id="1"><w:p><w:r>'
                         '<w:t xml:space="preserve">参见 Alpha，2020，第 5 页。</w:t>'
                         '</w:r></w:p></w:footnote></w:footnotes>') % _W_NS
            with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", document)
                archive.writestr("word/footnotes.xml", footnotes)
            proc, taskbook = _run_convert(source, root, work="task-fn")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(taskbook["ok"], proc.stdout + proc.stderr)
            text = taskbook["text"]
            work = root / "task-fn"
            state = json.loads((work / ".aiw" / "state.json").read_text(encoding="utf-8"))
            sets = state["context"]["sets"]
        self.assertNotIn(ZERO_MARKER, text, "脚注引用时未引用条目原文不得进任务书")
        self.assertEqual(sets["T"], [TAIL4_KEYS[0]], "脚注里的作者-年必须映射到第 1 条")
        self.assertNotIn(TAIL4_KEYS[3], sets["T"], "未引用的第 4 条不得委派")


# ══ (j) LaTeX 通道的作者-年脚注：判不出也要如实标明，不许说成「未识别到」 ═══
class LatexFootnoteUnresolvedTest(unittest.TestCase):
    r"""纯 LaTeX 源稿没有「文末原文逐条留档」，脚注里的作者-年引用判不出键。

    这时**不泄漏**（零委派），但必须如实说「判不出」——说成「未识别到可核引用」
    会让作者以为稿子真没引用（PLAN §31「如实标明」）。判据松、委派严。
    """

    def test_latex_author_year_footnote_marks_unresolved(self):
        from slcn import ai_workflow_convert as conv

        cited, unresolved = conv._footnote_cited_keys(
            {}, "正文无引用。\n\\footnote{参见 Alpha，2020，第 5 页。}\n")
        self.assertEqual(cited, set(), "没有映射表时不得凭空解析出键")
        self.assertTrue(unresolved, "看得出是引用却判不出 → 必须置判不出标记")

    def test_latex_plain_footnote_stays_resolved(self):
        """普通脚注（无引导语、无年份）不该被一刀切置位。"""
        from slcn import ai_workflow_convert as conv

        cited, unresolved = conv._footnote_cited_keys(
            {}, "正文无引用。\n\\footnote{这里只是普通注释。}\n")
        self.assertEqual(cited, set())
        self.assertFalse(unresolved)

    def test_numbered_footnote_still_maps_when_table_exists(self):
        """有映射表时编号脚注仍要正常映射（不许因为新增判不出而回退）。"""
        from slcn import ai_workflow_convert as conv

        cited, unresolved = conv._footnote_cited_keys(
            {"K01": "[1] Alpha, A. 2020. First cited work."},
            "\\footnote{参见 [1]。}")
        self.assertEqual(cited, {"K01"})
        self.assertFalse(unresolved)

    def test_taskbook_says_unresolved_not_unrecognized(self):
        """任务书文案：判不出时不得说成「正文未识别到可核引用」。"""
        ctx = _build(tex="", state=_state(), evidence={"scope_unresolved": True})
        note = str(ctx["coverage"]["citation_scope"])
        self.assertIn("判不出", note)
        self.assertIn("脚注", note, "文案要覆盖脚注引用这一来源，不能只提编号标记")
        self.assertIn("保全", note)
        self.assertNotIn("未识别到可核引用", note)


# ══ (i) `.DS_Store` 等系统元数据噪音：精确忽略、不掩盖真实外改 ══════════════
class FinderMetadataNoiseTest(unittest.TestCase):
    """Finder 在目录里随手写 `.DS_Store`，不该让交付失败，也不该掩盖真实外改。

    只认精确名 `.DS_Store`：`.aiw`/`.ai前` 各有既有语义，`previous.txt` 必须仍被
    计入指纹（它是「最终目录里有个陌生文件」的证据）。
    """

    def test_publication_ignore_covers_ds_store_only(self):
        from slcn import ai_workflow_convert as conv

        ignored = conv._publication_ignore(
            ".", [".DS_Store", "a.tex", ".aiw", ".ai前", "previous.txt"])
        self.assertIn(".DS_Store", ignored)      # GUI 侧能力探测依赖这一条
        self.assertIn(".aiw", ignored)
        self.assertIn(".ai前", ignored)
        self.assertNotIn("a.tex", ignored)
        self.assertNotIn("previous.txt", ignored)  # 不做 `.` 前缀通配

    def test_tree_shas_ignores_ds_store(self):
        """根目录 `.DS_Store` 不进指纹；只装 `.DS_Store` 的子目录与真·空子目录同指纹。"""
        from slcn import ai_workflow_convert as conv

        with tempfile.TemporaryDirectory(prefix="slcn-scope-ds-") as raw:
            root = Path(raw)
            (root / "a.tex").write_text("x\n", encoding="utf-8")
            (root / ".DS_Store").write_bytes(b"\x00\x01junk")
            (root / "sub").mkdir()                      # 真空子目录：记空目录标记
            before = conv._tree_shas(str(root))
            self.assertNotIn(".DS_Store", before)
            self.assertIn("sub/", before)
            (root / "sub" / ".DS_Store").write_bytes(b"\x00\x01junk")
            after = conv._tree_shas(str(root))
        self.assertEqual(after, before, "Finder 往空子目录写 `.DS_Store` 不该让指纹变化")
        self.assertNotIn("sub/.DS_Store", after)

    def test_tree_shas_ds_store_only_dir_matches_truly_empty(self):
        """只装 `.DS_Store` 的目录与真·空目录指纹一致（空目录标记用**过滤后**的子项）。"""
        from slcn import ai_workflow_convert as conv

        with tempfile.TemporaryDirectory(prefix="slcn-scope-ds1b-") as raw:
            root = Path(raw)
            (root / "noise").mkdir()
            (root / "noise" / ".DS_Store").write_bytes(b"\x00junk")
            (root / "empty").mkdir()
            shas = conv._tree_shas(str(root))
        self.assertIn("noise/", shas)
        self.assertIn("empty/", shas)
        self.assertNotIn("noise/.DS_Store", shas)

    def test_tree_shas_ds_store_removal_keeps_fingerprint(self):
        """删掉 `.DS_Store`（目录随之变空）不改变指纹。"""
        from slcn import ai_workflow_convert as conv

        with tempfile.TemporaryDirectory(prefix="slcn-scope-ds1c-") as raw:
            root = Path(raw)
            (root / "a.tex").write_text("x\n", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / ".DS_Store").write_bytes(b"\x00junk")
            before = conv._tree_shas(str(root))
            (root / "sub" / ".DS_Store").unlink()
            after = conv._tree_shas(str(root))
        self.assertEqual(after, before, "删掉噪音文件不该让指纹变化")

    def test_tree_shas_real_empty_dir_changes_still_block(self):
        """反例：真实空目录的新增/删除必须让指纹变化（不因忽略 `.DS_Store` 而放过）。"""
        from slcn import ai_workflow_convert as conv

        with tempfile.TemporaryDirectory(prefix="slcn-scope-ds1d-") as raw:
            root = Path(raw)
            (root / "a.tex").write_text("x\n", encoding="utf-8")
            before = conv._tree_shas(str(root))
            (root / "figures").mkdir()
            added = conv._tree_shas(str(root))
            self.assertIn("figures/", added)
            self.assertNotEqual(added, before, "真实空目录新增必须让指纹变化")
            self.assertIn("figures/", conv._changed_message(before, added, what="冻结产物已变化"))
            (root / "figures").rmdir()
            removed = conv._tree_shas(str(root))
            self.assertEqual(removed, before, "删掉真实空目录应回到原指纹")
            self.assertIn("figures/", conv._changed_message(added, removed, what="冻结产物已变化"))

    def test_tree_shas_real_file_changes_still_block(self):
        """反例：真实文件的增/删/改必须让指纹变化，且文案点名该文件。"""
        from slcn import ai_workflow_convert as conv

        with tempfile.TemporaryDirectory(prefix="slcn-scope-ds1e-") as raw:
            root = Path(raw)
            (root / "a.tex").write_text("x\n", encoding="utf-8")
            before = conv._tree_shas(str(root))
            (root / "a.tex").write_text("changed\n", encoding="utf-8")
            modified = conv._tree_shas(str(root))
            self.assertNotEqual(modified, before)
            self.assertIn("a.tex", conv._changed_message(before, modified, what="冻结产物已变化"))
            (root / "new.tex").write_text("n", encoding="utf-8")
            self.assertIn("new.tex", conv._changed_message(modified, conv._tree_shas(str(root)),
                                                           what="冻结产物已变化"))
            (root / "a.tex").unlink()
            self.assertIn("a.tex", conv._changed_message(modified, conv._tree_shas(str(root)),
                                                         what="冻结产物已变化"))

    def test_tree_shas_still_rejects_symlinks(self):
        """反例：符号链接仍阻断（忽略噪音不得放宽到链接）。"""
        from slcn import ai_workflow_convert as conv

        with tempfile.TemporaryDirectory(prefix="slcn-scope-ds1f-") as raw:
            root = Path(raw)
            (root / "a.tex").write_text("x\n", encoding="utf-8")
            os.symlink(str(root / "a.tex"), str(root / "link.tex"))
            with self.assertRaises(ValueError) as caught:
                conv._tree_shas(str(root))
            self.assertIn("符号链接", str(caught.exception))
            (root / "link.tex").unlink()
            dir_link = Path(raw).parent / (Path(raw).name + "-dirlink")
            os.symlink(str(root), str(dir_link))
            try:
                with self.assertRaises(ValueError) as caught2:
                    conv._tree_shas(str(dir_link))
                self.assertIn("符号链接", str(caught2.exception))
            finally:
                dir_link.unlink()

    def test_tree_shas_still_counts_real_files(self):
        from slcn import ai_workflow_convert as conv

        with tempfile.TemporaryDirectory(prefix="slcn-scope-ds2-") as raw:
            root = Path(raw)
            (root / "a.tex").write_text("x\n", encoding="utf-8")
            before = conv._tree_shas(str(root))
            (root / "previous.txt").write_text("p", encoding="utf-8")
            after = conv._tree_shas(str(root))
        self.assertIn("previous.txt", after)
        self.assertNotEqual(after, before)

    def test_stage_ds_store_no_longer_blocks_delivery(self):
        """实测矩阵 a：stage 里有 `.DS_Store` → 交付成功，且它不进最终目录。"""
        with tempfile.TemporaryDirectory(prefix="slcn-scope-ds3-") as raw:
            root = Path(raw)
            source = root / "Anonymous.tex"
            source.write_text(r"""\documentclass{article}
\title{An Anonymous Example}
\author{Alice Example}
\begin{document}
\maketitle
An anonymous sample paragraph.
\end{document}
""", encoding="utf-8")
            work = root / "work"
            env = dict(os.environ, SLCN_MANUSCRIPTS=str(root / "absent"),
                       SLCN_NOTES=str(root / "absent-notes"))
            first = subprocess.run(
                [sys.executable, "-B", str(Path(_ENGINE) / "convert.py"),
                 "--source", str(source), "--out", str(root / "out"),
                 "--name", "Anonymous", "--lang", "en", "--force",
                 "--no-bibdb-apply", "--ai-taskbook-version", "3",
                 "--ai-execute", "typeset,text", "--ai-work-dir", str(work)],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            state = json.loads((work / ".aiw" / "state.json").read_text(encoding="utf-8"))
            stage = Path(state["conversion"]["stage_dir"])
            final = Path(state["conversion"]["final_dir"])
            (stage / ".DS_Store").write_bytes(b"\x00\x01junk")
            delivered = subprocess.run(
                [sys.executable, "-B", str(Path(_ENGINE) / "convert.py"),
                 "--ai-workflow", "--work-dir", str(work), "--json",
                 "deliver", "--use-original"],
                capture_output=True, text=True, env=env,
            )
            payload = json.loads(delivered.stdout)
            self.assertEqual(delivered.returncode, 0, delivered.stdout + delivered.stderr)
            self.assertTrue(payload["delivered"], payload)
            self.assertFalse((final / ".DS_Store").exists())

    def test_stage_ds_store_does_not_hide_real_intruder(self):
        """实测矩阵 b：`.DS_Store` 与真实外改并存 → 仍失败，且 error 点名真实文件。"""
        with tempfile.TemporaryDirectory(prefix="slcn-scope-ds4-") as raw:
            root = Path(raw)
            source = root / "Anonymous.tex"
            source.write_text(r"""\documentclass{article}
\title{An Anonymous Example}
\author{Alice Example}
\begin{document}
\maketitle
An anonymous sample paragraph.
\end{document}
""", encoding="utf-8")
            work = root / "work"
            env = dict(os.environ, SLCN_MANUSCRIPTS=str(root / "absent"),
                       SLCN_NOTES=str(root / "absent-notes"))
            subprocess.run(
                [sys.executable, "-B", str(Path(_ENGINE) / "convert.py"),
                 "--source", str(source), "--out", str(root / "out"),
                 "--name", "Anonymous", "--lang", "en", "--force",
                 "--no-bibdb-apply", "--ai-taskbook-version", "3",
                 "--ai-execute", "typeset,text", "--ai-work-dir", str(work)],
                capture_output=True, text=True, env=env, check=True,
            )
            state = json.loads((work / ".aiw" / "state.json").read_text(encoding="utf-8"))
            stage = Path(state["conversion"]["stage_dir"])
            (stage / ".DS_Store").write_bytes(b"\x00\x01junk")
            (stage / "intruder.txt").write_text("external edit", encoding="utf-8")
            delivered = subprocess.run(
                [sys.executable, "-B", str(Path(_ENGINE) / "convert.py"),
                 "--ai-workflow", "--work-dir", str(work), "--json",
                 "deliver", "--use-original"],
                capture_output=True, text=True, env=env,
            )
            payload = json.loads(delivered.stdout)
        self.assertEqual(delivered.returncode, 2, delivered.stdout + delivered.stderr)
        self.assertFalse(payload["delivered"])
        self.assertIn("intruder.txt", payload["error"])   # 可定位到真实文件
        self.assertNotIn(".DS_Store", payload["error"])   # 不拿噪音当原因


# ══ (f) 普通（非 AI）流程不受影响 ═══════════════════════════════════════════
class PlainConvertUnaffectedTest(unittest.TestCase):
    """非 R3 流程仍然照旧查库（这里只证明流程能跑通、产物照旧生成）。"""

    def test_plain_latex_convert_still_works(self):
        with tempfile.TemporaryDirectory(prefix="slcn-scope-plain-") as raw:
            root = Path(raw)
            source = root / "Anonymous.tex"
            source.write_text(r"""\documentclass{article}
\title{A Fictional Example}
\author{Alice Example}
\begin{document}
\maketitle
A concrete sample paragraph with one citation \cite{K01}.
\bibliographystyle{plain}
\bibliography{Anonymous}
\end{document}
""", encoding="utf-8")
            (root / "Anonymous.bib").write_text(BIB_SCOPE, encoding="utf-8")
            env = dict(os.environ, SLCN_MANUSCRIPTS=str(root / "absent-manuscripts"),
                       SLCN_NOTES=str(root / "absent-notes"))
            proc = subprocess.run(
                [sys.executable, "-B", str(Path(_ENGINE) / "convert.py"),
                 "--source", str(source), "--out", str(root / "out"), "--no-original"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            produced = list((root / "out").rglob("*.tex"))
            self.assertTrue(produced, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
