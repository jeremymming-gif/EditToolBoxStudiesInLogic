r"""「生成目录」：从中英文检查报告生成中英文目录 .tex（第 94 条，从 mac 的 `ContentFeature.swift` 搬进引擎）。

上游是原版 `charti_build_content.py`，mac 那份做了三处**结构性修正**（这里一并继承）：

  1. **模板继承上一期成品**（不再用写死的 2022 年模板），卷号/期号/年份/总期号/出版日期自动填
     —— 模板处理在 `slcn/toctemplate.py`；
  2. **修掉副标题排版 bug**：原版把副标题排在页码行之后（多一行且错位），
     现在按成品写法放在同一单元格内用 `\\` 换行；
  3. 两字姓名不再插 `\hspace{1em}`；最后一篇之后不再多一个 `\vdistance`。

**输入**：`信息检查<时间戳>.txt`（`infocheck.py` 的产物）。
**输出**：报告同目录下的 `Chinese Content_<时间戳>.tex` 与 `contents_<时间戳>.tex`。
**顺序**：先把英文写完再写中文 —— 与原版 `main.py` 的 `run_tab5` 一致。

本期设置（栏目、模板来源、标题断行上限）读期目录下的 `目录设置.json`，见 `TocSettings`。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass, field

from . import infocheck as _ic          # 复用 extract_title / get_line_num（与 Swift 侧是同一份逻辑）
from . import toctemplate as _tt

#: 目录条目的换行写法（LaTeX 里是 `\\` 两字符）
_LINEBREAK = "\\\\"


@dataclass
class TocEntry:
    """从检查报告里解析出的一篇文章。字段名对应 Swift `TocEntry`。"""

    folderName: str
    page: str = ""
    chineseTitle: str = ""
    englishTitle: str = ""
    chineseAuthors: list[str] = field(default_factory=list)
    englishAuthors: list[str] = field(default_factory=list)
    titleParseFailed: bool = False


def clean_name(name: str) -> str:
    """只保留 CJK 统一表意文字（U+4E00–U+9FFF）。

    ⚠️ 注意与 `infocheck.no_chinese` 的范围**不同**：那边是 9FA5（复刻上游），
    这里是 9FFF（mac 的 `cleanName` 就是这么写的）。刻意各自保留，别"顺手统一"。
    """
    return "".join(c for c in name if 0x4E00 <= ord(c) <= 0x9FFF)


def python_readlines(content: str) -> list[str]:
    """等价于 Python 的 `open(...).readlines()`：通用换行归一后按 `\\n` 切，**保留行尾换行符**。"""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    current = ""
    for ch in normalized:
        current += ch
        if ch == "\n":
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return lines


def parse_issue_info(lines: list[str]) -> tuple[str, str, str]:
    """从报告里取本期的卷号/期号/年份。

    报告里这三项写在同一行：`卷号：19  期号：5  年份：2026`，
    所以不能直接用 `get_line_num`（它会把整行数字连成一串），必须按字段拆。
    """
    for line in lines:
        if "卷号：" in line:
            volume = issue = year = ""
            for fld in line.split():
                if fld.startswith("卷号："):
                    volume = "".join(c for c in fld[3:] if c.isdigit())
                elif fld.startswith("期号："):
                    issue = "".join(c for c in fld[3:] if c.isdigit())
                elif fld.startswith("年份："):
                    year = "".join(c for c in fld[3:] if c.isdigit())
            return volume, issue, year
    return "", "", ""


def split_articles(lines: list[str]) -> list[tuple[str, list[str]]]:
    """按「起始页码」行把报告切成一篇篇，返回 [(文件夹名, 该篇的行), …]。

    每篇的块结构是：文件夹名 / 卷期年 / 起始页码…，所以文件夹名在「起始页码」行**往前数第 2 行**。
    （原版用 end_idx 收尾会重复带上下一篇的一行，这里按块边界精确切分。）
    """
    starts = [i for i, ln in enumerate(lines) if "起始页码" in ln]
    result: list[tuple[str, list[str]]] = []
    for idx, start in enumerate(starts):
        block_start = max(0, start - 2)
        block_end = max(block_start, starts[idx + 1] - 2) if idx + 1 < len(starts) else len(lines)
        if block_start >= block_end:
            continue
        folder = lines[start - 2].strip() if start - 2 >= 0 else ""
        result.append((folder, lines[block_start:block_end]))
    return result


def clean_title(raw: str | None, keep_braces: bool) -> str:
    r"""标题清洗：去掉残留的 LaTeX 命令。

    原版只去掉 `\\`、`\Large`、`\large`，`\zihao{4}` 这类会留在标题里 ——
    成品里真的出现过 `\large \zihao{4}Reasoning Disposition...`。
    """
    if raw is None:
        return ""
    t = raw
    for token in ("\\\\", "\\Large", "\\large"):
        t = t.replace(token, "")
    t = re.sub(r"\\[a-zA-Z]+\s*\{[^}]*\}", "", t)
    if not keep_braces:
        t = t.replace("{", "").replace("}", "")
    return t.strip()


def parse_entry(folder_name: str, block: list[str]) -> TocEntry:
    """从一篇报告块里解析出目录条目。"""
    entry = TocEntry(folderName=folder_name)
    for line in block:
        if "起始页码：" in line:
            first = (line.split() or [""])[0]
            try:
                entry.page = str(int(first[5:].strip()))
            except ValueError:
                pass
        elif "中文标题:" in line:
            raw = line.split("中文标题:")[-1].strip()
            if raw == "None":
                entry.titleParseFailed = True
            else:
                entry.chineseTitle = raw
        elif "英文标题" in line:
            raw = line.split("英文标题:")[-1].strip()
            if raw == "None":
                entry.titleParseFailed = True
            else:
                entry.englishTitle = raw
        elif "第一作者英文:" in line:
            entry.englishAuthors.append(line[7:].strip())
        elif "第一作者中文:" in line:
            entry.chineseAuthors.append(clean_name(line[7:].strip()))
        elif "第二作者英文:" in line:
            entry.englishAuthors.append(line[7:].strip())
        elif "第二作者中文:" in line:
            entry.chineseAuthors.append(clean_name(line[7:].strip()))
        elif "第三作者英文:" in line:
            entry.englishAuthors.append(line[7:].strip())
        elif "第三作者中文:" in line:
            entry.chineseAuthors.append(clean_name(line[7:].strip()))

    entry.chineseTitle = clean_title(entry.chineseTitle, keep_braces=False)
    entry.englishTitle = clean_title(entry.englishTitle, keep_braces=True)
    entry.chineseAuthors = [a for a in entry.chineseAuthors if a]
    entry.englishAuthors = [a for a in entry.englishAuthors if a]
    return entry


# ── 标题断行 ─────────────────────────────────────────────────────────────

def display_width(s: str) -> float:
    """估算显示宽度：汉字与全角标点算 1，ASCII 字母数字算 0.5，空格算 0.25。"""
    width = 0.0
    for ch in s:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF:
            width += 1.0
        elif cp == 0x20:
            width += 0.25
        else:
            width += 0.5
    return width


def wrap_title(title: str, limit: float, is_english: bool) -> tuple[str, str] | None:
    r"""把过长的标题断成两行（返回 None 表示不用断）。

    为什么需要：目录第一列是内容自适应的 `l` 列，只要有一篇标题太长，整张表就会被撑宽，
    页码列被推出版心（看起来就是右侧溢出）。同事在成品里也是手工断行的。

    断点选择：中文优先在标点处断，否则在不超过上限的位置尽量靠右；英文在最后一个空格处断。
    两侧各至少留 3 个字，避免孤字行；两边太不均衡（<35% 或 >65%）时改用居中断。
    """
    if limit <= 0 or display_width(title) <= limit:
        return None
    chars = list(title)
    if len(chars) < 8:
        return None

    min_side = 3
    best: int | None = None

    if is_english:
        for i in range(len(chars) - min_side, min_side - 1, -1):
            if chars[i] == " " and display_width("".join(chars[:i])) <= limit:
                best = i
                break
    else:
        punctuation = set("：，、；。！？")
        for i in range(len(chars) - min_side, min_side - 1, -1):
            if chars[i - 1] not in punctuation:
                continue
            # 标点太靠前会造成「第一行很短、第二行很长」的难看版式，这种情况不用它
            if i < len(chars) * 0.4:
                continue
            if display_width("".join(chars[:i])) <= limit:
                best = i
                break
        if best is None:
            for i in range(len(chars) - min_side, min_side - 1, -1):
                if display_width("".join(chars[:i])) <= limit:
                    best = i
                    break
        if best is not None:
            first_ratio = best / len(chars)
            if first_ratio < 0.35 or first_ratio > 0.65:
                middle = len(chars) // 2
                if middle >= min_side and len(chars) - middle >= min_side:
                    best = middle

    if best is None or best < min_side or len(chars) - best < min_side:
        return None
    head = "".join(chars[:best]).strip()
    tail = "".join(chars[best:]).strip()
    if not head or not tail:
        return None
    return head, tail


# ── 生成条目 ─────────────────────────────────────────────────────────────

def chinese_entry(e: TocEntry, in_column: bool, wrap_limit: float) -> str:
    """中文目录的一条。"""
    prefix = "~~~~ " if in_column else ""
    title_lines = [e.chineseTitle]

    if "——" in e.chineseTitle:
        parts = e.chineseTitle.split("——")
        title_lines = [parts[0].strip(),
                       "——" + "——".join(parts[1:]).strip()]
    else:
        wrapped = wrap_title(e.chineseTitle,
                             # 上限里要扣掉 `~~~~ ` 缩进前缀的宽度，否则量的是标题、
                             # 渲染出来却多了前缀，实际会超
                             limit=wrap_limit - display_width(prefix),
                             is_english=False)
        if wrapped:
            title_lines = [wrapped[0], wrapped[1]]

    authors = "&\\textit{\\large " + prefix + "，".join(e.chineseAuthors) + "}"

    if len(title_lines) == 2:
        # 第二行在同一个单元格内换行 —— 这是成品的写法
        return ("{\\large " + prefix + title_lines[0] + "}\n"
                + _LINEBREAK + "{\\large " + prefix + title_lines[1] + "}\n"
                + authors + "\n& " + e.page + _LINEBREAK)
    return ("{\\large " + prefix + title_lines[0] + "}\n"
            + authors + "\n& " + e.page + _LINEBREAK)


def english_entry(e: TocEntry, in_column: bool, wrap_limit: float) -> str:
    """英文目录的一条（成品里只有**第一位**作者带 ` ~~~~ ` 前缀）。"""
    authors = ", ".join(
        "{" + (" ~~~~ " if (i == 0 and in_column) else "") + name + "}"
        for i, name in enumerate(e.englishAuthors))
    prefix = "~~~~ " if in_column else ""

    wrapped = wrap_title(e.englishTitle,
                         limit=wrap_limit - display_width(prefix),
                         is_english=True)
    if wrapped:
        return ("\\large " + prefix + wrapped[0] + _LINEBREAK + "\n"
                + "\\large " + prefix + wrapped[1] + _LINEBREAK + "\n"
                + "\\textit{" + authors + "}\n& " + e.page + _LINEBREAK)
    return ("\\large " + prefix + e.englishTitle + _LINEBREAK + "\n"
            + "\\textit{" + authors + "}\n& " + e.page + _LINEBREAK)


# ── 本期设置（目录设置.json）─────────────────────────────────────────────

@dataclass
class TocSettings:
    """「生成目录」的本期设置，保存在期目录下的 `目录设置.json`。

    为什么栏目设置放在「生成目录」而不是「检查tex信息」：「检查tex信息」是**校对**工具
    （核对 tex 里填的信息与 PDF 页数是否相符），给它加"是不是专栏文章"的勾选会让职责混乱；
    栏目信息是"这一期怎么排目录"的决定，属于「生成目录」的输入。
    """

    columnKind: str = ""
    columnName: str = ""
    columnNameEN: str = ""
    columnHost: str = ""
    columnArticles: list[str] = field(default_factory=list)
    templateFolder: str = ""
    titleWrapLimit: float = 20
    titleWrapLimitEN: float = 35

    fileName = "目录设置.json"

    @property
    def is_column_enabled(self) -> bool:
        return bool(self.columnKind) and bool(self.columnName)

    @property
    def column_editing(self) -> bool:
        """界面是否展示栏目控件（不能用 is_column_enabled：那要求名称非空，
        会导致「选了专栏但还没填名称时输入框被隐藏」，从而永远填不上名称）。"""
        return bool(self.columnKind)

    def contains(self, report_article_name: str) -> bool:
        """某篇文章是否属于栏目。报告里的名字是 `folder_name`（如 `Jingru_Kou`），
        设置里可能存带序号的（如 `01 Jingru_Kou`），所以两边都兼容。"""
        if not self.is_column_enabled:
            return False
        target = report_article_name.strip()
        if not target:
            return False
        for stored in self.columnArticles:
            s = stored.strip()
            if s == target:
                return True
            tail = s.split()[-1] if s.split() else s
            if tail == target:
                return True
        return False

    @property
    def chinese_column_title(self) -> str:
        return f"{self.columnKind}：{self.columnName}"

    @property
    def english_column_title(self) -> str:
        name = self.columnNameEN or self.columnName
        return f"Column: {name}"

    @classmethod
    def load(cls, folder: str) -> "TocSettings":
        """读期目录下的 `目录设置.json`。**每个字段都允许缺失** ——
        Swift 合成的 Codable 遇到缺键会整份失败、属性默认值不生效，所以那边自己实现了
        逐字段 decodeIfPresent（加 titleWrapLimit 时踩过：旧文件整份读取失败，
        栏目名、主持人、勾选的文章全被清空）。这里同样逐字段取值。"""
        path = os.path.join(folder, cls.fileName)
        if not os.path.isfile(path):
            return cls()
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(
            columnKind=str(data.get("columnKind", "")),
            columnName=str(data.get("columnName", "")),
            columnNameEN=str(data.get("columnNameEN", "")),
            columnHost=str(data.get("columnHost", "")),
            columnArticles=[str(x) for x in data.get("columnArticles", []) or []],
            templateFolder=str(data.get("templateFolder", "")),
            titleWrapLimit=float(data.get("titleWrapLimit", 20)),
            titleWrapLimitEN=float(data.get("titleWrapLimitEN", 35)),
        )

    def save(self, folder: str) -> str:
        path = os.path.join(folder, self.fileName)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "columnKind": self.columnKind,
                "columnName": self.columnName,
                "columnNameEN": self.columnNameEN,
                "columnHost": self.columnHost,
                "columnArticles": self.columnArticles,
                "templateFolder": self.templateFolder,
                "titleWrapLimit": self.titleWrapLimit,
                "titleWrapLimitEN": self.titleWrapLimitEN,
            }, fh, ensure_ascii=False, indent=2)
        return path


# ── 生成文件 ─────────────────────────────────────────────────────────────

def generate_tex(report_path: str, flag: str, settings: TocSettings,
                 log=lambda _s: None, stamp: str | None = None) -> str | None:
    """生成一份目录 .tex。`flag` 取 `en` 或 `ch`。返回产物路径（失败返回 None）。"""
    is_chinese = flag == "ch"
    label = "中文" if is_chinese else "英文"
    base_name = "Chinese Content" if is_chinese else "contents"
    date_str = stamp or _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    report_dir = os.path.dirname(os.path.abspath(report_path))
    log(f"开始生成{label}目录")

    try:
        with open(report_path, encoding="utf-8", errors="replace") as fh:
            lines = python_readlines(fh.read())
    except OSError as e:
        log(f"{e}")
        log(f"{label}目录TeX文件生成失败")
        return None

    articles = split_articles(lines)
    volume, issue, year = parse_issue_info(lines)
    total_issue = _tt.total_issue_number(report_dir) or ""
    # 双月刊：第 N 期 → 2N 月出版（2、4、6、8、10、12 月）
    publish_month = str(int(issue) * 2) if issue.isdigit() else ""

    # ---- 模板（方案 A：继承成品）----
    # 优先级：本期 000 目录里已有的成品 → 上一期 → 内置模板。
    # 本期已有成品时优先用它，因为同事可能已经在本期调过版式，重新生成应当继承那一次调整。
    template_folder = (os.path.normpath(settings.templateFolder) if settings.templateFolder
                       else _tt.previous_issue_folder(report_dir))

    parts = _tt.load(report_dir, f"{base_name}.tex")
    if parts is not None:
        log(f"模板来源：本期 000 Table of Contents 里已有的 {base_name}.tex")
    else:
        parts = _tt.load(template_folder, f"{base_name}.tex") if template_folder else None
        if parts is not None:
            log(f"模板来源：上一期 {parts.source} 的 {base_name}.tex")
        else:
            parts = _tt.builtin(base_name)
            log("未找到可继承的模板，改用内置模板（卷期号等仍需手工核对）")

    # ---- 头部：填入本期卷期信息，并统一使用 Windows 字体 ----
    head = _tt.ensure_windows_fontset(_tt.patch_header(
        parts.head,
        volume=volume or "?", issue=issue or "?", year=year or "?",
        total_issue=total_issue or "?", publish_month=publish_month or "?",
        is_chinese=is_chinese))

    # ---- 栏目块 ----
    column_block = _tt.make_column_block(settings, parts.columnBlock, is_chinese)
    if settings.is_column_enabled:
        title = settings.chinese_column_title if is_chinese else settings.english_column_title
        log(f"本期栏目：{title}（{len(settings.columnArticles)} 篇）")

    # ---- 正文条目 ----
    entries: list[str] = []
    for folder_name, block in articles:
        entry = parse_entry(folder_name, block)
        if entry.titleParseFailed:
            log(f"⚠️ {folder_name}：标题在检查报告里是「None」，"
                "请回「检查tex信息」核对该篇 tex 的标题填写。")
        in_column = settings.contains(folder_name)
        entries.append(chinese_entry(entry, in_column, settings.titleWrapLimit) if is_chinese
                       else english_entry(entry, in_column, settings.titleWrapLimitEN))
    body = "\n\n\\vdistance\n\n".join(entries)

    # ---- 拼装 ----
    result = head
    if not column_block:
        result += "\n\n%中间主体\n\n"
    else:
        result += "\n" + "\n".join(column_block) + "\n\n\n%中间主体\n\n"
    result += body + "\n\n" + parts.tail

    out_path = os.path.join(report_dir, f"{base_name}_{date_str}.tex")
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(result)

    log(f"{label}目录TeX文件已生成")
    return out_path


def list_articles(report_path: str) -> list[tuple[str, str]]:
    """供界面使用：从检查报告里列出所有文章（名称 + 中文标题），用于栏目勾选。"""
    try:
        with open(report_path, encoding="utf-8", errors="replace") as fh:
            lines = python_readlines(fh.read())
    except OSError:
        return []
    return [(folder, parse_entry(folder, block).chineseTitle)
            for folder, block in split_articles(lines)]


def template_source_note(report_path: str, settings: TocSettings) -> str:
    """供界面使用：推断本期的模板来源说明（本期已有 → 上一期 → 内置）。"""
    d = os.path.dirname(os.path.abspath(report_path))
    if _tt.load(d, "Chinese Content.tex") is not None:
        return "本期 000 Table of Contents 里已有成品，将继承它的版式"
    folder = (os.path.normpath(settings.templateFolder) if settings.templateFolder
              else _tt.previous_issue_folder(d))
    if folder and _tt.load(folder, "Chinese Content.tex") is not None:
        return f"将继承上一期 {os.path.basename(os.path.normpath(folder))} 的版式"
    return "找不到可继承的成品，将使用内置模板（卷期号等需手工核对）"


def run(txt_path: str, settings: TocSettings, log=lambda _s: None,
        stamp: str | None = None) -> list[str]:
    """先英文后中文（与原版 `main.py` 的 `run_tab5` 顺序一致）。返回产物路径列表。"""
    log("运行生成目录的函数")
    outputs: list[str] = []
    for flag in ("en", "ch"):
        out = generate_tex(txt_path, flag, settings, log=log, stamp=stamp)
        if out:
            outputs.append(out)
    return outputs
