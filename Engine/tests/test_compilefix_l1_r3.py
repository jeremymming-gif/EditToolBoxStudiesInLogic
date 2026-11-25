"""R3 failure injection: local validated hash and persisted preview move together."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from slcn.compilefix.files import digest
from slcn.compilefix.l1_r3 import PreviewSync, recover_preview
from slcn import ai_workflow as workflow, ai_workflow_convert as conv
from test_ai_workflow_delivery_20261118 import NormalConvertTest

RAW = b"\\documentclass{article}\n\\begin{document}\nAnonymous synthetic body.\n\\end{document}\n"
NEW = RAW.replace(b"Anonymous", b"Repaired")


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name).resolve()
        task = self.work / ".aiw" / "synthetic-task"
        task.mkdir(parents=True)
        self.path = task / "candidate.tex"
        self.path.write_bytes(RAW)
        self.state = {"task_id": "synthetic-task", "preview": {"candidate_text": str(self.path), "text_new_sha256": digest(RAW)}}
        self.preview = self.state["preview"]
        self.persisted = copy.deepcopy(self.state)
        self.original = copy.deepcopy(self.state)

    def load(self):
        return copy.deepcopy(self.persisted)

    def save(self, value):
        self.persisted = copy.deepcopy(value)

    def read_candidate(self):
        raw = self.path.read_bytes()
        if digest(raw) != self.state["preview"]["text_new_sha256"]:
            raise ValueError("candidate hash mismatch")
        return raw

    def sync(self, save=None):
        return PreviewSync(self.work, self.state, self.preview, NEW, load_state=self.load,
                           save_state=save or self.save, read_candidate=self.read_candidate)

    def test_success_and_reversible_rollback(self):
        sync = self.sync()
        self.assertEqual(sync.sha256, digest(NEW))
        self.assertEqual(self.persisted["preview"]["text_new_sha256"], digest(NEW))
        self.assertEqual(self.read_candidate(), NEW)
        sync.rollback()
        self.assertEqual(self.persisted, self.original)
        self.assertEqual(self.read_candidate(), RAW)

    def test_state_save_failure_restores_both(self):
        calls = 0
        def fail_once(value):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected state write failure")
            self.save(value)
        with self.assertRaises(OSError):
            self.sync(save=fail_once)
        self.assertEqual(self.path.read_bytes(), RAW)
        self.assertEqual(self.persisted, self.original)

    def test_unsynchronized_candidate_is_rejected(self):
        self.path.write_bytes(NEW)
        with self.assertRaises(ValueError):
            self.read_candidate()
        with self.assertRaises(ValueError):
            self.sync()

    def test_interrupted_sync_recovers_before_another_delivery(self):
        self.sync()
        recover_preview(self.work, load_state=self.load, save_state=self.save)
        self.assertEqual(self.persisted, self.original)
        self.assertEqual(self.path.read_bytes(), RAW)

    def test_delivered_callback_cannot_accept_external_state_mutation(self):
        self.sync()
        self.persisted["external"] = True
        with self.assertRaises(ValueError):
            recover_preview(self.work, load_state=self.load, save_state=self.save,
                            is_delivered=lambda value, sha: True)
        self.assertEqual(self.path.read_bytes(), NEW)
        self.assertTrue(self.persisted["external"])

    def test_external_state_change_blocks_recovery(self):
        self.sync()
        self.persisted["external"] = True
        with self.assertRaises(ValueError):
            recover_preview(self.work, load_state=self.load, save_state=self.save)
        self.assertEqual(self.path.read_bytes(), NEW)
        self.assertTrue(self.persisted["external"])


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = NormalConvertTest("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        state = self.fixture.convert()
        self.work = str(self.fixture.work)
        conversion = state["conversion"]
        stage = Path(conversion["stage_dir"])
        (stage / conversion["tex_name"]).write_bytes(RAW)
        conversion["stage_sha256s"] = conv._tree_shas(str(stage))
        conversion["options"].update(compile=True, source_pdf=False, clean=False)
        task = Path(self.work) / ".aiw" / state["task_id"]
        task.mkdir(exist_ok=True)
        self.candidate = task / "l1-candidate.tex"
        self.candidate.write_bytes(RAW)
        state["active_reply"] = {"sha256": "a" * 64}
        state["preview"] = {"ok": True, "preview_id": "l1-synthetic-preview", "reply_sha256": "a" * 64,
                            "candidate_text": str(self.candidate), "text_new_sha256": digest(RAW),
                            "candidate_bib": "", "bib_new_sha256": digest(b"")}
        workflow.save_state(self.work, state)
        self.before = workflow.load_state(self.work)

    def repaired(self, compile_pdf, candidate, stem, **kwargs):
        (Path(candidate) / (stem + ".tex")).write_bytes(NEW)
        pdf = Path(candidate) / (stem + ".pdf")
        pdf.write_bytes(b"synthetic fresh PDF")
        return True, str(pdf), {"l1_repaired": digest(NEW)}

    def deliver(self):
        return conv.deliver(work_dir=self.work, preview_id="l1-synthetic-preview")

    def test_real_r3_entry_synchronizes_both_hashes_before_delivery(self):
        with patch("slcn.compilefix.compile_with_l1", side_effect=self.repaired), \
                patch.object(workflow, "_now", return_value="2099-01-01T00:00:00"):
            result = self.deliver()
        self.assertTrue(result["ok"], result)
        latest = workflow.load_state(self.work)
        self.assertEqual(latest["preview"]["text_new_sha256"], digest(NEW))
        self.assertEqual(self.candidate.read_bytes(), NEW)
        self.assertEqual((Path(result["final"]) / self.before["conversion"]["tex_name"]).read_bytes(), NEW)

    def test_existing_hash_guard_rejects_changed_tex_without_sync(self):
        def unsynchronized(*args, **kwargs):
            ok, path, _ = self.repaired(*args, **kwargs)
            return ok, path, {}
        with patch("slcn.compilefix.compile_with_l1", side_effect=unsynchronized):
            result = self.deliver()
        self.assertFalse(result["ok"], result)
        self.assertIn("候选文件被修改", result["error"])
        self.assertEqual(workflow.load_state(self.work), self.before)

    def test_downstream_failure_restores_preview_and_state(self):
        with patch("slcn.compilefix.compile_with_l1", side_effect=self.repaired), \
                patch.object(conv, "_render_check_note", side_effect=ValueError("injected downstream failure")):
            result = self.deliver()
        self.assertFalse(result["ok"], result)
        self.assertIn("injected downstream", result["error"])
        self.assertEqual(workflow.load_state(self.work), self.before)
        self.assertEqual(self.candidate.read_bytes(), RAW)
        self.assertFalse(self.fixture.final.exists())

    def test_commit_marker_failure_after_receipt_keeps_delivery_and_recovers_preview(self):
        with patch("slcn.compilefix.compile_with_l1", side_effect=self.repaired), \
                patch.object(PreviewSync, "commit", side_effect=OSError("injected commit marker failure")):
            result = self.deliver()
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["delivered"], result)
        self.assertIn("injected commit marker", result["error"])
        delivered = workflow.load_state(self.work)
        self.assertEqual(delivered["phase"], "delivered")
        self.assertEqual(self.candidate.read_bytes(), NEW)
        self.assertEqual((self.fixture.final / delivered["conversion"]["tex_name"]).read_bytes(), NEW)
        receipt = conv._read_journal(conv._journal_path(self.work))
        self.assertTrue(receipt["delivered"])
        self.assertFalse(receipt["promoting"])
        sync_path = Path(self.work) / ".aiw" / "l1-preview-recovery.json"
        self.assertEqual(conv.json.loads(sync_path.read_text(encoding="utf-8"))["state"], "syncing")

        conv._recover_compile_preview(self.work)
        self.assertEqual(conv.json.loads(sync_path.read_text(encoding="utf-8"))["state"], "completed")
        self.assertEqual(workflow.load_state(self.work), delivered)

    def test_delivered_state_write_failure_after_receipt_keeps_delivery_and_recovers_preview(self):
        original_save = workflow.save_state
        failed = False
        def save_then_fail(work, state):
            nonlocal failed
            original_save(work, state)
            if state.get("phase") == "delivered" and not failed:
                failed = True
                raise OSError("injected delivered state failure")
        with patch("slcn.compilefix.compile_with_l1", side_effect=self.repaired), \
                patch.object(workflow, "save_state", side_effect=save_then_fail), \
                patch.object(workflow, "_now", return_value="2099-01-01T00:00:00"):
            result = self.deliver()
        self.assertTrue(failed)
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["delivered"], result)
        delivered = workflow.load_state(self.work)
        self.assertEqual(delivered["phase"], "delivered")
        self.assertEqual(delivered["delivery"], conv._read_journal(conv._journal_path(self.work)))
        self.assertEqual(self.candidate.read_bytes(), NEW)
        self.assertEqual((self.fixture.final / delivered["conversion"]["tex_name"]).read_bytes(), NEW)
        sync_path = Path(self.work) / ".aiw" / "l1-preview-recovery.json"
        self.assertEqual(conv.json.loads(sync_path.read_text(encoding="utf-8"))["state"], "syncing")

        conv._recover_compile_preview(self.work)
        self.assertEqual(conv.json.loads(sync_path.read_text(encoding="utf-8"))["state"], "completed")
        self.assertEqual(workflow.load_state(self.work), delivered)

    def test_crash_after_completed_journal_recovers_synchronized_delivery(self):
        with patch("slcn.compilefix.compile_with_l1", side_effect=self.repaired), \
                patch.object(conv, "_record_delivery_state", side_effect=SystemExit(94)):
            with self.assertRaises(SystemExit):
                self.deliver()
        self.assertTrue(conv._read_journal(conv._journal_path(self.work))["delivered"])
        self.assertNotEqual(workflow.load_state(self.work)["phase"], "delivered")
        result = self.deliver()
        self.assertFalse(result["ok"], result)
        self.assertIn("任务已经交付", result["error"])
        latest = workflow.load_state(self.work)
        self.assertEqual(latest["phase"], "delivered")
        self.assertEqual(latest["preview"]["text_new_sha256"], digest(NEW))
        self.assertEqual(conv._read_candidate(self.work, latest, latest["preview"], kind="tex"), NEW)
        self.assertEqual((self.fixture.final / latest["conversion"]["tex_name"]).read_bytes(), NEW)

    def test_crash_after_delivery_state_first_undo_recovers_preview_and_delivery(self):
        with patch("slcn.compilefix.compile_with_l1", side_effect=self.repaired), \
                patch.object(PreviewSync, "commit", side_effect=SystemExit(95)):
            with self.assertRaises(SystemExit):
                self.deliver()

        result = conv.undo_delivery(work_dir=self.work)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["undone"], result)
        state = workflow.load_state(self.work)
        self.assertEqual(state["phase"], "undone")
        self.assertFalse(self.fixture.final.exists())
        self.assertEqual((Path(result["undo_keep"]) / state["conversion"]["tex_name"]).read_bytes(), NEW)
        self.assertEqual(self.candidate.read_bytes(), NEW)
        self.assertTrue(conv._read_journal(conv._journal_path(self.work))["undone"])
        sync_path = Path(self.work) / ".aiw" / "l1-preview-recovery.json"
        self.assertEqual(conv.json.loads(sync_path.read_text(encoding="utf-8"))["state"], "completed")

    def test_compile_failure_keeps_r3_valueerror_path_and_original_preview(self):
        with patch("slcn.compilefix.compile_with_l1", return_value=(False, "synthetic compile failure", {})):
            result = self.deliver()
        self.assertFalse(result["ok"], result)
        self.assertIn("编译失败：synthetic compile failure", result["error"])
        self.assertEqual(workflow.load_state(self.work), self.before)
        self.assertEqual(self.candidate.read_bytes(), RAW)


if __name__ == "__main__":
    # Imported existing fixture is used as a helper, never collected twice.
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                               for cls in (PreviewTests, EntryTests))
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
