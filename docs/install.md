---
title: 安装说明
layout: default
---

# 安装说明

## macOS

1. 打开 **[最新 Release](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest)**，下载
   `编辑部工具箱-vX.Y.Z-macos.zip`；
2. 解压得到 `编辑部工具箱.app`，拖进「应用程序」；
3. 首次打开若提示「无法验证开发者」：在 Finder 里 **右键点 App → 打开 → 仍要打开**（只需一次）。

> 不需要另装 Python：引擎已打包在 App 内。**编译 LaTeX 需要本机装 TeX Live / MacTeX**
> （MacTeX 自带 `xelatex`/`biber`）；不编译只做转换则不需要。

## Windows

1. 下载 `编辑部工具箱-vX.Y.Z-windows.zip`，解压到任意目录（路径尽量不要带奇怪的符号）；
2. 双击主程序运行；**不需要另装 Python**（已打包）；
3. SmartScreen 提示时选「更多信息 → 仍要运行」。

> 编译 LaTeX 仍需本机安装 TeX Live；PDF 合并等 PDF 处理已随包。

## 版本与更新

- 版本号唯一出处是 `Engine/slcn/__init__.py`；Releases 页面会同时更新 `latest.json` 供自动检查；
- 更新时直接下载新包覆盖旧目录即可（个人设置保存在系统应用支持目录，不会被覆盖）。

## 常见安装问题

| 现象 | 处理 |
|---|---|
| macOS 提示「已损坏，无法打开」 | 右键 → 打开；或系统设置 → 隐私与安全性 → 仍要打开 |
| Windows 提示缺少 DLL / 打不开 | 确认解压完整（不要直接在压缩包里运行） |
| 转换成功但编译失败 | 检查是否安装 TeX Live、`xelatex` 是否在 PATH；日志里会给出缺失命令 |
| 找不到引擎 | 设置页确认「引擎路径」；App 内自带引擎，通常不需要改 |
