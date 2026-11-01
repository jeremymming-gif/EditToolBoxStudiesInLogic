"""查库高级检索的纯函数与 argv 口径（批 109 W1a）。

只测「取值 → 拼参数 → 文案」这三层，不启动 Flet 页面：控件由 `build()` 重建，
状态在视图对象上，这些规则全在可单测的纯函数里。
"""
from __future__ import annotations

import pytest

from services import engine_bridge as EB
from ui import bib_advanced as BA


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """桩 Engine 目录：让 base_cmd() 走 dev 模式（与 test_engine_bridge 同款）。"""
    root = tmp_path / "Engine"
    root.mkdir()
    for entry in EB.ENTRIES:
        (root / entry).write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("P2_ENGINE", str(root))
    for name in ("P2_ENGINE_EXE", "SLCN_ENGINE_EXE", "P2_ENGINE_PYTHON", "SLCN_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    return root


def args_of(call: EB.EngineCall) -> list[str]:
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


def test_basic_call_argv_and_label(engine):
    call = EB.bibdb_query_basic_call("key", " Fine1975 ", db="D:/notes/bib_db/x.jsonl")
    assert args_of(call) == ["query", "--json", "--key", "Fine1975", "--limit", "10",
                             "--bibtex", "--db", "D:/notes/bib_db/x.jsonl"]
    assert call.label == "引用库 · 查库（key）"


def test_basic_call_doi_and_unknown_mode(engine):
    call = EB.bibdb_query_basic_call("doi", "10.1007/s10992-020-09558-5")
    assert args_of(call)[:3] == ["query", "--json", "--doi"]
    assert call.label.endswith("（DOI）")
    with pytest.raises(ValueError, match="未知的检索方式"):
        EB.bibdb_query_basic_call("author", "x")


def test_advanced_call_argv_order(engine):
    call = EB.bibdb_query_advanced_call(
        terms=[("author", "Smith", "contains"), ("year", "1990-2000", "contains")],
        sort="year", limit=30, strict=False)
    args = args_of(call)
    assert args[0] == "query-advanced"
    assert args[1:5] == ["--author", "Smith", "--mode", "author=contains"]
    assert args[5:9] == ["--year", "1990-2000", "--mode", "year=contains"]
    assert args[9:] == ["--sort", "year", "--limit", "30", "--min-score=0.75",
                        "--bibtex", "--json"]
    assert call.label == "引用库 · 高级检索"


def test_advanced_call_strict_replaces_min_score(engine):
    args = args_of(EB.bibdb_query_advanced_call(terms=[("title", "逻辑", "fuzzy")], strict=True))
    assert "--strict" in args
    assert "--min-score=0.75" not in args
    assert ["--title", "逻辑", "--mode", "title=fuzzy"] == args[1:5]


def test_advanced_skips_blank_rows_and_rejects_all_blank(engine):
    call = EB.bibdb_query_advanced_call(
        terms=[{"field": "author", "value": "  ", "mode": "contains"},
               {"field": "title", "value": "逻辑", "mode": "contains"}])
    assert "--author" not in args_of(call)
    with pytest.raises(ValueError, match="至少要填一行"):
        EB.bibdb_query_advanced_call(terms=[{"field": "author", "value": " ", "mode": "contains"}])


def test_advanced_rejects_bad_field_mode_sort(engine):
    with pytest.raises(ValueError, match="未知的排序方式"):
        EB.bibdb_query_advanced_call(terms=[("title", "x", "contains")], sort="title")
    with pytest.raises(ValueError, match="未知的检索字段"):
        EB.bibdb_query_advanced_call(terms=[("key", "Fine1975", "exact")])
    with pytest.raises(ValueError, match="未知的匹配方式"):
        EB.bibdb_query_advanced_call(terms=[("title", "x", "regex")])


@pytest.mark.parametrize("raw", ["abc", "0", "201", "-1", ""])
def test_limit_validation_message(engine, raw):
    with pytest.raises(ValueError) as info:
        EB.bibdb_query_advanced_call(terms=[("title", "x", "contains")], limit=raw)
    assert str(info.value) == f"limit 必须是 1–200 的整数：{raw!r}"


def test_adv_terms_skips_empty_and_normalizes():
    rows = [{"field": "year", "mode": "fuzzy", "value": ">=2010"},
            {"field": "title", "mode": "contains", "value": ""},
            {"field": "doi", "mode": "fuzzy", "value": "10.1/x"}]
    assert BA.adv_terms(rows) == [("year", ">=2010", "exact"), ("doi", "10.1/x", "exact")]


def test_default_rows_are_author_and_title():
    rows = BA.default_rows()
    assert [(row["field"], row["mode"]) for row in rows] == [("author", "contains"),
                                                             ("title", "contains")]
    assert rows is not BA.DEFAULT_ROWS  # 可变副本，改行不污染常量


def test_normalize_mode_locks_year_doi_alias():
    assert BA.normalize_mode("year", "fuzzy") == "exact"
    assert BA.normalize_mode("doi", "fuzzy") == "exact"
    assert BA.normalize_mode("alias", "fuzzy") == "exact"
    assert BA.normalize_mode("title", "fuzzy") == "fuzzy"
    assert BA.normalize_mode("title", "bogus") == "exact"


def test_hit_view_fallbacks_and_confidence():
    hit = {"key": "K1", "confidence": "low", "record": {
        "key": "K1", "verified": True,
        "fields": {"booktitle": "逻辑研究", "editor": "张三", "date": "2019-05"},
        "sources": [{"issue": "2020-1", "slug": "S01", "file": "a.bib"}]}}
    view = BA.hit_view(hit)
    assert view["title"] == "逻辑研究"
    assert view["author"] == "张三"
    assert view["year"] == "2019"
    assert view["status"] == "已核实（低置信）"
    assert view["verified"] is True
    assert view["source"] == "2020-1 / S01 / a.bib"
    empty = BA.hit_view({"key": "K2", "record": {"key": "K2", "fields": {}}})
    assert empty["title"] == "（无题名）"
    assert empty["author"] == "（无作者）"
    assert empty["source"] == ""


def test_why_lines_three_decimals_and_newline_collapse():
    lines = BA.why_lines([{"field": "title", "mode": "fuzzy", "score": 0.91234,
                           "snippet": "逻辑\n研究"}])
    assert lines == ["[title] fuzzy · 0.912 · 逻辑 研究"]


def test_summary_texts_and_fmt_seconds():
    assert BA.fmt_seconds(0.01) == "很快"
    assert BA.fmt_seconds(1.24) == "1.2 s"
    assert BA.basic_summary(2, 0.01) == "命中 2 条（用时 很快）。"
    assert BA.basic_summary(0, 0.01) == "没有命中。"
    assert BA.basic_miss("key「X」", 0.5) == "没有命中：key「X」（用时 0.5 s）"
    assert BA.advanced_summary(20, 45, True, 0.2) == "命中 20 条，共 45 条匹配，已截断（用时 0.2 s）。"
    assert BA.advanced_summary(3, 3, False, 0.2) == "命中 3 条（用时 0.2 s）。"
    assert BA.advanced_miss("作者「Smith」", 0.2) == "高级检索没有命中：作者「Smith」（用时 0.2 s）"


def test_detail_and_copy_messages():
    view = BA.hit_view({"key": "K1", "verified": False, "record": {"key": "K1", "fields": {}}})
    assert BA.detail_title(None) == "（未选中）"
    assert BA.detail_title(view) == "已选中：K1（未核实）"
    assert BA.copy_done_message("K1") == "已复制 BibTeX（K1）到剪贴板。"
    assert BA.BIBTEX_PLACEHOLDER == "（引擎未返回 BibTeX）"
    assert "没有 BibTeX 文本" in BA.NO_BIBTEX_TEXT


def test_log_lines_include_key_title_and_fields():
    view = BA.hit_view({"key": "K1", "record": {"key": "K1", "verified": True,
                                               "fields": {"title": "逻辑", "author": "张三",
                                                          "year": "1990", "doi": "10.1/x"}}})
    lines = BA.log_lines(view)
    assert lines[:3] == ["key：K1", "题名：逻辑", "作者：张三"]
    assert "doi：10.1/x" in lines


def test_mode_options_drop_fuzzy_for_year_doi_alias():
    assert BA.mode_options("title") == list(EB.QUERY_MATCH_MODES)
    for field in EB.QUERY_NO_FUZZY:
        assert BA.mode_options(field) == [mode for mode in EB.QUERY_MATCH_MODES
                                          if mode != "fuzzy"]
    assert BA.mode_options("not-a-field") == list(EB.QUERY_MATCH_MODES)


def test_field_switch_normalizes_mode_to_legal_option():
    from types import SimpleNamespace

    view = BA.BibQueryView(SimpleNamespace(_render=lambda: None))
    view.rows = [{"field": "title", "mode": "fuzzy", "value": "逻辑"}]
    view._on_field(0, SimpleNamespace(control=SimpleNamespace(value="year")))
    assert view.rows[0]["field"] == "year"
    assert view.rows[0]["mode"] == "exact"
    assert view.rows[0]["mode"] in BA.mode_options("year")
