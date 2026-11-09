"""P03 协同流程的只读展示文案（纯函数；不 import flet、不读写状态、不判文档）。

边界（CONTRACT-P03 §5）：这里只把**引擎已经写下的磁盘事实**（`_AI协同状态.json`
里的路径/分片清单/统计）与**本次运行日志原文**整理成给人看的行；不判断回填是否
有效、不猜身份、不改文件、不跑引擎。阶段判定仍由 `services.ai_prefs.plan_mode`
与状态 `phase` 负责。

所有函数都必须容忍：`stage is None`、旧状态缺键、文件不存在、路径为空——
旧状态/交付后已清理时降级成说明行，绝不抛异常。
"""
from __future__ import annotations

import os

from services import ai_prefs as AI

#: 暂存状态缺失（交付成功后引擎会清理暂存目录；也可能是路径不属于本次暂存）。
MISSING_STATE = "未发现暂存状态（可能已交付并清理，或路径不属于本次暂存）"
#: 旧状态缺分卷/分片键时的降级说明（不猜、不补写）。
#: 旧状态（无 taskbook_version，即 P02 及更早写的 state）缺分卷/分片键时的降级说明。
#: 契约要求同时出现「旧状态未记录」「按单文件处理」两个口径。
OLD_STATE = "旧状态未记录（按单文件处理）：本次为旧状态或未记录分卷/分片信息"
#: 新状态（有 taskbook_version）但本次未启用分卷时的说明——与旧状态必须可区分。
NOSPLIT_NOTE = "本次未启用分卷（按单文件处理）：未生成卷/片文件"
#: 文案纪律：路径与全文是两件事。
COPY_PATH_NOTICE = "复制路径 ≠ 复制全文：面板只给路径，正文请用编辑器打开文件。"
#: 文案纪律：字符数只是体量，不是计费数字。
CHARS_NOTICE = "字符数≈体量，不是模型 token/费用（引擎只按字符估算）。"
#: 文案纪律：未验证的效果必须明说未测。
LIMITS_NOTICE = "未测：模型准确性、回复成功率、费用/提速（本面板不给出这些数字）。"

_VOLUME_LABELS = ("卷A", "卷B", "卷C")
_PHASE_LABELS = {
    "ph1": "第一段（ph1）· 等回填",
    "ph2": "第二段（ph2）· 未完成",
    "done": "已交付（done）",
}
#: 引擎第二段回填/分片相关日志行的前缀（原文展示，不改写）。
_ENGINE_LINE_PREFIXES = (
    "⚠",
    "回填拒收",
    "分片拒收",
    "回填来源",
    "文献字段",
    "低置信项",
    "回填：",
    "回填:",
)
#: 交付成功后引擎打印的两类收尾行（只做原文展示，不解析成身份）。
_DELIVERY_MARKERS = ("完成。产物目录：", "暂存目录已清理：")


def _num(value) -> int:
    try:
        return max(0, int(str(value).strip(), 10))
    except (TypeError, ValueError):
        return 0


def _read_chars(path: str) -> int | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return len(handle.read())
    except OSError:
        return None


def _file_state(path: str) -> str:
    """只读事实：文件/目录/缺失 + 字符数（不判断内容对不对）。"""
    text = str(path or "")
    if not text:
        return "路径缺失"
    if os.path.isdir(text):
        return "目录"
    if not os.path.isfile(text):
        return "不存在"
    chars = _read_chars(text)
    return "存在（字符数读不出）" if chars is None else f"存在（{chars} 字符）"


def _path_state(path: str) -> str:
    text = str(path or "")
    if os.path.isdir(text):
        return "目录"
    if not os.path.isfile(text):
        return "不存在"
    chars = _read_chars(text)
    return "文件（字符数读不出）" if chars is None else f"文件（{chars} 字符）"


def _phase_label(phase: str) -> str:
    return _PHASE_LABELS.get(str(phase or ""), "（未知阶段）")


def identity_lines(stage) -> list[str]:
    """当前阶段与身份：源稿、暂存目录、最终目录、阶段/状态、任务书版本与 TASK-ID。

    注意：state.final_dir 是引擎写下的**计划交付路径**，ph1/ph2 阶段该目录并不写盘；
    只有 phase == "done" 才把它当「已交付目录」裸写，其余阶段必须明确标注计划值。
    """
    if stage is None or not getattr(stage, "found", False):
        return [MISSING_STATE]
    source = stage.source_path
    lines = [f"源稿：{os.path.basename(source) or '（未记录）'}"]
    if source:
        lines.append(f"源稿路径：{source}")
    else:
        lines.append("源稿路径：（旧状态未记录）")
    lines.append(f"暂存目录：{stage.stage_dir}")
    final_dir = stage.final_dir
    if str(stage.phase or "") == "done":
        lines.append(f"最终目录：{final_dir or '（未记录）'}")
    elif final_dir:
        lines.append(f"最终目录（计划；交付前不写）：{final_dir}")
    else:
        lines.append("最终目录：（未记录）")
    lines.append(f"阶段：{_phase_label(stage.phase)} · 状态：{stage.status_text}")
    version = stage.taskbook_version
    version_text = f"v{version}" if version is not None else "未记录（旧任务书）"
    task_id = stage.taskbook_task_id or "未记录（旧任务书）"
    lines.append(f"任务书：版本 {version_text} · TASK-ID {task_id}")
    return lines


def taskbook_lines(stage) -> list[str]:
    """任务书形态：单文件默认 / 三卷（路径+存在+字符数）/ 分片清单（k/n、第 a–b 条）。"""
    if stage is None or not getattr(stage, "found", False):
        return [MISSING_STATE]
    taskbook = stage.taskbook
    if taskbook:
        lines = [f"任务书文件：{taskbook} · {_file_state(taskbook)}"]
    else:
        lines = ["任务书文件：（未记录）"]
    volumes = stage.volumes
    slices = stage.slices
    if volumes:
        lines.append("任务书：分三卷（卷A/B/C 单文件）")
        for index, path in enumerate(volumes):
            label = _VOLUME_LABELS[index] if index < len(_VOLUME_LABELS) else f"卷{index + 1}"
            lines.append(f"· {label}：{_file_state(path)} · {path}")
        lines.append(CHARS_NOTICE)
    elif slices:
        lines.append("任务书：单文件（未分三卷）")
    else:
        lines.append("任务书：单文件（三卷合订），默认")
        if stage.taskbook_version is None:
            lines.append(OLD_STATE)
        else:
            lines.append(NOSPLIT_NOTE)
    if slices:
        total = max(_num(item.get("total")) for item in slices)
        first = slices[0]
        size_hint = ""
        first_index, last_index = _num(first.get("first")), _num(first.get("last"))
        if first_index > 0 and last_index >= first_index:
            size_hint = f"，首片 {first_index}–{last_index} 条"
        lines.append(f"分片：共 {total or len(slices)} 片（卷A 按文献条目数切分{size_hint}）")
        for item in slices:
            index = _num(item.get("index"))
            item_total = _num(item.get("total")) or total or len(slices)
            begin, end = _num(item.get("first")), _num(item.get("last"))
            keys = item.get("keys")
            count = len(keys) if isinstance(keys, list) else max(0, end - begin + 1)
            range_text = f"第 {begin}–{end} 条" if begin and end else "条数未记录"
            path = str(item.get("path") or "")
            lines.append(
                f"· 分片 {index}/{item_total}：{range_text}（共 {count} 条）"
                f" · {_path_state(path)} · {path or '（路径未记录）'}"
            )
    return lines


#: 契约 §2 的 stats 字段（缺一就回退到 AI.stats_summary 的旧口径文案）。
_STATS_FIELDS = ("bib_total", "bib_replied", "bib_pending", "low_total",
                 "low_replied", "low_applied", "low_pending_manual")


def _int_stat(stats: dict, key: str) -> int | None:
    """容错取整数字段；缺失/非法/布尔返回 None（由调用方回退，不静默当 0）。"""
    value = stats.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit():
            return int(text, 10)
    return None


def _contract_stats_line(stats: dict) -> str:
    """CONTRACT §2 的回填统计行；字段不全/非法返回空串，交给旧口径回退。"""
    values = {key: _int_stat(stats, key) for key in _STATS_FIELDS}
    if any(value is None for value in values.values()):
        return ""
    return (
        f"文献回填 {values['bib_replied']}/{values['bib_total']}"
        f"（未回填 {values['bib_pending']}）；"
        f"低置信回复 {values['low_replied']}"
        f"（可应用 {values['low_applied']} / 仅记录待人工 {values['low_pending_manual']}）"
    )


def _engine_reply_lines(run_lines) -> list[str]:
    """只挑引擎第二段回填/分片相关行，原文返回（不改写、不排序、不去重语义）。"""
    picked: list[str] = []
    for raw in run_lines or []:
        line = str(raw or "")
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(_ENGINE_LINE_PREFIXES):
            picked.append(line.rstrip("\n"))
    return picked


def reply_check_lines(stage, reply_paths, run_lines) -> list[str]:
    """回填检查：已选路径状态 + 引擎本次告警原文 + 统计口径（未回填 ≠ 0）。"""
    paths = AI.reply_paths(reply_paths or [])
    lines: list[str] = []
    if not paths:
        lines.append("已选回填文件：无（未选回填不会启动第二段）")
    else:
        lines.append(
            f"已选回填文件：{len(paths)} 个（只透传路径；面板不读回复正文判断有效性）"
        )
        for index, path in enumerate(paths, 1):
            lines.append(f"· 回填 {index}：{_path_state(path)} · {path}")
    if stage is not None and getattr(stage, "found", False) and stage.replied:
        lines.append(f"状态记录已收到回填：{len(stage.replied)} 个文献键")
    engine_lines = _engine_reply_lines(run_lines)
    if engine_lines:
        lines.append("引擎本次告警/回填摘要（原文，未改写）：")
        lines.extend(engine_lines)
    else:
        lines.append("引擎本次告警：暂无（本次运行未产生回填相关日志）")
    if stage is not None and getattr(stage, "found", False) and stage.stats:
        contract_line = _contract_stats_line(stage.stats)
        # 契约格式优先；stats 字段缺失/非法时回退 AI.stats_summary 的旧口径文案
        # （P02 API 语义不变，只在组合层补契约行）。
        lines.append("回填统计：" + (contract_line or AI.stats_summary(stage.state)["text"]))
        if stage.pending:
            lines.append(
                f"状态记录待回填（文献键）：{len(stage.pending)} 个——缺片/串稿请按日志原文修正。"
            )
    else:
        lines.append(
            "回填统计：本段尚未运行或旧状态未记录 stats——统计不可用"
            "（不可用 ≠ 0 条；以引擎日志与状态文件为准）。"
        )
    return lines


def next_step_lines(stage, mode: str, reply_paths) -> list[str]:
    """下一步：按「本次运行去向 + 状态 phase」给唯一明确动作，不新开自动流程。"""
    paths = AI.reply_paths(reply_paths or [])
    mode = str(mode or "")
    if mode == AI.MODE_DIRECT:
        return ["本次运行：AI 直连核实（不生成任务书，也不进入两段式）。"]
    if mode == AI.MODE_PLAIN:
        return ["本次运行：普通初排（不碰 AI）；要 AI 核实请勾选「AI 协同初排（两段式）」。"]
    found = stage is not None and getattr(stage, "found", False)
    phase = stage.phase if found else ""
    if phase == "done":
        return [
            f"下一步：已交付到 {stage.final_dir or '（交付目录见本次日志）'}。",
            "业务成品（题名/版式/页数/公式）请打开 PDF 人工核对；暂存目录交付后已清理。",
        ]
    if not found:
        return [
            MISSING_STATE,
            "下一步：先运行第一段（生成任务书）；已交付并清理的暂存不会重新出现。",
        ]
    if mode == AI.MODE_PH1 or not paths:
        lines = [
            "下一步（第一段已出任务书）：把任务书交给 chat 模型核实 → 把回复保存成文件 → "
            "勾选至少一个回填文件后再运行第二段（未选回填不会启动第二段）。",
        ]
        if stage.slices:
            lines.append(
                "分片任务书请整片回传（片号/TASK-ID 与片头一致）；缺片/串稿由引擎在第二段日志"
                "原文告警。"
            )
        if phase == "ph2":
            lines.append(
                "注意：暂存目录里已有「第二段未完成」的状态；直接选回填文件重跑第二段即可，"
                "重跑第一段会重新出任务书（旧回填可能与新任务书不匹配）。"
            )
        return lines
    return [
        "下一步（第二段）：导入回填并交付到最终目录。",
        "若引擎告警缺片/串稿，修正回填后重跑第二段（最终目录未写时暂存目录与备份保留）。",
    ]


def adoption_lines(stage=None) -> list[str]:
    """采用范围三分开 + 防止把「不勾 apply」误解成「reference.bib 零变化」。"""
    return [
        "① 文献核实结果（默认安全并回）：并回 reference.bib，改前备份 reference.bib.ai前；"
        "勾选「不并回文献（--no-ai-merge）」= 只记录，不改 reference.bib。",
        "② 低置信 BIB/META/FN 建议：只有勾选「应用低置信建议（--coop-apply）」才写进产物"
        "（改前自动备份）；不勾 = 只写建议清单；这一项受①的 --no-ai-merge 覆盖。",
        "③ 附四（report-only）：始终只写报告/建议清单，不修改正文与文献库。",
        "注意：未勾 --coop-apply ≠ 整个 reference.bib 零变化——文献核实结果是否并回由①控制。",
    ]


def missing_stage_lines(run_lines) -> list[str]:
    """暂存状态缺失时的说明（交付后清理/路径不对），尾部原文展示交付收尾日志。"""
    lines = [MISSING_STATE]
    picked = [
        str(raw).rstrip("\n")
        for raw in (run_lines or [])
        if any(marker in str(raw or "") for marker in _DELIVERY_MARKERS)
    ]
    if picked:
        lines.append("本次运行日志（原文，未解析身份）：")
        lines.extend(picked)
    return lines
