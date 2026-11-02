"""批 113 C：startup_log 每行 JSONL 带 t_ms（模块导入起的毫秒数）。

目的：打包形态的「启动慢」只有逐事件时间戳才能拆段（launch_begin → shell_built →
visible 各花了多久）。要求同进程内单调不减，且不影响原有开关/静默约束。
"""
from __future__ import annotations

import json
import sys
import time

import services.startup_log as SLOG


def _rows(log_file) -> list[dict]:
    return [json.loads(line) for line in
            log_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_every_line_has_t_ms_and_monotonic(tmp_path, monkeypatch):
    log_file = tmp_path / "startup.log"
    monkeypatch.setenv("SLCN_STARTUP_LOG", str(log_file))
    for event in ("launch_begin", "shell_built", "visible"):
        SLOG.log(event)
        time.sleep(0.03)
    rows = _rows(log_file)
    assert [row["event"] for row in rows] == ["launch_begin", "shell_built", "visible"]
    stamps = [row["t_ms"] for row in rows]
    assert all(isinstance(value, (int, float)) for value in stamps)
    assert stamps == sorted(stamps), f"t_ms 非单调不减：{stamps}"
    # 每段 sleep 30ms，t_ms 的间隔应与之同量级（0.1ms 精度，不设过窄区间）
    for before, after in zip(stamps, stamps[1:]):
        assert 10 <= after - before < 2000, f"相邻事件间隔异常：{before} → {after}"
    # 首行距 import 不应是几小时（防止基线被换成绝对时间）
    assert 0 <= stamps[0] < 5 * 60 * 1000


def test_existing_fields_kept_for_backward_compat(tmp_path, monkeypatch):
    log_file = tmp_path / "startup.log"
    monkeypatch.setenv("SLCN_STARTUP_LOG", str(log_file))
    SLOG.log("page_ready", page="home")
    row = _rows(log_file)[0]
    for key in ("event", "ts", "mono", "t_ms", "pid", "frozen"):
        assert key in row, f"缺少兼容字段 {key}"
    assert row["event"] == "page_ready"
    assert row["page"] == "home"
    assert isinstance(row["ts"], float)
    assert isinstance(row["mono"], float)
    assert isinstance(row["pid"], int)
    assert isinstance(row["frozen"], bool)


def test_log_exception_carries_t_ms(tmp_path, monkeypatch):
    log_file = tmp_path / "startup.log"
    monkeypatch.setenv("SLCN_STARTUP_LOG", str(log_file))
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        exc_type, exc, tb = sys.exc_info()
        SLOG.log_exception(exc_type, exc, tb, where="unit-test")
    row = _rows(log_file)[-1]
    assert row["event"] == "exception"
    assert row["where"] == "unit-test"
    assert "RuntimeError: boom" in row["error"]
    assert "t_ms" in row and row["t_ms"] >= 0


def test_off_still_disables(tmp_path, monkeypatch):
    log_file = tmp_path / "startup.log"
    monkeypatch.setenv("SLCN_STARTUP_LOG", "off")
    SLOG.log("page_ready")
    assert not log_file.exists()
    assert SLOG.log_path() is None


def test_bad_path_still_silent(tmp_path, monkeypatch):
    # 父路径是普通文件 → mkdir 必然失败；日志必须静默吞掉（t_ms 也不改变这条约束）。
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("SLCN_STARTUP_LOG", str(blocker / "startup.log"))
    SLOG.log("page_ready")  # 不抛即通过
