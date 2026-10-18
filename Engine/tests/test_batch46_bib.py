#!/usr/bin/env python3.14
"""第四十六批③：C 类口径开关判据 + 中文脚注补漏 的回归测试。

钉住（默认行为必须与现状逐字节一致 = 开关默认关）：
  ① `footnotes.footnote_promotion_allowed()`：
       · 默认（end_list_priority=False）→ 永远 True（含/不含文末清单都一样）；
       · 开启且**有**文末清单 → False（只保留文末清单）；
       · 开启但**没有**文末清单 → True（纯脚注体例不能把参考文献清空）；
  ② `footnotes.filter_footnote_entries()`：只保留编号清单键对应条目（供 build.py 接线）；
  ③ 86-03 中文脚注补漏：`2010第1期`（无「年」字）与会议/论坛报告也能判成文献；
  ④ `bibcheck.split_entries` 等既有行为不受影响（见 test_batch45_bib.py）。

不联网、不写库、不跑 corpus。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_batch46_bib.py
"""
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_ENGINE = _HERE.parent
for _p in (str(_ENGINE), str(_ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import footnotes  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ── ① 开关判据：默认关必须与现状完全一致 ─────────────────────────────
check("① 默认关：有文末清单 → 仍允许脚注提升（现行为）",
      footnotes.footnote_promotion_allowed(has_end_list=True) is True)
check("① 默认关：无文末清单 → 允许脚注提升（现行为）",
      footnotes.footnote_promotion_allowed(has_end_list=False) is True)
check("① 开关开：有文末清单 → 禁止脚注提升（只保留文末清单）",
      footnotes.footnote_promotion_allowed(has_end_list=True, end_list_priority=True) is False)
check("① 开关开：无文末清单 → 仍允许脚注提升（否则参考文献为空）",
      footnotes.footnote_promotion_allowed(has_end_list=False, end_list_priority=True) is True)
try:
    footnotes.footnote_promotion_allowed(True, True)  # type: ignore[call-arg]
    _positional_ok = True
except TypeError:
    _positional_ok = False
check("① 开关判据为 keyword-only（接线时不会传错顺序）", _positional_ok is False)

# ── ② build.py 接线用过滤器：只留编号清单键 ────────────────────────────
entries = [("Camap1962", {"title": "A"}, "raw1"),
           ("ref01", {"title": "B"}, "raw2"),
           ("Hong1982", {"title": "C"}, "raw3"),
           ("ref02", {"title": "D"}, "raw4")]
kept = footnotes.filter_footnote_entries(entries, {"Camap1962", "Hong1982"})
check("② 过滤器只保留编号清单键（脚注 refNN 全部丢弃）",
      [e[0] for e in kept] == ["Camap1962", "Hong1982"],
      str([e[0] for e in kept]))
check("② 过滤器 keep=空集 → 全部丢弃（纯脚注提升场景）",
      footnotes.filter_footnote_entries(entries, set()) == [])

# ── ③ 86-03 中文脚注补漏：年份后无「年」/会议报告也要能识别 ────────────
f_journal = footnotes.classify(
    "鞠实儿. 论逻辑的文化相对性——从民族志和历史学的观点看. 中国社会科学. 2010第1期，第36-47页。")
check("③ 年份后无「年」字（`2010第1期`）→ pure_ref（86-03 缺 2 条之一）",
      f_journal.kind == "pure_ref", f_journal.kind)
f_talk = footnotes.classify(
    "鞠实儿. The Foundation for Generalized Argumentation . 2019年第三届推理论证与传播国际论坛会议大会特邀报告.")
check("③ 会议/论坛特邀报告 → pure_ref（86-03 缺 1 条）",
      f_talk.kind == "pure_ref", f_talk.kind)
f_prose = footnotes.classify(
    "这里说的是语用论辩学由西方学者提出，广义论证由东方学者提出。（[20]，第36页；[21]，第8页）")
check("③ 纯编号定位/散文 → pure_note（不误判）", f_prose.kind == "pure_note", f_prose.kind)

print()
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS!r}")
    sys.exit(1)
print("失败 0 项（全部通过）")
