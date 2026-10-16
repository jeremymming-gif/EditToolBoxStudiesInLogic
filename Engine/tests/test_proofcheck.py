#!/usr/bin/env python3
"""一校核查（第 92 条）：把《排校手册》《流程化经验书》的规范变成可回归的规则。

三条纪律要一起钉住：
  (a) **该报的报** —— 每条规则配正例（含真实稿里出现过的形态）；
  (b) **不该报的不报** —— 注释 / 数学 / 宏名 / 键参数 / 自带 `\\textup` 的命令里的东西一律不算；
      中文规则不去管英文正文，英文规则不去管英文模板里打印的中文题名摘要；
  (c) **自动改只碰无歧义的** —— 脚注里的标点只报不改；半角改全角要连空格一起吃掉；
      位置移动（脚注/引文移到标点之前）要按花括号配对、绝不改坏。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import proofcheck as P  # noqa: E402

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def hits_of(tex: str, rule_id: str, lang: str = "zh"):
    return [h for h in P.check(tex, lang=lang).hits if h.rule_id == rule_id]


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 每条规则：正例与反例 ──")

# 中文半角标点（经验书 预校正·中文）
for rid, bad, good in (("ZH-01", "甲,乙", "甲，乙"), ("ZH-02", "甲.乙", "甲。乙"),
                       ("ZH-03", "甲:乙", "甲：乙"), ("ZH-04", "甲;乙", "甲；乙"),
                       ("ZH-05", "甲?乙", "甲？乙")):
    check(f"(a) {rid} 命中半角并与中文相邻", len(hits_of(f"{bad}。", rid)) == 1, bad)
    check(f"(a) {rid} 反例：已是全角 → 不报", not hits_of(f"{good}。", rid))

check("(a) ZH-01 反例：英文之间的半角逗号**不报**（那是英文正文）",
      not hits_of("a, b and c\n", "ZH-01"))
check("(a) ZH-06 顿号后紧跟引号 → 报，且建议是「删掉顿号」",
      len(hits_of("甲、“乙”\n", "ZH-06")) == 1
      and hits_of("甲、“乙”\n", "ZH-06")[0].replacement == "")
check("(a) ZH-06 反例：正常顿号（甲、乙）不报", not hits_of("甲、乙。\n", "ZH-06"))

check("(a) ZH-07 中文旁的半角括号 → 报（标为噪声大）",
      len(hits_of("他说(是这样)。\n", "ZH-07")) == 1
      and next(r for r in P.RULES if r.id == "ZH-07").noisy)
check("(a) ZH-08 中文旁的半角短杠 → 报（标为噪声大）",
      len(hits_of("具-体。\n", "ZH-08")) == 1
      and next(r for r in P.RULES if r.id == "ZH-08").noisy)
check("(a) ZH-09 第8-12页 → 报，建议用 \\cndash",
      [h.replacement for h in hits_of("第8-12页。\n", "ZH-09")] == ["第8\\cndash 12页"])
check("(a) ZH-09 反例：已是第8\\cndash 12页 → 不报", not hits_of("第8\\cndash 12页。\n", "ZH-09"))

# 脚注与引文位置（手册 §常规 4 / 5；用户 2026-10-15 点名的两条）
check("(a) ZH-10 脚注在逗号之后 → 报（手册：逗号处放逗号前）",
      len(hits_of("这个说法，\\footnote{注}是错的。\n", "ZH-10")) == 1)
check("(a) ZH-10 正例：脚注在逗号之前 → 不报",
      not hits_of("这个说法\\footnote{注}，是错的。\n", "ZH-10"))
check("(a) ZH-11 引文在逗号之后 → 报",
      len(hits_of("见文献，\\parencite{Haack1974}的讨论。\n", "ZH-11")) == 1)
check("(a) ZH-11 正例：引文在逗号之前 → 不报",
      not hits_of("见文献\\parencite{Haack1974}，以及别的。\n", "ZH-11"))
check("(a) ZH-11 反例：**英文稿**里的 `work, \\cite{x}` 不报（手册这条是中文口径）",
      not hits_of("Besides Burgess' work, \\cite{Holliday17} considered it.\n", "ZH-11", lang="en"))
check("(a) ZH-12 句号后的脚注紧跟汉字 → 报（手册：需加 \\,）",
      len(hits_of("完成了。\\footnote{注}这就是结果。\n", "ZH-12")) == 1)

# 数学环境（手册 §数学环境）
check("(a) ZH-13 数学里的三个点 → 报", len(hits_of("$a_1,...,a_n$\n", "ZH-13")) == 1)
check("(a) ZH-13 反例：**正文文本**里的六个点不报（那条规则只管数学区）",
      not hits_of("这里......六个点\n", "ZH-13"))
check("(a) ZH-14 集合里的竖线 → 报，且建议改成 \\mid",
      [h.replacement for h in hits_of("$\\{x|x>5\\}$\n", "ZH-14")] == ["\\{x\\mid x>5\\}"])
check("(a) ZH-14 反例：条件概率的竖线不在集合里 → 不报", not hits_of("$P(A|B)$\n", "ZH-14"))
check("(a) ZH-15 \\mathbf → 报", len(hits_of("$\\mathbf{a+b}=c$\n", "ZH-15")) == 1)

# 引用与版式（手册 §排版指南）
check("(a) ZH-16 图表引用缺波浪号 → 报", len(hits_of("如表\\ref{table1}所示。\n", "ZH-16")) == 1)
check("(a) ZH-17 caption 里用 \\\\ → 报",
      len(hits_of("\\caption{第一行\\\\第二行}\n", "ZH-17")) == 1)
check("(a) ZH-19 人名缩写 J.N. → 报", len(hits_of("J.N. Mohanty 说。\n", "ZH-19")) == 1)

# 英文稿（手册 §英文文章；经验书 预校正·英文）
# 替换只覆盖命中片段（`n(2` → `n (2`），所以断言也只看片段
check("(a) EN-01 `John(2009)` 缺空格 → 报并可自动改",
      [h.replacement for h in hits_of("John(2009) argues\n", "EN-01", lang="en")] == ["n (2"])
check("(a) EN-02 英文冒号后缺空格 → 报并可自动改",
      [h.replacement for h in hits_of("Note:this is it\n", "EN-02", lang="en")] == [": t"])
check("(a) EN-03 覆盖 Word 中文撇号（原 EN-04 已并入：同一批字符，不再重复报）",
      len(hits_of("Under Kaplan’s framework\n", "EN-03", lang="en")) == 1
      and not any(r.id == "EN-04" for r in P.RULES))
check("(a) EN-03 是**可自动改**的：`Kaplan’s` → `Kaplan's`",
      [h.replacement for h in hits_of("Under Kaplan’s framework\n", "EN-03", lang="en")] == ["'"])
check("(a) EN-03 的映射：`“x”` → `` ``x'' ``、`—` → `---`、`…` → `\\ldots{}`",
      [h.replacement for h in hits_of("He said “character” once\n", "EN-03", lang="en")] == ["``", "''"]
      and [h.replacement for h in hits_of("it is key — really\n", "EN-03", lang="en")] == ["---"]
      and [h.replacement for h in hits_of("and then … nothing\n", "EN-03", lang="en")] == ["\\ldots{}"])
check("(a) EN-03 反例：整行以中文为主时**不改**（英文模板里的中文题名摘要）",
      not hits_of("\mytitleCN{论逻辑的文化相对性——“甲”}\n", "EN-03", lang="en"))
check("(a) EN-05 `pp. 6` 缺 \\, → 报", len(hits_of("see pp. 6-9\n", "EN-05", lang="en")) == 1)
check("(a) ALL-01 位图插图 → 报",
      len(hits_of("\\includegraphics[width=.5\\textwidth]{a.jpg}\n", "ALL-01")) == 1)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 不该报的不报：屏蔽层 ──")
BAD = "排中律,弗协 与 \\footnote{注,释} 与 $a,b$ 与 \\cite{key,other}\n"
check("(b) 注释里的半角标点不报",
      not hits_of("% 注释：甲,乙 与 丙.丁\n", "ZH-01") and not hits_of("% 注释：甲.乙\n", "ZH-02"))
check("(b) 数学里的半角逗号不报（那是数学语法）", not hits_of("$f(x,y)=z$ 说完了。\n", "ZH-01"))
check("(b) `\\cite{key,other}` 的键不报", not hits_of("见\\cite{key,other}。\n", "ZH-01"))
check("(b) 宏名不算正文：`\\hfill` 不报 ZH-18",
      not hits_of("\\begin{quotation}\\hfill\\end{quotation}\n", "ZH-18"))
check("(b) 但 `\\textbf{bold}` 的**参数**是楷体环境里的英文 → 要报（并另有 ZH-20 提示改用 \\textsf）",
      len(hits_of("\\begin{quotation}\\textbf{bold}\\end{quotation}\n", "ZH-18")) == 1
      and len(hits_of("\\begin{quotation}\\textbf{bold}\\end{quotation}\n", "ZH-20")) == 1)
check("(b) 自带 `\\textup` 的命令参数不报 ZH-18",
      not hits_of("\\begin{quotation}\\textup{This is fine} \\ct{a6}\\end{quotation}\n", "ZH-18"))
check("(b) 楷体环境里**裸露的英文**要报 ZH-18",
      len(hits_of("\\begin{quotation}bare English here\\end{quotation}\n", "ZH-18")) == 1)
check("(b) 英文规则不去管英文模板里的中文题名摘要（整行以中文为主 → 跳过）",
      not hits_of("\\mytitleCN{论逻辑的文化相对性}\n", "EN-03", lang="en"))
check("(b) 英文规则管英文正文（整行 ASCII 为主 → 报）",
      len(hits_of("Under Kaplan’s framework, “character” is key.\n", "EN-03", lang="en")) >= 2)
check("(b) 反例：`（Beall 2006）` 这种中文括号括英文引用**不算**半角括号问题",
      not hits_of("（Beall Restall 2006）\n", "ZH-07"))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) 自动改：只碰无歧义的 ──")
tex = ("令 $P$ 命题变元的无穷集合, 公式集递归定义。\n"
       "页：第8-12页；顿号：“甲”、‘乙’。\n"
       "集合：$\\{x|x>5\\}$。\n"
       "脚注：\\footnote{[] 鞠实儿. 论逻辑的文化相对性. 中国社会科学}\n")
rep = P.check(tex, lang="zh")
out, applied = P.apply_fixes(tex, rep)
ids = sorted({h.rule_id for h in applied})
check("(c) 自动改覆盖 ZH-01/ZH-06/ZH-09/ZH-14", ids == ["ZH-01", "ZH-06", "ZH-09", "ZH-14"], f"{ids}")
check("(c) 半角改全角时**吃掉多余空格**：`集合, 公式集` → `集合，公式集`",
      "无穷集合，公式集递归定义" in out, out.splitlines()[0])
check("(c) 页码改成 `第8\\cndash 12页`", "第8\\cndash 12页" in out)
check("(c) 顿号被删（`“甲”、‘乙’` → `“甲”‘乙’`）",
      "顿号：“甲”‘乙’" in out, out.splitlines()[1])
check("(c) 集合竖线改成 `\\mid`", "\\{x\\mid x>5\\}" in out)
foot_hits = [h for h in rep.hits if h.rule_id == "ZH-02"]
check("(c) 脚注里的中文文献条目（`鞠实儿. 论逻辑…`）**只报不改**",
      foot_hits and all(not h.fixable for h in foot_hits), f"{[(h.text, h.fixable) for h in foot_hits]}")
check("(c) 英文弯引号整行替换：`Kaplan’s` 与 `“character”` 一次改对",
      P.apply_fixes("Under Kaplan’s framework, “character” is key — really.\n",
                    P.check("Under Kaplan’s framework, “character” is key — really.\n", lang="en"))[0]
      == "Under Kaplan's framework, ``character'' is key --- really.\n")
check("(c) 中文行**不因英文规则被改**（语言判据拦住）",
      P.apply_fixes("他说“甲”，这是‘乙’——对不对？\n",
                    P.check("他说“甲”，这是‘乙’——对不对？\n", lang="en"))[0]
      == "他说“甲”，这是‘乙’——对不对？\n")
check("(c) 输入文本没有被就地改动（apply_fixes 返回新字符串）",
      tex.startswith("令 $P$ 命题变元的无穷集合, 公式集"))

# 位置移动：脚注/引文移到标点之前（函数手册 §常规 4.1 / 5.1）
moved, logs = P.move_marks_before_punct("甲说，\\footnote{嵌套{花}括号}不对。见，\\parencite{a,b}那里。\n")
check("(c) 脚注被移到「，」之前，且**嵌套花括号**完整搬走",
      "甲说\\footnote{嵌套{花}括号}，不对。" in moved, moved.strip())
check("(c) 引文（键里带逗号）也被移到「，」之前",
      "见\\parencite{a,b}，那里。" in moved, moved.strip())
check("(c) 位置移动有日志（逐条说明改了哪一行）",
      len(logs) == 2 and all("移到" in x for x in logs), f"{logs}")
check("(c) 反例：无法配对的花括号 → 跳过并记日志，不改坏",
      P.move_marks_before_punct("甲，\\footnote{没关完\n")[1][0].startswith("跳过"))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) CLI 契约 ──")
PY = sys.executable
ENV = dict(os.environ, PYTHONPATH=str(ENGINE / "vendor"))


def run(*args):
    return subprocess.run([PY, str(ENGINE / "proofcheck.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV, cwd=str(ENGINE))


tmp = tempfile.mkdtemp(prefix="slcn_proof_")
tex_path = os.path.join(tmp, "Demo.tex")
with open(tex_path, "w", encoding="utf-8") as fh:
    fh.write(tex)

r = run("--tex", tex_path)
check("(d) 有命中 → 退出码 1（正常情况，不是错误）", r.returncode == 1, f"rc={r.returncode}")
check("(d) 契约行给出命中数与报告路径",
      "完成。一校核查：命中" in r.stdout and "_一校核查.md" in r.stdout,
      r.stdout.strip().splitlines()[-1])
check("(d) 报告写到源文件同目录", os.path.exists(os.path.join(tmp, "Demo_一校核查.md")))
md = open(os.path.join(tmp, "Demo_一校核查.md"), encoding="utf-8").read()
check("(d) 报告里有摘要表与依据", "## 摘要" in md and "依据" in md)
check("(d) 报告里逐条列了行号", "| 行 |" in md)

r2 = run("--tex", tex_path, "--fix")
check("(d) `--fix` 写出新文件、**输入文件字节不变**",
      os.path.exists(os.path.join(tmp, "Demo_一校核查后.tex"))
      and open(tex_path, encoding="utf-8").read() == tex)
check("(d) `--fix` 的产物里半角逗号已改", "集合，公式集" in
      open(os.path.join(tmp, "Demo_一校核查后.tex"), encoding="utf-8").read())

clean = os.path.join(tmp, "Clean.tex")
with open(clean, "w", encoding="utf-8") as fh:
    fh.write("这是一段干净的中文正文，标点全角，没有问题。\n")
r3 = run("--tex", clean)
check("(d) 没有命中 → 退出码 0", r3.returncode == 0, f"rc={r3.returncode}")
r4 = run("--tex", "/tmp/不存在_zzz.tex")
check("(d) 文件不存在 → 退出码 2", r4.returncode == 2 and "找不到文件" in r4.stderr)
r5 = run("--version")
check("(d) `--version` 可用", r5.returncode == 0 and "SLCN 引擎" in r5.stdout)
r6 = run("--tex", tex_path, "--lang", "en")
check("(d) `--lang en` 时中文规则让位（命中数下降）",
      "命中" in r6.stdout and "ZH-01" not in r6.stdout, r6.stdout[:60])

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
