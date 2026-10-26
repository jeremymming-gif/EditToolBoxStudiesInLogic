"""「设置」页（`SettingsTab`；文件名必须带 `_tab`，防与 `settings.py` 同名遮蔽）（B-230 从 `main.py` **原样搬出**；纯搬运，行为零变化）。"""
from __future__ import annotations

import json
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import engine as E
import settings as S
import theme
from .base import APP_TITLE, _check

class SettingsTab(ttk.Frame):
    """设置：引擎状态 + AI 配置（密钥只落本地，显示打码）。"""

    name = "设置"

    def __init__(self, parent, cfg: dict, on_save, post=None):
        super().__init__(parent)
        self.cfg = cfg
        self.on_save = on_save
        # 子线程只往队列里放结果（Tk 控件一律在主线程改 —— 项目纪律）
        self.post = post or (lambda _msg: None)
        self.columnconfigure(1, weight=1)
        row = 0

        self.status_label = ttk.Label(self, text="", wraplength=760, justify="left")
        self.status_label.grid(row=row, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        row += 1

        self.vars: dict[str, tk.StringVar] = {}
        for label, key, hint in (
            ("引擎目录（Engine/）", "engineDir", "留空 = 自动找；也可用环境变量 SLCN_ENGINE"),
            ("API 地址", "base", "例如 https://api.deepseek.com"),
            ("API Key", "key", "只存在本机，显示时打码；绝不出现在命令行里"),
            ("模型名", "model", "例如 deepseek-chat"),
            ("检索工具名/路径", "searchTool",
             "留空 = 用引擎默认；内置检索由端点/模型决定（DeepSeek 需 deepseek-v4-pro）"),
        ):
            ttk.Label(self, text=label).grid(row=row, column=0, padx=10, pady=3, sticky="e")
            var = tk.StringVar(value=str(cfg.get("api", {}).get(key, "") or cfg.get(key, "")))
            self.vars[key] = var
            show = "•" if key == "key" else ""
            entry = ttk.Entry(self, textvariable=var, width=52, show=show)
            entry.grid(row=row, column=1, padx=10, pady=3, sticky="ew")
            if key == "key":
                self.key_entry = entry                      # 显示/隐藏要切它的 show 属性
                self.v_show_key = tk.BooleanVar(value=False)
                tools = ttk.Frame(self)
                tools.grid(row=row, column=1, padx=10, sticky="e")
                ttk.Checkbutton(tools, text="显示密钥", variable=self.v_show_key,
                                command=self._toggle_key_visible).pack(side="right")
                ttk.Button(tools, text="清除密钥", width=10, command=self._clear_key).pack(
                    side="right", padx=6)
            ttk.Label(self, text=hint, style="Muted.TLabel").grid(
                row=row + 1, column=1, padx=10, sticky="w")
            row += 2

        # 方案 §4「设置页不吓人」：只留**必需**项。思考模式/最长输出是任务相关选项，
        # 已挪到「初排转换」页的 AI 区域（那里才知道上下文）。
        self.v_web = tk.BooleanVar(value=bool(cfg.get("api", {}).get("webSearch", False)))
        # P2（第 72 条起）：检索证据开关（默认关）——与「允许联网检索」是**两件事**：
        # 后者是模型内置检索工具；前者是工具自己用 DeepSeek 独立 Search API 检索证据，
        # 把证据注入提示词并在合并前校验 `% 来源：` URL（按次计费 ¥5/1000 次）。
        self.v_searchrefs = tk.BooleanVar(value=bool(cfg.get("api", {}).get("searchRefs", False)))
        opts = ttk.Frame(self)
        opts.grid(row=row, column=1, padx=10, pady=6, sticky="w"); row += 1
        _check(opts, "允许联网检索", self.v_web).pack(side="left")
        _check(opts, "检索参考文献证据（DeepSeek 独立 Search API，按次计费；默认关）",
               self.v_searchrefs).pack(side="left", padx=(16, 0))

        # ── 第五十五批：AI 直连 API 的 token 预算闸（默认 30000 / 1000000；0 = 不限）──
        # 只约束「直连 API」通道；两段式外部 chat 由编辑自己操作、软件零上传零费用。
        api_cfg = cfg.get("api") or {}
        self.v_budget = tk.StringVar(
            value=str(api_cfg.get("budgetTokens", S.AI_BUDGET_DEFAULT)))
        self.v_batch_budget = tk.StringVar(
            value=str(api_cfg.get("batchBudgetTokens", S.AI_BATCH_BUDGET_DEFAULT)))
        budget_row = ttk.Frame(self)
        budget_row.grid(row=row, column=1, padx=10, pady=(6, 0), sticky="w"); row += 1
        ttk.Label(budget_row, text="单篇 token 预算").pack(side="left")
        ttk.Entry(budget_row, textvariable=self.v_budget, width=10).pack(
            side="left", padx=(4, 14))
        ttk.Label(budget_row, text="单批 token 预算").pack(side="left")
        ttk.Entry(budget_row, textvariable=self.v_batch_budget, width=12).pack(
            side="left", padx=(4, 10))
        ttk.Label(budget_row, text="0 = 不限", style="Muted.TLabel").pack(side="left")
        ttk.Label(
            self,
            text=(f"默认 {S.AI_BUDGET_DEFAULT} / {S.AI_BATCH_BUDGET_DEFAULT}；"
                  "只约束「直连 API」通道（超预计用量先打印账、不发起请求），"
                  "直连默认只发条目/片段；勾选「初排转换」页「全文 AI 核查（直连 API）」后"
                  "才会把正文分片发送（默认不勾、不持久化）。两段式外部 chat 流程由编辑自己操作，"
                  "软件不上传任何内容、零费用，预算不参与。"),
            style="Muted.TLabel", wraplength=760, justify="left").grid(
            row=row, column=1, padx=10, sticky="w"); row += 1
        # 非法值红字提示（不落盘）：颜色取 theme，不写死色号（(f) 组有守卫）
        self.budget_error = ttk.Label(self, text="", foreground=theme.COLOR["error"],
                                      wraplength=760, justify="left")
        self.budget_error.grid(row=row, column=1, padx=10, pady=(0, 4), sticky="w"); row += 1

        # 密钥常驻状态行：说清「已存/末四位/只在本机」—— 隐藏字段光靠打码，编辑不知道配没配对
        self.key_status = ttk.Label(self, text="", wraplength=760, justify="left",
                                    style="Muted.TLabel")
        self.key_status.grid(row=row, column=1, padx=10, pady=(0, 4), sticky="w"); row += 1
        btns = ttk.Frame(self)
        btns.grid(row=row, column=1, padx=10, pady=10, sticky="w")
        ttk.Button(btns, text="保存设置", width=14, style="Accent.TButton",
                   command=self.save).pack(side="left")
        # 测试连接（mac 侧早就有这个按钮）：把"配对了没有"从玄学变事实
        ttk.Button(btns, text="测试连接", width=12,
                   command=self.test_connection).pack(side="left", padx=8)
        self.refresh_status()
        self.refresh_key_status()

    def test_connection(self) -> None:
        """打一次最小请求，把「连不连得上 / 密钥对不对」变成一条实测结论。

        在**工作线程**里发请求（不冻界面），结果经主窗口队列回传（子线程不碰 Tk 控件）。
        密钥只进 Authorization 头，不进日志、不进命令行。
        """
        base = self.vars["base"].get().strip().rstrip("/")
        key = self.vars["key"].get().strip()
        model = self.vars["model"].get().strip()
        if not base:
            messagebox.showwarning(APP_TITLE, "请先填 API 地址（如 https://api.deepseek.com）")
            return
        self.key_status.config(text="⏳ 测试连接中…（最多等 10 秒）")
        threading.Thread(target=self._probe, args=(base, key, model), daemon=True).start()

    def _probe(self, base: str, key: str, model: str) -> None:
        import time as _t
        import urllib.request
        def report(text: str) -> None:
            self.post(("__KEYSTATUS__", text))
        t0 = _t.monotonic()
        # ① 先试 GET /models（尽力而为：自建端点没有这个路由不算失败）
        req = urllib.request.Request(base + "/models", headers={"User-Agent": "SLCN-toolbox"})
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                report(f"✅ 连接正常（{_t.monotonic() - t0:.1f} 秒，HTTP {resp.status}）· 密钥只在本机使用")
                return
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code == 401:
                report("⛔ 401 密钥无效（地址对、密钥不对）")
                return
            if code == 403:
                report("⛔ 403 无权限（密钥或 IP 受限）")
                return
            # ② 退回打一次最小 chat 请求（有的端点不提供 /models）
        body = json.dumps({"model": model or "deepseek-chat", "max_tokens": 1,
                           "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8")
        req2 = urllib.request.Request(base + "/chat/completions", data=body,
                                      headers={"Content-Type": "application/json",
                                               "User-Agent": "SLCN-toolbox"})
        if key:
            req2.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req2, timeout=10) as resp:
                report(f"✅ 连接正常（{_t.monotonic() - t0:.1f} 秒，HTTP {resp.status}）· 密钥只在本机使用")
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code in (401, 403):
                report(f"⛔ {code} 密钥无效或无权限")
            else:
                report(f"⛔ 连不上：{e}")

    def _toggle_key_visible(self) -> None:
        """眼睛开关：只切输入框的显示方式，**不落盘**。"""
        self.key_entry.config(show="" if self.v_show_key.get() else "•")

    def _clear_key(self) -> None:
        """清空密钥（只清界面输入框；点「保存」才真正覆盖磁盘 —— 与 mac 同一口径）。"""
        self.vars["key"].set("")
        self.refresh_key_status()

    def refresh_key_status(self) -> None:
        key = self.vars["key"].get().strip()
        if not key:
            self.key_status.config(text="密钥：未配置（本机/自建端点通常不需要）")
            return
        tail = key[-4:] if len(key) >= 8 else "…"
        self.key_status.config(
            text=f"密钥：已保存 · 末四位 {tail} · 只存在本机设置文件里，不会上传、不进命令行")

    def refresh_status(self) -> None:
        st = E.status()
        text = st.describe()
        if st.problems:
            text += "\n" + "\n".join("· " + p for p in st.problems)
        text += "\n（引擎目录改完点「保存」即生效；这一行显示的是当前实际解析到的路径）"
        self.status_label.config(text=text)

    def save(self) -> None:
        # 第五十五批：预算输入先校验 —— 非法值**不落盘**（红字提示，旧值保留）；
        # 其他设置照常保存，不让一个填错的预算把整页保存卡死。
        budget_article, err_article = S.parse_budget_tokens(
            self.v_budget.get(), S.AI_BUDGET_DEFAULT)
        budget_batch, err_batch = S.parse_budget_tokens(
            self.v_batch_budget.get(), S.AI_BATCH_BUDGET_DEFAULT)
        errors = [e for e in (err_article, err_batch) if e]
        self.budget_error.config(
            text=("；".join(errors) + "（本次未保存预算，其他设置照常保存）") if errors else "")
        for key, var in self.vars.items():
            if key == "engineDir":
                self.cfg["engineDir"] = var.get().strip()
                # 立刻生效（不用重开）：engine.py 读的就是这个环境变量
                if self.cfg["engineDir"]:
                    os.environ["SLCN_ENGINE"] = self.cfg["engineDir"]
                else:
                    os.environ.pop("SLCN_ENGINE", None)
            else:
                self.cfg.setdefault("api", {})[key] = var.get().strip()
        # thinking/maxTokens 不再由本页负责（input 在「初排转换」页，改即写进 cfg）
        self.cfg["api"]["webSearch"] = bool(self.v_web.get())
        self.cfg["api"]["searchRefs"] = bool(self.v_searchrefs.get())
        if not errors:
            self.cfg["api"]["budgetTokens"] = budget_article
            self.cfg["api"]["batchBudgetTokens"] = budget_batch
        try:
            path = S.save(self.cfg)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"保存失败：{e}")
            return
        # 保存回执：**写进窗口**而不是弹窗（静默成功/静默失败在用户眼里一样 —— 方案 §4）
        self.refresh_key_status()
        self.log(f"✅ 已保存设置（{time.strftime('%H:%M:%S')}，仅存在本机：{path}）") if hasattr(self, "log") else None
        self.key_status.config(
            text=f"✅ 已保存 · {time.strftime('%H:%M:%S')}（仅存在本机：{path}）")
