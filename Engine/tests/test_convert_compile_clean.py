#!/usr/bin/env python3.14
r"""重编译前清上一轮中间产物（第 141 条，2026-10-18；期 87 子案例 9 实测）。

缺陷
----
`convert.compile_pdf()` 直接 xelatex → biber → xelatex×2。**第一次 xelatex 会读旧的
`.bbl`**（biber 还没重跑），若上一轮产物目录里留着坏数据，会在 step 1 就报错、被
判「编译未通过」——哪怕这一轮生成的 `.bib` 已经修好。

实测：期 87 子案例 9（10 Wei_Chen）第一次跑时 `reference.bib` 的 author 带裸 `&`
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

import shutil  # noqa: E402

shutil.rmtree(TMP, ignore_errors=True)

print()
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
