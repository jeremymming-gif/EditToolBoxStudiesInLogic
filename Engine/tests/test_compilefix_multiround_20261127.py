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
    candidate_override = None

    def setUp(self):
        if shutil.which("xelatex") is None:
            self.skipTest("BLOCKED: XeLaTeX is unavailable")
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
        if self.candidate_override is not None:
            self._candidate_path.write_bytes(self.candidate_override)
        else:
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
        # Call the PRODUCTION helper, not a hand-built tautology: the key must be
        # FLAT (kind, normalized name, acc digest) per spec §11 decision 1.
        from slcn.compilefix.l1 import _repeat_key

        self.assertEqual(
            _repeat_key(("command", "\\othermacro"), b"candidate"),
            ("command", "othermacro", _sha(b"candidate")),
        )
        # Line-drift / backslash form: different subject spelling normalizes to
        # the same flat key.
        self.assertEqual(
            _repeat_key(("command", "othermacro"), b"candidate"),
            _repeat_key(("command", "\\othermacro"), b"candidate"),
        )
        # Digests of different accumulated candidates differ.
        self.assertNotEqual(
            _repeat_key(("command", "\\othermacro"), b"candidate"),
            _repeat_key(("command", "\\othermacro"), b"other candidate"),
        )
        self.assertIsNone(_repeat_key(None, b"candidate"))


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


class TestCatcodeDirectPublish(_ProjectCase):
    """A CATCODE EditPlan is a two-wrapper span edit, not a preamble line.

    It cannot be folded into the single-continuous-slice combined plan, so the
    loop must publish it directly with exactly one apply_plan call. Existing
    fixtures carry amsmath/hyperref (so _definitions() admits nothing); this
    fixture is a pure article with one @-control-word \newcommand so the
    CATCODE rule fires.
    """

    def setUp(self):
        # Pure article preamble: no packages, one @-control-word definition.
        self.candidate_override = (
            "\\documentclass{article}\n"
            "\\newcommand{\\Demo}{\\@gobble{discard}Kept}\n"
            "\\begin{document}\n"
            "\\Demo\n"
            "\\end{document}\n"
        ).encode()
        super().setUp()
        self.assertEqual([(i.category, i.confidence) for i in self.report.items],
                         [("catcode", "exact")])

    def test_catcode_plan_is_published_once_with_reversible_patch(self):
        from slcn.compilefix.l1_rules import select_plan
        plan = select_plan(self.original_candidate, self.report)
        self.assertIsNotNone(plan, "fixture must actually trigger the CATCODE rule")
        self.assertEqual(plan.reverse(plan.patched), self.original_candidate)

        import slcn.compilefix.l1 as l1mod
        calls = []
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
        self.assertEqual(len(calls), 1, "CATCODE plan must publish exactly once")
        published_plan = calls[0]
        self.assertEqual(published_plan.reverse(published_plan.patched),
                         self.original_candidate)
        self.assertEqual(self._candidate_path.read_bytes(), published_plan.patched)
        self.assertIn("l1_repaired", new_capture)
        results = [a["result"] for a in new_capture["l1"]["attempts"]]
        self.assertIn("passed", results)

    def test_catcode_rollback_records_attempt_and_no_delivery(self):
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


# ── Task 6 Step 1: end-to-end rc=2 → rc=0 evidence ───────────────────────────
#
# Each test below is a synthetic COMPLETE TeX project compiled through the real
# converter chain ``convert_compile.compile_pdf`` (xelatex → biber → xelatex ×2,
# no compiler mocks).  ``compile_pdf`` returns ``(False, msg)`` on a failing
# real xelatex step; convert.py's contract turns that into process rc=2
# ("转换已完成，但 ⑨ 编译未通过（退出码 2）").  We assert the failing real step's
# returncode directly via ``collecting_results`` and map it to the documented
# rc=2 contract explicitly; after the self-heal publishes, the same real chain
# returns ``(True, pdf)`` → convert rc=0.  The raw xelatex rc (1) is recorded,
# never claimed to be 2.  All fixes come from source-side definitions supplied
# to the real staged loop, which performs its own real XeLaTeX dry compiles.

from slcn.compilefix.l1_detailed import collecting_results  # noqa: E402
from slcn import convert_compile  # noqa: E402

# All subject material is generic synthetic text (no real author/title, no
# ≥91-period information).  Only digit criteria appear (line numbers, codes).
_E2E_ENV_DECL = r"\newenvironment{firstenv}{}{}"
_E2E_MACRO_DECL = r"\newcommand{\othermacro}{Other text.}"
_E2E_TIKZ_BLOCK = (
    "\\tikzset{modal/.style={draw, rectangle, minimum width=#1},\n"
    "  world/.style={modal={2cm}, fill=blue!20}}\n"
)


class _EndToEndCase(_ProjectCase):
    """Real converter-chain compile + real staged-loop repair, source-side subject."""

    # subclasses set: support_sty (the EXACT source-side support file contents),
    # body, and subject_decl.  No duplicate declarations: the uniqueness gate
    # counts every source definition, so the support file must contain exactly
    # one definition of the subject.
    support_sty = ""
    subject_decl = ""

    def setUp(self):
        super().setUp()
        # Support definitions live ONLY in the source-side probe.sty, matching
        # the migration family's source-bound gate (candidate dir has none).
        self.sty.write_text(self.support_sty, encoding="utf-8")
        self.capture = capture_compile(self.out, self.stem, source=self.source)
        code, cap, raw = self._dry_compile(self.original_candidate, "e2e-orig")
        self.assertNotEqual(code, 0, "pre-repair candidate must really fail")
        self.capture["failure"] = ("c" * 32, 1, code, raw)
        self.report = diagnose(self.original_candidate, self.capture,
                               available_paths={"demo.tex"})

    def _real_convert_compile(self):
        """Run the REAL convert.py compile chain; return (convert_rc, result)."""
        with collecting_results() as results:
            ok, info = convert_compile.compile_pdf(str(self.out), self.stem)
        self.assertEqual(len(results), 1)
        result = results[0]
        # convert.py rc contract: failure → 2, success → 0.
        convert_rc = 0 if ok else 2
        return convert_rc, result, info

    def _assert_e2e_contract(self, decl: str, kind: str):
        # (a) pre-repair: real chain fails, raw xelatex rc is a real nonzero.
        before_rc, before_result, before_info = self._real_convert_compile()
        self.assertEqual(before_rc, 2, before_info)
        self.assertFalse(before_result.ok)
        self.assertTrue(str(before_result.phase).startswith("xelatex-"))
        self.assertIsNotNone(before_result.returncode)
        self.assertNotEqual(before_result.returncode, 0)
        self.assertEqual(before_result.candidate_sha256, _sha(self.original_candidate))
        self.assertEqual(self.report.subject[0], kind)

        # (b) real staged repair, one publish, source-side subject.
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
        self.assertEqual(len(calls), 1, "exactly one apply_plan publish")
        plan = calls[0]
        published = self._candidate_path.read_bytes()
        self.assertEqual(published, plan.patched)
        self.assertEqual(plan.reverse(plan.patched), self.original_candidate)
        self.assertEqual(published.count(decl.encode()), 1,
                         "definition appears exactly once")
        self.assertIn("l1_repaired", new_capture)
        for key in CAPTURE_KEYS:
            self.assertIn(key, new_capture)
        l1 = new_capture["l1"]
        self.assertEqual(set(l1), SHAREABLE_KEYS)
        dumped = json.dumps(l1, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("subject", dumped)
        self.assertNotIn("round", dumped)

        # (c) post-repair: SAME real chain now passes → convert rc=0.
        after_rc, after_result, after_info = self._real_convert_compile()
        self.assertEqual(after_rc, 0, after_info)
        self.assertTrue(after_result.ok, after_info)
        self.assertEqual(after_result.phase, "complete")
        self.assertEqual(after_result.returncode, 0)
        self.assertEqual(after_result.candidate_sha256, _sha(published))
        self.assertTrue((self.out / (self.stem + ".pdf")).exists())
        return before_result, after_result, plan


class TestEndToEndEnvironment(_EndToEndCase):
    """Environment missing: real rc=2 → publish → real rc=0."""

    support_sty = _E2E_ENV_DECL + "\n"
    subject_decl = _E2E_ENV_DECL
    body = ("\\begin{firstenv}\ninside first environment.\n\\end{firstenv}\n")

    def test_missing_environment_real_rc2_to_rc0(self):
        self._assert_e2e_contract(self.subject_decl, "environment")


class TestEndToEndMacro(_EndToEndCase):
    """Macro missing: real rc=2 → publish → real rc=0."""

    support_sty = _E2E_MACRO_DECL + "\n"
    subject_decl = _E2E_MACRO_DECL
    # Unconditional use: the real xelatex log carries the anchored l.N frame
    # the command-subject predicate needs (gating on \ifdefined hides it).
    body = ("\\othermacro marker.\n")

    def test_missing_macro_real_rc2_to_rc0(self):
        self._assert_e2e_contract(self.subject_decl, "command")


class TestEndToEndTikzKey(_EndToEndCase):
    """TikZ key missing: real rc=2 → publish → real rc=0."""

    support_sty = _E2E_TIKZ_BLOCK
    subject_decl = "modal/.style"
    body = ("\\begin{tikzpicture}\n\\node[modal]{modal node};\n\\node[world]{world node};\n"
            "\\end{tikzpicture}\n")

    def setUp(self):
        # TikZ candidate needs \usepackage{tikz} FIRST: insert_lines() places
        # the source-bound \tikzset block after the first \usepackage, so tikz
        # must already be loaded there (mirrors the family fixture's order).
        self.candidate_override = TEMPLATE.replace(
            "\\usepackage{amsmath}\n",
            "\\usepackage{tikz}\n\\usepackage{amsmath}\n"
        ).replace("SKELMARK", self.body).encode("utf-8")
        super().setUp()

    def test_missing_tikzkey_real_rc2_to_rc0(self):
        self._assert_e2e_contract(self.subject_decl, "tikz_key")


class TestEndToEndMultiroundChain(_EndToEndCase):
    """Real multiround chain env→macro: one publish, byte-exact reverse each round."""

    support_sty = _E2E_ENV_DECL + "\n" + _E2E_MACRO_DECL + "\n"
    subject_decl = _E2E_ENV_DECL
    body = BODY_2  # env → gated macro → convergence in ≤3 rounds

    def test_multiround_chain_converges_with_single_publish(self):
        before_result, after_result, plan = self._assert_e2e_contract(
            self.subject_decl, "environment")
        # Chain must have staged BOTH definitions and converged in ≤3 rounds.
        published = self._candidate_path.read_bytes()
        self.assertEqual(published.count(_E2E_ENV_DECL.encode()), 1)
        self.assertEqual(published.count(_E2E_MACRO_DECL.encode()), 1)
        self.assertIn(_E2E_MACRO_DECL.encode(), plan.added_bytes)
        # The chain is continuous and reversible as one final slice.
        self.assertEqual(plan.start + len(plan.added_bytes), plan.end)
        self.assertEqual(plan.reverse(plan.patched), self.original_candidate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
