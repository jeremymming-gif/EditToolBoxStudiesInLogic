#!/usr/bin/env python3
r"""B-288：免报清单与 `proofcheck.RULES` / `docs/PROOF_RULES.md` 对齐（C4）。

判据（spec `drafts/specs/b288-taskbook-v2.md` §3 C4）：
  · `ruleset.RULESET_ID` 锁定；`len(RULES) == 29`；
  · `RULES` 的 id 集合 == `PROOF_RULES.md` §二 表的 id 集合；
  · 卡内免报清单一律由 `RULES` **生成**（禁止手抄，R4 防漂移）：
    每一行必须能逐字由 `id · title · default_action` 拼出，条数 == 29。

另测 `default_action()` 与文档表「默认动作」列逐条一致——这是 S3 的关键：
`proofcheck.RULES` 只有 `fixable`/`noisy` 等机器字段，文档写的是人话四级；
映射写错时这里立刻红（不是靠人眼看 29 行）。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_ruleset.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
for _p in (ENGINE, os.path.join(ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import proofcheck as P  # noqa: E402
from slcn import refmark as _refmark  # noqa: E402
from slcn import ruleset as R  # noqa: E402

FAILS: list[str] = []
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def _doc_table() -> dict[str, str]:
    """解析 `docs/PROOF_RULES.md` §二 表：{id: 默认动作}。"""
    path = os.path.join(ENGINE, "docs", "PROOF_RULES.md")
    rows: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("|"):
                continue
            cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
            if len(cells) == 5 and re.fullmatch(r"(?:ZH|EN|ALL)-\d+", cells[0]):
                rows[cells[0]] = cells[-1]
    return rows


def main() -> int:
    doc = _doc_table()
    ids = [r.id for r in P.RULES]

    # ── ① 规则集 id / 条数 ──────────────────────────────────────────────
    check("①-1 RULESET_ID 锁定为 proof29+fmt11+v1", R.RULESET_ID == "proof29+fmt11+v1",
          R.RULESET_ID)
    check("①-2 len(RULES) == 29", len(P.RULES) == 29, len(P.RULES))
    check("①-3 规则 id 无重复", len(ids) == len(set(ids)))
    check("①-4 规则 id 集合 == PROOF_RULES.md 表 id 集合",
          set(ids) == set(doc), (sorted(set(ids) ^ set(doc)),))
    check("①-5 文档表恰 29 行", len(doc) == 29, len(doc))
    check("①-6 FORMAT 硬性要求 11 条（RULESET_ID 的 fmt11）",
          [int(x) for x in re.findall(r"(?m)^(\d+)\.", _refmark.FORMAT_MD)] == list(range(1, 12)),
          re.findall(r"(?m)^(\d+)\.", _refmark.FORMAT_MD))

    # ── ② default_action 与文档「默认动作」列逐条一致 ─────────────────
    allowed = {"初排自动改", "初排自动搬位", "仅 --fix", "只报", "只报（噪声大）"}
    bad = [(rid, P.default_action(r), doc.get(rid)) for rid, r in zip(ids, P.RULES)
           if not doc.get(rid, "").startswith(P.default_action(r))]
    check("②-1 29 条 default_action 都落在五级之内",
          all(P.default_action(r) in allowed for r in P.RULES))
    check("②-2 default_action 与文档表「默认动作」列逐条一致", not bad, bad[:3])
    check("②-3 位置类 ZH-10/11 = 初排自动搬位（文档写 `--fix` 同）",
          P.default_action(next(r for r in P.RULES if r.id == "ZH-10")) == "初排自动搬位"
          and P.default_action(next(r for r in P.RULES if r.id == "ZH-11")) == "初排自动搬位")
    check("②-4 噪声项 ZH-07 = 只报（噪声大）",
          P.default_action(next(r for r in P.RULES if r.id == "ZH-07")) == "只报（噪声大）")
    check("②-5 ZH-08 虽 noisy 但在初排白名单 = 初排自动改",
          P.default_action(next(r for r in P.RULES if r.id == "ZH-08")) == "初排自动改")

    # ── ③ 免报清单生成式 ──────────────────────────────────────────────
    zh = R.no_report_lines("zh")
    en = R.no_report_lines("en")
    check("③-1 中文免报清单 29 行", len(zh) == 29, len(zh))
    check("③-2 英文免报清单 29 行", len(en) == 29, len(en))
    want = [f"`{r.id}` · {r.title} · {P.default_action(r)}" for r in P.RULES]
    check("③-3 中文清单逐行 == `id · 标题 · 默认动作`（生成式，不是手抄）",
          zh == want, [x for x in zh if x not in want][:2])
    check("③-4 中文清单顺序 == RULES 顺序",
          [x.split("`")[1] for x in zh] == ids)
    cjk = re.compile(r"[\u3400-\u9fff]")
    check("③-5 英文清单不含中文（C7 的 en 卡同源）",
          not any(cjk.search(x) for x in en), [x for x in en if cjk.search(x)][:2])
    check("③-6 英文 EN-05 带上目标形 `pp.\\,x--x`",
          any("pp.\\,x--x" in x for x in en))
    check("③-7 未知语种回退中文",
          R.no_report_lines("fr") == zh)

    # ── ④ rules_digest ────────────────────────────────────────────────
    dg = R.rules_digest()
    check("④-1 rules_digest 12 位十六进制", bool(re.fullmatch(r"[0-9a-f]{12}", dg)), dg)
    check("④-2 rules_digest 同进程两次相同（纯函数）", R.rules_digest() == dg)

    print(f"\n断言通过 {PASS} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
