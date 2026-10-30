# 编辑部工具箱（SLCN Editorial Toolbox）

把编辑部从「来稿」到「成刊」的重复劳动自动化：初排转换、文献核实、一校核查、整期装配。
macOS 与 Windows 使用同一个引擎，同一份输入得到同样的结果。

[项目主页](https://jeremymming-gif.github.io/EditToolBoxStudiesInLogic/) ｜ [下载最新版](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest) ｜ [安装与首次打开](https://jeremymming-gif.github.io/EditToolBoxStudiesInLogic/guide/install)

## 能做什么

- **初排转换**：`.docx` / `.tex` 来稿 → 本刊模板排版稿（`.tex` + PDF + `reference.bib`），自动展开多文件、迁移前言依赖、复制图片，并默认套用无歧义的标点与脚注位置修正。
- **文献核实**：把来稿引用与 `reference.bib` 对照。可选直连大模型（需自备 API），也可走「两段式」外部对话流程（不产生 API 费用）。
- **一校核查**：按排版手册与校对经验逐条核查初排稿；初排已默认改好无歧义项，报告只列需要人工判断的命中，可一键补写修正（另存新文件）。
- **整期装配**：检查 tex 信息 → 编译全部 → 生成中英目录 → 删除临时文件 → 合并成刊 →（macOS）生成目录摘要推送文案。
- **编辑部引用库**：查库、入藏、待处理（字段冲突、重复、健康检查）；写库有 dry-run 与备份，提交由人工完成。
- **命令行**：引擎每个入口都能单独调用，便于脚本化与批量处理。

## 平台与下载

| 平台 | 安装方式 | 需要 Python 吗 |
|---|---|---|
| Windows 10/11（x64） | 解压即用：`SLCNEngine.exe` 与 `编辑部工具箱.exe` 放在同一文件夹，双击后者 | 不需要 |
| macOS（Apple 芯片） | `编辑部工具箱.app` 拖进「应用程序」 | 系统需有 python3 |

安装包未做代码签名：Windows 首次运行点「更多信息 → 仍要运行」；macOS 首次右键「打开」，或按[安装说明](https://jeremymming-gif.github.io/EditToolBoxStudiesInLogic/guide/install)移除隔离标记。下载页附各平台的 `FIRST-OPEN` 说明，`latest.json` 指向当前版本。本页不记录版本历史，变更见[版本记录](https://jeremymming-gif.github.io/EditToolBoxStudiesInLogic/reference/changelog)。

## 快速开始
### 直接使用（编辑部）

1. 从 [Releases](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest) 下载对应平台的 zip。
2. Windows：解压到任意目录，两个 exe 保持同目录，双击 `编辑部工具箱.exe`。
3. macOS：解压后把 `编辑部工具箱.app` 拖入「应用程序」，首次右键「打开」。

### 从源码运行（开发）

| 依赖 | 用途 | 必需性 |
|---|---|---|
| Python 3.14 | 引擎运行与测试（`vendor/` 依赖按 3.14 预编译） | 源码运行必需 |
| XeLaTeX + biber | 编译排版稿（TeX Live / MiKTeX / MacTeX） | 必需 |
| LibreOffice | 转换含 MathType / OLE 公式的 Word 稿；缺失时公式图为占位 | 按稿件情况 |
| Xcode Command Line Tools | macOS 打包 `.app` | 仅 macOS 构建 |

```bash
# 安装引擎依赖（一次）
cd Engine && python3 -m pip install --target vendor -r requirements.txt

# macOS：构建 App，产物 dist/编辑部工具箱.app
./build.sh && open dist/编辑部工具箱.app

# Windows：构建两个 exe，产物 build\dist\
cd sources-win
pwsh -ExecutionPolicy Bypass -File build-win.ps1
```

> 上面命令里的 `python3` 指你用来跑引擎的解释器；本机实测用 `/opt/homebrew/bin/python3.14`（`vendor/` 内的预编译依赖只适配 3.14）。CI 在云端用 Python 3.12 安装依赖后跑同一套测试。

### 只用引擎（命令行）

引擎入口都在 `Engine/` 下：`convert.py`（初排转换）、`checkbib.py`（文献核实）、`proofcheck.py`（一校核查）、`infocheck.py`、`toc.py`、`compileall.py`、`mergepdf.py`、`cleantemp.py`、`publish.py`、`bibdb.py`。参数、退出码与输出格式见 [`Engine/docs/ENGINE_API.md`](Engine/docs/ENGINE_API.md)。

```bash
cd Engine
PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 convert.py --source <来稿> --out <期目录> --compile
```

## 文档地图

| 文档 | 读者 | 内容 |
|---|---|---|
| [`使用说明.md`](使用说明.md) | 编辑部同事 | 面向使用者的操作手册（可直接转发） |
| [官网文档](https://jeremymming-gif.github.io/EditToolBoxStudiesInLogic/) | 所有人 | 快速上手、安装、常见问题、已知限制 |
| [`Engine/docs/GUIDE.md`](Engine/docs/GUIDE.md) | 引擎使用者 | 引擎用法、参数与行为细节 |
| [`Engine/docs/ENGINE_API.md`](Engine/docs/ENGINE_API.md) | 集成 / 脚本 | 各 CLI 参数、退出码与输出契约 |
| [`Engine/docs/PROOF_RULES.md`](Engine/docs/PROOF_RULES.md) | 一校核查 | 核查规则表与未实现项 |
| [`Engine/docs/RULES.md`](Engine/docs/RULES.md) | 维护者 | 排版与工程口径 |
| [`RELEASING.md`](RELEASING.md) | 维护者 | 发版与公开流程 |

## 仓库结构

```text
Engine/            引擎（Python）：convert / checkbib / proofcheck / infocheck / toc /
                   compileall / mergepdf / cleantemp / publish / bibdb 等入口
  slcn/            引擎内部模块（初排、核查、引用库、元数据等）
  docs/            GUIDE、ENGINE_API、PROOF_RULES、RULES 等工程文档
  tests/           引擎测试套件
sources-mac/       macOS 外壳（SwiftUI）
sources-win/       Windows 外壳（Tkinter）
编辑笔记/模板文件/  本刊模板与样式（随 App 发布）
tools/             开发与验收工具
build.sh           macOS App 打包脚本
```

## 开发与测试

```bash
# 生成 Engine/README.md 中的统计块（数字唯一出处，勿手抄）
cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tools/gen_docs.py

# 文档一致性与路径真实性检查
cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_docs_consistency.py

# 引擎全部测试套件（逐个跑，只看最后一行）
cd Engine && for f in tests/test_*.py; do PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 "$f" | tail -1; done

# Windows 外壳桥接测试（不改界面，只读引擎 API）
PYTHONPATH=Engine/vendor /opt/homebrew/bin/python3.14 sources-win/tests/test_bridge.py

# 一键自检（--full 含全部套件）
cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tools/selfcheck.py --full
```

- [`Engine/README.md`](Engine/README.md) 的统计块由脚本生成，改文档后请跑一次 `--check` 确认无差异。
- 完整开发约定、红线与验收清单见 [`AGENTS.md`](AGENTS.md)。

## 许可与注意

MIT，见 [`LICENSE`](LICENSE)；第三方组件与许可见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。

1. 本仓是公开代码与模板，不含真实期刊数据；稿件与编辑部数据保存在内部仓库。
2. 启用联网文献核实需自备并填写 API 配置，费用由服务商计费。
3. 覆盖安装不会自动迁移旧配置，升级前请先备份。
