#!/usr/bin/env python3.14
r"""批112 B-312：**英文稿**的引用外层括号 + 脚注/引用位序（+ EN-03 初排生效）。

缺陷（`2026_02_*` 英文稿实测，数字见证据报告；本套件只用**合成**句子，不含任何稿件原文）
--------------------------------------------------------------------------------------
英文稿走 Word 通道 ⑤′ 时，`apply_proof_autofix` 被 `lang == "zh"` 挡住 → 英文稿的
一校安全子集（含 `_PROOF_AUTOFIX_IDS` 里早就写好的 EN-01/02/03）**从不上场**：
弯引号 368 处、破折号/省略号原样进了初排产物；正文引用一水儿是裸的
`\parencite[45]{Key}`（手册要求外面再包一层字面括号 `(\parencite[45]{Key})`）；
脚注/引用在逗号后的位序也没人搬。

修法（不新增规则 id —— `proof29+fmt11+v1` 是锁定契约）
------------------------------------------------------
① 两通道去掉 `lang == "zh"` 门（`build.py` ⑥′/⑤′），EN-01/02/03 自然生效；
② 英文位序 + 外层括号做成**内置 pass** `proofcheck.fix_en_marks`
   （`EN-10` 括号 / `EN-11` 位序记进 `rule_hits`，不进 `check()` 报告）；
③ 判据全用半角 `, .`，中文那三条（ZH-10/11/21）`langs = ("zh",)` 不受影响。

本套件钉住
----------
  ① 外层括号：裸命令包成 `(…)`、前置补空格；`\textcite`（行文引用）不包；
  ② 幂等/豁免：已包过的不重复包、脚注体里的引用不包、`( … )` 留白也认；
  ③ 逗号方向：`text, (\parencite{k})` → `text (\parencite{k}),`（空白跟着搬）；
  ④ 逗号方向（脚注）：`text,\footnote{…}` → `text\footnote{…},`；
  ⑤ 句号方向：`text (\parencite{k}).` → `text. (\parencite{k})`；
  ⑥ 句号方向（脚注）：`text\footnote{…}.` → `text. \footnote{…}`；
  ⑦ 反例：已合规、`;`/`:`、省略号、小数一律不动；
  ⑧ 幂等 + 计数键（EN-10/EN-11）；
  ⑨ 端到端 `build.apply_proof_autofix`（lang=en）：括号 + 位序 + EN-03 弯引号/
     破折号/省略号都改，`rule_hits` 同时含 EN-10/EN-11/EN-03；
  ⑩ 中文不变：同一段中文（含弯引号）走 lang=zh 逐字节不变（英文 pass 不外溢）。

不联网、不写仓库、不读真实稿件。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" python3.14 tests/test_b312_post_en_rules.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slcn import build, proofcheck   # noqa: E402

FAILS: list[str] = []


def check(msg: str, cond: bool, got: str = "") -> None:
    print(("  ✅ " if cond else "  ❌ ") + msg + ("" if cond else f"  got={got}"))
    if not cond:
        FAILS.append(msg)


print("── ① 外层括号（手册 §常规 5.2：正文引用外面再包一层字面括号）──")
_src = "Modal logic is old.\\parencite[45]{KeyA} More text."
_out, _n = proofcheck.en_outer_parens(_src)
check("① 裸 \\parencite[45]{KeyA} → (…) 且前面补空格",
      _out == "Modal logic is old. (\\parencite[45]{KeyA}) More text." and _n == 1,
      repr(_out))
_out, _n = proofcheck.en_outer_parens("See \\parencite{k1,k2} here.")
check("① 多键合并命令（\\parencite{k1,k2}）同样只包一层",
      _out == "See (\\parencite{k1,k2}) here." and _n == 1, repr(_out))
_out, _n = proofcheck.en_outer_parens("As \\textcite{k} argues.")
check("① \\textcite 是「作者（年）」行文引用 → 不包（改了意思就错）",
      _out == "As \\textcite{k} argues." and _n == 0, repr(_out))

print("\n── ② 幂等 / 豁免（不重复包、脚注体不包）──")
_out, _n = proofcheck.en_outer_parens("See (\\parencite{k}) here.")
check("② 已有外层括号不重复包", _out == "See (\\parencite{k}) here." and _n == 0, repr(_out))
_out, _n = proofcheck.en_outer_parens("See ( \\parencite{k} ) here.")
check("② 括号内有留白也认作已包", _out == "See ( \\parencite{k} ) here." and _n == 0,
      repr(_out))
_fn = "Text.\\footnote{See \\parencite[63]{K1} for the definition.}"
_out, _n = proofcheck.en_outer_parens(_fn)
check("② 脚注体里的引用不包（脚注里是整句，不是正文括注）",
      _out == _fn and _n == 0, repr(_out))

print("\n── ③ 逗号方向：引用放到逗号前（手册 §常规 5.1）──")
_out, _n = proofcheck.en_move_marks("the dyadic pair, (\\parencite{k}) and more")
check("③ 「逗号 + 空白 + 引用」→「引用 + 逗号」，空白跟着搬",
      _out == "the dyadic pair (\\parencite{k}), and more" and _n == 1, repr(_out))

print("\n── ④ 逗号方向（脚注，手册 §常规 4.1）──")
_out, _n = proofcheck.en_move_marks("the ordered pair,\\footnote{A note.} and more")
check("④ 「逗号 + 脚注」→「脚注 + 逗号」",
      _out == "the ordered pair\\footnote{A note.}, and more" and _n == 1, repr(_out))

print("\n── ⑤ 句号方向：引用放到句号后（手册 §常规 5.1）──")
_out, _n = proofcheck.en_move_marks("Modal logic is old (\\parencite{k}). More text.")
check("⑤ 「…引用.」→「…. 引用」",
      _out == "Modal logic is old. (\\parencite{k}) More text." and _n == 1, repr(_out))

print("\n── ⑥ 句号方向（脚注，手册 §常规 4.1）──")
_out, _n = proofcheck.en_move_marks("Modal logic is old\\footnote{A note.}. More text.")
check("⑥ 「…脚注.」→「…. 脚注」",
      _out == "Modal logic is old. \\footnote{A note.} More text." and _n == 1, repr(_out))

print("\n── ⑦ 反例：已合规 / 冒号分号 / 省略号 / 小数一律不动 ──")
for _s in ("already fine. (\\parencite{k}) More",
           "list: (\\parencite{k}) and more",
           "list; (\\parencite{k}) and more",
           "end (\\parencite{k})... more",
           "old (\\parencite{k}).5 more"):
    _out, _n = proofcheck.en_move_marks(_s)
    check(f"⑦ 不动：{_s}", _out == _s and _n == 0, repr((_out, _n)))

print("\n── ⑧ 幂等 + 计数键 ──")
_txt = "Text,\\footnote{A note.} old (\\parencite{k}). More."
_o1, _h1 = proofcheck.fix_en_marks(_txt)
_o2, _h2 = proofcheck.fix_en_marks(_o1)
check("⑧ 第一遍两处位序都搬（EN-11=2），第二遍零改动",
      _o1 == "Text\\footnote{A note.}, old. (\\parencite{k}) More."
      and _h1.get("EN-11") == 2 and _h2 == {} and _o2 == _o1,
      repr((_o1, _h1, _h2)))

print("\n── ⑨ 端到端：apply_proof_autofix(lang=en) 括号 + 位序 + EN-03 ──")
_en_tex = ("Studies in Logic “revised” version — a short note…\n"
           "The dyadic pair, (\\parencite{k}) is fine.\\parencite[45]{K1} More.\n")
_fixed, _hits = build.apply_proof_autofix(_en_tex, "en")
check("⑨ EN-03 弯引号换成 `` ''", "``revised''" in _fixed, repr(_fixed))
check("⑨ EN-03 破折号 —→---", " --- " in _fixed, repr(_fixed))
check("⑨ EN-03 省略号 …→\\ldots{}", "\\ldots{}" in _fixed, repr(_fixed))
check("⑨ EN-10 外层括号生效：裸引用全部变成 (\\parencite[…]{…})",
      "\\parencite" not in _fixed.replace("(\\parencite", ""), repr(_fixed))
check("⑨ EN-11 位序生效：句号后的引用不再有裸的 `.(\\parencite`",
      ". (\\parencite[45]{K1})" in _fixed, repr(_fixed))
check("⑨ rule_hits 记 EN-10/EN-11/EN-03",
      all(_hits.get(k, 0) >= 1 for k in ("EN-10", "EN-11", "EN-03")), repr(_hits))
check("⑨ EN-01/02 未误伤（行内 `:` 后接 `/` 的 URL 不动）",
      "https://example.org" in build.apply_proof_autofix(
          "See https://example.org for it.\n", "en")[0])

print("\n── ⑩ 中文稿逐字节不变（英文 pass 不外溢）──")
_zh = "甲，\\footnote{注文}乙。“引号”与破折号—照旧。\n"
_zh_fixed, _zh_hits = build.apply_proof_autofix(_zh, "zh")
check("⑩ lang=zh：弯引号/破折号原样（EN-03 不跑）",
      "“引号”" in _zh_fixed and "—" in _zh_fixed, repr(_zh_fixed))
check("⑩ lang=zh：脚注仍按 ZH-10 搬到逗号前（旧行为）",
      _zh_fixed.startswith("甲\\footnote{注文}，乙"), repr(_zh_fixed))
check("⑩ lang=zh：rule_hits 里没有 EN-10/EN-11",
      "EN-10" not in _zh_hits and "EN-11" not in _zh_hits, repr(_zh_hits))

print("\n── ⑪ 紧邻行内数学不再吞掉后文（屏蔽层配对修复）──")
# 缺陷：`$\lnot$$\Box$` 被旧的 `$$…$$` 正则错认成行间公式，后面整段正文被当数学区，
# 弯引号等文本规则全部漏报（本批现场稿 9 行、58 处弯引号因此残留）。
_adj = "We have $\\lnot$$\\Box$ x and \u201cquoted\u201d words.\n"
_vis = proofcheck.visible_mask(_adj)
check("⑪ 紧邻行内数学之后，弯引号仍在可见层",
      "\u201cquoted\u201d" in _vis, repr(_vis))
_mm = proofcheck.math_mask(_adj)
check("⑪ math_mask 只挖数学、不吞普通正文", "quoted" not in _mm and "\\lnot" in _mm, repr(_mm))
_fixed_adj, _ = proofcheck.apply_proof_autofix(_adj, "en") if hasattr(proofcheck, "apply_proof_autofix") else (None, {})
if _fixed_adj is None:  # 入口在 build 里
    _fixed_adj, _ = build.apply_proof_autofix(_adj, "en")
check("⑪ 端到端：数学区后的弯引号被 EN-03 修正", "``quoted''" in _fixed_adj, repr(_fixed_adj))
check("⑪ 数学区自身原样保留", "$\\lnot$$\\Box$" in _fixed_adj, repr(_fixed_adj))

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
