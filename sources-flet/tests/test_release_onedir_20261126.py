"""v1.7.2 macOS 启动慢修复：release.yml 的应用壳必须是 onedir（静态契约，不真打包）。

根因（已证）：commit 7827515 只把 ``--pyinstaller-build-args=--onedir`` 加进
``.github/workflows/build-flet.yml``，而真正产出 v1.7.2 发布的
``.github/workflows/release.yml`` 仍用 ``flet pack`` 的 onefile 形态。onefile 的 .app
每次启动都把 C 扩展解压到随机的 ``_MEI*`` 目录，macOS 对 ad-hoc 签名的校验缓存按
路径/inode 命中 —— 随机路径永远冷，36 个扩展逐个付 0.5–2.5s（实测约 30s）。

口径：macOS 应用壳与引擎 sidecar 双双 onedir，C 扩展固定落在
``.app/Contents/Frameworks/``，签名缓存跨启动命中。

只做文本/YAML 静态断言，绝不触发打包。Windows 的 ``flet pack`` 仍是 onefile 明文
exe（无 macOS 签名缓存问题），不在本次范围 —— 因此“禁 onefile”断言限定在
Windows job 之外。
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _text() -> str:
    return RELEASE_YML.read_text(encoding="utf-8")


def _macos_job(text: str) -> str:
    """截取 macos job 段：从 ``\\n  macos:\\n`` 到下一个 job ``\\n  windows:\\n``。"""
    start = text.index("\n  macos:\n")
    end = text.index("\n  windows:\n", start)
    return text[start:end]


def _windows_job(text: str) -> str:
    start = text.index("\n  windows:\n")
    end = text.index("\n  release:\n", start)
    return text[start:end]


def _flet_pack_step(macos_job: str) -> str:
    """macos job 里 ``flet pack`` 那一步的完整文本（含 name 与 run）。"""
    steps = macos_job.split("\n      - name: ")
    hits = [s for s in steps if s.startswith("flet pack")]
    assert len(hits) == 1, f"macos job 里应恰有 1 个 flet pack step，实际 {len(hits)} 个"
    return hits[0]


def test_macos_flet_pack_passes_onedir_via_pyinstaller_build_args():
    """release.yml 的 flet pack 必须用 --pyinstaller-build-args 透传 --onedir。"""
    step = _flet_pack_step(_macos_job(_text()))
    assert "--pyinstaller-build-args=--onedir" in step, (
        "release.yml 的 flet pack 缺少 --pyinstaller-build-args=--onedir："
        "应用壳仍是 onefile，macOS 冷启动会重新解压并重新签名校验"
    )
    # 透传必须排在 --hidden-import 列表之后（镜像 build-flet.yml 的写法：flet 自己追加的
    # 单文件开关在前，PyInstaller 6.x 同 dest 后者生效）
    assert step.index("ui.bib_prefs") < step.index(
        "--pyinstaller-build-args=--onedir"
    ), "--pyinstaller-build-args 必须排在 --hidden-import 列表之后"


def test_macos_flet_pack_step_name_says_onedir_app():
    step = _flet_pack_step(_macos_job(_text()))
    name = step.split("\n", 1)[0]
    assert "onedir .app" in name, f"step 名称应写明 onedir .app，实际：{name!r}"
    assert "onefile" not in name.lower(), f"step 名称不得再写 onefile，实际：{name!r}"


def test_flet_pack_step_documents_why_passthrough_and_onedir():
    """step 内要有注释解释：flet 1.0.5 拒绝直接 --onedir，只能透传；以及为何 onedir。"""
    step = _flet_pack_step(_macos_job(_text()))
    assert "Flet 1.0.5" in step, "缺少 flet 1.0.5 拒绝 -D/--onedir、只能透传的说明"
    assert "PyInstaller" in step, "缺少 PyInstaller 同 dest 后者生效的说明"
    assert "_MEI" in step, "缺少 onefile 随机 _MEI* 解压导致签名缓存永远冷的说明"


def test_no_onefile_outside_windows_job():
    """macOS 应用壳不得再出现 onefile；Windows exe 的 onefile 保持现状（不在本次范围）。"""
    text = _text()
    windows = _windows_job(text)
    outside = text.replace(windows, "", 1)
    lowered = outside.lower()
    assert "onefile" not in lowered, (
        "release.yml 除 Windows job 外不得再出现 onefile："
        f"首个残留位置 {outside.lower().index('onefile')}"
    )


def test_shape_assertion_checks_app_onedir_shape():
    """"macOS 检查 dist" 步骤要断言 .app 是目录形态（Frameworks + >=30 个 .so/.dylib）。"""
    macos = _macos_job(_text())
    anchor = "test -d build-flet/SLCNEngine/_internal"
    assert anchor in macos
    after = macos.split(anchor, 1)[1]
    for needle in (
        "Contents/Frameworks",
        "SO_COUNT",
        '-ge 30',
        "Contents/MacOS/编辑部工具箱",
        "ls -d build-flet/dist/*.app",
    ):
        assert needle in after, f".app 目录形态断言缺少：{needle}"
    # BSD find 兼容：不得用 GNU 独有的 -maxdepth
    assert "-maxdepth" not in after.split("组装发布包", 1)[0], (
        "macOS runner 是 BSD find，.app 形状检查不得使用 -maxdepth"
    )


def test_release_body_says_app_shell_and_sidecar_both_onedir():
    text = _text()
    assert "应用壳与引擎 sidecar 均为 onedir" in text, (
        "Release 正文「启动与体积」必须写明应用壳与 sidecar 均为 onedir"
    )
    assert "应用壳为 `flet pack` onefile" not in text, (
        "Release 正文仍残留旧的「应用壳为 flet pack onefile」口径"
    )


def test_release_body_packaging_bullet_covers_app_shell():
    text = _text()
    assert "macOS 的应用壳与引擎 sidecar 均改 onedir" in text, (
        "「打包与 CI」条目必须说明应用壳与 sidecar 都改 onedir"
    )
    # 同一条目的其余内容保持原样
    assert "CI 增加平台守卫与 macOS runner" in text


def test_release_yml_yaml_structure_intact():
    """YAML 可解析，jobs/on 结构未被这次文本编辑破坏。"""
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(_text())
    assert set(data["jobs"]) == {"test", "macos", "windows", "release"}, (
        f"jobs 结构异常：{sorted(data['jobs'])}"
    )
    # PyYAML 1.1 未加引号的 on: 会被解析成布尔 True
    on = data.get("on") if "on" in data else data.get(True)
    assert on is not None, "缺少 on: 触发配置"
    assert on["push"]["tags"] == ["v*"], f"push tags 异常：{on['push']}"
    assert "workflow_dispatch" in on, "缺少 workflow_dispatch 手动触发"
