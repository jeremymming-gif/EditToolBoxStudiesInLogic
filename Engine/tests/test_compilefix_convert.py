"""Test both real convert.main CLI failure branches with no journal template I/O."""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compilefix"
sys.path.insert(0, str(FIXTURES))
from cli_fixture import NEW, OLD, STEM, CompileFixFixture, document, sha  # noqa: E402


class CompileFixConvertTests(CompileFixFixture):
    def run_failed_conversion(self, *, cooperative=False, label="default", real_compile=False,
                              source_dir=None):
        case = self.root / label
        source = (case / "source" if source_dir is None else source_dir) / "incoming.tex"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(document().encode("utf-8"))
        out_root = case / "conversion"
        output = out_root / STEM
        arguments = ["--source", str(source), "--out", str(out_root), "--name", STEM,
                     "--lang", "en", "--compile", "--no-original", "--no-handoff"]
        if cooperative:
            output = out_root / "explicit-staging"
            arguments += ["--coop-taskbook-only", "--coop-stage", str(output)]
        env = dict(self.env)
        if real_compile:
            env["COMPILEFIX_FIXTURE_REAL_COMPILE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", str(FIXTURES / "convert_entry.py"), *arguments],
            cwd=self.root, env=env, capture_output=True, timeout=30, check=False,
        )
        stdout, stderr = (value.decode("utf-8") for value in (result.stdout, result.stderr))
        self.assertEqual(result.returncode, 2, stdout + stderr)
        prefix = "暂存目录：" if cooperative else "完成。产物目录："
        self.assertEqual(stdout.splitlines()[-1], prefix + str(output))
        self.assertEqual((output / f"{STEM}.tex").read_bytes(), source.read_bytes())
        if not real_compile:
            self.assertEqual((output / f"{STEM}.pdf").read_bytes(),
                             b"%PDF-1.4\nSynthetic original PDF bytes\n")
        self.assertTrue((output / f"{STEM}.log").is_file())
        if cooperative:
            self.assertFalse((out_root / STEM).exists())
        return source, output, result

    def assert_convert_failure(self, *, cooperative=False, label="default", expect_export=True):
        source, output, result = self.run_failed_conversion(cooperative=cooperative, label=label)
        books = list(self.store.glob("tasks/*/taskbook.json"))
        self.assertEqual(len(books), int(expect_export), result.stdout + result.stderr)
        if not expect_export:
            self.assertIn("编译修复任务书未导出".encode(), result.stderr)
            return output, None
        book = json.loads(books[0].read_bytes())
        task_id = book["identity"]["task_id"]
        self.assertEqual(book["identity"]["candidate_sha256"], sha(source.read_bytes()))
        self.assertEqual(book["diagnostics"][0]["classification"], "editable")
        self.assertEqual(self.status(task_id)["state"], "exported")
        preview = books[0].read_bytes().rstrip(b"\n")
        self.assertIn(preview, result.stdout + result.stderr)
        self.assertFalse((books[0].parent / "reply.json").exists())
        return output, task_id

    def test_ordinary_compile_failure_keeps_completion_line(self):
        self.assert_convert_failure(cooperative=False)

    def test_cooperative_compile_failure_keeps_staging_line(self):
        _, _, result = self.run_failed_conversion(cooperative=True, label="coop-gated")
        self.assertEqual(list(self.store.glob("tasks/*/taskbook.json")), [])
        self.assertNotIn("编译修复任务书".encode(), result.stdout)
        self.assertNotIn("编译修复任务书".encode(), result.stderr)
        self.assertIn("协同初排已完成转换".encode(), result.stderr)

    def test_convert_rejects_private_state_inside_source_before_any_write(self):
        source_dir = self.root / "source-state" / "source"
        self.store = source_dir / "repair"
        self.env["SLCN_COMPILEFIX_STATE_DIR"] = str(self.store)
        source, _, result = self.run_failed_conversion(label="source-state")
        self.assertFalse(self.store.exists(), list(source_dir.iterdir()))
        self.assertEqual(list(source_dir.iterdir()), [source])
        self.assertIn("编译修复任务书未导出".encode(), result.stderr)

    def test_convert_rejects_unicode_alias_to_source_before_any_write(self):
        case = self.root / "unicode-source"
        source_dir = case / "Caf\u00e9"
        alias = case / "Cafe\u0301"
        source_dir.mkdir(parents=True)
        if not alias.is_dir():
            self.skipTest("The synthetic test volume lacks Unicode directory aliases")
        self.assertTrue(os.path.samestat(source_dir.stat(), alias.stat()))
        self.assertNotEqual(os.fsencode(source_dir), os.fsencode(alias))
        self.store = alias / "repair"
        self.assertFalse(self.store.exists())
        self.env.update(SLCN_COMPILEFIX_STATE_DIR=str(self.store),
                        COMPILEFIX_FIXTURE_FORBID_MKDIR=str(self.store))

        source, _, result = self.run_failed_conversion(label=case.name, source_dir=source_dir)

        self.assertFalse(self.store.exists())
        self.assertEqual([entry.name for entry in source_dir.iterdir()], [source.name])
        self.assertIn("编译修复任务书未导出".encode(), result.stderr)

    def test_ordinary_failure_hook_sweeps_expired_payloads_without_recovering_closeout(self):
        def signed_task(label):
            source, output, _ = self.run_failed_conversion(label=label)
            task_id = self.run_cmd("export", output, STEM).stdout.decode("ascii").strip()
            raw, book = self.preview(task_id)
            self.run_cmd("confirm", task_id, sha(raw))
            return source, output, task_id, book

        source, output, expired, book = signed_task("old-expiring")
        external_reply = self.put_reply(book, self.report(book))
        external_before = external_reply.read_bytes()
        self.run_cmd("import", expired, external_reply)
        self.reply = self.root / "interrupted-editor-reply.json"
        pending_source, pending_out, pending, pending_book = signed_task("old-interrupted")
        self.run_cmd("import", pending, self.put_reply(pending_book, self.edit(pending_book)))
        journal, guard, stage = self.seed_interrupted_closeout(pending_out, pending)

        def snapshot(paths):
            return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

        old_private = [self.task_dir(expired) / name for name in ("taskbook.json", "reply.json")]
        self.assertTrue(all(path.is_file() for path in old_private))
        outputs = snapshot([source, pending_source, *output.glob(f"{STEM}.*"),
                            *pending_out.glob(f"{STEM}.*"), guard])
        pending_dir = self.task_dir(pending)
        pending_private = snapshot([journal, pending_dir / "state.json", pending_dir / "taskbook.json",
                                    pending_dir / "reply.json", *(pending_dir / "rollback").glob("*.bak")])
        self.assertTrue(any(path.suffix == ".bak" for path in pending_private))
        self.assertEqual(self.status(expired)["state"], "imported")
        epoch = max(self.status(task_id)["expires_at"] for task_id in (expired, pending)) + 1
        result = self.run_cmd_at(epoch, "status", expired)
        self.assertEqual(json.loads(result.stdout)["state"], "expired")
        self.assertTrue(all(path.is_file() for path in old_private))
        self.env["COMPILEFIX_FIXTURE_EPOCH"] = str(epoch)

        _, new_out, result = self.run_failed_conversion(label="new-trigger")

        self.assertTrue(all(not path.exists() for path in old_private))
        self.assertEqual(external_reply.read_bytes(), external_before)
        self.assertEqual(snapshot(outputs), outputs)
        self.assertEqual(snapshot(pending_private), pending_private)
        self.assertTrue(stage.is_dir())
        issued = [state for path in self.store.glob("tasks/*/state.json")
                  if (state := json.loads(path.read_bytes()))["out_dir"] == str(new_out)]
        self.assertEqual(len(issued), 1, result.stdout + result.stderr)
        self.assertEqual(issued[0]["state"], "exported")
        self.assertEqual(self.status(expired)["state"], "expired")
        self.assertEqual(self.status(pending)["state"], "unknown")

    def test_convert_handles_malformed_target_ledger_without_losing_failure_tail(self):
        out_dir = self.root / "corrupt-ledger" / "conversion" / STEM
        key_bytes = (json.dumps([os.path.normcase(str(out_dir)), os.path.normcase(STEM)],
                                indent=2, ensure_ascii=False) + "\n").encode()
        ledger = self.store / "targets" / f"{sha(key_bytes)}.json"
        ledger.parent.mkdir(parents=True, mode=0o700)
        self.store.chmod(0o700)
        corrupt = b'["synthetic non-object ledger"]\n'
        ledger.write_bytes(corrupt)
        _, _, result = self.run_failed_conversion(label="corrupt-ledger")
        self.assertIn("编译修复任务书未导出".encode(), result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertEqual(ledger.read_bytes(), corrupt)
        self.assertEqual(list(self.store.glob("tasks/*/taskbook.json")), [])

    def test_unbound_same_stem_log_cannot_authorize_edit(self):
        self.env["COMPILEFIX_FIXTURE_UNBOUND_LOG"] = "1"
        _, output, _ = self.run_failed_conversion(label="unbound-log")
        result = self.run_cmd("export", output, STEM)
        task_id = result.stdout.decode("ascii").strip()
        actual, book = self.preview(task_id)
        self.assertEqual([item["classification"] for item in book["diagnostics"]], ["unknown"])
        self.run_cmd("confirm", task_id, sha(actual))
        for evidence in book["evidence"]:
            with self.subTest(evidence=evidence["evidence_id"]):
                action = dict(self.edit(book), evidence_id=evidence["evidence_id"])
                self.run_cmd("import", task_id, self.put_reply(book, action), code=2)
        self.assertFalse((self.task_dir(task_id) / "reply.json").exists())

        if shutil.which("xelatex", path=self.tool_path) is None:
            self.skipTest("BLOCKED: XeLaTeX is unavailable for the bound-log positive control")
        self.env.pop("COMPILEFIX_FIXTURE_UNBOUND_LOG")
        self.store = self.root / "bound-control-state"
        self.env["SLCN_COMPILEFIX_STATE_DIR"] = str(self.store)
        source, output, first_compile = self.run_failed_conversion(label="bound-log", real_compile=True)
        self.use_output(output.relative_to(self.root))
        self.assertIn(b"Misplaced alignment tab character", self.log.read_bytes())
        task_id = self.run_cmd("export", self.out, STEM).stdout.decode("ascii").strip()
        actual, book = self.preview(task_id)
        self.assertIn(actual, first_compile.stdout)
        self.assertEqual(book["diagnostics"][0]["classification"], "editable")
        original = source.read_bytes()
        self.run_cmd("confirm", task_id, sha(actual))
        self.run_cmd("import", task_id, self.put_reply(book, self.edit(book)))
        self.assertEqual(self.tex.read_bytes(), original)
        diff = self.review(task_id)
        self.assert_finish_line(self.run_cmd("apply", task_id, sha(diff)))
        status = self.status(task_id)
        self.assertEqual(status["state"], "completed")
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.tex.read_bytes(), original.replace(OLD.encode(), NEW.encode(), 1))
        self.assertTrue(self.pdf.read_bytes().startswith(b"%PDF-"))
        for run in ("isolated", "output"):
            self.assertEqual(status["runs"][run]["exit_code"], 0)

    def test_convert_preview_bytes_and_confirmation_survive_crlf_stdout(self):
        self.env["COMPILEFIX_FIXTURE_CRLF"] = "1"
        _, _, result = self.run_failed_conversion(label="crlf")
        self.assertIn(b"\r\n", result.stdout, "the fixture must exercise CRLF translation")
        match = re.search(rb"(?ms)^\{\r?\n.*?^\}\r?\n", result.stdout)
        self.assertIsNotNone(match, result.stdout)
        actual = match.group()
        task_id = json.loads(actual)["identity"]["task_id"]
        self.assertEqual(actual, (self.task_dir(task_id) / "taskbook.json").read_bytes())
        self.assertIn(f"SHA-256 {sha(actual)}".encode(), result.stdout)
        self.run_cmd("confirm", task_id, sha(actual))

    def select_round_limit(self, limit):
        self.store = self.root / f"state-limit-{limit}"
        self.env["SLCN_COMPILEFIX_STATE_DIR"] = str(self.store)
        self.env["SLCN_COMPILEFIX_ROUND_LIMIT"] = str(limit)

    def test_convert_hook_accepts_and_freezes_supported_round_limits(self):
        for limit in (1, 3):
            with self.subTest(limit=limit):
                self.select_round_limit(limit)
                output, task_id = self.assert_convert_failure(label=f"round-{limit}")
                status = self.status(task_id)
                self.assertEqual((status["round"], status["round_limit"]), (1, limit))
                higher = 2 if limit == 1 else 4
                self.run_cmd("export", output, STEM, "--round-limit", higher, code=2)
                self.assertEqual(self.status(task_id)["round_limit"], limit)
                for round_number in range(2, limit + 1):
                    self.run_cmd("cancel", task_id)
                    next_id = self.run_cmd("export", output, STEM).stdout.decode("ascii").strip()
                    self.assertNotEqual(next_id, task_id)
                    task_id = next_id
                    status = self.status(task_id)
                    self.assertEqual((status["round"], status["round_limit"]), (round_number, limit))
                self.run_cmd("cancel", task_id)
                self.run_cmd("export", output, STEM, code=2)
                self.assertEqual(len(list(self.store.glob("tasks/*/taskbook.json"))), limit)

    def test_convert_hook_rejects_out_of_range_round_limits_without_losing_artifacts(self):
        for limit in (0, 4):
            with self.subTest(limit=limit):
                self.select_round_limit(limit)
                output, _ = self.assert_convert_failure(label=f"invalid-{limit}", expect_export=False)
                self.run_cmd("export", output, STEM, code=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
