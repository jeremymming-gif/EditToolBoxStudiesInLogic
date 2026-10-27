#!/usr/bin/env python3
r"""生成中英文目录（对应 mac 版的「生成目录」，第 94 条搬进引擎）。

    python3 toc.py --report "<信息检查.txt>" [--template <期目录>] [--settings <目录设置.json>]
                   [--place] [--compile] [--result-json <结果.json>] [--version]

从 `infocheck.py` 的检查报告生成两份目录 .tex（**先英文后中文**，与原版 `run_tab5` 一致）：

  · `Chinese Content_<时间戳>.tex`
  · `contents_<时间戳>.tex`

模板来源优先级：本期 `000 Table of Contents` 里已有的成品 →（`--template` 显式指定）→
上一期 → 内置模板（继承上一期是为了延续同事手工调过的版式，不再依赖旧版写死模板；内置模板已是 2026 年第 3 期(88) 版式）。

本期设置（栏目、主持人、标题断行上限）读报告同目录的 `目录设置.json`；不存在则按默认值。

**B-226 起「一键最终交付」**（`--place` / `--compile`）：

  · `--place`：缺 `000*` 则自动创建 `000 Table of Contents`，生成后落位固定名
    `000*/Chinese Content.tex`、`000*/contents.tex`（覆盖旧成品；根下带时间戳的 tex 保留给下一期继承）；
  · `--compile`：落位后先做 Windows 字体前置检查（SimSun/SimHei/KaiTi，缺字体明确报错、绝不静默换字体），
    再 `xelatex ×2`；页数 > 1 时一次性压缩 `\vdistance` 行距做单页适配（中英同一机制）；
  · 两者可分别使用；`--compile` 隐含需要落位。
  · 落位/编译/单页适配的唯一实现在 `slcn/tocdelivery.py`（`publish.py` 第 ④ 步共用，禁止第二份）。
  · `--result-json`：给外壳读的结构化结果（schema 见 `docs/ENGINE_API.md` §五）；默认仍是只生成 tex。

退出码：`0` 两份都生成（且已请求的落位/编译都成功）；
`1` 两份已生成但**有需人工核对项**（只生成了一份 / 报告不在期目录里导致「总第 ? 期」无法确定）；
`2` 用法/文件错误，或落位/编译/单页适配失败（原因在 stderr）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import platform as _plat        # noqa: E402
from slcn import toc as _toc              # noqa: E402
from slcn import tocdelivery as _td       # noqa: E402
from slcn import toctemplate as _tt       # noqa: E402


def _result_json(report: str, report_dir: str, settings: "_toc.TocSettings",
                 delivery, extra_warnings: list[str],
                 source: str = "", note: str = "") -> dict:
    """拼 `--result-json` 的固定 schema（GUI 依赖字段名，别随手改名）。

    ⚠️ `templateSource`/`templateNote` 必须用**生成时**的解析结果（调用方在生成前快照），
    不能等交付完再算：`--place` 会把成品写进 000，事后再解析就永远是「本期成品」了。
    """
    if not source:
        source, _note = _toc.template_source(report, settings)
    if not note:
        note = _toc.template_source_note(report, settings)
    placed = {"zhTex": "", "zhPdf": "", "enTex": "", "enPdf": ""}
    pages = {"zh": 0, "en": 0}
    if delivery is not None:
        placed["zhTex"] = delivery.placed.get("zh", "")
        placed["zhPdf"] = delivery.pdfs.get("zh", "")
        placed["enTex"] = delivery.placed.get("en", "")
        placed["enPdf"] = delivery.pdfs.get("en", "")
        pages = {"zh": int(delivery.pages.get("zh") or 0),
                 "en": int(delivery.pages.get("en") or 0)}
    return {
        "templateSource": source,
        "templateNote": note,
        "tocDir": delivery.toc_dir if delivery is not None else _td.find_toc_dir(report_dir),
        "placed": placed,
        "compiled": bool(delivery is not None and delivery.compiled),
        "pages": pages,
        "column": {
            "enabled": settings.is_column_enabled,
            "title": settings.chinese_column_title if settings.is_column_enabled else "",
        },
        "manualEdits": _toc.manual_edits(report, settings),
        "warnings": list(extra_warnings),
    }


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="toc.py",
        description="从「信息检查」报告生成中英文目录 .tex（模板继承上一期成品；可一键落位+编译）",
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
    ap.add_argument("--place", action="store_true",
                    help="生成后落位固定名到 000 目录（缺 000* 时自动创建 `000 Table of Contents`）")
    ap.add_argument("--compile", action="store_true",
                    help="落位后编译目录 PDF（字体前置检查 + xelatex ×2 + 单页适配）；隐含 --place")
    ap.add_argument("--result-json", default="", metavar="结果.json",
                    help="把结构化结果写成 JSON（GUI 用；字段 schema 见 docs/ENGINE_API.md）")
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
        payload = {
            "articles": [{"name": n, "title": ti} for n, ti in articles],
            "templateNote": note,
            "columnArticles": settings.columnArticles,
            "columnEnabled": settings.is_column_enabled,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    note = _toc.template_source_note(report, settings)
    print("运行生成目录的函数\n")
    print(f"报告：{report}")
    print(f"模板：{note}\n")

    # 「总第 N 期」是从**报告所在目录名**（`2026年第5期(90)`）里取的：报告被挪到期目录外时
    # 会写成「总第 ? 期」。从前这种情况照旧退出 0（只有一行日志），产物看上去"成功"却是残的；
    # 2026-10-17 第十四批改为退出码 1（INVARIANTS 三.2 的「有结果、需人工核对」），
    # 界面会显示橙色警告而不是绿色完成。
    total_issue_known = _tt.total_issue_number(report_dir) is not None

    # 模板来源快照：**必须在生成/落位之前**取（`--place` 会写 000，事后再解析就不是生成时的口径了）
    template_source, template_note = _toc.template_source(report, settings)

    outputs = _toc.run(report, settings, log=lambda s: print(s))

    print()
    for p in outputs:
        print(f"  {os.path.basename(p)}")
    warnings: list[str] = []
    if not total_issue_known:
        warnings.append("无法确定「总第 N 期」：检查报告不在形如 `2026年第5期(90)` 的期目录里，"
                        "目录头部会写成「总第 ? 期」，请人工补。")
        print("⚠️ " + warnings[-1])

    # ---- B-226：落位 / 编译（唯一实现在 slcn/tocdelivery.py）----
    delivery = None
    if args.place or args.compile:
        print()
        print("开始落位到 000 目录" + ("并编译目录 PDF" if args.compile else "") + "……")
        delivery = _td.deliver(report_dir, do_compile=args.compile, log=lambda s: print(s))
        if delivery.error:
            print(f"\n❌ 目录交付失败：{delivery.error}\n", file=sys.stderr)
            return 2
        warnings.extend(delivery.warnings)
        for w in delivery.warnings:
            print(f"⚠️ {w}")
        if args.compile:
            print(f"\n✅ 已落位并编译：{delivery.toc_dir}")
            for key, stem in _td.TEX_STEMS:
                print(f"   {stem}.pdf：{delivery.pages.get(key, 0)} 页")

    if args.result_json:
        path = os.path.abspath(os.path.expanduser(args.result_json))
        payload = _result_json(report, report_dir, settings, delivery, warnings,
                               source=template_source, note=template_note)
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
        except OSError as e:
            print(f"\n❌ 写不了结果 JSON：{e}\n", file=sys.stderr)
            return 2
        print(f"结果 JSON：{path}")

    # 契约行（`docs/ENGINE_API.md`）：外壳靠它拿产物
    print(f"完成。生成目录 {len(outputs)} 份（{report_dir}）")
    if len(outputs) != 2 or not total_issue_known:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
