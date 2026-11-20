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
    /opt/homebrew/bin/python3.14 tools/gen_docs.py --check --strict-tests
                                                              # 严格入口：前提不全 → BLOCKED + 非零；
                                                              # 真跑套件，任何套件状态异常都判不通过
    /opt/homebrew/bin/python3.14 tools/gen_docs.py --cmp-app   # 只比对 App 包内引擎白名单（毫秒级）

**G1（门禁可信化）：套件状态必须可信，`--check` 区分「文档新鲜度」与「套件通过」**
* `run_tests()` 从前**只数 `PASS`/`FAIL` 文本行、把子进程的 returncode 丢掉了** —— rc=3 崩在
  import、5 个 PASS 之后 exit 7、甚至超时，全都会被渲染成「FAIL 0 行 ✅」。现在每个套件带
  `status`：`None` 才表示 `rc==0 且无 FAIL 行`；否则是含原始 rc 的可读串
  （`rc=7` / `startup failed rc=1` / `timeout 300s` / `rc=0 FAIL 2`），并且 `subprocess.run`
  一律带 `timeout=`、`TimeoutExpired` 算**显式失败**。
* `build_block` 的 OK 判定与渲染都改成 status 感知：全绿时测试行**逐字节不变**（AUTO 块跨机器
  可复现），出现异常套件时在摘要行追加 `❌ …（异常套件 k / n）`、并在「套件明细」后新增
  `异常套件：…` 一行逐套件标状态。
* `out/auto-inputs.json` 的 schema 升到 3：旧缓存版本一律不复用。缓存还记录测试运行时指纹；
  稿件库存在但没有 `SLCN_TEST_DATASET_ID` 时，持久套件缓存拒绝复用。
* 严格校验优先验证本轮 full runner manifest（identity、全套件覆盖及成功的文档自检）；
  manifest 缺失或无效时实跑 full suites，不使用持久快速缓存替代本轮证据。
* `--check`（非严格）仍以**新鲜度**为主；但**本次真跑了套件**且存在异常套件时，必须打印明确的
  非 PASS 行并以非零退出码结束（不再出现"文档一致所以套件崩了也算过"）。
* 新增 `--strict-tests`：前提不全（缺稿件库/TeX/LibreOffice）→ `BLOCKED` + 原因清单 + 退出码 2；
  不降级（不伪造 `environment_complete=True`、不归一化测试行）、不减少套件数。
  `--no-test-runs` 与它互斥（前者根本不跑套件，无权声称任何套件状态）。

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

**批74 / B-110（写模式「一次写绿」，终结 RB-08 第 3 次复发的二写）：**
写模式的顺序改成「**先数据、后测试**」：先现算并落盘**非测试数据块**（普查/corpus/占位符/红线行；
「测试」段沿用 README 里现有的行，**不写占位数字**），再跑套件、用现算计数补写最终块。
原因是套件里的 `test_docs_consistency.py` / `test_selfcheck.py` 会调用本脚本的
`--check --no-test-runs`：从前写模式先跑套件，那时 README 数据块还是旧值 → 它们记 FAIL，
AUTO 写出「FAIL 1」瞬态，必须再写一次（或删 `out/auto-inputs.json` 缓存）才绿。
一次调用内部允许**两次落盘**（数据块先行 + 测试计数补写），对外仍是「写 1 次」；
`out/auto-inputs.json` 里带 FAIL 的测试计数缓存**不再复用**（避免瞬态 FAIL 被缓存粘住）。
AUTO 数字口径不变：块里每个数字仍然全部现算。

**批77 / B-136（AUTO 普查 sha 单源）：** AUTO 的「普查」数字唯一来源是**笔记仓冻结基线**
`baselines/docx_audit.csv`（本机可发现笔记仓时；行内注明来源 + 成功/失败数），**不再**读
gitignored 的 `Engine/out` 缓存 —— 从前 README 公开显示的 sha 是"某台机器的 out 缓存值"
（批73–76 一直是 b70 的 `76ff3ba8…`），与冻结基线不同源。**无笔记仓的环境（CI / 新克隆）
信息性降级**：不展示会误导的 sha；本机有 `out` 缓存时只报成功/失败数并标 fallback。
`--check` 在无笔记仓环境把「普查」整段归一化（环境差异不误红），**有**笔记仓时仍严格比对
（基线 sha 漂移 → rc1；反例见 `tests/test_b77_b136_auto_single_source.py`）；
`out/auto-inputs.json` 增 `notes_audit_*_sha256` 参与快速通道失效判定。

约定
----
* **块内不放时间戳**：否则 `--check` 永远失败。新鲜度看块内各数据源的 sha256。
* 铁律：**只读**。除了给 `README.md` 的 AUTO 块换内容 + 写 `out/gen_docs-state.txt`
  （本机状态快照，gitignored、**不进提交**；B-159），本脚本不写任何文件，
  不跑 `docx_audit.py`、不跑 `corpus.py`（那些会重生基线产物），不跑任何写操作的 git 命令。
* **B-159（批82）**：AUTO 的「红线只读快照」行只写**确定性文本**（`_GIT_LINE`），不再把本机
  `.git/index` mtime / `HEAD` / `git status` 行数写进**被提交的** README —— 该行在 `--check`
  里本来就被整行归一化（`GIT_LINE`→`_GIT_PLACEHOLDER`）、从不参与判定，写入侧却保证每次
  写入 1 行 diff。现况仍**只读**采集，落 `out/gen_docs-state.txt`；clean 树上连跑两次写模式
  **0 行 diff**（测试 `tests/test_b159_gen_docs_state_line.py`）。
* 测试用 `/opt/homebrew/bin/python3.14` + `PYTHONPATH=<Engine>/vendor`（绝对路径），
  与人工跑法逐字一致；有 FAIL 行时**照实写进块里**并让退出码非 0。
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# B300-06：**不要**硬编码 mac 的解释器路径（Windows 上直接 FileNotFoundError [WinError 2]，
# README 的 AUTO 块从此再也生不出来）。优先级：
#   ① 环境变量 `SLCN_PYTHON`（要**本机原生**路径；CI/临时切解释器时用）
#   ② 当前解释器 `sys.executable`（要求 ≥3.14：套件与引擎都要求 3.14）
#   ③ 原 mac 路径兜底（从更老的 python 启动时行为保持原样，方便发现而不是静默跑错）
# mac 上照旧用 /opt/homebrew/bin/python3.14 跑时 ①→③ 都不生效、② 给出同一个路径，
# 因此 Path(PY).stem 与套件子进程完全不变 —— AUTO 块**逐字节不变**。
def _default_python() -> str:
    env = os.environ.get("SLCN_PYTHON", "").strip()
    if env:
        return env
    exe = sys.executable
    if exe and sys.version_info >= (3, 14):
        return exe
    return "/opt/homebrew/bin/python3.14"


PY = _default_python()
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
# ⚠️ B-136（批77）：「普查」的唯一来源是**笔记仓冻结基线**（见下方 NOTES_BASELINE_*）。
# 这两个 `Engine/out/` 路径是 gitignored 的**本机缓存**：只作为无笔记仓环境下的
# fallback 数据（信息性成功/失败数）与 auto-inputs 的环境前提，**不再**决定 AUTO 展示的 sha。
AUDIT_CSV = ENGINE / "out" / "docx_audit.csv"
AUDIT_TXT = ENGINE / "out" / "docx_audit.txt"
# 笔记仓内的冻结基线（相对笔记仓根）；发现逻辑复用 `slcn/paths.py` 的 `notes_root()`。
NOTES_BASELINE_CSV = Path("baselines") / "docx_audit.csv"
NOTES_BASELINE_TXT = Path("baselines") / "docx_audit.txt"
CORPUS_CSV = ENGINE / "out" / "corpus" / "corpus.csv"
CORPUS_DIR = ENGINE / "out" / "corpus"

# ── O14（第四十七批）：AUTO 输入冻结 `out/auto-inputs.json` ──────────────────
# 写模式把「AUTO 块依赖的输入」的 sha（corpus/audit/engine py/tests）+ 测试计数缓存
# + 写出的块指纹落盘；`--check` 据此走**快速通道**（输入未变 → 不跑套件直接 PASS），
# 写模式在 engine/tests 未变时**复用测试计数**（corpus/audit 变了也不再整跑套件）。
# 背景：批46 O5=0 的「AUTO-corpus 循环」——重跑 corpus → AUTO corpus sha 变 → 再 gen_docs+build。
# `Engine/out/` 是 gitignore 的产物目录；测试可用 SLCN_AUTO_INPUTS 指向临时文件。
AUTO_INPUTS = Path(os.environ.get("SLCN_AUTO_INPUTS") or (ENGINE / "out" / "auto-inputs.json"))
# G1：schema 3 包含套件状态和测试运行时指纹；旧 schema 缓存一律不复用。
# schema 不匹配 → 缓存与快速通道一律拒用。
AUTO_INPUTS_SCHEMA = 3
TEST_DEPENDENCIES = ("lxml", "pypdf", "pypinyin")
_LAST_TEST_ROWS: list[tuple[str, int, int, str | None]] | None = None
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


def runner_test_files() -> list[Path]:
    """full runner 清单：与 shell runner 一样包含自指的文档自检套件。"""
    return [f for f in sorted((ENGINE / "tests").glob("test_*.py")) if f.is_file()]


def _runner_python() -> str:
    """按 `run_engine_tests.sh` 的解释器优先级解析测试 Python。"""
    explicit = os.environ.get("SLCN_TEST_PYTHON", "").strip()
    if explicit:
        return shutil.which(explicit) or explicit
    mac_python = "/opt/homebrew/bin/python3.14"
    if os.path.isfile(mac_python) and os.access(mac_python, os.X_OK):
        return mac_python
    return shutil.which("python3") or shutil.which("python") or sys.executable


def _resolve_executable(command: str | None) -> str | None:
    if not command:
        return None
    found = shutil.which(command)
    candidate = found or command
    try:
        return str(Path(candidate).resolve())
    except OSError:
        return candidate


_FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".otc", ".dfont", ".pfb", ".pfa"}


def font_inventory_fingerprint(roots: list[Path] | None = None) -> dict:
    """Fingerprint installed font paths and metadata without reading font contents."""
    source = "fixture-directories"
    files: list[Path] = []
    if roots is None:
        fc_list = shutil.which("fc-list")
        if fc_list:
            try:
                p = subprocess.run([fc_list, "--format=%{file}\\n"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=8)
                if p.returncode == 0:
                    files = [Path(line.strip()) for line in p.stdout.splitlines() if line.strip()]
                    source = "fontconfig"
            except (OSError, subprocess.TimeoutExpired):
                pass
        if not files:
            home = Path.home()
            if os.name == "nt":
                roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                         Path(os.environ.get("LOCALAPPDATA", str(home)))
                         / "Microsoft" / "Windows" / "Fonts"]
            else:
                roots = [Path("/System/Library/Fonts"), Path("/Library/Fonts"),
                         home / "Library" / "Fonts", Path("/usr/share/fonts"),
                         Path("/usr/local/share/fonts"), home / ".local" / "share" / "fonts",
                         home / ".fonts"]
            source = "standard-font-directories"
    if roots is not None:
        for root in roots:
            if not root.is_dir():
                continue
            try:
                files.extend(p for p in root.rglob("*")
                             if p.is_file() and p.suffix.lower() in _FONT_EXTENSIONS)
            except OSError:
                continue

    records = []
    for path in sorted(set(files), key=lambda item: str(item).casefold()):
        if path.suffix.lower() not in _FONT_EXTENSIONS:
            continue
        try:
            resolved = path.resolve()
            stat = resolved.stat()
        except OSError:
            continue
        records.append((str(resolved), stat.st_size, stat.st_mtime_ns))
    digest = hashlib.sha256(json.dumps(records, ensure_ascii=False, separators=(",", ":"))
                            .encode("utf-8")).hexdigest()
    return {"source": source, "files": len(records), "sha256": digest}


def _version_probe(command: list[str]) -> str | None:
    try:
        p = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = p.stdout or p.stderr
    return next((line.strip() for line in text.splitlines() if line.strip()), None) if p.returncode == 0 else None


def test_runtime_fingerprint() -> dict:
    """测试运行时身份：Python/依赖与 TeX、LibreOffice 可执行文件及版本。"""
    python = _runner_python()
    python_identity = _resolve_executable(python) or python
    dep_names = json.dumps(TEST_DEPENDENCIES)
    probe = (
        "import importlib.metadata as m,json,sys;"
        f"names={dep_names};"
        "installed={d.metadata.get('Name','').lower().replace('_','-'):d.version "
        "for d in m.distributions() if d.metadata.get('Name')};"
        "print(json.dumps({'version':sys.version,'implementation':sys.implementation.name,"
        "'version_info':list(sys.version_info[:3]),"
        "'dependencies':{n:installed.get(n) for n in names}},sort_keys=True))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE / "vendor")
    try:
        p = subprocess.run([python, "-c", probe], cwd=str(ENGINE), env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=8)
        py_data = json.loads(p.stdout) if p.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        py_data = {}

    xelatex = _resolve_executable(shutil.which("xelatex"))
    try:
        if str(ENGINE) not in sys.path:
            sys.path.insert(0, str(ENGINE))
        from slcn.platform import find_soffice, soffice_cli_for  # noqa: PLC0415
        soffice = _resolve_executable(soffice_cli_for(find_soffice()))
    except Exception:  # noqa: BLE001 - strict preflight will report unavailable tool identity
        soffice = None

    return {
        "python": {
            "identity": python_identity,
            "version": py_data.get("version"),
            "version_info": py_data.get("version_info"),
            "implementation": py_data.get("implementation"),
            "dependencies": py_data.get("dependencies", {name: None for name in TEST_DEPENDENCIES}),
        },
        "xelatex": {
            "identity": xelatex or "",
            "version": _version_probe([xelatex, "--version"]) if xelatex else None,
        },
        "libreoffice": {
            "identity": soffice or "",
            "version": _version_probe([soffice, "--version"]) if soffice else None,
        },
        "fonts": font_inventory_fingerprint(),
    }


def strict_runtime_missing(runtime: dict) -> list[str]:
    """严格校验启动长套件前的快速环境检查。"""
    missing: list[str] = []
    py = runtime.get("python") if isinstance(runtime, dict) else None
    if not isinstance(py, dict) or not py.get("version"):
        missing.append("测试 Python 无法启动或读取版本")
    version_info = py.get("version_info") if isinstance(py, dict) else None
    if (not isinstance(version_info, list) or len(version_info) < 2
            or not all(type(part) is int and part >= 0 for part in version_info[:2])
            or tuple(version_info[:2]) < (3, 14)):
        missing.append("测试 Python 需要 ≥3.14")
    deps = py.get("dependencies", {}) if isinstance(py, dict) else {}
    for name in TEST_DEPENDENCIES:
        if not isinstance(deps, dict) or not deps.get(name):
            missing.append(f"Python 依赖 {name}")
    for key, label in (("xelatex", "xelatex 版本探测"), ("libreoffice", "LibreOffice 版本探测")):
        item = runtime.get(key) if isinstance(runtime, dict) else None
        if not isinstance(item, dict) or not item.get("identity") or not item.get("version"):
            missing.append(label)
    fonts = runtime.get("fonts") if isinstance(runtime, dict) else None
    if (not isinstance(fonts, dict) or type(fonts.get("files")) is not int
            or fonts["files"] < 1 or not fonts.get("sha256")):
        missing.append("字体清单不可用")
    return missing


# ── G1：套件状态可信化 ────────────────────────────────────────────────────
# 从前这里只数 `^\s*PASS` / `^\s*FAIL` 文本行，**把子进程的 returncode 丢了**。后果实测
# （2026-11-15 的 6 个假套件反例）：`import` 阶段崩掉（rc=3）→ 0 PASS / 0 FAIL → 渲染成
# "FAIL 0 行 ✅"；5 个 PASS 之后 `sys.exit(7)` → 5 PASS / 0 FAIL → ✅；甚至一个什么都不做的
# 套件（rc=0，0 PASS）也被当成功。这套计数是 AUTO 块「测试」行的唯一来源，一旦它能把崩溃
# 说成全绿，那块绿就一文不值。
#
# 现在每个套件带一个 `status`：**`None` 才表示"可信的全绿"**（rc==0 且 0 个 FAIL 行）；
# 否则是给人看的失败描述，一律带原始 rc：
#   * `startup failed rc=1`  —— 还没打出任何断言行就非零退出（import/收集期崩溃）
#   * `rc=7`                 —— 打出过 PASS 行，但进程非零退出
#   * `rc=0 FAIL 2`          —— 进程正常但打出了 FAIL 行
#   * `timeout 300s`         —— TimeoutExpired（**显式失败**，不是"没看见就当过"）
# 成功判据收紧成一个：`rc == 0 and fails == 0`。
TEST_TIMEOUT_SECONDS = 900


def _suite_status(rc: int | None, passes: int, fails: int, timed_out: bool,
                  timeout: float) -> str | None:
    """把一次子进程运行归成 status：None = 可信全绿，否则是可读的失败串。"""
    if timed_out:
        return f"timeout {timeout:g}s"
    if rc is None:
        return "no-exit-code"
    if fails:
        return f"rc={rc} FAIL {fails}"
    if rc != 0:
        # 一个 PASS 行都没打出来就非零退出：几乎总是 import/收集期就崩了 —— 单列出来，
        # 免得和"跑完了但有断言失败"混在一起看不出病根。
        return f"startup failed rc={rc}" if passes == 0 else f"rc={rc}"
    return None


def run_tests(timeout: float | None = None,
              files: list[Path] | None = None) -> tuple[list[tuple[str, int, int, str | None]], int]:
    """现跑套件，返回 `([(套件, PASS 数, FAIL 数, status)], PASS 总数)`。

    `status` 语义见 `_suite_status()`：**只有 None 算全绿**。
    `timeout` / `files` 只给测试用（生产语义固定：默认超时 + 全量 `test_files()`）。
    """
    if os.environ.get("SLCN_GEN_DOCS_NESTED"):
        sys.exit("gen_docs.py 被递归调用（SLCN_GEN_DOCS_NESTED 已置位）—— 检查测试清单")
    if timeout is None:
        timeout = TEST_TIMEOUT_SECONDS
    env = dict(os.environ, PYTHONPATH=str(ENGINE / "vendor"), SLCN_GEN_DOCS_NESTED="1")
    rows: list[tuple[str, int, int, str | None]] = []
    for f in (files if files is not None else test_files()):
        timed_out = False
        rc: int | None = None
        out = ""
        try:
            p = subprocess.run([PY, "-u", str(f)], cwd=str(ENGINE), env=env,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
            out = p.stdout + p.stderr
            rc = p.returncode
        except subprocess.TimeoutExpired as e:
            # 超时是**显式失败**：把已经吐出来的部分照样计数（便于看它卡在哪一项），
            # 但 status 必须是 timeout —— 不许因为"没看到 FAIL 行"就当过。
            timed_out = True
            out = "".join(x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
                          for x in (e.stdout, e.stderr))
        except OSError as e:
            print(f"⚠ gen_docs: 套件 {f.name} 启动失败：{type(e).__name__}: {e}", file=sys.stderr)
        passes = len(re.findall(r"^\s*PASS\b", out, re.M))
        fails = len(re.findall(r"^\s*FAIL\b", out, re.M))
        rows.append((f.stem, passes, fails, _suite_status(rc, passes, fails, timed_out, timeout)))
    return rows, sum(r[1] for r in rows)


def abnormal_suites(rows: list | None) -> list[tuple[str, str]]:
    """挑出 status 非 None 的套件（兼容 3 元素旧行：缺 status 视为 None）。

    卡 §9 第 2 项（qoder §22.2.2 / Codex lead §23 的 fail-return）：
    **status 的判据是 `is not None`，不是 truthiness**。空串 / `False` / `0` 都是
    「有人给了一个状态」而不是「没有状态」—— 按 truthiness 判会把它们当成全绿，
    从而让一份带空串状态的缓存放行、并被 `_norm_test_rows()` 洗成 `None` 落盘。
    """
    out: list[tuple[str, str]] = []
    for r in rows or []:
        status = r[3] if len(r) > 3 else None
        if status is not None:
            # 空串/False/0 也要看得到"它给了个东西但不可信"（不要渲染成空白）。
            out.append((str(r[0]), str(status) or f"status={status!r}"))
    return out


def suite_rows_trusted(rows: list | None, expected: list[str] | None = None) -> bool:
    """这批测试行能不能用来声称「套件通过」：非空、每行 ≥4 列、且 status 全 None。

    `expected`（G1-F5）：**证据覆盖面**校验。给了期望套件名单时，这批行必须**恰好**覆盖
    当前 `test_files()` 的每一个套件（多一行、少一行、换一个名字都算不成立）。
    没有它的话，一份被截断的缓存（比如 174 个套件只留 1 行、单套断言数被改成 99999）
    也能冒充"全部套件都绿过"的直接证据 —— 缓存只能证明"曾经写过这份计数"，
    不能证明"当前这套套件跑过且全绿"。`expected=None` 时保持历史语义（只看形状与 status）。

    ⚠️ 卡 §9 第 2 项：status 的可信判据是**严格 `is None`**，不是 falsy。空串 / `False` / `0`
    都是"给了状态"却不等于可信全绿；按 truthiness 判会让它们混过去（qoder §22.2.2 实测）。
    """
    if not isinstance(rows, list) or not rows:
        return False
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 4:
            return False
        try:
            int(r[1]), int(r[2])
        except (TypeError, ValueError):
            return False
        if r[3] is not None:
            return False
    if expected is not None:
        want = [str(x) for x in expected]
        have = [str(r[0]) for r in rows]
        if len(have) != len(want) or set(have) != set(want):
            return False
    return True


def render_test_section(rows: list[tuple[str, int, int, str | None]],
                        total: int | None = None) -> list[str]:
    """把测试行渲染成 AUTO 的「测试」段（摘要行 + 空行 + 明细 + 可选异常明细 + 空行）。

    G1-F5：单独抽出来是为了让**缓存自证**成为可能 —— 严格档走快速通道前，要拿缓存里的
    计数重新渲染一遍，与 README 里已经写着的测试段逐行核对（见 `cached_rows_match_block`）。
    渲染逻辑只有这一份，`build_block()` 与该校验共用，避免两处走样。
    """
    if total is None:
        total = sum(p for _, p, _, _st in rows)
    abnormal = abnormal_suites(rows)
    # ⚠️ 这一行**不许出现绝对路径**：块是提交进仓库的，换台机器/换个克隆目录就会
    #    `--check` 失败（2026-10-15 在一个新克隆里实测到）。只写解释器名与相对路径。
    # B300-06：用 `.stem` 而不是 `.name` —— 否则 Windows 上会写 `python3.14.exe`，
    # 同一份 README 在 mac 上 `--check` 就红（AUTO 块跨平台必须一致）。
    t = (f"**测试**（`{Path(PY).stem}` + `PYTHONPATH=Engine/vendor`）："
         f"**{len(rows)} 套件 / {total} 项断言 / FAIL {sum(f for _, _, f, _st in rows)} 行**")
    # 三种事实分开表达：PASS 文本计数（上面那行）、FAIL 断言行数（bad）、套件是否成功（abnormal）。
    # 同一个套件既非零退出又打了 FAIL 时只报状态（状态串里已含 FAIL 数）。
    # ⚠️ 卡 §9 第 2 项：`st is not None` 才是"有状态"，空串/False/0 也算有状态（渲染成 `status=…`），
    #    不许被 truthiness 判成"全绿"而渲染出 `✅`。
    marks = [f"{n}({st if str(st) else f'status={st!r}'})" if st is not None else f"{n}({f} FAIL)"
             for n, p, f, st in rows if st is not None or f]
    if not marks:
        # 全绿：**逐字节**保持历史渲染（AUTO 块跨机器可复现，B-136/O14 的守门断言都靠它）。
        t += " ✅"
    else:
        t += " ❌ " + "、".join(marks)
        if abnormal:
            # 异常标记里必须带套件数：`❌ 1/2 套件异常` 一眼能看出规模，不必去数明细。
            t += f"（异常套件 {len(abnormal)}/{len(rows)}，明细见下）"
    t += "（不含 `test_docs_consistency.py` 自身：它调用本脚本，属自指）"
    lines = [t, ""]
    parts = [f"`{n}` {p}" for n, p, _f, _st in rows]
    lines.append("套件明细：" + " ｜ ".join(parts[:4]))
    for i in range(4, len(parts), 4):
        lines.append(" ｜ ".join(parts[i:i + 4]))
    if abnormal:
        # 明细里逐条标出状态异常的套件（全绿时不出现这一行 → 渲染逐字不变）。
        lines.append(TEST_ABNORMAL_HEAD
                     + " ｜ ".join(f"`{n}` {st}" for n, st in abnormal))
    lines.append("")
    return lines


def cached_rows_match_block(old_body: str, rows: list | None) -> bool:
    """缓存里的测试计数，是否**与 README 里已经写着的测试段逐行一致**。

    G1-F5b：`suite_rows_trusted()` 只能证明「这批行覆盖了当前全部套件、且没有失败证据」，
    它**看不懂数字对不对** —— 一份把某个套件的断言数从 45 改成 99999 的缓存，套件名
    集合仍然完全匹配。缓存是"自己说自己对"，所以严格档还要拿它重渲染一遍，与提交进仓库的
    README 里那段（上次写模式落下的）逐行对比：对不上就是缓存与块不是同一次生成的，
    这时不许拿缓存当"套件通过"的证据，必须回退到现算。
    """
    if not rows:
        return False
    try:
        got = test_section_lines(old_body)
        if got is None:
            return False
        want = render_test_section(_norm_test_rows(rows))
    except Exception:  # noqa: BLE001 —— 证据比对失败一律按"不可信"处理，绝不放行
        return False
    return got == want


def _norm_test_rows(rows_in: list) -> list[tuple[str, int, int, str | None]]:
    """把 3 或 4 元素的测试行**规范成 4 元素**：`(套件, PASS, FAIL, status)`。

    为什么要容 3 元素：G1 之前写出的 `out/auto-inputs.json` 与若干测试里的
    `[["test_x", 1, 0]]` 都是三列（没有套件状态）。缺第 4 列时 status 记 None ——
    即「这三列本身不含失败证据」；能不能真复用另由 `cached_test_rows()` 判
    （schema 与 status 两道闸在那里，不在渲染层）。

    ⚠️ 本函数只管**形状**，不管「能不能当通过证据」：写模式走的是 `reusable_test_rows()`
    （卡 §7 第 1 项，在 `cached_test_rows()` 之上再要求 4 列 + 覆盖面 + 与块逐行一致），
    三列行在那里一律被拒。渲染层保留三列兼容只为历史块与测试替身。
    """
    out: list[tuple[str, int, int, str | None]] = []
    for r in rows_in:
        n, p, f = r[0], r[1], r[2]
        st = r[3] if len(r) > 3 else None
        # ⚠️ 卡 §9 第 2 项：只有 None 才是"没有状态"。空串 / False / 0 一律**原样保留**
        # （`str(st)`），不许被 truthiness 洗成 None —— 那等于替一份坏缓存伪造"全绿"证据。
        out.append((str(n), int(p), int(f), None if st is None else str(st)))
    return out


# ── 普查：读 out/docx_audit.csv（**只读**，绝不重生成基线）──────────────────
# ── 本仓有没有稿件库？（拆仓后工具仓没有；稿件仓有）─────────────────────
# 普查与 corpus 的数字**来自作者稿件**，那些数据（连文件名带作者名）不该出现在公开仓里，
# 所以公开仓里这两行必然没有数据。这不等于「工具坏了」——必须与「跑过但对不上」分开报，
# 否则新克隆一开就 `--check` 失败（2026-10-15 拆仓预演实测）。
def has_manuscripts() -> bool:
    r"""本机有没有稿件库：探测逻辑唯一来源 = `slcn/paths.py:manuscripts_root()`。

    B-304/D-18：旧实现只扫**公开仓根**有没有 `20xx年第N期` 目录 —— Windows 上稿件仓
    并排放在仓外（`D:\GitHub\Studies_in_Logic_Database`）就误判「本仓不含稿件库」，
    输出降级文本 → 与 README 的 corpus/占位符数字不一致 → `--check` 误红（非真实回归）。
    paths 的探测顺序（SLCN_MANUSCRIPTS → 老布局上级 → 两仓并排）与 mac 既有行为一致。
    """
    try:
        return _slcn_paths().manuscripts_root() is not None
    except Exception:  # noqa: BLE001 —— 探测失败按「没有稿件库」降级（与旧实现同语义）
        return False


# ── B-136（批77）：普查数字的**唯一来源 = 笔记仓冻结基线** ────────────────────
# 从前读 `Engine/out/docx_audit.csv`（gitignored 的本机缓存）→ 公开 README 上的 sha
# 其实是"某台机器的 out 缓存值"（批73–76 一直是 b70 的 76ff3ba8…），与笔记仓冻结基线
# 不同源、且陈旧。现在：本机可发现笔记仓 → 只读 `baselines/docx_audit.csv`（并注明来源）；
# 无笔记仓（CI / 新克隆）→ 信息性降级，不展示会误导的 sha（环境差异由 normalize_for_check
# 归一化，`--check` 不因此变红）。发现逻辑复用 `slcn/paths.py` 的 `notes_root()`。
def _slcn_paths():
    """`from slcn import paths`：必须先把 `Engine/` 挂进 sys.path（同 environment_complete 的教训）。"""
    _engine_dir = str(Path(__file__).resolve().parents[1])
    if _engine_dir not in sys.path:
        sys.path.insert(0, _engine_dir)
    from slcn import paths as _p  # noqa: PLC0415
    return _p


def notes_audit_paths() -> tuple[Path, Path] | None:
    """笔记仓冻结基线 (csv, txt)；本机没有笔记仓 / 没有基线文件 → None。

    `None` 是**环境差异**而不是错误：CI / 新克隆没有私有笔记仓，`--check` 会把「普查」
    整段归一化（见 `normalize_for_check`），绝不因此变红。
    """
    try:
        root = _slcn_paths().notes_root()
    except Exception:  # noqa: BLE001 —— 探测失败按「无笔记仓」降级，不影响主流程
        return None
    if not root:
        return None
    base = Path(root) / NOTES_BASELINE_CSV.parent
    csv_path = base / NOTES_BASELINE_CSV.name
    if not csv_path.is_file():
        return None
    return csv_path, base / NOTES_BASELINE_TXT.name


def _audit_success_fail(txt_path: Path) -> tuple[int, int] | None:
    """从普查 txt 报告的抬头读「成功解析 N 份、解析失败 M 份」；读不到返回 None。"""
    try:
        text = txt_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"成功解析\s*(\d+)\s*份、解析失败\s*(\d+)\s*份", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _audit_info_block(reason: str) -> list[str]:
    """无笔记仓基线时的**信息性降级**：不展示任何 sha（避免误导），只报能确定的事实。

    本机若恰有 `out/` 普查产物，只报告「成功/失败数」并显式标 fallback；CI / 新克隆两者都没有，
    则指向笔记仓基线。这一行不参与 `--check`（见 normalize_for_check）。
    """
    line = f"**普查**：{reason}"
    sf = _audit_success_fail(AUDIT_TXT)
    if sf is not None:
        line += (f"；本机 `out/docx_audit.txt` 仅供参照：成功解析 **{sf[0]}** 份 / 解析失败 **{sf[1]}** 份"
                 "（fallback，信息性，不参与校验）")
    else:
        line += ("；本环境也没有 `out/` 普查产物，成功/失败数见笔记仓基线"
                 "（本行信息性，不参与校验）")
    return [line]


def audit_block() -> tuple[list[str], bool]:
    """AUTO「普查」段：唯一来源 = 笔记仓冻结基线；无笔记仓 → 信息性降级（不展示 sha）。"""
    out, ok = [], True
    source = notes_audit_paths()
    if source is None:
        return _audit_info_block(
            "本环境无笔记仓基线（AUTO 普查数字的唯一来源=笔记仓 `baselines/docx_audit.csv`）"), True
    csv_path, txt_path = source
    try:
        with csv_path.open(encoding="utf-8", newline="") as fh:
            recs = list(csv.DictReader(fh))
    except (OSError, csv.Error) as e:  # 基线文件坏了不是环境差异，但也别把 --check 弄崩
        return _audit_info_block(f"笔记仓基线读取失败（{type(e).__name__}），按信息性处理"), False
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
    sf = _audit_success_fail(txt_path)
    sf_txt = f"成功解析 **{sf[0]}** 份 / 解析失败 **{sf[1]}** 份；" if sf else ""
    out.append(f"**普查**（来源：笔记仓冻结基线 `baselines/docx_audit.csv`，{len(recs)} 份源稿；"
               f"{sf_txt}三态互斥、相加 = 篇数）：")
    out += ["", "| 字段 | `ok` | `fail` | `absent` |", "|---|---|---|---|"]
    for f, cn in FIELDS:
        c = cnt[f]
        out.append(f"| {cn} `{f}` | {c['ok']} | {c['fail']} | {c['absent']} |")
    out += ["", f"- `n_headings` 合计 **{tot_h}**；`n_bib` 合计 **{tot_b}**",
            f"- sha256[:12]：`baselines/docx_audit.csv` = `{sha12(csv_path)}`，"
            f"`baselines/docx_audit.txt` = `{sha12(txt_path)}`"
            f"（来源：笔记仓冻结基线；与 `_ok` 列交叉核对，不一致 **{mismatch}** 行 "
            f"{'✅' if mismatch == 0 else '❌'}）"]
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
# B-159（批82）：**本机状态不进提交物**。分析见笔记仓
# `drafts/b81-b159-auto-state-analysis.md`：`--check` 侧早已把这一行整行归一化
# （`GIT_LINE` → `_GIT_PLACEHOLDER`），该行**从不参与任何判定**；但写入侧从前仍把
# `.git/index` mtime / `HEAD` / `git status` 行数 materialize 进 README → 不参与判定，
# 却保证每次写入产生 1 行 diff（批80/81 的 notes-only 批次都只能写完再 revert）。
# 现在：写模式只写下面这行**确定性文本**；现况仍**只读**采集，落
# `out/gen_docs-state.txt`（README 同级的 `out/`，gitignored、不进提交）。
_GIT_LINE = ("**红线只读快照**（只跑 `git rev-parse` / `git status`，不写）："
             "本机工作区状态不写进提交物；现况见 `out/gen_docs-state.txt`（不提交）")


def git_state_lines() -> list[str]:
    """本机工作区现况：`.git/index` mtime / HEAD / `git status` 行数（**只读** git 命令）。"""
    idx = REPO / ".git" / "index"
    mt = (datetime.fromtimestamp(idx.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
          if idx.is_file() else "（无 .git/index）")
    head = sh(["git", "rev-parse", "--short", "HEAD"], REPO).strip() or "（不可用）"
    n = len([x for x in sh(["git", "status", "--porcelain"], REPO).split("\n") if x.strip()])
    return [f".git/index mtime: {mt}", f"HEAD: {head}", f"git status --porcelain 行数: {n}"]


def state_file() -> Path:
    """现况快照落盘位置：`README.parent/out/gen_docs-state.txt`（`Engine/out/`，gitignored）。

    用 `README.parent` 而不是 `ENGINE`：测试会把 `G.README` 指到 fixture（保持 hermetic），
    快照就跟着落到 fixture 的 `out/` 里，不污染真实仓库。
    """
    return README.parent / "out" / "gen_docs-state.txt"


def record_git_state() -> None:
    """B-159：把本机现况写进 `out/gen_docs-state.txt`。**尽力**写 —— 失败只告警，不改退出码。"""
    try:
        p = state_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        p.write_text("\n".join(["# gen_docs 写模式的本机状态快照（B-159：不写进 README/提交物）",
                                f"generated_at: {stamp}", *git_state_lines()]) + "\n",
                     encoding="utf-8")
    except OSError as e:  # noqa: BLE001 —— 证据文件写不了，不影响主流程
        print(f"⚠ gen_docs: 状态快照写盘失败（忽略）：{e}", file=sys.stderr)


def git_block() -> list[str]:
    """AUTO 块的「红线只读快照」行：**确定性文本**（B-159，不随本机状态变化）。"""
    return [_GIT_LINE]


# ── 组装 ──────────────────────────────────────────────────────────────────
def _sha256_file(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def test_run_identity(inputs: dict) -> str:
    """Stable full-run identity shared with selfcheck and the strict consumer."""
    runner = ENGINE / "tools" / "run_engine_tests.sh"
    runner_sha = _sha256_file(runner)
    if not runner_sha:
        raise RuntimeError(f"full runner script is unavailable: {runner}")
    payload = {"auto_inputs": inputs, "runner_script_sha256": runner_sha}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_current_test_manifest(inputs_now: dict) -> tuple[list | None, str]:
    """Load and validate the runner's ephemeral schema-1 full-run manifest.

    A returned row list contains only AUTO suites. The self-referential docs suite is
    nevertheless mandatory in the runner manifest and must be green before this proof
    can replace a strict test run.
    """
    path_text = os.environ.get("SLCN_TEST_RESULT_MANIFEST", "").strip()
    identity_text = os.environ.get("SLCN_TEST_INPUTS_SHA256", "").strip().lower()
    if not path_text:
        return None, "没有配置当前运行 manifest"
    if not re.fullmatch(r"[0-9a-f]{64}", identity_text):
        return None, "当前运行 identity 缺失或格式错误"
    try:
        expected_identity = test_run_identity(inputs_now)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None, "无法计算当前 full runner identity"
    if identity_text != expected_identity:
        return None, "SLCN_TEST_INPUTS_SHA256 与当前输入/runner 不匹配"
    try:
        manifest_path = Path(path_text).expanduser()
        if not manifest_path.is_file() or manifest_path.stat().st_size > 4 * 1024 * 1024:
            return None, "当前运行 manifest 缺失或超过大小上限"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None, "当前运行 manifest 不可读或 JSON 无效"
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data.get("schema") != 1:
        return None, "manifest schema 必须为整数 1"
    if data.get("mode") != "full":
        return None, "manifest mode 必须为 full"
    if data.get("identity_sha256") != expected_identity:
        return None, "manifest identity 与当前输入/runner 不匹配"

    expected_names = [f.stem for f in runner_test_files()]
    suites = data.get("suites")
    if not isinstance(suites, list) or len(suites) != len(expected_names):
        return None, "manifest suite 覆盖面与 full runner 不一致"
    by_name: dict[str, dict] = {}
    for row in suites:
        if not isinstance(row, dict):
            return None, "manifest suite row 必须为对象"
        name = row.get("name")
        if not isinstance(name, str) or name not in expected_names or name in by_name:
            return None, "manifest suite 名称无效或重复"
        passes, fails, return_code = row.get("passes"), row.get("fails"), row.get("return_code")
        status, duration = row.get("status"), row.get("duration_seconds")
        if (type(passes) is not int or passes < 0 or type(fails) is not int or fails < 0
                or type(return_code) is not int):
            return None, f"manifest suite {name} 的计数/退出码类型无效"
        if status is not None and not isinstance(status, str):
            return None, f"manifest suite {name} 的 status 类型无效"
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration < 0):
            return None, f"manifest suite {name} 的 duration_seconds 无效"
        if status is None and (return_code != 0 or fails != 0):
            return None, f"manifest suite {name} 以 status=null 标记，但退出码或 FAIL 数非零"
        by_name[name] = row
    if set(by_name) != set(expected_names):
        return None, "manifest 未精确覆盖 full runner suites"

    docs_name = "test_docs_consistency"
    docs = by_name.get(docs_name)
    if (docs is None or docs["status"] is not None or docs["return_code"] != 0
            or docs["fails"] != 0):
        return None, "manifest 缺少成功的 test_docs_consistency 自检"
    auto_names = [f.stem for f in test_files()]
    rows = [(name, by_name[name]["passes"], by_name[name]["fails"], by_name[name]["status"])
            for name in auto_names]
    return rows, ""


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
    """AUTO 块的输入清单（O14）：每个数据源的 sha + 环境前提 + 稿件库指纹。

    B-136（批77）：增 `notes_audit_csv_sha256` / `notes_audit_txt_sha256` —— **AUTO 普查数字的
    真正输入**（笔记仓冻结基线）。它们一变 → 快速通道失效，`--check` 回退到现算比对，
    于是「基线更新了但 README 没重写」当场被抓。无笔记仓环境两项为空字符串。
    """
    env_ok, missing = environment_complete()
    tex_sha, tex_n = _corpus_tex_manifest()
    tests_sha, tests_n = _tests_manifest()
    manuscripts_root, manuscripts_fp = _manuscripts_info()
    dataset_id = os.environ.get("SLCN_TEST_DATASET_ID", "").strip()
    notes = notes_audit_paths()
    return {
        "schema": AUTO_INPUTS_SCHEMA,
        "corpus_csv_sha256": _sha256_file(CORPUS_CSV),
        "corpus_tex_manifest_sha256": tex_sha,
        "corpus_tex_files": tex_n,
        # out 缓存（gitignored）不再是 AUTO 展示的来源，仅作为无笔记仓环境的 fallback /
        # 环境前提留在清单里（键名不变：既有调用方与测试依赖）。
        "audit_csv_sha256": _sha256_file(AUDIT_CSV),
        "audit_txt_sha256": _sha256_file(AUDIT_TXT),
        "notes_audit_csv_sha256": _sha256_file(notes[0]) if notes else "",
        "notes_audit_txt_sha256": _sha256_file(notes[1]) if notes else "",
        "tests_manifest_sha256": tests_sha,
        "tests_files": tests_n,
        "requirements_sha256": _sha256_file(ENGINE / "requirements.txt"),
        "engine_py_sha256": (_run_record.engine_py_sha256() if _run_record is not None else ""),
        "test_runtime_fingerprint": test_runtime_fingerprint(),
        "env_complete": env_ok,
        "env_missing": missing,
        "manuscripts_present": not any(str(item).startswith("稿件库") for item in missing),
        "manuscripts_root": manuscripts_root,
        "manuscripts_fingerprint": manuscripts_fp,
        "test_dataset_id_sha256": hashlib.sha256(dataset_id.encode("utf-8")).hexdigest()
        if dataset_id else "",
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
    """测试计数缓存的第一道闸：**只有 engine/tests/稿件库/环境前提都没变**时才允许复用。

    测试计数只取决于这几项（同一环境下）；corpus/audit 变了不影响计数。

    ⚠️ **写模式不要直接调本函数**（卡 §7 第 1 项）：它的判据是「不含失败证据」，于是
    缺 status 的三列缓存也能过 —— 拿它当写模式的判据，等于把「上次可能没跑完」的计数
    升级成「套件通过」的证据。写模式用 `reusable_test_rows()`（本函数 + 4 列/覆盖面/块自证）。
    本函数保留原语义，是因为 (e3) 与 `test_b74` ③ 直接断言它、且渲染层要兼容三列替身。
    """
    if not project or project.get("schema") != AUTO_INPUTS_SCHEMA:
        # G1：schema 1 的三列行**没有套件状态**，复用等于把「上次可能崩过一半」的计数
        # 当成 PASS。旧缓存一律不认 —— 宁可多跑一遍套件，也不许把不可信数字写进块里。
        return None
    if not persistent_cache_allowed(inputs_now):
        return None
    old = project.get("inputs") or {}
    keys = ("engine_py_sha256", "tests_manifest_sha256", "env_complete", "manuscripts_fingerprint",
            "manuscripts_present", "test_dataset_id_sha256", "requirements_sha256",
            "test_runtime_fingerprint")
    if any(old.get(k) != inputs_now.get(k) for k in keys):
        return None
    rows = project.get("test_rows")
    if not isinstance(rows, list) or not rows:
        return None
    # B-110：缓存里带着 FAIL 计数的**不许复用**——那是一张「上次套件没全绿」的快照
    # （典型：RB-08 自指瞬态被写进块里，随后就被缓存「粘住」，只能删 out/ 缓存才恢复）。
    # 有 FAIL 就重跑套件让计数回到现算值；全绿快照的复用语义不变。
    try:
        if any(int(r[2]) for r in rows):
            return None
    except (TypeError, ValueError, IndexError):
        return None
    # G1：**套件状态**非 None 的缓存行同样不许复用（0 FAIL 但崩溃/超时/非零退出）。
    # 缺第 4 列的三元素行（G1 之前的写法，或测试里手写的 `[["test_x", 1, 0]]`）
    # 视为「不含失败证据」，语义与 B-110 之前一致，继续可复用。
    #
    # ⚠️ 这里只做「不含失败证据」这一层判断 —— 三列行**能过**这一关是历史语义，是给
    #    渲染与 `test_docs_consistency.py` 的 (e3) / `test_b74` ③ 留的兼容面。
    #    **写模式不许走这条宽口径**：它要的是「能声称套件通过」的证据，必须 4 列且与
    #    块内测试段自洽 —— 见 `reusable_test_rows()`（卡 §7 第 1 项）。
    if abnormal_suites(rows):
        return None
    return rows


def persistent_cache_allowed(inputs_now: dict) -> bool:
    """A persistent suite cache needs an explicit identity for any detected manuscript root."""
    return not (inputs_now.get("manuscripts_present") is True
                and not inputs_now.get("test_dataset_id_sha256"))


def reusable_test_rows(inputs_now: dict, project: dict | None, block_body: str | None) -> list | None:
    """**写模式**真正采用的测试计数缓存：在 `cached_test_rows()` 之上再加两道闸。

    GATE-RELIABILITY §7 第 1 项（qoder §18 / Codex lead §19 的「G1 写模式缓存复用 FAIL」）：
    `cached_test_rows()` 的宽口径会把**缺 status 的三列缓存**当成「不含失败证据」放行，
    写模式随后 `_norm_test_rows()` 把 status 补成 None、渲染成 `✅` 并 `save_auto_inputs()`
    落盘 —— 等于把「上次可能没跑完/没跑过」的计数升级成「套件通过」的证据。

    所以写模式的复用判据是下面三条**同时**成立，任一条不成立就回退到「现跑套件」分支：

    ① `cached_test_rows()` 全过（schema/指纹/无 FAIL/无异常 status）；
    ② `suite_rows_trusted(rows, expected=[f.stem for f in test_files()])`：
       每行 **≥4 列**、status **严格 `is None`**、且套件名单**恰好**覆盖当前全部套件
       （卡 §9 第 1 项：不保留 `covered == 0` 豁免 —— 全假名缓存也要拒）；
    ③ `cached_rows_match_block(block_body, rows)`：拿缓存重渲染一遍，与 README 里**已经写着**
       的测试段逐行一致 —— 被截断（174 个套件只剩 1 行）、被改名、被塞进多余行，都会与块
       对不上；对不上说明缓存和块不是同一次生成的，不许当证据。

    ③ 是「缓存自证」：缓存自己说自己对没有意义，必须与提交进仓库的那段文字互相印证。
    真实的成功缓存（写模式刚写出来的那种）天然满足三条，所以**没有关掉缓存**；
    只有「形状不全 / 名单不符 / 与块不一致」的缓存被拒，且拒了之后走的是原有的现跑分支。
    """
    rows = cached_test_rows(inputs_now, project)
    if rows is None:
        return None
    # ③a 覆盖面（卡 §9 第 1 项）：缓存的套件名单必须**无条件恰好**覆盖当前 `test_files()`。
    # 多一行 / 少一行 / 换一个名字 / 全是假名（`covered == 0`）都算不成立 —— 一律回退现跑。
    #
    # ⚠️ 这里曾有一个 `covered == 0` 的豁免（"一行都对不上就当测试替身"），
    #    qoder §22.2.1 用它把 all-fake 缓存 + all-fake README 段自洽地复用成功并落盘了假名。
    #    卡 §9 第 1 项明确**不许保留这个特例**；测试替身改在测试自己那边注入 `test_files()`
    #    （`test_b74_gen_docs_write_once.py`），不再占用生产的判据。
    if not suite_rows_trusted(rows, expected=[f.stem for f in test_files()]):
        return None
    if not cached_rows_match_block(block_body or "", rows):
        return None
    return rows


def fast_check_ok(old_body: str, project: dict | None, inputs_now: dict) -> bool:
    """`--check` 快速通道：输入指纹未变 **且** README 块指纹 == 上次写出的块指纹。

    成立时可直接 PASS（不必重跑套件）；任一项对不上都回退到「现算 + 逐行比对」。
    """
    if not project or project.get("schema") != AUTO_INPUTS_SCHEMA:
        return False
    if not persistent_cache_allowed(inputs_now):
        return False
    if not inputs_now.get("env_complete"):
        return False
    if inputs_sha(project.get("inputs") or {}) != inputs_sha(inputs_now):
        return False
    stored = project.get("block_sha256")
    if not stored:
        return False
    # G1：缓存里只要有**套件状态异常**的行，就不能靠指纹直接宣布 PASS ——
    # 块指纹只能证明「README 与上次写出的一致」，不能证明「套件那次真的跑成功了」。
    if abnormal_suites(project.get("test_rows")):
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
                cached_test_rows_in: list | None = None,
                test_section: list[str] | None = None,
                test_rows_in: list | None = None) -> tuple[str, bool]:
    """组装 AUTO 块正文，返回 (块正文, 块内「数据」检查是否全 ok)。

    `test_section` / `test_rows_in` 只给**写模式**用（B-110 自指循环修复）：
    * `test_section`：把 README 里**原样**的「测试」段行拿来占位（中间态落盘用，不重算、不造假数字）；
    * `test_rows_in`：套件已在外面跑完，直接用这批 `(套件, 断言数, FAIL 数)` 渲染，不重复跑。
    """
    global _LAST_TEST_ROWS
    ok = True
    if test_section is not None:
        # B-110：写模式的**中间态**——数据块先行落盘时，原样保留 README 里尚未重算的测试段。
        # 它不参与 `--no-test-runs` 比对（那两行会被整段剔除），也不会被当成新算的数字。
        lines = list(test_section)
        rows: list[tuple[str, int, int, str | None]] = []
        total = 0
        _LAST_TEST_ROWS = None
    elif test_rows_in is not None:
        # B-110：套件已跑完（写模式第二阶段），直接渲染测试计数。
        rows = _norm_test_rows(test_rows_in)
        total = sum(p for _, p, _, _st in rows)
        _LAST_TEST_ROWS = rows
    elif cached_test_rows_in is not None:
        # O14：engine/tests 未变 → 测试计数直接复用（corpus/audit 变化不必整跑套件）
        global _CACHED_TESTS_USED
        rows = _norm_test_rows(cached_test_rows_in)
        total = sum(p for _, p, _, _st in rows)
        _LAST_TEST_ROWS = rows
        _CACHED_TESTS_USED = True
        print("提示  测试计数复用 out/auto-inputs.json 缓存（engine/tests 未变；本次未跑套件）",
              file=sys.stderr)
    elif run_test_suites:
        rows, total = run_tests()
        # G1：`run_tests()` 现在给的是 4 列（含 status）；这里过一遍 `_norm_test_rows`
        # 只为兼容老测试替身（3 列）—— 别让一行旧形状的替身把整个门禁炸掉。
        rows = _norm_test_rows(rows)
        total = sum(p for _, p, _, _st in rows)
        _LAST_TEST_ROWS = rows
    else:
        # `--no-test-runs`：**不跑**套件，只保留套件清单的行数与形状（比对前测试行会被整段剔除）。
        # 用 0 而不是真值，是为了"绝不把占位数字写进 README"——写模式禁用本开关，见 main()。
        rows, total = [(f.stem, 0, 0, None) for f in test_files()], 0
        _LAST_TEST_ROWS = None
    # G1：FAIL 断言数（bad）与「套件本身是否可信」（abnormal）是**两件事**——
    # 一个套件可以 0 FAIL 却崩溃/超时/根本没跑起来（rc!=0）。ok 必须同时看这两者。
    bad = [(n, f) for n, p, f, _st in rows if f]
    abnormal = abnormal_suites(rows)
    if test_section is None:
        lines = render_test_section(rows, total)
    for fn in (audit_block, corpus_block):
        sub, sub_ok = fn()
        lines += sub + [""]
        ok = ok and sub_ok
    # App 包内一致性**不在 AUTO 块里**（第四十四批③a / O9）：它依赖「本机是否刚 build 过」，
    # 放进 AUTO 会让 gen_docs 的测试计数（经 test_selfcheck → selfcheck）依赖 App 新鲜度，
    # 形成「gen_docs×2 + build×2」的循环依赖。现在由 build.sh 末尾 `--cmp-app` 守卫（见 app_cmp）。
    lines += placeholder_block() + [""]
    lines += git_block()
    if bad or abnormal:
        # G1：**FAIL 行数**与**套件状态**分开判 —— 一个套件可以 0 FAIL 却崩溃/超时/没跑起来。
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
# 「普查」整段：**无笔记仓的环境（CI / 新克隆）无法重现**它的数据（唯一来源在私有笔记仓），
# 按环境差异归一化 —— 否则开发机上写出的 notes 基线块到了 CI 必然被判过期（B-136）。
# 有笔记仓时**不**归一化：基线 sha 漂移必须被 --check 抓住（反例见新测试）。
AUDIT_SECTION = re.compile(r"^\*\*普查\*\*.*?(?=^\*\*corpus 回归)", re.M | re.S)
_AUDIT_PLACEHOLDER = "**普查**：（本环境无笔记仓基线，普查数字不参与校验）"


# 测试计数两行：**在本机前提齐全时是硬断言**（"数字不许手抄"最要紧的一条），
# 但缺前提时用例会 SKIP，计数必然变小（CI 上没有稿件库/TeX/LibreOffice 就是这样）——
# 那种情况下把它们也归一化掉，否则 CI 永远红，久而久之就没人看 CI 了。
TEST_SUMMARY_LINE = re.compile(r"^\*\*测试\*\*（.*$", re.M)
TEST_BREAKDOWN_LINE = re.compile(r"^`test_[a-z0-9_]+` \d+( ｜ `test_[a-z0-9_]+` \d+)*$", re.M)
# ⚠️ 明细的**第一行**带前缀（`套件明细：`test_…` 45 ｜ …`），不匹配上面的纯明细正则 ——
#    `strip_test_lines()` 必须单独认它，否则 --no-test-runs 会把这一行留在比对里而误红。
TEST_BREAKDOWN_HEAD = "套件明细："
# G1：套件**状态**异常（崩溃/超时/非零退出）时的逐条明细行。全绿时这一行根本不出现，
# 所以历史渲染逐字不变；出现时它同样属于「测试段」，--no-test-runs 必须一并剔除。
TEST_ABNORMAL_HEAD = "异常套件："
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
        from slcn import platform as _plat
        # B-304/D-18：Windows 的 LibreOffice 安装器**不**把 soffice 加进 PATH ——
        # 旧实现只看 `shutil.which("soffice")`，于是本机明明装了也误判「缺 LibreOffice」
        # → 一直走宽松模式。改用平台模块的统一定位（SLCN_SOFFICE → PATH → 常见安装位置）。
        if _plat.find_soffice() is None:
            missing.append("LibreOffice")
    except Exception as e:  # noqa: BLE001
        missing.append(f"LibreOffice（探测异常 {type(e).__name__}）")
    return (not missing), missing


def normalize_for_check(body: str, lenient: bool = False) -> str:
    # APP_LINE 用**删除**（迁移兼容，见上面的注释）；GIT_LINE 仍是占位符替换。
    out = GIT_LINE.sub(_GIT_PLACEHOLDER, APP_LINE.sub("", body))
    # B-136：无笔记仓的环境（CI / 新克隆）看不到冻结基线 → 「普查」整段按环境差异归一化。
    # 有笔记仓的机器上**不**归一化 → 基线 sha 漂移仍会把 --check 判红。
    if notes_audit_paths() is None:
        out = AUDIT_SECTION.sub(_AUDIT_PLACEHOLDER, out)
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
            and not ln.startswith(TEST_BREAKDOWN_HEAD)
            and not ln.startswith(TEST_ABNORMAL_HEAD)]
    return "\n".join(keep)


def read_readme() -> str:
    return README.read_text(encoding="utf-8")


def split_block(text: str) -> tuple[str, str, str]:
    """返回 (块前, 块内, 块后)；标记行本身留在「块前」「块后」里。"""
    i = text.index(BEGIN) + len(BEGIN)
    j = text.index(END)
    return text[:i], text[i:j], text[j:]


# ── B-110（批74）：写模式「数据块先行」的两个小工具 ───────────────────────────
# RB-08（连续 3 次复发）：数据变了而 README 的 AUTO 块还是旧值时，写模式从前是
# 「先跑套件算测试计数」→ 套件里的 `test_docs_consistency.py` / `test_selfcheck.py`
# 会调用 `gen_docs.py --check --no-test-runs`，那一刻看到的数据块仍是旧的 → 记 FAIL，
# AUTO 块写出「FAIL 1」瞬态；必须再写一次（或删 `out/auto-inputs.json` 缓存）才绿。
# 现在写模式改成：**先把非测试数据块算好并落盘**，再跑套件补写测试计数 ——
# 套件里的自指检查在同一进程内第二次落盘前，看到的数据块已经是现算的新值。
_DATA_HEAD_PREFIXES = ("**普查**", "**corpus 回归**", "**占位符**", "**红线只读快照**")


def test_section_lines(body: str) -> list[str] | None:
    """取 AUTO 块正文里「测试」段（摘要 + 明细 + 分隔空行）的逐字行；找不到返回 None。

    中间态落盘用它原样占位：**不重算、不造假数字**（写模式仍不许写占位数字）。
    """
    text = body[1:] if body.startswith("\n") else body
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith(_DATA_HEAD_PREFIXES):
            return lines[:i]
    return None


def _flush_block(new: str, head: str, tail: str) -> tuple[str, bool]:
    """把块正文 `new` 落到 README；返回 (最终正文, 是否改写)。

    第四十一批 O1：只就地替换那一行（两条只读 git 命令），不重算整块、不再跑套件。
    B-110：写模式可能分两次调用（数据块先行 / 测试计数补写），每次落盘都走这里。
    B-159（批82）：状态行已改成**确定性文本** → 原来的「写 README 改变 status 行数」自指
    消失，3 轮收敛循环简化为**写一次即稳定**；本机现况改落 `out/gen_docs-state.txt`。
    """
    stable = head + "\n" + new + tail
    changed = stable != read_readme()
    if changed:
        # B-304：显式 newline="\n" —— Windows 上 Path.write_text 默认做 os.linesep 转换，
        # 会把 README 整体改成 CRLF（git 显示全文件 468/234 行变更）。README 是核对物，
        # 必须两平台都写 LF。
        README.write_text(stable, encoding="utf-8", newline="\n")
    record_git_state()
    return new, changed


def _main_impl(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验 README.md 的「当前状态」AUTO 块")
    ap.add_argument("--check", action="store_true", help="只校验，不一致就退出 1 并打印 diff")
    ap.add_argument("--no-test-runs", action="store_true",
                    help="（只能配 --check）不跑套件：AUTO 的「测试」计数行不参与比对。"
                         "供 full 套件 / selfcheck quick 去掉重复的一遍套件；"
                         "测试计数守卫由独立的 `gen_docs.py --check` 负责")
    ap.add_argument("--strict-tests", action="store_true",
                    help="（只能配 --check）严格测试入口：前提不全 → 打印 BLOCKED + 原因清单并退出 2；"
                         "测试计数行按实测严格比对（不做宽松归一化）。"
                         "把「文档新鲜度」与「套件通过」分开报，两者都绿才算真绿")
    ap.add_argument("--cmp-app", action="store_true",
                    help="只比对 App 包内引擎白名单（APP_FILES，28 个）与仓库；毫秒级。"
                         "build.sh 末尾用；不读写 README")
    ap.add_argument("--app-dir", default=None,
                    help="（配 --cmp-app）覆盖包内 Engine 目录，供反例测试模拟「包内过期」")
    a = ap.parse_args(argv)
    if a.no_test_runs and not a.check:
        ap.error("--no-test-runs 只能与 --check 一起用：写模式必须现算测试计数")
    if a.strict_tests and not a.check:
        ap.error("--strict-tests 只能与 --check 一起用：写模式的成功判据已经是实跑套件")
    if a.no_test_runs and a.strict_tests:
        # G1：两件事互相矛盾 —— 一个声称"没跑套件、不表态"，一个要求"必须实测通过"。
        # 允许叠加等于给"我没跑，但请当它过了"开口子，直接拒。
        ap.error("--no-test-runs 与 --strict-tests 互斥：前者不声称任何套件状态，后者要求实测")

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
        # G1：严格入口。缺前提时**不许**退化成宽松校验 —— 那正是「红灯变绿」的元凶
        # （CI 上没有稿件库 → 测试用例全 SKIP、计数变小 → 宽松归一化 → 永远 PASS）。
        if a.strict_tests and not complete:
            print(f"BLOCKED  严格测试校验无法进行：本机缺 {'、'.join(missing)}")
            for m in missing:
                print(f"      - {m}：缺它时相关用例会 SKIP，测试计数与依赖稿件库的数字必然不可信")
            print("      严格模式不会把「前提不全」当「通过」；请在具备前提的机器上重跑，")
            print("      或改用非严格的 `tools/gen_docs.py --check`（只保证文档新鲜度）。")
            return 2
        # 严格模式绝不宽化：测试计数行按实测逐字比对（契约 3 的宽松归一化在严格下禁用）。
        lenient = (not complete) and not a.strict_tests
        inputs_now = collect_auto_inputs() if a.strict_tests else None
        if a.strict_tests:
            runtime_missing = strict_runtime_missing(inputs_now.get("test_runtime_fingerprint", {}))
            if runtime_missing:
                print("BLOCKED  严格测试校验环境预检失败：")
                for item in runtime_missing:
                    print(f"      - {item}")
                print("      尚未启动测试套件；请先修复运行时依赖/工具，再重跑严格校验。")
                return 2
            manifest_rows, manifest_reason = load_current_test_manifest(inputs_now)
            if manifest_rows is None:
                print(f"提示  当前 full runner manifest 未采信（{manifest_reason}）；"
                      f"严格档将实跑全部 {len(runner_test_files())} 个套件，含文档自检")
            else:
                print("严格校验证据：已采信与当前输入和 runner 匹配的 full manifest；"
                      "文档自检成功，AUTO 只计入非自指套件")
        else:
            manifest_rows = None
        # O14 快速通道只供非严格新鲜度校验使用。严格档优先接受本轮 full manifest；
        # 缺少有效 manifest 时必须实跑，不以历史快照替代本轮及文档自检证据。
        if not lenient and not a.strict_tests:
            project = load_auto_inputs()
            if project:
                inputs_now = collect_auto_inputs()
                _fast_ok = fast_check_ok(old, project, inputs_now)
                if _fast_ok:
                    global _FAST_PATH_USED
                    _FAST_PATH_USED = True
                    print("PASS  AUTO 块与上次写入一致；输入指纹未变"
                          "（快速通道：corpus/audit/engine/tests 均未变，未跑套件）")
                    return 0
        if a.strict_tests:
            if manifest_rows is None:
                full_rows, _ = run_tests(files=runner_test_files())
                full_by_name = {str(row[0]): row for row in full_rows}
                docs_row = full_by_name.get("test_docs_consistency")
                if (docs_row is None or len(docs_row) < 4 or docs_row[3] is not None
                        or int(docs_row[2]) != 0):
                    status = "缺失" if docs_row is None else str(docs_row[3] or "非绿色退出")
                    print(f"FAIL  严格校验的 test_docs_consistency 自检未通过：{status}")
                    return 1
                expected = {f.stem for f in test_files()}
                if not expected.issubset(full_by_name):
                    print("FAIL  严格实跑结果未覆盖全部 AUTO suites")
                    return 1
                rows = [full_by_name[f.stem] for f in test_files()]
            else:
                rows = manifest_rows
            new, all_ok = build_block(run_test_suites=False, test_rows_in=rows)
            suites_ran = True
        else:
            # 非严格检查可显式跳过 suites；默认的非严格 --check 仍走现算。
            new, all_ok = build_block(run_test_suites=not a.no_test_runs)
            suites_ran = not a.no_test_runs
        abnormal = abnormal_suites(_LAST_TEST_ROWS) if suites_ran else []
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
        elif a.strict_tests:
            source = "full runner manifest" if manifest_rows is not None else "本次完整实跑"
            print(f"严格测试校验（前提齐全）：证据={source}，"
                  f"AUTO suites {len(_LAST_TEST_ROWS or [])} 个，异常 {len(abnormal)} 个；"
                  "测试计数行按实测逐字比对")
        if old_cmp == new_cmp:
            if a.no_test_runs:
                print("PASS  AUTO 块非测试行与现算结果一致（--no-test-runs：本次未跑套件，"
                      "测试计数行不参与比对）")
            elif old != new_body:
                print("PASS  AUTO 块与现算结果一致（差异只在本机状态那几行——"
                      "按设计不参与校验）")
            else:
                print("PASS  AUTO 块与现算结果逐字一致")
            # G1：「文档新鲜度」与「套件通过」是**两个结论**。块新鲜不等于套件跑成功了；
            #     本次真跑了套件且有异常套件 → 必须给出明确的非 PASS 行并 rc≠0，
            #     不许像从前那样用一句"提示 …不影响 --check 的结论"把它咽掉。
            if abnormal:
                print(f"FAIL  套件异常 {len(abnormal)}/{len(_LAST_TEST_ROWS or [])}："
                      "文档新鲜度通过，但「套件通过」未达成")
                for n, st in abnormal:
                    print(f"      - {n}: {st}")
                return 1
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
        # G1：过期与套件异常是两件事，都要报 —— 否则只修好文档新鲜度就以为"全绿"了。
        if abnormal:
            print(f"FAIL  另：本次实跑的套件里有 {len(abnormal)}/{len(_LAST_TEST_ROWS or [])} 个异常："
                  + "、".join(f"{n}({st})" for n, st in abnormal))
        return 1

    # ── 写模式（O14：engine/tests/稿件库/环境都没变时，**复用测试计数缓存**）──
    #    B-110 / RB-08：「测试计数缓存不可复用」时不再「先跑套件」——先落盘非测试数据块
    #    （套件里的自指检查看到的因此已是新数据），再跑套件补写测试计数。
    inputs_now = collect_auto_inputs()
    # 卡 §7 第 1 项：写模式只认**能自证**的缓存（4 列带 status、与 README 测试段逐行一致）。
    # 三列/截断/错名/多余的缓存一律拒 → 走下面的现跑分支，绝不当「套件通过」的证据。
    cached = reusable_test_rows(inputs_now, load_auto_inputs(), old)
    if cached is not None:
        # 快速路径：测试计数可复用（engine/tests/稿件库/环境都没变）→ 一次算全块、一次落盘。
        new, all_ok = build_block(run_test_suites=False, cached_test_rows_in=cached)
        new, changed = _flush_block(new, head, tail)
        if changed:
            print("PASS  已就地更新 AUTO 块（块外内容逐字节未动）")
        else:
            print("PASS  AUTO 块已是最新，未改动 README.md")
    else:
        # B-110 / RB-08（批74）：从前这里是「先跑套件」→ 套件里的 `test_docs_consistency.py`
        # 与 `test_selfcheck.py` 会调用 `gen_docs.py --check --no-test-runs`，此刻 README 的
        # 数据块还是旧值 → 当场记 FAIL，AUTO 写出「FAIL 1」瞬态，必须再写一次（或删
        # `out/auto-inputs.json` 缓存）才绿。现在改成：
        #   ① 先算并落盘**非测试数据块**（「测试」段沿用 README 里现有的行，不写占位数字）；
        #   ② 再跑套件；③ 用现算的测试计数补写最终块。
        # 一次调用内部允许**两次落盘**，对外仍是「写 1 次」；中途失败 → rc≠0，重跑可收敛。
        interim, _ = build_block(run_test_suites=False,
                                 test_section=test_section_lines(old) or [])
        _, wrote_interim = _flush_block(interim, head, tail)
        print("提示  写模式：非测试数据块已先行落盘"
              + ("（README 数据块本来已是最新，未改写）" if not wrote_interim else "")
              + "；接着跑套件算测试计数（自指检查看到的是新数据）", file=sys.stderr)
        rows, _total = run_tests()
        new, all_ok = build_block(run_test_suites=False, test_rows_in=rows)
        new, changed = _flush_block(new, head, tail)
        if changed:
            print("PASS  已就地更新 AUTO 块（数据块先行 + 测试计数补写；块外内容逐字节未动）")
        else:
            print("PASS  AUTO 块已是最新，未改动 README.md")
    new_body = "\n" + new
    after_head, after, after_tail = split_block(read_readme())
    assert (head, tail) == (after_head, after_tail), "块外内容被改动了 —— 这是 bug"
    assert after == new_body, "写后 AUTO 块与算出结果不一致 —— 这是 bug"
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
                engine_sha: str | None = None, strict_tests: bool = False) -> None:
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
                # G1：严格测试入口是**另一档语义**（缺前提 BLOCKED / 测试计数按实测严格比对），
                # manifest 里必须能区分「非严格 --check 通过」与「严格测试校验通过」。
                "strict_tests": strict_tests,
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
    strict_tests = "--strict-tests" in args
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
                        no_test_runs=no_test_runs, readme_changed=changed, engine_sha=engine_sha,
                        strict_tests=strict_tests)


if __name__ == "__main__":
    sys.exit(main())
