"""T2 · CLI-stitch RED/GREEN tests (authoritative contract: T1 report §1/§7).

Iron law: written before any production change; RED captured verbatim, then GREEN.
LEAD UPDATE 2026-11-27: opt-in → opt-out (default-on, --no-compile-multiround disables).
Only spies on apply_plan / run_staged_repair_loop / diagnose / export_failure.
No xelatex / biber mocks: every compile uses the real TeX toolchain.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

os.environ["SLCN_MANUSCRIPTS"] = "/dev/null"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import convert  # noqa: E402
import slcn.compilefix as cfmod  # noqa: E402
import slcn.compilefix.l1 as l1mod  # noqa: E402
from slcn.compilefix import capture_compile  # noqa: E402
from slcn.compilefix.compiler import support_manifest, _environment  # noqa: E402
from slcn.compilefix.files import RepairError  # noqa: E402
from slcn.compilefix.l1 import template_results  # noqa: E402
from slcn.compilefix.l1_diagnostics import diagnose  # noqa: E402
from slcn.convert_stage import build_arg_parser  # noqa: E402

EXPECTED_HASH = "b75f7f4e8129844844a3f805aaeed42bebacc2bd6a4d3748c5ce3a40965fa8b9"
SHAREABLE_KEYS = {"identity", "candidate_sha256", "phase", "returncode",
                  "diagnostics", "attempts"}

TEMPLATE = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{hyperref}
\begin{document}
SKELMARK
\end{document}
"""
BODY = r"""\begin{firstenv}
inside.
\end{firstenv}
\ifdefined\firstenv
\othermacro marker.
\fi
"""
ENV_DECL = r"\newenvironment{firstenv}{}{}"
MACRO_DECL = r"\newcommand{\othermacro}{Other text.}"
EXTRA_DECL = r"\newcommand{\thirdmacro}{Third text.}"


def _set_env(root: pathlib.Path) -> None:
    values = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "SLCN_MANUSCRIPTS": "/dev/null",
        "PYTHONPATH": str(REPO / "vendor"),
        "TMPDIR": str(root),
        "TEXMFVAR": str(root / "texmf-var"),
        "TEXMFCONFIG": str(root / "texmf-config"),
        "SLCN_COMPILEFIX_STATE_DIR": str(root / "state"),
    }
    for key, value in values.items():
        os.environ[key] = value
    for key in ("TMPDIR", "TEXMFVAR", "TEXMFCONFIG", "SLCN_COMPILEFIX_STATE_DIR"):
        pathlib.Path(values[key]).mkdir(parents=True, exist_ok=True)


def _fail_fixture(root: pathlib.Path, *, overlap: bool = False):
    """Source whose body uses an undefined env; real XeLaTeX fails.

    The source dir is separated from out so capture_compile() binds (unless
    overlap=True, which deliberately yields capture=None for the safety test).
    """
    src_dir = root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "main.tex"
    src.write_text(r"""\documentclass{article}
\begin{document}
\begin{firstenv}
inside.
\end{firstenv}
\ifdefined\firstenv
\othermacro marker.
\fi
\end{document}
""", encoding="utf-8")
    (src_dir / "probe.sty").write_text(
        ENV_DECL + "\n" + MACRO_DECL + "\n" + EXTRA_DECL + "\n",
        encoding="utf-8")
    out = (src_dir / "out") if overlap else (root / "out")
    return src, out


def _repair_source(root: pathlib.Path) -> pathlib.Path:
    r"""A source whose preamble \usepackage{probe} exposes the declarations."""
    src = root / "src" / "repair.tex"
    src.write_text(r"""\documentclass{article}
\usepackage{probe}
\begin{document}
Main text.
\end{document}
""", encoding="utf-8")
    return src


def _run_cli(src, out, *args):
    old = sys.argv
    sys.argv = ["convert.py", "--source", str(src), "--out", str(out),
                "--name", "demo", "--lang", "en", "--force", "--no-original",
                "--compile", "--no-bibdb", "--verify-max", "0", *args]
    so, se = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            try:
                rc = convert.main()
            except SystemExit as exc:
                rc = exc.code
    finally:
        sys.argv = old
    return rc, so.getvalue(), se.getvalue()


def _last_stdout(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


class _CliCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        if shutil.which("xelatex") is None or shutil.which("biber") is None:
            self.skipTest("BLOCKED: XeLaTeX/Biber unavailable")
        self.tmp = tempfile.mkdtemp(prefix="slcn-cli-stitch-", dir="/tmp")
        self.root = pathlib.Path(self.tmp)
        _set_env(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestCliStitch(_CliCase):

    def test_cli_single_round_failure_invokes_staged_loop(self):
        src, out = _fail_fixture(self.root)
        staged, exports = [], []

        def fake_loop(out_dir, stem, *, source, capture, candidate, report,
                      max_rounds=3):
            staged.append(dict(out_dir=str(out_dir), stem=stem, source=source,
                               capture=capture, candidate=candidate,
                               report=report, max_rounds=max_rounds))
            return False, "multiround-failed", capture

        def fake_export(out_dir, stem, message, *, source, capture,
                        stage_log=None):
            exports.append(dict(message=message, capture=capture, source=source))

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop), \
             patch.object(cfmod, "export_failure", side_effect=fake_export):
            rc, so, se = _run_cli(src, out)

        self.assertEqual(len(staged), 1, "staged loop must be called exactly once")
        call = staged[0]
        self.assertEqual(call["candidate"], (out / "demo" / "demo.tex").read_bytes())
        self.assertIsNotNone(call["capture"])
        self.assertEqual(call["source"], str(src))
        self.assertEqual(call["stem"], "demo")
        self.assertEqual(len(exports), 1)
        self.assertIs(exports[0]["capture"], call["capture"],
                      "export_failure must receive the multiround-returned capture")

    def test_cli_staged_success_publishes_once_and_returns_zero(self):
        # Known-good multiround fixture (real XeLaTeX, no compiler mocks):
        # plain source with \usepackage{probe}; gated chained body in candidate;
        # three declarations in the local support file.
        src, out = _fail_fixture(self.root)
        repair_src = _repair_source(self.root)
        good = TEMPLATE.replace("SKELMARK", BODY).encode("utf-8")
        (out / "demo").mkdir(parents=True, exist_ok=True)
        cd = out / "demo" / "demo.tex"
        cd.write_bytes(good)
        work = self.root / "dry"
        work.mkdir(exist_ok=True)
        (work / "demo.tex").write_bytes(good)
        private = self.root / "private"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="compiler-home-", dir=private) as home:
            proc = subprocess.run(
                ["xelatex", "-no-shell-escape", "-interaction=nonstopmode",
                 "demo.tex"], cwd=work, env=_environment(pathlib.Path(home)),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        self.assertNotEqual(proc.returncode, 0, "fixture must fail before repair")
        fail_raw = proc.stdout[:2 * 1024 * 1024]
        real_loop = l1mod.run_staged_repair_loop
        real_apply = l1mod.apply_plan
        apply_calls = []

        def staged_spy(out_dir, stem, *, source, capture, candidate, report,
                       max_rounds=3):
            cd.write_bytes(good)
            # Bind the capture AFTER the CLI rebuilt the candidate so its
            # stamp matches the bytes apply_plan verifies.
            bound = capture_compile(out_dir, stem, source=repair_src)
            self.assertIsNotNone(bound)
            bound["failure"] = ("c" * 32, 1, proc.returncode, fail_raw)
            fresh = diagnose(good, bound, available_paths={stem + ".tex"})
            return real_loop(out_dir, stem, source=repair_src, capture=bound,
                             candidate=good, report=fresh, max_rounds=max_rounds)

        def apply_spy(*args, **kwargs):
            apply_calls.append(kwargs["plan"])
            return real_apply(*args, **kwargs)

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=staged_spy),              patch.object(l1mod, "apply_plan", side_effect=apply_spy):
            rc, so, se = _run_cli(src, out)

        self.assertEqual(len(apply_calls), 1, "must publish exactly once")
        self.assertEqual(rc, 0, se[-1200:])
        self.assertEqual(_last_stdout(so), "完成。产物目录：" + str(out / "demo"))

    def test_cli_staged_failure_keeps_contract_and_exports_failure(self):
        src, out = _fail_fixture(self.root)
        marker = {"marker": True, "candidate_sha256": "deadbeef"}
        exports = []
        original_capture = []  # capture handed to the staged loop by the CLI

        def fake_loop(out_dir, stem, *, source, capture, candidate, report,
                      max_rounds=3):
            original_capture.append(capture)
            return False, "multiround-failed", marker

        def fake_export(out_dir, stem, message, *, source, capture,
                        stage_log=None):
            exports.append(dict(message=message, capture=capture))

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop), \
             patch.object(cfmod, "export_failure", side_effect=fake_export):
            rc, so, se = _run_cli(src, out)

        self.assertEqual(rc, 2)
        self.assertIn("退出码 2", se)
        self.assertEqual(_last_stdout(so), "完成。产物目录：" + str(out / "demo"))
        self.assertEqual(len(exports), 1)
        self.assertEqual(len(original_capture), 1)
        self.assertIs(exports[0]["capture"], original_capture[0],
                      "export_failure must keep the original capture on staged failure")
        self.assertNotEqual(exports[0]["message"], "multiround-failed",
                            "staged failure must not overwrite the original failure message")

    def test_cli_default_engages_staged_loop_on_failure(self):
        src, out = _fail_fixture(self.root)
        staged = []

        def fake_loop(out_dir, stem, *, source, capture, candidate, report,
                      max_rounds=3):
            staged.append(dict(source=source, capture=capture,
                               candidate=candidate, max_rounds=max_rounds))
            return False, "mrfail", capture

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop):
            rc, so, se = _run_cli(src, out)

        self.assertEqual(len(staged), 1,
                         "default --compile failure must engage staged repair")
        self.assertIsNotNone(staged[0]["capture"])
        self.assertEqual(rc, 2)

    def test_cli_optout_no_compile_multiround_unchanged(self):
        src, out = _fail_fixture(self.root)
        staged, exports = [], []

        def fake_loop(*args, **kwargs):
            staged.append(kwargs)
            return False, "multiround-failed", kwargs["capture"]

        def fake_export(out_dir, stem, message, *, source, capture,
                        stage_log=None):
            exports.append(dict(message=message, capture=capture, source=source))

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop), \
             patch.object(cfmod, "export_failure", side_effect=fake_export):
            rc, so, se = _run_cli(src, out, "--no-compile-multiround")

        self.assertEqual(staged, [], "--no-compile-multiround must disable staged repair")
        self.assertEqual(rc, 2)
        self.assertEqual(_last_stdout(so), "完成。产物目录：" + str(out / "demo"))
        self.assertIn("退出码 2", se)
        self.assertEqual(len(exports), 1)
        self.assertIsNotNone(exports[0]["capture"])
        self.assertIn("xelatex 第 1 步失败", exports[0]["message"])

    def test_cli_coop_taskbook_only_never_staged(self):
        src, out = _fail_fixture(self.root)
        staged = []

        def fake_loop(*args, **kwargs):
            staged.append(kwargs)
            return False, "multiround-failed", kwargs["capture"]

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop):
            rc, so, se = _run_cli(src, out, "--coop-taskbook-only")

        self.assertEqual(staged, [], "coop taskbook-only forbids staged repair")
        self.assertIn("暂存目录：" + str(out / "demo"), so)

    def test_compile_with_l1_bytes_unchanged(self):
        raw = (REPO / "slcn" / "compilefix" / "l1.py").read_bytes()
        segment = b"".join(raw.splitlines(keepends=True)[75:178])
        self.assertEqual(len(segment), 5859)
        self.assertEqual(hashlib.sha256(segment).hexdigest(), EXPECTED_HASH)

    def test_cli_staged_report_rebuilt_with_l1_recipe(self):
        src, out = _fail_fixture(self.root)
        events = []
        real_diagnose = l1mod.diagnose

        def diag_spy(candidate, capture, **kwargs):
            result = real_diagnose(candidate, capture, **kwargs)
            events.append((candidate, dict(kwargs), result))
            return result

        staged = []

        def loop_spy(out_dir, stem, *, source, capture, candidate, report,
                     max_rounds=3):
            staged.append((candidate, report))
            return False, "mrfail", capture

        with patch.object(l1mod, "diagnose", side_effect=diag_spy), \
             patch.object(l1mod, "run_staged_repair_loop", side_effect=loop_spy):
            _run_cli(src, out)

        self.assertEqual(len(staged), 1)
        candidate, report = staged[0]
        matches = [e for e in events if e[2] is report]
        self.assertEqual(len(matches), 1,
                         "staged loop must receive a diagnose-built report")
        diag_candidate, kwargs, _ = matches[0]
        self.assertEqual(diag_candidate, candidate)
        self.assertEqual(candidate, (out / "demo" / "demo.tex").read_bytes())

        path = out / "demo"
        try:
            expected_checks = template_results(candidate, path, "demo")
        except (OSError, RepairError, ValueError, UnicodeError):
            expected_checks = ()
        self.assertEqual(kwargs.get("template_results"), expected_checks)

        try:
            expected_available = set(support_manifest(path, "demo")) | {"demo.tex"}
        except (OSError, RepairError):
            expected_available = None
        self.assertEqual(kwargs.get("available_paths"), expected_available)

    def test_cli_multiround_default_on_and_env_ignored(self):
        ap = build_arg_parser()
        dests = {action.dest for action in ap._actions}
        self.assertIn("compile_multiround", dests,
                      "--no-compile-multiround must be registered in build_arg_parser")
        ns = ap.parse_args(["--source", "dummy.tex"])
        self.assertTrue(getattr(ns, "compile_multiround", False))
        off = ap.parse_args(["--source", "dummy.tex", "--no-compile-multiround"])
        self.assertFalse(off.compile_multiround)

        src, out = _fail_fixture(self.root)
        staged = []

        def fake_loop(*args, **kwargs):
            staged.append(kwargs)
            return False, "mrfail", kwargs["capture"]

        for val in ("1", "0", ""):
            os.environ["SLCN_COMPILEFIX_CLI_MULTIROUND"] = val
            with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop):
                _run_cli(src, out, "--no-compile-multiround")
        self.assertEqual(staged, [], "env var must not re-enable staged repair")

    def test_cli_max_rounds_never_reads_round_limit_env(self):
        src, out = _fail_fixture(self.root)
        seen = []

        def fake_loop(out_dir, stem, *, source, capture, candidate, report,
                      max_rounds=3):
            seen.append(max_rounds)
            return False, "mrfail", capture

        os.environ["SLCN_COMPILEFIX_ROUND_LIMIT"] = "99"
        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop):
            _run_cli(src, out)

        self.assertEqual(seen, [3], "max_rounds must stay fixed at 3")

    def test_cli_staged_capture_and_shareable_contract(self):
        src, out = _fail_fixture(self.root)
        called, exports = [], []

        def fake_loop(out_dir, stem, *, source, capture, candidate, report,
                      max_rounds=3):
            called.append(True)
            return False, "mrfail", capture

        def fake_export(out_dir, stem, message, *, source, capture,
                        stage_log=None):
            exports.append(capture)

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop), \
             patch.object(cfmod, "export_failure", side_effect=fake_export):
            rc, so, se = _run_cli(src, out)

        self.assertTrue(called, "capture contract must be checked on staged path")
        self.assertEqual(rc, 2)
        self.assertTrue(exports)
        l1 = exports[0].get("l1")
        self.assertIsNotNone(l1)
        self.assertEqual(set(l1), SHAREABLE_KEYS)
        dumped = json.dumps(l1, ensure_ascii=False)
        self.assertNotIn('"subject"', dumped)
        self.assertNotIn('"round"', dumped)
        for attempt in l1["attempts"]:
            self.assertEqual(set(attempt), {"rule", "result"})

    def test_cli_capture_none_safety_precondition(self):
        ok, info, cap = l1mod.staged_repair_after_failure(
            "/tmp/nonexistent-cli-stitch", "demo", source="/tmp/x.tex",
            capture=None)
        self.assertFalse(ok)
        self.assertEqual(info, "L1 多轮暂存未启用：本轮无绑定 capture。")
        self.assertIsNone(cap)

        src, out = _fail_fixture(self.root, overlap=True)
        staged = []

        def fake_loop(*args, **kwargs):
            staged.append(kwargs)
            return False, "mrfail", kwargs["capture"]

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop):
            rc, so, se = _run_cli(src, out)

        self.assertEqual(staged, [], "capture=None must never enter staged repair")
        self.assertEqual(rc, 2)
        self.assertEqual(_last_stdout(so), "完成。产物目录：" + str(out / "demo"))


    def test_cli_no_edit_convergence_degrades_to_failure(self):
        """H1 RED: staged loop's no-edit convergence yields a failure string as
        'published'; the CLI must take the rc=2 failure path, not crash."""
        src, out = _fail_fixture(self.root)
        exports = []

        def fake_loop(out_dir, stem, *, source, capture, candidate, report,
                      max_rounds=3):
            return (True, "L1 未修复：多轮暂存未收敛；请人工检查本地日志。",
                    capture)

        def fake_export(out_dir, stem, message, *, source, capture,
                        stage_log=None):
            exports.append(dict(message=message, capture=capture))

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop), \
             patch.object(cfmod, "export_failure", side_effect=fake_export):
            rc, so, se = _run_cli(src, out)

        self.assertEqual(rc, 2,
                         "no-edit convergence must degrade to the rc=2 failure path")
        self.assertIn("退出码 2", se)
        self.assertEqual(_last_stdout(so), "完成。产物目录：" + str(out / "demo"))
        self.assertEqual(len(exports), 1,
                         "export_failure must be called on degraded failure")

    def test_cli_staged_success_with_real_pdf_is_preserved(self):
        """H1 counter-test: a real non-empty PDF path must still succeed."""
        src, out = _fail_fixture(self.root)
        pdf = self.root / "published.pdf"
        pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 1024 + b"\n%%EOF\n")
        exports = []

        def fake_loop(out_dir, stem, *, source, capture, candidate, report,
                      max_rounds=3):
            return True, str(pdf), capture

        def fake_export(out_dir, stem, message, *, source, capture,
                        stage_log=None):
            exports.append(dict(message=message, capture=capture))

        with patch.object(l1mod, "run_staged_repair_loop", side_effect=fake_loop), \
             patch.object(cfmod, "export_failure", side_effect=fake_export):
            rc, so, se = _run_cli(src, out)

        self.assertEqual(rc, 0, se[-1200:])
        self.assertIn("published.pdf", so)
        self.assertIn("1 KB", so)
        self.assertEqual(exports, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
