#!/usr/bin/env python3
"""批 115 回归：`toc.py --preview` 在真实报告上可用（GUI 目录预览不得崩）。

背景：`Engine/toc.py` 的 `--preview` 分支原本读取 `note`，但 `note` 只在**正式生成**
分支前才赋值 —— 任意合法报告走预览都会 `UnboundLocalError`，外壳「生成目录」页的
预览入口 100% 失败（E2E 基线 b115-e2e-baseline 实测）。修复：把模板来源解析上移到
预览分支之前，两分支共用同一口径。

判据：
  (a) 合法报告 `--preview` → rc 0，stdout 是单行 JSON，含 articles / templateNote；
  (b) 预览不写任何文件（报告目录里仍只有报告本身）；
  (c) 同输入两次 stdout 逐字节相同（界面可稳定 diff）；
  (d) 反例：`.tex` 当报告仍 rc 2（预览同样拒绝，不许「清单为空」）。

本文件是独立脚本（与 Engine/tests 其余套件同一风格），失败退出码非 0。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
PY = sys.executable

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


REPORT = """\
WeiNong_Li
起始页码：1  终止页码：10  页数：10
字号 10.5t
第一作者中文: \\kaishu 邱德钧
中文标题: 测试中文题名一
英文标题: Test English Title One
全文行距: 13

Hao_Wu
起始页码：11  终止页码：20  页数：10
字号 11pt
第一作者中文: \\kaishu 吴昊
中文标题: 测试中文题名二
英文标题: Test English Title Two
全文行距: 13
"""


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(ENGINE / "toc.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(ENGINE))


def test_preview() -> None:
    print("\n── (a) 合法报告 --preview：rc 0 + JSON + 两篇 ──")
    tmp = Path(tempfile.mkdtemp(prefix="slcn_toc_preview_"))
    try:
        report = tmp / "信息检查TEST.txt"
        report.write_text(REPORT, encoding="utf-8")
        r = _run("--report", str(report), "--preview")
        check("(a) 退出码 0", r.returncode == 0,
              f"rc={r.returncode}｜stderr={r.stderr.strip()[-120:]}")
        check("(a) stderr 无 UnboundLocalError",
              "UnboundLocalError" not in r.stderr, r.stderr.strip()[-160:])
        payload = None
        try:
            payload = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            check("(a) stdout 是合法 JSON", False, f"{type(exc).__name__}: {exc}")
        check("(a) JSON 含 articles 两篇、templateNote 非空",
              isinstance(payload, dict)
              and len(payload.get("articles", [])) == 2
              and isinstance(payload.get("templateNote"), str)
              and bool(payload.get("templateNote")),
              str(payload)[:200])

        print("\n── (b) 预览不写文件 ──")
        check("(b) 报告目录里只有报告本身",
              sorted(p.name for p in tmp.iterdir()) == [report.name],
              repr(sorted(p.name for p in tmp.iterdir())))

        print("\n── (c) 同输入两次 stdout 逐字节相同 ──")
        r2 = _run("--report", str(report), "--preview")
        check("(c) 两次 --preview 输出相同", r.stdout == r2.stdout)

        print("\n── (d) .tex 当报告：预览同样拒绝 ──")
        bad = tmp / "main.tex"
        bad.write_text("\\documentclass{article}\n\\begin{document}hi\\end{document}\n",
                       encoding="utf-8")
        rb = _run("--report", str(bad), "--preview")
        check("(d) 退出码 2（不是 0）", rb.returncode == 2, f"rc={rb.returncode}")
        check("(d) 报错指向信息检查报告", "信息检查" in rb.stderr, rb.stderr.strip()[-120:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("B-115 toc --preview 回归（GUI 目录预览）")
    test_preview()
    print("\n" + "=" * 64)
    if _fails:
        print(f"失败 {len(_fails)} 项（共 {_passed + len(_fails)} 项）："
              + "；".join(_fails[:8]))
        return 1
    print(f"全部 {_passed} 项断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
