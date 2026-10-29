#!/usr/bin/env python3
r"""B-268 S2：交接单计数的「生成时点值 + 回填后值」双值与未命中点名。

覆盖（全部离线、纯字符串断言）：
  · ① 首行保留旧口径，紧跟一行注明 ⑥ 生成期 / ⑦ 回填后两个时点（同一 bibcheck 实现重算）；
  · 引用库未命中条目点名（`[etype] key「title」`），允许超量截断；
  · ② 新增「已写入但形态非法」：titleEN 写成邮箱、authorEN 写成单位行；
    **两行式（`\\newcommand{\\mytitleEN}` + 下一行 `{值}`，模板实产）与行内式同判**；
  · 空结果仍四节齐全且「无」不少于三处（守住既有约定）。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_b268_report.py
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import handoff  # noqa: E402

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


_BIB = {"total": 2, "must": 1, "nice": 0, "unstructured": 0,
        "report_path": "B_参考文献核对.txt"}
_BIBDB = {
    "done": True,
    "safe": {"k_a"},
    "candidates": {},
    "missed": ["k_miss"],
    "entries": [("misc", "k_miss", "title = {Synthetic Missing Title},\n  year = {2001},")],
    "applied": [{"key": "k_a", "etype": "article",
                 "filled": {"pages": "1-2"},
                 "replaced": {"publisher": "Old Publisher"}}],
    "filled": 3,
}
_TEX = ("\\mytitleEN{Synthetic English Title}\n"
        "\\myfirstauthorEN{Department of Philosophy}\n"
        "\\mytitleEN{someone@example.edu}\n")

#: 模板真实产物的两行式形态：`\newcommand{\mytitleEN}`（行尾注释）+ 下一行 `{值}`
#:（中间允许空行）。值全为抽象样例，不含稿件原文/真实姓名/邮箱。
_TEX_TWOLINE = ("\\newcommand{\\mytitleEN}                       % 请输入英文标题\n"
                "\n"
                "{someone@example.edu}\n"
                "\\newcommand{\\myfirstauthorEN}                 % 请输入第一作者姓名\n"
                "{Department of Philosophy}\n")

_txt = handoff.render(stem="B", bib=_BIB, bibdb=_BIBDB, tex_text=_TEX, warnings=["w"],
                      bib_after={"total": 5, "must": 0, "nice": 1, "unstructured": 0})

check("① 旧口径首行保留",
      "条目总数：2（必补 1 / 建议 0 / 未结构化 0）" in _txt)
check("① 双时点：⑥ 生成期", "初排 ⑥ 生成时点值" in _txt)
check("① 双时点：⑦ 回填后", "⑦ 引用库回填之后" in _txt)
check("① 回填后值可见", "条目 5 条（必补 0 / 建议 1 / 未结构化 0）" in _txt)
check("① 披露「同一实现重算、非新判据」", "同一 bibcheck 实现重算、非新判据" in _txt)
check("① 回填口径沿用旧行", "回填 1 条 / 3 项" in _txt)
check("① 未命中点名", "引用库未命中" in _txt and "[misc] k_miss「Synthetic Missing Title」" in _txt)
check("① 回填冲突进交接单", "回填冲突（⑦ 覆盖库内已有值）：1 项" in _txt)
check("② 已写入但形态非法类", "已写入但形态非法" in _txt)
check("② titleEN 写成邮箱被点名", "mytitleEN" in _txt and "写成了邮箱地址" in _txt)
check("② authorEN 写成单位行被点名", "myfirstauthorEN" in _txt and "像单位行" in _txt)

# 两行式（模板实产形态）必须与行内式同判：V4 的根因就是只认行内式。
check("② 两行式 titleEN 含 @ 进形态非法类",
      handoff.shape_items(_TEX_TWOLINE) == [
          ("mytitleEN", "写成了邮箱地址", "someone@example.edu"),
          ("myfirstauthorEN", "像单位行", "Department of Philosophy")])
_txt_twoline = handoff.render(stem="B", bib=_BIB, bibdb={"done": False},
                              tex_text=_TEX_TWOLINE, warnings=[])
check("② 两行式进交接单②",
      "已写入但形态非法（2 项" in _txt_twoline
      and "mytitleEN：写成了邮箱地址「someone@example.edu」" in _txt_twoline)
check("② 注释掉的两行式定义不误报",
      handoff.shape_items("%\\newcommand{\\mytitleEN}\n{school@example.edu}\n") == [])

# 无 ⑦ 值 → 单时点口径（仍注明缺哪一侧，不假装有回填后值）
_single = handoff.render(stem="B", bib=_BIB, bibdb={"done": False}, warnings=[])
check("单时点：明说没有 ⑦ 重算值", "无 ⑦ 回填后重算值" in _single)
check("单时点：不出现回填后条目行", "⑦ 引用库回填之后" not in _single)

# 空结果：四节齐全、「无」≥3（既有测试约定的同款断言）
_empty = handoff.render(stem="Z")
check("空结果：四节齐全", all(t in _empty for t in handoff.SECTION_TITLES))
check("空结果：「无」≥3", _empty.count("无") >= 3, _empty.count("无"))

print()
print(f"PASS={PASS} FAIL={len(FAILS)}")
if FAILS:
    print("FAILED: " + "; ".join(FAILS))
sys.exit(len(FAILS))
