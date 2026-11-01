"""批 109 W2a：参考文献规范化页 + 初排元数据三入口（P0 NR-001、CV-001..005）回归。

只测外壳的取值/拼参/文案与只读边界；不跑引擎、不碰真实引用库。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import engine_bridge as EB  # noqa: E402
from ui import metadata_form as MF  # noqa: E402
from ui import normalize as NR  # noqa: E402


# ── 元数据三入口：JSON 契约 ───────────────────────────────────────────────
def test_empty_form_writes_nothing():
    form = MF.MetadataForm()
    assert form.json_object() == {}
    assert form.json_string() == "{}"
    assert form.summary_line() == "将写入 0 位作者 / 0 项字段"


def test_form_json_omits_empty_and_keeps_schema_version():
    form = MF.MetadataForm()
    form.values["title_cn"] = "  逻辑多元论  "
    form.authors[0].name_cn = "张三"
    form.authors[0].affiliations_cn = [" 中山大学 ", ""]
    data = form.json_object()
    assert data["schema_version"] == 1
    assert data["title_cn"] == "逻辑多元论"
    assert "title_en" not in data and "keywords_cn" not in data
    assert data["authors"] == [{"name_cn": "张三", "affiliations_cn": ["中山大学"]}]


def test_form_json_string_is_sorted_and_stable():
    form = MF.MetadataForm()
    form.values["title_en"] = "Logic"
    form.values["abstract_cn"] = "摘要"
    once, twice = form.json_string(), form.json_string()
    assert once == twice
    keys = [k for k in json.loads(once)]
    assert keys == sorted(keys)


def test_author_and_affiliation_limits_follow_template():
    form = MF.MetadataForm()
    for _ in range(MF.MAX_AUTHORS - 1):
        assert form.add_author() is True
    assert form.add_author() is False  # 第 6 位没有槽位
    assert form.remove_author(0) is False  # 第一位永不可删
    assert form.remove_author(1) is True
    for _ in range(MF.MAX_AFFILIATIONS):
        form.add_affiliation(0, "cn")
    assert form.add_affiliation(0, "cn") is False
    assert len(form.authors[0].affiliations_cn) == MF.MAX_AFFILIATIONS


def test_affiliation_limit_hint_matches_old_shell_text():
    form = MF.MetadataForm()
    assert "第 1 位作者的中文单位已到 3 行上限" in form.affiliation_limit_hint(0, "cn")
    assert "英文单位" in form.affiliation_limit_hint(0, "en")


def test_warnings_cover_empty_bad_email_and_no_name():
    form = MF.MetadataForm()
    assert MF.WARN_NO_AUTHOR in form.warnings()
    form.authors.append(MF.Author())
    assert any("位作者为空" in w for w in form.warnings())
    form.authors[0] = MF.Author(email="not-an-email")
    assert any("邮箱格式可疑" in w for w in form.warnings())
    assert any("没有姓名" in w for w in form.warnings())


def test_summary_counts_kept_authors_and_fields():
    form = MF.MetadataForm()
    form.authors[0].name_cn = "张三"
    form.authors[0].email = "z@example.com"
    form.authors.append(MF.Author())  # 空作者不计入
    form.values["title_cn"] = "题名"
    assert form.author_count_for_write() == 1
    assert form.filled_field_count() == 3
    assert form.summary_line() == "将写入 1 位作者 / 3 项字段"


def test_temp_json_name_and_cleanup(tmp_path=None):
    form = MF.MetadataForm()
    form.authors[0].name_cn = "张三"
    path = form.write_temp("初排稿.docx")
    try:
        assert os.path.basename(path).startswith("slcn_metadata_初排稿_")
        assert os.path.basename(path).endswith(f"_{os.getpid()}.json")
        with open(path, encoding="utf-8") as handle:
            assert json.load(handle)["authors"] == [{"name_cn": "张三"}]
    finally:
        MF.remove_temp(path)
    assert not os.path.exists(path)
    assert MF.sanitize_stem("a/b:c") == "b_c"  # 先取基名，再把 /:\0 换成下划线
    assert MF.sanitize_stem("x/y.md") == "y"
    assert MF.sanitize_stem("") == "metadata"


def test_prefill_first_author_is_one_way():
    form = MF.MetadataForm()
    assert MF.prefill_first_author(form, "李四") is True
    assert form.authors[0].name_cn == "李四"
    assert MF.prefill_first_author(form, "王五") is False  # 不覆盖已有输入
    assert form.authors[0].name_cn == "李四"


def test_preview_view_renders_rows_conflicts_and_failure():
    payload = {"schema": 1, "channel": "latex", "lang": "zh", "rows": [
        {"field": "title_cn", "label": "中文题名", "value": "题名", "source": "正文",
         "status": "已填"},
        {"field": "title_en", "label": "英文题名", "value": "", "source": "",
         "status": "冲突", "note": "已有 正文「X」"},
    ], "unrecognized": ["foo"], "warnings": ["坏源"]}
    view = MF.preview_view(payload)
    assert view["ok"] is True
    assert view["hint"].startswith("LaTeX 通道 · 语种 zh · 共 2 行")
    assert view["conflicts"][0]["label"] == "英文题名"
    assert view["unrecognized"] == ["foo"] and view["hints"] == ["坏源"]
    assert MF.preview_view(None)["message"] == MF.PREVIEW_FAILED


# ── 规范化页：报告路径 / 自动填充 / argv ──────────────────────────────────
def test_report_path_defaults():
    assert NR.report_path_for_returned("/x/y/reference_改回.bib").endswith(
        os.path.join("x", "y", "reference_改回_回读校验报告.md"))
    assert NR.report_path_for_returned("") == ""
    assert NR.paste_report_path("/a/b/reference.bib").endswith(
        os.path.join("a", "b", "reference_回读校验报告.md"))
    assert NR.paste_report_path("").endswith(NR.PASTE_REPORT_NAME)
    assert "系统临时目录" in NR.paste_report_note("")


def test_resolved_report_path_empty_dir_and_file(tmp_path):
    auto = os.path.join(str(tmp_path), "x_回读校验报告.md")
    assert NR.resolved_report_path("", auto) == auto
    assert NR.resolved_report_path(str(tmp_path), auto) == auto
    target = os.path.join(str(tmp_path), "out.md")
    assert NR.resolved_report_path(target, auto) == os.path.abspath(target)


def test_product_bib_target_is_sibling_reference():
    source = "/p/01 Han/reference_改回.bib"
    expected = os.path.join(os.path.dirname(os.path.abspath(source)), "reference.bib")
    assert NR.product_bib_target(source) == expected
    assert NR.product_bib_target("") == ""


def test_autofill_siblings_picks_other_bib_and_unique_tex(tmp_path):
    (tmp_path / "reference.bib").write_text("", encoding="utf-8")
    (tmp_path / "reference_改回.bib").write_text("", encoding="utf-8")
    (tmp_path / "main.tex").write_text("", encoding="utf-8")
    original, tex, note = NR.autofill_siblings(
        str(tmp_path / "reference_改回.bib"), "", "")
    assert original.endswith("reference.bib")
    assert tex.endswith("main.tex")
    assert "已填进「原件」" in note
    # 已填值不覆盖
    assert NR.autofill_siblings(str(tmp_path / "reference_改回.bib"), "/keep", "/keep.tex")[:2] == (
        "/keep", "/keep.tex")


def test_preview_text_uses_last_engine_line():
    assert NR.preview_text(0, ["N 条：a, b"], "x") == "识别：N 条：a, b"
    assert NR.preview_text(0, [], "x") == "识别：0 条"
    assert NR.preview_text(2, ["坏"], "x") == "识别失败（退出码 2）：坏"
    assert NR.preview_text(0, ["x"], "") == ""


def test_build_argv_file_and_paste_modes():
    call = NR.build_argv("a.bib", mode=NR.MODE_FILE, original="o.bib", tex="t.tex", out="r.md")
    argv = call.argv
    assert argv[argv.index("--returned"):] == ["--returned", "a.bib", "--original", "o.bib",
                                               "--tex", "t.tex", "--out", "r.md"]
    paste = NR.build_argv("", mode=NR.MODE_PASTE, original="o.bib", out="r.md")
    assert "--returned-stdin" in paste.argv and "--returned" not in paste.argv
    preview = NR.preview_call().argv
    assert preview[preview.index("--list-keys"):] == ["--list-keys", "--returned-stdin"]


def test_no_merge_or_delete_flags_anywhere():
    assert not hasattr(NR, "merge") and "merge_into" not in dir(EB)
    for mode in (NR.MODE_FILE, NR.MODE_PASTE):
        argv = NR.build_argv("a.bib", mode=mode)
        assert not any(flag in ("--merge-into", "--delete", "--empty-trash")
                       for flag in argv.argv)


# ── 初排 argv：元数据三入口 + P1 参数 ────────────────────────────────────
def test_convert_call_metadata_entries_and_force():
    call = EB.convert_call(
        source="s.docx", out="o", metadata="m.docx", metadata_json="/tmp/m.json",
        metadata_preview_out="/tmp/p.json", force=True, lang="en", editor="韩",
        grants="国社科", refs="r.bib", sty="s.cls", skeleton="k.tex",
    )
    argv = call.argv
    for flag, value in (("--metadata-json", "/tmp/m.json"),
                        ("--metadata-preview-out", "/tmp/p.json"),
                        ("--metadata", "m.docx"), ("--lang", "en"),
                        ("--editor", "韩"), ("--grants", "国社科"),
                        ("--refs", "r.bib"), ("--sty", "s.cls"), ("--skeleton", "k.tex")):
        assert argv[argv.index(flag) + 1] == value
    assert "--force" in argv


def test_convert_call_preview_still_needs_no_extra_switch():
    call = EB.convert_call(source="s.tex", out="o", metadata_preview_out="/tmp/p.json")
    assert call.entry == "convert.py" and "--metadata-preview-out" in call.argv
    assert "--metadata-json" not in call.argv  # 空值不传（= 不覆盖）


def test_run_call_accepts_stdin_text():
    import inspect
    params = inspect.signature(EB.run_call).parameters
    assert "stdin_text" in params and params["stdin_text"].default is None


def test_single_page_flow_renamed_and_merge_not_reintroduced():
    from ui import pages
    assert pages.SinglePage.FLOWS["checkbib"]["label"] == "参考文献规范化"
    assert not hasattr(pages.SinglePage, "_on_merge")
    source = open(NR.__file__, encoding="utf-8").read()
    assert "--merge-into" in source  # 只允许出现在「已停用」提示文案里
    assert "merge_back" not in source


# ── 返工（NR-010 报告回显 / 跨流程 stdin 守卫）：最小假壳，不建控件、不跑引擎 ──
class _FakeLog:
    def __init__(self):
        self.lines = []

    def clear(self):
        self.lines.clear()

    def add_line(self, line, level, summary):
        self.lines.append(line)


class _FakeResult:
    def __init__(self):
        self.text = ""

    def reset(self):
        self.text = ""

    def show(self, text, level="info", artifact=""):
        self.text = text


class _FakeStore:
    def set_recent(self, key, path):
        pass


class _FakeApp:
    def __init__(self):
        self.running = False
        self.cancel_requested = False
        self.last_line = ""
        self.notes_repo = None
        self.store = _FakeStore()

    def begin_run(self, call):
        pass

    def finish_run(self, call, code):
        self.code = code

    def mark(self, name, **kwargs):
        pass

    def record_line(self, line):
        pass

    def track_proc(self, proc):
        pass


class _FakeNorm:
    def __init__(self, report=""):
        self.last_report = report
        self.refreshed = 0

    def stdin_text(self):
        return "粘贴的 bib 正文"

    def refresh_after_run(self):
        self.refreshed += 1


class _FakeFletPage:
    def __init__(self):
        self.tasks = []

    def update(self):
        pass

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))


def _fake_single_page(report=""):
    from ui import pages

    class _Fake(pages.SinglePage):
        def __init__(self):
            self.flow_key = "checkbib"
            self._ai = None
            self._norm = _FakeNorm(report)
            self._meta_panel = None
            self._meta_temp = ""
            self._log = _FakeLog()
            self._result = _FakeResult()
            self.app = _FakeApp()
            self.page = _FakeFletPage()

    return _Fake()


def test_checkbib_run_echoes_report_body_into_log(tmp_path, monkeypatch):
    from ui import pages
    report = tmp_path / "reference_回读校验报告.md"
    report.write_text("结论：1 处问题 / 2 处提示\n\n- [问题] 第 2 行：缺标题\n", encoding="utf-8")
    page = _fake_single_page(str(report))
    call = NR.build_argv("a.bib", mode=NR.MODE_FILE, out=str(report))

    async def fake_run_call(call, on_line=None, on_proc=None, api=None, notes=None,
                            stdin_text=None):
        on_line("结论：1 处问题 / 2 处提示")
        return 1

    monkeypatch.setattr(EB, "run_call", fake_run_call)
    asyncio.run(pages.SinglePage._run(page, call))
    joined = "\n".join(page._log.lines)
    assert "== 文本报告：reference_回读校验报告.md ==" in joined
    assert "- [问题] 第 2 行：缺标题" in joined  # 正文真的逐行进日志
    assert page._norm.refreshed == 1


def test_checkbib_run_missing_report_does_not_crash(tmp_path, monkeypatch):
    from ui import pages
    page = _fake_single_page(str(tmp_path / "没有这个报告.md"))
    call = NR.build_argv("a.bib", mode=NR.MODE_FILE)

    async def fake_run_call(call, on_line=None, on_proc=None, api=None, notes=None,
                            stdin_text=None):
        on_line("结论：0 处问题 / 0 处提示")
        return 0

    monkeypatch.setattr(EB, "run_call", fake_run_call)
    asyncio.run(pages.SinglePage._run(page, call))
    joined = "\n".join(page._log.lines)
    assert "结论：0 处问题" in joined and "报告读取失败" in joined
    assert page._result.text  # 结果条照常刷新
    assert page._norm.refreshed == 1


def test_stdin_text_only_passed_for_checkbib_flow():
    page = _fake_single_page()
    page._build_call = lambda preview_path="": NR.build_argv("a.bib", mode=NR.MODE_FILE)
    for flow in ("convert", "proofcheck"):
        page.flow_key = flow
        page._on_run()
        assert page.page.tasks[-1][1][1] is None  # 粘贴正文不漏进不读 stdin 的引擎
    page.flow_key = "checkbib"
    page._on_run()
    assert page.page.tasks[-1][1][1] == "粘贴的 bib 正文"

