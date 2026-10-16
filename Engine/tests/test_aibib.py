#!/usr/bin/env python3.14
r"""`slcn/aibib.py`（把 AI 返回的条目**并入** reference.bib）的判据测试。

五组断言：
  ① 取条目：markdown 围栏（带/不带 `bibtex` 标记）、没有围栏、`% ⚠ 待核实` 注释要跟着走；
     垃圾回复 / 多条 / 改了 key / 空回复 一律取不出来（返回 None）。
     ①b（GAP 1，2026-10-15 用户实测）说明写在**围栏外面**（条目后面或前面）也要收，
     搬到条目上方；上下两处同名去重；与条目隔着散文的 `%` 行不吞；注释文本一字不改。
  ② 合并的四条硬保障：key 不可改、恰好一条、不增删条目（键集合与顺序不变）、
     **AI 没返回的条目逐字节不动**；外加「一个字段都没有不许清空」「重复键跳过」
     「原件里没有的键不新增」；围栏外的注释并进文件后要在条目**上方**。
  ③ 字段级 diff 与 author/editor 倒装：改动逐字段记下来、中文姓名不动、幂等；
     GAP 3 字段顺序按任务书第三节重排（未知字段留末尾、只动顺序、幂等、记进 diff）。
  ④ `convert.apply_ai_merge()` 落盘：回滚副本 `reference.bib.ai前`、`<stem>_回读校验报告.md`、
     任务书「六、合并记录」、**0 条并入时一个字都不写**、半截返回不并入但记原因。
  ⑤ 端到端（真实 CLI + 本地 mock 的 replay 夹具）：`convert.py --verify-refs` 配 LLM 跑
     `2026年第5期(90)/01 Jingru_Kou/定稿.docx`，核对上面每一项的**实际产物**——
     含 GAP 1（`Dang2022` 的围栏外注释 → 报告降为提示）与 GAP 3（`Kaplan1989` 重排）。


不写 out/、不联网（mock 只绑 127.0.0.1）、不跑 corpus.py / build.sh。

用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_aibib.py
"""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_MACTOOLBOX = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import aibib, bibnorm, refmark  # noqa: E402
from slcn.bibparse import parse_entries  # noqa: E402

import convert  # noqa: E402

FAILS = []
SKIPS = []

# 夹具目录 2026-10-15 phase 2c 从 `tools/gui_fixtures/` 改名成 `tools/fixtures/`：
# 人工点击时代的「回读校验」夹具已删（无任何测试引用），这里只留 mock 回放用的那一个。
FAKE = "fixtures/merge_replies.json"
MANUSCRIPT = os.path.join(_MACTOOLBOX, "..", "2026年第5期(90)", "01 Jingru_Kou", "定稿.docx")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


# ── 语料 ──────────────────────────────────────────────────────────────────

ORIG = """\
% ── 由脚注 / 文末编号清单中的文献条目生成 ──

@article{Burge1984,
  author  = {T. Burge},
  title   = {Epistemic Paradox},
  journal = {The Journal of Philosophy},
  volume  = {81},
  number  = {1},
  pages   = {5--29},
  year    = {1984},
  doi     = {10.1000/x}
}

@book{Almog1989,
  title     = {Themes from Kaplan},
  author    = {Joseph Almog and John Perry and Howard Wettstein},
  publisher = {Oxford University Press},
  address   = {Oxford},
  year      = {1989}
}

@book{A2014,
  author    = {阿斯海姆（著），张建军、万林（译）},
  title     = {指称与意向性},
  publisher = {南京大学出版社},
  address   = {南京},
  year      = {2014},
  userf     = {cn}
}
"""


def fence(body: str, info: str = "bibtex") -> str:
    return f"```{info}\n{body}\n```"


def entry(text: str) -> str:
    return text[text.index("@"):].rstrip()


BURGE_OK = """@article{Burge1984,
  author  = {T. Burge},
  title   = {Epistemic Paradox},
  journal = {The Journal of Philosophy},
  volume  = {81},
  number  = {1},
  pages   = {5--29},
  year    = {1984}
}"""
BURGE_RENAMED = BURGE_OK.replace("{Burge1984,", "{Burge1984a,")
ALMOG_OK = """@book{Almog1989,
  title     = {Themes from Kaplan},
  author    = {J. Almog and J. Perry and H. Wettstein},
  publisher = {Oxford University Press},
  address   = {Oxford},
  year      = {1989}
}"""


# ── ① 从回复里取条目 ─────────────────────────────────────────────────────

print("── ① parse_ai_reply：从一条 AI 回复里取那一条条目 ──")
_t = aibib.parse_ai_reply("Burge1984", fence(BURGE_OK))
check("① 带 ```bibtex 围栏 → 取到条目",
      _t is not None and _t.startswith("@article{Burge1984,") and _t.rstrip().endswith("}"), repr(_t)[:80])
check("① 回复里的说明文字不进条目（前后言都丢掉）",
      aibib.parse_ai_reply("Burge1984", "好的，这是修改后的条目：\n\n" + fence(BURGE_OK) + "\n\n以上。")
      == _t, "")
check("① 围栏没有语言标记、或写成 ```bib 也能取",
      aibib.parse_ai_reply("Burge1984", fence(BURGE_OK, "")) == _t
      and aibib.parse_ai_reply("Burge1984", fence(BURGE_OK, "bib")) == _t)
check("① 连围栏都没有（模型直接吐 bibtex）也能取，且只取条目本身",
      aibib.parse_ai_reply("Burge1984", "如下：\n" + BURGE_OK + "\n希望有帮助。") == _t)
_pend = aibib.parse_ai_reply(
    "Almog1989", fence("% ⚠ 待核实：number（官网只有在线优先版）\n" + ALMOG_OK))
check("① `% ⚠ 待核实` 注释**跟着条目一起**取出来（任务书第三节第 6 条）",
      _pend is not None and _pend.startswith("% ⚠ 待核实：number"), repr(_pend)[:60])
check("① 垃圾回复 → None", aibib.parse_ai_reply("Burge1984", "抱歉，我查不到，请人工核对。") is None)
check("① 空回复 / None → None",
      aibib.parse_ai_reply("Burge1984", "") is None and aibib.parse_ai_reply("Burge1984", None) is None)
check("① 回了一整份 bib（多条）→ None（分不清哪条是哪条）",
      aibib.parse_ai_reply("Burge1984", fence(BURGE_OK + "\n\n" + ALMOG_OK)) is None)
check("① 改了 key → None（正文引用会全部悬空）",
      aibib.parse_ai_reply("Burge1984", fence(BURGE_RENAMED)) is None)
check("① 半截条目（花括号不配平）→ None",
      aibib.parse_ai_reply("Burge1984", fence('@article{Burge1984,\n  title = {没写完')) is None)
_r, _why = aibib.extract_reply_entry("Burge1984", fence(BURGE_RENAMED))
check("① 取不出来时给的是**中文原因**（要写进合并记录）",
      _r is None and "不一致" in _why and "Burge1984a" in _why, _why[:60])

# ── GAP 1（2026-10-15 用户实测）：说明写在**围栏外面**会被丢掉 ──────────────
# 真实回复形状（`Dang2022`）：```bibtex 里只有条目，`% ⚠ 待核实` 那行在收尾围栏**之后**。
# 旧实现只取围栏内那一段，说明当场丢失 → 回读校验把缺失的必填字段报成**问题**。
print("\n── ①b 注释在围栏外面 / 上下两处都有：都要收齐、搬到条目上方、不吞散文 ──")
_OUT_NOTE = "% ⚠ 待核实：volume（官网只有在线优先版，卷号两说）"
_OUT_TYPE = "% 类型已改：@misc → @article（源条目判错类型）"
_t_out = aibib.parse_ai_reply("Burge1984", fence(BURGE_OK) + "\n" + _OUT_NOTE)
check("①b 围栏外（条目后面）的 `% ⚠ 待核实` 被收下，并**搬到条目上方**",
      _t_out is not None and _t_out.startswith(_OUT_NOTE) and _t_out.endswith(BURGE_OK),
      repr(_t_out)[:70])
check("①b 注释文本**一字不改**（只挪位置）",
      _t_out is not None and _OUT_NOTE in _t_out, "")
_t_typ = aibib.parse_ai_reply("Burge1984", fence(BURGE_OK) + "\n" + _OUT_TYPE + "\n" + _OUT_NOTE)
check("①b 围栏外的 `% 类型已改` 也收，且两行顺序保持不变",
      _t_typ is not None and _t_typ.startswith(_OUT_TYPE + "\n" + _OUT_NOTE), repr(_t_typ)[:80])
check("①b 围栏内 + 围栏外各写一份**同一条**注释 → 去重，只留一份",
      aibib.parse_ai_reply(
          "Burge1984", fence(_OUT_NOTE + "\n" + BURGE_OK) + "\n" + _OUT_NOTE
      ).count("待核实") == 1, "")
check("①b 围栏外的注释**前面**也认（写在围栏开头上方）",
      aibib.parse_ai_reply("Burge1984", _OUT_NOTE + "\n" + fence(BURGE_OK)) == _t_out, "")
check("①b 与条目隔着散文的 `%` 行**不吞**（任务书正文里的 `%` 例子不会被当成这条的注释）",
      aibib.parse_ai_reply(
          "Burge1984", fence(BURGE_OK) + "\n\n说明如下：\n% 这不是这条的注释\n"
      ) == _t, "")
check("①b 围栏外的普通散文不会被当成条目内容",
      aibib.parse_ai_reply("Burge1984", fence(BURGE_OK) + "\n以上，请核对。\n" + _OUT_NOTE) == _t, "")
check("①b 任务书第三节现在**明确要求**注释写在代码块内部、条目上方（规范与解析器一起改）",
      "代码块内部" in refmark.FORMAT_MD and "条目上方" in refmark.FORMAT_MD, "")


# ── ② 四条硬保障 ────────────────────────────────────────────────────────

print("\n── ② merge：四条硬保障（违反就跳过、保留原字节）──")


def blocks(text):
    return {e.key: text[e.start:e.end + 1] for e in parse_entries(text)[0]}


def keys(text):
    return [e.key for e in parse_entries(text)[0]]


res = aibib.merge(ORIG, {"Burge1984": fence(BURGE_OK), "Almog1989": fence(ALMOG_OK)})
check("② 返回的键集合与顺序与原文件完全一致（不增不删）",
      keys(res.text) == keys(ORIG), f"{keys(res.text)}")
check("② verify_key_sets：一致时返回空串", aibib.verify_key_sets(ORIG, res.text) == "")
check("② verify_key_sets：少一条就报出来（第 ③ 条保障的最后一道闸）",
      "不一致" in aibib.verify_key_sets(ORIG, fence(BURGE_OK)), "")
_ob, _nb = blocks(ORIG), blocks(res.text)
check("② AI **没有**返回的条目逐字节不动（A2014）", _ob["A2014"] == _nb["A2014"], "")
check("② 文件头注释没被顺手抹掉", res.text.startswith("% ── 由脚注 / 文末编号清单中的文献条目生成 ──"))
check("② 文件尾换行/空行结构不变（条目之间的空行数不变）",
      res.text.count("\n\n") == ORIG.count("\n\n"), f"{res.text.count(chr(10)+chr(10))} vs {ORIG.count(chr(10)+chr(10))}")
check("② n_merged / n_skipped 对得上", (res.n_merged, res.n_skipped) == (2, 1), f"{res.n_merged}/{res.n_skipped}")
check("② 未返回的那条记为 skipped（中文原因）",
      res.by_key("A2014").status == "skipped" and "未返回" in res.by_key("A2014").reason)

# key 改名：跳过，正文里的这一条一个字节都不动
res2 = aibib.merge(ORIG, {"Burge1984": fence(BURGE_RENAMED)})
check("② 改了 key → 整条跳过（不并入），原字节不动",
      res2.n_merged == 0 and res2.text == ORIG and "不一致" in res2.by_key("Burge1984").reason,
      res2.by_key("Burge1984").reason[:60])

# 多条 / 垃圾：跳过
res3 = aibib.merge(ORIG, {"Burge1984": fence(BURGE_OK + "\n\n" + ALMOG_OK)})
check("② 回复里多条 → 跳过（不猜是哪一条）",
      res3.n_merged == 0 and res3.text == ORIG and "2 条" in res3.by_key("Burge1984").reason,
      res3.by_key("Burge1984").reason[:60])
res4 = aibib.merge(ORIG, {"Burge1984": "查不到。"})
check("② 垃圾回复 → 跳过",
      res4.n_merged == 0 and res4.text == ORIG and "没有可用条目" in res4.by_key("Burge1984").reason)

# 一个字段都没有 → 不许把整条清空
res5 = aibib.merge(ORIG, {"Burge1984": fence("@article{Burge1984,\n}")})
check("② 返回的条目一个字段都没有 → 跳过（不许把整条清空）",
      res5.n_merged == 0 and res5.text == ORIG and "清空" in res5.by_key("Burge1984").reason,
      res5.by_key("Burge1984").reason[:60])

# 原件里没有的键 → 绝不新增条目
res6 = aibib.merge(ORIG, {"Zed1999": fence("@article{Zed1999,\n  title = {Z}\n}")})
check("② AI 返回原件里没有的键 → 不新增条目，记一笔",
      res6.n_merged == 0 and res6.text == ORIG and "不存在" in res6.by_key("Zed1999").reason
      and "Zed1999" not in keys(res6.text), res6.by_key("Zed1999").reason[:50])

# 重复键 → 跳过（否则同一条会被写进两个位置）
DUP = ORIG + "\n@article{Burge1984,\n  title = {重了一条}\n}\n"
res7 = aibib.merge(DUP, {"Burge1984": fence(BURGE_OK)})
check("② 原件里键重复 → 跳过该键（不重复写入）",
      res7.n_merged == 0 and "2 次" in res7.by_key("Burge1984").reason, res7.by_key("Burge1984").reason[:40])

# 一条都没得并 → 文本逐字节相同
res8 = aibib.merge(ORIG, {})
check("② 没有任何回复可用 → 文本与原件**逐字节相同**", res8.text == ORIG and not res8.changed)

# 幂等：同一份回复再并一次，结果不变
res9 = aibib.merge(res.text, {"Burge1984": fence(BURGE_OK), "Almog1989": fence(ALMOG_OK)})
check("② 幂等：同一份回复再并一次结果逐字节相同", res9.text == res.text, "")

# GAP 1：围栏外的说明并进文件后必须在**条目上方**（回读校验只认上方注释）
res_note = aibib.merge(ORIG, {"Burge1984": fence(BURGE_OK) + "\n" + _OUT_NOTE})
check("② 围栏外的 `% ⚠ 待核实` 进了文件，且紧挨在该条目**上方**（不是下面）",
      f"{_OUT_NOTE}\n@article{{Burge1984," in res_note.text
      and res_note.text.count("待核实") == 1, "")
_note_entry = next(e for e in parse_entries(res_note.text)[0] if e.key == "Burge1984")
check("② 该注释被 `bibparse` 认作这条的注释（回读校验据此把缺字段降为提示）",
      any("待核实" in c for c in _note_entry.comments), f"{_note_entry.comments}")
check("② 说明行不影响条目本身（键集合与条目数不变）",
      keys(res_note.text) == keys(ORIG), "")
check("② 合并记录里写明了「条目上方保留的 AI 注释」",
      any("待核实" in n for n in res_note.by_key("Burge1984").notes),
      f"{res_note.by_key('Burge1984').notes}")


# ── ③ 字段级 diff + author/editor 倒装 ───────────────────────────────────

print("\n── ③ 字段级 diff 与 author/editor 倒装 ──")
_d = res.by_key("Burge1984").diff
check("③ Burge1984 的 diff：去掉 doi（AI 没写回）",
      _d.removed == ["doi"], f"removed={_d.removed}")
check("③ Burge1984 的 diff：author 值改动被记下来（含改法）",
      any(c.startswith("author: T. Burge → Burge, T.") for c in _d.changed), f"{_d.changed}")
check("③ 倒装说明写进 notes（编辑要能看出是引擎改的）",
      any("normalize_author_field" in n for n in res.by_key("Burge1984").notes),
      f"{res.by_key('Burge1984').notes}")
check("③ 多条英文作者**每一个**都倒装（editor 也查）",
      blocks(res.text)["Almog1989"].count("Almog, J. and Perry, J. and Wettstein, H.") == 1,
      blocks(res.text)["Almog1989"].splitlines()[3])
_cjk = aibib.merge(ORIG, {"A2014": fence(
    "@book{A2014,\n  author    = {阿斯海姆（著），张建军、万林（译）},\n"
    "  title     = {指称与意向性},\n  publisher = {南京大学出版社},\n"
    "  year      = {2014},\n  address   = {南京},\n  userf     = {cn}\n}")})
check("③ 中文著/译体例的作者值一个字节不动（不许拆成 and）",
      "author    = {阿斯海姆（著），张建军、万林（译）}" in _cjk.text and _cjk.n_merged == 1,
      blocks(_cjk.text)["A2014"].splitlines()[1])
check("③ A2014 的字段顺序被按第三节重排（year 让位给 address），记进 diff",
      _cjk.by_key("A2014").diff.reordered
      == ["author", "title", "publisher", "address", "year", "userf"],
      f"{_cjk.by_key('A2014').diff.reordered}")
check("③ 重排说明写进 notes（编辑要能看出是引擎挪的）",
      any("字段顺序已按任务书第三节" in n for n in _cjk.by_key("A2014").notes),
      f"{_cjk.by_key('A2014').notes}")
# 字段值与顺序都没变、只是排版（对齐空格）不同时，才该说「只有排版不同」
_same = aibib.merge(ORIG, {"A2014": fence(
    "@book{A2014,\n  author = {阿斯海姆（著），张建军、万林（译）},\n"
    "  title = {指称与意向性},\n  publisher = {南京大学出版社},\n"
    "  address = {南京},\n  year = {2014},\n  userf = {cn}\n}")})
check("③ 字段值与顺序都没变、只是排版不同时，说明里点出「只有排版/注释位置不同」",
      any("只有排版/注释位置不同" in n for n in _same.by_key("A2014").notes)
      and _same.by_key("A2014").diff.empty, f"{_same.by_key('A2014').notes}")

_etype = aibib.merge(ORIG, {"Burge1984": fence(
    "% 类型已改：@article → @book（其实是专著）\n" + BURGE_OK.replace("@article", "@book"))})
check("③ 类型变化记进 diff（@article → @book）",
      _etype.by_key("Burge1984").diff.etype_from == "article"
      and _etype.by_key("Burge1984").diff.etype_to == "book"
      and "类型" in _etype.by_key("Burge1984").diff.summary(),
      _etype.by_key("Burge1984").diff.summary()[:50])
_miss = aibib.merge(ORIG, {"Burge1984": fence(BURGE_OK.replace(
    "  journal = {The Journal of Philosophy},\n", "").replace(
    "  volume  = {81},\n", ""))})
check("③ 缺必填字段**不是**跳过理由：照样并入，但记成 warning",
      _miss.n_merged == 1 and any("journal" in w for w in _miss.by_key("Burge1984").warnings),
      f"{_miss.by_key('Burge1984').warnings}")
check("③ 已带 `% ⚠ 待核实` 注释的条目：缺字段也会被并入并提示",
      _miss.by_key("Burge1984").status == "merged")

# GAP 3：字段顺序按任务书第三节重排（未知字段留末尾），且幂等
SHUFFLED = fence(
    "@article{Burge1984,\n"
    "  year    = {1984},\n"
    "  doi     = {10.1000/z},\n"
    "  author  = {T. Burge},\n"
    "  pages   = {5--29},\n"
    "  title   = {Epistemic Paradox},\n"
    "  journal = {The Journal of Philosophy},\n"
    "  number  = {1},\n"
    "  volume  = {81}\n}"
)
_sf = aibib.merge(ORIG, {"Burge1984": SHUFFLED})
_order = [ln.strip().split()[0] for ln in blocks(_sf.text)["Burge1984"].splitlines()[1:-1]
          if "=" in ln]
check("③ 乱序回复并入后，字段顺序 = 任务书第三节的固定顺序",
      _order == ["author", "title", "journal", "volume", "number", "pages", "year", "doi"],
      f"{_order}")
check("③ 未知/额外字段（doi）留在**末尾**（不是被删、也不是插在中间）",
      _order[-1] == "doi" and _sf.by_key("Burge1984").diff.reordered == _order,
      f"{_sf.by_key('Burge1984').diff.reordered}")
check("③ 重排**只动顺序**：字段值逐字节保留（含花括号与 `--`）",
      "  pages   = {5--29}," in blocks(_sf.text)["Burge1984"]
      and "  author  = {Burge, T.}," in blocks(_sf.text)["Burge1984"], "")
_sf2 = aibib.merge(_sf.text, {"Burge1984": SHUFFLED})
check("③ 重排幂等：再并一次同一份回复，结果逐字节相同", _sf2.text == _sf.text, "")
_sf3 = aibib.merge(ORIG, {"Burge1984": fence(BURGE_OK)})
check("③ 本来就在固定顺序上的条目：不报重排（diff.reordered 为空）",
      _sf3.by_key("Burge1984").diff.reordered == [], "")

# 合并记录（任务书第六节）
_sec = aibib.render_merge_section(res, "reference.bib.ai前")
check("③ 合并记录：章节标题、并入/跳过计数、回滚副本名、逐条原因都在",
      _sec.startswith("## 六、合并记录（本次已并入 reference.bib）")
      and "并入 2 条" in _sec and "跳过 1 条" in _sec
      and "reference.bib.ai前" in _sec and "`A2014`" in _sec, "")
check("③ 合并记录：0 条并入时明说「逐字节相同」",
      "没有任何条目并入" in aibib.render_merge_section(res8), "")


# ── ④ apply_ai_merge：落盘 ───────────────────────────────────────────────

print("\n── ④ apply_ai_merge：回滚副本 / 报告 / 合并记录 / 0 条不写盘 ──")


def make_case(tmp, replies, rejected=None, tex=True):
    bib = os.path.join(tmp, "reference.bib")
    with open(bib, "w", encoding="utf-8") as f:
        f.write(ORIG)
    tb = os.path.join(tmp, "Demo_文献核实.md")
    with open(tb, "w", encoding="utf-8") as f:
        f.write("# 参考文献核实任务书 · Demo\n")
    if tex:
        with open(os.path.join(tmp, "Demo.tex"), "w", encoding="utf-8") as f:
            f.write("\\parencite{Burge1984}\n")
    return bib, tb, convert.apply_ai_merge(bib, tb, replies, "Demo", tmp, rejected=rejected)


with tempfile.TemporaryDirectory() as tmp:
    bib, tb, mg = make_case(tmp, {"Burge1984": fence(BURGE_OK), "Almog1989": fence(ALMOG_OK)})
    check("④ 返回并入/跳过计数", (mg["n_merged"], mg["n_skipped"]) == (2, 1), str(mg)[:80])
    check("④ 回滚副本 `reference.bib.ai前` = 合并**前**的那份（逐字节相同）",
          os.path.isfile(mg["rollback"]) and open(mg["rollback"], encoding="utf-8").read() == ORIG)
    check("④ reference.bib 已写回合并结果",
          "Burge, T." in open(bib, encoding="utf-8").read() and "{doi}" not in open(bib, encoding="utf-8").read())
    check("④ `<stem>_回读校验报告.md` 已生成，且结论行同源",
          os.path.isfile(mg["report"])
          and "**结论：" in open(mg["report"], encoding="utf-8").read()
          and mg["conclusion"].startswith("结论："), mg["conclusion"])
    check("④ 报告的「原件」指向回滚副本（编辑能对着 diff 看）",
          "reference.bib.ai前" in open(mg["report"], encoding="utf-8").read())
    check("④ 任务书追加了「六、合并记录」", "## 六、合并记录（本次已并入 reference.bib）"
          in open(tb, encoding="utf-8").read())

with tempfile.TemporaryDirectory() as tmp:
    bib, tb, mg = make_case(tmp, {}, rejected={"Burge1984": "AI 返回未正常结束（截断），不并入"})
    check("④ 0 条并入：reference.bib **一个字节都不动**",
          open(bib, encoding="utf-8").read() == ORIG, "")
    check("④ 0 条并入：**不写**回滚副本、**不写**报告",
          not os.path.exists(bib + ".ai前") and not mg["report"], str(mg)[:90])
    check("④ 0 条并入：error 非空（调用方据此打印「未改动」而不是成功）",
          bool(mg["error"]) and mg["n_merged"] == 0, mg["error"])
    _tb = open(tb, encoding="utf-8").read()
    check("④ 0 条并入：原因仍逐条写进任务书（编辑要看得到）",
          "## 六、合并记录（本次未并入 reference.bib）" in _tb and "截断" in _tb, "")

with tempfile.TemporaryDirectory() as tmp:
    _bib, _tb, mg = make_case(tmp, {})
    check("④ 没有任何回复：也不许写盘",
          open(_bib, encoding="utf-8").read() == ORIG and not mg["report"], str(mg)[:80])


# ── ⑤ 端到端：真实 CLI + mock 的 replay 夹具 ─────────────────────────────

print("\n── ⑤ 端到端：convert.py --verify-refs + 本地 mock（replay 夹具）──")
_MOCK = os.path.join(_MACTOOLBOX, "tools", "mock_llm_server.py")
_FIXTURE = os.path.join(_MACTOOLBOX, "tools", FAKE)


def load_mock():
    spec = importlib.util.spec_from_file_location("mock_llm_server_test", _MOCK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_convert(src, out, extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SLCN_LLM_")}
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    env["SLCN_LLM_THINKING"] = "0"
    p = subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"),
                        "--source", src, "--out", out, "--verify-refs", *extra],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, env=env, cwd=_ENGINE)
    return p


if not (os.path.isfile(_MOCK) and os.path.isfile(_FIXTURE)):
    skip("⑤ 端到端", f"缺 mock 或夹具（{_MOCK} / {_FIXTURE}）")
elif not os.path.isfile(MANUSCRIPT):
    skip("⑤ 端到端", f"缺真实稿件（{MANUSCRIPT}）")
else:
    mock = load_mock()
    mock.STATE.mode = "replay"
    mock.STATE.replies = mock.load_replies(_FIXTURE)
    srv = mock.ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "noai")
            ai = os.path.join(tmp, "ai")
            # 先跑一遍**不并入**的，作为「合并前」的基准
            p0 = run_convert(MANUSCRIPT, base, ["--no-ai-merge", "--no-original"])
            p1 = run_convert(MANUSCRIPT, ai, [
                "--no-original",
                "--llm-base", f"http://127.0.0.1:{port}/v1",
                "--llm-key", "mock", "--llm-model", "mock-model"])
            out0 = os.path.join(base, "Jingru_Kou")
            out1 = os.path.join(ai, "Jingru_Kou")
            check("⑤ 两次 convert.py 都正常结束（AI 通道不改退出码）",
                  p0.returncode == 0 and p1.returncode == 0,
                  (p1.stderr or p1.stdout)[-200:])
            bib0 = open(os.path.join(out0, "reference.bib"), encoding="utf-8").read()
            bib1 = open(os.path.join(out1, "reference.bib"), encoding="utf-8").read()
            roll = os.path.join(out1, "reference.bib.ai前")
            check("⑤ `--no-ai-merge` 真的不并：产物与从前一致（无 .ai前、无报告）",
                  not os.path.exists(os.path.join(out0, "reference.bib.ai前"))
                  and not os.path.exists(os.path.join(out0, "Jingru_Kou_回读校验报告.md")), "")
            check("⑤ 并入路径：写了回滚副本，且它 = 未并入时的那份 reference.bib（逐字节相同）",
                  os.path.isfile(roll) and open(roll, encoding="utf-8").read() == bib0, "")
            check("⑤ 合并后的 reference.bib 确实变了（至少 1 条）", bib1 != bib0, "")
            check("⑤ 正文引用的键一个都没变（不增不删，顺序一致）",
                  keys(bib1) == keys(bib0), f"{keys(bib1)}")
            b0, b1 = blocks(bib0), blocks(bib1)
            merged_keys = {"Burge1984", "Kvanvig1995", "Kaplan1989", "A2014", "Dang2022"}
            changed = {k for k in b0 if b0[k] != b1[k]}
            check("⑤ 改动的条目**全部**落在 AI 返回过的那几条上",
                  changed <= merged_keys, f"changed={sorted(changed)}")
            check("⑤ AI 未返回的条目逐字节不动", not (changed - merged_keys)
                  and all(b0[k] == b1[k] for k in b0 if k not in merged_keys), "")
            check("⑤ 倒装的英文作者写进了产物（`David Kaplan` → `Kaplan, D.`）",
                  "Kaplan, D." in bib1 and "David Kaplan" not in bib1, "")
            check("⑤ `% ⚠ 待核实` 注释跟着条目进了 reference.bib",
                  "% ⚠ 待核实：number" in bib1, "")
            check("⑤ 中文著/译体例没被拆成 and（A2014）",
                  "阿斯海姆（著），张建军、万林（译）" in bib1 and "阿斯海姆（著） and" not in bib1, "")
            # GAP 1 端到端：Dang2022 的 `% ⚠ 待核实` 写在**围栏外面**，也必须进 bib、且在条目上方
            _d22 = next(e for e in parse_entries(bib1)[0] if e.key == "Dang2022")
            check("⑤ GAP1：围栏外的 `% ⚠ 待核实：volume` 被搬到 Dang2022 条目**上方**",
                  any(c.startswith("% ⚠ 待核实：volume") for c in _d22.comments)
                  and "\n@article{Dang2022," in bib1, f"{_d22.comments}")
            check("⑤ GAP1：围栏外的两行注释都进了条目上方（顺序保持、文本不改）",
                  _d22.comments == [
                      "% 类型已改：@misc → @article（源条目是 @misc，本条实为期刊论文）",
                      "% ⚠ 待核实：volume（官网只有在线优先版，卷号两说；查不到就省略该字段）",
                  ], f"{_d22.comments}")
            # GAP 3 端到端：Kaplan1989 的乱序回复被按第三节固定顺序重排
            _k_order = [ln.strip().split()[0] for ln in b1["Kaplan1989"].splitlines()[1:-1]
                        if "=" in ln]
            check("⑤ GAP3：Kaplan1989 的字段顺序 = 第三节固定顺序（title→booktitle→editor→…）",
                  _k_order == ["author", "title", "booktitle", "editor", "publisher",
                               "address", "pages", "year"], f"{_k_order}")

            _rpt = os.path.join(out1, "Jingru_Kou_回读校验报告.md")
            _rp = open(_rpt, encoding="utf-8").read() if os.path.isfile(_rpt) else ""
            check("⑤ 回读校验报告已生成，且控制台打印了它的结论行",
                  bool(_rp) and "**结论：" in _rp and "结论：" in p1.stdout, "")
            check("⑤ 报告把「原件」标成 reference.bib.ai前（diff 有对象）",
                  "reference.bib.ai前" in _rp, "")
            # GAP 1 的**验收标准**：说明被认出来后，缺字段从「问题」降为「提示」
            check("⑤ GAP1：Dang2022 的 volume 不再报「问题」（有「待核实」注释就算已注明）",
                  "`Dang2022`：必填字段缺失：`volume`（@article 的必备字段见任务书第一节" not in _rp, "")
            check("⑤ GAP1：volume 进了「提示」，并写明「已按任务书第三节第 6 条注明「待核实」」",
                  "`Dang2022`：必填字段缺失：`volume`——条目上方已按任务书第三节第 6 条注明"
                  "「待核实」" in _rp, "")
            check("⑤ GAP3：字段顺序汇总提示里不再点名合并过的 Kaplan1989",
                  "Kaplan1989" not in next(
                      (ln for ln in _rp.splitlines() if "字段顺序与任务书第三节" in ln), ""), "")
            _md = open(os.path.join(out1, "Jingru_Kou_文献核实.md"), encoding="utf-8").read()
            check("⑤ 任务书里有「六、合并记录」，逐条给出并入/跳过与原因",
                  "## 六、合并记录（本次已并入 reference.bib）" in _md
                  and "`Chalmers2011`" in _md and "不一致" in _md, "")
            check("⑤ 任务书原有的「AI 建议（**须人工核实**…）」全文仍在",
                  "AI 建议（**须人工核实**，工具不保证正确）" in _md, "")
            check("⑤ 控制台给出并入/跳过条数、回滚副本路径与那句人工核对提示",
                  "已并入" in p1.stdout and "reference.bib.ai前" in p1.stdout
                  and "这份 reference.bib 已含 AI 结果，请人工核对" in p1.stdout, "")
            check("⑤ mock 侧确实按 key 命中了夹具（夹具真的被用上了）",
                  mock.STATE.n_replay_hit == len(mock.STATE.replies)
                  and mock.STATE.n_replay_miss > 0,
                  f"hit={mock.STATE.n_replay_hit} miss={mock.STATE.n_replay_miss}")
    finally:
        srv.shutdown()
        srv.server_close()


print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
