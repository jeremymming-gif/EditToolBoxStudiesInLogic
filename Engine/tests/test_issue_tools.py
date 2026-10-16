#!/usr/bin/env python3
"""期目录级的两件小事：删除临时文件、编译全部（第 91 条，从 mac 版搬进引擎）。

两件事都**在期目录上操作**（不是单篇稿件），所以判据是：
  · 删哪些、**不删哪些**（`.tex` / `.pdf` / `.log` 必须留着）；
  · 编哪些、**跳过哪些**（`000 目录` 不编）；
  · 某一篇失败时**只停那一篇**，并给出 `.log` 里的错误摘要。
"""
from __future__ import annotations

import os
import pathlib
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

r = run("compileall.py", "--folder", root, "--dry-run")
check("(b) `--dry-run`：退出码 0", r.returncode == 0, f"rc={r.returncode}")
check("(b) 列出 2 篇要编的", r.stdout.count("（预演）将编译：") == 2, r.stdout.strip()[-120:])
check("(b) **`000 目录` 不编**（中英文目录不参与）", "Chinese Content" not in r.stdout)
check("(b) 缺 `.tex` 的那篇报「没有找到」但不中断", "没有找到C_Author文件" in r.stdout)
check("(b) 契约行说明「列了几篇」", "只列出 2 篇" in r.stdout,
      r.stdout.strip().splitlines()[-1])

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
    check("(b) 反例：有一篇编译失败 → 退出码 1，且仍编译了另一篇",
          r.returncode == 1 and "成功 1 / 失败 1" in r.stdout, r.stdout.strip()[-200:])
    check("(b) 反例：给出 `.log` 里的 `!` 错误摘要（老版什么都看不到）",
          "Undefined control sequence" in r.stdout or "!" in r.stdout,
          r.stdout.strip()[-200:])
shutil.rmtree(root, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
