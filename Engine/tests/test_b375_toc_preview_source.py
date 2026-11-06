#!/usr/bin/env python3
"""B-375 D5：`toc.py --preview` 的 JSON 带 templateSource（与正式生成同一解析口径）。

背景：GUI「生成目录」预览信息卡要显示模板来源（内置模板/显式指定/上一期/本期成品）。
此前 --preview 只有 templateNote 文案，外壳只能兜底；本套件锁定 additive 字段
templateSource 在四种来源下分别取对，并确认预览仍 rc0、零写入。

本文件是独立脚本（同 Engine/tests 其余套件风格），失败退出码非 0。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
PY = sys.executable
TOC_DIR = "000 Table of Contents"

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


REPORT = r"""WeiNong_Li
卷号：19 期号：2 年份：2030
起始页码：1  终止页码：10  页数：10
字号 10.5t
第一作者中文: \kaishu 邱德钧
中文标题: 测试中文题名一
英文标题: Test English Title One
全文行距: 13
Hao_Wu
卷号：19 期号：2 年份：2030
起始页码：11  终止页码：20  页数：10
字号 11pt
第一作者中文: \kaishu 吴昊
中文标题: 测试中文题名二
英文标题: Test English Title Two
全文行距: 13
"""

TEMPLATE_ZH = r"""\documentclass{ctexart}
\newcommand{\vdistance}{\\[4.8ex]}
\begin{document}
\begin{tabularx}{\textwidth}{l@{\hspace{1em}}r}
%中间主体
\vdistance
\end{tabularx}
\end{document}
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_report(folder: Path) -> Path:
    return write(folder / "信息检查2030_01_01_00_00_00.txt", REPORT)


def snapshot(root: Path) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            stat = p.stat()
            rows.append((str(p.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return sorted(rows)


def preview(report: Path, *extra: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [PY, str(ENGINE / "toc.py"), "--report", str(report), "--preview", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ENGINE))
    return proc.returncode, proc.stdout, proc.stderr


def payload_of(stdout: str) -> dict:
    try:
        data = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}
    return data if isinstance(data, dict) else {}


def case_builtin(root: Path) -> None:
    print("\n── (a) 内置模板：无 000、无上一期 ──")
    folder = root / "builtin_case"
    report = make_report(folder)
    before = snapshot(root)
    rc, out, err = preview(report)
    after = snapshot(root)
    payload = payload_of(out)
    check("(a1) rc=0 且 stdout 是 JSON", rc == 0 and bool(payload),
          f"rc={rc} err={err.strip()[-160:]!r}")
    check("(a2) templateSource=内置模板", payload.get("templateSource") == "内置模板",
          str(payload.get("templateSource")))
    check("(a3) templateNote 非空、articles 两篇",
          bool(payload.get("templateNote")) and len(payload.get("articles") or []) == 2,
          str({k: payload.get(k) for k in ("templateNote", "articles")})[:200])
    check("(a4) 预览零写入", before == after, f"before={before} after={after}")


def case_explicit(root: Path) -> None:
    print("\n── (b) 显式指定 templateFolder ──")
    tpl = root / "explicit_tpl" / TOC_DIR
    write(tpl / "Chinese Content.tex", TEMPLATE_ZH)
    folder = root / "explicit_case"
    report = make_report(folder)
    before = snapshot(root)
    rc, out, err = preview(report, "--template", str(root / "explicit_tpl"))
    after = snapshot(root)
    payload = payload_of(out)
    check("(b1) rc=0 且 stdout 是 JSON", rc == 0 and bool(payload),
          f"rc={rc} err={err.strip()[-160:]!r}")
    check("(b2) templateSource=显式指定", payload.get("templateSource") == "显式指定",
          str(payload.get("templateSource")))
    check("(b3) templateNote 非空、articles 两篇",
          bool(payload.get("templateNote")) and len(payload.get("articles") or []) == 2,
          str({k: payload.get(k) for k in ("templateNote", "articles")})[:200])
    check("(b4) 预览零写入", before == after, f"before={before} after={after}")


def case_previous(root: Path) -> None:
    print("\n── (c) 上一期成品 ──")
    prev = root / "prev_case" / "2026年第1期(86)" / TOC_DIR
    write(prev / "Chinese Content.tex", TEMPLATE_ZH)
    folder = root / "prev_case" / "2026年第2期(87)"
    report = make_report(folder)
    before = snapshot(root)
    rc, out, err = preview(report)
    after = snapshot(root)
    payload = payload_of(out)
    check("(c1) rc=0 且 stdout 是 JSON", rc == 0 and bool(payload),
          f"rc={rc} err={err.strip()[-160:]!r}")
    check("(c2) templateSource=上一期", payload.get("templateSource") == "上一期",
          str(payload.get("templateSource")))
    check("(c3) templateNote 非空、articles 两篇",
          bool(payload.get("templateNote")) and len(payload.get("articles") or []) == 2,
          str({k: payload.get(k) for k in ("templateNote", "articles")})[:200])
    check("(c4) 预览零写入", before == after, f"before={before} after={after}")


def case_current(root: Path) -> None:
    print("\n── (d) 本期成品（000 里已有 Chinese Content.tex）──")
    folder = root / "cur_case"
    write(folder / TOC_DIR / "Chinese Content.tex", TEMPLATE_ZH)
    report = make_report(folder)
    before = snapshot(root)
    rc, out, err = preview(report)
    after = snapshot(root)
    payload = payload_of(out)
    check("(d1) rc=0 且 stdout 是 JSON", rc == 0 and bool(payload),
          f"rc={rc} err={err.strip()[-160:]!r}")
    check("(d2) templateSource=本期成品", payload.get("templateSource") == "本期成品",
          str(payload.get("templateSource")))
    check("(d3) templateNote 非空、articles 两篇",
          bool(payload.get("templateNote")) and len(payload.get("articles") or []) == 2,
          str({k: payload.get(k) for k in ("templateNote", "articles")})[:200])
    check("(d4) 预览零写入", before == after, f"before={before} after={after}")


def main() -> int:
    print("B-375 toc --preview templateSource（D5）")
    root = Path(tempfile.mkdtemp(prefix="slcn_b375_preview_src_"))
    try:
        case_builtin(root)
        case_explicit(root)
        case_previous(root)
        case_current(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n" + "=" * 64)
    if _fails:
        print(f"失败 {len(_fails)} 项（共 {_passed + len(_fails)} 项）："
              + "；".join(_fails[:8]))
        return 1
    print(f"全部 {_passed} 项断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
