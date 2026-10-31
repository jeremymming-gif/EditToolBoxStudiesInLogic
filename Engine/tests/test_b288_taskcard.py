#!/usr/bin/env python3.14
"""B-288 S1/S2：任务书 v2 任务卡 + golden（C1/C2/C3/C4/C6/C7）。

不做端到端（那是 e2e 的事）：这里只钉**任务卡本体的契约**——
  · C1 卡在题名之后、`## 一` 之前；TASK-ID 纯函数、稳定、对 7 个输入全敏感；
  · C2 SPEC_MD/SOURCES_MD/FORMAT_MD/_ITEM10_COOP 一字节不改；默认参数输出 == v1；
  · C3 golden 逐字节 + pinned sha256；
  · C4 免报清单 29 条由 `proofcheck.RULES` 生成（条数/id 集合/标题/默认动作全同源）；
  · C6 卡 ≤60 行、正反例条数、全合成脱敏；
  · C7 zh/en 双语各自的内容锚点。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

_ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE))

from slcn import coop, proofcheck as P, refmark as R, ruleset as RS, taskcard as T  # noqa: E402

GOLDEN = _ENGINE / "tests" / "fixtures" / "golden_taskbook" / "taskbook_bibliography_v2.golden"

#: golden 的输入 bib：3 条合成条目（`San_Zhang2020`/`Doe1999`/`Quine1970`）。
BIB = """@article{San_Zhang2020,
  author = {San, Zhang},
  title = {Logic and Computation},
  journal = {Journal of Logic},
  year = {2020},
}
@book{Doe1999,
  author = {Doe, Jane},
  title = {A Book of Logic},
  publisher = {Doe Press},
  year = {1999},
}
@inproceedings{Quine1970,
  author = {Quine, W. V. O.},
  title = {Philosophy of Logic},
  booktitle = {Proceedings of Mind},
  year = {1970},
}
"""
BIB_SHA = "cd2e180eaa8c1a2a10865ecfc97bc27adad393cd2b5ba7c2ab68e340dd89e56e"
SCOPE = ["Doe1999", "Quine1970", "San_Zhang2020", "mode=all", "verify-max=200"]
TID = "BIB-San_Zhang-19db7b80"

#: C2 冻结常量与 v1 输出的 pinned sha256（2026-10-31 于 base 348ed03 实测）。
SHA_SPEC = "cba4a9acd087a8d4d0e9561e58f3d20050f142d098492105fd5141493afeb492"
SHA_SOURCES = "df25a7a11ebdd0c4159aa0086ac758e44c6c1b3de2c3909a96897f1c3c258366"
SHA_FORMAT = "0cdf6977057211de331dfb03ed47fd17245826853753e4a21876636b60ede77f"
SHA_ITEM10_COOP = "349aee002114b1ef1785088878f1909f5e1d3bba0db1fac624e53e605d54f68a"
SHA_ITEM10_PLAIN = "959ed2a49d8805113f00c2da9b3739298f5ac16030f63e6e188a1b90dd40e82c"
SHA_V1 = "d8393ca3e07ecc73f7c8e49d9ac2f33e33b166180240063cd1fb2d77f62fcf88"
SHA_GOLDEN = "d6adffaf762aff15745380e247740e98ab0b24bfc165228f8c03486ac271e55b"

_ok = 0
_bad: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ok
    if cond:
        _ok += 1
        print(f"PASS  {label}")
    else:
        _bad.append(label)
        print(f"FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build(task_id: str = TID, **kw) -> str:
    base = dict(coop=True, lang="zh", ruleset=RS.RULESET_ID)
    base.update(kw)
    if task_id:
        base["task_id"] = task_id
    return R.build(BIB, "San_Zhang", **base)


def _card(text: str) -> str:
    """从任务书里切出卡本体（`## 任务卡` 到 `## 一` 之前的 `---` 分隔线）。"""
    i = text.find("## 任务卡")
    if i < 0:
        i = text.find("## Task card")
    j = text.find("\n\n---\n\n## ", i)
    j = text.find("\n\n---\n\n", i)
    return text[i:j]


def test_c1() -> None:
    md = _build()
    lines = md.splitlines()
    check("①-1 题名行保持 v1 原样", lines[0] == "# 参考文献核实任务书 · San_Zhang")
    i_card = md.find("## 任务卡")
    i_spec = md.find("## 一、编辑部 bib 规范")
    i_counts = md.find("- 条目总数：")
    check("①-2 卡在逐条计数之后", i_counts < i_card, f"{i_counts} < {i_card}")
    check("①-3 卡在 `## 一` 之前（S2 插入点）", 0 < i_card < i_spec, f"{i_card} < {i_spec}")
    check("①-4 卡不在题名之前", not md.startswith("## 任务卡"))
    check("①-5 TASK-ID 文本可在任务书里原样取回", T.extract_task_id(md) == TID,
          T.extract_task_id(md))
    base = dict(source_sha256=BIB_SHA, engine_version="1.4.1", ruleset_id=RS.RULESET_ID,
                schema=T.TASKBOOK_SCHEMA, kind="BIB", lang="zh", scope_keys=SCOPE)
    tid = T.task_id(**base, label="San_Zhang")
    check("①-6 同输入两次 ID 相同（纯函数）", T.task_id(**base, label="San_Zhang") == tid)
    check("①-7 ID 形如 `<KIND>-<Author>-<8 hex>`",
          re.fullmatch(r"BIB-San_Zhang-[0-9a-f]{8}", tid) is not None, tid)
    other = dict(base, scope_keys=["Doe1999", "Quine1970", "San_Zhang2020", "mode=all",
                                    "verify-max=5"])
    check("①-8 --verify-max 变化（scope_keys）→ ID 变", T.task_id(**other, label="San_Zhang") != tid)
    variants = {
        "engine_version": dict(base, engine_version="1.4.2"),
        "ruleset_id": dict(base, ruleset_id="proof29+fmt11+v2"),
        "schema": dict(base, schema="taskbook.v3"),
        "kind": dict(base, kind="FULL"),
        "lang": dict(base, lang="en"),
        "source_sha256": dict(base, source_sha256="0" * 64),
    }
    bad = [k for k, v in variants.items() if T.task_id(**v, label="San_Zhang") == tid]
    check("①-9 7 个输入任一变化都改 ID（独立 sha，非指纹复用）", not bad, str(bad))
    spaced = T.task_id(**base, label="San Zhang")
    check("①-10 label 里的空格归一成 `_`", re.fullmatch(r"BIB-San_Zhang-[0-9a-f]{8}", spaced))
    check("①-11 完全相同的 payload 才有相同 ID（label 也参与）",
          spaced != T.task_id(**base, label="SanZhang"))
    check("①-12 无卡的文本取不到 ID", T.extract_task_id("# 参考文献核实任务书 · X\n") == "")


def test_c2() -> None:
    for label, text, want in (
        ("②-1 SPEC_MD 一字节未改", R.SPEC_MD, SHA_SPEC),
        ("②-2 SOURCES_MD 一字节未改", R.SOURCES_MD, SHA_SOURCES),
        ("②-3 FORMAT_MD 一字节未改", R.FORMAT_MD, SHA_FORMAT),
        ("②-4 _ITEM10_COOP 一字节未改", R._ITEM10_COOP, SHA_ITEM10_COOP),
        ("②-5 _ITEM10_PLAIN 一字节未改", R._ITEM10_PLAIN, SHA_ITEM10_PLAIN),
    ):
        check(f"{label}（sha256）", sha(text) == want, sha(text))
    v1 = R.build(BIB, "San_Zhang", coop=True)
    check("②-6 默认参数（无 task_id）任务书 == v1 逐字节", sha(v1) == SHA_V1, sha(v1))
    check("②-7 默认参数下没有任务卡", "## 任务卡" not in v1)
    full = _build()
    i = full.find("\n\n## 任务卡")
    j = full.find("\n\n---\n\n## 一、", i)
    check("②-8 带卡输出去卡后 == v1 逐字节", full[:i] + full[j:] == v1)


def test_c3() -> None:
    text = GOLDEN.read_text(encoding="utf-8")
    check("③-1 golden 存在且非空", len(text) > 10000, str(len(text)))
    check("③-2 golden 与 render 输出逐字节相同（build 自带文件尾换行）", _build() == text)
    check("③-3 golden sha256 == pinned", sha(text) == SHA_GOLDEN, sha(text))
    check("③-4 golden 的输入 bib sha256 == pinned", sha(BIB) == BIB_SHA, sha(BIB))


def _rule_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if re.match(r"^  - `[A-Z]+-\d+` · ", ln)]


def test_c4() -> None:
    md = _build()
    card = _card(md)
    lines = _rule_lines(card)
    ids = [re.match(r"^  - `([^`]+)`", ln).group(1) for ln in lines]
    check("④-1 卡内免报清单恰好 29 条", len(lines) == 29, str(len(lines)))
    check("④-2 清单 id 集合 == proofcheck.RULES id（同源，禁手抄）",
          ids == [r.id for r in P.RULES], str(ids[:3]))
    check("④-3 len(RULES) == 29", len(P.RULES) == 29, str(len(P.RULES)))
    missing = []
    for line, rule in zip(lines, P.RULES):
        action = P.default_action(rule)
        if rule.title not in line or action not in line:
            missing.append(rule.id)
    check("④-4 每条标题/默认动作与 RULES+default_action 一致", not missing, str(missing))
    check("④-5 卡头摘要 == ruleset.rules_digest()", f"摘要 `{RS.rules_digest()}`" in card,
          RS.rules_digest())
    en = _card(_build(lang="en"))
    en_ids = [re.match(r"^  - `([^`]+)`", ln).group(1) for ln in _rule_lines(en)]
    check("④-6 en 卡清单条数/id 与 zh 完全相同（C4 语种无关）", en_ids == ids)


def test_c6() -> None:
    for lang in ("zh", "en"):
        card = T.render_card("BIB", lang, TID, "x", coop=True, ruleset_id=RS.RULESET_ID)
        check(f"⑥-1[6] {lang} 卡总长 ≤60 行", len(card.splitlines()) <= 60,
              str(len(card.splitlines())))
        full = T.render_card("FULL", lang, TID, "x", coop=True, ruleset_id=RS.RULESET_ID)
        check(f"⑥-2[6] {lang} 全文卡也 ≤60 行", len(full.splitlines()) <= 60,
              str(len(full.splitlines())))
    zh = T.render_card("BIB", "zh", TID, "x", coop=True, ruleset_id=RS.RULESET_ID)
    check("⑥-3[6] bib 正例 ≥2", sum(1 for ln in zh.splitlines() if "✅ bib" in ln) >= 2)
    check("⑥-4[6] bib 反例 ≥2", sum(1 for ln in zh.splitlines() if "❌ bib" in ln) >= 2)
    check("⑥-5[6] 全文正例 ≥1", sum(1 for ln in zh.splitlines() if "✅ 全文" in ln or "✅ full" in ln) >= 1)
    check("⑥-6[6] 全文反例 ≥1", sum(1 for ln in zh.splitlines() if "❌ 全文" in ln or "❌ full" in ln) >= 1)
    check("⑥-7[6] 正反例全部合成脱敏（只用 San_Zhang/Doe）",
          "San_Zhang" in zh and "Doe" in zh and "Quine" not in zh and "张三" not in zh)
    check("⑥-8[6] 明写第 10 条只覆盖 BibTeX、附一 JSON 单列",
          "只覆盖 BibTeX" in zh and "单列" in zh)


def test_c7() -> None:
    zh = T.render_card("BIB", "zh", TID, "x", coop=True, ruleset_id=RS.RULESET_ID)
    en = T.render_card("BIB", "en", TID, "x", coop=True, ruleset_id=RS.RULESET_ID)
    check("⑦-1 zh 卡含「数学区不报」", "数学区" in zh and "不报" in zh)
    check("⑦-2 zh 卡含「不改写正文」铁律", "不改写正文" in zh)
    check("⑦-3 en 卡含 `pp\\,x--x` 目标形", "pp.\\,x--x" in en)
    check("⑦-4 en 卡不含中文（C7）", not re.search(r"[\u4e00-\u9fff]", en))
    check("⑦-5 en 卡仍是 29 条清单", len(_rule_lines(_card(_build(lang="en")))) == 29)
    check("⑦-6 未知语种回退中文", T.render_card("BIB", "fr", TID) == T.render_card("BIB", "zh", TID))
    check("⑦-7 默认 ruleset 省略时卡头仍显示 RULESET_ID",
          f"`{RS.RULESET_ID}`" in T.render_card("BIB", "zh", TID))
    check("⑦-8 lang 只影响卡，不影响题名/条目段", _card(_build(lang="en")) != _card(_build()))


def test_state() -> None:
    common = dict(params={"source": "x.tex", "source_sha256": "y"},
                  taskbook_path="", stage_dir=".", final_dir=".",
                  bib_keys=["A"], low_items=[], budget_article=0, budget_batch=0,
                  engine_version="1.4.1")
    new = coop.build_state(**common, taskbook_version=2, taskbook_task_id=TID,
                           taskbook_volumes=["/a", "/b", "/c"])
    old = coop.build_state(**common)
    check("⑧-1 SCHEMA_VERSION 仍是 1（不 bump）", new["schema"] == 1)
    check("⑧-2 taskbook_version/task_id/volumes 三键写入", new.get("taskbook_version") == 2
          and new.get("taskbook_task_id") == TID
          and new.get("taskbook_volumes") == ["/a", "/b", "/c"])
    check("⑧-3 旧调用不写这三键（旧状态兼容）",
          "taskbook_version" not in old and "taskbook_task_id" not in old)
    check("⑧-4 check_ph2 接受新键状态", coop.check_ph2(new, common["params"])[0])


def main() -> int:
    test_c1()
    test_c2()
    test_c3()
    test_c4()
    test_c6()
    test_c7()
    test_state()
    print()
    print(f"断言通过 {_ok} 项，失败 {len(_bad)} 项")
    for label in _bad:
        print(f"  · {label}")
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
