#!/usr/bin/env python3.14
r"""B-264 v2：`bibparse.parse_entries()` 对「行内 % 注释」的剥离。

背景（期 71 Ting_Lu / 期 73 Lei_Zhang 实测的 8 条真实条目）
--------------------------------------------------------------
BibTeX 里 `%` 本来是**行注释**，但真实 .bib 常把人工批注直接写在条目行尾：

    @book{a19, %20,22,23
      ...
      year = {2003},%1958
    }

旧实现只在 `_blank_comment_lines()` 里抹**整行**注释（行首 `%`），行内 `%` 之后
的原文留在字段体里 → `_scan_top_fields()` 报「字段体里有无法解析的片段」→
`Entry.broken=True`，整条被下游跳过。8 条目标条目全部因此被误判。

修法（本批）
------------
1. 新增 `_strip_inline_comments(bib)`：与 `_blank_comment_lines()` 同构的**长度不变**
   抹除器——整份文件累计花括号深度与顶层引号状态，只在 **depth≤1 且不在引号里**
   把未转义的 `%` 到行尾替换成空格；depth≥2（字段值花括号内）的 `%` 是**数据**，
   原样保留；`\%` 这类被反斜杠转义的 `%` 不算注释。
2. `_in_line_comment(text, pos)` 同步改成转义感知（数前缀反斜杠奇偶），避免把
   `\%` 当注释起点、把后面整行吞掉。
3. `parse_entries()` 用 `_strip_inline_comments(_blank_comment_lines(bib))` 作扫描副本；
   原文件不动、偏移与行号仍按等长替换对齐（`Entry.start/end/line` 可直接用于原文定
   位与回写）。

语义红线（钉住）
----------------
- 行首 `%` 整行注释仍按旧规则抹除（不产生条目、不影响行号）；
- `%` 出现在**字段值花括号内**时是数据，不能删（`note = {50% of cases}`）；
- `\%` 是转义百分号，不是注释起点（同一行后面的字段不能被吞）；
- 剥离只发生在扫描副本上：`_strip_inline_comments()` 返回串与原串**等长、行数相同**
  （下游 aibib / 回写依赖偏移对齐）。

本套件纯离线**合成夹具**：不读稿件库、不含任何真实文献原文，只写内存。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" python3.14 tests/test_bibparse_inline_pct.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibparse  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _parse(bib):
    return bibparse.parse_entries(bib)


def _keyed(entries):
    return {e.key: e for e in entries}


def _problems(findings):
    return [f for f in findings if f.level == bibparse.LEVEL_PROBLEM]


# ── 夹具：行内 % 的四种真实形态（内容虚构）─────────────────────────────────
# ① 条目头行内注释（`@book{a, %20,22,23`）：键后逗号之后就是注释。
# ② 字段值后行内注释（`year = {2003},%1958`）。
# ③ 键后无空格行内注释（`@misc{a25,%26--30`）。
# ④ 作者行内注释（`author = {…},%…`）。
INLINE_HEAD = """% 合成夹具：条目头行内注释（BibTeX 语义 = 注释）
@book{h19, %20,22,23
  author = {Roe, R.},
  title  = {A synthetic book},
  publisher = {Synthetic Press},
  year   = {2003},%1958
}
"""

INLINE_NOSPACE = """@misc{n25,%26--30
  author = {Doe, J.},
  title  = {A synthetic note},
  year   = {1999}
}
"""

INLINE_AUTHOR = """@article{a7,
  author  = {Roe, R. and others},%Roe, R. et al. (synthetic annotation)
  title   = {A synthetic article},
  journal = {Synthetic Journal},
  volume  = {12},
  number  = {3},
  pages   = {45--67},
  year    = {2001}
}
"""

# 干净条目（对照：行内 % 剥离不应影响无注释条目）
CLEAN = """@incollection{c1,
  author    = {Doe, J.},
  title     = {A synthetic chapter},
  booktitle = {A synthetic collection},
  pages     = {1--10},
  year      = {2010}
}
"""

# 整行注释（行首 %）：旧规则保留
WHOLE_LINE = """% @book{ghost, 这一整行都是注释，不应产生条目
@book{real,
  author = {Poe, E.},
  title  = {A real synthetic entry},
  year   = {1990}
}
"""

# 字段值花括号里的 % 是数据（不能删、也不能吞后面的字段）。
PERCENT_IN_VALUE = """@misc{v1,
  note  = {uses 50% of the synthetic cases},
  title = {A synthetic percentage note},
  year  = {2002}
}
"""

# 转义百分号 \\%：不是注释起点，同一行后面的内容都是数据。
ESCAPED_PCT = """@misc{e1,
  note  = {about 50\\% of cases, and 25\\% of controls},
  title = {A synthetic escaped percentage},
  year  = {2004}
}
"""

# 多行字段值里跨行的 %（字段值内 = 数据）
MULTILINE_PCT = """@misc{m1,
  note = {first synthetic line with 10% data,
          second synthetic line with 20% data},
  title = {A synthetic multiline note},
  year  = {2005}
}
"""

# ── ① / ② 条目头 + 字段尾行内注释 ──────────────────────────────────────────
bib = INLINE_HEAD + INLINE_NOSPACE + CLEAN
entries, findings = _parse(bib)
by = _keyed(entries)

check("① 条目头行内注释不再判 broken", by.get("h19") is not None and not by["h19"].broken,
      f"broken={getattr(by.get('h19'), 'broken', None)}")
check("① 条目头行内注释：键/类型正确",
      by.get("h19") is not None and by["h19"].etype == "book", str(sorted(by)))
check("② 字段值后行内注释：year 干净（无 `%1958` 残渣）",
      by.get("h19") is not None and by["h19"].fields.get("year") == "2003",
      repr(by.get("h19").fields.get("year") if by.get("h19") else None))
check("② 字段值后行内注释：同条目其它字段不受影响",
      by.get("h19") is not None
      and (by["h19"].fields.get("author"), by["h19"].fields.get("title"))
      == ("Roe, R.", "A synthetic book"),
      repr(by.get("h19").fields if by.get("h19") else None))
check("③ 键后无空格行内注释也不 broken",
      by.get("n25") is not None and not by["n25"].broken,
      f"broken={getattr(by.get('n25'), 'broken', None)}")
check("③ 键后无空格：etype/title 正确",
      by.get("n25") is not None and by["n25"].etype == "misc"
      and by["n25"].fields.get("title") == "A synthetic note",
      repr(by.get("n25").fields if by.get("n25") else None))
check("③ 无行内注释的干净条目不受影响",
      by.get("c1") is not None and not by["c1"].broken
      and by["c1"].fields.get("pages") == "1--10",
      f"broken={getattr(by.get('c1'), 'broken', None)}")
check("①②③ 全库无 problem 级 findings", _problems(findings) == [],
      repr([f.message for f in _problems(findings)]))
check("①②③ 条目数 = 3（注释不产生伪条目）", len(entries) == 3, str(len(entries)))

# ── ④ 作者字段行内注释 ────────────────────────────────────────────────────
entries, findings = _parse(INLINE_AUTHOR)
e = entries[0] if entries else None
check("④ 作者行内注释不 broken", e is not None and not e.broken,
      f"broken={getattr(e, 'broken', None)}")
check("④ 作者行内注释：author 只留真值",
      e is not None and e.fields.get("author") == "Roe, R. and others",
      repr(e.fields.get("author") if e else None))
check("④ 作者行内注释：后续字段完整（journal/volume/number/pages/year）",
      e is not None and (e.fields.get("journal"), e.fields.get("volume"),
                         e.fields.get("number"), e.fields.get("pages"),
                         e.fields.get("year"))
      == ("Synthetic Journal", "12", "3", "45--67", "2001"),
      repr(e.fields if e else None))

# ── ⑤ 整行注释（旧语义不变）──────────────────────────────────────────────
entries, findings = _parse(WHOLE_LINE)
keys = [x.key for x in entries]
check("⑤ 行首 % 整行注释仍被忽略（不产生 ghost 条目）", "ghost" not in keys, str(keys))
check("⑤ 整行注释后的真实条目正常解析",
      keys == ["real"] and entries[0].fields.get("year") == "1990", str(keys))
check("⑤ 整行注释不产生 problem findings", _problems(findings) == [],
      repr([f.message for f in _problems(findings)]))

# ── ⑥ 字段值内的 % 是数据（depth≥2 不剥离）────────────────────────────────
entries, findings = _parse(PERCENT_IN_VALUE)
e = entries[0] if entries else None
check("⑥ 字段值里的 % 原样保留",
      e is not None and "50% of the synthetic cases" in e.fields.get("note", ""),
      repr(e.fields.get("note") if e else None))
check("⑥ 字段值含 % 时同条目其它字段不被吞",
      e is not None and e.fields.get("title") == "A synthetic percentage note"
      and e.fields.get("year") == "2002", repr(e.fields if e else None))
check("⑥ 字段值含 % 不 broken", e is not None and not e.broken,
      f"broken={getattr(e, 'broken', None)}")

entries, findings = _parse(ESCAPED_PCT)
e = entries[0] if entries else None
check("⑦ `\\%` 不是注释起点：转义百分号前后都是数据",
      e is not None and "about 50\\% of cases" in e.fields.get("note", ""),
      repr(e.fields.get("note") if e else None))
check("⑦ `\\%` 行尾字段不被吞（year 仍在）",
      e is not None and e.fields.get("year") == "2004", repr(e.fields if e else None))

entries, findings = _parse(MULTILINE_PCT)
e = entries[0] if entries else None
check("⑦ 跨行字段值里的 % 也是数据",
      e is not None and "10% data" in e.fields.get("note", "")
      and "20% data" in e.fields.get("note", ""), repr(e.fields.get("note") if e else None))
check("⑦ 跨行 % 值不 broken", e is not None and not e.broken,
      f"broken={getattr(e, 'broken', None)}")

# ── ⑧ 剥离器本身：等长、行数相同、偏移对齐 ─────────────────────────────────
raws = [INLINE_HEAD, INLINE_NOSPACE, INLINE_AUTHOR, CLEAN, WHOLE_LINE,
        PERCENT_IN_VALUE, ESCAPED_PCT, MULTILINE_PCT]
for i, raw in enumerate(raws, 1):
    stripped = bibparse._strip_inline_comments(bibparse._blank_comment_lines(raw))
    check(f"⑧ 夹具{i} 剥离结果与原串等长",
          len(stripped) == len(raw), f"{len(stripped)} vs {len(raw)}")
    check(f"⑧ 夹具{i} 行数不变",
          stripped.count("\n") == raw.count("\n"),
          f"{stripped.count(chr(10))} vs {raw.count(chr(10))}")

# 行内注释按空格抹除后，同一行上 % 之后应全是空格
stripped = bibparse._strip_inline_comments(bibparse._blank_comment_lines(INLINE_HEAD))
line0 = stripped.split("\n")[1]      # 第 0 行是整行注释（本夹具首行）
check("⑧ 行内注释被抹成等长空格", line0.startswith("@book{h19,") and line0.rstrip() == "@book{h19,",
      repr(line0))
check("⑧ 行内注释抹除不动机体原文（author 行无损）",
      "author = {Roe, R.}," in stripped, repr(stripped.split(chr(10))[2]))

# Entry 偏移能定位回**原文件**（等长替换保证）
entries, _f = _parse(INLINE_HEAD + INLINE_NOSPACE)
full = INLINE_HEAD + INLINE_NOSPACE
for e in entries:
    seg = full[e.start:e.end + 1]
    check(f"⑧ Entry({e.key}) 偏移切片以 @ 开头且以 }} 收尾",
          seg.lstrip().startswith("@") and seg.rstrip().endswith("}"),
          repr(seg[:20]))
check("⑧ 偏移等长替换下 line 号仍指向原文件的同一行",
      entries[0].line == 2, str(entries[0].line))

# ── ⑨ `_in_line_comment()` 转义感知 ───────────────────────────────────────
_LINE_PCT = "title = {x}, % tail"
check("⑨ 裸 % 判为注释",
      bibparse._in_line_comment(_LINE_PCT, len(_LINE_PCT)) is True)
_LINE_NOPCT = "title = {x},"
check("⑨ 行内无 % 不判注释",
      bibparse._in_line_comment(_LINE_NOPCT, len(_LINE_NOPCT)) is False)
_LINE_ESC = "note = {a 50\\% b}"
check("⑨ 单个 \\% 不判注释",
      bibparse._in_line_comment(_LINE_ESC, len(_LINE_ESC)) is False)
_LINE_2BS = "note = {a}\\\\% b"
check("⑨ 双反斜杠 + % 判注释（\\\\% 的 % 未被转义）",
      bibparse._in_line_comment(_LINE_2BS, len(_LINE_2BS)) is True)

# ── ⑩ 综合：混合夹具全条目 broken=False ───────────────────────────────────
entries, findings = _parse(INLINE_HEAD + INLINE_NOSPACE + INLINE_AUTHOR
                           + CLEAN + WHOLE_LINE + PERCENT_IN_VALUE + ESCAPED_PCT)
check("⑩ 混合夹具无 broken 条目", all(not e.broken for e in entries),
      repr([(e.key, e.broken) for e in entries if e.broken]))
check("⑩ 混合夹具无 problem findings", _problems(findings) == [],
      repr([f.message for f in _problems(findings)]))
check("⑩ 混合夹具条目数 = 7", len(entries) == 7, str(len(entries)))

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
