"""Bug d regression: L1 recovery must not be guard-blocked inside a manuscript work area.

Only synthetic paths under /tmp/slcn-l1-srcarea-* are created. The real
manuscript repository is never accessed; issue markers are numeric-only.
"""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from slcn.compilefix import files, l1_transaction  # noqa: E402
from slcn.compilefix.files import RepairError  # noqa: E402
from slcn.compilefix.l1 import compile_with_l1  # noqa: E402


SOURCE_COMPONENT = "Studies_in_Logic_Database"
ISSUE_COMPONENT = "2026年第91期"  # numeric-only synthetic issue marker
STEM = "main"
RAW = b"\\documentclass{article}\n\\begin{document}\nSynthetic body.\n\\end{document}\n"


class L1SourceAreaTests(unittest.TestCase):
    """Synthetic AIW delivery directory under a protected source-area component."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slcn-l1-srcarea-", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        repo = Path(self.tmp) / SOURCE_COMPONENT
        issue = repo / ("." + ISSUE_COMPONENT + ".aiw")
        self.work = issue / "task-uuid-1234" / ".aiw" / "delivery-synthetic"
        self.work.mkdir(parents=True)
        for part in (issue, issue / "task-uuid-1234", issue / "task-uuid-1234" / ".aiw", self.work):
            os.chmod(part, 0o700)
        self.source = issue / "source.tex"
        self.source.write_bytes(RAW)
        (self.work / (STEM + ".tex")).write_bytes(RAW)
        self.compiled = False

    def failing_compile(self, out_dir, stem, *, run_xelatex=None):
        """A deterministic fake failure so no TeX engine or API is involved."""
        self.compiled = True
        return False, "编译失败：合成复现（未启动修复）"

    # --- RED: the reported symptom -----------------------------------------

    def test_compile_with_l1_does_not_report_l1_recovery_incomplete(self):
        ok, info, _capture = compile_with_l1(
            self.failing_compile, self.work, STEM, source=self.source)
        self.assertFalse(ok)
        self.assertTrue(self.compiled, "编译根本没有开始，护栏仍在 recover 前拦死")
        self.assertNotIn("L1 恢复未完成", info)
        self.assertIn("编译失败", info)

    def test_recover_without_marker_returns_normally(self):
        log = []
        l1_transaction.recover(self.work, STEM, source=self.source, stage_log=log)
        self.assertEqual(log, ["产物目录", "事务标记"])

    # --- GREEN capability: identity helpers accept explicit opt-in ----------

    def test_directory_id_accepts_opt_in_and_default_stays_strict(self):
        with self.assertRaises(RepairError) as caught:
            files.directory_id(self.work)
        self.assertIn("路径属于受保护源区", str(caught.exception))
        identity = files.directory_id(self.work, allow_source_area=True)
        self.assertEqual(len(identity), 2)

    def test_directory_ancestry_accepts_opt_in_and_default_stays_strict(self):
        with self.assertRaises(RepairError):
            files.directory_ancestry(self.work)
        identities, anchor = files.directory_ancestry(self.work, allow_source_area=True)
        self.assertTrue(identities)
        self.assertIsNotNone(anchor)

    # --- Negative guards that must NOT be weakened --------------------------

    def test_absolute_default_still_rejects_source_area(self):
        with self.assertRaises(RepairError) as caught:
            files.absolute(self.work)
        self.assertIn("路径属于受保护源区", str(caught.exception))

    def test_real_source_file_path_still_rejected(self):
        with self.assertRaises(RepairError):
            files.absolute(self.source)
        with self.assertRaises(RepairError):
            files.directory(self.source.parent)

    def test_private_directory_rejects_wide_permissions_with_opt_in(self):
        private = self.work / "l1-private"
        private.mkdir()
        os.chmod(private, 0o755)
        with self.assertRaises(RepairError) as caught:
            files.directory(private, private=True, allow_source_area=True)
        self.assertIn("权限不得宽于 0700", str(caught.exception))

    def test_private_directory_rejects_foreign_owner_with_opt_in(self):
        private = self.work / "l1-private-owner"
        private.mkdir()
        os.chmod(private, 0o700)
        from unittest.mock import patch
        with patch.object(files.os, "getuid", return_value=os.getuid() + 4242):
            with self.assertRaises(RepairError) as caught:
                files.directory(private, private=True, allow_source_area=True)
        self.assertIn("状态目录须由当前用户拥有", str(caught.exception))

    def test_symlink_rejected_even_with_source_area_opt_in(self):
        neutral = Path(self.tmp) / "neutral"
        neutral.mkdir()
        link = self.work / "linked"
        try:
            link.symlink_to(neutral, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with self.assertRaises(RepairError) as caught:
            files.directory(link, allow_source_area=True)
        self.assertIn("符号链接或非目录", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
