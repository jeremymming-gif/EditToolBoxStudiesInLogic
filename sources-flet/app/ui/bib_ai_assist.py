"""引用库 · AI 处理 Bib（DESIGN §3.2 / §4 / §9 / §10，VIEW_KEY="bib_ai"）。

自包含视图：新增文件，不改任何既有页面。两个入口（初排转换 / 引用库 Bib）共用
本视图，切换只改文案与参数提示，不改流程（DESIGN §3.2）。

硬边界（DESIGN §8 / §10）：
* 本视图**只取值、调用引擎桥、显示结构化结果**；不解析 .bib、不识别引用、
  不拼补条目、不写任何用户文件。写目标 Bib 由引擎完成，页面只显示结果。
* 执行范围（生成任务书前）与采纳范围（引擎返回后）是**两组独立勾选**，分别由
  不同控件对象承载；「文字纠错」在两组里都默认关闭（采纳组每次重建都从 False 起）。
* 「一键粘贴正文」读剪贴板里的**正文**，与「粘贴/浏览路径」是两个不同控件，
  互不冒充（DESIGN §10 / N04）。
* 宿主协议（UI-REWORK-DESIGN §2）：本视图只接受满足宿主协议的宿主（tokens t、
  app 服务、flet page、刷新/调度/文件选择/通知/设置跳转）。构造时校验；
  绝不把 .t/.app 塞到裸 Flet Page 上，也不靠吞 AttributeError 假装能画。
* 引擎桥缺函数、引擎非 0、结果无法解析 → 如实报失败，**不显示任何成功态**。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import uuid
from collections.abc import Callable

import flet as ft

from ui import bib_shell as S
from ui import components as C

#: 宿主登记用（`bib_host.VIEW_SPECS` 的键）。接线是宿主侧的事，这里只提供名字。
VIEW_KEY = "bib_ai"
TITLE = "AI 处理 Bib"
#: 视图能力：读写（应用步骤会改动用户选定的目标 Bib，由引擎执行）。
ROLE = "readwrite"
#: `S.page_shell` 只认 readonly/write/stale；readwrite 落到「写」。
ROLE_CHIP = "write"

#: 用途：同一个视图服务两个入口，切入口只改文案与参数提示。
PURPOSES = (("initial", "初排转换"), ("library", "引用库 Bib"))
PURPOSE_LABELS = dict(PURPOSES)

#: 通道：Chat（把任务书交给 AI，再把结果贴/存回来）或 API（本机直接调）。
CHANNELS = (("chat", "等待 AI 结果（Chat）"), ("api", "直接调 API"))
CHANNEL_LABELS = dict(CHANNELS)

#: 处理类别（Bib 页只出现文献类别，DESIGN §4）。
CATEGORIES = (("verify", "文献核实"), ("proofcheck", "初排检查"), ("textfix", "文字纠错"))
CATEGORY_LABELS = dict(CATEGORIES)

#: 视图内部键 → 引擎类别名。**两套词表**：界面用 `verify/proofcheck/textfix`，
#: 引擎只认 `Engine/slcn/ai_workflow.py` 的 `CATS`（`bib_verify/typeset/text`）。
#: 不翻译就等于给引擎递非法类别，CLI 的 `_scope_of` 会整条退出 2（问题 4 M2）。
CATEGORY_MAP = {"verify": "bib_verify", "proofcheck": "typeset", "textfix": "text"}

#: 工作区默认落点：目标 Bib 同目录下的隐藏子目录。
#: 为什么是隐藏名 `.aiw`：`Engine/slcn/clean.py` 的交付清理按「名字含 `_AI` /
#: 以 `.ai前` 结尾 / 具名清单」删文件，隐藏点目录里的状态与 undo 副本不在命中面上
#: （验收 B2 已按此落地），所以换一台机器、清一次交付目录仍能续跑/撤销。
WORK_DIR_NAME = ".aiw"

#: 执行组默认（DESIGN §4）：文字纠错默认**关闭**。
EXECUTE_DEFAULTS = {"verify": True, "proofcheck": True, "textfix": False}
#: 采纳组默认（DESIGN §4）：文字纠错**永远**默认不勾。
ACCEPT_DEFAULTS = {"verify": True, "proofcheck": True, "textfix": False}

#: 统一阶段（DESIGN §9）：准备中 → 等待结果 → 可预览 → 已应用；另有阻断/失效。
PHASES = ("idle", "preparing", "waiting", "preview", "applied", "blocked", "stale")
PHASE_LABELS = {
    "idle": "准备中",
    "preparing": "准备中",
    "waiting": "等待 AI 结果",
    "preview": "可预览",
    "applied": "已应用",
    "blocked": "阻断",
    "stale": "已失效",
}

#: 引擎阶段（`slcn.ai_workflow` 的 `PH_*`）→ 本视图阶段。
#: 两份词表**故意不同**：视图要「可预览」这种操作态，引擎要 `previewed` 这种记录态。
#: 少了这层映射，`status` 一回填就把 `phase` 写成 `previewed`，而 `can_apply()` 只认
#: `preview` —— 结果是「引擎说能应用、界面按钮永远灰着」。
ENGINE_PHASE_MAP = {
    "prepared": "waiting",
    "requesting": "waiting",
    "imported": "waiting",
    "previewed": "preview",
    "applied": "applied",
    "delivered": "applied",
    "undone": "stale",
    "blocked": "blocked",
    "cancelled": "idle",
    "stale": "stale",
}


def phase_from_engine(phase: str) -> str:
    """引擎阶段 → 视图阶段；认不出的**原样返回**（宁可显示生词，也不假装成功）。"""
    raw = str(phase or "").strip()
    return ENGINE_PHASE_MAP.get(raw, raw)


#: 面板四阶段（方案 B：按操作时序分块折叠，见 ANALYSIS-5-layout §2.2）。
#: 与 `PHASES` 的分工：`PHASES` 是引擎/状态机词表，这里只是**展示分组**；
#: 两块词表由下面的纯函数单向映射，状态机仍是 `self.phase`，不另造。
STAGE_ORDER = ("prepare", "waiting", "review", "done")
STAGE_LABELS = {
    "prepare": "① 准备",
    "waiting": "② 等待结果",
    "review": "③ 检查采纳",
    "done": "④ 完成",
}


def stage_for(phase: str, *, imported: bool = False) -> str:
    """视图阶段 → 当前展开的阶段块（纯展示映射，不改 `self.phase`）。

    - `idle/preparing` → 准备
    - `waiting` → 等待结果；已导入/取回结果 → 检查采纳（下一步就是生成拟应用结果）
    - `preview/blocked` → 检查采纳（采纳范围、对比、应用都在这块里完成）
    - `applied/stale` → 完成（撤销；失效后重新生成）

    认不出的阶段退回「准备」：与 `phase_label()`「宁可显示生词，也不假装成功」
    的取向一致——界面可以退一步，但不能假装流程已经往前走了。
    """
    key = str(phase or "").strip()
    if key in ("", "idle", "preparing"):
        return "prepare"
    if key == "waiting":
        return "review" if imported else "waiting"
    if key in ("preview", "blocked"):
        return "review"
    if key in ("applied", "stale"):
        return "done"
    return "prepare"


def primary_slot(phase: str, channel: str = "chat", imported: bool = False
                 ) -> tuple[str, str]:
    """当前阶段的唯一主操作：`(所属阶段块, 动作 key)`。

    `_parts()` 每次重画只把**等于本返回值**的按钮交给 `C.primary_button`，其余
    一律 `C.secondary_button`——任何时刻树中恰 1 个 FilledButton（DESIGN §3/§6）。

    `stale` 取「生成拟应用结果」而不是「撤销」：失效态的 `can_undo()` 为假，
    若主按钮绑撤销，页面上唯一被强调的按钮会是灰的，也没有重新生成的入口；
    失效横幅给出的下一步本来就是「重新生成拟应用结果」。
    """
    key = str(phase or "").strip()
    if key in ("", "idle", "preparing"):
        return ("waiting", "request") if channel == "api" else ("prepare", "prepare")
    if key == "waiting":
        if imported:
            return ("review", "preview")
        return ("waiting", "request") if channel == "api" else ("waiting", "import")
    if key in ("preview", "blocked"):
        return ("review", "apply")
    if key == "applied":
        return ("done", "undo")
    if key == "stale":
        return ("done", "preview")
    return ("prepare", "prepare")


#: 引擎桥（engine_bridge）里本视图会用到的 7 个语义；缺任何一个都不许假装能跑。
BRIDGE_FUNCS = ("prepare_call", "import_call", "preview_call", "apply_call",
                "undo_call", "status_call", "run_call")

#: 视图内部名 → `services.engine_bridge` 里的真实函数名。
#: 为什么不直接叫 `prepare_call`：`ui/normalize.py` 已经有一个同名的
#: `preview_call`（且被 `test_b109_w2a` 使用），裸名会互相覆盖；桥侧统一加
#: `ai_` 前缀，这里做一次显式映射，页面逻辑不必知道前缀。
BRIDGE_NAMES = {
    "prepare_call": "ai_prepare_call",
    "import_call": "ai_import_call",
    "preview_call": "ai_preview_call",
    "apply_call": "ai_apply_call",
    "undo_call": "ai_undo_call",
    "status_call": "ai_status_call",
    # `request` 是 API 通道专用，不在 BRIDGE_FUNCS 的必备集里；但名字照样要映射，
    # 否则真桥（`ai_request_call`）会被当成「缺少 request_call」而拒绝执行。
    "request_call": "ai_request_call",
    "run_call": "run_call",
    # R3 Task5 只读内容桥 / 交付桥：不进 BRIDGE_FUNCS 必备集（旧桥/假桥可缺），
    # 但名字照样映射，让「复制任务书全文」走真实桥而不是从日志拼文本。
    "taskbook_call": "ai_taskbook_call",
    "deliver_call": "ai_deliver_call",
}

#: 六个动作按钮（文案固定，接线方按这些字符串断言/翻译）。
ACTION_LABELS = {
    "prepare": "出任务书",
    "request": "直接请求（API）",
    "import": "导入结果",
    "preview": "生成拟应用结果",
    "apply": "应用到目标 Bib",
    "undo": "撤销本次写入",
    "refresh": "刷新状态",
}

R3_ACTION_LABELS = {
    "prepare": "生成任务书",
    "deliver_original": "按原稿交付…",
    "restart": "更换任务",
    "replace_reply": "重新导入结果",
}

BLOCKED_TITLE = "有阻断项：现在不能应用"
BRIDGE_PREFIX = "引擎桥未就绪：缺少 "

#: 冲突三选一的显示文案（键 = 合同 decisions 的取值，逐字对应）。
#: 合同 CONTRACT.md §2：`decisions{cf-…: "library"|"reply"|"original"}`，
#: 缺省一律 `original`（保留原值 = 最保守），界面绝不替用户预选库值/回件值。
DECISION_CHOICES = (
    ("library", "用库值"),
    ("reply", "用回件值"),
    ("original", "保留原值"),
)
DECISION_DEFAULT = "original"

#: 库不可用时的「人话原因」（合同 §1 的 state 取值 → 给用户看的句子）。
#: 关键纪律：库不可用**不是**「零冲突」，必须把具体原因显示出来，否则用户会
#: 以为「引擎比过了，一条冲突都没有」——那是把没做过的检查说成做过了。
LIBRARY_STATE_TEXT = {
    "ok": "库已比对",
    "unconfigured": "没有配置共享库（本次没有比对库值）",
    "missing": "共享库不存在或路径不对（本次没有比对库值）",
    "unreadable": "共享库读不出来（权限或格式问题，本次没有比对库值）",
    "failed": "共享库比对失败（本次没有比对库值）",
    "skipped": "本次跳过了库比对",
}

#: 冲突区标题与说明（合同 §4.1：仅 conflicts 非空才出现）。
CONFLICT_TITLE = "冲突裁决（库值 / 回件值 / 原条目不一致）"

#: 源稿字段的提示随用途变化（同一控件、不同文案；流程不变）。
SOURCE_HINTS = {
    "initial": "初排稿 .tex（可空：留空则由引擎按目标 Bib 定范围）",
    "library": "源 .bib（可空：留空则默认处理目标 Bib 自身）",
}
SOURCE_LABELS = {"initial": "初排稿（.tex）", "library": "源 Bib（.bib）"}


# ── 纯函数（可单独测；不碰文件、不碰引擎）────────────────────────────────────
def phase_label(phase: str) -> str:
    """阶段键 → 中文文案；未知键如实回显，不冒充某个已知阶段。"""
    return PHASE_LABELS.get(str(phase or ""), str(phase or "") or "—")


def _bridge_fn(bridge, name: str):
    """按 `BRIDGE_NAMES` 取函数：先找真实名（`ai_` 前缀），再退回裸名。

    退回裸名是为了兼容两种桥：已经按 `ai_prepare_call` 命名的产品桥，和只认
    裸名的假桥/旧桥。**报缺项时一律用内部名**（`prepare_call`），界面与测试
    看到的是同一套名字。
    """
    if bridge is None:
        return None
    real = BRIDGE_NAMES.get(name, name)
    fn = getattr(bridge, real, None)
    if fn is None and real != name:
        fn = getattr(bridge, name, None)
    return fn


def missing_bridge(bridge, names=None) -> list[str]:
    """检查引擎桥缺哪些可调用函数（缺 run_call 也算缺，否则跑不起来）。"""
    wanted = tuple(names or BRIDGE_FUNCS)
    if bridge is None:
        return list(wanted)
    return [name for name in wanted if not callable(_bridge_fn(bridge, name))]


def bridge_error_text(missing) -> str:
    """缺函数的统一文案：`引擎桥未就绪：缺少 a、b`（按传入顺序，不排序）。"""
    names = [str(name) for name in missing or ()]
    return f"{BRIDGE_PREFIX}{'、'.join(names)}" if names else ""


def _int_of(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_status(lines) -> dict:
    """解析引擎 `status` 输出的 `key=value` 行（只做展示口径，不碰 bib 内容）。

    支持：phase / task_id / keys / entries / preview_id / backup / stale /
    blocked（blocked 可用 `；` 或 `;` 分隔多条）。无法识别的行忽略；没有关键
    字段的行也忽略，宁可少显示也不猜。
    """
    out: dict = {}
    for raw in lines or ():
        text = str(raw or "").strip()
        if not text or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip().lower().replace("-", "_")
        value = value.strip()
        if not key:
            continue
        if key in ("phase", "task_id", "preview_id", "backup", "candidate_bib",
                   "candidate_bib_sha256", "work_dir", "taskbook"):
            out[key] = value
        elif key in ("keys", "key_count"):
            out["keys"] = _int_of(value, 0)
        elif key in ("entries", "entry_count"):
            out["entries"] = _int_of(value, 0)
        elif key == "stale":
            out["stale"] = value.lower() in ("1", "true", "yes", "on")
        elif key == "blocked":
            items = [item.strip() for item in value.replace("；", ";").split(";")]
            out["blocked"] = [item for item in items if item]
    return out


def parse_taskbook_path(lines) -> str:
    """从引擎人读输出里取任务书路径（`任务书：<path>`），取不到返回 ""。

    引擎 CLI 在 prepare 成功时打印 `任务书：<work_dir>/<stem>_AI任务书.md`
    （全角冒号）；`EngineCall` 上并没有路径属性，所以界面只能从日志行取。
    只认整行前缀，避免把正文里偶然出现的「任务书：」当成路径；取不到就返回
    空串，由调用方如实写「路径见引擎日志」，绝不拼一个猜的路径。
    """
    for raw in reversed(list(lines or ())):
        text = str(raw or "").strip()
        for prefix in ("任务书：", "任务书:"):
            if text.startswith(prefix):
                return text[len(prefix):].strip()
    return ""


#: 合同的三个裁决取值；顺序即界面里的显示顺序（用库值 → 用回件值 → 保留原值）。
DECISION_VALUES = tuple(key for key, _label in DECISION_CHOICES)


def normalize_decision(value) -> str:
    """把任意输入收敛成合同允许的三个裁决值，认不出就回 `original`。

    为什么默认是 `original`：合同 §2 写死「缺省裁决一律 original」，也就是
    「什么都不选 = 保留作者原条目」。界面和解析都必须走同一条兜底，否则一个
    拼错的裁决值会悄悄变成「用库值」——那等于替用户改了稿子。
    """
    text = str(value or "").strip().lower()
    return text if text in DECISION_VALUES else DECISION_DEFAULT


def parse_library(payload) -> dict:
    """从 status/preview 的 JSON 里取 `library` 块，缺字段按「没比对」补齐。

    返回的 dict 永远有 `checked/state/path/sha256/matched/conflicts/
    skipped_reason` 七个键，调用方不必再判空。**关键**：拿不到 `library`
    时 `checked=False`、`state="skipped"`——不能默认成 `checked=True` 且
    `conflicts=0`，那会把「没做库比对」显示成「比过了，零冲突」。
    """
    raw = payload.get("library") if isinstance(payload, dict) else None
    block = raw if isinstance(raw, dict) else {}
    checked = block.get("checked")
    if not isinstance(checked, bool):
        checked = bool(block.get("checked"))
    state = str(block.get("state") or "").strip().lower()
    if state not in LIBRARY_STATE_TEXT:
        state = "ok" if checked else "skipped"
    return {
        "checked": checked,
        "state": state,
        "path": str(block.get("path") or ""),
        "sha256": str(block.get("sha256") or ""),
        "matched": _int_of(block.get("matched"), 0),
        "conflicts": _int_of(block.get("conflicts"), 0),
        "skipped_reason": str(block.get("skipped_reason") or ""),
    }


def parse_conflicts(payload) -> list:
    """取合同里的 `conflicts` 列表；结构不全的条目丢掉，不猜字段。

    每条只保留合同写明的键；`choices` 缺省按 `DECISION_VALUES` 补，
    `default` 认不出就 `original`。`conflict_id` 缺失或不是 `cf-` 开头的
    条目直接丢弃——没有 ID 就没法把裁决回传给引擎（合同 §2：未知 ID 会被
    引擎以退出码 2 拒绝），与其让用户点了没反应，不如不显示。
    """
    raw = payload.get("conflicts") if isinstance(payload, dict) else None
    out = []
    for item in raw or ():
        if not isinstance(item, dict):
            continue
        cid = str(item.get("conflict_id") or "").strip()
        if not cid or not cid.startswith("cf-"):
            continue
        choices = [normalize_decision(ch) for ch in (item.get("choices") or DECISION_VALUES)]
        choices = [ch for ch in DECISION_VALUES if ch in choices]
        if not choices:
            choices = list(DECISION_VALUES)
        conflict = {
            "conflict_id": cid,
            "key": str(item.get("key") or ""),
            "field": str(item.get("field") or ""),
            "original": str(item.get("original") or ""),
            "library": str(item.get("library") or ""),
            "reply": str(item.get("reply") or ""),
            "choices": choices,
            "default": normalize_decision(item.get("default")),
            "group": str(item.get("group") or ""),
        }
        raw_expressions = item.get("expressions")
        if isinstance(raw_expressions, dict):
            expressions = {
                side: raw_expressions[side]
                for side in ("original", "reply")
                if isinstance(raw_expressions.get(side), str)
            }
            if expressions:
                conflict["expressions"] = expressions
        out.append(conflict)
    return out


def parse_decisions(payload) -> dict:
    """取合同里的 `decisions` 映射（只留 cf- 开头、值合法的项）。"""
    raw = payload.get("decisions") if isinstance(payload, dict) else None
    out = {}
    for cid, value in (raw or {}).items() if isinstance(raw, dict) else ():
        key = str(cid or "").strip()
        if key.startswith("cf-"):
            out[key] = normalize_decision(value)
    return out


def decision_for(conflict: dict, decisions: dict | None = None) -> str:
    """某条冲突当前生效的裁决：用户选过的优先，否则用条目自带的 default。"""
    cid = str((conflict or {}).get("conflict_id") or "")
    chosen = (decisions or {}).get(cid)
    if chosen:
        return normalize_decision(chosen)
    return normalize_decision((conflict or {}).get("default"))


def conflict_decision_map(conflicts, decisions=None) -> dict:
    """把「条目自带默认值 + 用户已选」合成一份完整裁决表（preview 调用下发给引擎）。

    每条冲突都必须有一个值：引擎侧缺省也是 original，两边一致；这里显式补齐
    是为了让下发的 JSON 自带全部 ID，出问题能一眼看出「这条我选了什么」。
    """
    return {
        str(item.get("conflict_id")): decision_for(item, decisions)
        for item in (conflicts or ())
        if item.get("conflict_id")
    }


def library_lines(library: dict) -> list:
    """库状态 → 界面要显示的说明行（第一行永远是「可用/不可用」的结论）。

    库不可用时返回「原因」而不是「零冲突」：合同 §1 明确禁止把没比对过的
    情况显示成 `conflicts: []`。原因优先取引擎给的 `skipped_reason`
    （人话），没有就用 state 的内置文案。
    """
    block = library or {}
    state = str(block.get("state") or "").strip().lower()
    checked = bool(block.get("checked"))
    if not checked:
        reason = str(block.get("skipped_reason") or "").strip()
        text = LIBRARY_STATE_TEXT.get(state) or "库状态未知（本次没有比对库值）"
        return [text, f"原因：{reason}"] if reason else [text]
    lines = [LIBRARY_STATE_TEXT.get(state, "库已比对")]
    if block.get("matched"):
        lines.append(f"库匹配 {_int_of(block.get('matched'), 0)} 条")
    if block.get("conflicts"):
        lines.append(f"库冲突 {_int_of(block.get('conflicts'), 0)} 条")
    return lines


def _three_values(conflict: dict) -> list:
    """一条冲突的三个值，顺序固定为 原条目 / 库 / 回件（与裁决选项一一对应）。"""
    item = conflict or {}
    return [
        ("原条目", str(item.get("original") or "（空）")),
        ("库", str(item.get("library") or "（空）")),
        ("回件", str(item.get("reply") or "（空）")),
    ]


#: 左侧「冻结稿件版 Bib」身份判定结果里 `kind` 的三种取值。
FROZEN_OK = "frozen"          #: 引擎给了快照且 sha 对得上，左侧就是冻结稿件版
FROZEN_LEGACY = "legacy"      #: 旧 /1 任务没有快照，退化用 source_bib/target_bib
FROZEN_MISSING = "missing"    #: 快照声称存在却对不上，或三条都取不到 → 禁用对比


def file_sha256(path: str) -> str:
    """文件字节的 sha256（十六进制小写）；读不到返回空串。

    自己算而不借 `editor_bridge.file_sha256`：`_can_diff()` 在**每次刷新**都要
    判身份，而 `editor_bridge` 在视图里是惰性 import（打包缺模块时不能让刷新崩）。
    """
    digest = hashlib.sha256()
    try:
        with open(str(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def resolve_frozen_bib(frozen_path: str, frozen_sha: str,
                       fallbacks: tuple = ()) -> tuple:
    """左侧「冻结稿件版 Bib」到底用哪一份 —— 合同 §4.2 / v1.1 的四条判据。

    返回 `(path, kind, message)`：

    1. `frozen_path` 非空、文件在、sha 与引擎冻结时一致 → `(path, FROZEN_OK, "")`。
    2. `frozen_path` 非空但文件不在 / sha 不符 / 引擎没给 sha（身份核不出来）→
       `("", FROZEN_MISSING, 原因)`，**绝不**退化到 `source_bib`/`target_bib`
       去冒充冻结版：那样左侧是谁就成了未知数，用户以为在核冻结稿其实不是。
    3. `frozen_path` 为空（旧 /1 任务，合同 v1.1 明写返回空串）→ 按
       `source_bib` → `target_bib` 顺序退化，但 `message` 必须说清「旧任务没有
       冻结快照」，界面把它写进提示，**不静默**。
    4. 三条都取不到 → `("", FROZEN_MISSING, 原因)`。

    退化只读：本函数只 `os.path.isfile` + 读字节算 sha，不改任何文件。
    """
    frozen = str(frozen_path or "").strip()
    if frozen:
        if not os.path.isfile(frozen):
            return ("", FROZEN_MISSING,
                    f"冻结的稿件版 Bib 不在了（{frozen}）：请重新出任务书再对比")
        expected = str(frozen_sha or "").strip().lower()
        actual = file_sha256(frozen)
        if not expected:
            return ("", FROZEN_MISSING,
                    f"引擎没有给出冻结 Bib 的 sha256（{frozen}），核不出左侧身份："
                    "请重新出任务书再对比")
        if actual != expected:
            return ("", FROZEN_MISSING,
                    f"冻结的稿件版 Bib 已变动（{frozen}）：期望 sha {expected[:16]}…，"
                    f"实际 {actual[:16] or '读不出来'}…；请重新出任务书再对比")
        return (frozen, FROZEN_OK, "")
    for candidate in fallbacks:
        path = str(candidate or "").strip()
        if path and os.path.isfile(path):
            return (path, FROZEN_LEGACY,
                    f"旧任务没有冻结快照，左侧用 {path}（未冻结）：请自行确认这就是"
                    "稿件版 Bib；重新出任务书后可按 sha 核身份")
    return ("", FROZEN_MISSING,
            "找不到冻结的稿件版 Bib（也没有目标 Bib）：无法对比")


#: 引擎 `compile_status` 的三种取值 → 日志里给用户看的一句话（合同 §4.3）。
#: 引擎只有「编译成功 / 未要求编译 / 抛错」三种结果，没有「编译跳过」这个值；
#: 「跳过」= 用户没勾编译，如实说跳过，不冒充成功。
COMPILE_STATUS_TEXT = {
    "编译成功": "成功",
    "编译失败": "失败",
    "未要求编译": "跳过",
    "未要求导出原稿 PDF": "跳过",
}

DELIVERY_LOG_TAG = "AI 初排"


def compile_status_text(payload: dict) -> str:
    """引擎返回的编译状态 → 日志文案（成功 / 失败 / 跳过 / 引擎未报告）。

    不猜：`deliver` 失败时引擎只回 `error`，没有 `compile_status` 字段，
    这里就回「引擎未报告」；调用方按错误文本另外判定是否编译失败。
    """
    raw = str((payload or {}).get("compile_status") or "").strip()
    if not raw:
        return "引擎未报告"
    if raw in COMPILE_STATUS_TEXT:
        return COMPILE_STATUS_TEXT[raw]
    if "成功" in raw:
        return "成功"
    if "失败" in raw:
        return "失败"
    if "未要求" in raw or "跳过" in raw:
        return "跳过"
    return raw


def delivery_log_lines(*, task_id: str, final: str, compile_text: str,
                       ok: bool = True, error: str = "", use_original: bool = False) -> list:
    """完成态日志的三行（+ 失败时一行原因）：任务号 / 编译状态 / 最终路径。

    合同 §4.3 要求「成功与失败都写底部运行日志」，且日志里的最终路径必须与
    status/deliver 返回的 `final` 同源——所以路径只由调用方传入的那一个值渲染，
    这里不推导、不拼目录、不猜。
    """
    head = f"{DELIVERY_LOG_TAG} · "
    lines = [
        f"{head}任务号：{str(task_id or '').strip() or '（引擎未返回任务号）'}",
        f"{head}编译状态：{compile_text}",
    ]
    if ok:
        tail = "（未采纳 AI 结果）" if use_original else ""
        lines.append(f"{head}交付目录：{final or '（引擎未返回最终路径）'}{tail}")
    else:
        lines.append(f"{head}交付目录：（交付失败，引擎未返回最终路径）")
        lines.append(f"{head}失败原因：{str(error or '').strip() or '引擎未给出原因'}")
    return lines


async def _await_if_needed(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _invoke(fn, kwargs: dict, dropped: list | None = None):
    """按签名过滤 kwargs 再调用（与 `bib_shell.danger_confirm` 同一手法）。

    为什么要过滤：这 6 个桥函数的签名还没定稿（接线方后写），视图不能因为多传
    一个 `work_dir` 就整页崩。被过滤掉的参数名会写进 `dropped` 供排查；但那是
    签名差异、不是接线故障，页面不再把它当橙色警告上屏（真机噪音）。
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(**kwargs)
    params = sig.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(**kwargs)
    accepted = {k: v for k, v in kwargs.items() if k in params}
    if dropped is not None:
        dropped.extend(k for k in kwargs if k not in params)
    return fn(**accepted)


_BRIDGE_CACHE: list = []


def _load_bridge():
    """惰性取引擎桥。

    包根是 `app/`（`tests/conftest.py` 把 `app/` 插进 sys.path），所以同层各页
    都用 `from services import engine_bridge`；`..services` 在 `ui` 这一层是
    「超出顶层包」。两种写法都试一遍，取不到就返回 None（页面如实报未就绪）。
    """
    if _BRIDGE_CACHE:
        return _BRIDGE_CACHE[0]
    module = None
    try:
        from services import engine_bridge as module  # noqa: F401
    except Exception:  # noqa: BLE001 - 未安装/未接线：返回 None 由页面报错
        try:
            from ..services import engine_bridge as module  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            module = None
    _BRIDGE_CACHE.append(module)
    return module


def _refresh_workspace(view: BibAiAssistView) -> None:
    """刷新同页工作区。其他入口仍走原宿主重绘。"""
    refresh = getattr(view, "on_workspace_refresh", None)
    if refresh is None or not refresh():
        view._refresh()


class BibAiAssistView:
    """AI 处理 Bib：选范围 → 出任务书 → 导入结果 → 预览 → 应用 / 撤销。"""

    def __init__(self, page, *, bridge=None, purpose: str = "",
                 taskbook_version: int | None = None):
        # 宿主协议校验（UI-REWORK-DESIGN §2）：必须给带 tokens t / app 服务的宿主，
        # 绝不接受裸 Flet Page；缺服务在构造时就把话说清楚，不靠绘制时吞异常。
        _tokens = getattr(page, "t", None)
        if _tokens is None:
            raise TypeError(
                "BibAiAssistView 需要带 tokens t 的工具箱宿主；"
                f"收到 {type(page).__name__}（裸 Flet Page 没有 .t）。"
                "请传 SinglePage/BibHost 或显式适配器（UI-REWORK-DESIGN §2）")
        if getattr(page, "app", None) is None:
            raise TypeError(
                "BibAiAssistView 需要带 app 服务（store/notes_repo/copy_text/...）的宿主；"
                f"收到 {type(page).__name__}。请传 SinglePage/BibHost 或显式适配器")
        self.page = page
        self._bridge = bridge
        self.on_workspace_refresh: Callable[[], bool] | None = None

        # 两组独立选择（DESIGN §4）：execute 决定引擎做什么，accept 决定采纳什么。
        # `purpose` 由宿主传入（DESIGN §3.1 两个入口）：初排页给 "initial"，
        # 引用库页给 "library"。不传时保持旧默认，避免破坏既有调用点。
        self.purpose = purpose if purpose in PURPOSE_LABELS else PURPOSES[0][0]
        self._initial_call = getattr(page, "build_ai_initial_call", None)
        self._r3 = (taskbook_version == 3 if taskbook_version is not None
                    else purpose == "library" or callable(self._initial_call))
        self._parent_paths: tuple[str, str] | None = None
        self._original_final_dir = ""
        self._original_dialog = None
        self._initial_open_stage = ""
        self._external_import_draft: tuple | None = None
        self._reply_revision = 0
        self._initial_operation = ""
        self._initial_action_pending = False
        self._compare_paths: tuple[str, ...] = ()
        self._compare_feedback = ""
        self._review_notes: list[str] = []
        self._category_reports: dict = {}
        self._text_items: dict[str, str] = {}
        self._skip_items: set[str] = set()
        self._preview_dirty = False
        self._final_dir = ""
        #: 交付完成态（合同 §4.3）：成功与失败都记，日志与状态卡同源。
        #: `delivery_ok` 区分「真交付成功」与「交付失败但有错误原因」——成功横幅
        #: 只在 delivery_ok 且 final 与 status 一致时出现，绝不因为「没报错」就报成功。
        self.delivery_ok = False
        self._operation_receipt: dict = {}
        self._disclosure_open: dict[str, bool] = {}
        self.delivery_compile = ""
        self.delivery_error = ""
        self.compile_fix_eligible: dict = {}
        self.compile_fix_notice = ""
        self._compile_fix_row = None
        self._compile_fix_prepared = None
        self._compile_fix_pdf_opened = None
        #: 本次交付写进底部运行日志的那几行（与页面 LogConsole 同一份文本）。
        #: 保留一份是为了让测试与「日志 == 状态」的核对有可断言的对象。
        self.delivery_log: list[str] = []
        self._has_active_reply = False
        self.channel = CHANNELS[0][0]
        self.execute = dict(EXECUTE_DEFAULTS)
        self.accept = dict(ACCEPT_DEFAULTS)

        # 用户显式选择的输入（页面自己不写任何文件）。
        self.source_path = ""
        self.target_bib = ""
        self.bib_out = ""
        #: 可选关联正文（.tex）：只用来算引用键闭合与文字纠错基线，**永不回写**。
        self.tex_path = ""
        #: 正文交付候选的写回目标（初排才有意义）；空 = 正文只报告不写回。
        self.tex_out = ""
        self.result_path = ""
        self.work_dir = ""

        self._enforce_library_boundaries()

        # AI 结果正文（与"路径粘贴"是两个不同控件）。
        self.body_text = ""

        # R3 Task5：只读内容桥的竞态防护。任何会改变「复制对象」的动作
        #（重开任务、切工作区、切 task_id、prepare 成功重出任务书）都递增
        # request_seq 并清掉缓存，让在飞的异步复制结果作废——旧任务全文不得
        # 被复制到新任务的剪贴板里。
        self.request_seq: int = 0
        self._taskbook_cache = None

        # 流程状态
        self.phase = "idle"
        self.task_id = ""
        self.key_count = 0
        self.entry_count = 0
        self.blocked: list[str] = []
        self.preview_id = ""
        self.backup = ""
        #: 「在 VS Code 里对比」用的新版一侧：引擎 preview 写的候选文件（只读展示）。
        self.candidate_bib = ""
        self._candidate_bib_sha = ""
        #: 左侧「冻结稿件版 Bib」——**引擎冻结快照**的路径与 sha（合同 v1.1 的
        #: `status.frozen_bib = {"path","sha256"}`，来源 `state["snapshot"]["bib"]`）。
        #: 只有 sha 对得上才认这是冻结版；空串 = 旧 /1 任务，允许退化但必须明示。
        self._frozen_bib_path = ""
        self._frozen_bib_sha = ""
        #: 引擎 status 的 `source_bib`（冻结快照**对应的源路径**，不是快照本身）。
        #: 旧任务（无 `frozen_bib`）才退化用它，且界面必须写出「未冻结」。
        self._source_bib = ""
        self.task_text = ""
        #: 引擎实际写出的任务书路径（从日志「任务书：…」行解析；取不到为 ""）。
        self.taskbook_path = ""
        self.log: list[str] = []
        self.notice = ""
        # B-394：批量裁决的跳过提示状态。R3 路径不渲染通用 notice，必须在
        # _conflict_section 内以可见控件呈现，所以单独存这里，每次点击覆盖（含 0）。
        self._batch_skip_notice = 0
        self._batch_last_value = ""
        self.error = ""
        self.running = False
        self.imported = False
        self.undone = False
        self.scope_frozen = False

        # 库比对与冲突裁决（CONTRACT.md §1/§2）。三个字段的生命周期不同：
        # - `library`：每次 status/preview 都整体替换（引擎说什么就是什么）。
        # - `conflicts`：同上，随本次预览重来；旧预览失效后仍保留，方便用户
        #   看着上一次的冲突重新决定，但「应用/交付」必须重新 preview。
        # - `decisions`：**用户的选择**，只能由用户改或整任务清空，不能因为
        #   重新 preview 就被抹掉——否则用户点一次「生成拟应用结果」，
        #   刚做的裁决全丢，等于界面偷偷替他改回「保留原值」。
        self.library: dict = {}
        self.conflicts: list[dict] = []
        self.decisions: dict[str, str] = {}
        #: 引擎侧预览版本（合同 §2：裁决变更 → preview_revision+1 → 新 preview_id）。
        #: 界面用它识别「裁决变了但还没重新 preview」，从而禁用应用/交付/对比。
        self.preview_revision = 0

        # 桥/接线诊断
        self._bridge_missing: list[str] = []
        self._wiring_notes: list[str] = []

        # 控件/宿主缓存
        self._root: ft.Container | None = None
        self._refs: dict[str, list] = {
            "source_path": [], "target_bib": [], "tex_path": [], "tex_out": [],
            "result_path": [], "work_dir": []}
        self._execute_boxes: dict[str, ft.Checkbox] = {}
        self._accept_boxes: dict[str, ft.Checkbox] = {}
        #: 冲突行的三选一控件（`conflict_id` → RadioGroup），供测试与重绘定位。
        self._conflict_groups: dict[str, ft.Control] = {}
        self._paste_field: ft.TextField | None = None
        self._paste_button: ft.Control | None = None
        self._action_buttons: dict[str, ft.Control] = {}
        self._picking = False
        self._proc = None
        self._build_width = 0.0
        #: 当前阶段块（`stage_for()` 的结果）与唯一主操作所属块（`primary_slot()`）。
        #: 只影响渲染：块用 `visible` 折叠、按钮用 Filled/Outlined 区分强调。
        self._stage_boxes: dict[str, ft.Control] = {}
        self._primary_slot: tuple[str, str] = ("prepare", "prepare")

    # ── 宿主小工具（照 bib_normalize / bib_import 的用法）──────────────
    @property
    def t(self):
        tokens = getattr(self.page, "t", None)
        if tokens is None:
            # 裸 Flet Page 没有 t：构造时已校验，这里只保留清晰的失败语义。
            raise AttributeError(
                "BibAiAssistView 需要满足宿主协议的宿主（含 tokens t）；"
                "不要把裸 Flet Page 直接传进来（UI-REWORK-DESIGN §2）")
        return tokens

    def _flet(self):
        return getattr(self.page, "page", None)

    def _app(self):
        return getattr(self.page, "app", None)

    def _notes(self) -> str:
        return str(getattr(self._app(), "notes_repo", "") or "")

    def _api_env(self) -> dict:
        """AI 设置 → run_call(api=…) 的 env 映射：Key 只走 SLCN_LLM_KEY。

        惰性 import（测试可直接注入假桥、无需引擎包）；取不到设置就返回空 dict，
        绝不把 Key 写进 argv / 日志 / 任务书。
        """
        try:
            from services import ai_prefs as AI
            from services import engine_bridge as EB
        except Exception:  # noqa: BLE001 - 视图层无引擎包时降级为空 env
            return {}
        store = getattr(self.page, "store", None) or getattr(self._app(), "store", None)
        try:
            raw = AI.raw_from_store(store)
            settings = AI.r3_request_settings(raw) if self._r3 else AI.from_store(store)
            return EB.ai_settings_env(settings)
        except Exception:  # noqa: BLE001
            return {}

    def _store_get(self, key: str, default=None):
        store = getattr(self.page, "store", None)
        if store is None:
            store = getattr(self._app(), "store", None)
        getter = getattr(store, "get", None)
        if not callable(getter):
            return default
        try:
            return getter(key, default)
        except Exception:  # noqa: BLE001 - 设置读不到不影响本页
            return default

    def _store_set(self, key: str, value) -> None:
        store = getattr(self.page, "store", None)
        if store is None:
            store = getattr(self._app(), "store", None)
        setter = getattr(store, "set", None)
        if callable(setter):
            try:
                setter(key, value)
            except Exception:  # noqa: BLE001
                pass

    def _refresh(self) -> None:
        render = getattr(self.page, "_render", None)
        if callable(render):
            try:
                render()
            except Exception:  # noqa: BLE001 - 未挂载时忽略
                pass

    def _mark(self, event: str, **fields) -> None:
        mark = getattr(self.page, "mark", None)
        if callable(mark):
            try:
                mark(event, **fields)
            except Exception:  # noqa: BLE001 - 遥测失败不影响操作
                pass

    def _notify(self, message: str) -> None:
        notify = getattr(self.page, "notify", None)
        if callable(notify):
            try:
                notify(str(message))
                return
            except Exception:  # noqa: BLE001
                pass
        toast = getattr(self._app(), "toast", None)
        if callable(toast):
            try:
                toast(str(message))
            except Exception:  # noqa: BLE001
                pass

    def _copy(self, text: str, *, exact: bool = False) -> bool:
        """写剪贴板，**如实**返回结果：成功 True；无服务/抛异常 False。

        R3 §10：宿主 copy 失败不得无条件返回成功——上层 copy_task_text 只在
        拿到 True 时才通知「已复制」。两种宿主服务按优先级试：先 app.copy_text，
        再 Flet page.set_clipboard；两者都不可用/都抛异常才 False。
        """
        fn = getattr(self._app(), "copy_text", None)
        if callable(fn):
            try:
                if exact and "preserve_whitespace" in inspect.signature(fn).parameters:
                    return fn(str(text), preserve_whitespace=True) is True
                return fn(str(text)) is True
            except Exception:  # noqa: BLE001
                pass
        page = self._flet()
        setter = getattr(page, "set_clipboard", None) if page is not None else None
        if callable(setter):
            try:
                return setter(str(text)) is not False
            except Exception:  # noqa: BLE001
                pass
        return False

    def _task(self, fn) -> None:
        """把协程函数交给宿主调度（三级降级，与 bib_import 同款）。"""
        runner = getattr(self.page, "run_task_ui", None)
        if callable(runner):
            try:
                runner(fn)
                return
            except Exception:  # noqa: BLE001
                pass
        page = self._flet()
        run = getattr(page, "run_task", None) if page is not None else None
        if callable(run):
            try:
                run(fn)
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            asyncio.ensure_future(fn())
        except Exception as exc:  # noqa: BLE001
            self.error = f"无法启动任务：{type(exc).__name__}"
            self._refresh()

    def _goto(self, view: str) -> None:
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter(view)

    def _design_width(self) -> float:
        width = 0.0
        page = self._flet()
        try:
            width = float(getattr(page, "width", 0) or 0)
        except Exception:  # noqa: BLE001
            width = 0.0
        if width <= 0:
            width = float(getattr(self._app(), "window_w", 0) or 0)
        return width / max(self.t.k, 1e-6)

    # ── 引擎桥 ─────────────────────────────────────────────────────────
    @property
    def bridge(self):
        """惰性取：不 import 也能构造视图（测试直接注入假 bridge）。"""
        if self._bridge is None:
            self._bridge = _load_bridge()
        return self._bridge

    def _fn(self, name: str):
        return _bridge_fn(self.bridge, name)

    def _require(self, *names: str) -> bool:
        """缺函数 → 记下缺项、显示失败横幅、返回 False（绝不继续跑）。"""
        missing = [name for name in names if not callable(self._fn(name))]
        if missing:
            self._bridge_fail(missing)
            return False
        self._bridge_missing = []
        return True

    def _bridge_fail(self, missing) -> None:
        self._bridge_missing = [str(name) for name in missing]
        self.error = ""
        self.running = False
        self._mark("bib_ai_bridge_missing", missing=",".join(self._bridge_missing))

    def _sync_bridge(self) -> None:
        """每次重画都如实检查引擎桥：缺就当场显示未就绪，**不进任何成功态**。

        这里会触发惰性 import（`_load_bridge`）——「用到」就包括「要告诉用户
        能不能跑」。桥齐了就把横幅撤掉，不残留上一次的缺项。
        """
        self._bridge_missing = missing_bridge(self.bridge)

    def _fail(self, op: str, detail) -> None:
        """失败出口：写失败文案并退回一个**非成功**阶段，绝不通知成功。

        `preparing` / `waiting` 是「正在跑」的中间态；失败后必须离开它们，
        否则状态区会一直停在「准备中」，看上去像还在跑（DESIGN §10）。
        已经可预览/已应用的既成事实不回退——失败只说明这一动没成。
        """
        text = str(detail or "").strip() or "原因未知"
        self.error = f"{op}失败：{text}"
        self.running = False
        self._bridge_missing = []
        if self.phase == "preparing" or (self.phase == "waiting" and not self._r3):
            self.phase = "idle"
        self._mark("bib_ai_failed", op=op, error=self.error)

    def _fail_code(self, op: str, code) -> None:
        """退出码失败：把引擎最后一行输出拼进红字，别只报一个数字。

        `run_call` 已把 stderr 合流进逐行回调，`_run` 又把它攒在 `self.log`；
        引擎真正的原因（argparse 的 `the following arguments are required`、
        `不认识的类别：…`、`Permission denied`）就在最后一行。只写「退出码 2」
        等于把唯一的线索扔掉（问题 4 M3）。
        """
        tail = [ln.strip() for ln in self.log if str(ln).strip()]
        detail = f"引擎返回退出码 {code}"
        if tail:
            detail = f"{detail}：{tail[-1][:160]}"
        self._fail(op, detail)

    def _ok(self) -> None:
        self.error = ""
        self._bridge_missing = []
        self.running = False

    # ── 两组勾选（独立控件对象；采纳组文字纠错默认恒 False）────────────
    def set_execute(self, key: str, value: bool) -> None:
        if key not in EXECUTE_DEFAULTS or (self._is_library_page() and key != "verify"):
            return
        self.execute[key] = bool(value)

    def set_accept(self, key: str, value: bool) -> None:
        if (callable(self._initial_call) and self.running
                or key not in ACCEPT_DEFAULTS
                or (self._is_library_page() and key != "verify")
                or self.accept.get(key) == bool(value)):
            return
        self.accept[key] = bool(value)
        self._invalidate_preview()

    def set_decision(self, conflict_id: str, value: str, *,
                     invalidate: bool = True) -> bool:
        """改一条冲突的裁决；返回是否真的变了。

        裁决是**预览的输入**（合同 §2：`--decide cf-…=…` 参与 preview_id 的
        计算），所以一旦变化，旧预览立刻作废：走 `_invalidate_preview()` 清
        preview_id/candidate_bib 并进 stale/waiting，让「应用/交付/对比」变灰，
        必须重新「生成拟应用结果」。这里不静默重算——重算要跑引擎，用户点了
        才跑。

        `invalidate` 默认 True 保持逐条路径与合同测试逐字节不变；批量路径传
        False，循环结束后只失效一次（B-394，避免第二次起因 phase 已变而吞掉
        自定义 notice）。
        """
        if callable(self._initial_call) and self.running:
            return False
        cid = str(conflict_id or "").strip()
        if not cid.startswith("cf-"):
            return False
        chosen = normalize_decision(value)
        row = next((item for item in self.conflicts if item["conflict_id"] == cid), None)
        if row is not None and chosen not in (row.get("choices") or DECISION_VALUES):
            return False
        if self.decisions.get(cid) == chosen:
            return False
        self.decisions[cid] = chosen
        if invalidate:
            self._invalidate_preview(notice="冲突裁决已改；请重新生成拟应用结果。")
        return True

    def _decision_supported(self, item: dict, value: str) -> bool:
        """与 `set_decision` 的 choices 白名单同口径；value 必须已归一化。"""
        return value in (item.get("choices") or DECISION_VALUES)

    def set_decisions_for_value(self, value: str) -> tuple[int, int, int]:
        """批量把支持该值的冲突切到该裁决；返回 `(changed, skipped, already)`。

        入口只接受 `library`/`reply` 两个字面量（A4：绝不把文案/错字喂给
        `normalize_decision` 兜底成 original = 静默改稿）；running 时二次拒绝
        （A5：library 入口无 `_initial_call`，逐条 set_decision 的 running guard
        不生效）。预扫描分出三态：不支持计 skipped、已选中计 already（不算
        skipped），待改才收集。空操作不失效/不打点/不刷新（A8）。
        """
        if value not in ("library", "reply"):
            return (0, 0, 0)
        if self.running:
            return (0, 0, 0)
        changed = skipped = already = 0
        to_change: list[str] = []
        for item in self.conflicts:
            cid = str(item.get("conflict_id") or "")
            if not cid.startswith("cf-"):
                continue
            if not self._decision_supported(item, value):
                skipped += 1
                continue
            if decision_for(item, self.decisions) == value:
                already += 1
                continue
            to_change.append(cid)
        if not to_change:
            return (changed, skipped, already)
        for cid in to_change:
            if self.set_decision(cid, value, invalidate=False):
                changed += 1
        # A2：循环后唯一一次失效，自定义 notice 不会被后续调用吞掉。
        self._invalidate_preview(notice="冲突裁决已改；请重新生成拟应用结果。")
        self._mark("bib_ai_batch_decision", value=value, changed=changed,
                   skipped=skipped, already=already, total=len(self.conflicts))
        return (changed, skipped, already)

    def reset_decisions(self) -> None:
        """把全部裁决恢复成条目默认值（「保留原值」）。"""
        if callable(self._initial_call) and self.running:
            return
        self.decisions = {}
        self._invalidate_preview(notice="冲突裁决已恢复默认（保留原值）；请重新生成拟应用结果。")

    def decision_scope(self) -> dict:
        """当前生效的完整裁决表（每条冲突都有值，默认 original）。"""
        return conflict_decision_map(self.conflicts, self.decisions)

    def _is_library_page(self) -> bool:
        return self._r3 and self.purpose == "library"

    def _enforce_library_boundaries(self) -> None:
        if not self._is_library_page():
            return
        self.source_path = ""
        self.tex_path = ""
        self.tex_out = ""
        for key in EXECUTE_DEFAULTS:
            if key != "verify":
                self.execute[key] = False
                self.accept[key] = False

    def _library_status_issue(self, status: dict) -> str:
        if not self._is_library_page():
            return ""
        entry = str(status.get("entry") or "").strip()
        if entry != "bib":
            return "任务入口不是引用库 Bib"
        if status.get("tex") or status.get("text_target"):
            return "任务包含关联正文或正文交付目标"
        scope = status.get("scope") or {}
        adoption = status.get("adopt", scope.get("adopt"))
        # 任务执行范围仍固定为 bib_verify。预览的采纳范围可显式为空。
        has_preview = bool(status.get("preview_id")) and phase_from_engine(
            status.get("phase")) in ("preview", "blocked", "applied", "stale")
        ranges = [("执行", status.get("execute", scope.get("exec")), False),
                  ("采纳", adoption, has_preview)]
        if "exec" in scope:
            ranges.append(("任务执行", scope["exec"], False))
        if "adopt" in scope:
            ranges.append(("任务采纳", scope["adopt"], False))
        for label, categories, allow_empty in ranges:
            if not isinstance(categories, (list, tuple, set)):
                return f"任务未明确记录{label}范围"
            normalized = {str(item) for item in categories}
            if allow_empty and not normalized:
                continue
            if normalized != {"bib_verify"}:
                extras = sorted(normalized - {"bib_verify"})
                if extras:
                    return f"任务的{label}范围含有 {', '.join(extras)}"
                return f"任务的{label}范围不是仅文献核实"
        return ""

    def _deactivate_legacy_library_task(self, reason: str) -> None:
        self._clear_task()
        self.work_dir = ""
        self.running = False
        self._initial_operation = ""
        self._enforce_library_boundaries()
        self.notice = f"旧引用库任务已停用（{reason}）；请重新生成任务书。"

    def _invalidate_preview(self, notice: str = "采纳范围已改；请重新生成拟应用结果。") -> None:
        if self.phase in ("preview", "blocked", "applied"):
            previous = self.phase
            self._clear_preview()
            self._preview_dirty = True
            self.phase = "stale" if previous == "applied" else "waiting"
            if previous in ("preview", "blocked"):
                self.imported = True
            self.notice = notice
        elif callable(self._initial_call) and self._has_active_reply:
            self._preview_dirty = True
            self.notice = notice

    def reset_accept(self) -> None:
        """重建采纳组：文字纠错**永远**从 False 起（DESIGN §4）。"""
        self.accept = dict(ACCEPT_DEFAULTS)
        self.accept["textfix"] = False
        self._enforce_library_boundaries()

    def execute_scope(self) -> list[str]:
        """执行组 → **引擎类别名**（内部键不下传，见 `CATEGORY_MAP`）。"""
        return [CATEGORY_MAP[key] for key in self.execute
                if self.execute[key] and self._can_execute_category(key)]

    def accept_scope(self) -> list[str]:
        """采纳组 → **引擎类别名**（同上）。"""
        return [CATEGORY_MAP[key] for key in self.accept
                if self.accept[key] and self._can_accept_category(key)]

    def _can_execute_category(self, key: str) -> bool:
        return not (self._is_library_page() and key != "verify")

    def _can_accept_category(self, key: str) -> bool:
        if self._is_library_page():
            return key == "verify"
        return True

    def _visible_categories(self) -> tuple[tuple[str, str], ...]:
        return CATEGORIES[:1] if self._is_library_page() else CATEGORIES

    def _clear_preview(self) -> None:
        self.preview_id = ""
        self.candidate_bib = ""
        self._candidate_bib_sha = ""
        self.compile_fix_eligible = {}
        self.compile_fix_notice = ""
        self._compile_fix_prepared = None
        self._compile_fix_pdf_opened = None
        self.blocked = []
        self._review_notes = []
        self._clear_comparison()
        # `conflicts` 与 `decisions` **不在这里清**：它们描述的是「这次任务里
        # 有哪些冲突、用户选了哪个」，与「有没有一份有效预览」是两件事。
        # 清掉它们会让用户在一次失效后看不到自己刚才的裁决依据。

    def _clear_initial_reply_review(self) -> None:
        """活动回件更换后，清除只对上一份回件有效的检查结果。"""
        self._reply_revision += 1
        self._category_reports.clear()
        self._clear_preview()
        self._preview_dirty = False
        self._skip_items.clear()
        self._text_items.clear()
        self.library = {}
        self.conflicts = []
        self.decisions = {}
        self.preview_revision = 0

    def _clear_task(self) -> None:
        self._bump_request()
        if callable(self._initial_call):
            self.log = []
        self.task_id = ""
        self.task_text = ""
        self.taskbook_path = ""
        self.body_text = ""
        self.result_path = ""
        self.imported = False
        self._has_active_reply = False
        self.scope_frozen = False
        self.undone = False
        self.backup = ""
        self.key_count = 0
        self.entry_count = 0
        self._final_dir = ""
        self._original_final_dir = ""
        self._initial_open_stage = ""
        self._external_import_draft = None
        self.delivery_ok = False
        self.delivery_compile = ""
        self.delivery_error = ""
        self.delivery_log.clear()
        self._operation_receipt.clear()
        self.compile_fix_eligible = {}
        self.compile_fix_notice = ""
        self._disclosure_open.clear()
        self._clear_preview()
        self._preview_dirty = False
        self._skip_items.clear()
        self._text_items.clear()
        self._category_reports.clear()
        # 换任务/换工作区：库状态与冲突属于「上一个任务」，裁决属于上一个任务的
        # 冲突 ID，全部清空，避免把旧 ID 下发给新任务（引擎会以退出码 2 拒绝）。
        self.library = {}
        self.conflicts = []
        self.decisions = {}
        self.preview_revision = 0
        # 冻结快照身份属于「上一个任务」：换任务后必须重新从 status 取，
        # 否则会拿旧任务的 sha 去核新任务的 Bib。
        self._frozen_bib_path = ""
        self._frozen_bib_sha = ""
        self._source_bib = ""
        self.phase = "idle"
        self.notice = ""
        self.error = ""

    def _restart(self) -> None:
        self._clear_task()
        self._parent_paths = None
        self.work_dir = ""
        if self._r3:
            self._store_set(self._task_store_key(), {})
        self.reset_accept()
        self._refresh()

    def _check_parent_task(self) -> None:
        if self._parent_paths is not None and self._parent_paths != self.page.ai_initial_paths():
            self._clear_task()
            self._parent_paths = None
            self.work_dir = ""
            self.notice = "父页源稿或输出目录已更换，请生成新任务书。"

    def _on_execute(self, key: str):
        def _handler(e) -> None:
            self.set_execute(key, bool(getattr(e.control, "value", False)))
            self._mark("bib_ai_scope_execute", key=key, value=self.execute[key])
            _refresh_workspace(self)
        return _handler

    def _on_accept(self, key: str):
        def _handler(e) -> None:
            self.set_accept(key, bool(getattr(e.control, "value", False)))
            self._mark("bib_ai_scope_accept", key=key, value=self.accept[key])
            _refresh_workspace(self)
        return _handler

    def _checkbox(self, label: str, value: bool, on_change, *, disabled: bool = False
                  ) -> tuple[ft.Control, ft.Checkbox]:
        """28 高勾选行（照 bib_normalize 的样板，Material 默认 48 会撑高）。

        返回 `(行, 勾选框)`：行拿来排版，勾选框拿去登记进 `_execute_boxes` /
        `_accept_boxes`——两组必须是**不同的控件对象**，不能共用一个。
        """
        t = self.t
        box = ft.Checkbox(value=value, on_change=on_change, disabled=disabled,
                          visual_density=ft.VisualDensity.COMPACT, splash_radius=0)
        box._bib_ai_label = label
        row = ft.Container(
            height=t.px(28),
            padding=ft.Padding(left=t.px(6), right=t.px(6), top=0, bottom=0),
            content=ft.Row(spacing=t.sp("half"), controls=[
                box,
                C.text(label, t, "caption",
                       t.palette.text_faint if disabled else t.palette.text),
            ]))
        return row, box

    # 两组勾选**不再捆在同一个块里**（问题 5）：执行范围归阶段①（准备），采纳范围
    # 归阶段③（检查采纳）。各自在自己阶段的块内紧挨对应按钮，去掉 `_scope_block()`。
    def _execute_section(self) -> ft.Control:
        """执行范围：生成任务书时交给引擎的类别（之后冻结）。"""
        t = self.t
        self._execute_boxes = {}
        rows: list[ft.Control] = []
        for key, label in self._visible_categories():
            available = self._can_execute_category(key)
            row, box = self._checkbox(label, self.execute[key] and available,
                                      self._on_execute(key),
                                      disabled=self.scope_frozen or self.running or not available)
            self._execute_boxes[key] = box
            rows.append(row)
        note = ""
        if self._is_library_page():
            note = "引用库仅执行文献核实。"
        if self.scope_frozen and not self._r3:
            note = "执行范围在任务书生成后冻结；如需更改，请再出任务书。"
        controls: list[ft.Control] = [
            S.section(t, "执行范围（生成任务书时生效）"),
            ft.Row(wrap=True, spacing=t.sp("2"), controls=rows),
        ]
        if note:
            controls.append(C.text(note, t, "caption", t.palette.text_faint,
                                   max_lines=2))
        return ft.Column(spacing=t.sp("half"), controls=controls)

    def _accept_section(self) -> ft.Control:
        """采纳范围：引擎返回后决定哪些类别可以并入（永远默认最保守）。"""
        t = self.t
        self._accept_boxes = {}
        rows: list[ft.Control] = []
        for key, label in self._visible_categories():
            available = self._can_accept_category(key)
            report = self._category_reports.get(CATEGORY_MAP[key]) or {}
            if self._r3 and (report.get("status") == "not_run" or not self.execute.get(key)):
                rows.append(C.text(f"{label} · 本次未执行", t, "caption", t.palette.text_muted))
                continue
            if self._r3 and not available:
                reason = ("仅报告" if self.purpose == "library" and key == "proofcheck"
                          else "只报告，未登记正文副本")
                rows.append(C.text(f"{label} · {reason}", t, "caption", t.palette.text_muted))
                continue
            row, box = self._checkbox(label, self.accept[key] and available,
                                      self._on_accept(key), disabled=self.running or not available)
            self._accept_boxes[key] = box
            rows.append(row)
        return ft.Column(spacing=t.sp("half"), controls=[
            S.section(t, "采纳范围（生成拟应用结果时生效）"),
            ft.Row(wrap=True, spacing=t.sp("2"), controls=rows),
            *self._text_item_checks(),
            self._caption("引用库仅采纳文献核实结果。") if self._is_library_page()
            else self._caption("文字纠错默认不采纳。"),
        ])

    # ── 库比对与冲突裁决（CONTRACT.md §4.1）────────────────────────────
    def _library_section(self) -> list[ft.Control]:
        """库比对结论行：可用就报匹配/冲突数，不可用就报**原因**（不是零冲突）。

        合同 §1 的硬要求：库没比对过时不能显示成 `conflicts: []`，否则用户会
        把「没查」当成「查过了没差异」。所以 checked=False 时这里只显示
        「没有比对」+ 人话原因，一个计数都不给。
        """
        if not self._r3 or not self.library:
            return []
        t = self.t
        block = self.library
        lines = library_lines(block)
        checked = bool(block.get("checked"))
        tone = "info" if checked else "warn"
        controls: list[ft.Control] = [S.section(t, "库比对")]
        controls.append(C.inline_note(lines[0], t, tone))
        for extra in lines[1:]:
            controls.append(C.text(extra, t, "caption", t.palette.text_faint,
                                   max_lines=2))
        path = str(block.get("path") or "")
        if path:
            controls.append(C.kv_row(t, "库文件", path, mono=True))
        sha = str(block.get("sha256") or "")
        if sha:
            controls.append(C.kv_row(t, "库 sha256", sha[:16], mono=True))
        return controls

    def _conflict_row(self, item: dict) -> ft.Control:
        """字段候选与采用动作放在同一列，完整值可选择复制。"""
        t = self.t
        cid = str(item.get("conflict_id") or "")
        chosen = decision_for(item, self.decisions)
        allowed = item.get("choices") or DECISION_VALUES
        values = (("original", "原稿", "保留原值"),
                  ("reply", "AI 结果", "用回件值"),
                  ("library", "引用库", "用库值"))
        narrow = self._workspace_width() < 600
        cells = []
        expressions = item.get("expressions")
        for key, heading, action in values:
            raw_value = item.get(key)
            if (key in ("original", "reply") and isinstance(expressions, dict)
                    and isinstance(expressions.get(key), str)):
                raw_value = expressions[key]
            value = str(raw_value or ("无库值" if key == "library" and key not in allowed else "（空）"))
            radio = ft.Radio(value=key, label=action,
                             label_style=C.tstyle(t, size=t.font("small")),
                             disabled=key not in allowed)
            cells.append(ft.Container(
                expand=not narrow, padding=t.sp("1"),
                bgcolor=t.palette.surface_2,
                border_radius=t.radius("control"),
                content=ft.Column(spacing=t.sp("half"), controls=[
                    C.text(heading, t, "caption", t.palette.text_muted),
                    C.text(value, t, "body", selectable=True),
                    radio,
                ])))
        group = ft.RadioGroup(
            value=chosen, disabled=self.running,
            on_change=self._on_decision(cid),
            content=(ft.Column(spacing=t.sp("half"), controls=cells) if narrow else
                     ft.Row(spacing=t.sp("half"),
                            vertical_alignment=ft.CrossAxisAlignment.START, controls=cells)))
        group._bib_ai_field = f"conflict:{cid}"
        group._bib_ai_conflict = cid
        self._conflict_groups[cid] = group
        return ft.Column(spacing=t.sp("half"), controls=[
            C.text(f"{item.get('key') or '（无条目键）'} · {item.get('field') or '（无字段）'}",
                   t, "body", weight="medium", selectable=True), group])

    def _conflict_section(self) -> list[ft.Control]:
        """差异数不代表阻断；默认保留原稿是一项有效选择。"""
        if not self._r3 or not self.conflicts:
            return []
        t = self.t
        self._conflict_groups = {}
        rows = [self._conflict_row(item) for item in self.conflicts]
        controls: list[ft.Control] = [
            S.section(t, f"字段差异 · {len(self.conflicts)} 项"),
            self._caption("默认保留原稿；需要时逐项改选。改选后须重新生成拟应用结果。"),
        ]
        # A1：跳过提示必须是冲突区内可见控件（R3 路径不渲染通用 notice）。
        skipped = self._batch_skip_notice
        if skipped > 0:
            note = (f"{skipped} 条无库值已跳过，保留原裁决"
                    if self._batch_last_value == "library" else
                    f"{skipped} 条无 AI 结果已跳过，保留原裁决")
            controls.append(C.inline_note(note, t, "warn"))
        controls.extend(rows[:2])
        if len(rows) > 2:
            controls.append(self._disclosure(
                "more_differences", f"其余 {len(rows) - 2} 项字段差异", rows[2:]))

        def _has_support(value: str) -> bool:
            return any(self._decision_supported(item, value) for item in self.conflicts)

        for label, value in (("全选库值", "library"), ("全选 AI 值", "reply")):
            btn = C.secondary_button(
                label, self._on_batch_decision(value), t, height=28,
                disabled=self.running or not _has_support(value))
            if btn.disabled:
                btn.tooltip = ("正在处理任务，暂不可批量选择" if self.running else
                               f"当前 {len(self.conflicts)} 项字段差异均不支持该值")
            controls.append(btn)
        controls.append(C.secondary_button(
            "全部保留原值", lambda e: self._on_reset_decisions(), t,
            height=28, disabled=self.running))
        return controls

    def _on_decision(self, conflict_id: str):
        def _handler(e) -> None:
            value = str(getattr(e.control, "value", "") or "")
            if self.set_decision(conflict_id, value):
                self._mark("bib_ai_decision", conflict_id=conflict_id, decision=value)
                _refresh_workspace(self)
        return _handler

    def _on_batch_decision(self, value: str):
        def _handler(e) -> None:
            _changed, skipped, _already = self.set_decisions_for_value(value)
            self._batch_skip_notice = skipped
            self._batch_last_value = value
            _refresh_workspace(self)
        return _handler

    def _on_reset_decisions(self) -> None:
        self.reset_decisions()
        self._mark("bib_ai_decision_reset", conflicts=len(self.conflicts))
        _refresh_workspace(self)

    # ── 用途 / 通道 ────────────────────────────────────────────────────
    def set_purpose(self, purpose: str) -> None:
        if purpose not in PURPOSE_LABELS or purpose == self.purpose:
            return
        self._clear_task()
        self.purpose = purpose
        self.notice = f"用途已切换为「{PURPOSE_LABELS[purpose]}」；流程不变，只改文案与参数提示。"
        self._mark("bib_ai_purpose", purpose=purpose)

    def set_channel(self, channel: str) -> None:
        if channel not in CHANNEL_LABELS or channel == self.channel:
            return
        self.channel = channel
        self.notice = ""
        self._mark("bib_ai_channel", channel=channel)

    def _on_purpose(self, e) -> None:
        self.set_purpose(str(getattr(e.control, "value", "") or ""))
        self._refresh()

    def _on_channel(self, e) -> None:
        self.set_channel(str(getattr(e.control, "value", "") or ""))
        _refresh_workspace(self)

    def _radio_group(self, options, value, on_change, key: str) -> ft.RadioGroup:
        t = self.t
        style = C.tstyle(t, size=t.font("small"))
        group = ft.RadioGroup(
            value=value, on_change=on_change,
            content=ft.Row(spacing=t.sp("2"), wrap=True, controls=[
                ft.Radio(value=item_key, label=label, label_style=style)
                for item_key, label in options]))
        group._bib_ai_field = key
        return group

    def _purpose_row(self) -> ft.Control:
        t = self.t
        return ft.Row(spacing=t.sp("2"), wrap=True, controls=[
            C.text("用途", t, "small", t.palette.text_muted),
            self._radio_group(PURPOSES, self.purpose, self._on_purpose, "purpose"),
        ])

    def _channel_row(self) -> ft.Control:
        t = self.t
        return ft.Row(spacing=t.sp("2"), wrap=True, controls=[
            C.text("通道", t, "small", t.palette.text_muted),
            self._radio_group(CHANNELS, self.channel, self._on_channel, "channel"),
        ])

    # ── 输入（路径 vs 正文是两组不同控件）──────────────────────────────
    def _on_path(self, field: str):
        def _handler(e) -> None:
            value = str(getattr(e.control, "value", "") or "").strip()
            if self._set_path(field, value):
                if field == "result_path":
                    # B-393 #2: 结果路径每字输入/✕清空/浏览回填都不重绘整树，
                    # 否则输入框会被新控件替换而丢失焦点，且滚动位置归零。
                    # 值已写入 self.result_path；✕ 可见性由 sync_clearable 就地维护。
                    return
                self._refresh()
        return _handler

    def _set_path(self, field: str, value: str) -> bool:
        if self._is_library_page() and field in {
                "source_path", "tex_path", "tex_out", "work_dir"}:
            return False
        if value == getattr(self, field) or self.running:
            return False
        if field == "work_dir":
            self.set_work_dir(value)
        elif field == "result_path":
            self.result_path = value
        else:
            self._clear_task()
            if self._is_library_page() and field == "target_bib":
                self.work_dir = ""
            setattr(self, field, value)
            if field == "tex_path":
                self.tex_out = ""
            if field == "target_bib":
                self.bib_out = ""
        if field not in ("result_path", "tex_out"):
            self._store_set(f"bib_ai.{field}", value)
        return True

    def _confirm(self, *, title: str, message: str, impact: str, callback) -> None:
        page = self._flet()
        if not callable(getattr(page, "show_dialog", None)):
            self._fail(title, "对话框不可用")
            self._refresh()
            return
        S.danger_confirm(page, self.t, title=title, message=message,
                         impact=impact, on_confirm=callback)

    def _register_copy(self, field: str) -> None:
        source_field = "tex_path" if field == "tex_out" else "target_bib"
        source = getattr(self, source_field)
        if not source or self.running or self.scope_frozen:
            return
        stem, extension = os.path.splitext(os.path.abspath(os.path.expanduser(source)))
        dest = f"{stem}-AI交付-{uuid.uuid4().hex[:8]}{extension}"

        def register() -> None:
            if getattr(self, source_field) == source and not self.running and not self.scope_frozen:
                setattr(self, field, dest)
                self._refresh()

        kind = "正文" if field == "tex_out" else "Bib"
        self._confirm(title=f"登记{kind}交付副本",
                      message=f"采纳结果将写入这个新{kind}副本，所选原件保持只读。",
                      impact=dest, callback=register)

    def _browse(self, field: str, title: str, *, kind: str = "file"):
        """真实选择器结束时同步业务状态，不限制用户在原生窗口里的选择时长。"""
        def _handler(e=None) -> None:
            if self._picking:
                return
            browse = getattr(self._app(), "browse_field", None)
            ref = self._refs.get(field)
            if not callable(browse) or not ref:
                self.error = "文件选择器不可用；请直接粘贴路径。"
                self._refresh()
                return
            request_seq = self.request_seq
            before = getattr(self, field)
            self._picking = True

            def guard() -> bool:
                # 选择期间换任务、改输入或开跑后，在宿主写入前拒收迟到结果。
                return (request_seq == self.request_seq and not self.running
                        and before == getattr(self, field))

            def _picked(value) -> None:
                self._picking = False
                if not guard():
                    self._mark("bib_ai_browse", field=field, picked=False, stale=True)
                    return
                after = str(value or "").strip()
                if not after:
                    self._mark("bib_ai_browse", field=field, picked=False)
                    return
                self._set_path(field, after)
                self.error = ""
                self._mark("bib_ai_browse", field=field, picked=True)
                if field != "result_path":
                    self._refresh()

            try:
                browse(ref, title, kind, f"bib_ai_{field}", on_result=_picked, result_guard=guard)
            except Exception as exc:  # noqa: BLE001 - 选择器实现因平台而异
                self._picking = False
                self.error = f"文件选择器不可用（{type(exc).__name__}）；请粘贴路径。"
                self._refresh()
        return _handler

    def _paths(self, *, compact: bool = False) -> ft.Control:
        """阶段①（准备）的路径区：库页只选 Bib，单篇流程可选关联正文。

        `compact=True`（`body_only()` 内嵌）不显示用途、重复源 Bib、正文交付件和工作目录；
        单篇流程保留可选关联 TeX，引用库页只显示目标 Bib。独立 `build()` 对旧式非 R3
        流程仍提供完整路径；`AI 结果文件` 位于阶段②，见 `_result_picker()`。

        清空 `_refs` 的动作在 `_parts()` 开头统一做（四个阶段块共用一次重画）。
        """
        t = self.t
        pickers: list = []
        if callable(self._initial_call):
            return self._caption("使用上方源稿、输出目录和初排选项。")
        if not compact and not self._r3:
            source_label = SOURCE_LABELS[self.purpose]
            source_hint = SOURCE_HINTS[self.purpose]
            pickers.append(C.path_picker(
                source_label, self.source_path, source_hint, t,
                on_change=self._on_path("source_path"),
                on_browse=self._browse("source_path", "选择源稿"),
                required=False, show_note=False,
                field_ref=self._refs["source_path"]))
        pickers.append(C.path_picker(
            "待处理 Bib（.bib）" if self._r3 else "目标 Bib（.bib）", self.target_bib,
            "应用步骤写这个文件；引擎先备份再原子替换",
            t, on_change=self._on_path("target_bib"),
            on_browse=self._browse("target_bib", "选择目标 .bib"),
            required=True, show_note=False,
            field_ref=self._refs["target_bib"]))
        if not self._is_library_page():
            pickers.append(C.path_picker(
                "关联正文（.tex，可选）", self.tex_path,
                "用来算引用键闭合与文字纠错基线；源正文只读，永不回写",
                t, on_change=self._on_path("tex_path"),
                on_browse=self._browse("tex_path", "选择正文 .tex", kind="file"),
                required=False, show_note=False,
                field_ref=self._refs["tex_path"]))
        if self._is_library_page():
            if self.target_bib:
                pickers.extend([
                    C.secondary_button("另存为新 Bib…", lambda e: self._register_copy("bib_out"), t,
                                       disabled=self.running or self.scope_frozen),
                    self._caption(self.bib_out or "默认并入所选 Bib，可登记新副本。"),
                ])
        elif not compact and not self._is_library_page():
            pickers.extend([
                C.path_picker("正文交付件（可选）", self.tex_out,
                              "文字纠错的候选写到这里（新建或已存在的交付件），"
                              "不动你选的源正文",
                              t, on_change=self._on_path("tex_out"),
                              on_browse=self._browse("tex_out", "选择正文交付件",
                                                     kind="file"),
                              required=False, show_note=False,
                              field_ref=self._refs["tex_out"]),
                C.path_picker("工作目录", self.work_dir,
                              "任务书 / 拟应用结果放这里（可空 = 引擎默认）",
                              t, on_change=self._on_path("work_dir"),
                              on_browse=self._browse("work_dir", "选择工作目录",
                                                     kind="dir"),
                              required=False, show_note=False,
                              field_ref=self._refs["work_dir"]),
            ])
        return ft.Column(spacing=t.sp("1"), controls=pickers)

    def _result_picker(self, *, label: str = "AI 结果文件") -> ft.Control:
        """阶段②（等待结果）的「AI 结果文件」：紧挨「导入结果」按钮（完整版才有）。

        生产内嵌视图（`compact=True`）不显示它——用户把回复直接粘进下面的正文框。
        """
        t = self.t
        return ft.Column(spacing=t.sp("1"), controls=[
            C.path_picker(label, self.result_path,
                          "AI 返回的新 .bib 或整份回复（可选）",
                          t, on_change=self._on_path("result_path"),
                          on_browse=self._browse("result_path", "选择 AI 结果文件"),
                          required=False, show_note=False,
                          field_ref=self._refs["result_path"]),
        ])

    def _body_paste_row(self, *, label: str = "", hint: str = "") -> ft.Control:
        """「一键粘贴正文」：读剪贴板里的**正文**，不是路径（DESIGN §10 / N04）。"""
        t = self.t
        field = C.text_field(
            t, value=self.body_text,
            hint_text=hint or "把 AI 返回的 bib 文本或整份回复正文粘贴到这里（不是文件路径）",
            multiline=True, min_lines=4, max_lines=8, expand=True,
            on_change=self._on_body_change)
        field._value_tooltip = False
        self._paste_field = field
        button = C.paste_button(t, self.page, field, height=t.control_h())
        button._bib_ai_paste_body = True
        self._paste_button = button
        row = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.START,
                     controls=[field, button])
        return C.form_field(t, label or "AI 结果正文（可选，纯文本，不是文件路径）", row,
                            expand=True, clearable=False)

    def _on_body_change(self, e) -> None:
        self.body_text = str(getattr(e.control, "value", "") or "")

    # ── R3 复制任务书的请求身份（竞态核对用）─────────────────────────────
    def _bump_request(self) -> None:
        """标记「复制对象变了」：请求序号 +1，旧异步结果/缓存一并作废。"""
        self.request_seq += 1
        self._taskbook_cache = None

    def set_work_dir(self, value: str) -> None:
        """切换工作区（含引擎恢复回填）：值变了就让在飞的复制结果作废。"""
        text = str(value or "")
        if text != str(self.work_dir or ""):
            self._clear_task()
            self.work_dir = text

    def set_task_id(self, value: str) -> None:
        """切换活动任务：值变了就让在飞的复制结果作废，且不重新发请求。"""
        text = str(value or "")
        if text != str(self.task_id or ""):
            self._clear_task()
            self.task_id = text

    def paste_reply_text(self) -> str:
        """从宿主真实剪贴板读纯文本（Chat 通道粘 AI 回复的备用入口）。

        导入仍走 `import_result` → 引擎 `ai_import_call` 校验；这里只负责取文本，
        绝不绕过引擎直接把剪贴板内容写进目标 Bib。无剪贴板服务返回空串。
        """
        fn = getattr(self._app(), "paste_text", None)
        if callable(fn):
            try:
                text = fn()
                return str(text) if isinstance(text, str) else ""
            except Exception:  # noqa: BLE001
                pass
        page = self._flet()
        getter = getattr(page, "get_clipboard", None) if page is not None else None
        if callable(getter):
            try:
                text = getter()
                if inspect.isawaitable(text):
                    return ""
                return str(text) if isinstance(text, str) else ""
            except Exception:  # noqa: BLE001
                pass
        return ""


    # ── 状态区 ─────────────────────────────────────────────────────────
    def _status_block(self) -> ft.Control:
        t = self.t
        rows = [
            C.kv_row(t, "阶段", phase_label(self.phase)),
            C.kv_row(t, "TASK-ID", self.task_id or "—", mono=True),
            C.kv_row(t, "引用键数", str(_int_of(self.key_count))),
            C.kv_row(t, "条目数", str(_int_of(self.entry_count))),
        ]
        if self.scope_frozen:
            # 用户真机：出完任务书不知道文件落在哪。引擎日志里有实际路径就原样显示
            # （mono + tooltip 全文）；日志没给出就如实说路径见日志，绝不编造。
            path = str(self.taskbook_path or "").strip()
            rows.append(C.kv_row(
                t, "任务书路径",
                path or "任务书已生成（路径见引擎日志）",
                mono=bool(path)))
        frozen_path, frozen_kind, frozen_message = self._frozen_identity()
        if self._r3 and (self._frozen_bib_path or frozen_path):
            # 合同 v1.1 / 红线2-⑦：左侧是谁必须写在界面上，退化也要看得见。
            label = ("冻结稿件版 Bib" if frozen_kind == FROZEN_OK
                     else "对比左侧 Bib（未冻结）")
            rows.append(C.kv_row(t, label,
                                 frozen_path or self._frozen_bib_path or "—",
                                 mono=bool(frozen_path or self._frozen_bib_path)))
        notes = ([] if frozen_kind == FROZEN_OK or not frozen_message
                 else [C.inline_note(frozen_message, t,
                                     "warn" if frozen_kind == FROZEN_LEGACY
                                     else "error")])
        return ft.Column(spacing=0, controls=[
            S.section(t, "状态"),
            *rows,
            *notes,
            *([C.kv_row(t, "任务目录", self.work_dir, mono=True)] if self._r3 and self.work_dir else []),
            *([C.kv_row(t, "交付目录", self._final_dir, mono=True)] if self._final_dir else []),
            *self._wiring_lines(),
        ])

    def _wiring_lines(self) -> list[ft.Control]:
        """只显示真正的接线缺口；纯签名过滤提示不再上屏（用户真机噪音）。

        `_build_call` 仍把「X 不接受参数：…」记进 `_wiring_notes` 供排查，但那是
        视图为兼容未定稿桥而按签名过滤参数的正常过程，不是故障。原先橙色长串让
        用户以为接线坏了。缺函数/桥未就绪走 `_bridge_missing` + `_banners()`，
        不经过这里，所以真故障提示不受影响。
        """
        t = self.t
        lines: list[ft.Control] = []
        for note in self._wiring_notes:
            if "不接受参数" in note:
                continue
            lines.append(C.inline_note(f"接线提示：{note}", t, "warn"))
        return lines

    # ── 横幅 ───────────────────────────────────────────────────────────
    def _banners(self) -> list[ft.Control]:
        t = self.t
        parts: list[ft.Control] = []
        if self._bridge_missing:
            text = bridge_error_text(self._bridge_missing)
            parts.append(S.state_banner(
                t, "failure", message=text,
                detail="引擎桥还没接上：视图不会自己造结果，接好之后再试。",
                next_label="复制错误", on_next=lambda e: self._copy(text)))
        if self.error and not self._bridge_missing:
            parts.append(S.state_banner(
                t, "failure", message=self.error,
                detail="没有写入任何用户文件。",
                next_label="复制错误", on_next=lambda e: self._copy(self.error)))
        if self.blocked:
            parts.append(S.state_banner(
                t, "failure", message=BLOCKED_TITLE,
                detail="阻断项修掉之前，「应用」保持禁用。"))
            for item in self.blocked:
                parts.append(C.inline_note(item, t, "error"))
        if self.running:
            parts.append(S.state_banner(t, "running", message="运行中",
                                        stage=phase_label(self.phase)))
        if self.phase == "applied" and not self.error and not self._bridge_missing:
            # 合同 §4.3：成功卡主操作 =「打开交付目录」，次操作 =「撤销本次写入」
            # （撤销就是 done 块里那颗唯一的 FilledButton，不在这里重复一颗，
            #  免得同一个动作出现两条入口）。
            detail = f"备份：{self.backup}" if self.backup else "引擎已先备份再原子替换。"
            parts.append(S.state_banner(
                t, "success", message=("已完成初排转换" if callable(self._initial_call)
                                       else "已应用到目标 Bib"),
                detail=self._final_dir or detail,
                next_label=("打开交付目录" if self._final_dir else ACTION_LABELS["undo"]),
                on_next=(lambda e: self._open_delivery_dir()) if self._final_dir
                else (lambda e: self._do(self.undo))))
        if self.phase == "stale" and not self.error:
            text = ("已撤销本次写入。" if self.undone
                    else "结果已失效：需要重新生成拟应用结果。")
            parts.append(S.state_banner(t, "stale", message="已失效", detail=text))
        if self.notice:
            parts.append(C.inline_note(self.notice, t))
        return parts

    # ── 动作 ───────────────────────────────────────────────────────────
    def _button(self, key: str, *, primary: bool = False, disabled: bool = False,
                label: str = ""):
        default = R3_ACTION_LABELS.get(key) if self._r3 else None
        label = label or default or ACTION_LABELS[key]
        if self._r3 and key == "apply":
            if self.purpose == "initial":
                label = "完成初排转换"
            else:
                label = "并入目标 Bib 与正文副本" if self.tex_out else "并入目标 Bib"
        maker = C.primary_button if primary else C.secondary_button
        context = self._initial_action_context() if self._r3 else None
        btn = maker(label, lambda e: self._click(key, context), self.t,
                    height=36 if self._r3 and primary else 28,
                    disabled=disabled or self.running)
        btn._bib_ai_action = key
        self._action_buttons[key] = btn
        return btn

    def _click(self, key: str, displayed_context: tuple | None = None) -> None:
        """按钮统一入口：同一个 key 既可点击也可直接测协程方法。"""
        initial = callable(self._initial_call)
        if self.running or (initial and self._initial_action_pending):
            return
        old_task = self.task_id
        self._check_parent_task()
        if initial and old_task and not self.task_id and key not in ("prepare", "restart"):
            self._refresh()
            return
        if displayed_context is not None and displayed_context != self._initial_action_context():
            return
        if self._r3 and key in ("review", "confirm_stage", "replace_reply", "open_final"):
            if key == "open_final":
                self._open_result_location()
            else:
                self._initial_open_stage = {"review": "review", "confirm_stage": "", "replace_reply": "waiting"}[key]
                self._refresh()
            return
        if key in ("use_chat", "use_api"):
            self.set_channel("chat" if key == "use_chat" else "api")
            self._initial_open_stage = "waiting"
            _refresh_workspace(self)
            return
        if key == "restart":
            self._restart()
            return
        if key == "cancel_external_import":
            self._cancel_external_import()
            return
        if key == "import_external":
            if self._initial_open_stage != "external" or self._external_import_draft is None:
                return
            if not self.result_path.strip() and not self.body_text.strip():
                self._fail("采用完整外部 Bib", "请先选择完整 Bib 文件，或粘贴完整 Bib 正文。")
                self._refresh()
                return
            self._confirm_import("complete_external")
            return
        if key == "undo" and self._r3:
            context = displayed_context
            task = (self.task_id, self.work_dir, self.preview_id, self.phase)
            def confirmed() -> None:
                if task == (self.task_id, self.work_dir, self.preview_id, self.phase):
                    self._click("undo_confirmed", context)
            self._confirm(
                title="撤销本次交付" if initial else "撤销本次写入",
                message=("恢复本次交付前的目录状态。" if initial else
                         "用本次备份恢复目标文件，撤回本次写入的修改。"),
                impact="确认后由引擎检查并恢复；不会修改源稿。", callback=confirmed)
            return
        if key == "replace_reply":
            self.imported = False
            self.phase = "waiting"
            self._clear_preview()
            self._refresh()
            return
        runner = {
            "deliver_original": self.deliver_original,
            "prepare": self.prepare,
            "request": self.request_api,
            "import": self.import_result,
            "preview": self.preview,
            "apply": self.apply,
            "undo": self.undo,
            "undo_confirmed": self.undo,
            "refresh": self.refresh_status,
        }.get(key)
        if runner is None:
            return
        if not initial:
            self._do(runner)
            return
        context = displayed_context or self._initial_action_context()
        self._initial_action_pending = True

        async def run_current() -> None:
            try:
                if context == self._initial_action_context():
                    await runner()
            finally:
                self._initial_action_pending = False

        self._mark("bib_ai_action", action=runner.__name__)
        self._task(run_current)

    def _do(self, coro_fn) -> None:
        self._mark("bib_ai_action", action=getattr(coro_fn, "__name__", ""))
        self._task(coro_fn)

    def _frozen_identity(self) -> tuple:
        """左侧到底用哪一份 Bib —— `(path, kind, message)`（合同 §4.2 / v1.1）。

        判据全在模块级 `resolve_frozen_bib()` 里（可单独测）：sha 对得上才认冻结版；
        对不上就禁用而不是拿 `source_bib`/`target_bib` 冒充；旧任务（引擎没给
        `frozen_bib`）才退化，并把「旧任务没有冻结快照」写进提示。
        """
        return resolve_frozen_bib(self._frozen_bib_path, self._frozen_bib_sha,
                                  (self._source_bib, self.target_bib))

    def _frozen_bib(self) -> str:
        """左侧「冻结稿件版 Bib」的真实路径；身份核不出来时返回 ""。

        退化（旧任务）时也返回路径，但调用方**必须**把 `_frozen_identity()` 的
        message 显示出来——红线2-⑦：退化不得静默。
        """
        return self._frozen_identity()[0]

    def _frozen_hint(self) -> str:
        """左侧身份的提示文案（冻结正常时为空串）。"""
        path, kind, message = self._frozen_identity()
        return "" if kind == FROZEN_OK else message

    def _candidate_diff_hint(self) -> str:
        if self._r3 and not self._candidate_bib_sha:
            return "引擎状态未提供候选 Bib 的摘要。请重新生成拟应用结果，若仍缺摘要，请更新引擎。"
        return ""

    def _can_diff(self) -> bool:
        """能不能打开对比：既要能应用、有真实候选，左侧身份还得核得出来。

        合同 §4.2 把对比入口钉在阶段③（人工裁决已产出**有效** preview 之后）：
        `can_apply()` 已经涵盖「phase==preview、无 blocked、有 preview_id」，
        所以等待回件 / blocked / 裁决刚改未重新 preview / 撤销失效 时这里一律
        为 False，按钮灰着。

        合同 v1.1 再加一条：左侧必须是**能核为冻结稿件版**的那一份。冻结快照
        sha 不符或文件不在 → 这里就是 False，按钮禁用，绝不拿别的 Bib 顶替。
        """
        if not (self.can_apply() and self.candidate_bib
                and os.path.isfile(self.candidate_bib)) or self._candidate_diff_hint():
            return False
        kind = self._frozen_identity()[1]
        if callable(self._initial_call):
            return kind == FROZEN_OK and os.path.isdir(self.work_dir)
        return kind != FROZEN_MISSING

    def _compare_work_dir(self) -> str:
        """对比副本的落点（合同 §4.2：`<work_dir>/compare/`，绝不许进 stage/）。

        用引擎的任务目录（`_work_dir_path()` = 用户选的 work_dir，或
        `<target 目录>/.aiw`）：这是「本次任务自己的目录」，`compare/` 落在里面
        既不在 `stage/`，也不在最终交付目录里。

        任务目录还不存在时（例如刚打开页面、引擎没跑过）退回目标 Bib 所在目录：
        `editor_bridge` 只**新建**最后一层 `compare/`，不会替用户造整条路径
        （它宁可报「产物目录不存在」也不悄悄 mkdir -p），所以这里必须给一个
        真实存在的父目录。
        """
        try:
            base = str(self._work_dir_path() or "").strip()
        except Exception:  # noqa: BLE001 - 取不到就退回目标 Bib 所在目录
            base = ""
        if base and os.path.isdir(base):
            return base
        target = str(self.target_bib or "").strip()
        if target:
            folder = os.path.dirname(os.path.abspath(os.path.expanduser(target)))
            if os.path.isdir(folder):
                return folder
        return base

    def _clear_comparison(self) -> None:
        self._compare_paths = ()
        self._compare_feedback = ""

    def _copy_compare_paths(self, paths: tuple[str, ...]) -> None:
        self._check_parent_task()
        if not paths or paths != self._compare_paths or not self._can_diff():
            return
        copied = self._copy("\n".join(paths), exact=True)
        self._compare_feedback = ("已复制两份对比副本的路径。" if copied else
                                  "剪贴板不可用，可从下方选取路径。")
        _refresh_workspace(self)

    def open_diff(self) -> None:
        """DESIGN §10 / 合同 §4.2：双文件 diff。**只展示**，不写用户文件、不绑应用。

        左侧 = 冻结稿件版 Bib（`source_bib`，无则退化 `target_bib`），右侧 = 当前
        有效 preview 的 `candidate_bib`（逐字节就是待交付候选）。两侧副本由
        `editor_bridge` 写成一对标准文件名，落在 `<work_dir>/compare/`。
        """
        initial = callable(self._initial_call)
        self._check_parent_task()
        self._clear_comparison()
        # 两道门分开报，别用一句笼统的「先生成拟应用结果」盖住身份问题
        # （红线2-⑦：左侧核不出来时必须说出真实原因）。
        if not (self.can_apply() and self.candidate_bib
                and os.path.isfile(self.candidate_bib)):
            self._fail("对比", "还没有可对比的候选：先「生成拟应用结果」")
            self._refresh()
            return
        candidate_issue = self._candidate_diff_hint()
        if candidate_issue:
            self._fail("对比", candidate_issue)
            self._refresh()
            return
        try:
            from services import editor_bridge as ED
        except Exception:  # noqa: BLE001 - 打包缺模块时如实报错
            try:
                from ..services import editor_bridge as ED  # type: ignore[no-redef]
            except Exception as exc:  # noqa: BLE001
                self._fail("对比", f"对比模块不可用（{type(exc).__name__}）")
                self._refresh()
                return
        frozen, frozen_kind, frozen_message = self._frozen_identity()
        if not frozen or (initial and frozen_kind != FROZEN_OK):
            # 合同 v1.1：身份核不出来就禁用并说明原因，绝不退化冒充冻结版。
            self._fail("对比", frozen_message or "找不到冻结的稿件版 Bib：无法对比")
            launch = {"launch_requested": False, "window_observed": None} if initial else {"opened": False}
            self._mark("bib_ai_diff", frozen_kind=frozen_kind, frozen_reason=frozen_message, **launch)
            self._refresh()
            return
        work_dir = self.work_dir if initial else self._compare_work_dir()
        try:
            with open(self.candidate_bib, "rb") as handle:
                new_content = handle.read()
            preview = ED.make_preview(
                old_file=frozen, new_bytes=new_content, ext=".bib", work_dir=work_dir,
                expected_old_sha256=self._frozen_bib_sha if frozen_kind == FROZEN_OK else "",
                expected_new_sha256=self._candidate_bib_sha, dir_name=ED.COMPARE_DIRNAME)
        except ED.CandidateIntegrityError as exc:
            self._invalidate_preview(notice=str(exc))
            self._fail("对比", exc)
            self._refresh()
            return
        except Exception as exc:  # noqa: BLE001 - 生成失败如实报
            self._fail("对比", str(exc) or f"{type(exc).__name__}")
            self._refresh()
            return
        ok, message = ED.open_diff(preview)
        if initial:
            self._compare_paths = (preview.old_path, preview.new_path)
            self._compare_feedback = ("已请求 VS Code 打开对比。" if ok else
                                      f"无法启动 VS Code 对比：{message}")
            self._mark("bib_ai_diff", preview_id=preview.preview_id, launch_requested=ok,
                       window_observed=None, old_sha256=preview.old_sha256, new_sha256=preview.new_sha256,
                       frozen_kind=frozen_kind)
            self._refresh()
            return
        # 红线2-⑦：旧任务退化到 source_bib/target_bib 时**必须**说出来。降级说明
        # 追加在成功文案**之后**，不挡既有「已在编辑器里打开对比…」的前缀断言，
        # 也不改 `self.error`（这不是失败，但也不是「一切照旧」）。
        limitations = [] if frozen_kind == FROZEN_OK else [frozen_message]
        if not self._candidate_bib_sha:
            limitations.append("旧任务没有登记候选 Bib 的摘要，右侧副本无法核对身份。")
        degraded = f"（{' '.join(limitations)}）" if limitations else ""
        if ok:
            self.notice = (f"已在编辑器里打开对比（{preview.preview_id}）："
                           "只读展示，不会自动应用。" + degraded)
            self._mark("bib_ai_diff", preview_id=preview.preview_id, opened=True,
                       old_sha256=preview.old_sha256, new_sha256=preview.new_sha256,
                       frozen_kind=frozen_kind, degraded=bool(degraded))
        else:
            # 打不开编辑器**不是成功**：把两条路径原样给出，记成提示而不是成功态。
            # 文案里同时说明副本落在哪：合同 §4.2 要求落 `compare/`，**不落**
            # `.diff`（旧目录名）也不落 stage/，用户手工对比时要知道去哪儿找。
            compare_root = ED.preview_dir(work_dir, ED.COMPARE_DIRNAME)
            self.notice = (f"没能自动打开对比：{message}。"
                           f"旧版：{preview.old_path}｜新版：{preview.new_path}"
                           f"（副本在 {compare_root}，按合同不落 .diff 目录）"
                           + degraded)
            self._mark("bib_ai_diff", preview_id=preview.preview_id, opened=False,
                       old_sha256=preview.old_sha256, new_sha256=preview.new_sha256,
                       frozen_kind=frozen_kind, degraded=bool(degraded))
        self._refresh()

    def can_apply(self) -> bool:
        return (self.phase == "preview" and not self.blocked
                and not (callable(self._initial_call) and (self._preview_dirty or self.running))
                and bool(self.preview_id) and (self._r3 or not self.error))

    def can_undo(self) -> bool:
        if callable(self._initial_call):
            return (self.phase == "applied" and not self.undone and self.delivery_ok
                    and os.path.isdir(self._final_dir)
                    and (not self.backup or os.path.isdir(self.backup)))
        return self.phase == "applied" and (self._r3 or not self.error)

    # ── 阶段块（方案 B：按操作时序分块，按钮紧挨它操作的输入框）──────────────
    def _open(self, stage: str) -> bool:
        """该阶段块此刻是否展开（复用 `stage_for()`，不另造状态）。"""
        current = stage_for(self.phase, imported=self.imported)
        if self._r3:
            if self.phase == "preview":
                current = "done"
            return stage == current
        if stage == "waiting" and self.channel == "api" and current == "prepare":
            # API 的请求入口在②：准备阶段必须同时展开②，否则这条路不可达。
            return True
        return stage == current

    def _stage_box(self, key: str, *, open_: bool,
                   controls: list[ft.Control]) -> ft.Control:
        """一个阶段块：块标题 + 该阶段的输入/勾选 + 紧挨的专属按钮。

        非当前阶段**折叠**（`visible=False`）而不是删除：控件对象与 `_refs` /
        `_action_buttons` 登记都保留，动作实现和既有测试的控件契约不受显隐影响。
        """
        t = self.t
        box = ft.Column(spacing=t.sp("1"), visible=open_,
                        controls=[S.section(t, STAGE_LABELS[key]), *controls])
        box._bib_ai_stage = key
        self._stage_boxes[key] = box
        return box

    def _caption(self, text: str) -> ft.Control:
        return C.text(text, self.t, "caption", self.t.palette.text_faint, max_lines=2)

    def _copy_task_button(self) -> ft.Control:
        """「复制任务书全文」：出任务书成功后紧挨②的「导入结果」（用户真机点名）。

        它属于**等待阶段**：任务书生成后阶段①已折叠，若把按钮留在①，用户
        恰恰在最需要复制的时候看不到它。只由 `_waiting_block()` 构建一次，
        `_action_buttons["copy_task"]` 也只登记一次。
        """
        copy_btn = C.secondary_button("复制任务书全文",
                                      lambda e: self._do(self.copy_task_text), self.t, height=28)
        copy_btn.disabled = self.running or not (self.task_id or self.task_text.strip()
                                                or self._taskbook_cache is not None)
        if copy_btn.disabled:
            copy_btn.tooltip = "还没有任务书：先出任务书"
        copy_btn._bib_ai_copy_task = True
        self._action_buttons["copy_task"] = copy_btn
        return copy_btn

    def _diff_button(self) -> ft.Control:
        # DESIGN §10：拟应用结果 → 双文件 diff。只给「看一眼两侧文件」，
        # **不绑应用**：应用仍然只看 preview_id + 指纹；编辑器打不开时如实给两条路径。
        diff_btn = C.secondary_button("在 VS Code 里对比…",
                                      lambda e: self.open_diff(), self.t, height=28)
        diff_btn.disabled = not self._can_diff()
        if diff_btn.disabled:
            # 禁用要说清是哪一条不满足，且按「先决条件优先」排序：
            # 还没出有效预览时，先说「先生成拟应用结果」；已经出了预览、
            # 只剩左侧身份核不出来时，才说冻结 Bib 的事（红线2-⑦ 不静默）。
            if not (self.can_apply() and self.candidate_bib
                    and os.path.isfile(self.candidate_bib)):
                diff_btn.tooltip = "先「生成拟应用结果」：对比的是冻结的旧版与候选新版"
            else:
                diff_btn.tooltip = (self._candidate_diff_hint() or self._frozen_hint() or
                                    "先「生成拟应用结果」：对比的是冻结的旧版与候选新版")
        diff_btn._bib_ai_action = "diff"
        self._action_buttons["diff"] = diff_btn
        return diff_btn

    def _prepare_block(self, *, compact: bool) -> ft.Control:
        """阶段① 准备：路径 → 执行范围 → 出任务书（按操作先后）。

        「复制任务书全文」不在这里：它归②等待块，因为出任务书成功后①会折叠，
        按钮留在①就等于在用户最需要它时消失（真机缺陷 A）。
        """
        t = self.t
        controls: list[ft.Control] = [self._paths(compact=compact),
                                      self._execute_section()]
        if self.channel == "api" and not self._r3:
            # API 通道不用任务书：请求入口在阶段②（与「取回结果」同一语义），
            # 这里只留一行说明，免得用户在准备块里找一个不存在的按钮。
            controls.append(self._caption(
                "API 通道：选好目标 Bib 与执行范围后，在「等待结果」里直接请求。"))
        else:
            slot = self._primary_slot
            controls.append(ft.Row(
                spacing=t.sp("1"), wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self._button("prepare", primary=slot == ("prepare", "prepare")),
                ]))
        return self._stage_box("prepare", open_=self._open("prepare"),
                               controls=controls)

    def _waiting_block(self, *, compact: bool) -> ft.Control:
        """阶段② 等待结果：结果输入框 → 导入结果 / 复制任务书全文（紧挨输入框）。"""
        t = self.t
        slot = self._primary_slot
        controls: list[ft.Control] = []
        if callable(self._initial_call) and self._r3:
            scope_caption = self._r3_scope_caption()
            if scope_caption:
                controls.append(C.notice_bar(scope_caption, t, "warn"))
        if self.channel == "api":
            # API 通道必须**有入口**：只写一句「会产生费用」而不给按钮，
            # 等于这条路走不通（DESIGN §3.1「Chat/API 两条路都要能跑完」）。
            controls.append(C.notice_bar(
                "直接调 API：会走本机配置的 API，可能产生费用；失败不会自动重试。",
                t, "warn"))
            controls.append(ft.Row(
                spacing=t.sp("1"), wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self._button("request", primary=slot == ("waiting", "request")),
                    self._caption(
                        "用环境里配置的 Key 直接取结果（与 Chat 之后的解析同一条路）"),
                ]))
        else:
            if self._r3 or not compact:
                controls.append(self._result_picker())
            controls.append(self._body_paste_row())
            controls.append(ft.Row(
                spacing=t.sp("1"), wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self._button("import", primary=slot == ("waiting", "import")),
                    self._copy_task_button(),
                    self._caption("复制任务书给 AI → 把回复粘到上面 → 导入结果"),
                ]))
        if self._r3 and (self.channel != "api" or self.task_id):
            opener = getattr(self._app(), "open_path", None)
            if self.taskbook_path and callable(opener):
                controls.append(C.secondary_button("打开任务书",
                                                   lambda e: opener(self.taskbook_path), t))
            if self.channel == "api":
                controls.extend([self._result_picker(), self._body_paste_row(),
                                 self._button("import"), self._copy_task_button()])
            if self.purpose == "library":
                controls.append(C.secondary_button(
                    "采用完整外部 Bib…", lambda e: self._confirm_import("complete_external"),
                    t, disabled=self.running))
            else:
                controls.append(self._button("deliver_original", disabled=not self.task_id))
        return self._stage_box("waiting", open_=self._open("waiting"),
                               controls=controls)

    def _conflict_area(self) -> list[ft.Control]:
        """库结论 + 冲突裁决区（阶段③；两者都为空时返回空列表，不占版面）。"""
        return [*self._library_section(), *self._conflict_section()]

    def _review_block(self) -> ft.Control:
        """阶段③ 检查采纳：采纳范围 → 冲突裁决 → 生成拟应用结果 / 对比 / 应用。"""
        t = self.t
        slot = self._primary_slot
        return self._stage_box("review", open_=self._open("review"), controls=[
            self._accept_section(),
            *self._conflict_area(),
            ft.Row(spacing=t.sp("1"), wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       self._button("preview", primary=slot == ("review", "preview")),
                       # R3 的对比入口只挂在「完成初排转换」那一块（合同 §4.2：
                       # 与主操作同块、且全树只有这一条路径）；这里只给旧的非 R3
                       # 流程保留原位。
                       *([] if self._r3 else [self._diff_button()]),
                       (self._button("replace_reply") if self._r3 else
                        self._button("apply", primary=slot == ("review", "apply"),
                                     disabled=not self.can_apply())),
                   ]),
            *(self._caption(note) for note in self._review_notes),
            *([self._button("deliver_original")] if callable(self._initial_call) else []),
            self._caption("先定采纳范围 → 生成拟应用结果 → 对比无误再应用；"
                          "文字纠错默认不采纳。"),
        ])

    def _done_block(self) -> ft.Control:
        """阶段④ 完成：写入之后只剩撤销；失效态给「重新生成拟应用结果」。"""
        t = self.t
        slot = self._primary_slot
        buttons: list[ft.Control] = []
        if self._r3 and self.phase not in ("applied", "stale"):
            ready = self.phase == "preview"
            return self._stage_box("done", open_=ready, controls=[
                self._accept_section(),
                *self._conflict_area(),
                *(self._caption(note) for note in self._review_notes),
                ft.Row(wrap=True, controls=[
                    self._button("apply", primary=ready, disabled=not self.can_apply()),
                    self._diff_button(),
                    self._button("replace_reply"),
                    *([self._button("deliver_original")] if self.purpose == "initial" else []),
                ]),
            ])
        if self.phase == "stale":
            # 失效（撤销过 / 采纳范围改过 / 裁决改过）：先重新生成；此时撤销无处
            # 可使，所以主操作给「生成拟应用结果」，不再是一个灰着的强调按钮。
            # 冲突区在失效态仍保留：用户刚改过裁决，得看得见自己改了什么。
            buttons.append(self._button("preview",
                                        primary=slot == ("done", "preview")))
        buttons.append(self._button("undo", primary=slot == ("done", "undo"),
                                    disabled=not self.can_undo()))
        if callable(self._initial_call):
            if self.phase == "stale":
                buttons.append(self._button("deliver_original"))
            note = "撤销将恢复本次交付前的目录状态。"
            return self._stage_box("done", open_=self._open("done"),
                                   controls=[*self._conflict_area(),
                                             ft.Row(wrap=True, controls=buttons),
                                             self._caption(note)])
        note = ("撤销会把目标 Bib 恢复到写入前内容（引擎先备份过）。"
                if self.phase == "applied" else
                "结果已失效：重新生成拟应用结果；若刚撤销，目标 Bib 已回到写入前内容。")
        return self._stage_box("done", open_=self._open("done"), controls=[
            *self._conflict_area(),
            ft.Row(spacing=t.sp("1"), wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=buttons),
            self._caption(note),
        ])

    def _dock(self) -> ft.Control:
        t = self.t
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[
                          C.text("只读源稿 · 只写你选定的目标 Bib（引擎执行，先备份可撤销）",
                                 t, "caption", t.palette.text_faint, expand=True,
                                 max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                          self._button("refresh"),
                      ])

    # ── 引擎调用 ───────────────────────────────────────────────────────
    async def _run(self, call, *, private_lines=None) -> int | None:
        """跑一次引擎调用；返回退出码，出错返回 None（错误已写入 self.error）。"""
        runner = self._fn("run_call")
        if not callable(runner):
            self._bridge_fail(["run_call"])
            return None
        lines = [] if private_lines is None else private_lines
        if private_lines is None:
            self.log = lines
        identity = self._initial_identity() if callable(self._initial_call) else None

        def on_line(line) -> None:
            lines.append(str(line))

        def on_proc(proc) -> None:
            if self.log is lines or (private_lines is not None and identity == self._initial_identity()):
                self._proc = proc

        # 正文粘贴（不是路径）：`ai_import_call` 把正文挂在 call 上，这里原样喂进去。
        stdin_text = getattr(call, "stdin_text", "") or None
        # Key/notes 必须在 run_call 这一层显式传：`_invoke` 会静默丢弃 _call_kwargs
        # 里签名不匹配的键，塞进 call 工厂无效。Key 只经 env_for_run →
        # SLCN_LLM_KEY 进子进程环境，不进 argv/日志（UI-REWORK §2 / 调研结论）。
        # 用签名判断按契约传参：真桥 (engine_bridge.run_call) 收 api/notes → 一定传；
        # 只实现旧签名的测试假桥不收 → 不硬塞，避免把 Key 静默丢在 TypeError 里。
        run_kwargs: dict = dict(on_line=on_line, on_proc=on_proc,
                                cwd=None if self._r3 else self.work_dir or None,
                                stdin_text=stdin_text)
        explicit_api_only = getattr(call, "explicit_api_only", False)
        try:
            params = inspect.signature(runner).parameters
            variadic = any(p.kind == inspect.Parameter.VAR_KEYWORD
                           for p in params.values())
            if variadic or "api" in params:
                run_kwargs["api"] = None if explicit_api_only else self._api_env()
            if variadic or "notes" in params:
                run_kwargs["notes"] = self._notes()
        except (TypeError, ValueError):  # noqa: BLE001 - 取不到签名就按真桥全传
            run_kwargs["api"] = None if explicit_api_only else self._api_env()
            run_kwargs["notes"] = self._notes()
        try:
            code = await _await_if_needed(runner(call, **run_kwargs))
        except Exception as exc:  # noqa: BLE001 - 引擎异常也要有失败文案
            if private_lines is None and (identity is None or identity == self._initial_identity()):
                self._fail("引擎调用", f"{type(exc).__name__}: {exc}")
            return None
        if identity is not None and identity != self._initial_identity():
            return None
        try:
            return int(code)
        except (TypeError, ValueError):
            if private_lines is None:
                self._fail("引擎调用", f"返回值不是退出码：{code!r}")
            return None

    def _build_call(self, name: str, kwargs: dict):
        """调一个 call 工厂：缺函数/抛错/返回空都如实失败并返回 None。"""
        fn = self._fn(name)
        if not callable(fn):
            self._bridge_fail([name])
            return None
        dropped: list[str] = []
        try:
            call = _invoke(fn, kwargs, dropped)
        except Exception as exc:  # noqa: BLE001
            self._fail(name, f"{type(exc).__name__}: {exc}")
            return None
        if dropped:
            note = f"{name} 不接受参数：{'、'.join(dropped)}"
            if note not in self._wiring_notes:
                self._wiring_notes.append(note)
        if call is None:
            self._fail(name, "引擎桥没有返回调用对象")
            return None
        return call

    def _work_dir_path(self) -> str:
        """本次引擎调用用哪个工作区（**绝对路径**；`--work-dir` 是 CLI 必填项）。

        显式「工作目录」优先；没选就从**目标 Bib 所在目录**派生隐藏子目录
        `<那层>/.aiw`。为什么要派生：界面从来没人填过工作目录，于是 argv 里
        `--work-dir` 被桥当空值丢掉、argparse 直接退 2 —— 「出任务书」永远失败
        （问题 4 M1）。派生目录跟着 Bib 走，引擎的状态/undo 都在工作区里
        （验收 B2），换机器、清交付目录之后还能续跑，也不往稿件正文那一层写文件。
        连 Bib 都没选 → 返回 ""，由各动作给「先选…」的红字。
        """
        chosen = str(self.work_dir or "").strip()
        if chosen:
            return os.path.abspath(os.path.expanduser(chosen))
        target = str(self.target_bib or "").strip()
        if not target:
            return ""
        folder = os.path.dirname(os.path.abspath(os.path.expanduser(target)))
        if not folder:
            return ""
        return os.path.join(folder, WORK_DIR_NAME)

    def _call_kwargs(self) -> dict:
        """**两套命名都给**：桥的真实形参名 + 视图内部叫法。

        验收 §3.1-2/3 抓到的两个真缺陷就是这里漏了真实名：
        桥的形参是 `reply=` / `adopt=`，视图只给 `result_path` / `accept`，
        被 `_invoke` 按签名过滤掉 → argv 里既没有 `--reply` 也没有 `--adopt`，
        于是「选了结果文件」和「勾了采纳范围」在引擎眼里都等于没做。
        真名在前、别名在后；`_invoke` 只保留签名认得的那些。
        """
        execute, adopt = self.execute_scope(), self.accept_scope()
        return {
            "mode": self.purpose,
            "purpose": self.purpose,
            "entry": "convert" if self.purpose == "initial" else "bib",
            "source": "" if self._is_library_page() else self.source_path,
            "source_path": "" if self._is_library_page() else self.source_path,
            "target": self.target_bib,
            "target_bib": self.target_bib,
            # 验收 §3.1-2：桥的形参叫 `reply`
            "reply": self.result_path,
            "result_path": self.result_path,
            "tex": "" if self._is_library_page() else self.tex_path,
            "tex_path": "" if self._is_library_page() else self.tex_path,
            "tex_out": "" if self._is_library_page() else self.tex_out,
            "bib_out": self.bib_out,
            "result_text": self.body_text,
            "body_text": self.body_text,
            "stdin": bool(self.body_text.strip()) and not self.result_path.strip(),
            # 验收 §3.1-3：桥的形参叫 `adopt`
            "adopt": adopt,
            "accept": adopt,
            "execute": execute,
            "preview_id": self.preview_id,
            # `--work-dir` 是 CLI 必填项：给空值会被桥丢掉、argparse 退 2（M1）。
            "work_dir": self._work_dir_path(),
            # `request_call` 用得到（其余工厂会把它当签名差异过滤掉，仅留诊断）
            "taskbook": "",
            "reply_out": "",
            "taskbook_version": 3 if self._r3 else None,
            "task_id": self.task_id,
            "explicit_adopt": self._r3,
            "skip_items": sorted(self._skip_items),
            "json_output": self._r3,
            # 冲突裁决（合同 §2）：`--decide cf-…=library|reply|original` 一次下发。
            # **集中在这一处构造**：B 包落地后如果桥的形参名/形状变了，只改这里
            # 和 `_decide_payload()` 两个地方，不用在 6 个动作里各改一遍。
            "decisions": self.decision_scope(),
            "decide": self.decision_scope(),
        }

    def _task_store_key(self) -> str:
        return "bib_ai.active_initial" if callable(self._initial_call) else "bib_ai.active_library"

    def _task_inputs(self) -> list[str]:
        if callable(self._initial_call):
            return list(self.page.ai_initial_paths())
        if self._is_library_page():
            return [self.target_bib, ""]
        return [self.target_bib, self.tex_path]

    def _remember_task(self) -> None:
        self._store_set(self._task_store_key(), {
            "inputs": self._task_inputs(),
            "work_dir": self.work_dir,
            "task_id": self.task_id,
        })

    async def _run_r3(self, op: str, call, label: str, *, blocked_ok: bool = False,
                      on_result=None) -> dict | None:
        from services import engine_bridge as EB

        code = await self._run(call)
        if code is None:
            return None
        payload = EB.parse_json_document("\n".join(self.log))
        if on_result is not None:
            on_result(code, payload)
        if isinstance(payload, dict) and payload.get("op") == op:
            if code == 0 and payload.get("ok") is True:
                return payload
            if blocked_ok and payload.get("blocked"):
                return payload
            reasons = (payload.get("error") or payload.get("reason") or payload.get("message")
                       or payload.get("notes") or payload.get("blocked"))
            if isinstance(reasons, list):
                reasons = "；".join(map(str, reasons))
            self._fail(label, reasons or f"引擎返回退出码 {code}")
        elif code != 0:
            self._fail_code(label, code)
        else:
            self._fail(label, "引擎未返回可识别的操作结果")
        return None

    async def _read_status(self, work_dir: str) -> dict | None:
        call = self._build_call("status_call", {"work_dir": work_dir, "json_output": True})
        if call is None:
            return None
        return await self._run_r3("status", call, "读取任务状态")

    def _receive_status(self, status: dict) -> bool:
        issue = self._library_status_issue(status)
        if issue:
            self._deactivate_legacy_library_task(issue)
            return False
        candidate_sha = str(status.get("candidate_bib_sha256") or "")
        if (self.preview_id, self.candidate_bib, self._candidate_bib_sha) != (
                status.get("preview_id") or "", status.get("candidate_bib") or "", candidate_sha):
            if self.preview_id != (status.get("preview_id") or ""):
                self.compile_fix_eligible = {}
                self.compile_fix_notice = ""
                self._compile_fix_prepared = None
                self._compile_fix_pdf_opened = None
            self._clear_comparison()
        self.phase = phase_from_engine(status["phase"])
        self.scope_frozen = True
        self.preview_id = str(status.get("preview_id") or "")
        self.candidate_bib = str(status.get("candidate_bib") or "")
        self._candidate_bib_sha = candidate_sha
        self.blocked = [str(item) for item in status.get("blocked") or []]
        self.backup = str(status.get("backup") or "")
        self.key_count = _int_of(status.get("cite_keys"), self.key_count)
        self.entry_count = _int_of(status.get("bib_keys"), self.entry_count)
        engine_taskbook = status.get("taskbook")
        if isinstance(engine_taskbook, dict):
            engine_taskbook = engine_taskbook.get("path")
        self.taskbook_path = str(engine_taskbook or self.taskbook_path)
        target = str(status.get("target_bib") or "")
        # 合同 v1.1：`frozen_bib` 是**冻结快照**（`state["snapshot"]["bib"]`），
        # 与 `source_bib`（快照对应的源路径）不是一回事。字段缺失 = 旧 /1 任务，
        # 两个字段都留空，走「允许退化但必须明示」的判据；不要因此崩。
        frozen = status.get("frozen_bib")
        if isinstance(frozen, dict):
            self._frozen_bib_path = str(frozen.get("path") or "")
            self._frozen_bib_sha = str(frozen.get("sha256") or "")
        else:
            self._frozen_bib_path = ""
            self._frozen_bib_sha = ""
        self._source_bib = str(status.get("source_bib") or "")
        initial = callable(self._initial_call)
        if initial:
            self.target_bib = target
            self._original_final_dir = self._conversion_final_dir(status)
        elif status.get("source_bib") and target:
            self.target_bib = str(status["source_bib"])
            self.bib_out = target if target != self.target_bib else ""
        if "text_target" in status:
            self.tex_out = str(status.get("text_target") or "")
        scope = status.get("scope") or {}
        execution = status.get("execute", scope.get("exec"))
        if isinstance(execution, list):
            self.execute = {key: value in execution for key, value in CATEGORY_MAP.items()}
        adoption = status.get("adopt", scope.get("adopt"))
        if (not self._preview_dirty and isinstance(adoption, list)
                and (not callable(self._initial_call) or self.phase in ("preview", "applied", "blocked"))):
            self.accept = {key: value in adoption for key, value in CATEGORY_MAP.items()}
        self.imported = str(status["phase"]) == "imported"
        self._has_active_reply = bool(status.get("imported", status.get("n_replies")))
        if self._preview_dirty and self.phase in ("preview", "blocked"):
            self._clear_preview()
            self.phase = "waiting"
            self.imported = True
        else:
            if "skip_items" in status and not (initial and self._preview_dirty):
                if initial:
                    self._skip_items.clear()
                self._skip_items.update(status.get("skip_items") or [])
            self._receive_text_items(status.get("text_items") or {})
        delivery = status.get("delivery") or {}
        if isinstance(delivery, dict) and delivery:
            self._operation_receipt = dict(delivery)
        self.undone = bool(delivery.get("undone"))
        if initial:
            self._final_dir = self._delivered_output(delivery)
            self.delivery_ok = bool(self._final_dir)
            self.delivery_compile = compile_status_text(delivery) if delivery else ""
            self.delivery_error = str(delivery.get("error") or "") if not self.delivery_ok else ""
            self.backup = str(delivery.get("backup") or "") if self.delivery_ok else ""
        elif delivery:
            self._final_dir = str(delivery.get("final") or delivery.get("final_dir") or "") if not self.undone else ""
            delivered = bool(delivery.get("delivered", delivery.get("ok")))
            self.delivery_ok = bool(delivered and self._final_dir)
            self.delivery_compile = compile_status_text(delivery)
            self.delivery_error = str(delivery.get("error") or "") if not delivered else ""
        if not (initial and self._preview_dirty):
            self.notice = str(status.get("message") or "")
        self._receive_library(status)
        self._set_review_notes(status)
        self._enforce_library_boundaries()
        return True

    def _receive_library(self, payload: dict) -> None:
        """把 status/preview 里的库比对与冲突裁决收进视图状态（合同 §1/§2）。

        三条纪律：
        1. `library`/`conflicts` 每次整体替换 —— 引擎是唯一权威，界面不合并旧值。
        2. `decisions` **只增不减**：用户选过的保留；引擎下发了新裁决才覆盖对应项；
           引擎没提的条目按 default 走（`decision_scope()` 里补）。这样重新 preview
           不会把用户刚做的裁决悄悄改回「保留原值」。
        3. `preview_revision` 变化 = 引擎换了一份预览，界面把「旧预览已失效」的事实
           记下来（phase 由 `_receive_status` 按引擎 phase 决定）。
        """
        if not isinstance(payload, dict):
            return
        if "library" in payload:
            self.library = parse_library(payload)
        if "conflicts" in payload:
            self.conflicts = parse_conflicts(payload)
        engine_decisions = parse_decisions(payload)
        if engine_decisions and not (callable(self._initial_call) and self._preview_dirty):
            self.decisions.update(engine_decisions)
        revision = _int_of(payload.get("preview_revision"), self.preview_revision)
        if revision and revision != self.preview_revision:
            self.preview_revision = revision

    def _set_review_notes(self, payload: dict) -> None:
        notes = [str(note) for note in payload.get("notes") or []]
        category_results = payload.get("category_results") or {}
        if "category_results" in payload:
            self._category_reports = dict(category_results)
        counts = payload.get("categories") or {}
        status_labels = {
            "completed": "AI 报告已执行",
            "partial": "未完成核查",
            "not_run": "未执行",
            "unverified": "身份未验证",
        }
        count_labels = {
            "applied": "拟采纳", "reported": "待人工", "rejected": "拒绝",
            "backfilled": "补回旧版", "frozen": "保留旧版", "isolated": "隔离",
            "dropped": "已省略", "pending": "待核",
        }
        for key, category in CATEGORY_MAP.items():
            result = category_results.get(category) or {}
            values = counts.get(category) or {}
            state = str(result.get("status") or "")
            message = [status_labels.get(state, state), str(result.get("reason") or "")]
            message.extend(f"{label} {values[field]} {values.get('unit', '')}"
                           for field, label in count_labels.items() if values.get(field))
            details = " · ".join(filter(None, message))
            if details:
                notes.append(f"{CATEGORY_LABELS[key]} · {details}")
        if payload.get("identity_status") == "unverified":
            notes.append("回复身份未验证。")
        self._review_notes = notes

    def _receive_text_items(self, result: dict) -> None:
        applied = result.get("applied") or []
        skipped = [item for item in (result.get("reported") or [])
                   if item.get("item_id") in self._skip_items]
        for item in applied + skipped:
            item_id = str(item.get("item_id") or "")
            if item_id:
                self._text_items[item_id] = f"{item.get('old', '')} → {item.get('new', '')}"

    def _text_item_checks(self) -> list[ft.Control]:
        rows = []
        if not self._r3 or not self.accept.get("textfix"):
            return rows
        for item_id, label in self._text_items.items():
            def change(e, item=item_id):
                if self.running:
                    return
                if e.control.value:
                    self._skip_items.discard(item)
                else:
                    self._skip_items.add(item)
                self._invalidate_preview()
                self._refresh()
            row, _ = self._checkbox(label, item_id not in self._skip_items, change,
                                    disabled=self.running)
            rows.append(row)
        return rows

    async def _prepare_r3(self) -> None:
        initial = callable(self._initial_call)
        if self.running:
            return
        if initial:
            inputs = tuple(self._task_inputs())
            source, out = inputs
            if not source or not out:
                self._fail("生成任务书", "请先在初排页选择源稿件和输出根目录")
                self._refresh()
                return
            out = os.path.abspath(os.path.expanduser(out))
            out_parent, out_name = os.path.split(out)
            work = os.path.join(out_parent, f".{out_name}.aiw", uuid.uuid4().hex)
        else:
            if not self.target_bib.strip():
                self._fail("生成任务书", "请先选择待处理 Bib")
                self._refresh()
                return
            root = os.path.dirname(os.path.abspath(os.path.expanduser(self.target_bib)))
            work = os.path.join(root, WORK_DIR_NAME, uuid.uuid4().hex)

        self._clear_task()
        self._parent_paths = inputs if initial else None
        self.work_dir = work
        self.phase = "preparing"
        self.running = True
        self._initial_operation = "正在生成任务书…"
        self._refresh()
        expected_id, taskbook = "", ""
        if initial:
            try:
                call = self._initial_call(self, work)
                if call is not None and hasattr(call, "argv"):
                    from services import ai_prefs as AI
                    from services import engine_bridge as EB

                    store = getattr(self.page, "store", None) or getattr(self._app(), "store", None)
                    call.argv.extend(EB.r3_ai_budget_args(AI.raw_from_store(store)))
                code = await self._run(call)
                if code == 0:
                    reported = parse_status(self.log)
                    if os.path.normcase(os.path.realpath(reported.get("work_dir") or "")) == os.path.normcase(os.path.realpath(work)):
                        expected_id = reported.get("task_id", "")
                    taskbook = reported.get("taskbook") or parse_taskbook_path(self.log)
                elif code is not None:
                    self._fail_code("生成任务书", code)
            except Exception as exc:
                self._fail("生成任务书", str(exc))
            finally:
                self.page.finish_ai_initial_call()
        else:
            call = self._build_call("prepare_call", self._call_kwargs())
            payload = await self._run_r3("prepare", call, "生成任务书") if call else None
            if payload and os.path.normcase(os.path.realpath(payload.get("work_dir") or "")) == os.path.normcase(os.path.realpath(work)):
                expected_id = str(payload.get("task_id") or "")
                taskbook = str(payload.get("taskbook") or "")
        if not expected_id:
            if not self.error and not self._bridge_missing:
                self._fail("生成任务书", "引擎未返回本次任务根及任务身份")
            self.running = False
            self._refresh()
            return

        status = await self._read_status(work)
        if not status or not status.get("phase") or status.get("task_id") != expected_id:
            if status:
                self._fail("生成任务书", "引擎状态与本次任务身份不符")
            self._refresh()
            return
        self._check_parent_task()
        if self.work_dir != work:
            self.running = False
            self._refresh()
            return
        self.task_id = expected_id
        self.taskbook_path = taskbook
        if not self._receive_status(status):
            self._refresh()
            return
        self._ok()
        self._remember_task()
        self._notify("任务书已就绪")
        self._refresh()
        self._task(self._prefetch_taskbook)

    async def prepare(self) -> None:
        """出任务书（会冻结执行范围）。"""
        if self._r3:
            await self._prepare_r3()
            return
        if not self._require("prepare_call", "run_call"):
            self._refresh()
            return
        if not self.target_bib.strip():
            self._fail("出任务书", "先选目标 Bib")
            self._refresh()
            return
        call = self._build_call("prepare_call", self._call_kwargs())
        if call is None:
            self._refresh()
            return
        self.phase = "preparing"
        self.running = True
        self.error = ""
        self._refresh()
        code = await self._run(call)
        if code is None:
            self._refresh()
            return
        if code != 0:
            self._fail_code("出任务书", code)
            self._refresh()
            return
        self._ok()
        self.scope_frozen = True
        self.phase = "waiting"
        # 重出任务书 = 新任务：旧异步复制结果/缓存立刻作废。
        self._bump_request()
        # 仅保留引擎调用对象上可能的展示字段；**绝不**从日志正文拼任务书全文。
        # 复制走 copy_task_text() 的只读桥，取到的是引擎 JSON 的真实全文。
        self.task_text = str(getattr(call, "task_text", "") or "").strip()
        # 任务书路径只能从引擎日志取（EngineCall 没有这个属性）；取不到留空，
        # 状态区会如实写「路径见引擎日志」，不猜路径。
        self.taskbook_path = parse_taskbook_path(self.log)
        tid = str(getattr(call, "task_id", "") or self.task_id)
        # 上面的 _bump_request() 已经让旧任务作废；这里直接赋值，不再经 setter 重复 +1。
        self.task_id = tid
        self.notice = ("任务书已就绪：复制全文交给 AI，拿到结果后导入。"
                       if self.channel == "chat" else
                       "API 通道：任务书已就绪，等引擎返回结果。")
        self._notify("任务书已就绪")
        self._mark("bib_ai_prepare_ok", keys=len(self.log))
        self._refresh()
        # 进入等待阶段：异步预取一次任务书内容元信息（不发送 AI、不写文件）。
        self._task(self._prefetch_taskbook)

    async def request_api(self) -> None:
        """直连 API 取结果（DESIGN §8 `request`）。

        Key 只走环境（`env_for_run`），**不进 argv / 任务书 / 日志**；失败保留已取得的
        部分结果、不自动重试；成功也只是「结果已落盘」，仍需 import/preview 后才谈采纳。
        """
        initial = callable(self._initial_call)
        if initial and self.running:
            return
        if self.channel != "api":
            self._fail("直接请求", "先切到「直接调 API」通道（Chat 通道请复制任务书）")
            self._refresh()
            return
        if not self._require("request_call", "run_call"):
            self._refresh()
            return
        if self._r3 and not self.task_id:
            self._fail("直接请求", "请先生成任务书")
            self._refresh()
            return
        if not self._work_dir_path():
            self._fail("直接请求", "先选工作目录，或先选目标 Bib（会在同目录派生 .aiw）")
            self._refresh()
            return
        if initial and self._r3:
            from services import ai_prefs as AI

            try:
                request_identity = self._r3_api_request_identity()
            except (TypeError, ValueError) as exc:
                self._fail("直接请求", f"AI 预算或请求配置无效，已阻止请求：{exc}")
                self._refresh()
                return
            taskbook = await self._read_current_taskbook(force=True)
            fields = self._taskbook_cache if isinstance(self._taskbook_cache, dict) else {}
            if (not taskbook or request_identity != self._r3_api_request_identity()):
                self._fail("直接请求", "无法读取本次任务书范围；为避免扩大到全库，已阻止 API 请求")
                self._refresh()
                return
            scope = AI.r3_task_scope(taskbook)
            if not scope["known"]:
                self._fail("直接请求", scope["warning"])
                self._refresh()
                return
            if scope["count"] > AI.R3_VERIFY_MAX:
                self._fail("直接请求",
                           f"本次委派 {scope['count']} 条，超过固定上限 {AI.R3_VERIFY_MAX} 条；"
                           "已阻止请求。请缩小本次正文引用范围。")
                self._refresh()
                return
            try:
                store = getattr(self.page, "store", None) or getattr(self._app(), "store", None)
                from services import engine_bridge as EB

                request_env = EB.env_for_run(self._api_env())
                budgets = AI.r3_budget_estimate(
                    taskbook, AI.raw_from_store(store), request_env=request_env,
                )
            except ValueError as exc:
                self._fail("直接请求", f"AI 预算配置无效，已阻止请求：{exc}")
                self._refresh()
                return
            if budgets["unbounded_with_finite_budget"]:
                self._fail(
                    "直接请求",
                    "当前引擎输出不封顶，无法核实有限 token 预算；已阻止请求。"
                    "请设置正数 SLCN_LLM_MAX_TOKENS，或将 AI 预算明确设为 0（不限）。",
                )
                self._refresh()
                return
            if budgets["over_budget"]:
                self._fail("直接请求",
                           f"本次 token 用量估算约 {budgets['total_tokens']}，超过现有单篇或批次预算；"
                           "未发送请求。请缩小范围或调整 AI 设置预算。")
                self._refresh()
                return
            detail = (f"正文引用范围内预计委派 {scope['count']} 条（上限 {AI.R3_VERIFY_MAX} 条）。"
                      + (f"\n⚠ {scope['warning']}" if scope["warning"] else ""))
            if budgets["budget_verifiable"]:
                token_line = (
                    f"token 用量估算：输入约 {budgets['prompt_tokens']} + 输出上限 "
                    f"{budgets['completion_tokens']} = 约 {budgets['total_tokens']} tokens "
                    f"（{budgets['output_limit_source']}；不是实际用量）。"
                )
            else:
                token_line = (
                    f"输入约 {budgets['prompt_tokens']} tokens；输出不封顶，"
                    "无法计算 token 总量上界。"
                )
            impact = (
                token_line
                + f"\n单篇预算 {budgets['article_budget_tokens']} / 批次预算 "
                f"{budgets['batch_budget_tokens']} tokens。"
                "\n当前没有可核实的价格表，无法估算金额；此提示不是账单或费用承诺。"
            )
            expected_digest = hashlib.sha256(taskbook.encode("utf-8")).hexdigest()
            expected_path = str(fields.get("path") or "")
            consumed = False

            async def send_confirmed() -> None:
                nonlocal consumed
                if consumed:
                    return
                consumed = True
                await self._send_confirmed_r3_api(
                    request_identity, expected_digest, expected_path,
                )

            self._confirm(
                title="确认本次初排 AI API 请求", message=detail, impact=impact,
                callback=lambda: self._do(send_confirmed),
            )
            return

        call = self._build_call("request_call", self._call_kwargs())
        if call is None:
            self._refresh()
            return
        identity = self._initial_identity() if initial else None
        await self._run_api_request(call, initial, identity)

    def _r3_api_request_identity(self) -> tuple:
        """Bind one-use approval to this task, channel, request settings and budgets."""
        from services import ai_prefs as AI
        from services import engine_bridge as EB

        store = getattr(self.page, "store", None) or getattr(self._app(), "store", None)
        raw = AI.raw_from_store(store)
        article_budget, batch_budget = AI.r3_budget_limits(raw)
        configured = EB.ai_settings_env(AI.r3_request_settings(raw))
        env_names = (
            "SLCN_LLM_BASE", "SLCN_LLM_KEY", "SLCN_LLM_MODEL", "SLCN_LLM_PROTOCOL",
            "SLCN_LLM_SEARCH_TOOL", "SLCN_LLM_RESPONSES_PATH", "SLCN_LLM_THINKING",
            "SLCN_LLM_MAX_TOKENS", "SLCN_LLM_WEB_SEARCH", "SLCN_SEARCH_REFS",
        )
        effective = {name: configured.get(name, os.environ.get(name, ""))
                     for name in env_names}
        relevant = {
            "article_budget_tokens": article_budget,
            "batch_budget_tokens": batch_budget,
            "enabled": raw.get("enabled", AI.AI_DEFAULTS["enabled"]),
            "environment": effective,
        }
        encoded = json.dumps(relevant, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), default=str).encode("utf-8")
        config_digest = hashlib.sha256(encoded).hexdigest()
        return (self._initial_identity(), self.channel, str(self.task_id or ""),
                os.path.realpath(self._work_dir_path()), config_digest)

    async def _send_confirmed_r3_api(self, expected_identity: tuple,
                                     expected_digest: str, expected_path: str) -> None:
        """Re-read reviewed taskbook and rebuild the request immediately before dispatch."""
        from services import ai_prefs as AI

        try:
            current_identity = self._r3_api_request_identity()
        except (TypeError, ValueError) as exc:
            self._fail("直接请求", f"AI 预算或请求配置无效；旧确认已失效：{exc}")
            self._refresh()
            return
        if expected_identity != current_identity:
            self._fail("直接请求", "任务、通道或 API 配置已变化；旧确认已失效")
            self._refresh()
            return
        taskbook = await self._read_current_taskbook(force=True)
        fields = self._taskbook_cache if isinstance(self._taskbook_cache, dict) else {}
        current_digest = hashlib.sha256(taskbook.encode("utf-8")).hexdigest() if taskbook else ""
        current_path = str(fields.get("path") or "")
        try:
            unchanged = expected_identity == self._r3_api_request_identity()
        except (TypeError, ValueError):
            unchanged = False
        if (not unchanged or current_digest != expected_digest
                or current_path != expected_path):
            self._fail("直接请求", "任务书正文或路径已变化；旧确认已失效，请重新检查")
            self._refresh()
            return
        scope = AI.r3_task_scope(taskbook)
        if not scope["known"] or scope["count"] > AI.R3_VERIFY_MAX:
            self._fail("直接请求", scope["warning"] or
                       f"本次委派 {scope['count']} 条，超过固定上限 {AI.R3_VERIFY_MAX} 条；已阻止请求")
            self._refresh()
            return
        store = getattr(self.page, "store", None) or getattr(self._app(), "store", None)
        try:
            from services import engine_bridge as EB

            request_env = EB.env_for_run(self._api_env())
            budgets = AI.r3_budget_estimate(
                taskbook, AI.raw_from_store(store), request_env=request_env,
            )
        except ValueError as exc:
            self._fail("直接请求", f"AI 预算配置无效，已阻止请求：{exc}")
            self._refresh()
            return
        if budgets["unbounded_with_finite_budget"]:
            self._fail("直接请求", "当前输出不封顶，无法核实有限 token 预算；已阻止请求")
            self._refresh()
            return
        if budgets["over_budget"]:
            self._fail("直接请求", "当前任务书超出 AI token 预算，已阻止请求")
            self._refresh()
            return
        kwargs = self._call_kwargs()
        kwargs["taskbook"] = current_path
        kwargs["taskbook_sha256"] = current_digest
        call = self._build_call("request_call", kwargs)
        if call is None:
            self._refresh()
            return
        argv = list(getattr(call, "argv", ()) or ())
        try:
            digest_index = argv.index("--taskbook-sha256")
            bound_digest = argv[digest_index + 1]
        except (ValueError, IndexError):
            bound_digest = ""
        if bound_digest != current_digest:
            self._fail("直接请求", "引擎桥不支持任务书摘要绑定，已阻止 API 请求")
            self._refresh()
            return
        if current_path:
            try:
                path_index = argv.index("--taskbook")
                bound_path = argv[path_index + 1]
            except (ValueError, IndexError):
                bound_path = ""
            if bound_path != current_path:
                self._fail("直接请求", "引擎桥未绑定当前任务书路径，已阻止 API 请求")
                self._refresh()
                return
        try:
            initial_identity = self._initial_identity()
        except Exception:  # noqa: BLE001 - identity failure must fail closed
            initial_identity = None
        if initial_identity is None:
            self._fail("直接请求", "无法验证当前任务身份，已阻止 API 请求")
            self._refresh()
            return
        await self._run_api_request(
            call, True, initial_identity, request_identity=expected_identity,
        )

    async def _run_api_request(self, call, initial: bool, identity,
                               request_identity: tuple | None = None) -> None:
        """执行已通过界面范围/预算提示的 API 调用。"""
        try:
            request_unchanged = (request_identity is None
                                 or request_identity == self._r3_api_request_identity())
        except (TypeError, ValueError):
            request_unchanged = False
        if ((initial and identity != self._initial_identity()) or not request_unchanged):
            self._fail("直接请求", "任务或正文范围已变化，请重新检查后再确认")
            self._refresh()
            return
        self.running = True
        self._initial_operation = "正在请求 API…"
        self.error = ""
        self.notice = ""
        self._refresh()
        if self._r3:
            payload = await self._run_r3("request", call, "直接请求")
            if initial and not self._same_initial_task(identity):
                return
            if not payload:
                self._refresh()
                return
            self.result_path = str(payload.get("reply_path") or payload.get("reply") or "")
            imported = payload.get("imported")
            same_reply = initial and isinstance(imported, dict) and imported.get("idempotent") is True
            if not same_reply:
                if initial:
                    self._clear_initial_reply_review()
                    if isinstance(imported, dict):
                        self._set_review_notes(imported)
                else:
                    self._clear_preview()
                self.phase = "waiting"
            self._ok()
            self._has_active_reply = True
            self._initial_open_stage = ""
            self.notice = ("同一份回件，已保留当前检查结果。" if same_reply else
                           "模型结果已取回，检查后可生成拟应用结果。")
            self.imported = True
            self._refresh()
            return
        code = await self._run(call)
        if code is None:
            self._refresh()
            return
        if code != 0:
            self._fail_code("直接请求", code)
            self._refresh()
            return
        self._ok()
        self.imported = True
        self.phase = "waiting"
        self.notice = "模型结果已落盘到工作区；下一步「导入结果」再「生成拟应用结果」。"
        self._notify("请求完成，结果已落盘")
        self._mark("bib_ai_request_ok", chars=len(self.log))
        self._refresh()

    def _r3_scope_caption(self) -> str:
        fields = self._taskbook_cache if isinstance(self._taskbook_cache, dict) else {}
        text = str(fields.get("text") or "")
        if not text:
            return "任务书范围正在读取；读取失败时不会按全库范围请求。"
        from services import ai_prefs as AI

        scope = AI.r3_task_scope(text)
        if not scope["known"]:
            return scope["warning"]
        caption = f"本次正文引用任务书：预计委派 {scope['count']} 条；未引用条目不纳入范围。"
        return caption + (f" {scope['warning']}" if scope["warning"] else "")

    async def _read_current_taskbook(self, *, force: bool = False) -> str:
        """读取并校验当前 R3 任务书，供范围/数量保护使用。"""
        cached = self._taskbook_cache if isinstance(self._taskbook_cache, dict) else {}
        tid = str(self.task_id or "")
        if (not force and cached.get("ok") and cached.get("task_id") == tid
                and str(cached.get("text") or "").strip()):
            return str(cached["text"])
        fn = self._fn("taskbook_call")
        if not callable(fn):
            return ""
        wd = self._work_dir_path()
        if not wd or not tid:
            return ""
        seq = self.request_seq
        try:
            from services import engine_bridge as EB

            call = _invoke(fn, {"work_dir": wd, "task_id": tid})
            result = await asyncio.to_thread(EB.read_json_command, call)
            fields = EB.taskbook_result_fields(result)
        except Exception:  # noqa: BLE001 - failure to verify scope must fail closed
            return ""
        self._check_parent_task()
        if (seq != self.request_seq or wd != self._work_dir_path()
                or tid != str(self.task_id or "")
                or not fields.get("ok") or fields.get("task_id") != tid):
            return ""
        self._taskbook_cache = fields
        self.taskbook_path = str(fields.get("path") or self.taskbook_path)
        return str(fields.get("text") or "")

    def _begin_external_import(self, expected: tuple) -> None:
        if self.running or expected != self._initial_action_context():
            return
        if not self.task_id or self.phase == "applied":
            return
        if self._external_import_draft is None:
            self._external_import_draft = (self.result_path, self.body_text, self._initial_open_stage,
                                           self.error, self.notice)
        self.result_path = ""
        self.body_text = ""
        self.error = ""
        self.notice = ""
        self._initial_open_stage = "external"
        self._refresh()

    def _cancel_external_import(self) -> None:
        if self.running or self._external_import_draft is None:
            return
        (self.result_path, self.body_text, self._initial_open_stage,
         self.error, self.notice) = self._external_import_draft
        self._external_import_draft = None
        self._refresh()

    def _confirm_import(self, source_kind: str) -> None:
        identity = (self.work_dir, self.task_id, self.result_path, self.body_text)
        external = source_kind == "complete_external"
        initial_identity = self._initial_identity() if callable(self._initial_call) else None

        async def import_current() -> None:
            if (initial_identity != self._initial_identity()
                    or identity != (self.work_dir, self.task_id, self.result_path, self.body_text)):
                self._fail("导入结果", "任务或回件已变化，请重新确认")
                self._refresh()
                return
            await self.import_result(source_kind=source_kind, replace_active=True)

        def import_confirmed() -> None:
            if initial_identity is not None:
                self._do(import_current)
            elif identity == (self.work_dir, self.task_id, self.result_path, self.body_text):
                self._do(lambda: self.import_result(source_kind=source_kind, replace_active=True))

        self._confirm(title="采用完整外部 Bib" if external else "替换活动回复",
                      message=("完整外部 Bib 会成为合并基础，未引用且未出现在新文件中的旧条目可能省略。"
                               if external else "新回复将替换当前活动回复，已有预览会失效。"),
                      impact=self.result_path or "本次粘贴的正文",
                      callback=import_confirmed)

    async def import_result(self, *, source_kind: str = "ai_subset",
                            replace_active: bool = False) -> None:
        """导入 AI 结果（文件路径或正文；两者至少一个）。"""
        if self._r3 and not self.task_id:
            self._fail("导入结果", "请先生成任务书")
            self._refresh()
            return
        if not self._require("import_call", "run_call"):
            self._refresh()
            return
        if not self.result_path.strip() and not self.body_text.strip():
            self._fail("导入结果", "先选结果文件，或把 AI 回复正文粘贴到「AI 结果正文」")
            self._refresh()
            return
        if self.running:
            return
        if self._r3 and (self._has_active_reply or source_kind == "complete_external") and not replace_active:
            self._confirm_import(source_kind)
            return
        kwargs = self._call_kwargs()
        if self._r3:
            kwargs.update(source_kind=source_kind, replace_active=replace_active)
        call = self._build_call("import_call", kwargs)
        if call is None:
            self._refresh()
            return
        initial = callable(self._initial_call)
        identity = self._initial_identity() if initial else None
        self.running = True
        self._initial_operation = "正在读取回件…"
        self.error = ""
        self._refresh()
        if self._r3:
            payload = await self._run_r3("import", call, "导入结果")
            if initial and not self._same_initial_task(identity):
                return
            if payload:
                same_reply = initial and payload.get("idempotent") is True
                if not same_reply:
                    if initial:
                        self._clear_initial_reply_review()
                    else:
                        self._clear_preview()
                        self._preview_dirty = False
                        self._skip_items.clear()
                        self._text_items.clear()
                    self.phase = "waiting"
                    self._set_review_notes(payload)
                self._ok()
                self.imported = True
                self._has_active_reply = True
                self._initial_open_stage = ""
                self._external_import_draft = None
                self.notice = ("结果已导入，回复身份未验证。请生成拟应用结果。"
                               if payload.get("identity_status") == "unverified" else
                               "结果已导入，请生成拟应用结果。")
                self._notify("同一份回件，已保留当前检查结果。" if same_reply else "结果已导入")
            self._refresh()
            return
        code = await self._run(call)
        if code is None:
            self._refresh()
            return
        if code != 0:
            self._fail_code("导入结果", code)
            self._refresh()
            return
        self._ok()
        self.imported = True
        self.phase = "waiting"
        self.notice = "结果已导入；下一步「生成拟应用结果」。"
        self._notify("结果已导入")
        self._refresh()

    def _pick_preview_id(self, call) -> str:
        """取本次预览的 preview_id：先看调用对象的属性，再看引擎输出。

        每次预览都必须是一个**新的** preview_id（DESIGN §9）；两处都拿不到就
        留空 —— 应用保持禁用、并在状态区直说，绝不沿用上一次的旧 id 蒙混。
        """
        attr = str(getattr(call, "preview_id", "") or "").strip()
        if attr:
            return attr
        return str(parse_status(self.log).get("preview_id", "") or "").strip()

    async def preview(self) -> None:
        """生成拟应用结果（采纳范围在这里生效，每次重新生成 preview_id）。"""
        initial = callable(self._initial_call)
        if initial and self.running:
            return
        if initial:
            self._check_parent_task()
        if not self._require("preview_call", "run_call"):
            self._refresh()
            return
        if not self._r3 and not self.target_bib.strip():
            self._fail("生成拟应用结果", "先选目标 Bib")
            self._refresh()
            return
        if not self._r3 and not self.accept_scope():
            self._fail("生成拟应用结果", "采纳范围至少勾一项")
            self._refresh()
            return
        if self._r3 and not self.task_id:
            self._fail("生成拟应用结果", "请先生成任务书")
            self._refresh()
            return
        kwargs = self._call_kwargs()
        old_preview_id = self.preview_id
        previous_conflicts = {item["conflict_id"] for item in self.conflicts}
        identity = self._initial_identity() if initial else None
        if initial:
            self._initial_open_stage = "review"
            self._preview_dirty = self._preview_dirty or bool(old_preview_id)
            if self.phase in ("preview", "blocked", "stale"):
                self.imported = True
                self.phase = "waiting"
        if self._r3:
            # 预览只消费已登记者，绝不隐式再导入路径上的内容。
            kwargs["reply"] = ""
            self._clear_preview()
        call = self._build_call("preview_call", kwargs)
        if call is None:
            self._refresh()
            return
        self.running = True
        self._initial_operation = "正在生成拟应用结果…"
        self.error = ""
        self._refresh()
        if self._r3:
            payload = await self._run_r3("preview", call, "生成拟应用结果", blocked_ok=True)
            if initial and not self._same_initial_task(identity):
                return
            if payload:
                self.preview_id = str(payload.get("preview_id") or "")
                self.blocked = [str(item) for item in payload.get("blocked") or []]
                self._set_review_notes(payload)
                self._receive_text_items(payload.get("text") or {})
                # 库比对与冲突裁决随 preview 一起回来（合同 §1/§2）：先收，再定 phase，
                # 这样冲突区在 blocked 与 preview 两种状态下都能显示。
                self._receive_library(payload)
                self.phase = "blocked" if self.blocked else "preview"
                if not self.preview_id and not self.blocked:
                    self._fail("生成拟应用结果", "引擎未返回本次预览编号")
                elif self.blocked:
                    self.notice = ""
                    self._ok()
                else:
                    notes = self._review_notes
                    status = await self._read_status(self.work_dir)
                    if initial and not self._same_initial_task(identity):
                        return
                    if (status and status.get("task_id") == self.task_id
                            and status.get("preview_id") == self.preview_id):
                        self._preview_dirty = False
                        if not self._receive_status(status):
                            self._refresh()
                            return
                        if initial:
                            new_conflicts = {item["conflict_id"] for item in self.conflicts} - previous_conflicts
                            self._initial_open_stage = "review" if new_conflicts else ""
                        self._review_notes = list(dict.fromkeys(notes + self._review_notes))
                        self.notice = "拟应用结果已生成。"
                        self._ok()
                    else:
                        self._clear_preview()
                        self.phase = "waiting"
                        self.imported = True
                        if status:
                            self._fail("生成拟应用结果", "引擎状态与本次预览身份不符")
                self._mark("bib_ai_preview_ok", blocked=len(self.blocked),
                           has_preview=bool(self.preview_id),
                           preview_changed=bool(self.preview_id and self.preview_id != old_preview_id),
                           conflicts=len(self.conflicts),
                           library_checked=bool(self.library.get("checked")))
            self._refresh()
            return
        code = await self._run(call)
        if code is None:
            self._refresh()
            return
        if code != 0:
            self._fail_code("生成拟应用结果", code)
            self._refresh()
            return
        self._ok()
        self.preview_id = self._pick_preview_id(call)
        blocked = [str(item) for item in (getattr(call, "blocked", None) or [])]
        self.blocked = [item for item in blocked if item.strip()]
        self.key_count = _int_of(getattr(call, "keys", self.key_count), self.key_count)
        self.entry_count = _int_of(getattr(call, "entries", self.entry_count),
                                   self.entry_count)
        self.phase = "blocked" if self.blocked else "preview"
        if self.blocked:
            self.notice = ""
        elif not self.preview_id:
            self.notice = "引擎没有返回 preview_id：应用会保持禁用。"
        else:
            self.notice = f"可预览：{len(self.log)} 行引擎输出；确认后点「应用到目标 Bib」。"
        self._notify("拟应用结果已生成" if not self.blocked else "拟应用结果有阻断项")
        self._mark("bib_ai_preview_ok", blocked=len(self.blocked),
                   has_preview=bool(self.preview_id))
        self._refresh()

    @staticmethod
    def _conversion_final_dir(status: dict) -> str:
        conversion = status.get("conversion") or {}
        return str(conversion.get("final_dir") or "").strip()

    def _initial_identity(self) -> tuple:
        return (self.request_seq, self.task_id, self.work_dir,
                os.path.realpath(self.work_dir) if self.work_dir else "",
                self.page.ai_initial_paths() if callable(self._initial_call) else None)

    def _initial_action_context(self) -> tuple:
        """把按钮与所展示的任务、回件及预览绑定。"""
        return (self._initial_identity(), self._reply_revision, self.phase,
                self.preview_id, self.preview_revision, self.candidate_bib,
                self._original_final_dir, self._final_dir, self.backup,
                self._initial_open_stage)

    def _same_initial_task(self, expected: tuple) -> bool:
        self._check_parent_task()
        if expected == self._initial_identity():
            return True
        self.running = False
        self._refresh()
        return False

    async def deliver_original(self) -> None:
        if self.running or self._original_dialog in C._open_dialogs:
            return
        self._check_parent_task()
        if not (self.task_id and self.work_dir and self._original_final_dir) or self.phase == "applied":
            self._fail("按原稿交付", "请刷新状态以取得本次任务的最终输出目录")
            self._refresh()
            return
        identity = self._initial_identity()
        final = self._original_final_dir
        preview_inputs = (self.phase, self.preview_id, self.accept_scope(),
                          self.decision_scope(), set(self._skip_items))
        labels = [label for key, label in CATEGORIES if CATEGORY_MAP[key] in self.execute_scope()]
        skipped = "、".join(labels) or "本次任务的 AI 结果"

        def unchanged() -> bool:
            return (identity == self._initial_identity() and final == self._original_final_dir
                    and preview_inputs == (self.phase, self.preview_id, self.accept_scope(),
                                           self.decision_scope(), self._skip_items))

        def changed() -> None:
            self._fail("按原稿交付", "任务或交付上下文已变化，请返回检查后重新确认")
            self._refresh()

        async def confirm_delivery() -> None:
            if self.running:
                return
            if not unchanged():
                changed()
                return
            self.running = True
            self._initial_operation = "正在核对交付任务…"
            self._refresh()
            status = await self._read_status(identity[3])
            if not unchanged():
                changed()
                return
            if status is None:
                self.running = False
                self._refresh()
                return
            if (status.get("task_id") != identity[1] or self._conversion_final_dir(status) != final
                    or phase_from_engine(status.get("phase", "")) == "applied"):
                changed()
                return
            self.running = False
            await self._deliver(use_original=True, confirmed_work_dir=identity[3])

        self._original_dialog = S.danger_confirm(
            self._flet(), self.t, title="按原稿交付", message=f"最终输出目录\n{final}",
            impact=f"本次交付将跳过以下 AI 结果：{skipped}。引擎将按冻结的初排设置交付。",
            cancel_label="返回检查", cancel_autofocus=True, confirm_label="确认按原稿交付",
            on_confirm=lambda: self._do(confirm_delivery),
        )

    def _log_line(self, text: str) -> None:
        """把一行写进宿主底部「运行日志」（合同 §4.3），并留在 `delivery_log` 里。

        宿主没有日志服务时（测试假宿主、旧宿主）静默跳过：日志是**证据**，
        不是成功判据——判据只看引擎返回的 final 与状态，绝不因为写不进日志
        就把交付判成失败，也绝不因为写不进日志就假装成功。
        """
        line = str(text)
        self.delivery_log.append(line)
        page = self.page
        for name in ("log_line", "log"):
            fn = getattr(page, name, None)
            if callable(fn):
                try:
                    fn(line)
                    return
                except Exception:  # noqa: BLE001 - 日志失败不影响业务结论
                    pass
        app = self._app()
        fn = getattr(app, "record_line", None)
        if callable(fn):
            try:
                fn(line)
            except Exception:  # noqa: BLE001
                pass

    def _log_delivery(self, *, task_id: str, final: str, compile_text: str,
                      ok: bool, error: str = "", use_original: bool = False) -> None:
        """按合同 §4.3 写完成态日志：任务号 / 编译状态 / 最终路径（失败也写）。"""
        for line in delivery_log_lines(task_id=task_id, final=final,
                                       compile_text=compile_text, ok=ok,
                                       error=error, use_original=use_original):
            self._log_line(line)

    def _open_delivery_dir(self) -> None:
        """成功卡主操作：用宿主既有 `app.open_path` 打开引擎返回的交付目录。"""
        path = str(self._final_dir or "").strip()
        opener = getattr(self._app(), "open_path", None)
        if not path:
            self._fail("打开交付目录", "引擎没有返回最终交付路径")
            self._refresh()
            return
        if not callable(opener):
            self._fail("打开交付目录", "宿主没有提供打开路径的服务")
            self._refresh()
            return
        self._mark("bib_ai_open_final", path=path)
        try:
            opener(path)
        except Exception as exc:  # noqa: BLE001 - 打不开如实报，不静默
            self._fail("打开交付目录", f"{type(exc).__name__}: {exc}")
        self._refresh()

    async def _deliver(self, *, use_original: bool = False, confirmed_work_dir: str = "",
                       compile_fix=None) -> None:
        if self.running:
            return
        self._check_parent_task()
        if (not self.task_id or self.phase == "applied"
                or not (use_original or self.can_apply())):
            self._fail("完成初排转换", "请先生成任务书与可交付的拟应用结果")
            self._refresh()
            return
        settings = self._compile_fix_settings()
        enabled = settings.get("ai_enabled") is True and settings.get("compile_fix_enabled") is True
        if compile_fix and not enabled:
            self._set_compile_fix(None, "设置已改变，未交付本次修复。")
            return
        params = {
            "work_dir": confirmed_work_dir or self.work_dir,
            "preview_id": "" if use_original else self.preview_id,
            "use_original": use_original,
        }
        if enabled and not use_original:
            params.update(compile_fix_enabled=True, ai_enabled=True)
        if compile_fix:
            params.update({key: compile_fix[key] for key in ("evidence_id", "request_id", "review_sha256")})
        call = self._build_call("deliver_call", params)
        if not call:
            if compile_fix:
                self._set_compile_fix(None, self.error or "无法启动本轮编译修复交付。")
            else:
                self._refresh()
            return
        identity = self._initial_identity()
        preview_id = self.preview_id
        from services import engine_bridge as EB
        eligible = None
        verified = False
        compiled = self.compile_fix_eligible
        context = self._initial_action_context()

        def capture(code, receipt):
            nonlocal eligible, verified
            if enabled and not use_original and not compile_fix:
                eligible = EB.compile_fix_result(code, receipt, op="deliver", task_id=identity[1],
                                                 preview_id=preview_id)
            if compile_fix:
                verified = EB.compile_fix_delivery_matches(code, receipt, task_id=identity[1],
                                                           preview_id=preview_id, compiled=compiled)

        self.running = True
        self._initial_operation = "正在交付初排…"
        self.error = ""
        self.delivery_ok = False
        self.delivery_compile = ""
        self.delivery_error = ""
        if compile_fix:
            self._set_compile_fix(None, "正在交付本轮编译修复…")
        else:
            _refresh_workspace(self)
        payload = await self._run_r3("deliver", call, "完成初排转换", on_result=capture)
        if not self._same_initial_task(identity):
            return
        self.running = False
        if payload:
            if compile_fix and (not verified or not self._delivered_output(payload)):
                self._fail("核实编译修复交付", "交付回执与本次请求或复编运行不匹配，请刷新任务状态核对")
                self._set_compile_fix({**compiled, "status": "check_status"}, self.error)
                self._record_delivery_failure(self.error, compile_text="交付回执不匹配", use_original=False)
                return
            await self._finish_delivery(payload, use_original=use_original, identity=identity)
            if self.delivery_ok:
                self._set_compile_fix({})
        else:
            receipt = EB.parse_json_document("\n".join(self.log)) or {}
            current = self._compile_fix_settings()
            if compile_fix:
                self._set_compile_fix({**compiled, "status": "check_status"}, "请先恢复本地状态核对本次交付。")
            if (eligible and context == self._initial_action_context() and current.get("ai_enabled") is True
                    and current.get("compile_fix_enabled") is True):
                self.compile_fix_eligible = eligible
                self._compile_fix_prepared = None
                self._compile_fix_pdf_opened = None
            compile_text = compile_status_text(receipt) if receipt.get("op") == "deliver" else "引擎未报告"
            self._record_delivery_failure(self.error, compile_text=compile_text, use_original=use_original)
        if not compile_fix or self.delivery_ok:
            _refresh_workspace(self)

    def _compile_fix_settings(self) -> dict:
        try:
            from services import ai_prefs as AI
            from services import engine_bridge as EB
            return EB.compile_fix_settings(AI.raw_from_store(self._app().store))
        except (AttributeError, TypeError, ValueError):
            return {"ai_enabled": False, "compile_fix_enabled": False, "key": ""}

    def _set_compile_fix(self, result, notice="", *, prepared=None) -> None:
        if result is not None:
            self.compile_fix_eligible = result
            self._compile_fix_prepared = prepared
            if self._compile_fix_pdf_opened != self._compile_fix_pdf_identity(result):
                self._compile_fix_pdf_opened = None
        self.compile_fix_notice = notice
        if self._compile_fix_row is not None:
            self._compile_fix_row.controls = self._compile_fix_controls()
            try:
                self._compile_fix_row.update()
            except (AssertionError, RuntimeError):  # Not mounted yet.
                pass

    def _compile_fix_controls(self) -> list[ft.Control]:
        t = self.t
        settings = self._compile_fix_settings()
        result = self.compile_fix_eligible
        if (not callable(self._initial_call) or not self.preview_id or self.phase != "preview"
                or (result and (result.get("task_id") != self.task_id
                                or result.get("preview_id") != self.preview_id))):
            return []
        enabled = settings.get("ai_enabled") is True and settings.get("compile_fix_enabled") is True
        status = result.get("status") or ("eligible" if result.get("eligible") else "")
        if not enabled and not status:
            return []
        controls = []
        if self.compile_fix_notice:
            controls.append(C.inline_note(self.compile_fix_notice, t, "info"))
        buttons = []
        identity = self._initial_action_context()
        ids = {key: result.get(key, "") for key in ("evidence_id", "request_id", "review_sha256")}

        def button(label, callback):
            control = C.secondary_button(label, callback, t)
            control.disabled = self.running
            buttons.append(control)

        def operation(action, **extra):
            self._task(lambda: self._compile_fix_action(action, expected=identity, **extra))

        def local_path(key, label, *, pdf=False):
            path = result.get(key)
            if pdf:
                from services import engine_bridge as EB
                opener = EB.open_path
            else:
                opener = getattr(self._app(), "open_path", None)

            def open_local(_):
                if self.running or identity != self._initial_action_context() or result is not self.compile_fix_eligible:
                    return
                try:
                    if not isinstance(path, str) or not path or not os.path.isfile(path):
                        raise OSError
                    outcome = opener(path)
                    if (outcome[0] is not True if pdf else outcome is False):
                        raise OSError
                except OSError:
                    self._set_compile_fix(None, "无法打开本轮本地产物。")
                    return
                if pdf:
                    self._compile_fix_pdf_opened = self._compile_fix_pdf_identity(result)
                    self._set_compile_fix(None, "")

            button(label, open_local)
            if not isinstance(path, str) or not path or not callable(opener):
                buttons[-1].disabled = True

        def choose_scope(_):
            if (identity != self._initial_action_context() or self.running
                    or result is not self.compile_fix_eligible or not self.can_apply()):
                return
            from ui import compile_fix_dialog as CFD
            CFD.show_scope_dialog(self._flet(), t, self._compile_fix_evidence(result),
                                  result.get("diagnostics") or [],
                                  lambda selected: self._begin_compile_fix(selected, expected=identity, prior=result),
                                  selected=(result.get("scope") or {}).get("evidence_ids"))

        if status == "unknown":
            controls.append(C.inline_note("请求已发出但未收到可用回件", t, "warn"))
            local_path("log_path", "查看本地日志")
            button("新轮重试…", choose_scope)
            buttons[-1].disabled = self.running or not enabled or not self.can_apply()
            button("放弃并保留原产物", lambda e: operation("cancel", **ids))
            return [*controls, ft.Row(wrap=True, spacing=t.sp("1"), controls=buttons)]

        if status in {"eligible", "prepared", "compile_failed", "cancelled"} and enabled:
            labels = {"eligible": "选择范围并准备请求…", "prepared": "更改范围并重新授权…"}
            button(labels.get(status, "新轮重试…"), choose_scope)
        if status == "prepared":
            from ui import compile_fix_dialog as CFD
            if enabled:
                button("审阅待发送请求…", lambda e: self._task(lambda: self._resume_compile_fix(identity, result)))
                buttons[-1].disabled = self.running or self._compile_fix_prepared is None
            button("导入本地回件…", lambda e: CFD.show_reply_dialog(
                self._flet(), t, lambda text: operation("import", reply_text=text, **ids)))
        if status in {"imported", "reviewed", "compile_failed"}:
            button("审阅回件差异…", lambda e: self._task(lambda: self._review_compile_fix(identity, ids)))
        if status == "compiled":
            controls.append(C.inline_note("隔离复编已通过；请先打开 PDF 验版，再确认交付。", t, "info"))
            local_path("pdf_path", "打开复编 PDF", pdf=True)
            if enabled and all(ids.values()):
                button("确认验版并交付…", lambda e: self._confirm_compile_fix_delivery(identity, ids, result))
                buttons[-1].disabled = self.running or self._compile_fix_pdf_opened != self._compile_fix_pdf_identity(result)
        if not status or status == "check_status":
            button("恢复本稿编译修复状态", lambda e: self._task(
                lambda: self._restore_compile_fix_status(identity)))
        if buttons:
            controls.append(ft.Row(wrap=True, spacing=t.sp("1"), controls=buttons))
        return controls

    async def _review_compile_fix(self, identity, ids) -> None:
        result = await self._compile_fix_action("review", expected=identity,
                                               evidence_id=ids["evidence_id"], request_id=ids["request_id"])
        if not result or result.get("status") != "reviewed":
            return
        from ui import compile_fix_dialog as CFD
        apply_ids = {key: result.get(key) for key in ("evidence_id", "request_id", "review_sha256")}

        async def apply():
            if all(apply_ids.values()) and result.get("can_adopt") is True:
                if (identity != self._initial_action_context() or not self.can_apply()
                        or self.compile_fix_eligible.get("status") != "reviewed"
                        or any(self.compile_fix_eligible.get(key) != val for key, val in apply_ids.items())):
                    return
                await self._compile_fix_action("apply", expected=identity, **apply_ids)

        CFD.show_review_dialog(self._flet(), self.t, result, apply if all(apply_ids.values())
                               and result.get("can_adopt") is True else None)

    def _compile_fix_pdf_identity(self, result):
        if result.get("status") == "compiled":
            return (self._initial_action_context(), *(result.get(key) for key in
                    ("evidence_id", "request_id", "review_sha256", "compile_run_id", "pdf_path")))
        return None

    async def _publish_compile_fix(self, identity, ids, pdf_identity) -> None:
        if (not self.running and identity == self._initial_action_context()
                and self.compile_fix_eligible.get("status") == "compiled"
                and pdf_identity == self._compile_fix_pdf_opened == self._compile_fix_pdf_identity(self.compile_fix_eligible)
                and all(self.compile_fix_eligible.get(key) == value for key, value in ids.items())):
            await self._deliver(compile_fix=ids)

    def _confirm_compile_fix_delivery(self, identity, ids, result) -> None:
        pdf_identity = self._compile_fix_pdf_identity(result)
        if (self.running or identity != self._initial_action_context() or result is not self.compile_fix_eligible
                or not pdf_identity or pdf_identity != self._compile_fix_pdf_opened):
            return
        from ui import compile_fix_dialog as CFD
        CFD.show_delivery_dialog(self._flet(), self.t, lambda: self._task(
            lambda: self._publish_compile_fix(identity, ids, pdf_identity)))

    async def _compile_fix_action(self, action, *, expected=None, settings=None, quiet_missing=False, **extra):
        context = self._initial_action_context()
        if (self.running or (expected is not None and context != expected)
                or self.phase != "preview" or not self.preview_id or not self.task_id):
            return
        from services import engine_bridge as EB
        previous = self.compile_fix_eligible
        settings = self._compile_fix_settings() if settings is None else settings
        if action != "status" and any(extra[key] != previous.get(key, "") for key in
                                      ("evidence_id", "request_id", "review_sha256") if key in extra):
            return
        if action in {"prepare", "send", "apply"} and not self.can_apply():
            return
        if action == "apply" and not (settings.get("ai_enabled") is True and settings.get("compile_fix_enabled") is True):
            self._set_compile_fix(None, "AI 总开关或 AI 辅助编译修复未开启；未采纳。")
            return
        try:
            call = EB.ai_compile_fix_call(action=action, work_dir=self.work_dir, preview_id=self.preview_id,
                                          settings=settings, **extra)
        except (RuntimeError, ValueError) as exc:
            self._set_compile_fix(None, str(exc))
            return
        self.running = True
        if action in {"prepare", "send"}:
            self._compile_fix_prepared = None
        self._set_compile_fix(None, "正在处理编译修复…")
        private_lines = []
        try:
            code = await self._run(call, private_lines=private_lines)
        finally:
            self.running = False
        if context != self._initial_action_context():
            return
        payload = EB.parse_json_document("\n".join(private_lines)) or {}
        result = EB.compile_fix_action_result(code, payload, action=action, task_id=self.task_id,
                                              preview_id=self.preview_id, previous=previous, arguments=extra)
        if result is None:
            if action == "send":
                self._set_compile_fix({**previous, "status": "check_status"},
                                      "未能确认请求结果；请先查看本地状态，不会自动重发。")
            elif action == "status" and quiet_missing and not previous:
                self._set_compile_fix(None, "")
            else:
                notice = str(payload.get("error") or "引擎未返回与本轮匹配的编译修复回执。")
                stale = {**previous, "status": "check_status"} if previous else {}
                self._set_compile_fix(stale, notice)
            return
        visible = {k: v for k, v in result.items() if k not in {"body", "summary", "authorization"}}
        prepared = None
        if result["status"] == "prepared":
            scope = self._compile_fix_scope(result)
            if (not self._valid_compile_fix_disclosure(result, scope)
                    or (action == "prepare" and scope != extra["evidence_ids"])):
                self._set_compile_fix({**previous, "status": "check_status"} if previous else {},
                                      "引擎未返回完整且匹配的授权对象；未发送。")
                return
            # Recovered receipts have no trusted local settings/key snapshot. Re-prepare before consenting.
            prepared = (result, context, settings if action == "prepare" else None, scope)
        self._set_compile_fix(visible, str(payload.get("error") or "") if result["status"] != "unknown" else "",
                              prepared=prepared)
        return result if code == 0 else None

    @staticmethod
    def _compile_fix_evidence(result):
        items = result.get("evidence")
        return [item for item in items if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
                and item["evidence_id"]] if isinstance(items, list) else []

    @staticmethod
    def _compile_fix_scope(result):
        scope = result.get("scope")
        return scope.get("evidence_ids") if isinstance(scope, dict) else None

    async def _begin_compile_fix(self, evidence_ids, *, expected, prior) -> None:
        context = self._initial_action_context()
        if self.running or context != expected or prior is not self.compile_fix_eligible or not self.can_apply():
            return
        settings = self._compile_fix_settings()
        if not settings.get("key"):
            self._set_compile_fix(None, "未配置本机 API Key；没有发出请求。")
            return
        signed = [item["evidence_id"] for item in self._compile_fix_evidence(prior)]
        if (not evidence_ids or any(key not in signed for key in evidence_ids)
                or len(set(evidence_ids)) != len(evidence_ids)):
            self._set_compile_fix(None, "必须至少选择一项本轮签发的诊断证据；未准备请求。")
            return
        evidence_ids = [key for key in dict.fromkeys(signed) if key in evidence_ids]
        result = await self._compile_fix_action("prepare", settings=settings, expected=context,
                                               evidence_id=prior.get("evidence_id", ""),
                                               evidence_ids=evidence_ids)
        if result and self._compile_fix_prepared is not None:
            await self._resume_compile_fix(context, self.compile_fix_eligible)

    async def _resume_compile_fix(self, context, prior):
        entry = self._compile_fix_prepared
        if (self.running or context != self._initial_action_context() or prior is not self.compile_fix_eligible
                or prior.get("status") != "prepared" or entry is None or entry[1] != context
                or not self.can_apply()):
            return
        settings = self._compile_fix_settings()
        if not (settings.get("key") and settings.get("ai_enabled") is True
                and settings.get("compile_fix_enabled") is True):
            self._set_compile_fix(None, "缺少本机 API Key 或未开启 AI 编译修复；没有发出请求。")
            return
        if entry[2] != settings:
            # Re-freeze once; do not recurse if settings also change while prepare is awaiting its receipt.
            result = await self._compile_fix_action("prepare", settings=settings, expected=context,
                                                   evidence_id=prior["evidence_id"], evidence_ids=entry[3])
            if not result or settings != self._compile_fix_settings():
                return
            entry = self._compile_fix_prepared
        if entry is not None and entry[2] == self._compile_fix_settings():
            self._show_prepared_compile_fix(entry)

    @staticmethod
    def _valid_compile_fix_disclosure(result, evidence_ids) -> bool:
        from services import engine_bridge as EB
        return EB.compile_fix_disclosure_valid(result, evidence_ids)

    def _show_prepared_compile_fix(self, entry):
        from ui import compile_fix_dialog as CFD
        result, context, settings, evidence_ids = entry

        async def send(prepared, ack):
            from services import engine_bridge as EB
            current_entry = self._compile_fix_prepared
            if current_entry is not entry or context != self._initial_action_context():
                return
            matches = EB.compile_fix_authorization_matches(
                prepared=entry, current=current_entry, expected_context=context,
                current_context=self._initial_action_context(), expected_settings=settings,
                current_settings=self._compile_fix_settings(), expected_scope=result["scope"],
                current_scope=self.compile_fix_eligible.get("scope"))
            if (ack is not True or not matches or not self.can_apply()
                    or prepared != result
                    or not self._valid_compile_fix_disclosure(prepared, evidence_ids)):
                self._compile_fix_prepared = (result, context, None, evidence_ids)
                self._set_compile_fix(None, "请求范围、任务或设置已变化，或确认不完整；未发送。")
                return
            await self._compile_fix_action("send", settings=settings, expected=context,
                                           terms_ack=ack, evidence_ids=evidence_ids,
                                           **{key: prepared[key] for key in ("evidence_id", "request_id",
                                                "request_sha256", "authorization_id")})

        CFD.show_authorization_dialog(self._flet(), self.t, result, send)

    async def _restore_compile_fix_status(self, identity, *, quiet_missing=False):
        if identity != self._initial_action_context() or not self.preview_id:
            return
        await self._compile_fix_action("status", expected=identity, quiet_missing=quiet_missing)

    def _compile_fix_retention_button(self):
        identity = self._initial_identity()
        return C.secondary_button("管理编译修复本机留存…", lambda e: self._task(
            lambda: self._compile_fix_retention(identity)), self.t, disabled=self.running)

    async def _compile_fix_retention(self, identity, record=None) -> None:
        if (self.running or identity != self._initial_identity() or not self.task_id or not self.work_dir
                or (record is not None and record["task_id"] != self.task_id)):
            return
        from services import engine_bridge as EB
        from ui import compile_fix_dialog as CFD

        try:
            call = EB.ai_compile_fix_call(action="delete" if record else "retained",
                                          work_dir=self.work_dir, settings={},
                                          record_key=record["record_key"] if record else "")
        except (RuntimeError, ValueError):
            CFD.show_retained_dialog(self._flet(), self.t, message="无法启动本机留存操作。")
            return
        self.running = True
        lines = []
        try:
            code = await self._run(call, private_lines=lines)
        finally:
            self.running = False
        if identity != self._initial_identity():
            return
        result = EB.compile_fix_retained_result(code, EB.parse_json_document("\n".join(lines)),
                                                task_id=self.task_id, record=record)
        if record is not None:
            message = {"deleted": "已删除这条本机留存记录。", "expired": "这条本机留存记录已过期清除。"}.get(
                result, "未能核实删除结果；请重新查看本机留存。")
            CFD.show_retained_dialog(self._flet(), self.t, message=message)
        elif result:
            CFD.show_retained_dialog(
                self._flet(), self.t, records=result,
                message="可逐条删除本机请求、回件及修复记录；不会撤销已交付产物。",
                on_delete=lambda entry: self._task(
                    lambda: self._compile_fix_retention(identity, entry)))
        else:
            message = "本任务没有可删除的修复记录。" if result == [] else "无法核实本任务的修复留存记录。"
            CFD.show_retained_dialog(self._flet(), self.t, message=message)

    def _record_delivery_failure(self, error: str, *, compile_text: str, use_original: bool) -> None:
        self.delivery_ok = False
        self.delivery_compile = compile_text
        self.delivery_error = error
        self._log_delivery(task_id=self.task_id, final="", compile_text=compile_text,
                           ok=False, error=error, use_original=use_original)
        self._mark("bib_ai_deliver_failed", task_id=self.task_id, error=error)

    def _delivered_output(self, payload: dict) -> str:
        if (payload.get("delivered") is not True or payload.get("undone")
                or payload.get("task_id") not in (None, "", self.task_id)):
            return ""
        final = str(payload.get("final") or payload.get("final_dir") or "").strip()
        if self._original_final_dir and final != self._original_final_dir:
            return ""
        return final if os.path.isdir(final) else ""

    async def _finish_delivery(self, payload: dict, *, use_original: bool,
                               identity: tuple | None = None) -> None:
        identity = identity or self._initial_identity()
        task_id = self.task_id
        final = self._delivered_output(payload)
        if (not final and identity[3] and payload.get("delivered") is not False
                and payload.get("task_id") in (None, "", task_id)):
            status = await self._read_status(identity[3])
            if not self._same_initial_task(identity):
                return
            if status and status.get("task_id") == task_id:
                receipt = status.get("delivery") or {}
                final = self._delivered_output(receipt)
                if final:
                    payload = receipt
        compile_text = compile_status_text(payload)
        if not final:
            reason = str(payload.get("error") or self.error or "引擎没有返回最终交付路径或匹配的成功记录")
            self._record_delivery_failure(reason, compile_text=compile_text, use_original=use_original)
            self._fail("完成初排转换", reason)
            return
        self._ok()
        self.delivery_ok = True
        self._operation_receipt = dict(payload)
        self.delivery_compile = compile_text
        self.delivery_error = ""
        self.phase = "applied"
        self.undone = False
        self.backup = str(payload.get("backup") or "")
        self._final_dir = final
        self._clear_comparison()
        self.notice = "初排已交付（未采纳 AI）" if use_original else "初排已交付"
        self._log_delivery(task_id=task_id, final=final, compile_text=compile_text,
                           ok=True, use_original=use_original)
        self._mark("bib_ai_deliver_ok", task_id=task_id, final=final,
                   compile_status=compile_text)
        self._notify(self.notice)

    async def apply(self) -> None:
        """应用到目标 Bib（仅当可预览且无阻断）。"""
        if self._r3 and callable(self._initial_call):
            await self._deliver()
            return
        if not self._require("apply_call", "run_call"):
            self._refresh()
            return
        if self.blocked:
            self._fail("应用", BLOCKED_TITLE)
            self._refresh()
            return
        if not self.can_apply():
            self._fail("应用", "还没有可应用的拟应用结果：先「生成拟应用结果」")
            self._refresh()
            return
        call = self._build_call("apply_call", self._call_kwargs())
        if call is None:
            self._refresh()
            return
        context = self._initial_action_context()
        work = self._work_dir_path()
        self.running = True
        self.error = ""
        self._refresh()
        if self._r3:
            payload = await self._run_r3("apply", call, "写入所选目标")
            if not self._same_initial_task(context[0]):
                return
            if context != self._initial_action_context():
                self.running = False
                self._refresh()
                return
            if payload:
                if _int_of(payload.get("wrote"), -1) > 0:
                    self._ok()
                    self._operation_receipt = dict(payload)
                    self.phase = "applied"
                    self.undone = False
                    self.backup = str(payload.get("backup") or "")
                    self.notice = self._library_completion_title()
                elif payload.get("wrote") == 0:
                    self._ok()
                    self._operation_receipt = dict(payload)
                    self.notice = "本次未写入文件。" + str(payload.get("note") or "")
                else:
                    # 调用成功不等于写入成功；身份不一致时不接纳任何回填字段。
                    status = await self._read_status(work)
                    if not self._same_initial_task(context[0]):
                        return
                    if context != self._initial_action_context():
                        self.running = False
                        self._refresh()
                        return
                    if status and status.get("task_id") != self.task_id:
                        self._clear_task()
                        self._fail("核实写入状态", "工作区中的任务身份已变化，请重新选择任务")
                    elif status and status.get("task_id") and status.get("phase"):
                        self._ok()
                        self._operation_receipt = dict(payload)
                        if not self._receive_status(status):
                            self._refresh()
                            return
                        self.notice = "引擎未报告写入数量，请核对任务详情与结果文件。"
                    else:
                        self._fail("核实写入状态", "未能确认本次写入状态，请刷新任务后核对。")
                if not self.error:
                    self._notify(self.notice)
            self._refresh()
            return
        code = await self._run(call)
        if code is None:
            self._refresh()
            return
        if code != 0:
            self._fail_code("应用", code)
            self._refresh()
            return
        self._ok()
        self.backup = str(getattr(call, "backup", "") or self.backup)
        self.phase = "applied"
        self.undone = False
        self.notice = "已应用到目标 Bib；需要回退就点「撤销本次写入」。"
        self._notify("已应用到目标 Bib")
        self._mark("bib_ai_apply_ok", backup=bool(self.backup))
        self._refresh()

    async def undo(self) -> None:
        """撤销本次写入（只撤销本视图刚做的那一次）。"""
        if not self._require("undo_call", "run_call"):
            self._refresh()
            return
        initial = callable(self._initial_call)
        if initial and self.running:
            return
        if not self.can_undo():
            self._fail("撤销", "本次没有可撤销的写入")
            self._refresh()
            return
        call = self._build_call("undo_call", self._call_kwargs())
        if call is None:
            self._refresh()
            return
        identity = self._initial_identity() if initial else None
        self.running = True
        self._initial_operation = "正在撤销本次交付…"
        self.error = ""
        self._refresh()
        if self._r3:
            payload = await self._run_r3("undo", call, "撤销本次写入")
            if initial and not self._same_initial_task(identity):
                return
            if payload:
                self._ok()
                self.undone = True
                self.delivery_ok = False
                self._initial_open_stage = ""
                self.phase = "stale"
                self._clear_preview()
                self._final_dir = ""
                self.notice = "已撤销本次写入。"
                self._notify(self.notice)
            self._refresh()
            return
        code = await self._run(call)
        if code is None:
            self._refresh()
            return
        if code != 0:
            self._fail_code("撤销", code)
            self._refresh()
            return
        self._ok()
        self.undone = True
        self.phase = "stale"
        self.preview_id = ""
        self.notice = "已撤销本次写入；如要再次应用，请重新生成拟应用结果。"
        self._notify("已撤销本次写入")
        self._refresh()

    async def refresh_status(self) -> None:
        """刷新状态：读引擎报告的阶段/计数/阻断，不猜、不补。"""
        initial = callable(self._initial_call)
        if initial and self.running:
            return
        if self._r3:
            work = self._work_dir_path()
            if not work:
                self._fail("刷新状态", "请先生成任务书")
                _refresh_workspace(self)
                return
            identity = self._initial_identity() if initial else None
            self.running = True
            self._initial_operation = "正在刷新任务状态…"
            _refresh_workspace(self)
            status = await self._read_status(work)
            if initial and not self._same_initial_task(identity):
                return
            repair_context = None
            if status and status.get("phase") and status.get("task_id"):
                expected = str(status["task_id"])
                if self.task_id and self.task_id != expected:
                    self._clear_task()
                    self._fail("刷新状态", "工作区中的任务身份已变化，请重新选择任务")
                else:
                    self.task_id = expected
                    self.work_dir = work
                    if self._receive_status(status):
                        self._ok()
                        self._task(self._prefetch_taskbook)
                        if initial and self.phase == "preview" and self.preview_id:
                            repair_context = self._initial_action_context()
            elif status:
                self._fail("刷新状态", "引擎未返回任务身份与阶段")
            self.running = False
            _refresh_workspace(self)
            if repair_context is not None:
                self._task(lambda: self._restore_compile_fix_status(repair_context, quiet_missing=True))
            return
        if not self._require("status_call", "run_call"):
            self._refresh()
            return
        fn = self._fn("status_call")
        try:
            params = inspect.signature(fn).parameters
            work_dir = self._work_dir_path()
            if "work_dir" in params:
                # 真桥 `ai_status_call(*, work_dir)` 是 **keyword-only**：按「位置参数
                # 个数」判断会数出 0 个 → 裸调 `fn()` → TypeError，状态永远刷不出来。
                call = fn(work_dir=work_dir)
            elif any(p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                inspect.Parameter.POSITIONAL_OR_KEYWORD)
                     for p in params.values()):
                call = fn(work_dir)
            else:
                call = fn()
        except Exception as exc:  # noqa: BLE001
            self._fail("刷新状态", f"{type(exc).__name__}: {exc}")
            self._refresh()
            return
        if call is None:
            self._fail("刷新状态", "引擎桥没有返回调用对象")
            self._refresh()
            return
        self.error = ""
        code = await self._run(call)
        if code is None:
            self._refresh()
            return
        if code != 0:
            self._fail_code("刷新状态", code)
            self._refresh()
            return
        self._ok()
        status = parse_status(self.log)
        if not status:
            self._fail("刷新状态", "引擎输出里没有可识别的状态行")
            self._refresh()
            return
        # 引擎回填 task_id / 工作区 = 恢复一个活动任务：改身份要让在飞的复制作废，
        # 但**不重新 prepare、不发送任何 API 请求**（规格 §3）。
        self.set_task_id(status.get("task_id") or self.task_id)
        if status.get("work_dir"):
            status_task = self.task_id
            self.set_work_dir(status["work_dir"])
            self.task_id = status_task
        if status.get("phase"):
            self.phase = phase_from_engine(status["phase"])
        self.preview_id = status.get("preview_id") or self.preview_id
        self.backup = status.get("backup") or self.backup
        self.key_count = _int_of(status.get("keys"), self.key_count)
        self.entry_count = _int_of(status.get("entries"), self.entry_count)
        # 候选路径以引擎为唯一来源：这次没给就**清空**（不许沿用上一次的旧路径，
        # 否则「对比」按钮会对着一个已经作废的候选打开 diff）。
        self.candidate_bib = str(status.get("candidate_bib") or "")
        self._candidate_bib_sha = str(status.get("candidate_bib_sha256") or "")
        if "blocked" in status:
            self.blocked = status["blocked"]
        if status.get("stale"):
            self.phase = "stale"
        self._refresh()

    async def copy_task_text(self) -> bool:
        """把任务书**全文**写进剪贴板（R3 §10）。

        内容只来自只读桥 `ai_taskbook_call` 的 JSON payload（stdout 解码），
        **绝不**从日志行、文件路径或旧 `self.task_text` 拼。发起前后各核对一次
        请求身份 `(work_dir, task_id, request_seq)`：任一在飞期间变了（重开任务、
        切工作区/任务、快速重复点击），旧异步结果一律丢弃，不复制、不报成功。
        返回 bool 与宿主剪贴板的真实结果一致。
        """
        fn = self._fn("taskbook_call")
        if not callable(fn):
            self._bridge_fail(["taskbook_call"])
            _refresh_workspace(self)
            return False
        self._check_parent_task()
        wd = self._work_dir_path()
        if not wd or (self._r3 and not self.task_id):
            self.error = "复制任务书全文失败：没有已登记的活动任务"
            _refresh_workspace(self)
            return False

        # 每一次点击都是一个新请求：递增序号让上一次尚未落地的复制作废。
        self._bump_request()
        seq = self.request_seq
        tid = str(self.task_id or "")
        dropped: list[str] = []
        try:
            call = _invoke(fn, {"work_dir": wd, "task_id": tid}, dropped)
            if call is None:
                raise RuntimeError("引擎桥没有返回调用对象")
            from services import engine_bridge as EB
            result = await asyncio.to_thread(EB.read_json_command, call)
            fields = EB.taskbook_result_fields(result)
        except Exception as exc:  # noqa: BLE001 - 引擎/剪贴板异常如实失败
            self.error = f"复制任务书全文失败：{type(exc).__name__}: {exc}"
            self._mark("bib_ai_copy_task_failed", op="copy_task", error=self.error)
            _refresh_workspace(self)
            return False

        # 父页未重绘时也要检查它的输入，避免复制旧任务。
        self._check_parent_task()
        if (seq != self.request_seq
                or wd != self._work_dir_path()
                or tid != str(self.task_id or "")):
            # 如实说明「任务已切换，旧结果作废」，不能静默吞掉也不报成功。
            self.error = "复制任务书全文失败：任务/工作区已切换，旧结果作废"
            self._mark("bib_ai_copy_task_stale", op="copy_task", seq=seq,
                       current_seq=self.request_seq)
            _refresh_workspace(self)
            return False

        text = str(fields.get("text") or "")
        if (not fields.get("ok") or not text.strip()
                or (self._r3 and fields.get("task_id") != tid)):
            self.error = "复制任务书全文失败：引擎没有返回可复制的任务书全文"
            self._mark("bib_ai_copy_task_failed", op="copy_task", error=self.error)
            _refresh_workspace(self)
            return False

        if self._r3:
            from services import ai_prefs as AI

            scope = AI.r3_task_scope(text)
            if not scope["known"] or scope["count"] > AI.R3_VERIFY_MAX:
                self._taskbook_cache = fields
                self.error = (scope["warning"] if not scope["known"] else
                              f"本次正文引用任务书预计委派 {scope['count']} 条，"
                              f"超过固定上限 {AI.R3_VERIFY_MAX} 条；未复制。"
                              "任务书仍可查看，请缩小范围后重新生成。")
                self._mark("bib_ai_copy_task_blocked", task_id=tid,
                           count=scope["count"] if scope["known"] else None)
                _refresh_workspace(self)
                return False

        if not self._copy(text, exact=True):
            self.error = "复制任务书全文失败：剪贴板不可用"
            self._mark("bib_ai_copy_task_failed", op="copy_task", error=self.error)
            _refresh_workspace(self)
            return False

        self._taskbook_cache = fields
        self.error = ""
        self._notify("已复制任务书全文")
        self._mark("bib_ai_copy_task", chars=len(text), task_id=tid, seq=seq)
        _refresh_workspace(self)
        return True

    async def _prefetch_taskbook(self) -> None:
        """进入等待阶段/恢复任务后异步取一次内容（不发送 AI、不写文件）。

        只用于把 path/sha256/chars 等元信息缓存进 `self._taskbook_cache`；
        复制仍走 `copy_task_text()` 的实时桥取全文，绝不因缓存而复制过期文本。
        """
        fn = self._fn("taskbook_call")
        if not callable(fn):
            return
        wd = self._work_dir_path()
        if not wd:
            return
        seq, tid = self.request_seq, str(self.task_id or "")
        try:
            from services import engine_bridge as EB
            call = _invoke(fn, {"work_dir": wd, "task_id": tid})
            result = await asyncio.to_thread(EB.read_json_command, call)
            fields = EB.taskbook_result_fields(result)
        except Exception:  # noqa: BLE001 - 预取是便利，失败不影响主流程
            return
        if (seq != self.request_seq
                or wd != self._work_dir_path()
                or tid != str(self.task_id or "")):
            return
        if fields.get("ok") and str(fields.get("text") or "").strip():
            if self._r3 and fields.get("task_id") != tid:
                return
            self._taskbook_cache = fields
            self.taskbook_path = str(fields.get("path") or self.taskbook_path)
            _refresh_workspace(self)

    # ── 布局 ───────────────────────────────────────────────────────────
    def _workspace_width(self) -> float:
        """估算内容宽度，断点须扣掉导航与页面边距。"""
        width = self._build_width or self._design_width()
        return max(240.0, width - (220.0 if width >= 720 else 56.0) - 64.0)

    def _disclosure(self, key: str, title: str, controls: list[ft.Control]) -> ft.Control:
        def changed(e) -> None:
            self._disclosure_open[key] = str(getattr(e, "data", "")).lower() == "true"
        tile = ft.ExpansionTile(
            title=C.text(title, self.t, role="body-strong"),
            expanded=self._disclosure_open.get(key, False),
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.START,
            controls_padding=self.t.sp("1"), dense=True,
            on_change=changed, controls=controls)
        tile._bib_ai_disclosure = key
        return tile

    def _initial_stage(self) -> str:
        """两个生产入口共用的展示阶段；有效候选仍在审阅阶段。"""
        if self.phase == "applied":
            return "done"
        if not self.task_id:
            return "prepare"
        if self._initial_open_stage in ("waiting", "external"):
            return "waiting"
        if (self._has_active_reply or self.imported or self._preview_dirty
                or self.phase in ("preview", "blocked", "stale")):
            return "review"
        return "waiting"

    def _initial_library_summary(self) -> str:
        differences = f"{len(self.conflicts)} 项字段差异"
        if self.library.get("checked"):
            return f"匹配 {self.library['matched']} 条文献 · {differences}"
        if self.conflicts:
            return " · ".join([*library_lines(self.library), differences])
        if not self.library or str(self.library.get("skipped_reason") or "").startswith("尚未查库"):
            return "尚未查库，生成拟应用结果后显示查库结论。"
        return " · ".join(library_lines(self.library))

    def _library_completion_title(self) -> str:
        kinds = {str(item.get("kind") or "")
                 for item in self._operation_receipt.get("writes") or []}
        if kinds == {"text"}:
            return "已更新正文副本"
        if kinds == {"bib"}:
            return "已更新 Bib"
        if kinds == {"bib", "text"}:
            return "已更新 Bib 与正文副本"
        return "本次处理已完成"

    def _result_location(self) -> str:
        if callable(self._initial_call):
            return self._final_dir
        receipt = self._operation_receipt
        target = str(receipt.get("target") or self.bib_out or self.target_bib)
        return os.path.dirname(os.path.abspath(target)) if target else ""

    def _open_result_location(self) -> None:
        if callable(self._initial_call):
            self._open_delivery_dir()
            return
        path = self._result_location()
        opener = getattr(self._app(), "open_path", None)
        if not path or not callable(opener):
            self._fail("打开结果位置", "结果路径或打开路径服务不可用")
        else:
            try:
                opener(path)
            except Exception as exc:  # noqa: BLE001 - 打开失败应可见
                self._fail("打开结果位置", f"{type(exc).__name__}: {exc}")
        self._refresh()

    def _adoption_history(self) -> list[ft.Control]:
        """只读历史，不构造可编辑采纳/裁决/回件控件。"""
        t = self.t
        receipt = self._operation_receipt
        rows: list[ft.Control] = []
        for item in receipt.get("adopted") or []:
            category = next((label for key, label in CATEGORIES
                             if CATEGORY_MAP[key] == item.get("action")), str(item.get("action") or "采用"))
            rows.append(C.text(f"{category} · {item.get('item_id') or '引擎未报告编号'}",
                               t, "body", selectable=True))
        for item in receipt.get("deferred") or []:
            rows.append(C.text(f"待人工确认 · {item.get('item_id') or ''} · {item.get('reason') or ''}",
                               t, "body", selectable=True))
        for item in self.conflicts:
            choice = ("original" if receipt.get("use_original") or not self.accept.get("verify")
                      else decision_for(item, self.decisions))
            label = dict(DECISION_CHOICES).get(choice, choice)
            rows.append(C.text(
                f"{item.get('key') or '（无条目键）'} · {item.get('field') or '（无字段）'} · "
                f"{label}：{item.get(choice) or '（空）'}", t, "body", selectable=True))
        if not rows:
            rows.append(self._caption("引擎未提供逐项采用记录。"))
        return rows

    def _completion_content(self) -> list[ft.Control]:
        t = self.t
        initial = callable(self._initial_call)
        receipt = self._operation_receipt
        rows: list[ft.Control] = []
        if initial:
            if self.delivery_ok:
                rows.append(C.text(os.path.basename(self._final_dir) or self._final_dir,
                                   t, role="body-strong", selectable=True))
                if receipt.get("use_original") is True:
                    rows.append(self._caption("按原稿交付，未采纳 AI 建议。"))
                elif "n_applied" in receipt:
                    rows.append(self._caption(f"实际采纳 {receipt['n_applied']} 项建议"))
                else:
                    rows.append(self._caption("引擎未报告本次采纳数量。"))
                if receipt.get("deferred"):
                    rows.append(C.inline_note(
                        f"另有 {len(receipt['deferred'])} 项保留待人工确认，可展开本次采用详情。", t, "warn"))
                rows.append(self._caption(f"编译：{self.delivery_compile or '引擎未报告'}"))
            else:
                rows.append(C.inline_note("引擎未返回可确认的成功交付。请刷新状态核实。", t, "warn"))
        else:
            target = str(receipt.get("target") or self.bib_out or self.target_bib)
            if target:
                rows.append(C.text(os.path.basename(target), t, role="body-strong", selectable=True))
            wrote = _int_of(receipt.get("wrote"), -1)
            rows.append(self._caption(f"实际写入 {wrote} 个文件" if wrote > 0 else
                                      "引擎已记录应用完成；未报告本次写入数量。"))
            for item in receipt.get("writes") or []:
                kind = {"bib": "Bib 文件", "text": "正文副本"}.get(item.get("kind"), "文件")
                rows.append(C.text(f"{kind} · {item.get('target') or ''}", t, "caption", selectable=True))
        return rows

    def _initial_parts(self) -> list[ft.Control]:
        """共享工作区：任务与阶段 → 必要内容 → 唯一主动作 → 渐进披露。"""
        t = self.t
        initial = callable(self._initial_call)
        stage = self._initial_stage()
        ready = self.can_apply()
        done = self.phase == "applied"
        external = self._initial_open_stage == "external" and self._external_import_draft is not None
        titles = {"prepare": "准备 AI 任务", "waiting": "获取 AI 结果",
                  "review": "审阅拟应用结果" if ready else "选择要采纳的内容",
                  "done": ("初排转换已完成" if self.delivery_ok else "核实交付结果")
                  if initial else self._library_completion_title()}
        if external:
            titles["waiting"] = "选择完整外部 Bib 来源"
        descriptions = {
            "prepare": "确认文件、处理方式和执行范围，再生成任务书。",
            "waiting": ("使用本机配置请求 API，取回结果后再审阅。" if self.channel == "api" else
                        "先把任务书交给 AI，再将完整回复粘贴回来。"),
            "review": ("检查差异与采用值，确认后写入结果。" if ready else
                       "默认保留原稿；生成拟应用结果不会改动目标文件。"),
            "done": "本次处理已经结束，可打开结果或查看采用记录。",
        }
        if external:
            descriptions["waiting"] = "重新选择完整 Bib 文件或粘贴其全部正文；本步不会使用之前隐藏的 AI 回复。"
        labels = (("prepare", "准备任务"), ("waiting", "获取结果"),
                  ("review", "审阅采纳"), ("done", "完成"))
        step_controls = []
        narrow = self._workspace_width() < 520
        for index, (key, label) in enumerate(labels, 1):
            text = C.text(label, t, role="body-strong" if key == stage else "caption",
                          color=t.palette.accent if key == stage else t.palette.text_muted)
            text._initial_stage_heading = True
            step_controls.append(ft.Container(
                # False still becomes Expanded(0) in Flet; Wrap must omit expand.
                expand=None if narrow else True, width=t.px(130) if narrow else None,
                padding=t.sp("1"), border_radius=t.radius("control"),
                bgcolor=t.palette.surface_2 if key == stage else None,
                content=ft.Row(spacing=t.sp("half"), controls=[
                    C.text(str(index), t, "caption", t.palette.text_muted), text])))
        context_path = (self._task_inputs()[0] if initial else self.target_bib) or "未选择文件"
        context = os.path.basename(context_path)
        heading = C.text(titles[stage], t, role="section")
        heading._bib_ai_status = True
        body: list[ft.Control] = [heading, self._caption(descriptions[stage])]
        if self._bridge_missing:
            body.append(C.inline_note(bridge_error_text(self._bridge_missing), t, "error"))
        if self.error or self.delivery_error:
            body.append(C.inline_note(self.error or self.delivery_error, t, "error"))
        if initial and stage == "review":
            self._compile_fix_row = ft.Column(controls=self._compile_fix_controls(), spacing=t.sp("half"))
            body.append(self._compile_fix_row)
        else:
            self._compile_fix_row = None
        if self.running:
            body.append(C.inline_note(self._initial_operation or "正在处理任务…", t, "info"))
        elif self.blocked and not done:
            body.append(C.inline_note("暂不能写入：" + "；".join(self.blocked), t, "warn"))
        elif self._preview_dirty:
            body.append(C.inline_note("已改选，需重新生成拟应用结果后才能交付。", t, "info"))
        elif self.undone:
            body.append(C.inline_note("已撤销本次写入；重新生成拟应用结果后可再次处理。", t, "info"))
        elif self.notice.startswith("本次未写入") or self.notice.startswith("引擎未报告写入"):
            body.append(C.inline_note(self.notice, t, "info"))
        primary = ""
        label = ""
        disabled = False
        secondary: list[ft.Control] = []
        if stage == "prepare":
            body.extend([self._paths(compact=True), self._channel_row(), self._execute_section()])
            primary, label = "prepare", "生成任务书"
        elif stage == "waiting":
            if external:
                body.extend([
                    C.inline_note("完整外部 Bib 将成为合并基础，未出现在其中的旧条目可能省略。", t, "warn"),
                    self._body_paste_row(label="完整 Bib 正文", hint="粘贴完整外部 Bib 的全部内容，不是之前的 AI 子集回复。"),
                    self._disclosure("external_file", "选择完整 Bib 文件（替代粘贴）", [
                        self._result_picker(label="完整外部 Bib 文件")]),
                ])
                primary, label = "import_external", "检查并采用完整 Bib…"
                secondary.append(self._button("cancel_external_import", label="取消，返回当前任务"))
            elif self.channel == "api":
                body.append(C.inline_note("请求可能产生费用；失败后由你手动重试。", t, "warn"))
                primary, label = "request", "手动重试 API" if self.error else "开始 API 核实"
            else:
                body.extend([self._copy_task_button(), self._body_paste_row(),
                             self._disclosure("reply_file", "从文件读取结果（替代粘贴）", [self._result_picker()])])
                primary, label = "import", "读取并检查结果"
            if self._has_active_reply and not external:
                secondary.append(self._button("confirm_stage", label="返回当前结果"))
        elif stage == "review":
            body.extend([self._accept_section(),
                         C.inline_note(self._initial_library_summary(), t,
                                       "info" if self.library.get("checked") else "warn"),
                         *self._conflict_section()])
            if self._review_notes:
                body.append(self._disclosure("review_notes", "各项检查的执行情况", [
                    self._caption(note) for note in self._review_notes]))
            if ready:
                primary, label = "apply", ("完成初排转换" if initial else "并入目标 Bib")
                secondary.append(self._diff_button())
                if self._compare_paths:
                    paths = self._compare_paths
                    copy_btn = C.secondary_button("复制两份文件路径", lambda e: self._copy_compare_paths(paths), t)
                    self._action_buttons["copy_compare"] = copy_btn
                    secondary.append(copy_btn)
                    body.append(self._caption("在 VS Code 中修改对比副本不会改变交付。"))
                    body.append(self._caption(self._compare_feedback))
            else:
                primary = "preview"
                label = "重新生成拟应用结果" if self._preview_dirty or self.error or self.blocked or self.undone else "生成拟应用结果"
        else:
            body.extend(self._completion_content())
            if not initial or self.delivery_ok:
                primary, label = "open_final", "打开交付目录" if initial else "打开结果位置"
                disabled = not os.path.isdir(self._result_location())
        self._primary_slot = (stage, primary)
        self._workspace_action_bar = None
        if primary:
            consequence = ("确认后由引擎写入最终目录；源稿不变。" if initial else
                           "确认后由引擎备份并写入目标文件。") if primary == "apply" else (
                           "仅生成候选，不写入目标文件。" if primary == "preview" else "")
            action_buttons = [
                self._button(primary, primary=True, label=label, disabled=disabled), *secondary]
            uniform_initial_actions = initial and primary == "apply"
            if uniform_initial_actions:
                # Set the actual buttons, not just surrounding boxes; keep all
                # ready-stage borders equal through wrapping and user zoom.
                for button in action_buttons:
                    button.width = t.px(184)
                    button.height = t.px(36)
                    button.style.visual_density = ft.VisualDensity.STANDARD
            actions = ft.Column(spacing=t.sp("half"), controls=[
                ft.Row(wrap=True, spacing=t.sp("1"),
                       run_spacing=t.sp("1") if uniform_initial_actions else None,
                       controls=action_buttons),
                *([self._caption(consequence)] if consequence else []),
            ])
            actions._bib_ai_action_bar = True
            self._workspace_action_bar = actions
            body.append(actions)
        work = ft.Container(
            content=ft.Column(spacing=t.sp("1"), controls=body),
            border=C.box(t), border_radius=t.radius("control"), padding=t.sp("2"))
        work._bib_ai_stage = stage
        self._workspace_body = work.content
        self._stage_boxes = {stage: work}
        result: list[ft.Control] = [
            ft.Row(wrap=narrow, spacing=t.sp("half"), controls=step_controls),
            C.text(f"{context} · {'Chat' if self.channel == 'chat' else 'API'}", t,
                   "caption", t.palette.text_muted, selectable=True), work]
        if done:
            result.append(self._disclosure("adoption_history", "本次采用详情（只读）", self._adoption_history()))
        other = []
        if self.work_dir:
            other.extend([self._button("refresh"), self._button("restart")])
            if initial and self.task_id:
                other.append(self._compile_fix_retention_button())
            if not done:
                if self._has_active_reply and stage != "waiting":
                    other.insert(0, self._button("replace_reply", label="替换 AI 结果…"))
                if initial and self.task_id and self._original_final_dir:
                    other.insert(0, self._button("deliver_original"))
                if stage == "waiting" and not external:
                    other.append(self._button("use_chat" if self.channel == "api" else "use_api",
                        label="改用 Chat 获取结果" if self.channel == "api" else "改用 API 获取结果"))
                if self.purpose == "library" and not external:
                    expected = self._initial_action_context()
                    other.append(C.secondary_button("采用完整外部 Bib…",
                        lambda e: self._begin_external_import(expected), t, disabled=self.running))
        if done and self.can_undo():
            other.append(self._button("undo", label="撤销本次交付…" if initial else "撤销本次写入…"))
            other.append(self._caption("撤销将恢复本次交付前的目录状态。" if initial else
                                       "撤销将把写入的文件恢复到本次处理前。"))
        if other:
            result.append(self._disclosure("other_actions", "其他操作", [ft.Row(wrap=True, controls=other)]))
        technical = []
        for caption, value in (("任务编号", self.task_id), ("任务目录", self.work_dir),
                               ("任务书", self.taskbook_path), ("输入文件", context_path),
                               ("结果位置", self._result_location() if done else ""),
                               ("最终输出目录", self._original_final_dir),
                               ("库文件", self.library.get("path")),
                               ("库 SHA-256", self.library.get("sha256")),
                               ("冻结 Bib", self._frozen_bib_path),
                               ("冻结 SHA-256", self._frozen_bib_sha),
                               ("拟应用结果", self.candidate_bib), ("备份", self.backup),
                               ("引擎阶段", self.phase), ("引用键数", str(self.key_count)),
                               ("文献条目数", str(self.entry_count))):
            if value:
                technical.append(C.text(f"{caption} · {value}", t, "caption", selectable=True))
        if self._compare_paths:
            technical.extend(C.text(path, t, "caption", selectable=True) for path in self._compare_paths)
        if technical:
            result.append(self._disclosure("task_details", "任务详情", technical))
        if self.log:
            result.append(self._disclosure("task_log", "处理日志", [
                C.text("\n".join(self.log), t, "caption", selectable=True)]))
        return result

    def _parts(self, *, compact: bool = False) -> list[ft.Control]:
        """本页所有内容区控件（build 与 body_only 共用，避免两套顺序漂移）。

        顺序按**操作阶段**排（问题 5，方案 B）：用途/通道/横幅/状态常驻，中间四块
        依次是 ① 准备 → ② 等待结果 → ③ 检查采纳 → ④ 完成；每块内「输入 + 紧挨的
        按钮」，非当前阶段折叠（`visible=False`），主操作随阶段切换
        （`primary_slot()` → 全树恰 1 个 FilledButton）。

        `compact=True` 按 UI-REWORK-DESIGN §3/§4 精简内嵌视图：隐藏用途单选
        （用途由宿主构造时的 `purpose` 固定），阶段①只留目标 Bib + 可选关联 TeX，
        阶段②不显示「AI 结果文件」路径（直接粘回复正文）。独立 `build()` 仍给全套，
        保持本视图单测的展示契约。
        """
        self._check_parent_task()
        self._build_width = self._design_width()
        self._sync_bridge()
        # 每次重画都重建输入框：旧列表必须清空，否则「浏览…」会去写已经不在
        # 页面上的旧控件（点了没反应），列表还会无限增长。四个阶段块共用这次清空。
        for key in self._refs:
            self._refs[key] = []
        if self._r3:
            self._action_buttons.clear()
            self._conflict_groups.clear()
            self._execute_boxes.clear()
            self._accept_boxes.clear()
            self._paste_field = None
            return self._initial_parts()
        self._primary_slot = primary_slot(self.phase, self.channel, self.imported)
        if self._r3 and self.phase in ("idle", "preparing"):
            self._primary_slot = ("prepare", "prepare")
        if self._r3 and self.phase == "blocked":
            self._primary_slot = ("review", "preview")
        parts: list[ft.Control] = []
        if not compact:
            parts.append(self._purpose_row())
        parts.extend([
            self._channel_row(),
            *([self._button("restart"), self._button("refresh")]
              if self._r3 and self.work_dir else []),
            *self._banners(),
            self._prepare_block(compact=compact),
            self._waiting_block(compact=compact),
            self._review_block(),
            self._done_block(),
            self._status_block(),
        ])
        return parts

    def _body(self) -> ft.Control:
        t = self.t
        return ft.Column(spacing=t.sp("1"), scroll=C.scrollbar(t), expand=True,
                         controls=self._parts())

    def body_only(self, *, compact: bool = False, pinned_actions: bool = False,
                  previous: ft.Control | None = None) -> ft.Control:
        """无壳内容。生产宿主用有界工作区固定唯一动作条。

        默认不加滚动，兼容原有嵌入。``pinned_actions=True`` 返回 expand Column：
        唯一滚动内容在上、当前动作在下；宿主须给有界高度，并撤掉外层纵向滚动。
        ``compact`` 只控制兼容版路径字段；R3 两入口共享当前阶段与必要输入。
        同一任务及展示阶段的原位刷新可传入 ``previous``，复用原滚动容器。
        """
        parts = self._parts(compact=compact)
        if pinned_actions and self._r3:
            action_bar = self._workspace_action_bar
            if action_bar is not None:
                self._workspace_body.controls.remove(action_bar)
            identity = (self._initial_stage(), self.task_id, self._work_dir_path())
            if getattr(previous, "_bib_ai_workspace_identity", None) == identity:
                workspace = previous
                content = workspace.controls[0]
                content.controls = parts
            else:
                workspace = ft.Column(spacing=0, expand=True)
                workspace._bib_ai_workspace_identity = identity
                content = ft.Column(spacing=self.t.sp("1"), controls=parts,
                                    scroll=C.scrollbar(self.t), expand=True)
                content._bib_ai_workspace_scroll = True
            dock = ([ft.Container(padding=self.t.sp("1"), content=action_bar)]
                    if action_bar is not None else [])
            workspace.controls = [content, *dock]
            return workspace
        return ft.Column(spacing=self.t.sp("1"), controls=parts)

    def build(self) -> ft.Control:
        self._build_width = self._design_width()
        return S.page_shell(
            self.t, title=TITLE,
            meta="写目标 Bib 由引擎执行 · 先备份可撤销 · 不改中央库",
            role_chip=ROLE_CHIP, action=None,
            on_gear=lambda e: self._goto("settings"),
            body=self._body(), dock=self._dock(), dock_height=S.DOCK_H)

    # ── 宿主可选钩子（bib_host 会按需调用）──────────────────────────────
    def activate(self) -> None:
        """切到本页：恢复上次选的路径，但**不自动跑引擎**（AI 调用有成本）。"""
        if self._r3:
            if self.running:
                return
            self._check_parent_task()
            if not callable(self._initial_call):
                fields = ("target_bib",) if self._is_library_page() else ("target_bib", "tex_path")
                for field in fields:
                    if not getattr(self, field):
                        setattr(self, field, str(self._store_get(f"bib_ai.{field}", "") or ""))
                self._enforce_library_boundaries()
            stored = self._store_get(self._task_store_key(), {})
            stored_inputs = stored.get("inputs") if isinstance(stored, dict) else None
            if (self._is_library_page() and isinstance(stored_inputs, (list, tuple))
                    and stored_inputs and stored_inputs[0] == self.target_bib
                    and list(stored_inputs) != self._task_inputs()):
                self._deactivate_legacy_library_task("任务身份与当前引用库范围不一致")
                return
            if (isinstance(stored, dict) and not self.task_id and
                    stored.get("inputs") == self._task_inputs()
                    and stored.get("work_dir") and stored.get("task_id")):
                self.work_dir = str(stored["work_dir"])
                self.task_id = str(stored["task_id"])
                if callable(self._initial_call):
                    self._parent_paths = tuple(self._task_inputs())
                self._task(self.refresh_status)
            return
        restored = 0
        for field in ("source_path", "target_bib", "tex_path", "tex_out",
                      "result_path", "work_dir"):
            if getattr(self, field, ""):
                continue
            saved = self._store_get(f"bib_ai.{field}", "")
            if isinstance(saved, str) and saved.strip():
                setattr(self, field, saved.strip())
                restored += 1
        self._mark("bib_ai_activate", phase=self.phase, restored=restored)

    def refresh_running(self) -> None:
        """宿主重绘时调用：不重启任何引擎任务。"""
        return None

    def header_actions(self) -> list[ft.Control]:
        """页头不抢动作（六个动作都在主体里，避免两个主按钮）。"""
        return []

    def on_resize(self) -> None:
        """宽度跨档时重画一次；同档不动，避免打断输入焦点。"""
        width = self._design_width()
        def band(value):
            content = max(240.0, value - (220.0 if value >= 720 else 56.0) - 64.0)
            return (value >= 720, content < 520, content < 600)
        if band(width) != band(self._build_width):
            self._refresh()
