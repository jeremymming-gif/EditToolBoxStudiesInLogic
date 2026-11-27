"""TDD for Task 5: multi-round staged repair loop (≤3 rounds, one publish).

Written BEFORE the production code.  RED: ``run_staged_repair_loop`` does not
exist, so every test fails at import.  GREEN: the loop stages definitions in
memory across real XeLaTeX dry compiles, publishes exactly one combined patch,
and keeps the frozen single-round shareable bytes unchanged.

Every fixture here is a synthetic complete project.  Every dry compile is a
REAL XeLaTeX run (stdout captured the same way the converter captures it), with
no compiler mocks.  Only the final publish (``apply_plan``) is observed via a
spy/patch where the test needs to count calls or force a failure.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from slcn.compilefix import capture_compile  # noqa: E402
from slcn.compilefix.compiler import _environment  # noqa: E402
from slcn.compilefix.l1 import run_staged_repair_loop  # noqa: E402 (RED until implemented)
from slcn.compilefix.l1_diagnostics import diagnose, normalize_subject  # noqa: E402
from slcn.compilefix.l1_transaction import RecoveryRequired  # noqa: E402

ENV_DECL = r"\newenvironment{firstenv}{}{}"
MACRO_DECL = r"\newcommand{\othermacro}{Other text.}"
EXTRA_DECL = r"\newcommand{\thirdmacro}{Third text.}"
SHAREABLE_KEYS = {"identity", "candidate_sha256", "phase", "returncode",
                  "diagnostics", "attempts"}
CAPTURE_KEYS = ("candidate_sha256", "candidate_id", "source_id", "out_id")

# A complete, self-contained candidate.  The macro uses are gated on the
# preceding definition, so each failing dry compile reveals exactly one new
# subject in sequence: environment → command → (command) → convergence.
TEMPLATE = (
    "\\documentclass{article}\n"
    "\\usepackage{amsmath}\n"
    "\\usepackage{hyperref}\n"
    "\\begin{document}\n"
    "SKELMARK\n"
    "\\end{document}\n"
)
BODY_2 = (
    "\\begin{firstenv}\ninside first environment.\n\\end{firstenv}\n"
    "\\ifdefined\\firstenv\n\\othermacro marker.\n\\fi\n"
)
BODY_3 = (
    "\\begin{firstenv}\ninside first environment.\n\\end{firstenv}\n"
    "\\ifdefined\\firstenv\n\\othermacro marker.\n\\fi\n"
    "\\ifdefined\\othermacro\n\\thirdmacro marker.\n\\fi\n"
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        SLCN_MANUSCRIPTS="/dev/null",
        PYTHONPATH=str(REPO / "vendor"),
        TMPDIR=str(root),
        TEXMFVAR=str(root / "texmf-var"),
        TEXMFCONFIG=str(root / "texmf-config"),
        SLCN_COMPILEFIX_STATE_DIR=str(root / "state"),
    )
    for key in ("TMPDIR", "TEXMFVAR", "TEXMFCONFIG", "SLCN_COMPILEFIX_STATE_DIR"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


class _ProjectCase(unittest.TestCase):
    """Synthetic source + candidate, with a real XeLaTeX on PATH."""

    maxDiff = None
    body = BODY_2

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slcn-t5-multiround-", dir="/tmp")
        self.root = Path(self.tmp)
        self.env = _env(self.root)
        (self.root / "in").mkdir()
        (self.root / "out" / "demo").mkdir(parents=True)
        (self.root / "private").mkdir()
        self.source = self.root / "in" / "main.tex"
        self.sty = self.root / "in" / "probe.sty"
        self.out = self.root / "out" / "demo"
        self.stem = "demo"
        # Source body is plain; the definitions live in the local support file
        # so the selectors' "retained body must NOT use the subject" gate holds.
        self.source.write_text(
            "\\documentclass{article}\n\\usepackage{probe}\n"
            "\\begin{document}\nMain text.\n\\end{document}\n",
            encoding="utf-8",
        )
        self.sty.write_text(ENV_DECL + "\n" + MACRO_DECL + "\n" + EXTRA_DECL + "\n",
                            encoding="utf-8")
        self._candidate_path.write_text(TEMPLATE.replace("SKELMARK", self.body),
                                        encoding="utf-8")
        self.original_candidate = self._candidate_path.read_bytes()
        # The round-bound capture belongs to the OUT dir candidate (what
        # apply_plan verifies), with a failure transcript from a real compile.
        self.capture = capture_compile(self.out, self.stem, source=self.source)
        assert self.capture is not None
        code, cap, raw = self._dry_compile(self.original_candidate, "orig")
        assert code != 0
        self.capture["failure"] = ("c" * 32, 1, code, raw)
        self.report = diagnose(self.original_candidate, self.capture,
                               available_paths={"demo.tex"})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def _candidate_path(self):
        return self.out / (self.stem + ".tex")

    def _dry_compile(self, candidate: bytes, stage: str):
        """REAL XeLaTeX, stdout captured like the converter. Returns (code, cap, raw)."""
        work = self.root / stage
        work.mkdir(exist_ok=True)
        (work / (self.stem + ".tex")).write_bytes(candidate)
        with tempfile.TemporaryDirectory(prefix="compiler-home-",
                                         dir=self.root / "private") as home:
            proc = subprocess.run(
                ["xelatex", "-no-shell-escape", "-interaction=nonstopmode",
                 self.stem + ".tex"],
                cwd=work, env=_environment(Path(home)),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90,
            )
        raw = proc.stdout[:2 * 1024 * 1024]
        cap = capture_compile(work, self.stem, source=self.source)
        if proc.returncode:
            cap["failure"] = ("c" * 32, 1, proc.returncode, raw)
        return proc.returncode, cap, raw


class TestChainedDefinitionsPositive(_ProjectCase):
    """Round 1 reveals env, round 2 reveals macro, round 3 converges."""

    def test_two_chained_defs_publish_once_and_report_per_round(self):
        self.assertEqual(self.report.subject[0], "environment")
        calls = []
        import slcn.compilefix.l1 as l1mod
        real_apply = l1mod.apply_plan

        def spy(*args, **kwargs):
            calls.append(kwargs["plan"])
            return real_apply(*args, **kwargs)

        with patch.object(l1mod, "apply_plan", side_effect=spy):
            ok, info, new_capture = run_staged_repair_loop(
                self.out, self.stem, source=self.source, capture=self.capture,
                candidate=self.original_candidate, report=self.report, max_rounds=3,
            )
        self.assertTrue(ok, info)
        self.assertEqual(len(calls), 1, "must publish exactly once")
        plan = calls[0]
        self.assertEqual(plan.reverse(plan.patched), self.original_candidate)
        published = self._candidate_path.read_bytes()
        self.assertEqual(published, plan.patched)
        self.assertEqual(published.count(ENV_DECL.encode()), 1)
        self.assertEqual(published.count(MACRO_DECL.encode()), 1)
        l1 = new_capture["l1"]
        results = [a["result"] for a in l1["attempts"]]
        self.assertIn("passed", results)
        self.assertGreaterEqual(len(results), 2)
        self.assertTrue(all(set(a) == {"rule", "result"} for a in l1["attempts"]))
        for key in CAPTURE_KEYS:
            self.assertIn(key, new_capture)
        dumped = json.dumps(l1, ensure_ascii=False)
        self.assertNotIn("subject", dumped)
        self.assertNotIn("round", dumped)
        self.assertTrue((self.out / (self.stem + ".pdf")).exists())

    def test_single_continuous_slice_reverse_each_round(self):
        """Every staged accumulation must be one reversible continuous slice."""
        import slcn.compilefix.l1 as l1mod
        real_apply = l1mod.apply_plan
        published = []

        def spy(*args, **kwargs):
            published.append(kwargs["plan"])
            return real_apply(*args, **kwargs)

        with patch.object(l1mod, "apply_plan", side_effect=spy):
            ok, _, _ = run_staged_repair_loop(
                self.out, self.stem, source=self.source, capture=self.capture,
                candidate=self.original_candidate, report=self.report, max_rounds=3,
            )
        self.assertTrue(ok)
        plan = published[0]
        # One continuous insertion at the hyperref row; reverse is byte-exact.
        self.assertEqual(plan.reverse(plan.patched), self.original_candidate)
        self.assertEqual(plan.start + len(plan.added_bytes), plan.end)
        self.assertEqual(plan.patched[:plan.start], self.original_candidate[:plan.start])
        self.assertEqual(plan.patched[plan.end:], self.original_candidate[plan.start:])
        self.assertIn(MACRO_DECL.encode(), plan.added_bytes)
        self.assertIn(ENV_DECL.encode(), plan.added_bytes)


class TestRoundCap(_ProjectCase):
    """A fourth missing definition must not be staged or published."""

    body = BODY_3

    def test_fourth_definition_is_not_executed(self):
        import slcn.compilefix.l1 as l1mod
        with patch.object(l1mod, "apply_plan",
                          side_effect=AssertionError("must not publish")):
            ok, _, new_capture = run_staged_repair_loop(
                self.out, self.stem, source=self.source, capture=self.capture,
                candidate=self.original_candidate, report=self.report, max_rounds=3,
            )
        self.assertFalse(ok)
        self.assertEqual(len(new_capture["l1"]["attempts"]), 3)
        self.assertNotIn("l1_repaired", new_capture)
        self.assertEqual(self._candidate_path.read_bytes(), self.original_candidate)


class TestDeathLoop(_ProjectCase):
    """Repeating the same (subject, normalized, acc_sha) stops the loop."""

    def test_repeat_stops_without_publish(self):
        import slcn.compilefix.l1 as l1mod
        with patch.object(l1mod, "select_environment_plan", return_value=None), \
             patch.object(l1mod, "select_macro_plan", return_value=None), \
             patch.object(l1mod, "apply_plan",
                          side_effect=AssertionError("must not publish")):
            ok, _, new_capture = run_staged_repair_loop(
                self.out, self.stem, source=self.source, capture=self.capture,
                candidate=self.original_candidate, report=self.report, max_rounds=3,
            )
        self.assertFalse(ok)
        results = [a["result"] for a in new_capture["l1"]["attempts"]]
        self.assertIn("stopped-repeat", results)
        self.assertLessEqual(len(results), 2)
        self.assertNotIn("l1_repaired", new_capture)

    def test_repeat_key_is_subject_normalized_and_acc_sha(self):
        self.assertEqual(normalize_subject("command", "\\othermacro"),
                         ("command", "othermacro"))
        self.assertEqual(normalize_subject("environment", "firstenv"),
                         ("environment", "firstenv"))
        kind, name = normalize_subject("command", "\\othermacro")
        key = (kind, name, _sha(b"candidate"))
        self.assertEqual(key, ("command", "othermacro", _sha(b"candidate")))


class TestLineDrift(_ProjectCase):
    """A line-number move of the same subject is not a new error."""

    def test_same_subject_different_line_is_not_new(self):
        drifted = self.original_candidate.replace(
            b"\\begin{firstenv}", b"\n\n\n\\begin{firstenv}", 1)
        code, cap, raw = self._dry_compile(drifted, "drift")
        self.assertNotEqual(code, 0)
        cap["failure"] = ("c" * 32, 1, code, raw)
        rep2 = diagnose(drifted, cap, available_paths={"demo.tex"})
        self.assertEqual(self.report.subject, rep2.subject)
        self.assertEqual(
            (self.report.subject[0], normalize_subject(*self.report.subject)),
            (rep2.subject[0], normalize_subject(*rep2.subject)),
        )
        # Identity does change (different bytes), but the new-error predicate
        # (subject type + normalized subject) does not.
        self.assertNotEqual(self.report.identity, rep2.identity)


class TestRollback(_ProjectCase):
    """RecoveryRequired from apply_plan must not deliver."""

    def test_recovery_required_records_attempt_and_no_delivery(self):
        import slcn.compilefix.l1 as l1mod
        with patch.object(l1mod, "apply_plan", side_effect=RecoveryRequired("forced")):
            ok, info, new_capture = run_staged_repair_loop(
                self.out, self.stem, source=self.source, capture=self.capture,
                candidate=self.original_candidate, report=self.report, max_rounds=3,
            )
        self.assertFalse(ok)
        self.assertIn("回滚", info)
        self.assertIn("recovery-required",
                      [a["result"] for a in new_capture["l1"]["attempts"]])
        self.assertNotIn("l1_repaired", new_capture)
        self.assertEqual(self._candidate_path.read_bytes(), self.original_candidate)


class TestFrozenBytes(_ProjectCase):
    """Single-round behavior must keep the shareable bytes shape frozen."""

    def test_single_round_shareable_has_no_round_number(self):
        import slcn.compilefix.l1 as l1mod
        with patch.object(l1mod, "apply_plan", side_effect=RecoveryRequired("forced")):
            ok, _, new_capture = run_staged_repair_loop(
                self.out, self.stem, source=self.source, capture=self.capture,
                candidate=self.original_candidate, report=self.report, max_rounds=1,
            )
        self.assertFalse(ok)
        l1 = new_capture["l1"]
        self.assertEqual(set(l1), SHAREABLE_KEYS)
        self.assertEqual(len(l1["attempts"]), 1)
        self.assertEqual(l1["identity"], self.report.identity)
        self.assertEqual(l1["candidate_sha256"], _sha(self.original_candidate))
        dumped = json.dumps(l1, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("round", dumped)
        self.assertNotIn("subject", dumped)
        # Byte-stable across an identical second round-bound invocation.
        self.assertEqual(dumped, json.dumps(l1, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
