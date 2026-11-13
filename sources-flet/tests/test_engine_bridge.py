"""engine_bridge 单元测试：argv 语义 / 摘要判定 / 产出路径 / 环境准备。"""
from __future__ import annotations

import asyncio
import json
import platform
import sys
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
    assert EB.result_text("convert.py", 0) == "完成"
    assert "编译失败" in EB.result_text("compileall.py", 1)
    assert "核对" in EB.result_text("proofcheck.py", 1)
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


def test_artifact_path_ignores_directories_and_missing_files(tmp_path):
    """P08 D1：产物按「行内完整收尾」判定，目录/半截前缀不算产物。

    回归样本：引擎报告一个不存在的文件时，逐字符截短会退到已存在的父目录
    （V 实测 `/tmp/`），GUI 就会渲染一个打不开的「打开」。
    """
    folder = tmp_path / "out"
    folder.mkdir()
    # 目录存在、里面的文件不存在（含空格/中文名，贴近引擎末行）：不能退到父目录。
    missing = folder / "p08 不存在 的产物.pdf"
    assert EB.artifact_path(f"完成。生成文件：{missing}（共 3 页）") == ""
    # V 实测复现行原样：/tmp 存在，但 /tmp/p08 … 不存在。
    assert EB.artifact_path("完成。生成文件：/tmp/p08 不存在 的产物.pdf（共 3 页）") == ""
    # 纯根 / 盘符根不是产物
    assert EB.artifact_path("/") == ""
    assert EB.artifact_path("完成。报告：/") == ""


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


# ── run_call 读取超长单行（大期 --json 实测 ~255KB > asyncio 默认 64KiB） ──

def _py_file_call(tmp_path, script: str) -> EB.EngineCall:
    """用当前解释器执行 tmp_path 下的脚本文件，避免 Windows argv 超长（206）。"""
    script_path = tmp_path / "stub_long_line.py"
    script_path.write_text(script, encoding="utf-8")
    argv = [sys.executable, str(script_path)]
    # 回归闸：大脚本必须走文件，argv 保持很短；不再出现 python -c <136KB>。
    assert sum(len(part) for part in argv) < 4096, "argv 过长：脚本应写入文件"
    assert "-c" not in argv
    return EB.EngineCall(entry="stub.py", argv=argv, label="stub")


def test_run_call_reads_single_line_over_64k(tmp_path):
    """单行 >64KiB 的 JSON 必须完整读完：rc0、on_line 只收一行且可解析。"""
    payload = {
        "status": "success",
        "operationId": "2026_11_07_120000-deadbeef",
        "entries": [{"path": f"01 作者/文章{i}.aux", "action": "move"}
                    for i in range(1200)],
        "blob": "x" * 70000,
    }
    script = (
        "import json\n"
        f"payload = {payload!r}\n"
        "print(json.dumps(payload, ensure_ascii=False))\n"
    )

    call = _py_file_call(tmp_path, script)
    assert call.argv[0] == sys.executable
    assert call.argv[1].endswith(".py")

    lines: list[str] = []
    code = asyncio.run(EB.run_call(call, on_line=lines.append))

    assert code == 0
    assert len(lines) == 1, f"长单行共收到 {len(lines)} 行"
    assert len(lines[0].encode("utf-8")) > 64 * 1024
    assert json.loads(lines[0]) == payload


def test_run_call_reads_multiline_with_oversized_line(tmp_path):
    """多行混合：普通行、超长 JSON 行、末尾无换行行都不能丢/不能串。"""
    payload = {"status": "success", "count": 3, "blob": "y" * 90000}
    script = (
        "import json, sys\n"
        "print('start')\n"
        f"print(json.dumps({payload!r}, ensure_ascii=False))\n"
        "print('done')\n"
        "sys.stdout.write('tail-no-newline')"
    )

    call = _py_file_call(tmp_path, script)
    assert call.argv[0] == sys.executable
    assert call.argv[1].endswith(".py")

    lines: list[str] = []
    code = asyncio.run(EB.run_call(call, on_line=lines.append))

    assert code == 0
    assert len(lines) == 4, lines[0][:80] if lines else "无输出"
    assert lines[0] == "start"
    assert len(lines[1].encode("utf-8")) > 64 * 1024
    assert json.loads(lines[1]) == payload
    assert lines[2] == "done"
    assert lines[3] == "tail-no-newline"



# 真实平台守卫：frozen .app 布局仅 macOS 可验；用真实 platform.system()，不受测试内 monkeypatch sys.platform 影响。
_DARWIN = platform.system() == "Darwin"

# ── P09 R04（PRE-012）：frozen 形态 sidecar 缺失/不可执行/执行失败 → ok=False ──

def _frozen_layout(tmp_path, monkeypatch, *, sidecar_body: str | None, executable: bool = True):
    """搭 frozen .app 目录形态（MacOS/<exe> + Resources/SLCNEngine/SLCNEngine），返回 sidecar 路径。

    monkeypatch sys.frozen/sys.executable/sys.platform 与 repo_root（去掉外部 Engine 源码候选），
    并清掉所有引擎覆盖 env —— 与打包运行期一致，只走包内 sidecar。
    """
    app = tmp_path / "编辑部工具箱.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "编辑部工具箱"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(EB, "repo_root", lambda: tmp_path)  # 无外部 Engine 源码 / 无仓库根 sidecar
    for name in ("P2_ENGINE", "SLCN_ENGINE", "P2_ENGINE_EXE", "SLCN_ENGINE_EXE",
                 "P2_ENGINE_PYTHON", "SLCN_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    EB.clear_engine_version_cache()
    if sidecar_body is None:
        return app, None
    sc = app / "Contents" / "Resources" / "SLCNEngine" / "SLCNEngine"
    sc.parent.mkdir(parents=True)
    sc.write_text(sidecar_body, encoding="utf-8")
    sc.chmod(0o755 if executable else 0o644)
    return app, sc


def test_engine_status_frozen_without_sidecar_not_ok(tmp_path, monkeypatch):
    """frozen 且包内没有 sidecar：ok=False、problems 非空（不得误报「就绪」）。"""
    _frozen_layout(tmp_path, monkeypatch, sidecar_body=None)
    status = EB.engine_status()
    assert status.frozen_exe == ""
    assert status.ok is False
    assert status.problems, "缺 sidecar 必须给出 problems"
    assert status.to_dict()["ok"] is False


@pytest.mark.skipif(not _DARWIN, reason="frozen .app 布局仅 macOS：Windows 无 exec 位且无法执行 sh sidecar")
def test_engine_status_frozen_sidecar_not_executable(tmp_path, monkeypatch):
    """frozen + sidecar 存在但不可执行：ok=False、problems 含「不可执行」。"""
    _app, sc = _frozen_layout(tmp_path, monkeypatch, sidecar_body="#!/bin/sh\nexit 0\n", executable=False)
    status = EB.engine_status()
    assert status.frozen_exe == str(sc)
    assert status.executable is False
    assert status.ok is False
    assert any("不可执行" in p for p in status.problems), status.problems


@pytest.mark.skipif(not _DARWIN, reason="frozen .app 布局仅 macOS：Windows 无 exec 位且无法执行 sh sidecar")
def test_engine_status_frozen_sidecar_version_fails(tmp_path, monkeypatch):
    """frozen + 可执行但 --version 失败（exit 3）：ok=False、problems 含 rc=3。"""
    _app, sc = _frozen_layout(tmp_path, monkeypatch, sidecar_body="#!/bin/sh\necho boom >&2\nexit 3\n")
    status = EB.engine_status()
    assert status.frozen_exe == str(sc)
    assert status.executable is True
    assert status.ok is False
    assert any("rc=3" in p for p in status.problems), status.problems


# ── MC-03：缺失产物不得误认父目录（结构性收尾） ─────────────────────────────
# expected 一律来自业务要求：「打开产物」必须对应契约行声明的实际产物；
# 声明的文件/目录不存在时返回空串，不得回退到任何已存在的祖先前缀。
# 夹具均为 tmp_path 下合成（macOS）；Windows 盘符/UNC 只锁既有契约形态，
# 不是 Windows 文件系统实测。


def test_artifact_path_missing_hidden_segment_is_not_parent(tmp_path):
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    # 声明的文件不存在：不得返回已存在的 .hidden/ 或更上层祖先目录。
    assert EB.artifact_path(f"完成。生成文件：{hidden}/missing.pdf（共 3 页）") == ""
    assert EB.artifact_path(f"完成。生成文件：{hidden}/missing.pdf") == ""
    deeper = hidden / "sub"
    deeper.mkdir()
    assert EB.artifact_path(f"完成。生成文件：{deeper}/缺失.pdf（共 3 页）") == ""


def test_artifact_path_real_file_inside_hidden_segment(tmp_path):
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    real = hidden / "报告.pdf"
    real.write_bytes(b"%PDF-1.4")
    assert EB.artifact_path(f"完成。合并文件：{real}（共 3 页）") == str(real)
    assert EB.artifact_path(f"完成。产物文件：{real}") == str(real)


def test_artifact_path_spaces_chinese_dots(tmp_path):
    folder = tmp_path / "含 空格 目录.v2"
    folder.mkdir()
    real = folder / "合并 2026.终稿.pdf"
    real.write_bytes(b"%PDF-1.4")
    assert EB.artifact_path(f"完成。合并文件：{real}（共 3 页）") == str(real)
    # 目录存在（名含空格与点）、其中的文件缺失：不得退到该目录。
    assert EB.artifact_path(f"完成。合并文件：{folder}/缺失 文件.pdf（共 3 页）") == ""


def test_artifact_path_issue_dir_with_ascii_parens(tmp_path):
    issue = tmp_path / "2026年第1期(总第100期)"
    issue.mkdir()
    # 显式交付目录（compileall 契约行）：半角括号是路径本身的一部分。
    line = f"完成。编译 3 篇（成功 2 / 失败 1）（目录：{issue}）"
    assert EB.artifact_path(line) == str(issue)
    real = issue / "合并.pdf"
    real.write_bytes(b"%PDF-1.4")
    assert EB.artifact_path(f"完成。合并文件：{real}（共 3 页）") == str(real)
    # 文件缺失：不得把含半角括号的期号目录误认成产物。
    assert EB.artifact_path(f"完成。生成文件：{issue}/缺失.pdf（共 3 页）") == ""


def test_artifact_path_explicit_delivery_directory(tmp_path):
    out = tmp_path / "out" / "作者名"
    out.mkdir(parents=True)
    # convert.py 末行契约：目录即产物，行尾完整收尾。
    assert EB.artifact_path(f"完成。产物目录：{out}") == str(out)
    assert EB.artifact_path(f"完成。产物目录：{out}。") == str(out)
    spaced = tmp_path / "交付 目录.v1"
    spaced.mkdir()
    assert EB.artifact_path(f"完成。产物目录：{spaced}") == str(spaced)
    # 声明的目录不存在：不得退到已存在的 tmp_path。
    assert EB.artifact_path(f"完成。产物目录：{tmp_path}/不存在目录") == ""


def test_artifact_path_report_wraps_and_status_tails(tmp_path):
    report = tmp_path / "r.txt"
    report.write_text("stub", encoding="utf-8")
    info = f"完成。检查 2 篇（有 tex 2 / 有 pdf 1；不一致 0 项）（报告：{report}）"
    assert EB.artifact_path(info) == str(report)
    proof = f"完成。一校核查：命中 3 处（可自动改 1 处）（报告：{report}）"
    assert EB.artifact_path(proof) == str(report)
    assert EB.artifact_path(f"报告：{report}") == str(report)
    assert EB.artifact_path(f"完成。报告：{report}。") == str(report)
    missing = f"完成。检查 2 篇（有 tex 2 / 有 pdf 1；不一致 0 项）（报告：{tmp_path}/缺失.txt）"
    assert EB.artifact_path(missing) == ""


def test_artifact_path_trailing_quotes_and_punctuation(tmp_path):
    real = tmp_path / "a.pdf"
    real.write_bytes(b"%PDF-1.4")
    assert EB.artifact_path(f'完成。报告："{real}"') == str(real)
    assert EB.artifact_path(f"完成。报告：{real}）") == str(real)


def test_artifact_path_windows_drive_and_unc_contract_on_macos():
    # 只锁 macOS 下的既有契约形态：盘符路径在本机不可能存在 → 空串；
    # 盘符根永不是产物；UNC（纯反斜杠）不匹配路径起点正则（既有契约）。
    # 这不是 Windows 文件系统实测，Windows 行为需 Windows 侧独立验证。
    assert EB.artifact_path("完成。合并文件：D:\\out\\a.pdf（共 3 页）") == ""
    assert EB.artifact_path("完成。产物目录：D:\\") == ""
    assert EB.artifact_path("完成。报告：D:\\。") == ""
    assert EB.artifact_path("完成。合并文件：\\\\server\\share\\a.pdf（共 3 页）") == ""


# ── MC-03 返工：多路径起点不得遮蔽声明产物 / 中文标点不回退前缀 ─────────────
# expected 来自验收卡第 7 项与业务契约：一行有多个路径起点时，日志中其他现存
# 目录不得当成声明产物；中文标点是路径合法字符，声明文件缺失时不得回退到
# 标点前已存在的前缀。夹具均为 tmp_path 下合成（macOS）。


def test_artifact_path_logdir_does_not_shadow_missing_report(tmp_path):
    # [MC-03 返工 / ACCEPT H2] 日志目录存在、声明报告缺失：返回空串，
    # 不得把靠前的现存日志目录当成产物。
    logdir = tmp_path / "运行 日志B"
    logdir.mkdir()
    report = tmp_path / "卷 三" / ".cache 归档" / "缺失 报告.txt"
    report.parent.mkdir(parents=True)
    line = (f"完成。检查 12 篇（成功 11 / 失败 1；状态 0）"
            f"（日志目录：{logdir}）（报告：{report}）")
    assert EB.artifact_path(line) == ""


def test_artifact_path_logdir_does_not_shadow_real_report(tmp_path):
    # [MC-03 返工 / ACCEPT H3] 日志目录与声明报告都真实存在：必须返回声明
    # 报告，不得被靠前的日志目录遮蔽；日志目录在后也不得顶替。
    logdir = tmp_path / "运行 日志B"
    logdir.mkdir()
    report = tmp_path / "卷 三" / ".cache 归档" / "合并 第6,7卷 v3.1(终).pdf"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"%PDF-1.4")
    line = (f"完成。检查 12 篇（状态 0）"
            f"（日志目录：{logdir}）（报告：{report}）")
    assert EB.artifact_path(line) == str(report)
    line2 = (f"完成。检查 12 篇（状态 0）"
             f"（报告：{report}）（日志目录：{logdir}）")
    assert EB.artifact_path(line2) == str(report)


def test_artifact_path_fullwidth_comma_in_name(tmp_path):
    # [MC-03 返工 / ACCEPT J/J2] 中文逗号是路径合法字符：真实文件完整返回；
    # 声明文件缺失、逗号前的同名目录存在时不得退到该目录。
    folder = tmp_path / "第 4 册.v1"
    folder.mkdir()
    real = folder / "报告，终稿.pdf"
    real.write_bytes(b"%PDF-1.4")
    assert EB.artifact_path(f"完成。报告：{real}（共 3 页）") == str(real)
    trap = folder / "校样"
    trap.mkdir()
    assert EB.artifact_path(f"完成。报告：{trap}，终稿.missing.pdf（共 3 页）") == ""


def test_artifact_path_directory_label_no_prefix_fallback(tmp_path):
    # [MC-03 返工] 目录契约 + 中文标点：声明目录缺失时不得退到标点前已存在前缀。
    base = tmp_path / "交付"
    base.mkdir()
    assert EB.artifact_path(f"完成。产物目录：{base}，终稿目录") == ""


def test_artifact_path_checkbib_report_label(tmp_path):
    # [MC-03 返工] checkbib 末行契约：前导空白 + 长标签「合并后回读校验报告：」。
    report = tmp_path / "回读 校验.txt"
    report.write_text("stub", encoding="utf-8")
    assert EB.artifact_path(f"   合并后回读校验报告：{report}") == str(report)
    missing = tmp_path / "缺失 校验.txt"
    assert EB.artifact_path(f"   合并后回读校验报告：{missing}") == ""


def test_artifact_path_unlabeled_toc_directory(tmp_path):
    # [MC-03 返工] toc 末行无标签：`完成。生成目录 N 份（{dir}）`，退回起点
    # 扫描；目录即产物。声明目录缺失时不得退到已存在的父目录。
    out = tmp_path / "目 录输出"
    out.mkdir()
    assert EB.artifact_path(f"完成。生成目录 2 份（{out}）") == str(out)
    assert EB.artifact_path(f"完成。生成目录 2 份（{tmp_path}/不存在 目录）") == ""


# ── MC-03 返工第 3 轮：wb02 边界形态 B1–B4 ─────────────────────────────────
# expected 来自 wb02 §6 三方对照中 base 的正确行为与业务契约：声明产物必须
# 完整返回；缺失即空串；其他现存目录/标签不得顶替。夹具均为 tmp_path 合成。


def test_artifact_path_ascii_space_page_tail(tmp_path):
    # [MC-03 返工3 / wb02 B1] ASCII 空格页数尾注（无全角括号）：真实文件必须
    # 返回；文件缺失时空串，不得退到父目录。
    real = tmp_path / "汇总 第1,2卷.pdf"
    real.write_bytes(b"%PDF-1.4")
    assert EB.artifact_path(f"完成。报告：{real} 共 3 页") == str(real)
    missing = tmp_path / "缺失 汇总.pdf"
    assert EB.artifact_path(f"完成。报告：{missing} 共 3 页") == ""


def test_artifact_path_fullwidth_parens_in_name(tmp_path):
    # [MC-03 返工3 / wb02 B2] 路径名含全角括号：真实文件完整返回（最长优先），
    # 带页数尾注时同样完整返回；文件缺失时不得退到括号前不存在的前缀。
    folder = tmp_path / "全角（括号）目录"
    folder.mkdir()
    real = folder / "a 报告.pdf"
    real.write_bytes(b"%PDF-1.4")
    assert EB.artifact_path(f"完成。报告：{real}") == str(real)
    assert EB.artifact_path(f"完成。报告：{real}（共 3 页）") == str(real)
    missing = folder / "缺失（终）.pdf"
    assert EB.artifact_path(f"完成。报告：{missing}") == ""


def test_artifact_path_two_labels_report_wins(tmp_path):
    # [MC-03 返工3 / wb02 B3] 一行两个标签：报告/文件类优先于目录类，与先后
    # 顺序无关；声明报告缺失即空串，不得退到目录标签的现存目录。
    report = tmp_path / "报告 终.pdf"
    report.write_bytes(b"%PDF-1.4")
    logdir = tmp_path / "日志 目录"
    logdir.mkdir()
    assert EB.artifact_path(f"完成。报告：{report}（目录：{logdir}）") == str(report)
    assert EB.artifact_path(f"完成。检查 2 篇（目录：{logdir}）（报告：{report}）") == str(report)
    missing = tmp_path / "报告 缺失.pdf"
    assert EB.artifact_path(f"完成。报告：{missing}（目录：{logdir}）") == ""


def test_artifact_path_unlabeled_two_paths_last_wins(tmp_path):
    # [MC-03 返工3 / wb02 B4] 无标签行两个现存路径：toc 契约目录在行尾括号内，
    # 取最后一组路径；末位目录缺失即空串，不得回退靠前的现存日志目录。
    logdir = tmp_path / "日志 目录"
    logdir.mkdir()
    out = tmp_path / "输出 目录"
    out.mkdir()
    line = f"完成。生成目录 2 份（日志目录：{logdir}）（{out}）"
    assert EB.artifact_path(line) == str(out)
    missing = tmp_path / "输出 缺失"
    line2 = f"完成。生成目录 2 份（日志目录：{logdir}）（{missing}）"
    assert EB.artifact_path(line2) == ""


def test_artifact_path_page_tail_rule_does_not_reopen_prefix_traps(tmp_path):
    # [MC-03 返工3 守卫] 页数尾注规则不得重开空格/半角括号前缀陷阱：声明文件
    # 缺失、截短前缀（空格/半角括号处）真实存在时仍返回空串。
    folder = tmp_path / "丙 辑"
    folder.mkdir()
    (folder / "合并").mkdir()
    (folder / "合并 第3,4卷 v2.0(终)").mkdir()
    missing = folder / "合并 第3,4卷 v2.0(终).pdf"
    assert EB.artifact_path(f"完成。合并文件：{missing}（共 3 页）") == ""
    assert EB.artifact_path(f"完成。合并文件：{missing} 共 3 页") == ""
