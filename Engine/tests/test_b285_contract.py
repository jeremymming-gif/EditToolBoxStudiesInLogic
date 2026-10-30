#!/usr/bin/env python3
"""B-285a 总契约测试：旧 CLI golden、ENGINE_API 文档、quick 白名单、冻结快照、隐私。

判据（B-285 spec C1/C2/C3/C8）：diagnose.v1 的单 JSON/单键表；旧 CLI 默认 stdout/stderr/
退出码零变化（engine_main.py 的 ENTRIES 接线按 Lead 裁决延到 B-285b，本测试守住「a 批
没偷偷接线」）；ENGINE_API 含 schema 示例、退出码表、17 类与「报告怎么读」；quick 白名单
含三套新测试；infocheck 冻结 txt sha256 不变、json 只多 summary。

本文件是独立脚本（与 `Engine/tests/` 其余套件同一风格），失败退出码非 0。
"""
from __future__ import annotations

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
GOLDEN_TXT_SHA = "f00cbc25a5912171ed4add44e0525b6923fd490839594ae36c29263db081e1b8"
GOLDEN_IC = ENGINE / "tests" / "fixtures" / "golden_infocheck"
NEW_FILES = [
    ENGINE / "diagnose.py",
    ENGINE / "slcn" / "diagnose.py",
    ENGINE / "slcn" / "report_header.py",
    ENGINE / "tests" / "test_b285_diagnose.py",
    ENGINE / "tests" / "test_b285_report_header.py",
    ENGINE / "tests" / "test_b285_contract.py",
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


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(ENGINE / script), *args], cwd=str(ENGINE),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def last_line(stdout: str) -> str:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# ─────────────────────────────────────────────────────────────────────────
def test_docs() -> None:
    print("\n── (a) C8 文档契约：diagnose.v1 + 17 类 + 退出码 + 报告怎么读 ──")
    path = ENGINE / "docs" / "ENGINE_API.md"
    docs = path.read_text(encoding="utf-8")
    check("(a) ENGINE_API 有「## 十一」章节", "## 十一、" in docs)
    check("(a) 章节标题含真实标题「失败诊断（diagnose.v1）」", "失败诊断（diagnose.v1）" in docs)
    marker = "## 十一、"
    section = docs.split(marker, 1)[1].split("\n## ", 1)[0] if marker in docs else ""
    check("(a) 含 schema 版本与 JSON 示例", '"schema": "diagnose.v1"' in section, section[:80])
    check("(a) 含 17 类类别表",
          all(c in section for c in DG.CATEGORIES),
          str([c for c in DG.CATEGORIES if c not in section]))
    check("(a) 含退出码表（0/2 与参数错误）", "退出码" in section and "参数错误" in section)
    check("(a) 含「报告怎么读」", "报告怎么读" in section)
    check("(a) 记录 ENTRIES 接线延到 B-285b", "B-285b" in section)
    # C8：每个 doc_anchor 可解析到真实标题（doc_anchor 指向上面这节）
    check("(a) doc_anchor 指向 §十一 真实标题",
          "§十一" in DG.DOC_ANCHOR and "失败诊断（diagnose.v1）" in DG.DOC_ANCHOR
          and DG.DOC_ANCHOR.split(" ", 1)[1] in docs, DG.DOC_ANCHOR)


def test_quick_whitelist() -> None:
    print("\n── (b) quick 白名单：三套新测试都在，且文件真实存在 ──")
    sh = (ENGINE / "tools" / "run_engine_tests.sh").read_text(encoding="utf-8")
    start = sh.index("QUICK=(")
    block = sh[start:sh.index(")", start)]
    for name in ("test_b285_contract", "test_b285_diagnose", "test_b285_report_header"):
        check(f"(b) quick 白名单含 {name}", name in block)
        check(f"(b) tests/{name}.py 存在", (ENGINE / "tests" / f"{name}.py").is_file())


def test_frozen_fixture() -> None:
    print("\n── (c) infocheck 冻结快照：txt sha256 不变、json 只多 summary ──")
    txt = (GOLDEN_IC / "信息检查GOLDEN.txt").read_bytes()
    check("(c) 冻结 txt sha256 不变",
          hashlib.sha256(txt).hexdigest() == GOLDEN_TXT_SHA,
          hashlib.sha256(txt).hexdigest())
    payload = json.loads((GOLDEN_IC / "信息检查GOLDEN.json").read_text(encoding="utf-8"))
    old_keys = {"generatedAt", "issueFolder", "articles"}
    check("(c) json 旧字段原样保留", old_keys <= set(payload), str(set(payload)))
    check("(c) json 只新增 summary 一个键",
          set(payload) - old_keys == {"summary"}, str(set(payload) - old_keys))
    check("(c) summary 字段给外壳用",
          set(payload["summary"]) == {"articles", "hasTeX", "hasPDF", "inconsistencies",
                                      "conclusion"}, str(payload["summary"]))
    check("(c) articles 字段没被改写（仍是 1 篇）",
          isinstance(payload["articles"], list) and len(payload["articles"]) == 1)


def _make_issue(root: Path) -> Path:
    issue = root / "2026年第5期(90)"
    folder = issue / "01 Test_A"
    folder.mkdir(parents=True)
    (folder / "Test_A.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n测试。\n\\end{document}\n", encoding="utf-8")
    return issue


def test_old_cli_golden() -> None:
    print("\n── (d) C3 旧 CLI golden：默认 stdout/退出码形状不变 ──")
    tmp = Path(tempfile.mkdtemp(prefix="slcn_contract_"))
    try:
        # proofcheck：stdout 末行契约行形状不变，且默认输出里不出现「摘要」
        tex = tmp / "Test_Author.tex"
        tex.write_text("\\documentclass{article}\n\\begin{document}\n测试正文。\n"
                       "\\end{document}\n", encoding="utf-8")
        out_md = tmp / "proof.md"
        r = run("proofcheck.py", "--tex", str(tex), "--out", str(out_md))
        check("(d) proofcheck 退出码仍是 0/1", r.returncode in (0, 1), f"rc={r.returncode}")
        check("(d) proofcheck 契约行形状不变",
              re.fullmatch(r"完成。一校核查：命中 \d+ 处（可自动改 \d+ 处）（报告：.+）",
                           last_line(r.stdout)) is not None, last_line(r.stdout))
        check("(d) proofcheck 默认 stdout 不出现摘要头",
              "摘要" not in r.stdout and "top 5" not in r.stdout)
        check("(d) proofcheck 仍写 `_一校核查.md` 且以摘要块开头",
              out_md.is_file() and out_md.read_text(encoding="utf-8").startswith("**摘要**："))

        # infocheck：契约行形状不变
        issue = _make_issue(tmp / "ic")
        r = run("infocheck.py", "--folder", str(issue))
        check("(d) infocheck 退出码仍是 0", r.returncode == 0, f"rc={r.returncode} {r.stderr[:100]}")
        check("(d) infocheck 契约行形状不变",
              re.fullmatch(r"完成。检查 1 篇（有 tex 1 / 有 pdf 0；不一致 0 项）（报告：.+\.txt）",
                           last_line(r.stdout)) is not None, last_line(r.stdout))
        check("(d) infocheck 默认 stdout 不出现摘要头", "摘要" not in r.stdout)
        produced = sorted(p.name for p in issue.iterdir())
        check("(d) infocheck 新增 `_摘要.md` 且旧 txt/json 都在",
              any(n.endswith("_摘要.md") for n in produced)
              and any(n.endswith(".txt") for n in produced)
              and any(n.endswith(".json") for n in produced), str(produced))

        # compileall：dry-run 契约行形状不变、且不写报告
        issue2 = _make_issue(tmp / "ca")
        r = run("compileall.py", "--folder", str(issue2), "--dry-run")
        check("(d) compileall --dry-run 退出码 0", r.returncode == 0, f"rc={r.returncode}")
        check("(d) compileall 契约行形状不变",
              re.fullmatch(r"完成。编译 0 篇（成功 0 / 失败 0）（目录：.+）",
                           last_line(r.stdout)) is not None, last_line(r.stdout))
        check("(d) compileall --dry-run 不写 `编译报告.md`（不真跑就不落产物）",
              not (issue2 / "编译报告.md").exists())
        check("(d) compileall 默认 stdout 不出现报告头", "编译报告" not in r.stdout)

        # checkbib：报告仍是同一个入口，摘要头加在最前
        bib = tmp / "退回.bib"
        bib.write_text("@article{key1,\n  title = {示例},\n}\n", encoding="utf-8")
        rep_md = tmp / "checkbib.md"
        r = run("checkbib.py", "--returned", str(bib), "--out", str(rep_md))
        check("(d) checkbib 退出码仍是 0/1", r.returncode in (0, 1), f"rc={r.returncode}")
        check("(d) checkbib 默认 stdout 不出现摘要头", "摘要" not in r.stdout)
        check("(d) checkbib 报告 `_回读校验报告` 同样以摘要块开头",
              rep_md.is_file() and rep_md.read_text(encoding="utf-8").startswith("**摘要**："))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_engine_main_deferral() -> None:
    print("\n── (e) engine_main 延期断言（a 批不接线）＋ 隐私 ──")
    from engine_main import ENTRIES  # noqa: PLC0415

    expected = ("convert.py", "checkbib.py", "mergepdf.py", "cleantemp.py", "compileall.py",
                "proofcheck.py", "infocheck.py", "toc.py", "publish.py", "bibdb.py",
                "envcheck.py")
    check("(e) ENTRIES 仍是 11 项且未偷偷加 diagnose.py（接线延 B-285b）",
          tuple(ENTRIES) == expected, str(ENTRIES))
    # R5 隐私：新文件不得含真实姓名/邮箱/API key（针样在运行时拼，避免本文件自我命中）
    email = re.compile(r"[\w.+-]+" + r"@" + r"[\w-]+\.[\w.]+")
    needles = ["sk" + "-", "api" + "_key"]
    for f in NEW_FILES:
        text = f.read_text(encoding="utf-8")
        check(f"(e) {f.name} 无邮箱/密钥痕迹",
              not email.search(text) and not any(n in text for n in needles))
    # report_header 必须是纯函数：不取时钟、不做文件 IO
    src = (ENGINE / "slcn" / "report_header.py").read_text(encoding="utf-8")
    check("(e) report_header 不取当前时间（确定性）",
          "datetime" not in src and "time." not in src and "now(" not in src)
    check("(e) report_header 不做文件 IO", "open(" not in src and "os." not in src)


def test_diagnose_json() -> None:
    print("\n── (f) C1 diagnose：单 JSON、枚举、确定性、退出码 ──")
    tmp = Path(tempfile.mkdtemp(prefix="slcn_contract_diag_"))
    try:
        log = tmp / "fail.log"
        log.write_text("! Undefined control sequence.\nl.12 \\foo\n", encoding="utf-8")
        args = ("--entry", "compileall.py", "--exit-code", "1", "--log", str(log),
                "--json", "--generated-at", "2026-10-30T00:00:00+0800")
        r1 = run("diagnose.py", *args)
        r2 = run("diagnose.py", *args)
        check("(f) --json 成功 exit 0", r1.returncode == 0, f"rc={r1.returncode}")
        rep = json.loads(r1.stdout)
        check("(f) 单个 JSON 对象（顶层无多余输出）",
              r1.stdout.strip().startswith("{") and r1.stdout.strip().endswith("}")
              and "\n{" not in r1.stdout.strip())
        check("(f) schema/category/confidence 契约",
              rep["schema"] == "diagnose.v1" and rep["category"] in DG.CATEGORIES
              and 0.0 <= rep["confidence"] <= 1.0, str(rep)[:160])
        check("(f) 同输入两次逐字节相同", r1.stdout == r2.stdout)
        r = run("diagnose.py", "--entry", "convert.py", "--exit-code", "2")
        check("(f) 参数错误 exit 2", r.returncode == 2, f"rc={r.returncode}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("B-285a 总契约测试")
    test_docs()
    test_quick_whitelist()
    test_frozen_fixture()
    test_old_cli_golden()
    test_engine_main_deferral()
    test_diagnose_json()
    print("\n" + "=" * 64)
    if _fails:
        print(f"失败 {len(_fails)} 项（共 {_passed + len(_fails)} 项）：" + "；".join(_fails[:8]))
        return 1
    print(f"全部 {_passed} 项断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
