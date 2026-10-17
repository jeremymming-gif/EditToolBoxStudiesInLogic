#!/usr/bin/env python3
"""删除编译临时文件（对应 mac 版的「删除临时文件」，第 91 条搬进引擎）。

    python3 cleantemp.py --folder "<期目录或任意目录>" [--dry-run] [--trash] [--version]
    python3 cleantemp.py --folder "<期目录>" --restore "<.toolbox-trash/时间戳 目录>"

两种"真动手"的方式：
  · **默认**：直接删除（与老版/mac 版逐字节一致的旧行为，外壳不传 `--trash` 时走这条）；
  · `--trash`：移入 `<目录>/.toolbox-trash/<时间戳>/`（可撤销，`--restore` 搬回来）。
    新界面用这条（UI 方案第 3 件：能撤销才敢点）。

删哪些：`.aux .bbl .bcf .blg .out .run.xml .synctex.gz` 与 macOS 的 `.DS_Store`。
**不删**：`.tex .pdf .sty .bib .log`（`.log` 是排错用的，故意留着）。
名单与判据在 `slcn/clean.py`（与 mac 那份 Swift 逐项一致）。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import clean as _clean  # noqa: E402
from slcn import platform as _plat  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="cleantemp.py",
        description="递归删除目录下的 LaTeX 编译临时文件（不删 .tex / .pdf / .log）",
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--folder", required=True, metavar="目录", help="【必填】要清理的目录（递归）")
    ap.add_argument("--dry-run", action="store_true", help="只列出会删哪些，不真删")
    ap.add_argument("--trash", action="store_true",
                    help="移入回收目录（<目录>/.toolbox-trash/<时间戳>/），可用 --restore 撤销")
    ap.add_argument("--restore", metavar="回收目录", default="",
                    help="把某个回收目录里的文件搬回原处（不覆盖已存在的文件）")
    args = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(root):
        print(f"\n❌ 路径不是文件夹：{root}\n", file=sys.stderr)
        return 2

    if args.restore:
        trash_root = os.path.abspath(os.path.expanduser(args.restore))
        if not os.path.isdir(trash_root):
            print(f"\n❌ 回收目录不存在：{trash_root}\n", file=sys.stderr)
            return 2
        print("撤销清理（从回收目录搬回）\n")
        print(f"路径：{root}\n")
        n, _files = _clean.restore(root, trash_root, log=lambda s: print(s),
                                   dry_run=args.dry_run)
        print()
        verb = "将恢复" if args.dry_run else "已恢复"
        print(f"完成。{verb} {n} 个文件（目录：{root}）")
        return 0

    print("删除临时文件\n")
    print(f"路径：{root}\n")
    if args.trash:
        n, trash_root = _clean.trash(root, log=lambda s: print(s), dry_run=args.dry_run)
        print()
        # 契约行：外壳靠它拿「移了几个」与「回收目录在哪」（撤销按钮要用）
        verb = "将移入回收目录" if args.dry_run else "已移入回收目录"
        print(f"完成。{verb} {n} 个临时文件（回收目录：{trash_root or '（无可清理文件）'}）")
        return 0

    n, _files = _clean.clean(root, log=lambda s: print(s), dry_run=args.dry_run)

    print()
    # 契约行（`docs/ENGINE_API.md`）：外壳靠它拿「删了几个」
    verb = "将删除" if args.dry_run else "已删除"
    print(f"完成。{verb} {n} 个临时文件（目录：{root}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
