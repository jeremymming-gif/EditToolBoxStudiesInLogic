"""批 116 SINGLE-FN：单篇稿件页功能补强（F1 P0-1 及结果动作）。

锁五组契约（全部离线：不建窗口、不跑真引擎）：
1. `EB.convert_call(dry_run=...)` 只按需追加 `--dry-run`（引擎 convert_stage.py:1291）。
2. 第 5 张处理选项「只预演（不落盘）」→ store `options.convert_dry_run` → argv。
3. 预演 rc=0：文案「预演完成（只读：未建产物目录、未写产物）」、ok、artifact 清空、
   不写最近记录、目录动作行隐藏。
4. 真实转换成功：结果卡下方「打开产物目录」行出现，点击打开 artifact 的 dirname；
   没有 artifact 时整行隐藏（切步骤也隐藏）。
5. exit1（proofcheck / convert）一律 warn，proofcheck 文案指向报告。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

from services import engine_bridge as EB
from ui import pages as P

from test_b114_polish import _FakeApp, _walk

DRY_TEXT = "预演完成（只读：未建产物目录、未写产物）"


class _App(_FakeApp):
    """在 b114 的构建型假壳上补运行钩子（_run 需要，不启 GUI）。"""

    def __init__(self) -> None:
        super().__init__()
        self.last_line = ""
        self.cancel_requested = False
        self.notes_repo = ""
        self.begun: list = []
        self.finished: list = []
        self.recorded: list[str] = []
        self.proc = None

    # b114 的 set_option 是空实现；这里落到 store，才能验「tile → 选项」。
    def set_option(self, key: str, value) -> None:
        self.store.set(f"options.{key}", value)

    def begin_run(self, call) -> None:
        self.running = True
        self.cancel_requested = False
        self.last_line = ""
        self.begun.append(call)

    def finish_run(self, call, code) -> None:
        self.running = False
        self.finished.append((call, code))

    def track_proc(self, proc) -> None:
        self.proc = proc

    def record_line(self, line) -> None:
        self.recorded.append(line)

    def copy_text(self, value) -> None:  # ResultBanner 的 on_copy 回调需要
        pass

    def open_path(self, value) -> None:
        pass


def _make_page(tmp_path, app=None):
    page = P.SinglePage(app or _App())
    root = page.build()
    source = tmp_path / "src.docx"
    source.write_bytes(b"PK\x03\x04stub")
    page.set_field("single_source", str(source))
    page.set_field("single_out", str(tmp_path / "out"))
    return page, root


def _install_run(monkeypatch, page, code, last_line=""):
    """桩掉 EB.run_call：记录调用、按需设置最后一行（artifact 探测用）。"""
    calls: list[EB.EngineCall] = []

    async def fake_run_call(call, *, on_line=None, on_proc=None, api=None,
                            notes="", stdin_text=None, **kwargs):
        calls.append(call)
        page.app.last_line = last_line
        return code

    monkeypatch.setattr(EB, "run_call", fake_run_call)
    return calls


def _dry_run_tile(page):
    rows = [ctl for ctl in page._option_tiles({}).controls if isinstance(ctl, ft.Row)]
    for row in rows:
        for tile in row.controls:
            for ctl in _walk(tile):
                if isinstance(ctl, ft.Text) and ctl.value == "只预演（不落盘）":
                    checkbox = next(c for c in _walk(tile) if isinstance(c, ft.Checkbox))
                    return checkbox
    raise AssertionError("没有找到「只预演（不落盘）」选项卡")


# ── 1. argv 拼装 ─────────────────────────────────────────────────────────
def test_convert_call_dry_run_flag_only_when_requested():
    plain = EB.convert_call(source="/tmp/a.docx", out="/tmp/out")
    assert plain.entry == "convert.py"
    assert "--dry-run" not in plain.argv
    dry = EB.convert_call(source="/tmp/a.docx", out="/tmp/out", dry_run=True)
    assert dry.argv.count("--dry-run") == 1
    # 与 --compile 共存：都由引擎按顺序消费（引擎在生成前返回，不会真编译）。
    both = EB.convert_call(source="/tmp/a.docx", out="/tmp/out",
                           compile_pdf=True, dry_run=True)
    assert "--compile" in both.argv and "--dry-run" in both.argv


# ── 2. 选项卡 → store → argv ─────────────────────────────────────────────
def test_dry_run_tile_wired_to_store_and_build_call(tmp_path):
    app = _App()
    page, _root = _make_page(tmp_path, app)
    checkbox = _dry_run_tile(page)
    assert checkbox.value is False, "预演默认关"

    checkbox.value = True
    checkbox.on_change(SimpleNamespace(control=checkbox))
    assert app.store.get("options.convert_dry_run") is True

    call = page._build_call()
    assert "--dry-run" in call.argv


def test_dry_run_default_off_keeps_argv_unchanged(tmp_path):
    page, _root = _make_page(tmp_path)
    call = page._build_call()
    assert "--dry-run" not in call.argv


# ── 3. 预演结果语义（只读）───────────────────────────────────────────────
def test_dry_run_result_is_readonly_and_clears_artifact(tmp_path, monkeypatch):
    page, _root = _make_page(tmp_path)
    page.app.store.set("options.convert_dry_run", True)
    call = page._build_call()
    assert "--dry-run" in call.argv

    # 即使磁盘上真有一个看起来像产物的路径，预演也不得把它当产物。
    artifact = tmp_path / "out" / "Jingru_Kou.tex"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("stub", encoding="utf-8")
    _install_run(monkeypatch, page, 0, str(artifact))

    asyncio.run(page._run(call))

    assert page._result.title.value == DRY_TEXT
    assert page._result.icon.color == page.t.palette.success  # level=ok
    assert page._result.detail.value == ""
    assert page._result.open_btn.visible is False
    assert page._artifact_row.visible is False
    assert page.app.store.get("recent.single_out") is None


def test_dry_run_metadata_preview_keeps_preview_text(tmp_path, monkeypatch):
    """预演 + 元数据预览同时跑：预览文案优先，不出现两套「只读」文案打架。"""
    page, _root = _make_page(tmp_path)
    page.app.store.set("options.convert_dry_run", True)
    preview = tmp_path / "preview.json"
    preview.write_text('{"title": "测试稿"}', encoding="utf-8")
    seen: list = []

    class _Panel:
        def show_preview(self, payload):
            seen.append(payload)

    # 先拼 argv（此时不挂人工元数据面板），再挂预览面板供 _show_metadata_preview。
    call = page._build_call(preview_path=str(preview))
    assert "--dry-run" in call.argv and "--metadata-preview-out" in call.argv
    page._meta_panel = _Panel()
    _install_run(monkeypatch, page, 0)

    asyncio.run(page._run(call, None, str(preview)))

    assert seen == [{"title": "测试稿"}]
    assert page._result.title.value == "元数据预览完成（只读：未建产物目录、未写产物）"
    assert page._result.icon.color == page.t.palette.text_muted  # level=info
    assert page._result.open_btn.visible is False


# ── 4. 产物动作行 ────────────────────────────────────────────────────────
def test_artifact_row_hidden_without_artifact(tmp_path):
    page, _root = _make_page(tmp_path)
    assert page._artifact_row.visible is False
    button = page._artifact_row.controls[0]
    assert isinstance(button, ft.OutlinedButton)
    assert str(button.content) == "打开产物目录"


def test_successful_convert_shows_dir_action_and_opens_dirname(tmp_path, monkeypatch):
    page, _root = _make_page(tmp_path)
    artifact = tmp_path / "out" / "Jingru_Kou.tex"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("stub", encoding="utf-8")
    _install_run(monkeypatch, page, 0, str(artifact))

    asyncio.run(page._run(page._build_call()))

    assert page._result.detail.value == str(artifact)
    assert page._result.open_btn.visible is True
    assert page._artifact_row.visible is True
    assert page.app.store.get("recent.single_out") == str(artifact)

    opened: list[str] = []

    def fake_open(path):
        opened.append(path)
        return True, ""

    monkeypatch.setattr(EB, "open_path", fake_open)
    page._on_open_artifact_dir()
    assert opened == [str(artifact.parent)]


def test_dir_action_with_directory_artifact_opens_its_parent(tmp_path, monkeypatch):
    """真实引擎的末行是产物目录（out/<作者>），按契约打开它的 dirname。"""
    page, _root = _make_page(tmp_path)
    product_dir = tmp_path / "out" / "Jingru_Kou"
    product_dir.mkdir(parents=True)
    _install_run(monkeypatch, page, 0, str(product_dir))
    asyncio.run(page._run(page._build_call()))

    assert page._result.detail.value == str(product_dir)
    assert page._artifact_row.visible is True
    opened: list[str] = []
    monkeypatch.setattr(EB, "open_path", lambda path: (opened.append(path) or (True, "")))
    page._on_open_artifact_dir()
    assert opened == [str(product_dir.parent)]


def test_artifact_row_hides_again_on_flow_switch(tmp_path, monkeypatch):
    page, _root = _make_page(tmp_path)
    artifact = tmp_path / "out" / "A.tex"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("stub", encoding="utf-8")
    _install_run(monkeypatch, page, 0, str(artifact))
    asyncio.run(page._run(page._build_call()))
    assert page._artifact_row.visible is True

    page.activate("proofcheck")
    assert page._artifact_row.visible is False


def test_open_dir_without_artifact_is_noop(tmp_path, monkeypatch):
    page, _root = _make_page(tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(EB, "open_path", lambda path: (opened.append(path) or (True, "")))
    page._on_open_artifact_dir()
    assert opened == []


# ── 5. exit1 = warn（黄），proofcheck 文案指向报告 ────────────────────────
def test_exit1_is_warn_for_proofcheck_and_convert():
    assert EB.result_level("proofcheck.py", 1) == "warn"
    assert EB.result_level("convert.py", 1) == "warn"
    text = EB.result_text("proofcheck.py", 1)
    assert "有需要核对的条目" in text
    assert "报告" in text
    assert "命中" in text  # 既有 test_engine_bridge 的锚字，保留


def test_convert_exit1_banner_is_warn(tmp_path, monkeypatch):
    page, _root = _make_page(tmp_path)
    _install_run(monkeypatch, page, 1)
    asyncio.run(page._run(page._build_call()))
    assert page._result.icon.color == page.t.palette.warn
    assert "退出码 1" in page._result.title.value
    assert page._artifact_row.visible is False
