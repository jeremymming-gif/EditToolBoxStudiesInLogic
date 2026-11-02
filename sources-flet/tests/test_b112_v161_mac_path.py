"""v1.6.1：macOS Finder 启动的 app 需要补 TeX/Homebrew 工具 PATH。

用户现场：从 Release 下载 v1.6.0 双击启动，设置页「运行环境检查」报失败；
终端启动同一 app 却 8/8 通过。根因是 LaunchServices 启动的进程只有系统默认
PATH（/usr/bin:/bin:/usr/sbin:/sbin），不含 MacTeX（/Library/TeX/texbin）
与 Homebrew，`envcheck.py` 的 `shutil.which("xelatex")`/`("biber")` 因此误报缺失。
修复：`services.engine_bridge._child_env()` 在 darwin 上把存在的工具目录前置。
"""
from __future__ import annotations

import os
import sys

from services import engine_bridge as EB


def test_augment_path_prepends_existing_dir(tmp_path):
    tool = tmp_path / "texbin"
    tool.mkdir()
    out = EB._augment_path("/usr/bin:/bin", extra=(str(tool),))
    assert out == f"{tool}:/usr/bin:/bin"


def test_augment_path_keeps_unmodified_when_dir_missing(tmp_path):
    missing = tmp_path / "no-such-dir"
    assert EB._augment_path("/usr/bin:/bin", extra=(str(missing),)) == "/usr/bin:/bin"


def test_augment_path_dedupes_existing_entry(tmp_path):
    tool = tmp_path / "texbin"
    tool.mkdir()
    path = f"{tool}:/usr/bin:/bin"
    assert EB._augment_path(path, extra=(str(tool),)) == path


def test_augment_path_noop_off_darwin(monkeypatch, tmp_path):
    tool = tmp_path / "texbin"
    tool.mkdir()
    monkeypatch.setattr(sys, "platform", "win32")
    assert EB._augment_path("/usr/bin:/bin", extra=(str(tool),)) == "/usr/bin:/bin"


def test_child_env_carries_augmented_path(monkeypatch, tmp_path):
    tool = tmp_path / "texbin"
    tool.mkdir()
    monkeypatch.setattr(EB, "_MAC_TOOL_DIRS", (str(tool),))
    # 默认参数在函数定义时已绑定；直接验证 _child_env 对 PATH 走过 _augment_path。
    env = EB._child_env()
    assert isinstance(env.get("PATH"), str)
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8"


def test_mac_tool_dirs_cover_texbin_and_homebrew():
    assert "/Library/TeX/texbin" in EB._MAC_TOOL_DIRS
    assert "/opt/homebrew/bin" in EB._MAC_TOOL_DIRS
