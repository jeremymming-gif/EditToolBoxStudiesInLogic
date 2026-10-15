"""删除 LaTeX 编译中间文件。

⚠️ 本模块刻意与 EditToolBox 的 `sources-mac/Features/DeleteTempFeature.swift` **行为完全一致**，
以便将来把本工具并入 EditToolBox 时两边不会打架。对齐的细节包括：

  · 后缀列表逐项相同（含 macOS 新增的 `.DS_Store`）
  · 递归遍历整棵目录树
  · 后缀**两边都转小写**再比，所以列表里可以写成易读的 `.DS_Store`
  · **故意不删 `.log`** —— 编译出错时要靠它排查；工具箱的「删除临时文件」也不删
  · 日志文案相同：`Deleted: <相对路径>` / `删除完成，共删除N个文件。`

不删的还有 `.tex` / `.pdf` / `.sty` / `.bib`。
"""

from __future__ import annotations

import os
from collections.abc import Callable

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
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            if not matches(fname):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root)
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
        emit(f"（预演）将删除 {len(deleted)} 个文件。")
    else:
        emit(f"删除完成，共删除{len(deleted)}个文件。")
    return len(deleted), deleted
