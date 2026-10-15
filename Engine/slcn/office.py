r"""用 LibreOffice 把 Word 原稿转成 PDF，供「矢量形状」类对象人工截图。

为什么需要这一步
----------------
全库实测：**矢量形状 235 个（10 篇），是内嵌图片（33 个）的 7 倍**。
它们是 Word 绘图工具画出来的对象——`<w:drawing>` 里没有 `<a:blip r:embed>`、
`word/media/` 里也没有对应文件，**根本无从「提取」，只能截图或重画**。

本模块负责把原稿转成 PDF，并定位每个矢量形状落在**第几页**，
这样编辑打开 PDF 就能直接找到要截的地方，不用通篇翻。

技术要点
--------
`soffice --headless` 必须显式指定可写的用户配置目录（`-env:UserInstallation`），
否则报 “User installation could not be completed” 直接退出。实测踩到过。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from . import platform as _plat


def find_soffice() -> str | None:
    """定位 `soffice`（第 86 条，跨平台：PATH → 三平台常见安装位置 → `SLCN_SOFFICE`）。"""
    return _plat.find_soffice()


def available() -> bool:
    return find_soffice() is not None


def docx_to_pdf(src: str, out_dir: str, timeout: int = 180) -> tuple[str | None, str]:
    """把 docx/doc 转成 PDF，返回 (PDF 路径, 提示信息)。"""
    soffice = find_soffice()
    if not soffice:
        return None, "未找到 soffice（LibreOffice），跳过原稿 PDF 导出"

    os.makedirs(out_dir, exist_ok=True)
    profile = os.path.join(out_dir, ".loprofile")
    os.makedirs(profile, exist_ok=True)

    cmd = [
        soffice,
        "--headless",
        # ⚠️ 必须是 **URI**：Windows 上盘符要写成 `file:///C:/…`
        #    （从前手工拼 `file://{路径}`，在 mac 上侥幸能用、到 Windows 就错）。
        f"-env:UserInstallation={_plat.file_uri(profile)}",
        "--convert-to",
        "pdf",
        "--outdir",
        out_dir,
        os.path.abspath(src),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, f"LibreOffice 转换超时（{timeout}s）"

    stem = os.path.splitext(os.path.basename(src))[0]
    pdf = os.path.join(out_dir, stem + ".pdf")
    if os.path.exists(pdf):
        return pdf, f"已生成原稿 PDF（{os.path.getsize(pdf) // 1024} KB）"
    return None, f"转换失败（退出码 {r.returncode}）：{(r.stderr or r.stdout or '')[-200:]}"


def page_texts(pdf: str) -> list[str]:
    """取 PDF 每页的文字，用于定位图形所在页。

    优先用 macOS 自带的 `mdimport`/PDFKit 不现实，这里用 pypdf 若可用，
    否则退回 `pdftotext`（poppler）。都没有就返回空列表。
    """
    try:  # pypdf / PyPDF2
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader  # type: ignore

        return [(p.extract_text() or "") for p in PdfReader(pdf).pages]
    except Exception:  # noqa: BLE001
        pass

    exe = shutil.which("pdftotext")
    if exe:
        try:
            r = subprocess.run([exe, "-layout", pdf, "-"], capture_output=True, text=True, timeout=120, check=False)
            return r.stdout.split("\f")
        except Exception:  # noqa: BLE001
            pass
    return []


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def locate_pages(pdf: str, anchors: list[str]) -> list[int]:
    """给每个锚点文字找出所在页码（1 起）。找不到返回 0。"""
    pages = page_texts(pdf)
    if not pages:
        return [0] * len(anchors)
    norm_pages = [_norm(p) for p in pages]
    out: list[int] = []
    for a in anchors:
        key = _norm(a)[-24:] if len(_norm(a)) > 24 else _norm(a)
        page = 0
        if key:
            for i, t in enumerate(norm_pages, 1):
                if key and key in t:
                    page = i
                    break
        out.append(page)
    return out


def cleanup_profile(out_dir: str) -> None:
    """删掉 LibreOffice 的用户配置目录（每次转换会产生几十 MB）。"""
    prof = os.path.join(out_dir, ".loprofile")
    if os.path.isdir(prof):
        shutil.rmtree(prof, ignore_errors=True)
