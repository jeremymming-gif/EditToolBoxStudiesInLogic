"""跨平台小事：UTF-8 标准输出、运行环境描述、外部工具定位（第 86 条）。

为什么要单开一个模块：引擎要在 **macOS 与 Windows 上跑同一份逻辑**，
凡「和操作系统打交道」的东西都收在这里，别散到各处各写一份——
散着写的结果一定是「Mac 上好好的、Windows 上某个分支没改到」。

**不是**要做平台抽象层：只有四件小事，加起来不到 60 行。
"""

from __future__ import annotations

import os
import platform as _platform
import shutil
import sys
from pathlib import Path


def utf8_stdio() -> None:
    """把 `stdout` / `stderr` 切到 UTF-8（**幂等、永不抛**）。

    为什么非做不可：Windows 的 `sys.stdout` 默认用**控制台代码页**（简体中文机器上是
    cp936）。我们的日志里全是中文，一旦被 shell 用管道接走，轻则乱码、重则
    `UnicodeEncodeError` 直接崩在半路（`print` 抛异常 = 转换中断）。
    macOS/Linux 本来就是 UTF-8，这里是**无副作用的空操作**。

    `errors="replace"` 是刻意的：**宁可某个字变成 `?`，也不能因为一个字符让整篇稿子转不出来**。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001  被重定向成非文本流时没有 reconfigure
            pass


def describe() -> str:
    """一行运行环境，给界面/排查用：`Windows 11 (AMD64)，Python 3.12.4`。"""
    return (f"{_platform.system()} {_platform.release()} "
            f"({_platform.machine()})，Python {_platform.python_version()}")


def engine_version() -> str:
    """引擎版本号（唯一出处是 `slcn/__init__.py` 的 `__version__`）。

    为什么要绕这一道：`checkbib.py` 的 import 白名单是**封闭**的
    （`tests/test_checkbib.py` ⑤ 组盯着「可证明纯离线」），
    多用 `from slcn import __version__` 就会多出一个 `slcn.__version__` 条目。
    收进这里之后，`checkbib.py` 只需要认识 `slcn.platform` 一个模块。
    """
    try:
        from slcn import __version__ as v   # 延迟导入：避免包初始化期的半成品状态
        return v
    except Exception:  # noqa: BLE001  理论上到不了这儿；真到了也别让版本号把命令弄崩
        return "0.0"


#: LibreOffice 的常见安装位置。`shutil.which` 找不到时的兜底 ——
#: **三个平台都要列**：Windows 的安装器默认**不**把 soffice 加进 PATH，
#: macOS 的 .app 同理（`/opt/homebrew/bin/soffice` 只是 brew 装的软链）。
SOFFICE_CANDIDATES: tuple[str, ...] = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
)


def find_soffice() -> str | None:
    """定位 LibreOffice 的 `soffice`；找不到返回 `None`（调用方自己决定怎么提示）。

    顺序：**环境变量 `SLCN_SOFFICE`**（给装在奇怪位置的人一个出口）→ `PATH` → 常见安装位置。
    """
    override = os.environ.get("SLCN_SOFFICE", "").strip()
    if override and os.path.exists(override):
        return override
    found = shutil.which("soffice")
    if found:
        return found
    for cand in SOFFICE_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return None


def file_uri(path: str) -> str:
    """本地路径 → `file://` URI。

    ⚠️ 为什么要专门做：LibreOffice 的 `-env:UserInstallation=` **只认 URI**，
    而 Windows 的 `C:\\Users\\…` 直接拼成 `file://C:\\Users\\…` 是**错的**
    （盘符要写成 `file:///C:/…`、反斜杠要正斜杠）。`Path.as_uri()` 两个平台都对。
    """
    return Path(path).resolve().as_uri()
