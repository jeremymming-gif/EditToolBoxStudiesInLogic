"""批 110：托盘偏好 / 版本单一来源 / 帮助内容 / 错误文案 / 文件选择失败。"""
from __future__ import annotations

import sys


def test_tray_setting_defaults_off_and_normalizes(tmp_path):
    from services.settings_store import SettingsStore, normalize_tray_enabled

    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    data, exists = store.load()
    assert exists is False
    assert data["tray_enabled"] is False

    store.set("tray_enabled", "YES")
    store.save()
    again, exists = SettingsStore(path).load()
    assert exists is True
    assert again["tray_enabled"] is True

    assert normalize_tray_enabled(True) is True
    assert normalize_tray_enabled("on") is True
    assert normalize_tray_enabled("0") is False
    assert normalize_tray_enabled(None) is False
    assert normalize_tray_enabled("maybe") is False


def test_version_info_prefers_engine_and_falls_back():
    from services import version_info as V

    class _Status:
        def __init__(self, version: str) -> None:
            self.version = version

    # 成功路径：以引擎版本为准（值不变），并缓存
    V.cache_clear()
    info = V.resolve(_Status("1.5.0"))
    assert {"version": info["version"], "source": info["source"],
            "engine_version": info["engine_version"]} == {
        "version": "v1.5.0", "source": "engine", "engine_version": "1.5.0"}
    assert info["available"] is True
    assert info["version_fallback"] is False
    assert V.resolve(_Status("9.9.9"))["version"] == "v1.5.0"  # 进程内缓存

    # 读取失败：显示「未知」且不猜任何版本号
    V.cache_clear()
    fallback = V.resolve(_Status("未知"))
    assert fallback["version"] == "未知" == V.UNKNOWN_VERSION
    assert fallback["source"] == "fallback"
    assert fallback["available"] is False
    assert fallback["version_fallback"] is True
    assert V.available() is False
    assert V.FALLBACK_VERSION == "未知" and not any(
        ch.isdigit() for ch in V.FALLBACK_VERSION)

    # 空串 / 非版本串同样不猜（不硬编码任何具体版本号）
    for raw in ("", "v", "dev", "1.5.0-rc.1!", "latest"):
        V.cache_clear()
        assert V.resolve(_Status(raw))["version"] == "未知", raw
        assert V.resolve(_Status(raw))["available"] is False, raw

    V.cache_clear()
    assert V.app_version(_Status("v1.6.0")) == "v1.6.0"
    V.cache_clear()


def test_help_content_covers_platform_tray_ime_docs_and_license():
    from ui import help_content as HC

    fields = [HC.TITLE, HC.SUBTITLE]
    for name, where, detail in HC.ENTRIES:
        fields.extend((name, where, detail))
    for rows in (HC.PLATFORM, HC.DETAILS, HC.DOCS):
        for row in rows:
            fields.extend(str(item) for item in row)
    fields.extend(HC.TRAY_STEPS + HC.TRAY_LIMITS + HC.BOUNDARY)
    fields.extend((HC.LICENSE, HC.THIRD_PARTY))
    text = "\n".join(fields)

    for needle in ("托盘", "macOS", "输入法", "使用说明.md", "Jeremymming", "MIT",
                   "作者来稿只读", "绝不自动 push"):
        assert needle in text, needle
    assert "技术人员" not in text
    assert "P0" not in text and "验收" not in text


def test_checks_failure_hint_is_actionable():
    from ui import checks

    assert "技术人员" not in checks.FAIL_HINT
    assert "环境检查" in checks.FAIL_HINT


def test_picker_error_toasts_and_marks():
    from main import App

    class _Dummy:
        def __init__(self) -> None:
            self.marks = []
            self.toasts = []

        def mark(self, event, **fields) -> None:
            self.marks.append((event, fields))

        def toast(self, message: str) -> None:
            self.toasts.append(message)

    dummy = _Dummy()
    App._picker_error(dummy, "", cancel=True)
    assert dummy.marks[0][0] == "picker_cancel"
    assert "取消" in dummy.toasts[0]

    App._picker_error(dummy, "RuntimeError: boom")
    assert dummy.marks[-1][0] == "picker_error"
    assert "文件选择器" in dummy.toasts[-1]


def test_tray_start_failure_falls_back_to_off(monkeypatch):
    from services import winplat
    from main import App

    monkeypatch.setitem(sys.modules, "pystray", None)
    monkeypatch.setattr(winplat, "IS_WIN", True)

    class _Store:
        def __init__(self) -> None:
            self.values = {}

        def set(self, key, value) -> None:
            self.values[key] = value

    class _Dummy:
        tray = None
        tray_enabled = True
        tray_active = False

        def __init__(self) -> None:
            self.store = _Store()
            self.marks = []
            self.toasts = []
            self.persisted = 0

        def mark(self, event, **fields) -> None:
            self.marks.append((event, fields))

        def toast(self, message: str) -> None:
            self.toasts.append(message)

        def persist(self) -> None:
            self.persisted += 1

    dummy = _Dummy()
    result = App._start_tray(dummy, reason="setting")
    assert result["started"] is False
    assert dummy.tray is None
    assert dummy.tray_enabled is False
    assert dummy.store.values["tray_enabled"] is False
    assert dummy.persisted == 1
    assert "回退" in dummy.toasts[-1]
    assert any(event == "tray" and fields.get("forced") is False
               for event, fields in dummy.marks)
