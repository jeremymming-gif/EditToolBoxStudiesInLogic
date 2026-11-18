#!/usr/bin/env python3.14
r"""R3 精简回复解析层（规格 §7 / 执行卡 Task 3 + C 包，2026-11-18）。

职责边界（只做这一件事）
------------------------
不再造 Bib 解析器：BibTeX 的平衡解析统一复用 ``ai_bib_candidate.entries_by_key``
（其底层是 ``bibparse.parse_entries``）。本模块只负责：

  1. 识别**边界**：只取明确标识的 ``bibtex`` 围栏区与唯一的 ``schema`` JSON 区；
     叙述文字 / JSON 字符串里的 ``@book`` 示例一律不算数据。
  2. 校验**身份**：``% TASK-ID:`` 头与 context.task_id 比对；错 ID / 多冲突 ID 整份拒收。
  3. 保留**注释与定义**：围栏内原文整段作为 bib_text 交出（含前导 ``%`` 注释、
     ``@string`` / ``@comment`` / ``@preamble``），由下游保全逻辑消费。
  4. 类别**分离**：只有 execution[typeset/text] 为真才收对应 JSON items；
     不支持的动作逐项进 rejected 隔离，不使整份失败，也不进入活动结果。
  5. **覆盖**：coverage.missing_t 标出 T 中漏交的键；F/U 正常免交不算漏交。

C 包补充（2026-11-18b，一份 Markdown 一次导入）
-----------------------------------------------
  6. **可定位**：每条拒收理由都带位置/标识——围栏语言与开围栏行号、是否闭合、
     TASK-ID 的声明值/行号与要求值、条目键、缺件键、多余键、JSON 区个数。
     机器可读版在 ``errors``（``code`` + ``message`` + 可选 ``line``）。
  7. **分支提示**：``branch`` / ``branches`` / ``branch_hint`` / ``next_step``
     说明这份回件属于哪一类范围（委派子集 / 含缺件 / 外部完整 Bib /
     未验证纯 Bib / 报告式只读项 / 拒收）以及下一步做什么。
  8. 以上都是**追加键**：``ok`` / ``identity_status`` / ``source_kind`` /
     ``bib_text`` / ``typeset`` / ``text`` / ``coverage`` / ``rejected``
     八个既有键的语义与形状不变，state_v3.import_reply 的调用契约不变。

任何输入都返回结果，不抛异常；拒收时 bib_text 置空，杜绝从原文重扫得到“可用”候选。
"""

from __future__ import annotations

import json
import re
from typing import NamedTuple

from .ai_bib_candidate import entries_by_key
from .ai_workflow_taskbook import _structural_blocks

SCHEMA = "slcn.ai_result/3"

#: 身份头：整行注释形式，行首可选空白。
_TASKID_RE = re.compile(r"^[ \t]*%[ \t]*TASK-ID:[ \t]*([^\s]+)[ \t]*\r?$", re.M)
#: 围栏识别（行级状态机用）：开围栏带语言标签，闭围栏只有反引号。
_FENCE_OPEN_RE = re.compile(r"^[ \t]*(`{3,})[ \t]*([A-Za-z0-9_+-]*)[ \t]*$")
_FENCE_CLOSE_RE = re.compile(r"^[ \t]*(`{3,})[ \t]*$")

#: 支持动作白名单（规格 §8：typeset 只真正支持 metadata_fill / footnote_link + report_only）。
_TYPESET_ACTIONS = frozenset(("metadata_fill", "footnote_link", "report_only"))
_TEXT_KINDS = frozenset(("typo", "missing_char", "grammar", "uncertain"))
_TEXT_CERTAINTY = frozenset(("clear", "uncertain"))
_STATUSES = frozenset(("completed", "partial", "not_run"))

# ── 分支标识（C 包 3.3）：一份回件属于哪一类范围，决定下一步做什么 ──────
BRANCH_REJECTED = "rejected"
BRANCH_DELEGATED_SUBSET = "delegated_subset"
BRANCH_MISSING_ENTRIES = "missing_entries"
BRANCH_EXTERNAL_COMPLETE_BIB = "external_complete_bib"
BRANCH_UNVERIFIED_PLAIN_BIB = "unverified_plain_bib"
BRANCH_REPORT_ONLY = "report_only"

#: 拒收后的统一下一步（不拆多份、不静默降级）。
_NEXT_STEP_REJECTED = ("按上面指出的位置修改回件后，整份重新导入；"
                       "不要拆成多份，也不要只改一半。")


# ── 边界识别 ──────────────────────────────────────────────────────────────


class _Fence(NamedTuple):
    """一个围栏块：语言、内容、是否闭合、开/闭围栏行号（1 基）、反引号数。"""

    lang: str
    body: str
    closed: bool
    start_line: int
    end_line: int
    ticks: int


def _scan_fences(text: str) -> list[_Fence]:
    """扫描独占一行的 ``` 围栏，带位置信息（C 包：错误要能定位到行）。

    只认**独占一行**的 ``` 围栏；闭围栏反引号数不少于开围栏。
    围栏之外的叙述字符串不进任何结果块——这正是“叙述里的 @book 不算数据”的保证。
    未闭合的围栏 ``closed=False``，``end_line`` 记到回件末行。
    """
    lines = text.splitlines(keepends=True)
    total = len(lines)
    blocks: list[_Fence] = []
    i = 0
    while i < total:
        opening = _FENCE_OPEN_RE.match(lines[i].rstrip("\r\n"))
        if not opening or not opening.group(2):
            i += 1
            continue
        ticks, lang = opening.group(1), opening.group(2).lower()
        start_line = i + 1
        i += 1
        body: list[str] = []
        closed = False
        end_line = total
        while i < total:
            cur = lines[i].rstrip("\r\n")
            closing = _FENCE_CLOSE_RE.match(cur)
            if closing and len(closing.group(1)) >= len(ticks):
                closed = True
                end_line = i + 1
                i += 1
                break
            body.append(lines[i])
            i += 1
        blocks.append(_Fence(lang, "".join(body), closed, start_line, end_line,
                             len(ticks)))
    return blocks


def _extract_fences(text: str) -> list[tuple[str, str, bool]]:
    """返回 ``[(language, inner_text, closed), ...]``（旧签名，保留兼容）。"""
    return [(f.lang, f.body, f.closed) for f in _scan_fences(text)]


def _task_id_marks(text: str) -> list[tuple[str, int]]:
    """所有 ``% TASK-ID:`` 声明，带 1 基行号（C 包：错误要报声明位置）。"""
    marks: list[tuple[str, int]] = []
    for match in _TASKID_RE.finditer(text):
        marks.append((match.group(1), text.count("\n", 0, match.start()) + 1))
    return marks


def _identity(marks: list[tuple[str, int]], task_id: object) -> tuple[str, str, str]:
    """返回 ``(identity_status, error_or_empty, error_code)``。"""
    if not marks:
        return "unverified", "", ""
    distinct: list[tuple[str, int]] = []
    for value, line in marks:
        if value not in [v for v, _ in distinct]:
            distinct.append((value, line))
    expected = str(task_id or "")
    if len(distinct) > 1:
        listed = "、".join(f"{value}（第 {line} 行）" for value, line in distinct)
        return ("mismatch",
                f"多个不一致的 TASK-ID：{listed}，任务要求 {expected}"
                "；整份按拒收处理，不采用其中任何结果",
                "task_id_conflict")
    value, line = distinct[0]
    if value != expected:
        return ("mismatch",
                f"TASK-ID 不符：回件第 {line} 行声明 {value}，任务要求 {expected}"
                "；整份按拒收处理，不采用其中任何结果",
                "task_id_mismatch")
    return "verified", "", ""


def _rejected(*, identity_status: str, source_kind: str, reason: str,
              extra=None, code: str = "", line: int | None = None) -> dict:
    """整份拒收：bib_text 置空，类别一律 not_run。

    除既有八键外，追加 ``errors`` / ``branch`` / ``branches`` / ``branch_hint`` /
    ``next_step`` / ``diagnostics``（C 包 3.2–3.3）。
    """
    rejected = list(extra or [])
    if reason and reason not in rejected:
        rejected.append(reason)
    error: dict = {"code": code or "rejected", "message": reason}
    if line is not None:
        error["line"] = line
    hint = f"整份拒收：{reason}。{_NEXT_STEP_REJECTED}"
    return {
        "ok": False,
        "identity_status": identity_status,
        "source_kind": source_kind,
        "bib_text": "",
        "typeset": {"status": "not_run", "items": [], "reason": reason},
        "text": {"status": "not_run", "items": [], "reason": reason},
        "coverage": {"missing_t": [], "rejected": True},
        "rejected": rejected,
        # —— C 包追加键（只增不改） ——
        "errors": [error],
        "diagnostics": [],
        "branch": BRANCH_REJECTED,
        "branches": [BRANCH_REJECTED],
        "branch_hint": hint,
        "next_step": _NEXT_STEP_REJECTED,
    }


# ── 类别分离 ──────────────────────────────────────────────────────────────


def _filter_typeset(items: list, rejected: list) -> list:
    kept = []
    for item in items:
        if not isinstance(item, dict):
            rejected.append({"category": "typeset", "reason": "建议项不是对象"})
            continue
        item_id = str(item.get("item_id") or "")
        action = item.get("action")
        if not isinstance(action, str) or action not in _TYPESET_ACTIONS:
            rejected.append({"category": "typeset", "item_id": item_id,
                             "reason": f"不支持的动作：{action!r}"})
            continue
        if not item_id:
            rejected.append({"category": "typeset", "reason": "建议项缺 item_id"})
            continue
        if action == "report_only" and not str(item.get("reason") or "").strip():
            # report_only 必填 item_id / reason / evidence_ids；不强制 old/new。
            rejected.append({"category": "typeset", "item_id": item_id,
                             "reason": "report_only 缺 reason"})
            continue
        kept.append(item)
    return kept


def _filter_text(items: list, rejected: list) -> list:
    kept = []
    for item in items:
        if not isinstance(item, dict):
            rejected.append({"category": "text", "reason": "建议项不是对象"})
            continue
        item_id = str(item.get("item_id") or "")
        if not item_id:
            rejected.append({"category": "text", "reason": "建议项缺 item_id"})
            continue
        kind, certainty = item.get("kind"), item.get("certainty")
        if (not isinstance(kind, str) or kind not in _TEXT_KINDS
                or not isinstance(certainty, str) or certainty not in _TEXT_CERTAINTY):
            rejected.append({"category": "text", "item_id": item_id,
                             "reason": f"kind/certainty 非法：{kind!r}/{certainty!r}"})
            continue
        if any(not isinstance(item.get(field), str) for field in
               ("unit_id", "old", "new", "before", "after", "reason")):
            rejected.append({"category": "text", "item_id": item_id,
                             "reason": "文字建议缺有效字段"})
            continue
        kept.append(item)
    return kept


def _category_result(name: str, selected: bool, json_obj, rejected: list) -> dict:
    """未选类别一律 not_run；未交类别 not_run；status 固定枚举。"""
    if not selected:
        return {"status": "not_run", "items": [], "reason": "该类别未选择执行"}
    if not isinstance(json_obj, dict) or name not in json_obj:
        return {"status": "not_run", "items": []}
    node = json_obj[name]
    if not isinstance(node, dict):
        return {"status": "not_run", "items": [], "reason": "类别节点结构无效"}
    status = node.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        rejected.append({"category": name, "reason": f"非法 status：{status!r}"})
        return {"status": "not_run", "items": [], "reason": "类别 status 非法"}
    reason = node.get("reason")
    if status in ("partial", "not_run") and (not isinstance(reason, str) or not reason.strip()):
        return {"status": "not_run", "items": [], "reason": "未提供部分执行或未执行的原因"}
    raw_items = node.get("items", [])
    if not isinstance(raw_items, list):
        return {"status": "not_run", "items": [], "reason": "建议列表无效"}
    if status == "not_run":
        return {"status": status, "items": [], "reason": reason}
    kept = (_filter_typeset(raw_items, rejected) if name == "typeset"
            else _filter_text(raw_items, rejected))
    result = {"status": status, "items": kept}
    reason = node.get("reason")
    if status in ("partial", "not_run") or reason is not None:
        result["reason"] = reason if isinstance(reason, str) else str(reason)
    return result


# ── 纯 Bib 判定（可定位版） ───────────────────────────────────────────────


def _plain_bib_offenders(text: str, line_offset: int = 0) -> list[tuple[int, str]]:
    """返回 ``[(行号, 行内容摘要), ...]``：Bib 条目/定义/注释之外的非空行。

    这是“未验证身份时只接受纯 Bib”的定位依据：夹带叙述就点名到行。

    ``line_offset`` 把「相对本段文本的行号」换算成「回件里的真实行号」：围栏内
    正文第 1 行其实是回件的第 ``开围栏行号 + 1`` 行，所以调用方传
    ``fence.start_line``；无围栏（整份就是 Bib）时传 0。行号算错会让用户照着
    报错去改错行，故单独开这个参数而不是靠调用方事后加偏移。
    """
    entries, _problems = entries_by_key(text)
    intervals = [(entry.start, entry.end + 1) for entry in entries.values()]
    definitions, comments, preamble = _structural_blocks(text)
    for raw in [*(item["raw"] for item in definitions), *comments, *preamble]:
        start = text.find(raw)
        if start >= 0:
            intervals.append((start, start + len(raw)))
    covered = bytearray(len(text))
    for start, end in intervals:
        for index in range(max(0, start), min(len(text), end)):
            covered[index] = 1
    offenders: list[tuple[int, str]] = []
    offset = 0
    for line_no, raw_line in enumerate(text.splitlines(keepends=True), 1):
        content = raw_line.rstrip("\r\n")
        start = offset
        offset += len(raw_line)
        if not content.strip() or content.lstrip().startswith("%"):
            continue
        if any(not covered[i] and not content[i - start].isspace()
               for i in range(start, start + len(content))):
            offenders.append((line_no + line_offset, content.strip()[:60]))
    return offenders


def _outside_fence_offenders(text: str, fences: list[_Fence]) -> list[tuple[int, str]]:
    """围栏之外的非空、非 ``%`` 注释行（未验证身份时不允许夹带叙述）。

    已验证身份的正文允许围栏外有说明文字（任务书示例就这么写）；但**未验证**
    的回件只接受纯 Bib，围栏外夹带叙述一律点名到行。
    """
    covered = bytearray(len(text))
    offset = 0
    starts: list[tuple[int, int, int]] = []
    for raw in text.splitlines(keepends=True):
        starts.append((offset, offset + len(raw.rstrip("\r\n")), len(raw)))
        offset += len(raw)
    for fence in fences:
        # 围栏块覆盖 [开围栏行首, 闭围栏行尾)；未闭合则到文末。
        first = starts[fence.start_line - 1][0]
        last_index = min(fence.end_line, len(starts)) - 1
        last = starts[last_index][0] + starts[last_index][2]
        for index in range(first, last):
            covered[index] = 1
    offenders: list[tuple[int, str]] = []
    for line_no, (begin, content_end, _width) in enumerate(starts, 1):
        content = text[begin:content_end]
        if not content.strip() or content.lstrip().startswith("%"):
            continue
        if any(not covered[i] for i in range(begin, content_end)):
            offenders.append((line_no, content.strip()[:60]))
    return offenders


def _plain_bib_problem(text: str, line_offset: int = 0) -> tuple[str, int | None]:
    """纯 Bib 判定：``(理由, 首个违规行的回件真实行号)``；通过时返回 ``("", None)``。

    ``line_offset`` 见 :func:`_plain_bib_offenders`：围栏内正文要按开围栏行号换算。
    """
    entries, problems = entries_by_key(text)
    if problems:
        return "回件里的 Bib 解析不完整：" + "；".join(problems), None
    if not entries:
        return "回件里没有解析出任何 Bib 条目（应为 @type{键, 字段…}）", None
    offenders = _plain_bib_offenders(text, line_offset)
    if offenders:
        shown = "；".join(f"第 {line} 行「{txt}」" for line, txt in offenders[:3])
        more = f"（另有 {len(offenders) - 3} 行）" if len(offenders) > 3 else ""
        return f"围栏/条目之外还有叙述文字：{shown}{more}", offenders[0][0]
    return "", None


def _is_plain_bib(text: str) -> bool:
    return not _plain_bib_problem(text)[0]


# ── 分支提示（C 包 3.3） ──────────────────────────────────────────────────


def _is_report_only_only(result: dict) -> bool:
    """typeset 是否全是报告式只读项（只记录、不改稿）。"""
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list) or not items:
        return False
    return all(isinstance(item, dict) and item.get("action") == "report_only"
               for item in items)


def _branch_hints(*, branch: str, t_keys: list, missing_t: list,
                  submitted: set, typeset_result: dict) -> tuple[str, str]:
    """返回 ``(branch_hint, next_step)``：说清这次属于哪种范围、下一步做什么。"""
    if branch == BRANCH_MISSING_ENTRIES:
        shown = "、".join(missing_t[:5]) + ("…" if len(missing_t) > 5 else "")
        hint = (f"范围：委派条目子集，但 T 内 {len(missing_t)} 条没交回"
                f"（{shown}）。缺件不会自动猜，也不会拿原文旧值顶替。")
        step = "可先 preview 看已交回部分，或让 AI 补齐缺件后整份重新导入。"
        return hint, step
    if branch == BRANCH_EXTERNAL_COMPLETE_BIB:
        return ("范围：外部完整 Bib（无 TASK-ID，整份替换语义）。",
                "导入时需 replace_active 确认才会替换当前来源，之后 preview。")
    if branch == BRANCH_UNVERIFIED_PLAIN_BIB:
        return (f"范围：未验证身份的纯 Bib（回件没有 TASK-ID 行，按委派子集语义，"
                f"已收 {len(submitted)} 条）。",
                "先人工确认这份 Bib 属于本任务，再 preview（只合并交回的键）。")
    if branch == BRANCH_DELEGATED_SUBSET:
        return (f"范围：委派条目子集（T 内 {len(t_keys)} 条已全部交回，"
                f"共 {len(submitted)} 条）。",
                "preview 生成拟应用候选（不写目标 bib），核对后 apply。")
    return "", ""


def _diagnostics(*, missing_t: list, extra_keys: list, rejected_items: list) -> list:
    """把“缺件 / 多余键 / 被隔离的建议”变成可定位的一行一条说明。"""
    notes: list[str] = []
    if missing_t:
        shown = "、".join(missing_t[:8]) + ("…" if len(missing_t) > 8 else "")
        notes.append(f"T 未交条目 {len(missing_t)} 个：{shown}（见 coverage.missing_t）")
    if extra_keys:
        shown = "、".join(extra_keys[:8]) + ("…" if len(extra_keys) > 8 else "")
        notes.append(f"回件多出任务书之外的条目 {len(extra_keys)} 个：{shown}"
                     "（不在 O/T/F/U/M 任一范围，不会被采纳）")
    for item in rejected_items:
        if isinstance(item, dict):
            category = item.get("category") or "?"
            item_id = item.get("item_id") or ""
            reason = item.get("reason") or ""
            where = f"{category} 项 {item_id}：" if item_id else f"{category} 项："
            notes.append(f"已隔离{where}{reason}")
        elif item:
            notes.append(f"已隔离：{item}")
    return notes


# ── 主入口 ────────────────────────────────────────────────────────────────


def parse_reply(*, text: str, context: dict,
                source_kind: str = "ai_subset") -> dict:
    """解析一份 R3 AI 回复，返回固定契约 dict（八键不变，另有追加键）。

    固定签名见执行卡行 61；``source_kind`` 原样回显（缺省 ai_subset，显式
    complete_external 仅允许无身份的纯 Bib）。
    """
    text = text or ""
    context = context if isinstance(context, dict) else {}
    sk = source_kind if isinstance(source_kind, str) and source_kind else "ai_subset"
    sets = context.get("sets") if isinstance(context.get("sets"), dict) else {}
    execution = (context.get("execution")
                 if isinstance(context.get("execution"), dict) else {})

    # 1) 身份：错 ID / 多冲突 ID 整份拒收，不给下游留下可重扫的原文。
    marks = _task_id_marks(text)
    identity_status, id_error, id_code = _identity(marks, context.get("task_id", ""))
    if id_error:
        return _rejected(identity_status=identity_status, source_kind=sk,
                         reason=id_error, code=id_code,
                         line=marks[0][1] if marks else None)

    if identity_status == "verified":
        sk = "ai_subset"

    # 2) 边界：只认围栏内的明确结果块。
    fences = _scan_fences(text)
    bib_fences = [f for f in fences if f.lang == "bibtex"]
    json_fences = [f for f in fences if f.lang == "json"]

    unclosed_bib = [f for f in bib_fences if not f.closed]
    if unclosed_bib:
        fence = unclosed_bib[0]
        return _rejected(
            identity_status=identity_status, source_kind=sk,
            code="bibtex_fence_unclosed", line=fence.start_line,
            reason=(f"bibtex 围栏未闭合：第 {fence.start_line} 行的 ```bibtex "
                    f"到回件末尾（第 {fence.end_line} 行）都没有闭合围栏"
                    "；整份拒收，请补上 ``` 后重新整份导入"))

    if len(bib_fences) > 1 and len({f.body for f in bib_fences}) > 1:
        # 允许逐字重复的围栏（同一结果抄两遍），但冲突块必须整份拒收。
        lines = "、".join(str(f.start_line) for f in bib_fences)
        return _rejected(
            identity_status=identity_status, source_kind=sk,
            code="conflicting_bibtex_blocks", line=bib_fences[0].start_line,
            reason=(f"多个冲突结果块：回件有 {len(bib_fences)} 个内容不同的 "
                    f"```bibtex 结果块（开围栏在第 {lines} 行），整份拒收"
                    "；一份回件只能有一份结果块，不能挑一份用"))

    # 3) 协议声明：出现 json 围栏或 slcn.ai_result 即声称新协议。
    #    此时 schema/JSON 必须完好，否则整份拒收，绝不降级为裸 Bib。
    claimed = bool(json_fences) or ("slcn.ai_result" in text)
    # 协议声明的位置：优先 JSON 围栏行，否则正文里第一次提到 slcn.ai_result 的行。
    claim_line: int | None = json_fences[0].start_line if json_fences else None
    if claim_line is None:
        index = text.find("slcn.ai_result")
        if index >= 0:
            claim_line = text.count("\n", 0, index) + 1
    json_obj = None
    if claimed:
        if len(json_fences) != 1:
            if not json_fences:
                # 只提了协议名却没给 JSON 区：报出提到它的那一行，而不是「找到 0 个围栏」。
                return _rejected(
                    identity_status=identity_status, source_kind=sk,
                    code="json_zone_not_unique", line=claim_line,
                    reason=(f"声称新协议但 schema JSON 区不唯一：第 {claim_line} 行提到 "
                            f"{SCHEMA}，但回件里没有任何 ```json 围栏；整份拒收"
                            "；一份回件必须恰好一个 JSON 区"))
            lines = "、".join(str(f.start_line) for f in json_fences)
            return _rejected(
                identity_status=identity_status, source_kind=sk,
                code="json_zone_not_unique",
                line=json_fences[0].start_line,
                reason=(f"声称新协议但 schema JSON 区不唯一：找到 {len(json_fences)} 个 "
                        f"```json 围栏（开围栏在第 {lines} 行），整份拒收"
                        "；一份回件只允许一个 JSON 区"))
        fence = json_fences[0]
        if not fence.closed:
            return _rejected(
                identity_status=identity_status, source_kind=sk,
                code="json_fence_unclosed", line=fence.start_line,
                reason=(f"声称新协议但 JSON 围栏未闭合：第 {fence.start_line} 行的 "
                        f"```json 没有闭合围栏（回件共 {fence.end_line} 行），整份拒收"
                        "；请补上 ``` 后重新整份导入"))
        try:
            json_obj = json.loads(fence.body)
        except Exception as exc:  # noqa: BLE001 —— 坏 JSON 要如实拒收，不能吞成裸 Bib
            return _rejected(
                identity_status=identity_status, source_kind=sk,
                code="json_parse_error", line=fence.start_line,
                reason=(f"声称新协议但 JSON 解析失败：第 {fence.start_line}–"
                        f"{fence.end_line} 行的 JSON 区无法解析（{exc}），整份拒收"))
        if not isinstance(json_obj, dict):
            return _rejected(
                identity_status=identity_status, source_kind=sk,
                code="json_not_object", line=fence.start_line,
                reason=(f"schema JSON 必须是对象：第 {fence.start_line} 行的 JSON 区"
                        f"解析成 {type(json_obj).__name__}，整份拒收"))
        if json_obj.get("schema") != SCHEMA:
            return _rejected(
                identity_status=identity_status, source_kind=sk,
                code="schema_unsupported", line=fence.start_line,
                reason=(f"schema 不支持：第 {fence.start_line} 行声明 "
                        f"{json_obj.get('schema')!r}，本引擎要求 {SCHEMA!r}；整份拒收"))

    # 4) bib_text = 围栏内原文整段（含前导 % 注释、@string/@comment/@preamble）。
    bib_text = bib_fences[0].body if bib_fences else ""
    if identity_status == "unverified":
        plain = bib_text if bib_fences else text
        if claimed:
            return _rejected(
                identity_status=identity_status, source_kind=sk,
                code="unverified_not_plain_bib", line=claim_line,
                reason=("无 TASK-ID 时只接受纯 Bib：回件声称新协议（含 ```json 围栏或 "
                        "slcn.ai_result），但没有任何 `% TASK-ID:` 身份行，整份拒收"))
        # 围栏内正文的行号是相对围栏体的；换算成回件真实行号要加开围栏行号
        # （``start_line`` 是 1 基，正文第 1 行 = 回件第 ``start_line + 1`` 行，
        #  故偏移取 ``start_line``）。无围栏时整份就是 Bib，偏移 0。
        fence_offset = bib_fences[0].start_line if bib_fences else 0
        problem, problem_line = _plain_bib_problem(plain, fence_offset)
        if problem:
            return _rejected(
                identity_status=identity_status, source_kind=sk,
                code="unverified_not_plain_bib", line=problem_line,
                reason=f"无 TASK-ID 时只接受纯 Bib：{problem}；整份拒收")
        if bib_fences:
            # 未验证身份时围栏外也不许夹带叙述（已验证身份允许，见 state_r3 用例）。
            outside = _outside_fence_offenders(text, fences)
            if outside:
                shown = "；".join(f"第 {line} 行「{txt}」" for line, txt in outside[:3])
                more = f"（另有 {len(outside) - 3} 行）" if len(outside) > 3 else ""
                return _rejected(
                    identity_status=identity_status, source_kind=sk,
                    code="unverified_not_plain_bib", line=outside[0][0],
                    reason=(f"无 TASK-ID 时只接受纯 Bib：围栏之外还有叙述文字："
                            f"{shown}{more}；整份拒收"))
        bib_text = plain
    submitted_keys: set[str] = set()
    if bib_text.strip():
        table, _problems = entries_by_key(bib_text)
        submitted_keys = {str(k) for k in table}

    # 5) 覆盖：只看 T；F/U 正常免交，不计漏交。
    t_keys = [str(k) for k in (sets.get("T") or [])]
    missing_t = [k for k in t_keys if k not in submitted_keys]
    cov_in = context.get("coverage")
    coverage = dict(cov_in) if isinstance(cov_in, dict) else {}
    coverage["missing_t"] = missing_t if sk == "ai_subset" else []
    # C 包 3.2「多余项」：交回了任务书 O 之外的键，点名但不采纳。
    declared_keys = {str(k) for k in (sets.get("O") or [])}
    extra_keys = ([k for k in sorted(submitted_keys) if k not in declared_keys]
                  if declared_keys else [])
    coverage["extra_keys"] = extra_keys

    # 6) 类别：按 execution 开关分离；逐项隔离非法建议。
    rejected_items: list = []
    typeset_result = _category_result("typeset", bool(execution.get("typeset", False)),
                                      json_obj, rejected_items)
    text_result = _category_result("text", bool(execution.get("text", False)),
                                   json_obj, rejected_items)
    if execution.get("text") and not context.get("body_units"):
        text_result = {"status": "not_run", "items": [], "reason": "未取得正文单元"}

    # 7) 分支：这份回件属于哪一类范围（C 包 3.3）。
    if identity_status == "unverified":
        branch = (BRANCH_EXTERNAL_COMPLETE_BIB if sk == "complete_external"
                  else BRANCH_UNVERIFIED_PLAIN_BIB)
    else:
        branch = BRANCH_MISSING_ENTRIES if missing_t else BRANCH_DELEGATED_SUBSET
    branches = [branch]
    if _is_report_only_only(typeset_result):
        branches.append(BRANCH_REPORT_ONLY)
    branch_hint, next_step = _branch_hints(
        branch=branch, t_keys=t_keys, missing_t=missing_t,
        submitted=submitted_keys, typeset_result=typeset_result)
    if BRANCH_REPORT_ONLY in branches:
        count = len(typeset_result.get("items") or [])
        branch_hint += (f"另含报告式只读项 {count} 条（report_only，只记录不改稿）。"
                        if branch_hint else
                        f"范围：报告式只读项 {count} 条（report_only，只记录不改稿）。")
        if not next_step:
            next_step = "preview 时这些项不产生改动，按报告逐条人工核对。"

    return {
        "ok": True,
        "identity_status": identity_status,
        "source_kind": sk,
        "bib_text": bib_text,
        "typeset": typeset_result,
        "text": text_result,
        "coverage": coverage,
        "rejected": rejected_items,
        # —— C 包追加键（只增不改） ——
        "errors": [],
        "diagnostics": _diagnostics(missing_t=missing_t, extra_keys=extra_keys,
                                    rejected_items=rejected_items),
        "branch": branch,
        "branches": branches,
        "branch_hint": branch_hint,
        "next_step": next_step,
    }


__all__ = [
    "parse_reply", "SCHEMA",
    "BRANCH_REJECTED", "BRANCH_DELEGATED_SUBSET", "BRANCH_MISSING_ENTRIES",
    "BRANCH_EXTERNAL_COMPLETE_BIB", "BRANCH_UNVERIFIED_PLAIN_BIB",
    "BRANCH_REPORT_ONLY",
]
