"""引用库薄宿主（批 115 BHOST）。

只做三件事，页面内容全部交给 `bib_<slug>.py` 的 10 个视图模块：

1. 路由：`overview/query/import/normalize/review/edit/dupes/fieldfix/health/settings`
   十个键 → 对应模块类；`bib`/`bib:overview` 由 main 归一到 `overview` 或
   store 里的 `bib_view`（`__init__` 恢复，非法值回落 overview）。
2. 懒加载：第一次进入某视图才 import + 实例化；模块缺失/构建异常只在该视图
   内显示 `state_banner("failure", "页面尚未接入")` 并记日志，绝不拖垮壳。
3. 宿主 API：`app/store/page/notify/run_task_ui/reload_view/navigate_route`，
   以及旧 BibPage 被测试引用的兼容成员（VIEWS/GROUPS/view_slot/_sync_header…）。

设计口径（report §4/§9.2）：整页只有一个 h1，由各视图自己的
`bib_shell.page_shell` 画出；宿主不再套第二层页头。旧「引用库 / 组」面包屑
只在 `_crumb` 里维护供兼容测试/旧 API 读取，`build()` 明确不把它挂到控件树。
"""

from __future__ import annotations

import asyncio
import importlib
import traceback

import flet as ft

from services import engine_bridge as EB
from services import settings_store as SS
from services import startup_log as SLOG
from ui import bib_shell as S
from ui import components as C
from ui import ia_map as IA
from ui.base_page import BasePage

#: 路由 → (模块, 类, 叶名, 角色)。叶名与 ia_map/页面模块的 TITLE 保持一致。
VIEW_SPECS: dict[str, tuple[str, str, str, str]] = {
    "overview": ("ui.bib_overview", "OverviewView", "概览", "readonly"),
    "query": ("ui.bib_query", "QueryView", "查库", "readonly"),
    "normalize": ("ui.bib_normalize", "NormalizeView", "回读校验", "readonly"),
    "import": ("ui.bib_import", "BibImportView", "入藏", "write"),
    "review": ("ui.bib_review", "ReviewView", "待处理", "write"),
    "edit": ("ui.bib_edit", "BibEditView", "编辑", "write"),
    "dupes": ("ui.bib_dupes", "BibDupesView", "查重合并", "write"),
    "fieldfix": ("ui.bib_fieldfix", "BibFieldFixView", "字段归位", "write"),
    "health": ("ui.bib_health", "HealthView", "库健康", "write"),
    "settings": ("ui.bib_prefs", "BibPrefsView", "设置", "write"),
}

#: 批 116 BIB-NAV2：页内二级导航条高度（单行 36）与两行断点（设计宽 <1100）。
BIB_NAV_H = 36
BIB_NAV_NARROW_BELOW = 1100

#: 写库提示的 tooltip 全文（与 pages.BIB_WRITE_RULES 同口径；宿主先于 pages 导入，
#: 不能反向 import，故这里保留一份说明文本，改口径时两处一起改）。
BIB_WRITE_RULES = (
    "W1 写库四步：预览（不写库）→ 确认 → 校验库指纹 → 原子替换。\n"
    "W2 只本地：写成功后自动提交 1 条，只含 bibliography.jsonl；绝不自动 push。\n"
    "W3 概览 / 查库 / 回读校验只读，不写库。"
)

#: 旧 BibPage 属性名 → 新路由。`__getattr__` 返回懒实例化后的视图对象；
#: main.py 仍用 `bib_page.normalize_view`（自动规范化路径）与旧测试用
#: `review_view/settings_view/...`，保留这层别名而不改调用方。
_LEGACY_ATTRS = {
    "query_view": "query",
    "import_view": "import",
    "review_view": "review",
    "editor_view": "edit",
    "dupes_view": "dupes",
    "fieldfix_view": "fieldfix",
    "health_view": "health",
    "settings_view": "settings",
    "normalize_view": "normalize",
    "overview_view": "overview",
}


def load_view_class(module: str, cls: str):
    """import 一个页面模块并取类（测试可 monkeypatch 本函数模拟缺失/半成品）。"""
    mod = importlib.import_module(module)
    return getattr(mod, cls)


class _NormalizeCompat:
    """`bib_page.normalize_view` 的旧接口垫片（main.py `_autorun_normalize` 仍在用）。

    旧 NormalizeView 有 `ensure_built()/set_field()/_on_run()`；新
    `bib_normalize.NormalizeView` 的对应物是 `build()`、`returned` 属性与
    `generate()`。垫片只翻译这三个入口，其余属性透传。
    """

    def __init__(self, host: "BibHost") -> None:
        object.__setattr__(self, "_host", host)

    def _impl(self):
        view = self._host._get_view("normalize")
        if view is None:
            raise RuntimeError("回读校验页未接入")
        return view

    def ensure_built(self):
        return self._impl().build()

    def set_field(self, name: str, value) -> None:
        view = self._impl()
        attr = {"bib_returned": "returned", "bib_original": "original"}.get(name, name)
        setattr(view, attr, str(value or ""))

    def _on_run(self):
        view = self._impl()
        runner = getattr(view, "generate", None)
        if callable(runner):
            return runner()
        return None

    def __getattr__(self, name):
        return getattr(self._impl(), name)


def _walk_controls(control, depth: int = 0):
    """只遍历常用容器字段：页头齿轮在 page_shell 的 header Row 里。"""
    if control is None or depth > 16:
        return
    if isinstance(control, ft.Control):
        yield control
        for attr in ("controls", "content", "actions"):
            value = getattr(control, attr, None)
            if isinstance(value, (list, tuple)):
                for child in value:
                    yield from _walk_controls(child, depth + 1)
            elif isinstance(value, ft.Control):
                yield from _walk_controls(value, depth + 1)


class BibHost(BasePage):
    """引用库 10 视图薄宿主（`pages.BibPage` 继承它，保留旧类名给测试/导入）。"""

    key = "bib"
    #: 旧测试与搜索索引按此顺序/标签建命令（不改）。
    VIEWS = [("overview", "概况"), ("query", "查库"), ("normalize", "规范化"),
             ("import", "入藏"), ("review", "待处理"), ("edit", "编辑"),
             ("dupes", "查重合并"), ("fieldfix", "字段归位"),
             ("health", "库健康"), ("settings", "设置")]
    #: 兼容旧 `set_group`/`group_of` 的 4 组记忆；新侧栏用 ia_map 的 3 组。
    GROUPS = {
        "read": ("查阅", ("overview", "query")),
        "intake": ("采录", ("normalize", "import")),
        "treat": ("治理", ("review", "edit", "dupes", "fieldfix")),
        "maint": ("维护", ("health", "settings")),
    }
    GROUP_ORDER = ("read", "intake", "treat", "maint")
    VIEW_ACCESS = {"overview": "只读", "query": "只读", "normalize": "只读",
                   "import": "写库", "review": "写库", "edit": "写库",
                   "dupes": "写库", "fieldfix": "写库", "health": "写库",
                   "settings": "配置"}
    VIEW_NOTICES = {key: ("info", "预览 → 确认 → 写入；只提交本地。")
                    for key in ("import", "review", "edit", "dupes", "fieldfix", "health")}
    VIEW_LABELS = {key: label for key, label in VIEWS}
    TOOLBAR_TITLE = "引用库"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.view = SS.pick(app.store, "bib_view", self.view_keys(), "overview")
        self.group = self.group_of(self.view)
        # 旧页头兼容对象：不挂控件树（§9.2 单 h1），只给旧 API/测试读。
        self._crumb: ft.Row | None = None
        self._child_title: ft.Text | None = None
        self._notice_slot: ft.Container | None = None
        self._status_slot: ft.Row | None = None
        self.stats_data: dict | None = None
        self.lint_counts: dict | None = None
        self.stats_error = ""
        self.view_slot: ft.Container | None = None
        self._views: dict[str, object] = {}
        self._view_errors: dict[str, str] = {}
        self._stats_loading = False
        self._root: ft.Control | None = None
        self._width_bucket: int | None = None
        self._nav_narrow_cache: bool | None = None
        self._bib_inpage_marked = False
        self._normalize_adapter = _NormalizeCompat(self)

    # ── 兼容旧属性（main.py / 旧测试）──────────────────────────────────
    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        route = _LEGACY_ATTRS.get(name)
        if route is None:
            raise AttributeError(name)
        if route == "normalize":
            return self._normalize_adapter
        return self._get_view(route)

    @classmethod
    def view_keys(cls) -> set[str]:
        return {key for key, _ in cls.VIEWS}

    @classmethod
    def group_of(cls, view: str) -> str:
        for group in cls.GROUP_ORDER:
            if view in cls.GROUPS[group][1]:
                return group
        return cls.GROUP_ORDER[0]

    def toolbar_meta(self) -> str:
        """工具栏计数：条数 = bib_stats.records，警告 = lint.counts.warnings。"""
        stats = self.stats_data or {}
        records = stats.get("records")
        if not isinstance(records, (int, float)):
            return "运行中"
        text = f"{int(records):,} 条"
        warnings = (self.lint_counts or {}).get("warnings")
        if isinstance(warnings, (int, float)) and warnings:
            text += f" · {int(warnings):,} 条警告"
        return text

    # ── 宿主 API（页面模块调用）────────────────────────────────────────
    def notify(self, text: str) -> None:
        toast = getattr(self.app, "toast", None)
        if callable(toast):
            try:
                toast(str(text))
                return
            except Exception as exc:  # noqa: BLE001 - 提示失败不影响业务
                self.mark("bib_notify_error", error=f"{type(exc).__name__}: {exc}")

    def run_task_ui(self, coro_fn):
        """把页面模块的协程函数/协程对象交给 Flet 的 run_task。"""
        runner = getattr(self.page, "run_task", None)
        if callable(runner):
            try:
                return runner(coro_fn)
            except Exception:  # noqa: BLE001 - 退回 asyncio 保证动作不丢
                pass
        try:
            result = coro_fn() if callable(coro_fn) else coro_fn
            return asyncio.ensure_future(result)
        except Exception as exc:  # noqa: BLE001
            self.mark("bib_task_error", error=f"{type(exc).__name__}: {exc}")
            return None

    def reload_view(self) -> None:
        """重画当前叶（写库/外部数据变化后页面可主动调用）。"""
        self._render()

    def navigate_route(self, route: str) -> None:
        """路由交给 main（BNAV 唯一路由入口）；无壳时退回本页激活。"""
        nav = getattr(self.app, "navigate", None)
        if callable(nav):
            try:
                nav(str(route), source="ui")
                return
            except TypeError:
                try:
                    nav(str(route))
                    return
                except Exception as exc:  # noqa: BLE001
                    self.mark("bib_navigate_error", route=str(route),
                              error=f"{type(exc).__name__}: {exc}")
                    return
            except Exception as exc:  # noqa: BLE001
                self.mark("bib_navigate_error", route=str(route),
                          error=f"{type(exc).__name__}: {exc}")
                return
        text = str(route or "")
        if text.startswith("bib:"):
            self.set_view(text[4:], source="ui")

    # ── 懒加载 ─────────────────────────────────────────────────────────
    def _get_view(self, view: str):
        view = str(view or "")
        cached = self._views.get(view)
        if cached is not None:
            return cached
        spec = VIEW_SPECS.get(view)
        if spec is None:
            return None
        try:
            cls = load_view_class(spec[0], spec[1])
            instance = cls(self)
        except Exception as exc:  # noqa: BLE001 - 单页缺失不拖垮壳
            error = f"{type(exc).__name__}: {exc}"
            self._view_errors[view] = error
            self.mark("bib_view_missing", view=view, error=error)
            try:
                SLOG.log("bib_view_missing", view=view, error=error)
            except Exception:  # noqa: BLE001
                pass
            return None
        self._views[view] = instance
        self._view_errors.pop(view, None)
        return instance

    # ── 视图切换 ───────────────────────────────────────────────────────
    def activate(self, view: str, *, source: str = "nav") -> None:
        """导航激活：白名单 → store（view + 组记忆）→ mark → 重画 → 按需加载。"""
        view = str(view or "")
        if view not in self.view_keys():
            self.mark("bib_view_invalid", view=view, source=source)
            view = "overview"
        previous = self.view
        self.view = view
        if previous == "normalize" and view != "normalize":
            leaving = getattr(self, "_views", {}).get("normalize")
            handler = getattr(leaving, "on_leave", None)
            if callable(handler):
                try:
                    handler()
                except Exception:  # noqa: BLE001
                    pass
        self.group = self.group_of(view)
        store = self.app.store
        store.set("bib_view", view)
        memory = store.get("bib_group_memory", {}) or {}
        memory = dict(memory) if isinstance(memory, dict) else {}
        memory[self.group] = view
        store.set("bib_group_memory", memory)
        self.mark("bib_view", view=view, source=source, group=self.group)
        self._sync_header()
        self._render()
        self._ensure_loaded(view)

    def set_view(self, view: str, *, source: str = "legacy") -> None:
        """旧入口：有路由时统一 navigate，无路由则本页激活。"""
        navigate = getattr(self.app, "navigate", None)
        if callable(navigate):
            navigate(f"bib:{view}", source=source)
            return
        self.activate(view, source=source)

    def set_group(self, group: str) -> None:
        if group not in self.GROUPS:
            return
        memory = self.app.store.get("bib_group_memory", {}) or {}
        target = memory.get(group) if isinstance(memory, dict) else None
        if target not in self.GROUPS[group][1]:
            target = self.GROUPS[group][1][0]
        self.set_view(target, source="group")

    def _switch(self, view: str, *, source: str = "ui") -> None:
        """兼容别名：main.py 的 P2_BIB_VIEW / P2_BIB_QUERY_UI 仍走这个入口。"""
        self.set_view(view, source=source)

    def _ensure_loaded(self, view: str) -> None:
        """首开补一次只读统计；再让当前叶的 activate() 自己刷新数据。"""
        if self.stats_data is None and not self._stats_loading:
            runner = getattr(self.page, "run_task", None)
            if callable(runner):
                self._stats_loading = True
                try:
                    runner(self._load_stats)
                except Exception as exc:  # noqa: BLE001
                    self._stats_loading = False
                    self.mark("bib_stats_schedule_error",
                              error=f"{type(exc).__name__}: {exc}")
        instance = self._views.get(view) or self._get_view(view)
        handler = getattr(instance, "activate", None)
        if callable(handler):
            try:
                handler()
            except Exception as exc:  # noqa: BLE001 - 页内初始化失败只降级该页
                error = f"{type(exc).__name__}: {exc}"
                self._view_errors[view] = error
                self.mark("bib_view_activate_error", view=view, error=error)
                SLOG.log("bib_view_activate_error", view=view, error=error)

    async def _load_stats(self) -> None:
        """并行取库概况与 lint 计数：工具栏条数/警告与页面 db/sha 都靠它。"""

        async def _read(call) -> dict:
            try:
                return await asyncio.to_thread(EB.read_json_command, call)
            except Exception as exc:  # noqa: BLE001 - 引擎缺失不阻塞页面
                return {"ok": False, "error": str(exc), "json": {}}

        try:
            result, lint_result = await asyncio.gather(
                _read(EB.bibdb_stats_call()), _read(EB.bibdb_lint_call()))
        finally:
            self._stats_loading = False
        self.stats_data = result.get("json") or None
        lint_json = lint_result.get("json") or {}
        self.lint_counts = (lint_json.get("counts") or None) if lint_json else None
        self.stats_error = "" if result.get("ok") else str(result.get("error") or "读取失败")
        stats = self.stats_data or {}
        aliases = stats.get("aliases") or {}
        conflicts = stats.get("conflicts") or {}
        counts = self.lint_counts or {}
        self.mark("bib_stats", ok=bool(result.get("ok")), code=result.get("code"),
                  records=stats.get("records"), warnings=counts.get("warnings"),
                  aliases_total=aliases.get("total"), conflicts=conflicts.get("records"),
                  bytes=stats.get("bytes"))
        self._sync_badge(stats)
        self._render()
        self._sync_toolbar_meta()
        self._update_toolbar()

    def _sync_badge(self, stats: dict) -> None:
        """侧栏徽标 = 待办冲突数（只用已取到的 stats，不额外起引擎调用）。"""
        setter = getattr(self.app, "set_bib_badge", None)
        if not callable(setter):
            return
        totals = (stats.get("conflicts") or {}).get("totals") or {}
        pending = totals.get("pending")
        if isinstance(pending, (int, float)):
            try:
                setter(int(pending))
            except Exception as exc:  # noqa: BLE001
                self.mark("bib_badge_error", error=f"{type(exc).__name__}: {exc}")

    def _update_toolbar(self) -> None:
        toolbar = getattr(self.app, "toolbar", None)
        if toolbar is None:
            return
        try:
            toolbar.update()
        except Exception:  # noqa: BLE001 - 未挂载时忽略
            pass

    async def _run_query(self) -> None:
        """兼容 main.py 的 P2_BIB_QUERY_UI：把输入交给新查库页并触发查询。"""
        view = self._get_view("query")
        if view is None:
            return
        field = self.query_field
        if field is not None and str(field.value or "").strip():
            view.text = str(field.value or "").strip()
        try:
            view.query()
        except Exception as exc:  # noqa: BLE001
            self.mark("bib_query_probe_error", error=f"{type(exc).__name__}: {exc}")

    @property
    def query_field(self):
        view = self._get_view("query")
        if view is None:
            return None
        field = getattr(view, "_field", None)
        if field is None:
            try:
                view.build()
            except Exception:  # noqa: BLE001
                return None
            field = getattr(view, "_field", None)
        return field

    # ── 渲染 ───────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.t
        self.view_slot = ft.Container(expand=True)
        self._status_slot = ft.Row(spacing=t.sp("1"), controls=[])
        self._notice_slot = ft.Container(visible=False)
        self._sync_header()
        if self._crumb is not None:
            # §9.2：全页只保留视图自己的 h1；旧面包屑不再上屏。
            self._crumb.visible = False
        self._root = ft.Column(spacing=0, expand=True, controls=[self.view_slot])
        self._width_bucket = self._bucket()
        self._render()
        return self._root

    def theme_roots(self) -> list:
        """宿主根 + 10 个懒加载视图已 build 的 root（含当前未挂载的缓存树）。

        批 116：主题热切换只染挂载树会漏掉 `_views` 里已存在的视图；切完后
        再导航到「编辑/入藏/查重合并/字段归位」就会看到旧皮肤。
        """
        roots = super().theme_roots()
        seen = {id(root) for root in roots}
        for view in self._views.values():
            root = getattr(view, "_root", None)
            if root is not None and id(root) not in seen:
                roots.append(root)
                seen.add(id(root))
        return roots

    def _sync_header(self) -> None:
        """维护旧页头状态（crumb/子标题/notice/状态）；不再挂到控件树。"""
        t = self.t
        self.group = self.group_of(self.view)
        crumb = f"引用库 / {self.GROUPS[self.group][0]}"
        if self._crumb is None:
            self._crumb = ft.Row(spacing=t.sp("half"), controls=[
                C.text(crumb, t, "caption", t.palette.text_muted)])
            self._child_title = C.text(self.VIEW_LABELS[self.view], t, role="page-title")
        else:
            self._crumb.controls[0].value = crumb
            self._child_title.value = self.VIEW_LABELS[self.view]
        if self._notice_slot is not None:
            self._notice_slot.content = self._notice_control()
            self._notice_slot.visible = self._notice_slot.content is not None
        if self._status_slot is not None:
            self._status_slot.controls = self._status_chips()
        self._safe_update(*[c for c in (self._crumb, self._child_title,
                                        self._notice_slot, self._status_slot)
                            if c is not None])
        self._sync_toolbar_meta()

    def _render_nav(self) -> None:
        """兼容旧名：导航已在侧栏，实际只刷新旧页头状态。"""
        self._sync_header()

    # ── 页内二级导航（批 116 BIB-NAV2，方案 A）──────────────────────────
    def _nav_narrow(self) -> bool:
        """设计宽 < 1100 时两行分组换行；0（未挂载）按宽窗单行。"""
        width = self._logical_width()
        return 0 < width < BIB_NAV_NARROW_BELOW

    def _bib_nav_control(self) -> ft.Container:
        """Bib 白面板顶部的 3 组 9 chip 导航（无可见组标题）；settings 无选中。

        批 119：与单篇/整期共用 C.page_nav 唯一实现；窄窗按治理组整体下移
        （查阅+采录 4 chip 一行，治理 5 chip 一行）。
        """
        t = self.t
        groups = IA.group_views("bib")
        content_keys = {str(view["key"]) for group in groups for view in group["views"]}
        active = str(getattr(self, "view", "") or "")
        if active not in content_keys:
            active = ""
        narrow = self._nav_narrow()
        split = next((index for index, group in enumerate(groups)
                      if group["key"] == "treat"), len(groups))
        row_groups = [groups[:split], groups[split:]] if narrow else [groups]
        # 行内相邻组计数：1000 两行时 read|intake 在行 1，intake|treat
        # 落在行间（不画多余分隔符），故实际渲染为 1。
        divider_count = sum(max(0, len(row) - 1) for row in row_groups)
        narrow_after = ""
        if narrow and 0 < split < len(groups):
            narrow_after = str(groups[split - 1]["views"][-1]["key"])
        payload = [
            [{"key": str(view["key"]), "label": str(view["label"]),
              "tooltip": str(group["label"]) if position == 0 else None}
             for position, view in enumerate(group["views"])]
            for group in groups
        ]

        def _mark_chip(chip, item, is_active) -> None:
            chip._bib_nav_chip = True
            chip._bib_nav_key = str(item["key"])
            chip._bib_nav_active = is_active

        def _mark_bar(bar) -> None:
            bar._bib_inpage_nav = True
            bar._bib_nav_narrow = narrow
            bar._bib_nav_active = active
            bar._bib_nav_keys = [str(view["key"]) for group in groups
                                 for view in group["views"]]

        nav = C.page_nav(
            t, groups=payload, active=active,
            on_click=lambda key: self.navigate_route(f"bib:{key}"),
            narrow=narrow, narrow_after=narrow_after,
            divider_attr="_bib_nav_divider",
            chip_attr=_mark_chip, bar_attrs=_mark_bar,
            bar_pad=(24, 0, 24, 0), bgcolor=t.palette.surface,
            palette_getter=lambda: self.t.palette)
        self._nav_narrow_cache = narrow
        # 证据 marker：组 / 空 captions / 渲染出的分隔符数 / 9 chip / 选中 / 断点。
        # 首次 build 时窗口可能还是 800 宽（随后 resize 到目标宽），故按
        # (narrow, dividers, active) 去重补记一次最终布局，避免宽窗日志误报两行。
        marker = (narrow, divider_count, active)
        if getattr(self, "_bib_inpage_marker_state", None) != marker:
            self._bib_inpage_marker_state = marker
            self._bib_inpage_marked = True
            self.mark("bib_inpage_nav",
                      groups=[str(group["key"]) for group in groups],
                      captions=[],
                      dividers=divider_count,
                      chips=[str(view["key"]) for group in groups
                             for view in group["views"]],
                      active=active, narrow=narrow)
        return nav

    def _shell_panel(self, content):
        """找到内容里的 page_shell 面板。

        写库页（edit/dupes/fieldfix/import/settings）的 `build()` 先包一层
        `_root = Container(content=self._body())`，而 `_body()` 才返回 page_shell；
        概览/查库等直接返回 page_shell。两种都要挂导航，故向下最多解 3 层容器。
        """
        node = content
        for _ in range(3):
            if getattr(node, "_bib_shell", False):
                return node
            node = getattr(node, "content", None)
            if not isinstance(node, ft.Control):
                return None
        return None

    def _attach_nav(self, content) -> object:
        """把导航条插进 page_shell 的白色面板（页头之上）；非 shell 内容不动。"""
        panel = self._shell_panel(content)
        if panel is None:
            return content
        inner = getattr(panel, "content", None)
        if not isinstance(inner, ft.Column) or not inner.controls:
            return content
        nav = self._bib_nav_control()
        inner.controls.insert(0, nav)
        panel._bib_inpage_nav = nav
        return content

    def _replant_nav(self) -> None:
        """视图异步重绘后把导航条重新插回 page_shell。

        批 116 验收回归（BIB-NAV3 之后）：edit/dupes/fieldfix/import/prefs 的
        `_render()` 会用 `self._root.content = self._body()` 整块替换内容，
        连 `_attach_nav` 插进旧 page_shell 的导航一起丢掉（navigate("bib:settings")
        后导航条消失，深链反而正常）。视图重绘后回调本方法做幂等补插；
        首个子控件已是导航时直接返回，不重建、不触发 update。
        """
        content = getattr(self.view_slot, "content", None)
        panel = self._shell_panel(content)
        if panel is None:
            return
        inner = getattr(panel, "content", None)
        if not isinstance(inner, ft.Column) or not inner.controls:
            return
        if getattr(inner.controls[0], "_bib_inpage_nav", False):
            return
        nav = self._bib_nav_control()
        inner.controls.insert(0, nav)
        panel._bib_inpage_nav = nav

    def _refresh_nav(self) -> None:
        """断点跨过 1100 时原位替换导航条，不重建整页（保输入/滚动状态）。"""
        panel = self._shell_panel(getattr(self.view_slot, "content", None))
        if panel is None:
            return
        inner = getattr(panel, "content", None)
        if not isinstance(inner, ft.Column) or not inner.controls:
            return
        if not getattr(inner.controls[0], "_bib_inpage_nav", False):
            return
        nav = self._bib_nav_control()
        inner.controls[0] = nav
        panel._bib_inpage_nav = nav
        try:
            panel.update()
        except Exception:  # noqa: BLE001 —— 未挂载
            pass

    def _notice_control(self):
        spec = self.VIEW_NOTICES.get(self.view)
        if not spec:
            return None
        tone, message = spec
        bar = C.notice_bar(message, self.t, tone)
        try:
            bar.tooltip = ft.Tooltip(message=BIB_WRITE_RULES, wait_duration=0.4)
        except Exception:  # noqa: BLE001 - Flet 版本差异：没有 Tooltip 就退化成一行
            pass
        return bar

    def _status_chips(self) -> list:
        t = self.t
        access = self.VIEW_ACCESS.get(self.view, "只读")
        tone = "warn" if access == "写库" else "ok"
        return [ft.Row(spacing=t.sp("half"), controls=[
            C.status_dot(tone, t),
            C.text(access, t, "caption", color=t.palette.text_muted),
        ])]

    def _safe_update(self, *controls) -> None:
        for control in controls:
            try:
                control.update()
            except Exception:  # noqa: BLE001
                pass

    def _sync_toolbar_meta(self) -> None:
        syncer = getattr(self.app, "_sync_toolbar", None)
        if callable(syncer) and getattr(self.app, "toolbar", None) is not None:
            try:
                syncer()
            except Exception:  # noqa: BLE001
                pass

    def _render(self) -> None:
        if self.view_slot is None:
            return
        view = str(getattr(self, "view", "overview") or "overview")
        instance = self._get_view(view)
        try:
            content = instance.build() if instance is not None else self._fallback(view)
        except Exception as exc:  # noqa: BLE001 - 单页构建失败不炸壳
            frames = traceback.extract_tb(exc.__traceback__)
            error = f"{type(exc).__name__}: {exc}"
            self._view_errors[view] = error
            self.mark("bib_render_error", view=view, error=error,
                      where=[f"{f.name}:{f.lineno}" for f in frames][-4:])
            SLOG.log("bib_render_error", view=view, error=error)
            content = self._fallback(view)
        content = self._attach_nav(content)
        self._wire_gear(content)
        self.view_slot.content = content
        try:
            self.view_slot.update()
        except Exception:  # noqa: BLE001 - 构建期未挂载
            pass

    def _fallback(self, view: str) -> ft.Control:
        t = self.t
        error = self._view_errors.get(view) or "页面模块未提供"
        banner = S.state_banner(t, "failure", message="页面尚未接入",
                                detail=f"{self.VIEW_LABELS.get(view, view)} · {error}")
        box = ft.Container(
            expand=True,
            padding=ft.Padding(left=t.px(24), top=t.px(24),
                               right=t.px(24), bottom=t.px(24)),
            content=ft.Column(spacing=t.sp("1"), expand=True, controls=[banner]))
        box._bib_state_banner = True
        return box

    def _wire_gear(self, root) -> None:
        """写库页 build 时没给齿轮回调；宿主兜底接到 `bib:settings`（1 击可达）。"""
        for control in _walk_controls(root):
            if (isinstance(control, ft.IconButton)
                    and getattr(control, "tooltip", None) == "设置"):
                if getattr(control, "disabled", False) or control.on_click is None:
                    control.disabled = False
                    control.on_click = lambda e: self.navigate_route("bib:settings")
                return

    # ── 生命周期 ───────────────────────────────────────────────────────
    def on_show(self) -> None:
        self._render_nav()
        self._ensure_loaded(self.view)

    def _logical_width(self) -> float:
        try:
            width = float(getattr(self.page, "width", 0) or 0)
        except (TypeError, ValueError):
            width = 0.0
        return width / max(getattr(self.t, "k", 1.0) or 1.0, 1e-6)

    def _bucket(self) -> int | None:
        width = self._logical_width()
        if width <= 0:
            return None
        if width < 1000:
            return 0
        if width < 1100:
            return 1
        return 2

    def on_resize(self) -> None:
        # 批 116 BIB-NAV2：先按 1100 断点换行/并回导航条，再让视图自己重排。
        narrow = self._nav_narrow()
        if narrow != getattr(self, "_nav_narrow_cache", None):
            self._refresh_nav()
        instance = self._views.get(self.view)
        handler = getattr(instance, "on_resize", None)
        if callable(handler):
            try:
                handler()
                return
            except Exception as exc:  # noqa: BLE001
                self.mark("bib_resize_error", error=f"{type(exc).__name__}: {exc}")
        bucket = self._bucket()
        if bucket is not None and bucket != self._width_bucket:
            self._width_bucket = bucket
            self._render()

    def refresh_running(self) -> None:
        """转发当前叶的 refresh_running（未实例化 = 没在跑，直接返回）。"""
        instance = self._views.get(self.view)
        handler = getattr(instance, "refresh_running", None)
        if callable(handler):
            try:
                handler()
            except Exception as exc:  # noqa: BLE001
                self.mark("bib_refresh_error", view=self.view,
                          error=f"{type(exc).__name__}: {exc}")

    def _kv(self, label: str, value: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", t.palette.text_muted),
            ft.Container(expand=True),
            C.text(str(value), t, "small", t.palette.text),
        ])
