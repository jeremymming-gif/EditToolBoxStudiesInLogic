"""品牌资产（B-109 W4）：图标只有一处出处 —— `packaging/assets/` 的 PNG/ICO。

运行时只做「找文件 → 软设置」；文件缺失（源码树挪动、打包未带、无窗口平台）一律降级：
侧栏回退矢量图标、窗口图标跳过、托盘退回内置图形 —— 任何失败都不得影响启动。
"""
from __future__ import annotations

import sys
from pathlib import Path

import flet as ft

from ui import components as C
from ui.tokens import Tokens

ASSETS_NAME = "assets"
SIDEBAR_PNG = "logo-256.png"
WINDOW_ICON = "logo.ico"
#: 侧栏 22×22 的 ℵ 品牌位（S1m-native）：浅色 slate 蓝、深色 slate-light。
ALEPH_LIGHT = "aleph-slate-64.png"
ALEPH_DARK = "aleph-slate-light-64.png"
ALEPH_DARK_FALLBACK = "aleph-white-64.png"


def _roots() -> list[Path]:
    here = Path(__file__).resolve()
    roots = [here.parents[2] / "packaging"]        # app/ui/brand.py → sources-flet/packaging
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:                                      # flet pack / PyInstaller 单文件
        roots += [Path(bundle) / "packaging", Path(bundle)]
    return roots


def asset(name: str) -> str:
    """返回存在的资产绝对路径；找不到返回空串，调用方据此降级。"""
    for root in _roots():
        path = root / ASSETS_NAME / name
        if path.is_file():
            return str(path)
    return ""


def sidebar_mark(t: Tokens, size: float = 36) -> ft.Control:
    png = asset(SIDEBAR_PNG)
    body = (ft.Image(src=png, width=t.px(size), height=t.px(size),
                     fit=ft.BoxFit.CONTAIN, border_radius=t.radius("md"))
            if png else ft.Icon(C.ic("AUTO_AWESOME", "STAR"), size=t.px(20),
                                color=t.palette.accent))
    return ft.Container(width=t.px(size), height=t.px(size), border_radius=t.radius("md"),
                        alignment=ft.Alignment.CENTER, content=body)


def aleph_mark(t: Tokens, size: float = 22) -> ft.Control:
    """侧栏品牌位：22×22、圆角 4；品牌色 = slate（浅 slate-64 / 深 slate-light-64，深色缺则 white）。

    文件缺失时回退矢量 AUTO_AWESOME（不画假字、不影响启动）；窗口图标仍走
    `apply_window_icon`（logo-256 / logo.ico），本函数不碰窗口图标。
    """
    png = asset(ALEPH_DARK if t.is_dark else ALEPH_LIGHT)
    if not png and t.is_dark:               # 浅色缺文件直接矢量，别把深色白 ℵ 放浅底上
        png = asset(ALEPH_DARK_FALLBACK)
    radius = t.radius("mark")
    body = (ft.Image(src=png, width=t.px(size), height=t.px(size),
                     fit=ft.BoxFit.CONTAIN, border_radius=radius)
            if png else ft.Icon(C.ic("AUTO_AWESOME", "STAR"), size=t.px(size - 4),
                                color=t.palette.accent))
    return ft.Container(width=t.px(size), height=t.px(size), border_radius=radius,
                        alignment=ft.Alignment.CENTER, content=body)


def apply_window_icon(page) -> str:
    """软设置窗口图标：属性不支持 / 文件缺失都静默跳过，返回实际设置的文件（空=未设）。"""
    icon = asset(WINDOW_ICON) or asset(SIDEBAR_PNG)
    if not icon:
        return ""
    try:
        page.window.icon = icon
    except Exception:  # noqa: BLE001 - 平台/版本差异，图标不是功能
        return ""
    return icon


def tray_image():
    """托盘图标：优先品牌 PNG，缺失或读图失败返回 None（调用方回退内置图形）。"""
    png = asset(SIDEBAR_PNG)
    if not png:
        return None
    try:
        from PIL import Image

        with Image.open(png) as image:
            return image.convert("RGBA").resize((64, 64), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        return None
