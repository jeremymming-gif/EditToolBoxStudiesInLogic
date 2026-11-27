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


if __name__ == "__main__":
    unittest.main()
