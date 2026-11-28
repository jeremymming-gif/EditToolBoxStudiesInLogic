"""整期刷新事务以目标哈希、实际合刊映射及恢复记录为验收依据。"""
import json
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from slcn import issuepaging_commit as commit
except ImportError:
    commit = None


class PowerLoss(BaseException):
    pass


class Publication(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(commit, "缺少可恢复的页码发布")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.issue = self.root / "issue"
        self.state = self.root / "state"
        self.run = self.state / "runs" / uuid.uuid4().hex
        self.run.mkdir(parents=True)
        self.article = self.issue / "01 Sample"
        self.article.mkdir(parents=True)
        self.paths = ["01 Sample/Sample.tex", "01 Sample/Sample.pdf",
                      "000 Contents/contents.pdf"]
        self.inputs = {}
        self.expected = {}
        for index, rel in enumerate(self.paths):
            dest = self.issue / rel
            if index < 2:
                dest.write_text(f"old {index}")
            candidate = self.run / f"new-{index}"
            candidate.write_text(f"new {index}")
            self.inputs[rel] = candidate
            self.expected[rel] = commit.target_stamp(self.issue, rel)

    def publish(self):
        return commit.publish(self.issue, self.state, self.run,
                              self.inputs, self.expected, selection=None,
                              verify=lambda: None)

    def interrupt_at(self, index, exc):
        original = commit._install
        def replace(source, destination, exists):
            if Path(destination) == self.issue / self.paths[index]:
                raise exc
            return original(source, destination, exists)
        return patch.object(commit, "_install", replace)

    def old(self):
        self.assertEqual((self.issue / self.paths[0]).read_text(), "old 0")
        self.assertEqual((self.issue / self.paths[1]).read_text(), "old 1")
        self.assertFalse((self.issue / self.paths[2]).parent.exists())

    def test_toc_failure_restores_article_tex_and_pdf(self):
        with self.interrupt_at(2, OSError("注入目录落位失败")):
            with self.assertRaises(commit.RolledBack):
                self.publish()
        self.old()
        self.assertFalse(commit._record_path(self.issue, self.state).exists())

    def test_process_exit_recovery_refuses_external_edit_and_wrong_id(self):
        with self.interrupt_at(1, PowerLoss()):
            with self.assertRaises(PowerLoss):
                self.publish()
        path = self.issue / self.paths[0]
        self.assertEqual(path.read_text(), "new 0")
        record = commit._record_path(self.issue, self.state)
        self.assertEqual(json.loads(record.read_text())["recovery_id"], self.run.name)
        with self.assertRaises(ValueError):
            commit.recover(self.issue, self.state, "bad")
        path.write_text("external")
        with self.assertRaises(commit.RecoveryRequired):
            commit.recover(self.issue, self.state, self.run.name)
        self.assertEqual(path.read_text(), "external")
        self.assertTrue(record.is_file())
        path.write_text("new 0")
        self.assertEqual(commit.issuewrite.pending_issue_write(self.issue, self.state)["operation"],
                         "refreshpages")
        commit.recover(self.issue, self.state, self.run.name)
        self.old()
        self.assertFalse(record.exists())

    def test_changed_target_or_parent_link_prevents_publication(self):
        (self.issue / self.paths[1]).write_text("external")
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual((self.issue / self.paths[0]).read_text(), "old 0")
        self.article.rename(self.root / "moved")
        self.article.symlink_to(self.root / "moved", target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.publish()
        self.assertEqual((self.root / "moved/Sample.pdf").read_text(), "external")

    def test_commit_checks_disk_and_removes_only_its_pending_record(self):
        self.publish()
        self.assertEqual([(self.issue / rel).read_text() for rel in self.paths],
                         ["new 0", "new 1", "new 2"])
        self.assertFalse(commit._record_path(self.issue, self.state).exists())

    def test_foreign_file_with_candidate_bytes_is_preserved(self):
        with self.interrupt_at(1, PowerLoss()), self.assertRaises(PowerLoss):
            self.publish()
        target = self.issue / self.paths[0]
        other = target.with_name("editor-copy")
        other.write_bytes(target.read_bytes())
        os.replace(other, target)
        with self.assertRaises(commit.RecoveryRequired):
            commit.recover(self.issue, self.state, self.run.name)
        self.assertEqual(target.read_text(), "new 0")

    def test_new_toc_directory_can_recover_between_rename_and_journal_update(self):
        original = commit.rename_exclusive
        def stop_after_move(source, target):
            original(source, target)
            if Path(target) == (self.issue / self.paths[2]).parent:
                raise PowerLoss
        with patch.object(commit, "rename_exclusive", stop_after_move), self.assertRaises(PowerLoss):
            self.publish()
        commit.recover(self.issue, self.state, self.run.name)
        self.old()

    def test_new_toc_unrecorded_directory_stays_outside_issue(self):
        original = Path.mkdir
        def stop_after_mkdir(path, *args, **kwargs):
            original(path, *args, **kwargs)
            if path.name.startswith(".slcn-dir-"):
                raise PowerLoss
        with patch.object(Path, "mkdir", stop_after_mkdir), self.assertRaises(PowerLoss):
            self.publish()
        self.assertFalse(list(self.issue.glob(".slcn-dir-*")))
        self.assertEqual(len(list(self.run.glob(".slcn-dir-*"))), 1)
        commit.recover(self.issue, self.state, self.run.name)
        self.old()
        self.assertFalse(commit._record_path(self.issue, self.state).exists())

    def test_live_refresh_record_blocks_other_issue_writers(self):
        with self.interrupt_at(1, PowerLoss()):
            with self.assertRaises(PowerLoss):
                self.publish()
        pending = commit.issuewrite.pending_issue_write(self.issue, self.state)
        self.assertEqual(pending["operation"], "refreshpages")
        self.assertEqual(pending["recovery_id"], self.run.name)

    def test_merge_cli_refuses_to_read_issue_with_pending_refresh(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import mergepdf

        identity = commit.issuewrite.issue_identity(self.issue)
        record = commit.issuewrite.record_path(identity, self.state)
        commit.issuewrite.write_record(record, {
            "schema": 1, "operation": "refreshpages", "recovery_id": self.run.name,
            "issue": identity,
        })
        previous = os.environ.get("SLCN_ISSUE_STATE_DIR")
        os.environ["SLCN_ISSUE_STATE_DIR"] = str(self.state)
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                code = mergepdf.main(["--folder", str(self.issue)])
        finally:
            if previous is None:
                os.environ.pop("SLCN_ISSUE_STATE_DIR", None)
            else:
                os.environ["SLCN_ISSUE_STATE_DIR"] = previous
        self.assertEqual(code, 2)
        self.assertIn("refreshpages", stderr.getvalue())


class ManualPlan(unittest.TestCase):
    def setUp(self):
        import refreshpages as refresh
        self.refresh = refresh
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.issue = self.root / "issue"
        self.state = self.root / "state"
        self.folder = self.issue / "01 Sample"
        self.folder.mkdir(parents=True)
        self.tex = self.folder / "Sample.tex"
        self.tex.write_text(
            r"\newcommand{\myfirstpage}{1}\newcommand{\mylastpage}{2}\newcommand{\mypages}{2}"
            r"\newcommand{\mytitle}{甲}\newcommand{\mytitleEN}{Example}"
            r"\newcommand{\myfirstauthor}{乙}\newcommand{\myfirstauthorEN}{Example}"
        )
        env = patch.dict(os.environ, {"SLCN_ISSUE_STATE_DIR": str(self.state)})
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            rc = self.refresh.main(["--issue", str(self.issue), "--json", *args])
        return rc, json.loads(stream.getvalue())

    def test_preview_binds_targets_dependencies_and_options_without_compiling_or_writing(self):
        with patch.object(self.refresh.issuepaging, "prepare_articles", side_effect=AssertionError):
            rc, initial = self.cli()
            self.assertEqual(rc, 0, initial)
            self.assertIsNone(initial["articles"][0]["new"])
            self.assertEqual(initial["plan_id"], self.cli()[1]["plan_id"])
            self.assertFalse(self.state.exists())
            self.assertEqual(set(self.issue.iterdir()), {self.folder})
            self.assertNotEqual(initial["plan_id"], self.cli("--regenerate-toc")[1]["plan_id"])
            (self.folder / "reference.bib").write_text("changed")
            rc, result = self.cli("--apply", "--plan-id", initial["plan_id"])
            self.assertEqual((rc, result["status"]), (2, "blocked"))
            self.assertFalse((self.state / "runs").exists())

    def test_external_edit_during_compile_is_preserved_and_diagnosed(self):
        from types import SimpleNamespace
        plan = self.refresh.build_refresh_plan(self.issue, state_dir=self.state)
        old = self.tex.read_text()
        def compile_stub(*args, **kwargs):
            self.tex.write_text(old + "%editor")
            return SimpleNamespace(articles=[], page_ranges=[])
        with patch.object(self.refresh.issuepaging, "prepare_articles", compile_stub), \
             patch.object(self.refresh.issuepaging_toc, "stage_issue_toc", return_value=None):
            rc, result = self.cli("--apply", "--plan-id", plan.plan_id)
        self.assertEqual(rc, 1)
        self.assertEqual(self.tex.read_text(), old + "%editor")
        self.assertTrue((Path(result["diagnostic_dir"]) / "error.txt").is_file())
        self.assertFalse((self.folder / "Sample.pdf").exists())

    def test_order_result_and_persisted_selection_are_bound_to_current_issue(self):
        from pypdf import PdfWriter, PdfReader
        import mergepdf
        aux = self.issue / "99 Auxiliary"
        aux.mkdir()
        writer = PdfWriter()
        writer.add_blank_page(100, 100)
        writer.write(self.folder / "Sample.pdf")
        writer.write(aux / "Auxiliary.pdf")
        rc, initial = self.cli()
        self.assertEqual(rc, 2, initial)
        path = self.root / "order.json"
        order = dict(schema=1, operation="renamefolders", issue=commit.issuewrite.issue_identity(self.issue),
                     status="completed", renames=[dict(old="Sample", new="01 Sample", main_tex="Sample.tex")],
                     excluded=[aux.name], directories=commit.numbered_directories(self.issue))
        path.write_text(json.dumps(order))
        plan = self.refresh.build_refresh_plan(self.issue, state_dir=self.state, order_result=path)
        self.assertEqual(plan.excluded, (aux.name,))
        with patch("sys.stdin", io.StringIO(json.dumps(order))):
            rc, preview = self.cli("--order-result", "-")
        self.assertEqual(rc, 0, preview)
        with patch("sys.stdin", io.StringIO(json.dumps(order, indent=1))):
            rc, stale = self.cli("--apply", "--plan-id", preview["plan_id"], "--order-result", "-")
        self.assertEqual(rc, 2, stale)
        run = self.state / "runs" / uuid.uuid4().hex
        run.mkdir(parents=True)
        commit.publish(self.issue, self.state, run, {}, {}, selection=plan.selection, verify=lambda: None)
        merged = self.root / "merge.pdf"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mergepdf.main(["--folder", str(self.issue), "--out", str(merged)]), 0)
        self.assertEqual(len(PdfReader(merged).pages), 1)
        aux.rename(self.root / "previous")
        aux.mkdir()
        with self.assertRaisesRegex(ValueError, "编号目录"):
            commit.merge_exclusions(self.issue, self.state)
        rc, stale = self.cli("--order-result", str(path))
        self.assertEqual(rc, 2, stale)
        self.assertIn("目录身份", stale["error"])
        order["issue"] = {}
        path.write_text(json.dumps(order))
        rc, error = self.cli("--order-result", str(path))
        self.assertEqual(rc, 2, error)
        self.assertIn("本期", error["error"])

    def test_spaced_directory_uses_shared_body_pdf_resolution(self):
        from pypdf import PdfReader, PdfWriter
        import mergepdf
        folder = self.issue / "01 Jane Doe"
        self.folder.rename(folder)
        (folder / self.tex.name).rename(folder / "Jane Doe.tex")
        full = folder / "Jane Doe.pdf"
        legacy = folder / "DOE.PDF"
        out = self.root / "combined.pdf"

        def make_pdf(path, pages):
            writer = PdfWriter()
            for _ in range(pages):
                writer.add_blank_page(100, 100)
            writer.write(path)
        def merge():
            return mergepdf.main(["--folder", str(self.issue), "--out", str(out)])

        rc, preview = self.cli()
        self.assertEqual(rc, 0, preview)
        self.assertEqual(preview["articles"][0]["pdf"], "01 Jane Doe/Jane Doe.pdf")

        for path, pages in ((full, 2), (legacy, 3)):
            make_pdf(path, pages)
            rc, preview = self.cli()
            self.assertEqual(rc, 0, preview)
            self.assertEqual(preview["articles"][0]["pdf"], f"01 Jane Doe/{path.name}")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(merge(), 0)
            self.assertEqual(len(PdfReader(out).pages), pages)
            path.unlink()

        make_pdf(full, 2)
        make_pdf(legacy, 3)
        self.assertEqual(self.cli()[0], 2)
        previous = out.read_bytes()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            self.assertEqual(merge(), 2)
        self.assertEqual(out.read_bytes(), previous)
        self.assertIn("多个正文 PDF", stderr.getvalue())

    def test_dependency_edit_during_publish_rolls_back_only_owned_outputs(self):
        from types import SimpleNamespace
        dependency = self.folder / "reference.bib"
        dependency.write_text("original")
        plan = self.refresh.build_refresh_plan(self.issue, state_dir=self.state)
        run = self.state / "runs" / uuid.uuid4().hex
        run.mkdir(parents=True)
        staged_pdf = run / "staged.pdf"
        staged_pdf.write_bytes(b"new")
        sources = {"01 Sample/Sample.pdf": staged_pdf}
        def verify():
            dependency.write_text("editor changed dependency")
            self.refresh._published(plan, SimpleNamespace(page_ranges=[]), None, sources)
        with self.assertRaisesRegex(commit.RolledBack, "编译依赖已变化"):
            commit.publish(self.issue, self.state, run, sources, plan.expected, selection=None, verify=verify)
        self.assertFalse((self.folder / "Sample.pdf").exists())
        self.assertEqual(dependency.read_text(), "editor changed dependency")

    def test_auto_argument_binding_and_compile_failure_do_not_publish(self):
        rc, preview = self.cli("--on-change", "--only", "01")
        self.assertEqual(rc, 0, preview)
        self.assertEqual(preview["only"], [1])
        self.assertFalse(self.state.exists())
        with patch.object(self.refresh.issuepaging, "prepare_articles",
                          side_effect=self.refresh.issuepaging.PageCompileError("compile failed")), \
             patch.object(self.refresh.issuepaging_toc, "stage_issue_toc", side_effect=AssertionError):
            rc, error = self.cli("--apply", "--plan-id", preview["plan_id"])
            self.assertEqual(rc, 2)
            rc, error = self.cli("--on-change", "--only", "1", "--apply", "--plan-id", preview["plan_id"])
            self.assertEqual(rc, 1)
            self.assertEqual(error["error"], "compile failed")
        self.assertFalse((self.folder / "Sample.pdf").exists())
        self.assertEqual(self.cli("--on-change", "--only", "0")[0], 2)
        self.assertEqual(self.cli("--only", "1")[0], 2)

    def test_pending_recovery_and_state_inside_issue(self):
        identity = commit.issuewrite.issue_identity(self.issue)
        record = commit.issuewrite.record_path(identity, self.state)
        commit.issuewrite.write_record(record, dict(issue=identity, operation="renamefolders", recovery_id="abc"))
        rc, result = self.cli()
        self.assertEqual((rc, result["status"], result["pending_operation"], result["recovery_id"]),
                         (1, "recovery_required", "renamefolders", "abc"))
        self.assertTrue(Path(result["recovery_path"]).is_file())
        with patch.dict(os.environ, {"SLCN_ISSUE_STATE_DIR": str(self.issue / "new")}):
            rc, result = self.cli()
            self.assertEqual(rc, 2)
            self.assertFalse((self.issue / "new").exists())



class TwoPageRefreshIntegration(unittest.TestCase):
    """Real preview/apply/commit with synthetic article and TOC compiler outputs."""

    def test_page_limit_matrix_preserves_all_files_on_failure(self):
        import refreshpages
        from pypdf import PdfReader, PdfWriter
        from slcn import issuepaging, tocdelivery
        from test_issue_paging_toc import make_article, ZH, EN, RANGES

        for setting in (None, True, False):
            for regenerate in (False, True):
                for pages in (2, 3):
                    with self.subTest(setting=setting, regenerate=regenerate, pages=pages):
                        with tempfile.TemporaryDirectory() as temp:
                            root = Path(temp).resolve()
                            issue = root / "2026年第5期(90)"
                            issue.mkdir()
                            make_article(issue, RANGES[0].folder, "第一篇——样本",
                                         "First Sample: A Test", "甲乙", "Author One")
                            make_article(issue, RANGES[1].folder, "第二篇",
                                         "Second Sample", "丙丁", "Author Two")
                            for span in RANGES:
                                tex = issue / span.folder / (span.folder.partition(" ")[2] + ".tex")
                                with tex.open("a") as stream:
                                    stream.write(
                                        r"\newcommand{\myfirstpage}{1}"
                                        r"\newcommand{\mylastpage}{1}"
                                        r"\newcommand{\mypages}{1}")
                            target = issue / "000 目录"
                            target.mkdir()
                            (target / "Chinese Content.tex").write_text(ZH)
                            (target / "contents.tex").write_text(EN)
                            for stem in ("Chinese Content", "contents"):
                                (target / (stem + ".pdf")).write_bytes(b"old pdf")
                            if setting is not None:
                                (issue / "目录设置.json").write_text(json.dumps({"singlePageToc": setting}))
                            before = {p.relative_to(issue): p.read_bytes()
                                      for p in issue.rglob("*") if p.is_file()}

                            def pdf_output(folder, stem, count):
                                pdf = Path(folder) / (stem + ".pdf")
                                with PdfWriter() as writer:
                                    for _ in range(count):
                                        writer.add_blank_page(595, 842)
                                    writer.write(pdf)
                                pdf.with_suffix(".log").write_text(
                                    f"Output written on {pdf.name} ({count} pages).")
                                return True, str(pdf)

                            def article_compile(folder, stem, **kwargs):
                                return pdf_output(folder, stem, 1)

                            def toc_compile(folder, stems):
                                return {key: pdf_output(folder, stem, pages) for key, stem in stems}

                            def cli(*args):
                                output = io.StringIO()
                                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                                    rc = refreshpages.main(["--issue", str(issue), "--json", *args])
                                return rc, json.loads(output.getvalue())

                            options = ["--regenerate-toc"] if regenerate else []
                            with patch.dict(os.environ, {"SLCN_ISSUE_STATE_DIR": str(root / "state")}), \
                                 patch.object(issuepaging, "compile_pdf", side_effect=article_compile), \
                                 patch.object(tocdelivery, "compile_pdfs", side_effect=toc_compile):
                                rc, preview = cli(*options)
                                self.assertEqual(rc, 0, preview)
                                rc, result = cli(*options, "--apply", "--plan-id", preview["plan_id"])
                            if setting is False and pages == 2:
                                self.assertEqual(rc, 0, result)
                                self.assertEqual((result["maxPages"], result["compressionSkipped"]), (2, True))
                                for stem in ("Chinese Content", "contents"):
                                    self.assertEqual(len(PdfReader(target / (stem + ".pdf")).pages), 2)
                            else:
                                self.assertNotEqual(rc, 0, result)
                                self.assertEqual({p.relative_to(issue): p.read_bytes()
                                                  for p in issue.rglob("*") if p.is_file()}, before)
                                self.assertFalse(list((root / "state").rglob("pending.json")))

class SelectionSummary(unittest.TestCase):
    """只读选择摘要：可见计数来自当前期与排序解析，不编译、不读正文、不写事务。"""

    def setUp(self):
        import refreshpages as refresh
        self.refresh = refresh
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.issue = self.root / "issue"
        self.state = self.root / "state"
        self.issue.mkdir()
        for name in ("01 甲", "02 乙", "000 目录", "说明"):
            folder = self.issue / name
            folder.mkdir()
            if name.startswith(("01", "02")):
                (folder / (name[3:] + ".tex")).write_text("\\newcommand{\\mypages}{1}")
        env = patch.dict(os.environ, {"SLCN_ISSUE_STATE_DIR": str(self.state)})
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *args, stdin=None):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), \
             patch("sys.stdin", io.StringIO(stdin) if stdin is not None else io.StringIO("")):
            rc = self.refresh.main(["--issue", str(self.issue), "--json", *args])
        return rc, json.loads(stream.getvalue())

    def summary(self, *args, stdin=None):
        rc, body = self.cli("--selection-summary", *args, stdin=stdin)
        self.assertEqual(rc, 0, body)
        self.assertEqual(body["schema"], 1)
        self.assertEqual(body["operation"], "refreshpages")
        self.assertEqual(body["status"], "selection_summary")
        self.assertEqual(body["issue"], commit.issuewrite.issue_identity(self.issue))
        self.assertEqual(body["article_count"],
                         body["numbered_count"] - body["excluded_numbered_count"])
        return body

    def _order(self, *, excluded=()):
        dirs = {name: [d.st_dev, d.st_ino]
                for name in ("01 甲", "02 乙")
                for d in [os.stat(self.issue / name)]}
        if "说明" in excluded:
            d = os.stat(self.issue / "说明")
            dirs["说明"] = [d.st_dev, d.st_ino]
        return dict(
            schema=1, operation="renamefolders", status="completed",
            issue=commit.issuewrite.issue_identity(self.issue),
            renames=[dict(old=name[3:] if name.startswith(("01", "02")) else name,
                          new=name, main_tex=name[3:] + ".tex" if name.startswith(("01", "02")) else "说明.tex")
                     for name in ("01 甲", "02 乙", "说明") if name not in excluded],
            excluded=[name for name in excluded],
            directories=dirs,
        )

    def test_implicit_summary_counts_numbered_directories_without_pdfs_and_excludes_nonnumbered(self):
        body = self.summary()
        self.assertEqual(body["order_source"], "implicit_all")
        self.assertEqual((body["numbered_count"], body["excluded_numbered_count"]), (2, 0))
        self.assertEqual(body["article_count"], 2)
        self.assertFalse(list(self.issue.rglob("*.pdf")))
        self.assertFalse(self.state.exists())

    def test_summary_does_not_read_tex_or_pdf_content(self):
        with patch("pathlib.Path.read_text", side_effect=AssertionError("不该读取正文")):
            body = self.summary()
        self.assertEqual(body["article_count"], 2)

    def test_saved_exclusions_are_matched_and_identity_switched_counts_are_recomputed(self):
        saved = dict(issue=commit.issuewrite.issue_identity(self.issue),
                     directories=commit.numbered_directories(self.issue),
                     excluded=["02 乙"])
        state_file = commit.issuewrite.record_path(
            commit.issuewrite.issue_identity(self.issue), self.state).with_name("refresh-selection.json")
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(saved, ensure_ascii=False))
        body = self.summary()
        self.assertEqual(body["order_source"], "saved")
        self.assertEqual((body["numbered_count"], body["excluded_numbered_count"]), (2, 1))
        self.assertEqual(body["article_count"], 1)
        # 切换到另一期（新 inode、独立状态）：旧期的 saved 排除不得套用到新期
        second = self.root / "second"
        second.mkdir()
        for name in ("01 甲", "02 乙", "03 丙"):
            folder = second / name
            folder.mkdir()
            (folder / (name[3:] + ".tex")).write_text("x")
        second_state = self.root / "second-state"
        with patch.dict(os.environ, {"SLCN_ISSUE_STATE_DIR": str(second_state)}):
            rc, body = self.cli("--selection-summary", "--issue", str(second))
        self.assertEqual(rc, 0, body)
        self.assertEqual(body["issue"], commit.issuewrite.issue_identity(second))
        self.assertEqual(body["order_source"], "implicit_all")
        self.assertEqual((body["numbered_count"], body["excluded_numbered_count"]), (3, 0))
        self.assertEqual(body["article_count"], 3)

    def test_provided_result_summary_excludes_only_numbered_directories(self):
        # 排序回执覆盖全部候选（含非编号"说明"）；000 与非编号排除不得进入编号计数。
        (self.issue / "说明" / "说明.tex").write_text("x")
        excluded = ["02 乙", "说明"]
        order = self._order(excluded=excluded)
        body = self.summary("--order-result", "-", stdin=json.dumps(order, ensure_ascii=False))
        self.assertEqual(body["order_source"], "provided")
        self.assertEqual((body["numbered_count"], body["excluded_numbered_count"]), (2, 1))
        self.assertEqual(body["article_count"], 1)
        path = self.root / "order.json"
        path.write_text(json.dumps(order, ensure_ascii=False))
        self.assertEqual(self.summary("--order-result", str(path))["article_count"], 1)
        self.assertEqual(self.summary()["numbered_count"], 2)  # 000/说明 从不计数

    def test_provided_result_with_stale_identity_is_blocked(self):
        (self.issue / "说明" / "说明.tex").write_text("x")
        order = self._order(excluded=["02 乙", "说明"])
        order["directories"]["01 甲"] = [123, 456]  # 目录身份已变
        rc, body = self.cli("--selection-summary", "--order-result", "-",
                            stdin=json.dumps(order, ensure_ascii=False))
        self.assertNotEqual(rc, 0)
        self.assertIn("目录身份", str(body.get("error", "")))

    def test_summary_is_exclusive_with_mutating_options(self):
        for extra in (("--apply",), ("--plan-id", "x"), ("--recover", "x"),
                      ("--on-change",), ("--only", "1"), ("--regenerate-toc",)):
            rc, body = self.cli("--selection-summary", *extra)
            self.assertNotEqual(rc, 0)
            self.assertNotIn("numbered_count", body)

    def test_empty_directory_reports_zero_without_guess(self):
        for folder in self.issue.iterdir():
            folder.rmdir() if not any(folder.iterdir()) else None
        for tex in self.issue.rglob("*.tex"):
            tex.unlink()
            tex.parent.rmdir()
        body = self.summary()
        self.assertEqual((body["numbered_count"], body["excluded_numbered_count"]), (0, 0))
        self.assertEqual(body["article_count"], 0)

    def test_pending_transaction_and_unsafe_state_directory_block_summary(self):
        identity = commit.issuewrite.issue_identity(self.issue)
        record = commit.issuewrite.record_path(identity, self.state)
        record.parent.mkdir(parents=True)
        commit.issuewrite.write_record(record, {
            "schema": 1, "operation": "refreshpages", "recovery_id": "recover-a",
            "issue": identity,
        })
        rc, body = self.cli("--selection-summary")
        self.assertEqual(rc, 1)
        self.assertEqual(body["status"], "recovery_required")
        self.assertEqual(body["recovery_id"], "recover-a")
        self.assertNotIn("numbered_count", body)
        record.unlink()
        rc, body = self.cli("--selection-summary")
        self.assertEqual(rc, 0, body)

        inside = self.issue / "state"
        inside.mkdir()
        with patch.dict(os.environ, {"SLCN_ISSUE_STATE_DIR": str(inside)}):
            rc, body = self.cli("--selection-summary")
        self.assertNotEqual(rc, 0)
        self.assertIn("状态目录", str(body.get("error", "")))


if __name__ == "__main__":
    unittest.main()
