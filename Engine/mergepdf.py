#!/usr/bin/env python3
"""合并一期的 PDF（第 88 条）—— 给两个外壳共用的命令行入口。

    python3 mergepdf.py --folder "<期目录>" [--out <合并.pdf>] [--nest-outlines]
                        [--editors-page] [--back-cover] [--version]

**为什么要有它**：这个功能原来 mac 走 SwiftUI + PDFKit、Windows 走 PyPDF2，
两套实现。做 Windows 版的前提是「引擎只有一份逻辑」，所以合并规则整体搬进
`slcn/pdfmerge.py`，两个外壳都来调这个 CLI（见 `docs/ENGINE_API.md`）。

**注意**：`文件夹路径` 指的是**期目录**（里面有 `扉页.pdf`、`000 目录/`、`01 Jie_Zhou/` …），
不是单篇稿件的目录。合并结果默认写成 `<期目录>/合并<时间戳>.pdf`。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import platform as _plat  # noqa: E402
from slcn import pdfmerge  # noqa: E402


def _pdf_page_count(path: str) -> int:
    """PDF 页数；打不开返回 0。

    ⚠️ pypdf 在 `slcn/pdfmerge.py` 里是**延迟导入**的（2026-10-17 提速），模块级不再暴露
    `PdfReader` —— 直接 `pdfmerge.PdfReader(...)` 会 AttributeError（实例测试当场抓到）。
    """
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:  # noqa: BLE001  坏 PDF 不该让 --detect 崩
        return 0


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="mergepdf.py",
        description="把一期的扉页、目录、各篇文章、封底按顺序合并成一个 PDF，并重建书签大纲",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--folder", required=True, metavar="期目录",
                    help="【必填】期目录（内含 扉页.pdf / 000 …/ / 01 作者/ …）")
    ap.add_argument("--out", default=None, metavar="合并.pdf",
                    help="输出路径；默认写到期目录下的「合并<时间戳>.pdf」")
    ap.add_argument("--nest-outlines", action="store_true",
                    help="把各篇 PDF 自带的小节书签挂到该篇文章的书签之下（默认平铺在顶层，与原版一致）")
    ap.add_argument("--editors-page", action="store_true",
                    help="把编委信息页插在扉页之后（按文件名识别，见 slcn/pdfmerge.py）")
    ap.add_argument("--detect", action="store_true",
                    help="只探测编委页/封底页/扉页页数，输出 JSON（给界面做运行前预览），不合并")
    ap.add_argument("--back-cover", action="store_true",
                    help="把封底附加页放到最后（形如 cover+bottom…pdf）")
    args = ap.parse_args(argv)

    folder = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(folder):
        print(f"\n❌ 期目录不存在：{folder}\n", file=sys.stderr)
        return 2

    if args.detect:
        # 给界面用：**与真正合并时同一套识别逻辑**，运行前就知道会插哪一份编委页/封底页。
        # 界面不该自己去扫文件 —— 那样"预览说找到 A、合并时插了 B"迟早发生。
        import json as _json
        chosen, others = pdfmerge.detect_editors_page(folder)
        back = pdfmerge.detect_back_cover(folder)
        cover = os.path.join(folder, "扉页.pdf")
        payload = {
            "editorsPage": ({"fileName": chosen.file_name, "pageCount": chosen.page_count,
                             "matchedBy": chosen.matched_by} if chosen else None),
            "others": others,
            "backCover": ({"fileName": back.file_name, "pageCount": back.page_count,
                           "matchedBy": back.matched_by} if back else None),
            "coverPages": (_pdf_page_count(cover) if os.path.isfile(cover) else 0),
        }
        print(_json.dumps(payload, ensure_ascii=False))
        return 0

    try:
        res = pdfmerge.merge_issue(
            folder,
            out=os.path.abspath(os.path.expanduser(args.out)) if args.out else None,
            nest_outlines=args.nest_outlines,
            editors_page=args.editors_page,
            back_cover=args.back_cover,
        )
    except Exception as e:  # noqa: BLE001  写不出去/PDF 坏到没法处理
        print(f"\n❌ 合并失败：{e}\n", file=sys.stderr)
        return 2

    if res.missing:
        print(f"   ⚠ 有 {len(res.missing)} 个预期文件没找到：{'、'.join(res.missing)}")
    if res.warnings:
        print("   ⚠ 注意事项：")
        for w in res.warnings:
            print(f"      · {w}")
    # 契约行（`docs/ENGINE_API.md` §三）：外壳靠这一行拿产物路径与页数
    print(f"完成。合并文件：{res.output}（共 {res.page_count} 页）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
