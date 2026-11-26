---
title: 安装说明
description: 下载解压即用，保留原目录结构，双击主程序即可打开；装好后可顺手跑一次环境检查。
---

# 安装说明

> **本页目录**：[下载](#下载) · [Windows](#windows) · [macOS](#macos) · [版本与更新](#版本与更新) · [常见安装问题](#常见安装问题) · [装好之后仍然失败？](#装好之后仍然失败)

编辑部工具箱是 Flet 单壳桌面应用，安装包已自带引擎，**不需要另装 Python**。TeX（XeLaTeX + biber）与 LibreOffice 是按需依赖：只做转换不需要，要编译 PDF / 导出公式图才需要，见[系统要求](./system-requirements)。

## 下载

<div class="download-row">
  <a class="download-button" href="https://github.com/zhangwenming/EditToolBoxStudiesInLogic/releases/latest">前往最新 Release 下载</a>
</div>

在 Release 页面按平台选 `EditToolBox-<版本>-Windows-x64.zip` 或 `EditToolBox-<版本>-macOS-arm64.zip`。

## Windows

1. 下载 `EditToolBox-<版本>-Windows-x64.zip`，解压到任意目录（不要在压缩包里直接双击）。
2. **保留压缩包里的原目录结构**：主程序 `编辑部工具箱.exe` 旁边是 `SLCNEngine` 文件夹，**引擎入口是 `SLCNEngine/SLCNEngine.exe`**。两者要保持在同一个父目录里——把 exe 单独拖出来、或只复制主程序，都会导致首页提示引擎缺失。
3. 双击 `编辑部工具箱.exe` 运行。首次启动可能被 SmartScreen 拦下：点「更多信息」→「仍要运行」。
4. 想编译 PDF 就装 TeX Live 或 MiKTeX（Windows 上推荐 MiKTeX，装完确认 `xelatex` 与 `biber` 都在 PATH 里）；含 MathType 公式的 Word 稿还要装 LibreOffice。

::: tip 升级时
把新包解压到**新目录**，确认能打开后再删旧目录；直接覆盖旧目录时，先关掉正在运行的应用。
:::

## macOS

只提供 arm64（Apple 芯片）包。

1. 下载 `EditToolBox-<版本>-macOS-arm64.zip`，解压得到 `编辑部工具箱.app`。
2. 首次打开若提示「无法验证开发者」，右键点应用 →「打开」，或到「系统设置 → 隐私与安全性」里允许。
3. 引擎在应用包内：`Contents/Resources/SLCNEngine/` 目录（含 `_internal/sha256.txt`），`Contents/MacOS/SLCNEngine` 是启动脚本。**不要单独移动应用包内的文件**。
4. 首次打开若提示没有权限访问文稿文件夹，到「系统设置 → 隐私与安全性 → 文件与文件夹」里允许。

::: tip Apple 芯片才有的包
Intel Mac 请用源码方式运行，见 [README](https://github.com/zhangwenming/EditToolBoxStudiesInLogic#readme) 的「快速开始 → 从源码运行」。
:::

## 版本与更新

版本号唯一出处是仓库里的 `Engine/slcn/__init__.py`；应用内「设置中心 → 高级与关于」显示当前版本。想知道这一版改了什么，看[版本记录](../reference/changelog)或 GitHub Release 页。

设置保存在本机用户目录（Windows 是 `%APPDATA%\EditToolBox\settings.json`，macOS 是 `~/.editorial-toolbox/settings.json`），升级不会清掉设置。

## 常见安装问题

| 现象 | 怎么办 |
|---|---|
| 双击没反应 / 提示缺少 DLL | 主程序与 `SLCNEngine` 文件夹必须保持原目录结构；别只复制 exe |
| SmartScreen 拦截 | 「更多信息」→「仍要运行」 |
| macOS 提示「已损坏」 | 右键 →「打开」，或到隐私与安全性里允许 |
| 首页提示引擎缺失 | 确认 `SLCNEngine/SLCNEngine.exe` 还在主程序旁边；仍不行去设置中心 → 路径核对引擎目录 |
| 把文件拖到窗口上没反应 | 应用不接收系统拖放，统一用「浏览…」或粘贴路径 |
| 首次启动偏慢 | macOS 版是目录式打包（onedir），冷启动约半分钟属正常，之后会快 |
| 中文输入法候选窗位置不对 | 已知限制，见[已知限制](../reference/known-limitations) |
| 转换成功但编译失败 | 设置中心 → 引擎与环境里看环境检查，找 `xelatex` / `biber` 两项 |
| 公式图缺失 | 装 LibreOffice，装好后重跑一次 |

更多问题见[常见问题](../reference/faq)。

## 装好之后仍然失败？

1. 设置中心 → 引擎与环境，看环境检查九项哪项红（只读，可重跑）。
2. 日志区先只看「摘要」层，找到 `xelatex` / `biber` 相关的行。
3. 诊断信息只在本机生成：用「复制诊断信息（文本）」或「导出为文本文件」，不会自动上传任何内容，导出前可先核对预览。
