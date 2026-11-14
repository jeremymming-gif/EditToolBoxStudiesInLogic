#!/usr/bin/env python3
"""R3 缺陷 2/3：双语目录栏目分组 + 末条底边框 + 标题反斜杠。

独立断言（合成匿名期目录；不读真实稿件）：
  A `render_docx._issue_table_rows` bi 模式：英文/中文各 10 条，
    前 4 条（按 TocEntry.is_column，不是硬编码）分别带 col/col/col/col_last，
    后 6 条无 tag。
  B `dirpush --issue --out --with-en-toc` 生成 docx：
    · 24 个 w:tr（en_section + en_column + 10 en + zh_section + host2 + 10 zh）；
    · 第 4 条栏目行（英文/中文各一条）标题/作者单元格 tcBorders **没有** bottom 元素
      （不是 bottom=nil）；其余 9 条普通/前 3 条栏目行保持原型 bottom=nil；
    · 英文标题单元格 0 个字面反斜杠；
    · 中文 `冒号标题：\\\\后半` 冒号后的 `\\\\` 不再分段（摘要块/目录条目各 1 段）；
    · `\\mytitle{主\\\\——副}` 仍分两段。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKOUT = os.path.dirname(ENGINE)
VENDOR = os.path.join(ENGINE, "vendor")
sys.path.insert(0, ENGINE)
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

try:
    from lxml import etree
except ImportError:  # 兜底：只读外部 vendor（任务书给定路径）
    sys.path.insert(0, "/Users/zhangwenming/Documents/GitHub/EditToolBoxStudiesInLogic/Engine/vendor")
    from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
PY = sys.executable
ENV = dict(os.environ, PYTHONPATH=ENGINE + os.pathsep + VENDOR)

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}")
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def w(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


EN_TITLES = [
    "Column Paper One", "Column Paper Two", "Column Paper Three", "Column Paper Four",
    "Plain Paper Five", "Plain Paper Six", "Plain Paper Seven", "Plain Paper Eight",
    "Plain Paper Nine", "Plain Paper Ten",
]
EN_AUTHORS = [f"Anon Author {i}" for i in range(1, 11)]
ZH_TITLES = [
    "栏目标题一", "冒号目录标题：\\\\后半", "栏目标题三", "栏目标题四",
    "普通标题五", "普通标题六", "普通标题七", "普通标题八",
    "普通标题九", "普通标题十",
]
ZH_AUTHORS = [f"匿名作者{i}" for i in range(1, 11)]


def make_issue(root: str) -> str:
    """10 篇匿名文章 + 4 栏目 + 6 普通；中英文目录各 10 条。"""
    d = os.path.join(root, "2026年第5期(90)")
    toc = os.path.join(d, "000 Table of Contents")
    hdr = ("\\begin{flushright}\n\\heiti{逻辑学研究（双月刊）\\\\\n"
           "第\\ 19 卷，第\\ 5 期，总第\\ 90 期}}\\\\\n"
           "\\heiti{2026 年第\\ 5 期，10月\\ 18 日出版}\n\\end{flushright}\n")
    host = "{\\large \\bf{专栏：匿名专栏}}&&\\textit{\\large 主持人：匿名主持}\n\\\\[3.0 ex]\n"
    body: list[str] = []
    for i, (t, a) in enumerate(zip(ZH_TITLES, ZH_AUTHORS)):
        prefix = "~~~~ " if i < 4 else ""
        body.append("{\\large " + prefix + t + "}\n"
                    "&\\textit{\\large " + prefix + a + "}\n& " + str(i + 1) + "\\\\\n"
                    "\\vdistance\n")
    zh = ("\\documentclass{ctexart}\n\\begin{document}\n" + hdr
          + "\\vspace{4ex}\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{-0.3em}}r}\n"
          + host + "%中间主体\n" + "".join(body) + "\\end{tabularx}\n\\end{document}\n")
    w(os.path.join(toc, "Chinese Content.tex"), zh)

    en_body: list[str] = []
    for i, (t, a) in enumerate(zip(EN_TITLES, EN_AUTHORS)):
        if i < 4:
            en_body.append("{\\large ~~~~ " + t + " part A}\\\\\n"
                           "{\\large \\phantom{~~~~ }and part B}\\\\\n"
                           "\\textit{~~~~ " + a + "}\n& " + str(i + 1) + "\\\\\n"
                           "\\vdistance\n")
        else:
            en_body.append("\\large " + t + "\\\\\n"
                           "\\textit{{" + a + "}}\n& " + str(i + 1) + "\\\\\n"
                           "\\vdistance\n")
    en = ("\\documentclass{ctexart}\n\\begin{document}\n"
          "\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n"
          "\\large  \\bf{Column: Anonymous Column}\n\\\\[6.2mm]\n%中间主体\n"
          + "".join(en_body) + "\\end{tabularx}\n\\end{document}\n")
    w(os.path.join(toc, "contents.tex"), en)

    for i in range(1, 11):
        if i == 1:
            zh_title = "主\\\\——副"
        elif i == 2:
            zh_title = "冒号标题：\\\\后半"
        else:
            zh_title = ZH_TITLES[i - 1]
        name = f"Fake_{i}"
        tex = ("\\documentclass{article}\n\\begin{document}\n"
               f"\\newcommand{{\\mytitle}}{{{zh_title}}}\n"
               f"\\newcommand{{\\mytitleEN}}{{{EN_TITLES[i - 1]}}}\n"
               f"\\newcommand{{\\myfirstauthor}}{{{ZH_AUTHORS[i - 1]}}}\n"
               "\\newcommand{\\myfirstaffiliation}{匿名单位}\n"
               f"\\newcommand{{\\myabstract}}{{匿名摘要{i}。}}\n"
               "\\end{document}\n")
        w(os.path.join(d, f"{i:02d} {name}", f"{name}.tex"), tex)
    return d


def row_tag(row) -> str:
    return row[2] if len(row) > 2 else ""


def cell_text(tc) -> str:
    return "".join(tc.itertext())


def cell_borders(tc):
    return tc.find("./w:tcPr/w:tcBorders", NS)


def border_val(tc, side: str):
    b = cell_borders(tc)
    if b is None:
        return "<no-tcBorders>"
    el = b.find("w:" + side, NS)
    return None if el is None else el.get("{%s}val" % W)


def has_bottom(tc) -> bool:
    b = cell_borders(tc)
    return b is not None and b.find("w:bottom", NS) is not None


def para_texts_after(body, marker: str) -> list[str]:
    out: list[str] = []
    seen = False
    for ch in body:
        if ch.tag != "{%s}p" % W:
            continue
        txt = "".join(ch.itertext())
        if marker in txt:
            seen = True
            continue
        if seen:
            out.append(txt)
    return out


work = tempfile.mkdtemp(prefix="r3_docx_group_text_")
try:
    from slcn.pushcopy import collect as PC
    from slcn.pushcopy import render_docx as PR

    src = make_issue(work)
    out_root = os.path.join(work, "out")
    issue = PC.collect_issue(src, with_en_toc=True, out_root=out_root)

    # ── A. 分组 tag 只来自 is_column ─────────────────────────────────
    rows = PR._issue_table_rows(issue, "bi", annual=False)
    en_rows = [r for r in rows if r[0] == "en_entry"]
    zh_rows = [r for r in rows if r[0] == "zh_entry"]
    want = ["col", "col", "col", "col_last"] + [""] * 6
    check("(A1) bi：英文 10 条，前 4 条 col/col/col/col_last，其余无 tag",
          len(en_rows) == 10 and [row_tag(r) for r in en_rows] == want,
          f"tags={[row_tag(r) for r in en_rows]}")
    check("(A2) bi：中文 10 条，前 4 条 col/col/col/col_last，其余无 tag",
          len(zh_rows) == 10 and [row_tag(r) for r in zh_rows] == want,
          f"tags={[row_tag(r) for r in zh_rows]}")
    check("(A3) tag 来自 TocEntry.is_column（前 4 真 / 后 6 假）",
          [e.is_column for e in issue.en_entries] == [True] * 4 + [False] * 6
          and [e.is_column for e in issue.zh_entries] == [True] * 4 + [False] * 6,
          f"en={[e.is_column for e in issue.en_entries]} "
          f"zh={[e.is_column for e in issue.zh_entries]}")

    # ── 生成 docx ───────────────────────────────────────────────────
    r = subprocess.run([PY, os.path.join(ENGINE, "dirpush.py"), "--issue", src,
                        "--out", out_root, "--with-en-toc", "--json"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV, cwd=CHECKOUT)
    docx = os.path.join(out_root, "2026 年第 5 期",
                        "逻辑学研究2026年第5期目录摘要.docx")
    ok_gen = r.returncode == 0 and os.path.isfile(docx)
    check("(B0) dirpush --with-en-toc 生成成功", ok_gen,
          f"rc={r.returncode} err={r.stderr.strip()[-300:]!r}")
    if not ok_gen:
        raise SystemExit(1)

    with zipfile.ZipFile(docx) as z:
        xml = z.read("word/document.xml")
    root = etree.fromstring(xml)
    body = root.find("w:body", NS)
    trs = body.findall(".//w:tr", NS)
    check("(B1) 目录表 24 个 w:tr（en_section+en_column+10+zh_section+host2+10）",
          len(trs) == 24, f"n={len(trs)}")

    en_trs = trs[2:12]
    zh_trs = trs[14:24]
    en_titles = [cell_text(tr.findall("w:tc", NS)[0]) for tr in en_trs]
    check("(B2) 英文标题单元格 0 个字面反斜杠",
          all("\\" not in t for t in en_titles) and all(t.strip() for t in en_titles),
          f"titles={en_titles}")
    check("(B2b) 英文栏目多行标题合并为连续整串",
          en_titles[0] == "Column Paper One part A and part B", repr(en_titles[0]))

    zh_titles_xml = [cell_text(tr.findall("w:tc", NS)[0]) for tr in zh_trs]
    check("(B3) 中文标题单元格 0 个字面反斜杠",
          all("\\" not in t for t in zh_titles_xml), f"titles={zh_titles_xml}")
    # 目录条目：冒号后的 \\ 不分段（1 个 w:p），字不丢
    zh2_cell = zh_trs[1].findall("w:tc", NS)[0]
    zh2_paras = zh2_cell.findall("w:p", NS)
    zh2_text = "".join("".join(p.itertext()) for p in zh2_paras)
    check("(B3b) 中文目录条目「冒号：\\\\后半」仍 1 段、字不丢",
          len(zh2_paras) == 1 and "冒号目录标题" in zh2_text and "后半" in zh2_text,
          f"paras={len(zh2_paras)} text={zh2_text!r}")

    # ── 末条栏目行：没有 bottom 元素；其余保持 bottom=nil ──────────
    for label, group in (("英文", en_trs), ("中文", zh_trs)):
        tcs = group[3].findall("w:tc", NS)   # 第 4 条 = col_last
        check(f"(B4) {label}第 4 条栏目行 tcBorders 无 bottom 元素（不是 nil）",
              all(not has_bottom(tc) for tc in tcs)
              and all(cell_borders(tc) is not None for tc in tcs),
              f"bottom={[has_bottom(tc) for tc in tcs]}")
        normal = group[4:]
        check(f"(B5) {label}后 6 条普通行保持原型 bottom=nil",
              all(border_val(tr.findall("w:tc", NS)[0], "bottom") == "nil"
                  for tr in normal),
              f"vals={[border_val(tr.findall('w:tc', NS)[0], 'bottom') for tr in normal]}")
        first3 = group[:3]
        check(f"(B6) {label}前 3 条栏目行 bottom=nil（栏目内部分隔线取消）",
              all(border_val(tr.findall("w:tc", NS)[0], "bottom") == "nil"
                  for tr in first3),
              f"vals={[border_val(tr.findall('w:tc', NS)[0], 'bottom') for tr in first3]}")

    # ── 中文摘要块：文章 mytitle 的 \\ 分段口径 ────────────────────
    paras = para_texts_after(body, "论文摘要")
    main_paras = [t for t in paras if t.strip() in ("主", "——副")]
    check("(B7) \\mytitle{主\\\\——副} 仍分两段（主 / ——副）",
          main_paras[:2] == ["主", "——副"], f"paras={paras[:6]}")
    colon_paras = [t for t in paras if "冒号标题" in t]
    check("(B8) \\mytitle{冒号标题：\\\\后半} 冒号后 \\\\ 只 1 段且不丢字",
          len(colon_paras) == 1 and "后半" in colon_paras[0]
          and "\\" not in colon_paras[0],
          f"colon={colon_paras}")
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
