"""设置值的有界归一（纯函数；不 import flet、不读写盘）。

P01 R03：加载时只对**已知字段**做类型归一与范围夹取；未知字段（含未来版本
字段）原样保留。一次坏值只回落该字段的默认值，不把用户其余设置整段丢掉。

口径：
- `normalize_settings(data, defaults)` 返回新 dict，`data` 是已合并过旧键与
  `flet` 段的完整设置；`defaults` 是 `settings_store._defaults()`。
- 幂等：`normalize_settings(_defaults(), _defaults()) == _defaults()`。
- 本模块不判断设置文件版本（那是 settings_store 的事），也不做 Tk 旧键映射。
"""
from __future__ import annotations

from services import ai_prefs

#: 与 `ui/tokens.ZOOM_MIN/ZOOM_MAX` 同口径（services 不反向依赖 ui；新增缩放
#: 档位时两处同步）。
ZOOM_MIN, ZOOM_MAX = 0.8, 2.0

#: 窗口尺寸的有界区间：坏值回落默认，不让手改配置把窗口撑到不可见。
WINDOW_MIN_W, WINDOW_MIN_H = 640, 480
WINDOW_MAX = 10000

_TRUE_TEXT = ("1", "true", "yes", "on")
_FALSE_TEXT = ("0", "false", "no", "off", "")


def _bool(value, default: bool) -> bool:
    """只把明确的布尔写法归一；其余坏值回落 `default`。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_TEXT:
            return True
        if text in _FALSE_TEXT:
            return False
    return bool(default)


def _text(value, default: str) -> str:
    """只接受字符串；数字/列表/None 等一律回落默认（不 str() 坏值）。"""
    return value if isinstance(value, str) else str(default)


def _zoom(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number or number in (float("inf"), float("-inf")):
        return float(default)
    return max(ZOOM_MIN, min(ZOOM_MAX, number))


def _int_between(value, low: int, high: int, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < low or number > high:
        return default
    return number


def _window(value, defaults: dict) -> dict:
    if not isinstance(value, dict):
        return dict(defaults)
    width = _int_between(value.get("width"), WINDOW_MIN_W, WINDOW_MAX, defaults["width"])
    height = _int_between(value.get("height"), WINDOW_MIN_H, WINDOW_MAX, defaults["height"])
    out = {"width": width, "height": height, "left": None, "top": None}
    for key in ("left", "top"):
        raw = value.get(key)
        out[key] = raw if isinstance(raw, int) and not isinstance(raw, bool) else None
    return out


def _recent(value, defaults: dict) -> dict:
    out = dict(defaults)
    if not isinstance(value, dict):
        return out
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        out[key] = item if isinstance(item, str) else ""
    return out


def _options(value, defaults: dict) -> dict:
    if not isinstance(value, dict):
        return dict(defaults)
    out = dict(defaults)
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if key in defaults:
            out[key] = _bool(item, bool(defaults[key]))
        else:
            # 本版不认识的选项键：原样保留（不因一次归一丢掉用户/未来字段）。
            out[key] = item
    return out


def _paths(value, defaults: dict) -> dict:
    out = dict(defaults)
    if not isinstance(value, dict):
        return out
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        out[key] = item if isinstance(item, str) else ""
    return out


def _bibdb(value, defaults: dict) -> dict:
    out = dict(defaults)
    if not isinstance(value, dict):
        return out
    for key, item in value.items():
        if key == "verify_max":
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                out["verify_max"] = defaults["verify_max"]
            else:
                out["verify_max"] = item
        elif key in defaults:
            out[key] = _bool(item, bool(defaults[key]))
        else:
            out[key] = item
    return out


def _strmap(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items()
            if isinstance(key, str) and isinstance(item, str)}


def normalize_settings(data: dict, defaults: dict) -> dict:
    """按白名单归一已知字段，未知字段原样透传；返回新 dict（不改入参）。"""
    source = data if isinstance(data, dict) else {}
    out = dict(source)
    out["theme"] = "dark" if str(source.get("theme") or "").strip().lower() == "dark" else "light"
    out["zoom"] = _zoom(source.get("zoom"), defaults["zoom"])
    out["last_page"] = _text(source.get("last_page"), defaults["last_page"])
    out["window"] = _window(source.get("window"), defaults["window"])
    out["recent"] = _recent(source.get("recent"), defaults["recent"])
    out["options"] = _options(source.get("options"), defaults["options"])
    out["paths"] = _paths(source.get("paths"), defaults["paths"])
    out["bibdb"] = _bibdb(source.get("bibdb"), defaults["bibdb"])
    for flag in ("log_show_raw", "env_check_done", "tray_enabled"):
        out[flag] = _bool(source.get(flag), bool(defaults[flag]))
    out["bib_view"] = _text(source.get("bib_view"), defaults["bib_view"])
    out["bib_group_memory"] = _strmap(source.get("bib_group_memory"))
    out["engine_override"] = _text(source.get("engine_override"), "")
    out["updated_at"] = _text(source.get("updated_at"), "")
    # AI 段：Key/base/model 的钳制与缺省统一走 ai_prefs（Key 只留在本机文件与内存）。
    out["ai"] = ai_prefs.normalize(source.get("ai"))
    nav = source.get("nav_open")
    if isinstance(nav, dict):
        out["nav_open"] = {
            "page": nav.get("page") if isinstance(nav.get("page"), str) else "",
            "group": nav.get("group") if isinstance(nav.get("group"), str) else "",
        }
    else:
        out["nav_open"] = {"page": "", "group": ""}
    # last_route 由 settings_store 的迁移逻辑兜底：坏类型直接移除，交给迁移重算。
    if "last_route" in out and not isinstance(out["last_route"], str):
        out.pop("last_route")
    return out
