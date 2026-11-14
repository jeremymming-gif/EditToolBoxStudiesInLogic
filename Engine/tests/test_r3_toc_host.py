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
  (d) 规范三列列定义不变（X / p{0.26\textwidth} / r、2em / 1.5em）。

本文件是独立脚本（同 Engine/tests 其余套件风格），失败退出码非 0。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

ISSUE_NAME = "2026年第5期(999)"
GS = "/usr/local/bin/gs"
TEXBIN = "/Library/TeX/texbin"

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


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}  {detail}")


def env() -> dict[str, str]:
    e = dict(os.environ)
    e["PYTHONPATH"] = str(ENGINE / "vendor")
    e["PATH"] = TEXBIN + os.pathsep + e.get("PATH", "")
    return e


def pdf_pages(path: Path) -> int:
    """用 ghostscript 数 PDF 页数（xelatex 的对象流用正则数不准）。"""
    proc = subprocess.run(
        [GS, "-q", "-dNODISPLAY", "-dNOSAFER",
         "-c", f"({path}) (r) file runpdfbegin pdfpagecount = quit"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return int((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return -1


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
    proc = subprocess.run(
        [sys.executable, str(ENGINE / "toc.py"),
         "--report", str(issue / "信息检查_ANON.txt"),
         "--place", "--compile",
         "--result-json", str(result_path)],
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


def make_issue(root: Path) -> Path:
    """在临时目录内合成匿名期目录（不读任何外部夹具）。"""
    issue = root / "issue" / ISSUE_NAME
    toc_dir = issue / "000 Table of Contents"
    toc_dir.mkdir(parents=True)
    (issue / "信息检查_ANON.txt").write_text(REPORT, encoding="utf-8")
    settings = dict(SETTINGS)
    settings["columnArticles"] = list(SETTINGS["columnArticles"][:4])
    (issue / "目录设置.json").write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (toc_dir / "contents.tex").write_text(EN_TEMPLATE, encoding="utf-8")
    return issue


def main() -> None:
    from slcn import toctemplate as TT

    with tempfile.TemporaryDirectory(prefix="r3-toc-host-") as tmp:
        issue = make_issue(Path(tmp))
        result_path = Path(tmp) / "result.json"
        proc, out, result = run_toc(issue, result_path)

        print("── (a) rc=0、中英 PDF 各 1 页 ──")
        check("(a1) toc.py 生成/编译 rc=0", proc.returncode == 0,
              f"rc={proc.returncode}\n{out[-2000:]}")
        toc_dir = issue / "000 Table of Contents"
        zh_tex = toc_dir / "Chinese Content.tex"
        en_tex = toc_dir / "contents.tex"
        zh_pdf = toc_dir / "Chinese Content.pdf"
        en_pdf = toc_dir / "contents.pdf"
        check("(a2) 中文/英文 tex 与 PDF 均已落位",
              all(p.is_file() for p in (zh_tex, en_tex, zh_pdf, en_pdf)),
              str([(p.name, p.exists()) for p in (zh_tex, en_tex, zh_pdf, en_pdf)]))
        zh_pages = pdf_pages(zh_pdf)
        en_pages = pdf_pages(en_pdf)
        check("(a3) 中文 PDF 1 页", zh_pages == 1, f"pages={zh_pages}")
        check("(a4) 英文 PDF 1 页", en_pages == 1, f"pages={en_pages}")
        check("(a5) 结果 JSON pages.zh/en 均为 1",
              (result.get("pages") or {}).get("zh") == 1
              and (result.get("pages") or {}).get("en") == 1,
              repr(result.get("pages")))

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

        print("\n── (d) 规范三列列定义不变 ──")
        spec_line = next((ln for ln in lines if "p{0.26\\textwidth}" in ln), "")
        check("(d1) 列定义仍是 X @2em p{0.26\\textwidth} @1.5em r",
              spec_line == (
                  "{@{}>{\\raggedright\\arraybackslash}X@{\\hspace{2em}}"
                  ">{\\raggedleft\\arraybackslash}p{0.26\\textwidth}"
                  "@{\\hspace{1.5em}}r@{}}"), repr(spec_line))

        print("\n── (e) 已是 multicolumn 的主持人行替换幂等 ──")
        new_form = "&\\multicolumn{2}{@{}r@{}}{\\textit{\\large 主持人：匿名主持人}}"
        once = TT._replace_host_with_optional_break(new_form, "匿名主持人")
        twice = TT._replace_host_with_optional_break(once, "匿名主持人")
        check("(e1) 已是 multicolumn → 原样返回（不吞外层花括号）",
              once == new_form, repr(once))
        check("(e2) 连续替换两次 == 替换一次", twice == once,
              f"once={once!r} twice={twice!r}")
        check("(e3) 主持人行花括号配对", braces_balanced(once), repr(once))

        print("\n── (f) 第二代：旧式→新式后再生成一次 ──")
        check("(f0) 第一代由内置模板生成（旧式主持人）",
              result.get("templateSource") == "内置模板",
              repr(result.get("templateSource")))
        result2_path = Path(tmp) / "result2.json"
        proc2, out2, result2 = run_toc(issue, result2_path)
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
        check("(f4) 第二次模板来源=本期成品（继承第一代产物）",
              result2.get("templateSource") == "本期成品",
              repr(result2.get("templateSource")))
        check("(f5) 两次编译后中英 PDF 仍各 1 页",
              pdf_pages(zh_pdf) == 1 and pdf_pages(en_pdf) == 1,
              f"zh={pdf_pages(zh_pdf)} en={pdf_pages(en_pdf)}")
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

    if _fails:
        print(f"\n失败 {len(_fails)} 项：{_fails}")
        sys.exit(1)
    print(f"\n全部 {_passed} 项断言通过")


if __name__ == "__main__":
    main()
