"""L1 failure injection, freshness, rollback and a real offline XeLaTeX repair."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slcn.compilefix import compile_with_capture, compile_with_l1
from slcn.compilefix.compiler import CompileResult
from slcn.compilefix.files import digest, read_json, RepairError
from slcn.compilefix.l1_diagnostics import diagnose
from slcn.compilefix.l1_rules import select_plan
from slcn.compilefix.l1_transaction import apply_plan, recover, RecoveryRequired
from slcn.compilefix.state import capture_compile
from slcn import convert_compile as cc

RAW = b"\\documentclass{article}\n\\newcommand{\\Demo}{\\@gobble{discard}Kept}\n\\begin{document}\n\\Demo\n\\end{document}\n"
LOG = b"! You can't use `\\spacefactor' in vertical mode.\n\\Demo ->\\@\nl.4 \\Demo\n"


def pdf_bytes():
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "source").mkdir()
        self.source = self.root / "source" / "incoming.tex"
        self.source.write_bytes(RAW)
        self.out = self.root / "out"
        self.out.mkdir()
        self.tex, self.pdf = self.out / "demo.tex", self.out / "demo.pdf"
        self.tex.write_bytes(RAW)
        self.pdf.write_bytes(b"OLD-PDF")
        (self.out / "demo.log").write_bytes(LOG)
        self.before = self.snapshot()
        self.capture = capture_compile(self.out, "demo", source=self.source)
        self.capture["failure"] = ("c" * 32, 1, 1, LOG)
        self.report = diagnose(RAW, self.capture)
        self.plan = select_plan(RAW, self.report)
        self.assertIsNotNone(self.plan)

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.out.iterdir() if p.is_file()}

    def compiler(self, cwd, stem, expected, support, private, **kwargs):
        self.assertFalse((cwd / (stem + ".pdf")).exists(), "old PDF must never enter isolation")
        pdf, log, recorder = pdf_bytes(), b"fresh synthetic log", b"fresh recorder"
        for ext, data in ((".pdf", pdf), (".log", log), (".fls", recorder)):
            (cwd / (stem + ext)).write_bytes(data)
        return CompileResult("passed", expected, "f" * 32, "bind", 0,
                             digest(pdf), digest(log), digest(recorder))

    def apply(self, **kwargs):
        return apply_plan(self.out, "demo", source=self.source, capture=self.capture,
                          plan=self.plan, report=self.report, compiler=kwargs.pop("compiler", self.compiler), **kwargs)

    def test_success_publishes_exact_tex_and_readable_fresh_pdf(self):
        self.assertEqual(self.apply(), str(self.pdf))
        self.assertEqual(self.tex.read_bytes(), self.plan.patched)
        self.assertTrue(self.pdf.read_bytes().startswith(b"%PDF-"))
        self.assertEqual(self.source.read_bytes(), RAW)
        self.assertEqual((self.out / "demo.log").read_bytes(), LOG)

    def test_each_publication_fault_restores_original_bytes(self):
        for point in ("applied", "verified", "published-tex", "published-pdf"):
            with self.subTest(point=point):
                def fault(phase):
                    if phase == point:
                        raise OSError("injected")
                with self.assertRaises(OSError):
                    self.apply(checkpoint=fault)
                self.assertEqual(self.snapshot(), self.before)
                # Rollback can replace an inode, so bind this next independent attempt afresh.
                self.capture.update(capture_compile(self.out, "demo", source=self.source))

    def test_nonzero_timeout_and_exception_never_touch_outputs(self):
        def failed(*args, **kwargs):
            return CompileResult("failed", self.plan.after_sha256, "d" * 32, "xelatex-1", 1)
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("synthetic", 1)
        def crashed(*args, **kwargs):
            raise RuntimeError("rule/compiler fault")
        for compiler in (failed, timeout, crashed):
            with self.subTest(compiler=compiler.__name__), self.assertRaises((RepairError, subprocess.TimeoutExpired, RuntimeError)):
                self.apply(compiler=compiler)
            self.assertEqual(self.snapshot(), self.before)

    def test_old_absent_bad_unreadable_and_mismatched_pdf_are_rejected(self):
        for mode in ("absent", "bad", "empty", "mismatch"):
            def compiler(*args, **kwargs):
                result = self.compiler(*args, **kwargs)
                pdf = args[0] / "demo.pdf"
                if mode == "absent":
                    pdf.unlink()
                else:
                    pdf.write_bytes({"bad": b"%PDF-1.4\nbroken\n%%EOF", "empty": b"", "mismatch": pdf_bytes() + b"x"}[mode])
                return result
            with self.subTest(mode=mode), self.assertRaises((OSError, RepairError)):
                self.apply(compiler=compiler)
            self.assertEqual(self.snapshot(), self.before)

    def test_candidate_change_during_isolation_is_preserved(self):
        def checkpoint(phase):
            if phase == "verified":
                self.tex.write_bytes(b"external edit")
        with self.assertRaises(RepairError):
            self.apply(checkpoint=checkpoint)
        self.assertEqual(self.tex.read_bytes(), b"external edit")
        self.assertEqual(self.pdf.read_bytes(), b"OLD-PDF")

    def test_original_pdf_change_during_isolation_is_preserved(self):
        def checkpoint(phase):
            if phase == "verified":
                self.pdf.write_bytes(b"external PDF")
        with self.assertRaises(RepairError):
            self.apply(checkpoint=checkpoint)
        self.assertEqual(self.tex.read_bytes(), RAW)
        self.assertEqual(self.pdf.read_bytes(), b"external PDF")

    def test_dependency_change_during_isolation_is_preserved(self):
        bib = self.out / "reference.bib"
        bib.write_bytes(b"before")
        def checkpoint(phase):
            if phase == "verified":
                bib.write_bytes(b"external")
        with self.assertRaises(RepairError):
            self.apply(checkpoint=checkpoint)
        self.assertEqual(self.tex.read_bytes(), RAW)
        self.assertEqual(bib.read_bytes(), b"external")

    def test_modified_isolated_tex_or_dependency_never_promotes(self):
        for mode in ("tex", "dependency"):
            def compiler(*args, **kwargs):
                result = self.compiler(*args, **kwargs)
                (args[0] / ("demo.tex" if mode == "tex" else "extra.bib")).write_bytes(b"changed")
                return result
            with self.subTest(mode=mode), self.assertRaises(RepairError):
                self.apply(compiler=compiler)
            self.assertEqual(self.snapshot(), self.before)

    def test_symlink_and_shared_inode_are_refused(self):
        link = self.out / "reference.bib"
        link.symlink_to(self.source)
        with self.assertRaises(RepairError):
            self.apply()
        link.unlink()
        os.link(self.source, link)
        with self.assertRaises(RepairError):
            self.apply()
        self.assertEqual(self.source.read_bytes(), RAW)

    def test_external_replacement_during_publish_blocks_rollback(self):
        def checkpoint(phase):
            if phase == "published-tex":
                self.tex.write_bytes(b"external after publication")
                raise OSError("interrupted")
        with self.assertRaises(RecoveryRequired):
            self.apply(checkpoint=checkpoint)
        self.assertEqual(self.tex.read_bytes(), b"external after publication")
        self.assertEqual(self.pdf.read_bytes(), b"OLD-PDF")
        with self.assertRaises(RepairError):
            recover(self.out, "demo", source=self.source)

    def test_pdf_rollback_failure_blocks_success(self):
        from slcn.compilefix import l1_transaction as tx
        with patch.object(tx, "_rollback", side_effect=OSError("injected restore failure")):
            def checkpoint(phase):
                if phase == "published-tex":
                    raise OSError("fail")
            with self.assertRaises(RecoveryRequired):
                self.apply(checkpoint=checkpoint)
        self.assertTrue((self.out / ".compilefix-demo.transaction.json").is_file())
        recover(self.out, "demo", source=self.source)
        self.assertEqual(self.snapshot(), self.before)

    def test_process_exit_during_native_swap_recovers_original_pair(self):
        child = r"""
import sys, os
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from test_compilefix_l1_transaction import TransactionTests, RAW, LOG
from slcn.compilefix.state import capture_compile
from slcn.compilefix.l1_diagnostics import diagnose
from slcn.compilefix.l1_rules import select_plan
from slcn.compilefix import atomicfiles
root=Path(sys.argv[1])
case=TransactionTests('runTest')
case.root=root; case.out=root/'out'; case.source=root/'source'/'incoming.tex'
case.capture=capture_compile(case.out,'demo',source=case.source)
case.capture['failure']=('c'*32,1,1,LOG)
case.report=diagnose(RAW,case.capture); case.plan=select_plan(RAW,case.report)
exchange=atomicfiles.exchange
calls=0
def crash(target, staged):
    global calls
    exchange(target,staged)
    if target.name=='demo.tex':
        os._exit(93)
atomicfiles.exchange=crash
case.apply()
"""
        run = subprocess.run([sys.executable, "-c", child, str(self.root), str(Path(__file__).parent)],
                             capture_output=True, timeout=20, check=False)
        self.assertEqual(run.returncode, 93, run.stdout + run.stderr)
        self.assertEqual(self.tex.read_bytes(), self.plan.patched)
        recover(self.out, "demo", source=self.source)
        self.assertEqual(self.snapshot(), self.before)

    def test_real_offline_compile_repair(self):
        if shutil.which("xelatex") is None or shutil.which("biber") is None:
            self.skipTest("BLOCKED: XeLaTeX/Biber unavailable")
        ok, info, capture = compile_with_l1(cc.compile_pdf, str(self.out), "demo", source=str(self.source))
        self.assertTrue(ok, info)
        self.assertEqual(self.tex.read_bytes(), self.plan.patched)
        self.assertEqual(capture["l1_repaired"], digest(self.tex.read_bytes()))
        self.assertEqual(self.source.read_bytes(), RAW)
        from pypdf import PdfReader
        self.assertEqual(len(PdfReader(str(self.pdf)).pages), 1)

    def test_detailed_and_tuple_share_executor_and_original_messages(self):
        from types import SimpleNamespace
        with patch.object(cc.shutil, "which", return_value="fake"), patch("slcn.platform.run_no_window", return_value=SimpleNamespace(returncode=1)):
            detail = cc.compile_pdf_detailed(str(self.out), "demo")
            old = cc.compile_pdf(str(self.out), "demo")
        self.assertEqual(old, detail.legacy())
        self.assertEqual(detail.phase, "xelatex-1")
        self.assertEqual(detail.returncode, 1)
        self.assertEqual(detail.candidate_sha256, digest(RAW))
        self.assertTrue(detail.identity)


if __name__ == "__main__":
    unittest.main()
