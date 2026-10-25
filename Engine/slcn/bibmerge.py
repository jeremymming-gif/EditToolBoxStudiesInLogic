"""参考文献合并 / 内容指纹 / 结构校验（B-233 mini-split：从 `convert_bib` 整体搬出）。

为什么单独成模块：B-229 族有**硬行数上限**（`Engine/tests/test_platform.py:710-712`：
`convert.py + convert_bib + convert_compile + convert_metadata + convert_stage ≤ 3977`）。
B-233 的 Word `\\bibitem` 接线要在 `run_refs_attach()` 里加分支，按「先腾行数再改行为」
把既有纯 helper **逐字**搬到这里（新模块不计入族）；`convert_bib` 顶部 import re-export，
`convert.py` 门面与既有套件的 `convert.bib_xxx` 用法保持不变。

判据零改动：既有 helper 与搬运前逐字相同；`_attach_bibitem_text` 是 B-233 新增
（Word refs 附件 `\\bibitem` 文本 → reference.bib）。本模块不读命令行、不写日志。
"""
from __future__ import annotations

import os
import re


# ── 参考文献「内容指纹」：跨来源去重用 ────────────────────────────────────
#
# 同一份文献可能从两条通道进来（正文文末的编号清单 / 参考文献附件），**键却不同**
# （`Beall2000` vs `a1`），只按键去重挡不住。实测 02 Lili_Zhao：正文自带 19 条
# 编号清单、附件又是同一份，于是 bib 翻倍成 38 条。
_NORM_RE = re.compile(r"[\W_]+")


def _ref_fingerprint(author: str, title: str, year: str) -> tuple[str, str, str]:
    """作者 + 年份 + 题名的归一化指纹（去掉全部标点与空白，大小写无关）。"""
    def norm(s: str) -> str:
        return _NORM_RE.sub("", (s or "").lower())

    return (norm(author), norm(year), norm(title))


def _bib_fingerprints(bib_text: str) -> set[tuple[str, str, str]]:
    """从已有 .bib 文本里取出每条目的内容指纹。"""
    if not bib_text.strip():
        return set()
    from slcn.bibcheck import fields_of, split_entries

    out = set()
    for _etype, _key, body in split_entries(bib_text):
        f = fields_of(body)
        fp = _ref_fingerprint(f.get("author", ""), f.get("title", ""), f.get("year", ""))
        if fp != ("", "", ""):
            out.add(fp)
    return out


# ── 第 134 条（2026-10-18）：参考文献附件的「校验 / 合并」 ───────────────────


def bib_entry_structurally_safe(entry: str) -> bool:
    """渲染后的 BibTeX 条目最低结构校验（第 134 条）。

    花括号配平、字段区无相邻 `}{`——71/04 的坏条目就是 `title = {A}{B}` 这种形状
    （biber: "found {...}, expected end of entry"）。
    """
    if "}{" in entry:
        return False
    depth = 0
    for ch in entry:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def bib_attachment_entries(text: str) -> list[tuple[str, str, str, str]]:
    """把 BibTeX 附件拆成 [(类型, 键, 字段体, 条目原文)]。

    第 134 条：条目按**原文直入**（不改写作者字段/顺序），只做去重与结构校验。
    """
    from slcn.bibnorm import split_entries as split_raw

    out: list[tuple[str, str, str, str]] = []
    for head, fields, tail in split_raw(text):
        m = re.match(r"@(\w+)\s*\{\s*([^,]+)\s*,", head)
        if not m:
            continue
        key = m.group(2).strip()
        if not key:
            continue
        out.append((m.group(1), key, fields, head + fields + tail))
    return out


def merge_bib_attachment(bibp: str, have: str, bib_text: str) -> tuple[int, int, int]:
    """把 BibTeX 附件按条目原文并入 `bibp`；返回 (新增, 重复跳过, 结构不合格跳过)。

    第 134 条：按键 + 「作者+年份+题名」内容指纹去重；渲染后结构校验不合格的条目不写
    （错误条目会让 biber 报语法错、**整份 bib 报废**——71/04 的教训）。
    """
    from slcn.bibcheck import fields_of as _fields_of
    from slcn.bibcheck import split_entries as _split3

    have = have or ""
    have_keys = {k for _t, k, _b in _split3(have)} if have else set()
    seen = _bib_fingerprints(have) if have else set()
    added: list[str] = []
    skipped_dup = 0
    skipped_bad = 0
    for _etype, key, fields, raw in bib_attachment_entries(bib_text):
        if key in have_keys:
            skipped_dup += 1
            continue
        if not bib_entry_structurally_safe(raw):
            skipped_bad += 1
            continue
        f = _fields_of(fields)
        fp = _ref_fingerprint(f.get("author", ""), f.get("title", ""), f.get("year", ""))
        if fp != ("", "", "") and fp in seen:
            skipped_dup += 1
            continue
        seen.add(fp)
        have_keys.add(key)
        added.append(raw if raw.endswith("}") else raw + "}")
    if added:
        with open(bibp, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n% ── 由参考文献附件合并 ──\n" + "\n".join(added) + "\n")
    return len(added), skipped_dup, skipped_bad


# ── B-233：Word refs 附件（`\bibitem` 纯文本）→ reference.bib ────────────────


def _attach_bibitem_text(res, txt, *, out_dir, stem, word_source=False):
    """B-233：`\\bibitem` 纯文本附件 → 逐条写入 bib（键保留/失败不阻断）。

    去重与写盘口径同 `convert_bib.run_refs_attach()` 的编号清单路径：键命中或内容
    指纹命中即跳过；渲染后过 `bib_entry_structurally_safe` 闸，坏条目不写。
    返回 `(res, 新增, 重复, 不合格)`。`word_source=True` 时补建的 bib 用 Word 通道
    既有单点名 `reference.bib`（.tex 的 `\\addbibresource` 写死了这个名字）；其它通道
    沿用旧逻辑的 `<stem>.bib`。
    """
    from slcn import bibitem_convert

    try:
        result = bibitem_convert.convert(txt)
    except Exception:  # noqa: BLE001 —— 附件转换失败按「未识别」处理，不阻断出稿
        return res, 0, 0, 0
    entries = [e for e in (getattr(result, "entries", None) or []) if e.get("key")]
    if not entries:
        return res, 0, 0, 0
    bibp = res.bib_path or os.path.join(
        out_dir, "reference.bib" if word_source else f"{stem}.bib")
    have = ""
    if os.path.exists(bibp):
        have = open(bibp, encoding="utf-8", errors="replace").read()
    elif not res.bib_path:
        with open(bibp, "w", encoding="utf-8", newline="\n") as f:
            f.write("% 由参考文献附件生成\n\n")
    # 就地补 res.bib_path：不能走 `res.__class__(**res.__dict__)`——`BuildResult` 上
    # 可能挂着运行时动态属性（如引用库回填的 `_bibdb_store_shared`），重建会崩。
    if res.bib_path is None:
        res.bib_path = bibp
    added: list[str] = []
    seen = _bib_fingerprints(have)
    skipped_dup = skipped_bad = 0
    for e in entries:
        key = str(e["key"])
        fp = _ref_fingerprint(str(e.get("author") or ""), str(e.get("title") or ""),
                              str(e.get("year") or ""))
        if f"{{{key}," in have or fp in seen:
            skipped_dup += 1
            continue
        seen.add(fp)
        try:
            text = bibitem_convert.render_entries([e])
        except Exception:  # noqa: BLE001 —— 单条渲染失败不阻断其余条目
            skipped_bad += 1
            continue
        # render_entries 统一带一行 `% 由 \bibitem…` 注释头；落盘只取条目本体
        entry_text = "\n".join(
            ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("%")
        ).strip()
        if not entry_text or not bib_entry_structurally_safe(entry_text):
            skipped_bad += 1
            continue
        added.append(entry_text)
    if added:
        with open(bibp, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n% ── 由参考文献附件合并（\\bibitem 转换） ──\n"
                    + "\n\n".join(added) + "\n")
    return res, len(added), skipped_dup, skipped_bad
