---
title: 安装说明
description: Windows / macOS 安装、首次打开与常见问题（Flet 单壳 v1.5.0 起）。
---

# 安装说明

> 桌面端自 **v1.5.0** 起为 **Flet 单壳**：Windows/macOS 同一套界面，产物由 `.github/workflows/build-flet.yml`
> 与 `release.yml` 两条流水线共用同一套打包口径。

## 下载

到[最新 Release](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest)下载对应平台的压缩包
（Windows 版与 macOS 版在同一页，认准文件名里的 `Windows` / `macOS`）：

<div class="download-row">
  <a href="https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest">⬇️ Windows 版</a>
  <a href="https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest">⬇️ macOS 版</a>
</div>

## Windows

编辑部同事多数使用 Windows；按下面四步即可。

1. 下载 `EditToolBox-<版本>-Windows-x64.zip`，解压到任意目录（路径尽量不含特殊符号）；
2. 解压后得到 `编辑部工具箱.exe`（Flet 单壳）与 `SLCNEngine.exe`（引擎 sidecar），
   **两个 exe 必须放在同一个文件夹**（压缩包已按此排好，不要拆开）；
3. 双击 `编辑部工具箱.exe` 运行；**不需要另装 Python**（Flet 运行时与引擎都已打包）；
4. 首次运行若弹出蓝色的 SmartScreen 提示（「Windows 已保护你的电脑」）：点 **「更多信息」→「仍要运行」**（只需一次）。

> 编译 LaTeX 需要本机安装 **TeX Live**（或 MiKTeX），`xelatex`、`biber` 要在 PATH 里；只做转换不编译则不需要。
>
> 处理 Word 稿时：作者若用早期 **MathType** 写公式（OLE/预览图 WMF/EMF），或要用「导出原稿 PDF」截矢量形状，
> 还需要本机安装 **LibreOffice**（条件必需依赖；不装时转换仍完成，但公式图位置是「公式图缺失」占位，产物不可直接送排）。

## macOS

macOS 版目前只提供 Apple 芯片（arm64）包。

1. 下载 `EditToolBox-<版本>-macOS-arm64.zip`，解压得到 `编辑部工具箱.app`，拖进「应用程序」；
2. 首次打开若提示「无法验证开发者」或「已损坏」：在访达里 **右键点 App → 打开 → 仍要打开**（只需一次），
   或终端执行 `xattr -dr com.apple.quarantine "/Applications/编辑部工具箱.app"`；
3. **不需要另装 Python**：引擎 sidecar 已打进 App（`Contents/MacOS/SLCNEngine`），Flet 运行时随包分发。

> 编译 LaTeX 需要本机安装 **TeX Live / MacTeX**（自带 `xelatex`、`biber`）；只做转换不编译则不需要。
>
> 处理 Word 稿时：作者若用早期 **MathType** 写公式（OLE/预览图 WMF/EMF），或要用「导出原稿 PDF」截矢量形状，
> 还需要本机安装 **LibreOffice**（`brew install --cask libreoffice`，条件必需依赖；缺失时公式图位置是「公式图缺失」占位）。

## 版本与更新

- 版本号唯一出处是仓库里的 `Engine/slcn/__init__.py`；Releases 页面同时维护 `latest.json`；
- 更新时下载新包覆盖旧目录即可，个人设置保存在系统配置目录（macOS 应用支持目录 / Windows `%APPDATA%`），不会被覆盖。

## 常见安装问题

| 现象 | 处理 |
|---|---|
| Windows 提示缺少 DLL / 打不开 | 确认解压完整（不要在压缩包里直接运行），`编辑部工具箱.exe` 与 `SLCNEngine.exe` 放在同一个文件夹 |
| Windows 首次运行被 SmartScreen 拦截 | 「更多信息」→「仍要运行」；确认文件是从 Releases 解压出来的完整包 |
| macOS 提示「已损坏，无法打开」 | 右键 → 打开；或系统设置 → 隐私与安全性 → 仍要打开；或 `xattr -dr com.apple.quarantine <App>` |
| 首页显示「引擎缺失」 | 确认 `SLCNEngine.exe` 与 `编辑部工具箱.exe` 在同一目录；或在「设置 → 路径 → 引擎目录」手动指定 |
| 拖文件到窗口没有反应 | Flet/Flutter 桌面窗口不接收系统拖放（已实测）；点「浏览…」用原生文件对话框，或把路径粘贴进输入框 |
| 首次启动明显偏慢（约 4–5 秒） | onefile 包要解压运行时，属已知现象；第二次启动通常约 2 秒 |
| 中文输入正常但候选窗位置不理想 | IME 候选窗未做自动化验证，属已知限制；可先确认系统输入法状态，仍异常请附截图反馈 |
| 转换成功但编译失败 | 检查是否安装 TeX 发行版（TeX Live / MiKTeX / MacTex）、`xelatex` 是否在 PATH；「设置 → 环境检查」可一键只读诊断 |
| Word 稿转换后公式位置是「公式图缺失」占位 | 作者用了 MathType/OLE 公式，本机未装或未找到 LibreOffice；装好后**重跑一次初排转换** |

更多问题见[常见问题](../reference/faq)。

## 装好之后仍然失败？

先看 **设置 → 环境检查**（只读调用 `Engine/envcheck.py --json`，显示 TeX / biber / LibreOffice / 字体 / 磁盘检查）
与运行日志区（可切「只看摘要」）：失败时结果横幅给出可读原因与产物路径，日志尾部保留原始输出。
找不到 `xelatex`/`biber` 属工具缺失，按本文对应小节补齐后重跑；诊断信息只在本机生成，不会上传。
