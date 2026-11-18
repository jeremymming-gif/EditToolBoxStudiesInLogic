"""批 115 BPAGES-A：回读校验页契约测试（不启动真引擎）。

覆盖：一页一个主按钮、空态保留表头、Markdown 解析（错误优先）、
0 错误“去入藏”、失败保留旧报告、1000 宽不横滚。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from services import engine_bridge as EB
from ui import bib_normalize as NV
from ui.tokens import Tokens


class _FakeStore:
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value


class _FakePage:
    def __init__(self) -> None:
        self.on_keyboard_event = None
        self.dialogs = []
        self.tasks = []
        self.width = 1160

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)


class _Host:
    def __init__(self) -> None:
        self.t = Tokens("light")
        self.page = _FakePage()
        self.app = SimpleNamespace(store=_FakeStore(), notes_repo="", window_w=1160,
                                   copy_text=lambda text: None)
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl"}
        self.view = ""

    def mark(self, event, **kwargs) -> None:
        pass

    def _render(self) -> None:
        pass

    def set_view(self, view) -> None:
        self.view = view


def _walk(control) -> list:
    out = [control]
    content = getattr(control, "content", None)
    if content is not None:
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    for child in getattr(control, "actions", None) or ():
        out.extend(_walk(child))
    return out


def _texts(control) -> list[str]:
    out = []
    for node in _walk(control):
        value = getattr(node, "value", None)
        if isinstance(value, str):
            out.append(value)
        content = getattr(node, "content", None)
        if isinstance(content, str):
            out.append(content)
        label = getattr(node, "label", None)
        if isinstance(label, str):
            out.append(label)
    return out


REPORT = """# 回读校验报告

**结论：5 处问题 / 7 处提示** —— 交回稿与原件不一致。

- 退回文件：`/tmp/a.bib`（解析出 9 条条目）
- 原件：`/tmp/b.bib`（解析出 9 条条目）

## 问题（5）

- 键 `k1`：题名不一致
- 键 `k2`：年份不一致

## 提示（7）

- 键 `k9`：作者顺序可能不同
- 键 `k10`：页码缺失
- 键 `k11`：DOI 大小写
- 键 `k12`：期号缺失

## 附录：逐条状态

| 键 | 类型 | 问题 | 提示 | 与原件 |
|---|---|---|---|---|
| `k1` | @article | 1 | 0 | 题名不同 |
| `k2` | @book | 1 | 0 | 年份不同 |
| `k3` | @article | 0 | 0 | 一致 |
| `k4` | @article | 0 | 1 | 作者顺序 |
| `k5` | @article | 0 | 0 | 一致 |
| `k6` | @article | 0 | 0 | 一致 |
"""


def test_parse_report_counts_and_error_first():
    data = NV.parse_report(REPORT)
    assert (data["errors"], data["warnings"], data["records"]) == (5, 7, 9)
    assert data["keys"] == 6
    assert data["ok"] is False
    assert data["rows"][0]["kind"] == "error"
    assert "题名不一致" in data["rows"][0]["text"]


def test_default_report_path_matches_engine():
    assert NV.default_report_path("/tmp/a/Foo.bib").endswith("Foo_回读校验报告.md")


def test_normalize_initial_state_is_sole_ai_view_no_old_form():
    """DESIGN §4：库页直接就是「AI 辅助处理 Bib」一页，旧回读表单不再渲染。

    旧断言钉的是「回读校验 + 生成报告 + 三字段 + 空态表头」（已按 lead 授权
    替换为更强的新业务断言，见交付的旧→新映射）：现在要求唯一标题、唯一
    主按钮是当前阶段的真主动作（idle → 「生成任务书」）、扁平无 Card、
    旧表单/开关/报告工具条全无、默认只留目标 Bib + 关联正文（可选），
    底栏写明「仅更新选定 Bib」。
    """
    host = _Host()
    view = NV.NormalizeView(host)
    tree = view.build()
    texts = _texts(tree)
    # 视图身份：路由 key 不变（bib_host 兼容），但页头语义改为「写目标 Bib」。
    assert NV.VIEW_KEY == "normalize"
    assert texts.count("AI 辅助处理 Bib") == 1, "唯一页头，不许折叠区再画一次"
    assert "仅更新选定 Bib，不改中央库" in texts, "页头/底栏必须写清楚写语义"
    # 唯一主按钮 = 当前阶段的真主动作（方案 B：主操作随阶段切换）。
    # idle 阶段是「生成任务书」；「应用到目标 Bib」退成阶段③的次要按钮。
    primaries = [n for n in _walk(tree) if isinstance(n, ft.FilledButton)]
    assert len(primaries) == 1 and primaries[0].content == "生成任务书", \
        "主操作随阶段：idle → 生成任务书"
    # 旧断言钉「应用未到 preview 时禁用」；方案 B 下 idle 的主按钮是「生成任务书」，
    # 它**常可点**（缺目标 Bib 时点它会给「生成任务书失败：请先选择待处理 Bib」的明确红字，
    # 而不是灰着不给反馈）。「应用」仍禁用，但它是阶段③的次要按钮，单独钉。
    assert primaries[0].disabled is False, "生成任务书在 idle 可点，缺输入时如实报错"
    apply_btn = view._ai_view._action_buttons["apply"]
    assert isinstance(apply_btn, ft.OutlinedButton) and apply_btn.disabled, \
        "没有 preview 结果时应用必须禁用（阶段③的次要按钮）"
    assert not any(isinstance(n, ft.Card) for n in _walk(tree)), "不许引入 Card"
    # 旧表单/开关/工具条彻底不渲染（§4）。
    for gone in ("生成报告", "字段对比", "键闭合", "报告路径", "去入藏",
                 "还没有报告；先选 .bib 再生成。"):
        assert not any(gone in text for text in texts), f"旧表单残留：{gone}"
    # 新默认字段：待处理 Bib（必填、也是输入）+ 关联正文（可选）；不再有
    # 用途单选/正文交付件/工作目录/重复源 Bib。
    assert any("待处理 Bib" in text and "必填" in text for text in texts)
    tex_labels = [text for text in texts if ".tex" in text]
    assert tex_labels and all("关联正文" in text and "可选" in text for text in tex_labels)
    for gone in ("工作目录", "正文交付件", "用途"):
        assert not any(gone in text for text in texts), f"compact 未生效：{gone}"
    # 视图在构造期就建好（新版直接成为页面内容，不再是折叠惰性实例）。
    assert view._ai_view is not None and getattr(view._ai_view, "purpose", "") == "library"


def test_normalize_requires_returned_path():
    """功能契约（非展示）：没有交回稿就绝不排引擎任务，并如实写明原因。

    旧断言读 build() 里的报文字串；旧表单已移除，改为断言数据层
    `input_error` 与「未调度任何任务」——比原先只测文本更强，防止
    generate() 在缺路径时仍静默起子进程（_NormalizeCompat 依赖这条红线）。
    """
    host = _Host()
    view = NV.NormalizeView(host)
    view.generate()
    assert view.input_error == "先选交回的 .bib 文件。"
    assert not host.page.tasks, "缺交回稿时不得调度 checkbib 子进程"


def _fake_engine(monkeypatch, report: str, code: int = 1, captured=None):
    def fake_checkbib(**kwargs):
        out = kwargs.get("out") or "/tmp/report.md"
        return SimpleNamespace(out=out, kwargs=kwargs)

    async def fake_run(call, *, on_line=None, on_proc=None, cwd=None, api=None,
                       notes="", stdin_text=None):
        if captured is not None:
            captured.update(call.kwargs)
        Path(call.out).write_text(report, encoding="utf-8")
        if on_line:
            on_line("checkbib running")
        return code

    monkeypatch.setattr(EB, "checkbib_call", fake_checkbib)
    monkeypatch.setattr(EB, "run_call", fake_run)


def test_normalize_success_parses_counts_and_keeps_report_fresh(monkeypatch, tmp_path):
    """报告解析/计数是数据层契约（§5 只读检查兼容面依赖），不随 UI 移除而弱化。

    旧断言读旧表单里的「体检未通过 / 5 错误 · 7 警告 / 还有 2 条 / 报告未更新」
    文案（已移除）；改为断言数据层解析结果、success 摘要、loaded 与「本次确实
    写出并回读了报告」——比读文案更贴近引擎输出契约。
    """
    _fake_engine(monkeypatch, REPORT)
    host = _Host()
    view = NV.NormalizeView(host)
    view.returned = str(tmp_path / "returned.bib")
    view.original = str(tmp_path / "original.bib")
    view.compare = True
    view.report_path = str(tmp_path / "report.md")
    asyncio.run(view._run_check())
    assert view.success.startswith("体检未通过：5 错误、7 警告")
    assert (view.data["errors"], view.data["warnings"],
            view.data["records"], view.data["keys"]) == (5, 7, 9, 6)
    assert view.loaded is True
    assert Path(view.report_path).read_text(encoding="utf-8") == REPORT
    assert not view.error, "正常解析路径不得留错误态"


def test_normalize_pass_marks_ok_without_side_effects(monkeypatch, tmp_path):
    """0 错误报告要标体检通过；新版不再提供「去入藏」按钮（§4 不改中央库）。

    旧断言「去入藏」是旧表单里的入藏预填入口，已按 §4 移除；改为断言数据层
    errors==0 + success 文案 + 没有触发任何入藏/Git 副作用（本页无这些方法调用）。
    """
    report = REPORT.replace("**结论：5 处问题 / 7 处提示**", "**结论：0 处问题 / 2 处提示**")
    _fake_engine(monkeypatch, report, code=0)
    host = _Host()
    view = NV.NormalizeView(host)
    view.returned = str(tmp_path / "returned.bib")
    view.report_path = str(tmp_path / "report.md")
    asyncio.run(view._run_check())
    assert view.data["errors"] == 0
    assert view.success == "体检通过：0 错误、2 警告。"
    assert view.loaded is True
    # §4：不自动定位/覆盖来稿，不触发中央库入藏或 Git 提交。
    assert not getattr(view, "_import_requested", False)
    assert "去入藏" not in " ".join(_texts(view.build()))


def test_normalize_failure_keeps_old_report(monkeypatch, tmp_path):
    """引擎抛错时：如实记 error，且绝不覆盖上次报告（保留旧文件/旧内容）。"""
    async def fake_run(call, **kwargs):
        raise RuntimeError("checkbib exploded")

    monkeypatch.setattr(EB, "checkbib_call", lambda **kw: SimpleNamespace(out=kw["out"]))
    monkeypatch.setattr(EB, "run_call", fake_run)
    host = _Host()
    view = NV.NormalizeView(host)
    view.returned = str(tmp_path / "returned.bib")
    view.report_path = str(tmp_path / "report.md")
    view.loaded = True
    view.report_text = "OLD-REPORT-CONTENT"
    asyncio.run(view._run_check())
    assert view.error == "生成失败：checkbib exploded"
    assert view.report_text == "OLD-REPORT-CONTENT", "失败不得覆盖上次报告内容"
    assert not Path(view.report_path).exists(), "失败不得写出新报告文件"


def test_normalize_narrow_no_horizontal_scroll():
    host = _Host()
    host.page.width = 1000
    host.app.window_w = 1000
    view = NV.NormalizeView(host)
    tree = view.build()
    assert view._build_width < 1100
    assert not any(getattr(node, "scroll", None) == ft.ScrollMode.ALWAYS
                   for node in _walk(tree))
