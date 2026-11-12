"""FIX-20261112 包：sources-flet UI 侧回归（Q1 / Q2 / Q3 / Q9）。

四组纯运行时断言，不启动 Flet 窗口（与 tests/ 既有 headless 约定一致）：

1. Q1 —— 换肤 retint 要把 TextField 的 `TextStyle.color`（正文/提示/标签/错误/光标文字）
   一起按前景语义重染；用 `LIGHT`/`DARK` token 对照断言，不硬编码颜色，并用相对亮度
   证明深色主题下正文与输入框底色对比度 ≥ 4.5（近黑字压在深底上就是 Q1 的症状）。
2. Q2 —— 输入过程中 `✕` 只同步属性、不发 update（否则客户端重建整框、首字母丢焦点）；
   失焦补发一次；「有内容显示 ✕ / 空不显示」的既有语义不变。
3. Q3 —— navigate / set_theme 的整页 `page.update()` 在 180ms 窗口内合并成 1 次；
   navigate 的路由状态、set_theme 的换肤效果仍然同步落地（不能为了合并而变慢/变卡）。
4. Q9 —— 报告固定行解析出 `fix_written` / `fix_path`；`proof_result_text` 只有报告明确
   「已另写」才报路径，0 改照实说「未另写」，报告缺失/旧报告说「未确认」。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft

import main as M
from ui import checks as CK
from ui import components as C
from ui.tokens import DARK, LIGHT, Tokens, palette_remap


# ── Q1：换肤后输入控件的 TextStyle.color 也要重染 ────────────────────────────
def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    raw = color.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(fg: str, bg: str) -> float:
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _three_input_kinds() -> list[ft.TextField]:
    """元数据（form_field）/ 源文件（floating_field）/ 查询（text_field）三类输入。"""
    light = Tokens("light")
    return [
        C.form_field(light, "作者", C.text_field(light, value="张三")),
        C.floating_field(light, "初排 tex", value="/tmp/稿/a.tex"),
        C.text_field(light, value="核查", hint_text="搜索"),
    ]


def test_retint_recolors_text_styles_for_all_input_kinds():
    fields = _three_input_kinds()
    tree = ft.Column(controls=fields)
    app = object.__new__(M.App)          # 只调纯递归方法，不依赖 App.__init__
    app.t = Tokens("dark")

    changed = app._retint(tree, palette_remap("dark"), palette_remap("dark", role="fg"))

    assert changed >= len(fields) * 2       # 每个字段至少正文 + 一处样式被重染
    for field in fields:
        assert field.text_style.color == DARK.text
        assert field.hint_style.color == DARK.text_faint
        assert field.bgcolor == DARK.surface
        assert _contrast(field.text_style.color, field.bgcolor) >= 4.5
        if field.label_style is not None:
            assert field.label_style.color == DARK.text_muted
        if field.error_style is not None:
            assert field.error_style.color == DARK.error
        if field.cursor_color is not None:
            assert field.cursor_color == DARK.accent

    # 再切回浅色：同一批样式对象必须恢复 LIGHT token（颜色不会单向漂移）。
    assert app._retint(tree, palette_remap("light"),
                       palette_remap("light", role="fg")) >= len(fields) * 2
    for field in fields:
        assert field.text_style.color == LIGHT.text
        assert field.hint_style.color == LIGHT.text_faint
        assert field.bgcolor == LIGHT.surface


def test_retint_shell_covers_mounted_text_styles():
    """Q1 走真实入口 `_retint_shell`（page 挂载树 + offscreen 缓存树共用 `_retint`）。"""
    field = C.floating_field(Tokens("light"), "初排 tex", value="/tmp/稿/a.tex")
    app = object.__new__(M.App)
    app.theme = "dark"
    app.t = Tokens("dark")
    app.page = SimpleNamespace(controls=[field])
    app.pages = {}
    app.sidebar = SimpleNamespace(bgcolor=LIGHT.sidebar)
    app.toolbar = None
    app.content = SimpleNamespace(bgcolor=LIGHT.surface)
    app.theme_switch = None
    app._refresh_tokens = lambda old: 0

    changed = M.App._retint_shell(app, Tokens("light"))

    assert changed >= 1
    assert field.text_style.color == DARK.text
    assert field.hint_style.color == DARK.text_faint
    assert field.bgcolor == DARK.surface


# ── Q2：清空按钮不再在输入中触发整框 update（不丢焦点）──────────────────────
def _clearable_field() -> ft.TextField:
    field = C.text_field(Tokens("light"), value="", hint_text="路径")
    assert isinstance(field.suffix, ft.IconButton)
    assert field.suffix.visible is False       # 空值不显示 ✕
    return field


def _spy(field: ft.TextField) -> tuple[list[str], ft.IconButton]:
    calls: list[str] = []
    button = field._clear_button
    field.update = lambda: calls.append("field")
    button.update = lambda: calls.append("button")
    return calls, button


def test_typing_first_char_keeps_focus_contract_without_update():
    field = _clearable_field()
    calls, button = _spy(field)

    field.value = "a"
    field.on_change(SimpleNamespace(control=field))     # 用户敲入首字母

    assert button.visible is True                       # 属性照旧同步：有内容显示 ✕
    assert calls == []                                  # 但不发 patch → 客户端不重建、不丢焦点
    assert field._clearable_dirty is True

    field.on_blur(SimpleNamespace(control=field))       # 失焦补发一次
    assert calls == ["field", "button"]
    assert field._clearable_dirty is False


def test_clear_button_still_clears_and_toggles_visible():
    field = _clearable_field()
    field.update = lambda: None
    button = field._clear_button
    button.update = lambda: None

    field.value = "abc"
    C.sync_clearable(field)
    assert button.visible is True

    field.suffix.on_click(None)                          # 点 ✕
    assert field.value == "" and button.visible is False

    calls: list[str] = []
    field.update = lambda: calls.append("field")
    button.update = lambda: calls.append("button")
    field.value = "a"                                    # 清空后重输首字母
    field.on_change(SimpleNamespace(control=field))
    assert button.visible is True and calls == []


def test_programmatic_sync_still_publishes_once():
    field = _clearable_field()
    calls, button = _spy(field)

    field.value = "x"
    C.sync_clearable(field)                              # 程序化回填：仍需立刻上屏
    assert button.visible is True
    assert calls == ["field", "button"]

    calls.clear()
    C.sync_clearable(field)                              # 无变化：不增加 update 噪声
    assert calls == []


# ── Q3：navigate / set_theme 的整页 update 合并 ─────────────────────────────
class _FakePage:
    def __init__(self) -> None:
        self.tasks: list = []
        self.updated = 0
        self.controls: list = []
        self.theme_mode = None
        self.bgcolor = None

    def run_task(self, handler, *args, **kwargs) -> None:
        self.tasks.append(handler)

    def update(self) -> None:
        self.updated += 1


class _StubPage:
    def __init__(self, key: str) -> None:
        self.key = key
        self.root = ft.Container()
        self.activated: list = []
        self.shown = 0

    def ensure_built(self):
        return self.root

    def activate(self, view, **kwargs) -> None:
        self.activated.append((view, kwargs))

    def on_show(self) -> None:
        self.shown += 1


def _nav_app() -> object:
    app = object.__new__(M.App)
    app.page = _FakePage()
    app.store = SimpleNamespace(set=lambda key, value: None)
    app.marks: list = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.current_page_key = ""
    app.current_route = ""
    app.nav_open = {}
    app.page_switches: list = []
    app.pages = {"home": _StubPage("home"), "bib": _StubPage("bib"),
                 "single": _StubPage("single"), "settings": _StubPage("settings")}
    app.content = SimpleNamespace(content=None)
    app.sidebar = SimpleNamespace(content=None)
    app._sidebar_content = lambda: ft.Column()
    app._sync_toolbar = lambda: None
    app._nav_open_for = lambda route: {"page": route}
    app._update_scheduled = False
    app._update_after = None
    return app


def test_navigate_coalesces_rapid_switches_to_one_update(monkeypatch):
    monkeypatch.setattr(M, "_PAGE_UPDATE_COALESCE_S", 0.0)
    app = _nav_app()

    for route in ("home", "bib:edit", "home", "bib:edit"):
        M.App.navigate(app, route)

    assert app.page.updated == 0            # 窗口内不逐次发整页 patch
    assert len(app.page.tasks) == 1         # 只排一次合并发送
    # 路由状态仍同步落地（最后一次导航生效），不因合并而变慢/丢状态
    assert app.current_page_key == "bib"
    assert app.content.content is app.pages["bib"].root
    assert app.pages["bib"].activated[-1][0] == "edit"

    asyncio.run(app.page.tasks[0]())
    assert app.page.updated == 1


def _theme_app() -> object:
    app = object.__new__(M.App)
    app.page = _FakePage()
    app.running = False
    app.theme = "light"
    app.zoom = 1.0
    app.dpi = 96
    app.button_palette = "c"
    app.t = Tokens("light", 1.0, 96, "c")
    app.store = SimpleNamespace(set=lambda key, value: None)
    app.marks: list = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app._apply_page_theme = lambda: None
    app.status_bar = SimpleNamespace(set_meta=lambda meta: None)
    app.apply_titlebar = lambda: None
    app.persist = lambda: False
    app.toast = lambda message: None
    app.rebuild_shell = lambda: None
    app._update_scheduled = False
    app._update_after = None
    return app


def test_set_theme_applies_each_switch_synchronously_but_sends_once(monkeypatch):
    monkeypatch.setattr(M, "_PAGE_UPDATE_COALESCE_S", 0.0)
    app = _theme_app()

    M.App.set_theme(app, "dark")
    assert app.t.palette is DARK           # 换肤同步生效（既有测试依赖这一点）
    assert app.page.theme_mode == ft.ThemeMode.DARK
    M.App.set_theme(app, "light")
    M.App.set_theme(app, "dark")

    assert app.page.updated == 0 and len(app.page.tasks) == 1
    asyncio.run(app.page.tasks[0]())
    assert app.page.updated == 1


def test_coalesced_update_falls_back_to_sync_without_run_task():
    app = object.__new__(M.App)
    updates: list[int] = []
    after: list[int] = []
    app.page = SimpleNamespace(update=lambda: updates.append(1))
    app.mark = lambda event, **fields: None
    app._update_scheduled = False
    app._update_after = None

    M.App._coalesced_update(app, after=lambda: after.append(1))

    assert updates == [1] and after == [1]
    assert app._update_scheduled is False


# ── Q9：修正稿固定行解析 + 结果文案（有改 / 0 改 / 未加 --fix / 报告缺失）──
_WRITTEN_LINE = "修正稿：已另写 `/tmp/稿/a_一校核查后.tex`（自动改 2 处＋位序调整 1 处；输入文件未动）"
_ZERO_LINE = "修正稿：未另写（未发现可自动修改项；输入文件未动）"


def test_parse_proof_report_exposes_fix_fields():
    written = CK.parse_proof_report(f"**计数**：需人工 1 ｜ 可自动改 2 ｜ 命中合计 3 ｜ 规则数 1\n\n{_WRITTEN_LINE}")
    assert written.fix_known is True and written.fix_written is True
    assert written.fix_path == "/tmp/稿/a_一校核查后.tex"
    assert "自动改 2 处" in written.fix_note

    zero = CK.parse_proof_report(f"{_ZERO_LINE}")
    assert zero.fix_known is True and zero.fix_written is False
    assert zero.fix_path == ""

    legacy = CK.parse_proof_report("**计数**：需人工 0 ｜ 可自动改 0 ｜ 命中合计 0 ｜ 规则数 0")
    assert legacy.fix_known is False and legacy.fix_written is False

    assert CK.parse_proof_report("").fix_known is False


def test_read_proof_report_extracts_fix_line(tmp_path):
    path = tmp_path / "a_一校核查.md"
    path.write_text(_WRITTEN_LINE, encoding="utf-8")
    report = CK.read_proof_report(str(path))
    assert report.fix_written is True and report.fix_path.endswith("a_一校核查后.tex")

    missing = CK.read_proof_report(str(tmp_path / "不存在.md"))
    assert missing.fix_known is False and missing.fix_written is False


def test_proof_result_text_only_claims_written_when_report_says_so():
    written = CK.parse_proof_report(_WRITTEN_LINE)
    zero = CK.parse_proof_report(_ZERO_LINE)

    text = CK.proof_result_text(1, tex="a.tex", fix=True, report=written)
    assert "修正稿：已另写 `/tmp/稿/a_一校核查后.tex`" in text
    assert "输入文件未动" in text

    zero_text = CK.proof_result_text(1, tex="a.tex", fix=True, report=zero)
    assert "未另写" in zero_text and "未发现可自动修改项" in zero_text
    assert "已另写" not in zero_text

    # 报告缺失 / 旧报告（没有固定行）：只能说「未确认」，绝不冒充已另写
    for unknown in (CK.proof_result_text(1, tex="a.tex", fix=True),
                    CK.proof_result_text(1, tex="a.tex", fix=True,
                                         report=CK.parse_proof_report("**摘要**：还行"))):
        assert "已另写" not in unknown
        assert "未确认" in unknown

    # 没加 --fix：不出现修正稿行
    assert "修正稿" not in CK.proof_result_text(1, tex="a.tex", fix=False, report=written)

    # 失败分支不追加修正稿说法
    assert CK.proof_result_text(2, tex="a.tex", fix=True,
                                report=written).startswith("失败 · 核查未完成（退出码 2）")
