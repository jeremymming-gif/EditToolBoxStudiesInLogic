"""按期锁内发布已核准的页码成品，恢复记录和旧文件放在期目录外。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import unicodedata

from .clean_workspaces import checked_stat, is_link, rename_exclusive
from . import issuewrite


class RolledBack(RuntimeError):
    """发布失败，已核对并还原所有原位目标。"""


class RecoveryRequired(RuntimeError):
    """有未完成的事务。保留诊断和备份，等待显式恢复。"""


def _sync_dir(path):
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _save(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=path.parent,
                                     prefix=".paging-", delete=False) as f:
        tmp = Path(f.name)
        try:
            json.dump(value, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
        _sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _load(path):
    info = path.lstat()
    if is_link(info) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"记录不是普通文件：{path}")
    return json.loads(path.read_text(encoding="utf8"))


def _digest(path, before=None):
    before = path.lstat() if before is None else before
    if is_link(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError(f"拒绝读取非独立普通文件：{path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(before, opened) or opened.st_nlink != 1:
            raise ValueError(f"文件身份在打开期间发生变化：{path}")
        result = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
    if (not os.path.samestat(after, path.lstat()) or
            (before.st_ctime_ns, before.st_size) != (after.st_ctime_ns, after.st_size)):
        raise ValueError(f"文件在读取期间发生变化：{path}")
    return result


def _id(info):
    return [info.st_dev, info.st_ino]


def _issue(issue):
    return issuewrite.issue_identity(Path(issue))


def _parent_stamp(issue, name):
    try:
        info = checked_stat(str(issue), name)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"目标父路径不是目录：{name}")
    return _id(info)


def _checked_relative(rel):
    if not isinstance(rel, str) or "\\" in rel or ":" in rel:
        raise ValueError(f"非法成品相对路径：{rel!r}")
    parts = rel.split("/")
    if len(parts) != 2 or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"成品必须为直属篇目或目录内文件：{rel}")
    parent, name = parts
    if parent.startswith("000"):
        if name not in {"Chinese Content.tex", "Chinese Content.pdf", "contents.tex", "contents.pdf"}:
            raise ValueError(f"不允许的目录成品：{rel}")
    elif not re.fullmatch(r"(?:0[1-9]|[1-9][0-9]) .+", parent) or Path(name).suffix.lower() not in {".tex", ".pdf"}:
        raise ValueError(f"不允许的篇目成品：{rel}")
    return parent


def _check_alias(folder, name):
    try:
        info = folder.lstat()
    except FileNotFoundError:
        return
    if is_link(info):
        raise ValueError(f"目标父路径不是实际目录：{folder}")
    if stat.S_ISDIR(info.st_mode):
        key = unicodedata.normalize("NFC", name).casefold()
        for item in os.listdir(folder):
            if item != name and unicodedata.normalize("NFC", item).casefold() == key:
                raise ValueError(f"目标存在大小写或 Unicode 冲突：{folder / item}")


def target_stamp(issue, rel):
    """在同一期内检查真实路径及文件身份。父目录未创建时仅允许计划中的目录产物。"""
    parent = _checked_relative(rel)
    _check_alias(issue, parent)
    parent_id = _parent_stamp(issue, parent)
    if parent_id is None and not parent.startswith("000"):
        raise ValueError(f"稿件目录不存在：{parent}")
    _check_alias(issue / parent, Path(rel).name)
    try:
        info = checked_stat(str(issue), rel)
    except FileNotFoundError:
        return dict(parent=parent_id, identity=None, sha=None)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"目标不是普通文件：{rel}")
    return dict(parent=parent_id, identity=_id(info), sha=_digest(issue / rel, info))


def _copy(source, dest, mode=None, *, created=None):
    with source.open("rb") as reader, dest.open("xb") as writer:
        if created is not None:
            created(_id(os.fstat(writer.fileno())))
        shutil.copyfileobj(reader, writer)
        if mode is not None:
            os.chmod(dest, stat.S_IMODE(mode))
        writer.flush()
        os.fsync(writer.fileno())
    _sync_dir(dest.parent)


def read_selection(issue, state):
    identity = _issue(issue)
    path, value, stored = issuewrite.locate_state_record(
        identity, state, "refresh-selection.json")
    if value is None:
        return None
    issuewrite.record_origin(identity, {"issue": stored})
    if not isinstance(value, dict):
        raise ValueError("已保存的编号排除清单不是 JSON 对象")
    return value


def numbered_directories(issue):
    """Record the identities of numbered article/auxiliary directories used by the merger."""
    from .pdfmerge import numbered_entries
    return {name: _parent_stamp(issue, name) for name in numbered_entries(str(issue))
            if not name.startswith("000")}


def merge_exclusions(issue, state):
    selection = read_selection(issue, state)
    if selection is None:
        return ()
    excluded = selection.get("excluded")
    directories = selection.get("directories")
    if (not isinstance(excluded, list) or not all(isinstance(name, str) for name in excluded)
            or selection.get("issue") != _issue(issue) or directories != numbered_directories(issue)
            or not set(excluded) <= directories.keys()):
        raise ValueError("已确认的编号目录发生变化，请重新预览整期刷新并核准篇目")
    return tuple(excluded)


def _set_selection(issue, state, value):
    identity = _issue(issue)
    old_path, old_value, stored = issuewrite.locate_state_record(
        identity, state, "refresh-selection.json")
    if old_value is not None:
        issuewrite.record_origin(identity, {"issue": stored})
    path = _record_path(issue, state).with_name("refresh-selection.json")
    if value is None:
        if old_value is not None:
            old_path.unlink(missing_ok=True)
            _sync_dir(old_path.parent)
    else:
        _save(path, value)
        if old_value is not None and old_path != path:
            old_path.unlink(missing_ok=True)
            _sync_dir(old_path.parent)


def _run_dir(issue, state, recovery_id):
    if not isinstance(recovery_id, str) or not re.fullmatch(r"[0-9a-f]{32}", recovery_id):
        raise ValueError("恢复 ID 无效")
    run = state / "runs" / recovery_id
    if run.resolve() != run or run.is_relative_to(issue) or not run.is_dir():
        raise ValueError("恢复工作区不安全或缺失")
    return run


def _temp(issue, rec, row, idx):
    return (issue / row["rel"]).parent / f".slcn-pages-{rec['recovery_id']}-{idx}.tmp"


def _install(tmp, dest, exists):
    if exists:
        os.replace(tmp, dest)
    else:
        rename_exclusive(str(tmp), str(dest))
    _sync_dir(dest.parent)


def _check_parent(issue, rec, rel):
    parent = _checked_relative(rel)
    expected = rec["parents"][parent]
    actual = _parent_stamp(issue, parent)
    if actual != expected:
        raise ValueError(f"目标父目录身份已变化：{parent}")


def _dir_temp(issue, run, rec, parent):
    # The directory is prepared outside the issue. Prefer durable state; on a
    # separate volume use the issue's parent so the final move remains atomic.
    base = issue.parent if rec.get("external_dir_stage") else run
    return base / f".slcn-dir-{rec['recovery_id']}-{rec['created_dirs'].index(parent)}"


def _temp_dir_id(path):
    info = path.lstat()
    if is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"目录暂存位置不安全：{path}")
    return _id(info)


def _record_path(issue, state):
    return issuewrite.record_path(_issue(issue), state)


def _journal(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    _save(path, record)
    _sync_dir(path.parent.parent)


def _candidate_copy(source, tmp, row, record, rec, field):
    def created(file_id):
        row[field] = file_id
        _journal(record, rec)
    _copy(source, tmp, row["mode"], created=created)


def _owned_file(row, found):
    if found["sha"] == row["old"]:
        return found["identity"] in (row["old_id"], row.get("restored_id"))
    return found["sha"] == row["new"] and found["identity"] == row.get("candidate_id")


def _drop_record(path):
    path.unlink(missing_ok=True)
    _sync_dir(path.parent)


def recover(issue: Path, state: Path, recovery_id: str):
    """调用方持有按期锁。利用实物与备份恢复，包括替换后尚未来得及记进度的情况。"""
    run = _run_dir(issue, state, recovery_id)
    identity = _issue(issue)
    record, rec, stored = issuewrite.locate_state_record(identity, state, "pending.json")
    if rec is None:
        raise ValueError("没有待恢复的页码记录")
    issuewrite.record_origin(identity, {"issue": stored})
    if (rec.get("operation") != "refreshpages" or rec.get("recovery_id") != recovery_id
            or rec.get("issue") != _issue(issue)):
        raise ValueError("恢复 ID 或期目录身份不符")
    try:
        for parent in rec["created_dirs"]:
            temp = _dir_temp(issue, run, rec, parent)
            known = rec.get("prepared_dirs", {}).get(parent)
            actual = _parent_stamp(issue, parent)
            if known and os.path.lexists(temp):
                if _temp_dir_id(temp) != known:
                    raise ValueError(f"新目录暂存身份无法核对：{temp}")
                temp.rmdir()
                _sync_dir(temp.parent)
            if actual and known == actual and rec["parents"][parent] is None:
                rec["parents"][parent] = actual
                _journal(record, rec)
        removing = rec.get("removing_dir")
        if removing and _parent_stamp(issue, removing) is None:
            rec["parents"][removing] = None
            _journal(record, rec)
        selection = read_selection(issue, state)
        if selection not in (rec["old_selection"], rec["selection"]):
            raise ValueError("篇目清单已被其他操作修改")
        # 先核对整组文件及备份，再开始逆操作。用户改动优先保留。
        for idx, row in enumerate(rec["files"]):
            rel = row["rel"]
            _check_parent(issue, rec, rel)
            found = target_stamp(issue, rel)
            if not _owned_file(row, found):
                raise ValueError(f"恢复冲突，成品已被外部修改：{rel}")
            if row["old"] is not None and _digest(run / f"backup-{idx}") != row["old"]:
                raise ValueError(f"恢复备份不匹配：{rel}")
            tmp = _temp(issue, rec, row, idx)
            if tmp.exists() or tmp.is_symlink():
                if (is_link(tmp.lstat()) or not tmp.is_file() or tmp.lstat().st_nlink != 1
                        or _id(tmp.lstat()) not in (row.get("candidate_id"), row.get("restored_id"))):
                    raise ValueError(f"临时文件状态需核对：{tmp}")
        for idx in reversed(range(len(rec["files"]))):
            row = rec["files"][idx]
            rel = row["rel"]
            _check_parent(issue, rec, rel)
            tmp = _temp(issue, rec, row, idx)
            tmp.unlink(missing_ok=True)
            found = target_stamp(issue, rel)
            if not _owned_file(row, found):
                raise ValueError(f"恢复中目标已变化：{rel}")
            if found["sha"] == row["old"]:
                continue
            dest = issue / rel
            if row["old"] is None:
                dest.unlink()
                _sync_dir(dest.parent)
            else:
                _candidate_copy(run / f"backup-{idx}", tmp, row, record, rec, "restored_id")
                if not _owned_file(row, target_stamp(issue, rel)):
                    raise ValueError(f"恢复中目标已变化：{rel}")
                _install(tmp, dest, True)
            if target_stamp(issue, rel)["sha"] != row["old"]:
                raise ValueError(f"恢复后哈希不一致：{rel}")
        for parent in rec["created_dirs"]:
            if rec["parents"][parent] is not None:
                _check_parent(issue, rec, f"{parent}/contents.pdf")
                # rmdir 拒绝删除含有其他文件的目录。
                rec["removing_dir"] = parent
                _journal(record, rec)
                (issue / parent).rmdir()
                _sync_dir(issue)
                rec["parents"][parent] = None
                _journal(record, rec)
        if selection != rec["old_selection"]:
            _set_selection(issue, state, rec["old_selection"])
        _drop_record(record)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise RecoveryRequired(f"{exc}；恢复记录：{record}") from exc


def publish(issue: Path, state: Path, run: Path, sources: dict, expected: dict, *, selection, verify):
    """验证阶段已结束；按预览清单建立持久备份，再逐项准备同卷临时文件并发布。"""
    if _run_dir(issue, state, run.name) != run or issuewrite.pending_issue_write(issue, state):
        raise ValueError("发布工作区无效或已有待恢复事务")
    identity = _issue(issue)
    existing_record, existing_value, stored = issuewrite.locate_state_record(identity, state, "pending.json")
    if existing_value is not None:
        issuewrite.record_origin(identity, {"issue": stored})
        raise ValueError("已有待恢复事务")
    rec = dict(schema=1, operation="refreshpages", issue=identity, recovery_id=run.name,
               parents={}, created_dirs=[], files=[], old_selection=read_selection(issue, state), selection=selection)
    if not sources.keys() <= expected.keys():
        raise ValueError("候选成品超出已确认的写入范围")
    selected = []
    for rel, source in sources.items():
        old = expected[rel]
        if target_stamp(issue, rel) != old:
            raise ValueError(f"发布前目标已变化：{rel}")
        if source.resolve() != source or not source.is_relative_to(run) or not source.is_file():
            raise ValueError(f"候选成品路径无效：{source}")
        digest = _digest(source)
        if old["sha"] == digest:
            continue
        idx = len(selected)
        parent = _checked_relative(rel)
        rec["parents"][parent] = old["parent"]
        mode = source.stat().st_mode
        if old["sha"] is not None:
            dest = issue / rel
            mode = dest.stat().st_mode
            backup = run / f"backup-{idx}"
            _copy(dest, backup, mode)
            if _digest(backup) != old["sha"]:
                raise ValueError(f"备份期间目标已变化：{rel}")
        rec["files"].append(dict(rel=rel, old=old["sha"], new=digest, mode=mode,
                                 old_id=old["identity"]))
        selected.append(source)
    rec["created_dirs"] = [parent for parent, identity in rec["parents"].items() if identity is None]
    rec["external_dir_stage"] = bool(rec["created_dirs"] and run.stat().st_dev != issue.stat().st_dev)
    record = _record_path(issue, state)
    _journal(record, rec)
    try:
        for parent in rec["created_dirs"]:
            temp = _dir_temp(issue, run, rec, parent)
            temp.mkdir()
            _sync_dir(temp.parent)
            rec.setdefault("prepared_dirs", {})[parent] = _temp_dir_id(temp)
            _journal(record, rec)
            rename_exclusive(str(temp), str(issue / parent))
            _sync_dir(temp.parent)
            _sync_dir(issue)
            rec["parents"][parent] = rec["prepared_dirs"][parent]
            _journal(record, rec)
        for idx, row in enumerate(rec["files"]):
            _check_parent(issue, rec, row["rel"])
            tmp = _temp(issue, rec, row, idx)
            _candidate_copy(selected[idx], tmp, row, record, rec, "candidate_id")
            if _digest(tmp) != row["new"]:
                raise ValueError(f"候选成品复制校验失败：{row['rel']}")
        for idx, row in enumerate(rec["files"]):
            rel = row["rel"]
            _check_parent(issue, rec, rel)
            found = target_stamp(issue, rel)
            if found["sha"] != row["old"] or found["identity"] != row["old_id"]:
                raise ValueError(f"提交中目标已变化：{rel}")
            _install(_temp(issue, rec, row, idx), issue / rel, row["old"] is not None)
            rec["installed"] = idx + 1
            _journal(record, rec)
        for rel, source in sources.items():
            if target_stamp(issue, rel)["sha"] != _digest(source):
                raise ValueError(f"落盘哈希不一致：{rel}")
        verify()
        if read_selection(issue, state) != rec["old_selection"]:
            raise ValueError("提交期间篇目清单已变化")
        if selection != rec["old_selection"]:
            _set_selection(issue, state, selection)
        _drop_record(record)
    except (Exception, KeyboardInterrupt) as exc:
        recover(issue, state, run.name)
        raise RolledBack(f"发布失败，已恢复旧成品：{exc}") from exc
    return [row["rel"] for row in rec["files"]]
