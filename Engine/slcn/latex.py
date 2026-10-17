"""LaTeX 感知的文本工具：括号配平、注释剥离、\\newcommand 宏的解析与改写。

之所以不能直接用正则，是因为 SLCN 模板里的宏值常常长这样：

    \\newcommand{\\myfirstaffiliationEN}{%          % 请输入第一作者单位
    {Department of Philosophy, Soochow University}
    %\\\\　\\hspace{1em}\\phantom{\\myfirstauthorEN}\\hspace{1em}
    }

值里既有嵌套花括号，又有注释，注释里还有假的花括号。必须按 LaTeX 规则扫描。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


def strip_comment(line: str) -> str:
    """去掉一行里第一个未转义 % 之后的内容（保留 % 本身之前的部分）。"""
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            out.append(line[i : i + 2])
            i += 2
            continue
        if ch == "%":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def strip_comments(text: str) -> str:
    return "\n".join(strip_comment(ln) for ln in text.split("\n"))


def match_brace(text: str, start: int) -> int:
    """text[start] 必须是 '{'，返回配对 '}' 的下标。跳过注释与转义字符。"""
    if text[start] != "{":
        raise ValueError(f"位置 {start} 不是左花括号")
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":  # 转义：连同下一个字符一起跳过（含 \\{ \\} \\%）
            i += 2
            continue
        if ch == "%":  # 注释：跳到行尾
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("花括号未配平")


def skip_ws_comments(text: str, i: int) -> int:
    """从 i 起跳过空白与注释，返回下一个有效字符的下标。"""
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "%":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        else:
            break
    return i


@dataclass
class Macro:
    """一个 \\newcommand / \\renewcommand 定义。"""

    kind: str  # 'newcommand' | 'renewcommand'
    name: str  # 不含反斜杠，如 'mytitle'
    value: str  # 花括号内的原始内容（不含最外层括号）
    span: tuple[int, int]  # 整个定义的 [起, 止)
    value_span: tuple[int, int]  # 值的 [起, 止)，指向最外层括号之间
    commented: bool = False  # 是否被 % 注释掉
    comment_pos: int | None = None  # 该行 % 的位置

    @property
    def is_null(self) -> bool:
        return self.value.strip() in ("null", "")


_MACRO_RE = re.compile(
    r"\\(?P<kind>newcommand|renewcommand|providecommand)\s*"
    r"\{\s*\\(?P<name>[A-Za-z@]+)\s*\}"
)


def _comment_pos_on_line(text: str, pos: int) -> int | None:
    """返回 pos 所在行内、位于 pos 之前或等于 pos 的第一个未转义 % 的下标。"""
    line_start = text.rfind("\n", 0, pos) + 1
    i = line_start
    while i <= pos:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "%":
            return i
        i += 1
    return None


def parse_macros(text: str, include_commented: bool = False) -> dict[str, Macro]:
    """解析全文里的宏定义，返回 name -> 最后一次出现的 Macro。

    同名宏可能先 \\newcommand 后 \\renewcommand，后者覆盖前者（但两者都能由
    find_all_macros 取到）。被 % 注释掉的定义默认跳过。
    """
    out: dict[str, Macro] = {}
    for m in find_all_macros(text, include_commented=include_commented):
        out[m.name] = m  # 后出现的覆盖先出现的
    return out


def find_all_macros(text: str, include_commented: bool = False) -> list[Macro]:
    """按出现顺序返回所有宏定义。"""
    result: list[Macro] = []
    for m in _MACRO_RE.finditer(text):
        cpos = _comment_pos_on_line(text, m.start())
        commented = cpos is not None
        if commented and not include_commented:
            continue
        i = skip_ws_comments(text, m.end())
        # 允许 \newcommand{\x}[1][默认]{值} 这种带参数个数的写法
        while i < len(text) and text[i] == "[":
            j = text.find("]", i)
            if j < 0:
                break
            i = skip_ws_comments(text, j + 1)
        if i >= len(text) or text[i] != "{":
            continue
        end = match_brace(text, i)
        result.append(
            Macro(
                kind=m.group("kind"),
                name=m.group("name"),
                value=text[i + 1 : end],
                span=(m.start(), end + 1),
                value_span=(i + 1, end),
                commented=commented,
                comment_pos=cpos,
            )
        )
    return result


def _brace_safe(value: str) -> str:
    """确保值的末尾不会把收尾花括号注释掉。

    踩过的坑：从旧稿搬过来的字段值常常长这样——
        `% 说明文字\n{affiliation 2}\n%\\\u3000...%{another affiliation}`
    最后一行是**注释**。若直接拼上收尾 `}`，它就落在 `%` 之后被注释掉，
    于是整个 \newcommand 的括号失去配平，后续所有宏解析全崩。
    实测全库有 12 篇栽在这里。
    """
    last = value.rsplit("\n", 1)[-1]
    if strip_comment(last) != last:  # 末行有生效的注释
        return value + "\n"
    return value


def _strip_leading_comments(value: str) -> str:
    """剥掉值开头的纯注释行——那是旧模板写给作者的填写说明，搬到新稿里只会干扰阅读。"""
    lines = value.split("\n")
    i = 0
    while i < len(lines) - 1 and lines[i].lstrip().startswith("%"):
        i += 1
    return "\n".join(lines[i:])


def set_field(text: str, name: str, value: str) -> str:
    """按 SLCN 模板的惯例写入一个 \\my* 字段值。

    模板的写法是「\\newcommand{\\mygrants}{null}」+ 一行被注释掉的
    「%\\renewcommand{\\mygrants}{...}」。惯例是保留 null 那行不动，把
    \\renewcommand 取消注释并填值——这样既符合模板作者的意图，也让人一眼看出
    哪些字段被改过。所以本函数优先复用被注释的 \\renewcommand 行。
    """
    all_m = [m for m in find_all_macros(text, include_commented=True) if m.name == name]
    if not all_m:
        raise KeyError(f"模板中不存在字段 {name}")

    live = [m for m in all_m if not m.commented]
    dead_renew = [m for m in all_m if m.commented and m.kind == "renewcommand"]

    # 值为 null/空 → 保持模板原样（该字段不启用）
    if value.strip() in ("", "null"):
        return text

    value = _brace_safe(_strip_leading_comments(value))

    if dead_renew:
        # 取消注释被注释掉的 \renewcommand，并填入新值
        m = dead_renew[-1]
        assert m.comment_pos is not None
        head = text[: m.comment_pos]
        # 去掉 % 与其后的空格
        mid = text[m.comment_pos + 1 : m.span[0]]
        mid = mid.lstrip(" \t")
        tail = text[m.span[1] :]
        new_def = text[m.span[0] : m.value_span[0]] + value + text[m.value_span[1] : m.span[1]]
        return head + mid + new_def + tail

    if live:
        m = live[-1]
        return text[: m.value_span[0]] + value + text[m.value_span[1] :]

    raise KeyError(f"字段 {name} 只有被注释的定义且不是 \\renewcommand")


def collect_custom_macros(text: str, exclude_prefix: str = "my") -> list[tuple[str, str, str]]:
    """收集文中所有 \\newcommand 定义（排除 \\my* 模板字段）。

    返回 [(kind, name, 完整定义源码), ...]，用于把旧稿的自定义命令搬到新模板。
    正文依赖这些命令（如 \\val、\\e、\\A），漏搬会直接编译失败。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for m in find_all_macros(text, include_commented=False):
        if m.name.startswith(exclude_prefix) or m.name in seen:
            continue
        seen.add(m.name)
        out.append((m.kind, m.name, text[m.span[0] : m.span[1]]))
    return out


def get_macro_value(text: str, name: str) -> str | None:
    """取某个宏的最终值，找不到返回 None。"""
    macros = parse_macros(text)
    m = macros.get(name)
    return m.value if m else None


def replace_macro_value(text: str, name: str, new_value: str) -> str:
    """把某个宏的最终定义替换成新值，保留定义的位置与注释风格。

    只替换最后一次出现的那个定义（与 get_macro_value 的语义一致）。
    """
    macros = parse_macros(text)
    m = macros.get(name)
    if m is None:
        raise KeyError(f"未找到宏 {name}")
    return text[: m.value_span[0]] + new_value + text[m.value_span[1] :]


_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")


def _active_input_matches(text: str) -> list[re.Match]:
    """返回**没被注释掉**的 \\input/\\include 匹配（注释里的命令不生效）。"""
    out: list[re.Match] = []
    for m in _INPUT_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        i = line_start
        commented = False
        while i < m.start():
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "%":
                commented = True
                break
            i += 1
        if not commented:
            out.append(m)
    return out


def count_active_inputs(text: str) -> int:
    """可生效的 \\input/\\include 数量（注释掉的不算；第 120 条）。"""
    return len(_active_input_matches(text))


def expand_inputs(
    path: str, text: str | None = None, _depth: int = 0, _stats: dict | None = None
) -> str:
    """递归展开 \\input{...} / \\include{...}，把正文真正读进来。

    为什么必须有这个：实测全库 194 份成品 tex 里有 10 份把正文拆到了外部文件，
    而且好几份就叫 `copy_from_word_body.tex`（手工从 Word 转出来后另存的）。
    不展开的话，这些文章的「正文」只有一行 \\input，比对结果会假性跌到 6%。

    **被 `%` 注释掉的不展开**（第 120 条）：作者把旧附录注释掉是常态，展开它等于
    把本应排除的内容带进正文（合成稿实测会泄漏）。
    """
    if _stats is not None and _depth == 0:
        _stats.setdefault("expanded", 0)
    if _depth > 6:
        return text or ""
    if text is None:
        text = open(path, encoding="utf-8", errors="replace").read()
    base = os.path.dirname(os.path.abspath(path))
    matches = _active_input_matches(text)
    if not matches:
        return text

    parts: list[str] = []
    last = 0
    for m in matches:
        parts.append(text[last : m.start()])
        rel = m.group(1).strip()
        cand = rel if os.path.splitext(rel)[1] else rel + ".tex"
        target = os.path.join(base, cand)
        if os.path.exists(target):
            parts.append(expand_inputs(target, _depth=_depth + 1, _stats=_stats))
            if _stats is not None:
                _stats["expanded"] = _stats.get("expanded", 0) + 1
        else:
            parts.append(m.group(0))  # 找不到就原样保留，便于发现缺文件
        last = m.end()
    parts.append(text[last:])
    return "".join(parts)


def split_body(text: str) -> tuple[str, str]:
    key = "\\begin{document}"
    i = text.find(key)
    if i < 0:
        return text, ""
    return text[:i], text[i:]


TEMPLATE_BODY_MARKER = "正文内容从这里开始"
TEMPLATE_BIB_MARKER = "2.8 参考文献"

# 全库实测：本刊历史上有 4 代模板，节标记各不相同——
#   CN 现行：`% 2.7 正文内容从这里开始` … `% 2.8 参考文献`
#   EN 现行：`% 2.6 Body text`          … `% 2.7 References`
# 靠中文字符串定位必然在 EN 模板上全线失败（实测 14 篇）。所以改为按
# 「\printtitlepage 之后、\printbibliography 之前」这个**结构性**区间定位，
# 它与语言、模板代次都无关。
_BODY_START_ANCHOR = "\\printtitlepage"
_BODY_END_ANCHOR = "\\printbibliography"
_BIB_HEADING_WORDS = ("参考文献", "References", "参考书目")


def _is_heading_noise(line: str) -> bool:
    """判断一行是否属于『参考文献』小节的标题/分隔线/留白（可被替换掉）。"""
    s = line.strip()
    if s == "" or s.startswith("\\vspace"):
        return True
    if s.startswith("%"):
        return any(w in s for w in _BIB_HEADING_WORDS) or set(s) <= set("%- ")
    return False


def insert_body(template_text: str, body: str) -> str:
    """把正文插进模板的正文区，替换掉模板自带的示例章节。

    区间定义为「\\printtitlepage 之后」到「\\printbibliography 之前的参考文献小节
    标题」为止，与模板语种和代数无关。
    """
    lines = template_text.split("\n")

    # 取**第一个**后面还有 \printbibliography 的 \printtitlepage。
    # 不能取最后一个：模板末尾的「2.9 打印非原创文章作者信息」里还有个
    # \printtitlepageEN / \printtitlepageCN，它在 \printbibliography 之后。
    start = -1
    bib_i = -1
    for i, ln in enumerate(lines):
        if ln.strip().startswith("%"):
            continue
        if start < 0 and _BODY_START_ANCHOR in ln:
            start = i
        elif start >= 0 and _BODY_END_ANCHOR in ln:
            bib_i = i
            break
    if start < 0:
        raise ValueError("模板中未找到 \\printtitlepage 锚点")
    if bib_i < 0:
        raise ValueError("模板中未找到 \\printbibliography 锚点")

    end = bib_i
    while end - 1 > start and _is_heading_noise(lines[end - 1]):
        end -= 1

    # 保留正文区开头的节标题注释块（如 `%----%` + `% 2.7 正文内容从这里开始`），
    # 人工成品里是有这几行的，不该丢。
    header: list[str] = []
    for ln in lines[start + 1 : end]:
        s = ln.strip()
        if s == "" or s.startswith("%"):
            header.append(ln)
        else:
            break

    head = "\n".join(lines[: start + 1])
    if header:
        head += "\n" + "\n".join(header).rstrip("\n")
    tail = "\n".join(lines[end:])
    return head + "\n\n" + body.strip("\n") + "\n\n" + tail
