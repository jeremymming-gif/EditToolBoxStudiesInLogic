"""批 109 W2a：初排转换的人工元数据表单与只读预览（CV-001..008）。

契约与旧壳 `sources-mac/Features/MetadataFormFeature.swift`、引擎
`Engine/slcn/convert_metadata.py` 同源：

- ``schema_version = 1``；标量键 title/running_title/abstract/keywords × cn/en；
- ``authors`` 1–5 位（模板 myfirstauthor…myfifthauthor 五个槽位）；
- 每位作者每种语言单位 ≤3 行（模板版式容量，第 4 行起溢版）；
- 空串 / 空数组 = 「未提供」= **省略该键**（= 不覆盖正文解析结果）。

纯逻辑（`MetadataForm` 及其辅助函数）不依赖 Flet 控件，测试直接断言 JSON 与
argv ↔ 表单字段的互转；`MetadataPanel` 只负责把同一份状态渲染成控件。
"""

from __future__ import annotations

import json
import os
import tempfile
import time

import flet as ft

from . import components as C

SCHEMA_VERSION = 1
MAX_AUTHORS = 5
MAX_AFFILIATIONS = 3

AUTHOR_LIMIT_SUMMARY = "作者最多 5 位（模板固定 5 个槽位）。"
AFFILIATION_LIMIT_SUMMARY = "单位最多 3 行（每位作者、每种语言；第 4 行溢版）。"
LIMIT_SUMMARY = AUTHOR_LIMIT_SUMMARY + AFFILIATION_LIMIT_SUMMARY
AUTHOR_LIMIT_HINT = "作者已满 5 位。先删空作者行；更多署名请合并同单位，或写进正文致谢。"
AFFILIATION_LIMIT_HINT = (
    "第 {n} 位作者的{lang}单位已到 3 行上限，第 4 行会溢版。删空行或合并到一行。"
)

WARN_NO_AUTHOR = "未填作者：仍用引擎自动解析的结果。"
WARN_EMPTY_AUTHOR = "第 {n} 位作者为空，已跳过。"
WARN_BAD_EMAIL = "第 {n} 位作者邮箱格式可疑：{email}（只提示）。"
WARN_NO_NAME = "第 {n} 位作者没有姓名（只有单位/邮箱），该槽位会留占位。"

#: (键, 标签, 是否多行)
SCALARS = (
    ("title_cn", "中文题名", False),
    ("title_en", "英文题名", False),
    ("abstract_cn", "中文摘要", True),
    ("abstract_en", "英文摘要", True),
    ("keywords_cn", "中文关键词", False),
    ("keywords_en", "英文关键词", False),
)
PLACEHOLDER = {
    "title_cn": "留空则用自动解析结果",
    "title_en": "留空则用自动解析结果",
    "abstract_cn": "留空则用自动解析结果",
    "abstract_en": "留空则用自动解析结果",
    "keywords_cn": "如：逻辑学；语义学",
    "keywords_en": "如：logic; semantics",
}
KEYWORDS_NOTE = "英文关键词两个模板都没有槽位；填了不会丢，但不会自动排版。"
FORM_TITLE = "元数据"
FORM_NOTE = "写入交付产物；留空 = 不覆盖。"
FORM_PRIORITY_NOTE = "优先于 .tex 元数据；临时文件跑完即删。"
FORM_COOP_NOTE = "两段式：表单随第一段写入；第二段沿用，改表单不生效。"

PREVIEW_TITLE = "元数据预览"
PREVIEW_FAILED = "预览失败：没拿到引擎结果（详见日志）。未写任何文件。"
PREVIEW_EMPTY = "（还没有预览；先点「预览元数据」。）"
PREVIEW_FOOTER = "状态：已填 · 冲突-未采用 · 未识别 · 缺项。只读预览，不写文件。"
CONFLICT_HEADER = "冲突（已有该字段，未采用）"
UNRECOGNIZED_HEADER = "未识别（对不上模板槽位）"
HINT_HEADER = "提示"


# ── 纯逻辑 ──────────────────────────────────────────────────────────────────
def _trim(value) -> str:
    return " ".join(str(value or "").split())


def looks_like_email(raw: str) -> bool:
    """旧壳同名判据：只提示不阻断。"""
    s = _trim(raw)
    if not s or " " in s or s.count("@") != 1:
        return False
    local, _, domain = s.partition("@")
    if not local or not domain or "." not in domain:
        return False
    if domain.startswith((".", "-")) or domain.endswith((".", "-")):
        return False
    return True


class Author:
    def __init__(self, name_cn: str = "", name_en: str = "", email: str = "",
                 affiliations_cn=None, affiliations_en=None) -> None:
        self.name_cn = name_cn
        self.name_en = name_en
        self.email = email
        self.affiliations_cn = list(affiliations_cn or [])
        self.affiliations_en = list(affiliations_en or [])

    # 单位行数按「非空行」算（空行不写进产物，也不占模板行数判断）
    def affil(self, lang: str) -> list[str]:
        return self.affiliations_cn if lang == "cn" else self.affiliations_en

    def has_content(self) -> bool:
        return bool(_trim(self.name_cn) or _trim(self.name_en) or _trim(self.email)
                    or any(_trim(a) for a in self.affiliations_cn)
                    or any(_trim(a) for a in self.affiliations_en))

    def to_dict(self) -> dict:
        out: dict = {}
        for key in ("name_cn", "name_en", "email"):
            value = _trim(getattr(self, key))
            if value:
                out[key] = value
        for lang, key in (("cn", "affiliations_cn"), ("en", "affiliations_en")):
            items = [_trim(a) for a in self.affil(lang) if _trim(a)][:MAX_AFFILIATIONS]
            if items:
                out[key] = items
        return out

    def state(self) -> dict:
        return {"name_cn": self.name_cn, "name_en": self.name_en, "email": self.email,
                "affiliations_cn": list(self.affiliations_cn),
                "affiliations_en": list(self.affiliations_en)}


class MetadataForm:
    def __init__(self, state=None) -> None:
        self.values: dict[str, str] = {key: "" for key, _, _ in SCALARS}
        self.authors: list[Author] = [Author()]
        if state:
            self.load(state)

    # ── 状态 ────────────────────────────────────────────────────────────
    def load(self, state) -> None:
        try:
            data = json.loads(state) if isinstance(state, str) else dict(state or {})
        except (ValueError, TypeError):
            data = {}
        for key in self.values:
            value = data.get(key)
            self.values[key] = str(value) if isinstance(value, str) else ""
        authors = data.get("authors")
        self.authors = []
        for raw in authors if isinstance(authors, list) else []:
            if isinstance(raw, dict):
                self.authors.append(Author(
                    str(raw.get("name_cn") or ""), str(raw.get("name_en") or ""),
                    str(raw.get("email") or ""),
                    [str(x) for x in raw.get("affiliations_cn") or [] if isinstance(x, str)],
                    [str(x) for x in raw.get("affiliations_en") or [] if isinstance(x, str)],
                ))
        if not self.authors:
            self.authors = [Author()]

    def state(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, **self.values,
                "authors": [a.state() for a in self.authors]}

    def clear(self) -> None:
        self.values = {key: "" for key, _, _ in SCALARS}
        self.authors = [Author()]

    # ── 作者 / 单位增删（旧壳规则）──────────────────────────────────────
    def is_full(self) -> bool:
        return len(self.authors) >= MAX_AUTHORS

    def add_author(self) -> bool:
        if self.is_full():
            return False
        self.authors.append(Author())
        return True

    def remove_author(self, index: int) -> bool:
        """第一位永不可删（模板按连续作者数排版）。"""
        if index <= 0 or index >= len(self.authors):
            return False
        del self.authors[index]
        return True

    def add_affiliation(self, index: int, lang: str) -> bool:
        if not 0 <= index < len(self.authors):
            return False
        lines = self.authors[index].affil(lang)
        if len(lines) >= MAX_AFFILIATIONS:
            return False
        lines.append("")
        return True

    def remove_affiliation(self, index: int, lang: str, position: int) -> bool:
        if not 0 <= index < len(self.authors):
            return False
        lines = self.authors[index].affil(lang)
        if not 0 <= position < len(lines):
            return False
        del lines[position]
        return True

    def affiliation_limit_hint(self, index: int, lang: str) -> str:
        return AFFILIATION_LIMIT_HINT.format(n=index + 1, lang="中文" if lang == "cn" else "英文")

    # ── 统计 / 校验 / 写出 ──────────────────────────────────────────────
    def kept_authors(self) -> list[Author]:
        return [a for a in self.authors if a.has_content()]

    def has_any_value(self) -> bool:
        return bool(any(_trim(v) for v in self.values.values()) or self.kept_authors())

    def author_count_for_write(self) -> int:
        return len(self.kept_authors())

    def filled_field_count(self) -> int:
        count = sum(1 for v in self.values.values() if _trim(v))
        for author in self.kept_authors():
            count += sum(1 for key in ("name_cn", "name_en", "email")
                         if _trim(getattr(author, key)))
            count += sum(1 for line in author.affiliations_cn if _trim(line))
            count += sum(1 for line in author.affiliations_en if _trim(line))
        return count

    def summary_line(self) -> str:
        return f"将写入 {self.author_count_for_write()} 位作者 / {self.filled_field_count()} 项字段"

    def warnings(self) -> list[str]:
        out: list[str] = []
        if not self.kept_authors():
            out.append(WARN_NO_AUTHOR)
        for index, author in enumerate(self.authors):
            n = index + 1
            if not author.has_content():
                if len(self.authors) > 1:
                    out.append(WARN_EMPTY_AUTHOR.format(n=n))
                continue
            email = _trim(author.email)
            if email and not looks_like_email(email):
                out.append(WARN_BAD_EMAIL.format(n=n, email=email))
            if not (_trim(author.name_cn) or _trim(author.name_en)):
                out.append(WARN_NO_NAME.format(n=n))
        return out

    def json_object(self) -> dict:
        out: dict = {"schema_version": SCHEMA_VERSION}
        for key, _, _ in SCALARS:
            value = _trim(self.values[key])
            if value:
                out[key] = value
        authors = [a.to_dict() for a in self.kept_authors()]
        authors = [a for a in authors if a]
        if authors:
            out["authors"] = authors
        return out if len(out) > 1 else {}

    def json_string(self) -> str:
        """单行 + sortedKeys：同一份表单两次输出逐字节相同（旧壳口径）。"""
        return json.dumps(self.json_object(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))

    def temp_path(self, stem: str) -> str:
        """``slcn_metadata_<stem>_<yyyyMMdd-HHmmss>_<pid>.json``（系统临时目录）。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = f"slcn_metadata_{sanitize_stem(stem)}_{stamp}_{os.getpid()}.json"
        return os.path.join(tempfile.gettempdir(), name)

    def write_temp(self, stem: str) -> str:
        path = self.temp_path(stem)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.json_object(), f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        return path


def sanitize_stem(stem: str) -> str:
    base = os.path.splitext(os.path.basename(str(stem or "")))[0]
    cleaned = "".join("_" if ch in '/:\\\0' else ch for ch in base).strip()[:80]
    return cleaned or "metadata"


def remove_temp(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


def prefill_first_author(form: MetadataForm, candidate: str) -> bool:
    """单向预填：仅当第一作者中文名为空且候选非空；不覆盖已有输入。"""
    name = _trim(candidate)
    if not name or not form.authors:
        return False
    first = form.authors[0]
    if _trim(first.name_cn):
        return False
    first.name_cn = name
    return True


# ── 预览（只读 JSON → 可渲染行）──────────────────────────────────────────
def preview_hint(payload: dict) -> str:
    channel = "Word 通道" if str(payload.get("channel") or "") == "word" else "LaTeX 通道"
    lang = str(payload.get("lang") or "zh")
    return (f"{channel} · 语种 {lang} · 共 {len(payload.get('rows') or [])} 行；"
            "只读预览，不写文件。")


def preview_view(payload) -> dict:
    """引擎预览 JSON → 分区渲染结构；缺字段一律降级为空，不抛异常。"""
    if not isinstance(payload, dict) or "rows" not in payload:
        return {"ok": False, "message": PREVIEW_FAILED, "hint": "", "rows": [],
                "conflicts": [], "unrecognized": [], "hints": []}
    rows = []
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        rows.append({
            "label": str(row.get("label") or row.get("field") or ""),
            "value": str(row.get("value") or ""),
            "source": str(row.get("source") or ""),
            "status": str(row.get("status") or ""),
            "note": str(row.get("note") or ""),
        })
    conflicts = [r for r in rows if r["status"].startswith("冲突")]
    unrecognized = [str(x) for x in payload.get("unrecognized") or [] if str(x).strip()]
    hints = [str(x) for x in (payload.get("warnings") or payload.get("hints") or [])
             if str(x).strip()]
    return {"ok": True, "message": "", "hint": preview_hint(payload), "rows": rows,
            "conflicts": conflicts, "unrecognized": unrecognized, "hints": hints}


# ── Flet 面板 ─────────────────────────────────────────────────────────────
class MetadataPanel:
    """把 `MetadataForm` 渲染成折叠区里的控件；状态存 store 的 form.metadata_draft。"""

    DRAFT_KEY = "form.metadata_draft"

    def __init__(self, page) -> None:
        self.page = page
        self.form = MetadataForm(page.app.store.get(self.DRAFT_KEY, "") or "")
        self._payload = None
        self._body = None
        self._preview_box = None
        self._summary = None

    # ── 持久化 ──────────────────────────────────────────────────────────
    def _save(self) -> None:
        try:
            self.page.app.store.set(
                self.DRAFT_KEY, json.dumps(self.form.state(), ensure_ascii=False))
        except Exception:  # noqa: BLE001 草稿持久化失败不影响填写
            pass

    # ── 控件工厂 ────────────────────────────────────────────────────────
    def _field(self, value: str, label: str, placeholder: str, width: float, *,
               multiline: bool = False, on_change=None) -> ft.Control:
        t = self.page.t
        field = C.text_field(
            t, value=value, hint_text=placeholder,
            width=t.px(width) if width else None,
            expand=True if not width else None,
            multiline=multiline, min_lines=2 if multiline else 1,
            max_lines=5 if multiline else 1,
            on_change=on_change,
        )
        return C.form_field(t, label, field, expand=True if not width else None)

    def _small_button(self, label: str, on_click, *, disabled: bool = False):
        return C.secondary_button(label, on_click, self.page.t, disabled=disabled)

    # ── 渲染 ────────────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        t = self.page.t
        self._summary = C.text("", t, "caption", t.palette.text_faint)
        self._body = ft.Column(spacing=t.sp("1"), controls=[])
        self._preview_box = ft.Column(spacing=t.sp("1"), controls=[])
        self._render_body()
        self._render_preview()
        head = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[
                          C.text(FORM_TITLE, t, "small", t.palette.text),
                          ft.Container(expand=True),
                          self._small_button("清空表单", self._on_clear),
                      ])
        return C.card(ft.Column(spacing=t.sp("1"), controls=[
            head,
            C.text(FORM_NOTE, t, "caption", t.palette.text_faint),
            C.text(LIMIT_SUMMARY, t, "caption", t.palette.text_faint),
            C.text(FORM_PRIORITY_NOTE, t, "caption", t.palette.text_faint),
            C.text(FORM_COOP_NOTE, t, "caption", t.palette.text_faint),
            self._body,
            self._summary,
            self._preview_box,
        ]), t)

    def _render_body(self) -> None:
        t = self.page.t
        controls: list = []
        row: list = []
        for key, label, multiline in SCALARS:
            field = self._field(
                self.form.values.get(key, ""), label, PLACEHOLDER[key],
                0 if multiline else 320, multiline=multiline,
                on_change=lambda e, k=key: self._on_scalar(k, e.control.value))
            if multiline:
                if row:
                    controls.append(ft.Row(spacing=t.sp("1"), controls=row))
                    row = []
                controls.append(field)
            else:
                row.append(ft.Container(expand=True, content=field))
                if len(row) == 2:
                    controls.append(ft.Row(spacing=t.sp("1"), controls=row))
                    row = []
        if row:
            controls.append(ft.Row(spacing=t.sp("1"), controls=row))
        controls.append(C.text(KEYWORDS_NOTE, t, "caption", t.palette.text_faint))
        controls.append(C.divider(t))
        controls.append(ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[
                                   C.text(f"作者（最多 {MAX_AUTHORS} 位）", t, "small", t.palette.text),
                                   C.text(f"{len(self.form.authors)}/{MAX_AUTHORS}", t, "caption",
                                          t.palette.text_faint),
                                   ft.Container(expand=True),
                                   self._small_button("添加作者", self._on_add_author,
                                                      disabled=self.form.is_full()),
                               ]))
        if self.form.is_full():
            controls.append(C.notice_bar(AUTHOR_LIMIT_HINT, t, tone="warning"))
        for index, author in enumerate(self.form.authors):
            controls.append(self._author_card(index, author))
        self._body.controls = controls
        self._refresh_summary()

    def _author_card(self, index: int, author: Author) -> ft.Control:
        t = self.page.t
        title = "第一作者" if index == 0 else f"作者 {index + 1}"
        header: list = [C.text(title, t, "small", t.palette.text)]
        if index > 0:
            header.append(ft.Container(expand=True))
            # 用户 m02990：破坏性动作「删除作者」→ danger 按钮，不再是无样式文字链。
            header.append(C.danger_button("删除", lambda e, i=index: self._on_remove_author(i), t,
                                          icon="DELETE_OUTLINE"))
        rows: list = [ft.Row(spacing=t.sp("1"), controls=header),
                      ft.Row(spacing=t.sp("1"), controls=[
                          ft.Container(expand=True, content=self._field(
                              author.name_cn, "中文姓名", "", 0,
                              on_change=lambda e, i=index: self._on_author(i, "name_cn", e.control.value))),
                          ft.Container(expand=True, content=self._field(
                              author.name_en, "英文姓名", "", 0,
                              on_change=lambda e, i=index: self._on_author(i, "name_en", e.control.value))),
                          ft.Container(expand=True, content=self._field(
                              author.email, "邮箱", "", 0,
                              on_change=lambda e, i=index: self._on_author(i, "email", e.control.value))),
                      ])]
        for lang, label in (("cn", "单位（中文）"), ("en", "单位（英文）")):
            lines = author.affil(lang)
            # FIX-20261112 Q10 AFF-LABEL-01：两语言区块的按钮与计数各自带语言标识，
            # 否则 3 态（0/1/3 行）下两个「添加单位」和两个「n/3」无法区分是哪一种单位。
            lang_short = "中文" if lang == "cn" else "英文"
            affil_rows = [
                # 输入框 48（浮动标签）→「删」按钮底边与控制行齐平（用户 m02816）。
                C.field_row(
                    ft.Container(expand=True, content=self._field(
                        line, f"{label} {pos + 1}", "", 0,
                        on_change=lambda e, i=index, lg=lang, p=pos:
                            self._on_affiliation(i, lg, p, e.control.value))),
                    # 删除单位是破坏性动作 → danger 按钮（同行 48 高，与输入框齐平）。
                    C.danger_button("删", lambda e, i=index, lg=lang, p=pos:
                                    self._on_remove_affiliation(i, lg, p), t),
                    t) for pos, line in enumerate(lines)
            ]
            limit = self.form.affiliation_limit_hint(index, lang)
            affil_rows.append(ft.Row(spacing=t.sp("1"), controls=[
                C.text(f"{len(lines)}/{MAX_AFFILIATIONS} {lang_short}单位", t, "caption",
                       t.palette.text_faint),
                self._small_button(f"添加{lang_short}单位", lambda e, i=index, lg=lang:
                                   self._on_add_affiliation(i, lg),
                                   disabled=len(lines) >= MAX_AFFILIATIONS),
            ]))
            if len(lines) >= MAX_AFFILIATIONS:
                affil_rows.append(C.text(limit, t, "caption", t.palette.text_faint))
            rows.extend(affil_rows)
        return C.card(ft.Column(spacing=t.sp("1"), controls=rows), t, pad=12)

    def _render_preview(self) -> None:
        if self._preview_box is None:
            return
        t = self.page.t
        if self._payload is None:
            self._preview_box.controls = [C.text(PREVIEW_EMPTY, t, "caption", t.palette.text_faint)]
            return
        view = preview_view(self._payload)
        controls: list = [C.section_title(PREVIEW_TITLE, t)]
        if not view["ok"]:
            controls.append(C.notice_bar(view["message"], t, tone="warning"))
            self._preview_box.controls = controls
            return
        controls.append(C.text(view["hint"], t, "caption", t.palette.text_faint))
        if view["conflicts"]:
            controls.append(C.text(CONFLICT_HEADER, t, "caption", t.palette.warn))
            for row in view["conflicts"]:
                controls.append(C.text(
                    f"　{row['label']}：{row['value'] or '—'}（{row['note'] or row['source']}）",
                    t, "caption", t.palette.warn))
        if view["unrecognized"]:
            controls.append(C.text(UNRECOGNIZED_HEADER, t, "caption", t.palette.text_faint))
            for item in view["unrecognized"]:
                controls.append(C.text(f"　{item}", t, "caption", t.palette.text_faint))
        if view["hints"]:
            controls.append(C.text(HINT_HEADER, t, "caption", t.palette.warn))
            for item in view["hints"]:
                controls.append(C.text(f"　{item}", t, "caption", t.palette.warn))
        for row in view["rows"]:
            controls.append(C.text(
                f"{row['label']}：{row['value'] or '—'}"
                + (f"　［{row['source']}］" if row["source"] else "")
                + (f"　{row['status']}" if row["status"] else ""),
                t, "caption", (t.palette.warn if row["status"].startswith("冲突")
                               else t.palette.text_faint)))
        controls.append(C.text(PREVIEW_FOOTER, t, "caption", t.palette.text_faint))
        self._preview_box.controls = controls

    def _refresh_summary(self) -> None:
        if self._summary is None:
            return
        t = self.page.t
        self._summary.color = t.palette.text_faint
        parts = [self.form.summary_line()]
        for warning in self.form.warnings():
            parts.append("注意 · " + warning)
        self._summary.value = "\n".join(parts)

    # ── 事件 ────────────────────────────────────────────────────────────
    def _after_change(self, rerender: bool = False) -> None:
        self._save()
        if rerender:
            self._render_body()
        self._refresh_summary()
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.page.root.update()
        except Exception:  # noqa: BLE001 控件未挂载时忽略
            pass

    def _on_scalar(self, key: str, value: str) -> None:
        self.form.values[key] = value
        self._after_change()

    def _on_author(self, index: int, key: str, value: str) -> None:
        if 0 <= index < len(self.form.authors):
            setattr(self.form.authors[index], key, value)
            self._after_change()

    def _on_affiliation(self, index: int, lang: str, pos: int, value: str) -> None:
        if 0 <= index < len(self.form.authors):
            lines = self.form.authors[index].affil(lang)
            if 0 <= pos < len(lines):
                lines[pos] = value
                self._after_change()

    def _on_add_author(self, e=None) -> None:
        if self.form.add_author():
            self._after_change(rerender=True)

    def _on_remove_author(self, index: int) -> None:
        if self.form.remove_author(index):
            self._after_change(rerender=True)

    def _on_add_affiliation(self, index: int, lang: str) -> None:
        if self.form.add_affiliation(index, lang):
            self._after_change(rerender=True)

    def _on_remove_affiliation(self, index: int, lang: str, pos: int) -> None:
        if self.form.remove_affiliation(index, lang, pos):
            self._after_change(rerender=True)

    def _on_clear(self, e=None) -> None:
        self.form.clear()
        self._payload = None
        self._after_change(rerender=True)
        self._render_preview()
        self._safe_update()

    # ── 与运行管线对接 ──────────────────────────────────────────────────
    def payload_for_run(self) -> tuple[str, str]:
        """写出临时 JSON；返回 (path, json 文本)。空表单返回 ("", "")。

        运行结束后调用方必须 `remove_temp(path)`（旧壳：运行结束删除临时 JSON）。
        """
        if not self.form.has_any_value():
            return "", ""
        path = self.form.write_temp(self.page.field("single_source") or "metadata")
        return path, self.form.json_string()

    def show_preview(self, payload) -> None:
        self._payload = payload
        self._render_preview()
        self._safe_update()
