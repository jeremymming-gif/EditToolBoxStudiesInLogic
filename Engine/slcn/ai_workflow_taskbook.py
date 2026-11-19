r"""R3 任务书上下文构建与完整渲染（2026-11-18，单一纯函数模块）。

契约（执行卡行 59，签名固定）：
    build_context(*, state, bib_text, tex_text, source_evidence, rule_sections) -> dict
    render_taskbook(*, context) -> str

职责边界：
  * 只把**冻结输入**组织成精确 v3 context 与一份足量、不截断的任务书文本；
  * 原文一律用 ``bibparse.Entry`` 的 ``block_start/end`` 绝对偏移逐字切片，
    不从 ``fields`` 字典重建，因此注释、宏、大小写保护、非 ASCII 全部保留；
  * 引用/依赖/字符串判据复用 ``ai_bib_candidate`` / ``bibparse``，不自造第二套；
  * 上下文保留源证据与规则，任务书只呈现当前类别适用的材料。
  * 本模块不写文件、不联网、不调模型、不产生副作用。
"""

from __future__ import annotations

import hashlib
import json
import re

from . import ai_bib_candidate, bibcheck, bibparse

__all__ = ["DEFAULT_RULE_SECTIONS", "build_context", "render_taskbook"]

DEFAULT_RULE_SECTIONS = [
    {"title": "人名和角色",
     "body": "英文作者、编者逐人写成“姓, 名缩写”，保留所有中间名首字母，用 `and` 区隔各人。"
             "姓氏中的 `van`、`von`、`de` 保持小写及原有连接，编者角色标记随姓氏。"
             "普通长名单按本刊规则，作者超过 4 人或编者超过 3 人用 `and others`。"
             "中文、日文姓名不倒装。普通中文多作者以 `and` 分隔。"
             "中文译著“原作者（著），译者（译）”保留中文逗号，各组多人用顿号。"},
    {"title": "语种与题名",
     "body": "只看作者和题名。含日文假名标 `userf={jp}`，否则含汉字标 `userf={cn}`，纯英文不填 `userf`。"
             "已有语言字段与作品一致。受句首大写处理的英文文章、章节和会议题名只给专名、术语、"
             "副标题首字母、罗马数字或变量所需字母加保护花括号，不锁住整句。"
             "书名和中文题名无需这种保护。英文题名标点用半角。"},
    {"title": "字段细节",
     "body": "连续页码用 `--`，期号去掉数字前导零，月份使用三字母缩写。"
             "正式发表的条目用 DOI 或网址定位来源，不把 DOI、URL 写回最终 Bib，ISBN 也仅做查证。"
             "未出版、在线资源及 SEP 按相应规则保留可核网址。"
             "不能确认卷、期、页或日期时说明待核，不补占位数据。"},
    {"title": "article（期刊论文）", "types": ("article",),
     "body": "核对作者、论文名、期刊全名、正式出版年、卷、期、页码或文章号。不用网页更新年代替出版年。"},
    {"title": "专著 `book`", "types": ("book",),
     "body": "核对作者／编者、书名、使用版本、出版者、出版地与出版年。"
             "英文版次写作“书名, (版次)”放入 title，出版地只在 address。"
             "只有编者时用外层双花括号保留整体名，形如 `author={{A. Example and B. Sample~(eds.)}}`。"
             "单人用 `(ed.)`，超过 3 人选 2–3 人，仍按编文集写法。另有实际作者时才单列 editor。"},
    {"title": "章节／会议论文 `incollection`／`inproceedings`",
     "types": ("incollection", "inproceedings", "conference"),
     "body": "核对章节或论文的作者和题名、母书／论文集名称、编者、出版者、出版地、出版年与章节页段。"
             "保留作者和编者的角色区别。`conference` 归为 `inproceedings`。"},
    {"title": "学位论文", "types": ("thesis", "mastersthesis", "phdthesis"),
     "body": "中文用 `thesis`，核对作者、题名、硕士／博士 `type`、`institution` 和 `date`。"
             "英文用 `mastersthesis`／`phdthesis`，核对作者、题名、`school`、`year`。"},
    {"title": "在线／未出版", "types": ("online", "unpublished", "electronic", "www"),
     "body": "核对作者／机构、资源原题、该版本日期及可访问的官方 URL。保留印出所需的 URL，"
             "避免把预印本和后来发表版本混作同一条目。"},
    {"title": "SEP 特例", "types": ("sep",),
     "body": "用 `incollection`，`booktitle` 为 `The Stanford Encyclopedia of Philosophy`，"
             "`publisher` 为 `Metaphysics Research Lab, Stanford University`。编者与实际存档页一致，"
             "版次按官网标注的季节加年份写到 edition，网址用同版 `archives/` 永久链接。"
             "官网没给版次时不推测。省略 pages、address、urldate。"
             "以该版官方 BibTeX 为证据，官方 `howpublished` 中的网址归入 url，原引用键保持不变。"},
    {"title": "无法确定类型", "types": ("other",),
     "body": "保留原类型与原文，标待核。已按整段原文排印的杂项保留 verbatim 字段，勿按普通作者名拆写。"},
]

_KNOWN_RULE_TYPES = {etype for section in DEFAULT_RULE_SECTIONS for etype in section.get("types", ())}


#: 三个执行/采纳类别（顺序即渲染顺序，固定）。
CATEGORIES = ("bib_verify", "typeset", "text")
CAT_LABELS = {
    "bib_verify": "文献核实",
    "typeset": "初排检查",
    "text": "文字纠错",
}

#: 结构块（@string / @comment / @preamble）不是文献条目，单独按原文切片收集。
_STRUCT_HEAD = re.compile(r"@\s*(string|comment|preamble)\s*[{(]", re.I)
_STRING_NAME = re.compile(r"@\s*string\s*[{(]\s*([A-Za-z][\w:+-]*)", re.I)

#: 常见类型的必备字段，只用于生成“离线发现”提示（不代替真实来源核对）。
_REQUIRED_BY_TYPE = {
    "article": ("author", "title", "journal", "year"),
    "book": ("author", "title", "publisher", "year"),
    "inproceedings": ("author", "title", "booktitle", "year"),
    "incollection": ("author", "title", "booktitle", "pages"),
    "phdthesis": ("author", "title", "school", "year"),
    "mastersthesis": ("author", "title", "school", "year"),
    "misc": ("author", "title", "year"),
}
_DEFAULT_REQUIRED = ("author", "title", "year")


# ── 小工具 ────────────────────────────────────────────────────────────────


def _ordered_unique(seq) -> list[str]:
    """保序去重，元素归一成 str。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in seq or []:
        key = str(raw)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _flag_map(values) -> dict[str, bool]:
    """把 scope.exec / scope.adopt 的类别列表归一成三键布尔表。"""
    chosen = set(values or [])
    return {name: (name in chosen) for name in CATEGORIES}


def _structural_blocks(text: str) -> tuple[list[dict], list[str], list[str]]:
    """按原文偏移切出 @string / @comment / @preamble 块。

    用外层花括号（或圆括号）配平收尾，支持 ``@string{acm = {X} # " Press"}``
    这类定义体里带花括号的写法。返回的 ``raw`` 是逐字原文，供任务书只读附给。
    """
    strings: list[dict] = []
    comments: list[str] = []
    preambles: list[str] = []
    text = text or ""
    for m in _STRUCT_HEAD.finditer(text):
        opener = text[m.end() - 1]
        closer = "}" if opener == "{" else ")"
        depth = 1
        j = m.end()
        end = -1
        while j < len(text):
            ch = text[j]
            if ch in "{(":
                depth += 1
            elif ch in ")}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
            j += 1
        if end < 0:
            end = len(text)
        raw = text[m.start():end]
        kind = m.group(1).lower()
        if kind == "string":
            nm = _STRING_NAME.match(raw)
            strings.append({"name": nm.group(1) if nm else "", "raw": raw})
        elif kind == "comment":
            comments.append(raw)
        else:
            preambles.append(raw)
    return strings, comments, preambles


def _evidence_map(source_evidence: dict, *names: str) -> dict:
    """在证据里按可能的键名找 {key: text} 映射，取到第一个非空字典。"""
    for name in names:
        value = source_evidence.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _offline_note(entry, *, sep: bool = False) -> str:
    """基于冻结字段的离线缺项提示；明确“不代表其余字段已核实”。"""
    fields = dict(getattr(entry, "fields", {}) or {})
    required = (("author", "title", "booktitle", "publisher", "url") if sep else
                _REQUIRED_BY_TYPE.get((entry.etype or "").lower(), _DEFAULT_REQUIRED))
    missing = [name for name in required if not str(fields.get(name, "")).strip()]
    if missing:
        return ("离线发现：缺 " + "、".join(missing)
                + "；此提示不表示其余字段已核实。")
    return "离线检查未发现明显缺字段；此提示不表示其余字段已核实。"


def _fence(text: str, lang: str = "") -> tuple[str, str]:
    """按材料里最长反引号游程生成足够长的围栏，避免材料把围栏提前关掉。"""
    longest = 0
    run = 0
    for ch in text or "":
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    ticks = "`" * max(3, longest + 1)
    return ticks + lang, ticks


def _line_list(keys, empty: str = "（无）") -> str:
    return "、".join(keys) if keys else empty


# ── build_context ─────────────────────────────────────────────────────────


def build_context(*, state: dict, bib_text: str, tex_text: str,
                  source_evidence: dict, rule_sections: list[dict]) -> dict:
    """把冻结输入组织成 CANONICAL CONTEXT SCHEMA v3；纯函数，零写入。"""
    state = dict(state or {})
    bib_text = bib_text or ""
    tex_text = tex_text or ""
    source_evidence = source_evidence if isinstance(source_evidence, dict) else {}
    rule_sections = list(rule_sections or [])

    # ── 冻结 Bib：解析 + 原文偏移表 ──────────────────────────────────────
    entries, _ = bibparse.parse_entries(bib_text)
    table: dict[str, object] = {}
    for e in entries:
        key = (e.key or "").strip()
        if key and key not in table:
            table[key] = e
    O = _ordered_unique([(e.key or "").strip() for e in entries if (e.key or "").strip()])
    O_set = set(O)

    # cited_keys 保留与 nocite{*} 同时出现的具体引用。
    has_tex = bool(tex_text.strip())
    scope = dict(state.get("scope") or {})
    execution = _flag_map(scope.get("exec") or [])
    adopt = _flag_map(scope.get("adopt") or [])
    entry = str(state.get("entry", "") or "")
    standalone_bib = entry == "bib" and not has_tex and execution["bib_verify"]
    # 转换侧可能另外给出「正文实际引用键」（Word 纯文本 `[N]` 清单没有 cite 命令，
    # 由 `ai_workflow_convert._number_cited_keys` 从条目原文前缀 + 正文 `[N]` 反推）。
    evidence_cited = {str(k) for k in (source_evidence.get("cited_keys") or []) if str(k)}
    cited = (bibcheck.cited_keys(tex_text) | set(map(str, state.get("cite_keys") or []))
             if has_tex else set())
    cited |= evidence_cited
    # `\nocite{*}` / state["nocite_all"] 只是「不必逐条核实」的信号，
    # **不得**把正文未引用的条目提升进必保集合 R（否则它们会整条进委派集合 T）。
    nocite = "*" in cited or bool(state.get("nocite_all"))
    cited.discard("*")
    C = [k for k in O if k in cited] + sorted(cited - O_set)
    cite_note = ""
    # 转换侧报告「正文有编号标记，但编号→条目映射判不出/判不全」（Word 纯文本编号
    # 清单的漏网写法）。此时**不得**退回保守全量：那会把正文未引用的条目原文整条
    # 送给 AI（红线 2）。只按已识别出来的引用键裁剪，并在 citation_scope 里说明。
    scope_unresolved = bool(source_evidence.get("scope_unresolved"))
    # 初排入口没有可识别引用时生成零条文献核查任务，避免把作者未引用条目送给 AI。
    # 引用库独立入口则以用户明确选择的 Bib 为核实对象，需核实其中全部有效条目。
    PRESERVED = "原 Bib 已在本地与交付候选保全（未删除任何条目或依赖）。"
    if standalone_bib:
        cite_note = ("引用库独立核实：以本次选定 Bib 为核实范围，覆盖全部 %d 条有效条目及其必要依赖；"
                     "安全命中沿用库内已核实记录，其余条目进入本次 AI 任务。"
                     "未另提供源侧参考文献原文或正文引用上下文；这些材料不作为逐条缺件。" % len(O)
                     + PRESERVED)
    elif scope_unresolved:
        cite_note = ("正文里有编号标记或脚注引用，但判不出对应的条目键："
                     "按已识别的引用键裁剪，**未识别部分不在此披露**"
                     "（请人工核对正文编号、脚注与文末清单）。" + PRESERVED)
    elif not cited:
        # 零引用 / 判不出：**零条核查任务**（旧实现此处退回 R=O 全量，属泄漏）。
        # `\\nocite{*}` 先判：它是「列出全部条目」的信号，但正文没有可核引用键，
        # 结果同样是零条——必须让编辑看见「为什么没有任务」，而不是笼统的缺正文。
        if nocite:
            cite_note = ("正文含 `\\nocite{*}`（语义是列出全部条目），但没有可核引用键："
                         "本次生成零条文献核查任务；未引用条目原文不在此披露。" + PRESERVED)
        elif not has_tex:
            cite_note = "未取得正文，无法识别可核引用：本次生成零条文献核查任务。" + PRESERVED
        else:
            cite_note = ("正文未识别到可核引用：本次生成零条文献核查任务；"
                         "未引用条目原文不在此披露。" + PRESERVED)
    elif not has_tex:
        cite_note = ("未取得正文，但转换侧已给出正文实际引用键 %d 个（Word 编号引用），"
                     "按此裁剪范围。" % len(evidence_cited) + PRESERVED)
    elif nocite:
        cite_note = ("正文含 `\\nocite{*}`：语义是列出全部条目，但本任务书只呈现"
                     "**正文实际引用到**的条目；未引用条目原文不在此披露。" + PRESERVED)
    else:
        cite_note = ("按正文实际引用的 %d 个键裁剪范围；未引用条目原文不在此披露。"
                     % len(cited) + PRESERVED)
    r_base = list(O) if standalone_bib else [k for k in O if k in cited]
    R_set, unresolved = ai_bib_candidate.closure(r_base, table, label="原")
    R = [k for k in O if k in R_set] + sorted((R_set | cited) - O_set)

    # ── 安全命中 F：命中键 + crossref/xdata 依赖闭包 ────────────────────
    safe_keys = [str(k) for k in (state.get("safe_hit_keys") or []) if str(k) in table]
    F_set, _f_unresolved = ai_bib_candidate.closure(safe_keys, table, label="原")
    F = _ordered_unique(
        [k for k in O if k in F_set] + [k for k in sorted(F_set) if k not in O_set]
    )

    # ── 委派集 T / 保全集 U / 已知缺条 M / 并集 A ──────────────────────
    if execution["bib_verify"]:
        T = [k for k in O if k in R_set and k not in F_set]
    else:
        T = []                      # 未选文献核实：不委派、不索 Bib 回复
    T_set = set(T)
    U = [k for k in O if k not in F_set and k not in T_set]
    M = [k for k in R if k not in O_set]
    allowed = {str(k) for k in (state.get("allowed_keys") or [])}
    A = O + [k for k in M if k in allowed]

    # ── 定义（独立命名空间，不与 entry 键混计）──────────────────────────
    strings, comments, preambles = _structural_blocks(bib_text)

    # ── 未知依赖：crossref/xdata 指向不在 O 的键”——如实记入缺件 ──────
    unknown_deps = _ordered_unique(
        dep
        for e in entries
        for dep in ai_bib_candidate.dep_keys(dict(getattr(e, "fields", {}) or {}))
        if dep not in O_set
    )

    # ── 逐条材料：原文逐字切片 + 稳定 ID ────────────────────────────────
    ref_map = _evidence_map(source_evidence, "refs_by_key", "original_refs", "raw_refs_by_key")
    ctx_map = _evidence_map(source_evidence, "cite_contexts", "cite_context_by_key")
    items: list[dict] = []
    for index, e in enumerate(entries):
        key = (e.key or "").strip()
        if not key:
            continue
        raw = bib_text[e.block_start:e.end + 1]
        # item_id 只随“冻结原文 + 稳定来源位置”变；不含 task_id / 采纳 / 输出路径。
        item_id = "B-" + hashlib.sha256(
            (str(index) + "\x00" + raw).encode("utf-8")
        ).hexdigest()[:12]
        original_ref = str(ref_map.get(key, "") or "")
        cite_context = str(ctx_map.get(key, "") or "")
        items.append({
            "item_id": item_id,
            "key": key,
            "etype": e.etype or "",
            "raw": raw,
            "offset": int(e.start),
            "crossref_deps": ai_bib_candidate.dep_keys(dict(getattr(e, "fields", {}) or {})),
            "original_ref": original_ref,
            "cite_context": cite_context,
        })

    # ── 覆盖与缺件 ──────────────────────────────────────────────────────
    body_units = list(source_evidence.get("body_units") or [])
    # 源侧文献原文只以「正文实际引用键 → 该条源稿原文」的映射进入任务书；
    # 整段 `raw_refs`（整份 .bib / Word 文末参考文献区）不再作为材料来源。
    src_has_refs = bool(ref_map)
    if standalone_bib:
        source_side = ("独立 Bib 核实：以选定 Bib 原文为核实依据（有效条目 %d 条）；"
                       "未另提供源侧参考文献原文或正文引用上下文。" % len(O))
        fidelity = "未执行独立源稿对照；按选定 Bib 原文核实书目信息。"
    elif src_has_refs:
        source_side = ("源侧原始参考文献原文已提供（限正文实际引用的 %d 条）；"
                       "正文单元 %d 个。" % (len(ref_map), len(body_units)))
        fidelity = ("源/产物对照材料已提供，可逐项核对；"
                    "未提供对应关系的项仍须写“未定位/材料未取得”。")
    else:
        source_side = ("源侧原始参考文献原文未取得；正文单元 %d 个。"
                       "只能说明生成态内部问题，不得声称原稿不存在某内容。" % len(body_units))
        fidelity = "未执行（源侧材料不足，仅能检查生成态内部问题）"

    missing: list[str] = []
    for dep in unknown_deps:
        missing.append("依赖键 `%s`：crossref/xdata 指向的条目不在冻结 Bib 中（未伪造条目）" % dep)
    for other in (source_evidence.get("missing") or []):
        missing.append(str(other))
    for note in unresolved:
        missing.append(str(note))

    coverage = {
        "source_side": source_side,
        "generated_side": ("冻结输入 Bib %d 条（按原文逐字切片）；引用键 %d 个；"
                           "定义 @string %d / @comment %d / @preamble %d。"
                           % (len(O), len(C), len(strings), len(comments), len(preambles))),
        "fidelity_check": fidelity,
        "missing": missing,
        "citation_scope": cite_note,
    }

    return {
        "version": 3,
        "task_id": str(state.get("task_id", "") or ""),
        "entry": entry,
        "standalone_bib": standalone_bib,
        "execution": execution,
        "adopt": adopt,
        "sets": {"O": O, "C": C, "R": R, "F": F, "T": T, "U": U, "M": M, "A": A},
        "definitions": {"strings": strings, "comments": comments, "preamble": preambles},
        "items": items,
        "source_evidence": source_evidence,
        "coverage": coverage,
        "body_units": body_units,
        "rule_sections": rule_sections,
        "fn_items": list(source_evidence.get("fn_items") or []),
        "meta_items": list(source_evidence.get("meta_items") or []),
    }


# ── render_taskbook ───────────────────────────────────────────────────────


def _offline_note_from_raw(item: dict) -> str:
    """从已切出的原文粗略判断缺字段（仅作离线提示，不作核实结论）。

    复用 ``bibparse.parse_one`` 对**该条原文**做一次字段抽取；不从生产字典重建
    任务书原文，这里只借它生成“离线发现”一句提示。
    """
    parsed, _ = bibparse.parse_one(str(item.get("raw", "")))
    if parsed is not None:
        return _offline_note(parsed, sep="sep" in _entry_rule_types(item))
    return "离线检查未能解析该条字段；请按原文逐项核对，此提示不代表其余字段已核实。"


def _add_data(lines: list[str], text: str, lang: str = "text") -> None:
    opener, closer = _fence(text, lang)
    lines.extend([opener, text, closer, ""])


_SEP_MARKER = re.compile(
    r"the stanford encyclopedia of philosophy|(?:https?://)?plato\.stanford\.edu(?=[/\s}>)]|$)", re.I,
)


def _entry_rule_types(item: dict) -> set[str]:
    etype = str(item.get("etype") or "").lower()
    sources = [str(item.get("original_ref") or "")]
    if item.get("raw"):
        entries, _ = bibparse.parse_entries(item["raw"])
        if entries:
            fields = entries[0].fields
            sources.extend(fields.get(name, "") for name in ("booktitle", "publisher", "url", "howpublished"))
    if etype == "sep" or any(_SEP_MARKER.search(text) for text in sources):
        return {"sep"}
    return {etype if etype in _KNOWN_RULE_TYPES else "other"}


_STRING_REFERENCE_TOKEN = re.compile(r'\\.|[{}"]|[=#]\s*([A-Za-z][\w:+-]*)')


def _string_references(body: str) -> set[str]:
    """Collect bare atoms in Bib expressions, including concatenated values."""
    used = set()
    depth = 0
    quoted = False
    for token in _STRING_REFERENCE_TOKEN.finditer(body):
        value = token.group()
        if value == '"' and depth == 0:
            quoted = not quoted
        elif not quoted:
            if value == "{":
                depth += 1
            elif value == "}":
                depth = max(0, depth - 1)
            elif depth == 0 and token.group(1):
                used.add(token.group(1).lower())
    return used - ai_bib_candidate.BUILTIN_MONTHS


def _used_definitions(strings: list[dict], entries: list[dict]) -> list[str]:
    by_name = {block["name"].lower(): block for block in strings}
    pending = set()
    for item in entries:
        parsed, _ = bibparse.parse_entries(item["raw"])
        for entry in parsed:
            pending.update(_string_references(entry.body))
    used = set()
    while pending:
        name = pending.pop()
        block = by_name.get(name)
        if block and name not in used:
            used.add(name)
            value = block["raw"].partition("=")[2][:-1]
            pending.update(_string_references("=" + value) - used)
    return [block["raw"] for block in strings if block["name"].lower() in used]


def _render_bib(lines: list[str], ctx: dict, evidence: dict) -> bool:
    """呈现委派材料，返回本次是否需要文献回复。"""
    add = lines.append
    sets = ctx["sets"]
    T, M = sets["T"], sets["M"]
    by_key = {}
    for item in ctx.get("items") or []:
        by_key.setdefault(item["key"], item)
    ref_map = _evidence_map(evidence, "refs_by_key", "original_refs", "raw_refs_by_key")
    supplied_contexts = _evidence_map(evidence, "cite_contexts", "cite_context_by_key")
    generated_contexts = evidence.get("generated_cite_contexts") or {}
    standalone_bib = bool(ctx.get("standalone_bib"))
    types_by_key = evidence.get("entry_types_by_key") or {}
    missing = {
        key: {"original_ref": str(ref_map.get(key) or ""),
              "cite_context": str(supplied_contexts.get(key) or generated_contexts.get(key) or ""),
              "etype": types_by_key.get(key)}
        for key in M
    }
    allowed = set(sets["A"])
    identified = {key for key in M if any(str(value or "").strip() for value in
                                         (ref_map.get(key), supplied_contexts.get(key)))}
    fillable = allowed & identified
    cited = set(sets["C"])
    cited_missing = [key for key in M if key in cited]
    dep_missing = [key for key in M if key not in cited]
    add("## 文献核实")
    if ctx.get("coverage", {}).get("citation_scope"):
        add(ctx["coverage"]["citation_scope"])
    if standalone_bib:
        add(f"- 可用材料：待核条目 {len(T)} 条，必要依赖缺条 {len(dep_missing)} 条。")
    else:
        add(f"- 可用材料：待核条目 {len(T)} 条，正文引用缺条 {len(cited_missing)} 条，必要依赖缺条 {len(dep_missing)} 条。")
    if T or fillable:
        lines.extend([
            "",
            "### 来源与不确定性",
            "先确认原参考文献、当前条目和查到的资料指向同一作品及同一版本，再逐项核对该类型适用的字段。"
            "已有字段也要核查。优先引用出版社、期刊、DOI 落地页或官方档案。聚合页面仅供找线索。"
            "来源冲突、无法访问或查不到时，保留有依据的原值，缺值留空，在条目上方说明待核字段与原因。",
            "",
            "每条呈现稳定编号、键、类型、离线提示、可得原参考文献与引用上下文、完整当前 BibTeX。"
            "离线提示只反映格式或缺项。原文和解析结果不一致时，先用原文识别作品，再据可核来源纠正解析错误。",
            "",
            "所有委派条目逐条交回完整 BibTeX，未变的条目也交回。"
            "每条上方用 `% 来源：…` 或 `% 待核实：字段（原因）` 标注实际情况。"
            "保留引用键，不编造书目信息，不替换原作者引用的版本。",
            "",
            "### 适用规则",
        ])
        types = set()
        for item in [by_key[k] for k in T] + [missing[k] for k in M if k in fillable]:
            types.update(_entry_rule_types(item))
        for rule in ctx.get("rule_sections") or []:
            if not rule.get("types") or types.intersection(rule["types"]):
                lines.extend([f"#### {rule['title']}", rule["body"], ""])

    if T:
        add("### 文献待核材料")
    for key in T:
        item = by_key[key]
        add(f"#### {item['item_id']} · {key} · @{item['etype']}")
        add(_offline_note_from_raw(item))
        for label, value in (
            ("原参考文献", item.get("original_ref")),
            ("引用上下文", item.get("cite_context")),
            ("生成态引用上下文", generated_contexts.get(key) if not item.get("cite_context") else ""),
        ):
            if value:
                add(label)
                _add_data(lines, str(value))
        if not standalone_bib and not item.get("original_ref"):
            add("- 缺件：该键的源参考文献原文未取得。")
        if not standalone_bib and not (item.get("cite_context") or generated_contexts.get(key)):
            add("- 缺件：该键的引用上下文未取得。")
        _add_data(lines, item["raw"], "bibtex")

    for heading, keys in (("正文已引用，Bib 缺条", cited_missing), ("必要依赖缺条", dep_missing)):
        if not keys:
            continue
        add(f"### {heading}")
        for key in keys:
            item = missing[key]
            if key in fillable:
                detail = "确认为同一来源后可按原键交回完整条目"
            elif key in identified:
                detail = "待人工确认，本次未授权补条"
            else:
                detail = "待人工确认，现有材料不足以识别作品，不补条"
            add(f"#### {key} · {detail}")
            if item["original_ref"]:
                add("原参考文献")
                _add_data(lines, item["original_ref"])
            if item["cite_context"]:
                add("引用上下文" if supplied_contexts.get(key) else "生成态引用上下文")
                _add_data(lines, item["cite_context"])

    add("### 软件保全说明")
    safe_label = "安全命中（沿用库内已核实记录）" if standalone_bib else "安全命中"
    for label, keys in ((safe_label, sets["F"]), ("其他未委派", sets["U"])):
        known = [key for key in keys if key in by_key]
        add(f"- {label} {len(known)} 条。")
    pending = list(T)
    dependencies = set(T)
    while pending:
        item = by_key.get(pending.pop())
        if not item:
            continue
        additional = set(item["crossref_deps"]) - dependencies
        dependencies.update(additional)
        pending.extend(additional)
    for key in sorted(dependencies - by_key.keys() - set(M)):
        add(f"- 缺件：委派条目所需的依赖键 `{key}` 在当前 Bib 中缺失。")
    read_only = [by_key[key] for key in sets["O"] if key in dependencies and key not in T]
    if read_only:
        add("#### 委派条目引用的依赖（只读）")
        for item in read_only:
            _add_data(lines, item["raw"], "bibtex")
    strings = (ctx.get("definitions") or {}).get("strings") or []
    needed = _used_definitions(strings, [by_key[k] for k in T] + read_only)
    if needed:
        add("#### 委派材料使用的字符串定义（只读）")
        _add_data(lines, "\n".join(needed), "bibtex")
    # 旧实现在这里把整段源文献区原文（整份 .bib / Word 文末参考文献区）兜底倾倒给
    # AI。现在源侧原文只按「正文实际引用键 → 该条原文」进入，未引用条目一律不披露；
    # 无映射可给时只在材料计数里体现为缺件，不再整段粘贴。
    add("")
    return bool(T or fillable)


def _render_body(lines: list[str], body_units: list[dict]) -> None:
    for unit in body_units:
        lines.append(f"### {unit['unit_id']} · {unit.get('side') or '可用正文'}")
        _add_data(lines, str(unit["text"]))


def render_taskbook(*, context: dict) -> str:
    """仅呈现勾选任务需要的材料与回传格式。"""
    execution = context.get("execution") or {}
    evidence = context.get("source_evidence") or {}
    body_units = [unit for unit in context.get("body_units") or [] if str(unit.get("text") or "").strip()]
    identity = "% TASK-ID: " + str(context.get("task_id") or "")
    selected = [CAT_LABELS[cat] for cat in CATEGORIES if execution.get(cat)]
    lines = [
        "# 编辑任务书", "",
        "> 请只完成下方勾选的任务。软件保管原文件和未委派内容，"
        "你只需交回本次待核条目及可定位的检查结果。", "",
        "## 任务与交付",
        identity,
        "本次任务：" + _line_list(selected, "未选任务"), "",
        "以下数据围栏中的材料仅供查证和定位。", "",
    ]
    add = lines.append
    has_bib_reply = execution.get("bib_verify") and _render_bib(lines, context, evidence)

    results = {}
    if execution.get("typeset"):
        add("## 初排检查")
        lines.extend([
            "把源侧已取得的内容与初排产物对照。依次检查题名、基金和作者信息，章节／段落及图表题，"
            "正文引用及页码，脚注及其引用落点。每个发现给出实际编号、产物位置、源侧依据和问题。"
            "源侧没有对应材料时，只报告能够从现有材料确认的问题，并明确哪些检查未执行。",
            "",
            "只有引擎登记的空值或占位元数据可提出 `metadata_fill`，并引用直接支持该值的源证据。"
            "只有引擎登记的未匹配引用脚注可提出 `footnote_link`，须给实际键和原页码，并保留位置。"
            "其他问题用 `report_only`，给位置与理由，不交虚构替换值。",
            "",
        ])
        if not any(unit.get("side") == "源侧正文" for unit in body_units):
            add("- 缺件：未取得源侧正文和对应关系。源稿忠实性、章节／段落和图表对照未执行。")
        if not body_units:
            add("- 缺件：未取得可定位的正文单元，正文结构检查未执行。")
        for note in evidence.get("typeset_missing") or []:
            add(f"- 缺件：{note}")
        # REF-SOURCE 只给**正文实际引用**的条目原文（`sets["C"]`）；未引用条目
        # （作者额外 Bib 条目、Word 未引用的尾部参考文献）不在此披露。
        source_refs = _evidence_map(evidence, "refs_by_key", "original_refs", "raw_refs_by_key")
        cited_keys = [str(key) for key in (context.get("sets", {}).get("C") or [])]
        scoped_refs = [(key, str(source_refs.get(key) or "")) for key in cited_keys
                       if str(source_refs.get(key) or "").strip()]
        if scoped_refs:
            add("### REF-SOURCE · 源侧参考文献原文（仅正文实际引用的条目）")
            for key, ref in scoped_refs:
                add(f"- 对应引用键：`{key}`")
                _add_data(lines, ref)
        else:
            add("- 缺件：未取得源侧参考文献原文（或源侧条目与正文引用键对不上）。")
        for label, rows in (
            ("META-CURRENT · 产物当前字段", evidence.get("current_fields")),
            ("WARN · 转换警告（仅供定位）", evidence.get("conversion_warnings")),
            ("已登记的脚注", context.get("fn_items")),
            ("已登记的元数据槽位", context.get("meta_items")),
        ):
            if rows:
                add(f"### {label}")
                _add_data(lines, json.dumps(rows, ensure_ascii=False, indent=2))
        if body_units:
            _render_body(lines, body_units)
        has_typeset_material = bool(body_units or evidence.get("current_fields") or
                                    context.get("fn_items") or context.get("meta_items") or
                                    scoped_refs)
        reason = ("示例只覆盖已提供的产物或片段，须逐项说明缺少的对照。"
                  if has_typeset_material else "缺少可定位的初排对照材料。")
        results["typeset"] = {"status": "partial" if has_typeset_material else "not_run",
                              "items": [], "reason": reason}
        add("")
    if execution.get("text"):
        add("## 文字纠错")
        add("仅指出明确错字、缺字、语法错误或语病。给出正文单元编号、原文、最小局部替换、前后文及原因。"
            "公式、引用标记、宏、脚注结构、术语与原表达受保护。"
            "不要润色、改语气、替换偏好用词、整句重写或改变论证。有歧义只报告，由编辑判断。")
        if execution.get("typeset") and body_units:
            add("请使用初排检查节中的正文单元：" + _line_list([unit["unit_id"] for unit in body_units]) + "。")
        elif body_units:
            _render_body(lines, body_units)
        else:
            add("- 缺件：未取得正文单元，文字纠错未执行。")
        results["text"] = ({"status": "completed", "items": []} if body_units else
                           {"status": "not_run", "items": [], "reason": "未取得正文单元。"})
        add("")

    add("## 回传格式与自检")
    add(f"首行原样复制 `{identity}`。")
    if has_bib_reply:
        add("使用一个标为 bibtex 围栏的代码块，只放全部待核条目（含未变条目）和已确认同一来源的授权补齐条目。")
    if results:
        add("使用一个标为 json 的围栏。类别状态用 `completed`（完成）、`partial`（部分执行）"
            "或 `not_run`（未执行），后两种须给 `reason`。以下是空条目格式示例，请按实际结果填写。")
        if execution.get("typeset"):
            add("- `metadata_fill` 提供 item_id、action、target、old、new、reason、evidence_ids。"
                "`footnote_link` 提供 item_id、action、old、new、key、locator、reason、evidence_ids。"
                "`report_only` 提供 item_id、action、reason、evidence_ids，有位置便附上。")
        if execution.get("text"):
            add("- 文字项提供 item_id、unit_id、old、new、before、after、kind、reason、certainty。"
                "kind 取 typo、missing_char、grammar 或 uncertain，certainty 取 clear 或 uncertain。"
                "新增字词用含原有文字的非空锚点定位。")
        _add_data(lines, json.dumps({"schema": "slcn.ai_result/3", **results}, ensure_ascii=False, indent=2), "json")
    if has_bib_reply:
        add("交回前核对键未改、待核条目齐全、来源或待核说明可查。")
    add("交回前核对未选类别无结果。")
    return "\n".join(lines) + "\n"
