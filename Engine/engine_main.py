#!/usr/bin/env python3
"""把十个 CLI 冻成**一个** exe 的入口（第 89 条，给 Windows 发布包用）。

    SLCNEngine.exe convert.py   --source "<稿件.docx>" --out "<输出目录>" --compile
    SLCNEngine.exe checkbib.py  --returned "<交回.bib>" --merge-into "<产物 reference.bib>"
    SLCNEngine.exe mergepdf.py  --folder "<期目录>" --editors-page --nest-outlines
    SLCNEngine.exe proofcheck.py --folder "<期目录>"
    SLCNEngine.exe --version

**为什么要它**：Windows 上不该要求编辑先装 Python。把十个入口冻成一个
`SLCNEngine.exe`（PyInstaller `--onefile`），Tkinter 外壳直接调这个 exe
（见 `sources-win/engine.py: frozen_engine_exe()`），用户机器上零依赖。

**为什么不是八个 exe**：冻结一次要几十 MB，八个 exe 就是八份运行时。

⚠️ **`ENTRIES` 必须与外壳的 `sources-win/engine.py: ENTRIES` 逐项一致**，而且
`build-win.ps1` 的 `--hidden-import` 与自检都要按它逐项走。2026-10-17 实测：
这张表停留在"三个入口"（89 条时的规模），而外壳已经长到 8 个入口 ——
于是**发布的 Windows 包里 9 个标签页有 5 个直接失败**（「❌ 不认识的入口」退出 2），
而桥接测试那句"打包 dispatcher 认这几个"只比了外壳自己的表（**假保证**）。
配对守卫在 `sources-win/tests/test_bridge.py` (b) 组，打包自检在 `build-win.ps1`。
"""

from __future__ import annotations

import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

#: 允许通过本 exe 调用的入口（键 = 命令行里写的名字）。
#: 顺序与 `sources-win/engine.py: ENTRIES` 相同 —— 那条一致性由测试守着，别只改一边。
#: 第 132 条加了 `bibdb.py`（引用库）：mac 包与 Windows 包共用同一份 CLI，
#: Windows「引用库」5 页（库概况/查库/入藏/待裁决/设置）全靠它。
ENTRIES = ("convert.py", "checkbib.py", "mergepdf.py",
           "cleantemp.py", "compileall.py", "proofcheck.py",
           "infocheck.py", "toc.py", "publish.py", "bibdb.py")

USAGE = f"""SLCN 引擎（打包版）

用法：SLCNEngine.exe <入口> [参数…]

入口：
  {"  ".join(ENTRIES)}

例：
  SLCNEngine.exe convert.py  --source "稿件.docx" --out "输出目录" --compile
  SLCNEngine.exe mergepdf.py --folder "2026年第5期(90)" --editors-page
  SLCNEngine.exe --version

每个入口自己的参数表：SLCNEngine.exe <入口> --help
"""


def main(argv: list[str] | None = None) -> int:
    # ⚠️ **第一件事**就是把 stdout/stderr 切成 UTF-8：Windows 控制台默认用**代码页**
    #    （英文机 cp1252、中文机 cp936），而下面每一行提示都带中文 —— 不先切，
    #    一句 `SLCNEngine.exe --version` 就会 `UnicodeEncodeError` 崩掉。
    #    2026-10-16 windows-latest 打包自检实测：两个 exe 都打出来了，自检崩在这里
    #    （CI 上 `PYTHONIOENCODING=utf-8` 恰好遮住了它，**用户的机器上没有这个变量**）。
    #    其余入口（convert.py / checkbib.py / …）一直有这一行，漏的只有本文件 ——
    #    静态守卫见 `tests/test_platform.py` 的 (a) 组。
    from slcn import platform as _plat
    _plat.utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "/?"):
        print(USAGE)
        return 0 if argv else 2
    if argv[0] in ("--version", "-V"):
        print(f"SLCN 引擎 {_plat.engine_version()}（打包版）｜ {_plat.describe()}")
        return 0

    entry, rest = argv[0], argv[1:]
    if entry not in ENTRIES:
        print(f"❌ 不认识的入口：{entry}\n\n{USAGE}", file=sys.stderr)
        return 2

    if entry == "convert.py":
        import convert
        # convert.main() 读的是 sys.argv（不接参数），所以这里替它摆好
        sys.argv = ["convert.py", *rest]
        return convert.main()
    # 其余入口的签名统一是 `main(argv=None)`（checkbib / mergepdf / cleantemp /
    # compileall / proofcheck / infocheck / toc），所以按名字动态 import 即可 ——
    # 一个一个 if 下去，下次加入口就会漏掉一个（这正是本文件踩过的坑）。
    mod = importlib.import_module(entry[:-3])
    return mod.main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
