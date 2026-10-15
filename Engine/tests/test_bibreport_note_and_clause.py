#!/usr/bin/env python3.14
r"""「待核实」注释判据（案例 77）与任务书第三节条号（案例 78）的判据测试。

两组断言：
  ① 第 77 条 —— 「待核实」注释什么时候才该建议删
     · 反例（必配）：注释点名了 `edition` 而 `edition` 缺（@book 的 **nice** 字段，
       `Quine1970` / `Williamson2007` 形态）→ **绝不许**出现「建议删掉该注释」；
     · 反例：注释还在记一个**悬着的问题**（值两说 / 疑似笔误 / 暂按原文保留，
       `Maddy2002` / `Martin2025` / `ChenBo2023` 形态）→ 绝不许建议删；
     · 正例：注释点名的字段**已填齐**（`ChenBo2022` 的「卷号暂缺」但 `volume` 已补、
       `HaRui1998`）、或干脆没点名任何字段、或写着「已按官方来源核实，可删」→ 才可以建议删；
     · 「点名」的解析必须用 `slcn/bibcheck.py` 的字段表（`REQUIRED` 的 must + nice），
       不许另列一份；`booktitle` 不许被误认成 `title`；
     · 真实稿 `2026年第5期(90)/10 Bo_Chen` 的受控夹具（7 条「待核实」注释原样抄录）
       → 改前 7 条建议删，改后只剩 2 条（`ChenBo2022` / `HaRui1998`）。
  ② 第 78 条 —— 任务书第三节的条号只能从**唯一一处**取
     · `bibreport.SECTION3_CLAUSES` 里每个条号，与 `refmark.FORMAT_MD` 实际渲染出的
       第三节编号清单**逐条对齐**（锚点必须落在该号那一条的正文里）；
     · 有人把条号改错一位（历史上就是整体错一位）→ 对齐函数必须报错（测试自带反证）；
     · 报告里真正渲染出来的消息，用的必须是那一处的号（5→6 / 6→7 / 2→3 / 7→8）。

不写 out/、不联网、不跑 corpus.py / build.sh；只读内存里的字符串。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 \
        tests/test_bibreport_note_and_clause.py
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibcheck, bibreport, refmark  # noqa: E402

BIBREPORT_PY = os.path.join(_ENGINE, "slcn", "bibreport.py")

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def details(rep, key, level=None, needle=None):
    """该键下的 finding.detail（可按级别/子串过滤）。"""
    out = []
    for f in rep.findings:
        if f.key != key:
            continue
        if level is not None and f.level != level:
            continue
        if needle is not None and needle not in f.detail:
            continue
        out.append(f.detail)
    return out


def delete_suggestions(rep):
    """报告里所有「建议删掉该注释」的键（排序去重）。"""
    return sorted({f.key for f in rep.findings if "建议删掉该注释" in f.detail})


DELETE_TAG = "建议删掉该注释"


# ══════════════════════════════════════════════════════════════════════════
# ① 第 77 条：「待核实」注释判据
# ══════════════════════════════════════════════════════════════════════════

# ── 「点名」的解析：字段名只认 bibcheck.REQUIRED（must + nice）──

_union = set()
for _spec in bibcheck.REQUIRED.values():
    _union.update(_spec.get("must", ()))
    _union.update(_spec.get("nice", ()))

check("① 认字段名的表 == bibcheck.REQUIRED 的 must+nice 并集（不另列一份）",
      set(bibreport.KNOWN_FIELD_NAMES) == _union,
      f"{len(bibreport.KNOWN_FIELD_NAMES)} 个字段")
check("① `edition` 在表里（@book 的 nice 字段——本缺陷的关键）",
      "edition" in bibreport.KNOWN_FIELD_NAMES
      and ("edition" in bibcheck.REQUIRED["book"]["nice"]
           or "edition" in bibcheck.REQUIRED["book"]["must"]),
      f"book.must={bibcheck.REQUIRED['book']['must']} book.nice={bibcheck.REQUIRED['book']['nice']}")
check("① 注释里点名 `booktitle` 不会被误认成 `title`",
      bibreport.pointed_fields(["% ⚠ 待核实：booktitle 的编者与书名（源稿作「马蒂尼奇. 语言哲学」）"])
      == ["booktitle"],
      repr(bibreport.pointed_fields(["% ⚠ 待核实：booktitle 的编者与书名（源稿作「马蒂尼奇. 语言哲学」）"])))
check("① 注释里点名多个字段（真实稿 ChenBo2023 形态）→ 全部认出",
      bibreport.pointed_fields(["% ⚠ 待核实：journal / volume / number / pages（无法访问官方来源）"])
      == ["journal", "number", "pages", "volume"], "")
check("① 只写来源、没点名任何字段的注释 → 认不出字段",
      bibreport.pointed_fields(["% 来源：WorldCat 书目页（未记下 URL）", "% 类型已改：@misc → @article"]) == [],
      "")
check("① 原因里出现 `URL`/`edition` 这类普通词，不会凭空点出 `url`/`edition` 之外的字段",
      bibreport.pointed_fields(["% ⚠ 待核实：publisher（未记下官方 URL，初版信息见 WorldCat）"]) == ["publisher"],
      repr(bibreport.pointed_fields(["% ⚠ 待核实：publisher（未记下官方 URL，初版信息见 WorldCat）"])))

check("① `两说` / `疑为` / `暂按…保留` / `未核实` → 判定为「悬着的问题」",
      all(bibreport.value_still_in_doubt([m]) for m in
          ("两说", "疑为 \"Press\" 之误", "暂按源稿原文保留", "未核实出版地全称")), "")
check("① `暂缺` / `未直接给出卷号` / `已按官方来源核实，可删` → **不是**悬着的问题（补上后才是过期注释）",
      not any(bibreport.value_still_in_doubt([m]) for m in
              ("CNKI 目次页未直接给出卷号，暂缺", "已按官方来源核实，可删", "已按译著体例补入")), "")

# ── 真实稿夹具：`2026年第5期(90)/10 Bo_Chen/reference.bib` 里 8 条带「待核实」的条目 ──
#    （原文一字不改地抄录；数字 15 等就是原稿里的值——本测试只读字符串，不碰原稿）
REAL_FIXTURE = """\
% 来源：未查到（无法联网核实；以下按源稿原文保留，仅修正作者字段体例）
% ⚠ 待核实：publisher（源稿作 "Oxford University Pres"，疑为 "Oxford University Press" 之误，未核实）
% ⚠ 待核实：address（源稿作 "Oxford"，未核实出版地全称）
@book{Martin2025,
  author    = {Martin, B.},
  title     = {The Epistemology of Logic},
  publisher = {Oxford University Pres},
  address   = {Oxford},
  year      = {2025}
}

% 来源：https://www.jstor.org/stable/20010234
% ⚠ 待核实：journal（源稿作 "Proceedings of the American Philosophical Association"，官方刊名为 "Proceedings and Addresses of the American Philosophical Association"，两说）
@article{Maddy2002,
  author  = {Maddy, P.},
  title   = {A Naturalistic Look at Logic},
  journal = {Proceedings and Addresses of the American Philosophical Association},
  volume  = {76},
  number  = {2},
  pages   = {61--90},
  year    = {2002}
}

% 来源：Harvard University Press 书目页；初版信息见 WorldCat 书目页
% ⚠ 待核实：address（源稿原文作 Englewood，但 Prentice Hall 1970 年初版出版地通常记作 Englewood Cliffs, NJ，未查到官方页面确认）
% ⚠ 待核实：edition（源稿原文提到 2nd edition, Cambridge, MA: Harvard University Press, 1986，与 1970 年 Prentice Hall 版为不同版本，本条按 1970 年初版著录，未查到官方页面确认版次写法）
@book{Quine1970,
  author    = {Quine, W. V. O.},
  title     = {Philosophy of Logic},
  publisher = {Prentice Hall},
  address   = {Englewood},
  year      = {1970}
}

% 来源：https://www.worldcat.org/title/1420033660 （WorldCat 书目页，2026-10-15 查）
% ⚠ 待核实：edition（源稿提到 2021 年 Wiley Blackwell 第二增订版，但本条 year=2007 对应初版；两版信息混在一条里，建议确认要引哪一版）
@book{Williamson2007,
  author    = {Williamson, T.},
  title     = {The Philosophy of Philosophy},
  publisher = {Blackwell Publishing},
  address   = {Oxford},
  year      = {2007}
}

% 来源：CNKI 知网《逻辑学研究》2022 年第 5 期目次（https://www.cnki.net）
% ⚠ 待核实：volume（该刊 2022 年为第 15 卷，CNKI 目次页未直接给出卷号，暂缺）
@article{ChenBo2022,
  author  = {陈波},
  title   = {逻辑例外论的演变},
  journal = {逻辑学研究},
  volume  = {15},
  number  = {5},
  pages   = {38--50},
  year    = {2022},
  userf   = {cn}
}

% 来源：未查到（《哲学与文化》为台湾辅仁大学刊物，无公开可访问的官方卷期页；CNKI 未收录该刊）
% ⚠ 待核实：journal / volume / number / pages（无法访问官方来源，暂按源稿原文保留）
@article{ChenBo2023,
  author  = {陈波},
  title   = {逻辑例外论及其困境},
  journal = {哲学与文化},
  volume  = {50},
  number  = {2},
  pages   = {3--21},
  year    = {2023},
  userf   = {cn}
}

% 来源：源稿原文（哈瑞，R. 1998. 科学哲学导论 [M]. 邱仁宗，译. 沈阳：辽宁教育出版社.）
% ⚠ 待核实：author（原文作者为「哈瑞，R.」，即 R. Harrey/Harre 的中译；译者「邱仁宗」原文有而解析条目缺失，已按译著体例补入）
@book{HaRui1998,
  author    = {哈瑞（著），邱仁宗（译）},
  title     = {科学哲学导论},
  publisher = {辽宁教育出版社},
  address   = {沈阳},
  year      = {1998},
  userf     = {cn}
}

% 来源：源稿原文（编辑部提供）；出版信息未能在官方来源核实
% ⚠ 待核实：pages（源稿未给出页码）
% ⚠ 待核实：booktitle 的编者与书名（源稿作「马蒂尼奇. 语言哲学」，未查到对应官方书目页）
@incollection{Tarski2019,
  author    = {塔斯基（著），牟博（译）},
  title     = {真理的语义概念和语义学的基础},
  booktitle = {语言哲学},
  editor    = {马蒂尼奇},
  publisher = {商务印书馆},
  address   = {北京},
  year      = {2019},
  userf     = {cn}
}
"""

_rep_real = bibreport.analyze(REAL_FIXTURE)
_flagged = delete_suggestions(_rep_real)

check("① 真实稿夹具：改后只剩 2 条「建议删掉该注释」",
      _flagged == ["ChenBo2022", "HaRui1998"], f"{len(_flagged)} 条：{_flagged}")
check("① 反例 Quine1970（点名 edition 而 edition 缺）→ **不许**建议删",
      "Quine1970" not in _flagged, "")
check("① 反例 Williamson2007（点名 edition 而 edition 缺）→ **不许**建议删",
      "Williamson2007" not in _flagged, "")
check("① 反例 Maddy2002（journal「两说」）→ **不许**建议删",
      "Maddy2002" not in _flagged, "")
check("① 反例 Martin2025（publisher「疑为…之误，未核实」）→ **不许**建议删",
      "Martin2025" not in _flagged, "")
check("① 反例 ChenBo2023（「无法访问官方来源，暂按源稿原文保留」）→ **不许**建议删",
      "ChenBo2023" not in _flagged, "")
check("① Tarski2019（点名 pages 而 pages 缺）→ **不许**建议删",
      "Tarski2019" not in _flagged, "")
check("① 正例 ChenBo2022（注「卷号暂缺」但 volume 已补）→ 可以建议删",
      "ChenBo2022" in _flagged, "")
check("① 正例 HaRui1998（点名 author，author 已填齐）→ 可以建议删",
      "HaRui1998" in _flagged, "")

# 不变量：凡被判「过期」的条目，其「待核实」注释绝没有点名一个仍然缺着的字段
_by_key = {e.key: e for e in _rep_real.entries}
_bad = []
for _k in _flagged:
    _e = _by_key.get(_k)
    _named = bibreport.pointed_fields([c for c in _e.comments if "待核实" in c])
    _still = [n for n in _named if not (_e.fields.get(n, "") or "").strip()]
    if _still:
        _bad.append((_k, _still))
check("① 不变量：被判「过期」的条目，点名的字段一个都不缺", not _bad, str(_bad))

# 口径开关（第 7 条 vs 第 6 条的分界，见 `bibreport.VALUE_STILL_IN_DOUBT`）：
# 关掉「悬着的问题」那一档 → 退化成「只看点名字段是否还缺」，多出 3 条误判。
_keep_markers = bibreport.VALUE_STILL_IN_DOUBT
try:
    bibreport.VALUE_STILL_IN_DOUBT = ()
    _flagged_loose = delete_suggestions(bibreport.analyze(REAL_FIXTURE))
finally:
    bibreport.VALUE_STILL_IN_DOUBT = _keep_markers
check("① 口径开关：关掉「悬着的问题」档 → 退化成 5 条（多出 Maddy2002/Martin2025/ChenBo2023）",
      _flagged_loose == ["ChenBo2022", "ChenBo2023", "HaRui1998", "Maddy2002", "Martin2025"],
      f"{_flagged_loose}")
check("① 口径开关：恢复后回到交付口径（7 条注释 → 只剩 2 条建议删）",
      delete_suggestions(bibreport.analyze(REAL_FIXTURE)) == ["ChenBo2022", "HaRui1998"], "")

# ── 单条形态：反例 / 正例 ──

# 反例 1：@book 的 edition 是 **nice** 不是 must——旧判据 missing_must 为空 → 误判「都已填齐」
QUINE_SHAPE = """\
% ⚠ 待核实：edition（源稿原文提到 2nd edition，与初版为不同版本，未查到官方页面确认版次写法）
@book{Shape1,
  author    = {Quine, W. V. O.},
  title     = {Philosophy of Logic},
  publisher = {Prentice Hall},
  address   = {Englewood},
  year      = {1970}
}
"""
_s1 = bibreport.analyze(QUINE_SHAPE)
check("① 反例（nice 字段）：注释点名 edition 而 edition 缺 → 不许建议删",
      len(_s1.entries) == 1 and not details(_s1, "Shape1", bibreport.LEVEL_INFO, DELETE_TAG), "")
check("① 反例（nice 字段）：edition 仍按「建议补字段」提示出来",
      bool(details(_s1, "Shape1", bibreport.LEVEL_INFO, "`edition`")), "")

# 正例 1：注释说「已按官方来源核实，可删」且字段已填
RESOLVED_SHAPE = """\
% ⚠ 待核实：edition（已按官方来源核实，edition = {2} 已补齐，可删）
@book{Shape2,
  author    = {Williamson, T.},
  title     = {The Philosophy of Philosophy},
  publisher = {Blackwell Publishing},
  address   = {Oxford},
  edition   = {2},
  year      = {2007}
}
"""
_s2 = bibreport.analyze(RESOLVED_SHAPE)
check("① 正例（已核实、可删）：可以建议删",
      bool(details(_s2, "Shape2", bibreport.LEVEL_INFO, DELETE_TAG)), "")

# 正例 2：注释点名 volume，volume 已填齐
FILLED_SHAPE = """\
% ⚠ 待核实：volume（官网只有在线优先版，卷号后来已补）
@article{Shape3,
  author  = {Chen, B.},
  title   = {T},
  journal = {J},
  volume  = {15},
  number  = {5},
  pages   = {38--50},
  year    = {2022}
}
"""
_s3 = bibreport.analyze(FILLED_SHAPE)
check("① 正例（点名字段已填齐）：可以建议删",
      bool(details(_s3, "Shape3", bibreport.LEVEL_INFO, DELETE_TAG)), "")

# 正例 3：注释没点名任何字段、must 字段都齐 → 真过期注释
NOFIELD_SHAPE = """\
% ⚠ 待核实：这条注释什么字段都没点名，只是历史遗留
@article{Shape4,
  author  = {Chen, B.},
  title   = {T},
  journal = {J},
  volume  = {15},
  number  = {5},
  pages   = {38--50},
  year    = {2022}
}
"""
_s4 = bibreport.analyze(NOFIELD_SHAPE)
check("① 正例（没点名任何字段、must 齐）：可以建议删",
      bool(details(_s4, "Shape4", bibreport.LEVEL_INFO, DELETE_TAG)), "")

# 反例 2（值两说）：注释点名字段已填，但值还悬着
DOUBT_SHAPE = """\
% ⚠ 待核实：journal（源稿作 X，官方刊名为 Y，两说）
@article{Shape5,
  author  = {Maddy, P.},
  title   = {T},
  journal = {Y},
  volume  = {76},
  number  = {2},
  pages   = {61--90},
  year    = {2002}
}
"""
_s5 = bibreport.analyze(DOUBT_SHAPE)
check("① 反例（值两说）：字段填着也不许建议删",
      len(_s5.entries) == 1 and not details(_s5, "Shape5", bibreport.LEVEL_INFO, DELETE_TAG), "")

# 反例 3：注点名的字段已填，但 must 字段缺（missing_must 非空）→ 不许建议删
MUST_MISSING_SHAPE = """\
% ⚠ 待核实：edition（已按官方来源核实，edition = {2} 已补齐，可删）
@book{Shape6,
  author    = {A, B.},
  title     = {T},
  publisher = {P},
  edition   = {2},
  year      = {2020}
}
"""
_s6 = bibreport.analyze(MUST_MISSING_SHAPE)
check("① 反例（must 字段仍缺）：即使点名字段已填，也不许建议删",
      len(_s6.entries) == 1 and not details(_s6, "Shape6", bibreport.LEVEL_INFO, DELETE_TAG), "")
check("① 反例（must 字段仍缺）：address 照旧报成**问题**",
      bool(details(_s6, "Shape6", bibreport.LEVEL_PROBLEM, "`address`")), "")

# 只报不改：判据只影响报告，字符串输入原样不动（本测试全部只传内存字符串，无文件写入）
check("① 只报不改：analyze() 不改写传入的 bib 文本",
      REAL_FIXTURE.endswith("userf     = {cn}\n}\n"), "")


# ══════════════════════════════════════════════════════════════════════════
# ② 第 78 条：任务书第三节条号只能从唯一一处取
# ══════════════════════════════════════════════════════════════════════════

def parse_section3(md):
    """把任务书第三节的编号清单解析成 {条号: 该条正文}（用于与常量对齐）。"""
    items = {}
    cur = None
    for ln in md.splitlines():
        m = re.match(r"^(\d+)\.\s+(.*)$", ln)
        if m:
            cur = int(m.group(1))
            items[cur] = m.group(2)
        elif cur is not None:
            items[cur] += "\n" + ln
    return items


def align_errors(table, items):
    """把 `{语义键: (条号, 锚点)}` 与任务书实际条目逐条对齐，返回不一致清单。"""
    errs = []
    for key, (num, anchor) in table.items():
        if num not in items:
            errs.append(f"{key}: 任务书里没有第 {num} 条")
        elif anchor not in items[num]:
            errs.append(f"{key}: 第 {num} 条正文里找不到锚点 {anchor!r}")
    return errs


_items = parse_section3(refmark.FORMAT_MD)

check("② 权威清单 = refmark.FORMAT_MD 的「## 三、」那一节",
      "## 三、" in refmark.FORMAT_MD.splitlines()[0], refmark.FORMAT_MD.splitlines()[0])
check("② 任务书第三节恰好渲染出第 1–11 条（连续、不重号）",
      set(_items) == set(range(1, 12)), f"实际 {sorted(_items)}")

_align = align_errors(bibreport.SECTION3_CLAUSES, _items)
check("② SECTION3_CLAUSES 与任务书第三节逐条对齐（锚点落在对应条号里）",
      not _align, str(_align))

# 测试自己的反证：把条号改错一位，对齐函数必须变红（否则这个测试挡不住第 78 条）
_anchors = {k: v[1] for k, v in bibreport.SECTION3_CLAUSES.items()}
for _key, _wrong in (("missing_field_note", 5), ("no_guessing", 6),
                     ("all_known_fields", 2), ("type_changed", 7)):
    _broken = dict(bibreport.SECTION3_CLAUSES)
    _broken[_key] = (_wrong, _anchors[_key])
    check(f"② 反证：把 `{_key}` 错成第 {_wrong} 条 → 对齐立刻报错（能变红）",
          bool(align_errors(_broken, _items)), f"锚点 {_anchors[_key]!r}")

check("② 条号值与任务书一致（key=1 / 写全字段=3 / 待核实=6 / 不要猜=7 / 类型已改=8 / 来源=11）",
      [bibreport.clause_no(k) for k in
       ("key_unchanged", "all_known_fields", "missing_field_note",
        "no_guessing", "type_changed", "source_note")] == [1, 3, 6, 7, 8, 11], "")

# `refmark` 自己另有两处文字引用同一批条号（SPEC_MD），必须与常量一致
check("② refmark.SPEC_MD 里写「第三节第 6 条」 → 与 missing_field_note 一致",
      "第三节第 6 条" in refmark.SPEC_MD and bibreport.clause_no("missing_field_note") == 6, "")
check("② refmark.SPEC_MD 里写「第三节第 11 条」 → 与 source_note 一致",
      "第三节第 11 条" in refmark.SPEC_MD and bibreport.clause_no("source_note") == 11, "")

# 源码里不许再有硬写的「第三节第 <数字> 条」（那正是第 78 条的成因）
_src = open(BIBREPORT_PY, encoding="utf-8").read()
_hard = re.findall(r"第三节第\s*[0-9]", _src)
check("② bibreport.py 里没有硬写的「第三节第 <数字> 条」", not _hard, str(_hard))

# ── 报告里真正渲染出来的消息，必须用唯一那一处的号 ──

def _clause_fragment(name):
    return f"第三节第 {bibreport.clause_no(name)} 条"


# 路径 A：must 字段缺 + 没有「待核实」注释 → 教编辑照第 6 条写注释
MUST_MISSING_NO_NOTE = """\
@article{NoNote2026,
  author  = {Chen, B.},
  title   = {T},
  journal = {J},
  number  = {3},
  pages   = {1--26},
  year    = {2026}
}
"""
_rep_a = bibreport.analyze(MUST_MISSING_NO_NOTE)
_da = details(_rep_a, "NoNote2026", bibreport.LEVEL_PROBLEM, "查不到就照")
check("② 缺必填字段、无注释：教编辑照第 6 条（不是第 5 条）写「待核实」",
      len(_da) == 1 and _clause_fragment("missing_field_note") in _da[0]
      and "第三节第 5 条" not in _da[0], str(_da)[:120])

# 路径 B：must 字段缺 + 注释点名了它 → 「已按第 6 条注明」
_rep_b = bibreport.analyze(
    "% ⚠ 待核实：volume（官网只有在线优先版）\n"
    "@article{Named2026,\n"
    "  author  = {Chen, B.},\n"
    "  title   = {T},\n"
    "  journal = {J},\n"
    "  number  = {3},\n"
    "  pages   = {1--26},\n"
    "  year    = {2026}\n"
    "}\n"
)
_db = details(_rep_b, "Named2026", bibreport.LEVEL_INFO, "已按任务书第三节")
check("② 缺必填字段、注释点名了它：写「已按…第 6 条注明」（不是第 5 条）",
      len(_db) == 1 and _clause_fragment("missing_field_note") in _db[0]
      and "第三节第 5 条" not in _db[0], str(_db)[:120])

# 路径 C：新增字段（diff）→ 第 7 条「不确定的值不要猜」
_ORIG_C = """\
@article{Add2020,
  author  = {Chen, B.},
  title   = {T},
  journal = {J},
  volume  = {1},
  number  = {2},
  pages   = {1--10},
  year    = {2020}
}
"""
_NEW_C = """\
@article{Add2020,
  author  = {Chen, B.},
  title   = {T},
  journal = {J},
  volume  = {1},
  number  = {2},
  pages   = {1--10},
  month   = {nov},
  year    = {2020}
}
"""
_rep_c = bibreport.analyze(_NEW_C, _ORIG_C)
_dc = details(_rep_c, "Add2020", bibreport.LEVEL_INFO, "新增字段")
check("② 新增字段：写第 7 条「不确定的值不要猜」（不是第 6 条）",
      len(_dc) == 1 and _clause_fragment("no_guessing") in _dc[0]
      and "第三节第 6 条" not in _dc[0], str(_dc)[:120])

# 路径 D：字段被删（diff）→ 第 3 条「每条写全所有已知字段」
_ORIG_D = _NEW_C
_NEW_D = _ORIG_C
_rep_d = bibreport.analyze(_NEW_D, _ORIG_D)
_dd = details(_rep_d, "Add2020", bibreport.LEVEL_PROBLEM, "字段被删掉")
check("② 字段被删：写第 3 条「每条写全所有已知字段」（不是第 2 条）",
      len(_dd) == 1 and _clause_fragment("all_known_fields") in _dd[0]
      and "第三节第 2 条" not in _dd[0], str(_dd)[:120])

# 路径 E：类型被改（diff）→ 第 8 条；带 `% 类型已改` 注释时也写第 8 条
_TYPED_ORIG = """\
@book{Typed2020,
  author    = {Zhang, S.},
  title     = {T},
  publisher = {P},
  address   = {A},
  year      = {2020}
}
"""
_TYPED_NEW = """\
@incollection{Typed2020,
  author    = {Zhang, S.},
  title     = {T},
  booktitle = {B},
  publisher = {P},
  address   = {A},
  pages     = {1--2},
  year      = {2020}
}
"""
_rep_e = bibreport.analyze(_TYPED_NEW, _TYPED_ORIG)
_de = details(_rep_e, "Typed2020", bibreport.LEVEL_PROBLEM, "类型被改")
check("② 类型被改：写第 8 条（不是第 7 条）",
      len(_de) == 1 and _clause_fragment("type_changed") in _de[0]
      and "第三节第 7 条" not in _de[0], str(_de)[:120])

_TYPED_NEW_NOTE = "% 类型已改：@book → @incollection（源稿是书中章节）\n" + _TYPED_NEW
_rep_e2 = bibreport.analyze(_TYPED_NEW_NOTE, _TYPED_ORIG)
_de2 = details(_rep_e2, "Typed2020", bibreport.LEVEL_INFO, "类型已改")
check("② 类型已改且上方有注释：写第 8 条（不是第 7 条），且降为提示",
      len(_de2) == 1 and _clause_fragment("type_changed") in _de2[0]
      and "第三节第 7 条" not in _de2[0], str(_de2)[:120])

# 路径 F：key 被改名（diff）→ 第 1 条
_KEY_OLD = """\
@book{OldKey2020,
  author    = {Zhang, S.},
  title     = {Same Title},
  publisher = {P},
  address   = {A},
  year      = {2020}
}
"""
_KEY_NEW = _KEY_OLD.replace("OldKey2020", "NewKey2020")
_rep_f = bibreport.analyze(_KEY_NEW, _KEY_OLD)
_df = details(_rep_f, "OldKey2020", bibreport.LEVEL_PROBLEM, "疑似改了 key")
check("② key 被改名：写第 1 条「key 一律不改」",
      len(_df) == 1 and _clause_fragment("key_unchanged") in _df[0], str(_df)[:120])

# 报告整篇：真实稿夹具渲染出来的号里，不许再出现「第 5 条」当条号
_md_real = bibreport.render(_rep_real)
check("② 真实稿报告里「第三节第 5 条」一次都不出现",
      "第三节第 5 条" not in _md_real, "")
check("② 真实稿报告里出现了修正后的「第三节第 6 条」",
      "第三节第 6 条" in _md_real, "")

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
