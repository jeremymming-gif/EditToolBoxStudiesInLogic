"""Strict issue-order validation and recoverable directory renaming."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import unicodedata

from .clean_workspaces import boundary, is_link, rename_exclusive
from . import issuewrite
from .orderdraft import list_order_candidates

_NUMBERED = re.compile(r"^\d{2} (.+)$")


@dataclass(frozen=True)
class OrderInput:
    ordered: tuple[str, ...]
    excluded: tuple[str, ...]


@dataclass(frozen=True)
class Rename:
    old: str
    new: str
    main_tex: str


@dataclass(frozen=True)
class RenamePlan:
    issue: dict[str, object]
    plan_id: str
    ordered: tuple[str, ...]
    excluded: tuple[str, ...]
    renames: tuple[Rename, ...]
    fingerprint: str
    directories: dict[str, tuple[int, int]]
    warnings: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    @property
    def can_apply(self) -> bool:
        return not self.conflicts


@dataclass(frozen=True)
class RenameResult:
    status: str
    renames: tuple[Rename, ...] = ()
    recovery_id: str | None = None
    recovery_path: str | None = None
    conflicts: tuple[str, ...] = ()
    recovery_operation: str | None = None
    directories: dict[str, tuple[int, int]] | None = None


def _name(name: str) -> str:
    if not isinstance(name, str) or not name or name in {".", ".."}:
        raise ValueError("顺序包含无效目录名")
    if any(char in name for char in '/\\:\x00'):
        raise ValueError("顺序包含路径而非直属目录名")
    return name


def _fold(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _main_tex(issue: Path, folder: str) -> str:
    path = issue / folder
    info = path.lstat()
    if is_link(info) or not stat.S_ISDIR(info.st_mode) or boundary(info, path, issue.lstat().st_dev):
        raise ValueError(f"来源不是安全的直属目录：{folder}")
    numbered = _NUMBERED.match(folder)
    stem = numbered.group(1) if numbered else folder
    tex_name = f"{stem}.tex"
    tex = path / tex_name
    try:
        with os.scandir(path) as candidates:
            if not any(item.name == tex_name for item in candidates):
                raise FileNotFoundError(tex_name)
        tex_info = tex.lstat()
    except OSError as exc:
        raise ValueError(f"缺少精确同名主 TeX：{folder}") from exc
    if (is_link(tex_info) or not stat.S_ISREG(tex_info.st_mode)
            or tex_info.st_nlink != 1 or tex_info.st_dev != info.st_dev):
        raise ValueError(f"主 TeX 不是常规文件：{folder}")
    return tex.name


def _fingerprint(issue: Path, names: list[str], tex_names: dict[str, str]) -> str:
    parts = []
    for name in names:
        directory = issue / name
        st = directory.lstat()
        tex_stamp = None
        if name in tex_names:
            tex = directory / tex_names[name]
            tex_stat = tex.lstat()
            tex_stamp = (tex_stat.st_dev, tex_stat.st_ino, tex_stat.st_mode,
                         tex_stat.st_nlink, tex_stat.st_size, tex_stat.st_mtime_ns, tex_stat.st_ctime_ns)
        parts.append((name, st.st_dev, st.st_ino, st.st_mode, st.st_mtime_ns, tex_stamp))
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_rename_plan(issue_dir: Path, order: OrderInput) -> RenamePlan:
    issue = Path(issue_dir).resolve(strict=True)
    identity = issuewrite.issue_identity(issue)
    candidates = list_order_candidates(issue)
    names = [candidate.folder for candidate in candidates]
    directory_ids = {}
    for name in names:
        try:
            directory_ids[name] = _directory_id(issue / name, identity["device"])
        except OSError as exc:
            if name in order.excluded:
                info = (issue / name).lstat()
                directory_ids[name] = (info.st_dev, info.st_ino)
                continue
            conflicts = [f"直属目录身份无法确认：{name}: {exc}"]
            return RenamePlan(identity, "", tuple(order.ordered), tuple(order.excluded), (), "",
                              {}, (), tuple(conflicts))
    warnings, conflicts = [], []
    ordered = tuple(_name(x) for x in order.ordered)
    excluded = tuple(_name(x) for x in order.excluded)
    if len(ordered) != len(set(ordered)) or len(excluded) != len(set(excluded)):
        conflicts.append("顺序或排除清单含重复目录")
    if set(ordered) & set(excluded):
        conflicts.append("同一目录不能同时排序和排除")
    if set(ordered) | set(excluded) != set(names):
        conflicts.append("顺序与排除清单必须完整覆盖所有直属稿件目录")
    numbered = [n for n in names if re.match(r"^\d{2} ", n)]
    if len(numbered) != len(set(re.match(r"^(\d{2}) ", n).group(1) for n in numbered)):
        warnings.append("现有编号有重复，已按编辑确认的顺序重新编号")
    if len(ordered) > 99:
        conflicts.append("两位序号最多支持 99 篇")
    tex_names: dict[str, str] = {}
    for folder in (name for name in ordered if name in names):
        try:
            tex_names[folder] = _main_tex(issue, folder)
        except (OSError, ValueError) as exc:
            conflicts.append(str(exc))
    renames = []
    for index, folder in enumerate(ordered, 1):
        match = _NUMBERED.match(folder)
        body = match.group(1) if match else folder
        renames.append(Rename(folder, f"{index:02d} {body}", tex_names.get(folder, f"{body}.tex")))
    targets: dict[str, str] = {}
    for rename in renames:
        folded = _fold(rename.new)
        if folded in targets:
            conflicts.append(f"目标名称冲突：{rename.new}")
        targets[folded] = rename.old
    moving = {r.old for r in renames if r.old != r.new}
    occupied = {}
    for item in issue.iterdir():
        if item.name not in moving:
            occupied.setdefault(_fold(item.name), []).append(item.name)
    for rename in renames:
        if any(name != rename.old for name in occupied.get(_fold(rename.new), ())):
            conflicts.append(f"目标已被本批次外项目占用：{rename.new}")
    fingerprint = _fingerprint(issue, names, tex_names)
    raw = json.dumps([identity, ordered, excluded, [r.__dict__ for r in renames],
                      directory_ids, fingerprint],
                     ensure_ascii=False, sort_keys=True).encode("utf-8")
    plan_id = hashlib.sha256(raw).hexdigest()
    return RenamePlan(identity, plan_id, ordered, excluded, tuple(renames), fingerprint, directory_ids,
                      tuple(warnings), tuple(dict.fromkeys(conflicts)))


def _directory_id(path: Path, device: int) -> tuple[int, int]:
    info = path.lstat()
    if is_link(info) or not stat.S_ISDIR(info.st_mode) or boundary(info, path, device):
        raise OSError(f"目录身份不安全：{path.name}")
    return info.st_dev, info.st_ino


def _rollback(issue: Path, record: dict) -> None:
    device = record["issue"]["device"]
    moves = [(_name(source), _name(target), tuple(move_id))
             for (source, target), move_id in zip(record["moves"], record["move_ids"], strict=True)]
    positions = {}
    for source, _, move_id in moves:
        positions.setdefault(move_id, source)
    # Each atomic move has a unique set of positions. This recognizes a crash
    # between a rename and the journal write, including midway through recovery.
    occupants = {item.name for item in issue.iterdir()}
    actual = {}
    touched = {name for source, target, _ in moves for name in (source, target)}
    for name in touched & occupants:
        try:
            move_id = _directory_id(issue / name, device)
        except OSError:
            continue
        if move_id in positions:
            actual[move_id] = name
    applied = []
    for source, target, move_id in moves:
        if positions == actual:
            break
        if positions[move_id] != source:
            raise ValueError("恢复记录中的目录移动不连续")
        positions[move_id] = target
        applied.append((source, target, move_id))
    if positions != actual:
        raise ValueError("恢复冲突，无法确认原稿件目录当前所在位置")

    for source, target, _ in reversed(applied):
        occupants.remove(target)
        if any(_fold(name) == _fold(source) for name in occupants):
            raise OSError(f"恢复目标被其他项目占用：{source}")
        occupants.add(source)
    for source, target, move_id in reversed(applied):
        if _directory_id(issue / target, device) != move_id:
            raise OSError(f"恢复前目录身份已变化：{target}")
        rename_exclusive(str(issue / target), str(issue / source))


def _execute_renames(identity: dict, renames: tuple[Rename, ...], issue: Path,
                     state_dir: Path, confirmed_ids: dict[str, tuple[int, int]]) -> RenameResult:
    operations = [(r.old, r.new) for r in renames if r.old != r.new]
    source_names = {_fold(old) for old, _ in operations}
    has_cycle = any(_fold(new) in source_names for _, new in operations)
    if has_cycle:
        token = secrets.token_hex(8)
        staged = [(old, f".toolbox-order-{token}-{i:02d}", new)
                  for i, (old, new) in enumerate(operations)]
        moves = [(old, temp) for old, temp, _ in staged] + [(temp, new) for _, temp, new in staged]
    else:
        moves = operations
    recovery_id = secrets.token_hex(12)
    record_path = issuewrite.record_path(identity, state_dir)
    source_ids = dict(confirmed_ids)
    for old, _ in operations:
        if source_ids.get(old) != _directory_id(issue / old, identity["device"]):
            return RenameResult("blocked", renames, conflicts=(f"改名来源身份已变化：{old}",))
    if has_cycle:
        for old, temp, _ in staged:
            source_ids[temp] = source_ids[old]
    record = {"schema": 1, "operation": "renamefolders", "recovery_id": recovery_id,
              "issue": identity, "moves": moves, "completed": 0,
              "move_ids": [source_ids[source] for source, _ in moves]}
    issuewrite.write_record(record_path, record)
    try:
        for index, (source, target) in enumerate(moves, 1):
            if _directory_id(issue / source, identity["device"]) != source_ids[source]:
                raise OSError(f"改名来源身份已变化：{source}")
            rename_exclusive(str(issue / source), str(issue / target))
            if _directory_id(issue / target, identity["device"]) != source_ids[source]:
                raise OSError(f"改名后目录身份与已确认来源不符：{target}")
            record["completed"] = index
            issuewrite.write_record(record_path, record)
    except (OSError, KeyboardInterrupt) as exc:
        try:
            _rollback(issue, record)
        except (OSError, ValueError) as rollback_exc:
            return RenameResult("recovery_required", renames, recovery_id, str(record_path),
                                (str(exc), str(rollback_exc)), "renamefolders")
        record_path.unlink(missing_ok=True)
        return RenameResult("rolled_back", renames, conflicts=(str(exc),))
    record_path.unlink(missing_ok=True)
    renamed = {rename.old: rename.new for rename in renames}
    final_ids = {renamed.get(name, name): move_id for name, move_id in confirmed_ids.items()}
    return RenameResult("completed", renames, directories=final_ids)


def apply_rename_plan(plan: RenamePlan, *, plan_id: str, state_dir: Path) -> RenameResult:
    if not plan.can_apply or plan_id != plan.plan_id:
        return RenameResult("blocked", plan.renames, conflicts=("改名计划无效或已过期",))
    issue = Path(str(plan.issue["path"]))
    if all(r.old == r.new for r in plan.renames):
        pending = issuewrite.pending_issue_write(issue, state_dir)
        if not pending:
            return RenameResult("unchanged", plan.renames,
                                directories={r.new: plan.directories[r.old] for r in plan.renames}
                                | {name: move_id for name, move_id in plan.directories.items()
                                   if name in plan.excluded})
        return RenameResult("recovery_required", plan.renames, pending.get("recovery_id"),
                            pending.get("path"), ("本期存在待恢复写入",), pending.get("operation"))
    with issuewrite.issue_write_lock(issue, state_dir) as identity:
        if identity != plan.issue:
            return RenameResult("blocked", plan.renames, conflicts=("期目录身份已变化",))
        pending = issuewrite.pending_issue_write(issue, state_dir)
        if pending:
            return RenameResult("recovery_required", plan.renames, pending.get("recovery_id"),
                                pending.get("path"), ("本期存在待恢复写入",), pending.get("operation"))
        refreshed = build_rename_plan(issue, OrderInput(plan.ordered, plan.excluded))
        if not refreshed.can_apply:
            return RenameResult("blocked", plan.renames, conflicts=refreshed.conflicts)
        if refreshed.plan_id != plan.plan_id:
            return RenameResult("blocked", plan.renames, conflicts=("期目录内容自预览后发生变化，请重新预览",))
        if refreshed.directories != plan.directories:
            return RenameResult("blocked", plan.renames, conflicts=("期目录身份自预览后发生变化，请重新预览",))
        return _execute_renames(identity, plan.renames, issue, state_dir, plan.directories)


def recover_issue_rename(issue: Path, recovery_id: str, state_dir: Path) -> RenameResult:
    with issuewrite.issue_write_lock(issue, state_dir) as identity:
        record = issuewrite.pending_issue_write(issue, state_dir) or {}
        path = Path(record.get("path", issuewrite.record_path(identity, state_dir)))
        if record.get("operation") != "renamefolders" or record.get("recovery_id") != recovery_id:
            return RenameResult("recovery_required", recovery_id=record.get("recovery_id"),
                                recovery_path=str(path), conflicts=("恢复编号或操作不匹配",),
                                recovery_operation=record.get("operation"))
        try:
            issuewrite.record_origin(identity, record)
        except OSError as exc:
            return RenameResult("recovery_required", recovery_id=recovery_id,
                                recovery_path=str(path), conflicts=(str(exc),),
                                recovery_operation="renamefolders")
        issue_path = Path(str(identity["path"]))
        try:
            _rollback(issue_path, record)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return RenameResult("recovery_required", recovery_id=recovery_id,
                                recovery_path=str(path), conflicts=(str(exc),),
                                recovery_operation="renamefolders")
        path.unlink(missing_ok=True)
        return RenameResult("rolled_back", recovery_id=recovery_id)
