"""2026-11-17 修复 R4 / R1 / R7：首页 bib 自激循环 + 笔记仓路径校验 + 取消态标签。

R4（P0，本文件的重点）：打包态 `bib_host._load_stats()` 起不来引擎子进程时
**不抛异常**，而是内部把失败吞成 `{"ok": False, "json": {}}`，于是
`self.stats_data = result.get("json") or None` 恒为 None。`HomePage._kick_bib_stats`
原来的守卫只看 `stats_data is not None`，于是：

    build() → _kick_bib_stats() → run_task(_load) → _load_stats() 返回但没数据
            → _refresh_band() → self.root = self.build() → _kick_bib_stats() → …

实测 106 次 / 11.9 秒，直到 `RuntimeError: An attempt to fetch destroyed session.`。
修法：`_bib_kick_done` 一次性闸门（与取数成败解耦，每次页面生命周期只 kick 一次）。

锁五件事（不建窗口、不跑引擎、不读真实笔记仓）：
1. 取数永远失败时，`_kick_bib_stats` 恰好触发 **1** 次后台任务（改前是无限次，
   `test_prefix_behaviour_kicks_forever` 用改前的原函数体量出 >1，作为回归见证）；
2. 取数成功时行为不变：仍刷新一次数据带（闸门不误伤正常路径）；
3. 闸门是「每页面实例一次」，新建的 HomePage 仍会 kick（不是进程级开关）；
4. R1：`notes_repo` 填成 `bib_db` 子目录时给提示但**不阻断保存**，合法/空值不提示；
5. R7：cancelled 默认词「继续」保持不变 —— 用静态断言锁住「凡传 on_next 的
   cancelled 调用点必须显式传 next_label」，保证默认词在真实 UI 里不可达。
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from app.ui import bib_shell as S
from app.ui import pages as P
from app.ui.tokens import Tokens
from test_b112_post_b import _FakeApp, _Store, _walk
from test_b115_issue_settings import _build_settings

APP_DIR = Path(__file__).resolve().parent.parent / "app" / "ui"


# ── R4 夹具：模拟「打包态取数永远失败」的 bib 页 ──────────────────────────
class _PackagedFailingBib:
    """`bib_host._load_stats` 的打包态行为：不抛异常，正常返回，stats_data 仍 None。"""

    def __init__(self) -> None:
        self.stats_data = None
        self.calls = 0

    async def _load_stats(self) -> None:
        self.calls += 1
        # 引擎子进程起不来 → result={"ok": False, "json": {}} → stats_data = None
        return None


class _HealthyBib:
    """取数成功：填上 stats_data（闸门不该拦住这条正常路径）。"""

    def __init__(self) -> None:
        self.stats_data = None
        self.calls = 0

    async def _load_stats(self) -> None:
        self.calls += 1
        self.stats_data = {"records": 1234}


class _LoopPage:
    """只记录 run_task 的调度队列，由 `_drain` 手动驱动（复刻事件循环）。"""

    def __init__(self) -> None:
        self.tasks: list = []
        self.updates = 0

    def run_task(self, handler, *args) -> None:
        self.tasks.append((handler, args))

    def update(self) -> None:
        self.updates += 1


class _LoopApp(_FakeApp):
    """够 HomePage.build() / _refresh_band() 用的假壳。"""

    def __init__(self, bib) -> None:
        super().__init__()
        self.page = _LoopPage()
        self.pages = {"bib": bib}
        self.current_page_key = "home"
        self.content = SimpleNamespace(content=None)
        self.app_version = "1.6.0"

    def open_bib_view(self, *args, **kwargs) -> None:
        pass


def _drain(app: _LoopApp, *, limit: int = 500) -> int:
    """跑完调度队列里所有后台任务，返回实际执行的任务数（超 limit 即判自激）。"""
    ran = 0
    while app.page.tasks:
        if ran >= limit:
            raise AssertionError(
                f"后台任务超过 {limit} 次仍未停：_kick_bib_stats 自激未修")
        handler, args = app.page.tasks.pop(0)
        ran += 1
        asyncio.run(handler(*args))
    return ran


def _home(bib) -> tuple[P.HomePage, _LoopApp]:
    app = _LoopApp(bib)
    return P.HomePage(app), app


# ── 1. R4 主体：取数永远失败时恰好 kick 一次 ─────────────────────────────
def test_kick_runs_exactly_once_when_stats_never_arrive():
    bib = _PackagedFailingBib()
    home, app = _home(bib)

    home.build()                       # 首页首次构建 → kick #1
    assert len(app.page.tasks) == 1, "首次 build 必须踢一次取数"
    ran = _drain(app)                  # 任务失败 → _refresh_band → build() → 不该再 kick

    assert ran == 1, f"后台取数任务执行了 {ran} 次，应为 1 次"
    assert bib.calls == 1, f"_load_stats 调用了 {bib.calls} 次，应为 1 次"
    assert app.page.tasks == [], "闸门后不得再排新的取数任务"
    assert home._bib_kick_done is True


def test_band_still_refreshes_once_on_packaged_failure():
    """闸门只管「不再 kick」，就地刷新数据带的那一次仍要发生（否则引用库永远 —）。"""
    bib = _PackagedFailingBib()
    home, app = _home(bib)

    root = home.ensure_built()
    app.content.content = root
    scroller = root.content
    _drain(app)

    assert app.page.updates == 1, "取回（失败）后应刷新一次数据带"
    assert home.root is app.content.content is root
    assert root.content is scroller


def test_second_build_does_not_kick_again():
    """导航回首页会重新 build：闸门要挡住第二次 kick。"""
    bib = _PackagedFailingBib()
    home, app = _home(bib)

    home.build()
    _drain(app)
    home.build()                       # 再次构建（如导航回来）
    _drain(app)

    assert bib.calls == 1
    assert len(app.page.tasks) == 0


def test_guard_is_per_page_instance_not_process_wide():
    """闸门挂在页面实例上：换一个 HomePage 实例仍会取数（不该变成全局开关）。"""
    bib = _PackagedFailingBib()
    first, app = _home(bib)
    first.build()
    _drain(app)

    second = P.HomePage(app)
    second.build()
    _drain(app)

    assert bib.calls == 2, "新页面实例应重新取一次数"
    assert first._bib_kick_done and second._bib_kick_done


# ── 2. 正常路径不受影响：取数成功仍刷新一次 ───────────────────────────────
def test_successful_stats_still_refresh_band():
    bib = _HealthyBib()
    home, app = _home(bib)

    root = home.ensure_built()
    app.content.content = root
    scroller = root.content
    ran = _drain(app)

    assert ran == 1
    assert bib.calls == 1
    assert bib.stats_data == {"records": 1234}
    assert app.page.updates == 1, "取到数据后必须刷新数据带"
    # 数据带真的显示了条数（1234 → "1,234"）。
    assert "1,234" in _all_text(home.root)
    assert home.root is app.content.content is root
    assert root.content is scroller


def test_stats_already_loaded_skips_kick_entirely():
    """引用库页已先取过数：首页直接借用，不再排队。"""
    bib = _HealthyBib()
    bib.stats_data = {"records": 7}
    home, app = _home(bib)

    home.build()

    assert app.page.tasks == []
    assert bib.calls == 0


# ── 3. 改前行为见证：无闸门时同一个驱动器会 kick 很多次（>1） ─────────────
def _prefix_kick(self) -> None:  # noqa: ANN001  改前 _kick_bib_stats 的函数体（原样）
    """R4 修复前的 `_kick_bib_stats`：守卫只看 stats_data，打包态恒放行。"""
    bib = self.app.pages.get("bib")
    if bib is None or bib.stats_data is not None:
        return

    async def _load() -> None:
        try:
            await bib._load_stats()
        except Exception:  # noqa: BLE001 取数失败不阻塞首页
            return
        self._refresh_band()

    self.page.run_task(_load)


def test_prefix_behaviour_kicks_forever(monkeypatch):
    """同一套夹具跑改前函数体：任务数远大于 1（这就是 P0 自激）。"""
    bib = _PackagedFailingBib()
    home, app = _home(bib)
    monkeypatch.setattr(P.HomePage, "_kick_bib_stats", _prefix_kick)
    def rebuild_old_band(self):
        self.root = self.build()
        self.app.content.content = self.root
        self.app.page.update()

    monkeypatch.setattr(P.HomePage, "_refresh_band", rebuild_old_band)

    home.build()
    with pytest.raises(AssertionError, match="自激未修"):
        _drain(app, limit=50)
    assert len(app.page.tasks) > 0, "改前状态：队列里永远还有下一次 kick"
    assert bib.calls >= 50, f"改前 _load_stats 至少被调 50 次，实测 {bib.calls}"


# ── 4. R1：notes_repo 填成 bib_db 子目录 → 提示但不断保存 ──────────────────
def _build_paths_settings(tmp_path):
    page, root = _build_settings(tmp_path)
    page.activate("paths")
    return page, root


def _notes_column(page, root) -> ft.Column:
    field = page.fields["notes_repo"][0]
    matches = [ctl for ctl in _walk(root) if isinstance(ctl, ft.Column)
               and any(any(child is field for child in _walk(row))
                       for row in (ctl.controls or []) if isinstance(row, ft.Control))]
    assert matches, "没找到 notes_repo 的 path_picker Column"
    return min(matches, key=lambda column: len(list(_walk(column))))


def _warn_text(page, root) -> ft.Text:
    column = _notes_column(page, root)
    texts = [ctl for ctl in (column.controls or []) if isinstance(ctl, ft.Text)]
    assert texts, "notes_repo 字段下方必须有常驻校验提示位"
    return texts[-1]


def _type(page, key: str, value: str) -> None:
    field = page.fields[key][0]
    field.value = value
    field.on_change(SimpleNamespace(control=field))


def test_empty_notes_repo_is_valid_and_silent(tmp_path):
    page, root = _build_paths_settings(tmp_path)
    warn = _warn_text(page, root)
    assert str(warn.value or "") == ""
    assert warn.visible is False, "合法（空）时提示位不占高度"


def test_notes_repo_pointing_at_bib_db_warns_but_saves(tmp_path):
    page, root = _build_paths_settings(tmp_path)
    warn = _warn_text(page, root)
    bad = tmp_path / "notes" / "bib_db"
    bad.mkdir(parents=True)

    _type(page, "notes_repo", str(bad))

    assert P.NOTES_REPO_SHAPE_HINT in str(warn.value), \
        f"填 bib_db 子目录必须提示，实际：{warn.value!r}"
    assert warn.visible is True
    assert "笔记仓根" in str(warn.value) and "bib_db" in str(warn.value)
    # 关键：只提示、不阻断 —— 值照样写进 store，保存路径不变。
    assert page.app.store.get("paths.notes_repo") == str(bad)


def test_notes_repo_root_with_cases_is_silent(tmp_path):
    page, root = _build_paths_settings(tmp_path)
    warn = _warn_text(page, root)
    good = tmp_path / "notes"
    (good / "cases").mkdir(parents=True)

    _type(page, "notes_repo", str(good))

    assert str(warn.value or "") == "", f"合法路径不该提示，实际：{warn.value!r}"
    assert warn.visible is False
    assert page.app.store.get("paths.notes_repo") == str(good)


def test_warning_clears_after_fixing_typo(tmp_path):
    """先填错再改对：提示必须撤掉，不能一直挂着。"""
    page, root = _build_paths_settings(tmp_path)
    warn = _warn_text(page, root)
    bad = tmp_path / "notes" / "bib_db"
    bad.mkdir(parents=True)
    good = tmp_path / "notes"
    (good / "cases").mkdir(parents=True)

    _type(page, "notes_repo", str(bad))
    assert warn.visible is True
    _type(page, "notes_repo", str(good))
    assert warn.visible is False
    assert str(warn.value or "") == ""


def test_library_dir_shape_is_also_flagged(tmp_path):
    """笔记仓根下就是 bib_db/bibliography.jsonl：填成笔记仓根 → 仍提示。"""
    root_dir = tmp_path / "notes"
    (root_dir / "bib_db").mkdir(parents=True)
    (root_dir / "bib_db" / "bibliography.jsonl").write_text(
        '{"id": "TEST-0001", "title": "Test Title"}\n', encoding="utf-8")

    assert P._notes_repo_shape_warning(str(root_dir)) == P.NOTES_REPO_SHAPE_HINT
    assert P._notes_repo_shape_warning(str(root_dir / "bib_db")) == P.NOTES_REPO_SHAPE_HINT
    # 有 cases/ 的才是笔记仓根。
    (root_dir / "cases").mkdir()
    assert P._notes_repo_shape_warning(str(root_dir)) is None


@pytest.mark.parametrize("value", ["", "   ", None])
def test_shape_warning_ignores_empty(value):
    assert P._notes_repo_shape_warning(value) is None


def test_shape_warning_ignores_unrelated_dir(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert P._notes_repo_shape_warning(str(other)) is None


def test_other_path_fields_have_no_extra_widget(tmp_path):
    """R1 只给 notes_repo 加提示位：engine_override 的控件树保持原样。"""
    page, _root = _build_settings(tmp_path)
    control = page._path_field("engine_override", "引擎目录（可空）")
    assert len(control.controls) == 1


# ── 5. R7：cancelled 默认词保持「继续」，靠「调用点必须传 next_label」兜住 ──
def _action_labels(control) -> list[str]:
    out: list[str] = []
    for ctl in _walk(control):
        if isinstance(ctl, ft.TextButton) and ctl.content is not None:
            out.append(str(ctl.content))
    return out


def test_cancelled_default_label_is_still_continue():
    """契约不变：没传 next_label 时取消态默认仍是「继续」（调用方文件不在本批写域）。"""
    t = Tokens("light", 1.0, 96)
    banner = S.state_banner(t, "cancelled", message="已取消", on_next=lambda e: None)
    assert _action_labels(banner) == ["继续"]


def _cancelled_calls(path: Path) -> list[tuple[int, set[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name != "state_banner":
            continue
        kind = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            kind = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                kind = kw.value.value
        if kind != "cancelled":
            continue
        found.append((node.lineno, {kw.arg for kw in node.keywords if kw.arg}))
    return found


def test_every_cancelled_caller_with_on_next_passes_next_label():
    """默认「继续」不可达的保证：凡传 on_next（会渲染按钮）就必须自带 next_label。

    否则用户会在健康/复核/字段修复页看到一个语义模糊的「继续」。此断言把
    「调用点自己说清楚按钮干什么」钉在源码上，改词时才不会悄悄漏掉某处。
    """
    files = sorted(APP_DIR.glob("bib_*.py"))
    assert files, "没找到 bib 页面模块"
    offenders = []
    checked = 0
    for path in files:
        for lineno, kwargs in _cancelled_calls(path):
            if "on_next" not in kwargs:
                continue          # 不传 on_next → 不渲染按钮，默认词不可达
            checked += 1
            if "next_label" not in kwargs and "action" not in kwargs:
                offenders.append(f"{path.name}:{lineno}")
    assert checked >= 3, f"应至少有三处带按钮的取消态调用点，实际 {checked}"
    assert not offenders, (
        "cancelled 调用点传了 on_next 却没给 next_label/action（会显示默认「继续」）："
        + ", ".join(offenders))


def _all_text(control) -> str:
    return "\n".join(str(ctl.value) for ctl in _walk(control)
                     if isinstance(ctl, ft.Text))
