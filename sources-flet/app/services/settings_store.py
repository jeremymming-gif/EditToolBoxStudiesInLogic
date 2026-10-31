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

APP_NAME = "XuehengToolbox"
SCHEMA_KEY = "flet"
SCHEMA_VERSION = 2


def settings_path() -> Path:
    override = os.environ.get("P2_SETTINGS", "").strip()
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "EditToolBox" / "settings.json"
    return Path.home() / ".xueheng" / "settings.json"


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
            "checkbib_merge": False,
            "proofcheck_fix": False,
            "toc_text_only": True,
            "compileall_dry_run": False,
            "cleantemp_trash": True,
            "dirpush_with_en_toc": False,
            "dirpush_dry_run": False,
            "log_summary_only": True,
        },
        "paths": {"engine_dir": "", "notes_repo": ""},
        "log_show_raw": False,
        "env_check_done": False,
        "engine_override": "",
        "updated_at": "",
    }


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
        return self.data, self.exists

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

    def set_recent(self, key: str, value: str) -> None:
        recent = self.data.setdefault("recent", {})
        if value:
            recent[key] = value
