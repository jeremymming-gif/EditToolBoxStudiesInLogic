#!/usr/bin/env python3
"""「本期出刊」向导：按顺序跑完一期稿件的五步（对应 UI 方案候选 C 的引擎侧）。

    python3 publish.py --folder "<期目录>" [--only N] [--from N]
                       [--skip-toc-pdf]
                       [--no-nest-outlines] [--no-editors-page] [--no-back-cover]
                       [--version]

五步（每一步都是现成的入口，向导只做**编排**）：
  ① 检查tex信息    infocheck.py --folder <期>                 → 期目录根《信息检查<时间戳>.txt》
  ② 生成目录       toc.py --report <①的报告>                    → 期目录根两份带时间戳的目录 .tex
                    （**不再硬传 `--template <本期>`**：模板解析与「生成目录」页同一套
                      「本期成品 → 显式指定 → 上一期 → 内置」；需要显式指定时传 `--template <期目录>`）
  ③ 编译全部       compileall.py --folder <期>（只编论文，跳过 000 目录）
  ④ 目录 PDF 落位  调 `slcn/tocdelivery.py`（与 `toc.py --place --compile` **同一份实现**）：
                   取期目录根最新 `Chinese Content_<ts>.tex` / `contents_<ts>.tex`
                   → 复制为 `000*/Chinese Content.tex` / `contents.tex`（覆盖旧的、保留 .tex 供下一期继承）
                   → Windows 字体前置检查 → 在 `000*` 内 xelatex ×2 出同名 .pdf
                   → 页数 > 1 时一次性压缩行距做单页适配
                   （该期缺 `000*` 目录时**自动创建** `000 Table of Contents`，不再报错中止）
  ⑤ 合并整期 PDF   mergepdf.py --folder <期> --out <期>/合并.pdf
                   **显式传** `--nest-outlines --editors-page --back-cover`（与 GUI「合并文件」页默认三开一致），
                   可用 `--no-nest-outlines` / `--no-editors-page` / `--no-back-cover` 逐个关闭。

**为什么编排放在引擎里**（2026-10-17 用户拍板）：项目红线是「一个引擎，两个外壳；
外壳只取值/拼参数/显示日志」。若 mac 用 Swift、Windows 用 Python 各写一遍"五步怎么串、
哪步失败算中止"，就是第三份判据（案例 98/106 都栽在"两份实现"上）。向导的编排只能有一份，
所以是**一个入口**，两个外壳都只调它。

失败语义（用户 2026-10-17 拍板，INVARIANTS 三.2）：
  · 退出码 **2**（致命/用法错误）⇒ **中止**：前 N−1 步的产物保留，末行写明「卡在第 N 步」；
  · 退出码 **1**（该步"有结果"：一校/编译有失败等）⇒ **橙色警告、继续**；
  · 退出码 **0** ⇒ 继续。
`--only N` / `--from N` 对 1–5 步都生效（单步重跑 / 从某步续跑）。

关于第 ④ 步（目录 PDF，2026-10 第 71 批 B-116）：
  · 从前「编译目录 tex → 改名放进 000 目录」是**人工步骤**（`使用说明.md` 写着），
    现在由本步自动完成 —— 目录 PDF 是合并的输入之一，不落位就等于产不出含新目录的成品；
  · 必须**先**跑（第③步之后、第⑤步之前）：合并要读 `000*/Chinese Content.pdf` / `contents.pdf`；
  · 目录 .tex 编译失败 ⇒ **退出码 2 中止**，不允许拿上一期的旧 PDF 静默合并；
  · 该期没有 `000` 开头的目录 ⇒ **自动创建** `000 Table of Contents` 后照常落位（B-226）；
  · `--skip-toc-pdf` 跳过第 ④ 步（直接用 000 目录里现有 PDF），缺目录 PDF 时**明确告警**。

⚠️ 本文件**不复制任何判据**：每步的"什么算成功/什么算有结果"仍由那个入口自己决定；
向导只看退出码，把每步的契约行原样透传。第 ④ 步的「复制 + 编译 + 单页适配 + 字体检查」
单点在 `slcn/tocdelivery.py`（`toc.py --place --compile` 共用同一份，禁止第二份实现）。
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import platform as _plat      # noqa: E402
from slcn import pdfmerge as _pm        # noqa: E402
from slcn import tocdelivery as _td     # noqa: E402

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

#: 第 N 步 → (步骤名, 入口文件)。顺序就是出刊流程的顺序（文档 `docs/ENGINE_API.md` §五 钉住）。
STEPS = {
    1: ("检查tex信息", "infocheck.py"),
    2: ("生成目录", "toc.py"),
    3: ("编译全部", "compileall.py"),
    4: ("目录 PDF 落位", ""),          # 内部步骤：复制目录 tex + xelatex ×2，不另起引擎入口
    5: ("合并整期 PDF", "mergepdf.py"),
}

#: 第 ④ 步的固定名与顺序（与合并顺序一致：先中文目录、再英文目录）。
#: 实现单点在 `slcn/tocdelivery.py`（`TEX_STEMS`），这里只是别名。
TOC_TEX_PAIRS = tuple((f"{stem}_*.tex", stem) for _key, stem in _td.TEX_STEMS)

#: 合并输出名（与旧版 publish 一致：期目录下的固定名；`合并` 前缀让 pdfmerge 的识别排除它）。
MERGED_NAME = "合并.pdf"


@dataclass
class _Opts:
    """一次出刊运行的选项（步骤函数共用；测试可直接构造）。"""
    root: str
    template: str = ""              # ② 显式指定模板期目录（默认空 = 与「生成目录」同一套解析）
    skip_toc_pdf: bool = False
    nest_outlines: bool = True      # ⑤ 显式传 `--nest-outlines`（默认与 GUI 合并页一致：开）
    editors_page: bool = True       # ⑤ 显式传 `--editors-page`
    back_cover: bool = True         # ⑤ 显式传 `--back-cover`


@dataclass
class _StepResult:
    """一步的结果：向导只认退出码，`warnings` 是给末行汇总的附加说明。"""
    rc: int
    warnings: list[str] = field(default_factory=list)
    reason: str = ""                # rc=2 时给编辑看的可操作原因（打到 stderr）


# ── 小工具 ──────────────────────────────────────────────────────────────

def _entry(name: str) -> str:
    return os.path.join(ENGINE_DIR, name)


def _newest(root: str, pattern: str) -> str:
    """期目录根下匹配 `pattern` 的最新文件（按 mtime）；没有返回空串。"""
    cands = glob.glob(os.path.join(root, pattern))
    if not cands:
        return ""
    return max(cands, key=lambda p: os.path.getmtime(p))


def _newest_report(root: str) -> str:
    """期目录里最新的《信息检查*.txt》——第 ② 步的输入（口径与「生成目录」页一致）。"""
    return _newest(root, "信息检查*.txt")


def _run_script(argv: list[str]) -> _StepResult:
    """起子进程跑一个引擎入口，**只看退出码**（判据仍由那个入口自己决定）。"""
    try:
        proc = subprocess.run([PY] + argv, cwd=ENGINE_DIR)
    except OSError as e:  # 解释器都起不来：致命
        return _StepResult(2, reason=f"启动失败：{e}")
    return _StepResult(proc.returncode)


# ── 五步 ────────────────────────────────────────────────────────────────

def _step1_infocheck(root: str, opts: _Opts) -> _StepResult:
    return _run_script([_entry("infocheck.py"), "--folder", root])


def _step2_toc(root: str, opts: _Opts) -> _StepResult:
    report = _newest_report(root)
    if not report:
        # 前置条件不满足 ⇒ 中止（不是"跳过"——静默跳过会产出半成品，方案明令禁止）
        return _StepResult(2, reason="没有找到《信息检查*.txt》报告 —— 请先跑第 1 步")
    print(f"（报告：{report}）\n")
    argv = [_entry("toc.py"), "--report", report]
    if opts.template:
        # 显式指定模板期目录时才传（默认让 toc.py 走「本期成品 → 上一期 → 内置」同一套解析，
        # 从前硬传 `--template <本期>`，缺 000 时会退化成内置、与「生成目录」页不一致，B-226 E8）
        argv += ["--template", opts.template]
        print(f"（显式模板：{opts.template}）\n")
    return _run_script(argv)


def _step3_compileall(root: str, opts: _Opts) -> _StepResult:
    return _run_script([_entry("compileall.py"), "--folder", root])


def _step4_toc_pdf(root: str, opts: _Opts) -> _StepResult:
    """目录 PDF 落位：**唯一实现**在 `slcn/tocdelivery.py`（与 `toc.py --place --compile` 共用）。

    B-226 起缺 `000*` 目录时**自动创建** `000 Table of Contents` 再落位（不再 rc2 中止）；
    `--skip-toc-pdf` 仍整步跳过（直接用 000 目录里现有的目录 PDF，缺 PDF 时明确告警）。
    """
    if opts.skip_toc_pdf:
        print("（--skip-toc-pdf：跳过第 4 步，直接用 000 目录里现有的目录 PDF）\n")
        toc_dir = _td.find_toc_dir(root)
        missing = ([stem + ".pdf" for _pat, stem in TOC_TEX_PAIRS] if not toc_dir
                   else [stem + ".pdf" for _pat, stem in TOC_TEX_PAIRS
                         if not os.path.isfile(os.path.join(toc_dir, stem + ".pdf"))])
        if not missing:
            return _StepResult(0)
        warn = (f"第 4 步已按 --skip-toc-pdf 跳过，但 000 目录里缺 {'、'.join(missing)}"
                f" —— 合并产物会缺（或沿用旧的）目录页")
        print(f"   ⚠ {warn}")
        return _StepResult(1, warnings=[warn])

    print("（落位 + 编译目录 PDF；缺 000 目录时自动创建）\n")
    res = _td.deliver(root, do_compile=True, log=lambda s: print(s))
    if res.error:
        return _StepResult(2, reason=res.error)
    for key, stem in _td.TEX_STEMS:
        pdf = res.pdfs.get(key, "")
        pages = res.pages.get(key, 0)
        print(f"   ✅ 目录 PDF：{pdf}（{pages} 页）")
    return _StepResult(0, warnings=list(res.warnings))


def _step5_merge(root: str, opts: _Opts) -> _StepResult:
    argv = [_entry("mergepdf.py"), "--folder", root, "--out", os.path.join(root, MERGED_NAME)]
    # 显式传三个开关（mergepdf 自己的默认值**不动**：单入口默认仍是「全关」= 原契约）
    flags: list[tuple[str, bool]] = [
        ("--nest-outlines", opts.nest_outlines),
        ("--editors-page", opts.editors_page),
        ("--back-cover", opts.back_cover),
    ]
    argv += [flag for flag, enabled in flags if enabled]
    on = [flag for flag, enabled in flags if enabled]
    print(f"（合并开关：{' '.join(on) if on else '全关'}）\n")
    return _run_script(argv)


#: 步骤号 → 步骤函数（测试可替换单项来钉住步骤序/退出码传播）。
STEP_FUNCS = {
    1: _step1_infocheck,
    2: _step2_toc,
    3: _step3_compileall,
    4: _step4_toc_pdf,
    5: _step5_merge,
}


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="publish.py",
        description="本期出刊向导：依次跑 检查tex信息 → 生成目录 → 编译全部 → 目录 PDF 落位 → 合并整期 PDF"
                    "（失败中止、有结果继续，可单步重跑）",
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--folder", required=True, metavar="期目录", help="【必填】一期的目录")
    ap.add_argument("--only", type=int, choices=(1, 2, 3, 4, 5), metavar="N",
                    help="只跑第 N 步（单步重跑）")
    ap.add_argument("--from", dest="from_step", type=int, choices=(1, 2, 3, 4, 5), metavar="N",
                    help="从第 N 步开始跑（前 N−1 步的产物当作已经就绪）")
    ap.add_argument("--template", default="", metavar="期目录",
                    help="第 2 步显式指定模板来源期目录（默认不传：与「生成目录」同一套"
                         "「本期成品 → 上一期 → 内置」解析）")
    ap.add_argument("--skip-toc-pdf", action="store_true",
                    help="跳过第 4 步（目录 PDF 落位）：直接用 000 目录里现有的目录 PDF；缺了会告警")
    ap.add_argument("--no-nest-outlines", dest="nest_outlines", action="store_false",
                    help="第 5 步不传 --nest-outlines（默认传：各篇小节书签挂到文章书签之下）")
    ap.add_argument("--no-editors-page", dest="editors_page", action="store_false",
                    help="第 5 步不传 --editors-page（默认传：插入编委信息页）")
    ap.add_argument("--no-back-cover", dest="back_cover", action="store_false",
                    help="第 5 步不传 --back-cover（默认传：附加封底页）")
    args = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(root):
        print(f"\n❌ 期目录不存在：{root}\n", file=sys.stderr)
        return 2

    first = args.only or args.from_step or 1
    last = args.only or 5
    opts = _Opts(root=root,
                 template=os.path.abspath(os.path.expanduser(args.template)) if args.template else "",
                 skip_toc_pdf=args.skip_toc_pdf,
                 nest_outlines=args.nest_outlines,
                 editors_page=args.editors_page,
                 back_cover=args.back_cover)

    print("本期出刊向导\n")
    print(f"期目录：{root}\n")

    warnings: list[str] = []
    for no in range(first, last + 1):
        name = STEPS[no][0]
        print(f"\n══════ 第 {no} 步：{name} ══════\n")
        res = STEP_FUNCS[no](root, opts)
        if res.rc == 2 or res.rc < 0:
            print()
            print(f"完成。出刊：卡在第 {no} 步（{name}）；前 {no - 1} 步产物有效（目录：{root}）")
            if res.reason:
                print(f"      原因：{res.reason}", file=sys.stderr)
            return 2
        if res.rc == 1:                  # "有结果"不是失败：橙色警告、继续（INVARIANTS 三.2）
            if res.warnings:
                warnings.extend(res.warnings)
            else:
                warnings.append(f"第 {no} 步（{name}）：有需要处理的结果（退出码 1）")
        else:
            warnings.extend(res.warnings)   # 0 但带说明的（如 --skip-toc-pdf 全齐）

    print()
    for w in warnings:
        print(f"   ⚠ {w}")
    # 契约行必须是**最后一行**（docs/ENGINE_API.md §五）：警告在这之前打印
    if args.only:
        print(f"完成。出刊：只跑了第 {args.only} 步（{STEPS[args.only][0]}）（目录：{root}）")
    else:
        tail = f"，警告 {len(warnings)} 项" if warnings else ""
        print(f"完成。出刊：{last - first + 1} 步都跑完{tail}（目录：{root}）")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
