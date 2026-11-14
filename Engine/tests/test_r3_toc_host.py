#!/usr/bin/env python3
r"""R3 目录返修 / 缺陷 1：中文栏目「主持人」必须落在最右列、右端与页码列对齐。

本测试**自包含**：在临时目录内合成匿名期目录（信息检查报告 + 目录设置.json +
英文继承模板 contents.tex），不依赖任何外部夹具仓，官方 runner / 干净 checkout 可跑。

判据：
  (a) 生成/编译 rc=0；中文与英文 PDF 各 1 页；
  (b) 中文 `Chinese Content.tex` 主持人行是
      `&\multicolumn{2}{@{}r@{}}{\textit{\large <host_cell>}}`，
      host 文本在该 multicolumn 组内（长主持人仍保留格内换行）；
  (c) 普通条目行不变：仍 4 条中文 `~~~~`（columnArticles 前 4 篇），
      其余 6 条无 `~~~~`；作者列仍是普通 `&\textit{\large …}`；
  (d) 规范三列列定义不变（X / p{0.26\textwidth} / r、2em / 1.5em）；
  (e) 英文专栏/专辑的标题首行、标题续行与作者全部行使用同一物理排版起点；
      普通条目仍不缩进。用 pypdf 读编译后 PDF 的文本矩阵原点（不是墨迹边缘），
      缺 xelatex/pypdf 时明确 SKIP；同时验证推送解析仍识别栏目归属与作者名。

本文件是独立脚本（同 Engine/tests 其余套件风格），失败退出码非 0。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    PdfReader = None
    HAS_PYPDF = False

ISSUE_NAME = "2026年第5期(999)"


def _find_tool(name: str, *fallbacks: str) -> str:
    """优先 PATH；再试常见安装路径；都没有返回空串。"""
    found = shutil.which(name)
    if found:
        return found
    for candidate in fallbacks:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return ""


XELATEX = _find_tool("xelatex", "/Library/TeX/texbin/xelatex")
GS = (_find_tool("gs", "/usr/local/bin/gs", "/opt/homebrew/bin/gs")
      or _find_tool("gswin64c") or _find_tool("gswin32c"))
TEXBIN = str(Path(XELATEX).parent) if XELATEX else ""
CAN_COMPILE = bool(XELATEX)
CAN_COUNT_PAGES = bool(GS)

#: 10 篇匿名稿件的检查报告（字段与真实报告同形，内容全部匿名合成）。
REPORT = r"""L__Lambda_1
卷号：19  期号：5  年份：2026
起始页码：1  终止页码：16  页数：16
字号 10.5t
第一作者英文: L. Lambda
第二作者英文: Z. Omega
中文标题: 匿名标题甲
第一作者中文: \kaishu 匿名甲
第二作者中文: \kaishu 匿名甲
英文标题: Model Solution Question Perspective Contribution Observation Question
全文行距: 13
--------------------------实际pdf页数: 16

G__Eta_2
卷号：19  期号：5  年份：2026
起始页码：17  终止页码：27  页数：11
字号 10.5t
第一作者英文: G. Eta
第二作者英文: H. Anonymous
中文标题: 匿名标题乙
第一作者中文: \kaishu 匿名乙
第二作者中文: \kaishu 匿名乙
英文标题: Logic Principle Logic Perspective Property Logic Inquiry Puzzle Status
全文行距: 13
--------------------------实际pdf页数: 11

A__Alpha_Beta_3
卷号：19  期号：5  年份：2026
起始页码：28  终止页码：45  页数：18
字号 10.5t
第一作者英文: A. Alpha Beta
中文标题: 匿名标题丙
第一作者中文: \kaishu 匿名丙
英文标题: Methodology Argument Epistemic Critical Argument Argument Generalization Status Approach Status
全文行距: 13
--------------------------实际pdf页数: 18

C__Epsilon_Zeta_4
卷号：19  期号：5  年份：2026
起始页码：46  终止页码：62  页数：17
字号 10.5t
第一作者英文: C. Epsilon Zeta
第二作者英文: F. Anonymous
中文标题: 匿名标题丁
第一作者中文: \kaishu 匿名丁
第二作者中文: \kaishu 匿名丁
英文标题: Solution Relation Foundation Proposition Journal Reflection Relation Tradition Survey Analytical Relation
全文行距: 13
--------------------------实际pdf页数: 17

U__Upsilon_5
卷号：19  期号：5  年份：2026
起始页码：63  终止页码：76  页数：14
字号 10.5t
第一作者英文: U. Upsilon
中文标题: 匿名标题戊
第一作者中文: \kaishu 匿名戊
英文标题: Model Structure Argument Structure Analysis Practical Inquiry Language Puzzle Examination Theoretical Status Formal
全文行距: 13
--------------------------实际pdf页数: 14

A__Anonymous_6
卷号：19  期号：5  年份：2026
起始页码：77  终止页码：94  页数：18
字号 10.5t
第一作者英文: A. Anonymous
中文标题: 匿名标题己
第一作者中文: \kaishu 匿名己
英文标题: Inference Epistemic Argument
全文行距: 13
--------------------------实际pdf页数: 18

Anonymous_Observer_7
卷号：19  期号：5  年份：2026
起始页码：95  终止页码：118  页数：24
字号 10.5t
第一作者英文: Anonymous Observer
第二作者英文: Z. Omega
中文标题: 匿名标题庚
第一作者中文: \kaishu 匿名庚
第二作者中文: \kaishu 匿名庚
英文标题: Method Analytical Methodology Examination Semantics Structure Method Epistemic Synthetic Approach Contribution Practice
全文行距: 13
--------------------------实际pdf页数: 24

O__Omicron_8
卷号：19  期号：5  年份：2026
起始页码：119  终止页码：136  页数：18
字号 10.5t
第一作者英文: O. Omicron
中文标题: 匿名标题辛
第一作者中文: \kaishu 匿名辛
英文标题: Foundation Generalization Framework Survey Criterion System Relation Statement Theory Relation Study
全文行距: 13
--------------------------实际pdf页数: 18

U__Upsilon_9
卷号：19  期号：5  年份：2026
起始页码：137  终止页码：152  页数：16
字号 10.5t
第一作者英文: U. Upsilon
中文标题: 匿名标题壬
第一作者中文: \kaishu 匿名壬
英文标题: Truth Inference Observation Comparison Property Foundation Historical Inquiry Critical Status Statement Relation
全文行距: 13
--------------------------实际pdf页数: 16

D__Delta_10
卷号：19  期号：5  年份：2026
起始页码：153  终止页码：172  页数：20
字号 10.5t
第一作者英文: D. Delta
中文标题: 匿名标题癸
第一作者中文: \kaishu 匿名癸
英文标题: Study Status Conceptual Practical Inquiry Study
全文行距: 13
--------------------------实际pdf页数: 20
"""

#: 对齐回归用 2 条栏目 + 2 条普通；第 3 篇加长作者名，强制作者换行。
REPORT_INDENT = "\n\n".join(
    REPORT.strip().split("\n\n")[index] for index in (0, 2, 4, 5)) + "\n"
REPORT_INDENT = REPORT_INDENT.replace(
    "第一作者英文: A. Alpha Beta\n",
    "第一作者英文: A. Alpha Beta Maximus\n"
    "第二作者英文: B. Gamma Maximilian\n"
    "第三作者英文: C. Delta Bartholomew\n")

#: 专辑回归只用前 3 篇：全部带栏目缩进后仍能单页（对齐检查不混入超页问题）。
REPORT_ALBUM = "\n\n".join(REPORT_INDENT.strip().split("\n\n")[:3]) + "\n"

#: 上一期的英文目录（匿名合成：10pt、-3.05mm 行距、规范 X@2em 两列）。
EN_TEMPLATE = r"""\documentclass[b5paper,10pt,onecolumn,twoside,fontset=windows]{ctexart}
\pagestyle{empty}%不显示页码
\usepackage[total={130mm,203mm}, top=20.3mm, headheight=20pt, headsep=4truemm]{geometry}%调整版式
\usepackage{tabularx}
\usepackage{amssymb}
\setmainfont{Times New Roman}

\newcommand{\vdistance}{\\[-3.05mm]}%调整行间隔

\begin{document}
\begin{flushright}
\zihao{-4}
\textbf{Studies in Logic\phantom{|}\\
Volume 19, Number 5, October 2026\phantom{|}}
\end{flushright}
\vspace{0.5ex} {\rmfamily{\bfseries TABLE OF CONTENTS}} \vspace{-0.5ex}

\noindent\hrulefill{\rmfamily} \vspace{2ex}\\
\begin{tabularx}{\textwidth}{@{}>{\raggedright\arraybackslash}X@{\hspace{2em}}r@{}}
%中间主体
\end{tabularx}
\end{document}
"""

SETTINGS = {
    "columnKind": "专栏",
    "columnName": "匿名专栏",
    "columnNameEN": "Statement Practice Interpretation",
    "columnHost": "匿名主持人",
    "columnArticles": [
        "01 L__Lambda_1",
        "02 G__Eta_2",
        "03 A__Alpha_Beta_3",
        "04 C__Epsilon_Zeta_4",
        "05 U__Upsilon_5",
        "06 A__Anonymous_6",
        "07 Anonymous_Observer_7",
        "08 O__Omicron_8",
        "09 U__Upsilon_9",
        "10 D__Delta_10",
    ],
}

_passed = 0
_fails: list[str] = []
_skips: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}  {detail}")


def skip(name: str, why: str) -> None:
    _skips.append(name)
    print(f"SKIP  {name}  {why}")


def env() -> dict[str, str]:
    e = dict(os.environ)
    e["PYTHONPATH"] = str(ENGINE / "vendor")
    if TEXBIN:
        e["PATH"] = TEXBIN + os.pathsep + e.get("PATH", "")
    return e


def pdf_pages(path: Path) -> int:
    """用 ghostscript 数 PDF 页数（xelatex 的对象流用正则数不准）。"""
    if not GS:
        return -1
    proc = subprocess.run(
        [GS, "-q", "-dNODISPLAY", "-dNOSAFER",
         "-c", f"({path}) (r) file runpdfbegin pdfpagecount = quit"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return int((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return -1


def overfull_warnings(log_path: Path) -> list[str]:
    r"""xelatex 日志里的 Overfull \hbox/\vbox：真实横向/纵向溢出，不是 Underfull。"""
    if not log_path.is_file():
        return [f"缺少编译日志：{log_path.name}"]
    return [ln.strip() for ln in log_path.read_text(
                encoding="utf-8", errors="replace").splitlines()
            if re.match(r"^Overfull \\[hv]box\b", ln.strip())]


def pdf_lines(path: Path) -> list[dict]:
    r"""PDF 第一页的文本行：x 是文本矩阵原点（排版起点，不是墨迹外沿）。"""
    if not HAS_PYPDF:
        return []
    page = PdfReader(str(path)).pages[0]
    raw: list[tuple[float, float, float, str]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        text = " ".join((text or "").split())
        if text:
            raw.append((float(tm[4]), float(tm[5]), float(font_size or 0), text))

    page.extract_text(visitor_text=visitor)
    groups: list[dict] = []
    for x, y, size, text in sorted(raw, key=lambda item: (-item[1], item[0])):
        for group in groups:
            if abs(group["y"] - y) <= 0.6:
                group["items"].append((x, size, text))
                break
        else:
            groups.append({"y": y, "items": [(x, size, text)]})
    lines = []
    for group in groups:
        items = sorted(group["items"], key=lambda item: item[0])
        x, size, _ = items[0]
        lines.append({"x": x, "y": group["y"], "size": size,
                      "text": " ".join(item[2] for item in items)})
    return lines


def document_title_rows(tex: str) -> list[tuple[bool, str]]:
    r"""按文档顺序取全部条目首行标题（栏目 + 普通，不含续行/作者/栏目头）。"""
    rows: list[tuple[bool, str]] = []
    at_entry_start = True
    for raw in tex.splitlines():
        line = raw.strip()
        if line.startswith("\\vdistance") or line.startswith("\\\\["):
            at_entry_start = True
            continue
        body = None
        in_column = True
        column_prefix = "{\\large ~~~~ "
        if line.startswith(column_prefix):
            body = line[len(column_prefix):]
            if body.endswith("}\\\\"):
                body = body[:-3]
        elif line.startswith("\\large ") and "\\bf" not in line and "~~~~" not in line:
            in_column = False
            body = line[len("\\large "):]
            if body.endswith("\\\\"):
                body = body[:-2]
        if at_entry_start and body and body.strip():
            rows.append((in_column, body.strip()))
        if body is not None:
            at_entry_start = False
    return rows


def first_title_rows(tex: str, in_column: bool) -> list[str]:
    r"""从生成的目录 tex 取每条目首行标题（不含续行/作者/栏目头）。"""
    return [text for kind, text in document_title_rows(tex) if kind == in_column]


def indent_report(lines: list[dict], titles: list[str],
                  order: list[str] | None = None) -> tuple[list[float], str]:
    r"""每条目首行标题 vs 全部标题续行/作者行的排版起点差。

    返回 (栏目/普通条目的首行起点列表, 错误说明)。容差 0.25pt 只用于吸收 PDF
    坐标舍入；斜体字形外探不参与比较，因为量的是 tm[4] 而不是墨迹包围盒。
    order 是文档顺序的全部条目首行标题，用于确定本条目的下边界（栏目条目后面
    可能紧跟普通条目）。
    """
    order = order or titles
    order_ys: list[float] = []
    for title in order:
        key = re.sub(r"\s+", "", title)
        found = next((ln for ln in lines
                      if re.sub(r"\s+", "", ln["text"]) == key), None)
        if found is not None:
            order_ys.append(found["y"])

    anchors: list[float] = []
    for title in titles:
        key = re.sub(r"\s+", "", title)
        match = next((ln for ln in lines
                      if re.sub(r"\s+", "", ln["text"]) == key), None)
        if match is None:
            return [], f"PDF 里找不到标题首行：{title!r}"
        below = [y for y in order_ys if y < match["y"] - 0.6]
        next_y = max(below) if below else None
        bottom = next_y if next_y is not None else min(ln["y"] for ln in lines) - 1
        top = match["y"]
        rows = [ln for ln in lines
                if ln["size"] > 10.5 and top + 0.6 >= ln["y"] > bottom + 0.6]
        authors = [ln for ln in lines
                   if ln["size"] < 10.5 and top - 0.6 > ln["y"] > bottom + 0.6
                   and ln["x"] < 200]
        if not authors:
            return [], f"PDF 里找不到作者行：{title!r}"
        bad = [ln for ln in rows + authors if abs(ln["x"] - match["x"]) > 0.25]
        if bad:
            detail = [(round(ln["x"], 3), round(ln["y"], 3), ln["text"][:40])
                      for ln in [match] + bad]
            return [], f"起点不一致：{detail}"
        anchors.append(match["x"])
    return anchors, ""


def push_entries(tex: str):
    from slcn.pushcopy import collect as PC
    body = PC._tabularx_body(tex)
    return PC._parse_column_and_entries(body, True)[1]


def multicolumn_body(line: str) -> str | None:
    r"""取 `\multicolumn{n}{spec}{body}` 的 body（花括号配对，body 可含嵌套组）。"""
    m = re.search(r"\\multicolumn\s*\{\s*2\s*\}\s*\{(?:[^{}]|\{[^{}]*\})*\}", line)
    if not m:
        return None
    i = m.end()
    if i >= len(line) or line[i] != "{":
        return None
    depth = 0
    start = i + 1
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return line[start:i]
        i += 1
    return None


def run_toc(issue: Path, result_path: Path):
    cmd = [sys.executable, str(ENGINE / "toc.py"),
           "--report", str(issue / "信息检查_ANON.txt"),
           "--place"]
    if CAN_COMPILE:
        cmd.append("--compile")
    cmd.extend(["--result-json", str(result_path)])
    proc = subprocess.run(
        cmd,
        cwd=str(ENGINE), env=env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900)
    out = (proc.stdout or "") + (proc.stderr or "")
    result = {}
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except ValueError:
            result = {}
    return proc, out, result


def host_line_of(text: str) -> str:
    return next((ln for ln in text.splitlines() if "主持人" in ln), "")


def braces_balanced(line: str) -> bool:
    return line.count("{") == line.count("}")


def newest_intermediate(issue: Path) -> Path | None:
    files = sorted((issue / ".toolbox-tmp").glob("Chinese Content_*.tex"),
                   key=lambda p: p.stat().st_mtime_ns)
    return files[-1] if files else None


def make_issue(root: Path, *, column_kind: str = "专栏",
               name: str = ISSUE_NAME, report: str = REPORT) -> Path:
    """在临时目录内合成匿名期目录（不读任何外部夹具）。"""
    issue = root / "issue" / name
    toc_dir = issue / "000 Table of Contents"
    toc_dir.mkdir(parents=True)
    (issue / "信息检查_ANON.txt").write_text(report, encoding="utf-8")
    settings = dict(SETTINGS)
    settings["columnKind"] = column_kind
    settings["columnArticles"] = list(SETTINGS["columnArticles"][:4])
    (issue / "目录设置.json").write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (toc_dir / "contents.tex").write_text(EN_TEMPLATE, encoding="utf-8")
    return issue


def main() -> None:
    from slcn import toctemplate as TT

    print(f"工具发现：xelatex={XELATEX or '缺失'}；ghostscript={GS or '缺失'}")
    compile_skip = "缺 xelatex，跳过真实编译/PDF 检查"
    pages_skip = ("缺 xelatex，跳过真实编译/PDF 检查"
                  if not CAN_COMPILE else "缺 ghostscript，跳过 PDF 页数检查")

    with tempfile.TemporaryDirectory(prefix="r3-toc-host-") as tmp:
        issue = make_issue(Path(tmp))
        result_path = Path(tmp) / "result.json"
        proc, out, result = run_toc(issue, result_path)

        print("── (a) rc=0、中英 PDF 各 1 页 ──")
        if CAN_COMPILE:
            check("(a1) toc.py 生成/编译 rc=0", proc.returncode == 0,
                  f"rc={proc.returncode}\n{out[-2000:]}")
        else:
            skip("(a1) toc.py 生成/编译 rc=0", compile_skip)
        toc_dir = issue / "000 Table of Contents"
        zh_tex = toc_dir / "Chinese Content.tex"
        en_tex = toc_dir / "contents.tex"
        zh_pdf = toc_dir / "Chinese Content.pdf"
        en_pdf = toc_dir / "contents.pdf"
        if CAN_COMPILE:
            check("(a2) 中文/英文 tex 与 PDF 均已落位",
                  all(p.is_file() for p in (zh_tex, en_tex, zh_pdf, en_pdf)),
                  str([(p.name, p.exists()) for p in (zh_tex, en_tex, zh_pdf, en_pdf)]))
        else:
            skip("(a2) 中文/英文 tex 与 PDF 均已落位", compile_skip)
        if CAN_COMPILE and CAN_COUNT_PAGES:
            zh_pages = pdf_pages(zh_pdf)
            en_pages = pdf_pages(en_pdf)
            check("(a3) 中文 PDF 1 页", zh_pages == 1, f"pages={zh_pages}")
            check("(a4) 英文 PDF 1 页", en_pages == 1, f"pages={en_pages}")
        else:
            skip("(a3) 中文 PDF 1 页", pages_skip)
            skip("(a4) 英文 PDF 1 页", pages_skip)
        if CAN_COMPILE:
            check("(a5) 结果 JSON pages.zh/en 均为 1",
                  (result.get("pages") or {}).get("zh") == 1
                  and (result.get("pages") or {}).get("en") == 1,
                  repr(result.get("pages")))
        else:
            skip("(a5) 结果 JSON pages.zh/en 均为 1", compile_skip)

        zh = zh_tex.read_text(encoding="utf-8") if zh_tex.is_file() else ""
        lines = zh.splitlines()
        host_line = next((ln for ln in lines if "主持人" in ln), "")
        mline = next((ln for ln in lines if "\\multicolumn" in ln), "")

        print("\n── (b) 主持人行跨作者列+页码列、右对齐 ──")
        check("(b1) 主持人行以 &\\multicolumn{2}{@{}r@{}}{ 开头",
              host_line.startswith("&\\multicolumn{2}{@{}r@{}}{"), repr(host_line))
        check("(b2) 主持人行 = multicolumn 行", host_line == mline, repr(host_line))
        body = multicolumn_body(host_line)
        check("(b3) multicolumn 组内包含「主持人：匿名主持人」",
              body is not None and "主持人：" in body and "匿名主持人" in body,
              repr(body))
        check("(b4) 主持人不再落在普通作者格 &\\textit",
              not re.match(r"^&\s*\\textit", host_line), repr(host_line))
        check("(b5) 长主持人格内换行能力保留（嵌套 tabular[t]）",
              body is not None and "\\begin{tabular}[t]{@{}l@{}}" in body,
              repr(body))

        print("\n── (c) 普通条目行不变：4 条栏目 ~~~~ / 6 条普通 ──")
        column_titles = [ln for ln in lines if ln.startswith("{\\large ~~~~ ")]
        plain_titles = [ln for ln in lines
                        if ln.startswith("{\\large ") and "~~~~" not in ln
                        and "专栏" not in ln]
        check("(c1) 栏目条目仍 4 条（前 4 篇带 ~~~~）",
              len(column_titles) == 4, repr(column_titles))
        check("(c2) 普通条目仍 6 条且无 ~~~~",
              len(plain_titles) == 6, repr(plain_titles))
        check("(c3) 普通条目作者列仍是 &\\textit{\\large（无 multicolumn）",
              all(f"&\\textit{{\\large {t}" in zh
                  for t in ("匿名戊", "匿名己", "匿名庚", "匿名辛", "匿名壬", "匿名癸")),
              "缺少普通作者行")

        print("\n── (c-en) 英文栏目/普通条目：标题与作者共用物理缩进基准 ──")
        indent_issue = make_issue(Path(tmp), name="2026年第5期(997)",
                                  report=REPORT_INDENT)
        indent_result_path = Path(tmp) / "indent.json"
        indent_proc, indent_out, indent_result = run_toc(
            indent_issue, indent_result_path)
        indent_dir = indent_issue / "000 Table of Contents"
        indent_tex = indent_dir / "contents.tex"
        indent_pdf = indent_dir / "contents.pdf"
        indent_text = (indent_tex.read_text(encoding="utf-8")
                       if indent_tex.is_file() else "")
        col_titles = first_title_rows(indent_text, True)
        plain_titles = first_title_rows(indent_text, False)
        if CAN_COMPILE:
            indent_overfull = overfull_warnings(indent_dir / "contents.log")
            check("(c-en1) 对齐夹具有真实编译：rc=0、2 栏目 + 2 普通、PDF 1 页、无溢出",
                  indent_proc.returncode == 0 and len(col_titles) == 2
                  and len(plain_titles) == 2
                  and (indent_result.get("pages") or {}).get("en") == 1
                  and not indent_overfull,
                  f"rc={indent_proc.returncode} column={len(col_titles)} "
                  f"plain={len(plain_titles)} pages={indent_result.get('pages')} "
                  f"overfull={indent_overfull!r}\n{indent_out[-1000:]}")
        else:
            skip("(c-en1) 对齐夹具有真实编译：rc=0、2 栏目 + 2 普通、PDF 1 页、无溢出",
                 "缺 xelatex，跳过真实编译/PDF 检查")
        check("(c-en2) 夹具覆盖两行标题与多行作者",
              "{\\large \\phantom{~~~~ }" in indent_text
              and "\\textit{\\phantom{" in indent_text,
              "缺少标题续行或作者续行")
        if CAN_COMPILE and HAS_PYPDF and len(col_titles) == 2 and len(plain_titles) == 2:
            indent_lines = pdf_lines(indent_pdf)
            all_titles = [text for _, text in document_title_rows(indent_text)]
            col_axes, col_err = indent_report(indent_lines, col_titles, all_titles)
            plain_axes, plain_err = indent_report(indent_lines, plain_titles, all_titles)
            check("(c-en3) 英文栏目：标题续行/作者全部行与标题首行同一排版起点",
                  not col_err and len(col_axes) == 2, col_err or repr(col_axes))
            check("(c-en4) 英文普通条目：标题/作者起点一致且不继承栏目缩进",
                  not plain_err and len(plain_axes) == 2
                  and max(plain_axes) - min(plain_axes) <= 0.25
                  and (not col_axes or max(plain_axes) < min(col_axes) - 5),
                  plain_err or f"plain={plain_axes!r} column={col_axes!r}")
        else:
            why = ("缺 xelatex，跳过真实编译/PDF 起点检查" if not CAN_COMPILE
                   else "缺 pypdf，跳过 PDF 起点检查")
            skip("(c-en3) 英文栏目：标题续行/作者全部行与标题首行同一排版起点", why)
            skip("(c-en4) 英文普通条目：标题/作者起点一致且不继承栏目缩进", why)
        indent_entries = push_entries(indent_text) if indent_text else []
        check("(c-en5) 推送解析仍识别前 2 篇为栏目、后 2 篇为普通",
              [entry.is_column for entry in indent_entries]
              == [True, True, False, False],
              repr([entry.is_column for entry in indent_entries]))
        check("(c-en6) 新作者前缀不污染推送作者名",
              len(indent_entries) == 4
              and all("phantom" not in name.lower()
                      for entry in indent_entries for name in entry.authors)
              and "B. Gamma Maximilian" in " ".join(indent_entries[1].authors)
              and "C. Delta Bartholomew" in " ".join(indent_entries[1].authors),
              repr([entry.authors for entry in indent_entries[:2]]))

        album_issue = make_issue(Path(tmp), column_kind="专辑",
                                 name="2026年第5期(998)", report=REPORT_ALBUM)
        album_result_path = Path(tmp) / "album.json"
        album_proc, album_out, album_result = run_toc(album_issue, album_result_path)
        album_dir = album_issue / "000 Table of Contents"
        album_tex = album_dir / "contents.tex"
        album_pdf = album_dir / "contents.pdf"
        album_text = (album_tex.read_text(encoding="utf-8")
                      if album_tex.is_file() else "")
        album_titles = first_title_rows(album_text, True)
        if CAN_COMPILE:
            album_overfull = overfull_warnings(album_dir / "contents.log")
            check("(c-en7) 英文专辑：真实编译 rc=0、3 篇全部栏目、PDF 1 页、无溢出",
                  album_proc.returncode == 0 and len(album_titles) == 3
                  and (album_result.get("pages") or {}).get("en") == 1
                  and not album_overfull,
                  f"rc={album_proc.returncode} titles={len(album_titles)} "
                  f"pages={album_result.get('pages')} overfull={album_overfull!r}"
                  f"\n{album_out[-1000:]}")
        else:
            skip("(c-en7) 英文专辑：真实编译 rc=0、3 篇全部栏目、PDF 1 页、无溢出",
                 "缺 xelatex，跳过真实编译/PDF 检查")
        if CAN_COMPILE and HAS_PYPDF and len(album_titles) == 3:
            album_axes, album_err = indent_report(pdf_lines(album_pdf), album_titles)
            check("(c-en8) 英文专辑：标题续行/作者全部行与标题首行同一排版起点",
                  not album_err and len(album_axes) == 3,
                  album_err or repr(album_axes))
        else:
            skip("(c-en8) 英文专辑：标题续行/作者全部行与标题首行同一排版起点",
                 "缺 xelatex/pypdf 或专辑标题数不为 3")
        album_entries = push_entries(album_text) if album_text else []
        check("(c-en9) 专辑推送解析仍把 3 篇全部识别为栏目",
              [entry.is_column for entry in album_entries] == [True] * 3,
              repr([entry.is_column for entry in album_entries]))

        print("\n── (d) 规范三列列定义不变 ──")
        spec_line = next((ln for ln in lines if "p{0.26\\textwidth}" in ln), "")
        check("(d1) 列定义仍是 X @2em p{0.26\\textwidth} @1.5em r",
              spec_line == (
                  "{@{}>{\\raggedright\\arraybackslash}X@{\\hspace{2em}}"
                  ">{\\raggedleft\\arraybackslash}p{0.26\\textwidth}"
                  "@{\\hspace{1.5em}}r@{}}"), repr(spec_line))

        print("\n── (e) multicolumn 主持人行：同 host 幂等 / 换 host 替换 ──")
        new_form = "&\\multicolumn{2}{@{}r@{}}{\\textit{\\large 主持人：匿名主持人}}"
        once = TT._replace_host_with_optional_break(new_form, "匿名主持人")
        twice = TT._replace_host_with_optional_break(once, "匿名主持人")
        check("(e1) 同 host → 原样返回（不吞外层花括号）",
              once == new_form, repr(once))
        check("(e2) 连续替换两次 == 替换一次", twice == once,
              f"once={once!r} twice={twice!r}")
        check("(e3) 主持人行花括号配对", braces_balanced(once), repr(once))
        changed = TT._replace_host_with_optional_break(new_form, "匿名第二主持")
        check("(e4) 旧 host A → 新 host B：替换且保留外层 multicolumn",
              "匿名第二主持" in changed and "匿名主持人" not in changed
              and changed.startswith("&\\multicolumn{2}{@{}r@{}}{")
              and changed.endswith("}}") and braces_balanced(changed),
              repr(changed))
        check("(e5) 新 host B 再替换一次 == 自身",
              TT._replace_host_with_optional_break(changed, "匿名第二主持") == changed,
              repr(changed))
        long_body = TT._host_cell_content("匿名旧长主持人")
        long_form = ("&\\multicolumn{2}{@{}r@{}}{\\textit{\\large "
                     + long_body + "}}")
        long_changed = TT._replace_host_with_optional_break(
            long_form, "匿名新长主持人")
        check("(e6) 长 host 嵌套 tabular 也能换 host，外层括号保留",
              "\\begin{tabular}[t]{@{}l@{}}" in long_form
              and "匿名新长主持人" in long_changed
              and "匿名旧长主持人" not in long_changed
              and "\\begin{tabular}[t]{@{}l@{}}" in long_changed
              and long_changed.startswith("&\\multicolumn{2}{@{}r@{}}{")
              and long_changed.endswith("}}") and braces_balanced(long_changed),
              f"form={long_form!r} changed={long_changed!r}")

        print("\n── (f) 第二代：旧式→新式后再生成一次 ──")
        check("(f0) 第一代由内置模板生成（旧式主持人）",
              result.get("templateSource") == "内置模板",
              repr(result.get("templateSource")))
        result2_path = Path(tmp) / "result2.json"
        proc2, out2, result2 = run_toc(issue, result2_path)
        if CAN_COMPILE:
            check("(f1) 第二次生成/编译 rc=0", proc2.returncode == 0,
                  f"rc={proc2.returncode}\n{out2[-2000:]}")
            check(r"(f2) 第二次日志无 File ended while scanning use of \TX@get@body",
                  "File ended while scanning use of \\TX@get@body" not in out2,
                  out2[-2000:])
            check("(f3) 第二次 result-json 落盘且 pages 1/1",
                  result2_path.is_file()
                  and (result2.get("pages") or {}).get("zh") == 1
                  and (result2.get("pages") or {}).get("en") == 1,
                  f"exists={result2_path.is_file()} pages={result2.get('pages')}")
        else:
            skip("(f1) 第二次生成/编译 rc=0", compile_skip)
            skip(r"(f2) 第二次日志无 File ended while scanning use of \TX@get@body",
                 compile_skip)
            skip("(f3) 第二次 result-json 落盘且 pages 1/1", compile_skip)
        check("(f4) 第二次模板来源=本期成品（继承第一代产物）",
              result2.get("templateSource") == "本期成品",
              repr(result2.get("templateSource")))
        if CAN_COMPILE and CAN_COUNT_PAGES:
            check("(f5) 两次编译后中英 PDF 仍各 1 页",
                  pdf_pages(zh_pdf) == 1 and pdf_pages(en_pdf) == 1,
                  f"zh={pdf_pages(zh_pdf)} en={pdf_pages(en_pdf)}")
        else:
            skip("(f5) 两次编译后中英 PDF 仍各 1 页", pages_skip)
        zh2 = zh_tex.read_text(encoding="utf-8") if zh_tex.is_file() else ""
        host2 = host_line_of(zh2)
        check("(f6) 第二代主持人行与第一代完全一致",
              host2 == host_line and host2.startswith(
                  "&\\multicolumn{2}{@{}r@{}}{"), f"got={host2!r}")
        check("(f7) 落位成品的主持人行花括号配对",
              braces_balanced(host2), repr(host2))
        tmp_tex = newest_intermediate(issue)
        tmp_text = (tmp_tex.read_text(encoding="utf-8")
                    if tmp_tex is not None else "")
        check("(f8) 第二代中间 tex 的主持人行花括号配对（失败根源不在落位成品）",
              bool(tmp_text) and braces_balanced(host_line_of(tmp_text)),
              f"file={tmp_tex} host={host_line_of(tmp_text)!r}")

        print("\n── (g) 主持人 A→B：继承本期成品时必须替换旧 host ──")
        settings_path = issue / "目录设置.json"
        settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
        settings_data["columnHost"] = "匿名第二主持"
        settings_path.write_text(
            json.dumps(settings_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        result3_path = Path(tmp) / "result3.json"
        proc3, out3, result3 = run_toc(issue, result3_path)
        if CAN_COMPILE:
            check("(g1) A→B 生成/编译 rc=0", proc3.returncode == 0,
                  f"rc={proc3.returncode}\n{out3[-2000:]}")
            check(r"(g2) A→B 日志无 File ended while scanning use of \TX@get@body",
                  "File ended while scanning use of \\TX@get@body" not in out3,
                  out3[-2000:])
            check("(g3) A→B 落盘且 pages 1/1",
                  result3_path.is_file()
                  and (result3.get("pages") or {}).get("zh") == 1
                  and (result3.get("pages") or {}).get("en") == 1,
                  f"exists={result3_path.is_file()} pages={result3.get('pages')}")
        else:
            skip("(g1) A→B 生成/编译 rc=0", compile_skip)
            skip(r"(g2) A→B 日志无 File ended while scanning use of \TX@get@body",
                 compile_skip)
            skip("(g3) A→B 落盘且 pages 1/1", compile_skip)
        check("(g4) A→B 仍继承本期成品",
              result3.get("templateSource") == "本期成品",
              repr(result3.get("templateSource")))
        zh3 = zh_tex.read_text(encoding="utf-8") if zh_tex.is_file() else ""
        host3 = host_line_of(zh3)
        check("(g5) Chinese Content.tex 含新 host B、不含旧 host A",
              "匿名第二主持" in zh3 and "匿名主持人" not in zh3,
              f"host={host3!r}")
        check("(g6) A→B 主持人行仍是 multicolumn 且括号配对",
              host3.startswith("&\\multicolumn{2}{@{}r@{}}{")
              and braces_balanced(host3), repr(host3))

        result4_path = Path(tmp) / "result4.json"
        proc4, out4, result4 = run_toc(issue, result4_path)
        zh4 = zh_tex.read_text(encoding="utf-8") if zh_tex.is_file() else ""
        host4 = host_line_of(zh4)
        if CAN_COMPILE:
            check("(g7) host B 再生成一次 rc=0 且无 \\TX@get@body",
                  proc4.returncode == 0
                  and "File ended while scanning use of \\TX@get@body" not in out4,
                  f"rc={proc4.returncode}\n{out4[-2000:]}")
        else:
            skip("(g7) host B 再生成一次 rc=0 且无 \\TX@get@body", compile_skip)
        g8_ok = (host4 == host3 and "匿名第二主持" in host4
                 and "匿名主持人" not in host4)
        if not CAN_COMPILE:
            g8_ok = g8_ok and proc4.returncode == 0
        check("(g8) host B 再生成一次主持人行不变（换 host 后仍幂等）",
              g8_ok, f"rc={proc4.returncode} got={host4!r}")

    if _fails:
        print(f"\n失败 {len(_fails)} 项：{_fails}"
              + (f"｜SKIP {len(_skips)} 项" if _skips else ""))
        sys.exit(1)
    print(f"\n全部 {_passed} 项断言通过"
          + (f"｜SKIP {len(_skips)} 项（缺编译工具）" if _skips else ""))


if __name__ == "__main__":
    main()
