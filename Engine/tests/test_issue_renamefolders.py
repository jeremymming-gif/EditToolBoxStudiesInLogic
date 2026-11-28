#!/usr/bin/env python3
"""Synthetic coverage for in-place issue folder renaming and interrupted writes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))


def tree_hashes(folder):
    return {
        file.relative_to(folder).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in folder.rglob("*") if file.is_file()
    }


class RenameFoldersTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="slcn-order-rename-")
        self.addCleanup(temp.cleanup)
        self.parent = Path(temp.name).resolve()
        self.issue = self.parent / "issue"
        self.issue.mkdir()
        self.state = self.parent / "state"
        self.env = dict(os.environ, SLCN_ISSUE_STATE_DIR=str(self.state))
        self.env["PYTHONPATH"] = os.pathsep.join((str(ENGINE), self.env.get("PYTHONPATH", "")))

    def article(self, folder, stem=None):
        article = self.issue / folder
        article.mkdir()
        (article / f"{stem or folder}.tex").write_text(f"synthetic {folder}\n", encoding="utf-8")
        (article / "original").mkdir()
        (article / "original" / "original.docx").write_bytes(f"bytes {folder}".encode())
        return article

    def run_cli(self, *args, ordered=(), excluded=()):
        if "--recover" in args:
            options, data = [], None
        else:
            options = ["--order", "-"]
            data = json.dumps({"schema": 1, "ordered": list(ordered), "excluded": list(excluded)})
        result = subprocess.run(
            [sys.executable, "-B", str(ENGINE / "renamefolders.py"), "--issue", str(self.issue),
             "--json", *options, *args], input=data, capture_output=True, text=True,
            encoding="utf-8", env=self.env, check=False,
        )
        self.assertIn(result.returncode, (0, 1, 2), result.stderr)
        try:
            body = json.loads(result.stdout)
        except ValueError:
            self.fail(f"Expected one JSON response. rc={result.returncode}: {result.stderr}")
        self.assertEqual(body["schema"], 1)
        self.assertEqual(body["operation"], "renamefolders")
        return result.returncode, body

    def test_preview_and_existing_numbered_reorder_preserve_subtrees(self):
        alpha = self.article("02 Alpha", "Alpha")
        beta = self.article("01 Beta", "Beta")
        first = tree_hashes(alpha)
        second = tree_hashes(beta)
        alpha_ino = alpha.stat().st_ino
        beta_ino = beta.stat().st_ino
        order = ("02 Alpha", "01 Beta")

        rc, preview = self.run_cli(ordered=order)
        self.assertEqual((rc, preview["status"]), (0, "preview"))
        self.assertTrue(preview["can_apply"])
        self.assertEqual(preview["renames"], [
            {"old": "02 Alpha", "new": "01 Alpha", "main_tex": "Alpha.tex"},
            {"old": "01 Beta", "new": "02 Beta", "main_tex": "Beta.tex"},
        ])
        self.assertEqual(preview["issue"]["path"], str(self.issue))
        self.assertFalse(self.state.exists())
        self.assertEqual(tree_hashes(alpha), first)
        self.assertEqual(tree_hashes(beta), second)

        rc, result = self.run_cli("--apply", "--plan-id", preview["plan_id"], ordered=order)
        self.assertEqual((rc, result["status"]), (0, "completed"))
        self.assertEqual(result["renames"], preview["renames"])
        self.assertEqual(result["directories"], {
            "01 Alpha": [self.issue.stat().st_dev, alpha_ino],
            "02 Beta": [self.issue.stat().st_dev, beta_ino],
        })
        self.assertEqual({p.name for p in self.issue.iterdir()}, {"01 Alpha", "02 Beta"})
        new_alpha = self.issue / "01 Alpha"
        new_beta = self.issue / "02 Beta"
        self.assertEqual((new_alpha.stat().st_ino, tree_hashes(new_alpha)), (alpha_ino, first))
        self.assertEqual((new_beta.stat().st_ino, tree_hashes(new_beta)), (beta_ino, second))

    def test_already_canonical_plan_has_zero_writes_even_in_state(self):
        article = self.article("01 Alpha", "Alpha")
        before = self.issue.stat().st_mtime_ns, article.stat().st_ctime_ns, tree_hashes(article)
        rc, preview = self.run_cli(ordered=("01 Alpha",))
        self.assertEqual(rc, 0)
        rc, result = self.run_cli("--apply", "--plan-id", preview["plan_id"], ordered=("01 Alpha",))
        self.assertEqual((rc, result["status"]), (0, "unchanged"))
        self.assertEqual(
            (self.issue.stat().st_mtime_ns, article.stat().st_ctime_ns, tree_hashes(article)), before,
        )
        self.assertFalse(self.state.exists())

    def test_stale_plan_preserves_new_occupant(self):
        article = self.article("Alpha")
        _, preview = self.run_cli(ordered=("Alpha",))
        occupant = self.issue / "01 Alpha"
        occupant.write_bytes(b"other file")
        rc, result = self.run_cli("--apply", "--plan-id", preview["plan_id"], ordered=("Alpha",))
        self.assertEqual((rc, result["status"]), (2, "blocked"))
        self.assertEqual(occupant.read_bytes(), b"other file")
        self.assertTrue(article.is_dir())
        self.assertFalse(result["can_apply"])

    def test_same_name_replacement_after_validation_is_never_moved(self):
        from slcn import issueorder, issuewrite

        original = self.article("Alpha")
        plan = issueorder.build_rename_plan(self.issue, issueorder.OrderInput(("Alpha",), ()))
        displaced = self.parent / "displaced-alpha"
        execute = issueorder._execute_renames

        def replace_then_execute(identity, renames, issue, state_dir, confirmed_ids):
            original.rename(displaced)
            replacement = issue / "Alpha"
            replacement.mkdir()
            (replacement / "replacement.txt").write_text("replacement", encoding="utf-8")
            return execute(identity, renames, issue, state_dir, confirmed_ids)

        with patch.object(issueorder, "_execute_renames", replace_then_execute):
            result = issueorder.apply_rename_plan(plan, plan_id=plan.plan_id, state_dir=self.state)
        self.assertEqual(result.status, "blocked")
        self.assertEqual((self.issue / "Alpha" / "replacement.txt").read_text(encoding="utf-8"),
                         "replacement")
        self.assertFalse((self.issue / "01 Alpha").exists())
        self.assertTrue((displaced / "Alpha.tex").is_file())
        self.assertIsNone(issuewrite.pending_issue_write(self.issue, self.state))

    def test_typo_in_plan_id_and_main_tex_edit_stop_apply(self):
        article = self.article("Alpha")
        _, preview = self.run_cli(ordered=("Alpha",))
        rc, _ = self.run_cli("--apply", "--plan-id", "0" * 64, ordered=("Alpha",))
        self.assertEqual(rc, 2)
        tex = article / "Alpha.tex"
        tex.write_text("edited since preview", encoding="utf-8")
        rc, _ = self.run_cli("--apply", "--plan-id", preview["plan_id"], ordered=("Alpha",))
        self.assertEqual(rc, 2)
        self.assertEqual(tex.read_text(encoding="utf-8"), "edited since preview")

    def test_explicit_coverage_and_unsafe_order_names(self):
        self.article("Alpha")
        (self.issue / "Auxiliary").mkdir()
        for ordered, excluded in [
            (("Alpha",), ()),
            (("Alpha", "Alpha"), ("Auxiliary",)),
            (("Alpha", "../elsewhere"), ("Auxiliary",)),
            (("Alpha", "X\\Y"), ("Auxiliary",)),
            (("Alpha",), ("Alpha", "Auxiliary")),
            (("Alpha", "Auxiliary"), ()),
            (("Alpha",), ("Auxiliary", "000 Table of Contents")),
        ]:
            with self.subTest(ordered=ordered, excluded=excluded):
                rc, result = self.run_cli(ordered=ordered, excluded=excluded)
                self.assertEqual((rc, result["status"], result["can_apply"]), (2, "blocked", False))
                self.assertTrue(result["conflicts"])
        rc, plan = self.run_cli(ordered=("Alpha",), excluded=("Auxiliary",))
        self.assertEqual((rc, plan["excluded"]), (0, ["Auxiliary"]))
        self.assertFalse(self.state.exists())

    def test_orderdraft_candidates_are_the_complete_rename_inventory(self):
        from slcn.orderdraft import list_order_candidates

        self.article("Alpha")
        (self.issue / "Auxiliary").mkdir()
        for name in (".editorial-work", "__pycache__", "origin", "原稿", "000 Table of Contents", "000 目录"):
            self.article(name)
        candidates = list_order_candidates(self.issue)
        self.assertEqual([item.folder for item in candidates], ["Alpha", "Auxiliary"])
        rc, preview = self.run_cli(ordered=("Alpha",), excluded=("Auxiliary",))
        self.assertEqual((rc, preview["status"]), (0, "preview"))
        rc, preview = self.run_cli(ordered=("Alpha", "000 目录"), excluded=("Auxiliary",))
        self.assertEqual((rc, preview["status"]), (2, "blocked"))

    def test_rename_cannot_apply_or_recover_a_refresh_transaction(self):
        from slcn.issuewrite import issue_identity, record_path, write_record

        self.article("Alpha")
        _, preview = self.run_cli(ordered=("Alpha",))
        self.assertTrue(preview["can_apply"])
        identity = issue_identity(self.issue)
        record = record_path(identity, self.state)
        write_record(record, {"schema": 1, "operation": "refreshpages", "issue": identity,
                              "recovery_id": "a" * 32})
        before = record.read_bytes(), tree_hashes(self.issue)
        rc, result = self.run_cli("--apply", "--plan-id", preview["plan_id"], ordered=("Alpha",))
        self.assertEqual((rc, result["status"], result["recovery_operation"]),
                         (1, "recovery_required", "refreshpages"))
        rc, result = self.run_cli("--recover", "a" * 32)
        self.assertEqual((rc, result["status"]), (1, "recovery_required"))
        self.assertEqual((record.read_bytes(), tree_hashes(self.issue)), before)

    def test_default_state_location_is_persistent_and_override_is_read_only(self):
        from slcn.issuewrite import default_state_dir, pending_issue_write

        with patch.dict(os.environ, {"SLCN_ISSUE_STATE_DIR": ""}):
            self.assertEqual(default_state_dir(), Path.home().resolve() / ".editorial-toolbox" / "issue-write")
        with patch.dict(os.environ, {"SLCN_ISSUE_STATE_DIR": str(self.state)}):
            self.assertEqual(default_state_dir(), self.state)
        self.assertFalse(self.state.exists())
        with self.assertRaises(OSError):
            pending_issue_write(self.issue, self.issue / "state")
        self.assertFalse((self.issue / "state").exists())

    def test_folded_occupied_target_and_number_boundary(self):
        self.article("Alpha")
        other = self.issue / "01 alpha"
        other.write_bytes(b"collision")
        rc, plan = self.run_cli(ordered=("Alpha",))
        self.assertEqual((rc, plan["status"]), (2, "blocked"))
        self.assertEqual(other.read_bytes(), b"collision")
        other.unlink()
        names = ("Alpha",) + tuple(f"Article{x:03}" for x in range(99))
        for name in names[1:]:
            self.article(name)
        rc, plan = self.run_cli(ordered=names[:-1], excluded=names[-1:])
        self.assertEqual(rc, 0)
        self.assertEqual(plan["renames"][-1]["new"], "99 Article097")
        rc, plan = self.run_cli(ordered=names)
        self.assertEqual((rc, plan["status"]), (2, "blocked"))
        self.assertTrue(any("99" in conflict for conflict in plan["conflicts"]))

    def test_selected_link_rejected_and_reserved_prefix_not_a_candidate(self):
        self.article("Alpha")
        (self.issue / "000 Table of Contents").mkdir()
        outside = self.parent / "elsewhere"
        outside.mkdir()
        try:
            (self.issue / "External").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        rc, result = self.run_cli(ordered=("Alpha", "External"))
        self.assertEqual((rc, result["status"]), (2, "blocked"))
        rc, result = self.run_cli(ordered=("Alpha",), excluded=("External",))
        self.assertEqual((rc, result["status"]), (0, "preview"))
        self.assertTrue((self.issue / "External").is_symlink())

    def test_explicit_order_resolves_duplicate_old_numbers(self):
        self.article("01 Alpha", "Alpha")
        self.article("01 Beta", "Beta")
        rc, plan = self.run_cli(ordered=("01 Beta", "01 Alpha"))
        self.assertEqual(rc, 0)
        self.assertTrue(plan["warnings"])
        self.assertEqual([r["new"] for r in plan["renames"]], ["01 Beta", "02 Alpha"])

    def test_second_step_failure_rolls_back_true_cycle(self):
        from slcn import issueorder, issuewrite

        self.article("Alpha")
        self.article("Beta")
        before = tree_hashes(self.issue)
        renames = (issueorder.Rename("Alpha", "Beta", "Alpha.tex"),
                   issueorder.Rename("Beta", "Alpha", "Beta.tex"))
        real_rename = issueorder.rename_exclusive
        movements = []

        def fail_second(source, target):
            movements.append((source, target))
            if len(movements) == 2:
                raise PermissionError("simulated Windows sharing violation")
            real_rename(source, target)

        with issuewrite.issue_write_lock(self.issue, self.state):
            with patch.object(issueorder, "rename_exclusive", fail_second):
                result = issueorder._execute_renames(issuewrite.issue_identity(self.issue), renames,
                    self.issue, self.state,
                    {name: issueorder._directory_id(self.issue / name, self.issue.stat().st_dev)
                     for name in ("Alpha", "Beta")})
        self.assertEqual(result.status, "rolled_back")
        self.assertGreater(len(movements), 2)
        self.assertEqual(tree_hashes(self.issue), before)
        self.assertEqual({p.name for p in self.issue.iterdir()}, {"Alpha", "Beta"})
        self.assertIsNone(issuewrite.pending_issue_write(self.issue, self.state))

    def test_recovery_rejects_replaced_directory_and_can_resume_partial_rollback(self):
        from slcn import issueorder, issuewrite

        self.article("Alpha")
        self.article("Beta")
        before = tree_hashes(self.issue)
        renames = (issueorder.Rename("Alpha", "01 Alpha", "Alpha.tex"),
                   issueorder.Rename("Beta", "02 Beta", "Beta.tex"))
        real_write = issuewrite.write_record

        def interrupt_after_publish(path, record):
            if record["completed"] == 2:
                raise RuntimeError("process interrupted")
            real_write(path, record)

        with patch.object(issuewrite, "write_record", interrupt_after_publish):
            with self.assertRaises(RuntimeError):
                issueorder._execute_renames(issuewrite.issue_identity(self.issue), renames,
                    self.issue, self.state,
                    {name: issueorder._directory_id(self.issue / name, self.issue.stat().st_dev)
                     for name in ("Alpha", "Beta")})
        pending = issuewrite.pending_issue_write(self.issue, self.state)
        recovery_id = pending["recovery_id"]
        displaced = self.issue / "02 Beta"
        saved = self.parent / "saved"
        displaced.rename(saved)
        displaced.mkdir()
        rc, result = self.run_cli("--recover", recovery_id)
        self.assertEqual((rc, result["status"]), (1, "recovery_required"))
        self.assertTrue((self.issue / "01 Alpha").is_dir())
        displaced.rmdir()
        saved.rename(displaced)

        rename = issueorder.rename_exclusive

        def interrupt_during_rollback(source, target):
            if Path(source).name == "01 Alpha":
                raise PermissionError("simulated rollback interruption")
            rename(source, target)

        with patch.object(issueorder, "rename_exclusive", interrupt_during_rollback):
            result = issueorder.recover_issue_rename(self.issue, recovery_id, self.state)
        self.assertEqual(result.status, "recovery_required")
        self.assertTrue((self.issue / "Beta").is_dir())
        rc, result = self.run_cli("--recover", recovery_id)
        self.assertEqual((rc, result["status"]), (0, "rolled_back"))
        self.assertEqual(tree_hashes(self.issue), before)

    def test_process_death_releases_lock_and_recovery_preserves_occupants(self):
        self.article("Alpha")
        before = tree_hashes(self.issue)
        script = """
import os, sys
from pathlib import Path
from slcn import issueorder
issue, state = map(Path, sys.argv[1:])
plan = issueorder.build_rename_plan(issue, issueorder.OrderInput(("Alpha",), ()))
rename = issueorder.rename_exclusive
def die_after_rename(source, target):
    rename(source, target)
    os._exit(93)
issueorder.rename_exclusive = die_after_rename
issueorder.apply_rename_plan(plan, plan_id=plan.plan_id, state_dir=state)
"""
        child = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.issue), str(self.state)],
            env=self.env, capture_output=True, check=False,
        )
        self.assertEqual(child.returncode, 93, child.stderr.decode())
        self.assertTrue((self.issue / "01 Alpha").is_dir())
        rc, pending = self.run_cli()
        self.assertEqual((rc, pending["status"], pending["recovery_operation"]),
                         (1, "recovery_required", "renamefolders"))
        recovery_id = pending["recovery_id"]
        recovery_path = Path(pending["recovery_path"])
        self.assertTrue(recovery_path.is_relative_to(self.state))
        self.assertTrue(recovery_path.is_file())

        occupant = self.issue / "Alpha"
        occupant.write_bytes(b"external occupant")
        rc, blocked = self.run_cli("--recover", recovery_id)
        self.assertEqual((rc, blocked["status"]), (1, "recovery_required"))
        self.assertEqual(occupant.read_bytes(), b"external occupant")
        self.assertTrue((self.issue / "01 Alpha").is_dir())
        self.assertTrue(recovery_path.is_file())

        occupant.unlink()
        rc, restored = self.run_cli("--recover", recovery_id)
        self.assertEqual((rc, restored["status"]), (0, "rolled_back"))
        self.assertEqual(tree_hashes(self.issue), before)
        self.assertFalse(recovery_path.exists())

    def test_lock_contends_for_same_issue_and_state_must_stay_outside_issue(self):
        from slcn.issuewrite import IssueBusyError, issue_write_lock

        with issue_write_lock(self.issue, self.state):
            with self.assertRaises(IssueBusyError):
                with issue_write_lock(self.issue, self.state):
                    self.fail("second caller acquired live lock")
        with self.assertRaises(OSError):
            with issue_write_lock(self.issue, self.issue / "state"):
                self.fail("state beneath issue was accepted")
        self.assertFalse((self.issue / "state").exists())


if __name__ == "__main__":
    unittest.main()
