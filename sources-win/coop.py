"""两段式「AI 协同初排」的外壳侧**纯逻辑**（批55 建立，批56 扩展）。

不 import tkinter（与 `engine.py` 同一条纪律：逻辑要能离开窗口测试，见 `tests/test_bridge.py`）。
判据一行都不在这里：CLI 契约、状态 JSON 的 schema、建议校验全在引擎
（`Engine/convert.py` 与 `Engine/slcn/coop.py`）。本模块只做外壳侧需要的事：

  1. **状态发现**：第一段（`--coop-taskbook-only`）把 `_AI协同状态.json` 写进
     `<输出根>/<输出名>_待核/`；第二段要复用同一个暂存目录。界面不该让编辑手选，
     也不该猜 —— 扫 `<输出根>/*_待核/`，按 `state.source.path` 与
     `state.params.out_root` **逐项对上**才算（与引擎 `_coop_locate_state` 同一口径）。
  2. **成功摘要 / 0 回填警示**（批56）：优先读引擎写进状态文件的 `stats`
     （`bib_total/bib_replied/bib_pending/low_total/low_replied/low_applied/
     low_pending_manual`）；旧状态没有 `stats` 时回退解析 `_AI协同记录.md`、
     再回退按 `keys` + 建议清单估算。**不得**再拿「jsonl accepted 数」冒充待人工数
     （批55 真实用户测试里被误导成「待人工 0」）。
  3. **回填导入**（批56）：已选回填清单（文件/目录/剪贴板临时文件可混合、累加、
     移除、清空）的纯逻辑；剪贴板文本落成有确定命名规则的临时 `.md`。
     引擎批56 起 `--coop-replies` 可重复，界面把「已选这份清单」按顺序交给引擎，
     **不再**复制到临时目录、也不再折叠成父目录（批55 的 `prepare_replies` 已删）。
  4. **全文 AI 核查范围映射**（批59）：面板复选框 + 「引用上下文 / 全文」单选 →
     `--coop-fulltext context|full`；**不勾一律空串**（默认零变化），第二段从不带。
     判据不在外壳：正文抽取/窗口合并/截断上限全在引擎（`slcn/coop.py`）。

显示口径（两壳一致，见 `drafts/coop-workflow-design.md` §3 与 mac `CoopFeature`）：
  `ph1` → 「待人工核实（未交付）」；`ph2` → 「上次第二段未完成（可重跑第二段）」；
  `done` → 「已交付」；没有状态 → 「尚未初排」；不认识的 phase → 「状态未知（…）」。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

#: 暂存目录里的状态文件（引擎 `slcn/coop.py: STATE_NAME`，**不**随交付进最终目录）。
STATE_NAME = "_AI协同状态.json"
#: 最终交付目录里的协同记录（引擎 `slcn/coop.py: RECORD_NAME`）。
RECORD_NAME = "_AI协同记录.md"
#: 低置信建议的后缀（引擎 `slcn/coop.py: SUGGESTIONS_SUFFIX`，实际文件再加 `.md`/`.jsonl`）。
SUGGESTIONS_SUFFIX = "_AI建议"
#: 暂存目录的默认后缀（`<out>/<stem>_待核/`）。
STAGE_SUFFIX = "_待核"
#: 剪贴板回填临时文件的命名前缀（批56）。
CLIPBOARD_PREFIX = "回填剪贴板"

#: 批59「全文 AI 核查」的两种范围 = 引擎 `--coop-fulltext` 的合法取值（外壳协议固定）。
#: `context` = 引用/脚注锚点 ±窗口（引擎上限 40,000 字符）；`full` = 正文全文（80,000）。
FULLTEXT_CONTEXT = "context"
FULLTEXT_FULL = "full"
#: 全部取值（顺序 = 面板单选顺序）；不勾选时一个都不传（默认零变化）。
FULLTEXT_MODES = (FULLTEXT_CONTEXT, FULLTEXT_FULL)

#: 状态文案（两壳必须一致；`drafts/batch55-gui-plan.md` §3.1 与 mac `CoopFeature` 同一口径）。
STATUS_PENDING = "待人工核实（未交付）"
STATUS_PH2 = "上次第二段未完成（可重跑第二段）"
STATUS_DELIVERED = "已交付"
STATUS_NOT_STARTED = "尚未初排"
#: 运行失败（不是状态文件里的 phase，而是这次跑挂了）——界面给的红字口径。
STATUS_FAILED = "失败"

#: 引擎批56 写进 `_AI协同状态.json` 的统计字段（缺一即视为旧状态，整体回退）。
STATS_KEYS = ("bib_total", "bib_replied", "bib_pending", "low_total",
              "low_replied", "low_applied", "low_pending_manual")


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


def coop_fulltext_mode(enabled: bool, scope: str = FULLTEXT_CONTEXT) -> str:
    """界面「全文 AI 核查」勾选 + 范围 → `--coop-fulltext` 的取值（批59，纯逻辑）。

    **不勾 = 空串**：调用方（`ConvertTab.coop_ph1_call`）据此不追加任何 argv，
    argv 与批58 逐字节相同（默认零变化）；引擎缺省本来也不带这个 flag。
    不认识的 scope（理论上面板单选给不出来）一律回落 `context`：引用上下文外发的
    正文比全文少，宁可少发 —— 隐私侧的错误只能往「少」的方向错。
    """
    if not enabled:
        return ""
    return FULLTEXT_FULL if str(scope or "") == FULLTEXT_FULL else FULLTEXT_CONTEXT


def _same_path(a: str, b: str) -> bool:
    """路径比较：绝对化 + `normcase`（Windows 大小写不敏感；mac 上 normcase 是恒等）。"""
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _read_text(path: str) -> str:
    """读文本文件；不存在/读不了返回空串（摘要回退用，**不抛**）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


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
        """协同记录：交付后优先 `delivered_to`，否则 `final_dir`（未交付时通常还不存在）。"""
        directory = self.delivery_dir or self.final_dir
        return os.path.join(directory, RECORD_NAME) if directory else ""

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


# ── 建议清单（第二段成功后才有）───────────────────────────────────────────

_STATUSES = ("accepted", "rejected", "missing")


def read_suggestions(jsonl_path: str) -> dict:
    """读 `<stem>_AI建议.jsonl`，按 `status` 计数；坏行忽略（引擎自己会写合法 JSONL）。

    返回 `{accepted, rejected, missing, replied, total, exists, path}`。
    `accepted` 是**建议行**数（同一个低置信 ID 的多个字段各一行，与引擎写出的口径一致）；
    `replied` 是「**有回复**的 ID 数」（accepted/rejected 去重；`missing` 不算回填）。
    批55 的「待人工」口径错误就错在拿 accepted 行数当成了全部——批56 摘要改用 stats，
    这里只作为旧状态的回退输入。
    """
    path = jsonl_path or ""
    summary = {"accepted": 0, "rejected": 0, "missing": 0, "replied": 0,
               "total": 0, "exists": bool(path) and os.path.isfile(path), "path": path}
    if not summary["exists"]:
        return summary
    replied_ids: set[str] = set()
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
                    if status in ("accepted", "rejected"):
                        replied_ids.add(str(row.get("id") or ""))
    except OSError:
        summary["exists"] = False
    summary["replied"] = len(replied_ids)
    return summary


def suggestions_summary(stage_dir: str, stem: str) -> dict:
    """按引擎的落点约定找建议清单：`<stage>/<stem>_AI建议.jsonl`。"""
    if not stage_dir or not stem:
        return read_suggestions("")
    return read_suggestions(os.path.join(stage_dir, f"{stem}{SUGGESTIONS_SUFFIX}.jsonl"))


def suggestions_text(summary: dict) -> str:
    """一行建议清单摘要（第二段成功后才有；不是成功摘要的主口径）。"""
    if not summary or not summary.get("exists"):
        return "低置信建议：第二段成功后生成（当前还没有清单）"
    return (f"低置信建议清单：接受 {summary.get('accepted', 0)} / "
            f"拒绝 {summary.get('rejected', 0)} / "
            f"未回填 {summary.get('missing', 0)}")


# ── 第二段成功摘要 / 0 回填警示（批56）────────────────────────────────────

#: 协同记录里的「并入」行：`- 并入：**9** 条`（引擎 `render_record`）。
_RECORD_MERGED_RE = re.compile(r"并入：\**\s*(\d+)\s*\**\s*条")
#: 记录里 ParseReport / LowConfReport 的 summary 行。
_RECORD_BIB_RE = re.compile(r"解析到\s*(\d+)\s*条[，,]\s*拒绝\s*(\d+)\s*条[，,]\s*未回填\s*(\d+)\s*条")
_RECORD_LOW_RE = re.compile(r"接受\s*(\d+)\s*条[，,]\s*拒绝\s*(\d+)\s*条[，,]\s*未回填\s*(\d+)\s*条")


def state_stats(state: dict | None) -> dict | None:
    """读引擎批56 写进状态文件的 `stats`。

    与 mac `CoopStats.fromJSON` 同口径：**任一新字段存在**就按 stats 解析（缺的按 0），
    一个都没有才返回 None 回退旧状态；负值/非整数按 0（引擎 `make_stats` 只会写非负整数）。
    """
    stats = (state or {}).get("stats")
    if not isinstance(stats, dict) or not any(key in stats for key in STATS_KEYS):
        return None
    out: dict = {}
    for key in STATS_KEYS:
        value = stats.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            value = 0
        out[key] = max(0, value)
    return out


def merged_count_from_record(record_path: str) -> int | None:
    """从 `_AI协同记录.md` 读「并入：**N** 条」；读不到返回 None。"""
    match = _RECORD_MERGED_RE.search(_read_text(record_path))
    return int(match.group(1)) if match else None


def stats_from_record(record_path: str, *, state: dict | None = None) -> dict | None:
    """旧状态（无 `stats`）的回退：解析 `_AI协同记录.md` 的两个 summary 行。"""
    text = _read_text(record_path)
    bib = _RECORD_BIB_RE.search(text)
    low = _RECORD_LOW_RE.search(text)
    if not bib and not low:
        return None
    bib_replied = int(bib.group(1)) if bib else 0
    bib_rejected = int(bib.group(2)) if bib else 0
    bib_missing = int(bib.group(3)) if bib else 0
    # 任务书的文献键 = 回填成功 + 被拒 + 未回填（与 mac 的回退口径一致）
    bib_total = bib_replied + bib_rejected + bib_missing
    low_accepted = int(low.group(1)) if low else 0
    low_rejected = int(low.group(2)) if low else 0
    low_missing = int(low.group(3)) if low else 0
    low_total = low_accepted + low_rejected + low_missing
    applied = state.get("applied_low_confidence") if isinstance(state, dict) else None
    applied = (applied if isinstance(applied, int) and not isinstance(applied, bool)
               and applied >= 0 else 0)
    return {
        "bib_total": bib_total, "bib_replied": bib_replied,
        "bib_pending": bib_missing,
        "low_total": low_total, "low_replied": low_accepted + low_rejected,
        "low_applied": applied,
        "low_pending_manual": max(0, low_total - applied),
    }


def stats_from_state(stage: CoopStage) -> dict:
    """最后回退：按状态文件的 keys/replied/applied + 建议清单估算（旧状态也能有个合理摘要）。"""
    keys = stage.state.get("keys") or {}
    bib_keys = {str(key) for key in (keys.get("bib") or [])}
    low_keys = {str(key) for key in (keys.get("low_confidence") or [])}
    replied = {str(key) for key in (stage.state.get("replied") or [])}
    bib_replied = len(bib_keys & replied)
    bib_total = len(bib_keys)
    suggestions = read_suggestions(stage.suggestions_jsonl)
    low_replied = min(int(suggestions.get("replied") or 0), len(low_keys))
    applied = stage.state.get("applied_low_confidence")
    applied = (applied if isinstance(applied, int) and not isinstance(applied, bool)
               and applied >= 0 else 0)
    low_total = len(low_keys)
    return {
        "bib_total": bib_total, "bib_replied": bib_replied,
        "bib_pending": max(0, bib_total - bib_replied),
        "low_total": low_total, "low_replied": low_replied,
        "low_applied": min(applied, low_total),
        # 旧状态没有「仅记录待人工」字段：没被应用的低置信项都算待人工（含拒绝/未回复）
        "low_pending_manual": max(0, low_total - min(applied, low_total)),
    }


def effective_bib_pending(stats: dict) -> int:
    """`bib_pending` 缺失（0）但确实没回填完时用 total - replied 兜底（与 mac 同口径）。"""
    pending = int(stats.get("bib_pending") or 0)
    total = int(stats.get("bib_total") or 0)
    replied = int(stats.get("bib_replied") or 0)
    if pending == 0 and total > 0:
        return max(0, total - replied)
    return pending


def stats_line(stats: dict, *, merged: int | None = None) -> str:
    """第二段成功摘要（与 mac `CoopFeature.formatResult` **逐字一致**）：

    「文献回填 x/y（并入 z 条，未回填 n）｜低置信回复 a（应用 b / 仅记录待人工 c）」
    `merged` = 实际并入 reference.bib 的条数（来自协同记录）；读不到时说
    「并入条数见协同记录」，**不**拿回填数冒充并入数（那是批55 式误导）。
    """
    if not stats:
        return ""
    merged_text = "并入条数见协同记录" if merged is None else f"并入 {int(merged)} 条"
    return (f"文献回填 {stats.get('bib_replied', 0)}/{stats.get('bib_total', 0)}"
            f"（{merged_text}，未回填 {effective_bib_pending(stats)}）｜"
            f"低置信回复 {stats.get('low_replied', 0)}"
            f"（应用 {stats.get('low_applied', 0)} / "
            f"仅记录待人工 {stats.get('low_pending_manual', 0)}）")


#: 0 文献回填的橙色警示（批56；与 mac `CoopFeature.zeroBibRepliesWarning` **逐字一致**）。
BIB_ZERO_WARNING = ("本次没有文献条目回填，reference.bib 未并入任何 AI 修订；"
                    "若外部 chat 已返回 bibtex/bib，可能漏导入"
                    "（可重新初排后再导入全部回填）。")


def bib_zero_warning(stats: dict | None) -> str:
    """`bib_total > 0 且 bib_replied == 0` → 橙色警示文本；否则空串。"""
    if stats and stats.get("bib_total", 0) > 0 and stats.get("bib_replied", 0) == 0:
        return BIB_ZERO_WARNING
    return ""


def summarize_stage(stage: CoopStage) -> dict:
    """ph2 成功后界面要的全部摘要信息（stats → record → 旧 state 三级回退）。

    返回 `{stats, merged, text, warning}`；`text` 为空串表示没有可显示的统计。
    只有 `phase=done` 的暂存目录才有意义（0 回填警示也只在这里出现）。
    """
    stats = state_stats(stage.state)
    legacy = stats is None
    merged = merged_count_from_record(stage.record_path) if stage.record_path else None
    if stats is None:
        stats = stats_from_record(stage.record_path, state=stage.state)
    if stats is None:
        stats = stats_from_state(stage)
    if merged is None and stats.get("bib_replied", 0) == 0:
        merged = 0                     # 记录读不到且一条都没回填 → 「并入 0 条」是准确说法
    return {
        "stats": stats,
        "merged": merged,
        "legacy": legacy,
        "text": stats_line(stats, merged=merged) if stats else "",
        "warning": bib_zero_warning(stats),
    }


# ── 回填导入：已选清单（纯逻辑；界面只负责摆放控件）─────────────────────


class ReplySelection:
    """已选回填路径：文件 / 目录 / 剪贴板临时文件可混合、可多次导入累加。

    与 mac 侧同一语义：**每个路径原样、按选择顺序**交给引擎（重复 `--coop-replies`），
    不折叠父目录、不复制临时目录（批55 的 `prepare_replies` 因此删除）。
    """

    def __init__(self) -> None:
        self._paths: list[str] = []

    @property
    def paths(self) -> list[str]:
        return list(self._paths)

    @property
    def count(self) -> int:
        return len(self._paths)

    def label(self) -> str:
        """面板标题：「已选 N 项」。"""
        return f"已选 {self.count} 项"

    def add(self, paths) -> tuple[int, int]:
        """追加路径，返回 `(新增数, 跳过数)`；空串跳过，同一路径（按绝对/normcase）去重。"""
        added = skipped = 0
        for raw in paths or []:
            path = str(raw or "").strip()
            if not path:
                skipped += 1
                continue
            if any(_same_path(path, kept) for kept in self._paths):
                skipped += 1
                continue
            self._paths.append(path)
            added += 1
        return added, skipped

    def remove_index(self, index: int) -> str | None:
        """按下标移除一项，返回被移除的路径（下标越界返回 None）。"""
        if not (0 <= index < len(self._paths)):
            return None
        return self._paths.pop(index)

    def remove(self, path: str) -> bool:
        """按路径移除一项（不存在返回 False）。"""
        for index, kept in enumerate(self._paths):
            if _same_path(path, kept):
                del self._paths[index]
                return True
        return False

    def clear(self) -> int:
        """清空，返回清掉了多少项。"""
        count = len(self._paths)
        self._paths.clear()
        return count


# ── 剪贴板回填：文本 → 命名确定的临时 .md（纯命名 + 落盘）────────────────

_CLIP_SEQ = {"value": 0}


def clipboard_reply_name(seq: int, *, now: float | None = None) -> str:
    """剪贴板回填临时文件名：`回填剪贴板-YYYYmmdd-HHMMSS-<seq>.md`（纯函数，可测）。"""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    return f"{CLIPBOARD_PREFIX}-{stamp}-{max(1, int(seq))}.md"


def write_clipboard_reply(text: str, directory: str, *,
                          now: float | None = None) -> str:
    """把剪贴板文本写成 `<directory>/回填剪贴板-…-N.md`，返回路径。

    空文本 / 纯空白 → ValueError（界面红字提示「剪贴板里没有可导入的文本」）；
    同名已存在（同一秒内多次粘贴）时序号递增，**不覆盖**已导入的回复。
    """
    content = (text or "").strip()
    if not content:
        raise ValueError("剪贴板里没有可导入的文本")
    target_dir = directory or ""
    if not target_dir:
        raise ValueError("没有可写的临时目录")
    os.makedirs(target_dir, exist_ok=True)
    seq = 1
    while True:
        path = os.path.join(target_dir, clipboard_reply_name(seq, now=now))
        if not os.path.exists(path):
            break
        seq += 1
    # 计数器不参与命名唯一性判定（以磁盘上是否已存在为准），但留着便于日志/测试观察。
    _CLIP_SEQ["value"] = seq
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content + "\n")
    return path
