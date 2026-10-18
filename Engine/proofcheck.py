#!/usr/bin/env python3
"""一校核查：按《排校手册》与《流程化经验书》的规范检查初排稿（第 92 条）。

    python3 proofcheck.py --tex "<初排稿.tex>" [--lang zh|en] [--out <报告.md>] [--fix] [--version]

**用在哪一步**：初排稿经过编辑**第一次人工校对**之后。这时正文里会出现只有人手才会引入的
排版问题（脚注跑到逗号后面、引文括号位置、半角标点混进中文、顿号后紧跟引号……）。
本工具把那批规范逐条检出来，附「第几行 / 原文 / 建议 / 依据」，供编辑逐条处理。

**默认只报不改**（与 `checkbib.py` 同一口径）：经验书原话是「并非修改所有匹配项，稍作观察」。
加 `--fix` 才会把**无歧义**的那一小撮写到一个新文件（`<stem>_一校核查后.tex`），
输入文件一个字节都不动。

退出码：`0` 没有命中；`1` 有命中（正常情况，不是错误）；`2` 用法/文件错误。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import platform as _plat  # noqa: E402
from slcn import fsutil as _fsutil  # noqa: E402
from slcn import proofcheck as _pc  # noqa: E402


def _guess_lang(tex: str, tex_path: str, given: str) -> str:
    """判语种：优先用引擎自己的 `convert.detect_lang`（看模板字段 mytitleCN/mytitleEN）。

    ⚠️ 不要只看汉字比例：英文稿也带中文题名与摘要（实测 2024_03_Xiaoyang_Wang），
    按比例会被判成中文稿，于是中文规则去管英文正文 —— 那正是 2026-10-15 第一版的问题。
    """
    if given in ("zh", "en"):
        return given
    try:
        import convert  # noqa: PLC0415
        lang, _why = convert.detect_lang(tex_path)
        if lang in ("zh", "en"):
            return lang
    except Exception:  # noqa: BLE001  引擎起不来时退到启发式，别让核查整个失败
        pass
    cjk = sum(1 for c in tex if "\u4e00" <= c <= "\u9fff")
    return "zh" if cjk > 200 else "en"


def _report_md(stem: str, tex_path: str, rep: _pc.Report, fixed: list[_pc.Hit],
               moves: list[str], fixed_path: str | None) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    by = rep.by_rule()
    out: list[str] = [
        f"# 一校核查报告：{stem}",
        "",
        f"> 源文件：`{tex_path}`　｜　生成：{now}　｜　语种判据：`{rep.lang}`　｜　"
        f"规则数：{len(_pc.RULES)}",
        ">",
        "> **默认只报不改**（《流程化经验书》原话：「并非修改所有匹配项，稍作观察」）。",
        "> 每条都注明**依据**，便于回查《排校手册》原文。",
        "",
        "## 摘要",
        "",
        "| 规则 | 条数 | 说明 | 依据 |",
        "|---|---|---|---|",
    ]
    order = [r.id for r in _pc.RULES]
    for rid in order:
        hits = by.get(rid)
        if not hits:
            continue
        out.append(f"| `{rid}` | **{len(hits)}** | {hits[0].title} | {hits[0].basis} |")
    if not by:
        out.append("| — | 0 | 没有命中任何规则 | — |")
    out += ["", f"**合计命中 {rep.n} 处。**", ""]

    if fixed or moves:
        out += ["## 已自动改的部分（`--fix`）", ""]
        if fixed:
            out.append("| 规则 | 行 | 原文 | 改成 |")
            out.append("|---|---|---|---|")
            for h in fixed:
                out.append(f"| `{h.rule_id}` | {h.line} | `{h.text}` | `{h.replacement}` |")
        if moves:
            out.append("")
            out.append("位置调整（脚注/引文移到标点之前）：")
            for m in moves:
                out.append(f"- {m}")
        out.append("")
        if fixed_path:
            out.append(f"改后的文件：`{fixed_path}`（输入文件未改动）")
        out.append("")

    out += ["## 逐条", ""]
    for rid in order:
        hits = by.get(rid)
        if not hits:
            continue
        head = hits[0]
        # 混合规则（如 ZH-08：只有「汉字-汉字」窄子集可自动改，其余只报）要标出可改条数，
        # 不能因为 hits[0] 不可改就把整节标成「只报」、也不能反过来让人以为全都能改。
        n_fix = sum(1 for h in hits if h.fixable)
        if n_fix == len(hits):
            flag = "（可自动改）"
        elif n_fix:
            flag = f"（可自动改 {n_fix}/{len(hits)} 处，其余需人工判断）"
        else:
            flag = ""
        out += [f"### `{rid}` {head.title}{flag} —— {len(hits)} 处", "",
                f"依据：{head.basis}", "",
                f"建议：{head.advice}", "",
                "| 行 | 列 | 原文 |", "|---|---|---|"]
        for h in hits[:200]:
            txt = h.text if len(h.text) <= 60 else h.text[:57] + "…"
            out.append(f"| {h.line} | {h.col} | `{txt}` |")
        if len(hits) > 200:
            out.append(f"| … | | 另有 {len(hits) - 200} 处未列出 |")
        out.append("")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="proofcheck.py",
        description="一校核查：按本刊《排校手册》《流程化经验书》的规范检查初排稿（默认只报不改）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--tex", required=True, metavar="初排稿.tex", help="【必填】要核查的 .tex")
    ap.add_argument("--lang", default="", choices=["", "zh", "en"], help="语种（默认按汉字比例自动判）")
    ap.add_argument("--out", default=None, metavar="报告.md",
                    help="报告写到这个文件（默认 <同目录>/<文件名>_一校核查.md）")
    ap.add_argument("--fix", action="store_true",
                    help="把**可自动改**的规则写到一个新文件（输入文件不动）")
    args = ap.parse_args(argv)

    tex_path = os.path.abspath(os.path.expanduser(args.tex))
    if not os.path.isfile(tex_path):
        print(f"\n❌ 找不到文件：{tex_path}\n", file=sys.stderr)
        return 2
    try:
        tex = open(tex_path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print(f"\n❌ 读不了文件：{e}\n", file=sys.stderr)
        return 2

    lang = _guess_lang(tex, tex_path, args.lang)
    stem = os.path.splitext(os.path.basename(tex_path))[0]
    out_dir = os.path.dirname(tex_path)

    print(f"一校核查：{stem}（语种 {lang}）\n")
    rep = _pc.check(tex, lang=lang)
    by = rep.by_rule()
    if not by:
        print("  ✅ 没有命中任何规则。")
    for rid, hits in by.items():
        n_fix = sum(1 for h in hits if h.fixable)
        if n_fix == len(hits):
            flag = "（可自动改）"
        elif n_fix:
            flag = f"（可自动改 {n_fix}/{len(hits)} 处）"
        else:
            flag = ""
        print(f"  {rid}  {len(hits):3d} 处  {hits[0].title}{flag}")
        for h in hits[:3]:
            print(f"        第 {h.line} 行：{h.text[:52]}")
        if len(hits) > 3:
            print(f"        …… 另有 {len(hits) - 3} 处")

    fixed: list[_pc.Hit] = []
    fixed_path = None
    moves: list[str] = []
    if args.fix:
        new_tex, fixed = _pc.apply_fixes(tex, rep)
        new_tex, moves = _pc.move_marks_before_punct(
            new_tex, kinds=tuple(k for k in ("footnote", "cite")
                                 if any(h.rule_id in ("ZH-10", "ZH-11", "ZH-12") for h in rep.hits)))
        fixed_path = os.path.join(out_dir, f"{stem}_一校核查后.tex")
        # 原子写 + 旧版备份（UI 方案第 4 件）：这份是"改后稿"，可能被反复生成
        _fsutil.atomic_write(fixed_path, new_tex, newline="", log=print)
        print(f"\n  已写出改后的文件：{fixed_path}"
              f"（自动改 {len(fixed)} 处 + 位置调整 {len(moves)} 处；输入文件未动）")

    out_path = os.path.abspath(os.path.expanduser(args.out)) if args.out \
        else os.path.join(out_dir, f"{stem}_一校核查.md")
    _fsutil.atomic_write(out_path,
                         _report_md(stem, tex_path, rep, fixed, moves, fixed_path),
                         newline="", log=print)

    print()
    # 契约行（`docs/ENGINE_API.md`）：外壳靠它拿命中数与报告路径
    print(f"完成。一校核查：命中 {rep.n} 处（可自动改 {sum(1 for h in rep.hits if h.fixable)} 处）"
          f"（报告：{out_path}）")
    return 1 if rep.n else 0


if __name__ == "__main__":
    raise SystemExit(main())
