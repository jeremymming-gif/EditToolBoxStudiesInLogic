"""P04 GUI：合刊「安全写出与完整反馈」——合并结果卡。

覆盖 CONTRACT.md §1 的 GUI 部分：
- R04：结果卡同时显示页数、缺失篇数/名单、warning 条数/摘要；缺失/warning 用
  warn 色；只有 rc=0 才说「合并完成」，rc=1 是「需人工核对」。
- R05：运行开始即清上一轮结果缓存；取消后显示「本次已取消/未完成」，不把旧
  PDF 当本次产物提供「打开文件」；运行中切步骤时结果仍归本次合并；重跑只显示
  本次产物。
- R06：旧产物不在原位/不可识别时明确提示，不静默显示上次成功。

不启动真 GUI/真引擎：沿用 test_b112_post_b 的 _FakeApp 桩，只桩掉
engine_bridge.run_call 的输出来驱动 pages/assembly 的真实运行管线。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft
import pytest

from services import engine_bridge as EB
from ui import assembly as ASM
from ui import pages as P

from test_b112_post_b import _build_issue, _walk


# ── 桩/工具 ─────────────────────────────────────────────────────────────
def _text_nodes(control) -> list[ft.Text]:
    return [ctl for ctl in _walk(control) if isinstance(ctl, ft.Text)]


def _texts(control) -> list[str]:
    return [str(ctl.value) for ctl in _text_nodes(control)]


def _buttons(control) -> list[str]:
    out: list[str] = []
    for ctl in _walk(control):
        value = getattr(ctl, "content", None)
        if isinstance(ctl, ft.TextButton) and isinstance(value, str):
            out.append(value)
    return out


def _install_app_plumbing(app) -> None:
    """给 _FakeApp 补 ASM._run_sequence 需要的最小运行管线。"""
    app.last_line = ""
    app.cancel_requested = False
    app.current_call = None
    app.notes_repo = None
    app.running = False

    def begin_run(call):
        app.running = True
        app.last_line = ""
        app.cancel_requested = False
        app.current_call = call

    def finish_run(call, rc):
        app.running = False

    def record_line(line):
        app.last_line = line

    app.begin_run = begin_run
    app.finish_run = finish_run
    app.record_line = record_line
    app.track_proc = lambda proc: None


def _install_engine(monkeypatch, app, runs: list[dict]) -> list[int]:
    """按顺序消费 runs；每条 = {lines, code, cancel}。返回消费计数。"""
    _install_app_plumbing(app)
    seen = {"n": 0}

    async def fake_run_call(call, *, on_line=None, on_proc=None, notes="", **kwargs):
        spec = runs[min(seen["n"], len(runs) - 1)]
        seen["n"] += 1
        for line in spec.get("lines") or []:
            app.last_line = line
            if on_line:
                on_line(line)
        if spec.get("cancel"):
            app.cancel_requested = True
        return int(spec.get("code") or 0)

    monkeypatch.setattr(EB, "run_call", fake_run_call)
    return seen


def _run(page, key: str = "merge", out: Path | None = None, folder: Path | None = None):
    call = EB.merge_call(folder=str(folder or ""), out=str(out or ""))
    asyncio.run(ASM._run_sequence(page, [key], {key: call}))


def _pdf(path: Path, payload: bytes = b"%PDF-1.4 test") -> Path:
    path.write_bytes(payload)
    return path


def _prepare(tmp_path: Path, *, with_merge_page: bool = True):
    page, _root = _build_issue()
    page.set_field("issue_dir", str(tmp_path))
    if with_merge_page:
        page.activate("merge", source="test")
    return page


# ── R05-1：运行开始即清上一次结果缓存 ────────────────────────────────────
def test_start_clears_previous_success_card(tmp_path: Path) -> None:
    old = _pdf(tmp_path / "合并_旧.pdf")
    page = _prepare(tmp_path)
    page.set_field("merge_out", str(old))
    # 上一轮成功留下的缓存（修前取消后会继续显示这张卡）。
    page._merge_info = {"path": str(old), "pages": 47}
    page._status["merge"] = ASM.StepStatus("done", exit_code=0, artifact=str(old))
    assert any("合并完成 · 47 页" in text for text in _texts(page._merge_result()))

    page.on_step_started("merge")

    texts = _texts(page._merge_result())
    assert page._merge_info == {}
    assert any("本次合并正在运行" in text for text in texts)
    assert not any("合并完成" in text for text in texts)
    assert not any("47 页" in text for text in texts)
    assert "打开文件" not in _buttons(page._merge_result())


def test_issue_change_clears_merge_run(tmp_path: Path) -> None:
    page = _prepare(tmp_path)
    page._merge_info = {"path": str(_pdf(tmp_path / "out.pdf")), "pages": 2}
    page._merge_run = {"state": "done", "code": 0, "artifact": str(tmp_path / "out.pdf")}
    page._reset_issue_runtime()
    assert page._merge_info == {}
    assert page._merge_run == {}
    texts = _texts(page._merge_result())
    assert any("还没有运行记录" in text for text in texts)
    assert not any("合并完成" in text for text in texts)


# ── R05-2：取消后不报成功、不提供旧产物打开 ──────────────────────────────
def test_cancel_shows_not_finished_and_never_offers_old_pdf(tmp_path, monkeypatch) -> None:
    old = _pdf(tmp_path / "合并_旧.pdf")
    page = _prepare(tmp_path)
    page.set_field("merge_out", str(old))
    page._merge_info = {"path": str(old), "pages": 47}
    page._status["merge"] = ASM.StepStatus("done", exit_code=0, artifact=str(old))
    _install_engine(monkeypatch, page.app, [{
        "lines": ["▶ 04/06 合并 PDF · merge · 12:00:00", "正在合并 ……"],
        "code": 0, "cancel": True,
    }])

    _run(page, out=tmp_path / "合并_新.pdf")

    texts = _texts(page._merge_result())
    assert page._state_of("merge") == "cancelled"
    assert any("本次已取消/未完成" in text for text in texts)
    assert any("原有目标未改动（上次结果在" in text for text in texts)
    assert not any("合并完成" in text for text in texts)
    # 结果卡与顶栏都不把旧 PDF 当本次产物提供打开。
    assert "打开文件" not in _buttons(page._merge_result())
    assert page._merge_info == {}
    assert page._result.title.value == "本次已取消/未完成：没有生成本次合并产物"
    assert page._result.open_btn.visible is False
    assert page._result.detail.value == ""


def test_cancel_banner_has_no_artifact_but_other_steps_keep_theirs(tmp_path, monkeypatch) -> None:
    page = _prepare(tmp_path)
    _install_engine(monkeypatch, page.app, [{
        "lines": ["正在合并 ……"], "code": 0, "cancel": True,
    }])
    assert page.sanitize_cancelled_artifact("merge", str(tmp_path / "x.pdf")) == ""
    assert page.sanitize_cancelled_artifact("toc", str(tmp_path / "x.json")) \
        == str(tmp_path / "x.json")


# ── R05-3：运行中切步骤，结果仍归本次合并 ────────────────────────────────
def test_cross_step_run_is_captured_into_merge_page(tmp_path, monkeypatch) -> None:
    out = _pdf(tmp_path / "合并_新.pdf")
    page = _prepare(tmp_path, with_merge_page=False)
    page.activate("compileall", source="test")  # 用户停在别的步骤
    _install_engine(monkeypatch, page.app, [{
        "lines": ["完成。合并文件：%s（共 3 页）" % out],
        "code": 0,
    }])

    _run(page, out=out)

    assert page._merge_info.get("path") == str(out)
    assert page._merge_info.get("pages") == 3
    page.activate("merge", source="test")
    texts = _texts(page._merge_result())
    assert any("合并完成 · 3 页" in text for text in texts)
    assert any(str(out) in text for text in texts)
    assert "打开文件" in _buttons(page._merge_result())


def test_other_step_lines_do_not_pollute_merge_card(tmp_path, monkeypatch) -> None:
    page = _prepare(tmp_path)  # 停在合并页
    _install_engine(monkeypatch, page.app, [{
        "lines": ["⚠ 注意事项：", "   · 第 3 篇页数为 0"],
        "code": 0,
    }])

    _run(page, key="compileall")

    assert "warnings" not in page._merge_info  # 编译步骤的告警不能进合并卡
    assert page._merge_warn_capture is False
    assert not any("warning" in text for text in _texts(page._merge_result()))
    # 但按步骤缓冲仍然工作：告警归到 compileall。
    assert page._step_tail.get("compileall")


# ── R05-4：重跑/成功只显示本次产物 ────────────────────────────────────────
def test_success_replaces_previous_result_with_only_current_product(tmp_path, monkeypatch) -> None:
    old = _pdf(tmp_path / "合并_旧.pdf")
    new = _pdf(tmp_path / "合并_新.pdf")
    page = _prepare(tmp_path)
    page.set_field("merge_out", str(new))
    page._merge_info = {"path": str(old), "pages": 47}
    page._status["merge"] = ASM.StepStatus("done", exit_code=0, artifact=str(old))
    _install_engine(monkeypatch, page.app, [{
        "lines": ["完成。合并文件：%s（共 3 页）" % new], "code": 0,
    }])

    _run(page, out=new)

    texts = _texts(page._merge_result())
    assert any("合并完成 · 3 页" in text for text in texts)
    assert any(str(new) in text for text in texts)
    assert not any(str(old) in text for text in texts)
    assert not any("47 页" in text for text in texts)
    assert "打开文件" in _buttons(page._merge_result())
    assert page._result.title.value == "完成"
    assert page._result.open_btn.visible is True
    assert page._result.detail.value == str(new)
    # 结果卡原地刷新过（不等整页重建）。
    assert page._merge_box is not None
    assert any("合并完成 · 3 页" in text for text in _texts(page._merge_box.content))


def test_cancel_then_rerun_success_shows_only_rerun_product(tmp_path, monkeypatch) -> None:
    first = tmp_path / "合并_第一次.pdf"
    second = _pdf(tmp_path / "合并_第二次.pdf")
    page = _prepare(tmp_path)
    _install_engine(monkeypatch, page.app, [
        {"lines": ["正在合并 ……"], "code": 0, "cancel": True},
        {"lines": ["完成。合并文件：%s（共 4 页）" % second], "code": 0},
    ])

    _run(page, out=first)
    assert any("本次已取消/未完成" in text
               for text in _texts(page._merge_result()))
    # 用户重新点「合并」= 新的运行入口；陈旧取消标记由页面清掉（R05-4），
    # 否则装配层开头的取消检查会再次把本次直接判成已取消。
    page._clear_stale_cancel()
    _run(page, out=second)

    texts = _texts(page._merge_result())
    assert any("合并完成 · 4 页" in text for text in texts)
    assert any(str(second) in text for text in texts)
    assert not any(str(first) in text for text in texts)
    assert not any("已取消" in text for text in texts)
    assert page._merge_run.get("state") == "done"


def test_stale_cancel_flag_cleared_only_when_idle(tmp_path: Path) -> None:
    page = _prepare(tmp_path, with_merge_page=False)
    page.app.running = False
    page.app.cancel_requested = True
    page._clear_stale_cancel()
    assert page.app.cancel_requested is False
    # 运行中的停止请求不能被清理掉（真中止路径）。
    page.app.running = True
    page.app.cancel_requested = True
    page._clear_stale_cancel()
    assert page.app.cancel_requested is True


# ── R04：缺失/warning 完整反馈 + warn 色 ─────────────────────────────────
def test_missing_and_warnings_are_shown_with_warn_color(tmp_path, monkeypatch) -> None:
    out = _pdf(tmp_path / "合并_新.pdf")
    page = _prepare(tmp_path)
    _install_engine(monkeypatch, page.app, [{
        "lines": [
            "   ⚠ 有 2 个预期文件没找到：01_a.pdf、02_b.pdf",
            "   ⚠ 注意事项：",
            "      · 第 3 篇页数为 0",
            "      · 封面缺页",
            "完成。合并文件：%s（共 5 页）" % out,
        ],
        "code": 0,
    }])

    _run(page, out=out)

    nodes = _text_nodes(page._merge_result())
    texts = [str(node.value) for node in nodes]
    warn = page.t.palette.warn
    muted = page.t.palette.text_muted
    assert any("合并完成 · 5 页" in text for text in texts)
    assert any("缺 2 篇：01_a.pdf、02_b.pdf" in text for text in texts)
    assert any("warning 2 条" in text for text in texts)
    assert any("· 第 3 篇页数为 0" in text for text in texts)
    for keyword in ("缺 2 篇", "warning 2 条", "· 第 3 篇页数为 0", "· 封面缺页"):
        node = next(node for node in nodes if keyword in str(node.value))
        assert node.color == warn, f"{keyword} 应使用 warn 色，实际 {node.color}"
    success = next(node for node in nodes if "合并完成 · 5 页" in str(node.value))
    assert success.color == muted


def test_rc1_says_manual_check_and_never_merge_complete(tmp_path, monkeypatch) -> None:
    out = _pdf(tmp_path / "合并_新.pdf")
    page = _prepare(tmp_path)
    _install_engine(monkeypatch, page.app, [{
        "lines": ["完成。合并文件：%s（共 5 页）" % out], "code": 1,
    }])

    _run(page, out=out)

    texts = _texts(page._merge_result())
    assert any("需人工核对 · 5 页" in text for text in texts)
    assert not any("合并完成" in text for text in texts)
    warn_node = next(node for node in _text_nodes(page._merge_result())
                     if "需人工核对" in str(node.value))
    assert warn_node.color == page.t.palette.warn
    assert page._state_of("merge") == "warn"
    assert "需人工核对" in EB.result_text("mergepdf.py", 1)
    assert EB.result_level("mergepdf.py", 1) == "warn"
    assert "需人工核对" in page._result.title.value


def test_rc2_failure_shows_failure_reason(tmp_path, monkeypatch) -> None:
    page = _prepare(tmp_path)
    _install_engine(monkeypatch, page.app, [{
        "lines": ["❌ 合并失败：输出文件被占用"], "code": 2,
    }])

    _run(page, out=tmp_path / "合并_新.pdf")

    texts = _texts(page._merge_result())
    assert any("本次合并失败" in text for text in texts)
    assert any("输出文件被占用" in text for text in texts)
    assert not any("合并完成" in text for text in texts)
    assert "打开文件" not in _buttons(page._merge_result())


# ── R06：旧产物丢失/不可识别 ──────────────────────────────────────────────
def test_missing_previous_product_is_flagged_not_silently_successful(tmp_path) -> None:
    gone = tmp_path / "合并_上次.pdf"  # 从未创建：上次记录还在但产物已不在
    page = _prepare(tmp_path)
    page._merge_info = {}
    page._status["merge"] = ASM.StepStatus("done", exit_code=0, artifact=str(gone))

    texts = _texts(page._merge_result())
    assert any("上次产物不在原位/无法识别" in text for text in texts)
    assert any(str(gone) in text for text in texts)
    assert not any("合并完成" in text for text in texts)
    assert "打开文件" not in _buttons(page._merge_result())


def test_previous_product_still_here_is_labeled_as_previous(tmp_path) -> None:
    kept = _pdf(tmp_path / "合并_上次.pdf")
    page = _prepare(tmp_path)
    page._merge_info = {}
    page._status["merge"] = ASM.StepStatus("done", exit_code=0, artifact=str(kept))

    texts = _texts(page._merge_result())
    assert any("上次合并结果（本次没有重新合并）" in text for text in texts)
    assert any(str(kept) in text for text in texts)
    assert not any("合并完成" in text for text in texts)
    assert "打开文件" in _buttons(page._merge_result())


def test_current_run_product_missing_is_flagged(tmp_path, monkeypatch) -> None:
    out = tmp_path / "合并_本次.pdf"
    page = _prepare(tmp_path)
    _install_engine(monkeypatch, page.app, [{
        "lines": ["完成。合并文件：%s（共 2 页）" % out], "code": 0,
    }])

    _run(page, out=out)
    # 引擎报成功但产物随后被移走/删掉：必须明示，不能只显示成功。
    out.unlink(missing_ok=True)

    texts = _texts(page._merge_result())
    assert any("产物不在原位/无法识别" in text for text in texts)
    assert "打开文件" not in _buttons(page._merge_result())


# ── R05-5：覆盖确认两个出口保持 ──────────────────────────────────────────
def test_merge_success_keeps_status_and_does_not_require_confirm(tmp_path, monkeypatch) -> None:
    out = _pdf(tmp_path / "合并_新.pdf")
    page = _prepare(tmp_path)
    page.set_field("merge_out", str(out))
    shown: list = []
    page.app.page.show_dialog = lambda dialog: shown.append(dialog)
    page._run_step = lambda key: shown.append(("run", key))  # type: ignore[method-assign]
    page._primary_action("merge")
    # 目标已存在 → 仍先走覆盖确认对话框（两出口由 test_b116 细则锁定）。
    assert shown and hasattr(shown[0], "actions")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
