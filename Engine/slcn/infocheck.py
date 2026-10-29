r"""「检查tex信息」的引擎实现（第 94 条：从 mac 的 Swift 侧搬进引擎）。

对应 mac 的 `sources-mac/Features/InfoCheckFeature.swift`（其上游是原版 `outinfo.py`）。

**干什么**：把一期里每篇文章 `.tex` 里**已经填好**的信息读出来汇总成检查报告 ——
卷号 / 期号 / 年份 / 起止页码 / 页数 / 字号 / 中英文标题 / 各作者（中英文）/ 全文行距，
再与该篇 PDF 的**实际页数**对比。这个功能**没有任何交互问答**，纯粹是"把 tex 里的值抄出来核对"。

**两份产物**（与原版／mac 版逐字节一致）：

  · `信息检查<时间戳>.txt` —— **CRLF** 行尾（复刻原版在 Windows 文本模式写盘的结果），
    字段顺序 = 扫描顺序（不是固定 schema，因为作者行的出现位置取决于文件本身）；
  · `信息检查<时间戳>.json` —— 结构化数据（本项目的扩展），给「生成目录」和界面表格用。
    刻意复刻 Swift `JSONEncoder(.prettyPrinted, .sortedKeys, .withoutEscapingSlashes)` 的字节形态：
    两空格缩进、`" : "`（冒号前有空格）、键排序、**空字段省略（不写 null）**、中文不转义。

**与上游（原版 outinfo.py）的三处差异**（都写在 mac 那份的注释里，这里一并继承）：

  1. **取花括号里的值**，而不是整行 —— 否则报告里会粘上「% 若需要，请输入第二作者姓名…」
     这类中文注释，更严重的是注释本身是中文，会让英文作者名被写进「中文作者」字段，
     英文目录里该作者直接消失；
  2. **按花括号配平收集标题行**（最多 12 行），而不是固定取两行 —— 有的文章把闭合 `}` 放在
     第三行，固定两行会配不平 → 标题写成字面量 `None` → 目录里标题整行消失；
  3. 索引越界时**留空并提示**，而不是抛 IndexError 让整份报告失败。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass, field

from . import pdfmerge as _pm

#: 汉字范围。⚠️ 与 Swift 那份逐字一致：`\u4e00-\u9fa5`（**不是** 9fff）
_CJK = re.compile(r"[\u4e00-\u9fa5]")

_TITLE_KEYWORDS_ZH = "请输入论文题目"
_TITLE_KEYWORDS_EN = "请输入英文标题"
_TITLE_KEYWORDS_ZH_ALT = "Insert Chinese title"
_TITLE_KEYWORDS_EN_ALT = "Insert the title of your paper."


@dataclass
class ArticleInfo:
    """一篇文章的信息。字段名与 Swift `ArticleInfo` **逐字一致**（JSON 靠它对齐）。"""

    folder: str
    folderName: str
    volume: str = ""
    issue: str = ""
    year: str = ""
    startPage: str = ""
    endPage: str = ""
    pages: str = ""
    chineseTitle: str | None = None
    englishTitle: str | None = None
    firstAuthorEnglish: str | None = None
    firstAuthorChinese: str | None = None
    secondAuthorEnglish: str | None = None
    secondAuthorChinese: str | None = None
    thirdAuthorEnglish: str | None = None
    thirdAuthorChinese: str | None = None
    fontSize: str | None = None
    lineSpacing: str | None = None
    actualPDFPages: int | None = None
    hasTeX: bool = False
    hasPDF: bool = False

    @property
    def page_mismatch(self) -> bool:
        """声明的页数与 PDF 实际页数不符（界面标红用）。"""
        if self.actualPDFPages is None or not self.pages.strip():
            return False
        try:
            return int(self.pages.strip()) != self.actualPDFPages
        except ValueError:
            return False


@dataclass
class InfoReport:
    generatedAt: str = ""
    issueFolder: str = ""
    articles: list[ArticleInfo] = field(default_factory=list)

    @property
    def inconsistencies(self) -> list[str]:
        """跨篇的不一致项（卷号/期号/年份不一致、页数对不上），供界面提示。"""
        notes: list[str] = []
        for key, label in (("volume", "卷号"), ("issue", "期号"), ("year", "年份")):
            vals = sorted({getattr(a, key) for a in self.articles if getattr(a, key)})
            if len(vals) > 1:
                notes.append(f"{label}不一致：{' / '.join(vals)}")
        for a in self.articles:
            if a.page_mismatch:
                notes.append(f"{a.folderName}：声明页数 {a.pages}，实际 {a.actualPDFPages} 页")
        return notes


# ── 上游的三个工具函数 ───────────────────────────────────────────────────

def no_chinese(s: str) -> bool:
    """不含中文返回 True（对应上游 `no_chinese`）。"""
    return _CJK.search(s) is None


def extract_latex_value(line: str) -> str:
    """从 tex 的一行里取出「真正的值」：先去掉注释，再取**最后一个**花括号组。

    上游直接拿整行当作者名，于是报告里会粘上中文注释（见模块开头第 1 条差异）。
    """
    # 第 1 步：去掉第一个**未转义**的 `%` 及其后面
    code = line
    i = 0
    while i < len(line):
        if line[i] == "\\":      # \% 是转义的百分号
            i += 2
            continue
        if line[i] == "%":
            code = line[:i]
            break
        i += 1
    # 第 2 步：取最后一个花括号组的内容
    open_at = code.rfind("{")
    if open_at >= 0:
        close_at = code.rfind("}")
        if close_at > open_at:
            return code[open_at + 1:close_at].strip()
    return code.strip()


def extract_title(text: str) -> str | None:
    """对应上游 `extract_title`：找到花括号配平为 -1 的位置，取 `text[1:i]`（去换行）。"""
    flag = 0
    for i, ch in enumerate(text):
        if ch == "{":
            if i == 0:
                continue
            flag += 1
        elif ch == "}":
            flag -= 1
        if flag == -1:
            return text[1:i].replace("\n", "")
    m = re.search(r"\{([^{}]+)\}", text)
    if m:
        return m.group(1)
    return None


def get_line_num(lines: list[str], keyword: str) -> str:
    """第一条含关键词的行里，**所有数字字符按原顺序拼接**（对应上游 `get_line_num`）。"""
    for line in lines:
        if keyword in line:
            return "".join(c for c in line if c.isdigit())
    return ""


def collect_braced_lines(lines: list[str], starting_after: int, max_lines: int = 12) -> str:
    """从 `starting_after` 之后收集行，直到花括号配平（见模块开头第 2 条差异）。"""
    depth = 0
    started = False
    collected: list[str] = []
    end = min(starting_after + 1 + max_lines, len(lines))
    i = starting_after + 1
    while i < end:
        line = lines[i]
        collected.append(line)
        for ch in line:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
        if started and depth <= 0:
            break
        i += 1
    return "\n".join(collected)


# ── JSON：复刻 Swift JSONEncoder 的字节形态 ──────────────────────────────

def _swift_json(value, indent: int = 0) -> str:
    r"""把 dict/list/str/int/bool 输出成 Swift `JSONEncoder` 那种排版。

    为什么要自己写：Python 的 `json.dumps` 用 `": "`，Swift 用 `" : "`；
    而且 Swift 对 `nil` 的 Optional 是**省略键**，Python 会写 `null`。
    自己写这 30 行，两个平台的产物才能逐字节比对（第 88 条对合并 PDF 也是这个要求）。
    """
    pad = "  " * indent
    if isinstance(value, dict):
        items = [(k, v) for k, v in value.items() if v is not None]
        if not items:
            return "{}"
        body = ",\n".join(
            f'{"  " * (indent + 1)}"{k}" : {_swift_json(v, indent + 1)}'
            for k, v in sorted(items))
        return "{\n" + body + "\n" + pad + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        body = ",\n".join(f'{"  " * (indent + 1)}{_swift_json(v, indent + 1)}' for v in value)
        return "[\n" + body + "\n" + pad + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


# ── 主流程 ───────────────────────────────────────────────────────────────

def _first_sty(folder: str) -> str | None:
    try:
        stys = sorted(n for n in os.listdir(folder) if n.endswith(".sty"))
    except OSError:
        return None
    return os.path.join(folder, stys[0]) if stys else None


def process_folders(path: str, out_dir: str | None = None,
                    log=lambda _s: None,
                    stamp: str | None = None,
                    write: bool = True) -> InfoReport:
    """扫描一期目录，写 `信息检查<时间戳>.txt/.json`，返回报告对象。

    `out_dir` 默认就是期目录本身（与 mac 版一致：「信息检查」产物落在期目录根下）。
    `write=False` 时**只算不写** —— 界面预览（`infocheck.py --preview`）用它，
    免得"看一眼表格"就往稿件目录里落两个文件。
    """
    out_dir = out_dir or path
    output: list[str] = []
    articles: list[ArticleInfo] = []

    for folder in _pm.numbered_entries(path):
        folder_path = os.path.join(path, folder)
        # `000…` 是「中英文目录」保留号（合并/编译都按前缀跳过）。从前这里只认
        # `article_name == "Contents"`，于是 `000 目录` 这种写法会被当成**一篇文章**
        # 混进报告与目录（2026-10-17 全库普查发现 `000 Table of Contents` 全库统一，
        # 但标准里必须写明，别让人以为改成中文名也行）。
        if folder.startswith("000"):
            continue
        name = _pm.article_name(folder)

        output.append(f"{name}\n")
        art = ArticleInfo(folder=folder, folderName=name)

        # ---------- 读 .tex ----------
        # 与 `compileall` 同一口径：先精确、再宽松兜底（`MingXu.tex` 少了别人的下划线、
        # `Tianan_He.tex` 丢了撇号这类）。从前这里**只认精确名**，于是同一篇
        # 「编译全部」能编、「检查tex信息」却报字段缺失、目录里标题成 None
        # （2026-10-17 全库普查实测：2024年第2期 03 Ming_Xu）。
        tex_file = _pm.find_tex(folder_path, name)
        if tex_file:
            art.hasTeX = True
            with open(tex_file, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            lines = content.splitlines()

            vol = get_line_num(lines, "输入卷号")
            if vol:
                output.append(f"卷号：{vol}  ")
                art.volume = vol
            issue = get_line_num(lines, "输入当年期号")
            if issue:
                output.append(f"期号：{issue}  ")
                art.issue = issue
            year = get_line_num(lines, "输入出版年份")
            if year:
                output.append(f"年份：{year}\n")
                art.year = year
            start = get_line_num(lines, "输入起始页码")
            if start:
                output.append(f"起始页码：{start}  ")
                art.startPage = start
            end = get_line_num(lines, "输入终止页码")
            if end:
                output.append(f"终止页码：{end}  ")
                art.endPage = end
            pages = get_line_num(lines, "输入页数")
            if pages:
                output.append(f"页数：{pages}\n")
                art.pages = pages

            for i, line in enumerate(lines):
                if _TITLE_KEYWORDS_ZH in line or _TITLE_KEYWORDS_ZH_ALT in line:
                    title = extract_title(collect_braced_lines(lines, i))
                    if title is None:
                        log(f"⚠️ {name}：中文标题解析失败，报告里会写成 None。")
                    output.append(f"中文标题: {title if title is not None else 'None'}\n")
                    art.chineseTitle = title
                    continue

                if _TITLE_KEYWORDS_EN in line or _TITLE_KEYWORDS_EN_ALT in line:
                    title = extract_title(collect_braced_lines(lines, i))
                    if title is None:
                        log(f"⚠️ {name}：英文标题解析失败，报告里会写成 None。")
                    output.append(f"英文标题: {title if title is not None else 'None'}\n")
                    art.englishTitle = title
                    continue

                if "第一作者姓名" in line or "Insert the first author name." in line:
                    if i + 1 < len(lines):
                        val = extract_latex_value(lines[i + 1])
                    else:
                        log(f"⚠️ {name}.tex 第 {i + 2} 行不存在（关键词出现在文件末尾），该字段留空。")
                        val = ""
                    if no_chinese(val):
                        output.append(f"第一作者英文: {val}\n")
                        art.firstAuthorEnglish = val
                    else:
                        output.append(f"第一作者中文: {val}\n")
                        art.firstAuthorChinese = val
                    continue

                # ⚠️ 第二/三作者取的是**关键词所在行本身**（不是下一行），
                #    且行首是 `%` 的（被注释掉的模板行）要跳过 —— 与上游一致。
                if ("第二作者姓名" in line or "Insert the second author if" in line) \
                        and not line.startswith("%"):
                    val = extract_latex_value(line)
                    if no_chinese(val):
                        output.append(f"第二作者英文: {val}\n")
                        art.secondAuthorEnglish = val
                    else:
                        output.append(f"第二作者中文: {val}\n")
                        art.secondAuthorChinese = val
                    continue

                if ("第三作者姓名" in line or "Insert the third author if" in line) \
                        and not line.startswith("%"):
                    val = extract_latex_value(line)
                    if no_chinese(val):
                        output.append(f"第三作者英文: {val}\n")
                        art.thirdAuthorEnglish = val
                    else:
                        output.append(f"第三作者中文: {val}\n")
                        art.thirdAuthorChinese = val
                    continue

                if "documentclass" in line:
                    parts = line.split(",")
                    if len(parts) > 1:
                        output.append(f"字号 {parts[1]}\n")
                        art.fontSize = parts[1]
                    continue

        # ---------- 读 .sty（全文行距）----------
        sty = _first_sty(folder_path)
        if sty:
            with open(sty, encoding="utf-8", errors="replace") as fh:
                baseline = get_line_num(fh.read().splitlines(), "全文行间距")
            if baseline:
                output.append(f"全文行距: {baseline}\n")
                art.lineSpacing = baseline

        # ---------- PDF 实际页数 ----------
        # 与 `mergepdf` 同一口径：先精确、再宽松兜底（`Yang Liu.pdf` 空格/下划线不同）。
        pdf_hit = _pm.resolve_article_pdf(folder_path, name)
        if pdf_hit:
            pdf_file, actual_pdf = pdf_hit
            art.hasPDF = True
            if actual_pdf != f"{name}.pdf":
                log(f"⚠️ {name}：PDF 实际文件名是「{actual_pdf}」，与目录名不一致，已按宽松匹配采用它"
                    "（建议改成与目录名一致，否则别的工具可能找不到）。")
            try:
                from pypdf import PdfReader  # noqa: PLC0415
                n = len(PdfReader(pdf_file).pages)
                art.actualPDFPages = n
                output.append(f"{'-' * 26}实际pdf页数: {n}\n")
            except Exception as e:  # noqa: BLE001
                log(f"⚠️ 无法打开 {name}.pdf，跳过页数检查（{type(e).__name__}）。")

        output.append("\n")
        articles.append(art)

    stamp = stamp or _dt.datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    report = InfoReport(generatedAt=stamp, issueFolder=path, articles=articles)
    if not write:
        return report

    base = os.path.join(out_dir, f"信息检查{stamp}")

    # txt：CRLF（复刻原版在 Windows 文本模式写盘的结果）
    with open(base + ".txt", "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(output).replace("\n", "\r\n"))

    payload = {
        "generatedAt": stamp,
        "issueFolder": path,
        "articles": [a.__dict__ for a in articles],
    }
    with open(base + ".json", "w", encoding="utf-8", newline="") as fh:
        fh.write(_swift_json(payload))

    return report
