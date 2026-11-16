"""F-04：GUI 选定语种/责编必须进入 argv（最小合成 argv 回归测试）。

不建窗口、不跑真实引擎/编译、不联网：只把 SinglePage._build_call 拼出的
EngineCall.argv 取出来断言，顺带把 argv 里的 --lang 值喂给引擎模板映射，
验证落点（en → SLEN.sty / EN_template.tex；zh → SLCN-MIX.sty / CN_template.tex）。

覆盖：
  1. 选 en  → argv 含 --lang en，映射英文模板；
  2. 选 zh  → argv 含 --lang zh，映射中文模板；
  3. 未选/重置 → argv 无 --lang（保持引擎自动检测）；
  4. 责编同类断言 + 旧 bug 回归（只填 field 不应产生 --lang/--editor）。

隐私：全用合成占位值，不含任何真实作者/题名/稿件全文/私人路径。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.engine_bridge as EB
from ui.pages import SinglePage
from ui.tokens import Tokens

# 引擎模板模块仅用于验证 argv --lang → 模板落点；相对本测试定位，不硬编码绝对路径。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENGINE_SLCN = _REPO_ROOT / "Engine" / "slcn"
if str(_ENGINE_SLCN) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SLCN))
import templates as T  # noqa: E402


class _Store:
    def __init__(self, data=None):
        self.data = data if data is not None else {}

    def get(self, key, default=None):
        node = self.data
        for part in str(key).split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key, value):
        parts = str(key).split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value


class _App:
    def __init__(self):
        self.store = _Store()
        self.page = SimpleNamespace(run_task=lambda *a, **k: None,
                                    update=lambda *a, **k: None)
        self.t = Tokens("light", 1.0, 96)
        self.marked = []
        self.engine_status = SimpleNamespace(ok=True)

    def mark(self, event, **fields):
        self.marked.append((event, fields))


@pytest.fixture(autouse=True)
def _fake_engine_head(monkeypatch):
    """base_cmd 需要一个 head；给合成 head，绝不触碰真实引擎路径/进程。"""
    monkeypatch.setattr(EB, "base_cmd", lambda entry: ["python-demo", entry])


def _page_with(lang=None, editor=None):
    app = _App()
    if lang is not None:
        app.store.set("form.convert_lang", lang)
    if editor is not None:
        app.store.set("form.convert_editor", editor)
    page = SinglePage(app)
    # 只登记两个必填字段的合成控件；field() 读 fields[key][0].value。
    page.fields["single_source"] = [SimpleNamespace(value="/synthetic/src.docx")]
    page.fields["single_out"] = [SimpleNamespace(value="/synthetic/out")]
    return page


def _flag_value(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


# ── 1. en → --lang en → 英文模板 ─────────────────────────────────────────
def test_en_lang_in_argv_and_maps_to_en_template():
    argv = _page_with(lang="en")._build_call().argv
    assert _flag_value(argv, "--lang") == "en"
    resolved = T.resolve(_flag_value(argv, "--lang"))
    assert Path(resolved.sty).name == "SLEN.sty"
    assert Path(resolved.skeleton).name == "EN_template.tex"


# ── 2. zh → --lang zh → 中文模板 ─────────────────────────────────────────
def test_zh_lang_in_argv_and_maps_to_zh_template():
    argv = _page_with(lang="zh")._build_call().argv
    assert _flag_value(argv, "--lang") == "zh"
    resolved = T.resolve(_flag_value(argv, "--lang"))
    assert Path(resolved.sty).name == "SLCN-MIX.sty"
    assert Path(resolved.skeleton).name == "CN_template.tex"


# ── 3. 未选/重置 → 无 --lang（自动检测）───────────────────────────────────
def test_no_lang_flag_when_unset_or_reset():
    assert "--lang" not in _page_with()._build_call().argv          # 从未选
    assert "--lang" not in _page_with(lang="")._build_call().argv   # 重置/清空


# ── 4a. 责编 → --editor；未选/重置无 --editor ─────────────────────────────
def test_editor_flag_follows_store():
    argv = _page_with(editor="editor_demo")._build_call().argv
    assert _flag_value(argv, "--editor") == "editor_demo"
    assert "--editor" not in _page_with()._build_call().argv
    assert "--editor" not in _page_with(editor="")._build_call().argv


# ── 4b. 回归：旧 bug 只读 field，store 未选时不得产生 --lang/--editor ──────
def test_lang_editor_read_store_not_field_regression():
    page = _page_with()
    page.fields["convert_lang"] = [SimpleNamespace(value="en")]
    page.fields["convert_editor"] = [SimpleNamespace(value="editor_demo")]
    argv = page._build_call().argv
    assert "--lang" not in argv
    assert "--editor" not in argv
