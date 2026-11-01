"""pytest 接入：注册走查开关；默认 skip，避免 CI / 无桌面环境误红。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

# 非 Windows 不收集走查用例：`harness.py` 导入即 dlopen(user32)（ctypes.WinDLL 解码
# 依赖的库），mac 上会 OSError 打断整个 pytest 会话。Windows 行为保持不变。
collect_ignore = [] if sys.platform.startswith("win") else ["test_gui_auto.py"]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("gui_auto", "GUI 自动走查（真实桌面）")
    group.addoption(
        "--run-gui", action="store_true", default=False,
        help="运行真实桌面 GUI 自动走查（等价于 SLCN_GUI_AUTO=1）",
    )
    group.addoption(
        "--gui-evidence", default=None,
        help="证据根目录（默认 C:\\slcn-work\\p6-2026-11-01\\evidence\\p6-gui-auto）",
    )
    group.addoption(
        "--gui-baseline", default=None,
        help="视觉基线目录（默认 <证据根>/baseline）",
    )
    group.addoption(
        "--gui-update-baseline", action="store_true", default=False,
        help="重写视觉基线（首次运行或界面有意变更后使用）",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "gui_auto: 真实桌面 GUI 自动走查（默认 skip）"
    )


def _gui_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("--run-gui")) or os.environ.get("SLCN_GUI_AUTO") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    enabled = _gui_enabled(config)
    desktop_ok = sys.platform.startswith("win")
    reason = (
        "GUI 自动走查默认跳过：加 --run-gui 或设置 SLCN_GUI_AUTO=1"
        if not enabled
        else "GUI 自动走查需要 Windows 交互桌面"
    )
    for item in items:
        try:
            in_dir = HERE in Path(str(item.path)).resolve().parents
        except OSError:
            in_dir = False
        if not in_dir:
            continue
        item.add_marker(pytest.mark.gui_auto)
        if not enabled or not desktop_ok:
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(scope="session")
def gui_ctx(request: pytest.FixtureRequest):
    """整个走查会话共享：证据目录、基线、报告汇总。"""
    if sys.platform != "win32":
        pytest.skip("GUI 自动走查需要 Windows 交互桌面")
    config = request.config
    from . import harness as H

    evidence = config.getoption("--gui-evidence") or os.environ.get("SLCN_GUI_EVIDENCE")
    evidence_root = Path(evidence) if evidence else H.DEFAULT_EVIDENCE
    baseline = config.getoption("--gui-baseline")
    ctx = H.RunContext(
        evidence_root,
        Path(baseline) if baseline else None,
        update_baseline=bool(config.getoption("--gui-update-baseline")),
    )
    print(f"\n[gui_auto] evidence={ctx.run_dir} baseline={ctx.baseline.root}")
    try:
        yield ctx
    finally:
        ctx.finalize()
        print(f"[gui_auto] report={ctx.run_dir / 'report.json'}")
