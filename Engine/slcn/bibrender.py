"""bib 字段输出顺序（B-222 B 段 A1–A5 的**单点**）。

行为纪律（spec A）：`_TYPE_ORDER` **只决定排序、不筛字段**——entry 里的实际键
一律输出（BibTeX 允许任意字段）；类型序里没有的键按 entry 内顺序补在后面。
旧实现 `footnotes.entry_to_bib()` 直接按 `_TYPE_ORDER.get(etype, misc)` 遍历，
白名单外字段被静默裁掉（实测 90-06 @book 丢 journal/volume/number/pages、
90-07 @incollection 丢 editor、@inproceedings 退 misc 整条裁、90-10 丢 edition）。

`footnotes._TYPE_ORDER` 保留为 re-export（与这里是**同一对象**，不是第二份定义）；
`footnotes.entry_to_bib()` 改调 `bib_field_order()`。
"""

#: 类型 → 惯用字段序（只排序，不筛字段）。各类型内**既有键的相对顺序保持不变**
#: （非目标案例的产物逐字节不变）；新增键只插进空档。
#: · book：A1/A4（journal/volume/number/pages/booktitle 来自源稿）
#: · incollection：A2/A5（editor/edition/volume/number）
#: · inproceedings：A3（旧表无此行 → 退 misc 字段序，容器/卷/页全裁）
_TYPE_ORDER = {
    "book": ["author", "title", "booktitle", "editor", "edition", "journal",
             "volume", "number", "pages", "publisher", "address", "year",
             "url", "userf", "note"],
    "article": ["author", "title", "journal", "booktitle", "editor", "volume",
                "number", "year", "pages", "url", "userf", "note"],
    "incollection": ["author", "title", "booktitle", "editor", "edition",
                     "volume", "number", "publisher", "address", "year",
                     "pages", "url", "userf", "note"],
    "inproceedings": ["author", "title", "booktitle", "editor", "volume",
                      "number", "publisher", "address", "year", "pages",
                      "url", "userf", "note"],
    # B-217②（中文学位论文按引用库既有口径）：`school=学校`、
    # `address=学位类型原字串`（「博士论文」/「硕士学位论文」）。`thesis` 是往期
    # 成品里的另一种写法（institution/type）。
    "phdthesis": ["author", "title", "school", "address", "year", "userf", "note"],
    "mastersthesis": ["author", "title", "school", "address", "year", "userf", "note"],
    "thesis": ["author", "title", "type", "institution", "school", "address",
               "year", "userf", "note"],
    # 未知类型（techreport 等）也走这张序 + 实际键补集——**不再退 misc 裁字段**。
    "misc": ["author", "title", "year", "url", "userf", "note"],
}


#: entry 里的**内部元数据**（不是 BibTeX 字段，不得输出）。B-222 B 段 A 并集上线后
#: 实测 88-02 泄漏 `confidence = {low}`：`footnotes.parse_entry` 的候选来自
#: `dataclasses.asdict(footref.Parsed)`（含 confidence）。`userf` 是合法字段，不在列。
_INTERNAL_KEYS = frozenset({"confidence"})


def bib_field_order(etype: str, present) -> list[str]:
    """返回 .bib 字段的输出顺序：**类型序 ∩ 实际键** + **实际键补集**（只筛内部键）。

    `etype` 未知时用 `misc` 序兜底；`present` 是 entry 里**有值**的键（可为任意
    iterable）。返回列表只含 `present` 里的**可输出**键，顺序 = 类型序在前、其余按
    `present` 原序补在末尾（dict 保序）；私有键（`_type` 等 `_` 开头）与
    `_INTERNAL_KEYS`（内部元数据）不输出。
    """
    keys = [str(k) for k in (present or ())
            if isinstance(k, str) and not k.startswith("_")
            and k not in _INTERNAL_KEYS]
    order = _TYPE_ORDER.get(str(etype or "misc").strip() or "misc",
                            _TYPE_ORDER["misc"])
    out: list[str] = []
    seen: set[str] = set()
    for k in order:
        if k in keys and k not in seen:
            out.append(k)
            seen.add(k)
    for k in keys:
        if k not in seen:
            out.append(k)
            seen.add(k)
    return out
