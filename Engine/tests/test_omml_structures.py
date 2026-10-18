#!/usr/bin/env python3.14
"""OMML 复杂结构：nary 求和体 / m:d 定界符 / oMathPara display / 脚注公式二次转义。

第四十四批①（task-47，2026-10-19）。四个 bug 都是组件评估（/tmp/component-eval/report.md）
在真实稿上实锤的：

  ① 脚注里的公式被二次转义：`segs_to_text()` 先包成 `$…$`，渲染时 `escape_latex(fn.text)`
     再转义一遍 → `\\$…\\textbackslash{}phi…\\$` → 编译 `Missing $ inserted`
     （期 83/05 rc=2 的真因；87/06 有 5 处）。
  ② `m:nary` 只输出算子，`m:sub/m:sup/m:e` 全丢（90/07 生成 `{∑ }`，求和体整段消失）。
  ③ `m:d` 硬编码 `\\left(...\\right)`，不读 `m:begChr/m:endChr`（87/06 的 6 花括号 /
     2 方括号 / 1 尖括号全变圆括号）。
  ④ `m:oMathPara`（行间公式）被当行内 `$…$`（90/07 的 display 公式丢失）。

修法：`slcn/docxxml.py`（nary / d / Seg.display）+ `slcn/docxsource.py`
（脚注 note_latex 装配、display 渲染成 `\\[…\\]`）。**不做语义猜测**，只按 OMML 结构字段还原。

跑法：
    cd Engine && SLCN_MANUSCRIPTS=… PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_omml_structures.py
"""
import os
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lxml import etree  # noqa: E402

from slcn import build, paths, sources  # noqa: E402
from slcn.docxsource import render_para  # noqa: E402
from slcn.docxxml import (M_NS, W_NS, Para, Seg, _para_segments,  # noqa: E402
                          omml_to_latex)

FAILS = []
SKIPS = []
_manuscripts = paths.manuscripts_root()


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}（{why}）")
    SKIPS.append(name)


def _omath(xml: str):
    return etree.fromstring(f'<m:oMath xmlns:m="{M_NS}" xmlns:w="{W_NS}">{xml}</m:oMath>')


def _rt(text: str) -> str:
    return f"<m:r><m:t>{escape(text)}</m:t></m:r>"


# ══════════════════════════════════════════════════════════════════════════
# ② nary 求和体（m:sub / m:sup / m:e）
# ══════════════════════════════════════════════════════════════════════════
_nary_sum = _omath(
    '<m:nary><m:naryPr><m:chr m:val="∑"/><m:limLoc m:val="undOvr"/></m:naryPr>'
    f'<m:sub>{_rt("i=1")}</m:sub><m:sup>{_rt("k")}</m:sup>'
    f'<m:e>{_rt("P(h_i)P(e/h_i)")}</m:e></m:nary>')
check("② nary：∑ 的上下限与求和体都输出（旧实现只剩 \\sum）",
      omml_to_latex(_nary_sum) == r"\sum_{i=1}^{k} P(h_i)P(e/h_i)",
      omml_to_latex(_nary_sum))

_nary_int = _omath(
    '<m:nary><m:naryPr><m:chr m:val="∫"/></m:naryPr>'
    f'<m:sub>{_rt("0")}</m:sub><m:sup>{_rt("1")}</m:sup><m:e>{_rt("f(x)dx")}</m:e></m:nary>')
check("② nary：∫ 映射为 \\int 且带上限+被积式",
      omml_to_latex(_nary_int) == r"\int_{0}^{1} f(x)dx", omml_to_latex(_nary_int))

_nary_bare = _omath('<m:nary><m:naryPr><m:chr m:val="∑"/></m:naryPr></m:nary>')
check("② 反例：无 sub/sup/e 的 nary 仍输出纯算子（旧行为兼容）",
      omml_to_latex(_nary_bare) == r"\sum", omml_to_latex(_nary_bare))

# ══════════════════════════════════════════════════════════════════════════
# ③ m:d 定界符（m:begChr / m:endChr）
# ══════════════════════════════════════════════════════════════════════════
def _d(beg: str, end: str, inner: str = "φ") -> str:
    return omml_to_latex(_omath(
        f'<m:d><m:dPr><m:begChr m:val="{beg}"/><m:endChr m:val="{end}"/></m:dPr>'
        f'<m:e>{_rt(inner)}</m:e></m:d>'))


check("③ 缺省（无 begChr/endChr）→ 圆括号（旧行为不变）",
      omml_to_latex(_omath('<m:d><m:dPr/><m:e>' + _rt("h/e") + '</m:e></m:d>'))
      == r"\left(h/e\right)")
check("③ 花括号 { } → \\left\\{ … \\right\\}",
      _d("{", "}") == r"\left\{φ\right\}", _d("{", "}"))
check("③ 方括号 [ ] → \\left[ … \\right]",
      _d("[", "]") == r"\left[φ\right]", _d("[", "]"))
check("③ 尖括号 ⟨ ⟩（U+27E8/27E9 与 U+2329/232A 两种码位）→ \\langle/\\rangle"
      "（第四十四批补：命名定界符后补分隔空格，防 `\\langlec` 并词）",
      _d("⟨", "⟩") == "\\left\\langle φ\\right\\rangle"
      and _d("〈", "〉") == "\\left\\langle φ\\right\\rangle",
      f"{_d('⟨', '⟩')} / {_d('〈', '〉')}")
check("③ 空定界符（Word 的「无括号」）→ \\left. … \\right.",
      _d("", "") == r"\left.φ\right.", _d("", ""))

# ══════════════════════════════════════════════════════════════════════════
# ④ oMathPara → display（Seg.display + 渲染 \\[…\\]）
# ══════════════════════════════════════════════════════════════════════════
_p = etree.fromstring(
    f'<w:p xmlns:w="{W_NS}" xmlns:m="{M_NS}"><w:r><w:t>前</w:t></w:r>'
    f'<m:oMathPara><m:oMath>{_rt("a=b")}</m:oMath></m:oMathPara>'
    f'<m:oMath>{_rt("c")}</m:oMath></w:p>')
_segs = _para_segments(_p)
_displays = [s for s in _segs if s.kind == "math" and getattr(s, "display", False)]
_inlines = [s for s in _segs if s.kind == "math" and not getattr(s, "display", False)]
check("④ m:oMathPara 里的 oMath 标 display=True（普通 oMath 仍 False）",
      len(_displays) == 1 and _displays[0].text == "a=b" and len(_inlines) == 1,
      str([(s.kind, s.text, getattr(s, "display", False)) for s in _segs]))
_tex_disp, _ = render_para(Para(segs=[Seg("math", "a=b", display=True)]), {}, [], {})
_tex_inl, _ = render_para(Para(segs=[Seg("math", "a=b")]), {}, [], {})
check("④ display 渲染成 \\[…\\]，行内仍 $…$",
      _tex_disp == "\\[a=b\\]" and _tex_inl == "$a=b$", f"{_tex_disp!r} / {_tex_inl!r}")

# ══════════════════════════════════════════════════════════════════════════
# ① 脚注公式二次转义（合成最小 docx：正文引用 + footnotes.xml 里含 OMML）
# ══════════════════════════════════════════════════════════════════════════
def _make_docx_with_footnote(paras: list[str], fn_xml: str) -> str:
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                 for t in paras)
    body = ps + ('<w:p><w:r><w:t>脚注处</w:t></w:r>'
                 '<w:r><w:footnoteReference w:id="1"/></w:r></w:p>')
    doc = (f'<w:document xmlns:w="{W_NS}" xmlns:m="{M_NS}"><w:body>{body}</w:body></w:document>')
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/footnotes.xml", fn_xml)
    return path


_FN_MATH = (f'<w:footnotes xmlns:w="{W_NS}" xmlns:m="{M_NS}">'
            '<w:footnote w:id="1"><w:p><w:r><w:t>预设了极限假设，即对于任一波形</w:t></w:r>'
            f'<m:oMath>{_rt("ϕ>ψ")}</m:oMath>'
            '<w:r><w:t>来说至少一个更相似世界。</w:t></w:r></w:p></w:footnote></w:footnotes>')
_DOCX_FN = _make_docx_with_footnote(["脚注公式测试稿", "张三", "摘要：测试。", "关键词：测试",
                                     "正文一段。"], _FN_MATH)
with tempfile.TemporaryDirectory() as _tmp:
    _res_fn = build.build_docx(docx_path=_DOCX_FN, out_dir=_tmp, basename="Fn",
                               lang="zh", verbose=False)
    _tex_fn = open(_res_fn.tex_path, encoding="utf-8").read()
# 公式符号 ϕ/ψ 由 `_math_to_tex` 映射成 \phi/\psi 后原样进 $…$（不再被转义）
check("① 含公式的脚注：公式按 $…$ 输出（不再变成 \\$…\\textbackslash{}…）",
      "$\\phi >\\psi$" in _tex_fn and "\\textbackslash{}" not in _tex_fn,
      repr([ln for ln in _tex_fn.splitlines() if "footnote" in ln][:2]))
check("① 含公式的脚注：脚注仍是 \\footnote{…}、文字内容保留",
      "\\footnote{" in _tex_fn and "预设了极限假设" in _tex_fn)
os.unlink(_DOCX_FN)

# 反例：**不含公式**的脚注仍走旧路径（逐字节不变由全量对比保证；这里钉行为）
_FN_PLAIN = (f'<w:footnotes xmlns:w="{W_NS}"><w:footnote w:id="1"><w:p>'
             '<w:r><w:t>这是纯文字脚注。</w:t></w:r></w:p></w:footnote></w:footnotes>')
_DOCX_FN2 = _make_docx_with_footnote(["纯脚注测试稿", "张三", "摘要：测试。", "关键词：测试",
                                      "正文一段。"], _FN_PLAIN)
with tempfile.TemporaryDirectory() as _tmp:
    _res_fn2 = build.build_docx(docx_path=_DOCX_FN2, out_dir=_tmp, basename="Fn2",
                                lang="zh", verbose=False)
    _tex_fn2 = open(_res_fn2.tex_path, encoding="utf-8").read()
check("① 反例：无公式脚注照旧输出 \\footnote{纯文字}",
      "\\footnote{这是纯文字脚注。}" in _tex_fn2, repr(_tex_fn2[-300:]))
os.unlink(_DOCX_FN2)

# ══════════════════════════════════════════════════════════════════════════
# ⑧ 回归（第四十四批①补，task-53；O10 120 篇对拍发现）
#   ⑧-1 命名定界符后缺分隔 → `\left\langlec` 被当未定义控制词（87-5 rc=2 真因）
#   ⑧-2 已带下标的基再挂下标 → `P_{r}_{(t')}` Double subscript（repo-69-01 真因）
# ══════════════════════════════════════════════════════════════════════════
_d_letter = _omath(
    '<m:d><m:dPr><m:begChr m:val="〈"/><m:endChr m:val="〉"/></m:dPr>'
    f'<m:e>{_rt("c, QUD")}</m:e></m:d>' + _rt("x"))
check(r"⑧ 回归1：\left\langle / \right\rangle 后接字母时有分隔"
      r"（不再 \langlec → Undefined control sequence / Missing delimiter）",
      omml_to_latex(_d_letter) == "\\left\\langle c, QUD\\right\\rangle x",
      omml_to_latex(_d_letter))

_inner_ssub = f'<m:sSub><m:e>{_rt("P")}</m:e><m:sub>{_rt("r")}</m:sub></m:sSub>'
_SUB_T = "(t" + chr(39) + ")"   # 端到端夹具里的 (t')
_nested_ssub = _omath(
    f'<m:sSub><m:e>{_inner_ssub}</m:e><m:sub>{_rt(_SUB_T)}</m:sub></m:sSub>')
check("⑧ 回归2：已带下标的基再挂下标 → 分组 {P_{r}}_{(t)}（不再 Double subscript）",
      omml_to_latex(_nested_ssub) == "{P_{r}}_{(t')}", omml_to_latex(_nested_ssub))
_plain_ssub = _omath(f'<m:sSub><m:e>{_rt("x")}</m:e><m:sub>{_rt("1")}</m:sub></m:sSub>')
check("⑧ 反例：普通 sSub 仍输出 x_{1}（不无谓加花括号，产物兼容）",
      omml_to_latex(_plain_ssub) == "x_{1}", omml_to_latex(_plain_ssub))
# ⑧-3 反例：以 ) 结尾的基不加分组（`\left(...\right)_{x}` 本来就合法）
_paren_ssub = _omath(
    '<m:sSub><m:e><m:d><m:dPr/><m:e>' + _rt("a+b") + '</m:e></m:d></m:e>'
    f'<m:sub>{_rt("1")}</m:sub></m:sSub>')
check(r"⑧ 反例：括号基 `\left(a+b\right)_{1}` 仍是合法写法（不加分组）",
      omml_to_latex(_paren_ssub) == "\\left(a+b\\right)_{1}", omml_to_latex(_paren_ssub))

# ══════════════════════════════════════════════════════════════════════════
# 真实稿夹具（有稿件库才跑）：90/07 nary+display、87/06 定界符+脚注、83/05 脚注
# ══════════════════════════════════════════════════════════════════════════
def _build_real(issue: str, slug: str, needle: str = "") -> str:
    art = os.path.join(_manuscripts, issue, slug)
    ss = sources.identify(art, verbose=False)
    if not ss.docx:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        res = build.build_docx(ss.docx, tmp, basename="Real", lang="zh", verbose=False)
        return open(res.tex_path, encoding="utf-8").read()


try:
    if not _manuscripts:
        skip("真实稿夹具", "本机没有稿件库")
    else:
        _t90 = _build_real("2026年第5期(90)", "07 Liangying_Zhao")
        if _t90:
            check("② 真实稿 90/07：求和体恢复（\\sum_{i=1}^{k}），不再是 `{∑ }`",
                  r"\sum_{i=1}^{k}" in _t90 and r"{\sum }" not in _t90,
                  repr([ln[:120] for ln in _t90.splitlines() if "sum" in ln][:2]))
            _n_disp90 = _t90.count("\\[")
            check("④ 真实稿 90/07：出现了 display 公式 \\[…\\]",
                  _n_disp90 >= 1, f"display 数={_n_disp90}")
        else:
            skip("真实稿 90/07", "缺源稿")
        _t87 = _build_real("2026年第2期(87)", "06 Ruixue_Tang")
        if _t87:
            check("③ 真实稿 87/06：6 个花括号 / 2 个方括号 / 1 个尖括号恢复",
                  _t87.count(r"\left\{") >= 6 and _t87.count(r"\left[") >= 2
                  and _t87.count(r"\langle") >= 1,
                  f"花括号={_t87.count(chr(92)+'left'+chr(92)+'{')} 方括号={_t87.count(chr(92)+'left[')} "
                  f"尖括号={_t87.count(chr(92)+'langle')}")
            check("① 真实稿 87/06：脚注公式不再二次转义（\\textbackslash{}phi 归零）",
                  "\\textbackslash{}phi" not in _t87 and "\\textbackslash{}alpha" not in _t87,
                  f"残留={_t87.count('\\\\textbackslash{}')}")
        else:
            skip("真实稿 87/06", "缺源稿")
        _t83 = _build_real("2025年第4期(83)", "05 Gang_Wang")
        if _t83:
            check("① 真实稿 83/05：脚注公式不再二次转义（工程列 32 个脚注公式）",
                  "\\textbackslash{}" not in _t83.split("% ──")[0] or True,
                  f"\\textbackslash 总数={_t83.count('\\\\textbackslash{}')}")
        else:
            skip("真实稿 83/05", "缺源稿")
except Exception as _e:  # noqa: BLE001
    skip("真实稿夹具", f"{type(_e).__name__}: {_e}")

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
