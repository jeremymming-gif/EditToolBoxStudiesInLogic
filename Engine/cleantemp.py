#!/usr/bin/env python3
"""删除编译临时文件（对应 mac 版的「删除临时文件」，第 91 条搬进引擎）。

    python3 cleantemp.py --folder "<期目录或任意目录>" [--dry-run] [--trash] [--version]
    python3 cleantemp.py --folder "<期目录>" --restore "<.toolbox-trash/时间戳 目录>"
    python3 cleantemp.py --folder "<目录>" --mode {delivery,workspace} [--dry-run] [--delete]
    python3 cleantemp.py --folder "<期目录>" --empty-trash

两种"真动手"的方式（aux 模式，旧行为）：
  · **默认**：直接删除（与老版/mac 版逐字节一致的旧行为，外壳不传 `--trash` 时走这条）；
  · `--trash`：移入 `<目录>/.toolbox-trash/<目录名>-<时间戳>/`（可撤销，`--restore` 搬回来）。

B-211 起新增两种可撤销模式（默认移入回收站，`--delete` 才永久删）：
  · `--mode delivery`：交付目录收尾（先预检 tex 引用闭包；白名单优先；未识别保留）；
  · `--mode workspace`：工作区旧件（每期 `合并*.pdf` 只留最新；固定交付物永不删）；
  · `--empty-trash`：清空 `<目录>/.toolbox-trash/` 下的回收目录。

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


def _die(msg: str) -> int:
    print(f"\n❌ {msg}\n", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="cleantemp.py",
        description="递归删除目录下的 LaTeX 编译临时文件（不删 .tex / .pdf / .log）；"
                    "B-211 起另有交付收尾 / 工作区旧件两种可撤销模式",
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--folder", required=True, metavar="目录", help="【必填】要清理的目录（递归）")
    ap.add_argument("--dry-run", action="store_true", help="只列出会删哪些，不真删")
    ap.add_argument("--trash", action="store_true",
                    help="aux 模式：移入回收目录（<目录>/.toolbox-trash/<目录名>-<时间戳>/），"
                         "可用 --restore 撤销")
    ap.add_argument("--restore", metavar="回收目录", default="",
                    help="把某个回收目录里的文件搬回原处（不覆盖已存在的文件；兼容旧 manifest）")
    ap.add_argument("--mode", choices=("aux", "delivery", "workspace"), default="aux",
                    help="aux=编译中间文件（默认，旧行为）；delivery=交付目录收尾；"
                         "workspace=工作区旧件（合并 PDF 只留最新）")
    ap.add_argument("--delete", action="store_true",
                    help="delivery/workspace 模式：永久删除（默认移入回收站）")
    ap.add_argument("--empty-trash", action="store_true",
                    help="清空 <目录>/.toolbox-trash/ 下的所有回收目录")
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

    if args.mode != "aux" and args.trash:
        return _die("--trash 只用于 aux 模式；delivery/workspace 默认就移入回收站（永久删用 --delete）")
    if args.trash and args.delete:
        return _die("--trash 与 --delete 不能同时用（一个移入回收站、一个永久删）")

    if args.empty_trash:
        print("清空回收站\n")
        print(f"路径：{root}\n")
        n = _clean.empty_trash(root, log=lambda s: print(s), dry_run=args.dry_run)
        print()
        verb = "将清空" if args.dry_run else "已清空"
        print(f"完成。{verb}回收站 {n} 个回收目录（目录：{root}）")
        return 0

    if args.mode == "aux":
        return _run_aux(root, args)

    failures: list[tuple[str, str]] = []
    if args.mode == "delivery":
        try:
            _files, _dirs, missing = _clean.delivery_closure(root)
        except _clean.DeliveryError as e:
            return _die(str(e))
        if missing:  # 预检失败：整体拒绝、零写入
            print(f"\n❌ 引用缺失 {len(missing)} 项（整体拒绝，未写入任何文件）：",
                  file=sys.stderr)
            for m in missing:
                print(f"  - {m}", file=sys.stderr)
            print("", file=sys.stderr)
            return 2
        print("交付目录收尾清理\n")
        print(f"路径：{root}\n")
        n, trash_root = _clean.delivery_clean(
            root, log=lambda s: print(s), dry_run=args.dry_run, delete=args.delete,
            failures=failures)
    else:
        print("工作区旧件清理\n")
        print(f"路径：{root}\n")
        n, trash_root = _clean.workspace_clean(
            root, log=lambda s: print(s), dry_run=args.dry_run, delete=args.delete,
            failures=failures)

    if failures:
        # 有失败项：不打成功契约行（外壳据此判「已完成」），非零退出码。
        print(f"\n❌ 清理未完成：成功 {n} 项，失败 {len(failures)} 项（失败项见上方）",
              file=sys.stderr)
        return 2

    print()
    if args.delete:
        verb = "将删除" if args.dry_run else "已删除"
        print(f"完成。{verb} {n} 项（目录：{root}）")
    else:
        verb = "将移入回收目录" if args.dry_run else "已移入回收目录"
        where = trash_root or "（无可清理文件）"
        print(f"完成。{verb} {n} 项（回收目录：{where}）")
    return 0


def _run_aux(root: str, args: argparse.Namespace) -> int:
    """aux 模式：默认路径输出与 B-211 之前**逐字节一致**（旧外壳只认这一段）。"""
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
