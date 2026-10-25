#!/usr/bin/env python3.14
"""年份唯一兜底必须带「姓名证据」（第 73 条）。

缺陷（案例 73）：`link_citations()` 里姓氏+年份精确匹配落空时，只要该年份在参考文献表里
**唯一**，就 `hit = by_year[year][0]` 直接替换。实测真稿 `10 Lei_Li` 的正文
`（Williamson 2017；修订扩充版，2021）` 因此被静默换成 `\\parencite{Hjortland2017}`
（Hjortland 2017《Anti-Exceptionalism About Logic》），印进 PDF 第 6 页；而参考文献里
Williamson 的键是 `Williamson2007`（年份 2007），真正的威廉姆森条目**从未被引用**。

全库 23 次触发逐条核对后：5 次姓名完全对不上（必须改成 unmatched），
18 次姓名其实对得上、只是 `RefEntry.surname` 把西文名首字母算进了姓
（`S. Blackburn` / `H.Kelsen` / `J.Woods`）或中文名写在方括号里（`Xing, Fuyi [邢福义]`），
精确匹配才落空——这 18 次必须留住。所以兜底**保留但加证据门**，且命中必留警示日志。

断言分组：
  (a) 反例（核心）：年份唯一但姓名对不上 → **不许**替换，且该引用点进 `unmatched`；
  (b) 反例边界：词边界，`Li` 不许命中 `Little`（防「子串松匹配」把证据门做漏）；
  (c) 正例：姓+年精确匹配照旧转换（含页码尾巴），且**不**走兜底、不留兜底日志；
  (d) 保留的兜底：有姓名证据时仍配，并且**留下可留档的警示**；
  (e) 真实稿回归（语料不在本机则 SKIP）：`10 Lei_Li` 的 Williamson 引用进 unmatched、
      `Hjortland2017` 只剩本来正确的那 1 处。

不写 out/、不联网、不跑 corpus.py / build.sh / docx_audit.py。用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 \\
        tests/test_citation_year_fallback.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_ENGINE))
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.docxsource import (  # noqa: E402
    RefEntry,
    _year_fallback_evidence,
    link_citations,
)

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def E(key, author, year, title="T"):
    """造一条最小 RefEntry（字段与 parse_docx 出来的同形）。"""
    return RefEntry(raw=f"{author}. {year}. {title}[M].", author=author, year=year,
                    title=title, key=key)


def fallback_logs(logs):
    return [s for s in logs if "兜底到" in s]


# ══ (a) 反例（核心）：年份唯一 ≠ 姓名对得上 ══════════════════════════════════
print("── (a) 反例：年份唯一但姓名对不上 → 不许替换，必须进 unmatched ──")

# 任务书点名的构造：refs 只有一条 2007 年的 Williamson2007，正文写「张三 2007」。
REFS_W = [E("Williamson2007", "Williamson, T.", "2007", "The Philosophy of Philosophy")]
body, un, logs = link_citations("（张三 2007）", REFS_W)
check("(a) 反例：`（张三 2007）` 未被替换成 cite（正文逐字不变）",
      body == "（张三 2007）", repr(body))
check("(a) 反例：`（张三 2007）` 进了 unmatched",
      un == ["（张三 2007）"], repr(un))
check("(a) 反例：没有留下「兜底命中」日志（因为根本没配）",
      fallback_logs(logs) == [], repr(fallback_logs(logs)))

# 案例 73 原样：2017 年唯一的是 Hjortland2017，正文说的是 Williamson 2017。
REFS_HW = [
    E("Hjortland2017", "Hjortland, O", "2017", "Anti-Exceptionalism About Logic"),
    E("Williamson2007", "Williamson, T.", "2007", "The Philosophy of Philosophy"),
]
W_BODY = "（Williamson 2017；修订扩充版，2021）"
body2, un2, logs2 = link_citations(W_BODY, REFS_HW)
check("(a) 案例 73：`（Williamson 2017；修订扩充版，2021）` 原样保留",
      body2 == W_BODY, repr(body2))
check("(a) 案例 73：该引用点进 unmatched",
      un2 == [W_BODY], repr(un2))
check("(a) 案例 73：`Hjortland2017` 未被塞进正文（不再静默错配）",
      "Hjortland2017" not in body2, repr(body2))

# 同源反例（用户已复现）：Kripke2007 / 某机构2013 这类「同姓不同年 / 同年不同姓」。
REFS_K = [E("Recanati2007", "Recanati, F", "2007", "Perspectival Thought")]
for raw in ["(Kripke 2007: 301)", "(Kripke 2007: 303)"]:
    b, u, _l = link_citations(raw, REFS_K)
    check(f"(a) 分例：`{raw}` 未被配成 Recanati2007", b == raw and u == [raw],
          f"body={b!r} un={u!r}")

REFS_G = [E("AGregory2021", "A. Gregory", "2021", "Desire as Belief")]
b_g, u_g, _l = link_citations("（何朝安，2021）", REFS_G)
check("(a) 分例：`（何朝安，2021）` 未被配成 AGregory2021",
      b_g == "（何朝安，2021）" and u_g == ["（何朝安，2021）"], f"body={b_g!r} un={u_g!r}")

# 说明性括号（不是引用）：`（IPCAS，1955年正式成立）` 曾被硬配成 Shen1955。
REFS_S = [E("Shen1955", "Shen, Y. [Yuting Shen]", "1955", "Two Semantical Paradoxes")]
b_s, u_s, _l = link_citations("（IPCAS，1955年正式成立）", REFS_S)
check("(a) 分例：说明性括号 `（IPCAS，1955年正式成立）` 未被配成 Shen1955",
      b_s == "（IPCAS，1955年正式成立）" and u_s == ["（IPCAS，1955年正式成立）"],
      f"body={b_s!r} un={u_s!r}")

# ══ (b) 反例边界：证据门必须是词边界，不许退化成子串匹配 ═══════════════════
print("\n── (b) 反例边界：`Li` 不许命中 `Little`（词边界，不是子串） ──")

REFS_L = [E("Little2001", "Little, S.", "2001", "X")]
b_l, u_l, _l = link_citations("（Li 2001）", REFS_L)
check("(b) `（Li 2001）` 未被配成 `Little, S.`（子串 `li` 不算证据）",
      b_l == "（Li 2001）" and u_l == ["（Li 2001）"], f"body={b_l!r} un={u_l!r}")
check("(b) 证据函数对 `Li`/`Little` 返回空（无证据）",
      _year_fallback_evidence("Li", ["Li"], REFS_L[0]) == "",
      repr(_year_fallback_evidence("Li", ["Li"], REFS_L[0])))
check("(b) 反过来：`（Little 2001）` 精确匹配仍然命中（正例，不是被证据门挡的）",
      link_citations("（Little 2001）", REFS_L)[0] == "（\\parencite{Little2001}）",
      repr(link_citations("（Little 2001）", REFS_L)[0]))

# ══ (c) 正例：姓+年精确匹配照旧 ═════════════════════════════════════════════
print("\n── (c) 正例：姓+年精确匹配照旧（含页码尾巴），且不走兜底 ──")

REFS_HAACK = [
    E("Haack1978", "Haack, S.", "1978", "Philosophy of Logics"),
    E("Williamson2007", "Williamson, T.", "2007", "The Philosophy of Philosophy"),
]
b_h, u_h, l_h = link_citations("（Haack 1978）", REFS_HAACK)
check("(c) `（Haack 1978）` → `（\\parencite{Haack1978}）`",
      b_h == "（\\parencite{Haack1978}）", repr(b_h))
check("(c) 精确命中不进 unmatched", u_h == [], repr(u_h))
check("(c) 精确命中**不留**兜底日志（走的是姓+年那条路，不是兜底）",
      fallback_logs(l_h) == [], repr(fallback_logs(l_h)))

b_p, u_p, l_p = link_citations("（Haack 1978, p.227-232）", REFS_HAACK)
check("(c) 带页码尾巴照旧：`（\\parencite{Haack1978}，第227\\cndash 232页）`",
      b_p == "（\\parencite{Haack1978}，第227\\cndash 232页）", repr(b_p))
check("(c) 带页码尾巴也不进 unmatched、不留兜底日志",
      u_p == [] and fallback_logs(l_p) == [], f"un={u_p!r} logs={fallback_logs(l_p)!r}")

# ══ (d) 保留的兜底：有姓名证据才配，且必须留警示 ═══════════════════════════
print("\n── (d) 保留的兜底：有姓名证据时仍配，并留下可留档的警示 ──")

# d1 中文名写在方括号里：Xing, Fuyi [邢福义]
b1, u1, l1 = link_citations("（邢福义 1984）", [E("Xing1984", "Xing, Fuyi [邢福义]", "1984")])
check("(d) 兜底（方括号译名）：`（邢福义 1984）` → `\\parencite{Xing1984}`",
      b1 == "（\\parencite{Xing1984}）" and u1 == [], f"body={b1!r} un={u1!r}")
check("(d) 兜底（方括号译名）：留下了警示日志",
      len(fallback_logs(l1)) == 1 and "Xing1984" in fallback_logs(l1)[0]
      and "请核对" in fallback_logs(l1)[0], repr(fallback_logs(l1)))

# d2 西文「首字母 + 姓」：S. Blackburn 让 surname 变成 "S. Blackburn"
b2, u2, l2 = link_citations("（Blackburn 1984）", [E("SBlackburn1984", "S. Blackburn", "1984")])
check("(d) 兜底（首字母+姓）：`（Blackburn 1984）` → `\\parencite{SBlackburn1984}`",
      b2 == "（\\parencite{SBlackburn1984}）" and u2 == [], f"body={b2!r} un={u2!r}")
check("(d) 兜底（首字母+姓）：警示里写明证据是「引用名含条目姓」",
      len(fallback_logs(l2)) == 1 and "blackburn" in fallback_logs(l2)[0].lower(),
      repr(fallback_logs(l2)))

# d3 机构作者：Qinghua xuexiao (清华学校)
b3, u3, l3 = link_citations("（Qinghua xuexiao 1926, 28–31）",
                            [E("Qinghua1926", "Qinghua xuexiao (清华学校)", "1926")])
check("(d) 兜底（机构作者）：换成 `\\parencite{Qinghua1926}` 且页码归一",
      b3 == "（\\parencite{Qinghua1926}，第28\\cndash 31页）" and u3 == [],
      f"body={b3!r} un={u3!r}")
check("(d) 兜底（机构作者）：留下警示", len(fallback_logs(l3)) == 1, repr(fallback_logs(l3)))

# d4 引用名被正则多抓了一段（真实稿 Tuomo_Aho）：证据仍指向 Peter of Spain
b4, u4, l4 = link_citations(r"(edited in \textit{Peter of Spain 1992)",
                            [E("Peter1992", "Peter of Spain", "1992")])
check("(d) 兜底（引用名带杂字）：`\\parencite{Peter1992}`",
      b4 == "（\\parencite{Peter1992}）" and u4 == [], f"body={b4!r} un={u4!r}")
check("(d) 兜底（引用名带杂字）：留下警示", len(fallback_logs(l4)) == 1, repr(fallback_logs(l4)))

# d5 警示是「日志」的一部分，且日志末尾仍报总账（下游 build.py 会逐条 say 出去）
check("(d) 日志末尾仍报总账（`引用替换命中 …；未匹配 …`）",
      any(s.startswith("引用替换命中") for s in l1), repr(l1[-1:]))

# ══ (e) 真实稿回归 ═════════════════════════════════════════════════════════
print("\n── (e) 真实稿回归：10 Lei_Li 的 Williamson 引用进 unmatched，Hjortland2017 只剩 1 处 ──")

REAL = os.path.join(_REPO, "2026年第5期(90)", "10 Lei_Li", "original",
                    "李雷 当代逻辑论题鸟瞰.docx")
if not os.path.exists(REAL):
    skip("(e) 真实稿 10 Lei_Li", "语料不在本机")
else:
    from slcn.docxsource import parse_docx

    d = parse_docx(REAL)
    _b, _u, _l = link_citations(d.body, d.refs)
    check("(e) 真实稿：`（Williamson 2017；修订扩充版，2021）` 在 unmatched 里",
          any(x.startswith("（Williamson 2017") for x in _u), repr(_u))
    check("(e) 真实稿：`Hjortland2017` 在正文里只出现 **1** 次（本来正确的那处）",
          _b.count("Hjortland2017") == 1, f"{_b.count('Hjortland2017')}")
    check("(e) 真实稿：`Hjortland2017` 那处确实带着 `p.632` 的页码",
          "\\parencite{Hjortland2017}，第632页" in _b)
    check("(e) 真实稿：`（Williamson 2017…）` 没有留下兜底日志（被拒＝没配）",
          not any("Williamson" in s and "兜底到" in s for s in _l),
          repr([s for s in _l if "Williamson" in s]))

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
