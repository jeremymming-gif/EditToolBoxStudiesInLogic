"""批 395 B-395：外壳 目录设置.json 的 singlePageToc 默认/往返 + 生成目录勾选框接线
（离线，不建窗口、不跑引擎）。

锁契约：
1. defaults()/normalize() 默认 singlePageToc=True（兼容旧文件缺字段=生成单页）。
2. save → read 往返保留 false，不丢字段（GUI 勾选保存后引擎可读到）。
3. params() 含可见「生成单页目录」勾选框；取消勾选改 values 并标脏，写设置后引擎可读 false。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path）
from services import toc_settings as TS
from ui import toc as TOC
from ui.tokens import Tokens

from test_b121_toc_settings import _make_panel


# ── 1/2. 设置读写往返 ────────────────────────────────────────────────────
def test_defaults_single_page_toc_is_true():
    assert TS.defaults()["singlePageToc"] is True
    assert TS.normalize({"columnName": "示例"})["singlePageToc"] is True
    assert TS.normalize(None)["singlePageToc"] is True


def test_read_missing_field_defaults_true(tmp_path):
    path = tmp_path / TS.FILE_NAME
    path.write_text('{"columnName": "示例", "titleWrapLimit": 22.0}', encoding="utf-8")
    data, err = TS.read(str(path))
    assert err == "" and data["singlePageToc"] is True


def test_save_read_roundtrip_preserves_false(tmp_path):
    path = tmp_path / TS.FILE_NAME
    data = TS.normalize({**TS.defaults(), "singlePageToc": False, "columnName": "示例"})
    ok, saved = TS.save(str(path), data)
    assert ok and saved == str(path)
    loaded, err = TS.read(str(path))
    assert err == ""
    assert loaded["singlePageToc"] is False
    assert loaded["columnName"] == "示例"


def test_normalize_coerces_truthiness():
    assert TS.normalize({"singlePageToc": 0})["singlePageToc"] is False
    assert TS.normalize({"singlePageToc": 1})["singlePageToc"] is True
    assert TS.normalize({"singlePageToc": "false"})["singlePageToc"] is True


# ── 3. GUI 勾选框接线 ─────────────────────────────────────────────────────
def _texts_of(control) -> list:
    out: list = []
    stack = list(control) if isinstance(control, list) else [control]
    while stack:
        node = stack.pop()
        for attr in ("text", "value"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                out.append(value)
        stack.extend(getattr(node, "controls", None) or [])
        content = getattr(node, "content", None)
        if content is not None:
            stack.append(content)
    return out


def _prep_panel(tmp_path, monkeypatch, values=None):
    panel, report, _page = _make_panel(tmp_path)
    if values is not None:
        panel.values = values
        panel._saved_snapshot = panel._snapshot()
    monkeypatch.setattr(_page, "add_picker", lambda *a, **k: ft.Container(),
                        raising=False)
    monkeypatch.setattr(_page, "page", SimpleNamespace(
        run_task=lambda fn, *a, **k: None))
    return panel, report


def test_params_has_default_checked_toggle(tmp_path, monkeypatch):
    panel, _report = _prep_panel(tmp_path, monkeypatch)
    texts = _texts_of(panel.params())
    assert "生成单页目录" in texts, texts[:20]
    assert panel.values["singlePageToc"] is True


def test_toggle_change_marks_dirty_and_persists_false(tmp_path, monkeypatch):
    panel, report = _prep_panel(tmp_path, monkeypatch)
    panel.params()
    panel._on_single_page_toc_change(SimpleNamespace(control=SimpleNamespace(value=False)))
    assert panel.values["singlePageToc"] is False
    assert panel._dirty is True

    target = Path(report).parent / TS.FILE_NAME
    path, error = panel.sync_settings(force=True)
    assert error == "" and path == str(target)
    loaded, read_error = TS.read(str(target))
    assert read_error == "" and loaded["singlePageToc"] is False


def test_loaded_false_reflected_in_params(tmp_path, monkeypatch):
    folder = tmp_path / "期"
    folder.mkdir()
    settings = folder / TS.FILE_NAME
    TS.save(str(settings), {**TS.defaults(), "singlePageToc": False})
    panel, _report = _prep_panel(tmp_path, monkeypatch,
                                  values=TS.read(str(settings))[0])
    panel.params()
    assert panel.values["singlePageToc"] is False
