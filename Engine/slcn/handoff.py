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


def _short(text: str, n: int = 40) -> str:
    """显示用截断（压空白；不参与任何判据）。"""
    v = " ".join(str(text or "").split())
    return v if len(v) <= n else v[: n - 1] + "…"


def _missed_items(bibdb: dict | None) -> list[tuple[str, str, str]]:
    """⑦ 未命中条目 → `[(etype, key, title)]`。

    数据全部来自 `bibdb_readonly_scan()` 的现成返回值（`missed` 是 key 列表，
    `entries` 是 `split_entries()` 的三元组），这里只做 key→标题的**查表**，
    不重算命中/身份判据（模块纪律）。
    """
    if not bibdb or not bibdb.get("done"):
        return []
    from .bibcheck import fields_of

    meta: dict[str, tuple[str, str]] = {}
    for row in bibdb.get("entries") or ():
        try:
            etype, key, body = row
        except (TypeError, ValueError):
            continue
        title = _short((fields_of(body or "").get("title") or ""), 60)
        meta[str(key)] = (str(etype), title)
    out: list[tuple[str, str, str]] = []
    for key in bibdb.get("missed") or ():
        etype, title = meta.get(str(key), ("", ""))
        out.append((etype, str(key), title))
    return out


def _missed_lines(bibdb: dict | None, limit: int = 20) -> list[str]:
    """B-268 S2：未命中条目点名（key + 标题；只点名，不改产物/不重判）。"""
    items = _missed_items(bibdb)
    if not items:
        return []
    lines = [f"引用库未命中（本次未回填，{len(items)} 条；仅点名，不动产物）："]
    for etype, key, title in items[:limit]:
        label = f"[{etype}] {key}" if etype else key
        lines.append(f"  · {label}" + (f"「{title}」" if title else "（无题名）"))
    if len(items) > limit:
        lines.append(f"  · …另 {len(items) - limit} 条略（库扫描明细见核对报告）")
    return lines


def _conflict_lines(bibdb: dict | None) -> list[str]:
    """B-268 S3：⑦ 回填覆盖库内已有值的统计行 + 样本（数据来自 applied[*].replaced）。"""
    if not bibdb or not bibdb.get("done"):
        return []
    from .bibdbapply import _CONFLICT_SAMPLES, conflict_summary

    counts, samples = conflict_summary(bibdb.get("applied") or ())
    if not counts:
        return []
    n = sum(counts.values())
    detail = "、".join(f"{name} {cnt}" for name, cnt in
                      sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    lines = [f"回填冲突（⑦ 覆盖库内已有值）：{n} 项（{detail}）"]
    for key, name, old, new in samples[:_CONFLICT_SAMPLES]:
        lines.append(f"  · [{key}] {name}：「{_short(old)}」→「{_short(new)}」")
    if n > len(samples):
        lines.append(f"  · …另 {n - len(samples)} 项见核对报告逐条行")
    return lines


#: 最终 `.tex` 里的英文前置宏（S2「已写入但形态非法」只看**产物实值**，不重推）。
_EN_MACROS = ("mytitleEN", "myfirstauthorEN", "mysecondauthorEN",
              "mythirdauthorEN", "myfourthauthorEN", "myfifthauthorEN")
_EN_MACRO_SET = frozenset(_EN_MACROS)
#: 兼容手改/旧夹具的行内写法 `\mytitleEN{值}`；模板实产是两行式（见 `_en_values`）。
_EN_VALUE_RE = re.compile(
    r"\\(mytitleEN|my(?:first|second|third|fourth|fifth)authorEN)\s*\{([^{}]*)\}")
_EMAIL_VALUE_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
#: 机构行形态（与 wordmeta 的判据同源；这里只用于**检查产物实值**，不参与抽取）。
_ORG_IN_VALUE_RE = re.compile(
    r"^\s*(?:School|Department|Institute|College|Faculty|Centre|Center|Academy|"
    r"Laboratory)\b|\b(?:Department|School|Institute|Faculty|College)\s+of\b", re.I)


def _en_values(tex_text: str) -> list[tuple[int, str, str]]:
    """取最终 `.tex` 里英文前置宏的**产物实值** → `[(位置, 宏, 值)]`（按出现顺序）。

    两种写法都认（B-268 V4 修复）：
      · 模板真实产物的两行式：`\\newcommand{\\mytitleEN}`（行尾可有注释）+ 下一行 `{值}`
        （中间允许空白/空行；同名 `\\renewcommand` 后写覆盖先写，注释掉的定义不算）——
        走 `latex.parse_macros`（其内部用 `find_all_macros`）的 LaTeX 感知解析（值可含嵌套花括号）；
      · 行内式 `\\mytitleEN{值}`（旧夹具/手改产物；保留兼容）。
    位置用于按产物顺序输出、并让同值去重保持确定性。
    """
    from .latex import parse_macros

    text = tex_text or ""
    # 同名宏以最后一次未注释的定义为准（与 `latex.get_macro_value` 语义一致）。
    defs = parse_macros(text)
    out = [(m.span[0], name, m.value)
           for name, m in defs.items() if name in _EN_MACRO_SET]
    for m in _EN_VALUE_RE.finditer(text):
        out.append((m.start(), m.group(1), m.group(2)))
    out.sort(key=lambda it: it[0])
    return out


def shape_items(tex_text: str) -> list[tuple[str, str, str]]:
    """B-268 S2：最终 `.tex` 里**已写入但形态非法**的英文前置 → `[(宏, 原因, 实值)]`。

    判据只看产物实值（不回头改抽取、不改产物）：
      · 含完整邮箱地址（模板实产形态：`\\newcommand{\\mytitleEN}` + 下一行
        `{lingm3@…}`，也兼容行内 `\\mytitleEN{lingm3@…}`）；
      · 含其它 `@` 符号；
      · `authorEN` / `titleEN` 的值是纯机构行（段首机构词或 `Department of …`）。
    同一 (宏, 原因, 实值) 只列一次，保持产物出现顺序（确定性）。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for _pos, macro, value in _en_values(tex_text):
        value = value.strip()
        if not value:
            continue
        if _EMAIL_VALUE_RE.search(value):
            reason = "写成了邮箱地址"
        elif "@" in value:
            reason = "含 @ 符号"
        elif _ORG_IN_VALUE_RE.search(value):
            reason = "像单位行"
        else:
            continue
        item = (macro, reason, _short(value, 60))
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _timepoint_line(bib: dict | None, bib_after: dict | None) -> str:
    """B-268 S2：计数「生成时点值 + 回填后值」双值并注时点。

    上值来自 ⑥ `_write_bib_report()` 的 bibcheck 结论（`res.handoff_bib`，口径冻结）；
    下值是 ⑦ 回填后用**同一 `bibcheck.check()` 实现**对最终 .bib 的重算——不是新判据，
    只补上「回填之后到底还剩多少」这一句（T5 的根因就是这一句缺失）。
    """
    if not bib_after:
        return "时点：上行为初排 ⑥ 生成时点值（无 ⑦ 回填后重算值）。"
    return ("时点：上行为初排 ⑥ 生成时点值；⑦ 引用库回填之后（同一 bibcheck 实现重算、"
            f"非新判据）：条目 {int(bib_after.get('total') or 0)} 条"
            f"（必补 {int(bib_after.get('must') or 0)}"
            f" / 建议 {int(bib_after.get('nice') or 0)}"
            f" / 未结构化 {int(bib_after.get('unstructured') or 0)}）")


def _bib_section(bib: dict | None, bibdb: dict | None, short_hits: int,
                 bib_after: dict | None = None) -> list[str]:
    """① bibcheck 结论 + 引用库回填 + 回填后值 + 冲突/未命中点名 + 核对报告指针。"""
    if not bib:
        return ["无（源稿未解析出参考文献条目，未生成核对报告）"]
    total = int(bib.get("total") or 0)
    must = int(bib.get("must") or 0)
    nice = int(bib.get("nice") or 0)
    unstr = int(bib.get("unstructured") or 0)
    report_path = str(bib.get("report_path") or "")
    missed = _missed_lines(bibdb)
    conflicts = _conflict_lines(bibdb)
    clean = (not report_path and not must and not nice and not unstr
             and not short_hits and _bibdb_applied(bibdb) == 0
             and not missed and not conflicts)
    if clean:
        # 反例口径：bibcheck 没发现问题、也没有回填/短引命中 → 该节无事可交接。
        return [f"无（bibcheck 未发现问题：条目 {total} 条，必补 0 / 建议 0 / "
                "未结构化 0；未生成参考文献核对报告）"]
    lines = [
        f"条目总数：{total}（必补 {must} / 建议 {nice} / 未结构化 {unstr}）",
        _timepoint_line(bib, bib_after),
        f"引用库安全命中（短引库→bib，生成期）：{short_hits} 条",
        _bibdb_line(bibdb),
    ]
    lines.extend(conflicts)
    lines.extend(missed)
    if report_path:
        lines.append(f"指针：{os.path.basename(report_path)}")
    else:
        lines.append("指针：无（bibcheck 未生成核对报告）")
    return lines


def _meta_section(undetected: list[tuple[str, str]], missing_fields: list[str],
                  meta_sources: list[str] | None = None,
                  meta_conflicts: list[str] | None = None,
                  shape: list[tuple[str, str, str]] | None = None) -> list[str]:
    """② 未识别元数据占位逐项 + 编辑侧未提供字段 + 补充信息（来源/冲突）+ 形态非法。"""
    lines: list[str] = []
    if undetected:
        lines.append(f"未识别元数据（产物中已写「【未识别：…】」占位符，"
                     f"{len(undetected)} 项）：")
        for label, macro in undetected:
            lines.append(f"  · {label}（{macro}）" if macro else f"  · {label}")
    if shape:
        # B-268 S2：**非空但不合法**的实值——占位符那类已在上一段，这里单列。
        lines.append(f"已写入但形态非法（{len(shape)} 项；产物实值原样，需人工改）：")
        for macro, reason, value in shape:
            lines.append(f"  · {macro}：{reason}「{value}」")
    if missing_fields:
        lines.append(f"编辑侧未提供字段（{len(missing_fields)} 项；"
                     "可用 --vol/--issue/--year/--pages 等补）：")
        for name in missing_fields:
            label = _EDITORIAL_LABELS.get(name)
            lines.append(f"  · {name}（{label}）" if label else f"  · {name}")
    # B-223：元数据 / 补充信息（来源逐份 + 正文已有未采用的冲突清单；旧输入下不出现）。
    if meta_conflicts:
        lines.append(f"补充信息冲突（正文已有该字段，未采用 {len(meta_conflicts)} 项）：")
        lines.extend(f"  · {item}" for item in meta_conflicts)
    if meta_sources:
        lines.append(f"元数据来源（本次用到的补充信息 {len(meta_sources)} 份）：")
        lines.extend(f"  · {item}" for item in meta_sources)
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
           warnings: list[str] | None = None,
           meta_sources: list[str] | None = None,
           meta_conflicts: list[str] | None = None,
           bib_after: dict | None = None) -> str:
    """渲染交接单全文（**纯函数**，便于逐字断言）。四节固定，空节写「无」。

    `bib_after`（B-268 S2，可选）= ⑦ 回填后用同一 bibcheck 实现重算的计数
    `{total,must,nice,unstructured}`；不给时只写 ⑥ 生成时点值（行为同旧）。
    """
    warnings = [str(w) for w in (warnings or ())]
    short_hits = sum(1 for w in warnings if w.startswith(BIBDB_SHORT_HIT_PREFIX))
    sections = [
        (SECTION_TITLES[0], _bib_section(bib, bibdb, short_hits, bib_after)),
        (SECTION_TITLES[1], _meta_section(list(undetected or ()),
                                          list(missing_fields or ()),
                                          list(meta_sources or ()),
                                          list(meta_conflicts or ()),
                                          shape_items(tex_text))),
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


def _bib_after_counts(res) -> dict | None:
    """B-268 S2：⑦ 回填之后对最终 .bib 的重算计数（同一 `bibcheck.check` 实现）。

    只读 `res.bib_path` 的终态；读不到/解析异常一律返回 None（交接单退回单时点口径，
    绝不连累出稿）。**这不是新判据**：就是把 ⑥ 的那次 `bibcheck.check()` 再跑一遍，
    让「回填后还剩多少」在单子上有一行——T5 的「口径冻结」正是因为缺这一行。
    """
    bib_path = str(getattr(res, "bib_path", "") or "")
    if not bib_path or not os.path.isfile(bib_path):
        return None
    try:
        from .bibcheck import check as _bibcheck
        with open(bib_path, encoding="utf-8", errors="replace") as f:
            rep = _bibcheck(f.read())
        return {"total": rep.total, "must": rep.n_must, "nice": rep.n_nice,
                "unstructured": rep.n_unstructured}
    except Exception:  # pragma: no cover —— 附加汇总，绝不连累出稿
        return None


def write_from_result(res, *, bibdb: dict | None = None,
                      log=None) -> str | None:
    """把 `BuildResult` 汇总成交接单写入其产物目录；返回路径（没法写则 None）。

    `build_docx()` 在全部产物写完后调用一次；`convert.py` 在 ⑦ 引用库回填之后带
    `bibdb=` 再调用一次刷新。B-268 S2 起两处都附「生成时点值 + 回填后值」：
    ⑥ 的计数来自 `res.handoff_bib`（口径冻结），⑦ 的重算值来自 `_bib_after_counts()`。
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
        meta_sources=getattr(res, "meta_sources", None) or (),
        meta_conflicts=getattr(res, "meta_conflicts", None) or (),
        bib_after=_bib_after_counts(res),
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
