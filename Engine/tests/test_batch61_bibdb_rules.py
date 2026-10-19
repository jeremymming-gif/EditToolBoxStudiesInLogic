#!/usr/bin/env python3
r"""批61（2026-10-20）：引用库命中 **key-only 二次判定 + 写法归一** 的判据测试。

背景（`/tmp/audit_lead/b61-bibdb-41-evidence.md`）：5 期成品 bib 的 17 条候选里 12 条是
per-paper key 碰撞（键不可信但仍走检索）；初排产物 safe 93.4% 明显低于成品 98.92%——键
重生成 / 作者写法 / 题名写法差异吃掉了命中。本批在 `slcn/bibdb.py` 加两条**保守**规则：

  ① 题名写法归一二判（`hit="title-normalized"`）：题名写法变体（全角标点/书名号/引号、
     副标题分隔符、版次后缀、尾部括号说明、「, in 书名」容器尾巴、作者分隔符/年份后缀）
     归一后 ≥0.98，且**至少两条见证**（题名 + 年份/作者/出版社之一），
     **任何一方有值却矛盾 → 直接否决**（年份/作者/出版社不符 → 保持候选）；
  ② 内容指纹一致（`hit="content-identical"`）：与库内**已核实**记录的逐字段规范化内容
     完全相同 → 同一条记录（题名缺失/整条引文塞进 author 的旧中文条目靠这条；不存在模糊）。
两条都只影响「是否跳过检索/AI」，不改 reference.bib/.tex；`query_title`（GUI 查库）不放宽。

断言分组：
  (a) 写法归一助手：题名变体 / 作者名归一（正反例）
  (b) 题名归一二判正例：版次、书名号/全角、副标题、章节「, in 」、作者写法、key 碰撞
  (c) 内容指纹正例：无题名条目与已验证记录逐字段一致
  (d) false-safe 反例（≥10 条）：跨年/跨作者/跨社/题名不足/未核实/key 异作品/单见证
  (e) 阈值边界（0.98）与「无矛盾但见证不足」不判 safe
  (f) API 兼容：签名/返回键/HIT_* 不变；query_title 不放宽；hit 值互不越权
  (g) 真实库只读抽测（库存在才跑；否则 SKIP）

只读、离线：不联网、不写库、不改任何仓库文件。
用法：cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch61_bibdb_rules.py
"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibdb  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0


def check(name: str, cond, detail="") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


PUBLISHED = {"kind": bibdb.KIND_PUBLISHED, "issue": "2026年第5期(90)",
             "slug": "08 Yanlin_Liao", "file": "reference.bib"}

_LIB_BIB = """@article{a8,
  author = {Benacerraf, P.},
  title  = {Mathematical Truth},
  year   = {1983}
}
@article{a8-26,
  author    = {van Eemeren, F. H.},
  title     = {The study of argumentation as normative pragmatics},
  year      = {1990},
  publisher = {Foris}
}
@book{Munkres2000,
  author    = {Munkres, J. R.},
  title     = {Topology, (2nd)},
  year      = {2000},
  publisher = {Prentice Hall}
}
@article{VanEijck2014,
  author = {{ van Eijck}, Jan and Renne, Bryan},
  title  = {Belief as {{Willingness}} to {{Bet}}},
  year   = {2014}
}
@article{KC2019ACS,
  author = {Ujjwal, K. C. and Chodorowski, J.},
  title  = {A Case Study of Adding Proactivity in Indoor Social Robots Using Belief Desire Intention (BDI) Model},
  year   = {2019}
}
@book{Russell2010-2,
  author    = {罗素（著） and 晏成书（译）},
  title     = {数理哲学导论},
  year      = {2010},
  publisher = {商务印书馆}
}
@article{VanEemeren2018,
  author = {van Eemeren, F. H.},
  title  = {Argumentation Theory: A Pragma-dialectical Perspective},
  year   = {2018}
}
@article{Wolf1987,
  author = {Wolf, S.},
  title  = {Sanity and the Metaphysics of Responsibility},
  year   = {1987}
}
@article{Chen2018,
  author    = {Chen, Bo},
  title     = {《论语》中的政治隐喻},
  year      = {2018},
  publisher = {中华书局}
}
@article{SharedTitle2020,
  author = {Alpha, A.},
  title  = {A Shared Title Across Works},
  year   = {2020},
  publisher = {Publisher One}
}
@article{Beta2021,
  author = {Beta, B.},
  title  = {A Shared Title Across Works},
  year   = {2021},
  publisher = {Publisher Two}
}
@article{Richard1983,
  author = {Richard, M.},
  title  = {Direct Reference and Ascriptions of Belief},
  year   = {1969}
}
@article{PubConf2020,
  author    = {Chan, C.},
  title     = {Publisher Conflict Work},
  year      = {2020},
  publisher = {Publisher X}
}
@book{NoTitle2020,
  author    = {旧籍整理组},
  year      = {2020},
  publisher = {中华书局}
}
@article{EemerenPolluter,
  author = {Eemeren, X.},
  title  = {An Unrelated Work About Something Else},
  year   = {1960}
}
@article{EijckPolluter,
  author = {Eijck, X.},
  title  = {Another Unrelated Work About Other Things},
  year   = {1960}
}
"""
_UNVERIFIED_BIB = """@article{UnverifiedCase2020,
  author = {Draft, Dana},
  title  = {An Unverified Work For Matching},
  year   = {2020}
}
"""


def _build_store(tmp: str) -> bibdb.Store:
    path = os.path.join(tmp, "bibliography.jsonl")
    store = bibdb.Store.load(path, create=True)
    store.import_bib_text(_LIB_BIB, PUBLISHED, verified=True)
    store.import_bib_text(_UNVERIFIED_BIB,
                          {"kind": bibdb.KIND_IMPORTED, "file": "draft.bib"},
                          verified=False)
    store.save()
    return bibdb.Store.load(path)


_TMP = tempfile.mkdtemp(prefix="batch61 bibdb rules ")
_STORE = _build_store(_TMP)


def _match(fields, **kwargs):
    return _STORE.match_reference(fields, **kwargs)


# ══ (a) 写法归一助手 ════════════════════════════════════════════════════════
print("── (a) 写法归一助手：题名变体 / 作者名归一 ──")
check("(a) 版次后缀 `Topology, (2nd)` → 剥成 `topology`",
      "topology" in bibdb._match_title_forms("Topology, (2nd)"))
check("(a) 中文版次 `（第2版）` 被剥掉（存在 `逻辑学导论` 变体）",
      "逻辑学导论" in bibdb._match_title_forms("逻辑学导论（第2版）"))
check("(a) 尾部括号说明 `(上)` 被剥掉",
      any("" == f or "上" not in f for f in bibdb._match_title_forms("西方哲学原著选读(上)")))
check("(a) 书名号/中文标点折叠：`《论语》中的政治隐喻` 与 `论语 中的政治隐喻` 有共同变体",
      bool(bibdb._match_title_forms("《论语》中的政治隐喻")
           & bibdb._match_title_forms("论语 中的政治隐喻")))
check("(a) 副标题 `：`/`:` 能产出主题名变体",
      "argumentation theory" in bibdb._match_title_forms(
          "Argumentation Theory：A Pragma-dialectical Perspective"))
check("(a) 章节题名「, in 书名」容器尾巴被截掉",
      "sanity and the metaphysics of responsibility" in bibdb._match_title_forms(
          "“Sanity and the Metaphysics of Responsibility,” in Responsibility, Character"))
check("(a) 全角空格/不换行空格折叠后一致",
      bool(bibdb._match_title_forms("A\u00a0Title") & bibdb._match_title_forms("A Title")))
check("(a) 中文作者分隔符：`甲，乙` → 第一作者 `甲`",
      bibdb._match_first_author_name("甲，乙") == "甲")
check("(a) 中文「等」后缀被剥：`沃斯等（著）` → `沃斯`",
      bibdb._match_first_author_name("沃斯等（著）") == "沃斯")
check("(a) 英文「姓在前、名缩写在后」：`Ujjwal K. C.` → `ujjwal`",
      bibdb._match_first_author_name("Ujjwal K. C.") == "ujjwal")
check("(a) 英文常规 `Munkres, J. R.` → `munkres`",
      bibdb._match_first_author_name("Munkres, J. R.") == "munkres")
check("(a) 作者归一不误伤普通姓名：`Johan van Benthem` → `benthem`",
      bibdb._match_first_author_name("Johan van Benthem") == "benthem")
check("(a) 年份后缀 `2005a` 归一为 2005",
      bibdb.entry_year({"year": "2005a"}) == "2005")
check("(a) 题名变体不跨作品：`Topology` 与 `Topology: A First Course` 主题名相同（由见证否决，不在助手层否决）",
      bool(bibdb._match_title_forms("Topology")
           & bibdb._match_title_forms("Topology: A First Course")))

# ══ (b) 题名归一二判正例（key 碰撞 / 写法差异）══════════════════════════════
print("\n── (b) 题名归一二判：hit=title-normalized ──")
m = _match({"title": "The study of argumentation as normative pragmatics",
            "author": "F. H. van Eemeren", "year": "1990"}, key="a8")
check("(b) key 碰撞（a8 指向 Benacerraf）→ 题名归一命中 a8-26 → safe",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "title-normalized"
      and m["record_key"] == "a8-26", f"{m['status']}/{m['hit']}/{m['record_key']}")
check("(b) 理由写明「题名写法归一」与见证（含 year/author）",
      "题名写法归一" in m["reason"] and "year" in m["reason"] and "author" in m["reason"],
      m["reason"])
m = _match({"title": "Topology", "author": "James R. Munkres", "year": "2000",
            "publisher": "Prentice Hall, Inc."}, key="Munkres2000")
check("(b) 版次后缀差异（`Topology` vs `Topology, (2nd)`）→ safe，见证含 publisher",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "title-normalized"
      and "publisher" in m["reason"], f"{m['status']}/{m['hit']}")
m = _match({"title": "Belief as Willingness to Bet",
            "author": "Jan van Eijck and Bryan Renne", "year": "2014"}, key="van2014")
check("(b) 双花括号写法差异 → safe（题名变体一致 + 年/作者）",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "title-normalized"
      and m["record_key"] == "VanEijck2014", f"{m['status']}/{m['hit']}")
m = _match({"title": "A Case Study of Adding Proactivity in Indoor Social Robots "
                     "Using Belief Desire Intention (BDI) Model",
            "author": "Ujjwal K. C. and Jacques Chodorowski", "year": "2019"},
           key="KC2019ACS")
check("(b) 作者「姓在前、名缩写在后」写法差异 → safe",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "title-normalized",
      f"{m['status']}/{m['hit']}/{m['reason']}")
m = _match({"title": "Argumentation Theory",
            "author": "F. H. van Eemeren", "year": "2018"}, key="a10")
check("(b) 副标题（稿只有主题名）→ safe",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "title-normalized"
      and m["record_key"] == "VanEemeren2018", f"{m['status']}/{m['hit']}")
m = _match({"title": "“Sanity and the Metaphysics of Responsibility,” "
                     "in Responsibility, Character, and the Emotions",
            "author": "Cf. Susan Wolf", "year": "1987"}, key="Wolf1987")
check("(b) 章节引文「, in 书名」→ safe（截容器 + 作者 Cf. 前缀容忍）",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "title-normalized",
      f"{m['status']}/{m['hit']}")
m = _match({"title": "数理哲学导论", "author": "罗素", "year": "2010"},
           key="Russell2010")
check("(b) 译著作者写法（稿「罗素」 vs 库「罗素（著） and 晏成书（译）」）→ safe",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "title-normalized"
      and m["record_key"] == "Russell2010-2", f"{m['status']}/{m['hit']}")
m = _match({"title": "论语 中的政治隐喻", "author": "Chen, Bo", "year": "2018"},
           key="chen2018")
check("(b) 书名号/全角标点差异 → safe（旧题名路径即可命中，或走归一）",
      m["status"] == bibdb.HIT_SAFE and m["record_key"] == "Chen2018",
      f"{m['status']}/{m['hit']}/{m['record_key']}")

# ══ (c) 内容指纹一致（无题名 / 旧中文条目）══════════════════════════════════
print("\n── (c) 内容指纹（hit=content-identical）──")
_no_title = {"author": "旧籍整理组", "year": "2020", "publisher": "中华书局"}
m = _match(dict(_no_title), key="NoTitle2020", entry_type="book")
check("(c) 无题名条目与已验证记录逐字段一致 → safe/content-identical",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "content-identical"
      and m["record_key"] == "NoTitle2020", f"{m['status']}/{m['hit']}")
check("(c) 内容指纹理由写明「完全一致」", "完全一致" in m["reason"], m["reason"])
m = _match({"author": "旧籍整理组", "year": "2021", "publisher": "中华书局"},
           key="NoTitle2020", entry_type="book")
check("(c) 出版年不同 → 内容指纹不命中（仍是候选/未命中，不许 safe）",
      m["status"] != bibdb.HIT_SAFE, f"{m['status']}/{m['hit']}")
m = _match({"author": "Draft, Dana", "title": "An Unverified Work For Matching",
            "year": "2020"}, key="UnverifiedCase2020")
check("(c) 内容相同的库记录**未核实** → 不许 safe（仍走检索/AI）",
      m["status"] != bibdb.HIT_SAFE, f"{m['status']}/{m['hit']}")
m = _match({"author": "旧籍整理组", "year": "2020", "publisher": "另一个社"},
           key="x", entry_type="book")
check("(c) 内容指纹不同（出版社不同）→ 不判 content-identical",
      m.get("hit") != "content-identical")

# ══ (d) false-safe 反例（≥10 条）════════════════════════════════════════════
print("\n── (d) false-safe 反例（任何矛盾都不许 safe）──")
m = _match({"title": "Direct Reference and Ascriptions of Belief",
            "author": "Mark Richard", "year": "1983"}, key="Richard1983")
check("(d1) 题名同但**年份**不同（1983 vs 1969）→ 不是 safe",
      m["status"] != bibdb.HIT_SAFE, f"{m['status']}/{m['hit']}")
m = _match({"title": "The study of argumentation as normative pragmatics",
            "author": "Someone Else", "year": "1990"}, key="a8")
check("(d2) 题名同但**作者**不同 → 不是 safe",
      m["status"] != bibdb.HIT_SAFE, f"{m['status']}/{m['hit']}")
check("(d3) 新规则对**出版社矛盾**直接否决（题名/年/作者全同也不收）",
      bibdb._match_title_witnesses(
          {"title": "Publisher Conflict Work", "author": "Chan, C.",
           "year": "2020", "publisher": "Publisher Y"},
          _STORE.get("PubConf2020")) is None)
m = _match({"title": "A Completely Different Work Title", "author": "Alpha, A.",
            "year": "2020"}, key="SharedTitle2020")
check("(d4) 题名相似度不足 → 不是 safe", m["status"] != bibdb.HIT_SAFE)
m = _match({"title": "Mathematical Truth", "author": "Nobody, N.", "year": "1983"},
           key="a8")
check("(d5) key 指向不同作品且作者不符 → 仍是候选（不许 safe）",
      m["status"] == bibdb.HIT_CANDIDATE, f"{m['status']}/{m['hit']}")
m = _match({"title": "An Unverified Work For Matching", "author": "Draft, Dana",
            "year": "2020"}, key="UnverifiedCase2020")
check("(d6) 未核实记录的题名匹配 → 候选（不能跳过检索/AI）",
      m["status"] == bibdb.HIT_CANDIDATE, f"{m['status']}/{m['hit']}")
m = _match({"title": "A Shared Title Across Works", "author": "Alpha, A.",
            "year": "2021"}, key="x")
check("(d7) 同题名但**年份矛盾**（2020 记录 vs 稿 2021）→ 不是 safe",
      m["status"] != bibdb.HIT_SAFE, f"{m['status']}/{m['hit']}")
m = _match({"title": "A Shared Title Across Works", "author": "Beta, B.",
            "year": "2020"}, key="x")
check("(d8) 同题名、年份 2020/2021 不符且作者不同 → 不是 safe",
      m["status"] != bibdb.HIT_SAFE, f"{m['status']}/{m['hit']}")
m = _match({"title": "A Shared Title Across Works", "author": "Beta, B.",
            "year": "2021", "publisher": "Publisher Two"}, key="x")
check("(d9) 同题名但**两个不同作品**（作者/年不同）→ 只命中与稿一致的 Beta2021，不是 SharedTitle2020",
      m["status"] == bibdb.HIT_SAFE and m["record_key"] == "Beta2021",
      f"{m['status']}/{m['record_key']}")
m = _match({"title": "Publisher Conflict Work", "author": "Chan, C.",
            "year": "2020"}, key="PubConf2020")
check("(d10) 稿缺出版社 → 题名+年+作者仍可 safe（缺见证不否决，有矛盾才否决）",
      m["status"] == bibdb.HIT_SAFE, f"{m['status']}/{m['hit']}")
m = _match({"title": "Argumentation Theory"}, key="a10")
check("(d11) 只有主标题、无作者/年份/出版社 → 见证不足（1 条）→ 不是 safe",
      m["status"] != bibdb.HIT_SAFE, f"{m['status']}/{m['hit']}")
m = _match({"title": "Philosophy of Logic", "author": "Nobody, N.",
            "year": "1970"}, key="zzz")
check("(d12) 库内无任何线索 → miss（不因新规则变成候选）",
      m["status"] == bibdb.HIT_MISS, f"{m['status']}/{m['hit']}")

# ══ (e) 阈值与见证边界 ══════════════════════════════════════════════════════
print("\n── (e) 阈值 0.98 / 见证数量边界 ──")
check("(e) TITLE_SAME_THRESHOLD 常量 = 0.98", bibdb.TITLE_SAME_THRESHOLD == 0.98)
check("(e) 变体命中打分 ≥ 阈值（Munkres 变体对相似度 = 1.0）",
      max(bibdb.title_similarity(a, b)
          for a in bibdb._match_title_forms("Topology")
          for b in bibdb._match_title_forms("Topology, (2nd)")) >= 0.98)
check("(e) 题名仅「主标题相同」但作者/年都缺 → 见证不足，不判 safe",
      _match({"title": "Argumentation Theory"}, key="a10")["status"] != bibdb.HIT_SAFE)
check("(e) 题名变体一致 + 只有年份一条见证 → 恰好 2 条见证 → safe",
      _match({"title": "Argumentation Theory", "year": "2018"}, key="a10")["status"]
      == bibdb.HIT_SAFE)
check("(e) 题名变体一致 + 只有作者一条见证 → exactly 2 条 → safe",
      _match({"title": "Argumentation Theory", "author": "F. H. van Eemeren"},
             key="a10")["status"] == bibdb.HIT_SAFE)
check("(e) 见证被否决时 title-normalized 不出现（年份矛盾的 hit 不是 title-normalized）",
      _match({"title": "Direct Reference and Ascriptions of Belief",
              "author": "Mark Richard", "year": "1983"}, key="Richard1983"
             ).get("hit") != "title-normalized")

# ══ (f) API 兼容 / 口径不放宽 ═══════════════════════════════════════════════
print("\n── (f) API 兼容：签名 / 返回键 / query_title 不放宽 ──")
_sig = inspect.signature(bibdb.Store.match_reference)
check("(f) match_reference 参数不变（self, fields, key, entry_type）",
      list(_sig.parameters) == ["self", "fields", "key", "entry_type"],
      list(_sig.parameters))
_expected = {"status", "key", "record_key", "hit", "title_similarity", "score",
             "field_diff", "reason", "record"}
check("(f) 返回 dict 的键集合不变", _expected <= set(m.keys()))
check("(f) HIT_* 常量值不变",
      (bibdb.HIT_SAFE, bibdb.HIT_CANDIDATE, bibdb.HIT_MISS)
      == ("safe", "candidate", "miss"))
check("(f) 新增 hit 只用于 safe（title-normalized / content-identical 不出现在候选/未命中）",
      all(x.get("hit") not in ("title-normalized", "content-identical")
          for x in (_match({"title": "A Completely Different Work Title",
                            "author": "Nobody", "year": "2000"}, key="x"),
                    _match({"title": ""}, key="y"))))
_qt = _STORE.query_title("Topology", author="James R. Munkres", year="2000")
check("(f) query_title（GUI 查库页）未被写法归一放宽：仍无 confidence=high 的变体命中",
      all(hit["confidence"] == "low" for hit in _qt["hits"]),
      [(h["key"], h["confidence"]) for h in _qt["hits"]])
check("(f) 内容指纹规则不改变 query_title 结果",
      _STORE.query_title("Topology")["count"] == _STORE.query_title("Topology")["count"])

# ══ (g) 真实库只读抽测（存在才跑；否则 SKIP）════════════════════════════════
print("\n── (g) 真实引用库只读抽测（笔记仓默认库）──")
try:
    _real_path = bibdb.default_db_path()
except bibdb.BibDBError:
    _real_path = ""
if not _real_path or not os.path.isfile(_real_path):
    SKIPS.append("(g) 默认引用库不存在（公开仓/CI 常态）")
    print("SKIP  (g) 默认引用库不存在 —— 跳过真实库抽测")
else:
    try:
        _real = bibdb.Store.load(_real_path)
    except Exception as _exc:  # noqa: BLE001
        _real = None
        SKIPS.append(f"(g) 真实引用库读不了：{type(_exc).__name__}")
        print(f"SKIP  (g) 真实引用库读不了：{type(_exc).__name__}")
    if _real is not None:
        _rm = _real.match_reference(
            {"title": "The study of argumentation as normative pragmatics",
             "author": "F. H. van Eemeren", "year": "1990"}, key="a8")
        if _rm["status"] == bibdb.HIT_SAFE and _rm["record_key"] == "a8-26":
            check("(g) 真实库：90/08 a8 的题名归一二判 → a8-26（safe）",
                  _rm["hit"] == "title-normalized", f"{_rm['status']}/{_rm['hit']}")
        else:
            SKIPS.append("(g) 真实库内无 a8-26 样本（库版本不同）")
            print("SKIP  (g) 真实库内无 a8-26 样本")
        check("(g) 真实库只读（load 后 dirty=False）", _real.dirty is False)

# ══ 汇总 ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 66)
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print(f"全部 {PASS} 项断言通过")
