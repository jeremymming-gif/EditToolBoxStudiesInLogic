"""2026-11-17 修复：设置页「私有笔记仓」字段必须自己说清楚该填什么。

用户报障：把「引用库路径」填成 `<笔记仓>/bib_db`，之后一直提示缺引用库。
根因在引擎侧 —— `Engine/slcn/paths.py:160 notes_root()` 只认**下面有 `cases/`
子目录**的目录；`bib_db/` 里没有 `cases/`（它在笔记仓根下），所以正确填法是
笔记仓根目录。UI 之前只给了 6 个字「私有笔记仓（可空）」，等于没说。

锁四件事（不建窗口、不跑引擎）：
1. `notes_repo` 字段的占位提示里出现「cases/」，常显说明里出现「bib_db」——
   用户在当前页就能看懂该填哪一层；
2. 字段名/标签不变（设置键仍是 `flet.paths.notes_repo`，老配置继续有效）；
3. `_path_field` 扩参全是默认值：不传 `hint`/`note` 的调用点（含
   `engine_override` 与测试里的直接调用）控件树与扩参前完全一致；
4. 说明文案是常显 caption，不是 `show_note` 那条「可直接粘贴路径」（本页关着）。
"""
from __future__ import annotations

import inspect

import flet as ft

from app.ui import pages as P
from test_b112_post_b import _walk
from test_b115_issue_settings import _build_settings

# 提示必须点到这两层目录的差别，否则用户还是会填 bib_db。
HINT_MUST = ("cases/",)
NOTE_MUST = ("cases/", "bib_db")


def _build_paths_settings(tmp_path):
    page, root = _build_settings(tmp_path)
    page.activate("paths")
    return page, root


def _picker_column(root: ft.Control, field: ft.Control) -> ft.Column:
    """找到直接承载该输入行的 `C.path_picker` 外层 Column（取最内层那个）。"""
    matches = []
    for column in [ctl for ctl in _walk(root) if isinstance(ctl, ft.Column)]:
        rows = [ctl for ctl in (column.controls or [])
                if isinstance(ctl, ft.Control)
                and any(child is field for child in _walk(ctl))]
        if rows:
            matches.append(column)
    if not matches:
        raise AssertionError("没找到承载该输入行的 path_picker Column")
    # 设置页外面还套着分组 block，取控件最少的那层才是 path_picker 自己的 Column。
    return min(matches, key=lambda column: len(list(_walk(column))))


def _caption_texts(column: ft.Column) -> list[str]:
    return [str(ctl.value) for ctl in (column.controls or [])
            if isinstance(ctl, ft.Text)]


# ── 1. 用户能在字段上直接看到「填根目录、别填 bib_db」 ────────────────────
def test_notes_repo_field_shows_root_dir_hint(tmp_path):
    page, root = _build_paths_settings(tmp_path)
    field = page.fields["notes_repo"][0]
    assert field.hint_text, "notes_repo 输入框必须有占位提示"
    for token in HINT_MUST:
        assert token in field.hint_text, f"占位提示缺 {token!r}：{field.hint_text!r}"

    column = _picker_column(root, field)
    captions = _caption_texts(column)
    assert captions, "notes_repo 字段下方必须有常显说明"
    joined = "".join(captions)
    for token in NOTE_MUST:
        assert token in joined, f"说明缺 {token!r}：{joined!r}"


def test_notes_repo_caption_is_not_the_paste_note(tmp_path):
    """本页 `show_note=False`；新说明是独立 caption，不复活「可粘贴」提示。"""
    page, root = _build_paths_settings(tmp_path)
    field = page.fields["notes_repo"][0]
    column = _picker_column(root, field)
    assert not any("可直接粘贴路径" in value for value in _caption_texts(column))


# ── 2. 字段名 / 标签不变：老配置 `flet.paths.notes_repo` 继续有效 ──────────
def test_notes_repo_key_and_label_unchanged(tmp_path):
    page, _root = _build_paths_settings(tmp_path)
    assert "notes_repo" in page.fields, "设置键名不能改"
    field = page.fields["notes_repo"][0]
    assert getattr(field, "_form_label", None) == "私有笔记仓（可空）"


# ── 3. 扩参全默认：其它调用点控件树逐项不变 ───────────────────────────────
def test_path_field_new_params_are_optional():
    sig = inspect.signature(P.SettingsPage._path_field)
    for name in ("hint", "note"):
        assert name in sig.parameters, f"_path_field 缺参数 {name}"
        assert sig.parameters[name].default is not inspect.Parameter.empty, \
            f"{name} 必须有默认值（否则其它调用点全炸）"
    assert sig.parameters["hint"].default == ""
    assert sig.parameters["note"].default is None
    # 既有参数与默认值不许动。
    assert sig.parameters["kind"].default == "dir"


def test_path_field_without_hint_matches_old_tree(tmp_path):
    """不传 hint/note：Column 仍只有输入行（+ 浏览按钮）一项，占位回落到「路径」。"""
    page, _root = _build_settings(tmp_path)
    control = page._path_field("engine_override", "引擎目录（可空）")
    assert isinstance(control, ft.Column)
    assert len(control.controls) == 1, "默认不该追加说明文案"
    field = page.fields["engine_override"][0]
    assert field.hint_text == "路径"  # path_picker 的兜底占位，与扩参前一致
    assert getattr(field, "_form_label", None) == "引擎目录（可空）"


def test_engine_override_field_has_no_extra_caption(tmp_path):
    page, root = _build_paths_settings(tmp_path)
    field = page.fields["engine_override"][0]
    column = _picker_column(root, field)
    assert _caption_texts(column) == []


# ── 4. hint / note 各自独立生效（任一缺省都不影响另一条） ─────────────────
def test_path_field_hint_and_note_are_independent(tmp_path):
    page, _root = _build_settings(tmp_path)
    only_hint = page._path_field("probe_hint", "只给提示（可空）", hint="占位文案")
    assert len(only_hint.controls) == 1
    assert page.fields["probe_hint"][0].hint_text == "占位文案"

    only_note = page._path_field("probe_note", "只给说明（可空）", note="说明文案")
    assert len(only_note.controls) == 2
    assert isinstance(only_note.controls[-1], ft.Text)
    assert only_note.controls[-1].value == "说明文案"
    assert page.fields["probe_note"][0].hint_text == "路径"
