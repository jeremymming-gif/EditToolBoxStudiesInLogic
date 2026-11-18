"""R3 state transitions on the shared AI workflow state file."""
from __future__ import annotations

import json
import os
import types
from contextlib import contextmanager
from pathlib import Path

from . import ai_bib_candidate as bib
from . import ai_text_corrections as text_edits
from . import ai_workflow as workflow
from . import ai_workflow_reply as reply
from . import ai_workflow_taskbook as book
from . import bibcheck as bibcheck_mod
from . import bibdb as db
from . import bibdbapply as dbapply
from . import coop


def digest(value) -> str:
    return coop.sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def prepare(state, bib_text, tex_text, conversion, source_evidence, request_options):
    work_dir = state["work_dir"]
    source_evidence = dict(source_evidence or {})
    if (state["entry"] == workflow.ENTRY_BIB and tex_text and
            set(state["scope"]["exec"]) & {workflow.CAT_TEXT, workflow.CAT_TYPESET}):
        source_evidence.setdefault("body_units", [{
            "unit_id": "BODY-" + state["tex_sha256"][:12],
            "side": "关联 TeX", "text": tex_text,
        }])
    conversion = conversion or {}
    request_options = request_options or {}
    old, _ = bib.entries_by_key(bib_text)
    safe = state["safe_hit_keys"]
    required, _ = bib.closure([*state["cite_keys"], *safe], old)
    # 只有「判不出正文引用范围」才保守保全旧 Bib（无正文 / 正文里一个 cite 键都没有）。
    # `\nocite{*}` 不再触发全量：它只是「不必逐条核实」的信号，把未引用条目塞进必保
    # 集合 R 会让它们整条进委派集合 T，等于把作者额外 Bib 条目送给 AI。
    # 转换侧可能另外给出「正文实际引用键」（Word 纯文本 `[N]` 清单没有 cite 命令，
    # 由 `ai_workflow_convert._number_cited_keys` 反推），有它就算判得出范围。
    evidence_cited = {str(key) for key in (source_evidence.get("cited_keys") or []) if str(key)}
    # 判不出范围时**不再**把旧 Bib 全量塞进必保集合：任务书的委派集 T 由
    # `book.build_context` 独立算出（零引用 → T=[]），这里再补一遍只会让
    # `required` 虚高，而 `required - set(old)` 恒为空，改不动 R/T 与 marker 数。
    # 原 Bib 的保全靠 `allowed_keys`（下一行）与交付候选，不靠这个分支。
    no_scope = not evidence_cited and (
        not str(tex_text or "").strip() or not state["cite_keys"])
    state["allowed_keys"] = sorted(set(old) | required)
    state.update(schema_version=workflow.SCHEMA_VERSION_R3,
                 taskbook_version=3, reply_schema=reply.SCHEMA,
                 conversion=conversion, request_options=request_options)
    context = book.build_context(state=state, bib_text=bib_text, tex_text=tex_text,
                                 source_evidence=source_evidence,
                                 rule_sections=book.DEFAULT_RULE_SECTIONS)
    context["sets"]["R"] = list(dict.fromkeys([
        *context["sets"]["R"], *sorted(required - set(old))]))
    context["sets"]["M"] = [key for key in context["sets"]["R"] if key not in old]
    context["sets"]["A"] = state["allowed_keys"]
    refs = book._evidence_map(source_evidence, "refs_by_key", "original_refs", "raw_refs_by_key")
    cites = book._evidence_map(source_evidence, "cite_contexts", "cite_context_by_key")
    context["fillable_missing"] = [key for key in context["sets"]["M"]
                                    if any(str(value or "").strip()
                                           for value in (refs.get(key), cites.get(key)))]
    stable = {
        "protocol": [workflow.SCHEMA_VERSION_R3, reply.SCHEMA],
        "source": state["source"]["sha256"],
        "bib": state["bib_sha256"], "tex": state["tex_sha256"],
        "execute": state["scope"]["exec"], "safe_hit_keys": safe,
        "rules": [state["rule_version"], book.DEFAULT_RULE_SECTIONS],
        "evidence": source_evidence, "sets": context["sets"],
        "request_options": request_options,
        "conversion": conversion.get("input_identity", {}),
    }
    fingerprint = digest(stable)
    task_id = workflow._task_id(fingerprint, state["entry"], state["rule_version"])
    state.update(task_id=task_id, fingerprint=fingerprint, context=context,
                 items=context["items"], coverage=context["coverage"])
    context["task_id"] = task_id
    taskbook_text = book.render_taskbook(context=context)

    previous = workflow.load_state(work_dir) or {}
    if previous.get("pending_apply"):
        raise workflow.WorkflowError("应用中断，请先撤销本次写入。")
    if previous.get("task_id") == task_id:
        existing = workflow.read_taskbook(work_dir=work_dir, task_id=task_id)
        if not existing["ok"]:
            raise workflow.WorkflowError("已有同一任务的任务书已变动，无法恢复。")
        destinations_changed = any(previous.get(key) != state.get(key)
                                   for key in ("target", "tex", "text_target", "bib_out"))
        if destinations_changed:
            for key in ("target", "tex", "text_target", "bib_out"):
                previous[key] = state[key]
            previous.update(preview=None, last_preview_counts={},
                            phase=(workflow.PH_IMPORTED if previous.get("active_reply")
                                   else workflow.PH_PREPARED))
            workflow.save_state(work_dir, previous)
        return workflow.PrepareResult(existing["text"], existing["path"], task_id,
                                      previous, fingerprint, state["cite_keys"],
                                      state["bib_keys"], state["items"])
    folder = Path(workflow.state_dir(work_dir), task_id)
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = {}
    for kind, data in (("bib", bib_text), ("tex", tex_text)):
        path = str(folder / f"input.{kind}")
        workflow._write_text_atomic(path, data)
        snapshot[kind] = {"path": path, "sha256": coop.sha256_text(data)}
    taskbook_path = str(folder / "任务书.md")
    workflow._write_text_atomic(taskbook_path, taskbook_text)
    state["taskbook"] = {"path": taskbook_path, "sha256": coop.sha256_text(taskbook_text)}
    state["snapshot"] = snapshot
    # prepare 阶段绝不查库：只落一份「尚未查库」的投影，等回件合格后的 preview 再查。
    state.setdefault("library", library_default())
    state.setdefault("conflicts", [])
    state.setdefault("decisions", {})
    state["history"].append({"at": workflow._now(), "event": "prepare", "task_id": task_id})
    workflow.save_state(work_dir, state)
    return workflow.PrepareResult(taskbook_text, taskbook_path, task_id, state, fingerprint,
                                  state["cite_keys"], state["bib_keys"], state["items"])


def import_reply(state, body, task_id, source_kind, replace_active):
    if state.get("pending_apply"):
        raise workflow.WorkflowError("应用中断，请先撤销本次写入。")
    if source_kind not in ("ai_subset", "complete_external"):
        raise workflow.WorkflowError("回填来源无效。")
    expected = state["task_id"]
    if task_id and task_id != expected:
        raise workflow.WorkflowError("导入指定的 TASK-ID 与当前任务不符。")
    work_dir = state["work_dir"]
    result = reply.parse_reply(text=body, context=state["context"], source_kind=source_kind)
    actual_kind = result["source_kind"]
    sha = coop.sha256_text(body)
    active = state.get("active_reply") or {}
    if result["ok"] and active and active["sha256"] == sha and active["source_kind"] == actual_kind:
        return {**active, "notes": [], "task_id": expected, "chars": len(body), "idempotent": True}
    if result["ok"] and active and not replace_active and (
            actual_kind != active["source_kind"] or result["identity_status"] == "unverified"):
        return {"ok": False, "task_id": expected, "confirmation_required": True,
                "notes": ["替换当前活动回复的来源需要确认。"]}

    entry = {"at": workflow._now(), "sha256": sha, "source_kind": actual_kind,
             "identity_status": result["identity_status"], "ok": result["ok"]}
    entry["path"] = os.path.join(workflow.state_dir(work_dir), expected, f"reply-{sha}.md")
    if not any(all(old.get(k) == entry[k] for k in ("sha256", "source_kind", "ok"))
               for old in state["replies"]):
        workflow._write_text_atomic(entry["path"], body)
        state["replies"].append(entry)
        state["history"].append({"at": workflow._now(), "event": "import", **entry})
    if result["ok"]:
        state.update(active_reply={**result, **entry}, preview=None, phase=workflow.PH_IMPORTED,
                     last_preview_counts={}, source_kind=actual_kind, coverage=result["coverage"],
                     category_results=category_results(state, result),
                     # 新回件 → 旧裁决与旧查库快照一律作废（下次 preview 重新查库）。
                     library=library_default(), conflicts=[], decisions={},
                     library_query=None, preview_signature=None)
    else:
        # 回件未通过校验：明确记「跳过查库」，绝不用 conflicts=[] 冒充「已比对零差异」。
        state.update(library=library_default(), conflicts=[], decisions={},
                     library_query=None, preview_signature=None)
    workflow.save_state(work_dir, state)
    notes = [str(item.get("reason", "") if isinstance(item, dict) else item)
             for item in result["rejected"]]
    return {**result, "task_id": expected, "expected_task_id": expected,
            "foreign": result["identity_status"] == "mismatch", "chars": len(body),
            "notes": notes}


def category_results(state, result):
    bib_enabled = state["context"]["execution"]["bib_verify"]
    external = result["source_kind"] == "complete_external"
    missing = result["coverage"]["missing_t"]
    missing_material = state["context"]["sets"]["M"]
    if not bib_enabled or external:
        status = "not_run"
    elif result["identity_status"] == "unverified":
        status = "unverified"
    elif missing or missing_material:
        status = "partial"
    elif not state["context"]["sets"]["T"]:
        status = "not_run"
    else:
        status = "completed"
    return {"bib_verify": {"status": status, "missing_t": missing},
            "typeset": result["typeset"], "text": result["text"]}


def _entry_fragment(source, entry):
    start = min(entry.start, entry.block_start) if entry.block_start else entry.start
    return source[start:entry.end + 1]


def synthesize_bib(*, old_text, new_text, sets, fillable_missing, external, adopt,
                   preserve_all=False):
    old, old_bad = bib.entries_by_key(old_text)
    new, new_bad = bib.entries_by_key(new_text) if adopt else ({}, [])
    items = []
    blocked = list(dict.fromkeys(old_bad + new_bad))
    selected = {}
    required = set(sets["R"])
    frozen = set(sets["F"]) & set(old)
    allowed = set(sets["A"])
    delegated = set(sets["T"])
    usable = ((allowed if external else delegated | set(fillable_missing)) & allowed) - frozen
    unknown = sorted(set(new) - allowed)
    unauthorized = sorted(set(new) & (set(sets["M"]) - set(fillable_missing)))
    if not external:
        for key in unauthorized:
            items.append({"key": key, "status": bib.ST_ISOLATED, "reason": "缺少授权补条的原始材料"})
    for key in unknown:
        items.append({"key": key, "status": bib.ST_ISOLATED, "reason": "超出准备时授权的键"})
    for key in sorted(frozen):
        items.append({"key": key, "status": bib.ST_FROZEN, "reason": "安全命中与依赖保持冻结旧版"})
    if external and adopt:
        pool = dict(old)
        pool.update({key: new[key] for key in usable if key in new})
        roots = required | frozen | (usable & set(new))
        required, _ = bib.closure(roots, pool)
    for key in dict.fromkeys([*old, *new]):
        if adopt and key in usable and key in new:
            selected[key] = (new_text, new[key])
            items.append({"key": key, "status": bib.ST_APPLIED, "reason": "采纳该键的回填"})
        elif key in old:
            if (external and adopt and not preserve_all
                    and key not in required and key not in frozen):
                items.append({"key": key, "status": bib.ST_DROPPED, "reason": "完整外部 Bib 未包含此未引用键"})
                continue
            selected[key] = (old_text, old[key])
            if preserve_all and adopt and key not in frozen and not (
                    adopt and key in usable and key in new):
                # 新两段式初排任务：原 Bib 里已有、回件未带回的条目一律保全，
                # 不得因模型缺报而删除（用户裁定：PLAN §2 不限零引用场景）。
                items.append({"key": key, "status": bib.ST_BACKFILL,
                              "reason": "初排任务保全原条目，回件未带回"})
                continue
            if adopt and (key in delegated or (external and key in required)) and key not in frozen:
                items.append({"key": key, "status": bib.ST_BACKFILL, "reason": "保留旧版，未完成本次核查"})
    available = {key: entry for key, (_, entry) in selected.items()}
    deps, dep_bad = bib.closure(available, available)
    missing = sorted((required | deps) - set(available))
    blocked.extend(dep_bad)
    if missing:
        blocked.append("缺少必保文献或依赖：" + "、".join(missing))

    candidate = old_text
    if adopt and not blocked and (new or new_text.strip()):
        old_defs, old_comments, old_pre = book._structural_blocks(old_text)
        new_defs, new_comments, _ = book._structural_blocks(new_text)
        protected_raw = book._used_definitions(
            old_defs, [{"raw": _entry_fragment(old_text, old[key])} for key in sorted(frozen)])
        protected = {definition["name"].lower() for definition in old_defs
                     if definition["raw"] in protected_raw}
        defs = {}
        canonical = {}
        for definition in [*old_defs, *new_defs]:
            name = definition["name"].lower()
            raw = definition["raw"]
            value = bib.string_defs(raw).get(name)
            if not value or not name:
                blocked.append("无法解析 @string 定义。")
            elif name in canonical and canonical[name] != value:
                if name not in protected or definition in old_defs:
                    blocked.append(f"同名 @string 定义冲突：{name}")
            elif name not in canonical:
                canonical[name], defs[name] = value, raw

        fragments = []
        for source, entry in selected.values():
            fragments.append(_entry_fragment(source, entry).rstrip())
        body = "\n\n".join(fragments)
        # 保留来源注释（含被替换或从完整外部表省略条目上的旧注释）。
        included_lines = set(body.splitlines())
        source_comments = list(dict.fromkeys(
            line for source in (old_text, new_text) for line in source.splitlines()
            if line.lstrip().startswith("%") and line not in included_lines))
        used = set().union(*(book._string_references(entry.body) for entry in available.values()))
        dependencies, missing_defs = set(used), set()
        while used:
            name = used.pop()
            if name not in defs:
                missing_defs.add(name)
                continue
            rhs = defs[name].partition("=")[2][:-1]
            more = book._string_references("=" + rhs) - dependencies
            dependencies.update(more)
            used.update(more)
        if missing_defs:
            blocked.append("未定义的 @string：" + "、".join(sorted(missing_defs)))
        old_names = {definition["name"].lower() for definition in old_defs}
        if external:
            definitions = [raw for name, raw in defs.items() if name in dependencies]
            comments = list(dict.fromkeys([*old_comments, *new_comments, *source_comments]))
            candidate = "\n\n".join([*definitions, *comments, *old_pre, body]).strip("\n") + "\n"
        else:
            # Replace only delegated entry spans. Old declarations and provenance stay in place.
            additions = [raw for name, raw in defs.items()
                         if name in dependencies and name not in old_names]
            additions.extend(comment for comment in new_comments if comment not in old_comments)
            additions_for_entry = {}
            for key in old:
                if key not in usable or key not in new:
                    continue
                entry = new[key]
                comments = new_text[(entry.block_start or entry.start):entry.start]
                raw = new_text[entry.start:entry.end + 1]
                baseline = old[key]
                fragment = old_text[(baseline.block_start or baseline.start):baseline.start]
                source_lines = set(fragment.splitlines())
                ai_comments = [line for line in comments.splitlines()
                               if line.lstrip().startswith("%") and line not in source_lines]
                prefix = "\n".join(ai_comments)
                old_start, old_end = baseline.start, baseline.end + 1
                if prefix:
                    raw = prefix + "\n" + raw
                # Edits are applied in reverse input order below.
                additions_for_entry[key] = (old_start, old_end, raw)
            for start, end, raw in sorted(additions_for_entry.values(), reverse=True):
                candidate = candidate[:start] + raw + candidate[end:]
            missing_entries = [_entry_fragment(new_text, new[key]).rstrip()
                               for key in selected if key not in old]
            if missing_entries:
                candidate = candidate.rstrip("\n") + "\n\n" + "\n\n".join(missing_entries) + "\n"
            if additions:
                declarations = "\n\n".join(additions)
                candidate = declarations + "\n\n" + candidate
    counts = {"applied": sum(
        1 for key, (source, entry) in selected.items()
        if adopt and key in usable and key in new and (
            key not in old or source[entry.start:entry.end + 1]
            != old_text[old[key].start:old[key].end + 1])),
        "backfilled": sum(item["status"] == bib.ST_BACKFILL for item in items),
        "frozen": len(frozen), "isolated": sum(item["status"] == bib.ST_ISOLATED for item in items),
        "dropped": sum(item["status"] == bib.ST_DROPPED for item in items)}
    return {"ok": not blocked, "skipped": not adopt, "text": candidate,
            "items": items, "unknown": unknown, "missing": missing,
            "closure_closed": not blocked, "counts": counts,
            "new_sha256": coop.sha256_text(candidate), "summary": []}, blocked


def frozen_input(state, kind):
    snapshot = state["snapshot"][kind]
    try:
        data = Path(snapshot["path"]).read_text(encoding="utf-8")
    except OSError as exc:
        raise workflow.WorkflowError(f"冻结 {kind} 快照无法读取。") from exc
    if coop.sha256_text(data) != snapshot["sha256"]:
        raise workflow.WorkflowError(f"冻结 {kind} 快照已变动，请重新准备任务。")
    original = state["target" if kind == "bib" else "tex"]
    if original["path"]:
        current, error = workflow._read_text(original["path"])
        if (not current and error) or coop.sha256_text(current) != snapshot["sha256"]:
            raise workflow.WorkflowError(f"原 {kind} 已变动，请重新准备任务。")
    return data


def _text_result(tex, context, suggestions, adopt, excluded):
    units = {unit.get("unit_id"): unit for unit in context["body_units"]}
    eligible = []
    deferred = []
    for suggestion in suggestions:
        unit = units.get(suggestion.get("unit_id")) or {}
        unit_text = str(unit.get("text") or "")
        old = suggestion.get("old") or ""
        before = suggestion.get("before") or ""
        after = suggestion.get("after") or ""
        if (not adopt or suggestion.get("item_id") in excluded
                or suggestion.get("certainty") != "clear" or not old
                or before + old + after not in unit_text):
            deferred.append({**suggestion, "reason": "未采纳、尚不确定或未在冻结正文单元找到定位。"})
            continue
        converted = dict(suggestion)
        if converted.get("kind") == "missing_char":
            converted["kind"] = "missing"
        eligible.append(converted)
    result = text_edits.apply_items(tex, text_edits.parse_suggestions(eligible))
    result["reported"].extend(deferred)
    return result


# ── 第二段批量查库 + 字段级冲突裁决 ────────────────────────────────────────
#
# 纪律（合同 §1）：查库只发生在回件**已导入且合格**之后；prepare 阶段绝不查库；
# 一次 preview 对全部待核实键做**一次**批量查询（复用 `bibpipeline.classify_bibdb`
# 的既有入口，不新写库访问）；全流程对库**只读**（记库路径 + sha256，前后可比对）；
# 只有「同作品 + 同版本」才自动采纳库值，版本不同一律降级为冲突行或跳过；
# 冲突行默认 `original`，裁决三选一（library / reply / original）。

#: 互相依赖的事实字段同组：整组一致裁决（publisher+year+edition+address、serial 五字段）
_FIELD_GROUPS = {
    "publisher": "publication",
    "year": "publication",
    "edition": "publication",
    "address": "publication",
    "journal": "serial",
    "booktitle": "serial",
    "volume": "serial",
    "number": "serial",
    "pages": "serial",
}

#: 冲突行的三选一（顺序即界面展示顺序）
CHOICES = ("library", "reply", "original")

LIBRARY_STATES = ("ok", "unconfigured", "missing", "unreadable", "failed", "skipped")


def field_group(name) -> str:
    """字段所属裁决组：互相依赖的事实字段整组一致。"""
    key = str(name or "").strip().lower()
    return _FIELD_GROUPS.get(key, "field:" + key if key else "field")


def _library_state(checked, state, *, path="", sha256="", matched=0, conflicts=0,
                   skipped_reason=""):
    state = str(state or "")
    if state not in LIBRARY_STATES:
        state = "failed"
    return {
        "checked": bool(checked),
        "state": state,
        "path": str(path or ""),
        "sha256": str(sha256 or ""),
        "matched": int(matched),
        "conflicts": int(conflicts),
        "skipped_reason": str(skipped_reason or ""),
    }


def library_default():
    """还没查过库时的投影：**不是**「已比对零差异」。"""
    return _library_state(False, "unconfigured",
                          skipped_reason="尚未查库：查库只发生在回件通过校验之后的 preview。")


def library_of(state):
    library = state.get("library")
    if isinstance(library, dict) and "checked" in library:
        return _library_state(library.get("checked"), library.get("state") or "failed",
                              path=library.get("path"), sha256=library.get("sha256"),
                              matched=library.get("matched") or 0,
                              conflicts=library.get("conflicts") or 0,
                              skipped_reason=library.get("skipped_reason"))
    return library_default()


def conflicts_of(state):
    rows = state.get("conflicts")
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def decisions_of(state):
    saved = state.get("decisions")
    return {str(k): str(v) for k, v in saved.items()} if isinstance(saved, dict) else {}


def _entries(text):
    try:
        return list(bibcheck_mod.split_entries(text or ""))
    except Exception:  # 解析失败按「没有可查的条目」处理，绝不因此中断预览
        return []


def _field_map(text):
    """{key: (etype, {字段名: 值文本})}——值与 `bibdbapply` 回填用的是同一套解析。"""
    out = {}
    for etype, key, body in _entries(text):
        try:
            out[key] = (etype, dbapply._field_text_map(body))
        except Exception:
            continue
    return out


def _same_value(name, first, second) -> bool:
    """沿用 `bibdb` 既有等价判据（页码破折号、花括号、大小写、年份抽取都算等价）。"""
    if str(first or "").strip() == str(second or "").strip():
        return True
    if not str(first or "").strip() or not str(second or "").strip():
        return False
    try:
        return bool(db._fields_equivalent(name, first, second))
    except Exception:
        return False


def conflict_id(key, field, original, library, reply) -> str:
    """稳定 ID：同一（键 + 字段 + 三值）永远算出同一个 conflict_id。"""
    return "cf-" + digest([str(key), str(field), str(original or ""),
                           str(library or ""), str(reply or "")])[:16]


def library_query(path, entries, originals):
    """对全部待核实键做**一次**批量查询；返回 {key: 可序列化快照}。

    快照带 `tier`：
    - `safe` = 库内安全命中（可作候选值，但仍须过 `same_version`）；
    - `candidate` = 库内**相似但未达安全命中**的记录（典型是「同作品不同版本」：
      年份/版次不符）。它**绝不允许自动顶替**，只用于把差异降级成可解释的冲突行，
      让编辑自己决定要不要采纳；
    - `miss` = 库里没有这条。
    """
    from . import bibpipeline

    store = db.Store.load(path)
    safe, candidates, missed = bibpipeline.classify_bibdb(store, entries)
    snapshot = {}

    def _record(key, match, tier):
        record = match.get("record") or {}
        etype, body_fields = originals.get(key, ("", {}))
        try:
            fields = dbapply._render_library_candidates(
                etype or record.get("entry_type") or "book",
                db.canonical_fields(record.get("fields") or {}))
        except Exception:
            fields = []
        snapshot[key] = {
            "status": str(match.get("status") or ""),
            "tier": tier,
            "record_key": str(match.get("record_key") or ""),
            "hit": str(match.get("hit") or ""),
            "title_similarity": float(match.get("title_similarity") or 0.0),
            "weak": bool(dbapply.is_weak_identity(match)),
            "same_version": _same_version(body_fields, record.get("fields") or {}),
            "fields": [[str(name), str(value)] for name, value in fields],
        }

    for key, match in safe.items():
        _record(key, match, "safe")
    for key, match in candidates.items():
        if key in snapshot or not match.get("record"):
            continue
        # 只接受「同作品、只是版本不同」的强信号：题名近乎相同 + 第一作者姓相容。
        # 否则宁可不提，避免把不相干的相似题名塞进冲突行。
        try:
            similarity = float(match.get("title_similarity") or 0.0)
        except (TypeError, ValueError):
            similarity = 0.0
        if similarity < db.FUZZY_TITLE_THRESHOLD:
            continue
        _etype, body_fields = originals.get(key, ("", {}))
        record_fields = (match.get("record") or {}).get("fields") or {}
        surname_a = db.first_author_surname(
            (body_fields or {}).get("author") or (body_fields or {}).get("editor"))
        surname_b = db.first_author_surname(
            (record_fields or {}).get("author") or (record_fields or {}).get("editor"))
        if surname_a and surname_b and surname_a != surname_b:
            continue
        _record(key, match, "candidate")
    for key in missed:
        snapshot[key] = {"status": db.HIT_MISS, "tier": "miss", "record_key": "", "hit": "",
                         "title_similarity": 0.0, "weak": False,
                         "same_version": False, "fields": []}
    return snapshot


def _same_version(entry_fields, record_fields) -> bool:
    """同作品 + 同版本才可作候选值：沿用 `bibdb.works_match`（题名/作者姓/年份）。"""
    try:
        if not db.works_match(entry_fields or {}, record_fields or {}):
            return False
    except Exception:
        return False
    entry_year = db.entry_year(entry_fields or {})
    record_year = db.entry_year(record_fields or {})
    if entry_year and record_year and entry_year != record_year:
        return False
    entry_edition = str((entry_fields or {}).get("edition") or "").strip()
    record_edition = str((record_fields or {}).get("edition") or "").strip()
    if entry_edition and record_edition and not _same_value("edition", entry_edition,
                                                           record_edition):
        return False
    return True


def build_conflicts(*, old_bib, reply_bib, candidate_bib, snapshot):
    """逐字段比较「原条目 / 库条目 / 回件值」三值；只有实质差异才进 conflicts。

    返回 `(rows, auto_fills, notes)`：
    - `auto_fills` = 同版命中且候选缺值 → 直接采纳库值（不产生冲突行）；
    - 不同版本（第 2 版 vs 第 3 版、不同年份）**不得自动顶替**，只降级为冲突行；
    - 三值全等价、或候选已是库等价值 → 不产生任何行。
    """
    original = _field_map(old_bib)
    reply = _field_map(reply_bib)
    candidate = _field_map(candidate_bib)
    rows = []
    auto_fills = {}
    notes = []
    for key in sorted(snapshot):
        item = snapshot[key] or {}
        tier = str(item.get("tier") or "")
        if tier not in ("safe", "candidate"):
            continue
        if item.get("weak"):
            notes.append("`%s` 库内命中属弱身份（题名相似度 %s < %s），不采纳库值。"
                         % (key, item.get("title_similarity"), dbapply.WEAK_TITLE_SIM))
            continue
        fields = item.get("fields") or []
        if not fields:
            continue
        # 只有「safe 命中 + 同版」才允许自动补齐缺值；candidate 命中（典型是同作品
        # 不同版本）**一律不自动顶替**，只把差异降级成冲突行交给编辑裁决。
        same_version = bool(item.get("same_version")) and tier == "safe"
        if not same_version:
            notes.append("`%s` 与库内记录**版本不同**（年份/版次/出版社判据不满足，"
                         "命中层级 %s），库值不自动采纳，只列为冲突行待裁决。"
                         % (key, tier))
        _etype, orig_fields = original.get(key, ("", {}))
        _rtype, reply_fields = reply.get(key, ("", {}))
        _ctype, cand_fields = candidate.get(key, ("", {}))
        for pair in fields:
            try:
                name, value = str(pair[0]), str(pair[1])
            except (TypeError, IndexError):
                continue
            value = value.strip()
            if not name or not value:
                continue
            before = str(orig_fields.get(name) or "").strip()
            after = str(cand_fields.get(name) or "").strip()
            replied = str(reply_fields.get(name) or "").strip()
            if _same_value(name, after, value):
                continue          # 候选已是库等价值：不算冲突，也不产生改动
            if _same_value(name, before, value) and _same_value(name, replied, value):
                continue          # 三值全等价
            if not after and same_version:
                # 同版命中 + 候选缺值 → 直接采纳库值（既有回填口径，不进冲突）
                auto_fills.setdefault(key, {})[name] = value
                continue
            rows.append({
                "conflict_id": conflict_id(key, name, before, value, replied),
                "key": key,
                "field": name,
                "original": before,
                "library": value,
                "reply": replied,
                "choices": list(CHOICES),
                "default": "original",
                "group": field_group(name),
            })
    rows.sort(key=lambda row: (row["key"], row["field"]))
    return rows, auto_fills, notes


def check_decisions(rows, decisions):
    """校验裁决：未知 conflict_id / 非法值 / 组内混裁 → 可定位错误（含 ID）。

    组内一致是硬约束（ACCEPTANCE:20 第 7 条）：`publisher+year+edition` 互相依赖，
    库的年份配回件的出版社就是书目事实自相矛盾。同一 group 的所有冲突行必须
    取同一个值，否则拒绝并点名该组每一条的 conflict_id 与取值 —— 不静默传播
    （用户会以为自己只改了一行却连带改了别的字段）。
    """
    known = {row["conflict_id"]: row for row in rows}
    out = {}
    for raw_id, raw_value in (decisions or {}).items():
        cid = str(raw_id or "").strip()
        if not cid:
            continue
        if cid not in known:
            raise workflow.WorkflowError(
                "不认识的 conflict_id：%s（本次预览的冲突行里没有这一项；"
                "对非冲突项裁决一律拒绝）" % cid)
        value = str(raw_value or "").strip().lower()
        if value not in CHOICES:
            raise workflow.WorkflowError(
                "conflict_id %s 的裁决值不合法：%s（只能是 %s 之一）"
                % (cid, raw_value, " / ".join(CHOICES)))
        out[cid] = value
    # 组内一致：校验**生效后**的整组，而不是只看用户显式传的那些。
    # 只看显式项会漏掉「只改 year、publisher 走缺省 original」这种静默混裁
    # （用户没显式选过，但落地结果依然是库的年份配回件的出版社）。
    # 作用域必须带条目键：两个不同条目的 year 不是同一组，各自选不同来源是合法的。
    settled = effective_decisions(rows, out)
    by_scope = {}
    for row in rows:
        cid = row["conflict_id"]
        scope = (str(row.get("key") or ""), str(row.get("group") or "field"))
        by_scope.setdefault(scope, []).append(
            (cid, settled.get(cid, "original"), str(row.get("field") or "")))
    for (key, group), items in sorted(by_scope.items()):
        values = {value for _cid, value, _field in items}
        if len(values) > 1:
            detail = "、".join(
                "%s=%s（%s）" % (cid, value, field)
                for cid, value, field in sorted(items, key=lambda i: (i[2], i[0])))
            raise workflow.WorkflowError(
                "同一组字段必须整组一致，不能混裁：条目 %s 的 %s 组 —— %s；"
                "请把这一组统一成同一个值（library / reply / original）。"
                % (key or "（无键）", group, detail))
    return out


def effective_decisions(rows, decisions):
    """未裁决项按 default=original 补齐，供 preview_id 与落地共用同一份裁决。"""
    decisions = decisions or {}
    return {row["conflict_id"]: decisions.get(row["conflict_id"], row.get("default") or "original")
            for row in rows}


def _pad_entry_tails(bib_text, keys):
    """在指定条目的右花括号前补一个换行（不改字段、不改键）。

    `bibdbapply._render_entry` 在「字段体末尾紧贴 `}`、且没有尾逗号」时会把补逗号
    与插入新字段都算在同一个偏移上，产出 `...,\\n field = {v},\\n,}` 这种非法结构
    （花括号配平但多一个裸逗号）。补一个换行把两处编辑错开，输出即正确。
    该模块属只读复用清单，故在此侧规避。
    """
    spans = [span for span in dbapply.entry_spans(bib_text) if span[1] in keys]
    if not spans:
        return bib_text
    out = bib_text
    for _etype, _key, _start, body_start, close in sorted(spans, key=lambda s: -s[4]):
        body = out[body_start:close]
        if body and not body.endswith(("\n", "\r", " ", "\t")):
            out = out[:close] + "\n" + out[close:]
    return out


def apply_decisions(bib_text, rows, decisions, auto_fills=None):
    """把选定值合成进候选 Bib；未裁决项按 default=original 保留原条目。

    红线「未裁决项按 original 回退」是**字节级**要求：若本次没有任何值真正落地
    （全部裁决为 original/reply 且与现值等价），必须原样返回 `bib_text`。
    否则 `_pad_entry_tails` 会给「字段体紧贴 `}`」的条目补一个换行，产出语义等价
    但字节不同的候选，编辑器里「采纳前后」对比就会出现无意义的差异。

    调用方须先过 `check_decisions(rows, decisions)`：这里只负责落地，不做合法性校验
    （未知 conflict_id、非法取值、组内混裁都归 `check_decisions` 判）。`decisions=None`
    按「全部走缺省 original」处理，不会抛异常。
    """
    decisions = decisions or {}
    chosen = effective_decisions(rows, decisions)
    entries = _field_map(bib_text)
    plans = []
    notes = []
    by_key = {}
    for key, fields in sorted((auto_fills or {}).items()):
        etype, present = entries.get(key, ("", {}))
        if not etype:
            notes.append("`%s` 不在候选 Bib 里，同版库值未补齐。" % key)
            continue
        plan = {"etype": etype, "key": key, "fills": {}, "replaces": {}}
        for name, value in sorted(fields.items()):
            plan["replaces" if name in present else "fills"][name] = value
        by_key[key] = plan
        plans.append(plan)
    for row in rows:
        choice = chosen.get(row["conflict_id"], "original")
        value = str(row.get(choice) or "").strip()
        etype, fields = entries.get(row["key"], ("", {}))
        if not etype:
            notes.append("`%s` 不在候选 Bib 里，裁决未落地。" % row["key"])
            continue
        plan = by_key.get(row["key"])
        if plan is None:
            plan = {"etype": etype, "key": row["key"], "fills": {}, "replaces": {}}
            by_key[row["key"]] = plan
            plans.append(plan)
        if value:
            bucket = "replaces" if row["field"] in fields else "fills"
            plan[bucket][row["field"]] = value
        elif choice == "original" and row["field"] in fields:
            notes.append("`%s` 的 `%s` 原条目为空，保留空值需手工删除候选里的该字段。"
                         % (row["key"], row["field"]))
    if not plans:
        return bib_text, [], notes
    try:
        prepared = _pad_entry_tails(bib_text, {plan["key"] for plan in plans})
        new_text, applied = dbapply.apply_text(prepared, plans)
    except Exception as exc:  # 结构异常 fail-soft：候选保持原样，改动记账
        notes.append("裁决落地失败（%s），候选 Bib 保持未裁决状态。" % exc)
        return bib_text, [], notes
    if not applied:
        # 没有任何值真正落地 → 字节级回退到原条目（padding 也不许留下痕迹）。
        return bib_text, [], notes
    return new_text, applied, notes


def _library_probe(state, *, old_bib, reply_bib, candidate_bib, decisions, requery):
    """第二段查库总入口 → (library 投影, conflicts, 候选 Bib 文本, 说明)。"""
    active = state.get("active_reply") or {}
    if not active:
        return (_library_state(False, "skipped",
                              skipped_reason="回件未通过校验：没有通过校验的活动回件，本次不查库。"),
                [], candidate_bib, [])
    if not active.get("ok"):
        return (_library_state(False, "skipped",
                              skipped_reason="回件未通过校验：回件被整份拒收，本次不查库。"),
                [], candidate_bib, [])
    identity = str(active.get("identity_status") or "")
    if identity == "mismatch":
        return (_library_state(False, "skipped",
                              skipped_reason="回件未通过校验：TASK-ID 不符，本次不查库。"),
                [], candidate_bib, [])

    originals = _field_map(old_bib)
    entries = _entries(old_bib)
    if not entries:
        return (_library_state(False, "skipped",
                              skipped_reason="原 Bib 没有可核实的条目，本次不查库。"),
                [], candidate_bib, [])

    saved = state.get("library_query") if isinstance(state.get("library_query"), dict) else {}
    path = str(saved.get("path") or "")
    snapshot = None
    notes = []
    if saved and not requery:
        current = db.db_sha256(path) if path else ""
        if current and saved.get("sha256") and current != saved.get("sha256"):
            return (_library_state(
                False, "failed", path=path, sha256=current,
                skipped_reason="引用库在预览后已变动（sha256 不一致），需 --requery 重新查库。"),
                [], candidate_bib,
                ["引用库文件已变动，本次不采纳库值：请加 --requery 重新查库。"])
        snapshot = saved.get("matches") if isinstance(saved.get("matches"), dict) else None

    if snapshot is None:
        try:
            path = db.default_db_path()
        except db.BibDBError as exc:
            return (_library_state(False, "unconfigured",
                                   skipped_reason="未配置引用库：%s" % exc),
                    [], candidate_bib, [])
        if not path or not os.path.isfile(path):
            return (_library_state(False, "missing", path=path,
                                   skipped_reason="引用库文件不存在：%s（本次不查库）" % path),
                    [], candidate_bib, [])
        try:
            snapshot = library_query(path, entries, originals)
        except db.BibDBError as exc:
            return (_library_state(False, "unreadable", path=path, sha256=db.db_sha256(path),
                                   skipped_reason="引用库读取失败：%s" % exc),
                    [], candidate_bib, [])
        except Exception as exc:
            return (_library_state(False, "failed", path=path, sha256=db.db_sha256(path),
                                   skipped_reason="引用库查询失败：%s" % exc),
                    [], candidate_bib, [])
        state["library_query"] = {"path": path, "sha256": db.db_sha256(path),
                                  "at": workflow._now(), "matches": snapshot}

    rows, auto_fills, more = build_conflicts(old_bib=old_bib, reply_bib=reply_bib,
                                             candidate_bib=candidate_bib, snapshot=snapshot)
    notes.extend(more)
    checked = check_decisions(rows, decisions)
    merged_text, applied, tail = apply_decisions(candidate_bib, rows, checked, auto_fills)
    notes.extend(tail)
    del applied
    # `matched` = 库内命中数（safe + candidate 都算「库里有这条」）；
    # `conflicts` 只数真正需要编辑裁决的行。
    matched = sum(1 for item in snapshot.values()
                  if str((item or {}).get("tier") or "") in ("safe", "candidate"))
    library = _library_state(True, "ok", path=path, sha256=db.db_sha256(path),
                             matched=matched, conflicts=len(rows))
    if not rows:
        library["skipped_reason"] = ""
    return library, rows, merged_text, notes


def preview(state, adopt, skip_items=(), decisions=None, requery=False):
    if state.get("pending_apply"):
        raise workflow.WorkflowError("应用中断，请先撤销本次写入。")
    active = state.get("active_reply") or {}
    context = state["context"]
    chosen = set(adopt if adopt is not None else state["scope"]["adopt"])
    chosen.intersection_update(state["scope"]["exec"])
    blocked = []
    notes = []
    work_dir = state["work_dir"]
    try:
        old_bib = frozen_input(state, "bib")
        tex = frozen_input(state, "tex")
    except workflow.WorkflowError as exc:
        old_bib, tex = "", ""
        blocked.append(str(exc))
    waiting_for = set(chosen)
    if not context["sets"]["T"] and not context["sets"]["M"]:
        waiting_for.discard(workflow.CAT_BIB_VERIFY)
    if not active and waiting_for:
        blocked.append("尚无通过校验的活动回复。可导入回复或明确取消全部采纳。")
    external = active.get("source_kind") == "complete_external"
    adopt_bib = workflow.CAT_BIB_VERIFY in chosen and bool(active)
    adopt_typeset = workflow.CAT_TYPESET in chosen and not external
    adopt_text = (workflow.CAT_TEXT in chosen and not external
                  and bool(state.get("conversion") or state["text_target"]["path"]))
    excluded = set(skip_items)
    final_tex = tex
    # 判据是「是不是新两段式初排任务」，不是「R 是否为空」：初排任务的原 Bib 与
    # 交付候选必须保全全部未委派条目及依赖（PLAN §2），旧任务/独立引用库入口
    # 仍按原身份语义走 `ST_DROPPED`。
    preserve_all = (state.get("entry") == workflow.ENTRY_CONVERT
                    and bool(state.get("conversion")))
    bib_info, bib_errors = synthesize_bib(
        old_text=old_bib, new_text=active.get("bib_text", ""), sets=context["sets"],
        fillable_missing=context["fillable_missing"], external=external, adopt=adopt_bib,
        preserve_all=preserve_all)
    blocked.extend(bib_errors)
    # 第二段查库：只在回件已导入且合格之后（不合格时 library.checked=False + state=skipped）。
    library, conflicts, decided_bib, library_notes = _library_probe(
        state, old_bib=old_bib, reply_bib=active.get("bib_text", ""),
        candidate_bib=bib_info["text"], decisions=decisions, requery=requery)
    notes.extend(library_notes)
    if decided_bib != bib_info["text"]:
        bib_info = dict(bib_info, text=decided_bib,
                        new_sha256=coop.sha256_text(decided_bib))

    text_info = _text_result(tex, context, (active.get("text") or {}).get("items", []),
                             adopt_text, excluded)
    final_tex = text_info["text"]
    typeset_info = {"applied": [], "rejected": []}
    all_typeset = (active.get("typeset") or {}).get("items", [])
    typeset_items = [item for item in all_typeset if item.get("item_id") not in excluded]
    if typeset_items and adopt_typeset and state.get("conversion") and not blocked:
        from .ai_workflow_convert import preview_typeset

        typeset_info = preview_typeset(state=state, tex_text=tex, bib_text=bib_info["text"],
                                      items=typeset_items, text_result=text_info)
        text_info = typeset_info.pop("text_result", None) or text_info
        final_tex = typeset_info["text"]
    elif typeset_items:
        typeset_info["rejected"] = [{**item, "reason": "未采纳或未取得可应用的初排产物。"}
                                    for item in typeset_items]
    cited, _, _ = bib.observed_cited(final_tex)
    have, _ = bib.entries_by_key(bib_info["text"])
    if cited - set(have):
        missing = "、".join(sorted(cited - set(have)))
        blocked.append("最终正文引用缺少对应 Bib 条目：" + missing)

    ok = not blocked
    bib_sha = bib_info["new_sha256"]
    tex_sha = coop.sha256_text(final_tex)
    settled = check_decisions(conflicts, decisions)
    # 幂等：同输入 + 同裁决 → 同 preview_id（signature 不变则不自增 revision）；
    # 裁决一改，signature 变 → revision+1 → 新 preview_id，旧 ID 的 apply/deliver 自然被拒。
    signature = digest([
        state["task_id"], active.get("sha256"), active.get("source_kind"),
        sorted(chosen), sorted(excluded), bib_sha, tex_sha,
        effective_decisions(conflicts, settled),
    ])
    if state.get("preview_signature") != signature:
        state["preview_revision"] = state.get("preview_revision", 0) + 1
    state["preview_signature"] = signature
    preview_id = "pv-" + digest([
        state["task_id"], state.get("preview_revision", 0), active.get("sha256"),
        active.get("source_kind"), sorted(chosen), sorted(excluded), bib_sha, tex_sha,
        effective_decisions(conflicts, settled),
    ])[:16]
    paths = {}
    folder = Path(workflow.state_dir(work_dir), state["task_id"])
    if ok:
        for kind, contents in (("bib", bib_info["text"]), ("tex", final_tex)):
            if contents:
                path = str(folder / f"{preview_id}.{kind}")
                workflow._write_text_atomic(path, contents)
                paths[kind] = path

    text_info.update(ok=bool(text_info["applied"]), skipped=not adopt_text,
                     text=final_tex, new_sha256=tex_sha, typeset=typeset_info)
    # Counts represent selectable effective changes, not reports or unchanged acknowledgements.
    state["last_preview_counts"] = {
        workflow.CAT_BIB_VERIFY: dict(bib_info["counts"]),
        workflow.CAT_TYPESET: {"unit": "修改处", "applied": len(typeset_info["applied"]),
                               "reported": len(typeset_info["rejected"])},
        workflow.CAT_TEXT: {"unit": "修改处", "applied": len(text_info["applied"]),
                            "reported": len(text_info["reported"]),
                            "rejected": len(text_info["rejected"])},
    }
    state["phase"] = workflow.PH_PREVIEWED if ok else workflow.PH_BLOCKED
    state["library"] = library
    state["conflicts"] = conflicts
    state["decisions"] = settled
    state["preview"] = {
        "at": workflow._now(), "preview_id": preview_id, "ok": ok,
        "reply_sha256": active.get("sha256", ""), "adopt": sorted(chosen),
        "skip_items": sorted(excluded),
        "target_sha256": state["bib_sha256"], "bib_new_sha256": bib_sha,
        "text_new_sha256": tex_sha, "blocked": blocked,
        "candidate_bib": paths.get("bib", ""), "candidate_text": paths.get("tex", ""),
        "write_bib": adopt_bib and (bool(state.get("bib_out")) or bib_info["text"] != old_bib),
        "write_text": adopt_text and final_tex != tex,
        "bib_items": bib_info["items"], "typeset": typeset_info,
        "text_items": {key: text_info[key] for key in ("applied", "reported", "rejected")},
    }
    state["history"].append({"at": workflow._now(), "event": "preview",
                             "preview_id": preview_id, "ok": ok})
    workflow.save_state(work_dir, state)
    return workflow.Preview(ok, state["phase"], preview_id, state["entry"],
                            bib_info, text_info, blocked, notes, state)


def request(*, work_dir, taskbook_text, client, timeout, reply_path, retry):
    from . import llm

    with request_lock(work_dir) as acquired:
        if not acquired:
            return _request_failure(workflow.PH_REQUESTING, "已有请求正在执行。")
        state = workflow.load_state(work_dir) or {}
        phase = state.get("phase", "")
        previous = state.get("request_status") or ""
        if state.get("schema_version") != workflow.SCHEMA_VERSION_R3:
            return _request_failure(phase, "当前任务已变动。")
        if state.get("pending_apply"):
            return _request_failure(phase, "应用中断，请先撤销本次写入。")
        if previous == "succeeded":
            return _request_failure(phase, "已有成功请求，无法重复发送。")
        if previous and not retry:
            return _request_failure(phase, "上一次请求未成功，须显式重试。")
        registered = workflow.read_taskbook(work_dir=work_dir, task_id=state["task_id"])
        if not registered["ok"] or registered["text"] != taskbook_text:
            return _request_failure(phase, "任务书身份或内容与持久状态不符。")

        options = state["request_options"]
        context = state["context"]
        execution, evidence = context["execution"], context["source_evidence"]
        has_body = ((execution["typeset"] or execution["text"]) and context["body_units"]
                    or execution["bib_verify"] and (
                        evidence.get("cite_contexts") or evidence.get("generated_cite_contexts")
                        or evidence.get("cite_context_by_key")))
        if has_body and not options.get("allow_body"):
            return _request_failure(phase, "任务书含正文材料，当前策略未授权通过 API 发送。")
        config = llm.config_from_env() if client is None else None
        if config is not None and not config.ready:
            return _request_failure(phase, "直连 API 需要 base/model。")
        search = (config.uses_web_search if config is not None else
                  getattr(client, "uses_web_search", False))
        if options.get("require_search") and not search:
            return _request_failure(phase, "本任务要求联网检索，当前 API 未启用该能力。")

        system = "请按编辑任务书返回数据。没有实际查证的外部信息应标为部分执行或未执行。"
        max_output = options.get("max_output_tokens") or llm.request_params_from_env()[1]
        estimate = coop.estimate_api_budget(system, [taskbook_text], max_output)
        budget_error = coop.budget_exceeded(
            estimate, options.get("article_budget", 30000), options.get("batch_budget", 1000000))
        if budget_error:
            return _request_failure(phase, budget_error)
        verify_max = options.get("verify_max", 0)
        delegated = len(context["sets"]["T"]) + len(context["fillable_missing"])
        if execution["bib_verify"] and verify_max and delegated > verify_max:
            return _request_failure(phase, "待核文献数超过本次 API 核实范围。")
        context_tokens = options.get("context_tokens") or getattr(client, "context_tokens", 0)
        output_tokens = getattr(client, "max_output_tokens", 0)
        if context_tokens and estimate["total_tokens"] > context_tokens:
            return _request_failure(phase, "预估请求超过所选模型上下文容量。")
        if output_tokens and max_output and max_output > output_tokens:
            return _request_failure(phase, "本次配置的输出额度超过所选模型容量。")
        capacity_note = ("按已知上下文和输出上限预检" if context_tokens and max_output
                         else "容量未验证")

        from uuid import uuid4

        req_id = "req-" + uuid4().hex
        state.update(request_id=req_id, request_status="in_flight", phase=workflow.PH_REQUESTING,
                     request_capacity=capacity_note)
        workflow.save_state(work_dir, state)

        text, error = "", ""
        try:
            if config is None:
                out = client.complete(system=system, user=taskbook_text, timeout=timeout)
            else:
                out = llm.complete(config, system, taskbook_text, timeout=timeout,
                                   max_tokens=max_output)
            text = str(out or "")
            if not text.strip():
                error = "API 未返回有效内容。"
        except Exception as exc:
            error = f"API 请求失败（{type(exc).__name__}）。"

        state = workflow.load_state(work_dir) or {}
        if state.get("request_id") != req_id:
            return _request_failure(state.get("phase", ""), "任务身份在请求期间已变动。")
        imported = {}
        path = ""
        if not error:
            imported = import_reply(state, text, state["task_id"], "ai_subset", False)
            if not imported.get("ok"):
                error = "；".join(imported.get("notes") or ["回填被拒收。"])
            else:
                path = (state.get("active_reply") or {}).get("path", "")
        if not error and reply_path:
            try:
                with open(reply_path, "x", encoding="utf-8", newline="") as f:
                    f.write(text)
                path = reply_path
            except OSError:
                # The accepted work-area copy remains the authoritative reply.
                imported["notes"] = [*(imported.get("notes") or []),
                                     "另存 API 回复失败，活动副本仍在工作区。"]
        status = "unknown" if error else "succeeded"
        state.update(request_status=status, last_error=error,
                     phase=(workflow.PH_IMPORTED if state.get("active_reply") else workflow.PH_BLOCKED))
        state["history"].append({"at": workflow._now(), "event": "request",
                                 "request_id": req_id, "ok": not error, "retry": retry})
        workflow.save_state(work_dir, state)
        return {"ok": not error, "phase": state["phase"], "reply_text": text,
                "reply_path": path, "chars": len(text), "error": error,
                "request_id": req_id, "request_status": status, "capacity": capacity_note,
                "imported": imported}


def _request_failure(phase, error):
    return {"ok": False, "phase": phase, "reply_text": "", "reply_path": "",
            "chars": 0, "error": error}


@contextmanager
def request_lock(work_dir):
    """Hold a cross-process advisory lock until the response has been persisted."""
    folder = Path(workflow.state_dir(work_dir))
    folder.mkdir(exist_ok=True, parents=True)
    with open(folder / "request.lock", "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_bytes(path, contents, *, create=False):
    """Stage a sibling then install it atomically, including a no-overwrite create."""
    from stat import S_IMODE
    from tempfile import NamedTemporaryFile

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, prefix=".aiw-", delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            temp.write(contents)
        except BaseException:
            temp.close()
            temp_path.unlink(missing_ok=True)
            raise
    try:
        if create:
            os.link(temp_path, target)
        else:
            if target.exists():
                temp_path.chmod(S_IMODE(target.stat().st_mode))
            os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def _current_file(path):
    path = Path(path)
    if path.is_symlink():
        raise workflow.WorkflowError("写入目标为符号链接，已阻断。")
    if not path.exists():
        return ""
    return coop.sha256_file(path)


def write_bib_outputs(state, plans, preview_id):
    """Apply all verified outputs as a recoverable transaction."""
    work_dir = state["work_dir"]
    folder = Path(workflow.undo_dir(work_dir), preview_id)
    writes = []
    for plan in plans:
        target = plan["target"]
        before = _current_file(target)
        if bool(before) != bool(plan["before_sha256"]):
            raise workflow.WorkflowError("目标状态在应用前已变动。")
        backup = ""
        if before:
            data, _ = workflow._read_text(target)
            if coop.sha256_text(data) != plan["before_sha256"]:
                raise workflow.WorkflowError("目标内容在应用前已变动。")
            backup = str(folder / (plan["kind"] + ".before"))
            _write_bytes(backup, Path(target).read_bytes())
            if coop.sha256_file(backup) != before:
                raise workflow.WorkflowError("目标内容在备份期间已变动。")
        writes.append({"kind": plan["kind"], "target": target, "backup": backup,
                       "undo_copy": backup, "created": not before,
                       "before_sha256": before, "after_sha256": coop.sha256_text(plan["text"])})

    first = writes[0]
    txn = {"kind": "multi" if len(writes) > 1 else first["kind"], "writes": writes,
           "target": first["target"], "backup": first["backup"],
           "before_sha256": first["before_sha256"], "after_sha256": first["after_sha256"],
           "at": workflow._now(), "preview_id": preview_id}
    state["pending_apply"] = txn
    state["phase"] = workflow.PH_APPLYING
    workflow.save_state(work_dir, state)
    try:
        for plan, write in zip(plans, writes, strict=True):
            target = plan["target"]
            if _current_file(target) != write["before_sha256"]:
                raise workflow.WorkflowError("目标内容在应用期间已变动。")
            _write_bytes(target, plan["text"].encode("utf-8"), create=write["created"])
            if _current_file(target) != write["after_sha256"]:
                raise workflow.WorkflowError("写后回读指纹不符。")
    except (OSError, workflow.WorkflowError) as exc:
        try:
            restore_bib_outputs(state, txn, recovering=True)
        except (OSError, workflow.WorkflowError) as recovery_error:
            raise workflow.WorkflowError("应用中断，须核对后撤销。") from recovery_error
        raise workflow.WorkflowError(f"应用中断，已回滚写入：{exc}") from exc

    state.pop("pending_apply")
    state.update(undo=txn, phase=workflow.PH_APPLIED)
    state["applies"].append(txn)
    state["history"].append({"at": workflow._now(), "event": "apply",
                             "preview_id": preview_id, "wrote": len(writes)})
    workflow.save_state(work_dir, state)
    return {"ok": True, "error": "", "wrote": len(writes), "writes": writes, "undo": txn,
            "target": first["target"], "backup": first["backup"],
            "before_sha256": first["before_sha256"], "after_sha256": first["after_sha256"]}


def restore_bib_outputs(state, txn, *, recovering=False):
    restorations = []
    for write in txn["writes"]:
        current = _current_file(write["target"])
        if recovering and current == write["before_sha256"]:
            continue
        if current != write["after_sha256"]:
            raise workflow.WorkflowError("应用后的目标文件已变动，无法自动撤销。")
        backup = None
        if not write["created"]:
            backup = Path(write["backup"]).read_bytes()
            if coop.sha256_file(write["backup"]) != write["before_sha256"]:
                raise workflow.WorkflowError("撤销备份已变动。")
        restorations.append((write, backup))
    restored = []
    for write, backup in reversed(restorations):
        target = write["target"]
        if _current_file(target) != write["after_sha256"]:
            raise workflow.WorkflowError("目标文件在撤销期间已变动。")
        if backup is None:
            Path(target).unlink()
        else:
            _write_bytes(target, backup)
        restored.append({"kind": write["kind"], "target": target,
                         "restored_from": write["backup"], "sha256": write["before_sha256"],
                         "removed": backup is None})
    if recovering:
        state.pop("pending_apply", None)
    else:
        state["undo"] = None
    state["phase"] = workflow.PH_PREVIEWED
    state["history"].append({"at": workflow._now(), "event": "undo", "wrote": len(restored)})
    workflow.save_state(state["work_dir"], state)
    first = restored[0] if restored else {"target": "", "restored_from": "", "sha256": ""}
    return {"ok": True, **first, "restored": restored}
