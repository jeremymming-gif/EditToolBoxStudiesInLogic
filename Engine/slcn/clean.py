"""删除 LaTeX 编译中间文件。

⚠️ 本模块刻意与 EditToolBox 的 `sources-mac/Features/DeleteTempFeature.swift` **行为完全一致**，
以便将来把本工具并入 EditToolBox 时两边不会打架。对齐的细节包括：

  · 后缀列表逐项相同（含 macOS 新增的 `.DS_Store`）
  · 递归遍历整棵目录树
  · 后缀**两边都转小写**再比，所以列表里可以写成易读的 `.DS_Store`
  · **故意不删 `.log`** —— 编译出错时要靠它排查；工具箱的「删除临时文件」也不删
  · 日志文案相同：`Deleted: <相对路径>` / `删除完成，共删除N个文件。`

不删的还有 `.tex` / `.pdf` / `.sty` / `.bib`。

**第三种模式（2026-10-17 加）：移入回收目录，可撤销。**
`trash()` 不删，而是把中间文件搬到 `<目录>/.toolbox-trash/<时间戳>/` 并写 `manifest.json`；
`restore()` 按 manifest 搬回去。为什么不用系统回收站：Windows 上 stdlib 没有回收站 API
（`SHFileOperation` 要 ctypes + COM），自建回收目录跨平台一致、可逆、可审计。
UI 方案第 3 件：编辑主动点的破坏性动作，**能撤销才敢点**（HIG：能撤销就别弹 alert）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
from collections.abc import Callable

from . import paths as P

# 与 EditToolBox DeleteTempFeature.suffixes 逐项一致
CLEAN_SUFFIXES = [
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".out",
    ".run.xml",
    ".synctex.gz",
    ".DS_Store",
]

# 明确声明「不删」的，防止以后有人手滑加进去
KEEP_SUFFIXES = [".tex", ".pdf", ".sty", ".bib", ".log"]


def matches(name: str) -> bool:
    """判断文件名是否属于待清理的中间文件。"""
    low = name.lower()
    return any(low.endswith(s.lower()) for s in CLEAN_SUFFIXES)


#: **永不进入**的目录名（小写比较）：`original/` 是作者来稿归档，红线是"只读"
#: （`docs/INVARIANTS.md` 一.1、仓库根 `AGENTS.md` 红线 1）。
#: 为什么非拦不可：清理器的后缀表里有 `.bbl` / `.out` / `.bcf` 这些 ——
#: 作者交 LaTeX 稿时 `original/` 里**真的可能有同名文件**，删掉就是丢作者的东西。
#: 2026-10-17 在真期目录上实测：`--dry-run` 列出 5 个待删文件，其中 **3 个在 `original/` 里**
#: （清一色 `.DS_Store`）—— 那已经是在动"只读"目录了。
SKIP_DIRS = ("original",)

#: 本工具自建的回收目录名（放在被清理目录的根下）。遍历时必须一起跳过 ——
#: 否则「移入回收 → 再清理」会把回收目录里的东西当中间文件再搬一次（嵌套回收）。
TRASH_DIR = ".toolbox-trash"


def clean(
    root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """递归删除 root 下的编译中间文件。

    返回 (删除数量, 被删文件的相对路径列表)。
    log 传入一个可调用对象即可把过程写进界面日志（与 EditToolBox 的 Logger 对应）。
    """
    emit = log or (lambda _s: None)

    if not os.path.isdir(root):
        emit(f"路径不是文件夹：{root}")
        return 0, []

    deleted: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # 红线：`original/` 一律不进（剪掉分支，等于整棵子树都不看）
        # 回收目录也不进：防止「移入回收 → 再清理」把回收的东西再搬一遍
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in SKIP_DIRS and d != TRASH_DIR]
        for fname in filenames:
            if not matches(fname):
                continue
            full = os.path.join(dirpath, fname)
            rel = P.rel(full, root)
            try:
                total += os.path.getsize(full)
            except OSError:
                pass
            if dry_run:
                emit(f"Will delete: {rel}")
                deleted.append(rel)
                continue
            try:
                os.remove(full)
                emit(f"Deleted: {rel}")
                deleted.append(rel)
            except OSError as e:
                emit(f"Error deleting {rel}: {e}")

    if dry_run:
        emit(f"（预演）将删除 {len(deleted)} 个文件，共 {_human_size(total)}。")
    else:
        emit(f"删除完成，共删除{len(deleted)}个文件。")
    return len(deleted), deleted


def _human_size(n: int) -> str:
    """给编辑看的体积（确认框里说"多大"比说"确定吗"有用得多）。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y_%m_%d_%H%M%S")


def trash(
    root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> tuple[int, str]:
    """把中间文件**移入** `<root>/.toolbox-trash/<时间戳>/`（保留相对目录结构）。

    返回 (文件数, 回收目录)。回收目录里另写 `manifest.json`（原始相对路径），供 `restore()`。

    与 `clean()` 的三点不同：① 不 `os.remove`，而是 `shutil.move`；② 建回收目录；
    ③ 目录结构保留（`01 A/xx.aux` → `.toolbox-trash/<ts>/01 A/xx.aux`），还原时不用猜。
    """
    emit = log or (lambda _s: None)
    if not os.path.isdir(root):
        emit(f"路径不是文件夹：{root}")
        return 0, ""
    trash_root = os.path.join(root, TRASH_DIR, _stamp())
    moved: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in SKIP_DIRS and d != TRASH_DIR]
        for fname in filenames:
            if not matches(fname):
                continue
            full = os.path.join(dirpath, fname)
            rel = P.rel(full, root)
            try:
                total += os.path.getsize(full)
            except OSError:
                pass
            if dry_run:
                emit(f"Will move: {rel}")
                moved.append(rel)
                continue
            target = os.path.join(trash_root, rel)
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(full, target)
                emit(f"Moved: {rel}")
                moved.append(rel)
            except OSError as e:
                emit(f"Error moving {rel}: {e}")
    if dry_run:
        emit(f"（预演）将移入回收目录 {len(moved)} 个文件，共 {_human_size(total)}。")
        return len(moved), trash_root
    if moved:
        os.makedirs(trash_root, exist_ok=True)
        with open(os.path.join(trash_root, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"root": root, "createdAt": _stamp(), "files": moved},
                      fh, ensure_ascii=False, indent=2)
    emit(f"已移入回收目录 {len(moved)} 个文件。")
    return len(moved), trash_root if moved else ""


def restore(
    root: str,
    trash_root: str,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """按 `manifest.json` 把回收目录里的文件搬回 `<root>`；返回 (恢复数, 恢复的相对路径)。

    ⚠️ 原位置**已经有同名文件时不覆盖**（跳过并记一条）—— 还原不能变成第二次破坏。
    """
    emit = log or (lambda _s: None)
    manifest = os.path.join(trash_root, "manifest.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        emit(f"读不到回收清单：{e}")
        return 0, []
    files = [str(x) for x in data.get("files", [])]
    done: list[str] = []
    for rel in files:
        src = os.path.join(trash_root, rel)
        dst = os.path.join(root, rel)
        if not os.path.exists(src):
            emit(f"Missing in trash: {rel}")
            continue
        if os.path.exists(dst):
            emit(f"Skip (已经存在，不覆盖): {rel}")
            continue
        if dry_run:
            emit(f"Will restore: {rel}")
            done.append(rel)
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            emit(f"Restored: {rel}")
            done.append(rel)
        except OSError as e:
            emit(f"Error restoring {rel}: {e}")
    if dry_run:
        emit(f"（预演）将恢复 {len(done)} 个文件。")
    else:
        emit(f"恢复完成，共恢复{len(done)}个文件。")
    return len(done), done
