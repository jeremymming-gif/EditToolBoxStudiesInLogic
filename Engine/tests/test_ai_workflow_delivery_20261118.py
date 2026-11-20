#!/usr/bin/env python3.14
"""R3 真正分类采纳 / 联合交付 / 整目录撤销 独立期望测试（2026-11-18，Task 4）。

被测（尚不存在，本文件先写钉死契约）：
  Engine/slcn/ai_workflow_convert.py
    add_convert_args(parser)
    normalize_convert_args(args)
    dispatch_taskbook(args, res, **existing_stage_kwargs) -> int|None
    deliver(*, work_dir: str, preview_id: str, use_original: bool=False) -> dict
    undo_delivery(*, work_dir: str) -> dict

全部匿名夹具；不读真实稿件、不联网、不调模型、不触用户配置。
隔离在 tempfile 临时目录；只写临时目录。

规格 §8 关键语义：
  metadata_fill 只填空/占位值且需证据+模板槽；footnote_link 需唯一 span+场=match_key+
    键在最终 Bib+locator 受保护；冲突不自动择一；preview 不写真目标；
    真正写入计数来自写后回读；整目录撤销原子移走本次目录再恢复旧目录，
    外改阻断；原先无目录则恢复为不存在；不物理删目录。

用法：
  cd Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 \
      tests/test_ai_workflow_delivery_20261118.py
"""

import argparse
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.ai_workflow_convert import (  # noqa: E402
    add_convert_args, normalize_convert_args, dispatch_taskbook,
    deliver, undo_delivery,
)


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ConvertArgsTest(unittest.TestCase):
    """普通转换新参数与旧路径不混。"""

    def test_add_convert_args_accepts_new_mode(self):
        p = argparse.ArgumentParser()
        add_convert_args(p)
        args = p.parse_args([
            "--ai-taskbook-version", "3",
            "--ai-execute", "bib_verify,typeset,text",
            "--ai-work-dir", "/tmp/r3-task",
        ])
        self.assertEqual(getattr(args, "ai_taskbook_version", None), 3)
        self.assertEqual(
            set(str(x) for x in getattr(args, "ai_execute", [])),
            {"bib_verify", "typeset", "text"},
        )
        self.assertEqual(getattr(args, "ai_work_dir", ""), "/tmp/r3-task")

    def test_normalize_maps_new_mode_without_dropping_options(self):
        p = argparse.ArgumentParser()
        add_convert_args(p)
        args = p.parse_args([
            "--source", "/tmp/src.docx", "--out", "/tmp/out", "--lang", "en",
            "--ai-taskbook-version", "3", "--ai-execute", "typeset",
            "--ai-work-dir", "/tmp/r3-task", "--compile",
        ])
        out = normalize_convert_args(args)
        self.assertTrue(out.get("ai_mode"), "新模式应标记，供 convert 第一段分流")
        self.assertEqual(getattr(out.get("args", args), "lang", "en"), "en")
        self.assertEqual(getattr(out.get("args", args), "out", "/tmp/out"), "/tmp/out")

    def test_normalize_rejects_mixing_legacy_coop(self):
        p = argparse.ArgumentParser()
        add_convert_args(p)
        args = p.parse_args([
            "--ai-taskbook-version", "3", "--ai-execute", "typeset",
            "--ai-work-dir", "/tmp/r3-task",
            "--coop-replies", "/tmp/old.json",
        ])
        with self.assertRaises(ValueError):
            normalize_convert_args(args)

    def test_dispatch_legacy_returns_none(self):
        p = argparse.ArgumentParser()
        add_convert_args(p)
        args = p.parse_args(["--source", "/tmp/x.docx"])
        self.assertIsNone(
            dispatch_taskbook(args, object()),
            "非新模式必须返回 None，交还旧 run_coop_taskbook，不抢路径",
        )


class NormalConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="slcn-r3-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "input.tex"
        self.source.write_text(r"""\documentclass{article}
\newcommand{\mypubyear}{2020}
\title{An Anonymous Example}
\author{Alice Example}
\begin{document}
\maketitle
An anonymous mispelling for conversion.
\end{document}
""", encoding="utf-8")
        self.work = self.root / "work"
        self.output = self.root / "out"
        self.final = self.output / "Anonymous"

    def invoke(self, *args, input=None):
        env = dict(os.environ, SLCN_MANUSCRIPTS=str(self.root / "unavailable"))
        return subprocess.run([sys.executable, "-B", os.path.join(_ENGINE, "convert.py"), *args],
                              capture_output=True, text=True, env=env, input=input, check=False)

    def convert(self, *args, execute="typeset,text"):
        run = self.invoke(
            "--source", str(self.source), "--out", str(self.output), "--name", "Anonymous",
            "--lang", "en", "--force", "--no-bibdb-apply", "--ai-taskbook-version", "3",
            "--ai-execute", execute, "--ai-work-dir", str(self.work), *args,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        state = self.state()
        self.assertIn("task_id=" + state["task_id"], run.stdout)
        return state

    def state(self):
        return json.loads((self.work / ".aiw" / "state.json").read_text(encoding="utf-8"))

    def cli(self, op, *args, input=None, expected=0):
        run = self.invoke("--ai-workflow", "--work-dir", str(self.work), "--json",
                          op, *args, input=input)
        self.assertEqual(run.returncode, expected, run.stdout + run.stderr)
        return json.loads(run.stdout)

    def test_first_stage_leaves_existing_final_and_freezes_conversion(self):
        self.final.mkdir(parents=True)
        sentinel = self.final / "previous.txt"
        sentinel.write_text("original delivery", encoding="utf-8")
        state = self.convert("--compile", "--source-pdf", "--clean")
        self.assertEqual(list(self.final.iterdir()), [sentinel])
        self.assertFalse((self.work / "state.json").exists())
        conversion = state["conversion"]
        stage = Path(conversion["stage_dir"])
        self.assertTrue(stage.is_relative_to(self.work))
        self.assertTrue((stage / conversion["tex_name"]).is_file())
        self.assertFalse(list(stage.glob("*.pdf")))
        for key in ("compile", "source_pdf", "clean"):
            self.assertTrue(conversion["options"][key])
        taskbook = self.cli("taskbook", "--task-id", state["task_id"])
        self.assertEqual(taskbook["sha256"], _sha(taskbook["text"]))
        self.assertIn("源侧正文", taskbook["text"])
        self.assertIn("An Anonymous Example", taskbook["text"])

    def test_original_delivers_and_undo_restores_whole_directory(self):
        self.final.mkdir(parents=True)
        sentinel = self.final / "prior.txt"
        sentinel.write_text("prior", encoding="utf-8")
        state = self.convert()
        conversion = state["conversion"]
        staged_tex = Path(conversion["stage_dir"], conversion["tex_name"])
        result = self.cli("deliver", "--use-original")
        self.assertTrue(result["delivered"], result)
        self.assertEqual((self.final / staged_tex.name).read_bytes(), staged_tex.read_bytes())
        self.assertFalse(sentinel.exists())
        self.assertFalse((self.final / ".aiw").exists())
        undone = self.cli("undo")
        self.assertTrue(undone["undone"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "prior")
        self.assertTrue(Path(undone["undo_keep"], staged_tex.name).exists())

    def test_delivery_counts_only_applied_bib_and_preserves_backfilled_entry(self):
        old_bib = """@book{A,
  title = {Original A},
  year = {2000},
}
@book{B,
  title = {Original B},
  year = {2001},
}
@book{UNUSED,
  title = {Untouched Original},
  year = {2002},
}
"""
        reply_bib = """@book{A,
  title = {Revised A},
  year = {2003},
}
@book{B,
  title = {Revised B},
  year = {2004},
}
"""
        self.source.write_text(r"""\documentclass{article}
\title{An Anonymous Example}
\author{Alice Example}
\begin{document}
See \cite{A} and \cite{B}.
\bibliographystyle{plain}
\bibliography{reference}
\end{document}
""", encoding="utf-8")
        (self.root / "reference.bib").write_text(old_bib, encoding="utf-8")

        state = self.convert(execute="bib_verify")
        imported = self.cli("import", "--stdin", "--source-kind", "complete_external",
                            "--replace-active", input=reply_bib)
        self.assertTrue(imported["ok"], imported)
        preview = self.cli("preview", "--adopt", "bib_verify")
        self.assertTrue(preview["ok"], preview)
        decisions = {item["conflict_id"]: "reply" for item in preview["conflicts"]}
        preview = self.cli("preview", "--adopt", "bib_verify", "--decide-json",
                           json.dumps(decisions))
        self.assertTrue(preview["ok"], preview)
        statuses = {item["key"]: item["status"] for item in preview["bib"]["items"]}
        self.assertEqual(statuses, {"A": "applied", "B": "applied", "UNUSED": "backfilled"})

        result = self.cli("deliver", "--preview-id", preview["preview_id"])
        self.assertTrue(result["delivered"], result)
        self.assertEqual(result["n_applied"], 2, result)
        self.assertEqual(
            {(item["item_id"], item["action"]) for item in result["adopted"]},
            {("A", "bib_verify"), ("B", "bib_verify")},
        )
        delivered_bib = (self.final / state["conversion"]["bib_name"]).read_text(encoding="utf-8")
        from slcn.bibparse import parse_entries
        entries, findings = parse_entries(delivered_bib)
        self.assertFalse([item for item in findings if item.level == "problem"], findings)
        entries_by_key = {entry.key: entry for entry in entries}
        self.assertEqual(entries_by_key["A"].fields["title"], "Revised A")
        self.assertEqual(entries_by_key["B"].fields["title"], "Revised B")
        self.assertEqual(entries_by_key["UNUSED"].fields["title"], "Untouched Original")
        self.assertEqual(entries_by_key["UNUSED"].fields["year"], "2002")

    def test_frozen_input_change_blocks_original_delivery(self):
        self.convert()
        self.source.write_text("external edit", encoding="utf-8")
        failure = self.cli("deliver", "--use-original", expected=2)
        self.assertFalse(failure["delivered"])
        self.assertFalse(self.final.exists())

    def test_frozen_resource_change_blocks_original_delivery(self):
        state = self.convert()
        stage = Path(state["conversion"]["stage_dir"])
        style = next(stage.glob("*.sty"))
        style.write_text("changed style", encoding="utf-8")
        failure = self.cli("deliver", "--use-original", expected=2)
        self.assertFalse(failure["delivered"])
        self.assertFalse(self.final.exists())

    def test_metadata_requires_source_and_only_adopts_registered_placeholder(self):
        state = self.convert()
        year = next(item for item in state["conversion"]["meta_items"]
                    if item["macro"] == "mytitle")
        evidence = next(iter(year["source_support"]))
        proposed = {"item_id": year["id"], "action": "metadata_fill",
                    "old": year["current"], "new": "An Anonymous Example", "target": "mytitle",
                    "evidence_ids": [evidence], "reason": "源侧字段明确记录题名"}
        bad = {**proposed, "new": "A Different Example"}
        self.import_typeset(state, [bad])
        rejected = self.cli("preview", "--adopt", "typeset")
        self.assertFalse(rejected["text"]["typeset"]["applied"])
        self.import_typeset(state, [proposed])
        preview = self.cli("preview", "--adopt", "typeset")
        preview_id = preview["preview_id"]
        self.assertFalse(self.final.exists())
        self.assertTrue(preview["text"]["typeset"]["applied"])
        candidate = Path(self.state()["preview"]["candidate_text"])
        self.assertIn("An Anonymous Example", candidate.read_text(encoding="utf-8"))
        result = self.cli("deliver", "--preview-id", preview_id)
        self.assertEqual(result["n_applied"], 1, result)
        contents = (self.final / "Anonymous.tex").read_text(encoding="utf-8")
        self.assertEqual(contents, candidate.read_text(encoding="utf-8"))
        undone = self.cli("undo")
        self.assertTrue(undone["undone"])
        self.assertFalse(self.final.exists())

    def footnote_proposal(self):
        from zipfile import ZipFile

        self.source = self.root / "input.docx"
        namespace = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

        def paragraph(text, tail=""):
            return f"<w:p><w:r><w:t>{text}</w:t></w:r>{tail}</w:p>"

        body = "".join([
            paragraph("An Anonymous Word Example"), paragraph("Alice Example"),
            paragraph("Abstract: This is a test abstract for a short article."),
            paragraph("Keywords: logic, inference"),
            paragraph("The thesis is supported by a source.",
                      '<w:r><w:footnoteReference w:id="1"/></w:r>'),
            paragraph("References"),
            paragraph("Brown, John. (2000). A Treatise on Logic. Oxford: Anonymous Press."),
        ])
        with ZipFile(self.source, "w") as archive:
            archive.writestr("word/document.xml",
                             f"<w:document {namespace}><w:body>{body}</w:body></w:document>")
            archive.writestr("word/footnotes.xml",
                             f'<w:footnotes {namespace}><w:footnote w:id="1">'
                             + paragraph("Brown 2001, p. 27.") + "</w:footnote></w:footnotes>")
        state = self.convert()
        footnote, = state["conversion"]["fn_items"]
        key, = state["context"]["sets"]["O"]
        proposal = {"item_id": footnote["id"], "action": "footnote_link",
                    "old": footnote["evidence"],
                    "new": f"\\parencite[p. 27]{{{key}}}", "key": key,
                    "reason": "核对原注后链接已有条目", "locator": "p. 27",
                    "evidence_ids": [footnote["id"]]}
        return state, proposal

    def test_footnote_rejects_new_that_differs_from_generated_citation(self):
        state, proposal = self.footnote_proposal()
        self.import_typeset(state, [{**proposal, "new": "THIS IS NOT A TEX CITATION"}])
        rejected = self.cli("preview", "--adopt", "typeset")
        self.assertFalse(rejected["text"]["typeset"]["applied"])

    def test_footnote_rejects_changed_locator_even_with_exact_new(self):
        state, proposal = self.footnote_proposal()
        self.import_typeset(state, [{**proposal, "locator": "p. 93",
                                    "new": f"\\parencite[p. 93]{{{proposal['key']}}}"}])
        rejected = self.cli("preview", "--adopt", "typeset")
        self.assertFalse(rejected["text"]["typeset"]["applied"])

    def test_unregistered_footnote_is_rejected_and_registered_footnote_delivers(self):
        state, proposal = self.footnote_proposal()
        self.import_typeset(state, [{**proposal, "item_id": "FN-99"}])
        rejected = self.cli("preview", "--adopt", "typeset")
        self.assertFalse(rejected["text"]["typeset"]["applied"])
        self.import_typeset(state, [proposal])
        preview = self.cli("preview", "--adopt", "typeset")
        proposed = self.state()["preview"]
        tex = Path(proposed["candidate_text"]).read_bytes()
        bib = Path(proposed["candidate_bib"]).read_bytes()
        self.assertIn(proposal["new"].encode(), tex)
        result = self.cli("deliver", "--preview-id", preview["preview_id"])
        self.assertEqual(result["n_applied"], 1, result)
        self.assertEqual((self.final / "Anonymous.tex").read_bytes(), tex)
        self.assertEqual((self.final / "reference.bib").read_bytes(), bib)

    def test_candidate_tamper_is_blocked(self):
        self.convert()
        self.cli("preview", "--adopt", "")
        preview = self.state()["preview"]
        Path(preview["candidate_text"]).write_text("modified", encoding="utf-8")
        result = self.cli("deliver", "--preview-id", preview["preview_id"], expected=2)
        self.assertIn("候选 tex 已变化", result["error"])
        self.assertFalse(self.final.exists())

    def test_external_edit_blocks_undo(self):
        self.convert()
        self.cli("deliver", "--use-original")
        edited = self.final / "Anonymous.tex"
        edited.write_text("edited", encoding="utf-8")
        result = self.cli("undo", expected=2)
        self.assertFalse(result["undone"])
        self.assertEqual(edited.read_text(encoding="utf-8"), "edited")

    def import_typeset(self, state, items):
        payload = {"schema": "slcn.ai_result/3",
                   "typeset": {"status": "completed", "items": items},
                   "text": {"status": "not_run", "items": []}}
        raw = "% TASK-ID: " + state["task_id"] + "\n```json\n"
        return self.cli("import", "--stdin", input=raw + json.dumps(payload) + "\n```\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
