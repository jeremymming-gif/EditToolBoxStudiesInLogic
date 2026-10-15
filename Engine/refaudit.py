#!/usr/bin/env python3
"""全库参考文献质量普查。

跑一遍语料库，对每篇**工具生成的** bib 做**离线**核对：

  ① 字段完整性：按类型列出必补字段（README 第 21 条的必备字段表）
  ② 引用双向核对：悬空引用 / 冗余条目 / 疑似漏引

产出总体数字 + 逐篇明细，用于量化「现有稿件里有多少参考文献信息对不上」。

⚠️ 2026-10-14：**CrossRef 联网核实已按编辑部要求移除**（中文覆盖差 + 跨类型错配）。
联网核实改由 `<作者名>_文献核实.md` 任务书交给编辑各自的 AI，见 `slcn/refmark.py`。
"""

from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import bibcheck  # noqa: E402

CORPUS = "out/corpus"
OUT = "out/refaudit.txt"


def main() -> int:
    dirs = sorted(d for d in os.listdir(CORPUS) if os.path.isdir(os.path.join(CORPUS, d)))
    articles = []
    for d in dirs:
        p = os.path.join(CORPUS, d)
        tex = [f for f in os.listdir(p) if f.endswith(".tex")]
        bib = [f for f in os.listdir(p) if f.endswith(".bib")]
        if tex and bib:
            articles.append((d, os.path.join(p, tex[0]), os.path.join(p, bib[0])))

    print(f"共 {len(articles)} 篇有产物的稿件\n", flush=True)

    cite_stat = Counter()
    ref_stat = Counter()
    field_missing = Counter()
    lines: list[str] = []

    for n, (name, texp, bibp) in enumerate(articles, 1):
        tex = open(texp, encoding="utf-8", errors="replace").read()
        bib = open(bibp, encoding="utf-8", errors="replace").read()

        # ① 离线核对
        r = bibcheck.check(bib)
        cites = bibcheck.check_citations(tex, bib)
        for c in cites:
            cite_stat[c[0]] += 1
        ref_stat["条目"] += r.total
        ref_stat["必补字段"] += r.n_must
        for i in r.issues:
            if i.severity == "must":
                field_missing[i.detail] += 1

        print(f"  [{n:2d}/{len(articles)}] {name[:34]:36s} 条目 {r.total:3d} "
              f"必补 {r.n_must:3d} 引用问题 {len(cites):2d}", flush=True)

    lines.append("══ 全库参考文献质量普查 ══")
    lines.append("")
    lines.append(f"稿件数：{len(articles)}｜条目总数：{ref_stat['条目']}")
    lines.append("")
    lines.append("── ① 字段完整性（离线核对）──")
    lines.append(f"  必补字段合计：{ref_stat['必补字段']} 项")
    for k, v in field_missing.most_common(12):
        lines.append(f"    {k:12s} ×{v}")
    lines.append("")
    lines.append("── ② 引用双向核对 ──")
    for k, v in cite_stat.most_common():
        lines.append(f"    {k} ×{v}")
    lines.append("")
    lines.append("（联网核实已移除：改由每篇的「_文献核实.md」任务书交给编辑各自的 AI）")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n报告：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
