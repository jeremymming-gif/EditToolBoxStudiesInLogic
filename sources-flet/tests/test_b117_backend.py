"""批 117 P1 后端锁：clean temp JSON 契约 / toc outputs 桥参 / 仅生成 tex 语义。

纯逻辑，不启动 GUI、不跑真引擎（真引擎已在批 117 手工验收）：
  1. `EB.cleantemp_call` 的三种档位与撤销都必须带 `--json`（预览/撤销靠它解析）；
  2. 永不出现 `--delete` / `--empty-trash`（壳不暴露永久删除）；
  3. `EB.toc_call` 的 `text_only` 按布尔语义处理（run_all/单步共用同一构建函数）；
  4. `assembly._toc_text_only` 只读 store：缺键 False、已有值原样尊重、绝不写回；
  5. `build_step_call(...,"toc")` 把布尔透传给 `toc_call`。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import engine_bridge as EB
from ui import assembly as ASM


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """引擎目录用桩（真实 Engine 不参与 argv 断言）。"""
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


# ── T1：cleantemp bridge 必须带 --json ───────────────────────────────────
def test_cleantemp_aux_default_has_json(engine):
    args = args_of(EB.cleantemp_call(folder="f"))
    assert args == ["--folder", "f", "--trash", "--json"]


def test_cleantemp_dry_run_preview_has_json(engine):
    args = args_of(EB.cleantemp_call(folder="f", dry_run=True))
    assert args == ["--folder", "f", "--dry-run", "--trash", "--json"]
    # 预览失败正是「先预演」死锁的根因：没有 --json 时 read_json_command 必 ok=False。
    assert "--json" in args


def test_cleantemp_delivery_workspace_and_restore_have_json(engine):
    delivery = args_of(EB.cleantemp_call(folder="f", mode="delivery"))
    assert delivery == ["--folder", "f", "--mode", "delivery", "--json"]
    workspace = args_of(EB.cleantemp_call(folder="f", mode="workspace", dry_run=True))
    assert workspace == ["--folder", "f", "--mode", "workspace", "--dry-run", "--json"]
    restore = args_of(EB.cleantemp_call(folder="f", restore="/f/.toolbox-trash/x"))
    assert restore == ["--folder", "f", "--restore", "/f/.toolbox-trash/x", "--json"]


def test_cleantemp_never_exposes_permanent_delete(engine):
    for call in (
        EB.cleantemp_call(folder="f"),
        EB.cleantemp_call(folder="f", dry_run=True),
        EB.cleantemp_call(folder="f", mode="delivery"),
        EB.cleantemp_call(folder="f", mode="workspace", dry_run=True),
        EB.cleantemp_call(folder="f", restore="t"),
    ):
        assert "--delete" not in call.argv
        assert "--empty-trash" not in call.argv


# ── T2：toc 桥参（text_only 布尔语义）────────────────────────────────────
def test_toc_call_text_only_is_boolean(engine):
    place = args_of(EB.toc_call(report="r.txt", text_only=0))
    assert "--place" in place and "--compile" in place
    text_only = args_of(EB.toc_call(report="r.txt", text_only="1"))
    assert "--place" not in text_only and "--compile" not in text_only
    assert "--result-json" in args_of(EB.toc_call(report="r.txt", text_only=True,
                                                  result_json="目录结果.json"))


# ── T3：仅生成 tex 只读 store，不写迁移标记 ──────────────────────────────
class _Store:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.writes: list[tuple[str, object]] = []

    def get(self, key: str, default=None):
        current = self.data
        for part in str(key).split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set(self, key: str, value) -> None:
        self.writes.append((key, value))
        parts = str(key).split(".")
        current = self.data
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value


def _page(store: _Store) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(store=store))


def test_toc_text_only_missing_key_defaults_false_without_writing():
    store = _Store()
    page = _page(store)
    assert ASM._toc_text_only(page) is False
    assert store.writes == []
    assert store.get("options.toc_text_only") is None
    assert store.get("options.toc_text_only_migrated") is None


def test_toc_text_only_preserves_user_true():
    store = _Store({"options": {"toc_text_only": True}})
    assert ASM._toc_text_only(_page(store)) is True
    assert store.writes == []


def test_toc_text_only_preserves_user_false():
    store = _Store({"options": {"toc_text_only": False}})
    assert ASM._toc_text_only(_page(store)) is False
    assert store.writes == []


# ── build_step_call：toc 布尔透传（单步与 run_all 共用此函数）────────────
def test_build_step_call_toc_passes_bool(monkeypatch):
    seen: dict = {}

    def fake_toc_call(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(entry="toc.py", argv=[], label="生成目录")

    monkeypatch.setattr(ASM.EB, "toc_call", fake_toc_call)
    store = _Store({"recent": {}})
    page = SimpleNamespace(
        app=SimpleNamespace(store=store),
        field=lambda key: {"issue_dir": "/tmp/期", "toc_report": "/tmp/期/信息检查.txt"}.get(key, ""),
        set_field=lambda key, value: None,
    )
    ASM.build_step_call(page, "toc")
    assert seen["report"] == "/tmp/期/信息检查.txt"
    assert seen["text_only"] is False
    assert seen["result_json"].endswith("目录结果.json")

    store.data["options"] = {"toc_text_only": "yes"}
    ASM.build_step_call(page, "toc")
    assert seen["text_only"] is True
