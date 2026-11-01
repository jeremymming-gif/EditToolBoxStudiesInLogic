"""批 109 Wave 0 安全回归（S1–S5）。

S1 并回覆盖正文 tex / S2 可绕到永久删除 / S3 入藏口径缺失 + 过期预览 /
S4 「仅生成 tex」默认 / S5 设置保存失败静默。
全部在临时目录与假 page 上跑；不启动 GUI、不碰真实 bib_db。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from services import engine_bridge as EB
from services import settings_store as SS
from ui import bib_write as BW

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """桩 Engine 目录：让 base_cmd() 走 dev 模式（与 test_engine_bridge 同款）。"""
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


def _string_constants(path: Path) -> list[str]:
    """源码里的字符串常量（注释不进 AST；用来静态证明某参数根本不出现）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


# ── S1：不再有覆盖输入 tex 的调用路径 ────────────────────────────────
def test_s1_checkbib_has_no_overwrite_output_parameter():
    assert "merge_into" not in inspect.signature(EB.checkbib_call).parameters
    bridge = _string_constants(APP / "services" / "engine_bridge.py")
    assert not any("--merge-into" in text for text in bridge)
    pages = _string_constants(APP / "ui" / "pages.py")
    assert not any("--merge-into" in text or "checkbib_merge" in text for text in pages)


def test_s1_checkbib_tex_is_read_only_input(engine):
    args = args_of(EB.checkbib_call(returned="r.bib", tex="paper.tex", out="o"))
    assert args == ["--returned", "r.bib", "--tex", "paper.tex", "--out", "o"]
    # 正文 tex 只出现在 --tex（只读分析输入）之后，绝不作为写入目标。
    assert args.count("paper.tex") == 1 and args[args.index("--tex") + 1] == "paper.tex"


# ── S2：清理固定 --trash，无永久删除参数（D-49 允许 --mode / --restore）──
def test_s2_cleantemp_fixed_trash_no_permanent_delete(engine):
    params = set(inspect.signature(EB.cleantemp_call).parameters)
    assert {"folder", "mode", "dry_run", "restore"} <= params
    calls = [
        EB.cleantemp_call(folder="f"),
        EB.cleantemp_call(folder="f", dry_run=True),
        EB.cleantemp_call(folder="f", mode="aux"),
        EB.cleantemp_call(folder="f", mode="delivery"),
        EB.cleantemp_call(folder="f", mode="workspace", dry_run=True),
        EB.cleantemp_call(folder="f", restore="t"),
    ]
    assert args_of(calls[0]) == ["--folder", "f", "--trash"]
    assert args_of(calls[1]) == ["--folder", "f", "--dry-run", "--trash"]
    assert args_of(calls[3]) == ["--folder", "f", "--mode", "delivery"]
    assert args_of(calls[4]) == ["--folder", "f", "--mode", "workspace", "--dry-run"]
    assert args_of(calls[5]) == ["--folder", "f", "--restore", "t"]
    for call in calls:
        assert "--delete" not in call.argv and "--empty-trash" not in call.argv


def test_s2_shell_has_no_permanent_delete_controls():
    all_app = "\n".join("\n".join(_string_constants(path)) for path in APP.rglob("*.py"))
    for flag in ("--delete", "--empty-trash"):
        assert flag not in all_app
    pages = "\n".join(_string_constants(APP / "ui" / "pages.py"))
    assert "--restore" not in pages
    assert "--mode" not in pages
    assert "cleantemp_trash" not in SS._defaults()["options"]
    assert "checkbib_merge" not in SS._defaults()["options"]


# ── 假 page：BibImportView 只用到这些成员 ────────────────────────────
class _Store:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key: str, default=None):
        return self.data.get(key, default)


class _App:
    notes_repo = ""

    def __init__(self) -> None:
        self.store = _Store()
        self.toasts: list[str] = []

    def toast(self, message: str) -> None:
        self.toasts.append(message)


class _Page:
    def __init__(self) -> None:
        self.app = _App()
        self.stats_data = None
        self.events: list[tuple[str, dict]] = []
        self.renders = 0
        self.page = self

    @property
    def t(self):
        return None

    def mark(self, event: str, **fields) -> None:
        self.events.append((event, fields))

    def _render(self) -> None:
        self.renders += 1


def _import_view() -> tuple[BW.BibImportView, _Page]:
    page = _Page()
    view = BW.BibImportView(page)
    view.file_path = "a.bib"
    return view, page


# ── S3：入藏口径 --verified/--issue/--slug + 预览过期闸 ──────────────
def test_s3_import_argv_carries_issue_slug_verified(engine):
    view, _ = _import_view()
    view.issue = "2026年第5期(90)"
    view.slug = "01 Meimei_Han"
    args = args_of(view._build_call(dry_run=True, expect_sha=""))
    assert args[:2] == ["import-bib", "--json"]
    assert args[args.index("--bib") + 1] == "a.bib"
    assert args[args.index("--issue") + 1] == "2026年第5期(90)"
    assert args[args.index("--slug") + 1] == "01 Meimei_Han"
    assert "--verified" in args and "--unverified" not in args


def test_s3_unverified_override(engine):
    view, _ = _import_view()
    view.verified = False
    args = args_of(view._build_call(dry_run=False, expect_sha="a" * 64))
    assert "--unverified" in args and "--verified" not in args


def test_s3_stale_preview_blocks_confirm_and_write(engine, monkeypatch):
    view, page = _import_view()
    calls: list = []

    def fake_read(call, notes=""):
        calls.append(call)
        return {"ok": True, "code": 0, "json": {
            "db": "tmp/bibliography.jsonl", "db_existed": False,
            "db_sha256_before": "a" * 64, "report": {"entries": 1, "new": 1}}}
    monkeypatch.setattr(BW.EB, "read_json_command", fake_read)

    asyncio.run(view._preview())
    assert view.preview and view.expect_sha == "a" * 64
    assert not view._preview_stale()
    preview_calls = len(calls)

    # 任何输入变更（此例：取消「已核实」）→ 旧预览立即过期、sha 闸作废
    view._on_verified_change(SimpleNamespace(control=SimpleNamespace(value=False)))
    assert view.stale and view.expect_sha == ""
    assert view._preview_stale()

    confirms: list = []
    monkeypatch.setattr(BW.C, "confirm", lambda *a, **k: confirms.append(k))
    view._confirm_write()
    assert confirms == []                      # 不再弹「确认写入」

    asyncio.run(view._write())
    assert len(calls) == preview_calls          # 拒绝写入：没有发第二条引擎调用
    assert view.preview is not None             # 保留旧预览，但明确标过期
    assert "过期" in view.error
    assert any(event == "bib_import_stale_block" for event, _ in page.events)


def test_s3_stale_preview_disables_write_button():
    """控件树可构建，且过期后「确认写入」是灰的（无需启动 GUI）。"""
    import flet as ft

    from ui.tokens import Tokens

    class _RenderPage:
        def __init__(self) -> None:
            self.app = _App()
            self.stats_data = None
            self.events: list = []
            self.renders = 0
            self.page = self
            self.t = Tokens()

        def mark(self, event: str, **fields) -> None:
            self.events.append((event, fields))

        def _render(self) -> None:
            self.renders += 1

    def walk(control):
        yield control
        for child in list(getattr(control, "controls", []) or []):
            yield from walk(child)
        content = getattr(control, "content", None)
        if content is not None:
            yield from walk(content)

    page = _RenderPage()
    view = BW.BibImportView(page)
    view.file_path = "a.bib"
    assert view.build() is not None
    view.preview = {"db": "d.jsonl", "db_sha256_before": "a" * 64,
                    "report": {"entries": 1, "new": 1}, "changes": []}
    view.preview_signature = view._signature()
    view._on_issue_change(SimpleNamespace(control=SimpleNamespace(value="2026年第5期(90)")))
    buttons = [node for node in walk(view._preview_card())
               if isinstance(node, ft.FilledButton)]
    write = [b for b in buttons if "确认写入" in str(getattr(b, "content", ""))]
    assert write and all(b.disabled for b in write)


# ── S4：「仅生成 tex」默认 True；旧文件缺键走新默认、已存值不迁移 ────
def test_s4_toc_text_only_default_and_legacy(tmp_path, monkeypatch):
    assert SS._defaults()["options"]["toc_text_only"] is True
    settings = tmp_path / "settings.json"
    monkeypatch.setenv("P2_SETTINGS", str(settings))
    settings.write_text(json.dumps({"flet": {"version": 2, "options": {}}}), encoding="utf-8")
    data, _ = SS.SettingsStore().load()
    assert data["options"]["toc_text_only"] is True       # 缺键 → 新默认
    settings.write_text(json.dumps({"flet": {"version": 2,
                                             "options": {"toc_text_only": False}}}),
                        encoding="utf-8")
    data, _ = SS.SettingsStore().load()
    assert data["options"]["toc_text_only"] is False      # 已存值不迁移


# ── S5：设置保存失败要 toast；成功保持安静 ───────────────────────────
class _PersistSelf:
    def __init__(self, error: OSError | None = None) -> None:
        self._error = error
        self.store = SimpleNamespace(save=self._save)
        self.toasts: list[str] = []
        self.marks: list[tuple[str, dict]] = []

    def _save(self) -> None:
        if self._error is not None:
            raise self._error

    def toast(self, message: str) -> None:
        self.toasts.append(message)

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))


def test_s5_settings_save_failure_toasts():
    fake = _PersistSelf(OSError(28, "No space left on device"))
    main.App.persist(fake)
    assert fake.toasts and "设置保存失败" in fake.toasts[0]
    assert "No space" in fake.toasts[0] and "磁盘" in fake.toasts[0]
    assert any(event == "settings_save_error" for event, _ in fake.marks)


def test_s5_settings_save_success_stays_silent():
    fake = _PersistSelf()
    main.App.persist(fake)
    assert fake.toasts == [] and fake.marks == []
