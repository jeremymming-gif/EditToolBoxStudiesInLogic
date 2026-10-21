#!/usr/bin/env python3.14
r"""批73 B-130：**脚注文本里的裸 `[N]` 引用** → `\parencite`（保守，不猜）。

缺陷（86-08 真稿实测；本套件只用**脱敏合成** docx，不读任何真实稿件）
--------------------------------------------------------------------
编号体例稿的文末清单是「Word 自动编号 / 显式 `[N]` 文本」，正文引用写成
`（[12]）`/`（[12]，第8页）`（引擎由 `link_numbered_citations` 换成 `\parencite`）。
但**脚注文本**里的引用常常是**裸编号**：

    \footnote{参见[12]。}
    \footnote{参见吉尔伯特在[13]的批评。}
    \footnote{参见[13]，第8页。}

旧实现只匹配中文括号组 → 裸编号原样残留（成品 PDF 悬空标记；`[12]` 对应的
Gilbert2002 还会因为「从未被引」而不被 biblatex 打印）。B-130 的修法：

① gate（`_footnote_num2key_gate`）= `ref_key_by_num` 是**可靠 1..N 映射**：
   Word 自动编号安全闸（B-022）物化成功，**或** 文末显式 `[N]` 清单解析一致且
   编号覆盖 1..N 连续；两者都无 → 不猜（脚注里「像引用」的裸编号汇总一条 warning）。
② 逐处判据（`link_footnote_bare_citations`）：`[N]` 在**括号外** + `N ∈ 映射` +
   **引用引导语证据**（参见/参看/详见/另见/转引自/引自/参考/参考文献/例如/见/据…）；
   拿不准 → 原样保留 + warning（不猜）。
③ 只换 `[N]` 本身：前后中文文字与页码定位（`参见[x]，第8页。`）原样保留。

本套件钉住（spec §2.5 六形态 + 端到端 + 默认零变化）
----------------------------------------------------
  ① 纯函数：纯引用 / 带页码 / 句中「在[13]的批评」/ 同脚注多编号 / 越界 /
     疑似非引用（规则[3]条）/ 括号组（归正文通道）/ 幂等 / 空映射不动作；
  ② gate 单元：auto 物化成功 / 显式 1..N 连续 / 编号不齐（不通过）/ 空映射；
  ③ 合成 auto 编号稿 parse：映射 1..13、脚注 3 类命中、越界+疑似保留 + warning；
  ④ 合成显式 `[N]` 清单稿 parse：同样命中（gate = explicit 分支）；
  ⑤ 非编号体例（人年制清单）：不转换、无映射 → 脚注裸编号保留 + 一条「未转换」warning；
  ⑥ 端到端 `build_docx`：`.tex` 括号内引用与脚注裸编号都成 `\parencite`、
     裸 `[12]`/`[13]` 归零、`reference.bib` 条数不变、`\nocite` 0；
  ⑦ 默认零变化：脚注无裸编号 → 开/关 gate 的 `doc.body` 逐字节相同；同稿两遍一致。

不联网、不写仓库（全部 tempfile）、不跑 corpus.py / build.sh / 真实稿件库。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" \
      /Library/Frameworks/Python.framework/Versions/Current/bin/python3.14 \
      tests/test_b73_footnote_bare_cite.py
"""

import os
import re
import sys
import tempfile
import zipfile
from types import SimpleNamespace

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build, docxsource  # noqa: E402
from slcn.docxsource import (  # noqa: E402
    _footnote_bare_cite_gate_warning,
    _footnote_num2key_gate,
    link_footnote_bare_citations,
    parse_docx,
)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if not cond and detail else ""))


# ── 合成 docx ────────────────────────────────────────────────────────────────

_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _xesc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text: str, num: tuple[str, int] | None = None, fnrefs: tuple[int, ...] = ()) -> str:
    ppr = ""
    if num is not None:
        nid, ilvl = num
        ppr = (f'<w:pPr><w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{nid}"/>'
               f'</w:numPr></w:pPr>')
    runs = ""
    if text:
        runs += f'<w:r><w:t xml:space="preserve">{_xesc(text)}</w:t></w:r>'
    for fid in fnrefs:
        runs += f'<w:r><w:footnoteReference w:id="{fid}"/></w:r>'
    return f"<w:p>{ppr}{runs}</w:p>"


def _numbering_xml() -> str:
    """一条 decimal + `lvlText="[%1]"` 的 Word 自动编号定义（numId=1）。"""
    return (f'<w:numbering {_NS}><w:abstractNum w:abstractNumId="1">'
            '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
            '<w:lvlText w:val="[%1]"/></w:lvl></w:abstractNum>'
            '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num></w:numbering>')


def _refs13() -> list[str]:
    return [f"A. Author{i}, {2000 + i}, “Test Title {i}”, Journal of Testing, "
            f"{i}(1): {i}-{i + 9}." for i in range(1, 14)]


def _make_docx(path: str, footnotes: list[str], *, numbering: bool,
               body_extra: str = "") -> str:
    """合成一份编号体例稿：题名/作者/摘要/关键词 + 正文（含脚注引用）+ 参考文献。

    numbering=True：文末 13 条用 **Word 自动编号**（序号不在文字里，B-022 物化）；
    numbering=False：文末 13 条是**显式 `[N] ` 文本**（`_numbered_refs` 直接解析）。
    正文里写 `（[1]）`/`（[13]）` 作为「编号体例」证据（物化安全闸要求正文 max([n])=N）。
    """
    refs = _refs13()
    parts = [_p("测试题名：脚注裸编号引用"), _p("张三"),
             _p("摘要：这是测试用摘要，用于合成文档解析。"),
             _p("关键词：测试；裸编号；引用")]
    parts.append(_p("正文第一段，含正文编号引用（[1]）与（[13]）。" + body_extra,
                    fnrefs=tuple(range(1, len(footnotes) + 1))))
    parts.append(_p("参考文献"))
    if numbering:
        parts.extend(_p(t, num=("1", 0)) for t in refs)
    else:
        parts.extend(_p(f"[{i}] {t}") for i, t in enumerate(refs, 1))
    doc_xml = (f'<w:document {_NS}><w:body>' + "".join(parts) + "</w:body></w:document>")
    fn_xml = (f'<w:footnotes {_NS}>' + "".join(
        f'<w:footnote w:id="{i}">{_p(t)}</w:footnote>'
        for i, t in enumerate(footnotes, 1)) + "</w:footnotes>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc_xml)
        z.writestr("word/footnotes.xml", fn_xml)
        if numbering:
            z.writestr("word/numbering.xml", _numbering_xml())
    return path


# ══ ① 纯函数判据 ═══════════════════════════════════════════════════════════
print("── ① link_footnote_bare_citations 判据 ──")
M13 = {i: f"K{i}" for i in range(1, 14)}

_new, _n, _iss = link_footnote_bare_citations("参见[12]。", M13)
check("① 裸编号纯引用 → \\parencite，前后文字保留",
      _new == "参见\\parencite{K12}。" and _n == 1 and _iss == [], repr(_new))

_new, _n, _iss = link_footnote_bare_citations("参见[12]，第8页。", M13)
check("① 带页码 → 只换编号，页码定位原样保留",
      _new == "参见\\parencite{K12}，第8页。" and _n == 1 and _iss == [], repr(_new))

_new, _n, _iss = link_footnote_bare_citations("参见吉尔伯特在[13]的批评。", M13)
check("① 句中「在[13]的批评」→ 中文语序原样",
      _new == "参见吉尔伯特在\\parencite{K13}的批评。" and _n == 1, repr(_new))

_new, _n, _iss = link_footnote_bare_citations("参见[5]与[12]。", M13)
check("① 同一脚注多个编号 → 各转各的", _n == 2
      and _new == "参见\\parencite{K5}与\\parencite{K12}。", repr(_new))

_new, _n, _iss = link_footnote_bare_citations("参见[99]。", M13)
check("① 越界编号 → 原文保留 + warning（不猜）",
      _new == "参见[99]。" and _n == 0 and any("[99]" in x for x in _iss), repr(_iss))

_new, _n, _iss = link_footnote_bare_citations("规则[3]条。", M13)
check("① 疑似非引用（规则[3]条）→ 原文保留 + warning",
      _new == "规则[3]条。" and _n == 0
      and any("疑似非引用" in x for x in _iss), repr(_iss))

_new, _n, _iss = link_footnote_bare_citations("（[12]，第3页）", M13)
check("① 括号组内编号不处理（归正文 link_numbered_citations），也不出 warning",
      _new == "（[12]，第3页）" and _n == 0 and _iss == [], repr(_new))

_new, _n, _iss = link_footnote_bare_citations("无编号的普通注释。", M13)
check("① 无编号 → 原样", _new == "无编号的普通注释。" and _n == 0 and _iss == [])

_new, _n, _iss = link_footnote_bare_citations("参见$[3]$。", M13)
check("① 公式里的 [3] 是数学记号（区间/下标）→ 不换、不出 warning",
      _new == "参见$[3]$。" and _n == 0 and _iss == [], repr(_new))

_new, _n, _iss = link_footnote_bare_citations("参见[12]与$[3]$。", M13)
check("① 公式外的 [12] 换、公式内的 [3] 不动",
      _new == "参见\\parencite{K12}与$[3]$。" and _n == 1 and _iss == [], repr(_new))

_new, _n, _iss = link_footnote_bare_citations("参见[12]。", {})
check("① 空映射（非编号体例稿）→ 一个字都不动",
      _new == "参见[12]。" and _n == 0 and _iss == [])

_once, _n1, _ = link_footnote_bare_citations("参见[12]。另见[13]，第8页。", M13)
_twice, _n2, _ = link_footnote_bare_citations(_once, M13)
check("① 幂等：转换结果再跑一遍不再变化",
      _n1 == 2 and _n2 == 0 and _twice == _once, repr(_twice))


# ══ ② gate 单元 ═══════════════════════════════════════════════════════════
print("── ② _footnote_num2key_gate：可靠 1..N 映射才放行 ──")


def _gate(map_: dict, auto: int = 0) -> dict:
    return _footnote_num2key_gate(SimpleNamespace(
        ref_key_by_num=map_, ref_auto_materialized=auto))


check("② auto 物化成功 → 放行", _gate({1: "A", 3: "C"}, auto=3) == {1: "A", 3: "C"})
check("② 显式清单 1..N 连续 → 放行", _gate({1: "A", 2: "B"}) == {1: "A", 2: "B"})
check("② 显式清单编号不齐（缺 2）→ 不放行", _gate({1: "A", 3: "C"}) == {})
check("② 空映射 → 不放行", _gate({}) == {})


# ══ ③ 合成 Word 自动编号稿（B-022 物化） ═══════════════════════════════════
_TMP = tempfile.mkdtemp(prefix="b73_b130_")
FN_AUTO = [
    "参见[12]。",
    "参见[13]，第8页。",
    "参见吉尔伯特在[13]的批评。",
    "参见[99]。",
    "规则[3]条。",
    "参见[5]与[12]。",
    "（[6]，第434页）",
]
_docx_auto = _make_docx(os.path.join(_TMP, "auto.docx"), FN_AUTO, numbering=True)
_d_auto = parse_docx(_docx_auto, c_class_union=True)
_k = _d_auto.ref_key_by_num
print("── ③ 合成 auto 编号稿：物化 13 条 → 映射 1..13 ──")
check("③ Word 自动编号物化成功（ref_auto_materialized=13）",
      _d_auto.ref_auto_materialized == 13, str(_d_auto.ref_auto_materialized))
check("③ 编号→键映射 1..13 齐全",
      sorted(_k) == list(range(1, 14)) and len(_d_auto.bib_entries) == 13,
      f"map={len(_k)} bib={len(_d_auto.bib_entries)}")
_b = _d_auto.body
check("③ 纯引用 参见[12]。 → 参见\\parencite{K12}。",
      f"参见\\parencite{{{_k[12]}}}。" in _b)
check("③ 带页码 参见[13]，第8页。 → 参见\\parencite{K13}，第8页。",
      f"参见\\parencite{{{_k[13]}}}，第8页。" in _b)
check("③ 句中 参见吉尔伯特在[13]的批评。 → 在\\parencite{K13}的批评",
      f"参见吉尔伯特在\\parencite{{{_k[13]}}}的批评。" in _b)
check("③ 同脚注多编号 参见[5]与[12]。 → 两处 \\parencite",
      f"参见\\parencite{{{_k[5]}}}与\\parencite{{{_k[12]}}}。" in _b)
check("③ 越界 [99] 原样保留",
      "参见[99]。" in _b and "\\parencite{" + str(_k.get(99, "")) + "}" not in _b)
check("③ 疑似非引用 规则[3]条。 原样保留", "规则[3]条。" in _b)
check("③ 括号组（[6]，第434页）本层不动（build 的正文通道再转）",
      "（[6]，第434页）" in _b)
check("③ 脚注裸 [12]/[13] 已无残留",
      "参见[12]" not in _b and "参见吉尔伯特在[13]" not in _b)
check("③ 越界 + 疑似 各一条 warning",
      any("[99]" in w for w in _d_auto.warnings)
      and any("疑似非引用" in w and "[3]" in w for w in _d_auto.warnings),
      repr([w for w in _d_auto.warnings if "脚注内编号" in w]))
check("③ 正文括号组引用不受影响（（[1]）/（[13]）仍在 body，等 build 转）",
      "（[1]）" in _b and "（[13]）" in _b)


# ══ ④ 合成显式 `[N]` 清单稿（gate 的第二分支） ═══════════════════════════
_docx_expl = _make_docx(os.path.join(_TMP, "explicit.docx"),
                        ["参见[12]。", "参见[13]，第8页。"], numbering=False)
_d_expl = parse_docx(_docx_expl, c_class_union=True)
print("── ④ 合成显式 [N] 清单稿：无自动编号也要转 ──")
check("④ 显式清单：未走 B-022（ref_auto_materialized=0）但映射 1..13 连续",
      _d_expl.ref_auto_materialized == 0
      and sorted(_d_expl.ref_key_by_num) == list(range(1, 14)),
      f"auto={_d_expl.ref_auto_materialized} map={len(_d_expl.ref_key_by_num)}")
_ke = _d_expl.ref_key_by_num
check("④ 显式清单稿脚注裸编号同样转成 \\parencite",
      f"参见\\parencite{{{_ke[12]}}}。" in _d_expl.body
      and f"参见\\parencite{{{_ke[13]}}}，第8页。" in _d_expl.body)


# ══ ⑤ 非编号体例（人年制清单）：不转换 + 一条「未转换」warning ═══════════
parts = [_p("测试题名：人年制稿"), _p("张三"),
         _p("摘要：这是测试用摘要，用于合成文档解析。"), _p("关键词：测试"),
         _p("正文第一段，见作者（2001）。", fnrefs=(1,)),
         _p("参考文献")]
parts.extend(_p(t) for t in _refs13())
_docx_ay = os.path.join(_TMP, "author_year.docx")
with zipfile.ZipFile(_docx_ay, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("word/document.xml",
               f'<w:document {_NS}><w:body>' + "".join(parts) + "</w:body></w:document>")
    z.writestr("word/footnotes.xml",
               f'<w:footnotes {_NS}><w:footnote w:id="1">'
               + _p("参见[12]。") + "</w:footnote></w:footnotes>")
_d_ay = parse_docx(_docx_ay, c_class_union=True)
print("── ⑤ 非编号体例（无映射）：不转换 + 汇总 warning ──")
check("⑤ 人年制稿没有 ref_key_by_num", _d_ay.ref_key_by_num == {})
check("⑤ 脚注裸编号原样保留（不猜）", "参见[12]。" in _d_ay.body)
check("⑤ gate 不通过 → 一条「未转换」warning",
      any("未转换（不猜）" in w for w in _d_ay.warnings),
      repr([w for w in _d_ay.warnings if "脚注里" in w]))


# ══ ⑥ 端到端 build：.tex / bib 数字 ═══════════════════════════════════════
print("── ⑥ 端到端 build_docx：脚注裸编号进 .tex、bib 不变、无 \\nocite ──")


def _build_once(docx_path: str, tag: str):
    out = os.path.join(_TMP, f"out_{tag}")
    res = build.build_docx(docx_path=docx_path, out_dir=out, basename=f"Case_{tag}",
                           lang="zh", verbose=False)
    tex = open(res.tex_path, encoding="utf-8").read()
    bib_p = os.path.join(out, "reference.bib")
    bib = open(bib_p, encoding="utf-8").read() if os.path.isfile(bib_p) else ""
    return tex, bib, out


_tex, _bib, _out = _build_once(_docx_auto, "auto")
_k = _d_auto.ref_key_by_num
check("⑥ .tex 里脚注是 参见\\parencite{K12}。",
      f"参见\\parencite{{{_k[12]}}}。" in _tex)
check("⑥ .tex 里脚注页码引用 参见\\parencite{K13}，第8页。",
      f"参见\\parencite{{{_k[13]}}}，第8页。" in _tex)
check("⑥ .tex 裸 [12]/[13] 归零（含正文括号组已由正文通道转换）",
      "参见[12]" not in _tex and "参见吉尔伯特在[13]" not in _tex
      and "（[1]）" not in _tex and "（[13]）" not in _tex)
check("⑥ 未转换的 [99] 仍原样保留（透明，不吞内容）", "参见[99]。" in _tex)
check("⑥ reference.bib 13 条不变",
      len(re.findall(r"^@", _bib, re.M)) == 13,
      str(len(re.findall(r"^@", _bib, re.M))))
check("⑥ 产物无 \\nocite", "\\nocite" not in _tex)
_parent = re.findall(r"\\parencite\{([^}]*)\}", _tex)
_distinct = {x.strip() for p in _parent for x in p.split(",") if x.strip()}
check("⑥ 被引键 = 正文 2（[1]/[13]）+ 脚注 4（[5]/[6]/[12]/[13]，13 去重）",
      len(_distinct) == 5 and {_k[i] for i in (1, 5, 6, 12, 13)} == _distinct,
      repr(sorted(_distinct)))
check("⑥ 括号组脚注（[6]，第434页）由正文通道转成（\\parencite{K6}，第434页）",
      f"（\\parencite{{{_k[6]}}}，第434页）" in _tex)


# ══ ⑦ 默认零变化 + 确定性 ═════════════════════════════════════════════════
print("── ⑦ 默认零变化：脚注无裸编号时开/关 gate 的 body 逐字节相同 ──")
_docx_clean = _make_docx(os.path.join(_TMP, "clean.docx"),
                         ["这是普通的作者信息注释。", "另注：无需转引用。"],
                         numbering=True)
_on = parse_docx(_docx_clean, c_class_union=True)
_orig_gate = docxsource._footnote_num2key_gate
docxsource._footnote_num2key_gate = lambda doc: {}
try:
    _off = parse_docx(_docx_clean, c_class_union=True)
finally:
    docxsource._footnote_num2key_gate = _orig_gate
check("⑦ 无裸编号脚注：gate 开/关 body 逐字节相同（默认零变化）",
      _on.body == _off.body and _on.body != "")
check("⑦ 无裸编号脚注：不新增 warning",
      not any("脚注内编号" in w or "脚注里" in w for w in _on.warnings),
      repr([w for w in _on.warnings if "脚注" in w]))

_two = parse_docx(_docx_auto, c_class_union=True)
check("⑦ 确定性：同稿解析两遍 body / warnings 一致",
      _two.body == _d_auto.body and _two.warnings == _d_auto.warnings)
check("⑦ _footnote_bare_cite_gate_warning 空输入返回空串",
      _footnote_bare_cite_gate_warning({}) == "")

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
