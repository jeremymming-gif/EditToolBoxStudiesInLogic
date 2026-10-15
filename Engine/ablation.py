#!/usr/bin/env python3
"""消融实验：逐条关闭规则，量化每条规则对最终准确率的真实贡献。

这是判断「规则到底有没有用」的唯一可靠办法——凭直觉写的规则很容易帮倒忙，
比如 `zh-ascii-paren` 乍看合理（西文语词用半角括号），实测却发现本刊恰恰
要求保留全角括号。
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_poc import ARTICLES, BODY_END, BODY_START, TPL, extract_region  # noqa: E402
from slcn import score as S  # noqa: E402
from slcn.latex import insert_body, parse_macros, set_field  # noqa: E402
from slcn.rules import RULES, apply_rules  # noqa: E402
from slcn.sources import extract_body, identify  # noqa: E402

TEX_RULES = [r for r in RULES if r.lang in ("zh", "both") and r.safe]


def build_tex_variant(disabled: set[str]) -> str:
    """重建 LaTeX 通道的产物，但停用指定规则。"""
    a = ARTICLES["tex"]
    ss = identify(a["dir"], verbose=False)
    tpl = open(os.path.join(TPL, "CN_template.tex"), encoding="utf-8").read()
    meta = open(ss.metadata.path, encoding="utf-8").read()
    for name, m in parse_macros(meta).items():
        if name.startswith("my") and name in set(parse_macros(tpl)) and not m.is_null:
            try:
                tpl = set_field(tpl, name, m.value.strip())
            except KeyError:
                pass
    tpl = insert_body(tpl, extract_body(ss.body.path))
    return apply_rules(tpl, lang="zh", disabled=disabled).text


def body_of(tex: str) -> str:
    return extract_region(tex, BODY_START, BODY_END)


def main() -> int:
    a = ARTICLES["tex"]
    ref = body_of(open(os.path.join(a["dir"], f"{a['basename']}.tex"), encoding="utf-8").read())

    base_tex = build_tex_variant(disabled=set())
    base_res = S.score(ref, body_of(base_tex))
    base_acc = base_res.raw_ratio
    print("LaTeX 通道 · 规则消融实验")
    print(f"  基准（全部规则启用）：正文裸相似度 {base_acc * 100:.2f}%")
    print()
    print(f"  {'规则':26s} {'命中':>5s} {'停用后相似度':>13s} {'贡献':>10s}")
    print("  " + "─" * 60)

    rows = []
    for rule in TEX_RULES:
        tex = build_tex_variant(disabled={rule.id})
        acc = S.score(ref, body_of(tex)).raw_ratio
        n = len(re.findall(rule.pattern, build_tex_variant(disabled={rule.id})))
        rows.append((rule.id, n, acc, base_acc - acc))

    for rid, n, acc, delta in sorted(rows, key=lambda x: x[3]):
        if n == 0 and abs(delta) < 1e-9:
            continue
        flag = "✅" if delta > 1e-9 else ("❌ 有害" if delta < -1e-9 else "—")
        print(f"  {rid:28s} {n:5d} {acc * 100:11.2f}% {delta * 100:+8.3f}pp  {flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
