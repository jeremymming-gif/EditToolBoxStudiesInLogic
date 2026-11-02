"""批 112 v1.6.1：启动诊断日志 + 首启不再自动环境检查。

覆盖用户报告的「启动慢 / 首次启动窗口一闪 / 环境检查失败」三问题的可定位性：
① services.startup_log 把生命周期事件与未捕获异常写用户日志目录（失败静默）；
② App.mark() 对 LIFECYCLE_EVENTS 落盘；after_boot 首尾各一行，便于算阶段耗时；
③ 首启不再自动跑 envcheck（打包形态每次解包 sidecar ≈10s，失败还会误报黄条），
   改为显式入口 + 首启 toast；
④ 冻结形态首启先用系统通知提示「正在准备运行环境」，避免用户以为没启动而反复点击。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import services.startup_log as SLOG


def test_log_writes_jsonl(tmp_path, monkeypatch):
    log_file = tmp_path / "startup.log"
    monkeypatch.setenv("SLCN_STARTUP_LOG", str(log_file))
    SLOG.log("page_ready", page="home")
    SLOG.log("shell_built", zoom=1.0)
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "page_ready"
    assert first["page"] == "home"
    assert isinstance(first["ts"], float)
    assert isinstance(first["pid"], int)
    assert isinstance(first["mono"], float)
    assert isinstance(first["frozen"], bool)
    assert json.loads(lines[1])["event"] == "shell_built"


def test_log_off_disables(tmp_path, monkeypatch):
    log_file = tmp_path / "startup.log"
    monkeypatch.setenv("SLCN_STARTUP_LOG", "off")
    SLOG.log("page_ready")
    assert not log_file.exists()


def test_log_never_raises_on_bad_path(tmp_path, monkeypatch):
    # 父路径是普通文件 → mkdir 必然失败；日志必须静默吞掉。
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("SLCN_STARTUP_LOG", str(blocker / "startup.log"))
    SLOG.log("page_ready")  # 不抛即通过


def test_log_rotates_when_too_large(tmp_path, monkeypatch):
    log_file = tmp_path / "startup.log"
    monkeypatch.setenv("SLCN_STARTUP_LOG", str(log_file))
    log_file.write_text("x" * (SLOG.MAX_BYTES + 10), encoding="utf-8")
    SLOG.log("page_ready")
    backup = log_file.with_name(log_file.name + ".1")
    assert backup.exists()
    assert json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])["event"] == "page_ready"


def test_exception_hook_logs_traceback(tmp_path, monkeypatch):
    log_file = tmp_path / "startup.log"
    monkeypatch.setenv("SLCN_STARTUP_LOG", str(log_file))
    monkeypatch.setattr(sys, "_slcn_startup_log_installed", False, raising=False)
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: None, raising=False)
    SLOG.install()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        exc_type, exc, tb = sys.exc_info()
        sys.excepthook(exc_type, exc, tb)
    payload = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["event"] == "exception"
    assert payload["where"] == "sys.excepthook"
    assert "RuntimeError: boom" in payload["error"]
    assert "Traceback" in payload["traceback"]


def test_lifecycle_events_include_startup_milestones():
    for event in ("launch_begin", "page_ready", "process_start", "shell_built",
                  "first_update_sent", "visible", "engine_status",
                  "envcheck_first_run_skipped", "close_requested", "destroy"):
        assert event in SLOG.LIFECYCLE_EVENTS


def test_main_does_not_auto_run_first_envcheck():
    """首启自动环境检查已移除：源码里只保留「跳过」标记与显式入口。"""
    src = Path(__file__).resolve().parents[1] / "app" / "main.py"
    text = src.read_text(encoding="utf-8")
    assert "envcheck_first_run_skipped" in text
    assert "self.page.run_task(settings_page._envcheck_task)" in text  # P2_ENVCHECK 显式钩子仍在
    # 首启分支不得再直接触发 _envcheck_task：跳过标记后紧跟 toast，而不是 run_task。
    after_first_run = text.split('settings_page = self.pages.get("settings")', 2)[2]
    block = after_first_run.split('self.mark("envcheck_first_run_skipped"', 1)[1][:800]
    assert "run_task(settings_page._envcheck_task)" not in block


def test_launcher_installs_startup_log_and_first_run_notice():
    src = Path(__file__).resolve().parents[1] / "app" / "main.py"
    text = src.read_text(encoding="utf-8")
    launcher = text.split("def _launch() -> None:", 1)[1]
    assert "SLOG.install()" in launcher
    assert "SLOG.log(\"launch_begin\"" in launcher
    assert "first_run_detected" in launcher
    assert "display notification" in launcher
    assert "ft.run(main" in launcher
