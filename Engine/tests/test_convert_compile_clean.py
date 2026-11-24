#!/usr/bin/env python3.14
r"""重编译前清上一轮中间产物（第 141 条，2026-10-18；期 87 子案例 9 实测）。

缺陷
----
`convert.compile_pdf()` 直接 xelatex → biber → xelatex×2。**第一次 xelatex 会读旧的
`.bbl`**（biber 还没重跑），若上一轮产物目录里留着坏数据，会在 step 1 就报错、被
判「编译未通过」——哪怕这一轮生成的 `.bib` 已经修好。

实测：期 87 子案例 9（10 Jie_Chen）第一次跑时 `reference.bib` 的 author 带裸 `&`
（`expand_note_entries` 未转义，第 141 条已修），编译在 step 3 失败，留下坏 `.bbl`；
修好 bib 后**重跑同一目录**，step 1 直接读旧 `.bbl` 报 `Misplaced alignment tab
character &`，rc=2。重跑同目录是编辑的常规操作，不能把上一轮残留算到这一轮头上。

修法：编译前删掉本工具生成的中间产物（.aux/.bbl/.bcf/.blg/.run.xml/.out/.toc），
`.tex` / `.bib` / `.pdf` / 模板样式的用户可见产物一律不动。

本套件钉住：① 七类中间产物被清掉；② 用户可见产物不动；③ 其它 stem 的文件不动；
④ 幂等（第二次调用返回空）。

只写临时目录；不跑 xelatex、不联网、不读稿件库。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_convert_compile_clean.py
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import convert  # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix="slcn_compileclean_")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def touch(name):
    with open(os.path.join(TMP, name), "w", encoding="utf-8") as f:
        f.write("x")


for _n in ("Demo.aux", "Demo.bbl", "Demo.bcf", "Demo.blg", "Demo.run.xml",
           "Demo.out", "Demo.toc",
           "Demo.tex", "Demo.pdf", "Demo.bib", "SLCN-MIX.sty", "other.aux"):
    touch(_n)

_removed = convert.clean_stale_compile_artifacts(TMP, "Demo")
check("① 七类中间产物全部清掉",
      sorted(_removed) == sorted(["Demo.aux", "Demo.bbl", "Demo.bcf", "Demo.blg",
                                  "Demo.run.xml", "Demo.out", "Demo.toc"]),
      str(_removed))
check("① 中间产物已不在磁盘上",
      not any(os.path.exists(os.path.join(TMP, n)) for n in _removed))
check("② 用户可见产物不动（.tex / .pdf / .bib / 模板 .sty）",
      all(os.path.exists(os.path.join(TMP, n))
          for n in ("Demo.tex", "Demo.pdf", "Demo.bib", "SLCN-MIX.sty")))
check("③ 别的 stem 的文件不动（other.aux）",
      os.path.exists(os.path.join(TMP, "other.aux")))
check("④ 幂等：第二次调用返回空", convert.clean_stale_compile_artifacts(TMP, "Demo") == [])


# B-391：每条 TeX 错误只配到下一条 ! 之前的最后一个 l.###。
from slcn import convert_compile as CC  # noqa: E402
from unittest.mock import patch  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_log_path = os.path.join(TMP, "Demo.log")
_log_text = """l.9 before any error
! First error.
context
l.10 earlier location
l.11 nearest location
! Second error has no location.
context
! Third error.
l.30 third location
! Fourth error.
l.40 must not leak
"""
with open(_log_path, "w", encoding="utf-8") as f:
    f.write(_log_text)
_expected = ["! First error. l.11 nearest location", "! Second error has no location.",
             "! Third error. l.30 third location"]
check("B-391 最多三条、最近行号、无行号保持原文且不误配下一错",
      CC._tail_log_lines(_log_path) == _expected, str(CC._tail_log_lines(_log_path)))
check("B-391 contains 过滤也不能跨过未选中的下一条 !",
      CC._tail_log_lines(_log_path, contains="Second") == [_expected[1]])
with patch.object(CC.shutil, "which", return_value="fake-xelatex"), \
        patch("slcn.platform.run_no_window", return_value=SimpleNamespace(returncode=1)):
    _ok, _msg = CC.compile_pdf(TMP, "Demo")
check("B-391 编译失败文案保留三条错误及各自行号",
      not _ok and all(part in _msg for part in _expected) and "l.40" not in _msg)
with open(_log_path, "w", encoding="utf-8") as f:
    f.write("No errors\nl.99 not an error\n")
check("B-391 无错日志行为不变", CC._tail_log_lines(_log_path) == [])
check("B-391 缺失/不可读日志返回空",
      CC._tail_log_lines(os.path.join(TMP, "missing.log")) == [] and CC._tail_log_lines(TMP) == [])
with open(_log_path, "w", encoding="utf-8") as f:
    f.write("INFO\nERROR one\nl.1 ignored\nERROR two\nERROR three\nERROR four\n")
check("B-391 biber ERROR 提取保持原样",
      CC._tail_log_lines(_log_path, prefix="", contains="ERROR") == ["ERROR one", "ERROR two", "ERROR three"])

import shutil  # noqa: E402

shutil.rmtree(TMP, ignore_errors=True)

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
