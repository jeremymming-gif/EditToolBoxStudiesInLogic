"""引用库设置页：库文件摘要、Git 版本层文案与二次确认门槛（批 109 W1a）。

写操作本身只走 `services.bib_git`（已有 571 行实现与单测）；这里核对设置页把
「状态 → 人读摘要 → 确认文案 → 结果提示」这三层接对，且不可用时不放行。
"""
from __future__ import annotations

import json

from services import settings_store as SS
from ui import bib_settings as BS


def _probe(**overrides):
    probe = {
        "ok": True, "branch": "main", "head": "abcdef0123456789",
        "upstream": "origin/main", "ahead": 2, "behind": 0,
        "dirty_db": False, "dirty_other": 1,
        "identity": {"name": "编辑部", "email": "ed@example.org"},
        "commits": [{"sha": "1111111111111111", "subject": "引用库：入藏"},
                    {"sha": "2222222222222222", "subject": "引用库：合并"},
                    {"sha": "3333333333333333", "subject": "其它"},
                    {"sha": "4444444444444444", "subject": "第四条不显示"}],
        "warnings": [],
    }
    probe.update(overrides)
    return probe


def test_probe_lines_ok():
    lines = BS.probe_lines(_probe())
    assert lines[0] == "分支 main · upstream origin/main"
    assert lines[1] == "未推送 2 条 · 落后 0 条"
    assert lines[2] == "库文件：干净 · 其它改动 1 个"
    assert lines[3] == "提交身份：编辑部 <ed@example.org>"
    assert lines[4].startswith("最近库提交：11111111 引用库：入藏；")
    assert "第四条" not in lines[4]


def test_probe_lines_unreadable_and_conflict():
    assert BS.probe_lines(None) == ["尚未读取版本层状态。"]
    assert BS.probe_lines({"ok": False, "error": "没有 git"}) == ["读取失败：没有 git"]
    lines = BS.probe_lines(_probe(conflict=True))
    assert lines[-1].startswith("仓库状态：")


def test_confirm_texts_mention_guardrails():
    commit = BS.commit_confirm_text(_probe())
    assert "bib_db/bibliography.jsonl" in commit
    assert "没有改动不会空提交" in commit
    push = BS.push_confirm_text(_probe())
    assert "未推送 2 条" in push
    assert "只推「连续仅含 bib_db/ 文件」的提交" in push
    assert "绝不 force" in push
    rollback = BS.rollback_confirm_text()
    assert "git checkout HEAD~1 -- bib_db/bibliography.jsonl" in rollback
    assert "不推送" in rollback and "hard reset" in rollback


def test_notices_commit_push_rollback():
    tone, text = BS.commit_notice({"ok": True, "committed": True, "sha": "abcdef0123456789"})
    assert tone == "success" and "已提交" in text and "abcdef01" in text
    tone, text = BS.commit_notice({"ok": True, "committed": False,
                                   "reason": "库文件没有改动，未生成空提交。"})
    assert tone == "info" and "没有改动" in text
    tone, text = BS.commit_notice({"ok": False, "soft": True, "error": "提交身份未配置"})
    assert tone == "warn" and "提交失败" in text

    tone, text = BS.push_notice({"ok": True, "pushed": True, "count": 2,
                                 "tip": "abcdef0123456789"})
    assert tone == "success" and "已推送 2 条" in text
    tone, text = BS.push_notice({"ok": True, "pushed": False, "reason": "没有待推送提交。"})
    assert tone == "info" and text == "没有待推送提交。"
    tone, text = BS.push_notice({"ok": False, "soft": True,
                                 "error": "最旧的未推送提交 deadbeef 含非 bib_db/ 文件（README.md）"})
    assert tone == "warn" and "推送被拒绝" in text and "README.md" in text

    tone, text = BS.rollback_notice({"ok": True, "reason": "已回退并生成新提交（sha abc）。"})
    assert tone == "success" and "已回退" in text
    tone, _text = BS.rollback_notice({"ok": False, "error": "没有 HEAD~1"})
    assert tone == "error"


def test_git_available_blocks_conflict_merge_rebase():
    assert BS.git_available(_probe()) is True
    assert BS.git_available({"ok": False, "error": "x"}) is False
    for flag in ("conflict", "merge", "rebase"):
        assert BS.git_available(_probe(**{flag: True})) is False


class _FakeApp:
    def __init__(self, store) -> None:
        self.store = store
        self.notes_repo = ""
        self.toasts: list[str] = []

    def toast(self, message: str) -> None:
        self.toasts.append(message)


class _FakeBibPage:
    """只给 `BibSettingsView` 用到的壳：`app.store` / `mark` / `_render`。"""

    def __init__(self, store) -> None:
        self.app = _FakeApp(store)
        self.stats_data = None
        self.events: list[tuple[str, dict]] = []
        self.renders = 0

    def mark(self, event: str, **fields) -> None:
        self.events.append((event, fields))

    def _render(self) -> None:
        self.renders += 1


def _view(tmp_path, monkeypatch):
    monkeypatch.setenv("P2_SETTINGS", str(tmp_path / "settings.json"))
    store = SS.SettingsStore()
    store.load()
    return BS.BibSettingsView(_FakeBibPage(store)), store


def test_save_bibdb_writes_section(tmp_path, monkeypatch):
    view, store = _view(tmp_path, monkeypatch)
    view.bibdb_text = "0"
    view.bibdb_plan, view.bibdb_all, view.bibdb_lookup = True, False, False
    view._save_bibdb()
    assert store.data["bibdb"] == {"verify_max": 0, "verify_plan": True,
                                   "verify_all_refs": False, "lookup_enabled": False}
    assert view.bibdb_receipt.startswith("✅ 已保存 · ")
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw["flet"]["bibdb"]["verify_max"] == 0
    # 生效值：0 = 不限，真进 argv；先算账开 = --verify-plan；关掉查库 = --no-bibdb
    assert SS.bibdb_convert_args(store.data) == {"verify_max": 0, "verify_plan": True,
                                                 "no_bibdb": True}
    assert any(event == "bib_settings_bibdb" for event, _ in view.page.events)


def test_save_bibdb_rejects_bad_max(tmp_path, monkeypatch):
    view, store = _view(tmp_path, monkeypatch)
    view.bibdb_text = "-3"
    view._save_bibdb()
    assert view.bibdb_receipt == "未保存：核实上限输入有误（修好再保存）。"
    assert view.bibdb_error == "上限不能是负数（要「不限」请填 0）"
    assert store.data["bibdb"]["verify_max"] is None          # 一个键都没写
    assert (tmp_path / "settings.json").exists() is False
    assert not any(event == "bib_settings_bibdb" for event, _ in view.page.events)


def test_on_verify_max_hint_without_mount(tmp_path, monkeypatch):
    """输入非法时 hint 立刻变红字；控件未挂载（无 update）也不能崩。"""
    view, _store = _view(tmp_path, monkeypatch)
    view._on_verify_max(None)
    assert view.bibdb_error == ""
    fake = type("E", (), {"control": type("C", (), {"value": "abc"})()})()
    view._on_verify_max(fake)
    assert view.bibdb_error == "上限必须是整数（0 = 不限；留空 = 引擎默认 120 条）"


def test_library_summary_and_sizes():
    summary = BS.library_summary({
        "db": "D:/notes/bib_db/bibliography.jsonl",
        "aliases": {"records": 376, "total": 400},
        "bytes": 3_605_834,
    })
    assert summary == {"db": "D:/notes/bib_db/bibliography.jsonl", "records": "376",
                       "aliases_total": "400", "size": "3.4 MB"}
    empty = BS.library_summary(None)
    assert empty["records"] == "—" and empty["size"] == "—"
    assert BS.fmt_size(0) == "0 B"
    assert BS.fmt_size(2048) == "2.0 KB"
    assert BS.fmt_size("x") == "—"
