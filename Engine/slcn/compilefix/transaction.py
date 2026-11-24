"""Isolated verification and journaled in-place publishing of repaired TeX."""

from contextlib import contextmanager
from dataclasses import asdict
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
import time

from . import atomicfiles
from .compiler import (COMPILE_EXTENSIONS, DISPLACED_DIR, SUPPORT_LIMIT, compile_candidate,
                       copy_support, support_manifest)
from .files import (ARTIFACT_LIMIT, RepairError, atomic_write, digest, directory,
                    directory_id, lock, put_json, read, read_json, regular, sync_directory)
from .state import ACTIVE, local_actor


CHECK_NOTE = "AI交付检查说明.md"
COMPILE_INPUT_EXTENSIONS = {".aux", ".bbl", ".bcf", ".out", ".toc", ".lof", ".lot", ".nav", ".snm"}


def _marker_path(out_dir, stem):
    return out_dir / f".compilefix-{stem}.protection.json"


def _guard_path(out_dir, stem):
    return out_dir / f".compilefix-{stem}.transaction.json"


@contextmanager
def _output_lock(out_dir, stem):
    directory(out_dir)
    if os.name == "nt":
        with lock(out_dir / f".compilefix-{stem}.lock") as lease:
            yield lease
    else:
        with lock(out_dir, directory_lock=True) as lease:
            yield lease


def _check_directory(out_dir, state):
    if directory_id(out_dir) != state["provenance"]["out_id"]:
        raise RepairError("产物目录身份变化，需要人工核查")


def _guard(store, task_id):
    return {"task_id": task_id, "state_root_sha256": digest(os.fsencode(store.root))}


def _require_available_output(store, out_dir, stem):
    path = _guard_path(out_dir, stem)
    existing = read_json(path, optional=True)
    if existing is None:
        return
    owner = existing.get("task_id", "")
    if existing == _guard(store, owner):
        owner_state = store.load(owner)
        _check_directory(out_dir, owner_state)
        if owner_state["state"] == "completed":
            path.unlink()
            sync_directory(out_dir)
            return
    raise RepairError("本产物目录有未恢复的修复事务")


def mark_output(store, task_id, state, action, actor):
    out_dir, stem = Path(state["out_dir"]), state["stem"]
    with _output_lock(out_dir, stem):
        _require_available_output(store, out_dir, stem)
        if state["state"] == "completed" and action in {"confirmed", "frozen"}:
            raw = store.validate_target(out_dir, stem, state["provenance"])
            expected = state["runs"]["output"]["candidate_sha256"]
            if digest(raw) != expected:
                raise RepairError("已修复候选在标记前发生变化")
        elif state["state"] in ACTIVE:
            expected = digest(store.candidate(state))
        else:
            raise RepairError("当前状态不能标记或重新开放")
        path = _marker_path(out_dir, stem)
        record = {"actor": local_actor(actor), "timestamp": time.time(), "source_path": str(path),
                  "writer": f"Engine/compilefix.py mark {action}", "task_id": task_id,
                  "candidate_sha256": expected, "out_id": state["provenance"]["out_id"]}
        if action == "reopen":
            marker = read_json(path, optional=True)
            if marker is None or marker.get("kind") not in {"confirmed", "frozen"}:
                raise RepairError("此产物没有需重新开放的保护标记")
            marker["reopen"] = record
        else:
            marker = dict(record, kind=action)
        put_json(path, marker)
        state["protection"] = marker
        store.save(task_id, state)


def _consume_reopen(store, task_id, state, out_dir, stem):
    path = _marker_path(out_dir, stem)
    marker = read_json(path, optional=True)
    if marker is None:
        if state.get("protection"):
            raise RepairError("保护标记遗失，需要人工核查")
        return
    if marker.get("kind") not in {"confirmed", "frozen"} or marker.get("out_id") != state["provenance"]["out_id"]:
        raise RepairError("无法验证产物保护标记")
    consent = marker.get("reopen")
    if (type(consent) is not dict or consent.get("task_id") != task_id
            or consent.get("candidate_sha256") != state["identity"]["candidate_sha256"]
            or consent.get("out_id") != state["provenance"]["out_id"]):
        raise RepairError("产物已确认或冻结，请显式重新开放本任务与候选")
    marker["last_consumed_reopen"] = marker.pop("reopen")
    put_json(path, marker)
    state["protection"] = marker
    store.save(task_id, state)


def _files(stem):
    return [f"{stem}.tex", *(stem + extension for extension in COMPILE_EXTENSIONS), CHECK_NOTE]


def _backup(out_dir, stem, task_dir, original, patched):
    backup = directory(task_dir / "rollback", create=True, private=True)
    originals = {}
    total = 0
    for index, filename in enumerate(_files(stem)):
        path = out_dir / filename
        info = regular(path, optional=True)
        originals[filename] = None
        if info is None:
            continue
        content = read(path, ARTIFACT_LIMIT)
        total += len(content)
        if total > SUPPORT_LIMIT:
            raise RepairError("原产物超过回滚上限")
        name = f"{index}.bak"
        atomic_write(backup / name, content)
        originals[filename] = {"name": name, "sha256": digest(content),
                               "mode": stat.S_IMODE(info.st_mode)}
    if originals[f"{stem}.tex"]["sha256"] != digest(original):
        raise RepairError("备份期间原候选发生变化")
    journal = {"files": originals, "owned": {f"{stem}.tex": [digest(patched)]},
               "stage": f"compilefix-run-{uuid.uuid4().hex}"}
    put_json(task_dir / "journal.json", journal)
    return journal


def _file_hash(path):
    data = read(path, ARTIFACT_LIMIT, optional=True)
    return None if data is None else digest(data)


def _original_hash(journal, filename):
    record = journal["files"][filename]
    return None if record is None else record["sha256"]


def _stage_path(out_dir, journal):
    name = journal.get("stage")
    prefix = "compilefix-run-"
    if (type(name) is not str or not name.startswith(prefix) or len(name) != len(prefix) + 32
            or any(char not in "0123456789abcdef" for char in name[len(prefix):])):
        raise RepairError("事务复编目录记录无效")
    return out_dir / name


def _replace_checked(out_dir, filename, previous, data, mode, journal, journal_path):
    """Install at the target atomically and retain the inode actually displaced."""
    if "pending_replacement" in journal:
        raise RepairError("已有未完成的产物替换，请核查受限事务日志")
    stage = directory(_stage_path(out_dir, journal), private=True)
    if directory_id(stage) != journal.get("stage_id"):
        raise RepairError("事务复编目录身份变化，需要人工核查")
    token = uuid.uuid4().hex
    retained = stage / f"retained-{token}"
    if previous is not None:
        operation = "exchange" if data is not None else "remove"
    else:
        operation = "create"
    incoming = retained if operation == "exchange" else stage / f"incoming-{token}"
    target = out_dir / filename
    desired = None if data is None else digest(data)
    if data is not None:
        atomic_write(incoming, data, mode=mode)
    journal["pending_replacement"] = {
        "operation": operation,
        "file": filename,
        "retained": retained.name if previous is not None else None,
        "incoming": incoming.name if data is not None else None,
        "expected_sha256": previous,
        "desired_sha256": desired,
    }
    put_json(journal_path, journal)
    if operation == "exchange":
        atomicfiles.exchange(target, incoming)
    elif operation == "remove":
        os.rename(target, retained)
    else:
        # Only an initially absent target is installed by link, without clobbering a new writer.
        os.link(incoming, target)
        sync_directory(out_dir)
        incoming.unlink()
    sync_directory(stage)
    sync_directory(out_dir)
    if previous is not None and _file_hash(retained) != previous:
        raise RepairError("实际离位的产物由外部更新，已保留在受限事务目录")
    if _file_hash(target) != desired:
        raise RepairError("原子操作后的产物被另行修改，已保留受限事务材料")
    committed = {key: value for key, value in journal.items() if key != "pending_replacement"}
    if previous is not None:
        entry = {"file": filename, "retained": retained.name, "expected_sha256": previous}
        committed["displaced"] = [*journal.get("displaced", ()), entry]
    put_json(journal_path, committed)
    journal.clear()
    journal.update(committed)


def _archive_displaced(out_dir, stage, journal, state):
    entries = journal.get("displaced", [])
    if type(entries) is not list or any(
        type(entry) is not dict or type(entry.get("retained")) is not str
        or not re.fullmatch(r"retained-[0-9a-f]{32}", entry["retained"])
        or type(entry.get("file")) is not str
        or entry.get("file") not in journal["files"]
        for entry in entries
    ):
        raise RepairError("产物恢复索引无效")
    if not entries:
        return
    task_id = state["identity"]["task_id"]
    archive_root = directory(out_dir / DISPLACED_DIR, create=True, private=True)
    archive = directory(archive_root / task_id, create=True, private=True)
    index = archive / "journal.json"
    record = {"task_id": task_id, "stage": stage.name, "displaced": entries}
    if read_json(index, optional=True) != record:
        put_json(index, record)
    state["displaced_journal"] = str(index)
    for entry in entries:
        retained = stage / entry["retained"]
        archived = archive / retained.name
        if regular(retained, optional=True) is not None:
            if regular(archived, optional=True) is not None:
                raise RepairError("产物恢复目录已有同名文件，已保留双方")
            # Keep the original inode linked, including writes through an editor's open FD.
            os.rename(retained, archived)
    sync_directory(archive)
    sync_directory(archive_root)
    sync_directory(stage)
    sync_directory(out_dir)
    if any(_file_hash(archive / entry["retained"]) != entry["expected_sha256"] for entry in entries):
        raise RepairError("移走的产物仍被外部写入，已保留恢复索引与受限备份")


def _remove_stage(out_dir, journal, state):
    stage = _stage_path(out_dir, journal)
    try:
        identity = directory_id(stage)
    except FileNotFoundError:
        return
    if identity != journal.get("stage_id"):
        raise RepairError("事务复编目录身份变化，需要人工核查")
    _archive_displaced(out_dir, stage, journal, state)
    shutil.rmtree(stage)
    sync_directory(out_dir)


def _rollback(store, task_id, state, out_dir, stem):
    task_dir = store.task_dir(task_id)
    journal_path = task_dir / "journal.json"
    journal = read_json(journal_path, optional=True)
    guard_path = _guard_path(out_dir, stem)
    marker = read_json(guard_path, optional=True)
    if journal is not None and "pending_replacement" in journal:
        raise RepairError("产物替换中断或发生冲突，请按受限事务日志恢复")
    if marker is None:
        return  # The journal was prepared but the transaction never touched output.
    if marker != _guard(store, task_id) or journal is None:
        raise RepairError("事务标记与回滚记录无法绑定，需要人工恢复")
    _check_directory(out_dir, state)
    files, owned = journal.get("files"), journal.get("owned")
    if (type(files) is not dict or set(files) != set(_files(stem)) or type(owned) is not dict
            or owned.keys() - files.keys() or any(type(values) is not list or not values for values in owned.values())):
        raise RepairError("原产物回滚记录不完整")
    backup = directory(task_dir / "rollback", private=True)
    restored = {}
    for index, (filename, item) in enumerate((name, files[name]) for name in _files(stem)):
        if item is not None:
            if type(item) is not dict or item.get("name") != f"{index}.bak" or type(item.get("mode")) is not int:
                raise RepairError("原产物备份记录无效")
            data = read(backup / item["name"], ARTIFACT_LIMIT)
            if digest(data) != item.get("sha256"):
                raise RepairError("原产物备份指纹不符，需要人工恢复")
            restored[filename] = data
    conflicts = []
    for filename, item in files.items():
        path = out_dir / filename
        try:
            current = _file_hash(path)
            if current == _original_hash(journal, filename):
                continue
            if current not in owned.get(filename, ()):
                conflicts.append(filename)
                continue
            _replace_checked(out_dir, filename, current, restored.get(filename),
                             item["mode"] if item else 0o600, journal, journal_path)
        except (OSError, RepairError):
            conflicts.append(filename)
            if "pending_replacement" in journal:
                break
    if conflicts:
        raise RepairError("事务期间产物被另行修改或无法安全读取，已保留现有文件及受限备份")
    _remove_stage(out_dir, journal, state)
    guard_path.unlink()
    sync_directory(out_dir)


def _publish(out_dir, filename, data, journal, journal_path, *, after_cleanup=False):
    path = out_dir / filename
    original = _original_hash(journal, filename)
    expected = journal["owned"].get(filename, [original])
    previous = expected[-1] if after_cleanup else original
    if _file_hash(path) != previous:
        raise RepairError("发布前产物被另行修改，需要人工核查")
    desired = None if data is None else digest(data)
    if desired == previous:
        return
    if filename not in journal["owned"] or desired != expected[-1]:
        journal["owned"].setdefault(filename, []).append(desired)
    item = journal["files"][filename]
    _replace_checked(out_dir, filename, previous, data, item["mode"] if item else 0o600,
                     journal, journal_path)


def _clear_backup(task_dir):
    directory(task_dir, private=True)
    backup = task_dir / "rollback"
    if backup.is_dir():
        shutil.rmtree(directory(backup, private=True))
    journal = task_dir / "journal.json"
    if regular(journal, optional=True) is not None:
        journal.unlink()


def recover_transaction(store, task_id, state):
    out_dir, stem = Path(state["out_dir"]), state["stem"]
    task_dir = store.task_dir(task_id)
    if regular(task_dir / "journal.json", optional=True) is not None:
        atomicfiles.preflight(out_dir, probe_dir=task_dir)
        with _output_lock(out_dir, stem):
            _check_directory(out_dir, state)
            try:
                _rollback(store, task_id, state, out_dir, stem)
            except (OSError, RepairError):
                state.update(failure_phase="rollback", pid=None)
                store.save(task_id, state)
                raise
    state.update(state="unknown", failure_phase="interrupted", pid=None)
    store.save(task_id, state)
    _clear_backup(task_dir)


def _review_report(state, book, actions):
    lines = ["\n\n## 编译修复", "", f"- 任务 ID：`{book['identity']['task_id']}`",
             f"- 任务书 SHA-256：`{state['taskbook_sha256']}`",
             f"- 回件 SHA-256：`{state['reply_sha256']}`",
             f"- 完整差异 SHA-256：`{state['review_sha256']}`（本地受限状态区审阅）",
             "- 人工审阅：操作者确认并采纳上述差异；关键版面待人工验收。",
             "- 交付状态：产物目录复编通过；最终交付确认待记录。"]
    for key, label in (("isolated", "隔离候选"), ("output", "产物目录")):
        run = state["runs"][key]
        lines.extend([f"- {label}编译：{run['state']}，运行 ID `{run['run_id']}`",
                      f"  - 候选 SHA-256：`{run['candidate_sha256']}`",
                      f"  - PDF SHA-256：`{run['pdf_sha256']}`",
                      f"  - 编译日志 SHA-256：`{run['log_sha256']}`",
                      f"  - 编译文件记录 SHA-256：`{run['recorder_sha256']}`"])
    evidence = {item["evidence_id"]: item for item in book["evidence"]}
    for action in actions:
        evidence_id = action["evidence_id"]
        diagnostic = book["diagnostics"][evidence[evidence_id]["diagnostic_index"]]
        decision = "采纳正文替换" if action["action"] == "edit" else "人工排查（仅报告）"
        lines.append(f"- {evidence_id} · {decision} · {diagnostic['summary']} · "
                     f"候选行 {diagnostic['source_line']} · 日志行 {diagnostic['log_line']}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _failed(store, task_id, state, result):
    state.update(state="compile_failed" if result.state == "failed" else "unknown",
                 failure_phase=result.phase, pid=None)
    store.save(task_id, state)
    raise RepairError(f"复编未通过（{result.phase}），请人工查看本地诊断")


def apply(store, task_id, fingerprint, lease):
    state = store.load(task_id)
    store.recover(task_id, state)
    original, repaired, diff, book, actions = store.review(task_id, state)
    if not diff or digest(diff) != fingerprint:
        raise RepairError("请审阅并明确确认完整差异的 SHA-256")
    if state.get("reviewed_sha256") != fingerprint:
        raise RepairError("请先运行 review 查看完整差异")
    patched_hash = digest(repaired)
    out_dir, stem = Path(state["out_dir"]), state["stem"]
    task_dir = store.task_dir(task_id)
    atomicfiles.preflight(out_dir, probe_dir=task_dir)
    with _output_lock(out_dir, stem) as output_lease:
        _check_directory(out_dir, state)
        _require_available_output(store, out_dir, stem)
        _consume_reopen(store, task_id, state, out_dir, stem)
        state.update(state="applied", pid=os.getpid(), runs={}, private_review="accepted")
        store.save(task_id, state)
        _execute(store, task_id, state, task_dir, out_dir, stem, original, repaired,
                 patched_hash, book, actions, (lease, output_lease))


def _execute(store, task_id, state, task_dir, out_dir, stem, original, repaired,
             patched_hash, book, actions, leases):
    phase = "isolation"
    try:
        support = support_manifest(out_dir, stem)
        with tempfile.TemporaryDirectory(prefix="candidate-", dir=task_dir) as workspace:
            isolated = Path(workspace)
            copy_support(out_dir, isolated, support)
            tex = isolated / f"{stem}.tex"
            atomic_write(tex, original)
            if digest(read(tex)) != book["identity"]["candidate_sha256"]:
                raise RepairError("隔离候选身份与任务书不符")
            atomic_write(tex, repaired)
            result = compile_candidate(isolated, stem, patched_hash, support, task_dir, leases=leases)
            state["runs"]["isolated"] = asdict(result)
            if result.state != "passed":
                _failed(store, task_id, state, result)
        phase = "prepare_closeout"
        if store.candidate(state) != original or support_manifest(out_dir, stem) != support:
            raise RepairError("产物或复编支持文件在隔离复编期间变化")
        journal = _backup(out_dir, stem, task_dir, original, repaired)
        journal_path = task_dir / "journal.json"
        guard_path = _guard_path(out_dir, stem)
        put_json(guard_path, _guard(store, task_id))
        phase = "closeout"
        stage = _stage_path(out_dir, journal)
        stage.mkdir(mode=0o700)
        journal["stage_id"] = directory_id(stage)
        put_json(journal_path, journal)
        tex_name = f"{stem}.tex"
        _publish(out_dir, tex_name, repaired, journal, journal_path)
        for extension in sorted(COMPILE_INPUT_EXTENSIONS):
            _publish(out_dir, stem + extension, None, journal, journal_path)
        result = compile_candidate(out_dir, stem, patched_hash, support, task_dir,
                                   leases=leases, artifact_dir=stage)
        state["runs"]["output"] = asdict(result)
        if result.state != "passed":
            state["failure_phase"] = result.phase
            _rollback(store, task_id, state, out_dir, stem)
            _failed(store, task_id, state, result)
        phase = "bind_closeout"
        final = store.validate_target(out_dir, stem, state["provenance"])
        if (digest(final) != patched_hash or support_manifest(out_dir, stem, exclude_dir=stage) != support
                or directory_id(stage) != journal["stage_id"]):
            raise RepairError("关闭事务前产物身份发生变化")
        expected = {".pdf": result.pdf_sha256, ".log": result.log_sha256, ".fls": result.recorder_sha256}
        for extension in COMPILE_EXTENSIONS:
            filename = stem + extension
            data = read(stage / filename, ARTIFACT_LIMIT, optional=True)
            if extension in expected and (data is None or digest(data) != expected[extension]):
                raise RepairError("产物发布前本轮编译指纹发生变化")
            _publish(out_dir, filename, data, journal, journal_path, after_cleanup=True)
        previous = read(out_dir / CHECK_NOTE, optional=True) or b""
        _publish(out_dir, CHECK_NOTE, previous + _review_report(state, book, actions), journal, journal_path)
        for filename in journal["files"]:
            expected_hash = journal["owned"].get(filename, [_original_hash(journal, filename)])[-1]
            if _file_hash(out_dir / filename) != expected_hash:
                raise RepairError("事务完成前产物被另行修改，需要人工核查")
        _remove_stage(out_dir, journal, state)
        state.update(state="completed", pid=None)
        store.save(task_id, state)
    except (OSError, RepairError, KeyboardInterrupt):
        if state["state"] == "applied" or state["state"] == "completed":
            try:
                _check_directory(out_dir, state)
                _rollback(store, task_id, state, out_dir, stem)
            except (OSError, RepairError) as exc:
                state.update(state="applied", failure_phase="rollback", pid=None)
                store.save(task_id, state)
                raise RepairError("回滚未完成，已保留受限备份与事务标记；请人工核查") from exc
            state.update(state="unknown", failure_phase=phase, pid=None)
            store.save(task_id, state)
        raise
    else:
        guard_path.unlink()
        sync_directory(out_dir)
    finally:
        if state["state"] != "applied":
            _clear_backup(task_dir)
