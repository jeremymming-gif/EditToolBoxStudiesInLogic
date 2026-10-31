---
title: 维护者接入
description: 本机接入（Windows / macOS 单命令源）、自检命令、文档站维护与发布口径——维护者的命令级入口。
---

# 维护者接入

本页面向**本刊编辑部维护者**：给出本机接入、自检、构建与文档站维护的**命令级**做法。案例、成本、人员分工与内部流程不在这里（留在笔记仓），公开仓只写可公开、可复制的命令与指针。

## 1. 本机接入（两个平台）

工具链只依赖 **Python 3.14**、**git** 与 TeX/LibreOffice（编译与排版需要）。引擎依赖装进 `Engine/vendor/`，不污染系统 Python。

**Windows（PowerShell）**

```powershell
cd Engine
py -3.14 -m pip install --target vendor -r requirements.txt
$env:PYTHONPATH = "$PWD\vendor"
py -3.14 tools\selfcheck.py            # quick；发版前用 --full
```

**macOS**

```bash
cd Engine
python3.14 -m pip install --target vendor -r requirements.txt
export PYTHONPATH="$PWD/vendor"
python3.14 tools/selfcheck.py          # quick；发版前用 --full
```

> 只需用 GUI/发布包、不改代码的同事**不需要**这一步：发布包内置运行时，解压即用（见[安装说明](/guide/install)）。

## 2. 自检与门禁

在仓库根执行（两个平台同一份判据）：

```bash
# 环境/依赖冒烟（quick）
cd Engine && PYTHONPATH="$PWD/vendor" python3.14 tools/selfcheck.py

# 文档看门狗：README / AGENTS / docs/** 里的路径引用必须真实存在
py -3.14 -X utf8 Engine/tests/test_docs_consistency.py
```

- 看门狗期望输出末行「失败 0 项（全部通过）」；路径引用坏掉时会逐条列出。
- 历史叙述行（含「已删/已移除/历史/曾/不再」等）按约定豁免，见 `AGENTS.md`「文档维护」。
- Windows 上请带 `-X utf8`（否则中文输出可能乱码）；`PYTHONPATH` 用分号分隔。

## 3. 构建（只在需要发版时）

```bash
# macOS App
bash build.sh

# Windows（在 Windows 上，产物在 sources-win/build/dist/ 下两个 exe）
cd sources-win
powershell -ExecutionPolicy Bypass -File build-win.ps1
```

发版流程与 tag/Release 口径见 `RELEASING.md`；构建前先跑第 2 节自检。

## 4. 文档站维护

本站是 VitePress（`docs/`）：

```bash
cd docs
npm ci
npm run docs:dev       # 本地预览 http://localhost:5173/EditToolBoxStudiesInLogic/
npm run docs:build     # 构建产物在 docs/.vitepress/ 下的 dist（构建时生成，不入库）
```

- **本地构建需要 Node ≥ 18**（`docs/package.json` 的 VitePress 依赖）；只改文案时可以推到 `main` 由 CI（Node 20）构建验证。
- 推送到 `main` 且改动 `docs/**` 时，GitHub Actions 自动构建并部署 Pages。
- 新增或改名页面**必须**同步 `docs/.vitepress/config.mts` 的 nav/sidebar（当前：nav 5 项、sidebar 4 组），否则页面直达但没有入口。
- 站内下载资产放 `docs/public/templates/`（Word / LaTeX / 样式文件）；页面里用站内路径链接，不要贴 GitHub 附件地址。

## 5. 故障速查

环境检查不过、引擎找不到、编译失败、文档站构建失败、看门狗变红：见[故障速查](/maintain/troubleshooting)。
八项环境检查与诊断包说明见[环境检查与诊断](/reference/environment-check)。

## 6. 指针

- 开发与构建细节：[开发与构建](/reference/development)
- 规则与红线：仓库根 `AGENTS.md`（公开仓）与 `Engine/docs/RULES.md`、`Engine/docs/INVARIANTS.md`
- 发布流程：`RELEASING.md`
- Windows 冒烟清单：[Windows 冒烟清单](/reference/windows-smoke-checklist)
