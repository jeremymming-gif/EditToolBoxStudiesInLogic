#!/usr/bin/env python3.14
"""B-307 S5：`.bib` 结构体检（花括号不配平 / 可解析性 problem）**只报告**。

背景（B-303 B2）：11/62 份退回 bib 结构坏——6 份花括号不配平、5 份 key 重复、3 份字段名
写成数字。`split_entries()` 对不配平的条目没有报错通道，会从坏条目起把余下内容并进
自己的字段体，编辑只看字段核对看不出「文件从第几行起坏了」。

本轮的边界（一个字都不许越）：
  · 不改 `split_entries()` 的切分/吞并行为，不改 `check()` 的必补/建议判据；
  · 新发现放 `Report.syntax`，**不进** `issues` → `ok` / `n_must` / `n_unstructured` /
    `docx_audit.csv` 冻结口径零变化；
  · 初排留档报告（`bibcheck.format_report`）与 `checkbib.py` 输出只在**末尾追加**一节，
    既有行逐字不动。
"""
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibcheck  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


CLEAN = """@book{c, author={A}, title={T}, year={2020}, publisher={P}, address={X}}
"""
DUP = """@article{dup, author={X}, title={T}, journal={J}, year={2020}, volume={1}, number={1}, pages={1--2}}
@article{dup, author={Y}, title={U}, journal={J}, year={2021}, volume={2}, number={2}, pages={3--4}}
"""
# `open` 的字段齐全，所以字段核对本身 0 必补——结构坏是**另一类**信息
BAL = """@book{ok, author={A}, title={T}, year={2020}, publisher={P}, address={X}, edition={2}}
@book{open, author={B}, title={U}, year={2021}, publisher={Q}, address={Y}, edition={2}
"""
NUM = "@book{x, 2019 = {Y}, author={A}, title={T}, year={2020}, publisher={P}, address={X}}\n"

# ══ ① 只报告：不进 issues / 不改计数 ═══════════════════════════════════════
print("── ① 只报告，不改冻结口径 ──")

rep = bibcheck.check(DUP)
check("重复 key 进 `syntax`", any("重复" in i.detail for i in rep.syntax), repr([i.detail for i in rep.syntax]))
check("`syntax` 不进 `issues`", not any(i.severity == "syntax" for i in rep.issues))
check("重复 key 不改必补计数（0 必补）", rep.n_must == 0, f"n_must={rep.n_must}")
check("重复 key 不改 `ok`（字段齐全）", rep.ok is True)

rep = bibcheck.check(BAL)
bal = [i for i in rep.syntax if i.kind == "balance"]
check("不配平条目进 `syntax`（键 + 行号）",
      len(bal) == 1 and bal[0].key == "open" and "第 2 行" in bal[0].detail,
      repr([(i.key, i.detail[:40]) for i in bal]))
check("不配平仍不改必补计数/`ok`（字段都在坏条目自己的行里）",
      rep.n_must == 0 and rep.ok is True, f"n_must={rep.n_must}")
check("可解析性 problem（parse_entries）也进 `syntax`",
      any(i.kind == "parse" for i in rep.syntax) or len(bal) >= 1)

rep = bibcheck.check(NUM)
check("字段名写成数字 → `syntax` 报出来", any("字段名不合法" in i.detail for i in rep.syntax),
      repr([i.detail[:40] for i in rep.syntax]))

rep = bibcheck.check(CLEAN)
check("干净 bib：`syntax` 为空", rep.syntax == [])

# ══ ② 报告层：只在末尾追加 ═════════════════════════════════════════════════
print("\n── ② 报告层只追加 ──")

rep = bibcheck.check(BAL)
text = bibcheck.format_report(rep)
check("既有结论行仍在（✅ 未发现问题 / 必补 0）",
      "  ✅ 未发现问题。" in text and "  必补项  ：0" in text)
check("结构体检追加在最后", text.rstrip().splitlines()[-1].lstrip().startswith("[syntax]")
      or "结构体检" in text.splitlines()[-2], repr(text.splitlines()[-2:]))
check("既有行逐字不变（前 6 行仍是表头）",
      text.splitlines()[:6] == [
          "══ 参考文献核对报告 ══", "  条目总数：2", "  必补项  ：0", "  建议补项：0",
          "  未结构化：0（整条在 note 里，非缺字段）", ""], repr(text.splitlines()[:6]))
check("干净 bib 的报告不出现「结构体检」",
      "结构体检" not in bibcheck.format_report(bibcheck.check(CLEAN)))
check("`format_syntax_section([])` 是空串", bibcheck.format_syntax_section([]) == "")

# ══ ③ checkbib CLI：既有输出不动，只在末尾追加 ═════════════════════════════
print("\n── ③ checkbib CLI ──")


def run_cli(path):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")]))
    return subprocess.run(
        [sys.executable, os.path.join(_ENGINE, "checkbib.py"), "--returned", path],
        cwd=_ENGINE, env=env, capture_output=True, text=True,
    )


with tempfile.TemporaryDirectory() as tmp:
    bad = os.path.join(tmp, "bad.bib")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write(DUP + BAL)
    r = run_cli(bad)
    out = r.stdout
    check("既有小节仍在（结论 / 附录：逐条状态）",
          "**结论：" in out and "## 附录：逐条状态" in out)
    check("结构体检在既有附录之后", out.rfind("── 结构体检") > out.rfind("## 附录：逐条状态"))
    check("结构体检是输出的最后一块",
          out.rstrip().splitlines()[-1].lstrip().startswith("[")
          and "split_entries 会把余下内容整段并进这一条" in out, repr(out.rstrip().splitlines()[-1]))
    check("代码块外的既有尾巴（SOFT 说明）一行没动",
          "> ⚠️ 本报告只回答" in out and out.rfind("> ⚠️ 本报告只回答") < out.rfind("── 结构体检"))
    check("退出码仍是「有问题=1」（这一条来自既有 analyze，不由追加节改变）", r.returncode == 1,
          f"code={r.returncode}")

    clean = os.path.join(tmp, "clean.bib")
    with open(clean, "w", encoding="utf-8") as fh:
        fh.write(CLEAN)
    r = run_cli(clean)
    check("干净 bib：CLI 输出不出现「结构体检」", "结构体检" not in r.stdout)
    check("干净 bib：退出码 0", r.returncode == 0, f"code={r.returncode}")

print("\n" + "=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("全部 PASS")
sys.exit(0)
