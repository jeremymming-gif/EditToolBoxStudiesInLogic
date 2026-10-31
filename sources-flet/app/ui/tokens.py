"""B-246c P1 · 设计 tokens（颜色 / 间距 / 字号 / 圆角 / 图标 / 缩放）。

设计约束（见 p1/p1-design.md D1）：
- 间距走 **8px 栅格**（4 为半格，仅用于图标内衬与紧凑行）；
- 字号层级 24 / 20 / 16 / 14 / 12（标题区另有 28 展示字号，用于工作台主标题）；
- 圆角 8 / 12 / 16（胶囊用 999）；
- 颜色是「中性底 + INDIGO 主色 + 成功/警告/错误」两套（浅 / 深），全部 ≥ WCAG AA；
- 用户缩放 0.8–2.0 与 DPI 仿真共用 `Tokens.k` 一个乘数，所有像素都过 `px()/sp()/font()`，
  **禁止**在组件里写裸数字——这是 4 档 DPI 不裁切的唯一保证。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

BASE_DPI = 144.0          # 本机系统 DPI（150%）
BASE_W, BASE_H = 1240, 860  # 设计基准逻辑窗口尺寸（P0 实测定稿）
ZOOM_MIN, ZOOM_MAX = 0.8, 2.0

# ── 8px 栅格 ────────────────────────────────────────────────────────────────
SPACE: dict[str, int] = {
    "half": 4, "1": 8, "2": 16, "3": 24, "4": 32, "6": 48,
}
RADIUS: dict[str, int] = {"sm": 8, "md": 12, "lg": 16, "pill": 999}
FONT: dict[str, int] = {
    "display": 28, "h1": 24, "h2": 20, "h3": 16, "body": 14, "small": 13, "caption": 12,
}
FONT_WEIGHT: dict[str, str] = {
    "regular": "W_400", "medium": "W_500", "semibold": "W_600", "bold": "W_700",
}


@dataclass(frozen=True)
class Palette:
    """语义色板；组件只引用语义名，不写死色号（换主题 = 换一张表）。"""
    bg: str
    sidebar: str
    surface: str
    surface_2: str
    surface_3: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    accent_hover: str
    on_accent: str
    accent_soft: str
    on_accent_soft: str
    success: str
    success_soft: str
    warn: str
    warn_soft: str
    error: str
    error_soft: str
    log_bg: str
    log_text: str
    log_muted: str


LIGHT = Palette(
    bg="#F6F8FA",
    sidebar="#FFFFFF",
    surface="#FFFFFF",
    surface_2="#F0F3F7",
    surface_3="#E7EDF3",
    border="#D0D7DE",
    border_strong="#AFB8C1",
    text="#1F2328",
    text_muted="#57606A",
    text_faint="#8C959F",
    accent="#4F46E5",
    accent_hover="#4338CA",
    on_accent="#FFFFFF",
    accent_soft="#E0E7FF",
    on_accent_soft="#312E81",
    success="#1A7F37",
    success_soft="#DAFBE1",
    warn="#9A6700",
    warn_soft="#FFF8C5",
    error="#CF222E",
    error_soft="#FFEBE9",
    log_bg="#FFFFFF",
    log_text="#24292F",
    log_muted="#8C959F",
)

DARK = Palette(
    bg="#0D1117",
    sidebar="#0F141B",
    surface="#161B22",
    surface_2="#1C232C",
    surface_3="#232B36",
    border="#30363D",
    border_strong="#3D444D",
    text="#E6EDF3",
    text_muted="#9BA7B4",
    text_faint="#6E7681",
    accent="#818CF8",
    accent_hover="#A5B4FC",
    on_accent="#0B1220",
    accent_soft="#2A2F63",
    on_accent_soft="#C7D2FE",
    success="#3FB950",
    success_soft="#12261A",
    warn="#D29922",
    warn_soft="#2B2110",
    error="#F85149",
    error_soft="#2D1416",
    log_bg="#0B0F14",
    log_text="#D8E1EA",
    log_muted="#6E7681",
)


class Tokens:
    """一个主题 + 一个缩放乘数 = 一次构建皮肤；缩放变化必须整体重建（见 main.py）。"""

    def __init__(self, theme: str = "light", zoom: float = 1.0, dpi: int = int(BASE_DPI)) -> None:
        self.theme = "dark" if theme == "dark" else "light"
        self.palette = DARK if self.theme == "dark" else LIGHT
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, float(zoom)))
        self.dpi = int(dpi)
        self.dpi_k = self.dpi / BASE_DPI
        #: 唯一乘数：DPI 仿真 × 用户缩放。所有像素 = 设计值 × k。
        self.k = self.dpi_k * self.zoom

    # -- 像素 ------------------------------------------------------------
    def px(self, value: float) -> float:
        return round(value * self.k, 2)

    def sp(self, key: str | int) -> float:
        """间距：允许 "1"/"2"/"3" 这类栅格键，也允许直接给整数键（兼容早期页面代码）。"""
        if isinstance(key, int):
            key = str(key)
        scale = SPACE.get(key)
        if scale is None:
            raise KeyError(f"unknown space key: {key!r}")
        return self.px(scale)

    def radius(self, key: str) -> float:
        return self.px(RADIUS[key])

    def font(self, key: str) -> float:
        return self.px(FONT[key])

    def weight(self, key: str) -> str:
        return FONT_WEIGHT[key]

    # -- 布局 ------------------------------------------------------------
    @property
    def window_size(self) -> tuple[int, int]:
        return round(BASE_W * self.k), round(BASE_H * self.k)

    @property
    def min_window_size(self) -> tuple[int, int]:
        return round(1000 * self.k), round(700 * self.k)

    @property
    def sidebar_width(self) -> int:
        return round(232 * self.k)

    @property
    def screenshot_dpi(self) -> int:
        """给 PNG 元数据用：按仿真 DPI 标注，评审看图时不混淆。"""
        return self.dpi

    @property
    def is_dark(self) -> bool:
        return self.theme == "dark"

    def with_theme(self, theme: str) -> "Tokens":
        return Tokens(theme, self.zoom, self.dpi)

    def __repr__(self) -> str:  # 便于 marker 里留证
        return (f"Tokens(theme={self.theme}, zoom={self.zoom:.2f}, dpi={self.dpi}, "
                f"k={self.k:.3f})")


def dpi_preset(target: int) -> int:
    """P1_DPI 只接受四档，避免截图目录出现奇怪档位。"""
    target = int(target)
    return target if target in (96, 120, 144, 192) else int(BASE_DPI)
