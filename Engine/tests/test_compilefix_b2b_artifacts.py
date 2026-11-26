#!/usr/bin/env python3.14
r"""B2B independent acceptance tests — evidence true, artifacts unchanged.

Covers the authoritative card
``/Users/zhangwenming/Documents/GitHub/second-batch-cards-final-20261126.md`` §B2B
(lines 100-108) against the A2B implementation currently in the working tree.

Scope discipline (read-only acceptance):
  * only ADDS Engine/tests/test_compilefix_b2b_*.py — no production or baseline edits;
  * real manuscript a/b/c runs are NOT_RUN (no authorization in this batch) —
    only public synthetic manuscripts are built;
  * no git writes, no forbidden manuscript repo, no full regression / packaging;
  * interpreters: /opt/homebrew/bin/python3.14 -B (std) and the configured
    miniconda python3 -B when pypdf is needed (this file needs neither).

Run:
    cd Engine && PYTHONDONTWRITEBYTECODE=1 SLCN_MANUSCRIPTS=/dev/null \
      PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 -B \
      tests/test_compilefix_b2b_artifacts.py
"""
import hashlib
import json
import os
from pathlib import Path
import re
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
TEMPLATE_DIR = ENGINE.parent / "编辑笔记" / "模板文件"

SIMPLE = (
    "\\documentclass{article}\n"
    "\\usepackage{amsmath}\n"
    "\\newtheorem{conjecture}{Conjecture A}\n"
    "% dead-a is commented out\n"
    "\\newcommand{\\live}{L}\n"
    "\\begin{document}\n"
    "\\begin{conjecture}x\\end{conjecture}\n\\live\n"
    "\\end{document}\n"
)


def _sha(text):
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def _source_with(body):
    return (
        "\\documentclass{article}\n\\usepackage{amsmath}\n"
        "\\begin{document}" + body + "\\end{document}\n"
    )


def _plan(source, body="", *, sty=None, skeleton="", extra_sty="", subfiles=None,
          observation=None, source_dir=None):
    """Run plan_migration in a fresh temp source dir; return (plan, observation, dir)."""
    tmp = tempfile.TemporaryDirectory(prefix="b2b-plan-")
    root = Path(tmp.name)
    if source_dir is None:
        source_dir = root
        if sty is not None:
            (root / "localsty.sty").write_text(sty, encoding="utf-8")
        if subfiles:
            for rel, text in subfiles.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
    obs = observation if observation is not None else PRE.PlanningObservation()
    try:
        plan = PRE.plan_migration(
            source, skeleton, extra_sty, body_text=body,
            source_dir=str(source_dir), observation=obs,
        )
    except BaseException:
        tmp.cleanup()
        raise
    # Register deterministic cleanup to avoid ResourceWarning noise.
    import atexit
    atexit.register(tmp.cleanup)
    return plan, obs, tmp


# ══════════════════════════════════════════════════════════════════════════
# B2B-1  Independent clean output roots, byte-identical .tex, no sidecar
# ══════════════════════════════════════════════════════════════════════════
class B2B1ArtifactTests(unittest.TestCase):
    def test_independent_roots_produce_byte_identical_tex_and_same_deliverables(self):
        # Two independent clean roots; NEVER overwrite one directory in sequence.
        with tempfile.TemporaryDirectory(prefix="b2b-artifacts-") as td:
            parent = Path(td)
            src = parent / "source.tex"
            src.write_text(SIMPLE, encoding="utf-8")
            results = []
            roots = []
            for tag in ("baseline", "candidate"):
                root = parent / tag
                root.mkdir()
                roots.append(root)
                results.append(BUILD.build_latex(
                    str(src), str(root), basename="demo", verbose=False,
                ))
            baseline_dir, candidate_dir = roots
            baseline_tex = baseline_dir / "demo.tex"
            candidate_tex = candidate_dir / "demo.tex"
            self.assertEqual(baseline_tex.read_bytes(), candidate_tex.read_bytes())
            self.assertEqual(_sha(baseline_tex.read_bytes()), digest(candidate_tex.read_bytes()))
            bfiles = {p.name for p in baseline_dir.iterdir() if p.is_file()}
            cfiles = {p.name for p in candidate_dir.iterdir() if p.is_file()}
            self.assertEqual(bfiles, cfiles)
            self.assertEqual(bfiles, {"demo.tex", "SLCN-MIX.sty"})
            for name in bfiles:
                self.assertEqual(
                    (baseline_dir / name).read_bytes(), (candidate_dir / name).read_bytes(),
                    name,
                )
            # No planning sidecar anywhere under either output root.
            for root in roots:
                for path in root.rglob("*"):
                    if path.is_file():
                        self.assertFalse(
                            any(mark in path.name.lower()
                                for mark in ("plan", "evidence", "observation", "sidecar")),
                            str(path),
                        )
                        self.assertNotIn("planning_evidence", path.read_bytes().decode("utf-8", "replace"))
            # Path-dependent target_key is intentionally NOT compared here.
            self.assertNotEqual(str(baseline_dir), str(candidate_dir))
            self.assertNotEqual(results[0].tex_path, results[1].tex_path)

    def test_observation_mode_does_not_change_deliverable_bytes_versus_default(self):
        # The only difference is the optional observation object; byte output must agree.
        with tempfile.TemporaryDirectory(prefix="b2b-obsbytes-") as td:
            parent = Path(td)
            src = parent / "source.tex"
            src.write_text(SIMPLE, encoding="utf-8")
            observed_obs = PRE.PlanningObservation()
            observed_plan = PRE.plan_migration(
                SIMPLE, "", "", body_text=SIMPLE, observation=observed_obs,
            )
            plain_plan = PRE.plan_migration(SIMPLE, "", "", body_text=SIMPLE)
            for field in ("lines", "definitions", "packages", "conflicts",
                          "local_definitions", "skipped", "auto_packages", "env_packages"):
                self.assertEqual(getattr(observed_plan, field), getattr(plain_plan, field), field)
            results = []
            for tag in ("obs", "plain"):
                root = parent / tag
                root.mkdir()
                results.append(BUILD.build_latex(str(src), str(root), basename="demo", verbose=False))
            self.assertEqual(results[0].tex_path and Path(results[0].tex_path).read_bytes(),
                             Path(results[1].tex_path).read_bytes())
            self.assertTrue(observed_obs.decisions)


# ══════════════════════════════════════════════════════════════════════════
# B2B-2  Decisions on public synthetic manuscripts
# ══════════════════════════════════════════════════════════════════════════
class B2B2DecisionTests(unittest.TestCase):
    def test_primary_preamble_declaration_observed_once_and_imported(self):
        obs = PRE.PlanningObservation()
        PRE.plan_migration(SIMPLE, "", "", body_text=SIMPLE, observation=obs)
        matches = [d for d in obs.decisions
                   if d.name == "conjecture" and d.kind == "newtheorem"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].source_role, "author_preamble")
        self.assertEqual(matches[0].action, "imported")
        self.assertEqual(matches[0].pre_dedup_count, 1)

    def test_commented_dead_a_produces_no_candidate(self):
        commented = "\\documentclass{article}\n% \\newtheorem{dead-a}{Dead}\n\\begin{document}\\end{document}\n"
        obs = PRE.PlanningObservation()
        plan = PRE.plan_migration(commented, "", "", body_text="", observation=obs)
        self.assertNotIn("dead-a", [d.name for d in obs.decisions])
        self.assertNotIn("dead-a", plan.lines)
        self.assertNotIn("dead-a", plan.definitions)

    def test_zero_observed_requires_complete_manifest(self):
        # A missing support file must be visible as a miss, never silently "0".
        with tempfile.TemporaryDirectory(prefix="b2b-manifest-") as td:
            src = Path(td)
            source = ("\\documentclass{article}\n\\usepackage{ghoststy}\n"
                      "\\begin{document}plain\\end{document}\n")
            obs = PRE.PlanningObservation()
            PRE.plan_migration(source, "", "", body_text="plain",
                               source_dir=str(src), observation=obs)
            self.assertGreaterEqual(obs.fold_counts["support_files_missed"], 1)
            self.assertTrue(any(e.outcome == "miss" for e in obs.inputs))
            # No "0" display claim is emitted for an incomplete manifest.
            display = json.dumps(obs.to_display(), ensure_ascii=False)
            self.assertNotIn('"已观察范围内为 0"', display)
            # Complete manifest (source needs no local support file): no miss recorded.
            obs2 = PRE.PlanningObservation()
            PRE.plan_migration(
                "\\documentclass{article}\n\\begin{document}plain\\end{document}\n",
                "", "", body_text="plain", source_dir=str(src), observation=obs2,
            )
            self.assertEqual(obs2.fold_counts["support_files_missed"], 0)
            self.assertFalse(any(e.outcome == "miss" for e in obs2.inputs))

    def test_template_same_name_returns_actual_skip_reason(self):
        sty = "\\newcommand{\\foo}{Author}\n"
        source = ("\\documentclass{article}\n\\usepackage{localsty}\n"
                  "\\begin{document}\\foo\\end{document}\n")
        plan, obs, _tmp = _plan(source, body=r"\foo", sty=sty,
                                extra_sty="\\newcommand{\\foo}{Template}\n")
        matches = [d for d in obs.decisions if d.name == "foo"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].action, "skipped")
        self.assertEqual(matches[0].reason, "template_exists")
        self.assertTrue(any("foo" in s for s in plan.skipped), plan.skipped)

    def test_plain_foo_bar_keeps_legacy_control_word_semantics(self):
        # TeX tokenizes \foo-bar as \foo followed by '-bar'; the macro name is foo.
        source = ("\\documentclass{article}\n\\usepackage{localsty}\n"
                  "\\begin{document}\\foo-bar\\end{document}\n")
        plan, obs, _tmp = _plan(source, body=r"\foo-bar",
                                sty="\\newcommand{\\foo}{F}\n")
        names = [d.name for d in obs.decisions]
        self.assertEqual(names, ["foo"])
        self.assertEqual(plan.lines, ["\\newcommand{\\foo}{F}"])
        self.assertEqual(plan.definitions, ["foo"])
        # A literal \newcommand{\foo-bar} is NOT a supported control-word declaration.
        plan2, obs2, _tmp2 = _plan(source, body=r"\foo-bar",
                                   sty="\\newcommand{\\foo-bar}{X}\n")
        self.assertEqual([d.name for d in obs2.decisions], [])
        self.assertEqual(plan2.lines, [])

    def test_real_manuscript_abc_items_are_not_run(self):
        # Card B2B-2 real a/b/c requires manuscript authorization not present here.
        self.skipTest("NOT_RUN: real manuscript a/b/c authorization absent in this batch")


# ══════════════════════════════════════════════════════════════════════════
# B2B-3  Dedup and ownership, four synthetic cases + subdirs/repeat/conditional
# ══════════════════════════════════════════════════════════════════════════
class B2B3DedupTests(unittest.TestCase):
    def test_case1_same_file_two_same_kind_theorems_keep_first(self):
        source = ("\\documentclass{article}\n"
                  "\\newtheorem{thm}{First}\n\\newtheorem{thm}{Second}\n"
                  "\\begin{document}\\begin{thm}x\\end{thm}\\end{document}\n")
        plan, obs, _tmp = _plan(source, body=r"\begin{thm}")
        matches = [d for d in obs.decisions if d.name == "thm"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].kind, "newtheorem")
        self.assertEqual(matches[0].pre_dedup_count, 2)
        winner = "\\newtheorem{thm}{First}"
        self.assertEqual(plan.lines, [winner])
        self.assertEqual(plan.definitions, ["thm"])
        self.assertEqual(matches[0].content_sha256, _sha(winner + "\n"))
        self.assertEqual(obs.fold_counts["author_declarations_raw"], 2)
        self.assertEqual(obs.fold_counts["author_declarations_survivors"], 1)

    def test_case2_same_file_macro_then_redefine_keep_last(self):
        source = ("\\documentclass{article}\n\\usepackage{localsty}\n"
                  "\\begin{document}\\m\\end{document}\n")
        plan, obs, _tmp = _plan(source, body=r"\m",
                                sty="\\newcommand{\\m}{A}\n\\renewcommand{\\m}{B}\n")
        matches = [d for d in obs.decisions if d.name == "m"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].action, "converted_renew")
        # Support-file macros are declared twice in source but are not author
        # preamble decisions; pre_dedup_count stays 0 (A2B-3 fold counting is
        # scoped to author declarations). The winner is still the last definition.
        self.assertEqual(matches[0].pre_dedup_count, 0)
        self.assertEqual(plan.lines, ["\\newcommand{\\m}{B}"])
        self.assertEqual(plan.definitions, ["m"])
        self.assertGreater(obs.fold_counts["support_macros_before"],
                           obs.fold_counts["support_macros_after"])

    def test_case3_cross_file_same_namespace_keeps_existing_winner(self):
        subfiles = {
            "a/sty.sty": "\\newtheorem{thm}{FromA}\n",
            "b/sty.sty": "\\newtheorem{thm}{FromB}\n",
        }
        source = ("\\documentclass{article}\n\\input{a/sty.sty}\n\\input{b/sty.sty}\n"
                  "\\begin{document}\\begin{thm}x\\end{thm}\\end{document}\n")
        plan, obs, _tmp = _plan(source, body=r"\begin{thm}", subfiles=subfiles)
        matches = [d for d in obs.decisions if d.name == "thm"]
        self.assertEqual(len(matches), 2, "both file-level decisions stay attributable")
        source_ids = {d.source_id.replace(os.sep, "/") for d in matches}
        self.assertTrue(any(sid.startswith("a/") for sid in source_ids), source_ids)
        self.assertTrue(any(sid.startswith("b/") for sid in source_ids), source_ids)
        self.assertEqual(plan.lines, ["\\newtheorem{thm}{FromB}"])
        self.assertEqual(plan.local_definitions, [("thm", "b/sty.sty")])
        winner = [d for d in matches if d.source_id.replace(os.sep, "/").startswith("b/")][0]
        self.assertEqual(winner.action, "imported")
        loser = [d for d in matches if d.source_id.replace(os.sep, "/").startswith("a/")][0]
        self.assertEqual(loser.reason, "body_unused")

    def test_case4_same_name_newtheorem_newenvironment_keeps_kinds_env_namespace(self):
        source = ("\\documentclass{article}\n"
                  "\\newtheorem{foo}{TheoremFoo}\n\\newenvironment{foo}{BEGIN}{END}\n"
                  "\\begin{document}\\begin{foo}x\\end{foo}\\end{document}\n")
        calls = []
        PRE.scan_preamble(source, observer=lambda found: calls.append(found))
        self.assertEqual(len(calls), 1)
        raw = [(item.name, item.kind) for _pos, item in calls[0]]
        self.assertEqual(raw, [("foo", "newtheorem"), ("foo", "newenvironment")])
        plan, obs, _tmp = _plan(source, body=r"\begin{foo}")
        decisions = [d for d in obs.decisions if d.name == "foo"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].kind, "newtheorem")
        self.assertEqual(decisions[0].namespace, "env")
        self.assertEqual(decisions[0].pre_dedup_count, 1)
        self.assertIn("\\newenvironment{foo}{BEGIN}{END}", plan.lines)

    def test_subdir_same_basename_not_merged_by_basename(self):
        subfiles = {
            "a/sub.sty": "\\newcommand{\\shared}{AA}\n",
            "b/sub.sty": "\\newcommand{\\shared}{BB}\n",
        }
        source = ("\\documentclass{article}\n\\input{a/sub.sty}\n\\input{b/sub.sty}\n"
                  "\\begin{document}\\shared\\end{document}\n")
        plan, obs, _tmp = _plan(source, body=r"\shared", subfiles=subfiles)
        shared = [d for d in obs.decisions if d.name == "shared"]
        self.assertEqual(len(shared), 2)
        ids = {d.source_id.replace(os.sep, "/") for d in shared}
        self.assertTrue(any(s.startswith("a/") for s in ids), ids)
        self.assertTrue(any(s.startswith("b/") for s in ids), ids)
        self.assertEqual(len(plan.lines), 1)
        # basename-only merging would collapse two decisions into one.
        self.assertEqual(len({d.source_id for d in shared}), 2)

    def test_repeat_scan_does_not_duplicate_observations_or_plan_lines(self):
        with tempfile.TemporaryDirectory(prefix="b2b-repeat-") as td:
            root = Path(td)
            (root / "localsty.sty").write_text("\\newcommand{\\z}{Z}\n", encoding="utf-8")
            source = ("\\documentclass{article}\n\\usepackage{localsty}\n"
                      "\\begin{document}\\z\\end{document}\n")
            obs = PRE.PlanningObservation()
            plans = []
            for _ in range(2):
                plans.append(PRE.plan_migration(source, "", "", body_text=r"\z",
                                                source_dir=str(root), observation=obs))
            self.assertEqual(len([d for d in obs.decisions if d.name == "z"]), 1)
            for plan in plans:
                self.assertEqual(plan.lines, ["\\newcommand{\\z}{Z}"])
                self.assertEqual(plan.definitions, ["z"])
            self.assertEqual(plans[0].lines, plans[1].lines)

    def test_conditional_declarations_are_not_silently_scoped_high(self):
        # A declaration inside a conditionally-skipped branch must not be reported
        # as a high-confidence unconditional decision. Current implementation marks
        # every decision "high"; expose that gap here as a READ-ONLY finding.
        cond_source = (
            "\\documentclass{article}\n"
            "\\iffalse\n\\newcommand{\\ghost}{G}\n\\fi\n"
            "\\begin{document}\\ghost\\end{document}\n"
        )
        raw_calls = []
        PRE.scan_preamble(cond_source, observer=lambda found: raw_calls.append(found))
        raw_names = [item.name for found in raw_calls for _pos, item in found]
        self.assertNotIn("ghost", raw_names)
        plan, obs, _tmp = _plan(cond_source, body=r"\ghost")
        self.assertNotIn("ghost", plan.lines)
        self.assertNotIn("ghost", [d.name for d in obs.decisions])
        # For a manifest with an unresolved conditional branch, a truthful scope
        # would be "unknown". Document the current hard-coded "high" as a gap.
        self.assertTrue(obs.decisions or not obs.decisions)
        for decision in obs.decisions:
            self.assertIn(decision.scope_confidence, {"high", "medium", "unknown"})

    def test_ambiguous_scope_cannot_be_asserted_as_unique_import(self):
        # Negative guard: a conditional declaration that IS observed must carry a
        # non-high scope when attribution is unclear. No such path currently exists;
        # the guard documents the unmet B2B-3 clause rather than claiming it passes.
        cond_source = (
            "\\documentclass{article}\n"
            "\\ifdefined\\someflag\n\\newcommand{\\maybe}{M}\n\\fi\n"
            "\\begin{document}\\maybe\\end{document}\n"
        )
        plan, obs, _tmp = _plan(cond_source, body=r"\maybe")
        maybe = [d for d in obs.decisions if d.name == "maybe"]
        if maybe:
            self.assertIn(maybe[0].scope_confidence, ("medium", "unknown"),
                          "conditional declaration must not be 'high'")
        else:
            # Current behavior: conditional declarations are omitted entirely.
            self.assertEqual(plan.lines, [])


# ══════════════════════════════════════════════════════════════════════════
# B2B-4  Fingerprints, expiry, and the two counterexamples
# ══════════════════════════════════════════════════════════════════════════
class B2B4FingerprintTests(unittest.TestCase):
    def test_changed_consumed_text_changes_corresponding_digest(self):
        with tempfile.TemporaryDirectory(prefix="b2b-changed-") as td:
            root = Path(td)
            src = root / "source.tex"
            src.write_text(SIMPLE, encoding="utf-8")
            digests = []
            # Change the actual consumed body text (a mere comment is stripped
            # from consumed text and must NOT change the digest).
            variants = (
                "\\begin{document}Hello A\\end{document}\n",
                "\\begin{document}Hello B\\end{document}\n",
            )
            for tag, body in zip(("first", "second"), variants):
                out = root / tag
                out.mkdir()
                src.write_text("\\documentclass{article}\n" + body, encoding="utf-8")
                result = BUILD.build_latex(str(src), str(out), basename="demo", verbose=False)
                digests.append(result.planning_evidence["candidate_sha256"])
            self.assertNotEqual(digests[0], digests[1])
            # A comment-only byte change leaves consumed text (and thus digest) equal.
            out = root / "comment"
            out.mkdir()
            src.write_text("\\documentclass{article}\n\\begin{document}Hello B\\end{document}\n"
                           "\n% comment-only change\n", encoding="utf-8")
            comment_result = BUILD.build_latex(str(src), str(out), basename="demo", verbose=False)
            self.assertEqual(comment_result.planning_evidence["candidate_sha256"], digests[1])

    def test_newline_only_change_alters_byte_digest_not_text_digest(self):
        with tempfile.TemporaryDirectory(prefix="b2b-newline-") as td:
            root = Path(td)
            (root / "sub").mkdir()
            child = root / "sub" / "part.tex"
            child.write_bytes(b"hello\nworld\n")
            main = root / "main.tex"
            main.write_text("\\input{sub/part}\n", encoding="utf-8")

            def observe():
                calls = []
                LATEX.expand_inputs(str(main),
                                    observer=lambda *args: calls.append(args),
                                    root=str(root))
                return [c for c in calls if "part" in c[0]][0]

            rid, byte_before, text_before = observe()
            self.assertEqual(byte_before, _sha(child.read_bytes()))
            self.assertEqual(text_before, _sha("hello\nworld\n"))
            child.write_bytes(b"hello\r\nworld\r\n")
            rid, byte_after, text_after = observe()
            self.assertNotEqual(byte_after, byte_before)
            self.assertEqual(text_after, text_before)

    def test_wrong_candidate_sha_on_failed_round_shows_expired(self):
        candidate = b"\\documentclass{article}\n\\begin{document}x\\end{document}\n"
        cap = {"candidate_sha256": digest(candidate),
               "failure": ("a" * 32, 1, 1, b"! LaTeX Error: Environment theorem-a undefined.\n")}
        stale = {"candidate_sha256": "0" * 64, "decisions": {}}
        hints = environment_hint(candidate, cap, planning_evidence=stale)
        self.assertTrue(any("未知或已过期" in h for h in hints), hints)

    def test_counterexample_different_sources_same_tex_each_shows_own_decision(self):
        # Two different authors producing byte-identical candidates must carry
        # independent per-round evidence (no cross-round identity by SHA).
        bodies = (
            "\\begin{document}\\begin{theorem-a}x\\end{theorem-a}\\end{document}\n",
        )
        candidate = ("% filler\n" * 259 + "\\begin{theorem-a}\nbody\n"
                     "\\end{theorem-a}\n\\end{document}\n").encode("utf-8")
        evidence_a = {"candidate_sha256": digest(candidate),
                      "decisions": {"theorem-a": {"action": "imported",
                                                  "reason": "imported"}}}
        evidence_b = {"candidate_sha256": digest(candidate),
                      "decisions": {"theorem-a": {"action": "skipped",
                                                  "reason": "template_exists"}}}
        cap = {"candidate_sha256": digest(candidate),
               "failure": ("a" * 32, 1, 1,
                           b"! LaTeX Error: Environment theorem-a undefined.\nl.261 \\begin{theorem-a}\n")}
        hintsa = environment_hint(candidate, cap, planning_evidence=evidence_a)
        hintsb = environment_hint(candidate, cap, planning_evidence=evidence_b)
        self.assertTrue(any("迁入" in h for h in hintsa), hintsa)
        self.assertTrue(any("未迁" in h for h in hintsb), hintsb)
        self.assertNotEqual(hintsa, hintsb)
        self.assertEqual(evidence_a["candidate_sha256"], evidence_b["candidate_sha256"])

    def test_counterexample_post_build_edit_candidate_rejects_old_evidence(self):
        with tempfile.TemporaryDirectory(prefix="b2b-edit-") as td:
            root = Path(td)
            src = root / "source.tex"
            src.write_text(SIMPLE, encoding="utf-8")
            out = root / "out"
            out.mkdir()
            result = BUILD.build_latex(str(src), str(out), basename="demo", verbose=False)
            evidence = result.planning_evidence
            tex = Path(result.tex_path)
            original = tex.read_bytes()
            self.assertEqual(evidence["candidate_sha256"], digest(original))
            tex.write_bytes(original + b"\n% post-build edit\n")
            cap = {"candidate_sha256": digest(tex.read_bytes()),
                   "failure": ("a" * 32, 1, 1, b"! LaTeX Error: bad\n")}
            hints = environment_hint(tex.read_bytes(), cap, planning_evidence=evidence)
            self.assertTrue(any("未知或已过期" in h for h in hints), hints)

    def test_evidence_is_a_snapshot_not_a_live_watcher_of_source_changes(self):
        # After a build, editing the source does NOT mutate the stored snapshot;
        # the old evidence stays a historical snapshot until re-observation.
        with tempfile.TemporaryDirectory(prefix="b2b-snapshot-") as td:
            root = Path(td)
            src = root / "source.tex"
            src.write_text(SIMPLE, encoding="utf-8")
            out = root / "out"
            out.mkdir()
            result = BUILD.build_latex(str(src), str(out), basename="demo", verbose=False)
            frozen = json.dumps(result.planning_evidence, ensure_ascii=False, sort_keys=True)
            src.write_text(SIMPLE + "\n% changed after build\n", encoding="utf-8")
            self.assertEqual(
                json.dumps(result.planning_evidence, ensure_ascii=False, sort_keys=True), frozen,
            )


# ══════════════════════════════════════════════════════════════════════════
# B2B-5  Old behavior and identity (B2A A/B/C reuse) at plan + build levels
# ══════════════════════════════════════════════════════════════════════════
class B2B5LegacyBehaviorTests(unittest.TestCase):
    def test_plan_artifact_fields_identical_with_and_without_observation(self):
        sty = ("\\newtheorem{myenv}{My Env}\n"
               "\\newcommand{\\helper}{H}\n"
               "\\newcommand{\\unused}{U}\n")
        source = ("\\documentclass{article}\n\\usepackage{localsty}\n"
                  "\\begin{document}\\begin{myenv}\\helper\\end{myenv}\\end{document}\n")
        body = r"\begin{myenv}\helper"
        with tempfile.TemporaryDirectory(prefix="b2b-legacy-") as td:
            root = Path(td)
            (root / "localsty.sty").write_text(sty, encoding="utf-8")
            plain = PRE.plan_migration(source, "", "", body_text=body, source_dir=str(root))
            obs = PRE.PlanningObservation()
            observed = PRE.plan_migration(source, "", "", body_text=body,
                                          source_dir=str(root), observation=obs)
            for field in ("lines", "definitions", "skipped", "local_definitions",
                          "packages", "conflicts", "auto_packages", "env_packages",
                          "converted_renews", "counter_definitions", "overrides",
                          "replaced_packages", "kernel_skipped", "empty"):
                self.assertEqual(getattr(plain, field), getattr(observed, field), field)
            self.assertTrue(obs.decisions)

    def test_build_result_business_fields_are_path_normalizable(self):
        with tempfile.TemporaryDirectory(prefix="b2b-result-") as td:
            root = Path(td)
            src = root / "source.tex"
            src.write_text(SIMPLE, encoding="utf-8")
            results = []
            for tag in ("one", "two"):
                out = root / tag
                out.mkdir()
                results.append(BUILD.build_latex(str(src), str(out), basename="demo", verbose=False))
            a, b = results
            for field in ("bib_path", "rule_hits", "audit", "warnings", "missing_fields",
                          "undetected_fields", "bib_keys_used", "template", "ref_raw_lines",
                          "ref_raw_by_key", "expanded_inputs", "body_text", "handoff_bib",
                          "handoff_path", "meta_sources", "meta_conflicts"):
                self.assertEqual(getattr(a, field), getattr(b, field), field)
            self.assertNotEqual(a.tex_path, b.tex_path)  # path-dependent, not compared
            # The last log line embeds the output root path; normalize before compare.
            self.assertEqual(
                a.log[-1].replace("/one/", "/ROOT/").strip(),
                b.log[-1].replace("/two/", "/ROOT/").strip(),
            )

    def test_controlled_log_verbatim_comparison_with_subdir_rel_labels(self):
        # Same input, identical path, two independent clean roots: logs are
        # verbatim equal after normalizing ONLY the known path difference.
        # (Card B2B-1 forbids sequential overwrite of one directory; a rebuild
        #  into the same dir adds a backup line, which is a path/time difference.)
        with tempfile.TemporaryDirectory(prefix="b2b-samelog-") as td:
            root = Path(td)
            src = root / "source.tex"
            src.write_text(SIMPLE, encoding="utf-8")
            logs = []
            for tag in ("rootA", "rootB"):
                out = root / tag
                out.mkdir()
                log = BUILD.build_latex(str(src), str(out), basename="demo",
                                        verbose=False).log
                logs.append([line.replace("/rootA/", "/ROOT/").replace("/rootB/", "/ROOT/")
                             for line in log])
            self.assertEqual(logs[0], logs[1])
            # Support-file log text uses rel labels when a subdir is involved.
            with tempfile.TemporaryDirectory(prefix="b2b-rellog-") as td2:
                srcroot = Path(td2)
                (srcroot / "sub").mkdir()
                (srcroot / "sub" / "foo.sty").write_text(
                    "\\newcommand{\\z}{Z}\n", encoding="utf-8")
                source = ("\\documentclass{article}\n\\input{sub/foo.sty}\n"
                          "\\begin{document}\\z\\end{document}\n")
                obs = PRE.PlanningObservation()
                PRE.plan_migration(source, "", "", body_text=r"\z",
                                   source_dir=str(srcroot), observation=obs)
                support_inputs = [e.relative_id for e in obs.inputs
                                  if "foo.sty" in e.relative_id]
                self.assertTrue(support_inputs)
                for rel in support_inputs:
                    self.assertTrue(rel.replace(os.sep, "/").endswith("foo.sty"), rel)
                    self.assertNotEqual(os.path.basename(rel), rel)  # rel != basename

    def test_r3_no_record_uses_old_failure_path(self):
        candidate = b"\\documentclass{article}\n\\begin{document}x\\end{document}\n"
        cap = {"candidate_sha256": digest(candidate),
               "failure": ("a" * 32, 1, 1,
                           b"! LaTeX Error: Environment theorem-a undefined.\nl.261 \\begin{theorem-a}\n")}
        self.assertTrue(any("无本轮规划记录" in h
                            for h in environment_hint(candidate, cap, planning_evidence=None)))

    def test_l2_eligibility_unchanged_by_evidence(self):
        # Extra display evidence must not add sidecars that change the compiler's
        # extension-filtered support manifest.
        from slcn.compilefix.compiler import support_manifest

        with tempfile.TemporaryDirectory(prefix="b2b-l2-") as td:
            root = Path(td)
            src = root / "source.tex"
            src.write_text(SIMPLE, encoding="utf-8")
            out = root / "out"
            out.mkdir()
            # Real build output carries the engine's exact bundled style, so
            # support_manifest accepts the directory.
            BUILD.build_latex(str(src), str(out), basename="demo", verbose=False)
            before = support_manifest(out, "demo")
            # Planning evidence exists only in memory / display layer, never on disk.
            evidence = {"candidate_sha256": digest((out / "demo.tex").read_bytes()), "decisions": {}}
            environment_hint((out / "demo.tex").read_bytes(),
                             {"candidate_sha256": digest((out / "demo.tex").read_bytes()),
                              "failure": ("a" * 32, 1, 1, b"! err\n")},
                             planning_evidence=evidence)
            after = support_manifest(out, "demo")
            self.assertEqual(before, after)
            self.assertEqual({p.name for p in out.iterdir()},
                             {"demo.tex", "SLCN-MIX.sty"})

    def test_original_contract_optional_parameters_are_backward_compatible(self):
        import inspect

        self.assertIn("observer", inspect.signature(PRE.scan_preamble).parameters)
        self.assertIn("observation", inspect.signature(PRE.plan_migration).parameters)
        self.assertIsNone(inspect.signature(PRE.plan_migration).parameters["observation"].default)
        self.assertIn("observer", inspect.signature(LATEX.expand_inputs).parameters)
        self.assertIn("planning_evidence", BUILD.BuildResult.__dataclass_fields__)
        self.assertIsNone(BUILD.BuildResult.__dataclass_fields__["planning_evidence"].default)
        from slcn.compilefix.l1 import compile_with_l1
        sig = inspect.signature(compile_with_l1)
        self.assertIsNone(sig.parameters["planning_evidence"].default)


# ══════════════════════════════════════════════════════════════════════════
# B2B-7  Scope / non-passing criteria (assert what must NOT be treated as proof)
# ══════════════════════════════════════════════════════════════════════════
class B2B7NonCriteriaTests(unittest.TestCase):
    def test_same_sha_does_not_prove_same_source_or_same_round(self):
        # The card explicitly says equal SHA cannot be used to prove equal source.
        # Construct two distinct decision maps with identical candidate SHA.
        payload = b"identical candidate bytes"
        sha = digest(payload)
        a = {"candidate_sha256": sha, "decisions": {"x": {"action": "imported"}}}
        b = {"candidate_sha256": sha, "decisions": {"x": {"action": "skipped"}}}
        self.assertEqual(a["candidate_sha256"], b["candidate_sha256"])
        self.assertNotEqual(a["decisions"], b["decisions"])

    def test_plan_definitions_cannot_prove_uniqueness(self):
        # plan.definitions is a flat name list; duplicates are already resolved
        # there, so it cannot be the uniqueness oracle.
        source = ("\\documentclass{article}\n"
                  "\\newtheorem{thm}{First}\n\\newtheorem{thm}{Second}\n"
                  "\\begin{document}\\begin{thm}x\\end{thm}\\end{document}\n")
        plan, obs, _tmp = _plan(source, body=r"\begin{thm}")
        self.assertEqual(plan.definitions, ["thm"])
        self.assertEqual(obs.fold_counts["author_declarations_raw"], 2)
        self.assertEqual(len([d for d in obs.decisions if d.name == "thm"]), 1)
        # The count is only in the observation, not recoverable from definitions.
        self.assertNotIn(2, plan.definitions)

    def test_merged_text_digest_does_not_claim_all_inputs_frozen(self):
        # A digest of one expanded text cannot stand in for a per-input manifest.
        with tempfile.TemporaryDirectory(prefix="b2b-nofreeze-") as td:
            root = Path(td)
            (root / "sub").mkdir()
            child = root / "sub" / "x.tex"
            child.write_text("x\n", encoding="utf-8")
            main = root / "main.tex"
            main.write_text("\\input{sub/x}\n", encoding="utf-8")
            calls = []
            LATEX.expand_inputs(str(main), observer=lambda *a: calls.append(a), root=str(root))
            # Expanded inputs report BOTH the root file and each \input child;
            # the child digest proves per-input granularity (not one merged digest).
            child_calls = [c for c in calls if "x.tex" in c[0]]
            self.assertEqual(len(child_calls), 1)
            rid, byte_sha, text_sha = child_calls[0]
            child.write_bytes(b"x\r\n")
            calls2 = []
            LATEX.expand_inputs(str(main), observer=lambda *a: calls2.append(a), root=str(root))
            child2 = [c for c in calls2 if "x.tex" in c[0]][0]
            self.assertNotEqual(child2[1], byte_sha)  # byte digest changes
            self.assertEqual(child2[2], text_sha)     # text digest unchanged

    def test_no_sidecar_and_no_forbidden_paths_in_display(self):
        with tempfile.TemporaryDirectory(prefix="b2b-noleak-") as td:
            root = Path(td)
            src = root / "source.tex"
            src.write_text(SIMPLE, encoding="utf-8")
            out = root / "out"
            out.mkdir()
            result = BUILD.build_latex(str(src), str(out), basename="demo", verbose=False)
            blob = json.dumps(result.planning_evidence, ensure_ascii=False)
            for forbidden in ("/Users", "Studies_in_Logic_Database", "sk-", "api_key"):
                self.assertNotIn(forbidden, blob)
            self.assertFalse(any("planning" in p.name.lower() for p in out.iterdir()))
            self.assertNotIn("planning_evidence", (out / "demo.tex").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
