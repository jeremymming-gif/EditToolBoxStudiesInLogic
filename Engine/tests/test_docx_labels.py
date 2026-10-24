#!/usr/bin/env python3.14
"""P2-0：图 `\\label{fig:N}` 必须**文档级唯一**（期 87 P2-2 前置缺陷）。

缺陷
-----
`render_para()` 里 `fig_seq = 0` 是**函数内**计数器（每个段落从 1 重来），而 `parse_docx()`
对每个段落各调一次 `render_para()` → 多段有图时每段的图都叫 `\\label{fig:1}`。
实测 corpus 12 篇有 fig label 的产物里 **6 篇 label 重复**（`2025_04_YingJin_Xu` 6 个
全是 `fig:1`）；hyperref 会报 "destination with the same identifier"，`\\ref` 会指错图。

修法
----
`render_para(..., fig_counter=[0])`：由 `parse_docx()` 在整篇持有同一个计数器并传入；
不传时保留段落内计数（旧调用方/单测行为逐字节不变）。

跑法
----
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_docx_labels.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.docxsource import render_para  # noqa: E402
from slcn.wordcaption import caption_candidate, find_captions  # noqa: E402
from slcn.docxxml import Para, Seg  # noqa: E402

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


IMG = {"rId1": "a.png", "rId2": "b.png"}


def _para(*segs):
    return Para(segs=list(segs))


# ══════════════════════════════════════════════════════════════════════════
# ① 两段各一张图 + 共享文档级计数器 → fig:1 / fig:2（旧实现两段都是 fig:1）
# ══════════════════════════════════════════════════════════════════════════
counter = [0]
tex1, _ = render_para(_para(Seg("image", rid="rId1")), {}, [], {}, img_names=IMG,
                      fig_counter=counter)
tex2, _ = render_para(_para(Seg("image", rid="rId2")), {}, [], {}, img_names=IMG,
                      fig_counter=counter)
check("① 第 1 段图 = fig:1", "\\label{fig:1}" in tex1, tex1[:120])
check("① 第 2 段图 = fig:2（文档级计数；旧实现会又是 fig:1）",
      "\\label{fig:2}" in tex2 and "\\label{fig:1}" not in tex2, tex2[:120])

# ══════════════════════════════════════════════════════════════════════════
# ② 不传计数器（旧调用方/单测）→ 保留段落内计数，行为与从前逐字节相同
# ══════════════════════════════════════════════════════════════════════════
legacy1, _ = render_para(_para(Seg("image", rid="rId1")), {}, [], {}, img_names=IMG)
legacy2, _ = render_para(_para(Seg("image", rid="rId2")), {}, [], {}, img_names=IMG)
check("② 兼容：不传 fig_counter 时两段都从 fig:1 起（旧行为不变）",
      "\\label{fig:1}" in legacy1 and "\\label{fig:1}" in legacy2,
      f"{legacy1[:80]!r} / {legacy2[:80]!r}")

# ══════════════════════════════════════════════════════════════════════════
# ③ 同一段里两张图：保持既有「合并成一个 figure」语义（只推进 1 次计数）
# ══════════════════════════════════════════════════════════════════════════
counter = [0]
tex3, _ = render_para(_para(Seg("image", rid="rId1"), Seg("text", "中间文字"),
                            Seg("image", rid="rId2")), {}, [], {}, img_names=IMG,
                      fig_counter=counter)
check("③ 同段两张图合并为一个 figure（fig:1），计数器只推进到 1（既有合并语义不变）",
      tex3.count("\\label{fig:") == 1 and "\\label{fig:1}" in tex3
      and tex3.count("\\includegraphics") == 2 and counter == [1],
      f"{tex3[:200]} counter={counter}")

# ══════════════════════════════════════════════════════════════════════════
# ④ 真实稿抽查：有 fig label 的 Word 稿，产物 label 必须唯一
#      （本机没有稿件库时 SKIP —— 公开仓/CI 行为一致）
# ══════════════════════════════════════════════════════════════════════════
def _real_check() -> None:
    import csv
    import tempfile

    from slcn import build, paths, sources as S

    root = paths.manuscripts_root()
    csv_path = os.path.join(_ENGINE, "out", "corpus", "corpus.csv")
    if not root or not os.path.isfile(csv_path):
        skip("④ 真实稿 fig label 唯一性抽查", "本机没有稿件库或 out/corpus/corpus.csv")
        return
    # 取 corpus 里「现有产物就有重复 fig label」的篇做前后对照最有说服力；
    # corpus.csv 不在时退回按名字找。
    cands = []
    rows = [r for r in csv.DictReader(open(csv_path, encoding="utf-8"))
            if r.get("channel") == "Word"]
    for r in rows:
        art = os.path.join(root, r["issue"], r["slug"])
        if os.path.isdir(art):
            cands.append((r["issue"], r["slug"]))
    picked = 0
    for issue, slug in cands:
        if picked >= 3:
            break
        if slug not in ("04 YingJin_Xu", "01 Yanjin_Chen", "06 Zhixi_Chen"):
            continue
        art = os.path.join(root, issue, slug)
        ss = S.identify(art, verbose=False)
        if not ss.docx:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            res = build.build_docx(ss.docx, tmp, basename="Case", verbose=False)
            tex = open(res.tex_path, encoding="utf-8").read()
        labels = re.findall(r"\\label\{fig:(\d+)\}", tex)
        if labels:
            picked += 1
            check(f"④ 真实稿 {slug}：fig label 唯一（{len(labels)} 个）",
                  len(labels) == len(set(labels)), str(labels))
    if picked == 0:
        skip("④ 真实稿 fig label 唯一性抽查", "本机没找到带图 Word 稿")


try:
    _real_check()
except Exception as _e:  # noqa: BLE001
    skip("④ 真实稿 fig label 唯一性抽查", f"{type(_e).__name__}: {_e}")


# ══════════════════════════════════════════════════════════════════════════
# ⑤ B-217③：Word 题注 → \caption（唯一相邻关联；关联失败保持原行为 + warning）
# ══════════════════════════════════════════════════════════════════════════
print("\n── ⑤ B-217③ 题注关联与 \\caption 接线 ──")


def _cap(text, style=""):
    return Para(segs=[Seg("text", text=text)], style=style)


_c = caption_candidate(_cap("图1 系统架构", "Caption"))
check("⑤ 判据：Caption 样式「图1 系统架构」→ 图题、去掉编号前缀",
      bool(_c) and _c["kind"] == "fig" and _c["text"] == "系统架构", repr(_c))
_c = caption_candidate(_cap("表2 实验数据"))
check("⑤ 判据：无样式但「表2 + 说明」的短段落也认",
      bool(_c) and _c["kind"] == "tab" and _c["text"] == "实验数据", repr(_c))
check("⑤ 反例：无样式正文叙述「图1 展示了系统架构。」不认（句末标点）",
      caption_candidate(_cap("图1 展示了系统架构。")) is None)
check("⑤ 反例：普通正文段落不认", caption_candidate(_cap("本文讨论逻辑")) is None)

_items = [
    {"caption": None, "figure": True, "table": False, "blank": False},
    {"caption": caption_candidate(_cap("图1 系统架构")), "figure": False,
     "table": False, "blank": False},
    {"caption": None, "figure": False, "table": False, "blank": True},
    {"caption": None, "figure": False, "table": False, "blank": False},  # 正文段
    {"caption": None, "figure": False, "table": True, "blank": False},
    {"caption": caption_candidate(_cap("表1 实验数据")), "figure": False,
     "table": False, "blank": False},
]
_assoc, _skip, _warns = find_captions(_items)
check("⑤ 唯一相邻：图/表各并入题注、题注段落要跳过、无 warning",
      _assoc.get(0) == {"kind": "fig", "text": "系统架构"}
      and _assoc.get(4) == {"kind": "tab", "text": "实验数据"}
      and _skip == {1, 5} and _warns == [], repr((_assoc, _skip, _warns)))
_amb = [
    {"caption": None, "figure": True, "table": False, "blank": False},
    {"caption": caption_candidate(_cap("图1 甲")), "figure": False,
     "table": False, "blank": False},
    {"caption": None, "figure": True, "table": False, "blank": False},
]
_a2, _s2, _w2 = find_captions(_amb)
check("⑤ 反例：题注夹在两个图之间 → 不关联 + warning（不猜）",
      _a2 == {} and _s2 == set() and bool(_w2), repr((_a2, _s2, _w2)))
_mis = [
    {"caption": None, "figure": False, "table": True, "blank": False},
    {"caption": caption_candidate(_cap("图1 甲")), "figure": False,
     "table": False, "blank": False},
]
_a3, _s3, _w3 = find_captions(_mis)
check("⑤ 反例：写「图1」却挨着表格 → 不关联 + warning（不猜）",
      _a3 == {} and _s3 == set() and bool(_w3), repr((_a3, _s3, _w3)))

_texc, _ = render_para(_para(Seg("image", rid="rId1")), {}, [], {}, img_names=IMG,
                       fig_caption="系统架构")
check("⑤ 接线：render_para(fig_caption=…) → figure 用真实题注、无占位",
      "\\caption{系统架构}" in _texc and "【需补充图题】" not in _texc, _texc[:160])
_texd, _ = render_para(_para(Seg("image", rid="rId1")), {}, [], {}, img_names=IMG)
check("⑤ 反例：不传 fig_caption → 占位与从前逐字节相同",
      "\\caption{【需补充图题】}" in _texd, _texd[:160])

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
