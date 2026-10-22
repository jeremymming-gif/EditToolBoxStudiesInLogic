#!/usr/bin/env python3.14
r"""B-189：参考文献 DOI/网址渲染规范（2026-10-22 用户拍板）。

规范（权威口径 = `Engine/docs/RULES.md` §四.10；实现 = `slcn/bibnorm.py`）
----------------------------------------------------------------------
  · **正式发表**条目（article 有 journal、book 有 publisher、incollection 有
    booktitle、thesis/report 有 institution…）→ 参考文献列表**不印** DOI、不印网址，
    产物 `reference.bib` 里连 `url`/`doi` 字段一起删掉；
  · **未出版 / 在线资源**（`online`/`unpublished`/`misc`、arXiv 预印本、SEP 词条等）
    → 保留并打印网址；
  · 双通道：LaTeX 通道走 `normalize_bib(strip_urls=True)`；Word 通道走
    `strip_published_urls()`（Word 的 bib 不经过 normalize_bib）；模板
    `SLCN-MIX(.4).sty` 另有 `\AtEveryBibitem` 兜底（作者自行编译也成立）。

本套件钉住（hermetic；不读真实库/稿件仓、不联网；产物只写临时目录）
  ① `strip_published_urls_fields()`：正式/在线/SEP/未知类型/夹带网址/幂等；
  ② `strip_published_urls()`（Word 通道文本级）：只重写命中条目、其余逐字节不动、
     注释与条目间文本保留、花括号不配平时不猜；
  ③ `normalize_bib()` 默认**不改变**旧输出（向后兼容）；`strip_urls=True` 才清洗；
  ④ 两通道产物接线：`build_latex` 产物 bib 清洗、`build_docx` 产物 bib 清洗；
  ⑤ 模板兜底编译夹具（有 xelatex+biber 才跑，CI 上自动 SKIP）：正式条目无
     doi.org/正式网址、SEP/在线/misc 网址仍在。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" python3 tests/test_bib_url_render.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibnorm, build, bibparse  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
TMP = tempfile.mkdtemp(prefix="b189_url_")


def check(name: str, cond, detail: str = "") -> bool:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name: str, why: str) -> None:
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def fields_to_bib(etype: str, key: str, pairs) -> str:
    width = max((len(n) for n, _ in pairs), default=4)
    body = ",\n".join(f"  {n.ljust(width)} = {{{v}}}" for n, v in pairs)
    return f"@{etype}{{{key},\n{body}\n}}\n"


# ══════════════════════════════════════════════════════════════════════════
# ① 字段级：判据（正式 vs 在线）与清洗
# ══════════════════════════════════════════════════════════════════════════
FORMAL_ARTICLE = [("author", "Smith, J."), ("title", "A Formal Article"),
                  ("journal", "Journal of Logic"), ("year", "2020"),
                  ("doi", "10.1000/abc"), ("url", "https://doi.org/10.1000/abc")]
_notes: list[str] = []
_out = bibnorm.strip_published_urls_fields("Art", "article", FORMAL_ARTICLE, _notes)
check("① 正式 article：url/doi 字段都删除",
      [n for n, _ in _out] == ["author", "title", "journal", "year"], _out)
check("① 正式 article：两条删除都记 note",
      sum(1 for n in _notes if "正式发表条目" in n and "不写" in n) == 2, _notes)

BOOK_EMBED = [("author", "Cotton, C."), ("title", "Argument from Fallacy"),
              ("publisher", r"John Wiley \& Sons. https://doi.org/10.1002/x"),
              ("address", "Chichester"), ("year", "2018")]
_notes = []
_out = bibnorm.strip_published_urls_fields("Cotton2018", "book", BOOK_EMBED, _notes)
_pub = dict(_out).get("publisher")
check("① Word 形 publisher 夹带 doi.org：网址剥除且出版者收尾干净",
      _pub == r"John Wiley \& Sons", repr(_pub))
check("① publisher 夹带网址记 note",
      any("publisher 内嵌网址" in n and "删除" in n for n in _notes), _notes)

NOTE_EMBED = [("journal", "J"), ("note", "Journal, 48(3), 571–601. https://doi.org/10.1/x")]
_notes = []
_out = bibnorm.strip_published_urls_fields("N", "article", NOTE_EMBED, _notes)
check("① 正式 article 的 note 夹带网址：删网址、保留 note 正文",
      dict(_out).get("note") == "Journal, 48(3), 571–601", _out)

for etype, key, pairs, why in (
        ("online", "Web", [("author", "A"), ("title", "T"), ("url", "https://ex.org/web")],
         "online 恒保留"),
        ("unpublished", "Draft", [("author", "A"), ("title", "T"), ("url", "https://ex.org/d")],
         "unpublished 恒保留"),
        ("misc", "Arxiv", [("author", "A"), ("title", "T"), ("eprint", "2401.1"),
                           ("url", "https://arxiv.org/abs/2401.1")], "misc/arXiv 恒保留"),
        ("incollection", "Sep", [("booktitle", "The {Stanford} Encyclopedia of Philosophy"),
                                 ("publisher", "Metaphysics Research Lab"),
                                 ("url", "https://plato.stanford.edu/archives/spr2022/entries/preferences/")],
         "SEP 词条（booktitle 带保护花括号）保留"),
        ("article", "SepArt", [("journal", "The Stanford Encyclopedia of Philosophy"),
                               ("year", "2012"),
                               ("url", "https://plato.stanford.edu/archives/win2012/entries/preferences/")],
         "SEP 词条（错写成 article + journal）保留"),
        ("custom", "X", [("title", "T"), ("url", "https://ex.org/x")], "未知类型保守保留"),
):
    _notes = []
    _out = bibnorm.strip_published_urls_fields(key, etype, pairs, _notes)
    check(f"① {why}", _out == pairs and not [n for n in _notes if "已删除" in n],
          f"{_out} / {_notes}")
    check(f"① {why}（is_online_resource=True）", bibnorm.is_online_resource(etype, dict(pairs)))

# 正式类型但缺出版信息 → 不进正式判据（设计卡：类型 + 出版信息两者都要求）
_nopub = [("author", "A"), ("title", "T"), ("url", "https://uni.edu/thesis.pdf")]
check("① phdthesis 无 institution：判在线（保留）",
      bibnorm.is_online_resource("phdthesis", dict(_nopub)))
check("① phdthesis 有 institution：判正式（删除）",
      not bibnorm.is_online_resource("phdthesis", {"author": "A", "title": "T",
                                                   "institution": "某大学",
                                                   "url": "https://uni.edu/t"}))

# online 的 howpublished 里夹网址 → 搬进 url 字段、空壳 howpublished 删掉
_notes = []
_out = bibnorm.strip_published_urls_fields(
    "H", "misc", [("author", "A"), ("howpublished", r"\url{https://ex.org/p}")], _notes)
check("① 在线资源 howpublished 内嵌网址 → url 字段（空壳字段删除）",
      _out == [("author", "A"), ("url", "https://ex.org/p")], _out)
check("① 在线资源搬网址记 note", any("→ url 字段" in n for n in _notes), _notes)

# 幂等
_notes = []
_out1 = bibnorm.strip_published_urls_fields("Idem", "article", FORMAL_ARTICLE, _notes)
_notes2 = []
_out2 = bibnorm.strip_published_urls_fields("Idem", "article", _out1, _notes2)
check("① 字段级幂等（第二遍零改动、零 note）", _out1 == _out2 and not _notes2, _notes2)

# ══════════════════════════════════════════════════════════════════════════
# ② 文本级（Word 通道）：只重写命中条目，其余逐字节不动
# ══════════════════════════════════════════════════════════════════════════
MIXED = (
    "% 头注释：逐字节保留\n"
    + fields_to_bib("article", "KeepOnline",
                    [("author", "A"), ("title", "T"), ("url", "https://arxiv.org/abs/1")])
    + "\n% 条目间的注释也不动\n"
    + fields_to_bib("book", "Formal",
                    [("author", "B"), ("title", "U"),
                     ("publisher", r"Springer. https://doi.org/10.1007/x"), ("year", "2019")])
)
_new, _notes = bibnorm.strip_published_urls(MIXED)
check("② 文本级：头注释被保留", _new.startswith("% 头注释：逐字节保留"), _new[:60])
check("② 文本级：条目间注释被保留", "% 条目间的注释也不动" in _new)
check("② 文本级：未命中条目逐字节不动",
      ("@article{KeepOnline,\n" in _new) and ("https://arxiv.org/abs/1" in _new))
check("② 文本级：正式 book 内嵌网址被剥除、出版者干净",
      "doi.org" not in _new and "publisher = {Springer}" in _new, _new)
_new2, _notes2 = bibnorm.strip_published_urls(_new)
check("② 文本级幂等（第二遍零改动、零 note）", _new2 == _new and not _notes2, _notes2)
_reparsed, _findings = bibparse.parse_entries(_new)
check("② 清洗后仍是 2 条可解析条目",
      len(_reparsed) == 2 and not [f for f in _findings if "错误" in getattr(f, "severity", "")],
      len(_reparsed))

# 花括号不配平：不猜、原样返回
_bad = "@article{Bad,\n  url = {https://doi.org/1}\n"      # 缺结尾 }
_same, _notes = bibnorm.strip_published_urls(_bad)
check("② 花括号不配平文本原样返回（不猜）", _same == _bad and not _notes)

# ══════════════════════════════════════════════════════════════════════════
# ③ normalize_bib：默认向后兼容；strip_urls=True 才清洗
# ══════════════════════════════════════════════════════════════════════════
SRC = (fields_to_bib("article", "Art", FORMAL_ARTICLE)
       + "\n" + fields_to_bib("incollection", "Sep",
                              [("booktitle", "The {Stanford} Encyclopedia of Philosophy"),
                               ("year", "2022"),
                               ("url", "https://plato.stanford.edu/x")])
       + "\n" + fields_to_bib("misc", "Pre",
                              [("author", "A"), ("title", "T"),
                               ("url", "https://arxiv.org/abs/2")]))
_default, _notes = bibnorm.normalize_bib(SRC)
check("③ normalize_bib 默认（strip_urls=False）保留 url/doi（向后兼容）",
      "10.1000/abc" in _default and "https://doi.org/10.1000/abc" in _default)
check("③ 默认输出与旧版逐字节同（未给 strip_urls 时不进 B-189 分支）",
      "plato.stanford.edu/x" in _default and "arxiv.org/abs/2" in _default)
_stripped, _notes = bibnorm.normalize_bib(SRC, strip_urls=True)
_entries, _ = bibparse.parse_entries(_stripped)
check("③ strip_urls=True：正式 article 的 url/doi 不再出现在产物",
      "10.1000/abc" not in _stripped and "doi.org/10.1000/abc" not in _stripped)
check("③ strip_urls=True：SEP 词条与 misc/在线资源网址保留",
      "plato.stanford.edu/x" in _stripped and "arxiv.org/abs/2" in _stripped)
check("③ strip_urls=True：条目数/键不变",
      [e.key for e in _entries] == ["Art", "Sep", "Pre"], [e.key for e in _entries])

# ══════════════════════════════════════════════════════════════════════════
# ④ 两通道产物接线（不跑 xelatex）
# ══════════════════════════════════════════════════════════════════════════
_latex_dir = os.path.join(TMP, "latex")
os.makedirs(os.path.join(_latex_dir, "src"), exist_ok=True)
with open(os.path.join(_latex_dir, "src", "main.tex"), "w", encoding="utf-8") as fh:
    fh.write("\\documentclass{article}\n\\begin{document}\n正文 $x^2$。\n\\end{document}\n")
with open(os.path.join(_latex_dir, "src", "refs.bib"), "w", encoding="utf-8") as fh:
    fh.write(SRC)
_res = build.build_latex(os.path.join(_latex_dir, "src", "main.tex"),
                         os.path.join(_latex_dir, "out"), basename="main", lang="zh",
                         bib_path=os.path.join(_latex_dir, "src", "refs.bib"), verbose=False)
_bib = open(_res.bib_path, encoding="utf-8").read() if _res.bib_path else ""
check("④ LaTeX 通道产物 reference.bib：正式 article 的 url/doi 已删除",
      "10.1000/abc" not in _bib, _bib[:200])
check("④ LaTeX 通道产物 reference.bib：SEP/misc 网址保留",
      "plato.stanford.edu/x" in _bib and "arxiv.org/abs/2" in _bib)

# Word 通道：直接钉接线（build_docx 的 bib 由 refs_to_bib 渲染，不经过 normalize_bib；
# 用 monkeypatch 提供固定的 Word 形状 bib，验证产物写出口确实调用了 B-189 清洗）。
try:
    sys.path.insert(0, os.path.join(_REPO, "tools"))
    from docx_fixture_gen import make_docx  # noqa: E402
    from slcn import docxsource  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    make_docx = None
    _skip_why = f"夹具生成器不可用：{_exc}"

if make_docx is not None:
    _docx = os.path.join(TMP, "Word.docx")
    make_docx(_docx, paras=["论测试逻辑", "张三", "摘要：本文讨论测试逻辑。",
                            "关键词：测试；逻辑", "正文第 1 段。" * 20,
                            "参考文献",
                            "Cotton, C. (2018). Argument from Fallacy. Journal of Logic, 12(3), 1-10.",
                            "Smith, J. (2020). A Formal Article. Journal of Logic, 12(3), 1-10.",
                            "张三. 2020. 论文题目[J]. 逻辑学研究, 12(3): 1-10."])
    _WORD_BIB = (fields_to_bib("book", "Cotton2018",
                               [("author", "Cotton, C."),
                                ("title", "Argument from Fallacy"),
                                ("publisher", r"John Wiley \& Sons. https://doi.org/10.1002/c"),
                                ("address", "Chichester"), ("year", "2018")])
                 + "\n" + fields_to_bib("misc", "Sep",
                                        [("author", "Hansson, S. O."),
                                         ("booktitle", "The {Stanford} Encyclopedia of Philosophy"),
                                         ("url", "https://plato.stanford.edu/y")]))
    _orig = docxsource.refs_to_bib
    docxsource.refs_to_bib = lambda refs: _WORD_BIB  # noqa: E731
    try:
        _wres = build.build_docx(docx_path=_docx, out_dir=os.path.join(TMP, "word_out"),
                                 basename="Word", lang="zh", verbose=False)
    finally:
        docxsource.refs_to_bib = _orig
    _wbib = open(_wres.bib_path, encoding="utf-8").read() if _wres.bib_path else ""
    check("④ Word 通道产物 reference.bib：正式 book 的 publisher 夹带网址已剥除",
          "doi.org" not in _wbib and r"John Wiley \& Sons" in _wbib, _wbib)
    check("④ Word 通道产物 reference.bib：SEP 词条网址保留",
          "plato.stanford.edu/y" in _wbib)
else:
    skip("④ Word 通道产物接线", _skip_why)

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 模板兜底编译夹具（xelatex + biber；CI 无 TeX 时自动 SKIP）
# ══════════════════════════════════════════════════════════════════════════
_XELATEX = shutil.which("xelatex")
_BIBER = shutil.which("biber")
try:
    from pypdf import PdfReader  # noqa: E402
except Exception:  # noqa: BLE001
    PdfReader = None


def _compile_fixture(sty_name: str, tag: str) -> None:
    sty_src = os.path.join(_REPO, "编辑笔记", "模板文件", sty_name)
    if not (os.path.isfile(sty_src) and _XELATEX and _BIBER and PdfReader):
        skip(f"⑤ 模板兜底编译（{tag}）",
             "xelatex/biber/pypdf 或模板不可用（CI 预期）")
        return
    d = os.path.join(TMP, "tex_" + tag)
    os.makedirs(d, exist_ok=True)
    shutil.copy2(sty_src, d)
    with open(os.path.join(d, "refs.bib"), "w", encoding="utf-8") as fh:
        fh.write(
            fields_to_bib("article", "ArtFormal",
                          [("author", "Smith, J."), ("title", "A Formal Article"),
                           ("journal", "Journal of Logic"), ("volume", "12"),
                           ("pages", "1--10"), ("year", "2020"),
                           ("url", "https://doi.org/10.1000/abc")])
            + fields_to_bib("incollection", "SepEntry",
                            [("author", "Hansson, S. O."), ("title", "{Preferences}"),
                             ("booktitle", "The {Stanford} Encyclopedia of Philosophy"),
                             ("editor", "Edward N. Zalta"),
                             ("publisher", "Metaphysics Research Lab"),
                             ("year", "2022"),
                             ("url", "https://plato.stanford.edu/archives/spr2022/entries/preferences/")])
            + fields_to_bib("misc", "ArxivPreprint",
                            [("author", "Lee, K."), ("title", "A Preprint"),
                             ("eprint", "2401.00001"), ("year", "2024"),
                             ("url", "https://arxiv.org/abs/2401.00001")])
            + fields_to_bib("online", "WebPage",
                            [("author", "Wiki, C."), ("title", "Some Web Page"),
                             ("year", "2023"), ("url", "https://example.org/web")]))
    with open(os.path.join(d, "fixture.tex"), "w", encoding="utf-8") as fh:
        fh.write("\\documentclass[b5paper,10.5t,onecolumn,UTF8]{article}\n"
                 f"\\usepackage{{{sty_name[:-4]}}}\n"
                 "\\addbibresource{refs.bib}\n"
                 "\\begin{document}\n测试正文。\n\\nocite{*}\n"
                 "\\printbibliography\n\\end{document}\n")
    env = dict(os.environ, TEXINPUTS=d + os.pathsep, BIBINPUTS=d + os.pathsep)
    _run = lambda args: subprocess.run(args, cwd=d, env=env, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=180)
    _run([_XELATEX, "-interaction=nonstopmode", "fixture.tex"])
    _run([_BIBER, "fixture"])
    _run([_XELATEX, "-interaction=nonstopmode", "fixture.tex"])
    _run([_XELATEX, "-interaction=nonstopmode", "fixture.tex"])
    pdf = os.path.join(d, "fixture.pdf")
    if not os.path.isfile(pdf):
        check(f"⑤ 模板兜底编译（{tag}）：产物 PDF 存在", False)
        return
    text = "\n".join(p.extract_text() or "" for p in PdfReader(pdf).pages)
    norm = re.sub(r"\s+", "", text)
    check(f"⑤ 模板兜底（{tag}）：正式 article 的 doi.org 不印", "doi.org" not in norm)
    check(f"⑤ 模板兜底（{tag}）：SEP 词条网址仍打印", "plato.stanford.edu" in norm)
    check(f"⑤ 模板兜底（{tag}）：misc/arXiv 网址仍打印", "arxiv.org" in norm)
    check(f"⑤ 模板兜底（{tag}）：online 网址仍打印", "example.org/web" in norm)


_compile_fixture("SLCN-MIX.sty", "mix")
_compile_fixture("SLCN-MIX4.sty", "mix4")

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"；跳过 {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
