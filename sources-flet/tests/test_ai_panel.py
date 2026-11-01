"""P6 批107 Phase2 测试：AI 协同两段式 / 直连核实（全程离线，零真实 API）。

覆盖任务书 R5：argv 拼装（普通/第一段/第二段/直连，默认值不变）、暂存目录发现、
上传预览字段白名单、总开关关闭 → 零 AI 调用、预算拦截文案、密钥不进日志/argv；
唯一一个集成测试用 tools/mock_llm_server.py（127.0.0.1，mock key），不联网。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import flet as ft
import pytest

from services import ai_prefs as AI
from services import engine_bridge as EB
from services import settings_store as SS
from ui import ai_panel as AIP
from ui.tokens import Tokens

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "Engine"
MOCK = ROOT / "tools" / "mock_llm_server.py"


# ── 测试替身：不碰真窗口，只让面板能取值 ────────────────────────────────
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

    def update_options(self, **kwargs) -> None:
        self.data.setdefault("options", {}).update(kwargs)

    def save(self) -> None:
        self.saved = True


class FakeApp:
    def __init__(self, store) -> None:
        self.store = store
        self.running = False
        self.toasts: list[str] = []
        self.marks: list[tuple] = []
        self.persisted = 0
        self.rebuilt = 0
        self.opened: list[str] = []
        self.gone: list[str] = []

    def toast(self, message) -> None:
        self.toasts.append(str(message))

    def mark(self, event, **fields) -> None:
        self.marks.append((event, fields))

    def persist(self) -> None:
        self.persisted += 1

    def rebuild_shell(self) -> None:
        self.rebuilt += 1

    def browse_field(self, *args, **kwargs) -> None:
        return None

    def open_path(self, value) -> None:
        self.opened.append(str(value))

    def goto(self, key) -> None:
        self.gone.append(str(key))


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
    for i, item in enumerate(call.argv):
        if str(item).endswith(call.entry):
            return [str(v) for v in call.argv[i + 1:]]
    return [str(v) for v in call.argv]


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
    instance = SS.SettingsStore()
    instance.load()
    return instance


@pytest.fixture
def enabled(store) -> SS.SettingsStore:
    AI.save(store, {"enabled": True})
    return store


# ── ① 默认值不变 + 总开关关闭 → 零 AI 调用 ─────────────────────────────
def test_plain_defaults_unchanged_and_master_off_blocks_ai(store):
    page = FakePage(store)
    panel = AIP.AiPanel(page)
    kwargs = dict(source=r"C:\m\main.tex", out=r"C:\out", author="张三")
    call = panel.build_call(**kwargs)
    expected = EB.convert_call(**kwargs)
    assert _args(call) == _args(expected)
    for flag in ("--coop-taskbook-only", "--coop-replies", "--llm-base",
                 "--no-ai-merge", "--ai-budget-tokens", "--verify-refs"):
        assert flag not in _args(call)
    assert panel.api_env() == {}

    panel._coop = True
    with pytest.raises(ValueError, match="总开关"):
        panel.build_call(**kwargs)

    view = AIP.AiPanel(FakePage(store)).build()
    checkboxes = [c for c in _walk(view) if isinstance(c, ft.Checkbox)]
    assert checkboxes == [], "总开关关着时不应出现任何 AI 勾选项"


def test_master_on_shows_entries(enabled):
    view = AIP.AiPanel(FakePage(enabled)).build()
    assert any(isinstance(c, ft.Checkbox) for c in _walk(view))


# ── ② 第一段 / 第二段 argv ─────────────────────────────────────────────
def test_phase1_argv_zero_upload(enabled):
    panel = AIP.AiPanel(FakePage(enabled))
    panel._coop = True
    call = panel.build_call(source=r"C:\m\main.tex", out=r"C:\out", author="张三")
    args = _args(call)
    assert "--verify-refs" in args
    assert "--coop-taskbook-only" in args and "--no-ai-merge" in args
    assert "--coop-fulltext" not in args          # 默认不上传正文
    assert "--coop-stage" not in args             # 留空 = 自动发现
    assert not [a for a in args if a.startswith("--llm-")]

    panel._fulltext, panel._scope = True, AI.FULLTEXT_FULL
    stage = r"C:\out\Zhang_待核"
    panel._stage_field = _Field(stage)
    panel._stage_user = True
    args = _args(panel.build_call(source=r"C:\m\main.tex", out=r"C:\out"))
    assert _flag_value(args, "--coop-fulltext") == "full"
    assert _flag_value(args, "--coop-stage") == stage


def test_phase2_argv_replies_and_apply(enabled):
    panel = AIP.AiPanel(FakePage(enabled))
    panel._coop = True
    stage = r"C:\out\Zhang_待核"
    panel._stage_field = _Field(stage)
    panel._stage_user = True
    panel._reply_fields = [_Field(r"C:\r\一.jsonl"), _Field(r"C:\r\二.jsonl")]
    panel._apply = True
    args = _args(panel.build_call(source=r"C:\m\main.tex", out=r"C:\out"))
    assert args.count("--coop-replies") == 2
    assert _flag_value(args, "--coop-replies") == r"C:\r\一.jsonl"
    assert "--coop-apply" in args
    assert "--coop-taskbook-only" not in args
    assert _flag_value(args, "--coop-stage") == stage


def test_phase2_requires_stage_and_replies():
    page = FakePage(FakeStore())
    panel = AIP.AiPanel(page)
    panel._coop = True
    panel._reply_fields = [_Field(r"C:\r\一.jsonl")]
    with pytest.raises(ValueError, match="暂存目录"):
        panel.build_call(source="s.tex", out="out")
    assert AI.MODE_PH2 == AI.plan_mode(direct=False, coop=True,
                                       replies=[r"C:\r\一.jsonl"], stage="x")


# ── ③ 直连核实：预算/协议/密钥边界 ────────────────────────────────────
def test_direct_argv_budget_and_key_never_in_argv(enabled):
    key = "sk-mock-key-not-printed"
    AI.save(enabled, {"key": key, "base": "http://127.0.0.1:9/v1", "model": "mock-model",
                      "protocol": "chat", "article_budget_tokens": 30000,
                      "batch_budget_tokens": 1000000, "verify_max": 5})
    panel = AIP.AiPanel(FakePage(enabled))
    panel._direct = True
    call = panel.build_call(source=r"C:\m\main.tex", out=r"C:\out")
    args = _args(call)
    assert "--verify-refs" in args
    assert _flag_value(args, "--llm-base") == "http://127.0.0.1:9/v1"
    assert _flag_value(args, "--llm-model") == "mock-model"
    assert _flag_value(args, "--llm-protocol") == "chat"
    assert _flag_value(args, "--ai-budget-tokens") == "30000"
    assert _flag_value(args, "--ai-batch-budget-tokens") == "1000000"
    assert _flag_value(args, "--verify-max") == "5"
    assert "--no-ai-merge" in args
    assert key not in " ".join(call.argv)
    assert "Authorization" not in call.display

    env = panel.api_env()
    assert env["key"] == key and env["base"] == "http://127.0.0.1:9/v1"
    assert env["model"] == "mock-model" and env["thinking"] == "0"

    panel._no_ai_merge = False
    assert "--no-ai-merge" not in _args(panel.build_call(source="s.tex", out="out"))


def test_direct_requires_key(enabled):
    panel = AIP.AiPanel(FakePage(enabled))
    panel._direct = True
    with pytest.raises(ValueError, match="API Key"):
        panel.build_call(source="s.tex", out="out")


def test_two_channels_are_exclusive(enabled):
    panel = AIP.AiPanel(FakePage(enabled))
    panel._direct = panel._coop = True
    with pytest.raises(ValueError, match="只能二选一"):
        panel.build_call(source="s.tex", out="out")


# ── ④ 上传预览：白名单 + 禁传未刊稿 ───────────────────────────────────
def test_upload_preview_whitelist_and_forbidden_fields():
    secret = "未刊稿正文-秘密-不得发送"
    values = {"source_name": "main.tex", "refs_count": 3, "bib_keys": "A;B",
              "taskbook_brief": "核实规范", "budget_tokens": 30000,
              "body": secret, "fulltext": secret, "abstract": secret, "content": secret}
    rows = AI.upload_preview(values)
    assert [label for label, _ in rows] == [label for _, label in AI.UPLOAD_FIELDS]
    assert all(secret not in str(value) for _, value in rows)
    assert AI.forbidden_upload(values) == "body"
    assert AI.forbidden_upload({"source_name": "main.tex"}) == ""
    text = AI.preview_text(values)
    assert secret not in text
    assert "不发送" in AI.UPLOAD_ALWAYS
    assert AI.UPLOAD_ALWAYS not in text          # 预览只列白名单元数据行


# ── ⑤ 预算硬停文案 ────────────────────────────────────────────────────
def test_budget_notice_maps_to_clear_message():
    assert AI.budget_notice(["普通日志"]) == ""
    notice = AI.budget_notice(["预算闸拦下本次直接 API 调用：超出单篇预算"])
    assert notice == AI.BUDGET_NOTICE
    assert "预算不足，未发起请求" in notice


# ── ⑥ 暂存目录发现 + 状态统计 ─────────────────────────────────────────
def _write_state(stage_dir: Path, **extra) -> dict:
    stage_dir.mkdir(parents=True, exist_ok=True)
    state = {"schema": 1, "phase": "ph1", "params": {"stem": "Zhang"},
             "source": {"path": r"C:\m\main.tex"}, "taskbook": "任务书.md"}
    state.update(extra)
    (stage_dir / AI.STATE_NAME).write_text(json.dumps(state, ensure_ascii=False),
                                           encoding="utf-8")
    return state


def test_stage_discovery_matches_source(tmp_path):
    out = tmp_path / "out"
    stage = out / "Zhang_待核"
    _write_state(stage, params={"stem": "Zhang", "out_root": str(out)},
                 source={"path": r"C:\m\main.tex"})
    found = AI.discover_stage(str(out), source=r"C:\m\main.tex")
    assert found is not None and found.stem == "Zhang"
    assert found.phase == "ph1" and found.status_text == AI.STATUS_PH1
    assert found.summary["available"] is False
    assert AI.discover_stage(str(out), source=r"C:\other\main.tex") is None
    assert AI.discover_stage("", source=r"C:\m\main.tex") is None
    assert AI.stage_dirs(str(out)) == [str(stage)]


def test_stats_summary_rounds():
    state = {"phase": "ph2", "stats": {"bib_total": 5, "bib_replied": 3, "bib_pending": 2,
                                       "low_total": 4, "low_replied": 2, "low_applied": 1,
                                       "low_pending_manual": 1}}
    summary = AI.stats_summary(state)
    assert summary["available"] is True
    assert "文献回填 3/5" in summary["text"]
    assert "低置信回复 2 条" in summary["text"]
    assert "已应用 1 条" in summary["text"]


# ── ⑦ 设置段：向后兼容 + 默认关 ───────────────────────────────────────
def test_settings_ai_defaults_backward_compatible(tmp_path, monkeypatch):
    data, exists = SS.SettingsStore(tmp_path / "new.json").load()
    assert exists is False
    assert data["ai"] == AI.defaults()
    assert data["ai"]["enabled"] is False
    assert data == SS._defaults()

    old = tmp_path / "old.json"
    old.write_text(json.dumps({"flet": {"version": 2, "theme": "dark"}}), encoding="utf-8")
    legacy, _ = SS.SettingsStore(old).load()
    assert legacy["theme"] == "dark"
    assert legacy["ai"] == AI.defaults()


# ── ⑧ 集成（唯一一条）：mock LLM + 真引擎子进程，预算硬停 → 零请求 ────
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Mock:
    def __init__(self) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}/v1"
        self.proc = None

    def __enter__(self):
        env = dict(os.environ)
        for key in [k for k in env if k.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES"))]:
            env.pop(key, None)
        # CI 上曾出现子进程 15s 内没响应就报「没起来」（本地全绿）：失败时把 stderr 尾带出来，
        # 并换端口重试一次；仍失败才报错，避免把「环境慢/端口被占」误当成用例失败原因不明。
        last_err = ""
        for _attempt in range(2):
            self.port = _free_port()
            self.base = f"http://127.0.0.1:{self.port}/v1"
            err = tempfile.TemporaryFile()
            self.proc = subprocess.Popen(
                [sys.executable, str(MOCK), "--port", str(self.port), "--mode", "normal"],
                stdout=subprocess.DEVNULL, stderr=err, env=env)
            deadline = time.time() + 40
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/__stats", timeout=1):
                        return self
                except OSError:
                    time.sleep(0.1)
            err.seek(0)
            last_err = err.read().decode("utf-8", "replace")[-800:]
            self.__exit__(None, None, None)
        raise RuntimeError(f"mock LLM 没起来（stderr 尾：{last_err}）")

    def stats(self) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/__stats", timeout=5) as rsp:
            return json.loads(rsp.read().decode("utf-8"))

    def __exit__(self, *exc):
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                self.proc.kill()
        return False


_TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文引用\parencite{Quine1970}。
\end{document}
"""

_BIB = """@article{Quine1970,
  author = {Quine, W. V. O.},
  title = {Philosophy of Logic},
  journal = {Mind},
  year = {1970},
}
"""


def test_direct_budget_hardstop_with_mock_zero_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("P2_SETTINGS", str(tmp_path / "settings.json"))
    store = SS.SettingsStore()
    store.load()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src = src_dir / "main.tex"
    src.write_text(_TEX, encoding="utf-8")
    bib = src_dir / "refs.bib"
    bib.write_text(_BIB, encoding="utf-8")
    out = tmp_path / "out"

    with Mock() as mock:
        AI.save(store, {"enabled": True, "key": "mock-key-not-printed",
                        "base": mock.base, "model": "mock-model", "protocol": "chat",
                        "thinking": False, "article_budget_tokens": 1,
                        "batch_budget_tokens": 100000})
        page = FakePage(store)
        page.fields.update(single_source=str(src), single_out=str(out))
        panel = AIP.AiPanel(page)
        panel._direct = True
        call = panel.build_call(source=str(src), out=str(out), author="张三", lang="zh",
                                bib=str(bib), no_bibdb=True, extra="--no-original")
        assert "mock-key-not-printed" not in " ".join(call.argv)

        lines: list[str] = []
        code = asyncio.run(EB.run_call(call, on_line=lines.append,
                                       api=panel.api_env(), cwd=str(ENGINE)))
        stats = mock.stats()

    joined = "\n".join(lines)
    assert code == 2, joined[-2000:]
    assert stats["n_requests"] == 0, "预算硬停必须在发出任何请求之前"
    assert "预算" in joined
    assert "mock-key-not-printed" not in joined
    assert "Authorization" not in joined
    if out.exists():
        assert not list(out.rglob("*AI*")), "预算硬停不应产出核实报告"


# ── ⑨ 设置页 Key 输入：Flet 1.0.5 的 on_change 传 Event（回归 verifier-3 FAIL） ──
try:  # Flet 1.0.5 的真实事件类型；换布局时退回等价的鸭子类型，断言同样成立
    from flet.controls.control_event import Event as FletEvent
except Exception:  # noqa: BLE001 - 只影响事件构造方式，不影响被测逻辑
    FletEvent = None


def _event(value, *, attached: bool = True):
    """构造 on_change 实参：attached=True 带控件（正常 Flet），False 只有 data。"""
    control = _Field(value) if attached else None
    if FletEvent is not None:
        return FletEvent(name="change", data=value, control=control)
    import types
    return types.SimpleNamespace(name="change", data=value, control=control)


class _PersistingApp(FakeApp):
    """persist() 真落盘：模拟「立即保存」/切协议/勾总开关走的保存路径。"""

    def persist(self) -> None:
        super().persist()
        self.store.save()


def _settings(store):
    page = FakePage(store)
    page.app = _PersistingApp(store)
    settings = AIP.AiSettings(page)
    settings.build()
    return page, settings


def test_key_typing_event_writes_exact_value_and_mask(store):
    page, settings = _settings(store)
    settings._on_key_typing(_event("sk-test-123456"))
    assert page.app.store.get("ai.key") == "sk-test-123456"
    assert "Event(" not in str(page.app.store.get("ai.key"))
    label = settings._key_label.value
    assert label == AI.mask_key("sk-test-123456")
    assert label.endswith("3456）") and "sk-test-123456" not in label
    # 逐字符键入也只是覆盖成当前控件值，不会累积 Event 文本
    for prefix in ("s", "sk", "sk-test-123456"):
        settings._on_key_typing(_event(prefix))
        assert page.app.store.get("ai.key") == prefix


def test_key_typing_falls_back_to_data_and_legacy_str(store):
    page, settings = _settings(store)
    settings._on_key_typing(_event("sk-no-control-99", attached=False))  # verifier 探针构造
    assert page.app.store.get("ai.key") == "sk-no-control-99"
    settings._on_key_typing("  sk-legacy-8888  ")  # 老测试/脚本直接传 str
    assert page.app.store.get("ai.key") == "sk-legacy-8888"


def test_key_never_persists_event_text(store, tmp_path):
    page, settings = _settings(store)
    settings._on_key_typing(_event("sk-t"))
    settings._on_key_typing(_event("sk-test-123456"))
    page.app.persist()                      # 「立即保存」→ save_settings → persist
    settings._set_protocol("responses")     # 切协议 → _save → persist
    settings._on_key_commit(_event("sk-test-123456"))   # 失焦提交
    raw = store.path.read_text(encoding="utf-8")
    assert "Event(" not in raw and "change" not in raw
    data = json.loads(raw)
    assert data["flet"]["ai"]["key"] == "sk-test-123456"
    blob = json.dumps(data, ensure_ascii=False)
    assert "Event(" not in blob and "data='sk-" not in blob


def test_key_commit_strips_whitespace_and_marks(store):
    page, settings = _settings(store)
    settings._on_key_typing(_event("  sk-pad-7777  "))
    assert page.app.store.get("ai.key") == "sk-pad-7777"
    settings._on_key_commit(_event("  sk-pad-7777  "))
    assert page.app.store.get("ai").get("key") == "sk-pad-7777"
    assert page.app.marks[-1] == ("ai_key_set", {"key_set": True})
    settings._on_key_typing(_event("   "))
    settings._on_key_commit(_event(""))
    assert page.app.marks[-1] == ("ai_key_set", {"key_set": False})


def test_fill_stage_updates_field_without_mount(enabled):
    panel = AIP.AiPanel(FakePage(enabled))
    panel.build()
    stage = type("S", (), {"stage_dir": r"C:\out\Zhang_待核"})()
    panel._fill_stage(stage, user=False)
    assert panel._stage_field.value == r"C:\out\Zhang_待核"

