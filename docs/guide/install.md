---
title: 安装说明
description: macOS / Windows 安装、首次打开与常见问题。
---

# 安装说明

## 下载

到[最新 Release](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest)下载对应平台的压缩包：

<div class="download-row">
  <a href="https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest">⬇️ macOS 包</a>
  <a href="https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest">⬇️ Windows 包</a>
</div>

## macOS

1. 解压得到 `编辑部工具箱.app`，拖进「应用程序」；
2. 首次打开若提示「无法验证开发者」：在 Finder 里 **右键点 App → 打开 → 仍要打开**（只需一次）；
3. 引擎与 Python 已打包在 App 内，不需要另装 Python。

> 编译 LaTeX 需要本机安装 **TeX Live / MacTeX**（自带 `xelatex`、`biber`）；只做转换不编译则不需要。

## Windows

1. 解压到任意目录（路径尽量不含特殊符号）；
2. 双击主程序运行；**不需要另装 Python**（已打包）；
3. SmartScreen 提示时选「更多信息 → 仍要运行」。

> 编译 LaTeX 仍需本机安装 TeX Live；PDF 合并等处理已随包提供。

## 版本与更新

- 版本号唯一出处是仓库里的 `Engine/slcn/__init__.py`；Releases 页面同时维护 `latest.json`；
- 更新时下载新包覆盖旧目录即可，个人设置保存在系统应用支持目录，不会被覆盖。

## 常见安装问题

| 现象 | 处理 |
|---|---|
| macOS 提示「已损坏，无法打开」 | 右键 → 打开；或系统设置 → 隐私与安全性 → 仍要打开 |
| Windows 提示缺少 DLL / 打不开 | 确认解压完整（不要在压缩包里直接运行） |
| 转换成功但编译失败 | 检查是否安装 TeX Live、`xelatex` 是否在 PATH |
| 找不到引擎 | 设置页确认「引擎路径」；App 自带引擎，通常不用改 |

更多问题见[常见问题](../reference/faq)。
