#!/usr/bin/env python3.14
r"""引文 hoist：**脚注标记插在引文中间时不许硬替换**（第 141 条，2026-10-18）。

缺陷
----
`render_para()` 把超过阈值/含多个句末标点的引文提升成 `quotation` 环境。精确串
`“<content>”` 找不到时（引文被斜体/公式/脚注标记切开）会退化成
`re.sub(r"“[^”]{20,}”", ...)`，**替换第一处匹配**。

Word 里脚注标记常挂在引文中间（期 87 子案例 3 / 04 Fayang_Ye：脚注 6 在
「这种表示」后面）。此时：
  · 精确串当然对不上；
  · 退化正则从引文开头一直匹配到**脚注正文里的第一对短引号**（`“这种表示”`），
    于是把「引文前半 + `\footnote{…此处的“`」整段换成 quotation 块；
  · 引文后半被重复一遍、还漏出脚注的 `}` 和「以及后文的“标准表示”…」一段 →
    `! Too many }'s`，整篇编译失败。

修法：退化匹配里若第一处匹配含 `\footnote`，**放弃提升**（保持原文内联；人工成品
也是这么处理的），并给一条 warning。

本套件钉住：
  ① 纯长引文照旧提升；
  ② 引文中间插脚注 → 不提升、不重复、不吞脚注；
  ③ 引文被斜体切开（无脚注）→ 仍退化提升（既有能力不回退）；
  ④ 引文不足阈值/句末标点 → 不提升。

只写内存；不跑 xelatex、不读稿件库、不跑 corpus.py / build.sh。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_docx_quote_footnote.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.docxsource import hoist_quote_fallback, render_para  # noqa: E402
from slcn.docxxml import Para, Seg  # noqa: E402
from slcn.footnotes import Footnote  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


QUOTE_HEAD = "他们具体阐释道：“由于这种表示"
QUOTE_TAIL = ("的语义明确依赖于同伦论的术语和概念，因此标准表示显然是不自主的。"
              "对类型、项和恒等证明的解释依赖于对空间、点和路径的理解，"
              "这种理解不满足前数学意义，而是来源于同伦论数学。”")
FN_TEXT = "根据雷迪曼和普雷斯内尔的解释，此处的“这种表示”以及后文的“标准表示”指的是在[17]中给出的关于同伦类型论的概述与阐释。"


# ══════════════════════════════════════════════════════════════════════════
# ① 纯长引文：照旧提升
# ══════════════════════════════════════════════════════════════════════════
_para_ok = Para(segs=[Seg("text", QUOTE_HEAD + QUOTE_TAIL)])
_latex_ok, _notes_ok = render_para(_para_ok, {}, [], {}, quote_threshold=105)
check("① 纯长引文照旧提升为 quotation 环境",
      "\\begin{quotation}" in _latex_ok and "\\end{quotation}" in _latex_ok)
check("① 引文内容进 quotation（只出现一次）",
      _latex_ok.count("的语义明确依赖于") == 1)

# ══════════════════════════════════════════════════════════════════════════
# ② 引文中间插脚注：不提升、不重复、不吞脚注（旧实现在这里生成坏 LaTeX）
# ══════════════════════════════════════════════════════════════════════════
_para_fn = Para(segs=[
    Seg("text", QUOTE_HEAD),
    Seg("footnote", fid=6),
    Seg("text", QUOTE_TAIL),
])
_fn = {6: Footnote(6, FN_TEXT, kind="pure_note")}
_latex_fn, _notes_fn = render_para(_para_fn, _fn, [], {}, quote_threshold=105)
check("② 引文内含脚注 → 不提升（不生成 quotation 环境）",
      "\\begin{quotation}" not in _latex_fn)
check("② 脚注仍在（没有被吞掉）", "\\footnote{" in _latex_fn and "根据雷迪曼" in _latex_fn)
check("② 引文正文只出现一次（旧实现会重复一遍）",
      _latex_fn.count("的语义明确依赖于") == 1, str(_latex_fn.count("的语义明确依赖于")))
check("② 引文两半都在（脚注前后各一段）",
      "由于这种表示" in _latex_fn and "而是来源于同伦论数学。”" in _latex_fn)
check("② 脚注正文只出现一次（旧实现会漏出脚注右花括号、把同一段再铺一遍）",
      _latex_fn.count("以及后文的") == 1, str(_latex_fn.count("以及后文的")))
check("② 明确给出「未提升」提示（进 warnings）",
      any("未提升为 quotation" in n for n in _notes_fn), str(_notes_fn))
# 退化函数的直接单测：第一处匹配含 \footnote → 原样返回
_mixed = "“由于这种表示\\footnote{脚注正文“短引号”}的语义明确依赖于同伦论数学。”"
_out_mixed, _ok_mixed = hoist_quote_fallback(_mixed, "【QUOTED】")
check("② hoist_quote_fallback：匹配跨 \\footnote → 不替换、原文逐字不变",
      _ok_mixed is False and _out_mixed == _mixed)

# ══════════════════════════════════════════════════════════════════════════
# ③ 引文被斜体切开（无脚注）：仍退化提升（既有能力不回退）
# ══════════════════════════════════════════════════════════════════════════
_para_it = Para(segs=[
    Seg("text", QUOTE_HEAD),
    Seg("text", "的语义", italic=True),
    Seg("text", QUOTE_TAIL[len("的语义"):]),
])
_latex_it, _ = render_para(_para_it, {}, [], {}, quote_threshold=105)
check("③ 斜体切开的引文仍提升（退化匹配保留）",
      "\\begin{quotation}" in _latex_it)
check("③ 斜体切开的引文不重复", _latex_it.count("而是来源于同伦论数学") == 1)

# ══════════════════════════════════════════════════════════════════════════
# ④ 短引文 / 短语引用不提升（反例）
# ══════════════════════════════════════════════════════════════════════════
_para_short = Para(segs=[Seg("text", "他说：“科学家实际在做什么”，然后走了。")])
_latex_short, _ = render_para(_para_short, {}, [], {}, quote_threshold=105)
check("④ 短引文（不足阈值、句末标点 < 2）不提升",
      "\\begin{quotation}" not in _latex_short)
_out_none, _ok_none = hoist_quote_fallback("没有引号的一段文字", "【QUOTED】")
check("④ hoist_quote_fallback：没有长引文 → 原样返回", _ok_none is False)

# ══════════════════════════════════════════════════════════════════════════
# ⑤ B-220 N7：引文之后**紧随**已解析引用时，引用折进 quotation 环境、不再留
#    「【需补充参考文献与页码】」占位（实测 90-05：旧实现占位与引用重复印两处）
# ══════════════════════════════════════════════════════════════════════════
_para_cite = Para(segs=[
    Seg("text", QUOTE_HEAD + QUOTE_TAIL + "。（[5]，第40页）"),
])
_l7, _n7 = render_para(_para_cite, {}, [], {}, quote_threshold=105,
                       num2key={5: "Sosa2021"})
check("⑤ N7 紧随的编号引用折进 quotation（\hfill（\parencite…，第 40 页））",
      "\\hfill（\\parencite{Sosa2021}，第 40 页）" in _l7, _l7[-160:])
check("⑤ N7 不再留「需补充参考文献与页码」占位",
      "需补充参考文献与页码" not in _l7 and _l7.count("Sosa2021") == 1, _l7[-160:])
# 反例：引文后**没有**可解析引用 → 占位照旧（不编造）
_para_nocite = Para(segs=[Seg("text", QUOTE_HEAD + QUOTE_TAIL)])
_l7n, _n7n = render_para(_para_nocite, {}, [], {}, quote_threshold=105)
check("⑤ N7 反例：无可解析引用 → 占位照旧（不编造）",
      "需补充参考文献与页码" in _l7n)

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
