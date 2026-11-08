"""P14 许可声明回归（2.14-D 新建；SCOPE-LOCK §4 固定文件名与函数名）。

五项断言，全部从文件读取（不依赖产品实现的运行期状态；help_content 的 LICENSE 常量
按 SCOPE-LOCK §4 允许的 import 方式取）：
1. 仓库根 LICENSE 是标准 MIT 正文（只把版权行换成当前署名）。
2. 当前声明面（帮助页常量 / README / 站点 footer / RELEASING / guide quickstart /
   THIRD_PARTY）不再出现「专有」与「All rights reserved」。
3. THIRD_PARTY.md 的历史 MIT 归属与第三方组件名/许可证串仍在（不因换许可被删改）。
4. LICENSE-SCOPE.md 的资产矩阵 A–G 七分区与六项处置条目齐备。
5. LICENSE-SCOPE.md 的排除项齐备，且不含「All rights reserved」。

路径根：本文件位于 PUBLIC/sources-flet/tests/，parents[2] == PUBLIC。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# SPDX 权威正文（https://spdx.org/licenses/MIT.txt，2026-11-08 取回；
# EVID/executor/sources/spdx-MIT-20261108T050953Z.txt，sha256 c3b1b78b…cef24db4）。
SPDX_MIT = '''MIT License

Copyright (c) <year> <copyright holders>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.'''


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _without_copyright(text: str) -> str:
    """删掉首个以 Copyright 开头的非空行（版权行不参与正文比对）。"""
    lines = text.split("\n")
    out = []
    dropped = False
    for line in lines:
        if not dropped and line.startswith("Copyright ") and line.strip():
            dropped = True
            continue
        out.append(line)
    assert dropped, "未找到版权行"
    return "\n".join(out)


def _readme_license_section() -> str:
    text = _read("README.md")
    match = re.search(r"^## 许可与注意\s*$.*?(?=^## |\Z)", text, re.M | re.S)
    assert match, "README.md 缺少 `## 许可与注意` 段"
    return match.group(0)


def _footer_message() -> str:
    """取 config.mts 中含 message 的那个 footer 块（locale 内也有 footer 块，需按括号配对）。"""
    text = _read("docs/.vitepress/config.mts")
    for found in re.finditer(r"\bfooter\s*:", text):
        start = text.find("{", found.end())
        assert start != -1, "config.mts footer 后缺少 {"
        depth = 0
        end = start
        for pos in range(start, len(text)):
            char = text[pos]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = pos
                    break
        msg = re.search(r"message:\s*'([^']*)'", text[start : end + 1])
        if msg:
            return msg.group(1)
    raise AssertionError("config.mts 的 footer 块缺少 message")


def test_license_is_standard_mit() -> None:
    text = _read("LICENSE")
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text
    assert _norm(_without_copyright(text)) == _norm(_without_copyright(SPDX_MIT)), (
        "LICENSE 正文（去版权行后）与 SPDX 标准 MIT 不一致"
    )
    for forbidden in ("专有", "All rights reserved", "商业用途", "事先书面许可"):
        assert forbidden not in text, f"LICENSE 仍含旧专有措辞：{forbidden}"


def test_no_current_proprietary_surfaces() -> None:
    import sys

    app = ROOT / "sources-flet" / "app"
    if str(app) not in sys.path:  # 直接运行本文件时也可断言 help_content
        sys.path.insert(0, str(app))
    from ui.help_content import LICENSE as HELP_LICENSE  # noqa: PLC0415

    assert "MIT" in HELP_LICENSE
    assert "Jeremymming" in HELP_LICENSE
    for forbidden in ("专有", "All rights reserved", "仅用于"):
        assert forbidden not in HELP_LICENSE, f"help_content.LICENSE 仍含：{forbidden}"

    readme = _readme_license_section()
    for forbidden in ("专有", "All rights reserved"):
        assert forbidden not in readme, f"README `## 许可与注意` 段仍含：{forbidden}"

    footer = _footer_message()
    assert "仅用于《逻辑学研究》编辑部内部流程" not in footer
    assert "MIT License · 为《逻辑学研究》编辑部工作流而建" == footer.strip()

    for rel in (
        "RELEASING.md",
        "docs/guide/quickstart.md",
        "THIRD_PARTY.md",
    ):
        text = _read(rel)
        for forbidden in ("专有", "All rights reserved"):
            assert forbidden not in text, f"{rel} 仍含：{forbidden}"


def test_third_party_history_preserved() -> None:
    text = _read("THIRD_PARTY.md")
    for token in ("v1.0.0–v1.5.0", "MIT"):
        assert token in text, f"THIRD_PARTY.md 缺少历史归属：{token}"
    for token in (
        "flet",
        "PyInstaller",
        "pystray",
        "Pillow",
        "pypdf",
        "pypinyin",
        "lxml",
        "Apache-2.0",
        "GPL-2.0-or-later",
        "LGPL-3.0",
        "MIT-CMU",
        "BSD-3",
        "PSF",
    ):
        assert token in text, f"THIRD_PARTY.md 缺少组件名/许可证串：{token}"


def test_license_scope_matrix() -> None:
    path = ROOT / "LICENSE-SCOPE.md"
    assert path.exists(), "LICENSE-SCOPE.md 不存在"
    text = path.read_text(encoding="utf-8")
    for letter in "ABCDEFG":
        assert f"## {letter}." in text, f"LICENSE-SCOPE.md 缺少 {letter} 分区"
    for item in (
        "投稿模板",
        "品牌与刊名",
        "截图",
        "版权署名",
        "官网页脚",
        "编辑笔记",
    ):
        assert item in text, f"LICENSE-SCOPE.md 缺少六项处置条目：{item}"
    assert "无待确认项" in text
    # 「待确认」只允许作为「无待确认项」的一部分出现（不得留下未决项）
    assert text.count("待确认") == text.count("无待确认项")
    assert "商标" in text


def test_license_scope_excludes_non_public_assets() -> None:
    text = _read("LICENSE-SCOPE.md")
    for token in ("真实作者稿件", "notes", "密钥", "旧版本发行包"):
        assert token in text, f"LICENSE-SCOPE.md 缺少排除项：{token}"
    assert "All rights reserved" not in text
