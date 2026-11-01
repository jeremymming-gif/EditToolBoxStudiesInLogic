"""应用版本：运行时以引擎版本为单一来源，进程内缓存，读不到就显示「未知」。

口径（批 110，B2）：
- 正常路径：`Engine/slcn/__init__.py` 的 `__version__`（冻结包则 `engine --version`），
  由 `engine_bridge.engine_version()` 读取；本模块只做归一（补 `v`）与缓存。
- 引擎版本读不到（`未知` / 不是数字版本）时**不猜版本号**：`version` 就是「未知」，
  同时给出失败标记 `source="fallback"`、`available=False`、`version_fallback=True`
  供 UI/日志区分（启动时另记一次 `version_fallback`）。不请求网络，不改任何文件。
"""
from __future__ import annotations

import re

from services import engine_bridge as EB

#: 读不到引擎版本时的显示值：明确「未知」，**不**放任何具体版本号。
UNKNOWN_VERSION = "未知"

#: 兼容旧名（`main.py` 启动前的占位值）；语义与 UNKNOWN_VERSION 相同，不猜版本。
FALLBACK_VERSION = UNKNOWN_VERSION

_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.\-+]+)?$")

_CACHE: dict | None = None


def _from_engine(raw: str) -> dict:
    """引擎版本归一；读不到就返回「未知」并带上失败标记。"""
    text = (raw or "").strip()
    if text.startswith("v"):
        text = text[1:]
    if text and _VERSION_RE.fullmatch(text):
        return {"version": "v" + text, "source": "engine",
                "available": True, "version_fallback": False}
    return {"version": UNKNOWN_VERSION, "source": "fallback",
            "available": False, "version_fallback": True}


def resolve(status=None) -> dict:
    """返回 {version, source, available, version_fallback, engine_version}。

    同一进程只读一次引擎；读取失败时 version 为「未知」，调用方不要再猜版本号。
    """
    global _CACHE
    if _CACHE is None:
        status = status or EB.engine_status()
        engine_version = str(getattr(status, "version", "") or "").strip() or UNKNOWN_VERSION
        _CACHE = {"engine_version": engine_version, **_from_engine(engine_version)}
    return dict(_CACHE)


def app_version(status=None) -> str:
    return resolve(status)["version"]


def available(status=None) -> bool:
    """版本是否可显示（False = 读不到引擎版本，UI 只能显示「未知」）。"""
    return resolve(status)["available"]


def cache_clear() -> None:
    """测试用：清掉进程内缓存。"""
    global _CACHE
    _CACHE = None
