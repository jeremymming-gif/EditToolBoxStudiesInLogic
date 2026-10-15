#!/usr/bin/env python3.14
r"""第 79 条（AI 结果并入 bib 的**合并记录漏记**）的判据测试。

两个缺陷都只该动「六、合并记录」的**文本**，**一个字都不该动**合并结果：
  · 缺陷 A：并入时新增的 `% 来源` 注释（本次 25/25 条都有）在记录里 **0 次提及**
    ——`来源` 没进注释登记表，于是产物里唯一「这批到底联没联网查过」的审计线索
    在留档里看不见。现在逐条记，形态与既有
    `- 说明：条目上方保留的 AI 注释：…` **完全一致**；
  · 缺陷 B：AI 新增/改动的字段与**它自己写的** `% ⚠ 待核实` 注释自相矛盾
    （`ChenBo2022`：注释写「volume…暂缺」，字段却新增了 `volume = {15}`）→ 现在给一条
    ⚠️ 警告。**只报不改**：字段、注释、四条硬保障、`n_merged` / `n_skipped` 口径全不动。

五组断言：
  ① 缺陷 A：合成小例（有 URL / 无 URL 两种都要记）+「来源」只记账、不动替换边界；
  ② 缺陷 B：点名 + 本次改动 → 警告；**两向反例**——改了字段但注释没点名 → 不出、
     注释点名了但本次没动它 → 不出；并钉住「点名」解析与 `bibreport.pointed_fields()`
     （第 77 条）**同源**、与 `check_spec()` 那处整行子串口径**不同**的地方
     （字段名只写在括号里不算点名；`booktitle` 不会被当成 `title`；大小写不敏感；
     不设 `bibcheck.REQUIRED` 白名单，`userf` 这类本条真实字段也认）；
  ③ 四条硬保障与 `n_merged` / `n_skipped` 口径**不受影响**（回归）；
  ④ **真实产物重放**（`~/Desktop/Bo_Chen/`，可用 `SLCN_BO_CHEN_DIR` 换位置；只读）：拿任务书
     `# AI 建议` 段当 replies 喂 `aibib.merge()`，再 `render_merge_section()`：
     ① 记录里出现 25 条「来源」说明（20 条带 http URL、5 条无 URL）；
     ② `ChenBo2022` 那条出现⚠️矛盾警告；③ 生成的 bib 与 `reference.bib`
     **逐字节相同**（证明改动只动了记录文本、没动合并结果）；
  ⑤ 替换边界没被「来源」污染：拿同一批 replies 把合并后的 `reference.bib` **再并一遍**，
     逐字节幂等、文件头注释不被切坏——这是「`来源` 只进记账表
     （`_RECORDED_COMMENT_KEYS`）、**不进**替换边界表（`_ANNOTATION_KEYS`）」的验收标准。

不写 out/、不联网、不跑 corpus.py / build.sh；外部真实产物**只读**。

用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_merge_record_provenance.py
"""

import hashlib
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import aibib  # noqa: E402
from slcn.bibparse import parse_entries  # noqa: E402

FAILS = []
SKIPS = []

#: 真实产物（`2024年第6期(79)/10 Bo_Chen` 那一轮真稿实跑的留档）——**只读**。
# 这是**外部**目录（用户在桌面上真跑 GUI 的产物），不在仓库里，所以不可能写成相对路径。
# 用环境变量 `SLCN_BO_CHEN_DIR` 指到别处；目录不在就整组 SKIP（见下面 `skip("④ …")`）。
BO_CHEN = os.environ.get("SLCN_BO_CHEN_DIR", os.path.expanduser("~/Desktop/Bo_Chen"))


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


# ── 语料（合成，不碰任何真实文件）───────────────────────────────────────────

ORIG = """\
% ── 合并测试用 ──

@article{Alpha2020,
  author  = {Doe, J.},
  title   = {A Study},
  journal = {Journal of Tests},
  year    = {2020}
}

@article{Beta2021,
  author  = {Roe, R.},
  title   = {B Study},
  journal = {Journal of Tests},
  volume  = {3},
  year    = {2021}
}

@book{Gamma1999,
  author    = {Poe, E.},
  title     = {Collected},
  publisher = {Some Press},
  year      = {1999}
}
"""


def fence(body):
    return f"```bibtex\n{body}\n```"


def blocks(text):
    return {e.key: text[e.start:e.end + 1] for e in parse_entries(text)[0]}


def keys(text):
    return [e.key for e in parse_entries(text)[0]]


def warnings_of(result, key):
    rec = result.by_key(key)
    return list(rec.warnings) if rec is not None else []


def contradictions(result, key):
    return [w for w in warnings_of(result, key) if "矛盾" in w]


def source_notes(record_text):
    """记录里逐条 `% 来源` 的说明行（形态必须与既有注释说明一致）。"""
    return [ln for ln in record_text.splitlines()
            if "  - 说明：条目上方保留的 AI 注释：`% 来源" in ln]


# ── ① 缺陷 A：`% 来源` 注释要进记录 ────────────────────────────────────────

print("── ① 缺陷 A：`% 来源` 注释逐条进「合并记录」 ──")

_SRC_URL = "% 来源：https://www.worldcat.org/title/12345 （WorldCat 书目页）"
_SRC_NONE = "% 来源：未查到（无法联网核实，按源稿原文保留）"
_SRC_LONG = ("% 来源：https://example.org/a/very/long/path/that/goes/on/and/on/for/a/while"
             "（很长的一行，用来钉住记录的截断口径）")
_ALPHA_NEW = """@article{Alpha2020,
  author  = {Doe, J.},
  title   = {A Study},
  journal = {Journal of Tests},
  year    = {2020}
}"""
_BETA_NEW = """@article{Beta2021,
  author  = {Roe, R.},
  title   = {B Study},
  journal = {Journal of Tests},
  volume  = {3},
  year    = {2021}
}"""

res_a = aibib.merge(ORIG, {
    "Alpha2020": fence(_SRC_URL + "\n" + _ALPHA_NEW),
    "Beta2021": fence(_SRC_NONE + "\n" + _BETA_NEW),
})
sec_a = aibib.render_merge_section(res_a)
_notes_a = source_notes(sec_a)
check("① `% 来源` 注释进记录（形态 = 既有 `- 说明：条目上方保留的 AI 注释：…`）",
      len(_notes_a) == 2 and all(ln.startswith("  - 说明：条目上方保留的 AI 注释：`")
                                 and ln.endswith("`") for ln in _notes_a),
      f"{len(_notes_a)} 条")
check("① 带 URL 的那条原文进记录（56 字，未触到 `_clip` 70 字上限，一字不改）",
      any(_SRC_URL in ln for ln in _notes_a), repr(_notes_a[0]) if _notes_a else "")
check("① **没有 URL** 的那条也要如实记（写的是「未查到…」）",
      any(_SRC_NONE in ln for ln in _notes_a), "")
check("① 超过 70 字的 `来源` 行按既有口径截断（结尾 `…` + 反引号），不另造格式",
      aibib._clip(_SRC_LONG, 70) in aibib.render_merge_section(aibib.merge(
          ORIG, {"Alpha2020": fence(_SRC_LONG + "\n" + _ALPHA_NEW)}))
      and aibib.render_merge_section(aibib.merge(
          ORIG, {"Alpha2020": fence(_SRC_LONG + "\n" + _ALPHA_NEW)})).count("`% 来源：https:") == 1, "")
check("① 记录里 `来源` 说明的条数 = 实际并入条数（不是只记一条）",
      len(_notes_a) == res_a.n_merged == 2, f"{len(_notes_a)}/{res_a.n_merged}")
# 反例：没有 `% 来源` 注释的条目**不许**凭空多出这条说明
res_a2 = aibib.merge(ORIG, {"Alpha2020": fence(_ALPHA_NEW)})
check("① 反例：AI 没写 `% 来源` → 记录里没有来源说明（不无中生有）",
      source_notes(aibib.render_merge_section(res_a2)) == [], "")
check("① 记账表 = 替换边界表 + 来源（两张表分开，理由见 `_ANNOTATION_KEYS` 旁的注释）",
      aibib._RECORDED_COMMENT_KEYS == tuple(aibib._ANNOTATION_KEYS) + ("来源",)
      and "来源" not in aibib._ANNOTATION_KEYS, f"{aibib._RECORDED_COMMENT_KEYS}")


# ── ② 缺陷 B：`% ⚠ 待核实` 点名了、本次却动了它 → 警告 ─────────────────────

print("\n── ② 缺陷 B：待核实注释点名的字段被本次合并改动 → ⚠️ 矛盾警告 ──")

_GAMMA_PENDING = "% ⚠ 待核实：edition（官网查不到版次）"
_GAMMA_NEW = """@book{Gamma1999,
  author    = {Poe, E.},
  title     = {Collected},
  publisher = {Some Press},
  edition   = {2},
  year      = {1999}
}"""
res_b = aibib.merge(ORIG, {"Gamma1999": fence(_GAMMA_PENDING + "\n" + _GAMMA_NEW)})
_sec_b = aibib.render_merge_section(res_b)
check("② 新增了被点名的字段 → 出一条「矛盾」警告",
      len(contradictions(res_b, "Gamma1999")) == 1
      and "`edition`" in contradictions(res_b, "Gamma1999")[0],
      contradictions(res_b, "Gamma1999")[0] if contradictions(res_b, "Gamma1999") else "")
check("② 警告措辞 = 「AI 本次改动了 X…两者矛盾，请人工核。」（与要求一致）",
      contradictions(res_b, "Gamma1999") == [
          "AI 本次改动了 `edition`，但同一条上方的待核实注释说它查不到——两者矛盾，请人工核。"],
      "")
check("② 渲染成 `  - ⚠️ …` 一行（与既有 warning 同一形态）",
      "  - ⚠️ AI 本次改动了 `edition`，但同一条上方的待核实注释说它查不到——两者矛盾，请人工核。"
      in _sec_b, "")
check("② 只报不改：字段值 / 注释一个字都没动",
      "edition   = {2}" in res_b.text and _GAMMA_PENDING in res_b.text, "")

# 矛盾警告与「仍缺必填字段」同时出现时，矛盾排**前面**（编辑先看见「这条自相矛盾」）
_GAMMA_EDITION_NO_PUB = """@book{Gamma1999,
  author  = {Poe, E.},
  title   = {Collected},
  edition = {2},
  year    = {1999}
}"""
res_b2 = aibib.merge(ORIG, {"Gamma1999": fence(_GAMMA_PENDING + "\n" + _GAMMA_EDITION_NO_PUB)})
_w2 = warnings_of(res_b2, "Gamma1999")
check("② 同一条上「矛盾」警告排在「仍缺必填字段」警告**前面**"
      "（编辑先看见「这条自相矛盾」）",
      len(_w2) >= 2 and "矛盾" in _w2[0]
      and all(w.startswith("仍缺必填字段") for w in _w2[1:]), f"{_w2}")
check("② 缺字段警告的文案一字未改（口径不变）",
      any(w.startswith("仍缺必填字段 `publisher`（已按用户决定并入，请人工补）") for w in _w2), "")

# 反例 1：字段被改动，但注释**没点名**它
_ALPHA_PAGES = "% ⚠ 待核实：pages（官网未给出页码）"
_ALPHA_VOL = """@article{Alpha2020,
  author  = {Doe, J.},
  title   = {A Study},
  journal = {Journal of Tests},
  volume  = {12},
  year    = {2020}
}"""
res_c1 = aibib.merge(ORIG, {"Alpha2020": fence(_ALPHA_PAGES + "\n" + _ALPHA_VOL)})
check("② 反例①：改了 `volume`，注释只点名 `pages` → **不许**出矛盾警告",
      res_c1.n_merged == 1 and contradictions(res_c1, "Alpha2020") == []
      and "新增 `volume`" in aibib.render_merge_section(res_c1), "")
check("② 反例① 的注释说明照旧在（不是把说明整条吞掉）",
      "`% ⚠ 待核实：pages（官网未给出页码）`"
      in aibib.render_merge_section(res_c1), "")

# 反例 2：注释点名了该字段，但本次**没动**它
_BETA_PENDING = "% ⚠ 待核实：volume（查不到卷号，暂按源稿）"
_BETA_TITLE = """@article{Beta2021,
  author  = {Roe, R.},
  title   = {B Study（修订题名）},
  journal = {Journal of Tests},
  volume  = {3},
  year    = {2021}
}"""
res_c2 = aibib.merge(ORIG, {"Beta2021": fence(_BETA_PENDING + "\n" + _BETA_TITLE)})
check("② 反例②：注释点名 `volume`，但本次只改了 `title` → **不许**出矛盾警告",
      res_c2.n_merged == 1 and contradictions(res_c2, "Beta2021") == []
      and res_c2.by_key("Beta2021").diff.touched == ["title"], "")
check("② 反例② 的注释说明照旧在",
      "`% ⚠ 待核实：volume（查不到卷号，暂按源稿）`"
      in aibib.render_merge_section(res_c2), "")

# 反例 3：字段名只写在**括号里**
# 口径对照（如实说明差异）：本解析与 `bibreport.pointed_fields()`（第 77 条）**一致**——
# 括号里的字段名不算点名；但与 `bibreport.check_spec()` 里「缺字段降档」那处的
# `iss.detail in c`（**整行子串**）口径**不同**：那处连括号里的都算。这里故意更窄。
_ALPHA_PAREN = "% ⚠ 待核实：查不到（volume 未给出）"
res_c3 = aibib.merge(ORIG, {"Alpha2020": fence(_ALPHA_PAREN + "\n" + _ALPHA_VOL)})
check("② 反例③：字段名只出现在括号里 → 本解析**不算点名**，不出警告"
      "（与 `pointed_fields()` 一致；比 `check_spec()` 那个整行子串口径窄——**故意**）",
      aibib.pending_review_fields([_ALPHA_PAREN]) == []
      and contradictions(res_c3, "Alpha2020") == [], "")

# 反例 4：`来源` / `类型已改` 不表示「查不到」，不参与点名
res_c4 = aibib.merge(ORIG, {"Alpha2020": fence(
    "% 来源：WorldCat 书目页（volume 未给出）\n"
    "% 类型已改：@misc → @article（源条目判错类型）\n" + _ALPHA_VOL)})
check("② 反例④：`来源` / `类型已改` 里出现字段名 → **不算**「待核实」点名，不出警告",
      aibib.pending_review_fields(
          ["% 来源：WorldCat 书目页（volume 未给出）",
           "% 类型已改：@misc → @article（源条目判错类型）"]) == []
      and contradictions(res_c4, "Alpha2020") == [], "")

# 与 `bibreport.pointed_fields()`（第 77 条）的**同名区**口径：`booktitle` 不许被当成 `title`
check("② 点名区口径与 `bibreport.pointed_fields()` 同源：`booktitle` 不会被误认成 `title`",
      aibib.pending_review_fields(["% ⚠ 待核实：booktitle（未查到官方书目页）"]) == ["booktitle"]
      and aibib.pending_review_fields(["% ⚠ 待核实：Volume（大写也要认）"]) == ["volume"],
      "")
# 差异（有意）：不设 `bibcheck.REQUIRED` 白名单——`userf` 不在那张表里，但它确实是本条的字段
res_c9 = aibib.merge("""\
@book{Eps2000,
  author    = {Poe, E.},
  title     = {Y},
  publisher = {P},
  year      = {2000},
  userf     = {cn}
}
""", {"Eps2000": fence("% ⚠ 待核实：userf（语种标记不确定）\n" + """\
@book{Eps2000,
  author    = {Poe, E.},
  title     = {Y},
  publisher = {P},
  year      = {2000},
  userf     = {en}
}""")})
check("② 差异（有意）：点名 `userf`（**不在** `bibcheck.REQUIRED` 里）也认——"
      "本解析不设必备字段白名单，与 `pointed_fields()` 只认必备字段表不同",
      res_c9.n_merged == 1 and len(contradictions(res_c9, "Eps2000")) == 1
      and "`userf`" in contradictions(res_c9, "Eps2000")[0], "")

# 一条注释点多个字段：点名区按 `/`、`、`、空格切
res_c5 = aibib.merge(ORIG, {"Gamma1999": fence(
    "% ⚠ 待核实：volume / edition（两说，未核实）\n" + _GAMMA_NEW)})
check("② 一条注释点多个字段：只对**本次真的动了**的那几个出警告（volume 没动 → 只报 edition）",
      aibib.pending_review_fields(["% ⚠ 待核实：volume / edition（两说）"]) == ["volume", "edition"]
      and contradictions(res_c5, "Gamma1999") == [
          "AI 本次改动了 `edition`，但同一条上方的待核实注释说它查不到——两者矛盾，请人工核。"],
      f"{contradictions(res_c5, 'Gamma1999')}")
# 点名的多个字段**都**动了 → 逐字段各一条（`diff.touched` 顺序：新增在前，改动随后）
_ALPHA_TWO = """@article{Alpha2020,
  author  = {Doe, J.},
  title   = {A Study},
  journal = {Journal of Tests},
  volume  = {12},
  number  = {4},
  year    = {2020}
}"""
res_c6 = aibib.merge(ORIG, {"Alpha2020": fence(
    "% ⚠ 待核实：number / volume（两个都查不到）\n" + _ALPHA_TWO)})
check("② 点名的多个字段都动了 → 逐字段各一条警告（顺序稳定：新增按条目字段顺序）",
      contradictions(res_c6, "Alpha2020") == [
          "AI 本次改动了 `volume`，但同一条上方的待核实注释说它查不到——两者矛盾，请人工核。",
          "AI 本次改动了 `number`，但同一条上方的待核实注释说它查不到——两者矛盾，请人工核。"],
      f"{contradictions(res_c6, 'Alpha2020')}")

# 反例 5：原件的注释被 AI 换掉、AI 这条自己没写「待核实」→ 不出（口径见函数 docstring）
_ORIG_PENDING = ORIG.replace("@book{Gamma1999,",
                             "% ⚠ 待核实：edition（官网查不到版次）\n@book{Gamma1999,")
res_c7 = aibib.merge(_ORIG_PENDING, {"Gamma1999": fence(_GAMMA_NEW)})
check("② 反例⑤：原件的「待核实」注释没被 AI 重复、合并后条目上方没有这行 → 不出警告"
      "（警告说的是「**同一条上方**的注释」，产物里不存在这行注释）",
      res_c7.n_merged == 1 and contradictions(res_c7, "Gamma1999") == []
      and "% 待核实" not in res_c7.text, "")

# 反例 6：单纯字段顺序重排（值没变）不算「动过值」
res_c8 = aibib.merge("""\
@book{Delta1998,
  address   = {Oxford},
  author    = {Poe, E.},
  title     = {X},
  publisher = {Some Press},
  year      = {1998}
}
""", {"Delta1998": fence("% ⚠ 待核实：address（未核实出版地）\n" + """\
@book{Delta1998,
  author    = {Poe, E.},
  title     = {X},
  address   = {Oxford},
  publisher = {Some Press},
  year      = {1998}
}""")})
check("② 反例⑥：只是字段顺序重排（值一字未变）→ 不算「改动了 address」，不出警告",
      res_c8.n_merged == 1 and contradictions(res_c8, "Delta1998") == []
      and res_c8.by_key("Delta1998").diff.reordered != [], "")


# ── ③ 四条硬保障与 n_merged / n_skipped 口径不变 ──────────────────────────

print("\n── ③ 四条硬保障 / `n_merged` / `n_skipped`：本次改动**不受影响** ──")

res_g = aibib.merge(ORIG, {
    "Alpha2020": fence(_SRC_URL + "\n" + _ALPHA_NEW),
    "Beta2021": fence(_BETA_PENDING + "\n" + _BETA_TITLE),
})
check("③ 键的集合与顺序与原文件逐项一致（不增不删）", keys(res_g.text) == keys(ORIG),
      f"{keys(res_g.text)}")
check("③ `verify_key_sets()` 仍然返回空串", aibib.verify_key_sets(ORIG, res_g.text) == "")
check("③ AI **没返回**的条目逐字节不动（Gamma1999）",
      blocks(ORIG)["Gamma1999"] == blocks(res_g.text)["Gamma1999"], "")
check("③ 文件头注释没被顺手抹掉", res_g.text.startswith("% ── 合并测试用 ──"), "")
check("③ `n_merged` / `n_skipped` 口径不变（2 并入 / 1 未返回）",
      (res_g.n_merged, res_g.n_skipped) == (2, 1), f"{res_g.n_merged}/{res_g.n_skipped}")
check("③ 未返回的那条仍记为 skipped + 中文原因",
      res_g.by_key("Gamma1999").status == "skipped"
      and "未返回" in res_g.by_key("Gamma1999").reason, "")

_res_key = aibib.merge(ORIG, {"Alpha2020": fence(_ALPHA_NEW.replace("{Alpha2020,", "{Alpha2020a,"))})
check("③ 保障①：改 key → 整条跳过、原字节不动（有「来源」注释也一样）",
      _res_key.n_merged == 0 and _res_key.text == ORIG
      and "不一致" in _res_key.by_key("Alpha2020").reason, "")
_res_many = aibib.merge(ORIG, {"Alpha2020": fence(_ALPHA_NEW + "\n\n" + _GAMMA_NEW)})
check("③ 保障②：回复里不是恰好一条 → 跳过",
      _res_many.n_merged == 0 and _res_many.text == ORIG, "")
_res_empty = aibib.merge(ORIG, {"Alpha2020": fence("@article{Alpha2020,\n}")})
check("③ 保障②：一个字段都没有 → 跳过（不许把整条清空）",
      _res_empty.n_merged == 0 and _res_empty.text == ORIG, "")
_res_new = aibib.merge(ORIG, {"Zeta2099": fence("@article{Zeta2099,\n  title = {X}\n}")})
check("③ 保障③：AI 返回原件里没有的键 → 不新增条目，只记一笔",
      _res_new.n_merged == 0 and _res_new.text == ORIG
      and "不新增条目" in _res_new.by_key("Zeta2099").reason, "")
check("③ 保障④：`% 来源` 进记录**不会**让 diff 多出字段（记录只多说明行）",
      res_g.by_key("Alpha2020").diff.added == []
      and res_g.by_key("Alpha2020").diff.touched == [], "")


# ── ④ 真实产物重放（只读）──────────────────────────────────────────────

print("\n── ④ 真实产物重放：Bo_Chen 那一轮的《文献核实.md》→ merge() → 记录 ──")

AI_HEAD = "# AI 建议"
REC_HEAD = "## 六、合并记录"


def replies_from_taskbook(md_text):
    """把任务书 `# AI 建议` 段（到「## 六、合并记录」为止）切成 `merge()` 要的 replies。"""
    start = md_text.index(AI_HEAD)
    end = md_text.index(REC_HEAD, start)
    sec = md_text[start:end]
    heads = list(re.finditer(r"^## `([^`]+)`\s*$", sec, re.M))
    out = {}
    for i, m in enumerate(heads):
        nxt = heads[i + 1].start() if i + 1 < len(heads) else len(sec)
        out[m.group(1)] = sec[m.end():nxt].strip()
    return out


_md_p = os.path.join(BO_CHEN, "Bo_Chen_文献核实.md")
_bib_p = os.path.join(BO_CHEN, "reference.bib")
_pre_p = os.path.join(BO_CHEN, "reference.bib.ai前")
if not (os.path.isfile(_md_p) and os.path.isfile(_bib_p) and os.path.isfile(_pre_p)):
    skip("④ 真实产物重放", f"跑不了：{BO_CHEN} 里缺 "
                           "Bo_Chen_文献核实.md / reference.bib / reference.bib.ai前")
else:
    _md = open(_md_p, encoding="utf-8").read()
    _pre = open(_pre_p, encoding="utf-8").read()
    _want_bytes = open(_bib_p, "rb").read()
    _want = _want_bytes.decode("utf-8")
    _replies = replies_from_taskbook(_md)
    _res = aibib.merge(_pre, _replies)
    _sec = aibib.render_merge_section(_res)

    N = len(_replies)
    check("④ 任务书 `# AI 建议` 段切出的 replies 数 = 并入数（口径不变：全并入 / 0 跳过）",
          N > 0 and (_res.n_merged, _res.n_skipped) == (N, 0),
          f"replies={N} → 并入 {_res.n_merged} / 跳过 {_res.n_skipped}")

    # ⚠️ 这一组**不许写死 25 / 20 / 5 这类绝对数字**：它读的是
    # `~/Desktop/Bo_Chen/`（用户真跑 GUI 的产物目录；可用 SLCN_BO_CHEN_DIR 换），
    # 用户**再跑一次**就换了一批数据，写死会让测试无端变红。
    # 断言一律改成「记录与产物之间的数量关系」，判据照样有牙齿。
    # （2026-10-15 实测踩到：第一次交付时写死 25，用户第二次跑 GUI 后 9 项全红。）

    def bib_source_map(bib_text):
        """bib 里每条上方的 `% 来源：…` → {key: [来源行…]}（只看紧挨条目上方的那几行注释）。"""
        out, pending = {}, []
        for ln in bib_text.splitlines():
            s = ln.strip()
            if s.startswith("%"):
                pending.append(s)
                continue
            m = re.match(r"@\w+\{([^,]+),", s)
            if m:
                src = [x for x in pending if x.startswith("% 来源")]
                if src:
                    out[m.group(1)] = src
                pending = []
            elif s:
                pending = []
        return out

    def pending_named(bib_text):
        """bib 里每条上方 `待核实：<字段…>（原因）` 点名的字段 → {key: {字段…}}。"""
        out, pending = {}, []
        for ln in bib_text.splitlines():
            s = ln.strip()
            if s.startswith("%"):
                pending.append(s)
                continue
            m = re.match(r"@\w+\{([^,]+),", s)
            if m:
                named = set()
                for x in pending:
                    mm = re.search(r"待核实[:：]\s*([^（(]*)", x)
                    if mm:
                        named |= {t.strip().lower() for t in re.split(r"[/、,，\s]+", mm.group(1)) if t.strip()}
                if named:
                    out[m.group(1)] = named
                pending = []
            elif s:
                pending = []
        return out

    # ① 来源标注：合并前 0 行 → 合并后逐条进记录
    _src_lines = source_notes(_sec)
    _bib_src = bib_source_map(_want)
    check("④ 缺陷 A-1/A-2：合并前 `% 来源` 0 行 → 合并后每条都进了记录（条数相等且 > 0）",
          len(_bib_src) == len(_src_lines) > 0
          and not any(ln.startswith("% 来源") for ln in _pre.splitlines()),
          f"bib {len(_bib_src)} 条 / 记录 {len(_src_lines)} 条")
    _url_n = sum("http" in ln for ln in _src_lines)
    check("④ 缺陷 A-3：带 URL 的条数与 bib 里带 URL 的 `% 来源` 一致（无 URL 的也如实记）",
          _url_n == sum(1 for v in _bib_src.values() if any("http" in x for x in v))
          and _url_n > 0 and _url_n < len(_src_lines),
          f"带 URL {_url_n} / 无 URL {len(_src_lines) - _url_n}")
    _nourl_keys = sorted(
        re.match(r"- `([^`]+)`", b).group(1)
        for b in re.split(r"\n(?=- `)", _sec)
        if re.match(r"- `([^`]+)`", b)
        and any("% 来源" in ln and "http" not in ln for ln in source_notes(b)))
    _bib_nourl = sorted(k for k, v in _bib_src.items() if all("http" not in x for x in v))
    check("④ 缺陷 A-4：记录里「无 URL 的那几条」与 bib 里无 URL 的 `% 来源` **一一对应**",
          _nourl_keys == _bib_nourl and len(_bib_nourl) > 0,
          f"记录 {_nourl_keys} vs bib {_bib_nourl}")

    # ② 矛盾警告：**由产物独立算出应有哪几条**，再与记录比对
    _named = pending_named(_want)
    _expect_con = {}
    for r in _res.records:
        d = r.diff
        # ⚠️ 用 `diff.touched`（第 79 条新加的裸字段名清单）。
        # 不要用 `diff.changed` —— 那里面是「author: A → B」这种**整句**，不是字段名，
        # 拿它去跟注释点名的字段求交集只会得到空集（本条第一次实现就栽在这里）。
        touched = {x.lower() for x in (getattr(d, "touched", []) or [])}
        hit = sorted((_named.get(r.key, set()) & touched))
        if hit:
            _expect_con[r.key] = hit
    _con_keys = sorted(r.key for r in _res.records if any("矛盾" in w for w in r.warnings))
    # ⚠️ 这一条**允许两边都为空**：用户再跑一次 GUI，AI 可能一个字段都没改，
    # 那就「本就不该有矛盾警告」——空集也是正确结果（合成夹具的 ①②③ 组另有强断言）。
    check("④ 缺陷 B-1/B-4：记录里的矛盾警告**正好**是「注释点名的字段 ∩ 本次真动过的字段」",
          _con_keys == sorted(_expect_con),
          f"记录 {_con_keys} vs 独立算 {sorted(_expect_con)}")
    check("④ 缺陷 B-2：每条矛盾警告都渲染在**它自己那个键**名下，且逐字段列出",
          all(re.search(r"- `" + re.escape(k) + r"`[^\n]*\n(  - [^\n]*\n)*"
                        r"  - ⚠️ AI 本次改动了 `[^`]+`，[^\n]*两者矛盾，请人工核。", _sec)
              for k in _con_keys),
          f"{_con_keys}")
    check("④ 缺陷 B-3：这些条目的 diff 仍然只有「新增/改值」两种口径（**只报不改**）",
          all((getattr(_res.by_key(k).diff, "added", []) or getattr(_res.by_key(k).diff, "changed", []))
              for k in _con_keys), "")
    check("④ 缺陷 B-5/B-6：点名的字段**没被动**的条目一律不许出警告（反例）",
          all(contradictions(_res, k) == []
              for k in _named if k not in _expect_con),
          f"不该出的：{[k for k in _named if k not in _expect_con]}")

    # ③ 逐字节相同
    check("④ 缺陷 A/B：合并结果与 `reference.bib` **逐字节相同**"
          "（改动只动了记录文本、没动合并结果）",
          _res.text == _want and _res.text.encode("utf-8") == _want_bytes,
          "sha256=" + hashlib.sha256(_res.text.encode("utf-8")).hexdigest()[:12])
    check("④ 记录里 `来源` 说明**没有**替代掉原有的「待核实 / 类型已改」说明",
          _sec.count("条目上方保留的 AI 注释：`% ⚠ 待核实") > 0,
          "")
    check("④ 记录里追加了 `来源` 说明后，`本次：**并入 25 条** / 跳过 0 条` 一字未变",
          "- 本次：**并入 25 条** / 跳过 0 条（逐条见下；共 25 个键）" in _sec, "")

    # ── ⑤ 替换边界没被「来源」污染（`来源` 只进记账表）──────────────────
    print("\n── ⑤ `来源` 只进记账表：同一批 replies 把 `reference.bib` **再并一遍** ──")
    _res2 = aibib.merge(_want, _replies)
    check("⑤ 再并一遍逐字节幂等（`% 来源` 没有被叠成两份、也没丢）",
          _res2.text == _want and _res2.text.encode("utf-8") == _want_bytes, "")
    check("⑤ 再并一遍时文件头注释原封不动（`% ── …` / `% 由 Word …`）",
          _res2.text.startswith("\n% ── 由文末参考文献区生成 ──\n\n"
                                "% 由 Word 参考文献自动生成，请核对类型与字段。\n"),
          repr(_res2.text[:40]))
    # 反事实探针：把「来源」也塞进**替换边界**表，会切坏文件头、也不再幂等
    # ——这张表分开是必要的（探针只改内存里的常量，随后 finally 还原）。
    _saved_keys = aibib._ANNOTATION_KEYS
    aibib._ANNOTATION_KEYS = ("待核实", "类型已改", "来源")
    try:
        _naive = aibib.merge(_want, _replies).text
    finally:
        aibib._ANNOTATION_KEYS = _saved_keys
    check("⑤ 反事实：若把「来源」并进**替换边界**表（`_ANNOTATION_KEYS`），"
          "同一份 bib 再并一遍就不再逐字节相同、文件头注释被切坏",
          _naive != _want and "% ── 由文末参考文献区生成 ──" not in _naive,
          repr(_naive[:24]))
    check("⑤ 探针已还原：替换边界表仍是 `待核实` / `类型已改`（不含 `来源`）",
          aibib._ANNOTATION_KEYS == ("待核实", "类型已改")
          and aibib._RECORDED_COMMENT_KEYS == ("待核实", "类型已改", "来源"), "")


print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
