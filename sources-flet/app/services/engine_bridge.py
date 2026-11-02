"""引擎桥：Flet 外壳 ↔ Engine 的唯一通道。

本模块**不 import** tkinter / flet，可在纯控制台与 pytest 下导入测试。
argv 语义与旧 Tk 壳 `sources-win/engine.py`（历史文件，2026-11-01 已删除）对齐
（同一个引擎入口，不为 Flet 另造口径）。

口径：
- dev 模式：解释器 = `P2_ENGINE_PYTHON` / 当前解释器（python3.14 绝对路径）/ `py -3.14`，
  命令 = `[python, Engine/<entry>, ...]`。
- 打包模式（flet pack onefile）：命令 = `[SLCNEngine.exe, <entry>, ...]`（同目录 sidecar，
  可用 `P2_ENGINE_EXE` / `SLCN_ENGINE_EXE` 覆盖）。
- 子进程一律 CREATE_NO_WINDOW；stdout/stderr 合并成一条流，逐行回调解码为 UTF-8。
- 取消 = 终止整棵进程树（taskkill /T /F；POSIX killpg）。
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_MAC_TOOL_DIRS = (
    "/Library/TeX/texbin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)


def _augment_path(path: str, extra: tuple[str, ...] = _MAC_TOOL_DIRS,
                  *, platform: str | None = None) -> str:
    """macOS：Finder/LaunchServices 启动的 app 只有系统默认 PATH。

    系统默认 PATH 不含 MacTeX（/Library/TeX/texbin）与 Homebrew，导致
    `envcheck.py` 的 `shutil.which("xelatex")`/`("biber")` 误报缺失，
    继而 GUI「环境检查失败」；同一 PATH 也会让 convert 找不到 TeX 工具。
    仅 darwin 生效：把存在且尚未入 PATH 的工具目录前置（不改 Windows/Linux）。
    """
    if (sys.platform if platform is None else platform) != "darwin":
        return path
    parts = [item for item in path.split(os.pathsep) if item]
    missing = [d for d in extra if os.path.isdir(d) and d not in parts]
    if not missing:
        return path
    return os.pathsep.join(missing + parts)


def _child_env() -> dict[str, str]:
    """P4：给引擎子进程固定的 UTF-8 stdio 环境。

    PyInstaller 冻结的 SLCNEngine 在 Windows 上没有控制台时，`print` 默认按
    locale（cp936）编码；父进程按 UTF-8 解码就会得到乱码，`--version` 的
    "SLCN 引擎 X.Y.Z" 也因此匹配不到（打包版显示「未知」）。
    统一注入 PYTHONIOENCODING/PYTHONUTF8，dev 与打包模式行为一致。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PATH"] = _augment_path(env.get("PATH", ""))
    return env

ENTRIES = (
    "convert.py",
    "checkbib.py",
    "mergepdf.py",
    "cleantemp.py",
    "compileall.py",
    "proofcheck.py",
    "infocheck.py",
    "toc.py",
    "publish.py",
    "bibdb.py",
    "envcheck.py",
    "diagnose.py",
    "dirpush.py",
)

SUMMARY_MARKERS = (
    "✅", "⚠", "❌", "❗", "ℹ", "⏹", "✓", "✗", "⛔",
    "完成。", "程序错误", "未找到", "启动失败", "失败", "警告", "需核对",
    "不一致", "命中", "跳过", "取消", "产物", "结论", "报告", "备份",
    "提示", "合计", "已配置", "通道", "预演", "剪贴板",
)
_SUMMARY_PREFIX = re.compile(r"^(?:\[(\d+)/(\d+)\]|（\d+/\d+）|▶|[①-⑳])")

_RESULT_1 = {
    "checkbib.py": "❗ 体检发现问题（退出码 1；不是程序出错）",
    "proofcheck.py": "❗ 有命中项（退出码 1；不是程序出错）",
    "compileall.py": "⚠ 有文章编译失败（退出码 1）",
    "toc.py": "⚠ 已完成，但有需要人工核对的内容（退出码 1）",
    "dirpush.py": "❌ 引擎内部出错（退出码 1）",
}
_ARTIFACT_START = re.compile(r"(?:[A-Za-z]:[\\/]|/)")
_ARTIFACT_TRAIL = "。.…，,；;：: \t\n\r\"'）)」』】]}>"


# ── 纯函数（可单测） ────────────────────────────────────────────────────
def is_summary_line(text: str) -> bool:
    line = (text or "").strip()
    if not line:
        return False
    if line.startswith("▶") or _SUMMARY_PREFIX.match(line):
        return True
    return any(marker in line for marker in SUMMARY_MARKERS)


def result_text(entry: str, code: int) -> str:
    if code == 0:
        return "✅ 完成"
    if code == 1:
        return _RESULT_1.get(entry, "❗ 完成，但有需要处理的结果（退出码 1）")
    if code < 0:
        return "❌ 启动失败或被中止"
    return f"❌ 失败（退出码 {code}）"


def result_level(entry: str, code: int) -> str:
    if code == 0:
        return "ok"
    if code == 1:
        return "warn"
    return "error"


def artifact_path(last_line: str) -> str:
    """从契约行里抠出真实存在的产物绝对路径；抠不到返回空串（不猜）。

    做法：从每个盘符/路径起点开始，按「最长优先」逐字符截短，取第一个磁盘上
    真实存在的前缀；尾部的中文标点/右括号先剥掉再试。路径里允许中文、空格、点。
    """
    text = last_line or ""
    for match in _ARTIFACT_START.finditer(text):
        start = match.start()
        for end in range(len(text), start + 1, -1):
            candidate = text[start:end]
            for probe in (candidate, candidate.rstrip(_ARTIFACT_TRAIL)):
                if not probe:
                    continue
                # 契约行里的「有 tex 2 / 有 pdf 1」会被当成路径起点；
                # 根目录/盘符根不是产物，跳过继续找后面的「报告：…」。
                bare = probe.strip()
                if bare in ("/", "\\") or (
                    len(bare) == 3 and bare[1] == ":" and bare[2] in "\\/"
                ):
                    continue
                try:
                    if Path(probe).exists():
                        return probe
                except OSError:
                    continue
    return ""


def first_existing_path(*candidates: str) -> str:
    for item in candidates:
        if item and Path(item).exists():
            return str(Path(item))
    return ""


# ── 引擎/解释器发现 ─────────────────────────────────────────────────────
@dataclass
class EngineStatus:
    engine_dir: str = ""
    python: str = ""
    frozen_exe: str = ""
    version: str = "未知"
    mode: str = "dev"
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # P4：打包模式（flet pack + 同目录 SLCNEngine sidecar）下没有 Engine 源码目录，
        # 此时只要冻结 sidecar 在就算可用；源码模式（dev）仍要求 Engine 目录 + 解释器。
        return bool(self.frozen_exe or (self.engine_dir and self.python))

    def describe(self) -> str:
        if self.frozen_exe:
            return f"冻结引擎 · {self.frozen_exe}"
        if self.python and self.engine_dir:
            return f"源码引擎 · {self.python}"
        return "未找到引擎"

    def to_dict(self) -> dict:
        return {
            "engine_dir": self.engine_dir,
            "python": self.python,
            "frozen_exe": self.frozen_exe,
            "version": self.version,
            "mode": self.mode,
            "ok": self.ok,
            "describe": self.describe(),
            "problems": list(self.problems),
        }


def app_root() -> Path:
    """sources-flet/app 目录（本文件在 app/services 下）。"""
    return Path(__file__).resolve().parent.parent


def repo_root() -> Path:
    return app_root().parent.parent


def find_engine_dir() -> str:
    override = os.environ.get("P2_ENGINE") or os.environ.get("SLCN_ENGINE") or ""
    candidates = [
        override,
        str(repo_root() / "Engine"),
    ]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates += [str(exe_dir / "Engine"), str(exe_dir / "_internal" / "Engine")]
    return first_existing_path(*candidates)


def find_engine_python() -> str:
    override = os.environ.get("P2_ENGINE_PYTHON") or os.environ.get("SLCN_PYTHON") or ""
    if override:
        return override
    if not getattr(sys, "frozen", False):
        return sys.executable or ""
    return shutil.which("python3.14") or shutil.which("python") or ""


def find_engine_exe() -> str:
    override = os.environ.get("P2_ENGINE_EXE") or os.environ.get("SLCN_ENGINE_EXE") or ""
    if override:
        return override if Path(override).exists() else ""
    names = ("SLCNEngine.exe", "SLCNEngine")
    roots = [Path(sys.executable).resolve().parent] if getattr(sys, "frozen", False) else []
    roots.append(repo_root())
    found = []
    for root in roots:
        for name in names:
            found.append(str(root / name))
    return first_existing_path(*found)


_ENGINE_VERSION_CACHE: dict[tuple, str] = {}


def engine_version() -> str:
    """引擎版本；同进程内按 (engine_dir, exe) 缓存。

    打包形态下每次读版本都要启动一次 SLCNEngine（PyInstaller onefile，
    每次解包约 23MB），启动期有多处调用会叠加成明显卡顿；源码形态读文件即可。
    用 (engine_dir, exe) 作 key，设置里改引擎目录后自动失效。
    """
    engine_dir = find_engine_dir()
    exe = find_engine_exe()
    cache_key = (engine_dir, exe)
    cached = _ENGINE_VERSION_CACHE.get(cache_key)
    if cached is not None:
        return cached
    version = _read_engine_version(engine_dir, exe)
    _ENGINE_VERSION_CACHE[cache_key] = version
    return version


def clear_engine_version_cache() -> None:
    """测试/刷新用：清掉版本缓存。"""
    _ENGINE_VERSION_CACHE.clear()


def _read_engine_version(engine_dir: str | None, exe: str | None) -> str:
    if engine_dir:
        init_py = Path(engine_dir) / "slcn" / "__init__.py"
        try:
            match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_py.read_text(encoding="utf-8"), re.M)
            if match:
                return match.group(1)
        except OSError:
            pass
    if exe:
        try:
            proc = subprocess.run(
                [exe, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10,
                creationflags=CREATE_NO_WINDOW,
                env=_child_env(),
            )
            match = re.search(r"SLCN 引擎 ([0-9][0-9A-Za-z.\-+]*)", proc.stdout + proc.stderr)
            if match:
                return match.group(1)
        except (OSError, subprocess.SubprocessError):
            pass
    return "未知"


def engine_status() -> EngineStatus:
    status = EngineStatus()
    status.engine_dir = find_engine_dir()
    status.python = find_engine_python()
    status.frozen_exe = find_engine_exe()
    status.mode = "frozen" if status.frozen_exe else "dev"
    status.version = engine_version() if status.engine_dir or status.frozen_exe else "未知"
    if status.engine_dir:
        missing = [entry for entry in ("convert.py", "checkbib.py") if not (Path(status.engine_dir) / entry).exists()]
        if missing:
            status.problems.append(f"Engine 目录缺少入口：{', '.join(missing)}")
    elif not status.frozen_exe:
        # P4：sidecar（同目录 SLCNEngine）存在时不需要 Engine 源码目录，不算问题。
        status.problems.append("找不到 Engine 目录（可用设置里的「引擎目录」或 SLCN_ENGINE 指定）")
    if not status.frozen_exe and not status.python:
        status.problems.append("找不到可用于运行引擎的 Python 解释器")
    return status


# ── 命令构造（与旧 Tk 壳 engine.py 同口径；旧壳属历史） ──────────────────
@dataclass
class EngineCall:
    entry: str
    argv: list[str]
    label: str = ""

    @property
    def display(self) -> str:
        return " ".join(self.argv)

    def to_dict(self) -> dict:
        return {"entry": self.entry, "label": self.label, "argv": list(self.argv)}


def base_cmd(entry: str) -> list[str] | None:
    exe = find_engine_exe()
    if exe:
        return [exe, entry]
    engine_dir = find_engine_dir()
    python = find_engine_python()
    if engine_dir and python:
        return [python, "-X", "utf8", str(Path(engine_dir) / entry)]
    return None


def _call(entry: str, args: list[str], label: str = "") -> EngineCall:
    head = base_cmd(entry)
    if head is None:
        raise RuntimeError("找不到引擎：请检查设置里的「引擎目录」或安装包是否带 SLCNEngine.exe")
    return EngineCall(entry=entry, argv=head + args, label=label or entry.removesuffix(".py"))


def _append_flag(args: list[str], flag: str, enabled: bool) -> None:
    if enabled:
        args.append(flag)


def _append_opt(args: list[str], flag: str, value: str | int | None) -> None:
    if value is None or value == "":
        return
    args.extend([flag, str(value)])


def convert_call(
    *,
    source: str,
    out: str,
    compile_pdf: bool = False,
    clean: bool = False,
    source_pdf: bool = False,
    verify_refs: bool = False,
    verify_plan: bool = False,
    verify_max: int | None = None,
    verify_all_refs: bool = False,
    no_bibdb: bool = False,
    bib: str = "",
    metadata: str = "",
    author: str = "",
    name: str = "",
    vol: str = "",
    issue: str = "",
    year: str = "",
    pages: str = "",
    received: str = "",
    editor: str = "",
    grants: str = "",
    lang: str = "",
    metadata_json: str = "",
    metadata_preview_out: str = "",
    refs: str = "",
    sty: str = "",
    skeleton: str = "",
    force: bool = False,
    extra: str = "",
) -> EngineCall:
    args = ["--source", source, "--out", out]
    _append_flag(args, "--compile", compile_pdf)
    _append_flag(args, "--clean", clean)
    if verify_plan:
        args.append("--verify-plan")
    elif verify_refs:
        args.append("--verify-refs")
    if verify_refs or verify_plan:
        _append_opt(args, "--verify-max", verify_max)
        _append_flag(args, "--verify-all-refs", verify_all_refs)
        _append_flag(args, "--no-bibdb", no_bibdb)
    _append_flag(args, "--source-pdf", source_pdf)
    for flag, value in (
        ("--bib", bib), ("--metadata", metadata), ("--author", author), ("--name", name),
        ("--vol", vol), ("--issue", issue), ("--year", year), ("--pages", pages),
        ("--received", received), ("--editor", editor), ("--grants", grants), ("--lang", lang),
    ):
        _append_opt(args, flag, value)
    # 批 109 W2a CV-001..003：元数据三入口（人工 JSON / 只读预览）与
    # 初排 P1 的 --force/--refs/--sty/--skeleton；全部按「非空才传」。
    _append_opt(args, "--metadata-json", metadata_json)
    _append_opt(args, "--metadata-preview-out", metadata_preview_out)
    _append_opt(args, "--refs", refs)
    _append_opt(args, "--sty", sty)
    _append_opt(args, "--skeleton", skeleton)
    _append_flag(args, "--force", force)
    if extra:
        args.extend(extra.split())
    return _call("convert.py", args, "初排转换")


# ── AI：协同两段式 / 直连核实（批 107 P2） ──────────────────────────────
# 三个入口都只是「把界面取值拼成引擎参数」；文档逻辑与预算闸都在 Engine/。
def ai_settings_env(ai: dict) -> dict:
    """AI 设置 → `run_call(api=…)` 的环境映射。

    Key **只**经环境变量 `SLCN_LLM_KEY` 交给子进程：`EngineCall.to_dict()` 会进
    运行证据（P2MARK / marker 文件），argv 里出现密钥就等于写进证据。
    """
    if not isinstance(ai, dict):
        return {}
    api: dict = {}
    for src, dst in (("base", "base"), ("key", "key"), ("model", "model")):
        value = str(ai.get(src) or "").strip()
        if value:
            api[dst] = value
    protocol = str(ai.get("protocol") or "").strip()
    if protocol in ("chat", "responses"):
        api["protocol"] = protocol
    api["thinking"] = "1" if ai.get("thinking") else "0"
    return api


def _budget_arg(value):
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        return max(0, int(text, 10))
    except ValueError:
        return None


def coop_ph1_call(*, source: str, out: str, fulltext: str = "", stage: str = "",
                  **convert_kwargs) -> EngineCall:
    """第一段：出核实任务书（尚未交付、零上传）。"""
    call = convert_call(source=source, out=out, verify_refs=True, **convert_kwargs)
    extra = ["--coop-taskbook-only", "--no-ai-merge"]
    if fulltext in ("context", "full"):
        extra.extend(["--coop-fulltext", fulltext])
    _append_opt(extra, "--coop-stage", stage)
    return EngineCall(entry=call.entry, argv=list(call.argv) + extra,
                      label="协同初排 · 第一段（出任务书，尚未交付）")


def coop_ph2_call(*, source: str, out: str, replies, stage: str = "",
                  apply: bool = False, **convert_kwargs) -> EngineCall:
    """第二段：导入回填（`--coop-apply` 才改产物，否则只记录）。"""
    paths = [str(path).strip() for path in (replies or []) if str(path or "").strip()]
    if not paths:
        raise ValueError("导入回填至少要一个回填文件")
    call = convert_call(source=source, out=out, **convert_kwargs)
    extra: list[str] = []
    for path in paths:
        extra.extend(["--coop-replies", path])
    _append_opt(extra, "--coop-stage", stage)
    if apply:
        extra.append("--coop-apply")
    return EngineCall(entry=call.entry, argv=list(call.argv) + extra,
                      label="协同初排 · 第二段（" + ("应用回填" if apply else "仅记录") + "）")


def ai_verify_call(*, source: str, out: str, ai: dict, no_ai_merge: bool = True,
                   **convert_kwargs) -> EngineCall:
    """直连核实：Key 不进 argv（见 `ai_settings_env`），预算参数总是显式传。"""
    settings = ai if isinstance(ai, dict) else {}
    if not str(settings.get("key") or "").strip():
        raise ValueError("直连核实需要 API Key：请先在「设置 → AI」填好（Key 只经环境变量传给引擎）")
    convert_kwargs.pop("verify_refs", None)
    convert_kwargs.pop("verify_max", None)
    verify_max = settings.get("verify_max") or 0
    try:
        verify_max = int(verify_max) or None
    except (TypeError, ValueError):
        verify_max = None
    call = convert_call(source=source, out=out, verify_refs=True,
                        verify_max=verify_max, **convert_kwargs)
    args = list(call.argv)
    _append_opt(args, "--llm-base", str(settings.get("base") or "").strip())
    _append_opt(args, "--llm-model", str(settings.get("model") or "").strip())
    protocol = str(settings.get("protocol") or "").strip()
    if protocol in ("chat", "responses"):
        _append_opt(args, "--llm-protocol", protocol)
    _append_opt(args, "--ai-budget-tokens", _budget_arg(settings.get("article_budget_tokens")))
    _append_opt(args, "--ai-batch-budget-tokens", _budget_arg(settings.get("batch_budget_tokens")))
    if no_ai_merge:
        args.append("--no-ai-merge")
    return EngineCall(entry=call.entry, argv=args, label="AI 核实（直连 API）")


def checkbib_call(
    *,
    returned: str = "",
    original: str = "",
    tex: str = "",
    out: str = "",
    returned_stdin: bool = False,
    list_keys: bool = False,
) -> EngineCall:
    # 批 109 Wave 0 / S1：**不传 --merge-into**。该参数在 Engine/checkbib.py 里是
    # 「就地覆盖写回的目标文件」（aibib.apply_merge_to_file），外壳曾把用户正文 .tex
    # 当目标传入，会损坏来稿；Flet 无法保证目标准确，故按任务书方案 B 关闭并回入口。
    # 证据：C:\slcn-work\p6-2026-11-01\evidence\b109-parity\report.md（S1 行）与
    #       C:\slcn-work\p6-2026-11-01\prompts\b109-wave0-safety-taskbook.md §S1。
    # --tex 只作只读统计输入（报告用），列表模式永不写文件。
    # 批 109 W2a NR-002/003/011：交回来源两态（文件 / 粘贴走标准输入），
    # 以及只打印键名的 --list-keys（粘贴模式的「识别」预览用；永不写文件）。
    args: list[str] = ["--list-keys"] if list_keys else []
    if returned_stdin:
        args.append("--returned-stdin")
    else:
        args.extend(["--returned", returned])
    _append_opt(args, "--original", original)
    _append_opt(args, "--tex", tex)
    _append_opt(args, "--out", out)
    label = "识别文献键" if list_keys else "参考文献规范化"
    return _call("checkbib.py", args, label)


def proofcheck_call(*, tex: str, fix: bool = False, lang: str = "") -> EngineCall:
    args = ["--tex", tex]
    _append_opt(args, "--lang", lang)
    _append_flag(args, "--fix", fix)
    return _call("proofcheck.py", args, "一校核查")


def infocheck_call(*, folder: str, preview: bool = False) -> EngineCall:
    args = ["--folder", folder]
    _append_flag(args, "--preview", preview)
    return _call("infocheck.py", args, "检查 tex 信息")


def toc_call(*, report: str, template: str = "", text_only: bool = False,
             result_json: str = "", preview: bool = False, settings: str = "") -> EngineCall:
    args = ["--report", report]
    _append_opt(args, "--template", template)
    _append_opt(args, "--settings", settings)
    if preview:
        args.append("--preview")
    elif not text_only:
        args.extend(["--place", "--compile"])
        _append_opt(args, "--result-json", result_json)
    return _call("toc.py", args, "生成目录")


def compileall_call(*, folder: str, only: str = "", dry_run: bool = False) -> EngineCall:
    args = ["--folder", folder]
    _append_opt(args, "--only", only)
    _append_flag(args, "--dry-run", dry_run)
    return _call("compileall.py", args, "编译全部")


def merge_call(
    *, folder: str, out: str = "", nest_outlines: bool = False,
    editors_page: bool = False, back_cover: bool = False, detect: bool = False,
) -> EngineCall:
    args = ["--folder", folder]
    _append_opt(args, "--out", out)
    _append_flag(args, "--nest-outlines", nest_outlines)
    _append_flag(args, "--editors-page", editors_page)
    _append_flag(args, "--back-cover", back_cover)
    _append_flag(args, "--detect", detect)
    return _call("mergepdf.py", args, "合并文件")


def cleantemp_call(*, folder: str, mode: str = "aux", dry_run: bool = False,
                   restore: str = "") -> EngineCall:
    # S2/D-49：aux 档固定 --trash（移入 <期目录>/.toolbox-trash，可 --restore 恢复）；
    # delivery/workspace 引擎自身即移入回收站。外壳不暴露 --delete / --empty-trash。
    if restore:
        return _call("cleantemp.py", ["--folder", folder, "--restore", restore], "撤销清理")
    args = ["--folder", folder]
    if mode and mode != "aux":
        args.extend(["--mode", mode])
    _append_flag(args, "--dry-run", dry_run)
    if not mode or mode == "aux":
        args.append("--trash")
    return _call("cleantemp.py", args, "删除临时文件")


def dirpush_call(
    *, issue: str, out_root: str = "", with_en_toc: bool = False,
    annual: bool = False, year: str = "", include_issues: str = "", dry_run: bool = False,
) -> EngineCall:
    args = ["--issue", issue]
    _append_opt(args, "--out", out_root)
    if annual:
        args.append("--annual")
        _append_opt(args, "--year", year)
        csv = dirpush_issues_csv(include_issues)
        if csv:
            args.extend(["--include-issues", csv])
    elif with_en_toc:
        args.append("--with-en-toc")
    args.append("--json")
    _append_flag(args, "--dry-run", dry_run)
    return _call("dirpush.py", args, "目录摘要推送")


def dirpush_issues_csv(value: str) -> str:
    seen: list[int] = []
    for chunk in (value or "").replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk.isdigit():
            continue
        number = int(chunk)
        if number > 0 and number not in seen:
            seen.append(number)
    return ",".join(str(n) for n in sorted(seen))


# ── 只读读库 / 环境检查（P3：引用库视图与设置页，不写库）────────────────
def bibdb_stats_call() -> EngineCall:
    """引用库概况（只读）：stats --json。"""
    return _call("bibdb.py", ["stats", "--json"], "引用库概况")


def bibdb_query_call(
    *,
    key: str = "",
    title: str = "",
    author: str = "",
    year: str = "",
    limit: int = 20,
) -> EngineCall:
    """引用库检索（只读）：query --json。只允许检索参数，绝不带写子命令。"""
    args = ["query", "--json"]
    if key.strip():
        args += ["--key", key.strip()]
    if title.strip():
        args += ["--title", title.strip()]
    if author.strip():
        args += ["--author", author.strip()]
    if year.strip():
        args += ["--year", year.strip()]
    args += ["--limit", str(max(1, min(int(limit or 20), 100)))]
    return _call("bibdb.py", args, "引用库检索")


# 旧壳查库页的两组常量（bibdb_ui.py QUERY_MODES / ADVANCED_*；顺序即界面顺序）
QUERY_MODES = ("key", "doi", "title")
QUERY_MODE_LABELS = {"key": "key", "doi": "DOI", "title": "题名"}
QUERY_FIELDS = ("author", "editor", "title", "booktitle", "journal",
                "publisher", "year", "doi", "alias", "any")
QUERY_FIELD_LABELS = {
    "author": "作者", "editor": "编者", "title": "题名", "booktitle": "书名",
    "journal": "期刊", "publisher": "出版社", "year": "年份", "doi": "DOI",
    "alias": "别名", "any": "任意",
}
QUERY_MATCH_MODES = ("contains", "prefix", "exact", "fuzzy")
QUERY_MATCH_LABELS = {"contains": "包含", "prefix": "前缀", "exact": "精确", "fuzzy": "模糊"}
QUERY_SORTS = ("relevance", "year", "author")
QUERY_SORT_LABELS = {"relevance": "相关度", "year": "年份", "author": "作者"}
QUERY_NO_FUZZY = ("year", "doi", "alias")


def _limit_value(raw, *, default: int = 20) -> int:
    """limit 1–200；报错句子与旧壳逐字一致（`raw!r` 保留原始输入，便于人核对）。

    `None` = 调用方没给（用默认值）；显式空串 = 非数字，按旧壳报错（Spinbox 被清空时
    旧壳同样是「limit 必须是 1–200 的整数：''」）。
    """
    if raw is None:
        return default
    if str(raw).strip() == "":
        raise ValueError(f"limit 必须是 1–200 的整数：{raw!r}")
    try:
        number: int | None = int(str(raw).strip())
    except (TypeError, ValueError):
        number = None
    if number is None or not 1 <= number <= 200:
        raise ValueError(f"limit 必须是 1–200 的整数：{raw!r}")
    return number


def bibdb_query_basic_call(
    mode: str,
    value: str,
    *,
    limit: int | str = 10,
    bibtex: bool = True,
    db: str = "",
) -> EngineCall:
    """基本检索（只读）：query --key|--doi|--title --json [--bibtex]。

    与旧壳 `engine.py` 的 bibdb_query 同口径：方式三选一、`--bibtex` 让每条命中带
    可直接粘贴的 BibTeX；`--limit` 只在题名（模糊匹配）时对旧壳有意义，这里统一给
    引擎（默认 10，与引擎一致）。
    """
    mode = (mode or "key").strip()
    if mode not in QUERY_MODES:
        raise ValueError(f"未知的检索方式：{mode}（只认 key/doi/title）")
    text = (value or "").strip()
    args = ["query", "--json"]
    args += [f"--{mode}", text]
    args += ["--limit", str(_limit_value(limit, default=10))]
    _append_flag(args, "--bibtex", bibtex)
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, f"引用库 · 查库（{QUERY_MODE_LABELS[mode]}）")


def bibdb_query_advanced_call(
    *,
    terms: list[tuple[str, str, str]] | list[dict],
    sort: str = "relevance",
    limit: int | str = 20,
    strict: bool = False,
    bibtex: bool = True,
    db: str = "",
) -> EngineCall:
    """高级检索（只读）：query-advanced，多行条件 AND。

    `terms` 每项 `(field, value, mode)`（也接受同名字典）。口径对齐旧壳：空值行跳过；
    字段 10 项、模式 4 项、排序 3 项；每行都显式带 `--mode field=mode`；`--strict`
    （模糊门槛 0.90）与 `--min-score=0.75`（引擎默认）二选一，不叠加。
    """
    rows: list[tuple[str, str, str]] = []
    for term in terms or []:
        if isinstance(term, dict):
            field, value, mode = term.get("field"), term.get("value"), term.get("mode")
        else:
            field, value, mode = (list(term) + ["", "", ""])[:3]
        value = str(value or "").strip()
        if not value:
            continue  # 旧壳：空值行跳过（不算条件）
        rows.append((str(field or "").strip(), value, str(mode or "").strip()))
    if not rows:
        raise ValueError("高级检索至少要填一行检索条件。")
    if sort not in QUERY_SORTS:
        raise ValueError(f"未知的排序方式：{sort}（只认 relevance/year/author）")
    args = ["query-advanced"]
    for field, value, mode in rows:
        if field not in QUERY_FIELDS:
            raise ValueError(f"未知的检索字段：{field}（只认 10 个字段）")
        if mode not in QUERY_MATCH_MODES:
            raise ValueError(f"未知的匹配方式：{mode}（只认 contains/prefix/exact/fuzzy）")
        args += [f"--{field}", value, "--mode", f"{field}={mode}"]
    args += ["--sort", sort, "--limit", str(_limit_value(limit, default=20))]
    args.append("--strict" if strict else "--min-score=0.75")
    _append_flag(args, "--bibtex", bibtex)
    args.append("--json")
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, "引用库 · 高级检索")


# ── 写库调用（B-246c P6：入藏 / 待裁决；默认 dry-run，写前带 sha 闸）──────
def bibdb_import_call(
    *,
    bib: str = "",
    issue: str = "",
    slug: str = "",
    dry_run: bool = True,
    force: bool = False,
    verified: bool = False,
    unverified: bool = False,
    expect_db_sha256: str = "",
) -> EngineCall:
    """入藏单份 bib：import-bib --json（dry_run=True 只算不写）。"""
    args = ["import-bib", "--json"]
    _append_opt(args, "--bib", bib)
    _append_opt(args, "--issue", issue)
    _append_opt(args, "--slug", slug)
    _append_flag(args, "--force", force)
    _append_flag(args, "--verified", verified)
    _append_flag(args, "--unverified", unverified)
    _append_flag(args, "--dry-run", dry_run)
    _append_opt(args, "--expect-db-sha256", expect_db_sha256)
    return _call("bibdb.py", args, "入藏预览" if dry_run else "写入引用库")


def bibdb_import_dir_call(
    *,
    root: str = "",
    through_year: int | str = "",
    finished_only: bool = False,
    dry_run: bool = True,
    force: bool = False,
    expect_db_sha256: str = "",
) -> EngineCall:
    """入藏整期目录 / 稿件库：import-dir --json（dry_run=True 只算不写）。"""
    args = ["import-dir", "--json"]
    _append_opt(args, "--root", root)
    _append_opt(args, "--through-year", through_year)
    _append_flag(args, "--finished-only", finished_only)
    _append_flag(args, "--force", force)
    _append_flag(args, "--dry-run", dry_run)
    _append_opt(args, "--expect-db-sha256", expect_db_sha256)
    return _call("bibdb.py", args, "整期入藏预览" if dry_run else "整期写入引用库")


def bibdb_conflicts_call(*, pending_only: bool = True, db: str = "") -> EngineCall:
    """待裁决清单（只读）：conflicts --json [--pending-only]。"""
    args = ["conflicts", "--json"]
    _append_flag(args, "--pending-only", pending_only)
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, "待裁决清单")


def bibdb_resolve_call(
    *,
    key: str = "",
    action: str = "keep",
    fields: str = "",
    all_fields: bool = False,
    incoming_sha256: str = "",
    bib: str = "",
    adopt_all_pending: bool = False,
    note: str = "",
    expect_db_sha256: str = "",
    dry_run: bool = False,
    db: str = "",
) -> EngineCall:
    """记录冲突裁决：resolve --json（默认写库；dry_run=True 只预览）。"""
    args = ["resolve", "--json"]
    if adopt_all_pending:
        args.append("--adopt-all-pending")
    else:
        _append_opt(args, "--key", key)
        _append_opt(args, "--action", action)
    _append_opt(args, "--fields", fields)
    _append_flag(args, "--all-fields", all_fields)
    _append_opt(args, "--incoming-sha256", incoming_sha256)
    _append_opt(args, "--bib", bib)
    _append_opt(args, "--note", note)
    _append_flag(args, "--dry-run", dry_run)
    _append_opt(args, "--expect-db-sha256", expect_db_sha256)
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, "裁决预览" if dry_run else "记录裁决")


# ── 写库高级能力（批 109 W1b）：编辑 / 合并 / 字段归位 / 健康 / 确认重复 ──
def bibdb_lint_call(*, with_records: bool = False, db: str = "") -> EngineCall:
    """库健康检查（只读）：lint --json（退出码 1 = 有警告，仍带完整 JSON）。"""
    args = ["lint", "--json"]
    _append_flag(args, "--with-records", with_records)
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, "库健康检查")


def bibdb_edit_call(
    *,
    key: str,
    fields: dict | None = None,
    new_key: str = "",
    entry_type: str = "",
    note: str = "",
    dry_run: bool = True,
    expect_db_sha256: str = "",
    backup: str = "",
    db: str = "",
) -> EngineCall:
    """编辑条目：edit --json（默认 dry-run）。

    `fields` 是**整份字段字典**（引擎整份替换，不是 patch）；`--new-key` 保留旧 key 为别名。
    注意：`--bib` 是「改从 .bib 文件读取整份字段」，与库路径无关；库路径只能走 `--db`。
    """
    import json

    args = ["edit", "--json"]
    _append_opt(args, "--key", key)
    if fields is not None:
        args += ["--json-fields", json.dumps(fields, ensure_ascii=False, sort_keys=True)]
    _append_opt(args, "--new-key", new_key)
    _append_opt(args, "--entry-type", entry_type)
    _append_opt(args, "--note", note)
    _append_flag(args, "--dry-run", dry_run)
    if not dry_run:
        args.append("--apply")
    _append_opt(args, "--backup", backup)
    _append_opt(args, "--expect-db-sha256", expect_db_sha256)
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, "编辑预览" if dry_run else "编辑条目")


def bibdb_merge_call(
    *,
    keys: list | tuple,
    new_key: str = "",
    entry_type: str = "",
    field_sources: dict | None = None,
    note: str = "",
    dry_run: bool = True,
    expect_db_sha256: str = "",
    backup: str = "",
    db: str = "",
) -> EngineCall:
    """合并重复条目：merge-records --json（默认 dry-run）。"""
    args = ["merge-records", "--json"]
    picked = [str(item).strip() for item in (keys or []) if str(item).strip()]
    if picked:
        args += ["--keys", ",".join(picked)]
    _append_opt(args, "--new-key", new_key)
    _append_opt(args, "--entry-type", entry_type)
    for field, source in (field_sources or {}).items():
        field, source = str(field or "").strip(), str(source or "").strip()
        if field and source:
            args += ["--field-source", f"{field}={source}"]
    _append_opt(args, "--note", note)
    _append_flag(args, "--dry-run", dry_run)
    if not dry_run:
        args.append("--apply")
    _append_opt(args, "--backup", backup)
    _append_opt(args, "--expect-db-sha256", expect_db_sha256)
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, "合并预览" if dry_run else "合并条目")


def bibdb_suggest_fixes_call(*, registry: str = "missing_field", limit: int = 0,
                             db: str = "") -> EngineCall:
    """字段归位候选（只读）：suggest-field-fixes --json。"""
    args = ["suggest-field-fixes", "--json"]
    if str(registry or "").strip():
        args += ["--registry", str(registry).strip()]
    if int(limit or 0) > 0:
        args += ["--limit", str(int(limit))]
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, "字段归位候选")


def bibdb_report_duplicates_call(*, out: str = "", db: str = "") -> EngineCall:
    """查重报告（读库 + 写报告文件）：report-duplicates --json --out 临时目录。"""
    args = ["report-duplicates", "--json"]
    _append_opt(args, "--out", out)
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, "查重报告")


def bibdb_ack_duplicates_call(
    *,
    code: str = "near_duplicates",
    keys: list | tuple = (),
    reason: str = "",
    dry_run: bool = True,
    db: str = "",
) -> EngineCall:
    """确认「不是重复」（写 health-ignore.json sidecar，不动库）：ack-duplicates。"""
    args = ["ack-duplicates", "--json"]
    if str(code or "").strip():
        args += ["--code", str(code).strip()]
    picked = [str(item).strip() for item in (keys or []) if str(item).strip()]
    if picked:
        args += ["--keys", ",".join(picked)]
    _append_opt(args, "--reason", reason)
    _append_flag(args, "--dry-run", dry_run)
    if not dry_run:
        args.append("--apply")
    _append_opt(args, "--db", db)
    return _call("bibdb.py", args, "确认非重复")


def envcheck_call(out_dir: str = "") -> EngineCall:
    """环境检查（只读）：envcheck --json。"""
    args = ["--json"]
    if out_dir:
        args += ["--out-dir", out_dir]
    return _call("envcheck.py", args, "环境检查")


def parse_json_document(text: str):
    """从多行 stdout 里解析最后一段完整 JSON 对象（引擎 JSON 带缩进，逐行 json.loads 不够）。"""
    import json

    decoder = json.JSONDecoder()
    best = None
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return best
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(value, dict):
            best = value
        index = max(end, start + 1)


def read_json_command(
    call: EngineCall,
    *,
    timeout: float = 120.0,
    notes: str = "",
    env_extra: dict | None = None,
) -> dict:
    """同步执行只读子命令并解析 JSON（调用方放 asyncio.to_thread，避免阻塞 UI）。"""
    import json

    env = env_for_run(notes=notes)
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items() if v is not None})
    result: dict = {"ok": False, "entry": call.entry, "label": call.label, "argv": call.argv}
    try:
        proc = subprocess.run(
            call.argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        payload = parse_json_document(out)
        result.update(
            {
                "ok": proc.returncode == 0 and payload is not None,
                "code": proc.returncode,
                "json": payload,
                "lines": [ln for ln in out.splitlines() if ln.strip()][-80:],
            }
        )
        if payload is None and proc.returncode != 0:
            result["error"] = (proc.stderr or proc.stdout or "").strip()[-500:] or f"退出码 {proc.returncode}"
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


# ── 运行环境 ────────────────────────────────────────────────────────────
def env_for_run(api: dict | None = None, notes: str = "") -> dict:
    env = dict(os.environ)
    engine_dir = find_engine_dir()
    if engine_dir:
        vendor = str(Path(engine_dir) / "vendor")
        old = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = vendor + (os.pathsep + old if old else "")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    api = api or {}
    mapping = {
        "SLCN_LLM_BASE": api.get("base"),
        "SLCN_LLM_KEY": api.get("key"),
        "SLCN_LLM_MODEL": api.get("model"),
        "SLCN_LLM_PROTOCOL": api.get("protocol"),
        "SLCN_LLM_SEARCH_TOOL": api.get("searchTool"),
        "SLCN_LLM_RESPONSES_PATH": api.get("responsesPath"),
        "SLCN_LLM_THINKING": api.get("thinking"),
        "SLCN_LLM_MAX_TOKENS": api.get("maxTokens"),
        "SLCN_LLM_WEB_SEARCH": api.get("webSearch"),
    }
    for key, value in mapping.items():
        if value:
            env[key] = str(value)
    if api.get("searchRefs"):
        env["SLCN_SEARCH_REFS"] = "1"
    if notes:
        env["SLCN_NOTES"] = notes
    return env


# ── 进程控制 ────────────────────────────────────────────────────────────
def kill_tree(proc) -> None:
    """终止整棵进程树（防 xelatex / biber 孙进程残留）。"""
    if proc is None or proc.returncode is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10,
            )
        else:
            os.killpg(os.getpgid(proc.pid), 15)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.terminate()
        except ProcessLookupError:
            pass


async def run_call(
    call: EngineCall,
    *,
    on_line=None,
    on_proc=None,
    cwd: str | None = None,
    api: dict | None = None,
    notes: str = "",
    stdin_text: str | None = None,
) -> int:
    """流式执行一次引擎调用；返回退出码。取消请调用方 kill_tree(proc)。

    `stdin_text` 非 None 时把文本喂给子进程标准输入并关闭（批 109 W2a：
    参考文献规范化的「粘贴交回 .bib」模式 —— ``--returned-stdin``，不落中间文件）。
    """
    env = env_for_run(api, notes)
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec(
        *call.argv,
        cwd=cwd or None,
        env=env,
        stdin=(asyncio.subprocess.PIPE if stdin_text is not None
               else asyncio.subprocess.DEVNULL),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **kwargs,
    )
    if on_proc is not None:
        on_proc(proc)
    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass
    try:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if on_line is not None:
                on_line(line)
        return await proc.wait()
    finally:
        if proc.returncode is None:
            kill_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass


def toc_result(path: str) -> dict:
    try:
        import json

        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def toc_result_lines(payload: dict) -> list[str]:
    if not payload:
        return []
    lines = []
    placed = payload.get("placed") or {}
    if placed.get("zhPdf"):
        lines.append(f"中文 PDF：{placed['zhPdf']}")
    if placed.get("enPdf"):
        lines.append(f"英文 PDF：{placed['enPdf']}")
    pages = payload.get("pages") or {}
    if pages:
        lines.append(f"页数：中文 {pages.get('zh', '?')} / 英文 {pages.get('en', '?')}")
    for warning in payload.get("warnings") or []:
        lines.append(f"⚠ {warning}")
    return lines
