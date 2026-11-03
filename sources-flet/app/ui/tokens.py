"""B-246c P1 · 设计 tokens（颜色 / 间距 / 字号 / 圆角 / 图标 / 缩放）。

批 114 P2 v2 起按 S1m-native 定稿（b114-redesign/style-round2/styles.md）取值：

- 间距仍走 8px 栅格，界面行高另给 ROW_H（26/28/30/32/52/58）；
- 字号：标题 22 / 正文 13 / 元信息 11 / 分区标签 11.5 / 表头 11；控件统一 28 高、6 圆角；
- 颜色是「中性底 + 主色 + 语义色」两套（浅 / 深），全部 ≥ WCAG AA；
- 主按钮有 C（石板蓝）/ D（石墨）两套，由设置页选择，运行时只换 token、不重建窗口；
- 批 115 P0-1（用户批准方案 A）：`Tokens.k` **只等于用户 zoom**，DPI 交给
  Flutter/OS 处理；设计基准 1240×860 逻辑 px 即 1.0×。所有像素都过
  `px()/sp()/font()`，**禁止**在组件里写裸数字——这是缩放不裁切的唯一保证。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, fields

#: 历史 DPI 基准（旧 `k = zoom × dpi / 144`）。批 115 P0-1 起渲染不再使用，
#: 只作为旧 zoom 一次性迁移的换算分母（见 main._migrate_zoom_value）。
BASE_DPI = 144.0
BASE_W, BASE_H = 1240, 860  # 设计基准逻辑窗口尺寸（P0 实测定稿）
ZOOM_MIN, ZOOM_MAX = 0.8, 2.0

# ── 尺寸 token（S1m-native）──────────────────────────────────────────────────
SIDEBAR_W = 220           # 侧栏设计宽
TOOLBAR_H = 52            # 统一工具栏
CONTROL_H = 28            # 输入框 / 下拉 / 按钮统一高度

#: 侧栏「常显滚动条」的设计高度阈值（批 115 P1-12）。
#: 1160×780 / 1000×700 下导航内容高于可视区，AUTO 只在滚动瞬间显示滚动条，
#: 评审看到的是「被裁且无提示」；设计高低于该值时导航列改用 ALWAYS。
SIDEBAR_SCROLL_H = 820

#: 内容面板右缘 gutter（设计像素，批 115 P2-11）：让面板 10px 圆角完整露出。
CONTENT_GUTTER = 10

#: 各场景行高（设计像素）。
ROW_H: dict[str, int] = {
    "sidebar": 28,
    "table": 32,
    "list": 30,
    "entry": 52,
    "band": 58,
    "tab": 34,
    "toolbar": 52,
    "status": 26,
}

#: 断点（设计像素）：窗口设计宽小于该值即进入紧凑分支。
BREAK: dict[str, int] = {
    "rail": 720,       # 首页：侧栏收成 56 图标条
    "columns": 900,    # 表格：收掉次要列
    "detail": 860,     # 查库：详情由右侧改为下方
}

# ── 8px 栅格 ────────────────────────────────────────────────────────────────
SPACE: dict[str, int] = {
    "half": 4, "xs": 2, "s": 6, "1": 8, "10": 10, "12": 12, "14": 14,
    "2": 16, "20": 20, "3": 24, "4": 32, "6": 48,
}
RADIUS: dict[str, int] = {
    "mark": 4, "control": 6, "sm": 8, "popover": 8, "panel": 10,
    "md": 12, "lg": 16, "pill": 999,
}
FONT: dict[str, float] = {
    "display": 28,
    "title": 22, "h1": 22,
    "stat": 20,
    "h2": 18,
    "section": 15, "h3": 15, "item": 15,
    "body": 13,
    "nav": 13,
    "table": 12.5,
    "small": 12,
    "section-label": 11.5, "log": 11.5, "mono": 11.5,
    "caption": 11, "meta": 11,
}
FONT_WEIGHT: dict[str, str] = {
    "regular": "W_400", "medium": "W_500", "semibold": "W_600", "bold": "W_700",
}

# 字体族：不打包字体，正文用系统默认（macOS = SF Pro + PingFang SC，Windows =
# Segoe UI + 微软雅黑，均由系统按字符自动回退）。
#
# 注意（批 112 GUI-07）：这里曾写成 "Segoe UI, PingFang SC, …" 逗号串。Flet 1.0.5 的
# `ft.Theme.font_family` 是**单个 Optional[str]**（不是 CSS font-family），整串会被当成
# 一个不存在的族名 → 实际落平台默认。因此正文族改为 None（= 不指定，交给系统），
# 等宽族用平台单族 + `font_family_fallback` 显式回退。
FONT_FAMILY: str | None = None
if sys.platform == "darwin":
    FONT_FAMILY_MONO = "Menlo"
    MONO_FALLBACK: tuple[str, ...] = ("SF Mono", "Monaco")
elif sys.platform == "win32":
    FONT_FAMILY_MONO = "Consolas"
    MONO_FALLBACK = ("Cascadia Mono", "Courier New")
else:
    FONT_FAMILY_MONO = "DejaVu Sans Mono"
    MONO_FALLBACK = ("Liberation Mono", "Courier New")

#: 语义排版角色 → (字号键, 字重键)。组件只引用角色，不再各自拼字号+字重：
#: 这是「同级标题字重一致 / 输入框字号=正文」的唯一出处（B-109 W4 收编）。
TEXT_ROLE: dict[str, tuple[str, str]] = {
    "page-title": ("h1", "semibold"),
    "page-subtitle": ("body", "regular"),
    "section": ("section", "semibold"),
    "section-label": ("section-label", "semibold"),
    "item-title": ("item", "semibold"),
    "step-title": ("body", "semibold"),
    "dialog-title": ("h2", "semibold"),
    "body": ("body", "regular"),
    "body-strong": ("body", "semibold"),
    "meta": ("meta", "regular"),
    "caption": ("caption", "regular"),
    "small": ("small", "regular"),
    "badge": ("caption", "medium"),
    "nav-label": ("nav", "regular"),
    "nav-selected": ("nav", "medium"),
    "table-head": ("meta", "semibold"),
    "stat": ("stat", "semibold"),
    # 选中态（用户 GUI-01 / 批 112 S1）：**选中 = W500，未选 = W400**；
    # 字号随场景（chip/tab = small，nav/步骤卡/配对卡 = body），字重只有一个来源。
    "item-selected": ("body", "medium"),
    "chip-selected": ("small", "medium"),
    "tab-selected": ("small", "medium"),
    "step-selected": ("body", "medium"),
    "control": ("body", "regular"),
    "log": ("log", "regular"),
    "mono": ("mono", "regular"),
}
#: 需要等宽字体的角色（日志行、路径、ID）。
MONO_ROLES = frozenset({"log", "mono"})


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
    # S1m 增量：侧栏选中底 / 链接 / 选中 tint / 下划线 / 危险 tint。
    nav_sel: str = "#E5E5EA"
    link: str = "#35597F"
    sel_tint: str = "#E8EEF4"
    sel_text: str = "#33506F"
    tab_underline: str = "#3D5A80"
    danger: str = "#B3121C"
    danger_soft: str = "#FBE7E9"


LIGHT = Palette(
    bg="#F5F5F7",
    sidebar="#F2F2F7",
    surface="#FFFFFF",
    surface_2="#F2F2F7",
    surface_3="#E8E8ED",
    border="#DFDFE4",
    border_strong="#C7C7CC",
    text="#1D1D1F",
    text_muted="#6E6E73",
    # 批 115 P1-11：11px 说明文字在 #FFFFFF 上需 ≥4.5（#8E8E93 仅 3.26）；
    # #6E6E73 = 5.07 on white / 4.54 on #F2F2F7。与 text_muted 同值，重染同色。
    text_faint="#6E6E73",
    accent="#3D5A80",
    accent_hover="#344D6E",
    on_accent="#FFFFFF",
    accent_soft="#E8EEF4",
    on_accent_soft="#33506F",
    success="#2C6A45",
    success_soft="#E8F0EA",
    warn="#7A5300",
    warn_soft="#F6EEDC",
    error="#A3312B",
    error_soft="#F7E7E4",
    log_bg="#FFFFFF",
    log_text="#1D1D1F",
    log_muted="#6E6E73",
    nav_sel="#E5E5EA",
    link="#35597F",
    sel_tint="#E8EEF4",
    sel_text="#33506F",
    tab_underline="#3D5A80",
    danger="#B3121C",
    danger_soft="#FBE7E9",
)

DARK = Palette(
    bg="#1E1E1E",
    sidebar="#252527",
    surface="#2C2C2E",
    surface_2="#3A3A3C",
    surface_3="#48484A",
    border="#3A3A3C",
    border_strong="#545458",
    text="#F5F5F7",
    text_muted="#A8A8AD",
    # 批 115 P1-11：暗色实测 #8E8E93 在 #2C2C2E 上仅 4.27；#A8A8AD 在
    # surface/surface_2/sidebar/bg 上均 ≥4.5（#3A3A3C 上 4.53）。
    text_faint="#A8A8AD",
    accent="#98C1D9",
    accent_hover="#ADD2E7",
    on_accent="#16232D",
    accent_soft="#26343F",
    on_accent_soft="#C8DCEA",
    success="#6FBF8B",
    success_soft="#1E2A22",
    warn="#D9A441",
    warn_soft="#332A18",
    error="#E5766E",
    error_soft="#33201F",
    log_bg="#1C1C1E",
    log_text="#F5F5F7",
    log_muted="#A8A8AD",
    nav_sel="#3A3A3C",
    link="#9CC3DC",
    sel_tint="#26343F",
    sel_text="#C8DCEA",
    tab_underline="#98C1D9",
    danger="#FF6B62",
    danger_soft="#3A1D1B",
)


#: 浅色板里 #FFFFFF 同时是 sidebar / surface / on_accent / log_bg，#F2F2F7 同时是
#: sidebar 与 surface_2 —— 纯值映射分不清角色，所以按「背景」「前景」两类给重复源色
#: 排一个先到先得的优先级；必须区分的节点另带 `data="tone:<palette 字段>"` 语义标签，
#: 换肤时由 main._retint_node 精确改写（见 `_TONE_TAG`）。
_ROLE_PRIORITY: dict[str, tuple[str, ...]] = {
    "bg": ("surface", "sidebar", "on_accent", "log_bg"),
    "fg": ("on_accent", "sidebar", "surface", "log_bg"),
}

#: 语义标签前缀：`data="tone:surface_2"` → 换主题时 bgcolor 直接取目标色板同名字段。
TONE_TAG = "tone:"


def palette_remap(target: str, role: str = "bg") -> dict[str, str]:
    """当前主题色值 → target 主题色值（原地换肤用；不重建控件）。

    `role="bg"` 管背景类属性（bgcolor / border_color / fill_color …），
    `role="fg"` 管前景 `color`：两者只对 #FFFFFF 这类一词多义的源色有区别。
    反向（dark→light）时 4 个白目标合一，两个角色给出同一张表。
    """
    if target not in ("light", "dark"):
        raise ValueError(f"unknown theme: {target!r}")
    if role not in _ROLE_PRIORITY:
        raise ValueError(f"unknown role: {role!r}")
    src, dst = (LIGHT, DARK) if target == "dark" else (DARK, LIGHT)
    names = [field.name for field in fields(Palette)]
    ordered = list(_ROLE_PRIORITY[role]) + [name for name in names
                                            if name not in _ROLE_PRIORITY[role]]
    mapping: dict[str, str] = {}
    for name in ordered:
        mapping.setdefault(getattr(src, name), getattr(dst, name))
    return mapping


@dataclass(frozen=True)
class ButtonColors:
    """一套主按钮配色（C 石板蓝 / D 石墨）。设置页只选名字，不碰色号。"""
    fill: str
    hover: str
    press: str
    on: str
    off_bg: str
    off_fg: str
    link: str
    sel_tint: str
    sel_text: str
    tab_underline: str
    danger: str
    danger_soft: str


#: C = 石板蓝（默认）、D = 石墨（去蓝模式）；链接色两套都取 C 组，保证可读。
BUTTON_PALETTES: dict[str, dict[str, ButtonColors]] = {
    "c": {
        "light": ButtonColors(
            fill="#3D5A80", hover="#344D6E", press="#2A4059", on="#FFFFFF",
            off_bg="#D9E0E8", off_fg="#98A5B4",
            link="#35597F", sel_tint="#E8EEF4", sel_text="#33506F",
            tab_underline="#3D5A80", danger="#B3121C", danger_soft="#FBE7E9",
        ),
        "dark": ButtonColors(
            fill="#98C1D9", hover="#ADD2E7", press="#7FAAC6", on="#16232D",
            off_bg="#37404A", off_fg="#78838F",
            link="#9CC3DC", sel_tint="#26343F", sel_text="#C8DCEA",
            tab_underline="#98C1D9", danger="#FF6B62", danger_soft="#3A1D1B",
        ),
    },
    "d": {
        "light": ButtonColors(
            fill="#1C1C1E", hover="#3A3A3C", press="#0F0F11", on="#FFFFFF",
            off_bg="#E5E5EA", off_fg="#8E8E93",
            link="#35597F", sel_tint="#E8EEF4", sel_text="#33506F",
            tab_underline="#1C1C1E", danger="#B3121C", danger_soft="#FBE7E9",
        ),
        "dark": ButtonColors(
            fill="#E6E6EA", hover="#FFFFFF", press="#D1D1D6", on="#1C1C1E",
            off_bg="#3A3A3C", off_fg="#8E8E93",
            link="#9CC3DC", sel_tint="#26343F", sel_text="#C8DCEA",
            tab_underline="#E6E6EA", danger="#FF6B62", danger_soft="#3A1D1B",
        ),
    },
}


class Tokens:
    """一个主题 + 一个用户缩放乘数 = 一次构建皮肤；缩放变化必须整体重建（见 main.py）。

    批 115 P0-1（方案 A）：DPI 由 Flutter/OS 负责（Flet 的窗口坐标本来就是逻辑 px），
    因此 `k = zoom`；`dpi` 参数/属性只保留作诊断与截图元数据，**不再参与任何像素计算**。
    旧壳的 `dpi_k = dpi / 144` 会把 mac 误报的 96 变成 0.667（设计按 67% 渲染），已删除。
    """

    def __init__(self, theme: str = "light", zoom: float = 1.0,
                 dpi: int = int(BASE_DPI), button: str = "c") -> None:
        self.theme = "dark" if theme == "dark" else "light"
        self.palette = DARK if self.theme == "dark" else LIGHT
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, float(zoom)))
        #: 诊断值（PNG 元数据 / 设置页「窗口 / DPI」行）；不参与 k。
        self.dpi = int(dpi)
        #: 唯一乘数：用户缩放。设计基准 1240×860 逻辑 px = 1.0×。
        self.k = self.zoom
        self.button = button if button in BUTTON_PALETTES else "c"

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

    def row_h(self, key: str) -> float:
        return self.px(ROW_H[key])

    def control_h(self) -> float:
        return self.px(CONTROL_H)

    def break_at(self, width: float, key: str) -> bool:
        """设计宽（物理宽 / k）小于断点 → True。调用方负责把物理宽 / k。"""
        return width < BREAK[key]

    # -- 排版角色 --------------------------------------------------------
    @property
    def font_family(self) -> str | None:
        """正文族：None = 平台默认（Flet 的 font_family 只收单个族名，不能是逗号串）。"""
        return FONT_FAMILY

    @property
    def mono_family(self) -> str:
        return FONT_FAMILY_MONO

    @property
    def mono_fallback(self) -> tuple[str, ...]:
        """等宽族的显式回退（组件的 ft.Text 出口会带上它）。"""
        return MONO_FALLBACK

    def text_role(self, role: str) -> tuple[str, str]:
        """角色 → (字号键, 字重键)；未知角色直接报错，避免又出现裸字号散点。"""
        return TEXT_ROLE[role]

    # -- 按钮 ------------------------------------------------------------
    @property
    def buttons(self) -> ButtonColors:
        """当前按钮配色（C/D × 浅/深）。"""
        return BUTTON_PALETTES[self.button][self.theme]

    @property
    def button_label(self) -> str:
        return "C · 石板蓝" if self.button == "c" else "D · 石墨"

    # -- 布局 ------------------------------------------------------------
    @property
    def window_size(self) -> tuple[int, int]:
        return round(BASE_W * self.k), round(BASE_H * self.k)

    @property
    def min_window_size(self) -> tuple[int, int]:
        return round(1000 * self.k), round(700 * self.k)

    @property
    def sidebar_width(self) -> int:
        return round(SIDEBAR_W * self.k)

    @property
    def rail_width(self) -> int:
        return round(56 * self.k)

    @property
    def screenshot_dpi(self) -> int:
        """给 PNG 元数据用：按仿真 DPI 标注，评审看图时不混淆。"""
        return self.dpi

    @property
    def is_dark(self) -> bool:
        return self.theme == "dark"

    def with_theme(self, theme: str) -> "Tokens":
        return Tokens(theme, self.zoom, self.dpi, self.button)

    def with_button(self, button: str) -> "Tokens":
        return Tokens(self.theme, self.zoom, self.dpi, button)

    def __repr__(self) -> str:  # 便于 marker 里留证
        return (f"Tokens(theme={self.theme}, zoom={self.zoom:.2f}, dpi={self.dpi}, "
                f"k={self.k:.3f}, button={self.button})")
