#!/usr/bin/env python3
"""批 115 回归：`toc.py --result-json` 的 `articles` 计数（additive 字段）。

背景：整期装配工作台「生成目录」详情要显示「篇目 26 篇」，但旧结果 JSON 只有
模板/落位/栏目，没有篇目数；外壳只能二次解析报告，口径会和引擎漂移。
D1-115-29 裁决：结果 JSON 增 `articles:{total,inToc}`，**不改任何旧字段**。

判据：
  (a) 合法报告 + `--result-json`（只生成 tex）→ rc 0，JSON 存在；
  (b) `articles == {"total": 2, "inToc": 2}`；
  (c) 旧字段仍在（templateSource / placed / column / manualEdits / warnings）；
  (d) 只生成 tex 时 `placed` 四个路径均为空、`compiled` false。

本文件与 Engine/tests 其余套件同风格：独立脚本，失败退出码非 0。
"""
from __future__ import annotations

import json
import os
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


def test_result_json() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="slcn_toc_result_"))
    issue = tmp / "2026年第5期(90)"
    issue.mkdir()
    report = issue / "信息检查TEST.txt"
    report.write_text(REPORT, encoding="utf-8")
    out = issue / "目录结果.json"
    proc = subprocess.run(
        [PY, str(ENGINE / "toc.py"), "--report", str(report),
         "--result-json", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ENGINE),
    )
    print("\n── (a) 只生成 tex + --result-json ──")
    check("(a) rc==0", proc.returncode == 0,
          f"rc={proc.returncode} stderr={proc.stderr.strip()[-200:]}")
    check("(a) 结果 JSON 已写", out.is_file(), str(out))
    if not out.is_file():
        return
    payload = json.loads(out.read_text(encoding="utf-8"))
    print("\n── (b) articles 计数 ──")
    check("(b) articles", payload.get("articles") == {"total": 2, "inToc": 2},
          repr(payload.get("articles")))
    print("\n── (c) 旧字段未丢 ──")
    for key in ("templateSource", "templateNote", "tocDir", "placed", "compiled",
                "pages", "column", "manualEdits", "warnings"):
        check(f"(c) 保留 {key}", key in payload)
    print("\n── (d) text-only 语义 ──")
    check("(d) placed 全空", all(not (payload.get("placed") or {}).get(k)
                                 for k in ("zhTex", "zhPdf", "enTex", "enPdf")),
          repr(payload.get("placed")))
    check("(d) compiled false", payload.get("compiled") is False)
    print("\n── (e) 批 117：outputs + 卷/期 warning ──")
    outputs = payload.get("outputs") or {}
    check("(e) outputs 四键",
          set(outputs) == {"zhTex", "enTex", "zhPdf", "enPdf"}, repr(outputs))
    for key, stem in (("zhTex", "Chinese Content_"), ("enTex", "contents_")):
        value = str(outputs.get(key) or "")
        check(f"(e) {key} 时间戳文件", os.path.basename(value).startswith(stem),
              value)
        check(f"(e) {key} 绝对路径且存在",
              os.path.isabs(value) and os.path.isfile(value), value)
    check("(e) 未编译 PDF 为空",
          not outputs.get("zhPdf") and not outputs.get("enPdf"), repr(outputs))
    check("(e) 卷/期 warning 已写",
          any("卷/期无法确定" in w for w in (payload.get("warnings") or [])),
          repr(payload.get("warnings")))


def test_result_json_place() -> None:
    """批 117：`--place` 时 outputs 指向 000 下固定名（而非根下时间戳文件）。"""
    tmp = Path(tempfile.mkdtemp(prefix="slcn_toc_place_"))
    issue = tmp / "2026年第5期(90)"
    issue.mkdir()
    report = issue / "信息检查TEST.txt"
    report.write_text(REPORT, encoding="utf-8")
    out = issue / "目录结果.json"
    proc = subprocess.run(
        [PY, str(ENGINE / "toc.py"), "--report", str(report),
         "--place", "--result-json", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ENGINE),
    )
    print("\n── (f) --place 的 outputs ──")
    check("(f) place rc==0", proc.returncode == 0,
          f"rc={proc.returncode} stderr={proc.stderr.strip()[-200:]}")
    check("(f) 结果 JSON 已写", out.is_file(), str(out))
    if not out.is_file():
        return
    payload = json.loads(out.read_text(encoding="utf-8"))
    outputs = payload.get("outputs") or {}
    toc_dir = str(payload.get("tocDir") or "")
    for key, fixed in (("zhTex", "Chinese Content.tex"), ("enTex", "contents.tex")):
        value = str(outputs.get(key) or "")
        check(f"(f) {key} 固定名", os.path.basename(value) == fixed, value)
        check(f"(f) {key} 落在 000 下", os.path.dirname(value) == toc_dir, value)
        check(f"(f) {key} 存在", os.path.isfile(value), value)
    check("(f) 未编译 PDF 为空",
          not outputs.get("zhPdf") and not outputs.get("enPdf"), repr(outputs))
    check("(f) 期号 warning 已写",
          any("卷/期无法确定" in w for w in (payload.get("warnings") or [])),
          repr(payload.get("warnings")))


def main() -> int:
    test_result_json()
    test_result_json_place()
    print(f"\n结果：{_passed} passed / {len(_fails)} failed")
    if _fails:
        print("失败：" + "、".join(_fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
