"""Directory units for daily cleanup. Never follow workspace links or reparse points."""

from __future__ import annotations

import ctypes
import json
import os
import re
import stat
import sys
from contextlib import contextmanager

from . import clean
from . import paths

TMP_DIR = ".toolbox-tmp"
AI_DIR = ".aiw"
SOURCE_FINGERPRINT = ".slcn-源指纹.json"
BIB_BACKUP = re.compile(r"reference\.bib\.[0-9]{8}-[0-9]{6}-[0-9]{6}\.bak")
SKIP_DIRS = frozenset({".git", ".svn", ".hg", "__pycache__", clean.TRASH_DIR, *clean.SKIP_DIRS})


def is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def skip_dir(name: str) -> bool:
    return name.lower() in SKIP_DIRS or bool(paths.ISSUE_RE.match(name))


def protected_dir(name: str) -> bool:
    low = name.lower()
    return any(mark in low for mark in ("backup", "history", "备份", "历史", "待核")) or low.endswith(
        ("_bak", "-bak", ".bak")
    )


def boundary(st: os.stat_result, path: str, device: int) -> bool:
    return st.st_dev != device or (stat.S_ISDIR(st.st_mode) and os.path.ismount(path))


def identity(st: os.stat_result) -> tuple:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def checked_stat(root: str, rel: str) -> os.stat_result:
    """Check each component before inspecting the next, including Windows junctions."""
    current = root
    device = os.lstat(root).st_dev
    parts = rel.split("/")
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        st = os.lstat(current)
        if (is_link(st) or boundary(st, current, device)
                or (index < len(parts) - 1 and not stat.S_ISDIR(st.st_mode))):
            raise OSError(f"路径包含链接或非目录组件：{current}")
    return st


def path_problem(root: str, rel: str, label: str) -> str:
    if os.name == "nt" and "\\" in rel:
        return f"{label}路径含反斜杠"
    try:
        checked_stat(root, rel)
    except FileNotFoundError:
        pass
    except OSError as exc:
        return f"{label}越界或路径不安全：{exc}"
    return ""


def snapshot(folder: str, stop) -> dict[str, tuple]:
    """Snapshot metadata for a whole unit, rejecting anything we cannot safely move."""
    items: dict[str, tuple] = {}
    device = os.lstat(folder).st_dev

    def walk(full: str, rel: str) -> None:
        stop()
        st = os.lstat(full)
        if is_link(st) or boundary(st, full, device):
            raise OSError(f"包含链接或挂载点：{rel or '.'}")
        items[rel] = identity(st)
        if stat.S_ISREG(st.st_mode):
            return
        if not stat.S_ISDIR(st.st_mode):
            raise OSError(f"包含非普通文件：{rel or '.'}")
        with os.scandir(full) as entries:
            children = sorted(entry.name for entry in entries)
        for name in children:
            name_low = name.lower()
            child = os.path.join(full, name)
            if (name_low == SOURCE_FINGERPRINT or skip_dir(name) or name_low.endswith(AI_DIR)
                    or (protected_dir(name) and stat.S_ISDIR(os.lstat(child).st_mode))):
                raise OSError(f"包含受保护路径：{name}")
            walk(child, f"{rel}/{name}" if rel else name)

    walk(folder, "")
    if not stat.S_ISDIR(items[""][2]):
        raise OSError("工作区已不是目录")
    return items


def _object(path: str) -> dict:
    if not stat.S_ISREG(checked_stat(os.path.dirname(path), os.path.basename(path)).st_mode):
        raise OSError("状态文件不是普通文件")
    with open(path, encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("状态文件不是 JSON 对象")
    return value


def ai_retention(folder: str) -> tuple[str, str] | None:
    # Import the workflow only for actual AI workspaces.
    from . import ai_workflow as workflow
    from .ai_workflow_convert import JOURNAL_FILENAME

    invalid = ("ai-invalid", "AI 工作区状态或交付回执缺失、损坏或互不一致")
    unknown = ("ai-unknown", "无法确认 AI 工作区已结束（保留）")
    try:
        state = _object(os.path.join(folder, workflow.STATE_NAME))
    except (OSError, ValueError, RecursionError):
        return invalid
    phase = state.get("phase")
    request = state.get("request_status")
    if state.get("pending_apply") is not None or phase == workflow.PH_APPLYING:
        return ("ai-pending", "AI 工作区有待应用或中断写入（保留）")
    if request == "in_flight" or phase in (
        workflow.PH_PREPARED, workflow.PH_REQUESTING, workflow.PH_IMPORTED, workflow.PH_PREVIEWED,
        workflow.PH_APPLIED, workflow.PH_BLOCKED, workflow.PH_CANCELLED, workflow.PH_STALE, "undone",
    ) or state.get("undo"):
        return ("ai-active", "AI 任务尚可继续或需要保留撤销数据")
    if (state.get("schema_version") != workflow.SCHEMA_VERSION_R3
            or state.get("entry") != workflow.ENTRY_CONVERT or phase != workflow.PH_DELIVERED
            or request not in (None, "", "succeeded")):
        return unknown
    task_id = state.get("task_id")
    parent = os.path.dirname(folder)
    local_paths = {os.path.normcase(parent), os.path.normcase(os.path.realpath(parent))}
    work_dir = state.get("work_dir")
    conversion = state.get("conversion")
    if (not isinstance(task_id, str) or not task_id or not isinstance(work_dir, str)
            or os.path.normcase(os.path.normpath(work_dir)) not in local_paths
            or not isinstance(conversion, dict)):
        return invalid
    try:
        journal = _object(os.path.join(folder, JOURNAL_FILENAME))
        shas = journal.get("shas")
        final = journal.get("final")
        if (journal != state.get("delivery") or journal.get("task_id") != task_id
                or journal.get("ok") is not True or journal.get("delivered") is not True
                or journal.get("promoting") is not False or journal.get("undone")
                or not isinstance(final, str) or not os.path.isabs(final)
                or final != conversion.get("final_dir") or not isinstance(journal.get("backup"), str)
                or not isinstance(shas, dict) or not shas
                or not all(isinstance(sha, str) and (re.fullmatch(r"[0-9a-f]{64}", sha)
                           or (path.endswith("/") and sha == "")) for path, sha in shas.items())):
            return invalid
    except (OSError, ValueError, RecursionError):
        return invalid
    return None

@contextmanager
def idle_ai(folder: str, *, for_rename: bool = False):
    """Yield whether the existing workflow lock is idle, never create it."""
    from .ai_workflow_state_v3 import request_lock

    work_dir = os.path.dirname(folder)
    try:
        st = checked_stat(folder, "request.lock")
        if not stat.S_ISREG(st.st_mode):
            yield False
            return
    except FileNotFoundError:
        yield True
        return
    # Windows cannot rename a directory containing our own open file handle.
    # Check there immediately before the final state/tree recheck and rename.
    with request_lock(work_dir, existing_only=True) as idle:
        if os.name != "nt" or not for_rename:
            yield idle
            return
    yield idle


def ensure_parent(root: str, rel: str) -> None:
    """Create only validated directory components, refusing links and junctions."""
    device = os.lstat(root).st_dev
    for part in rel.split("/")[:-1]:
        root = os.path.join(root, part)
        try:
            os.mkdir(root)
        except FileExistsError:
            st = os.lstat(root)
            if is_link(st) or not stat.S_ISDIR(st.st_mode) or boundary(st, root, device):
                raise OSError(f"目标父目录不安全：{root}")

def rename_exclusive(src: str, dst: str) -> None:
    """Same-device rename that fails even if an empty destination races us."""
    if os.name == "nt":
        os.rename(src, dst)  # Windows os.rename already has no-replace semantics.
        return
    system = ctypes.CDLL(None, use_errno=True)
    source, target = os.fsencode(src), os.fsencode(dst)
    if sys.platform == "darwin":
        result = system.renamex_np(source, target, ctypes.c_uint(0x00000004))  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(system, "renameat2"):
        result = system.renameat2(-100, source, -100, target, ctypes.c_uint(1))  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError("当前平台不支持安全的工作区独占重命名")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), dst)
