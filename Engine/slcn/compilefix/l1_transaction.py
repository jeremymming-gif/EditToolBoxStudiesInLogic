"""L1 candidate isolation and recoverable publishing using Stage A's inode-safe journal."""
from dataclasses import asdict
import io
import os
from pathlib import Path
import time

from . import atomicfiles
from .compiler import (ARTIFACT_LIMIT, LOG_LIMIT, compile_candidate, copy_support, support_manifest)
from .files import (RepairError, atomic_write, digest, directory, directory_id, put_json,
                    read, read_json, regular, stem_name, sync_directory)
from .state import capture_compile, target_key
from .transaction import (_backup, _check_directory, _file_hash, _guard, _guard_path,
                          _output_lock, _publish, _remove_stage, _rollback, _stage_path)

RETENTION_SECONDS = 30 * 24 * 60 * 60


class RecoveryRequired(RepairError):
    """An interrupted/externally changed output must never be reported as success."""


class _LocalStore:
    def __init__(self, root):
        self.root = root

    def task_dir(self, task_id):
        if len(task_id) != 64 or any(c not in "0123456789abcdef" for c in task_id):
            raise RecoveryRequired("L1 恢复身份无效")
        return directory(self.root / task_id, private=True)

    def save(self, task_id, state):
        put_json(self.task_dir(task_id) / "state.json", state)


def _store(out_dir, stem, source):
    from .state import _overlap
    root = out_dir.parent / (".compilefix-l1-" + target_key(out_dir, stem))
    # Reuse Stage A provenance separation before any directory creation.
    if _overlap(root, Path(source).parent):
        raise RepairError("L1 恢复区与源目录重叠")
    return _LocalStore(directory(root, create=True, private=True))


def _finish_pending(store, task_id, out_dir, state):
    """Reconcile only an unambiguous interrupted native operation before rollback."""
    path = store.task_dir(task_id) / "journal.json"
    journal = read_json(path)
    pending = journal.get("pending_replacement")
    if pending is None:
        return
    filename = pending.get("file")
    if filename not in journal["files"]:
        raise RecoveryRequired("L1 中断记录文件无效")
    stage = directory(_stage_path(out_dir, journal), private=True)
    if directory_id(stage) != journal.get("stage_id"):
        raise RecoveryRequired("L1 中断目录身份不符")
    retained = pending.get("retained")
    incoming = pending.get("incoming")
    import re
    for name in (retained, incoming):
        if name is not None and not re.fullmatch(r"(?:retained|incoming)-[0-9a-f]{32}", name):
            raise RecoveryRequired("L1 中断记录路径无效")
    current = _file_hash(out_dir / filename)
    expected, desired = pending["expected_sha256"], pending["desired_sha256"]
    displaced = _file_hash(stage / retained) if retained else None
    if current == desired and displaced == expected:
        if retained:
            journal.setdefault("displaced", []).append({"file": filename, "retained": retained,
                                                        "expected_sha256": expected})
    elif current == expected and (not retained or displaced == desired):
        pass  # Native operation had not happened. Original target still intact.
    else:
        raise RecoveryRequired("L1 中断时有外部修改；已保留原 inode 与恢复材料")
    journal.pop("pending_replacement")
    put_json(path, journal)


def recover(out_dir, stem, *, source):
    """Before another compiler pass, refuse or restore an interrupted L1 publication."""
    out_dir = directory(out_dir)
    marker = read_json(_guard_path(out_dir, stem_name(stem)), optional=True)
    if marker is None:
        return
    store = _store(out_dir, stem, source)
    task_id = marker.get("task_id", "")
    if marker != _guard(store, task_id):
        raise RecoveryRequired("产物有其他未完成修复事务；请先人工恢复")
    state = read_json(store.task_dir(task_id) / "state.json")
    with _output_lock(out_dir, stem):
        _check_directory(out_dir, state)
        _finish_pending(store, task_id, out_dir, state)
        _rollback(store, task_id, state, out_dir, stem)
        state.update(state="rolled_back", rollback="recovered")
        store.save(task_id, state)


def _pdf_readable(data):
    if not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
        return False
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=True)
        return not reader.is_encrypted and len(reader.pages) > 0
    except Exception:
        return False


def apply_plan(out_dir, stem, *, source, capture, plan, report, compiler=None, checkpoint=None):
    """All automatic edits stay isolated until compile, reader and hash gates pass.

    Full local candidates/logs/backups live in a private sibling recovery directory
    for at most 30 days (purged on the next L1 run). They may be manually removed
    after checking state.json; unfinished transactions are never purged.
    """
    compiler = compile_candidate if compiler is None else compiler
    checkpoint = (lambda phase: None) if checkpoint is None else checkpoint
    out_dir = directory(out_dir)
    stem_name(stem)
    if capture_compile(out_dir, stem, source=source) != {k: capture[k] for k in
            ("candidate_sha256", "candidate_id", "source_id", "out_id") if k in capture}:
        raise RepairError("L1 冻结前候选或来源变化")
    if plan.reverse(plan.patched) != read(out_dir / f"{stem}.tex"):
        raise RepairError("L1 原候选与规则指纹不符")
    store = _store(out_dir, stem, source)
    # Bounded retention only within this target's private L1 root.
    for child in store.root.iterdir():
        directory(child, private=True)
        old = read_json(child / "state.json", optional=True)
        if (old and old.get("state") in {"completed", "failed", "rolled_back"}
                and old.get("expires_at", float("inf")) <= time.time()):
            import shutil
            archive = out_dir / ".compilefix-displaced" / child.name
            try:
                directory(archive, private=True)
                record = read_json(archive / "journal.json")
            except FileNotFoundError:
                pass
            else:
                if record.get("task_id") != child.name:
                    raise RecoveryRequired("L1 过期恢复索引身份不符")
                shutil.rmtree(archive)
            shutil.rmtree(child)
    import uuid
    task_id = digest((report.identity + uuid.uuid4().hex).encode("ascii"))
    task_dir = directory(store.root / task_id, create=True, private=True)
    state = {"identity": {"task_id": task_id, "diagnostic_id": report.identity},
             "state": "isolating", "expires_at": time.time() + RETENTION_SECONDS,
             "provenance": {"out_id": directory_id(out_dir)}, "rule": plan.rule_id,
             "candidate_sha256": plan.before_sha256, "repaired_sha256": plan.after_sha256,
             "byte_spans": [[plan.start, plan.start], [plan.end, plan.end]], "rollback": "not-needed"}
    store.save(task_id, state)
    published = False
    with _output_lock(out_dir, stem) as lease:
        try:
            if read_json(_guard_path(out_dir, stem), optional=True) is not None:
                raise RecoveryRequired("产物有未完成修复事务")
            support = support_manifest(out_dir, stem)
            original_pdf = read(out_dir / f"{stem}.pdf", ARTIFACT_LIMIT, optional=True)
            original_pdf_hash = None if original_pdf is None else digest(original_pdf)
            state["original_pdf_sha256"] = original_pdf_hash
            state["dependencies"] = support
            isolated = directory(task_dir / "candidate", create=True, private=True)
            copy_support(out_dir, isolated, support)
            atomic_write(isolated / f"{stem}.tex", plan.patched)
            checkpoint("applied")
            result = compiler(isolated, stem, plan.after_sha256, support, task_dir, leases=(lease,))
            state["compile"] = asdict(result)
            store.save(task_id, state)
            if result.state != "passed":
                raise RepairError("L1 隔离复编未通过")
            pdf = read(isolated / f"{stem}.pdf", ARTIFACT_LIMIT)
            log = read(isolated / f"{stem}.log", LOG_LIMIT)
            recorder = read(isolated / f"{stem}.fls", LOG_LIMIT)
            if (not _pdf_readable(pdf) or digest(pdf) != result.pdf_sha256
                    or digest(log) != result.log_sha256 or digest(recorder) != result.recorder_sha256
                    or digest(read(isolated / f"{stem}.tex")) != plan.after_sha256
                    or support_manifest(isolated, stem) != support):
                raise RepairError("L1 PDF/日志/依赖无法绑定本轮候选")
            checkpoint("verified")
            if (capture_compile(out_dir, stem, source=source) != {k: capture[k] for k in
                    ("candidate_sha256", "candidate_id", "source_id", "out_id")}
                    or support_manifest(out_dir, stem) != support
                    or _file_hash(out_dir / f"{stem}.pdf") != original_pdf_hash):
                raise RepairError("L1 复编期间原候选/PDF/依赖被外部修改")
            atomicfiles.preflight(out_dir, probe_dir=task_dir)
            journal = _backup(out_dir, stem, task_dir, plan.original, plan.patched)
            if journal["files"][f"{stem}.pdf"] is None:
                backed_pdf_hash = None
            else:
                backed_pdf_hash = journal["files"][f"{stem}.pdf"]["sha256"]
            if backed_pdf_hash != original_pdf_hash:
                raise RepairError("L1 备份期间原 PDF 被修改")
            journal_path = task_dir / "journal.json"
            stage = _stage_path(out_dir, journal)
            stage.mkdir(mode=0o700)
            journal["stage_id"] = directory_id(stage)
            put_json(journal_path, journal)
            state["state"] = "publishing"
            store.save(task_id, state)
            put_json(_guard_path(out_dir, stem), _guard(store, task_id))
            published = True
            for filename, data in ((f"{stem}.tex", plan.patched), (f"{stem}.pdf", pdf)):
                _publish(out_dir, filename, data, journal, journal_path)
                checkpoint("published-" + filename.rsplit(".", 1)[-1])
            if (read(out_dir / f"{stem}.tex") != plan.patched
                    or digest(read(out_dir / f"{stem}.pdf", ARTIFACT_LIMIT)) != result.pdf_sha256
                    or support_manifest(out_dir, stem, exclude_dir=stage) != support):
                raise RepairError("L1 提交后指纹不符")
            state.update(state="completed", rollback="not-needed", pdf_sha256=result.pdf_sha256,
                         gates={"compile": True, "local_invariants": True, "publication": True})
            store.save(task_id, state)
            # Keep the stage and displaced inodes for recoverability until commit marker removal.
            # Stage A's cleanup preserves displaced inodes in its restricted archive.
            _remove_stage(out_dir, journal, state)
            store.save(task_id, state)
            _guard_path(out_dir, stem).unlink()
            sync_directory(out_dir)
            return str(out_dir / f"{stem}.pdf")
        except BaseException:
            if published:
                try:
                    _finish_pending(store, task_id, out_dir, state)
                    _rollback(store, task_id, state, out_dir, stem)
                    state.update(state="rolled_back", rollback="restored")
                except (OSError, RepairError) as exc:
                    state.update(state="recovery_required", rollback="blocked")
                    store.save(task_id, state)
                    raise RecoveryRequired("L1 回滚未完成；已保留受限恢复材料，禁止交付") from exc
            else:
                state.update(state="failed", rollback="original-untouched")
            store.save(task_id, state)
            raise
