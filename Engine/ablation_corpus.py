#!/usr/bin/env python3
"""全库规则消融：判断每条规则在 56 篇真实稿件上的**净贡献**。

为什么必须全库做
----------------
早先只在 03 Liang_Zhang 一篇上做消融，结果 `en-apostrophe`（’ → '）显示 +0.025pp，
看起来有益，就留下了。后来全库逐项核对才发现：人工成品里 `’` 是**保留**的
（05 Wenzhong_Ma 的 docx 有 35 个 ’，成品保留 32 个），那条规则其实在拖后腿。
单篇调参会过拟合，规则取舍必须以全库为准。

做法：先把每篇的「未套规则」文本构建并缓存下来（docx 解析最慢，只做一次），
然后逐条规则重算，避免重复解析。
"""

from __future__ import annotations

import difflib
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from corpus import OUT, collect, pick_lang, target_basename  # noqa: E402
from run_poc import BODY_END, BODY_START, TPL, extract_region  # noqa: E402
from slcn import score as S  # noqa: E402
from slcn.build import (  # noqa: E402
    clean_field_value,
    insert_custom_macros,
    insert_body,
)
from slcn.docxsource import link_citations, parse_docx, refs_to_bib  # noqa: E402
from slcn.latex import collect_custom_macros, expand_inputs, parse_macros, set_field  # noqa: E402
from slcn.rules import RULES, apply_rules  # noqa: E402
from slcn.sources import extract_body, identify  # noqa: E402


SAMPLE = int(os.environ.get("ABL_SAMPLE", "40"))


def precache() -> list[tuple[str, str]]:
    """为每篇稿件构建「尚未套用规则」的完整 tex，并取回展开后的基准正文。

    返回 [(候选规则生效的 tex 文本, 人工成品正文), ...]
    """
    jobs = []
    items = collect()
    for n, (issue, slug, target) in enumerate(items, 1):
        try:
            art_dir = os.path.dirname(target)
            base = target_basename(slug)
            lang = pick_lang(target)
            from slcn.templates import resolve, sync_package_line
            sel = resolve(lang)
            tpl, _ = sync_package_line(open(sel.skeleton, encoding="utf-8").read(), sel.sty_name)
            ss = identify(art_dir, verbose=False)

            if ss.metadata:
                fields = set(parse_macros(tpl))
                meta = open(ss.metadata.path, encoding="utf-8", errors="replace").read()
                for name, m in parse_macros(meta).items():
                    if not name.startswith("my") or name not in fields:
                        continue
                    val, _ = clean_field_value(name, m.value)
                    if val.strip() in ("", "null"):
                        continue
                    tpl = set_field(tpl, name, val)
                tpl, _ = insert_custom_macros(tpl, collect_custom_macros(meta))
                body = extract_body(ss.body.path) if ss.body else ""
            elif ss.docx:
                doc = parse_docx(ss.docx)
                body, _, _ = link_citations(doc.body, doc.refs)
                for k, v in {
                    "mytitle": doc.title,
                    "myrunningtitle": doc.title,
                    "myfirstauthor": "\\kaishu " + doc.author.replace(" ", "") if doc.author else "null",
                    "myabstract": doc.abstract,
                    "mykeywords": doc.keywords,
                }.items():
                    if v and v != "null":
                        try:
                            tpl = set_field(tpl, k, v)
                        except KeyError:
                            pass
            else:
                continue

            if not body.strip():
                continue
            tpl = insert_body(tpl, body)
            ref = extract_region(expand_inputs(target), BODY_START, BODY_END)
            jobs.append((lang, tpl, ref))
            print(f"  [{n:2d}/{len(items)}] 缓存 {slug[:24]:26s} ok", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  [{n:2d}/{len(items)}] 缓存 {slug[:24]:26s} 跳过（{type(e).__name__}）", flush=True)
    return jobs


def _quick_ratio(a: str, b: str) -> float:
    """快速相似度：与 S.score 的 raw_ratio 口径一致，但关掉 autojunk=False。

    autojunk=False 在 6 万字符文本上是 O(n²)，全库消融会跑到几十分钟。
    这里只做**规则之间的相对比较**，用默认 autojunk=True 足够，且快一个量级。
    """
    return difflib.SequenceMatcher(None, a, b).quick_ratio()


def evaluate(jobs, disabled: set[str]) -> float:
    """返回平均相似度。"""
    vals = []
    for lang, tpl, ref in jobs:
        gen = extract_region(apply_rules(tpl, lang=lang, disabled=disabled).text, BODY_START, BODY_END)
        vals.append(_quick_ratio(ref, gen))
    return statistics.mean(vals)


def main() -> int:
    print("① 预构建（每篇只解析一次）…")
    jobs = precache()
    if SAMPLE and len(jobs) > SAMPLE:
        # 均匀抽样，保证中英文都覆盖到
        step = max(1, len(jobs) // SAMPLE)
        jobs = jobs[::step][:SAMPLE]
        print(f"   （抽样 {len(jobs)} 篇做消融，可用 ABL_SAMPLE 环境变量调整）")
    print(f"\n可用样本 {len(jobs)} 篇\n")
    if not jobs:
        return 1

    print("② 基准（全部规则启用）…")
    base_mean = evaluate(jobs, set())
    print(f"   平均相似度 {base_mean * 100:.3f}%\n")

    # 只测「会真的命中」的规则
    fired = set()
    for lang, tpl, _ in jobs:
        fired |= set(apply_rules(tpl, lang=lang).applied)

    print(f"③ 逐条停用（共 {len(fired)} 条命中过的规则）…\n")
    print(f"  {'规则':26s} {'停用后均值':>11s} {'贡献':>10s}   结论")
    print("  " + "─" * 62)
    rows = []
    for rid in sorted(fired):
        m = evaluate(jobs, {rid})
        rows.append((rid, m, base_mean - m))
    for rid, m, delta in sorted(rows, key=lambda x: x[2]):
        if abs(delta) < 1e-9:
            verdict = "—（无影响）"
        elif delta > 0:
            verdict = "✅ 有益"
        else:
            verdict = "❌ 有害，建议删除"
        print(f"  {rid:26s} {m * 100:10.3f}% {delta * 100:+9.3f}pp   {verdict}")

    print()
    print("  说明：贡献 = 启用该规则后的均值 − 停用后的均值。正数表示该规则有用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
