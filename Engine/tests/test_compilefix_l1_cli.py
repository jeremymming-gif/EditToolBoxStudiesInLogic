"""Exact ordinary CLI byte contract after whitelist admission and failed recompile."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slcn.compilefix.files import digest

RAW = b"\\documentclass{article}\n\\newcommand{\\Demo}{\\@gobble{discard}Kept}\n\\begin{document}\n\\Demo\n\\end{document}\n"


class CliTests(unittest.TestCase):
    def test_gate_failure_keeps_tail_exit2_source_and_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / "source" / "incoming.tex"
            source.parent.mkdir()
            source.write_bytes(RAW)
            output = root / "out" / "Anonymous"
            env = dict(os.environ, SLCN_COMPILEFIX_STATE_DIR=str(root / "state"))
            script = Path(__file__).parent / "fixtures" / "compilefix_l1" / "l1_cli_entry.py"
            run = subprocess.run([sys.executable, "-B", str(script), "--source", str(source),
                                  "--out", str(output.parent), "--name", "Anonymous", "--lang", "en",
                                  "--compile", "--no-original", "--no-handoff"],
                                 env=env, capture_output=True, timeout=30, check=False)
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertEqual(run.stdout.splitlines(keepends=True)[-1],
                             ("完成。产物目录：" + str(output) + "\n").encode())
            self.assertIn("编译未通过（退出码 2）".encode(), run.stderr)
            self.assertIn(b'"result": "failed-rolled-back"', run.stderr)
            self.assertEqual((output / "Anonymous.tex").read_bytes(), RAW)
            self.assertEqual(digest(source.read_bytes()), digest(RAW))
            self.assertTrue((output / "Anonymous.pdf").exists())
            self.assertTrue((output / "Anonymous.log").exists())


if __name__ == "__main__":
    unittest.main()
