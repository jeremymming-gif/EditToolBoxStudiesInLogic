"""初排交接单（B-221 C1）：把初排阶段**已有**的四类结论汇总成一份给人看的单子。

纪律（spec §4.1 C1 冻结口径）：**只汇总引用、不重算判据**——
这里不判断字段该不该补、占位该不该有、warning 是否成立；每一类数据都由原实现
单点产出（`build.BuildResult` / `_write_bib_report` / `convert.bibdb_readonly_scan`），
本模块只负责渲染成四节固定文本，空节写「无」。因此 `.tex/.bib` 逐字节不变：
交接单在全部产物写完之后由 `build_docx()` 接线写入；`convert.py` 在 ⑦ 引用库回填
之后用 `write_from_result()` 刷新一次，补上回填命中条数（build_docx 阶段还看不到）。

产物名：`<stem>_初排交接单.txt`（UTF-8 / LF）。
"""
from __future__ import annotations

import os
import re

#: 交接单文件名后缀（`convert.is_own_product()` 也用这个后缀认自己产物）。
HANDOFF_SUFFIX = "_初排交接单.txt"

#: `bibdbapply.attach_short_citation()` 命中时写进 `doc.warnings` 的那句前缀
#: （B-220 N6；从既有 warning 原文里数命中条数，不另立判据）。
BIBDB_SHORT_HIT_PREFIX = "短引经引用库安全命中"

#: 四节固定标题（顺序即产物顺序；测试与验收都按它们定位）。
SECTION_TITLES = (
    "① 参考文献核对",
    "② 未识别元数据",
    "③ 需补充占位",
    "④ 注意事项",
)

# 【需补充…】占位：按**最终 .tex 里实际出现**的标签汇总（不重算「该不该有」）。
_PLACEHOLDER_RE = re.compile(r"【需补充[^】\n]{0,20}】")
_WS_RE = re.compile(r"\s+")

# 编辑侧出版信息宏 → 人话（**仅显示用**；缺哪些由 `build.editorial_fields()` 单点判定）。
_EDITORIAL_LABELS = {
    "myvolnumber": "卷号",
    "myissnumber": "期号",
    "mypubyear": "出版年",
    "myfirstpage": "起始页",
    "mylastpage": "结束页",
    "mypages": "总页数",
}


def _context(tex_text: str, start: int, end: int, width: int = 28) -> str:
    """占位符左右各取 `width` 字（压掉换行/连续空白），供人工在 .tex 里定位。"""
    left = _WS_RE.sub(" ", tex_text[max(0, start - width):start])
    right = _WS_RE.sub(" ", tex_text[end:end + width])
    return "…" + (left + tex_text[start:end] + right).strip() + "…"


def placeholder_items(tex_text: str) -> list[tuple[str, list[str]]]:
    """最终 `.tex` 里已有的「【需补充…】」占位 → `[(标签, [短上下文…]), …]`。

    按标签首次出现顺序分组；每个标签下列出**全部**出现处的短上下文（逐项，不定位、
    不改产物）。不预设标签清单：.tex 里写了什么就汇总什么（当前实现产出
    表题/图题/页码/参考文献/参考文献与页码 五类）。
    """
    items: list[tuple[str, list[str]]] = []
    seen: dict[str, int] = {}
    for m in _PLACEHOLDER_RE.finditer(tex_text or ""):
        label = m.group(0)
        ctx = _context(tex_text, m.start(), m.end())
        if label not in seen:
            seen[label] = len(items)
            items.append((label, [ctx]))
        else:
            items[seen[label]][1].append(ctx)
    return items


def _bibdb_applied(bibdb: dict | None) -> int:
    """⑦ 引用库回填实际应用条数（`bibdb_readonly_scan()` 返回值；没有则 0）。"""
    if not bibdb or not bibdb.get("done"):
        return 0
    try:
        return len(bibdb.get("applied") or ())
    except TypeError:  # pragma: no cover —— 防御性：绝不让汇总拖累出稿
        return 0


def _bibdb_line(bibdb: dict | None) -> str:
    """① 里那行「引用库回填」结论（数据来自 convert ⑦ 的现成返回值）。"""
    if not bibdb or not bibdb.get("done"):
        return "引用库回填（初排 ⑦）：无（引用库未启用或不可用，不查库）"
    n_safe = len(bibdb.get("safe") or ())
    n_cand = len(bibdb.get("candidates") or ())
    n_missed = len(bibdb.get("missed") or ())
    applied = bibdb.get("applied") or ()
    n_applied = len(applied)
    n_filled = int(bibdb.get("filled") or 0)
    if n_applied:
        return (f"引用库回填（初排 ⑦）：引用库命中 {n_safe} 条，本次回填 {n_applied} 条"
                f" / {n_filled} 项（候选 {n_cand}、未命中 {n_missed} 不动）")
    return (f"引用库回填（初排 ⑦）：引用库命中 {n_safe} 条，本次无可回填字段"
            f"（候选 {n_cand}、未命中 {n_missed} 不动）")


def _bib_section(bib: dict | None, bibdb: dict | None, short_hits: int) -> list[str]:
    """① bibcheck 结论 + 引用库回填 + 核对报告指针。"""
    if not bib:
        return ["无（源稿未解析出参考文献条目，未生成核对报告）"]
    total = int(bib.get("total") or 0)
    must = int(bib.get("must") or 0)
    nice = int(bib.get("nice") or 0)
    unstr = int(bib.get("unstructured") or 0)
    report_path = str(bib.get("report_path") or "")
    clean = (not report_path and not must and not nice and not unstr
             and not short_hits and _bibdb_applied(bibdb) == 0)
    if clean:
        # 反例口径：bibcheck 没发现问题、也没有回填/短引命中 → 该节无事可交接。
        return [f"无（bibcheck 未发现问题：条目 {total} 条，必补 0 / 建议 0 / "
                "未结构化 0；未生成参考文献核对报告）"]
    lines = [
        f"条目总数：{total}（必补 {must} / 建议 {nice} / 未结构化 {unstr}）",
        f"引用库安全命中（短引库→bib，生成期）：{short_hits} 条",
        _bibdb_line(bibdb),
    ]
    if report_path:
        lines.append(f"指针：{os.path.basename(report_path)}")
    else:
        lines.append("指针：无（bibcheck 未生成核对报告）")
    return lines


def _meta_section(undetected: list[tuple[str, str]],
                  missing_fields: list[str]) -> list[str]:
    """② 未识别元数据占位逐项 + 编辑侧未提供字段（均来自 BuildResult）。"""
    lines: list[str] = []
    if undetected:
        lines.append(f"未识别元数据（产物中已写「【未识别：…】」占位符，"
                     f"{len(undetected)} 项）：")
        for label, macro in undetected:
            lines.append(f"  · {label}（{macro}）" if macro else f"  · {label}")
    if missing_fields:
        lines.append(f"编辑侧未提供字段（{len(missing_fields)} 项；"
                     "可用 --vol/--issue/--year/--pages 等补）：")
        for name in missing_fields:
            label = _EDITORIAL_LABELS.get(name)
            lines.append(f"  · {name}（{label}）" if label else f"  · {name}")
    return lines or ["无"]


def _placeholder_section(tex_text: str) -> list[str]:
    """③ 最终 .tex 里的「【需补充…】」占位逐项（次数 + 短上下文）。"""
    items = placeholder_items(tex_text)
    if not items:
        return ["无"]
    n_all = sum(len(ctxs) for _label, ctxs in items)
    lines = [f"最终 .tex 中「需补充」占位共 {n_all} 处、{len(items)} 类"
             "（只汇总，不定位、不改产物）："]
    for label, ctxs in items:
        lines.append(f"  · {label} × {len(ctxs)}")
        for i, ctx in enumerate(ctxs, 1):
            lines.append(f"      {i}. {ctx}")
    return lines


def _warnings_section(warnings: list[str]) -> list[str]:
    """④ BuildResult.warnings 原文逐条（源稿/转换注意事项，不重算判据）。"""
    warnings = [str(w) for w in (warnings or ())]
    if not warnings:
        return ["无"]
    return [f"共 {len(warnings)} 条（原文，按生成顺序）："] + [f"  · {w}" for w in warnings]


def render(*, stem: str, bib: dict | None = None, bibdb: dict | None = None,
           undetected: list[tuple[str, str]] | None = None,
           missing_fields: list[str] | None = None, tex_text: str = "",
           warnings: list[str] | None = None) -> str:
    """渲染交接单全文（**纯函数**，便于逐字断言）。四节固定，空节写「无」。"""
    warnings = [str(w) for w in (warnings or ())]
    short_hits = sum(1 for w in warnings if w.startswith(BIBDB_SHORT_HIT_PREFIX))
    sections = [
        (SECTION_TITLES[0], _bib_section(bib, bibdb, short_hits)),
        (SECTION_TITLES[1], _meta_section(list(undetected or ()),
                                          list(missing_fields or ()))),
        (SECTION_TITLES[2], _placeholder_section(tex_text)),
        (SECTION_TITLES[3], _warnings_section(warnings)),
    ]
    lines = [
        f"══ {stem} 初排交接单 ══",
        "",
        "本单只汇总初排阶段已有结论（不重算判据、不改 .tex/.bib）；四节固定，空节写「无」。",
        "",
    ]
    for title, body in sections:
        lines.append(title)
        lines.extend(body)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_from_result(res, *, bibdb: dict | None = None,
                      log=None) -> str | None:
    """把 `BuildResult` 汇总成交接单写入其产物目录；返回路径（没法写则 None）。

    `build_docx()` 在全部产物写完后调用一次；`convert.py` 在 ⑦ 引用库回填之后带
    `bibdb=` 再调用一次刷新（此时 .bib 已是回填后的最终版，条数口径以生成时 bibcheck
    结论为准，回填明细以 ⑦ 返回值 + 核对报告为准）。
    """
    tex_path = str(getattr(res, "tex_path", "") or "")
    if not tex_path:
        return None
    stem = os.path.splitext(os.path.basename(tex_path))[0]
    out_dir = os.path.dirname(os.path.abspath(tex_path))
    try:
        with open(tex_path, encoding="utf-8", errors="replace") as f:
            tex_text = f.read()
    except OSError:  # pragma: no cover —— 文件刚写过，读不到也不阻断出稿
        tex_text = ""
    text = render(
        stem=stem,
        bib=getattr(res, "handoff_bib", None),
        bibdb=bibdb,
        undetected=getattr(res, "undetected_fields", None) or (),
        missing_fields=getattr(res, "missing_fields", None) or (),
        tex_text=tex_text,
        warnings=getattr(res, "warnings", None) or (),
    )
    path = os.path.join(out_dir, f"{stem}{HANDOFF_SUFFIX}")
    # Lead B-221 C1 硬要求：**不用 `fsutil.atomic_write`**——它会在第二次写/刷新时
    # 留下 `<name>.<时间戳>.bak`，污染产物目录（41 篇 DoD =「仅新增交接单文件」）。
    # 交接单是可再生成的汇总（不是孤本产物），普通覆盖写即可；`--no-handoff` 时
    # build 与 convert 两侧都不调用本函数，目录里不会有任何新增文件。
    # 内容确定性（Lead 硬要求二）：render() 只按固定顺序遍历 list/首次出现序，
    # 不含时间戳、绝对路径、集合遍历——同一输入两遍生成逐字节相同。
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    try:
        res.handoff_path = path
    except Exception:  # pragma: no cover —— 传进来的对象没有该槽位就算了
        pass
    if log:
        log(f"   初排交接单：{os.path.basename(path)}（四节汇总；--no-handoff 关闭）")
    return path
