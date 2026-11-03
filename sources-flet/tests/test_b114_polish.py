"""批 114 验收期打磨（用户 m02696）：去灰底块 / 真搜索 / 路径输入浏览 + 粘贴。

本文件锁六组契约（全部离线，不建窗口、不跑引擎、不联网）：

1. `ui.search_index` 是「真搜索」的数据层：页面 / 命令 / 最近记录都进索引，
   结果 action 全部可执行；不搜正文、不拿 startup_log 冒充「最近文稿」。
2. `App` 的搜索执行层：即时过滤、无结果提示、Enter 执行第一条、↑↓/Esc 键、
   结果面板含边界说明；每条 action 落到真动作（goto / bib 视图 / 打开路径…）。
3. 输入控件不再有 surface_2 灰底块：text_field / search_field 纸色底 + 描边。
4. 必填语义进 label（「（必填）」），不再出现 `*` 或单独「必填」行。
5. 路径输入统一有「浏览…」按钮 +「可粘贴」提示（Flet 1.0.5 无系统级文件拖放，
   所以不实现拖拽，也不假装支持 —— 见 evidence/impl-notes.md）。
6. 元数据默认展开 + 扁平箭头；日志「只看摘要」是扁平 toggle，不是 Material Switch。
"""
from __future__ import annotations

from types import SimpleNamespace

import flet as ft

import main as M
from services import ai_prefs as AI
from services import settings_store as SS
from ui import ai_panel as AIP
from ui import components as C
from ui import pages as P
from ui import ia_map as IA
from ui import search_index as SI
from ui.ia_map import NAV_GROUPS
from ui.tokens import Tokens


# ── 通用替身 / 遍历 ────────────────────────────────────────────────────────
def _walk(control):
    """递归遍历 controls/content（够本文件的所有容器形状）。"""
    yield control
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        if isinstance(child, (list, tuple)):
            for item in child:
                if isinstance(item, ft.Control):
                    yield from _walk(item)
        elif isinstance(child, ft.Control):
            yield from _walk(child)


def _texts(control) -> list[str]:
    return [str(ctl.value) for ctl in _walk(control) if isinstance(ctl, ft.Text)]


def _button_contents(control) -> list[str]:
    contents: list[str] = []
    for ctl in _walk(control):
        if isinstance(ctl, (ft.TextButton, ft.OutlinedButton, ft.FilledButton)):
            value = getattr(ctl, "content", None)
            if value is not None:
                contents.append(str(value))
    return contents


class _Store:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data if data is not None else {}

    def get(self, key: str, default=None):
        node = self.data
        for part in str(key).split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value) -> None:
        parts = str(key).split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def set_recent(self, key: str, value) -> None:
        self.data.setdefault("recent", {})[key] = value


class _NoopPage:
    def run_task(self, *args, **kwargs) -> None:
        pass

    def update(self) -> None:
        pass

    def show_dialog(self, *args, **kwargs) -> None:
        pass

    def pop_dialog(self) -> None:
        pass


# ── 1. 搜索索引数据层 ─────────────────────────────────────────────────────
def _sample_index():
    return SI.build_index(
        nav_tree=IA.NAV_TREE,
        bib_views=P.BibPage.VIEWS,
        issue_steps=[(step["key"], step["title"]) for step in P.IssuePage.STEPS],
        recent={"single_source": "/tmp/slcn/作者来稿/main.tex"},
        paths={"notes_repo": "/tmp/slcn/EditToolBox-notes"},
    )


def test_search_index_covers_pages_commands_recent_and_paths():
    entries = _sample_index()
    kinds = {entry.kind for entry in entries}
    assert kinds == {"page", "command", "recent", "path"}
    # 父项 6 + 子项 18（单篇 2 + 整期 6 + 引用库 10）；父项 action 仍可直接 goto。
    pages = [entry for entry in entries if entry.kind == "page"]
    parents = {entry.key for entry in pages if ":" not in entry.key}
    assert parents == {"home", "single", "issue", "bib", "settings", "help"}
    assert len(pages) == 6 + len(IA.nav_views("single")) + len(IA.nav_views("issue")) + len(IA.nav_views("bib"))
    assert all(entry.action.startswith("goto:") for entry in pages)
    # 10 个命令 + 引用库 10 视图 + 整期 6 步骤
    commands = [entry for entry in entries if entry.kind == "command"]
    assert len(commands) == 10 + len(P.BibPage.VIEWS) + len(P.IssuePage.STEPS)
    # 最近记录：人话标签 + basename，action 是可直接打开的绝对路径
    recent = next(entry for entry in entries if entry.kind == "recent")
    assert recent.action == "open:/tmp/slcn/作者来稿/main.tex"
    assert "main.tex" in recent.label
    notes = next(entry for entry in entries if entry.kind == "path")
    assert notes.action == "open:/tmp/slcn/EditToolBox-notes"
    assert "编辑仓" in notes.label or "notes" in notes.key
    # 边界：不索引运行日志 / 正文 / startup_log（该模块按设计只有生命周期事件）
    assert all("startup" not in entry.action for entry in entries)


def test_search_results_are_executable_not_fake():
    real_commands = {"theme.toggle", "theme.light", "theme.dark", "engine.refresh",
                     "run.current", "stop.current", "zoom.in", "zoom.out",
                     "zoom.reset", "new.single"}
    for entry in _sample_index():
        assert (entry.action in real_commands
                or entry.action.startswith(("goto:", "bib:", "issue:", "open:"))), (
            f"结果 {entry.label!r} 的 action={entry.action!r} 不可执行")


def test_search_match_is_case_insensitive_and_multilingual():
    entries = _sample_index()
    assert [entry.action for entry in SI.match_entries(entries, "THEME")] == ["theme.toggle"]
    actions = [entry.action for entry in SI.match_entries(entries, "深色")]
    assert "theme.dark" in actions
    # 多 token：所有词都要命中（引用库 + 查库）
    first = SI.match_entries(entries, "引用库 查库")[0]
    assert first.action == "goto:bib:query"
    # 大小写/全角分隔符
    assert [entry.action for entry in SI.match_entries(entries, "Light")] == ["theme.light"]
    assert SI.match_entries(entries, "压根不存在的检索词") == []
    assert SI.match_entries(entries, "   ") == []


def test_search_split_query_and_limit_and_ranking():
    assert SI.split_query("引用库，查  库") == ["引用库", "查", "库"]
    assert SI.split_query("Theme") == ["theme"]
    entries = _sample_index()
    assert len(SI.match_entries(entries, "页面", limit=3)) == 3
    # 同名时页面优先于最近记录（kind boost）
    custom = [
        SI.SearchEntry(kind="recent", key="x", label="设置：旧目录", action="open:/tmp/x"),
        SI.SearchEntry(kind="page", key="settings", label="设置", action="goto:settings"),
    ]
    assert SI.match_entries(custom, "设置")[0].kind == "page"


def test_search_source_note_states_boundary():
    assert "不搜稿件正文" in SI.SOURCE_NOTE
    assert "运行日志" in SI.SOURCE_NOTE


# ── 2. App 搜索执行层（flet 假壳，不建窗口）──────────────────────────────
def _search_app(data: dict | None = None):
    app = M.App.__new__(M.App)
    app.t = Tokens("light", 1.0, 96)
    app.theme = "light"
    app.zoom = 1.0
    app.store = SimpleNamespace(data=data or {})
    app.pages = {key: SimpleNamespace(key=key)
                 for key in ("home", "single", "issue", "bib", "settings", "help")}
    app.current_page_key = "home"
    app._search_query = ""
    app._search_matches = []
    app._search_selected = 0
    app._search_focus = False
    app._search_slot = None
    app._toolbar_search = None
    app.page = _NoopPage()
    app.got: list[str] = []
    app.bib_views: list[tuple] = []
    app.opened: list[str] = []
    app.toasts: list[str] = []
    app.marks: list[tuple] = []
    app.toast = lambda message: app.toasts.append(str(message))
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.goto = lambda key: app.got.append(str(key))
    app.navigate = lambda route, source="nav": app.got.append(str(route))
    app.open_bib_view = lambda view, source="nav": app.bib_views.append((str(view), source))
    app.open_path = lambda path: app.opened.append(str(path))
    app._page_label = lambda key: {"home": "工作台", "settings": "设置",
                                   "single": "单篇稿件"}.get(key, str(key))
    app.set_theme = lambda theme: setattr(app, "theme", str(theme))
    app.set_zoom = lambda zoom: setattr(app, "zoom", float(zoom))
    app.stop_run = lambda: None
    app.refresh_engine_status = lambda: None
    app.rebuild_shell = lambda: None
    return app


def test_app_search_entries_include_real_records():
    app = _search_app({"recent": {"single_out": "/tmp/slcn/out"},
                       "paths": {"notes_repo": "/tmp/slcn/notes"}})
    entries = app._search_entries()
    assert {entry.kind for entry in entries} == {"page", "command", "recent", "path"}
    assert any(entry.action == "goto:single" for entry in entries)
    assert any(entry.action == "goto:bib:query" for entry in entries)
    assert any(entry.action == "goto:issue:toc" for entry in entries)
    assert any(entry.action == "open:/tmp/slcn/out" for entry in entries)


def test_app_search_submit_without_match_toasts_and_keeps_query():
    app = _search_app()
    app._on_search_submit(SimpleNamespace(control=SimpleNamespace(value="zzz 不存在的词")))
    assert app.toasts[-1] == "没有匹配的页面、命令或最近记录"
    assert app._search_query == "zzz 不存在的词"


def test_app_search_submit_runs_first_match():
    app = _search_app()
    app._on_search_submit(SimpleNamespace(control=SimpleNamespace(value="设置")))
    assert app.got == ["settings"]           # 第一条 = 页面「设置」，真的导航
    assert app.toasts[-1] == "已打开「设置」"


def test_app_search_open_missing_record_does_not_fake_success():
    app = _search_app()
    entry = SI.SearchEntry(kind="recent", key="x", label="最近源稿：x.tex",
                           action="open:/不存在的/路径/x.tex")
    app._run_search_entry(entry)
    assert app.opened == []
    assert app.toasts[-1].startswith("记录不存在：")


def test_app_search_entry_dispatches_bib_view_and_theme():
    app = _search_app()
    app._run_search_entry(SI.SearchEntry(kind="command", key="bib.query",
                                         label="引用库 · 查库", action="bib:query"))
    assert app.bib_views == [("query", "search")]
    app._run_search_entry(SI.SearchEntry(kind="command", key="theme.dark",
                                         label="切换到深色模式", action="theme.dark"))
    assert app.theme == "dark"
    app._run_search_entry(SI.SearchEntry(kind="command", key="zoom.in",
                                         label="放大界面", action="zoom.in"))
    assert round(app.zoom, 2) == 1.1


def test_search_panel_has_boundary_note_and_empty_state():
    app = _search_app()
    entries = _sample_index()
    matches = SI.match_entries(entries, "设置")[:3]
    panel = app._search_panel_content("设置", matches)
    texts = _texts(panel)
    assert any(value == SI.SOURCE_NOTE for value in texts)
    assert any("搜索「设置」" in value for value in texts)
    empty = app._search_panel_content("zzz", [])
    assert "没有匹配的页面、命令或最近记录。" in _texts(empty)


def test_search_panel_marks_selected_row_flat():
    app = _search_app()
    matches = SI.match_entries(_sample_index(), "设置")[:2]
    panel = app._search_panel_content("设置", matches)
    selected = [ctl for ctl in panel.controls
                if isinstance(ctl, ft.Container) and ctl.bgcolor is not None]
    assert len(selected) == 1
    assert selected[0].bgcolor == app.t.palette.sel_tint


def test_search_keyboard_updown_and_escape():
    app = _search_app()
    app._search_entries = lambda: [SI.SearchEntry(kind="page", key=f"p{i}", label=f"页{i}",
                                                  action=f"goto:p{i}") for i in range(3)]
    app._search_matches = app._search_entries()
    app._search_focus = True
    assert app.search_key_handle(SimpleNamespace(key="Arrow Down")) is True
    assert app._search_selected == 1
    assert app.search_key_handle(SimpleNamespace(key="Arrow Down")) is True
    assert app._search_selected == 2
    assert app.search_key_handle(SimpleNamespace(key="Arrow Down")) is True
    assert app._search_selected == 0          # 循环
    assert app.search_key_handle(SimpleNamespace(key="Arrow Up")) is True
    assert app._search_selected == 2
    app._search_query = "设置"
    assert app.search_key_handle(SimpleNamespace(key="Escape")) is True
    assert app._search_query == ""
    # 未聚焦时不抢全局热键
    app._search_focus = False
    assert app.search_key_handle(SimpleNamespace(key="Arrow Down")) is False


# ── 3. 输入控件无灰底块 ───────────────────────────────────────────────────
def test_text_field_is_paper_outlined_not_gray_block():
    t = Tokens("light", 1.0, 96)
    field = C.text_field(t)
    assert field.filled is False and field.fill_color is None
    assert field.bgcolor != t.palette.surface_2
    assert isinstance(field.border, dict)
    default = field.border[ft.ControlState.DEFAULT]
    assert isinstance(default, ft.OutlineInputBorder)
    assert default.side.width == t.px(1)
    assert field.height == t.control_h()


def test_search_field_is_paper_with_magnifier():
    t = Tokens("light", 1.0, 96)
    field = C.search_field(t, width=t.px(236))
    assert field.bgcolor == t.palette.surface
    assert field.bgcolor != t.palette.surface_2
    assert field.filled is False and field.fill_color is None
    assert field.prefix_icon == C.ic("SEARCH")
    assert getattr(field, "_search_chrome", False) is True
    assert field.height == t.control_h()


# ── 4. 必填文案：进 label，不用 `*` / 单独一行 ─────────────────────────────
def test_path_picker_labels_use_required_suffix_not_star():
    t = Tokens("light", 1.0, 96)
    required = C.path_picker("源稿件", "", "", t, required=True)
    field = required.controls[0].controls[0]
    assert field.label == "源稿件（必填）"
    assert "*" not in field.label
    assert any("可直接粘贴路径" in value and "浏览" in value
               for value in _texts(required))
    optional = C.path_picker("参考文献 .bib", "", "", t, required=False)
    assert optional.controls[0].controls[0].label == "参考文献 .bib（可空）"
    assert "浏览…" in _button_contents(optional)
    # label 已带后缀时不再重复追加
    dedup = C.path_picker("目录模板（可空）", "", "", t, required=False)
    assert dedup.controls[0].controls[0].label == "目录模板（可空）"


def test_form_field_required_uses_suffix_not_star():
    t = Tokens("light", 1.0, 96)
    wrapped = C.form_field(t, "卷", C.text_field(t), required=True)
    assert wrapped.label == "卷（必填）"
    assert "*" not in wrapped.label
    assert wrapped._form_label == "卷"
    multiline = C.form_field(t, "中文摘要", C.text_field(t, multiline=True), required=True)
    assert isinstance(multiline, ft.Column)
    texts = _texts(multiline)
    assert any("（必填）" in value for value in texts)
    assert all("*" not in value for value in texts)


# ── 5. 单篇稿件：输入区 / 高级参数 / 元数据 / AI / 日志 ───────────────────
class _FakeApp:
    def __init__(self) -> None:
        self.store = _Store()
        self.page = _NoopPage()
        self.running = False
        self.t = Tokens("light", 1.0, 96)
        self.engine_status = SimpleNamespace(ok=True, version="1.6.0",
                                            engine_dir="/tmp/engine",
                                            describe=lambda: "源码引擎 · /tmp/engine")
        self.toasts: list[str] = []
        self.marked: list[tuple] = []

    def toast(self, message: str) -> None:
        self.toasts.append(str(message))

    def mark(self, event: str, **fields) -> None:
        self.marked.append((event, fields))

    def open_path(self, value) -> None:
        pass

    def browse_field(self, *args, **kwargs) -> None:
        pass

    def stop_run(self) -> None:
        self.running = False

    def goto(self, key: str) -> None:
        pass

    def persist(self) -> None:
        pass

    def set_option(self, key: str, value) -> None:
        pass


def _build_single():
    page = P.SinglePage(_FakeApp())
    root = page.build()
    return page, root


def test_single_page_source_and_output_labels_required_and_browsable():
    page, root = _build_single()
    source = page.fields["single_source"][0]
    output = page.fields["single_out"][0]
    assert source._form_label == "源稿件（必填）"
    assert output._form_label == "输出根目录（必填）"
    assert "*" not in source.label and "*" not in output.label
    assert "必填" not in _texts(root) or any("（必填）" in value for value in _texts(root))
    # 两个字段都带「浏览…」按钮；行内含粘贴提示（不假装支持拖拽）
    assert _button_contents(root).count("浏览…") >= 2
    assert any("可直接粘贴路径" in value for value in _texts(root))
    # 去掉了单独的「源稿件必填 · 输出根目录必填」提示行
    assert not any("源稿件必填" in value for value in _texts(root))


def test_single_page_engine_extra_files_have_browse_buttons():
    page, root = _build_single()
    labels = {"convert_bib": "参考文献 .bib（可空）",
              "convert_refs": "附加参考文献 --refs（可空）",
              "convert_sty": "样式文件 --sty（可空）",
              "convert_skeleton": "模板骨架 --skeleton（可空）"}
    fields = {key: page.fields[key][0] for key in labels}
    for key, shown in labels.items():
        assert fields[key]._form_label == shown
    browse_rows = [ctl for ctl in _walk(root) if isinstance(ctl, ft.Row)
                   and any(field in ctl.controls for field in fields.values())]
    assert len(browse_rows) == 4, "4 个引擎附加文件字段应各有一行浏览按钮"
    for row in browse_rows:
        assert "浏览…" in [str(getattr(ctl, "content", "")) for ctl in row.controls]


def test_single_page_metadata_expanded_flat_and_abstracts_no_gray():
    page, root = _build_single()
    section = page._meta_section
    assert isinstance(section, C.CollapsibleSection)
    assert section.expanded is True and section.body.visible is True
    assert isinstance(section._toggle_btn, ft.IconButton)
    # 元数据里所有输入框都不是灰底块；中/英文摘要多行框也在内。
    meta_fields = [ctl for ctl in _walk(section.body.content)
                   if isinstance(ctl, ft.TextField)]
    assert meta_fields, "元数据表单应有输入框"
    for field in meta_fields:
        assert field.filled is False and field.fill_color is None
        assert field.bgcolor != page.t.palette.surface_2
    abstracts = [field for field in meta_fields if field.multiline]
    assert len(abstracts) >= 2, "中文摘要/英文摘要都应是多行输入"
    assert _button_contents(section.body.content).count("预览元数据") >= 1
    assert not [ctl for ctl in _walk(root) if isinstance(ctl, ft.ExpansionTile)]


def test_single_page_ai_inputs_and_log_toolbar_are_flat():
    page, root = _build_single()
    # AI 默认关：卡在，但没有 AI 输入框。
    assert not [ctl for ctl in _walk(root) if isinstance(ctl, ft.Switch)]
    # 日志：「只看摘要」是扁平 TextButton，不是 Material Switch。
    log = page._log
    assert isinstance(log._fold_btn, ft.TextButton)
    assert not isinstance(log._fold_btn, ft.Switch)
    toolbar = log.root.content.controls[0]
    assert not [ctl for ctl in _walk(toolbar) if isinstance(ctl, ft.Switch)]
    assert "清空" in _button_contents(toolbar)


def test_log_console_fold_toggle_switch_semantics_without_gray_pill():
    t = Tokens("light", 1.0, 96)
    log = C.LogConsole(t)
    assert isinstance(log._fold_btn, ft.TextButton)
    assert log._fold_btn.style.color[ft.ControlState.DEFAULT] == t.palette.text_muted
    log._fold_btn.on_click(None)
    assert log._fold_only is True
    assert log._fold_btn.style.color[ft.ControlState.DEFAULT] == t.palette.accent
    log._fold_btn.on_click(None)
    assert log._fold_only is False


def test_collapsible_section_defaults_open_and_toggles_with_flat_icon():
    t = Tokens("light", 1.0, 96)
    section = C.CollapsibleSection(t, title="元数据（可选）", content=C.text("x", t))
    assert section.expanded is True
    assert section.body.visible is True
    assert isinstance(section._toggle_btn, ft.IconButton)
    assert section._toggle_btn.icon == C.ic("EXPAND_LESS")
    section.toggle()
    assert section.expanded is False and section.body.visible is False
    assert section._toggle_btn.icon == C.ic("EXPAND_MORE")


# ── 6. AI 助手：暂存目录 / 回填文件去灰底 + 浏览/选择 ──────────────────────
class _AiFakePage:
    def __init__(self, store) -> None:
        self.app = SimpleNamespace(store=store, running=False, toasts=[],
                                   mark=lambda *a, **k: None, browse_field=lambda *a, **k: None,
                                   persist=lambda: None, open_path=lambda *a, **k: None)
        self.t = Tokens("light", 1.0, 96)
        self.fields = {"single_source": "", "single_out": ""}

    def field(self, key):
        return self.fields.get(key, "")


def _enabled_store(tmp_path, monkeypatch):
    monkeypatch.setenv("P2_SETTINGS", str(tmp_path / "b114-polish-settings.json"))
    store = SS.SettingsStore()
    store.load()
    AI.save(store, {"enabled": True})
    return store


def test_ai_panel_stage_and_reply_inputs_outlined_with_browse(tmp_path, monkeypatch):
    store = _enabled_store(tmp_path, monkeypatch)
    panel = AIP.AiPanel(_AiFakePage(store))
    view = panel.build()
    t = panel.t
    stage = panel._stage_field
    assert stage is not None
    assert stage._form_label == "暂存目录（可空）"
    assert stage.filled is False and stage.fill_color is None
    assert stage.bgcolor != t.palette.surface_2
    reply = panel._reply_fields[0]
    assert reply._form_label == "回填文件（可空）"
    assert reply.filled is False and reply.fill_color is None
    assert reply.bgcolor != t.palette.surface_2
    contents = _button_contents(view)
    for needed in ("浏览…", "自动发现暂存目录", "选择…", "删除"):
        assert needed in contents, f"AI 卡缺少「{needed}」按钮"
    # 暂存目录行内：输入框 + 浏览 + 自动发现
    stage_rows = [ctl for ctl in _walk(view) if isinstance(ctl, ft.Row)
                  and stage in ctl.controls]
    assert stage_rows, "暂存目录输入框应在行内"
    row_contents = [str(getattr(ctl, "content", "")) for ctl in stage_rows[0].controls]
    assert "浏览…" in row_contents and "自动发现暂存目录" in row_contents
