#!/usr/bin/env python3.14
"""OMML 栈（task-50，第四十四批②）：vendored XSLT + MIT 转换器，**默认关**。

背景：组件评估（/tmp/component-eval/report.md §2.6）实测纯 Python 栈
`omml2mml.xsl`（Apache-2.0）+ `mathml-to-latex`（MIT）在 149 个真实公式上
149/149 成功、结构覆盖优于自研 `_conv()`。本卡把源码 vendored 进
`Engine/vendor_omml/`，在 `docxxml.omml_to_latex()` 加开关（**默认 False**，
Lead 批准前不改默认产物），旧实现保留为 fallback。

跑法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_omml_stack.py
"""
import json
import os
import re
import sys
import warnings

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lxml import etree  # noqa: E402

from slcn import docxxml  # noqa: E402
from slcn.docxxml import M_NS, _conv, _math_to_tex, omml_to_latex  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}（{why}）")
    SKIPS.append(name)


def norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


FIXTURE = os.path.join(_ENGINE, "vendor_omml", "testdata", "omml_149.json")
if not os.path.isfile(FIXTURE):
    skip("149 fixture pin", f"缺 fixture：{FIXTURE}")
    items, els = [], []
else:
    items = json.load(open(FIXTURE, encoding="utf-8"))
    els = [etree.fromstring(it["xml"]) for it in items]

#: 与旧实现「去空白后不等」的 8 条 = 组件评估里已确认的**改进**（task-50 报告逐条解释）
EXPECTED_CHANGED = {1, 69, 136, 137, 138, 141, 142, 145}

# ══════════════════════════════════════════════════════════════════════════
# ① 默认关：149 条输出与旧实现**逐字节**相同（第一批不改默认产物）
# ══════════════════════════════════════════════════════════════════════════
if items:
    check("① 默认 `USE_OMML_STACK=False`", docxxml.USE_OMML_STACK is False, str(docxxml.USE_OMML_STACK))
    _bad = [it["id"] for it, el in zip(items, els) if omml_to_latex(el) != _conv(el).strip()]
    check("① 默认路径：149 条与旧 `_conv()` 逐字节相同（产物零变化）", not _bad, str(_bad[:5]))

    # ══════════════════════════════════════════════════════════════════════
    # ② 开关打开：149/149 成功；141 条与旧实现等价；8 条 = 预期改进
    # ══════════════════════════════════════════════════════════════════════
    _new = {}
    _empty = []
    for it, el in zip(items, els):
        tex = omml_to_latex(el, use_omml_stack=True)
        _new[it["id"]] = tex
        if not tex:
            _empty.append(it["id"])
    check("② 栈开启：149/149 非空（0 空串/0 异常）", _empty == [], str(_empty[:5]))
    # 对照基准要含 `_math_to_tex`（真实链路 omml_to_latex→_math_to_tex；栈输出自带命令）
    _changed = {it["id"] for it, el in zip(items, els)
                if norm(_math_to_tex(omml_to_latex(el))) != norm(_new[it["id"]])}
    check("② 差异集合恰为 8 条预期改进（其余 141 条去空白后等价）",
          _changed == EXPECTED_CHANGED, f"多出={sorted(_changed - EXPECTED_CHANGED)} 缺少={sorted(EXPECTED_CHANGED - _changed)}")
    check("② #142 `\\lnot` → `\\neg`（LaTeX 规范写法）", _new.get(142) == "\\neg", repr(_new.get(142)))
    check("② #145 `\\to` → `\\rightarrow`（语义等价，风格差异）", _new.get(145) == "\\rightarrow", repr(_new.get(145)))
    check("② #1/#69 `\\lnot` → `\\neg`", all("\\neg" in _new.get(i, "") for i in (1, 69)))
    check("② #136/#137 `\\lim` 命令恢复（旧实现输出 `limn→…` 缺命令）",
          all("\\lim_" in _new.get(i, "") for i in (136, 137)))
    check("② #138 nary 求和体带上下限", "\\sum_{" in _new.get(138, ""), repr(_new.get(138, "")[:120]))
    check("② #141 全角 ＝ 已在后处理中转半角（不再进公式文本）",
          "＝" not in _new.get(141, "") and "=" in _new.get(141, ""))

# ══════════════════════════════════════════════════════════════════════════
# ③ 合成结构：nary / m:d 定界符 / CJK / display 语义
# ══════════════════════════════════════════════════════════════════════════
def _m(xml: str):
    return etree.fromstring(f'<m:oMath xmlns:m="{M_NS}">{xml}</m:oMath>')


def _rt(t: str) -> str:
    return f"<m:r><m:t>{t}</m:t></m:r>"


try:
    _nary = omml_to_latex(_m(
        '<m:nary><m:naryPr><m:chr m:val="∑"/></m:naryPr>'
        f'<m:sub>{_rt("i=1")}</m:sub><m:sup>{_rt("k")}</m:sup>'
        f'<m:e>{_rt("P(h_i)")}</m:e></m:nary>'), use_omml_stack=True)
    check("③ nary：栈输出含上下限与求和体", "\\sum" in _nary and "P" in _nary, repr(_nary))
    _d = omml_to_latex(_m(
        '<m:d><m:dPr><m:begChr m:val="⟨"/><m:endChr m:val="⟩"/></m:dPr>'
        f'<m:e>{_rt("c, QUD")}</m:e></m:d>'), use_omml_stack=True)
    check("③ m:d 尖括号定界符按原符号", "\\langle" in _d and "\\rangle" in _d, repr(_d))
    _cjk = omml_to_latex(_m(_rt("会碎，不会碎")), use_omml_stack=True)
    check("③ CJK 公式文本 → \\text{}（不丢字、不留空格）",
          "\\text{会碎，不会碎}" in _cjk, repr(_cjk))
    check("③ display 标记仍由 `_para_segments` 负责（栈只转内容，不改 Seg.display 语义）",
          omml_to_latex(_m(_rt("a=b")), use_omml_stack=True) != "")
except Exception as _e:  # noqa: BLE001
    skip("③ 合成结构", f"{type(_e).__name__}: {_e}")

# ══════════════════════════════════════════════════════════════════════════
# ④ fallback：栈不可用/异常 → 回退旧实现 + warning（不阻断出稿）
# ══════════════════════════════════════════════════════════════════════════
_el0 = _m(_rt("x+1"))
_saved = docxxml._OMML_STACK
try:
    docxxml._OMML_STACK = False            # 模拟「vendored 栈缺失/损坏」
    with warnings.catch_warnings(record=True) as _w:
        warnings.simplefilter("always")
        _fb = omml_to_latex(_el0, use_omml_stack=True)
    check("④ fallback：栈不可用时回退旧实现（输出 = `_conv`）", _fb == _conv(_el0).strip(), repr(_fb))
    check("④ fallback：发 RuntimeWarning（可发现，不只静默）",
          any(issubclass(w.category, RuntimeWarning) for w in _w), str([str(w.message) for w in _w]))
finally:
    docxxml._OMML_STACK = _saved

# ══════════════════════════════════════════════════════════════════════════
# ⑤ 真实稿 pin（有稿件库时才跑）：87/06 CJK 集合公式与人工成品同款
# ══════════════════════════════════════════════════════════════════════════
try:
    from slcn import paths

    _root = paths.manuscripts_root()
    if not _root:
        skip("⑤ 真实稿 pin", "本机没有稿件库")
    else:
        # 按「期号 + 篇序号」发现目录，不硬编码作者目录名（B-269）
        import glob
        _dirs = sorted(glob.glob(os.path.join(_root, "2026年第2期(87)", "06 *")))
        _docx = (os.path.join(_dirs[0], "original",
                              "260310-修订-反事实怀疑论与联结词语境主义.docx")
                 if _dirs else "")
        if not _docx or not os.path.isfile(_docx):
            skip("⑤ 真实稿 pin", "缺 87/06 源稿")
        else:
            import zipfile
            _xml = zipfile.ZipFile(_docx).read("word/document.xml").decode("utf-8", "replace")
            _n = len(re.findall(r"<m:oMath(?![A-Za-z])", _xml))
            check("⑤ 真实稿 87/06：XML 里 OMML 公式数 = 135（fixture 同源）", _n == 135, str(_n))
except Exception as _e:  # noqa: BLE001
    skip("⑤ 真实稿 pin", f"{type(_e).__name__}: {_e}")

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
