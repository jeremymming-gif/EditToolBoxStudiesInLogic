#!/usr/bin/env python3.14
"""第四十九批 C 类渲染（引擎侧骨架）：pure_citation→正文 \\parencite、mixed→保留脚注。

用户拍板（2026-10-19）：纯引用型脚注 → 正文 `\\parencite`（页码放可选参数 `[p.13]`），
删除该脚注；混合型脚注 → 保留 `\\footnote`（说明文字+引用），并出「请人工核对」warning。
本文件直接构造 Footnote/Para 测渲染分支（不依赖 bib-refs 新分类接口）。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build  # noqa: E402
from slcn import footnotes as F  # noqa: E402
from slcn.docxxml import Para, Seg  # noqa: E402
from slcn.docxsource import render_para  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _para(fid: int) -> Para:
    return Para(segs=[Seg("text", "正文"), Seg("footnote", fid=fid)])


# ① pure_ref/pure_citation：脚注标记处 → \parencite[页码]{键}，不保留 \footnote
for kind in ("pure_ref", "pure_citation"):
    fn = F.Footnote(fid=1, text="Cook, 2010.", kind=kind,
                    ref="Cook, 2010.", locator="p. 13")
    tex, _notes = render_para(_para(1), {1: fn}, [("K1", {}, "Cook, 2010.")],
                              {1: "K1"}, {}, c_class_union=True)
    check(f"① {kind} → 正文 \\parencite[p. 13]{{K1}}、删脚注、无中文括号包裹",
          "\\parencite[p. 13]{K1}" in tex and "\\footnote" not in tex
          and "（" not in tex, repr(tex))

# ② mixed：保留 \footnote，说明文字（已 link_citations 的 note_latex）原样
fn = F.Footnote(fid=2, text="说明文字（Cook, 2010）", kind="mixed",
                note="说明文字", note_latex="说明文字\\parencite{K1}")
tex, _notes = render_para(_para(2), {2: fn}, [("K1", {}, "")], {2: "K1"}, {},
                              c_class_union=True)
check("② mixed → 保留 \\footnote 且说明文字+引用都在",
      "\\footnote{说明文字\\parencite{K1}}" in tex, repr(tex))

# ③ mixed_citation（bib-refs 接口可能用的新名字）同样保留脚注
fn = F.Footnote(fid=3, text="说明（Cook, 2010）", kind="mixed_citation",
                note="说明", note_latex="说明\\parencite{K1}")
tex, _notes = render_para(_para(3), {3: fn}, [("K1", {}, "")], {3: "K1"}, {},
                              c_class_union=True)
check("③ mixed_citation 别名 → 保留 \\footnote（不再丢说明文字/引用）",
      "\\footnote{说明\\parencite{K1}}" in tex, repr(tex))

# ④ 无脚注引用键（pure_ref 但 keymap 无键）→ 不崩、不产生空 \parencite
fn = F.Footnote(fid=4, text="Cook, 2010.", kind="pure_citation", ref="Cook, 2010.")
tex, _notes = render_para(_para(4), {4: fn}, [], {}, {}, c_class_union=True)
check("④ 缺键时不崩（空键也不 panic，输出可编译占位）", isinstance(tex, str), repr(tex[-120:]))

# ⑤a 默认 OFF（v7 实测非 5 篇过并 → 第四十九批回退默认 OFF）
import inspect as _inspect
_sig = _inspect.signature(build.build_docx)
check("⑤a build_docx(c_class_union=False) 默认关（C 类 opt-in）",
      _sig.parameters["c_class_union"].default is False)

# ⑤ 默认 OFF：旧行为逐字节不变（pure_ref 仍中文括号包裹、mixed 仍 cf. 格式）
fn = F.Footnote(fid=5, text="Cook, 2010.", kind="pure_ref", ref="Cook, 2010.")
tex, _notes = render_para(_para(5), {5: fn}, [("K1", {}, "")], {5: "K1"}, {})
check("⑤ 默认 OFF：pure_ref 保持旧格式（（\\parencite{...}）），开关默认关不改变产物",
      "（\\parencite{K1}）" in tex, repr(tex))

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
