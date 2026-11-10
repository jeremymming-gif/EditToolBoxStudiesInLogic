"""任务反馈文本的小工具：把引擎输出整理成界面上能直接显示的文本。

P04-O2：引擎 `Engine/slcn/pdfmerge.py` 打的是 `⚠️`（U+26A0 + U+FE0F 变体选择符）。
壳里按行解析 `⚠` 时，`\\s*` 不吃 U+FE0F，它就被留在 warning 文本开头，
结果卡于是渲染成 `· ️ 扉页.pdf 有 3 页`（一个看不见但占位的变体符 + 双空格）。

这里只做「显示层」的规范化：不改引擎输出、不改判定与放行规则。
"""
from __future__ import annotations

# 不可见 / 格式化字符：
# - U+FE00–FE0F   变体选择符（VS1–VS16，`⚠️` 里的 U+FE0F 就在这）
# - U+E0100–E01EF 变体选择符补充区（VS17–VS256）
# - U+200B–200F   零宽空格 / ZWNJ / ZWJ / LRM / RLM
# - U+2060        word joiner
_FORMAT_CHARS = frozenset(
    list(range(0xFE00, 0xFE10))
    + list(range(0xE0100, 0xE01F0))
    + list(range(0x200B, 0x2010))
    + [0x2060]
)
_STRIP_TABLE = {code: None for code in _FORMAT_CHARS}


def strip_format_chars(text: str) -> str:
    """去掉不可见格式字符并去掉首尾空白（P04-O2 / P08 I-3）。

    >>> strip_format_chars("\\ufe0f 扉页.pdf 有 3 页")
    '扉页.pdf 有 3 页'
    """
    if not text:
        return ""
    return str(text).translate(_STRIP_TABLE).strip()


def clean_warnings(values) -> list[str]:
    """清洗一组 warning 文本：去格式字符、丢空条目，保持原顺序。"""
    out: list[str] = []
    for value in values or []:
        text = strip_format_chars(str(value or ""))
        if text:
            out.append(text)
    return out
