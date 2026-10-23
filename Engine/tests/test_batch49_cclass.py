#!/usr/bin/env python3.14
"""第四十九批①：C 类新口径基础设施的回归测试（分类器 / 并集 / warning / 开关默认）。

钉住：
  ① `classify_citation()` 四种 kind（pure_citation / reference_entry / mixed / note_only）
     与字段（ref_raw / entry / note / locator），旧 `classify()` 行为不变；
  ② `c_class_union_enabled()` **默认开**（第五十二批 phaseB：用户拍板切默认 ON；
     显式 False 回退旧行为）；
  ③ `entry_fingerprint()` 忽略页码/版次；`union_entries()` 同文献合并 1 条、
     文末版本优先、脚注补缺、字段冲突 → warning；
  ④ `resolve_citation()`：作者-年+题名相似度+完整度匹配；**不编造**——匹配失败返回
     `citation_unmatched` warning，调用方保留原脚注/正文。

不联网、不写库、不跑 corpus。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_batch49_cclass.py
"""
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_ENGINE = _HERE.parent
for _p in (str(_ENGINE), str(_ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import footnotes  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ── ① 新分类 API（默认即新口径；旧 classify 不变）──────────────────────
f_short = footnotes.classify_citation("Cf. Gary Watson, “Free Agency,” p. 215.")
check("① 短引用（作者+题名+页码）→ pure_citation，locator=215",
      f_short.kind_new == footnotes.CITATION_PURE and f_short.locator == "215",
      f"{f_short.kind_new}/{f_short.locator}")

f_entry = footnotes.classify_citation(
    "Gary Watson, “Free Agency,” The Journal of Philosophy, Vol. 72 (8), pp. 205-220, 1975.")
check("① 完整文献条目 → reference_entry，entry 含题名/年份",
      f_entry.kind_new == footnotes.CITATION_ENTRY
      and "Free Agency" in str((f_entry.entry or {}).get("title", ""))
      and (f_entry.entry or {}).get("year") == "1975",
      str((f_entry.entry or {}).get("title"))[:40])

f_mixed = footnotes.classify_citation(
    "沃森认为自己的观点与柏拉图对灵魂的划分异曲同工。Gary Watson, “Free Action and Free Will,” p. 150.")
# B-199（2026-10-23）：说明+引用由「整条保留 mixed」细化为可拆分的 mixed_split
#（note_before + ref）；docxsource 里 ref 匹配不到既有键时仍回退 mixed（保留脚注）。
check("① 说明+引用 → mixed_split（note_before 带说明、ref_raw 带引用）",
      f_mixed.kind_new == footnotes.CITATION_MIXED_SPLIT and bool(f_mixed.ref_raw)
      and "沃森" in (f_mixed.note_before or ""),
      f"{f_mixed.kind_new} / {f_mixed.note_before!r}")

f_note = footnotes.classify_citation("基金项目：国家社会科学基金重点项目（22AZX019）。")
check("① 纯说明 → note_only", f_note.kind_new == footnotes.CITATION_NOTE)
check("① 旧 classify() 仍返回旧 kind（现网 docxsource 未切换不被破坏）",
      footnotes.classify("Cf. Gary Watson, “Free Agency,” p. 215.").kind in
      ("pure_ref", "mixed", "pure_note", "classical"))

# ── ② 开关默认开（第五十二批 phaseB：v9b PASS 后用户拍板切默认 ON）─────────
check("② C 类总开关默认开（None 缺省 → ON）",
      footnotes.c_class_union_enabled() is True
      and footnotes.c_class_union_enabled(None) is True)
check("② 显式 False 回退旧行为", footnotes.c_class_union_enabled(False) is False)
check("② 显式 True 显式开", footnotes.c_class_union_enabled(True) is True)

# ── ③ 指纹/并集 ─────────────────────────────────────────────────────────
base = {"author": "Watson, Gary", "title": "Free Agency", "year": "1975",
        "publisher": "Journal", "pages": "205-220"}
same_work_other_pages = {"author": "Gary Watson", "title": "{Free} Agency", "year": "1975",
                         "pages": "215", "locator": "215"}
check("③ 指纹忽略页码/花括号、保留卷号/版次（同文献不同页码同指纹）",
      footnotes.entry_fingerprint(base) == footnotes.entry_fingerprint(same_work_other_pages),
      str(footnotes.entry_fingerprint(base)))
end = [("W1975", dict(base), "raw-end")]
foot = [("ref01", dict(same_work_other_pages), "raw-foot")]
merged = footnotes.union_entries(end, foot)
check("③ 同文献仅页码不同 → 合并 1 条（文末版本优先、字段补缺）",
      len(merged) == 1 and merged[0][0] == "W1975"
      and merged[0][1]["pages"] == "205-220")
new_entry = {"author": "Audi, Robert", "title": "Moral Perception", "year": "2013"}
merged2 = footnotes.union_entries(end, foot + [("ref02", dict(new_entry), "raw-new")])
check("③ 脚注新条目并入并集（文末 1 + 新增 1 = 2）",
      len(merged2) == 2 and any(key == "ref02" for key, _e, _r in merged2))
warnings = []
conflict_end = [("K1", {"author": "Watson, Gary", "title": "Free Agency", "publisher": "A"}, "")]
conflict_foot = [("ref03", {"author": "Gary Watson", "title": "Free Agency",
                            "publisher": "B"}, "")]
footnotes.union_entries(conflict_end, conflict_foot, warnings=warnings)
check("③ 字段冲突 → field_conflict warning（文末值优先）",
      any(w.code == "field_conflict" for w in warnings), str([w.code for w in warnings]))

# ── ③b 卷号/版次参与判重（用户 2026-10-19 澄清）─────────────────────────
vol1 = {"author": "苗力田", "title": "亚里士多德全集", "year": "1990", "volume": "第一卷"}
vol2 = {"author": "苗力田", "title": "亚里士多德全集", "year": "1990", "volume": "第2卷"}
check("③b 卷号归一：`第一卷` 与 `Vol.1` 同指纹",
      footnotes.entry_fingerprint(vol1) == footnotes.entry_fingerprint(
          {"author": "苗力田", "title": "亚里士多德全集", "year": "1990", "volume": "Vol.1"}))
merged_vols = footnotes.union_entries([("T1", dict(vol1), "")], [("refV2", dict(vol2), "")])
check("③b 同书不同卷 → 不合并（两条）", len(merged_vols) == 2)
same_vol_pages = {"author": "苗力田", "title": "亚里士多德全集", "year": "1990",
                  "volume": "第1卷", "pages": "999-1000", "locator": "999"}
merged_same_vol = footnotes.union_entries([("T1", dict(vol1), "")],
                                          [("refV1", dict(same_vol_pages), "")])
check("③b 同书同卷、仅页码不同 → 合并 1 条", len(merged_same_vol) == 1)
warns_vol = []
no_vol = {"author": "苗力田", "title": "亚里士多德全集", "year": "1990"}
footnotes.union_entries([("T1", dict(vol1), "")], [("refNV", dict(no_vol), "")],
                        warnings=warns_vol)
check("③b 一方有卷号、一方无 → 不自动合并（两条）+ warning",
      len(footnotes.union_entries([("T1", dict(vol1), "")], [("refNV", dict(no_vol), "")])) == 2
      and any("卷" in w.reason for w in warns_vol), str([w.reason for w in warns_vol]))
merged_eds = footnotes.union_entries(
    [("T1", {"author": "Aristotle", "title": "Works", "year": "1990", "edition": "1"}, "")],
    [("refE2", {"author": "Aristotle", "title": "Works", "year": "1990", "edition": "2"}, "")])
check("③b edition 不同 → 不合并（两条）", len(merged_eds) == 2)

# ── ④ resolve_citation：匹配 + 不编造 ────────────────────────────────────
citation = footnotes.Footnote(fid=7, text="Gary Watson, “Free Agency,” p. 215.",
                              kind_new=footnotes.CITATION_PURE,
                              ref_raw="Gary Watson, “Free Agency,” p. 215.",
                              locator="215")
citation.entry = {"author": "Gary Watson", "title": "Free Agency", "year": "1975"}
key, warning = footnotes.resolve_citation(
    citation, [("W1975", {"author": "Watson, Gary", "title": "Free Agency",
                          "year": "1975", "publisher": "J"}, "")])
check("④ 作者-年+题名相似 → 命中已有 key（正文可转 parencite）",
      key == "W1975" and warning is None)
citation2 = footnotes.Footnote(fid=8, text="Gary Watson, “Unknown Work,” 1999.",
                               kind_new=footnotes.CITATION_PURE,
                               ref_raw="Gary Watson, “Unknown Work,” 1999.")
citation2.entry = {"author": "Gary Watson", "title": "Unknown Work", "year": "1999"}
key2, warning2 = footnotes.resolve_citation(
    citation2, [("W1975", {"author": "Watson, Gary", "title": "Free Agency",
                          "year": "1975", "publisher": "J"}, "")])
check("④ 文末无对应 → citation_unmatched warning，**不编造条目**",
      key2 is None and warning2 is not None
      and warning2.code == "citation_unmatched" and bool(warning2.reason))
citation3 = footnotes.Footnote(fid=9, text="苗力田主编《亚里士多德全集》第二卷，p.10。",
                               kind_new=footnotes.CITATION_PURE,
                               ref_raw="苗力田主编《亚里士多德全集》第二卷，p.10。")
citation3.entry = {"author": "苗力田", "title": "亚里士多德全集", "year": "1990",
                   "volume": "第二卷"}
key3, _w3 = footnotes.resolve_citation(
    citation3, [("T1", {"author": "苗力田", "title": "亚里士多德全集",
                        "year": "1990", "volume": "第一卷"}, "")])
check("④ 双方卷号不同 → 不匹配（不误挂到另一卷）", key3 is None)

# ── ⑤ resolve_citation 匹配率增强（第五十批：候选扩展/作者归一/题名部分/年份强约束）──
def _cite(text, entry=None):
    f = footnotes.Footnote(fid=1, text=text, kind_new=footnotes.CITATION_PURE,
                           ref_raw=text)
    f.entry = entry
    return f

entries_zh = [("Chen2018", {"author": "陈波", "title": "逻辑学导论", "year": "2018"}, "")]
check("⑤ 中文短引用「（陈波，2018）」→ 命中", 
      footnotes.resolve_citation(_cite("陈波，2018，第3页。"), entries_zh)[0] == "Chen2018")
entries_en = [("Cook2010", {"author": "Cook, R. T.", "title": "Let a Thousand Flowers Bloom",
                            "year": "2010"}, "")]
check("⑤ 缩写作者「Cook 2010」→ 命中", 
      footnotes.resolve_citation(_cite("Cook 2010"), entries_en)[0] == "Cook2010")
entries_multi = [("Smith2019", {"author": "Smith, J. and Jones, A.",
                                "title": "Collective Reasoning", "year": "2019"}, "")]
check("⑤ 多作者「Smith et al. 2019」→ 命中（首作者姓）",
      footnotes.resolve_citation(_cite("Smith et al. 2019"), entries_multi)[0] == "Smith2019")
check("⑤ 同姓不同年 → 不匹配（年份强约束）",
      footnotes.resolve_citation(_cite("Smith et al. 2018"), entries_multi)[0] is None)
entries_partial = [("Wolf1987", {"author": "Wolf, S.", "title": "Sanity and the Metaphysics of Responsibility",
                                 "year": "1987"}, "")]
check("⑤ 题名残缺/部分匹配（引号内题名前缀）→ 命中",
      footnotes.resolve_citation(_cite("Susan Wolf, “Sanity and the Metaphysics of Responsibility,” p. 50."),
                                 entries_partial)[0] == "Wolf1987")
check("⑤ 作者拼写容错（Waston/Watson）→ 命中",
      footnotes.resolve_citation(_cite("Gary Waston, “Free Agency,” p. 215."),
                                 [("W1975", {"author": "Watson, G.", "title": "Free Agency",
                                             "year": "1975"}, "")])[0] == "W1975")

# ── ⑥ 第五十批：过并防护 + old→new 键映射（v7 回归 87-9/88-02/86-03）────────
empty_entries = [("n1", {}, ""), ("n2", {"note": "基于表象"}, ""),
                 ("n3", {"author": "", "title": "p. 36"}, "")]
check("⑥ 66-1 空 author/title/仅页码条目 → 指纹 None、禁止合并（87-9 8→1 回归）",
      all(footnotes.entry_fingerprint(e) is None for _k, e, _r in empty_entries)
      and len(footnotes.union_entries([], empty_entries)) == 3)
short_title = [("s1", {"author": "陈波", "title": "逻辑", "year": "2018"}, ""),
               ("s2", {"author": "陈波", "title": "逻辑", "year": "2018"}, "")]
check("⑥ 题名 <4 字不单独作依据（不合并）",
      len(footnotes.union_entries([], short_title)) == 2)
page_like = [("p1", {"author": "Watson, G.", "title": "pp. 61-62", "year": "1975"}, "")]
check("⑥ 题名仅为页码 → 指纹 None", footnotes.entry_fingerprint(page_like[0][1]) is None)
end_k = [("W1975", {"author": "Watson, Gary", "title": "Free Agency", "year": "1975",
                    "publisher": "J"}, "")]
foot_k = [("ref01", {"author": "Gary Watson", "title": "Free Agency", "year": "1975",
                     "publisher": "J", "pages": "215"}, "")]
keymap = {}
merged_k = footnotes.union_entries(end_k, foot_k, keymap=keymap)
check("⑥ 并集提供 old→new 键映射：ref01 → W1975、只保留 1 条",
      merged_k and merged_k[0][0] == "W1975" and keymap.get("ref01") == "W1975")
cit_ref = footnotes.Footnote(fid=3, text="Watson, “Free Agency,” p. 215.",
                             kind_new=footnotes.CITATION_PURE,
                             ref_raw="Watson, “Free Agency,” p. 215.")
cit_ref.entry = {"author": "Gary Watson", "title": "Free Agency", "year": "1975"}
key_final, _w = footnotes.resolve_citation(
    cit_ref, end_k + foot_k, keymap={"ref01": "W1975"})
check("⑥ resolve_citation 返回并集**最终键**（被并键不得再被 cite）",
      key_final == "W1975")
cn_ref = footnotes.classify_citation(
    "杨正文：鼓藏节仪式与苗族社会组织，西南民族学院学报，2000年第5期，第13-26页。")
check("⑥ 中文文献脚注（句末「。」）→ reference_entry（86-03 不误判 mixed）",
      cn_ref.kind_new == footnotes.CITATION_ENTRY, cn_ref.kind_new)

print()
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS!r}")
    sys.exit(1)
print("失败 0 项（全部通过）")
