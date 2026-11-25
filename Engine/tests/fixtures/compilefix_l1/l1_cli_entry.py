"""Synthetic CLI failure after an admitted L1 rule, with compiler timeout injection."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compilefix"))
import convert_entry
sys.path.insert(0, str(convert_entry.ENGINE))
LOG = b"! You can't use `\\spacefactor' in vertical mode.\nl.4 \\Demo\n"


def fail(out_dir, stem, *, run_xelatex=None):
    if run_xelatex:
        def runner(command, *, cwd, quiet, check, stdout=None):
            if stdout is not None:
                stdout.write(LOG)
            return SimpleNamespace(returncode=1)
        run_xelatex(runner, ["xelatex", stem + ".tex"], out_dir, 1)
    (Path(out_dir) / (stem + ".log")).write_bytes(LOG)
    return False, "xelatex 第 1 步失败（退出码 1）：synthetic catcode failure"


convert_entry.fail_compilation = fail
with patch("slcn.compilefix.l1_transaction.compile_candidate", side_effect=subprocess.TimeoutExpired("synthetic", 1)):
    raise SystemExit(convert_entry.main())
