"""Windows 外壳与引擎之间的**唯一**桥梁（不含任何界面代码）。

为什么单独一个模块、而且**不许 import tkinter**：
  1. 逻辑要能**离开窗口测试** —— `tests/test_bridge.py` 在 mac/CI 上跑得动，
     不必有显示器、不必真开窗口；
  2. 界面只负责「取值 → 拼参数 → 显示日志」，这条纪律与 mac 外壳一致
     （见 `Engine/docs/ENGINE_API.md`「外壳的义务」）。

**引擎怎么找**（与 mac 外壳同样的思路，但 Windows 上没有 Xcode/Homebrew 那套路径）：
  `slcn_engine()` 依次尝试
    ① 环境变量 `SLCN_ENGINE`（指到 `Engine/` 目录）
    ② **打包形态**：本 exe 同级的 `Engine/`（PyInstaller 会把它放进 `_MEIPASS` 或旁边）
    ③ 开发形态：本文件上一级的 `../Engine/`（仓库里 `sources-win/` 与 `Engine/` 并列）
  `slcn_python()` 依次尝试
    ① 环境变量 `SLCN_PYTHON`
    ② 已经装了 Python 的开发机：`python` / `python3` / Windows 的 `py` 启动器
    ③ **冻结引擎**：`SLCNEngine.exe`（把十二个 CLI 冻成一个 exe，见 `Engine/engine_main.py`；
       本文件的 `ENTRIES` 必须与它的 `ENTRIES` 逐项一致 —— 由 tests/test_bridge.py 守着）
       第 132 条起 `bibdb.py`（引用库 CLI）也在表里：Windows「引用库」5 页的数据源，
       四处入口表（外壳 `ENTRIES` / dispatcher `ENTRIES` / build-win.ps1 / 桥接断言）
       必须一起改 —— 少一处，用户机器上那 5 页全部「❌ 不认识的入口」退出 2。
  —— 顺序上「先找真 Python」是因为开发机上改完代码就能验证；发布包走 ③，用户机器上不需要装 Python。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field

import clean_logic as _clean_logic   # B-281：清理三模式的纯逻辑（同目录、无 tkinter）

#: CLI 入口（与 `Engine/docs/ENGINE_API.md` 一致）。
#: 第 132 条加了 `bibdb.py`（引用库）——它与 mac 包共用同一份 CLI，Windows 侧不重写判据。
#: B-283：末尾追加 `envcheck.py`（首次运行环境自检）——**前 10 项顺序不动**。
#: B-285b：再追加 `diagnose.py`（失败诊断）——**前 11 项顺序不动**，12 项与
#: `Engine/engine_main.py: ENTRIES` 逐项一致（由 tests/test_bridge.py (b) 守着）。
ENTRIES = ("convert.py", "checkbib.py", "mergepdf.py",
           "cleantemp.py", "compileall.py", "proofcheck.py",
           "infocheck.py", "toc.py", "publish.py", "bibdb.py",
           "envcheck.py", "diagnose.py")


# ── 日志分层（U4：摘要 / 原始两层，默认折叠原始）─────────────────────────
#: 摘要行的标记词 —— 与 mac 侧 `sources-mac/Core/Log.swift` 的 `LogStore.summaryMarkers`
#: **逐字相同**：两侧跑同一份夹具 `tools/log_cases.json`（Swift 在 `tools/ai_settings_test`，
#: Python 在 `sources-win/tests/test_bridge.py`）。改词表必须两边同时改并更新那份夹具 ——
#: 否则「两个平台算出来的结果一致」就破了。
SUMMARY_MARKERS = (
    "✅", "⚠", "❌", "❗", "ℹ", "⏹", "✓", "✗", "⛔",
    "完成。", "程序错误", "未找到", "启动失败", "失败", "警告", "需核对", "不一致",
    "命中", "跳过", "取消", "产物", "结论", "报告", "备份", "提示", "合计",
    "已配置", "通道", "预演", "剪贴板",
)

#: 结构前缀：进度行（`[3/25] …` / `（3/25）…`）。
_SUMMARY_PROGRESS = re.compile(r"^(?:\[\d+/\d+\]|（\d+/\d+）)")
#: 阶段圈码（`① …` 到 `⑳ …`）。
_SUMMARY_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def is_summary_line(text: str) -> bool:
    """一行日志是否属于「摘要层」（摘要只留关键节点与结果，原始层逐行全留）。

    判据只有三类（与 mac 侧 `LogStore.isSummaryLine` 同源）：标记词、阶段前缀
    （`▶` / 圈码 `①②…`）、进度行前缀（`[i/N]` / `（i/N）`）。空行（含纯空白）
    **不算**摘要 —— 否则默认的摘要视图里全是空行。

    宁可多收一点：摘要只是默认视图，切到「原始」永远能看到全部行；反过来漏掉
    一句「✅ 完成」才真要命。唯一的取舍是**不认行首相对时间戳**（外壳会加
    `+0:03.2s`）—— 那是界面的装饰，调用方拿引擎原文判定（见 `main.MainWindow.log`）。
    """
    s = (text or "").strip()
    if not s:
        return False
    if any(marker in s for marker in SUMMARY_MARKERS):
        return True
    if s.startswith("▶"):
        return True
    if s[0] in _SUMMARY_CIRCLED:
        return True
    return bool(_SUMMARY_PROGRESS.match(s))


# ── 定位引擎与解释器 ─────────────────────────────────────────────────────

def _shell_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def slcn_engine() -> str | None:
    """找到 `Engine/` 目录（内含 `convert.py`），找不到返回 None。"""
    cands: list[str] = []
    env = os.environ.get("SLCN_ENGINE", "").strip()
    if env:
        cands.append(env)
    if getattr(sys, "frozen", False):                      # PyInstaller 打包后的形态
        cands.append(os.path.join(os.path.dirname(sys.executable), "Engine"))
        cands.append(os.path.join(getattr(sys, "_MEIPASS", ""), "Engine"))
    cands.append(os.path.join(os.path.dirname(_shell_dir()), "Engine"))   # 开发形态
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "convert.py")):
            return os.path.abspath(c)
    return None


def slcn_python() -> str | None:
    """找到能跑引擎的 Python 解释器；找不到返回 None（此时应改用冻结引擎 exe）。"""
    env = os.environ.get("SLCN_PYTHON", "").strip()
    if env and os.path.exists(env):
        return env
    if not getattr(sys, "frozen", False):
        return sys.executable                               # 外壳自己就是 Python 跑的
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found
    return None


def frozen_engine_exe() -> str | None:
    """冻结引擎 exe（`SLCNEngine.exe`）：把八个 CLI 打成一个可执行文件，用户不必装 Python。"""
    env = os.environ.get("SLCN_ENGINE_EXE", "").strip()
    if env and os.path.exists(env):
        return env
    names = ("SLCNEngine.exe", "SLCNEngine")
    for base in (os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else _shell_dir(),
                 _shell_dir(),
                 slcn_engine() or ""):
        for n in names:
            p = os.path.join(base, n) if base else ""
            if p and os.path.exists(p):
                return os.path.abspath(p)
    return None


# ── 版本号（B-274，用户 m02462：侧栏底部只显示版本号）────────────────────
# 版本号**只有一份**：`Engine/slcn/__init__.py::__version__`（见该文件抬头）——
#   · 源码形态（开发 / 仓库里直接跑）：读 `<Engine>/slcn/__init__.py` 的那一行；
#   · 冻结形态（双击 exe）：没有源码可读，就调 `SLCNEngine.exe --version` 解析输出。
# 这里**不另写第二份版本号**；读不到就给「未知」，界面照常显示别的、不崩。
_VERSION_LINE_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)
_ENGINE_CLI_VERSION_RE = re.compile(r"SLCN 引擎 ([0-9][0-9A-Za-z.\-+]*)")
_ENGINE_VERSION_CACHE: str | None = None


def engine_version() -> str:
    """当前引擎版本号（`x.y.z`），结果缓存；取不到返回 `"未知"`（B-274）。

    缓存的意义：侧栏 label 只在启动时取一次，且冻结形态会起一次子进程；
    测试可把 `_ENGINE_VERSION_CACHE` 置 None 后重取。
    """
    global _ENGINE_VERSION_CACHE
    if _ENGINE_VERSION_CACHE is not None:
        return _ENGINE_VERSION_CACHE
    version = "未知"
    try:
        eng = slcn_engine()
        if eng:
            init_py = os.path.join(eng, "slcn", "__init__.py")
            try:
                with open(init_py, encoding="utf-8") as fh:
                    m = _VERSION_LINE_RE.search(fh.read())
                if m:
                    version = m.group(1)
            except OSError:
                pass
        if version == "未知":
            exe = frozen_engine_exe()
            if exe:
                proc = subprocess.run([exe, "--version"], capture_output=True,
                                      timeout=15, encoding="utf-8", errors="replace")
                m = _ENGINE_CLI_VERSION_RE.search((proc.stdout or "") + "\n"
                                                  + (proc.stderr or ""))
                if m:
                    version = m.group(1)
    except Exception:  # noqa: BLE001  版本号取不到不该让外壳崩（界面显示「版本 未知」）
        version = "未知"
    _ENGINE_VERSION_CACHE = version
    return version


# ── 引用库（第 132 条）：笔记仓 / 库文件 / 清单文件的定位 ────────────────
# 为什么由**外壳**算这些路径：引擎在发布形态里位于 `dist/`（PyInstaller 临时目录），
# 从那儿往上找不到仓库根；而引用库按 `SLCN_NOTES` 定位
# `<笔记仓>/bib_db/bibliography.jsonl`（`Engine/slcn/paths.py: notes_root`）。
# 这与 mac 侧 `sources-mac/Core/Support.swift: EngineLocator` 是同一套职责划分：
# 外壳负责「库在哪」，引擎负责「库怎么读、怎么判」—— 判据一行都不在这份文件里。
#
# ⚠️ 下面几个是**纯函数**（不碰 Tk、不读全局状态），路径可传合成目录做测试：
#    真机/CI 上找不到笔记仓时的行为（回落、不静默写错地方）必须能离开 Windows 验证。

NOTES_DIRNAME = "EditToolBox-notes"
BIBDB_DIRNAME = "bib_db"
BIBDB_FILENAME = "bibliography.jsonl"
#: 待裁决清单（`bibdb.py report` 的产物；「待裁决」页只读展示、不改库）
BIBDB_REPORT_FILES = ("CONFLICTS.md", "DUPLICATES.md", "duplicates.csv")


def looks_like_notes_repo(path: str) -> bool:
    """这个目录像不像**私有笔记仓**（有引用库 / 案例法 / 交接说明之一）。

    判据比 mac 侧多一条 `cases/`：引擎 `slcn/paths.py: notes_root()` 对显式
    `SLCN_NOTES` 的验收标准就是「里面有 cases/ 目录」—— 注入一个引擎会拒绝的路径，
    等于把引擎自己「向上找同级笔记仓」的兜底也关掉（环境变量优先，找不到就返回 None）。
    所以这里只认引擎真的会接受的目录：**不静默写错地方**比多认出几个候选重要。
    """
    if not path or not os.path.isdir(path):
        return False
    return (os.path.isdir(os.path.join(path, "cases"))
            or os.path.isfile(os.path.join(path, BIBDB_DIRNAME, BIBDB_FILENAME))
            or os.path.isfile(os.path.join(path, "HANDOFF.md")))


def _notes_start_dirs() -> list[str]:
    """自动探测的起点（按可信度）：exe 所在目录 → 外壳目录 → 引擎目录。"""
    starts: list[str] = []
    if getattr(sys, "frozen", False):
        starts.append(os.path.dirname(sys.executable))
    starts.append(_shell_dir())
    engine = slcn_engine()
    if engine:
        starts.append(engine)
    return [p for p in starts if p]


def detect_notes_repo(start_dir: str | None = None, *, max_up: int = 8) -> str | None:
    """**只做自动探测**：从起点逐级向上，找同级的 `EditToolBox-notes`（找不到返回 None）。

    镜像 mac `EngineLocator.detectedNotesRepo()`：每一层查两个位置 ——
    「父目录下的同名目录」（仓库并排的默认布局）与「当前目录下的同名目录」
    （exe 就放在笔记仓里面那种放法）。最多上 8 层，防止在根目录死循环。
    """
    starts = [start_dir] if start_dir else _notes_start_dirs()
    for start in starts:
        directory = os.path.abspath(os.path.expanduser(start))
        for _ in range(max_up):
            parent = os.path.dirname(directory)
            for candidate in (os.path.join(parent, NOTES_DIRNAME),
                              os.path.join(directory, NOTES_DIRNAME)):
                if looks_like_notes_repo(candidate):
                    return os.path.abspath(candidate)
            if parent == directory:          # 已经到根
                break
            directory = parent
    return None


def manual_notes_valid(manual: str) -> bool:
    """手动指定的笔记仓路径是否有效（空串由调用方解释成「没填」）。"""
    text = (manual or "").strip().strip('"')
    if not text:
        return False
    return looks_like_notes_repo(os.path.abspath(os.path.expanduser(text)))


def effective_notes_repo(manual: str = "", start_dir: str | None = None) -> str | None:
    """**当前生效的笔记仓**：手动指定（且真的有效）优先 → 自动探测。

    ⚠️ 无效的手动路径**不注入** `SLCN_NOTES`，而是回落自动探测 —— 界面同时显示红字。
    这比「照存照传」好：传一个引擎拒绝的路径会静默关掉库查找（编辑以为设置生效了，
    其实核实链路一次都没查到库）。
    """
    if manual_notes_valid(manual):
        return os.path.abspath(os.path.expanduser(manual.strip().strip('"')))
    return detect_notes_repo(start_dir)


def notes_dir(notes_root: str | None) -> str | None:
    """引用库目录 `<笔记仓>/bib_db`（清单文件都在这里）。"""
    if not notes_root:
        return None
    return os.path.join(notes_root, BIBDB_DIRNAME)


def bibdb_db_path(notes_root: str | None) -> str | None:
    """引用库文件路径 `<笔记仓>/bib_db/bibliography.jsonl`（位置由引擎定义，外壳不另立规矩）。"""
    directory = notes_dir(notes_root)
    return os.path.join(directory, BIBDB_FILENAME) if directory else None


def _file_info(path: str, name: str) -> dict:
    """一个文件的只读状态（存在 / 字节数 / 修改时间）；不抛异常（清单没生成是常态）。"""
    info = {"name": name, "path": path, "exists": False, "bytes": 0, "mtime": 0.0}
    try:
        stat = os.stat(path)
    except OSError:
        return info
    if os.path.isfile(path):
        info.update(exists=True, bytes=stat.st_size, mtime=stat.st_mtime)
    return info


def bibdb_report_files(notes_root: str | None) -> list[dict]:
    """「待裁决」页要显示的三个清单文件的当前状态（**只 stat，不跑引擎**）。"""
    directory = notes_dir(notes_root)
    if not directory:
        return []
    return [_file_info(os.path.join(directory, name), name) for name in BIBDB_REPORT_FILES]


def infer_issue_and_slug(raw_path: str) -> tuple[str, str]:
    """从 bib 路径推断「来源期」与「篇目录名」（认不出返回两个空串，**绝不猜**）。

    例：`…/2026年第2期(87)/02 Hai_Xing/reference.bib` → (`2026年第2期(87)`, `02 Hai_Xing`)。
    判据与引擎 `Engine/slcn/paths.py: ISSUE_RE`、`Engine/slcn/bibdb.py` 的作者目录
    正则**同一口径**（期 = `20xx年第…`；篇 = 两位数字 + 空格/下划线/短横）。
    为什么故意不猜：来源是库里的**审计凭据**，写错的来源比空来源更糟
    （mac 侧 `BibDbFeature.inferIssueAndSlug` 就是这条纪律，两侧逐字对齐）。
    """
    text = (raw_path or "").strip()
    if not text:
        return "", ""
    parts = text.replace("\\", "/").split("/")     # Windows 路径也按 / 拆（用户可能手填）
    for index, part in enumerate(parts):
        if not re.match(r"^\d{4}年第", part):
            continue
        slug = ""
        if index + 1 < len(parts) and re.match(r"^\d{2}[\s_-]", parts[index + 1]):
            slug = parts[index + 1]
        return part, slug
    return "", ""


def parse_json_lines(lines) -> dict | None:
    """从引擎输出里找**最后一段完整 JSON 对象**；没有返回 None。

    为什么不能直接 `json.loads("\\n".join(lines))`：`run_call` 把 stderr 并进了
    stdout，而 `bibdb.py --json` 的 JSON 是**多行缩进**格式（`indent=2`），前面可能
    还有告警行/中文提示。做法与 mac `BibDbFeature.jsonObject` 相同：从后往前找
    行首 `{`，把从那里到结尾的整段拿去解析，第一个解析成功的对象就是它。
    """
    if isinstance(lines, str):
        lines = lines.splitlines()
    rows = list(lines or [])
    for index in range(len(rows) - 1, -1, -1):
        if not rows[index].lstrip().startswith("{"):
            continue
        try:
            payload = json.loads("\n".join(rows[index:]))
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def output_tail(lines, *, count: int = 1) -> str:
    """引擎输出最后几条非空行（失败时给编辑看的原因；CLI 的中文报错都走 stderr）。"""
    if isinstance(lines, str):
        lines = lines.splitlines()
    rows = [line.strip() for line in (lines or []) if str(line).strip()]
    return " ／ ".join(rows[-count:]) if rows else "（引擎没有任何输出）"


def bibdb_tool() -> str | None:
    """引用库 CLI 的位置（给「库概况/设置」页显示；冻结形态显示 dispatcher exe）。"""
    if frozen_engine_exe():
        return frozen_engine_exe()
    engine = slcn_engine()
    if not engine:
        return None
    script = os.path.join(engine, "bibdb.py")
    return script if os.path.isfile(script) else None


# ── 拼命令行（纯函数，测试直接打这些）──────────────────────────────────

@dataclass
class EngineCall:
    """一次引擎调用的全部信息（**密钥不在这里**，见 `env_for_run`）。"""
    argv: list[str]
    entry: str
    label: str

    @property
    def display(self) -> str:
        return " ".join(self.argv)


def _base_cmd(entry: str) -> list[str] | None:
    exe = frozen_engine_exe()
    engine = slcn_engine()
    if exe:
        # 冻结形态：`SLCNEngine.exe convert.py --source …`（见 Engine/engine_main.py）
        return [exe, entry]
    if not engine:
        return None
    py = slcn_python()
    if not py:
        return None
    return [py, os.path.join(engine, entry)]


def _nonneg_int(value, flag: str) -> int:
    """预算/上限参数校验收口：必须是 ≥0 的 int（bool 不算；0 = 不限）。"""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{flag} 必须是 ≥0 的整数（0 = 不限）：{value!r}")
    return value


def _coop_reply_paths(value) -> list[str]:
    """规整 --coop-replies：str / list[str] → 非空路径列表（空列表/含空串 → ValueError）。

    批55 单路径与批56 多路径的**唯一**入口：单路径必须与批55 的 argv 逐字节相同
    （一个 `--coop-replies`），多路径按界面里已选的顺序输出**重复**的 flag。
    引擎批56 起 `--coop-replies` 是 `append`：给多个文件/目录 = 一次读到全部回填。
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        paths = [str(path).strip() for path in value]
        if not paths or any(not path for path in paths):
            raise ValueError("--coop-replies 至少要一个非空路径，且列表里不能有空字符串")
        return paths
    text = str(value).strip()
    return [text] if text else []


def convert_call(*, source: str, out: str, compile_pdf: bool = False,
                 clean: bool = False, verify_refs: bool = False,
                 source_pdf: bool = False, bib: str = "", metadata: str = "",
                 author: str = "", name: str = "",
                 vol: str = "", issue: str = "", year: str = "", pages: str = "",
                 received: str = "", editor: str = "", grants: str = "",
                 lang: str = "", extra: str = "",
                 verify_max: int | None = None, verify_plan: bool = False,
                 verify_all_refs: bool = False, no_bibdb: bool = False,
                 # ── 批62：入口收敛 B —— 界面「用 AI 处理参考文献」→ 内部强制 ⑪ ──
                 use_ai: bool = False,
                 # ── 批60：全文 AI 核查（直连 API；valueless flag，仅 ⑪ 生效）──
                 llm_fulltext: bool = False,
                 # ── 第五十四批（CLI）/ 第五十五批（GUI 接入）：两段式 AI 协同初排 ──
                 coop_taskbook_only: bool = False, coop_stage: str = "",
                 coop_replies: "str | list[str]" = "", coop_apply: bool = False,
                 coop_fulltext: str = "",
                 # ── 第五十五批：直接 API 通道的 token 预算闸（引擎默认 30000/1000000）──
                 ai_budget_tokens: int | None = None,
                 ai_batch_budget_tokens: int | None = None) -> EngineCall:
    """「初排转换」：`convert.py --source … --out …`（参数与 mac 界面一致）。

    ⚠️ `author` / `name` 不是可选项而是**某一类稿件的活路**：正文里读不出第一作者姓名时
    引擎会退出 2 并要求「请显式指定 --author "李雷" / --name Lei_Li」——
    界面若没有这两个输入框，这类稿（本刊真实的 LaTeX 投稿里就有）**整页做不了**
    （2026-10-17 审计实测：`2026年第5期(90)/03 Jie_Zhou/…/main.tex` 等 2 篇 rc=2）。
    出版信息（vol/issue/…）缺了不报错，但 PDF 页眉会是「xxxx 年第 x 期，1–10」占位。

    第 132 条的核实成本闸门（`verify_max` / `verify_plan` / `verify_all_refs` /
    `no_bibdb`）**默认全是 None/False = 一个参数都不加** —— 没碰过「引用库设置」的
    用户，命令行与接入引用库之前逐字节相同。它们只在**这次真的执行核实**
    （`verify_refs=True` 或 `use_ai=True`）时才进 argv：引擎对单独出现的一律只打
    「本次忽略」告警，外壳不该发这种噪声。`--verify-plan` 与 `--verify-refs`
    **二选一**（前者只算账就退出 0；两个都传会让日志说不清到底核不核）。

    批62「入口收敛 B」：初排页**不再暴露**独立「出文献核实任务书」勾选；`verify_refs`
    字段保留兼容旧设置/旧调用，但界面默认传 False。直连 AI 路径由 `use_ai=True`
    （= 勾「用 AI 处理参考文献（直连 API）」，显式主动勾选）驱动，本函数内部把它
    **等价**成 `verify_refs=True` —— argv/产物与批61 的 AI 路径逐字节相同
    （`--verify-refs` 由引擎内部隐含出任务书）。普通转换（两者皆 False）是批62 的
    行为变化：argv 不含 `--verify-refs`、默认产物不再有 `<stem>_文献核实.md`；
    两段式第一段照旧强制带 `--verify-refs`（任务书就是第一段的产物）。

    ── 两段式 AI 协同初排（CLI 批54 冻结；本函数是外壳侧唯一拼装点）──────────
    `coop_taskbook_only=True` = **第一段**：现有参数 + `--verify-refs --no-ai-merge
    --coop-taskbook-only`（`--coop-stage` 可选）。第一段**绝不**传 `--verify-plan`
    （引擎该分支在写任务书/状态前就退出 0，传了等于没跑），也不传任何
    `--llm-*`/`--search-refs`（外部 chat 通道，软件不调 API、不上传内容）。
    批62 起初排页不再暴露独立勾选，但第一段**照旧强制**带 `--verify-refs`：任务书就是
    这一段的全部产出（引擎自己也会把它当隐含开关）。scan-only 时界面不建 coop 参数（本函数不看）。

    批59「全文 AI 核查」：`coop_fulltext="context"|"full"` **仅第一段有效**，在
    `--coop-taskbook-only` 之后追加 `--coop-fulltext context|full`（引擎把正文/引用
    上下文附进任务书；软件仍不调 API、零上传）。**默认空串 = 一个字节都不加**
    （argv 与批58 逐字节相同）；非法取值 / 普通转换 / 第二段传入一律 ValueError
    —— 第二段从状态文件继承，绝不带这个 flag。

    批60「全文 AI 核查（直连 API）」：`llm_fulltext=True` 表示编辑本次在界面上勾了
    「全文 AI 核查（直连 API，会产生费用）」；**只有本次真的跑 ⑪ 直连通道**
    （非两段式 + `verify_refs`/`use_ai` 且 `verify_plan=False`）才在 `--verify-refs` 之后追加
    无取值的 `--llm-fulltext`。默认 False = 一个字节都不加（argv 与批59 逐字节相同）；
    普通转换 / 只算账 / 两段式（ph1/ph2）**都不加**（引擎在同一批里对这些非法组合
    exit 2，外壳在拼装点就**不制造**这些组合，不需要在这些路径传）。
    引擎 report-only：只写 `<stem>_AI全文复核.md`，不改 reference.bib/.tex；
    端点未配置时引擎 exit 2、零请求 —— 外壳不替引擎判定，日志另给一行提示。

    `coop_replies` 非空（str 或 list[str]）= **第二段**：只传最小集
    `--source/--out/--coop-replies… [--coop-stage] [--coop-apply] [--compile] [--clean]`，
    其余页面参数一律**不传** —— 引擎会从状态文件继承 ph1 的关键参数并做指纹校验
    （多传反而可能改变指纹、把正常续跑误拒）。
    批56 起 `--coop-replies` **可重复**：`list[str]` 按顺序逐项输出（引擎一次读入全部
    文件/目录，解决「只导入 json 丢了 bibtex」）。单路径仍是一个 flag，argv 与批55
    逐字节相同；空列表/列表含空串属调用方 bug → ValueError（不许静默丢回填）。
    `clean=True` 时第二段末尾带 `--clean`（引擎在编译后、promote 前清中间文件）。

    `ai_budget_tokens` / `ai_batch_budget_tokens`：只约束**直接 API 通道**（默认
    30000 / 1000000；0 = 不限）。**默认 None = 不传**；且 coop 两段都不传（外部 chat
    零费用）。「值 != 引擎默认才传」的取舍在 `settings.ai_budget_convert_args()`，
    本函数只负责在给了值时进 argv（非法值 ValueError，不让编辑器把错参数发给引擎）。
    """
    cmd = _base_cmd("convert.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    reply_paths = _coop_reply_paths(coop_replies)
    # 批62：`use_ai`（勾「用 AI 处理参考文献」）= 内部强制 ⑪ 直连通道 —— 与显式
    # `verify_refs` 完全等价（argv 逐字节相同）；普通转换两者皆 False → 不带 --verify-refs。
    verify = bool(verify_refs or use_ai)

    # ── 参数组合先校验，别把自相矛盾的命令行发给引擎 ──
    if coop_taskbook_only and reply_paths:
        raise ValueError("--coop-taskbook-only 与 --coop-replies 是两个阶段，不能同时给")
    if coop_apply and not reply_paths:
        raise ValueError("--coop-apply 只配合 --coop-replies（先回填回复，才谈得上应用建议）")
    if coop_stage and not (coop_taskbook_only or reply_paths):
        raise ValueError("--coop-stage 只配合两段式协同初排（第一段或第二段）")
    # 批59：--coop-fulltext 只在第一段（--coop-taskbook-only）成立；第二段不带（状态继承）。
    if coop_fulltext:
        if not coop_taskbook_only:
            raise ValueError("--coop-fulltext 只在两段式第一段（--coop-taskbook-only）有效："
                             "第二段由状态文件继承，普通转换不该带它")
        if coop_fulltext not in ("context", "full"):
            raise ValueError(f"--coop-fulltext 只接受 context / full：{coop_fulltext!r}")
    if ai_budget_tokens is not None:
        _nonneg_int(ai_budget_tokens, "--ai-budget-tokens")
    if ai_batch_budget_tokens is not None:
        _nonneg_int(ai_batch_budget_tokens, "--ai-batch-budget-tokens")

    cmd += ["--source", source, "--out", out]

    # ── 第二段：最小集，别的页面参数一个都不带（指纹/继承由引擎负责）──
    if reply_paths:
        for reply in reply_paths:
            cmd += ["--coop-replies", reply]
        if coop_stage:
            cmd += ["--coop-stage", coop_stage]
        if coop_apply:
            cmd.append("--coop-apply")
        if compile_pdf:
            cmd.append("--compile")
        if clean:
            cmd.append("--clean")
        return EngineCall(cmd, "convert.py", "AI 协同初排·第二段")

    for flag, on in (("--compile", compile_pdf), ("--clean", clean)):
        if on:
            cmd.append(flag)

    if coop_taskbook_only:
        # 第一段：强制出任务书；--verify-plan / --llm-* / --search-refs 一律不传。
        cmd.append("--verify-refs")
        if verify_max is not None:
            cmd += ["--verify-max", str(_nonneg_int(verify_max, "--verify-max"))]
        if verify_all_refs:
            cmd.append("--verify-all-refs")
        if no_bibdb:
            cmd.append("--no-bibdb")
        cmd += ["--no-ai-merge", "--coop-taskbook-only"]
        if coop_fulltext:                 # 批59：默认空串 → 一个字节都不加
            cmd += ["--coop-fulltext", coop_fulltext]
        if coop_stage:
            cmd += ["--coop-stage", coop_stage]
    elif verify:
        cmd.append("--verify-plan" if verify_plan else "--verify-refs")
        if verify_max is not None:
            cmd += ["--verify-max", str(_nonneg_int(verify_max, "--verify-max"))]
        if verify_all_refs:
            cmd.append("--verify-all-refs")
        # 查库默认就是开（引擎默认行为）；只有显式关闭才传 `--no-bibdb`，
        # 于是默认命令行里连这个开关都没有 —— 「默认零参数」的判据见 test_bridge (i)。
        if no_bibdb:
            cmd.append("--no-bibdb")
        # 批60：全文 AI 核查（直连 API）只在**本次真的跑 ⑪ 直连 AI** 时追加：
        # `--verify-refs` 且不是 `--verify-plan`（后者只算账就退出 0、不发任何请求）。
        # 两段式走上面 coop 分支、普通转换没有 ⑪ —— 都不带，自然满足「不影响」。
        if llm_fulltext and not verify_plan:
            cmd.append("--llm-fulltext")
    if source_pdf:
        cmd.append("--source-pdf")
    if bib:
        cmd += ["--bib", bib]
    if metadata:
        cmd += ["--metadata", metadata]
    for flag, val in (("--author", author), ("--name", name),
                      ("--vol", vol), ("--issue", issue), ("--year", year),
                      ("--pages", pages), ("--received", received),
                      ("--editor", editor), ("--grants", grants), ("--lang", lang)):
        if val:
            cmd += [flag, val]
    # 预算闸只约束直接 API 通道；协同两段是外部 chat 通道，一个字节都不传。
    if not coop_taskbook_only:
        if ai_budget_tokens is not None:
            cmd += ["--ai-budget-tokens", str(ai_budget_tokens)]
        if ai_batch_budget_tokens is not None:
            cmd += ["--ai-batch-budget-tokens", str(ai_batch_budget_tokens)]
    if extra.strip():
        cmd += extra.split()
    return EngineCall(cmd, "convert.py", "初排转换")


def checkbib_call(*, returned: str, original: str = "", tex: str = "",
                  merge_into: str = "", out: str = "") -> EngineCall:
    """「文献回读校验」：`checkbib.py --returned … [--merge-into …]`。"""
    cmd = _base_cmd("checkbib.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--returned", returned]
    if original:
        cmd += ["--original", original]
    if tex:
        cmd += ["--tex", tex]
    if merge_into:
        cmd += ["--merge-into", merge_into]
    if out:
        cmd += ["--out", out]
    return EngineCall(cmd, "checkbib.py", "文献回读校验")


def merge_call(*, folder: str, out: str = "", nest_outlines: bool = False,
               editors_page: bool = False, back_cover: bool = False,
               detect: bool = False) -> EngineCall:
    """「合并文件」：`mergepdf.py --folder …`（把原 Windows 版的合并规则换成引擎那份）。"""
    cmd = _base_cmd("mergepdf.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--folder", folder]
    if out:
        cmd += ["--out", out]
    for flag, on in (("--nest-outlines", nest_outlines), ("--editors-page", editors_page),
                     ("--back-cover", back_cover)):
        if on:
            cmd.append(flag)
    if detect:
        cmd.append("--detect")
    return EngineCall(cmd, "mergepdf.py", "合并文件")


def cleantemp_call(*, folder: str, dry_run: bool = False,
                   trash: bool = False, restore_trash: str = "",
                   mode: str = "aux", delete: bool = False,
                   empty_trash: bool = False) -> EngineCall:
    """「删除临时文件」：`cleantemp.py --folder …`（参数与 mac 界面一致 + 可回收模式）。

    `trash=True` → **移入 `<目录>/.toolbox-trash/<时间戳>/`**（可撤销）；界面用这条。
    `restore_trash=<回收目录>` → 撤销，把文件搬回原处（不覆盖已存在的文件）。
    B-281：`mode=aux|delivery|workspace` 与 mac `DeleteTempFeature.CleanMode` 逐项对齐；
    `delete=True`（delivery/workspace）= 引擎永久删；`empty_trash=True` = 清空回收站
    （不可撤销）。老调用（只传 `folder` / `dry_run` / `trash`）拼出的 argv **逐字节不变**。
    """
    cmd = _base_cmd("cleantemp.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    if restore_trash:
        cmd += ["--folder", folder, "--restore", restore_trash]
        return EngineCall(cmd, "cleantemp.py", "撤销清理")
    if empty_trash:
        cmd += ["--folder", folder, "--empty-trash"]
        if dry_run:
            cmd.append("--dry-run")
        return EngineCall(cmd, "cleantemp.py", "清空回收站")
    mode = _clean_logic.normalize_mode(mode)
    if mode == _clean_logic.DEFAULT_MODE:
        # ⚠️ 老路径：不传模式时 argv 必须与 B-211 之前逐字节一致（mac 外壳仍走这条）。
        cmd += ["--folder", folder]
        if dry_run:
            cmd.append("--dry-run")
        if trash:
            cmd.append("--trash")
        return EngineCall(cmd, "cleantemp.py", "删除临时文件")
    cmd += _clean_logic.cleantemp_args(folder, mode=mode, dry_run=dry_run, delete=delete)
    return EngineCall(cmd, "cleantemp.py", "删除临时文件")


def compileall_call(*, folder: str, only: str = "", dry_run: bool = False) -> EngineCall:
    """「编译全部」：`compileall.py --folder …`（xelatex → biber → xelatex ×2）。"""
    cmd = _base_cmd("compileall.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--folder", folder]
    if only.strip():
        cmd += ["--only", only.strip()]
    if dry_run:
        cmd.append("--dry-run")
    return EngineCall(cmd, "compileall.py", "编译全部")


def proofcheck_call(*, tex: str, fix: bool = False, lang: str = "") -> EngineCall:
    """「一校核查」：`proofcheck.py --tex …`（按《排校手册》《流程化经验书》逐条核查）。"""
    cmd = _base_cmd("proofcheck.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--tex", tex]
    if lang:
        cmd += ["--lang", lang]
    if fix:
        cmd.append("--fix")
    return EngineCall(cmd, "proofcheck.py", "一校核查")


def infocheck_call(*, folder: str, preview: bool = False) -> EngineCall:
    """「检查tex信息」：`infocheck.py --folder …`。

    `preview=True` 时加 `--preview`：引擎只把检查结果 JSON 打到 stdout、**不落文件**，
    界面用它填表格（口径与真跑一遍完全一致，不会出现"表里 10 篇、报告里 9 篇"）。
    """
    cmd = _base_cmd("infocheck.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--folder", folder]
    if preview:
        cmd.append("--preview")
    return EngineCall(cmd, "infocheck.py", "检查tex信息（预览）" if preview else "检查tex信息")


def toc_call(*, report: str, template: str = "", text_only: bool = False,
             result_json: str = "") -> EngineCall:
    """「生成目录」：默认**一键最终交付**（`--place --compile`，落位/编译逻辑在引擎里）。

    高级开关「仅生成 tex（不落位）」打开时只传 `--report`（旧行为：只写时间戳 .tex）。
    `result_json` 是界面给的临时路径：引擎把 4 个固定名路径/模板来源/页数写成 JSON，
    跑完由 `read_toc_result()` 读回来显示 —— 外壳不猜模板来源、不拼产物路径。
    """
    cmd = _base_cmd("toc.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--report", report]
    if template.strip():
        cmd += ["--template", template.strip()]
    if not text_only:
        cmd += ["--place", "--compile"]
        if result_json:
            cmd += ["--result-json", result_json]
    return EngineCall(cmd, "toc.py", "生成目录")


def toc_preview_call(*, report: str) -> EngineCall:
    """目录预览：`toc.py --report … --preview`（篇目清单 + 模板来源，JSON，不落文件）。"""
    cmd = _base_cmd("toc.py")
    if cmd is None:
        raise RuntimeError("找不到引擎：请先在「设置」里指定 Engine 目录或 SLCNEngine.exe")
    cmd += ["--report", report, "--preview"]
    return EngineCall(cmd, "toc.py", "目录预览")


def read_toc_result(path: str) -> dict:
    """读 `toc.py --result-json` 写的结果；文件不在/不是 JSON → `{}`（不猜、不炸）。"""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def toc_result_lines(payload: dict) -> list[str]:
    """把生成目录的结果 JSON 翻成结果区的行（**纯函数**，桥梁测试直接断言）。

    只显示引擎给的事实：4 个固定名路径 / 模板来源 / 页数 / 栏目 / 人工核对项 / 警告；
    模板来源**不在这里判断**（引擎说「上一期」就是上一期）。
    """
    if not isinstance(payload, dict):
        return []
    lines: list[str] = []
    source = str(payload.get("templateSource") or "").strip()
    if source:
        lines.append(f"模板来源：{source}")
    note = str(payload.get("templateNote") or "").strip()
    if note:
        lines.append(note)
    placed = payload.get("placed")
    if isinstance(placed, dict):
        for label, key in (("中文目录 PDF", "zhPdf"), ("中文目录 tex", "zhTex"),
                           ("英文目录 PDF", "enPdf"), ("英文目录 tex", "enTex")):
            value = str(placed.get(key) or "").strip()
            if value:
                lines.append(f"{label}：{value}")
    pages = payload.get("pages")
    if isinstance(pages, dict):
        parts: list[str] = []
        if isinstance(pages.get("zh"), int):
            parts.append(f"中文 {pages['zh']} 页")
        if isinstance(pages.get("en"), int):
            parts.append(f"英文 {pages['en']} 页")
        if parts:
            lines.append("页数：" + "／".join(parts))
    column = payload.get("column")
    if isinstance(column, dict) and column.get("enabled") is True:
        title = str(column.get("title") or "").strip()
        lines.append("栏目：" + (title or "已启用"))
    edits = payload.get("manualEdits")
    if isinstance(edits, list):
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            kind = str(edit.get("kind") or "").strip()
            text = "需人工核对" + (f"（{kind}）" if kind else "")
            article = str(edit.get("article") or "").strip()
            if article:
                text += f"：{article}"
            note = str(edit.get("note") or "").strip()
            if note:
                text += f" — {note}"
            lines.append(text)
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            text = str(warning).strip()
            if text:
                lines.append(f"⚠️ {text}")
    return lines


# ── 引用库 CLI（第 132 条）：`bibdb.py` 的薄参数拼装 ────────────────────────
# 纪律与 mac `sources-mac/Features/BibDbFeature.swift` 相同：**外壳不判定**
# 「什么算已核实 / 冲突怎么留档 / 题名相似度多少算命中」—— 那些判据只在
# `Engine/slcn/bibdb.py`。这里只把界面取值拼成 argv，再把 `--json` 读回来显示。

def _bibdb_cmd() -> list[str]:
    cmd = _base_cmd("bibdb.py")
    if cmd is None:
        raise RuntimeError("找不到引用库脚本 Engine/bibdb.py：请先在「设置」里指定 Engine 目录，"
                           "或确认 SLCNEngine.exe 已随包一起安装。")
    return cmd


def bibdb_stats_call(db: str = "") -> EngineCall:
    """库概况：`bibdb.py stats --json`（条数/期数/最近更新/冲突数/库大小）。"""
    cmd = _bibdb_cmd() + ["stats", "--json"]
    if db:
        cmd += ["--db", db]
    return EngineCall(cmd, "bibdb.py", "引用库 · 库概况")


def bibdb_lint_call(db: str = "") -> EngineCall:
    """库体检：`bibdb.py lint --json`（退出码 1 = 库有 error，**不是程序出错**）。"""
    cmd = _bibdb_cmd() + ["lint", "--json"]
    if db:
        cmd += ["--db", db]
    return EngineCall(cmd, "bibdb.py", "引用库 · 体检")


#: 查库模式 → CLI 参数（与 `bibdb.py query` 的三个互斥参数一一对应）
QUERY_FLAGS = {"key": "--key", "doi": "--doi", "title": "--title"}


def bibdb_query_call(mode: str, text: str, db: str = "", limit: int = 20) -> EngineCall:
    """查库：`bibdb.py query --key/--doi/--title … --bibtex --json`。

    `--limit` 只对题名模糊查询有意义（key/DOI 是精确查询，传了反而误导）。
    """
    flag = QUERY_FLAGS.get(mode)
    if flag is None:
        raise ValueError(f"未知的查库方式：{mode!r}（只认 key/doi/title）")
    cmd = _bibdb_cmd() + ["query", flag, text, "--bibtex", "--json"]
    if mode == "title":
        cmd += ["--limit", str(max(1, int(limit)))]
    if db:
        cmd += ["--db", db]
    return EngineCall(cmd, "bibdb.py", f"引用库 · 查库（{mode}）")


#: 高级检索字段（**不含 key**：key 是基本检索的精确入口；口径见 b259 spec §2）
ADVANCED_QUERY_FIELDS = ("author", "editor", "title", "booktitle", "journal",
                         "publisher", "year", "doi", "alias", "any")
#: 高级检索匹配方式（与 `bibdb.py query-advanced --mode` 一一对应）
ADVANCED_QUERY_MODES = ("contains", "prefix", "exact", "fuzzy")
#: 高级检索排序（与 `bibdb.py query-advanced --sort` 一一对应）
ADVANCED_QUERY_SORTS = ("relevance", "year", "author")


def bibdb_query_advanced_call(terms: list[dict], sort: str = "relevance", limit: int = 20,
                              min_score: float = 0.75, strict: bool = False,
                              db: str = "") -> EngineCall:
    """高级检索：`bibdb.py query-advanced --字段 值 --mode 字段=模式 … --bibtex --json`。

    每个 term 都**显式**带 `--mode 字段=模式`（不靠 CLI 默认值），两壳发出去的参数
    才逐字一致、桥测试才能钉死。`strict=True` 用引擎的 0.90 门槛，否则显式传
    `--min-score`（默认 0.75）。判据全在引擎，这里只拼参数。
    """
    if not isinstance(terms, list) or not terms:
        raise ValueError("高级检索至少要填一行检索条件。")
    if sort not in ADVANCED_QUERY_SORTS:
        raise ValueError(f"未知的排序方式：{sort!r}（只认 {'/'.join(ADVANCED_QUERY_SORTS)}）")
    if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 200):
        raise ValueError(f"limit 必须是 1–200 的整数：{limit!r}")
    if (isinstance(min_score, bool) or not isinstance(min_score, (int, float))
            or not (0 <= min_score <= 1)):
        raise ValueError(f"min_score 必须是 0–1 之间的数：{min_score!r}")

    cmd = _bibdb_cmd() + ["query-advanced"]
    for index, term in enumerate(terms, 1):
        if not isinstance(term, dict):
            raise ValueError(f"第 {index} 行检索条件必须是字典（field/mode/value）：{term!r}")
        field = term.get("field")
        mode = term.get("mode")
        value = term.get("value")
        if field not in ADVANCED_QUERY_FIELDS:
            raise ValueError(f"第 {index} 行检索字段未知：{field!r}"
                             f"（只认 {'/'.join(ADVANCED_QUERY_FIELDS)}）")
        if mode not in ADVANCED_QUERY_MODES:
            raise ValueError(f"第 {index} 行匹配方式未知：{mode!r}"
                             f"（只认 {'/'.join(ADVANCED_QUERY_MODES)}）")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"第 {index} 行检索条件没有填值。")
        cmd += [f"--{field}", value]
        cmd += ["--mode", f"{field}={mode}"]
    cmd += ["--limit", str(limit)]
    if strict:
        cmd.append("--strict")
    else:
        cmd.append(f"--min-score={min_score}")
    cmd += ["--bibtex", "--json"]
    if db:
        cmd += ["--db", db]
    return EngineCall(cmd, "bibdb.py", "引用库 · 高级检索")


def bibdb_import_bib_call(*, bib: str, issue: str = "", slug: str = "",
                          dry_run: bool = True, db: str = "",
                          verified: bool = True) -> EngineCall:
    """入藏单篇 `.bib`：`bibdb.py import-bib --bib … [--issue … --slug …] --verified …`。

    `dry_run=True` = 「预览」按钮：引擎照常解析/查重/算冲突，但**库一个字节都不写**
    （库文件不存在也不会新建）。只有「确认入藏」才用 `dry_run=False` 真正写库。
    """
    cmd = _bibdb_cmd() + ["import-bib", "--bib", bib]
    if verified:
        cmd.append("--verified")
    if issue:
        cmd += ["--issue", issue]
    if slug:
        cmd += ["--slug", slug]
    cmd.append("--json")
    if db:
        cmd += ["--db", db]
    if dry_run:
        cmd.append("--dry-run")
    return EngineCall(cmd, "bibdb.py", "引用库 · 入藏预览" if dry_run else "引用库 · 入藏")


def bibdb_import_dir_call(*, root: str, dry_run: bool = True, db: str = "") -> EngineCall:
    """入藏整期目录：`bibdb.py import-dir --root … [--dry-run] --json`。

    `root` 可以是一期目录（只导入这一期）或稿件库根（导入全部期）；
    哪份 bib 算成品 bib、成品期算不算 verified，判据在引擎（与种子导入同一份）。
    """
    cmd = _bibdb_cmd() + ["import-dir", "--root", root, "--json"]
    if db:
        cmd += ["--db", db]
    if dry_run:
        cmd.append("--dry-run")
    return EngineCall(cmd, "bibdb.py", "引用库 · 入藏预览" if dry_run else "引用库 · 入藏")


def bibdb_report_call(*, out_dir: str, db: str = "") -> EngineCall:
    """重新生成待裁决清单：`bibdb.py report --json --out <笔记仓>/bib_db`。

    只写 `CONFLICTS.md` / `DUPLICATES.md` / `duplicates.csv` 三个文件，
    **不改库本体**（引擎保证幂等：同一份库重复生成逐字节相同）。
    """
    cmd = _bibdb_cmd() + ["report", "--json"]
    if db:
        cmd += ["--db", db]
    if out_dir:
        cmd += ["--out", out_dir]
    return EngineCall(cmd, "bibdb.py", "引用库 · 生成待裁决清单")


# ── 待裁决 / 写库（B-282a）：与 mac `BibDbFeature` 同一组 argv ──────────────
# 判据全部在引擎：这里只拼参数、读 `--json`。写库一律带 `--expect-db-sha256`
# （列表加载时的库 sha），过期时引擎退出 2、库一个字节都不动。
# 注意：**不传 `--backup`** —— 写库成功后由 `bibdb_git.py` 只提交库文件。

RESOLVE_ACTIONS = ("keep", "adopt", "defer", "replace")


def bibdb_conflicts_call(*, db: str = "", pending_only: bool = False) -> EngineCall:
    """待裁决列表：`bibdb.py conflicts --json [--pending-only]`。"""
    cmd = _bibdb_cmd() + ["conflicts"]
    if pending_only:
        cmd.append("--pending-only")
    cmd.append("--json")
    if db:
        cmd += ["--db", db]
    return EngineCall(cmd, "bibdb.py", "引用库 · 待裁决列表")


def bibdb_resolve_call(*, key: str, action: str, incoming_sha256: str = "",
                       all_fields: bool = False, note: str = "",
                       expect_db_sha256: str = "", dry_run: bool = False,
                       db: str = "") -> EngineCall:
    """逐条裁决：`bibdb.py resolve --key … --action keep|adopt|defer …`。

    `incoming_sha256` 非空时传 `--incoming-sha256`（mac 对 keep/adopt/defer 都这样）；
    为空时 keep/defer 传 `--all-fields`。adopt/replace 必须有指纹（界面已先置灰）。
    """
    action = str(action or "").strip()
    if action not in RESOLVE_ACTIONS:
        raise ValueError(f"未知的裁决动作：{action!r}（只认 {'/'.join(RESOLVE_ACTIONS)}）")
    name = str(key or "").strip()
    if not name:
        raise ValueError("缺少库内记录 key（不能裁决）。")
    cmd = _bibdb_cmd() + ["resolve", "--key", name, "--action", action]
    fingerprint = str(incoming_sha256 or "").strip()
    if fingerprint:
        cmd += ["--incoming-sha256", fingerprint]
    elif all_fields:
        cmd.append("--all-fields")
    note_text = str(note or "").strip()
    if note_text:
        cmd += ["--note", note_text]
    expect = str(expect_db_sha256 or "").strip()
    if expect:
        cmd += ["--expect-db-sha256", expect]
    cmd.append("--json")
    if db:
        cmd += ["--db", db]
    if dry_run:
        cmd.append("--dry-run")
    return EngineCall(cmd, "bibdb.py", f"引用库 · 裁决（{action}）")


def bibdb_replace_call(*, key: str, bib: str, incoming_sha256: str, note: str = "",
                       expect_db_sha256: str = "", dry_run: bool = False,
                       db: str = "") -> EngineCall:
    """编辑并替换：`bibdb.py resolve --key … --action replace --incoming-sha256 … --bib …`。"""
    name = str(key or "").strip()
    bib_path = str(bib or "").strip()
    fingerprint = str(incoming_sha256 or "").strip()
    if not name:
        raise ValueError("缺少库内记录 key（不能替换）。")
    if not bib_path:
        raise ValueError("缺少替换用的 .bib 文件路径。")
    if not fingerprint:
        raise ValueError("这条冲突没有指纹（incoming sha256 为空）：请改用「全部采用」或"
                         "重新生成清单后再试。")
    cmd = _bibdb_cmd() + ["resolve", "--key", name, "--action", "replace",
                          "--incoming-sha256", fingerprint, "--bib", bib_path]
    note_text = str(note or "").strip()
    if note_text:
        cmd += ["--note", note_text]
    expect = str(expect_db_sha256 or "").strip()
    if expect:
        cmd += ["--expect-db-sha256", expect]
    cmd.append("--json")
    if db:
        cmd += ["--db", db]
    if dry_run:
        cmd.append("--dry-run")
    return EngineCall(cmd, "bibdb.py", "引用库 · 替换预览" if dry_run else "引用库 · 替换")


def bibdb_adopt_all_call(*, note: str = "", expect_db_sha256: str = "",
                         dry_run: bool = False, db: str = "") -> EngineCall:
    """全部采用：`bibdb.py resolve --adopt-all-pending …`（单次原子写，一次提交）。"""
    cmd = _bibdb_cmd() + ["resolve", "--adopt-all-pending"]
    note_text = str(note or "").strip()
    if note_text:
        cmd += ["--note", note_text]
    expect = str(expect_db_sha256 or "").strip()
    if expect:
        cmd += ["--expect-db-sha256", expect]
    cmd.append("--json")
    if db:
        cmd += ["--db", db]
    if dry_run:
        cmd.append("--dry-run")
    return EngineCall(cmd, "bibdb.py", "引用库 · 全部采用预览" if dry_run else "引用库 · 全部采用")


# ── 环境自检（B-283）：`envcheck.py` 的薄参数拼装 ─────────────────────────
# 纪律与 `bibdb.py` 相同：**外壳不判定**「什么算缺依赖 / 退出码几」——
# 那些判据只在 `Engine/envcheck.py`。这里只把界面取值拼成 argv，再把 `--json` 读回来显示。
# 检查结果**不拦任何功能**（spec §4）：没装 LibreOffice 也能转换纯 LaTeX 稿。

def _envcheck_cmd() -> list[str]:
    cmd = _base_cmd("envcheck.py")
    if cmd is None:
        raise RuntimeError("找不到环境自检脚本 Engine/envcheck.py：请先在「设置」里指定 Engine 目录，"
                           "或确认 SLCNEngine.exe 已随包一起安装。")
    return cmd


def envcheck_call(*, json_output: bool = True, out_dir: str = "",
                  profile: str = "") -> EngineCall:
    """一次环境自检：`envcheck.py --json [--out-dir DIR] [--profile auto|exe|source]`。

    默认带 `--json`：界面要解析它排版（人类可读的那份是给命令行用户的）。
    `out_dir` 给了就顺带检查该目录可写，并落一份 `envcheck.json` 进去（诊断包用）；
    `profile` 留空 = 由引擎自动判断发布包/源码形态。
    """
    cmd = _envcheck_cmd()
    if json_output:
        cmd.append("--json")
    if out_dir:
        cmd += ["--out-dir", out_dir]
    if profile:
        cmd += ["--profile", profile]
    return EngineCall(cmd, "envcheck.py", "环境自检")


# ── 失败诊断（B-285b）：`diagnose.py` 的薄接线 ───────────────────────────
# 判据（17 类 / 标题 / 下一步 / 置信度）只在 `Engine/slcn/diagnose.py`，外壳只做四件事：
#   ① 判定**要不要出卡**（红：转换 exit 2、信息检查 exit 2、全编译 exit 1、启动失败 <0；
#      中性：一校核查 / 文献回读 exit 1；0 一律不出卡）；
#   ② 把本次运行的日志落进应用缓存（`%LOCALAPPDATA%/编辑部工具箱/logs`，留 20 份）；
#   ③ 调 `diagnose.py --json` 拿 13 键 JSON（旧引擎没有它 → 回退，不阻断）；
#   ④ 拼一份**零上传**的本地诊断包文本（≤16KB，日志尾部已脱敏）。
# 与 mac 侧 `sources-mac/Core/Diagnose.swift` 同一口径；两平台必须给出同样的类别/原因/下一步。

DIAGNOSIS_ENTRIES = ("convert.py", "proofcheck.py", "infocheck.py",
                     "compileall.py", "checkbib.py")
DIAGNOSIS_RED_EXITS = {"convert.py": (2,), "infocheck.py": (2,), "compileall.py": (1,)}
DIAGNOSIS_NEUTRAL_EXITS = {"proofcheck.py": (1,), "checkbib.py": (1,)}
DIAGNOSIS_PACK_LIMIT = 16 * 1024
DIAGNOSIS_LOG_KEEP = 20

#: 与 `Engine/slcn/diagnose.py: _SECRET_PATTERNS` 同一口径（两平台各一份，测试逐条对拍）。
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{3,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|slcn_llm_key)"
               r"\s*[=:]\s*[^\s,，;；]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
)


def mask_secrets(text: str) -> str:
    """把日志/诊断包里的密钥字样替换成 `***`（确定性、幂等）。"""
    out = text or ""
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("***", out)
    return out


def diagnosis_level(entry: str, code: int) -> str | None:
    """要不要出诊断卡、出哪一种。`None` = 不出卡（成功 / 非失败口径）。"""
    if entry not in DIAGNOSIS_ENTRIES or code == 0:
        return None
    if code < 0:
        return "error"          # 启动失败 / 被中止：一律红卡
    if code in DIAGNOSIS_RED_EXITS.get(entry, ()):
        return "error"
    if code in DIAGNOSIS_NEUTRAL_EXITS.get(entry, ()):
        return "neutral"
    return None                 # 未列出的退出码：不出卡（维持旧行为）


def app_logs_dir() -> str:
    """应用缓存的日志目录（D-05）：Windows `%LOCALAPPDATA%/编辑部工具箱/logs`。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, "编辑部工具箱", "logs")
    os.makedirs(path, exist_ok=True)
    return path


def _log_stamp() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def save_diagnosis_log(entry: str, lines, *, directory: str | None = None,
                       keep: int = DIAGNOSIS_LOG_KEEP, stamp: str | None = None) -> str:
    """把一次运行的输出落进应用缓存（已脱敏），并只留最近 `keep` 份。返回文件路径。"""
    folder = directory or app_logs_dir()
    os.makedirs(folder, exist_ok=True)
    stem = entry[:-3] if entry.endswith(".py") else entry
    name = f"{stamp or _log_stamp()}-{stem}.log"
    path = os.path.join(folder, name)
    body = "".join(str(line).rstrip("\n") + "\n" for line in lines)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(mask_secrets(body))
    prune_diagnosis_logs(folder, keep=keep)
    return path


def prune_diagnosis_logs(directory: str, *, keep: int = DIAGNOSIS_LOG_KEEP) -> list[str]:
    """日志轮转：按文件名的时间戳前缀排序，只留最近 `keep` 份。返回删掉的文件名。"""
    try:
        names = sorted(n for n in os.listdir(directory)
                       if n.endswith(".log") and re.match(r"^\d{8}-\d{6}-", n))
    except OSError:
        return []
    removed = []
    victims = names[:-keep] if keep > 0 else names
    for name in victims:
        try:
            os.remove(os.path.join(directory, name))
            removed.append(name)
        except OSError:
            pass
    return removed


def _diagnose_cmd() -> list[str]:
    cmd = _base_cmd("diagnose.py")
    if cmd is None:
        raise RuntimeError("找不到失败诊断脚本 Engine/diagnose.py（旧版引擎）："
                           "已回退为「退出码 + 日志尾部」，不影响其他功能。")
    return cmd


def diagnose_call(entry: str, exit_code: int, log_path: str) -> EngineCall:
    """一次失败诊断：`diagnose.py --entry X --exit-code N --log PATH --json`。"""
    cmd = _diagnose_cmd()
    cmd += ["--entry", entry, "--exit-code", str(int(exit_code)), "--log", log_path, "--json"]
    return EngineCall(cmd, "diagnose.py", "失败诊断")


def parse_diagnosis(text: str) -> dict | None:
    """解析 diagnose.py 的 `--json` 输出；不是合法 diagnose.v1 就返回 None（走回退）。"""
    raw = (text or "").strip()
    start = raw.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(raw[start:])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != "diagnose.v1":
        return None
    if not all(data.get(k) for k in ("entry", "category", "title", "reason")):
        return None
    if not isinstance(data.get("next_steps"), list):
        return None
    return data


def diagnosis_doc_path(engine_dir: str | None = None) -> str | None:
    """「查看文档」按钮打开哪一份：契约 → 使用说明 → FAQ。"""
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    candidates = []
    if engine_dir:
        candidates.append(os.path.join(engine_dir, "docs", "ENGINE_API.md"))
    candidates += [
        os.path.join(repo, "使用说明.md"),
        os.path.join(repo, "docs", "reference", "faq.md"),
        os.path.join(repo, "docs", "reference", "known-limitations.md"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def diagnosis_pack(*, entry: str, exit_code: int, level: str,
                   report: dict | None = None, log_tail=(),
                   engine_version: str = "", generated_at: str = "",
                   envcheck_json: str = "") -> str:
    """本地诊断包文本（零上传）：类别/原因/下一步 + 脱敏日志尾部；≤16KB。"""
    from datetime import datetime
    if not generated_at:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = [
        "编辑部工具箱 · 失败诊断包（本地生成，不会上传）",
        f"生成时间：{generated_at}",
        f"入口：{entry}",
        f"退出码：{exit_code}",
        f"卡片：{'红色（失败）' if level == 'error' else '中性（有结果）'}",
    ]
    if report:
        head += [
            f"类别：{report.get('category', '')}",
            f"标题：{report.get('title', '')}",
            f"原因：{report.get('reason', '')}",
            f"置信度：{report.get('confidence', '')}",
            f"引擎版本：{report.get('engine_version') or engine_version or ''}",
            "下一步：",
        ]
        for i, step in enumerate(report.get("next_steps") or [], 1):
            head.append(f"  {i}. {step}")
        head.append(f"依据文档：{report.get('doc_anchor', '')}")
    else:
        head += [
            "类别：（诊断器不可用：旧版引擎没有 diagnose.py，或本次诊断本身失败）",
            "标题：本次运行没有成功",
            "原因：只拿到退出码。先看下面的日志尾部；升级到带 diagnose.py 的版本后会有完整诊断。",
            "下一步：",
            "  1. 看「日志尾部」最后几行的报错，按提示修正输入或环境后重跑。",
            "  2. 若反复失败，点「复制诊断包」把这份文本发给维护者；"
            "日志文件在应用缓存目录（「打开日志」按钮可直接打开）。",
            "  3. 需要完整契约时看 Engine/docs/ENGINE_API.md §十一。",
            "引擎版本：" + (engine_version or ""),
        ]
    footer = [
        "说明：本文件只含退出码、诊断结论与脱敏后的日志尾部；不含稿件正文、"
        "作者信息、bib 条目或 API Key；不会上传任何地方。",
    ]
    if envcheck_json:
        footer.insert(0, "环境自检（节选）：\n" + mask_secrets(envcheck_json)[:2000])
    else:
        footer.insert(0, "环境自检：见「设置 → 环境检查」；诊断包不含 envcheck.json 原文。")
    evidence = [mask_secrets(str(line)) for line in log_tail if str(line).strip()]

    def render() -> str:
        body = ["证据（日志末尾，已脱敏）："] + (evidence or ["（本次没有捕获到日志行）"])
        return "\n".join(head + [""] + body + footer) + "\n"

    text = render()
    # 硬上限：超了从证据里去掉最早的行（保留结论与最后几行）。
    while len(text.encode("utf-8")) > DIAGNOSIS_PACK_LIMIT and len(evidence) > 1:
        evidence = evidence[1:]
        text = render()
    data = text.encode("utf-8")
    if len(data) > DIAGNOSIS_PACK_LIMIT:
        text = data[:DIAGNOSIS_PACK_LIMIT - 64].decode("utf-8", "ignore") + "\n…（已截断）\n"
    return text


# ── 环境变量与执行 ───────────────────────────────────────────────────────

def env_for_run(api: dict | None = None, engine: str | None = None,
                notes: str = "") -> dict:
    """子进程的环境：UTF-8、无缓冲、vendor 依赖，以及**只走环境变量的密钥**。

    ⚠️ 密钥**绝不进 argv**：命令行参数在任务管理器 / `wmic process` 里谁都能看见。

    第 132 条：还把**私有笔记仓路径**注入 `SLCN_NOTES`（引擎按它定位
    `<笔记仓>/bib_db/bibliography.jsonl`；核实链路与 `bibdb.py` 都认这一个变量）。
    顺序与 mac `Shell.environment()` 一致：**环境里已经显式设了就不覆盖**（用户/CI
    优先），否则 `notes`（界面「引用库设置」里的手动指定，无效时自动回落探测）优先，
    最后才是从程序/exe 位置向上自动探测。找不到就不设 —— 引擎会优雅跳过查库。
    """
    env = dict(os.environ)
    engine = engine or slcn_engine()
    if engine:
        vendor = os.path.join(engine, "vendor")
        env["PYTHONPATH"] = vendor + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"          # 否则日志按块缓冲，界面看着像卡死
    env["PYTHONIOENCODING"] = "utf-8"      # Windows 控制台默认 cp936，中文会乱码/报错
    api = api or {}
    for key, var in (("base", "SLCN_LLM_BASE"), ("key", "SLCN_LLM_KEY"),
                     ("model", "SLCN_LLM_MODEL"), ("protocol", "SLCN_LLM_PROTOCOL"),
                     ("searchTool", "SLCN_LLM_SEARCH_TOOL"),
                     ("responsesPath", "SLCN_LLM_RESPONSES_PATH")):
        val = str(api.get(key, "") or "").strip()
        if val:
            env[var] = val
    if api.get("thinking"):
        env["SLCN_LLM_THINKING"] = str(api["thinking"])
    if api.get("maxTokens"):
        env["SLCN_LLM_MAX_TOKENS"] = str(api["maxTokens"])
    if api.get("webSearch"):
        env["SLCN_LLM_WEB_SEARCH"] = "1"
    # P2（第 72 条起）：检索证据开关 → 引擎 `SLCN_SEARCH_REFS=1`（默认关）。
    # 走环境变量而非 argv：Windows 的 `wmic process` 同机可见，与密钥同一条纪律。
    if api.get("searchRefs"):
        env["SLCN_SEARCH_REFS"] = "1"
    # 第 132 条：笔记仓路径（引用库位置）。空/无效 → 回落自动探测；都没有就不设。
    if "SLCN_NOTES" not in env:
        notes_root = effective_notes_repo(notes)
        if notes_root:
            env["SLCN_NOTES"] = notes_root
    return env


def kill_tree(proc: subprocess.Popen) -> None:
    """停任务：杀**整棵**进程树，不只是直接子进程。

    为什么非这样不可（2026-10-17 审计实测）：直接子进程在发布形态里只是 PyInstaller
    的 bootloader，真正的引擎与它起的 `xelatex` 都是**孙进程** —— 只 `terminate()`
    会出现「界面已经显示不忙、TeX 还在写产物目录」，编辑再点一次运行就有两组 TeX
    同写一个目录。POSIX 上实测：按下停止后 xelatex 又跑了 ~12 秒并写出了 PDF。

    Windows 用 `taskkill /T /F`（连子孙一起），POSIX 用 `killpg`（`run_call` 里
    已 `start_new_session=True`）；两条路都失败才退回 `terminate()`。
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
        except OSError:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            return
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.terminate()
    except OSError:
        pass


def run_call(call: EngineCall, *, on_line=None, cwd: str | None = None,
             api: dict | None = None, on_proc=None, notes: str = "") -> int:
    """跑一次引擎调用，逐行回调日志；返回退出码（**只看 0 / 非 0**）。

    `stderr` 合并进 `stdout`：引擎的致命错误走 stderr，编辑在同一个日志框里才看得见。
    `on_proc(proc)` 在进程刚起来时回调 —— 界面拿它做「停止」按钮（`proc.terminate()`）。
    `notes` = 界面「引用库设置」里的手动笔记仓路径（见 `env_for_run`；第 132 条）。
    """
    env = env_for_run(api, notes=notes)
    proc = subprocess.Popen(
        call.argv, cwd=cwd or None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),   # Windows：别弹黑框
        # POSIX：自成进程组，停止时能整组杀（见 kill_tree）
        start_new_session=(os.name != "nt"),
    )
    if on_proc:
        on_proc(proc)
    assert proc.stdout is not None
    for line in proc.stdout:                       # 逐行读 = 实时进度（第 82 条）
        if on_line:
            on_line(line.rstrip("\n"))
    proc.wait()
    return proc.returncode


#: 退出码 1 在引擎里**常常是"有结果"而不是失败**（`Engine/docs/INVARIANTS.md` 三.2）：
#: checkbib 并回 = 已并入 ≥1 条、compileall = 有失败、proofcheck = 有命中、
#: toc = 只生成一份 / 「总第 ? 期」无法确定。外壳**不许**把它显示成错误 ——
#: 这条口径与 mac 各页逐字对齐（`CheckBibFeature.swift` 等）。
_RESULT_1 = {
    "checkbib.py": "❗ 体检发现问题 / 已并入，请按报告逐条处理（不是程序出错）",
    "proofcheck.py": "❗ 有命中项，请按报告逐条处理（不是程序出错）",
    "compileall.py": "⚠ 有文章编译失败（每篇的错误摘要见上面的日志）",
    "toc.py": "⚠ 已完成，但有需要人工核对的地方（见上面的日志）",
}
_RESULT_1_DEFAULT = "❗ 完成，但有需要处理的结果（退出码 1，见上面的日志）"


#: 契约行里可能「像路径起点」的位置：Unix 的 `/`，或 Windows 的 `C:\\` / `C:/`。
#: ⚠️ **不能靠"路径字符集"去截断**：本项目的期目录名里就带半角括号
#: （`2026年第5期(90)`），字符集一排除括号就把路径截断了（第一版就这么错的）。
_PATH_START = re.compile(r"(?:[A-Za-z]:[\\/]|/)")

#: 路径后面允许出现、且表示"路径到此为止"的字符（全角/半角括号、标点、空白、引号）。
#: 见 `artifact_path()` 里的注释：不要求边界的话会退化成父目录。
#: ⚠️ 必须同时含**左括号**：尾部注释形如 `（共 176 页）`，路径紧挨的是 `（` 不是 `）`。
_PATH_BOUNDARY = set("（）()「」『』【】[]{}”。.…，,；;：: \t\n\r\"'")


def artifact_path(last_line: str) -> str:
    """从引擎的**契约行**（stdout 最后一行）里抠出产物路径；抠不到返回空串。

    为什么需要它：编辑最常问的两句是「跑完了吗」「产物在哪」。契约行（`ENGINE_API.md` 五）
    本来就把路径写在里面了，界面把它做成**可点开 / 可复制**的，比让人翻日志强得多。

    抠法：对每个「路径起点」取**磁盘上真的存在的最长前缀** —— 这样路径里的括号、
    尾巴上的 `（共 176 页）` / `）` / `。` 都不会误伤，也不需要给路径定字符集。
    抠不到（dry-run、路径已被移走）就返回空串，界面只显示状态、不给按钮 —— **不猜**。
    """
    line = last_line or ""
    best = ""
    for m in _PATH_START.finditer(line):
        rest = line[m.start():]
        for end in range(len(rest), 0, -1):
            cand = rest[:end].rstrip("。.…")
            # ⚠️ 候选必须停在**边界字符**上（结尾 / 空格 / 括号 / 标点）。
            #    否则「路径不存在」会退化成"存在的父目录"——`/tmp/不存在/xyz` 会被
            #    抠成 `/tmp`，横幅显示一个**错路径**比不显示更糟（2026-10-17 双侧测试抓出）。
            nxt = rest[end] if end < len(rest) else ""
            if nxt and nxt not in _PATH_BOUNDARY:
                continue
            if os.path.exists(cand):
                if len(cand) > len(best):
                    best = cand
                break
    return best


def result_text(entry: str, code: int) -> str:
    """把退出码翻成给编辑看的一句话（H3：1 ≠ 失败）。"""
    if code == 0:
        return "✅ 完成"
    if code == 1:
        return _RESULT_1.get(entry, _RESULT_1_DEFAULT)
    if code < 0:
        return f"❌ 启动失败或被中止（退出码 {code}）"
    return f"❌ 失败（退出码 {code}）"


@dataclass
class EngineStatus:
    """给「设置」页显示的引擎状态。"""
    engine_dir: str | None = None
    python: str | None = None
    frozen_exe: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def describe(self) -> str:
        if self.engine_dir is None and self.frozen_exe is None:
            return "❌ 没找到引擎"
        # ⚠️ 冻结形态下 `engine_dir` **本来就是 None**（发布包不带 Engine/ 源码），
        #    从前照样拼出「引擎目录 None」—— 而那正是发布版设置页的默认画面
        #    （2026-10-17 审计实测；README 的发布前检查还要求它显示「运行方式：冻结引擎 …」）。
        if self.frozen_exe and self.engine_dir is None:
            return f"✅ 运行方式：冻结引擎 {self.frozen_exe}"
        if self.frozen_exe:
            return f"✅ 引擎目录 {self.engine_dir}；运行方式：冻结引擎 {self.frozen_exe}"
        return f"✅ 引擎目录 {self.engine_dir}；运行方式：Python {self.python}"


def status() -> EngineStatus:
    st = EngineStatus(engine_dir=slcn_engine(), python=slcn_python(),
                      frozen_exe=frozen_engine_exe())
    if st.engine_dir is None and st.frozen_exe is None:
        st.problems.append("找不到 Engine 目录（可用环境变量 SLCN_ENGINE 指定）")
    if st.frozen_exe is None and st.python is None:
        st.problems.append("既没有冻结引擎 exe，也没有可用的 Python 解释器")
    return st
