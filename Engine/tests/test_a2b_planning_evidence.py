#!/usr/bin/env python3.14
r"""A2B RED tests — planning evidence is real, per-round, display-only.

This file is the RED half of TDD for card A2B (see
``/Users/zhangwenming/Documents/GitHub/second-batch-cards-final-20261126.md`` §A2B
and ``/tmp/a2b_contract.md``). Every assertion against a NEW interface lives
inside a test method (or a helper only called from a test method), so the module
imports cleanly before GREEN and each new contract fails as an
assertion/attribute failure rather than a collection-time crash.

Coverage:
  A2B-1  decision object fields; digest is a hash, never a raw fragment
  A2B-2  scan_preamble observer sees raw pre-``seen`` found (with duplicates)
  A2B-3  local support fold counts; realpath dedup; subdirs with same basename
  A2B-4  decisions/reasons for skip/closure/override/downgrade/seed-missed
  A2B-5  input manifest: byte vs text digest, roles, read order, miss, no bib/img
  A2B-6  binding: disk bytes == expected before candidate_sha256 is stamped
  A2B-7  display: old path on missing/stale; "本轮规划时观察到" + REASON_LABELS;
          raw reason is NEVER echoed; capture/items/taskbook carry no evidence

Run:
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 -B tests/test_a2b_planning_evidence.py

Only writes to temp dirs. No manuscript repo, no network, no corpus, no git writes.
"""
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slcn import build as BUILD  # noqa: E402
from slcn import latex as LATEX  # noqa: E402
from slcn import preamble as PRE  # noqa: E402
from slcn.compilefix.files import digest  # noqa: E402
from slcn.compilefix.l1_diagnostics import environment_hint  # noqa: E402

ENGINE = Path(__file__).resolve().parents[1]

DECISION_FIELDS = {
    "name", "kind", "namespace", "source_role", "source_id", "position",
    "coordinate", "content_sha256", "pre_dedup_count", "scope_confidence",
    "action", "reason",
}
INPUT_FIELDS = {
    "role", "relative_id", "byte_sha256", "text_sha256", "read_order", "outcome",
}
OBSERVATION_FIELDS = {
    "decisions", "inputs", "fold_counts", "incomplete", "notes",
    "candidate_sha256", "binding_verified",
}
FOLD_KEYS = {
    "author_declarations_raw", "author_declarations_survivors",
    "support_declarations_raw", "support_macros_before", "support_macros_after",
    "local_items_before_merge", "local_items_after_merge",
    "support_files_read", "support_files_missed",
}
REASON_KEYS = {
    "imported", "template_exists", "body_unused", "kernel_exists", "overridden",
    "converted_renew", "closure_added", "seed_missed", "name_conflict",
    "source_unclear",
}
REASON_LABELS_EXPECTED = {
    "imported": "迁入",
    "template_exists": "模板已有同名，未迁",
    "body_unused": "正文未用到，未迁",
    "kernel_exists": "内核已有，未迁",
    "overridden": "重定义覆盖提示，未迁",
    "converted_renew": "降级为 newcommand 后迁入",
    "closure_added": "依赖闭包引入",
    "seed_missed": "种子未命中，闭包待定",
    "name_conflict": "同类同名按口径取先",
    "source_unclear": "来源归属不清",
}

AUTHOR_TEXT = (
    "\\documentclass{article}\n"
    "\\usepackage{amsmath}\n"
    "\\newtheorem{conjecture}{Conjecture A}\n"
    "\\newtheorem{conjecture}{Conjecture B}\n"
    "\\begin{document}\n"
    "\\begin{conjecture}x\\end{conjecture}\n"
    "\\end{document}\n"
)

ENV_LOG = b"! LaTeX Error: Environment theorem-a undefined.\nl.261 \\begin{theorem-a}\n"


def _sha(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _env_candidate(begin_line=261):
    lines = ["% filler"] * (begin_line - 1)
    lines += ["\\begin{theorem-a}", "body", "\\end{theorem-a}", "\\end{document}"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _cap(candidate, log=ENV_LOG, step=1, code=1):
    return {"candidate_sha256": digest(candidate),
            "failure": ("a" * 32, step, code, log)}


def _decisions_named(observation, name):
    return [d for d in observation.decisions if d.name == name]


class _SupportPlanMixin:
    """Run plan_migration with a synthetic local .sty and hand back the observation."""

    def _plan_with_support(self, sty_text, *, body_text=r"\A",
                           extra_skeleton="", extra_sty="", source_name="localsty.sty"):
        tmp = tempfile.TemporaryDirectory(prefix="a2b-plan-")
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        src.mkdir()
        (src / source_name).write_text(sty_text, encoding="utf-8")
        source = (
            "\\documentclass{article}\n"
            "\\usepackage{localsty}\n"
            "\\begin{document}\n" + body_text + "\n\\end{document}\n"
        )
        observation = PRE.PlanningObservation()
        PRE.plan_migration(
            source, extra_skeleton, extra_sty,
            body_text=body_text, source_dir=str(src), observation=observation,
        )
        return observation


# ══════════════════════════════════════════════════════════════════════════
# A2B-1  Decision object fields
# ══════════════════════════════════════════════════════════════════════════
class A2B1DecisionObjectTests(unittest.TestCase):
    def test_decision_dataclass_has_all_required_fields(self):
        fields = {f.name for f in dataclasses.fields(PRE.DeclarationDecision)}
        self.assertTrue(DECISION_FIELDS <= fields, sorted(DECISION_FIELDS - fields))

    def test_observation_dataclass_has_all_required_fields(self):
        fields = {f.name for f in dataclasses.fields(PRE.PlanningObservation)}
        self.assertTrue(OBSERVATION_FIELDS <= fields, sorted(OBSERVATION_FIELDS - fields))

    def test_observation_inputs_is_a_list_not_a_dict(self):
        # Contract erratum: inputs must be list[InputEvidence], default_factory=list.
        observation = PRE.PlanningObservation()
        self.assertIsInstance(observation.inputs, list)

    def test_author_decision_records_kind_namespace_role_position_and_coordinate(self):
        observation = PRE.PlanningObservation()
        PRE.plan_migration(AUTHOR_TEXT, "", "", observation=observation)
        matches = _decisions_named(observation, "conjecture")
        self.assertEqual(len(matches), 1)
        decision = matches[0]
        self.assertEqual(decision.kind, "newtheorem")
        self.assertEqual(decision.namespace, "env")
        self.assertEqual(decision.source_role, "author_preamble")
        self.assertEqual(decision.coordinate, "author_preamble")
        self.assertIsInstance(decision.position, int)
        self.assertGreaterEqual(decision.position, 0)
        self.assertIn(decision.scope_confidence, {"high", "medium", "unknown"})
        self.assertEqual(decision.action, "imported")

    def test_digest_is_a_hash_and_never_carries_raw_fragment(self):
        observation = PRE.PlanningObservation()
        PRE.plan_migration(AUTHOR_TEXT, "", "", observation=observation)
        self.assertTrue(observation.decisions)
        for decision in observation.decisions:
            self.assertEqual(len(decision.content_sha256), 64)
            self.assertNotIn("Conjecture", decision.content_sha256)
        display_blob = json.dumps(observation.to_display(), ensure_ascii=False)
        self.assertNotIn("Conjecture A", display_blob)
        self.assertNotIn("Conjecture B", display_blob)

    def test_to_display_shape_has_no_absolute_paths_or_free_text(self):
        observation = PRE.PlanningObservation()
        PRE.plan_migration(AUTHOR_TEXT, "", "", observation=observation)
        display = observation.to_display()
        for key in ("candidate_sha256", "decisions", "counts", "incomplete"):
            self.assertIn(key, display)
        blob = json.dumps(display, ensure_ascii=False)
        self.assertNotIn("/Users", blob)
        self.assertNotIn("/private", blob)


# ══════════════════════════════════════════════════════════════════════════
# A2B-2  preamble observer before seen-dedup
# ══════════════════════════════════════════════════════════════════════════
class A2B2PreambleObserverTests(unittest.TestCase):
    def test_observer_receives_raw_found_including_duplicates(self):
        calls = []
        PRE.scan_preamble(AUTHOR_TEXT, observer=lambda found: calls.append(found))
        self.assertEqual(len(calls), 1)
        raw = calls[0]
        names = [item.name for _pos, item in raw]
        self.assertEqual(names.count("conjecture"), 2)
        # A single (kind,name) still survives the normal deduplicated result.
        self.assertEqual(len(PRE.scan_preamble(AUTHOR_TEXT)), 1)

    def test_observer_is_invoked_once_per_scan_call(self):
        # The contract says "one call per scan"; internal calls of scan_preamble
        # (mask_definition_containers, defined_names_outside_containers, ...) must
        # NOT report through an observer passed to plan_migration.
        calls = []
        PRE.scan_preamble(AUTHOR_TEXT, observer=lambda found: calls.append(found))
        PRE.scan_preamble(AUTHOR_TEXT, observer=lambda found: calls.append(found))
        self.assertEqual(len(calls), 2)

    def test_observer_raw_list_is_pos_sorted_and_first_wins(self):
        calls = []
        PRE.scan_preamble(AUTHOR_TEXT, observer=lambda found: calls.append(found))
        raw = calls[0]
        positions = [pos for pos, _item in raw]
        self.assertEqual(positions, sorted(positions))
        first_source = raw[0][1].source
        self.assertIn("Conjecture A", first_source)
        self.assertNotIn("Conjecture B", first_source)

    def test_plan_migration_counts_raw_not_multiplied_by_internal_scans(self):
        observation = PRE.PlanningObservation()
        PRE.plan_migration(AUTHOR_TEXT, "", "", observation=observation)
        self.assertEqual(observation.fold_counts["author_declarations_raw"], 2)
        self.assertEqual(observation.fold_counts["author_declarations_survivors"], 1)

    def test_without_observer_scan_preamble_behaviour_unchanged(self):
        # Backward compatibility: no observer argument means the old result.
        self.assertEqual(len(PRE.scan_preamble(AUTHOR_TEXT)), 1)
        self.assertEqual(PRE.scan_preamble(AUTHOR_TEXT)[0].name, "conjecture")


# ══════════════════════════════════════════════════════════════════════════
# A2B-3  Local support folds / dedup
# ══════════════════════════════════════════════════════════════════════════
class A2B3LocalSupportTests(_SupportPlanMixin, unittest.TestCase):
    def test_fold_counts_has_all_nine_required_keys(self):
        observation = self._plan_with_support(
            "\\newtheorem{myenv}{My Env}\n\\newcommand{\\zhelper}{Z}\n",
            body_text=r"\begin{myenv}\zhelper\end{myenv}",
        )
        self.assertTrue(FOLD_KEYS <= set(observation.fold_counts),
                        sorted(FOLD_KEYS - set(observation.fold_counts)))

    def test_support_macro_fold_keeps_last_and_counts_raw_vs_survivor(self):
        observation = self._plan_with_support(
            "\\newcommand{\\dupmacro}{A}\n\\renewcommand{\\dupmacro}{B}\n",
            body_text=r"\dupmacro",
        )
        fold = observation.fold_counts
        self.assertGreater(fold["support_macros_before"], fold["support_macros_after"])
        self.assertEqual(fold["support_files_read"], 1)
        self.assertEqual(fold["support_files_missed"], 0)

    def test_missing_support_file_is_recorded_as_miss(self):
        tmp = tempfile.TemporaryDirectory(prefix="a2b-miss-")
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        src.mkdir()
        source = (
            "\\documentclass{article}\n\\usepackage{ghoststy}\n"
            "\\begin{document}plain\\end{document}\n"
        )
        observation = PRE.PlanningObservation()
        PRE.plan_migration(source, "", "", body_text="plain",
                           source_dir=str(src), observation=observation)
        self.assertGreaterEqual(observation.fold_counts["support_files_missed"], 1)

    def test_reread_same_path_with_changed_content_marks_incomplete_once(self):
        tmp = tempfile.TemporaryDirectory(prefix="a2b-reread-")
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        src.mkdir()
        sty = src / "localsty.sty"
        sty.write_text("\\newcommand{\\shared}{FIRST}\n", encoding="utf-8")
        source = (
            "\\documentclass{article}\n\\usepackage{localsty}\n"
            "\\begin{document}\\shared\\end{document}\n"
        )
        observation = PRE.PlanningObservation()
        PRE.plan_migration(source, "", "", body_text=r"\shared",
                           source_dir=str(src), observation=observation)
        first_count = len(_decisions_named(observation, "shared"))
        sty.write_text("\\newcommand{\\shared}{SECOND}\n", encoding="utf-8")
        PRE.plan_migration(source, "", "", body_text=r"\shared",
                           source_dir=str(src), observation=observation)
        self.assertTrue(observation.incomplete)
        self.assertEqual(len(_decisions_named(observation, "shared")), first_count)

    def test_same_basename_in_different_subdirs_is_not_merged(self):
        tmp = tempfile.TemporaryDirectory(prefix="a2b-subdir-")
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        (src / "a").mkdir(parents=True)
        (src / "b").mkdir(parents=True)
        (src / "a" / "sub.sty").write_text("\\newcommand{\\shared}{AA}\n", encoding="utf-8")
        (src / "b" / "sub.sty").write_text("\\newcommand{\\shared}{BB}\n", encoding="utf-8")
        source = (
            "\\documentclass{article}\n"
            "\\input{a/sub.sty}\n\\input{b/sub.sty}\n"
            "\\begin{document}\\shared\\end{document}\n"
        )
        observation = PRE.PlanningObservation()
        PRE.plan_migration(source, "", "", body_text=r"\shared",
                           source_dir=str(src), observation=observation)
        shared = _decisions_named(observation, "shared")
        self.assertEqual(len(shared), 2)
        source_ids = {d.source_id.replace(os.sep, "/") for d in shared}
        self.assertTrue(any("a/" in sid for sid in source_ids), source_ids)
        self.assertTrue(any("b/" in sid for sid in source_ids), source_ids)


# ══════════════════════════════════════════════════════════════════════════
# A2B-4  Decisions and SAFE reasons
# ══════════════════════════════════════════════════════════════════════════
class A2B4DecisionReasonTests(_SupportPlanMixin, unittest.TestCase):
    def test_imported_seed_and_closure_added_dependency(self):
        observation = self._plan_with_support(
            "\\newcommand{\\A}{\\B}\n\\newcommand{\\B}{B-body}\n",
            body_text=r"\A",
        )
        imported = _decisions_named(observation, "A")
        closure = _decisions_named(observation, "B")
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0].action, "imported")
        self.assertEqual(len(closure), 1)
        self.assertEqual(closure[0].action, "closure_added")
        self.assertEqual(closure[0].reason, "closure_added")

    def test_seed_missed_is_not_recorded_as_final_skip(self):
        # A is not a body seed; B is. The closure must still pick A up.
        observation = self._plan_with_support(
            "\\newcommand{\\B}{\\A}\n\\newcommand{\\A}{A-body}\n",
            body_text=r"\B",
        )
        picked = _decisions_named(observation, "A")
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].action, "closure_added")
        self.assertNotEqual(picked[0].action, "skipped")

    def test_template_existing_name_is_skipped_with_safe_reason(self):
        observation = self._plan_with_support(
            "\\newcommand{\\foo}{Author}\n",
            body_text=r"\foo",
            extra_sty="\\newcommand{\\foo}{Template}\n",
        )
        decisions = _decisions_named(observation, "foo")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "skipped")
        self.assertEqual(decisions[0].reason, "template_exists")

    def test_body_unused_definition_is_recorded_not_imported(self):
        observation = self._plan_with_support(
            "\\newcommand{\\unused}{U}\n",
            body_text="plain body without the macro",
        )
        decisions = _decisions_named(observation, "unused")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].reason, "body_unused")
        self.assertNotEqual(decisions[0].action, "imported")

    def test_kernel_existing_name_is_skipped_with_kernel_reason(self):
        observation = self._plan_with_support(
            "\\newcommand{\\section}{X}\n",
            body_text=r"\section{A}",
        )
        decisions = _decisions_named(observation, "section")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].reason, "kernel_exists")

    def test_renewcommand_override_records_overridden_action(self):
        observation = self._plan_with_support(
            "\\renewcommand{\\foo}{Author}\n",
            body_text=r"\foo",
            extra_sty="\\newcommand{\\foo}{Template}\n",
        )
        decisions = _decisions_named(observation, "foo")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "overridden")
        self.assertEqual(decisions[0].reason, "overridden")

    def test_renewcommand_downgrade_records_converted_renew(self):
        observation = self._plan_with_support(
            "\\renewcommand{\\foo}{X}\n",
            body_text=r"\foo",
        )
        decisions = _decisions_named(observation, "foo")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "converted_renew")
        self.assertEqual(decisions[0].reason, "converted_renew")

    def test_all_reasons_are_short_safe_codes(self):
        observation = self._plan_with_support(
            "\\newcommand{\\foo}{Author secret-sauce}\n"
            "\\newcommand{\\unused}{U}\n",
            body_text=r"\foo",
        )
        self.assertTrue(observation.decisions)
        for decision in observation.decisions:
            self.assertIn(decision.reason, REASON_KEYS)
            self.assertLessEqual(len(decision.reason), 30)
            self.assertNotIn(" ", decision.reason)
            self.assertNotIn("secret-sauce", decision.reason)

    def test_sorted_skip_log_does_not_drop_decisions(self):
        observation = self._plan_with_support(
            "\\newcommand{\\zfoo}{Z}\n\\newcommand{\\afoo}{A}\n",
            body_text="no macro used",
        )
        names = {d.name for d in observation.decisions}
        self.assertIn("zfoo", names)
        self.assertIn("afoo", names)


# ══════════════════════════════════════════════════════════════════════════
# A2B-5  Input manifest
# ══════════════════════════════════════════════════════════════════════════
class A2B5InputManifestTests(_SupportPlanMixin, unittest.TestCase):
    def test_input_evidence_dataclass_has_all_required_fields(self):
        fields = {f.name for f in dataclasses.fields(PRE.InputEvidence)}
        self.assertTrue(INPUT_FIELDS <= fields, sorted(INPUT_FIELDS - fields))

    def test_expand_inputs_observer_separates_byte_digest_from_text_digest(self):
        tmp = tempfile.TemporaryDirectory(prefix="a2b-input-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "root"
        (root / "sub").mkdir(parents=True)
        child = root / "sub" / "child.tex"
        child.write_bytes(b"child\r\nline\r\n")  # CRLF on disk, normalized in text
        main = root / "main.tex"
        main.write_bytes(
            b"\\documentclass{article}\n\\begin{document}\n"
            b"\\input{sub/child}\n\\end{document}\n"
        )
        calls = []
        LATEX.expand_inputs(
            str(main),
            observer=lambda rid, byte_sha, text_sha: calls.append((rid, byte_sha, text_sha)),
            root=str(root),
        )
        child_calls = [c for c in calls if "child" in c[0]]
        self.assertEqual(len(child_calls), 1)
        rid, byte_sha, text_sha = child_calls[0]
        self.assertEqual(rid.replace(os.sep, "/"), "sub/child.tex")
        self.assertNotIn(str(root), rid)
        self.assertEqual(byte_sha, _sha(child.read_bytes()))
        # open(..., encoding="utf-8", errors="replace") with universal newlines.
        self.assertEqual(text_sha, _sha("child\nline\n"))
        self.assertNotEqual(byte_sha, text_sha)

    def test_expand_inputs_relative_id_preserves_subdirectory_no_absolute_path(self):
        tmp = tempfile.TemporaryDirectory(prefix="a2b-relid-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "root"
        (root / "deep" / "deeper").mkdir(parents=True)
        (root / "deep" / "deeper" / "part.tex").write_text("part\n", encoding="utf-8")
        main = root / "main.tex"
        main.write_text("\\input{deep/deeper/part}\n", encoding="utf-8")
        calls = []
        LATEX.expand_inputs(
            str(main), observer=lambda rid, *_rest: calls.append(rid), root=str(root)
        )
        self.assertTrue(calls)
        for rid in calls:
            self.assertFalse(os.path.isabs(rid), rid)
        self.assertIn("deep/deeper/part.tex", [rid.replace(os.sep, "/") for rid in calls])

    def test_inputs_register_roles_in_read_order(self):
        tmp = tempfile.TemporaryDirectory(prefix="a2b-roles-")
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        src.mkdir()
        (src / "localsty.sty").write_text("\\newcommand{\\foo}{X}\n", encoding="utf-8")
        source = (
            "\\documentclass{article}\n\\usepackage{localsty}\n"
            "\\begin{document}\\foo\\end{document}\n"
        )
        observation = PRE.PlanningObservation()
        skeleton = "\\documentclass{article}\n\\begin{document}x\\end{document}\n"
        PRE.plan_migration(
            source, skeleton, "\\newcommand{\\foo}{X}\n",
            body_text=r"\foo", source_dir=str(src), observation=observation,
        )
        self.assertIsInstance(observation.inputs, list)
        self.assertTrue(observation.inputs)
        allowed_roles = {
            "body", "metadata", "skeleton_raw", "skeleton_synced", "style",
            "support", "expanded_input", "build_option", "normalization_param",
        }
        for entry in observation.inputs:
            self.assertIn(entry.role, allowed_roles)
            self.assertFalse(os.path.isabs(entry.relative_id), entry.relative_id)
        orders = [entry.read_order for entry in observation.inputs]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(len(orders), len(set(orders)))

    def test_repeated_read_with_different_content_marks_evidence_incomplete(self):
        observation = PRE.PlanningObservation()
        PRE.plan_migration(AUTHOR_TEXT, "", "", observation=observation)
        PRE.plan_migration(AUTHOR_TEXT + "% changed\n", "", "",
                           observation=observation)
        self.assertTrue(observation.incomplete)

    def test_bib_and_images_never_enter_the_manifest_or_display(self):
        observation = PRE.PlanningObservation()
        PRE.plan_migration(AUTHOR_TEXT, "", "", observation=observation)
        for entry in observation.inputs:
            self.assertNotIn(".bib", entry.relative_id)
            self.assertNotIn(".png", entry.relative_id)
        blob = json.dumps(observation.to_display(), ensure_ascii=False)
        for forbidden in (".bib", ".png", ".jpg", "reference.bib"):
            self.assertNotIn(forbidden, blob)


# ══════════════════════════════════════════════════════════════════════════
# A2B-6  Binding / lifecycle
# ══════════════════════════════════════════════════════════════════════════
class A2B6BindingTests(unittest.TestCase):
    def _source(self, directory):
        path = Path(directory) / "source.tex"
        path.write_text(
            "\\documentclass{article}\n\\usepackage{amsmath}\n"
            "\\begin{document}Hello\\end{document}\n",
            encoding="utf-8",
        )
        return path

    def test_build_result_has_planning_evidence_field_defaulting_to_none(self):
        fields = {f.name for f in dataclasses.fields(BUILD.BuildResult)}
        self.assertIn("planning_evidence", fields)
        # A freshly constructed result keeps the default (Word/docx path).
        result = BUILD.BuildResult(tex_path="x", bib_path=None, sty_path=None)
        self.assertIsNone(result.planning_evidence)

    def test_build_stamps_candidate_only_after_disk_bytes_match(self):
        with tempfile.TemporaryDirectory(prefix="a2b-bind-") as tmp:
            root = Path(tmp)
            source = self._source(root)
            out = root / "out"
            out.mkdir()
            result = BUILD.build_latex(str(source), str(out), basename="demo", verbose=False)
            evidence = result.planning_evidence
            self.assertIsInstance(evidence, dict)
            disk_bytes = Path(result.tex_path).read_bytes()
            self.assertEqual(evidence["candidate_sha256"], _sha(disk_bytes))
            self.assertEqual(evidence["candidate_sha256"], digest(disk_bytes))

    def test_disk_mismatch_leaves_candidate_none_and_incomplete(self):
        from slcn.fsutil import atomic_write as original_atomic

        def corrupting_write(path, text, **kwargs):
            if str(path).endswith(".tex"):
                text = text + "\n% tampered after the build wrote it\n"
            return original_atomic(path, text, **kwargs)

        with tempfile.TemporaryDirectory(prefix="a2b-mismatch-") as tmp:
            root = Path(tmp)
            source = self._source(root)
            out = root / "out"
            out.mkdir()
            with patch("slcn.fsutil.atomic_write", side_effect=corrupting_write):
                result = BUILD.build_latex(str(source), str(out),
                                           basename="demo", verbose=False)
            evidence = result.planning_evidence
            self.assertIsInstance(evidence, dict)
            self.assertIsNone(evidence["candidate_sha256"])
            self.assertTrue(evidence["incomplete"])

    def test_two_rounds_keep_independent_evidence(self):
        with tempfile.TemporaryDirectory(prefix="a2b-two-") as tmp:
            root = Path(tmp)
            source = self._source(root)
            results = []
            for name in ("one", "two"):
                out = root / ("out_" + name)
                out.mkdir()
                results.append(BUILD.build_latex(str(source), str(out),
                                                 basename="demo", verbose=False))
            first, second = results
            self.assertIsNot(first.planning_evidence, second.planning_evidence)
            self.assertEqual(first.planning_evidence["candidate_sha256"],
                             _sha(Path(first.tex_path).read_bytes()))
            self.assertEqual(second.planning_evidence["candidate_sha256"],
                             _sha(Path(second.tex_path).read_bytes()))


# ══════════════════════════════════════════════════════════════════════════
# A2B-7  Display degradation / safe labels
# ══════════════════════════════════════════════════════════════════════════
class A2B7DisplayTests(unittest.TestCase):
    def test_reason_labels_fixed_mapping_exists(self):
        from slcn.compilefix.l1_diagnostics import REASON_LABELS
        for code, label in REASON_LABELS_EXPECTED.items():
            self.assertEqual(REASON_LABELS.get(code), label, code)

    def test_missing_evidence_uses_old_path(self):
        candidate = _env_candidate()
        hints = environment_hint(candidate, _cap(candidate), planning_evidence=None)
        self.assertTrue(any("无本轮规划记录" in hint for hint in hints), hints)

    def test_stale_or_non_dict_evidence_shows_unknown_or_expired(self):
        candidate = _env_candidate()
        stale = {"candidate_sha256": "0" * 64, "decisions": {}}
        hints = environment_hint(candidate, _cap(candidate), planning_evidence=stale)
        self.assertTrue(any("未知或已过期" in hint for hint in hints), hints)
        hints_tuple = environment_hint(candidate, _cap(candidate),
                                       planning_evidence=("not-a-dict",))
        self.assertTrue(any(("未知或已过期" in h or "无本轮规划记录" in h)
                            for h in hints_tuple), hints_tuple)

    def test_bound_evidence_shows_planned_observations_with_mapped_reasons(self):
        candidate = _env_candidate()
        bait = "must-not-be-echoed-A2B-7"
        evidence = {
            "candidate_sha256": digest(candidate),
            "decisions": {"theorem-a": {
                "action": "imported",
                "reason": bait,
                "source_role": "author_preamble",
            }},
            "counts": {"declarations_seen": 2, "fold_counts": {}},
            "incomplete": False,
        }
        hints = environment_hint(candidate, _cap(candidate), planning_evidence=evidence)
        joined = " ".join(hints)
        self.assertIn("本轮规划时观察到", joined)
        self.assertIn("theorem-a", joined)
        self.assertIn("迁入", joined)
        self.assertIn("原因未归类", joined)
        self.assertNotIn(bait, joined)

    def test_incomplete_and_unknown_source_are_surfaced(self):
        candidate = _env_candidate()
        evidence = {
            "candidate_sha256": digest(candidate),
            "decisions": {"theorem-a": {
                "action": "imported", "reason": "imported", "source_role": "unknown",
            }},
            "counts": {"declarations_seen": 1, "fold_counts": {}},
            "incomplete": True,
        }
        hints = environment_hint(candidate, _cap(candidate), planning_evidence=evidence)
        joined = " ".join(hints)
        self.assertIn("来源不清", joined)
        self.assertIn("证据不完整", joined)

    def test_evidence_never_leaks_into_capture_or_diagnose(self):
        from slcn.compilefix.l1_diagnostics import diagnose

        candidate = _env_candidate()
        bait = "EVIDENCE_BAIT_A2B_capture"
        evidence = {
            "candidate_sha256": digest(candidate),
            "decisions": {"theorem-a": {"action": "imported", "reason": bait}},
        }
        cap = _cap(candidate)
        before = dict(cap)
        environment_hint(candidate, cap, planning_evidence=evidence)
        self.assertEqual(cap, before)
        report = diagnose(candidate, cap)
        blob = (json.dumps(report.shareable(), ensure_ascii=False)
                + "".join(str(item) for item in report.items))
        self.assertNotIn(bait, blob)


if __name__ == "__main__":
    unittest.main()
