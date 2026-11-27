"""Bounded local I/O and private filesystem state for compile repair."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import sys
import tempfile


TEXT_LIMIT = 4 * 1024 * 1024
REPLY_LIMIT = 64 * 1024
ARTIFACT_LIMIT = 64 * 1024 * 1024
ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
DENIED_COMPONENTS = frozenset({"studies_in_logic_database", "edittoolboxstudiesinlogic-manuscripts"})


class RepairError(Exception):
    """An anticipated failure with a message safe to display locally."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RepairError("JSON 含重复字段")
        value[key] = item
    return value


def decode_json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError) as exc:
        raise RepairError("无法解析 UTF-8 JSON") from exc


def absolute(path, *, allow_source_area=False):
    path = Path(os.path.abspath(path))
    if sys.platform == "darwin" and len(path.parts) > 1 and path.parts[1] in {"tmp", "var"}:
        # Canonicalize only the fixed system aliases, without following user links.
        path = Path("/private", *path.parts[1:])
    if not allow_source_area and any(part.casefold() in DENIED_COMPONENTS or re.match(r"^\d{4}年第", part)
                                     for part in path.parts):
        raise RepairError("路径属于受保护源区")
    return path


def within(path, directory):
    return path == directory or directory in path.parents


def stem_name(stem):
    if (not stem or stem[0] in ".-" or stem[-1] in ". "
            or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', stem)
            or getattr(os.path, "isreserved", lambda _name: False)(stem)):
        raise RepairError("候选文件名不安全")
    return stem


def _is_link(info):
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def directory(path, *, private=False, create=False, parents=False, allow_source_area=False):
    path = absolute(path, allow_source_area=allow_source_area)
    # Walk from the anchor so a symlink cannot escape into a protected tree.
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if not create or (part != path and not parents):
                raise
            try:
                part.mkdir(mode=0o700)
            except FileExistsError:
                pass
            info = part.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise RepairError("目录路径包含符号链接或非目录")
    if private and os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise RepairError("状态目录须由当前用户拥有，权限不得宽于 0700")
    return path


def directory_id(path, *, allow_source_area=False):
    info = directory(path, allow_source_area=allow_source_area).stat()
    return [info.st_dev, info.st_ino]


def directory_ancestry(path, *, allow_source_area=False):
    """Collect existing directory identities up to a missing or unsafe component."""
    path = absolute(path, allow_source_area=allow_source_area)
    identities = []
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            return identities, None
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            return identities, None
        identities.append((info.st_dev, info.st_ino))
    return identities, identities[-1]


def regular(path, *, optional=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return None
        raise
    if _is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RepairError("拒绝链接、共享 inode 或非普通文件")
    return info


def read(path, limit=TEXT_LIMIT, *, optional=False):
    info = regular(path, optional=optional)
    if info is None:
        return None
    if info.st_size > limit:
        raise RepairError("本地文件超过读取上限")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        actual = os.fstat(stream.fileno())
        if not os.path.samestat(info, actual) or actual.st_nlink != 1:
            raise RepairError("读取时文件身份变化")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if (len(data) > limit or actual.st_size != after.st_size
                or actual.st_mtime_ns != after.st_mtime_ns or len(data) != after.st_size):
            raise RepairError("读取时文件内容变化或超限")
    return data


def read_json(path, *, optional=False):
    data = read(path, REPLY_LIMIT, optional=optional)
    if data is None:
        return None
    record = decode_json(data)
    if type(record) is not dict:
        raise RepairError("本地状态 JSON 必须是对象")
    return record


def write_stdout(data):
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write(path, data, *, mode=0o600):
    regular(path, optional=True)
    fd, temporary = tempfile.mkstemp(prefix=".compilefix-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def put_json(path, value):
    atomic_write(path, json_bytes(value))


@contextmanager
def lock(path, *, directory_lock=False):
    if directory_lock and os.name != "nt":
        flags = os.O_RDONLY | os.O_DIRECTORY
    else:
        regular(path, optional=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not os.path.samestat(os.fstat(fd), path.lstat()):
            raise RepairError("取得锁时目标身份变化")
        yield fd
    finally:
        os.close(fd)
