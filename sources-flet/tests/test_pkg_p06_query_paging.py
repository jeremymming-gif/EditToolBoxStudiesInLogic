"""P06 包 · 查库分页与复用（GUI 侧契约测试；不启 GUI、不碰真实库）。

覆盖 CONTRACT §6 的 GUI 清单：
- 列表型首查显式 ``offset=0``，续页同一条件快照 + ``offset=_loaded``，``--limit``
  始终是 100（绝不改成 200 冒充「载入更多」）；
- 条件变化（文本/方式/排序/高级提交）重置分页与选择，再走首查；
- 迟到结果 / 取消按 ``_gen`` 世代丢弃，可重跑不串结果；
- ``page.db_sha256`` 变化：拒绝合并、提示「引用库已变化」、给刷新入口；
- ``page.offset`` 错位：拒绝合并，绝不拼接；
- 显示「已载入 X / 共 Y 条」「已全部载入（共 Y 条）」；
- 第 201 条跨页选中 / 复制用引擎 ``hit['bibtex']``（缺失时按 key 向引擎取）/
  转编辑按 ``hit['key']``；
- 查询 / 复制 / 翻页零写库（记录所有 EB 写调用为空）、零 Git。

假引擎只实现 CONTRACT §2/§4 的 JSON 形状，数据全在内存里合成。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

from services import engine_bridge as EB
from ui import bib_query as QV
from ui.tokens import Tokens

LIB_TOTAL = 250
SHA_A = "a" * 64
SHA_B = "b" * 64
DB_PATH = "/tmp/p06-synthetic/bibliography.jsonl"

WRITE_NAMES = [
    "bibdb_import_call",
    "bibdb_import_dir_call",
    "bibdb_resolve_call",
    "bibdb_conflicts_resolve_call",
    "bibdb_edit_call",
    "bibdb_merge_call",
    "bibdb_ack_duplicates_call",
    "dirpush_call",
]


@pytest.fixture(autouse=True)
def _stub_engine(tmp_path, monkeypatch):
    """桩 Engine 目录：让 base_cmd() 走 dev 模式，argv 前缀可稳定剥离。"""
    root = tmp_path / "Engine"
    root.mkdir()
    for entry in EB.ENTRIES:
        (root / entry).write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("P2_ENGINE", str(root))
    for name in ("P2_ENGINE_EXE", "SLCN_ENGINE_EXE", "P2_ENGINE_PYTHON", "SLCN_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    return root


def args_of(call: EB.EngineCall) -> list[str]:
    """去掉解释器/脚本前缀，只留传给引擎的参数（与 test_bib_advanced 同款）。"""
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


def _hit(index: int) -> dict:
    """合成一条引擎 hit：source / verified / bibtex 都是「引擎原值」。"""
    key = f"k{index:03d}"
    verified = index % 2 == 0
    return {
        "key": key,
        "score": float(LIB_TOTAL - index),
        "verified": verified,
        "title": f"分页题名 {index}",
        "author": f"作者 {index}",
        "year": "2024",
        "bibtex": f"@article{{{key},\n  title = {{分页题名 {index}}}\n}}",
        "record": {
            "key": key,
            "entry_type": "article",
            "verified": verified,
            "fields": {
                "title": f"分页题名 {index}",
                "author": f"作者 {index}",
                "year": "2024",
                "doi": f"10.1000/{key}",
            },
            "sources": [{"issue": "2024年第1期", "slug": key}],
        },
    }


class _FakeLibrary:
    """假引擎：按 argv 里的 --offset/--limit 切片，返回 page 六字段（§2）。"""

    def __init__(self, total: int = LIB_TOTAL, sha: str = SHA_A) -> None:
        self.total = total
        self.sha = sha
        self.calls: list[list[str]] = []
        self.on_call = None       # 每次读库前执行（模拟取消 / 条件变化 / 库变化）
        self.page_sha: str | None = None    # 强制某次 page 的 sha
        self.page_offset: int | None = None  # 强制某次 page 的 offset（错位）

    def _paged(self, argv: list[str]) -> bool:
        return argv[0] == "query-advanced" or "--title" in argv

    def __call__(self, call, **_kwargs) -> dict:
        argv = args_of(call)
        self.calls.append(argv)
        if self.on_call is not None:
            self.on_call()
        if not self._paged(argv):
            key = argv[argv.index("--key") + 1] if "--key" in argv else ""
            hits = ([_hit(int(key[1:]))] if key[:1] == "k" and key[1:].isdigit()
                    and int(key[1:]) < self.total else [])
            return {"ok": True, "json": {"query": {"key": key}, "hits": hits,
                                         "count": len(hits)},
                    "code": 0, "lines": [], "error": ""}
        offset = int(argv[argv.index("--offset") + 1]) if "--offset" in argv else 0
        limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 10
        hits = [_hit(i) for i in range(offset, min(offset + limit, self.total))]
        has_more = offset + len(hits) < self.total
        page = {
            "offset": self.page_offset if self.page_offset is not None else offset,
            "limit": limit,
            "count": len(hits),
            "total_matched": self.total,
            "has_more": has_more,
            "next_offset": offset + len(hits) if has_more else None,
            "db_sha256": self.page_sha or self.sha,
        }
        data = {"query": {"limit": limit}, "hits": hits, "count": len(hits),
                "total_matched": self.total, "truncated": has_more, "page": page}
        return {"ok": True, "json": data, "code": 0, "lines": [], "error": ""}


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict = {}

    def set(self, key, value) -> None:
        self.data[key] = value


class _FakePage:
    def __init__(self, width: int = 1160) -> None:
        self.width = width
        self.tasks: list = []
        self.dialogs: list = []
        self.clipboard: list[str] = []

    def run_task(self, fn, *args, **kwargs) -> None:
        self.tasks.append(fn if not args else _partial(fn, *args))

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def set_clipboard(self, text: str) -> None:
        self.clipboard.append(text)


def _partial(fn, *args):
    import functools

    return functools.partial(fn, *args)


class _Host:
    def __init__(self, width: int = 1160) -> None:
        self.t = Tokens("light")
        self.page = _FakePage(width)
        self.app = SimpleNamespace(store=_FakeStore(), notes_repo="", window_w=width,
                                   copy_text=self.page.clipboard.append,
                                   open_path=lambda path: None)
        self.stats_data = {"db": DB_PATH, "records": LIB_TOTAL, "sha256": SHA_A}
        self.view = ""
        self.marks: list[tuple[str, dict]] = []

    def mark(self, event: str, **kwargs) -> None:
        self.marks.append((event, kwargs))

    def _render(self) -> None:
        pass

    def set_view(self, view: str) -> None:
        self.view = view


def _run_tasks(host: _Host) -> None:
    """把页面事件循环里排队的协程跑完（模拟 flet run_task 立即执行）。"""
    while host.page.tasks:
        task = host.page.tasks.pop(0)
        asyncio.run(task())


def _walk(control) -> list:
    out = [control]
    content = getattr(control, "content", None)
    if content is not None:
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    for child in getattr(control, "actions", None) or ():
        out.extend(_walk(child))
    return out


def _texts(control) -> list[str]:
    out: list[str] = []
    for node in _walk(control):
        value = getattr(node, "value", None)
        if isinstance(value, str):
            out.append(value)
        content = getattr(node, "content", None)
        if isinstance(content, str):
            out.append(content)
    return out


def _prepared(monkeypatch, *, total: int = LIB_TOTAL):
    fake = _FakeLibrary(total=total)
    monkeypatch.setattr(EB, "read_json_command", fake)
    host = _Host()
    view = QV.QueryView(host)
    view.activate()
    return fake, host, view


def _load_pages(host: _Host, view, count: int) -> None:
    """跑首查 + (count-1) 次载入更多。"""
    asyncio.run(view._run())
    for _ in range(count - 1):
        view.load_more()
        _run_tasks(host)


# ── bridge：offset 只接线、None 时 argv 逐字不变 ───────────────────────
def test_bridge_offset_appended_right_after_limit():
    call = EB.bibdb_query_basic_call("title", "逻辑", limit=100, offset=200)
    assert args_of(call) == ["query", "--json", "--title", "逻辑", "--limit", "100",
                             "--offset", "200", "--bibtex"]

    adv = EB.bibdb_query_advanced_call(
        terms=[("author", "Smith", "contains")], sort="year", limit=30, offset=100)
    assert args_of(adv)[-9:] == ["--sort", "year", "--limit", "30", "--offset", "100",
                                 "--min-score=0.75", "--bibtex", "--json"]


def test_bridge_offset_none_keeps_legacy_argv():
    call = EB.bibdb_query_basic_call("key", "Fine1975", limit=10, bibtex=False)
    assert "--offset" not in call.argv
    assert args_of(call) == ["query", "--json", "--key", "Fine1975", "--limit", "10"]

    adv = EB.bibdb_query_advanced_call(
        terms=[("author", "Smith", "contains")], sort="year", limit=30)
    assert "--offset" not in adv.argv
    assert args_of(adv) == ["query-advanced", "--author", "Smith", "--mode", "author=contains",
                            "--sort", "year", "--limit", "30", "--min-score=0.75",
                            "--bibtex", "--json"]


def test_bridge_offset_rejects_bad_values():
    with pytest.raises(ValueError):
        EB.bibdb_query_basic_call("title", "x", limit=10, offset=-1)
    with pytest.raises(ValueError):
        EB.bibdb_query_advanced_call(terms=[("title", "x", "contains")], offset="nope")


# ── 同条件续页：offset 链 0 → 100 → 200，limit 恒为 100 ────────────────
def test_load_more_uses_offset_chain_same_condition(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "title"
    view.text = "分页"

    asyncio.run(view._run())
    assert view._loaded == 100 and view._total == LIB_TOTAL
    assert view._has_more is True and view._db_sha == SHA_A
    assert view._cond == view._cond_snapshot()
    assert view.rows[0]["key"] == "k000"
    texts = _texts(view.build())
    assert "已载入 100 / 共 250 条" in texts
    assert "载入更多" in texts

    view.load_more()
    _run_tasks(host)
    assert view._loaded == 200 and len(view._items) == 200 == len(view.hits)
    assert view._cond == view._cond_snapshot()
    assert "已载入 200 / 共 250 条" in _texts(view.build())

    view.load_more()
    _run_tasks(host)
    assert view._loaded == 250 and len(view._items) == 250
    assert view._has_more is False
    texts = _texts(view.build())
    assert "已全部载入（共 250 条）" in texts
    assert "载入更多" not in texts

    offsets = [int(argv[argv.index("--offset") + 1]) for argv in fake.calls]
    assert offsets == [0, 100, 200]
    for argv in fake.calls:
        assert argv[argv.index("--limit") + 1] == "100"
        assert argv[argv.index("--title") + 1] == "分页"
    # 翻完页后再点载入更多不会多发请求。
    view.load_more()
    assert host.page.tasks == [] and len(fake.calls) == 3


# ── 条件变化：清空已载入/选择，再走首查 offset=0 ──────────────────────
def test_condition_change_resets_paging_and_selection(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "title"
    view.text = "分页"
    _load_pages(host, view, 2)
    assert view._loaded == 200
    view.selected_key = "k150"

    fake.total = 30
    view._on_text(SimpleNamespace(control=SimpleNamespace(value="新条件")))
    assert view._loaded == 0 and view._items == [] and view.rows == []
    assert view.loaded is False and view._has_more is False
    assert view.selected_key is None and view._cond is None

    asyncio.run(view._run())
    assert fake.calls[-1][fake.calls[-1].index("--offset") + 1] == "0"
    assert view._loaded == 30 and view._total == 30 and view._has_more is False
    assert "已全部载入（共 30 条）" in _texts(view.build())


def test_mode_and_sort_change_reset_paging(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "title"
    view.text = "分页"
    _load_pages(host, view, 2)
    assert view._loaded == 200

    view._on_sort("year", True)
    assert view._loaded == 0 and view._items == [] and view.loaded is False
    assert view.selected_key is None
    assert host.page.tasks, "排序变化应重置分页并触发新首查"
    _run_tasks(host)
    assert view._loaded == 100
    assert fake.calls[-1][fake.calls[-1].index("--offset") + 1] == "0"

    view._on_mode(SimpleNamespace(data="key"))
    assert view._loaded == 0 and view._items == [] and view.loaded is False
    assert host.page.tasks, "方式变化应重置分页并触发新首查"
    _run_tasks(host)
    last = fake.calls[-1]
    assert "--key" in last and "--offset" not in last
    assert view._loaded == 0 and view.loaded is True  # key "分页" 无命中


# ── 迟到结果 / 取消：按世代丢弃，可重跑不串结果 ────────────────────────
def test_cancel_discards_late_result_and_allows_rerun(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "title"
    view.text = "分页"

    def cancel_in_flight() -> None:
        fake.on_call = None
        view.cancel()

    fake.on_call = cancel_in_flight
    asyncio.run(view._run())
    assert view.running is False
    assert view.cancelled is True
    assert view.loaded is False and view._items == [] and view.rows == []

    asyncio.run(view._run())
    assert view.cancelled is False
    assert view.loaded is True and view._loaded == 100
    assert view.rows[0]["key"] == "k000"


def test_late_result_after_condition_change_is_dropped(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "title"
    view.text = "分页"

    def reset_in_flight() -> None:
        fake.on_call = None
        view._reset_paging()  # 模拟在途期间条件变化：旧结果世代过期

    fake.on_call = reset_in_flight
    asyncio.run(view._run())
    assert view.running is False
    assert view._items == [] and view.rows == [] and view.loaded is False
    assert view._loaded == 0
    assert view.cancelled is True


# ── 库在分页之间变化：拒绝合并 + 提示 + 刷新入口 ──────────────────────
def test_sha_change_refuses_merge_with_refresh_entry(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "title"
    view.text = "分页"
    asyncio.run(view._run())
    assert view._loaded == 100 and view._db_sha == SHA_A

    fake.sha = SHA_B  # 第二次读库时库文件已变
    view.load_more()
    _run_tasks(host)
    assert view._loaded == 100 and len(view._items) == 100
    assert view._has_more is False
    assert view._page_error == QV.DB_CHANGED_MSG
    assert "引用库已变化" in view._page_error
    texts = _texts(view.build())
    assert QV.DB_CHANGED_MSG in texts
    assert "刷新后重查" in texts
    assert "载入更多" not in texts

    # 刷新入口 = 按当前库重查（新首查 offset=0，采纳新 sha）。
    view._restart_query()
    _run_tasks(host)
    assert view._page_error == ""
    assert view._db_sha == SHA_B
    assert view._loaded == 100 and view._has_more is True
    assert fake.calls[-1][fake.calls[-1].index("--offset") + 1] == "0"


def test_page_offset_mismatch_is_rejected(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "title"
    view.text = "分页"
    asyncio.run(view._run())

    fake.page_offset = 150  # 引擎返回的页 offset 与已载入 100 不一致
    view.load_more()
    _run_tasks(host)
    assert view._loaded == 100 and len(view._items) == 100
    assert view._has_more is False
    assert "分页位置不一致" in view._page_error
    assert "刷新后重查" in _texts(view.build())


# ── key/doi 单条定位：没有 page、不翻页 ───────────────────────────────
def test_single_lookup_has_no_paging(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "key"
    view.text = "k007"
    asyncio.run(view._run())
    assert "--offset" not in fake.calls[-1]
    assert view._loaded == 1 and view._total == 1
    assert view.total_matched == 1 and view._has_more is False
    assert view.selected_key is None
    texts = _texts(view.build())
    assert "已全部载入（共 1 条）" in texts
    assert "载入更多" not in texts

    view.load_more()
    assert host.page.tasks == [] and len(fake.calls) == 1


# ── 高级检索：page 分页、排序变化重置并换 --sort ──────────────────────
def test_advanced_query_pages_and_sort_resets(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.query_advanced = True
    view.adv_values = {"author": "Smith"}
    view.limit = 100

    asyncio.run(view._run())
    assert fake.calls[-1][0] == "query-advanced"
    assert fake.calls[-1][fake.calls[-1].index("--offset") + 1] == "0"
    assert fake.calls[-1][fake.calls[-1].index("--sort") + 1] == "relevance"
    assert view._loaded == 100 and view._has_more is True

    view.load_more()
    _run_tasks(host)
    assert view._loaded == 200
    assert fake.calls[-1][fake.calls[-1].index("--offset") + 1] == "100"
    assert fake.calls[-1][fake.calls[-1].index("--sort") + 1] == "relevance"

    view._on_sort("year", True)
    assert view._loaded == 0 and view._items == []
    _run_tasks(host)
    last = fake.calls[-1]
    assert last[0] == "query-advanced"
    assert last[last.index("--sort") + 1] == "year"
    assert last[last.index("--offset") + 1] == "0"
    assert view._loaded == 100


# ── 第 201 条：跨页选中 / 引擎 bibtex / 按 key 转编辑 ─────────────────
def test_201st_row_uses_engine_identity(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "title"
    view.text = "分页"
    _load_pages(host, view, 3)
    assert len(view.rows) == 250

    row = view.rows[200]
    assert row["key"] == "k200"
    assert row["source"] == "2024年第1期 · k200"
    assert row["status"] == "已核实"  # verified 原值直传，不改写

    view._row_clicked(row)
    assert view.selected_key == "k200"
    hit = view._selected_hit()
    assert hit is not None and hit["key"] == "k200"
    detail = _texts(view._detail_body())
    assert "k200" in detail and "已核实" in detail

    view._copy_bibtex(hit)
    assert host.page.clipboard[-1] == _hit(200)["bibtex"]

    view._edit("k200")
    assert host.app.store.data["bib_focus_key"] == "k200"
    assert host.app.store.data["bib_nav_origin"] == "query"
    assert host.view == "edit"


def test_copy_bibtex_fetches_from_engine_when_hit_lacks_it(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    view.mode = "title"
    view.text = "分页"
    _load_pages(host, view, 3)

    hit = dict(_hit(201))
    hit.pop("bibtex", None)
    view._copy_bibtex(hit)
    _run_tasks(host)
    # 缺 bibtex 时按 key 向引擎只读取一次，仍然用引擎给的字符串，不自己拼。
    assert fake.calls[-1][:3] == ["query", "--json", "--key"]
    assert fake.calls[-1][fake.calls[-1].index("--key") + 1] == "k201"
    assert host.page.clipboard[-1] == _hit(201)["bibtex"]


# ── 零写库 / 零 Git ──────────────────────────────────────────────────
def test_query_copy_and_paging_never_write(monkeypatch):
    fake, host, view = _prepared(monkeypatch)
    writes: list[str] = []

    def _recorder(name: str):
        def fake_write(*_args, **_kwargs):
            writes.append(name)
            return None
        return fake_write

    for name in WRITE_NAMES:
        monkeypatch.setattr(EB, name, _recorder(name))

    view.mode = "title"
    view.text = "分页"
    _load_pages(host, view, 2)
    view._row_clicked(view.rows[150])
    hit = view._selected_hit()
    view._copy_bibtex(hit)
    _run_tasks(host)
    view._edit("k150")
    view.load_more()
    _run_tasks(host)

    assert writes == [], f"查询/复制/翻页期间不得调用写库或 Git 入口：{writes}"
    assert len(view._items) == 250
    assert all(argv[0] in ("query", "query-advanced") for argv in fake.calls)
