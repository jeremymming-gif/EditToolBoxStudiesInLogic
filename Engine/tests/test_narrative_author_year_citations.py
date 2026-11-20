"""双作者人年引用：匿名合成测试，可独立运行，不发现真实稿件。

    cd Engine && /opt/homebrew/bin/python3.14 -B tests/test_narrative_author_year_citations.py
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import traceback

sys.dont_write_bytecode = True
_ENGINE = Path(__file__).resolve().parents[1]
for _p in (_ENGINE, _ENGINE / "vendor"):
    sys.path.insert(0, str(_p))

TMP = Path(tempfile.mkdtemp(prefix="slcn_narrative_"))
for _role in ("settings", "cache", "data", "manuscripts", "notes", "bibdb", "output"):
    (TMP / _role).mkdir()
os.environ.update({
    "XDG_CONFIG_HOME": str(TMP / "settings"),
    "XDG_CACHE_HOME": str(TMP / "cache"),
    "XDG_DATA_HOME": str(TMP / "data"),
    "SLCN_MANUSCRIPTS": str(TMP / "manuscripts"),
    "SLCN_NOTES": str(TMP / "notes"),
})

from slcn.wordcore import RefEntry, escape_latex  # noqa: E402
from slcn.wordcitebody import link_citations  # noqa: E402

FAILS: list[str] = []
N_CHECKS = 0


def check(name, cond, detail=""):
    global N_CHECKS
    N_CHECKS += 1
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if not cond else ""))
    if not cond:
        FAILS.append(name)


def entry(key="AsterBirch2020", author="Aster, A. and Birch, B.", year="2020"):
    return RefEntry(raw=f"{author}. {year}. Synthetic Study[M]. Example: Example Press.",
                    author=author, year=year, title="Synthetic Study", key=key)


def unit_checks():
    refs = [entry()]
    for source in ("Aster & Birch (2020)", r"Aster \& Birch (2020)",
                   "Aster and Birch (2020)", r"Aster \& Birch （2020）"):
        out, unmatched, logs = link_citations(source + " argue that the rule holds.", refs)
        check("双作者正例 " + source,
              out == r"\textcite{AsterBirch2020} argue that the rule holds."
              and unmatched == [] and "命中 1 个不同条目" in logs[-1], (out, unmatched, logs))

    for author in ("A. Aster & B. Birch", "A. Aster, B. Birch"):
        out, unmatched, _ = link_citations("Aster and Birch (2020)", [entry(author=author)])
        check("复用既有姓名规范化 " + author,
              out == r"\textcite{AsterBirch2020}" and unmatched == [], (out, unmatched))

    out, unmatched, logs = link_citations("Aster & Birch (2020)", [entry(), entry()])
    check("同key重复不制造歧义", out == r"\textcite{AsterBirch2020}" and not unmatched,
          (out, unmatched, logs))

    for label, entries in (
        ("双作者同年多key", [entry(), entry(key="Other2020")]),
        ("第二作者不同", [entry(author="Aster, A. and Cedar, C.")]),
        ("作者顺序反转", [entry(author="Birch, B. and Aster, A.")]),
        ("引用表三作者", [entry(author="Aster, A. and Birch, B. and Cedar, C.")]),
        ("引用表逗号混写三作者", [entry(author="A. Cedar, A. Aster and B. Birch")]),
        ("年份唯一但作者不同", [entry(author="Cedar, C. and Dune, D.")]),
        ("无候选", []),
    ):
        source = "Aster & Birch (2020)"
        out, unmatched, logs = link_citations(source, entries)
        check(label + "原样保留并INFO", out == source and unmatched == [source]
              and any("INFO" in log for log in logs), (out, unmatched, logs))

    suffix_refs = [entry("WorkA", year="2020a"), entry("WorkB", year="2020b")]
    out, unmatched, logs = link_citations("Aster and Birch (2020a); Aster & Birch（2020b）", suffix_refs)
    check("完整后缀精确命中", out == r"\textcite{WorkA}; \textcite{WorkB}"
          and not unmatched and "命中 2 个不同条目" in logs[-1], (out, unmatched, logs))
    for source, entries in (("Aster & Birch (2020)", suffix_refs),
                            ("Aster & Birch (2020a)", refs)):
        out, unmatched, logs = link_citations(source, entries)
        check("无后缀与后缀不互相兜底 " + source,
              out == source and unmatched == [source] and any("INFO" in log for log in logs),
              (out, unmatched, logs))

    for source in ("Cedar, Aster & Birch (2020)", "Cedar & Aster & Birch (2020)",
                   "Cedar, Aster, and Birch (2020)", "Cedar and Aster and Birch (2020)",
                   "LongAster & Birch (2020)", "Long-Aster & Birch (2020)",
                   "Long Aster & Birch (2020)", "Aster & LongBirch (2020)",
                   "van Aster & Birch (2020)", "'Long Aster & Birch (2020)'",
                   "Cedar,\nAster & Birch (2020) argue that the rule holds.",
                   "Cedar,\r\n\tAster \\& Birch (2020)",
                   "Cedar\n, Aster and Birch (2020)",
                   "Cedar,\nAster,\nand Birch (2020)",
                   "Cedar &\nAster & Birch (2020)",
                   "Long\nAster & Birch (2020)", "van\nAster & Birch (2020)"):
        out, _, _ = link_citations(source, refs)
        check("不截取三作者或姓名词尾 " + source, out == source, repr(out))

    for source in ("Aster &\nBirch (2020)", "Aster\n\\& Birch（2020）",
                   "Aster and\r\n\tBirch (2020)"):
        out, unmatched, logs = link_citations(source, refs)
        check("双作者单次行断 " + repr(source), out == r"\textcite{AsterBirch2020}"
              and not unmatched and "命中 1 个不同条目" in logs[-1], (out, unmatched, logs))

    for prefix in ("Cedar,\n\n", "Cedar,\n \t\n", "Cedar.\n"):
        source = prefix + "Aster & Birch (2020)"
        out, unmatched, logs = link_citations(source, refs)
        check("空行或句号不牵入前段姓名 " + repr(prefix),
              out == prefix + r"\textcite{AsterBirch2020}" and not unmatched,
              (out, unmatched, logs))

    for source in ("Aster &\n\nBirch (2020)",
                   "Aster & Birch (2020）", "Aster & Birch（2020)",
                   "Aster & Birch (20201)", "Aster & Birch (2020ab)",
                   "The archive opened (2020).", "Aster & Birch argue."):
        out, _, _ = link_citations(source, refs)
        check("不扩展非本形态 " + source, out == source, repr(out))

    for source in (r"$Aster & Birch (2020)$", r"\verb|Aster & Birch (2020)|",
                   r"\begin{verbatim}Aster & Birch (2020)\end{verbatim}",
                   "% Aster & Birch (2020)", r"\cite{Aster & Birch (2020)}",
                   r"\textcite{Aster & Birch (2020)}", r"\parencite[Aster & Birch (2020)]{K}",
                   r"\verb&Aster & Birch (2020)", "$Aster &\nBirch (2020)$",
                   "% Aster\n& Birch (2020)", "\\textcite{Aster &\nBirch (2020)}"):
        out, unmatched, logs = link_citations(source, refs)
        check("保护完整或跨区间候选 " + source,
              out == source and not unmatched, (out, unmatched, logs))

    out, unmatched, _ = link_citations(r"(Aster \& Birch 2020)", refs)
    check("既有括号式输出不变", out == r"（\parencite{AsterBirch2020}）" and not unmatched,
          (out, unmatched))
    once = link_citations("Aster & Birch (2020) argue.", refs)[0]
    out, unmatched, logs = link_citations(once, refs)
    check("成功转换结果再次调用幂等", out == r"\textcite{AsterBirch2020} argue."
          and not unmatched and "命中 0 个不同条目" in logs[-1], (out, unmatched, logs))
    out, _, _ = link_citations(r"See (\parencite{AsterBirch2020}).", refs)
    check("既有parencite正文不变", out == r"See (\parencite{AsterBirch2020}).", repr(out))


def parenthetical_checks():
    wrong = entry("keyWrong", author="Aster, A. and Cedar, C.")
    right = entry("keyRight")
    other = entry("keyOther")
    suffix_refs = [entry("WorkA", year="2020a"), entry("WorkB", year="2020b")]
    for label, source, refs, expected in (
        ("refs正序", r"(Aster \& Birch 2020)", [wrong, right], r"（\parencite{keyRight}）"),
        ("refs反序", r"(Aster \& Birch 2020)", [right, wrong], r"（\parencite{keyRight}）"),
        ("裸&连接", "(Aster & Birch 2020)", [wrong, right], r"（\parencite{keyRight}）"),
        ("and连接", "(Aster and Birch 2020)", [wrong, right], r"（\parencite{keyRight}）"),
        ("同key重复", r"(Aster \& Birch 2020)", [right, right], r"（\parencite{keyRight}）"),
        ("完整年份后缀", "(Aster and Birch 2020b)", suffix_refs, r"（\parencite{WorkB}）"),
        ("定位符格式", r"（Aster \& Birch 2020，pp.12-14）", [wrong, right],
         r"（\parencite{keyRight}，第12\cndash 14页）"),
    ):
        out, unmatched, logs = link_citations(source, refs)
        check("括号式双作者 " + label, out == expected and not unmatched
              and "命中 1 个不同条目" in logs[-1], (out, unmatched, logs))

    for label, source, refs in (
        ("仅错误合作者", r"(Aster \& Birch 2020)", [wrong]),
        ("同pair多key正序", r"(Aster \& Birch 2020)", [right, other]),
        ("同pair多key反序", r"(Aster \& Birch 2020)", [other, right]),
        ("两姓顺序不符", "(Birch and Aster 2020)", [right]),
        ("引用表三作者", "(Aster & Birch 2020)",
         [entry("Three", author="Aster, A. and Birch, B. and Cedar, C.")]),
        ("无后缀不配后缀", "(Aster & Birch 2020)", suffix_refs),
        ("后缀不配无后缀", "(Aster & Birch 2020a)", [right]),
    ):
        out, unmatched, logs = link_citations(source, refs)
        check("括号式双作者保留并INFO " + label, out == source and unmatched == [source]
              and any("INFO" in log for log in logs)
              and "命中 0 个不同条目" in logs[-1], (out, unmatched, logs))

    out, unmatched, logs = link_citations("(Aster 2020)", [wrong, right])
    check("单作者保留既有首作者选择", out == r"（\parencite{keyWrong}）"
          and not unmatched and "命中 1 个不同条目" in logs[-1], (out, unmatched, logs))


def docx_check():
    sys.path.insert(0, str(_ENGINE.parent / "tools"))
    from docx_fixture_gen import make_docx
    from slcn.docxsource import parse_docx
    from slcn.build import build_docx

    source = TMP / "anonymous.docx"
    paragraphs = ("Synthetic Citation Study", "Dana Example", "Abstract",
                 "This synthetic study checks a citation using anonymous text only.",
                 "Keywords: synthetic; citation", "1 Introduction",
                 "Aster & Birch (2020) argue that the rule holds.",
                 "References",
                 "Aster, A. and Birch, B. 2020. Synthetic Study[M]. Example: Example Press.")
    make_docx(str(source), paras=paragraphs)
    parsed = parse_docx(str(source), c_class_union=False, bibdb_store=None)
    check("匿名DOCX解析到双作者人年文献", len(parsed.refs) == 1 and not parsed.bib_entries
          and parsed.refs[0].year == "2020"
          and escape_latex("Aster & Birch (2020)") in parsed.body,
          ([(e.author, e.year) for e in parsed.refs], parsed.body))
    result = build_docx(str(source), str(TMP / "output"), basename="anonymous", lang="en",
                        c_class_union=False, bibdb_store=None, handoff=False,
                        compile_requested=False, verbose=False)
    tex = Path(result.tex_path).read_text()
    bib = Path(result.bib_path).read_text() if result.bib_path else ""
    key = parsed.refs[0].key if parsed.refs else "NO_PARSED_KEY"
    expected = rf"\textcite{{{key}}} argue that the rule holds."
    check("匿名DOCX到build链保留叙述式主语并链入bib", expected in result.body_text
          and expected in tex and f"{{{key}," in bib
          and escape_latex("Aster & Birch (2020)") not in result.body_text,
          (result.body_text, result.bib_path))
    (TMP / "build.log").write_text("\n".join(result.log) + "\n")
    print("EVIDENCE  " + str(TMP))


if __name__ == "__main__":
    unit_checks()
    parenthetical_checks()
    try:
        docx_check()
    except Exception:
        traceback.print_exc()
        check("匿名DOCX集成无异常", False, "见stderr；不安装依赖或扩大环境排查")
    check("设置与稿件/引用库隔离目录仍为空",
          all(not list((TMP / role).iterdir()) for role in ("settings", "manuscripts", "notes", "bibdb")))
    print(f"RESULT  {N_CHECKS - len(FAILS)}/{N_CHECKS} PASS; {len(FAILS)} FAIL")
    raise SystemExit(1 if FAILS else 0)
