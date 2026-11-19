#!/usr/bin/env python
"""C 接线回归：回读校验页的 AI 折叠区 + 「在 VS Code 里对比」的诚实失败。

为什么单独一个文件：`test_bib_ai_assist_c_20261117.py` 是视图自身的契约（由子代理
产出、已全绿），这里只钉**接线**带来的两个风险点：

1. 「AI 处理 Bib」作为**回读校验页的折叠区**接入，不得破坏同页既有的三条钉子
   （只能有 1 个 `FilledButton`、不许出现 `Card`、页面标题只出现 1 次），并且
   **首次展开之前不许实例化视图**（否则初始控件树里就多出第二个主按钮）。
2. 「在 VS Code 里对比」打不开编辑器时，**必须**把两侧副本路径原样给出，且
   文案里不许出现「已打开」——「给了路径」不等于「打开了」。

不启动真引擎、不启动真 VS Code：只用假宿主与假编辑器模块。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from services import editor_bridge as ED  # noqa: E402
from ui import bib_ai_assist as BAA  # noqa: E402
from ui import bib_normalize as NV  # noqa: E402
from ui.tokens import Tokens  # noqa: E402


# ── 假宿主（照 test_b116_bib_normalize 的形状，够本文件用）────────────────
class _FakeStore:
    def __init__(self) -> None:
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

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

    def update(self) -> None:
        pass


class _Host:
    def __init__(self) -> None:
        self.t = Tokens("light")
        self.page = _FakePage()
        self.store = _FakeStore()
        self.app = SimpleNamespace(store=self.store, notes_repo="", window_w=1160,
                                   copy_text=lambda text: None)
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl"}
        self.view = ""
        self.notes: list[str] = []
        self.marks: list[tuple[str, dict]] = []

    def mark(self, event, **kwargs) -> None:
        self.marks.append((event, kwargs))

    def notify(self, message) -> None:
        self.notes.append(str(message))

    def _render(self) -> None:
        pass

    def set_view(self, view) -> None:
        self.view = view

    def run_task_ui(self, fn) -> None:
        self.page.tasks.append(fn)


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


# ── 1. 引用库页：新版直接成为页面内容（DESIGN §4；旧→新断言映射见交付） ──
def test_normalize_page_is_sole_ai_view_no_old_form():
    """新业务断言（替代旧 test_ai_section_keeps_page_nails）：

    旧断言钉的是「回读校验旧表单 + 折叠区 + 生成报告主按钮」；§4 裁定删旧表单后，
    库页**唯一内容**就是 BibAiAssistView(purpose=library)。新断言更强：
      · 唯一页面标题「AI 辅助处理 Bib」（body_only 自带无壳，不重复标题）；
      · 恰 1 个 FilledButton，且是当前阶段（idle → 准备）的真主动作「出任务书」；
        「应用到目标 Bib」退成阶段③里的次要按钮且禁用（问题 5 / 方案 B：主操作随
        阶段切换，apply 只在 preview 阶段才是页面上唯一的 FilledButton）；
      · 旧表单控件（交回稿/原件/正文三字段、字段对比/键闭合、生成报告、报告路径）
        全部不再渲染；无 Card；底栏「仅更新选定 Bib，不改中央库」。
    """
    host = _Host()
    view = NV.NormalizeView(host)
    tree = view.build()
    texts = _texts(tree)

    assert texts.count("AI 辅助处理 Bib") == 1, "唯一页面标题（body_only 不套壳）"
    primaries = [n for n in _walk(tree) if isinstance(n, ft.FilledButton)]
    assert len(primaries) == 1, "每阶段恰一个主操作"
    assert primaries[0].content == BAA.R3_ACTION_LABELS["prepare"], \
        "主操作须绑当前阶段真动作（idle → 出任务书）"
    assert "apply" not in view._ai_view._action_buttons, "准备态不创建未来阶段动作"
    assert not view._ai_view.can_apply(), "没有有效候选仍然禁止应用"
    assert not any(isinstance(n, ft.Card) for n in _walk(tree)), "不许引入 Card"
    for gone in ("交回稿（.bib）", "原件（.bib", "字段对比",
                 "键闭合", "生成报告", "报告路径"):
        assert not any(gone in t for t in texts), f"旧表单控件必须移除：{gone}"
    assert any("待处理 Bib" in text for text in texts), "库页只保留目标 Bib 输入"
    assert not any(".tex" in t or "关联正文" in t for t in texts), \
        "库页不显示正文路径"
    execute = view._ai_view._execute_boxes
    assert set(execute) == {"verify"}, "库页执行范围只有文献核实"
    assert view._ai_view.execute_scope() == ["bib_verify"]
    accept_tree = view._ai_view._accept_section()
    assert set(view._ai_view._accept_boxes) == {"verify"}, "库页采纳范围只有文献核实"
    assert not any("初排检查" in t or "文字纠错" in t for t in _texts(accept_tree))
    assert "仅更新选定 Bib，不改中央库" in texts, "底栏/页头语义必须是新边界"
    # 新版直接成为页面内容：构造即建视图（不再是「首次展开才建」的折叠区）
    assert view._ai_view is not None
    assert view._ai_view.purpose == "library", "库页必须 purpose=library"


def test_library_page_folds_to_current_stage_block():
    """方案 B（问题 5）：库页同一时刻只展开当前阶段块。

    idle（chat）→ 只有「① 准备」可见；②③④ 仍在树里、只是 `visible=False`。
    这样既有的「控件存在性」钉子全部不受影响，新增的是阶段显隐这条结构断言。
    """
    view = NV.NormalizeView(_Host())
    tree = view.build()
    boxes = {getattr(n, "_bib_ai_stage"): n for n in _walk(tree)
             if getattr(n, "_bib_ai_stage", None)}
    assert set(boxes) == {"prepare"}, "只创建当前工作区，不保留隐藏的过程输入"
    texts = _texts(tree)
    assert all(label in texts for label in ("准备任务", "获取结果", "审阅采纳", "完成"))
    assert "apply" not in view._ai_view._action_buttons
    assert not view._ai_view.can_apply()


def test_normalize_page_uses_body_only_compact(monkeypatch):
    """新业务断言（替代旧 test_ai_section_toggle_lazily_builds_body_only）：

    旧断言钉「折叠区惰性 body_only()」；§4 裁定新版直接成页面内容，改为断言：
    生产 build() 只调 `body_only(compact=True)`，不调 build()/page_shell，
    且显式传 purpose=library。compact=True 保证库页不显示用途单选/工作目录。
    """
    called: list = []

    class _FakeView:
        def __init__(self, page, *, purpose=""):
            called.append(("init", purpose))

        def body_only(self, *, compact=False, pinned_actions=False):
            assert pinned_actions is True, "宿主将唯一动作区固定在工作区底部"
            called.append(("body_only", compact))
            return ft.Column(controls=[ft.Text("LIBRARY-AI-BODY")])

        def build(self):
            raise AssertionError("库页只嵌 body_only，不得套 page_shell")

    monkeypatch.setattr(BAA, "BibAiAssistView", _FakeView)
    view = NV.NormalizeView(_Host())
    blob = " ".join(_texts(view.build()))
    assert [name for name, _ in called] == ["init", "body_only"]
    assert called[0][1] == "library", "库页要传 purpose=library"
    assert called[1][1] is True, "库页必须 compact=True（不显示用途/工作目录）"
    assert "LIBRARY-AI-BODY" in blob


def test_ai_section_passes_library_purpose(monkeypatch):
    """保护项（保留）：宿主传的是 library，不是 initial（否则会错标初排稿）。"""
    seen: list = []

    class _FakeView:
        def __init__(self, page, *, purpose=""):
            seen.append(purpose)

        def body_only(self, *, compact=False, pinned_actions=False):
            return ft.Column(controls=[])

    monkeypatch.setattr(BAA, "BibAiAssistView", _FakeView)
    NV.NormalizeView(_Host()).build()
    assert seen == ["library"]


def test_normalize_view_construction_failure_is_loud(monkeypatch):
    """新业务断言（替代旧 test_ai_section_failure_does_not_break_page）：

    旧断言钉「构造失败吞掉、旧表单仍可用 + 1 主按钮」；§4 删旧表单后，视图就是
    页面内容，构造失败不再有「旧表单兜底」。§2 要求构造时给清楚错误、禁止吞
    AttributeError 假装成功——新断言要求**如实抛出**、不静默造一个空页。
    """
    class _Boom:
        def __init__(self, page, *, purpose=""):
            raise RuntimeError("bridge missing")

    monkeypatch.setattr(BAA, "BibAiAssistView", _Boom)
    view = NV.NormalizeView(_Host())
    with pytest.raises(RuntimeError, match="bridge missing"):
        view.build()


def test_body_only_has_no_page_shell():
    """保护项（保留）：body_only() 不返回 page_shell 壳；compact 模式同样。"""
    host = _Host()
    view = BAA.BibAiAssistView(host, bridge=SimpleNamespace(run_call=lambda **k: 0))
    for compact in (False, True):
        body = view.body_only(compact=compact)
        assert getattr(body, "_bib_shell", None) is None
        assert isinstance(body, ft.Column)
        assert "AI 处理 Bib" not in " ".join(_texts(body)), "内嵌时不能再出现页面标题"


# ── 2. 「在 VS Code 里对比」：诚实失败，绝不假成功 ─────────────────────────
class _StubBridge:
    """7 个语义齐全的最小假桥（只用到 status_call / run_call）。"""

    def __init__(self) -> None:
        self.ran: list = []
        self.lines: list[str] = []

    def status_call(self, work_dir):
        return SimpleNamespace(entry="convert.py", argv=["status"], label="status")

    async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                       api=None, notes="", stdin_text=None):
        for line in self.lines:
            if on_line:
                on_line(line)
        self.ran.append(call)
        return 0


def _ready_view(tmp_path, *, candidate="cand.bib", target="target.bib",
                with_candidate=True):
    host = _Host()
    bridge = _StubBridge()
    view = BAA.BibAiAssistView(host, bridge=bridge)
    view.build()
    view.phase = "preview"
    view.preview_id = "pv-abc"
    view.blocked = []
    view.target_bib = str(tmp_path / target)
    Path(view.target_bib).write_text("@article{a, title={A}}\n", encoding="utf-8")
    if with_candidate:
        view.candidate_bib = str(tmp_path / candidate)
        Path(view.candidate_bib).write_text("@article{a, title={A2}}\n", encoding="utf-8")
    return view, host, bridge


def test_diff_button_disabled_without_candidate(tmp_path):
    view, _, _ = _ready_view(tmp_path, with_candidate=False)
    tree = view.build()
    buttons = [n for n in _walk(tree) if getattr(n, "_bib_ai_action", "") == "diff"]
    assert len(buttons) == 1
    assert buttons[0].disabled is True
    assert "生成拟应用结果" in (buttons[0].tooltip or "")
    assert view._can_diff() is False


def test_diff_reports_two_paths_when_editor_missing(tmp_path, monkeypatch):
    """编辑器不可用：给两条路径，文案里没有「已打开」，也不进成功态。"""
    view, host, _ = _ready_view(tmp_path)
    monkeypatch.setattr(ED, "find_editor", lambda: "")
    spawns: list = []
    monkeypatch.setattr(ED.subprocess, "run", lambda *a, **k: spawns.append((a, k)))

    view.open_diff()

    assert spawns == [], "找不到编辑器就不许启子进程"
    assert "已打开" not in view.notice
    assert str(tmp_path / "target.bib") not in view.error
    preview_dir = ED.preview_dir(str(tmp_path))
    assert preview_dir in view.notice or ".diff" in view.notice
    # 两侧副本路径都在文案里（旧版 / 新版）
    assert "旧版：" in view.notice and "新版：" in view.notice
    marks = [fields for event, fields in host.marks if event == "bib_ai_diff"]
    assert marks and marks[-1]["opened"] is False


def test_diff_success_uses_list_argv_and_no_shell(tmp_path, monkeypatch):
    view, host, _ = _ready_view(tmp_path)
    monkeypatch.setattr(ED, "find_editor",
                        lambda: "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")
    seen: list = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ED.subprocess, "run", fake_run)
    view.open_diff()

    assert len(seen) == 1
    argv, kwargs = seen[0]
    assert isinstance(argv, list) and "--diff" in argv
    assert kwargs.get("shell") in (None, False), "绝不许 shell=True"
    assert view.notice.startswith("已在编辑器里打开对比")
    marks = [fields for event, fields in host.marks if event == "bib_ai_diff"]
    assert marks and marks[-1]["opened"] is True


def test_diff_expired_copies_cannot_be_opened(tmp_path, monkeypatch):
    """副本被改过 → 应用闸门 `verify_preview` 必须判失效（过期副本不能用）。"""
    old_file = tmp_path / "reference.bib"
    old_file.write_text("@article{a, title={A}}\n", encoding="utf-8")
    preview = ED.make_preview(old_file=str(old_file), new_text="@article{a, title={A2}}\n",
                              ext=".bib", work_dir=str(tmp_path))
    ok, _ = ED.verify_preview(preview.preview_id, str(tmp_path))
    assert ok is True, "刚生成的预览应当有效"
    Path(preview.new_path).write_text("@article{a, title={TAMPERED}}\n", encoding="utf-8")
    ok, message = ED.verify_preview(preview.preview_id, str(tmp_path))
    assert ok is False and message, "副本被改过就不能再算有效"


def test_diff_requires_candidate_file(tmp_path):
    view, _, _ = _ready_view(tmp_path, with_candidate=False)
    view.open_diff()
    assert "先「生成拟应用结果」" in view.error
    assert view.phase == "preview", "失败不能改阶段"


# ── 3. 候选路径来自 status（引擎唯一来源）────────────────────────────────
def test_status_candidate_path_drives_diff_gate(tmp_path):
    view, host, bridge = _ready_view(tmp_path, with_candidate=False)
    bridge.lines = [
        "phase=previewed",
        "task_id=AIW-1",
        "preview_id=pv-1",
        "keys=3",
        f"candidate_bib={tmp_path / 'cand.bib'}",
    ]
    Path(tmp_path / "cand.bib").write_text("@article{a}\n", encoding="utf-8")
    asyncio.run(view.refresh_status())
    assert view.candidate_bib == str(tmp_path / "cand.bib")
    assert view._can_diff() is True
    # 引擎没给 candidate_bib 时必须保持空（不许拿别处的路径猜）
    bridge.lines = ["phase=previewed", "task_id=AIW-1"]
    asyncio.run(view.refresh_status())
    assert view.candidate_bib == "", "引擎没给路径就不能自己编一个"
    assert view._can_diff() is False



# ── 4. 正文粘贴 ≠ 路径：正文走 stdin，不进 argv ───────────────────────────
def test_body_paste_goes_to_stdin_not_argv():
    """用户粘的是 AI 回复**正文**：必须通过 stdin 交给引擎，绝不当成路径塞 argv。"""
    from services import engine_bridge as EB

    body = "@article{alpha, title={T}}\n"
    call = EB.ai_import_call(work_dir="/tmp/ws", body_text=body)
    assert "--stdin" in call.argv, "给了正文就该走 stdin 模式"
    assert body not in call.argv, "正文不许出现在 argv（会被当路径/进日志）"
    assert getattr(call, "stdin_text", "") == body

    # 给了明确路径时，路径优先，正文不进 stdin（两个入口不互相冒充）
    call2 = EB.ai_import_call(work_dir="/tmp/ws", reply="/tmp/r.md", body_text=body)
    assert "--stdin" not in call2.argv
    assert "/tmp/r.md" in call2.argv

# ── 5. A（§11.2）：真实报告抬头不得冒充「提示」行 ────────────────────────
REAL_REPORT = """**摘要**：结论：6 处问题 / 0 处提示 —— ❗ 有 6 处问题需要处理

**计数**：问题 6 ｜ 提示 0 ｜ 条目 1

<details>
<summary>全部明细（2 条）</summary>

- [二、编辑部规范] `a`：必填字段缺失：`author`
- [二、编辑部规范] `a`：必填字段缺失：`year`

</details>

# 退回版参考文献核对报告

**结论：6 处问题 / 0 处提示** —— ❗ 有 6 处问题需要处理

- 退回文件：`/tmp/pn/a.bib`（解析出 1 条条目）
- 原件（--original）：`/tmp/pn/a.bib`
- 正文（--tex）：**未提供**（不做引用键闭合核对）
- diff 静音：DROP_FIELDS 0 处 / SEP howpublished→url 0 处（本刊规范，不计假差异）

## 一、可解析性（语法）

✅ 本节未发现问题。

## 二、编辑部规范

**问题（2）**

- `a`：必填字段缺失：`author`
- `a`：必填字段缺失：`year`

## 附录：逐条状态

| 键 | 类型 | 问题 | 提示 | 与原件 |
|---|---|---|---|---|
| `a` | @article | 2 | 0 | 未变（逐字段一致） |
"""


def test_parse_report_drops_header_paths_and_marks_problems():
    """DESIGN §11.2 A：抬头三行是路径元信息，`**问题（` 之后才是问题行。

    真实报告（`bibreport.render`）开头有 `report_header` 的摘要/明细重复段，
    且抬头写的是 `原件（--original）：`。旧实现只认半截 `原件：`、又用分节标题
    判级别（`## 二、编辑部规范` 不含「问题」二字），结果路径冒充警告、问题被标成
    `warn`（计数对、标色错）。
    """
    from ui import bib_normalize as NV

    data = NV.parse_report(REAL_REPORT)
    assert (data["errors"], data["warnings"], data["records"]) == (6, 0, 1)
    kinds = {row["text"]: row["kind"] for row in data["rows"]}
    assert not any("a.bib" in text for text in kinds), f"路径噪声混进表格：{sorted(kinds)}"
    assert not any(text.startswith(("退回文件：", "原件", "正文", "diff ")) for text in kinds)
    assert kinds["`a`：必填字段缺失：`author`"] == "error", "在 `**问题（` 之后必须算问题"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ── 6. 初排页入口（DESIGN §3：唯一 AI 分区「AI 辅助初排」） ──────────────
def test_convert_page_has_initial_purpose_ai_entry():
    """新业务断言（旧 :439 `page._ai is not None` 要旧卡，§3 裁定删旧卡）：

    初排页必须有**唯一**「AI 辅助初排」分区，入口是折叠区、默认收起、惰性
    实例化；旧两段式 AiPanel 卡不再渲染（旧的「生成报告」文案消失），只留
    轻量 provider 供运行链复用。
    """
    from test_b114_polish import _build_single

    page, _root = _build_single()
    tree = page._build_convert()
    texts = _texts(tree)
    assert any("AI 辅助初排" in t for t in texts), "初排页要有 AI 辅助初排唯一分区"
    assert sum(1 for t in texts if "AI 辅助初排" in t) >= 1
    assert not any("生成报告" in t for t in texts), "旧两段式面板的按钮不得渲染"
    assert page._aiw_open is False
    assert page._aiw_view is None, "首次展开之前不许实例化"
    # 唯一 AI 分区：只有一个 bib_ai_toggle，无 _ai_section 旧卡
    assert sum(1 for n in _walk(tree) if getattr(n, "_bib_ai_toggle", False)) == 1
    # 轻量 provider 仍在（运行链 api_env/on_line/budget_notice/after_run 不断）
    assert page._ai is not None, "删卡后_lightweight provider 必须保留给运行链"


def test_convert_page_toggle_builds_initial_purpose_via_adapter(monkeypatch):
    """新业务断言（强化旧 toggle 测试）：初排展开必须经 _SingleAiHost 适配器
    （含 t/app/store/刷新/调度/文件选择/通知），不是裸 Flet Page；purpose=initial。
    """
    seen: list = []

    class _FakeView:
        def __init__(self, page, *, purpose=""):
            seen.append((purpose, page))
            # 宿主协议：适配器必须带 t/app/store 与各项服务，不是裸 Flet Page
            assert getattr(page, "t", None) is not None
            assert getattr(page, "app", None) is not None
            assert getattr(page, "store", None) is not None
            assert callable(getattr(page, "_render", None))
            assert callable(getattr(page, "run_task_ui", None))
            assert callable(getattr(page, "browse_field", None))

        def body_only(self, *, compact=False, pinned_actions=False):
            assert compact is True, "初排嵌视图必须 compact=True（不重复六路径）"
            return ft.Column(controls=[ft.Text("INITIAL-AI-BODY")])

    monkeypatch.setattr(BAA, "BibAiAssistView", _FakeView)
    from test_b114_polish import _build_single

    page, _root = _build_single()
    page._build_convert()
    page.set_field("single_source", "/tmp/anonymous.tex")
    page.set_field("single_out", "/tmp/output")
    page._toggle_ai_workflow(None)
    assert page._aiw_open is True
    assert [p for p, _ in seen] == ["initial"], "初排入口必须传 purpose=initial"
    # 宿主要是适配器，不是裸 Flet Page（Page.t 崩溃根因）
    host = seen[0][1]
    assert type(host).__name__ == "_SingleAiHost"
    blob = " ".join(_texts(page._build_convert()))
    assert "INITIAL-AI-BODY" in blob
    page._toggle_ai_workflow(None)
    assert page._aiw_open is False
    assert "INITIAL-AI-BODY" not in " ".join(_texts(page._build_convert()))
    assert len(seen) == 1, "收起/展开不得重建实例"


def test_convert_page_real_toggle_no_page_t_crash():
    """新业务断言（真宿主接线）：不 monkeypatch 假视图，用真 _build_single +
    真 BibAiAssistView 展开，证明 Page.t 崩溃不再出现（错误被 catch 不算过）。
    """
    from test_b114_polish import _build_single

    page, _root = _build_single()
    page._build_convert()
    page.set_field("single_source", "/tmp/anonymous.tex")
    page.set_field("single_out", "/tmp/output")
    page._toggle_ai_workflow(None)
    assert page._aiw_open is True, "真视图必须能展开"
    assert type(page._aiw_view).__name__ == "BibAiAssistView"
    assert page._aiw_view.t is not None, "适配器必须把 tokens t 接到视图"
    assert not any("打不开" in m or "绘制失败" in m for m in page.app.toasts), \
        "不得有任何绘制/构造失败的 toast"
    blob = " ".join(_texts(page._build_convert()))
    assert "使用上方源稿、输出目录和初排选项" in blob
    assert "目标 Bib（.bib）" not in blob
    assert "关联正文（.tex，可选）" not in blob
    assert "用途" not in blob, "compact=True 不显示用途单选"
    assert "工作目录" not in blob, "compact=True 不显示工作目录"


def test_convert_page_ai_entry_failure_is_honest(monkeypatch):
    """保护项（保留）：构造失败如实提示，不假装展开。"""
    class _Boom:
        def __init__(self, page, *, purpose=""):
            raise RuntimeError("boom")

    monkeypatch.setattr(BAA, "BibAiAssistView", _Boom)
    from test_b114_polish import _build_single

    page, _root = _build_single()
    page._build_convert()
    page._toggle_ai_workflow(None)
    assert page._aiw_open is False, "构造失败就不能假装展开"
    assert any("打不开" in m for m in page.app.toasts), "必须如实提示失败"


# ── 7. 真桥形参名：勾了采纳 / 选了结果文件必须真的进 argv（§3.1-2 / §3.1-3）──
class _RealSigBridge:
    """形参名与产品桥**完全一致**的假桥（这正是 §3.1-2/3 的失配面）。

    视图只给别名（`result_path` / `accept`）时，`_invoke` 会按签名把它们过滤掉，
    argv 里就既没有 `--reply` 也没有 `--adopt`——本类存在的意义就是让这个
    失配在测试里必然暴露（而不是像假桥那样用 `**kwargs` 兜住）。
    """

    def __init__(self) -> None:
        from services import engine_bridge as EB

        self.EB = EB
        self.ran: list = []

    # 与 engine_bridge 同名同签名（不含 **kwargs）
    def prepare_call(self, *, work_dir, target_bib, tex="", source="", stem="",
                     execute=(), adopt=(), text=False, entry="bib", tex_out=""):
        return self.EB.ai_prepare_call(
            work_dir=work_dir, target_bib=target_bib, tex=tex, source=source,
            stem=stem, execute=execute, adopt=adopt, text=text, entry=entry,
            tex_out=tex_out)

    def preview_call(self, *, work_dir, reply="", adopt=()):
        return self.EB.ai_preview_call(work_dir=work_dir, reply=reply, adopt=adopt)

    def import_call(self, *, work_dir, reply="", stdin=False, task_id="",
                    body_text=""):
        return self.EB.ai_import_call(work_dir=work_dir, reply=reply, stdin=stdin,
                                      task_id=task_id, body_text=body_text)

    def status_call(self, *, work_dir):
        return self.EB.ai_status_call(work_dir=work_dir)

    async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                       api=None, notes="", stdin_text=None):
        self.ran.append(call)
        return 0


def _argv_of(call) -> list[str]:
    return [str(part) for part in (getattr(call, "argv", None) or [])]


def test_prepare_argv_carries_adopt_and_convert_entry():
    """§3.1-3：采纳范围必须变成 `--adopt`；§3.1-1：初排入口走 `--entry convert`。"""
    host = _Host()
    bridge = _RealSigBridge()
    view = BAA.BibAiAssistView(host, bridge=bridge, purpose="initial")
    view.target_bib = "/tmp/ws/target.bib"
    view.work_dir = "/tmp/ws"
    view.tex_path = "/tmp/ws/paper.tex"
    # 只勾文献核实（取消初排检查）——argv 必须如实反映「用户勾了什么」。
    view.set_accept("proofcheck", False)
    # 内部键 `verify` → 引擎类别名 `bib_verify`（问题 4 M2；旧断言此处写成 `verify`）
    assert view.accept_scope() == ["bib_verify"], view.accept_scope()

    asyncio.run(view.prepare())

    assert bridge.ran, "prepare 必须真的跑一次引擎调用"
    argv = _argv_of(bridge.ran[0])
    assert "--adopt" in argv, f"采纳范围没进 argv：{argv}"
    adopt = argv[argv.index("--adopt") + 1]
    assert adopt == "bib_verify", f"--adopt 值必须是勾选项的引擎名：{adopt!r}"
    assert "--entry" in argv, f"初排入口没传 --entry：{argv}"
    assert argv[argv.index("--entry") + 1] == "convert", "初排链路必须 entry=convert"


def test_prepare_argv_omits_adopt_when_accept_all_off():
    """一项都没勾 → 不许凭空塞 `--adopt`（空串会被 `_append_opt` 丢掉）。"""
    host = _Host()
    bridge = _RealSigBridge()
    view = BAA.BibAiAssistView(host, bridge=bridge, purpose="library", taskbook_version=1)
    view.target_bib = "/tmp/ws/target.bib"
    view.work_dir = "/tmp/ws"
    view.set_accept("verify", False)
    view.set_accept("proofcheck", False)
    assert view.accept_scope() == []

    asyncio.run(view.prepare())

    argv = _argv_of(bridge.ran[0])
    assert "--adopt" not in argv, f"没勾任何类别却传了 --adopt：{argv}"
    assert argv[argv.index("--entry") + 1] == "bib", "库页入口必须 entry=bib"


def test_preview_argv_carries_reply_and_adopt():
    """§3.1-2：选了结果文件 → `--reply <路径>`；§3.1-3：采纳范围 → `--adopt`。"""
    host = _Host()
    bridge = _RealSigBridge()
    view = BAA.BibAiAssistView(host, bridge=bridge, purpose="library", taskbook_version=1)
    view.target_bib = "/tmp/ws/target.bib"
    view.work_dir = "/tmp/ws"
    view.result_path = "/tmp/ws/回填_API.md"
    view.set_accept("proofcheck", False)

    asyncio.run(view.preview())

    assert bridge.ran, "preview 必须真的跑一次引擎调用"
    argv = _argv_of(bridge.ran[0])
    assert "--reply" in argv, f"结果文件没进 argv：{argv}"
    assert argv[argv.index("--reply") + 1] == "/tmp/ws/回填_API.md"
    assert "--adopt" in argv, f"采纳范围没进 argv：{argv}"
    assert argv[argv.index("--adopt") + 1] == "bib_verify"


def test_import_argv_uses_reply_path_not_body_when_both_present():
    """路径与正文是两个入口：有路径时正文不许冒充路径（DESIGN §10）。"""
    host = _Host()
    bridge = _RealSigBridge()
    view = BAA.BibAiAssistView(host, bridge=bridge)
    view.work_dir = "/tmp/ws"
    view.result_path = "/tmp/ws/回填.md"
    view.body_text = "@article{alpha, title={T}}\n"

    asyncio.run(view.import_result())

    argv = _argv_of(bridge.ran[0])
    assert argv[argv.index("--reply") + 1] == "/tmp/ws/回填.md"
    assert "--stdin" not in argv, "给了路径就不该再走 stdin"
    assert "@article{alpha" not in " ".join(argv), "正文绝不许进 argv"


def test_body_only_import_sends_text_to_bridge_stdin():
    """验收 B1（终验阻断）：只粘正文时，正文必须真的到达桥的 `stdin_text`。

    缺陷原状：`_call_kwargs` 给了 `stdin`/`body_text`，但真正跑的那次调用若拿不到
    正文，引擎就只收到 `--stdin` + 空输入 → import 落不到内容 → 后面 preview 报
    「未提供回填 bib 块」，Chat 主通道（N04）在第二步断掉。
    """
    host = _Host()
    bridge = _RealSigBridge()
    view = BAA.BibAiAssistView(host, bridge=bridge)
    view.work_dir = "/tmp/ws"
    view.body_text = "@article{alpha, title={T}}\n"

    asyncio.run(view.import_result())

    call = bridge.ran[0]
    argv = _argv_of(call)
    assert "--stdin" in argv, "只给正文时应当走 stdin 模式"
    assert "--reply" not in argv, "没有路径就不该伪造一个 --reply"
    assert getattr(call, "stdin_text", "") == view.body_text, (
        "正文必须原样交给桥的 stdin_text，否则引擎收到空输入")


# ── 8. N02 路径选择器：工作目录必须是 dir、正文入口必须在（§3.1-11①②）────
class _BrowseHost(_Host):
    """记录 `browse_field(ref, title, kind, key)` 的 kind（N02 的判据）。

    注意：视图取的是 `self._app().browse_field`（`_Host.app` 是 SimpleNamespace），
    所以桩必须挂在 `app` 上，挂在宿主自己身上视图看不到。
    """

    def __init__(self) -> None:
        super().__init__()
        self.picked: list[tuple[str, str, str]] = []
        self.app.browse_field = self._record

    def _record(self, ref, title, kind, key, store_prefix="recent", *, on_result=None,
                result_guard=None) -> None:
        assert callable(result_guard) and result_guard()
        self.picked.append((str(key), str(title), str(kind)))


def _kinds_by_key(view, host) -> dict[str, str]:
    """点每个「浏览…」按钮一次，收集 key → kind。

    `_browse` 有重入闸（`self._picking`）：一次只能有一个选择器在等，所以每点一次
    都要把闸放开——否则第二个按钮的 handler 会直接 return，测试就只看到第一个。
    """
    host.picked.clear()
    for control in _walk(view.body_only()):
        if isinstance(control, ft.OutlinedButton) and str(control.content or "") == "浏览…":
            view._picking = False
            if callable(control.on_click):
                control.on_click(None)
    return {key: kind for key, _title, kind in host.picked}


def test_work_dir_picker_uses_dir_kind():
    """§3.1-11①：工作目录是**目录**，按 file 选会让人挑到一个文件。"""
    host = _BrowseHost()
    view = BAA.BibAiAssistView(host, purpose="library", taskbook_version=1)
    kinds = _kinds_by_key(view, host)
    assert kinds.get("bib_ai_work_dir") == "dir", kinds


def test_tex_pickers_are_file_kind_and_present():
    """§3.1-11②：可选关联正文 / 正文交付件两个 `.tex` 入口必须在，且按 file 选。"""
    host = _BrowseHost()
    view = BAA.BibAiAssistView(host, purpose="initial")
    kinds = _kinds_by_key(view, host)
    assert kinds.get("bib_ai_tex_path") == "file", kinds
    assert kinds.get("bib_ai_tex_out") == "file", kinds


def test_tex_picker_values_reach_prepare_argv():
    """§3.1-11②：选了 `.tex` 与交付件，`--tex` / `--tex-out` 必须真的进 argv。"""
    host = _Host()
    bridge = _RealSigBridge()
    view = BAA.BibAiAssistView(host, bridge=bridge, purpose="initial")
    view.target_bib = "/tmp/ws/t.bib"
    view.work_dir = "/tmp/ws"
    view.tex_path = "/tmp/ws/paper.tex"
    view.tex_out = "/tmp/ws/out.tex"

    asyncio.run(view.prepare())

    argv = _argv_of(bridge.ran[0])
    assert argv[argv.index("--tex") + 1] == "/tmp/ws/paper.tex"
    assert argv[argv.index("--tex-out") + 1] == "/tmp/ws/out.tex"


# ── 9. Key 可达（lead m00653 ①）：_run 必须把 api/notes 传到 run_call ────────
def test_run_passes_api_and_notes_to_real_signature_bridge():
    """引擎 Key 链路：store 里的 ai.key 必须经 run_call(api=) → env_for_run
    → SLCN_LLM_KEY 进子进程环境；notes_repo 也要到 run_call(notes=)。

    只用假桥断言**形参到达**，不真发请求、不调付费 API。这正是调研结论里
    `_run` 原先漏传 api/notes 导致 Key 静默断链的回归钉子。
    """
    from services import engine_bridge as EB  # noqa: PLC0415

    captured: dict = {}

    class _RealSigClient:
        async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                           api=None, notes="", stdin_text=None):
            captured.update(api=api, notes=notes, cwd=cwd, stdin_text=stdin_text)
            if on_line:
                on_line("ok")
            return 0

    host = _Host()
    host.store.set("ai", {"base": "http://llm.local", "key": "SK-TEST-KEY",
                          "model": "m", "protocol": "chat", "thinking": False})
    host.app.notes_repo = "/tmp/notes.md"
    view = BAA.BibAiAssistView(host, bridge=_RealSigClient(), purpose="initial")
    view.work_dir = "/tmp/ws"

    code = asyncio.run(view._run(SimpleNamespace(stdin_text="")))

    assert code == 0
    assert captured["api"] and captured["api"]["key"] == "SK-TEST-KEY", (
        "store 的 key 必须经 api= 到达 run_call，不能断在 call 工厂")
    assert captured["notes"] == "/tmp/notes.md", "notes_repo 必须到 run_call(notes=)"
    assert captured["cwd"] == "/tmp/ws"
    # 再走一层真 env_for_run：证明 key 落到 SLCN_LLM_KEY（不进 argv/日志）。
    env = EB.env_for_run(captured["api"], captured["notes"])
    assert env.get("SLCN_LLM_KEY") == "SK-TEST-KEY"
    assert "SK-TEST-KEY" not in " ".join(view.log), "Key 绝不许写进日志行"


def test_run_keeps_legacy_bridge_without_api_notes():
    """兼容面：旧签名假桥（不收 api/notes）不能被硬塞参数而 TypeError。

    非我写域 test_bib_ai_assist_c_20261117.py 的 _Bridge 就是这种签名；
    `_run` 用 inspect.signature 按契约传参，旧桥仍可用。
    """
    captured: dict = {}

    class _LegacyClient:
        async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                           stdin_text=None):
            captured["called"] = True
            return 0

    view = BAA.BibAiAssistView(_Host(), bridge=_LegacyClient(), purpose="initial")
    code = asyncio.run(view._run(SimpleNamespace(stdin_text="")))
    assert code == 0 and captured.get("called")
    assert not view.error, "旧签名桥不该被 api/notes 硬塞弄出错误态"

# ── 8. 问题 4 M1：argv 里必须真的有 `--work-dir`（CLI 必填项）──────────────
class _StatusBridge(_RealSigBridge):
    """真桥签名 + 能报状态行的假 run：只为验「状态怎么被调用」。"""

    async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                       api=None, notes="", stdin_text=None):
        self.ran.append(call)
        for line in ("phase=preview", "keys=7", "entries=3"):
            if on_line is not None:
                on_line(line)
        return 0


def test_prepare_argv_derives_work_dir_from_target_bib(tmp_path):
    """界面从没填工作目录时，出任务书仍必须给 `--work-dir`（否则 argparse 退 2）。"""
    folder = tmp_path / "2026年第5期(90)" / "07 匿名作者"
    folder.mkdir(parents=True)
    target = folder / "reference.bib"
    target.write_text("@article{a, title={A}}\n", encoding="utf-8")
    before = target.read_bytes()

    bridge = _RealSigBridge()
    view = BAA.BibAiAssistView(_Host(), bridge=bridge, purpose="library", taskbook_version=1)
    assert view.work_dir == "", "前置：视图本来就没工作目录"
    view.target_bib = str(target)

    asyncio.run(view.prepare())

    assert not view.error, view.error
    assert bridge.ran, "prepare 必须真的跑一次引擎调用"
    argv = _argv_of(bridge.ran[0])
    assert "--work-dir" in argv, f"`--work-dir` 没进 argv：{argv}"
    assert argv[argv.index("--work-dir") + 1] == str(folder / ".aiw")
    assert target.read_bytes() == before, "出任务书不许动目标 Bib 一个字节"


def _path_parts_without_anchor(value: str) -> tuple[str, ...]:
    """路径分量（去掉盘符/根锚点），供 POSIX 与 Windows 跨平台比较。

    Windows 下 `os.path.abspath("/tmp/x")` 会用当前盘符补成 `D:\\tmp\\x`；
    去掉锚点后两边都只剩 ('tmp', 'x')。这样既钉住「目录结构与文件名完全相同」，
    又不把平台盘符差异误判成产品缺陷。
    """
    p = Path(value)
    return p.parts[1:] if p.anchor else p.parts


def test_status_call_uses_keyword_work_dir_on_real_signature():
    """真桥 `ai_status_call(*, work_dir)` 是 keyword-only：按位置数参数会漏传。"""
    bridge = _StatusBridge()
    view = BAA.BibAiAssistView(_Host(), bridge=bridge, purpose="library", taskbook_version=1)
    view.target_bib = "/tmp/ws/中文 空格/reference.bib"

    asyncio.run(view.refresh_status())

    assert not view.error, f"状态没刷出来：{view.error!r}"
    assert bridge.ran, "status 必须真的跑一次引擎调用"
    argv = _argv_of(bridge.ran[0])
    assert "status" in argv, argv
    assert "--work-dir" in argv, "status 调用必须真的带 --work-dir 关键字"
    # 原断言：argv[argv.index("--work-dir") + 1] == "/tmp/ws/中文 空格/.aiw"
    # Windows 实际是 D:\tmp\ws\中文 空格\.aiw（abspath 补了盘符）。改为按路径分量比较，
    # 仍要求值恰好是 target_bib 所在目录 + ".aiw"，一字不差（含中文与空格）。
    assert _path_parts_without_anchor(argv[argv.index("--work-dir") + 1]) == (
        "tmp", "ws", "中文 空格", ".aiw",
    )
    assert view.phase == "preview"


# ── 10. 真机缺陷 C：纯签名过滤不再当橙色「接线提示」上屏 ──────────────────
def test_signature_filter_noise_not_rendered_as_wiring_warning():
    """真桥形参没有的产品参数会被 `_invoke` 过滤，但那是兼容过程、不是故障。

    修前：状态区橙色长串「接线提示：prepare_call 不接受参数：mode、purpose…」。
    修后：诊断仍留在 `_wiring_notes`（供排查），但不渲染；缺函数/桥未就绪走
    `_banners()`，不在本测试覆盖的范围内（既有测试继续钉它）。
    """
    host = _Host()
    bridge = _RealSigBridge()
    view = BAA.BibAiAssistView(host, bridge=bridge, purpose="library", taskbook_version=1)
    view.target_bib = "/tmp/ws/target.bib"

    asyncio.run(view.prepare())

    assert view.phase == "waiting", "前置：prepare 必须成功"
    assert view._wiring_notes, "签名差异仍要留诊断记录（不删记录功能）"
    assert any("不接受参数" in note for note in view._wiring_notes), view._wiring_notes

    blob = " ".join(_texts(ft.Column(controls=view._parts())))
    assert "不接受参数" not in blob, f"签名过滤噪音又上屏了：{blob!r}"
    assert "接线提示" not in blob, f"不该有橙色接线长串：{blob!r}"
