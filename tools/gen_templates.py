#!/usr/bin/env python3
"""
历史工具（dormant）：把 Windows 原版程序里的 LaTeX 模板常量写成模板夹具。

最初它会生成 Swift 源 `ContentTemplates.swift`（v1.5.0 的 mac 外壳常量）；批108 删除旧外壳后，
模板字面量改由 `Engine/tests/fixtures/content_templates.json` 承载，本脚本随之改为写那份 JSON。
它是**一次性提取工具**，不是日常改模板的入口：日常改模板直接编辑 JSON 夹具，再跑
`Engine/tools/gen_content_templates.py` 重生成 Python 侧。

输出曾逐字节来自 exe 内的字节码常量；**2026-10-27 起中/英内置模板已升级为 2026 年第 3 期(88) 版式**，
与 Windows 原版不再逐字节一致。**重跑本脚本会覆盖这次升级，慎用。**
"""
import json
import os

TEMPLATES = [
    ("chContentHead", "/tmp/toolx/charti_const_3.txt"),
    ("chContentEnd", "/tmp/toolx/charti_const_4.txt"),
    ("enContentHead", "/tmp/toolx/charti_const_5.txt"),
]

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "Engine", "tests", "fixtures", "content_templates.json")


def main():
    values = {}
    for name, path in TEMPLATES:
        with open(path, encoding="utf-8") as f:
            values[name] = f.read()
    # 原程序编译时把中英文目录的结尾视为同一个字符串（内容相同被去重）
    values["enContentEnd"] = values["chContentEnd"]

    target = os.path.normpath(OUT)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        json.dump(values, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("已生成", target)


if __name__ == "__main__":
    main()
