"""生成目录：预览模板/篇目 → 编辑栏目表（目录设置.json）→ 落位编译并读结果 JSON。

排版与落位逻辑全在 `Engine/toc.py`；本模块只做取值、拼参数、显示。
"""
from __future__ import annotations

import asyncio
import datetime
import os
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from services import toc_settings as TS
from ui import components as C

PREVIEW_HINT = ("自动取最新检查报告；栏目表自动读入、运行前自动保存，"
                "预览与生成无需先点「保存栏目表」。")
TEXT_ONLY_HINT = "只生成 tex，不写入、不编译；取消勾选后会写入并编译。"

#: 批 121（B-355/E 车道接口）：生成中间件统一放期目录的 `.toolbox-tmp/`。
TMP_DIR = ".toolbox-tmp"
RESULT_JSON_NAME = "目录结果.json"


def result_json_path(report: str) -> str:
    """结果 JSON 的目标路径：`<报告目录>/.toolbox-tmp/目录结果.json`。

    报告目录为空（测试桩只给相对文件名）时回退旧路径 `目录结果.json`，
    保持批 120 之前的调用口径。
    """
    if not report:
        return ""
    report_dir = os.path.dirname(report)
    if not report_dir:
        return RESULT_JSON_NAME
    return os.path.join(report_dir, TMP_DIR, RESULT_JSON_NAME)


def legacy_result_json_path(report: str) -> str:
    """旧版结果 JSON 路径（兼容用户已有文件与旧测试）。"""
    if not report:
        return ""
    return os.path.join(os.path.dirname(report), RESULT_JSON_NAME)


def existing_result_json_path(report: str) -> str:
    """「打开结果 JSON」用：新路径优先，旧路径兜底，都没有则返回新目标路径。"""
    for path in (result_json_path(report), legacy_result_json_path(report)):
        if path and os.path.exists(path):
            return path
    return result_json_path(report)


def ensure_result_dir(report: str) -> str:
    """生成前创建结果目录（`.toolbox-tmp/`），返回结果 JSON 路径；失败抛 OSError。"""
    path = result_json_path(report)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def existing_settings_path(path: str) -> str:
    """B-375 Lead m00904：只返回真实存在的设置文件路径，否则返回空串。

    引擎对显式 `--settings` 指向不存在的文件会直接拒绝（「找不到设置文件」）。
    首次进入还没有 `目录设置.json` 时，sync_settings() 会保留「路径已确定但文件
    不存在」的状态（保护：不为显示控件提前建文件），此时必须省略该参数让引擎用
    内置默认——预览和生成共用这套「只传已存在的设置文件」解析。
    """
    path = str(path or "")
    return path if path and os.path.isfile(path) else ""


def read_result(report: str) -> dict:
    """读结果 JSON：新路径优先，旧路径兜底（返回 {} 表示都没有/读不了）。"""
    for path in (result_json_path(report), legacy_result_json_path(report)):
        if path and os.path.exists(path):
            payload = EB.toc_result(path)
            if payload:
                return payload
    return {}


def latest_report(issue: str) -> str:
    try:
        reports = sorted(Path(issue).glob("信息检查*.txt"), key=lambda p: p.stat().st_mtime)
        return str(reports[-1]) if reports else ""
    except OSError:
        return ""


def article_rows(payload: dict) -> list[dict]:
    """从 infocheck --preview 的 JSON 取篇目（文件夹名 + 标题）。"""
    rows: list[dict] = []
    for raw in (payload or {}).get("articles") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("folderName") or raw.get("folder") or "").strip()
        folder = str(raw.get("folder") or name).strip()
        title = str(raw.get("chineseTitle") or raw.get("englishTitle") or "").strip()
        title = " ".join(title.split())
        if name:
            rows.append({"name": name, "folder": folder, "title": title})
    return rows


def articles_from_report(report: str) -> list[dict]:
    """从《信息检查》报告旁边的 JSON 同步读篇目（用户 m02698：勾选专栏篇目）。

    信息检查同时写 `.txt` 与 `.json`；报告字段选的是 `.txt`，这里找同名 `.json`。
    读不到时返回空列表，由异步回退（引擎 infocheck --preview）兜底。
    """
    path = str(report or "").strip()
    if not path:
        return []
    candidate = Path(path)
    if candidate.suffix.lower() == ".json":
        candidates = [candidate]
    else:
        candidates = [candidate.with_suffix(".json")]
        try:
            candidates += sorted(
                candidate.parent.glob(f"{candidate.stem}*.json"),
                key=lambda p: p.stat().st_mtime, reverse=True)[:1]
        except OSError:
            pass
    for json_path in candidates:
        try:
            if not json_path.is_file():
                continue
            import json
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = article_rows(data if isinstance(data, dict) else {"articles": data})
        if rows:
            return rows
    return []



def preview_lines(payload: dict, *, column_kind: str = "",
                  report_articles: int | None = None) -> list[str]:
    payload = payload or {}
    source = str(payload.get("templateSource") or "").strip()
    note = str(payload.get("templateNote") or "").strip()
    if source:
        lines = [f"模板来源：{source}"]
        if note:
            lines.append(note)
    elif note:
        # 引擎 preview JSON 目前只回 templateNote（templateSource 只在正式结果 JSON 里）；
        # note 已写明「内置模板/上一期/本期成品」等口径，直接给用户看，别显示「（未知）」。
        # Lead m00904：首次进入（无设置文件）预览信息卡应显示内置模板来源。
        lines = [f"模板来源：{note}"]
    else:
        lines = ["模板来源：（未知）"]
    if str(column_kind or "").strip() == "专辑":
        # 2026-11-06 用户决定(c) + 独立验收 task-6(1)：专辑的有效集合 = 当前报告全部
        # 篇目，settings.columnArticles（通常是空/旧子集）不是专辑的生效集合，预览卡
        # 不能显示「已选 0 篇」。N 取当前报告篇目数（已载入 available，缺省用引擎
        # preview 回传的 articles），且只用于显示，不写回 columnArticles。
        count = report_articles if report_articles is not None \
            else len(payload.get("articles") or [])
        lines.append(f"栏目：本期全部 {max(0, int(count))} 篇自动收入（无需勾选）")
        return lines
    articles = payload.get("columnArticles") or []
    lines.append(f"栏目：{'已启用' if payload.get('columnEnabled') else '本期无栏目'}"
                 f" · 已选 {len(articles)} 篇")
    return lines


def result_paths(payload: dict) -> list[tuple[str, str]]:
    placed = (payload or {}).get("placed") or {}
    pairs = [
        ("中文目录 tex", placed.get("zhTex", "")),
        ("中文目录 PDF", placed.get("zhPdf", "")),
        ("英文目录 tex", placed.get("enTex", "")),
        ("英文目录 PDF", placed.get("enPdf", "")),
    ]
    return [(label, str(path)) for label, path in pairs if path]


def result_lines(payload: dict) -> list[str]:
    payload = payload or {}
    lines: list[str] = []
    pages = payload.get("pages") or {}
    if pages:
        lines.append(f"页数：中文 {pages.get('zh', 0)} 页 / 英文 {pages.get('en', 0)} 页")
    column = payload.get("column") or {}
    if column.get("enabled"):
        lines.append(f"本期栏目：{column.get('title', '')}")
    if payload.get("manualEdits"):
        lines.append(f"人工改动 {len(payload.get('manualEdits') or [])} 处（见结果 JSON）")
    else:
        lines.append("人工改动：无")
    for warning in payload.get("warnings") or []:
        lines.append(f"注意 · {warning}")
    return lines


def result_outputs(payload: dict, issue: str = "") -> list[tuple[str, str, bool]]:
    """目录 tex 产物：P1 JSON `outputs.zhTex/enTex` 优先。

    旧结果只写 `placed`（仅落位时非空）；text-only 的旧结果两者都没有，此时才回退
    到 000 目录 glob，并用第三个返回值标明「推断」，界面上如实标注。
    """
    payload = payload or {}
    outputs = payload.get("outputs") or {}
    pairs: list[tuple[str, str, bool]] = []
    for key, label in (("zhTex", "中文目录 tex"), ("enTex", "英文目录 tex")):
        path = str(outputs.get(key) or "") if isinstance(outputs, dict) else ""
        if path and os.path.isfile(path):
            pairs.append((label, path, False))
    if pairs:
        return pairs
    for label, path in result_paths(payload):
        if label.endswith("tex") and path:
            pairs.append((label, path, False))
    if pairs or not issue:
        return pairs
    folder = os.path.join(issue, "000")
    for pattern, label in (("Chinese Content_*.tex", "中文目录 tex"),
                           ("contents_*.tex", "英文目录 tex")):
        try:
            found = sorted(Path(folder).glob(pattern))
        except OSError:
            found = []
        if found:
            pairs.append((label, str(found[-1]), True))
    return pairs


def first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


# 2026-11-06 用户决定(b)(c)：启用栏目（专栏/专辑）四项必填，缺项一次列全；
# 栏目类型为空 = 本期无栏目，永远不因字段缺失阻断。
COLUMN_FIELDS = (
    ("columnName", "栏目名称（中文）"),
    ("columnNameEN", "栏目英文名"),
    ("columnHost", "主持人"),
)
ENABLED_COLUMN_KINDS = ("专栏", "专辑")


def column_issue(values) -> tuple[bool, str]:
    """页面级唯一栏目有效性判断（TocPanel / pages / assembly 共用）。

    返回 `(是否阻止生成, 说明文案)`。纯空格按空处理；专栏/专辑缺任意一项即阻断，
    并在提示里一次列出全部缺项（用户决定(b)：disabled everywhere with missing-item explanation）。
    """
    data = values or {}
    kind = str(data.get("columnKind") or "").strip()
    if not kind:
        return False, ""
    if kind not in ENABLED_COLUMN_KINDS:
        return True, (f"栏目类型「{kind}」无法识别：请在栏目类型里选「本期无栏目」、"
                      "「专栏」或「专辑」。")
    missing = [label for key, label in COLUMN_FIELDS
               if not str(data.get(key) or "").strip()]
    if not missing:
        return False, ""
    return True, (f"已启用「{kind}」，但还缺：{'、'.join(missing)}。"
                  "四项都填好才能生成目录；不想启用栏目请把类型改成「本期无栏目」。")


class TocPanel:
    """目录步骤的预览/栏目表/生成 UI；状态保存在实例上，切步骤不丢。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = page.app
        self.t = page.t
        self.payload: dict = {}
        self.values: dict = TS.defaults()
        self.selected: list[str] = []
        self.available: list[dict] = []
        self.info = ft.Container()
        self.feedback = ft.Container()
        self.editor_box = ft.Column(spacing=self.t.sp("1"))
        # 用户 m03037：栏目类型改成 48 高浮动下拉（C.select_box），与右侧输入框盒高一致；
        # 初始控件在 _editor_controls() 里按当前值重建（选项里可能含自定义类型）。
        self.kind = C.select_box(
            self.t, "栏目类型",
            value_text=TS.KIND_LABELS.get(TS.COLUMN_KINDS[0], TS.COLUMN_KINDS[0]),
            options=[(key, TS.KIND_LABELS[key]) for key in TS.COLUMN_KINDS],
            on_select=self._on_kind_select, expand=True,
        )
        # 批 123：篇目改为勾选清单（_article_checklist），不再需要「可加入的篇目」下拉。
        # 批 121 A1/A2：自动读入/自动保存的状态。
        # `_loaded_path` = 已自动读过的设置文件；`_saved_snapshot` = 上次读/写的内容快照；
        # `_dirty` = 用户是否在面板里改过（只有改过才自动写文件，避免空跑覆盖手改文件）。
        self._loaded_path = ""
        self._saved_snapshot = self._snapshot()
        self._dirty = False
        # B-373：草稿归属的报告——切换报告时草稿只写回它当时所在的那份设置文件。
        self._loaded_report = ""
        # B-374：当前篇目勾选是否已由设置文件或用户明确给出（显式空集不得被默认全选覆盖）。
        self._sel_explicit = False
        # B-373 返工：目标设置读入失败的状态（非空即阻断 sync/预览/生成/装配，直到修复或重载）。
        self._load_error = ""
        self._load_error_path = ""
        # 批 123（用户 m02698）：专栏篇目勾选清单 = 当前报告已加载到哪个报告 + 是否在异步加载。
        self._articles_for = ""
        self._articles_loading = False

    # ── 参数区 ─────────────────────────────────────────────────────────
    def params(self) -> list:
        options = self.app.store.get("options", {}) or {}
        report = self._report()
        self._auto_load_settings(report)
        self._ensure_articles(report)
        self.editor_box.controls = self._editor_controls()
        if report:
            try:
                stamp = datetime.datetime.fromtimestamp(
                    os.path.getmtime(report)).strftime("%m-%d %H:%M")
            except OSError:
                stamp = ""
            hint = f"最新：{os.path.basename(report)}" + (f" · {stamp} 修改" if stamp else "")
        else:
            hint = "还没有信息检查报告；先生成第 1 步报告，这里会自动取最新"
        return [
            C.notice_bar(PREVIEW_HINT, self.t, "info"),
            self._report_picker_row(),
            C.text(hint, self.t, "caption", self.t.palette.text_muted),
            self.page.add_picker("目录模板（可空）", "toc_template",
                                 "指定模板期目录；空 = 本期成品 → 上一期 → 内置",
                                 kind="dir", required=False),
            self.page.add_picker("目录设置 JSON（可空）", "toc_settings",
                                 "默认读写报告同目录的 目录设置.json", kind="file",
                                 required=False),
            # 批 115：主操作只留在详情头（唯一主按钮）。栏目表相关全部是次操作；
            # 注：pinned test_b114_toc_grid 要求参数区保留 4 个描边按钮且可换行，
            # 故此处用 4 个 28 高次按钮（无主色），不在本页提供第二个运行入口。
            ft.Row(spacing=self.t.sp("1"), wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                       C.secondary_button("读取栏目表", lambda e: self._load_settings(),
                                          self.t, height=self.t.px(28)),
                       C.secondary_button("保存栏目表", lambda e: self._save_settings(),
                                          self.t, height=self.t.px(28)),
                       C.secondary_button("打开目录设置",
                                          lambda e: self.app.open_path(self._settings_path()),
                                          self.t, height=self.t.px(28)),
                       C.secondary_button("打开结果 JSON",
                                          lambda e: self.app.open_path(self._toc_result_path()),
                                          self.t, height=self.t.px(28)),
                   ]),
            # frozen #2 返工：预览入口此前只有实现没有调用者，用户找不到。
            # 单独一行（不动上面 pinned 的 4 按钮 Row），在「仅生成 tex」之前；
            # 预览只读模板/篇目，不写产物、不解锁生成，四项缺项时只 warn。
            ft.Row(spacing=self.t.sp("1"), wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                       C.secondary_button("预览模板/篇目", lambda e: self._request_preview(),
                                          self.t, height=self.t.px(28)),
                   ]),
            C.option_row("仅生成 tex（不写入、不编译）", TEXT_ONLY_HINT, self.t,
                         icon="DESCRIPTION_OUTLINED",
                         checked=self._text_only(),
                         on_change=lambda e: self.app.set_option("toc_text_only",
                                                                 e.control.value)),
            self.info,
            self.editor_box,
            self.feedback,
        ]

    def _text_only(self) -> bool:
        """与运行入口同一口径（批 117）：读 assembly 的开关，不另存一份。"""
        try:
            from ui import assembly as ASM
            return bool(ASM._toc_text_only(self.page))
        except Exception:  # noqa: BLE001
            return bool((self.app.store.get("options", {}) or {}).get("toc_text_only", False))

    def _request_preview(self) -> None:
        """frozen #2 返工：可见「预览模板/篇目」入口。

        只在界面里调度既有 `_preview_task`（不写产物、不解锁生成；缺项只 warn）。
        无 run_task 的页面桩（离线测试/旧壳）退化为事件循环里直接起协程。
        """
        hook = getattr(getattr(self, "page", None), "mark", None)
        if callable(hook):
            try:
                hook("toc_preview_requested")
            except Exception:  # noqa: BLE001 - 页面桩/未挂载
                pass
        runner = getattr(getattr(self, "page", None), "page", None)
        run_task = getattr(runner, "run_task", None)
        if callable(run_task):
            try:
                run_task(self._preview_task)
                return
            except Exception:  # noqa: BLE001 - 老页面/测试桩无 run_task 语义
                pass
        try:
            asyncio.get_running_loop().create_task(self._preview_task())
        except RuntimeError:
            pass  # 没有事件循环：离线测试直接 await _preview_task 即可

    def _set_value(self, key: str, value) -> None:
        self.values[key] = str(value or "").strip()
        self._mark_dirty()
        # 原生冒烟 frozen #1：缺项不能只写在按钮 tooltip 里——输入时同步刷新编辑区
        # 的可见提示（只换 content，不重建输入框，避免丢焦点）。
        self._refresh_gate_hint()

    def _report(self) -> str:
        """非副作用解析当前报告：字段优先，空才回退磁盘最新（绝不写字段）。

        Lead task-17：渲染结果区/打开结果 JSON/生成参数解析都会调这里；旧的
        set_field("toc_report") 让每次渲染都可能把字段改回最新报告，B 的
        「本次生成完成」就这样串到了 A。回填只允许页面显式动作做。
        """
        report = self.page.field("toc_report")
        if report:
            return report
        return latest_report(self.page.field("issue_dir"))

    # ── 报告选择行（Lead task-17(c)：字段一改就重算结果区归属）────────────
    def _report_picker_row(self) -> ft.Control:
        """自建「信息检查报告」行：add_picker 的浏览回填不触发 on_change。

        base_page.add_picker 的浏览走 app.browse_field，只写控件值/store，不回调；
        旧代码因此手动切报告后结果区不重算。这里复用 C.path_picker（同一外观），
        浏览后启动轮询 watcher（同 pages._watch_issue_pick）补一次归属重算。
        """
        t = self.t
        value = self._report()
        ref: list = []

        def _changed(e) -> None:
            self._on_report_changed()

        def _browse(e) -> None:
            before = str((ref[0].value if ref else "") or "").strip()
            browse = getattr(getattr(self.page, "app", None), "browse_field", None)
            if callable(browse):
                try:
                    browse(ref, "选择信息检查报告", "file", "toc_report")
                except Exception:  # noqa: BLE001 - 页面桩/未挂载
                    return
            self._schedule_coro(self._watch_report_pick(ref, before))

        control = C.path_picker(
            "信息检查报告", value, "期目录下的 信息检查*.txt", t,
            on_change=_changed, on_browse=_browse, required=True, field_ref=ref,
        )
        fields = getattr(self.page, "fields", None)
        if isinstance(fields, dict):
            fields["toc_report"] = ref
        return control

    def _on_report_changed(self, value: str | None = None) -> None:
        """报告字段变化（键入/粘贴/清空/浏览回填）：同步最近值并触发归属重算。"""
        current = value if value is not None else self.page.field("toc_report")
        current = str(current or "").strip()
        store = getattr(getattr(self.page, "app", None), "store", None)
        set_recent = getattr(store, "set_recent", None)
        if callable(set_recent):
            try:
                set_recent("toc_report", current)
            except Exception:  # noqa: BLE001 - 页面桩
                pass
        mark = getattr(self.page, "mark", None)
        if callable(mark):
            try:
                mark("path_change", field="toc_report", value=current)
            except Exception:  # noqa: BLE001
                pass
        hook = getattr(self.page, "on_toc_report_changed", None)
        if callable(hook):
            try:
                hook(current)
            except Exception:  # noqa: BLE001 - 页面桩/未挂载
                pass

    def _schedule_coro(self, coro) -> None:
        """调度后台协程：优先页面 run_task，否则当前事件循环（离线测试可直接 await）。"""
        runner = getattr(getattr(self, "page", None), "page", None)
        run_task = getattr(runner, "run_task", None)
        if callable(run_task):
            try:
                run_task(coro)
                return
            except Exception:  # noqa: BLE001 - 老页面/测试桩无 run_task 语义
                pass
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            try:
                coro.close()
            except Exception:  # noqa: BLE001
                pass

    async def _watch_report_pick(self, ref: list, before: str) -> None:
        """浏览回填只写控件值、不触发 on_change：轮询等值变化后补一次归属重算。

        240×0.25s（60s，与 pages._watch_issue_pick 同量级）；取消选择则自然退出。
        """
        for _ in range(240):
            await asyncio.sleep(0.25)
            try:
                value = str((ref[0].value if ref else "") or "").strip()
            except Exception:  # noqa: BLE001
                return
            if value != before:
                self._on_report_changed(value)
                try:
                    ref[0].update()
                except Exception:  # noqa: BLE001
                    pass
                return

    def _toc_result_path(self) -> str:
        """「打开结果 JSON」：新路径（.toolbox-tmp）优先，旧路径兜底。"""
        return existing_result_json_path(self._report())

    def _settings_path(self) -> str:
        explicit = self.page.field("toc_settings")
        if explicit:
            return explicit
        report = self._report()
        return TS.path_for(os.path.dirname(report)) if report else ""

    # ── 批 121 A1/A2/A4：自动读入、自动保存、负向校验 ───────────────────
    def _snapshot(self) -> dict:
        data = dict(self.values)
        data["columnArticles"] = list(self.selected)
        return TS.normalize(data)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._notify_page()

    def _notify_page(self) -> None:
        """B-375 共享门：栏目表/勾选/设置状态变化后让页面重算生成入口可用性。"""
        page = getattr(self, "page", None)
        hook = getattr(page, "on_toc_state_changed", None)
        if callable(hook):
            try:
                hook()
            except Exception:  # noqa: BLE001 - 页面桩/未挂载
                pass

    def _auto_load_settings(self, report: str) -> None:
        """A1：选定报告后自动读默认 目录设置.json，回填栏目表。

        只在「生效路径」变化时读一次；用户已改过（`_dirty`）则先写回**草稿原本所属**
        的那份设置文件再切换（B-373：不能把上一份报告的草稿写进新报告的设置）。
        原归属路径写失败时保持草稿与 `_dirty`、显示错误并原地停住，等用户处理。
        """
        path = self._settings_path()
        if not path:
            return
        load_error_path = str(getattr(self, "_load_error_path", "") or "")
        if path == self._loaded_path and load_error_path != path:
            # 当前归属就是它；若刚才是别的目标读失败，回到归属即视为恢复（R2 返工）。
            self._load_error = ""
            self._load_error_path = ""
            return
        report_key = str(report or "") or self._report()
        previous_path = str(getattr(self, "_loaded_path", "") or "")
        previous_report = str(getattr(self, "_loaded_report", "") or "")
        if self._dirty and previous_path:
            saved_path, error = self._write_settings(previous_path)
            if error:
                self._notice(f"上一份报告的栏目表保存失败：{error}", "error")
                self.page.mark("toc_settings_error", path=saved_path, error=error,
                               report=previous_report, auto=True)
                self.page.mark("toc_settings_switch_blocked",
                               path=previous_path, target=path)
                # 不推进 `_loaded_path`：草稿仍属旧报告，sync_settings 也会拦住生成。
                return
        data, error = TS.read(path)
        if error:
            # R2 返工：读失败保持可识别错误态，不推进归属（面板值仍属上一份设置），
            # 由 sync_settings 阻断预览/生成/整期装配；修好 JSON 或「读取栏目表」后恢复。
            self._load_error = str(error)
            self._load_error_path = path
            self._notice(error, "error")
            self.page.mark("toc_settings_error", path=path, error=error)
            self._notify_page()
            return
        self._loaded_path = path
        self._loaded_report = report_key
        self._load_error = ""
        self._load_error_path = ""
        if not os.path.isfile(path):
            if previous_path and previous_report and previous_report != report_key:
                # 换报告且新报告还没有设置文件：用新报告的默认值（不继承上一份报告）。
                self.values = TS.defaults()
                self.selected = []
                self._saved_snapshot = self._snapshot()
                self._dirty = False
                self._sel_explicit = False
                self.page.mark("toc_settings_defaulted", path=path)
                self._notify_page()
                return
            # 文件还不存在：保留面板里的值，等用户改动后再自动写（不凭空建空文件）。
            self._saved_snapshot = self._snapshot()
            self._notify_page()
            return
        self.values = data
        self.selected = list(data.get("columnArticles") or [])
        self._sel_explicit = self._has_explicit_selection()
        self._saved_snapshot = self._snapshot()
        self._dirty = False
        self.page.mark("toc_settings_loaded", path=path, articles=len(self.selected),
                       auto=True)
        self._notify_page()

    def _has_explicit_selection(self) -> bool:
        """R1 返工：区分「专辑里的明确空集」与「别的栏目类型下的空列表」。

        非空勾选、或已保存的栏目类型就是「专辑」时才算明确选择；无栏目/专栏设置里的
        空列表只是「还没勾过」，首次切到专辑仍要默认全选（B-374 原契约不变）。
        """
        if self.selected:
            return True
        return str(self.values.get("columnKind") or "").strip() == "专辑"

    def _write_settings(self, path: str, force: bool = False) -> tuple[str, str]:
        """把面板 `values` + `selected` 写回指定路径；返回 `(path, error)`。

        私有 helper（B-373）：切换报告时草稿必须写回它原本所在的设置文件。
        """
        path = str(path or "")
        if not path:
            return "", "没有保存位置：先选信息检查报告或目录设置 JSON"
        data = dict(self.values)
        data["columnArticles"] = list(self.selected)
        snapshot = TS.normalize(data)
        if not force and snapshot == self._saved_snapshot:
            return path, ""
        try:
            ok, message = TS.save(path, data)
        except OSError as exc:  # 备份/写入/os.replace 的真实异常（R3 返工）
            return path, f"写入失败：{exc}"
        if not ok:
            return path, message
        self._saved_snapshot = snapshot
        self._dirty = False
        self.page.mark("toc_settings_saved", path=message, articles=len(self.selected),
                       kind=str(data.get("columnKind") or ""), auto=not force)
        return path, ""

    def sync_settings(self, force: bool = False) -> tuple[str, str]:
        """A2/A6：把面板的 `values` + `selected` 合并写回生效路径。

        返回 `(path, error)`；`error` 非空表示写失败（调用方必须显式报错并中止）。
        - 生效路径：显式「目录设置 JSON」优先，否则报告同目录（与引擎默认一致）；
        - 无改动且文件已存在时不重写，避免每次运行都产生一份 .bak；
        - 用户没改过且文件不存在时不创建空文件（等首次编辑）；
        - 草稿属于别的报告（切换报告时原路径没保存成功）→ 一律不写新路径（B-373）。
        """
        path = self._settings_path()
        if not path:
            return "", "没有保存位置：先选信息检查报告或目录设置 JSON"
        load_error = str(getattr(self, "_load_error", "") or "")
        if load_error:
            return path, (f"设置文件读取失败（{load_error}）："
                          "修好该 JSON 或点「读取栏目表」重新读入后再运行。")
        owner = str(getattr(self, "_loaded_path", "") or "")
        if self._dirty and owner and owner != path:
            return path, (f"栏目表草稿属于上一份报告的设置（{owner}），不能写进当前报告："
                          f"请切回该报告先保存，或点「读取栏目表」重新读入。")
        return self._write_settings(path, force=force)

    def column_issue(self) -> tuple[bool, str]:
        """A4 负向校验（2026-11-06 用户决定 b/c）：返回 `(是否阻止运行, 提示文案)`。

        模块级 `column_issue(values)` 是唯一判断源；这里只委托面板当前 values，
        保证面板、页面按钮、整期串行用的都是同一套四项必填规则。
        """
        return column_issue(self.values)

    # ── 批 123（用户 m02698）：专栏篇目勾选 ──────────────────────────────
    def _normalize_selected(self) -> None:
        """把历史设置里的「全名/folder」规范成当前报告的 folderName，勾选态才对得上。"""
        names = [row["name"] for row in self.available]
        if not names:
            return
        by_tail = {name.split()[-1]: name for name in names}
        fixed: list[str] = []
        for stored in self.selected:
            name = str(stored or "").strip()
            if not name:
                continue
            if name in names:
                fixed.append(name)
            elif name.split()[-1] in by_tail:
                fixed.append(by_tail[name.split()[-1]])
        if fixed != self.selected:
            self.selected = fixed
            self._mark_dirty()

    def _album_mode(self) -> bool:
        """2026-11-06 用户决定(c)：专辑 = 当前报告全部篇目自动收入。"""
        return str(self.values.get("columnKind") or "").strip() == "专辑"

    def _is_article_selected(self, name: str) -> bool:
        # 2026-11-06 用户决定(c)：专辑的有效选择永远是「当前报告全部篇目」；
        # 只影响展示/生成口径，不写回 selected（columnArticles 仍保存专栏历史）。
        if self._album_mode():
            return True
        tail = name.split()[-1]
        return any(stored == name or stored.split()[-1] == tail for stored in self.selected)

    def _toggle_article(self, name: str, checked: bool) -> None:
        if self._album_mode():
            return  # 决定(c)：专辑无逐篇勾选，也不得改动 columnArticles
        tail = name.split()[-1]
        kept = [stored for stored in self.selected
                if not (stored == name or stored.split()[-1] == tail)]
        if checked:
            kept.append(name)
        self.selected = kept
        self._sel_explicit = True
        self._mark_dirty()
        self._render_editor()

    def _select_all_articles(self) -> None:
        if self._album_mode():
            return  # 决定(c)：专辑自动收入全部，不把「全选」写成 columnArticles
        self.selected = [row["name"] for row in self.available]
        self._sel_explicit = True
        self._mark_dirty()
        self._render_editor()

    def _clear_articles(self) -> None:
        if self._album_mode():
            return  # 决定(c)：专辑不提供手动清空，也不得清掉专栏历史
        self.selected = []
        self._sel_explicit = True
        self._mark_dirty()
        self._render_editor()

    def _ensure_articles(self, report: str) -> None:
        """真实生成路径也把篇目清单载入面板（此前只死在 _preview_task 里）。

        先同步读《信息检查》旁边的 JSON（无需跑引擎）；读不到再异步回退旧预览接口。
        """
        report = str(report or "")
        if report and report == getattr(self, "_articles_for", "") and self.available:
            if self._apply_album_default():
                self._mark_dirty()
            return
        self._articles_for = report
        rows = articles_from_report(report)
        if rows:
            self.available = rows
            self._normalize_selected()
            if self._apply_album_default():
                self._mark_dirty()
            self.page.mark("toc_articles_loaded", report=report, count=len(rows))
            return
        self.available = []
        runner = getattr(getattr(self, "page", None), "page", None)
        run_task = getattr(runner, "run_task", None)
        if report and run_task is not None and not getattr(self, "_articles_loading", False):
            self._articles_loading = True
            try:
                run_task(self._load_articles_and_render)
            except Exception:  # noqa: BLE001 - 老页面/测试桩无 run_task 语义
                self._articles_loading = False

    async def _load_articles_and_render(self) -> None:
        try:
            await self._load_articles()
        finally:
            self._articles_loading = False
        if self.available:
            if self._apply_album_default():
                self._mark_dirty()
            self._render_editor()

    def _article_checklist(self) -> ft.Control:
        """勾选清单：勾上的篇目写进 columnArticles（顺序不影响渲染）。"""
        t = self.t
        chosen = sum(1 for row in self.available if self._is_article_selected(row["name"]))
        header = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
            C.text(f"专栏篇目（勾选 {chosen} / {len(self.available)} 篇）", t, "small",
                   weight="semibold"),
            ft.Container(expand=True),
            C.text_button("全选", lambda e: self._select_all_articles(), t),
            C.text_button("清空", lambda e: self._clear_articles(), t),
        ])
        lines: list[ft.Control] = [header]
        for row in self.available:
            name = row["name"]
            title = row.get("title") or ""
            controls: list[ft.Control] = [
                ft.Checkbox(
                    label=name, value=self._is_article_selected(name),
                    label_style=ft.TextStyle(size=t.font("small"), color=t.palette.text),
                    on_change=lambda e, n=name: self._toggle_article(
                        n, bool(e.control.value)),
                ),
            ]
            if title:
                controls.append(ft.Container(
                    expand=True,
                    content=C.text(title, t, "caption", t.palette.text_faint,
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                   tooltip=title),
                ))
            lines.append(ft.Row(controls, spacing=t.sp("1"),
                                vertical_alignment=ft.CrossAxisAlignment.CENTER))
        return ft.Column(spacing=t.sp("half"), controls=lines)


    # ── 栏目表 ─────────────────────────────────────────────────────────
    def _dialog_page(self):
        """flet Page：面板挂在 IssuePage 下（旧测试桩可能没有 .page）。"""
        return getattr(self.page, "page", self.page)

    def _load_settings(self) -> None:
        path = self._settings_path()
        if not path:
            self._notice("请先选信息检查报告或目录设置文件", "warn")
            return
        if self._dirty:
            # OBS-A（2026-11-06 用户决定(f)）：读取会覆盖未保存草稿，必须先问。
            self._confirm_load_settings(path)
            return
        self._apply_load_settings(path)

    def _confirm_load_settings(self, path: str) -> None:
        """读取栏目表三向确认：保存草稿并读取 / 放弃草稿并读取 / 取消。

        C.confirm 只有两个按钮，这里沿用 pages._confirm_overwrite 的 ft.AlertDialog 模式；
        保存失败时保持草稿与归属、不推进读取路径（用户决定(f)）。
        """
        t = self.t

        def _cancel(_event=None) -> None:
            C.dismiss_top_dialog(self._dialog_page())

        def _discard(_event=None) -> None:
            C.dismiss_top_dialog(self._dialog_page())
            self._apply_load_settings(path)

        def _save_then_load(_event=None) -> None:
            C.dismiss_top_dialog(self._dialog_page())
            draft_path = str(getattr(self, "_loaded_path", "") or "") or self._settings_path()
            _saved, error = self._write_settings(draft_path, force=True)
            if error:
                # 保存失败：保持当前草稿与归属，绝不推进读取路径/文件。
                self._notice(f"草稿保存失败：{error}", "error")
                self.page.mark("toc_settings_error", path=draft_path, error=error)
                return
            self._apply_load_settings(path)

        dialog = ft.AlertDialog(
            modal=True,
            title=C.text("读取栏目表会覆盖草稿", t, "h2"),
            content=ft.Container(width=t.px(460), content=ft.Column([
                C.text("栏目表里有还没保存的修改：直接读取会丢掉它们。", t, "small"),
                C.text(f"要读取：{path}", t, "caption", t.palette.text_muted, max_lines=2),
            ], spacing=t.sp("1"), tight=True)),
            actions=[
                C.text_button("取消", _cancel, t),
                C.text_button("放弃草稿并读取", _discard, t),
                C.primary_button("保存草稿并读取", _save_then_load, t),
            ],
        )
        C.show_dialog(self._dialog_page(), dialog)

    def _apply_load_settings(self, path: str) -> None:
        """真正读入；读失败保持旧状态（values/selected/_loaded_path 一概不动）。"""
        data, error = TS.read(path)
        if error:
            self._load_error = str(error)
            self._load_error_path = path
            self._notice(error, "error")
            self.page.mark("toc_settings_error", path=path, error=error)
            self._notify_page()
            return
        self._loaded_path = path
        self._loaded_report = self._report()
        self._load_error = ""
        self._load_error_path = ""
        self.values = data
        self.selected = list(data.get("columnArticles") or [])
        self._sel_explicit = self._has_explicit_selection()
        self._saved_snapshot = self._snapshot()
        self._dirty = False
        self.editor_box.controls = self._editor_controls()
        self._notice(f"已读入：{path}", "info")
        self.page.mark("toc_settings_loaded", path=path, articles=len(self.selected))
        self._safe_update(self.editor_box)
        self._notify_page()

    def _save_settings(self) -> None:
        path, error = self.sync_settings(force=True)
        if error:
            self._notice(error, "error")
            self.page.mark("toc_settings_error", path=path, error=error)
            return
        self._notice(f"已保存：{path}（原文件已备份）", "success")

    def _editor_controls(self) -> list:
        t = self.t
        # 用户 m03037：栏目类型 / 栏目名称 / 栏目英文名 / 主持人 排成 2×2 等宽网格；
        # 栏目类型用 48 高 C.select_box，与三个浮动输入框盒高一致、基线对齐；
        # 窄面板（两格放不下）由 C.form_grid 自动降单列，不再挤出画面。
        kind_value = str(self.values.get("columnKind") or "")
        kind_options = [(key, TS.KIND_LABELS[key]) for key in TS.COLUMN_KINDS]
        if kind_value and kind_value not in TS.COLUMN_KINDS:
            kind_options.append((kind_value, kind_value))
        self.kind = C.select_box(
            t, "栏目类型",
            value_text=dict(kind_options).get(kind_value)
            or dict(kind_options).get(TS.COLUMN_KINDS[0], "未指定"),
            options=kind_options, on_select=self._on_kind_select, expand=True,
        )
        name = self._field("columnName", "栏目名称（中文）")
        name_en = self._field("columnNameEN", "栏目英文名")
        host = self._field("columnHost", "主持人")
        # 2026-11-06 用户决定(b)/(c)：四项语义必填（栏目类型为空时不影响生成，也不显示勾选）。
        rows: list = [
            C.form_grid(t, [
                self.kind,
                C.form_field(t, "栏目名称（中文）", name),
                C.form_field(t, "栏目英文名", name_en),
                C.form_field(t, "主持人", host),
            ]),
        ]
        # 2026-11-06 用户决定(b)：四项不完整时紧贴四项字段给可见提示（与 header 按钮
        # tooltip、页面门用的是同一句 column_issue 文案）；补齐或切回无栏目后消失。
        rows.append(self._gate_hint_control())
        kind = str(self.values.get("columnKind") or "").strip()
        if kind == "专栏" and self.available:
            # 批 123（用户 m02698）：专栏直接勾选篇目，不再依赖「逐篇加入」。
            rows.append(self._article_checklist())
        elif kind == "专栏":
            # 专栏但篇目还没载入：保留历史勾选的手动增删（专栏专属，见用户决定(b)）。
            if not self.selected:
                rows.append(C.text("栏目篇目：尚未选择。先运行第 1 步「检查 tex 信息」，篇目清单会自动载入。",
                                   t, "caption", t.palette.text_faint))
            for index, article in enumerate(self.selected):
                # 用户 m03037：篇目名占满剩余宽度并单行省略——超长篇名不会把 ↑↓/删除 挤出面板。
                rows.append(ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                   controls=[
                    ft.Container(
                        expand=True,
                        content=C.text(article, t, "small", weight="medium", max_lines=1,
                                       overflow=ft.TextOverflow.ELLIPSIS, tooltip=article),
                    ),
                    C.text_button("↑", lambda e, i=index: self._move(i, -1), t),
                    C.text_button("↓", lambda e, i=index: self._move(i, 1), t),
                    # 用户 m02990：移除篇目是破坏性动作 → danger 按钮；↑↓ 属列表内微调保留文字链。
                    C.danger_button("删除", lambda e, i=index: self._remove(i), t,
                                    icon="DELETE_OUTLINE"),
                ]))
            if not self.selected:
                rows.append(C.text("还没有篇目清单：先运行第 1 步「检查 tex 信息」生成报告，篇目会自动载入。",
                                   t, "caption", t.palette.text_faint))
        elif kind == "专辑":
            # 2026-11-06 用户决定(c)：专辑 = 当期全部篇目自动收入；不显示逐篇勾选/手动增删。
            count = len(self.available)
            note = (f"本期全部 {count} 篇自动收入（自动，无需勾选）。" if count
                    else "本期全部篇目自动收入；运行第 1 步载入篇目后这里显示数量。")
            rows.append(C.text(note, t, "caption", t.palette.text_muted))
        else:
            # 2026-11-06 用户决定(a)：本期无栏目 → 目录不带栏目信息，勾选/手动增删全部隐藏。
            rows.append(C.text("本期无栏目：目录不带栏目信息，无需勾选篇目。",
                               t, "caption", t.palette.text_faint))
        return rows

    def _gate_hint_control(self) -> ft.Control:
        """编辑区可见的「缺项」提示容器（module 级 column_issue 是唯一判断源）。"""
        box = getattr(self, "gate_hint", None)
        if box is None:
            box = ft.Container()
            self.gate_hint = box
        blocked, hint = column_issue(self.values)
        box.content = C.notice_bar(hint, self.t, "warn") if blocked else None
        return box

    def _refresh_gate_hint(self) -> None:
        box = self._gate_hint_control()
        try:
            box.update()
        except Exception:  # noqa: BLE001 - 未挂载（测试桩/首次构建前）
            pass

    def _apply_album_default(self) -> bool:
        """B-375（2026-11-06 用户决定(c)）：不再把「专辑=全选」写进 selected。

        专辑的「全部篇目」是展示与生成时的有效状态（由 `_is_article_selected`
        统一回答），不能覆盖 selected——否则会把专栏历史勾选写坏。
        保留该方法仅为旧调用点/旧测试兼容，永远返回 False（无副作用）。
        """
        return False

    def _on_kind_select(self, key: str, label: str) -> None:
        """栏目类型选择：写入设置值并原地更新浮动下拉的显示文案（用户 m03037）。

        2026-11-06 用户决定(a)/(b)/(c)：切到专辑显示「全部自动收入」、切回专栏恢复
        selected 里的专栏历史、无栏目隐藏勾选；三者都不写 columnArticles。
        """
        self._set_value("columnKind", key)
        box = getattr(self, "kind", None)
        if box is not None and getattr(box, "_value_text", None) is not None:
            box._value_text.value = label
            try:
                box._value_text.update()
            except Exception:  # noqa: BLE001 - 未挂载时仅更新数据
                pass
        self._render_editor()

    def _field(self, key: str, label: str) -> ft.TextField:
        """栏目编辑区输入框：expand 填满网格格，标签由 form_field 浮动显示。"""
        return C.text_field(
            self.t, value=str(self.values.get(key) or ""), expand=True,
            on_change=lambda e, k=key: self._set_value(k, e.control.value),
        )

    def _move(self, index: int, delta: int) -> None:
        target = index + delta
        if 0 <= target < len(self.selected):
            self.selected[index], self.selected[target] = self.selected[target], self.selected[index]
            self._mark_dirty()
            self._render_editor()

    def _remove(self, index: int) -> None:
        if 0 <= index < len(self.selected):
            self.selected.pop(index)
            self._sel_explicit = True
            self._mark_dirty()
            self._render_editor()

    def _render_editor(self) -> None:
        self.editor_box.controls = self._editor_controls()
        self._safe_update(self.editor_box)
        page = getattr(self, "page", None)
        hook = getattr(page, "mark", None)
        if callable(hook):
            try:
                hook("toc_editor", articles=len(self.selected))
            except Exception:  # noqa: BLE001 - 页面桩/未挂载
                pass

    # ── 预览 / 生成 ────────────────────────────────────────────────────
    async def _preview_task(self) -> None:
        report = self._report()
        if not report:
            self._notice("请先选信息检查报告（可先运行「检查 tex 信息」）", "error")
            self.page.mark("validation_error", step="toc", reason="missing report")
            return
        blocked, hint = self.column_issue()
        if blocked:
            # B-375 Lead 返工（m00363 A，spec line 28）：辅助预览不锁死——只要选了报告
            # 就继续预览，缺项只做警示；引擎 preview 会回 columnValidationError，
            # 由下面的信息卡展示。生成入口（按钮/重新生成/仅TeX/整期串行）仍然阻断。
            self._notice(hint + "（预览仍会继续；补齐前不能生成目录。）", "warn")
            self.page.mark("toc_column_invalid", reason=hint, blocked=True)
        elif hint:
            self._notice(hint, "warn")
        settings_path, save_error = self.sync_settings()
        if save_error:
            self._notice(f"栏目表保存失败：{save_error}", "error")
            self.page.mark("toc_settings_error", path=settings_path, error=save_error)
            return
        # B-375 Lead m00904：与生成侧 build_step_call 同一套「只传已存在的设置文件」
        # 解析。sync_settings 在「路径已确定但文件还不存在且无改动」时不写文件；
        # 此时把不存在的路径传给引擎会被拒绝（原生 smoke：首次进入预览失败）。
        # 因此这里 isfile 才传，否则回退到报告同目录已存在的设置，再没有就省略
        # --settings 让引擎用内置默认（不创建任何设置文件）。
        settings = existing_settings_path(settings_path)
        if not settings:
            saved = TS.path_for(os.path.dirname(report))
            settings = existing_settings_path(saved)
        self.info.content = C.text("预览中…", self.t, "small", self.t.palette.text_muted)
        call = EB.toc_call(report=report, template=self.page.field("toc_template"),
                           settings=settings, preview=True)
        try:
            result = await asyncio.to_thread(EB.read_json_command, call, timeout=180,
                                             notes=self.app.notes_repo)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "json": {}}
        payload = result.get("json") or {}
        if not result.get("ok") or not payload:
            error_text = first_line(result.get("error")) or "引擎没有返回 JSON"
            self._notice(f"预览失败：{error_text}", "error")
            # Lead m00984(2)：预览失败/异常后信息卡不能一直停留在「预览中…」；
            # 换成失败说明（feedback 里另有错误行），避免看起来像卡住。
            self.info.content = C.notice_bar(f"预览失败：{error_text}", self.t, "error")
            self._safe_update(self.info)
            self.page.mark("toc_preview", ok=False, error=str(result.get("error") or ""))
            return
        self.payload = payload
        self.values["columnArticles"] = list(self.payload.get("columnArticles") or [])
        if not self.selected:
            self.selected = list(self.values["columnArticles"])
        if not self.available:
            await self._load_articles()
        warning = str(payload.get("columnValidationError") or "").strip()
        # task-6(1)：专辑行显示「本期全部 N 篇自动收入」；N 优先用面板已载入的当前
        # 报告篇目数，载入失败/为空时用引擎 preview 回传的 articles 数（只显示，不写回）。
        card_lines: list = [
            C.text(line, self.t, "small")
            for line in preview_lines(
                payload,
                column_kind=str(self.values.get("columnKind") or ""),
                report_articles=len(self.available) or None,
            )
        ]
        if warning:
            # B-375：引擎 preview 仍退出 0，但把「四项不齐/种类无法识别」交回外壳显示。
            card_lines.append(C.notice_bar(f"引擎：{warning}", self.t, "warn"))
        self.info.content = C.card(ft.Column(spacing=self.t.sp("half"), controls=card_lines),
                                   self.t)
        self._render_editor()
        self._safe_update(self.info)
        self.page.mark("toc_preview", ok=True,
                       source=payload.get("templateSource"),
                       column=bool(payload.get("columnEnabled")),
                       articles=len(self.available))

    async def _load_articles(self) -> None:
        issue = self.page.field("issue_dir")
        if not issue:
            return
        try:
            result = await asyncio.to_thread(
                EB.read_json_command, EB.infocheck_call(folder=issue, preview=True), timeout=180)
        except Exception:  # noqa: BLE001
            return
        self.available = article_rows(result.get("json") or {})
        if self.available:
            self._articles_for = self._report()
            self._normalize_selected()
        if not self.available:
            self._notice("篇目清单为空：先运行「检查 tex 信息」生成报告", "warn")

    async def _generate_task(self) -> None:
        report = self._report()
        if not report:
            self._notice("请先选信息检查报告（可先运行「检查 tex 信息」）", "error")
            self.page.mark("validation_error", step="toc", reason="missing report")
            return
        blocked, hint = self.column_issue()
        if blocked:
            self._notice(hint, "warn")
            self.page.mark("toc_column_invalid", reason=hint, blocked=True)
            return
        if hint:
            self._notice(hint, "warn")
        settings_path, save_error = self.sync_settings()
        if save_error:
            self._notice(f"栏目表保存失败：{save_error}", "error")
            self.page.mark("toc_settings_error", path=settings_path, error=save_error)
            return
        try:
            result_path = ensure_result_dir(report)
        except OSError as exc:
            self._notice(f"无法创建结果目录：{exc}", "error")
            self.page.mark("toc_generated", ok=False, error=str(exc), text_only=self._text_only())
            return
        text_only = self._text_only()
        call = EB.toc_call(report=report, template=self.page.field("toc_template"),
                           settings=settings_path, text_only=text_only,
                           result_json=result_path)
        hint = ("运行中 · 生成 tex（不写入、不编译）" if text_only
                else "生成并编译中…（首次编译约 1–3 分钟）")
        self.feedback.content = C.text(hint, self.t, "small", self.t.palette.text_muted)
        self._safe_update(self.feedback)
        try:
            result = await asyncio.to_thread(EB.read_json_command, call, timeout=900,
                                             notes=self.app.notes_repo)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "json": {}}
        payload = result.get("json") or read_result(report)
        # 引擎正式生成只回日志、不打印 JSON；有结果 JSON 就算成功（退出码 1 仅表示需人工核对）。
        ok = bool(payload)
        rows: list = [C.text(line, self.t, "small") for line in result_lines(payload)]
        warnings = [line for line in result_lines(payload) if line.startswith("注意")]
        if not ok:
            rows.append(C.notice_bar(f"生成失败：{first_line(result.get('error')) or '没有结果 JSON'}",
                                     self.t, "error"))
        if ok and text_only:
            rows.append(C.notice_bar("仅生成 tex，未写入 000。", self.t, "info"))
        if ok and result.get("code") not in (0, None):
            rows.append(C.notice_bar(
                f"引擎退出码 {result.get('code')}：产物已生成，请人工核对提醒项。", self.t, "warn"))
        shown = {str(path) for _label, path in result_paths(payload)}
        for label, path, inferred in result_outputs(payload, self.page.field("issue_dir")):
            if path in shown:
                continue
            shown.add(path)
            rows.append(ft.Row(spacing=self.t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[
                C.text(label + ("（推断）" if inferred else ""), self.t, "small",
                       self.t.palette.text_muted),
                C.text(path, self.t, "caption", self.t.palette.text_faint),
                ft.Container(expand=True),
                C.text_button("打开", lambda e, p=path: self.app.open_path(p), self.t),
            ]))
        rows.append(ft.Row(spacing=self.t.sp("2"), controls=[
            C.text("结果 JSON", self.t, "small", self.t.palette.text_muted),
            ft.Container(expand=True),
            C.text_button("打开", lambda e: self.app.open_path(result_path), self.t),
        ]))
        self.feedback.content = C.card(ft.Column(spacing=self.t.sp("1"), controls=rows), self.t)
        self._safe_update(self.feedback)
        if ok:
            self.app.store.set_recent("toc_report", report)
        self.page.mark("toc_generated", ok=ok, result=result_path,
                       settings=settings_path, text_only=text_only,
                       warnings=len(warnings),
                       zh_pdf=bool((payload.get("placed") or {}).get("zhPdf")),
                       error=str(result.get("error") or ""))

    def refresh_result(self, report: str = "", code=None, failed: bool = False,
                       cancelled: bool = False, *, this_run: bool = False) -> None:
        """OBS-2：toc 步骤在页外（整期串行/其他视图）跑完后刷新面板反馈区。

        面板可能不在 DOM（其他视图/串行运行），所有 update 都走 _safe_update。
        本轮失败/取消时只写明确结论；旧结果仅以「上次结果（非本轮）」文字列出，
        不提供产物打开按钮，绝不把上一次的成品冒充成本轮产物（Lead m00363 B/D）。
        """
        t = self.t
        report = str(report or self._report() or "")
        payload = read_result(report) if report else {}
        rows: list = []
        stale = bool(failed or cancelled)
        if cancelled:
            # Lead task-22（原生取消观察）：Engine 取消不回滚，CN 落位/拟合后取消时
            # 000 目录可能已有本轮部分 tex/pdf；不能再断言「未产生新结果」。
            rows.append(C.notice_bar(
                "本次已停止，未生成结果摘要；000 目录可能已部分更新，请重新生成后再使用。",
                t, "warn"))
        elif failed:
            rows.append(C.notice_bar(
                f"本次生成未成功（退出码 {code}）：原因见整期日志；修好后点「重新生成目录」。",
                t, "error"))
        elif payload and this_run:
            rows.append(C.notice_bar("本次生成完成。", t, "success"))
        elif payload:
            # Lead round2 C1（结果归属）：结果 JSON 是本期最近一次生成的产物，本身没有
            # 报告身份；重启/初次加载/切到别的报告后读到它不能声称「本次」，只能作参考。
            rows.append(C.text("上次生成结果（非本轮，可能由其他检查报告触发），仅作参考。",
                               t, "caption", t.palette.text_muted))
        elif this_run:
            rows.append(C.notice_bar("本次没有生成结果 JSON：原因见整期日志。", t, "warn"))
        else:
            rows.append(C.text("还没有读到目录结果 JSON；运行「生成目录」后这里显示结果。",
                               t, "caption", t.palette.text_faint))
        if payload and stale:
            rows.append(C.text("上次结果（非本轮，仅供参考）：", t, "caption",
                               t.palette.text_muted))
            rows += [C.text(line, t, "caption", t.palette.text_faint)
                     for line in result_lines(payload)]
        elif payload:
            rows += [C.text(line, t, "small") for line in result_lines(payload)]
            for label, path, inferred in result_outputs(payload, self.page.field("issue_dir")):
                rows.append(ft.Row(spacing=t.sp("2"),
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                   controls=[
                    C.text(label + ("（推断）" if inferred else ""), t, "small",
                           t.palette.text_muted),
                    C.text(path, t, "caption", t.palette.text_faint),
                    ft.Container(expand=True),
                    C.text_button("打开", lambda e, p=path: self.app.open_path(p), t),
                ]))
            if report:
                rows.append(ft.Row(spacing=t.sp("2"), controls=[
                    C.text("结果 JSON", t, "small", t.palette.text_muted),
                    ft.Container(expand=True),
                    C.text_button("打开", lambda e, p=existing_result_json_path(report):
                                  self.app.open_path(p), t),
                ]))
        self.feedback.content = C.card(ft.Column(spacing=t.sp("1"), controls=rows), t)
        self._safe_update(self.feedback)

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _notice(self, message: str, tone: str = "info") -> None:
        self.feedback.content = C.notice_bar(message, self.t, tone)
        self._safe_update(self.feedback)

    def _safe_update(self, *controls) -> None:
        for control in controls:
            try:
                control.update()
            except Exception:  # noqa: BLE001
                pass
