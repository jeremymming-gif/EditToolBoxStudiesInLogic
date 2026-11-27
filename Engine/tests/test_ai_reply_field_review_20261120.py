#!/usr/bin/env python3
"""AI 回件字段变更须审阅；匿名隔离、小范围引擎回归（2026-11-20）。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slcn import ai_workflow as workflow
from slcn import ai_workflow_cli as cli
from slcn import ai_workflow_state_v3 as review
from slcn import bibdb, bibparse


ABSENT = object()


def article(key="CITED", number="3", **extra):
    fields = {"author": "Example, A.", "title": "Anonymous Field Review " + key,
              "year": "2020", "journal": "Anonymous Journal"}
    if number is not ABSENT:
        fields["number"] = number
    fields.update(extra)
    return "@article{" + key + ",\n" + "".join(
        f"  {name} = {{{value}}},\n" for name, value in fields.items()) + "}\n"


def fields(text, key="CITED"):
    entries, problems = bibparse.parse_entries(text)
    if any(problem.section == "syntax" for problem in problems):
        raise AssertionError(problems)
    return {entry.key: entry.fields for entry in entries}[key]


def hit(values, *, tier="safe", weak=False, same_version=True):
    return {"CITED": {"tier": tier, "weak": weak, "same_version": same_version,
                       "title_similarity": 0.96 if weak else 1.0,
                       "fields": list(values.items())}}


def compare(old, reply, snapshot=None, **kwargs):
    return review.build_conflicts(old_bib=old, reply_bib=reply, candidate_bib=reply,
                                  snapshot=snapshot or {}, **kwargs)


def settle(candidate, rows, decisions=None, fills=None, *, old=None):
    checked = review.check_decisions(rows, decisions)
    return review.apply_decisions(candidate, rows, checked, fills, original_bib=old)[0]


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="slcn-anonymous-reply-review-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.notes = self.root / "notes"
        (self.notes / "cases").mkdir(parents=True)
        self.work = self.root / "work"
        self.work.mkdir()
        self.bib = self.work / "input.bib"
        self.tex = self.work / "input.tex"
        self.db_path = self.notes / "bib_db" / "bibliography.jsonl"
        env = patch.dict(os.environ, {
            "SLCN_MANUSCRIPTS": str(self.root / "absent-manuscripts"),
            "SLCN_NOTES": str(self.notes),
        })
        env.start()
        self.addCleanup(env.stop)
        # 本套件只有本地 Chat 模拟回件；意外网络调用直接失败。
        network = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        network.start()
        self.addCleanup(network.stop)


class FieldReview(IsolatedTest):
    def test_miss_defaults_to_original_and_reply_can_be_selected(self):
        old, reply = article(), article(number="7")
        rows, fills, _ = compare(old, reply, hit({}, tier="miss"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["field"], row["original"], row["reply"]), ("number", "3", "7"))
        self.assertEqual(row["choices"], ["original", "reply"])
        self.assertEqual((row["library"], row["default"]), ("", "original"))
        self.assertEqual(fills, {})
        self.assertEqual(fields(settle(reply, rows, old=old))["number"], "3")
        chosen = settle(reply, rows, {row["conflict_id"]: "reply"}, old=old)
        self.assertEqual(fields(chosen)["number"], "7")
        with self.assertRaisesRegex(workflow.WorkflowError, row["conflict_id"]):
            review.check_decisions(rows, {row["conflict_id"]: "library"})

    def test_missing_snapshot_still_reviews_changes(self):
        rows, _, _ = compare(article(), article(number="7"))
        self.assertEqual([row["field"] for row in rows], ["number"])
        self.assertEqual(rows[0]["choices"], ["original", "reply"])

    def test_added_field_defaults_to_absent_or_empty_original(self):
        for original_value in (ABSENT, ""):
            with self.subTest(original_value=repr(original_value)):
                old, reply = article(number=original_value), article(number="7")
                rows, fills, _ = compare(old, reply)
                self.assertEqual(len(rows), 1)
                default = fields(settle(reply, rows, fills=fills, old=old))
                if original_value is ABSENT:
                    self.assertNotIn("number", default)
                else:
                    self.assertEqual(default["number"], "")
                chosen = {rows[0]["conflict_id"]: "reply"}
                self.assertEqual(fields(settle(reply, rows, chosen, old=old))["number"], "7")

    def test_reply_omission_is_reviewed_before_same_version_auto_fill(self):
        old, reply = article(), article(number=ABSENT)
        rows, fills, _ = compare(old, reply, hit({"number": "9"}))
        self.assertEqual(len(rows), 1)
        self.assertEqual(fills, {})
        self.assertEqual(fields(settle(reply, rows, old=old))["number"], "3")
        chosen = {rows[0]["conflict_id"]: "reply"}
        self.assertNotIn("number", fields(settle(reply, rows, chosen, old=old)))

    def test_same_or_equivalent_values_do_not_conflict(self):
        for reply in (article(pages="1--9"), article(pages="1–9")):
            with self.subTest(reply=reply):
                rows, fills, _ = compare(article(pages="1--9"), reply)
                self.assertEqual((rows, fills), ([], {}))

    def test_candidate_that_did_not_adopt_reply_is_not_a_change(self):
        old = article()
        rows, fills, _ = review.build_conflicts(
            old_bib=old, reply_bib=article(number="7"), candidate_bib=old, snapshot={})
        self.assertEqual((rows, fills), ([], {}))
        rows, fills, _ = compare(old, article(number="7"), hit({"number": "9"}), review_keys=set())
        self.assertEqual((rows, fills), ([], {}))

    def test_library_record_without_changed_field_has_no_library_choice(self):
        rows, _, _ = compare(article(), article(number="7"),
                             hit({"title": "Anonymous Field Review CITED"}))
        self.assertEqual([row["field"] for row in rows], ["number"])
        self.assertEqual(rows[0]["choices"], ["original", "reply"])

    def test_reply_equal_to_library_still_needs_original_review(self):
        old, reply = article(), article(number="7")
        rows, fills, _ = compare(old, reply, hit({"number": "7"}))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["choices"], ["library", "reply", "original"])
        self.assertEqual(fields(settle(reply, rows, fills=fills, old=old))["number"], "3")
        for choice in ("reply", "library"):
            with self.subTest(choice=choice):
                self.assertEqual(fields(settle(reply, rows, {rows[0]["conflict_id"]: choice},
                                                old=old))["number"], "7")

    def test_same_version_missing_field_still_auto_fills(self):
        old = article(number=ABSENT)
        rows, fills, _ = compare(old, old, hit({"number": "9"}))
        self.assertEqual(rows, [])
        self.assertEqual(fills, {"CITED": {"number": "9"}})
        self.assertEqual(fields(settle(old, rows, fills=fills, old=old))["number"], "9")

    def test_weak_or_miss_identity_cannot_offer_library_values(self):
        for snapshot in (hit({"number": "9"}, weak=True), hit({"number": "9"}, tier="miss")):
            with self.subTest(snapshot=snapshot):
                old, reply = article(), article(number="7")
                rows, fills, _ = compare(old, reply, snapshot)
                self.assertEqual(rows[0]["choices"], ["original", "reply"])
                self.assertEqual(fills, {})
                self.assertEqual(fields(settle(reply, rows, old=old))["number"], "3")
                rows, fills, _ = compare(old, old, snapshot)
                self.assertEqual((rows, fills), ([], {}))

    def test_other_version_never_auto_fills_but_remains_reviewable(self):
        old = article(number=ABSENT)
        for tier in ("safe", "candidate"):
            with self.subTest(tier=tier):
                rows, fills, notes = compare(old, old, hit({"number": "9"}, tier=tier,
                                                           same_version=False))
                self.assertEqual(fills, {})
                self.assertEqual(rows[0]["choices"], ["library", "reply", "original"])
                self.assertTrue(any("版本不同" in note for note in notes))
                self.assertNotIn("number", fields(settle(old, rows, old=old)))

    def test_group_decisions_include_implicit_original(self):
        """部分裁决 + 另一行隐式 original → 接受，另一行按 default 落地。"""
        old, reply = article(pages="1--9"), article(number="7", pages="10--19")
        rows, _, _ = compare(old, reply, hit({"number": "9"}))
        self.assertEqual([row["field"] for row in rows], ["number", "pages"])
        decisions = {rows[0]["conflict_id"]: "reply"}
        self.assertEqual(review.check_decisions(rows, decisions), decisions)
        actual = fields(settle(reply, rows, decisions, old=old))
        self.assertEqual((actual["number"], actual["pages"]), ("7", "1--9"))

    def test_group_decisions_reject_missing_library(self):
        """逐行合法性不变：library 但本行无库值 → 仍按 conflict_id 定位拒绝。"""
        old, reply = article(pages="1--9"), article(number="7", pages="10--19")
        rows, _, _ = compare(old, reply, hit({"number": "9"}))
        with self.assertRaisesRegex(workflow.WorkflowError, rows[1]["conflict_id"]):
            review.check_decisions(rows, {row["conflict_id"]: "library" for row in rows})

    def test_bare_numeric_field_change_is_reviewed_and_restored(self):
        old = article().replace("number = {3}", "number = 3")
        reply = article(number="7").replace("number = {7}", "number = 7")
        rows, _, _ = compare(old, reply)
        self.assertEqual((rows[0]["original"], rows[0]["reply"]), ("3", "7"))
        self.assertEqual(fields(settle(reply, rows, old=old))["number"], "3")
        chosen = {rows[0]["conflict_id"]: "reply"}
        self.assertEqual(fields(settle(reply, rows, chosen, old=old))["number"], "7")

    def test_no_library_omission_restores_original_and_unrelated_fields(self):
        old = article(url="https://example.invalid/reviewed-entry")
        reply = article(number=ABSENT, url="https://example.invalid/reviewed-entry")
        rows, _, _ = compare(old, reply)
        self.assertEqual([row["field"] for row in rows], ["number"])
        actual = fields(settle(reply, rows, old=old))
        self.assertEqual(actual["number"], "3")
        self.assertEqual(actual["url"], "https://example.invalid/reviewed-entry")

    def test_explicit_row_choices_override_global_even_with_library_value(self):
        rows, _, _ = compare(article(), article(number="7"), hit({"number": "9"}))
        rows[0]["choices"] = ["original", "reply"]
        with self.assertRaisesRegex(workflow.WorkflowError, rows[0]["conflict_id"]):
            review.check_decisions(rows, {rows[0]["conflict_id"]: "library"})
        rows[0]["choices"] = ["original"]
        with self.assertRaisesRegex(workflow.WorkflowError, rows[0]["conflict_id"]):
            review.check_decisions(rows, {rows[0]["conflict_id"]: "reply"})

    def test_legacy_rows_without_choices_remain_compatible(self):
        rows, _, _ = compare(article(), article(number="7"), hit({"number": "9"}))
        del rows[0]["choices"]
        choice = {rows[0]["conflict_id"]: "library"}
        self.assertEqual(review.check_decisions(rows, choice), choice)

    def test_default_removal_does_not_change_other_entries_or_nested_text(self):
        unused = article("UNUSED", number="5", url="https://example.invalid/keep")
        old = article(number=ABSENT, note="Keep {number = {nested}}") + unused
        reply = article(number="7", note="Keep {number = {nested}}") + unused
        rows, _, _ = compare(old, reply)
        restored = settle(reply, rows, old=old)
        self.assertNotIn("number", fields(restored))
        self.assertIn("Keep {number = {nested}}", restored)
        self.assertTrue(restored.endswith(unused))


class OriginalExpressionReview(IsolatedTest):
    TOKENS = (
        ("month", "jan"),
        ("number", "3"),
        ("title", '"An {Anonymous} Title"'),
        ("title", "{An {Anonymous} Title}"),
        ("title", "prefix # {Suffix}"),
        ("title", '"Prefix, " # prefix # {, Suffix}'),
        ("title", '{Prefix, } # "Suffix"'),
        ("title", "prefix #\n    {Suffix, End}"),
        ("title", r'"An \"accent\" {Title}"'),
    )

    @staticmethod
    def raw_bib(expression, field="month"):
        values = {"author": "{Example, A.}", "title": "{Anonymous title}",
                  "year": "{2020}", "number": "3",
                  "note": "{Keep {nested, text}}", "url": '"https://example.invalid/keep"'}
        if expression is ABSENT:
            values.pop(field, None)
        else:
            values[field] = expression
        return ('@string{jan = {Anonymous January}}\n'
                '@string{prefix = {Anonymous Prefix }}\n@article{CITED,\n' +
                "".join(f"  {name} = {token},\n" for name, token in values.items()) + "}\n")

    def test_original_replacement_preserves_complete_tokens_and_unreviewed_bytes(self):
        unused = '@misc{UNUSED, title=prefix # {Suffix}, month=jan, url="keep"}\n'
        for field, token in (*self.TOKENS, ("month", "{}"), ("month", '""')):
            with self.subTest(field=field, token=token):
                old = self.raw_bib(token, field) + unused
                reply = old.replace(f"  {field} = {token},\n",
                                    f"  {field} = {{Replacement}},\n", 1)
                rows, fills, _ = compare(old, reply, review_keys={"CITED"})
                self.assertEqual([(r["key"], r["field"]) for r in rows], [("CITED", field)])
                self.assertEqual(settle(reply, rows, fills=fills, old=old), old)
                chosen = {rows[0]["conflict_id"]: "reply"}
                self.assertEqual(settle(reply, rows, chosen, old=old), reply)

    def test_original_reinsertion_preserves_complete_tokens(self):
        unused = '@misc{UNUSED, title={Keep this entry}, month=jan}\n'
        for field, token in self.TOKENS:
            with self.subTest(field=field, token=token):
                old = self.raw_bib(token, field) + unused
                reply = old.replace(f"  {field} = {token},\n", "", 1)
                rows, fills, _ = compare(old, reply, review_keys={"CITED"})
                self.assertEqual([r["field"] for r in rows], [field])
                actual = settle(reply, rows, fills=fills, old=old)
                self.assertIn(f"  {field} = {token},\n", actual)
                self.assertTrue(actual.endswith(unused))
                self.assertIn('  url = "https://example.invalid/keep",\n', actual)
                self.assertIn('  note = {Keep {nested, text}},\n', actual)
                chosen = {rows[0]["conflict_id"]: "reply"}
                self.assertEqual(settle(reply, rows, chosen, old=old), reply)

    def test_same_visible_macro_and_concat_literal_need_review(self):
        for field, token, literal in (
            ("month", "jan", "{jan}"),
            ("month", "{jan}", "jan"),
            ("title", "prefix # {Suffix}", "{prefix # {Suffix}}"),
            ("title", "{prefix # {Suffix}}", "prefix # {Suffix}"),
        ):
            with self.subTest(field=field):
                old, reply = self.raw_bib(token, field), self.raw_bib(literal, field)
                rows, fills, _ = compare(old, reply)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["original"], row["reply"])
                self.assertEqual(row["expressions"], {"original": token, "reply": literal})
                self.assertEqual((row["default"], row["choices"]),
                                 ("original", ["original", "reply"]))
                self.assertEqual(settle(reply, rows, fills=fills, old=old), old)
                self.assertEqual(settle(reply, rows, {row["conflict_id"]: "reply"}, old=old), reply)

    def test_expression_identity_rejects_stale_flat_value_decision(self):
        macro, literal = self.raw_bib("jan"), self.raw_bib("{jan}")
        first, _, _ = compare(macro, literal)
        second, _, _ = compare(literal, macro)
        self.assertNotEqual(first[0]["conflict_id"], second[0]["conflict_id"])
        self.assertEqual((first[0]["original"], first[0]["reply"]), ("jan", "jan"))
        self.assertEqual((second[0]["original"], second[0]["reply"]), ("jan", "jan"))
        with self.assertRaisesRegex(workflow.WorkflowError, first[0]["conflict_id"]):
            review.check_decisions(second, {first[0]["conflict_id"]: "reply"})

    def test_unadopted_or_out_of_scope_expression_does_not_conflict(self):
        old, reply = self.raw_bib("jan"), self.raw_bib("{jan}")
        for candidate, keys in ((old, {"CITED"}), (reply, set())):
            with self.subTest(keys=keys):
                rows, fills, _ = review.build_conflicts(
                    old_bib=old, reply_bib=reply, candidate_bib=candidate,
                    snapshot={}, review_keys=keys)
                self.assertEqual((rows, fills), ([], {}))
        # 候选是宏，回件是同可见字符的文字，也不算已采纳回件。
        rows, _, _ = review.build_conflicts(
            old_bib=self.raw_bib("{Other}"), reply_bib=reply, candidate_bib=old,
            snapshot={}, review_keys={"CITED"})
        self.assertEqual(rows, [])

    def test_equivalent_tokens_and_unchanged_input_do_not_conflict(self):
        for before, after in (('"Keep"', "{Keep}"), ("jan", "JAN"),
                              ("prefix # {Suffix}", "prefix#{Suffix}"), ("3", "{3}")):
            field = "number" if before == "3" else "title"
            with self.subTest(before=before, after=after):
                old, reply = self.raw_bib(before, field), self.raw_bib(after, field)
                self.assertEqual(compare(old, reply)[:2], ([], {}))
                self.assertEqual(compare(old, old)[:2], ([], {}))
                self.assertEqual(settle(old, [], old=old), old)
        # 拼接片段内部空白影响展开，不能作为 # 两侧的格式空白忽略。
        rows, _, _ = compare(self.raw_bib("prefix # { Suffix}", "title"),
                             self.raw_bib("prefix # {Suffix}", "title"))
        self.assertEqual([row["field"] for row in rows], ["title"])

    def test_reply_library_choices_and_legacy_value_fallback_stay_compatible(self):
        old, reply = self.raw_bib("jan"), self.raw_bib("{AI Month}")
        rows, fills, _ = compare(old, reply, hit({"month": "Library Month"}))
        row = rows[0]
        self.assertEqual(settle(reply, rows, {row["conflict_id"]: "reply"}, old=old), reply)
        actual = settle(reply, rows, {row["conflict_id"]: "library"}, old=old)
        self.assertIn("  month = {Library Month},\n", actual)
        self.assertEqual(settle(reply, rows, fills=fills, old=old), old)
        self.assertIn("  month = {jan},\n", settle(reply, rows))

    def test_original_restoration_ignores_ui_expression_metadata(self):
        old, reply = self.raw_bib("jan"), self.raw_bib("{jan}")
        rows, _, _ = compare(old, reply)
        rows[0]["expressions"]["original"] = "{Not the frozen source}"
        self.assertEqual(settle(reply, rows, old=old), old)

    def test_raw_postcheck_rejects_corrupted_insert_even_when_display_matches(self):
        old, reply = self.raw_bib("jan"), self.raw_bib(ABSENT)
        rows, _, _ = compare(old, reply)
        insert = review._insert_original_fields
        def corrupt(body, values):
            return insert(body, {name: "{" + raw + "}" for name, raw in values.items()})
        with patch.object(review, "_insert_original_fields", side_effect=corrupt):
            with self.assertRaisesRegex(workflow.WorkflowError, "原稿值表达式未原样恢复"):
                settle(reply, rows, old=old)
        with self.assertRaisesRegex(workflow.WorkflowError, "原稿值表达式未原样恢复"):
            review._check_original_tokens(self.raw_bib("{jan}"), {"CITED": {"month": "jan"}})

    def test_ambiguous_frozen_fields_fail_closed(self):
        old = self.raw_bib("jan").replace("  month = jan,", "  month = jan,\n  month = jan,")
        reply = self.raw_bib("{Changed}")
        with self.assertRaisesRegex(workflow.WorkflowError, "字段重复"):
            compare(old, reply)

    def test_raw_insertion_handles_missing_comma_and_trailing_comment(self):
        old = '@article{CITED, month=jan, title={Keep}}\n'
        reply = '@article{CITED, title={Keep} % keep comment\n}\n'
        rows, _, _ = compare(old, reply)
        actual = settle(reply, rows, old=old)
        self.assertIn('title={Keep}, % keep comment\n', actual)
        self.assertIn('month = jan,\n', actual)
        self.assertEqual(actual, '@article{CITED, title={Keep}, % keep comment\n month = jan,\n}\n')


class WorkflowFieldReview(IsolatedTest):
    def prepare(self, old=None, *, execute=None, safe=(), entry=workflow.ENTRY_CONVERT):
        self.old = old or article() + article("UNUSED", number="5")
        self.bib.write_text(self.old, encoding="utf-8")
        self.tex.write_text(r"See \cite{CITED}.", encoding="utf-8")
        execute = {workflow.CAT_BIB_VERIFY} if execute is None else execute
        return workflow.prepare(
            work_dir=str(self.work), entry=entry,
            bib_text=self.old, tex_text=self.tex.read_text(encoding="utf-8"),
            target_path=str(self.bib), tex_path=str(self.tex), taskbook_version=3,
            scope=workflow.Scope(exec=execute, adopt={workflow.CAT_BIB_VERIFY}),
            safe_hit_keys=safe,
            conversion={"input_identity": {}, "stage_dir": str(self.work),
                        "final_dir": str(self.root / "out"), "tex_name": "input.tex",
                        "bib_name": "reference.bib"} if entry == workflow.ENTRY_CONVERT else None)

    def import_reply(self, task, text=None):
        result = workflow.import_reply(work_dir=str(self.work), text=(
            f"% TASK-ID: {task.task_id}\n```bibtex\n" +
            (text if text is not None else article(number="7")) + "```\n"))
        self.assertTrue(result["ok"], result)
        return result

    def build_library(self, text=None):
        store = bibdb.Store.load(str(self.db_path), create=True)
        store.import_bib_text(text or article("OTHER", number="9"),
                              {"kind": bibdb.KIND_PUBLISHED, "issue": "anonymous",
                               "slug": "anonymous", "file": "anonymous.bib"}, verified=True)
        store.save()

    def preview(self, **kwargs):
        result = workflow.preview(work_dir=str(self.work), **kwargs)
        self.assertTrue(result.ok, result.blocked)
        return result

    def test_real_miss_default_then_reply_and_stale_preview_is_refused(self):
        self.build_library()
        db_before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        task = self.prepare(entry=workflow.ENTRY_BIB)
        self.assertEqual(task.state["context"]["sets"]["T"], ["CITED"])
        self.import_reply(task)
        first = self.preview()
        self.assertEqual(first.state["library"]["state"], "ok")
        self.assertEqual(first.state["library"]["matched"], 0)
        self.assertEqual(first.state["library"]["conflicts"], 1)
        self.assertEqual(fields(first.bib["text"])["number"], "3")
        row = first.state["conflicts"][0]
        with self.assertRaisesRegex(workflow.WorkflowError, row["conflict_id"]):
            self.preview(decisions={row["conflict_id"]: "library"})
        second = self.preview(decisions={row["conflict_id"]: "reply"})
        self.assertNotEqual(first.preview_id, second.preview_id)
        self.assertEqual(fields(second.bib["text"])["number"], "7")
        with self.assertRaisesRegex(workflow.WorkflowError, "预览已过期"):
            workflow.apply(work_dir=str(self.work), preview_id=first.preview_id)
        self.assertEqual(self.bib.read_text(encoding="utf-8"), self.old)
        workflow.apply(work_dir=str(self.work), preview_id=second.preview_id)
        actual = self.bib.read_text(encoding="utf-8")
        self.assertEqual(fields(actual)["number"], "7")
        self.assertTrue(actual.endswith(article("UNUSED", number="5")))
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), db_before)

    def test_missing_library_keeps_reason_and_reviews_ai_fields(self):
        task = self.prepare()
        self.import_reply(task)
        preview = self.preview()
        library = preview.state["library"]
        self.assertEqual((library["checked"], library["state"], library["conflicts"]),
                         (False, "missing", 1))
        self.assertIn("不存在", library["skipped_reason"])
        self.assertEqual(fields(preview.bib["text"])["number"], "3")
        self.assertEqual(preview.state["conflicts"][0]["choices"], ["original", "reply"])

    def test_unconfigured_unreadable_and_failed_queries_still_review_ai(self):
        self.build_library()
        task = self.prepare()
        self.import_reply(task)
        for state, target, failure in (
            ("unconfigured", "default_db_path", bibdb.BibDBError("anonymous configuration")),
            ("unreadable", "Store.load", bibdb.BibDBError("anonymous unreadable library")),
            ("failed", None, RuntimeError("anonymous query failure")),
        ):
            context = (patch.object(review, "library_query", side_effect=failure) if target is None
                       else patch("slcn.bibdb." + target, side_effect=failure))
            with self.subTest(state=state), context:
                preview = self.preview(requery=True)
                library = preview.state["library"]
                self.assertEqual((library["checked"], library["state"], library["conflicts"]),
                                 (False, state, 1))
                self.assertTrue(library["skipped_reason"])
                self.assertEqual(fields(preview.bib["text"])["number"], "3")

    def test_not_adopted_and_not_executed_never_query_or_review(self):
        task = self.prepare()
        self.import_reply(task)
        with patch.object(review, "library_query", side_effect=AssertionError("unexpected query")):
            preview = self.preview(adopt=set())
        self.assertEqual(preview.bib["text"], self.old)
        self.assertEqual(preview.state["conflicts"], [])
        self.assertFalse(preview.state["preview"]["write_bib"])
        task = self.prepare(execute={workflow.CAT_TYPESET})
        self.import_reply(task)
        with patch.object(review, "library_query", side_effect=AssertionError("unexpected query")):
            preview = self.preview(adopt={workflow.CAT_BIB_VERIFY})
        self.assertEqual(preview.bib["text"], self.old)
        self.assertEqual(preview.state["conflicts"], [])

    def test_unreferenced_or_frozen_entries_are_not_writable_differences(self):
        unused = article("UNUSED", number="5", url="https://example.invalid/keep")
        self.build_library(article("UNUSED", number="9"))
        task = self.prepare(old=article() + unused)
        self.import_reply(task, article(number="7") + article("UNUSED", number="8"))
        preview = self.preview()
        self.assertEqual([(r["key"], r["field"]) for r in preview.state["conflicts"]],
                         [("CITED", "number")])
        self.assertTrue(preview.bib["text"].endswith(unused))
        task = self.prepare(safe=("CITED",))
        self.import_reply(task)
        preview = self.preview()
        self.assertEqual(preview.state["conflicts"], [])
        self.assertEqual(preview.bib["text"], self.old)

    def test_missing_reply_entry_cannot_acquire_library_changes(self):
        self.build_library(article(number="9"))
        task = self.prepare()
        self.import_reply(task, article("UNUSED", number="8"))
        preview = self.preview()
        self.assertEqual(preview.state["active_reply"]["coverage"]["missing_t"], ["CITED"])
        self.assertEqual(preview.state["conflicts"], [])
        self.assertEqual(preview.bib["text"], self.old)

    def test_cli_cannot_choose_an_unavailable_library_field(self):
        task = self.prepare()
        self.import_reply(task)
        first = self.preview()
        cid = first.state["conflicts"][0]["conflict_id"]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--work-dir", str(self.work), "preview", "--adopt", "bib_verify",
                           "--decide", cid + "=library"])
        self.assertEqual(rc, 2)
        self.assertIn(cid, err.getvalue())
        self.assertIn("original / reply", err.getvalue())
        self.assertEqual(workflow.load_state(str(self.work))["preview"]["preview_id"], first.preview_id)
        with redirect_stdout(out := io.StringIO()), redirect_stderr(err := io.StringIO()):
            rc = cli.main(["--json", "--work-dir", str(self.work), "status"])
        self.assertEqual(rc, 0, err.getvalue())
        status = json.loads(out.getvalue())
        self.assertEqual(status["conflicts"][0]["choices"], ["original", "reply"])
        self.assertEqual(status["library"]["conflicts"], 1)
        self.assertFalse(status["library"]["checked"])

    def test_changed_cached_library_still_reviews_reply_without_library_values(self):
        self.build_library(article(number="9"))
        task = self.prepare()
        self.import_reply(task)
        first = self.preview()
        self.assertIn("library", first.state["conflicts"][0]["choices"])
        self.build_library(article("OTHER", number="6"))
        second = self.preview()
        self.assertEqual(second.state["library"]["state"], "failed")
        self.assertFalse(second.state["library"]["checked"])
        self.assertIn("--requery", second.state["library"]["skipped_reason"])
        self.assertEqual(second.state["library"]["conflicts"], 1)
        self.assertEqual(second.state["conflicts"][0]["choices"], ["original", "reply"])
        self.assertEqual(fields(second.bib["text"])["number"], "3")

    def test_older_r3_state_needs_no_new_persisted_fields(self):
        task = self.prepare()
        self.import_reply(task)
        state = workflow.load_state(str(self.work))
        for name in ("library", "conflicts", "decisions", "library_query", "preview_signature"):
            state.pop(name, None)
        workflow.save_state(str(self.work), state)
        preview = self.preview()
        self.assertEqual(fields(preview.bib["text"])["number"], "3")
        self.assertEqual(preview.state["conflicts"][0]["choices"], ["original", "reply"])


    def test_expression_only_workflow_defaults_source_and_invalidates_old_preview(self):
        raw = OriginalExpressionReview.raw_bib
        unused = '@misc{UNUSED, title={Keep}, month=jan}\n'
        task = self.prepare(old=raw("jan") + unused)
        source_bytes = self.bib.read_bytes()
        self.import_reply(task, raw("{jan}") + unused.replace("month=jan", "month={Changed}"))
        first = self.preview()
        rows = first.state["conflicts"]
        self.assertEqual([(r["key"], r["field"]) for r in rows], [("CITED", "month")])
        row = rows[0]
        self.assertEqual(row["expressions"], {"original": "jan", "reply": "{jan}"})
        self.assertIn("  month = jan,\n", first.bib["text"])
        self.assertTrue(first.bib["text"].endswith(unused))
        second = self.preview(decisions={row["conflict_id"]: "reply"})
        self.assertIn("  month = {jan},\n", second.bib["text"])
        self.assertNotEqual(first.preview_id, second.preview_id)
        with self.assertRaisesRegex(workflow.WorkflowError, "预览已过期"):
            workflow.apply(work_dir=str(self.work), preview_id=first.preview_id)
        self.assertEqual(self.bib.read_bytes(), source_bytes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
