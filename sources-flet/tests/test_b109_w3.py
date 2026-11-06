"""批 109 合并 W3：清理三档/撤销、目录设置、推送、设置外壳回归。

关键阻断断言：永久删除参数不存在；三档与撤销 argv 正确；目录/推送读引擎 JSON；
目录设置写前备份且坏 JSON 不覆盖；日志有上限、诊断脱敏、快捷键动作固定。
不启动 GUI、不碰真实 bib_db；引擎目录用桩。
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import engine_bridge as EB
from services import settings_store as SS
from services import toc_settings as TS
from ui import assembly as ASM
from ui import dirpush as DP
from ui import settings_shell as SHELL
from ui import toc as TOC
from ui.tokens import Tokens

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    root = tmp_path / "Engine"
    root.mkdir()
    for entry in EB.ENTRIES:
        (root / entry).write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("P2_ENGINE", str(root))
    for name in ("P2_ENGINE_EXE", "SLCN_ENGINE_EXE", "P2_ENGINE_PYTHON", "SLCN_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    return root


def args_of(call: EB.EngineCall) -> list[str]:
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


# ── 清理三档与撤销 ───────────────────────────────────────────────────────
def test_cleantemp_three_modes_and_restore(engine):
    default = args_of(EB.cleantemp_call(folder="f"))
    assert default == ["--folder", "f", "--trash", "--json"]
    assert "--mode" not in default and "--restore" not in default

    workspace = args_of(EB.cleantemp_call(folder="f", mode="workspace"))
    assert workspace == ["--folder", "f", "--mode", "workspace", "--json"]
    assert "--trash" not in workspace and "--delete" not in workspace

    undo = args_of(EB.cleantemp_call(folder="f", restore="/f/.toolbox-trash/x"))
    assert undo == ["--folder", "f", "--restore", "/f/.toolbox-trash/x", "--json"]
    for call in (EB.cleantemp_call(folder="f"), EB.cleantemp_call(folder="f", mode="delivery"),
                 EB.cleantemp_call(folder="f", restore="t")):
        assert "--delete" not in call.argv and "--empty-trash" not in call.argv


# ── 目录：参数与 JSON 读取 ────────────────────────────────────────────────
def test_toc_preview_place_and_result_json(engine):
    preview = args_of(EB.toc_call(report="信息检查.txt", settings="目录设置.json", preview=True))
    assert "--preview" in preview and "--settings" in preview
    assert "--place" not in preview and "--compile" not in preview

    place = args_of(EB.toc_call(report="r.txt", template="期目录", result_json="目录结果.json"))
    assert "--place" in place and "--compile" in place
    assert place[place.index("--result-json") + 1] == "目录结果.json"

    text_only = args_of(EB.toc_call(report="r.txt", text_only=True))
    assert "--place" not in text_only and "--compile" not in text_only


def test_toc_result_paths_and_preview_lines():
    payload = {
        "templateSource": "上一期成品", "templateNote": "沿用上期版式",
        "columnEnabled": True, "columnArticles": ["01 A", "02 B"],
        "placed": {"zhTex": "a.tex", "zhPdf": "a.pdf", "enTex": "", "enPdf": ""},
        "pages": {"zh": 4, "en": 2},
    }
    labels = dict(TOC.result_paths(payload))
    assert labels["中文目录 tex"] == "a.tex" and "英文目录 tex" not in labels
    lines = TOC.preview_lines(payload)
    assert any("上一期成品" in line for line in lines)
    assert any("已启用" in line and "2" in line for line in lines)


# ── 推送：当期/年度与代码仓二次确认 ─────────────────────────────────────
def test_dirpush_annual_argv_and_repo_warning(engine, tmp_path):
    annual = args_of(EB.dirpush_call(issue="期目录", out_root="out", annual=True,
                                     year="2026", include_issues="3, 2,2"))
    assert "--annual" in annual and annual[annual.index("--year") + 1] == "2026"
    assert annual[annual.index("--include-issues") + 1] == "2,3"
    assert "--json" in annual

    issue = args_of(EB.dirpush_call(issue="期目录", with_en_toc=True))
    assert "--with-en-toc" in issue and "--annual" not in issue

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert "代码仓内" in DP.repo_warning(str(repo / "out"), repo=repo)
    assert DP.repo_warning(str(tmp_path / "else"), repo=repo) == ""
    assert DP.repo_warning("", repo=repo) == ""


def test_dirpush_summary_reads_engine_json():
    payload = {
        "issue": {"year": 2026, "number": 2, "vol": 5, "total": 20},
        "counts": {"zh_entries": 3, "en_entries": 2, "articles": 3},
        "outputs": [], "dry_run": True, "warnings": ["输出根在代码仓内"],
    }
    lines = DP.summary_lines(payload)
    assert any("2026 年第 2 期" in line for line in lines)
    assert any("中文 3" in line for line in lines)
    assert any("预览" in line for line in lines)
    assert any("代码仓内" in line for line in lines)
    assert DP.size_text(2048) == "2 KB"


# ── 目录设置.json：读写、备份、坏 JSON 不覆盖 ────────────────────────────
def test_toc_settings_roundtrip_backup_and_bad_json(tmp_path):
    missing = tmp_path / "目录设置.json"
    data, error = TS.read(str(missing))
    assert data == TS.defaults() and error == ""

    saved = TS.save(str(missing), {"columnKind": "专栏", "columnName": "逻辑与计算",
                                   "columnArticles": ["01 A", " 02 B "],
                                   "titleWrapLimit": "18"})
    assert saved[0] is True
    loaded, error = TS.read(str(missing))
    assert error == "" and loaded["columnName"] == "逻辑与计算"
    assert loaded["columnArticles"] == ["01 A", "02 B"]
    assert loaded["titleWrapLimit"] == 18.0

    assert TS.save(str(missing), {"columnName": "改"})[0] is True
    backups = list(tmp_path.glob("目录设置.json.bak-*"))
    assert len(backups) == 1 and "逻辑与计算" in backups[0].read_text(encoding="utf-8")

    broken = tmp_path / "坏设置.json"
    broken.write_text("{not json", encoding="utf-8")
    ok, message = TS.save(str(broken), {"columnName": "x"})
    assert ok is False and "不是合法 JSON" in message
    assert broken.read_text(encoding="utf-8") == "{not json"


# ── 装配调用：缺期目录/默认报告/三档/年度 ────────────────────────────────
class _Store:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def update_options(self, **kwargs):
        self.data.setdefault("options", {}).update(kwargs)


class _App:
    running = False
    auto_confirm = True
    notes_repo = ""

    def __init__(self, data=None):
        self.store = _Store(data)
        self.toasts: list[str] = []
        self.marks: list[tuple] = []
        self.engine_status = SimpleNamespace(ok=True, version="v", mode="dev",
                                             engine_dir="", describe=lambda: "stub",
                                             problems=[])

    def toast(self, message):
        self.toasts.append(message)

    def mark(self, event, **fields):
        self.marks.append((event, fields))

    def set_option(self, key, value):
        self.store.update_options(**{key: value})


class _Page:
    STEPS = [{"key": k, "desc": k, "title": k, "icon": ""}
             for k in ("infocheck", "toc", "compileall", "merge", "cleantemp", "dirpush")]

    def __init__(self, data=None):
        self.app = _App(data)
        self.t = Tokens()
        self._fields: dict[str, str] = {}
        self._status: dict[str, str] = {}
        self.page = SimpleNamespace(run_task=lambda *a, **k: None,
                                    update=lambda *a, **k: None)
        self._result = SimpleNamespace(show=lambda *a, **k: None)

    def field(self, key):
        return self._fields.get(key, "")

    def set_field(self, key, value):
        self._fields[key] = value

    def mark(self, event, **fields):
        self.app.marks.append((event, fields))

    def _refresh_steps(self):
        pass


def test_assembly_step_calls_and_guards(tmp_path, engine):
    page = _Page()
    with pytest.raises(ValueError, match="请先选择期目录"):
        ASM.build_step_call(page, "cleantemp")

    page.set_field("issue_dir", str(tmp_path))
    page.app.store.set("form.cleantemp_mode", "delivery")
    page.app.store.update_options(cleantemp_dry_run=True)
    clean = ASM.build_step_call(page, "cleantemp")
    assert "--mode" in clean.argv and "delivery" in clean.argv
    assert "--dry-run" in clean.argv and "--trash" not in clean.argv
    assert ASM.cleantemp_mode(page) == "delivery"
    assert ASM.cleantemp_dry_run(page) is True

    with pytest.raises(ValueError, match="信息检查报告"):
        ASM.build_step_call(page, "toc")


def test_assembly_toc_uses_latest_report_and_result_json(tmp_path, engine):
    report = tmp_path / "信息检查-最新.txt"
    report.write_text("stub", encoding="utf-8")
    page = _Page()
    page.set_field("issue_dir", str(tmp_path))
    page.app.store.update_options(toc_text_only=False)
    # 2026-11-06 Lead m00984(3)：只传真实存在的设置文件，不把已删除/搬走的
    # recent 旧路径塞给引擎（引擎会「❌ 找不到设置文件」直接拒绝）。
    stale = tmp_path / "旧目录" / TS.FILE_NAME
    page.app.store.set("recent.toc_settings", str(stale))
    call = ASM.build_step_call(page, "toc")
    assert page.field("toc_report") == str(report)
    args = args_of(call)
    assert "--place" in args and "--compile" in args
    assert args[args.index("--result-json") + 1].endswith("目录结果.json")
    assert "--settings" not in args, args

    # recent 指向真实存在的 目录设置.json 时仍作为兜底传给引擎。
    stale.parent.mkdir()
    stale.write_text(json.dumps(TS.defaults(), ensure_ascii=False), encoding="utf-8")
    call = ASM.build_step_call(page, "toc")
    args = args_of(call)
    assert args[args.index("--settings") + 1] == str(stale)


def test_assembly_dirpush_annual_mode(tmp_path, engine):
    page = _Page()
    page.set_field("issue_dir", str(tmp_path))
    page.app.store.set("form.dirpush_mode", "annual")
    page.app.store.set("form.dirpush_year", "2026")
    page.app.store.set("form.dirpush_issues", "1,2")
    call = ASM.build_step_call(page, "dirpush")
    args = args_of(call)
    assert "--annual" in args and "--include-issues" in args


# ── 设置外壳：日志上限/脱敏/快捷键/诊断 ─────────────────────────────────
def test_log_buffer_limit_filter_and_classify():
    buffer = SHELL.LogBuffer(limit=5, trim=2)
    for index in range(7):
        buffer.add(f"line {index}", "error" if index % 2 else "info")
    assert buffer.count() == 5 and buffer.lines()[0] == "line 2"
    assert all(line.startswith("line") for line in buffer.lines("error"))
    assert len(buffer.lines("error")) == 2
    assert SHELL.classify("❌ boom") == "error"
    assert SHELL.classify("⚠️ 警告") == "warn"
    assert SHELL.LOG_LIMIT == 4000 and SHELL.LOG_TRIM == 2000


def test_hotkeys_are_fixed():
    assert SHELL.hotkey_action(",", ctrl=True) == "settings"
    assert SHELL.hotkey_action("F1") == "help"
    assert SHELL.hotkey_action("F5") == "refresh"
    assert SHELL.hotkey_action("F9") == "run"
    assert SHELL.hotkey_action("Escape") == "stop"
    assert SHELL.hotkey_action("d", ctrl=True, shift=True) == "theme"
    assert SHELL.hotkey_action("2", ctrl=True) == "page:2"


def test_diagnosis_redacts_secrets_and_writes(tmp_path):
    store = SS.SettingsStore()
    store.path = tmp_path / "settings.json"
    store.data = {"ai": {"key": "sk-secret", "model": "deepseek-flash"},
                  "paths": {"notes_repo": str(tmp_path)}}
    app = SimpleNamespace(
        app_version="v-test", flet_version="1.0", python_version="3.14",
        dpi=96, window_w=1200, window_h=800, theme="light", zoom=1.0,
        store=store, page=SimpleNamespace(platform="windows"), client_hwnd=0,
        titlebar_info={}, notes_repo="", running=False, auto_confirm=True,
        marks=[], toast=lambda message: None,
        mark=lambda event, **fields: app_marks.append((event, fields)),
        engine_status=SimpleNamespace(version="v", mode="dev", engine_dir="",
                                      ok=True, describe=lambda: "stub", problems=[]),
    )
    app_marks: list = []
    text = SHELL.build_diagnosis(app, {"checks": [{"id": "xelatex", "status": "fail"}]},
                                 "line\n")
    assert "v-test" in text and "sk-secret" not in text
    assert "（已隐藏）" in text
    path = SHELL.write_diagnosis(app, {}, "log")
    assert Path(path).is_file()
    assert app_marks and app_marks[-1][0] == "diagnosis_exported"


def test_connection_probe_and_defaults_copy(tmp_path):
    offline = SHELL.connection_probe({"key": ""})
    assert offline["ok"] is False
    assert "¥0.5" in offline["cost_notice"] and "¥0.5" in offline["confirm"]
    assert SHELL.connection_probe({"key": "k", "base": "https://api.example/v1",
                                   "model": "m"})["ok"] is True

    store = SS.SettingsStore()
    store.path = tmp_path / "settings.json"
    store.data["options"] = {"cleantemp_dry_run": True}
    copy = store.defaults_copy()
    copy["options"]["toc_text_only"] = False
    assert store.data["options"]["cleantemp_dry_run"] is True
    assert "toc_text_only" not in store.data["options"]
    assert "cleantemp_dry_run" not in copy["options"]


# ── 接线：pages.py 只委托，新模块持有实现 ────────────────────────────────
def test_pages_delegates_and_no_delete_flags():
    pages = (APP / "ui" / "pages.py").read_text(encoding="utf-8")
    assert "ASM.build_step_call(self, key)" in pages
    assert "ASM.run_step(self, key)" in pages
    assert "ASM.after_step(" in pages
    assert "SHELL.logs_card(self)" in pages
    assert "--delete" not in pages and "--empty-trash" not in pages
    assert "--restore" not in pages
    signature = inspect.signature(EB.cleantemp_call)
    assert "mode" in signature.parameters and "restore" in signature.parameters
