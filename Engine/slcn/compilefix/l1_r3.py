"""Journaled R3 hash synchronization; only caller-supplied local state callbacks."""
import copy
from pathlib import Path

from .files import RepairError, atomic_write, digest, directory, put_json, read, read_json


class PreviewSync:
    def __init__(self, work_dir, state, preview, repaired, *, load_state, save_state, read_candidate,
                 repair_kind="L1"):
        self.root = directory(Path(work_dir) / ".aiw")
        self.journal = self.root / "l1-preview-recovery.json"
        self.state, self.preview = state, preview
        self.repair_kind = repair_kind
        self.load_state, self.save_state = load_state, save_state
        self.original_state = copy.deepcopy(state)
        self.original = read_candidate()
        self.sha256 = digest(repaired)
        self.path = Path(preview["candidate_text"])
        self.task = directory(self.root / state["task_id"])
        if self.path.parent != self.task or read(self.path) != self.original or load_state() != state:
            raise ValueError(f"{self.repair_kind} 同步前预览或状态变化")
        self.backup = self.task / "l1-preview-original.tex"
        atomic_write(self.backup, self.original)
        new_state = copy.deepcopy(state)
        new_state["preview"]["text_new_sha256"] = self.sha256
        self.updated_state = copy.deepcopy(new_state)
        self.record = {"identity": digest((state["task_id"] + self.sha256).encode()), "repair_kind": repair_kind,
                       "state": "syncing", "task_id": state["task_id"],
                       "candidate_name": self.path.name, "before": self.original_state,
                       "after": new_state, "before_sha256": digest(self.original), "after_sha256": self.sha256}
        put_json(self.journal, self.record)
        try:
            atomic_write(self.path, repaired)
            save_state(new_state)
            if read_candidate_new(self.path, self.sha256) != repaired or load_state() != new_state:
                raise ValueError(f"{self.repair_kind} 同步后预览与状态指纹不符")
            state.clear()
            state.update(copy.deepcopy(new_state))
            preview.clear()
            preview.update(new_state["preview"])
        except BaseException:
            self.rollback()
            raise

    def rollback(self, *, expected_state=None):
        current = self.load_state()
        raw = read(self.path)
        # The R3 caller may have persisted its own final delivery transition
        # before a later journal write failed. Only that exact local snapshot
        # is accepted in addition to the two pre-delivery snapshots.
        accepted = (self.original_state, self.updated_state)
        if expected_state is not None:
            accepted += (expected_state,)
        own_delivery = (expected_state is not None and _same_delivery_snapshot(current, expected_state))
        if ((current not in accepted and not own_delivery)
                or digest(raw) not in (digest(self.original), self.sha256)):
            raise ValueError(f"{self.repair_kind} 预览回滚遇到外部修改，保留恢复材料并阻断交付")
        atomic_write(self.path, self.original)
        self.save_state(self.original_state)
        self.state.clear()
        self.state.update(copy.deepcopy(self.original_state))
        self.preview.clear()
        self.preview.update(self.original_state["preview"])
        self.record["state"] = "rolled_back"
        put_json(self.journal, self.record)

    def commit(self):
        self.record["state"] = "completed"
        put_json(self.journal, self.record)


def read_candidate_new(path, expected):
    raw = read(path)
    if digest(raw) != expected:
        raise ValueError("L1 同步后候选指纹不符")
    return raw


def _same_delivery_snapshot(current, expected):
    # The legacy final-state writer refreshes only updated_at in its payload.
    # Every business field must still match the caller's own transition.
    if current.get("phase") != "delivered" or expected.get("phase") != "delivered":
        return False
    normalized = copy.deepcopy(current)
    if "updated_at" in expected:
        normalized["updated_at"] = expected["updated_at"]
    else:
        normalized.pop("updated_at", None)
    return normalized == expected


def _known_delivery_transition(current, expected):
    if current == expected:
        return True
    history, prior = current.get("history", []), expected.get("history", [])
    if (current.get("phase") != "delivered" or not isinstance(history, list)
            or history[:-1] != prior or len(history) != len(prior) + 1
            or not isinstance(history[-1], dict) or history[-1].get("event") != "delivered"):
        return False
    normalized = copy.deepcopy(current)
    for key in ("phase", "history", "delivery", "updated_at"):
        if key in expected:
            normalized[key] = copy.deepcopy(expected[key])
        else:
            normalized.pop(key, None)
    return normalized == expected


def recover_preview(work_dir, *, load_state, save_state, is_delivered=None):
    root = directory(Path(work_dir) / ".aiw", allow_source_area=True)
    journal = root / "l1-preview-recovery.json"
    record = read_json(journal, optional=True)
    if record is None or record.get("state") in {"completed", "rolled_back"}:
        return
    kind = "L2" if record.get("repair_kind") == "L2" else "L1"
    task_id = record.get("task_id", "")
    # Task IDs are generated by R3; restrict to a single path component.
    if not task_id or Path(task_id).name != task_id or task_id in {".", ".."}:
        raise ValueError(f"{kind} 预览恢复身份无效")
    task = directory(root / task_id, allow_source_area=True)
    name = record.get("candidate_name", "")
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"{kind} 预览恢复路径无效")
    path = task / name
    original = read(task / "l1-preview-original.tex")
    if digest(original) != record["before_sha256"]:
        raise ValueError(f"{kind} 预览恢复材料指纹不符")
    current = load_state()
    if is_delivered is not None and is_delivered(current, record["after_sha256"]):
        if not _known_delivery_transition(current, record["after"]):
            raise ValueError(f"{kind} 预览恢复遇到外部状态修改，禁止交付")
        if digest(read(path)) != record["after_sha256"]:
            raise ValueError(f"{kind} 已交付预览指纹变化")
        record["state"] = "completed"
        put_json(journal, record)
        return
    if (current not in (record["before"], record["after"])
            or digest(read(path)) not in (record["before_sha256"], record["after_sha256"])):
        raise ValueError(f"{kind} 预览恢复遇到外部修改，禁止交付")
    atomic_write(path, original)
    save_state(record["before"])
    record["state"] = "rolled_back"
    put_json(journal, record)
