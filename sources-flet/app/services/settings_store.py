"""设置持久化：兼容 Tk 版 `%APPDATA%\\EditToolBox\\settings.json`。

设计口径（P2）：
- 文件路径沿用 Tk 版（用户升级后不会丢配置）。
- 旧键（options/api/bibdb/envCheckDone/engineDir/notesRepoPath…）原样保留，**不破坏**；
  本外壳的全部新增状态写进同一个文件的 `flet` 对象里（版本 2）。
- 保存用「临时文件 + os.replace」原子替换；P2_SETTINGS 环境变量可隔离证据运行。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from services import ai_prefs

APP_NAME = "编辑部工具箱"
SCHEMA_KEY = "flet"
SCHEMA_VERSION = 2

#: 旧壳 `settings.py` 的 `DEFAULT_VERIFY_MAX`（只给界面显示，不参与判据）
DEFAULT_VERIFY_MAX = 120
#: 引用库「核实」段（R6）：与 Tk 版 `bibdb` 段同口径，缺省 = 一个参数都不加
BIBDB_DEFAULTS = {
    "verify_max": None,
    "verify_plan": False,
    "verify_all_refs": False,
    "lookup_enabled": True,
}
#: 批 114 导航迁移用：引用库视图 → 组（与 ui/ia_map.NAV_TREE 同口径；这里只是
#: 纯数据，避免 services 反向依赖 app/ui。新增视图时两处同步）。
BIB_VIEW_GROUPS = {
    "overview": "read", "query": "read",
    "normalize": "intake", "import": "intake",
    "review": "treat", "edit": "treat", "dupes": "treat", "fieldfix": "treat",
    "health": "maint", "settings": "maint",
}


def settings_path() -> Path:
    override = os.environ.get("P2_SETTINGS", "").strip()
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "EditToolBox" / "settings.json"
    return Path.home() / ".editorial-toolbox" / "settings.json"


def _defaults() -> dict:
    return {
        "theme": "light",
        "zoom": 1.0,
        "last_page": "home",
        "window": {"width": 1240, "height": 860, "left": None, "top": None},
        "recent": {
            "single_source": "",
            "single_out": "",
            "bib_returned": "",
            "bib_original": "",
            "bib_tex": "",
            "bib_out": "",
            "proof_tex": "",
            "issue_dir": "",
            "toc_report": "",
            "dirpush_out": "",
            "merge_out": "",
        },
        "options": {
            "convert_compile": True,
            "convert_clean": False,
            "convert_source_pdf": False,
            "convert_verify": False,
            "proofcheck_fix": False,
            # 批 109 Wave 0 / S4：与旧壳一致，默认「仅生成 tex」（不落位、不编译）。
            "toc_text_only": True,
            "compileall_dry_run": False,
            "dirpush_with_en_toc": False,
            "dirpush_dry_run": False,
            "log_summary_only": True,
        },
        "paths": {"engine_dir": "", "notes_repo": ""},
        # 引用库核实段（R6）：None = 不传 --verify-max（引擎默认 120 条）
        "bibdb": dict(BIBDB_DEFAULTS),
        "log_show_raw": False,
        "env_check_done": False,
        # 引用库导航（批 112 S3a1）：当前视图 + 每组「上次视图」记忆。
        # 只写内存，退出时随 main.py 的唯一 save 落盘；切视图不产生磁盘 IO。
        "bib_view": "overview",
        "bib_group_memory": {},
        # 批 114 导航（用户 m03256）：上次路由 + 当前展开的页面/组。
        # route 形如 `single:proofcheck` / `issue:toc` / `bib:edit`；无二级视图的页面就是 key。
        "last_route": "home",
        "nav_open": {"page": "home", "group": ""},
        # 主按钮配色（批 114 P2 v2）：「c」石板蓝（默认）/「d」石墨。
        "button_palette": "c",
        # 关闭窗口 → 最小化到托盘（仅 Windows；默认关，见 App.set_tray_enabled）
        "tray_enabled": False,
        "engine_override": "",
        # AI 段：总开关默认关；Key 只写本机设置文件，不进日志/证据/仓库
        "ai": ai_prefs.defaults(),
        "updated_at": "",
    }


def normalize_tray_enabled(value) -> bool:
    """托盘开关归一：缺省/坏值一律 False；兼容手改配置里的 "true"/"1"。

    非 Windows 的运行时判断在 App 里做；这里只保证存储值是真正的 bool，
    避免旧配置缺键或写成字符串时把「关闭即退出」变成意外的隐藏。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def normalize_button_palette(value) -> str:
    """按钮配色归一：只认 C/D，缺键或坏值回默认 C（旧配置必须能读）。"""
    text = str(value or "").strip().lower()
    return text if text in ("c", "d") else "c"


def pick(store, dotted: str, allowed, default):
    """白名单取值（纯函数，不写盘）：缺键 / 非法值一律回 `default`。

    `store` 显式传入，只需实现 `get(dotted, default)`（`SettingsStore` 或测试假壳），
    这样本函数可直接单测；页面侧也可用 `store.pick(dotted, allowed, default)` 三参形式。
    """
    value = store.get(dotted, default)
    try:
        valid = value in allowed
    except TypeError:  # 手改配置把值写成 list/dict 时 `in` 会抛，视为非法
        valid = False
    return value if valid else default


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else settings_path()
        self.raw: dict = {}
        self.data: dict = _defaults()
        self.exists = False

    # ── 读取 ────────────────────────────────────────────────────────────
    def load(self) -> tuple[dict, bool]:
        self.raw, self.exists = {}, self.path.exists()
        if self.exists:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.raw = loaded
            except (OSError, ValueError):
                self.raw = {}
        section = self.raw.get(SCHEMA_KEY)
        self.data = _defaults()
        legacy = self._legacy_view()
        if legacy:
            # Tk 旧配置先铺底，改造成家壳初值
            self.data = _deep_merge(self.data, legacy)
        if isinstance(section, dict):
            # 已保存过的 Flet 段拥有最终优先级（旧键只在首次迁移时生效）
            self.data = _deep_merge(self.data, section)
        self.data["tray_enabled"] = normalize_tray_enabled(self.data.get("tray_enabled"))
        # 旧 settings.json 没有该键 → _defaults 已补 "c"；坏值/大写也归一。
        self.data["button_palette"] = normalize_button_palette(
            self.data.get("button_palette"))
        # 批 114：没有 last_route 的旧配置（或手改成坏值）→ 由 last_page / single_flow /
        # bib_view 推导一次；之后按新键读写。坏 nav_open 也补成合法 dict。
        # 注意：_defaults 本身带 last_route="home"，所以「是否迁移」要看保存文件里
        # 是否真的写过该键，而不是看合并后的 data。
        saved_route = section.get("last_route") if isinstance(section, dict) else None
        route = self.data.get("last_route")
        if not isinstance(route, str) or not route.strip():
            self._migrate_route()
        elif not isinstance(saved_route, str) or not saved_route.strip():
            self._migrate_route()
        open_state = self.data.get("nav_open")
        if not isinstance(open_state, dict):
            self.data["nav_open"] = {"page": "", "group": ""}
        return self.data, self.exists

    def _migrate_route(self) -> None:
        """旧键 → last_route / nav_open（仅首次迁移或坏值时调用；不删旧键）。"""
        page = str(self.data.get("last_page") or "home")
        flow = str(self.data.get("single_flow") or "convert")
        view = str(self.data.get("bib_view") or "overview")
        if page == "single":
            route = f"single:{flow if flow in ('convert', 'proofcheck') else 'convert'}"
        elif page == "issue":
            route = "issue:overview"
        elif page == "bib":
            route = f"bib:{view if view in BIB_VIEW_GROUPS else 'overview'}"
        elif page in ("home", "settings", "help"):
            route = page
        else:
            route = "home"
        page_key = route.partition(":")[0]
        group = BIB_VIEW_GROUPS.get(route.partition(":")[2], "") if page_key == "bib" else ""
        self.data["last_route"] = route
        self.data["nav_open"] = {"page": page_key, "group": group}


    def _legacy_view(self) -> dict:
        """把 Tk 版遗留键映射成家壳能用的初值（只读，不删原键）。"""
        legacy: dict = {}
        options = self.raw.get("options")
        if isinstance(options, dict):
            mapped = {}
            pairs = {
                "convert_compile": "convertCompile",
                "convert_clean": "convertClean",
                "convert_source_pdf": "convertSourcePdf",
                "convert_verify": "convertVerifyRefs",
            }
            for new_key, old_key in pairs.items():
                if old_key in options:
                    mapped[new_key] = bool(options[old_key])
            if mapped:
                legacy["options"] = mapped
        legacy["engine_override"] = str(self.raw.get("engineDir") or "")
        notes = str(self.raw.get("notesRepoPath") or "")
        if notes:
            legacy["paths"] = {"notes_repo": notes}
        show_raw = self.raw.get("logShowRaw")
        if isinstance(show_raw, bool):
            legacy["log_show_raw"] = show_raw
        if self.raw.get("envCheckDone") is True:
            legacy["env_check_done"] = True
        mapped_bibdb = _legacy_bibdb(self.raw.get("bibdb"))
        if mapped_bibdb:
            legacy["bibdb"] = mapped_bibdb
        return legacy

    # ── 写入 ────────────────────────────────────────────────────────────
    def save(self) -> None:
        payload = dict(self.raw)
        payload[SCHEMA_KEY] = {"version": SCHEMA_VERSION, **self.data}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
        self.raw = payload

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def pick(self, dotted: str, allowed, default):
        """白名单取值：缺键或值不在 `allowed` 内一律回 `default`（纯读取，不写盘）。"""
        return pick(self, dotted, allowed, default)

    def set(self, dotted: str, value) -> None:
        node = self.data
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                return
        node[parts[-1]] = value

    def update_options(self, **kwargs) -> None:
        options = self.data.setdefault("options", {})
        options.update(kwargs)

    def defaults_copy(self) -> dict:
        """恢复默认：返回一份全新的默认设置（不共享嵌套）。"""
        return _defaults()

    def set_recent(self, key: str, value: str) -> None:
        recent = self.data.setdefault("recent", {})
        if value:
            recent[key] = value


# ── 引用库核实段（R6）：纯函数，可直接单测；不碰 flet、不跑引擎 ────────────
def _legacy_bibdb(raw) -> dict:
    """Tk 版 `bibdb` 段 → 家壳同名键（只映射四键，不改其它键语义）。"""
    if not isinstance(raw, dict):
        return {}
    mapped: dict = {}
    value, error = parse_verify_max(raw.get("verifyMax"))
    if error is None and value is not None:
        mapped["verify_max"] = value
    for new_key, old_key in (("verify_plan", "verifyPlan"),
                             ("verify_all_refs", "verifyAllRefs"),
                             ("lookup_enabled", "lookupEnabled")):
        if isinstance(raw.get(old_key), bool):
            mapped[new_key] = raw[old_key]
    return mapped


def parse_verify_max(text) -> tuple[int | None, str | None]:
    """「核实上限」输入 → (值, 错误)。空 = None（引擎默认）；0 = 不限且真传。"""
    raw = "" if text is None else str(text).strip()
    if not raw:
        return None, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, "上限必须是整数（0 = 不限；留空 = 引擎默认 120 条）"
    if value < 0:
        return None, "上限不能是负数（要「不限」请填 0）"
    return value, None


def bibdb_section(cfg) -> dict:
    """取 `bibdb` 段并归一（缺省/坏值一律回默认；也接受直接传段本身）。"""
    section = cfg if isinstance(cfg, dict) else {}
    nested = section.get("bibdb")
    if isinstance(nested, dict):
        section = nested
    out = dict(BIBDB_DEFAULTS)
    value = section.get("verify_max")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        value = None
    out["verify_max"] = value
    out["verify_plan"] = bool(section.get("verify_plan"))
    out["verify_all_refs"] = bool(section.get("verify_all_refs"))
    out["lookup_enabled"] = bool(section.get("lookup_enabled", True))
    return out


def verify_max_text(cfg) -> str:
    """输入框预填文本：设过才显示数字，否则空（= 引擎默认）。"""
    value = bibdb_section(cfg)["verify_max"]
    return "" if value is None else str(value)


def verify_max_summary(cfg) -> str:
    value = bibdb_section(cfg)["verify_max"]
    if value is None:
        return f"引擎默认 {DEFAULT_VERIFY_MAX} 条（不传 --verify-max）"
    if value == 0:
        return "不限（--verify-max 0）"
    return f"{value} 条（--verify-max）"


def bibdb_convert_args(cfg) -> dict:
    """引用库核实段 → `convert_call` 关键字。默认全不传 = 零 argv 变化。"""
    section = bibdb_section(cfg)
    args: dict = {}
    if section["verify_max"] is not None:
        args["verify_max"] = section["verify_max"]
    if section["verify_plan"]:
        args["verify_plan"] = True
    if section["verify_all_refs"]:
        args["verify_all_refs"] = True
    if not section["lookup_enabled"]:
        args["no_bibdb"] = True
    return args


def bibdb_effective_rows(cfg, *, db: str = "", db_exists: bool = False) -> list[tuple[str, str]]:
    """「当前生效值」四行（旧壳 ④ 同文）：下一次「初排转换」会用这些。"""
    section = bibdb_section(cfg)
    if section["verify_plan"]:
        plan = "开（只算账：不生成任务书、不检索、不调 AI）"
    else:
        plan = "关（照常核实）"
    all_refs = "开（越过上限）" if section["verify_all_refs"] else "关（受上限约束）"
    if not section["lookup_enabled"]:
        lookup = "关（本次传 --no-bibdb）"
    elif db_exists and db:
        lookup = f"开（库：{db}）"
    elif section["lookup_enabled"]:
        lookup = "开（库文件不存在，会跳过查库）"
    else:  # pragma: no cover - 上面两分支已穷尽
        lookup = "关（本次传 --no-bibdb）"
    return [
        ("核实上限", verify_max_summary(section)),
        ("先算账", plan),
        ("全量核实", all_refs),
        ("先查引用库", lookup),
    ]


def bibdb_convert_hint(cfg) -> str:
    """「初排转换」页的核实范围回显（旧壳 refresh_bibdb_hint 同口径）。"""
    section = bibdb_section(cfg)
    text = "核实范围：" + verify_max_summary(section)
    if section["verify_all_refs"]:
        text += "；已允许全量核实"
    if section["verify_plan"]:
        text += "；先算账（只打印计划）"
    if not section["lookup_enabled"]:
        text += "；本次不查引用库（--no-bibdb）"
    return text + "（在「引用库 → 设置」改；默认不加参数）"
