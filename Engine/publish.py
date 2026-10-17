#!/usr/bin/env python3
"""「本期出刊」向导：按顺序跑完一期稿件的四步（对应 UI 方案候选 C 的引擎侧）。

    python3 publish.py --folder "<期目录>" [--only N] [--from N] [--version]

四步（每一步都是现成的入口，向导只做**编排**）：
  ① 检查tex信息  infocheck.py --folder <期>
  ② 生成目录      toc.py --report <①的报告> --template <期>
  ③ 合并整期 PDF  mergepdf.py --folder <期> --out <期>/合并.pdf
  ④ 编译全部      compileall.py --folder <期>

**为什么编排放在引擎里**（2026-10-17 用户拍板）：项目红线是「一个引擎，两个外壳；
外壳只取值/拼参数/显示日志」。若 mac 用 Swift、Windows 用 Python 各写一遍"四步怎么串、
哪步失败算中止"，就是第三份判据（案例 98/106 都栽在"两份实现"上）。向导的编排只能有一份，
所以是**一个入口**，两个外壳都只调它。

失败语义（用户 2026-10-17 拍板）：
  · 退出码 **2**（致命/用法错误）⇒ **中止**：前 N−1 步的产物保留，界面显示"卡在第 N 步"；
  · 退出码 **1**（该步"有结果"：一校/编译有失败等，见 INVARIANTS 三.2）⇒ **橙色警告、继续**；
  · 退出码 **0** ⇒ 继续。
`--only N` / `--from N` 支持单步重跑（方案要求"可单步重跑"）。

⚠️ 本文件**不复制任何判据**：每步的"什么算成功/什么算有结果"仍由那个入口自己决定；
向导只看退出码，把每步的契约行原样透传。
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import platform as _plat  # noqa: E402

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

#: 第 N 步 → (名字, 入口文件)。顺序就是出刊流程的顺序。
STEPS = {
    1: ("检查tex信息", "infocheck.py"),
    2: ("生成目录", "toc.py"),
    3: ("合并整期 PDF", "mergepdf.py"),
    4: ("编译全部", "compileall.py"),
}


def _entry(name: str) -> str:
    return os.path.join(ENGINE_DIR, name)


def _newest_report(root: str) -> str:
    """期目录里最新的《信息检查*.txt》——第 ② 步的输入（口径与「生成目录」页一致）。"""
    cands = glob.glob(os.path.join(root, "信息检查*.txt"))
    if not cands:
        return ""
    return max(cands, key=lambda p: os.path.getmtime(p))


def _run(no: int, argv: list[str]) -> int:
    _name = STEPS[no][0]
    print(f"\n══════ 第 {no} 步：{_name} ══════\n")
    try:
        proc = subprocess.run([PY] + argv, cwd=ENGINE_DIR)
    except OSError as e:  # 解释器都起不来：致命
        print(f"❌ 第 {no} 步启动失败：{e}", file=sys.stderr)
        return 2
    return proc.returncode


def _step_argv(no: int, root: str) -> tuple[list[str], str]:
    """返回 (argv, 前置说明)。第 ② 步要先找到 ① 的报告，找不到就是**中止**（不是跳过）。"""
    if no == 1:
        return [_entry("infocheck.py"), "--folder", root], ""
    if no == 2:
        report = _newest_report(root)
        if not report:
            return [], "没有找到《信息检查*.txt》报告 —— 请先跑第 1 步"
        return [_entry("toc.py"), "--report", report, "--template", root], f"报告：{report}"
    if no == 3:
        return ([_entry("mergepdf.py"), "--folder", root,
                 "--out", os.path.join(root, "合并.pdf")], "")
    return [_entry("compileall.py"), "--folder", root], ""


def main(argv: list[str] | None = None) -> int:
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="publish.py",
        description="本期出刊向导：依次跑 检查tex信息 → 生成目录 → 合并整期 PDF → 编译全部"
                    "（失败中止、有结果继续，可单步重跑）",
    )
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    ap.add_argument("--folder", required=True, metavar="期目录", help="【必填】一期的目录")
    ap.add_argument("--only", type=int, choices=(1, 2, 3, 4), metavar="N",
                    help="只跑第 N 步（单步重跑）")
    ap.add_argument("--from", dest="from_step", type=int, choices=(1, 2, 3, 4), metavar="N",
                    help="从第 N 步开始跑（前 N−1 步的产物当作已经就绪）")
    args = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(root):
        print(f"\n❌ 期目录不存在：{root}\n", file=sys.stderr)
        return 2

    first = args.only or args.from_step or 1
    last = args.only or 4

    print("本期出刊向导\n")
    print(f"期目录：{root}\n")

    warnings: list[str] = []
    for no in range(first, last + 1):
        step_argv, note = _step_argv(no, root)
        name = STEPS[no][0]
        if not step_argv:
            # 前置条件不满足 ⇒ 中止（不是"跳过"——静默跳过会产出半成品，方案明令禁止）
            print()
            print(f"完成。出刊：卡在第 {no} 步（{name}）；前 {no - 1} 步产物有效（目录：{root}）")
            print(f"      原因：{note}", file=sys.stderr)
            return 2
        if note:
            print(f"（{note}）\n")
        rc = _run(no, step_argv)
        if rc == 2 or rc < 0:
            print()
            print(f"完成。出刊：卡在第 {no} 步（{name}）；前 {no - 1} 步产物有效（目录：{root}）")
            return 2
        if rc == 1:                      # "有结果"不是失败：橙色警告、继续（INVARIANTS 三.2）
            warnings.append(f"第 {no} 步（{name}）：有需要处理的结果（退出码 1）")

    print()
    if args.only:
        print(f"完成。出刊：只跑了第 {args.only} 步（{STEPS[args.only][0]}）（目录：{root}）")
    else:
        tail = f"，警告 {len(warnings)} 项" if warnings else ""
        print(f"完成。出刊：{last - first + 1} 步都跑完{tail}（目录：{root}）")
    for w in warnings:
        print(f"   ⚠ {w}")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
