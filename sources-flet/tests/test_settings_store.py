"""settings_store 单元测试：默认值 / Tk 旧键迁移 / 保存不清旧字段。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import settings_store as SS


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    monkeypatch.setenv("P2_SETTINGS", str(settings))
    return SS.SettingsStore(), settings


def test_defaults_when_missing(store):
    st, path = store
    data, exists = st.load()
    assert exists is False
    assert data["theme"] in ("light", "dark")
    assert data["options"]["convert_compile"] is True
    # 批 109 Wave 0 / S4：默认只生成 tex（不落位、不编译）。
    assert data["options"]["toc_text_only"] is True
    # S1/S2 关闭的入口不再保留设置键。
    assert "checkbib_merge" not in data["options"]
    assert "cleantemp_trash" not in data["options"]
    assert data["recent"]["single_source"] == ""
    assert path.exists() is False


def test_load_legacy_tk_keys(store):
    st, path = store
    _write(path, {
        "options": {"convertCompile": False, "convertClean": True,
                    "convertVerifyRefs": True, "sourcePdf": False},
        "engineDir": "D:\\Engine",
        "notesRepoPath": "D:\\Notes",
        "logShowRaw": True,
        "envCheckDone": True,
    })
    data, exists = st.load()
    assert exists is True
    assert data["theme"] == "light"          # Tk 版没有主题键 → 用默认
    assert data["options"]["convert_compile"] is False
    assert data["options"]["convert_clean"] is True
    assert data["options"]["convert_verify"] is True
    assert data["engine_override"] == "D:\\Engine"
    assert data["paths"]["notes_repo"] == "D:\\Notes"
    assert data["log_show_raw"] is True
    assert data["env_check_done"] is True


def test_flet_section_wins_over_legacy(store):
    st, path = store
    _write(path, {
        "options": {"convertCompile": False},
        "engineDir": "D:\\LegacyEngine",
        "flet": {"version": 2, "theme": "dark", "options": {"convert_compile": True},
                 "engine_override": "D:\\NewEngine"},
    })
    data, _ = st.load()
    assert data["theme"] == "dark"
    assert data["options"]["convert_compile"] is True
    assert data["engine_override"] == "D:\\NewEngine"


def test_get_set_nested(store):
    st, _ = store
    st.load()
    st.set("theme", "dark")
    st.set("recent.single_source", "D:\\a.tex")
    st.update_options(convert_compile=False)
    assert st.get("theme") == "dark"
    assert st.get("recent.single_source") == "D:\\a.tex"
    assert st.get("options.convert_compile") is False
    st.set_recent("single_source", "D:\\b.tex")
    assert st.get("recent.single_source") == "D:\\b.tex"
    assert st.get("missing.key", "fallback") == "fallback"


def test_save_preserves_legacy_fields(store):
    st, path = store
    _write(path, {"appVersion": "1.4.0", "options": {"convertCompile": False},
                  "api": {"base": "https://example.invalid", "key": "SECRET"}})
    st.load()
    st.set("theme", "dark")
    st.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["appVersion"] == "1.4.0"
    assert raw["api"]["base"] == "https://example.invalid"
    assert raw["options"]["convertCompile"] is False          # 旧字段保留
    assert raw["flet"]["version"] == 2
    assert raw["flet"]["theme"] == "dark"
    assert raw["flet"]["options"]["convert_compile"] is False  # 迁移值


def test_bad_json_falls_back(store):
    st, path = store
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是 json", encoding="utf-8")
    data, exists = st.load()
    assert exists is True                    # 文件存在但不可解析 → 用默认值
    assert data == SS._defaults()


def test_settings_path_env(monkeypatch, tmp_path):
    target = tmp_path / "custom" / "s.json"
    monkeypatch.setenv("P2_SETTINGS", str(target))
    assert SS.settings_path() == target


# ── 批 109 W1b（R6）：bibdb 段（核实成本闸门）──────────────────────────────
@pytest.mark.parametrize("text,value,error", [
    ("", None, None),          # 留空 = 引擎默认 120 条（本次不传 --verify-max）
    ("0", 0, None),            # 0 = 不限，必须真传
    (" 120 ", 120, None),
    ("-1", None, "上限不能是负数（要「不限」请填 0）"),
    ("abc", None, "上限必须是整数（0 = 不限；留空 = 引擎默认 120 条）"),
])
def test_parse_verify_max(text, value, error):
    assert SS.parse_verify_max(text) == (value, error)


def test_bibdb_defaults_zero_argv():
    data = SS._defaults()
    assert data["bibdb"] == {"verify_max": None, "verify_plan": False,
                             "verify_all_refs": False, "lookup_enabled": True}
    # 默认「一个参数都不加」：普通初排 argv 与接入引用库之前逐字节相同。
    assert SS.bibdb_convert_args(data) == {}
    assert SS.bibdb_convert_args({}) == {}


def test_bibdb_convert_args_nondefault():
    cfg = {"bibdb": {"verify_max": 0, "verify_plan": True,
                     "verify_all_refs": True, "lookup_enabled": False}}
    assert SS.bibdb_convert_args(cfg) == {"verify_max": 0, "verify_plan": True,
                                          "verify_all_refs": True, "no_bibdb": True}


def test_verify_max_summary_three_states():
    assert SS.verify_max_summary({"bibdb": {"verify_max": None}}) == \
        "引擎默认 120 条（本次不传 --verify-max）"
    assert SS.verify_max_summary({"bibdb": {"verify_max": 0}}) == "不限（--verify-max 0）"
    assert SS.verify_max_summary({"bibdb": {"verify_max": 50}}) == "50 条（--verify-max）"
    assert SS.verify_max_text({"bibdb": {"verify_max": None}}) == ""
    assert SS.verify_max_text({"bibdb": {"verify_max": 0}}) == "0"


def test_bibdb_legacy_tk_mapping(store):
    st, path = store
    _write(path, {"bibdb": {"verifyMax": 0, "verifyPlan": True,
                            "verifyAllRefs": False, "lookupEnabled": False}})
    data, _ = st.load()
    assert data["bibdb"] == {"verify_max": 0, "verify_plan": True,
                             "verify_all_refs": False, "lookup_enabled": False}
    # 非法旧值（负数）不迁移，回落默认（宁可不带参数也不悄悄改行为）
    _write(path, {"bibdb": {"verifyMax": -5}})
    data, _ = st.load()
    assert data["bibdb"]["verify_max"] is None


def test_bibdb_roundtrip(store):
    st, path = store
    st.load()
    st.set("bibdb.verify_max", 0)
    st.set("bibdb.verify_plan", True)
    st.set("bibdb.lookup_enabled", False)
    st.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["flet"]["bibdb"] == {"verify_max": 0, "verify_plan": True,
                                    "verify_all_refs": False, "lookup_enabled": False}
    data, exists = SS.SettingsStore().load()
    assert exists is True
    assert data["bibdb"]["verify_max"] == 0 and data["bibdb"]["verify_plan"] is True
    # 只新增 bibdb 段，其它键语义不变
    assert data["options"]["convert_compile"] is True


def test_bibdb_effective_rows():
    rows = SS.bibdb_effective_rows({"bibdb": {"verify_max": None}}, db="", db_exists=False)
    assert len(rows) == 4
    assert rows[0][0] == "核实上限"
    assert rows[0][1] == "引擎默认 120 条（本次不传 --verify-max）"
    assert rows[3][1] == "开（但库文件不存在：引擎会优雅跳过查库）"
    off = SS.bibdb_effective_rows({"bibdb": {"lookup_enabled": False}})
    assert off[3][1] == "关（本次传 --no-bibdb）"
