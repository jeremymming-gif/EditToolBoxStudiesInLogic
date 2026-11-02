"""启动诊断日志（v1.6.1 / B-320）。

目的：打包形态下「窗口一闪而过 / 启动很慢 / 首启很久」只能靠现场日志定位。
本模块把启动生命周期事件与未捕获异常写到用户日志目录，**不记录稿件内容与业务路径**：

  macOS   ~/Library/Logs/编辑部工具箱/startup.log
  Windows %LOCALAPPDATA%\\编辑部工具箱\\Logs\\startup.log
  Linux   ~/.local/state/编辑部工具箱/startup.log

开关（测试/排障用）：
  SLCN_STARTUP_LOG=<path>   写到指定文件（测试用）
  SLCN_STARTUP_LOG=off      关闭（默认开启）

约束：本模块任何失败都必须静默——诊断日志不能反过来影响启动。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

APP_DIRNAME = "编辑部工具箱"
MAX_BYTES = 512 * 1024  # 超过即轮转一次，只留上一份，避免无限增长

#: 模块导入时刻：t_ms 用来量「从 import 起过了多久」，便于定位启动慢在哪一段。
_T0 = time.monotonic()

#: 只把启动/窗口/异常相关事件写盘，避免高频事件把日志刷满。
LIFECYCLE_EVENTS = frozenset({
    "launch_begin",
    "first_run_detected",
    "page_ready",
    "process_start",
    "shell_built",
    "first_update_sent",
    "visible",
    "engine_status",
    "envcheck",
    "envcheck_first_run",
    "envcheck_first_run_skipped",
    "on_show_error",
    "settings_saved",
    "close_requested",
    "destroy",
    "auto_close_begin",
    "client_window",
})


def log_path() -> Path | None:
    """返回日志路径；返回 None 表示关闭。"""
    override = os.environ.get("SLCN_STARTUP_LOG", "").strip()
    if override:
        return None if override.lower() in ("off", "0", "no", "none") else Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_DIRNAME / "startup.log"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / APP_DIRNAME / "Logs" / "startup.log"
    return Path.home() / ".local" / "state" / APP_DIRNAME / "startup.log"


def _rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            backup = path.with_name(path.name + ".1")
            try:
                backup.unlink()
            except OSError:
                pass
            path.rename(backup)
    except OSError:
        pass


def log(event: str, **fields) -> None:
    """写一行 JSONL；任何异常都吞掉（诊断日志不得影响主流程）。"""
    path = log_path()
    if path is None:
        return
    payload = {
        "event": event,
        "ts": time.time(),
        "mono": round(time.monotonic(), 3),
        "t_ms": round((time.monotonic() - _T0) * 1000, 1),
        "pid": os.getpid(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    payload.update(fields)
    try:
        line = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 - 字段不可序列化时降级
        line = json.dumps({"event": event, "ts": payload["ts"], "t_ms": payload["t_ms"],
                           "pid": payload["pid"], "note": "fields_not_serializable"},
                          ensure_ascii=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def log_exception(exc_type, exc, tb, *, where: str = "uncaught") -> None:
    """记录未捕获异常（含 traceback 尾部），随后由调用方继续走默认处理。"""
    try:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
    except Exception:  # noqa: BLE001
        text = f"{exc_type}: {exc}"
    log("exception", where=where, error=f"{getattr(exc_type, '__name__', exc_type)}: {exc}",
        traceback=text[-4000:])


def install() -> None:
    """安装 sys/threading 异常钩子；重复调用安全。"""
    if getattr(sys, "_slcn_startup_log_installed", False):
        return
    sys._slcn_startup_log_installed = True  # type: ignore[attr-defined]

    def _sys_hook(exc_type, exc, tb):  # noqa: ANN001
        log_exception(exc_type, exc, tb, where="sys.excepthook")
        default = sys.__excepthook__
        if default is not None:
            default(exc_type, exc, tb)

    sys.excepthook = _sys_hook

    def _thread_hook(args):  # noqa: ANN001
        name = getattr(getattr(args, "thread", None), "name", "")
        log_exception(args.exc_type, args.exc_value, args.exc_traceback, where=f"thread:{name}")
        default = getattr(threading, "__excepthook__", None)
        if default is not None:
            default(args)

    threading.excepthook = _thread_hook
