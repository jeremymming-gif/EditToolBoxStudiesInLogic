"""2A B2A RED tests: stage-localized recovery/export failure presentation.

TDD RED phase: the production APIs below do not yet accept ``stage_log`` /
``planning_evidence``.  Every reference to a new symbol or new behaviour lives
*inside* a test method, so importing this module never crashes.

Scope (batch 2A, subagent B):
  * B2A-4 recover boundary injection / stage_log / compile_with_l1 presentation;
  * B2A-6 export_failure boundary injection / ID truthfulness / stdout contract.

Only this test file may be written; production code and git are untouched.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import slcn.compilefix as cf
import slcn.compilefix.l1 as l1_mod
import slcn.compilefix.l1_transaction as tx
import slcn.compilefix.transaction as txn
from slcn.compilefix.compiler import CompileResult
from slcn.compilefix.files import RepairError
from slcn.compilefix.l1_diagnostics import diagnose
from slcn.compilefix.l1_rules import select_plan
from slcn.compilefix.l1_transaction import RecoveryRequired
from slcn.compilefix.state import Store, capture_compile

RAW = (
    b"\\documentclass{article}\n"
    b"\\newcommand{\\Demo}{\\@gobble{discard}Kept}\n"
    b"\\begin{document}\n\\Demo\n\\end{document}\n"
)
LOG = (
    b"! You can't use `\\spacefactor' in vertical mode.\n"
    b"\\Demo ->\\@\n"
    b"l.4 \\Demo\n"
)

INFO_RECOVERY_FAILURE = "L1 恢复未完成；请人工检查受限事务材料"
INFO_ROLLBACK_FAILURE = "L1 回滚未完成，禁止交付；请人工检查受限事务材料"
FAKE_ID = "f" * 64


class _BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "source").mkdir()
        # source is a FILE, exactly as in TransactionTests.setUp.
        self.source = self.root / "source" / "incoming.tex"
        self.source.write_bytes(RAW)
        self.out = self.root / "out"
        self.out.mkdir()
        (self.out / "demo.tex").write_bytes(RAW)
        (self.out / "demo.pdf").write_bytes(b"OLD-PDF")
        (self.out / "demo.log").write_bytes(LOG)
        # compile_pdf is called by compile_with_capture, which appends the
        # capture itself and returns a 3-tuple; a 2-tuple outcome is correct.
        self.compiler = MagicMock(return_value=(True, ""))

    def _capture(self):
        capture = capture_compile(self.out, "demo", source=self.source)
        self.assertIsNotNone(capture)
        capture["failure"] = ("c" * 32, 1, 1, LOG)
        return capture

    def _plan(self, capture):
        report = diagnose(RAW, capture)
        self.assertEqual(
            report.identity,
            "a45c351cdee573e3b643c73f9f878e99cdb1c7cf3e0541943c2ebe0093df7832",
        )
        plan = select_plan(RAW, report)
        self.assertIsNotNone(plan)
        return plan

    @staticmethod
    def _compiler(cwd, stem, expected, support, private, **kwargs):
        """Passing synthetic compiler (same shape as TransactionTests.compiler)."""
        from pypdf import PdfWriter
        from slcn.compilefix.files import digest

        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        stream = io.BytesIO()
        writer.write(stream)
        pdf = stream.getvalue()
        for ext, data in ((".pdf", pdf), (".log", b"fresh synthetic log"),
                          (".fls", b"fresh recorder")):
            (cwd / (stem + ext)).write_bytes(data)
        return CompileResult("passed", expected, "f" * 32, "bind", 0,
                             digest(pdf), digest(b"fresh synthetic log"),
                             digest(b"fresh recorder"))

    def _publish(self):
        """Leave an interrupted transaction marker for recover() to find."""
        # apply_plan may have published the patched .tex into out/ on a previous
        # call; reset the fixture so capture_compile() stays bound to RAW.
        (self.out / "demo.tex").write_bytes(RAW)
        (self.out / "demo.pdf").write_bytes(b"OLD-PDF")
        (self.out / "demo.log").write_bytes(LOG)
        marker = tx._guard_path(self.out, "demo")
        if marker.is_file():
            marker.unlink()
        capture = self._capture()
        report = diagnose(RAW, capture)
        plan = self._plan(capture)

        def checkpoint(phase):
            if phase == "published-tex":
                raise OSError("simulated interruption after publication")

        with patch.object(tx, "_rollback", side_effect=RepairError("simulated recovery block")):
            with self.assertRaises(RecoveryRequired):
                tx.apply_plan(
                    self.out, "demo", source=self.source, capture=capture,
                    plan=plan, report=report, compiler=self._compiler,
                    checkpoint=checkpoint,
                )
        self.assertTrue(tx._guard_path(self.out, "demo").is_file())

    def _recover(self, stage_log=None):
        """Call recover with the NEW optional stage_log keyword (inside test methods)."""
        return tx.recover(self.out, "demo", source=self.source, stage_log=stage_log)


# ---------------------------------------------------------------------------
# B2A-4 / A2A-2: recover stage_log + compile_with_l1 presentation
# ---------------------------------------------------------------------------
@unittest.skipUnless(sys.platform == "darwin", "产物原子交换事务仅 macOS 可用")
class RecoveryStageLogTests(_BaseCase):
    """Each boundary failure must be attributed to exactly the promised label."""

    def test_boundaries_record_expected_stage_labels(self):
        cases = [
            ("directory", "产物目录", OSError("dir-open failed")),
            ("_store", "恢复区", RepairError("store failed")),
            ("_guard", "标记归属", RepairError("guard failed")),
            ("_check_directory", "产物目录身份", RepairError("identity failed")),
            ("_finish_pending", "协调中断", OSError("pending failed")),
            ("_rollback", "回滚", OSError("rollback failed")),
        ]
        for target, label, exc in cases:
            with self.subTest(target=target):
                self._publish()
                log = []
                with patch.object(tx, target, side_effect=exc):
                    with self.assertRaises((OSError, RepairError)):
                        self._recover(log)
                self.assertIn(label, log, f"{target} should log stage {label}")
                self.assertEqual(log[-1], label, f"{target} last stage must be {label}")

    def test_state_read_and_lock_and_save_boundaries(self):
        # 任务状态读取: fail only the state.json read, not the marker read.
        self._publish()
        marker = tx._guard_path(self.out, "demo")
        real_read_json = tx.read_json

        def selective_read(path, *args, **kwargs):
            if str(path).endswith("state.json"):
                raise OSError("state read failed")
            return real_read_json(path, *args, **kwargs)

        log = []
        with patch.object(tx, "read_json", side_effect=selective_read):
            with self.assertRaises(OSError):
                self._recover(log)
        self.assertIn("任务状态读取", log)
        self.assertEqual(log[-1], "任务状态读取")
        self.assertTrue(marker.is_file())

        # 取得锁：OSError 和 RepairError 两种都要归因同一阶段
        for exc in (OSError("flock failed"), RepairError("取得锁时目标身份变化")):
            with self.subTest(exc=type(exc).__name__):
                self._publish()
                log = []
                with patch.object(tx, "_output_lock", side_effect=exc):
                    with self.assertRaises((OSError, RepairError)):
                        self._recover(log)
                self.assertIn("取得锁", log)
                self.assertEqual(log[-1], "取得锁")

        # 保存恢复状态：patch the class method so the real store (and its
        # .root) is preserved — wrapping in a MagicMock would break _guard().
        self._publish()
        log = []
        with patch.object(type(tx._store(self.out, "demo", self.source)),
                          "save", side_effect=OSError("save failed")):
            with self.assertRaises(OSError):
                self._recover(log)
        self.assertIn("保存恢复状态", log)
        self.assertEqual(log[-1], "保存恢复状态")

    def test_no_guard_returns_successfully_without_later_stages(self):
        log = []
        result = self._recover(log)  # no transaction marker
        self.assertIsNone(result)
        self.assertIn("产物目录", log)
        self.assertIn("事务标记", log)
        self.assertNotIn("回滚", log)
        self.assertNotIn("取得锁", log)

        compile_pdf = MagicMock(return_value=(True, ""))
        ok, info, cap = l1_mod.compile_with_l1(
            compile_pdf, self.out, "demo", source=self.source
        )
        self.assertTrue(ok)
        self.assertEqual(compile_pdf.call_count, 1)

    def test_unattributable_runtime_error_does_not_fabricate_stage(self):
        self._publish()
        log = []  # no stage recorded at all
        with patch.object(tx, "_rollback", side_effect=RuntimeError("weird")):
            with self.assertRaises(RuntimeError):
                self._recover(log)
        self.assertEqual(log, [])  # cannot attribute -> no fabrication

    def test_lock_repair_error_is_attributed_to_lock_stage(self):
        self._publish()
        log = []
        with patch.object(txn, "lock", side_effect=RepairError("取得锁时目标身份变化")):
            with self.assertRaises(RepairError):
                self._recover(log)
        self.assertIn("取得锁", log)
        self.assertEqual(log[-1], "取得锁")

    def test_context_manager_exit_failure_uses_exit_or_unknown_stage(self):
        self._publish()

        class _BadContext:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                raise OSError("unlock failed")

        log = []
        with patch.object(tx, "_output_lock", return_value=_BadContext()):
            with self.assertRaises(OSError):
                self._recover(log)
        # Must never misreport the last successful step as the failure point.
        self.assertNotEqual(log[-1], "保存恢复状态")
        self.assertIn(log[-1], ("退出恢复操作", "阶段未定"))

    def test_recover_failure_stage_is_printed_and_compiler_never_runs(self):
        boundaries = [
            ("directory", "产物目录", OSError("dir-open failed")),
            ("_store", "恢复区", RepairError("store failed")),
            ("_guard", "标记归属", RepairError("guard failed")),
            ("_check_directory", "产物目录身份", RepairError("identity failed")),
            ("_finish_pending", "协调中断", OSError("pending failed")),
            ("_rollback", "回滚", OSError("rollback failed")),
        ]
        for target, label, exc in boundaries:
            with self.subTest(target=target):
                self.compiler.reset_mock()

                def boom(*args, _label=label, _exc=exc, **kwargs):
                    # GREEN implementation is expected to hand a stage_log to recover;
                    # mark the failing boundary so the hint can be localized.
                    log = kwargs.get("stage_log")
                    if log is not None:
                        log.append(_label)
                    raise _exc

                err = io.StringIO()
                with patch.object(l1_mod, "recover", side_effect=boom):
                    with contextlib.redirect_stderr(err):
                        ok, info, cap = l1_mod.compile_with_l1(
                            self.compiler, self.out, "demo", source=self.source
                        )
                self.assertFalse(ok)
                self.assertEqual(info, INFO_RECOVERY_FAILURE)
                self.assertIsNone(cap)
                self.assertEqual(self.compiler.call_count, 0)
                self.assertIn(label, err.getvalue())

    def test_compile_with_l1_accepts_planning_evidence_keyword(self):
        evidence = MagicMock()
        self.compiler.reset_mock()
        ok, info, cap = l1_mod.compile_with_l1(
            self.compiler, self.out, "demo", source=self.source,
            planning_evidence=(None, evidence),
        )
        self.assertTrue(ok, info)
        self.assertEqual(self.compiler.call_count, 1)
        self.assertEqual(info, "")

    def test_post_publish_rollback_failure_keeps_independent_info(self):
        capture = self._capture()
        err = io.StringIO()
        with patch.object(cf, "compile_with_capture",
                          return_value=(False, "synthetic failure", capture)):
            with patch.object(l1_mod, "apply_plan",
                              side_effect=RecoveryRequired("rollback failed")):
                with contextlib.redirect_stderr(err):
                    ok, info, cap = l1_mod.compile_with_l1(
                        self.compiler, self.out, "demo", source=self.source,
                    )
        self.assertFalse(ok)
        self.assertEqual(info, INFO_ROLLBACK_FAILURE)
        self.assertIn("L1 回滚未完成", err.getvalue())
        self.assertNotIn("编译尚未启动", err.getvalue())


# ---------------------------------------------------------------------------
# B2A-6 / A2A-4: export_failure stage_log + ID truthfulness
# ---------------------------------------------------------------------------
class _ExportCase(_BaseCase):
    """Common helpers for export_failure injection tests."""

    def _capture(self):
        return {
            "failure": (FAKE_ID[:32], 1, 1, LOG),
            "manifest": {"evidence": "stub"},
        }

    def _assert_stdout_contract(self, stdout):
        self.assertIn(FAKE_ID, stdout)
        self.assertIn("SHA-256", stdout)
        self.assertIn("本地保留 30 天", stdout)
        self.assertRegex(
            stdout,
            r"编译修复任务书 " + FAKE_ID + r" · SHA-256 \S+ · 本地保留 30 天\s*$",
        )

    def _assert_no_fake_id(self, stderr):
        self.assertNotRegex(stderr, r"[0-9a-f]{64}")
        self.assertTrue(
            "签发阶段" in stderr or "未取得已确认的 ID" in stderr,
            f"pre-issue failure must say issue-stage/no-ID, got: {stderr!r}",
        )

    def _assert_post_issue_failure(self, stderr, label):
        self.assertIn("任务书已签发", stderr)
        self.assertIn(FAKE_ID, stderr)
        self.assertIn(label, stderr)


class ExportStageLogTests(_ExportCase):
    def test_stage_log_before_issue_never_reports_confirmed_id(self):
        cases = [
            ("store_init", "建Store", OSError("store init failed")),
            ("validate_locations", "位置校验", RepairError("location failed")),
            ("issue", "签发", OSError("issue failed")),
        ]
        for target, label, exc in cases:
            with self.subTest(target=target):
                log = []
                err = io.StringIO()
                if target == "store_init":
                    ctx = patch.object(cf, "Store", side_effect=exc)
                else:
                    ctx = patch.object(Store, target, side_effect=exc)
                with ctx:
                    with contextlib.redirect_stderr(err):
                        result = cf.export_failure(
                            self.out, "demo", "msg", source=self.source,
                            capture=self._capture(), stage_log=log,
                        )
                self.assertIsNone(result)
                self.assertIn(label, log)
                self.assertEqual(log[-1], label)
                self.assertIn("编译修复任务书未导出", err.getvalue())
                self._assert_no_fake_id(err.getvalue())

    def test_round_limit_parse_and_state_operation_boundaries(self):
        cases = [
            ("round_limit", "轮次配置解析",
             lambda: patch.dict(os.environ, {"SLCN_COMPILEFIX_ROUND_LIMIT": "NaN"})),
            ("operation", "进入状态操作",
             lambda: patch.object(Store, "operation",
                                  side_effect=OSError("open state failed"))),
        ]
        for target, label, setup in cases:
            with self.subTest(target=target):
                log = []
                err = io.StringIO()
                with setup():
                    with contextlib.redirect_stderr(err):
                        result = cf.export_failure(
                            self.out, "demo", "msg", source=self.source,
                            capture=self._capture(), stage_log=log,
                        )
                self.assertIsNone(result)
                self.assertIn(label, log)
                self.assertEqual(log[-1], label)
                self.assertIn("编译修复任务书未导出", err.getvalue())
                self._assert_no_fake_id(err.getvalue())

    def test_post_issue_failures_report_id_and_taskbook_issued(self):
        # 签发信息输出、预览写出、预览登记、退出状态操作
        cases = [
            ("info_output", "签发信息输出",
             lambda: patch.object(cf, "digest", side_effect=OSError("digest failed"))),
            ("write_stdout", "预览写出",
             lambda: patch.object(cf, "write_stdout", side_effect=OSError("stdout failed"))),
            ("record_preview", "预览登记",
             lambda: patch.object(Store, "record_preview", side_effect=OSError("preview failed"))),
            ("operation_exit", "退出状态操作", _operation_exit_setup),
        ]
        for target, label, setup in cases:
            with self.subTest(target=target):
                log = []
                err = io.StringIO()
                with patch.object(Store, "issue", return_value=(FAKE_ID, b"FAKE-RAW")):
                    with setup():
                        with contextlib.redirect_stderr(err):
                            result = cf.export_failure(
                                self.out, "demo", "msg", source=self.source,
                                capture=self._capture(), stage_log=log,
                            )
                self.assertIsNone(result)
                self.assertIn(label, log)
                self.assertIn(f"签发后确认 ID {FAKE_ID}", log)
                self.assertIn("编译修复任务书未导出", err.getvalue())
                self._assert_post_issue_failure(err.getvalue(), label)

    def test_keyboard_interrupt_is_not_swallowed(self):
        log = []
        with patch.object(Store, "validate_locations", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                cf.export_failure(
                    self.out, "demo", "msg", source=self.source,
                    capture=self._capture(), stage_log=log,
                )

    def test_stdout_contract_and_no_capture_branch(self):
        out = io.StringIO()
        err = io.StringIO()
        with patch.object(Store, "issue", return_value=(FAKE_ID, b"FAKE-RAW")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                result = cf.export_failure(
                    self.out, "demo", "msg", source=self.source,
                    capture=self._capture(), stage_log=[],
                )
        self.assertIsNone(result)
        self._assert_stdout_contract(out.getvalue())

        log = []
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = cf.export_failure(
                self.out, "demo", "msg", source=self.source,
                capture=None, stage_log=log,
            )
        self.assertIsNone(result)
        self.assertIn("无capture", log)
        self.assertIn("无法绑定本轮候选", err.getvalue())


def _operation_exit_setup():
    """Make operation.__exit__ fail while everything before it succeeds.

    issue() is mocked, so no real state exists for FAKE_ID; record_preview()
    must also be mocked or it would fail first at 预览登记.
    """

    class _BadContext:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            raise OSError("unlock failed")

    stack = contextlib.ExitStack()
    stack.enter_context(patch.object(Store, "record_preview", return_value=None))
    stack.enter_context(patch.object(Store, "operation", _BadContext))
    return stack


if __name__ == "__main__":
    unittest.main()
