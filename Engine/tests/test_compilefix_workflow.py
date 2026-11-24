"""Stage A identity, authorization, redaction and review contracts."""

import difflib
import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "compilefix"))
from cli_fixture import (  # noqa: E402
    BODY_LINE, FILLER, NEW, OLD, CompileFixFixture, document, sha,
)


class CompileFixWorkflowTests(CompileFixFixture):
    def test_preview_digest_and_identity_are_stable(self):
        not_requested = self.status("0" * 64)
        self.assertEqual(not_requested["state"], "not_requested")
        task_id = self.export()
        a, book = self.preview(task_id)
        self.assertEqual(self.export(), task_id)
        b, _ = self.preview(task_id)
        self.assertEqual(a, b)
        identity = book["identity"]
        self.assertEqual(identity["task_id"], task_id)
        self.assertEqual(identity["candidate_sha256"], sha(self.tex.read_bytes()))
        self.assertEqual(identity["schema_version"], 1)
        self.assertEqual(identity["prompt_version"], 1)
        self.assertRegex(identity["diagnostic_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.status(task_id)["identity"], identity)
        self.assertEqual(self.status(task_id)["state"], "exported")

    def test_status_rejects_json_list_without_traceback_or_state_write(self):
        task_id = self.export()
        state = self.task_dir(task_id) / "state.json"
        corrupt = b'["synthetic non-object state"]\n'
        state.write_bytes(corrupt)
        before = state.stat().st_mtime_ns
        result = self.run_cmd("status", task_id, code=2)
        self.assertEqual(result.stdout, b"")
        self.assertIn("编译修复".encode(), result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertEqual(state.read_bytes(), corrupt)
        self.assertEqual(state.stat().st_mtime_ns, before)

    def test_confirmation_binds_actual_preview_and_each_task(self):
        one = self.export()
        raw = (self.task_dir(one) / "taskbook.json").read_bytes()
        book = json.loads(raw)
        self.run_cmd("confirm", one, sha(raw), code=2)
        self.run_cmd("import", one, self.put_reply(book, self.edit(book)), code=2)
        raw, book = self.preview(one)
        self.run_cmd("confirm", one, sha(raw.rstrip(b"\n")), code=2)
        self.run_cmd("confirm", one, sha(raw))

        self.use_output("second-candidate")
        self.write_candidate(document(BODY_LINE + "\nSecond synthetic candidate.\n" + FILLER))
        two = self.export()
        two_raw, two_book = self.preview(two)
        self.assertNotEqual(two, one)
        self.run_cmd("confirm", two, sha(raw), code=2)
        self.run_cmd("import", two, self.put_reply(two_book, self.edit(two_book)), code=2)
        self.run_cmd("confirm", two, sha(two_raw))
        self.run_cmd("import", two, self.put_reply(two_book, self.edit(two_book)))

    def test_import_rejects_wrong_identity_versions_and_action_schema(self):
        task_id, book = self.authorize()
        edit = self.edit(book)
        mutations = {
            "task_id": "0" * 64,
            "candidate_sha256": "1" * 64,
            "schema_version": 2,
            "prompt_version": 2,
            "diagnostic_sha256": "2" * 64,
        }
        for key, changed in mutations.items():
            with self.subTest(field=key):
                identity = dict(book["identity"], **{key: changed})
                self.run_cmd("import", task_id, self.put_reply(book, edit, identity=identity), code=2)
        self.run_cmd("import", task_id,
                     self.put_reply(book, {**edit, "action": "verify_reference"}), code=2)
        self.reply.write_bytes(b'{"identity":')
        self.run_cmd("import", task_id, self.reply, code=2)
        duplicate = json.dumps({"identity": book["identity"], "actions": [edit]})
        self.reply.write_text('{"actions": [], ' + duplicate[1:], encoding="utf-8")
        self.run_cmd("import", task_id, self.reply, code=2)
        self.assertEqual(self.status(task_id)["state"], "exported")

    def test_duplicate_reply_and_cancelled_reply_are_rejected(self):
        task_id, book = self.authorize()
        reply = self.put_reply(book, self.edit(book))
        self.run_cmd("import", task_id, reply)
        self.run_cmd("import", task_id, reply, code=2)
        self.assertEqual(self.status(task_id)["state"], "imported")
        self.run_cmd("cancel", task_id)
        self.assertEqual(self.status(task_id)["state"], "cancelled")
        self.run_cmd("import", task_id, reply, code=2)
        self.assertEqual(self.tex.read_text(encoding="utf-8"), document())

    def test_cross_manuscript_reply_is_rejected_after_confirming_both(self):
        _, first = self.authorize()
        first_reply = self.put_reply(first, self.edit(first))
        self.use_output("other-manuscript")
        self.write_candidate(document(BODY_LINE + "\nAnother synthetic manuscript.\n" + FILLER))
        second_id, second = self.authorize()
        self.assertNotEqual(first["identity"], second["identity"])
        self.run_cmd("import", second_id, first_reply, code=2)
        self.assertEqual(self.status(second_id)["state"], "exported")

    def test_candidate_changed_after_review_is_never_overwritten(self):
        task_id, diff = self.prepare_edit()
        changed = self.tex.read_bytes().replace(b"generated filler", b"human corrected", 1)
        self.tex.write_bytes(changed)
        result = self.run_cmd("apply", task_id, sha(diff), code=2)
        self.assert_finish_line(result)
        self.assertEqual(self.tex.read_bytes(), changed)
        self.assertFalse(self.pdf.exists())

    def test_review_contains_full_diff_and_requires_exact_acceptance(self):
        before = self.tex.read_bytes()
        task_id, diff = self.prepare_edit()
        after = before.replace(OLD.encode(), NEW.encode(), 1)
        expected = list(difflib.unified_diff(
            before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True),
        ))
        actual = diff.decode().splitlines(keepends=True)
        self.assertTrue(actual[0].startswith("--- "), actual)
        self.assertTrue(actual[1].startswith("+++ "), actual)
        self.assertEqual(actual[2:], expected[2:])
        self.assertNotIn(str(self.out).encode(), diff)
        self.assertEqual(self.tex.read_bytes(), before)
        self.assertFalse(self.pdf.exists())
        result = self.run_cmd("apply", task_id, "0" * 64, code=2)
        self.assert_finish_line(result)
        self.assertEqual(self.status(task_id)["state"], "imported")
        self.assertEqual(self.tex.read_bytes(), before)
        self.assertFalse(self.pdf.exists())

    def test_report_only_uses_signed_evidence_and_never_writes_candidate(self):
        before = self.tex.read_bytes()
        task_id, book = self.authorize()
        evidence = book["evidence"][0]
        diagnostic = book["diagnostics"][evidence["diagnostic_index"]]
        self.assertEqual(evidence["source_line"], diagnostic["source_line"])
        self.assertEqual(diagnostic["source_line"], 3)
        for extra in ({"path": "/synthetic/arbitrary.tex"}, {"line": 999},
                      {"log_line": 999}, {"evidence_id": "invented"}):
            with self.subTest(extra=extra):
                self.run_cmd("import", task_id,
                             self.put_reply(book, self.report(book, **extra)), code=2)
        reason = self.report(book)["reason"]
        self.run_cmd("import", task_id, self.put_reply(book, self.report(book)))
        [bound] = self.status(task_id)["report_only"]
        self.assertEqual(bound["evidence_id"], evidence["evidence_id"])
        self.assertEqual(bound["source_line"], diagnostic["source_line"])
        self.assertEqual(bound["log_line"], diagnostic["log_line"])
        self.assertEqual(bound["reason_sha256"], sha(reason.encode("utf-8")))
        self.assertEqual(self.tex.read_bytes(), before)
        self.assertFalse(self.pdf.exists())

    def test_cli_mark_accepts_actor_at_128_characters_but_rejects_129(self):
        task_id, _ = self.authorize()
        actor = "编" * 128
        self.run_cmd("mark", task_id, "frozen", "--actor", actor)
        protection = self.status(task_id)["protection"]
        self.assertEqual(protection["actor"], actor)
        self.run_cmd("mark", task_id, "confirmed", "--actor", actor + "辑", code=2)
        self.assertEqual(self.status(task_id)["protection"], protection)

    def test_cli_report_reason_uses_300_character_boundary(self):
        task_id, book = self.authorize()
        reason = "查" * 300
        self.run_cmd("import", task_id, self.put_reply(book, self.report(book, reason=reason + "验")), code=2)
        self.assertFalse((self.task_dir(task_id) / "reply.json").exists())
        self.run_cmd("import", task_id, self.put_reply(book, self.report(book, reason=reason)))
        [report] = self.status(task_id)["report_only"]
        self.assertEqual(report["reason_sha256"], sha(reason.encode("utf-8")))

    def test_cli_reads_exact_64_kib_state_and_reply_but_refuses_one_byte_more(self):
        limit = 64 * 1024
        task_id, book = self.authorize()
        state = self.task_dir(task_id) / "state.json"
        original = state.read_bytes()
        exact = original.ljust(limit, b" ")
        self.assertEqual(len(exact), limit)
        state.write_bytes(exact)
        self.assertEqual(self.status(task_id)["state"], "exported")
        state.write_bytes(exact + b" ")
        self.run_cmd("status", task_id, code=2)
        self.assertEqual(state.read_bytes(), exact + b" ")
        state.write_bytes(original)

        reply = self.put_reply(book, self.report(book))
        exact = reply.read_bytes().ljust(limit, b" ")
        self.assertEqual(len(exact), limit)
        reply.write_bytes(exact + b" ")
        self.run_cmd("import", task_id, reply, code=2)
        self.assertFalse((self.task_dir(task_id) / "reply.json").exists())
        reply.write_bytes(exact)
        self.run_cmd("import", task_id, reply)
        self.assertEqual(self.status(task_id)["state"], "imported")

    def test_sensitive_report_reason_never_reaches_private_reply_or_delivery(self):
        self.write_candidate(document(preamble="\\author{SyntheticPrivateName}\n"))
        task_id, book = self.authorize()
        blocked = ("SyntheticPrivateName", "author@example.invalid", "/Users/Synthetic/private.log",
                   "sk-SyntheticKey1234567890", "Bearer SyntheticCredential123456")
        for value in blocked:
            with self.subTest(private=value):
                action = self.report(book, reason=f"Review {value}.")
                response = self.run_cmd("import", task_id, self.put_reply(book, action), code=2)
                self.assertNotIn(value.encode(), response.stdout + response.stderr)
                self.assertFalse((self.task_dir(task_id) / "reply.json").exists())
                self.assertFalse((self.out / "AI交付检查说明.md").exists())

    def test_redaction_in_structured_source_and_embedded_log(self):
        markers = (
            "SyntheticPersonXZQ", "SyntheticInstituteXZQ", "SyntheticTitleXZQ",
            "author-xzq@example.invalid", "/Users/SyntheticHomeXZQ/private/source.tex",
            r"C:\Users\SyntheticWinXZQ\private\source.tex", "sk-SyntheticTokenXZQ1234567890",
            "SyntheticCommentXZQ", "SyntheticEmbeddedLogXZQ", "SyntheticOutsideExcerptXZQ",
        )
        person, institution, title, mail, posix, windows, key, comment, embed, outside = markers
        preamble = (
            f"\\author{{{person}}}\n\\title{{{title}}}\n"
            f"\\newcommand{{\\affiliation}}{{{institution}}}\n"
            f"% {comment} {mail}\n"
        )
        body = f"{BODY_LINE} % {comment}\n" + FILLER + f"{outside}\n"
        diagnostic = (
            f"Misplaced alignment tab character &. {person} {institution} {title} "
            f"{mail} {posix} {windows} api_key={key} {embed}"
        )
        self.write_candidate(document(body, preamble=preamble), error=diagnostic,
                             anchor=f"{BODY_LINE} % {comment}",
                             log_prefix=f"{embed} {mail} {posix} {windows} {key}\n")
        task_id = self.export()
        raw, book = self.preview(task_id)
        # Check serialized bytes and decoded strings, including escaped Windows paths.
        decoded = json.dumps(book, ensure_ascii=False)
        for marker in (*markers, str(self.out), str(self.root)):
            with self.subTest(marker=marker):
                self.assertNotIn(marker.encode(), raw)
                self.assertNotIn(json.dumps(marker, ensure_ascii=False)[1:-1], decoded)
        for undisclosed in ("SyntheticHomeXZQ", "SyntheticWinXZQ", "api_key="):
            self.assertNotIn(undisclosed.encode(), raw)
        self.assertTrue(book["diagnostics"])

    def test_classifies_editable_manual_and_unknown_diagnostics(self):
        cases = (
            ("Misplaced alignment tab character &.", "editable"),
            ("LaTeX Error: File `SyntheticMissingPackage.sty' not found.", "manual"),
            ("Synthetic diagnostic deliberately outside the known taxonomy.", "unknown"),
        )
        for index, (error, expected) in enumerate(cases):
            with self.subTest(classification=expected):
                self.use_output(f"diagnostic-{index}")
                self.write_candidate(document(BODY_LINE + f"\nVariant {index}.\n" + FILLER),
                                     error=error)
                task_id = self.export()
                _, book = self.preview(task_id)
                diagnostic = book["diagnostics"][0]
                self.assertEqual(diagnostic["classification"], expected)
                self.assertEqual(diagnostic["tool"], "xelatex")
                self.assertIn("phase", diagnostic)
                self.assertIn("exit_code", diagnostic)
                self.assertEqual(diagnostic["source_line"], 3)
                self.assertEqual(diagnostic["log_line"], 2)
                if expected != "editable":
                    self.run_cmd("confirm", task_id, self.status(task_id)["taskbook_sha256"])
                    self.run_cmd("import", task_id,
                                 self.put_reply(book, self.edit(book)), code=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
