#!/usr/bin/env python3
"""B-285a S1 契约测试：`diagnose.v1` 的 17 类、schema、unknown 回退、只读性。

判据（B-285 spec C1/C2）：17 类各至少 1 条**真实失败写法**日志夹具；每条 reason/
next_steps≥2/doc_anchor；无法归类回 `unknown` 且 confidence<0.5；夹具不得输出「成功」。
CLI 必须是**只读**的：不改日志、不写产物；同输入（含注入 generated_at）逐字节确定。

本文件是独立脚本（与 `Engine/tests/` 其余套件同一风格），失败退出码非 0。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import diagnose as DG  # noqa: E402

PY = sys.executable
FIXED_TIME = "2026-10-30T00:00:00+0800"

#: diagnose.v1 契约键（spec 清单；顺序即 JSON 键序）
EXPECTED_KEYS = [
    "schema", "entry", "exit_code", "category", "severity", "title", "reason",
    "evidence", "next_steps", "doc_anchor", "confidence", "generated_at", "engine_version",
]

_passed = 0
_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}  {detail}")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(ENGINE / "diagnose.py"), *args], cwd=str(ENGINE),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────────────────
# 17 类夹具：每一行都是引擎/TeX/操作系统真实会打出来的失败写法，
# 分类只靠关键字与退出码规则，不靠夹具名（R1：分类过拟合的反例测试）。
CASES: list[tuple[str, str, int, str]] = [
    ("usage", "convert.py", 2,
     "usage: convert.py --source SRC [--out OUT]\n"
     "convert.py: error: the following arguments are required: --source"),
    ("source_missing", "convert.py", 2,
     "❌ 找不到源文件：/tmp/稿件/01 张三/正文.tex\n（文件可能被移走或改名）"),
    ("source_type", "convert.py", 2,
     "❌ 不支持的文件类型：/tmp/稿件/正文.pdf（只支持 .tex / .docx）"),
    ("source_empty", "convert.py", 2,
     "❌ 源文件是空的：/tmp/稿件/正文.tex（0 字节）"),
    ("metadata_invalid", "convert.py", 2,
     "❌ 稿件基本信息读取失败：JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n"
     "（信息表可能少了一个引号）"),
    ("template_unavailable", "convert.py", 2,
     "❌ 找不到模板：SLCN-MIX.sty\n模板不可用：请重装工具或检查模板文件夹是否被杀毒软件隔离"),
    ("output_conflict", "convert.py", 2,
     "❌ 输出目录已存在且不是本工具的产物，拒绝覆盖：/tmp/out/2026年第5期"),
    ("compile_tool_missing", "compileall.py", 1,
     "❌ 找不到命令 xelatex，无法编译：/tmp/01 张三/正文.tex\n"
     "请确认已安装 TeX Live（2024 以上）"),
    ("compile_tex_error", "compileall.py", 1,
     "! Undefined control sequence.\n"
     "l.12 \\unknownmacro\n"
     "xelatex 第 1 步失败：/tmp/01 张三/正文.tex"),
    ("compile_bib_error", "convert.py", 2,
     "❌ biber 处理参考文献失败：I couldn't open database file /tmp/ref.bib"),
    ("compile_no_pdf", "compileall.py", 1,
     "❌ 编译结束但未生成 PDF：/tmp/01 张三/正文.pdf"),
    ("report_read_error", "proofcheck.py", 2,
     "❌ 报告文件解析失败：报告格式不对\nJSONDecodeError: Expecting ',' delimiter"),
    ("permission_denied", "convert.py", 2,
     "PermissionError: [Errno 13] Permission denied: '/tmp/out/正文.tex'"),
    ("file_locked", "mergepdf.py", 1,
     "OSError: [WinError 32] 另一个程序正在使用此文件，进程无法访问：'/tmp/合并.pdf'"),
    ("disk_full", "convert.py", 2,
     "OSError: [Errno 28] No space left on device"),
    ("cancelled", "compileall.py", -15,
     "进程被用户终止（SIGTERM）"),
    ("unknown", "convert.py", 3,
     "RuntimeError: 内部错误\n（日志里没有已知的失败写法）"),
]


def test_categories() -> None:
    print("\n── (a) 17 类夹具：分类、置信度、证据、下一步 ──")
    for category, entry, code, log_text in CASES:
        rep = DG.build(entry=entry, exit_code=code, log_text=log_text,
                       generated_at=FIXED_TIME, engine_version="1.4.1")
        check(f"(a) {category}：category 命中", rep["category"] == category,
              f"{rep['category']}（confidence={rep['confidence']}）")
        check(f"(a) {category}：confidence∈[0,1]",
              isinstance(rep["confidence"], float) and 0.0 <= rep["confidence"] <= 1.0,
              str(rep["confidence"]))
        if category != "unknown":
            check(f"(a) {category}：证据 matched_lines 非空",
                  bool(rep["evidence"]["matched_lines"]), str(rep["evidence"]))
        check(f"(a) {category}：next_steps≥2", len(rep["next_steps"]) >= 2,
              str(rep["next_steps"]))
        check(f"(a) {category}：reason/doc_anchor 非空",
              bool(rep["reason"].strip()) and bool(rep["doc_anchor"].strip()))
        check(f"(a) {category}：severity 在枚举内",
              rep["severity"] in ("error", "warning", "info"), rep["severity"])
        check(f"(a) {category}：log_tail≤10 行",
              len(rep["evidence"]["log_tail"]) <= DG.LOG_TAIL_LINES,
              str(len(rep["evidence"]["log_tail"])))
        # C2：夹具不得输出「成功」字样（诊断是给失败用的，不许打鸡血）
        check(f"(a) {category}：输出不含「成功」", "成功" not in DG.render_json(rep))


def test_schema_and_determinism() -> None:
    print("\n── (b) schema：13 个契约键、枚举、同输入逐字节确定 ──")
    check("(b) SCHEMA 版本号", DG.SCHEMA == "diagnose.v1", DG.SCHEMA)
    check("(b) CATEGORIES 恰为 17 类且 unknown 兜底在最后",
          len(DG.CATEGORIES) == 17 and DG.CATEGORIES[-1] == "unknown"
          and set(DG.CATEGORIES) == {c for c, *_ in CASES}, str(DG.CATEGORIES))
    log_text = CASES[8][3]  # compile_tex_error
    rep1 = DG.build(entry="compileall.py", exit_code=1, log_text=log_text,
                    generated_at=FIXED_TIME, engine_version="1.4.1")
    rep2 = DG.build(entry="compileall.py", exit_code=1, log_text=log_text,
                    generated_at=FIXED_TIME, engine_version="1.4.1")
    check("(b) 键序/键集合与契约一致", list(rep1.keys()) == EXPECTED_KEYS,
          str(list(rep1.keys())))
    check("(b) evidence 三个子字段",
          set(rep1["evidence"]) == {"log_tail", "matched_lines", "artifacts"})
    check("(b) 同输入注入同 generated_at → 逐字节相同",
          DG.render_json(rep1) == DG.render_json(rep2))
    rep3 = DG.build(entry="compileall.py", exit_code=1, log_text=log_text,
                    generated_at="2027-01-01T00:00:00+0800", engine_version="1.4.1")
    check("(b) generated_at 可注入（只影响该字段）",
          rep3["generated_at"] != rep1["generated_at"]
          and DG.render_json(rep3).replace(rep3["generated_at"], "X")
          == DG.render_json(rep1).replace(rep1["generated_at"], "X"))
    check("(b) 默认引擎版本来自 slcn（非空）",
          bool(rep1["engine_version"].strip()), rep1["engine_version"])


def test_unknown_fallback() -> None:
    print("\n── (c) unknown 兜底：不硬猜 ──")
    rep = DG.build(entry="convert.py", exit_code=3, log_text="完全无关的日志行\n还是无关",
                   generated_at=FIXED_TIME, engine_version="1.4.1")
    check("(c) 无法归类 → unknown", rep["category"] == "unknown", rep["category"])
    check("(c) unknown 置信度 < 0.5", rep["confidence"] < 0.5, str(rep["confidence"]))
    check("(c) unknown 不打鸡血（无「成功」）", "成功" not in DG.render_json(rep))
    rep0 = DG.build(entry="convert.py", exit_code=0, log_text="一切正常",
                    generated_at=FIXED_TIME, engine_version="1.4.1")
    check("(c) 退出码 0 → unknown + 低置信（不硬猜原因）",
          rep0["category"] == "unknown" and rep0["confidence"] < 0.5, str(rep0))
    check("(c) 退出码 0 的 reason 说明「没有失败」",
          "没有失败" in rep0["reason"], rep0["reason"])
    for code in (-2, -15, 130, 143):
        cat, conf, _m = DG.classify("compileall.py", code, "任意日志")
        check(f"(c) 退出码 {code}（被信号杀掉）→ cancelled", cat == "cancelled",
              f"{cat}/{conf}")


def test_artifacts_and_tail() -> None:
    print("\n── (d) evidence：artifacts 提取 ≤10、日志只读尾部 ──")
    rep = DG.build(entry="convert.py", exit_code=2,
                   log_text="❌ 找不到源文件：/tmp/稿件/01 张三/正文.tex",
                   generated_at=FIXED_TIME, engine_version="1.4.1")
    arts = rep["evidence"]["artifacts"]
    check("(d) 从日志里提取产物路径", bool(arts) and arts[0].endswith(".tex"), str(arts))
    many = "\n".join(f"❌ 找不到源文件：/tmp/稿件/第{i:02d}篇.tex" for i in range(30))
    rep30 = DG.build(entry="convert.py", exit_code=2, log_text=many,
                     generated_at=FIXED_TIME, engine_version="1.4.1")
    check("(d) artifacts ≤10", len(rep30["evidence"]["artifacts"]) <= DG.ARTIFACTS_LIMIT,
          str(len(rep30["evidence"]["artifacts"])))

    tmp = tempfile.mkdtemp(prefix="slcn_diag_")
    try:
        tail_log = os.path.join(tmp, "tail.log")
        filler = "普通输出行，没有关键字\n" * 1200          # >16KB：错误行只可能从尾部看到
        with open(tail_log, "w", encoding="utf-8") as fh:
            fh.write(filler + "! LaTeX Error: File `foo.sty' not found.\n")
        r = run("--entry", "compileall.py", "--exit-code", "1", "--log", tail_log,
                "--json", "--generated-at", FIXED_TIME)
        rep = json.loads(r.stdout)
        check("(d) 日志只读尾部：尾部的错误照样认得出",
              rep["category"] == "compile_tex_error", rep["category"])
        check("(d) 读了日志尾部（log_tail 非空且≤10 行）",
              0 < len(rep["evidence"]["log_tail"]) <= 10,
              str(len(rep["evidence"]["log_tail"])))
        check("(d) 尾部分类照样只给 ≤10 条相中行",
              len(rep["evidence"]["matched_lines"]) <= 10)

        head_log = os.path.join(tmp, "head.log")
        with open(head_log, "w", encoding="utf-8") as fh:
            fh.write("! LaTeX Error: 开头这一行在 16KB 之外，读不到\n" + filler)
        r = run("--entry", "compileall.py", "--exit-code", "1", "--log", head_log, "--json")
        rep = json.loads(r.stdout)
        check("(d) 只读尾部：16KB 之外的错误行不参与分类（不偷偷整份读）",
              rep["category"] == "unknown", rep["category"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_redaction() -> None:
    _FAKE_KEY = "sk" + "-abcdef1234567890"
    print("\n── (f) 脱敏：密钥 / API key / token / Bearer 不出现在任何输出 ──")
    secret_log = (
        f"❌ 找不到源文件：/tmp/稿件/正文.tex（{'api' + '_key'}=SECRET_VALUE_123）\n"
        f"调用接口时带了 {_FAKE_KEY} 和 token=SECRET_TOKEN_456\n"
        "SLCN_LLM_KEY=SECRET_LLM_789 Authorization: Bearer eyJhbGciOiJIUzI1NiJ9\n"
        "tokenization 这个词是方法名，不该被掩码"
    )
    rep = DG.build(entry="convert.py", exit_code=2, log_text=secret_log,
                   generated_at=FIXED_TIME, engine_version="1.4.1")
    blob = DG.render_json(rep) + DG.render_text(rep)
    leaked = [s for s in ("SECRET_VALUE_123", _FAKE_KEY,
                          "SECRET_TOKEN_456", "SECRET_LLM_789", "eyJhbGciOiJIUzI1NiJ9")
              if s in blob]
    check("(f) 报告（JSON+人类摘要）不含密钥原文", not leaked, str(leaked))
    check("(f) 掩码后仍能正确分类", rep["category"] == "source_missing", rep["category"])
    check("(f) log_tail 已脱敏（含 ***、不含密钥）",
          any("***" in ln for ln in rep["evidence"]["log_tail"])
          and not any("SECRET_" in ln for ln in rep["evidence"]["log_tail"]))
    check("(f) matched_lines 已脱敏",
          all("SECRET_" not in ln for ln in rep["evidence"]["matched_lines"]),
          str(rep["evidence"]["matched_lines"]))
    check("(f) reason 引用的日志行也已脱敏", "SECRET_" not in rep["reason"], rep["reason"])
    check("(f) 不误伤普通单词 tokenization", "tokenization" in blob)
    check("(f) 同输入两次逐字节相同",
          DG.render_json(rep) == DG.render_json(DG.build(
              entry="convert.py", exit_code=2, log_text=secret_log,
              generated_at=FIXED_TIME, engine_version="1.4.1")))
    check("(f) mask_secrets 幂等",
          DG.mask_secrets(DG.mask_secrets(secret_log)) == DG.mask_secrets(secret_log))
    check("(f) 无秘密的日志原样通过（不误改）",
          DG.mask_secrets("❌ 找不到源文件：/tmp/正文.tex") == "❌ 找不到源文件：/tmp/正文.tex")

    tmp = tempfile.mkdtemp(prefix="slcn_diag_mask_")
    try:
        log = os.path.join(tmp, "convert.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(secret_log + "\n")
        r = run("--entry", "convert.py", "--exit-code", "2", "--log", log, "--json",
                "--generated-at", FIXED_TIME)
        check("(f) CLI --json 输出不含密钥原文",
              r.returncode == 0 and "SECRET_" not in r.stdout
              and _FAKE_KEY not in r.stdout, r.stdout[:120])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli() -> None:
    print("\n── (e) CLI：单 JSON、人类摘要、exit 0/2、只读 ──")
    tmp = tempfile.mkdtemp(prefix="slcn_diag_cli_")
    try:
        log = os.path.join(tmp, "convert.log")
        body = "❌ 稿件基本信息读取失败：JSONDecodeError: Expecting value"
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        before = (hashlib.sha256(open(log, "rb").read()).hexdigest(), sorted(os.listdir(tmp)))

        r = run("--entry", "convert.py", "--exit-code", "2", "--log", log, "--json",
                "--generated-at", FIXED_TIME)
        check("(e) --json：exit 0", r.returncode == 0, f"rc={r.returncode} err={r.stderr[:120]}")
        check("(e) --json：stdout 唯一一个 JSON 对象",
              r.stdout.strip().startswith("{") and r.stdout.strip().endswith("}")
              and "\n{" not in r.stdout.strip())
        rep = json.loads(r.stdout)
        check("(e) --json：键与分类正确", list(rep.keys()) == EXPECTED_KEYS
              and rep["category"] == "metadata_invalid", rep["category"])
        r2 = run("--entry", "convert.py", "--exit-code", "2", "--log", log, "--json",
                 "--generated-at", FIXED_TIME)
        check("(e) 同输入两次逐字节相同", r.stdout == r2.stdout)

        r = run("--entry", "convert.py", "--exit-code", "2", "--log", log)
        check("(e) 人类摘要：exit 0 且含标题/下一步/依据文档",
              r.returncode == 0 and "失败诊断：" in r.stdout and "下一步：" in r.stdout
              and "依据文档：" in r.stdout)
        check("(e) 人类摘要不含「成功」", "成功" not in r.stdout)

        after = (hashlib.sha256(open(log, "rb").read()).hexdigest(), sorted(os.listdir(tmp)))
        check("(e) 只读：日志内容不变、目录里没有新产物", before == after,
              f"{before} → {after}")

        r = run("--entry", "convert.py", "--exit-code", "2")
        check("(e) 缺 --log → exit 2", r.returncode == 2, f"rc={r.returncode}")
        r = run("--entry", "convert.py", "--exit-code", "2", "--log",
                os.path.join(tmp, "不存在的.log"))
        check("(e) 日志不存在 → exit 2 + stderr 说清原因",
              r.returncode == 2 and "找不到日志文件" in r.stderr, r.stderr[:120])
        r = run("--entry", "../evil.py", "--exit-code", "2", "--log", log)
        check("(e) 入口名带路径 → exit 2", r.returncode == 2, f"rc={r.returncode}")
        r = run("--entry", "convert.py", "--exit-code", "abc", "--log", log)
        check("(e) 退出码非整数 → exit 2", r.returncode == 2, f"rc={r.returncode}")
        r = run("--version")
        check("(e) --version 可用", r.returncode == 0 and "diagnose.py" in r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print(f"B-285a diagnose.v1 契约测试（{len(CASES)} 类夹具）")
    test_categories()
    test_schema_and_determinism()
    test_unknown_fallback()
    test_artifacts_and_tail()
    test_redaction()
    test_cli()
    print("\n" + "=" * 64)
    if _fails:
        print(f"失败 {len(_fails)} 项（共 {_passed + len(_fails)} 项）：" + "；".join(_fails[:8]))
        return 1
    print(f"全部 {_passed} 项断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
