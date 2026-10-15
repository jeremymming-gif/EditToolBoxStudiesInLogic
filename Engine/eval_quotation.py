#!/usr/bin/env python3
"""引文环境转换功能的专项评测。

基准：07 Liangying_Zhao 人工成品的 quotation 块。
被测：本工具从原稿 docx 生成的 quotation 块。

报出 查全率 / 查准率 / 漏检原因 / 误检内容，并对每个匹配块做逐字对照。
"""

from __future__ import annotations

import difflib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HUMAN = "../../2026年第5期(90)/07 Liangying_Zhao/Liangying_Zhao.tex"
DOCX = ("../../2026年第5期(90)/07 Liangying_Zhao/original/"
        "经验贝叶斯主义的基本方法及其理论前景 修改版 缩减.docx")
MINE = "out/quote_eval/Liangying_Zhao/Liangying_Zhao.tex"

# 用法：
#   python3 convert.py --source '<Liangying_Zhao 原稿.docx>' --out out/quote_eval --compile
#   python3 eval_quotation.py


def strip_tex(s: str) -> str:
    """去掉 LaTeX 命令与标记，只留可见文字。"""
    s = re.sub(r"\\hfill\s*（[^）]*）", "", s)
    s = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}", "", s)
    s = re.sub(r"\\[A-Za-z]+\*?(\[[^\]]*\])?(\{[^{}]*\})?", "", s)
    s = re.sub(r"[{}$\\~]", "", s)
    return re.sub(r"\s+", "", s)


def blocks(path: str) -> list[str]:
    s = open(path, encoding="utf-8").read()
    return re.findall(r"\\begin\{quotation\}(.*?)\\end\{quotation\}", s, re.S)


def main() -> int:
    if not os.path.exists(MINE):
        print(f"缺少被测产物：{MINE}\n请先运行：")
        print("  python3 convert.py --source '<docx>' --out out/quote_eval --compile")
        return 1

    h_raw = blocks(HUMAN)
    m_raw = blocks(MINE)
    H = [(strip_tex(b), b) for b in h_raw]
    M = [(strip_tex(b), b) for b in m_raw]
    print(f"人工 quotation 块：{len(H)}    工具生成：{len(M)}\n")

    # 用内容相似度做双向匹配（0.62 是实测较合适的阈值）
    TH = 0.62
    matched_h: set[int] = set()
    matched_m: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for i, (ht, _) in enumerate(H):
        best, bj = 0.0, -1
        for j, (mt, _) in enumerate(M):
            if j in matched_m:
                continue
            r = difflib.SequenceMatcher(None, ht, mt).ratio()
            if r > best:
                best, bj = r, j
        if bj >= 0 and best >= TH:
            matched_h.add(i)
            matched_m.add(bj)
            pairs.append((i, bj, best))

    recall = len(matched_h) / len(H) if H else 0
    precision = len(matched_m) / len(M) if M else 0
    print("═" * 72)
    print(f"查全率（人工的块被我们抓到的比例）: {len(matched_h)}/{len(H)} = {recall * 100:.1f}%")
    print(f"查准率（我们生成的块确实该生成）  : {len(matched_m)}/{len(M)} = {precision * 100:.1f}%")
    print("═" * 72)

    print("\n★ 匹配上的块（逐个看内容是否一致）：")
    for i, j, r in pairs[:8]:
        print(f"\n   [人工 {i} ⇄ 工具 {j}]  相似度 {r * 100:.1f}%")
        print(f"     人工 {len(H[i][0]):4d}字：{H[i][0][:76]}…")
        print(f"     工具 {len(M[j][0]):4d}字：{M[j][0][:76]}…")

    missed = [i for i in range(len(H)) if i not in matched_h]
    print(f"\n★ 漏检 {len(missed)} 个（人工有、我们没有）——按字数排列：")
    for i in sorted(missed, key=lambda k: len(H[k][0]))[:12]:
        print(f"   [人工 {i}] {len(H[i][0]):4d}字  {H[i][0][:74]}…")

    extra = [j for j in range(len(M)) if j not in matched_m]
    if extra:
        print(f"\n★ 误检 {len(extra)} 个（我们生成了但人工没当成引文块）：")
        for j in extra[:8]:
            print(f"   [工具 {j}] {len(M[j][0]):4d}字  {M[j][0][:74]}…")

    # 漏检块的字数分布 vs 命中块
    hit_lens = sorted(len(H[i][0]) for i in matched_h)
    miss_lens = sorted(len(H[i][0]) for i in missed)
    print(f"\n★ 命中块字数：{hit_lens[:5]} … 中位 {hit_lens[len(hit_lens) // 2] if hit_lens else 0}")
    print(f"   漏检块字数：{miss_lens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
