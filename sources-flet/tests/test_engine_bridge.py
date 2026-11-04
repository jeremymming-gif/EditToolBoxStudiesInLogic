"""engine_bridge 单元测试：argv 语义 / 摘要判定 / 产出路径 / 环境准备。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import engine_bridge as EB


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """桩 Engine 目录：让 base_cmd() 走 dev 模式。"""
    root = tmp_path / "Engine"
    root.mkdir()
    for entry in EB.ENTRIES:
        (root / entry).write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("P2_ENGINE", str(root))
    for name in ("P2_ENGINE_EXE", "SLCN_ENGINE_EXE", "P2_ENGINE_PYTHON", "SLCN_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    return root


def args_of(call: EB.EngineCall) -> list[str]:
    """去掉命令头（解释器/-X/utf8/入口路径）。"""
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


def test_entries_complete():
    assert len(EB.ENTRIES) == 13
    for name in ("convert.py", "checkbib.py", "proofcheck.py", "infocheck.py",
                 "toc.py", "compileall.py", "mergepdf.py", "cleantemp.py", "dirpush.py"):
        assert name in EB.ENTRIES


def test_is_summary_line():
    assert EB.is_summary_line("✅ 完成。产物：D:\\out\\a.pdf")
    assert EB.is_summary_line("▶ 开始处理")
    assert EB.is_summary_line("[3/10] 编译 01")
    assert EB.is_summary_line("（2/5）生成目录")
    assert EB.is_summary_line("① 第一步")
    assert EB.is_summary_line("警告：字体缺失")
    assert not EB.is_summary_line("")
    assert not EB.is_summary_line("   ")
    assert not EB.is_summary_line("XeTeX 0.99998")


def test_result_text_and_level():
    assert EB.result_text("convert.py", 0) == "✅ 完成"
    assert "编译失败" in EB.result_text("compileall.py", 1)
    assert "命中" in EB.result_text("proofcheck.py", 1)
    assert "启动失败" in EB.result_text("convert.py", -1)
    assert "退出码 3" in EB.result_text("convert.py", 3)
    assert EB.result_level("convert.py", 0) == "ok"
    assert EB.result_level("proofcheck.py", 1) == "warn"
    assert EB.result_level("convert.py", 2) == "error"


def test_convert_call_argv(engine):
    call = EB.convert_call(source="D:\\s\\a.tex", out="D:\\o", compile_pdf=True, clean=True,
                           vol="2026", issue="1", year="2026", pages="1-10", received="2026-01-02")
    args = args_of(call)
    assert args[:4] == ["--source", "D:\\s\\a.tex", "--out", "D:\\o"]
    assert "--compile" in args and "--clean" in args
    for flag, val in (("--vol", "2026"), ("--issue", "1"), ("--year", "2026"),
                      ("--pages", "1-10"), ("--received", "2026-01-02")):
        assert args[args.index(flag) + 1] == val
    assert "--verify-refs" not in args


def test_convert_verify_branches(engine):
    plan = EB.convert_call(source="a.tex", out="o", verify_plan=True, verify_max="120")
    assert "--verify-plan" in args_of(plan) and "--verify-refs" not in args_of(plan)
    assert args_of(plan)[args_of(plan).index("--verify-max") + 1] == "120"
    refs = EB.convert_call(source="a.tex", out="o", verify_refs=True, no_bibdb=True)
    assert "--verify-refs" in args_of(refs) and "--no-bibdb" in args_of(refs)


def test_period_calls_argv(engine):
    # 批 109 Wave 0 / S1：壳层永不传写入型 --merge-into；--tex 只作只读输入。
    assert args_of(EB.checkbib_call(returned="r.bib", tex="t.tex", out="o")) == [
        "--returned", "r.bib", "--tex", "t.tex", "--out", "o"]
    assert "--merge-into" not in args_of(EB.checkbib_call(returned="r.bib", tex="t.tex"))
    assert args_of(EB.proofcheck_call(tex="t.tex", fix=True, lang="en")) == [
        "--tex", "t.tex", "--lang", "en", "--fix"]
    assert args_of(EB.infocheck_call(folder="f", preview=True)) == ["--folder", "f", "--preview"]
    assert "--place" not in args_of(EB.toc_call(report="r.txt", text_only=True))
    toc2 = args_of(EB.toc_call(report="r.txt", result_json="x.json"))
    assert "--place" in toc2 and "--compile" in toc2
    assert args_of(EB.compileall_call(folder="f", only="01,03", dry_run=True)) == [
        "--folder", "f", "--only", "01,03", "--dry-run"]
    merge = args_of(EB.merge_call(folder="f", nest_outlines=True, editors_page=True))
    assert set(merge) >= {"--folder", "--nest-outlines", "--editors-page"}
    # 批 109 Wave 0 / S2：清理固定 --trash（可恢复），不存在永久删除参数。
    assert args_of(EB.cleantemp_call(folder="f")) == ["--folder", "f", "--trash", "--json"]
    assert args_of(EB.cleantemp_call(folder="f", dry_run=True)) == [
        "--folder", "f", "--dry-run", "--trash", "--json"]
    annual = args_of(EB.dirpush_call(issue="i", annual=True, year="2026", include_issues="2,1"))
    assert "--annual" in annual and "--json" in annual
    assert annual[annual.index("--include-issues") + 1] == "1,2"
    assert "--with-en-toc" in args_of(EB.dirpush_call(issue="i", with_en_toc=True))


def test_artifact_path(tmp_path):
    target = tmp_path / "合并2026.pdf"
    target.write_bytes(b"%PDF-1.4")
    line = f"完成。合并文件：{target}（共 3 页）"
    assert EB.artifact_path(line) == str(target)
    # 信息检查契约行里的「有 tex 2 / 有 pdf 1」不能当成根路径产物
    info_line = (
        f"完成。检查 2 篇（有 tex 2 / 有 pdf 1；不一致 0 项）（报告：{target}）"
    )
    assert EB.artifact_path(info_line) == str(target)
    assert EB.artifact_path("没有路径的一行") == ""


def test_env_for_run(tmp_path, monkeypatch):
    monkeypatch.setenv("P2_ENGINE", str(tmp_path))
    env = EB.env_for_run(notes=str(tmp_path))
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert str(tmp_path / "vendor") in env["PYTHONPATH"]
    assert env["SLCN_NOTES"] == str(tmp_path)


def test_base_cmd_source_mode(engine):
    python = EB.find_engine_python()
    assert python
    cmd = EB.base_cmd("convert.py")
    assert cmd and cmd[0] == python and cmd[1:3] == ["-X", "utf8"]
    assert Path(cmd[3]) == engine / "convert.py"


def test_engine_status_fields(engine):
    data = EB.engine_status().to_dict()
    assert data["ok"] is True
    assert data["mode"] in ("dev", "frozen")
    assert Path(data["engine_dir"]) == engine


def test_engine_version_reads_init(tmp_path, monkeypatch):
    root = tmp_path / "Engine"
    (root / "slcn").mkdir(parents=True)
    (root / "convert.py").write_text("# stub\n", encoding="utf-8")
    (root / "slcn" / "__init__.py").write_text('__version__ = "9.9.9-test"\n', encoding="utf-8")
    monkeypatch.setenv("P2_ENGINE", str(root))
    assert EB.engine_version() == "9.9.9-test"


def test_engine_call_to_dict(engine):
    call = EB.checkbib_call(returned="r.bib")
    payload = call.to_dict()
    assert payload["entry"] == "checkbib.py"
    assert payload["label"]
    assert json.dumps(payload, ensure_ascii=False)
