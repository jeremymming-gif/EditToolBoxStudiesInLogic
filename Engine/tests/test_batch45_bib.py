#!/usr/bin/env python3.14
"""第四十五批 B：文献类 P0/P1 修复的判据测试（bibnorm / bibcheck / footnotes / checkbib）。

钉住：
  ① bibnorm.parse_fields：字段名允许连字符（`bdsk-url-1`/`date-added`/WOS 导出），
     值里 `{\"a}` 的转义引号不再截断（89-01 biber syntax error 根因）；垃圾字段剔除；
  ② split_entries：`@Comment{…}`（无逗号）不再吞掉下一条目（88-05 悬空引用根因）；
  ③ footnotes.classify：作者信息/基金脚注永不提升；中文期刊脚注（年份+第N期/学报/出版社）
     识别为文献（86-03）；纯编号定位（`[20]，第36页`）不误判；
  ④ checkbib 发现性提示：页码型悬空键 / 编号引用无 \cite / note-only 条目（C 类口径）。

不联网、不写库、不跑 corpus。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_batch45_bib.py
"""
import os
import pathlib
import subprocess
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_ENGINE = _HERE.parent
for _p in (str(_ENGINE), str(_ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibcheck, bibnorm, footnotes  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


CLI = str(_ENGINE / "checkbib.py")
ENV = dict(os.environ, PYTHONPATH=str(_ENGINE) + os.pathsep + str(_ENGINE / "vendor"),
           PYTHONIOENCODING="utf-8")


def run_checkbib(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE), env=ENV)


# ── ① bibnorm：连字符字段 + 转义引号 + 垃圾字段 ──────────────────────────
SRC = r'''@article{FAN_2025,
  author = {Fan, Jie},
  date-added = {2025-12-17 16:27:19 +0800},
  bdsk-url-1 = {https://doi.org/10.1/x},
  title = {A UNIFORM APPROACH},
  year = {2025}
}
@article{AGM85,
  AUTHOR = "Alchourr\'{o}n, C.  and G{\"a}rdenfors, P.  and Makinson, D.",
  TITLE = "On the logic of theory change",
  JOURNAL = "Journal of Symbolic Logic",
  YEAR = "1985"
}
'''
out, notes = bibnorm.normalize_bib(SRC)
check("① 连字符字段不再拆成非法数字字段（无 `1 =`；bdsk-* 剔除）",
      "\n  1        =" not in out and "bdsk" not in out.lower()
      and "date-added" in out)
check("① 引号值内 `G{\\\"a}rdenfors` 不再截断（89-01 根因）",
      'G{\\"a}rdenfors, P.' in out)

# ── ② split_entries：@Comment 块不吞下一条目（88-05 根因）──────────────
BIB_COMMENT = r'''@book{a1990,
  author = {A, B}, title = {T1}, year = {1990}
}
@Comment{jabref-meta: databaseType:bibtex;}
@InProceedings{Mkrtychev1997_LP,
  author = {Mkrtychev, Alexey},
  title = {Models for the logic of proofs},
  year = {1997}
}
'''
keys = bibcheck.entry_keys(BIB_COMMENT)
check("② @Comment 后的条目仍被解析（Mkrtychev1997_LP 不悬空）",
      "Mkrtychev1997_LP" in keys and "a1990" in keys, str(sorted(keys)))
_norm, _ = bibnorm.normalize_bib(BIB_COMMENT)
check("② bibnorm 亦不把 @Comment 吞成条目",
      "@InProceedings{Mkrtychev1997_LP" in _norm and "jabref-meta" not in _norm)

# ── ③ footnotes：作者信息不提升 / 中文期刊脚注识别 / 定位不误判 ─────────
f_author = footnotes.classify(
    "作者简介：任静，（1984-）女，唐山师范学院讲师。基金项目：国家社科基金项目（22AZX019）。")
check("③ 作者信息/基金脚注 → pure_note（87-7 不再生成 junk bib 条目）",
      f_author.kind == "pure_note")
f_cn = footnotes.classify(
    "杨正文：鼓藏节仪式与苗族社会组织，西南民族学院学报，2000年第5期，第13-26页。")
check("③ 中文期刊脚注（年份+第N期/学报）→ pure_ref（86-03 根因）",
      f_cn.kind == "pure_ref", f_cn.kind)
f_loc = footnotes.classify(
    "这是广义论证对“论证”的最新定义。（[20]，第36页；[21]，第8页）")
check("③ 纯编号定位（[20]，第36页）→ pure_note（90-08 不误判）",
      f_loc.kind == "pure_note", f_loc.kind)

# ── ④ checkbib 发现性提示 ───────────────────────────────────────────────
with tempfile.TemporaryDirectory(prefix="batch45_bib_") as tmp:
    bib_path = os.path.join(tmp, "reference.bib")
    tex_path = os.path.join(tmp, "main.tex")
    with open(bib_path, "w", encoding="utf-8") as fh:
        fh.write(r'''@article{Yalcin2007,
  author = {Yalcin, Seth},
  title = {Epistemic modals},
  journal = {Mind},
  year = {2007}
}
@misc{ref01,
  note = {Harry Frankfurt, “The Importance of What We Care About,” p. 262}
}
''')
    with open(tex_path, "w", encoding="utf-8") as fh:
        fh.write(r"""\documentclass{article}
\begin{document}
示例\parencite{Yalcin2007, p985}认为……[1] 还有编号引用 [12]。
\end{document}
""")
    r = run_checkbib("--returned", bib_path, "--tex", tex_path)
    out_text = r.stdout + r.stderr
    check("④ 页码写进引用键 → 提示（p985）",
          "疑似把页码范围写进引用键" in out_text and "p985" in out_text)
    check("④ note-only 条目 → C 类口径提示",
          "只有 note、没有结构化" in out_text)
    # 编号引用场景：正文没有任何 cite 命令（P0-1 体例）
    numbered_tex = os.path.join(tmp, "numbered.tex")
    with open(numbered_tex, "w", encoding="utf-8") as fh:
        fh.write(r"""\documentclass{article}
\begin{document}
这是编号引用 [1]，还有 [12] 与 [3]。
\end{document}
""")
    r2 = run_checkbib("--returned", bib_path, "--tex", numbered_tex)
    out2 = r2.stdout + r2.stderr
    check("④ 编号引用无 \\cite → P0-1 体例提示（不猜映射）",
          "编号引用" in out2 and "不猜映射" in out2)

print()
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS!r}")
    sys.exit(1)
print("失败 0 项（全部通过）")
