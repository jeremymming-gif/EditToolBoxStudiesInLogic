"""方案 A：macOS 外层 .app 的 Info.plist 必须注入 ``LSUIElement=true``（静态契约，不真打包）。

根因（已诊断确认，本测试不重复诊断）：Flet 1.0.5 ``AppView.FLET_APP`` 是两进程架构——
无头 Python 服务进程（主程序，仅链接 CoreFoundation，无 AppKit/NSApplication，永不发
启动完成握手）+ 独立合法 AppKit 应用 ``com.appveyor.flet``（Flet.app，画窗口）。
主 app 的 Dock tile 被 ``open -W`` 阻塞的子进程接管，无窗口、不结束启动态 → 灰色 ℵ 图标
持续跳动。解法是给**外层 .app** 的 Info.plist 加 ``LSUIElement=true``，让无头主进程
不再有 Dock tile；彩色 Flet.app 图标不受影响（它有自己的 Info.plist）。

血泪教训：commit 7827515 的 onedir 修复只改了 build-flet.yml、没同步 release.yml，
正式发布包没生效。本测试**同时**检查两个 workflow，缺一即红。

只做文本/YAML 静态断言，绝不触发打包。真机 A/B（ASN 是否随主进程消失、tile 是否还跳）
不在本测试范围，由 lead 另排。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE_YML = WORKFLOWS / "release.yml"
BUILD_FLET_YML = WORKFLOWS / "build-flet.yml"

# 两个工作流都必须满足（防 7827515「只改一个」重演）
WORKFLOW_FILES = (RELEASE_YML, BUILD_FLET_YML)

ADD_LINE = 'Add :LSUIElement bool true'
SET_LINE = 'Set :LSUIElement true'
OUTER_PLIST_DEF = 'PLIST="$APPDIR/Contents/Info.plist"'


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _step(text: str, name_prefix: str) -> str:
    """取某个 step 的完整文本：从 ``- name: <前缀>`` 到下一个 step 之前。"""
    chunks = text.split("\n      - name: ")
    hits = [c for c in chunks if c.startswith(name_prefix)]
    assert len(hits) == 1, (
        f"期望恰有 1 个以 {name_prefix!r} 开头的 step，实际 {len(hits)} 个"
    )
    return hits[0]


def _plist_step(text: str) -> str:
    return _step(text, "Info.plist 收口")


def _sign_step(text: str) -> str:
    return _step(text, "macOS 重签封条")


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_lsui_element_add_and_set_present(path: Path):
    """两个 workflow 的 plist 收口步骤都要有 PlistBuddy 的 Add + Set 兜底。"""
    step = _plist_step(_text(path))
    assert ADD_LINE in step, f"{path.name} 缺少 `{ADD_LINE}`"
    assert SET_LINE in step, f"{path.name} 缺少 `{SET_LINE}`"


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_plutil_grep_covers_lsui_element(path: Path):
    """plutil 自检的 grep 正则必须含 LSUIElement，否则 CI 日志看不到注入结果。"""
    step = _plist_step(_text(path))
    grep_lines = [
        ln for ln in step.splitlines()
        if "plutil -p" in ln and "grep -E" in ln
    ]
    assert grep_lines, f"{path.name} 的 plist 步骤找不到 plutil 自检行"
    for line in grep_lines:
        assert "LSUIElement" in line, (
            f"{path.name} 的 plutil grep 未包含 LSUIElement：{line.strip()}"
        )


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_lsui_element_targets_outer_info_plist_only(path: Path):
    """注入必须作用于外层 $APPDIR/Contents/Info.plist，不得碰 Flet.app 的 plist。"""
    step = _plist_step(_text(path))
    assert OUTER_PLIST_DEF in step, (
        f"{path.name} 的 plist 步骤未定义外层 {OUTER_PLIST_DEF}"
    )
    inject_lines = [
        ln for ln in step.splitlines()
        if "LSUIElement" in ln and "$PB" in ln
    ]
    assert len(inject_lines) == 2, (
        f"{path.name} 期望 2 行 LSUIElement 注入（Add/Set），实际 {len(inject_lines)} 行"
    )
    for line in inject_lines:
        assert '"$PLIST"' in line, (
            f"{path.name} 的 LSUIElement 注入未作用于外层 $PLIST：{line.strip()}"
        )
        assert "Flet.app" not in line and "flet-client" not in line, (
            f"{path.name} 的 LSUIElement 注入不得指向随包 Flet.app：{line.strip()}"
        )
    # 整个 plist 步骤里不允许出现针对内嵌客户端 Info.plist 的注入
    assert "Flet.app/Contents/Info.plist" not in step, (
        f"{path.name} 不得修改 Flet.app 自己的 Info.plist"
    )


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_lsui_element_after_multi_instance_and_inside_plist_step(path: Path):
    """LSUIElement 应放在禁多实例之后（与版本号循环前后均可），紧跟外层 plist 收口。"""
    step = _plist_step(_text(path))
    idx_multi = step.index("LSMultipleInstancesProhibited")
    idx_lsui = step.index("LSUIElement")
    assert idx_multi < idx_lsui, (
        f"{path.name} 的 LSUIElement 注入应排在 LSMultipleInstancesProhibited 之后"
    )


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_signing_chain_preserved_without_deep_resign(path: Path):
    """签名链路原样保留：外层 ad-hoc 重签 + --deep --strict 校验，且不对 flet-client --deep 重签。"""
    text = _text(path)
    step = _sign_step(text)
    assert 'codesign --force --sign - "$APPDIR"' in step, (
        f"{path.name} 缺少外层 ad-hoc 重签"
    )
    assert 'codesign --verify --deep --strict "$APPDIR"' in step, (
        f"{path.name} 缺少 --deep --strict 封条自检"
    )
    # --deep 只允许出现在校验，不允许出现在 --sign 重签（会剥掉 entitlement）
    assert "codesign --force --sign - --deep" not in text, (
        f"{path.name} 不得用 --deep 重签"
    )
    assert "codesign --force --deep" not in text, (
        f"{path.name} 不得用 --deep 重签"
    )
    deep_sign_hits = [
        ln.strip() for ln in step.splitlines()
        if "codesign" in ln and "--deep" in ln
        and "--verify" not in ln and not ln.lstrip().startswith("#")
    ]
    assert not deep_sign_hits, (
        f"{path.name} 的签名步骤里 --deep 只能用于 verify，实际：{deep_sign_hits}"
    )


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_lsui_element_comment_documents_purpose(path: Path):
    """plist 步骤内要有宽松注释，说明无头主进程 / Dock tile / 彩色客户端不受影响。"""
    step = _plist_step(_text(path))
    keywords = ("Dock", "无头", "跳动", "灰色", "Flet 客户端", "Flet.app")
    assert any(kw in step for kw in keywords), (
        f"{path.name} 的 LSUIElement 注入缺少用途注释（关键词：{keywords}）"
    )
    # 注入值是 bool true，不是字符串
    assert re.search(r"Add :LSUIElement bool true", step), (
        f"{path.name} 的 LSUIElement 必须是 bool true"
    )


def test_both_workflows_remain_valid_yaml():
    """两个 workflow 仍是合法 YAML，jobs 结构未被文本编辑破坏。"""
    yaml = pytest.importorskip("yaml")
    for path in WORKFLOW_FILES:
        data = yaml.safe_load(_text(path))
        assert isinstance(data, dict) and "jobs" in data, (
            f"{path.name} YAML 结构异常：缺少 jobs"
        )
        assert "macos" in _text(path), f"{path.name} 缺 macos 构建目标"
