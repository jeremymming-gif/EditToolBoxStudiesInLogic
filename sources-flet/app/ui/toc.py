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



def preview_lines(payload: dict) -> list[str]:
    payload = payload or {}
    lines = [f"模板来源：{payload.get('templateSource') or '（未知）'}"]
    note = str(payload.get("templateNote") or "").strip()
    if note:
        lines.append(note)
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
            self.page.add_picker("信息检查报告", "toc_report", "期目录下的 信息检查*.txt",
                                 kind="file"),
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

    def _set_value(self, key: str, value) -> None:
        self.values[key] = str(value or "").strip()
        self._mark_dirty()

    def _report(self) -> str:
        report = self.page.field("toc_report")
        if not report:
            report = latest_report(self.page.field("issue_dir"))
            if report:
                self.page.set_field("toc_report", report)
        return report

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
                return
            # 文件还不存在：保留面板里的值，等用户改动后再自动写（不凭空建空文件）。
            self._saved_snapshot = self._snapshot()
            return
        self.values = data
        self.selected = list(data.get("columnArticles") or [])
        self._sel_explicit = self._has_explicit_selection()
        self._saved_snapshot = self._snapshot()
        self._dirty = False
        self.page.mark("toc_settings_loaded", path=path, articles=len(self.selected),
                       auto=True)

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
        """A4 负向校验：返回 `(是否阻止运行, 提示文案)`。"""
        kind = str(self.values.get("columnKind") or "").strip()
        name = str(self.values.get("columnName") or "").strip()
        if kind and not name:
            return True, (f"已选栏目类型「{kind}」但栏目名称为空：请填写栏目名称，"
                          "或把类型改成「本期无栏目」。")
        if not kind and self.selected:
            return False, ("栏目类型为空，但已勾选篇目：本次按「本期无栏目」生成，"
                           "勾选不会生效。")
        return False, ""

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

    def _is_article_selected(self, name: str) -> bool:
        tail = name.split()[-1]
        return any(stored == name or stored.split()[-1] == tail for stored in self.selected)

    def _toggle_article(self, name: str, checked: bool) -> None:
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
        self.selected = [row["name"] for row in self.available]
        self._sel_explicit = True
        self._mark_dirty()
        self._render_editor()

    def _clear_articles(self) -> None:
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
        if not str(self.values.get("columnKind") or "").strip():
            lines.append(C.text("勾选栏目类型并填写栏目名称后，下面的勾选才写入目录。",
                                t, "caption", t.palette.text_faint))
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
    def _load_settings(self) -> None:
        path = self._settings_path()
        if not path:
            self._notice("请先选信息检查报告或目录设置文件", "warn")
            return
        data, error = TS.read(path)
        if error:
            self._load_error = str(error)
            self._load_error_path = path
            self._notice(error, "error")
            self.page.mark("toc_settings_error", path=path, error=error)
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
        name_en = self._field("columnNameEN", "栏目英文名（可空）")
        host = self._field("columnHost", "主持人（可空）")
        rows: list = [
            C.form_grid(t, [
                self.kind,
                C.form_field(t, "栏目名称（中文）", name),
                C.form_field(t, "栏目英文名（可空）", name_en),
                C.form_field(t, "主持人（可空）", host),
            ]),
        ]
        if self.available:
            # 批 123（用户 m02698）：直接勾选专栏篇目，不再依赖「逐篇加入」。
            rows.append(self._article_checklist())
        else:
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
        return rows

    def _apply_album_default(self) -> bool:
        """用户 m02929：勾选「专辑」时默认整期都是专辑稿件（全部勾上，可再手动取消）。

        B-374：只在「还没形成明确勾选」时补默认值。设置文件里的 `columnArticles`
        （含空列表）与用户清空/取消都属于明确选择，切页重建不得再覆盖成全选。
        """
        if str(self.values.get("columnKind") or "").strip() != "专辑":
            return False
        if not self.available or self.selected:
            return False
        if getattr(self, "_sel_explicit", False):
            return False
        self.selected = [row["name"] for row in self.available]
        return True

    def _on_kind_select(self, key: str, label: str) -> None:
        """栏目类型选择：写入设置值并原地更新浮动下拉的显示文案（用户 m03037）。"""
        self._set_value("columnKind", key)
        box = getattr(self, "kind", None)
        if box is not None and getattr(box, "_value_text", None) is not None:
            box._value_text.value = label
            try:
                box._value_text.update()
            except Exception:  # noqa: BLE001 - 未挂载时仅更新数据
                pass
        if self._apply_album_default():
            self._mark_dirty()
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
        self.page.mark("toc_editor", articles=len(self.selected))

    # ── 预览 / 生成 ────────────────────────────────────────────────────
    async def _preview_task(self) -> None:
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
        self.info.content = C.text("预览中…", self.t, "small", self.t.palette.text_muted)
        call = EB.toc_call(report=report, template=self.page.field("toc_template"),
                           settings=settings_path, preview=True)
        try:
            result = await asyncio.to_thread(EB.read_json_command, call, timeout=180,
                                             notes=self.app.notes_repo)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "json": {}}
        payload = result.get("json") or {}
        if not result.get("ok") or not payload:
            self._notice(f"预览失败：{first_line(result.get('error')) or '引擎没有返回 JSON'}",
                         "error")
            self.page.mark("toc_preview", ok=False, error=str(result.get("error") or ""))
            return
        self.payload = payload
        self.values["columnArticles"] = list(self.payload.get("columnArticles") or [])
        if not self.selected:
            self.selected = list(self.values["columnArticles"])
        if not self.available:
            await self._load_articles()
        self.info.content = C.card(ft.Column(spacing=self.t.sp("half"), controls=[
            C.text(line, self.t, "small") for line in preview_lines(payload)
        ]), self.t)
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
