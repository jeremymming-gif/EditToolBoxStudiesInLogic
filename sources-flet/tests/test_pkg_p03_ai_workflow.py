"""P03 包测试：协同工作流展示 / 三卷与条目分片接线（全程离线，零真实 API）。

覆盖 CONTRACT-P03 §3–§6：
- `parse_slice_size` 正反例（非法值必须报错，不能静默变「不分片」）；
- `coop_ph1_call` argv：不传新参时与旧版逐字节相同；split / slice N / 组合；
- `CoopStage` 旧状态（缺键）容忍读取；
- `ai_workflow_summary` 纯展示：三卷/分片/旧状态/告警原文透传/采用范围/空回复；
- 面板：分三卷与分片条数默认值、非法分片就地提示且不启动不横扫日志、
  只作用于第一段、状态区接入与交付后 state 消失的降级。

边界：不启动引擎、不联网、不读写真实用户设置（P2_SETTINGS 指到 tmp_path）。
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import flet as ft
import pytest

from services import ai_prefs as AI
from services import engine_bridge as EB
from services import settings_store as SS
from ui import ai_panel as AIP
from ui import ai_workflow_summary as W
from ui.tokens import Tokens

SLICE_ERROR = "分片条数要填正整数（留空=不分片）"


# ── 测试替身（不碰真窗口，只让面板能取值） ──────────────────────────────
class FakeStore:
    def __init__(self) -> None:
        self.data = {"ai": AI.defaults(), "options": {}, "recent": {}, "paths": {}}
        self.saved = False

    def get(self, dotted, default=None):
        node = self.data
        for part in str(dotted).split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted, value) -> None:
        parts = str(dotted).split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def save(self) -> None:
        self.saved = True


class FakeApp:
    def __init__(self, store) -> None:
        self.store = store
        self.toasts: list[str] = []
        self.opened: list[str] = []

    def toast(self, message) -> None:
        self.toasts.append(str(message))

    def open_path(self, value) -> None:
        self.opened.append(str(value))

    def browse_field(self, *args, **kwargs):
        return None

    def mark(self, *args, **kwargs) -> None:
        return None

    def persist(self) -> None:
        return None

    def rebuild_shell(self) -> None:
        return None

    def goto(self, key) -> None:
        return None


class FakePage:
    def __init__(self, store) -> None:
        self.app = FakeApp(store)
        self.t = Tokens()
        self.fields = {"single_source": "", "single_out": ""}

    def field(self, key):
        return self.fields.get(key, "")


class _Field:
    def __init__(self, value: str = "") -> None:
        self.value = value


def _args(call) -> list[str]:
    for index, item in enumerate(call.argv):
        if str(item).endswith(call.entry):
            return [str(value) for value in call.argv[index + 1:]]
    return [str(value) for value in call.argv]


def _flag_value(args: list[str], flag: str):
    return args[args.index(flag) + 1] if flag in args else None


def _walk(control):
    yield control
    for name in ("controls", "content"):
        child = getattr(control, name, None)
        if isinstance(child, list):
            for item in child:
                yield from _walk(item)
        elif isinstance(child, ft.Control):
            yield from _walk(child)


@pytest.fixture
def store(tmp_path, monkeypatch) -> SS.SettingsStore:
    monkeypatch.setenv("P2_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("SLCN_STARTUP_LOG", str(tmp_path / "startup.log"))
    instance = SS.SettingsStore()
    instance.load()
    return instance


@pytest.fixture
def enabled(store) -> SS.SettingsStore:
    AI.save(store, {"enabled": True})
    return store


# ── 状态/文本造数 ─────────────────────────────────────────────────────
def _write_state(stage_dir: Path, state: dict) -> None:
    (stage_dir / AI.STATE_NAME).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _base_state(**extra) -> dict:
    state = {
        "schema": 1,
        "phase": "ph1",
        "params": {"stem": "Zhang", "out_root": ""},
        "source": {"path": r"C:\m\main.tex", "sha256": "abc"},
        "taskbook": "",
        "keys": {"bib": [], "low_confidence": [], "all": []},
        "low_confidence": [],
        "replied": [],
        "pending": [],
    }
    state.update(extra)
    return state


def _stage(tmp_path: Path, state: dict | None = None, *, name: str = "Zhang_待核") -> AI.CoopStage:
    stage_dir = tmp_path / name
    stage_dir.mkdir(parents=True, exist_ok=True)
    if state is not None:
        _write_state(stage_dir, state)
    return AI.CoopStage(str(stage_dir))


def _joined(lines) -> str:
    return "\n".join(str(line) for line in lines)


# ── ① parse_slice_size：空/0/正整数 → int；其他 → ValueError ─────────────
@pytest.mark.parametrize("raw,expected", [
    ("", 0), ("   ", 0), ("0", 0), ("00", 0),
    ("1", 1), ("3", 3), (" 12 ", 12),
])
def test_parse_slice_size_accepts_empty_zero_and_positive(raw, expected):
    assert AI.parse_slice_size(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "-0", "1.5", "+2", "1e3", "0x10", "abc", "１", "１２"])
def test_parse_slice_size_rejects_non_positive_integer_text(raw):
    with pytest.raises(ValueError) as info:
        AI.parse_slice_size(raw)
    assert str(info.value) == SLICE_ERROR


# ── ② coop_ph1_call argv：默认逐字节不变 + split / slice ────────────────
def test_ph1_signature_has_optional_split_and_slice():
    params = inspect.signature(EB.coop_ph1_call).parameters
    assert params["split"].default is False
    assert params["slice_size"].default == 0


def test_ph2_signature_has_no_split_or_slice():
    params = inspect.signature(EB.coop_ph2_call).parameters
    assert "split" not in params
    assert "slice_size" not in params


def test_ph1_default_argv_byte_identical_to_legacy():
    legacy = EB.convert_call(source="s.tex", out="out", verify_refs=True)
    expected = list(legacy.argv) + ["--coop-taskbook-only", "--no-ai-merge"]
    call = EB.coop_ph1_call(source="s.tex", out="out")
    assert call.argv == expected
    # 显式传默认值同样不追加任何新 flag
    same = EB.coop_ph1_call(source="s.tex", out="out", split=False, slice_size=0)
    assert same.argv == expected


def test_ph1_split_appends_only_coop_split():
    args = _args(EB.coop_ph1_call(source="s.tex", out="out", split=True))
    assert args[-1] == "--coop-split"
    assert "--coop-slice" not in args
    assert args.count("--coop-split") == 1


def test_ph1_slice_size_appends_coop_slice_n():
    args = _args(EB.coop_ph1_call(source="s.tex", out="out", slice_size=3))
    assert "--coop-split" not in args
    assert _flag_value(args, "--coop-slice") == "3"
    assert args[args.index("--coop-slice") + 1] == "3"


def test_ph1_split_and_slice_combo_order():
    args = _args(EB.coop_ph1_call(source="s.tex", out="out", split=True, slice_size=7))
    assert args.index("--coop-split") < args.index("--coop-slice")
    assert _flag_value(args, "--coop-slice") == "7"


@pytest.mark.parametrize("value", [-1, -5, 0])
def test_ph1_non_positive_slice_size_is_not_passed(value):
    # 负值由 GUI 在启动引擎前拦下；bridge 只把 >0 透传成 --coop-slice。
    args = _args(EB.coop_ph1_call(source="s.tex", out="out", slice_size=value))
    assert "--coop-slice" not in args


def test_ph2_argv_never_contains_split_or_slice():
    args = _args(EB.coop_ph2_call(source="s.tex", out="out", replies=["a.md"],
                                  apply=True, no_ai_merge=True))
    assert "--coop-replies" in args
    assert "--coop-split" not in args and "--coop-slice" not in args


# ── ③ CoopStage 旧状态（缺键）降级：空值、不抛 ─────────────────────────
def test_coop_stage_old_state_properties_are_tolerant(tmp_path):
    stage = _stage(tmp_path, _base_state())
    assert stage.found is True
    assert stage.volumes == []
    assert stage.slices == []
    assert stage.stats is None
    assert stage.replied == [] and stage.pending == []
    assert stage.delivery is None
    assert stage.taskbook_version is None
    assert stage.taskbook_task_id == ""
    assert stage.source_path == r"C:\m\main.tex"
    assert stage.fingerprint == ""


def test_coop_stage_reads_new_state_properties(tmp_path):
    stage_dir = tmp_path / "Zhang_待核"
    stage_dir.mkdir()
    volume = write_text(stage_dir / "volA.md", "abc")
    state = _base_state(
        params={"stem": "Zhang", "out_root": str(tmp_path)},
        taskbook_volumes=[str(volume)],
        taskbook_version=3,
        taskbook_task_id="T-2026-001",
        fingerprint="fp1",
        replied=["k1"],
        pending=["k2"],
        stats={"bib_total": 2, "bib_replied": 1, "bib_pending": 1,
               "low_total": 1, "low_replied": 1, "low_applied": 0,
               "low_pending_manual": 1},
        delivery={"schema": 1, "bibdb_checked": True, "bibdb_safe": True,
                  "bibdb_missed": 0},
    )
    _write_state(stage_dir, state)
    stage = AI.CoopStage(str(stage_dir))
    assert stage.volumes == [str(volume)]
    assert stage.taskbook_version == 3
    assert stage.taskbook_task_id == "T-2026-001"
    assert stage.fingerprint == "fp1"
    assert stage.replied == ["k1"] and stage.pending == ["k2"]
    assert stage.delivery["bibdb_checked"] is True
    assert stage.stats["bib_pending"] == 1
    assert stage.out_dir == str(tmp_path)


def test_coop_stage_missing_state_is_not_found_and_tolerant(tmp_path):
    stage = AI.CoopStage(str(tmp_path / "nope_待核"))
    assert stage.found is False
    assert stage.volumes == [] and stage.slices == [] and stage.stats is None
    assert stage.taskbook_version is None and stage.taskbook_task_id == ""


def test_coop_stage_mis_typed_values_do_not_raise(tmp_path):
    stage = _stage(tmp_path, _base_state(
        taskbook_volumes="not-a-list",
        taskbook_slices={"bad": True},
        taskbook_version=True,
        taskbook_task_id=123,
        fingerprint=456,
        replied="x",
        pending=None,
        stats="nope",
        delivery=["nope"],
    ))
    assert stage.volumes == [] and stage.slices == []
    assert stage.taskbook_version is None
    assert stage.taskbook_task_id == "123"
    assert stage.fingerprint == "456"
    assert stage.replied == [] and stage.pending == []
    assert stage.stats is None and stage.delivery is None


def write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ── ④ ai_workflow_summary：身份 ────────────────────────────────────────
def test_identity_lines_old_state_marks_taskbook_unrecorded(tmp_path):
    stage = _stage(tmp_path, _base_state())
    text = _joined(W.identity_lines(stage))
    assert "源稿：" in text and "main.tex" in text
    assert stage.stage_dir in text
    assert "版本 未记录（旧任务书）" in text
    assert "TASK-ID 未记录（旧任务书）" in text
    # R02：ph1 阶段最终目录只是计划值，不能被读成「已交付/已写盘」。
    assert "最终目录：（未记录）" in text
    assert "最终目录：" not in text.replace("最终目录：（未记录）", "")


def test_identity_lines_new_state_has_version_and_task_id(tmp_path):
    final_dir = str(tmp_path / "最终交付")
    stage = _stage(tmp_path, _base_state(taskbook_version=2,
                                        taskbook_task_id="T-2026-007",
                                        phase="done", final_dir=final_dir))
    text = _joined(W.identity_lines(stage))
    assert "版本 v2" in text
    assert "TASK-ID T-2026-007" in text
    assert "已交付（done）" in text
    # done 才是已交付目录：裸写且不带「计划」标注。
    assert f"最终目录：{final_dir}" in text
    assert "交付前不写" not in text


@pytest.mark.parametrize("phase", ["ph1", "ph2", "weird"])
def test_identity_lines_non_done_final_dir_marked_as_plan(tmp_path, phase):
    # R02：ph1/ph2/未知阶段下 state.final_dir 只是计划值（引擎第一/二段都不写该目录），
    # 面板不得裸写成已交付目录；必须带「计划；交付前不写」标注。
    final_dir = str(tmp_path / "计划交付")
    stage = _stage(tmp_path, _base_state(phase=phase, final_dir=final_dir))
    text = _joined(W.identity_lines(stage))
    assert f"最终目录（计划；交付前不写）：{final_dir}" in text
    assert "最终目录：" not in text
    assert "交付前不写" in text


def test_identity_lines_missing_stage_explains_cleanup():
    assert W.identity_lines(None) == [W.MISSING_STATE]


# ── ⑤ 任务书形态：三卷 / 分片 / 旧状态 ─────────────────────────────────
def test_taskbook_lines_three_volumes_with_chars(tmp_path):
    stage_dir = tmp_path / "Zhang_待核"
    stage_dir.mkdir()
    v1 = write_text(stage_dir / "Zhang_文献核实_卷A.md", "abc")          # 3 字符
    v2 = write_text(stage_dir / "Zhang_文献核实_卷B.md", "defgh")          # 5 字符
    missing = stage_dir / "Zhang_文献核实_卷C.md"
    _write_state(stage_dir, _base_state(
        taskbook=str(write_text(stage_dir / "任务书.md", "tb")),
        taskbook_volumes=[str(v1), str(v2), str(missing)],
        taskbook_version=3, taskbook_task_id="T-1",
    ))
    lines = W.taskbook_lines(AI.CoopStage(str(stage_dir)))
    text = _joined(lines)
    assert "任务书：分三卷（卷A/B/C 单文件）" in text
    assert f"卷A：存在（3 字符） · {v1}" in text
    assert f"卷B：存在（5 字符） · {v2}" in text
    assert "卷C：不存在" in text
    assert W.CHARS_NOTICE in text


def test_taskbook_lines_slices_show_range_total_and_missing_file(tmp_path):
    stage_dir = tmp_path / "Zhang_待核"
    stage_dir.mkdir()
    s1 = write_text(stage_dir / "Zhang_文献核实_分片1-2.md", "0123456789")  # 10 字符
    missing = stage_dir / "Zhang_文献核实_分片2-2.md"
    slices = [
        {"index": 1, "total": 2, "volume": "A", "path": str(s1),
         "first": 1, "last": 5, "keys": ["a", "b", "c", "d", "e"]},
        {"index": 2, "total": 2, "volume": "A", "path": str(missing),
         "first": 6, "last": 9, "keys": ["f", "g", "h", "i"]},
    ]
    _write_state(stage_dir, _base_state(
        taskbook=str(write_text(stage_dir / "任务书.md", "tb")),
        taskbook_slices=slices, taskbook_version=3, taskbook_task_id="T-2",
    ))
    lines = W.taskbook_lines(AI.CoopStage(str(stage_dir)))
    text = _joined(lines)
    assert "任务书：单文件（未分三卷）" in text
    assert "分片：共 2 片" in text
    assert "首片 1–5 条" in text
    assert f"· 分片 1/2：第 1–5 条（共 5 条） · 文件（10 字符） · {s1}" in text
    assert "· 分片 2/2：第 6–9 条（共 4 条） · 不存在" in text


def test_taskbook_lines_old_state_degrades_to_single_file(tmp_path):
    # 旧 state：没有 taskbook_version → 注记必须同时含「旧状态未记录」「按单文件处理」。
    stage = _stage(tmp_path, _base_state())
    assert stage.taskbook_version is None
    text = _joined(W.taskbook_lines(stage))
    assert "单文件（三卷合订），默认" in text
    assert W.OLD_STATE in text
    assert "旧状态未记录" in text
    assert "按单文件处理" in text


def test_taskbook_lines_new_default_state_is_not_marked_old(tmp_path):
    # 新 state（有 taskbook_version）但本次未启用分卷：用另一句说明，不得冒充旧状态。
    stage = _stage(tmp_path, _base_state(taskbook_version=3, taskbook_task_id="T-9"))
    assert stage.taskbook_version == 3
    text = _joined(W.taskbook_lines(stage))
    assert "单文件（三卷合订），默认" in text
    assert W.NOSPLIT_NOTE in text
    assert W.OLD_STATE not in text
    assert "旧状态未记录" not in text


# ── ⑥ 回填检查：路径状态 / 告警原文透传 / stats 口径 ───────────────────
def test_reply_check_without_replies_says_ph2_will_not_start():
    lines = W.reply_check_lines(None, [], [])
    assert any("未选回填不会启动第二段" in line for line in lines)


def test_reply_check_paths_and_raw_engine_warnings(tmp_path):
    file1 = write_text(tmp_path / "reply1.md", "abc")
    missing = tmp_path / "reply2.md"
    run_lines = [
        "   回填来源：1 个文本 / 1 个路径",
        "   ⚠ 分片：第 2/2 片未收到任何回填",
        "   ⚠ 回填拒收：bad.md（缺 TASK-ID）",
        "   ⚠ 分片拒收：bad2.md（片号冲突）",
        "   与回填无关的日志行",
    ]
    lines = W.reply_check_lines(None, [str(file1), str(missing)], run_lines)
    text = _joined(lines)
    assert "已选回填文件：2 个" in text
    assert f"· 回填 1：文件（3 字符） · {file1}" in text
    assert f"· 回填 2：不存在 · {missing}" in text
    # 引擎告警原样透传（不去改写、不隐藏）
    assert "   ⚠ 分片：第 2/2 片未收到任何回填" in text
    assert "   ⚠ 回填拒收：bad.md（缺 TASK-ID）" in text
    assert "   ⚠ 分片拒收：bad2.md（片号冲突）" in text
    assert "   回填来源：1 个文本 / 1 个路径" in text
    assert "与回填无关的日志行" not in text


def test_reply_check_stats_present_uses_engine_stats_text(tmp_path):
    state = _base_state(
        phase="ph2", replied=["k1"], pending=["k2"],
        stats={"bib_total": 2, "bib_replied": 1, "bib_pending": 1,
               "low_total": 1, "low_replied": 1, "low_applied": 0,
               "low_pending_manual": 1},
    )
    stage = _stage(tmp_path, state)
    lines = W.reply_check_lines(stage, [], [])
    text = _joined(lines)
    # CONTRACT §2 的契约格式行（数值直接取自 stage.stats）。
    assert ("回填统计：文献回填 1/2（未回填 1）；低置信回复 1"
            "（可应用 0 / 仅记录待人工 1）") in text
    # P02 旧口径文案不再作为主行出现；面板不读回复正文做判断（这里本来也没有日志）。
    assert "状态记录已收到回填：1 个文献键" in text
    assert "状态记录待回填（文献键）：1 个" in text


def test_reply_check_stats_missing_fields_fall_back_to_old_summary(tmp_path):
    # stats 字段缺失/非法时不硬造契约行，回退 AI.stats_summary 的旧口径文案。
    state = _base_state(
        phase="ph2",
        stats={"bib_total": 2, "bib_replied": 1,
               "low_applied": "bad", "low_pending_manual": None},
    )
    stage = _stage(tmp_path, state)
    text = _joined(W.reply_check_lines(stage, [], []))
    assert "回填统计：" + AI.stats_summary(stage.state)["text"] in text
    assert "未回填" not in text.replace(AI.stats_summary(stage.state)["text"], "")
    # 字符串数字是容错可接受的合法写法。
    tolerant = _base_state(
        phase="ph2",
        stats={"bib_total": "7", "bib_replied": "6", "bib_pending": "1",
               "low_total": 1, "low_replied": 1, "low_applied": 0,
               "low_pending_manual": 1},
    )
    stage2 = _stage(tmp_path, tolerant, name="tolerant_待核")
    text2 = _joined(W.reply_check_lines(stage2, [], []))
    assert ("回填统计：文献回填 6/7（未回填 1）；低置信回复 1"
            "（可应用 0 / 仅记录待人工 1）") in text2


def test_reply_check_stats_absent_says_unavailable_not_zero(tmp_path):
    stage = _stage(tmp_path, _base_state())
    text = _joined(W.reply_check_lines(stage, [], []))
    assert "统计不可用" in text
    assert "不可用 ≠ 0 条" in text


# ── ⑦ 下一步 / 采用范围 / 交付后降级 ───────────────────────────────────
def test_next_step_ph1_waits_for_replies(tmp_path):
    stage = _stage(tmp_path, _base_state())
    text = _joined(W.next_step_lines(stage, AI.MODE_PH1, []))
    assert "把任务书交给 chat 模型核实" in text
    assert "未选回填不会启动第二段" in text


def test_next_step_sliced_taskbook_asks_for_whole_slices(tmp_path):
    stage = _stage(tmp_path, _base_state(
        taskbook_slices=[{"index": 1, "total": 1, "volume": "A", "path": "",
                          "first": 1, "last": 2, "keys": ["a", "b"]}]))
    text = _joined(W.next_step_lines(stage, AI.MODE_PH1, []))
    assert "整片回传" in text


def test_next_step_ph2_with_replies_points_to_second_stage(tmp_path):
    stage = _stage(tmp_path, _base_state(phase="ph2"))
    text = _joined(W.next_step_lines(stage, AI.MODE_PH2, [str(tmp_path / "r.md")]))
    assert "下一步（第二段）：导入回填并交付到最终目录" in text
    assert "重跑第二段" in text


def test_next_step_done_and_missing_stage(tmp_path):
    stage = _stage(tmp_path, _base_state(phase="done", final_dir=str(tmp_path / "final")))
    text = _joined(W.next_step_lines(stage, AI.MODE_PH2, []))
    assert "已交付到" in text and "人工核对" in text

    text = _joined(W.next_step_lines(None, AI.MODE_PH1, []))
    assert W.MISSING_STATE in text
    assert "先运行第一段" in text


def test_next_step_plain_and_direct_are_explained():
    assert "普通初排（不碰 AI）" in _joined(W.next_step_lines(None, AI.MODE_PLAIN, []))
    assert "AI 直连核实" in _joined(W.next_step_lines(None, AI.MODE_DIRECT, []))


def test_adoption_lines_keep_three_scopes_separated():
    text = _joined(W.adoption_lines(None))
    assert "① 文献核实结果" in text
    assert "② 低置信 BIB/META/FN" in text
    assert "③ 附四（report-only）" in text
    assert "--coop-apply" in text
    assert "受①的 --no-ai-merge 覆盖" in text
    assert "未勾 --coop-apply ≠ 整个 reference.bib 零变化" in text


def test_missing_stage_lines_keep_delivery_log_verbatim():
    run_lines = [
        "   回填：1 条（未回填 0 条）",
        "完成。产物目录：/tmp/out",
        "  暂存目录已清理：/tmp/out/Zhang_待核",
    ]
    lines = W.missing_stage_lines(run_lines)
    assert lines[0] == W.MISSING_STATE
    text = _joined(lines)
    assert "完成。产物目录：/tmp/out" in text
    assert "暂存目录已清理：/tmp/out/Zhang_待核" in text
    assert "回填：1 条" not in text   # 不把普通日志当身份/交付行


# ── ⑧ 面板：默认值 / 非法分片拦截 / 只作用于第一段 / 状态区接入 ────────
def test_panel_new_controls_default_off_and_empty(enabled):
    panel = AIP.AiPanel(FakePage(enabled))
    view = panel.build()
    assert panel._split is False
    assert panel._slice_text == ""
    assert panel._split_chk.value is False
    assert panel._slice_field.value == ""
    assert panel._slice_hint.visible is False
    labels = [str(getattr(item, "value", "")) for item in _walk(view)]
    labels += [str(getattr(item, "label", "")) for item in _walk(view)]
    assert any("分三卷（卷A/B/C 单文件）" in label for label in labels)
    assert any("分片条数（卷A每片文献条数，留空=不分片）" in label for label in labels)


def test_panel_ph1_default_argv_matches_legacy(enabled):
    panel = AIP.AiPanel(FakePage(enabled))
    panel._coop = True
    kwargs = dict(source=r"C:\m\main.tex", out=r"C:\out", author="张三")
    call = panel.build_call(**kwargs)
    legacy = EB.convert_call(**kwargs, verify_refs=True)
    assert call.argv == list(legacy.argv) + ["--coop-taskbook-only", "--no-ai-merge"]
    assert "--coop-split" not in _args(call)
    assert "--coop-slice" not in _args(call)


def test_panel_ph1_split_and_slice_are_wired(enabled):
    panel = AIP.AiPanel(FakePage(enabled))
    panel._coop = True
    panel._split = True
    panel._slice_text = "3"
    args = _args(panel.build_call(source="s.tex", out="out"))
    assert "--coop-split" in args
    assert _flag_value(args, "--coop-slice") == "3"


def test_panel_illegal_slice_hint_in_place_and_no_engine_no_log_clear(enabled):
    panel = AIP.AiPanel(FakePage(enabled))
    panel.build()
    panel._coop = True
    panel._slice_field.value = "-1"   # 文本框是真实输入源（与界面输入一致）
    panel._slice_text = "-1"
    panel._lines = ["上一轮日志"]
    with pytest.raises(ValueError) as info:
        panel.build_call(source="s.tex", out="out")
    assert str(info.value) == SLICE_ERROR
    # 就地中文提示（反馈区可见），且上一轮日志未被清空
    assert panel._slice_field.error == SLICE_ERROR
    assert panel._slice_hint.visible is True
    assert panel._slice_hint.value == SLICE_ERROR
    assert panel._feedback.visible is True
    assert panel._lines == ["上一轮日志"]


def test_panel_illegal_slice_does_not_block_plain_or_ph2(enabled):
    # 分片条数只在第一段生效：普通初排/第二段不因非法输入而中断
    panel = AIP.AiPanel(FakePage(enabled))
    panel._slice_text = "-1"
    assert panel.build_call(source="s.tex", out="out") is not None

    stage = FakePage(enabled)
    panel = AIP.AiPanel(stage)
    stage_dir = _stage(Path(os.environ["P2_SETTINGS"]).parent, _base_state(phase="ph2"))
    panel._coop = True
    panel._split = True
    panel._slice_text = "-1"
    panel._stage_field = _Field(str(stage_dir))
    panel._reply_fields = [_Field(str(write_text(
        Path(os.environ["P2_SETTINGS"]).parent / "reply.md", "x")))]
    call = panel.build_call(source="s.tex", out="out")
    assert panel._last_mode == AI.MODE_PH2
    assert "--coop-split" not in _args(call)
    assert "--coop-slice" not in _args(call)


def test_panel_ph2_mode_text_says_split_slice_only_ph1(enabled):
    page = FakePage(enabled)
    panel = AIP.AiPanel(page)
    panel.build()
    tmp = Path(os.environ["P2_SETTINGS"]).parent
    stage_dir = _stage(tmp, _base_state(phase="ph2"))
    panel._coop = True
    panel._split = True
    panel._slice_text = "2"
    panel._stage_field = _Field(str(stage_dir))
    panel._reply_fields = [_Field(str(write_text(tmp / "reply.md", "x")))]
    panel._refresh_mode()
    assert "只作用于第一段" in panel._mode_ctl.value


def test_panel_status_area_uses_summary_and_notices(enabled):
    tmp = Path(os.environ["P2_SETTINGS"]).parent
    stage_dir = tmp / "Zhang_待核"
    stage_dir.mkdir(parents=True, exist_ok=True)
    _write_state(stage_dir, _base_state(
        taskbook=str(write_text(stage_dir / "任务书.md", "tb")),
        taskbook_version=3, taskbook_task_id="T-9",
    ))
    panel = AIP.AiPanel(FakePage(enabled))
    panel.build()
    panel._last_mode = AI.MODE_PH1
    panel._lines = ["   ⚠ 分片：第 1/2 片未收到任何回填"]
    panel._show_stage(AI.CoopStage(str(stage_dir)))
    status = panel._status_ctl.value
    for header in ("【当前阶段与身份】", "【任务书】", "【回填检查】",
                   "【下一步】", "【采用范围（三分开）】"):
        assert header in status
    assert "   ⚠ 分片：第 1/2 片未收到任何回填" in status
    assert W.COPY_PATH_NOTICE in status
    assert W.CHARS_NOTICE in status
    assert W.LIMITS_NOTICE in status


def test_panel_after_run_ph2_success_after_cleanup_keeps_delivery_log(enabled):
    page = FakePage(enabled)
    panel = AIP.AiPanel(page)
    panel.build()
    panel._last_mode = AI.MODE_PH2
    panel._lines = ["完成。产物目录：/tmp/out"]
    panel._stage_field = _Field(str(Path(os.environ["P2_SETTINGS"]).parent / "gone_待核"))
    panel.after_run(None, 0)
    status = panel._status_ctl.value
    assert W.MISSING_STATE in status
    assert "完成。产物目录：/tmp/out" in status
    assert "技术交付完成" in panel._summary_ctl.value


def test_panel_open_stage_paths_use_existing_open_path(enabled):
    tmp = Path(os.environ["P2_SETTINGS"]).parent
    stage_dir = tmp / "Zhang_待核"
    stage_dir.mkdir(parents=True, exist_ok=True)
    taskbook = write_text(stage_dir / "任务书.md", "tb")
    reply = write_text(tmp / "reply.md", "x")
    _write_state(stage_dir, _base_state(taskbook=str(taskbook)))
    page = FakePage(enabled)
    panel = AIP.AiPanel(page)
    panel._stage_field = _Field(str(stage_dir))
    panel._reply_fields = [_Field(str(reply))]
    panel._open_stage_path("taskbook")
    panel._open_stage_path("stage")
    panel._open_stage_path("reply")
    assert page.app.opened == [str(taskbook), str(stage_dir), str(reply)]

    panel._stage_field = _Field("")
    panel._open_stage_path("final")
    assert page.app.toasts[-1] == "路径还没确定：先运行第一段，或填写暂存目录"


def test_panel_open_stage_paths_without_stage_toast(enabled):
    page = FakePage(enabled)
    panel = AIP.AiPanel(page)
    panel._open_stage_path("taskbook")
    assert page.app.opened == []
    assert page.app.toasts[-1] == "路径还没确定：先运行第一段，或填写暂存目录"
