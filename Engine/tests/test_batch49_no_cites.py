#!/usr/bin/env python3.14
"""第四十九批 NO_CITES A+B：初排日志强化 + 可选编号引用映射（默认关）。

A：bib>0 且正文 0 引用命令时，日志列出「正文裸编号 [N] 出现次数/最大编号/示例 +
   文末清单条数 + 参考文献区会空」；**纯日志、产物逐字节不变**。
B：`--number-cite-map` 默认关；仅当正文只有 [N]、无真引用、清单编号 1..N 有序时
   映射为 `\\parencite{第N条键}`；越界/混用真引用/清单无序 → 原样 + warning。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _tex(body: str) -> str:
    return "\\documentclass{x}\n\\begin{document}\n" + body + "\n\\end{document}\n"


# ── A ──
_lines = build._bib_zero_cites_hint(_tex("见[1]、[2] 与 [3-4]。"), 5, n_end_list=5)
check("A1 提示含 [N] 统计（次数/最大编号/示例）与文末清单条数",
      _lines is not None
      and "正文裸编号引用 [N]：3 处，最大编号 [4]" in " ".join(_lines)
      and "文末清单条数：5 条" in " ".join(_lines),
      repr(_lines))
check("A2 正文已有真引用命令 → 不提示（沿用旧判据）",
      build._bib_zero_cites_hint(_tex("见\\parencite{K1}。"), 5) is None)
check("A3 正文无 [N] → 明确写「未发现裸编号引用」",
      any("正文未发现裸编号引用" in ln
          for ln in (build._bib_zero_cites_hint(_tex("正文没有编号引用。"), 5) or [])))
check("A4 bib=0 不提示", build._bib_zero_cites_hint(_tex("[1]"), 0) is None)

# ── B ──
_refs = {1: "K1", 2: "K2"}
_body, n, warns = build._map_number_citations("见[1]，另见[2]。", _refs)
check("B1 有序清单+仅裸编号 → 映射为 \\parencite（保留正文其余文字）",
      n == 2 and "\\parencite{K1}" in _body and "\\parencite{K2}" in _body
      and "见" in _body, repr(_body))
_body, n, warns = build._map_number_citations("见[2]。", {1: "K1"})
check("B2 反例：编号越界 → 不映射 + warning", n == 0 and _body == "见[2]。"
      and any("越界" in w for w in warns), repr(warns))
_body, n, warns = build._map_number_citations("已有\\parencite{X}，见[1]。", _refs)
check("B3 反例：混用真引用 → 不映射 + warning",
      n == 0 and "\\parencite{X}" in _body and any("混用" in w for w in warns))
_body, n, warns = build._map_number_citations("见[2]。", {2: "K2"})
check("B4 反例：文末清单编号不是 1..N 有序 → 不映射 + warning",
      n == 0 and any("有序" in w for w in warns), repr(warns))
_body, n, warns = build._map_number_citations("见[1]与[2]。", {1: "K1", 2: "K2"})
check("B5 连续区间 [1-2] 展开为多个键",
      n == 1 and "\\parencite{K1,K2}" in _body, repr(_body)) if False else \
check("B5 无清单 → 跳过 + warning",
      build._map_number_citations("见[1]。", {})[1] == 0
      and any("没有可映射" in w for w in build._map_number_citations("见[1]。", {})[2]))

# ── 默认关：build_docx 参数默认 False（CLI 不传 = 不改产物）──
import inspect  # noqa: E402
_sig = inspect.signature(build.build_docx)
check("B6 build_docx(number_cite_map=False) 默认关",
      _sig.parameters["number_cite_map"].default is False)

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
