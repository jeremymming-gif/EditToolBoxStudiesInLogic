"""Per-issue write coordination and durable recovery records."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import local

from .clean_workspaces import is_link


class IssueBusyError(RuntimeError):
    pass


class IssuePendingError(RuntimeError):
    pass


_thread_locks = local()
_STATE_FILES = {"pending.json", "refresh-selection.json"}


def default_state_dir() -> Path:
    configured = os.environ.get("SLCN_ISSUE_STATE_DIR", "").strip()
    root = Path(configured) if configured else Path.home() / ".editorial-toolbox" / "issue-write"
    return root.expanduser().resolve()


def _canonical_path(path: Path) -> Path:
    """Return the on-disk spelling, including case and Unicode normalization."""
    resolved = path.expanduser().resolve(strict=True)
    current = Path(resolved.anchor)
    for part in resolved.parts[1:]:
        target = current / part
        target_info = target.lstat()
        matches = []
        with os.scandir(current) as entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if os.path.samestat(info, target_info):
                    matches.append(entry.name)
        if len(matches) != 1:
            raise OSError(f"无法唯一确认期目录的实际路径：{target}")
        current = current / matches[0]
    return current


def issue_identity(issue: Path) -> dict[str, object]:
    path = _canonical_path(issue)
    info = path.lstat()
    if is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise OSError("期目录不是安全的实际目录")
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino}


def _state_root(state_dir: Path, identity: dict[str, object]) -> Path:
    root = state_dir.expanduser().resolve()
    if root.is_relative_to(Path(str(identity["path"]))):
        raise OSError("期状态目录必须位于期目录之外")
    for path in (root, *root.parents):
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        if (info.st_dev, info.st_ino) == (identity["device"], identity["inode"]):
            raise OSError("期状态目录必须位于期目录之外")
    return root


def _physical_key(identity: dict[str, object]) -> str:
    return f"v2-{int(identity['device']):x}-{int(identity['inode']):x}"


def _path_key(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]


def _legacy_key(path: str) -> str:
    return _path_key(path)


def _folder(identity: dict[str, object], state_dir: Path) -> Path:
    return _state_root(state_dir, identity) / "issues" / _physical_key(identity)


def record_path(identity: dict[str, object], state_dir: Path) -> Path:
    """Return the v2 path used for new issue recovery records."""
    return _folder(identity, state_dir) / "pending.json"


def _state_directory(root: Path) -> Path | None:
    path = root / "issues"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"期状态目录不安全：{path}")
    return path


def _read_state_file(path: Path) -> dict:
    info = path.lstat()
    if is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError(f"期状态文件不安全：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"期状态文件不是 JSON 对象：{path}")
    return value


def _record_identity(record: dict) -> dict | None:
    identity = record.get("issue")
    if not isinstance(identity, dict):
        return None
    path = identity.get("path")
    device = identity.get("device")
    inode = identity.get("inode")
    if (not isinstance(path, str) or not Path(path).is_absolute()
            or isinstance(device, bool) or not isinstance(device, int)
            or isinstance(inode, bool) or not isinstance(inode, int)):
        return None
    return {"path": path, "device": device, "inode": inode}


def _record_path_matches(identity: dict, record_identity: dict) -> bool:
    """Whether a record belongs to this physical issue or its replaced path."""
    try:
        original = Path(str(record_identity["path"]))
        info = original.stat()
    except OSError:
        return False
    if (info.st_dev, info.st_ino) == (identity["device"], identity["inode"]):
        return True
    # A transaction tied to a path must continue blocking if that path now
    # names a replacement directory. Recovery will reject its changed inode.
    try:
        return _canonical_path(original) == Path(str(identity["path"]))
    except OSError:
        return False


def _valid_slot_identity(folder: str, record: dict) -> dict | None:
    stored = _record_identity(record)
    if stored is None:
        return None
    if folder.startswith("v2-"):
        expected = _physical_key(stored)
    else:
        expected = _legacy_key(str(stored["path"]))
    return stored if folder == expected else None


def locate_state_record(identity: dict[str, object], state_dir: Path, filename: str):
    """Locate one current or legacy state file without moving or creating it."""
    if filename not in _STATE_FILES:
        raise ValueError(f"未知的期状态文件：{filename}")
    root = _state_root(state_dir, identity)
    issues = _state_directory(root)
    current_path = Path(str(identity["path"]))
    folders = []
    v2 = _physical_key(identity)
    legacy = _legacy_key(str(current_path))
    if issues is not None:
        for entry in os.scandir(issues):
            if entry.name != v2 and entry.name != legacy and not (
                    len(entry.name) == 24 and all(c in "0123456789abcdef" for c in entry.name)) and not entry.name.startswith("v2-"):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise OSError(f"期状态槽不安全：{entry.path}")
            path = Path(entry.path) / filename
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            folders.append((entry.name, path))

    matches = []
    damaged = []
    for folder, path in folders:
        try:
            record = _read_state_file(path)
        except (OSError, ValueError, json.JSONDecodeError):
            if folder in {v2, legacy}:
                damaged.append(path)
            continue
        stored = _valid_slot_identity(folder, record)
        if stored is None:
            if folder in {v2, legacy}:
                damaged.append(path)
            continue
        same_inode = (stored["device"], stored["inode"]) == (
            identity["device"], identity["inode"])
        if same_inode or _record_path_matches(identity, stored):
            matches.append((path, record, stored))

    if damaged:
        paths = ", ".join(str(path) for path in damaged)
        raise OSError(f"期状态文件损坏或身份不符，已阻止操作：{paths}")
    if len(matches) > 1:
        paths = ", ".join(str(path) for path, _, _ in matches)
        raise OSError(f"本期存在多个未协调的状态文件，已阻止操作：{paths}")
    if matches:
        path, record, stored = matches[0]
        return path, record, stored
    return _folder(identity, state_dir) / filename, None, None


def record_origin(identity: dict[str, object], record: dict) -> Path:
    """Validate that recovery still targets the issue at its original path."""
    stored = _record_identity(record)
    if stored is None:
        raise OSError("恢复记录中的期目录身份无效")
    original = Path(str(stored["path"]))
    try:
        current = Path(original.anchor)
        info = None
        for part in original.parts[1:]:
            current = current / part
            info = current.lstat()
            if is_link(info):
                raise OSError(f"原期目录路径经过链接，不能自动恢复：{current}")
    except OSError as exc:
        raise OSError(f"原期目录位置不可用，不能自动恢复：{original}") from exc
    assert info is not None
    if is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"原期目录位置已被替换为链接或非目录：{original}")
    canonical = _canonical_path(original)
    if (canonical != Path(str(identity["path"]))
            or (info.st_dev, info.st_ino) != (identity["device"], identity["inode"])
            or (stored["device"], stored["inode"]) != (identity["device"], identity["inode"])):
        raise OSError(f"期目录已移动或被替换，不能自动恢复：{original}")
    return canonical


def pending_issue_write(issue: Path, state_dir: Path) -> dict | None:
    identity = issue_identity(issue)
    try:
        path, record, stored = locate_state_record(identity, state_dir, "pending.json")
    except (OSError, ValueError) as exc:
        return {"status": "corrupt", "path": str(record_path(identity, state_dir)),
                "error": str(exc)}
    if record is None:
        return None
    body = dict(record)
    body["path"] = str(path)
    if (stored["device"], stored["inode"]) != (identity["device"], identity["inode"]):
        body["status"] = "stale"
    return body


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _lock_paths(identity: dict[str, object], state_dir: Path) -> list[Path]:
    root = _state_root(state_dir, identity) / "locks"
    physical = root / f"{_physical_key(identity)}.lock"
    path = root / f"path-{_path_key(str(identity['path']))}.lock"
    return sorted({physical, path}, key=lambda value: str(value))


@contextmanager
def issue_write_lock(issue: Path, state_dir: Path, *, allow_nested: bool = False):
    """Acquire physical and canonical-path locks for one issue."""
    identity = issue_identity(issue)
    paths = _lock_paths(identity, state_dir)
    held = getattr(_thread_locks, "held", None)
    if held is None:
        held = _thread_locks.held = {}
    keys = [(os.getpid(), path) for path in paths]
    existing = [(key, held[key]) for key in keys if key in held]
    if existing:
        if (len(existing) != len(keys) or any(value != identity for _, value in existing)):
            raise OSError("期目录身份在持锁期间发生变化")
        yield identity
        return

    lock_dir = paths[0].parent
    lock_dir.mkdir(parents=True, exist_ok=True)
    if is_link(lock_dir.lstat()):
        raise OSError(f"期锁目录不安全：{lock_dir}")
    fds = []
    locked_keys = []
    try:
        for path in paths:
            if path.is_symlink() or path.is_junction():
                raise OSError(f"期锁文件不安全：{path}")
            fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            fds.append(fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(f"期锁文件不安全：{path}")
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise IssueBusyError("本期正在执行其他写入操作") from exc
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise IssueBusyError("本期正在执行其他写入操作") from exc
            locked_keys.append((os.getpid(), path))
        if issue_identity(issue) != identity:
            raise OSError("期目录身份在加锁期间发生变化")
        if allow_nested:
            for key in keys:
                held[key] = identity.copy()
        yield identity
    finally:
        for key in locked_keys:
            held.pop(key, None)
        for fd in reversed(fds):
            os.close(fd)


@contextmanager
def issue_write_guard(issue: Path, *, allow_nested: bool = False):
    """Block ordinary writers until any interrupted transaction is recovered."""
    state = default_state_dir()
    with issue_write_lock(issue, state, allow_nested=allow_nested):
        pending = pending_issue_write(issue, state)
        if pending is not None:
            operation = pending.get("operation", "未知操作")
            recovery_id = pending.get("recovery_id", "")
            detail = pending.get("error") or ""
            suffix = f"；{detail}" if detail else ""
            raise IssuePendingError(
                f"本期存在待恢复写入（{operation}，恢复 ID {recovery_id}）；"
                f"请先使用对应恢复命令完成恢复{suffix}"
            )
        yield
