"""Synthetic coverage for the strict same-name sorting draft and its CLI."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
from dirpush import main as dirpush


def _write_tex(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(*args) -> tuple[int, dict, str]:
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = dirpush(["--order-draft", "--json", *map(str, args)])
        except SystemExit as exc:
            code = exc.code
    return code, json.loads(stdout.getvalue()), stderr.getvalue()


def _text_cells(docx: Path) -> list[list[str]]:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(docx) as package:
        assert package.testzip() is None
        root = ET.fromstring(package.read("word/document.xml"))
    return [
        ["".join(t.text or "" for t in cell.findall(".//w:t", ns))
         for cell in row.findall("w:tc", ns)]
        for row in root.findall("./w:body/w:tbl/w:tr", ns)
    ]


class TestIssueOrderDraft(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="slcn-orderdraft-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.issue = self.root / "2026年第3期(101)"
        self.issue.mkdir()
        self.out = self.root / "external"

    def _make_alpha(self):
        _write_tex(self.issue / "Alpha/Alpha.tex", r"""
% \newcommand{\mytitleCN}{Commented}
\newcommand{\mytitle}{Generic title}
\newcommand{\mytitleCN}{合成 \edash 题名}
\newcommand{\myfirstauthorCN}{测试甲}
\newcommand{\mysecondauthorCN}{null}
\newcommand{\mysecondauthor}{Author Two}
\newcommand{\myfirstaffiliationCN}{Never include affiliation}
\newcommand{\myabstractCN}{Never include abstract}
\input{appendix}
""")

    def test_preview_and_docx_read_only_exact_regular_tex(self):
        self._make_alpha()
        _write_tex(self.issue / "Alpha/appendix.tex", "Never read supplement")
        _write_tex(self.issue / "Alpha/Alpha.docx", "Never read Word")
        _write_tex(self.issue / "Beta/BETA.tex", "Never read wrong case")
        _write_tex(self.issue / "Delta/Delta.tex", r"""
\newcommand{\mytitle}{Synthetic English title}
\newcommand{\myfirstauthor}{Sample Author}
""")
        _write_tex(self.issue / "Epsilon/Epsilon.tex", r"\newcommand{\mytitleCN}{无署名合成稿}")
        _write_tex(self.issue / "Gamma/appendix.tex", "Never open other TeX")
        _write_tex(self.issue / "Gamma/original/Gamma.tex", "Never open source TeX")
        _write_tex(self.issue / "original/original.tex", "Never open original/ directory")
        _write_tex(self.issue / ".toolbox-tmp/.toolbox-tmp.tex", "Never open hidden workspace")
        _write_tex(self.issue / "Theta/Theta.TEX", "Never read wrong extension spelling")
        (self.issue / "Kappa/Kappa.tex").mkdir(parents=True)
        (self.issue / "Zeta").mkdir()
        (self.issue / "Zeta/Zeta.tex").write_bytes(b"\xff")
        contents = {p: p.read_bytes() for p in self.issue.rglob("*") if p.is_file()}
        opens = []
        writes = []
        recording = True
        pinned_dir = None

        def audit(event, args):
            nonlocal pinned_dir
            if not recording:
                return
            if event == "open" and not isinstance(args[0], int):
                raw_path = Path(args[0])
                path = Path(os.path.abspath(raw_path))
                if path.is_relative_to(self.issue) and path.is_dir():
                    pinned_dir = path
                elif (not raw_path.is_absolute() and raw_path.suffix == ".tex"
                      and pinned_dir is not None):
                    opens.append((pinned_dir / raw_path).relative_to(self.issue).as_posix())
                elif path.is_relative_to(self.issue) and path.is_file():
                    opens.append(path.relative_to(self.issue).as_posix())
                if path.is_relative_to(self.root) and args[2] & (
                        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                    writes.append((event, path))
            elif event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir"}:
                path = Path(os.path.abspath(args[0]))
                if path.is_relative_to(self.root):
                    writes.append((event, path))

        sys.addaudithook(audit)
        try:
            rc, preview, _ = _run("--issue", self.issue, "--out", self.out, "--dry-run")
        finally:
            recording = False
        self.assertEqual(rc, 0, preview)
        self.assertEqual(preview["schema"], 1)
        self.assertEqual(preview["operation"], "order_draft")
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["issue"], str(self.issue))
        self.assertEqual(opens, ["Alpha/Alpha.tex", "Delta/Delta.tex",
                                 "Epsilon/Epsilon.tex", "Zeta/Zeta.tex"])
        self.assertEqual(writes, [])
        self.assertFalse(self.out.exists())
        self.assertFalse((self.issue / "000 Table of Contents").exists())
        self.assertEqual(preview["rows"], [
            {"folder": "Alpha", "title": "合成 - 题名", "authors": ["测试甲", "Author Two"], "state": "ok"},
            {"folder": "Beta", "title": "", "authors": [], "state": "missing_tex"},
            {"folder": "Delta", "title": "Synthetic English title",
             "authors": ["Sample Author"], "state": "ok"},
            {"folder": "Epsilon", "title": "无署名合成稿", "authors": [], "state": "missing_field"},
            {"folder": "Gamma", "title": "", "authors": [], "state": "missing_tex"},
            {"folder": "Kappa", "title": "", "authors": [], "state": "unsafe_entry"},
            {"folder": "Theta", "title": "", "authors": [], "state": "missing_tex"},
            {"folder": "Zeta", "title": "", "authors": [], "state": "unreadable"},
        ])
        for folder in ("Beta", "Epsilon", "Gamma", "Kappa", "Theta", "Zeta"):
            self.assertTrue(any(folder in item for item in preview["warnings"]), preview)

        rc, completed, _ = _run("--issue", self.issue, "--out", self.out)
        self.assertEqual(rc, 0, completed)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["rows"], preview["rows"])
        self.assertEqual(completed["docx"], preview["docx"])
        docx = Path(completed["docx"])
        self.assertEqual(docx.parent, self.out)
        self.assertEqual(list(self.out.iterdir()), [docx])
        self.assertEqual(_text_cells(docx), [
            ["题名", "作者"],
            ["合成 - 题名", "测试甲，Author Two"],
            ["Synthetic English title", "Sample Author"],
            ["无署名合成稿", ""],
        ])
        first = docx.read_bytes()
        self.assertEqual(_run("--issue", self.issue, "--out", self.out)[0], 0)
        self.assertEqual(docx.read_bytes(), first)
        self.assertEqual({p: p.read_bytes() for p in self.issue.rglob("*") if p.is_file()}, contents)

    def test_candidates_report_unsafe_teX_without_reading_it(self):
        external = self.root / "external.tex"
        _write_tex(external, r"\newcommand{\mytitleCN}{不得读取}")
        hard_dir = self.issue / "Hard"
        hard_dir.mkdir()
        os.link(external, hard_dir / "Hard.tex")
        self._make_alpha()
        _write_tex(self.issue / "000 Table of Contents/000 Table of Contents.tex", "reserved")
        _write_tex(self.issue / "000 目录/000 目录.tex", "legacy contents")
        _write_tex(self.issue / "000旧目录/000旧目录.tex", "legacy contents without space")
        opens = []
        recording = True
        pinned_dir = None

        def audit(event, args):
            nonlocal pinned_dir
            if recording and event == "open" and not isinstance(args[0], int):
                raw_path = Path(args[0])
                path = Path(os.path.abspath(raw_path))
                if path.is_relative_to(self.issue) and path.is_dir():
                    pinned_dir = path
                elif (not raw_path.is_absolute() and raw_path.suffix == ".tex"
                      and pinned_dir is not None):
                    opens.append(pinned_dir / raw_path)
                elif path.is_relative_to(self.root) and path.is_file():
                    opens.append(path)

        sys.addaudithook(audit)
        try:
            from slcn.orderdraft import list_order_candidates
            candidates = list_order_candidates(self.issue)
            self.assertEqual(opens, [], "候选只允许读文件元数据")
            self.assertEqual([(c.folder, c.state) for c in candidates],
                             [("Alpha", "ok"), ("Hard", "unsafe_entry")])
            rc, result, _ = _run("--issue", self.issue, "--out", self.out, "--dry-run")
        finally:
            recording = False
        self.assertEqual(rc, 0, result)
        self.assertEqual(opens, [self.issue / "Alpha/Alpha.tex"])
        self.assertEqual(result["rows"][1]["state"], "unsafe_entry")

    def test_linked_tex_and_linked_article_are_not_sources(self):
        outside = self.root / "outside"
        _write_tex(outside / "Linked/Linked.tex", r"\newcommand{\mytitleCN}{不得读取}")
        (self.issue / "SourceLink").mkdir()
        try:
            (self.issue / "SourceLink/SourceLink.tex").symlink_to(outside / "Linked/Linked.tex")
        except OSError:
            pass  # Windows may disable symbolic links. Its junction is covered below.
        linked = self.issue / "Linked"
        if os.name == "nt":
            junction = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(outside / "Linked")],
                                      capture_output=True)
            self.assertEqual(junction.returncode, 0, junction.stderr)
        else:
            linked.symlink_to(outside / "Linked", target_is_directory=True)
        self._make_alpha()
        rc, result, _ = _run("--issue", self.issue, "--out", self.out, "--dry-run")
        self.assertEqual(rc, 0, result)
        states = {row["folder"]: row["state"] for row in result["rows"]}
        self.assertEqual(states["Linked"], "unsafe_entry")
        if (self.issue / "SourceLink/SourceLink.tex").is_symlink():
            self.assertEqual(states["SourceLink"], "unsafe_entry")

    def test_rejects_inside_output_and_output_alias_even_in_preview(self):
        self._make_alpha()
        protected = self.issue / "000 Table of Contents"
        protected.mkdir()
        alias = self.root / "alias"
        if os.name == "nt":
            junction = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(protected)],
                                      capture_output=True)
            self.assertEqual(junction.returncode, 0, junction.stderr)
        else:
            alias.symlink_to(protected, target_is_directory=True)
        for path in (self.issue, protected, alias / "nested"):
            for mode in ([], ["--dry-run"]):
                with self.subTest(path=path, mode=mode):
                    rc, result, _ = _run("--issue", self.issue, "--out", path, *mode)
                    self.assertEqual(rc, 2, result)
                    self.assertEqual(result["status"], "blocked")
                    self.assertIsNone(result["docx"])
                    self.assertEqual(list(protected.iterdir()), [])

    def test_blocked_inputs_return_diagnostics_without_writes(self):
        _write_tex(self.issue / "Beta/BETA.tex", "wrong spelling")
        base = ["--issue", self.issue, "--out", self.out]
        for argv in (base, base + ["--annual"], base + ["--check-out-root", self.out],
                     base + ["--with-en-toc"], base + ["--year", "2026"],
                     ["--issue", self.issue], ["--out", self.out],
                     ["--issue", self.root / "absent", "--out", self.out]):
            with self.subTest(argv=argv):
                rc, result, _ = _run(*argv)
                self.assertEqual(rc, 2, result)
                self.assertEqual(result["operation"], "order_draft")
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(result["error"], result)
                self.assertIsNone(result["docx"])
                self.assertFalse(self.out.exists())
        _, result, _ = _run(*base)
        self.assertEqual([row["state"] for row in result["rows"]], ["missing_tex"])
        self.assertTrue(any("Beta" in warning for warning in result["warnings"]), result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
