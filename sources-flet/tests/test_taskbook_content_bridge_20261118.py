"""R3 Task5：只读 taskbook 内容桥 / 交付桥的失败测试（TDD RED）。

针对 `services.engine_bridge` 的锁定契约写断言；当前产品源码是骨架
（ai_taskbook_call / ai_deliver_call 抛 NotImplementedError，
taskbook_result_fields 尚未出现，convert_call 三新参尚未合流），
本文件应当**导入成功但 FAIL**，不修改任何产品源码。

不 import Engine/** 或任何判据模块；subprocess / base_cmd 全部用假宿主。
"""
from __future__ import annotations

import json
import subprocess

import pytest

from services import engine_bridge as EB


# ── helpers ──────────────────────────────────────────────────────────────
def _completed(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["fake-engine"], returncode=code, stdout=stdout, stderr=stderr
    )


def _call() -> EB.EngineCall:
    return EB.EngineCall(entry="convert.py", argv=["fake-engine", "taskbook"], label="taskbook")


@pytest.fixture
def fake_head(monkeypatch):
    """假宿主：让 _call 不再找真实引擎安装，只拼固定 argv 头。"""
    head = ["/fake/engine", "convert.py"]
    monkeypatch.setattr(EB, "base_cmd", lambda entry: list(head))
    return head


def _tail(call: EB.EngineCall, head: list[str]) -> list[str]:
    assert call.argv[: len(head)] == head
    return call.argv[len(head) :]


# ── 日志诱饵：stdout 的 JSON 才是 payload ────────────────────────────────
def test_read_json_command_takes_stdout_payload_not_stderr_decoy(monkeypatch):
    real_text = "任务书正文第 1 行\n@article{zhang2026, title={真实任务书}}\n规则：逐条核实引用。\n"
    payload = {"ok": True, "task_id": "T-7", "text": real_text}
    stdout = "phase=taskbook\n" + json.dumps(payload, ensure_ascii=False) + "\ndone.\n"
    stderr = (
        json.dumps({"ok": True, "text": "诱饵"}, ensure_ascii=False)
        + "\n任务书：/tmp/诱饵.md\n"
    )
    monkeypatch.setattr(EB.subprocess, "run", lambda *a, **k: _completed(stdout, stderr, 0))

    result = EB.read_json_command(_call())

    assert result["json"]["text"] == real_text
    assert "诱饵" not in result["json"]["text"]
    fields = EB.taskbook_result_fields(result)
    assert fields["text"] == real_text
    assert "诱饵" not in fields["text"]


# ── 真实全文：完整复制、chars 一致、不从日志拼 ────────────────────────────
def test_taskbook_result_fields_copies_full_text_with_matching_chars(monkeypatch):
    text = (
        "标题：Studies in Logic 卷 12\n\n"
        "@article{zhang2026, author={张三}, title={任务书}, year={2026}}\n\n"
        "规则 1：引用必须逐条核实。\n规则 2：源正文永不回写。\n"
    )
    payload = {"ok": True, "task_id": "T-9", "text": text, "chars": len(text)}
    stdout = "loading\n" + json.dumps(payload, ensure_ascii=False) + "\n"
    stderr = "日志唯一标记 LINE_ONLY_SENTINEL 不属于任务书\n任务书：/tmp/诱饵.md\n"
    monkeypatch.setattr(EB.subprocess, "run", lambda *a, **k: _completed(stdout, stderr, 0))

    result = EB.read_json_command(_call())
    fields = EB.taskbook_result_fields(result)

    assert fields["text"] == text
    assert fields["chars"] == len(text)
    assert "LINE_ONLY_SENTINEL" not in fields["text"]
    assert "诱饵" not in fields["text"]
    # 日志里确实有这些诱饵，证明断言不是空打：内容只来自 JSON，不来自 lines/stderr。
    assert "LINE_ONLY_SENTINEL" in result["stderr"]
    assert "LINE_ONLY_SENTINEL" in " ".join(result["lines"])


# ── 字段齐全：七个键恰好映射 ─────────────────────────────────────────────
def test_taskbook_result_fields_maps_exactly_seven_keys():
    payload = {
        "ok": True,
        "task_id": "T-42",
        "template_version": "v3",
        "path": "/tmp/real/taskbook.md",
        "sha256": "abc123",
        "text": "正文",
        "chars": 2,
    }
    fields = EB.taskbook_result_fields({"ok": True, "json": payload})
    assert fields == {
        "ok": True,
        "task_id": "T-42",
        "template_version": "v3",
        "path": "/tmp/real/taskbook.md",
        "sha256": "abc123",
        "text": "正文",
        "chars": 2,
    }


def test_taskbook_result_fields_defaults_when_payload_missing():
    fields = EB.taskbook_result_fields({"ok": False, "json": None})
    assert fields == {
        "ok": False,
        "task_id": "",
        "template_version": "",
        "path": "",
        "sha256": "",
        "text": "",
        "chars": 0,
    }


def test_taskbook_result_fields_ignores_lines_and_stderr():
    result = {
        "ok": True,
        "json": {"text": "真正文", "chars": 3},
        "lines": ["任务书：/tmp/诱饵.md"],
        "stderr": '{"text": "诱饵"}',
    }
    fields = EB.taskbook_result_fields(result)
    assert fields["text"] == "真正文"
    assert fields["path"] == ""
    assert all("诱饵" not in str(value) for value in fields.values())


def test_taskbook_result_fields_chars_falls_back_to_text_length():
    text = "中文正文\n第二行"
    fields = EB.taskbook_result_fields({"ok": True, "json": {"text": text, "chars": "bad"}})
    assert fields["chars"] == len(text)


# ── 失败语义 ─────────────────────────────────────────────────────────────
def test_read_json_command_ok_false_on_nonzero_exit(monkeypatch):
    payload = {"ok": True, "text": "x"}
    monkeypatch.setattr(
        EB.subprocess, "run", lambda *a, **k: _completed(json.dumps(payload), "", 2)
    )
    result = EB.read_json_command(_call())
    assert result["ok"] is False
    assert result["code"] == 2
    assert result["json"] == payload
    # 现有实现：payload 存在时即便 rc!=0 也不写 error 键。
    assert "error" not in result


def test_read_json_command_ok_false_without_json_payload(monkeypatch):
    monkeypatch.setattr(
        EB.subprocess, "run", lambda *a, **k: _completed("no json here\n", "", 0)
    )
    result = EB.read_json_command(_call())
    assert result["ok"] is False
    assert result["json"] is None
    assert "error" not in result


def test_read_json_command_falls_back_to_stderr_json(monkeypatch):
    payload = {"ok": True, "text": "来自 stderr 的 payload"}
    monkeypatch.setattr(
        EB.subprocess,
        "run",
        lambda *a, **k: _completed("warning only\n", json.dumps(payload, ensure_ascii=False), 0),
    )
    result = EB.read_json_command(_call())
    assert result["json"] == payload
    # 现有实现回退：rc==0 且回退拿到的 payload 非空 → ok=True。
    assert result["ok"] is True
    assert "来自 stderr 的 payload" in result["stderr"]


def test_read_json_command_reports_error_key_verbatim(monkeypatch):
    err = "E: 引擎拒绝：任务书尚未生成"
    monkeypatch.setattr(EB.subprocess, "run", lambda *a, **k: _completed("", err, 1))
    result = EB.read_json_command(_call())
    assert result["ok"] is False
    assert result["code"] == 1
    assert result["error"] == err


# ── argv 构造：只读 taskbook ─────────────────────────────────────────────
def test_ai_taskbook_call_argv_without_task_id(fake_head):
    call = EB.ai_taskbook_call(work_dir="/tmp/w")
    assert _tail(call, fake_head) == [
        "--ai-workflow", "--work-dir", "/tmp/w", "taskbook", "--json"
    ]


def test_ai_taskbook_call_argv_with_task_id(fake_head):
    call = EB.ai_taskbook_call(work_dir="/tmp/w", task_id="T-77")
    assert _tail(call, fake_head) == [
        "--ai-workflow", "--work-dir", "/tmp/w", "taskbook", "--json",
        "--task-id", "T-77",
    ]


# ── argv 构造：交付桥 ─────────────────────────────────────────────────────
def test_ai_deliver_call_argv_without_optional_flags(fake_head):
    call = EB.ai_deliver_call(work_dir="/tmp/w")
    assert _tail(call, fake_head) == [
        "--ai-workflow", "--work-dir", "/tmp/w", "deliver", "--json"
    ]


def test_ai_deliver_call_argv_with_preview_id_and_use_original(fake_head):
    call = EB.ai_deliver_call(work_dir="/tmp/w", preview_id="P-9", use_original=True)
    assert _tail(call, fake_head) == [
        "--ai-workflow", "--work-dir", "/tmp/w", "deliver", "--json",
        "--preview-id", "P-9", "--use-original",
    ]


def test_ai_deliver_call_argv_with_use_original_only(fake_head):
    call = EB.ai_deliver_call(work_dir="/tmp/w", use_original=True)
    assert _tail(call, fake_head) == [
        "--ai-workflow", "--work-dir", "/tmp/w", "deliver", "--json",
        "--use-original",
    ]


# ── argv 构造：convert_call 的 R3 三新参 ──────────────────────────────────
def test_convert_call_r3_options_sit_after_dry_run_before_extra(fake_head):
    call = EB.convert_call(
        source="/tmp/a.tex",
        out="/tmp/b.pdf",
        dry_run=True,
        extra="--custom-flag",
        ai_taskbook_version=3,
        ai_execute="verify,typeset",
        ai_work_dir="/tmp/w",
    )
    tail = _tail(call, fake_head)
    assert "--dry-run" in tail
    i = tail.index("--dry-run")
    assert tail[i + 1 : i + 8] == [
        "--ai-taskbook-version", "3",
        "--ai-execute", "verify,typeset",
        "--ai-work-dir", "/tmp/w",
        "--custom-flag",
    ]


def test_convert_call_without_r3_options_keeps_argv_unchanged(fake_head):
    call = EB.convert_call(
        source="/tmp/a.tex", out="/tmp/b.pdf", clean=True, dry_run=True,
        extra="--custom-flag",
    )
    tail = _tail(call, fake_head)
    assert tail[:4] == ["--source", "/tmp/a.tex", "--out", "/tmp/b.pdf"]
    assert "--clean" in tail
    assert "--dry-run" in tail
    assert tail[-1] == "--custom-flag"
    for flag in ("--ai-taskbook-version", "--ai-execute", "--ai-work-dir"):
        assert flag not in tail
