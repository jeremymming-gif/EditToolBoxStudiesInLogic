"""SLCN convert 拆分（B-229）：编译/清理/页数。纯搬运自 v1.4.0 `Engine/convert.py`，判据/文案/输出未改。"""
from __future__ import annotations

import os, shutil, subprocess


def pdf_page_count(pdf_path: str) -> int:
    """数 PDF 页数（用于提示 --pages）。数不出来就返回 0，绝不因此报错。"""
    try:
        import sys

        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
        from pypdf import PdfReader

        return len(PdfReader(pdf_path).pages)
    except Exception:  # noqa: BLE001
        return 0


_STALE_COMPILE_EXTS = (".aux", ".bbl", ".bcf", ".blg", ".run.xml", ".out", ".toc")


def clean_stale_compile_artifacts(out_dir: str, stem: str) -> list[str]:
    """删掉上一轮编译留下的中间产物，返回被删文件名（第 141 条，2026-10-18）。

    为什么必须删：第一次 xelatex 会**读旧的 `.bbl`**（biber 还没重跑）。若上一版
    residue 里有坏数据（实测期 87 子案例 9：旧 `.bbl` 的 author 带裸 `&`，新版已修好
    bib，但旧 `.bbl` 让 step 1 就报 `Misplaced alignment tab`），convert 会判「编译
    未通过」——其实只是没先清干净。重跑同一目录是编辑的常规操作（反复出稿/改稿），
    不能把上一轮残留算到这一轮头上。

    只删本工具自己会生成的中间文件；`.pdf`、`.tex`、`.bib`、`SLCN-MIX.sty` 一律不动。
    """
    removed: list[str] = []
    for ext in _STALE_COMPILE_EXTS:
        p = os.path.join(out_dir, stem + ext)
        if os.path.exists(p):
            try:
                os.remove(p)
                removed.append(os.path.basename(p))
            except OSError:
                pass
    return removed


def compile_pdf(out_dir: str, stem: str) -> tuple[bool, str]:
    """xelatex → biber -l=zh_pinyin → xelatex ×2。"""
    # 先清上一轮中间产物：否则第一次 xelatex 会读到旧 .bbl/.aux（见上面注释）
    clean_stale_compile_artifacts(out_dir, stem)
    steps = [
        ["xelatex", "-interaction=nonstopmode", f"{stem}.tex"],
        ["biber", "-l=zh_pinyin", stem],
        ["xelatex", "-interaction=nonstopmode", f"{stem}.tex"],
        ["xelatex", "-interaction=nonstopmode", f"{stem}.tex"],
    ]
    for i, cmd in enumerate(steps, 1):
        if shutil.which(cmd[0]) is None:
            return False, f"找不到命令 {cmd[0]}，请确认已安装 TeX Live"
        r = subprocess.run(
            cmd, cwd=out_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
        if r.returncode != 0 and i != 2:  # biber 无参考文献时可能非零，容忍
            log = os.path.join(out_dir, f"{stem}.log")
            detail = ""
            if os.path.exists(log):
                hits = [
                    ln.strip()
                    for ln in open(log, encoding="utf-8", errors="replace")
                    if ln.startswith("!")
                ][:3]
                detail = "；".join(hits)
            return False, f"{cmd[0]} 第 {i} 步失败（退出码 {r.returncode}）{('：' + detail) if detail else ''}"
    pdf = os.path.join(out_dir, f"{stem}.pdf")
    if not os.path.exists(pdf):
        return False, "编译结束但未生成 PDF"
    return True, pdf
# 兼容垫片：`pdf_page_count()` 以 `__file__` 定位 Engine/vendor（原文件在 Engine/ 下）。
globals()["__file__"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "convert.py")
