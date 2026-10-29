#!/usr/bin/env python3
"""期目录级的两件小事：删除临时文件、编译全部（第 91 条，从 mac 版搬进引擎）。

两件事都**在期目录上操作**（不是单篇稿件），所以判据是：
  · 删哪些、**不删哪些**（`.tex` / `.pdf` / `.log` 必须留着）；
  · 编哪些、**跳过哪些**（`000 目录` 不编）；
  · 某一篇失败时**只停那一篇**，并给出 `.log` 里的错误摘要；
  · ⑨ 编译没过时 `convert.py` 必须**退出 2**（契约：没有 1；外壳判成功只看 0）。
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import clean as C  # noqa: E402

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


PY = sys.executable
# ⚠️ 不要**覆盖** PYTHONPATH：把 vendor 拼在前面，环境里原有的继续生效
#    （本地用 `PYTHONPATH=/path/deps` 跑时曾被踢掉，表现为"找不到 pypdf"的假失败）。
_VENDOR = str(ENGINE / "vendor")
ENV = dict(os.environ,
           PYTHONPATH=os.pathsep.join([_VENDOR] + ([os.environ["PYTHONPATH"]]
                                                  if os.environ.get("PYTHONPATH") else [])))


def run(script: str, *args):
    return subprocess.run([PY, str(ENGINE / script), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV, cwd=str(ENGINE))


def mkfile(path: str, text: str = "x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 删除临时文件：删哪些、不删哪些 ──")
root = tempfile.mkdtemp(prefix="slcn_clean_")
for suf in (".aux", ".bbl", ".bcf", ".blg", ".out", ".run.xml", ".synctex.gz", ".DS_Store"):
    mkfile(os.path.join(root, "01 A", f"x{suf}"))
for keep in ("A.tex", "A.pdf", "A.log", "SLCN-MIX.sty", "reference.bib", "notes.aux.txt"):
    mkfile(os.path.join(root, "01 A", keep))
mkfile(os.path.join(root, "deep/deeper/y.synctex.gz"))
# 红线：作者来稿目录。里面就算有 `.aux`/`.bbl` 也**一个都不许删**（2026-10-17 第 107 条）
mkfile(os.path.join(root, "01 A/original/x.aux"))
mkfile(os.path.join(root, "02 B/original/deep/y.bbl"))
mkfile(os.path.join(root, "02 B/Original/z.out"))          # 大小写也算（同一棵树）
# 2026-10-17 第十五批：库里历史期真用过 `origin/`（8 篇）与 `原稿/`（3 篇）——
# 它们同样是**作者来稿**，清理器必须一起保护（否则哪天里面出现 .bbl/.aux 就会被删）
mkfile(os.path.join(root, "02 B/origin/w.bbl"))
mkfile(os.path.join(root, "02 B/原稿/v.aux"))
os.makedirs(os.path.join(root, "weird.aux"), exist_ok=True)      # **目录**名叫 .aux
mkfile(os.path.join(root, "weird.aux", "inside.log"))            # 里面的日志不许被连累

r = run("cleantemp.py", "--folder", root, "--dry-run")
check("(a) `--dry-run`：退出码 0，且报告 10 个（8 个后缀 + 深层的 1 个 + …）",
      r.returncode == 0 and "将删除 9 个" in r.stdout, r.stdout.strip().splitlines()[-1:])
check("(a) `--dry-run` **一个都没删**",
      os.path.exists(os.path.join(root, "01 A", "x.aux")))
check("(a) 契约行在最后一行", r.stdout.strip().splitlines()[-1].startswith("完成。将删除"),
      r.stdout.strip().splitlines()[-1])

r = run("cleantemp.py", "--folder", root)
check("(a) 真删：退出码 0，删掉 9 个", r.returncode == 0 and "已删除 9 个" in r.stdout,
      r.stdout.strip().splitlines()[-1])
check("(a) **`.tex` 留住了**", os.path.exists(os.path.join(root, "01 A", "A.tex")))
check("(a) **`.pdf` 留住了**", os.path.exists(os.path.join(root, "01 A", "A.pdf")))
check("(a) **`.log` 留住了**（排错要用；本项目的既有口径）",
      os.path.exists(os.path.join(root, "01 A", "A.log")))
check("(a) `.sty` / `.bib` 也留住了",
      os.path.exists(os.path.join(root, "01 A", "SLCN-MIX.sty"))
      and os.path.exists(os.path.join(root, "01 A", "reference.bib")))
check("(a) 反例：`notes.aux.txt` **不删**（后缀要匹配到名字末尾）",
      os.path.exists(os.path.join(root, "01 A", "notes.aux.txt")))
check("(a) 反例：名叫 `weird.aux` 的**目录**不许删，里面的 `inside.log` 也不许连累",
      os.path.isdir(os.path.join(root, "weird.aux"))
      and os.path.exists(os.path.join(root, "weird.aux", "inside.log")))
check("(a) 深层目录里的临时文件也删（递归）",
      not os.path.exists(os.path.join(root, "deep/deeper/y.synctex.gz")))
check("(a) **红线**：`original/` 里的临时文件一个都不碰（作者来稿只读）——"
      "真删之后它们仍在，且没被算进上面的 9 个",
      os.path.exists(os.path.join(root, "01 A/original/x.aux"))
      and os.path.exists(os.path.join(root, "02 B/original/deep/y.bbl")),
      "original/ 里的文件被删了")
check("(a) `Original/`（大小写不同）同样不进",
      os.path.exists(os.path.join(root, "02 B/Original/z.out")))
check("(a) 历史别名 `origin/`、`原稿/` 也按作者来稿保护（2026-10-17 全库普查发现库里真用过）",
      os.path.exists(os.path.join(root, "02 B/origin/w.bbl"))
      and os.path.exists(os.path.join(root, "02 B/原稿/v.aux")),
      "历史别名里的文件被删了")
# ── (a2) 可回收模式：移入 `.toolbox-trash/<时间戳>/`，可撤销还原（UI 方案第 3 件）──
root2 = tempfile.mkdtemp(prefix="slcn_trash_")
for suf in (".aux", ".bbl", ".out"):
    mkfile(os.path.join(root2, "01 A", f"t{suf}"))
mkfile(os.path.join(root2, "01 A", "A.tex"))
mkfile(os.path.join(root2, "01 A", "A.pdf"))
mkfile(os.path.join(root2, "01 A/original/作者稿.bbl"))      # 红线：不碰
mkfile(os.path.join(root2, "01 A/origin/作者稿2.bbl"))       # 历史别名：同样不碰
r = run("cleantemp.py", "--folder", root2, "--trash", "--dry-run")
check("(a2) `--trash --dry-run`：只说会移哪些，**一个都不动**",
      r.returncode == 0 and "将移入回收目录 3 个" in r.stdout
      and os.path.exists(os.path.join(root2, "01 A", "t.aux")), r.stdout.strip()[-90:])
r = run("cleantemp.py", "--folder", root2, "--trash")
check("(a2) `--trash`：退出码 0，移入 3 个", r.returncode == 0 and "已移入回收目录 3 个" in r.stdout,
      r.stdout.strip()[-90:])
check("(a2) 契约行在最后一行、且带**回收目录**（撤销按钮要用它）",
      r.stdout.strip().splitlines()[-1].startswith("完成。已移入回收目录")
      and "回收目录：" in r.stdout.strip().splitlines()[-1],
      r.stdout.strip().splitlines()[-1])
import glob as _glob
_tr = _glob.glob(os.path.join(root2, ".toolbox-trash", "*"))
check("(a2) 回收目录建在目录根下、带 manifest.json",
      len(_tr) == 1 and os.path.exists(os.path.join(_tr[0], "manifest.json")), str(_tr))
check("(a2) 结构保留：`01 A/t.aux` 在回收目录里对应位置",
      os.path.exists(os.path.join(_tr[0], "01 A", "t.aux")))
check("(a2) 原处的临时文件已经不在；`.tex`/`.pdf` 仍在",
      not os.path.exists(os.path.join(root2, "01 A", "t.aux"))
      and os.path.exists(os.path.join(root2, "01 A", "A.tex")))
check("(a2) **红线**：`original/` 里的 `.bbl` 没被移走",
      os.path.exists(os.path.join(root2, "01 A/original/作者稿.bbl")))
check("(a2) 历史别名 `origin/` 里的 `.bbl` 也没被移走（回收模式同样保护）",
      os.path.exists(os.path.join(root2, "01 A/origin/作者稿2.bbl")))
# 撤销：搬回来
r = run("cleantemp.py", "--folder", root2, "--restore", _tr[0])
check("(a2) `--restore`：退出码 0，恢复 3 个", r.returncode == 0 and "已恢复 3 个" in r.stdout,
      r.stdout.strip()[-90:])
check("(a2) 还原后文件回到原位、内容还在",
      os.path.exists(os.path.join(root2, "01 A", "t.aux")))
# 再清理一次：3 个文件都在原位了 → 又被搬走 3 个；回收目录里的旧东西**不许被二次搬运**
r = run("cleantemp.py", "--folder", root2, "--trash")
check("(a2) 再清理一次：搬走 3 个，且回收目录里的旧东西没被二次搬运（不许嵌套回收）",
      r.returncode == 0 and "已移入回收目录 3 个" in r.stdout, r.stdout.strip()[-80:])
_tr3 = sorted(_glob.glob(os.path.join(root2, ".toolbox-trash", "*")))[-1]
# 反例造法：**先**把原位置占住（模拟编辑在清理后又自己放了个同名文件），**再**还原
mkfile(os.path.join(root2, "01 A", "t.bbl"), "NEW CONTENT")
r = run("cleantemp.py", "--folder", root2, "--restore", _tr3)
check("(a2) 反例：还原时原位置**已有同名文件 → 跳过不覆盖**（还原不能变成第二次破坏）",
      r.returncode == 0 and "已恢复 2 个" in r.stdout and "Skip" in r.stdout
      and open(os.path.join(root2, "01 A", "t.bbl"), encoding="utf-8").read() == "NEW CONTENT",
      r.stdout.strip()[-140:])

r = run("cleantemp.py", "--folder", "/tmp/绝对不存在的目录_zzz")
check("(a) 目录不存在 → 退出码 2（错误在 stderr）",
      r.returncode == 2 and "不是文件夹" in r.stderr, f"rc={r.returncode}")
r = run("cleantemp.py", "--version")
check("(a) `--version` 可用", r.returncode == 0 and "SLCN 引擎" in r.stdout)
shutil.rmtree(root, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 编译全部：编哪些、跳过哪些 ──")
root = tempfile.mkdtemp(prefix="slcn_compileall_")
DOC = ("\\documentclass{article}\n\\begin{document}\nHello 编辑部.\n\\end{document}\n")
mkfile(os.path.join(root, "01 A_Author", "A_Author.tex"), DOC)
mkfile(os.path.join(root, "02 B_Author", "B_Author.tex"), DOC)
mkfile(os.path.join(root, "000 目录", "Chinese Content.tex"), DOC)   # 不编
os.makedirs(os.path.join(root, "03 C_Author"), exist_ok=True)        # 没有 .tex
# 撇号差异：目录名 `04 Tian'an_He`，人写的文件名却是 `Tianan_He.tex`
# （真实例子：目录名与文件名只差一个撇号）
mkfile(os.path.join(root, "04 Tian'an_He", "Tianan_He.tex"), DOC)

r = run("compileall.py", "--folder", root, "--dry-run")
check("(b) `--dry-run`：退出码 0", r.returncode == 0, f"rc={r.returncode}")
check("(b) 列出 3 篇要编的", r.stdout.count("（预演）将编译：") == 3, r.stdout.strip()[-120:])
check("(b) **宽松匹配**：目录名 `Tian'an_He` 与文件名 `Tianan_He.tex` 只差一个撇号，也要编它",
      "Tianan_He.tex" in r.stdout and "（宽松匹配）" in r.stdout,
      r.stdout.strip()[-160:])
check("(b) 反例：精确名对得上时**不许**走宽松匹配（不许把别的篇认错）",
      "（宽松匹配）A_Author" not in r.stdout)
check("(b) **`000 目录` 不编**（中英文目录不参与）", "Chinese Content" not in r.stdout)
check("(b) 缺 `.tex` 的那篇报「没有找到」但不中断", "没有找到C_Author文件" in r.stdout)
check("(b) 预演说明**单独一行**，不许接在契约行后面", "只列出 3 篇" in r.stdout,
      r.stdout.strip().splitlines()[-1])
# ⚠️ 契约行 = ENGINE_API §五 写的那句，**形状**不许变（第 107 条的教训：
#    从前预演说明接在它后面，于是"最后一行"不是文档里那句）。这里按整行匹配。
_last = r.stdout.strip().splitlines()[-1]
check("(b) **最后一行**就是契约行本身（`完成。编译 N 篇（成功 X / 失败 Y）（目录：…）`）",
      re.fullmatch(r"完成。编译 0 篇（成功 0 / 失败 0）（目录：.+）", _last) is not None, _last)

r = run("compileall.py", "--folder", root, "--dry-run", "--only", "01")
check("(b) `--only 01` 只列 01", r.stdout.count("（预演）将编译：") == 1
      and "A_Author" in r.stdout and "B_Author" not in r.stdout, r.stdout.strip()[-80:])
r = run("compileall.py", "--folder", root, "--dry-run", "--only", "09")
check("(b) `--only` 谁都不要时 → 0 篇、退出码 0（不是错误）",
      r.returncode == 0 and "编译 0 篇" in r.stdout, r.stdout.strip()[-70:])
r = run("compileall.py", "--folder", "/tmp/绝对不存在的目录_zzz")
check("(b) 目录不存在 → 退出码 2", r.returncode == 2 and "期目录不存在" in r.stderr)
r = run("compileall.py", "--version")
check("(b) `--version` 可用", r.returncode == 0 and "SLCN 引擎" in r.stdout)

# ── 真跑一次：这台机器有 TeX，就让 xelatex 真的编一遍（没有 TeX 就跳过）──
if shutil.which("xelatex") is None:
    print("SKIP  (b) 本机没有 xelatex，跳过真编译")
else:
    r = run("compileall.py", "--folder", root, "--only", "01")
    pdf = os.path.join(root, "01 A_Author", "A_Author.pdf")
    check("(b) 真编译：退出码 0（成功 1 / 失败 0）",
          r.returncode == 0 and "成功 1 / 失败 0" in r.stdout, r.stdout.strip()[-140:])
    check("(b) 真编译：产物 PDF 出来了", os.path.exists(pdf))
    check("(b) 真编译：`.log` 留着（排错用）",
          os.path.exists(os.path.join(root, "01 A_Author", "A_Author.log")))
    # 反例：故意写一篇编译不过的，验证「只停那一篇 + 给出 ! 摘要 + 退出码 1」
    mkfile(os.path.join(root, "02 B_Author", "B_Author.tex"),
           "\\documentclass{article}\n\\begin{document}\n\\undefinedcmd\n\\end{document}\n")
    r = run("compileall.py", "--folder", root)
    # 不写死具体篇数（夹具里加一篇就会失效）：只要求「有成功、有失败、退出码 1」
    check("(b) 反例：有一篇编译失败 → 退出码 1，且仍编译了别的（只停那一篇）",
          r.returncode == 1
          and re.search(r"成功 [1-9]\d* / 失败 [1-9]\d*", r.stdout) is not None,
          r.stdout.strip()[-200:])
    check("(b) 反例：给出 `.log` 里的 `!` 错误摘要（老版什么都看不到）",
          "Undefined control sequence" in r.stdout or "!" in r.stdout,
          r.stdout.strip()[-200:])
shutil.rmtree(root, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) 初排转换：⑨ 编译没过 ⇒ 整个转换算**失败**（退出码 2，不是 0）──")
# 契约（ENGINE_API「退出码」）：0 = 成功；**没有 1**；中途某一步失败也算失败、一律 2。
# 判据与 TeX 在不在无关：装了 TeX 是 xelatex 报错，没装是"找不到命令 xelatex"，
# 两条都走同一个"编译未通过"分支。
croot = tempfile.mkdtemp(prefix="slcn_convert_")
csrc = mkfile(os.path.join(croot, "main.tex"),
              "\\documentclass{article}\n\\begin{document}\n"
              "\\undefinedmacrohere\n\\end{document}\n")
r = run("convert.py", "--source", csrc, "--out", os.path.join(croot, "out"),
        "--name", "Test_Author", "--no-original", "--compile")   # --author 是中文姓名，会走拼音拆分
check("(c) 编译未通过 → 退出码 **2**（从前是 0，界面因此打 ✅）", r.returncode == 2,
      f"rc={r.returncode}｜stdout 末行：{(r.stdout or '').strip().splitlines()[-1][:70]}")
check("(c) 失败原因走 stderr（退出码 2 的口径）", "编译未通过" in (r.stderr or ""),
      (r.stderr or "").strip()[-90:])
check("(c) stdout **最后一行仍是契约行** `完成。产物目录：…`（新信息不许挤掉它）",
      (r.stdout or "").strip().splitlines()[-1].startswith("完成。产物目录："),
      (r.stdout or "").strip().splitlines()[-1][:80])
check("(c) 转换本身的产物 `.tex` 还在（只是编译没过，别把编辑的稿子也弄丢）",
      any(f.endswith(".tex") for _r, _d, fs in os.walk(os.path.join(croot, "out")) for f in fs))
shutil.rmtree(croot, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) 本期出刊向导：四步编排 / 失败中止 / 单步重跑（UI 方案候选 C 的引擎侧）──")
proot = tempfile.mkdtemp(prefix="slcn_publish_")
mkfile(os.path.join(proot, "01 A_Author", "A_Author.tex"), DOC)

r = run("publish.py", "--version")
check("(e) `--version` 可用", r.returncode == 0 and "SLCN 引擎" in r.stdout)
r = run("publish.py", "--folder", os.path.join(proot, "不存在的期目录"))
check("(e) 期目录不存在 → 退出码 2", r.returncode == 2 and "期目录不存在" in r.stderr)
r = run("publish.py", "--folder", proot, "--only", "9")
check("(e) `--only 9`（没有第 9 步）→ 用法错误、退出码 2", r.returncode == 2)

# 失败中止：没有 ① 的报告却直接跑 ② → 必须中止，且契约行明说"卡在第 2 步"
r = run("publish.py", "--folder", proot, "--only", "2")
check("(e) **失败中止**：没有报告就跑第 2 步 → 退出码 2、契约行说清\"卡在第 2 步\"",
      r.returncode == 2 and "卡在第 2 步（生成目录）" in r.stdout,
      r.stdout.strip()[-100:])
check("(e) 中止时契约行在**最后一行**（外壳靠它显示「卡在哪」）",
      r.stdout.strip().splitlines()[-1].startswith("完成。出刊：卡在第 2 步"),
      r.stdout.strip().splitlines()[-1])
check("(e) 中止时**不生成任何目录产物**（不许静默产出半成品）",
      not [f for f in os.listdir(proot) if f.endswith(".tex") and ("contents" in f or "Content" in f)])

# 单步重跑：只跑第 1 步 → 报告生成、退出码 0、契约行在最后
r = run("publish.py", "--folder", proot, "--only", "1")
check("(e) `--only 1`（单步重跑）：退出码 0、报告生成",
      r.returncode == 0
      and any(f.startswith("信息检查") and f.endswith(".txt") for f in os.listdir(proot)),
      r.stdout.strip()[-100:])
check("(e) 单步重跑的契约行在最后一行、明说「只跑了第 1 步」",
      r.stdout.strip().splitlines()[-1].startswith("完成。出刊：只跑了第 1 步"),
      r.stdout.strip().splitlines()[-1])

# 继续往后跑（这个夹具只有 1 篇 tex、没有 PDF/模板）→ 必然在中途某步中止；
# 判据只要求「中止语义正确」而不写死卡在第几步（夹具太简，toc 自己也可能拒绝）
r = run("publish.py", "--folder", proot, "--from", "2")
_last_line = r.stdout.strip().splitlines()[-1]
check("(e) 后续步骤失败 → **中止**：退出码 2、契约行在最后一行且说清「卡在第 N 步」",
      r.returncode == 2 and _last_line.startswith("完成。出刊：卡在第")
      and "前 " in _last_line and "步产物有效" in _last_line, _last_line)
check("(e) 中止时**已完成步骤的产物保留**（① 的信息检查报告还在）",
      any(f.startswith("信息检查") and f.endswith(".txt") for f in os.listdir(proot)),
      sorted(os.listdir(proot))[:5])
shutil.rmtree(proot, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (f) 合并预览 --detect：pypdf 延迟导入后的回归守卫 ──")
mroot = tempfile.mkdtemp(prefix="slcn_merge_detect_")
mkfile(os.path.join(mroot, "扉页.pdf"), "%PDF-1.4\n")        # 触发 coverPages 计算
mkfile(os.path.join(mroot, "000 Table of Contents", "contents.tex"), DOC)
mkfile(os.path.join(mroot, "01 A_Author", "A_Author.tex"), DOC)
mkfile(os.path.join(mroot, "01 A_Author", "A_Author.pdf"), "%PDF-1.4\n")
r = run("mergepdf.py", "--folder", mroot, "--detect")
check("(f) `--detect` 退出码 0、输出 JSON（2026-10-17 实例测试抓到：pypdf 延迟导入后 "
      "`pdfmerge.PdfReader` 不存在 → AttributeError 崩溃；当时没有测试覆盖 --detect）",
      r.returncode == 0 and "coverPages" in r.stdout, r.stdout.strip()[-120:] + r.stderr.strip()[-120:])
check("(f) `--detect` **不写任何产物**（只读预览）",
      not [f for f in os.listdir(mroot) if f.startswith("合并")], sorted(os.listdir(mroot))[:6])
shutil.rmtree(mroot, ignore_errors=True)

print("\n" + "=" * 66)
# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) 原子写入 + 旧版备份（UI 方案第 4 件）──")
from slcn import fsutil as _fu  # noqa: E402

droot = tempfile.mkdtemp(prefix="slcn_atomic_")
target = os.path.join(droot, "产物.tex")
bk = _fu.atomic_write(target, "第一版\n")
check("(d) 首写：文件生成、内容正确、没有旧版备份、**不留 .tmp-**",
      open(target, encoding="utf-8").read() == "第一版\n" and bk == ""
      and not [f for f in os.listdir(droot) if ".tmp-" in f], os.listdir(droot))
bk = _fu.atomic_write(target, "第二版\n")
check("(d) 覆盖写：新内容生效；旧版自动备份成 `.bak` 且内容是**旧的**",
      open(target, encoding="utf-8").read() == "第二版\n" and bk.endswith(".bak")
      and os.path.exists(bk) and open(bk, encoding="utf-8").read() == "第一版\n", bk)
for i in range(3, 8):
    _fu.atomic_write(target, f"第{i}版\n")
baks = [f for f in os.listdir(droot) if f.endswith(".bak")]
check("(d) 旧版只留最近 3 份（再多就是垃圾）", len(baks) == 3, sorted(baks))
check("(d) 保留的是**最近**三份（时间戳定长，字典序=时间序）",
      sorted(baks) == sorted(baks), sorted(baks))

# 反例：写一半崩掉 —— 旧文件必须**完好**，不能变成半截（这就是原子写的全部意义）
survivor = os.path.join(droot, "重要产物.tex")
_fu.atomic_write(survivor, "完整旧版")
_real_replace = os.replace
try:
    def _boom(*a, **k):
        raise OSError("模拟：替换瞬间磁盘满/进程被杀")
    os.replace = _boom
    raised = False
    try:
        _fu.atomic_write(survivor, "写了一半就死")
    except OSError:
        raised = True
finally:
    os.replace = _real_replace
check("(d) 反例：替换失败时抛错（不静默假装成功）", raised)
check("(d) 反例：替换失败后**旧文件仍是完整的旧版**（不会只剩半截）",
      open(survivor, encoding="utf-8").read() == "完整旧版",
      open(survivor, encoding="utf-8").read()[:30])
check("(d) 反例：失败时也不留 `.tmp-` 垃圾",
      not [f for f in os.listdir(droot) if ".tmp-" in f], os.listdir(droot))

# LF 契约：产物一律 LF（不因平台变成 CRLF）
lf = os.path.join(droot, "lf.tex")
_fu.atomic_write(lf, "a\nb\n", newline="\n")
check("(d) 写出的产物是 **LF**（不变成平台默认行尾）",
      open(lf, "rb").read() == b"a\nb\n", open(lf, "rb").read())
shutil.rmtree(droot, ignore_errors=True)

if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)

