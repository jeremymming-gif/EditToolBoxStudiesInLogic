"""批 109 W2b：一校核查 + 检查 tex 信息结果卡回归。

只测外壳的结构化显示/拼参/文案：数据全部来自引擎 JSON 与报告 md 的小样本，
不跑引擎、不碰真实引用库；UI 用 Tokens 直接构造，不需要 Flet 页面。
"""
import asyncio
import os
import sys
from types import SimpleNamespace

import flet as ft

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import engine_bridge as EB  # noqa: E402
from ui import checks as CK  # noqa: E402
from ui import components as C  # noqa: E402
from ui import pages  # noqa: E402
from ui.tokens import Tokens  # noqa: E402


# ── 检查 tex 信息：字段归一 ────────────────────────────────────────────────
def _article(**over):
    raw = {
        "folder": "01 张三", "folderName": "01 张三", "volume": "42", "issue": "3",
        "year": "2026", "startPage": "1", "endPage": "12", "pages": "12",
        "fontSize": "10.5", "lineSpacing": "1.5",
        "firstAuthorChinese": "张三", "chineseTitle": "论 一个 标题\n第二行",
        "actualPDFPages": 13, "hasPDF": True,
    }
    raw.update(over)
    return raw


def _payload():
    return {"articles": [
        _article(),
        _article(folder="02 李四", folderName="02 李四", startPage="13", endPage="20",
                 pages="8", actualPDFPages=8, firstAuthorChinese="",
                 firstAuthorEnglish="Li Si", chineseTitle="", englishTitle="On Logic"),
    ], "inconsistencies": ["01 张三：声明页数 12，实际 13 页"]}


def test_normalize_article_flattens_engine_fields():
    row = CK.normalize_article(_article())
    assert row["span"] == "1–12"
    assert row["pages"] == "12" and row["declared"] == 12
    assert row["actual"] == 13 and row["actual_text"] == "13"
    assert row["font_line"] == "10.5 / 1.5"
    assert row["author"] == "张三"
    assert row["title"] == "论 一个 标题 第二行"  # 换行不许撑开表格
    assert row["mismatch"] is True


def test_normalize_article_falls_back_to_english_and_dash():
    row = CK.normalize_article(_article(actualPDFPages=None, hasPDF=False, pages="",
                                        chineseTitle="", englishTitle="On Logic"))
    assert row["title"] == "On Logic"
    assert row["actual_text"] == "—" and row["declared"] is None
    assert row["mismatch"] is False and row["has_pdf"] is False
    empty = CK.normalize_article({})
    assert empty["author"] == "—" and empty["span"] == "—" and empty["folder"] == "—"


def test_normalize_articles_accepts_payload_list_and_garbage():
    arts = CK.normalize_articles(_payload())
    assert len(arts) == 2 and arts[1]["author"] == "Li Si"
    assert len(CK.normalize_articles([_article()])) == 1
    assert CK.normalize_articles(None) == []
    assert CK.normalize_articles({"articles": [None, 3, _article()]})[0]["author"] == "张三"


def test_inconsistencies_prefers_engine_and_recomputes_fallback():
    arts = CK.normalize_articles(_payload())
    assert CK.inconsistencies(_payload(), arts) == ["01 张三：声明页数 12，实际 13 页"]
    fallback = {"articles": [_article(volume="43"), _article(volume="42")]}
    notes = CK.inconsistencies(fallback)
    assert "卷号不一致：42 / 43" in notes
    assert any("声明页数 12，实际 13 页" in note for note in notes)


def test_info_summary_four_variants():
    assert CK.info_summary({}) == CK.EMPTY_INFO
    arts = CK.normalize_articles(_payload())
    summary = CK.info_summary(_payload(), articles=arts)
    assert summary.startswith("共 2 篇；发现 1 处需要核对：")
    assert CK.info_summary({"articles": []}) == CK.EMPTY_INFO
    notes = ["a", "b", "c", "d"]
    built = CK.info_summary({"articles": [_article()]}, notes=notes)
    assert built.startswith("共 1 篇；发现 4 处需要核对：a；b；c；…")
    good = {"articles": [_article(actualPDFPages=12),
                         _article(actualPDFPages=8, pages="8")]}
    assert CK.info_summary(good) == "共 2 篇，卷号/期号/年份一致，页数与 PDF 实际页数相符。"
    no_pdf = {"articles": [_article(actualPDFPages=None, hasPDF=False)]}
    assert "1 篇没有可比对的 PDF 页数" in CK.info_summary(no_pdf)
    assert CK.info_level({}) == "idle" and CK.info_level(good) == "done"
    assert CK.info_level(_payload()) == "warn"


# ── 检查 tex 信息：表格渲染 ────────────────────────────────────────────────
def test_info_table_has_ten_columns_and_red_mismatch_row():
    t = Tokens()
    table = CK.build_info_table(CK.normalize_articles(_payload()), t).controls[0].content
    assert [col.label.value for col in table.columns] == list(CK.INFO_COLUMNS)
    assert len(table.rows) == 2
    bad, good = table.rows
    assert bad.color == t.palette.error_soft and good.color is None
    assert bad.cells[6].content.color == t.palette.error
    assert bad.cells[6].content.weight == ft.FontWeight.W_600
    assert good.cells[6].content.color != t.palette.error
    title = bad.cells[9].content
    assert title.content.tooltip == "论 一个 标题 第二行"


def test_info_card_lifecycle_texts():
    t = Tokens()
    card = CK.InfoCheckCard(t, on_open=lambda path: called.append(path))
    called = []
    assert card.chip.content.value == "还没检查" and card.empty.visible
    card.show(_payload(), report="C:/report.txt")
    assert card.chip.content.value == "需要核对"
    assert card.open_btn.disabled is False and card.table_box.visible
    card._open()
    assert called == ["C:/report.txt"]
    card.show_unreadable("C:/report.txt")
    # 批 115 设计检修 P1-4：警告行已解释「表格为什么空」，通用空态不再叠加。
    assert card.warning.value == CK.UNREADABLE_INFO and card.empty.visible is False
    card.show_failed(2)
    assert card.chip.content.value == "失败" and card.table_box.visible is False


# ── 一校核查：报告解析 ─────────────────────────────────────────────────────
_REPORT = """**摘要**：有 3 处需人工

**计数**：需人工 3 ｜ 可自动改 2 ｜ 命中合计 5 ｜ 规则数 29

## 摘要
| 规则 | 条数 | 说明 | 依据 |
| --- | --- | --- | --- |
| `ZH-01` | **2** | 中文标点 | 手册 4.1 |
| `EN-01` | **1** | 英文标点 | 手册 4.2 |

**合计命中 3 处。**

可自动修 2 处（ZH-01、EN-01）：初排已默认处理，需补改请加 `--fix`。
"""


def test_parse_proof_report_prefers_header_counts_and_top_rules():
    report = CK.parse_proof_report(_REPORT)
    assert report.ok and report.manual == 3 and report.fixable == 2
    assert report.total == 5 and report.rule_count == 29
    assert report.conclusion == "有 3 处需人工"
    assert [r.rid for r in report.top_rules()] == ["ZH-01", "EN-01"]
    assert report.rules[0].basis == "手册 4.1"


_REPORT_REAL_SHAPE = r"""**摘要**：❗ 有 30 处需要人工处理

**计数**：需人工 30 ｜ 可自动改 3 ｜ 命中合计 33 ｜ 规则数 29

## 摘要

| 规则 | 条数 | 说明 | 依据 |
|---|---|---|---|
| `ZH-07` | **2** | 中文旁的半角括号 | 经验书 预校正·中文 |
| `ZH-08` | **27** | 中文旁的半角短杠 | 经验书 预校正·中文 |
| `ZH-10` | **1** | 脚注在逗号/句号之后 | 手册 §校对规范 4.1 |

**合计命中 33 处。**

可自动修 3 处（ZH-01、ZH-08）：初排已默认处理，需补改请加 `--fix`。

## 已自动改的部分（`--fix`）

| 规则 | 行 | 原文 | 改成 |
|---|---|---|---|
| `ZH-01` | 372 | `型,其` | `型，其` |
| `ZH-08` | 483 | `-` | `\edash ` |

## 逐条

### `ZH-07` 中文旁的半角括号 —— 2 处

| 行 | 列 | 原文 |
|---|---|---|
| 502 | 83 | `满(` |
"""


def test_parse_proof_report_ignores_fix_table_row_numbers():
    report = CK.parse_proof_report(_REPORT_REAL_SHAPE)
    rids = [r.rid for r in report.rules]
    assert "ZH-01" not in rids  # 「已自动改」行号表不算命中规则
    assert all(r.count not in (372, 483) for r in report.rules)  # 行号不是条数
    assert sum(r.count for r in report.rules) == 30 and len(report.rules) == 3
    assert [r.rid for r in report.top_rules()] == ["ZH-08", "ZH-07", "ZH-10"]
    assert [r.count for r in report.top_rules()] == [27, 2, 1]
    assert report.manual == 30 and report.total == 33 and report.fixable == 3
    assert report.rule_count == 29
    legacy = CK.parse_proof_report("| `ZH-02` | **2** | 标点 | 手册 |\n")  # 无「## 摘要」退回全文
    assert [r.rid for r in legacy.rules] == ["ZH-02"]


def test_parse_proof_report_fallback_without_header():
    text = ("## 摘要\n| `ZH-02` | **2** | 标点 | 手册 |\n| `EN-02` | **1** | 标点 | 手册 |\n\n"
            "**合计命中 4 处。**\n\n可自动修 3 处（ZH-02）：初排已默认处理，需补改请加 `--fix`。\n")
    report = CK.parse_proof_report(text)
    assert report.manual == 3 and report.total == 4 and report.fixable == 3
    assert report.rule_count == 2 and report.conclusion == ""
    assert CK.parse_proof_report("").ok is False
    assert CK.read_proof_report("C:/no/such/report.md").ok is False


def test_proof_report_and_lang_texts():
    # GUI-08 伴生：报告名要同时认 Windows 与 POSIX 路径（宿主平台无关）
    assert CK.proof_report_name(r"C:\稿\a.tex") == "a_一校核查.md"
    assert CK.proof_report_name("C:/稿/a.tex") == "a_一校核查.md"
    assert CK.proof_report_name(r"稿\a.tex") == "a_一校核查.md"
    assert CK.proof_report_name("/tmp/稿/a.tex") == "a_一校核查.md"
    assert CK.proof_report_name("a:b.tex") == "a:b_一校核查.md"  # POSIX 文件名里的冒号
    assert CK.proof_report_name("a.tex") == "a_一校核查.md"
    assert CK.proof_report_name("") == "<名字>_一校核查.md"
    assert CK.proof_fix_name("a.tex") == "a_一校核查后.tex"
    assert CK.proof_fix_name(r"C:\稿\a.tex") == "a_一校核查后.tex"
    assert CK.proof_fix_name("/tmp/稿/a.tex") == "a_一校核查后.tex"
    assert CK.proof_lang_label("") == "自动"
    assert CK.proof_lang_label("zh") == "中文（zh）" and CK.proof_lang_label("en") == "英文（en）"
    assert CK.proof_result_text(0) == "成功 · 没有命中任何规则。"
    assert CK.proof_result_text(1, tex="a.tex") == \
        "成功 · 有命中，报告已写入稿件同目录的 a_一校核查.md。"
    # Q9（FIX-20261112）：--fix 是否真的另写副本由报告固定行决定，0 改/未知不冒充已另写。
    written = CK.parse_proof_report(
        "修正稿：已另写 `/tmp/稿/a_一校核查后.tex`（自动改 2 处＋位序调整 1 处；输入文件未动）")
    assert written.fix_written and written.fix_path == "/tmp/稿/a_一校核查后.tex"
    assert "修正稿：已另写 `/tmp/稿/a_一校核查后.tex`" in CK.proof_result_text(
        1, tex="a.tex", fix=True, report=written)
    zero = CK.parse_proof_report("修正稿：未另写（未发现可自动修改项；输入文件未动）")
    assert "已另写" not in CK.proof_result_text(1, tex="a.tex", fix=True, report=zero)
    assert "未另写" in CK.proof_result_text(1, tex="a.tex", fix=True, report=zero)
    assert "已另写" not in CK.proof_result_text(1, tex="a.tex", fix=True)  # 未知不冒充
    assert CK.proof_result_text(2).startswith("失败 · 核查未完成（退出码 2）")
    assert CK.proof_result_text(-1) == "失败 · 无法启动。"
    lines = CK.proof_header_lines("a.tex", "zh", True)
    assert lines[0] == "══ 一校核查 ══" and lines[2] == "稿件：a.tex"
    assert lines[3] == "模式：报告 + 修正稿" and lines[4] == "语种：中文（zh）"


# ── 页面钩子（stub page，不跑引擎）────────────────────────────────────────
class _Store:
    def __init__(self):
        self.options = {}
        self.data = self.d = {}

    def get(self, key, default=None):
        if key == "options":
            return self.options
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def set_recent(self, key, value):
        self.data[f"recent.{key}"] = value


class _App:
    def __init__(self):
        self.cancel_requested = False
        self.running = False
        self.last_line = ""
        self.t = Tokens()
        self.store = _Store()
        self.page = SimpleNamespace(update=lambda: None, run_task=lambda *a, **k: None)
        self.engine_status = SimpleNamespace(ok=True, describe=lambda: "ok")
        self.open_path = lambda path: None
        self.notes_repo = "C:/notes"

    def begin_run(self, *args, **kwargs):
        self.running = True

    def finish_run(self, *args, **kwargs):
        self.running = False

    def mark(self, *args, **kwargs):
        pass

    def toast(self, *args, **kwargs):
        pass

    def track_proc(self, *args, **kwargs):
        pass

    def stop_run(self):
        pass

    def browse_field(self, *args, **kwargs):
        pass

    def record_line(self, line):
        pass


class _StubCard:
    def __init__(self):
        self.reset_called = False
        self.shown = []
        self.unreadable = []
        self.failed = []
        self.busy_called = False
        self.root = SimpleNamespace(visible=False)
        self.conclusion = SimpleNamespace(value="", color="")
        self.report_path = ""

    def reset(self):
        self.reset_called = True

    def show(self, payload, report="", report_path=""):
        path = report_path or report
        self.shown.append((payload, path))
        self.report_path = path

    def show_unreadable(self, report=""):
        self.unreadable.append(report)

    def show_failed(self, code):
        self.failed.append(code)

    def busy(self):
        self.busy_called = True

    def _update(self):
        pass


class _Page:
    def __init__(self, flow="proofcheck"):
        self.t = Tokens()
        self.app = _App()
        self.flow_key = flow
        self.fields = {}
        self.lines = []
        self.marks = []
        self._proof = _StubCard()
        self._info = _StubCard()

    def field(self, key):
        controls = self.fields.get(key) or []
        return controls[0].value if controls else ""

    def add_picker(self, label, recent_key, hint, **kwargs):
        control = ft.Text(label)
        self.fields[recent_key] = [control]
        return control

    def mark(self, event, **fields):
        self.marks.append((event, fields))

    def _on_line(self, line):
        self.lines.append(line)

    def _issue_dir(self):
        return "C:/issue"


def _texts(control) -> list[str]:
    found = []
    value = getattr(control, "value", None)
    if isinstance(value, str) and control.__class__.__name__ == "Text":
        found.append(value)
    content = getattr(control, "content", None)
    if content is not None:
        found += _texts(content)
    for child in getattr(control, "controls", None) or []:
        found += _texts(child)
    return found


def test_proof_section_wires_lang_dropdown_and_fix_option():
    page = _Page()
    section = CK.proofcheck_section(page)
    dropdown = page.fields["proof_lang"][0]
    assert isinstance(dropdown, C.Select) and dropdown.label == "语种"
    assert [opt.key for opt in dropdown.options] == ["", "zh", "en"]
    assert [opt.text for opt in dropdown.options] == ["自动", "中文", "英文"]
    labels = _texts(section)
    assert "顺手把无歧义的改掉（另存新文件）" in labels
    assert any("另写一份「一校核查后」副本" in line for line in labels)
    dropdown.value = "zh"
    CK._store_lang(page, dropdown)
    assert page.app.store.get("form.proof_lang") == "zh"
    assert page.field("proof_lang") == "zh"
    assert page.marks == [("proof_lang", {"value": "zh"})]


def test_cards_visible_only_in_their_flow():
    single = _Page("convert")
    card = CK.single_card(single)
    assert card.root.visible is False
    single.flow_key = "proofcheck"
    assert CK.single_card(single).root.visible is True
    info = CK.issue_card(_Page("issue"))
    assert isinstance(info, CK.InfoCheckCard)


def test_on_proof_log_emits_header_and_before_run_resets_card():
    page = _Page()
    page.fields["proof_tex"] = [ft.Text("a.tex")]
    page.app.store.options["proofcheck_fix"] = True
    CK.on_proof_log(page)
    assert len(page.lines) == 5 and page.lines[0] == "══ 一校核查 ══"
    CK.before_run(page)
    assert page._proof.reset_called and page._proof.root.visible is True


def test_after_proof_run_reads_report_and_rewrites_result(tmp_path):
    report_path = tmp_path / "a_一校核查.md"
    report_path.write_text(_REPORT, encoding="utf-8")
    page = _Page()
    page.fields["proof_tex"] = [ft.Text("a.tex")]
    page.app.store.options["proofcheck_fix"] = True
    text, level = CK.after_proof_run(page, None, 1, str(report_path), "旧文案", "warn")
    assert text == CK.proof_result_text(1, tex="a.tex", fix=True)
    assert level == "warn"
    report, path = page._proof.shown[0]
    assert report.manual == 3 and path == str(report_path)


def test_after_proof_run_handles_failure_and_cancel():
    page = _Page()
    page.fields["proof_tex"] = [ft.Text("a.tex")]
    text, _ = CK.after_proof_run(page, None, 2, "", "旧文案", "error")
    assert text.startswith("失败 · 核查未完成（退出码 2）")
    assert page._proof.reset_called and page._proof.root.visible is True
    assert page._proof.conclusion.value.startswith("失败 · 核查未完成")
    page.app.cancel_requested = True
    text, _ = CK.after_proof_run(page, None, 1, "", "⏹ 已取消", "warn")
    assert text == "⏹ 已取消"


def test_after_issue_step_uses_preview_json(monkeypatch):
    page = _Page("issue")
    captured = {}

    def fake_read(call, **kwargs):
        captured["call"] = call
        return {"ok": True, "json": _payload(), "code": 0}

    monkeypatch.setattr(EB, "read_json_command", fake_read)
    asyncio.run(CK.after_issue_step(page, "infocheck", None, 0, False, "C:/report.txt"))
    assert "--preview" in captured["call"].argv
    report, path = page._info.shown[0]
    assert len(report["articles"]) == 2 and path == "C:/report.txt"

    monkeypatch.setattr(EB, "read_json_command", lambda call, **kwargs: {"ok": False})
    asyncio.run(CK.after_issue_step(page, "infocheck", None, 0, False, "C:/report.txt"))
    assert page._info.unreadable == ["C:/report.txt"]
    asyncio.run(CK.after_issue_step(page, "infocheck", None, 2, False, ""))
    assert page._info.failed == [2]
    asyncio.run(CK.after_issue_step(page, "infocheck", None, 0, True, ""))
    assert page._info.reset_called


# ── 真页面接线（run_call 打桩，不跑引擎）────────────────────────────────
def test_single_proof_run_end_to_end(tmp_path, monkeypatch):
    report = tmp_path / "a_一校核查.md"
    report.write_text(_REPORT, encoding="utf-8")
    app = _App()
    page = pages.SinglePage(app)
    page.build()
    page._switch_flow("proofcheck")
    page.fields["proof_tex"][0].value = "C:/x/a.tex"

    async def fake_run(call, **kwargs):
        app.last_line = (f"完成。一校核查：命中 3 处（可自动改 2 处）"
                         f"（报告：{report.as_posix()}）")
        return 1

    monkeypatch.setattr(EB, "run_call", fake_run)
    asyncio.run(page._run(page._build_call()))
    assert page._proof.report_path == report.as_posix()
    assert page._proof.counts["manual"] == 3 and page._proof.counts["total"] == 5
    assert page._proof.root.visible is True


def test_issue_infocheck_run_fills_card(tmp_path, monkeypatch):
    report = tmp_path / "信息检查1.txt"
    report.write_text("report", encoding="utf-8")
    app = _App()
    page = pages.IssuePage(app)
    page.build()
    page.fields["issue_dir"][0].value = str(tmp_path)

    async def fake_run(call, **kwargs):
        app.last_line = (f"完成。检查 2 篇（有 tex 2 / 有 pdf 1；不一致 1 项）"
                         f"（报告：{report.as_posix()}）")
        return 0

    monkeypatch.setattr(EB, "run_call", fake_run)
    monkeypatch.setattr(EB, "read_json_command",
                        lambda call, **kwargs: {"ok": True, "json": _payload()})
    asyncio.run(page._run_calls([("infocheck", EB.infocheck_call(folder=str(tmp_path)))]))
    assert page._info.report == report.as_posix()
    assert len(page._info.articles) == 2
    assert page._info.summary.value.startswith("共 2 篇；发现 1 处需要核对：")
