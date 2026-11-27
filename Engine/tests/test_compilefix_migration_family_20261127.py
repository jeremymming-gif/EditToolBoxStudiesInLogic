"""Task 1: private typed subject & rule dispatch key (A+C generalized loop)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slcn.compilefix.files import digest
from slcn.compilefix.l1_diagnostics import diagnose, normalize_subject


def captured(data, log, step=1, code=1):
    return {"candidate_sha256": digest(data), "failure": ("a" * 32, step, code, log)}


class SubjectExtractionTests(unittest.TestCase):
    def test_environment_undefined_subject(self):
        data = b"\\documentclass{article}\n\\begin{document}\n\\begin{X}\n\\end{X}\n\\end{document}\n"
        log = b"! LaTeX Error: Environment X undefined.\nl.3 \\begin{X}\n"
        report = diagnose(data, captured(data, log))
        self.assertEqual(report.subject, ("environment", "X"))

    def test_undefined_control_sequence_direct_subject(self):
        data = b"\\documentclass{article}\n\\begin{document}\n\\foo\n\\end{document}\n"
        log = b"! Undefined control sequence.\nl.3 \\foo\n"
        report = diagnose(data, captured(data, log))
        self.assertEqual(report.subject, ("command", "foo"))

    def test_undefined_control_sequence_expansion_subject(self):
        data = b"\\documentclass{article}\n\\begin{document}\n\\foo\n\\end{document}\n"
        log = b"! Undefined control sequence.\n\\foo ->\\bar\nl.3 \\foo\n"
        report = diagnose(data, captured(data, log))
        self.assertEqual(report.subject, ("command", "bar"))

    def test_tikz_key_hardwrap_subject(self):
        data = (b"\\documentclass{article}\n\\usepackage{tikz}\n\\begin{document}\n"
                b"\\tikzset{modal/.style={}}\n\\end{document}\n")
        log = (b"! Package pgfkeys Error: I do not know the key '/tikz/mo\n"
               b"dal'. I am going to ignore it. Perhaps you misspelled it.\n"
               b"l.4 \\tikzset{modal/.style={}}\n")
        report = diagnose(data, captured(data, log))
        self.assertEqual(report.subject, ("tikz_key", "modal"))

    def test_no_subject_when_primary_subject_returns_none(self):
        data = b"\\begin{document}x\\end{document}\n"
        log = b"! Synthetic failure.\nl.1 x\n"
        report = diagnose(data, captured(data, log))
        self.assertIsNone(report.subject)


class NormalizeSubjectTests(unittest.TestCase):
    def test_leading_backslashes_stripped(self):
        self.assertEqual(normalize_subject("command", "\\\\foo"), ("command", "foo"))

    def test_tikz_prefix_stripped(self):
        self.assertEqual(normalize_subject("tikz_key", "/tikz/modal"), ("tikz_key", "modal"))

    def test_hardwrap_whitespace_collapsed(self):
        self.assertEqual(normalize_subject("tikz_key", "/tikz/mod\n  al"), ("tikz_key", "modal"))

    def test_line_drift_does_not_change_normalized_subject(self):
        for name in ("1env", "foo:bar"):
            subjects = []
            for pad in (0, 2):
                data = ("\\documentclass{article}\n\\begin{document}\n"
                        + "\n" * pad + "\\begin{" + name + "}\n\\end{" + name + "}\n"
                        + "\\end{document}\n").encode()
                line = 3 + pad
                log = ("! LaTeX Error: Environment " + name + " undefined.\n"
                       "l." + str(line) + " \\begin{" + name + "}\n").encode()
                report = diagnose(data, captured(data, log))
                subjects.append(normalize_subject(*report.subject))
            self.assertEqual(subjects[0], subjects[1])
            self.assertEqual(subjects[0], ("environment", name))

    def test_different_subjects_not_equal(self):
        self.assertNotEqual(normalize_subject("environment", "1env"),
                            normalize_subject("environment", "foo:bar"))
        self.assertNotEqual(normalize_subject("environment", "X"),
                            normalize_subject("command", "X"))


class PrivacyTests(unittest.TestCase):
    def test_shareable_has_no_subject_key(self):
        data = b"\\documentclass{article}\n\\begin{document}\n\\foo\n\\end{document}\n"
        log = b"! Undefined control sequence.\nl.3 \\foo\n"
        report = diagnose(data, captured(data, log))
        self.assertEqual(report.subject, ("command", "foo"))
        shared = report.shareable()
        self.assertNotIn("subject", shared)
        for key in ("identity", "candidate_sha256", "phase", "returncode", "diagnostics", "attempts"):
            self.assertIn(key, shared)


# ── Task 2: macro-migration-v1 (macro missing → source locate → reversible insert) ──
import shutil
import tempfile

from slcn.compilefix.migration_rule_family import build_macro_context, select_macro_plan


_MACRO_DECL = "\\newcommand{\\zzfoo}{macro-value}"
_MACRO_NAME = "zzfoo"
_CANDIDATE = (
    "\\documentclass{article}\n"
    "\\usepackage{hyperref}\n"
    "\\begin{document}\n"
    "\\zzfoo\n"
    "\\end{document}\n"
).encode()
_MACRO_LOG = b"! Undefined control sequence.\nl.4 \\zzfoo\nThe control sequence at the end of the top line\n"



def _macro_diagnosis(candidate=_CANDIDATE, log=_MACRO_LOG):
    report = diagnose(candidate, captured(candidate, log))
    assert report.subject == ("command", _MACRO_NAME), report.subject
    return report


class _MacroFixtureCase(unittest.TestCase):
    """Real source-bound context: source preamble, local support, style, candidate."""

    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slcn-macro-migration-")
        self.root = Path(self.tmp)
        self.src = self.root / "in"
        self.out = self.root / "out"
        self.src.mkdir()
        self.out.mkdir()
        self.source = self.src / "main.tex"
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write_source(self, definition, pre="\\usepackage{helpers}\n", body="source body text\n"):
        # The source supplies the definition; the macro USE lives only in the
        # converted candidate body (the failure being healed), not in the
        # source body — so retained_body_text must not reference the macro.
        self.source.write_text(
            "\\documentclass{article}\n" + pre + definition + "\\begin{document}\n"
            + body + "\\end{document}\n"
        )
        (self.src / "helpers.sty").write_text(definition + "\n")

    def _context(self):
        return build_macro_context(self.out, "main", self.source, None)

    def _plan(self, candidate=_CANDIDATE, definition=_MACRO_DECL + "\n"):
        self._write_source(definition)
        return select_macro_plan(
            candidate, _macro_diagnosis(candidate), context=self._context()
        )


class MacroMigrationPositiveTests(_MacroFixtureCase):
    def test_single_newcommand_yields_plan_with_one_declaration_and_byte_exact_reverse(self):
        plan = self._plan()
        self.assertIsNotNone(plan)
        self.assertEqual(list(plan.lines), [_MACRO_DECL])
        restored = plan.reverse(plan.patched)
        self.assertEqual(restored, plan.original)
        self.assertEqual(restored, _CANDIDATE)
        self.assertEqual(plan.patched.replace((_MACRO_DECL + "\n").encode(), b"", 1),
                         _CANDIDATE)

    def test_providecommand_is_accepted(self):
        plan = self._plan(definition="\\providecommand{\\zzfoo}{macro-value}\n")
        self.assertIsNotNone(plan)
        self.assertEqual(list(plan.lines), ["\\providecommand{\\zzfoo}{macro-value}"])
        self.assertEqual(plan.reverse(plan.patched), _CANDIDATE)

    def test_plan_offset_is_inside_the_candidate_preamble(self):
        plan = self._plan()
        self.assertIsNotNone(plan)
        preamble_bytes = len(_CANDIDATE[:_CANDIDATE.index(b"\\begin{document}")])
        self.assertLessEqual(plan.offset, preamble_bytes)
        self.assertEqual(plan.reverse(plan.patched), _CANDIDATE)


class MacroMigrationNegativeTests(_MacroFixtureCase):
    def assert_no_plan(self, definition, *, candidate=_CANDIDATE, log=_MACRO_LOG):
        self._write_source(definition)
        plan = select_macro_plan(
            candidate, _macro_diagnosis(candidate, log), context=self._context()
        )
        self.assertIsNone(plan)

    def test_def_form_is_rejected(self):
        self.assert_no_plan("\\def\\zzfoo{macro-value}\n")

    def test_let_form_is_rejected(self):
        self.assert_no_plan("\\let\\zzfoo\\relax\n")

    def test_newif_form_is_rejected(self):
        self.assert_no_plan("\\newif\\ifzzfoo\n")

    def test_renewcommand_form_is_rejected(self):
        self.assert_no_plan("\\renewcommand{\\zzfoo}{macro-value}\n")

    def test_two_same_name_definitions_in_source_are_rejected(self):
        self.assert_no_plan(_MACRO_DECL + "\n" + _MACRO_DECL + "\n")

    def test_definition_inside_conditional_is_rejected(self):
        self.assert_no_plan("\\iftrue\n" + _MACRO_DECL + "\n\\fi\n")

    def test_template_already_provides_macro_is_rejected(self):
        # Template-side provider = a bundled style in the output directory. Reuse
        # `build_context` for the real binding, then inject the same declaration
        # into that bound style text: the rebuilt selection must see it as an
        # existing name and skip the macro.
        self._write_source(_MACRO_DECL + "\n")
        context = self._context()
        object.__setattr__(context, "style_text", _MACRO_DECL + "\n")
        self.assertIsNone(select_macro_plan(
            _CANDIDATE, _macro_diagnosis(), context=context
        ))

    def test_candidate_already_defines_macro_is_rejected(self):
        # Add the candidate's own definition at the end (after the use), so the
        # bound subject still resolves against line 4 while the rebuild proof
        # must reject the macro as already defined in the candidate.
        candidate = _CANDIDATE.replace(
            b"\\end{document}",
            (_MACRO_DECL + "\n\\end{document}").encode(),
        )
        self.assert_no_plan(_MACRO_DECL + "\n", candidate=candidate)

    def test_wrong_subject_kind_is_rejected(self):
        self._write_source(_MACRO_DECL + "\n")
        report = _macro_diagnosis()
        object.__setattr__(report, "subject", ("environment", _MACRO_NAME))
        self.assertIsNone(select_macro_plan(
            _CANDIDATE, report, context=self._context()
        ))

    def test_body_not_using_macro_is_rejected(self):
        # The candidate still uses \zzfoo (so the failure binds), but the
        # retained source body ALSO uses it: the first fresh selector run must
        # select the macro, proving "body_unused" is false and the plan must
        # be rejected. This exercises the genuine rebuild proof, not a string
        # check or a missing-subject shortcut.
        self._write_source(_MACRO_DECL + "\n", body="\\zzfoo\n")
        report = _macro_diagnosis()
        plan = select_macro_plan(
            _CANDIDATE, report, context=self._context()
        )
        self.assertIsNone(plan)


if __name__ == "__main__":
    unittest.main()
