#!/usr/bin/env python3.14
r"""bibnorm：**整行注释**的旧条目不得被当成真条目（**第 133 条**，2026-10-18）。

缺陷
----
`slcn/bibnorm.py` 的 `split_entries()` 用 `re.finditer(r"@\w+\s*\{")` 全文匹配，**不跳过
`%` 注释行**，且匹配从 `@` 起（`% ` 前缀被丢掉）→ 作者 `.bib` 里注释掉的模板/重复条目
（`% @article{,`、`% @misc{z16,`）被当成有效条目写进产物 `reference.bib`（空键
`@article{,` + 重复键 `z16`）→ biber 语法报错、**整份 bib 报废**。实测 2023(71)04：
115 处 undefined citation、参考文献区未生成、19 页 vs 人工 20 页。

本套件钉住（纯离线、合成夹具；不读稿件库）：
  ① `strip_bib_comments()`：只清整行注释、保留换行/行内 `%`；
  ② `split_entries()`：注释掉的空键条目/完整旧条目不进；正常条目顺序与数量不变；
  ③ `normalize_bib()`：最小复现不再输出 `@article{,`；重复键只剩一条；字段值里
     `50\%` / `A % B` 不受影响；条目体内整行注释掉的字段不变成真字段；
  ④ 边界：空 bib / 只有注释的 bib 返回空文本；无注释 bib 结果与从前一致（结构/顺序）；
  ⑤ `bibcheck.split_entries()` 与 bibnorm **同一口径**（第 133 条遗留）：注释掉的
     非空键条目也不进、正常条目/顺序不变、行内 `%` 不受影响、`check()` 总数不含注释条目。

只写内存；不联网、不读稿件库、不跑 corpus.py / build.sh。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_bibnorm.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibcheck  # noqa: E402
from slcn import bibnorm  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ══════════════════════════════════════════════════════════════════════════
# ① strip_bib_comments：只清整行注释，换行与行内 % 原样
# ══════════════════════════════════════════════════════════════════════════
_stripped = bibnorm.strip_bib_comments("% 文件头\n@misc{a, title={X}}\n  % @article{,  \n")
check("① 整行注释被清空、换行保留（行结构不塌）",
      _stripped == "\n@misc{a, title={X}}\n\n", repr(_stripped))
check("① 行内 `%` 不清（不做聪明截断）：note={50\\%}",
      bibnorm.strip_bib_comments("@misc{a, note={50\\%}}") == "@misc{a, note={50\\%}}")
check("① 值中间的行内 `%` 不清：title={A % B}",
      bibnorm.strip_bib_comments("@misc{a, title={A % B}}") == "@misc{a, title={A % B}}")
check("① 缩进的整行注释也清（行首可选空白 + %）",
      bibnorm.strip_bib_comments("    % @article{,") == "")
check("① CRLF 行尾保留（只清注释内容，不吞 \\r\\n）",
      bibnorm.strip_bib_comments("% 注释\r\n@misc{a,}\r\n") == "\r\n@misc{a,}\r\n")

# ══════════════════════════════════════════════════════════════════════════
# ② split_entries：注释条目不进、正常条目顺序不变
# ══════════════════════════════════════════════════════════════════════════
_ENTRY_HEAD = r"^@(\w+)\s*\{\s*([^,]+)\s*,"


def keys_of(text):
    import re

    return [
        re.match(_ENTRY_HEAD, head).group(2).strip()
        for head, _fields, _tail in bibnorm.split_entries(text)
        if re.match(_ENTRY_HEAD, head)
    ]


_EMPTY_COMMENTED = "% @article{,\n% \tauthor={},\n% }\n"
check("② 注释掉的空键条目（% @article{,）不进条目列表",
      keys_of(_EMPTY_COMMENTED + "@misc{a, title={X}}\n") == ["a"],
      str(keys_of(_EMPTY_COMMENTED + "@misc{a, title={X}}\n")))
_WHOLE_COMMENTED = "% @misc{z16,\n%   author = {旧},\n%   title = {旧条目},\n% }\n"
check("② 注释掉的**完整旧条目**（多行、真键）不进",
      keys_of(_WHOLE_COMMENTED + "@misc{z16, author={新}}\n") == ["z16"])
check("② 正常条目不被误删、顺序不变（含条目之间/之后的注释）",
      keys_of("% 头\n@book{a, title={A}}\n% 中间注释\n@article{b, title={B}}\n% 尾\n")
      == ["a", "b"])
check("② 返回值结构不变：仍是 (head, fields, tail) 三元组",
      all(len(e) == 3 and all(isinstance(x, str) for x in e)
          for e in bibnorm.split_entries("@misc{a, title={X}}")))

# ══════════════════════════════════════════════════════════════════════════
# ③ normalize_bib：最小复现 + 重复键 + 值内 % + 条目体内注释字段
# ══════════════════════════════════════════════════════════════════════════
_min = "% @article{,\n% author={},\n% }\n@misc{a, title={X}}\n"
_out, _notes = bibnorm.normalize_bib(_min)
check("③ 最小复现：normalize_bib 不再输出 `@article{,`",
      "@article{," in _min and "@article{," not in _out and _out.count("@misc{a,") == 1,
      repr(_out))

_real_shape = """% 文件头注释（旧模板残留）
@article{a51,
  author = {甲},
  title = {乙}
}

% @misc{z16,
%   author = {旧},
%   title = {旧条目},
% }
@misc{z16,
  author = {丙},
  title = {丁}
}

% @article{,
%   author={},
%   year={},
% }
"""
_out2, _n2 = bibnorm.normalize_bib(_real_shape)
check("③ 真实稿形态：注释旧条目不进、真实重复键只剩一条、空键消失",
      "@misc{z16," in _out2
      and _out2.count("@misc{z16,") == 1
      and "@article{," not in _out2
      and "@article{a51," in _out2,
      repr(_out2[:60]) + " …")
check("③ 只有注释的 bib → 空文本（不造空条目）",
      bibnorm.normalize_bib("% foo\n  % @article{,\n") == ("", []))
check("③ 空 bib → 空文本", bibnorm.normalize_bib("") == ("", []))
_vals = "@misc{a, note={50\\%},\n  title={A % B}}\n"
_vals_out, _ = bibnorm.normalize_bib(_vals)
check("③ 字段值里的 `50\\%` / `A % B` 不受影响",
      "50\\%" in _vals_out and "A % B" in _vals_out, repr(_vals_out))
_commented_field = """@misc{a,
  % title = {被注释掉的标题},
  author = {X},
  year = {2020}
}
"""
_cf_out, _ = bibnorm.normalize_bib(_commented_field)
check("③ 条目体内**整行注释**的字段不变成真字段（不出现「被注释掉的标题」）",
      "被注释掉的标题" not in _cf_out and "author = {X}" in _cf_out
      and "year   = {2020}" in _cf_out,
      repr(_cf_out))

# ══════════════════════════════════════════════════════════════════════════
# ④ 边界：无注释 bib 结构/顺序与从前一致；Word 通道的窄版函数不受影响
# ══════════════════════════════════════════════════════════════════════════
_plain = "@book{b1,\n  title = {Book}\n}\n\n@article{a1,\n  title = {Paper}\n}\n"
check("④ 无注释 bib：split_entries 条目数/顺序不变",
      keys_of(_plain) == ["b1", "a1"])
_plain_norm, _ = bibnorm.normalize_bib(_plain)
check("④ 无注释 bib：normalize 结果与逐条解析一致（两条都在）",
      _plain_norm.count("@book{b1,") == 1 and _plain_norm.count("@article{a1,") == 1)
_word_bib = """@book{a,
  title = {Book},
  edition = {2nd}
}

% ── 由文末参考文献区生成 ──

@article{b,
  title = {Paper}
}
"""
_we_out, _we_notes = bibnorm.move_book_editions(_word_bib)
check("④ Word 通道窄版 move_book_editions：正常条目照旧、版次照旧并入 title",
      "title = {Book, (2nd)}" in _we_out and "@article{b," in _we_out
      and "由文末参考文献区生成" not in _we_out,
      repr(_we_out)[:120])

# ══════════════════════════════════════════════════════════════════════════
# ⑤ bibcheck.split_entries 与 bibnorm 口径统一（第 133 条遗留）
# ══════════════════════════════════════════════════════════════════════════
_COMMENTED_NONEMPTY = """% @misc{z16,
%   author = {旧},
%   title = {注释掉的旧条目},
% }
@misc{z16,
  author = {新}
}
"""
_bc = bibcheck.split_entries(_COMMENTED_NONEMPTY)
check("⑤ bibcheck.split_entries：注释掉的**非空键**条目不进（z16 只剩真实那条）",
      [k for _e, k, _b in _bc] == ["z16"], str([(e, k) for e, k, _b in _bc]))
_bc2 = bibcheck.split_entries(
    "% 头\n@book{a, title={A}}\n% 中间注释\n@article{b, title={B}}\n% @article{,\n% }\n")
check("⑤ bibcheck.split_entries：正常条目不被误删、顺序不变",
      [k for _e, k, _b in _bc2] == ["a", "b"], str([k for _e, k, _b in _bc2]))
_bc3 = bibcheck.split_entries("@misc{a, note={50\\%}, title={A % B}}\n")
check("⑤ bibcheck.split_entries：行内/字段值内 `%` 不影响条目",
      len(_bc3) == 1 and _bc3[0][1] == "a")
_bc4 = bibcheck.split_entries("% @misc{z16,\n% }\n@misc{real, title={X}}\n")
check("⑤ bibcheck.split_entries：返回值结构不变（仍是 (类型, 键, 字段体) 三元组）",
      all(len(e) == 3 and isinstance(e[0], str) and isinstance(e[1], str)
          for e in _bc4) and [k for _e, k, _b in _bc4] == ["real"])
_rep = bibcheck.check(_COMMENTED_NONEMPTY)
check("⑤ check() 的条目总数不再把注释掉的旧条目算进去（total = 1）",
      _rep.total == 1, str(_rep.total))

# ══════════════════════════════════════════════════════════════════════════
# ⑥ expand_note_entries 写回的字段要转义（第 141 条，期 87 子案例 9 实测）
# ══════════════════════════════════════════════════════════════════════════
# 症状：Word 脚注体例的条目（整条在 note 里）被 expand_note_entries 拆成结构化字段时，
#       **原样**把解析值写进 .bib —— 既不转义 &（LaTeX 特殊字符），也不把 `A & B`
#       规范成 BibTeX 的 `A and B`。实测 10 Jie_Chen 的 author
#       `van Eemeren, F. H. & van Haaften, T.` 原样落进 bib，biblatex 在
#       `\namepartsuffix` 里遇到 `&` 报 Misplaced alignment tab，整篇编译失败。
_NOTE_BIB = """@misc{ref03,
  note      = {Frans H. van Eemeren \\& T. van Haaften, “The Making of Argumentation Theory: A Pragma-dialectical View”, Argumentation, Vol. 37, No. 3, 2003, pp. 341--376},
}
"""
_exp_out, _exp_notes = bibnorm.expand_note_entries(_NOTE_BIB)
# B-248：该夹具（van Eemeren 引号条）在 spec §5 ② 的类型重判后是 @article（原 @misc）
check("⑥ expand_note_entries 拆出结构化条目（仍是 1 条）",
      len(_exp_notes) == 1 and "@article{ref03," in _exp_out, str(_exp_notes))
# author 里的 `&` 必须变成 BibTeX 的 `and`（不能是裸 `&`，也不能是转义后的 `\&` 当分隔符）
_auth_line = [ln for ln in _exp_out.splitlines() if ln.strip().startswith("author")][0]
check("⑥ author 字段里的 `&` 规范成 `and`（不出现裸 &）",
      "&" not in _auth_line and " and " in _auth_line, _auth_line.strip())
check("⑥ 其它字段里的 & / % / _ 等被转义（note 里的 `\\&` 保持不重复转义）",
      "\\&" in _exp_out and "\\\\&" not in _exp_out, repr(_exp_out)[:200])
# 反例：本来正常的条目逐字不变（不因为多转义一道而改坏）
_OK_BIB = """@book{b1,
  author = {Dillard, J. P.},
  title  = {Book}
}
"""
_ok_out, _ok_notes = bibnorm.expand_note_entries(_OK_BIB)
check("⑥ 反例：已结构化条目原样保留（不重复转义）",
      _ok_notes == [] and "author = {Dillard, J. P.}" in _ok_out
      and "\\\\&" not in _ok_out
      and _ok_out.count("@book{b1,") == 1, repr(_ok_out)[:120])

# ══════════════════════════════════════════════════════════════════════════
# ⑦ B-107（86-02）：plain book / 英文编号书字段归位；旧三形态逐字节不变
# ══════════════════════════════════════════════════════════════════════════
# 源行取自 86-02 文末 Word 自动编号清单（段 141–153），逐字；期望字段只由行内证据得出。
_B107_LINES = {
    "Wang2012": "[141] 汪奠基：《中国逻辑思想史》，武汉：武汉大学出版社，2012。",
    "Li1985": "[142] 李泽厚：《中国古代思想史论》，合肥：安徽文艺出版社，1985年。",
    "Boas2022": ("[143] Van Emde Boas P, Van Emde Boas G, Xie K, Zhao B. "
                 "Analyzing the logic of sun tzu in “the art of war”, using mind maps[M]. "
                 "Singapore: Springer Nature Singapore Pte Ltd. 2022."),
    "Wang1988": "[146] 王先谦：荀子集解，1988年，北京：中华书局。",
    "Yin1976": "[148] 银雀山汉墓竹简整理小组编：《银雀山汉墓竹简孙子兵法》，1976年，北京：中华书局。",
    "Sun2012": "[150] 孙武撰：《十一家注孙子》，2012年，北京：中华书局。",
    "Li2006": "[152] 李零：《兵以诈立：我读孙子》，2006年，北京：中华书局。",
}
_B107_EXPECT = {
    "Wang2012": {"_type": "book", "author": "汪奠基", "title": "中国逻辑思想史",
                 "address": "武汉", "publisher": "武汉大学出版社", "year": "2012"},
    "Li1985": {"_type": "book", "author": "李泽厚", "title": "中国古代思想史论",
               "address": "合肥", "publisher": "安徽文艺出版社", "year": "1985"},
    "Boas2022": {"_type": "book",
                 "author": "Van Emde Boas P and Van Emde Boas G and Xie K and Zhao B",
                 "title": "Analyzing the logic of sun tzu in “the art of war”, using mind maps",
                 "address": "Singapore",
                 "publisher": "Springer Nature Singapore Pte Ltd.", "year": "2022"},
    "Wang1988": {"_type": "book", "author": "王先谦", "title": "荀子集解",
                 "address": "北京", "publisher": "中华书局", "year": "1988"},
    "Yin1976": {"_type": "book", "author": "银雀山汉墓竹简整理小组编",
                "title": "银雀山汉墓竹简孙子兵法", "address": "北京",
                "publisher": "中华书局", "year": "1976"},
    "Sun2012": {"_type": "book", "author": "孙武撰", "title": "十一家注孙子",
                "address": "北京", "publisher": "中华书局", "year": "2012"},
    "Li2006": {"_type": "book", "author": "李零", "title": "兵以诈立：我读孙子",
               "address": "北京", "publisher": "中华书局", "year": "2006"},
}
for _k, _line in _B107_LINES.items():
    _got = bibnorm._parse_cn_numbered_line(_line)
    check(f"⑦-1 {_k}：plain book / 英文编号书按行内证据归位",
          _got == _B107_EXPECT[_k], repr(_got))

# 旧三形态基准 = **HEAD 版**函数（`git show HEAD:Engine/slcn/bibnorm.py` 于 /tmp 载入）
# 的逐字节输出，写死为期望；新分支不得改动它们。
_HEAD_OLD = [
    ("[1] 亚里士多德：《工具论》，张竹明译，北京：商务印书馆，1984。",
     {"author": "亚里士多德（著），张竹明（译）", "title": "工具论", "year": "1984",
      "_type": "book", "address": "北京", "publisher": "商务印书馆"}),
    ("[2] 李雷：《逻辑例外论的演变》，《逻辑学研究》，2020年第5期。",
     {"author": "李雷", "title": "逻辑例外论的演变", "year": "2020",
      "_type": "article", "journal": "逻辑学研究", "number": "5"}),
    ("[3] 塔斯基：《真理的语义概念》，马蒂尼奇（主编）：《语言哲学》，北京：商务印书馆，2004。",
     {"author": "塔斯基", "title": "真理的语义概念", "year": "2004",
      "_type": "incollection", "booktitle": "语言哲学",
      "note": "马蒂尼奇（主编）（主编）", "address": "北京", "publisher": "商务印书馆"}),
]
for _line, _want in _HEAD_OLD:
    check(f"⑦-2 旧形态与 HEAD 逐字节一致：{_want['title']}",
          bibnorm._parse_cn_numbered_line(_line) == _want,
          repr(bibnorm._parse_cn_numbered_line(_line)))

# 反例：行内证据不足 → None（不猜、不改原条目）。中文期刊 / 英文期刊 / 缺出版信息。
_NEG_LINES = (
    "[7] 李文：《某题名》，某刊名，2020年第3期。",
    "[8] Smith J. Some Title[J]. Journal Name, 2020(3): 12-20.",
    "[9] 王五：某书名，2020年。",
    "[10] 赵六：《某书》，2020年。",
    "[11] J. Smith. Some Book[M]. London: Springer, 2020.",
)
for _line in _NEG_LINES:
    check("⑦-3 不匹配行返回 None（不猜）：" + _line[:16],
          bibnorm._parse_cn_numbered_line(_line) is None,
          repr(bibnorm._parse_cn_numbered_line(_line)))

# ④ repair ②：无匹配 → 条目不替换；命中（中文无《》书 / 英文书）→ 只换该条、键不变。
import copy as _copy


class _Doc:
    pass


def _mk_doc(entries, num2key):
    d = _Doc()
    d.refs = []
    d.bib_entries = entries
    d.body = ""
    d.ref_raw_by_key = {_k: _r for _k, _e, _r in entries}
    d.ref_key_by_num = dict(num2key)
    return d


_neg = ("neg1", {"author": "李文", "title": "某题名", "year": "2020"},
        "[7] 李文：《某题名》，某刊名，2020年第3期。")
_d0 = _mk_doc([_neg], {7: "neg1"})
_before = _copy.deepcopy(_d0.bib_entries)
bibnorm.repair_word_refs(_d0)
check("⑦-4 无匹配时 repair 不替换条目（旧行为不变）", _d0.bib_entries == _before,
      repr(_d0.bib_entries))

_cn_bad = ("Wang1988", {"author": "王先谦：荀子集解", "title": "北京：中华书局",
                        "year": "1988"}, _B107_LINES["Wang1988"])
_en_bad = ("Boas2022",
           {"author": "Van Emde Boas P and Van Emde Boas G and Xie K and Zhao B. "
                      "Analyzing the logic of sun tzu in",
            "title": "the art of war",
            "publisher": "Springer Nature Singapore Pte Ltd. 2022",
            "address": "sing mind maps[M]. Singapore", "year": "2022"},
           _B107_LINES["Boas2022"])
_keep = ("Keep2020", {"author": "张三", "title": "正常题名", "year": "2020"}, "")
_d1 = _mk_doc([_cn_bad, _en_bad, _keep], {146: "Wang1988", 143: "Boas2022"})
bibnorm.repair_word_refs(_d1)
check("⑦-4 repair ②：中文无《》书按行内证据归位、键不变",
      _d1.bib_entries[0] == ("Wang1988", _B107_EXPECT["Wang1988"], _B107_LINES["Wang1988"]),
      repr(_d1.bib_entries[0]))
check("⑦-4 repair ②：英文编号书经新增路由归位（其余规则不动）",
      _d1.bib_entries[1] == ("Boas2022", _B107_EXPECT["Boas2022"], _B107_LINES["Boas2022"]),
      repr(_d1.bib_entries[1]))
check("⑦-4 repair ②：未命中条目原样保留、顺序不变",
      _d1.bib_entries[2] == _keep, repr(_d1.bib_entries[2]))

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
