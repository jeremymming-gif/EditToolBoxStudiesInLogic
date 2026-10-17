"""原子写入 + 旧版自动备份（UI 方案第 4 件）。

**要解决的问题**：编辑最怕「以为存上了其实没有」——进程被杀、磁盘满、断电时，
`open(path, "w")` 会先把旧文件截断成 0 字节，再写一半就死在中间，产物变成**半截**。
这也是「完成行说成功、文件却是坏的」这类事故的技术根因。

**做法**：先写同目录的 `xxx.tmp-<pid>`，`flush` + `fsync` 之后再 `os.replace()`。
POSIX `rename(2)` 保证「一个指向新文件的 link 在整个重命名过程中对其他进程可见」——
读者永远只看到**完整的旧版**或**完整的新版**，不会看到半截。
替换前把旧产物复制成 `xxx.<时间戳>.bak`，每个文件只留最近 `KEEP_BAK` 份。

**不做**（故意）：不备份到别的目录（要同一文件系统才能原子替换）；不备份 `.py`
（那是源码，归 git 管）；不覆盖用户在产物上的手工改动时静默——备份路径会写进日志。
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
from collections.abc import Callable

#: 每个产物最多留几份旧版（再多就是垃圾；UI 方案里定的是 3）
KEEP_BAK = 3


def _stamp() -> str:
    """定长时间戳（到微秒）。

    为什么带微秒：只到秒的话，同一秒内连写两次会**撞同一个备份名**（第二次把第一次的
    备份盖掉 → 实际只剩 1 份）。定长且左对齐，所以字典序仍然等于时间序，`prune_backups`
    的排序判据不变。2026-10-17 被自己的测试抓出来的。
    """
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def prune_backups(path: str, keep: int = KEEP_BAK) -> None:
    """只留最近 `keep` 份 `<path>.<时间戳>.bak`（时间戳定长，字典序就是时间序）。"""
    folder = os.path.dirname(os.path.abspath(path))
    base = os.path.basename(path)
    try:
        names = sorted(f for f in os.listdir(folder)
                       if f.startswith(base + ".") and f.endswith(".bak"))
    except OSError:
        return
    for name in names[:-keep] if keep > 0 else names:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def backup(path: str, keep: int = KEEP_BAK,
           log: Callable[[str], None] | None = None) -> str:
    """把**已存在**的 `path` 复制成 `<path>.<时间戳>.bak`；返回备份路径（没有旧文件则 ""）。"""
    if not os.path.exists(path):
        return ""
    bak = f"{path}.{_stamp()}.bak"
    try:
        shutil.copy2(path, bak)
    except OSError:
        return ""
    prune_backups(path, keep)
    if log:
        log(f"   （旧版已备份：{os.path.basename(bak)}）")
    return bak


def atomic_write(path: str, text: str, *, encoding: str = "utf-8",
                 newline: str | None = "\n", keep_backups: int = KEEP_BAK,
                 log: Callable[[str], None] | None = None) -> str:
    """原子写文本文件；替换前自动备份旧版。返回备份路径（首写/无旧版则 ""）。

    ⚠️ 临时文件必须与目标**同目录**（`os.replace` 要求同一文件系统）。
    ⚠️ `newline` 透传（本项目产物一律 LF：`newline="\\n"`），别改成平台默认。
    """
    bak = backup(path, keep=keep_backups, log=log)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding=encoding, newline=newline) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())          # 先落盘，再替换（断电也不留半截）
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):            # 失败时别留垃圾（旧文件此时仍然完好）
            try:
                os.remove(tmp)
            except OSError:
                pass
    return bak
