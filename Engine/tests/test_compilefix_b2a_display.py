"""B2A RED tests: local failure display must not change compilefix identity.

This file is the RED half of TDD for the second-batch "log localisation" card.
Every assertion against a NEW interface is referenced only inside a test
method, so the module imports cleanly before GREEN and each new contract fails
as an assertion/attribute failure rather than a collection-time crash.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import asdict
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slcn.compilefix.files import digest, json_bytes
from slcn.compilefix.l1_diagnostics import diagnose
from slcn.compilefix.l1_rules import select_plan
from slcn.compilefix.model import diagnostics, taskbook
from slcn.compilefix.state import Store, capture_compile, target_key

BASELINE_PATH = Path(__file__).resolve().parent / "fixtures" / "compilefix" / "b2a_baseline.json"
with BASELINE_PATH.open("r", encoding="utf-8") as stream:
    BASELINE = json.load(stream)

CATCODE = b"\\documentclass{article}\n\\newcommand{\\Demo}{\\@gobble{discard}Kept}\n\\begin{document}\n\\Demo\n\\end{document}\n"
CATCODE_LOG = b"! You can't use `\\spacefactor' in vertical mode.\n\\Demo ->\\@\nl.4 \\Demo\n"
ENV_LOG = b"! LaTeX Error: Environment theorem-a undefined.\nl.261 \\begin{theorem-a}\n"
SECRET_LOG = b"! LaTeX Error: Environment theorem-a undefined.\nl.261 \\begin{theorem-a}\n/Users/person/private sk-synthetic-secret123 api_key=secret\n"
MESSAGE = "xelatex 第 1 步失败（退出码 1）"
FIXED_KEY = "fixed-key-for-baseline"


def env_candidate(begin_line=261, *, duplicate=False, name="theorem-a"):
    """Build an ENV candidate with a begin at begin_line and a matching end."""
    filler = ["% filler line"] * 200
    while len(filler) < begin_line - 1:
        filler.append("% filler line")
    lines = filler[:begin_line - 1]
    lines.append("\\begin{" + name + "}")
    lines.append("body")
    if duplicate:
        lines.append("\\begin{" + name + "}")
        lines.append("duplicate body")
    lines.append("\\end{" + name + "}")
    lines.append("\\end{document}")
    return ("\n".join(lines) + "\n").encode("utf-8")


ENV = env_candidate()
assert digest(ENV) == BASELINE["env_fingerprint"], "ENV fixture must match the recorded baseline hash"
assert ENV.decode("utf-8").splitlines()[260] == BASELINE["env_line261"]


def captured(data, log, step=1, code=1):
    """Binding shape used by diagnose() in the baseline probe."""
    return {"candidate_sha256": digest(data), "failure": ("a" * 32, step, code, log)}


def _hint(candidate, cap, *, planning_evidence=None):
    """Call the NEW pure display function through an in-method import (RED-safe)."""
    from slcn.compilefix.l1_diagnostics import environment_hint
    return environment_hint(candidate, cap, planning_evidence=planning_evidence)


def _json_norm(value):
    """Normalise through the canonical JSON bytes (baseline was JSON-roundtripped)."""
    return json.loads(json_bytes(value))


def assert_clean_hints(testcase, hints):
    """Shared local-display hygiene assertions (limit, no raw log or secrets)."""
    testcase.assertIsInstance(hints, tuple)
    testcase.assertLessEqual(len(hints), 4)
    for hint in hints:
        testcase.assertIsInstance(hint, str)
        testcase.assertLessEqual(len(hint), 200)
        for private in ("/Users", "sk-", "api_key", "SECRET_MANUSCRIPT"):
            testcase.assertNotIn(private, hint)


class B2ACaptureTests(unittest.TestCase):
    def test_compile_preview_has_exactly_four_keys(self):
        """Pre-compile capture snapshot has exactly the four baseline keys."""
        with tempfile.TemporaryDirectory(prefix="b2a-capture-") as tmp:
            root = Path(tmp)
            src = root / "source" / "incoming.tex"
            src.parent.mkdir()
            src.write_bytes(CATCODE)
            outdir = root / "out"
            outdir.mkdir()
            (outdir / "demo.tex").write_bytes(CATCODE)
            before = capture_compile(outdir, "demo", source=src)
            self.assertIsNotNone(before)
            self.assertEqual(set(before), set(BASELINE["capture_keys"]))
            self.assertEqual(before["candidate_sha256"], digest(CATCODE))

    def test_run_xelatex_keeps_existing_fields_until_failure_binding(self):
        """Controlled compiler failure binds raw bytes without losing prior fields."""
        from slcn.compilefix import compile_with_capture

        raw = b"! LaTeX Error: Environment theorem-a undefined.\nl.261 \\begin{theorem-a}\n"
        with tempfile.TemporaryDirectory(prefix="b2a-capture-run-") as tmp:
            root = Path(tmp)
            src = root / "source" / "incoming.tex"
            src.parent.mkdir()
            src.write_bytes(CATCODE)
            outdir = root / "out"
            outdir.mkdir()
            (outdir / "demo.tex").write_bytes(CATCODE)

            def compile_pdf(out_dir, stem, *, run_xelatex):
                class Result:
                    returncode = 1

                def runner(command, *, cwd, quiet, check=False, stdout=None):
                    stdout.write(raw)
                    stdout.flush()
                    return Result()

                run_xelatex(runner, ["xelatex", "demo.tex"], out_dir, step=1)
                return False, "合成编译失败信息"

            ok, info, capture = compile_with_capture(compile_pdf, outdir, "demo", source=src)
            self.assertFalse(ok)
            self.assertEqual(info, "合成编译失败信息")
            self.assertIsNotNone(capture)
            failure = capture["failure"]
            self.assertEqual(len(failure), 4)
            self.assertEqual(failure[1:], (1, 1, raw))
            self.assertRegex(failure[0], r"^[0-9a-f]{32}$")

            # Existing caller fields must survive; sentinel proves no field loss.
            capture["sentinel"] = "keep-me"
            self.assertEqual(capture["sentinel"], "keep-me")
            self.assertEqual(capture["candidate_sha256"], digest(CATCODE))


class B2AIdentityTests(unittest.TestCase):
    def _three_roots(self):
        """Three isolated clean state roots, identical candidates and inode-safe setup."""
        roots = []
        for _ in range(3):
            tmp = tempfile.mkdtemp(prefix="b2a-identity-")
            root = Path(tmp)
            src = root / "source" / "incoming.tex"
            src.parent.mkdir()
            src.write_bytes(CATCODE)
            outdir = root / "out"
            outdir.mkdir()
            (outdir / "demo.tex").write_bytes(CATCODE)
            (outdir / "demo.log").write_bytes(CATCODE_LOG)
            cap = capture_compile(outdir, "demo", source=src)
            self.assertIsNotNone(cap)
            cap["failure"] = ("a" * 32, 1, 1, CATCODE_LOG)
            roots.append((root, src, outdir, cap))
        return roots

    def test_abc_candidate_reports_and_taskbook_are_identical(self):
        """A/B/C planning evidence must not alter identity or shared bytes."""
        from slcn.compilefix.l1_diagnostics import environment_hint  # NEW: local in-method import

        roots = self._three_roots()
        correct = {
            "candidate_sha256": BASELINE["catcode_fingerprint"],
            "decisions": {"theorem-a": {"action": "imported", "reason": "must-not-be-echoed"}},
        }
        wrong = {
            "candidate_sha256": "0" * 64,
            "decisions": {"theorem-a": {"action": "imported", "reason": "stale-secret"}},
        }
        cases = ((None, "无本轮规划记录"), (correct, "本轮规划"), (wrong, "未知或已过期"))
        snapshots = []
        for root, src, outdir, cap in roots:
            with patch.dict(os.environ, {"SLCN_COMPILEFIX_STATE_DIR": str(root / "state")}):
                report = diagnose(CATCODE, cap)
                self.assertEqual(report.identity, BASELINE["catcode_report"]["identity"])
                self.assertEqual(_json_norm(report.shareable()), BASELINE["catcode_report"]["shareable"])
                self.assertEqual([_json_norm(asdict(item)) for item in report.items], BASELINE["catcode_report"]["items"])
                self.assertEqual(list(report.error_lines), BASELINE["catcode_report"]["error_lines"])

                summaries = diagnostics(CATCODE, CATCODE_LOG, "demo", MESSAGE)
                book = taskbook(FIXED_KEY, CATCODE, summaries)
                self.assertEqual(book["identity"], BASELINE["taskbook_identity"])
                self.assertEqual(json_bytes(book).hex(), BASELINE["taskbook_raw_hex"])
                self.assertEqual(digest(json_bytes(book)), BASELINE["taskbook_sha256"])

                self.assertEqual((outdir / "demo.tex").read_bytes(), CATCODE)

                for evidence, marker in cases:
                    hints = environment_hint(CATCODE, cap, planning_evidence=evidence)
                    assert_clean_hints(self, hints)
                    self.assertTrue(any(marker in hint for hint in hints), (marker, hints))
                    self.assertFalse(any("must-not-be-echoed" in hint or "stale-secret" in hint for hint in hints))
                snapshots.append((report.identity, _json_norm(report.shareable()),
                                  tuple(json.dumps(_json_norm(asdict(item)), sort_keys=True) for item in report.items),
                                  report.error_lines, json_bytes(book)))
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])

    def test_real_issue_state_matches_baseline(self):
        """One real Store.issue call in a clean root preserves input/diagnostic identity."""
        with tempfile.TemporaryDirectory(prefix="b2a-issue-") as tmp:
            root = Path(tmp)
            src = root / "source" / "incoming.tex"
            src.parent.mkdir()
            src.write_bytes(CATCODE)
            outdir = root / "out"
            outdir.mkdir()
            (outdir / "demo.tex").write_bytes(CATCODE)
            (outdir / "demo.log").write_bytes(CATCODE_LOG)
            cap = capture_compile(outdir, "demo", source=src)
            cap["failure"] = ("a" * 32, 1, 1, CATCODE_LOG)
            with patch.dict(os.environ, {"SLCN_COMPILEFIX_STATE_DIR": str(root / "state")}):
                store = Store()
                with store.operation():
                    task_id, raw = store.issue(outdir, "demo", source=src, message=MESSAGE, capture=cap)
                    state = store.load(task_id)
            expected = BASELINE["issue_baseline"]
            # target_key() hashes the absolute out_dir path, so input_id and
            # taskbook bytes are not comparable across different temp roots;
            # recompute the locally expected values and self-check them.
            # Store.validate_locations() normalises to the absolute path first.
            local_key = target_key(outdir.resolve(), "demo")
            local_summaries = diagnostics(CATCODE, CATCODE_LOG, "demo", MESSAGE)
            local_book = taskbook(local_key, CATCODE, local_summaries)
            local_expected_id = local_book["identity"]["task_id"]
            self.assertEqual(state["input_id"], local_expected_id)
            self.assertEqual(task_id, state["input_id"])
            self.assertEqual(digest(raw), digest(json_bytes(local_book)))
            # Path-independent baseline invariants must still hold.
            self.assertEqual(state["identity"]["diagnostic_sha256"], expected["state_diagnostic_sha256"])
            self.assertEqual(len(raw), expected["raw_len"])
            self.assertEqual(state["identity"]["task_id"], task_id)

    def test_legacy_call_site_signature_keeps_defaults(self):
        """compile_with_l1 accepts the new optional parameter without changing old calls."""
        from slcn.compilefix.l1 import compile_with_l1
        import inspect

        signature = inspect.signature(compile_with_l1)
        self.assertIn("planning_evidence", signature.parameters)
        self.assertIsNone(signature.parameters["planning_evidence"].default)

        roots = self._three_roots()
        for root, src, outdir, cap in roots:
            with patch.dict(os.environ, {"SLCN_COMPILEFIX_STATE_DIR": str(root / "state")}):
                def compile_pdf(out_dir, stem, **kwargs):
                    return False, "合成编译失败信息"

                ok, info, new_capture = compile_with_l1(
                    compile_pdf, outdir, "demo", source=src, enabled=False,
                    planning_evidence={"candidate_sha256": BASELINE["catcode_fingerprint"], "decisions": {}})
                self.assertFalse(ok)
                self.assertEqual(info, "合成编译失败信息")
                self.assertEqual(new_capture["candidate_sha256"], cap["candidate_sha256"])
                self.assertEqual((outdir / "demo.tex").read_bytes(), CATCODE)


class B2AAmissionTests(unittest.TestCase):
    def test_catcode_plan_unchanged(self):
        """The admitted catcode plan keeps its exact offsets and reverse edit."""
        report = diagnose(CATCODE, captured(CATCODE, CATCODE_LOG))
        plan = select_plan(CATCODE, report)
        self.assertIsNotNone(plan)
        expected = BASELINE["catcode_plan"]
        self.assertEqual(plan.rule_id, expected["rule_id"])
        self.assertEqual((plan.start, plan.end), (expected["start"], expected["end"]))
        self.assertEqual(plan.prefix.decode(), expected["prefix"])
        self.assertEqual(plan.suffix.decode(), expected["suffix"])
        self.assertEqual(digest(plan.patched), expected["patched_sha256"])
        self.assertTrue(plan.reverse(plan.patched) == CATCODE)
        self.assertEqual(plan.patched.hex(), BASELINE["catcode_patched_hex"])

    def test_environment_undefined_still_not_admitted(self):
        """An undefined-environment report stays outside the catcode whitelist."""
        report = diagnose(ENV, captured(ENV, ENV_LOG))
        self.assertIsNone(select_plan(ENV, report))

    def test_mixed_catcode_plus_environment_keeps_old_error_lines_and_plan(self):
        """A second independent error does not perturb old error_lines/plan decisions."""
        mixed_log = CATCODE_LOG + b"! LaTeX Error: Environment theorem-a undefined.\nl.261 \\begin{theorem-a}\n"
        report = diagnose(CATCODE, captured(CATCODE, mixed_log))
        self.assertEqual(list(report.error_lines), BASELINE["catcode_report"]["error_lines"])
        plan = select_plan(CATCODE, report)
        # At 943bc31 a second error demotes catcode exact -> report-only, so the
        # baseline plan is None. B2A-3 requires this decision stay unchanged.
        self.assertIsNone(plan)


class B2AEnvironmentDisplayTests(unittest.TestCase):
    def _cap(self, data, log, step=1):
        return captured(data, log, step=step, code=1)

    def test_verified_begin_line_and_planning_evidence_are_shown(self):
        """XeLaTeX-1 bound failure verifies the literal begin line."""
        cap = self._cap(ENV, ENV_LOG)
        hints = _hint(ENV, cap)
        assert_clean_hints(self, hints)
        joined = " ".join(hints)
        for marker in ("已核实", "XeLaTeX 第 1 步", "候选第 261 行", "theorem-a 未定义", "无本轮规划记录"):
            self.assertIn(marker, joined)

        bound = {"candidate_sha256": BASELINE["env_fingerprint"],
                 "decisions": {"theorem-a": {"action": "imported", "reason": "internal-reason-never-echo"}}}
        hints_b = _hint(ENV, cap, planning_evidence=bound)
        assert_clean_hints(self, hints_b)
        self.assertTrue(any("本轮规划" in h for h in hints_b))
        self.assertTrue(any("theorem-a" in h for h in hints_b))
        self.assertFalse(any("internal-reason-never-echo" in h for h in hints_b))

    def test_different_begin_line_is_shown_when_log_and_candidate_agree(self):
        """A verified begin at line 300 displays that candidate line."""
        data = env_candidate(300)
        log = b"! LaTeX Error: Environment theorem-a undefined.\nl.300 \\begin{theorem-a}\n"
        hints = _hint(data, self._cap(data, log))
        assert_clean_hints(self, hints)
        joined = " ".join(hints)
        self.assertIn("已核实", joined)
        self.assertIn("候选第 300 行", joined)

    def test_duplicate_name_never_claims_unique_pairing(self):
        """Two same-named begins leave the line unverified and items split."""
        data = env_candidate(261, duplicate=True)
        hints = _hint(data, self._cap(data, ENV_LOG))
        assert_clean_hints(self, hints)
        joined = " ".join(hints)
        self.assertIn("theorem-a", joined)
        self.assertNotIn("已核实", joined)
        self.assertTrue(any("行号未核实" in h for h in hints), hints)

    def test_second_independent_error_is_visible_without_raw_echo(self):
        """Two independent errors produce two items without leaking raw text."""
        data = ENV
        log = ENV_LOG + b"! Package foo Error: something.\nl.10 x\n"
        hints = _hint(data, self._cap(data, log))
        assert_clean_hints(self, hints)
        self.assertTrue(any("theorem-a" in h for h in hints))
        self.assertFalse(any("something" in h for h in hints))

    def test_forged_line_and_wrong_inline_name_are_unverified(self):
        """Out-of-range anchors and mismatched begin names block verification."""
        forged_log = b"! LaTeX Error: Environment theorem-a undefined.\nl.999 \\begin{theorem-a}\n"
        wrong_name_data = env_candidate(261, name="theorem-b")
        wrong_name_log = b"! LaTeX Error: Environment theorem-a undefined.\nl.261 \\begin{theorem-a}\n"
        for data, log in ((ENV, forged_log), (wrong_name_data, wrong_name_log)):
            with self.subTest(log=log):
                hints = _hint(data, self._cap(data, log))
                assert_clean_hints(self, hints)
                joined = " ".join(hints)
                self.assertIn("theorem-a", joined)
                self.assertNotIn("已核实", joined)
                self.assertTrue(any("行号未核实" in h for h in hints), hints)

    def test_wrong_candidate_hash_and_biber_phase_yield_no_hints(self):
        """Unbound candidates and non-XeLaTeX phases are not explained."""
        bad = self._cap(ENV, ENV_LOG)
        bad["candidate_sha256"] = "0" * 64
        self.assertEqual(_hint(ENV, bad), ())
        self.assertEqual(_hint(ENV, self._cap(ENV, ENV_LOG, step=2)), ())

    def test_sensitive_log_never_pollutes_display_or_shareable(self):
        """Path/credential-looking log text stays out of display and shareable()."""
        cap = self._cap(ENV, SECRET_LOG)
        hints = _hint(ENV, cap)
        assert_clean_hints(self, hints)
        report = diagnose(ENV, cap)
        self.assertEqual(report.identity, BASELINE["env_report"]["identity"])
        self.assertEqual(_json_norm(report.shareable()), BASELINE["env_report"]["shareable"])
        shareable_raw = json_bytes(report.shareable())
        for private in (b"/Users", b"sk-", b"api_key", b"secret", b"something"):
            self.assertNotIn(private, shareable_raw)
        self.assertEqual([_json_norm(asdict(item)) for item in report.items], BASELINE["env_report"]["items"])
        self.assertEqual(list(report.error_lines), BASELINE["env_report"]["error_lines"])


if __name__ == "__main__":
    unittest.main()
