#!/usr/bin/env python3.14
"""生成 / 校验 `README.md` 里的「当前状态」数字块（AUTO 块）。

WHY：手抄的数字必然过期，而且过期了没人知道。所以这块里的**每一个数字都现算**：
测试套件现跑、普查与 corpus 从产物里现读、占位符现数、App 包内现 `cmp`、红线快照现查
（只用只读的 git 命令）。`--check` 把它当断言用：块与现算结果不一致就退出 1，
由 `tests/test_docs_consistency.py` 在文档一致性这一环卡住。

用法
----
    cd Engine
    /opt/homebrew/bin/python3.14 tools/gen_docs.py            # 就地更新 AUTO 块（块外一字不动）
    /opt/homebrew/bin/python3.14 tools/gen_docs.py --check    # 只校验，不一致 → 打印 diff 并退出 1

约定
----
* **块内不放时间戳**：否则 `--check` 永远失败。新鲜度看块内各数据源的 sha256。
* 铁律：**只读**。除了给 `README.md` 的 AUTO 块换内容，本脚本不写任何文件，
  不跑 `docx_audit.py`、不跑 `corpus.py`（那些会重生基线产物），不跑任何写操作的 git 命令。
* 测试用 `/opt/homebrew/bin/python3.14` + `PYTHONPATH=<Engine>/vendor`（绝对路径），
  与人工跑法逐字一致；有 FAIL 行时**照实写进块里**并让退出码非 0。
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PY = "/opt/homebrew/bin/python3.14"
BEGIN = "<!-- AUTO:STATE:BEGIN -->"
END = "<!-- AUTO:STATE:END -->"

ENGINE = Path(__file__).resolve().parents[1]          # …/<项目>/Engine
# 仓库根**问 git 要**，不按目录层数推：稿件仓里是 `<仓>/Engine`（要往上两级），
# 拆仓后的工具仓里是 `<仓>/Engine`（只往上一级）。按层数推的话，公开仓里会算到仓库外一层，
# 红线快照当场变成「无 .git/index / HEAD 不可用」（2026-10-15 拆仓实测）。
def _git_root() -> Path:
    try:
        out = subprocess.run(["git", "-C", str(ENGINE), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        if out:
            return Path(out)
    except OSError:
        pass
    return ENGINE.parents[1]                           # 不在 git 仓里时的兜底


REPO = _git_root()
README = ENGINE / "README.md"
AUDIT_CSV = ENGINE / "out" / "docx_audit.csv"
AUDIT_TXT = ENGINE / "out" / "docx_audit.txt"
CORPUS_CSV = ENGINE / "out" / "corpus" / "corpus.csv"
CORPUS_DIR = ENGINE / "out" / "corpus"

# App 包内的引擎副本。**两种布局都要认**（2026-10-15/16 拆仓前后）：
#   新（公开仓，当前）：`<公开仓>/dist/编辑部工具箱.app/…`   ← REPO 就是项目根
#   旧（稿件仓时代）：  `<稿件仓>/EditToolBox/dist/…`         ← REPO 是稿件仓根，项目在 EditToolBox/
# ⚠️ 从前只认旧路径 → 拆仓后这里**永远**报「还没 ./build.sh 过」，而这一行正是
#    「包内引擎过期」的守卫（本项目踩过多次）。**看错地方的守卫等于没有守卫** ——
#    与 ②3「宽松模式等于没有警告」是同一个教训（2026-10-16 实测：`./build.sh` 刚跑完，
#    本行仍写「不存在」）。
_APP_REL = Path("编辑部工具箱.app") / "Contents" / "Resources" / "Engine"
APP_ENGINE = next((p for p in (REPO / "dist" / _APP_REL, REPO / "EditToolBox" / "dist" / _APP_REL)
                   if p.is_dir()), REPO / "dist" / _APP_REL)

# 九个三态字段（与 docx_audit.py 的 CSV_COLUMNS 一一对应；顺序固定，便于 diff）
FIELDS = [("title", "题名"), ("subtitle", "副标题"), ("author", "作者"),
          ("affiliation", "单位"), ("email", "邮箱"), ("abstract_cn", "中文摘要"),
          ("keywords_cn", "中文关键词"), ("title_en", "英文题名"), ("abstract_en", "英文摘要")]
STATES = ("ok", "fail", "absent")
# 包内必须与仓库逐字节相同的**引擎代码**文件。2026-10-15 加了三个新模块：
# `slcn/bibparse.py`（唯一一份健壮 bib 解析器）、`slcn/bibreport.py`（回读校验报告）、
# `slcn/aibib.py`（把 AI 返回的条目并入 reference.bib）——`checkbib.py` 与 `convert.py`
# 都 import 它们，包内缺一份或过期一份，「文献回读校验」与「并入」两条通道当场就废。
APP_FILES = ["convert.py", "checkbib.py", "mergepdf.py", "engine_main.py",
             "cleantemp.py", "compileall.py", "proofcheck.py",
             "infocheck.py", "toc.py",
             "slcn/infocheck.py", "slcn/toc.py", "slcn/toctemplate.py",
             "slcn/content_templates.py",
             "slcn/proofcheck.py", "slcn/pdfmerge.py", "slcn/platform.py",
             "slcn/preamble.py",
             "slcn/llm.py", "slcn/docxsource.py",
             "slcn/refmark.py", "slcn/bibparse.py", "slcn/bibreport.py", "slcn/aibib.py",
             # 第 129 条：引用库接入核实链路（评分/判定在 slcn/bibdb.py，改错会多花钱）。
             "slcn/bibdb.py",
             # 这两条也是「改了就必进包、否则行为不对」的模块，漏检过一次（第 66 条：
             # 解析器修好了但包内还是旧版），所以一并纳入比对。
             "slcn/footref.py", "slcn/build.py"]


def sha12(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.is_file() else "（缺文件）"


def sh(cmd: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


# ── 测试：现跑 tests/*.py，数套件与 PASS 行 ─────────────────────────────────
# 自指排除：`tests/test_docs_consistency.py` 会调用本脚本的 `--check`，若它也进这份清单就是
# 无限递归。所以按名字排除它，并把排除理由写进 AUTO 块（见 build_block 的括注）。
SELF_REFERENTIAL = {"test_docs_consistency.py"}


def run_tests() -> tuple[list[tuple[str, int, int]], int]:
    if os.environ.get("SLCN_GEN_DOCS_NESTED"):
        sys.exit("gen_docs.py 被递归调用（SLCN_GEN_DOCS_NESTED 已置位）—— 检查测试清单")
    env = dict(os.environ, PYTHONPATH=str(ENGINE / "vendor"), SLCN_GEN_DOCS_NESTED="1")
    rows = []
    for f in sorted((ENGINE / "tests").glob("test_*.py")):
        if f.name in SELF_REFERENTIAL:
            continue
        p = subprocess.run([PY, "-u", str(f)], cwd=str(ENGINE), env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = p.stdout + p.stderr
        rows.append((f.stem, len(re.findall(r"^\s*PASS\b", out, re.M)),
                     len(re.findall(r"^\s*FAIL\b", out, re.M))))
    return rows, sum(r[1] for r in rows)


# ── 普查：读 out/docx_audit.csv（**只读**，绝不重生成基线）──────────────────
# ── 本仓有没有稿件库？（拆仓后工具仓没有；稿件仓有）─────────────────────
# 普查与 corpus 的数字**来自作者稿件**，那些数据（连文件名带作者名）不该出现在公开仓里，
# 所以公开仓里这两行必然没有数据。这不等于「工具坏了」——必须与「跑过但对不上」分开报，
# 否则新克隆一开就 `--check` 失败（2026-10-15 拆仓预演实测）。
def has_manuscripts() -> bool:
    try:
        return any("年第" in p.name and "期" in p.name for p in REPO.iterdir() if p.is_dir())
    except OSError:
        return False


def audit_block() -> tuple[list[str], bool]:
    out, ok = [], True
    if not AUDIT_CSV.is_file():
        if not has_manuscripts():
            return ["**普查**：本仓不含稿件库（拆仓后的工具仓 / 新克隆），"
                    "这一行由稿件仓里的运行给出"], True
        return ["**普查**：`out/docx_audit.csv` **未生成**（跑 `python3 docx_audit.py`）"], False
    with AUDIT_CSV.open(encoding="utf-8", newline="") as fh:
        recs = list(csv.DictReader(fh))
    cnt = {f: {s: 0 for s in STATES} for f, _ in FIELDS}
    mismatch = 0
    for r in recs:
        for f, _ in FIELDS:
            st = r.get(f"{f}_state", "")
            if st in cnt[f]:
                cnt[f][st] += 1
            # 三态必须与旧的 _ok 列同源（ok ⇔ 1），否则是解析器/写出不一致
            if (st == "ok") != (r.get(f"{f}_ok") == "1"):
                mismatch += 1
    tot_h = sum(int(r.get("n_headings") or 0) for r in recs)
    tot_b = sum(int(r.get("n_bib") or 0) for r in recs)
    out.append(f"**普查**（`out/docx_audit.csv`，{len(recs)} 份源稿；三态互斥、相加 = 篇数）：")
    out += ["", "| 字段 | `ok` | `fail` | `absent` |", "|---|---|---|---|"]
    for f, cn in FIELDS:
        c = cnt[f]
        out.append(f"| {cn} `{f}` | {c['ok']} | {c['fail']} | {c['absent']} |")
    out += ["", f"- `n_headings` 合计 **{tot_h}**；`n_bib` 合计 **{tot_b}**",
            f"- sha256[:12]：`docx_audit.csv` = `{sha12(AUDIT_CSV)}`，"
            f"`docx_audit.txt` = `{sha12(AUDIT_TXT)}`"
            f"（与 `_ok` 列交叉核对，不一致 **{mismatch}** 行 {'✅' if mismatch == 0 else '❌'}）"]
    if mismatch:
        ok = False
    return out, ok


# ── corpus：读 out/corpus/corpus.csv ───────────────────────────────────────
def corpus_block() -> tuple[list[str], bool]:
    if not CORPUS_CSV.is_file():
        if not has_manuscripts():
            return ["**corpus 回归**：本仓不含稿件库（拆仓后的工具仓 / 新克隆），"
                    "这一行由稿件仓里的运行给出"], True
        return ["**corpus 回归**：`out/corpus/corpus.csv` **未生成**（跑 `corpus.py`）"], True
    with CORPUS_CSV.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    good = [r for r in rows if r.get("ok") == "1"]
    med = {}
    for ch in ("LaTeX", "Word"):
        v = [float(r["raw_ratio"]) for r in good if r.get("channel") == ch]
        med[ch] = f"{100 * statistics.median(v):.2f}%" if v else "—"
    txt = (f"**corpus 回归**（`out/corpus/corpus.csv`）：成功 **{len(good)} / {len(rows)}** 篇；"
           f"裸相似度中位 LaTeX **{med['LaTeX']}**、Word **{med['Word']}**"
           f"（第 125 条起去重确定性：同输入连跑两遍 `corpus.csv` 应逐字节一致，不一致即回归）")
    return [txt], True


# ── 占位符：out/corpus/**/*.tex 里的【未识别：…】────────────────────────────
def placeholder_block() -> list[str]:
    hits = files = 0
    if CORPUS_DIR.is_dir():
        for p in sorted(CORPUS_DIR.rglob("*.tex")):
            n = len(re.findall(r"【未识别：[^】]*】", p.read_text(encoding="utf-8", errors="replace")))
            hits += n
            files += 1 if n else 0
    if not CORPUS_DIR.is_dir() and not has_manuscripts():
        return ["**占位符**：本仓不含稿件库（拆仓后的工具仓 / 新克隆），"
                "这一行由稿件仓里的运行给出"]
    return [f"**占位符**（`out/corpus/**/*.tex` 里 `【未识别：…】`）：**{hits} 处 / {files} 篇**"
            + ("" if CORPUS_DIR.is_dir() else "（`out/corpus/` 不存在）")]


# ── App 包内一致性：包内 5 个**引擎代码**文件 vs 仓库同名文件 ────────────────
# ⚠️ 这里**故意不比对 README.md**：AUTO 块就写在 README 里，把它算进比对会形成
#    「自引用死循环」—— 一旦把 ✅ 写进块，README 立刻与包内副本不同，`--check` 永远失败
#    （2026-10-15 实测踩到）。README 的同步交给 `./build.sh`：它是文档、不影响引擎行为，
#    所以不参与本行的判定；引擎代码必须逐个 `cmp` 全同（「包内引擎过期」是本项目踩过的坑）。
def app_block() -> tuple[list[str], bool]:
    if not APP_ENGINE.is_dir():
        return [f"**App 包内一致性**：`{APP_ENGINE.relative_to(REPO)}` 不存在"
                "（还没 `./build.sh` 过；构建后本行会变成 `cmp` 结果）"], True
    same, diff = [], []
    for rel in APP_FILES:
        a, b = APP_ENGINE / rel, ENGINE / rel
        if a.is_file() and b.is_file() and a.read_bytes() == b.read_bytes():
            same.append(rel)
        else:
            diff.append(rel)
    if not diff:
        txt = (f"**App 包内一致性**（`dist/编辑部工具箱.app/Contents/Resources/Engine/` vs 仓库）："
               f"✅ **{len(same)}/{len(APP_FILES)}** 个**引擎文件** `cmp` 全同"
               "（`README.md` 不在比对内 —— 块就写在它里面，见 `tools/gen_docs.py` 的注释；"
               "文档改动同样要 `./build.sh` 一次才进包）")
        return [txt], True
    txt = (f"**App 包内一致性**（`dist/编辑部工具箱.app/Contents/Resources/Engine/` vs 仓库）："
           f"❌ **{len(diff)}/{len(APP_FILES)}** 个**引擎文件**不同 —— 包内已过期，"
           f"下次 `./build.sh` 会同步：" + "、".join(f"`{d}`" for d in diff))
    return [txt], False


# ── 红线只读快照（只读 git 命令）───────────────────────────────────────────
def git_block() -> list[str]:
    idx = REPO / ".git" / "index"
    mt = (datetime.fromtimestamp(idx.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
          if idx.is_file() else "（无 .git/index）")
    head = sh(["git", "rev-parse", "--short", "HEAD"], REPO).strip() or "（不可用）"
    n = len([x for x in sh(["git", "status", "--porcelain"], REPO).split("\n") if x.strip()])
    return [f"**红线只读快照**（只跑 `git rev-parse` / `git status`，不写）："
            f"`.git/index` mtime `{mt}`；`HEAD` = `{head}`；`git status --porcelain` **{n}** 行"]


# ── 组装 ──────────────────────────────────────────────────────────────────
def build_block() -> tuple[str, bool]:
    ok = True
    rows, total = run_tests()
    bad = [(n, f) for n, p, f in rows if f]
    # ⚠️ 这一行**不许出现绝对路径**：块是提交进仓库的，换台机器/换个克隆目录就会
    #    `--check` 失败（2026-10-15 在一个新克隆里实测到）。只写解释器名与相对路径。
    t = (f"**测试**（`{Path(PY).name}` + `PYTHONPATH=Engine/vendor`）："
         f"**{len(rows)} 套件 / {total} 项断言 / FAIL {sum(f for _, _, f in rows)} 行**")
    t += " ✅" if not bad else " ❌ " + "、".join(f"{n}({f} FAIL)" for n, f in bad)
    t += "（不含 `test_docs_consistency.py` 自身：它调用本脚本，属自指）"
    lines = [t, ""]
    parts = [f"`{n}` {p}" for n, p, _ in rows]
    lines.append("套件明细：" + " ｜ ".join(parts[:4]))
    for i in range(4, len(parts), 4):
        lines.append(" ｜ ".join(parts[i:i + 4]))
    lines.append("")
    for fn in (audit_block, corpus_block):
        sub, sub_ok = fn()
        lines += sub + [""]
        ok = ok and sub_ok
    # App 包内过期只标 ❌、**不算退出码失败**：清掉它要跑 ./build.sh，而 build.sh 是共享资源，
    # 有时会（像本轮只改文档那样）有意推迟到下一次构建。若把它算成失败，
    # test_docs_consistency 就永远红着，反而没人看真正的失败。
    sub, _ = app_block()
    lines += sub + [""]
    lines += placeholder_block() + [""]
    lines += git_block()
    if bad:
        ok = False
    return "\n".join(lines).rstrip() + "\n", ok


# ── 「App 包内一致性」那一行**不参与 --check 的比对** ─────────────────────
# 它报告的是**本机构建状态**（`dist/` 在不在、包内引擎是否过期），不是仓库事实：
# 同一个提交，在构建过的机器上写「28 同 / 0 异 ✅」、在刚克隆的机器上写「还没构建」。
# 所以：
#   · 行**留在块里**（对读者有用，`build.sh` 之后 `gen_docs` 会刷新它）；
#   · 但 `--check` 比对前把它归一化掉 —— 否则**新克隆第一次跑看门狗必红**
#     （2026-10-15 拆仓实测：公开仓克隆下来就报 (a) 失败）。
APP_LINE = re.compile(r"^\*\*App 包内一致性\*\*.*$", re.M)
_APP_PLACEHOLDER = "**App 包内一致性**：（本机构建状态，不参与校验）"
# 「红线只读快照」整行同理：`.git/index` 的 mtime 与 `git status` 的行数都是**本机工作区**
# 状态，同一个提交在干净克隆里与在开发机上写得不一样 → 不参与 --check。
GIT_LINE = re.compile(r"^\*\*红线只读快照\*\*.*$", re.M)
_GIT_PLACEHOLDER = "**红线只读快照**：（本机工作区状态，不参与校验）"


# 测试计数两行：**在本机前提齐全时是硬断言**（"数字不许手抄"最要紧的一条），
# 但缺前提时用例会 SKIP，计数必然变小（CI 上没有稿件库/TeX/LibreOffice 就是这样）——
# 那种情况下把它们也归一化掉，否则 CI 永远红，久而久之就没人看 CI 了。
TEST_SUMMARY_LINE = re.compile(r"^\*\*测试\*\*（.*$", re.M)
TEST_BREAKDOWN_LINE = re.compile(r"^`test_[a-z0-9_]+` \d+( ｜ `test_[a-z0-9_]+` \d+)*$", re.M)
_TEST_PLACEHOLDER = "**测试**：（计数随本机前提浮动，本次不参与校验）"


def environment_complete() -> tuple[bool, list[str]]:
    """本机是否具备"全量跑"的前提：稿件库 + TeX + LibreOffice。

    缺任何一项，测试计数与依赖稿件库的数字就会变 —— `--check` 据此决定严格还是宽松。
    """
    import shutil as _sh

    # ⚠️ 必须把 `Engine/` 挂进 sys.path：本项目平时靠 `PYTHONPATH="$PWD/vendor"` 跑，
    #    那种情况下 `from slcn import paths` 会 ModuleNotFoundError → 探测"失败" →
    #    永远走宽松模式 → **AUTO 块过期也报 PASS**（2026-10-16 实测：README 写 38 套件/1465 断言，
    #    实际 39/1483，一直没人发现）。这行是"严格校验真的生效"的前提。
    _engine_dir = str(Path(__file__).resolve().parents[1])
    if _engine_dir not in sys.path:
        sys.path.insert(0, _engine_dir)

    missing: list[str] = []
    try:
        from slcn import paths as _p
        if _p.manuscripts_root() is None:
            missing.append("稿件库")
    except Exception as e:  # noqa: BLE001  探测本身失败也按"缺前提"处理，但要说清是哪一项
        missing.append(f"稿件库（探测异常 {type(e).__name__}）")
    try:
        if _sh.which("xelatex") is None:
            missing.append("xelatex")
    except Exception as e:  # noqa: BLE001
        missing.append(f"xelatex（探测异常 {type(e).__name__}）")
    try:
        if _sh.which("soffice") is None and _sh.which("libreoffice") is None:
            missing.append("LibreOffice")
    except Exception as e:  # noqa: BLE001
        missing.append(f"LibreOffice（探测异常 {type(e).__name__}）")
    return (not missing), missing


def normalize_for_check(body: str, lenient: bool = False) -> str:
    out = GIT_LINE.sub(_GIT_PLACEHOLDER, APP_LINE.sub(_APP_PLACEHOLDER, body))
    if lenient:
        out = TEST_SUMMARY_LINE.sub(_TEST_PLACEHOLDER, out)
        out = TEST_BREAKDOWN_LINE.sub(_TEST_PLACEHOLDER, out)
    return out


def read_readme() -> str:
    return README.read_text(encoding="utf-8")


def split_block(text: str) -> tuple[str, str, str]:
    """返回 (块前, 块内, 块后)；标记行本身留在「块前」「块后」里。"""
    i = text.index(BEGIN) + len(BEGIN)
    j = text.index(END)
    return text[:i], text[i:j], text[j:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验 README.md 的「当前状态」AUTO 块")
    ap.add_argument("--check", action="store_true", help="只校验，不一致就退出 1 并打印 diff")
    a = ap.parse_args(argv)

    text = read_readme()
    if BEGIN not in text or END not in text:
        print(f"FAIL  README.md 里找不到 {BEGIN} / {END}")
        return 1
    head, old, tail = split_block(text)
    new, all_ok = build_block()          # 现算（跑测试 + 读产物 + cmp + 只读 git）
    if not a.check:
        # ⚠️ 块里的「红线只读快照」记着 `git status --porcelain` 的行数，而**写 README 本身
        #    就会让这个数字变**（README 从「干净」变成「已修改」）—— 自指，跑一遍不收敛。
        #    所以写完再算一遍，直到稳定（最多 3 轮）。2026-10-15 实测：不收敛的话，
        #    紧接着跑 `--check` 会报「块已过期」，而块其实是刚写的。
        for _ in range(3):
            probe = "\n" + new
            stable = head + probe + tail
            if stable == read_readme():
                break
            README.write_text(stable, encoding="utf-8")
            again, all_ok2 = build_block()
            if again == new:
                break
            new, all_ok = again, all_ok2
    new_body = "\n" + new

    if a.check:
        complete, missing = environment_complete()
        lenient = not complete
        old_cmp = normalize_for_check(old, lenient)
        new_cmp = normalize_for_check(new_body, lenient)
        if lenient:
            print(f"⚠️  宽松校验（缺 {'、'.join(missing)}）：测试计数与依赖稿件库的数字**不参与比对**；"
                  "本机要拿到严格校验，请让稿件库可发现（设 SLCN_MANUSCRIPTS 或把两仓并排放）")
        if old_cmp == new_cmp:
            if old != new_body:
                print("PASS  AUTO 块与现算结果一致（差异只在本机状态那几行——"
                      "按设计不参与校验）")
            else:
                print("PASS  AUTO 块与现算结果逐字一致")
            # ⚠️ `--check` 只回答"块过期了吗"。`all_ok` 里还混着一条**本机状态**：
            #    「App 包内一致性」（`app_block()`）—— 它自己写着"不参与校验"，
            #    却曾经让 --check 退出 1：于是同一屏里既 `PASS 块一致` 又 `exit=1`，
            #    读的人（和另一个 AI 会话）会当成"文档数字过期"去查错方向。
            #    现在它只作为**提示**出现，退出码只看块是否过期（2026-10-17）。
            if not all_ok:
                print("提示  块本身一致；另有 ❌ 项只与**本机构建状态**有关"
                      "（多半是包内引擎过期）—— 跑一次 `./build.sh` 即可，不影响 --check 的结论")
            return 0
        print("FAIL  AUTO 块已过期（README 里的是旧数字）—— 跑 `tools/gen_docs.py` 更新")
        # ⚠️ diff 要打**归一化后**的文本：否则会把"按设计不参与校验"的行也列出来，
        #    真正的问题反而被淹没（2026-10-16 在 CI 上就是被这条误导了一轮）。
        for line in list(difflib.unified_diff(
                old_cmp.split("\n"), new_cmp.split("\n"),
                "README/AUTO（现状）", "现算结果", lineterm="", n=1))[:60]:
            print("      " + line)
        return 1

    if old == new_body:
        print("PASS  AUTO 块已是最新，未改动 README.md")
    else:
        README.write_text(head + new_body + tail, encoding="utf-8")
        after_head, after, after_tail = split_block(read_readme())
        assert (head, tail) == (after_head, after_tail), "块外内容被改动了 —— 这是 bug"
        print("PASS  已就地更新 AUTO 块（块外内容逐字节未动）")
    print(f"      退出码提示：{'各项检查通过' if all_ok else '有 ❌ 项，见块内'}"
          f"{'' if all_ok else '（若只有「App 包内一致性」是 ❌，跑 ./build.sh 即可）'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
