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

    from .l1_detailed import collecting_results
    with collecting_results() as results:
        outcome = compile_pdf(out_dir, stem, run_xelatex=run_xelatex)
    if results:
        capture["detailed"] = results[-1]
    return (*outcome, capture)


def export_failure(out_dir, stem, message, *, source, capture, stage_log=None):
    """Expose a redacted preview while preserving convert's failure contract.

    ``stage_log`` is an optional local display record. A confirmed task ID is
    retained *immediately* after ``Store.issue`` returns; later failures report
    that ID, while a failure inside issue() reports no ID at all (the ledger is
    written before its return). KeyboardInterrupt is intentionally not caught.
    """
    def stage(label):
        if stage_log is not None:
            stage_log.append(label)

    if capture is None:
        stage("无capture")
        print("   编译修复任务书未导出：无法绑定本轮候选，请人工排查。", file=sys.stderr)
        return
    issued_id = None
    operation = None
    entered = False
    try:
        stage("建Store")
        store = Store()
        stage("位置校验")
        store.validate_locations(out_dir, source=source)
        stage("轮次配置解析")
        override = os.environ.get("SLCN_COMPILEFIX_ROUND_LIMIT")
        limit = int(override) if override is not None else None
        stage("进入状态操作")
        operation = store.operation(recover_expired=False)
        operation.__enter__()
        entered = True
        stage("签发")
        task_id, raw = store.issue(out_dir, stem, source=source, message=message,
                                   capture=capture, limit=limit)
        issued_id = task_id  # issue() returned -> the ID is confirmed immediately.
        if stage_log is not None:
            stage_log.append(f"签发后确认 ID {issued_id}")
        stage("签发信息输出")
        print(f"   编译修复任务书 {task_id} · SHA-256 {digest(raw)} · 本地保留 30 天")
        stage("预览写出")
        write_stdout(raw)
        stage("预览登记")
        store.record_preview(task_id)
    except Exception:
        # The failing body stage is the last one recorded before closing the
        # state operation. The exit stage is a separate boundary and must not
        # overwrite the failure attribution (card: never misreport the last
        # successful operation as the failing one).
        failure_label = stage_log[-1] if stage_log else None
        if issued_id is None:
            detail = "签发阶段" if (stage_log and "签发" in stage_log) else "来源、状态或本地预览校验阶段"
            print(f"   编译修复任务书未导出：{detail}失败，未取得已确认的 ID，请人工排查。",
                  file=sys.stderr)
        else:
            print(f"   编译修复任务书未导出：任务书已签发，失败阶段{failure_label}，ID {issued_id}，请人工排查。",
                  file=sys.stderr)
        if entered:
            try:
                operation.__exit__(*sys.exc_info())
            except Exception:
                pass
        return
    # Normal path: leaving the state operation is itself a fallible stage.
    stage("退出状态操作")
    try:
        operation.__exit__(None, None, None)
    except Exception:
        print(f"   编译修复任务书未导出：任务书已签发，失败阶段退出状态操作，ID {issued_id}，请人工排查。",
              file=sys.stderr)


from .l1 import compile_with_l1  # noqa: E402 -- initialized Stage A capture is reused
