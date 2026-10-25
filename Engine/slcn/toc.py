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

from . import fsutil as _fsutil
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
    """只保留 CJK 统一表意文字（U+4E00–U+9FFF）与中文间隔号 `·`。

    ⚠️ 注意与 `infocheck.no_chinese` 的范围**不同**：那边是 9FA5（复刻上游），
    这里是 9FFF（mac 的 `cleanName` 就是这么写的）。刻意各自保留，别"顺手统一"。

    `·` 必须保留：外文译名的间隔号（如 `甲·乙`）从前被当噪声滤掉，
    成品目录里就成了「甲乙」。
    """
    return "".join(c for c in name if 0x4E00 <= ord(c) <= 0x9FFF or c == "·")


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


#: 「文本类」命令：清洗标题时**保留花括号里的内容**（不是整段删掉）。
#: 从前一律按 `\cmd{...}` 整段删除，于是 `甲\hbox{乙丙}` 只剩 `甲`、
#: `…\textbf{ABC}` 会丢 `ABC`（B-226 E1）。
_TEXT_COMMANDS = ("hbox", "textbf", "textit", "emph", "text")
_TEXT_COMMANDS_RE = re.compile(r"\\(?:" + "|".join(_TEXT_COMMANDS) + r")\s*\{\s*([^{}]*?)\s*\}")


def _unwrap_text_commands(t: str) -> str:
    r"""把 `\hbox{X}` / `\textbf{X}` / `\textit{X}` / `\emph{X}` / `\text{X}` 换成 `X`（可嵌套）。"""
    for _ in range(3):
        changed = _TEXT_COMMANDS_RE.sub(r"\1", t)
        if changed == t:
            break
        t = changed
    return t


def clean_title(raw: str | None, keep_braces: bool) -> str:
    r"""标题清洗：去掉残留的 LaTeX 命令，但**保留文本类命令的内容**。

    原版只去掉 `\\`、`\Large`、`\large`，`\zihao{4}` 这类会留在标题里 ——
    成品里真的出现过 `\large \zihao{4}Reasoning Disposition...`。

    两种特例（B-226 P0 冻结）：
      · 文本类命令保留内容：`甲\hbox{乙丙}` → `甲乙丙`、`...\textbf{ABC}` → `...ABC`；
      · 整条标题是数学模式（`$\text{术语}^*$`）：取 `\text` 内容、去掉上标星号
        → `术语`（从前会留下 `$^*$`，英文模板/数学字体在目录里是非法残留）。
    """
    if raw is None:
        return ""
    t = raw
    for token in ("\\\\", "\\Large", "\\large"):
        t = t.replace(token, "")
    math = re.match(r"^\s*\$(.*)\$\s*$", t, re.S)
    if math:
        inner = _unwrap_text_commands(math.group(1))
        inner = re.sub(r"\^\s*\{?\s*\*?\s*\}?", "", inner)   # 去掉 ^* / ^{*} 上标
        t = inner.replace("*", "").replace("$", "")
        t = re.sub(r"\\[a-zA-Z]+\s*\{[^}]*\}", "", t)
    else:
        t = _unwrap_text_commands(t)
        t = re.sub(r"\\[a-zA-Z]+\s*\{[^}]*\}", "", t)
    if not keep_braces:
        t = t.replace("{", "").replace("}", "")
    return t.strip()


def clean_author_en(raw: str | None) -> str:
    r"""英文作者清洗：去掉通讯作者/脚注标记（`$^\text{\Letter}$`、`\Letter`）等 LaTeX 残留。

    英文目录从前**编译失败**的根因就是它：报告里的第二作者英文取出来可能只剩
    `\Letter}$`，原样写进 tabularx 后 xelatex 报 `Argument of \@sect has an extra }`。
    这里只去标记，正文（英文名）原样保留；清洗后为空即由调用方丢弃（不是"换成别人"）。
    """
    if raw is None:
        return ""
    t = _unwrap_text_commands(raw)                  # \textit{Name} → Name
    t = re.sub(r"\\[a-zA-Z]+\s*", "", t)            # \Letter 之类无参命令
    t = t.replace("$", "").replace("^", "").replace("{", "").replace("}", "")
    t = t.replace("~", " ")                         # LaTeX 不换行空格 → 普通空格
    return re.sub(r"\s+", " ", t).strip()


#: 报告里的作者角色行：`第一作者英文: …` / `第二作者中文: …`（全角冒号也兼容）
_AUTHOR_LINE_RE = re.compile(r"(第[一二三])作者(英文|中文)\s*[:：]\s*(.*)")
_AUTHOR_ROLE_INDEX = {"第一": 0, "第二": 1, "第三": 2}
#: 源稿占位符（88 期末篇里真的写着「标题」「第一作者」）——只标注、不改行为
_PLACEHOLDER_TITLES = {"标题", "题目", "无", "None"}
_PLACEHOLDER_AUTHOR_RE = re.compile(r"^第[一二三]作者$")


def parse_entry(folder_name: str, block: list[str]) -> TocEntry:
    r"""从一篇报告块里解析出目录条目。

    作者按**角色**（第一/第二/第三）占位：同一角色出现两条时**后者优先**。
    报告里同一角色可能出现两行「第一作者中文」——先一行误填，后一行才对；
    后一条才是真名；从前两条都进列表，目录里就成了「甲，甲乙」（B-226 E9）。
    """
    entry = TocEntry(folderName=folder_name)
    zh: list[str | None] = [None, None, None]
    en: list[str | None] = [None, None, None]
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
        else:
            m = _AUTHOR_LINE_RE.search(line)
            if m:
                slot = en if m.group(2) == "英文" else zh
                raw_author = m.group(3).strip()
                if raw_author:
                    slot[_AUTHOR_ROLE_INDEX[m.group(1)]] = raw_author

    entry.chineseTitle = clean_title(entry.chineseTitle, keep_braces=False)
    entry.englishTitle = clean_title(entry.englishTitle, keep_braces=True)
    entry.chineseAuthors = [a for a in (clean_name(x) for x in zh if x) if a]
    entry.englishAuthors = [a for a in (clean_author_en(x) for x in en if x) if a]
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


# 中文断行的「次优断点」：在常见**虚词/连词**之后断（第一行以它们结尾），
# 比在词中间硬劈更像人工排版。**故意保守**：只收高频功能词，不收可作实词的单字
# （在/中/上/下/对/由/把/被/将/于/从/前/后/里/外 等都不收 —— 它们常出现在「存在」
# 这类词的词尾，断在它们后面反而会切词）。
_WEAK_BREAK_AFTER = set("的与和及或等者地得之其而并且")


def wrap_title(title: str, limit: float, is_english: bool) -> tuple[str, str] | None:
    r"""把过长的标题断成两行（返回 None 表示不用断）。

    为什么需要：目录第一列是内容自适应的 `l` 列，只要有一篇标题太长，整张表就会被撑宽，
    页码列被推出版心（看起来就是右侧溢出）。同事在成品里也是手工断行的。

    断点选择（2026-10-17 第十四批按期 90 成品校准）：
      英文：在**最后一个空格**处断（上限内尽量靠右）；
      中文：① 标点后（：，、；。！？……），且第一行至少占 40%（避免头重脚轻）；
            ② 没有合适标点时，在**虚词/连词后**断（`_WEAK_BREAK_AFTER`），同样要求第一行 ≥40%；
            ③ 都没有才用"上限内最靠右"，并在两侧 <35% / >65% 时退回**居中**兜底。
    为什么加第 ② 步：期 90 有一篇标题被 0.35/0.65 的居中兜底劈成「真理 / 论」这一类
    （切词）；成品在同一位置断在「的」后。引入 ② 之后，这类标题的断点与成品一致。
    两侧各至少留 3 个字，避免孤字行。
    """
    if limit <= 0 or display_width(title) <= limit:
        return None
    chars = list(title)
    if len(chars) < 8:
        return None

    min_side = 3
    min_first = len(chars) * 0.4      # 第一行至少占 40%（标点/虚词断点用）
    best: int | None = None

    def fits(i: int) -> bool:
        return (min_side <= i <= len(chars) - min_side
                and display_width("".join(chars[:i])) <= limit)

    if is_english:
        for i in range(len(chars) - min_side, min_side - 1, -1):
            if chars[i] == " " and display_width("".join(chars[:i])) <= limit:
                best = i
                break
    else:
        # ① 标点后（上限内取最靠右；太靠前会造成「第一行很短、第二行很长」的难看版式）
        for i in range(len(chars) - min_side, min_side - 1, -1):
            if chars[i - 1] in "：，、；。！？…—" and i >= min_first and fits(i):
                best = i
                break
        # ② 虚词/连词后（同类里取最靠右；同样不许第一行太短）
        if best is None:
            for i in range(len(chars) - min_side, min_side - 1, -1):
                if chars[i - 1] in _WEAK_BREAK_AFTER and i >= min_first and fits(i):
                    best = i
                    break
        # ③ 兜底：上限内最靠右；两侧失衡时退回居中（保留原行为，避免孤字行）
        if best is None:
            for i in range(len(chars) - min_side, min_side - 1, -1):
                if fits(i):
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
    # 英文断行上限：2026-10-17 第十四批按期 90 成品校准，35 → 30。
    # 证据：成品英文目录的断点实际落在 24–30；用默认 35 时整表比成品宽 21.86pt（7.7mm），
    # 且 7 处断行同一处都对不上；调成 30 后 4/7 与成品一致（详见案例第 110 条）。
    titleWrapLimitEN: float = 30

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
        """某篇文章是否属于栏目。报告里的名字是 `folder_name`（如 `Meimei_Han`），
        设置里可能存带序号的（如 `01 Meimei_Han`），所以两边都兼容。"""
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
            titleWrapLimitEN=float(data.get("titleWrapLimitEN", 30)),
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


# ── 模板解析（单点：generate_tex 与 template_source_note 共用）──────────

#: 模板来源的四种口径 —— 结果 JSON 的 `templateSource` 字段（GUI 依赖，别改字面量）。
TEMPLATE_CURRENT = "本期成品"
TEMPLATE_EXPLICIT = "显式指定"
TEMPLATE_PREVIOUS = "上一期"
TEMPLATE_BUILTIN = "内置模板"


@dataclass
class TemplateChoice:
    """一次模板解析的结果：模板本体 + 来源口径 + 人话说明。"""

    parts: _tt.Parts
    source: str
    note: str
    folder: str = ""


def resolve_template(report_dir: str, settings: "TocSettings",
                     file_name: str) -> TemplateChoice:
    r"""解析模板来源，优先级：**本期成品 → （显式 templateFolder）→ 上一期 → 内置模板**。

    单点实现：真正生成（`generate_tex`）与界面说明（`template_source_note`）必须同一口径，
    否则会出现「预览说继承上一期、生成时却用内置」这类对不上（B-226 E8/F2）。
    `--template` 显式指定的口径**保持现状**：排在「本期成品」之后（本期已有成品时本期优先）。
    """
    cur = _tt.load(report_dir, file_name)
    if cur is not None:
        return TemplateChoice(cur, TEMPLATE_CURRENT,
                              f"本期 000 Table of Contents 里已有的 {file_name}", report_dir)
    if settings.templateFolder:
        folder = os.path.normpath(settings.templateFolder)
        parts = _tt.load(folder, file_name)
        if parts is not None:
            return TemplateChoice(parts, TEMPLATE_EXPLICIT,
                                  f"指定模板 {os.path.basename(folder)} 的 {file_name}", folder)
    prev = _tt.previous_issue_folder(report_dir)
    if prev:
        parts = _tt.load(prev, file_name)
        if parts is not None:
            return TemplateChoice(parts, TEMPLATE_PREVIOUS,
                                  f"上一期 {os.path.basename(prev)} 的 {file_name}", prev)
    return TemplateChoice(_tt.builtin(file_name), TEMPLATE_BUILTIN,
                          "未找到可继承的模板，改用内置模板（卷期号等仍需手工核对）", "")


def template_source(report_path: str, settings: "TocSettings") -> tuple[str, str]:
    """供结果 JSON 用：`(templateSource, templateNote)`。"""
    d = os.path.dirname(os.path.abspath(report_path))
    choice = resolve_template(d, settings, "Chinese Content.tex")
    return choice.source, choice.note


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
    # 优先级：本期 000 目录里已有的成品 → （显式 templateFolder）→ 上一期 → 内置模板。
    # 解析逻辑单点在 `resolve_template`：界面预览与真正生成必须是同一口径。
    choice = resolve_template(report_dir, settings, f"{base_name}.tex")
    parts = choice.parts
    log(f"模板来源：{choice.note}")

    # ---- 头部：填入本期卷期信息，并统一使用 Windows 字体 ----
    head = _tt.ensure_windows_fontset(_tt.patch_header(
        parts.head,
        volume=volume or "?", issue=issue or "?", year=year or "?",
        total_issue=total_issue or "?", publish_month=publish_month or "?",
        is_chinese=is_chinese))

    # ---- 栏目块 ----
    column_block = _tt.make_column_block(settings, parts.columnBlock, is_chinese)
    if settings.is_column_enabled:
        count = len(settings.columnArticles)
        if is_chinese:
            log(f"本期栏目：{settings.chinese_column_title}（{count} 篇）")
        elif (settings.columnNameEN or "").strip():
            log(f"本期栏目：Column: {settings.columnNameEN.strip()}（{count} 篇）")
        else:
            log(f"本期栏目：英文栏未填英文名 → 不生成 Column 行"
                f"（条目仍带 ~~~~ 标记，{count} 篇）")

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
    # 原子写 + 旧版备份（UI 方案第 4 件）：目录 .tex 每期都会覆盖上一版，
    # 同事手工调过的版式万一被盖掉，还能从同目录的 `.bak` 捞回来。
    _fsutil.atomic_write(out_path, result, newline="", log=log)

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


def manual_edits(report_path: str, settings: "TocSettings") -> list[dict[str, str]]:
    r"""结果 JSON 的 `manualEdits`：**只标注、不改行为**的人工手改/源稿占位条目。

    给 P2 允许清单用：GUI 只负责显示，是否放行由人逐条确认（B-226 §11.6）。
    四类 `kind`（schema 冻结）：
      · `title` —— 标题在报告里是「None」/空/源稿占位（如 88 期末篇的「标题」）；
      · `author` —— 作者为空或源稿占位（如「第一作者」）；
      · `wrap` —— 标题由引擎自动断行，断点可能与人工成品不同；
      · `columnHeader` —— 本期开了栏目但继承的模板里没有栏目行（已按兜底形态重建）。
    """
    edits: list[dict[str, str]] = []

    def add(kind: str, article: str, note: str) -> None:
        edits.append({"kind": kind, "article": article, "note": note})

    try:
        with open(report_path, encoding="utf-8", errors="replace") as fh:
            lines = python_readlines(fh.read())
    except OSError:
        return edits
    for folder_name, block in split_articles(lines):
        e = parse_entry(folder_name, block)
        if not e.chineseTitle.strip():
            add("title", folder_name,
                "标题在检查报告里为空或「None」—— 请回「检查tex信息」核对该篇 tex 的标题填写")
        elif e.chineseTitle.strip() in _PLACEHOLDER_TITLES:
            add("title", folder_name,
                f"源稿标题是占位「{e.chineseTitle.strip()}」，成品目录需人工写正式标题")
        if not e.chineseAuthors:
            add("author", folder_name,
                "中文作者在检查报告里为空 —— 请回「检查tex信息」核对该篇 tex 的作者填写")
        elif any(_PLACEHOLDER_AUTHOR_RE.match(a) for a in e.chineseAuthors):
            add("author", folder_name, "源稿作者是占位（「第一作者」之类），需人工补正式姓名")
        wrapped = None
        if e.chineseTitle and "——" not in e.chineseTitle:
            wrapped = wrap_title(e.chineseTitle, settings.titleWrapLimit, is_english=False)
        if e.englishTitle:
            wrapped_en = wrap_title(e.englishTitle, settings.titleWrapLimitEN, is_english=True)
            wrapped = wrapped or wrapped_en
        if wrapped:
            add("wrap", folder_name,
                "标题由引擎自动断行，断点可能与人工成品不同（P2 按允许清单逐条确认）")

    if settings.is_column_enabled:
        d = os.path.dirname(os.path.abspath(report_path))
        parts = _tt.load(d, "Chinese Content.tex")
        if parts is None or not parts.columnBlock:
            add("columnHeader", "",
                f"本期栏目「{settings.chinese_column_title}」：继承的模板里没有栏目行，"
                "已按兜底形态重建，请核对栏目名/主持人")
    return edits


def template_source_note(report_path: str, settings: TocSettings) -> str:
    """供界面使用：推断本期的模板来源说明（本期已有 → 显式指定 → 上一期 → 内置）。"""
    d = os.path.dirname(os.path.abspath(report_path))
    choice = resolve_template(d, settings, "Chinese Content.tex")
    if choice.source == TEMPLATE_CURRENT:
        return "本期 000 Table of Contents 里已有成品，将继承它的版式"
    if choice.source == TEMPLATE_EXPLICIT:
        return f"将继承指定模板 {os.path.basename(os.path.normpath(choice.folder))} 的版式"
    if choice.source == TEMPLATE_PREVIOUS:
        return f"将继承上一期 {os.path.basename(os.path.normpath(choice.folder))} 的版式"
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
