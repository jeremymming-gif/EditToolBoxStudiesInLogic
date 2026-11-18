"""批 246 F2：按钮语义（P3）/ 复制文案（P4）/ 设置立即生效（P6）。

锁三件事（不建窗口、不碰真库、不起子进程）：
1. P3 `state_banner(..., next_label=…)` 真的决定按钮文字——用户报障「重试按钮却在复制」
   就是 `next_label` 被忽略（`bib_overview` 传「复制摘要」却显示「重试」）；
   没传 `next_label` 时各状态仍保留原来的默认文字（失败「重试」、取消「继续」）。
2. P4 复制成功文案按内容区分：路径说「已复制路径」，错误摘要/诊断文本说「已复制」。
3. P6 保存设置后 `app.notes_repo` 立即刷新（此前必须重启），且**不写** `SLCN_NOTES`
   环境变量（未校验的路径塞进环境变量会让引擎严格判定后不回退）。
"""
from __future__ import annotations

from pathlib import Path

import flet as ft
import pytest

import main as M
from services import settings_store as SS
from ui import bib_shell as S
from ui import components as C
from ui.tokens import Tokens


# ── 夹具 ────────────────────────────────────────────────────────────────
class _FakePage:
    def __init__(self) -> None:
        self.on_keyboard_event = None
        self.dialogs = []
        self.tasks = []
        self.width = 1160

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)


def _action_labels(control) -> list[str]:
    """横幅里的动作按钮文字（按出现顺序）。"""
    labels: list[str] = []
    if isinstance(control, ft.TextButton):
        labels.append(str(control.content))
    for child in list(getattr(control, "controls", []) or []):
        labels.extend(_action_labels(child))
    for child in list(getattr(control, "actions", []) or []):
        labels.extend(_action_labels(child))
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        labels.extend(_action_labels(content))
    return labels


def _store(tmp_path: Path) -> SS.SettingsStore:
    store = SS.SettingsStore(path=tmp_path / "settings.json")
    store.load()
    return store


def _app(store: SS.SettingsStore) -> M.App:
    app = M.App.__new__(M.App)
    app.store = store
    app.notes_repo = ""
    app.current_page_key = "settings"
    app.current_route = "settings"
    app.nav_open = {"page": "settings", "group": ""}
    app.marked: list = []
    app.toasts: list[str] = []
    app.mark = lambda event, **fields: app.marked.append((event, fields))
    app.toast = lambda message: app.toasts.append(message)
    app.refresh_engine_status = lambda: None
    return app


def _marks(app, name: str) -> list:
    return [fields for event, fields in app.marked if event == name]


# ── P3：next_label 决定按钮文字 ─────────────────────────────────────────
def test_failure_banner_uses_caller_next_label() -> None:
    """bib_overview 的报障点：传「复制摘要」就必须显示「复制摘要」，不能是「重试」。"""
    t = Tokens()
    copied: list = []
    page = _FakePage()
    banner = S.state_banner(t, "failure", message="读库失败",
                            next_label="复制摘要",
                            on_next=lambda e: copied.append(e),
                            page=page)
    assert _action_labels(banner) == ["复制摘要"]
    banner.content.controls[2].on_click(None)
    assert len(copied) == 1


def test_failure_banner_label_matches_behavior_for_every_caller() -> None:
    """三处真实调用点的「标签 ↔ 行为」都要自洽（标签由调用方决定）。"""
    t = Tokens()
    for label in ("复制摘要", "复制错误", "重试"):
        done: list[str] = []
        banner = S.state_banner(t, "failure", message="失败", next_label=label,
                                on_next=lambda e, label=label: done.append(label))
        assert _action_labels(banner) == [label]
        banner.content.controls[2].on_click(None)
        assert done == [label]


def test_next_label_wins_on_success_and_empty() -> None:
    """成功/空态同样认 next_label（概览「重新统计」、查询空态「高级检索」）。"""
    t = Tokens()
    ok = S.state_banner(t, "success", message="已完成",
                        next_label="去入藏", on_next=lambda e: None)
    assert _action_labels(ok) == ["去入藏"]
    empty = S.state_banner(t, "empty", message="没有记录",
                           next_label="高级检索", on_next=lambda e: None)
    assert _action_labels(empty) == ["高级检索"]


def test_next_label_wins_on_cancelled() -> None:
    """取消态也认 next_label（规范化页「重新生成」），否则才回落「继续」。"""
    t = Tokens()
    named = S.state_banner(t, "cancelled", message="已取消",
                           next_label="重新生成", on_next=lambda e: None)
    assert _action_labels(named) == ["重新生成"]
    default = S.state_banner(t, "cancelled", message="已取消", on_next=lambda e: None)
    assert _action_labels(default) == ["继续"]


def test_missing_next_label_keeps_state_defaults() -> None:
    """没传 next_label 时不能退化成同一个「下一步」：失败仍是「重试」。"""
    t = Tokens()
    assert _action_labels(S.state_banner(t, "failure", message="失败",
                                         on_next=lambda e: None)) == ["重试"]
    assert _action_labels(S.state_banner(t, "success", message="完成",
                                         on_next=lambda e: None)) == ["下一步"]
    assert _action_labels(S.state_banner(t, "empty", message="空",
                                         on_next=lambda e: None)) == ["下一步"]


def test_caller_action_still_beats_generated_button() -> None:
    """显式 action 优先：传了 action 就不该再按 next_label 造按钮。"""
    t = Tokens()
    custom = C.text_button("刷新后重查", None, t, height=28)
    banner = S.state_banner(t, "failure", message="失败", action=custom,
                            next_label="重试", on_next=lambda e: None)
    assert _action_labels(banner) == ["刷新后重查"]


# ── P4：复制成功文案按内容区分 ──────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "/Users/me/EditToolBox-notes/bib_db/bibliography.jsonl",
    "~/notes/cases/K1",
    "./out/report.pdf",
    "../shared/x.tex",
    r"C:\Users\me\notes\cases",
    "C:/Users/me/notes",
    r"\\server\share\notes",
])
def test_looks_like_path_accepts_paths(text: str) -> None:
    assert M._looks_like_path(text) is True


@pytest.mark.parametrize("text", [
    "",
    "已复制",
    "Traceback (most recent call last):\n  File \"x.py\", line 1",
    "key\ttype\ttitle\tstatus",                      # pages.py:3062 的制表符信息行
    "日志尾部：\n第 1 行\n第 2 行",                    # 设置页复制全部日志
    "诊断：flet 1.0.5 / python 3.14",
    "/" * 300,                                       # 超长内容不可能是单条路径
])
def test_looks_like_path_rejects_non_paths(text: str) -> None:
    assert M._looks_like_path(text) is False


def _ok_run(*_a, **_k):
    """真实的 `subprocess.run` 一定返回带 int `returncode` 的对象。

    以前这里用 `lambda *a, **k: None` 冒充成功——那正是「把返回值当成功」的
    同一个盲区（验收 §3.2-13）。夹具按真实形态返回。
    """
    from types import SimpleNamespace
    return SimpleNamespace(returncode=0)


def _clip_app() -> M.App:
    app = M.App.__new__(M.App)
    app.toasts: list[str] = []
    app.marked: list = []
    app.toast = lambda message: app.toasts.append(message)
    app.mark = lambda event, **fields: app.marked.append((event, fields))
    return app


def test_copy_text_says_path_for_path(monkeypatch) -> None:
    app = _clip_app()
    monkeypatch.setattr(M.subprocess, "run", _ok_run)
    app.copy_text("/Users/me/notes/bibliography.jsonl")
    assert app.toasts == ["已复制路径"]
    assert _marks(app, "copy_path")[0]["path"].endswith("bibliography.jsonl")


def test_copy_text_says_generic_for_error_summary(monkeypatch) -> None:
    """复制错误摘要 / 诊断文本时不能说「已复制路径」（用户报障 P4）。"""
    app = _clip_app()
    monkeypatch.setattr(M.subprocess, "run", _ok_run)
    app.copy_text("Traceback (most recent call last):\n  File \"bib.py\", line 7")
    assert app.toasts == ["已复制"]


def test_copy_text_nonzero_or_unknown_returncode_is_failure(monkeypatch) -> None:
    """`returncode` 非 0（或根本不是 int）必须当失败——不许「看起来没报错就算成功」。"""
    from types import SimpleNamespace

    app = _clip_app()
    monkeypatch.setattr(M.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=3))
    assert app.copy_text("/tmp/x") is False
    assert app.toasts == ["复制失败"]

    app2 = _clip_app()
    monkeypatch.setattr(M.subprocess, "run", lambda *a, **k: SimpleNamespace())
    assert app2.copy_text("/tmp/x") is False
    assert app2.toasts == ["复制失败"]

    app3 = _clip_app()
    monkeypatch.setattr(M.subprocess, "run", _ok_run)
    assert app3.copy_text("/tmp/x") is True


def test_copy_text_empty_and_failure_messages(monkeypatch) -> None:
    app = _clip_app()
    app.copy_text("   ")
    assert app.toasts == ["没有可复制的内容"]

    def _boom(*a, **k):
        raise OSError("no clipboard")

    monkeypatch.setattr(M.subprocess, "run", _boom)
    app.copy_text("/tmp/x")
    assert app.toasts == ["没有可复制的内容", "复制失败"]


# ── P6：保存设置后 notes_repo 立即生效 ──────────────────────────────────
def test_save_settings_refreshes_notes_repo(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    app = _app(store)
    app.notes_repo = "/old/notes"
    repo = tmp_path / "EditToolBox-notes"
    (repo / "cases").mkdir(parents=True)
    store.set("paths.notes_repo", str(repo))

    assert app.save_settings() is True
    assert app.notes_repo == str(repo)
    assert _marks(app, "notes_repo")[0]["has_cases"] is True


def test_save_settings_never_writes_slcn_notes_env(tmp_path, monkeypatch) -> None:
    """未校验输入不许进环境变量：引擎对已设的 SLCN_NOTES 严格判定、不回退。"""
    monkeypatch.delenv("SLCN_NOTES", raising=False)
    store = _store(tmp_path)
    app = _app(store)
    app.notes_repo = ""
    store.set("paths.notes_repo", str(tmp_path / "no-cases-here"))  # 故意没有 cases/

    assert app.save_settings() is True
    assert app.notes_repo == str(tmp_path / "no-cases-here")
    assert "SLCN_NOTES" not in M.os.environ
    assert _marks(app, "notes_repo")[0]["has_cases"] is False


def test_save_settings_keeps_notes_repo_when_unchanged(tmp_path) -> None:
    """值没变就不重复记 marker（与 _apply_engine_override 的口径一致）。"""
    store = _store(tmp_path)
    app = _app(store)
    repo = tmp_path / "notes"
    (repo / "cases").mkdir(parents=True)
    store.set("paths.notes_repo", str(repo))
    app.notes_repo = str(repo)

    assert app.save_settings() is True
    assert _marks(app, "notes_repo") == []


def test_clearing_notes_repo_takes_effect_immediately(tmp_path) -> None:
    store = _store(tmp_path)
    app = _app(store)
    app.notes_repo = "/old/notes"
    store.set("paths.notes_repo", "")

    assert app.save_settings() is True
    assert app.notes_repo == ""
    assert _marks(app, "notes_repo")[0]["has_cases"] is False
