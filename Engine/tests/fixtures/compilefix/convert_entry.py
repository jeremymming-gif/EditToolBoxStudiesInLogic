"""Execute convert.main with offline, synthetic build/first-compile adapters."""

import os
import shutil
import sys
import unicodedata
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cli_fixture import BODY_LINE, ENGINE


def build_synthetic(source, out_dir, *, basename, **options):
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output / f"{basename}.tex")
    (output / f"{basename}.pdf").write_bytes(b"%PDF-1.4\nSynthetic original PDF bytes\n")
    if os.environ.get("COMPILEFIX_FIXTURE_UNBOUND_LOG") == "1":
        (output / f"{basename}.log").write_bytes(b"Old synthetic log before compile capture.\n")
    return SimpleNamespace(warnings=[])


def fail_compilation(out_dir, stem, *, run_xelatex=None):
    message = "xelatex 第 1 步失败（退出码 1）：Misplaced alignment tab character &."
    log = Path(out_dir) / f"{stem}.log"
    if os.environ.get("COMPILEFIX_FIXTURE_UNBOUND_LOG") == "1":
        log.write_bytes(f"{stem}.tex:3: Misplaced alignment tab character &.\n".encode())
        return False, message

    diagnostic = (f"! Misplaced alignment tab character &.\nl.3 {BODY_LINE}\n").encode()
    if run_xelatex is not None:
        def synthetic_runner(command, *, cwd, quiet, check, stdout=None):
            if stdout is not None:
                stdout.write(diagnostic)
            return SimpleNamespace(returncode=1)

        run_xelatex(synthetic_runner, ["xelatex", f"{stem}.tex"], out_dir, 1)
    log.write_bytes(diagnostic)
    return False, message


def main():
    if os.environ.get("COMPILEFIX_FIXTURE_CRLF") == "1":
        sys.stdout.reconfigure(newline="\r\n")
    protected_dir = os.environ.get("COMPILEFIX_FIXTURE_FORBID_MKDIR")
    if protected_dir is not None:
        protected_dir = unicodedata.normalize("NFC", protected_dir)

        def reject_source_mkdir(event, args):
            if event == "os.mkdir" and unicodedata.normalize("NFC", os.fsdecode(args[0])) == protected_dir:
                os._exit(93)

        sys.addaudithook(reject_source_mkdir)
    sys.path.insert(0, str(ENGINE))
    import convert

    selection = SimpleNamespace(sty="synthetic.sty", skeleton="synthetic.tex")
    verify_result = {"taskbook_path": "", "bib_keys": [], "bibdb": {}}
    compiler = (nullcontext() if os.environ.get("COMPILEFIX_FIXTURE_REAL_COMPILE") == "1"
                else patch.object(convert, "compile_pdf", side_effect=fail_compilation))
    epoch = os.environ.get("COMPILEFIX_FIXTURE_EPOCH")
    clock = nullcontext() if epoch is None else patch("time.time", return_value=float(epoch))
    with (
        clock,
        patch.object(convert.T, "resolve", return_value=selection),
        patch.object(convert.T, "validate", return_value=[]),
        patch.object(convert.T, "describe", return_value="synthetic fixture"),
        patch.object(convert, "build_latex", side_effect=build_synthetic),
        compiler,
        patch.object(convert, "bibdb_readonly_scan", return_value={}),
        patch.object(convert, "run_verify_refs", return_value=verify_result),
        patch.object(convert, "run_coop_taskbook"),
    ):
        return convert.main()


if __name__ == "__main__":
    raise SystemExit(main())
