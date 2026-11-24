"""Isolated, synthetic fixtures for the compile-fix CLI contracts."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
ENGINE = REPO / "Engine"
CLI = ENGINE / "compilefix.py"
STEM = "synthetic"
OLD = "A & B"
NEW = r"A \& B"
BODY_LINE = "Synthetic anchor: A & B ends here."
FILLER = "This line contains only generated filler text.\n" * 18
CLOCKED_CLI = """
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

epoch = float(sys.argv[1])
sys.argv = sys.argv[2:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
with patch('time.time', return_value=epoch):
    runpy.run_path(sys.argv[0], run_name='__main__')
"""


def sha(content):
    return hashlib.sha256(content).hexdigest()


def document(body=BODY_LINE + "\n" + FILLER, *, preamble="", trailing=""):
    return (
        "\\documentclass{article}\n" + preamble + "\\begin{document}\n"
        + body + "\\end{document}\n" + trailing
    )


class CompileFixFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="compilefix-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = self.root / "private-state"
        self.use_output("output")
        self.reply = self.root / "manual-reply.json"
        self.home = self.root / "test-home"
        self.home.mkdir()
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("SLCN_", "TEXMF", "TEXINPUTS", "BIBINPUTS"))
        }
        self.env.update({
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "APPDATA": str(self.home), "LOCALAPPDATA": str(self.home),
            "XDG_CONFIG_HOME": str(self.home), "XDG_STATE_HOME": str(self.home),
            "SLCN_MANUSCRIPTS": str(self.root / "absent-manuscripts"),
            "SLCN_NOTES": str(self.root / "absent-notes"),
            "SLCN_COMPILEFIX_STATE_DIR": str(self.store),
            "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parent),
        })
        mac_tex = Path("/Library/TeX/texbin")
        if not shutil.which("xelatex", path=self.env.get("PATH")) and mac_tex.is_dir():
            self.env["PATH"] = os.pathsep.join((str(mac_tex), os.defpath))
        self.tool_path = self.env.get("PATH", os.defpath)
        self.write_candidate()

    def use_output(self, name):
        self.out = self.root / name
        self.tex = self.out / f"{STEM}.tex"
        self.pdf = self.out / f"{STEM}.pdf"
        self.log = self.out / f"{STEM}.log"

    def write_candidate(self, tex=None, *, error="Misplaced alignment tab character &.",
                        anchor=BODY_LINE, log_prefix=""):
        self.out.mkdir(exist_ok=True)
        tex = document() if tex is None else tex
        self.tex.write_bytes(tex.encode("utf-8"))
        lines = tex.splitlines()
        line_number = lines.index(anchor) + 1 if anchor in lines else 3
        self.log.write_bytes((
            "Synthetic XeTeX log banner\n" + log_prefix + f"! {error}\n"
            f"l.{line_number} {anchor}\n"
            "No pages of output.\n"
        ).encode("utf-8"))

    def command(self, *arguments):
        return [sys.executable, "-B", str(CLI), "--state-dir", str(self.store),
                *map(str, arguments)]

    def run_cmd(self, *arguments, code=0):
        result = subprocess.run(self.command(*arguments), cwd=self.root, env=self.env,
                                capture_output=True, timeout=100, check=False)
        self.assertEqual(result.returncode, code,
                         f"{arguments!r}\nstdout={result.stdout!r}\nstderr={result.stderr!r}")
        return result

    def run_cmd_at(self, epoch, *arguments, code=0):
        command = self.command(*arguments)
        command = [*command[:2], "-c", CLOCKED_CLI, str(epoch), *command[2:]]
        result = subprocess.run(command, cwd=self.root, env=self.env, capture_output=True,
                                timeout=30, check=False)
        self.assertEqual(result.returncode, code, result)
        return result

    def task_dir(self, task_id):
        return self.store / "tasks" / task_id

    def export(self, *options):
        raw = self.run_cmd("export", self.out, STEM, "--synthetic", *options).stdout
        task_id = raw.decode("ascii").strip()
        self.assertRegex(task_id, r"^[0-9a-f]{64}$")
        taskbook = self.task_dir(task_id) / "taskbook.json"
        self.assertEqual(json.loads(taskbook.read_bytes())["identity"]["candidate_sha256"],
                         sha(self.tex.read_bytes()))
        return task_id

    def preview(self, task_id):
        raw = self.run_cmd("preview", task_id).stdout
        self.assertTrue(raw.endswith(b"\n"), raw)
        self.assertEqual(raw, (self.task_dir(task_id) / "taskbook.json").read_bytes())
        return raw, json.loads(raw)

    def authorize(self):
        task_id = self.export()
        raw, book = self.preview(task_id)
        self.run_cmd("confirm", task_id, sha(raw))
        self.assertEqual(self.status(task_id)["taskbook_sha256"], sha(raw))
        return task_id, book

    def status(self, task_id):
        return json.loads(self.run_cmd("status", task_id).stdout)

    def edit(self, book, *, old=OLD, new=NEW, before="Synthetic anchor: ", after=" ends here."):
        return {"action": "edit", "evidence_id": book["evidence"][0]["evidence_id"],
                "old": old, "new": new, "before": before, "after": after}

    def report(self, book, **extra):
        return {"action": "report_only", "evidence_id": book["evidence"][0]["evidence_id"],
                "reason": "A human should inspect this synthetic diagnostic.", **extra}

    def put_reply(self, book, *actions, identity=None):
        data = {"identity": book["identity"] if identity is None else identity,
                "actions": list(actions)}
        self.reply.write_bytes((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"))
        return self.reply

    def review(self, task_id):
        return self.run_cmd("review", task_id).stdout

    def prepare_edit(self):
        task_id, book = self.authorize()
        self.run_cmd("import", task_id, self.put_reply(book, self.edit(book)))
        return task_id, self.review(task_id)

    def seed_interrupted_closeout(self, out, task_id, *, owned_pdf=None):
        """Stop a synthetic closeout after the requested publication step."""
        sys.path.insert(0, str(ENGINE))
        try:
            from slcn.compilefix import transaction
            from slcn.compilefix.state import Store
        finally:
            sys.path.pop(0)

        store = Store(self.store)
        state = store.load(task_id)
        task_dir = store.task_dir(task_id)
        tex = out / f"{STEM}.tex"
        original = tex.read_bytes()
        patched = original.replace(OLD.encode(), NEW.encode(), 1)
        self.assertNotEqual(patched, original)
        journal = transaction._backup(out, STEM, task_dir, original, patched)
        journal_path = task_dir / "journal.json"
        guard = transaction._guard_path(out, STEM)
        transaction.put_json(guard, transaction._guard(store, task_id))
        stage = transaction._stage_path(out, journal)
        stage.mkdir(mode=0o700)
        journal["stage_id"] = transaction.directory_id(stage)
        transaction.put_json(journal_path, journal)
        if owned_pdf is None:
            transaction._publish(out, tex.name, patched, journal, journal_path)
        else:
            transaction._publish(out, f"{STEM}.pdf", owned_pdf, journal, journal_path)
        state.update(state="applied", pid=None)
        store.save(task_id, state)
        return journal_path, guard, stage

    def assert_finish_line(self, result):
        self.assertEqual(result.stdout.decode("utf-8").splitlines()[-1],
                         f"完成。产物目录：{self.out}")
