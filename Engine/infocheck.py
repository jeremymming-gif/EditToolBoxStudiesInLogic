#!/usr/bin/env python3
r"""检查 tex 信息（对应 mac 版的「检查tex信息」，第 94 条搬进引擎）。

    python3 infocheck.py --folder "<期目录>" [--out-dir <目录>] [--version]

把一期里每篇文章 `.tex` 里已填好的信息读出来汇总：
卷号 / 期号 / 年份 / 起止页码 / 页数 / 字号 / 中英文标题 / 各作者（中英文）/ 全文行距，
并与该篇 PDF 的**实际页数**对比。**没有任何交互问答**——信息本来就在 tex 里。

产物（写在期目录根下，可用 `--out-dir` 挪走）：

  · `信息检查<时间戳>.txt` —— **CRLF** 行尾，与原版／mac 版逐字节一致
  · `信息检查<时间戳>.json` —— 结构化数据（供「生成目录」与界面表格用；B-285a 起多
    一个 `summary` 字段给外壳画摘要卡）
  · `信息检查<时间戳>_摘要.md` —— B-285a 新增：统一摘要头（结论/计数/top5/折叠明细），
    旧 `.txt` / `.json` 不动

退出码：`0` 生成成功；`2` 用法/目录错误。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import infocheck as _ic  # noqa: E402
from slcn import platform as _plat  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="infocheck.py",
        description="检查一期各篇 tex 里的信息（卷期年、页码、题目、作者、字号、行距）+ PDF 实际页数",
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--folder", required=True, metavar="期目录",
                    help="【必填】期目录（内含 `01 作者/` 等子目录）")
    ap.add_argument("--out-dir", default=None, metavar="目录",
                    help="报告写到这个目录（默认就是期目录本身，与 mac 版一致）")
    ap.add_argument("--preview", action="store_true",
                    help="只输出检查结果（JSON）到 stdout，不写任何文件（给界面用）")
    args = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(root):
        print(f"\n❌ 期目录不存在：{root}\n", file=sys.stderr)
        return 2
    out_dir = os.path.abspath(os.path.expanduser(args.out_dir)) if args.out_dir else root
    if not os.path.isdir(out_dir):
        print(f"\n❌ 输出目录不存在：{out_dir}\n", file=sys.stderr)
        return 2

    if args.preview:
        # 给界面用：与真正跑一遍**同一套解析**，但不落文件。
        # 界面的表格必须来自这里，不能自己再解析一遍 tex —— 否则表格与报告会对不上。
        import json as _json
        rep = _ic.process_folders(root, log=lambda _s: None, write=False)
        payload = {
            "generatedAt": rep.generatedAt,
            "issueFolder": rep.issueFolder,
            "articles": [a.__dict__ for a in rep.articles],
            "inconsistencies": rep.inconsistencies,
        }
        print(_json.dumps(payload, ensure_ascii=False))
        return 0

    print("运行检查文件的函数\n")
    print(f"当前目录为：{root}\n")

    report = _ic.process_folders(root, out_dir=out_dir, log=lambda s: print(s))
    n_tex = sum(1 for a in report.articles if a.hasTeX)
    n_pdf = sum(1 for a in report.articles if a.actualPDFPages is not None)
    print("检查文件生成成功，请查看。\n")
    for note in report.inconsistencies:
        print(f"⚠️ {note}")
    if report.inconsistencies:
        print()

    base = os.path.join(out_dir, f"信息检查{report.generatedAt}")
    # 契约行（`docs/ENGINE_API.md`）：外壳靠它拿篇数与产物路径
    print(f"完成。检查 {len(report.articles)} 篇（有 tex {n_tex} / 有 pdf {n_pdf}；不一致 "
          f"{len(report.inconsistencies)} 项）（报告：{base}.txt）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
