"""Test exact compiler capture, artifact, backup and tool limits on synthetic files."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "compilefix"))
from cli_fixture import BODY_LINE, ENGINE, STEM, document, sha  # noqa: E402

sys.path.insert(0, str(ENGINE))
LOG_LIMIT = 2 * 1024**2


class SizedBytes(bytes):
    """Small backing bytes with the reported length of a synthetic large asset."""

    def __new__(cls, content, size):
        value = super().__new__(cls, content)
        value.size = size
        return value

    def __len__(self):
        return self.size


def recorder_data(cwd, size=None):
    header = f"PWD {cwd}\nINPUT {STEM}.tex\n".encode()
    if size is None:
        return header

    def row(extra):
        # Recorder entries can repeat and the path normalizes to the same local candidate.
        return ("INPUT ." + "/" * (512 + extra) + f"{STEM}.tex\n").encode()

    count, remainder = divmod(size - len(header), len(row(0)))
    data = header + row(0) * (count - 1) + row(remainder)
    assert len(data) == size
    return data


class CompileFixLimitsTests(unittest.TestCase):
    def test_initial_stdout_accepts_two_mib_and_rejects_plus_one(self):
        import slcn.compilefix as repair
        from slcn.compilefix import model, state

        with tempfile.TemporaryDirectory(prefix="compilefix-capture-cap-") as temporary:
            root = Path(temporary).resolve()
            source = root / "source" / "synthetic-original.tex"
            output = root / "output"
            source.parent.mkdir()
            output.mkdir()
            original = document().encode()
            source.write_bytes(original)
            (output / f"{STEM}.tex").write_bytes(original)
            error = f"! Misplaced alignment tab character &.\nl.3 {BODY_LINE}\n".encode()
            message = "xelatex 第 1 步失败（退出码 19）"
            for size in (LOG_LIMIT - 1, LOG_LIMIT, LOG_LIMIT + 1):
                with self.subTest(size=size):
                    transcript = error.ljust(size, b"x")

                    def runner(command, *, cwd, quiet, check, stdout):
                        stdout.write(transcript)
                        return SimpleNamespace(returncode=19)

                    def first_compile(out_dir, stem, *, run_xelatex):
                        completed = run_xelatex(runner, ["xelatex", stem + ".tex"], out_dir, 1)
                        self.assertEqual(completed.returncode, 19)
                        return False, message

                    with patch.object(repair, "trusted_temporary_roots", return_value=(root,)):
                        ok, info, capture = repair.compile_with_capture(first_compile, output, STEM, source=source)
                    self.assertEqual((ok, info), (False, message))
                    accepted, binding = state._initial_transcript(capture, message)
                    [diagnostic] = model.diagnostics(original, accepted, STEM, info)
                    if size <= LOG_LIMIT:
                        self.assertEqual(accepted, transcript)
                        self.assertEqual(binding["transcript_sha256"], sha(transcript))
                        self.assertEqual(diagnostic["classification"], "editable")
                    else:
                        self.assertIsNone(accepted)
                        self.assertIsNone(binding)
                        self.assertEqual(diagnostic["classification"], "unknown")

    def simulate_recompile(self, *, log_size=None, recorder_size=None, bibliography=False):
        from slcn import sortlocale
        from slcn.compilefix import compiler

        pdf = b"%PDF-1.4\nSynthetic compiler output.\n%%EOF\n"
        log = b"Server-free synthetic compiler diagnostic.\n"
        if log_size is not None:
            log = log.ljust(log_size, b"x")
        with tempfile.TemporaryDirectory(prefix="compilefix-compile-cap-") as temporary:
            root = Path(temporary).resolve()
            cwd = root / "compile"
            cwd.mkdir()
            candidate = document().encode()
            (cwd / f"{STEM}.tex").write_bytes(candidate)
            recorder = recorder_data(cwd, recorder_size)
            support = {}
            if bibliography:
                bib = b"@book{synthetic,title={Synthetic},year={2026}}\n"
                (cwd / "synthetic.bib").write_bytes(bib)
                support["synthetic.bib"] = sha(bib)
            invocations = []

            def invoke(command, location, env, leases):
                self.assertEqual(location, cwd)
                tool = Path(command[0]).name
                invocations.append(tool)
                if tool == "biber":
                    (cwd / f"{STEM}.bbl").write_bytes(b"Synthetic bibliography output.\n")
                    return 0
                self.assertEqual(tool, "xelatex")
                for suffix, contents in ((".pdf", pdf), (".log", log), (".fls", recorder)):
                    (cwd / (STEM + suffix)).write_bytes(contents)
                if bibliography:
                    (cwd / f"{STEM}.bcf").write_bytes(
                        b"<root><datasource>synthetic.bib</datasource></root>")
                return 0

            with (patch.object(compiler, "_invoke", side_effect=invoke),
                  patch.object(compiler.shutil, "which", side_effect=lambda name: str(root / "fake-bin" / name)),
                  patch.object(sortlocale, "guard_after_biber", return_value=None)):
                result = compiler.compile_candidate(cwd, STEM, sha(candidate), support, root)
            return result, invocations, sha(log), sha(recorder)

    def test_recompile_log_accepts_two_mib_and_rejects_plus_one(self):
        for size in (LOG_LIMIT - 1, LOG_LIMIT, LOG_LIMIT + 1):
            with self.subTest(size=size):
                result, calls, log_hash, _ = self.simulate_recompile(log_size=size)
                self.assertEqual(calls, ["xelatex"] * 3)
                self.assertEqual(result.phase, "bind")
                if size <= LOG_LIMIT:
                    self.assertEqual(result.state, "passed")
                    self.assertEqual(result.log_sha256, log_hash)
                else:
                    self.assertEqual(result.state, "unknown")
                    self.assertIsNone(result.log_sha256)

    def test_recompile_recorder_accepts_two_mib_and_rejects_plus_one(self):
        for size in (LOG_LIMIT - 1, LOG_LIMIT, LOG_LIMIT + 1):
            with self.subTest(size=size):
                result, calls, _, recorder_hash = self.simulate_recompile(recorder_size=size)
                if size <= LOG_LIMIT:
                    self.assertEqual(calls, ["xelatex"] * 3)
                    self.assertEqual((result.state, result.phase), ("passed", "bind"))
                    self.assertEqual(result.recorder_sha256, recorder_hash)
                else:
                    self.assertEqual(calls, ["xelatex"])
                    self.assertEqual((result.state, result.phase), ("unknown", "xelatex-1"))
                    self.assertIsNone(result.recorder_sha256)

    def test_compiler_stops_after_three_passes_with_at_most_one_biber(self):
        for bibliography, expected in ((False, ["xelatex"] * 3),
                                       (True, ["xelatex", "biber", "xelatex", "xelatex"])):
            with self.subTest(bibliography=bibliography):
                result, calls, _, _ = self.simulate_recompile(bibliography=bibliography)
                self.assertEqual(result.state, "passed")
                self.assertEqual(calls, expected)

    def test_rollback_backup_total_accepts_128_mib_but_refuses_plus_one(self):
        from slcn.compilefix import files, transaction

        total_limit = 128 * 1024**2
        single_limit = 64 * 1024**2
        original = document().encode()
        patched = original.replace(b"A & B", b"A \\& B", 1)
        for total in (total_limit - 1, total_limit, total_limit + 1):
            with self.subTest(total=total), tempfile.TemporaryDirectory(prefix="compilefix-backup-cap-") as temporary:
                root = Path(temporary).resolve()
                out_dir, task = root / "output", root / "task"
                out_dir.mkdir()
                task.mkdir(mode=0o700)
                tex, pdf, log = (out_dir / (STEM + suffix) for suffix in (".tex", ".pdf", ".log"))
                tex.write_bytes(original)
                assets = {pdf: SizedBytes(b"synthetic PDF", single_limit),
                          log: SizedBytes(b"synthetic log", total - single_limit - len(original))}
                for path, data in assets.items():
                    self.assertLessEqual(len(data), single_limit)
                    path.write_bytes(data)

                def read(path, limit):
                    self.assertEqual(limit, single_limit)
                    return assets[path] if path in assets else files.read(path, limit)

                with patch.object(transaction, "read", side_effect=read):
                    if total <= total_limit:
                        journal = transaction._backup(out_dir, STEM, task, original, patched)
                        self.assertEqual(sum(len(data) for data in assets.values()) + len(original), total)
                        for path, data in assets.items():
                            item = journal["files"][path.name]
                            self.assertEqual(item["sha256"], sha(data))
                            self.assertEqual(sha((task / "rollback" / item["name"]).read_bytes()), sha(data))
                        self.assertTrue((task / "journal.json").is_file())
                    else:
                        with self.assertRaisesRegex(files.RepairError, "回滚上限"):
                            transaction._backup(out_dir, STEM, task, original, patched)
                        self.assertFalse((task / "journal.json").exists())
                self.assertEqual(tex.read_bytes(), original)
                self.assertEqual((pdf.read_bytes(), log.read_bytes()), (b"synthetic PDF", b"synthetic log"))

    def test_watchdog_deadline_at_60_seconds_and_poll_interval_are_deterministic(self):
        from slcn.compilefix import tool_runner

        for elapsed, expected in ((59.999, 0), (60, 124), (60.001, 124)):
            with self.subTest(elapsed=elapsed):
                event = Mock()
                event.wait.return_value = False
                process = Mock(returncode=0)
                process.poll.side_effect = (None, 0)
                threads = SimpleNamespace(Event=lambda: event, Thread=Mock())
                with (patch.object(tool_runner, "threading", threads),
                      patch.object(tool_runner, "os", SimpleNamespace(name="nt")),
                      patch.object(tool_runner.subprocess, "Popen", return_value=process),
                      patch.object(tool_runner.time, "monotonic", side_effect=(100, 100 + elapsed)),
                      patch.object(sys, "argv", ["tool_runner", "60", "synthetic-tool"])):
                    self.assertEqual(tool_runner.main(), expected)
                event.wait.assert_called_once_with(0.02)
                self.assertEqual(process.kill.call_count, int(expected == 124))

    def test_parent_watchdog_has_five_second_wait_and_cleanup_windows(self):
        from slcn.compilefix import compiler

        with tempfile.TemporaryDirectory(prefix="compilefix-watchdog-cap-") as temporary:
            root = Path(temporary).resolve()
            process = Mock()
            process.wait.side_effect = (0, subprocess.TimeoutExpired("tool_runner", 5), 0)
            with patch.object(compiler.subprocess, "Popen", return_value=process) as launch:
                self.assertEqual(compiler._invoke(["synthetic-tool"], root, {}, ()), 0)
            self.assertEqual(launch.call_args.args[0][-2:], ["60", "synthetic-tool"])
            self.assertEqual(process.wait.call_args_list, [call(timeout=65), call(timeout=5), call()])
            process.stdin.close.assert_called_once_with()
            process.kill.assert_called_once_with()


if __name__ == "__main__":
    unittest.main(verbosity=2)
