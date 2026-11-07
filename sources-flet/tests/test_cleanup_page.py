"""T3 清理页：单入口（自动校验+回收）、结果绑定、撤销本批次、整期串行 opt-in。

不起 GUI、不跑真引擎：EngineCall 由本文件桩住，JSON 契约按 INTERFACE-DAILY.md。
"""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

import flet as ft
import pytest

from services import engine_bridge as EB
from ui import assembly as ASM
from ui import cleanup as CLEANUP
from ui import components as C
from test_b112_post_b import _walk
from test_b116_issue_run import _Page, make_page

OP = "2026_11_07_110000-abcd1234"


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    root = tmp_path / "Engine"
    root.mkdir()
    for entry in EB.ENTRIES:
        (root / entry).write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("P2_ENGINE", str(root))
    for name in ("P2_ENGINE_EXE", "SLCN_ENGINE_EXE", "P2_ENGINE_PYTHON", "SLCN_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    return root


def _panel(page):
    panel = CLEANUP.CleanupPanel(page)
    page._cleanup = panel
    return panel


def _payload(root, **over):
    data = {
        "ok": True, "command": "cleantemp", "mode": "daily", "dryRun": False,
        "root": root, "count": 0, "entryCount": 0, "entries": [],
        "trashDir": "", "restoreDir": "", "failures": [], "warnings": [],
        "error": "", "operationId": "", "status": "success", "kept": [],
    }
    data.update(over)
    return data


def _install(monkeypatch, page, script):
    """桩 EB.run_call：script 第 i 项 = (payload|None, code)；逐次记录 EngineCall。"""
    calls: list[EB.EngineCall] = []

    async def fake_run_call(call, *, on_line=None, on_proc=None, notes="", **kwargs):
        calls.append(call)
        index = len(calls) - 1
        payload, code = script[index] if index < len(script) else (None, 0)
        if payload is not None and on_line is not None:
            on_line(json.dumps(payload, ensure_ascii=False))
        if code == "cancel":
            page.app.stop_run()
            return 0
        return int(code)

    monkeypatch.setattr(EB, "run_call", fake_run_call)
    return calls


def _args(call: EB.EngineCall) -> list[str]:
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


def _texts(control) -> list[str]:
    values: list[str] = []
    for ctl in _walk(control):
        if isinstance(ctl, ft.Text):
            values.append(str(ctl.value or ""))
        elif isinstance(ctl, (ft.Button, ft.FilledButton, ft.OutlinedButton,
                             ft.TextButton)):
            values.append(str(ctl.content or ""))
    return values


def _undo_button(control):
    for ctl in _walk(control):
        if isinstance(ctl, ft.OutlinedButton) and "撤销" in str(ctl.content or ""):
            return ctl
    return None


def test_cleanup_one_click_ignores_legacy_modes(tmp_path, monkeypatch):
    root = str(tmp_path / "期A")
    (tmp_path / "期A").mkdir()
    page = make_page(tmp_path / "期A")
    # 旧设置全部调成「会改变行为」的组合：三档 workspace / 只预演关 / 中间件关 / 全部报告开。
    page.app.store.set("form.cleantemp_mode", "workspace")
    page.app.store.update_options(cleantemp_dry_run=False, cleantemp_tocwork=False,
                                  cleantemp_all_reports=True)
    batch = root + "/.toolbox-trash/期A-" + OP
    calls = _install(monkeypatch, page, [
        (_payload(root, dryRun=True, count=2, entryCount=2,
                  entries=[{"path": "a.aux"}, {"path": "b.log"}],
                  status="success"), 0),
        (_payload(root, count=2, entryCount=2, operationId=OP, trashDir=batch,
                  entries=[{"path": "a.aux", "action": "move"},
                           {"path": "b.log", "action": "move"}],
                  kept=[{"path": "paper.pdf", "kind": "referenced", "reason": "被 .tex 引用"}],
                  status="success"), 0),
    ])
    panel = _panel(page)

    panel.start()  # 一次点击：自动校验 + 清理

    assert len(calls) == 2
    assert _args(calls[0]) == ["--folder", root, "--mode", "daily", "--dry-run", "--json"]
    assert _args(calls[1]) == ["--folder", root, "--mode", "daily", "--json"]
    for argv in (_args(calls[0]), _args(calls[1])):
        assert "workspace" not in argv and "delivery" not in argv
        assert "--trash" not in argv and "--all-reports" not in argv
    rec = panel.record()
    assert rec["status"] == "success" and rec["count"] == 2 and rec["operationId"] == OP
    assert panel.phase == ""
    texts = _texts(panel.result())
    assert any("实际回收 2 个文件" in text for text in texts), texts
    assert "撤销本次清理" in texts and "查看明细" in texts
    assert _undo_button(panel.result()).disabled is False


def test_cleanup_result_matches_operation_and_partial_failure(tmp_path, monkeypatch):
    root_a = tmp_path / "期A"
    root_b = tmp_path / "期B"
    root_a.mkdir(); root_b.mkdir()
    page = make_page(root_a)
    batch = str(root_a / ".toolbox-trash" / ("期A-" + OP))
    calls = _install(monkeypatch, page, [
        (_payload(str(root_a), dryRun=True, count=3,
                  entries=[{"path": "a.aux"}, {"path": "b.log"}, {"path": "c.synctex.gz"}]), 0),
        (_payload(str(root_a), count=2, entryCount=3, operationId=OP, trashDir=batch,
                  entries=[{"path": "a.aux"}, {"path": "b.log"}],
                  kept=[{"path": "paper.pdf", "kind": "referenced",
                         "reason": "被 .tex 引用"}],
                  failures=[{"path": "c.synctex.gz", "error": "EBUSY"}],
                  ok=False, status="partial"), 2),
    ])
    panel = _panel(page)

    panel.start()

    rec = panel.record()
    assert (rec["status"], rec["count"], rec["operationId"]) == ("partial", 2, OP)
    texts = _texts(panel.result())
    assert any("实际回收 2 个文件" in text and "1 项失败" in text for text in texts), texts
    assert any("保留 1 项" in text for text in texts)
    assert any("paper.pdf" in text and "被 .tex 引用" in text for text in texts)
    assert any("c.synctex.gz" in text and "EBUSY" in text for text in texts)
    assert "已撤销本次清理" not in texts
    assert _undo_button(panel.result()).disabled is False

    # 结果绑 root：切到另一期不显示本期结果；切回来/刷新重建仍在。
    page.set_field("issue_dir", str(root_b))
    other = _texts(panel.result())
    assert any("尚未清理" in text for text in other), other
    assert not any("实际回收" in text for text in other)
    page.set_field("issue_dir", str(root_a))
    fresh = _panel(page)  # 模拟重建/重新进入页面：结果从持久化状态恢复
    refreshed = _texts(fresh.result())
    assert any("实际回收 2 个文件" in text for text in refreshed), refreshed
    assert fresh.record()["operationId"] == OP


class _AsyncTaskPage:
    def __init__(self):
        self.task = None

    def run_task(self, handler, *args):
        self.task = asyncio.ensure_future(handler(*args))

    def update(self):
        pass


def test_cleanup_late_callback_does_not_cross_issue(tmp_path, monkeypatch):
    root_a = tmp_path / "期A"
    root_b = tmp_path / "期B"
    root_a.mkdir(); root_b.mkdir()

    async def scenario():
        page = make_page(root_a)
        page.page = _AsyncTaskPage()
        panel = _panel(page)
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[EB.EngineCall] = []

        async def fake_run_call(call, *, on_line=None, on_proc=None, notes="", **kwargs):
            calls.append(call)
            started.set()
            await release.wait()
            if on_line is not None:
                on_line(json.dumps(_payload(str(root_a), dryRun=True, count=2,
                                            entries=[{"path": "a.aux"}]), ensure_ascii=False))
            return 0

        monkeypatch.setattr(EB, "run_call", fake_run_call)

        panel.start()
        await started.wait()
        assert panel.phase == "scanning" and page._status["cleantemp"] == "running"

        # 扫描未结束时换期/重建：旧回调失效，不能把新一期留在「正在扫描」。
        panel.reset()
        page.set_field("issue_dir", str(root_b))
        assert panel.phase == ""
        release.set()
        await page.page.task

        assert panel.phase == "", "晚到回调不能把清理页留在运行中"
        assert panel.record(str(root_a)) == {}
        assert panel.record(str(root_b)) == {}
        assert page.app.store.get("issue_state", {}) == {}
        assert page._status["cleantemp"] == "running"  # 未被旧回调改成 done/error

    asyncio.run(scenario())


def test_cleanup_restore_uses_exact_batch_and_handles_conflicts(tmp_path, monkeypatch):
    root = str(tmp_path / "期A")
    base = tmp_path / "期A" / ".toolbox-trash"
    old_batch = base / ("期A-" + OP)
    new_batch = base / "期A-2026_11_07_120000-efffffff"
    old_batch.mkdir(parents=True); new_batch.mkdir()
    page = make_page(tmp_path / "期A")
    page.app.store.set("issue_state", {root: {"cleanup": {
        "schema": 1, "root": root, "operationId": OP, "status": "success",
        "count": 2, "entryCount": 2,
        "entries": [{"path": "a.aux", "action": "move"},
                    {"path": "b.aux", "action": "move"}],
        "kept": [], "failures": [], "warnings": [], "error": "",
        "trashDir": str(old_batch),
        "startedAt": "2026-11-07 11:00:00", "finishedAt": "2026-11-07 11:00:02",
    }}})
    calls = _install(monkeypatch, page, [
        ({"ok": False, "command": "cleantemp", "mode": "restore", "dryRun": False,
          "root": root, "count": 1, "entryCount": 2, "entries": [],
          "trashDir": str(old_batch), "restoreDir": root,
          "failures": [{"path": "b.aux", "error": "Skip (已经存在，不覆盖)"}],
          "warnings": [], "error": "", "operationId": OP,
          "status": "partial", "kept": []}, 2),
    ])
    panel = _panel(page)

    panel.restore_last()

    # 撤销必须精确回到该结果记录里的批次，不能按 mtime 猜「最新目录」。
    assert len(calls) == 1
    assert _args(calls[0]) == ["--folder", root, "--restore", str(old_batch), "--json"]
    rec = panel.record()
    assert rec["restore"]["status"] == "partial" and rec["restore"]["count"] == 1
    texts = _texts(panel.result())
    assert any("已恢复 1 项" in text and "同名冲突" in text for text in texts), texts
    undo = _undo_button(panel.result())
    assert undo is not None and undo.disabled is False  # 冲突可重试
    assert any("部分文件未恢复" in toast for toast in page.app.toasts)


def test_cleanup_runall_is_opt_in_daily(tmp_path, engine, monkeypatch):
    (tmp_path / "期A").mkdir()
    page = make_page(tmp_path / "期A", report=True)
    panel = _panel(page)
    calls: list[EB.EngineCall] = []

    async def fake_run_call(call, *, on_line=None, on_proc=None, notes="", **kwargs):
        calls.append(call)
        if call.entry == "cleantemp.py" and on_line is not None:
            on_line(json.dumps(_payload(
                str(page.field("issue_dir")), count=1, entryCount=1,
                operationId=OP, trashDir=str(tmp_path / "期A" / ".toolbox-trash" /
                                             ("期A-" + OP)),
                entries=[{"path": "a.aux", "action": "move"}],
                status="success"), ensure_ascii=False))
        return 0

    monkeypatch.setattr(EB, "run_call", fake_run_call)

    assert inspect.signature(ASM.run_all).parameters["keys"].default is None
    assert "cleantemp" not in ASM.DEFAULT_RUN_ALL_KEYS

    ASM.run_all(page)  # 默认整期：不清理
    assert [call.entry for call in calls] == [
        "infocheck.py", "toc.py", "compileall.py", "mergepdf.py",
    ]
    assert panel.record() == {}

    calls.clear()
    ASM.run_all(page, ["cleantemp"])  # 显式选择才走 daily
    assert [call.entry for call in calls] == ["cleantemp.py"]
    assert _args(calls[0]) == ["--folder", str(tmp_path / "期A"), "--mode", "daily", "--json"]
    rec = panel.record()
    assert rec["status"] == "success" and rec["operationId"] == OP and rec["count"] == 1
    assert page._status["cleantemp"] == "done"
    assert page._cleanup.phase == ""

def test_cleanup_empty_keeps_previous_batch_undo(tmp_path, monkeypatch):
    root = str(tmp_path / "期A")
    (tmp_path / "期A").mkdir()
    page = make_page(tmp_path / "期A")
    batch = root + "/.toolbox-trash/期A-" + OP
    calls = _install(monkeypatch, page, [
        (_payload(root, dryRun=True, count=1,
                  entries=[{"path": "a.aux"}]), 0),
        (_payload(root, count=1, entryCount=1, operationId=OP, trashDir=batch,
                  entries=[{"path": "a.aux", "action": "move"}],
                  status="success"), 0),
        (_payload(root, dryRun=True, count=0, status="empty"), 0),
        ({"ok": True, "command": "cleantemp", "mode": "restore", "dryRun": False,
          "root": root, "count": 1, "entryCount": 1, "entries": [],
          "trashDir": batch, "restoreDir": root, "failures": [], "warnings": [],
          "error": "", "operationId": OP, "status": "success", "kept": []}, 0),
    ])
    panel = _panel(page)

    panel.start()
    assert panel.record()["status"] == "success"
    panel.start()  # 空候选：不建批次，但上一批次必须仍可撤销

    rec = panel.record()
    assert (rec["status"], rec["count"], rec["entries"]) == ("empty", 0, [])
    assert rec["lastTrashDir"] == batch and rec["lastOperationId"] == OP
    assert rec["lastCount"] == 1
    texts = _texts(panel.result())
    assert any("没有可清理的临时文件（未创建新批次）；上次清理批次仍可撤销。" in text
               for text in texts), texts
    undo = _undo_button(panel.result())
    assert undo is not None and str(undo.content) == "撤销上次清理"
    assert undo.disabled is False

    shown: list = []
    page.page.show_dialog = lambda dialog: shown.append(dialog)
    page.page.pop_dialog = lambda: None
    C._open_dialogs.clear()
    panel.details_dialog()
    details = [str(ctl.value) for ctl in _walk(shown[0].content) if isinstance(ctl, ft.Text)]
    assert any("上次可撤销批次" in text for text in details), details
    assert any(OP in text for text in details), details
    assert any(batch in text for text in details), details
    assert any("1 个文件" in text for text in details), details
    C._open_dialogs.clear()

    panel.restore_last()  # 必须精确用上一批次，而不是本次空结果
    assert len(calls) == 4
    assert _args(calls[3]) == ["--folder", root, "--restore", batch, "--json"]
    rec = panel.record()
    assert rec["restore"]["status"] == "success"
    assert not rec.get("lastTrashDir")
    undo = _undo_button(panel.result())
    assert undo is not None and undo.disabled is True
    assert "已撤销上次清理" in str(undo.content)
    texts = _texts(panel.result())
    assert any("已撤销上次清理：恢复 1 个文件。" in text for text in texts), texts
    assert any("没有可清理的临时文件（未创建新批次）；上次清理已撤销。" in text
               for text in texts), texts


def test_cleanup_empty_without_previous_is_not_undoable(tmp_path, monkeypatch):
    root = str(tmp_path / "期A")
    (tmp_path / "期A").mkdir()
    page = make_page(tmp_path / "期A")
    calls = _install(monkeypatch, page, [(_payload(root, status="empty"), 0)])
    panel = _panel(page)

    panel.start()

    rec = panel.record()
    assert rec["status"] == "empty"
    assert not rec.get("lastTrashDir") and not rec.get("lastOperationId")
    texts = _texts(panel.result())
    assert any("没有可清理的临时文件（未创建批次，也没有撤销）。" in text
               for text in texts), texts
    undo = _undo_button(panel.result())
    assert undo is not None and str(undo.content) == "撤销本次清理"
    assert undo.disabled is True

    panel.restore_last()  # 无上批：不发 restore 命令
    assert len(calls) == 1
    assert page.app.toasts[-1] == "本次没有可清理的临时文件，无需撤销"
    assert panel.record() == rec


def test_cleanup_new_success_replaces_last_batch(tmp_path, monkeypatch):
    root = str(tmp_path / "期A")
    (tmp_path / "期A").mkdir()
    page = make_page(tmp_path / "期A")
    op1 = OP
    op2 = "2026_11_07_120000-bbbbbbbb"
    batch1 = root + "/.toolbox-trash/期A-" + op1
    batch2 = root + "/.toolbox-trash/期A-" + op2
    calls = _install(monkeypatch, page, [
        (_payload(root, dryRun=True, count=1, entries=[{"path": "a.aux"}]), 0),
        (_payload(root, count=1, operationId=op1, trashDir=batch1,
                  entries=[{"path": "a.aux"}], status="success"), 0),
        (_payload(root, status="empty"), 0),
        (_payload(root, dryRun=True, count=1, entries=[{"path": "b.aux"}]), 0),
        (_payload(root, count=1, operationId=op2, trashDir=batch2,
                  entries=[{"path": "b.aux"}], status="success"), 0),
        ({"ok": True, "command": "cleantemp", "mode": "restore", "dryRun": False,
          "root": root, "count": 1, "entryCount": 1, "entries": [],
          "trashDir": batch2, "restoreDir": root, "failures": [], "warnings": [],
          "error": "", "operationId": op2, "status": "success", "kept": []}, 0),
    ])
    panel = _panel(page)

    panel.start()
    panel.start()  # empty：last 仍指 op1
    assert panel.record()["lastTrashDir"] == batch1
    panel.start()  # 新成功批次：last 改指 op2

    rec = panel.record()
    assert (rec["status"], rec["operationId"], rec["count"]) == ("success", op2, 1)
    assert rec["lastTrashDir"] == batch2 and rec["lastOperationId"] == op2
    assert "restore" not in rec, "新批次的记录不能带上一批的撤销结论"

    panel.restore_last()
    assert len(calls) == 6
    assert _args(calls[5]) == ["--folder", root, "--restore", batch2, "--json"]
    assert panel.record()["restore"]["status"] == "success"
    assert _undo_button(panel.result()).disabled is True


def _restore_payload(root, batch, operation_id=OP, **over):
    data = {"ok": True, "command": "cleantemp", "mode": "restore", "dryRun": False,
            "root": root, "count": 1, "entryCount": 1, "entries": [],
            "trashDir": batch, "restoreDir": root, "failures": [], "warnings": [],
            "error": "", "operationId": operation_id, "status": "success",
            "kept": []}
    data.update(over)
    return data


def test_cleanup_undo_then_empty_does_not_revive_undone_batch(tmp_path, monkeypatch):
    root = str(tmp_path / "期A")
    (tmp_path / "期A").mkdir()
    page = make_page(tmp_path / "期A")
    batch = root + "/.toolbox-trash/期A-" + OP
    calls = _install(monkeypatch, page, [
        (_payload(root, dryRun=True, count=1, entries=[{"path": "a.aux"}]), 0),
        (_payload(root, count=1, entryCount=1, operationId=OP, trashDir=batch,
                  entries=[{"path": "a.aux", "action": "move"}], status="success"), 0),
        (_restore_payload(root, batch), 0),
        (_payload(root, dryRun=True, status="empty"), 0),
    ])
    panel = _panel(page)

    panel.start()
    panel.restore_last()  # 撤销成功：该批次用完
    rec = panel.record()
    assert rec["restore"]["status"] == "success"
    assert not rec.get("lastTrashDir") and not rec.get("lastOperationId")
    assert _undo_button(panel.result()).disabled is True

    panel.start()  # 再清理得 empty：不得把刚撤销的批次当成「上次可撤销」

    rec = panel.record()
    assert (rec["status"], rec["count"]) == ("empty", 0)
    assert not rec.get("lastTrashDir") and not rec.get("lastOperationId")
    assert not rec.get("lastCount") and not rec.get("lastFinishedAt")
    assert "restore" not in rec
    texts = _texts(panel.result())
    assert not any("仍可撤销" in text for text in texts), texts
    assert any("没有可清理的临时文件（未创建批次，也没有撤销）。" in text
               for text in texts), texts
    undo = _undo_button(panel.result())
    assert undo is not None and undo.disabled is True

    panel.restore_last()  # 无目标：不得再向已撤销批次发 restore
    assert len(calls) == 4
    assert page.app.toasts[-1] == "本次没有可清理的临时文件，无需撤销"


def test_cleanup_empty_undo_then_empty_keeps_undone_batch_dead(tmp_path, monkeypatch):
    root = str(tmp_path / "期A")
    (tmp_path / "期A").mkdir()
    page = make_page(tmp_path / "期A")
    batch = root + "/.toolbox-trash/期A-" + OP
    calls = _install(monkeypatch, page, [
        (_payload(root, dryRun=True, count=1, entries=[{"path": "a.aux"}]), 0),
        (_payload(root, count=1, entryCount=1, operationId=OP, trashDir=batch,
                  entries=[{"path": "a.aux", "action": "move"}], status="success"), 0),
        (_payload(root, dryRun=True, status="empty"), 0),
        (_restore_payload(root, batch), 0),
        (_payload(root, dryRun=True, status="empty"), 0),
    ])
    panel = _panel(page)

    panel.start()
    panel.start()  # empty：上一批次仍可撤销
    assert panel.record()["lastTrashDir"] == batch
    panel.restore_last()  # 撤销的是上次批次
    rec = panel.record()
    assert rec["restore"]["status"] == "success"
    assert not rec.get("lastTrashDir") and not rec.get("lastOperationId")

    panel.start()  # 再 empty：已撤销批次不得复活

    rec = panel.record()
    assert (rec["status"], rec["count"]) == ("empty", 0)
    assert not rec.get("lastTrashDir") and not rec.get("lastOperationId")
    assert "restore" not in rec
    texts = _texts(panel.result())
    assert not any("仍可撤销" in text for text in texts), texts
    undo = _undo_button(panel.result())
    assert undo is not None and undo.disabled is True
    panel.restore_last()
    assert len(calls) == 5
    assert page.app.toasts[-1] == "本次没有可清理的临时文件，无需撤销"


def test_cleanup_empty_does_not_inherit_undone_persisted_last(tmp_path, monkeypatch):
    """持久化记录里 restore 成功与 last 并存（旧版/异常状态）时也不得复活。"""
    root = str(tmp_path / "期A")
    (tmp_path / "期A").mkdir()
    page = make_page(tmp_path / "期A")
    batch = root + "/.toolbox-trash/期A-" + OP
    page.app.store.set("issue_state", {root: {"cleanup": {
        "schema": 1, "root": root, "operationId": "", "status": "empty",
        "count": 0, "entryCount": 0, "entries": [], "kept": [],
        "failures": [], "warnings": [], "error": "", "trashDir": "",
        "startedAt": "2026-11-07 12:00:00", "finishedAt": "2026-11-07 12:00:01",
        "lastOperationId": OP, "lastTrashDir": batch, "lastCount": 1,
        "lastFinishedAt": "2026-11-07 11:00:02",
        "restore": {"status": "success", "count": 1, "failures": [],
                    "error": "", "at": "2026-11-07 12:00:02"},
    }}})
    calls = _install(monkeypatch, page, [
        (_payload(root, dryRun=True, status="empty"), 0),
    ])
    panel = _panel(page)

    panel.start()

    rec = panel.record()
    assert (rec["status"], rec["count"]) == ("empty", 0)
    assert not rec.get("lastTrashDir") and not rec.get("lastOperationId")
    assert not rec.get("lastCount") and not rec.get("lastFinishedAt")
    texts = _texts(panel.result())
    assert not any("仍可撤销" in text for text in texts), texts
    assert any("没有可清理的临时文件（未创建批次，也没有撤销）。" in text
               for text in texts), texts
    undo = _undo_button(panel.result())
    assert undo is not None and undo.disabled is True

    panel.restore_last()
    assert len(calls) == 1
    assert page.app.toasts[-1] == "本次没有可清理的临时文件，无需撤销"

