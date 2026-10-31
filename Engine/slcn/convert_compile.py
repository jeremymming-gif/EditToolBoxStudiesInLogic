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


def _tail_log_lines(path: str, *, prefix: str = "!", contains: str = "") -> list[str]:
    """读日志里最多 3 行错误（`prefix` = 行首标记；`contains` = 行内关键词）。"""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [
                ln.strip() for ln in fh
                if (ln.startswith(prefix) if prefix else True) and contains in ln
            ][:3]
    except OSError:
        return []


def compile_pdf(out_dir: str, stem: str) -> tuple[bool, str]:
    """xelatex → biber -l=zh_pinyin → xelatex ×2。

    B-290 F5：biber 的退出码**不再无条件容忍**。无参考文献的稿子（没有 `.bcf`）biber
    本来就可能非零，仍照旧放行；一旦有 `.bcf`，biber 非零就是真错——读 `.blg` 的
    `ERROR` 行，且**只有 `.bbl` 非空**才判成功，否则 `convert --compile` 会拿一个
    空 `.bbl` 出「编译成功」的假 PDF（实测：坏 bib 时 PDF 4388B、正文 Citations 全空）。
    """
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
        if r.returncode == 0:
            continue
        if i == 2:  # biber：有 .bcf 才严格判定
            bcf = os.path.join(out_dir, f"{stem}.bcf")
            if not os.path.exists(bcf):
                continue  # 无参考文献：biber 退出码无意义，照旧放行
            bbl = os.path.join(out_dir, f"{stem}.bbl")
            bbl_ok = os.path.exists(bbl) and os.path.getsize(bbl) > 0
            blg_errors = _tail_log_lines(
                os.path.join(out_dir, f"{stem}.blg"), prefix="", contains="ERROR")
            if bbl_ok and not blg_errors:
                continue  # 非零但产物完好（biber 偶发告警）→ 放行
            detail = "；".join(blg_errors)
            if not detail:
                detail = "未生成 .bbl（参考文献为空）"
            return False, (f"biber 第 {i} 步失败（退出码 {r.returncode}）：{detail}"
                           f"（请检查 .bib 是否有语法错误，见 {stem}.blg）")
        log = os.path.join(out_dir, f"{stem}.log")
        detail = "；".join(_tail_log_lines(log))
        return False, f"{cmd[0]} 第 {i} 步失败（退出码 {r.returncode}）{('：' + detail) if detail else ''}"
    pdf = os.path.join(out_dir, f"{stem}.pdf")
    if not os.path.exists(pdf):
        return False, "编译结束但未生成 PDF"
    return True, pdf
# 兼容垫片：`pdf_page_count()` 以 `__file__` 定位 Engine/vendor（原文件在 Engine/ 下）。
globals()["__file__"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "convert.py")
