"""两段式「AI 协同初排」的外壳侧**纯逻辑**（批55）。

不 import tkinter（与 `engine.py` 同一条纪律：逻辑要能离开窗口测试，见 `tests/test_bridge.py`）。
判据一行都不在这里：CLI 契约、状态 JSON 的 schema、建议校验全在引擎
（`Engine/convert.py` 与 `Engine/slcn/coop.py`，批54 冻结）。本模块只做三件事：

  1. **状态发现**：第一段（`--coop-taskbook-only`）把 `_AI协同状态.json` 写进
     `<输出根>/<输出名>_待核/`；第二段要复用同一个暂存目录。界面不该让编辑手选，
     也不该猜 —— 扫 `<输出根>/*_待核/`，按 `state.source.path` 与
     `state.params.out_root` **逐项对上**才算（与引擎 `_coop_locate_state` 同一口径）。
  2. **建议摘要**：第二段成功后 `<暂存>/<输出名>_AI建议.jsonl` 每行一条建议
     （`status` = accepted / rejected / missing）；界面只统计、不做校验。
  3. **导入回复**：引擎 `--coop-replies` 只收**单个** FILE|DIR；界面允许多选文件，
     这里把它们拷进一个临时目录（引擎按文件名排序读入，顺序不影响结果）。

显示口径（两壳一致，见 `drafts/coop-workflow-design.md` §3）：
  `ph1`/`ph2` → 「待人工核实（未交付）」；`done` → 「已交付」；读不到/不认识 → 「失败」。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field

#: 暂存目录里的状态文件（引擎 `slcn/coop.py: STATE_NAME`，**不**随交付进最终目录）。
STATE_NAME = "_AI协同状态.json"
#: 最终交付目录里的协同记录（引擎 `slcn/coop.py: RECORD_NAME`）。
RECORD_NAME = "_AI协同记录.md"
#: 低置信建议的后缀（引擎 `slcn/coop.py: SUGGESTIONS_SUFFIX`，实际文件再加 `.md`/`.jsonl`）。
SUGGESTIONS_SUFFIX = "_AI建议"
#: 暂存目录的默认后缀（`<out>/<stem>_待核/`）。
STAGE_SUFFIX = "_待核"

#: 状态文案（两壳必须一致；`drafts/batch55-gui-plan.md` §3.1 与 mac `CoopFeature` 同一口径）。
STATUS_PENDING = "待人工核实（未交付）"
STATUS_PH2 = "上次第二段未完成（可重跑第二段）"
STATUS_DELIVERED = "已交付"
STATUS_NOT_STARTED = "尚未初排"
#: 运行失败（不是状态文件里的 phase，而是这次跑挂了）——界面给的红字口径。
STATUS_FAILED = "失败"


def status_text(phase: str | None) -> str:
    """`_AI协同状态.json` 的 `phase` → 界面状态文本（**不猜**：不认识就说「状态未知」）。"""
    if phase == "ph1":
        return STATUS_PENDING
    if phase == "ph2":
        return STATUS_PH2
    if phase == "done":
        return STATUS_DELIVERED
    if not phase:
        return STATUS_NOT_STARTED
    return f"状态未知（{phase}）"


def _same_path(a: str, b: str) -> bool:
    """路径比较：绝对化 + `normcase`（Windows 大小写不敏感；mac 上 normcase 是恒等）。"""
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ── 状态发现 ─────────────────────────────────────────────────────────────


def load_state(stage_dir: str) -> dict | None:
    """读暂存目录里的状态 JSON；没有/读坏返回 None（与引擎 `coop.load_state` 同口径）。"""
    path = os.path.join(stage_dir or "", STATE_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class CoopStage:
    """一个已发现的暂存目录 + 它状态文件里界面要用的字段。"""

    stage_dir: str
    state: dict = field(default_factory=dict)
    #: 同源同输出根下找到的暂存目录个数（>1 表示用了最近的一份；界面可提示有歧义）。
    found: int = 1

    # ── 状态文件里的键（读不到就给空串，界面据此禁用按钮）──
    @property
    def stem(self) -> str:
        stem = str((self.state.get("params") or {}).get("stem") or "")
        if stem:
            return stem
        # 兜底（与 mac `CoopFeature.parseState` 同口径）：默认暂存目录名 = `<stem>_待核`
        name = os.path.basename(self.stage_dir)
        return name[:-len(STAGE_SUFFIX)] if name.endswith(STAGE_SUFFIX) else ""

    @property
    def phase(self) -> str:
        return str(self.state.get("phase") or "")

    @property
    def status_text(self) -> str:
        return status_text(self.phase)

    @property
    def taskbook(self) -> str:
        return str(self.state.get("taskbook") or "")

    @property
    def final_dir(self) -> str:
        return str(self.state.get("final_dir") or "")

    @property
    def delivered_to(self) -> str:
        return str(self.state.get("delivered_to") or "")

    @property
    def delivery_dir(self) -> str:
        """交付目录：只有 `done` 才信 `delivered_to`；否则最终目录**还没写**（不许展示成交付）。"""
        if self.phase == "done":
            return self.delivered_to or self.final_dir
        return ""

    @property
    def suggestions_md(self) -> str:
        return os.path.join(self.stage_dir, f"{self.stem}{SUGGESTIONS_SUFFIX}.md")

    @property
    def suggestions_jsonl(self) -> str:
        return os.path.join(self.stage_dir, f"{self.stem}{SUGGESTIONS_SUFFIX}.jsonl")

    @property
    def record_path(self) -> str:
        return os.path.join(self.delivery_dir, RECORD_NAME) if self.delivery_dir else ""

    @property
    def state_path(self) -> str:
        return os.path.join(self.stage_dir, STATE_NAME)


def load_stage(stage_dir: str, *, found: int = 1) -> CoopStage | None:
    """从**已知**暂存目录读 CoopStage；没有状态文件/没有 `phase` 返回 None（与 mac 同口径）。"""
    state = load_state(stage_dir)
    if state is None or not str(state.get("phase") or "").strip():
        return None
    return CoopStage(stage_dir=os.path.abspath(stage_dir), state=state, found=found)


def stage_dirs(out_root: str) -> list[str]:
    """`<out_root>` 下所有 `*_待核/` 目录（排序、绝对路径；目录不存在返回空表）。"""
    root = os.path.abspath(os.path.expanduser(out_root or ""))
    if not os.path.isdir(root):
        return []
    return [os.path.join(root, name) for name in sorted(os.listdir(root))
            if name.endswith(STAGE_SUFFIX) and os.path.isdir(os.path.join(root, name))]


def discover_stages(out_root: str, *, source: str = "") -> list[CoopStage]:
    """扫暂存目录并按**状态文件自己写的**来源/输出根匹配（不按目录名猜）。

    `source` 非空时要求 `state.source.path` 也对上（同一输出目录里可能有别的稿的暂存目录）；
    输出根要求 `state.params.out_root` 对上（同一篇稿可能写去过不同期目录）。
    返回按状态文件 mtime **从新到旧**排序的列表。
    """
    if not str(out_root or "").strip() or not str(source or "").strip():
        return []                       # 缺任一项都不猜（与 mac `discoverStages` 同口径）
    found: list[tuple[float, CoopStage]] = []
    for stage_dir in stage_dirs(out_root):
        state = load_state(stage_dir)
        if state is None or not str(state.get("phase") or "").strip():
            continue
        params = state.get("params") or {}
        if not _same_path(str(params.get("out_root") or ""), out_root):
            continue
        if not _same_path(str((state.get("source") or {}).get("path") or ""), source):
            continue
        try:
            mtime = os.path.getmtime(os.path.join(stage_dir, STATE_NAME))
        except OSError:
            mtime = 0.0
        found.append((mtime, CoopStage(stage_dir=os.path.abspath(stage_dir), state=state)))
    found.sort(key=lambda pair: pair[0], reverse=True)
    stages = [stage for _mtime, stage in found]
    for stage in stages:
        stage.found = len(stages)
    return stages


def discover_stage(out_root: str, *, source: str = "") -> CoopStage | None:
    """最近的一份匹配暂存目录；一份都没有返回 None（界面据此禁用第二段按钮）。"""
    stages = discover_stages(out_root, source=source)
    return stages[0] if stages else None


# ── 建议摘要（第二段成功后才有）───────────────────────────────────────────

_STATUSES = ("accepted", "rejected", "missing")


def read_suggestions(jsonl_path: str) -> dict:
    """读 `<stem>_AI建议.jsonl`，按 `status` 计数；坏行忽略（引擎自己会写合法 JSONL）。

    返回 `{accepted, rejected, missing, total, exists, path}`。
    `accepted` 是**建议行**数（同一个低置信 ID 的多个字段各一行，与引擎写出的口径一致）。
    """
    path = jsonl_path or ""
    summary = {"accepted": 0, "rejected": 0, "missing": 0, "total": 0,
               "exists": bool(path) and os.path.isfile(path), "path": path}
    if not summary["exists"]:
        return summary
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                status = str(row.get("status") or "")
                if status in _STATUSES:
                    summary[status] += 1
                    summary["total"] += 1
    except OSError:
        summary["exists"] = False
    return summary


def suggestions_summary(stage_dir: str, stem: str) -> dict:
    """按引擎的落点约定找建议清单：`<stage>/<stem>_AI建议.jsonl`。"""
    if not stage_dir or not stem:
        return read_suggestions("")
    return read_suggestions(os.path.join(stage_dir, f"{stem}{SUGGESTIONS_SUFFIX}.jsonl"))


def suggestions_text(summary: dict) -> str:
    """一行建议摘要（界面显示用；清单还没生成时说清「第二段后才有」）。"""
    if not summary or not summary.get("exists"):
        return "低置信建议：第二段成功后生成（当前还没有清单）"
    return (f"低置信建议：接受 {summary.get('accepted', 0)} / "
            f"拒绝 {summary.get('rejected', 0)} / "
            f"待人工 {summary.get('missing', 0)}")


# ── 导入回复：多选文件 → 引擎认识的单个路径 ──────────────────────────────


def prepare_replies(paths) -> str:
    """把界面选中的回复路径换成 `--coop-replies` 能吃的**单个**路径。

    · 一个路径（文件或目录）→ 原样返回（引擎直接读）；
    · 多个文件 → 拷进一个临时目录（保留文件名；重名加 `-2`），返回该目录。
      **不改原文件**；临时目录交给操作系统清理（引擎读完就不再碰）。
    · 空 → ValueError（界面不该把空路径发给引擎）。
    """
    items = [str(p) for p in (paths or []) if str(p or "").strip()]
    if not items:
        raise ValueError("没有选择 AI 回复文件")
    if len(items) == 1:
        return items[0]
    target_dir = tempfile.mkdtemp(prefix="slcn-coop-replies-")
    used: dict[str, int] = {}
    for path in items:
        name = os.path.basename(path.rstrip("/\\")) or "reply.txt"
        count = used.get(name, 0) + 1
        used[name] = count
        if count > 1:
            stem, ext = os.path.splitext(name)
            name = f"{stem}-{count}{ext}"
        shutil.copy2(path, os.path.join(target_dir, name))
    return target_dir
