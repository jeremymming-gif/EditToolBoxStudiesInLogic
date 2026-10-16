#!/usr/bin/env python3
r"""从 Swift 源码生成 `slcn/content_templates.py`（内置目录模板）。

**为什么要生成而不是手抄**：这四个字符串是散文一样的长 LaTeX 片段，里面全是反斜杠、
换行、全角标点。手抄一遍几乎必然出现「少一个反斜杠」这种灾难性差异，而且很难发现。
做法是直接解析 `sources-mac/Features/ContentTemplates.swift` 里的字符串字面量，
把 Swift 的转义（`\\` `\n` `\t` `\"`）翻成 Python 的同一套转义 ——
两边对这些转义的定义一致，所以逐字节相同。

跑法（在 Engine 下）：

    python3 tools/gen_content_templates.py            # 生成/更新 slcn/content_templates.py
    python3 tools/gen_content_templates.py --check    # 只校验是否与 Swift 源同步（不同步退 1）
"""

from __future__ import annotations

import argparse
import os
import re
import sys

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWIFT = os.path.join(os.path.dirname(ENGINE), "sources-mac/Features/ContentTemplates.swift")
OUT = os.path.join(ENGINE, "slcn/content_templates.py")

HEADER = '''r"""内置目录模板 —— **本文件由 `tools/gen_content_templates.py` 生成，不要手改**。

源头：`sources-mac/Features/ContentTemplates.swift`（mac 界面的内置模板常量，
其上游是原版 `charti_build_content.py` 里写死的 2022 年模板）。

「生成目录」只在**找不到可继承的成品**时才用它（正常路径是继承上一期的
`000 Table of Contents/Chinese Content.tex` 或 `contents.tex`）。即便如此，
它也必须与 Swift 那份逐字节一致 —— 否则两个平台在兜底情形下会产出不同的目录。

要改模板：改 Swift 那份，然后跑
`python3 tools/gen_content_templates.py`。
"""
'''


def parse_swift(path: str) -> dict[str, str]:
    """从 Swift 源码里取出 `static let NAME = "..."` 的字符串值。"""
    src = open(path, encoding="utf-8").read()
    out: dict[str, str] = {}
    # 只处理单行字符串字面量（本文件里都是单行）；转义按 Swift 规则还原
    for m in re.finditer(r'static let (\w+)\s*=\s*"((?:[^"\\]|\\.)*)"', src):
        name, raw = m.group(1), m.group(2)
        out[name] = _unescape(raw)
    # 别名：`static let enContentEnd = chContentEnd`
    for m in re.finditer(r'static let (\w+)\s*=\s*(\w+)\s*$', src, re.M):
        alias, target = m.group(1), m.group(2)
        if target in out:
            out[alias] = out[target]
    return out


def _unescape(raw: str) -> str:
    """Swift 字符串转义 → 实际字符。

    ⚠️ 只处理本项目真正用到的几种：`\\\\` `\\"` `\\n` `\\t`。
    其余（`\\u{...}` 之类）这里不该出现，出现了就直接报错，免得悄悄生成错东西。
    """
    out: list[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        nxt = raw[i + 1] if i + 1 < len(raw) else ""
        if nxt == "\\":
            out.append("\\")
        elif nxt == '"':
            out.append('"')
        elif nxt == "n":
            out.append("\n")
        elif nxt == "t":
            out.append("\t")
        elif nxt == "r":
            out.append("\r")
        else:
            raise SystemExit(f"遇到没处理过的 Swift 转义：\\{nxt}（在 …{raw[max(0,i-20):i+20]!r} 附近）")
        i += 2
    return "".join(out)


def py_literal(s: str) -> str:
    r"""把字符串写成 Python 字面量：优先用裸三引号（LaTeX 里反斜杠太多，raw 更省心）。"""
    if '"""' not in s and not s.endswith('"') and "\\" in s:
        # raw 三引号：反斜杠原样，但结尾不能是反斜杠、也不能含 """
        if not s.endswith("\\"):
            return 'r"""' + s + '"""'
    return repr(s)


def render(values: dict[str, str]) -> str:
    names = ["chContentHead", "chContentEnd", "enContentHead", "enContentEnd"]
    parts = [HEADER]
    for n in names:
        assert n in values, f"Swift 源里没有 {n}"
        parts.append(f"{n} = {py_literal(values[n])}\n")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 Swift 生成内置目录模板（slcn/content_templates.py）")
    ap.add_argument("--check", action="store_true", help="只校验是否与 Swift 源同步")
    args = ap.parse_args(argv)

    if not os.path.isfile(SWIFT):
        print(f"❌ 找不到 Swift 源：{SWIFT}", file=sys.stderr)
        return 2
    text = render(parse_swift(SWIFT))

    if args.check:
        cur = open(OUT, encoding="utf-8").read() if os.path.isfile(OUT) else ""
        if cur == text:
            print("PASS  内置目录模板与 Swift 源一致")
            return 0
        print("FAIL  slcn/content_templates.py 与 Swift 源不同步；"
              "跑 python3 tools/gen_content_templates.py 重新生成", file=sys.stderr)
        return 1

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"✅ 已生成 {OUT}（4 个模板串，共 {len(text)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
