"""引用库 · 查库（统一结构化检索；P06 加分页）。

只读检索页：默认使用可组合条件检索，首查 100 条；查询结果
按引擎 ``page`` 六字段（offset/limit/total_matched/has_more/next_offset/db_sha256）
续页，单次 ``--limit`` 仍是 1–200，绝不靠放宽 limit 伪装「载入更多」。命中总数一律
用引擎的 ``total_matched``（不是 ``len(hits)``）；库在分页之间变化时拒绝合并并提示
按当前库重查。右侧 360 详情在 <1100 时折成表格下方的展开面板（面板占满剩余高度、正文内部
滚动、动作行钉在底部；不横向滚动）；筛选行在 <820 时改折行排布，360 宽不裁切。

所有查库请求统一走 ``EB.bibdb_query_advanced_call``，
通过 ``EB.read_json_command`` 执行；测试 monkeypatch 该函数或直接检查 argv。
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import time

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_shell as S
from ui import bib_table as BT
from ui import components as C
from ui.tokens import Tokens

VIEW_KEY = "query"
TITLE = "查库"
ROLE = "readonly"

COMMON_QUERY_FIELDS = (
    ("key", "key", "如 Fine1975"),
    ("alias", "别名", "按别名精确匹配"),
    ("doi", "DOI", "如 10.1007/s10992-020-09558-5"),
    ("author", "作者", "姓名或作者片段"),
    ("title", "题名", "题名关键词"),
    ("year", "年份", "年份或范围，如 1990-2000"),
)
QUERY_FIELD_LABELS = {
    "editor": "编者", "booktitle": "书名", "journal": "期刊",
    "publisher": "出版社", "any": "任意字段",
}
QUERY_FIELD_MODES = {
    "key": "exact", "alias": "exact", "doi": "exact", "year": "exact",
    "author": "contains", "editor": "contains", "title": "contains",
    "booktitle": "contains", "journal": "contains", "publisher": "contains",
    "any": "contains",
}
QUERY_MATCH_OPTIONS = {
    field: [(mode, EB.QUERY_MATCH_LABELS[mode])
            for mode in EB.QUERY_MATCH_MODES
            if mode != "fuzzy" or field not in EB.QUERY_NO_FUZZY]
    for field in EB.QUERY_FIELDS
}
EMPTY_INPUT = "至少填写一项检索条件。"
NO_RESULT = "没有匹配的记录"
NO_RESULT_HINT = "试试放宽年份，或只按题名检索。"
QV_NO_RESULT_HINT = NO_RESULT_HINT
ENGINE_CAP = 200
PAGE_SIZE = 100
DB_CHANGED_MSG = "引用库已变化，请刷新后重新查询（未合并新页）"
PAGE_MISMATCH_MSG = "分页位置不一致，已丢弃本页；请刷新后重新查询（未合并新页）"
DETAIL_W = 360

# R2-3：筛选行固定 352 搜索框 + 三按钮在 820 以下挤不进一行，改折行排布。
FILTER_WRAP_W = 820

# R-05（DEF-02 360×800）：窄窗详情面板不能用「设计宽 × t.px」定宽 —— 父级还有侧栏
# 与内外边距，定宽会超出真实内容区，动作行右端被裁剪、点不到。窄窗改为柔性布局：
# 面板 width=None 由父级拉伸决定，动作行换成 wrap Row 自动折行（见 _fluid_detail）。

# P1-1 详情字段中文注：只影响标签显示；键名与取值仍逐字符来自引擎记录。
FIELD_LABELS = {
    "address": "地址", "annote": "批注", "author": "作者", "booktitle": "书名",
    "chapter": "章节", "crossref": "交叉引用", "date": "日期", "doi": "DOI",
    "edition": "版次", "editor": "编者", "eprint": "预印本号",
    "howpublished": "出版方式", "institution": "机构", "isbn": "ISBN",
    "issn": "ISSN", "journal": "期刊", "key": "键", "language": "语言",
    "month": "月", "note": "附注", "number": "期号", "organization": "组织",
    "pages": "页码", "publisher": "出版社", "school": "学校", "series": "丛书",
    "title": "题名", "type": "类型", "translator": "译者", "url": "URL",
    "volume": "卷", "year": "年份",
}
FIELD_EMPTY = "—"
NO_SOURCES = "无来源记录"
NO_VALUE = "无"
CONFLICT_HINT = "{n} 条 · 去「审核」视图逐字段核对"

# P1-2「原始记录」：只读定位库物理行；正文/复制/校验和全部由同一份 pretty 派生。
RAW_BUTTON = "原始记录"
RAW_TITLE = "原始记录 · {key}"
RAW_DIALOG_W = 720
RAW_MIN_LINES, RAW_MAX_LINES = 12, 16
RAW_NOT_FOUND = "未在当前库中找到该 key"
RAW_NO_RECORD = "无原始记录"
RAW_NO_DB = "未配置引用库路径"
RAW_READ_FAILED = ("读取失败：{kind}；库文件可能被占用或已损坏。"
                   "可关闭后重新查询，或打开库文件核对。")
RAW_NEXT_HINT = "下一步：关闭后重查该 key，或点「打开库文件」核对原始库。"


def _clock(seconds: float) -> str:
    return f"{seconds:.2f} s"


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return "引擎没有返回说明"


def _hit_fields(hit: dict) -> dict:
    record = hit.get("record") or {}
    return record.get("fields") or {}


def _source_of(hit: dict) -> str:
    sources = (hit.get("record") or {}).get("sources") or []
    if not sources:
        return "—"
    first = sources[0] or {}
    issue, slug = str(first.get("issue") or ""), str(first.get("slug") or "")
    return " · ".join(x for x in (issue, slug) if x) or "—"

def _field_label(name) -> str:
    """字段名 → 显示标签：已知字段附中文注（如 pages（页码）），未知字段原样。"""
    raw = str(name)
    note = FIELD_LABELS.get(raw, "")
    return f"{raw}（{note}）" if note else raw


def _field_text(value) -> str:
    """字段值 → 显示文本：空字符串/None 用 —，其余保持值的逐字符表示。"""
    if value is None:
        return FIELD_EMPTY
    if isinstance(value, str):
        return value if value.strip() else FIELD_EMPTY
    if isinstance(value, bool):      # bool 是 int 子类，先判：JSON 里是 true/false
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _source_items(record: dict) -> list[str]:
    """来源逐条：序号 + 期号 + slug + file + kind；缺的字段留 —（不省列）。"""
    items = []
    for index, source in enumerate(record.get("sources") or [], 1):
        if not isinstance(source, dict):
            items.append(f"{index}. {_field_text(source)}")
            continue
        parts = [str(source.get(name) or "").strip() or FIELD_EMPTY
                 for name in ("issue", "slug", "file", "kind")]
        items.append(f"{index}. " + " · ".join(parts))
    return items


def _alias_items(record: dict) -> list[str]:
    return [_field_text(alias) for alias in (record.get("aliases") or [])]


def _pretty_record_text(raw_line: str) -> str:
    """库物理行 → pretty JSON：记录 SHA256 与「复制 JSON」的唯一来源（逐字符）。"""
    return json.dumps(json.loads(raw_line), ensure_ascii=False, indent=2)


def _record_sha256(pretty_text: str) -> str:
    return hashlib.sha256(pretty_text.encode("utf-8")).hexdigest()


def _locate_record(db_path: str, key: str) -> dict:
    """只读逐行定位 key 的物理行：命中即停、坏行跳过、不缓存、不写库。"""
    if not db_path:
        return {"status": "no_db"}
    target = str(key or "")
    if not target:
        return {"status": "no_record"}
    line_no = 0
    try:
        with open(db_path, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line_no += 1
                text = raw.rstrip("\r\n")
                if not text.strip():
                    continue
                try:
                    record = json.loads(text)
                except (TypeError, ValueError):
                    continue  # 坏行跳过：库可能正被写入，别因一行解析失败整只失败
                if not isinstance(record, dict) or str(record.get("key") or "") != target:
                    continue
                total = line_no
                for _rest in handle:   # 只为「总行数」继续数行，不解析、不缓存
                    total += 1
                pretty = _pretty_record_text(text)
                return {"status": "ok", "line": line_no, "total": total,
                        "raw": text, "pretty": pretty,
                        "sha256": _record_sha256(pretty)}
    except OSError as exc:
        return {"status": "read_failed", "kind": type(exc).__name__}
    return {"status": "not_found"}


def hit_row(hit: dict) -> dict:
    """把引擎 hit 映射成表格行；字段缺失回落到 record.entry_type/key。"""
    fields = _hit_fields(hit)
    record = hit.get("record") or {}
    year = fields.get("year") or fields.get("date") or ""
    if year:
        year = str(year)[:4]
    return {
        "title": hit.get("title") or fields.get("title") or "（无题名）",
        "author": hit.get("author") or fields.get("author") or "—",
        "year": year or "—",
        "key": hit.get("key") or record.get("key") or "",
        "status": "已核实" if (hit.get("verified") or record.get("verified")) else "未核实",
        "source": _source_of(hit),
        "_hit": hit,
    }


class QueryView:
    def __init__(self, page) -> None:
        self.page = page
        self.mode = "advanced"
        self.rows: list[dict] = []
        self.hits: list[dict] = []
        self.total_matched: int | None = None
        self.truncated = False
        self.count = 0
        self.limit = 100
        self.loaded = False
        self.running = False
        self.cancel_requested = False
        self.error = ""
        self.input_error = ""
        self.success = ""
        self.cancelled = False
        self.elapsed = 0.0
        self.selected_key: str | None = None
        self.sort_key = "relevance"
        self.sort_desc = False
        self._task_running = False
        self._build_width = 0.0
        # ── P06 分页与复用状态（CONTRACT §5）：page 字段只消费、不重算 ──
        self.query_advanced = True
        self.adv_values: dict[str, str] = {field: "" for field, _, _ in COMMON_QUERY_FIELDS}
        self.adv_modes: dict[str, str] = {field: QUERY_FIELD_MODES[field]
                                          for field, _, _ in COMMON_QUERY_FIELDS}
        self.extra_terms: list[dict[str, str]] = []
        self.adv_strict = False
        self._gen = 0
        self._loaded = 0
        self._total: int | None = None
        self._has_more = False
        self._db_sha = ""
        self._cond: dict | None = None
        self._items: list[dict] = []
        self._page_error = ""

    # ── 基础 ────────────────────────────────────────────────────────────
    @property
    def t(self) -> Tokens:
        return self.page.t

    def _notes(self) -> str:
        return str(getattr(getattr(self.page, "app", None), "notes_repo", "") or "")

    def _db(self) -> str:
        """引用库物理路径：stats_data["db"] → stats_data["path"] → GIT.default_db。

        R2-1（DEF-01）：真实引擎 ``cmd_stats`` 输出的是 ``store.stats()``（只有
        ``path``/``bytes``/``sha256``/``records`` 等，没有 ``db`` 键），
        ``bib_host._load_stats`` 又原样存进 ``stats_data``，所以只读 ``["db"]``
        在真实装配里恒为空。第二级读 ``path``；第三级回退到用户设置里的默认库，
        与 bib_edit / bib_dupes / bib_fieldfix / bib_import 兄弟页同款，
        这样「原始记录」和「打开库文件」在真实 GUI 里共享同一条路径。
        """
        stats = getattr(self.page, "stats_data", None) or {}
        path = ""
        if isinstance(stats, dict):
            path = str(stats.get("db") or stats.get("path") or "")
        if path:
            return path
        try:
            return str(GIT.default_db(self._notes()) or "")
        except Exception:  # noqa: BLE001 —— 设置读不到时由调用方按「未配置路径」提示
            return ""

    def _store(self):
        return getattr(getattr(self.page, "app", None), "store", None)

    def _set_store(self, key: str, value) -> None:
        store = self._store()
        if store is not None:
            try:
                store.set(key, value)
            except Exception:  # noqa: BLE001
                pass

    def _refresh(self) -> None:
        render = getattr(self.page, "_render", None)
        if callable(render):
            render()

    def _mark(self, event: str, **kwargs) -> None:
        marker = getattr(self.page, "mark", None)
        if callable(marker):
            try:
                marker(event, **kwargs)
            except Exception:  # noqa: BLE001
                pass

    def _schedule(self, runner=None) -> None:
        if self._task_running:
            return
        self._task_running = True
        run = getattr(self.page.page, "run_task", None)
        fn = runner or self._run
        if callable(run):
            run(fn)
        else:
            asyncio.run(fn())

    def _schedule_async(self, fn, *args) -> None:
        """把「只读取数」协程交给页面事件循环（flet 只收 coroutine function）。"""
        if self._task_running:
            return
        self._task_running = True
        run = getattr(self.page.page, "run_task", None)
        handler = functools.partial(fn, *args)
        if callable(run):
            run(handler)
        else:
            asyncio.run(handler())

    # ── 生命周期 ────────────────────────────────────────────────────────
    def activate(self) -> None:
        # 无条件时不自动跑查询；首屏显示空态和完整检索条件。
        self.query_advanced = True

    def refresh_running(self) -> None:
        return None

    def primary_action(self):
        return self._query_button()

    # ── 条件 ────────────────────────────────────────────────────────────
    def _on_advanced_field(self, key: str, e) -> None:
        control = getattr(e, "control", e)
        value = str(getattr(control, "value", None)
                    if hasattr(control, "value") else getattr(e, "data", "") or "")
        if value == self.adv_values.get(key, "") and not self.input_error:
            return
        had_error = bool(self.input_error)
        self.adv_values[key] = value
        self.input_error = ""
        # 条件变化（契约 §5）：作废旧分页/在途结果与选择。输入中不整页重建，
        # 免得重建 TextField 丢焦点；真正的首查在回车/「查询」时发起。
        self._reset_paging()
        if had_error:
            self._refresh()

    def _on_extra_value(self, index: int, e) -> None:
        control = getattr(e, "control", e)
        value = str(getattr(control, "value", None)
                    if hasattr(control, "value") else getattr(e, "data", "") or "")
        if index >= len(self.extra_terms):
            return
        if value == self.extra_terms[index].get("value", "") and not self.input_error:
            return
        had_error = bool(self.input_error)
        self.extra_terms[index]["value"] = value
        self.input_error = ""
        self._reset_paging()
        if had_error:
            self._refresh()

    def _on_extra_field(self, index: int, e) -> None:
        field = str(getattr(e, "data", None) or
                    getattr(getattr(e, "control", None), "value", "") or "editor")
        if index >= len(self.extra_terms) or field not in QUERY_FIELD_LABELS:
            return
        self.extra_terms[index]["field"] = field
        self.extra_terms[index]["mode"] = QUERY_FIELD_MODES[field]
        self._reset_paging()
        self._refresh()

    def _on_match_mode(self, field: str, e) -> None:
        mode = str(getattr(e, "data", None) or
                   getattr(getattr(e, "control", None), "value", "") or "")
        if mode not in {key for key, _ in QUERY_MATCH_OPTIONS[field]}:
            return
        if self.adv_modes.get(field) == mode:
            return
        self.adv_modes[field] = mode
        self._reset_paging()
        self._refresh()

    def _on_extra_mode(self, index: int, e) -> None:
        if index >= len(self.extra_terms):
            return
        field = self.extra_terms[index].get("field", "editor")
        mode = str(getattr(e, "data", None) or
                   getattr(getattr(e, "control", None), "value", "") or "")
        if mode not in {key for key, _ in QUERY_MATCH_OPTIONS[field]}:
            return
        self.extra_terms[index]["mode"] = mode
        self._reset_paging()
        self._refresh()

    def _add_extra_term(self, e=None) -> None:
        self.extra_terms.append({"field": "editor", "value": "",
                                 "mode": QUERY_FIELD_MODES["editor"]})
        self._reset_paging()
        self._refresh()

    def _remove_extra_term(self, index: int, e=None) -> None:
        if 0 <= index < len(self.extra_terms):
            self.extra_terms.pop(index)
            self._reset_paging()
            self._refresh()

    def _on_submit(self, e=None) -> None:
        self.query()

    def query(self) -> None:
        """校验结构化条件后启动查询；空条件只报错，不禁用按钮。"""
        if not self._has_condition():
            self.input_error = EMPTY_INPUT
            self.error = ""
            self._refresh()
            return
        self.input_error = ""
        self.error = ""
        self.cancelled = False
        # 新首查（契约 §5）：先清空旧分页/选择，再显式 offset=0 拉第一页。
        self._reset_paging()
        self._schedule()

    def _focus_search(self) -> None:
        field = getattr(self, "_field", None)
        if field is not None:
            try:
                run = getattr(getattr(self.page, "page", None), "run_task", None)
                if callable(run):
                    run(field.focus)
                else:
                    field.focus()
            except Exception:  # noqa: BLE001
                pass

    def cancel(self) -> None:
        if not self.running:
            return
        self.cancel_requested = True
        # 取消 = 让在途结果世代过期（契约 §5）：迟到结果丢弃，可重跑不串结果。
        self._gen += 1
        self.cancelled = True
        self._refresh()

    def _cancel_clicked(self, e=None) -> None:
        self.cancel()

    # ── 分页状态（CONTRACT §5：page 字段只消费不重算）───────────────────
    def _is_list_query(self) -> bool:
        """统一检索走列表型分页，精确 key/DOI 条件也可与其他条件组合。"""
        return True

    def _has_condition(self) -> bool:
        return (any(str(value or "").strip() for value in self.adv_values.values())
                or any(str(term.get("value") or "").strip()
                       for term in self.extra_terms))

    def _cond_snapshot(self) -> dict:
        """首查条件快照；载入更多必须与它逐字段一致（同条件才允许续页）。"""
        return {
            "advanced": True,
            "sort": self.sort_key,
            "strict": bool(getattr(self, "adv_strict", False)),
            "terms": {str(field): str(value or "").strip()
                      for field, value in (getattr(self, "adv_values", {}) or {}).items()},
            "modes": dict(self.adv_modes),
            "extra_terms": [dict(term) for term in self.extra_terms],
        }

    def _reset_paging(self) -> None:
        """条件变化 / 新首查：清已载入、分页元数据与选择；在途结果靠世代过期丢弃。"""
        self._gen += 1
        if self.running:
            self.cancelled = True
        self._items = []
        self.hits = self._items
        self.rows = []
        self._loaded = 0
        self._total = None
        self._has_more = False
        self._db_sha = ""
        self._cond = None
        self._page_error = ""
        self.count = 0
        self.total_matched = None
        self.truncated = False
        self.loaded = False
        self.selected_key = None

    def _restart_query(self) -> None:
        """库变化后的刷新入口：按当前条件/当前库重新走一次首查（offset=0）。"""
        if not self._has_condition():
            return
        self.error = ""
        self.cancelled = False
        self._reset_paging()
        self._schedule()

    # ── 执行 ────────────────────────────────────────────────────────────
    def _call(self, offset: int | None = None):
        terms = [
            (field, str(value or "").strip(), self.adv_modes.get(
                field, QUERY_FIELD_MODES[field]))
            for field, value in self.adv_values.items()
            if str(value or "").strip()
        ]
        terms.extend(
            (term["field"], str(term.get("value") or "").strip(),
             term.get("mode") or QUERY_FIELD_MODES[term["field"]])
            for term in self.extra_terms
            if term.get("field") in QUERY_FIELD_MODES
            and str(term.get("value") or "").strip()
        )
        return EB.bibdb_query_advanced_call(
            terms=terms, sort=self.sort_key, sort_desc=self.sort_desc,
            limit=self.limit, offset=offset,
            strict=bool(self.adv_strict), bibtex=True, db=self._db())

    def _adopt_first(self, data: dict) -> None:
        """首查采纳：列表型用 page 六字段；无 page（旧引擎 / key/doi）退回旧字段。"""
        hits = list(data.get("hits") or [])
        page = data.get("page") if isinstance(data.get("page"), dict) else None
        self._items = list(hits)
        self.hits = self._items
        self.rows = [hit_row(hit) for hit in hits]
        self._loaded = len(hits)
        self.count = len(hits)
        self.selected_key = None
        self.loaded = True
        if page is not None:
            self._total = int(page.get("total_matched") or 0)
            self._db_sha = str(page.get("db_sha256") or "")
            self._has_more = bool(page.get("has_more"))
        elif self._is_list_query():
            # 旧引擎/旧假数据没有 page：只能如实显示，不能假装可续页。
            self._total = int(data.get("total_matched") or 0)
            self._db_sha = ""
            self._has_more = False
        else:
            # key/doi：单条定位，total = 命中条数，不翻页。
            self._total = int(data.get("total_matched") or len(hits))
            self._db_sha = ""
            self._has_more = False
        self.total_matched = self._total
        self.truncated = self._has_more
        self.success = (f"找到 {self.total_matched:,} 条"
                        if self.total_matched else "0 条 · 没有匹配")
        self._mark("bib_query_run", mode="advanced", limit=self.limit,
                   count=self.count, total=self.total_matched)

    def _adopt_more(self, data: dict) -> None:
        """载入更多采纳：sha / offset 任一不符即拒绝合并（契约 §5）。"""
        page = data.get("page") if isinstance(data.get("page"), dict) else None
        if page is None:
            self._page_error = DB_CHANGED_MSG
            self._has_more = False
            self.truncated = False
            return
        sha = str(page.get("db_sha256") or "")
        try:
            offset = int(page.get("offset"))
        except (TypeError, ValueError):
            offset = -1
        if sha != self._db_sha:
            # 库在分页之间变了：不合并、保留已载入、给刷新入口。
            self._page_error = DB_CHANGED_MSG
            self._has_more = False
            self.truncated = False
            return
        if offset != self._loaded:
            # 位置错位：绝不拼接，提示按当前库重查。
            self._page_error = PAGE_MISMATCH_MSG
            self._has_more = False
            self.truncated = False
            return
        hits = list(data.get("hits") or [])
        self._items = self._items + hits
        self.hits = self._items
        self.rows = [hit_row(hit) for hit in self._items]
        self._loaded = offset + len(hits)
        self.count = self._loaded
        if page.get("total_matched") is not None:
            self._total = int(page.get("total_matched") or 0)
        self.total_matched = self._total if self._total is not None else self._loaded
        self._has_more = bool(page.get("has_more")) and bool(hits)
        self.truncated = self._has_more
        self.success = (f"找到 {self.total_matched:,} 条"
                        if self.total_matched else "0 条 · 没有匹配")
        self._mark("bib_query_more", mode="advanced", limit=self.limit,
                   count=self.count, total=self.total_matched)

    async def _run(self, *, more: bool = False) -> None:
        if self.running:
            return
        if more:
            gen = self._gen
        else:
            # 首查：新世代 + 条件快照（契约 §5）。
            gen = self._gen + 1
            self._gen = gen
            self._cond = self._cond_snapshot()
        self.running = True
        self.cancel_requested = False
        self.success = ""
        self.error = ""
        if not more:
            self.cancelled = False
            self._page_error = ""
        started = time.monotonic()
        try:
            offset = self._loaded if more else (0 if self._is_list_query() else None)
            call = self._call(offset=offset)
        except Exception as exc:  # noqa: BLE001 —— 参数问题按失败展示
            self.running = False
            self._task_running = False
            self.error = f"查询失败：{_first_line(str(exc))}"
            self._refresh()
            return
        self._refresh()
        try:
            engine_started = time.monotonic()
            result = await asyncio.to_thread(
                EB.read_json_command, call, timeout=30.0, notes=self._notes())
            engine_elapsed_ms = round((time.monotonic() - engine_started) * 1000, 1)
        except Exception as exc:  # noqa: BLE001
            engine_elapsed_ms = round((time.monotonic() - engine_started) * 1000, 1)
            result = {"ok": False, "json": {}, "error": f"{type(exc).__name__}: {exc}"}
        self.elapsed = time.monotonic() - started
        self.running = False
        self._task_running = False
        if gen != self._gen:
            # 迟到结果 / 取消 / 条件变化：世代不符，一律丢弃，不串结果。
            self._refresh()
            return
        if self.cancel_requested:
            self.cancelled = True
            self.success = ""
            self._refresh()
            return
        if not result.get("ok"):
            self.error = f"查询失败：{_first_line(result.get('error') or '引擎返回失败')}"
            self._refresh()
            return
        data = result.get("json") or {}
        adopt_started = time.monotonic()
        if more:
            self._adopt_more(data)
        else:
            self._adopt_first(data)
        adopt_elapsed_ms = round((time.monotonic() - adopt_started) * 1000, 1)
        refresh_started = time.monotonic()
        self._refresh()
        self._mark("bib_query_timing", more=more, engine_ms=engine_elapsed_ms,
                   adopt_ms=adopt_elapsed_ms,
                   refresh_call_ms=round((time.monotonic() - refresh_started) * 1000, 1),
                   loaded=self._loaded)

    async def _run_more(self) -> None:
        await self._run(more=True)

    def load_more(self) -> None:
        """载入更多：同一条件快照 + offset=_loaded，固定 PAGE_SIZE，绝不放宽 limit。"""
        if self.running or not self._has_more:
            return
        if self.limit >= ENGINE_CAP:
            return
        if not self._is_list_query():
            return
        if not self._db_sha:
            # 没有 page 元数据（旧引擎/旧数据）无法安全续页。
            return
        if self._cond is None or self._cond != self._cond_snapshot():
            # 条件已变但还没重查：旧页不再属于当前条件。
            return
        self._schedule(self._run_more)

    # ── 排序 ────────────────────────────────────────────────────────────
    def _on_sort(self, key: str, desc: bool) -> None:
        if key not in ("title", "year", "author"):
            return
        had = bool(self.loaded and self._has_condition())
        self.sort_key = key
        self.sort_desc = bool(desc)
        # 排序是查询条件的一部分：先重置分页与选择，再按新排序走首查。
        self.error = ""
        self.cancelled = False
        self._reset_paging()
        if had:
            self._schedule()
        else:
            self._refresh()

    # ── 高级检索 ────────────────────────────────────────────────────────
    def _common_condition_field(self, field: str, label: str, hint: str) -> ft.Control:
        t = self.t
        label_control = C.text(label, t, "caption", t.palette.text_muted)
        if field == "alias":
            label_control.tooltip = "同一条目曾使用过的其他 BibTeX key。"
        if field in ("author", "title"):
            match_control = C.select(
                t, value=self.adv_modes.get(field, QUERY_FIELD_MODES[field]),
                options=QUERY_MATCH_OPTIONS[field],
                on_select=lambda e, field=field: self._on_match_mode(field, e),
                width=96)
        else:
            match_control = None
        field_ctl = C.text_field(
            t, value=self.adv_values.get(field, ""), hint_text=hint,
            on_change=lambda e, field=field: self._on_advanced_field(field, e),
            on_submit=self._on_submit, expand=True)
        field_ctl._bib_query_field = field
        input_row = [field_ctl]
        if match_control is not None:
            input_row.append(match_control)
        return ft.Column(
            spacing=0,
            controls=[
                ft.Row(spacing=t.sp("half"), controls=[label_control]),
                ft.Row(spacing=t.sp("half"), controls=input_row),
            ],
        )

    def _extra_condition_row(self, index: int) -> ft.Control:
        t = self.t
        term = self.extra_terms[index]
        options = [(field, label) for field, label in QUERY_FIELD_LABELS.items()]
        selector = C.select(
            t, value=term.get("field", "editor"), options=options,
            on_select=lambda e, index=index: self._on_extra_field(index, e),
            width=132, hint="字段")
        value = C.text_field(
            t, value=term.get("value", ""),
            hint_text=f"{QUERY_FIELD_LABELS.get(term.get('field'), '条件')}条件",
            on_change=lambda e, index=index: self._on_extra_value(index, e),
            on_submit=self._on_submit, expand=True)
        value._bib_query_extra = index
        match = C.select(
            t, value=term.get("mode", QUERY_FIELD_MODES.get(term.get("field"), "contains")),
            options=QUERY_MATCH_OPTIONS.get(term.get("field"), []),
            on_select=lambda e, index=index: self._on_extra_mode(index, e),
            width=104)
        remove = C.icon_button("CLOSE", "移除此条件",
                               lambda e, index=index: self._remove_extra_term(index, e),
                               t, width=28, height=28)
        return ft.Row(spacing=t.sp("1"), controls=[selector, match, value, remove])

    def _filter(self) -> ft.Control:
        t = self.t
        width = self._design_width()
        wrap = width < FILTER_WRAP_W
        controls = [
            self._common_condition_field(field, label, hint)
            for field, label, hint in COMMON_QUERY_FIELDS
        ]
        self._field = next((n for control in controls for n in self._walk_local(control)
                            if getattr(n, "_bib_query_field", "") == "title"), None)
        rows = []
        for index in range(0, len(controls), 2):
            pair = controls[index:index + 2]
            if len(pair) == 1:
                pair.append(ft.Container(expand=True))
            rows.append(ft.Row(
                spacing=t.sp("1"),
                controls=[ft.Container(expand=True, content=control) for control in pair]))
        if wrap:
            rows = [ft.Row(spacing=t.sp("1"), controls=[
                ft.Container(expand=True, content=control)]) for control in controls]
        rows.extend(self._extra_condition_row(i) for i in range(len(self.extra_terms)))

        actions = ft.Row(
            spacing=t.sp("1"), run_spacing=t.sp("1"), wrap=wrap,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self._query_button(),
                C.paste_button(t, self.page, self._field, height=28),
                C.text_button("增加条件", self._add_extra_term, t, height=28),
                C.select(t, value=self.sort_key,
                         options=[("relevance", "相关度"), ("title", "题名"),
                                  ("author", "作者"), ("year", "年份")],
                         on_select=lambda e: self._on_sort_value(e),
                         width=116, hint="排序"),
                ft.Row(spacing=0, controls=[
                    ft.Checkbox(value=bool(self.adv_strict),
                                on_change=self._on_strict_change,
                                visual_density=ft.VisualDensity.COMPACT,
                                splash_radius=0),
                    C.text("严格匹配", t, "caption", t.palette.text_muted),
                ]),
                C.text_button("清空", lambda e: self._clear(), t, height=28),
                ft.Container(expand=True),
                self._count_control(),
            ])
        form = ft.Column(spacing=t.sp("1"), controls=[
            C.text("多个条件同时填写时取交集；key、别名和 DOI 默认精确匹配。",
                   t, "caption", t.palette.text_faint),
            *rows,
            actions,
        ])
        box = ft.Container(
            padding=ft.Padding(left=t.px(0), top=t.px(8),
                               right=t.px(0), bottom=t.px(8)),
            content=form)
        box._bib_filter_row = True
        box._bib_query_form = True
        page = self.page.page if hasattr(self.page, "page") else None
        if self._field is not None:
            box._bib_filter_field = self._field
            if page is not None:
                S.bind_filter_shortcut(page, self._field)
        return box

    def _walk_local(self, control):
        yield control
        content = getattr(control, "content", None)
        if content is not None and not isinstance(content, str):
            yield from self._walk_local(content)
        for child in getattr(control, "controls", None) or ():
            yield from self._walk_local(child)

    def _on_sort_value(self, e) -> None:
        value = str(getattr(e, "data", None) or
                    getattr(getattr(e, "control", None), "value", "relevance"))
        if value not in EB.QUERY_SORTS or value == self.sort_key:
            return
        self.sort_key = value
        self.sort_desc = False
        had = bool(self.loaded and self._has_condition())
        self.error = ""
        self.cancelled = False
        self._reset_paging()
        if had:
            self._schedule()
        else:
            self._refresh()

    def _on_strict_change(self, e) -> None:
        value = bool(getattr(getattr(e, "control", None), "value", False))
        if value == self.adv_strict:
            return
        had = bool(self.loaded and self._has_condition())
        self.adv_strict = value
        self._reset_paging()
        if had:
            self._schedule()
        else:
            self._refresh()

    # ── 控件的构建 ──────────────────────────────────────────────────────
    def _design_width(self) -> float:
        try:
            width = float(self.page.page.width or 0)
        except Exception:  # noqa: BLE001
            width = 0.0
        if width <= 0:
            width = float(getattr(getattr(self.page, "app", None), "window_w", 0) or 0)
        return width / max(self.t.k, 1e-6)

    def _query_button(self):
        label = "运行中" if self.running else ("重新查询" if self.loaded else "查询")
        btn = C.primary_button(label, lambda e: self.query(), self.t, height=28)
        btn.disabled = self.running
        btn._bib_query_btn = True
        return btn

    def _count_control(self):
        t = self.t
        if self.input_error:
            return C.text(self.input_error, t, "caption", t.palette.error, max_lines=1)
        if self.error:
            return C.text(self.error, t, "caption", t.palette.error, max_lines=1,
                          tooltip=self.error)
        if self.running:
            return ft.Row(spacing=t.sp("1"), controls=[
                C.text(f"运行中 · {self.elapsed:.1f} s", t, "caption", t.palette.text_muted),
                C.text_button("取消", self._cancel_clicked, t, height=24),
            ])
        if self.cancelled:
            return C.text("已取消，可重跑。", t, "caption", t.palette.text_muted)
        if self.loaded:
            total = self._total if self._total is not None else (self.total_matched or 0)
            shown = self._loaded or min(self.count, total)
            tail = " · 已截断" if (self.truncated and not self._has_more) else ""
            return C.text(f"1–{shown} / {total:,} · {_clock(self.elapsed)}{tail}",
                          t, "caption", t.palette.text_faint)
        return C.text("", t, "caption", t.palette.text_faint)

    def _table(self) -> ft.Control:
        t = self.t
        if self._build_width < 1100:
            # 1000 宽：只留题名/年/状态，详情折到表格下方，不横向滚动。
            columns = [
                BT.BibColumn("title", "题名", flex=3.2, sortable=True),
                BT.BibColumn("year", "年", flex=0.8, align="right", sortable=True),
                BT.BibColumn("status", "状态", flex=0.9, sortable=False),
            ]
        else:
            columns = [
                BT.BibColumn("title", "题名", flex=3.2, sortable=True),
                BT.BibColumn("author", "作者", flex=1.8, sortable=True),
                BT.BibColumn("year", "年", flex=0.8, align="right", sortable=True),
                BT.BibColumn("key", "key", flex=1.9, mono=True, sortable=False),
                BT.BibColumn("status", "状态", flex=0.9, sortable=False),
                BT.BibColumn("source", "来源", flex=1.6, sortable=False),
            ]
        table = BT.BibTable(
            t, columns, self.rows, limit=max(PAGE_SIZE, len(self.rows)),
            page_size=PAGE_SIZE,
            empty_message=(NO_RESULT if self.loaded else "还没有查询"),
            empty_action=None, on_row_click=self._row_clicked,
            on_sort=self._on_sort, row_key="key", first_col_width=160,
            sort_key=self.sort_key if self.sort_key in ("title", "year", "author") else None,
            sort_desc=self.sort_desc,
            scroll_x=(self._build_width >= 1100),
            force_list_view=(self._total is not None
                             and self._total > BT.LIST_VIEW_THRESHOLD),
            height=None, page=self.page.page)
        table._bib_first_col_width = 160
        table.empty_action = C.text_button(
            "清空筛选" if self.loaded else "查询", 
            (lambda e: self._clear()) if self.loaded else (lambda e: self.query()),
            t, height=28)
        return table.build()

    def _row_clicked(self, row: dict) -> None:
        key = str(row.get("key") or "")
        self.selected_key = key
        self._refresh()

    def _clear(self) -> None:
        self.adv_values = {field: "" for field, _, _ in COMMON_QUERY_FIELDS}
        self.adv_modes = {field: QUERY_FIELD_MODES[field]
                          for field, _, _ in COMMON_QUERY_FIELDS}
        self.extra_terms = []
        self.adv_strict = False
        self.sort_key = "relevance"
        self.sort_desc = False
        self.error = ""
        self.input_error = ""
        self.success = ""
        self.limit = 100
        self.query_advanced = True
        self._reset_paging()
        self._refresh()

    def _selected_hit(self) -> dict | None:
        for hit in self.hits:
            if str(hit.get("key") or (hit.get("record") or {}).get("key")) == self.selected_key:
                return hit
        return None

    def _detail_row(self, label: str, value) -> ft.Control:
        """详情一行：小灰标签 + 可选中值；视觉最多 2 行，全文进 tooltip（P1-1）。"""
        t = self.t
        text = str(value)
        return ft.Column(spacing=0, controls=[
            C.text(label, t, "caption", t.palette.text_faint),
            C.text(text, t, "caption", t.palette.text, selectable=True,
                   max_lines=2, overflow=ft.TextOverflow.ELLIPSIS, tooltip=text),
        ])

    def _detail_heading(self, label: str) -> ft.Control:
        t = self.t
        return C.text(label, t, "caption", t.palette.text_faint)

    def _detail_body(self) -> ft.Control:
        t = self.t
        hit = self._selected_hit()
        if hit is None:
            return C.empty_line("未选择记录", t)
        fields = _hit_fields(hit)
        record = hit.get("record") or {}
        rows = [
            ("key", hit.get("key") or record.get("key") or "—"),
            ("类型", record.get("entry_type") or fields.get("entry_type") or "—"),
            ("题名", hit.get("title") or fields.get("title") or "—"),
            ("作者", hit.get("author") or fields.get("author") or "—"),
            ("年份", fields.get("year") or fields.get("date") or "—"),
            ("DOI", fields.get("doi") or "—"),
            ("来源", _source_of(hit)),
            ("核实", "已核实" if (hit.get("verified") or record.get("verified")) else "未核实"),
        ]
        body: list = [self._detail_row(label, value) for label, value in rows]
        # P1-1：record.fields 所有键逐行（含未知键）；空值 —，长值 2 行 + tooltip 全文。
        body.append(self._detail_heading(f"字段（{len(fields)}）"))
        if fields:
            body.extend(self._detail_row(_field_label(name), _field_text(value))
                        for name, value in fields.items())
        else:
            body.append(C.text(NO_VALUE, t, "caption", t.palette.text))
        sources = _source_items(record)
        body.append(self._detail_heading(f"来源（{len(sources)}）"))
        if sources:
            for item in sources:
                body.append(C.text(item, t, "caption", t.palette.text, selectable=True,
                                   max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                                   tooltip=item, font_family=t.mono_family))
        else:
            body.append(C.text(NO_SOURCES, t, "caption", t.palette.text))
        aliases = _alias_items(record)
        body.append(self._detail_heading(f"别名（{len(aliases)}）"))
        if aliases:
            joined = "、".join(aliases)
            body.append(C.text(joined, t, "caption", t.palette.text, selectable=True,
                               tooltip=joined))
        else:
            body.append(C.text(NO_VALUE, t, "caption", t.palette.text))
        # 冲突只给计数与去处：逐字段对照属「审核」视图，只读详情不展开。
        try:
            conflict_count = len(record.get("conflicts") or [])
        except TypeError:
            conflict_count = 1
        body.append(self._detail_heading(f"冲突（{conflict_count}）"))
        if conflict_count:
            body.append(C.text(CONFLICT_HINT.format(n=conflict_count), t, "caption",
                               t.palette.warn, selectable=True))
        else:
            body.append(C.text(NO_VALUE, t, "caption", t.palette.text))
        return ft.Column(spacing=t.sp("1"), scroll=C.scrollbar(t), controls=body)

    def _detail_actions(self) -> list:
        t = self.t
        hit = self._selected_hit()
        if hit is None:
            return []
        key = str(hit.get("key") or "")
        return [
            C.secondary_button("去编辑", lambda e: self._edit(key), t, height=28),
            C.secondary_button("复制 BibTeX", lambda e: self._copy_bibtex(hit), t, height=28),
            # P1-2：选中即有入口；对话框走 C.show_dialog 登记，Esc 才能关。
            C.text_button(RAW_BUTTON, lambda e: self._raw_record_open(), t, height=28),
            C.text_button("打开库文件", lambda e: self._open_log(key), t, height=28),
        ]

    # ── P1-2 原始记录：只读定位库物理行 + pretty JSON 对话框 ─────────────
    def _dialog_host(self):
        """对话框宿主：真实壳里是 ft.Page（self.page.page）；测试假 page 退回自身。"""
        host = getattr(self.page, "page", None)
        return host if host is not None else self.page

    def _raw_record_open(self) -> None:
        """详情「原始记录」：后台流式定位物理行，UI 线程不做同步读库。"""
        hit = self._selected_hit()
        if hit is None:
            return
        record = hit.get("record") or {}
        key = str(hit.get("key") or record.get("key") or "")
        if not record:
            self._raw_record_failed(key, RAW_NO_RECORD)
            return
        if self._task_running:
            return
        self._schedule_async(self._raw_record_load, key)

    async def _raw_record_load(self, key: str) -> None:
        result = {"status": "read_failed", "kind": "Exception"}
        try:
            result = await asyncio.to_thread(_locate_record, self._db(), key)
        except Exception as exc:  # noqa: BLE001 —— 只读定位异常也必须给中文提示
            result = {"status": "read_failed", "kind": type(exc).__name__}
        finally:
            self._task_running = False
        status = str(result.get("status") or "")
        if status == "ok":
            self._raw_record_dialog(key, result)
            return
        if status == "no_db":
            self._raw_record_failed(key, RAW_NO_DB)
        elif status == "no_record":
            self._raw_record_failed(key, RAW_NO_RECORD)
        elif status == "not_found":
            self._raw_record_failed(key, RAW_NOT_FOUND)
        else:
            self._raw_record_failed(
                key, RAW_READ_FAILED.format(kind=str(result.get("kind") or "Error")))

    def _raw_record_dialog(self, key: str, result: dict) -> None:
        t = self.t
        line = int(result.get("line") or 0)
        total = int(result.get("total") or 0)
        sha = str(result.get("sha256") or "")
        pretty = str(result.get("pretty") or "")
        raw = str(result.get("raw") or "")
        header = ft.Column(spacing=0, controls=[
            C.text(f"行号 {line} / {total} · 记录 SHA256", t, "caption",
                   t.palette.text_faint, selectable=True),
            C.text(sha, t, "caption", t.palette.text, selectable=True,
                   font_family=t.mono_family),
        ])
        view = C.text_field(t, value=pretty, read_only=True, multiline=True,
                            min_lines=RAW_MIN_LINES, max_lines=RAW_MAX_LINES,
                            mono=True, clearable=False)
        dialog = ft.AlertDialog(
            modal=True,
            title=C.text(RAW_TITLE.format(key=key), t, role="dialog-title"),
            content=ft.Container(width=t.px(RAW_DIALOG_W), content=ft.Column(
                spacing=t.sp("1"), controls=[header, view])),
            actions=[
                C.secondary_button("复制 JSON", lambda e: self._copy(pretty), t),
                C.secondary_button("复制原文行", lambda e: self._copy(raw), t),
                C.primary_button("关闭", lambda e: self._raw_record_close(), t),
            ],
        )
        C.show_dialog(self._dialog_host(), dialog)
        self._mark("bib_raw_record_opened", key=key, line=line, total=total)

    def _raw_record_failed(self, key: str, message: str) -> None:
        t = self.t
        title = RAW_TITLE.format(key=key) if key else RAW_BUTTON
        dialog = ft.AlertDialog(
            modal=True,
            title=C.text(title, t, role="dialog-title"),
            content=ft.Container(width=t.px(420), content=ft.Column(
                spacing=t.sp("1"), controls=[
                    C.text(message, t, "body", t.palette.error, selectable=True),
                    C.text(RAW_NEXT_HINT, t, "caption", t.palette.text_muted),
                ])),
            actions=[
                C.text_button("打开库文件", lambda e: self._open_log(key), t),
                C.primary_button("关闭", lambda e: self._raw_record_close(), t),
            ],
        )
        C.show_dialog(self._dialog_host(), dialog)
        self._mark("bib_raw_record_failed", key=key, message=message)

    def _raw_record_close(self) -> None:
        C.dismiss_top_dialog(self._dialog_host())

    def _edit(self, key: str) -> None:
        self._set_store("bib_focus_key", key)
        self._set_store("bib_nav_origin", "query")
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter("edit")

    def _copy_bibtex(self, hit: dict) -> None:
        """复制引擎给的 BibTeX；命中没带时按 key 向引擎再取（绝不自己拼）。"""
        text = str(hit.get("bibtex") or "").strip()
        if text:
            self._copy(text)
            return
        key = str(hit.get("key") or (hit.get("record") or {}).get("key") or "")
        if not key:
            return
        self._schedule_async(self._copy_bibtex_fetch, key)

    async def _copy_bibtex_fetch(self, key: str) -> None:
        text = ""
        try:
            call = EB.bibdb_query_basic_call(
                "key", key, limit=1, bibtex=True, db=self._db())
            result = await asyncio.to_thread(
                EB.read_json_command, call, timeout=30.0, notes=self._notes())
            data = (result or {}).get("json") or {}
            for hit in list(data.get("hits") or []):
                if str(hit.get("key") or "") == key:
                    text = str(hit.get("bibtex") or "").strip()
                    break
        except Exception:  # noqa: BLE001 —— 取不到就提示，不伪造
            text = ""
        finally:
            self._task_running = False
        if text:
            self._copy(text)
            return
        self.error = f"没有取到 {key} 的 BibTeX；可打开详情核对记录。"
        self._refresh()

    def _open_log(self, key: str) -> None:
        opener = getattr(getattr(self.page, "app", None), "open_path", None)
        path = self._db()
        if callable(opener) and path:
            try:
                opener(path)
            except Exception:  # noqa: BLE001
                pass

    def _copy(self, text: str) -> None:
        fn = getattr(getattr(self.page, "app", None), "copy_text", None)
        if callable(fn):
            try:
                fn(text)
                return
            except Exception:  # noqa: BLE001
                pass
        setter = getattr(getattr(self.page, "page", None), "set_clipboard", None)
        if callable(setter):
            try:
                setter(text)
            except Exception:  # noqa: BLE001
                pass

    def _detail_panel(self) -> ft.Control:
        t = self.t
        hit = self._selected_hit()
        title = "记录详情" if hit else "未选择记录"
        return S.detail_panel(
            t, body=self._detail_body(), title=title,
            meta=(f"{hit.get('key')} · 单条记录" if hit else "选一行看字段，或按 key 精确定位。"),
            actions=self._detail_actions() or None)

    def _fluid_detail(self, panel: ft.Control, *, wrap_actions: bool) -> ft.Control:
        """把固定宽详情面板变成「吃满父级宽」的柔性面板（R-05）。

        bib_shell.detail_panel 的 width 是设计定宽（宽窗右栏 360 用）；窄窗里父级
        还有 rail/内外边距，定宽会超出实际内容区，动作行被 clip。这里：
        1) 面板容器 width 置 None —— 由外层常宽容器拉伸决定实际宽度；
        2) 动作行（面板 Column 最后一个 Row）换成 wrap Row —— 面板 Column 是
           STRETCH，wrap Row 拿到的是父级紧宽，放不下就折行，不会越界；
        3) 外层 Column(expand) -> Row(STRETCH) -> Container(expand) 提供有界
           高宽，面板内部 Column(expand=True) 的正文滚动与底部动作都成立。
        """
        t = self.t
        column = getattr(panel, "content", None)
        if wrap_actions and isinstance(column, ft.Column) and column.controls:
            last = column.controls[-1]
            if isinstance(last, ft.Row):
                column.controls[-1] = ft.Row(
                    spacing=t.sp("1"), run_spacing=t.sp("1"), wrap=True,
                    alignment=ft.MainAxisAlignment.END,
                    controls=self._detail_actions())
        panel.width = None
        return ft.Container(
            expand=True,
            content=ft.Row(
                spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[ft.Container(expand=True, content=panel)],
            ),
        )

    def _placeholder(self) -> ft.Control:
        t = self.t
        return S.detail_panel(t, body=C.empty_line("未选择记录", t),
                              title="未选择记录",
                              meta="选一行看字段，或按 key 精确定位。")

    def _footer(self) -> ft.Control:
        t = self.t
        parts: list = []
        cap_legacy = self.limit >= ENGINE_CAP
        if self.loaded and self.count:
            if cap_legacy:
                parts.append(C.text(f"已载入 {min(self.count, self.limit)} / "
                                    f"{(self.total_matched or 0):,}",
                                    t, "caption", t.palette.text_faint))
            elif self._has_more or self._page_error:
                # 分页中 / 库变化拒绝合并：如实显示已载入与总数，不冒充「全部载入」。
                parts.append(C.text(f"已载入 {self._loaded or self.count} / 共 "
                                    f"{self._total or 0} 条",
                                    t, "caption", t.palette.text_faint))
            else:
                parts.append(C.text(f"已全部载入（共 {self._total or self.count} 条）",
                                    t, "caption", t.palette.text_faint))
        if cap_legacy:
            parts.append(C.text("最多 200 条；请加条件", t, "caption",
                                t.palette.text_faint))
        elif self._has_more and self._db_sha:
            parts.append(C.text_button("载入更多", lambda e: self.load_more(), t, height=28))
        if not parts:
            return ft.Container(height=t.px(28))
        return ft.Row(spacing=t.sp("1"), controls=parts)

    def _initial_banner(self) -> ft.Control | None:
        t = self.t
        if self.running or self.loaded:
            return None
        if self.error:
            return S.state_banner(t, "failure", message=self.error,
                                  detail="条件与结果保留；可直接重试。",
                                  next_label="重试", on_next=lambda e: self.query(),
                                  page=self.page.page)
        return ft.Column(spacing=0, controls=[
            S.state_banner(t, "empty", message="还没有查询"),
            C.text("在上方填写一个或多个条件，然后查询。", t, "caption",
                   t.palette.text_faint),
        ])

    def _body(self) -> ft.Control:
        t = self.t
        self._build_width = self._design_width()
        narrow = self._build_width < 1100
        parts: list = []
        if self.error and self.loaded:
            parts.append(S.state_banner(
                t, "failure", message=self.error,
                next_label="复制错误", on_next=lambda e: self._copy(self.error),
                page=self.page.page))
        if self._page_error:
            # 库在分页之间变化 / 位置错位：不合并、保留已载入，给「按当前库重查」入口。
            parts.append(S.state_banner(
                t, "cancelled", message=self._page_error,
                detail="已载入的结果保留；按当前库重新查询会从第一页开始。",
                action=C.text_button("刷新后重查", lambda e: self._restart_query(),
                                     t, height=28),
                page=self.page.page))
        banner = self._initial_banner()
        if banner is not None:
            parts.append(banner)
        if self.cancelled:
            parts.append(S.state_banner(t, "cancelled", message="已取消，可重跑。",
                                        next_label="重试", on_next=lambda e: self.query()))
        table = self._table()
        footer = self._footer()
        if narrow:
            # R-05：窄窗详情不再按「页面设计宽 × t.px」定宽（父级还有侧栏/内外边距，
            # 定宽会超出实际内容区，动作行右端被父级裁剪、点不到）。改成柔性铺满：
            # 外层 Row+STRETCH 给详情一个有界高宽，面板 width=None 由父级拉伸决定，
            # 动作行换成 wrap Row（Column STRETCH 已给它紧宽），360 自动折行不越界。
            parts.append(table)
            parts.append(footer)
            if self.selected_key:
                parts.append(self._fluid_detail(self._detail_panel(),
                                                wrap_actions=True))
            else:
                parts.append(self._fluid_detail(self._placeholder(),
                                                wrap_actions=False))
        else:
            parts.append(ft.Row(
                expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[ft.Container(expand=True, content=ft.Column(
                    spacing=0, expand=True, controls=[table, footer])),
                    self._detail_panel()]))
        return ft.Column(spacing=t.sp("1"), expand=True, controls=parts)

    def build(self) -> ft.Control:
        self._build_width = self._design_width()
        meta = f"只读 · 每页 {PAGE_SIZE} 条"
        total = (getattr(self.page, "stats_data", None) or {}).get("records")
        if isinstance(total, int):
            meta = f"库 {total:,} 条 · {meta}"
        return S.page_shell(
            self.t, title=TITLE, meta=meta, role_chip="readonly",
            action=None, on_gear=lambda e: self._goto_settings(),
            body=ft.Column(spacing=0, expand=True,
                           controls=[self._filter(), self._body()]))

    def _goto_settings(self) -> None:
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter("settings")
