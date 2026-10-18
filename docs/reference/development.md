---
title: 开发与构建
description: 仓库结构、构建命令、文档站本地开发。
---

# 开发与构建

## 仓库结构

```text
Engine/         Python 引擎（唯一一份文档逻辑）
sources-mac/    macOS 外壳（SwiftUI）
sources-win/    Windows 外壳（Tkinter）
编辑笔记/模板文件/ 本刊排版模板
docs/           本站（VitePress 文档站）
```

一个引擎、两个外壳：文档逻辑全在 `Engine/`，外壳只做「取值 → 拼参数 → 显示日志」，
保证两个平台算出来的结果一致。

## 构建

```bash
# macOS App（产物 dist/编辑部工具箱.app）
bash build.sh

# Windows 包（在 Windows 上；产物 sources-win/build/dist/ 下的两个 exe）
cd sources-win
pwsh -ExecutionPolicy Bypass -File build-win.ps1

# 引擎测试（依赖装进 Engine/vendor）
cd Engine
python3 -m pip install --target vendor -r requirements.txt
PYTHONPATH="$PWD/vendor" python3 tools/selfcheck.py --full

# Swift 测试台
bash tools/run_swift_tests.sh

# Windows 外壳桥接测试（Windows 或带 tkinter 的环境）
PYTHONPATH=Engine/vendor python3 sources-win/tests/test_bridge.py
```

## 文档站本地开发

本站用 [VitePress](https://vitepress.dev/) 构建：

```bash
cd docs
npm install
npm run docs:dev      # 本地预览 http://localhost:5173/EditToolBoxStudiesInLogic/
npm run docs:build    # 产出 docs/.vitepress/dist
```

推送到 `main` 且改动 `docs/**` 时，GitHub Actions 会自动构建并部署到 GitHub Pages。

## 参与与红线

- 作者来稿目录**只读**；试验输出写到 `out/` 或临时目录（macOS/Linux `/tmp/`，Windows `%TEMP%`）；
- 公开仓库不放入作者姓名、未发表稿件片段、API 密钥；
- 修改引擎逻辑要跑隔离普查、反例测试、全库回归与文档一致性检查（见仓库 `AGENTS.md`）；
- 更多约定：`Engine/docs/INVARIANTS.md`、`RELEASING.md`。
