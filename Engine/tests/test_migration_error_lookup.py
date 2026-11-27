"""Bound compiler diagnostics use migration evidence only for the primary error."""
import copy
from hashlib import sha256
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slcn.compilefix.l1_diagnostics import environment_hint


def digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def occurrence(kind, name, reason="local_support", **extra):
    return dict(kind=kind, name_sha256=digest(name), reason=reason, action="skipped",
                source_role="author_preamble", position=2, candidate_state="absent") | extra


class MigrationLookupTests(unittest.TestCase):
    source = "\\documentclass{article}\n\n\\begin{document}\nabc \\missingcontrol{x}\n\\end{document}\n"
    package_error = "! Package demopackage Error: synthetic marker.\n"
    command_error = "! Undefined control sequence.\nl.4 abc \\missingcontrol\n                       {x}\n"

    def hints(self, log, *items, evidence=None, candidate_sha=None, step=1):
        fingerprint = digest(self.source)
        record = {"candidate_sha256": fingerprint, "migration": list(items),
                  "migration_incomplete": False} | (evidence or {})
        capture = {"candidate_sha256": candidate_sha or fingerprint,
                   "failure": ("a" * 32, step, 1, log.encode("utf-8"))}
        before = copy.deepcopy((record, capture))
        hints = environment_hint(self.source.encode(), capture, planning_evidence=record)
        self.assertEqual(before, (record, capture))
        self.assertLessEqual(len(hints), 4)
        self.assertTrue(all(len(hint) <= 200 for hint in hints))
        return " ".join(hints)

    def test_package_error_uses_package_namespace_and_candidate_state(self):
        text = self.hints(self.package_error, occurrence("package", "demopackage"),
                          occurrence("command", "demopackage", reason="body_unused"))
        for marker in ("宏包报错", "作者前言", "本地支撑", "第 2 行", "候选未见"):
            self.assertIn(marker, text)
        for private in ("demopackage", "synthetic marker", "正文未用到"):
            self.assertNotIn(private, text)

    def test_environment_same_named_macro_cannot_mask_skipped_environment(self):
        text = self.hints("! LaTeX Error: Environment sample undefined.\n",
                          occurrence("environment", "sample", "template_exists"),
                          occurrence("command", "sample", "body_unused"),
                          evidence={"decisions": {"sample": {"reason": "body_unused"}}})
        self.assertIn("未定义环境", text)
        self.assertIn("模板已有", text)
        self.assertNotIn("正文未用到", text)

    def test_direct_control_word_uses_error_split_point(self):
        text = self.hints(self.command_error, occurrence("command", "missingcontrol", "body_unused"))
        self.assertIn("未定义命令", text)
        self.assertIn("正文未用到", text)
        self.assertNotIn("missingcontrol", text)

    def test_macro_expansion_identifies_expanded_word_instead_of_the_caller(self):
        log = "! Undefined control sequence.\n\\sample ->\\missinginner \n                {x}\nl.4 abc \\sample\n"
        text = self.hints(log, occurrence("command", "missinginner", "body_unused"),
                          occurrence("command", "sample", "imported"))
        self.assertIn("正文未用到", text)

    def test_unusable_expansion_does_not_attribute_later_call_site(self):
        log = "! Undefined control sequence.\n<argument> \\a  text\nl.4 abc \\missingcontrol\n"
        text = self.hints(log, occurrence("command", "missingcontrol", "body_unused"))
        self.assertIn("未定义命令", text)
        self.assertNotIn("正文未用到", text)

    def test_direct_control_word_with_unverified_candidate_line_stays_unknown(self):
        text = self.hints(self.command_error.replace("l.4", "l.3"),
                          occurrence("command", "missingcontrol", "body_unused"))
        self.assertIn("未定义命令", text)
        self.assertNotIn("正文未用到", text)

    def test_tikz_error_matches_exact_key_including_tex_hard_wrap(self):
        key = "synthetic extra long style with spaces"
        line = f"! Package pgfkeys Error: I do not know the key '/tikz/{key}' and I am going to ignore it."
        log = line[:79] + "\n" + line[79:] + "\n"
        text = self.hints(log, occurrence("tikz_key", key, "imported"),
                          occurrence("package", "pgfkeys", "template_exists"))
        self.assertIn("未知 TikZ 键", text)
        self.assertIn("迁入", text)
        self.assertNotIn("模板已有", text)
        self.assertNotIn("synthetic", text)

    def test_incomplete_or_ambiguous_records_do_not_assert_unique_decision(self):
        item = occurrence("package", "demopackage", "conflict")
        for items, flags in (([item, item], {}), ([item], {"migration_incomplete": True}),
                             ([item] * 4097, {})):
            with self.subTest(size=len(items), flags=flags):
                text = self.hints(self.package_error, *items, evidence=flags)
                self.assertIn("无法唯一定位", text)
                self.assertNotIn("宏包冲突", text)

    def test_same_named_support_definitions_show_bounded_source_fingerprints(self):
        first = occurrence("command", "missingcontrol", "body_unused",
                           coordinate="support_file_line", source_sha256="a" * 64, position=12)
        second = occurrence("command", "missingcontrol", "body_unused",
                            coordinate="support_file_line", source_sha256="b" * 64, position=27)
        text = self.hints(self.command_error, first, second)
        self.assertIn("无法唯一定位", text)
        self.assertIn("第 12 行来源 aaaaaaaaaaaa", text)
        self.assertIn("第 27 行来源 bbbbbbbbbbbb", text)
        self.assertNotIn("a" * 64, text)
        self.assertNotIn("b" * 64, text)

    def test_other_style_does_not_attribute_generic_tikz_or_package(self):
        log = "! Package pgfkeys Error: I do not know the key '/tikz/missing'.\n"
        text = self.hints(log, occurrence("tikz_key", "other", "conflict"),
                          occurrence("package", "pgfkeys", "conflict"))
        self.assertIn("未找到对应决定", text)

    def test_unrelated_first_error_cannot_borrow_following_package_or_environment(self):
        log = "! Missing $ inserted.\n" + self.package_error + "! LaTeX Error: Environment sample undefined.\n"
        text = self.hints(log, occurrence("package", "demopackage", "conflict"),
                          evidence={"decisions": {"sample": {"reason": "body_unused"}}})
        self.assertNotIn("宏包报错", text)
        self.assertNotIn("正文未用到", text)

    def test_binding_and_phase_are_required(self):
        item = occurrence("package", "demopackage", "conflict")
        for overrides in ({"step": 2}, {"candidate_sha": "0"*64},
                          {"evidence": {"candidate_sha256": "0"*64}}):
            self.assertNotIn("宏包冲突", self.hints(self.package_error, item, **overrides))

    def test_legacy_record_keeps_secondary_environment_observations(self):
        fingerprint = digest(self.source)
        log = self.package_error + "! LaTeX Error: Environment sample undefined.\n"
        cap = {"candidate_sha256": fingerprint, "failure": ("a" * 32, 1, 1, log.encode())}
        legacy = {"candidate_sha256": fingerprint, "decisions": {"sample": {"reason": "body_unused"}}}
        text = " ".join(environment_hint(self.source.encode(), cap, planning_evidence=legacy))
        self.assertIn("sample 未定义", text)
        self.assertIn("正文未用到", text)

    def test_malformed_primary_record_does_not_borrow_secondary_environment_decision(self):
        log = "! Package uppercase Error: Environment sample undefined.\n"
        text = self.hints(log, occurrence("environment", "sample", "body_unused"))
        self.assertIn("宏包报错", text)
        self.assertNotIn("正文未用到", text)

    def test_malformed_evidence_cannot_echo_text_or_drop_error_class(self):
        item = occurrence("package", "demopackage", reason={"raw": "private-token"},
                          source_role=["private-token"], position=True, candidate_state="private-token")
        text = self.hints(self.package_error, item)
        self.assertIn("宏包报错", text)
        self.assertNotIn("private-token", text)


if __name__ == "__main__":
    unittest.main()
