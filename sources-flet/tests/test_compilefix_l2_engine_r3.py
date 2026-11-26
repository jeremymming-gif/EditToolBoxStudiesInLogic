"""R3 compile-fix engine contracts; synthetic files and injected transport only."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ENGINE = Path(__file__).resolve().parents[2] / "Engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
import slcn
from slcn import ai_workflow as workflow, ai_workflow_cli as cli, ai_workflow_convert as conv
from slcn import compilefix
from slcn.compilefix import l1_r3, l2_r3, model
from slcn.compilefix.compiler import CompileResult
from slcn.compilefix.files import REPLY_LIMIT, RepairError, digest, json_bytes, put_json
from slcn.compilefix.l2_request import fingerprint_api_key
from slcn.compilefix.state import capture_compile


BEFORE = (b"\\documentclass{article}\n\\begin{document}\nSynthetic & evidence.\n"
          + b"Only synthetic text is in this test.\n" * 16 + b"\\end{document}\n")
AFTER = BEFORE.replace(b"Synthetic & evidence.", b"Synthetic \\& evidence.")
SETTINGS = {"base": "https://example.invalid/v1", "model": "synthetic", "protocol": "chat",
            "ai_enabled": True, "compile_fix_enabled": True, "article_budget_tokens": 30000,
            "batch_budget_tokens": 1000000}
TEST_KEY = "synthetic-test-key"


class ParserContractTests(unittest.TestCase):
    def test_compile_fix_parser_accepts_every_ui_action_and_binding(self):
        parser = cli.build_parser()
        for action in l2_r3.ACTIONS:
            args = parser.parse_args(["--work-dir", "/tmp/synthetic", "compile-fix", action,
                                     "--preview-id", "preview", "--ai-enabled", "--compile-fix-enabled",
                                     "--request-id", "request", "--request-sha256", "a" * 64,
                                     "--authorization-id", "auth", "--evidence-id", "evidence",
                                     "--review-sha256", "b" * 64, "--json"])
            self.assertEqual(args.action, action)
        args = parser.parse_args(["--work-dir", "/tmp/synthetic", "deliver", "--preview-id", "preview",
                                  "--ai-enabled", "--compile-fix-enabled", "--evidence-id", "evidence",
                                  "--request-id", "request", "--review-sha256", "b" * 64, "--json"])
        self.assertTrue(args.ai_enabled and args.compile_fix_enabled)

    def test_deliver_forwards_explicit_repair_tuple_only_when_enabled(self):
        parser = cli.build_parser()
        args = parser.parse_args(["--work-dir", "/tmp/synthetic", "deliver", "--preview-id", "preview",
                                  "--ai-enabled", "--compile-fix-enabled", "--evidence-id", "e",
                                  "--request-id", "r", "--review-sha256", "h", "--json"])
        captured = {}
        with patch("slcn.ai_workflow_convert.deliver", side_effect=lambda **kw: captured.update(kw) or {"ok": True}):
            result = cli._op_deliver(args)
        self.assertEqual(captured["repair"], ("e", "r", "h"))
        self.assertTrue(captured["compile_fix_enabled"])
        self.assertTrue(result["ok"])

    def test_cli_send_consumes_only_dedicated_key_environment_name(self):
        parser = cli.build_parser()
        args = parser.parse_args(["--work-dir", "/tmp/synthetic", "compile-fix", "send",
                                  "--preview-id", "preview", "--timeout", "60", "--response-limit", "65536", "--json"])
        captured = {}
        def operate(*args, **kwargs):
            captured.update(kwargs)
            return {"ok": False, "error": "synthetic"}
        with patch.object(l2_r3, "operate", side_effect=operate), \
             patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{}'))), \
             patch.dict(os.environ, {"SLCN_COMPILE_FIX_API_KEY": "test-key", "SLCN_LLM_KEY": "legacy"}):
            cli._op_compile_fix(args)
            self.assertEqual(captured["api_key"], "test-key")
            self.assertNotIn("SLCN_COMPILE_FIX_API_KEY", os.environ)
            self.assertEqual(os.environ["SLCN_LLM_KEY"], "legacy")

    def test_prepare_and_send_reject_inherited_legacy_key(self):
        for action in ("prepare", "send"):
            args = cli.build_parser().parse_args(["--work-dir", "/tmp/synthetic", "compile-fix", action,
                                                  "--preview-id", "preview", "--json"])
            with self.subTest(action=action), patch.object(l2_r3, "operate") as operation, \
                    patch.dict(os.environ, {"SLCN_LLM_KEY": "legacy"}, clear=True):
                result = cli._op_compile_fix(args)
            self.assertFalse(result["ok"])
            self.assertNotIn("legacy", result["error"])
            operation.assert_not_called()


class PreviewSyncContractTests(unittest.TestCase):
    def test_generalized_sync_retains_legacy_journal_and_rolls_back(self):
        original = b"synthetic original"; changed = b"synthetic repaired"
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder).resolve()
            task = work / ".aiw" / "synthetic-task"; task.mkdir(parents=True)
            candidate = task / "candidate.tex"; candidate.write_bytes(original)
            state = {"task_id": "synthetic-task", "preview": {"candidate_text": str(candidate),
                     "text_new_sha256": digest(original)}}
            persisted = [dict(state, preview=dict(state["preview"]))]
            def load():
                return {**persisted[0], "preview": dict(persisted[0]["preview"])}
            def save(value):
                persisted[0] = {**value, "preview": dict(value["preview"])}
            sync = l1_r3.PreviewSync(work, state, state["preview"], changed,
                load_state=load, save_state=save, read_candidate=lambda: candidate.read_bytes(), repair_kind="L2")
            self.assertEqual(sync.journal.name, "l1-preview-recovery.json")
            self.assertEqual(candidate.read_bytes(), changed)
            self.assertEqual(persisted[0]["preview"]["text_new_sha256"], digest(changed))
            sync.rollback()
            self.assertEqual(candidate.read_bytes(), original)
            self.assertEqual(persisted[0]["preview"]["text_new_sha256"], digest(original))

    def test_same_filesystem_check_accepts_normal_paths_and_rejects_device_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            work = root / "work"; (work / ".aiw").mkdir(parents=True)
            final = root / "out" / "final"
            final.parent.mkdir()
            self.assertIsNone(l2_r3.require_same_fs(work, final))
        def device(number):
            return SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=number))
        with patch.object(l2_r3, "directory", side_effect=[device(1), device(2)]):
            with self.assertRaises(RepairError):
                l2_r3.require_same_fs("/synthetic/work", "/synthetic/out/final")


class PublicationContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.work = self.root / "work"
        self.final = self.root / "out" / "final"
        self.final.mkdir(parents=True)
        (self.final / "prior.tex").write_text("synthetic prior delivery", encoding="utf-8")
        stage = self.work / "stage"
        stage.mkdir(parents=True)
        (stage / "sample.tex").write_bytes(BEFORE)
        source = self.root / "inputs" / "source.tex"
        source.parent.mkdir()
        source.write_bytes(BEFORE)
        self.candidate = self.work / ".aiw" / "synthetic-task" / "preview.tex"
        self.candidate.parent.mkdir(parents=True)
        self.candidate.write_bytes(BEFORE)
        preview = {"ok": True, "preview_id": "synthetic-preview", "reply_sha256": "active-reply",
                   "candidate_text": str(self.candidate), "text_new_sha256": digest(BEFORE),
                   "candidate_bib": "", "bib_new_sha256": digest(b"")}
        conversion = {"stage_dir": str(stage), "final_dir": str(self.final), "stem": "sample",
                       "tex_name": "sample.tex", "bib_name": "", "stage_sha256s": conv._tree_shas(str(stage)),
                       "inputs": {"source": str(source), "files": {str(source): digest(BEFORE)}, "fingerprint": {}},
                       "options": {"force": True, "compile": True, "source_pdf": False, "clean": False}}
        workflow.save_state(str(self.work), {"schema_version": workflow.SCHEMA_VERSION_R3, "entry": "convert",
                            "task_id": "synthetic-task", "phase": "previewed", "history": [],
                            "active_reply": {"sha256": "active-reply"}, "preview": preview,
                            "conversion": conversion})
        self.state = workflow.load_state(str(self.work))
        self.prior = conv._tree_shas(str(self.final))

    def deliver(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--work-dir", str(self.work), "deliver", "--json", "--preview-id",
                             "synthetic-preview", *args])
        return code, json.loads(out.getvalue())

    def failure(self, *, attempt="not-admitted", diagnostic="Misplaced alignment tab character &."):
        def fail(_compile_pdf, out_dir, stem, *, source):
            capture = capture_compile(out_dir, stem, source=source)
            capture["failure"] = ("synthetic-run", 1, 1, f"./sample.tex:3: {diagnostic}\n".encode())
            capture["l1"] = {"candidate_sha256": digest(BEFORE), "phase": "xelatex-1", "returncode": 1,
                             "attempts": [{"result": attempt}]}
            return False, "synthetic L1 failure", capture
        with patch.object(compilefix, "compile_with_l1", side_effect=fail):
            return self.deliver("--ai-enabled", "--compile-fix-enabled")

    def operate(self, action, **kwargs):
        preview_id = "" if action in {"retained", "delete"} else "synthetic-preview"
        return l2_r3.operate(str(self.work), action, preview_id=preview_id, **kwargs)

    def retained_key(self):
        result = l2_r3.operate(str(self.work), "retained", preview_id="")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["retained"]), 1, result)
        return result["retained"][0]["record_key"]

    def prepare(self):
        code, failure = self.failure()
        self.assertEqual(code, 2, failure)
        evidence = failure["compile_fix"]
        self.assertTrue(evidence["eligible"])
        result = self.operate("prepare", evidence_id=evidence["evidence_id"], settings=SETTINGS, api_key=TEST_KEY)
        self.assertTrue(result["ok"], result)
        prepared = result["compile_fix"]
        self.assertEqual(digest(prepared["body"].encode()), prepared["request_sha256"])
        self.assertTrue(prepared["summary"]["key_configured"])
        self.assertNotIn(TEST_KEY, json.dumps(prepared))
        return prepared

    def apply(self, *, provider_metadata=None):
        prepared = self.prepare()
        ids = {key: prepared[key] for key in ("evidence_id", "request_id")}
        body = json.loads(prepared["body"])
        book = json.loads(body["messages"][1]["content"])
        action = {"action": "edit", "evidence_id": "e1", "old": "Synthetic & evidence.",
                  "new": "Synthetic \\& evidence.", "before": "\n", "after": "\n"}
        reply = {"identity": book["identity"], "actions": [action]}
        if provider_metadata is None:
            imported = self.operate("import", **ids, reply_text=json.dumps(reply))
        else:
            envelope = self.http_envelope(json.dumps(reply), provider=provider_metadata)
            imported = self.operate("send", **self.send_options(prepared), api_key=TEST_KEY,
                                    _test_urlopen=lambda *a, **kw: io.BytesIO(envelope))
        self.assertTrue(imported["ok"], imported)
        reviewed = self.operate("review", **ids)
        self.assertTrue(reviewed["ok"], reviewed)
        review_sha = reviewed["compile_fix"]["review_sha256"]
        self.assertFalse(self.operate("apply", **ids, review_sha256="wrong")["ok"])

        def compile_ok(folder, stem, expected, support, private_dir):
            self.assertEqual((folder / (stem + ".tex")).read_bytes(), AFTER)
            artifacts = (b"%PDF-1.4 synthetic\n%%EOF", b"synthetic log", b"synthetic recorder")
            for suffix, value in zip((".pdf", ".log", ".fls"), artifacts):
                (folder / (stem + suffix)).write_bytes(value)
            return CompileResult("passed", expected, "synthetic-compile", "bind", 0, *map(digest, artifacts))

        with patch.object(l2_r3.compiler, "compile_candidate", side_effect=compile_ok):
            compiled = self.operate("apply", **ids, review_sha256=review_sha)
        self.assertTrue(compiled["ok"], compiled)
        self.assertTrue(Path(compiled["compile_fix"]["pdf_path"]).is_file())
        self.assert_original()
        return ("--ai-enabled", "--compile-fix-enabled", "--evidence-id", ids["evidence_id"],
                "--request-id", ids["request_id"], "--review-sha256", review_sha)

    def assert_original(self):
        self.assertEqual(self.candidate.read_bytes(), BEFORE)
        self.assertEqual(workflow.load_state(str(self.work)), self.state)
        self.assertEqual(conv._tree_shas(str(self.final)), self.prior)

    def test_failed_deliver_exposes_only_eligible_diagnostic(self):
        for kwargs in ({"attempt": "recovery-required"}, {"diagnostic": "File missing.sty not found."}):
            with self.subTest(kwargs=kwargs):
                code, result = self.failure(**kwargs)
                self.assertEqual(code, 2)
                self.assertNotIn("compile_fix", result)
        code, result = self.failure()
        self.assertEqual(code, 2)
        self.assertTrue(result["compile_fix"]["eligible"])
        self.assert_original()

    def test_review_retry_from_compile_failed_reuses_bound_reply(self):
        prepared = self.prepare()
        ids = {key: prepared[key] for key in ('evidence_id', 'request_id')}
        body = json.loads(prepared['body'])
        book = json.loads(body['messages'][1]['content'])
        action = {'action': 'edit', 'evidence_id': 'e1', 'old': 'Synthetic & evidence.',
                  'new': 'Synthetic \\& evidence.', 'before': '\n', 'after': '\n'}
        reply = {'identity': book['identity'], 'actions': [action]}
        imported = self.operate('import', **ids, reply_text=json.dumps(reply))
        self.assertTrue(imported['ok'], imported)
        reviewed = self.operate('review', **ids)
        self.assertTrue(reviewed['ok'], reviewed)
        review_sha = reviewed['compile_fix']['review_sha256']
        round_no = reviewed['compile_fix']['round_number']

        # An isolated recompile failure must not consume the bound reply.
        with patch.object(l2_r3.compiler, 'compile_candidate', return_value=SimpleNamespace(
                state='failed', candidate_sha256=digest(AFTER), phase='xelatex-1', run_id='run')):
            failed = self.operate('apply', **ids, review_sha256=review_sha)
        self.assertFalse(failed['ok'])
        self.assertEqual(failed['compile_fix']['status'], 'compile_failed')

        # Retrying review reuses the SAME reply and review hash; no new paid round.
        retried = self.operate('review', **ids)
        self.assertTrue(retried['ok'], retried)
        self.assertEqual(retried['compile_fix']['status'], 'reviewed')
        self.assertEqual(retried['compile_fix']['review_sha256'], review_sha)
        self.assertEqual(retried['compile_fix']['round_number'], round_no)
        self.assert_original()

    def test_same_filesystem_publish_and_reject_repeat(self):
        args = self.apply()
        code, result = self.deliver(*args)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["compile_fix"]["status"], "delivered")
        self.assertEqual((self.final / "sample.tex").read_bytes(), AFTER)
        self.assertEqual(self.candidate.read_bytes(), AFTER)
        self.assertEqual(workflow.load_state(str(self.work))["preview"]["text_new_sha256"], digest(AFTER))
        note = (self.final / conv.CHECK_NOTE_FILENAME).read_text(encoding="utf-8")
        self.assertIn("## 编译修复", note)
        self.assertNotIn(TEST_KEY, note)
        self.assertEqual(self.deliver(*args)[0], 2)

    def test_publish_failure_after_preview_sync_preserves_old_final(self):
        args = self.apply()
        original_replace = os.replace

        def fail(src, dst, *args, **kwargs):
            if Path(dst) == self.final and ".coop-tmp-" in Path(src).name:
                raise OSError("synthetic publication failure")
            return original_replace(src, dst, *args, **kwargs)

        with patch.object(conv.os, "replace", side_effect=fail):
            code, result = self.deliver(*args)
        self.assertEqual(code, 2, result)
        self.assertIn("synthetic publication failure", result["error"])
        self.assert_original()

    def test_restart_before_delivery_receipt_restores_both_sides(self):
        args = self.apply()
        receipt_path = conv._journal_path(str(self.work))
        write_json = conv.coop.atomic_write_json

        def interrupt(path, value):
            if path == receipt_path and value.get("delivered"):
                raise SystemExit(73)
            return write_json(path, value)

        with patch.object(conv.coop, "atomic_write_json", side_effect=interrupt), \
                self.assertRaises(SystemExit):
            self.deliver(*args)
        self.assertEqual((self.final / "sample.tex").read_bytes(), AFTER)
        self.assertEqual(self.candidate.read_bytes(), AFTER)
        result = self.operate("status")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["compile_fix"]["status"], "compiled")
        self.assert_original()

    def test_restart_after_delivery_receipt_finishes_without_republishing(self):
        args = self.apply()
        with patch.object(conv, "_record_delivery_state", side_effect=SystemExit(73)), \
                self.assertRaises(SystemExit):
            self.deliver(*args)
        published = conv._tree_shas(str(self.final))
        self.assertEqual(self.candidate.read_bytes(), AFTER)
        self.assertEqual(workflow.load_state(str(self.work))["phase"], "previewed")
        result = self.operate("status")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["compile_fix"]["status"], "delivered")
        self.assertEqual(workflow.load_state(str(self.work))["phase"], "delivered")
        self.assertEqual(conv._tree_shas(str(self.final)), published)
        self.assertEqual(self.deliver(*args)[0], 2)

    def child(self, code, *args):
        setup = '''
import json, os, pathlib, sys
search, core, base, work, *args = sys.argv[1:]
sys.path.insert(0, base)
import slcn
slcn.__path__[:] = json.loads(search)
from slcn import compilefix
compilefix.__path__[:] = json.loads(core)
from slcn import ai_workflow_cli as cli, ai_workflow_convert as conv
from slcn.compilefix import l1_r3, l2_r3
'''
        return [sys.executable, "-B", "-c", setup + code, json.dumps(list(slcn.__path__)),
                json.dumps(list(compilefix.__path__)), str(Path(compilefix.__file__).parents[2]),
                str(self.work), *args]

    def test_restart_undo_first_after_success_receipt_before_preview_commit(self):
        args = self.apply()
        journal_path = self.work / ".aiw" / "l1-preview-recovery.json"
        interrupted = subprocess.run(self.child('''
conv._record_delivery_state = lambda *unused: os._exit(73)
raise SystemExit(cli.main(["--work-dir", work, "deliver", "--json", "--preview-id",
                           "synthetic-preview", *args]))
''', *args), capture_output=True, timeout=30)
        self.assertEqual(interrupted.returncode, 73, interrupted.stderr)
        receipt_path = Path(conv._journal_path(str(self.work)))
        receipt = json.loads(receipt_path.read_bytes())
        self.assertTrue(receipt["delivered"])
        self.assertEqual(json.loads(journal_path.read_bytes())["state"], "syncing")
        self.assertEqual(self.candidate.read_bytes(), AFTER)
        self.assertEqual(workflow.load_state(str(self.work))["phase"], "previewed")

        restarted = subprocess.run(self.child('''
raise SystemExit(cli.main(["--work-dir", work, "--json", "undo"]))
'''), capture_output=True, timeout=30)
        self.assertEqual(restarted.returncode, 0, (restarted.stdout, restarted.stderr))
        undone = json.loads(restarted.stdout)
        self.assertTrue(undone["undone"])
        self.assertEqual(conv._tree_shas(str(self.final)), self.prior)
        self.assertEqual(conv._tree_shas(undone["undo_keep"]), receipt["shas"])
        self.assertEqual(json.loads(journal_path.read_bytes())["state"], "completed")
        state = workflow.load_state(str(self.work))
        self.assertEqual(state["phase"], "undone")
        self.assertEqual(state["delivery"], json.loads(receipt_path.read_bytes()))
        self.assertEqual([item["event"] for item in state["history"]], ["delivered", "undone"])
        self.assertEqual(state["preview"]["text_new_sha256"], digest(self.candidate.read_bytes()))
        self.assertEqual(self.candidate.read_bytes(), AFTER)
        status = self.operate("status")
        self.assertTrue(status["ok"], status)
        with patch.object(compilefix, "compile_with_l1", return_value=(True, "", None)):
            self.assertEqual(self.deliver()[0], 0)

    def test_status_recovers_receipt_after_delivery_state_write_before_preview_commit(self):
        args = self.apply()
        interrupted = subprocess.run(self.child('''
original = conv._record_delivery_state
def commit_state_then_crash(work, state, receipt):
    original(work, state, receipt)
    os._exit(73)
conv._record_delivery_state = commit_state_then_crash
raise SystemExit(cli.main(["--work-dir", work, "deliver", "--json", "--preview-id",
                           "synthetic-preview", *args]))
''', *args), capture_output=True, timeout=30)
        self.assertEqual(interrupted.returncode, 73, interrupted.stderr)
        receipt = conv._read_journal(conv._journal_path(str(self.work)))
        self.assertTrue(receipt["delivered"])
        self.assertEqual(workflow.load_state(str(self.work))["phase"], "delivered")
        sync_path = self.work / ".aiw" / "l1-preview-recovery.json"
        self.assertEqual(json.loads(sync_path.read_bytes())["state"], "syncing")
        with patch.object(compilefix, "compile_with_l1", return_value=(True, "", None)):
            self.assertEqual(self.deliver()[0], 2)
        self.assertEqual(json.loads(sync_path.read_bytes())["state"], "completed")
        self.assertTrue(self.operate("status")["ok"])
        self.assertEqual(conv._tree_shas(str(self.final)), receipt["shas"])

    def test_inconsistent_delivery_receipt_blocks_undo_without_touching_trees(self):
        args = self.apply()
        interrupted = subprocess.run(self.child('''
conv._record_delivery_state = lambda *unused: os._exit(73)
raise SystemExit(cli.main(["--work-dir", work, "deliver", "--json", "--preview-id",
                           "synthetic-preview", *args]))
''', *args), capture_output=True, timeout=30)
        self.assertEqual(interrupted.returncode, 73, interrupted.stderr)
        receipt_path = Path(conv._journal_path(str(self.work)))
        receipt = json.loads(receipt_path.read_bytes())
        published = conv._tree_shas(str(self.final))
        backup = receipt["backup"]
        backup_shas = conv._tree_shas(backup)
        candidate = self.candidate.read_bytes()
        state_before = workflow.load_state(str(self.work))
        sync_path = self.work / ".aiw" / "l1-preview-recovery.json"
        sync_before = json.loads(sync_path.read_bytes())
        self.assertEqual(sync_before["state"], "syncing")

        receipt["preview_id"] = "synthetic-mismatched-preview"
        conv.coop.atomic_write_json(receipt_path, receipt)
        blocked = conv.undo_delivery(work_dir=str(self.work))
        self.assertFalse(blocked["ok"], blocked)
        self.assertEqual(conv._tree_shas(str(self.final)), published)
        self.assertEqual(conv._tree_shas(backup), backup_shas)
        self.assertEqual(self.candidate.read_bytes(), candidate)
        self.assertEqual(workflow.load_state(str(self.work)), state_before)
        self.assertEqual(json.loads(sync_path.read_bytes()), sync_before)

    def test_promotion_refuses_first_install_if_prior_final_moved_after_copy(self):
        args = self.apply()
        external_hold = self.final.with_name("external-hold")
        copytree = conv.coop.shutil.copytree

        def move_after_copy(src, dst, **kwargs):
            result = copytree(src, dst, **kwargs)
            if ".coop-tmp-" in Path(dst).name:
                self.final.rename(external_hold)
            return result

        with patch.object(conv.coop.shutil, "copytree", side_effect=move_after_copy):
            code, result = self.deliver(*args)
        self.assertEqual(code, 2, result)
        self.assertFalse(self.final.exists())
        self.assertEqual(conv._tree_shas(str(external_hold)), self.prior)
        self.assertFalse(conv._read_journal(conv._journal_path(str(self.work)))["delivered"])
        self.assertEqual(self.candidate.read_bytes(), BEFORE)
        self.assertEqual(workflow.load_state(str(self.work)), self.state)

    def report_reply(self, prepared):
        book = json.loads(json.loads(prepared["body"])["messages"][1]["content"])
        return json_bytes({"identity": book["identity"], "actions": [
            {"action": "report_only", "evidence_id": "e1", "reason": "Synthetic observation."}]})

    def send_options(self, prepared):
        options = {key: prepared[key] for key in ("evidence_id", "request_id", "request_sha256", "authorization_id")}
        return dict(options, settings=SETTINGS, terms_ack=True, timeout=60, response_limit=REPLY_LIMIT)

    @staticmethod
    def http_envelope(reply, **extra):
        return b" \n" + json.dumps({**extra, "choices": [
            {"finish_reason": "stop", "message": {"content": reply}}]}, indent=3).encode() + b"\n "

    def test_old_preview_has_cleanup_only_and_ordinary_status_expires_it(self):
        path = l2_r3._record_path(self.work, "synthetic-task", create=True)
        for action in ("delete", "expire"):
            with self.subTest(action=action):
                prepared = self.prepare()
                options = self.send_options(prepared)
                ids = {key: prepared[key] for key in ("evidence_id", "request_id")}
                reply = self.report_reply(prepared).decode()
                self.assertTrue(self.operate("import", **ids, reply_text=reply)["ok"])
                state = workflow.load_state(str(self.work))
                workflow.save_state(str(self.work), {**state, "preview": {**state["preview"],
                                                                         "preview_id": "synthetic-preview-2"}})
                switched = workflow.load_state(str(self.work))
                with patch("urllib.request.build_opener") as opener:
                    self.assertFalse(self.operate("send", **options, api_key=TEST_KEY)["ok"])
                    opener.assert_not_called()
                self.assertFalse(self.operate("import", **ids, reply_text=reply)["ok"])
                retained = l2_r3.operate(str(self.work), "retained", preview_id="")
                self.assertTrue(retained["ok"], retained)
                [row] = retained["retained"]
                self.assertEqual(set(row), {"record_key", "task_id", "preview_id", "status", "expires_at", "has_reply"})
                self.assertEqual(row, {"task_id": "synthetic-task", "record_key": row["record_key"],
                    "preview_id": "synthetic-preview", "status": "imported", "has_reply": True,
                    "expires_at": l2_r3._load(path)["expires_at"]})
                self.assertEqual(len(row["record_key"]), 64)
                if action == "delete":
                    self.assertFalse(self.operate("delete", record_key="0" * 64)["ok"])
                    deleted = self.operate("delete", record_key=row["record_key"])
                    self.assertEqual(deleted["compile_fix"]["status"], "deleted", deleted)
                    self.assertEqual(set(deleted["compile_fix"]), set(row))
                    self.assertFalse(deleted["compile_fix"]["has_reply"])
                    self.assertEqual(self.operate("delete", record_key=row["record_key"]), deleted)
                    self.assertFalse(self.operate("delete", record_key="0" * 64)["ok"])
                else:
                    put_json(path, dict(l2_r3._load(path), expires_at=1))
                    out = io.StringIO()
                    with redirect_stdout(out):
                        self.assertEqual(cli.main(["--work-dir", str(self.work), "status", "--json"]), 0)
                    self.assertEqual(l2_r3._load(path)["status"], "expired")
                self.assertFalse((path.parent / "reply.json").exists())
                self.assertNotIn("request", l2_r3._load(path))
                self.assertEqual(workflow.load_state(str(self.work)), switched)
                conv.coop.atomic_write_json(workflow.state_path(str(self.work)), self.state)

    def test_switched_from_prepared_preview_rejects_send_and_import(self):
        prepared = self.prepare()
        ids = {key: prepared[key] for key in ("evidence_id", "request_id")}
        state = workflow.load_state(str(self.work))
        state["preview"]["preview_id"] = "synthetic-preview-2"
        workflow.save_state(str(self.work), state)
        with patch("urllib.request.build_opener") as opener:
            self.assertFalse(self.operate("send", **self.send_options(prepared), api_key=TEST_KEY)["ok"])
            opener.assert_not_called()
        self.assertFalse(self.operate("import", **ids, reply_text=self.report_reply(prepared).decode())["ok"])
        self.assertFalse(l2_r3.operate(str(self.work), "status", preview_id="synthetic-preview")["ok"])
        self.assertTrue(self.operate("delete", record_key=self.retained_key())["ok"])

    def test_reply_arriving_after_preview_switch_does_not_write_either_payload(self):
        prepared = self.prepare()
        ids = {key: prepared[key] for key in ("evidence_id", "request_id")}
        envelope = self.http_envelope(self.report_reply(prepared).decode())
        def fake(*args, **kwargs):
            state = workflow.load_state(str(self.work))
            state["preview"]["preview_id"] = "synthetic-preview-2"
            workflow.save_state(str(self.work), state)
            return io.BytesIO(envelope)
        result = self.operate("send", **self.send_options(prepared), api_key=TEST_KEY, _test_urlopen=fake)
        self.assertFalse(result["ok"], result)
        path = l2_r3._record_path(self.work, "synthetic-task")
        self.assertEqual(l2_r3._load(path)["round_number"], 1)
        self.assertFalse((path.parent / "reply.json").exists())
        self.assertEqual(list(path.parent.glob("response-*")), [])
        self.assertTrue(self.operate("delete", record_key=self.retained_key())["ok"])

    @unittest.skipIf(os.name == "nt", "原始回件私有 ACL 校验仅非 Windows 可用；Windows 的 fail-closed 由 test_windows_raw_body_retention_fails_closed_without_acl_verification 单独覆盖")
    def test_http_raw_bytes_are_private_and_cleared_by_lifecycle(self):
        for action in ("delete", "expire"):
            with self.subTest(action=action):
                # Use a new eligibility record after each completed lifecycle.
                self.setUp()
                prepared = self.prepare()
                ids = {key: prepared[key] for key in ("evidence_id", "request_id")}
                reply = self.report_reply(prepared)
                marker = "synthetic provider metadata - do not disclose"
                envelope = self.http_envelope(reply.decode(), provider=marker)
                result = self.operate("send", **self.send_options(prepared), api_key=TEST_KEY,
                                      _test_urlopen=lambda *a, **kw: io.BytesIO(envelope))
                self.assertTrue(result["ok"], result)
                path = l2_r3._record_path(self.work, "synthetic-task")
                raw_path = path.parent / ("response-" + ids["request_id"]) / "response.raw"
                self.assertEqual(raw_path.read_bytes(), envelope)
                self.assertEqual((path.parent / "reply.json").read_bytes(), reply)
                self.assertEqual(l2_r3._load(path)["response_sha256"], digest(envelope))
                if os.name != "nt":
                    self.assertFalse(raw_path.stat().st_mode & 0o077)
                    self.assertFalse(raw_path.parent.stat().st_mode & 0o077)
                for value in (result, self.operate("status"), self.operate("review", **ids)):
                    disclosure = json.dumps(value)
                    for hidden in (marker, digest(envelope), str(raw_path), "response_sha256"):
                        self.assertNotIn(hidden, disclosure)
                self.assertNotIn(marker.encode(), (path.parent / "request.log").read_bytes())
                if action == "expire":
                    put_json(path, dict(l2_r3._load(path), expires_at=1))
                    self.assertEqual(self.operate("status")["compile_fix"]["status"], "expired")
                else:
                    cleaned = self.operate(action, record_key=self.retained_key())
                    self.assertTrue(cleaned["ok"], cleaned)
                    self.assertEqual(cleaned["compile_fix"]["status"], "deleted")
                self.assertFalse(raw_path.parent.exists())
                self.assertFalse((path.parent / "reply.json").exists())
                self.assertNotIn("response_sha256", l2_r3._load(path))

    @unittest.skipIf(os.name == "nt", "原始回件私有 ACL 校验仅非 Windows 可用；Windows 的 fail-closed 由 test_windows_raw_body_retention_fails_closed_without_acl_verification 单独覆盖")
    def test_delivered_raw_response_can_be_deleted_without_touching_delivery(self):
        marker = "synthetic provider only proof"
        args = self.apply(provider_metadata=marker)
        key_before_delivery = self.retained_key()
        code, receipt = self.deliver(*args)
        self.assertEqual(code, 0, receipt)
        self.assertNotIn(marker, json.dumps(receipt))
        publication = conv._tree_shas(str(self.final))
        state = workflow.load_state(str(self.work))
        self.assertFalse(any("response.raw" in name for name in publication))
        self.assertNotIn(marker, (self.final / conv.CHECK_NOTE_FILENAME).read_text())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["--work-dir", str(self.work), "compile-fix", "retained", "--json"]), 0)
        [retained] = json.loads(out.getvalue())["retained"]
        self.assertEqual(retained["status"], "delivered")
        self.assertEqual(retained["record_key"], key_before_delivery)
        root = l2_r3._record_path(self.work, "synthetic-task").parent
        record = l2_r3._load(root / "state.json")
        request_id = record["request"]["request_id"]
        self.assertTrue((root / ("response-" + request_id) / "response.raw").exists())
        with patch.object(l2_r3.shutil, "rmtree", side_effect=OSError("private response path")):
            blocked = self.operate("delete", record_key=retained["record_key"])
        self.assertFalse(blocked["ok"], blocked)
        self.assertNotIn("private response path", json.dumps(blocked))
        self.assertTrue(self.operate("delete", record_key=retained["record_key"])["ok"])
        self.assertEqual(list(root.glob("response-*")), [])
        self.assertEqual(workflow.load_state(str(self.work)), state)
        self.assertEqual(conv._tree_shas(str(self.final)), publication)

    def test_windows_raw_body_retention_fails_closed_without_acl_verification(self):
        prepared = self.prepare()
        envelope = self.http_envelope(self.report_reply(prepared).decode())
        self.assertFalse(l2_r3._raw_response_storage_supported("nt"))
        with patch.object(l2_r3, "_raw_response_storage_supported", return_value=False):
            result = self.operate("send", **self.send_options(prepared), api_key=TEST_KEY,
                                  _test_urlopen=lambda *a, **kw: io.BytesIO(envelope))
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["compile_fix"]["status"], "unknown")
        self.assertNotIn("synthetic-test-key", json.dumps(result))
        path = l2_r3._record_path(self.work, "synthetic-task")
        self.assertFalse((path.parent / "reply.json").exists())
        self.assertFalse(list(path.parent.glob("response-*")))
        record = l2_r3._load(path)
        self.assertNotIn("response_sha256", record)
        self.assertNotIn("response_bytes", record)
        self.assertEqual(record["round_number"], 1)

    def test_cli_retained_delete_uses_stable_key_without_material_or_key_input(self):
        prepared = self.prepare()
        ids = {key: prepared[key] for key in ("evidence_id", "request_id")}
        imported = self.operate("import", **ids, reply_text=self.report_reply(prepared).decode())
        self.assertTrue(imported["ok"], imported)
        before_state = workflow.load_state(str(self.work))
        before_final = conv._tree_shas(str(self.final))
        before_preview = self.candidate.read_bytes()

        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["--work-dir", str(self.work), "compile-fix", "retained", "--json"]), 0)
        [retained] = json.loads(out.getvalue())["retained"]
        self.assertEqual(set(retained), {"record_key", "task_id", "preview_id", "status", "expires_at", "has_reply"})
        self.assertEqual(retained["record_key"], self.retained_key())
        self.assertNotIn(prepared["request_id"], json.dumps(retained))
        self.assertNotIn(prepared["evidence_id"], json.dumps(retained))

        class NoInput:
            @property
            def buffer(self):
                raise AssertionError("cleanup must not read stdin")

        argv = ["--work-dir", str(self.work), "compile-fix", "delete", "--record-key",
                retained["record_key"], "--json"]
        out = io.StringIO()
        with redirect_stdout(out), patch.object(sys, "stdin", NoInput()), \
                patch.dict(os.environ, {"SLCN_COMPILE_FIX_API_KEY": "synthetic-must-not-read"}, clear=True):
            self.assertEqual(cli.main(argv), 0)
        deleted = json.loads(out.getvalue())
        self.assertTrue(deleted["ok"], deleted)
        self.assertEqual(deleted["compile_fix"]["status"], "deleted")
        self.assertFalse(deleted["compile_fix"]["has_reply"])
        self.assertNotIn("request_id", json.dumps(deleted))
        self.assertNotIn("synthetic-must-not-read", json.dumps(deleted))
        self.assertEqual(workflow.load_state(str(self.work)), before_state)
        self.assertEqual(conv._tree_shas(str(self.final)), before_final)
        self.assertEqual(self.candidate.read_bytes(), before_preview)

    @unittest.skipIf(os.name == "nt", "原始回件私有 ACL 校验仅非 Windows 可用；Windows 的 fail-closed 由 test_windows_raw_body_retention_fails_closed_without_acl_verification 单独覆盖")
    def test_invalid_or_uncommitted_http_response_is_unavailable_for_review(self):
        for failure in ("schema", "credential", "over_limit", "commit_crash"):
            with self.subTest(failure=failure):
                self.setUp()
                prepared = self.prepare()
                ids = {key: prepared[key] for key in ("evidence_id", "request_id")}
                reply = self.report_reply(prepared).decode()
                if failure == "schema":
                    reply = "{}"
                metadata = TEST_KEY if failure == "credential" else "extra"
                envelope = self.http_envelope(reply, provider=metadata)
                options = self.send_options(prepared)
                if failure == "over_limit":
                    options["response_limit"] = len(envelope) - 1
                def send():
                    return self.operate("send", **options, api_key=TEST_KEY,
                                        _test_urlopen=lambda *a, **kw: io.BytesIO(envelope))
                path = l2_r3._record_path(self.work, "synthetic-task")
                raw_path = path.parent / ("response-" + ids["request_id"]) / "response.raw"
                if failure == "commit_crash":
                    write_json = l2_r3.put_json
                    def interrupt(destination, value):
                        if destination == path and value.get("status") == "imported":
                            raise SystemExit(73)
                        return write_json(destination, value)
                    with patch.object(l2_r3, "put_json", side_effect=interrupt), self.assertRaises(SystemExit):
                        send()
                    self.assertEqual(raw_path.read_bytes(), envelope)
                else:
                    self.assertFalse(send()["ok"])
                status = self.operate("status")
                self.assertEqual(status["compile_fix"]["status"], "unknown")
                self.assertFalse(self.operate("review", **ids)["ok"])
                self.assertFalse(raw_path.parent.exists())
                self.assertFalse((path.parent / "reply.json").exists())
                self.assertNotIn("response_sha256", l2_r3._load(path))

    @unittest.skipIf(os.name == "nt", "原始回件私有 ACL 校验仅非 Windows 可用；Windows 的 fail-closed 由 test_windows_raw_body_retention_fails_closed_without_acl_verification 单独覆盖")
    def test_two_processes_consume_same_authorization_at_most_once(self):
        prepared = self.prepare()
        reply = self.report_reply(prepared).decode()
        envelope = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": reply}}]})
        code = '''
import io, time
root = pathlib.Path(work)
options, envelope, slot = args
def waiting_for(predicate):
    for _ in range(500):
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("child barrier timed out")
if slot != "replay":
    (root / f"ready-{slot}").touch()
    waiting_for(lambda: all((root / f"ready-{n}").exists() for n in ("1", "2")))
def fake(*unused, **kwargs):
    with (root / "opener-count").open("ab") as stream:
        stream.write(b"1")
    waiting_for(lambda: any((root / f"finished-{n}").exists() for n in ("1", "2")))
    return io.BytesIO(envelope.encode())
result = l2_r3.operate(work, "send", preview_id="synthetic-preview", **json.loads(options),
                       api_key="synthetic-test-key", _test_urlopen=fake)
(root / f"finished-{slot}").touch()
print(json.dumps(result))
'''
        child = self.child(code, json.dumps(self.send_options(prepared)), envelope)
        with subprocess.Popen([*child, "1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as one, \
                subprocess.Popen([*child, "2"], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as two:
            outcomes = []
            for process in (one, two):
                out, err = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, err)
                outcomes.append(json.loads(out))
        self.assertEqual(sorted(item["ok"] for item in outcomes), [False, True], outcomes)
        self.assertEqual(self.operate("status")["compile_fix"]["round_number"], 1)
        replay = subprocess.run([*child, "replay"], capture_output=True, timeout=15)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertFalse(json.loads(replay.stdout)["ok"])
        self.assertEqual((self.work / "opener-count").read_bytes(), b"1")

    def test_no_l2_delivery_note_matches_existing_rendering_bytes(self):
        with patch.object(compilefix, "compile_with_l1", return_value=(True, "", None)):
            code, result = self.deliver("--ai-enabled", "--compile-fix-enabled")
        self.assertEqual(code, 0, result)
        self.assertNotIn("compile_fix", result)
        expected = ("# R3 交付检查说明\n\n"
                    "- 任务编号：synthetic-task；预览编号：synthetic-preview\n"
                    "- 执行覆盖：0 类\n- 实际采纳写入：0 条\n\n"
                    "## 已采纳\n- （无）\n\n## 未采纳 / 带定位待人工\n- （无）\n\n"
                    "## 编译状态\n- 编译成功；未要求导出原稿 PDF\n\n"
                    "> 编译成功 / 引文闭合 / 模型自报通过 / 实际交付为四个独立事实，"
                    "此处只记录本模块的真实写入与调用结果。\n")
        self.assertEqual((self.final / conv.CHECK_NOTE_FILENAME).read_bytes(), expected.encode("utf-8"))

    @unittest.skipIf(os.name == "nt", "原始回件私有 ACL 校验仅非 Windows 可用；Windows 的 fail-closed 由 test_windows_raw_body_retention_fails_closed_without_acl_verification 单独覆盖")
    def test_cli_send_binds_credential_and_uses_default_opener_path(self):
        prepared = self.prepare()
        ids = {key: prepared[key] for key in ("evidence_id", "request_id", "request_sha256", "authorization_id")}
        book = json.loads(json.loads(prepared["body"])["messages"][1]["content"])
        reply = json.dumps({"identity": book["identity"], "actions": [
            {"action": "report_only", "evidence_id": "e1", "reason": "Synthetic observation."}]})
        envelope = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": reply}}]}).encode()
        args = cli.build_parser().parse_args(["--work-dir", str(self.work), "compile-fix", "send",
              "--preview-id", "synthetic-preview", "--timeout", "60", "--response-limit", str(REPLY_LIMIT),
              "--compile-fix-terms-ack", "--json",
              *(arg for name, value in ids.items() for arg in ("--" + name.replace("_", "-"), value))])
        stored = l2_r3._record_path(self.work, "synthetic-task")
        self.assertEqual(l2_r3._load(stored)["request"]["credential_fingerprint"], fingerprint_api_key(TEST_KEY))
        self.assertNotIn(TEST_KEY.encode(), stored.read_bytes())
        self.assertNotIn("credential_fingerprint", json.dumps(prepared))

        def send(key):
            with patch.dict(os.environ, {l2_r3.API_KEY_ENV: key}), \
                    patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json_bytes({"settings": SETTINGS})))):
                return cli._op_compile_fix(args)

        with patch("urllib.request.build_opener") as build_opener:
            rejected = send("synthetic-other-key")
            self.assertFalse(rejected["ok"], rejected)
            build_opener.assert_not_called()
            self.assertEqual(l2_r3._load(stored)["round_number"], 0)
            opened = build_opener.return_value.open
            opened.return_value = io.BytesIO(envelope)
            accepted = send(TEST_KEY)
            self.assertTrue(accepted["ok"], accepted)
            self.assertEqual(accepted["compile_fix"]["status"], "imported")
            opened.assert_called_once()
            self.assertEqual(opened.call_args.args[0].data, prepared["body"].encode())
            self.assertEqual(l2_r3._load(stored)["round_number"], 1)
        self.assert_original()

    def test_send_intent_survives_process_loss_and_blocks_replay(self):
        prepared = self.prepare()
        options = {key: prepared[key] for key in ("evidence_id", "request_id", "request_sha256", "authorization_id")}
        options.update(settings=SETTINGS, terms_ack=True, timeout=60, response_limit=REPLY_LIMIT)
        script = '''
import json, os, pathlib, sys
search, core, base, work, raw, phase = sys.argv[1:]
sys.path.insert(0, base)
import slcn
slcn.__path__[:] = json.loads(search)
from slcn import compilefix
compilefix.__path__[:] = json.loads(core)
from slcn.compilefix import l2_r3
def fake(request, **kwargs):
    with (pathlib.Path(work) / "synthetic-opener-count").open("ab") as count:
        count.write(b"1")
    os._exit(73)
opts = json.loads(raw)
result = l2_r3.operate(work, "send", preview_id="synthetic-preview", **opts,
                       api_key="synthetic-test-key", _test_urlopen=fake)
if phase == "replay":
    assert result["ok"] is False, result
    status = l2_r3.operate(work, "status", preview_id="synthetic-preview")
    assert status["compile_fix"]["status"] == "unknown", status
    assert status["compile_fix"]["round_number"] == 1, status
'''
        child = [sys.executable, "-B", "-c", script, json.dumps(list(slcn.__path__)),
                 json.dumps(list(compilefix.__path__)), str(Path(compilefix.__file__).parents[2]),
                 str(self.work), json.dumps(options)]
        initial = subprocess.run([*child, "initial"], capture_output=True, text=True, timeout=30)
        self.assertEqual(initial.returncode, 73, initial.stderr)
        replay = subprocess.run([*child, "replay"], capture_output=True, text=True, timeout=30)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual((self.work / "synthetic-opener-count").read_bytes(), b"1")
        self.assert_original()



RAW = (b"\\documentclass{article}\n\\begin{document}\nSyntheticKnown\n"
       + b"Synthetic padding for bounded changes.\n" * 9 + b"\\end{document}\n")
EDITABLE = [{"classification": "editable", "phase": "xelatex-1", "tool": "xelatex",
             "exit_code": 1, "summary": "Synthetic TeX diagnostic.", "source_line": 3, "log_line": 1}]
MANUAL = [{**EDITABLE[0], "classification": "manual"}]


class TaskbookTests(unittest.TestCase):
    def setUp(self):
        self.record = {"identity": {"task_id": "synthetic-task"}, "diagnostics": EDITABLE}

    def test_scope_is_exact_and_rejects_duplicates_or_unknown_evidence(self):
        book, scope = l2_r3._book(self.record, RAW, "request-1", None)
        self.assertEqual(scope, {"evidence_ids": ["e1"]})
        self.assertEqual(book["evidence"][0]["evidence_id"], "e1")
        with self.assertRaises(RepairError):
            l2_r3._book(self.record, RAW, "request-2", {"evidence_ids": ["e1", "e1"]})
        with self.assertRaises(RepairError):
            l2_r3._book(self.record, RAW, "request-3", {"evidence_ids": ["foreign"]})

    def test_manual_only_taskbook_never_offers_edit(self):
        record = {**self.record, "diagnostics": MANUAL}
        book, _ = l2_r3._book(record, RAW, "request-manual", None)
        self.assertEqual(book["actions"], ["report_only"])

    def test_report_only_schema_and_evidence_are_strict(self):
        book, _ = l2_r3._book(self.record, RAW, "request-report", None)
        reply = {"identity": book["identity"], "actions": [
            {"action": "report_only", "evidence_id": "e1", "reason": "Inspect the synthetic source locally."}]}
        self.assertEqual(model.parse_reply(json_bytes(reply), book, RAW)[0]["action"], "report_only")
        reply["actions"][0]["extra"] = "not allowed"
        with self.assertRaises(RepairError):
            model.parse_reply(json_bytes(reply), book, RAW)
        reply["actions"][0].pop("extra")
        reply["actions"][0]["evidence_id"] = "foreign"
        with self.assertRaises(RepairError):
            model.parse_reply(json_bytes(reply), book, RAW)

    def test_edit_action_has_reviewable_diff_and_request_bound_review_sha(self):
        book, _ = l2_r3._book(self.record, RAW, "request-edit", None)
        action = {"action": "edit", "evidence_id": "e1", "old": "SyntheticKnown",
                  "new": "SyntheticFixed", "before": "\n", "after": "\n"}
        reply = json_bytes({"identity": book["identity"], "actions": [action]})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            (Path(folder) / "reply.json").write_bytes(reply)
            record = {"book": book, "request": {"request_id": "request-edit"},
                      "reply_sha256": digest(reply)}
            patched, reviewed = l2_r3._review(path, record, RAW)
        self.assertIn(b"SyntheticFixed", patched)
        self.assertIn("-SyntheticKnown", reviewed["diff"])
        self.assertEqual(reviewed["sha256"], digest(json_bytes([
            "request-edit", record["reply_sha256"], {k: v for k, v in reviewed.items() if k != "sha256"}])))

    def test_expired_unknown_state_is_cleaned_without_consuming_original_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "state.json"
            failed_log = root / "l1-failure.log"
            failed_log.write_bytes(b"synthetic only")
            record = {"status": "unknown", "expires_at": 1, "compiled_dir": "",
                      "request": {"request_id": "synthetic"}}
            put_json(path, record)
            expired = l2_r3._expire(path, record)
            self.assertEqual(expired["status"], "expired")
            self.assertFalse(failed_log.exists())


class FailureEligibilityTests(unittest.TestCase):
    def test_missing_capture_is_not_eligible(self):
        with patch("slcn.ai_workflow.load_state", return_value={}), \
             patch("slcn.ai_workflow_convert._tree_shas", return_value={}):
            self.assertIsNone(l2_r3.record_failure("/tmp/work", {}, {}, "/tmp/candidate",
                                                capture=None, validated_bib="", support={}, prior_shas={},
                                                had_final=False))

    def test_failed_rolled_back_is_not_eligible_without_matching_capture_identity(self):
        capture = {"l1": {"attempts": [{"result": "failed-rolled-back"}],
                           "candidate_sha256": digest(RAW), "phase": "xelatex-1", "returncode": 1},
                   "failure": ("run", 1, 1, b"synthetic diagnostic")}
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder)
            (candidate / "sample.tex").write_bytes(RAW)
            state = {"task_id": "synthetic-task", "conversion": {
                "stem": "sample", "inputs": {"source": str(candidate / "source.tex")}}}
            preview = {"text_new_sha256": digest(RAW)}
            with patch.object(l2_r3, "capture_compile", return_value={"candidate_id": "other"}) as verify:
                self.assertIsNone(l2_r3.record_failure(folder, state, preview, candidate, capture=capture,
                                                    validated_bib="", support={}, prior_shas={},
                                                    had_final=False))
            verify.assert_called_once()

    def test_recovery_guard_file_blocks_eligibility_before_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder)
            (candidate / ".compilefix-sample.transaction.json").write_text("pending")
            state = {"conversion": {"stem": "sample"}}
            self.assertIsNone(l2_r3.record_failure(folder, state, {}, candidate, capture={"bound": True},
                                                validated_bib="", support={}, prior_shas={},
                                                had_final=False))


class IsolatedCompileTests(unittest.TestCase):
    def test_compile_failure_retains_original_preview_and_marks_retryable_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / "task"
            task.mkdir()
            candidate = root / "active.tex"
            candidate.write_bytes(RAW)
            path = task / "state.json"
            state = {"task_id": "synthetic-task", "conversion": {"stage_dir": str(root),
                "stem": "sample", "bib_name": "", "inputs": {"source": str(root / "source.tex")}}}
            preview = {"preview_id": "preview", "candidate_bib": "", "text_new_sha256": digest(RAW)}
            record = {"identity": {"task_id": "synthetic-task"}, "stem": "sample", "support": {},
                      "request": {"request_id": "request"}, "review": {"sha256": "review", "diff": "diff"},
                      "evidence_id": "evidence", "status": "reviewed"}
            patched = RAW.replace(b"SyntheticKnown", b"SyntheticFixed")
            record["review"]["new_sha256"] = digest(patched)
            with patch.object(l2_r3, "_review", return_value=(patched, record["review"])), \
                 patch("slcn.ai_workflow_convert._read_candidate", return_value=None), \
                 patch("slcn.ai_workflow_convert._check_references"), \
                 patch.object(l2_r3.compiler, "support_manifest", return_value={}), \
                 patch.object(l2_r3.compiler, "copy_support"), \
                 patch.object(l2_r3.compiler, "compile_candidate", return_value=SimpleNamespace(
                     state="failed", candidate_sha256=digest(patched), phase="xelatex-1", run_id="run")):
                with self.assertRaises(RepairError):
                    l2_r3._adopt(str(root), path, record, state, preview, RAW, "review")
            self.assertEqual(record["status"], "compile_failed")
            self.assertEqual(candidate.read_bytes(), RAW)
            self.assertEqual(preview["text_new_sha256"], digest(RAW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
