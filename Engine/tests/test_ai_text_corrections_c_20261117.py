#!/usr/bin/env python3.14
"""文字纠错硬边界（DESIGN §7）的独立回归测试：把「硬边界」逐条钉成断言。

被测模块：`slcn/ai_text_corrections.py`（纯函数；不写文件、不联网、不调模型）。
本文件只用**匿名夹具**，不读真实稿件与作者信息。

断言按 DESIGN §7 的条款分组（每条至少一个断言）：
  ① 保护区内一律不改（§7 ③）：数学 / 引用键 / 标签 / 注释 / 命令名 / URL / 路径。
  ② 重复片段不许猜（§7 ④⑤）：无唯一上下文 → reported；给足上下文 → applied。
  ③ 跨度上限 MAX_SPAN（§7 ②）：超限 → rejected，理由含「重写」。
  ④ 不确定分类 → 只报告（§7 ⑥）：uncertain / 未知类别永不 applied。
  ⑤ 专名保护（§7 ⑦）：两端首字母大写 → reported，理由是改术语/人名。
  ⑥ 同一基线不累积（§7 ⑧）：重叠改动只生效一处；源文件只读语义。
  ⑦ `parse_suggestions` 宽松（§7 ⑨）：认类别别名与中英文别名，认不出 → uncertain。
  ⑧ `summarise` 三类分开报（§7 ⑩），不合并成「全部核实」。
  ⑨ **已知缺陷的现状断言**：描述的是**当前实际行为**，与 DESIGN 不符；
     结论以最终报告的 bug 清单为准（不许当成都对）。

不写 out/、不联网、不调模型。

用法：
    cd Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 \
        tests/test_ai_text_corrections_c_20261117.py
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import ai_text_corrections as M  # noqa: E402

FAILS: list[str] = []
PROTECT = M.PROTECT_NOTE


def check(name: str, cond, detail: str = "") -> bool:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def item(**kw) -> M.Item:
    """造一条建议（缺省 category=typo，最容易被采纳的那一类）。"""
    kw.setdefault("category", M.CAT_TYPO)
    return M.Item(**kw)


print("被测模块：", M.SCHEMA, "| MAX_SPAN =", M.MAX_SPAN, "| CTX =", M.CTX)


# ══ ① 保护区内一律不改 ══════════════════════════════════════════════════
print("\n── ① 保护区内一律不改（DESIGN §7 ③）──")


def rejected_in(text: str, old: str, new: str, why: str) -> None:
    """改保护区里的东西 → 必须 rejected，且理由写明「保护区内（<原因>）」。"""
    it = M.judge(text, item(old=old, new=new))
    check(f"① 保护区：{why} → rejected",
          it.status == M.ST_REJECTED
          and "落在保护区内" in it.reason and why in it.reason and PROTECT in it.reason,
          f"{it.status} / {it.reason}")


rejected_in("见 $x+y$ 处。", "x+y", "x-y", "行内数学")            # §7 ③ 数学
rejected_in("见 \\cite{alpha} 处。", "alpha", "beta", "引用键")     # §7 ③ 引用键
rejected_in("见 \\label{sec:intro} 处。", "sec:intro", "sec:body", "标签/引用")
rejected_in("文字 % a typo heer\n", "heer", "here", "注释")         # §7 ③ 注释
rejected_in("见 \\url{http://ex.com/paht} 处。", "paht", "path", "URL/路径命令")
rejected_in("文件 a/b/c.txt 处。", "c.txt", "d.txt", "路径")        # §7 ③ 路径
rejected_in("用 \\textbf 加粗。", "textbf", "textit", "命令")        # §7 ③ 命令名
rejected_in("见 \\[a+b\\] 处。", "a+b", "a-b", "行间数学")           # §7 ③ 行间数学
rejected_in("见 \\begin{equation}x\\end{equation} 处。", "x", "y", "数学环境")

#: protected_spans 是这套拒绝的**唯一依据**，它自己也得对。
#: 优先级：`\cite{…}` 既是「引用键」也落在「命令+参数」里，原因须取「引用键」。
_spans = M.protected_spans("$x$ \\cite{a} % c\n")
check("① protected_spans 覆盖数学/引用/命令/注释（原因按优先级归一）",
      [(s, e) for s, e, _ in _spans] == [(0, 3), (4, 12), (13, 16)]
      and [r for _, _, r in _spans] == ["行内数学", "引用键", "注释"],
      str(_spans))

#: 替换的**结果**伸进保护区也不行（不是只看 old 的位置）。
_it_into = M.judge("word $x$ tail", item(old="word", new="word $x$"))
check("① 替换后伸进保护区 → rejected（不许把正文改写成带数学的片段）",
      _it_into.status == M.ST_REJECTED and "替换后会伸进保护区" in _it_into.reason
      and "行内数学" in _it_into.reason, f"{_it_into.status} / {_it_into.reason}")

#: 保护区内的片段如果**恰好也在别处出现**，不算「唯一可定位」——仍要拒绝。
check("① 保护区判定在定位之后（保护区里唯一命中 → 仍是 rejected 而非 applied）",
      M.judge("$ \\alpha $", item(old="alpha", new="beta")).status == M.ST_REJECTED,
      M.judge("$ \\alpha $", item(old="alpha", new="beta")).reason)


# ══ ② 重复片段不许猜 ════════════════════════════════════════════════════
print("\n── ② 重复片段不许猜（DESIGN §7 ④⑤）──")
_DUP = "the cat sat on the mat and the cat ran"

_it_dup = M.judge(_DUP, item(old="cat", new="dog"))
check("②-1 无上下文 → reported（不是 applied，也不乱挑第一个）",
      _it_dup.status == M.ST_REPORTED
      and _it_dup.reason == "片段在正文里出现 2 次，缺上下文无法唯一定位（不猜）",
      f"{_it_dup.status} / {_it_dup.reason}")

_it_ctx1 = M.judge(_DUP, item(old="cat", new="dog", before="the ", after=" sat"))
check("②-2 给了唯一上下文before/after → applied，且定位到第一处（start=4）",
      _it_ctx1.status == M.ST_APPLIED and _it_ctx1.start == 4,
      f"{_it_ctx1.status} / start={_it_ctx1.start}")

_it_ctx2 = M.judge(_DUP, item(old="cat", new="dog", before="and the ", after=" ran"))
check("②-3 换一组上下文 → 定位到第二处（start=31）",
      _it_ctx2.status == M.ST_APPLIED and _it_ctx2.start == 31,
      f"{_it_ctx2.status} / start={_it_ctx2.start}")

#: 注意 `before="the "` 并不是矛盾上下文：第 2 处前面是 "…and the "，**以** "the " 结尾，
#: 所以它确实唯一命中第 2 处（start=31）——这是设计要的行为。
_it_softctx = M.judge(_DUP, item(old="cat", new="dog", before="the ", after=" ran"))
check("②-4 before 只要**是前缀的结尾**就算匹配（第 2 处前面以 'the ' 结尾 → start=31）",
      _it_softctx.status == M.ST_APPLIED and _it_softctx.start == 31,
      f"{_it_softctx.status} / start={_it_softctx.start}")

_it_badctx = M.judge(_DUP, item(old="cat", new="dog", before="ZZZ "))
check("②-5 上下文与所有重复候选都对不上 → reported，理由「重复片段与给定上下文对不上」",
      _it_badctx.status == M.ST_REPORTED
      and _it_badctx.reason == "重复片段与给定上下文对不上",
      f"{_it_badctx.status} / {_it_badctx.reason}")
_it_badctx2 = M.judge(_DUP, item(old="cat", new="dog", after=" ZZZ"))
check("②-6 after 对不上同样 reported（不许退化成「随便挑一处」）",
      _it_badctx2.status == M.ST_REPORTED
      and _it_badctx2.reason == "重复片段与给定上下文对不上",
      f"{_it_badctx2.status} / {_it_badctx2.reason}")

#: locate() 是判定重复的底层，它自己的返回值也要钉住。
check("②-7 locate：唯一命中 → (2,7,'')（'a typoo here' 里 typoo 就在下标 2..7）",
      M.locate("a typoo here", "typoo") == (2, 7, ""), str(M.locate("a typoo here", "typoo")))
check("②-8 locate：找不到 → start<0 且理由含「在正文里找不到」",
      M.locate("abc", "zzz")[0] < 0 and "在正文里找不到" in M.locate("abc", "zzz")[2],
      str(M.locate("abc", "zzz")))
check("②-9 locate：重复无上下文 → start<0 且理由含「出现 2 次」",
      M.locate(_DUP, "cat")[0] < 0 and "出现 2 次" in M.locate(_DUP, "cat")[2],
      str(M.locate(_DUP, "cat")))
check("②-10 locate：old 为空 → start<0 且理由「原文片段为空」",
      M.locate("abc", "")[0] < 0 and M.locate("abc", "")[2] == "原文片段为空",
      str(M.locate("abc", "")))


# ══ ③ 跨度上限 ══════════════════════════════════════════════════════════
print("\n── ③ 跨度上限 MAX_SPAN（DESIGN §7 ②）──")
_LONG_OLD = "x" * (M.MAX_SPAN + 1)
_LONG_NEW = "y" * (M.MAX_SPAN + 1)
_it_long = M.judge(_LONG_OLD, item(old=_LONG_OLD, new="short"))
check(f"③-1 old 超过 {M.MAX_SPAN} 字 → rejected", _it_long.status == M.ST_REJECTED,
      f"{_it_long.status} / {_it_long.reason}")
check(f"③-2 超限理由含「跨度超过 {M.MAX_SPAN} 字」与「重写」（禁止润色/改写）",
      f"跨度超过 {M.MAX_SPAN} 字" in _it_long.reason and "重写" in _it_long.reason
      and "§7" in _it_long.reason, _it_long.reason)
_it_long_new = M.judge(_LONG_OLD, item(old=_LONG_OLD, new=_LONG_NEW))
check("③-3 new 超限同样 rejected（不只是看 old）",
      _it_long_new.status == M.ST_REJECTED and "重写" in _it_long_new.reason,
      f"{_it_long_new.status} / {_it_long_new.reason}")
#: 反例：刚好等于上限必须放过（否则边界写成 off-by-one 也没人发现）。
_at_limit = "a" * M.MAX_SPAN
_it_at = M.judge(_at_limit, item(old=_at_limit, new="b" * M.MAX_SPAN))
check(f"③-4 恰好 {M.MAX_SPAN} 字 → applied（边界不许提前一格）",
      _it_at.status == M.ST_APPLIED, f"{_it_at.status} / {_it_at.reason}")
check("③-5 MAX_SPAN 常量就是 40（报告里要引用的数字）", M.MAX_SPAN == 40, str(M.MAX_SPAN))


# ══ ④ 不确定分类 → 只报告 ═══════════════════════════════════════════════
print("\n── ④ 不确定分类 → 只报告（DESIGN §7 ⑥）──")
_it_unc = M.judge("一个 typoo 在此", item(category=M.CAT_UNCERTAIN, old="typoo", new="typo"))
check("④-1 category=uncertain → reported", _it_unc.status == M.ST_REPORTED,
      f"{_it_unc.status} / {_it_unc.reason}")
check("④-2 uncertain 理由说明「按 §7 只报告，默认不采纳」",
      "只报告" in _it_unc.reason and "默认不采纳" in _it_unc.reason, _it_unc.reason)

_it_style = M.judge("一个 typoo 在此", item(category="style", old="typoo", new="typo"))
check("④-3 未知类别（直调 judge）→ rejected，理由点名该类别",
      _it_style.status == M.ST_REJECTED and "style" in _it_style.reason,
      f"{_it_style.status} / {_it_style.reason}")

#: 同上一条建议，但**经 parse_suggestions 入口**：未知类别被降级成 uncertain → reported。
_parsed_style = M.parse_suggestions([{"category": "style", "old": "typoo", "new": "typo"}])
check("④-4 未知类别经 parse_suggestions → 归一成 uncertain",
      len(_parsed_style) == 1 and _parsed_style[0].category == M.CAT_UNCERTAIN,
      str(_parsed_style[0].category if _parsed_style else None))
_it_style2 = M.judge("一个 typoo 在此", _parsed_style[0])
check("④-5 于是这条路走到 reported（永不 applied）",
      _it_style2.status == M.ST_REPORTED, f"{_it_style2.status} / {_it_style2.reason}")
check("④-6 ADOPTABLE 只含 typo/missing/grammar（三类可采纳，uncertain 不在内）",
      M.ADOPTABLE == frozenset(("typo", "missing", "grammar")), str(sorted(M.ADOPTABLE)))


# ══ ⑤ 专名保护 ══════════════════════════════════════════════════════════
print("\n── ⑤ 专名保护（DESIGN §7 ⑦）──")
_it_proper = M.judge("Smith 说了话。", item(old="Smith", new="Smyth"))
check("⑤-1 两端都是首字母大写单词（Smith→Smyth）→ reported",
      _it_proper.status == M.ST_REPORTED, f"{_it_proper.status} / {_it_proper.reason}")
check("⑤-2 理由点明「像人名/地名/机构名，改它属于改术语」",
      "人名/地名/机构名" in _it_proper.reason and "改术语" in _it_proper.reason,
      _it_proper.reason)
_it_lower = M.judge("smith 说了话。", item(old="smith", new="smyth"))
check("⑤-3 反例：全小写同名改动 → applied（保护**不许**误伤普通词）",
      _it_lower.status == M.ST_APPLIED, f"{_it_lower.status} / {_it_lower.reason}")
_it_spaced = M.judge("New York 在此。", item(old="New York", new="Nu York"))
check("⑤-4 含空格的短语不触发专名保护（只按单 token 判）→ applied",
      _it_spaced.status == M.ST_APPLIED, f"{_it_spaced.status} / {_it_spaced.reason}")


# ══ ⑥ 同一基线不累积 ════════════════════════════════════════════════════
print("\n── ⑥ 同一基线不累积（DESIGN §7 ⑧）──")
_OVL = "aaa bbb ccc"
_res = M.apply_items(_OVL, [item(old="aaa bbb", new="xxx"),
                            item(old="bbb ccc", new="yyy")])
check("⑥-1 两处重叠 → 只有一处生效（text == 'aaa yyy'）",
      _res["text"] == "aaa yyy", repr(_res["text"]))
check("⑥-2 生效那条 applied、另一条降级 reported（理由是「重叠，不叠加」）",
      len(_res["applied"]) == 1 and len(_res["reported"]) == 1
      and "重叠" in _res["reported"][0]["reason"]
      and "不叠加" in _res["reported"][0]["reason"],
      f"applied={[a['old'] for a in _res['applied']]} "
      f"reported={[r['old'] for r in _res['reported']]}")
check("⑥-3 降级那条的理由含「与另一处已采纳改动重叠（同一基线，不叠加）」",
      _res["reported"][0]["reason"] == "与另一处已采纳改动重叠（同一基线，不叠加）",
      _res["reported"][0]["reason"])
check("⑥-4 没给 baseline_sha 时 baseline_sha256 == source_sha256（同一基线）",
      _res["baseline_sha256"] == _res["source_sha256"]
      and _res["source_sha256"] == M.sha256_text(_OVL),
      f"{_res['baseline_sha256']} / {_res['source_sha256']}")
check("⑥-5 源文件只读语义：apply_items **不改**传入的源字符串",
      _OVL == "aaa bbb ccc", repr(_OVL))
_res2 = M.apply_items("aaa bbb ccc", [item(old="aaa bbb", new="xxx"),
                                      item(old="bbb ccc", new="yyy")])
check("⑥-6 同一基线重算 → 结果与 source_sha256 都稳定（不漂移）",
      _res2["text"] == _res["text"] and _res2["source_sha256"] == _res["source_sha256"],
      repr(_res2["text"]))

_res3 = M.apply_items(_OVL, [item(old="aaa bbb", new="xxx")], baseline_sha="deadbeef")
check("⑥-7 显式 baseline_sha 被保留，而 source_sha256 仍是实算（两者不混同）",
      _res3["baseline_sha256"] == "deadbeef"
      and _res3["source_sha256"] == M.sha256_text(_OVL),
      f"{_res3['baseline_sha256']} / {_res3['source_sha256']}")

#: 反例：不重叠的两处都要生效（不许把「同一基线」误读成「一次只能改一处」）。
_res4 = M.apply_items("one typoo here and another there",
                      [item(old="typoo", new="typo"),
                       item(old="another", new="other")])
check("⑥-8 反例：不重叠的两处都 applied，正文两处都改了",
      len(_res4["applied"]) == 2 and _res4["text"] == "one typo here and other there",
      repr(_res4["text"]))
check("⑥-9 返回结构是 schema/text/baseline_sha256/source_sha256/unchanged/applied/reported/rejected",
      set(_res4) == {"schema", "text", "baseline_sha256", "source_sha256",
                     "unchanged", "applied", "reported", "rejected"}, str(sorted(_res4)))
check("⑥-10 有改动时 unchanged=False", _res4["unchanged"] is False, str(_res4["unchanged"]))
_res5 = M.apply_items(_OVL, [])
check("⑥-11 没有建议 → text 原样、unchanged=True、三类都空",
      _res5["text"] == _OVL and _res5["unchanged"] is True
      and (_res5["applied"], _res5["reported"], _res5["rejected"]) == ([], [], []),
      repr(_res5["text"]))

#: judge() 只判定不落盘：判完源字符串不变。
_before = "a typoo here"
_it_judged = M.judge(_before, item(old="typoo", new="typo"))
check("⑥-12 judge() 是纯函数：不改传入的正文",
      _before == "a typoo here" and _it_judged.status == M.ST_APPLIED
      and _it_judged.start == 2, f"{_it_judged.status} / {_it_judged.start}")
check("⑥-13 Item.as_dict() 可 json.dumps（要过 JSON 边界上报）",
      json.dumps(_it_judged.as_dict(), ensure_ascii=False) != ""
      and _it_judged.as_dict()["old"] == "typoo",
      json.dumps(_it_judged.as_dict(), ensure_ascii=False))


# ══ ⑦ parse_suggestions 宽松 ════════════════════════════════════════════
print("\n── ⑦ parse_suggestions 宽松（DESIGN §7 ⑨）──")
_payload = [
    {"category": "typo", "old": "teh", "new": "the"},
    {"kind": "missing", "find": "漏了一个字", "replace": "补上"},
    {"category": "语法", "original": "a b", "suggestion": "a, b"},
    {"category": "错别字", "old": "甲", "new": "乙"},
    {"category": "漏字", "old": "丙", "new": "丙丁"},
    {"category": "错字", "old": "戊", "new": "己"},
    {"category": "Missing", "old": "庚", "new": "辛"},
    {"category": "语法", "old": "壬", "new": "癸"},
    "not a dict",
]
_ps = M.parse_suggestions(_payload)
check("⑦-1 认 category 与 kind 两种键名（前两条正确归类）",
      [x.category for x in _ps[:2]] == [M.CAT_TYPO, M.CAT_MISSING],
      str([x.category for x in _ps[:2]]))
check("⑦-2 认中英文类别别名（语法/错别字/漏字/错字/Missing 全部归类）",
      [x.category for x in _ps] == [M.CAT_TYPO, M.CAT_MISSING, M.CAT_GRAMMAR,
                                    M.CAT_TYPO, M.CAT_MISSING, M.CAT_TYPO,
                                    M.CAT_MISSING, M.CAT_GRAMMAR],
      str([x.category for x in _ps]))
check("⑦-3 非 dict 项被跳过（9 项进 8 项出）",
      len(_ps) == 8 and all(isinstance(x, M.Item) for x in _ps), str(len(_ps)))
check("⑦-4 字段别名 old/find/original 都认（第 2 条 find→old，第 3 条 original→old）",
      _ps[1].old == "漏了一个字" and _ps[2].old == "a b",
      f"{_ps[1].old!r} / {_ps[2].old!r}")
check("⑦-5 字段别名 new/replace/suggestion 都认",
      _ps[1].new == "补上" and _ps[2].new == "a, b", f"{_ps[1].new!r} / {_ps[2].new!r}")
check("⑦-6 认不出的类别 → uncertain（不猜成 typo，永不 applied）",
      M.parse_suggestions([{"category": "风格问题", "old": "a", "new": "b"}])[0].category
      == M.CAT_UNCERTAIN,
      M.parse_suggestions([{"category": "风格问题", "old": "a", "new": "b"}])[0].category)
check("⑦-7 缺 category/kind 也降级 uncertain（不许默认成可采纳）",
      M.parse_suggestions([{"old": "a", "new": "b"}])[0].category == M.CAT_UNCERTAIN,
      M.parse_suggestions([{"old": "a", "new": "b"}])[0].category)
check("⑦-8 空输入 → []（None 与 [] 都不炸）",
      M.parse_suggestions(None) == [] and M.parse_suggestions([]) == [],
      f"{M.parse_suggestions(None)} / {M.parse_suggestions([])}")
_tr = M.parse_suggestions([{"category": "typo", "old": "a", "new": "b",
                            "before": "前", "after": "后", "context": "上下文",
                            "item_id": "x1"}])[0]
check("⑦-9 before/after/context/item_id 原样透传",
      (_tr.before, _tr.after, _tr.context, _tr.item_id) == ("前", "后", "上下文", "x1"),
      f"{_tr.before!r} {_tr.after!r} {_tr.context!r} {_tr.item_id!r}")
_auto = M.parse_suggestions([{"category": "typo", "old": "a", "new": "b"}])[0]
check("⑦-10 没给 item_id 时自动生成（便于复核时点名，不许是空串）",
      _auto.item_id.startswith("t") and len(_auto.item_id) == 13, _auto.item_id)


# ══ ⑧ summarise 三类分开报 ══════════════════════════════════════════════
print("\n── ⑧ summarise 三类分开报（DESIGN §7 ⑩）──")
_three = M.apply_items("见 $x+y$ 与 typoo", [
    item(old="x+y", new="x-y"),                        # 保护区 → rejected
    item(old="typoo", new="typo"),                     # applied
    item(category=M.CAT_UNCERTAIN, old="与", new="和"),  # reported
])
_lines = M.summarise(_three)
check("⑧-1 一行说完但**三类数字分开**（可采纳1 / 只报告1 / 判据拒绝1）",
      _lines[0] == "**文字纠错**：可采纳 1 处；只报告 1 处；判据拒绝 1 处（源文件只读，是否写入由调用方决定）",
      _lines[0])
check("⑧-2 三类计数与 apply_items 的列表长度一致（不许自己另算一套）",
      (len(_three["applied"]), len(_three["reported"]), len(_three["rejected"])) == (1, 1, 1),
      f"{len(_three['applied'])}/{len(_three['reported'])}/{len(_three['rejected'])}")
check("⑧-3 不许把三类合并成「全部核实/全部通过」",
      "全部" not in _lines[0] and "全部核实" not in _lines[0], _lines[0])
check("⑧-4 明说「源文件只读，是否写入由调用方决定」",
      "源文件只读" in _lines[0], _lines[0])
_conly = M.summarise(M.apply_items("abc", []))
check("⑧-5 无改动时追加「本次没有实际文本改动。」（第二行）",
      len(_conly) == 2 and _conly[1] == "本次没有实际文本改动。", str(_conly))
_mixed = M.summarise(M.apply_items("a typoo here", [item(old="typoo", new="typo")]))
check("⑧-6 有改动时**不**出现「本次没有实际文本改动。」",
      len(_mixed) == 1 and "本次没有实际文本改动" not in _mixed[0], str(_mixed))


# ══ ⑨ 回归：B1/B2/B3 三处缺陷已修（DESIGN §7 ③④⑤）═════════════════════
print("\n── ⑨ 回归：命令参数保护 / CTX 生效 / $$ 定界（§7 ③④⑤）──")

#: B1（严重）PROTECT_NOTE 与 §7 ③ 都写「命令/参数…内不做替换」。
#: 修前 `_RE_CMD` 只保护**命令名本身**，`\caption{a typo}` 这种全小写参数会被当正文改掉。
check("⑨-B1 命令**连同参数**都在保护面里（不再是只保护命令名）",
      M.protected_spans("a \\emph{teh} b") == [(2, 12, "命令")],
      str(M.protected_spans("a \\emph{teh} b")))
for _cmd_text, _old, _new, _label in [
        ("见 \\emph{teh} 处。", "teh", "the", "\\emph{...}"),
        ("见 \\footnote{teh} 处。", "teh", "the", "\\footnote{...}"),
        ("\\section{introdution}", "introdution", "introduction", "\\section{...}"),
        ("见 \\caption{a typo} 处。", "a typo", "a typo.", "\\caption{...}")]:
    _it = M.judge(_cmd_text, item(old=_old, new=_new))
    check(f"⑨-B1 {_label} 参数里的改动 → rejected（保护区拦下，不靠专名巧合）",
          _it.status == M.ST_REJECTED
          and "落在保护区内" in _it.reason and "命令" in _it.reason,
          f"{_it.status} / {_it.reason}")

#: 反例：**没有参数**的裸命令名仍是「命令」保护区；参数外的正文照改不误。
check("⑨-B1 反例：命令外的正文仍可采纳（保护面没有扩大成整段）",
      M.judge("见 \\emph{x} 处的 typo 。",
              item(old="typo", new="typoX")).status == M.ST_APPLIED,
      M.judge("见 \\emph{x} 处的 typo 。",
              item(old="typo", new="typoX")).reason)
check("⑨-B1 反例：嵌套命令的内层参数也被单独保护（改动仍被拒）",
      (10, 18, "命令") in M.protected_spans("a \\textbf{\\emph{x}} b")
      and M.judge("a \\textbf{\\emph{xy}} b",
                  item(old="xy", new="yx")).status == M.ST_REJECTED,
      f"{M.protected_spans('a \\\\textbf{\\\\emph{x}} b')} / "
      f"{M.judge('a \\\\textbf{\\\\emph{xy}} b', item(old='xy', new='yx')).reason}")

#: B2（低）`CTX` 曾声明不用（上下文整串比较 → 常量是死代码）。
#: 修后：只比靠锚点那侧 CTX 个字符，长/短上下文判定一致；对不上的上下文必须拒绝定位。
_ctx_text = "the cat sat"
_ctx_long = M.locate(_ctx_text, "cat", before="the lazily long context " * 3)
_ctx_short = M.locate(_ctx_text, "cat", before="the ")
check("⑨-B2 CTX 生效：上下文长短不改变定位结果（窗口只取靠锚点那侧）",
      M.CTX == 24 and _ctx_long == _ctx_short == (4, 7, ""),
      f"CTX={M.CTX} long={_ctx_long} short={_ctx_short}")
check("⑨-B2 上下文对不上 → 不定位（不猜，报告）",
      M.locate("the cat sat on the cat mat", "cat", before="zzz ")[:2] == (-1, -1),
      str(M.locate("the cat sat on the cat mat", "cat", before="zzz ")))

#: B3（中）`$$…$$` 曾被当**行内**数学，且 `$` 配对会跨过 `$$` 吞掉中间正文。
check("⑨-B3 `$$…$$` 标成「行间数学」",
      M.protected_spans("$$a+b$$") == [(0, 7, "行间数学")],
      str(M.protected_spans("$$a+b$$")))
_disp = "$$x$$ 正文 typo 在这里 $$y$$"
check("⑨-B3 两块 $$ 之间的**正文不在**保护区（不再被吞）",
      all(not (s <= _disp.index("typo") < e) for s, e, _ in M.protected_spans(_disp)),
      str(M.protected_spans(_disp)))
_it_disp = M.judge(_disp, item(old="typo", new="typoX"))
check("⑨-B3 后果修复：两块 $$ 之间正文里的错字 → applied（改得动了）",
      _it_disp.status == M.ST_APPLIED, f"{_it_disp.status} / {_it_disp.reason}")
check("⑨-B3 反例：孤立的单个 `$` 不会把后面整段正文当数学（正文仍可改）→ applied",
      M.judge("一个字 $ 符号后面 typo 在此", item(old="typo", new="typoX")).status
      == M.ST_APPLIED,
      M.judge("一个字 $ 符号后面 typo 在此", item(old="typo", new="typoX")).reason)
check("⑨-B3 反例：行内 $…$ 里的字仍被拒绝（数学不碰）",
      M.judge("$a+b$ 后面 typo", item(old="a+b", new="a-b")).status == M.ST_REJECTED,
      M.judge("$a+b$ 后面 typo", item(old="a+b", new="a-b")).reason)


# ── 汇总 ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：" + "；".join(FAILS))
    sys.exit(1)
print("失败 0 项（全部通过）")
