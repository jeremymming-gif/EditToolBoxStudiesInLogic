"""P01 · 设置持久化与诊断隐私（2.x 开发自测，配执行卡 R01–R07）。

锁六件事（不建窗口、不碰真实设置文件）：
1. R01 保存失败/高版本拒绝：不记 `settings_saved`、不报「设置已保存」、原文件字节不变；
   成功后 `save_settings()` 返回 True，且只有成功才报成功。
2. R02 显式清空不复活：`set_recent(key, "")` 落空串并写盘；未清项保留；
   路径控件手输/✕ 清空真的写回 store 并参与保存。
3. R03 当前 schema 版本不被 `data["version"]` 覆盖；坏值有界回落、未知字段保留；
   未来版本拒绝改写且原字节不变；首次升级写盘前留旧字节副本并保留文件权限。
4. R04 默认诊断不含设置值/路径/日志正文/Key，仍保留版本、平台与错误类别。
5. R05 附加内容必须显式勾选：勾选后预览/写入才含摘要与日志尾部（Key 仍隐藏）；
   导出写的就是预览文本；写失败不记 `diagnosis_exported`、不报成功。
6. R07 旧版设置加载不重置主题/缩放/Key/路径；首次保存留 `.bak-` 旧字节副本。
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from services import settings_store as SS
from ui import pages as P
from ui import settings_shell as SHELL
from test_b115_issue_settings import _SettingsApp


# ── 夹具 ────────────────────────────────────────────────────────────────
def _store(tmp_path: Path) -> SS.SettingsStore:
    store = SS.SettingsStore(path=tmp_path / "settings.json")
    store.load()
    return store


def _app(store: SS.SettingsStore) -> M.App:
    app = M.App.__new__(M.App)
    app.store = store
    app.current_page_key = "settings"
    app.current_route = "settings"
    app.nav_open = {"page": "settings", "group": ""}
    app.marked: list = []
    app.toasts: list[str] = []
    app.mark = lambda event, **fields: app.marked.append((event, fields))
    app.toast = lambda message: app.toasts.append(message)
    app.refresh_engine_status = lambda: None
    return app


def _marks(app, name: str) -> list:
    return [fields for event, fields in app.marked if event == name]


def _write(path: Path, payload: dict) -> bytes:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path.read_bytes()


def _fake_settings_app(tmp_path: Path, store: SS.SettingsStore) -> _SettingsApp:
    app = _SettingsApp(tmp_path)
    app.store = store
    app.marked: list = []
    app.toasts: list[str] = []
    app.mark = lambda event, **fields: app.marked.append((event, fields))
    app.toast = lambda message: app.toasts.append(message)
    app.copied: list[str] = []
    app.copy_text = lambda value: app.copied.append(value)
    return app


# ── R01：保存失败不得冒充成功 ───────────────────────────────────────────
def test_save_failure_keeps_bytes_and_reports_no_success(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    before = _write(path, {"appVersion": "1.6.0", "flet": {"version": 2, "theme": "dark"}})
    store = _store(tmp_path)
    app = _app(store)

    def _boom(self):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(SS.SettingsStore, "save", _boom)
    assert app.save_settings(toast=True) is False
    assert _marks(app, "settings_saved") == []
    assert _marks(app, "settings_save_error")
    assert not any("已保存" in message for message in app.toasts)
    assert path.read_bytes() == before


def test_future_version_save_is_refused_not_downgraded(tmp_path):
    path = tmp_path / "settings.json"
    before = _write(path, {"flet": {"version": 99, "theme": "dark"}})
    store = _store(tmp_path)
    assert store.unsupported_version == 99
    app = _app(store)

    assert app.save_settings(toast=True) is False
    assert _marks(app, "settings_saved") == []
    assert _marks(app, "settings_save_error")
    assert any("设置未保存" in message for message in app.toasts)
    assert not any("设置已保存" in message for message in app.toasts)
    assert path.read_bytes() == before


def test_save_success_marks_and_returns_true(tmp_path):
    store = _store(tmp_path)
    app = _app(store)
    assert app.save_settings(toast=True) is True
    assert len(_marks(app, "settings_saved")) == 1
    assert app.toasts == ["设置已保存"]
    payload = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert payload["flet"]["version"] == SS.SCHEMA_VERSION
    assert store.dirty is False


# ── R02：显式清空不复活 + 路径控件参与保存 ─────────────────────────────
def test_explicit_clear_survives_reload_and_keeps_others(tmp_path):
    store = _store(tmp_path)
    store.set_recent("single_source", "/tmp/a.tex")
    store.set_recent("single_out", "/tmp/out")
    assert store.save() is True

    again = _store(tmp_path)
    assert again.get("recent.single_source") == "/tmp/a.tex"
    assert again.get("recent.single_out") == "/tmp/out"
    again.set_recent("single_source", "")
    assert again.save() is True

    third = _store(tmp_path)
    assert third.get("recent.single_source") == ""
    assert third.get("recent.single_out") == "/tmp/out"


def test_typed_path_field_writes_store_and_saves(tmp_path):
    store = _store(tmp_path)
    store.set("paths.notes_repo", "/old/notes")
    app = _fake_settings_app(tmp_path, store)
    page = P.SettingsPage(app)

    page._path_field("notes_repo", "私有笔记仓")
    assert page.fields["notes_repo"], "路径控件应登记 field_ref"
    field = page.fields["notes_repo"][0]
    field.value = "  /tmp/typed-notes  "
    field.on_change(SimpleNamespace(control=field))

    assert store.get("paths.notes_repo") == "/tmp/typed-notes"
    assert store.dirty is True
    assert store.save() is True
    payload = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert payload["flet"]["paths"]["notes_repo"] == "/tmp/typed-notes"

    # ✕ 清空（components.thin_clear 合成同一 on_change 事件）也必须写回空串。
    field.value = ""
    field.on_change(SimpleNamespace(control=field))
    assert store.get("paths.notes_repo") == ""
    assert store.save() is True
    payload = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert payload["flet"]["paths"]["notes_repo"] == ""


def test_typed_engine_override_syncs_legacy_key(tmp_path, monkeypatch):
    monkeypatch.delenv("P2_ENGINE", raising=False)
    store = _store(tmp_path)
    app = _fake_settings_app(tmp_path, store)
    page = P.SettingsPage(app)

    page._path_field("engine_override", "引擎目录（可空）")
    field = page.fields["engine_override"][0]
    field.value = "/tmp/engine-override"
    field.on_change(SimpleNamespace(control=field))

    assert store.get("paths.engine_override") == "/tmp/engine-override"
    assert store.get("engine_override") == "/tmp/engine-override"


# ── R03：schema 版本、坏值归一、备份与权限 ─────────────────────────────
def test_saved_schema_version_not_overridden_by_data(tmp_path):
    store = _store(tmp_path)
    store.data["version"] = 1  # 旧值/坏值不得覆盖当前 schema 版本
    assert store.save() is True
    payload = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert payload["flet"]["version"] == SS.SCHEMA_VERSION == 2


def test_bad_values_fall_back_bounded_and_unknown_keys_survive(tmp_path):
    path = tmp_path / "settings.json"
    _write(path, {"flet": {
        "version": 2,
        "theme": "light",
        "zoom": "bad",
        "window": "nope",
        "recent": ["x"],
        "options": {"convert_clean": "yes", "weird_option": 3},
        "paths": {"engine_dir": 5, "notes_repo": 7},
        "last_route": 123,
        "form": {"a": 1},
        "unknown_thing": {"k": 1},
        "ai": {"key": "sk-test", "model": "deepseek-flash"},
    }})
    store = _store(tmp_path)

    assert store.data["zoom"] == pytest.approx(1.0)
    assert store.data["window"]["width"] == 1240
    assert isinstance(store.data["recent"], dict)
    assert store.data["options"]["convert_clean"] is True
    assert store.data["options"]["weird_option"] == 3
    assert store.data["paths"]["engine_dir"] == ""
    assert store.data["paths"]["notes_repo"] == ""
    # 未知字段（含未来字段）不因一次归一被丢掉。
    assert store.data["form"] == {"a": 1}
    assert store.data["unknown_thing"] == {"k": 1}
    assert store.data["ai"]["key"] == "sk-test"


def test_first_save_backs_up_old_bytes_and_keeps_mode(tmp_path):
    path = tmp_path / "settings.json"
    before = _write(path, {"flet": {"version": 1, "zoom": 1.25, "theme": "dark",
                                   "ai": {"key": "sk-old"}}})
    path.chmod(0o640)
    store = _store(tmp_path)
    assert store.version_status == "older"

    assert store.save() is True
    backups = list(tmp_path.glob("settings.json.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before
    if sys.platform == "win32":
        # Windows 的 os.chmod 只映射文件只读属性，POSIX 权限位不可表达
        # （chmod(0o640) 后 S_IMODE 仍为 0o666），因此这里不声称验证权限位；
        # 只验证原子替换后文件仍存在，字节/版本/内容断言见下。macOS/Linux
        # 继续在 else 分支断言 0o640 保留（不弱化原覆盖）。
        assert path.exists()
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["flet"]["version"] == 2
    assert payload["flet"]["zoom"] == pytest.approx(1.25)
    assert payload["flet"]["theme"] == "dark"
    assert payload["flet"]["ai"]["key"] == "sk-old"


# ── R04/R05：默认诊断不含内容；附加内容需显式选择 ─────────────────────
_MARKERS = ("MARKER-FORM-2026", "MARKER-RECENT.docx", "MARKER-ISSUE", "MARKER-LOG-BODY")


def _diagnosis_app(tmp_path: Path):
    store = _store(tmp_path)
    store.data["form"] = {"title": "MARKER-FORM-2026"}
    store.data["recent"]["single_source"] = "/tmp/MARKER-RECENT.docx"
    store.data["issue_state"] = {"note": "MARKER-ISSUE"}
    store.data["ai"] = {"key": "sk-abcdef123456", "model": "deepseek-flash"}
    app = _fake_settings_app(tmp_path, store)
    app.app_version = "1.6.0"
    app.flet_version = "1.0.5"
    app.python_version = "3.14.8"
    app.dpi = 96
    app.window_w = 1160
    app.window_h = 780
    app.page = SimpleNamespace(platform="macOS")
    app.engine_status = SimpleNamespace(ok=True, version="1.6.0", mode="source", problems=[])
    return app, store


def test_default_diagnosis_has_no_settings_or_log_bodies(tmp_path):
    app, _store_obj = _diagnosis_app(tmp_path)
    logs = "== 运行\n❌ 失败：MARKER-LOG-BODY sk-abcdef123456\n"
    envcheck = {"checks": [{"id": "xelatex", "status": "fail"}]}
    text = SHELL.build_diagnosis(app, envcheck, logs)

    for marker in _MARKERS:
        assert marker not in text
    assert "sk-abcdef123456" not in text
    assert "sk-" not in text
    assert "（已隐藏）" in text
    # 仍保留版本、平台与错误类别/状态。
    assert "1.6.0" in text
    assert "macOS" in text
    assert "xelatex: fail" in text
    assert "错误 1" in text


def test_optional_diagnosis_needs_explicit_flags(tmp_path):
    app, store = _diagnosis_app(tmp_path)
    logs = "日志：MARKER-LOG-BODY sk-abcdef123456\n"
    base = SHELL.build_diagnosis(app, {}, logs)
    assert "MARKER-LOG-BODY" not in base and "sk-abcdef123456" not in base

    with_summary = SHELL.build_diagnosis(app, {}, logs, settings_summary=True)
    assert '"theme"' in with_summary
    assert "sk-abcdef123456" not in with_summary
    assert "MARKER-FORM-2026" not in with_summary  # 白名单不含 form
    assert "/tmp/MARKER-RECENT.docx" not in with_summary  # 摘要不含 recent 路径

    with_logs = SHELL.build_diagnosis(app, {}, logs, log_tail=True)
    assert "MARKER-LOG-BODY" in with_logs  # 显式勾选才出现
    assert "sk-abcdef123456" not in with_logs  # 已知 Key 仍隐藏
    assert "（已隐藏）" in with_logs


def test_export_writes_exactly_the_preview_text(tmp_path):
    app, _store_obj = _diagnosis_app(tmp_path)
    path = SHELL.write_diagnosis(app, {}, "log", text="PREVIEW-ONLY-TEXT\n")
    assert Path(path).read_text(encoding="utf-8") == "PREVIEW-ONLY-TEXT\n"
    assert _marks(app, "diagnosis_exported")


def test_export_failure_marks_no_success(tmp_path, monkeypatch):
    app, _store_obj = _diagnosis_app(tmp_path)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(SHELL, "diagnosis_dir", lambda app: blocker / "diagnostics")

    with pytest.raises(OSError):
        SHELL.write_diagnosis(app, {}, "log")
    assert _marks(app, "diagnosis_exported") == []


def test_copy_diagnosis_copies_minimal_text(tmp_path):
    app, _store_obj = _diagnosis_app(tmp_path)
    page = SimpleNamespace(app=app, envcheck_data=None, mark=app.mark)
    SHELL.copy_diagnosis(page)
    assert len(app.copied) == 1
    text = app.copied[0]
    for marker in _MARKERS:
        assert marker not in text
    assert "sk-abcdef123456" not in text
    assert len(_marks(app, "diagnosis_copied")) == 1


# ── R07：Tk 旧顶层 api 段（Key/地址/模型）升级后仍可用 ─────────────────
def test_legacy_tk_api_section_migrates_to_ai(tmp_path):
    path = tmp_path / "settings.json"
    _write(path, {
        "api": {"base": "https://legacy.invalid/v1", "key": "SECRET-LEGACY-KEY",
                "model": "legacy-model"},
        "engineDir": "/opt/legacy-engine",
    })
    store = SS.SettingsStore(path=path)
    store.load()
    assert store.get("ai.key") == "SECRET-LEGACY-KEY"
    assert store.get("ai.base") == "https://legacy.invalid/v1"
    assert store.get("ai.model") == "legacy-model"
    assert store.raw["api"]["key"] == "SECRET-LEGACY-KEY"  # 原键保留，不丢数据
    assert store.save() is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["flet"]["ai"]["key"] == "SECRET-LEGACY-KEY"
    assert payload["api"]["key"] == "SECRET-LEGACY-KEY"


# ── R05：勾选日志尾部时，常见 Key 形状也要打码 ─────────────────────────
def test_log_tail_scrubs_common_key_shapes(tmp_path):
    app, _store_obj = _diagnosis_app(tmp_path)
    logs = ("api_key=ZXCVBNM-1234-SECRET\n"
            "Authorization: Bearer ABCDEF-9999-TOKEN\n"
            "token: plainsecretvalue99\n"
            "ok line\n")
    text = SHELL.build_diagnosis(app, {}, logs, log_tail=True)
    for leaked in ("ZXCVBNM-1234-SECRET", "ABCDEF-9999-TOKEN", "plainsecretvalue99"):
        assert leaked not in text
    assert "（已隐藏）" in text
    assert "ok line" in text
