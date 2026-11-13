"""批 121 2.BP-r2 集成测试：真实引擎装配链 + 真实引用库「原始记录」+ 窄布局可达性。

覆盖 EXECUTION.md v1.3 §12 R2-1..R2-3 的集成证据（不启窗口、不伪造 stats）：

- (a) 真调 ``EB.bibdb_stats_call()``：返回 JSON 只有 ``path`` 没有 ``db``，
      即 DEF-01 的断链源头（Engine/slcn/bibdb_cli.py cmd_stats → store.stats()）；
- (b) 按 ``bib_host._load_stats`` 的原样装配（``stats_data = result["json"]``）后
      ``QueryView._db()`` 必须等于 JSON 里的 path；``_open_log`` 用同一路径；
- (c) 在真实笔记仓的 bibliography.jsonl 上定位 Blackburn2006 的物理行，对话框
      行号/总行数/SHA256/pretty JSON 全部与独立 Python oracle 逐字符一致；
- (d) 三级回退全空时才报「未配置引用库路径」；
- (e) 900 / 360 设计宽：窄布局详情面板吃满父级实际宽（不按设计宽定宽）、详情由
      expand 容器约束高度、正文内部滚动；动作行是面板内 wrap Row，放不下自动折行；
      360 筛选行折行且搜索框 expand（不横向裁切）。

2.BP-r4（R-05，3.BP-r3 FAIL）把 (e) 从「定宽容器 + wrap Row」改为「柔性面板」：
定宽公式只看页面宽、没扣侧栏/内外边距，360×800+zoom1.07 时面板宽 302px 超出真实
内容区 231px，动作行右端被父级裁剪、点不到（verifier 鼠标 sweep 606/615 可点、
624+ 无事件）。现在面板 width=None，由 Container(expand)→Row(STRETCH)→
Container(expand) 拿父级实际宽，动作行 wrap Row 自动折行；四个按钮逐一触发，
验证去编辑/复制 BibTeX/原始记录/打开库文件。真实 Flutter 的 bounds 与鼠标点击由
GUI 冒烟（360x800 + zoom 1.07，见 EVID/gui-selfcheck）另行取证。

只读：真库前后 sha256 必须不变；不 skip、不 xfail、不捕获裸 except。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_query as QV
from ui import components as C
from ui import bib_shell as S
from ui.tokens import Tokens

BUTTON_TYPES = (ft.TextButton, ft.OutlinedButton, ft.FilledButton)
REAL_KEY = "Blackburn2006"
ACTION_LABELS = ("去编辑", "复制 BibTeX", QV.RAW_BUTTON, "打开库文件")


class _FakeStore:
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value


class _FakePage:
    def __init__(self, width: int = 1160) -> None:
        self.on_keyboard_event = None
        self.dialogs = []
        self.tasks = []
        self.width = width

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self):
        return self.dialogs.pop() if self.dialogs else None

    def run_task(self, fn) -> None:
        self.tasks.append(fn)


class _Host:
    """最小宿主：字段名/语义与 ui.bib_host.BibHost 对齐（t/page/app/mark）。"""

    def __init__(self, width: int = 1160, stats=None) -> None:
        self.t = Tokens("light")
        self.page = _FakePage(width)
        self.copied: list[str] = []
        self.opened: list[str] = []
        self.events: list[tuple] = []
        self.app = SimpleNamespace(store=_FakeStore(), notes_repo="", window_w=width,
                                   copy_text=self.copied.append,
                                   open_path=self.opened.append)
        self.stats_data = stats
        self.view = ""

    def mark(self, event, **kwargs) -> None:
        self.events.append((event, dict(kwargs)))

    def _render(self) -> None:
        pass

    def set_view(self, view) -> None:
        self.view = view


@pytest.fixture(autouse=True)
def _clean_dialog_stack():
    C._open_dialogs.clear()
    yield
    C._open_dialogs.clear()


def _walk(control) -> list:
    out = [control]
    for attr in ("title", "content"):
        child = getattr(control, attr, None)
        if child is not None and not isinstance(child, str):
            out.extend(_walk(child))
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


def _find_parent(root, target):
    """控件树里 target 的直接父控件（找不到返回 None）。"""
    children = []
    for attr in ("title", "content"):
        child = getattr(root, attr, None)
        if child is not None and not isinstance(child, str):
            children.append(child)
    children.extend(getattr(root, "controls", None) or ())
    children.extend(getattr(root, "actions", None) or ())
    if target in children:
        return root
    for child in children:
        found = _find_parent(child, target)
        if found is not None:
            return found
    return None


def _instant(view: QV.QueryView) -> None:
    view._schedule_async = lambda fn, *args: asyncio.run(fn(*args))


def _view(width: int = 1160, stats=None) -> tuple[_Host, QV.QueryView]:
    host = _Host(width, stats=stats)
    view = QV.QueryView(host)
    view.activate()
    return host, view


def _select(view: QV.QueryView, key: str = REAL_KEY) -> None:
    view.hits = [{"key": key, "score": 1.0, "verified": True,
                  "record": {"key": key, "fields": {"title": "T"}}}]
    view.loaded = True
    view.selected_key = key


def _real_stats() -> dict:
    """按 bib_host._load_stats 的口径取真实引擎 stats JSON（只读）。"""
    notes = GIT.notes_root("")
    assert notes and Path(notes).is_dir(), "真实验收环境缺少笔记仓"
    result = EB.read_json_command(EB.bibdb_stats_call(), notes=notes)
    assert result.get("ok") is True, result
    return result.get("json") or {}


def _db_sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ── (a) 真实 stats JSON：有 path、无 db（DEF-01 断链源头）────────────────
def test_real_stats_json_has_path_but_no_db_key():
    stats = _real_stats()
    assert "db" not in stats, f"引擎 stats 意外出现 db 键：{stats.get('db')!r}"
    path = str(stats.get("path") or "")
    assert path.endswith("bib_db/bibliography.jsonl"), path
    assert Path(path).is_file(), path
    assert stats.get("records") == 3875, stats.get("records")
    print(f"\n[integration] stats path={path} records={stats.get('records')}")


# ── (b) 原样装配后 _db() 取到 path；_open_log 共用同一路径 ────────────────
def test_assembled_stats_data_resolves_db_path_like_bib_host():
    stats = _real_stats()
    expected = str(stats["path"])
    host = _Host()
    # bib_host._load_stats 的最后一步就是 stats_data = result.get("json") or None
    host.stats_data = stats
    view = QV.QueryView(host)
    view.activate()
    assert view._db() == expected
    view._open_log(REAL_KEY)
    assert host.opened == [expected]

    # 第二级：旧宿主可能只塞 path（没有 db 键）
    host2 = _Host()
    host2.stats_data = {"path": expected, "records": 3875}
    view2 = QV.QueryView(host2)
    assert view2._db() == expected


# ── (c) 真实库定位：行号/总行数/SHA256/pretty 与独立 oracle 一致 ─────────
def test_real_library_raw_record_matches_independent_oracle():
    stats = _real_stats()
    path = str(stats["path"])
    before = _db_sha(path)

    total = 0
    line_no = 0
    raw = ""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            total = number
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("key") == REAL_KEY:
                line_no = number
                raw = line
                total += sum(1 for _ in handle)   # 命中即停后仍要得总行数
                break
    assert line_no > 0, f"真实库里找不到 {REAL_KEY}"
    pretty = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    sha = hashlib.sha256(pretty.encode("utf-8")).hexdigest()

    host, view = _view(stats=stats)
    _select(view)
    _instant(view)
    view._raw_record_open()
    assert len(host.page.dialogs) == 1
    dialog = host.page.dialogs[0]
    texts = _texts(dialog)
    assert f"原始记录 · {REAL_KEY}" in texts
    assert f"行号 {line_no} / {total} · 记录 SHA256" in texts
    assert sha in texts
    field = next(node for node in _walk(dialog)
                 if isinstance(node, ft.TextField) and node.value == pretty)
    assert field.read_only is True and field.multiline is True
    assert host.events[-1][0] == "bib_raw_record_opened"
    assert host.events[-1][1] == {"key": REAL_KEY, "line": line_no, "total": total}

    after = _db_sha(path)
    assert before == after, "集成测试必须零写：真库 sha256 变了"
    print(f"\n[integration] key={REAL_KEY} line={line_no}/{total} sha={sha[:16]}…")


# ── (d) 三级回退全空 → 未配置引用库路径；不读库不崩溃 ────────────────────
def test_no_db_only_when_all_three_sources_empty(monkeypatch):
    monkeypatch.setattr(GIT, "default_db", lambda notes="": "")
    host, view = _view(stats={})
    _select(view, "ghost")
    _instant(view)
    assert view._db() == ""
    view._raw_record_open()
    dialog = host.page.dialogs[0]
    assert QV.RAW_NO_DB in _texts(dialog)
    assert host.events[-1][0] == "bib_raw_record_failed"
    assert host.events[-1][1]["message"] == QV.RAW_NO_DB


# ── (e) 窄布局：柔性详情面板（R-05）+ 动作行可达 + 筛选行折行 ────────────
def _detail_fluid_panel(view: QV.QueryView) -> tuple:
    """返回 (body, panel, 外层 expand 容器, wrap 动作行)。

    R-05 起详情面板不再走「设计宽 × t.px」定宽：面板 width=None，由 _fluid_detail
    包进 Container(expand=True) → Row(STRETCH) → Container(expand) 拿父级实际宽；
    动作行换成 wrap Row 自动折行。这里把这条结构锁住。
    """
    body = view._body()
    panels = [node for node in _walk(body)
              if getattr(node, "_bib_detail_panel", False)]
    assert len(panels) == 1
    panel = panels[0]
    inner = _find_parent(body, panel)
    assert isinstance(inner, ft.Container) and inner.expand is True
    row = _find_parent(body, inner)
    assert isinstance(row, ft.Row)
    assert row.vertical_alignment == ft.CrossAxisAlignment.STRETCH
    outer = _find_parent(body, row)
    assert isinstance(outer, ft.Container) and outer.expand is True
    column = panel.content
    assert isinstance(column, ft.Column) and column.controls
    wrap_row = column.controls[-1]
    return body, panel, outer, wrap_row


def _panel_buttons(panel) -> list:
    """详情面板里的动作按钮（按控件树顺序；对话框不在 panel 内，不会混入）。"""
    return [node for node in _walk(panel) if isinstance(node, BUTTON_TYPES)]


@pytest.mark.parametrize("width", [900, 360])
def test_narrow_detail_actions_reachable_and_body_scrolls(width):
    host, view = _view(width, stats=_real_stats())
    _select(view)
    body, panel, outer, wrap_row = _detail_fluid_panel(view)
    # R-05：面板不写死宽度，交给父级实际宽（父级 = 页面内容区，已扣除侧栏/内外边距）
    assert panel.width is None, "窄窗详情面板不能按设计宽定宽，必须由父级拉伸"
    assert isinstance(wrap_row, ft.Row) and wrap_row.wrap is True
    # 正文内部滚动（S.detail_panel 的 body_ctl → Column(scroll=...)）
    body_ctl = getattr(panel, "_bib_detail_body", None)
    assert isinstance(body_ctl, ft.Container) and body_ctl.expand is True
    assert isinstance(body_ctl.content, ft.Column)
    assert body_ctl.content.scroll is not None
    # 4 个动作：都在面板树内、未禁用、有回调、不写死宽度；同一个 wrap Row 直接子项
    buttons = _panel_buttons(panel)
    assert [str(button.content) for button in buttons] == list(ACTION_LABELS)
    assert [str(child.content) for child in wrap_row.controls] == list(ACTION_LABELS)
    for button in buttons:
        assert button.on_click is not None, f"{width} 宽详情动作 {button.content} 没有回调"
        assert not button.disabled
        assert button.width is None


def test_narrow_detail_actions_all_clickable_at_360():
    """R-03/R-05 的可点击证据：360 档四个动作逐一触发，行为与回调一致。

    3.BP-r2 的 FAIL 是按钮被裁到面板外（AX 无节点）；3.BP-r3 的 FAIL 是定宽面板
    超出父级实际内容宽，按钮中心落在裁剪区外（鼠标点不到）。这里锁住 headless 层
    的接线与柔性结构（面板 width=None、wrap Row 四按钮、回调副作用）；真实 Flutter
    里的可见 bounds 与鼠标中心点击由 GUI 冒烟（360x800 + zoom 1.07）另行取证。
    """
    stats = _real_stats()
    host, view = _view(360, stats=stats)
    _select(view)
    view.hits[0]["bibtex"] = "[bibtex-from-engine]"   # 命中自带 BibTeX 时直接复制
    _instant(view)
    body, panel, outer, row = _detail_fluid_panel(view)
    assert panel.width is None
    assert isinstance(row, ft.Row) and row.wrap is True
    buttons = list(row.controls)
    assert [str(button.content) for button in buttons] == list(ACTION_LABELS)

    buttons[0].on_click(None)                       # 去编辑
    assert host.view == "edit"
    assert host.app.store.data.get("bib_focus_key") == REAL_KEY

    buttons[1].on_click(None)                       # 复制 BibTeX
    assert host.copied == ["[bibtex-from-engine]"]

    buttons[2].on_click(None)                       # 原始记录：真库只读定位 → 对话框
    assert len(host.page.dialogs) == 1
    dialog = host.page.dialogs[0]
    assert f"原始记录 · {REAL_KEY}" in _texts(dialog)
    assert host.events[-1][0] == "bib_raw_record_opened"
    view._raw_record_close()
    assert host.page.dialogs == []

    buttons[3].on_click(None)                       # 打开库文件
    assert host.opened == [str(stats["path"])]


def test_360_filter_row_wraps_without_fixed_widths():
    host, view = _view(360)
    view.loaded = True
    box = view._filter()
    assert getattr(box, "_bib_filter_row", False) is True
    assert box.height is None                     # 折行后高度自适应，不是固定 44
    field = getattr(box, "_bib_filter_field", None)
    assert isinstance(field, ft.TextField)
    assert field.expand is True and field.width is None
    assert isinstance(box.content, ft.Column)
    rows = list(box.content.controls)
    assert len(rows) == 3 and all(isinstance(row, ft.Row) for row in rows)
    texts = _texts(box)
    assert "重新查询" in texts and "高级检索" in texts


def test_wide_layout_keeps_single_row_and_360_detail():
    host, view = _view(1160, stats=_real_stats())
    _select(view)
    field = getattr(view._filter(), "_bib_filter_field", None)
    assert isinstance(field, ft.TextField)
    assert field.width == 352 and not field.expand
    body = view._body()
    panels = [node for node in _walk(body)
              if getattr(node, "_bib_detail_panel", False)]
    assert len(panels) == 1 and panels[0].width == view.t.px(QV.DETAIL_W)
