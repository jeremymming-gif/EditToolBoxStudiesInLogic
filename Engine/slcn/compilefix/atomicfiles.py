"""Native, same-filesystem replacement that retains the actual displaced inode."""

from contextlib import contextmanager
import ctypes
import errno
import os
from pathlib import Path
import sys
import tempfile

from .files import RepairError, directory


_RENAME_SWAP = 0x00000002  # Darwin <sys/stdio.h>


@contextmanager
def _open_parent(path):
    parent = directory(path)
    expected = parent.stat()
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not os.path.samestat(expected, os.fstat(fd)):
            raise RepairError("打开原子替换目录时身份变化")
        yield fd
    finally:
        os.close(fd)


def exchange(target: Path, staged: Path):
    """Swap two existing names in a single native operation, or raise."""
    if sys.platform != "darwin":
        raise OSError(errno.ENOTSUP, "本平台未支持产物原子交换")
    try:
        rename = ctypes.CDLL(None, use_errno=True).renameatx_np
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, "系统缺少产物原子交换接口") from exc
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    with _open_parent(target.parent) as target_fd, _open_parent(staged.parent) as staged_fd:
        if rename(target_fd, os.fsencode(target.name), staged_fd, os.fsencode(staged.name), _RENAME_SWAP) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), target)


def preflight(out_dir: Path, *, probe_dir: Path):
    """Verify native exchange and creation on the output filesystem, outside its tree."""
    if sys.platform != "darwin":
        raise RepairError("本平台未支持产物原子交换，未启动产物事务")
    out_dir = directory(out_dir)
    probe_dir = directory(probe_dir, private=True)
    if out_dir.stat().st_dev != probe_dir.stat().st_dev:
        raise RepairError("私有任务区与产物目录不在同一文件系统，无法预检原子交换")
    try:
        with tempfile.TemporaryDirectory(prefix="atomic-preflight-", dir=probe_dir) as workspace:
            work = Path(workspace)
            stage = directory(work / "stage", create=True, private=True)
            target, staged = work / "target", stage / "staged"
            target.write_bytes(b"old")
            staged.write_bytes(b"new")
            old, new = target.stat(), staged.stat()
            exchange(target, staged)
            if not (os.path.samestat(new, target.stat()) and os.path.samestat(old, staged.stat())):
                raise RepairError("文件系统原子交换预检未保留两个文件身份")
            os.link(staged, work / "created")
    except OSError as exc:
        raise RepairError("文件系统未通过原子交换或新建预检，未启动产物事务") from exc
