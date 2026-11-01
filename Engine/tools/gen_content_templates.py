#!/usr/bin/env python3
r"""从 JSON 夹具生成 `slcn/content_templates.py`（内置目录模板）。

**为什么要生成而不是手抄**：这四个字符串是散文一样的长 LaTeX 片段，里面全是反斜杠、
换行、全角标点。手抄一遍几乎必然出现「少一个反斜杠」这种灾难性差异，而且很难发现。
做法是把 `tests/fixtures/content_templates.json` 里的字面量写成 Python 常量；
JSON 里存的是**真实字符**（没有 Swift 转义要还原），生成物与夹具逐字节一致。

源头沿革（历史）：这四个常量原先在 v1.5.0 的 Swift 外壳常量文件 `ContentTemplates.swift` 里；
批108 删除旧外壳时把字面量抽成 JSON 夹具 —— 模板本身与外壳无关，外壳退了模板还在。

跑法（在 Engine 下）：

    python3 tools/gen_content_templates.py            # 生成/更新 slcn/content_templates.py
    python3 tools/gen_content_templates.py --check    # 只校验是否与夹具同步（不同步退 1）
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ENGINE, "tests/fixtures/content_templates.json")
OUT = os.path.join(ENGINE, "slcn/content_templates.py")

NAMES = ["chContentHead", "chContentEnd", "enContentHead", "enContentEnd"]

HEADER = '''r"""内置目录模板 —— **本文件由 `tools/gen_content_templates.py` 生成，不要手改**。

源头：`tests/fixtures/content_templates.json`（四个模板串的**真实字符**，不含任何转义；
中/英内置模板已于 2026-10-27 升级为 2026 年第 3 期(88) 版式，其上游是 Windows 原版
`charti_build_content.py` 里写死的模板；旧外壳退役后模板独立存在，沿革见 git 记录）。

「生成目录」只在**找不到可继承的成品**时才用它（正常路径是继承上一期的
`000 Table of Contents/Chinese Content.tex` 或 `contents.tex`）。即便如此，
它也必须与夹具逐字节一致 —— 否则兜底情形下会产出不同的目录。

要改模板：改 `tests/fixtures/content_templates.json`，然后跑
`python3 tools/gen_content_templates.py`。
"""
'''


def load_fixture(path: str = FIXTURE) -> dict[str, str]:
    """读取 JSON 夹具：`{模板名: 真实字符串}`；缺键立刻报错（不许悄悄生成半套）。"""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    missing = [n for n in NAMES if n not in data]
    if missing:
        raise SystemExit(f"❌ 夹具缺模板名：{missing}（{path}）")
    return {n: data[n] for n in NAMES}


def py_literal(s: str) -> str:
    r"""把字符串写成 Python 字面量：优先用裸三引号（LaTeX 里反斜杠太多，raw 更省心）。"""
    if '"""' not in s and not s.endswith('"') and "\\" in s:
        # raw 三引号：反斜杠原样，但结尾不能是反斜杠、也不能含 """
        if not s.endswith("\\"):
            return 'r"""' + s + '"""'
    return repr(s)


def render(values: dict[str, str]) -> str:
    parts = [HEADER]
    for n in NAMES:
        assert n in values, f"模板数据里没有 {n}"
        parts.append(f"{n} = {py_literal(values[n])}\n")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 JSON 夹具生成内置目录模板（slcn/content_templates.py）")
    ap.add_argument("--check", action="store_true", help="只校验是否与夹具同步")
    ap.add_argument("--fixture", default=FIXTURE, help="模板夹具路径")
    ap.add_argument("--out", default=OUT, help="生成的 Python 模块路径")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.fixture):
        print(f"❌ 找不到模板夹具：{args.fixture}", file=sys.stderr)
        return 2
    text = render(load_fixture(args.fixture))

    if args.check:
        cur = open(args.out, encoding="utf-8").read() if os.path.isfile(args.out) else ""
        if cur == text:
            print("PASS  内置目录模板与 tests/fixtures/content_templates.json 一致")
            return 0
        print("FAIL  slcn/content_templates.py 与模板夹具不同步；"
              "跑 python3 tools/gen_content_templates.py 重新生成", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"✅ 已生成 {args.out}（4 个模板串，共 {len(text)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
