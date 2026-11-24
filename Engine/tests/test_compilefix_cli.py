"""Stage A compile-fix command-line contract."""

import subprocess
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "Engine" / "compilefix.py"


class CompileFixCliTests(unittest.TestCase):
    def test_help_exposes_status_import_and_apply(self):
        result = subprocess.run(
            [sys.executable, "-B", str(CLI), "--help"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for command in ("status", "import", "apply"):
            self.assertRegex(result.stdout, rf"\b{command}\b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
