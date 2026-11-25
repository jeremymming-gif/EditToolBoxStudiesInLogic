"""Exact insertion, scope and closest-counterexample gates for the sole L1 rule."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slcn.compilefix.l1_diagnostics import diagnose
from slcn.compilefix.l1_rules import select_plan
from slcn.compilefix.files import digest, RepairError

POSITIVE = b"\\documentclass{article}\n\\newcommand{\\Demo}{\\@gobble{discard}Kept}\n\\begin{document}\n\\Demo\n\\end{document}\n"
LOG = b"! You can't use `\\spacefactor' in vertical mode.\n\\Demo ->\\@\n             gobble{discard}Kept\nl.4 \\Demo\n"


def plan_for(raw, log=LOG):
    capture = {"candidate_sha256": digest(raw), "failure": ("b" * 32, 1, 1, log)}
    return select_plan(raw, diagnose(raw, capture))


class RuleTests(unittest.TestCase):
    def test_positive_exact_two_spans_and_inverse(self):
        plan = plan_for(POSITIVE)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.reverse(plan.patched), POSITIVE)
        self.assertEqual(plan.original[plan.start:plan.end], b"\\newcommand{\\Demo}{\\@gobble{discard}Kept}")
        self.assertEqual(plan.patched, POSITIVE[:plan.start] + plan.prefix + POSITIVE[plan.start:plan.end]
                         + plan.suffix + POSITIVE[plan.end:])
        self.assertIsNone(plan_for(plan.patched))

    def test_closest_counterexamples_never_edit(self):
        macro = b"\\newcommand{\\Demo}{\\@gobble{discard}Kept}"
        negatives = {
            "comment": POSITIVE.replace(macro, b"% " + macro),
            "literal-at": POSITIVE.replace(b"\\@gobble", b"\\@"),
            "escaped-backslash": POSITIVE.replace(b"\\@gobble", b"\\\\@gobble"),
            "already-wrapped": POSITIVE.replace(macro, b"\\makeatletter\n" + macro + b"\n\\makeatother"),
            "local-group": POSITIVE.replace(macro, b"{" + macro + b"}"),
            "local-environment": POSITIVE.replace(macro, b"\\begin{group}\n" + macro + b"\n\\end{group}"),
            "body-definition": POSITIVE.replace(macro + b"\n", b"").replace(b"\\Demo\n", macro + b"\n\\Demo\n"),
            "conditional": POSITIVE.replace(macro, b"\\iftrue " + macro + b"\\fi"),
            "scope-open": POSITIVE.replace(macro, b"\\makeatletter\n" + macro),
            "scope-unmatched": POSITIVE.replace(macro, b"\\makeatother\n" + macro),
            "unknown-body-command": POSITIVE.replace(b"Kept}", b"\\catcode`x=1 Kept}"),
            "unknown-preamble": POSITIVE.replace(macro, b"\\mystery\n" + macro),
            "multiple-matches": POSITIVE.replace(macro, macro + b"\n" + macro.replace(b"Demo", b"Other")),
            "unfinished": POSITIVE.replace(b"discard}Kept}", b"discard}Kept"),
            "caret-encoding": POSITIVE.replace(b"Kept", b"^^61"),
            "unknown-class": POSITIVE.replace(b"article", b"custom"),
            "definition-container": POSITIVE.replace(macro, b"\\newenvironment{x}{" + macro + b"}{}"),
        }
        for label, raw in negatives.items():
            with self.subTest(label=label):
                before = digest(raw)
                self.assertIsNone(plan_for(raw))
                self.assertEqual(before, digest(raw))

    def test_log_anchor_must_be_bound_to_exact_candidate_use(self):
        for log in (LOG.replace(b"l.4", b"l.3"), LOG.replace(b"\\Demo\n", b"\\Other\n"),
                    LOG.replace(b"l.4 \\Demo", b"l.400 \\Demo"),
                    b"! Undefined control sequence.\n", LOG + b"! Other failure.\nl.4 \\Demo\n"):
            self.assertIsNone(plan_for(POSITIVE, log))

    def test_utf8_crlf_byte_offsets(self):
        raw = (b"% " + "匿名合成".encode() + b"\n" + POSITIVE).replace(b"\n", b"\r\n")
        log = LOG.replace(b"l.4", b"l.5")
        plan = plan_for(raw, log)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.reverse(plan.patched), raw)
        self.assertIn(b"\\makeatletter\r\n", plan.patched)
        with self.assertRaises(RepairError):
            plan.reverse(plan.patched + b"x")


if __name__ == "__main__":
    unittest.main()
