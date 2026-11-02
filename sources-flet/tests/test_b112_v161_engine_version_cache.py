"""v1.6.1：engine_version() 进程内缓存（打包形态启动期多次调用会叠加 onefile 解包开销）。"""
from __future__ import annotations

import types

from services import engine_bridge as EB


def _fake_proc(text: str):
    return types.SimpleNamespace(stdout=text, stderr="", returncode=0)


def test_frozen_version_probed_once_per_exe(monkeypatch, tmp_path):
    exe = str(tmp_path / "SLCNEngine")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _fake_proc("SLCN 引擎 1.6.1 ｜ Darwin 27.0.0 (arm64)\n")

    monkeypatch.setattr(EB, "find_engine_dir", lambda: None)
    monkeypatch.setattr(EB, "find_engine_exe", lambda: exe)
    monkeypatch.setattr(EB.subprocess, "run", fake_run)
    EB.clear_engine_version_cache()

    assert EB.engine_version() == "1.6.1"
    assert EB.engine_version() == "1.6.1"
    assert len(calls) == 1, f"同一 exe 只应探一次，实际 {len(calls)} 次"


def test_cache_key_includes_exe_and_can_be_cleared(monkeypatch, tmp_path):
    exe_a = str(tmp_path / "a" / "SLCNEngine")
    exe_b = str(tmp_path / "b" / "SLCNEngine")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _fake_proc("SLCN 引擎 1.6.1\n")

    monkeypatch.setattr(EB, "find_engine_dir", lambda: None)
    monkeypatch.setattr(EB.subprocess, "run", fake_run)
    EB.clear_engine_version_cache()

    monkeypatch.setattr(EB, "find_engine_exe", lambda: exe_a)
    assert EB.engine_version() == "1.6.1"
    monkeypatch.setattr(EB, "find_engine_exe", lambda: exe_b)
    assert EB.engine_version() == "1.6.1"
    assert len(calls) == 2, "换 exe 后应重新探测"

    EB.clear_engine_version_cache()
    assert EB.engine_version() == "1.6.1"
    assert len(calls) == 3, "清缓存后应重新探测"
