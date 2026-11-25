"""Model/storage boundaries that are easier to isolate below the CLI."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compilefix"
sys.path.insert(0, str(FIXTURES))
from cli_fixture import BODY_LINE, ENGINE, STEM, document  # noqa: E402

sys.path.insert(0, str(ENGINE))


class CompileFixModelTests(unittest.TestCase):
    def test_summary_length_boundary_after_selecting_only_safe_text(self):
        from slcn.compilefix import model

        candidate = document().encode()
        log = f"! Known synthetic diagnostic\nl.3 {BODY_LINE}\n".encode()
        self.assertEqual(model.MAX_SUMMARY, 160)
        for length in (159, 160, 161):
            with self.subTest(length=length), patch.object(
                model, "_FIXABLE", (("Known synthetic diagnostic", "s" * length, "&"),),
            ):
                diagnostics = model.diagnostics(candidate, log, STEM)
                [diagnostic] = model.taskbook("synthetic", candidate, diagnostics)["diagnostics"]
                self.assertEqual(diagnostic["summary"], "s" * min(160, length))

    def test_reason_and_action_count_limits_at_exact_boundaries(self):
        from slcn.compilefix import model
        from slcn.compilefix.files import RepairError, json_bytes

        candidate = document().encode()
        log = f"! Misplaced alignment tab character &.\nl.3 {BODY_LINE}\n".encode()
        book = model.taskbook("synthetic", candidate, model.diagnostics(candidate, log, STEM))
        action = {"action": "report_only", "evidence_id": book["evidence"][0]["evidence_id"]}
        for count, length, valid in ((0, 1, False), (1, 0, False), (1, 1, True),
                                     (6, 300, True), (7, 300, False), (6, 301, False)):
            with self.subTest(actions=count, reason_length=length):
                actions = [dict(action, reason="s" * length)] * count
                raw = json_bytes({"identity": book["identity"], "actions": actions})
                if valid:
                    self.assertEqual(model.parse_reply(raw, book, candidate), actions)
                else:
                    with self.assertRaises(RepairError):
                        model.parse_reply(raw, book, candidate)

    def test_log_with_unrelated_candidate_name_or_mismatched_line_cannot_license_edit(self):
        from slcn.compilefix import model

        candidate = document().encode()
        logs = (
            "foreign.tex:3: Misplaced alignment tab character &.\n",
            "! Misplaced alignment tab character &.\nl.3 A fabricated anchor\n",
            "! Misplaced alignment tab character &.\nl.999999999 Synthetic line\n",
        )
        for log in logs:
            with self.subTest(log=log):
                diagnostic = model.diagnostics(candidate, log.encode(), STEM)[0]
                self.assertNotEqual(diagnostic["classification"], "editable")
                self.assertIsNone(diagnostic["source_line"])

    def test_legacy_anchor_looks_ahead_at_most_eight_log_lines(self):
        from slcn.compilefix import model

        candidate = document().encode()
        for distance in (7, 8, 9):
            with self.subTest(distance=distance):
                log = ("! Misplaced alignment tab character &.\n"
                       + "Synthetic log context.\n" * (distance - 1)
                       + f"l.3 {BODY_LINE}\n").encode()
                [diagnostic] = model.diagnostics(candidate, log, STEM)
                self.assertEqual(diagnostic["log_line"], 1)
                self.assertEqual(diagnostic["source_line"], 3 if distance <= 8 else None)
                self.assertEqual(diagnostic["classification"], "editable" if distance <= 8 else "manual")

    def test_anchor_accepts_seven_digits_and_rejects_eight_in_both_log_forms(self):
        from slcn.compilefix import model

        candidate = document().encode()
        for form in ("legacy", "file-line"):
            for digit_count in (6, 7, 8):
                with self.subTest(form=form, digit_count=digit_count):
                    # Pad a real body line so the numeric range is valid for both widths.
                    anchor = f"{3:0{digit_count}d}"
                    log = (f"! Misplaced alignment tab character &.\nl.{anchor} {BODY_LINE}\n"
                           if form == "legacy" else
                           f"./{STEM}.tex:{anchor}: Misplaced alignment tab character &.\n")
                    [diagnostic] = model.diagnostics(candidate, log.encode(), STEM)
                    self.assertEqual(diagnostic["source_line"], 3 if digit_count <= 7 else None)
                    self.assertEqual(diagnostic["classification"], "editable" if digit_count <= 7 else "manual")

    def test_taskbook_excerpt_clips_to_eight_unescaped_structure_tokens(self):
        from slcn.compilefix import model

        tokens = ["&", "$", "_", "$", "&", "_", "$", "&", "_"]
        for count in (7, 8, 9):
            with self.subTest(count=count):
                line = "SyntheticPrivateWord " + " literal ".join(tokens[:count]) + r" \& \_ \$ % &$__"
                candidate = document(line + "\n").encode()
                log = f"./{STEM}.tex:3: Misplaced alignment tab character &.\n".encode()
                diagnostics = model.diagnostics(candidate, log, STEM)
                self.assertEqual(diagnostics[0]["classification"], "editable")
                [evidence] = model.taskbook("synthetic", candidate, diagnostics)["evidence"]
                self.assertEqual(evidence["excerpt"].split(), tokens[:min(count, 8)])

    def test_each_tool_preserves_only_the_first_three_diagnostics(self):
        from slcn.compilefix import model

        candidate = document().encode()
        for tool, template in (("xelatex", f"./{STEM}.tex:3: Misplaced alignment tab character &.\n"),
                               ("biber", "ERROR - Synthetic bibliography failure.\n")):
            for count in (2, 3, 4):
                with self.subTest(tool=tool, count=count):
                    diagnostics = model.diagnostics(candidate, (template * count).encode(), STEM,
                                                    f"{tool} 第 1 步失败（退出码 1）")
                    self.assertEqual([item["log_line"] for item in diagnostics],
                                     list(range(1, min(count, 3) + 1)))

    def test_storage_byte_limits_accept_equal_and_refuse_plus_one(self):
        from slcn.compilefix import compiler, files

        table = ((files, "TEXT_LIMIT", 4 * 1024**2), (files, "REPLY_LIMIT", 64 * 1024),
                 (files, "ARTIFACT_LIMIT", 64 * 1024**2), (compiler, "LOG_LIMIT", 2 * 1024**2))
        with tempfile.TemporaryDirectory(prefix="compilefix-limits-") as root:
            candidate = Path(root) / "local-synthetic-file"
            for module, name, limit in table:
                with self.subTest(limit=name):
                    self.assertEqual(getattr(module, name), limit)
                    with candidate.open("wb") as stream:
                        stream.truncate(limit)
                    self.assertEqual(len(files.read(candidate, limit)), limit)
                    with candidate.open("ab") as stream:
                        stream.write(b"x")
                    with self.assertRaises(files.RepairError):
                        files.read(candidate, limit)

    def test_edit_candidate_recovers_when_model_misreads_anchor_fields(self):
        """A real reply put old/new into before/after; unique old + source_line must still allow review."""
        from slcn.compilefix import model

        candidate = document("A & B\n").encode()
        log = f"./{STEM}.tex:3: Misplaced alignment tab character &.\n".encode()
        diagnostics = model.diagnostics(candidate, log, STEM)
        self.assertEqual(diagnostics[0]["classification"], "editable")
        book = model.taskbook("synthetic", candidate, diagnostics)
        action = {"action": "edit", "evidence_id": "e1", "old": "&",
                  "new": "\\&", "before": "&", "after": "\\&"}
        patched, diff = model.edit_candidate(candidate, book, [action])
        self.assertIn(b"A \\& B", patched)
        self.assertIn(b"-A & B", diff)

    def test_support_manifest_limits_count_and_total_bytes_separately(self):
        from slcn.compilefix import compiler, files

        self.assertEqual(compiler.SUPPORT_COUNT, 256)
        self.assertEqual(compiler.SUPPORT_LIMIT, 128 * 1024**2)
        with tempfile.TemporaryDirectory(prefix="compilefix-support-") as root:
            parent = Path(root)
            by_count = parent / "count"
            by_count.mkdir()
            for index in range(256):
                (by_count / f"generated-{index}.txt").touch()
            self.assertEqual(compiler.support_manifest(by_count, STEM), {})
            (by_count / "too-many.txt").touch()
            with self.assertRaises(files.RepairError):
                compiler.support_manifest(by_count, STEM)

            by_size = parent / "size"
            by_size.mkdir()
            for index in range(2):
                with (by_size / f"synthetic-{index}.png").open("wb") as stream:
                    stream.truncate(64 * 1024**2)
            self.assertEqual(len(compiler.support_manifest(by_size, STEM)), 2)
            (by_size / "synthetic-one-more-byte.jpg").write_bytes(b"x")
            with self.assertRaises(files.RepairError):
                compiler.support_manifest(by_size, STEM)

    def test_operator_actor_label_boundary(self):
        from slcn.compilefix import files, state

        self.assertEqual(state.local_actor("A" * 128), "A" * 128)
        for invalid in ("A" * 129, "", "   ", "one\ntwo"):
            with self.subTest(invalid=invalid), self.assertRaises(files.RepairError):
                state.local_actor(invalid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
