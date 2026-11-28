"""Collect the ordering draft from direct, exactly same-name article TeX files."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from pathlib import Path
import stat
from typing import Iterator

from .clean_workspaces import SKIP_DIRS, boundary, identity, is_link
from .pushcopy.collect import CollectError, article_title_authors


@dataclass(frozen=True)
class OrderCandidate:
    folder: str
    state: str


@dataclass(frozen=True)
class OrderRow:
    folder: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    state: str = "ok"


@dataclass(frozen=True)
class _Source:
    path: Path
    directory: os.stat_result
    file: os.stat_result


class _UnsafeEntry(OSError):
    pass


def _scan(issue_dir: Path) -> Iterator[tuple[OrderCandidate, _Source | None]]:
    root = issue_dir.lstat()
    if is_link(root) or not stat.S_ISDIR(root.st_mode):
        raise NotADirectoryError(f"期目录须为实际目录：{issue_dir}")
    with os.scandir(issue_dir) as entries:
        folders = sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name))
    for folder in folders:
        name = folder.name
        # The issue TOC and PDF merger also reserve legacy 000-prefixed names.
        if name.startswith((".", "000")) or name.casefold() in SKIP_DIRS:
            continue
        source = None
        path = issue_dir / name
        try:
            directory = path.lstat()
            if is_link(directory):
                raise _UnsafeEntry()
            if not stat.S_ISDIR(directory.st_mode):
                continue
            if boundary(directory, path, root.st_dev):
                raise _UnsafeEntry()
            # 精确同名 = 去掉「数字+空白」排序前缀后的名字（01 San_Zhang → San_Zhang）。
            # 剥离后的裸名优先命中；旧式带前缀 tex 名（01 San_Zhang.tex）作兜底，兼容历史稿件。
            stripped = re.sub(r"^\d+\s+", "", name, count=1)
            tex_names = [f"{stripped}.tex"]
            if stripped != name:
                tex_names.append(f"{name}.tex")
            # Iteration checks the filesystem spelling even on case-insensitive volumes.
            with os.scandir(path) as entries:
                present = {entry.name for entry in entries}
                for tex_name in tex_names:
                    if tex_name in present:
                        info = (path / tex_name).lstat()
                        if not _regular(info) or info.st_dev != directory.st_dev:
                            raise _UnsafeEntry()
                        source = _Source(path / tex_name, directory, info)
                        break
            state = "ok" if source is not None else "missing_tex"
        except _UnsafeEntry:
            state = "unsafe_entry"
        except OSError:
            state = "unreadable"
        yield OrderCandidate(name, state), source


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not is_link(info) and info.st_nlink == 1


def list_order_candidates(issue_dir: Path) -> list[OrderCandidate]:
    """Return folder names and TeX availability without opening any TeX for content."""
    return [candidate for candidate, _ in _scan(issue_dir)]


def _check_directory(source: _Source, actual: os.stat_result) -> None:
    if is_link(actual) or not os.path.samestat(source.directory, actual):
        raise _UnsafeEntry()


def _read_tex(source: _Source) -> str:
    folder = source.path.parent
    _check_directory(source, folder.lstat())
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0))
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        # Pin the manuscript directory across the open, including if renamed concurrently.
        directory_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _check_directory(source, os.fstat(directory_fd))
            fd = os.open(source.path.name, flags, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
    else:
        if identity(source.file) != identity(source.path.lstat()):
            raise _UnsafeEntry()
        fd = os.open(source.path, flags)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not _regular(before) or identity(before) != identity(source.file):
            raise _UnsafeEntry()
        data = stream.read()
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise OSError("读取期间 TeX 文件发生变化")
    _check_directory(source, folder.lstat())
    return data.decode("utf-8-sig")


def collect_order_rows(issue_dir: Path) -> list[OrderRow]:
    """Return every candidate, with title and byline for the safely readable TeX files."""
    rows = []
    for candidate, source in _scan(issue_dir):
        if source is None:
            rows.append(OrderRow(candidate.folder, state=candidate.state))
            continue
        try:
            text = _read_tex(source)
        except _UnsafeEntry:
            rows.append(OrderRow(candidate.folder, state="unsafe_entry"))
        except (OSError, UnicodeError):
            rows.append(OrderRow(candidate.folder, state="unreadable"))
        else:
            title, authors = article_title_authors(text, candidate.folder)
            state = "ok" if title and authors else "missing_field"
            rows.append(OrderRow(candidate.folder, title, authors, state))
    return rows


def order_docx_path(issue_dir: Path, out_root: Path) -> Path:
    """Resolve the output and reject paths that would write into the current issue."""
    root = out_root.resolve()
    issue = issue_dir.stat()
    if root.is_relative_to(issue_dir):
        raise CollectError(f"排序稿输出根不能放在期目录内：{root}")
    for path in (root, *root.parents):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if os.path.samestat(issue, info):
            raise CollectError(f"排序稿输出根不能放在期目录内：{root}")
        if is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise CollectError(f"排序稿输出根须为目录：{root}")

    target = root / f"{issue_dir.name}排序决策稿.docx"
    try:
        info = target.lstat()
    except FileNotFoundError:
        return target
    if not _regular(info):
        raise CollectError(f"排序稿输出目标不是可替换的普通文件：{target}")
    return target
