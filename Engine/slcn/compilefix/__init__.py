"""Stage A failure adapter. The manual import and apply entry lives separately."""

import os
import sys
import tempfile
import uuid

from .compiler import LOG_LIMIT
from .files import absolute, digest, within, write_stdout
from .state import Store, capture_compile as _capture_compile, trusted_temporary_roots


def capture_compile(out_dir, stem, *, source):
    try:
        return _capture_compile(out_dir, stem, source=source)
    except Exception:
        return None  # Conversion must still compile when repair capture fails.


def compile_with_capture(compile_pdf, out_dir, stem, *, source):
    capture = capture_compile(out_dir, stem, source=source)
    if capture is None:
        return (*compile_pdf(out_dir, stem), None)

    def run_xelatex(runner, command, cwd, step):
        capture.pop("failure", None)
        before = capture_compile(cwd, stem, source=source)
        transcript = None
        try:
            if before == capture:
                source_dir = absolute(source).parent
                root = next((path for path in trusted_temporary_roots() if not within(path, source_dir)), None)
                if root is not None:
                    transcript = tempfile.TemporaryFile(dir=root)
        except Exception:
            pass  # Failure to capture may not prevent the original invocation.
        if transcript is None:
            return runner(command, cwd=cwd, quiet=True, check=False)
        with transcript:
            result = runner(command, cwd=cwd, quiet=True, check=False, stdout=transcript)
            try:
                if result.returncode and before == capture_compile(cwd, stem, source=source):
                    transcript.seek(0)
                    raw = transcript.read(LOG_LIMIT + 1)
                    if raw and len(raw) <= LOG_LIMIT:
                        capture["failure"] = (uuid.uuid4().hex, step, result.returncode, raw)
            except Exception:
                pass  # A failed preview capture must leave the original compiler result intact.
            return result

    return (*compile_pdf(out_dir, stem, run_xelatex=run_xelatex), capture)


def export_failure(out_dir, stem, message, *, source, capture):
    """Expose a redacted preview while preserving convert's failure contract."""
    if capture is None:
        print("   编译修复任务书未导出：无法绑定本轮候选，请人工排查。", file=sys.stderr)
        return
    try:
        store = Store()
        store.validate_locations(out_dir, source=source)
        override = os.environ.get("SLCN_COMPILEFIX_ROUND_LIMIT")
        limit = int(override) if override is not None else None
        with store.operation(recover_expired=False):
            task_id, raw = store.issue(out_dir, stem, source=source, message=message,
                                       capture=capture, limit=limit)
            print(f"   编译修复任务书 {task_id} · SHA-256 {digest(raw)} · 本地保留 30 天")
            write_stdout(raw)
            store.record_preview(task_id)
    except Exception:
        print("   编译修复任务书未导出：来源、状态或本地预览校验失败，请人工排查。", file=sys.stderr)
