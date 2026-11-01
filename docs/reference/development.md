---
title: 开发与构建
description: 仓库结构、构建命令、文档站本地开发。
---

# 开发与构建

## 仓库结构

```text
Engine/         Python 引擎（唯一一份文档逻辑）
sources-flet/   Flet 单壳（默认产品线；Windows/macOS 共用同一套界面）
sources-win/    Windows 外壳（Tkinter，frozen：仅源码回退参考）
sources-mac/    macOS 外壳（SwiftUI，frozen：仅源码回退参考）
编辑笔记/模板文件/ 本刊排版模板
docs/           本站（VitePress 文档站）
```

一个引擎、一个默认外壳：文档逻辑全在 `Engine/`，Flet 单壳只做「取值 → 拼参数 → 显示日志」（`services/engine_bridge.py`），
保证两个平台算出来的结果一致。旧 Tkinter/SwiftUI 外壳自 v1.5.0 起 frozen，不再随发布线构建。

## 构建

```bash
# Flet 单壳：开发运行（热重载）与单元测试
cd sources-flet
python3 -m flet run app/main.py
python3 -m pytest tests -q

# Flet 单壳：打包（onefile；引擎 sidecar 另由 CI/打包脚本产出）
python3 -m flet pack app/main.py --name 编辑部工具箱 --distpath dist --yes

# 打包口径与发布线说明
cat sources-flet/packaging/README.md

# 引擎测试（依赖装进 Engine/vendor）
cd Engine
python3 -m pip install --target vendor -r requirements.txt
PYTHONPATH="$PWD/vendor" python3 tools/selfcheck.py --full

# 旧外壳构建脚本（frozen，仅回退参考）
bash build.sh                                  # 旧 macOS App
cd sources-win && pwsh -ExecutionPolicy Bypass -File build-win.ps1   # 旧 Windows 两个 exe

# 旧 Windows 外壳桥接测试（冻结前回归参考）
PYTHONPATH=Engine/vendor python3 sources-win/tests/test_bridge.py
```

## 文档站本地开发

本站用 [VitePress](https://vitepress.dev/) 构建：

```bash
cd docs
npm install
npm run docs:dev      # 本地预览 http://localhost:5173/EditToolBoxStudiesInLogic/
npm run docs:build    # 产出在 docs/.vitepress/ 下的 dist（构建时生成，不入库）
```

推送到 `main` 且改动 `docs/**` 时，GitHub Actions 会自动构建并部署到 GitHub Pages。

## 参与与红线

- 作者来稿目录**只读**；试验输出写到 `out/` 或临时目录（macOS/Linux `/tmp/`，Windows `%TEMP%`）；
- 公开仓库不放入作者姓名、未发表稿件片段、API 密钥；
- 修改引擎逻辑要跑隔离普查、反例测试、全库回归与文档一致性检查（见仓库 `AGENTS.md`）；
- 更多约定：`Engine/docs/INVARIANTS.md`、`RELEASING.md`。
