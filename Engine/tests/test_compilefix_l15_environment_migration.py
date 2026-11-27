"""TDD for the L1.5 environment-migration self-heal rule.

Written BEFORE any production code.  RED state: compile_with_l1 does not admit
the new rule, so the H7b five-gate failure returns rc-equivalent (ok=False) and
the candidate bytes stay unchanged.  GREEN state: the same failure is repaired
in place by a single, reversible, independently-shaped byte edit.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIXTURE_ROOT = Path("/tmp/slcn-l15-retry-independent")
MISSINGENV_ROOT = Path("/tmp/slcn-2b-manual/fixture")
EXPECTED_CANDIDATE_SHA = (
    "d2341a0d5b58518fdba5787e6b13beba87ee5ff020ab332e41a09b5b423d483d"
)
HINT_LINE = "环境 skelenv 未定义：已核实候选第 201 行"
DECL = "\\newenvironment{skelenv}{}{}"
MARKER = (DECL + "\n").encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _failure_json(stderr: str) -> dict:
    """Parse the single failure JSON object that L1 emits on the old path."""
    start = stderr.index("{")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(stderr[start:])
    return obj


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


class _FixtureCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        if shutil.which("xelatex") is None or shutil.which("biber") is None:
            self.skipTest("BLOCKED: XeLaTeX/Biber unavailable")
        self.tmp = tempfile.mkdtemp(prefix="slcn-l15-impl-test-")
        self.root = Path(self.tmp)
        self.env = _env(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prepare_synthetic(self, name: str):
        src = self.root / "in"
        src.mkdir(exist_ok=True)
        shutil.copy(FIXTURE_ROOT / "in" / "main.tex", src / "main.tex")
        shutil.copy(FIXTURE_ROOT / "in" / "probe.sty", src / "probe.sty")
        shutil.copy(FIXTURE_ROOT / "portable-template.tex", self.root / "portable-template.tex")
        return src

    def _convert(self, source: Path, out: Path, name: str, skeleton: Path):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(REPO / "convert.py"),
                "--source", str(source),
                "--out", str(out),
                "--name", name,
                "--lang", "en",
                "--skeleton", str(skeleton),
                "--compile",
            ],
            cwd=str(REPO),
            env=self.env,
            text=True,
            capture_output=True,
        )

    def _candidate(self, out: Path, name: str) -> Path:
        return out / name / f"{name}.tex"


class TestL15EnvironmentMigrationRedGreen(_FixtureCase):
    """The central H7b five-gate admission, repaired by the new rule."""

    def test_h7b_five_gate_failure_is_admitted_and_repaired(self):
        """RED: old L1 never admits; GREEN: L1.5 repairs with one reversible edit."""
        self._prepare_synthetic("H7bImpl")
        out = self.root / "out"
        proc = self._convert(self.root / "in" / "main.tex", out, "H7bImpl",
                             self.root / "portable-template.tex")

        candidate = self._candidate(out, "H7bImpl")
        self.assertTrue(candidate.exists(), proc.stderr)

        # GREEN: the five gates hold in this very round and L1.5 fires.  The
        # single failure transcript (RED evidence, kept under /tmp) carried the
        # bound primary error and its verified line hint; after repair the
        # published candidate is the original plus exactly one marker.
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("L1 编译通过", proc.stdout)
        self.assertNotIn("L1 未修复", proc.stderr)
        patched = candidate.read_bytes()
        self.assertEqual(patched.count(MARKER), 1)
        self.assertEqual(patched.find(MARKER), 9846)  # verified preamble byte offset
        self.assertEqual(len(patched), 12568 + len(MARKER))  # +29 B on the 12568 B candidate
        self.assertTrue((out / "H7bImpl" / "H7bImpl.pdf").exists())

        # Independent reversibility: delete the exact continuous slice and
        # recover the documented, byte-identical pre-repair candidate.
        restored = patched.replace(MARKER, b"", 1)
        self.assertEqual(len(restored), 12568)
        self.assertEqual(hashlib.sha256(restored).hexdigest(), EXPECTED_CANDIDATE_SHA)


class TestL15NegativeGuards(_FixtureCase):
    """Every rejection keeps the old failure path and leaves candidate bytes alone."""

    def _run_variant(self, name: str, probe_text: str | None,
                     template_declaration: bool = False):
        """Run a rejection variant; return (proc, candidate, pre_convert_bytes)."""
        src = self._prepare_synthetic(name)
        if probe_text is not None:
            (src / "probe.sty").write_text(probe_text)
        skeleton = self.root / "portable-template.tex"
        if template_declaration:
            text = skeleton.read_text()
            anchor = "\\begin{document}"
            text = text.replace(anchor, DECL + "\n" + anchor, 1)
            skeleton.write_text(text)
        out = self.root / "out"
        # Snapshot every support input before convert: a rejected variant must
        # not write through to any author submission or support file.
        before = {p: p.read_bytes() for p in (*src.iterdir(), skeleton)}
        proc = self._convert(src / "main.tex", out, name, skeleton)
        candidate = self._candidate(out, name)
        for path, original in before.items():
            self.assertEqual(path.read_bytes(), original, path)
        return proc, candidate

    def _assert_old_failure(self, proc, candidate):
        self.assertTrue(candidate.exists(), proc.stderr)
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("L1 未修复", proc.stderr)
        self.assertIn('"result": "not-admitted"', proc.stderr)
        self.assertNotIn("L1 编译通过", proc.stdout)
        # No environment declaration was added: the failing round keeps the
        # original artifact bytes (no partial L1.5 edit published).
        self.assertEqual(candidate.read_bytes().count(MARKER), 0)

    def test_missing_declaration_zero_is_not_admitted(self):
        """Acceptance §4 line 62: missingenv with zero declarations stays old path."""
        src = self.root / "missing"
        src.mkdir()
        shutil.copy(MISSINGENV_ROOT / "main.tex", src / "main.tex")
        self.assertEqual(_sha(src / "main.tex"),
                         "fe5d2a28ef30a5b96ab6f4d8ec0833313b4ec782220f0ea22e8a7048327da3d5")
        skeleton = FIXTURE_ROOT / "portable-template.tex"
        out = self.root / "out"
        proc = self._convert(src / "main.tex", out, "MissingEnv", skeleton)
        candidate = self._candidate(out, "MissingEnv")
        self._assert_old_failure(proc, candidate)
        self.assertIn("missingenv", proc.stderr)

    def test_renamed_declaration_is_not_admitted(self):
        proc, candidate = self._run_variant(
            "RenamedEnv", "\\newenvironment{otherenv}{}{}\n")
        self._assert_old_failure(proc, candidate)

    def test_renewenvironment_degrade_is_not_admitted(self):
        proc, candidate = self._run_variant(
            "RenewEnv", "\\renewenvironment{skelenv}{}{}\n")
        self._assert_old_failure(proc, candidate)

    def test_same_file_duplicate_declaration_is_not_admitted(self):
        """Card line 22: two declarations of one env in the same .sty must be
        rejected from the pre-dedup scan, even though a deduping observer sees
        only the first (winner) declaration (FINDING 1)."""
        first = "\\newenvironment{skelenv}{FIRST}{FIRST-END}\n"
        second = "\\newenvironment{skelenv}{SECOND}{SECOND-END}\n"
        proc, candidate = self._run_variant("DupEnv", first + second)
        self._assert_old_failure(proc, candidate)
        body = candidate.read_bytes()
        self.assertEqual(body.count(b"FIRST"), 0)
        self.assertEqual(body.count(b"SECOND"), 0)
        with self.subTest("artifact bytes unchanged after rejected round"):
            self.assertEqual(len(body), 12568)
            self.assertEqual(_sha(candidate), EXPECTED_CANDIDATE_SHA)
        failure = _failure_json(proc.stderr)
        self.assertIn(
            "environment-migration-v1",
            [a.get("rule") for a in failure.get("attempts", [])],
            failure["attempts"],
        )

    def test_unclear_conditional_scope_is_not_admitted(self):
        proc, candidate = self._run_variant(
            "ConditionalEnv", "\\iftrue\n\\newenvironment{skelenv}{}{}\n\\fi\n")
        self._assert_old_failure(proc, candidate)

    def test_template_already_defined_gets_no_self_heal(self):
        proc, candidate = self._run_variant(
            "TemplateHasEnv", None, template_declaration=True)
        self.assertTrue(candidate.exists(), proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("L1 编译通过", proc.stdout)
        self.assertEqual(candidate.read_bytes().count(MARKER), 1)

    def test_catcode_positive_still_uses_old_rule(self):
        """The pre-existing catcode rule keeps priority and is never displaced."""
        from slcn.compilefix.l1_diagnostics import diagnose
        from slcn.compilefix.l1_rules import CATCODE, select_plan
        from slcn.compilefix.files import digest

        raw = (b"\\documentclass{article}\n"
               b"\\newcommand{\\Demo}{\\@gobble{discard}Kept}\n"
               b"\\begin{document}\n\\Demo\n\\end{document}\n")
        log = (b"! You can't use `\\spacefactor' in vertical mode.\n"
               b"\\Demo ->\\@\n             gobble{discard}Kept\nl.4 \\Demo\n")
        capture = {"candidate_sha256": digest(raw), "failure": ("b" * 32, 1, 1, log)}
        plan = select_plan(raw, diagnose(raw, capture))
        self.assertIsNotNone(plan)
        self.assertEqual(plan.rule_id, CATCODE.rule_id)
        self.assertEqual(plan.reverse(plan.patched), raw)


class TestL15Gate1Boundaries(unittest.TestCase):
    """Gate ① refuses a wrong candidate hash or a non-matching anchor line."""

    @staticmethod
    def _round(log=None, sha=None):
        from slcn.compilefix.environment_migration_rule import _failure_round
        from slcn.compilefix.files import digest
        from slcn.compilefix.l1_diagnostics import diagnose

        candidate = (
            b"\\documentclass{article}\n"      # 1
            b"\\usepackage{probe}\n"            # 2
            b"\\begin{document}\n"              # 3
            b"\\begin{skelenv}\n"               # 4
            b"Body.\n"                          # 5
            b"\\end{skelenv}\n"                 # 6
            b"\\end{document}\n"                # 7
        )
        if log is None:
            log = (
                b"! LaTeX Error: Environment skelenv undefined.\n"
                b"l.4 \\begin{skelenv}\n"
                b"! LaTeX Error: \\begin{document} ended by \\end{skelenv}.\n"
                b"l.6 \\end{skelenv}\n"
            )
        capture = {"candidate_sha256": digest(candidate) if sha is None else sha,
                   "failure": ("b" * 32, 1, 1, log)}
        report = diagnose(candidate, capture)
        return _failure_round(report, capture, candidate)

    def test_positive_control_gate1_admits_bound_round(self):
        self.assertEqual(self._round(), ("skelenv", 4, 6))

    def test_wrong_candidate_sha256_is_not_admitted(self):
        self.assertIsNone(self._round(sha="0" * 64))

    def test_wrong_anchor_line_is_not_admitted(self):
        log = (
            b"! LaTeX Error: Environment skelenv undefined.\n"
            b"l.5 \\begin{skelenv}\n"  # line 5 is "Body." — anchor text mismatch
            b"! LaTeX Error: \\begin{document} ended by \\end{skelenv}.\n"
            b"l.6 \\end{skelenv}\n"
        )
        self.assertIsNone(self._round(log=log))


class TestL15ByteDiffGuards(unittest.TestCase):
    """The independent diff rejects tampering with hashes or the exact slice."""

    def _plan(self):
        from slcn.compilefix.files import digest
        from slcn.compilefix.environment_migration_rule import (
            EnvironmentEditPlan, InputBinding,
        )
        original = b"AAA\nBBB\n"
        marker = b"NEW\n"
        patched = b"AAA\nNEW\nBBB\n"
        binding = InputBinding("probe.sty", "00", None, ())
        return EnvironmentEditPlan(
            identity="id", rule_id="environment-migration-v1", version=1,
            diagnosis_identity="id", diagnosis_candidate_sha256=digest(original),
            context_fingerprint="fp", env_name="skelenv", target_binding=binding,
            declaration="NEW", separator=b"\n", offset=4, added_bytes=marker,
            before_sha256=digest(original), after_sha256=digest(patched),
            original=original, patched=patched,
        ), original, patched

    def test_reverse_rejects_wrong_after_hash(self):
        from slcn.compilefix.files import RepairError
        plan, _original, _patched = self._plan()
        with self.assertRaises(RepairError):
            plan.reverse(b"TAMPERED")

    def test_reverse_rejects_wrong_anchor_slice(self):
        from slcn.compilefix.files import RepairError
        plan, _original, _patched = self._plan()
        # Same length and same after-hash binding is impossible to forge, but a
        # caller passing a different byte sequence at the recorded hash must be
        # rejected: patch the bytes without recomputing the digest.
        shifted = b"AAA\nXEW\nBBB\n"
        with self.assertRaises(RepairError):
            plan.reverse(shifted)

    def test_reverse_is_byte_exact(self):
        plan, original, patched = self._plan()
        self.assertEqual(plan.start, 4)
        self.assertEqual(plan.end, 8)
        self.assertEqual(plan.reverse(patched), original)


class TestL15WiringGuards(unittest.TestCase):
    """FINDING 2: an unexpected exception in the new env path must not swallow
    the four-attempt semantics of L1 (not-admitted / recovery-required /
    failed-rolled-back / passed).  Before the fix, RuntimeError from
    build_context escapes to the outer handler, leaving attempts=[].
    """

    def test_env_path_unexpected_exception_still_records_not_admitted(self):
        import contextlib
        import io
        import slcn.compilefix as cf
        from slcn.compilefix import l1 as l1_mod
        from slcn.compilefix.state import capture_compile

        raw = (b"\\documentclass{article}\n\\begin{document}\nx\n"
               b"\\end{document}\n")
        log = b"! You can't use `\\spacefactor' in vertical mode.\nl.4 x\n"
        with tempfile.TemporaryDirectory(prefix="slcn-l15-wiring-") as tmp:
            root = Path(tmp)
            source = root / "source"
            out = root / "out"
            source.mkdir()
            out.mkdir()
            (source / "incoming.tex").write_bytes(raw)
            (out / "demo.tex").write_bytes(raw)
            (out / "demo.pdf").write_bytes(b"OLD-PDF")
            (out / "demo.log").write_bytes(log)
            capture = capture_compile(out, "demo", source=source / "incoming.tex")
            self.assertIsNotNone(capture)
            capture["failure"] = ("c" * 32, 1, 1, log)
            compiler = MagicMock(return_value=(True, ""))
            err = io.StringIO()
            with patch.object(cf, "compile_with_capture",
                              return_value=(False, "synthetic failure", capture)):
                with patch.object(l1_mod, "template_results", return_value=()):
                    with patch.object(l1_mod, "environment_hint", return_value=()):
                        with patch("slcn.compilefix.environment_migration_rule.build_context",
                                   side_effect=RuntimeError("unexpected")):
                            with contextlib.redirect_stderr(err):
                                ok, _info, cap = l1_mod.compile_with_l1(
                                    compiler, out, "demo",
                                    source=source / "incoming.tex",
                                )
            self.assertFalse(ok)
            # The full failure JSON must still be emitted (old failure path),
            # not the outer "diagnostic conditions insufficient" fallback.
            report = _failure_json(err.getvalue())
            self.assertEqual(report["attempts"], [
                {"rule": "environment-migration-v1", "result": "not-admitted"},
                {"rule": "catcode-top-level-at-v1", "result": "not-admitted"},
            ])
            self.assertIsNotNone(cap)
            self.assertIsNotNone(cap["l1"])
            self.assertEqual(cap["l1"]["attempts"], report["attempts"])


if __name__ == "__main__":
    unittest.main()
