r"""Windows 外壳的视觉主题：创建窗口时调一次 `apply(root)`。

**2026-10-17 用户拍板：ttk 主题 `vista` → `clam`。**
为什么必须换：`vista` 主题的控件由**系统绘制**，`ttk.Style.configure()` 改不动颜色 ——
不换就做不出与 mac 侧同一套色板（"两个平台像一家人"是项目既定目标）。
`clam` 是 Tk 自带、完全由 ttk 自己画的，颜色/字体/间距都能统一。

**这一层只做「主题 + 色板 + 字号」**；把 `tk.Button` / `tk.Entry`（Tk 经典控件）逐个换成
ttk 控件是后续的事 —— clam 管不到经典控件的外观。`tk.Text`（日志）保留原生白底，
与 clam 的浅灰底形成"卡片"层次（Fluent 的 base layer + content layer 近似做法）。

⚠️ 不做深色模式：Tk 8.6 不跟随系统主题，**半套深色比没有更糟**（用户 2026-10-17 已知）。
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
    style.configure("TNotebook", background=COLOR["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", padding=(12, 6))
    style.map("TNotebook.Tab",
              background=[("selected", COLOR["tab_on"]), ("!selected", COLOR["bg"])])
    style.configure("Treeview", rowheight=base + 11, fieldbackground="#ffffff",
                    background="#ffffff")
    style.configure("Treeview.Heading", font=(None, base, "bold"))
    style.configure("TCombobox", padding=(4, 2))
    style.configure("TSeparator", background="#d0d7de")
