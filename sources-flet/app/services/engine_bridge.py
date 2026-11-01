"""引擎桥：Flet 外壳 ↔ Engine 的唯一通道。

本模块**不 import** tkinter / flet，可在纯控制台与 pytest 下导入测试。
argv 语义与 `sources-win/engine.py` 对齐（同一个引擎入口，不为 Flet 另造口径）。

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


def engine_version() -> str:
    engine_dir = find_engine_dir()
    if engine_dir:
        init_py = Path(engine_dir) / "slcn" / "__init__.py"
        try:
            match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_py.read_text(encoding="utf-8"), re.M)
            if match:
                return match.group(1)
        except OSError:
            pass
    exe = find_engine_exe()
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


# ── 命令构造（与 sources-win/engine.py 同口径） ─────────────────────────
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
    if extra:
        args.extend(extra.split())
    return _call("convert.py", args, "初排转换")


def checkbib_call(*, returned: str, original: str = "", tex: str = "", merge_into: str = "", out: str = "") -> EngineCall:
    args = ["--returned", returned]
    _append_opt(args, "--original", original)
    _append_opt(args, "--tex", tex)
    _append_opt(args, "--merge-into", merge_into)
    _append_opt(args, "--out", out)
    return _call("checkbib.py", args, "文献回读校验")


def proofcheck_call(*, tex: str, fix: bool = False, lang: str = "") -> EngineCall:
    args = ["--tex", tex]
    _append_opt(args, "--lang", lang)
    _append_flag(args, "--fix", fix)
    return _call("proofcheck.py", args, "一校核查")


def infocheck_call(*, folder: str, preview: bool = False) -> EngineCall:
    args = ["--folder", folder]
    _append_flag(args, "--preview", preview)
    return _call("infocheck.py", args, "检查 tex 信息")


def toc_call(*, report: str, template: str = "", text_only: bool = False, result_json: str = "") -> EngineCall:
    args = ["--report", report]
    _append_opt(args, "--template", template)
    if not text_only:
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


def cleantemp_call(
    *, folder: str, dry_run: bool = False, trash: bool = False,
    restore_trash: str = "", mode: str = "aux", empty_trash: bool = False,
) -> EngineCall:
    args = ["--folder", folder]
    if restore_trash:
        args.extend(["--restore", restore_trash])
    elif empty_trash:
        args.append("--empty-trash")
        _append_flag(args, "--dry-run", dry_run)
    elif mode == "aux":
        _append_flag(args, "--dry-run", dry_run)
        _append_flag(args, "--trash", trash)
    else:
        args.extend(["--mode", mode])
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


def bibdb_conflicts_call(*, pending_only: bool = True) -> EngineCall:
    """待裁决清单（只读）：conflicts --json [--pending-only]。"""
    args = ["conflicts", "--json"]
    _append_flag(args, "--pending-only", pending_only)
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
    return _call("bibdb.py", args, "裁决预览" if dry_run else "记录裁决")


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
) -> int:
    """流式执行一次引擎调用；返回退出码。取消请调用方 kill_tree(proc)。"""
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
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **kwargs,
    )
    if on_proc is not None:
        on_proc(proc)
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
