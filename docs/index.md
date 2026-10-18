---
title: 编辑部工具箱
layout: default
---

# 编辑部工具箱（SLCN Editorial Toolbox）

《逻辑学研究》编辑部自用的稿件处理工具箱：把作者提交的 **Word / LaTeX 文稿**转成符合本刊排版规范的
**可编译初排稿**，并处理参考文献核实、整期 PDF 合并、引用库管理等编辑环节。

> 这是编辑部的工作工具，不是通用软件：默认模板、字段规范与判据都按本刊体例写死。

## 它能做什么

| 模块 | 能力 |
|---|---|
| **单篇稿件** | 初排转换（Word/LaTeX → 可编译 `.tex`）· 文献回读校验 · 一校核查 · 合并文件 |
| **整期装配** | 检查 tex 信息 · 编译全部 · 生成目录 · 删除临时文件 · 本期出刊 |
| **引用库** | 把已核实的参考文献沉淀成编辑部自己的库；查库、入藏、待裁决清单、核实设置 |
| **成本控制** | 核实只核正文引用（上限可调）、先算账（`--verify-plan`）、引用库命中直接跳过检索与 AI |

## 下载与安装

到 **[最新 Release](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest)** 下载：

- **macOS**：`编辑部工具箱-vX.Y.Z-macos.zip` → 解压得到 App。首次打开如被系统拦下，请 **右键 → 打开**；
- **Windows**：`编辑部工具箱-vX.Y.Z-windows.zip` → 解压运行，**不需要另装 Python**。
  SmartScreen 提示时选「更多信息 → 仍要运行」。

详细步骤见 [安装说明](install.md)。

## 快速上手

1. 把作者的源文件（`.docx` / `.tex`）拖进 **初排转换** 页的「源文件」栏；
2. 选输出目录，按需填写作者/出版信息与转换选项；
3. 点「运行」，看运行日志；产物目录里是 `.tex` + `reference.bib` + 模板 `.sty`（可编译）。

更多见 [快速上手](quickstart.md)。整期流程与引用库工作流见 [使用说明](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/blob/main/使用说明.md)。

## 截图

> 截图正在补充，见 [`assets/screenshots/`](assets/screenshots/) 下的占位说明。

## 文档

- [使用说明（完整手册）](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/blob/main/使用说明.md)
- [引擎指南 GUIDE](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/blob/main/Engine/docs/GUIDE.md)
- [引擎 API / 参数](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/blob/main/Engine/docs/ENGINE_API.md)
- [版本记录（Releases）](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases)
- [常见问题](faq.md)

## 安全与费用

- API key 只存本机（macOS 在应用支持目录的 `ai.json`，Windows 在本地配置文件），只通过**环境变量**传给引擎，不进命令行；
- 不配置 API 也能用：转换、回读校验、整期装配全部离线；
- 只有主动勾选「用 AI 处理参考文献」才会调用模型/检索；**检索证据**按成功请求计费（¥5/1000 次），
  核实默认只核正文引用、引用库命中会跳过检索与 AI；想先看账可在「引用库 → 设置」打开**先算账**。

## 开发与反馈

- 仓库：<https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic>
- 一个引擎（`Engine/`，Python）+ 两个外壳（macOS SwiftUI / Windows Tkinter）；
- 构建与发版流程见 [RELEASING.md](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/blob/main/RELEASING.md)。

---

<sub>MIT License · 由 DeepSeek Harness 协助维护</sub>
