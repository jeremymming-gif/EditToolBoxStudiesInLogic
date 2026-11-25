"""R3 compile repair lifecycle. The parent workflow owns the publication journal."""
from contextlib import contextmanager
from dataclasses import asdict
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from . import compiler, model
from .files import (REPLY_LIMIT, RepairError, atomic_write, decode_json, digest, directory,
                    json_bytes, put_json, read, stem_name)
from .state import DEFAULT_ROUNDS, MAX_ROUNDS, RETENTION_SECONDS, capture_compile
from .transaction import _guard_path


UNKNOWN = "请求已发出但未收到可用回件（结果不明）"
ACTIONS = ("prepare", "send", "import", "review", "apply", "status", "delete", "retained")
API_KEY_ENV = "SLCN_COMPILE_FIX_API_KEY"


def _identity(state, preview):
    return {"task_id": state["task_id"], "preview_id": preview["preview_id"],
            "candidate_sha256": preview["text_new_sha256"],
            "reply_sha256": preview.get("reply_sha256", ""),
            "bib_sha256": preview["bib_new_sha256"], "state_sha256": digest(json_bytes(state))}


def _record_path(work, task_id, *, create=False):
    if not task_id or Path(task_id).name != task_id or task_id in {".", ".."}:
        raise RepairError("无效的 R3 任务编号")
    task = directory(Path(work) / ".aiw" / task_id)
    return directory(task / "compile-fix", private=True, create=create) / "state.json"


def _load(path):
    raw = read(path, optional=True)
    if raw is None:
        return None
    record = decode_json(raw)
    if type(record) is not dict:
        raise RepairError("编译修复状态无效")
    return record


def _view(record):
    identity = record["identity"]
    view = {key: identity[key] for key in ("task_id", "preview_id", "candidate_sha256")}
    view.update(status=record["status"], diagnostics=record["diagnostics"],
                evidence_id=record["evidence_id"], eligible=record["status"] == "eligible",
                expires_at=record["expires_at"], round_number=record["round_number"],
                round_limit=record["round_limit"], evidence=record["evidence"])
    if request := record.get("request"):
        view.update(request_id=request["request_id"], request_sha256=request["body_sha256"],
                    authorization_id=request["authorization_id"])
        if record["status"] == "prepared":
            view.update(body=request["body"], summary=request["summary"],
                        authorization=request["summary"], scope=request["scope"])
    if record["status"] in {"sending", "unknown"}:
        view.update(status="unknown", message=UNKNOWN,
                    actions=["view_log", "retry", "cancel"], log_path=record["log_path"])
    if review := record.get("review"):
        view.update(review_sha256=review["sha256"], diff=review["diff"], actions=review["actions"],
                    can_adopt=bool(review["diff"]))
    if record["status"] == "compiled":
        view.update(pdf_path=str(Path(record["compiled_dir"]) / (record["stem"] + ".pdf")),
                    compile_run_id=record["compile"]["run_id"])
    return view


def record_failure(work, state, preview, candidate_dir, *, capture, validated_bib, support,
                   prior_shas, had_final):
    """Prove the original candidate, source and support still match this attempt."""
    from .. import ai_workflow as workflow, ai_workflow_convert as conv

    if not capture or capture.get("l1_repaired") or support is None:
        return None
    conversion = state["conversion"]
    stem = stem_name(conversion["stem"])
    candidate_dir = directory(candidate_dir)
    if os.path.lexists(_guard_path(candidate_dir, stem)):
        return None
    report = capture.get("l1") or {}
    attempt = report.get("attempts", [])
    if (type(attempt) is not list or len(attempt) != 1 or type(attempt[0]) is not dict
            or attempt[0].get("result") not in {"not-admitted", "failed-rolled-back"}
            or report.get("candidate_sha256") != preview["text_new_sha256"]):
        return None
    failure = capture.get("failure")
    if (not isinstance(failure, tuple) or len(failure) != 4 or not failure[0]
            or failure[1] not in {1, 3, 4} or type(failure[2]) is not int or not failure[2]
            or not isinstance(failure[3], bytes) or not 0 < len(failure[3]) <= compiler.LOG_LIMIT
            or report.get("phase") != f"xelatex-{failure[1]}" or report.get("returncode") != failure[2]):
        return None
    before = {key: capture.get(key) for key in ("candidate_sha256", "candidate_id", "source_id", "out_id")}
    if capture_compile(candidate_dir, stem, source=conversion["inputs"]["source"]) != before:
        return None
    if conv._tree_shas(conversion["stage_dir"]) != conversion["stage_sha256s"]:
        return None
    conv._validate_inputs(conversion["inputs"])
    final = conversion["final_dir"]
    if os.path.isdir(final) != had_final or conv._tree_shas(final) != prior_shas:
        return None
    if workflow.load_state(work) != state:
        return None
    raw = conv._read_candidate(work, state, preview, kind="tex")
    if digest(raw) != before["candidate_sha256"] or read(candidate_dir / (stem + ".tex")) != raw:
        return None
    bib = conv._read_candidate(work, state, preview, kind="bib")
    if (bib is not None and digest(bib) != validated_bib) or compiler.support_manifest(candidate_dir, stem) != support:
        return None
    summaries = model.diagnostics(raw, failure[3], stem,
                                  f"xelatex 第 {failure[1]} 步失败（退出码 {failure[2]}）")
    text = model.text_candidate(raw)
    lines = text.split("\n")
    if model._UNSAFE_SOURCE_CONTEXT.search(text) or compiler._ACTIVE_FILE_COMMAND.search(raw):
        return None
    for item in summaries:
        if (item["classification"] == "editable"
                and not model._plain_lines(lines[item["source_line"] - 1], original=True)):
            item["classification"] = "manual"
    if not any(item["classification"] == "editable" for item in summaries):
        return None
    path = _record_path(work, state["task_id"], create=True)
    previous = _expire(path, _load(path))
    identity = _identity(state, preview)
    if previous and previous["status"] not in {"expired", "deleted"}:
        if previous["identity"] == identity and previous["support"] == support:
            return _view(previous)
        # An old preview confers no authority on the new one. Retain the
        # parent-task budget even when a different preview becomes active.
        _purge_artifacts(path, previous)
    limit = int(os.environ.get("SLCN_COMPILEFIX_ROUND_LIMIT", DEFAULT_ROUNDS))
    if not 1 <= limit <= MAX_ROUNDS:
        raise RepairError("编译修复轮次上限无效")
    log_path = path.parent / "l1-failure.log"
    atomic_write(log_path, failure[3])
    record = {"identity": identity, "failure_run_id": failure[0], "diagnostics": summaries,
              "evidence_id": digest(json_bytes([identity, failure[0], summaries, support])),
              "evidence": model.taskbook(state["task_id"], raw, summaries)["evidence"],
              "diagnostic_sha256": digest(json_bytes(summaries)), "support": support,
              "prior_shas": prior_shas, "had_final": had_final, "stem": stem,
              "status": "eligible", "expires_at": time.time() + RETENTION_SECONDS,
              "round_number": previous["round_number"] if previous else 0,
              "round_limit": previous["round_limit"] if previous else limit,
              "log_path": str(log_path)}
    put_json(path, record)
    return _view(record)


def _response_directory(path, request_id):
    if not isinstance(request_id, str) or re.fullmatch(r"[0-9a-f]{32}", request_id) is None:
        raise RepairError("原始回件请求身份无效")
    return path.parent / ("response-" + request_id)


def _raw_response_storage_supported(platform=None):
    # No current-user-only Windows ACL verifier is available in the supported runtime.
    return (platform or os.name) != "nt"


def _purge_response(path, record):
    request_id = (record.get("request") or {}).get("request_id")
    if not request_id:
        return
    if not isinstance(request_id, str) or re.fullmatch(r"[0-9a-f]{32}", request_id) is None:
        if "response_sha256" in record:
            raise RepairError("原始回件请求身份无效")
        return  # Compatibility with records written before raw responses were retained.
    folder = _response_directory(path, request_id)
    if os.path.lexists(folder):
        try:
            shutil.rmtree(directory(folder, private=True))
        except (OSError, RepairError):
            raise RepairError("原始回件清理未完成；可重试本地清理") from None


def _purge_artifacts(path, record, *, keep_failure_log=False):
    root = path.parent
    _purge_response(path, record)
    for field in ("compiled_dir",):
        if value := record.get(field):
            folder = Path(value)
            if folder.parent != root or not folder.name.startswith("compile-"):
                raise RepairError("编译修复清理目录身份不符")
            if os.path.lexists(folder):
                shutil.rmtree(directory(folder, private=True))
    for name in (("request.log", "reply.json") if keep_failure_log else ("l1-failure.log", "request.log", "reply.json")):
        (root / name).unlink(missing_ok=True)


def _clear(path, record, status):
    request_id = (record.get("request") or {}).get("request_id") or record.get("request_id", "")
    _purge_artifacts(path, record)
    for key in ("request", "book", "review", "reply_sha256", "response_sha256", "response_bytes",
                "compiled_dir", "compile", "log_path"):
        record.pop(key, None)
    if request_id:
        record["request_id"] = request_id
    record["status"] = status
    put_json(path, record)
    return record


def _expire(path, record):
    if record and record["status"] not in {"expired", "deleted"} and time.time() >= record["expires_at"]:
        return _clear(path, record, "expired")
    return record


def _retained_record(work, state):
    """Resolve the current R3 task's single retained slot, never by preview ID.

    `compile-fix retained` intentionally exposes a JSON list for the Flet
    bridge, with zero or one row for this active task. A changed preview does
    not replace that row; a new task has its own slot and is not mixed into the
    current task's UI response.
    """
    if state.get("entry") != "convert" or not state.get("conversion") or not state.get("task_id"):
        return None, None
    try:
        path = _record_path(work, state["task_id"])
        record = _load(path)
    except FileNotFoundError:
        return None, None
    if record is not None:
        if record["identity"]["task_id"] != state["task_id"]:
            raise RepairError("本地保留记录不属于当前任务")
        record = _expire(path, record)
        if record["status"] == "sending" and "response_sha256" not in record:
            # A response not committed to state can never be reviewed or resumed.
            _purge_response(path, record)
            (path.parent / "reply.json").unlink(missing_ok=True)
    return path, record


def expire_retained_locked(work, state):
    _retained_record(work, state)


def expire_retained(work):
    """Sweep this task on ordinary local CLI use, without creating a new state."""
    from .. import ai_workflow as workflow
    from ..ai_workflow_state_v3 import request_lock

    state = workflow.load_state(work) or {}
    if state.get("entry") != "convert" or state.get("schema_version") != workflow.SCHEMA_VERSION_R3:
        return
    try:
        with request_lock(work, existing_only=True) as acquired:
            if acquired:
                expire_retained_locked(work, workflow.load_state(work) or {})
    except (OSError, KeyError, TypeError, ValueError, RepairError):
        return "编译修复保留资料清理未完成；可重试本地清理"


def _retained_view(record, state):
    identity = record["identity"]
    request_id = (record.get("request") or {}).get("request_id") or record.get("request_id", "")
    tuple_bytes = json_bytes([identity["task_id"], identity["preview_id"],
                              record["evidence_id"], request_id])
    record_key = digest(b"slcn-compilefix-retained-record-v1\0" + tuple_bytes)
    event = (state.get("delivery") or {}).get("compile_fix") or {}
    status = record["status"]
    if state.get("phase") == "delivered" and request_id and event.get("request_id") == request_id:
        status = "delivered"
    return {"record_key": record_key, "task_id": identity["task_id"],
            "preview_id": identity["preview_id"], "status": "unknown" if status == "sending" else status,
            "expires_at": record["expires_at"], "has_reply": bool(record.get("reply_sha256"))}


def _active(work, preview_id, *, validate=True):
    from .. import ai_workflow as workflow, ai_workflow_convert as conv

    state = workflow.load_state(work) or {}
    if state.get("entry") != "convert" or not state.get("conversion") or not state.get("task_id"):
        raise RepairError("工作区没有 R3 初排任务")
    preview = state.get("preview") or {}
    if not preview_id or preview_id != preview.get("preview_id"):
        raise RepairError("不是最后校验通过的活动预览")
    path, record = _retained_record(work, state)
    if record is None or record["identity"]["preview_id"] != preview_id:
        raise RepairError("没有本轮 L1 安全失败证据")
    if not validate:
        return path, record, state, preview, None
    if (record["status"] in {"expired", "deleted"} or not preview.get("ok")
            or preview.get("reply_sha256", "") != (state.get("active_reply") or {}).get("sha256", "")
            or record["identity"] != _identity(state, preview) or state.get("phase") == "delivered"):
        raise RepairError("任务、活动预览或候选状态已变化")
    conversion = state["conversion"]
    conv._validate_inputs(conversion["inputs"])
    if conv._tree_shas(conversion["stage_dir"]) != conversion["stage_sha256s"]:
        raise RepairError("冻结产物已变化")
    final = conversion["final_dir"]
    if (os.path.isdir(final) != record["had_final"] or conv._tree_shas(final) != record["prior_shas"]
            or (os.path.lexists(final) and not os.path.isdir(final))):
        raise RepairError("最终目录已变化")
    raw = conv._read_candidate(work, state, preview, kind="tex")
    conv._read_candidate(work, state, preview, kind="bib")
    if digest(json_bytes(record["diagnostics"])) != record["diagnostic_sha256"]:
        raise RepairError("失败诊断已变化")
    return path, record, state, preview, raw


def _book(record, raw, request_id, scope):
    full = model.taskbook(record["identity"]["task_id"], raw, record["diagnostics"], task_id=request_id)
    ids = [item["evidence_id"] for item in full["evidence"]]
    if scope is None:
        selected = ids
    elif (type(scope) is dict and set(scope) == {"evidence_ids"}
          and type(scope["evidence_ids"]) is list and scope["evidence_ids"]
          and all(type(key) is str and key in ids for key in scope["evidence_ids"])
          and len(scope["evidence_ids"]) == len(set(scope["evidence_ids"]))):
        selected = [key for key in ids if key in scope["evidence_ids"]]
    else:
        raise RepairError("请求范围必须选择本轮签发的诊断证据")
    full["evidence"] = [item for item in full["evidence"] if item["evidence_id"] in selected]
    original = full["diagnostics"]
    full["diagnostics"] = [original[item["diagnostic_index"]] for item in full["evidence"]]
    for index, item in enumerate(full["evidence"]):
        item["diagnostic_index"] = index
    full["identity"]["diagnostic_sha256"] = digest(json_bytes(full["diagnostics"]))
    if not any(item["classification"] == "editable" for item in full["diagnostics"]):
        full["actions"] = ["report_only"]
    return full, {"evidence_ids": selected}


def _require(record, request_id, states):
    if record["status"] not in states or not request_id or request_id != record.get("request", {}).get("request_id"):
        raise RepairError("编译修复请求已变化、过期或不在此操作所需状态")


def _import(path, record, raw, reply, *, response=None):
    model.parse_reply(reply, record["book"], raw)
    updated = dict(record, status="imported", reply_sha256=digest(reply))
    if response is not None:
        folder = _response_directory(path, record["request"]["request_id"])
        # These are exact bounded HTTP response-body/entity bytes, not a wire envelope.
        if not _raw_response_storage_supported():
            raise RepairError("无法验证原始回件的 Windows 私有 ACL；回件未导入")
        try:
            folder.mkdir(mode=0o700)
            atomic_write(directory(folder, private=True) / "response.raw", response)
        except (OSError, RepairError):
            raise OSError("原始回件未能安全写入本地工作区") from None
        updated.update(response_sha256=digest(response), response_bytes=len(response))
    atomic_write(path.parent / "reply.json", reply)
    put_json(path, updated)
    record.update(updated)


def _review(path, record, raw):
    reply = read(path.parent / "reply.json", limit=REPLY_LIMIT)
    if digest(reply) != record["reply_sha256"]:
        raise RepairError("已导入回件内容发生变化")
    actions = model.parse_reply(reply, record["book"], raw)
    edited, diff = model.edit_candidate(raw, record["book"], actions)
    reviewed = {"actions": actions, "diff": diff.decode("utf-8"), "new_sha256": digest(edited)}
    reviewed["sha256"] = digest(json_bytes([record["request"]["request_id"], record["reply_sha256"], reviewed]))
    return edited, reviewed


def _artifact_shas(folder, stem):
    shas = {}
    for suffix in compiler.COMPILE_EXTENSIONS:
        name = stem + suffix
        if (data := read(folder / name, compiler.ARTIFACT_LIMIT, optional=True)) is not None:
            shas[name] = digest(data)
    return shas


def _adopt(work, path, record, state, preview, raw, review_sha256):
    from .. import ai_workflow_convert as conv

    edited, review = _review(path, record, raw)
    if review != record["review"] or not review_sha256 or review_sha256 != review["sha256"] or not review["diff"]:
        raise RepairError("采纳需要本次审阅过的有效正文差异")
    folder = directory(path.parent / ("compile-" + uuid.uuid4().hex), private=True, create=True)
    record.update(status="compiling", compiled_dir=str(folder))
    put_json(path, record)
    try:
        conversion = state["conversion"]
        stage = directory(conversion["stage_dir"])
        stem = record["stem"]
        bib_name = conversion["bib_name"]
        bib = conv._read_candidate(work, state, preview, kind="bib")
        if bib is not None and not bib_name:
            bib_name = "reference.bib"
        # The failure manifest binds the preview Bib, not the stage's original Bib.
        support = compiler.support_manifest(stage, stem)
        to_copy = {name: sha for name, sha in support.items() if name != bib_name}
        compiler.copy_support(stage, folder, to_copy)
        if bib_name:
            atomic_write(folder / bib_name, bib if bib is not None else b"")
        if compiler.support_manifest(folder, stem) != record["support"]:
            raise RepairError("隔离复编依赖与失败时冻结的依赖不一致")
        tex = folder / (stem + ".tex")
        atomic_write(tex, edited)
        conv._check_references(str(tex), str(folder / bib_name) if bib_name else "")
        result = compiler.compile_candidate(folder, stem, review["new_sha256"], record["support"], path.parent)
        if result.state != "passed" or result.candidate_sha256 != review["new_sha256"] or not result.run_id:
            raise RepairError(f"隔离复编未通过（{result.phase}），已保留原预览")
        shas = _artifact_shas(folder, stem)
        if (not all(shas.get(stem + suffix) == expected and expected for suffix, expected in (
                (".pdf", result.pdf_sha256), (".log", result.log_sha256), (".fls", result.recorder_sha256)))
                or compiler.support_manifest(folder, stem) != record["support"]
                or digest(read(tex)) != review["new_sha256"]):
            raise RepairError("隔离复编产物、候选或依赖指纹不符")
        _active(work, preview["preview_id"])
        record.update(status="compiled", compile={**asdict(result), "artifacts": shas})
        put_json(path, record)
    except (OSError, ValueError, RepairError):
        record["status"] = "compile_failed"
        put_json(path, record)
        raise


def require_same_fs(work, final):
    """The original promotion and rollback use renames on the delivery filesystem."""
    origin = directory(Path(work) / ".aiw")
    target = Path(final).parent
    while not os.path.lexists(target):
        target = target.parent
    target_dev = directory(target).stat().st_dev
    if (origin.stat().st_dev != target_dev
            or (os.path.lexists(final) and directory(final).stat().st_dev != target_dev)):
        raise RepairError("编译修复交付要求工作区与最终目录位于同一文件系统")


def install_verified(work, state, preview, candidate, path, record):
    """Load the exact reviewed edit and bound artifacts into the existing R3 transaction."""
    _, fresh, _, _, raw = _active(work, preview["preview_id"])
    if fresh != record:
        raise RepairError("隔离复编记录已变化")
    patched, review = _review(path, record, raw)
    if review != record["review"]:
        raise RepairError("已审阅差异发生变化")
    stem = record["stem"]
    compiled_dir = directory(record["compiled_dir"], private=True)
    if compiled_dir.parent != path.parent or not compiled_dir.name.startswith("compile-"):
        raise RepairError("隔离编译目录身份不符")
    compiled = record["compile"]
    if (digest(read(compiled_dir / (stem + ".tex"))) != review["new_sha256"]
            or compiler.support_manifest(compiled_dir, stem) != record["support"]
            or _artifact_shas(compiled_dir, stem) != compiled["artifacts"]
            or compiler.support_manifest(candidate, stem) != record["support"]):
        raise RepairError("交付前隔离编译产物或依赖已变化")
    compiler.clear_outputs(candidate, stem)
    target = candidate / (stem + ".tex")
    atomic_write(target, patched)
    for name, expected in compiled["artifacts"].items():
        artifact = read(compiled_dir / name, compiler.ARTIFACT_LIMIT)
        if digest(artifact) != expected:
            raise RepairError("复制期间复编产物已变化")
        atomic_write(candidate / name, artifact)
    return patched, {"task_id": state["task_id"], "preview_id": preview["preview_id"],
                     "evidence_id": record["evidence_id"], "request_id": record["request"]["request_id"],
                     "candidate_sha256": review["new_sha256"],
                     "pdf_sha256": compiled["pdf_sha256"], "log_sha256": compiled["log_sha256"],
                     "run_id": compiled["run_id"], "evidence_ids": [item["evidence_id"] for item in review["actions"]
                                                                  if item["action"] == "edit"]}


def note_lines(event):
    if event is None:
        return []
    return ["", "## 编译修复", f"- 请求编号：{event['request_id']}",
            "- 人工审阅后采纳：" + "、".join(event["evidence_ids"]),
            f"- 复编通过（运行编号：{event['run_id']}）",
            f"- TeX SHA-256：{event['candidate_sha256']}", f"- PDF SHA-256：{event['pdf_sha256']}"]


@contextmanager
def _locked(work):
    from .. import ai_workflow as workflow
    from ..ai_workflow_state_v3 import request_lock

    if not os.path.isfile(workflow.state_path(work)):
        raise RepairError("工作区没有 R3 初排任务")
    with request_lock(work) as acquired:
        if not acquired:
            raise RepairError("任务正在处理")
        yield


def _recover_publication(work):
    from .. import ai_workflow as workflow, ai_workflow_convert as conv

    conv._recover_compile_preview(work)
    journal_path = conv._journal_path(work)
    receipt = conv._read_journal(journal_path)
    state = workflow.load_state(work) or {}
    if receipt.get("promoting"):
        conv._recover_delivery(work, receipt)
        conv.coop.atomic_write_json(journal_path, {**receipt, "promoting": False, "recovered": True})
    if (receipt.get("delivered") and not receipt.get("undone") and receipt.get("compile_fix")
            and receipt.get("task_id") == state.get("task_id")):
        if conv._tree_shas(receipt["final"]) != receipt["shas"]:
            raise RepairError("已交付目录发生外部修改")
        if state.get("delivery") != receipt:
            conv._record_delivery_state(work, state, receipt)


def for_publication(work, preview_id, evidence_id, request_id, review_sha256):
    path, record, _, _, _ = _active(work, preview_id)
    _check_evidence(record, evidence_id)
    _require(record, request_id, {"compiled"})
    if not review_sha256 or review_sha256 != record["review"]["sha256"]:
        raise RepairError("交付必须确认本轮已审阅和复编通过的差异")
    return path, record


def _check_evidence(record, supplied):
    if not supplied or supplied != record["evidence_id"]:
        raise RepairError("编译失败证据已变动或未明确指定")


def _out(record, error=None):
    out = {"ok": error is None, "compile_fix": _view(record)}
    if error is not None:
        out["error"] = error
    return out


def _prepare(path, record, raw, settings, scope, api_key):
    from .l2_request import fingerprint_api_key, prepare_request

    if record["status"] not in {"eligible", "prepared", "unknown", "sending", "compile_failed", "cancelled"}:
        raise RepairError("此状态不能开始新请求")
    rounds = record["round_number"]
    if rounds >= record["round_limit"]:
        raise RepairError("已达到单篇请求轮次上限")
    request_id = uuid.uuid4().hex
    book, scope = _book(record, raw, request_id, scope)
    credential = fingerprint_api_key(api_key)
    request = prepare_request(identity=book["identity"], request_id=request_id,
                              round_number=rounds + 1, scope=scope, settings=settings,
                              key_configured=credential is not None, credential_fingerprint=credential,
                              system="Return only JSON conforming to the supplied compile-repair taskbook.",
                              user=json_bytes(book).decode("utf-8")).to_record()
    _purge_artifacts(path, record, keep_failure_log=True)
    record.pop("compiled_dir", None)
    atomic_write(path.parent / "request.log", b"Prepared; no network transmission attempted.\n")
    record.update(status="prepared", request=request, book=book, log_path=str(path.parent / "request.log"))
    for key in ("review", "reply_sha256", "response_sha256", "response_bytes", "compile"):
        record.pop(key, None)
    put_json(path, record)


def _response_bytes(result, reply, limit, api_key):
    raw = result.get("source_bytes")
    if type(raw) is not bytes or not 0 < len(raw) <= min(limit, REPLY_LIMIT):
        raise RepairError("原始回件缺失或超过本次上限")
    # A provider may echo credentials in otherwise valid extra JSON fields.
    if api_key:
        pending = [decode_json(raw), decode_json(reply)]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value)
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, str) and api_key in value:
                raise RepairError("原始回件包含不应保留的凭据")
    return raw


def _send(work, preview_id, evidence_id, request_id, request_sha256, authorization_id,
          terms_ack, settings, scope, api_key, timeout, response_limit, test_urlopen):
    from .l2_request import FrozenRequest, send_request

    with _locked(work):
        _recover_publication(work)
        _, record, _, _, raw = _active(work, preview_id)
        _check_evidence(record, evidence_id)
        _require(record, request_id, {"prepared"})
        if not request_sha256 or request_sha256 != record["request"]["body_sha256"]:
            raise RepairError("已审阅请求字节指纹不符")
        book, scope = _book(record, raw, request_id, scope)
        if book != record["book"]:
            raise RepairError("任务书或请求范围已变动")
        frozen = FrozenRequest.from_record(record["request"])

    def consume(claim_id, claim_sha):
        with _locked(work):
            path, fresh, _, _, _ = _active(work, preview_id)
            if fresh != record or claim_id != request_id or claim_sha != request_sha256:
                return False
            fresh.update(status="sending", round_number=fresh["round_number"] + 1)
            atomic_write(path.parent / "request.log", b"Send intent recorded; no automatic retry.\n")
            put_json(path, fresh)
            return True

    result = send_request(frozen, authorization_id=authorization_id, compile_fix_terms_ack=terms_ack,
                          identity=book["identity"], scope=scope, settings=settings, api_key=api_key,
                          consume=consume, timeout=timeout, response_limit=response_limit,
                          _test_urlopen=test_urlopen)
    with _locked(work):
        path, fresh, _, _, current = _active(work, preview_id)
        _check_evidence(fresh, evidence_id)
        _require(fresh, request_id, {"sending"})
        if result["status"] == "reply":
            try:
                reply = result["source_text"].encode("utf-8")
                if len(reply) > REPLY_LIMIT:
                    raise RepairError("结构化回件超过本地上限")
                response = _response_bytes(result, reply, response_limit, api_key)
                _import(path, fresh, current, reply, response=response)
                return _out(fresh)
            except (RepairError, UnicodeError):
                pass
        fresh["status"] = "unknown"
        atomic_write(path.parent / "request.log", (UNKNOWN + "\n").encode("utf-8"))
        put_json(path, fresh)
        return _out(fresh, UNKNOWN)


def operate(work, action, *, preview_id="", evidence_id="", request_id="", record_key="", request_sha256="",
            authorization_id="", review_sha256="", terms_ack=False, settings=None, scope=None,
            reply_text=None, api_key=None, timeout=None, response_limit=None, _test_urlopen=None):
    work = str(Path(work).resolve())
    try:
        if action not in ACTIONS:
            raise RepairError("未知编译修复操作")
        if action == "send":
            if timeout is None or response_limit is None:
                raise RepairError("请求需要显式传输超时与原始回件上限；未发送")
            return _send(work, preview_id, evidence_id, request_id, request_sha256, authorization_id,
                         terms_ack, settings, scope, api_key, timeout, response_limit, _test_urlopen)
        with _locked(work):
            if action in {"retained", "delete"}:
                from .. import ai_workflow as workflow

                state = workflow.load_state(work) or {}
                path, record = _retained_record(work, state)
                if action == "retained":
                    keep = record and record["status"] not in {"deleted", "cancelled"}
                    # Stable array contract: 0 rows without a retained record,
                    # otherwise the single slot belonging to the current task.
                    return {"ok": True, "retained": [_retained_view(record, state)] if keep else []}
                if record is None:
                    raise RepairError("没有对应的本地编译修复保留记录")
                expected = _retained_view(record, state)
                if (not record_key or record_key != expected["record_key"]
                        or preview_id or evidence_id or request_id):
                    raise RepairError("本地保留记录身份已变化；拒绝清理")
                if record["status"] not in {"expired", "deleted", "cancelled"}:
                    record = _clear(path, record, "cancelled" if action == "cancel" else "deleted")
                return {"ok": True, "compile_fix": _retained_view(record, state)}
            _recover_publication(work)
            path, record, state, preview, raw = _active(work, preview_id, validate=action != "status")
            if action == "status":
                event = (state.get("delivery") or {}).get("compile_fix")
                if (state.get("phase") == "delivered" and event
                        and event["request_id"] == record.get("request", {}).get("request_id")):
                    record = {**record, "status": "delivered"}
                if record["status"] == "compiling":
                    record["status"] = "compile_failed"
                    put_json(path, record)
                return _out(record)
            _check_evidence(record, evidence_id)
            if action == "prepare":
                _prepare(path, record, raw, settings, scope, api_key)
            elif action == "import":
                _require(record, request_id, {"prepared", "unknown"})
                if type(reply_text) is not str or len(reply := reply_text.encode("utf-8")) > REPLY_LIMIT:
                    raise RepairError("回件必须是未超限的 UTF-8 文本")
                _import(path, record, raw, reply)
            elif action == "review":
                _require(record, request_id, {"imported", "reviewed"})
                _, review = _review(path, record, raw)
                record.update(status="reviewed", review=review)
                put_json(path, record)
            elif action == "apply":
                _require(record, request_id, {"reviewed"})
                try:
                    _adopt(work, path, record, state, preview, raw, review_sha256)
                except (OSError, ValueError, RepairError) as exc:
                    return _out(record, str(exc))
            else:
                raise RepairError("未知编译修复操作")
            return _out(record)
    except (OSError, TypeError, ValueError, KeyError, RepairError) as exc:
        return {"ok": False, "error": str(exc)}
