"""Exercise compilation, binding, rollback and locking on synthetic candidates."""

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compilefix"
sys.path.insert(0, str(FIXTURES))
from cli_fixture import BODY_LINE, FILLER, NEW, OLD, CompileFixFixture, document, sha  # noqa: E402


SHORT_TIMEOUT_CLI = """
import runpy
import sys
from pathlib import Path
sys.argv = sys.argv[1:]
cli = sys.argv[0]
sys.path.insert(0, str(Path(cli).parent))
from slcn.compilefix import compiler
assert compiler.TOOL_TIMEOUT == 60, compiler.TOOL_TIMEOUT
compiler.TOOL_TIMEOUT = 6.0
runpy.run_path(cli, run_name='__main__')
"""


ROLLBACK_RACE_CLI = """
import json
import os
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

target, stage, late, following, receipt = map(Path, sys.argv[1:6])
sys.argv = sys.argv[6:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
from slcn.compilefix import transaction

real_replace = os.replace
real_exchange = transaction.atomicfiles.exchange

def exchange_with_writer(source, destination):
    displaced = Path(destination)
    if Path(source) == target and displaced.parent == stage and late.is_file():
        real_replace(late, target)
        before = target.is_file()
        real_exchange(source, destination)
        after = target.is_file()
        info = displaced.stat()
        if following.is_file():
            real_replace(following, target)
        receipt.write_text(json.dumps({'target_before': before, 'target_after': after,
                                      'displaced_inode': [info.st_dev, info.st_ino]}), encoding='ascii')
        return None
    return real_exchange(source, destination)

with patch.object(transaction.atomicfiles, 'exchange', side_effect=exchange_with_writer):
    runpy.run_path(sys.argv[0], run_name='__main__')
"""


OPEN_WRITER_CLI = """
import hashlib
import json
import os
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

target, external, receipt = map(Path, sys.argv[1:4])
timing = sys.argv[4]
sys.argv = sys.argv[5:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
from slcn.compilefix import transaction

real_exchange = transaction.atomicfiles.exchange
real_hash = transaction._file_hash
observed = {}
moved = False
with target.open('r+b', buffering=0) as editor:
    def write_external():
        info = os.fstat(editor.fileno())
        observed.update(inode=[info.st_dev, info.st_ino], links_at_write=info.st_nlink,
                        target_absent=not target.exists(), timing=timing)
        editor.seek(0)
        editor.write(external.read_bytes())
        editor.truncate()
        os.fsync(editor.fileno())

    def exchange_with_open_writer(source, destination):
        global moved
        result = real_exchange(source, destination)
        if Path(source) == target:
            moved = True
            if timing == 'after_exchange' and not observed:
                write_external()
        return result

    def hash_with_open_writer(path):
        result = real_hash(path)
        if (moved and timing == 'after_validation' and not observed and Path(path).is_file()
                and os.path.samestat(Path(path).stat(), os.fstat(editor.fileno()))):
            write_external()
        return result

    try:
        with (patch.object(transaction.atomicfiles, 'exchange', side_effect=exchange_with_open_writer),
              patch.object(transaction, '_file_hash', side_effect=hash_with_open_writer)):
            runpy.run_path(sys.argv[0], run_name='__main__')
    finally:
        editor.seek(0)
        observed['descriptor_sha256'] = hashlib.sha256(editor.read()).hexdigest()
        observed['links_on_exit'] = os.fstat(editor.fileno()).st_nlink
        receipt.write_text(json.dumps(observed), encoding='utf-8')
"""


UNSUPPORTED_ATOMIC_CLI = """
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

receipt = Path(sys.argv[1])
mode = sys.argv[2]
sys.argv = sys.argv[3:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
from slcn.compilefix import atomicfiles
from slcn.compilefix.files import RepairError
real_preflight = atomicfiles.preflight

def reject(out_dir, *, probe_dir=None):
    receipt.write_text(json.dumps({'called_with': str(out_dir)}), encoding='utf-8')
    if mode == 'rejected_probe':
        raise RepairError('Synthetic unsupported atomic exchange')
    with patch.object(atomicfiles, 'sys', SimpleNamespace(platform=mode)):
        return real_preflight(out_dir, probe_dir=probe_dir)

with patch.object(atomicfiles, 'preflight', side_effect=reject):
    runpy.run_path(sys.argv[0], run_name='__main__')
"""


class CompileFixRecompileTests(CompileFixFixture):
    def test_unsupported_atomic_exchange_refuses_before_any_output_change(self):
        for mode in ("rejected_probe", "win32", "linux"):
            with self.subTest(mode=mode):
                self.use_output(f"unsupported-{mode}")
                self.write_candidate()
                self.check_unsupported_atomic_exchange(mode)

    def check_unsupported_atomic_exchange(self, mode):
        self.instrument_tool("fail")
        self.keep_original_pdf()
        task_id, diff = self.prepare_edit()
        self.run_cmd("mark", task_id, "frozen", "--actor", "Synthetic operator")
        self.run_cmd("mark", task_id, "reopen", "--actor", "Synthetic operator")

        def snapshot():
            result = {}
            for path in (self.out, *sorted(self.out.rglob("*"))):
                info = path.lstat()
                result[str(path.relative_to(self.out))] = (
                    info.st_dev, info.st_ino, info.st_mode, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns,
                    path.read_bytes() if stat.S_ISREG(info.st_mode) else None,
                )
            return result

        before = snapshot()
        state_path = self.task_dir(task_id) / "state.json"
        state_before = state_path.read_bytes()
        receipt = self.root / "unsupported-exchange.json"
        command = self.command("apply", task_id, sha(diff))
        result = subprocess.run(
            [*command[:2], "-c", UNSUPPORTED_ATOMIC_CLI, str(receipt), mode, *command[2:]],
            cwd=self.root, env=self.env, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assert_finish_line(result)
        self.assertEqual(json.loads(receipt.read_bytes()), {"called_with": str(self.out)})
        self.assertEqual(snapshot(), before)
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertFalse((self.task_dir(task_id) / "journal.json").exists())
        self.assertFalse((self.task_dir(task_id) / "rollback").exists())
        self.assertFalse(self.trace.exists())

    def test_existing_target_replacement_never_exposes_missing_name(self):
        import importlib.util
        from contextlib import ExitStack
        from unittest.mock import patch
        from cli_fixture import ENGINE

        sys.path.insert(0, str(ENGINE))
        try:
            from slcn.compilefix import transaction
            from slcn.compilefix.state import Store
            spec = importlib.util.find_spec("slcn.compilefix.atomicfiles")
            if spec is not None:
                from slcn.compilefix import atomicfiles
        finally:
            sys.path.pop(0)

        original = self.keep_original_pdf()
        owned = b"%PDF-1.4\nSynthetic atomically published PDF.\n"
        original_info = self.pdf.stat()
        task_id, _ = self.prepare_edit()
        observations = []

        def observe(name, operation):
            def execute(*args, **kwargs):
                result = operation(*args, **kwargs)
                data = self.pdf.read_bytes() if self.pdf.is_file() else None
                observations.append((name, None if data is None else sha(data)))
                return result
            return execute

        originals = {name: getattr(os, name) for name in ("replace", "rename", "link", "unlink")}
        with ExitStack() as stack:
            for name, operation in originals.items():
                stack.enter_context(patch.object(transaction.os, name, side_effect=observe(name, operation)))
            if spec is not None:
                stack.enter_context(patch.object(atomicfiles, "exchange",
                                                side_effect=observe("exchange", atomicfiles.exchange)))
            self.seed_interrupted_closeout(self.out, task_id, owned_pdf=owned)
            self.assertEqual(self.pdf.read_bytes(), owned)
            store = Store(self.store)
            store.recover(task_id, store.load(task_id))

        self.assertTrue(observations)
        self.assertTrue(all(value in (sha(original), sha(owned)) for _, value in observations),
                        f"canonical PDF missing or incomplete at a primitive return: {observations}")
        self.assertEqual(self.pdf.read_bytes(), original)
        retained = [path for path in self.out.glob(".compilefix-displaced/*/retained-*")
                    if os.path.samestat(original_info, path.stat())]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_bytes(), original)

    def instrument_tool(self, mode, *, require_real=False):
        real = shutil.which("xelatex", path=self.tool_path)
        if require_real and real is None:
            self.skipTest("BLOCKED: XeLaTeX is unavailable")
        if os.name != "posix":
            self.skipTest("The compiler observation fixture requires POSIX executables")
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        wrapper = tools / "xelatex"
        wrapper.write_bytes(b"#!" + os.fsencode(sys.executable) + b"\n"
                            + (FIXTURES / "compiler_tool.py").read_bytes())
        wrapper.chmod(0o700)
        self.trace = self.root / f"compile-{self.out.name}.jsonl"
        self.ready = self.root / "compile-ready"
        self.release = self.root / "compile-release"
        self.env["PATH"] = os.pathsep.join([str(tools), self.tool_path])
        config = {"real_tool": real or "", "mode": mode, "runs": str(self.trace),
                  "ready": str(self.ready), "release": str(self.release), "output": str(self.out)}
        wrapper.with_suffix(".json").write_text(json.dumps(config), encoding="utf-8")

    def attempts(self):
        return [json.loads(line) for line in self.trace.read_text(encoding="utf-8").splitlines()]

    def keep_original_pdf(self):
        content = b"%PDF-1.4\nSynthetic old output must survive rejection.\n"
        self.pdf.write_bytes(content)
        return content

    def make_real_initial_failure(self):
        tool = shutil.which("xelatex", path=self.tool_path)
        if tool is None:
            self.skipTest("BLOCKED: XeLaTeX is unavailable")
        command = [tool, "-no-shell-escape", "-halt-on-error", "-interaction=nonstopmode", self.tex.name]
        result = subprocess.run(command, cwd=self.out, env=self.env, capture_output=True,
                                timeout=45, check=False)
        self.assertNotEqual(result.returncode, 0, result)
        self.assertIn(b"Misplaced alignment tab character", self.log.read_bytes())

    def test_success_binds_both_real_compiles_and_preserves_source(self):
        self.make_real_initial_failure()
        self.instrument_tool("delegate", require_real=True)
        original = self.tex.read_bytes()
        prior_pdf = self.keep_original_pdf()
        source = self.root / "editor-original.tex"
        source.write_bytes(original)
        # Keep a prior compile section to prove the new section doesn't overwrite it.
        check_note = self.out / "AI交付检查说明.md"
        check_note.write_bytes("# Check\n\n## 编译状态\n\n先前合成转换记录。\n".encode())
        task_id, diff = self.prepare_edit()
        result = self.run_cmd("apply", task_id, sha(diff))
        self.assert_finish_line(result)
        expected = original.replace(OLD.encode(), NEW.encode(), 1)
        self.assertEqual(self.tex.read_bytes(), expected)
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(self.pdf.read_bytes().startswith(b"%PDF-"))
        self.assertGreater(self.pdf.stat().st_size, 1000)
        self.assertNotEqual(self.pdf.read_bytes(), prior_pdf)
        attempts = self.attempts()
        directories = list(dict.fromkeys(entry["cwd"] for entry in attempts))
        self.assertEqual(len(directories), 2, attempts)
        self.assertNotEqual(directories[0], str(self.out))
        self.assertEqual(directories[1], str(self.out))
        self.assertEqual([entry["cwd"] for entry in attempts],
                         [directories[0]] * 3 + [directories[1]] * 3)
        for entry in attempts:
            self.assertEqual(entry["sha256"], sha(expected))
            self.assertIn("-no-shell-escape", entry["argv"])
            self.assertIn("-recorder", entry["argv"])
        status = self.status(task_id)
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["private_review"], "accepted")
        self.assertEqual(set(status["runs"]), {"isolated", "output"})
        isolated_run, output_run = (status["runs"][key] for key in ("isolated", "output"))
        self.assertNotEqual(isolated_run["run_id"], output_run["run_id"])
        for run in (isolated_run, output_run):
            self.assertEqual(run["state"], "passed")
            self.assertEqual(run["candidate_sha256"], sha(expected))
            for artifact in ("pdf", "log", "recorder"):
                self.assertRegex(run[f"{artifact}_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(output_run["pdf_sha256"], sha(self.pdf.read_bytes()))
        self.assertEqual(output_run["log_sha256"], sha(self.log.read_bytes()))
        note = check_note.read_text(encoding="utf-8")
        self.assertIn("## 编译状态", note)
        self.assertIn("先前合成转换记录。", note)
        self.assertIn("## 编译修复", note)
        self.assertIn(sha(expected), note)
        self.assertIn(sha(self.pdf.read_bytes()), note)
        self.assertNotIn(str(self.root), note)
        before = self.pdf.read_bytes()
        rejected = self.run_cmd("apply", task_id, sha(diff), code=2)
        self.assert_finish_line(rejected)
        self.assertEqual(self.pdf.read_bytes(), before)
        self.assertEqual(self.attempts(), attempts)

    def test_second_compile_failure_rolls_back_original_tex_and_pdf(self):
        self.instrument_tool("fail-output", require_real=True)
        original = self.tex.read_bytes()
        prior_pdf = self.keep_original_pdf()
        task_id, diff = self.prepare_edit()
        result = self.run_cmd("apply", task_id, sha(diff), code=2)
        self.assert_finish_line(result)
        attempts = self.attempts()
        self.assertNotEqual(attempts[0]["cwd"], str(self.out))
        self.assertEqual(attempts[-1]["cwd"], str(self.out))
        self.assertEqual(self.tex.read_bytes(), original)
        self.assertEqual(self.pdf.read_bytes(), prior_pdf)
        self.assertEqual(self.status(task_id)["state"], "compile_failed")

    def test_failed_output_compile_never_overwrites_concurrent_external_pdf(self):
        self.instrument_tool("hold-output", require_real=True)
        original = self.tex.read_bytes()
        self.keep_original_pdf()
        task_id, diff = self.prepare_edit()
        external_pdf = b"%PDF-1.4\nSynthetic concurrent editorial PDF.\n"
        replacement = self.root / "external-replacement.pdf"
        replacement.write_bytes(external_pdf)
        with subprocess.Popen(
            self.command("apply", task_id, sha(diff)), cwd=self.root, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        ) as active:
            try:
                deadline = time.monotonic() + 15
                while not self.ready.exists() and active.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(self.ready.exists(), "apply never reached the output compiler")
                self.assertEqual(self.attempts()[-1]["cwd"], str(self.out))
                self.assertEqual(self.status(task_id)["state"], "applied")
                replacement.replace(self.pdf)
                self.assertEqual(self.pdf.read_bytes(), external_pdf)
            finally:
                self.release.touch()
                try:
                    stdout, stderr = active.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(active.pid, signal.SIGKILL)
                    active.communicate(timeout=5)
                    raise
        self.assertEqual(active.returncode, 2, stdout + stderr)
        self.assertEqual(stdout.decode().splitlines()[-1], f"完成。产物目录：{self.out}")
        self.assertEqual(self.pdf.read_bytes(), external_pdf)
        self.assertEqual(self.tex.read_bytes(), original)
        self.assertFalse((self.out / "AI交付检查说明.md").exists())
        self.assertEqual(self.status(task_id)["state"], "unknown")
        journal = self.task_dir(task_id) / "journal.json"
        self.assertTrue(journal.is_file())
        attempts = self.attempts()
        self.run_cmd("apply", task_id, sha(diff), code=2)
        self.assertEqual(self.pdf.read_bytes(), external_pdf)
        self.assertEqual(self.tex.read_bytes(), original)
        self.assertEqual(self.status(task_id)["state"], "unknown")
        self.assertTrue(journal.is_file())
        self.assertEqual(self.attempts(), attempts)

    def test_rollback_preserves_writers_between_owned_hash_and_replacement(self):
        owned_pdf = b"%PDF-1.4\nSynthetic transaction-owned PDF.\n"
        late_pdf = b"%PDF-1.4\nFirst synthetic external PDF.\n"
        following_pdf = b"%PDF-1.4\nSecond synthetic external PDF.\n"
        for second_writer in (False, True):
            with self.subTest(second_writer=second_writer):
                self.use_output(f"rollback-race-{second_writer}")
                self.write_candidate()
                original_tex = self.tex.read_bytes()
                original_pdf = self.keep_original_pdf()
                task_id, diff = self.prepare_edit()
                journal, guard, stage = self.seed_interrupted_closeout(self.out, task_id, owned_pdf=owned_pdf)
                self.assertEqual(self.pdf.read_bytes(), owned_pdf)
                self.assertEqual(self.status(task_id)["state"], "unknown")
                backup_dir = self.task_dir(task_id) / "rollback"
                backup = {path: path.read_bytes() for path in backup_dir.glob("*.bak")}
                self.assertIn(original_pdf, backup.values())
                guard_bytes = guard.read_bytes()
                late = self.out / "synthetic-external-before.pdf"
                following = self.out / "synthetic-external-after.pdf"
                receipt = self.root / f"race-receipt-{second_writer}"
                late.write_bytes(late_pdf)
                if second_writer:
                    following.write_bytes(following_pdf)
                command = self.command("apply", task_id, sha(diff))

                result = subprocess.run(
                    [*command[:2], "-c", ROLLBACK_RACE_CLI, *map(str, (self.pdf, stage, late, following, receipt)),
                     *command[2:]], cwd=self.root, env=self.env, capture_output=True, timeout=30, check=False,
                )

                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assert_finish_line(result)
                self.assertTrue(receipt.is_file(), "the writer did not run inside rollback replacement")
                observation = json.loads(receipt.read_bytes())
                self.assertTrue(observation["target_before"])
                self.assertTrue(observation["target_after"])
                retained_files = [path for path in self.out.rglob("*") if path.is_file()
                                  and [path.stat().st_dev, path.stat().st_ino] == observation["displaced_inode"]]
                self.assertEqual(len(retained_files), 1)
                retained = retained_files[0]
                self.assertFalse(late.exists())
                self.assertEqual(self.tex.read_bytes(), original_tex)
                self.assertFalse((self.out / "AI交付检查说明.md").exists())
                conflict = json.loads(journal.read_bytes())["pending_replacement"]
                self.assertEqual(conflict["file"], self.pdf.name)
                self.assertEqual(conflict["expected_sha256"], sha(owned_pdf))
                self.assertEqual(conflict["desired_sha256"], sha(original_pdf))
                if second_writer:
                    self.assertEqual(self.pdf.read_bytes(), following_pdf)
                    self.assertEqual(retained.read_bytes(), late_pdf)
                else:
                    self.assertIn(self.pdf.read_bytes(), (late_pdf, original_pdf))
                    self.assertEqual(retained.read_bytes(), late_pdf)
                if retained.exists():
                    self.assertEqual(conflict["retained"], retained.name)
                    if os.name == "posix":
                        self.assertEqual(stat.S_IMODE(stage.stat().st_mode) & 0o077, 0)

                evidence = {path: path.read_bytes() for path in (self.pdf, retained, journal, guard)
                            if path.is_file()}
                self.run_cmd("apply", task_id, sha(diff), code=2)
                self.run_cmd("purge", task_id, code=2)
                self.assertEqual({path: path.read_bytes() for path in (self.pdf, retained, journal, guard)
                                  if path.is_file()}, evidence)
                self.assertEqual(guard.read_bytes(), guard_bytes)
                self.assertEqual({path: path.read_bytes() for path in backup_dir.glob("*.bak")}, backup)
                status = self.status(task_id)
                self.assertEqual((status["state"], status["failure_phase"]), ("unknown", "rollback"))

    def test_rollback_keeps_displaced_inode_written_via_open_descriptor(self):
        if os.name != "posix":
            self.skipTest("This fixture exercises POSIX rename of an open file descriptor")
        for timing in ("after_exchange", "after_validation"):
            with self.subTest(timing=timing):
                self.use_output(f"open-writer-{timing}")
                self.write_candidate()
                self.check_open_writer_recovery(timing)

    def check_open_writer_recovery(self, timing):
        original_tex = self.tex.read_bytes()
        original_pdf = self.keep_original_pdf()
        owned_pdf = b"%PDF-1.4\nSynthetic transaction-owned open file.\n"
        external_pdf = b"%PDF-1.4\nSynthetic external in-place PDF update.\n"
        task_id, diff = self.prepare_edit()
        journal, guard, stage = self.seed_interrupted_closeout(self.out, task_id, owned_pdf=owned_pdf)
        self.assertEqual(self.pdf.read_bytes(), owned_pdf)
        external = self.root / "open-writer-input.pdf"
        external.write_bytes(external_pdf)
        receipt = self.root / "open-writer-receipt.json"
        command = self.command("apply", task_id, sha(diff))

        result = subprocess.run(
            [*command[:2], "-c", OPEN_WRITER_CLI, *map(str, (self.pdf, external, receipt)), timing, *command[2:]],
            cwd=self.root, env=self.env, capture_output=True, timeout=30, check=False,
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assert_finish_line(result)
        recorded = json.loads(receipt.read_bytes())
        self.assertFalse(recorded["target_absent"], recorded)
        self.assertEqual(recorded["links_at_write"], 1)
        self.assertEqual(recorded["descriptor_sha256"], sha(external_pdf))
        self.assertGreaterEqual(recorded["links_on_exit"], 1, recorded)
        self.assertEqual(self.tex.read_bytes(), original_tex)
        self.assertEqual(self.pdf.read_bytes(), original_pdf)
        self.assertFalse((self.out / "AI交付检查说明.md").exists())

        archive = self.out / ".compilefix-displaced" / task_id
        index = archive / "journal.json" if (archive / "journal.json").is_file() else journal
        index_bytes = index.read_bytes()
        index_data = json.loads(index_bytes)
        if index == journal:
            entry = index_data["pending_replacement"]
            self.assertEqual(entry["file"], self.pdf.name)
            self.assertEqual(entry["expected_sha256"], sha(owned_pdf))
            retained = stage / entry["retained"]
        else:
            [entry] = [item for item in index_data["displaced"]
                       if item["file"] == self.pdf.name and item["expected_sha256"] == sha(owned_pdf)]
            retained = archive / entry["retained"]
        for directory in {retained.parent, index.parent}:
            info = directory.lstat()
            self.assertTrue(stat.S_ISDIR(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode) & 0o077, 0)
        self.assertTrue(stat.S_ISREG(index.lstat().st_mode))
        self.assertEqual(retained.name, entry["retained"])
        self.assertEqual(retained.resolve().parent, retained.parent)
        info = retained.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual([info.st_dev, info.st_ino], recorded["inode"])
        self.assertEqual(retained.read_bytes(), external_pdf)

        status = self.status(task_id)
        self.assertEqual(status["state"], "unknown")
        phase = status["failure_phase"]
        self.assertEqual(phase, "rollback")
        if phase == "rollback":
            self.assertTrue(guard.is_file())
            self.assertTrue(journal.is_file())
            backups = [path.read_bytes() for path in (journal.parent / "rollback").glob("*.bak")]
            self.assertIn(original_pdf, backups)
        self.run_cmd("apply", task_id, sha(diff), code=2)
        self.run_cmd("purge", task_id, code=2 if phase == "rollback" else 0)
        self.assertEqual(index.read_bytes(), index_bytes)
        self.assertEqual(retained.read_bytes(), external_pdf)
        self.assertEqual(self.pdf.read_bytes(), original_pdf)

    def test_compiler_exit_failure_and_missing_fresh_artifacts_are_distinct(self):
        for mode, state in (("fail", "compile_failed"), ("no-artifacts", "unknown")):
            with self.subTest(mode=mode):
                self.use_output(mode)
                self.write_candidate(document_for_mode(mode))
                self.instrument_tool(mode)
                original = self.tex.read_bytes()
                prior_pdf = self.keep_original_pdf()
                prior_log = self.log.read_bytes()
                task_id, diff = self.prepare_edit()
                result = self.run_cmd("apply", task_id, sha(diff), code=2)
                self.assert_finish_line(result)
                self.assertEqual(self.status(task_id)["state"], state)
                self.assertEqual(self.tex.read_bytes(), original)
                self.assertEqual(self.pdf.read_bytes(), prior_pdf)
                self.assertEqual(self.log.read_bytes(), prior_log)

    def test_success_exit_cannot_reuse_old_output_pdf_or_log(self):
        self.instrument_tool("no-output-artifacts", require_real=True)
        original = self.tex.read_bytes()
        prior_pdf = self.keep_original_pdf()
        prior_log = self.log.read_bytes()
        task_id, diff = self.prepare_edit()
        result = self.run_cmd("apply", task_id, sha(diff), code=2)
        self.assert_finish_line(result)
        self.assertEqual(self.status(task_id)["state"], "unknown")
        self.assertEqual(self.attempts()[-1]["cwd"], str(self.out))
        self.assertEqual(self.tex.read_bytes(), original)
        self.assertEqual(self.pdf.read_bytes(), prior_pdf)
        self.assertEqual(self.log.read_bytes(), prior_log)

    def test_cleanup_failure_cannot_pass_using_old_artifacts(self):
        self.instrument_tool("delegate", require_real=True)
        original = self.tex.read_bytes()
        prior_pdf = self.keep_original_pdf()
        prior_log = self.log.read_bytes()
        task_id, diff = self.prepare_edit()
        stale_aux = self.out / self.tex.with_suffix(".aux").name
        stale_aux.mkdir()
        sentinel = stale_aux / "keep"
        sentinel.write_bytes(b"synthetic cleanup blocker")
        result = self.run_cmd("apply", task_id, sha(diff), code=2)
        self.assert_finish_line(result)
        self.assertEqual(self.status(task_id)["state"], "unknown")
        self.assertEqual(self.tex.read_bytes(), original)
        self.assertEqual(self.pdf.read_bytes(), prior_pdf)
        self.assertEqual(self.log.read_bytes(), prior_log)
        self.assertEqual(sentinel.read_bytes(), b"synthetic cleanup blocker")
        if self.trace.exists():
            self.assertNotIn(str(self.out), [entry["cwd"] for entry in self.attempts()])

    def test_tool_timeout_is_unknown_and_cannot_deliver_or_retry(self):
        self.instrument_tool("hold")
        original = self.tex.read_bytes()
        prior_pdf = self.keep_original_pdf()
        task_id, diff = self.prepare_edit()
        original_command = self.command("apply", task_id, sha(diff))
        command = [*original_command[:2], "-c", SHORT_TIMEOUT_CLI, *original_command[2:]]
        result = subprocess.run(command, cwd=self.root, env=self.env, capture_output=True,
                                timeout=17, check=False)
        self.assertEqual(result.returncode, 2, result)
        self.assert_finish_line(result)
        status = self.status(task_id)
        self.assertEqual(status["state"], "unknown")
        self.assertTrue(status["failure_phase"].endswith("-timeout"), (status, result))
        self.assertEqual(self.tex.read_bytes(), original)
        self.assertEqual(self.pdf.read_bytes(), prior_pdf)
        self.assertEqual(len(self.attempts()), 1)
        self.run_cmd("apply", task_id, sha(diff), code=2)
        self.run_cmd("export", self.out, "synthetic", "--synthetic", code=2)
        self.assertEqual(len(self.attempts()), 1)

    def test_output_symlink_swap_after_review_does_not_touch_source(self):
        original = self.tex.read_bytes()
        task_id, diff = self.prepare_edit()
        outside = self.root / "author-source"
        self.out.rename(outside)
        outside_tex = outside / self.tex.name
        try:
            self.out.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        result = self.run_cmd("apply", task_id, sha(diff), code=2)
        self.assert_finish_line(result)
        self.assertTrue(self.out.is_symlink())
        self.assertEqual(outside_tex.read_bytes(), original)
        self.assertFalse((outside / self.pdf.name).exists())

    def test_freeze_or_confirm_requires_operator_reopen(self):
        for protection in ("frozen", "confirmed"):
            with self.subTest(protection=protection):
                self.use_output(protection)
                self.write_candidate(document_for_mode(protection))
                self.instrument_tool("delegate", require_real=True)
                prior = self.tex.read_bytes()
                prior_pdf = self.keep_original_pdf()
                task_id, diff = self.prepare_edit()
                self.run_cmd("mark", task_id, protection, "--actor", "Synthetic checker")
                refused = self.run_cmd("apply", task_id, sha(diff), code=2)
                self.assert_finish_line(refused)
                self.assertEqual(self.tex.read_bytes(), prior)
                self.assertEqual(self.pdf.read_bytes(), prior_pdf)
                marker = self.status(task_id)["protection"]
                self.assertEqual(marker["kind"], protection)
                self.assertEqual(marker["actor"], "Synthetic checker")
                self.assertEqual(marker["writer"], f"Engine/compilefix.py mark {protection}")
                self.assertEqual(marker["candidate_sha256"], sha(prior))
                self.assertEqual(json.loads(Path(marker["source_path"]).read_bytes()), marker)
                self.run_cmd("mark", task_id, "reopen", "--actor", "Synthetic checker")
                applied = self.run_cmd("apply", task_id, sha(diff))
                self.assert_finish_line(applied)
                status = self.status(task_id)
                self.assertEqual(status["state"], "completed")
                self.assertNotIn("reopen", status["protection"])
                self.assertEqual(status["protection"]["last_consumed_reopen"]["task_id"], task_id)
                self.assertEqual(status["protection"]["last_consumed_reopen"]["candidate_sha256"], sha(prior))
                self.run_cmd("apply", task_id, sha(diff), code=2)

    def start_interrupted_apply(self, task_id, diff, *, check_rival=False):
        with subprocess.Popen(
            self.command("apply", task_id, sha(diff)), cwd=self.root, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        ) as active:
            compiler_pid = None
            try:
                deadline = time.monotonic() + 15
                while not self.ready.exists() and active.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(self.ready.exists(), "first apply never reached the compiler")
                compiler_pid = int(self.ready.read_text(encoding="ascii"))
                self.assertEqual(self.status(task_id)["state"], "applied")
                if check_rival:
                    rival = subprocess.run(self.command("apply", task_id, sha(diff)), cwd=self.root,
                                           env=self.env, capture_output=True, timeout=8, check=False)
                    self.assertEqual(rival.returncode, 2, rival)
                    self.assertEqual(len(self.attempts()), 1)
            finally:
                if active.poll() is None:
                    os.killpg(active.pid, signal.SIGKILL)
                self.release.touch()
                active.communicate(timeout=5)
                if compiler_pid is not None:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        try:
                            os.kill(compiler_pid, 0)
                        except ProcessLookupError:
                            break
                        time.sleep(0.02)
        self.assertEqual(self.status(task_id)["state"], "unknown")

    def test_apply_lock_excludes_other_process_and_interruption_recovers_unknown(self):
        self.instrument_tool("hold")
        task_id, diff = self.prepare_edit()
        prior = self.tex.read_bytes()
        prior_pdf = self.keep_original_pdf()
        self.start_interrupted_apply(task_id, diff, check_rival=True)
        self.assertEqual(self.tex.read_bytes(), prior)
        self.assertEqual(self.pdf.read_bytes(), prior_pdf)
        self.run_cmd("apply", task_id, sha(diff), code=2)
        self.assertEqual(len(self.attempts()), 1)
        state = json.loads((self.task_dir(task_id) / "state.json").read_bytes())
        self.assertEqual(state["state"], "unknown")

    def test_interrupted_closeout_recovers_original_files_without_recompiling(self):
        self.instrument_tool("hold-output", require_real=True)
        task_id, diff = self.prepare_edit()
        prior = self.tex.read_bytes()
        prior_pdf = self.keep_original_pdf()
        prior_log = self.log.read_bytes()
        self.start_interrupted_apply(task_id, diff)
        self.assertEqual(self.attempts()[-1]["cwd"], str(self.out))
        count = len(self.attempts())
        rejected = self.run_cmd("apply", task_id, sha(diff), code=2)
        self.assert_finish_line(rejected)
        self.assertEqual(self.status(task_id)["state"], "unknown")
        self.assertEqual(self.tex.read_bytes(), prior)
        self.assertEqual(self.pdf.read_bytes(), prior_pdf)
        self.assertEqual(self.log.read_bytes(), prior_log)
        self.assertEqual(len(self.attempts()), count)


def document_for_mode(mode):
    return document(f"{BODY_LINE}\nSynthetic variant {mode}.\n" + FILLER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
