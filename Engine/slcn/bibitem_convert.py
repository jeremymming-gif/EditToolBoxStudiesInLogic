r"""B-233：手写 `\bibitem` → 规范 `reference.bib`（LaTeX / Word 两通道共用）。

`bibnorm.bibitems_to_bib()` 是零调用方骨架（类型恒 @article、其余塞 note）。本模块在
**调用侧**健壮化它：先 `latex.strip_comments`（注释里的 `% \bibitem` 不算条目）、在
`\end{thebibliography}` 处截止（末条不吞环境尾），再逐条重解析提升字段/类型——
LNCS/Springer 体例（PhD thesis→@phdthesis、In:→@incollection、期刊→@article、
出版社+城市→@book）、GB/T 7714 中英文条目（`[J]/[M]/[C]/[D]` 与《题名》体例，复用
bibnorm/footref 既有解析器）；判不出的 @misc+note 原文并逐条低置信提示。渲染后逐条过
`convert_bib.bib_entry_structurally_safe`（不合格跳过+warning，不让坏 .bib 让 biber
整份报废）。key 原样保留，空/重复/非法 key 确定性兜底。

契约：`convert()` 绝不抛异常（fail-soft）；`enabled()/set_enabled()` 全局开关
（CLI `--no-bibitem-convert`）；`WARNING_AUTO` 是两通道共用的固定交接单提示。本模块
不读/写文件、不认识 docx（Word 通道由 impl-word 取 entries 后在既有单点写）。

B-222 A 段：解析规则已收编 `footref.py` 判据单点（本模块只做条目编排/key/渲染/CLI）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .footref import (clean_bibitem_note, empty_bibitem_fields,
                      parse_bibitem_entry, render_bibitem_entry,
                      bibitem_skeleton_fields)
from .latex import strip_comments

WARNING_AUTO = "由 \\bibitem 自动转换，请核对类型/字段"

_BIBITEM_RE = re.compile(
    r"\\bibitem(?:\[[^\]]*\])?\{([^}]*)\}\s*(.*?)(?=\\bibitem|\Z)", re.S)
_END_RE = re.compile(r"\\end\{thebibliography\}|\\end\{document\}")
_KEY_BAD_RE = re.compile(r"[\s,{}%#&~\\]+")
_ENABLED = True


def enabled() -> bool:
    """全局开关：默认 True（转换开）；`--no-bibitem-convert` 置 False 回旧行为。"""
    return _ENABLED


def set_enabled(flag: bool) -> None:
    global _ENABLED
    _ENABLED = bool(flag)


@dataclass
class BibitemResult:
    """转换结果；`entries`/`bib_text` 为空表示没东西可写（调用方回旧行为）。"""

    entries: list[dict] = field(default_factory=list)
    bib_text: str = ""
    warnings: list[str] = field(default_factory=list)
    n_source: int = 0
    n_parsed: int = 0


def looks_like_bibitems(text: str) -> bool:
    """剥注释后是否含**有效** `\\bibitem`（注释示例不算；默认路径的闸门）。"""
    if not text or "\\bibitem" not in text:
        return False
    t = strip_comments(text)
    m = _END_RE.search(t)
    return "\\bibitem" in (t[:m.start()] if m else t)


def render_entries(entries: list[dict]) -> str:
    """entries（统一 dict）→ .bib 文本；空条目跳过。结构安全由 convert() 把关。"""
    lines = ["% " + WARNING_AUTO + "。", ""]
    for e in entries or ():
        raw = render_bibitem_entry(e)
        if raw:
            lines.extend((raw, ""))
    return "\n".join(lines)


def _assign_keys(raw_keys: list[str]) -> list[str]:
    used: set[str] = set()
    out: list[str] = []
    for i, raw in enumerate(raw_keys, 1):
        key = _KEY_BAD_RE.sub("_", (raw or "").strip()) or f"bibitem{i}"
        base, n = key, 2
        while key in used:
            key = f"{base}-{n}"
            n += 1
        used.add(key)
        out.append(key)
    return out


def convert(text: str, *, strip: bool = True) -> BibitemResult:
    """手写 `\\bibitem` 文本 → `BibitemResult`（fail-soft：异常只记 warning）。"""
    res = BibitemResult()
    if not text:
        return res
    try:
        t = strip_comments(text) if strip else text
        m = _END_RE.search(t)
        t = t[:m.start()] if m else t
        items = _BIBITEM_RE.findall(t)
        res.n_source = len(items)
        if not items:
            return res
        try:  # 复用零调用方骨架：key 顺序 + 重解析失败时 title/year 兜底
            from .bibnorm import bibitems_to_bib
            skeleton = bibitem_skeleton_fields(bibitems_to_bib(t)[0])
        except Exception:  # noqa: BLE001
            skeleton = {}
        keys = _assign_keys([k for k, _b in items])
        entries: list[dict] = []
        warns: list[str] = []
        for idx, (raw_key, body) in enumerate(items):
            key, rawk = keys[idx], (raw_key or "").strip()
            if rawk != key:
                san = _KEY_BAD_RE.sub("_", rawk)
                if not rawk:
                    warns.append(f"{key}: 源 \\bibitem 为空 key，已按出现顺序兜底")
                elif san != rawk:
                    warns.append(f"{key}: key 含空白/非法字符，已兜底改写（原 {rawk!r}）")
                else:
                    warns.append(f"{key}: 源 key 重复，已确定性改名保留（原 {rawk!r}）")
            try:
                e = parse_bibitem_entry(body)
            except Exception as exc:  # noqa: BLE001
                e = empty_bibitem_fields()
                e["note"] = clean_bibitem_note(body)
                warns.append(f"{key}: 解析异常，已保留原文（{type(exc).__name__}）")
            e["key"] = key
            sk = skeleton.get(rawk) or {}
            if not e.get("title") and sk.get("title"):
                e["title"] = sk["title"]
            if not e.get("year") and re.fullmatch(r"(?:19|20)\d{2}", sk.get("year") or ""):
                e["year"] = sk["year"]
            if not (e.get("author") or e.get("title")):
                e["_type"] = "misc"
                e["note"] = e.get("note") or clean_bibitem_note(body)
            miss = [f for f in ("author", "title", "year") if not e.get(f)]
            if e.get("_type") == "misc" or miss:
                bits = ([f"缺 {'/'.join(miss)}"] if miss else [])
                if e["_type"] == "misc":
                    bits.append("类型未判定")
                warns.append(f"{key}: 低置信（{'，'.join(bits)}），请人工核对")
            entries.append(e)
        good: list[dict] = []
        for e in entries:
            raw_entry = render_bibitem_entry(e)
            try:
                from .convert_bib import bib_entry_structurally_safe
                ok = bool(bib_entry_structurally_safe(raw_entry)) if raw_entry else False
            except Exception:  # noqa: BLE001  校验器异常不能变成「整篇不转换」
                ok = bool(raw_entry)
            if ok:
                good.append(e)
            else:
                warns.append(f"{e.get('key')}: 结构校验未过，已跳过"
                             "（避免坏 .bib 让 biber 整份报废）")
        res.entries, res.n_parsed = good, len(good)
        res.bib_text = render_entries(good)
        if res.n_parsed != res.n_source:
            warns.insert(0, f"源 \\bibitem {res.n_source} 条，写入 {res.n_parsed} 条"
                            "（其余未通过校验或为空）")
        res.warnings = warns
        return res
    except Exception as exc:  # noqa: BLE001  fail-soft：绝不把转换异常抛给主流程
        res.warnings.append(f"\\bibitem 转换失败，保留原文：{type(exc).__name__}: {exc}")
        return res


# ── CLI 接线（B-229 族总量行数上限：门面只加两处调用，逻辑都在本模块）──────

def add_cli_args(ap):
    """给 `convert.py` 的 parser 挂 `--no-bibitem-convert`（默认转换开）。返回 ap。"""
    ap.add_argument("--no-bibitem-convert", action="store_true",
                    help="源稿没有 .bib、只有手写 \\bibitem 时**不**自动转换出"
                         " reference.bib（默认转换；转换结果请核对类型/字段）")
    return ap


def apply_cli_args(args):
    """按解析结果设全局开关；返回 args（供门面一行调用）。"""
    if getattr(args, "no_bibitem_convert", False):
        set_enabled(False)
    return args
