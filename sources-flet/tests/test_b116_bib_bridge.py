"""B-116（批 115）：引用库桥新增/修正形参的契约测试（不启 GUI、不跑引擎、零写库）。

覆盖：
  · review 逐条裁决 `bibdb_conflicts_resolve_call`：keep/defer/adopt 必须带
    `--incoming-sha256`，缺指纹在拼命令前就 ValueError（防 P0 误伤）；
  · `bibdb_report_call`（report --out/--db）与 `bibdb_resolve_call` 新增
    `--backup` / `--allow-overlap`；
  · `bibdb_edit_call` 的 `--backup` / `--new-key` 透传（fieldfix 未知类型改 key）；
  · `bibdb_lint_call(with_records=True)` / `bibdb_conflicts_call(pending_only=True)`；
  · 入藏预览 20 条上限的说明文案；
  · `open_path`（存在/不存在/系统调用失败）与 `read_json_command` 的 stderr 分节。

纪律：monkeypatch 掉 `subprocess` / `Popen`，不启动 GUI、不真跑引擎；真实库零写入。
"""
from __future__ import annotations

import subprocess

import pytest

from services import engine_bridge as EB


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """桩 Engine 目录：让 base_cmd() 走 dev 模式（与 test_bib_write 同款）。"""
    root = tmp_path / "Engine"
    root.mkdir()
    for entry in EB.ENTRIES:
        (root / entry).write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("P2_ENGINE", str(root))
    for name in ("P2_ENGINE_EXE", "SLCN_ENGINE_EXE", "P2_ENGINE_PYTHON", "SLCN_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    return root


def args_of(call: EB.EngineCall) -> list[str]:
    """去掉解释器/入口前缀，只留传给 `bibdb.py` 的参数。"""
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


def test_import_preview_limit_and_note() -> None:
    """预览上限常量是 20；未超过时无提示，超过时说明「其余 N 条」。"""
    assert EB.BIB_IMPORT_PREVIEW_LIMIT == 20
    assert EB.import_preview_note(0) == ""
    assert EB.import_preview_note(20) == ""
    note = EB.import_preview_note(27)
    assert "20" in note and "7" in note and "未展开" in note
    assert EB.import_preview_note(3, limit=2).startswith("… 其余 1 条")
    assert EB.import_preview_note(-5) == ""


def test_report_call_argv(engine) -> None:
    """通用报告：report --json，--out/--db 只在非空时拼入。"""
    assert args_of(EB.bibdb_report_call()) == ["report", "--json"]
    args = args_of(EB.bibdb_report_call(db="/tmp/bib.jsonl", out="/tmp/report"))
    assert args[:2] == ["report", "--json"]
    assert args[args.index("--out") + 1] == "/tmp/report"
    assert args[args.index("--db") + 1] == "/tmp/bib.jsonl"


def test_conflicts_resolve_call_argv(engine) -> None:
    """review 逐条裁决：按 key + action + 指纹拼 argv，不带 --fields。"""
    args = args_of(EB.bibdb_conflicts_resolve_call(
        key="Alpha2020", action="keep", incoming_sha256="a" * 12,
        note="保留旧值", expect_db_sha256="b" * 64, backup="/tmp/bk",
        db="/tmp/bib.jsonl"))
    assert args[:2] == ["resolve", "--json"]
    assert args[args.index("--key") + 1] == "Alpha2020"
    assert args[args.index("--action") + 1] == "keep"
    assert args[args.index("--incoming-sha256") + 1] == "a" * 12
    assert args[args.index("--note") + 1] == "保留旧值"
    assert args[args.index("--expect-db-sha256") + 1] == "b" * 64
    assert args[args.index("--backup") + 1] == "/tmp/bk"
    assert args[args.index("--db") + 1] == "/tmp/bib.jsonl"
    assert "--dry-run" not in args and "--fields" not in args
    assert "--dry-run" in args_of(EB.bibdb_conflicts_resolve_call(
        key="K", action="defer", incoming_sha256="c" * 8, dry_run=True))


@pytest.mark.parametrize("action", ["keep", "defer", "adopt"])
def test_conflicts_resolve_requires_sha_before_building(engine, action: str) -> None:
    """缺指纹 / 缺 key / 未知 action 一律在拼命令前 ValueError（不产生可写 argv）。"""
    with pytest.raises(ValueError, match="incoming_sha256"):
        EB.bibdb_conflicts_resolve_call(key="K", action=action, incoming_sha256="")
    with pytest.raises(ValueError, match="必须给 key"):
        EB.bibdb_conflicts_resolve_call(key="", action=action, incoming_sha256="a" * 12)
    with pytest.raises(ValueError, match="keep/defer/adopt"):
        EB.bibdb_conflicts_resolve_call(key="K", action="replace",
                                        incoming_sha256="a" * 12)


def test_resolve_call_new_safety_flags(engine) -> None:
    """旧 resolve 入口补的 --backup / --allow-overlap 均透传（向后兼容）。"""
    args = args_of(EB.bibdb_resolve_call(
        key="K", action="adopt", allow_overlap=True, backup="/tmp/bk",
        expect_db_sha256="d" * 64))
    assert "--allow-overlap" in args
    assert args[args.index("--backup") + 1] == "/tmp/bk"
    assert "--allow-overlap" not in args_of(EB.bibdb_resolve_call(key="K"))


def test_edit_call_backup_and_new_key(engine) -> None:
    """edit 写库：--apply + --backup DIR + --new-key；dry-run 不带 --apply。"""
    args = args_of(EB.bibdb_edit_call(
        key="OldKey", fields={"title": "T"}, new_key="NewKey",
        dry_run=False, backup="/tmp/bk", expect_db_sha256="e" * 64))
    assert "--apply" in args and "--dry-run" not in args
    assert args[args.index("--new-key") + 1] == "NewKey"
    assert args[args.index("--backup") + 1] == "/tmp/bk"
    assert args[args.index("--expect-db-sha256") + 1] == "e" * 64
    preview = args_of(EB.bibdb_edit_call(key="OldKey", new_key="NewKey"))
    assert "--dry-run" in preview and "--apply" not in preview


def test_lint_and_conflicts_flags(engine) -> None:
    """健康页要原始记录，待裁决页只要 pending（确认既有形参没丢）。"""
    assert "--with-records" in args_of(EB.bibdb_lint_call(with_records=True))
    assert "--with-records" not in args_of(EB.bibdb_lint_call())
    assert "--pending-only" in args_of(EB.bibdb_conflicts_call(pending_only=True))
    assert "--pending-only" not in args_of(EB.bibdb_conflicts_call(pending_only=False))


def test_open_path_success(tmp_path, monkeypatch) -> None:
    """存在的路径：用系统 open 打开（macOS），Popen 不等待。"""
    target = tmp_path / "report.md"
    target.write_text("# report\n", encoding="utf-8")
    calls: list[tuple[list[str], dict]] = []

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            calls.append((list(argv), kwargs))

    monkeypatch.setattr(EB.sys, "platform", "darwin")
    monkeypatch.setattr(EB.subprocess, "Popen", _FakePopen)
    assert EB.open_path(str(target)) == (True, "")
    assert calls and calls[0][0] == ["open", str(target)]


def test_open_path_failures_do_not_raise(tmp_path, monkeypatch) -> None:
    """空/不存在/系统调用失败都只回 (False, 原因)，不抛异常。"""
    assert EB.open_path("")[0] is False
    missing = tmp_path / "nope.md"
    ok, error = EB.open_path(str(missing))
    assert ok is False and "不存在" in error

    target = tmp_path / "x.md"
    target.write_text("x", encoding="utf-8")

    class _BoomPopen:
        def __init__(self, *args, **kwargs):
            raise OSError("no opener")

    monkeypatch.setattr(EB.sys, "platform", "darwin")
    monkeypatch.setattr(EB.subprocess, "Popen", _BoomPopen)
    ok, error = EB.open_path(str(target))
    assert ok is False and "打开失败" in error


def test_read_json_command_keeps_stderr_separate(engine, monkeypatch) -> None:
    """stdout 的 JSON 正常解析；stderr 独立成键，同时仍并入 lines 日志。"""
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout='{"count": 1}\n',
            stderr="警告：仅预览前 20 条\n")

    monkeypatch.setattr(EB.subprocess, "run", fake_run)
    result = EB.read_json_command(EB.bibdb_report_call())
    assert result["ok"] is True and result["code"] == 0
    assert result["json"] == {"count": 1}
    assert result["stderr"] == "警告：仅预览前 20 条"
    assert any("仅预览前 20 条" in line for line in result["lines"])


def test_read_json_command_falls_back_to_stderr(engine, monkeypatch) -> None:
    """个别入口把 JSON 打到 stderr：stdout 无 payload 时回退 stderr。"""
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 2, stdout="progress…\n", stderr='{"error": "boom"}\n')

    monkeypatch.setattr(EB.subprocess, "run", fake_run)
    result = EB.read_json_command(EB.bibdb_report_call())
    assert result["json"] == {"error": "boom"}
    assert result["ok"] is False and result["code"] == 2
    assert result["stderr"] == '{"error": "boom"}'
