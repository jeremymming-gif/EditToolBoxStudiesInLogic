#!/usr/bin/env python3
"""编译一期里所有文章的 .tex（对应 mac 版的「编译全部」，第 91 条搬进引擎）。

    python3 compileall.py --folder "<期目录>" [--only 01,03] [--dry-run] [--version]

对期目录下每个编号子文件夹（跳过 `000 目录`）执行四步：
`xelatex → biber -l=zh_pinyin → xelatex → xelatex`。

**与老版（原版 `compileallpdf.py`）的差别**：某一篇失败时会回显该篇 `.log` 里的
`!` 错误摘要（原版把输出丢进 NUL，出错时什么都看不到），并且**只停那一篇**，
继续编后面的。

退出码：`0` 全部成功（或 `--dry-run`）；`1` 有文章编译失败；`2` 用法/目录错误。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import pdfmerge as _pm  # noqa: E402
from slcn import platform as _plat  # noqa: E402


def _compile_pdf(out_dir: str, stem: str):
    """延迟导入 `convert.compile_pdf`（它 import 的东西多，--dry-run 时不必付这个成本）。"""
    import convert  # noqa: PLC0415
    return convert.compile_pdf(out_dir, stem)


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="compileall.py",
        description="编译一期里所有文章的 .tex（xelatex → biber → xelatex ×2）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--folder", required=True, metavar="期目录",
                    help="【必填】期目录（内含 `01 作者/`、`02 作者/` …）")
    ap.add_argument("--only", default="", metavar="序号",
                    help="只编这几篇，逗号分隔的序号前缀（如 `01,03`）")
    ap.add_argument("--dry-run", action="store_true", help="只列出会编哪些，不真跑 TeX")
    args = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(root):
        print(f"\n❌ 期目录不存在：{root}\n", file=sys.stderr)
        return 2

    print("运行全编译的函数\n")
    print(f"文件夹路径：{root}\n")

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    targets: list[tuple[str, str, str]] = []     # (目录名, 文章名, tex 路径)
    for name in _pm.numbered_entries(root):
        if name.startswith("000"):               # 中英文目录不参与编译
            continue
        if only and not any(name.startswith(o) for o in only):
            continue
        art = _pm.article_name(name)
        sub = os.path.join(root, name)
        tex = os.path.join(sub, f"{art}.tex")
        print(f"打开路径：{sub}\n")
        if not os.path.exists(tex):
            # ⚠️ 精确名找不到时用**宽松键**再找一遍（只留字母数字、小写）：
            #    目录名与人写的文件名常常只差一个撇号 —— 2025年第4期(83) 的
            #    `01 Chao'an_He/` 里文件叫 `Chaoan_He.tex`，精确匹配失败 → 这一篇
            #    **每期都被静默漏编**（`--dry-run` 只列 6/7，编辑多半不会注意；
            #    人工补编一次 ≈ 6 秒，但得先发现）。2026-10-17 逐页体检实测。
            lo = _pm.find_tex(sub, art)
            if lo:
                print(f"（宽松匹配）{art} → {os.path.basename(lo)}")
                tex = lo
        if os.path.exists(tex):
            # ⚠️ 编译用的 **stem 必须取"真正找到的那个文件"的名字**，不是目录名派生的
            #    `art`：宽松匹配到 `Chaoan_He.tex` 时若仍拿 `Chao'an_He` 去跑 xelatex，
            #    找不到文件 → 每期白报一次"编译失败"（我第一版只改了发现、没改编译参数，
            #    被自己的测试当场抓出来）。精确匹配时两者相同，行为不变。
            stem = os.path.splitext(os.path.basename(tex))[0]
            targets.append((name, stem, tex))
        else:
            print(f"没有找到{art}文件！\n")

    if not targets:
        print("没有找到可编译的 .tex。\n")
        print(f"完成。编译 0 篇（成功 0 / 失败 0）（目录：{root}）")
        return 0

    if args.dry_run:
        for name, art, tex in targets:
            print(f"（预演）将编译：{name} → {os.path.basename(tex)}")
        # ⚠️ 契约行（INVARIANTS 三.3 / ENGINE_API 五）的格式是
        #    `完成。编译 N 篇（成功 X / 失败 Y）（目录：…）`，**新信息一律新增行** ——
        #    预演这条说明从前是**接在契约行后面**的，于是"最后一行"不再是文档写的那句
        #    （2026-10-17 逐页体检发现；外壳只取退出码，所以没炸，但契约就是契约）。
        print(f"（--dry-run：只列出 {len(targets)} 篇，不真跑 TeX）")
        print()
        print(f"完成。编译 0 篇（成功 0 / 失败 0）（目录：{root}）")
        return 0

    ok = fail = 0
    failures: list[str] = []
    for name, art, _tex in targets:
        sub = os.path.join(root, name)
        print(f"第一步：Xelatex \"{art}.tex\"\n", end="")
        t0 = time.time()
        good, msg = _compile_pdf(sub, art)
        dt = time.time() - t0
        if good:
            ok += 1
            print(f"   ✅ {art}（{dt:.0f} 秒）")
        else:
            fail += 1
            failures.append(f"{art}：{msg}")
            print(f"   ❌ {art}（{dt:.0f} 秒）{msg}")

    print()
    if failures:
        print("⚠ 注意事项：")
        for f in failures:
            print(f"   · 编译失败 {f}")
    print(f"完成。编译 {len(targets)} 篇（成功 {ok} / 失败 {fail}）（目录：{root}）")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
