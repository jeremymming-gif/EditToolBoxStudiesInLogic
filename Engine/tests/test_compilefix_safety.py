"""Stage A fail-closed edit validation and builder limit boundaries."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "compilefix"))
from cli_fixture import BODY_LINE, FILLER, OLD, STEM, CompileFixFixture, document  # noqa: E402


class CompileFixSafetyTests(CompileFixFixture):
    def assert_rejected(self, task_id, book, *actions):
        before = self.tex.read_bytes()
        reply = self.put_reply(book, *actions)
        result = subprocess.run(self.command("import", task_id, reply), cwd=self.root,
                                env=self.env, capture_output=True, timeout=30, check=False)
        if result.returncode == 0:
            self.run_cmd("review", task_id, code=2)
        else:
            self.assertEqual(result.returncode, 2, result)
        self.assertEqual(self.tex.read_bytes(), before)
        self.assertFalse(self.pdf.exists())

    def assert_accepted(self, task_id, book, *actions):
        before = self.tex.read_bytes()
        self.run_cmd("import", task_id, self.put_reply(book, *actions))
        diff = self.review(task_id)
        self.assertIn(b"@@", diff)
        self.assertEqual(self.tex.read_bytes(), before)
        self.assertFalse(self.pdf.exists())

    def fresh_case(self, case_name):
        self.store = self.root / case_name
        self.env["SLCN_COMPILEFIX_STATE_DIR"] = str(self.store)

    def test_anchor_must_match_exactly_once_in_frozen_candidate(self):
        task_id, book = self.authorize()
        self.assert_rejected(task_id, book, self.edit(book, old="Not present"))
        self.fresh_case("duplicate")
        self.write_candidate(document(f"{BODY_LINE}\n{BODY_LINE}\n" + FILLER))
        task_id, book = self.authorize()
        self.assert_rejected(task_id, book, self.edit(book))
        self.fresh_case("unique")
        self.write_candidate()
        task_id, book = self.authorize()
        self.assert_accepted(task_id, book, self.edit(book))

    def test_overlapping_edits_are_rejected_as_a_batch(self):
        task_id, book = self.authorize()
        edits = [self.edit(book), self.edit(book, old="& B ends", new="and B ends",
                                          before="Synthetic anchor: A ", after=" here.")]
        self.assert_rejected(task_id, book, *edits)

    def test_foreign_source_path_and_preamble_or_postamble_edits_are_rejected(self):
        preamble = "Preamble anchor: Prelude.\n"
        postamble = "Postamble anchor: Afterword.\n"
        self.write_candidate(document(preamble=preamble, trailing=postamble))
        attempts = (
            ("preamble", dict(old="Prelude", new="Changed", before="Preamble anchor: ", after=".")),
            ("postamble", dict(old="Afterword", new="Changed", before="Postamble anchor: ", after=".")),
            ("body-delimiter", dict(old=r"\begin{document}", new="Changed", before="\n", after="\n")),
        )
        for key, fields in attempts:
            with self.subTest(attempt=key):
                self.fresh_case(key)
                task_id, book = self.authorize()
                self.assert_rejected(task_id, book, self.edit(book, **fields))
        self.fresh_case("foreign-path")
        task_id, book = self.authorize()
        self.assert_rejected(task_id, book, {**self.edit(book), "path": "../outside.tex"})
        self.fresh_case("outside-export")
        outside = self.root / "outside.tex"
        outside.write_text(document(), encoding="utf-8")
        self.run_cmd("export", self.out, "../outside", "--synthetic", code=2)
        self.assertEqual(outside.read_text(encoding="utf-8"), document())
        self.assertFalse((self.store / "tasks").exists() and list(self.store.glob("tasks/*/taskbook.json")))

    def test_author_source_directory_without_convert_provenance_is_rejected(self):
        self.use_output("synthetic-author-source")
        self.write_candidate()
        original = {p.name: p.read_bytes() for p in self.out.iterdir()}
        self.run_cmd("export", self.out, STEM, code=2)
        self.assertFalse(list(self.store.glob("tasks/*/taskbook.json")))
        self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()}, original)

    def test_environment_temp_override_does_not_qualify_synthetic_author_directory(self):
        fixture_root = Path(__file__).resolve().parent / "fixtures" / "compilefix"
        # The simulated source must be outside the trusted OS temp roots.
        with tempfile.TemporaryDirectory(prefix="author-fixture-", dir=fixture_root) as temporary:
            source_dir = Path(temporary).resolve()
            original = {f"{STEM}.tex": self.tex.read_bytes(), f"{STEM}.log": self.log.read_bytes()}
            for name, content in original.items():
                (source_dir / name).write_bytes(content)
            self.env.update({variable: str(source_dir) for variable in ("TMPDIR", "TEMP", "TMP")})
            self.run_cmd("export", source_dir, STEM, "--synthetic", code=2)
            self.assertEqual({p.name: p.read_bytes() for p in source_dir.iterdir()}, original)
            self.assertFalse(list(self.store.glob("tasks/*/taskbook.json")))
            # The same override must not block a candidate in the trusted temp area.
            task_id = self.export()
            self.assertEqual(self.status(task_id)["state"], "exported")

    def test_export_refuses_symlink_candidate_outside_the_output_directory(self):
        source = self.root / "outside.tex"
        original = self.tex.read_bytes()
        self.tex.replace(source)
        try:
            self.tex.symlink_to(source)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        self.run_cmd("export", self.out, STEM, "--synthetic", code=2)
        self.assertTrue(self.tex.is_symlink())
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(list(self.store.glob("tasks/*/taskbook.json")))

    def test_dangerous_or_unclassified_commands_are_refused_before_compiling(self):
        forbidden = (
            r"\write18{}", r"\openout1=unused.tmp", r"\read1 to\synthetic",
            r"\input{../unused.tex}", r"\include{unused}", r"\newcommand{\foo}{A}",
            r"\renewcommand{\foo}{A}", r"\def\foo{A}", r"\catcode`\A=12",
            r"\documentclass{article}", r"\usepackage{unused}", r"\loop\repeat",
            r"\begin{document}", r"\end{document}", r"\csname synthetic\endcsname",
            r"\SyntheticUnclassified", r"^^5cinput{unused.tex}",
        )
        for index, command in enumerate(forbidden):
            with self.subTest(command=command):
                self.fresh_case(f"unsupported-{index}")
                task_id, book = self.authorize()
                self.assert_rejected(task_id, book, self.edit(book, new=command))

    def test_three_edits_allowed_four_refused(self):
        errors = ("A & B", "C & D", "E & F")
        rows = [f"Row {index}: {old} ends here." for index, old in enumerate(errors, 1)]
        self.write_candidate(document("\n".join(rows) + "\n" + FILLER), anchor=rows[0])
        self.log.write_text("".join(f"! Misplaced alignment tab character &.\nl.{index} {row}\n"
                                    for index, row in enumerate(rows, 3)), encoding="utf-8")
        for count in (3, 4):
            with self.subTest(edits=count):
                self.fresh_case(f"edits-{count}")
                task_id, book = self.authorize()
                actions = [dict(self.edit(book, old=old, new=old.replace("&", r"\&"),
                                          before=f"Row {index + 1}: "),
                                evidence_id=book["evidence"][index]["evidence_id"])
                           for index, old in enumerate(errors)]
                if count == 3:
                    self.assert_accepted(task_id, book, *actions)
                else:
                    fourth = self.edit(book, old="Row 1", new="Row One", before="", after=": ")
                    self.assert_rejected(task_id, book, *actions, fourth)

    def test_character_limit_includes_old_and_new(self):
        for total in (512, 513):
            with self.subTest(changed_characters=total):
                self.fresh_case(f"characters-{total}")
                self.write_candidate(document(BODY_LINE + "\n" + FILLER * 8))
                task_id, book = self.authorize()
                action = self.edit(book, new="x" * (total - len(OLD)))
                check = self.assert_accepted if total == 512 else self.assert_rejected
                check(task_id, book, action)

    def test_ratio_limit_accepts_exactly_ten_percent(self):
        for total in (100, 101):
            with self.subTest(changed_characters=total):
                self.fresh_case(f"ratio-{total}")
                body = BODY_LINE + "\n"
                filler_size = 1000 - len(document(body + "\n"))
                self.write_candidate(document(body + "x" * filler_size + "\n"))
                self.assertEqual(len(self.tex.read_text(encoding="utf-8")), 1000)
                task_id, book = self.authorize()
                action = self.edit(book, new="x" * (total - len(OLD)))
                check = self.assert_accepted if total == 100 else self.assert_rejected
                check(task_id, book, action)

    def test_line_limit_accepts_four_and_rejects_five_in_each_direction(self):
        for direction in ("add", "delete"):
            for count in (4, 5):
                with self.subTest(direction=direction, lines=count):
                    self.fresh_case(f"lines-{direction}-{count}")
                    if direction == "add":
                        self.write_candidate()
                        fields = dict(new=r"A \& B" + "\nextra" * count)
                    else:
                        base = "\n".join([f"Generated row {index}." for index in range(1, count)]
                                         + [BODY_LINE])
                        self.write_candidate(document(f"Begin anchor.\n{base}\nEnd anchor.\n" + FILLER * 2))
                        fields = dict(old=base, new="", before="Begin anchor.\n", after="\nEnd anchor.")
                        # Include the newline removed with the old text, so count is exact.
                        fields["old"] += "\n"
                        fields["after"] = "End anchor."
                    task_id, book = self.authorize()
                    check = self.assert_accepted if count == 4 else self.assert_rejected
                    check(task_id, book, self.edit(book, **fields))

    def test_diagnostic_clipping_at_three_entries_and_160_characters(self):
        diagnostics = "".join(f"! Synthetic diagnostic {index}: " + "s" * 200 + f"\nl.3 {BODY_LINE}\n"
                              for index in range(4))
        self.log.write_text(diagnostics, encoding="utf-8")
        task_id = self.export()
        _, book = self.preview(task_id)
        self.assertEqual(len(book["diagnostics"]), 3)
        for diagnostic in book["diagnostics"]:
            self.assertLessEqual(len(diagnostic["summary"]), 160)
            self.assertNotIn("s" * 160, diagnostic["summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
