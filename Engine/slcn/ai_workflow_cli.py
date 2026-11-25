"""AI 工作流 CLI（DESIGN §8 七个操作）：prepare / request / import / preview / apply / undo / status。

**为什么不新开一个入口**：`Engine/engine_main.py: ENTRIES`、Windows sidecar 的 `$Entries`、
Flet 桥的 `ENTRIES` 三张表逐项互钉（`Engine/tests/test_platform.py` (g)、
`Engine/tests/test_b285_contract.py` (e)），加第 14 个入口要同时改四份表 + 打包白名单。
本模块因此**挂在既有入口 `convert.py` 上**：`convert.py main()` 在 argparse **之前**
看到 `--ai-workflow` 就整段交过来（`--source` 是 required=True，晚一步就进不来）。

调用形状（与 `Engine/docs/ENGINE_API.md` 一致）：

    convert.py --ai-workflow prepare --work-dir D --entry bib --target-bib X.bib
    convert.py --ai-workflow request --work-dir D
    convert.py --ai-workflow import  --work-dir D --reply 回填.md
    convert.py --ai-workflow preview --work-dir D --adopt verify,proofcheck
    convert.py --ai-workflow apply   --work-dir D --preview-id pv-xxxxxxxxxxxx
    convert.py --ai-workflow undo    --work-dir D
    convert.py --ai-workflow status  --work-dir D

退出码：0 = 操作完成（**不等于**没有阻断项，阻断看 `ok`/`blocked`）；
2 = 用法错误 / 操作被拒（`WorkflowError`：状态过期、指纹不符、备份不在…）。
stdout 只放结果与 `key=value` 进度行；**失败绝不打印成功结论**。
API Key 只从环境/配置读（`slcn.llm.config_from_env`），**永不进 argv / 任务书 / 日志**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

from . import ai_workflow as _w
from . import ai_workflow_state_v3 as _sv3

#: 退出码：0 操作完成；2 用法错误或操作被拒（与 checkbib/convert 的既有口径一致）。
RC_OK = 0
RC_FAIL = 2


def _read(path: str) -> str:
    """按 UTF-8 → GB18030 顺序读文本（中文 Windows 交回的 .bib 常见 GBK）。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _emit(payload: dict, *, as_json: bool, lines: list[str]) -> None:
    """结果输出：`--json` 打一份完整 JSON；否则打可读行 + `key=value` 进度行。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for line in lines:
        print(line)


def _scope_of(raw: str):
    """`--execute a,b` / `--adopt a,b` → `{a, b}`；空串 = 用默认。"""
    items = {p.strip() for p in str(raw or "").split(",") if p.strip()}
    bad = sorted(items - set(_w.CATS))
    if bad:
        raise _w.WorkflowError(
            f"不认识的类别：{'、'.join(bad)}（只有 {'、'.join(_w.CATS)}）")
    return items


def _decisions_of(raw_list, raw_json) -> dict:
    """`--decide cf-xxxx=library`（可重复）+ `--decide-json '{...}'` → `{cf-id: 值}`。

    未知 conflict_id、非法裁决值一律由 `state_v3.check_decisions` 拒绝（退出码 2），
    这里只做语法解析：缺 `=` 或空 id 属于用法错误，同样走 WorkflowError。
    """
    out = {}
    for raw in raw_list or []:
        item = str(raw or "").strip()
        if not item:
            continue
        if "=" not in item:
            raise _w.WorkflowError(
                f"裁决写法不合法：{item}（要写成 cf-xxxxxxxx=library|reply|original）")
        cid, value = item.split("=", 1)
        cid = cid.strip()
        if not cid:
            raise _w.WorkflowError(f"裁决缺少 conflict_id：{item}")
        out[cid] = value.strip()
    text = str(raw_json or "").strip()
    if text:
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise _w.WorkflowError(f"--decide-json 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise _w.WorkflowError("--decide-json 要一个对象：{\"cf-xxxx\": \"library\"}")
        for cid, value in payload.items():
            out[str(cid).strip()] = str(value).strip()
    return out


def _status_lines(st: dict) -> list[str]:
    lines = [f"phase={st.get('phase', '')}", f"task_id={st.get('task_id', '')}"]
    if st.get("preview_id"):
        lines.append(f"preview_id={st['preview_id']}")
    # `keys=` / `entries=` 是界面（bib_ai_assist.parse_status）读的两个计数字段；
    # 缺了它们，界面的「引用键数/条目数」永远停在旧值（静默不刷新）。
    lines.append(f"keys={st.get('cite_keys', 0)}")
    lines.append(f"entries={st.get('bib_keys', 0)}")
    # 候选文件路径：界面的「在 VS Code 里对比」要用它做新版一侧（只读展示，
    # 应用仍然只认 preview_id + 指纹）。没有预览时这一行不出现。
    cand = str(st.get("candidate_bib") or "")
    if cand:
        lines.append(f"candidate_bib={cand}")
        if st.get("candidate_bib_sha256"):
            lines.append(f"candidate_bib_sha256={st['candidate_bib_sha256']}")
    lines.append(f"message={st.get('message', '')}")
    cats = st.get("categories") or {}
    for cat in _w.CATS:
        got = cats.get(cat) or {}
        if not got:
            continue
        unit = got.get("unit", "")
        bits = [f"{k}={v}" for k, v in got.items() if k != "unit" and isinstance(v, int)]
        lines.append(f"category {cat}（{unit}）：{' '.join(bits)}")
    nxt = st.get("next") or []
    if nxt:
        lines.append("next=" + ",".join(nxt))
    # 以下两行是本包新增的投影行：**追加在既有行之后**，既有 `phase=` / `task_id=` /
    # `preview_id=` / `keys=` / `entries=` / `candidate_bib=` / `message=` / `category` /
    # `next=` 一行不动，界面既有解析不受影响。
    lib = st.get("library") or {}
    if lib:
        bits = [f"checked={'true' if lib.get('checked') else 'false'}",
                f"state={lib.get('state', '')}",
                f"matched={lib.get('matched', 0)}",
                f"conflicts={lib.get('conflicts', 0)}"]
        if str(lib.get("path") or ""):
            bits.append(f"path={lib['path']}")
        if str(lib.get("sha256") or ""):
            bits.append(f"sha256={lib['sha256']}")
        lines.append("library=" + " ".join(bits))
        if str(lib.get("skipped_reason") or ""):
            lines.append("library_skipped_reason=" + str(lib["skipped_reason"]))
    rows = st.get("conflicts") or []
    decided = st.get("decisions") or {}
    if rows or decided:
        lines.append(f"conflicts={len(rows)} decided={len(decided)}")
        for row in rows:
            cid = str(row.get("conflict_id") or "")
            chosen = str(decided.get(cid) or row.get("default") or "original")
            lines.append(
                f"conflict {cid} key={row.get('key', '')} field={row.get('field', '')}"
                f" group={row.get('group', '')} decided={chosen}")
    return lines


# ── 七个操作 ──────────────────────────────────────────────────────────────


def _op_prepare(a) -> dict:
    target = os.path.abspath(a.target_bib)
    bib_text = _read(target)
    tex_text = _read(a.tex) if a.tex else ""
    source_path = os.path.abspath(a.source) if a.source else ""
    defaults = _w.Scope.defaults()
    scope = _w.Scope(exec=defaults.exec if a.execute is None else _scope_of(a.execute),
                     adopt=defaults.adopt if a.adopt is None else _scope_of(a.adopt))
    if a.text:
        scope.exec.add(_w.CAT_TEXT)
    work_dir = os.path.abspath(a.work_dir)
    res = _w.prepare(work_dir=work_dir, entry=a.entry,
                     bib_text=bib_text, tex_text=tex_text, scope=scope,
                     stem=a.stem, source_path=source_path, target_path=target,
                     taskbook_version=a.taskbook_version,
                     bib_out=os.path.abspath(a.bib_out) if a.bib_out else "",
                     tex_path=os.path.abspath(a.tex) if a.tex else "",
                     text_target=os.path.abspath(a.tex_out) if a.tex_out else "")
    return {"ok": True, "op": "prepare", "task_id": res.task_id,
            "work_dir": work_dir, "taskbook": res.taskbook_path,
            "fingerprint": res.fingerprint,
            "cite_keys": len(res.cite_keys), "bib_keys": len(res.bib_keys),
            "items": len(res.items), "taskbook_text": res.taskbook_text}


def _op_request(a) -> dict:
    tb = a.taskbook
    if not tb:
        st = _w.load_state(a.work_dir) or {}
        tb = (st.get("taskbook") or {}).get("path") or ""
    if not tb or not os.path.exists(tb):
        raise _w.WorkflowError("找不到任务书：先 prepare（出任务书），或用 --taskbook 指定。")
    taskbook_text = _read(tb)
    supplied_sha_arg = getattr(a, "taskbook_sha256", None)
    if supplied_sha_arg is not None:
        supplied_sha = str(supplied_sha_arg).strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", supplied_sha):
            raise _w.WorkflowError("--taskbook-sha256 必须是 64 位十六进制 SHA-256。")
        actual_sha = hashlib.sha256(taskbook_text.encode("utf-8")).hexdigest()
        if supplied_sha.lower() != actual_sha:
            raise _w.WorkflowError("任务书 SHA-256 与 --taskbook-sha256 不一致。")
    # API 配置只从环境/配置读；Key 绝不进 argv、任务书、日志。
    out = _w.request(work_dir=os.path.abspath(a.work_dir),
                     taskbook_text=taskbook_text, timeout=a.timeout,
                     reply_path=a.reply_out or "",
                     retry=bool(getattr(a, "retry", False)))
    return {"op": "request", **out}


def _op_import(a) -> dict:
    text = ""
    if a.stdin:
        raw = sys.stdin.buffer.read()
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
    out = _w.import_reply(work_dir=os.path.abspath(a.work_dir), text=text,
                          reply_path=os.path.abspath(a.reply) if a.reply else "",
                          task_id=a.task_id, source_kind=a.source_kind,
                          replace_active=a.replace_active)
    return {"op": "import", **out}


def _op_preview(a) -> dict:
    from .compilefix.l2_r3 import expire_retained

    work = os.path.abspath(a.work_dir)
    cleanup_error = expire_retained(work)
    adopt = None if a.adopt is None else _scope_of(a.adopt)
    decisions = _decisions_of(getattr(a, "decide", None), getattr(a, "decide_json", ""))
    if a.reply:
        imported = _w.import_reply(work_dir=os.path.abspath(a.work_dir),
                                   reply_path=os.path.abspath(a.reply))
        if not imported.get("ok"):
            raise _w.WorkflowError("；".join(imported.get("notes") or ["回填被拒收。"]))
    # 统一走 ai_workflow.preview 门面（R3 由它透传给 state_v3），CLI 不出现 R3 分支。
    pv = _w.preview(work_dir=os.path.abspath(a.work_dir), adopt=adopt,
                    skip_items=a.skip_item, decisions=decisions,
                    requery=bool(getattr(a, "requery", False)))
    state = _w.load_state(os.path.abspath(a.work_dir)) or {}
    return {"op": "preview", "ok": pv.ok, "preview_id": pv.preview_id,
            "phase": pv.phase, "blocked": pv.blocked, "notes": pv.notes,
            "library": _sv3.library_of(state),
            "conflicts": _sv3.conflicts_of(state),
            "decisions": _sv3.decisions_of(state),
            "bib": {k: v for k, v in pv.bib.items() if k != "text"},
            "text": {k: v for k, v in pv.text.items() if k != "text"},
            **({"compile_fix_cleanup_error": cleanup_error} if cleanup_error else {})}


def _op_apply(a) -> dict:
    st = _w.load_state(a.work_dir) or {}
    # `--confirm-from` 优先；没给就用**本次预览记下的候选文件**（界面走这条）。
    # 两者都没有才落到「没写任何文件」——绝不去猜一个路径或临时拼一份。
    src = a.confirm_from or str(((st.get("preview") or {}).get("candidate_bib")) or "")
    text = _read(src) if src and os.path.exists(src) else ""
    tsrc = a.confirm_tex or str(((st.get("preview") or {}).get("candidate_text")) or "")
    tex = _read(tsrc) if tsrc and os.path.exists(tsrc) else ""
    out = _w.apply(work_dir=os.path.abspath(a.work_dir), preview_id=a.preview_id,
                   confirm_text=text, confirm_tex=tex)
    return {"op": "apply", **out}


def _op_undo(a) -> dict:
    work = os.path.abspath(a.work_dir)
    state = _w.load_state(work) or {}
    # R3 交付（schema /3）：整目录撤销要走 deliver 的 undo_delivery。
    # 旧 /1 任务仍走原逐文件 undo，行为完全不变。
    if str(state.get("schema_version") or "") == _w.SCHEMA_VERSION_R3 \
            and state.get("entry") == _w.ENTRY_CONVERT and state.get("conversion"):
        from . import ai_workflow_convert as _awc
        out = _awc.undo_delivery(work_dir=work)
        return {"op": "undo", **out}
    return {"op": "undo", **_w.undo(work_dir=work)}


def _op_taskbook(a) -> dict:
    """R3 §10 只读任务书全文：返回恰好 §10 七键（供 bridge 投影）。"""
    out = _w.read_taskbook(work_dir=os.path.abspath(a.work_dir),
                           task_id=getattr(a, "task_id", "") or "")
    return dict(out)


def _op_deliver(a) -> dict:
    """R3 交付：采纳候选写入最终目录；use_original 保留原 Bib 不采纳。"""
    from . import ai_workflow_convert as _awc
    work = os.path.abspath(a.work_dir)
    preview_id = getattr(a, "preview_id", "") or ""
    use_original = bool(getattr(a, "use_original", False))
    repair = tuple(getattr(a, name, "") for name in ("evidence_id", "request_id", "review_sha256"))
    enabled = bool(getattr(a, "ai_enabled", False) and getattr(a, "compile_fix_enabled", False))
    kwargs = {}
    if any(repair) or enabled:
        kwargs.update(compile_fix_enabled=enabled, repair=repair if any(repair) else None)
    out = _awc.deliver(work_dir=work, preview_id=preview_id, use_original=use_original, **kwargs)
    # ACCEPTANCE:22 第 9 条：旧预览的 deliver 必须被拒，且错误要能定位到**旧 ID**。
    # `ai_workflow_convert.deliver` 的文案只说「不是最后校验通过的活动预览」、不含 ID，
    # 且该文件属 A 包写域 —— 这里只在**已判拒**的前提下补「旧 ID / 当前 ID」两个值，
    # 不改 ok / delivered 语义，也不改 stdout JSON 的形状（仍是 deliver 的结果对象）。
    if not out.get("ok") and preview_id and not use_original:
        state = _w.load_state(work) or {}
        current = str(((state.get("preview") or {}).get("preview_id")) or "")
        error = str(out.get("error") or "")
        if "不是最后校验通过的活动预览" in error and preview_id not in error:
            out = dict(out, error=(
                "%s（收到 %s，当前 %s；请重新 preview 后再交付）"
                % (error, preview_id, current or "（没有活动预览）")))
    return {"op": "deliver", **out}


def _op_compile_fix(a) -> dict:
    from .compilefix import l2_r3
    from .compilefix.files import TEXT_LIMIT, RepairError, decode_json

    try:
        options = {}
        api_key = None
        if a.action not in {"retained", "delete"} and not a.preview_id:
            raise RepairError("操作需要明确指定预览编号")
        if a.action in {"prepare", "send"}:
            api_key = os.environ.pop(l2_r3.API_KEY_ENV, "")
            if not api_key:
                raise RepairError("未通过专用环境提供当前配置的 API key；未发送")
        if a.action in {"prepare", "send", "import"}:
            raw = sys.stdin.buffer.read(TEXT_LIMIT + 1)
            if len(raw) > TEXT_LIMIT:
                raise RepairError("编译修复操作输入超限")
            options = decode_json(raw)
            allowed = {"reply_text", "evidence_id", "request_id"} if a.action == "import" else {"settings", "scope"}
            if type(options) is not dict or options.keys() - allowed:
                raise RepairError("编译修复操作输入字段无效")
        if a.action == "import":
            for key in ("evidence_id", "request_id"):
                if key in options and getattr(a, key) and options[key] != getattr(a, key):
                    raise RepairError("回件操作的请求身份不一致")
        result = l2_r3.operate(
            os.path.abspath(a.work_dir), a.action, preview_id=a.preview_id,
            evidence_id=a.evidence_id or options.get("evidence_id", ""),
            request_id=a.request_id or options.get("request_id", ""), request_sha256=a.request_sha256,
            authorization_id=a.authorization_id, review_sha256=a.review_sha256,
            terms_ack=a.compile_fix_terms_ack, record_key=a.record_key,
            settings=options.get("settings"), scope=options.get("scope"),
            reply_text=options.get("reply_text"), timeout=a.timeout, response_limit=a.response_limit,
            api_key=api_key)
    except (OSError, RepairError) as exc:
        result = {"ok": False, "error": str(exc)}
    return {"op": "compile-fix", **result}


def _op_status(a) -> dict:
    from .compilefix.l2_r3 import expire_retained

    work = os.path.abspath(a.work_dir)
    cleanup_error = expire_retained(work)
    st = _w.status(work)
    state = _w.load_state(work) or {}
    preview = state.get("preview") or {}
    # 两次读状态之间可能恰好生成了另一份预览。摘要只属于同一任务、同一候选。
    digest = ""
    if (st.get("candidate_bib") and st.get("preview_id")
            and st.get("task_id") == state.get("task_id")
            and st["preview_id"] == preview.get("preview_id")
            and st["candidate_bib"] == preview.get("candidate_bib")):
        digest = str(preview.get("bib_new_sha256") or "")
    # 合同 §1：status 必须带 library / conflicts / decisions 三键。
    # `_w.status()` 是 A 包门面、保持不动；这里只做投影合并（缺省给「尚未查库」）。
    return {"op": "status", **st,
            "candidate_bib_sha256": digest,
            "library": _sv3.library_of(state),
            "conflicts": _sv3.conflicts_of(state),
            "decisions": _sv3.decisions_of(state),
            **({"compile_fix_cleanup_error": cleanup_error} if cleanup_error else {})}


_OPS = {
    "prepare": (_op_prepare, ["工作区已建立", "任务书"]),
    "request": (_op_request, ["已请求", "回填"]),
    "import": (_op_import, ["已导入"]),
    "preview": (_op_preview, ["拟应用结果"]),
    "apply": (_op_apply, ["已应用"]),
    "undo": (_op_undo, ["已撤销"]),
    "status": (_op_status, ["状态"]),
    "taskbook": (_op_taskbook, ["任务书"]),
    "deliver": (_op_deliver, ["已交付"]),
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="convert.py --ai-workflow",
        description="AI 初排/Bib 处理工作流（DESIGN §8）：prepare → request|import → "
                    "preview → apply → undo；status 随时可查。",
        epilog="两段式与直连 API 走同一份判据：类别采纳、预览、应用都不绕过同一套校验。")
    ap.add_argument("--work-dir", required=True, metavar="DIR",
                    help="工作区（任务书与状态都写这里；界面按任务分开建）")
    ap.add_argument("--json", action="store_true", help="机器可读输出（界面用）")
    sub = ap.add_subparsers(dest="op", required=True, metavar="<操作>")

    p = sub.add_parser("prepare", help="冻结输入并出任务书（不写目标 bib）")
    p.add_argument("--entry", default=_w.ENTRY_BIB, choices=[_w.ENTRY_BIB, _w.ENTRY_CONVERT],
                   help="入口类型：bib = 引用库 AI 处理 Bib；convert = 初排（默认 bib）")
    p.add_argument("--target-bib", required=True, metavar="PATH", help="目标 .bib（只读，不写）")
    p.add_argument("--bib-out", default="", metavar="PATH", help="另存为新的 Bib 文件")
    p.add_argument("--tex", default="", metavar="PATH", help="可选正文 .tex（算引用键闭合）")
    p.add_argument("--source", default="", metavar="PATH", help="可选源稿路径（只记指纹）")
    p.add_argument("--tex-out", default="", metavar="PATH",
                   help="正文可写目标（初排的交付候选）；默认空 = 正文只报告不写回")
    p.add_argument("--stem", default="", help="产物名（决定任务书文件名，默认 reference）")
    p.add_argument("--taskbook-version", type=int, choices=[1, 3], default=1)
    p.add_argument("--execute", default=None, metavar="A,B",
                   help=f"执行范围（默认 {'、'.join(_w.CATS)} 按 §4 缺省）")
    p.add_argument("--adopt", default=None, metavar="A,B", help="采纳范围（默认按 §4 缺省）")
    p.add_argument("--text", action="store_true", help="把「文字纠错」加进执行范围（默认关）")
    p.set_defaults(func=_op_prepare)

    p = sub.add_parser("request", help="直连 API 取结果（会产生费用；不自动采纳）")
    p.add_argument("--taskbook", default="", metavar="PATH", help="任务书路径（默认取 state 里那份）")
    p.add_argument("--taskbook-sha256", default=None, metavar="SHA256",
                   help="可选：发送前核对任务书文本的 SHA-256（UTF-8）")
    p.add_argument("--reply-out", default="", metavar="PATH", help="回填落盘路径（默认工作区内）")
    p.add_argument("--timeout", type=int, default=120, metavar="SEC")
    p.add_argument("--retry", action="store_true",
                   help="仅失败/取消/结果不明时显式重发一次（§9；成功不重发、进行中拒）")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="机器可读输出")
    p.set_defaults(func=_op_request)

    p = sub.add_parser("import", help="导入 Chat 回填（文件或标准输入正文）")
    p.add_argument("--reply", default="", metavar="PATH", help="回填文件路径")
    p.add_argument("--stdin", action="store_true", help="从标准输入读回填正文（粘贴）")
    p.add_argument("--task-id", default="", help="显式指定 TASK-ID（默认取 state 里那份）")
    p.add_argument("--source-kind", choices=["ai_subset", "complete_external"], default="ai_subset")
    p.add_argument("--replace-active", action="store_true", help="确认替换活动回复的来源")
    p.set_defaults(func=_op_import)

    p = sub.add_parser("preview", help="算出拟应用结果（不写目标 bib）")
    p.add_argument("--reply", default="", metavar="PATH", help="回填文件（默认取最近一次 import）")
    p.add_argument("--adopt", default=None, metavar="A,B", help="本次采纳范围（默认按 state 冻结值）")
    p.add_argument("--skip-item", action="append", default=[], help="不采纳该建议 ID，可重复")
    p.add_argument("--decide", action="append", default=[], metavar="CF=值",
                   help="字段级冲突裁决 cf-xxxxxxxx=library|reply|original，可重复")
    p.add_argument("--decide-json", default="", metavar="JSON",
                   help='一次性给多条裁决：{"cf-xxxxxxxx": "library"}')
    p.add_argument("--requery", action="store_true",
                   help="强制重新查库（默认复用本次预览的查库快照）")
    p.set_defaults(func=_op_preview)

    p = sub.add_parser("apply", help="校验后备份并写入目标 bib")
    p.add_argument("--preview-id", required=True, help="本次预览的 preview_id（过期会被拒）")
    p.add_argument("--confirm-from", default="", metavar="PATH",
                   help="要写入的 bib 文本（默认取本次预览的 bib 候选）")
    p.add_argument("--confirm-tex", default="", metavar="PATH",
                   help="要写入的正文候选（默认取本次预览的 tex 候选；只写 --tex-out）")
    p.set_defaults(func=_op_apply)

    p = sub.add_parser("undo", help="撤销本次 apply（当前文件须仍是应用后指纹）")
    p.set_defaults(func=_op_undo)

    p = sub.add_parser("status", help="现在到哪一步、还缺什么（不抛异常）")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p.set_defaults(func=_op_status)
    p = sub.add_parser("taskbook", help="R3 只读任务书全文（§10，核 task_id+SHA）")
    p.add_argument("--task-id", default="", help="要核对的 task-id（默认取 state）")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="机器可读输出（bridge 用）")
    p.set_defaults(func=_op_taskbook)
    p = sub.add_parser("deliver", help="R3 交付最终目录（采纳候选；use_original 不采纳）")
    p.add_argument("--preview-id", default="", help="预览编号（默认取 state）")
    p.add_argument("--use-original", action="store_true",
                   help="保留原 Bib，不采纳 AI 候选，仍走检查/交付")
    p.add_argument("--ai-enabled", action="store_true")
    p.add_argument("--compile-fix-enabled", action="store_true")
    for flag in ("--evidence-id", "--request-id", "--review-sha256"):
        p.add_argument(flag, default="")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="机器可读输出（bridge 用）")
    p.set_defaults(func=_op_deliver)
    from .compilefix.l2_r3 import ACTIONS

    p = sub.add_parser("compile-fix", help="逐稿编译修复；不会自动发送、采纳或交付")
    p.add_argument("action", choices=ACTIONS)
    p.add_argument("--preview-id", default="", help="预览编号（retained/delete 不需要）")
    p.add_argument("--record-key", default="", help="本地保留记录稳定键（仅 delete）")
    for flag in ("--evidence-id", "--request-id", "--request-sha256", "--authorization-id", "--review-sha256"):
        p.add_argument(flag, default="")
    p.add_argument("--compile-fix-terms-ack", action="store_true")
    # Bridge sends these flags as well as the explicit settings in stdin.
    p.add_argument("--ai-enabled", action="store_true")
    p.add_argument("--compile-fix-enabled", action="store_true")
    p.add_argument("--timeout", type=int)
    p.add_argument("--response-limit", type=int)
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p.set_defaults(func=_op_compile_fix)
    return ap


def main(argv: list[str] | None = None) -> int:
    """`convert.py --ai-workflow …` 的实现。返回 0 / 2（不抛异常给门面）。"""
    ap = build_parser()
    try:
        args = ap.parse_args(list(argv or []))
    except SystemExit as exc:                      # argparse 自己打了用法
        return int(exc.code or 0)
    try:
        payload = args.func(args)
    except _w.WorkflowError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return RC_FAIL
    except OSError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return RC_FAIL
    as_json = bool(getattr(args, "json", False))
    if as_json:
        _emit(payload, as_json=True, lines=[])
    elif args.op == "status":
        _emit(payload, as_json=False, lines=_status_lines(payload))
    else:
        _print_human(args.op, payload)
    # `status` 是**只读查询**：「还没有任何任务」是有效答复，不是失败（§9 续跑）。
    # 其余操作仍按 payload["ok"] 定退出码，失败绝不返回 0。
    if args.op == "status":
        return RC_OK
    return RC_OK if payload.get("ok", True) else RC_FAIL


def _print_import_branch(payload: dict) -> None:
    """C 包交接：把 `branch_hint` 与 `diagnostics` 读出来给人看。

    只**追加**新行（在既有 import 输出之后），既有输出行一字不改；没有这两个键
    （旧回件 / 非 R3）时静默不打印。
    """
    hint = str(payload.get("branch_hint") or "").strip()
    if hint:
        print(f"分支：{hint}")
    diags = payload.get("diagnostics") or []
    for item in diags:
        text = str(item or "").strip()
        if text:
            print(f"  诊断：{text}")
    errors = payload.get("errors") or []
    for item in errors:
        if isinstance(item, dict):
            where = str(item.get("line") or "").strip()
            msg = str(item.get("message") or item.get("code") or "").strip()
            if msg:
                print(f"  错误：{msg}" + (f"（第 {where} 行）" if where else ""))
        else:
            text = str(item or "").strip()
            if text:
                print(f"  错误：{text}")
    step = str(payload.get("next_step") or "").strip()
    if step:
        print(f"  建议下一步：{step}")


def _print_human(op: str, payload: dict) -> None:
    """人读输出：进度行在前、结论在后；失败不写成功字样。"""
    print(f"操作：{op}")
    if op == "prepare":
        print(f"工作区：{payload.get('work_dir', '')}")
        print(f"TASK-ID={payload.get('task_id', '')}")
        print(f"任务书：{payload.get('taskbook', '')}")
        print(f"条目 {payload.get('bib_keys', 0)} 条｜正文引用键 {payload.get('cite_keys', 0)} 个")
        print("下一步：把任务书全文交给 Chat（或用 request 直连 API），再 import。")
        return
    if op == "request":
        if payload.get("ok"):
            print(f"回填已落盘：{payload.get('reply_path', '')}（{payload.get('chars', 0)} 字）")
            print("下一步：preview（不自动采纳）。")
        else:
            print(f"请求失败：{payload.get('error', '')}")
            if payload.get("chars"):
                print(f"已取得 {payload['chars']} 字部分结果，保留在工作区；不自动重试。")
        return
    if op == "import":
        if not payload.get("ok") and "identity_status" in payload:
            print("回填拒收：" + "；".join(payload.get("notes") or ["回填无效。"]))
            _print_import_branch(payload)
            return
        if payload.get("confirmation_required"):
            print("；".join(payload.get("notes") or ["替换来源需要确认。"]))
            return
        if payload.get("foreign"):
            print(f"TASK-ID 不符（收到 {payload.get('task_id', '')}，本次是 "
                  f"{payload.get('expected_task_id', '')}）：整份按拒收处理。")
        elif "identity_status" in payload:
            counts = sum(len((payload.get(name) or {}).get("items") or [])
                         for name in ("typeset", "text"))
            identity = ("任务身份已核对" if payload["identity_status"] == "verified"
                        else "身份未验证")
            print(f"已导入（{identity}）｜结构化建议 {counts} 条")
        else:
            print(f"已导入：bib 块 {len(payload.get('bib_blocks') or [])} 个｜"
                  f"结构化建议 {len(payload.get('suggestions') or [])} 条")
        # 既有行全部打完才追加分支提示/诊断（既有输出行一字不改）。
        _print_import_branch(payload)
        print("下一步：preview（拟应用结果不写目标 bib）。")
        return
    if op == "preview":
        print(f"预览：{payload.get('preview_id', '')}｜phase={payload.get('phase', '')}")
        for line in (payload.get("bib") or {}).get("summary") or []:
            print(f"  {line}")
        for line in (payload.get("text") or {}).get("summary") or []:
            print(f"  {line}")
        # 「为什么这一类没采纳」必须自己说出来：指纹不符 / 采纳范围没勾 / 没有正文，
        # 都是**有效结论**，不能只在 JSON 里留一句 reason（人读输出会变成静默跳过）。
        for name in ("bib", "text"):
            why = str((payload.get(name) or {}).get("reason") or "").strip()
            if why:
                print(f"  {why}")
        blocked = payload.get("blocked") or []
        for item in blocked:
            print(f"  阻断：{item}")
        if payload.get("ok"):
            print("下一步：apply --preview-id " + str(payload.get("preview_id", "")))
        else:
            print("这份预览不能应用（有阻断项）：先解决上面列出的问题。")
        return
    if op == "apply":
        # 分清「真写了文件」与「什么都没写」：没写就说没写，不许含糊成「已写入」。
        writes = payload.get("writes") or []
        if payload.get("wrote"):
            for w in writes or [{"target": payload.get("target", ""),
                                 "backup": payload.get("backup", "")}]:
                print(f"已写入：{w.get('target', '')}（{w.get('kind', 'bib')}）")
                if w.get("backup"):
                    print(f"备份：{w['backup']}")
            print("可用 undo 撤销本次写入。")
        else:
            print("本次没有写入任何文件：" + str(payload.get("note") or "没有可写的候选。"))
            print("没有写入就没有「已应用」——重新 preview 或检查采纳范围。")
        return
    if op == "undo":
        print(f"已撤销：{payload.get('target', '')}（恢复到 {payload.get('restored_from', '')}）")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":                        # 便于单测直接跑
    raise SystemExit(main())
