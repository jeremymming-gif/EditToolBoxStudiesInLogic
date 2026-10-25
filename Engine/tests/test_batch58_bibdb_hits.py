#!/usr/bin/env python3.14
r"""批58（2026-10-20）：引用库**译著/CJK 安全命中**规则的判据测试。

背景（`/tmp/audit_lead/b58-bibdb-audit.md`）：往期稿件初排产物的参考文献里，译著经常
被引用库判成**候选**而不是安全命中——稿件写 `哈瑞`，库内成品 bib 写
`罗姆·哈瑞（著） and 邱仁宗（译）`：题名相似度 1.000、年份/出版社全对，但第一作者
「姓」字面不同，旧规则 `author_ok=False` → 不敢跳过检索/AI。

本批在 `slcn/bibdb.py: Store.match_reference()` 加**译著/CJK 专项**（`hit="translation"`）：
  题名相似度 ≥0.98 + 年份双方都有且相等 + 出版社一致/包含 + 责任标记（著/译/编）归一后
  第一作者姓名相容（相等，或短名 ≥2 字是长名一截**且**原文出现过责任标记）→ `safe`。
五重见证缺任何一条 / 任何矛盾 → 仍是候选（**宁缺毋滥**；误判比漏查更糟）。

断言分组：
  (a) 归一化助手：责任标记剥离 / CJK 姓 / 出版社归一（正反例）
  (b) `_translation_safe_hit` 边界：0.98 收、0.979 不收、未核实/缺出版社/年份矛盾不收
  (c) `match_reference` 端到端：3 条真实译著样式的候选 → safe/translation；
      年份/出版社/作者/题名矛盾 → 不是 safe；key 指向不同作品 → 仍是候选
  (d) **无 false-safe 放宽**：普通英文作者不符、未核实记录、key 碰撞、缺见证都不许 safe
  (e) API 兼容：签名/返回键/HIT_* 常量不变；`hit="translation"` 仍能被 refmark 任务书消费
  (f) 默认题名模糊查询（GUI「查库」）**未被放宽**：译著仍标 `confidence="low"`
  (g) 真实库只读抽测（笔记仓/默认库存在时；不存在则 SKIP）

只读、离线：不联网、不写库、不改任何仓库文件。
用法：cd Engine && python3 tests/test_batch58_bibdb_hits.py
"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibdb, refmark  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []


def check(name: str, cond, detail="") -> bool:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ══ 夹具：临时库（译著 3 条 + 英文 1 条 + key 碰撞 1 条 + 未核实 1 条）══════════
PUBLISHED = {"kind": bibdb.KIND_PUBLISHED, "issue": "2026年第5期(90)",
             "slug": "10 Lei_Li", "file": "reference.bib"}
_TMP = tempfile.mkdtemp(prefix="batch58 bibdb hits ")
_LIB = os.path.join(_TMP, "bibliography.jsonl")
_build = bibdb.Store.load(_LIB, create=True)
_build.import_bib_text("""@book{HaRui1998,
  author    = {罗姆·哈瑞（著） and 邱仁宗（译）},
  title     = {科学哲学导论},
  year      = {1998},
  publisher = {辽宁教育出版社}
}
@book{Kripke2013,
  author    = {克里普克（著） and 梅文（译）},
  title     = {命名与必然性},
  year      = {2013},
  publisher = {上海译文出版社}
}
@book{Tarski2019,
  author    = {塔斯基（著） and 牟博（译）},
  title     = {真理的语义概念和语义学的基础},
  year      = {2019},
  publisher = {商务印书馆}
}
@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970},
  doi     = {10.1000/QUINE.1970}
}
@book{Russell2010,
  author    = {Russell, S. J. and Norvig, P.},
  title     = {Artificial Intelligence: A Modern Approach},
  year      = {2010},
  publisher = {Prentice Hall}
}
""", PUBLISHED, verified=True)
_build.import_bib_text("""@book{Unverified2020,
  author    = {测试作者（著）},
  title     = {译著未核实样例},
  year      = {2020},
  publisher = {测试出版社}
}
""", {"kind": bibdb.KIND_IMPORTED, "file": "draft.bib"}, verified=False)
_build.save()
_STORE = bibdb.Store.load(_LIB)

ENTRY_HARUI = {"author": "哈瑞", "title": "科学哲学导论", "year": "1998",
               "publisher": "辽宁教育出版社"}
ENTRY_KRIPKE = {"author": "克里普克", "title": "命名与必然性", "year": "2013",
                "publisher": "上海译文出版社"}


def _match(fields, **kwargs):
    return _STORE.match_reference(fields, **kwargs)


# ══ (a) 归一化助手 ═════════════════════════════════════════════════════════
print("── (a) 归一化助手：责任标记 / CJK 姓 / 出版社 ──")
check("(a) TRANSLATION_TITLE_THRESHOLD 常量 = 0.98",
      bibdb.TRANSLATION_TITLE_THRESHOLD == 0.98)
check("(a) 剥全角（著）", "著" not in bibdb.strip_author_roles("罗姆·哈瑞（著）"))
check("(a) 剥全角（译）", "译" not in bibdb.strip_author_roles("邱仁宗（译）"))
check("(a) 剥半角 (eds.)", "eds" not in bibdb.strip_author_roles("Smith, J. (eds.)"))
check("(a) 剥半角 (trans.)", "trans" not in bibdb.strip_author_roles("Doe, J. (trans.)"))
check("(a) 普通姓名原样保留", "Quine" in bibdb.strip_author_roles("Quine, W. V. O."))
check("(a) CJK 姓剥离标记：罗姆·哈瑞（著）… → 罗姆哈瑞",
      bibdb._cjk_author_surname("罗姆·哈瑞（著） and 邱仁宗（译）") == "罗姆哈瑞",
      bibdb._cjk_author_surname("罗姆·哈瑞（著） and 邱仁宗（译）"))
check("(a) CJK 姓剥离标记：克里普克（著）→ 克里普克",
      bibdb._cjk_author_surname("克里普克（著）") == "克里普克")
check("(a) 译著姓名相容：哈瑞 ⊂ 罗姆·哈瑞（著）",
      bibdb._translation_surname_match("哈瑞", "罗姆·哈瑞（著） and 邱仁宗（译）") is True)
check("(a) 译著姓名相容：克里普克 = 克里普克（著）（剥标记后相等）",
      bibdb._translation_surname_match("克里普克", "克里普克（著） and 梅文（译）") is True)
check("(a) 反例：罗素 ≠ 罗姆·哈瑞（著）",
      bibdb._translation_surname_match("罗素", "罗姆·哈瑞（著） and 邱仁宗（译）") is False)
check("(a) 反例：英文姓名不走译著放宽",
      bibdb._translation_surname_match("Quine", "Quine, W. V. O.") is False)
check("(a) 反例：无责任标记的包含不算（哈瑞 ⊂ 罗姆哈瑞 但无（著））",
      bibdb._translation_surname_match("哈瑞", "罗姆哈瑞") is False)
check("(a) 出版社相同 → 相容",
      bibdb._publishers_match({"publisher": "辽宁教育出版社"},
                              {"publisher": "辽宁教育出版社"}) is True)
check("(a) 出版社不同 → 不相容",
      bibdb._publishers_match({"publisher": "辽宁教育出版社"},
                              {"publisher": "商务印书馆"}) is False)
check("(a) 出版社为空 → 不作证",
      bibdb._publishers_match({"publisher": ""}, {"publisher": "商务印书馆"}) is False)
check("(a) 出版社包含（ASCII 长名，Springer … 包含 Springer）",
      bibdb._publishers_match({"publisher": "Springer"},
                              {"publisher": "Springer Publishing"}) is True)
check("(a) 出版社泛词不误配（Press vs Prentice Hall）",
      bibdb._publishers_match({"publisher": "Press"},
                              {"publisher": "Prentice Hall"}) is False)

# ══ (b) _translation_safe_hit 边界（伪 hit 直接喂判据）══════════════════════
print("\n── (b) _translation_safe_hit：五重见证边界 ──")
_REC = _STORE.get("HaRui1998")
_HIT_OK = {"title_similarity": 0.98, "record": _REC, "score": 0.8}
check("(b) 题名 0.98（=阈值）+ 年/社/姓名全对 → True",
      bibdb._translation_safe_hit(ENTRY_HARUI, _HIT_OK) is True)
check("(b) 题名 0.979（低于阈值）→ False",
      bibdb._translation_safe_hit(ENTRY_HARUI,
                                  {"title_similarity": 0.979, "record": _REC}) is False)
check("(b) 年份矛盾 → False",
      bibdb._translation_safe_hit(dict(ENTRY_HARUI, year="1999"), _HIT_OK) is False)
check("(b) 缺年份（稿件）→ False",
      bibdb._translation_safe_hit({k: v for k, v in ENTRY_HARUI.items() if k != "year"},
                                  _HIT_OK) is False)
check("(b) 出版社矛盾 → False",
      bibdb._translation_safe_hit(dict(ENTRY_HARUI, publisher="商务印书馆"),
                                  _HIT_OK) is False)
check("(b) 缺出版社 → False",
      bibdb._translation_safe_hit({k: v for k, v in ENTRY_HARUI.items() if k != "publisher"},
                                  _HIT_OK) is False)
check("(b) 作者完全不同 → False",
      bibdb._translation_safe_hit(dict(ENTRY_HARUI, author="张三"), _HIT_OK) is False)
_UNVERIFIED_REC = _STORE.get("Unverified2020")
check("(b) 库记录未核实 → False（未核实不许跳过检索/AI）",
      bibdb._translation_safe_hit({"author": "测试作者", "title": "译著未核实样例",
                                   "year": "2020", "publisher": "测试出版社"},
                                  {"title_similarity": 1.0, "record": _UNVERIFIED_REC})
      is False)

# ══ (c) match_reference 端到端：真实译著样式的候选 → safe/translation ═══════
print("\n── (c) match_reference：译著 → safe（hit=translation）──")
m = _match(ENTRY_HARUI, key="HaRui1998")
check("(c) 哈瑞《科学哲学导论》→ safe/translation",
      m["status"] == bibdb.HIT_SAFE and m["hit"] == "translation"
      and m["record_key"] == "HaRui1998", f"{m['status']}/{m['hit']}/{m['record_key']}")
check("(c) 安全命中带中文理由（说明是译著/CJK 专项）",
      "译著" in m["reason"] and "作者" in m["reason"], m["reason"])
check("(c) 题名相似度 ≥0.99（题名几乎相同才收）", m["title_similarity"] >= 0.99,
      m["title_similarity"])
check("(c) 字段差异只提示：author 差异列出（库 vs 稿件），不改稿",
      m["field_diff"].get("author", {}).get("library", "").find("邱仁宗") >= 0
      and m["field_diff"].get("author", {}).get("manuscript") == "哈瑞",
      m["field_diff"].get("author"))
check("(c) 命中记录 verified=true（安全命中的前提）",
      (m["record"] or {}).get("verified") is True)
m2 = _match(ENTRY_KRIPKE, key="Kripke2013")
check("(c) 克里普克《命名与必然性》→ safe/translation",
      m2["status"] == bibdb.HIT_SAFE and m2["hit"] == "translation"
      and m2["record_key"] == "Kripke2013", f"{m2['status']}/{m2['hit']}")
m3 = _match({"author": "塔斯基", "title": "真理的语义概念和语义学的基础",
             "year": "2019", "publisher": "商务印书馆"}, key="Tarski2019")
check("(c) 塔斯基《真理的语义概念…》→ safe/translation",
      m3["status"] == bibdb.HIT_SAFE and m3["hit"] == "translation"
      and m3["record_key"] == "Tarski2019", f"{m3['status']}/{m3['hit']}")
m3b = _match({"author": "塔斯基（著）", "title": "真理的语义概念和语义学的基础",
              "year": "2019", "publisher": "商务印书馆"}, key="Tarski2019")
check("(c) 稿件侧也带（著）标记 → 至少 safe（走普通 title 路径或 translation 均可）",
      m3b["status"] == bibdb.HIT_SAFE and m3b["record_key"] == "Tarski2019",
      f"{m3b['status']}/{m3b['hit']}")

# ══ (d) 无 false-safe 放宽（反例全部不许 safe）══════════════════════════════
print("\n── (d) 反例：任何见证矛盾都不许 safe ──")
check("(d) 译著但年份不符 → 不是 safe",
      _match(dict(ENTRY_HARUI, year="1999"))["status"] != bibdb.HIT_SAFE)
check("(d) 译著但出版社不符 → 不是 safe",
      _match(dict(ENTRY_HARUI, publisher="商务印书馆"))["status"] != bibdb.HIT_SAFE)
check("(d) 译著但作者完全不同 → 不是 safe",
      _match(dict(ENTRY_HARUI, author="张三"))["status"] != bibdb.HIT_SAFE)
check("(d) 题名不同（相似度低）→ 不是 safe",
      _match({"author": "哈瑞", "title": "完全不同的一本书", "year": "1998",
              "publisher": "辽宁教育出版社"})["status"] != bibdb.HIT_SAFE)
_mk = _match({"author": "罗素", "title": "数理哲学导论", "year": "2010",
              "publisher": "商务印书馆"}, key="Russell2010")
check("(d) key 指向不同作品（Russell2010 = Russell&Norvig）→ 仍是候选（hit=key）",
      _mk["status"] == bibdb.HIT_CANDIDATE and _mk["hit"] == "key",
      f"{_mk['status']}/{_mk['hit']}/{_mk['record_key']}")
check("(d) key 碰撞候选的题名相似度低（证明没把不同作品洗成 safe）",
      _mk["title_similarity"] < 0.5, _mk["title_similarity"])
_mu = _match({"author": "测试作者", "title": "译著未核实样例", "year": "2020",
              "publisher": "测试出版社"})
check("(d) 库内未核实的译著 → 候选（不能跳过检索/AI）",
      _mu["status"] == bibdb.HIT_CANDIDATE, f"{_mu['status']}/{_mu['hit']}")
check("(d) 普通英文作者不符 → 不是 safe（本案不放宽英文姓名匹配）",
      _match({"title": "Philosophy of Logic", "author": "Someone Else",
              "year": "1970"})["status"] != bibdb.HIT_SAFE)
check("(d) 库内无任何线索 → 仍是 miss",
      _match({"title": "Zzzz Not A Real Work At All",
              "author": "Nobody, N.", "year": "2001"}, key="Nope2001")["status"]
      == bibdb.HIT_MISS)

# ══ (e) API 兼容（convert.py / 任务书调用点不改签名）══════════════════════════
print("\n── (e) API 兼容：签名 / 返回键 / HIT_* 常量 / 任务书消费 ──")
_sig = inspect.signature(bibdb.Store.match_reference)
check("(e) match_reference 参数不变（self, fields, key, entry_type）",
      list(_sig.parameters) == ["self", "fields", "key", "entry_type"],
      list(_sig.parameters))
check("(e) 关键字参数默认值不变（key='', entry_type=''）",
      _sig.parameters["key"].default == "" and _sig.parameters["entry_type"].default == "")
_expected_keys = {"status", "key", "record_key", "hit", "title_similarity", "score",
                  "field_diff", "reason", "record"}
check("(e) 返回 dict 的键集合不变（旧调用方按这些键取值）",
      _expected_keys <= set(m.keys()), sorted(set(m.keys()) - _expected_keys))
check("(e) HIT_* 常量值不变（safe/candidate/miss）",
      (bibdb.HIT_SAFE, bibdb.HIT_CANDIDATE, bibdb.HIT_MISS)
      == ("safe", "candidate", "miss"))
check("(e) 新增 hit 类型只用于 safe（translation ∉ {doi,title}，但 status=safe）",
      m["hit"] == "translation" and m["status"] == bibdb.HIT_SAFE)
_task_bib = """@book{HaRui1998,
  author    = {罗姆·哈瑞（著） and 邱仁宗（译）},
  title     = {科学哲学导论},
  year      = {1998},
  publisher = {辽宁教育出版社}
}
"""
_md = refmark.build(_task_bib, "Stem", bibdb_hits=[m])
check("(e) refmark 任务书能消费 hit=translation（不崩、归入「五、库内已核实」）",
      "五、库内已核实（无需再查）" in _md and "HaRui1998" in _md)
check("(e) 任务书仍写明「库内命中」（未改 refmark：hit 映射回退到通用文案）",
      "库内命中" in _md)

# ══ (f) 默认题名模糊查询未被放宽（GUI「查库」页口径不变）══════════════════════
print("\n── (f) query_title（查库页）不被译著规则放宽 ──")
_qt = _STORE.query_title("科学哲学导论", author="哈瑞", year="1998")
check("(f) query_title 仍能列出候选（有 hits）", _qt["count"] >= 1, _qt["count"])
check("(f) 但 confidence 仍是 low（查库页不冒充高置信；只有核实链路用专项安全命中）",
      all(hit["confidence"] == "low" for hit in _qt["hits"]),
      [(h["key"], h["confidence"]) for h in _qt["hits"]])

# ══ (g) 真实库只读抽测（存在才跑；不存在 SKIP）════════════════════════════════
print("\n── (g) 真实引用库只读抽测（笔记仓默认库）──")
try:
    _real_path = bibdb.default_db_path()
except bibdb.BibDBError:
    _real_path = ""
if not _real_path or not os.path.isfile(_real_path):
    SKIPS.append("(g) 默认引用库不存在（公开仓/CI 常态）")
    print("SKIP  (g) 默认引用库不存在 —— 跳过真实库抽测")
else:
    # Hermetic（批58 CI 修复，2026-10-20）：真实库只做**可跳过的抽样**——库不可读、
    # 或库内没有这条样本时 SKIP 而不是 FAIL（CI/公开仓/别人的笔记仓不该因库内容不同而红）。
    try:
        _real = bibdb.Store.load(_real_path)
    except Exception as _exc:  # noqa: BLE001
        _real = None
        SKIPS.append(f"(g) 真实引用库读不了：{type(_exc).__name__}")
        print(f"SKIP  (g) 真实引用库读不了：{type(_exc).__name__}")
    if _real is not None:
        _real_rec = _real.get("HaRui1998")
        if _real_rec is None:
            SKIPS.append("(g) 真实引用库内没有 HaRui1998 样本")
            print("SKIP  (g) 真实引用库内没有 HaRui1998 样本")
        else:
            _rm = _real.match_reference(ENTRY_HARUI, key="HaRui1998")
            check("(g) 真实库：哈瑞《科学哲学导论》→ safe/translation",
                  _rm["status"] == bibdb.HIT_SAFE and _rm["hit"] == "translation",
                  f"{_rm['status']}/{_rm['hit']}")
            _rf = (_real_rec.get("fields") or {})
            check("(g) 真实库记录字段未被本批改动（姓名为「（著）/（译）」原样）",
                  "（著）" in str(_rf.get("author") or "") and _real.dirty is False)

# ══ 汇总 ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 66)
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("失败 0 项（全部通过）")
