#!/usr/bin/env python3.14
"""Bib 合成候选（DESIGN §6）的独立回归测试：把「判据」逐条钉成断言。

被测模块：`slcn/ai_bib_candidate.py`（纯函数；不写文件、不联网、不调模型）。
本文件**只用匿名夹具**（`alpha` / `A. Author` 这类），不读真实稿件与作者信息。

断言按 DESIGN §6 的条款分组（每条至少一个断言）：
  ① 必保集 R 与「安全命中冻结集」是**两回事**（§6 ②③⑤）：
     R 里的键 N 有就用 N（applied）、N 没有就回填 O 原文（backfilled）；
     safe hit 无论 N 给了什么，一律冻结旧版（frozen）且连带旧版依赖闭包。
  ② 未定义 `@string` → 阻断（§6 ⑧：写了也编译不过，不许猜）。
  ③ `@string` 保护（§6 ⑥⑧）：只带**实际用到**的定义、同一个只带一次；
     同名不同体 → 阻断，不猜合并。
  ④ 键重复、必保键在 O/N 里都没有 → 阻断并**点名**（§6 ①⑥）。
  ⑤ 允许键集合外的未知键 → 隔离成「待人工」，不自动并入、不阻断（§6 ⑦⑨）。
  ⑥ 未引用的旧条目：N 里保留则 applied，N 里没有则 dropped（不进 text，摘要列出）（§6 ⑨）。
  ⑦ 三类结论分开：引用闭合 / AI 核实完成 / 能编译，且「能编译」不许声称已通过（§6 ⑩）。
  ⑧ 月份缩写不算未定义 `@string`（`month = jan` 不阻断）。
  ⑨ `closure()` / `entries_by_key()` 的环检测：A→B→A 要如实报、且必须终止（§6 ④）。
  ⑩ 周边契约：`dep_keys` / `check_new_bib_only` / `observed_cited` / 条目顺序。
  ⑪ **已知缺陷的现状断言**：这些断言描述的是**当前实际行为**，与 DESIGN 不符；
     放在最后单独一段打印，结论以最终报告的 bug 清单为准（不许当成都对）。

不写 out/、不联网、不调模型。

用法：
    cd Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 \
        tests/test_ai_bib_candidate_c_20261117.py
"""

import os
import sys
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import ai_bib_candidate as M  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond, detail: str = "") -> bool:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def statuses(cand) -> list[tuple[str, str]]:
    return [(it["key"], it["status"]) for it in cand.items]


# ── 匿名夹具 ───────────────────────────────────────────────────────────────
ALPHA = "@article{alpha, title={Alpha T}, author={A. Author}, year={2001}}\n"
BETA = "@article{beta, title={Beta T}, author={B. Author}, year={2002}}\n"
GAMMA = "@article{gamma, title={Gamma T}, author={C. Author}, year={2003}}\n"
DELTA = "@article{delta, title={Delta T}, author={D. Author}, year={2004}}\n"

print("被测模块：", M.SCHEMA)


# ══ ① 必保集 R vs 安全命中冻结集（这一条最容易被写错）══════════════════
print("\n── ① 必保集 R 与安全命中冻结集（DESIGN §6 ②③⑤）──")

#: §6 ②⑤：正文 `\cite{alpha,beta}`；N 里有 alpha → 用 N；N 里缺 beta → 回填 O 原文。
_c1 = M.build_candidate(old_text=ALPHA + BETA, new_text=ALPHA + GAMMA,
                        tex_text=r"见 \cite{alpha,beta}。")
check("①-1 必保键在 N 里有 → status=applied（理由点名「必保键」）",
      _c1.items[1]["key"] == "alpha" and _c1.items[1]["status"] == M.ST_APPLIED
      and "必保键" in _c1.items[1]["reason"],
      f"{statuses(_c1)} / {_c1.items[1]['reason']}")
check("①-2 必保键在 N 里缺 → status=backfilled，理由含「保留旧版，未完成本次核查」",
      _c1.items[2]["key"] == "beta" and _c1.items[2]["status"] == M.ST_BACKFILL
      and "保留旧版，未完成本次核查" in _c1.items[2]["reason"],
      f"{statuses(_c1)} / {_c1.items[2]['reason']}")
check("①-3 backfilled 的条目**回填的是 O 原文**（不是被 N 丢掉=消失）",
      "beta" in (_c1.text or "") and "Beta T" in (_c1.text or ""),
      repr((_c1.text or "")[:80]))
check("①-4 必保集都满足 → 无阻断项",
      _c1.blocked == [], repr(_c1.blocked))
check("①-5 不在允许集合里的新键隔离，且**没有写进候选正文**",
      "gamma" not in (_c1.text or "") and _c1.unknown == ["gamma"], repr(_c1.unknown))

#: §6 ③：safe hit 一律冻结旧版，即使 N 里给了新版。
_c2 = M.build_candidate(
    old_text="@article{zulu, title={Zulu OLD}, author={Z. Author}, year={2009}}\n",
    new_text="@article{zulu, title={Zulu NEW}, author={Z. Author}, year={2009}}\n",
    tex_text=r"\cite{zulu}", safe_hit_keys=["zulu"])
check("①-6 safe hit 键 status=frozen（N 给了新版也忽略）",
      statuses(_c2) == [("zulu", M.ST_FROZEN)], str(statuses(_c2)))
check("①-7 frozen 的条目正文取**旧版原文**：含 Zulu OLD、不含 Zulu NEW",
      "Zulu OLD" in (_c2.text or "") and "Zulu NEW" not in (_c2.text or ""),
      repr((_c2.text or "")[:80]))
check("①-8 frozen 理由说明「不采纳 AI 对此的改动」",
      "不采纳 AI 对此的改动" in _c2.items[0]["reason"], _c2.items[0]["reason"])

#: §6 ③④：safe hit 的**旧版依赖闭包**一起冻结（这是「两回事」的另一半：
#: 依赖闭包是冻结集的，不是必保集的）。
_c3 = M.build_candidate(
    old_text="@article{a, title={A}, author={A}, crossref={b}}\n"
             "@article{b, title={B}, author={B}}\n" + ALPHA,
    new_text="@article{a, title={A}, author={A}, crossref={b}}\n"
             "@article{b, title={B CHANGED}, author={B}}\n" + ALPHA,
    tex_text=r"\cite{a,alpha}", safe_hit_keys=["a"])
check("①-9 safe hit 连带旧版依赖闭包一起 frozen（a 与 b 都冻结）",
      statuses(_c3) == [("a", M.ST_FROZEN), ("b", M.ST_FROZEN), ("alpha", M.ST_APPLIED)],
      str(statuses(_c3)))
check("①-10 冻结闭包里的 b 也是旧版原文（N 改的 B CHANGED 没进去）",
      "B CHANGED" not in (_c3.text or ""), repr((_c3.text or "")[:90]))
check("①-11 safe_hit 属性只报真正命中的键（闭包不外泄到属性里）",
      _c3.safe_hit == ["a"], repr(_c3.safe_hit))


# ══ ② 未定义 @string → 阻断 ═════════════════════════════════════════════
print("\n── ② 未定义 @string → 阻断（DESIGN §6 ⑧）──")
_c4 = M.build_candidate(
    old_text=ALPHA,
    new_text="@article{alpha, title={Alpha T}, author={A. Author}, publisher = nosuch}\n",
    tex_text=r"\cite{alpha}")
check("②-1 裸标识符无定义 → blocked 非空且点名 nosuch",
      len(_c4.blocked) == 1 and "未定义的 `@string` 名字" in _c4.blocked[0]
      and "`nosuch`" in _c4.blocked[0], repr(_c4.blocked))
check("②-2 阻断时 text is None（一个字节都不许写）",
      _c4.text is None, repr(_c4.text))
check("②-3 阻断时 closure_closed=False，摘要第一行要给「未闭合」",
      _c4.closure_closed is False and "未闭合" in _c4.summary_lines()[0],
      _c4.summary_lines()[0])
check("②-4 阻断项计入摘要的「阻断 N 条」",
      "阻断 1 条" in _c4.summary_lines()[1], _c4.summary_lines()[1])


# ══ ③ @string 保护：只带用到的一次；同名不同体阻断 ═══════════════════════
print("\n── ③ @string 保护（DESIGN §6 ⑥⑧）──")
_SHARED = ("@string{acm = {ACM Press}}\n@string{unused = {Never Used}}\n"
           "@article{a, title={A}, author={A}, publisher = acm}\n"
           "@article{b, title={B}, author={B}, publisher = acm}\n")
_c5 = M.build_candidate(old_text=_SHARED, new_text=_SHARED, tex_text=r"\cite{a,b}")
check("③-1 两个条目共用同一定义 → 只带一次（@string 块出现 1 次）",
      (_c5.text or "").count("@string") == 1, (_c5.text or "").count("@string").__str__())
check("③-2 带上的是定义**原文**（ACM Press 恰好 1 次）",
      (_c5.text or "").count("ACM Press") == 1, repr((_c5.text or "")[:60]))
check("③-3 没被用到的定义不带（unused 不出现）",
      "unused" not in (_c5.text or ""), repr((_c5.text or "")[:60]))
check("③-4 定义保护不引入阻断", _c5.blocked == [], repr(_c5.blocked))

#: 定义只在 N 里（AI 新加的定义）也要带上——否则候选编译不过。
_c6 = M.build_candidate(
    old_text="@article{a, title={A}, author={A}}\n",
    new_text='@string{pub = "New Pub"}\n@article{a, title={A}, author={A}, publisher = pub}\n',
    tex_text=r"\cite{a}")
check("③-5 定义只在 N 里时也能带上，且不阻断",
      _c6.blocked == [] and '@string{pub = "New Pub"}' in (_c6.text or ""),
      repr((_c6.text or "")[:70]))

#: 同名不同体 → 阻断（不猜合并）。连**没被用到**的定义冲突也要阻断。
_c7 = M.build_candidate(
    old_text="@string{acm = {ACM Press}}\n" + ALPHA,
    new_text="@string{acm = {ACME Press}}\n" + ALPHA,
    tex_text=r"\cite{alpha}")
check("③-6 同名不同体 → 阻断并点名 acm（不猜合并）",
      len(_c7.blocked) == 1 and "同名 `@string` 定义冲突" in _c7.blocked[0]
      and "`acm`" in _c7.blocked[0], repr(_c7.blocked))
check("③-7 定义冲突时 text is None", _c7.text is None, repr(_c7.text))
check("③-8 完全相同的两版定义**不**阻断（反例：不许把「一样」判成冲突）",
      M.build_candidate(old_text="@string{acm = {ACM Press}}\n" + ALPHA,
                        new_text="@string{acm = {ACM Press}}\n" + ALPHA,
                        tex_text=r"\cite{alpha}").blocked == [],
      repr(M.build_candidate(old_text="@string{acm = {ACM Press}}\n" + ALPHA,
                             new_text="@string{acm = {ACM Press}}\n" + ALPHA,
                             tex_text=r"\cite{alpha}").blocked))


# ══ ④ 键重复 / 必保键缺失 → 阻断并点名 ══════════════════════════════════
print("\n── ④ 键重复、必保键缺失 → 阻断并点名（DESIGN §6 ①⑥）──")
_c8 = M.build_candidate(old_text=ALPHA, new_text=ALPHA + ALPHA, tex_text=r"\cite{alpha}")
check("④-1 键重复 → 阻断，文案点名 alpha",
      len(_c8.blocked) == 1 and "键重复：" in _c8.blocked[0] and "`alpha`" in _c8.blocked[0],
      repr(_c8.blocked))
check("④-2 键重复时 text is None", _c8.text is None, repr(_c8.text))

#: §6 ⑥：依赖键在 O/N 里都没有 → 阻断并**点名**（不能猜）。
_DEPMISS = "@article{alpha, title={Alpha T}, author={A. Author}, crossref={ghostdep}}\n"
_c9 = M.build_candidate(old_text=_DEPMISS, new_text=_DEPMISS, tex_text=r"\cite{alpha}")
check("④-3 依赖键 O/N 都没有 → 进 missing 并点名 ghostdep",
      _c9.missing == ["ghostdep"], repr(_c9.missing))
check("④-4 阻断文案逐字给出「旧版与新版里都没有」并点名",
      any("这些必保键在**旧版与新版里都没有**" in b and "`ghostdep`" in b
          for b in _c9.blocked), repr(_c9.blocked))
check("④-5 依赖缺失的阻断不吞掉原依赖说明（两条都在）",
      len(_c9.blocked) == 2 and "在bib 里找不到" in _c9.blocked[0], repr(_c9.blocked))
check("④-6 依赖缺失时 text is None", _c9.text is None, repr(_c9.text))

#: §6 ①：残缺条目（花括号没闭合）不能作为合成依据。
_BROKEN = "@article{alpha, title={T}\n"
_c10 = M.build_candidate(old_text=_BROKEN, new_text=_BROKEN, tex_text=r"\cite{alpha}")
check("④-7 残缺条目 → 阻断并点名（文案说明「不能作为合成依据」）",
      any("解析不完整" in b and "`alpha`" in b for b in _c10.blocked), repr(_c10.blocked))
check("④-8 残缺条目时 text is None", _c10.text is None, repr(_c10.text))


# ══ ⑤ 未知键隔离（不自动并入、不阻断）══════════════════════════════════
print("\n── ⑤ 允许键集合外的未知键 → 隔离（DESIGN §6 ⑦⑨）──")
_c11 = M.build_candidate(old_text=ALPHA, new_text=ALPHA + GAMMA, tex_text=r"\cite{alpha}")
check("⑤-1 未知键 status=isolated，理由含「待人工，不自动并入」",
      statuses(_c11) == [("gamma", M.ST_ISOLATED), ("alpha", M.ST_APPLIED)]
      and "待人工，不自动并入" in _c11.items[0]["reason"], str(statuses(_c11)))
check("⑤-2 隔离**不是**阻断（blocked 为空）", _c11.blocked == [], repr(_c11.blocked))
check("⑤-3 隔离的键不进候选正文", "gamma" not in (_c11.text or ""),
      repr((_c11.text or "")[:60]))
check("⑤-4 隔离计入摘要「待人工 N 条」", "待人工 1 条" in _c11.summary_lines()[1],
      _c11.summary_lines()[1])

#: §6 ⑦：允许键集合里的键才允许并入（prepare 那一刻冻结）。
_c12 = M.build_candidate(old_text=ALPHA, new_text=ALPHA + GAMMA,
                         tex_text=r"\cite{alpha}", allowed_keys={"alpha", "gamma"})
check("⑤-5 在允许集合里的新键 → applied，理由「AI 核实结果」",
      ("gamma", M.ST_APPLIED) in statuses(_c12)
      and [i for i in _c12.items if i["key"] == "gamma"][0]["reason"] == "AI 核实结果",
      str(statuses(_c12)))
check("⑤-6 允许集合内的新键真的进了正文", "gamma" in (_c12.text or ""),
      repr((_c12.text or "")[:60]))

#: §6 ⑨：AI 改键名不能靠顺序映射——新键一律按未知键处理，旧键回填。
_c13 = M.build_candidate(
    old_text=ALPHA + BETA,
    new_text="@article{alpha2, title={Alpha T}, author={A. Author}, year={2001}}\n",
    tex_text=r"\cite{alpha,beta}")
check("⑤-7 改键名不按顺序映射：alpha2 被隔离，alpha/beta 各自 backfilled",
      statuses(_c13) == [("alpha2", M.ST_ISOLATED),
                         ("alpha", M.ST_BACKFILL), ("beta", M.ST_BACKFILL)],
      str(statuses(_c13)))
check("⑤-8 顺序映射失败时要闭合（旧版原文都回填了 → 引用闭合）",
      _c13.closure_closed is True and _c13.blocked == [], repr(_c13.blocked))


# ══ ⑥ 未引用的旧条目 ════════════════════════════════════════════════════
print("\n── ⑥ 未引用的旧条目（DESIGN §6 ⑨ 第 9 条）──")
_c14 = M.build_candidate(old_text=ALPHA + BETA + DELTA, new_text=ALPHA + DELTA,
                         tex_text=r"\cite{alpha}")
check("⑥-1 N 里没有的未引用旧条目 → status=dropped",
      ("beta", M.ST_DROPPED) in statuses(_c14), str(statuses(_c14)))
check("⑥-2 dropped 理由点名「按 §6 第 9 条不进入新文件（摘要列出）」",
      [i for i in _c14.items if i["key"] == "beta"][0]["reason"]
      == "未引用的旧条目且 N 里没有——按 §6 第 9 条不进入新文件（摘要列出）",
      [i for i in _c14.items if i["key"] == "beta"][0]["reason"])
check("⑥-3 dropped 的条目**不进** text", "beta" not in (_c14.text or ""),
      repr((_c14.text or "")[:70]))
check("⑥-4 dropped 的条目在摘要里列出来（「未引用未保留 1 条」）",
      "未引用未保留 1 条" in _c14.summary_lines()[1], _c14.summary_lines()[1])
# 验收 D1（CLI 端到端取证发现）：RULES §6 第 9 条要求「必须列出来」，只给计数
# 等于静默丢弃 —— 键名必须出现在人读摘要里（不是只在 --json 的 items[] 里）。
check("⑥-4b dropped 的键名也列出来（「未引用未保留 1 条（键：`beta`）」）",
      "未引用未保留 1 条（键：`beta`）" in _c14.summary_lines()[1], _c14.summary_lines()[1])
check("⑥-4c 没有 dropped 时不加空括号",
      "未引用未保留 0 条（" not in _c1.summary_lines()[1], _c1.summary_lines()[1])

_c15 = M.build_candidate(old_text=ALPHA + BETA, new_text=ALPHA + BETA,
                         tex_text=r"\cite{alpha}")
check("⑥-5 未引用旧条目在 N 里保留 → applied（用 AI 结果）",
      ("beta", M.ST_APPLIED) in statuses(_c15) and "beta" in (_c15.text or ""),
      str(statuses(_c15)))


# ══ ⑦ 三类结论分开 ══════════════════════════════════════════════════════
print("\n── ⑦ 三类结论分开（DESIGN §6 ⑩）──")
_l1, _l2, _l3 = _c1.summary_lines()
check("⑦-1 summary_lines() 恰好 3 行（不合并成一句）",
      len(_c1.summary_lines()) == 3, str(len(_c1.summary_lines())))
check("⑦-2 第 1 行是「引用闭合」", _l1.startswith("**引用闭合**"), _l1)
check("⑦-3 第 2 行是「AI 核实完成」", _l2.startswith("**AI 核实完成**"), _l2)
check("⑦-4 第 3 行是「能编译」", _l3.startswith("**能编译**"), _l3)
check("⑦-5 「能编译」那行**不声称**已通过编译（含「不在此处声称」，不含「已通过」/「通过编译」）",
      "不在此处声称" in _l3 and "已通过" not in _l3 and "通过编译" not in _l3, _l3)
check("⑦-6 「能编译」那行逐字等于设计文案（不许改口径）",
      _l3 == "**能编译**：本条目不判断——是否可编译以实际编译结果为准（不在此处声称）。", _l3)
check("⑦-7 「AI 核实完成」行把五类计数分开写（用 AI/回填/冻结/待人工/未保留/阻断）",
      all(k in _l2 for k in ("用 AI 新版", "回填旧版", "安全命中冻结旧版", "待人工",
                             "未引用未保留", "阻断")), _l2)
check("⑦-8 闭合时说「✅ 正文引用键都在候选里」",
      _l1 == "**引用闭合**：✅ 正文引用键都在候选里", _l1)


# ══ ⑧ 月份不算未定义 @string ═══════════════════════════════════════════
print("\n── ⑧ 月份缩写不算未定义 @string ──")
_MONTH = "@article{a, title={A}, author={A}, month = jan, year = 2001}\n"
check("⑧-1 bare_string_refs 不把 month=jan 当裸标识符",
      M.bare_string_refs(_MONTH) == set(), repr(M.bare_string_refs(_MONTH)))
check("⑧-2 bare_string_refs 认得真正的裸标识符（publisher = zzz → {'zzz'}）",
      M.bare_string_refs("@article{a, title={A}, publisher = zzz}\n") == {"zzz"},
      repr(M.bare_string_refs("@article{a, title={A}, publisher = zzz}\n")))
check("⑧-3 month=jan 不触发阻断",
      M.build_candidate(old_text=_MONTH, new_text=_MONTH, tex_text=r"\cite{a}").blocked == [],
      repr(M.build_candidate(old_text=_MONTH, new_text=_MONTH,
                             tex_text=r"\cite{a}").blocked))
check("⑧-4 BUILTIN_MONTHS 覆盖 12 个月缩写且统一小写",
      set(M.BUILTIN_MONTHS) == {"jan", "feb", "mar", "apr", "may", "jun",
                                "jul", "aug", "sep", "oct", "nov", "dec"},
      str(sorted(M.BUILTIN_MONTHS)))


# ══ ⑨ 环检测（不许无限递归）════════════════════════════════════════════
print("\n── ⑨ 依赖环检测（DESIGN §6 ④）──")
_CYCLE = ("@article{a, title={A}, author={A}, crossref={b}}\n"
          "@article{b, title={B}, author={B}, crossref={a}}\n")
_c16 = M.build_candidate(old_text=_CYCLE, new_text="", tex_text=r"\cite{a}")
check("⑨-1 A→B→A 如实报「依赖成环」并给出路径 a → b → a",
      any("依赖成环" in b and "a → b → a" in b for b in _c16.blocked), repr(_c16.blocked))
check("⑨-2 成环时 text is None（不写出会报错的库）", _c16.text is None, repr(_c16.text))
check("⑨-3 成环时仍然返回了 items（没有死循环/没有抛异常）",
      ("a", M.ST_BACKFILL) in statuses(_c16) and ("b", M.ST_BACKFILL) in statuses(_c16),
      str(statuses(_c16)))

_c17 = M.build_candidate(
    old_text="@article{a, title={A}, author={A}, crossref={a}}\n", new_text="",
    tex_text=r"\cite{a}")
check("⑨-4 自环 a→a 也报（路径 a → a）",
      any("依赖成环" in b and "a → a" in b for b in _c17.blocked), repr(_c17.blocked))

#: closure() 直接调用必须终止，并把环写进无解说明。
_tab = {"a": SimpleNamespace(fields={"crossref": "b"}),
        "b": SimpleNamespace(fields={"crossref": "a"})}
_cl, _bad = M.closure(["a"], _tab)
check("⑨-5 closure() 直接调用：收敛为 {'a','b'} 并报成环",
      _cl == {"a", "b"} and any("依赖成环" in x for x in _bad), f"{_cl} / {_bad}")
check("⑨-6 closure() 对菱形 DAG **不**误报环（a→{b,c}，b→d，c→d）",
      M.closure(["a"], {"a": SimpleNamespace(fields={"crossref": "b,c"}),
                        "b": SimpleNamespace(fields={"crossref": "d"}),
                        "c": SimpleNamespace(fields={"crossref": "d"}),
                        "d": SimpleNamespace(fields={})}) == ({"a", "b", "c", "d"}, []),
      str(M.closure(["a"], {"a": SimpleNamespace(fields={"crossref": "b,c"}),
                           "b": SimpleNamespace(fields={"crossref": "d"}),
                           "c": SimpleNamespace(fields={"crossref": "d"}),
                           "d": SimpleNamespace(fields={})})))
check("⑨-7 closure() 对缺失依赖点名（`a` 依赖的 `ghost` 在bib 里找不到）",
      M.closure(["a"], {"a": SimpleNamespace(fields={"crossref": "ghost"})})[1]
      == ["`a` 依赖的 `ghost` 在bib 里找不到"],
      str(M.closure(["a"], {"a": SimpleNamespace(fields={"crossref": "ghost"})})[1]))


# ══ ⑩ 周边契约 ══════════════════════════════════════════════════════════
print("\n── ⑩ 周边契约（dep_keys / check_new_bib_only / observed_cited / 顺序）──")
check("⑩-1 dep_keys 取 crossref+xdata，字段值已剥花括号",
      M.dep_keys({"crossref": "a,b", "xdata": "c"}) == ["a", "b", "c"],
      str(M.dep_keys({"crossref": "a,b", "xdata": "c"})))
check("⑩-2 dep_keys 去重且保序", M.dep_keys({"crossref": "a,a, b"}) == ["a", "b"],
      str(M.dep_keys({"crossref": "a,a, b"})))
check("⑩-3 DEP_FIELDS 只含 crossref / xdata",
      M.DEP_FIELDS == ("crossref", "xdata"), str(M.DEP_FIELDS))

check("⑩-4 check_new_bib_only：合法 bib → (True, [])",
      M.check_new_bib_only(ALPHA) == (True, []), str(M.check_new_bib_only(ALPHA)))
check("⑩-5 check_new_bib_only：空串 → (False, ['粘贴内容里没有解析出任何条目。'])",
      M.check_new_bib_only("") == (False, ["粘贴内容里没有解析出任何条目。"]),
      str(M.check_new_bib_only("")))
check("⑩-6 check_new_bib_only：garbage → 同上（不假装成功）",
      M.check_new_bib_only("garbage") == (False, ["粘贴内容里没有解析出任何条目。"]),
      str(M.check_new_bib_only("garbage")))

check("⑩-7 observed_cited：\\nocite{*} → (set(), True, 说明)",
      M.observed_cited(r"\nocite{*}")[0] == set()
      and M.observed_cited(r"\nocite{*}")[1] is True
      and "nocite{*}" in M.observed_cited(r"\nocite{*}")[2],
      str(M.observed_cited(r"\nocite{*}")))
check("⑩-8 observed_cited：普通 cite → 键集合 + False + 「正文引用 2 个键。」",
      M.observed_cited(r"见 \cite{alpha,beta}")[:2] == ({"alpha", "beta"}, False)
      and M.observed_cited(r"见 \cite{alpha,beta}")[2] == "正文引用 2 个键。",
      str(M.observed_cited(r"见 \cite{alpha,beta}")))
check("⑩-9 observed_cited：空正文 → 「未提供正文：…旧版全部条目都算必保。」",
      M.observed_cited("")[2] == "未提供正文：按「没有可参照的引用清单」处理，旧版全部条目都算必保。",
      M.observed_cited("")[2])
check("⑩-10 observed_cited：解析不出 cite 时明确说「不是全部也要、也不是都不要」",
      "请人工确认正文是否真的没有引用" in M.observed_cited("没有引用")[2],
      M.observed_cited("没有引用")[2])

check("⑩-11 entries_by_key 跳过 @string 块（不把它当条目）",
      set(M.entries_by_key("@string{acm={P}}\n" + ALPHA)[0]) == {"alpha"},
      str(set(M.entries_by_key("@string{acm={P}}\n" + ALPHA)[0])))
check("⑩-12 entries_by_key 空文本 → ({}, [])",
      M.entries_by_key("") == ({}, []), str(M.entries_by_key("")))
check("⑩-13 entries_by_key 报键重复",
      M.entries_by_key(ALPHA + ALPHA)[1]
      == ["键重复：`alpha`（同一键只能有一条，否则编译与引用都不确定）。"],
      str(M.entries_by_key(ALPHA + ALPHA)[1]))

#: 顺序：旧版原文顺序为主；新键按新版相对顺序追加。
_c18 = M.build_candidate(
    old_text=BETA + ALPHA, new_text=ALPHA + BETA + DELTA + GAMMA,
    tex_text=r"\cite{alpha,beta}", allowed_keys={"alpha", "beta", "gamma", "delta"})
check("⑩-14 条目顺序以**旧版原文顺序**为主（OLD 是 beta,alpha → 输出 beta 在前）",
      _c18.text.index("beta") < _c18.text.index("alpha"), repr((_c18.text or "")[:60]))
check("⑩-15 新键按**新版相对顺序**追加（N 里 delta 在 gamma 前）",
      _c18.text.index("delta") < _c18.text.index("gamma"), repr((_c18.text or "")[:120]))

_c19 = M.build_candidate(old_text=ALPHA + BETA, new_text=ALPHA,
                         tex_text=r"\cite{alpha,beta}")
check("⑩-16 count() 与 items 一致；摘要计数由同一批数字算出",
      (_c19.count(M.ST_APPLIED), _c19.count(M.ST_BACKFILL)) == (1, 1)
      and "用 AI 新版 1 条" in _c19.summary_lines()[1]
      and "回填旧版 1 条" in _c19.summary_lines()[1],
      f"{_c19.count(M.ST_APPLIED)}/{_c19.count(M.ST_BACKFILL)}")
check("⑩-17 SCHEMA 与状态标签是固定字面量（报告与界面共用）",
      M.SCHEMA == "slcn.ai_bib_candidate/1"
      and (M.ST_APPLIED, M.ST_BACKFILL, M.ST_FROZEN, M.ST_DROPPED,
           M.ST_ISOLATED, M.ST_BLOCKED) == ("applied", "backfilled", "frozen",
                                            "dropped", "isolated", "blocked"),
      str(M.SCHEMA))


# ══ ⑪ 已知缺陷的现状断言（与 DESIGN 不符；见最终报告）═════════════════
print("\n── ⑪ 回归：本轮修复过的缺陷（断言的是**修复后**的行为）──")

#: A1（严重，已修复）§6 ⑥：正文引用、但 O/N 都没有的键必须**阻断并点名**，
#: 不能静默丢掉还谎称「引用闭合」。
_ghost = M.build_candidate(old_text=ALPHA, new_text=ALPHA, tex_text=r"\cite{alpha,ghost}")
check("⑪-A1 正文引用 ghost 在 O/N 都没有 → 阻断、点名、不报闭合",
      _ghost.blocked != [] and _ghost.missing == ["ghost"]
      and _ghost.closure_closed is False,
      f"blocked={_ghost.blocked} missing={_ghost.missing} closed={_ghost.closure_closed}")
check("⑪-A1 阻断消息点名 ghost，且摘要第一行明说未闭合（不再 ✅）",
      any("ghost" in b for b in _ghost.blocked)
      and _ghost.summary_lines()[0] == "**引用闭合**：❗ 未闭合（见下）",
      _ghost.summary_lines()[0])
check("⑪-A1 阻断时不产出候选文本（一个字节都不写）", _ghost.text is None,
      repr(_ghost.text))
#: 反例：正文没有 ghost 时仍正常闭合，说明修复没把正常路径打死。
_ok1 = M.build_candidate(old_text=ALPHA, new_text=ALPHA, tex_text=r"\cite{alpha}")
check("⑪-A1 反例：正文只引 alpha → 仍闭合、不阻断",
      _ok1.closure_closed is True and _ok1.blocked == [], repr(_ok1.blocked))

#: A2（中，已修复）一个键只能有**一个**终态：必保优先于「未知新键」隔离。
_dupitem = M.build_candidate(old_text=ALPHA, new_text=ALPHA, tex_text=r"\cite{alpha}",
                             allowed_keys={"other"})
check("⑪-A2 allowed_keys 与必保集冲突 → 必保优先：一个键只有一条 items",
      statuses(_dupitem) == [("alpha", M.ST_APPLIED)]
      and _dupitem.count(M.ST_ISOLATED) == 0
      and _dupitem.count(M.ST_APPLIED) == 1,
      str(statuses(_dupitem)))
check("⑪-A2 摘要不再自相矛盾：待人工 0 条，alpha 照写（必保）",
      "待人工 0 条" in _dupitem.summary_lines()[1]
      and "alpha" in (_dupitem.text or ""),
      _dupitem.summary_lines()[1])

#: A3（中，已修复）`#` 拼接的多段 @string 定义体必须整段取到，冲突才能被发现。
_concat_o = '@string{acm = {X} # " Press"}\n@article{a, title={A}, author={A}, publisher = acm}\n'
_concat_n = '@string{acm = {X} # " Books"}\n@article{a, title={A}, author={A}, publisher = acm}\n'
_cA3 = M.build_candidate(old_text=_concat_o, new_text=_concat_n, tex_text=r"\cite{a}")
check("⑪-A3 多段定义整段取到（不再截断成 `{X`）",
      M.string_defs(_concat_o) == {"acm": "X # Press"}
      and M.string_defs(_concat_n) == {"acm": "X # Books"},
      f"{M.string_defs(_concat_o)} / {M.string_defs(_concat_n)}")
check("⑪-A3 「Press」与「Books」的真冲突被抓住 → 阻断",
      _cA3.blocked == ["同名 `@string` 定义冲突（不猜，保持阻断）：`acm`"]
      and _cA3.text is None,
      repr(_cA3.blocked))
#: 反例：多段定义完全相同时不该阻断（只取一次、照常写出）。
_concat_same = ('@string{acm = {X} # " Press"}\n'
                '@article{a, title={A}, author={A}, publisher = acm}\n')
_csame = M.build_candidate(old_text=_concat_same, new_text=_concat_same, tex_text=r"\cite{a}")
check("⑪-A3 反例：多段定义相同 → 不阻断，且定义只写一次",
      _csame.blocked == [] and (_csame.text or "").count("@string") == 1,
      repr(_csame.blocked))

#: A4（低，已修复）同一语义的定义只因空白/包裹写法不同就判冲突 → 归一化后不拦。
_sem_o = '@string{pub = {Same Pub}}\n@article{a, title={A}, author={A}, publisher = pub}\n'
_sem_n = '@string{pub = { Same   Pub }}\n@article{a, title={A}, author={A}, publisher = pub}\n'
check("⑪-A4 定义体仅空白差异 → 不算冲突（归一化后相等）",
      M.build_candidate(old_text=_sem_o, new_text=_sem_n, tex_text=r"\cite{a}").blocked == [],
      repr(M.build_candidate(old_text=_sem_o, new_text=_sem_n,
                             tex_text=r"\cite{a}").blocked))
check("⑪-A4 花括号版与引号版的同一语义不算冲突",
      M.build_candidate(
          old_text='@string{pub = {Same Pub}}\n@article{a, title={A}, author={A}, publisher = pub}\n',
          new_text='@string{pub = "Same Pub"}\n@article{a, title={A}, author={A}, publisher = pub}\n',
          tex_text=r"\cite{a}").blocked == [],
      str(M.string_defs('@string{pub = {Same Pub}}\n'))
      + " / " + str(M.string_defs('@string{pub = "Same Pub"}\n')))
#: 反例：语义真不同仍要拦（归一化不能把真冲突吃掉）。
check("⑪-A4 反例：定义体真不同（Same Pub vs Other Pub）仍阻断",
      M.build_candidate(
          old_text='@string{pub = {Same Pub}}\n@article{a, title={A}, author={A}, publisher = pub}\n',
          new_text='@string{pub = {Other Pub}}\n@article{a, title={A}, author={A}, publisher = pub}\n',
          tex_text=r"\cite{a}").blocked
      == ["同名 `@string` 定义冲突（不猜，保持阻断）：`pub`"],
      "")

#: A5（低，已修复）O 与 N 都残缺时同一条阻断消息只出现一次（去重、保序）。
_cA5 = M.build_candidate(old_text=_BROKEN, new_text=_BROKEN, tex_text=r"\cite{alpha}")
check("⑪-A5 O/N 都残缺 → 同一条阻断消息只出现一次",
      len(_cA5.blocked) == 1
      and _cA5.blocked[0] == "条目 `alpha` 解析不完整（花括号/字段残缺），不能作为合成依据。",
      repr(_cA5.blocked))
#: 去重不能把**不同**的阻断原因也吃掉。
_multi_bad = M.build_candidate(old_text=_BROKEN + BETA, new_text="@article{beta, title={T}\n",
                               tex_text=r"\cite{beta}")
check("⑪-A5 反例：不同的阻断原因都保留（去重只去相同文字）",
      len(_multi_bad.blocked) == len(set(_multi_bad.blocked)) >= 2,
      repr(_multi_bad.blocked))


# ── 汇总 ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：" + "；".join(FAILS))
    sys.exit(1)
print("失败 0 项（全部通过）")
