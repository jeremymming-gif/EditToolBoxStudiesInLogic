"""L1 six-category synthetic pairs: report-only paths never edit input bytes."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slcn.compilefix.files import digest, json_bytes
from slcn.compilefix.l1_diagnostics import diagnose
from slcn.compilefix.l1_rules import CATCODE, select_plan

POSITIVE = b"\\documentclass{article}\n\\newcommand{\\Demo}{\\@gobble{discard}Kept}\n\\begin{document}\n\\Demo\n\\end{document}\n"


def captured(data, log, step=1, code=1):
    return {"candidate_sha256": digest(data), "failure": ("a" * 32, step, code, log)}


class DiagnosticsTests(unittest.TestCase):
    def pair(self, category, positive, negative, log=b"! Synthetic failure.\nl.1 x\n", **kwargs):
        for expected, raw in ((True, positive), (False, negative)):
            before = digest(raw)
            report = diagnose(raw, captured(raw, log), **kwargs)
            self.assertEqual(any(d.category == category for d in report.items), expected)
            if category != "catcode":
                self.assertIsNone(select_plan(raw, report))
            self.assertEqual(digest(raw), before)
            self.assertTrue(report.identity)

    def test_stale_positive_negative(self):
        data = b"\\begin{document}x\\end{document}\n"
        report = diagnose(data, captured(data, b"(./demo.bbl\n! Misplaced alignment tab character &.\nl.4 &\n"))
        self.assertIn("stale_intermediate", [d.category for d in report.items])
        for ext in ("bib", "tex", "pdf"):
            other = diagnose(data, captured(data, f"(./demo.{ext}\n! Misplaced alignment tab character &.\nl.4 &\n".encode()))
            self.assertNotIn("stale_intermediate", [d.category for d in other.items])
            self.assertIsNone(select_plan(data, other))
        self.assertEqual(digest(data), report.candidate_sha256)

    def test_catcode_positive_negative(self):
        self.pair("catcode", POSITIVE, POSITIVE.replace(b"\\@gobble", b"\\@"),
                  b"! You can't use `\\spacefactor' in vertical mode.\n\\Demo ->\\@\n             gobble{discard}Kept\nl.4 \\Demo\n")

    def test_path_positive_negative(self):
        self.pair("dependency_path", b"\\input{missing}\n", b"% \\input{missing}\n")
        for raw in (b"\\input{a/main}\n\\input{b/main}\n", b"\\includegraphics{\\macro}\n",
                    b"\\graphicspath{{figures/}}\\includegraphics{figure}\n", b"\\input{/private/secret}\n",
                    b"\\iftrue\\input{maybe}\\fi\n"):
            report = diagnose(raw, captured(raw, b"! File not found.\nl.1 x\n"))
            self.assertIsNone(select_plan(raw, report))
            self.assertEqual(report.candidate_sha256, digest(raw))

    def test_template_positive_negative(self):
        data = b"\\begin{document}short\\end{document}\n"
        fail = diagnose(data, captured(data, b"! Synthetic failure.\n"),
                        template_results=({"rule": "T2", "status": "FAIL", "lines": [1]},))
        self.assertIn("templatecheck", [d.category for d in fail.items])
        for status in ("PASS", "INFO", "SKIP"):
            report = diagnose(data, captured(data, b"! Synthetic failure.\n"),
                              template_results=({"rule": "T3", "status": status, "lines": []},))
            self.assertNotIn("templatecheck", [d.category for d in report.items])
            self.assertIsNone(select_plan(data, report))
        self.assertEqual(digest(data), fail.candidate_sha256)

    def test_duplicate_positive_negative(self):
        self.pair("duplicate_label", b"\\label{x}\n\\label{x}\n\\ref{x}\n",
                  b"\\label{x}\n% \\label{x}\n\\ref{x}\n")
        raw = b"{\\label{x}}\n{\\label{x}}\n"
        self.assertIsNone(select_plan(raw, diagnose(raw, captured(raw, b"! Unknown.\n"))))

    def test_package_positive_negative(self):
        data = b"\\usepackage{alpha}\n"
        before = digest(data)
        for expected, log in ((True, b"! LaTeX Error: Option clash for package alpha.\nl.1 x\n"),
                              (False, b"Package alpha loaded successfully.\n")):
            report = diagnose(data, captured(data, log))
            self.assertEqual(any(d.category == "package_conflict" for d in report.items), expected)
            self.assertIsNone(select_plan(data, report))
            self.assertEqual(before, digest(data))

    def test_minimal_export_drops_all_raw_content(self):
        log = b"! LaTeX Error: Option clash for package SECRET_MANUSCRIPT.\nl.1 /Users/person/private sk-synthetic-secret123 api_key=secret\n"
        data = b"\\author{SECRET_MANUSCRIPT}\\input{/Users/person/private}\n"
        report = diagnose(data, captured(data, log))
        raw = json_bytes(report.shareable())
        for private in (b"SECRET_MANUSCRIPT", b"/Users", b"person", b"sk-", b"api_key", data, log):
            self.assertNotIn(private, raw)
        self.assertEqual(report.identity, diagnose(data, captured(data, log)).identity)

    def test_unknown_missing_capture_biber_and_mismatch_never_write(self):
        for capture in (None, {}, captured(POSITIVE, b""),
                        captured(b"other", b"! Undefined control sequence.\nl.2 x\n"),
                        captured(POSITIVE, b"! Undefined control sequence.\nl.2 x\n", step=2)):
            self.assertIsNone(select_plan(POSITIVE, diagnose(POSITIVE, capture)))

    def test_relative_path_presence_missing_and_ambiguity_are_structured(self):
        raw = b"\\includegraphics{figure}\n"
        for files, anchor in ((set(), "relative-path-missing"), ({"figure.pdf"}, "relative-path-present"),
                              ({"figure.pdf", "figure.png"}, "relative-path-ambiguous")):
            report = diagnose(raw, captured(raw, b"! File not found.\n"), available_paths=files)
            self.assertEqual(report.items[0].anchor, anchor)
            self.assertIsNone(select_plan(raw, report))
            self.assertEqual(report.candidate_sha256, digest(raw))

    def test_template_report_never_probes_nested_external_or_macro_paths(self):
        import tempfile
        from unittest.mock import patch
        import templatecheck as tc
        from slcn.compilefix.l1 import template_results
        with tempfile.TemporaryDirectory() as temp:
            for target in ("{/private/secret}", "/private/secret", "../outside", r"\\macro"):
                raw = (r"\begin{document}\includegraphics{" + target + r"}\addbibresource{" + target + r"}\end{document}").encode()
                with patch.object(tc, "check_images", side_effect=AssertionError("unsafe image probe")), \
                     patch.object(tc, "check_references", side_effect=AssertionError("unsafe bib probe")):
                    results = template_results(raw, Path(temp), "demo")
                self.assertEqual(len(results), 8)
                self.assertEqual(results[3]["status"], "SKIP")
                self.assertEqual(results[7]["status"], "SKIP")

    def test_registry_is_single_auditable_rule(self):
        self.assertEqual(CATCODE.rule_id, "catcode-top-level-at-v1")
        self.assertEqual(CATCODE.max_edits, 2)
        for key in ("precondition", "allowed_region", "invariants", "rollback", "positive", "negative", "test_script"):
            self.assertTrue(getattr(CATCODE, key))


if __name__ == "__main__":
    unittest.main()
