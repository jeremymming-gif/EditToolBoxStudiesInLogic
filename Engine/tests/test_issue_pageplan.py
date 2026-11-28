#!/usr/bin/env python3.14
"""R3 page vectors, strict TeX, and isolated compiler results."""
from pathlib import Path
import codecs
import shutil
import sys
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfWriter

from slcn import issuepaging as paging
from slcn.pageplan import MeasuredArticle, PagePlanError, PageRange, plan_pages
from slcn.pdfmerge import merge_issue, resolve_issue_article_pdf


def tex_bytes(first=1, last=10, count=10):
    return codecs.BOM_UTF8 + (
        r"\documentclass{article}" "\r\n"
        r"\newcommand{\myfirstpage}{% 页首" "\r\n"
        f"  {first}  % 保留注释\r\n"
        "} % 输入起始页码\r\n"
        rf"\newcommand{{\mylastpage}}{{{last}}} % 输入终止页码" "\r\n"
        rf"\newcommand{{\mypages}}{{{count}}} % 输入页数" "\r\n"
        r"\begin{document}Synthetic text\end{document}" "\r\n"
    ).encode()


def write_pdf(path, count):
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(72, 72)
    writer.write(path)


class IssuePagePlanTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.issue = self.root / "2026年第6期"
        self.issue.mkdir()
        self.stage = self.root / "stage"

    def article(self, folder, first, last, count, *, pdf_name=None, pdf_count=None):
        sub = self.issue / folder
        sub.mkdir()
        source = sub / (folder.split(" ", 1)[1] + ".tex")
        source.write_bytes(tex_bytes(first, last, count))
        if pdf_count is not None:
            write_pdf(sub / (pdf_name or (source.stem + ".pdf")), pdf_count)
        return source

    def prepare(self, counts, **kwargs):
        remaining = {stem: iter(values) for stem, values in counts.items()}

        def compile_pdf(directory, stem, **kwargs):
            folder = Path(directory)
            output = folder / (stem + ".pdf")
            self.assertFalse(output.exists(), "compiler must start without a prior output")
            count = next(remaining[stem])
            write_pdf(output, count)
            return True, str(output)

        with patch.object(paging, "compile_pdf", side_effect=compile_pdf):
            return paging.prepare_articles(self.issue, self.stage, **kwargs)

    def test_planning_is_ordered_and_starts_at_one(self):
        measured = [MeasuredArticle("01 A", 10), MeasuredArticle("02 B", 12),
                    MeasuredArticle("03 C", 8)]
        self.assertEqual(plan_pages(measured), (
            PageRange("01 A", 1, 10, 10), PageRange("02 B", 11, 22, 12),
            PageRange("03 C", 23, 30, 8),
        ))
        for count in (None, 0, -1, True):
            with self.subTest(count=count), self.assertRaises(PagePlanError):
                plan_pages([MeasuredArticle("01 A", count)])

    def test_shorter_middle_article_recompiles_final_article_and_preserves_bytes(self):
        a = self.article("01 A", 1, 10, 10, pdf_count=10)
        b = self.article("02 B", 11, 22, 12, pdf_count=12)
        c = self.article("03 C", 23, 30, 8, pdf_count=8)
        (self.issue / "000 Contents").mkdir()
        write_pdf(self.issue / "扉页.pdf", 2)
        write_pdf(self.issue / "征稿启事.pdf", 4)
        original_files = {p: p.read_bytes() for p in self.issue.rglob("*") if p.is_file()}

        result = self.prepare({"A": [10], "B": [10, 10], "C": [8, 8]})

        self.assertEqual(result.page_ranges, (
            PageRange("01 A", 1, 10, 10), PageRange("02 B", 11, 20, 10),
            PageRange("03 C", 21, 28, 8),
        ))
        self.assertEqual([x.recompiled for x in result.articles], [False, True, True])
        self.assertEqual([x.tex.read_bytes() for x in result.articles], [
            tex_bytes(), tex_bytes(11, 20, 10), tex_bytes(21, 28, 8),
        ])
        self.assertEqual([x.source.tex for x in result.articles], [a, b, c])
        self.assertEqual({p: p.read_bytes() for p in self.issue.rglob("*") if p.is_file()},
                         original_files)

    def test_stale_source_pdf_cannot_satisfy_a_successful_compile(self):
        source = self.article("01 A", 1, 10, 10, pdf_count=10)
        old_pdf = source.with_suffix(".pdf").read_bytes()
        with patch.object(paging, "compile_pdf", return_value=(True, "old.pdf")):
            with self.assertRaisesRegex(paging.PageCompileError, "PDF"):
                paging.prepare_articles(self.issue, self.stage)
        self.assertEqual(source.with_suffix(".pdf").read_bytes(), old_pdf)

    def test_fresh_unreadable_zero_and_log_disagreement_stop(self):
        self.article("01 A", 1, 10, 10)
        for kind in ("corrupt", "zero", "log"):
            def compile_pdf(directory, stem, **kwargs):
                output = Path(directory) / (stem + ".pdf")
                if kind == "corrupt":
                    output.write_bytes(b"not a PDF")
                else:
                    write_pdf(output, 0 if kind == "zero" else 10)
                output.with_suffix(".log").write_text("Output written on A.pdf (9 pages).\n")
                return True, str(output)

            with self.subTest(kind=kind), patch.object(paging, "compile_pdf", side_effect=compile_pdf):
                with self.assertRaisesRegex(paging.PageCompileError, "PDF"):
                    paging.prepare_articles(self.issue, self.root / kind)

    def test_compiler_stem_and_existing_merge_target_can_differ(self):
        source = self.article("01 Jane Doe", 1, 2, 2, pdf_name="Doe.pdf", pdf_count=4)
        staged = self.prepare({"Jane Doe": [3, 3]}).articles[0]
        self.assertEqual(staged.source.tex, source)
        self.assertEqual(staged.source.pdf, source.with_name("Doe.pdf"))
        self.assertEqual(staged.pdf.name, "Doe.pdf")
        self.assertTrue(staged.pdf.is_file())
        self.assertFalse(staged.pdf.with_name("Jane Doe.pdf").exists())
        self.assertEqual(staged.physical_pages, 3)
        self.assertTrue(staged.recompiled)

    def test_pdf_target_matches_merge_and_case_on_disk(self):
        source = self.article("01 Jane Doe", 1, 1, 1)
        pdf = source.with_name("DOE.PDF")
        self.assertEqual(paging.inspect_articles(self.issue)[0].pdf,
                         source.with_name("Jane Doe.pdf"))
        write_pdf(pdf, 1)
        self.assertEqual(paging.inspect_articles(self.issue)[0].pdf, pdf)
        pdf.rename(source.with_name("Unrelated.pdf"))
        with self.assertRaisesRegex(PagePlanError, "PDF 命名"):
            paging.inspect_articles(self.issue)

    def test_preflight_stops_ambiguous_pdf_or_loose_tex(self):
        source = self.article("01 Jane Doe", 1, 2, 2, pdf_name="Doe.pdf", pdf_count=4)
        extra = source.with_suffix(".pdf")
        write_pdf(extra, 1)
        with self.assertRaisesRegex(PagePlanError, "PDF"):
            paging.inspect_articles(self.issue)
        extra.unlink()
        source.rename(source.with_name("Doe.tex"))
        with self.assertRaisesRegex(PagePlanError, "TeX"):
            paging.inspect_articles(self.issue)

    def test_preflight_rejects_missing_commented_parameterized_or_shadowed_definition(self):
        source = self.article("01 A", 1, 10, 10)
        simple = rb"\newcommand{\mypages}{10}"
        for replacement in (
            b"",
            b"%" + simple,
            rb"\newcommand{\mypages}[1]{10}",
            simple + b"\r\n" + rb"\renewcommand{\mypages}{12}",
            simple + b"\r\n" + rb"\def\mypages{12}",
            rb"\iffalse " + simple + rb"\fi",
            rb"\iffalse \iftrue \fi " + simple,
            rb"\newcommand{\other}{" + simple + b"}",
        ):
            with self.subTest(replacement=replacement):
                source.write_bytes(tex_bytes().replace(simple, replacement))
                with self.assertRaisesRegex(PagePlanError, "mypages"):
                    paging.inspect_articles(self.issue)

    def test_body_target_can_be_created_alongside_pdf_figure(self):
        source = self.article("01 Jane Doe", 1, 1, 1)
        write_pdf(source.with_name("figure.pdf"), 1)

        article = paging.inspect_articles(self.issue)[0]

        self.assertEqual(article.pdf, source.with_name("Jane Doe.pdf"))
        article.pdf.touch()
        resolved = resolve_issue_article_pdf(str(source.parent), article.folder)
        self.assertEqual(Path(resolved[0]), article.pdf)

    def test_pdf_figure_compiles_and_body_is_recognized_by_issue_merge(self):
        if not shutil.which("xelatex") or not shutil.which("biber"):
            self.skipTest("XeLaTeX and Biber are required for the isolated compile probe")

        source = self.article("01 Jane Doe", 1, 1, 1)
        figure_source = self.root / "figure-source.tex"
        figure_source.write_text(
            r"\documentclass{article}\pagestyle{empty}\begin{document}"
            r"\rule{1cm}{1cm}\end{document}",
            encoding="utf-8",
        )
        subprocess.run(
            ["xelatex", "-no-shell-escape", "-interaction=nonstopmode", figure_source.name],
            cwd=self.root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        source.write_text(
            r"""\documentclass{article}
\usepackage{graphicx}
\newcommand{\myfirstpage}{1}
\newcommand{\mylastpage}{1}
\newcommand{\mypages}{1}
\begin{document}
\includegraphics[width=1cm]{figure.pdf}
Synthetic body text.
\end{document}
""",
            encoding="utf-8",
        )
        shutil.copyfile(self.root / "figure-source.pdf", source.with_name("figure.pdf"))

        staged = paging.prepare_articles(self.issue, self.stage)
        article = staged.articles[0]
        merge_output = self.root / "merged.pdf"
        merged = merge_issue(str(self.stage), out=str(merge_output), log=lambda *_: None)

        self.assertEqual(article.pdf.name, "Jane Doe.pdf")
        self.assertTrue(article.pdf.is_file())
        self.assertEqual(article.physical_pages, 1)
        self.assertEqual(merged.page_count, 1)
        self.assertNotIn("Jane Doe.pdf", merged.missing)
        self.assertIn(("Doe", 0), merged.outline)
        self.assertTrue(merge_output.is_file())

    def test_biber_uses_the_isolated_compile_environment(self):
        self.article("01 A", 1, 1, 1)
        captured = {}

        def compile_pdf(directory, stem, **kwargs):
            def run(command, **options):
                captured.update(options)
                return SimpleNamespace(returncode=0)

            kwargs["run_biber"](run, ["biber", stem], directory)
            output = Path(directory) / f"{stem}.pdf"
            write_pdf(output, 1)
            return True, str(output)

        with patch.object(paging, "compile_pdf", side_effect=compile_pdf):
            paging.prepare_articles(self.issue, self.stage)

        environment = captured["env"]
        self.assertIn("HOME", environment)
        self.assertIn("TEXMFHOME", environment)
        self.assertEqual(captured["cwd"], str(self.stage / "01 A"))
        self.assertTrue(captured["quiet"])
        self.assertFalse(captured["check"])

    def test_recompile_replans_until_stable(self):
        self.article("01 A", 1, 1, 1)
        result = self.prepare({"A": [2, 3, 3]})
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.page_ranges, (PageRange("01 A", 1, 3, 3),))

    def test_repeated_vector_is_reported_as_oscillation(self):
        self.article("01 A", 1, 1, 1)
        with self.assertRaisesRegex(paging.PageCompileError, "振荡"):
            self.prepare({"A": [2, 3, 2]})

    def test_steady_change_stops_after_three_macro_rounds(self):
        self.article("01 A", 1, 1, 1)
        with self.assertRaisesRegex(paging.PageCompileError, "3"):
            self.prepare({"A": [2, 3, 4, 5]})

    def test_automatic_subset_retains_new_selected_pdf_when_pagination_matches(self):
        self.article("01 A", 1, 2, 2, pdf_count=2)
        self.article("02 B", 3, 5, 3, pdf_count=3)
        result = self.prepare({"B": [3]}, only=(2,))
        self.assertFalse(result.full_refresh)
        self.assertEqual(result.rounds, 0)
        self.assertEqual([x.compiled for x in result.articles], [False, True])
        self.assertEqual(result.page_ranges, (PageRange("01 A", 1, 2, 2),
                                              PageRange("02 B", 3, 5, 3)))

    def test_automatic_subset_escalates_when_old_declaration_incoherent(self):
        self.article("01 A", 1, 3, 2, pdf_count=2)
        self.article("02 B", 3, 5, 3, pdf_count=3)
        result = self.prepare({"A": [2, 2], "B": [3]}, only=(2,))
        self.assertTrue(result.full_refresh)
        self.assertEqual(result.page_ranges, (PageRange("01 A", 1, 2, 2),
                                              PageRange("02 B", 3, 5, 3)))

    def test_numbered_excluded_folder_can_be_removed_without_changing_article_order(self):
        self.article("01 A", 1, 1, 1)
        self.article("02 B", 2, 2, 1)
        (self.issue / "01 Appendix Info").mkdir()
        with self.assertRaises(PagePlanError):
            paging.inspect_articles(self.issue)
        result = self.prepare({"A": [1], "B": [1]}, excluded=("01 Appendix Info",))
        self.assertEqual([r.folder for r in result.page_ranges], ["01 A", "02 B"])
        self.assertFalse((self.stage / "01 Appendix Info").exists())

    def test_preflight_rejects_duplicate_and_missing_numbers(self):
        self.article("01 A", 1, 1, 1)
        duplicate = self.article("01 B", 2, 2, 1).parent
        with self.assertRaisesRegex(PagePlanError, "编号"):
            paging.inspect_articles(self.issue)
        duplicate.rename(self.issue / "03 B")
        with self.assertRaisesRegex(PagePlanError, "编号"):
            paging.inspect_articles(self.issue)

    def test_copy_and_fingerprint_ignore_source_archives_and_reject_hardlinks(self):
        source = self.article("01 A", 1, 1, 1)
        original = source.parent / "Original"
        original.mkdir()
        archived = original / "forbidden.txt"
        archived.write_text("archive sentinel", encoding="utf8")
        support = source.with_name("reference.bib")
        support.write_text("support sentinel", encoding="utf8")
        article = paging.inspect_articles(self.issue)[0]
        before = paging.article_fingerprint(article)
        archived.write_text("edited archive sentinel", encoding="utf8")
        self.assertEqual(paging.article_fingerprint(article), before)
        result = self.prepare({"A": [1]})
        self.assertFalse((result.articles[0].tex.parent / original.name).exists())
        self.assertEqual((result.articles[0].tex.parent / support.name).read_bytes(), support.read_bytes())

        link = source.with_name("shared.bib")
        try:
            link.hardlink_to(support)
        except OSError as exc:
            self.skipTest(f"filesystem does not support hardlinks: {exc}")
        with self.assertRaisesRegex(PagePlanError, "独占普通文件"):
            paging.article_fingerprint(article)

    def test_dependency_change_during_compile_aborts_staging(self):
        source = self.article("01 A", 1, 1, 1)
        support = source.with_name("reference.bib")
        support.write_text("initial contents", encoding="utf8")

        def compile_pdf(directory, stem, **kwargs):
            output = Path(directory) / f"{stem}.pdf"
            write_pdf(output, 1)
            support.write_text("concurrent edit", encoding="utf8")
            return True, str(output)

        with patch.object(paging, "compile_pdf", side_effect=compile_pdf):
            with self.assertRaisesRegex(PagePlanError, "隔离编译期间发生变化"):
                paging.prepare_articles(self.issue, self.stage)
        self.assertFalse(self.stage.exists())
        self.assertEqual(support.read_text(encoding="utf8"), "concurrent edit")

    def test_compile_rejects_outside_or_archived_literal_input_before_using_compiler(self):
        source = self.article("01 A", 1, 1, 1)
        for filename in ("../../outside.tex", "original/author.tex"):
            with self.subTest(filename=filename):
                source.write_bytes(tex_bytes() + rf"\input{{{filename}}}".encode())
                with patch.object(paging, "compile_pdf") as compiler:
                    with self.assertRaisesRegex(paging.PageCompileError, "拒绝期外或原稿目录"):
                        paging.prepare_articles(self.issue, self.stage)
                    compiler.assert_not_called()

    def test_automatic_subset_remeasures_remaining_articles_after_page_change(self):
        self.article("01 A", 1, 2, 2, pdf_count=2)
        self.article("02 B", 3, 5, 3, pdf_count=3)
        result = self.prepare({"A": [4, 4], "B": [2, 2]}, only=(2,))
        self.assertTrue(result.full_refresh)
        self.assertEqual(result.page_ranges, (PageRange("01 A", 1, 4, 4),
                                              PageRange("02 B", 5, 6, 2)))

    def test_staging_cannot_overlap_issue_and_does_not_follow_links(self):
        self.article("01 A", 1, 1, 1)
        with self.assertRaises(PagePlanError):
            paging.prepare_articles(self.issue, self.issue / "work")
        outside = self.root / "protected.txt"
        outside.write_bytes(b"outside dependency")
        (self.issue / "linked.tex").symlink_to(outside, target_is_directory=False)
        with self.assertRaisesRegex(PagePlanError, "linked.tex"):
            paging.prepare_articles(self.issue, self.stage)
        self.assertEqual(outside.read_bytes(), b"outside dependency")




class IssueDynamicLastPageTests(unittest.TestCase):
    """F2: only the two precise LastPage-derived page macros may be dynamic."""

    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.issue = self.root / "2026年第6期"
        self.issue.mkdir()
        self.stage = self.root / "stage"

    def write_tex(self, folder, *, first=1, last=None, count=None):
        if last is None:
            last = r"\pageref*{LastPage}"
        if count is None:
            count = r"\getpagerefnumber{LastPage}"
        sub = self.issue / folder
        sub.mkdir()
        source = sub / (folder.split(" ", 1)[1] + ".tex")
        crlf = chr(13) + chr(10)
        lines = [
            r"\documentclass{article}",
            rf"\newcommand{{\myfirstpage}}{{{first}}} % 页首",
            rf"\newcommand{{\mylastpage}}{{{last}}}",
            rf"\newcommand{{\mypages}}{{{count}}}",
            r"\begin{document}Synthetic text\end{document}",
        ]
        source.write_bytes(codecs.BOM_UTF8 + crlf.join(lines).encode() + crlf.encode())
        return source

    def test_mylastpage_pageref_star_lastpage_admitted(self):
        source = self.write_tex("01 A")
        articles = paging.inspect_articles(self.issue)
        self.assertEqual(len(articles), 1)
        self.assertTrue(articles[0].dynamic)
        self.assertEqual(articles[0].folder, "01 A")
        self.assertEqual((articles[0].first, articles[0].last, articles[0].count), (1, 1, 1))
        self.assertEqual(source.read_bytes()[:3], codecs.BOM_UTF8)
        self.assertIn(rb"\pageref*{LastPage}", source.read_bytes())

    def test_mypages_getpagerefnumber_lastpage_admitted(self):
        self.write_tex("01 A")
        articles = paging.inspect_articles(self.issue)
        self.assertEqual(len(articles), 1)
        self.assertTrue(articles[0].dynamic)
        self.assertGreater(articles[0].count, 0)

    def test_dynamic_forces_full_refresh(self):
        source = self.write_tex("01 A")
        write_pdf(source.with_suffix(".pdf"), 1)
        articles = paging.inspect_articles(self.issue)
        self.assertTrue(articles[0].dynamic)
        def compile_pdf(directory, stem, **kwargs):
            out = Path(directory) / (stem + ".pdf")
            write_pdf(out, 1)
            return True, str(out)
        with patch.object(paging, "compile_pdf", side_effect=compile_pdf) as compiler:
            result = paging.prepare_articles(self.issue, self.stage, only=(1,))
        self.assertTrue(result.full_refresh)
        self.assertTrue(compiler.called, "dynamic article must force a full compile")

    def test_integer_macros_unchanged(self):
        source = self.write_tex("01 A", last=10, count=10)
        original = source.read_bytes()
        write_pdf(source.with_suffix(".pdf"), 10)
        articles = paging.inspect_articles(self.issue)
        self.assertFalse(articles[0].dynamic)
        self.assertEqual((articles[0].first, articles[0].last, articles[0].count), (1, 10, 10))
        # Integer macros are not tagged dynamic; the F2 full-refresh override
        # is conditioned solely on the dynamic flag, so it must not apply.
        self.assertFalse(any(a.dynamic for a in articles))
        self.assertEqual(source.read_bytes(), original)

    def test_all_original_counterexamples_still_rejected(self):
        self.write_tex("01 A")
        source = self.issue / "01 A" / "A.tex"
        simple_last = rb"\newcommand{\mylastpage}{\pageref*{LastPage}}"
        simple_count = rb"\newcommand{\mypages}{\getpagerefnumber{LastPage}}"
        crlf = chr(13) + chr(10)
        cases = [
            b"",
            b"%" + simple_count,
            rb"\newcommand{\mypages}[1]{\getpagerefnumber{LastPage}}",
            simple_count + crlf.encode() + rb"\renewcommand{\mypages}{12}",
            simple_count + crlf.encode() + rb"\def\mypages{12}",
            rb"\iffalse " + simple_count + rb"\fi",
            rb"\iffalse \iftrue \fi " + simple_count,
            rb"\newcommand{\other}{" + simple_count + b"}",
            rb"\newcommand{\mylastpage}[1]{\pageref*{LastPage}}",
            simple_last + crlf.encode() + rb"\renewcommand{\mylastpage}{12}",
            rb"\newcommand{\other}{" + simple_last + b"}",
            rb"\newcommand{\mypages}{\pageref{LastPage}}",
            rb"\newcommand{\mylastpage}{\getpagerefnumber{LastPage}}",
            rb"\newcommand{\mypages}{\getpagerefnumber{Other}}",
            rb"\newcommand{\mylastpage}{\pageref*{Other}}",
            rb"\newcommand{\mypages}{\Getpagerefnumber{LastPage}}",
            rb"\newcommand{\mylastpage}{\Pageref*{LastPage}}",
            rb"\newcommand{\mypages}[1]{\getpagerefnumber{LastPage}}",
            rb"\newcommand{\mylastpage}[1]{\pageref*{LastPage}}",
            rb"\newcommand{\mylastpage}{\pageref*{LastPage}\relax}",
            rb"\newcommand{\mypages}{\getpagerefnumber{LastPage}\relax}",
        ]
        base = source.read_bytes()
        for replacement in cases:
            with self.subTest(replacement=replacement):
                mutated = base.replace(simple_count, replacement)
                if mutated == base:
                    mutated = base.replace(simple_last, replacement)
                self.assertNotEqual(mutated, base, "test setup must change the TeX")
                source.write_bytes(mutated)
                with self.assertRaises(PagePlanError):
                    paging.inspect_articles(self.issue)


if __name__ == "__main__":
    unittest.main(verbosity=2)
