r"""Windows 外壳的视觉主题：创建窗口时调一次 `apply(root)`。

**2026-10-17 用户拍板：ttk 主题 `vista` → `clam`。**
为什么必须换：`vista` 主题的控件由**系统绘制**，`ttk.Style.configure()` 改不动颜色 ——
不换就做不出与 mac 侧同一套色板（"两个平台像一家人"是项目既定目标）。
`clam` 是 Tk 自带、完全由 ttk 自己画的，颜色/字体/间距都能统一。

**这一层只做「主题 + 色板 + 字号 + 间距」**。2026-10-17 第十五批（U5）起，外壳里的
按钮 / 输入框 / 勾选框已经**全部换成 ttk**（clam 管不到 Tk 经典控件的外观），
只剩两处**必须**用经典控件：
  · `tk.Text`（日志框）：Tk 没有 ttk 对等物，颜色由 `COLOR[...]` 逐个喂进去；
  · `tk.Listbox`（文章勾选列表）：同上，用 `style_listbox()` 把色板套上去。
这两处**不写死颜色**，色板只有这一份。

⚠️ 不做深色模式：Tk 8.6 不跟随系统主题，**半套深色比没有更糟**（用户 2026-10-17 已知）。
⚠️ 不做 Mica / 圆角：Tk 画不出来，硬仿只会更丑 —— 清晰 > 花哨。
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

#: 语义色（与 mac 侧的状态词一一对应；都过 WCAG AA 的正文 ≥4.5:1 对比度）
COLOR = {
    "ok": "#1a7f37",        # ✅ 完成
    "warn": "#9a6700",      # ⚠ 需核对 / 有失败但产物有效
    "error": "#cf222e",     # ❌ 失败
    "muted": "#57606a",     # 辅助文字（旧的 #555/#666 收进这里，一处改全改）
    "bg": "#f6f8fa",        # 窗口/卡片底色（Fluent base layer 近似）
    "fg": "#1f2328",
    "tab_on": "#ffffff",    # 选中的标签页
    # ── 2026-10-17 第十五批（U5 主题铺满）：把还散落在主文件里的几种颜色收进来 ──
    "text_bg": "#ffffff",   # 日志框 / 列表 / 输入框的「内容层」白底
    "line": "#d0d7de",      # 分隔线 / 控件描边
    "hover": "#eaeef2",     # 悬停底色（工具条上的「摘要 / 原始」分段按钮）
    "accent": "#0969da",    # 主按钮 / 选中底色（对应 mac 的 `.borderedProminent`）
    "accent_hover": "#0550ae",
    "on_accent": "#ffffff",  # 主色/选中色上的文字
}

#: 间距三档（Fluent 的 4px 基准；新代码用它，老代码里的散装数字逐步替换）
SPACE = {"xs": 4, "sm": 8, "md": 16, "lg": 24}


def apply(root: tk.Misc) -> None:
    """把 clam 主题 + 色板 + 字号应用到 `root`（幂等、缺字体不抛）。"""
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    # 字号：Windows 上 TkDefaultFont 默认 9pt（≈12px），比 WinUI 正文（14px）小一档；
    # 中文在高分屏上尤其显小。macOS 侧本来就合适，只做小幅调整。
    base = 10 if sys.platform.startswith("win") else 13
    # 文字缩放（UI 方案 §5）：Windows 上跟随系统缩放比例，别写死绝对字号。
    # `tk scaling` = 每个 point 的像素数（96 dpi ≈ 1.333）。声明了 DPI 感知之后它是准的。
    try:
        scale = float(root.tk.call("tk", "scaling")) or 1.333
    except (tk.TclError, ValueError):
        scale = 1.333
    if sys.platform.startswith("win"):
        base = max(9, min(20, round(base * scale / 1.333)))
    for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
        try:
            tkfont.nametofont(name).configure(size=base)
        except tk.TclError:        # pragma: no cover  极端环境没有这个命名字体
            pass
    try:
        tkfont.nametofont("TkDefaultFont").configure(family="Microsoft YaHei UI")
    except tk.TclError:
        pass                        # 非 Windows / 无该字体：保留系统默认字体

    style.configure(".", background=COLOR["bg"], foreground=COLOR["fg"])
    style.configure("TFrame", background=COLOR["bg"])
    style.configure("TLabel", background=COLOR["bg"], foreground=COLOR["fg"])
    style.configure("Muted.TLabel", background=COLOR["bg"], foreground=COLOR["muted"])
    # 第 132 条：引用库 5 页的页标题/小节标题。字号从 base 派生（跟随系统缩放），
    # 不写死 px —— 与上面「文字缩放」的取舍一致。
    style.configure("Heading.TLabel", background=COLOR["bg"], foreground=COLOR["fg"],
                    font=(None, base + 3, "bold"))
    style.configure("Section.TLabel", background=COLOR["bg"], foreground=COLOR["fg"],
                    font=(None, base + 1, "bold"))
    style.configure("TNotebook", background=COLOR["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", padding=(12, 6))
    style.map("TNotebook.Tab",
              background=[("selected", COLOR["tab_on"]), ("!selected", COLOR["bg"])])

    # 按钮：clam 默认内边距对中文太挤（UI 方案 §5：可点目标要够大）
    style.configure("TButton", padding=(10, 4))
    # 主按钮（每页的「运行」）：一个页面只有一个主动作，与 mac 的 `.borderedProminent` 对应
    style.configure("Accent.TButton", padding=(10, 4), background=COLOR["accent"],
                    foreground=COLOR["on_accent"], bordercolor=COLOR["accent"],
                    lightcolor=COLOR["accent"], darkcolor=COLOR["accent"])
    style.map("Accent.TButton",
              background=[("disabled", COLOR["muted"]), ("pressed", COLOR["accent_hover"]),
                          ("active", COLOR["accent_hover"])],
              foreground=[("disabled", COLOR["on_accent"]), ("active", COLOR["on_accent"])])

    # 勾选框：clam 默认底色与 TFrame 不同一档，摆在浅灰底上会留一块白斑
    style.configure("TCheckbutton", background=COLOR["bg"], foreground=COLOR["fg"])
    style.map("TCheckbutton", background=[("active", COLOR["bg"])])

    # 输入框：内容层白底 + 细描边，聚焦时描边变主色（与 Windows 的焦点提示同一套语言）
    style.configure("TEntry", fieldbackground=COLOR["text_bg"], foreground=COLOR["fg"],
                    padding=(4, 2), bordercolor=COLOR["line"],
                    lightcolor=COLOR["line"], darkcolor=COLOR["line"])
    style.map("TEntry",
              fieldbackground=[("disabled", COLOR["bg"]), ("readonly", COLOR["bg"])],
              bordercolor=[("focus", COLOR["accent"])])

    # 日志工具条上的「摘要 / 原始」二选一：`Toolbutton` 布局 = 无圆点的分段按钮。
    # ⚠️ 两个 Radiobutton 的 value 用 **0/1**：BooleanVar 的 Tcl 值是 "0"/"1"，
    #    value 写成 False/True（→ "False"/"True"）初始态就一个都选不中。
    # ⚠️ 选中态用主色填（白底 vs 浅灰底几乎看不出选中，2026-10-17 截图自查抓到的）；
    #    悬停只作用于**未选中**那一半，否则鼠标一碰选中段就变成灰底白字。
    style.configure("Toolbutton", padding=(10, 3), background=COLOR["bg"],
                    foreground=COLOR["fg"], bordercolor=COLOR["line"],
                    lightcolor=COLOR["bg"], darkcolor=COLOR["bg"])
    style.map("Toolbutton",
              background=[("selected", COLOR["accent"]),
                          ("!selected", "active", COLOR["hover"])],
              foreground=[("selected", COLOR["on_accent"])],
              bordercolor=[("selected", COLOR["accent"])])

    style.configure("Treeview", rowheight=base + 11, fieldbackground=COLOR["text_bg"],
                    background=COLOR["text_bg"], bordercolor=COLOR["line"])
    style.map("Treeview", background=[("selected", COLOR["accent"])],
              foreground=[("selected", COLOR["on_accent"])])
    style.configure("Treeview.Heading", font=(None, base, "bold"))
    style.configure("TCombobox", padding=(4, 2))
    style.configure("TSeparator", background=COLOR["line"])


def mono_font() -> tkfont.Font:
    """日志用的等宽字体（Tk 命名字体 `TkFixedFont`：不写死 family / 字号）。

    必须是**函数**：命名字体要等 `Tk()` 建好之后才取得到（模块导入时还没有 root）。
    """
    return tkfont.nametofont("TkFixedFont")


def style_listbox(widget: tk.Listbox) -> None:
    """`tk.Listbox` 没有 ttk 对等物：把色板直接套到经典控件上（clam 管不到它）。

    调用点在 `main.TocTab` 建列表之后 —— 颜色只从 `COLOR` 来，主文件里不写死。
    """
    # B-014 U6-1：`highlightthickness=0` 会把**系统键盘焦点环**整个关掉 —— 用 Tab
    # 走进列表时界面上没有任何反馈（键盘用户无法判断焦点在哪）。这里恢复一圈描边：
    # 平时用分隔线色，`focus` 时切主色（与 TEntry 的焦点口径一致：颜色之外还有一圈
    # 粗细变化，不靠颜色单一通道）。
    widget.configure(background=COLOR["text_bg"], foreground=COLOR["fg"],
                     selectbackground=COLOR["accent"],
                     selectforeground=COLOR["on_accent"],
                     borderwidth=1, relief="solid",
                     highlightthickness=1,
                     highlightcolor=COLOR["accent"],
                     highlightbackground=COLOR["line"],
                     takefocus=True)
