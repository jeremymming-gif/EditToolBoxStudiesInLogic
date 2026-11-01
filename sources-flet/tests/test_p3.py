"""P3 增量测试：引用库只读调用 / 环境检查 / JSON 解析 / 窗口钳制 / IA 映射。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _args(call) -> list[str]:
    """取引擎脚本之后的参数（去掉 python -X utf8 <Engine/xxx.py> 前缀）。"""
    for i, item in enumerate(call.argv):
        if str(item).endswith(call.entry):
            return [str(v) for v in call.argv[i + 1:]]
    return [str(v) for v in call.argv]


def test_parse_json_document_finds_last_object():
    from services import engine_bridge as EB

    text = (
        "开始统计…\n"
        "{\"stage\": \"warm\"}\n"
        "verbose line with {not json}\n"
        "{\n  \"aliases\": {\"records\": 2081, \"total\": 2708},\n"
        "  \"bytes\": 3605834,\n"
        "  \"entry_types\": {\"article\": 1808, \"book\": 1020}\n"
        "}\n"
        "完成。\n"
    )
    doc = EB.parse_json_document(text)
    assert isinstance(doc, dict)
    assert doc["bytes"] == 3605834
    assert doc["aliases"]["records"] == 2081
    assert EB.parse_json_document("no json here") is None


def test_bibdb_stats_call_is_read_only():
    from services import engine_bridge as EB

    call = EB.bibdb_stats_call()
    assert call.entry == "bibdb.py"
    assert _args(call) == ["stats", "--json"]
    joined = " ".join(_args(call))
    for forbidden in ("add", "import", "set", "--delete", "write"):
        assert forbidden not in joined


def test_bibdb_query_call_argv_and_limit_clamp():
    from services import engine_bridge as EB

    call = EB.bibdb_query_call(key="Jin1935", limit=500)
    args = _args(call)
    assert args[:2] == ["query", "--json"]
    assert "--key" in args and "Jin1935" in args
    assert args[args.index("--limit") + 1] == "100"
    call2 = EB.bibdb_query_call(title="逻辑", author="金岳霖", year="1935", limit=0)
    args2 = _args(call2)
    assert "--title" in args2 and "--author" in args2 and "--year" in args2
    assert args2[args2.index("--limit") + 1] == "20"


def test_envcheck_call_argv():
    from services import engine_bridge as EB

    assert _args(EB.envcheck_call()) == ["--json"]
    assert _args(EB.envcheck_call(r"C:\tmp\out")) == ["--json", "--out-dir", r"C:\tmp\out"]
    assert EB.envcheck_call().entry == "envcheck.py"


def test_clamp_logical_position_into_work_area():
    from services import winplat

    if not winplat.IS_WIN:
        pytest.skip("Windows only")
    mon = winplat._monitor_work_rect(point=(200, 200))
    assert mon, "应能取到监视器工作区"
    scale = 144 / 96.0
    x, y = winplat.clamp_logical_position(99999, 99999, 400, 300, dpi=144)
    px, py = int(round(x * scale)), int(round(y * scale))
    work = mon["work"]
    assert work["left"] <= px and px + int(round(400 * scale)) <= work["right"]
    assert work["top"] <= py and py + int(round(300 * scale)) <= work["bottom"]
    # 负坐标也钳回工作区
    x2, y2 = winplat.clamp_logical_position(-500, -500, 400, 300, dpi=144)
    assert x2 >= 0 and y2 >= 0


def test_ia_map_p3_status_covers_15_pages():
    from ui import ia_map

    assert len(ia_map.IA_MAP) == 15
    assert len(ia_map.P3_STATUS) == 15
    counts = {"done": 0, "readonly": 0}
    for row in ia_map.IA_MAP:
        assert row["p3_status"] in ("done", "readonly", "deferred")
        counts[row["p3_status"]] = counts.get(row["p3_status"], 0) + 1
    assert counts["readonly"] == 2  # 入藏、待裁决（写库/写 sidecar 留 P4）
    assert counts["done"] == 13
    stats = ia_map.stats()
    assert stats["p3_done"] == 13 and stats["p3_readonly"] == 2
    assert [p["key"] for p in ia_map.P3_PAGES] == ["help", "taskbook"]
    assert stats["unmapped"] == 0


def test_help_page_is_in_main_navigation():
    from ui import ia_map

    keys = [item[0] for group in ia_map.NAV_GROUPS for _, items in [group] for item in items]
    assert "help" in keys
    assert "settings" in keys


def test_settings_store_window_geometry_defaults(tmp_path, monkeypatch):
    from services import settings_store

    monkeypatch.setenv("P2_SETTINGS", str(tmp_path / "s.json"))
    store = settings_store.SettingsStore()
    data, exists = store.load()
    assert exists is False
    assert data["window"]["left"] is None
    assert data["window"]["top"] is None
    store.set("window.left", 120)
    store.set("window.top", -30)
    store.save()
    saved = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert saved["flet"]["window"]["left"] == 120
    assert saved["flet"]["window"]["top"] == -30
