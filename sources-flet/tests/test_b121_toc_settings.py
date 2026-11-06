"""批 121 B-356：生成目录栏目输入不再被静默丢弃（A1/A2/A4/A6），并锁 B-357 壳侧接线。

锁四组契约（离线，不建窗口、不跑引擎）：

1. A1 自动读入：进入「生成目录」/点预览时，自动读报告同目录 `目录设置.json`
   （含 columnNameEN），显式「目录设置 JSON」优先。
2. A2 自动保存：预览/生成/整期运行前把面板 values+selected 写入生效路径，
   不需要用户先点「保存栏目表」；无改动不重写；写失败显式报错。
3. A4 负向校验：栏目类型已选但名称为空 → 明确提示并阻止调用；类型为空但有勾选 → 提示。
4. A6/结果路径：整期装配 `build_step_call("toc")` 复用同一份自动保存后的设置；
   结果 JSON 走 `<报告目录>/.toolbox-tmp/目录结果.json`（旧路径可读兼容）。

另锁 B-357：`cleantemp_call` 支持 `mode="tocwork"` / `all_reports`，清理步骤在勾选
「含生成中间件与旧检查报告」（默认勾选）时串行追加一次 tocwork 调用。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from services import engine_bridge as EB
from services import toc_settings as TS
from ui import assembly as ASM
from ui import components as C
from ui import toc as TOC
from ui.tokens import Tokens

from test_b112_post_b import _Store


# ── 假壳：够 TocPanel 的自动读入/保存/校验用 ───────────────────────────────
class _PanelPage:
    def __init__(self, store: _Store | None = None) -> None:
        self.fields_data: dict[str, str] = {}
        self.marks: list[tuple[str, dict]] = []
        self.app = SimpleNamespace(store=store or _Store(), notes_repo=None,
                                   page=SimpleNamespace(update=lambda: None))
        self.page = SimpleNamespace(update=lambda: None)

    def field(self, key: str) -> str:
        return self.fields_data.get(key, "")

    def set_field(self, key: str, value) -> None:
        self.fields_data[key] = str(value or "")

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))


def _t() -> Tokens:
    return Tokens("light", 1.0, 96)


def _args_of(call: EB.EngineCall) -> list[str]:
    """去掉 base_cmd 前缀（引擎可执行/解释器），只看脚本参数。"""
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


def _make_panel(tmp_path: Path, *, settings: dict | None = None,
                selected: list[str] | None = None) -> tuple[TOC.TocPanel, Path, _PanelPage]:
    report = tmp_path / "信息检查-最新.txt"
    report.write_text("report", encoding="utf-8")
    page = _PanelPage()
    page.set_field("toc_report", str(report))
    panel = object.__new__(TOC.TocPanel)
    panel.page = page
    panel.app = page.app
    panel.t = _t()
    panel.payload = {}
    panel.values = TS.defaults()
    panel.selected = list(selected or [])
    panel.available = []
    panel.info = ft.Container()
    panel.feedback = ft.Container()
    panel.editor_box = ft.Column(spacing=0)
    panel.add_box = C.select(panel.t, value="", label="可加入的篇目", options=[], expand=True)
    panel.kind = None
    panel._loaded_path = ""
    # B-373/B-374 新增私有状态：旧页面/旧 helper 没有时实现侧用 getattr 兜底。
    panel._loaded_report = ""
    panel._sel_explicit = False
    panel._load_error = ""
    panel._load_error_path = ""
    if settings:
        panel.values.update(settings)
    panel._saved_snapshot = panel._snapshot()
    panel._dirty = False
    return panel, report, page


def _write_settings(path: Path, **overrides) -> dict:
    data = TS.normalize({**TS.defaults(), **overrides})
    ok, saved = TS.save(str(path), data)
    assert ok, saved
    return data


# ── A1：自动读入 ─────────────────────────────────────────────────────────
def test_auto_load_reads_default_settings_next_to_report(tmp_path):
    folder = tmp_path / "2023年第6期(73)"
    folder.mkdir()
    panel, report, _ = _make_panel(folder)
    settings = _write_settings(folder / TS.FILE_NAME, columnKind="专栏",
                               columnName="逻辑与概率", columnNameEN="Logic and Probability",
                               columnHost="张三", columnArticles=["Yanjun_Li", "Jie_Fan"])

    panel._auto_load_settings(str(report))

    assert panel.values["columnName"] == "逻辑与概率"
    assert panel.values["columnNameEN"] == "Logic and Probability"
    assert panel.values["columnHost"] == "张三"
    assert panel.selected == ["Yanjun_Li", "Jie_Fan"]
    assert panel._loaded_path == str(folder / TS.FILE_NAME)
    assert panel._dirty is False
    assert panel._settings_path() == str(folder / TS.FILE_NAME)
    assert settings["columnArticles"] == ["Yanjun_Li", "Jie_Fan"]


def test_auto_load_keeps_panel_edits_when_file_missing(tmp_path):
    panel, report, _ = _make_panel(tmp_path, settings={"columnKind": "专辑",
                                                       "columnName": "手改"})
    panel._auto_load_settings(str(report))
    assert panel.values["columnKind"] == "专辑" and panel.values["columnName"] == "手改"
    # 路径已记录（避免每次 params 重复尝试读取），但没有文件就不覆盖面板手改值。
    assert panel._loaded_path == str(tmp_path / TS.FILE_NAME)
    assert not (tmp_path / TS.FILE_NAME).exists()


# ── A2：自动保存（含 columnNameEN），无改动不重写 ────────────────────────
def test_sync_settings_writes_values_and_selected_with_backup(tmp_path):
    folder = tmp_path / "2023年第6期(73)"
    folder.mkdir()
    panel, report, _ = _make_panel(folder)
    target = folder / TS.FILE_NAME
    _write_settings(target, columnKind="专栏", columnName="旧")
    panel._auto_load_settings(str(report))

    panel.values["columnKind"] = "专栏"
    panel.values["columnName"] = "逻辑与概率"
    panel.values["columnNameEN"] = "Logic and Probability"
    panel.selected = ["Yanjun_Li"]
    panel._mark_dirty()

    path, error = panel.sync_settings(force=True)

    assert error == ""
    assert path == str(target)
    loaded, read_error = TS.read(path)
    assert read_error == ""
    assert loaded["columnKind"] == "专栏"
    assert loaded["columnName"] == "逻辑与概率"
    assert loaded["columnNameEN"] == "Logic and Probability"
    assert loaded["columnArticles"] == ["Yanjun_Li"]
    assert panel._dirty is False
    assert list(folder.glob(TS.FILE_NAME + ".bak-*")), "覆盖前必须留 .bak 备份"


def test_sync_settings_skips_rewrite_when_nothing_changed(tmp_path):
    folder = tmp_path / "期"
    folder.mkdir()
    panel, report, _ = _make_panel(folder)
    target = folder / TS.FILE_NAME
    _write_settings(target, columnKind="专栏", columnName="逻辑与概率")
    panel._auto_load_settings(str(report))
    before = target.stat().st_mtime_ns

    path, error = panel.sync_settings()

    assert error == "" and path == str(target)
    assert target.stat().st_mtime_ns == before
    assert not list(folder.glob(TS.FILE_NAME + ".bak-*")), "无改动不应触发备份/重写"


# ── A4：负向校验 ─────────────────────────────────────────────────────────
def test_column_issue_blocks_kind_without_name():
    panel, _report, _ = _make_panel(Path("/tmp"))
    panel.values["columnKind"] = "专栏"
    panel.values["columnName"] = ""
    blocked, hint = panel.column_issue()
    assert blocked is True and "栏目名称" in hint

    panel.values["columnKind"] = ""
    panel.selected = ["A"]
    blocked, hint = panel.column_issue()
    assert blocked is False and hint

    panel.selected = []
    assert panel.column_issue() == (False, "")


def test_preview_task_refuses_invalid_column_without_calling_engine(tmp_path, monkeypatch):
    panel, _report, page = _make_panel(tmp_path)
    panel.values["columnKind"] = "专栏"
    panel.values["columnName"] = ""

    def _boom(*args, **kwargs):
        raise AssertionError("A4 阻止时不得调用引擎")

    monkeypatch.setattr(TOC.EB, "read_json_command", _boom)
    asyncio.run(panel._preview_task())

    events = [event for event, _fields in page.marks]
    assert "toc_column_invalid" in events
    assert not (tmp_path / TS.FILE_NAME).exists(), "阻止时不应写设置文件"


# ── B1：结果 JSON 新路径 + 旧路径兼容 ────────────────────────────────────
def test_result_json_paths_prefer_toolbox_tmp_and_read_legacy(tmp_path):
    report = tmp_path / "信息检查-最新.txt"
    report.write_text("x", encoding="utf-8")
    new_path = Path(TOC.result_json_path(str(report)))
    legacy = Path(TOC.legacy_result_json_path(str(report)))

    assert new_path == tmp_path / TOC.TMP_DIR / TOC.RESULT_JSON_NAME
    assert legacy == tmp_path / TOC.RESULT_JSON_NAME
    # 新旧都没有时返回新目标路径（调用方再判断 exists 显示提示）。
    assert TOC.existing_result_json_path(str(report)) == str(new_path)
    assert TOC.ensure_result_dir(str(report)) == str(new_path)
    assert new_path.parent.is_dir()

    legacy.write_text('{"pages": []}', encoding="utf-8")
    assert TOC.existing_result_json_path(str(report)) == str(legacy)
    new_path.write_text('{"pages": [1]}', encoding="utf-8")
    assert TOC.existing_result_json_path(str(report)) == str(new_path)
    assert TOC.read_result(str(report)) == {} or isinstance(TOC.read_result(str(report)), dict)


# ── A6：整期装配 build_step_call("toc") 复用自动保存后的设置 ─────────────
def _step_page(tmp_path: Path) -> tuple[SimpleNamespace, Path]:
    report = tmp_path / "信息检查-最新.txt"
    report.write_text("x", encoding="utf-8")
    store = _Store()
    page = SimpleNamespace(
        app=SimpleNamespace(store=store, notes_repo=None),
        field=lambda key: str({"issue_dir": str(tmp_path),
                               "toc_report": str(report)}.get(key, "")),
        set_field=lambda key, value: None,
        mark=lambda *args, **kwargs: None,
        _toc_panel=None,
    )
    return page, report


def test_build_step_call_uses_panel_auto_saved_settings(tmp_path):
    page, report = _step_page(tmp_path)
    settings = tmp_path / TS.FILE_NAME
    _write_settings(settings, columnKind="专栏", columnName="逻辑与概率",
                    columnNameEN="Logic and Probability")
    page._toc_panel = SimpleNamespace(
        column_issue=lambda: (False, ""),
        sync_settings=lambda: (str(settings), ""),
    )

    call = ASM.build_step_call(page, "toc")
    argv = list(call.argv)

    args = _args_of(call)
    assert "--settings" in args and args[args.index("--settings") + 1] == str(settings)
    result_json = args[args.index("--result-json") + 1]
    assert result_json == TOC.result_json_path(str(report))
    assert (tmp_path / TOC.TMP_DIR).is_dir()


def test_build_step_call_falls_back_to_saved_default_and_recent(tmp_path):
    page, report = _step_page(tmp_path)
    # 无面板：优先报告同目录已保存文件。
    saved = tmp_path / TS.FILE_NAME
    _write_settings(saved, columnKind="专栏", columnName="已保存")
    call = ASM.build_step_call(page, "toc")
    args = _args_of(call)
    assert args[args.index("--settings") + 1] == str(saved)

    # 已保存文件不存在时回退 recent（与旧行为一致）。
    saved.unlink()
    page.app.store.set("recent.toc_settings", str(tmp_path / "显式.json"))
    call = ASM.build_step_call(page, "toc")
    args = _args_of(call)
    assert args[args.index("--settings") + 1] == str(tmp_path / "显式.json")


def test_build_step_call_refuses_invalid_column_from_panel(tmp_path):
    page, _report = _step_page(tmp_path)
    page._toc_panel = SimpleNamespace(
        column_issue=lambda: (True, "栏目类型已选，请填栏目名称"),
        sync_settings=lambda: ("", ""),
    )
    try:
        ASM.build_step_call(page, "toc")
    except ValueError as exc:
        assert "栏目名称" in str(exc)
    else:  # pragma: no cover - 必须显式中止
        raise AssertionError("栏目名称缺失必须阻止整期运行")


# ── B-357：cleantemp 新档位与「aux + tocwork」串联 ────────────────────────
def test_cleantemp_call_tocwork_mode_and_all_reports():
    base = EB.cleantemp_call(folder="f")
    assert _args_of(base) == ["--folder", "f", "--trash", "--json"]
    assert base.label == "删除临时文件"

    call = EB.cleantemp_call(folder="f", mode="tocwork")
    assert _args_of(call) == ["--folder", "f", "--mode", "tocwork", "--json"]
    assert call.label == "清理生成中间件"

    more = EB.cleantemp_call(folder="f", mode="tocwork", all_reports=True, dry_run=True)
    args = _args_of(more)
    assert "--dry-run" in args and "--all-reports" in args
    assert "--trash" not in args


def test_cleantemp_step_chains_tocwork_only_when_enabled_and_real_run(tmp_path):
    store = _Store()
    page = SimpleNamespace(
        app=SimpleNamespace(store=store, notes_repo=None),
        field=lambda key: str(tmp_path) if key == "issue_dir" else "",
        set_field=lambda *args, **kwargs: None,
        mark=lambda *args, **kwargs: None,
    )
    store.set("options.cleantemp_dry_run", False)

    call = ASM.build_step_call(page, "cleantemp")
    followup = getattr(call, "_followup", None)
    assert followup is not None, "默认勾选时必须追加 tocwork 清理"
    follow_args = _args_of(followup)
    assert "--mode" in follow_args and "tocwork" in follow_args
    assert "--dry-run" not in follow_args

    store.set("options.cleantemp_tocwork", False)
    assert getattr(ASM.build_step_call(page, "cleantemp"), "_followup", None) is None

    store.set("options.cleantemp_tocwork", True)
    store.set("options.cleantemp_dry_run", True)
    assert getattr(ASM.build_step_call(page, "cleantemp"), "_followup", None) is None


# ── B-373（A-01）：切换报告不得把上一份报告的草稿写进新报告 ────────────────
def _two_reports(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """A/B 两个期目录各一份报告 + 各一份栏目设置；返回 (报告A, 报告B, 设置A, 设置B)。"""
    a_dir = tmp_path / "A期"
    b_dir = tmp_path / "B期"
    a_dir.mkdir()
    b_dir.mkdir()
    report_a = a_dir / "信息检查-最新.txt"
    report_a.write_text("A", encoding="utf-8")
    report_b = b_dir / "信息检查-最新.txt"
    report_b.write_text("B", encoding="utf-8")
    settings_a = a_dir / TS.FILE_NAME
    settings_b = b_dir / TS.FILE_NAME
    _write_settings(settings_a, columnKind="专栏", columnName="A栏目")
    _fix = json.dumps(TS.normalize({**TS.defaults(), "columnKind": "专栏",
                                   "columnName": "B栏目"}), ensure_ascii=False)
    settings_b.write_text(_fix, encoding="utf-8")  # 用户在编辑器里修好 JSON
    return report_a, report_b, settings_a, settings_b


def test_dirty_switch_saves_draft_to_old_path_and_keeps_target_bytes(tmp_path):
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    before_a = settings_a.read_bytes()
    before_b = settings_b.read_bytes()

    # A 上未保存的编辑（真实路径：输入框 on_change → _set_value → 脏）。
    panel.values["columnName"] = "A未保存编辑"
    panel._mark_dirty()

    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))

    # 草稿写回 A 的原归属路径；面板改成显示 B 的值。
    saved_a, error = TS.read(str(settings_a))
    assert error == ""
    assert saved_a["columnName"] == "A未保存编辑"
    assert settings_a.read_bytes() != before_a
    assert list(settings_a.parent.glob(TS.FILE_NAME + ".bak-*")), "写回 A 要沿用 TS.save 的备份"
    assert panel.values["columnName"] == "B栏目"
    assert panel.selected == []
    assert panel._loaded_path == str(settings_b)
    assert panel._dirty is False
    assert settings_b.read_bytes() == before_b, "B 的设置文件一个字节都不能被 A 的草稿改动"
    assert not list(settings_b.parent.glob(TS.FILE_NAME + ".bak-*")), "B 不该产生备份"


def test_failed_old_path_save_keeps_draft_and_blocks_generation(tmp_path, monkeypatch):
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    before_a = settings_a.read_bytes()
    before_b = settings_b.read_bytes()
    real_save = TS.save

    def _fail_old_path(path, data):
        if Path(path) == settings_a:
            return False, "注入失败：原路径不可写"
        return real_save(path, data)

    monkeypatch.setattr(TS, "save", _fail_old_path)
    panel.values["columnName"] = "A草稿"
    panel._mark_dirty()
    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))

    # 保存失败：不推进到 B、不清脏、草稿与两处文件都不动。
    assert panel._dirty is True
    assert panel.values["columnName"] == "A草稿"
    assert panel._loaded_path == str(settings_a)
    assert settings_a.read_bytes() == before_a
    assert settings_b.read_bytes() == before_b
    events = [event for event, _fields in page.marks]
    assert "toc_settings_error" in events
    assert "toc_settings_switch_blocked" in events

    # 生成前同样阻断：不能拿 A 的草稿去配 B 的报告。
    def _boom(*args, **kwargs):
        raise AssertionError("栏目表保存失败时不得调用引擎")

    monkeypatch.setattr(TOC.EB, "read_json_command", _boom)
    asyncio.run(panel._generate_task())

    events = [event for event, _fields in page.marks]
    assert "toc_settings_error" in events
    assert "toc_generated" not in events, "保存失败必须中止生成"
    assert settings_a.read_bytes() == before_a
    assert settings_b.read_bytes() == before_b
    assert not (report_b.parent / TOC.TMP_DIR).exists(), "中止时不应创建结果目录"


def test_clean_switch_writes_nothing(tmp_path):
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    before_a = settings_a.read_bytes()
    before_b = settings_b.read_bytes()

    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))

    assert panel.values["columnName"] == "B栏目"
    assert settings_a.read_bytes() == before_a
    assert settings_b.read_bytes() == before_b
    assert not list(settings_a.parent.glob(TS.FILE_NAME + ".bak-*"))


def test_dirty_same_path_reentry_does_not_save(tmp_path):
    folder = tmp_path / "期"
    folder.mkdir()
    panel, report, _page = _make_panel(folder)
    target = folder / TS.FILE_NAME
    _write_settings(target, columnKind="专栏", columnName="逻辑与概率")
    panel._auto_load_settings(str(report))
    before = target.read_bytes()

    panel.values["columnName"] = "改一半"
    panel._mark_dirty()
    panel._auto_load_settings(str(report))  # 同路径重入必须直接返回

    assert target.read_bytes() == before
    assert not list(folder.glob(TS.FILE_NAME + ".bak-*"))
    assert panel._dirty is True
    assert panel.values["columnName"] == "改一半"


def test_explicit_settings_path_still_wins_over_report_dir(tmp_path):
    folder = tmp_path / "期"
    folder.mkdir()
    panel, report, page = _make_panel(folder)
    _write_settings(folder / TS.FILE_NAME, columnKind="专栏", columnName="报告目录栏目")
    explicit = tmp_path / "显式设置.json"
    _write_settings(explicit, columnKind="专辑", columnName="显式栏目")
    page.set_field("toc_settings", str(explicit))
    page.set_field("toc_report", str(report))

    panel._auto_load_settings(str(report))

    assert panel._settings_path() == str(explicit)
    assert panel._loaded_path == str(explicit)
    assert panel.values["columnName"] == "显式栏目"


def test_switch_to_report_without_settings_uses_defaults(tmp_path):
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    settings_b.unlink()  # B 这份报告还没有栏目设置文件
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    assert panel.values["columnName"] == "A栏目"

    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))

    assert panel.values == TS.defaults(), "B 没有设置文件时用 B 的默认值，不继承 A"
    assert panel.selected == []
    assert panel._loaded_path == str(settings_b)
    assert panel._dirty is False
    assert not settings_b.exists(), "只在读入时不得凭空创建空设置文件"


def test_invalid_target_json_is_not_overwritten(tmp_path):
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    settings_b.write_text("{ 这不是合法 JSON", encoding="utf-8")
    broken = settings_b.read_bytes()
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    panel.values["columnName"] = "A草稿"
    panel._mark_dirty()

    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))

    events = [event for event, _fields in page.marks]
    assert "toc_settings_error" in events
    assert settings_b.read_bytes() == broken, "非法 JSON 的目标文件不得被覆盖"
    assert not list(settings_b.parent.glob(TS.FILE_NAME + ".bak-*"))
    saved_a, _error = TS.read(str(settings_a))
    assert saved_a["columnName"] == "A草稿", "草稿仍要写回原报告目录"


def test_assembly_toc_call_blocked_after_failed_old_path_save(tmp_path, monkeypatch):
    """整期装配同样不能拿 A 的草稿去配 B 的报告（B-373 的第二个入口）。"""
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("issue_dir", str(tmp_path))
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    real_save = TS.save

    def _fail_old_path(path, data):
        if Path(path) == settings_a:
            return False, "注入失败：原路径不可写"
        return real_save(path, data)

    monkeypatch.setattr(TS, "save", _fail_old_path)
    panel.values["columnName"] = "A草稿"
    panel._mark_dirty()
    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))
    page._toc_panel = panel
    before_b = settings_b.read_bytes()

    try:
        ASM.build_step_call(page, "toc")
    except ValueError as exc:
        assert "栏目表" in str(exc), str(exc)
    else:  # pragma: no cover - 必须显式中止
        raise AssertionError("草稿归属不一致时必须阻止整期运行")
    assert settings_b.read_bytes() == before_b


# ── 返工 R2（Codex 独立验收）：目标设置读失败不能被当作成功载入 ─────────────
def test_invalid_target_read_blocks_sync_and_generation_then_recovers(tmp_path, monkeypatch):
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    panel.values["columnName"] = "A未保存"
    panel._mark_dirty()
    broken = '{ broken JSON'
    settings_b.write_text(broken, encoding="utf-8")

    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))

    # A 草稿已回写 A；B 原字节不动；面板保持 A 值但状态是「读入失败」。
    saved_a, _ = TS.read(str(settings_a))
    assert saved_a["columnName"] == "A未保存"
    assert settings_b.read_text(encoding="utf-8") == broken
    assert panel.values["columnName"] == "A未保存"
    assert any(event == "toc_settings_error" for event, _ in page.marks)
    _path, error = panel.sync_settings()
    assert error, "目标读取失败必须阻断生成前 sync，不能返回成功"

    # 预览/生成/整期装配三个入口都不得调用引擎。
    def _boom(*args, **kwargs):
        raise AssertionError("设置读入失败时不得调用引擎")

    monkeypatch.setattr(TOC.EB, "read_json_command", _boom)
    asyncio.run(panel._generate_task())
    asyncio.run(panel._preview_task())
    assert "toc_generated" not in [event for event, _ in page.marks]
    assert "toc_preview" not in [event for event, _ in page.marks]
    page._toc_panel = panel
    page.set_field("issue_dir", str(tmp_path))
    try:
        ASM.build_step_call(page, "toc")
    except ValueError as exc:
        assert "栏目表" in str(exc), str(exc)
    else:  # pragma: no cover - 必须显式中止
        raise AssertionError("设置读入失败时必须阻止整期运行")
    assert settings_b.read_text(encoding="utf-8") == broken

    # 修好 B 的 JSON 后重新自动读入即恢复：错误清除、面板换成 B 的值、sync 通过。
    _fix = json.dumps(TS.normalize({**TS.defaults(), "columnKind": "专栏",
                                   "columnName": "B栏目"}), ensure_ascii=False)
    settings_b.write_text(_fix, encoding="utf-8")  # 用户在编辑器里修好 JSON
    panel._auto_load_settings(str(report_b))
    assert panel.values["columnName"] == "B栏目"
    assert panel._loaded_path == str(settings_b)
    assert panel.sync_settings() == (str(settings_b), "")


def test_switch_back_to_owner_after_target_read_error_clears_error(tmp_path):
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    panel.values["columnName"] = "A未保存"
    panel._mark_dirty()
    settings_b.write_text("{ broken JSON", encoding="utf-8")
    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))
    assert panel.sync_settings()[1], "先确认处于读失败阻断态"

    # 切回草稿归属的报告（A）：读失败状态必须解除，可正常继续。
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    assert panel.values["columnName"] == "A未保存"
    assert panel.sync_settings() == (str(settings_a), "")


def test_explicit_reload_recovers_after_target_read_error(tmp_path):
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    settings_b.write_text("{ broken JSON", encoding="utf-8")
    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))
    assert panel.sync_settings()[1]

    _fix = json.dumps(TS.normalize({**TS.defaults(), "columnKind": "专栏",
                                   "columnName": "B栏目"}), ensure_ascii=False)
    settings_b.write_text(_fix, encoding="utf-8")  # 用户在编辑器里修好 JSON
    panel._load_settings()  # 「读取栏目表」显式重载

    assert panel.values["columnName"] == "B栏目"
    assert panel._loaded_path == str(settings_b)
    assert panel.sync_settings() == (str(settings_b), "")


# ── 返工 R3（Codex 独立验收）：真实写盘 OSError 必须转成可见错误并留草稿 ────
def test_real_save_oserror_is_reported_and_keeps_draft(tmp_path, monkeypatch):
    report_a, report_b, settings_a, settings_b = _two_reports(tmp_path)
    panel, _report, page = _make_panel(tmp_path)
    page.set_field("toc_report", str(report_a))
    panel._auto_load_settings(str(report_a))
    before_a = settings_a.read_bytes()
    before_b = settings_b.read_bytes()
    panel.values["columnName"] = "A草稿"
    panel._mark_dirty()
    real_backup = TS.backup

    def _denied(path):
        raise PermissionError("注入：备份目录只读")

    monkeypatch.setattr(TS, "backup", _denied)
    page.set_field("toc_report", str(report_b))
    panel._auto_load_settings(str(report_b))  # 真实 TS.save 在 backup 阶段抛 OSError

    assert panel._dirty is True
    assert panel.values["columnName"] == "A草稿"
    assert panel._loaded_path == str(settings_a), "写失败不得推进草稿归属"
    assert settings_a.read_bytes() == before_a
    assert settings_b.read_bytes() == before_b
    assert any(event == "toc_settings_error" for event, _ in page.marks)
    _path, error = panel.sync_settings()
    assert error, "真实写盘异常后必须阻断后续执行"

    # 恢复可写后再次切换：草稿写回 A，B 正常读入。
    monkeypatch.setattr(TS, "backup", real_backup)
    panel._auto_load_settings(str(report_b))
    saved_a, _ = TS.read(str(settings_a))
    assert saved_a["columnName"] == "A草稿"
    assert panel.values["columnName"] == "B栏目"
    assert panel._dirty is False


def test_write_oserror_returns_error_contract_and_blocks_generation(tmp_path, monkeypatch):
    folder = tmp_path / "期"
    folder.mkdir()
    panel, report, _page = _make_panel(folder)
    target = folder / TS.FILE_NAME
    _write_settings(target, columnKind="专栏", columnName="原栏目")
    panel._auto_load_settings(str(report))
    before = target.read_bytes()
    panel.values["columnName"] = "改后"
    panel._mark_dirty()

    def _raise(path, data):
        raise OSError("注入：os.replace 失败")

    monkeypatch.setattr(TS, "save", _raise)
    path, error = panel.sync_settings(force=True)

    assert path == str(target)
    assert error and "os.replace" in error
    assert panel._dirty is True and panel.values["columnName"] == "改后"
    assert target.read_bytes() == before
    assert not list(folder.glob(TS.FILE_NAME + ".bak-*"))

    # 生成前同样阻断，不调用引擎。
    def _boom(*args, **kwargs):
        raise AssertionError("写盘失败时不得调用引擎")

    monkeypatch.setattr(TOC.EB, "read_json_command", _boom)
    asyncio.run(panel._generate_task())
    assert "toc_generated" not in [event for event, _ in _page.marks]
