"""SLCN convert 拆分（B-229）：编译/清理/页数。纯搬运自 v1.4.0 `Engine/convert.py`，判据/文案/输出未改。"""
from __future__ import annotations

import os, re, shutil, subprocess


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
    """读最多 3 条错误；TeX 错误附本段最后一条 l.###，不越过下一条 !。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh: text = fh.read()
    except OSError: return []
    if prefix != "!": return [ln.strip() for ln in text.splitlines() if ln.startswith(prefix) and contains in ln][:3]
    errors = []
    for first, context in re.findall(r"(?m)^(![^\n]*)((?:\n(?!\!)[^\n]*)*)", text):
        if contains not in first: continue
        locations = re.findall(r"(?m)^l\.\d+\b[^\n]*", context)
        errors.append(first.strip() + (" " + locations[-1].strip() if locations else ""))
    return errors[:3]


def compile_pdf_detailed(out_dir: str, stem: str, *, run_xelatex=None):
    """编译详细结果；与旧 tuple 入口共用唯一执行器。

    阶段与返回码直接来自命令执行，不解析人话错误摘要。
    `run_id` 标识本次调用，候选 hash 在清理和编译之前取得。
    `cleaned` 记录既有清理实际删除的文件；`cleanup_remaining`
    记录删除后仍存在的固定后缀，供 L1 报告而非再次删除。

    兼容边界：
    - xelatex → biber -l=zh_pinyin → xelatex ×2 的顺序不变。
    - `run_xelatex` 仍只截获 XeLaTeX；Stage A 负责首编捕获。
    - Biber 无 .bcf 时允许非零，沿用无参考文献的历史语义。
    - 有 .bcf 时需非空 .bbl；有 ERROR 行仍判失败。
    - sortlocale 的既有守卫保持相同调用时机及错误文案。
    - 缺命令、工具失败、缺 PDF 的旧摘要逐字节保留。
    - 此 API 本身不尝试 L1，不改变内部暂存编译行为。
    - 旧成功条件仍是 PDF 存在；L1 另做隔离与 reader 门。

    B-290 F5：不能无条件容忍 Biber 非零。否则坏 Bib 会
    产生正文引用为空的 PDF，却被旧调用方误记为编译成功。
    非零且 .bbl 完好、没有 ERROR 的偶发告警仍照旧放行。

    本模块只保留兼容门面与原清理/摘要函数。详细执行、
    六类诊断、白名单与候选事务均在 compilefix 包中。
    初编详细字段仅供本地结构化适配，不含新网络入口。
    供后续任务书复用的字段由 L1 的最小化接口单独导出。
    详细结果中的本地 info 路径不能直接当作可外发字段。

    成功并不证明 PDF 页数、版面或编辑意义正确。
    原编译调用与 L1 修复候选的验证是两个独立事实。
    """
    import sys
    from .compilefix.l1_detailed import execute
    return execute(sys.modules[__name__], out_dir, stem, run_xelatex=run_xelatex)


def compile_pdf(out_dir: str, stem: str, *, run_xelatex=None) -> tuple[bool, str]:
    """保留旧签名、清理策略、消息及二元组返回值。

    普通 CLI 与 R3 的 L1 接线在各自的失败适配层。
    convert_stage 仍只调用这个兼容入口，不接 L1。
    """
    return compile_pdf_detailed(out_dir, stem, run_xelatex=run_xelatex).legacy()





# 兼容垫片：`pdf_page_count()` 以 `__file__` 定位 Engine/vendor（原文件在 Engine/ 下）。
globals()["__file__"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "convert.py")
