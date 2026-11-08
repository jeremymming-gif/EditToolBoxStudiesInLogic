"""SLCN convert 编译期 biber 排序 locale 守卫（B-377 R02，从 convert_compile 外提）。

为什么单独成模块：`.bcf` 里写 `sortlocale=zh__pinyin` 时，若本机 biber 的 CLDR 数据缺
中文拼音 tailoring，biber **退出码仍是 0**、`.bbl` 也非空，只是静默退回 en_US 排序
——中文条目按西文规则排（拼音序全错），编译却「成功」。所以必须读 `.blg`：
  · 生效证据：`with template '<t>' and locale 'zh__pinyin'`（真实流水线里 `-l=zh_pinyin`
    会打印单下划线，`.bcf` 的 `zh__pinyin` 打印双下划线，两种都算）；
  · 退化证据：`No sort tailoring available for locale '<x>'`（退化优先于生效判定）。

外提口径沿用 B-222/B-229 先例（bibpipeline.py / bibmerge.py）：族行数只统计
`convert.py` + convert_bib/convert_compile/convert_metadata/convert_stage 四模块，
本文件的判据不占族行数；`convert_compile.py` 只留 1–2 行集成。
"""
from __future__ import annotations

import glob
import os
import re

_LOCALE_EFFECTIVE_RE = re.compile(
    r"with\s+template\s+'[^']*'\s+and\s+locale\s+'(?P<locale>zh_{1,2}pinyin)'")
_LOCALE_DEGRADED_RE = re.compile(
    r"No sort tailoring available for locale\s+'(?P<locale>[^']*)'")

#: 中文/日文假名/汉字：参考文献里出现即要求排序 locale 有实证（否则硬失败）。
_CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

#: `.bbl` 里的条目标记（biblatex 宏 `\entry{key}{...}`）；没有条目 = 没有可排序内容。
_BBL_ENTRY_RE = re.compile(r"\\entry\s*\{")


def bib_has_cjk(out_dir: str) -> bool:
    """`out_dir` 顶层 `*.bib` 里是否有中文/日文条目（读不出就 False，绝不抛）。"""
    try:
        paths = sorted(glob.glob(os.path.join(out_dir, "*.bib")))
    except OSError:
        return False
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if _CJK_RE.search(fh.read()):
                    return True
        except OSError:
            continue
    return False


def bbl_has_entries(out_dir: str, stem: str) -> bool:
    """`{stem}.bbl` 里是否有 BibTeX 条目（biblatex `\\entry{...}`）；读不出就 False。

    无引用条目的稿子（如稿件仓 `EN_template.tex`：有 `reference.bib` 但正文 0 个
    `\\cite`）biber 不会排序任何条目 → locale 生不生效与成品无关，必须放行，
    否则会把「有 bib、无引用」的合法稿子从可编译打成编译失败（B-377 停止条件
    「有 bib 的稿子编译失败面扩大」）。
    """
    try:
        with open(os.path.join(out_dir, f"{stem}.bbl"), "rb") as fh:
            return _BBL_ENTRY_RE.search(fh.read().decode("utf-8", "replace")) is not None
    except OSError:
        return False


def blg_locale_verdict(blg_path: str, *, bib_has_cjk: bool,
                       bbl_entries: bool = True) -> dict:
    """读 biber 的 `.blg` → 排序 locale 五态判定（D2/D3：degraded 优先，rc=0 也判）。

    返回 ``{"ok", "state", "locale", "detail", "evidence"}``：
      · ``effective``   —— 有排序行且命中 zh__pinyin/zh_pinyin → ok=True；
      · ``degraded``    —— 有 ``No sort tailoring available`` 行 → 恒 ok=False
                           （即便同一份 `.blg` 里还有排序行：那是静默退回 en_US）；
      · ``no_evidence`` —— 两行都没有 → 有中文条目才硬失败，无中文放行（合成稿）；
      · ``no_blg``      —— 文件不存在/为空 → 同上（有中文硬失败，无中文放行）；
      · ``no_entries``  —— `bbl_entries=False`（`.bbl` 无 `\\entry`，没东西可排）→ 恒放行；
                           优先级最高：没有可排序条目时 locale 与成品无关。
    """
    if not bbl_entries:
        return {"ok": True, "state": "no_entries", "locale": "", "evidence": "",
                "detail": ("参考文献没有条目（.bbl 无 \\entry），无需排序 locale，放行")}
    try:
        with open(blg_path, encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        lines = []
    if not lines:
        if bib_has_cjk:
            return {"ok": False, "state": "no_blg", "locale": "", "evidence": "",
                    "detail": ("没有 .blg（biber 日志缺失）且参考文献含中文条目，"
                               "无法确认排序 locale 生效")}
        return {"ok": True, "state": "no_blg", "locale": "", "evidence": "",
                "detail": "没有 .blg（biber 日志缺失）；参考文献无中文条目，放行"}
    degraded_locale = ""
    degraded_line = ""
    effective_locale = ""
    effective_line = ""
    for line in lines:
        if not degraded_line:
            match = _LOCALE_DEGRADED_RE.search(line)
            if match:
                degraded_locale, degraded_line = match.group("locale"), line
                continue
        if not effective_line:
            match = _LOCALE_EFFECTIVE_RE.search(line)
            if match:
                effective_locale, effective_line = match.group("locale"), line
    if degraded_line:
        return {"ok": False, "state": "degraded", "locale": degraded_locale,
                "evidence": degraded_line,
                "detail": ("排序 locale 未生效（No sort tailoring available for "
                           f"locale '{degraded_locale}'）")}
    if effective_line:
        return {"ok": True, "state": "effective", "locale": effective_locale,
                "evidence": effective_line,
                "detail": f"排序 locale 已生效：{effective_locale}"}
    if bib_has_cjk:
        return {"ok": False, "state": "no_evidence", "locale": "", "evidence": "",
                "detail": ".blg 无 zh__pinyin 排序生效证据且参考文献含中文条目"}
    return {"ok": True, "state": "no_evidence", "locale": "", "evidence": "",
            "detail": ".blg 无 zh__pinyin 排序生效证据；参考文献无中文条目，放行"}


def guard_after_biber(out_dir: str, stem: str) -> str | None:
    """biber 步之后判 locale：失败删本次 PDF 并返回错误文案，成功/无需判返回 None。

    D3/D4：`.bcf` 不存在时完全放行（无参考文献的稿子，biber 退出码无意义——既有语义）；
    有 `.bcf` 时失败即**硬失败**（rc=0 也判），删掉本次已产出/残留的 `{stem}.pdf`，
    宁可无 PDF 也不出「排序静默错序」的假成品。
    """
    if not os.path.exists(os.path.join(out_dir, f"{stem}.bcf")):
        return None
    verdict = blg_locale_verdict(
        os.path.join(out_dir, f"{stem}.blg"),
        bib_has_cjk=bib_has_cjk(out_dir),
        bbl_entries=bbl_has_entries(out_dir, stem))
    if verdict["ok"]:
        return None
    try:
        os.remove(os.path.join(out_dir, f"{stem}.pdf"))
    except OSError:
        pass
    return verdict["detail"] + f"（见 {stem}.blg）"
