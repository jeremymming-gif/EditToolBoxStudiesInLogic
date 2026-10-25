r"""目录摘要推送：**数据采集**（期目录 tex → 纯 Python 数据模型）。

为什么要有这一层
----------------
公众号推送文案 = 期头 + 中英文目录 + 专栏/主持人 + 各篇摘要。这些信息分散在三处：

  1. `<期目录>/000 Table of Contents/Chinese Content.tex` —— 期次、专栏/主持人、中文目录条目
  2. `<期目录>/000 Table of Contents/contents.tex`        —— 英文目录（双语体例才有）
  3. 各篇成品 `.tex`                                      —— 中文标题/作者/单位/摘要

本模块**只读**（不写期目录、不写 `Engine/out/`），把三处合成一份纯 Python 数据模型，
供 `Engine/dirpush.py`（CLI）与 `slcn/pushcopy/render_docx.py`（渲染）共用。
判据一律单点在这里：渲染层不再解析 tex，CLI 不再猜字段。

硬性约束（本文件必须一直满足）
------------------------------
  · 只读：不写任何文件（settings 读取也只读）；不改期目录；
  · 不联网、不调模型；
  · 解析失败**必须报错**（`CollectError` → CLI 退出码 2），不许静默生成半成品；
  · 模板变体（`mytitleCN` 型 / 中文为主 `mytitle` 型）在 `_cn_meta()` 一处判定。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .. import infocheck as _ic
from .. import paths as _paths
from .. import pdfmerge as _pm
from .. import rules as _rules

#: 期目录名：`2026年第3期(88)` / `2022年第6A期(67A)`（年度扫描用，取不到 (总期) 也算）
ISSUE_DIR_RE = re.compile(r"^(\d{4})年第\s*(\d+)\s*期(?:[\(（]|$)")

#: 每期输出子目录名（实例体例：目录名带空格，文件名不带）
def issue_dir_name(year: int, number: int) -> str:
    return f"{year} 年第 {number} 期"


def annual_dir_name(year: int) -> str:
    return f"{year} 年"


#: 每期生成成功后写出的体例设置（确定性：不含时间戳）
SETTINGS_NAME = "目录摘要设置.json"
SETTINGS_SCHEMA = 1


class CollectError(RuntimeError):
    """输入/用法错误（CLI 映射为退出码 2，stderr 给中文原因）。"""


@dataclass
class TocEntry:
    """一条目录条目（页码已按实例体例丢弃）。"""

    title_lines: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)


@dataclass
class Column:
    """专栏/专辑：`label` 保留源里用词（专栏/专辑），`name` 栏名，`host` 主持人。"""

    label: str
    name: str
    host: str = ""


@dataclass
class Article:
    folder: str
    title_lines: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    warnings: list[str] = field(default_factory=list)
    #: 摘要原文是否来自 CN 字段（False = 回落英文/通用字段）
    abstract_is_cn: bool = True


@dataclass
class IssueData:
    dir: str
    year: int
    number: int
    vol: int
    total: int
    month: int
    day: int
    header_lines: list[str]
    with_en_toc: bool
    column: Column | None = None
    en_column: Column | None = None
    zh_entries: list[TocEntry] = field(default_factory=list)
    en_entries: list[TocEntry] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def abstracts_ok(self) -> int:
        return sum(1 for a in self.articles if a.abstract.strip())


# ── 通用 LaTeX 小工具（判据单点；渲染层不重复实现）────────────────────────


def _strip_comments(text: str) -> str:
    """去掉 LaTeX 注释（行内第一个**未转义** `%` 起）。"""
    out = []
    for line in text.splitlines():
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "%":
                line = line[:i]
                break
            i += 1
        out.append(line)
    return "\n".join(out)


def _match_brace(s: str, start: int) -> tuple[str, int]:
    """`s[start]` 必须是 `{`；返回 (内容, 右花括号下标)。转义花括号不算结构。"""
    depth = 0
    i = start
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i
        i += 1
    return s[start + 1:], len(s) - 1


_UNESCAPE = {"\\{": "\x00", "\\}": "\x01", "\\%": "\x02", "\\&": "\x03"}
_ESCAPE_BACK = {"\x00": "{", "\x01": "}", "\x02": "%", "\x03": "&"}


def _protect(s: str) -> str:
    for k, v in _UNESCAPE.items():
        s = s.replace(k, v)
    return s


def _restore(s: str) -> str:
    for k, v in _ESCAPE_BACK.items():
        s = s.replace(k, v)
    return s


#: 只影响版式、清洗时**去掉命令本身**（内容保留）
_FORMAT_CMDS = (
    "emph", "textbf", "textit", "textsl", "textsc", "textup", "textmd", "textnormal",
    "textrm", "textsf", "texttt", "text", "mathrm", "mathbf", "mathit", "mathsf",
    "mathtt", "kaishu", "songti", "heiti", "fangsong", "bfseries", "mdseries",
    "itshape", "upshape", "slshape", "scshape", "rmfamily", "sffamily", "ttfamily",
    "large", "Large", "LARGE", "huge", "Huge", "small", "footnotesize", "scriptsize",
    "tiny", "normalsize", "zihao", "bf", "it", "rm", "sf", "tt", "vspace", "hspace",
    "quad", "qquad", "par",
)

_FORMAT_RE = re.compile(r"\\(" + "|".join(_FORMAT_CMDS) + r")(?![A-Za-z])")

#: 带参数但**整段丢弃**的命令（`\hspace{1em}` 只影响间距，留下参数会污染文本）
_ARG_DROP_RE = re.compile(r"\\(?:hspace|vspace|phantom|hphantom|vphantom|protect)\*?\s*\{[^{}]*\}")

#: 成品里已展开成 Unicode 的本地命令（目录 tex 的 `\cndash` 等；不在 UNICODE_MATH 里）
_LOCAL_ALIASES = {"cndash": "-", "naraba": "→", "to": "→"}


def _smart_quotes(s: str) -> str:
    """LaTeX 引号/Word 智能引号 → 实例体例（`` → “、'' → ”、词内 ' → ’）。"""
    s = s.replace("``", "“").replace("''", "”").replace("`", "‘")
    s = re.sub(r"(?<=[A-Za-z])'(?=[A-Za-z])", "’", s)
    return s


_MATH_REV: dict[str, str] = {}
for _u, _cmd in _rules.UNICODE_MATH.items():
    _MATH_REV.setdefault(_cmd.lstrip("\\"), _u)

#: `\cmd` → 单个 Unicode 符号（仅 UNICODE_MATH 收录的；math 环境内用）
_MATH_REV.update({"rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒",
                  "Leftarrow": "⇐", "Leftrightarrow": "⇔", "longrightarrow": "⟶"})
_MATH_CMD_RE = re.compile(r"\\([A-Za-z]+)")


def _math_to_text(m: str) -> str:
    """数学模式内部文本化：命令 → Unicode（`slcn/rules.UNICODE_MATH` 反向表）。"""

    def rep(mo: re.Match) -> str:
        name = mo.group(1)
        return _MATH_REV.get(name, mo.group(0))

    m = _MATH_CMD_RE.sub(rep, m)
    m = re.sub(r"\\(?:mathbf|mathsf|mathrm|mathit|mathtt|text|textrm|operatorname)\s*", "", m)
    m = m.replace("\\,", " ").replace("\\;", " ").replace("\\!", "")
    for ch in "{}^_":
        m = m.replace(ch, "")
    return m


def _clean_inline(s: str, warnings: list[str] | None = None) -> str:
    r"""单行 LaTeX → 纯文本（标题/作者/单位/目录条目共用）。

    `\emph{X}` 等版式命令去命令留内容；`$...$` 文本化；`~`→空格；
    无法识别的 `\command` **保留原文**并记一条 warning（同名去重）。
    """
    s = _protect(s)
    for ch in ("\u00ad", "\u200b", "\u200c", "\u200d", "\ufeff"):
        s = s.replace(ch, "")
    # 目录/摘要里常见的本地命令别名（实例成品里已展开成符号）
    for _cmd, _sym in _LOCAL_ALIASES.items():
        s = s.replace("\\" + _cmd, _sym)
    s = _ARG_DROP_RE.sub("", s)
    # `$...$` 数学段文本化
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "$":
            j = i + 1
            while j < len(s) and s[j] != "$":
                j += 1
            out.append(_math_to_text(s[i + 1:j]))
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    s = "".join(out)
    s = s.replace("---", "—").replace("--", "–").replace("~/~", " ").replace("~", " ")
    s = _FORMAT_RE.sub("", s)
    s = s.replace("{", "").replace("}", "")
    s = _smart_quotes(s)
    s = re.sub(r"\s+", " ", s).strip()
    if warnings is not None:
        for mo in re.finditer(r"\\[A-Za-z]+", s):
            cmd = mo.group(0)
            if cmd in ("\\Letter", "\\\\"):
                continue
            if cmd not in warnings:
                warnings.append(cmd)
    return _restore(s)


def _strip_page_marks(s: str) -> str:
    r"""去掉目录 tex 里作者名后的 `$^\text{\Letter}$` 等标记。"""
    s = re.sub(r"\$\s*\^?\s*\\text\{\\Letter\}\s*\$", "", s)
    s = re.sub(r"\$\s*\^?\s*\\Letter\s*\$", "", s)
    s = s.replace("\\Letter", "")
    return s


def _clean_author(s: str) -> str:
    s = _strip_page_marks(s)
    s = _clean_inline(s)
    s = s.replace("~/~", " ")
    s = re.sub(r"\s+文\s*$", "", s)      # 目录作者后的「文」= 撰稿人标记（实例不收）
    s = re.sub(r"\s+译\s*$", "（译）", s)  # 「译」→ 实例体例「（译）」
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clean_title(s: str) -> str:
    s = _clean_inline(s)
    return s.strip()


# ── 文件 / 期目录定位 ───────────────────────────────────────────────────


def toc_files(issue_dir: str) -> tuple[str, str]:
    base = os.path.join(issue_dir, "000 Table of Contents")
    return os.path.join(base, "Chinese Content.tex"), os.path.join(base, "contents.tex")


def issue_name_parts(issue_dir: str) -> tuple[int, int] | None:
    m = ISSUE_DIR_RE.match(os.path.basename(os.path.normpath(issue_dir)))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_issue_header(text: str) -> dict:
    """从 `Chinese Content.tex` 的 flushright 头解析期次信息（失败抛 CollectError）。"""
    m = re.search(r"\\begin\{flushright\}(.*?)\\end\{flushright\}", text, re.S)
    if not m:
        raise CollectError("Chinese Content.tex 缺少 flushright 期头，无法解析期次信息")
    raw = _strip_comments(m.group(1))
    raw = raw.replace("\\\\", " ").replace("\\ ", " ")
    raw = _FORMAT_RE.sub("", raw)
    raw = raw.replace("{", " ").replace("}", " ").replace("~", " ")
    raw = re.sub(r"\s+", " ", raw)
    vol = re.search(r"第\s*(\d+)\s*卷", raw)
    total = re.search(r"总第\s*(\d+)\s*期", raw)
    issue = re.search(r"第\s*(\d+)\s*期", raw)
    year = re.search(r"(\d{4})\s*年", raw)
    date = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", raw)
    if not (vol and total and issue and year and date):
        raise CollectError(
            "Chinese Content.tex 期头解析失败（需要 卷/期/总期/年月日）：%s" % raw[:80])
    return {
        "vol": int(vol.group(1)),
        "total": int(total.group(1)),
        "number": int(issue.group(1)),
        "year": int(year.group(1)),
        "month": int(date.group(1)),
        "day": int(date.group(2)),
    }


def _header_lines(info: dict) -> list[str]:
    return [
        f"逻辑学研究 {info['year']} 年第 {info['number']} 期",
        (f"第 {info['vol']} 卷（总第 {info['total']} 期）"
         f"{info['year']} 年 {info['month']} 月 {info['day']} 日出版"),
        "双月刊 2008 年创刊",
    ]


# ── 目录 tex 解析 ───────────────────────────────────────────────────────


def _tabularx_body(text: str) -> str:
    text = _strip_comments(text)
    m = re.search(r"\\begin\{tabularx\}", text)
    if not m:
        return ""
    start = text.find("\n", m.end())
    end = text.find("\\end{tabularx}", m.end())
    if start < 0 or end < 0:
        return ""
    return _strip_comments(text[start + 1:end])


_SEP_RE = re.compile(r"\\vdistance|^\s*\\\\\s*\[[^\]\n]*\]\s*$", re.M)


def _blocks(body: str) -> list[str]:
    body = _SEP_RE.sub("\n@@SEP@@\n", body)
    return [b for b in (b.strip() for b in body.split("@@SEP@@")) if b]


def _group_cmd(s: str, cmd: str) -> list[str]:
    r"""找出 `\cmd{...}` 的所有一级组内容（花括号配平）。"""
    res: list[str] = []
    i = 0
    pat = re.compile(re.escape(cmd) + r"\s*\{")
    while True:
        m = pat.search(s, i)
        if not m:
            return res
        content, end = _match_brace(s, m.end() - 1)
        res.append(content)
        i = end + 1


def _split_top_groups(s: str) -> list[str]:
    """把 `{a}, {b}` 这种作者组拆成 ["a", "b"]；没有顶层组就整段当一个。

    注意：`\\large ~~~~匡\\hspace{1em}钊` 里的 `{1em}` 不是作者组 —— 只有
    「第一个组之前除版式命令/空白/逗号外没有正文」时才按组拆（2025 年第 1 期实测）。
    """
    groups: list[str] = []
    first_at = -1
    i = 0
    has_group = False
    while i < len(s):
        if s[i] == "{":
            if first_at < 0:
                first_at = i
            content, end = _match_brace(s, i)
            groups.append(content)
            has_group = True
            i = end + 1
        else:
            i += 1
    if not has_group:
        return [s]
    prefix = s[:first_at] if first_at >= 0 else ""
    prefix = _FORMAT_RE.sub("", prefix).replace("~", " ").strip(" ,，" + "\t")
    if prefix:
        return [s]
    # 顶层组之外还夹着正文（如 `{\bf A}, B`）时，把正文也算进最后一段
    rest = re.sub(r"\{[^{}]*\}", "", s).strip(" ,，")
    if rest and rest not in groups:
        groups.append(rest)
    return groups


def _parse_column_and_entries(body: str, want_en: bool) -> tuple[Column | None, list[TocEntry]]:
    """解析 tabularx 主体 → (专栏/主持人, 条目列表)。

    zh 与 en 两套 tex 的书写体例不同（中文条目标题在 `{\\large …}` 里、英文条目
    `\\large` 裸行），分别走 `_parse_zh_block` / `_parse_en_block`，但**都很薄**：
    共同的花括号/命令清洗都在上面。
    """
    blocks = _blocks(body)
    column: Column | None = None
    entries: list[TocEntry] = []
    for bi, block in enumerate(blocks):
        if want_en:
            col, ents = _parse_en_block(block)
        else:
            col, ents = _parse_zh_block(block)
        if col is not None and column is None:
            column = col
        entries.extend(ents)
        # 首块里的专栏行不产生条目；其余块若空属正常间隔
        _ = bi
    return column, entries


def _parse_zh_block(block: str) -> tuple[Column | None, list[TocEntry]]:
    column: Column | None = None
    # 专栏/专辑 行（可能在 `{\large \bf{专栏：X}}` 里，也可能裸写）
    m = re.search(r"(专栏|专辑)\s*[：:]\s*([^}＆&\n]*)", block)
    host = re.search(r"主持人\s*[：:]\s*([^}＆&\n]*)", block)
    if m:
        column = Column(label=m.group(1), name=_clean_inline(m.group(2)).strip(),
                        host=_clean_inline(host.group(1)).strip() if host else "")
    # 标题：`{\large …}` 组（跳过专栏/主持人与 \bf 组）
    # 作者行形如 `&\textit{\large …}`；标题行是 `{\large …}`（标题内可能嵌 `\textit{}`）
    author_spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\\textit\s*\{", block):
        _content, end = _match_brace(block, m.end() - 1)
        author_spans.append((m.start(), end + 1))
    titles: list[str] = []
    for raw in _braced_after_commands(block, ("\\large", "\\Large"),
                                      skip_spans=author_spans):
        text = _clean_title(raw)
        if not text or text.startswith("主持人") or re.match(r"^(专栏|专辑)\s*[：:]", text):
            continue
        titles.append(text)
    # 作者：`\textit{…}`（zh 模板里有时在 `&\textit{\large …}`）
    authors: list[str] = []
    for content in _group_cmd(block, "\\textit"):
        for frag in _split_top_groups(content):
            name = _clean_author(frag)
            if name:
                authors.append(name)
    if not titles and not authors:
        return column, []
    # 标题里 `\\` 分行（如 2026 年第 4 期“标题} \\ {——副题”）
    title_lines: list[str] = []
    for t in titles:
        for part in re.split(r"\\\\|\n", t):
            part = _clean_title(part)
            if part:
                title_lines.append(part)
    entry = TocEntry(title_lines=title_lines, authors=authors)
    return (column if column is not None else None), ([entry] if entry.title_lines else [])


def _braced_after_commands(block: str, commands: tuple[str, ...],
                           skip_spans: list[tuple[int, int]] | None = None) -> list[str]:
    r"""取 `{\large …}` 这类**外层组**里的内容。

    zh 模板的每个标题行是 `{\large ~~~~ 标题}` 或 `{\large \bf{专栏：X}}`。
    直接对 `{\large` 做花括号配平即可，不用管 `\large` 出现在别处。
    """
    res: list[str] = []
    for cmd in commands:
        i = 0
        pat = re.compile(r"\{" + re.escape(cmd) + r"(?![A-Za-z])")
        while True:
            m = pat.search(block, i)
            if not m:
                break
            content, end = _match_brace(block, m.start())
            if not any(a <= m.start() < b for a, b in (skip_spans or [])):
                res.append(content)
            i = end + 1
    return res


def _parse_en_block(block: str) -> tuple[Column | None, list[TocEntry]]:
    column: Column | None = None
    m = re.search(r"(?:Column|Special Issue)\s*[：:]\s*(.+)", block)
    if m:
        name = _clean_inline(m.group(1))
        name = re.sub(r"\s+", " ", name).strip().rstrip("\\").strip()
        if name:
            column = Column(label="Column", name=name, host="")
    titles: list[str] = []
    authors: list[str] = []
    for line in block.splitlines():
        raw = line.strip()
        if not raw:
            continue
        # 列名行 / 版式声明行
        if re.search(r"(?:Column|Special Issue|专栏|专辑)\s*[：:]", raw):
            continue
        if re.search(r"\\bf\s*\{", raw) and "\\textit" not in raw:
            continue
        if raw.startswith("&") or raw.startswith("\\["):
            continue
        if "\\textit" in raw:
            for content in _group_cmd(raw, "\\textit"):
                for frag in _split_top_groups(content):
                    name = _clean_author(frag)
                    if name:
                        authors.append(name)
            continue
        if raw.startswith("\\large") or raw.startswith("\\Large"):
            # 同一行可能塞了多个 `\large …` 段（2026 年第 4 期 contents.tex 实测）
            for seg in re.split(r"\\\\", raw):
                seg = re.sub(r"\\Large?", "", seg)
                text = _clean_title(seg)
                if text:
                    titles.append(text)
            continue
        # 续行（不以 \large 开头的标题续行）
        text = _clean_title(raw)
        if text:
            titles.append(text)
    entry = TocEntry(title_lines=titles, authors=authors) if titles else None
    return column, ([entry] if entry else [])


# ── 各篇成品 tex：CN 标题/作者/单位/摘要 ────────────────────────────────


def _macro_value(text: str, name: str) -> tuple[bool, str]:
    """取 `\\newcommand{\\name}` / `\\renewcommand{\\name}` 的最后一个定义值。

    返回 (是否存在, 值)。值可能跨行、可含嵌套花括号；定义行后的 `% 注释` 跳过。
    """
    pat = re.compile(
        r"\\(?:newcommand|renewcommand|providecommand)\s*\{\s*\\" + re.escape(name) + r"\s*\}")
    last = None
    for m in pat.finditer(text):
        last = m
    if last is None:
        return False, ""
    n = len(text)
    i = last.end()
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "%":                      # 跳到行尾（定义行后的中文注释）
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        break
    if i < n and text[i] == "[":           # 可选参数 [n]
        j = text.find("]", i)
        i = n if j < 0 else j + 1
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i < n and text[i] == "%":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            while i < n and text[i] in " \t\r\n":
                i += 1
    if i >= n or text[i] != "{":
        return True, ""
    content, _end = _match_brace(text, i)
    return True, content


def _is_null(value: str) -> bool:
    return value.strip().lower() in ("", "null")


#: 模板未填时的占位文本（2026 年第 3 期 `07 Hu_Liu` 实测；按「缺失」处理）
_PLACEHOLDERS = frozenset((
    "标题", "摘要", "第一作者", "作者姓名", "单位", "单位一", "单位二", "单位三",
    "单位四", "单位五", "第一作者单位", "作者一", "作者二", "作者一，作者二",
    "Title", "Author", "First Author", "Second Author", "Third Author",
    "Fourth Author", "Fifth Author", "Abstract",
))


def _split_multi(value: str) -> list[str]:
    r"""单位/多行值按 `\\` 拆开（`\\\small` 分隔多个单位）。"""
    parts = re.split(r"\\\\", value)
    out = []
    for p in parts:
        p = _clean_inline(p)
        if p:
            out.append(p)
    return out


def _has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


_AUTHORS = ("first", "second", "third", "fourth", "fifth")


def _cn_meta(text: str, folder: str, warnings: list[str], missing: list[str]) -> dict:
    """按模板变体取中文元数据（三类模板，判据只在这里）。

    1. `cn`      —— 中文模板：`\\mytitleCN` / `\\myabstractCN` / `\\my*authorCN`（2026 年第 3 期、2025 年第 3/6 期）
    2. `mix`     —— 中文为主模板：`\\mytitle` / `\\myabstract` 即中文，`\\mytitleEN` 才是英文（2026 年第 4/5 期、2025 年第 1/2/4/5 期）
    3. `fallback`—— 非中文模板且无 CN 字段：回落英文 + warning（契约 §2.5）
    """
    clean_text = _strip_comments(text)
    placeholder_hits: list[str] = []

    def val(name: str) -> tuple[bool, str]:
        ok, v = _macro_value(clean_text, name)
        if not ok or _is_null(v):
            return ok, ""
        if v.strip() in _PLACEHOLDERS:
            placeholder_hits.append(v.strip())
            return True, ""
        return True, v

    cn_defined = _macro_value(clean_text, "mytitleCN")[0]
    en_defined = any(_macro_value(clean_text, k)[0] for k in ("mytitleEN", "myabstractEN"))
    base_title = val("mytitle")[1]
    if cn_defined:
        source = "cn"
    elif en_defined or _has_cjk(base_title):
        source = "mix"
    else:
        source = "fallback"

    cn_title_key = "mytitleCN" if source == "cn" else "mytitle"
    cn_abs_key = "myabstractCN" if source == "cn" else "myabstract"

    title_lines: list[str] = []
    title_key = cn_title_key
    ok, tval = val(title_key)
    if not ok or not tval:
        # CN 字段缺失时回落英文（逐字段 warning）；目录条目不依赖这里
        ok2, tval2 = val("mytitle")
        if ok2 and tval2:
            if source == "cn":
                warnings.append(f"{folder}: 缺少中文字段 {title_key}，已回落英文 mytitle")
            title_key, tval = "mytitle", tval2
        else:
            missing.append(f"{folder}: {title_key}")
            tval = ""
    for part in re.split(r"\\\\|\n", tval):
        part = _clean_title(part)
        if part:
            title_lines.append(part)
    if not title_lines:
        missing.append(f"{folder}: {title_key}（空）")

    authors: list[str] = []
    affiliations: list[str] = []
    for i, word in enumerate(_AUTHORS, start=1):
        a_key = f"my{word}authorCN" if source == "cn" else f"my{word}author"
        ok, av = val(a_key)
        if not ok:
            # 该序位在模板里可能根本没定义（只定义到第 n 作者）→ null 处理
            continue
        if not av:
            if source == "cn":
                f_ok, f_av = val(f"my{word}author")
                if f_ok and f_av:
                    warnings.append(f"{folder}: 缺少中文字段 {a_key}，已回落英文")
                    av = f_av
                else:
                    continue
            else:
                continue
        cleaned = _clean_author(av)
        if cleaned:
            authors.append(cleaned)
        f_key = f"my{word}affiliationCN" if source == "cn" else f"my{word}affiliation"
        f_ok, fv = val(f_key)
        if not f_ok or not fv:
            if source == "cn":
                f_ok2, fv2 = val(f"my{word}affiliation")
                if f_ok2 and fv2:
                    warnings.append(f"{folder}: 缺少中文字段 {f_key}，已回落英文")
                    f_ok, fv = True, fv2
        if f_ok and fv:
            units = _split_multi(fv)
            affiliations.append("，".join(units))
        else:
            affiliations.append("")

    abstract = ""
    a_ok, a_val = val(cn_abs_key)
    abs_raw_defined, abs_raw_val = _macro_value(clean_text, cn_abs_key)
    if abs_raw_defined and abs_raw_val.strip() in _PLACEHOLDERS:
        # 中文摘要是模板占位符 → 按缺失处理，**不回落英文摘要**（中文推送不出现整段英文）
        placeholder_hits.append(abs_raw_val.strip())
        missing.append(f"{folder}: {cn_abs_key}（占位符）")
        a_ok, a_val = True, ""
    elif not a_ok or not a_val:
        ok2, a_val2 = val("myabstract")
        if ok2 and a_val2:
            if source == "cn":
                warnings.append(
                    f"{folder}: 缺少中文字段 {cn_abs_key}，已回落英文 myabstract")
            a_val = a_val2
        else:
            missing.append(f"{folder}: {cn_abs_key}")
            a_val = ""
    if a_val:
        abstract = _clean_abstract(a_val, folder, warnings, missing, _local_macros(clean_text))

    if placeholder_hits:
        uniq = sorted(set(placeholder_hits))
        warnings.append(
            f"{folder}: 成品 tex 里仍是模板占位符（{'、'.join(uniq)}），按缺失处理")
    if source == "fallback":
        warnings.append(f"{folder}: 模板无中文字段，中文信息回落英文/通用字段")
    return {
        "title_lines": title_lines,
        "authors": authors,
        "affiliations": affiliations,
        "abstract": abstract,
        "source": source,
    }


_CITE_RE = re.compile(
    r"[（(]\s*\\(?:parencite|cite|citep|citet|textcite|autocite)\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*[）)]")
_CITE2_RE = re.compile(r"\\(?:parencite|cite|citep|citet|textcite|autocite)\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}")
_UNKNOWN_CMD_RE = re.compile(r"\\([A-Za-z]+)")


def _local_macros(text: str) -> dict[str, str]:
    """收集文章自己 `\\newcommand{\\x}{...}` 的**无参**简单宏（摘要清洗用）。

    例：2026 年第 3 期 `05 YouAn_Su` 的 `\\naraba` = `\\rightarrow`。
    """
    out: dict[str, str] = {}
    pat = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\s*\{\s*\\([A-Za-z]+)\s*\}")
    for m in pat.finditer(text):
        i = m.end()
        n = len(text)
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i < n and text[i] == "%":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            while i < n and text[i] in " \t\r\n":
                i += 1
        if i < n and text[i] == "[":
            continue
        if i >= n or text[i] != "{":
            continue
        val, _end = _match_brace(text, i)
        if "#" in val or len(val) > 80:
            continue
        out.setdefault(m.group(1), val)
    return out


def _clean_abstract(raw: str, folder: str, warnings: list[str], missing: list[str],
                    macros: dict[str, str] | None = None) -> str:
    """摘要 LaTeX → 纯文本（契约 §2.6 的口径；`摘要：` 前缀由渲染层加）。"""
    s = _strip_comments(raw)
    # 本地别名（实例成品里已展开成符号）
    for _cmd, _sym in _LOCAL_ALIASES.items():
        s = s.replace("\\" + _cmd, _sym)
    if macros:
        for _ in range(3):
            before = s
            for name, val in macros.items():
                s = re.sub(r"\\" + re.escape(name) + r"(?![A-Za-z])",
                           lambda _m, _v=val: _v, s)
            if s == before:
                break
    s = _protect(s)
    s = _CITE_RE.sub("", s)
    s = _CITE2_RE.sub("", s)
    # 数学段文本化
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "$":
            j = i + 1
            while j < len(s) and s[j] != "$":
                j += 1
            out.append(_math_to_text(s[i + 1:j]))
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    s = "".join(out)
    s = s.replace("\\\\", " ").replace("\\ ", " ")
    s = s.replace("~", " ")
    s = s.replace("---", "—").replace("--", "–")
    s = _strip_page_marks(s)
    s = _FORMAT_RE.sub("", s)
    s = s.replace("{", "").replace("}", "")
    # 无法映射的 `\command`：保留原文 + 逐命令去重 warning
    for mo in _UNKNOWN_CMD_RE.finditer(s):
        cmd = mo.group(0)
        msg = f"{folder}: 摘要含无法映射的命令 {cmd}（保留原文）"
        if msg not in warnings:
            warnings.append(msg)
    s = re.sub(r"\s+", " ", s).strip()
    return _restore(s)


# ── 期设置（体例）与输出路径 ────────────────────────────────────────────


def default_out_root() -> str:
    """本机默认输出根（spec §2.5；mac 外壳同理）。"""
    return os.path.join(os.path.expanduser("~"), "Documents", "sysu", "编辑部", "目录摘要推送")


def issue_output_dir(out_root: str, year: int, number: int) -> str:
    return os.path.join(out_root, issue_dir_name(year, number))


def issue_output_paths(out_root: str, year: int, number: int) -> tuple[str, str, str]:
    d = issue_output_dir(out_root, year, number)
    return (d,
            os.path.join(d, f"逻辑学研究{year}年第{number}期目录摘要.docx"),
            os.path.join(d, SETTINGS_NAME))


def annual_output_path(out_root: str, year: int) -> str:
    return os.path.join(out_root, annual_dir_name(year),
                        f"逻辑学研究{year}年总目录.docx")


def settings_paths(out_root: str, year: int, number: int, issue_dir: str) -> list[str]:
    """体例设置候选路径（先输出目录，后期目录；契约 §3）。"""
    return [os.path.join(issue_output_dir(out_root, year, number), SETTINGS_NAME),
            os.path.join(issue_dir, SETTINGS_NAME)]


def read_issue_settings(out_root: str, year: int, number: int,
                        issue_dir: str) -> dict | None:
    for p in settings_paths(out_root, year, number, issue_dir):
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("with_en_toc"), bool):
            return data
    return None


def infer_with_en_toc(out_root: str, year: int, number: int, issue_dir: str) -> bool:
    data = read_issue_settings(out_root, year, number, issue_dir)
    return bool(data["with_en_toc"]) if data else False


def scan_year_issues(parent: str, year: int) -> list[tuple[int, str]]:
    """扫描父目录下 `<year>年第N期*` 期目录，按 N 排序。"""
    found: dict[int, str] = {}
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return []
    for name in names:
        m = ISSUE_DIR_RE.match(name)
        if not m or int(m.group(1)) != year:
            continue
        full = os.path.join(parent, name)
        if not os.path.isdir(full):
            continue
        n = int(m.group(2))
        if n not in found:
            found[n] = full
    return sorted(found.items())


# ── 主采集入口 ─────────────────────────────────────────────────────────


def collect_issue(issue_dir: str, *, with_en_toc: bool | None = None,
                  out_root: str | None = None) -> IssueData:
    """采集一期（只读）。`with_en_toc=None` 时按输出目录/期目录的 settings 推断。"""
    issue_dir = os.path.abspath(issue_dir)
    if not os.path.isdir(issue_dir):
        raise CollectError(f"期目录不存在：{issue_dir}")
    zh_tex, en_tex = toc_files(issue_dir)
    if not os.path.isfile(zh_tex):
        raise CollectError(
            "本期目录缺少 `000 Table of Contents/Chinese Content.tex`。"
            "请先在本期目录生成中英文目录（000 Table of Contents）。")
    with open(zh_tex, encoding="utf-8", errors="replace") as fh:
        zh_text = fh.read()

    info = parse_issue_header(zh_text)
    name_parts = issue_name_parts(issue_dir)
    if name_parts and (name_parts[0], name_parts[1]) != (info["year"], info["number"]):
        # 期目录名与 tex 期头不一致 → 只 warning（编辑偶尔复制目录后忘改）
        info.setdefault("warnings", [])
    warnings: list[str] = []
    missing: list[str] = []
    if name_parts and (name_parts[0], name_parts[1]) != (info["year"], info["number"]):
        warnings.append(
            f"期目录名与期头不一致：目录={name_parts[0]}年第{name_parts[1]}期，"
            f"tex 期头={info['year']}年第{info['number']}期（以 tex 为准）")

    if with_en_toc is None:
        with_en_toc = infer_with_en_toc(out_root or default_out_root(),
                                        info["year"], info["number"], issue_dir)

    # 中文目录（专栏 + 条目）
    zh_body = _tabularx_body(zh_text)
    if not zh_body:
        raise CollectError("Chinese Content.tex 缺少 tabularx 目录主体，无法解析目录条目")
    column, zh_entries = _parse_column_and_entries(zh_body, want_en=False)

    # 英文目录（contents.tex；双语体例才有）
    en_entries: list[TocEntry] = []
    en_column: Column | None = None
    if os.path.isfile(en_tex):
        with open(en_tex, encoding="utf-8", errors="replace") as fh:
            en_text = fh.read()
        en_body = _tabularx_body(en_text)
        if not en_body:
            if with_en_toc:
                raise CollectError("contents.tex 缺少 tabularx 目录主体，无法生成中英文对照目录")
            warnings.append("contents.tex 缺少 tabularx 主体，英文目录为空")
        else:
            en_column, en_entries = _parse_column_and_entries(en_body, want_en=True)
    elif with_en_toc:
        raise CollectError(
            "本期目录缺少 `000 Table of Contents/contents.tex`，无法生成中英文对照目录。"
            "请先在本期目录生成中英文目录（000 Table of Contents）。")
    else:
        warnings.append("缺少 contents.tex，仅生成中文目录")

    # 文章清单（与「信息检查」同一口径；write=False 只算不写盘）
    try:
        report = _ic.process_folders(issue_dir, write=False)
        report_folders = [a.folder for a in report.articles]
    except Exception:  # noqa: BLE001  信息检查失败不该拖垮文案生成
        report_folders = []
        warnings.append("信息检查（infocheck）执行失败，按期目录自然序取文章")
    folders = [f for f in _pm.numbered_entries(issue_dir) if not f.startswith("000")]
    if report_folders and report_folders != folders:
        warnings.append("信息检查文章序与目录自然序不一致，按目录自然序生成")
    articles: list[Article] = []
    for folder in folders:
        folder_path = os.path.join(issue_dir, folder)
        name = _pm.article_name(folder)
        tex_file = _pm.find_tex(folder_path, name)
        art = Article(folder=folder)
        if not tex_file:
            art.warnings.append(f"{folder}: 找不到成品 tex，摘要块留空")
            missing.append(f"{folder}: tex")
            articles.append(art)
            continue
        with open(tex_file, encoding="utf-8", errors="replace") as fh:
            tex = fh.read()
        meta = _cn_meta(tex, folder, art.warnings, missing)
        art.title_lines = meta["title_lines"]
        art.authors = meta["authors"]
        art.affiliations = meta["affiliations"]
        art.abstract = meta["abstract"]
        art.abstract_is_cn = meta["source"] != "fallback"
        articles.append(art)

    # 映射校验（不静默错配；只 warning，仍按顺序生成）
    if len(zh_entries) != len(articles):
        warnings.append(
            f"目录条目数（中文 {len(zh_entries)}）与文章数（{len(articles)}）不一致")
    if with_en_toc and en_entries and len(en_entries) != len(articles):
        warnings.append(
            f"目录条目数（英文 {len(en_entries)}）与文章数（{len(articles)}）不一致")
    for i, (entry, art) in enumerate(zip(zh_entries, articles), start=1):
        if not art.title_lines:
            continue
        a = _norm_compare("".join(entry.title_lines))
        b = _norm_compare("".join(art.title_lines))
        if a != b:
            warnings.append(
                f"第 {i} 条目录标题与 {art.folder} 的 mytitle 不一致（以目录为准）")
        authors_a = _norm_compare("".join(entry.authors))
        authors_b = _norm_compare("".join(art.authors))
        if authors_a and authors_b and authors_a != authors_b:
            warnings.append(
                f"第 {i} 条目录作者与 {art.folder} 的作者不一致（以目录为准）")

    if not zh_entries:
        raise CollectError("中文目录条目为空（tabularx 主体解析不到条目）")

    return IssueData(
        dir=issue_dir,
        year=info["year"], number=info["number"], vol=info["vol"], total=info["total"],
        month=info["month"], day=info["day"],
        header_lines=_header_lines(info),
        with_en_toc=bool(with_en_toc),
        column=column,
        en_column=en_column,
        zh_entries=zh_entries,
        en_entries=en_entries,
        articles=articles,
        warnings=warnings,
        missing=missing,
    )


def _norm_compare(s: str) -> str:
    """内容一致性比较的规范化：去空白与常见标点（不改变文字本身）。"""
    s = re.sub(r"[\s，,。．\.、；;：:！!？\?（）\(\)“”\"'‘’《》〈〉\[\]【】—\-–~～·]+", "", s)
    return s


@dataclass
class AnnualPlan:
    year: int
    included: list[tuple[int, str]] = field(default_factory=list)
    excluded: list[int] = field(default_factory=list)
    missing_issues: list[int] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)   # {number, with_en_toc, status}
    data: list[IssueData] = field(default_factory=list)


def collect_annual(issue_dir: str, *, year: int | None = None,
                   include_issues: list[int] | None = None,
                   out_root: str | None = None) -> AnnualPlan:
    """年度采集：扫描 `--issue` 父目录下同年的各期，收集“期头 + 目录”。"""
    issue_dir = os.path.abspath(issue_dir)
    if not os.path.isdir(issue_dir):
        raise CollectError(f"期目录不存在：{issue_dir}")
    parts = issue_name_parts(issue_dir)
    parent = os.path.dirname(issue_dir)
    if year is None:
        year = parts[0] if parts else None
    if year is None:
        raise CollectError(f"无法从目录名识别年份：{os.path.basename(issue_dir)}（请用 --year）")
    found = scan_year_issues(parent, year)
    if not found:
        raise CollectError(f"{parent} 下找不到 {year} 年的任何期目录（<{year}年第N期*>）")

    out_root = out_root or default_out_root()
    found_nums = [n for n, _ in found]
    selected = set(include_issues) if include_issues else set(found_nums)
    unknown = sorted(selected - set(found_nums))
    plan = AnnualPlan(year=year)
    plan.missing_issues = [n for n in range(1, 7) if n not in found_nums]
    for n, d in found:
        zh_tex, _ = toc_files(d)
        status = "ok" if os.path.isfile(zh_tex) else "no_toc"
        plan.issues.append({"number": n, "with_en_toc": False, "status": status})
    for n, d in found:
        if n not in selected:
            plan.excluded.append(n)
            continue
        if not os.path.isfile(toc_files(d)[0]):
            plan.excluded.append(n)
            continue
        with_en = infer_with_en_toc(out_root, year, n, d)
        for rec in plan.issues:
            if rec["number"] == n:
                rec["with_en_toc"] = with_en
        data = collect_issue(d, with_en_toc=with_en, out_root=out_root)
        plan.included.append((n, d))
        plan.data.append(data)
    if not plan.data:
        raise CollectError(f"{year} 年没有可用的期目录（0 期可生成）")
    if unknown:
        plan.missing_issues.extend([])  # 未知期号不伪造缺期；调用方按 include 说明
    return plan
