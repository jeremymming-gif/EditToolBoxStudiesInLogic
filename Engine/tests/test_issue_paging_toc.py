"""R3 目录隔离更新、篇目核对和当前编译产物。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfWriter
from slcn import toc, tocdelivery

PageRange = namedtuple("PageRange", "folder first last count")
RANGES = (PageRange("01 sample one", 1, 5, 5), PageRange("02 sample two", 6, 8, 3))

ZH = r"""\documentclass[fontset=fandol]{ctexart}
\usepackage{tabularx}
\pagestyle{empty}
\newcommand{\vdistance}{\\[3mm]}
\begin{document}
\begin{tabularx}{\textwidth}{Xrr}
{\large\bfseries 专栏：合成专题}
&\multicolumn{2}{r}{主持人：甲乙}\\[3mm]
% { & 43\\ 本注释必须逐字保留
{\large ~~~~ 第一篇}
\newline{\large\phantom{~~~~ }——样本}
&\emph{甲乙} &  81\\ % 列尾保留
\vdistance
{\large 第二篇} &\textit{丙丁}
& {91}\\
\end{tabularx}
\end{document}
"""

EN = r"""\documentclass{article}
\usepackage{tabularx}
\pagestyle{empty}
\newcommand{\vdistance}{\\[3mm]}
\begin{document}
\begin{tabularx}{\textwidth}
{@{}X@{\hspace{3em}}r@{}}
\large\textbf{Column: Synthetic Area}\\[3mm]
\large First Sample:\\
\large A Test\\
\textit{\phantom{~~~~ }Author One} & 81\\[1mm]
\vdistance
\large Second Sample\\
\textit{Author Two} & 91\\
\end{tabularx}
\end{document}
"""


def make_article(root, folder, zh, en, zh_author, en_author):
    parent = root / folder
    parent.mkdir()
    body = (
        r"\newcommand{\myvolnumber}{19} % 输入卷号" "\n"
        r"\newcommand{\myissnumber}{5} % 输入当年期号" "\n"
        r"\newcommand{\myyearnumber}{2026} % 输入出版年份" "\n"
        r"\newcommand{\mystartpage}{81} % 输入起始页码" "\n"
        r"\newcommand{\mytitle} % 请输入论文题目" "\n"
        f"{{{zh}}}\n"
        r"\newcommand{\mytitleEN} % 请输入英文标题" "\n"
        f"{{{en}}}\n"
        r"\newcommand{\myfirstauthor} % 请输入第一作者姓名" "\n"
        f"{{{zh_author}}}\n"
        r"\newcommand{\myfirstauthorEN} % Insert the first author name." "\n"
        f"{{{en_author}}}\n"
    )
    (parent / f"{folder.partition(' ')[2]}.tex").write_text(body, encoding="utf-8")
    # 旧信息检查按最后一个词找 TeX。这个干扰项必须被忽略。
    (parent / f"{folder.split()[-1]}.tex").write_text("% wrong file", encoding="utf-8")


def fake_compile(folder, stems):
    result = {}
    for key, stem in stems:
        pdf = Path(folder) / f"{stem}.pdf"
        with PdfWriter() as writer:
            writer.add_blank_page(width=595, height=842)
            writer.write(pdf)
        pdf.with_suffix(".log").write_text(f"Output written on {pdf.name} (1 page).")
        result[key] = (True, str(pdf))
    return result


class StagedTocTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("slcn.issuepaging_toc"),
                             "缺少暂存目录刷新入口")
        from slcn import issuepaging_toc
        self.api = issuepaging_toc
        tmp = tempfile.TemporaryDirectory(prefix="slcn_toc_r3_")
        self.addCleanup(tmp.cleanup)
        self.issue = Path(tmp.name) / "2026年第5期(90)"
        self.issue.mkdir()
        make_article(self.issue, RANGES[0].folder, "第一篇——样本",
                     "First Sample: A Test", "甲乙", "Author One")
        make_article(self.issue, RANGES[1].folder, "第二篇", "Second Sample", "丙丁", "Author Two")
        self.target = self.issue / "000 目录"
        compiler = patch.object(tocdelivery, "compile_pdfs", side_effect=fake_compile)
        self.compilers = compiler.start()
        self.addCleanup(compiler.stop)

    def write_old_toc(self, *, en=EN, zh=ZH):
        self.target.mkdir()
        old_files = {
            "Chinese Content.tex": b"\xef\xbb\xbf" + zh.replace("\n", "\r\n").encode(),
            "contents.tex": en.encode(),
            "Chinese Content.pdf": b"old zh pdf",
            "contents.pdf": b"old en pdf",
        }
        for name, data in old_files.items():
            (self.target / name).write_bytes(data)
        return old_files

    def test_preserves_column_layout_and_all_bytes_except_page_digits(self):
        old = self.write_old_toc()
        staged = self.api.stage_issue_toc(self.issue, RANGES)
        self.assertEqual(staged.toc_dir, self.target)
        self.assertEqual(staged.mode, "updated")
        self.assertEqual(staged.tex["zh"].read_bytes(),
                         old["Chinese Content.tex"].replace(b"  81", b"  1").replace(b"{91}", b"{6}"))
        self.assertEqual(staged.tex["en"].read_bytes(),
                         old["contents.tex"].replace(b"& 81", b"& 1").replace(b"& 91", b"& 6"))
        self.assertEqual([(item.folder, item.zh, item.en) for item in staged.entries],
                         [("01 sample one", 1, 1), ("02 sample two", 6, 6)])
        self.assertEqual({p.name: p.read_bytes() for p in self.target.iterdir()}, old)

    def test_ambiguous_page_column_requires_explicit_regeneration(self):
        old = self.write_old_toc(en=EN.replace("& 81", "& \u201481"))
        self.assertTrue(self.api.check_issue_toc(self.issue, RANGES))
        with self.assertRaises(self.api.IssueTocError):
            self.api.stage_issue_toc(self.issue, RANGES)
        self.assertEqual({p.name: p.read_bytes() for p in self.target.iterdir()}, old)
        self.compilers.assert_not_called()
        staged = self.api.stage_issue_toc(self.issue, RANGES, regenerate=True)
        self.assertEqual(staged.mode, "regenerated")
        self.assertIn("& 6", staged.tex["en"].read_text(encoding="utf-8"))

    def test_reordered_existing_rows_require_explicit_regeneration(self):
        first = "\\large First Sample:"
        split = EN.index("\\vdistance", EN.index(first))
        end = EN.index("\\end{tabularx}")
        en = EN[:EN.index(first)] + EN[split + len("\\vdistance\n"):end] + EN[EN.index(first):split] + EN[end:]
        self.write_old_toc(en=en)
        with self.assertRaises(self.api.IssueTocError):
            self.api.stage_issue_toc(self.issue, RANGES)
        self.compilers.assert_not_called()

    def test_inline_italic_english_title_is_distinct_from_author_row(self):
        en = EN.replace("First Sample:", r"\textit{First} Sample:")
        (self.issue / "01 sample one" / "sample one.tex").write_text(
            (self.issue / "01 sample one" / "sample one.tex").read_text(encoding="utf-8")
            .replace("First Sample:", r"\textit{First} Sample:"), encoding="utf-8")
        self.write_old_toc(en=en)
        staged = self.api.stage_issue_toc(self.issue, RANGES)
        self.addCleanup(staged.cleanup)
        self.assertIn(r"\textit{First} Sample:", staged.tex["en"].read_text(encoding="utf-8"))

    def test_missing_directory_uses_only_current_range_inputs(self):
        old_dir = self.issue / ".toolbox-tmp"
        old_dir.mkdir()
        stale = old_dir / "contents_9999-12-31.tex"
        stale.write_text("stale", encoding="utf-8")
        os.utime(stale, (2_100_000_000, 2_100_000_000))
        unused = self.issue / "03 excluded"
        unused.mkdir()
        (unused / "excluded.tex").write_text("% not part of this issue")
        staged = self.api.stage_issue_toc(self.issue, RANGES)
        self.assertEqual(staged.mode, "generated")
        self.assertEqual(staged.toc_dir.name, "000 Table of Contents")
        self.assertEqual(staged.pages, {"zh": 1, "en": 1})
        self.assertIn("Volume 19, Number 5, October 2026", staged.tex["en"].read_text(encoding="utf-8"))
        self.assertIn("& 6", staged.tex["zh"].read_text(encoding="utf-8"))
        self.assertNotIn("stale", staged.tex["en"].read_text(encoding="utf-8"))
        self.assertEqual(stale.read_text(), "stale")
        self.assertFalse(staged.toc_dir.exists())

    def test_english_compile_failure_rejects_whole_pair(self):
        old = self.write_old_toc()

        def fail_english(folder, stems):
            return {key: (False, "synthetic XeLaTeX error") if key == "en"
                    else fake_compile(folder, ((key, stem),))[key] for key, stem in stems}

        self.compilers.side_effect = fail_english
        with self.assertRaisesRegex(self.api.IssueTocError, "contents.tex"):
            self.api.stage_issue_toc(self.issue, RANGES)
        self.assertEqual({p.name: p.read_bytes() for p in self.target.iterdir()}, old)

    def test_old_pdf_cannot_satisfy_a_success_without_a_new_output(self):
        self.write_old_toc()
        self.compilers.side_effect = lambda folder, stems: {
            key: (True, str(Path(folder) / f"{stem}.pdf")) for key, stem in stems
        }
        with self.assertRaisesRegex(self.api.IssueTocError, "PDF"):
            self.api.stage_issue_toc(self.issue, RANGES)


    def compile_pages(self, pages):
        def compile_pair(folder, stems):
            result = {}
            for key, stem in stems:
                pdf = Path(folder) / f"{stem}.pdf"
                with PdfWriter() as writer:
                    for _ in range(pages):
                        writer.add_blank_page(width=595, height=842)
                    writer.write(pdf)
                pdf.with_suffix(".log").write_text(
                    f"Output written on {pdf.name} ({pages} pages).")
                result[key] = (True, str(pdf))
            return result
        self.compilers.side_effect = compile_pair

    def test_two_page_setting_accepts_both_modes_without_compression(self):
        import json
        old = self.write_old_toc()
        (self.issue / toc.TocSettings.fileName).write_text(json.dumps({"singlePageToc": False}))
        self.compile_pages(2)
        for regenerate in (False, True):
            with self.subTest(regenerate=regenerate), patch.object(
                    tocdelivery, "fit_single_page", side_effect=AssertionError("no compression")):
                staged = self.api.stage_issue_toc(self.issue, RANGES, regenerate=regenerate)
                self.addCleanup(staged.cleanup)
                self.assertEqual(staged.pages, {"zh": 2, "en": 2})
                self.assertEqual((staged.max_pages, staged.compression_skipped), (2, True))
        self.assertEqual({p.name: p.read_bytes() for p in self.target.iterdir()}, old)

    def test_three_pages_rejected_in_both_modes_without_touching_000(self):
        import json
        old = self.write_old_toc()
        (self.issue / toc.TocSettings.fileName).write_text(json.dumps({"singlePageToc": False}))
        self.compile_pages(3)
        for regenerate in (False, True):
            with self.subTest(regenerate=regenerate), self.assertRaisesRegex(
                    self.api.IssueTocError, "超过.*2.*页"):
                self.api.stage_issue_toc(self.issue, RANGES, regenerate=regenerate)
            self.assertEqual({p.name: p.read_bytes() for p in self.target.iterdir()}, old)

    def test_missing_and_true_settings_reject_same_two_page_input_identically(self):
        import json
        old = self.write_old_toc()
        self.compile_pages(2)
        diagnostics = []
        for settings in (None, {}, {"singlePageToc": True}):
            if settings is not None:
                (self.issue / toc.TocSettings.fileName).write_text(json.dumps(settings))
            with self.assertRaises(self.api.IssueTocError) as caught:
                self.api.stage_issue_toc(self.issue, RANGES)
            diagnostics.append(str(caught.exception))
            self.assertEqual({p.name: p.read_bytes() for p in self.target.iterdir()}, old)
        self.assertEqual(diagnostics, ["更新页码后目录超过一页，请手工调整版式或选择重生成目录"] * 3)

    def test_default_and_true_keep_single_page_tex_bytes_identical(self):
        import json
        self.write_old_toc()
        for regenerate in (False, True):
            snapshots = []
            for settings in ({}, {"singlePageToc": True}):
                (self.issue / toc.TocSettings.fileName).write_text(json.dumps(settings))
                staged = self.api.stage_issue_toc(self.issue, RANGES, regenerate=regenerate)
                self.addCleanup(staged.cleanup)
                snapshots.append({key: path.read_bytes() for key, path in staged.tex.items()})
                self.assertEqual((staged.max_pages, staged.compression_skipped), (1, False))
            self.assertEqual(*snapshots)

    def test_regeneration_passes_selected_limit_to_shared_fitter(self):
        import json
        self.write_old_toc()
        for setting, limit in ((True, 1), (False, 2)):
            (self.issue / toc.TocSettings.fileName).write_text(json.dumps({"singlePageToc": setting}))
            self.compile_pages(limit + 1)
            with patch.object(tocdelivery, "fit_single_page", return_value=(
                    {"zh": limit + 1, "en": limit + 1}, "synthetic limit failure", {})) as fit:
                with self.assertRaisesRegex(self.api.IssueTocError, "synthetic limit failure"):
                    self.api.stage_issue_toc(self.issue, RANGES, regenerate=True)
                self.assertEqual(fit.call_args.kwargs["max_pages"], limit)


if __name__ == "__main__":
    unittest.main()
