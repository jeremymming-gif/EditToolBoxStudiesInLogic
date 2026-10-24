#!/usr/bin/env python3.14
r"""第五十二批 phaseB：C 类新口径**默认 ON** 的开关语义测试。

背景：v9b（默认 OFF + `--c-class-union` 全量）PASS 后用户拍板切默认 ON。
切换必须只有一处出处（`footnotes.C_CLASS_UNION_DEFAULT`），三层语义都要钉住：
  ① `c_class_union_enabled()`：None/缺省 → True；False → 回退；True → 开；
  ② `convert.py` CLI：默认（不带开关）→ ON；`--no-c-class-union` → OFF 回退；
     `--c-class-union` → 显式开；后写的开关优先（argparse 标准语义）；
  ③ `build_docx(c_class_union=None)` → 解析为 ON；`False/True` 原样透传；
  ④ 低层 `parse_docx()` 默认仍是 False（`docx_audit.py` 冻结基线不能变）。

只跑 argparse/签名/一次被桩替换的调用；不跑 xelatex、不读稿件库、不写仓库。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_batch52_default_on.py
"""
import inspect
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse  # noqa: E402

import convert  # noqa: E402
from slcn import build  # noqa: E402
from slcn import docxsource  # noqa: E402
from slcn import footnotes  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ── ① 总开关语义 ─────────────────────────────────────────────────────────
check("① C_CLASS_UNION_DEFAULT 默认开（第五十二批 phaseB 切换）",
      footnotes.C_CLASS_UNION_DEFAULT is True)
check("① enabled()：缺省/None → ON", footnotes.c_class_union_enabled() is True
      and footnotes.c_class_union_enabled(None) is True)
check("① enabled()：False → OFF 回退", footnotes.c_class_union_enabled(False) is False)
check("① enabled()：True → 显式开", footnotes.c_class_union_enabled(True) is True)

# ── ② convert CLI：真实 argparse 定义 + 解析逻辑 ─────────────────────────
_ap = argparse.ArgumentParser(prog="convert.py")
convert._add_c_class_union_args(_ap)


def _resolve(*argv):
    return convert.resolve_c_class_union(_ap.parse_args(list(argv)))


check("② CLI 默认（不带开关）→ ON", _resolve() is True,
      f"ns={_ap.parse_args([])}")
check("② CLI --no-c-class-union → OFF 回退", _resolve("--no-c-class-union") is False)
check("② CLI --c-class-union → 显式开", _resolve("--c-class-union") is True)
check("② CLI 后写开关优先（--no 后 --c → ON）",
      _resolve("--no-c-class-union", "--c-class-union") is True)
check("② CLI 后写开关优先（--c 后 --no → OFF）",
      _resolve("--c-class-union", "--no-c-class-union") is False)
check("② CLI default=None（默认值不硬编码 False，唯一出处是总开关）",
      _ap.parse_args([]).c_class_union is None)

# ── ③ build_docx：None → 总开关解析；显式值原样透传 ─────────────────────
_sig = inspect.signature(build.build_docx)
check("③ build_docx 签名默认 None（None → 总开关）",
      _sig.parameters["c_class_union"].default is None)


class _Stop(Exception):
    pass


_seen = {}


def _fake_parse_docx(path, quote_threshold=105, c_class_union=False,
                     bibdb_store=None):
    _seen["c_class_union"] = c_class_union
    raise _Stop("stop before real parse")


_orig_parse = docxsource.parse_docx
docxsource.parse_docx = _fake_parse_docx
try:
    for _kw, _want in (({}, True),
                       ({"c_class_union": None}, True),
                       ({"c_class_union": False}, False),
                       ({"c_class_union": True}, True)):
        _seen.clear()
        try:
            build.build_docx("/nonexistent.docx", "/tmp", verbose=False, **_kw)
        except _Stop:
            pass
        except Exception as _e:  # noqa: BLE001 —— 解析桩抛 Stop 之外都算失败
            _seen["error"] = repr(_e)
        check(f"③ build_docx({_kw} ) 传给 parse_docx 的 c_class_union={_want}",
              _seen.get("c_class_union") is _want, repr(_seen))
finally:
    docxsource.parse_docx = _orig_parse

# ── ④ 低层 parse_docx 默认仍 False（docx_audit 冻结基线不变）────────────
_p = inspect.signature(docxsource.parse_docx)
check("④ 低层 parse_docx(c_class_union) 默认仍 False",
      _p.parameters["c_class_union"].default is False)

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
