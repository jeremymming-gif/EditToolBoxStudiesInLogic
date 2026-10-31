---
title: 故障速查（维护者）
description: 环境检查不过、引擎找不到、LaTeX 编译失败、文档站构建失败、看门狗变红的排查顺序。
---

# 故障速查（维护者）

按「先定位层次，再看具体报错」的顺序排查；每一步都给出可复制的命令或指针。

## 1. 先跑一次环境检查

App 内：「设置 → 环境检查」；命令行：

```bash
cd Engine
PYTHONPATH="$PWD/vendor" python3.14 envcheck.py            # 人类可读
PYTHONPATH="$PWD/vendor" python3.14 envcheck.py --json     # 外壳读的就是它
```

- 退出码 `0` = 必需项齐；`1` = 必需齐、可选项缺（功能受限）；`2` = 必需项缺失。
- 八项含义、安装命令与诊断包内容见[环境检查与诊断](/reference/environment-check)。

## 2. 引擎找不到

现象：日志「找不到 Engine/convert.py」或启动即失败。

1. 确认 `Engine/convert.py` 存在（源码形态）；
2. 发布包形态：两个 exe（`编辑部工具箱.exe` 与 `SLCNEngine.exe`）必须在**同一目录**；
3. 源码运行时可显式指定：环境变量 `SLCN_ENGINE` 指向 `Engine/` 目录（该目录必须含 `convert.py`）；
4. 源码运行还需要 `PYTHONPATH` 含依赖目录（`Engine/vendor` 或已安装的 lxml/pypinyin/pypdf）。

## 3. LaTeX 编译失败

- `xelatex: not found` / `biber: not found`：装 TeX Live（Windows 可 MiKTeX）并确认在 PATH；`biber` 随发行版安装；
- `Fatal error occurred`：先看引擎日志尾部与 `.log`；常见为缺字体、缺 `.sty`（模板样式必须与 `.tex` 同目录）、`fontset` 不匹配；
- 中文稿必须用 **XeLaTeX**（UTF-8 源文件），不要改成 pdfLaTeX；
- 只做初排不编译时可在转换页取消「编译」勾选，先出 `.tex` 再单独编译。

## 4. Word 稿公式图缺失

现象：产物里公式位置是「公式图缺失」占位。

- 作者用了 MathType/OLE 等旧公式：需要本机 **LibreOffice**（条件必需依赖）；
- 装好后**重跑一次初排转换**（不会自动回填旧产物）；
- 纯 LaTeX 稿不需要 LibreOffice。

## 5. 文档站构建失败

- 本地 `npm run docs:build` 报 Node 版本：本站需要 **Node ≥ 18**（CI 用 Node 20）；本机 Node 16 时不要在本地构建，改由 CI 验证；
- 死链：VitePress 会检查站内链接；下载资产（`.docx/.pdf/.tex/.bib/.sty`）已在 `docs/.vitepress/config.mts` 的 `ignoreDeadLinks` 里按 `/templates/` 规则放行；
- 页面没有入口：检查 `config.mts` 的 nav/sidebar 是否已登记（nav 5 项、sidebar 4 组）；
- 部署后 404：等 Pages 构建完成或强制刷新；下载链接 404 时用仓库 `docs/public/templates/` 源文件直链。

## 6. 文档看门狗变红

```bash
py -3.14 -X utf8 Engine/tests/test_docs_consistency.py
```

- 「(b) 文档里引用的路径必须存在」逐条列出缺失路径：改成真实存在的相对路径，或把该行改写为历史叙述（含「已删/已移除/历史/曾/不再」等豁免词）；
- 新增页面后出现路径引用不存在的红：确认 `docs/**` 里引用的仓库路径（`Engine/...`、`docs/...`、`编辑笔记/模板文件/...`）确实存在；
- 只统计、不判失败的两类：案例文件与「有意不带」的内部资料（公开仓不含）。

## 7. 设置与引用库在哪（升级/回滚不丢）

- Windows：`%APPDATA%\EditToolBox\settings.json` 与引用库目录；
- macOS：系统「应用支持」目录下的工具箱配置；
- 升级：下载新包覆盖旧目录即可（配置不会被覆盖）；回滚：换回旧包，配置与引用库不动；
- 发版验证时可用隔离的 `APPDATA`/配置目录做升级/回滚演练。

## 8. 升级前收集什么

- 版本号（`Engine/slcn/__init__.py` 是唯一出处）与构建提交；
- App 内「导出诊断包…」生成的 zip（只含版本、系统、八项检查结论，不含稿件/密钥/引用库）；
- 复现步骤 + 日志尾部（App「打开日志」定位本次运行日志）。

> 仍无法定位时，把上述三样（版本、诊断包、日志尾部）交给维护者/上游，并注明「发布包还是源码形态」。
