"""Manual task lifecycle, provenance and output protection."""

from contextlib import contextmanager
import getpass
import math
import os
from pathlib import Path
import shutil
import sys
import time

from .compiler import LOG_LIMIT
from .files import (ID_PATTERN, REPLY_LIMIT, RepairError, absolute, atomic_write, decode_json,
                    digest, directory, directory_ancestry, directory_id, json_bytes,
                    lock, put_json, read, read_json, regular, stem_name, within)
from .model import diagnostics, edit_candidate, parse_reply, taskbook


STATES = ("not_requested", "exported", "imported", "applied", "compile_failed",
          "completed", "cancelled", "expired", "unknown")
ACTIVE = {"exported", "imported"}
DEFAULT_ROUNDS = 1
MAX_ROUNDS = 3
RETENTION_SECONDS = 30 * 24 * 60 * 60


def state_root(value=None):
    return absolute(value or os.environ.get("SLCN_COMPILEFIX_STATE_DIR")
                    or Path.home() / ".editorial-toolbox" / "compilefix")


def validate_id(task_id):
    if type(task_id) is not str or not ID_PATTERN.fullmatch(task_id):
        raise RepairError("任务 ID 必须是 SHA-256 十六进制值")
    return task_id


def local_actor(actor=None):
    if actor is not None:
        if not actor.strip() or len(actor) > 128 or any(not char.isprintable() for char in actor):
            raise RepairError("操作者标记无效")
        return actor
    return f"uid:{os.getuid()}" if hasattr(os, "getuid") else f"user:{getpass.getuser()}"


def _alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) terminates processes on Windows. Query using Win32 APIs.
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(wintypes.HANDLE(handle), ctypes.byref(code))
                        and code.value == 259)
        finally:
            kernel.CloseHandle(wintypes.HANDLE(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def target_key(out_dir, stem):
    return digest(json_bytes([os.path.normcase(str(out_dir)), os.path.normcase(stem)]))


def _validate_source(source, out_dir):
    source = absolute(source)
    if _overlap(out_dir, source.parent):
        raise RepairError("产物与源目录重叠，拒绝修复")
    directory(source.parent)
    info = regular(source)
    return source, [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size]


def _overlap(first, second):
    lexical_first, lexical_second = first, second
    if sys.platform == "darwin":
        lexical_first = Path(str(first).casefold())
        lexical_second = Path(str(second).casefold())
    if within(lexical_first, lexical_second) or within(lexical_second, lexical_first):
        return True
    first_ancestors, first_id = directory_ancestry(first)
    second_ancestors, second_id = directory_ancestry(second)
    return first_id in second_ancestors or second_id in first_ancestors


def _is_hash(value):
    return type(value) is str and ID_PATTERN.fullmatch(value) is not None


def _numbers(value, length):
    return type(value) is list and len(value) == length and all(type(item) is int for item in value)


def _validate_provenance(record):
    if (type(record) is not dict or not _numbers(record.get("out_id"), 2)
            or not _is_hash(record.get("candidate_sha256"))):
        raise RepairError("本地产物来源记录无效")
    if record.get("issuer") == "Engine/convert.py":
        if type(record.get("source")) is str and _numbers(record.get("source_id"), 4):
            run = record.get("diagnostic_run")
            if run is not None and (type(run) is not dict or not _is_hash(run.get("transcript_sha256"))
                                    or run.get("candidate_sha256") != record["candidate_sha256"]
                                    or type(run.get("run_id")) is not str or len(run["run_id"]) != 32):
                raise RepairError("本地首编诊断绑定无效")
            return record
    elif record.get("issuer") == "compilefix export --synthetic" and record.get("source") is None:
        return record
    raise RepairError("本地产物来源记录无效")


def _validate_task_record(record, task_id, *, tombstone=False):
    identity = record.get("identity")
    expiry = record.get("expires_at")
    if (type(identity) is not dict or identity.get("task_id") != task_id
            or record.get("state") not in (STATES[6:8] if tombstone else STATES[1:])
            or (expiry is not None and (type(expiry) not in (int, float) or not math.isfinite(expiry)))
            or (not tombstone and (expiry is None or not _is_hash(record.get("taskbook_sha256"))
                                   or not _is_hash(identity.get("candidate_sha256"))
                                   or type(record.get("out_dir")) is not str
                                   or type(record.get("stem")) is not str))):
        raise RepairError("本地任务状态记录无效")
    if not tombstone:
        stem_name(record["stem"])
        _validate_provenance(record.get("provenance"))
        for key in ("authorization", "protection", "runs"):
            if key in record and type(record[key]) is not dict:
                raise RepairError("本地任务状态记录无效")
    return record


def _initial_transcript(capture, message):
    failure = capture.get("failure")
    if not failure:
        return None, None
    run_id, step, exit_code, raw = failure
    expected = f"xelatex 第 {step} 步失败（退出码 {exit_code}）"
    if message != expected and not message.startswith(expected + "："):
        return None, None
    return raw, {"run_id": run_id, "step": step, "exit_code": exit_code,
                 "candidate_sha256": capture["candidate_sha256"], "transcript_sha256": digest(raw)}


def trusted_temporary_roots():
    # TMPDIR/TEMP are caller-controlled; use only OS-defined temporary roots.
    if os.name == "nt":
        import ctypes

        local = ctypes.create_unicode_buffer(260)
        shell = ctypes.WinDLL("shell32")
        if shell.SHGetFolderPathW(None, 0x001C, None, 0, local) != 0:
            return ()
        return (Path(local.value) / "Temp",)
    roots = [Path("/tmp").resolve()]
    if sys.platform == "darwin":
        # Darwin's _CS_DARWIN_USER_TEMP_DIR is absent from Python's name table.
        if value := os.confstr(65537):
            roots.append(Path(value).resolve())
    return tuple(roots)


def _visible_task(state):
    status = {key: state.get(key) for key in (
        "identity", "state", "taskbook_sha256", "expires_at", "round", "round_limit",
        "runs", "review_sha256", "private_review", "report_only", "protection", "failure_phase",
    )}
    if "displaced_journal" in state:
        status["displaced_journal"] = state["displaced_journal"]
    if state["state"] == "applied" and not _alive(state.get("pid")):
        status["state"] = "unknown"
        status["failure_phase"] = state.get("failure_phase") or "interrupted"
    elif state["state"] != "applied" and state["expires_at"] <= time.time():
        status["state"] = "expired"
    return status


class Store:
    def __init__(self, root=None):
        self.root = state_root(root)
        self.tasks = self.root / "tasks"
        self.targets = self.root / "targets"
        self.tombstones = self.root / "tombstones"

    def validate_locations(self, out_dir, *, source=None):
        out_dir = absolute(out_dir)
        if _overlap(out_dir, self.root):
            raise RepairError("产物目录与私有状态区必须分离")
        if source is not None:
            source_dir = absolute(source).parent
            if _overlap(self.root, source_dir) or _overlap(out_dir, source_dir):
                raise RepairError("原稿、产物目录与私有状态区必须分离")
        return out_dir

    def preflight_export(self, out_dir, stem, *, synthetic):
        out_dir = self.validate_locations(out_dir)
        key = target_key(out_dir, stem_name(stem))
        self._require_synthetic_location(out_dir, synthetic)
        try:
            directory(self.targets, private=True)
        except FileNotFoundError:
            ledger = None
        else:
            ledger = self._ledger(key)
        if ledger is None:
            if not synthetic:
                raise RepairError("缺少转换失败入口签发的产物来源")
        else:
            self._validate_export_source(out_dir, ledger["provenance"], synthetic)

    @staticmethod
    def _require_synthetic_location(out_dir, synthetic):
        if synthetic and not any(root in out_dir.parents for root in trusted_temporary_roots()):
            raise RepairError("合成入口仅接受系统临时区内的独立产物目录")

    def _validate_export_source(self, out_dir, provenance, synthetic):
        source = provenance["source"]
        self.validate_locations(out_dir, source=source)
        if synthetic and source is not None:
            raise RepairError("不能把已登记的普通产物当作合成候选")

    @contextmanager
    def operation(self, *, recover_expired=True):
        directory(self.root, create=True, parents=True, private=True)
        with lock(self.root / "lock") as lease:
            for path in (self.tasks, self.targets, self.tombstones):
                directory(path, create=True, private=True)
            self.cleanup(recover_transactions=recover_expired)
            yield lease

    def task_dir(self, task_id):
        return self.tasks / validate_id(task_id)

    def _load(self, task_id, *, check_locations=True):
        path = self.task_dir(task_id)
        try:
            directory(self.root, private=True)
            directory(path, private=True)
        except FileNotFoundError:
            return None
        record = read_json(path / "state.json", optional=True)
        if record is not None:
            _validate_task_record(record, task_id)
            if check_locations:
                self.validate_locations(record["out_dir"], source=record["provenance"]["source"])
        return record

    def status(self, task_id):
        state = self._load(task_id)
        if state is not None:
            return _visible_task(state)
        try:
            directory(self.tombstones, private=True)
            gone = read_json(self.tombstones / f"{task_id}.json", optional=True)
        except FileNotFoundError:
            gone = None
        if gone is not None:
            return _validate_task_record(gone, task_id, tombstone=True)
        return {"identity": {"task_id": task_id}, "state": "not_requested",
                "taskbook_sha256": None, "expires_at": None}

    def load(self, task_id):
        state = self._load(task_id)
        if state is None:
            raise RepairError("任务不存在、已删除或已过期")
        return state

    def save(self, task_id, state):
        put_json(self.task_dir(task_id) / "state.json", state)

    def cleanup(self, *, recover_transactions=True):
        for path in self.tasks.iterdir():
            validate_id(path.name)
            state = self._load(path.name, check_locations=recover_transactions)
            if state is None:
                # A crashed export may have reserved its attempt, but issued no task.
                self._delete(path.name, None, "cancelled")
            elif state["expires_at"] <= time.time():
                if state["state"] == "applied" and not recover_transactions:
                    continue
                self.recover(path.name, state)
                self._delete(path.name, state, "expired")

    def _delete(self, task_id, state, final):
        path = directory(self.task_dir(task_id), private=True)
        if state is None:
            gone = {"identity": {"task_id": task_id}, "taskbook_sha256": None, "expires_at": None}
        else:
            gone = {key: state.get(key) for key in ("identity", "taskbook_sha256", "expires_at")}
            if "displaced_journal" in state:
                gone["displaced_journal"] = state["displaced_journal"]
        gone["state"] = final
        put_json(self.tombstones / f"{task_id}.json", gone)
        shutil.rmtree(path)

    def purge(self, task_id=None):
        if task_id is None:
            self.cleanup()
            return
        state = self._load(task_id)
        if state is None:
            return
        self.recover(task_id, state)
        final = "expired" if state["expires_at"] <= time.time() else "cancelled"
        self._delete(task_id, state, final)

    def book(self, task_id, state):
        raw = read(self.task_dir(task_id) / "taskbook.json", REPLY_LIMIT)
        if digest(raw) != state["taskbook_sha256"]:
            raise RepairError("任务书字节与签发指纹不符")
        book = decode_json(raw)
        if type(book) is not dict or book.get("identity") != state["identity"]:
            raise RepairError("任务书身份无法验证")
        provenance = state["provenance"]
        if provenance["source"] is not None and provenance.get("diagnostic_run") is None and "edit" in book["actions"]:
            raise RepairError("任务书缺少可授权正文编辑的首编记录")
        return raw, book

    def _ledger_path(self, key):
        return self.targets / f"{key}.json"

    def _ledger(self, key):
        ledger = read_json(self._ledger_path(key), optional=True)
        if ledger is None:
            return None
        _validate_provenance(ledger.get("provenance"))
        attempts, limit, used = ledger.get("attempts"), ledger.get("limit"), ledger.get("used")
        summaries = ledger.get("diagnostics")
        if (type(limit) is not int or not 1 <= limit <= MAX_ROUNDS or type(used) is not int
                or not 1 <= used <= limit or type(attempts) is not list or len(attempts) != used
                or any(not _is_hash(item) for item in attempts) or ledger.get("last_task_id") != attempts[-1]
                or type(summaries) is not list or not summaries or any(type(item) is not dict for item in summaries)):
            raise RepairError("本地轮次记录无效")
        return ledger

    def issue(self, out_dir, stem, *, synthetic=False, limit=None, source=None,
              message="", capture=None):
        stem = stem_name(stem)
        out_dir = self.validate_locations(out_dir, source=source)
        if limit is not None and not 1 <= limit <= MAX_ROUNDS:
            raise RepairError(f"轮次上限必须在 1–{MAX_ROUNDS} 范围内")
        key = target_key(out_dir, stem)
        ledger_path = self._ledger_path(key)
        ledger = self._ledger(key) or {}
        provenance = ledger.get("provenance")
        if provenance is not None:
            self._validate_export_source(out_dir, provenance, synthetic)
        if source is None and not synthetic and not provenance:
            raise RepairError("缺少转换失败入口签发的产物来源；合成测试须显式授权")
        self._require_synthetic_location(out_dir, synthetic)
        if source is not None:
            source, source_id = _validate_source(source, out_dir)
        directory(out_dir)
        out_id = directory_id(out_dir)
        candidate = read(out_dir / f"{stem}.tex")
        candidate_hash = digest(candidate)
        if source is not None:
            if (capture is None or capture["candidate_sha256"] != candidate_hash
                    or capture["out_id"] != out_id or capture["source_id"] != source_id
                    or capture["candidate_id"] != _stamp(out_dir / f"{stem}.tex")):
                raise RepairError("编译前后无法确认转换候选身份")
            provenance = {"issuer": "Engine/convert.py", "source": str(source), "source_id": source_id,
                          "out_id": out_id, "candidate_sha256": candidate_hash}
        elif synthetic and not provenance:
            provenance = {"issuer": "compilefix export --synthetic", "source": None,
                          "out_id": out_id, "candidate_sha256": candidate_hash}
        elif provenance["issuer"] == "compilefix export --synthetic" and synthetic:
            if provenance["out_id"] != out_id:
                raise RepairError("合成产物目录身份变化")
            provenance = dict(provenance, candidate_sha256=candidate_hash)
        else:
            if provenance["issuer"] != "Engine/convert.py" or provenance["candidate_sha256"] != candidate_hash:
                raise RepairError("候选与已签发的转换来源不符")
            self.validate_target(out_dir, stem, provenance)
        if ledger and ledger["provenance"]["out_id"] != out_id:
            raise RepairError("原产物目录已被替换")
        if source is not None:
            transcript, binding = _initial_transcript(capture, message)
            if binding is not None:
                provenance["diagnostic_run"] = binding
            summaries = diagnostics(candidate, transcript, stem, message)
        elif not synthetic:
            summaries = ledger["diagnostics"]
            if provenance.get("diagnostic_run") is None and any(item.get("classification") == "editable"
                                                                for item in summaries):
                summaries = diagnostics(candidate, None, stem)
        else:
            summaries = diagnostics(candidate, read(out_dir / f"{stem}.log", LOG_LIMIT, optional=True), stem, message)
        book = taskbook(key, candidate, summaries)
        prospective = book["identity"]["task_id"]
        last = ledger.get("last_task_id")
        last_state = self._load(last) if last else None
        if last_state is not None and last_state["state"] == "applied":
            if source is not None:
                raise RepairError("存在需通过独立 CLI 恢复的未完成事务")
            self.recover(last, last_state)
        bound = ledger.get("limit", limit if limit is not None else DEFAULT_ROUNDS)
        if limit is not None and bound != limit:
            raise RepairError("本目标轮次上限已冻结")
        if (last_state and last_state["state"] in ACTIVE and last_state["input_id"] == prospective
                and last_state["expires_at"] > time.time()):
            return last, self.book(last, last_state)[0]
        round_number = ledger.get("used", 0) + 1
        if round_number > bound:
            raise RepairError("目标手动修复轮次已达上限")
        if last_state and last_state["state"] in ACTIVE:
            last_state["state"] = "cancelled"
            self.save(last, last_state)
        task_id = prospective
        attempts = ledger.get("attempts", [])
        if task_id in attempts:
            task_id = digest(json_bytes([prospective, round_number]))
            book = taskbook(key, candidate, summaries, task_id=task_id)
        raw = json_bytes(book)
        ledger = {"limit": bound, "used": round_number, "last_task_id": task_id,
                  "attempts": [*attempts, task_id], "provenance": provenance, "diagnostics": summaries}
        put_json(ledger_path, ledger)
        task_dir = directory(self.task_dir(task_id), create=True, private=True)
        state = {"identity": book["identity"], "state": "exported", "input_id": prospective,
                 "expires_at": time.time() + RETENTION_SECONDS, "out_dir": str(out_dir),
                 "stem": stem, "taskbook_sha256": digest(raw), "provenance": provenance,
                 "round": round_number, "round_limit": bound}
        atomic_write(task_dir / "taskbook.json", raw)
        self.save(task_id, state)
        return task_id, raw

    @staticmethod
    def validate_target(out_dir, stem, provenance):
        if directory_id(out_dir) != provenance["out_id"]:
            raise RepairError("产物目录身份已变化")
        if provenance["source"] is not None:
            _, source_id = _validate_source(provenance["source"], out_dir)
            if source_id != provenance["source_id"]:
                raise RepairError("来源文件身份已变化")
        return read(out_dir / f"{stem}.tex")

    def candidate(self, state):
        contents = self.validate_target(Path(state["out_dir"]), state["stem"], state["provenance"])
        if digest(contents) != state["identity"]["candidate_sha256"]:
            raise RepairError("候选已被外部修改")
        return contents

    def preview(self, task_id):
        state = self.load(task_id)
        if state["state"] not in ACTIVE:
            raise RepairError("任务已离开可预览阶段")
        self.candidate(state)
        raw, _ = self.book(task_id, state)
        return raw

    def record_preview(self, task_id):
        state = self.load(task_id)
        state["preview_sha256"] = state["taskbook_sha256"]
        self.save(task_id, state)

    def confirm(self, task_id, fingerprint):
        state = self.load(task_id)
        if (state["state"] != "exported" or state.get("preview_sha256") != fingerprint
                or state["taskbook_sha256"] != fingerprint):
            raise RepairError("请先预览并确认本稿的实际任务书字节")
        self.candidate(state)
        self.book(task_id, state)
        state["authorization"] = {"sha256": fingerprint, "actor": local_actor(), "timestamp": time.time()}
        self.save(task_id, state)

    def require_authorization(self, state):
        fingerprint = state["taskbook_sha256"]
        if (state.get("preview_sha256") != fingerprint
                or (state.get("authorization") or {}).get("sha256") != fingerprint):
            raise RepairError("任务缺少同一预览的逐稿确认")

    def import_reply(self, task_id, reply_path):
        state = self.load(task_id)
        if state["state"] != "exported":
            raise RepairError("仅可导入一次，且任务必须处于已导出状态")
        self.require_authorization(state)
        candidate = self.candidate(state)
        _, book = self.book(task_id, state)
        path = absolute(reply_path)
        directory(path.parent)
        raw = read(path, REPLY_LIMIT)
        actions = parse_reply(raw, book, candidate)
        _, diff = edit_candidate(candidate, book, actions)
        evidence = {entry["evidence_id"]: entry for entry in book["evidence"]}
        report_only = []
        for action in actions:
            if action["action"] != "report_only":
                continue
            entry = evidence[action["evidence_id"]]
            record = book["diagnostics"][entry["diagnostic_index"]]
            report_only.append({"evidence_id": entry["evidence_id"],
                                "source_line": record["source_line"], "log_line": record["log_line"],
                                "reason_sha256": digest(action["reason"].encode("utf-8"))})
        task_dir = self.task_dir(task_id)
        atomic_write(task_dir / "reply.json", raw)
        atomic_write(task_dir / "review.diff", diff)
        state.update(state="imported", reply_sha256=digest(raw), review_sha256=digest(diff),
                     report_only=report_only)
        self.save(task_id, state)

    def review(self, task_id, state):
        if state["state"] != "imported":
            raise RepairError("任务没有可审阅的回件")
        self.require_authorization(state)
        candidate = self.candidate(state)
        _, book = self.book(task_id, state)
        task_dir = self.task_dir(task_id)
        raw = read(task_dir / "reply.json", REPLY_LIMIT)
        if digest(raw) != state["reply_sha256"]:
            raise RepairError("回件字节与导入指纹不符")
        actions = parse_reply(raw, book, candidate)
        patched, diff = edit_candidate(candidate, book, actions)
        if digest(diff) != state["review_sha256"] or diff != read(task_dir / "review.diff"):
            raise RepairError("完整差异与待审指纹不符")
        return candidate, patched, diff, book, actions

    def cancel(self, task_id):
        state = self.load(task_id)
        self.recover(task_id, state)
        if state["state"] not in ACTIVE:
            raise RepairError("当前状态不能取消")
        state["state"] = "cancelled"
        self.save(task_id, state)

    def recover(self, task_id, state):
        if state["state"] != "applied":
            return
        from .transaction import recover_transaction

        recover_transaction(self, task_id, state)


def _stamp(path):
    info = regular(path)
    return [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size]


def capture_compile(out_dir, stem, *, source):
    try:
        out_dir = directory(out_dir)
        stem_name(stem)
        _, source_id = _validate_source(source, out_dir)
        path = out_dir / f"{stem}.tex"
        return {"candidate_sha256": digest(read(path)), "candidate_id": _stamp(path),
                "source_id": source_id, "out_id": directory_id(out_dir)}
    except (OSError, RepairError):
        return None
