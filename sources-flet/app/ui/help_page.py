"""帮助与关于页：页内目录 + 入口地图 + 平台差异与托盘 + 输入法/操作细节 +
快捷键 + 文档位置 + 边界与安全 + 版本与许可。

从 `pages.py` 拆出：页面只排版，文案集中在 `help_content.py`；
`pages.py` 只保留 `PAGE_CLASSES` 里的引用，避免 1200+ 行文件继续膨胀。

批 115 P3-C3（S1m-native）：页面 = 一张白纸内容面板（`page_body(panel=True)`），
分区用 `C.section_label` + 1px 发丝线，不再用 7 张 `C.card`；入口 / 平台 / 细节
行去掉 92px 手写标签列，统一「标题 + 说明」；版本行走 `C.kv_row`（32 高、
label muted、value 右对齐可截断）。原来「平台差异与托盘」+「关闭到托盘怎么用」
两张卡合并成一段，托盘步骤折叠在段内，重复的标题只出现一次。

页内目录：flet 1.0.5 的 `ScrollableControl.scroll_to(scroll_key=...)` 可用
（`flet/controls/scrollable_control.py:390`；目标控件 `key=ft.ScrollKey(...)`），
所以目录行可点跳转。跳转容器取 `page_body` 内层滚动 Column（本页无浮层，不会
变成 Stack）；构造期 / 测试替身没有挂载时不跳，静默返回。
"""
from __future__ import annotations

import flet as ft

from ui import components as C
from ui import help_content as HC
from ui import settings_shell as SHELL
from ui.base_page import BasePage

#: 页内目录：(分区锚点键, 分区标题)。顺序 = 页面顺序，与 `_section(...)` 一一对应。
TOC = (
    ("entries", "功能入口"),
    ("platform", "平台差异与托盘"),
    ("details", "输入法与操作细节"),
    ("shortcuts", "快捷键"),
    ("docs", "文档在哪"),
    ("boundary", "边界与安全"),
    ("about", "版本与许可"),
)

#: 滚动锚点前缀（`ft.ScrollKey` 的 value 必须与 `_section` 里写的一致）。
ANCHOR = "help."


class HelpPage(BasePage):
    key = "help"

    def build(self) -> ft.Control:
        t = self.t
        header = C.page_header(HC.TITLE, HC.SUBTITLE, t)
        body = C.page_body(
            header,
            self._toc(),
            self._entries(),
            self._platform(),
            self._details(),
            self._shortcuts(),
            self._docs(),
            self._boundary(),
            self._about(),
            t=t, gap=12, panel=True,
        )
        # 目录跳转的滚动容器：`page_body` 无 overlay 时返回 Container(content=滚动 Column)。
        self._scroll = body.content
        return body

    # ── 分区骨架（节标题 + 发丝线 + 内容；节点带滚动锚点）───────────────────
    def _section(self, key: str, title: str, note: str | None, *body) -> ft.Column:
        t = self.t
        return ft.Column(
            key=ft.ScrollKey(f"{ANCHOR}{key}"),
            spacing=t.sp("2"),
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                C.section_label(title, t, note=note),
                C.hairline(t),
                ft.Column(spacing=t.sp("2"),
                          horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                          controls=list(body)),
            ],
        )

    # ── 目录：两列 32 高可点行（左 4 / 右 3）─────────────────────────────
    def _toc(self) -> ft.Column:
        t = self.t
        rows = [self._toc_row(key, title) for key, title in TOC]
        split = (len(rows) + 1) // 2
        columns = ft.Row(
            spacing=t.sp("4"),
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=[
                ft.Column(expand=True, spacing=0,
                          horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                          controls=part)
                for part in (rows[:split], rows[split:])
            ],
        )
        return self._section("toc", "目录", "点击段落名跳转", columns)

    def _toc_row(self, key: str, title: str) -> ft.Container:
        t = self.t
        return C.table_row(
            [
                C.text(title, t, "small", weight="medium"),
                ft.Row(spacing=0, controls=[
                    ft.Container(expand=True),
                    ft.Icon(C.ic("CHEVRON_RIGHT"), size=t.px(14),
                            color=t.palette.text_faint),
                ]),
            ],
            t,
            on_click=lambda e, anchor=key: self._jump(anchor),
        )

    def _jump(self, anchor: str) -> None:
        """目录点击 → 把内层滚动 Column 滚到对应分区（未挂载时静默返回）。"""
        scroll = getattr(self, "_scroll", None)
        page = getattr(self, "page", None)
        if scroll is None or page is None:
            return
        try:
            page.run_task(self._scroll_to, f"{ANCHOR}{anchor}")
        except Exception:  # noqa: BLE001 构建期 / 测试替身：跳转失败不影响页面
            pass

    async def _scroll_to(self, anchor: str) -> None:
        try:
            await self._scroll.scroll_to(scroll_key=ft.ScrollKey(anchor), duration=180)
        except Exception:  # noqa: BLE001 未挂载（构建期 / 测试替身）
            pass

    # ── 功能入口（去 92px 标签列：标题 + 入口路径一行，说明在下）───────────
    def _entries(self) -> ft.Column:
        return self._section(
            "entries", "功能入口", "侧栏六个入口 + 单篇页里的 AI 面板",
            *[self._entry(name, where, self._entry_detail(name, detail))
              for name, where, detail in HC.ENTRIES],
        )

    def _entry_detail(self, name: str, detail: str) -> str:
        if name != "AI 任务书":
            return detail
        enabled = bool(self.app.store.get("ai.enabled", False))
        state = "已启用" if enabled else "已关闭"
        return f"当前{state}；开启后按预算调用，禁止把未刊稿发给外部模型。"

    def _entry(self, name: str, where: str, detail: str) -> ft.Column:
        t = self.t
        head = ft.Row(
            spacing=t.sp("1"),
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                C.text(name, t, "small", weight="medium"),
                C.vline(t),
                C.text(where, t, "caption", t.palette.text_muted, expand=True),
            ],
        )
        controls: list = [head]
        if detail:
            controls.append(C.text(detail, t, "caption", t.palette.text_muted))
        return ft.Column(spacing=t.px(2),
                         horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                         controls=controls)

    # ── 平台差异与托盘（两段合并：托盘步骤折叠在段内，标题只出现一次）──────
    def _platform(self) -> ft.Column:
        t = self.t
        body = ft.Column(
            spacing=t.sp("1"),
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                *[self._step(index, value)
                  for index, value in enumerate(HC.TRAY_STEPS, 1)],
                C.hairline(t),
                *[C.text(line, t, "caption", t.palette.text_faint)
                  for line in HC.TRAY_LIMITS],
            ],
        )
        body.visible = False
        self._tray_body = body
        self._tray_icon = ft.Icon(C.ic("EXPAND_MORE"), size=t.px(16),
                                  color=t.palette.text_faint)
        # 折叠行 = 32 高表格行（与目录行同一视觉），不进 TOC（它是段内细节）。
        head = C.table_row(
            [
                C.text("关闭到托盘怎么用", t, "small", weight="medium"),
                ft.Row(spacing=0, controls=[ft.Container(expand=True), self._tray_icon]),
            ],
            t,
            on_click=self._toggle_tray,
        )
        return self._section(
            "platform", "平台差异与托盘", "托盘仅 Windows",
            *[self._item(name, text) for name, text in HC.PLATFORM],
            head,
            body,
        )

    def _toggle_tray(self, e=None) -> None:
        """段内折叠：展开 4 步 + 3 条限制；失败（未挂载）不影响页面。"""
        body = getattr(self, "_tray_body", None)
        if body is None:
            return
        body.visible = not body.visible
        self._tray_icon.icon = C.ic("EXPAND_LESS" if body.visible else "EXPAND_MORE")
        self._tray_icon.tooltip = "收起" if body.visible else "展开"
        try:
            body.update()
            self._tray_icon.update()
        except Exception:  # noqa: BLE001 构建期 / 测试替身
            pass

    def _step(self, index: int, value: str) -> ft.Row:
        t = self.t
        return ft.Row(
            spacing=t.sp("1"),
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=[
                ft.Container(width=t.px(18), alignment=ft.Alignment.TOP_LEFT,
                             content=C.text(f"{index}.", t, "caption",
                                            t.palette.text_faint)),
                C.text(value, t, "small", expand=True),
            ],
        )

    # ── 输入法与操作细节（输入法放宽为一行 note，其余行去标签列）───────────
    def _details(self) -> ft.Column:
        t = self.t
        ime = next((row for row in HC.DETAILS if row[0] == "输入法"), None)
        rest = [row for row in HC.DETAILS if row is not ime]
        body: list = [C.inline_note(f"{ime[0]}：{ime[1]}", t)] if ime else []
        body.extend(self._item(label, text) for label, text in rest)
        return self._section("details", "输入法与操作细节", None, *body)

    # ── 快捷键（与外壳实际绑定同源，仍走 settings_shell 的行生成器）─────────
    def _shortcuts(self) -> ft.Column:
        return self._section("shortcuts", "快捷键", "与外壳实际绑定同源",
                             *SHELL.shortcut_rows(self.t))

    # ── 文档在哪 ────────────────────────────────────────────────────────
    def _docs(self) -> ft.Column:
        return self._section(
            "docs", "文档在哪", None,
            *[self._item(label, text, icon="DESCRIPTION_OUTLINED")
              for label, text in HC.DOCS],
        )

    # ── 边界与安全（一行一条，不铺底色）─────────────────────────────────
    def _boundary(self) -> ft.Column:
        t = self.t
        return self._section("boundary", "边界与安全", "写操作都有前置闸门",
                             *[C.inline_note(line, t) for line in HC.BOUNDARY])

    # ── 版本与许可（短值走 32 高键值行；长文本整段换行，不截断）───────────
    def _about(self) -> ft.Column:
        t = self.t
        app = self.app
        return self._section(
            "about", "版本与许可", None,
            C.kv_row(t, "应用版本", app.app_version, mono=True),
            C.kv_row(t, "引擎", f"{app.engine_status.version} · {app.engine_status.mode}",
                     mono=True),
            C.kv_row(t, "外壳", f"Flet {app.flet_version} · Python {app.python_version}",
                     mono=True),
            self._item("页面", "6 个（工作台 / 单篇稿件 / 整期装配 / 引用库 / 设置 / 帮助与关于）"),
            self._item("许可", HC.LICENSE),
            self._item("第三方", HC.THIRD_PARTY),
        )

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _item(self, title: str, desc: str, *, icon: str | None = None) -> ft.Column:
        """统一「标题 + 说明」两行块（平台 / 细节 / 文档 / 许可共用，无标签列）。"""
        t = self.t
        head: list = []
        if icon:
            head.append(ft.Icon(C.ic(icon), size=t.px(15),
                                color=t.palette.text_muted))
        head.append(C.text(title, t, "small", weight="medium"))
        return ft.Column(
            spacing=t.px(2),
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.Row(spacing=t.sp("1"),
                       vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=head),
                C.text(desc, t, "caption", t.palette.text_muted),
            ],
        )

    def refresh_running(self) -> None:
        pass
