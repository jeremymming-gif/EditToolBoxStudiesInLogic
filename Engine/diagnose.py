#!/usr/bin/env python3
"""失败诊断 CLI（B-285a）：`python3 diagnose.py --entry <入口> --exit-code <码> --log <日志> [--json]`

    python3 diagnose.py --entry convert.py --exit-code 2 --log "/tmp/convert-失败.log"
    python3 diagnose.py --entry compileall.py --exit-code 1 --log "…" --json

**用在哪一步**：任何一步失败之后。界面/外壳把「入口名 + 退出码 + 日志文件」交给它，
它把日志**尾部**读进来，交给 `slcn.diagnose` 分类，输出编辑能照着做的下一步。

**只读**：只读 `--log` 指的那个文件（读尾部 ≤16KB），不写任何产物、不联网、
不改日志本身。诊断成功 `exit 0`；参数错误（日志不存在、入口名不像文件名）`exit 2`。
**逐字节确定**：同一日志 + 同一 `--generated-at` → 同一字节（复现/测试用）。

契约（`diagnose.v1` 的 13 个契约键、17 类、报告怎么读）见 `Engine/docs/ENGINE_API.md` §十一。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import platform as _plat  # noqa: E402
from slcn import diagnose as _dg  # noqa: E402


def _now() -> str:
    """带时区的本地时间戳（报告里唯一允许随运行变化的值）。"""
    return _dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def read_log_tail(path: str, limit: int = _dg.LOG_TAIL_BYTES) -> str:
    """只读日志尾部 ≤`limit` 字节；返回文本（截断时丢掉被切断的首行）。

    二进制读 + `errors="replace"`：日志里混进 GBK/UTF-16 片段时不让诊断自己崩掉
    （诊断器崩了编辑就什么也拿不到）。
    """
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        start = max(0, size - limit)
        fh.seek(start)
        data = fh.read(limit)
    text = data.decode("utf-8", errors="replace")
    if start > 0:
        # 截断时首行是半行：没有换行就整段不可信，有换行就丢掉它
        if "\n" in text:
            text = text.split("\n", 1)[1]
    return text


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="diagnose.py",
        description="把「入口 + 退出码 + 日志」翻成下一步（diagnose.v1；只读，不写产物）。",
        epilog="示例：python3 diagnose.py --entry convert.py --exit-code 2 --log /tmp/fail.log",
    )
    ap.add_argument("--entry", required=True,
                    help="失败那次运行的入口文件名，如 convert.py（只写文件名，不带路径）")
    ap.add_argument("--exit-code", required=True, type=int, dest="exit_code",
                    help="失败那次运行的退出码（0 表示没有失败，也会出报告但不硬猜原因）")
    ap.add_argument("--log", required=True,
                    help="那次运行的日志文件（只读尾部 ≤16KB，不改文件）")
    ap.add_argument("--json", action="store_true",
                    help="输出单 JSON 对象（默认是人类可读摘要）")
    ap.add_argument("--generated-at", default=None, dest="generated_at",
                    help="可选：注入生成时间（复现/测试用；默认取当前时间）")
    ap.add_argument("--version", action="version",
                    version=f"diagnose.py（SLCN 引擎 {_plat.engine_version()}）")
    args = ap.parse_args(argv)

    entry = args.entry.strip()
    if (not entry or "/" in entry or "\\" in entry or entry.startswith(".")
            or "." not in entry):
        print(f"diagnose.py：入口名要写成文件名（如 convert.py），拿到的是：{args.entry!r}",
              file=sys.stderr)
        return 2
    if not os.path.isfile(args.log):
        print(f"diagnose.py：找不到日志文件：{args.log}", file=sys.stderr)
        return 2

    log_text = read_log_tail(args.log)
    report = _dg.build(
        entry=entry,
        exit_code=args.exit_code,
        log_text=log_text,
        generated_at=args.generated_at or _now(),
    )
    sys.stdout.write(_dg.render_json(report) if args.json else _dg.render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
