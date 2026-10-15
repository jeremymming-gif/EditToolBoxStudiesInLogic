#!/usr/bin/env python3
"""全库回归：把打分器跑到所有「有 original/ 且有同名目标成品」的稿件上。

为什么要做这件事
----------------
第 0 期只用 03 Liang_Zhang / 10 Bo_Chen 两篇验证，那是 2 个数据点，
不足以支撑「自动化率」这种量化结论。本库有 86 篇可做基准比对的稿件，
跑一遍才能得到真实分布与失败模式。

只做「装配 + 打分」，不编译（编译慢且本阶段不需要）。

用法：
    PYTHONPATH="$PWD/vendor" python3 corpus.py
"""

from __future__ import annotations

import os
import re
import statistics
import sys
import traceback
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _ensure_vendor() -> None:
    """把项目本地的 vendor/ 挂到 sys.path —— **只在依赖确实缺失时**。

    ⚠️ 与 convert.py 里同名函数同样的理由：vendor 里的 lxml 带**编译扩展**，为某个
    特定 Python 版本而装；无条件插到最前面会让另一个版本加载到不兼容的二进制
    （`ImportError: cannot import name 'etree'`）。

    ⚠️ 这里必须补这一步：convert.py 会自己调它，**corpus.py 原来没有**，于是用
    `/opt/homebrew/bin/python3`（3.14，本身没装 lxml）跑全库回归时，
    `sources.pick_main_docx` 打不开任何 docx → **48 篇 Word 全判「无可用源」**，
    成功数从 69/90 掉到 21/90 —— 看着像引擎大回归，其实是解释器问题
    （explicit-fail 实测复现，2026-10-14）。
    """
    import importlib.util

    if importlib.util.find_spec("lxml") is not None:
        return
    v = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if os.path.isdir(v) and v not in sys.path:
        sys.path.append(v)


_ensure_vendor()

from run_poc import BODY_END, BODY_START, ROOT, TPL, extract_region  # noqa: E402
from slcn import score as S  # noqa: E402
from slcn.build import build_docx, build_latex  # noqa: E402
from slcn.latex import expand_inputs, parse_macros  # noqa: E402
from slcn.sources import identify  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "corpus")

# 可由 --sty / --skeleton 覆盖；None 表示按语种取默认（中文 SLCN-MIX，英文 SLEN）
STY: str | None = None
SKELETON: str | None = None


@dataclass
class Row:
    issue: str
    slug: str
    channel: str
    lang: str
    ok: bool
    raw: float = 0.0
    content: float = 0.0
    l2: int = 0
    l3: int = 0
    ref_chars: int = 0
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    # 源稿里的表格/图片数量 与 产物里转换出的数量
    src_tbl: int = 0
    out_tbl: int = 0
    src_img: int = 0
    out_img: int = 0
    src_shape: int = 0      # 源稿里的矢量形状数（Word 绘图工具所画）
    out_shape: int = 0      # 产物里的截图占位数


def pick_lang(target_path: str) -> str:
    """按目标成品判断语种——EN 模板有 mytitleCN，CN 模板有 mytitleEN。

    只返回语种；具体用哪套 sty/骨架交给 slcn.templates 按默认规则解析
    （中文 SLCN-MIX.sty，英文 SLEN.sty）。
    """
    try:
        names = set(parse_macros(open(target_path, encoding="utf-8", errors="replace").read()))
        if "mytitleCN" in names:
            return "en"
    except OSError:
        pass
    return "zh"


def target_basename(article_name: str) -> str:
    """'03 Liang_Zhang' → 'Liang_Zhang'"""
    parts = article_name.split()
    return parts[1] if len(parts) > 1 else article_name


def collect() -> list[tuple[str, str, str]]:
    out = []
    for it in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, it)
        if not re.match(r"20\d\d年第", it) or not os.path.isdir(d):
            continue
        for a in sorted(os.listdir(d)):
            p = os.path.join(d, a)
            if not os.path.isdir(p) or a.startswith(("000", ".")):
                continue
            if not os.path.isdir(os.path.join(p, "original")):
                continue
            base = target_basename(a)
            tgt = os.path.join(p, f"{base}.tex")
            if os.path.exists(tgt):
                out.append((it, a, tgt))
    return out


def run_one(issue: str, slug: str, target: str) -> Row:
    art_dir = os.path.dirname(target)
    base = target_basename(slug)
    lang = pick_lang(target)
    slug_dir = f"{issue[:4]}_{slug.replace(' ', '_')}"
    out_dir = os.path.join(OUT, slug_dir)

    ss = identify(art_dir, verbose=False)
    if ss.metadata or ss.body:
        channel = "LaTeX"
        # 回归器自己做源发现，然后把**显式路径**交给引擎
        # （引擎本身不做自动发现——那是工具的要求，不是测试台的要求）
        if not ss.body:
            return Row(issue, slug, "无可用源", lang, False, error="original/ 里没有正文源")
        res = build_latex(
            body_path=ss.body.path, out_dir=out_dir,
            metadata_path=ss.metadata.path if ss.metadata else None,
            bib_path=ss.bib, basename=base,
            lang=lang, sty=STY, skeleton=SKELETON, verbose=False,
        )
    elif ss.docx:
        channel = "Word"
        res = build_docx(
            docx_path=ss.docx, out_dir=out_dir, basename=base,
            lang=lang, sty=STY, skeleton=SKELETON, verbose=False,
        )
    else:
        return Row(issue, slug, "无可用源", lang, False, error="original/ 里既无模板稿也无 docx")

    # 基准必须递归展开 \input —— 否则把正文拆出去的那 10 篇会假性跌到 6%
    ref = expand_inputs(target)
    gen = open(res.tex_path, encoding="utf-8").read()

    # 统计表格/图片的转换情况
    import zipfile

    src_tbl = src_img = src_shape = 0
    src_path = ss.docx or ""
    if src_path and src_path.lower().endswith(".docx"):
        try:
            from lxml import etree

            z = zipfile.ZipFile(src_path)
            xml = z.read("word/document.xml").decode("utf-8", "replace")
            src_tbl = xml.count("<w:tbl>")
            A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
            R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
            W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            root = etree.fromstring(z.read("word/document.xml"))
            for d in root.iter(W + "drawing"):
                b = d.find(".//" + A + "blip")
                if b is not None and b.get(R + "embed"):
                    src_img += 1
                elif b is None:
                    src_shape += 1
        except Exception:  # noqa: BLE001
            pass
    # 产物里只数未被注释的行
    active = [l for l in gen.split("\n") if not l.lstrip().startswith("%")]
    gen_a = "\n".join(active)
    out_tbl = gen_a.count("\\begin{table}")
    out_img = gen_a.count("\\includegraphics")
    out_shape = gen_a.count("需人工截图：")
    sc = S.score(
        extract_region(ref, BODY_START, BODY_END),
        extract_region(gen, BODY_START, BODY_END),
    )
    return Row(
        issue, slug, channel, lang, True,
        raw=sc.raw_ratio, content=sc.char_accuracy,
        l2=sc.l2_blocks, l3=sc.l3_blocks, ref_chars=sc.ref_chars,
        warnings=res.warnings,
        src_tbl=src_tbl, out_tbl=out_tbl, src_img=src_img, out_img=out_img,
        src_shape=src_shape, out_shape=out_shape,
    )


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * p))))
    return xs[k]


def main() -> int:
    global STY, SKELETON
    import argparse

    ap = argparse.ArgumentParser(description="全库回归")
    ap.add_argument("--sty", default=None, help="覆盖版面样式文件（默认按语种自动选）")
    ap.add_argument("--skeleton", default=None, help="覆盖骨架模板（默认按语种自动选）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 篇（调试用）")
    args = ap.parse_args()
    STY, SKELETON = args.sty, args.skeleton

    items = collect()
    if args.limit:
        items = items[: args.limit]
    os.makedirs(OUT, exist_ok=True)
    print(f"共 {len(items)} 篇有同名目标成品，开始回归…\n")

    rows: list[Row] = []
    for n, (issue, slug, target) in enumerate(items, 1):
        try:
            r = run_one(issue, slug, target)
        except Exception as e:  # noqa: BLE001
            r = Row(issue, slug, "异常", "?", False, error=f"{type(e).__name__}: {e}")
            if os.environ.get("TRACE"):
                traceback.print_exc()
        rows.append(r)
        mark = "✅" if r.ok else "❌"
        extra = f"{r.raw * 100:6.2f}%" if r.ok else r.error[:46]
        print(f"  [{n:2d}/{len(items)}] {mark} {slug[:24]:26s} {r.channel:6s} {extra}")

    ok = [r for r in rows if r.ok]
    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"  成功 {len(ok)} 篇 / 失败 {len(rows) - len(ok)} 篇")

    for ch in ("LaTeX", "Word"):
        sub = [r for r in ok if r.channel == ch]
        if not sub:
            continue
        raws = [r.raw for r in sub]
        cons = [r.content for r in sub]
        print(f"\n  【{ch} 通道】{len(sub)} 篇")
        print(f"    正文裸相似度  中位数 {statistics.median(raws) * 100:6.2f}%   "
              f"均值 {statistics.mean(raws) * 100:6.2f}%   "
              f"p10 {pct(raws, .1) * 100:6.2f}%   p90 {pct(raws, .9) * 100:6.2f}%")
        print(f"    内容准确率    中位数 {statistics.median(cons) * 100:6.2f}%   "
              f"均值 {statistics.mean(cons) * 100:6.2f}%   "
              f"p10 {pct(cons, .1) * 100:6.2f}%   p90 {pct(cons, .9) * 100:6.2f}%")
        print(f"    ≥95% 的篇数：{sum(1 for x in raws if x >= .95)}/{len(sub)}"
              f"    ≥90%：{sum(1 for x in raws if x >= .90)}/{len(sub)}"
              f"    <80%：{sum(1 for x in raws if x < .80)}/{len(sub)}")

    print("\n  【表格 / 图片 转换覆盖】")
    for label, sk, ok_ in (("表格", "src_tbl", "out_tbl"), ("图片", "src_img", "out_img")):
        have = [r for r in ok if getattr(r, sk) > 0]
        tot_src = sum(getattr(r, sk) for r in ok)
        tot_out = sum(getattr(r, ok_) for r in ok)
        full = sum(1 for r in have if getattr(r, ok_) >= getattr(r, sk))
        print(f"    {label}：{len(have)} 篇含{label}｜源 {tot_src} 个 → 产出 {tot_out} 个"
              f"｜完全转换 {full}/{len(have)} 篇")
        if have:
            worst = [r for r in have if getattr(r, ok_) < getattr(r, sk)][:5]
            for r in worst:
                print(f"       ⚠ {r.slug[:26]:28s} [{r.channel}] 源 {getattr(r, sk)} → 产出 {getattr(r, ok_)}")

    vec = [r for r in ok if r.src_shape > 0]
    if vec:
        tot_v = sum(r.src_shape for r in vec)
        tot_o = sum(r.out_shape for r in vec)
        print(f"    矢量形状：{len(vec)} 篇｜源 {tot_v} 个 → 占位 {tot_o} 个"
              f"｜检出率 {tot_o / tot_v * 100:.0f}%")

    print("\n  失败清单：")
    for r in rows:
        if not r.ok:
            print(f"    {r.slug[:26]:28s} [{r.channel}] {r.error[:60]}")

    worst = sorted(ok, key=lambda r: r.raw)[:10]
    print("\n  最差 10 篇（值得逐个看）：")
    for r in worst:
        print(f"    {r.slug[:26]:28s} [{r.channel:6s}] 相似度 {r.raw * 100:6.2f}%  "
              f"L2={r.l2:4d} L3={r.l3:4d}")

    with open(os.path.join(OUT, "corpus.csv"), "w", encoding="utf-8") as f:
        f.write("issue,slug,channel,lang,ok,raw_ratio,content_acc,l2,l3,ref_chars,"
                "src_tbl,out_tbl,src_img,out_img,src_shape,out_shape,error\n")
        for r in rows:
            f.write(f"{r.issue},{r.slug},{r.channel},{r.lang},{int(r.ok)},"
                    f"{r.raw:.6f},{r.content:.6f},{r.l2},{r.l3},{r.ref_chars},"
                    f"{r.src_tbl},{r.out_tbl},{r.src_img},{r.out_img},"
                    f"{r.src_shape},{r.out_shape},"
                    f'"{r.error}"\n')
    print(f"\n  明细已写出：{os.path.relpath(os.path.join(OUT, 'corpus.csv'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
