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
    /opt/homebrew/bin/python3.14 tools/gen_docs.py --check --no-test-runs
                                                              # 只校验：不跑套件（测试计数行不参与比对）
    /opt/homebrew/bin/python3.14 tools/gen_docs.py --cmp-app   # 只比对 App 包内引擎白名单（毫秒级）

**第四十四批③a（O9）：App 包内一致性已移出 AUTO 块** ——
它依赖「本机是否刚 `./build.sh` 过」，留在 AUTO 里会让测试计数（经 `test_selfcheck` →
`selfcheck` 的 App cmp）依赖 App 新鲜度，引擎改动批被迫「gen_docs×2 + build×2」。
现在：AUTO 不再有这一行（历史行由 `normalize_for_check` 删除式归一化，迁移期不误红）；
包内一致性由 **`build.sh` 末尾** `--cmp-app`（白名单 `APP_FILES` 28 个文件，毫秒级）判定并报错，
`selfcheck` 的同类检查可用 `--skip-app` 跳过（`test_selfcheck` 用它）。

**第四十一批 O1/O2（性能，语义不变）**：
* **写模式不再重算整块**：块里的「红线只读快照」含 `git status --porcelain` 的行数，而写 README
  本身会让这个数字变（自指）。从前靠「写完再 `build_block()` 一遍、直到稳定」收敛 —— 每次
  重算都要**再跑一整遍套件**，于是干净树过期时跑 3 遍（≈180s）、README 已脏时 2 遍（≈120s）。
  现在写循环只**就地替换**那一行（只跑两条只读 git 命令），测试/普查/corpus/App 各块**只算一次**。
* **`--no-test-runs` 只给"校验"用**：full 套件里的 `test_docs_consistency.py` 刚刚已经跑过同一批
  套件，再让本脚本的 `--check` 跑一遍等于 full 双重耗时。该模式下**不跑套件**、比对时把 AUTO 的
  「测试」计数行整段剔除；**非测试行（普查/corpus/占位符等）仍严格比对**（反例测试见
  `tests/test_docs_consistency.py` 的 (a2)）。**测试计数行的守卫**由**独立**的
  `gen_docs.py --check`（CI `ci.yml` 的独立步骤 / 发版前 `selfcheck.py --full` 的严格模式 /
  手工显式跑）负责 —— 分工写在 `test_docs_consistency.py` 的 (a) 注释里。
* 写模式**不允许** `--no-test-runs`（写出去的数字必须是现算的，不能是占位值）。

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
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PY = "/opt/homebrew/bin/python3.14"
BEGIN = "<!-- AUTO:STATE:BEGIN -->"
END = "<!-- AUTO:STATE:END -->"

# ── O13（第四十六批）：重活证据落盘（`run_record.py` 与本脚本同目录）──────────
# 每次运行**尽力**写一份 manifest（label=gen_docs-write / gen_docs-check）到
# `/tmp/audit_lead/runs/`：记录耗时 / 是否 --check / 是否跑了套件 / README 是否变化 +
# 引擎 py 指纹。**纯证据**：工具缺失或写失败只告警，绝不影响本脚本的计算、输出与退出码。
# `SLCN_RUN_RECORD=0` 可关闭（需要完全静默的调用方/测试用）。
try:
    _TOOLS_DIR = Path(__file__).resolve().parent
    if str(_TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(_TOOLS_DIR))
    import run_record as _run_record
except Exception:  # noqa: BLE001 —— 证据工具不可用不能拖垮 gen_docs
    _run_record = None

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

# ── O14（第四十七批）：AUTO 输入冻结 `out/auto-inputs.json` ──────────────────
# 写模式把「AUTO 块依赖的输入」的 sha（corpus/audit/engine py/tests）+ 测试计数缓存
# + 写出的块指纹落盘；`--check` 据此走**快速通道**（输入未变 → 不跑套件直接 PASS），
# 写模式在 engine/tests 未变时**复用测试计数**（corpus/audit 变了也不再整跑套件）。
# 背景：批46 O5=0 的「AUTO-corpus 循环」——重跑 corpus → AUTO corpus sha 变 → 再 gen_docs+build。
# `Engine/out/` 是 gitignore 的产物目录；测试可用 SLCN_AUTO_INPUTS 指向临时文件。
AUTO_INPUTS = Path(os.environ.get("SLCN_AUTO_INPUTS") or (ENGINE / "out" / "auto-inputs.json"))
AUTO_INPUTS_SCHEMA = 1
_LAST_TEST_ROWS: list[tuple[str, int, int]] | None = None
_FAST_PATH_USED = False
_CACHED_TESTS_USED = False

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
             # 第 132 条：mac 界面「引用库」5 页的数据源（只进 mac 包、不进 Windows
             # 分发器；build.sh 的白名单与 tests/test_platform.py 的 (g) 组一起钉着）。
             "bibdb.py",
             # 这两条也是「改了就必进包、否则行为不对」的模块，漏检过一次（第 66 条：
             # 解析器修好了但包内还是旧版），所以一并纳入比对。
             "slcn/footref.py", "slcn/build.py",
             # 第四十四批③a（O9/F4）：`slcn/sources.py` 是源识别（original/ 下每个文件扮演什么
             # 角色）的唯一出处；第四十二批 mac-shell 发现白名单漏了它 → 27→28。
             "slcn/sources.py"]


def sha12(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.is_file() else "（缺文件）"


def sh(cmd: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


# ── 测试：现跑 tests/*.py，数套件与 PASS 行 ─────────────────────────────────
# 自指排除：`tests/test_docs_consistency.py` 会调用本脚本的 `--check`，若它也进这份清单就是
# 无限递归。所以按名字排除它，并把排除理由写进 AUTO 块（见 build_block 的括注）。
SELF_REFERENTIAL = {"test_docs_consistency.py"}


def test_files() -> list[Path]:
    """参与 AUTO「测试」计数的套件清单（排除自指的 test_docs_consistency.py）。"""
    return [f for f in sorted((ENGINE / "tests").glob("test_*.py"))
            if f.name not in SELF_REFERENTIAL]


def run_tests() -> tuple[list[tuple[str, int, int]], int]:
    if os.environ.get("SLCN_GEN_DOCS_NESTED"):
        sys.exit("gen_docs.py 被递归调用（SLCN_GEN_DOCS_NESTED 已置位）—— 检查测试清单")
    env = dict(os.environ, PYTHONPATH=str(ENGINE / "vendor"), SLCN_GEN_DOCS_NESTED="1")
    rows = []
    for f in test_files():
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


# ── App 包内一致性：**已移出 AUTO 块**（第四十四批③a / O9）────────────────
# 从前这一行由 AUTO 块现算，而 AUTO 的测试计数会跑 `test_selfcheck` → 它又跑 `selfcheck`
# （含 App 包内 cmp）→ 引擎改动后、App 还没重建时出现 **FAIL 瞬态**：AUTO 记下 FAIL，
# 必须先 `./build.sh` 再重算、然后再 build 一次，形成「gen_docs×2 + build×2」的循环依赖。
# 现在：
#   · AUTO 块**不再有**这一行（README 里的历史行由 normalize_for_check **删除式**归一化，
#     迁移期 `--check` 不会因此红）；
#   · 包内一致性改由 **`build.sh` 末尾**调用 `gen_docs.py --cmp-app`（毫秒级）判定并报错；
#   · `selfcheck.py` 的同类检查保留（`--skip-app` 可跳过，供 `test_selfcheck` 用）。
# 单一出处仍是下面的 `APP_FILES`（build.sh / selfcheck / 本脚本共用）。
def app_cmp(app_dir: Path | None = None) -> tuple[bool, list[str], list[str]]:
    """比对**包内引擎**与仓库同名文件（白名单 APP_FILES）。返回 (ok, same, diff)。

    `app_dir` = 包内 `…/Resources/Engine`；None 时用默认 App 布局（`APP_ENGINE`）。
    纯读文件、毫秒级；供 `--cmp-app`（build.sh 末尾）与 selfcheck 共用。
    """
    target = Path(app_dir) if app_dir is not None else APP_ENGINE
    same: list[str] = []
    diff: list[str] = []
    if not target.is_dir():
        return False, same, list(APP_FILES)          # 目录都不存在 = 全部未进包
    for rel in APP_FILES:
        a, b = target / rel, ENGINE / rel
        if a.is_file() and b.is_file() and a.read_bytes() == b.read_bytes():
            same.append(rel)
        else:
            diff.append(rel)
    return (not diff), same, diff


def print_app_cmp(ok: bool, same: list[str], diff: list[str], app_dir: Path,
                  elapsed_ms: float) -> int:
    """打印 cmp 结论（✅/❌）并返回退出码。build.sh 末尾与 --cmp-app 共用。"""
    if ok:
        try:
            shown = str(app_dir.relative_to(REPO))
        except ValueError:
            shown = str(app_dir)
        print(f"✅ App 包内引擎 cmp：{len(same)}/{len(APP_FILES)} 个文件逐字节相同"
              f"（`{shown}`；{elapsed_ms:.1f} ms）")
        return 0
    print(f"❌ App 包内引擎过期：{len(diff)}/{len(APP_FILES)} 个文件不同"
          f"（{elapsed_ms:.1f} ms）——包内引擎与仓库不一致，请重新 `./build.sh`："
          + "、".join(f"`{d}`" for d in diff[:8])
          + (f"…（共 {len(diff)} 个）" if len(diff) > 8 else ""))
    return 1


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
def _sha256_file(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def _corpus_tex_manifest() -> tuple[str, int]:
    """`out/corpus/**/*.tex` 的相对路径 + 内容 sha 聚合（占位符那一行的输入）。"""
    h = hashlib.sha256()
    n = 0
    if CORPUS_DIR.is_dir():
        for p in sorted(CORPUS_DIR.rglob("*.tex")):
            h.update(p.relative_to(CORPUS_DIR).as_posix().encode("utf-8"))
            h.update(b"\0")
            h.update(_sha256_file(p).encode("utf-8"))
            h.update(b"\0")
            n += 1
    return h.hexdigest(), n


def _tests_manifest() -> tuple[str, int]:
    """参与测试计数的套件文件（路径+内容 sha 聚合）。"""
    h = hashlib.sha256()
    files = test_files()
    for f in files:
        h.update(f.relative_to(ENGINE).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(_sha256_file(f).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest(), len(files)


def _manuscripts_info() -> tuple[str, str]:
    """稿件库路径 + 期目录指纹（测试套件会读真实语料；期目录增删/改名会让 SKIP 计数变化）。"""
    try:
        if str(ENGINE) not in sys.path:
            sys.path.insert(0, str(ENGINE))
        from slcn import paths as _p  # noqa: PLC0415
        root = _p.manuscripts_root()
    except Exception:  # noqa: BLE001 —— 探测失败按「本仓」口径，不影响主流程
        root = None
    base = Path(root) if root else REPO
    try:
        names = sorted(p.name for p in base.iterdir()
                       if p.is_dir() and "年第" in p.name and "期" in p.name)
        return str(base), hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()
    except OSError:
        return str(base), ""


def collect_auto_inputs() -> dict:
    """AUTO 块的输入清单（O14）：每个数据源的 sha + 环境前提 + 稿件库指纹。"""
    env_ok, missing = environment_complete()
    tex_sha, tex_n = _corpus_tex_manifest()
    tests_sha, tests_n = _tests_manifest()
    manuscripts_root, manuscripts_fp = _manuscripts_info()
    return {
        "schema": AUTO_INPUTS_SCHEMA,
        "corpus_csv_sha256": _sha256_file(CORPUS_CSV),
        "corpus_tex_manifest_sha256": tex_sha,
        "corpus_tex_files": tex_n,
        "audit_csv_sha256": _sha256_file(AUDIT_CSV),
        "audit_txt_sha256": _sha256_file(AUDIT_TXT),
        "tests_manifest_sha256": tests_sha,
        "tests_files": tests_n,
        "engine_py_sha256": (_run_record.engine_py_sha256() if _run_record is not None else ""),
        "env_complete": env_ok,
        "env_missing": missing,
        "manuscripts_root": manuscripts_root,
        "manuscripts_fingerprint": manuscripts_fp,
    }


def inputs_sha(inputs: dict) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def load_auto_inputs() -> dict | None:
    try:
        data = json.loads(AUTO_INPUTS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def cached_test_rows(inputs_now: dict, project: dict | None) -> list | None:
    """写模式复用测试计数：**只有 engine/tests/稿件库/环境前提都没变**时才允许。

    测试计数只取决于这几项（同一环境下）；corpus/audit 变了不影响计数。
    """
    if not project or project.get("schema") != AUTO_INPUTS_SCHEMA:
        return None
    old = project.get("inputs") or {}
    keys = ("engine_py_sha256", "tests_manifest_sha256", "env_complete", "manuscripts_fingerprint")
    if any(old.get(k) != inputs_now.get(k) for k in keys):
        return None
    rows = project.get("test_rows")
    return rows if isinstance(rows, list) and rows else None


def fast_check_ok(old_body: str, project: dict | None, inputs_now: dict) -> bool:
    """`--check` 快速通道：输入指纹未变 **且** README 块指纹 == 上次写出的块指纹。

    成立时可直接 PASS（不必重跑套件）；任一项对不上都回退到「现算 + 逐行比对」。
    """
    if not project or project.get("schema") != AUTO_INPUTS_SCHEMA:
        return False
    if not inputs_now.get("env_complete"):
        return False
    if inputs_sha(project.get("inputs") or {}) != inputs_sha(inputs_now):
        return False
    stored = project.get("block_sha256")
    if not stored:
        return False
    return hashlib.sha256(normalize_for_check(old_body, lenient=False).encode("utf-8")).hexdigest() == stored


def save_auto_inputs(inputs: dict, rows: list | None, block_body: str) -> None:
    """写 `out/auto-inputs.json`（AUTO 输入清单 + 测试计数缓存 + 块指纹）；失败只告警。"""
    try:
        AUTO_INPUTS.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": AUTO_INPUTS_SCHEMA,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "inputs": inputs,
            "inputs_sha256": inputs_sha(inputs),
            "test_rows": [list(r) for r in (rows or [])],
            "block_sha256": hashlib.sha256(
                normalize_for_check(block_body, lenient=False).encode("utf-8")).hexdigest(),
        }
        tmp = AUTO_INPUTS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, AUTO_INPUTS)
    except Exception as e:  # noqa: BLE001 —— 证据/缓存失败不得影响主流程
        print(f"⚠ gen_docs: 写 AUTO 输入清单失败（忽略）：{type(e).__name__}: {e}", file=sys.stderr)


def build_block(run_test_suites: bool = True,
                cached_test_rows_in: list | None = None) -> tuple[str, bool]:
    global _LAST_TEST_ROWS
    ok = True
    if cached_test_rows_in is not None:
        # O14：engine/tests 未变 → 测试计数直接复用（corpus/audit 变化不必整跑套件）
        global _CACHED_TESTS_USED
        rows = [(str(n), int(p), int(f)) for n, p, f in cached_test_rows_in]
        total = sum(p for _, p, _ in rows)
        _LAST_TEST_ROWS = rows
        _CACHED_TESTS_USED = True
        print("提示  测试计数复用 out/auto-inputs.json 缓存（engine/tests 未变；本次未跑套件）",
              file=sys.stderr)
    elif run_test_suites:
        rows, total = run_tests()
        _LAST_TEST_ROWS = rows
    else:
        # `--no-test-runs`：**不跑**套件，只保留套件清单的行数与形状（比对前测试行会被整段剔除）。
        # 用 0 而不是真值，是为了"绝不把占位数字写进 README"——写模式禁用本开关，见 main()。
        rows, total = [(f.stem, 0, 0) for f in test_files()], 0
        _LAST_TEST_ROWS = None
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
    # App 包内一致性**不在 AUTO 块里**（第四十四批③a / O9）：它依赖「本机是否刚 build 过」，
    # 放进 AUTO 会让 gen_docs 的测试计数（经 test_selfcheck → selfcheck）依赖 App 新鲜度，
    # 形成「gen_docs×2 + build×2」的循环依赖。现在由 build.sh 末尾 `--cmp-app` 守卫（见 app_cmp）。
    lines += placeholder_block() + [""]
    lines += git_block()
    if bad:
        ok = False
    return "\n".join(lines).rstrip() + "\n", ok


# ── 「App 包内一致性」那一行**已移出 AUTO**（第四十四批③a / O9）──────────────
# 历史 README 块里可能还有这一行；这里保留**删除式**归一化只为迁移兼容 ——
# 比对前把该行连同其后的空行一起删掉，于是「代码已移除该行、README 尚未用新 gen_docs
# 重生成」的窗口里 `--check` 也不会误红。重生成之后块里不再有这行，正则自然匹配不到。
APP_LINE = re.compile(r"^\*\*App 包内一致性\*\*.*(?:\n\n)?", re.M)
# 「红线只读快照」整行同理：`.git/index` 的 mtime 与 `git status` 的行数都是**本机工作区**
# 状态，同一个提交在干净克隆里与在开发机上写得不一样 → 不参与 --check。
GIT_LINE = re.compile(r"^\*\*红线只读快照\*\*.*$", re.M)
_GIT_PLACEHOLDER = "**红线只读快照**：（本机工作区状态，不参与校验）"


# 测试计数两行：**在本机前提齐全时是硬断言**（"数字不许手抄"最要紧的一条），
# 但缺前提时用例会 SKIP，计数必然变小（CI 上没有稿件库/TeX/LibreOffice 就是这样）——
# 那种情况下把它们也归一化掉，否则 CI 永远红，久而久之就没人看 CI 了。
TEST_SUMMARY_LINE = re.compile(r"^\*\*测试\*\*（.*$", re.M)
TEST_BREAKDOWN_LINE = re.compile(r"^`test_[a-z0-9_]+` \d+( ｜ `test_[a-z0-9_]+` \d+)*$", re.M)
# ⚠️ 明细的**第一行**带前缀（`套件明细：`test_…` 45 ｜ …`），不匹配上面的纯明细正则 ——
#    `strip_test_lines()` 必须单独认它，否则 --no-test-runs 会把这一行留在比对里而误红。
TEST_BREAKDOWN_HEAD = "套件明细："
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
    # APP_LINE 用**删除**（迁移兼容，见上面的注释）；GIT_LINE 仍是占位符替换。
    out = GIT_LINE.sub(_GIT_PLACEHOLDER, APP_LINE.sub("", body))
    if lenient:
        out = TEST_SUMMARY_LINE.sub(_TEST_PLACEHOLDER, out)
        out = TEST_BREAKDOWN_LINE.sub(_TEST_PLACEHOLDER, out)
    return out


def strip_test_lines(body: str) -> str:
    """把 AUTO 的「测试」两行（摘要 + 套件明细）**整段删除**（`--no-test-runs` 的比对口径）。

    为什么删除而不是像宽松模式那样替换成占位符：占位符的**条数**会随套件数变化
    （新增/删除一个 test_*.py 就同红），而本模式下这两行的语义就是"不参与比对"。
    只删行、不动其余结构 —— 两边用同一函数处理，剩下的空白行也仍然对齐。
    """
    keep = [ln for ln in body.split("\n")
            if not TEST_SUMMARY_LINE.match(ln)
            and not TEST_BREAKDOWN_LINE.match(ln)
            and not ln.startswith(TEST_BREAKDOWN_HEAD)]
    return "\n".join(keep)


def read_readme() -> str:
    return README.read_text(encoding="utf-8")


def split_block(text: str) -> tuple[str, str, str]:
    """返回 (块前, 块内, 块后)；标记行本身留在「块前」「块后」里。"""
    i = text.index(BEGIN) + len(BEGIN)
    j = text.index(END)
    return text[:i], text[i:j], text[j:]


def _main_impl(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验 README.md 的「当前状态」AUTO 块")
    ap.add_argument("--check", action="store_true", help="只校验，不一致就退出 1 并打印 diff")
    ap.add_argument("--no-test-runs", action="store_true",
                    help="（只能配 --check）不跑套件：AUTO 的「测试」计数行不参与比对。"
                         "供 full 套件 / selfcheck quick 去掉重复的一遍套件；"
                         "测试计数守卫由独立的 `gen_docs.py --check` 负责")
    ap.add_argument("--cmp-app", action="store_true",
                    help="只比对 App 包内引擎白名单（APP_FILES，28 个）与仓库；毫秒级。"
                         "build.sh 末尾用；不读写 README")
    ap.add_argument("--app-dir", default=None,
                    help="（配 --cmp-app）覆盖包内 Engine 目录，供反例测试模拟「包内过期」")
    a = ap.parse_args(argv)
    if a.no_test_runs and not a.check:
        ap.error("--no-test-runs 只能与 --check 一起用：写模式必须现算测试计数")

    if a.cmp_app:
        # O9（第四十四批③a）：包内一致性的**唯一守卫**。build.sh 末尾调用；
        # 不依赖 README/AUTO，也不跑套件 → 毫秒级，不会再把 gen_docs 拖进 build 循环。
        _app_dir = Path(a.app_dir) if a.app_dir else APP_ENGINE
        _t0 = time.perf_counter()
        _ok, _same, _diff = app_cmp(_app_dir)
        return print_app_cmp(_ok, _same, _diff, _app_dir, (time.perf_counter() - _t0) * 1000)

    text = read_readme()
    if BEGIN not in text or END not in text:
        print(f"FAIL  README.md 里找不到 {BEGIN} / {END}")
        return 1
    head, old, tail = split_block(text)

    if a.check:
        complete, missing = environment_complete()
        lenient = not complete
        # O14（第四十七批）快速通道：`out/auto-inputs.json` 的输入指纹未变 **且** README 块指纹
        # 等于上次写出的块 → 直接 PASS，**不跑套件**（严格模式下这是 ~90s/次 的节省）。
        # 任何一项对不上（输入变了/块被改过/缓存损坏）都回退到下面的「现算 + 逐行比对」。
        if not lenient:
            project = load_auto_inputs()
            if project:
                inputs_now = collect_auto_inputs()
                if fast_check_ok(old, project, inputs_now):
                    global _FAST_PATH_USED
                    _FAST_PATH_USED = True
                    print("PASS  AUTO 块与上次写入一致；输入指纹未变"
                          "（快速通道：corpus/audit/engine/tests 均未变，未跑套件）")
                    return 0
        # 现算（跑测试 + 读产物 + 只读 git）；`--no-test-runs` 只用于校验，写模式必须现跑测试
        new, all_ok = build_block(run_test_suites=not a.no_test_runs)
        new_body = "\n" + new
        # 第四十一批 O2：`--no-test-runs` 时把「测试」两行整段剔除后再比（不跑套件）。
        # 这两行的守卫交给**独立**的 `gen_docs.py --check`（CI ci.yml 独立步骤 /
        # 发版前 `selfcheck.py --full` 严格模式 / 手工显式跑）——分工见 test_docs_consistency.py。
        old_for_cmp, new_for_cmp = old, new_body
        if a.no_test_runs:
            old_for_cmp = strip_test_lines(old_for_cmp)
            new_for_cmp = strip_test_lines(new_for_cmp)
        old_cmp = normalize_for_check(old_for_cmp, lenient)
        new_cmp = normalize_for_check(new_for_cmp, lenient)
        if lenient:
            print(f"⚠️  宽松校验（缺 {'、'.join(missing)}）：测试计数与依赖稿件库的数字**不参与比对**；"
                  "本机要拿到严格校验，请让稿件库可发现（设 SLCN_MANUSCRIPTS 或把两仓并排放）")
        if old_cmp == new_cmp:
            if a.no_test_runs:
                print("PASS  AUTO 块非测试行与现算结果一致（--no-test-runs：本次未跑套件，"
                      "测试计数行不参与比对）")
            elif old != new_body:
                print("PASS  AUTO 块与现算结果一致（差异只在本机状态那几行——"
                      "按设计不参与校验）")
            else:
                print("PASS  AUTO 块与现算结果逐字一致")
            # ⚠️ `--check` 只回答"块过期了吗"。`all_ok` 里还混着块内其它 ❌（普查三态交叉核对）；
            #    App 包内一致性已从 AUTO 移出（build.sh 末尾 cmp 守卫），不再影响本退出码。
            if not all_ok:
                print("提示  块文本本身一致；块内另有 ❌ 项（多为普查三态交叉核对）—— "
                      "见块内标记，不影响 --check 的结论")
            return 0
        print("FAIL  AUTO 块已过期（README 里的是旧数字）—— 跑 `tools/gen_docs.py` 更新")
        # ⚠️ diff 要打**归一化后**的文本：否则会把"按设计不参与校验"的行也列出来，
        #    真正的问题反而被淹没（2026-10-16 在 CI 上就是被这条误导了一轮）。
        for line in list(difflib.unified_diff(
                old_cmp.split("\n"), new_cmp.split("\n"),
                "README/AUTO（现状）", "现算结果", lineterm="", n=1))[:60]:
            print("      " + line)
        return 1

    # ── 写模式（O14：engine/tests/稿件库/环境都没变时，**复用测试计数缓存**）──
    inputs_now = collect_auto_inputs()
    cached = cached_test_rows(inputs_now, load_auto_inputs())
    new, all_ok = build_block(run_test_suites=True, cached_test_rows_in=cached)
    # ⚠️ 块里的「红线只读快照」记着 `git status --porcelain` 的行数，而**写 README 本身
    #    就会让这个数字变**（README 从「干净」变成「已修改」）—— 自指，跑一遍不收敛。
    #    第四十一批 O1：**不再重算整块**（那会再跑一整遍套件；干净树过期时共 3 遍 ≈180s）。
    #    只就地替换那一行（两条只读 git 命令），测试/普查/corpus/App 各块只算一次。
    for _ in range(3):
        probe = "\n" + new
        stable = head + probe + tail
        if stable == read_readme():
            break
        README.write_text(stable, encoding="utf-8")
        git_line = git_block()[0]        # 只刷新本机状态行（index mtime / HEAD / status 行数）
        new = GIT_LINE.sub(lambda _m: git_line, new)
    new_body = "\n" + new

    if old == new_body:
        print("PASS  AUTO 块已是最新，未改动 README.md")
    else:
        README.write_text(head + new_body + tail, encoding="utf-8")
        after_head, after, after_tail = split_block(read_readme())
        assert (head, tail) == (after_head, after_tail), "块外内容被改动了 —— 这是 bug"
        print("PASS  已就地更新 AUTO 块（块外内容逐字节未动）")
    # O14：写模式输出 AUTO 输入清单（corpus/audit/engine/tests 的 sha + 测试计数缓存 + 块指纹）
    save_auto_inputs(inputs_now, _LAST_TEST_ROWS, new_body)
    try:
        shown = AUTO_INPUTS.relative_to(REPO)
    except ValueError:
        shown = AUTO_INPUTS
    print(f"      AUTO 输入清单：{shown}（inputs sha {inputs_sha(inputs_now)[:12]}；"
          f"测试计数{'复用缓存' if cached else '现跑'}）")
    print(f"      退出码提示：{'各项检查通过' if all_ok else '有 ❌ 项，见块内'}")
    return 0 if all_ok else 1


def _record_run(*, label: str, rc: int, started: float, args: list[str],
                check_mode: bool, no_test_runs: bool, readme_changed: bool,
                engine_sha: str | None = None) -> None:
    """O13：尽力写一份 manifest；任何异常只告警，**绝不改变退出码**。"""
    if _run_record is None or os.environ.get("SLCN_RUN_RECORD") == "0":
        return
    try:
        _run_record.write_manifest(
            label=label,
            purpose="gen-docs",
            command=f"{Path(PY).name} tools/gen_docs.py" + (" " + " ".join(args) if args else ""),
            started_at=started,
            artifact=README,
            exit_code=rc,
            engine_sha=engine_sha,
            meta={
                "mode": label.rsplit("-", 1)[-1],       # write / check
                "check": check_mode,
                "no_test_runs": no_test_runs,
                # O14：快速通道 / 缓存命中时**没有跑套件**，manifest 如实记；
                # 未跑套件的 `--check` 仍是有意义的 PASS（输入冻结的等价性由 test_docs_consistency 覆盖）。
                "tests_ran": (not no_test_runs and not _FAST_PATH_USED and not _CACHED_TESTS_USED),
                "fast_path": _FAST_PATH_USED,
                "cached_tests": _CACHED_TESTS_USED,
                "readme_changed": readme_changed,
            },
        )
    except Exception as e:  # noqa: BLE001 —— 证据记录失败不影响主流程
        print(f"⚠ gen_docs: manifest 记录失败（忽略）：{type(e).__name__}: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """入口：记录一次运行（O13）后转 `_main_impl`；计算/输出/退出码原样不变。"""
    t0 = time.time()
    args = list(sys.argv[1:] if argv is None else argv)
    check_mode = "--check" in args
    no_test_runs = "--no-test-runs" in args
    cmp_app = "--cmp-app" in args
    # 指纹在**开始时**算好：并发写者窗口里「结束态」可能已不是本次跑的那份代码。
    engine_sha = None
    if _run_record is not None and not cmp_app:
        try:
            engine_sha = _run_record.engine_py_sha256()
        except Exception:  # noqa: BLE001
            engine_sha = None
    try:
        before = README.read_bytes() if README.is_file() else b""
    except OSError:
        before = b""
    label = "gen_docs-cmp-app" if cmp_app else ("gen_docs-check" if check_mode else "gen_docs-write")
    rc = 1
    try:
        rc = _main_impl(argv)
        return rc
    finally:
        changed = False
        try:
            after = README.read_bytes() if README.is_file() else b""
            changed = after != before
        except OSError:
            pass
        # cmp-app 是 build.sh 的内部调用（build 自己有 manifest），不重复记录
        if not cmp_app:
            _record_run(label=label, rc=rc, started=t0, args=args, check_mode=check_mode,
                        no_test_runs=no_test_runs, readme_changed=changed, engine_sha=engine_sha)


if __name__ == "__main__":
    sys.exit(main())
