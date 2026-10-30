---
title: 安装说明
description: Windows / macOS 安装、首次打开与常见问题。
---

# 安装说明

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
2. 解压后得到 `编辑部工具箱.exe` 与 `SLCNEngine.exe`，**两个 exe 必须放在同一个文件夹**（压缩包已按此排好，不要拆开）；
3. 双击 `编辑部工具箱.exe` 运行；**不需要另装 Python**（引擎已冻进 `SLCNEngine.exe`）；
4. 首次运行若弹出蓝色的 SmartScreen 提示（「Windows 已保护你的电脑」）：点 **「更多信息」→「仍要运行」**（只需一次）。

> 编译 LaTeX 需要本机安装 **TeX Live**（或 MiKTeX），`xelatex`、`biber` 要在 PATH 里；只做转换不编译则不需要。
>
> 处理 Word 稿时：作者若用早期 **MathType** 写公式（OLE/预览图 WMF/EMF），或要用「导出原稿 PDF」截矢量形状，
> 还需要本机安装 **LibreOffice**（条件必需依赖；不装时转换仍完成，但公式图位置是「公式图缺失」占位，产物不可直接送排）。

## macOS

macOS 版目前只提供 Apple 芯片（arm64）包。

1. 下载 `EditToolBox-<版本>-macOS-arm64.zip`，解压得到 `编辑部工具箱.app`，拖进「应用程序」；
2. 首次打开若提示「无法验证开发者」或「已损坏」：在访达里 **右键点 App → 打开 → 仍要打开**（只需一次）；
3. 引擎与依赖随 App 打包；App 运行需要本机有 `python3`（首次运行会体检版本，提示版本不对时按提示安装后重开 App）。

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
| Windows 提示缺少 DLL / 打不开 | 确认解压完整（不要在压缩包里直接运行），两个 exe 放在同一个文件夹 |
| Windows 首次运行被 SmartScreen 拦截 | 「更多信息」→「仍要运行」；确认文件是从 Releases 解压出来的完整包 |
| macOS 提示「已损坏，无法打开」 | 右键 → 打开；或系统设置 → 隐私与安全性 → 仍要打开 |
| 找不到引擎 | Windows 确认 `SLCNEngine.exe` 与 `编辑部工具箱.exe` 在同一个文件夹；macOS 在设置页确认「引擎路径」（App 自带引擎，通常不用改） |
| 转换成功但编译失败 | 检查是否安装 TeX 发行版（TeX Live / MiKTeX / MacTeX）、`xelatex` 是否在 PATH |
| Word 稿转换后公式位置是「公式图缺失」占位 | 作者用了 MathType/OLE 公式，本机未装或未找到 LibreOffice；装好后**重跑一次初排转换**（见 `使用说明.md` §一「Word 公式图（MathType）与 LibreOffice（条件必需）」） |

更多问题见[常见问题](../reference/faq)。

## 装好之后仍然失败？

先看 App 里的**诊断卡**：它把退出码翻成一句原因 + 2–4 条下一步，并给出「打开日志 / 复制诊断包 / 查看文档」。
红卡 = 初排转换(2)、信息检查(2)、编译全部(1)、启动失败(<0)；参考文献规范化/引用校验退出码 1 是中性卡（有命中，不是失败）。
找不到 `xelatex`/`biber`/`python3` 属工具缺失，按本文对应小节补齐后重跑；诊断包只在本机生成，不会上传。
