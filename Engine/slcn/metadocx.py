#!/usr/bin/env python3
r"""批63 E2：把 Word（`.docx`）元数据文件合成为临时 `.tex`（`--metadata` 最小适配）。

背景（用户 Q3，2026-10-20）：初排「元数据文件」此前只认 `.tex`——引擎 LaTeX 通道
的元数据源实质是「上一轮模板稿」，只靠 `\my*` 宏搬字段（见 `build.py` ③ 搬运元数据）。
本模块做**最小适配**：用现有 Word 通道的 `docxsource.parse_docx()` 抽取
题名/作者/单位/邮箱/摘要/关键词，按**目标模板语种**（zh/en）写成 `\my*` 宏的临时
`.tex`，后续 lang/author 推导与 `build_latex` 完全不变。**不做 Word→LaTeX 全文转换**。

边界：
  · `.docx` → 合成；提取到 0 个字段 = 明确报错（不静默产出无元数据稿，exit 2）；
  · 老式 `.doc`（OLE 二进制）→ 明确报错，提示另存 `.docx`/`.tex`（Mac 界面会先转）；
  · 其它**二进制**文件 → 明确报错（消灭「按文本解码成乱码」的静默失败）；
  · `.tex`/文本文件 → **原样返回**（默认路径零变化，与 HEAD 逐字节相同）；
  · 临时文件用 `atexit` 注册清理（成功/失败退出都删）。

限制（文档口径，交给 rules-docs）：
  · 只提取元数据字段，不转正文；字段映射按目标模板语种（zh→mytitle/mytitleEN…，
    en→mytitle/mytitleCN…）；
  · 多作者只填第一作者槽位（`myfirstauthor`），不拆 `mysecondauthor…`；
  · 协同两段式（`--coop-taskbook-only/--coop-replies`）暂不支持 Word 元数据文件
    （临时文件进程结束即删，第二段无法复用）——调用方应明确报错。
"""

from __future__ import annotations

import atexit
import os
import tempfile
import zipfile

#: 老式 Word（OLE 复合文档）魔数；`.doc` 一律明确报错，不做 OLE 解析。
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
#: 已知的**非文本**元数据扩展名：直接明确报错（不尝试解码）。
BINARY_EXTS = {".doc", ".docm", ".dot", ".dotx", ".wps", ".rtf", ".odt", ".pdf"}

#: 宏 → 日志里的中文字段名（只用于「提取 N 个字段」那行）。
FIELD_LABELS = {
    "mytitle": "题名",
    "mytitleEN": "英文题名",
    "mytitleCN": "中文题名",
    "myfirstauthor": "作者",
    "myfirstauthorEN": "英文作者",
    "myfirstaffiliation": "单位",
    "myfirstaffiliationEN": "英文单位",
    "myfirstemail": "邮箱",
    "myabstract": "摘要",
    "myabstractEN": "英文摘要",
    "myabstractCN": "中文摘要",
    "mykeywords": "关键词",
}

#: LaTeX 换行（副标题并进题名时用，与 build_docx 的 `\\` 口径一致）。
_TEX_BREAK = "\\\\"


class MetadataError(ValueError):
    """`--metadata` 适配失败：调用方 `die()` → exit 2（绝不静默继续）。"""


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s or "")


def _clean(v: str) -> str:
    """压成一行（Word 段落里的换行/制表符会让 `\\newcommand` 参数断行）。"""
    return " ".join(str(v or "").split())


def is_binary(path: str, probe: int = 8192) -> bool:
    """粗判二进制（前 8KB 有 NUL 字节）。只用于「未知扩展名」的兜底报错。"""
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(probe)
    except OSError:
        return False


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def adapt_metadata(path: str, *, lang: str = "zh") -> tuple[str, list[str], str]:
    """`--metadata` 的适配入口（纯函数式：返回新路径，不修改调用方文件）。

    返回 `(可交给 build_latex 的路径, 提取到的字段中文名列表, 临时文件路径 "")`：
      · `.tex`/文本文件 → `(path, [], "")`，**一个字节都不动**（默认零变化）；
      · `.docx` → `(临时 .tex, [字段…], 临时路径)`，`atexit` 自动清理；
      · `.doc` / 已知二进制 / 未知二进制 → 抛 `MetadataError`（调用方 exit 2）。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".doc":
        raise MetadataError(
            "--metadata 不支持老式 .doc（Word 97-2003 二进制格式）："
            "请另存为 .docx 或 .tex 后重试（Mac 界面会自动把 .doc 转成 .docx）。")
    if ext in BINARY_EXTS:
        raise MetadataError(
            f"--metadata 不支持 {ext} 文件（二进制格式）：引擎的元数据源只认 "
            ".tex（或由 Word .docx 提取）；请另存为 .tex / .docx 后重试。")
    if ext != ".docx":
        if is_binary(path):
            raise MetadataError(
                f"--metadata 指向的文件看起来是二进制（{os.path.basename(path)}）："
                "引擎只支持 .tex 文本或 .docx；请另存为 .tex / .docx 后重试。")
        return path, [], ""
    return _synthesize(path, lang=lang)


def _macros_for(doc, *, lang: str) -> dict[str, str]:
    """DocxDoc → `\\my*` 宏值（按目标模板语种映射，口径与 build_docx 一致）。"""
    from .docxsource import escape_latex

    def esc(v: str) -> str:
        return escape_latex(_clean(v))

    def with_sub(title: str, sub: str) -> str:
        t, s = esc(title), esc(sub)
        return f"{t}{_TEX_BREAK}{s}" if t and s else t

    title = _clean(getattr(doc, "title", ""))
    title_cn = _clean(getattr(doc, "title_cn", ""))
    title_en = _clean(getattr(doc, "title_en", ""))
    subtitle = _clean(getattr(doc, "subtitle", ""))
    subtitle_en = _clean(getattr(doc, "subtitle_en", ""))
    abstract = _clean(getattr(doc, "abstract", ""))
    abstract_cn = _clean(getattr(doc, "abstract_cn", ""))
    abstract_en = _clean(getattr(doc, "abstract_en", ""))
    keywords_cn = _clean(getattr(doc, "keywords_cn", ""))
    keywords_en = _clean(getattr(doc, "keywords_en", ""))
    author = _clean(getattr(doc, "author", ""))
    author_en = _clean(getattr(doc, "author_en", ""))
    aff_cn = _clean(getattr(doc, "affiliation_cn", ""))
    aff_en = _clean(getattr(doc, "affiliation_en", ""))
    email = _clean(getattr(doc, "email", ""))

    is_en = (lang or "zh") == "en"
    out: dict[str, str] = {}
    if is_en:
        # EN 模板：mytitle=英文题名、mytitleCN=中文题名；摘要同理。
        out["mytitle"] = with_sub(title_en or title or title_cn, subtitle_en or subtitle)
        out["mytitleCN"] = with_sub(title_cn, subtitle)
        out["myabstract"] = esc(abstract_en or abstract or abstract_cn)
        out["myabstractCN"] = esc(abstract_cn)
        out["myfirstauthor"] = esc(author if not author or _has_cjk(author)
                                   else author)
        out["myfirstaffiliation"] = esc(aff_en or aff_cn)
        out["myfirstemail"] = esc(email)
    else:
        # CN 模板：mytitle=中文题名、mytitleEN=英文题名；另有 mykeywords。
        out["mytitle"] = with_sub(title_cn or title, subtitle)
        out["mytitleEN"] = with_sub(title_en, subtitle_en)
        out["myabstract"] = esc(abstract_cn or abstract or abstract_en)
        out["myabstractEN"] = esc(abstract_en)
        out["mykeywords"] = esc(keywords_cn or keywords_en)
        out["myfirstauthor"] = esc(author or author_en)
        out["myfirstauthorEN"] = esc(author_en)
        out["myfirstaffiliation"] = esc(aff_cn or aff_en)
        out["myfirstaffiliationEN"] = esc(aff_en)
        out["myfirstemail"] = esc(email)
    return {name: val for name, val in out.items() if val}


def _synthesize(path: str, *, lang: str) -> tuple[str, list[str], str]:
    from .docxsource import parse_docx

    try:
        doc = parse_docx(path)
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise MetadataError(
            "--metadata 读取 Word 文件失败：" + f"{type(e).__name__}: {e}；"
            "请确认文件是有效的 .docx（不是改名的 .doc/其它格式）。") from e
    except Exception as e:  # noqa: BLE001  docx 解析的其它异常同样 exit 2，不静默乱码
        raise MetadataError(
            "--metadata 读取 Word 文件失败：" + f"{type(e).__name__}: {e}") from e

    macros = _macros_for(doc, lang=lang)
    if not macros:
        raise MetadataError(
            "--metadata 的 Word 文件里**没有提取到任何元数据字段**"
            "（题名/作者/单位/邮箱/摘要/关键词全空）：请改用 .tex 元数据文件，"
            "或确认选中的文件是否正确（不能静默产出无元数据稿）。")

    lines = [
        "% 本文件由 convert.py 从 Word 元数据文件**临时生成**（批63；运行结束自动删除）。",
        "% 只写 \\my* 元数据宏，不做 Word→LaTeX 全文转换；请勿手工编辑。",
    ]
    order = ("mytitle", "mytitleEN", "mytitleCN", "myfirstauthor", "myfirstauthorEN",
             "myfirstaffiliation", "myfirstaffiliationEN", "myfirstemail",
             "myabstract", "myabstractEN", "myabstractCN", "mykeywords")
    for name in order:
        val = macros.get(name)
        if val:
            lines.append("\\newcommand{\\%s}{%s}" % (name, val))
    fd, tmp = tempfile.mkstemp(prefix="slcn-meta-", suffix=".tex")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    atexit.register(_cleanup, tmp)
    labels = [FIELD_LABELS.get(name, name) for name in order if macros.get(name)]
    return tmp, labels, tmp
