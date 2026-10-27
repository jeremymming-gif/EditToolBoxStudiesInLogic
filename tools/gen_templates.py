#!/usr/bin/env python3
"""
从 Windows 原版程序里提取的 LaTeX 模板，生成 Swift 源文件（历史工具，dormant）。

输出曾逐字节来自 exe 内的字节码常量；**2026-10-27 起中/英内置模板已升级为 2026 年第 3 期(88) 版式**，
与 Windows 原版不再逐字节一致（现值台账见 `spec/charti.md` STEP 1b）。**重跑本脚本会覆盖这次升级，慎用**；
现在改模板的路径是：直接改 `sources-mac/Features/ContentTemplates.swift` 的字面量，再跑
`Engine/tools/gen_content_templates.py` 重生成 Python 侧并重冻结 golden。
"""
import os

TEMPLATES = [
    ("chContentHead", "/tmp/toolx/charti_const_3.txt"),
    ("chContentEnd", "/tmp/toolx/charti_const_4.txt"),
    ("enContentHead", "/tmp/toolx/charti_const_5.txt"),
]

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "sources-mac", "Features", "ContentTemplates.swift")


def swift_escape(s: str) -> str:
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)


def main():
    lines = [
        "// ⚠️ 本文件原由 tools/gen_templates.py 从 Windows 原版程序提取生成。",
        "// 2026-10-27 起中/英内置目录模板已升级为 2026 年第 3 期(88) 版式，与 Windows 原版不再逐字节一致；",
        "// 重跑提取脚本会覆盖这次升级 —— 改模板请直接改下面的字面量，再跑 Engine/tools/gen_content_templates.py 重生成并重冻结 golden。",
        "",
        "enum ContentTemplates {",
        "",
    ]

    for name, path in TEMPLATES:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        lines.append("    /// %s —— %d 个字符" % (name, len(content)))
        lines.append('    static let %s = "%s"' % (name, swift_escape(content)))
        lines.append("")

    lines.append("    /// 原程序编译时把中英文目录的结尾视为同一个字符串（内容相同被去重）")
    lines.append("    static let enContentEnd = chContentEnd")
    lines.append("}")

    target = os.path.normpath(OUT)
    with open(target, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("已生成", target)


if __name__ == "__main__":
    main()
