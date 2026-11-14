#!/usr/bin/env python3
"""R3 缺陷 4：按稿件语言选择目录体例（zh / en / mixed / unknown）。

独立断言（匿名合成期目录；不读真实稿件；临时目录）：
  A 10 篇全中文（\\mytitle 中文 + \\mytitleEN 英文，source=mix）、4 栏目+6 普通，
    显式 `--with-en-toc`：docx 24 行，标题「中英文对照目录」，
    中英各 4 栏目+6 普通，第 4 条栏目行有分隔线（无 bottom），无字面反斜杠。
  B 同一期不加 flag：docx 11 行，标题「目录」，无英文段/英文标题作者；
    JSON issue.language=="zh"、issue.with_en_toc==false；有「按稿件语言」类 warning；
    栏目分组正确、主持人行存在。
  C 7 篇英文（\\mytitleCN 放英文，使 _cn_meta source=cn）、2 栏目+5 普通，不加 flag：
    docx 18 行，标题「中英文对照目录」，JSON language=="en"、with_en_toc==true；
    所有 Article.source=="cn"；中英各 2 栏目+5 普通。
  D 混合语言（5 中 5 英）：必须出现「混合」warning；有 contents.tex 时按 bi；
    无 contents.tex 时可 zh，但必须 warning。
  E B 的纯中文 + --with-en-toc：JSON warnings 含「显式覆盖」，docx 24 行。
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


ZH_COL_TITLES = ["匿名栏目标题一", "匿名栏目标题二", "匿名栏目标题三", "匿名栏目标题四"]
ZH_PLAIN_TITLES = [f"匿名普通标题{i}" for i in ("五", "六", "七", "八", "九", "十")]
ZH_TITLES = ZH_COL_TITLES + ZH_PLAIN_TITLES
ZH_AUTHORS = [f"匿名作者{i}" for i in ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")]

EN_COL_TITLES = ["Column Paper One", "Column Paper Two", "Column Paper Three", "Column Paper Four"]
EN_PLAIN_TITLES = [f"Plain Paper {i}" for i in ("Five", "Six", "Seven", "Eight", "Nine", "Ten")]
EN_TITLES = EN_COL_TITLES + EN_PLAIN_TITLES
EN_AUTHORS = [f"Anon Author {i}" for i in range(1, 11)]

EN7 = ["English Column One", "English Column Two",
       "English Paper Three", "English Paper Four", "English Paper Five",
       "English Paper Six", "English Paper Seven"]
EN7_AUTHORS = [f"Eng Author {i}" for i in range(1, 8)]

MIX_TITLES = (["混合中文标题一", "混合中文标题二", "混合中文标题三",
               "混合中文标题四", "混合中文标题五"]
              + ["Mixed English Title Six", "Mixed English Title Seven",
                 "Mixed English Title Eight", "Mixed English Title Nine",
                 "Mixed English Title Ten"])
MIX_EN_TITLES = ["Mixed Column One", "Mixed Column Two",
                 "Mixed Paper Three", "Mixed Paper Four", "Mixed Paper Five",
                 "Mixed English Title Six", "Mixed English Title Seven",
                 "Mixed English Title Eight", "Mixed English Title Nine",
                 "Mixed English Title Ten"]


def _zh_toc(titles: list[str], n_column: int) -> str:
    hdr = ("\\begin{flushright}\n\\heiti{逻辑学研究（双月刊）\\\\\n"
           "第\\ 19 卷，第\\ 5 期，总第\\ 90 期}}\\\\\n"
           "\\heiti{2026 年第\\ 5 期，10月\\ 18 日出版}\n\\end{flushright}\n")
    host = "{\\large \\bf{专栏：匿名专栏}}&&\\textit{\\large 主持人：匿名主持}\n\\\\[3.0 ex]\n"
    body: list[str] = []
    for i, title in enumerate(titles):
        prefix = "~~~~ " if i < n_column else ""
        body.append("{\\large " + prefix + title + "}\n"
                    "&\\textit{\\large " + prefix + ZH_AUTHORS[i % len(ZH_AUTHORS)] + "}\n"
                    "& " + str(i + 1) + "\\\\\n\\vdistance\n")
    return ("\\documentclass{ctexart}\n\\begin{document}\n" + hdr
            + "\\vspace{4ex}\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{-0.3em}}r}\n"
            + host + "%中间主体\n" + "".join(body) + "\\end{tabularx}\n\\end{document}\n")


def _en_toc(titles: list[str], n_column: int) -> str:
    body: list[str] = []
    for i, title in enumerate(titles):
        prefix = "~~~~ " if i < n_column else ""
        body.append("\\large " + prefix + title + "\\\\\n"
                    "\\textit{{" + prefix + EN_AUTHORS[i % len(EN_AUTHORS)] + "}}\n"
                    "& " + str(i + 1) + "\\\\\n\\vdistance\n")
    return ("\\documentclass{ctexart}\n\\begin{document}\n"
            "\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n"
            "\\large  \\bf{Column: Anonymous Column}\n\\\\[6.2mm]\n%中间主体\n"
            + "".join(body) + "\\end{tabularx}\n\\end{document}\n")


def _article_tex(mode: str, i: int, zh_title: str, en_title: str) -> str:
    if mode == "cn":      # 中文模版：\\mytitleCN 可放英文（source 仍是 cn）
        return ("\\documentclass{article}\n\\begin{document}\n"
                f"\\newcommand{{\\mytitleCN}}{{{en_title}}}\n"
                f"\\newcommand{{\\myfirstauthorCN}}{{{ZH_AUTHORS[(i - 1) % len(ZH_AUTHORS)]}}}\n"
                "\\newcommand{\\myfirstaffiliationCN}{Anon Aff}\n"
                f"\\newcommand{{\\myabstractCN}}{{Anon abstract {i}.}}\n"
                "\\end{document}\n")
    # mix：\\mytitle 即中文、\\mytitleEN 英文
    return ("\\documentclass{article}\n\\begin{document}\n"
            f"\\newcommand{{\\mytitle}}{{{zh_title}}}\n"
            f"\\newcommand{{\\mytitleEN}}{{{en_title}}}\n"
            f"\\newcommand{{\\myfirstauthor}}{{{ZH_AUTHORS[(i - 1) % len(ZH_AUTHORS)]}}}\n"
            "\\newcommand{\\myfirstaffiliation}{匿名单位}\n"
            f"\\newcommand{{\\myabstract}}{{匿名摘要{i}。}}\n"
            "\\end{document}\n")


def make_issue(root: str, *, zh_titles: list[str], n_column: int,
               modes: list[str], en_titles: list[str] | None = None,
               with_en: bool = True) -> str:
    d = os.path.join(root, "2026年第5期(90)")
    toc = os.path.join(d, "000 Table of Contents")
    w(os.path.join(toc, "Chinese Content.tex"), _zh_toc(zh_titles, n_column))
    if with_en:
        w(os.path.join(toc, "contents.tex"), _en_toc(en_titles or zh_titles, n_column))
    for i, mode in enumerate(modes, start=1):
        name = f"Fake_{i}"
        zh_title = zh_titles[i - 1]
        en_title = (en_titles or EN_TITLES)[i - 1] if i - 1 < len(en_titles or EN_TITLES) else f"Fake Title {i}"
        w(os.path.join(d, f"{i:02d} {name}", f"{name}.tex"),
          _article_tex(mode, i, zh_title, en_title))
    return d


def run(src: str, out_root: str, *extra: str) -> tuple[int, dict | None, str]:
    r = subprocess.run([PY, os.path.join(ENGINE, "dirpush.py"), "--issue", src,
                        "--out", out_root, "--json", *extra],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV, cwd=CHECKOUT)
    try:
        obj = json.loads(r.stdout)
    except ValueError:
        obj = None
    return r.returncode, obj, r.stderr


def docx_path(out_root: str) -> str:
    return os.path.join(out_root, "2026 年第 5 期", "逻辑学研究2026年第5期目录摘要.docx")


def parse_docx(path: str):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = etree.fromstring(xml)
    body = root.find("w:body", NS)
    return root, body, body.findall(".//w:tr", NS)


def para_texts(body) -> list[str]:
    return ["".join(p.itertext()) for p in body.findall(".//w:p", NS)]


def cell_text(tc) -> str:
    return "".join(tc.itertext())


def cell_borders(tc):
    return tc.find("./w:tcPr/w:tcBorders", NS)


def has_bottom(tc) -> bool:
    b = cell_borders(tc)
    return b is not None and b.find("w:bottom", NS) is not None


def border_val(tc, side: str):
    b = cell_borders(tc)
    if b is None:
        return "<no-tcBorders>"
    el = b.find("w:" + side, NS)
    return None if el is None else el.get("{%s}val" % W)


def tr_title(tr) -> str:
    return cell_text(tr.findall("w:tc", NS)[0])


def warnings_of(obj: dict | None) -> list[str]:
    return list((obj or {}).get("warnings") or [])


def issue_of(obj: dict | None) -> dict:
    return ((obj or {}).get("issue") or {})


work = tempfile.mkdtemp(prefix="r3_language_mode_")
try:
    from slcn.pushcopy import collect as PC

    # ── A. 10 中文 + 显式 --with-en-toc → 24 行 bi ────────────────────
    src_a = make_issue(os.path.join(work, "a"), zh_titles=ZH_TITLES, n_column=4,
                       modes=["mix"] * 10, en_titles=EN_TITLES)
    out_a = os.path.join(work, "out_a")
    rc, obj, err = run(src_a, out_a, "--with-en-toc")
    check("(A1) 显式 --with-en-toc rc=0 且生成 docx",
          rc == 0 and os.path.isfile(docx_path(out_a)), f"rc={rc} err={err[-300:]!r}")
    if rc == 0 and os.path.isfile(docx_path(out_a)):
        _root, body, trs = parse_docx(docx_path(out_a))
        check("(A2) docx 24 行（en_section+en_column+10+zh_section+host2+10）",
              len(trs) == 24, f"n={len(trs)}")
        check("(A3) 标题「中英文对照目录」",
              "中英文对照目录" in para_texts(body), "标题缺失")
        en_trs, zh_trs = trs[2:12], trs[14:24]
        en_t = [tr_title(tr) for tr in en_trs]
        zh_t = [tr_title(tr) for tr in zh_trs]
        check("(A4) 中英各 10 条（4 栏目+6 普通）",
              len(en_trs) == 10 and len(zh_trs) == 10
              and en_t[:4] == EN_COL_TITLES and zh_t[:4] == ZH_COL_TITLES
              and en_t[4:] == EN_PLAIN_TITLES and zh_t[4:] == ZH_PLAIN_TITLES,
              f"en={en_t} zh={zh_t}")
        for label, group in (("英文", en_trs), ("中文", zh_trs)):
            tcs = group[3].findall("w:tc", NS)   # 第 4 条 = 末条栏目行
            check(f"(A5) {label}第 4 条栏目行有分隔线（tcBorders 无 bottom）",
                  all(cell_borders(tc) is not None and not has_bottom(tc) for tc in tcs),
                  f"borders={[cell_borders(tc) is not None for tc in tcs]} "
                  f"bottom={[has_bottom(tc) for tc in tcs]}")
        check("(A6) 目录条目无字面反斜杠",
              all("\\" not in t for t in en_t + zh_t), f"en={en_t} zh={zh_t}")
        check("(A7) 纯中文 + 显式 flag：JSON warnings 含「显式覆盖」",
              any("显式覆盖" in x for x in warnings_of(obj)),
              f"warnings={warnings_of(obj)}")

    # ── B. 同一期不加 flag → 11 行 zh ────────────────────────────────
    out_b = os.path.join(work, "out_b")
    rc, obj, err = run(src_a, out_b)
    check("(B1) 默认（纯中文）rc=0 且生成 docx",
          rc == 0 and os.path.isfile(docx_path(out_b)), f"rc={rc} err={err[-300:]!r}")
    if rc == 0 and os.path.isfile(docx_path(out_b)):
        _root, body, trs = parse_docx(docx_path(out_b))
        texts = para_texts(body)
        check("(B2) docx 11 行（host_merged + 10）", len(trs) == 11, f"n={len(trs)}")
        check("(B3) 标题「目录」且不是「中英文对照目录」",
              "目录" in texts and "中英文对照目录" not in texts, f"texts={texts[:4]}")
        all_text = "".join(texts)
        check("(B4) 无英文段/英文标题作者",
              "英文目录" not in all_text
              and not any(t in all_text for t in EN_COL_TITLES + EN_PLAIN_TITLES)
              and not any(a in all_text for a in EN_AUTHORS),
              "出现英文内容")
        j_issue = issue_of(obj)
        check("(B5) JSON issue.language=='zh' 且 with_en_toc==false",
              j_issue.get("language") == "zh" and j_issue.get("with_en_toc") is False,
              f"issue={j_issue}")
        check("(B6) warnings 含「按稿件语言」类说明",
              any("按稿件语言" in x for x in warnings_of(obj)),
              f"warnings={warnings_of(obj)}")
        check("(B7) 主持人行存在",
              any("主持人：匿名主持" in t for t in texts), f"texts={texts[:3]}")
        # 栏目分组：前 4 条带 tcBorders（col/col_last），后 6 条无
        entries = trs[1:]
        first3 = entries[:3]
        check("(B8) 前 3 条栏目行 tcBorders 且 bottom=nil",
              all(cell_borders(tr.findall("w:tc", NS)[0]) is not None
                  and border_val(tr.findall("w:tc", NS)[0], "bottom") == "nil"
                  for tr in first3),
              f"vals={[border_val(tr.findall('w:tc', NS)[0], 'bottom') for tr in first3]}")
        check("(B9) 第 4 条栏目行（末条）有分隔线（无 bottom）",
              all(cell_borders(tc) is not None and not has_bottom(tc)
                  for tc in entries[3].findall("w:tc", NS)),
              f"bottom={[has_bottom(tc) for tc in entries[3].findall('w:tc', NS)]}")
        check("(B10) 后 6 条普通行无 tcBorders（不加栏目边框）",
              all(cell_borders(tr.findall("w:tc", NS)[0]) is None for tr in entries[4:]),
              f"borders={[cell_borders(tr.findall('w:tc', NS)[0]) is not None for tr in entries[4:]]}")

    # ── C. 7 篇英文（mytitleCN 英文 → source=cn），默认 en → 18 行 bi ─
    src_c = make_issue(os.path.join(work, "c"), zh_titles=EN7, n_column=2,
                       modes=["cn"] * 7, en_titles=EN7)
    out_c = os.path.join(work, "out_c")
    iss_c = PC.collect_issue(src_c, out_root=out_c)
    check("(C0) 所有 Article.source=='cn'（mytitleCN 英文仍按 cn 模板）",
          len(iss_c.articles) == 7
          and all(getattr(a, "source", None) == "cn" for a in iss_c.articles),
          f"src={[getattr(a, 'source', None) for a in iss_c.articles]}")
    rc, obj, err = run(src_c, out_c)
    check("(C1) 默认（全英文）rc=0 且生成 docx",
          rc == 0 and os.path.isfile(docx_path(out_c)), f"rc={rc} err={err[-300:]!r}")
    if rc == 0 and os.path.isfile(docx_path(out_c)):
        _root, body, trs = parse_docx(docx_path(out_c))
        check("(C2) docx 18 行（1+1+7+1+1+7）", len(trs) == 18, f"n={len(trs)}")
        check("(C3) 标题「中英文对照目录」",
              "中英文对照目录" in para_texts(body), "标题缺失")
        j_issue = issue_of(obj)
        check("(C4) JSON language=='en' 且 with_en_toc==true",
              j_issue.get("language") == "en" and j_issue.get("with_en_toc") is True,
              f"issue={j_issue}")
        en_trs, zh_trs = trs[2:9], trs[11:18]
        check("(C5) 中英各 2 栏目+5 普通（7 条）",
              [tr_title(tr) for tr in en_trs] == EN7
              and [tr_title(tr) for tr in zh_trs] == EN7
              and [e.is_column for e in iss_c.zh_entries] == [True, True] + [False] * 5
              and [e.is_column for e in iss_c.en_entries] == [True, True] + [False] * 5,
              f"en={[tr_title(tr) for tr in en_trs]} zh={[tr_title(tr) for tr in zh_trs]} "
              f"cols={[e.is_column for e in iss_c.zh_entries]}")
        for label, group in (("英文", en_trs), ("中文", zh_trs)):
            tcs = group[1].findall("w:tc", NS) if len(group) >= 2 else []
            check(f"(C6) {label}第 2 条栏目行有分隔线（无 bottom）",
                  bool(tcs) and all(cell_borders(tc) is not None and not has_bottom(tc)
                                    for tc in tcs),
                  f"bottom={[has_bottom(tc) for tc in tcs]}")

    # ── D. 混合语言（5 中 5 英）─────────────────────────────────────
    d_root = os.path.join(work, "d")
    src_d = make_issue(os.path.join(d_root, "with_en"), zh_titles=MIX_TITLES,
                       n_column=2, modes=["mix"] * 5 + ["cn"] * 5,
                       en_titles=MIX_EN_TITLES)
    out_d1 = os.path.join(work, "out_d1")
    rc, obj, err = run(src_d, out_d1)
    warns_d1 = warnings_of(obj)
    check("(D1) 混合 + 有 contents.tex：rc=0、按 bi（不静默）",
          rc == 0 and issue_of(obj).get("language") == "mixed"
          and issue_of(obj).get("with_en_toc") is True
          and any("混合" in x for x in warns_d1),
          f"rc={rc} issue={issue_of(obj)} warnings={warns_d1}")
    if rc == 0 and os.path.isfile(docx_path(out_d1)):
        _root, _body, trs = parse_docx(docx_path(out_d1))
        check("(D2) 混合 + contents：docx 按 bi（1+1+10+1+1+10=24 行）",
              len(trs) == 24, f"n={len(trs)}")

    src_d2 = make_issue(os.path.join(d_root, "no_en"), zh_titles=MIX_TITLES,
                        n_column=2, modes=["mix"] * 5 + ["cn"] * 5,
                        en_titles=MIX_EN_TITLES, with_en=False)
    out_d2 = os.path.join(work, "out_d2")
    rc, obj, err = run(src_d2, out_d2)
    warns_d2 = warnings_of(obj)
    check("(D3) 混合 + 无 contents.tex：可降级 zh，但必须 warning",
          rc == 0 and issue_of(obj).get("language") == "mixed"
          and issue_of(obj).get("with_en_toc") is False
          and any("混合" in x for x in warns_d2),
          f"rc={rc} issue={issue_of(obj)} warnings={warns_d2}")
    if rc == 0 and os.path.isfile(docx_path(out_d2)):
        _root, _body, trs = parse_docx(docx_path(out_d2))
        check("(D4) 混合无 contents：docx 降级 zh（1+10=11 行）",
              len(trs) == 11, f"n={len(trs)}")

    # ── E. 显式覆盖：B 的纯中文 + --with-en-toc → 24 行 + 显式覆盖 ──
    out_e = os.path.join(work, "out_e")
    rc, obj, err = run(src_a, out_e, "--with-en-toc")
    check("(E1) 纯中文显式 flag：rc=0、JSON warnings 含「显式覆盖」",
          rc == 0 and any("显式覆盖" in x for x in warnings_of(obj)),
          f"rc={rc} warnings={warnings_of(obj)}")
    if rc == 0 and os.path.isfile(docx_path(out_e)):
        _root, _body, trs = parse_docx(docx_path(out_e))
        check("(E2) 显式覆盖后 docx 24 行", len(trs) == 24, f"n={len(trs)}")

    # ── F. 没有可用标题 → unknown：沿用旧设置推断，但必须 warning ─────
    src_f = os.path.join(work, "f", "2026年第5期(90)")
    w(os.path.join(src_f, "000 Table of Contents", "Chinese Content.tex"),
      _zh_toc(ZH_TITLES[:2], 0))
    for i in (1, 2):
        os.makedirs(os.path.join(src_f, f"{i:02d} Empty_{i}"), exist_ok=True)
    out_f = os.path.join(work, "out_f")
    rc, obj, err = run(src_f, out_f)
    check("(F1) 无可用标题 → language=='unknown'、沿用设置推断（默认仅中文）",
          rc == 0 and issue_of(obj).get("language") == "unknown"
          and issue_of(obj).get("with_en_toc") is False,
          f"rc={rc} issue={issue_of(obj)}")
    check("(F2) unknown 必须 warning（不静默沿用旧设置）",
          any("无法按稿件语言判定" in x for x in warnings_of(obj)),
          f"warnings={warnings_of(obj)}")
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
