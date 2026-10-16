#!/usr/bin/env python3
r"""生成中英文目录（对应 mac 版的「生成目录」，第 94 条搬进引擎）。

    python3 toc.py --report "<信息检查.txt>" [--template <期目录>] [--settings <目录设置.json>] [--version]

从 `infocheck.py` 的检查报告生成两份目录 .tex（**先英文后中文**，与原版 `run_tab5` 一致）：

  · `Chinese Content_<时间戳>.tex`
  · `contents_<时间戳>.tex`

模板来源优先级：本期 `000 Table of Contents` 里已有的成品 → 上一期 → 内置模板
（继承上一期是为了延续同事手工调过的版式，不再用写死的 2022 年模板）。

本期设置（栏目、主持人、标题断行上限）读报告同目录的 `目录设置.json`；不存在则按默认值。

退出码：`0` 两份都生成；`1` 只生成了一份；`2` 用法/文件错误。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import platform as _plat  # noqa: E402
from slcn import toc as _toc        # noqa: E402


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="toc.py",
        description="从「信息检查」报告生成中英文目录 .tex（模板继承上一期成品）",
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--report", required=True, metavar="信息检查.txt",
                    help="【必填】infocheck.py 生成的检查报告（.txt）")
    ap.add_argument("--template", default="", metavar="期目录",
                    help="指定模板来源期目录（默认：本期成品 → 上一期 → 内置模板）")
    ap.add_argument("--settings", default="", metavar="目录设置.json",
                    help="指定本期设置文件（默认：报告同目录的 目录设置.json）")
    ap.add_argument("--preview", action="store_true",
                    help="只输出预览（篇目清单 + 模板来源）为 JSON，不生成任何文件")
    args = ap.parse_args(argv)

    report = os.path.abspath(os.path.expanduser(args.report))
    if not os.path.isfile(report):
        print(f"\n❌ 找不到检查报告：{report}\n", file=sys.stderr)
        return 2
    report_dir = os.path.dirname(report)

    # ⚠️ 先确认"这确实是一份《信息检查》报告"：从前喂错文件（例如把某篇 .tex 当报告）
    #    会**静默产出两份「第 ? 卷，第 ? 期」的空目录、还退出 0** —— 界面照报 ✅，
    #    而且产物落在**报告所在目录**（真跑时就是作者目录）。2026-10-17 逐页体检实测
    #    （体检时我自己就这么误写进稿件库一次，当场恢复）。
    articles = _toc.list_articles(report)
    if not articles:
        print(f"\n❌ 这不是一份能用的《信息检查》报告（解析出 0 篇）：{report}\n"
              f"   要 `infocheck.py --folder \"<期目录>\"` 生成的那种 .txt；"
              f"本次**没有生成任何文件**。\n", file=sys.stderr)
        return 2

    settings_path = (os.path.abspath(os.path.expanduser(args.settings)) if args.settings
                     else os.path.join(report_dir, _toc.TocSettings.fileName))
    if args.settings and not os.path.isfile(settings_path):
        print(f"\n❌ 找不到设置文件：{settings_path}\n", file=sys.stderr)
        return 2
    settings = _toc.TocSettings.load(os.path.dirname(settings_path)
                                     if args.settings else report_dir)
    if args.settings:
        # `--settings` 指向别处的 json：直接读它，而不是同目录的默认文件
        import json
        try:
            with open(settings_path, encoding="utf-8") as fh:
                data = json.load(fh)
            for k, v in data.items():
                if hasattr(settings, k):
                    setattr(settings, k, v)
        except (OSError, ValueError) as e:
            print(f"\n❌ 读不了设置文件：{e}\n", file=sys.stderr)
            return 2
    if args.template:
        settings.templateFolder = args.template

    if args.preview:
        # 给界面用的预览：篇目清单 + 模板来源 + 生效的设置。
        # 为什么由引擎出而不是界面自己解析报告：解析口径必须与真正生成时**完全一致**，
        # 否则会出现「清单里勾了 3 篇、生成时只认 2 篇」这种对不上的情况。
        import json
        payload = {
            "articles": [{"name": n, "title": ti} for n, ti in articles],
            "templateNote": _toc.template_source_note(report, settings),
            "columnArticles": settings.columnArticles,
            "columnEnabled": settings.is_column_enabled,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    note = _toc.template_source_note(report, settings)
    print("运行生成目录的函数\n")
    print(f"报告：{report}")
    print(f"模板：{note}\n")

    outputs = _toc.run(report, settings, log=lambda s: print(s))

    print()
    for p in outputs:
        print(f"  {os.path.basename(p)}")
    # 契约行（`docs/ENGINE_API.md`）：外壳靠它拿产物
    print(f"完成。生成目录 {len(outputs)} 份（{report_dir}）")
    return 0 if len(outputs) == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
