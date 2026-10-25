#!/usr/bin/env python3
"""B-211 回归：交付目录收尾清理 / 工作区旧件 / 回收站 v2 / promote 后清暂存。

全部 hermetic（tmpdir），走**真的 CLI**（subprocess）。断言面：
  (a) 旧 aux 路径逐字节不变（stdout 契约行 + 删除集合）；
  (b) 回收站新命名、manifest v2、restore 逐字节（含目录条目）、v1 兼容；
  (c) --empty-trash（含 dry-run）；
  (d) --mode delivery：白名单/junk/未识别保留/original 保护/交付清单/引用闭包
      （230 假 png 全保留、闭包目录保留）/缺失整体拒绝零写入/--delete；
  (e) --mode workspace：四形态时间、固定名、不可解析保守保留、24 单元 68 份夹具、
      固定交付物保护、coop-bak/_待核 清理、--delete；
  (f) coop.cleanup_stage + convert.py 的调用位置（promote 失败绝不删 stage）。
"""
from __future__ import annotations

import glob
import hashlib
import json
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
from slcn import coop  # noqa: E402

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
_VENDOR = str(ENGINE / "vendor")
ENV = dict(os.environ,
           PYTHONPATH=os.pathsep.join([_VENDOR] + ([os.environ["PYTHONPATH"]]
                                                  if os.environ.get("PYTHONPATH") else [])))


def run(script: str, *args):
    return subprocess.run([PY, str(ENGINE / script), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=ENV, cwd=str(ENGINE))


def mkfile(path: str, text: str = "x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def snapshot(root: str) -> dict[str, str]:
    """相对路径 → 'dir' 或内容 sha。回收站不算（撤销后会留下空壳 manifest）。"""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != C.TRASH_DIR]
        for d in dirnames:
            out[os.path.relpath(os.path.join(dirpath, d), root).replace("\\", "/")] = "dir"
        for f in filenames:
            full = os.path.join(dirpath, f)
            out[os.path.relpath(full, root).replace("\\", "/")] = sha(full)
    return out


def trash_dirs(root: str) -> list[str]:
    return sorted(glob.glob(os.path.join(root, C.TRASH_DIR, "*")))


def last_line(r) -> str:
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 旧 aux 路径：无新参数时 stdout/删除集合不变 ──")
root = tempfile.mkdtemp(prefix="b211_aux_")
expected = (f"删除临时文件\n\n路径：{root}\n\n删除完成，共删除0个文件。\n\n"
            f"完成。已删除 0 个临时文件（目录：{root}）\n")
r = run("cleantemp.py", "--folder", root)
check("(a) 无参数空目录 stdout **逐字节**不变", r.stdout == expected, repr(r.stdout))
check("(a) 无参数退出码 0", r.returncode == 0, f"rc={r.returncode}")

for suf in (".aux", ".bbl", ".bcf", ".blg", ".out", ".run.xml", ".synctex.gz", ".DS_Store"):
    mkfile(os.path.join(root, "01 A", f"x{suf}"))
mkfile(os.path.join(root, "deep/deeper/y.synctex.gz"))
for keep in ("A.tex", "A.pdf", "A.log", "SLCN-MIX.sty", "reference.bib", "notes.aux.txt"):
    mkfile(os.path.join(root, "01 A", keep))
mkfile(os.path.join(root, "01 A/original/x.aux"))
mkfile(os.path.join(root, "02 B/Original/z.out"))
mkfile(os.path.join(root, "02 B/origin/w.bbl"))
mkfile(os.path.join(root, "02 B/原稿/v.aux"))
os.makedirs(os.path.join(root, "weird.aux"), exist_ok=True)
mkfile(os.path.join(root, "weird.aux", "inside.log"))
r = run("cleantemp.py", "--folder", root, "--dry-run")
check("(a) dry-run：报告将删除 9 个、契约行不变",
      r.returncode == 0 and "将删除 9 个" in r.stdout
      and last_line(r) == f"完成。将删除 9 个临时文件（目录：{root}）", last_line(r))
check("(a) dry-run 一个都没删", os.path.exists(os.path.join(root, "01 A", "x.aux")))
r = run("cleantemp.py", "--folder", root)
check("(a) 真删：退出码 0、删除 9 个", r.returncode == 0 and "已删除 9 个" in r.stdout,
      f"rc={r.returncode}")
check("(a) 契约行仍是旧文案", last_line(r) == f"完成。已删除 9 个临时文件（目录：{root}）",
      last_line(r))
check("(a) 日志行仍是 `删除完成，共删除9个文件。`", "删除完成，共删除9个文件。" in r.stdout)
left = sorted(os.path.relpath(os.path.join(dp, f), root).replace("\\", "/")
              for dp, _dn, fn in os.walk(root) for f in fn)
check("(a) 删除集合正确：只剩 keep 文件（含 original 系 4 个）",
      left == sorted(["01 A/A.tex", "01 A/A.pdf", "01 A/A.log", "01 A/SLCN-MIX.sty",
                      "01 A/reference.bib", "01 A/notes.aux.txt", "weird.aux/inside.log",
                      "01 A/original/x.aux", "02 B/Original/z.out",
                      "02 B/origin/w.bbl", "02 B/原稿/v.aux"]), left)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 回收站：新命名 + manifest v2 + restore（含目录条目 / v1 兼容）──")
root2 = tempfile.mkdtemp(prefix="b211_trash_")
mkfile(os.path.join(root2, "01 A", "t.aux"), "AUX内容")
mkfile(os.path.join(root2, "01 A", "t.bbl"), "BBL内容")
mkfile(os.path.join(root2, "x.out"), "OUT内容")
mkfile(os.path.join(root2, "01 A", "A.tex"), "TEX")
mkfile(os.path.join(root2, "01 A/original/作者稿.bbl"), "ORIG")
before = snapshot(root2)
r = run("cleantemp.py", "--folder", root2, "--trash")
check("(b) --trash 退出码 0、移入 3 个", r.returncode == 0 and "已移入回收目录 3 个" in r.stdout,
      last_line(r))
td = trash_dirs(root2)
base = os.path.basename(root2)
pat = re.compile(rf"^{re.escape(base)}-\d{{4}}_\d{{2}}_\d{{2}}_\d{{6}}$")
check("(b) 回收目录名 = <目录名>-<YYYY_MM_DD_HHMMSS>（无嵌套旧式纯时间戳）",
      len(td) == 1 and bool(pat.match(os.path.basename(td[0]))), td)
man = json.load(open(os.path.join(td[0], "manifest.json"), encoding="utf-8"))
check("(b) manifest schema=2 / mode=aux / root=绝对路径",
      man.get("schema") == 2 and man.get("mode") == "aux" and man.get("root") == root2, man.get("schema"))
check("(b) manifest entries=3 条且 type=file",
      len(man.get("entries", [])) == 3 and all(e["type"] == "file" for e in man["entries"]))
check("(b) 移入后原处只剩 .tex 与 original/",
      not os.path.exists(os.path.join(root2, "01 A", "t.aux"))
      and os.path.exists(os.path.join(root2, "01 A", "A.tex"))
      and os.path.exists(os.path.join(root2, "01 A/original/作者稿.bbl")))
check("(b) 契约行带回收目录、最后一行",
      last_line(r).startswith("完成。已移入回收目录 3 个临时文件（回收目录：") and "回收目录：" in last_line(r),
      last_line(r))
# 撤销：先占住一个原位置，验证不覆盖 + 其余逐字节还原
mkfile(os.path.join(root2, "01 A", "t.bbl"), "NEW")
r = run("cleantemp.py", "--folder", root2, "--restore", td[0])
check("(b) restore：恢复 2 个、跳过已存在 1 个", r.returncode == 0 and "已恢复 2 个" in r.stdout
      and "Skip" in r.stdout, last_line(r))
check("(b) restore 不覆盖：原位置新文件内容保留",
      open(os.path.join(root2, "01 A", "t.bbl"), encoding="utf-8").read() == "NEW")
check("(b) restore 逐字节：sha 与清理前一致（除被占位的那一个）",
      sha(os.path.join(root2, "01 A", "t.aux")) == before["01 A/t.aux"]
      and sha(os.path.join(root2, "x.out")) == before["x.out"]
      and sha(os.path.join(root2, "01 A/original/作者稿.bbl")) == before["01 A/original/作者稿.bbl"])
check("(b) restore 契约行不变", last_line(r) == f"完成。已恢复 2 个文件（目录：{root2}）",
      last_line(r))
# v1 manifest 兼容
v1 = os.path.join(root2, C.TRASH_DIR, "old-v1")
mkfile(os.path.join(v1, "v1/doc.aux"), "V1")
json.dump({"root": root2, "createdAt": "2026_01_01_000000",
           "files": ["v1/doc.aux"]},
          open(os.path.join(v1, "manifest.json"), "w", encoding="utf-8"))
r = run("cleantemp.py", "--folder", root2, "--restore", v1)
check("(b) 旧 v1 manifest 仍可 restore",
      r.returncode == 0 and "已恢复 1 个" in r.stdout
      and open(os.path.join(root2, "v1/doc.aux"), encoding="utf-8").read() == "V1",
      last_line(r))
# 目录条目 restore（direct 调用 delivery 走 CLI 已覆盖；这里先钉 manifest 类型）
root_fresh = tempfile.mkdtemp(prefix="b211_tdname_")
check("(b) clean.trash_dir 使用新命名（不创建即算路径）",
      re.search(rf"{re.escape(os.path.basename(root_fresh))}-\d{{4}}_\d{{2}}_\d{{2}}_\d{{6}}$",
                C.trash_dir(root_fresh)) is not None)
shutil.rmtree(root_fresh, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) --empty-trash ──")
root3 = tempfile.mkdtemp(prefix="b211_et_")
for name in ("a-2024_01_01_000000", "b-2024_01_01_000000"):
    mkfile(os.path.join(root3, C.TRASH_DIR, name, "x.aux"))
r = run("cleantemp.py", "--folder", root3, "--empty-trash", "--dry-run")
check("(c) dry-run：报告将清空 2 个、零写入",
      r.returncode == 0 and "将清空回收站 2 个" in r.stdout
      and len(trash_dirs(root3)) == 2, last_line(r))
r = run("cleantemp.py", "--folder", root3, "--empty-trash")
check("(c) 清空 2 个回收目录、契约行精确",
      r.returncode == 0 and trash_dirs(root3) == []
      and last_line(r) == f"完成。已清空回收站 2 个回收目录（目录：{root3}）", last_line(r))
r = run("cleantemp.py", "--folder", root3, "--empty-trash")
check("(c) 回收站不存在/已空 → 0，仍退出 0",
      r.returncode == 0 and last_line(r) == f"完成。已清空回收站 0 个回收目录（目录：{root3}）",
      last_line(r))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) --mode delivery：白名单优先 / 引用闭包 / 整体拒绝 ──")
D = tempfile.mkdtemp(prefix="b211_delivery_")
MAIN = r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
\includegraphics[width=.5\textwidth]{Figs/a}
\input{Chapters/ch1}
\addbibresource{refs.bib}
\bibliography{refs2}
\end{document}
"""
mkfile(os.path.join(D, "main.tex"), MAIN)
mkfile(os.path.join(D, "Chapters/ch1.tex"),
       r"\includegraphics{Figs/b.png}" + "\n")
mkfile(os.path.join(D, "Figs/a.pdf"), "A")
mkfile(os.path.join(D, "Figs/b.png"), "B")
mkfile(os.path.join(D, "Figs/c.png"), "C")          # 未被引用，但闭包目录整棵保留
mkfile(os.path.join(D, "Figs/notes.txt"), "N")      # 同上
for keep in ("A.pdf", "refs.bib", "refs2.bib", "SLCN-MIX.sty", "journal.cls",
             "交付清单.md", "我的交付清单.md", "mystery.xyz", "bad_AI.tex"):
    mkfile(os.path.join(D, keep), keep)
for suf in (".aux", ".bbl", ".bcf", ".blg", ".out", ".run.xml", ".synctex.gz",
            ".fls", ".fdb_latexmk", ".toc", ".lof", ".lot", ".nav", ".snm", ".vrb",
            ".bbl-SAVE-ERROR", ".DS_Store", ".ai前", ".log", ".bak"):
    mkfile(os.path.join(D, "junk", f"x{suf}"))
mkfile(os.path.join(D, "junk", "tex.tex.20240101.bak"))
for name in ("note_AI协同记录.md", "note_AI协同状态.json", "x_AI建议.txt",
             "x_文献核实.md", "x_回读校验报告.md", "x_参考文献核对.txt",
             "x_一校核查v2.md", "x_原稿.pdf"):
    mkfile(os.path.join(D, "junk", name))
mkfile(os.path.join(D, "old_待核/inner.txt"), "待核")
mkfile(os.path.join(D, "original/keep.aux"), "作者稿")
mkfile(os.path.join(D, "origin/k.bbl"), "作者稿2")
mkfile(os.path.join(D, "原稿/k.out"), "作者稿3")
snap_d = snapshot(D)
r = run("cleantemp.py", "--folder", D, "--mode", "delivery", "--dry-run")
check("(d) dry-run：退出码 0、契约行「将移入」、零写入",
      r.returncode == 0 and "已移入" not in r.stdout
      and "将移入回收目录" in r.stdout and trash_dirs(D) == []
      and os.path.exists(os.path.join(D, "junk", "x.aux"))
      and os.path.isdir(os.path.join(D, "old_待核")), last_line(r))
r = run("cleantemp.py", "--folder", D, "--mode", "delivery")
check("(d) delivery：退出码 0、契约行格式正确",
      r.returncode == 0 and re.match(r"^完成。已移入回收目录 \d+ 项（回收目录：.+）$",
                                     last_line(r)) is not None, last_line(r))
n_cli = int(re.search(r"已移入回收目录 (\d+) 项", r.stdout).group(1))
td_d = trash_dirs(D)
man_d = json.load(open(os.path.join(td_d[0], "manifest.json"), encoding="utf-8"))
check("(d) 回收目录根下、manifest schema=2/mode=delivery",
      len(td_d) == 1 and man_d["schema"] == 2 and man_d["mode"] == "delivery")
check("(d) CLI 数 = manifest 条目数 = 29 文件 + 1 目录",
      n_cli == len(man_d["entries"]) == 30, f"cli={n_cli} entries={len(man_d['entries'])}")
check("(d) 目录条目 type=dir（`_待核` 整棵）",
      any(e["path"] == "old_待核" and e["type"] == "dir" for e in man_d["entries"]))
check("(d) aux 全套 junk 已移走", not os.path.exists(os.path.join(D, "junk", "x.aux")))
check("(d) `.ai前/.log/.bak/时间戳.bak` 清掉",
      not [f for f in os.listdir(os.path.join(D, "junk"))
           if f.endswith((".ai前", ".log", ".bak"))])
check("(d) `_AI*` / 四类人工报告 / `_原稿.pdf` 清掉",
      not any(os.path.exists(os.path.join(D, "junk", n)) for n in
              ("note_AI协同记录.md", "x_文献核实.md", "x_回读校验报告.md",
               "x_参考文献核对.txt", "x_一校核查v2.md", "x_原稿.pdf")))
check("(d) `old_待核/` 整棵移走", not os.path.exists(os.path.join(D, "old_待核")))
check("(d) 白名单：tex/pdf/bib/sty/cls 全保留",
      all(os.path.exists(os.path.join(D, n)) for n in
          ("main.tex", "A.pdf", "refs.bib", "refs2.bib", "SLCN-MIX.sty", "journal.cls")))
check("(d) 交付清单两种命名都保留",
      os.path.exists(os.path.join(D, "交付清单.md"))
      and os.path.exists(os.path.join(D, "我的交付清单.md")))
check("(d) 引用闭包文件保留（`输入` 的 ch1.tex、pdf/png）",
      os.path.exists(os.path.join(D, "Chapters/ch1.tex"))
      and os.path.exists(os.path.join(D, "Figs/a.pdf"))
      and os.path.exists(os.path.join(D, "Figs/b.png")))
check("(d) 闭包目录整棵保留：未引用的 c.png / notes.txt 也在",
      os.path.exists(os.path.join(D, "Figs/c.png"))
      and os.path.exists(os.path.join(D, "Figs/notes.txt")))
check("(d) original/origin/原稿 系整棵跳过",
      os.path.exists(os.path.join(D, "original/keep.aux"))
      and os.path.exists(os.path.join(D, "origin/k.bbl"))
      and os.path.exists(os.path.join(D, "原稿/k.out")))
check("(d) 白名单优先：`.tex` 名字里含 `_AI` 也保留 + warning",
      os.path.exists(os.path.join(D, "bad_AI.tex")) and "保留（白名单）：bad_AI.tex" in r.stdout)
check("(d) 未识别文件保留 + warning「保留（未识别）：mystery.xyz」",
      os.path.exists(os.path.join(D, "mystery.xyz"))
      and "保留（未识别）：mystery.xyz" in r.stdout)
r = run("cleantemp.py", "--folder", D, "--restore", td_d[0])
check("(d) restore：目录条目与文件全部搬回（逐字节 sha + 目录结构）",
      r.returncode == 0 and snapshot(D) == snap_d, last_line(r))
shutil.rmtree(D, ignore_errors=True)

# 引用缺失：整体拒绝 + 零写入
D2 = tempfile.mkdtemp(prefix="b211_missing_")
mkfile(os.path.join(D2, "main.tex"), r"\includegraphics{missing_fig.png}" + "\n")
mkfile(os.path.join(D2, "x.aux"), "KEEP")
r = run("cleantemp.py", "--folder", D2, "--mode", "delivery")
check("(d) 引用缺失 → exit 2，stderr 列出缺失名",
      r.returncode == 2 and "missing_fig.png" in r.stderr and "引用缺失" in r.stderr,
      f"rc={r.returncode}")
check("(d) 引用缺失【零写入】：junk 仍在、无回收目录",
      os.path.exists(os.path.join(D2, "x.aux")) and trash_dirs(D2) == [])
check("(d) 引用缺失时 stdout 为空（预检先于任何清理输出）", r.stdout == "", repr(r.stdout))
D3 = tempfile.mkdtemp(prefix="b211_notex_")
mkfile(os.path.join(D3, "x.aux"), "KEEP")
r = run("cleantemp.py", "--folder", D3, "--mode", "delivery")
check("(d) 无顶层 .tex → exit 2 +「不是交付目录：未找到 .tex」+ 零写入",
      r.returncode == 2 and "不是交付目录：未找到 .tex" in r.stderr
      and os.path.exists(os.path.join(D3, "x.aux")), f"rc={r.returncode}")

# 230 假 png 闭包全保留（规模夹具；真库 Wenjing_Du 实测仅 0–2 图，已如实披露）
D4 = tempfile.mkdtemp(prefix="b211_230_")
lines = [r"\documentclass{article}", r"\usepackage{graphicx}", r"\begin{document}"]
for i in range(230):
    lines.append(rf"\includegraphics{{Figs/fig_{i:03d}.png}}")
lines.append(r"\end{document}")
mkfile(os.path.join(D4, "main.tex"), "\n".join(lines) + "\n")
for i in range(230):
    mkfile(os.path.join(D4, "Figs", f"fig_{i:03d}.png"), str(i))
mkfile(os.path.join(D4, "x.aux"), "J")
r = run("cleantemp.py", "--folder", D4, "--mode", "delivery")
check("(d) 230 个假 png 闭包全保留",
      r.returncode == 0 and len(glob.glob(os.path.join(D4, "Figs", "*.png"))) == 230,
      last_line(r))
check("(d) 230 图目录里的 junk 仍照清（x.aux 移走），目录本身保留",
      not os.path.exists(os.path.join(D4, "x.aux")) and os.path.isdir(os.path.join(D4, "Figs")))

# --delete：永久删、不建回收站
D5 = tempfile.mkdtemp(prefix="b211_del_")
mkfile(os.path.join(D5, "main.tex"), "\\documentclass{article}\n")
mkfile(os.path.join(D5, "x.aux"), "J")
mkfile(os.path.join(D5, "old_待核/y.bbl"), "J")
r = run("cleantemp.py", "--folder", D5, "--mode", "delivery", "--delete", "--dry-run")
check("(d) delivery --delete --dry-run：只报「将删除」、零写入",
      r.returncode == 0 and "将删除 2 项" in r.stdout
      and os.path.exists(os.path.join(D5, "x.aux")), last_line(r))
r = run("cleantemp.py", "--folder", D5, "--mode", "delivery", "--delete")
check("(d) delivery --delete：契约行「已删除 N 项（目录：…）」、无回收站",
      r.returncode == 0 and last_line(r) == f"完成。已删除 2 项（目录：{D5}）"
      and trash_dirs(D5) == [] and not os.path.exists(os.path.join(D5, "x.aux")),
      last_line(r))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) --mode workspace：合并只留最新 + 固定交付物保护 ──")
W = tempfile.mkdtemp(prefix="b211_ws_")
U1 = os.path.join(W, "2025年第1期")
os.makedirs(U1)
mkfile(os.path.join(U1, "合并.pdf"), "FIXED")
mkfile(os.path.join(U1, "合并2024_01_01_10_00_00.pdf"), "NEW")
mkfile(os.path.join(U1, "合并2024_01_01_09_00.pdf"), "MIN")
mkfile(os.path.join(U1, "合并2024_01_01.pdf"), "DATE")
mkfile(os.path.join(U1, "合并2024_01_01_08_00（修改目录）.pdf"), "MOD")
mkfile(os.path.join(U1, "信息检查.txt"), "J")
mkfile(os.path.join(U1, "a.bak"), "J")
mkfile(os.path.join(U1, "x.coop-bak-20240101"), "J")
mkfile(os.path.join(U1, "y.coop-bak-20240101", "info.bak"), "J")
mkfile(os.path.join(U1, "z_待核", "inner.txt"), "J")
mkfile(os.path.join(U1, ".DS_Store"), "J")
mkfile(os.path.join(U1, "original/old.bak"), "ORIG")
mkfile(os.path.join(U1, "000 目录/合并2020_01_01.pdf"), "PROT")
mkfile(os.path.join(U1, "000 目录/x.aux"), "PROT")
mkfile(os.path.join(U1, "contents.tex"), "PROT")
mkfile(os.path.join(U1, "Chinese Content.tex"), "PROT")
mkfile(os.path.join(U1, "扉页.pdf"), "PROT")
mkfile(os.path.join(U1, "编委名单.txt"), "PROT")
mkfile(os.path.join(U1, "封底.tex"), "PROT")
mkfile(os.path.join(U1, "扉页", "x.bak"), "PROT")
mkfile(os.path.join(U1, "编委", "x.aux"), "PROT")
mkfile(os.path.join(U1, "封底", "x.aux"), "PROT")
U2 = os.path.join(W, "2025年第2期")
mkfile(os.path.join(U2, "合并草稿.pdf"), "UNPARSABLE")
mkfile(os.path.join(U2, "合并2023_05_05_06_07.pdf"), "OLD")
U3 = os.path.join(W, "2025年第3期")
mkfile(os.path.join(U3, "合并a.pdf"), "A")
mkfile(os.path.join(U3, "合并b.pdf"), "B")
U4 = os.path.join(W, "2025年第4期")
mkfile(os.path.join(U4, "合并唯一草稿.pdf"), "ONLY")
mkfile(os.path.join(W, "合并.pdf"), "ROOTFIXED")
mkfile(os.path.join(W, "合并2020_01_01_12_00_00.pdf"), "ROOTNEW")
mkfile(os.path.join(W, "合并2020_01_01.pdf"), "ROOTOLD")
snap_w = snapshot(W)
r = run("cleantemp.py", "--folder", W, "--mode", "workspace", "--dry-run")
check("(e) workspace dry-run：0 写入、报 10 项",
      r.returncode == 0 and "将移入回收目录 10 项" in r.stdout and trash_dirs(W) == [],
      last_line(r))
r = run("cleantemp.py", "--folder", W, "--mode", "workspace")
check("(e) workspace：退出码 0、契约行 10 项",
      r.returncode == 0 and re.match(r"^完成。已移入回收目录 10 项（回收目录：.+）$",
                                     last_line(r)) is not None, last_line(r))
merges1 = sorted(f for f in os.listdir(U1) if f.startswith("合并") and f.endswith(".pdf"))
check("(e) 四形态时间：只留最新秒级 + 固定名 合并.pdf",
      merges1 == ["合并.pdf", "合并2024_01_01_10_00_00.pdf"], merges1)
check("(e) 固定名 `合并.pdf` 永不删", "合并.pdf" in merges1)
merges2 = sorted(f for f in os.listdir(U2) if f.startswith("合并") and f.endswith(".pdf"))
check("(e) 有可解析的 → 不可解析的一律保留（U2 两份都在）",
      merges2 == ["合并2023_05_05_06_07.pdf", "合并草稿.pdf"], merges2)
check("(e) 不可解析警告有出", "保留（无法解析时间" in r.stdout)
merges3 = sorted(f for f in os.listdir(U3) if f.startswith("合并") and f.endswith(".pdf"))
check("(e) 全不可解析且多份 → 全保留 + warning",
      merges3 == ["合并a.pdf", "合并b.pdf"]
      and "2 份均无法解析时间" in r.stdout, merges3)
merges4 = sorted(f for f in os.listdir(U4) if f.startswith("合并") and f.endswith(".pdf"))
check("(e) 解析不出时间且该单元只有 1 份 → 保留", merges4 == ["合并唯一草稿.pdf"], merges4)
merges0 = sorted(f for f in os.listdir(W) if f.startswith("合并") and f.endswith(".pdf"))
check("(e) root 自身也是单元：固定名 + 最新一份",
      merges0 == ["合并.pdf", "合并2020_01_01_12_00_00.pdf"], merges0)
check("(e) `信息检查*/ *.bak/ .DS_Store/ *.coop-bak-*` 清掉",
      not os.path.exists(os.path.join(U1, "信息检查.txt"))
      and not os.path.exists(os.path.join(U1, "a.bak"))
      and not os.path.exists(os.path.join(U1, ".DS_Store"))
      and not os.path.exists(os.path.join(U1, "x.coop-bak-20240101"))
      and not os.path.exists(os.path.join(U1, "y.coop-bak-20240101")))
check("(e) `*_待核/` 整棵清掉", not os.path.exists(os.path.join(U1, "z_待核")))
check("(e) original/ 整棵跳过（old.bak 仍在）",
      os.path.exists(os.path.join(U1, "original/old.bak")))
check("(e) 固定交付物整棵保护：000*/contents/Chinese Content/扉页/编委/封底",
      all(os.path.exists(os.path.join(U1, p)) for p in
          ("000 目录/合并2020_01_01.pdf", "000 目录/x.aux", "contents.tex",
           "Chinese Content.tex", "扉页.pdf", "编委名单.txt", "封底.tex",
           "扉页/x.bak", "编委/x.aux", "封底/x.aux")))
td_w = trash_dirs(W)
man_w = json.load(open(os.path.join(td_w[0], "manifest.json"), encoding="utf-8"))
check("(e) workspace manifest=2、含 dir 条目（coop-bak / _待核）",
      man_w["schema"] == 2 and man_w["mode"] == "workspace"
      and sum(1 for e in man_w["entries"] if e["type"] == "dir") == 2, man_w["mode"])
r = run("cleantemp.py", "--folder", W, "--restore", td_w[0])
check("(e) workspace 撤销逐字节（内容 sha + 相对路径 + 目录结构）",
      r.returncode == 0 and snapshot(W) == snap_w, last_line(r))

# 24 单元 / 68 份合并 PDF → 清后 24 份
W24 = tempfile.mkdtemp(prefix="b211_ws24_")
kept_expected: dict[str, str] = {}
for i in range(24):
    unit = os.path.join(W24, f"2024年第{i + 1:02d}期")
    os.makedirs(unit)
    count = 3 if i < 20 else 2
    for j in range(count):
        name = f"合并2024_01_{i + 1:02d}_10_{j:02d}_00.pdf"
        mkfile(os.path.join(unit, name), f"{i}-{j}")
        if j == count - 1:
            kept_expected[unit] = name
r = run("cleantemp.py", "--folder", W24, "--mode", "workspace")
check("(e) 24 单元 68 份合并 → 清后 24 份（移走 44）",
      r.returncode == 0 and "已移入回收目录 44 项" in r.stdout, last_line(r))
left_merges = [f for unit in kept_expected for f in os.listdir(unit)
               if f.startswith("合并") and f.endswith(".pdf")]
check("(e) 24 单元每单元恰好 1 份、且留的是最新",
      len(left_merges) == 24
      and all(os.listdir(unit) == [kept_expected[unit]] for unit in kept_expected),
      len(left_merges))
check("(e) 24 单元夹具清后 .toolbox-trash 只有 1 个回收目录",
      len(trash_dirs(W24)) == 1)

# workspace --delete
Wd = tempfile.mkdtemp(prefix="b211_wsdel_")
mkfile(os.path.join(Wd, "2024年第1期/信息检查.txt"), "J")
mkfile(os.path.join(Wd, "2024年第1期/合并2024_01_01.pdf"), "OLD")
mkfile(os.path.join(Wd, "2024年第1期/合并2024_01_02.pdf"), "NEW")
r = run("cleantemp.py", "--folder", Wd, "--mode", "workspace", "--delete")
check("(e) workspace --delete：契约行、无回收站、旧合并与 junk 都删",
      r.returncode == 0 and re.match(r"^完成。已删除 \d+ 项（目录：.+）$", last_line(r)) is not None
      and trash_dirs(Wd) == []
      and not os.path.exists(os.path.join(Wd, "2024年第1期/信息检查.txt"))
      and not os.path.exists(os.path.join(Wd, "2024年第1期/合并2024_01_01.pdf")),
      last_line(r))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (f) promote 成功后清暂存 / 失败保留 ──")
froot = tempfile.mkdtemp(prefix="b211_promote_")
stage = os.path.join(froot, "paper_待核")
mkfile(os.path.join(stage, "paper.tex"), "TEX")
final = os.path.join(froot, "paper")
r0 = coop.promote_stage(stage, final)
check("(f) promote_stage 成功：最终目录有产物、stage 还在（由调用方决定清）",
      os.path.exists(os.path.join(final, "paper.tex")) and os.path.isdir(stage))
check("(f) cleanup_stage(成功 stage) → True 且删掉",
      coop.cleanup_stage(stage) is True and not os.path.exists(stage))
check("(f) cleanup_stage(不存在) → False（不抛）",
      coop.cleanup_stage(os.path.join(froot, "nope")) is False)
check("(f) cleanup_stage('') → False（不抛）", coop.cleanup_stage("") is False)

stage2 = os.path.join(froot, "paper2_待核")
mkfile(os.path.join(stage2, "paper2.tex"), "TEX2")
final2 = os.path.join(froot, "paper2")
mkfile(os.path.join(final2, "old.tex"), "OLD")


def flaky(src, dst):
    if ".coop-tmp-" in os.path.basename(src):
        raise OSError("模拟 rename 失败")
    os.replace(src, dst)


try:
    coop.promote_stage(stage2, final2, rename=flaky)
    promoted_ok = True
except OSError:
    promoted_ok = False
check("(f) promote 失败（monkeypatch rename）→ 抛异常、旧交付目录仍是旧版",
      promoted_ok is False
      and open(os.path.join(final2, "old.tex"), encoding="utf-8").read() == "OLD")
check("(f) promote 失败 → stage 原样保留（绝不先删现场）",
      os.path.isdir(stage2) and os.path.exists(os.path.join(stage2, "paper2.tex")))
src_convert = (ENGINE / "slcn" / "convert_stage.py").read_text(encoding="utf-8")
check("(f) convert.py 在 promote_stage 成功路径之后调用 coop.cleanup_stage",
      src_convert.index("coop.cleanup_stage") > src_convert.index("coop.promote_stage")
      and "暂存目录已清理：" in src_convert)
check("(f) convert.py 的清理在 die(原子交付失败) 之后（失败分支走不到）",
      src_convert.index("coop.cleanup_stage") > src_convert.index("原子交付失败"))
shutil.rmtree(froot, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (g) 失败计数/退出码（OSError 不再静默）＋改动前逐字节对照 ──")


def head_file(path: str) -> str:
    proc = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=str(ENGINE.parent),
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"git show HEAD:{path} 失败：{proc.stderr}")
    return proc.stdout


# 旧引擎：当前 slcn 其余模块 + HEAD 版 clean.py/cleantemp.py（只替换被改的两个文件）
OLD_ENGINE = tempfile.mkdtemp(prefix="b211_oldeng_")
shutil.copytree(str(ENGINE / "slcn"), os.path.join(OLD_ENGINE, "slcn"),
                ignore=shutil.ignore_patterns("__pycache__"))
mkfile(os.path.join(OLD_ENGINE, "slcn", "clean.py"), head_file("Engine/slcn/clean.py"))
mkfile(os.path.join(OLD_ENGINE, "cleantemp.py"), head_file("Engine/cleantemp.py"))


def run_old(*args):
    return subprocess.run([PY, os.path.join(OLD_ENGINE, "cleantemp.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=ENV, cwd=OLD_ENGINE)


_STAMP_RE = re.compile(r"-[0-9]{4}_[0-9]{2}_[0-9]{2}_[0-9]{6}")


def norm_stamp(text: str) -> str:
    return _STAMP_RE.sub("-<STAMP>", text)


def same_out(old, new) -> bool:
    return ((norm_stamp(old.stdout), norm_stamp(old.stderr), old.returncode)
            == (norm_stamp(new.stdout), norm_stamp(new.stderr), new.returncode))


CMP = tempfile.mkdtemp(prefix="b211_cmp_")


def fresh(root: str, files: dict[str, str]) -> str:
    shutil.rmtree(root, ignore_errors=True)
    for rel, text in files.items():
        mkfile(os.path.join(root, rel), text)
    return root


def old_vs_new(name: str, files: dict[str, str], make_args):
    path = os.path.join(CMP, name)
    fresh(path, files)
    old = run_old(*make_args(path))
    fresh(path, files)
    new = run("cleantemp.py", *make_args(path))
    return old, new


# (c) 正常 delivery：整段 stdout+stderr+退出码 与改动前逐字节一致
DELIVERY_CMP = {
    "main.tex": r"\includegraphics{fig.png}" + "\n",
    "fig.png": "F",
    "junk.aux": "J",
    "junk.bbl": "J",
    "sub/nested.out": "J",
    "old_待核/inner.txt": "T",
    "bad_AI.tex": "WL",
    "mystery.xyz": "U",
    "original/keep.aux": "ORIG",
}
old_d, new_d = old_vs_new("delivery", DELIVERY_CMP,
                          lambda p: ("--folder", p, "--mode", "delivery"))
check("(g) 正常 delivery 新旧版逐字节一致（stdout+stderr+退出码，时间戳归一化）",
      new_d.returncode == 0 and "完成。已移入回收目录" in new_d.stdout and same_out(old_d, new_d),
      f"rc old/new={old_d.returncode}/{new_d.returncode}")

# (c) 正常 workspace：同上
WORKSPACE_CMP = {
    "2024年第1期/合并2024_01_01_10_00_00.pdf": "OLD",
    "2024年第1期/合并2024_01_02_10_00_00.pdf": "NEW",
    "2024年第1期/信息检查.txt": "J",
    "2024年第1期/x.bak": "J",
    "2024年第1期/sub_待核/in.txt": "J",
    "2024年第1期/000 目录/keep.aux": "P",
    "2024年第2期/合并草稿.pdf": "A",
    "2024年第2期/合并另一草稿.pdf": "B",
    "合并.pdf": "FIX",
    "合并2020_01_01.pdf": "R0",
    "合并2021_01_01_12_00_00.pdf": "R1",
}
old_w, new_w = old_vs_new("workspace", WORKSPACE_CMP,
                          lambda p: ("--folder", p, "--mode", "workspace"))
check("(g) 正常 workspace 新旧版逐字节一致（stdout+stderr+退出码，时间戳归一化）",
      new_w.returncode == 0 and "完成。已移入回收目录" in new_w.stdout and same_out(old_w, new_w),
      f"rc old/new={old_w.returncode}/{new_w.returncode}")

# (d) aux 默认与 --trash：旧输出不变（新增 1 条回归）
AUX_CMP = {"a.aux": "A", "b.bbl": "B", ".DS_Store": "D", "keep.tex": "T",
           "original/x.aux": "O"}
old_a0, new_a0 = old_vs_new("aux_default", AUX_CMP, lambda p: ("--folder", p))
old_a1, new_a1 = old_vs_new("aux_trash", AUX_CMP, lambda p: ("--folder", p, "--trash"))
check("(g) aux 默认/--trash 新旧版逐字节一致（时间戳归一化）",
      new_a0.returncode == new_a1.returncode == 0
      and same_out(old_a0, new_a0) and same_out(old_a1, new_a1),
      f"rc={new_a0.returncode}/{new_a1.returncode}")

# (a) 合成反例：delivery 里只读子目录内的 junk 文件 → 失败可计数、非零退出
F = tempfile.mkdtemp(prefix="b211_fail_")
mkfile(os.path.join(F, "main.tex"), r"\includegraphics{fig.png}" + "\n")
mkfile(os.path.join(F, "fig.png"), "F")
mkfile(os.path.join(F, "normal.aux"), "J")
mkfile(os.path.join(F, "locked/stuck.aux"), "J")
os.chmod(os.path.join(F, "locked"), 0o555)
r = run("cleantemp.py", "--folder", F, "--mode", "delivery", "--dry-run")
check("(g) dry-run 行为不变（只读夹具也不产生失败，退出码 0、零写入）",
      r.returncode == 0 and "失败" not in r.stderr and trash_dirs(F) == [],
      f"rc={r.returncode}")
if os.name == "nt":
    # Windows：目录「只读位」不阻止删除/移动（POSIX 语义 vs Windows ACL/属性），chmod 无法构造 OSError 反例。
    # F1「失败计数/非零退出」路径在 POSIX（mac/Linux）全量覆盖；Windows 侧登记为已知测试缺口（CI/preflight 如实记录）。
    print("\nSKIP (g) 只读目录失败反例（Windows 无 POSIX 目录只读语义；F1 失败计数路径由 POSIX 覆盖）")
else:
    r = run("cleantemp.py", "--folder", F, "--mode", "delivery")
    out = r.stdout + r.stderr
    check("(g) 只读目录：退出码 2、输出点名失败路径 + 失败计数、不打成功契约行",
          r.returncode == 2 and "locked/stuck.aux" in out and "失败 1 项" in r.stderr
          and "完成。" not in r.stdout, f"rc={r.returncode}")
    td_f = trash_dirs(F)
    # (b) 同一次运行里正常 junk 仍成功移入回收站（不回滚已成功项）
    check("(g) 同次运行正常 junk 仍移入回收站（源消失、trash 里在）",
          not os.path.exists(os.path.join(F, "normal.aux")) and len(td_f) == 1
          and os.path.exists(os.path.join(td_f[0], "normal.aux")))
    man_f = json.load(open(os.path.join(td_f[0], "manifest.json"), encoding="utf-8"))
    check("(g) 失败项源文件仍在、manifest 只记成功项",
          os.path.exists(os.path.join(F, "locked/stuck.aux"))
          and [e["path"] for e in man_f["entries"]] == ["normal.aux"],
          man_f["entries"])
    os.chmod(os.path.join(F, "locked"), 0o755)

    # --delete 模式同样计数
    Fd = tempfile.mkdtemp(prefix="b211_faildel_")
    mkfile(os.path.join(Fd, "main.tex"), r"\includegraphics{fig.png}" + "\n")
    mkfile(os.path.join(Fd, "fig.png"), "F")
    mkfile(os.path.join(Fd, "normal.aux"), "J")
    mkfile(os.path.join(Fd, "locked/stuck.aux"), "J")
    os.chmod(os.path.join(Fd, "locked"), 0o555)
    r = run("cleantemp.py", "--folder", Fd, "--mode", "delivery", "--delete")
    check("(g) --delete 同样计数：退出码 2、正常 junk 已永久删、失败项仍在",
          r.returncode == 2 and "失败 1 项" in r.stderr and "完成。" not in r.stdout
          and not os.path.exists(os.path.join(Fd, "normal.aux"))
          and os.path.exists(os.path.join(Fd, "locked/stuck.aux")), f"rc={r.returncode}")
    os.chmod(os.path.join(Fd, "locked"), 0o755)

for d in ((F, CMP, OLD_ENGINE) + (() if os.name == "nt" else (Fd,))):
    shutil.rmtree(d, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
for d in (root, root2, root3, D2, D3, D4, D5, W24, Wd):
    shutil.rmtree(d, ignore_errors=True)
for d in glob.glob(os.path.join(tempfile.gettempdir(), "b211_*")):
    shutil.rmtree(d, ignore_errors=True)

if fails:
    print(f"\n失败 {len(fails)} 项：{fails}")
    print(f"SUMMARY total={pass_n + len(fails)} pass={pass_n} fail={len(fails)}")
    sys.exit(1)
print(f"\n全部 {pass_n} 项断言通过")
print(f"SUMMARY total={pass_n} pass={pass_n} fail=0")
sys.exit(0)
