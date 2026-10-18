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
  ④ 边界：空 bib / 只有注释的 bib 返回空文本；无注释 bib 结果与从前一致（结构/顺序）。

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

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
