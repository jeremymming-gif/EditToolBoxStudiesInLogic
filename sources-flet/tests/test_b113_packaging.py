"""批113 packaging：sidecar 目录化（PyInstaller --onedir）+ 随包 Flet 客户端（FLET_VIEW_PATH）。

覆盖：
  · engine_bridge 在「目录 + 内部可执行」形态下解析 sidecar（mac .app / Windows 平铺）；
  · 旧 --onefile 文件形态仍是首选，互不回归；
  · 只有壳目录、没有内部可执行时不算命中；
  · packaging/sidecar_check.py 传目录时能解析内部可执行；
  · main._launch() 只在「打包 + macOS + 有随包 Flet.app」时设 FLET_VIEW_PATH；
  · release.yml 的关键口径（--onedir / Resources/SLCNEngine / flet-client / plist 键）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

from services import engine_bridge as EB

REPO_ROOT = Path(__file__).resolve().parents[2]


def _touch_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    path.chmod(0o755)
    return path


def _make_app_bundle(tmp_path: Path) -> tuple[Path, Path]:
    """造 macOS .app 骨架：Contents/MacOS/<名> + Contents/Resources/。"""
    app = tmp_path / "编辑部工具箱.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    return app, _touch_exe(macos / "编辑部工具箱")


def _as_frozen(monkeypatch, app_exe: Path, platform: str) -> None:
    monkeypatch.delenv("P2_ENGINE_EXE", raising=False)
    monkeypatch.delenv("SLCN_ENGINE_EXE", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "executable", str(app_exe))


# ── engine_bridge：目录形态解析 ─────────────────────────────────────────
def test_find_engine_exe_onedir_in_mac_resources(monkeypatch, tmp_path):
    app, app_exe = _make_app_bundle(tmp_path)
    sidecar_dir = app / "Contents" / "Resources" / "SLCNEngine"
    inner = _touch_exe(sidecar_dir / "SLCNEngine")
    (sidecar_dir / "_internal").mkdir()
    _as_frozen(monkeypatch, app_exe, "darwin")

    assert EB.find_engine_exe() == str(inner.resolve())


def test_find_engine_exe_onedir_next_to_windows_exe(monkeypatch, tmp_path):
    exe_dir = tmp_path / "编辑部工具箱"
    exe_dir.mkdir()
    app_exe = _touch_exe(exe_dir / "编辑部工具箱.exe")
    inner = _touch_exe(exe_dir / "SLCNEngine" / "SLCNEngine.exe")
    _as_frozen(monkeypatch, app_exe, "win32")

    assert EB.find_engine_exe() == str(inner.resolve())


def test_find_engine_exe_keeps_onefile_file_form(monkeypatch, tmp_path):
    """旧 --onefile 包：sidecar 就是 exe 同目录的单个可执行文件。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    app_exe = _touch_exe(exe_dir / "app")
    sidecar = _touch_exe(exe_dir / "SLCNEngine")
    _as_frozen(monkeypatch, app_exe, "darwin")

    assert EB.find_engine_exe() == str(sidecar.resolve())


def test_find_engine_exe_ignores_shell_dir_without_inner_exe(monkeypatch, tmp_path):
    """只有壳目录（没内部可执行）时不能命成一个「目录」路径。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    app_exe = _touch_exe(exe_dir / "app")
    (exe_dir / "SLCNEngine").mkdir()
    _as_frozen(monkeypatch, app_exe, "darwin")

    assert EB.find_engine_exe() == ""


def test_base_cmd_uses_inner_exe_for_onedir(monkeypatch, tmp_path):
    app, app_exe = _make_app_bundle(tmp_path)
    inner = _touch_exe(app / "Contents" / "Resources" / "SLCNEngine" / "SLCNEngine")
    _as_frozen(monkeypatch, app_exe, "darwin")

    assert EB.base_cmd("convert.py") == [str(inner.resolve()), "convert.py"]


def test_engine_status_frozen_exe_readable(monkeypatch, tmp_path):
    app, app_exe = _make_app_bundle(tmp_path)
    inner = _touch_exe(app / "Contents" / "Resources" / "SLCNEngine" / "SLCNEngine")
    _as_frozen(monkeypatch, app_exe, "darwin")
    EB.clear_engine_version_cache()
    monkeypatch.setattr(EB, "_read_engine_version", lambda d, e: "1.6.1")

    status = EB.engine_status()

    assert status.mode == "frozen" and status.ok
    assert status.frozen_exe == str(inner.resolve())
    assert status.to_dict()["frozen_exe"] == str(inner.resolve())


# ── packaging/sidecar_check.py：目录形态兼容 ────────────────────────────
def _load_sidecar_check():
    path = REPO_ROOT / "sources-flet" / "packaging" / "sidecar_check.py"
    spec = importlib.util.spec_from_file_location("b113_sidecar_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_sidecar_check_resolves_onedir_shell(tmp_path):
    mod = _load_sidecar_check()
    shell = tmp_path / "SLCNEngine"
    inner = _touch_exe(shell / "SLCNEngine")
    (shell / "_internal").mkdir()

    assert mod.resolve_sidecar(shell) == inner.resolve()


def test_sidecar_check_keeps_onefile_file(tmp_path):
    mod = _load_sidecar_check()
    onefile = _touch_exe(tmp_path / "SLCNEngine")
    assert mod.resolve_sidecar(onefile) == onefile.resolve()


# ── main._launch()：FLET_VIEW_PATH ──────────────────────────────────────
def _quiet_launch(monkeypatch):
    main = pytest.importorskip("main")
    monkeypatch.setattr(main.SLOG, "install", lambda *a, **k: None)
    monkeypatch.setattr(main.SLOG, "log", lambda *a, **k: None)
    monkeypatch.setattr(main, "SettingsStore", lambda: types.SimpleNamespace(load=lambda: (None, True)))
    ran: list[tuple] = []
    monkeypatch.setattr(main.ft, "run", lambda *a, **k: ran.append((a, k)))
    return main, ran


def test_launch_sets_flet_view_path_when_client_bundled(monkeypatch, tmp_path):
    main, ran = _quiet_launch(monkeypatch)
    app, app_exe = _make_app_bundle(tmp_path)
    client = app / "Contents" / "Resources" / "flet-client"
    (client / "Flet.app").mkdir(parents=True)
    _as_frozen(monkeypatch, app_exe, "darwin")
    monkeypatch.delenv("FLET_VIEW_PATH", raising=False)

    main._launch()

    assert os.environ.get("FLET_VIEW_PATH") == str(client.resolve())
    assert ran, "ft.run 应被调用（入口仍交给 Flet）"
    monkeypatch.delenv("FLET_VIEW_PATH", raising=False)


def test_launch_leaves_flet_view_path_unset_without_client(monkeypatch, tmp_path):
    main, _ran = _quiet_launch(monkeypatch)
    _app, app_exe = _make_app_bundle(tmp_path)  # 没有 Resources/flet-client/Flet.app
    _as_frozen(monkeypatch, app_exe, "darwin")
    monkeypatch.delenv("FLET_VIEW_PATH", raising=False)

    main._launch()

    assert "FLET_VIEW_PATH" not in os.environ


def test_launch_dev_mode_is_noop(monkeypatch, tmp_path):
    main, _ran = _quiet_launch(monkeypatch)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("FLET_VIEW_PATH", raising=False)

    main._launch()

    assert "FLET_VIEW_PATH" not in os.environ


# ── release.yml 口径守卫 ────────────────────────────────────────────────
def test_release_workflow_uses_onedir_and_bundles_client():
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    for needle in (
        "--onedir",
        "Contents/Resources/SLCNEngine",
        "FLET_VIEW_PATH",
        "flet-client",
        "ensure_client_cached",
        "LSMultipleInstancesProhibited",
        "CFBundleShortVersionString",
        "CFBundleVersion",
    ):
        assert needle in text, f"release.yml 缺少口径：{needle}"
    assert "--onefile" not in text, "sidecar 构建不得残留 --onefile"
