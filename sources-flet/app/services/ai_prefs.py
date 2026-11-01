"""AI 协同 / 直连核实偏好（纯函数；不 import flet、不发任何请求）。

外壳只做「取值 → 拼参数 → 显示」：文档逻辑仍在 `Engine/`。本模块是两通道
共用的**取值与闸门**：

· 设置页 AI 段的默认值，与旧 settings.json 的向后兼容（没有 `ai` 段 = 全默认）；
· 总开关 / Key / 预算三道闸门，以及「这一次运行走哪一段」的判定；
· 直连上传预览的**字段白名单**（只列文献元数据与任务书摘要，正文永不进预览）；
· 协同暂存目录发现（只读镜像 `_AI协同状态.json`，口径对齐旧 Tk 壳 `sources-win/coop.py`——
  历史文件，2026-11-01 批 108 已删除；但**不 import 旧外壳**）。

密钥红线：`key` 只存在本地设置 JSON 与内存里；命令行（argv）只经环境变量
`SLCN_LLM_KEY` 传给引擎，日志/证据/仓库里永远不出现。
"""
from __future__ import annotations

import json
import os

# ── 直连 API 默认值（与任务书 P6-2 R2 一致） ────────────────────────────
DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_PROTOCOL = "chat"
PROTOCOLS = ("chat", "responses")
DEFAULT_ARTICLE_BUDGET = 30000
DEFAULT_BATCH_BUDGET = 1000000

# ── 协同暂存（与 Engine/slcn/coop.py 同口径；旧 Tk 壳文件属历史） ──────
STATE_NAME = "_AI协同状态.json"
STAGE_SUFFIX = "_待核"
FULLTEXT_CONTEXT = "context"
FULLTEXT_FULL = "full"
FULLTEXT_MODES = (FULLTEXT_CONTEXT, FULLTEXT_FULL)
FULLTEXT_LABELS = {
    FULLTEXT_CONTEXT: "context（上下文摘要）",
    FULLTEXT_FULL: "full（整篇正文）",
}
STATUS_PH1 = "待人工核实（未交付）"
STATUS_PH2 = "上次第二段未完成（可重跑第二段）"
STATUS_DELIVERED = "已交付"
STATUS_UNKNOWN = "状态未知"

# 本次运行的四种去向（界面按它挑命令与摘要）。
MODE_PLAIN = "plain"
MODE_PH1 = "ph1"
MODE_PH2 = "ph2"
MODE_DIRECT = "direct"

AI_DEFAULTS = {
    "enabled": False,
    "key": "",
    "base": DEFAULT_BASE,
    "model": DEFAULT_MODEL,
    "protocol": DEFAULT_PROTOCOL,
    "thinking": False,
    "article_budget_tokens": DEFAULT_ARTICLE_BUDGET,
    "batch_budget_tokens": DEFAULT_BATCH_BUDGET,
    "verify_max": 0,
}


def defaults() -> dict:
    return dict(AI_DEFAULTS)


def _int_or(value, fallback: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return max(0, number)


def normalize(ai: dict | None) -> dict:
    """补默认 + 钳类型。旧设置没有 `ai` 段时等价于 `defaults()`。"""
    data = dict(AI_DEFAULTS)
    if isinstance(ai, dict):
        for key in AI_DEFAULTS:
            if key in ai and ai[key] is not None:
                data[key] = ai[key]
    data["enabled"] = bool(data["enabled"])
    data["thinking"] = bool(data["thinking"])
    data["key"] = str(data["key"] or "").strip()
    data["base"] = str(data["base"] or "").strip() or DEFAULT_BASE
    data["model"] = str(data["model"] or "").strip() or DEFAULT_MODEL
    if data["protocol"] not in PROTOCOLS:
        data["protocol"] = DEFAULT_PROTOCOL
    data["article_budget_tokens"] = _int_or(data["article_budget_tokens"], DEFAULT_ARTICLE_BUDGET)
    data["batch_budget_tokens"] = _int_or(data["batch_budget_tokens"], DEFAULT_BATCH_BUDGET)
    data["verify_max"] = _int_or(data["verify_max"], 0)
    return data


def from_store(store) -> dict:
    """从 SettingsStore 读 AI 段（缺段/坏值都回落默认）。"""
    raw = store.get("ai", {}) if store is not None else {}
    return normalize(raw if isinstance(raw, dict) else {})


def save(store, patch: dict) -> dict:
    """只写白名单键；返回落盘后的完整 AI 段。"""
    current = from_store(store)
    for key, value in (patch or {}).items():
        if key in AI_DEFAULTS:
            current[key] = value
    normalized = normalize(current)
    store.set("ai", normalized)
    return normalized


def mask_key(key: str) -> str:
    """给界面/日志看的密钥描述——永远不返回完整密钥。"""
    text = str(key or "").strip()
    if not text:
        return "（未设置）"
    tail = text[-4:] if len(text) >= 8 else ""
    return f"已设置（末尾 {tail}）" if tail else "已设置"


def key_state(ai: dict | None) -> dict:
    data = normalize(ai)
    return {"key_set": bool(data["key"]), "key_label": mask_key(data["key"])}


def parse_budget(raw, *, label: str = "预算") -> int:
    """预算输入 → 整数 tokens。空白/非法/负数都当场报错，绝不静默变成「不限」。"""
    text = str(raw if raw is not None else "").strip()
    if not text:
        raise ValueError(f"{label}不能为空（填 0 = 不限，会失去费用闸）")
    try:
        value = int(text, 10)
    except ValueError as exc:
        raise ValueError(f"{label}必须是整数（0 = 不限）") from exc
    if value < 0:
        raise ValueError(f"{label}必须 ≥ 0（0 = 不限）")
    return value


def channel_state(ai: dict | None) -> dict:
    """两通道可用性：总开关关 → 都不出入口；直连还要看有没有 Key。"""
    data = normalize(ai)
    enabled = bool(data["enabled"])
    keyed = bool(data["key"])
    reason = ""
    if not enabled:
        reason = "AI 功能总开关关着（设置 → AI）"
    elif not keyed:
        reason = "直连 API 未配置 Key（设置 → AI；协同初排不需要 Key）"
    return {
        "enabled": enabled,
        "coop": enabled,
        "direct": enabled and keyed,
        "reason": reason,
        "key_label": mask_key(data["key"]),
    }


def fulltext_mode(enabled: bool, scope: str) -> str:
    """--coop-fulltext 的取值：没勾就是空串（不传该参数）。"""
    if not enabled:
        return ""
    return FULLTEXT_FULL if scope == FULLTEXT_FULL else FULLTEXT_CONTEXT


def plan_mode(*, direct: bool, coop: bool, replies, stage: str) -> str:
    """判定本次运行走哪一种（纯函数，界面与测试共用）。"""
    if direct and coop:
        raise ValueError("「AI 协同初排」与「AI 核实（直连）」只能二选一")
    if direct:
        return MODE_DIRECT
    if not coop:
        return MODE_PLAIN
    if reply_paths(replies):
        if not str(stage or "").strip():
            raise ValueError("导入回填前先选好暂存目录：先运行第一段，或在面板里点「自动发现」")
        return MODE_PH2
    return MODE_PH1


def reply_paths(values) -> list[str]:
    """回填文件：多行文本/列表 → 去空去重后的路径列表（顺序保持输入顺序）。"""
    items = values.splitlines() if isinstance(values, str) else list(values or [])
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip().strip('"').strip("'")
        if not text:
            continue
        marker = os.path.normcase(os.path.abspath(text))
        if marker in seen:
            continue
        seen.add(marker)
        out.append(text)
    return out


# ── 直连上传预览：字段白名单 ────────────────────────────────────────────
# 顺序即界面顺序。这里只放**元数据**；正文/脚注/整篇稿件不在白名单里。
UPLOAD_FIELDS = (
    ("source_name", "源稿文件名"),
    ("refs_count", "参考文献条数"),
    ("bib_keys", "待核实的文献键"),
    ("taskbook_brief", "核实规范与回答格式（任务书摘要）"),
    ("budget_tokens", "单篇预算（tokens）"),
)
UPLOAD_ALWAYS = "正文、脚注与整篇稿件：不发送（只发上列元数据）"
FORBIDDEN_FIELDS = ("body", "fulltext", "manuscript", "content", "abstract", "正文", "全文")


def upload_preview(values: dict | None) -> list[tuple[str, str]]:
    """白名单过滤后的预览行；未知键（含正文）一律丢弃。"""
    data = values or {}
    rows: list[tuple[str, str]] = []
    for key, label in UPLOAD_FIELDS:
        value = data.get(key)
        if value is None or value == "" or value == [] or value == 0:
            continue
        if isinstance(value, (list, tuple)):
            shown = "、".join(str(item) for item in value[:8])
            text = shown + ("…" if len(value) > 8 else "")
        else:
            text = str(value)
        rows.append((label, text))
    return rows


def forbidden_upload(values: dict | None) -> str:
    """返回第一个被塞进预览的禁发字段名（空串 = 干净）。"""
    for key in FORBIDDEN_FIELDS:
        if (values or {}).get(key):
            return key
    return ""


def preview_text(values: dict | None) -> str:
    rows = upload_preview(values)
    body = "\n".join(f"{label}：{text}" for label, text in rows)
    return body or "（本次没有可发送的元数据）"


# ── 协同暂存目录发现（只读） ─────────────────────────────────────────────
def same_path(a, b) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def load_state(stage_dir: str) -> dict | None:
    path = os.path.join(str(stage_dir or ""), STATE_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def status_text(phase: str) -> str:
    return {
        "ph1": STATUS_PH1,
        "ph2": STATUS_PH2,
        "done": STATUS_DELIVERED,
    }.get(str(phase or ""), STATUS_UNKNOWN)


def stats_summary(state: dict | None) -> dict:
    """第二段统计（引擎写进状态 JSON 的 `stats`；旧状态没有就回退第一段口径）。"""
    stats = (state or {}).get("stats")
    keys = (state or {}).get("keys") or {}
    if not isinstance(stats, dict):
        bib = keys.get("bib") or []
        low = keys.get("low_confidence") or []
        return {
            "available": False,
            "text": f"第一段：文献 {len(bib)} 条、低置信 {len(low)} 项待核实（尚未导入回填）",
        }
    bib_total = _int_or(stats.get("bib_total"), 0)
    bib_replied = _int_or(stats.get("bib_replied"), 0)
    low_total = _int_or(stats.get("low_total"), 0)
    low_replied = _int_or(stats.get("low_replied"), 0)
    low_applied = _int_or(stats.get("low_applied"), 0)
    pending_manual = _int_or(stats.get("low_pending_manual"), 0)
    applied_label = "已应用" if low_applied else "仅记录"
    return {
        "available": True,
        "bib_total": bib_total,
        "bib_replied": bib_replied,
        "bib_pending": _int_or(stats.get("bib_pending"), 0),
        "low_total": low_total,
        "low_replied": low_replied,
        "low_applied": low_applied,
        "low_pending_manual": pending_manual,
        "text": (f"文献回填 {bib_replied}/{bib_total}、低置信回复 {low_replied} 条"
                 f"（{applied_label} {low_applied} 条，待人工 {pending_manual} 条）"),
    }


class CoopStage:
    """一个暂存目录的只读视图（状态 JSON 缺失时 `found` 为 False）。"""

    def __init__(self, stage_dir: str, state: dict | None = None) -> None:
        self.stage_dir = os.path.abspath(str(stage_dir))
        if state is None:
            state = load_state(self.stage_dir) or {}
        self.state = state if isinstance(state, dict) else {}

    @property
    def found(self) -> bool:
        return bool(self.state)

    @property
    def params(self) -> dict:
        params = self.state.get("params")
        return params if isinstance(params, dict) else {}

    @property
    def stem(self) -> str:
        stem = str(self.params.get("stem") or "")
        if stem:
            return stem
        name = os.path.basename(self.stage_dir)
        return name[: -len(STAGE_SUFFIX)] if name.endswith(STAGE_SUFFIX) else name

    @property
    def phase(self) -> str:
        return str(self.state.get("phase") or "")

    @property
    def status_text(self) -> str:
        return status_text(self.phase) if self.found else STATUS_UNKNOWN

    @property
    def taskbook(self) -> str:
        return str(self.state.get("taskbook") or "")

    @property
    def final_dir(self) -> str:
        return str(self.state.get("final_dir") or "")

    @property
    def state_path(self) -> str:
        return os.path.join(self.stage_dir, STATE_NAME)

    @property
    def suggestions_md(self) -> str:
        return os.path.join(self.stage_dir, f"{self.stem}_AI建议.md")

    @property
    def suggestions_jsonl(self) -> str:
        return os.path.join(self.stage_dir, f"{self.stem}_AI建议.jsonl")

    @property
    def summary(self) -> dict:
        return stats_summary(self.state)


def stage_dirs(out_root: str) -> list[str]:
    root = str(out_root or "").strip()
    if not root or not os.path.isdir(root):
        return []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    found = [
        os.path.abspath(os.path.join(root, name))
        for name in names
        if name.endswith(STAGE_SUFFIX) and os.path.isdir(os.path.join(root, name))
    ]
    return sorted(found)


def _state_mtime(stage_dir: str) -> float:
    try:
        return os.path.getmtime(os.path.join(stage_dir, STATE_NAME))
    except OSError:
        return 0.0


def discover_stages(out_root: str, *, source: str = "") -> list[CoopStage]:
    """在输出根目录下找当前源稿的暂存目录（最近改动的排前面）。"""
    root = str(out_root or "").strip()
    if not root or not source:
        return []
    found: list[CoopStage] = []
    for stage_dir in stage_dirs(root):
        state = load_state(stage_dir)
        if not state:
            continue
        if not same_path(((state.get("source") or {}).get("path")), source):
            continue
        found.append(CoopStage(stage_dir, state))
    found.sort(key=lambda stage: _state_mtime(stage.stage_dir), reverse=True)
    return found


def discover_stage(out_root: str, *, source: str = "") -> CoopStage | None:
    found = discover_stages(out_root, source=source)
    return found[0] if found else None


def resolve_stage(stage_dir: str, *, out_root: str = "", source: str = "") -> CoopStage | None:
    """手填优先；没填就按 (输出根目录, 源稿) 自动发现最近一个。"""
    text = str(stage_dir or "").strip()
    if text:
        stage = CoopStage(text)
        return stage if stage.found else None
    return discover_stage(out_root, source=source)


# ── 预算闸命中 → 界面口径 ───────────────────────────────────────────────
BUDGET_NOTICE = "预算不足，未发起请求（引擎在发出任何请求前退出；调高预算或改小核实范围）"
BUDGET_MARKERS = ("预算闸", "预算不足")


def budget_notice(lines) -> str:
    """引擎的预算闸消息 → 统一的中文提示；没命中返回空串。"""
    for line in lines or []:
        text = str(line or "")
        if any(marker in text for marker in BUDGET_MARKERS):
            return BUDGET_NOTICE
    return ""
