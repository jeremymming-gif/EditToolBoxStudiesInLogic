"""Source-area guard regression: R3 private recovery may live inside the manuscript repo.

Only synthetic paths under /tmp/slcn-guard-a-* are created; the real manuscript
repository is never accessed. Issue markers are numeric-only synthetic values.
"""

import copy
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from slcn.compilefix import files, l1_r3, l2_r3  # noqa: E402
from slcn.compilefix.files import RepairError, digest  # noqa: E402


SOURCE_COMPONENT = "Studies_in_Logic_Database"
ISSUE_COMPONENT = "2026年第91期"  # numeric-only synthetic issue marker
RAW = b"\\documentclass{article}\n\\begin{document}\nSynthetic body.\n\\end{document}\n"
NEW = RAW.replace(b"Synthetic", b"Repaired")


class SourceAreaGuardTests(unittest.TestCase):
    """Synthetic R3 work directory whose ancestor matches the source-area guard."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slcn-guard-a-", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = Path(self.tmp) / SOURCE_COMPONENT
        self.issue = self.repo / ("." + ISSUE_COMPONENT + ".aiw")
        self.work = self.issue / "task-uuid-1234"
        self.aiw = self.work / ".aiw"
        self.task_id = "synthetic-task"
        self.task = self.aiw / self.task_id
        self.task.mkdir(parents=True)
        os.chmod(self.aiw, 0o700)
        os.chmod(self.task, 0o700)
        self.candidate = self.task / "l1-candidate.tex"
        self.original = self.task / "l1-preview-original.tex"
        self.candidate.write_bytes(NEW)
        self.original.write_bytes(RAW)
        self.before = {"task_id": self.task_id,
                       "preview": {"candidate_text": str(self.candidate),
                                   "text_new_sha256": digest(RAW)}}
        self.after = copy.deepcopy(self.before)
        self.after["preview"]["text_new_sha256"] = digest(NEW)
        self.persisted = copy.deepcopy(self.after)

    def load(self):
        return copy.deepcopy(self.persisted)

    def save(self, value):
        self.persisted = copy.deepcopy(value)

    def write_journal(self, state="syncing"):
        record = {"repair_kind": "L1", "state": state, "task_id": self.task_id,
                  "candidate_name": self.candidate.name,
                  "before": self.before, "after": self.after,
                  "before_sha256": digest(RAW), "after_sha256": digest(NEW)}
        files.put_json(self.aiw / "l1-preview-recovery.json", record)

    # --- RED: the two delivery-path trigger points must stop failing ---------

    def test_recover_preview_accepts_private_work_inside_source_area(self):
        self.write_journal()
        l1_r3.recover_preview(self.work, load_state=self.load, save_state=self.save)
        self.assertEqual(self.candidate.read_bytes(), RAW)
        self.assertEqual(self.persisted, self.before)

    def test_expire_retained_locked_accepts_work_inside_source_area(self):
        compile_fix = self.task / "compile-fix"
        compile_fix.mkdir()
        os.chmod(compile_fix, 0o700)
        state = {"entry": "convert", "conversion": {"stage_dir": "synthetic"},
                 "task_id": self.task_id}
        l2_r3.expire_retained_locked(self.work, state)

    # --- Isolation guards that must NOT be weakened ---------------------------

    def test_absolute_default_still_rejects_source_area(self):
        with self.assertRaises(RepairError) as caught:
            files.absolute(self.aiw)
        self.assertIn("路径属于受保护源区", str(caught.exception))
        # Explicit opt-in is the only way through, and it returns a resolved path.
        resolved = files.absolute(self.aiw, allow_source_area=True)
        self.assertIn(SOURCE_COMPONENT.casefold(), [p.casefold() for p in resolved.parts])

    def test_absolute_default_still_rejects_plain_issue_component(self):
        plain = Path(self.tmp) / ISSUE_COMPONENT / "task"
        plain.mkdir(parents=True)
        with self.assertRaises(RepairError) as caught:
            files.absolute(plain)
        self.assertIn("路径属于受保护源区", str(caught.exception))

    def test_directory_default_still_rejects_source_area(self):
        with self.assertRaises(RepairError) as caught:
            files.directory(self.task)
        self.assertIn("路径属于受保护源区", str(caught.exception))

    def test_private_directory_rejects_wide_permissions_with_opt_in(self):
        compile_fix = self.task / "compile-fix"
        compile_fix.mkdir()
        os.chmod(compile_fix, 0o755)
        with self.assertRaises(RepairError) as caught:
            files.directory(compile_fix, private=True, allow_source_area=True)
        self.assertIn("权限不得宽于 0700", str(caught.exception))

    def test_private_directory_rejects_foreign_owner_with_opt_in(self):
        compile_fix = self.task / "compile-fix"
        compile_fix.mkdir()
        os.chmod(compile_fix, 0o700)
        with patch.object(files.os, "getuid", return_value=os.getuid() + 4242):
            with self.assertRaises(RepairError) as caught:
                files.directory(compile_fix, private=True, allow_source_area=True)
        self.assertIn("状态目录须由当前用户拥有", str(caught.exception))

    def test_directory_rejects_symlink_even_with_source_area_opt_in(self):
        neutral = Path(self.tmp) / "neutral"
        neutral.mkdir()
        link = self.aiw / "linked"
        try:
            link.symlink_to(neutral, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with self.assertRaises(RepairError) as caught:
            files.directory(link, allow_source_area=True)
        self.assertIn("符号链接或非目录", str(caught.exception))

    def test_recover_preview_still_rejects_symlinked_private_root(self):
        shutil.rmtree(self.aiw)
        neutral = Path(self.tmp) / "neutral-aiw"
        neutral.mkdir()
        try:
            self.aiw.symlink_to(neutral, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with self.assertRaises(RepairError) as caught:
            l1_r3.recover_preview(self.work, load_state=self.load, save_state=self.save)
        self.assertIn("符号链接或非目录", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
