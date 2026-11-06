#!/usr/bin/env python3
"""B-375 freeze-3：--place 失败回滚 —— 000 目录不得留下混合半成品。

独立验收复现：000 目录只读时，place 已把 000 里的 Chinese Content.tex / contents.tex
覆盖成新内容；随后编译失败返回 rc2，但 PDF/结果 JSON 还是旧的，目录里新 tex 配旧 pdf。
修复要求：落位前备份 000，任何失败（落位/编译/EN 阶段/未预期异常）按快照恢复，
失败后 000 文件集合与每个文件内容 SHA 与本轮开始前完全一致；成功路径不留备份。

本文件是独立脚本（同 Engine/tests 其余套件风格），失败退出码非 0。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import tocdelivery as _td  # noqa: E402

PY = sys.executable
TOC_NAME = "000 Table of Contents"

_passed = 0
_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}  {detail}")


# 与其它 TOC 套件一致的最小《信息检查》报告（两篇；folder 行是篇目文件夹名）。
REPORT = r"""WeiNong_Li
卷号：19 期号：2 年份：2030
起始页码：1  终止页码：10  页数：10
字号 10.5t
第一作者中文: \kaishu 邱德钧
中文标题: 测试中文题名一
英文标题: Test English Title One
全文行距: 13
Hao_Wu
卷号：19 期号：2 年份：2030
起始页码：11  终止页码：20  页数：10
字号 11pt
第一作者中文: \kaishu 吴昊
中文标题: 测试中文题名二
英文标题: Test English Title Two
全文行距: 13
"""

#: 000 里预置的「上一轮成品」四件套 + aux/log（失败后必须逐个字节不变）。
OLD_FILES = {
    "Chinese Content.tex": "OLD ZH TEX\n",
    "contents.tex": "OLD EN TEX\n",
    "Chinese Content.pdf": "OLD ZH PDF\n",
    "contents.pdf": "OLD EN PDF\n",
    "Chinese Content.aux": "OLD ZH AUX\n",
    "contents.log": "OLD EN LOG\n",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(toc_dir: Path) -> dict[str, str]:
    """000 目录相对文件名 → SHA256（含子目录，排序后比较）。"""
    out: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(toc_dir):
        for name in filenames:
            full = Path(dirpath) / name
            out[str(full.relative_to(toc_dir))] = sha256(full)
    return dict(sorted(out.items()))


def backup_leaks(root: Path) -> list[str]:
    """期目录树里残留的备份目录（成功/失败都必须为空）。"""
    found: list[str] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        for name in dirnames:
            if name.startswith("toc-backup-"):
                found.append(str(Path(dirpath) / name))
    return found


def force_rmtree(path: Path) -> None:
    """清理用：只读目录先补写权限（否则 rmtree 删不掉）。"""
    if not path.exists():
        return
    for dirpath, _dirnames, filenames in os.walk(path):
        try:
            os.chmod(dirpath, 0o700)
        except OSError:
            pass
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            try:
                os.chmod(full, 0o600)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def make_issue(root: Path, *, old_files: bool = True,
               toc_name: str = TOC_NAME) -> tuple[Path, Path]:
    """造期目录：根下报告 + .toolbox-tmp 两份时间戳目录 tex（place 的源）+ 000 旧成品。"""
    (root / ".toolbox-tmp").mkdir(parents=True, exist_ok=True)
    for stem in ("Chinese Content", "contents"):
        (root / ".toolbox-tmp" / f"{stem}_20260101_000000.tex").write_text(
            f"NEW {stem}\n", encoding="utf-8")
    toc_dir = root / toc_name
    toc_dir.mkdir()
    if old_files:
        for name, text in OLD_FILES.items():
            (toc_dir / name).write_text(text, encoding="utf-8")
    return root, toc_dir


def compile_fail(folder: str, stems):
    """模拟 xelatex 在只读 000 里写不了 aux/log 的失败（place 已发生）。"""
    return {key: (False, "模拟编译失败：Permission denied")
            for key, _stem in stems}


def compile_ok(folder: str, stems):
    """假编译：写 pdf + 1 页 log，让 fit_single_page 判定无需压缩。"""
    out = {}
    for key, stem in stems:
        Path(folder, f"{stem}.pdf").write_text(f"NEW {stem} PDF\n", encoding="utf-8")
        Path(folder, f"{stem}.log").write_text(
            f"Output written on {stem}.pdf (1 page).\n", encoding="utf-8")
        out[key] = (True, str(Path(folder, f"{stem}.pdf")))
    return out


def case_fail_restore(root: Path) -> None:
    print("\n── (a) 写回滚：编译失败（000 只读）→ 文件集合与 SHA 原样恢复 ──")
    root, toc_dir = make_issue(root)
    before = snapshot(toc_dir)
    os.chmod(toc_dir, 0o555)
    mode_before = toc_dir.stat().st_mode & 0o7777
    seen: dict[str, str] = {}

    def fail_after_place(folder: str, stems):
        # 记录失败时刻 000 里已被覆盖成新内容（证明「place 已发生」）；
        # 若实现没回滚，测试随后会看到新内容。
        seen["zh"] = Path(folder, "Chinese Content.tex").read_text(encoding="utf-8")
        seen["en"] = Path(folder, "contents.tex").read_text(encoding="utf-8")
        return compile_fail(folder, stems)

    try:
        res = _td.deliver(str(root), do_compile=True, compile_func=fail_after_place)
        after = snapshot(toc_dir)
        check("(a1) deliver 返回失败原因（不抛异常）", bool(res.error), res.error[:120])
        check("(a2) 失败原因指明编译失败/000",
              "编译失败" in res.error, res.error[:120])
        check("(a3) 失败时 000 tex 确已被本轮覆盖（回滚前状态）",
              "NEW Chinese Content" in seen.get("zh", "")
              and "NEW contents" in seen.get("en", ""),
              str(seen)[:120])
        check("(a4) 失败后 000 文件集合与 SHA 与本轮开始前完全一致",
              after == before, f"before={before} after={after}")
        check("(a5) 000 目录权限恢复为 0555", (toc_dir.stat().st_mode & 0o7777) == mode_before,
              oct(toc_dir.stat().st_mode & 0o7777))
        check("(a6) 不残留备份目录", not backup_leaks(root), str(backup_leaks(root)))
    finally:
        os.chmod(toc_dir, 0o755)


def case_exception_restore(root: Path) -> None:
    print("\n── (b) 未预期异常 → 同样恢复并留下可读错误 ──")
    root, toc_dir = make_issue(root)
    before = snapshot(toc_dir)

    def boom(folder: str, stems):
        raise RuntimeError("模拟编译阶段异常")

    res = _td.deliver(str(root), do_compile=True, compile_func=boom)
    after = snapshot(toc_dir)
    check("(b1) 异常不外泄，deliver 返回失败原因", bool(res.error), res.error[:120])
    check("(b2) 失败原因含异常摘要", "模拟编译阶段异常" in res.error, res.error[:120])
    check("(b3) 异常后 000 文件集合与 SHA 完全一致", after == before,
          f"before={before} after={after}")
    check("(b4) 不残留备份目录", not backup_leaks(root), str(backup_leaks(root)))


def case_en_stage_fail(root: Path) -> None:
    print("\n── (c) 中文已编译、英文阶段失败 → 已写出的中文 pdf/log 也要撤掉 ──")
    root, toc_dir = make_issue(root)
    before = snapshot(toc_dir)

    def en_fail(folder: str, stems):
        out = {}
        for key, stem in stems:
            if key == "zh":
                Path(folder, f"{stem}.pdf").write_text("NEW ZH PDF\n", encoding="utf-8")
                Path(folder, f"{stem}.log").write_text(
                    f"Output written on {stem}.pdf (1 page).\n", encoding="utf-8")
                out[key] = (True, str(Path(folder, f"{stem}.pdf")))
            else:
                out[key] = (False, "模拟英文 PDF 编译失败")
        return out

    res = _td.deliver(str(root), do_compile=True, compile_func=en_fail)
    after = snapshot(toc_dir)
    check("(c1) EN 阶段失败被当作交付失败", "contents.tex" in res.error, res.error[:120])
    check("(c2) 失败后 000 无本轮中文 pdf/log 残留", after == before,
          f"before={before} after={after}")
    check("(c3) 不残留备份目录", not backup_leaks(root), str(backup_leaks(root)))


def case_success(root: Path) -> None:
    print("\n── (d) 成功路径：000 更新、无备份残留、旧散落文件不动 ──")
    root, toc_dir = make_issue(root)
    (toc_dir / "keep-me.txt").write_text("UNRELATED\n", encoding="utf-8")
    before = snapshot(toc_dir)

    res = _td.deliver(str(root), do_compile=True, compile_func=compile_ok)
    zh = (toc_dir / "Chinese Content.tex").read_text(encoding="utf-8")
    en = (toc_dir / "contents.tex").read_text(encoding="utf-8")
    after = snapshot(toc_dir)
    check("(d1) 成功：无错误且 compiled", res.error == "" and res.compiled,
          f"error={res.error!r}")
    check("(d2) 000 两份 tex 已更新为新内容",
          zh.startswith("NEW Chinese Content") and en.startswith("NEW contents"),
          f"zh={zh[:30]!r} en={en[:30]!r}")
    zh_pdf = (toc_dir / "Chinese Content.pdf").read_text(encoding="utf-8").strip()
    en_pdf = (toc_dir / "contents.pdf").read_text(encoding="utf-8").strip()
    check("(d3) 000 生成了新 pdf", zh_pdf == "NEW Chinese Content PDF"
          and en_pdf == "NEW contents PDF", f"zh={zh_pdf!r} en={en_pdf!r}")
    check("(d4) 无关旧文件保持原样", after.get("keep-me.txt") == before.get("keep-me.txt"))
    check("(d5) 不残留备份目录", not backup_leaks(root), str(backup_leaks(root)))
    check("(d6) 000 里没有 000 备份文件残留",
          not any("toc-backup" in n for n in os.listdir(toc_dir)), str(os.listdir(toc_dir)))


def case_cli_readonly_000(root: Path) -> None:
    print("\n── (e) CLI 复现验收场景：000 只读 → rc2、结果 JSON 不写、000 原样 ──")
    root.mkdir(parents=True, exist_ok=True)
    report = root / "信息检查2030_01_01_00_00_00.txt"
    report.write_text(REPORT, encoding="utf-8")
    root, toc_dir = make_issue(root, old_files=True)
    before = snapshot(toc_dir)
    result_json = root / "result.json"
    os.chmod(toc_dir, 0o555)
    try:
        proc = subprocess.run(
            [PY, str(ENGINE / "toc.py"), "--report", str(report),
             "--place", "--compile", "--result-json", str(result_json)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ENGINE))
        after = snapshot(toc_dir)
        check("(e1) CLI rc=2（失败明确）", proc.returncode == 2,
              f"rc={proc.returncode} stderr={proc.stderr.strip()[-200:]!r}")
        check("(e2) stderr 有明确失败原因",
              "❌" in proc.stderr and ("目录交付失败" in proc.stderr
                                       or "编译" in proc.stderr or "字体" in proc.stderr),
              proc.stderr.strip()[-300:])
        check("(e3) 失败后 000 文件集合与 SHA 完全一致", after == before,
              f"before={before} after={after}")
        check("(e4) 不写成功态结果 JSON", not result_json.exists(), str(result_json))
        check("(e5) 不残留备份目录", not backup_leaks(root), str(backup_leaks(root)))
        check("(e6) 失败后 000 目录权限仍为 0555",
              (toc_dir.stat().st_mode & 0o7777) == 0o555,
              oct(toc_dir.stat().st_mode & 0o7777))
    finally:
        os.chmod(toc_dir, 0o755)


def main() -> int:
    print("B-375 失败回滚：--place 失败时 000 不得留下混合半成品")
    root = Path(tempfile.mkdtemp(prefix="slcn_b375_rollback_"))
    # CI/Linux 没有 xelatex：deliver() 的真实字体检查会在编译前就失败，注入的
    # compile_func 根本不会被执行，导致本文件 (a)-(d) 全是假红。这四个 in-process
    # case 只验证「回滚协议」，结果完全由注入的 compile_func 决定，因此这里把字体
    # 检查替换为通过；字体检查本身由 test_b285_* 专门套件覆盖。(e) 是真实 CLI
    # 子进程，不替换，保留真实的无 TeX / 只读 000 语义。
    original_font_check = _td.check_windows_fonts
    _td.check_windows_fonts = lambda: (True, "")
    try:
        try:
            case_fail_restore(root / "a")
            case_exception_restore(root / "b")
            case_en_stage_fail(root / "c")
            case_success(root / "d")
        finally:
            _td.check_windows_fonts = original_font_check
        case_cli_readonly_000(root / "e")
    finally:
        force_rmtree(root)
    print("\n" + "=" * 64)
    if _fails:
        print(f"失败 {len(_fails)} 项（共 {_passed + len(_fails)} 项）："
              + "；".join(_fails[:8]))
        return 1
    print(f"全部 {_passed} 项断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
