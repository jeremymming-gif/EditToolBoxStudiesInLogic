#!/usr/bin/env python3
"""
从 Windows 原版程序里提取的 LaTeX 模板，生成 Swift 源文件。

模板内容逐字节来自 exe 内的字节码常量，不做任何修改。
之所以用脚本生成而不是手抄，是为了保证一个字符都不差。
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
        "// ⚠️ 本文件由 tools/gen_templates.py 自动生成，请勿手工修改。",
        "// 内容是 Windows 原版程序内置的 LaTeX 模板，逐字节一致。",
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
