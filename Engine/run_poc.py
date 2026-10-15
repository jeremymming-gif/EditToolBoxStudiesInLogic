#!/usr/bin/env python3
"""第 0 期 PoC 驱动：用两篇真实稿件跑端到端，并与人工成品比对打分。

用法：
    python3 run_poc.py                 # 两条通道都跑，并写出 out/REPORT.md
    python3 run_poc.py --only tex      # 只跑 LaTeX 通道

注意：Word 通道依赖 python-docx，需要用 DSH 自带的 Python 解释器运行。
LaTeX 通道用系统 python3 即可。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import score as S  # noqa: E402
from slcn.build import build_docx, build_latex  # noqa: E402
from slcn.templates import REPO_ROOT as ROOT, TPL_DIR as TPL  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "out")
if not os.path.isdir(TPL):
    raise SystemExit(
        f"找不到模板目录 {TPL}\n"
        "（本脚本假定引擎位于 <仓库>/Engine/；"
        "模板定位见 slcn/templates.py）"
    )

ARTICLES = {
    "tex": {
        "dir": os.path.join(ROOT, "2026年第5期(90)", "03 Liang_Zhang"),
        "basename": "Liang_Zhang",
        "label": "通道 A · LaTeX 源文件",
    },
    "docx": {
        "dir": os.path.join(ROOT, "2026年第5期(90)", "10 Bo_Chen"),
        "basename": "Bo_Chen",
        "docx": "陈波 当代逻辑哲学鸟瞰.docx",
        "label": "通道 B · Word 文稿",
    },
}

BODY_START = r"\printtitlepage"
BODY_END = r"\printbibliography"


def extract_region(tex: str, start: str, end: str) -> str:
    """取 [start, end) 之间的内容，用于对齐两侧的可比区域。"""
    i = tex.find(start)
    if i < 0:
        return tex
    j = tex.find(end, i + 1)
    return tex[i : j if j > 0 else len(tex)]


def compare(ref_path: str, gen_path: str) -> tuple[S.ScoreResult, S.ScoreResult]:
    ref = open(ref_path, encoding="utf-8").read()
    gen = open(gen_path, encoding="utf-8").read()
    full = S.score(ref, gen)
    body = S.score(
        extract_region(ref, BODY_START, BODY_END),
        extract_region(gen, BODY_START, BODY_END),
    )
    return full, body


def run_tex_channel(verbose: bool = True, sty: str | None = None, skeleton: str | None = None) -> dict:
    a = ARTICLES["tex"]
    print("=" * 78)
    print(f"【{a['label']}】{os.path.relpath(a['dir'], ROOT)}")
    print("=" * 78)
    res = build_latex(
        article_dir=a["dir"],
        out_dir=os.path.join(OUT, "03_Liang_Zhang"),
        basename=a["basename"],
        lang="zh",
        sty=sty,
        skeleton=skeleton,
        verbose=verbose,
    )
    full, body = compare(os.path.join(a["dir"], f"{a['basename']}.tex"), res.tex_path)
    print()
    print(S.report(full, "A-1 全文（含模板骨架与元数据）", top=0))
    print()
    print(S.report(body, "A-2 正文（剔除骨架，只看内容）", top=10))
    return {"build": res, "full": full, "body": body, "key": "tex", "label": a["label"]}


def run_docx_channel(verbose: bool = True, sty: str | None = None, skeleton: str | None = None) -> dict:
    a = ARTICLES["docx"]
    print()
    print("=" * 78)
    print(f"【{a['label']}】{os.path.relpath(a['dir'], ROOT)}")
    print("=" * 78)
    res = build_docx(
        docx_path=os.path.join(a["dir"], "original", a["docx"]),
        out_dir=os.path.join(OUT, "10_Bo_Chen"),
        basename=a["basename"],
        lang="zh",
        sty=sty,
        skeleton=skeleton,
        verbose=verbose,
    )
    full, body = compare(os.path.join(a["dir"], f"{a['basename']}.tex"), res.tex_path)
    print()
    print(S.report(full, "B-1 全文（含模板骨架与元数据）", top=0))
    print()
    print(S.report(body, "B-2 正文（剔除骨架，只看内容）", top=10))
    return {"build": res, "full": full, "body": body, "key": "docx", "label": a["label"]}


def write_report(results: list[dict]) -> str:
    L: list[str] = []
    L.append("# SLCN 稿件转换器 · 第 0 期 PoC 结果")
    L.append("")
    L.append("> 本报告由 `Engine/run_poc.py` 自动生成。所有数字均可复现。")
    L.append("> 基准是**编辑部的人工成品**，不是理想值——人工成品本身就含编辑判断。")
    L.append("")
    L.append("## 一、总览")
    L.append("")
    L.append("| 通道 | 正文裸相似度 | 内容准确率(L3) | 表层差异(L2) | 实质差异(L3) |")
    L.append("|---|---|---|---|---|")
    for r in results:
        b = r["body"]
        L.append(
            f"| {r['label']} | **{b.raw_ratio * 100:.2f}%** | {b.char_accuracy * 100:.2f}% "
            f"| {b.l2_blocks} 块 | {b.l3_blocks} 块 |"
        )
    L.append("")
    L.append("**指标口径**")
    L.append("")
    L.append("- **正文裸相似度**：不做任何归一化的字符级相似度，端到端总指标。规则修标点也能加分。")
    L.append("- **内容准确率(L3)**：只惩罚实词增删改，不奖励标点修复。衡量「还需要编辑动多少脑子」。")
    L.append("- **L2/L3 分档**：抹掉标点、空白与排版宏后若两侧相同，则判为 L2（表层）。")
    L.append("")
    L.append("## 二、逐通道明细")
    for r in results:
        b = r["body"]
        L.append("")
        L.append(f"### {r['label']}")
        L.append("")
        L.append(f"- 正文裸相似度 **{b.raw_ratio * 100:.2f}%**，内容准确率 {b.char_accuracy * 100:.2f}%")
        L.append(f"- 差异块 {b.total_blocks} 个：L2 表层 {b.l2_blocks} 个 / L3 实质 {b.l3_blocks} 个")
        br = r["build"]
        if br.rule_hits:
            L.append("- 规则命中：" + "、".join(f"`{k}`×{v}" for k, v in sorted(br.rule_hits.items(), key=lambda x: -x[1])))
        if br.warnings:
            L.append("- ⚠️ 注意事项：")
            for w in br.warnings:
                L.append(f"  - {w}")
    L.append("")
    L.append("## 三、L3 实质差异 TOP（需编辑判断的部分）")
    for r in results:
        L.append("")
        L.append(f"### {r['label']}")
        L.append("")
        l3 = [x for x in r["body"].blocks if x.level == "L3"]
        for n, blk in enumerate(l3[:18], 1):
            lo = blk.left[:110].replace("\n", "⏎")
            ro = blk.right[:110].replace("\n", "⏎")
            L.append(f"{n}. 人工 `{lo}`")
            L.append(f"   工具 `{ro}`")
    path = os.path.join(OUT, "REPORT.md")
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["tex", "docx"], default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--sty", default=None,
                    help="指定版面样式文件（.sty）。默认：中文 SLCN-MIX.sty，英文 SLEN.sty")
    ap.add_argument("--skeleton", default=None,
                    help="指定骨架模板（.tex）。默认：中文 CN_template.tex，英文 EN_template.tex")
    ap.add_argument("--list-templates", action="store_true", help="列出可用模板后退出")
    args = ap.parse_args()

    if args.list_templates:
        from slcn import templates as T

        print("可用模板：")
        for lang, t in T.PRESETS.items():
            print(f"\n  【{'中文' if lang == 'zh' else '英文'}稿件】默认")
            print(f"    版面样式  {t.sty}")
            print(f"    骨架      {t.skeleton}")
        print(f"\n  模板目录下可覆盖的 .sty：{'、'.join(T.AVAILABLE_STY)}")
        return 0

    os.makedirs(OUT, exist_ok=True)
    results = []
    if args.only in (None, "tex"):
        results.append(run_tex_channel(verbose=not args.quiet, sty=args.sty, skeleton=args.skeleton))
    if args.only in (None, "docx"):
        try:
            results.append(run_docx_channel(verbose=not args.quiet, sty=args.sty, skeleton=args.skeleton))
        except ImportError:
            print()
            print("⚠ Word 通道需要 python-docx。请改用 DSH 自带解释器运行：")
            import sys as _sys
            print(f"   {_sys.executable} run_poc.py")   # 用当前解释器，不写死某台机器的路径

    if len(results) == 2:
        print()
        print("=" * 78)
        print("汇总")
        print("=" * 78)
        for r in results:
            print(f"  {r['label']:22s} 正文裸相似度 {r['body'].raw_ratio * 100:6.2f}%   "
                  f"内容准确率 {r['body'].char_accuracy * 100:6.2f}%")
        print()
        print(f"  报告已写出：{os.path.relpath(write_report(results))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
