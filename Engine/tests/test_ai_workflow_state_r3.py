"""R3 public Bib workflow contracts."""
import json
import os
import threading
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slcn import ai_workflow as w


class WorkflowStateR3(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = folder.name
        self.bib = Path(self.work, "in.bib")
        self.tex = Path(self.work, "in.tex")
        self.old = "@article{K1, title={One}}\n@article{K2, title={Two}}\n"
        self.bib.write_text(self.old, encoding="utf-8")
        self.tex.write_text(r"See \cite{K1}.", encoding="utf-8")

    def prepare(self, **options):
        return w.prepare(work_dir=self.work, entry=w.ENTRY_BIB,
                         bib_text=self.old, tex_text=self.tex.read_text(),
                         target_path=str(self.bib), tex_path=str(self.tex),
                         taskbook_version=3, **options)

    def import_reply(self, task_id, tail=""):
        return w.import_reply(work_dir=self.work, text=(
            f"% TASK-ID: {task_id}\n"
            "```bibtex\n@article{K1, title={Changed}}\n```\n" + tail))

    def test_r3_prepare_persists_version_and_taskbook(self):
        task = self.prepare()
        state = w.load_state(self.work)
        self.assertEqual(state["schema_version"], "slcn.ai_workflow/3")
        self.assertTrue(os.path.isfile(w.state_path(self.work)))
        self.assertFalse(Path(self.work, "state.json").exists())
        result = w.read_taskbook(work_dir=self.work, task_id=task.task_id)
        self.assertTrue(result["ok"])
        self.assertIn(f"% TASK-ID: {task.task_id}", result["text"])

    def test_rejected_reply_cannot_be_recovered_as_a_candidate(self):
        task = self.prepare()
        result = self.import_reply(task.task_id, "```json\n{broken\n```\n")
        self.assertFalse(result["ok"])
        self.assertIsNone(w.load_state(self.work).get("active_reply"))
        self.assertFalse(w.preview(work_dir=self.work).ok)

    def test_subset_preserves_undelegated_entries_and_correction_invalidates_preview(self):
        task = self.prepare()
        self.assertEqual(task.state["context"]["sets"]["T"], ["K1"])
        self.assertTrue(self.import_reply(task.task_id)["ok"])
        preview = w.preview(work_dir=self.work)
        self.assertTrue(preview.ok, preview.blocked)
        self.assertEqual(preview.bib["text"], self.old)
        self.assertEqual(self.bib.read_bytes(), self.old.encode("utf-8"))
        rows = preview.state["conflicts"]
        self.assertEqual([(row["key"], row["field"]) for row in rows], [("K1", "title")])
        row = rows[0]
        self.assertEqual((row["original"], row["reply"], row["default"]),
                         ("One", "Changed", "original"))
        selected = w.preview(work_dir=self.work, decisions={row["conflict_id"]: "reply"})
        self.assertTrue(selected.ok, selected.blocked)
        self.assertNotEqual(selected.preview_id, preview.preview_id)
        self.assertIn("title={Changed}", selected.bib["text"])
        self.assertTrue(selected.bib["text"].endswith('@article{K2, title={Two}}\n'))
        with self.assertRaises(w.WorkflowError):
            w.apply(work_dir=self.work, preview_id=preview.preview_id)
        self.assertTrue(self.import_reply(task.task_id, "\nExtra commentary\n")["ok"])
        self.assertIsNone(w.load_state(self.work)["preview"])
        with self.assertRaises(w.WorkflowError):
            w.apply(work_dir=self.work, preview_id=selected.preview_id)
        self.assertEqual(self.bib.read_bytes(), self.old.encode("utf-8"))

    def test_unselected_bib_never_writes_even_if_adopted(self):
        task = self.prepare(scope=w.Scope(exec={w.CAT_TYPESET}, adopt={w.CAT_BIB_VERIFY}))
        self.assertTrue(self.import_reply(task.task_id)["ok"])
        preview = w.preview(work_dir=self.work, adopt={w.CAT_BIB_VERIFY})
        self.assertTrue(preview.ok, preview.blocked)
        result = w.apply(work_dir=self.work, preview_id=preview.preview_id)
        self.assertEqual(result["wrote"], 0)
        self.assertEqual(self.bib.read_text(), self.old)

    def test_signed_reply_always_uses_subset_semantics(self):
        task = self.prepare()
        reply = f"% TASK-ID: {task.task_id}\n```bibtex\n@article{{K1,title={{Changed}}}}\n```\n"
        result = w.import_reply(work_dir=self.work, text=reply, source_kind="complete_external")
        self.assertEqual(result["source_kind"], "ai_subset")

    def test_explicit_external_replacement_drops_unreferenced_entries(self):
        task = self.prepare()
        self.assertTrue(self.import_reply(task.task_id)["ok"])
        plain = "@article{K1, title={Changed externally}}\n"
        result = w.import_reply(work_dir=self.work, text=plain, source_kind="complete_external")
        self.assertTrue(result["confirmation_required"])
        result = w.import_reply(work_dir=self.work, text=plain, source_kind="complete_external",
                                replace_active=True)
        self.assertTrue(result["ok"])
        candidate = w.preview(work_dir=self.work)
        self.assertNotIn("K2", candidate.bib["text"])
        self.assertEqual(candidate.bib["counts"]["dropped"], 1)

    def test_request_checks_taskbook_identity_before_calling_client(self):
        task = self.prepare()
        client = w.FakeClient(reply="@article{K1,title={Changed}}")
        result = w.request(work_dir=self.work, taskbook_text=task.taskbook_text + "changed",
                           client=client)
        self.assertFalse(result["ok"])
        self.assertEqual(len(client.calls), 0)

    def test_request_refuses_budget_and_private_body_before_call(self):
        client = w.FakeClient(reply="@article{K1,title={Changed}}")
        task = self.prepare(scope=w.Scope(exec={w.CAT_BIB_VERIFY}),
                            request_options={"article_budget": 1})
        result = w.request(work_dir=self.work, taskbook_text=task.taskbook_text, client=client)
        self.assertFalse(result["ok"])
        self.assertEqual(len(client.calls), 0)
        task = self.prepare(source_evidence={"body_units": [{"unit_id": "T-1", "text": "Private"}]})
        result = w.request(work_dir=self.work, taskbook_text=task.taskbook_text, client=client)
        self.assertFalse(result["ok"])
        self.assertEqual(len(client.calls), 0)

    def test_successful_api_reply_is_active_and_success_cannot_be_resent(self):
        task = self.prepare(scope=w.Scope(exec={w.CAT_BIB_VERIFY}, adopt={w.CAT_BIB_VERIFY}))
        client = w.FakeClient(reply="@article{K1,title={Changed}}", fail_times=1)
        options = {"work_dir": self.work, "taskbook_text": task.taskbook_text, "client": client}
        self.assertFalse(w.request(**options)["ok"])
        self.assertFalse(w.request(**options)["ok"])
        outcome = w.request(**options, retry=True)
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual(outcome["capacity"], "容量未验证")
        self.assertTrue(w.status(self.work)["imported"])
        self.assertTrue(w.preview(work_dir=self.work).ok)
        self.assertFalse(w.request(**options, retry=True)["ok"])
        self.assertEqual(len(client.calls), 2)

    def test_retry_cannot_interrupt_live_request(self):
        task = self.prepare(scope=w.Scope(exec={w.CAT_BIB_VERIFY}))
        entered, release = threading.Event(), threading.Event()

        class BlockingClient(w.FakeClient):
            def complete(self, **params):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test request timed out")
                return super().complete(**params)

        client = BlockingClient(reply="@article{K1,title={Changed}}")
        options = {"work_dir": self.work, "taskbook_text": task.taskbook_text, "client": client}
        outcomes = []
        sending = threading.Thread(target=lambda: outcomes.append(w.request(**options)))
        sending.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertFalse(w.request(**options, retry=True)["ok"])
        finally:
            release.set()
            sending.join(3)
        self.assertTrue(outcomes[0]["ok"], outcomes)
        self.assertEqual(len(client.calls), 1)

    def test_copy_apply_and_undo_leave_input_bib_unchanged(self):
        path = Path(self.work, "copy.bib")
        task = self.prepare(bib_out=str(path))
        self.assertTrue(self.import_reply(task.task_id)["ok"])
        preview = w.preview(work_dir=self.work)
        self.assertTrue(preview.ok, preview.blocked)
        self.assertEqual(preview.bib["text"], self.old)
        self.assertEqual(self.bib.read_bytes(), self.old.encode("utf-8"))
        self.assertFalse(path.exists())
        rows = preview.state["conflicts"]
        self.assertEqual([(row["key"], row["field"]) for row in rows], [("K1", "title")])
        row = rows[0]
        self.assertEqual((row["original"], row["reply"], row["default"]),
                         ("One", "Changed", "original"))
        selected = w.preview(work_dir=self.work, decisions={row["conflict_id"]: "reply"})
        self.assertTrue(selected.ok, selected.blocked)
        self.assertNotEqual(selected.preview_id, preview.preview_id)
        self.assertIn("title={Changed}", selected.bib["text"])
        self.assertTrue(selected.bib["text"].endswith('@article{K2, title={Two}}\n'))
        self.assertFalse(path.exists())
        self.assertEqual(w.status(self.work)["target_bib"], str(path))
        with self.assertRaises(w.WorkflowError):
            w.apply(work_dir=self.work, preview_id=preview.preview_id)
        self.assertFalse(path.exists())
        result = w.apply(work_dir=self.work, preview_id=selected.preview_id)
        self.assertEqual(result["wrote"], 1)
        self.assertEqual(self.bib.read_bytes(), self.old.encode("utf-8"))
        self.assertEqual(path.read_bytes(), selected.bib["text"].encode("utf-8"))
        self.assertIn("title={Changed}", path.read_text(encoding="utf-8"))
        self.assertTrue(path.read_bytes().endswith(b'@article{K2, title={Two}}\n'))
        self.assertTrue(w.undo(work_dir=self.work)["ok"])
        self.assertFalse(path.exists())
        self.assertEqual(self.bib.read_bytes(), self.old.encode("utf-8"))


    def test_bib_and_tex_apply_rolls_back_when_second_write_fails(self):
        self.tex.write_text("This is teh example. " + r"See \cite{K1}.", encoding="utf-8")
        copy = Path(self.work, "text-copy.tex")
        scope = w.Scope(exec={w.CAT_BIB_VERIFY, w.CAT_TEXT}, adopt={w.CAT_BIB_VERIFY, w.CAT_TEXT})
        task = self.prepare(scope=scope, text_target=str(copy))
        unit = task.state["context"]["body_units"][0]["unit_id"]
        response = {"schema": "slcn.ai_result/3", "text": {"status": "completed", "items": [{
            "item_id": "TXT-1", "unit_id": unit, "kind": "typo", "certainty": "clear",
            "old": "teh", "new": "the", "before": "This is ", "after": " example.", "reason": "typo",
        }]}}
        self.assertTrue(self.import_reply(task.task_id, f"```json\n{json.dumps(response)}\n```\n")["ok"])
        preview = w.preview(work_dir=self.work)
        self.assertTrue(preview.ok)
        self.assertTrue(preview.text["applied"])
        real_replace = os.replace

        def fail_tex_write(source, target):
            if Path(target) == copy:
                raise PermissionError("simulated failure after Bib write")
            real_replace(source, target)

        def fail_tex_create(source, target):
            raise PermissionError("simulated failure after Bib write")

        with patch.object(os, "replace", side_effect=fail_tex_write), patch.object(os, "link", side_effect=fail_tex_create):
            with self.assertRaises((OSError, w.WorkflowError)):
                w.apply(work_dir=self.work, preview_id=preview.preview_id)
        self.assertEqual(self.bib.read_text(), self.old)
        self.assertFalse(copy.exists())
    def test_tampered_candidate_cannot_be_applied(self):
        task = self.prepare()
        self.assertTrue(self.import_reply(task.task_id)["ok"])
        preview = w.preview(work_dir=self.work)
        Path(preview.state["preview"]["candidate_bib"]).write_text("tampered", encoding="utf-8")
        with self.assertRaises(w.WorkflowError):
            w.apply(work_dir=self.work, preview_id=preview.preview_id)
        self.assertEqual(self.bib.read_text(), self.old)

    def test_frozen_definitions_and_undelegated_entries_survive(self):
        self.old = ('@string{a = {Old Press}}\n% prior source\n'
                    '@article{K1,title={One}, publisher=a}\n'
                    '@article{K2,title={Two}}\n@preamble{"old"}\n')
        self.bib.write_text(self.old, encoding="utf-8")
        self.tex.write_text(r"See \cite{K1,K2}.", encoding="utf-8")
        task = self.prepare(safe_hit_keys={"K1"})
        result = w.import_reply(work_dir=self.work,
                               text=f"""% TASK-ID: {task.task_id}
```bibtex
@string{{a = {{New Press}}}}
@article{{K1,title={{Attempted rewrite}}}}
@article{{K2,title={{Changed}}}}
@preamble{{"new"}}
```
""")
        self.assertTrue(result["ok"])
        preview = w.preview(work_dir=self.work)
        self.assertTrue(preview.ok, preview.blocked)
        self.assertIn('@article{K1,title={One}, publisher=a}', preview.bib["text"])
        self.assertIn('@string{a = {Old Press}}', preview.bib["text"])
        self.assertIn('% prior source', preview.bib["text"])
        self.assertIn('@preamble{"old"}', preview.bib["text"])
        self.assertNotIn('New Press', preview.bib["text"])
        self.assertNotIn('@preamble{"new"}', preview.bib["text"])


    def test_ai_cannot_fill_missing_reference_without_source_evidence(self):
        self.tex.write_text(r"See \cite{K1,Missing}.", encoding="utf-8")
        task = self.prepare()
        raw = ("@article{K1,title={One}}\n"
               "@article{Missing,title={Guessed}}")
        reply = f"% TASK-ID: {task.task_id}\n```bibtex\n{raw}\n```\n"
        self.assertTrue(w.import_reply(work_dir=self.work, text=reply)["ok"])
        result = w.preview(work_dir=self.work)
        self.assertFalse(result.ok)
        self.assertIn("Missing", str(result.blocked))
        task = self.prepare(source_evidence={"refs_by_key": {"Missing": "A real source."}})
        authorized = reply.replace(reply.splitlines()[0], f"% TASK-ID: {task.task_id}")
        self.assertTrue(w.import_reply(work_dir=self.work, text=authorized)["ok"])
        self.assertTrue(w.preview(work_dir=self.work).ok)
    def test_convert_preview_result_is_json_serializable(self):
        task = w.prepare(work_dir=self.work, entry=w.ENTRY_CONVERT, bib_text=self.old,
                         tex_text=self.tex.read_text(), taskbook_version=3,
                         scope=w.Scope(exec={w.CAT_TYPESET}, adopt={w.CAT_TYPESET}),
                         conversion={"stage_dir": self.work})
        body = {"schema": "slcn.ai_result/3", "typeset": {"status": "completed", "items": [
            {"item_id": "RPT-1", "action": "report_only", "reason": "待核", "evidence_ids": []}
        ]}}
        result = w.import_reply(work_dir=self.work, text=(
            f"% TASK-ID: {task.task_id}\n```json\n{json.dumps(body)}\n```\n"))
        self.assertTrue(result["ok"])
        preview = w.preview(work_dir=self.work)
        self.assertTrue(preview.ok, preview.blocked)
        self.assertIn("RPT-1", json.dumps(preview.as_dict()))


if __name__ == "__main__":
    unittest.main()
