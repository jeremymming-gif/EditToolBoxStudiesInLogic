#!/usr/bin/env python3
r"""B-268 S3：引用库回填覆盖已有值时的冲突披露（old → new + 字段统计）。

覆盖（纯内存数据，不读库/不改 .bib）：
  · `conflict_summary()` 只把 `replaced`（覆盖已有非空值）算冲突，缺失→补齐不算；
  · 字段计数按次数降序、样本确定性、超量只报前 5 条 + 其余计数；
  · `format_conflicts()` / `format_checklist()` / 交接单 ① 都用同一份汇总。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_b268_conflict.py
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibdbapply, handoff  # noqa: E402

PASS = 0
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


_APPLIED = [
    {"key": "a", "etype": "article",
     "filled": {"publisher": "New Publisher", "pages": "10-20"},
     "replaced": {"publisher": "Old Publisher"}},
    {"key": "b", "etype": "book", "filled": {"year": "2001"}, "replaced": {}},
    {"key": "c", "etype": "book",
     "filled": {"address": "Berlin", "title": "New Title"},
     "replaced": {"address": "London", "title": "Old Title"}},
]

_counts, _samples = bibdbapply.conflict_summary(_APPLIED)
check("冲突只算覆盖已有值", _counts == {"publisher": 1, "address": 1, "title": 1}, _counts)
check("缺失→补齐不算冲突", "year" not in _counts and "pages" not in _counts)
check("样本带 old→new", ("a", "publisher", "Old Publisher", "New Publisher") in _samples, _samples)
check("样本顺序=applied 顺序", [s[0] for s in _samples] == ["a", "c", "c"])

_lines = bibdbapply.format_conflicts(_APPLIED)
_text = "\n".join(_lines)
check("统计行有「覆盖库值」与次数", "覆盖库值" in _text and "3 项" in _text, _lines[:1])
check("统计行按字段点名", "publisher 1" in _text and "title 1" in _text)
check("样本行含旧值/新值", "「Old Publisher」→「New Publisher」" in _text)
check("无冲突时不出块", bibdbapply.format_conflicts(
    [{"key": "z", "filled": {"pages": "1"}, "replaced": {}}]) == [])

_ctl = bibdbapply.format_checklist(_APPLIED, stem="X")
check("核对单回填块仍在", "引用库回填" in _ctl)
check("核对单含覆盖统计", "覆盖库值" in _ctl)
check("核对单逐条行仍在", "[article] a" in _ctl, _ctl[:80])

_h = handoff._conflict_lines({"done": True, "applied": _APPLIED})
_ht = "\n".join(_h)
check("交接单冲突块同口径", "回填冲突（⑦ 覆盖库内已有值）：3 项" in _ht, _h[:1])
check("交接单样本同口径", "[a] publisher：「Old Publisher」→「New Publisher」" in _ht)
check("交接单无 applied 时不出块", handoff._conflict_lines({"applied": []}) == [])

print()
print(f"PASS={PASS} FAIL={len(FAILS)}")
if FAILS:
    print("FAILED: " + "; ".join(FAILS))
sys.exit(len(FAILS))
