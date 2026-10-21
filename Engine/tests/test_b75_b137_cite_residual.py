#!/usr/bin/env python3.14
r"""批75 B-137：B-130 残余——脚注**同句引导语继承/枚举续引**（保守，不猜）。

背景（批73 B-130 残余；批74 41 篇复测实证）
------------------------------------------------
B-130 只转「逐处有引导语证据」的裸 `[N]`，于是同一脚注里**一个引导语带多个编号**时
只转首处，其余原样残留：

    kxj fn11：可参见[6]的第二节或者[10]的第2.1.2节。        （只转 [6]）
    87-3：  可参见[6]，第385-389页；[17]，第423-427页。     （只转 [6]）
    81/03： 请参见[6]，第2卷，第72-77页；[5]，…；[6]，…。    （只转首处 [6]）
    81/06： 可参见[1]；[8].                                  （只转 [1]）
    72/01： 请参见[1]，第100 页注释；[15]，第417–446页。       （只转 [1]）

B-137 判据（保守扩展，见 `docxsource.link_footnote_bare_citations`）
------------------------------------------------------------------
  · 一句 = 句号/问号/叹号/换行分隔（**分号、逗号不结束句子**，枚举在句内）；
  · 句内一处按原 B-130 判据转换后记锚点；锚点之后、同句内、与锚点之间只隔
    「位置语/连接语 + 标点/数字/西文字母」的裸 `[N]`（N∈可靠映射、括号外、公式外）
    → 继承引导语转换；可链式（第 3、4 处）；每个 N 独立校验映射与越界；
  · 中间夹普通叙述文字 / 跨句 / `[N]` 后紧跟 `条/款/项…`（「规则[3]条」类）→ 不转；
    有锚点但结构不符 → 原样 + 一条 warning（不猜）。
  · 拿不准一律保留原文；无命中稿/非编号体例稿逐字节不变。

本套件钉住（全部脱敏合成 docx，不读真实稿件、不联网、不写仓库）
 ① 纯函数：kxj/87-3/81-03/81-06/72-01 五形态 + 跨句不继承 + 叙述文字/`[3]条` 反例 +
    越界独立校验 + 括号组/公式静默 + 幂等 + 空映射；
 ② 合成 auto 编号稿 parse：同脚注两处都进 body、warning 不新增；
 ③ 合成显式 `[N]` 清单稿：同规则（gate 第二分支）；
 ④ 默认零变化：无同句枚举的稿子 gate 开/关 body 逐字节相同；
 ⑤ 确定性：同稿两遍解析 body/warnings 一致；
 ⑥ 端到端 build_docx：`.tex` 两处 `\parencite`、bib 条数不变、无 `\nocite`。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" python3 tests/test_b75_b137_cite_residual.py
"""

import os
import re
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build, docxsource  # noqa: E402
from slcn.docxsource import (  # noqa: E402
    _fn_inherit_gap_ok,
    link_footnote_bare_citations,
    parse_docx,
)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if not cond and detail else ""))


# ── 合成 docx（与 test_b73_footnote_bare_cite.py 同形，自包含） ──────────────
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
    return (f'<w:numbering {_NS}><w:abstractNum w:abstractNumId="1">'
            '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
            '<w:lvlText w:val="[%1]"/></w:lvl></w:abstractNum>'
            '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num></w:numbering>')


def _refs(n: int = 20) -> list[str]:
    return [f"A. Author{i}, {2000 + i}, “Test Title {i}”, Journal of Testing, "
            f"{i}(1): {i}-{i + 9}." for i in range(1, n + 1)]


def _make_docx(path: str, footnotes: list[str], *, numbering: bool,
               n_refs: int = 20) -> str:
    """合成编号体例稿：正文含 `（[1]）`/`（[n_refs]）` 作编号体例证据 + 脚注 + 文末清单。"""
    refs = _refs(n_refs)
    parts = [_p("测试题名：脚注同句引导语继承"), _p("张三"),
             _p("摘要：这是测试用摘要，用于合成文档解析。"),
             _p("关键词：测试；同句继承；枚举"),
             _p(f"正文第一段，含正文编号引用（[1]）与（[{n_refs}]）。",
                fnrefs=tuple(range(1, len(footnotes) + 1))),
             _p("参考文献")]
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
print("── ① link_footnote_bare_citations：同句引导语继承/枚举 ──")
M20 = {i: f"K{i}" for i in range(1, 21)}

# kxj fn11：一个引导语 + 「的第二节或者」续引
_new, _n, _iss = link_footnote_bare_citations(
    "${RCA_{0}}^{\\omega }$的形式定义可参见[6]的第二节或者[10]的第2.1.2节。", M20)
check("① kxj fn11：可参见[6]…或者[10]… → 两处都转",
      _n == 2 and "\\parencite{K6}" in _new and "\\parencite{K10}" in _new
      and _iss == [], repr(_new))

# 87-3：分号续引（分号不结束句子）
_new, _n, _iss = link_footnote_bare_citations(
    "详细内容，可参见[6]，第385-389页；[17]，第423-427页。", M20)
check("① 87-3：可参见[6]，第385-389页；[17]，第423-427页 → 两处都转（分号续引）",
      _n == 2 and _new == ("详细内容，可参见\\parencite{K6}，第385-389页；"
                           "\\parencite{K17}，第423-427页。"), repr(_new))

# 81/03：链式（第 2、3 处继承）
_new, _n, _ = link_footnote_bare_citations(
    "关于康德的存在观，请参见[6]，第2卷，第72-77页；[5]，第592-602页；[6]，第620-630页。", M20)
check("① 81/03：请参见[6]…；[5]…；[6]… → 三处都转（链式继承）",
      _n == 3 and _new == ("关于康德的存在观，请参见\\parencite{K6}，第2卷，第72-77页；"
                           "\\parencite{K5}，第592-602页；\\parencite{K6}，第620-630页。"),
      repr(_new))

# 81/03 半角分号 + §
_new, _n, _ = link_footnote_bare_citations(
    "关于弗雷格的观点，请参见[1]，§§46, 53; [2]，§12；[3]，第137-140, 173-175页。", M20)
check("① 81/03：半角 `;` + § 续引 → 三处都转", _n == 3
      and "\\parencite{K1}" in _new and "\\parencite{K2}" in _new
      and "\\parencite{K3}" in _new, repr(_new))

# 81/06：分号 + 句号（半角）
_new, _n, _ = link_footnote_bare_citations(
    "事实上，已经有很多学者关注过推理规则的规范性问题，具体可参见[1]；[8].", M20)
check("① 81/06：可参见[1]；[8]. → 两处都转",
      _n == 2 and _new.endswith("具体可参见\\parencite{K1}；\\parencite{K8}."), repr(_new))

# 72/01：「第100 页注释；」
_new, _n, _ = link_footnote_bare_citations(
    "请参见[1]，第100 页注释；[15]，第417–446页。", M20)
check("① 72/01：请参见[1]，第100 页注释；[15]，第417–446页 → 两处都转",
      _n == 2 and "\\parencite{K15}" in _new, repr(_new))

# 跨句不继承（句号结束）
_new, _n, _iss = link_footnote_bare_citations("参见[1]。这[8]点很重要。", M20)
check("① 跨句（句号）不继承：只转 [1]，[8] 原样 + warning",
      _n == 1 and "\\parencite{K1}" in _new and "[8]" in _new
      and any("[8]" in x for x in _iss), repr((_new, _iss)))

# 跨句但新句自带引导语 → 正常转换（不是继承）
_new, _n, _iss = link_footnote_bare_citations("参见[1]。另见[8]，第3页。", M20)
check("① 跨句但新句有新引导语 → 两处都转（走原 B-130 判据）",
      _n == 2 and "\\parencite{K1}" in _new and "\\parencite{K8}" in _new, repr(_new))

# 叙述文字夹在锚点与候选之间 → 不继承（注意：引导语本身 12 字内的既有通道仍会转，
# 所以锚点/引导语要隔远一点，才能测到 B-137 的继承判据本身）
_new, _n, _iss = link_footnote_bare_citations(
    "参见[1]，第100 页注释；作者认为这一点[3]很重要。", M20)
check("① 锚点后夹叙述文字 → 不继承（原样 + warning）",
      _n == 1 and "[3]" in _new and any("枚举/位置续引结构" in x for x in _iss),
      repr((_new, _iss)))

# `[3]条` 类非引用后缀 → 不继承
_new, _n, _iss = link_footnote_bare_citations("参见[1]，第3页；[3]条规则另见。", M20)
check("① 锚点后 `[3]条`（非引用后缀）→ 不继承",
      _n == 1 and "[3]条" in _new and any("枚举/位置续引结构" in x for x in _iss),
      repr((_new, _iss)))

# 越界编号独立校验：锚点不救越界
_new, _n, _iss = link_footnote_bare_citations("参见[1]；[99]。", M20)
check("① 继承位置上的越界编号独立校验：[99] 不转 + warning",
      _n == 1 and "\\parencite{K1}" in _new and "[99]" in _new
      and any("[99]" in x for x in _iss), repr((_new, _iss)))

# 未映射编号（N∉map）不转
_new, _n, _iss = link_footnote_bare_citations("参见[1]；[30]。", M20)
check("① N∉映射（30 > 1..20）→ 不转 + warning", _n == 1 and "[30]" in _new
      and any("[30]" in x for x in _iss), repr(_iss))

# 括号组归正文通道：即使同句有锚点也不动
_new, _n, _iss = link_footnote_bare_citations("参见[1]；（[6]，第434页）", M20)
check("① 括号组（同句）仍归正文通道：本层不转、不出 warning",
      _n == 1 and "（[6]，第434页）" in _new and _iss == [], repr(_new))

# 公式内编号：即使同句有锚点也不动
_new, _n, _iss = link_footnote_bare_citations("参见[1]与$[3]$。", M20)
check("① 公式内 [3]（同句）→ 不转、不出 warning",
      _n == 1 and "$[3]$" in _new and _iss == [], repr(_new))

# 空映射 / 无编号
_new, _n, _iss = link_footnote_bare_citations("可参见[6]或者[10]。", {})
check("① 空映射（非编号体例稿）→ 一个字都不动",
      _new == "可参见[6]或者[10]。" and _n == 0 and _iss == [])
_new, _n, _iss = link_footnote_bare_citations("普通注释，无编号。", M20)
check("① 无编号 → 原样", _n == 0 and _iss == [])

# 幂等：转换结果再跑一遍不再变化
_once, _n1, _ = link_footnote_bare_citations("可参见[6]；[10]，第2节。", M20)
_twice, _n2, _ = link_footnote_bare_citations(_once, M20)
check("① 幂等：继承转换结果再跑一遍不再变化",
      _n1 == 2 and _n2 == 0 and _twice == _once, repr(_twice))

# gap 判据直测（保守边界）
check("① gap 判据：位置语/连接语通过",
      _fn_inherit_gap_ok("，第385-389页；") and _fn_inherit_gap_ok("的第二节或者")
      and _fn_inherit_gap_ok("，§§46, 53; ") and _fn_inherit_gap_ok("；"))
check("① gap 判据：叙述文字/超长 → 不通过",
      not _fn_inherit_gap_ok("，作者认为")
      and not _fn_inherit_gap_ok("，第" + "1" * 40 + "页；"))


# ══ ② 合成 Word 自动编号稿（gate 第一分支） ═══════════════════════════════
_TMP = tempfile.mkdtemp(prefix="b75_b137_")
FN_AUTO = [
    "可参见[6]的第二节或者[10]的第2.1.2节。",          # kxj 形态
    "可参见[6]，第385-389页；[17]，第423-427页。",       # 87-3 形态
    "请参见[1]，第100 页注释；[15]，第417–446页。",      # 72/01 形态
    "参见[1]。这[8]点很重要。",                          # 跨句：不继承
    "规则[3]条不适用。",                                 # 非引用：不转
]
_docx_auto = _make_docx(os.path.join(_TMP, "auto.docx"), FN_AUTO, numbering=True)
_d_auto = parse_docx(_docx_auto, c_class_union=True)
_k = _d_auto.ref_key_by_num
print("── ② 合成 auto 编号稿：同脚注多处 → 都进 body ──")
check("② 映射 1..20 齐全（gate 第一分支放行）",
      sorted(_k) == list(range(1, 21)) and _d_auto.ref_auto_materialized == 20,
      f"map={len(_k)} auto={_d_auto.ref_auto_materialized}")
_b = _d_auto.body
check("② kxj 形态：两处 \\parencite",
      f"可参见\\parencite{{{_k[6]}}}的第二节或者\\parencite{{{_k[10]}}}的第2.1.2节。" in _b)
check("② 87-3 形态：分号续引两处 \\parencite",
      f"可参见\\parencite{{{_k[6]}}}，第385-389页；"
      f"\\parencite{{{_k[17]}}}，第423-427页。" in _b)
check("② 72/01 形态：两处 \\parencite（第100 页注释保留）",
      f"请参见\\parencite{{{_k[1]}}}，第100 页注释；"
      f"\\parencite{{{_k[15]}}}，第417–446页。" in _b)
check("② 跨句 [8] 原样保留（不继承）", "这[8]点很重要。" in _b)
check("② 非引用「规则[3]条」原样保留", "规则[3]条不适用。" in _b)
check("② 跨句/非引用各一条 warning",
      len([w for w in _d_auto.warnings if "脚注内编号" in w]) >= 2,
      repr([w for w in _d_auto.warnings if "脚注内编号" in w]))
check("② 正文括号组引用不受影响（等 build 正文通道转）",
      "（[1]）" in _b and "（[20]）" in _b)


# ══ ③ 合成显式 `[N]` 清单稿（gate 第二分支） ═══════════════════════════════
_docx_expl = _make_docx(os.path.join(_TMP, "explicit.docx"),
                        ["可参见[6]，第385-389页；[17]，第423-427页。"], numbering=False)
_d_expl = parse_docx(_docx_expl, c_class_union=True)
_ke = _d_expl.ref_key_by_num
print("── ③ 合成显式 [N] 清单稿：同规则（gate 第二分支） ──")
check("③ 显式清单稿 gate 第二分支放行（无 auto 物化）",
      _d_expl.ref_auto_materialized == 0 and sorted(_ke) == list(range(1, 21)))
check("③ 分号续引两处都转",
      f"可参见\\parencite{{{_ke[6]}}}，第385-389页；"
      f"\\parencite{{{_ke[17]}}}，第423-427页。" in _d_expl.body)


# ══ ④ 默认零变化：无同句枚举的稿子 gate 开/关 body 逐字节相同 ═════════════
_docx_clean = _make_docx(os.path.join(_TMP, "clean.docx"),
                         ["这是普通的作者信息注释。", "另注：无需转引用。"], numbering=True)
_on = parse_docx(_docx_clean, c_class_union=True)
_orig_gate = docxsource._footnote_num2key_gate
docxsource._footnote_num2key_gate = lambda doc: {}
try:
    _off = parse_docx(_docx_clean, c_class_union=True)
finally:
    docxsource._footnote_num2key_gate = _orig_gate
print("── ④ 默认零变化 + ⑤ 确定性 ──")
check("④ 无同句枚举的稿子：gate 开/关 body 逐字节相同", _on.body == _off.body and _on.body != "")
check("④ 非编号体例（gate 关）不新增「脚注内编号」warning",
      not any("脚注内编号" in w for w in _off.warnings),
      repr([w for w in _off.warnings if "脚注" in w]))
check("④ 无命中脚注的稿子（有普通引用）不因 B-137 新增 warning",
      not any("枚举/位置续引结构" in w for w in _on.warnings))


# ══ ⑤ 确定性 ═════════════════════════════════════════════════════════════
_two = parse_docx(_docx_auto, c_class_union=True)
check("⑤ 同稿解析两遍 body / warnings 一致",
      _two.body == _d_auto.body and _two.warnings == _d_auto.warnings)


# ══ ⑥ 端到端 build：.tex 两处 \parencite、bib 不变、无 \nocite ═══════════
print("── ⑥ 端到端 build_docx ──")
_out = os.path.join(_TMP, "out")
_res = build.build_docx(docx_path=_docx_auto, out_dir=_out, basename="Case_b137",
                        lang="zh", verbose=False)
_tex = open(_res.tex_path, encoding="utf-8").read()
_bib_p = os.path.join(_out, "reference.bib")
_bib = open(_bib_p, encoding="utf-8").read() if os.path.isfile(_bib_p) else ""
check("⑥ .tex 含 87-3 形态的两处 \\parencite（build 会把 `-` 规范成 \\cndash）",
      f"可参见\\parencite{{{_k[6]}}}，第385" in _tex
      and f"\\parencite{{{_k[17]}}}，第423" in _tex)
check("⑥ .tex 含 kxj 形态的两处 \\parencite",
      f"可参见\\parencite{{{_k[6]}}}的第二节或者\\parencite{{{_k[10]}}}的第2.1.2节。" in _tex)
check("⑥ .tex 里脚注裸 [6]/[10]/[17] 归零（正文括号组已由正文通道转换）",
      "可参见[6]" not in _tex and "或者[10]" not in _tex and "；[17]" not in _tex)
check("⑥ 未转换的跨句 [8] / 非引用 [3] 仍原样保留（透明，不吞内容）",
      "这[8]点" in _tex and "规则[3]条" in _tex)
check("⑥ reference.bib 20 条不变",
      len(re.findall(r"^@", _bib, re.M)) == 20, str(len(re.findall(r"^@", _bib, re.M))))
check("⑥ 产物无 \\nocite", "\\nocite" not in _tex)

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
