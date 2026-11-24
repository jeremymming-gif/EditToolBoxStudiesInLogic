"""Exercise expiration, deletion and bounded manual rounds across CLI processes."""

import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "compilefix"))
from cli_fixture import BODY_LINE, FILLER, STEM, CompileFixFixture, document, sha  # noqa: E402


DAY = 24 * 60 * 60


class CompileFixRetentionTests(CompileFixFixture):
    def test_30_day_boundary_late_reply_and_automatic_private_cleanup(self):
        issued_at = 1_800_000_000
        task_id = self.run_cmd_at(issued_at, "export", self.out, STEM, "--synthetic").stdout.decode().strip()
        preview = self.run_cmd_at(issued_at, "preview", task_id).stdout
        book = json.loads(preview)
        self.run_cmd_at(issued_at, "confirm", task_id, sha(preview))
        original = self.tex.read_bytes()
        expires = issued_at + 30 * DAY
        for when, expected in ((expires - 1, "exported"), (expires, "expired"), (expires + 1, "expired")):
            with self.subTest(epoch=when):
                status = json.loads(self.run_cmd_at(when, "status", task_id).stdout)
                self.assertEqual((status["expires_at"], status["state"]), (expires, expected))
        self.assertTrue((self.task_dir(task_id) / "taskbook.json").exists())
        late_reply = self.put_reply(book, self.edit(book))
        self.run_cmd_at(expires, "import", task_id, late_reply, code=2)
        self.assertFalse((self.task_dir(task_id) / "taskbook.json").exists())
        self.assertFalse((self.task_dir(task_id) / "reply.json").exists())
        self.assertEqual(self.tex.read_bytes(), original)
        self.assertFalse(self.pdf.exists())

    def test_expired_interrupted_task_removes_private_payloads(self):
        task_id, _ = self.prepare_edit()
        state_path = self.task_dir(task_id) / "state.json"
        state = json.loads(state_path.read_bytes())
        state.update(state="applied", pid=None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        original = self.tex.read_bytes()
        self.assertTrue((self.task_dir(task_id) / "reply.json").is_file())
        epoch = state["expires_at"] + 1
        interrupted = json.loads(self.run_cmd_at(epoch, "status", task_id).stdout)
        self.assertEqual(interrupted["state"], "unknown")
        self.run_cmd_at(epoch, "purge")
        self.assertEqual(self.status(task_id)["state"], "expired")
        self.assertFalse((self.task_dir(task_id) / "taskbook.json").exists())
        self.assertFalse((self.task_dir(task_id) / "reply.json").exists())
        self.assertEqual(self.tex.read_bytes(), original)
        self.assertFalse(self.pdf.exists())

    def test_manual_purge_removes_private_taskbook_and_reply_only(self):
        task_id, book = self.authorize()
        editor_reply = self.put_reply(book, self.edit(book))
        self.run_cmd("import", task_id, editor_reply)
        paths = [self.task_dir(task_id) / filename for filename in ("taskbook.json", "reply.json")]
        for private_artifact in paths:
            self.assertTrue(private_artifact.is_file())
        original = self.tex.read_bytes()
        self.run_cmd("purge", task_id)
        self.assertFalse(any(item.exists() for item in paths))
        self.assertTrue(editor_reply.is_file())
        self.assertEqual(self.tex.read_bytes(), original)
        self.assertFalse(self.pdf.exists())
        self.run_cmd("import", task_id, editor_reply, code=2)

    def test_round_limit_supported_values_and_same_target_accumulation(self):
        for disallowed in (0, 4):
            with self.subTest(limit=disallowed):
                self.run_cmd("export", self.out, STEM, "--synthetic", "--round-limit", disallowed, code=2)
        for limit in (1, 3):
            with self.subTest(limit=limit):
                self.use_output(f"round-limit-{limit}")
                ids = []
                for round_number in range(limit + 1):
                    self.write_candidate(document(f"{BODY_LINE}\nSynthetic round {round_number}.\n" + FILLER))
                    if round_number == limit:
                        self.run_cmd("export", self.out, STEM, "--synthetic", "--round-limit", limit, code=2)
                        break
                    options = () if limit == 1 else ("--round-limit", limit)
                    task_id = self.export(*options)
                    self.assertEqual(self.status(task_id)["round_limit"], limit)
                    self.assertNotIn(task_id, ids)
                    ids.append(task_id)
                    raw, book = self.preview(task_id)
                    self.run_cmd("import", task_id, self.put_reply(book, self.edit(book)), code=2)
                    self.run_cmd("confirm", task_id, sha(raw))
                    self.run_cmd("cancel", task_id)
                self.assertEqual(len(ids), limit)

    def test_status_does_not_change_durable_state_for_active_task(self):
        task_id, _ = self.authorize()
        state = self.task_dir(task_id) / "state.json"
        before = state.read_bytes(), state.stat().st_mtime_ns
        self.assertEqual(self.status(task_id)["state"], "exported")
        self.assertEqual((state.read_bytes(), state.stat().st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
