# WindowsToolBox（Windows 外壳）

**这是什么**：编辑部工具箱的 **Windows 版界面**。它是个**壳**——稿件处理逻辑一行都不在里面，
全部在隔壁的 [`Engine/`](../Engine/)（Python 引擎，唯一一份）。mac 版是同一个引擎的另一层壳。

```text
sources-win/            ← 本目录：Tkinter 界面（取值 → 拼参数 → 显示日志）
├── main.py              界面：4 个标签页 + 底部实时日志
├── engine.py            **与引擎之间的唯一桥梁**（不含 tkinter，所以能离开窗口测试）
├── settings.py          本地配置（%APPDATA%\EditToolBox\settings.json，0600）
└── tests/test_bridge.py 桥梁测试（41 项断言，在 mac/CI 上也能跑）
```

## 怎么跑

**开发机**（装了 Python 3.9+ 与引擎源码）：

```bat
cd sources-win
python main.py
```

界面里若提示「找不到引擎」，用环境变量指明：

```bat
set SLCN_ENGINE=D:\EditToolBox\Engine
python main.py
```

**用户机器**（发布形态，不需要装 Python）：见下面「打包」。

## 九个标签页（与 mac 版功能对等）

| 标签页 | 调哪个引擎入口 | 说明 |
|---|---|---|
| 初排转换 | `convert.py --source … --out …` | 作者稿件（.docx/.doc/.tex）→ 符合本刊规范的初排稿；可同时出《文献核实任务书》 |
| 文献回读校验 | `checkbib.py --returned …` | 编辑改完交回的 .bib 体检；可**并回**产物 `reference.bib`（并入前自动备份 `.ai前`） |
| 合并文件 | `mergepdf.py --folder …` | 把一期的扉页/目录/各篇/封底合并成一个 PDF 并重建书签（编委页、封底、嵌套书签都是开关） |
| **一校核查** | `proofcheck.py --tex …` | 一校之后按《排校手册》《流程化经验书》逐条核查（26 条规则）；默认只报不改，可另存改后稿 |
| **检查tex信息** | `infocheck.py --folder …` | 汇总各篇卷期年/页码/题目/作者/字号/行距并与 PDF 实际页数对比；界面用 `--preview` 填表格（不落文件） |
| **生成目录** | `toc.py --report …` | 从中英文检查报告生成两份目录 .tex（模板继承上一期成品）；「读取篇目」用 `--preview`，勾选写入 `目录设置.json` |
| **编译全部** | `compileall.py --folder …` | 对每个 `NN 作者/` 跑 xelatex → biber → xelatex ×2；跳过 `000 目录`，某篇失败只停那一篇 |
| **删除临时文件** | `cleantemp.py --folder …` | 递归删编译中间文件（不删 `.tex/.pdf/.log`）；默认先预演 |
| 设置 | — | 引擎状态、API 地址/Key/模型、检索工具；密钥只落本机并打码显示 |

参数与退出码的权威说明在 [`Engine/docs/ENGINE_API.md`](../Engine/docs/ENGINE_API.md)。

## 打包（PyInstaller）

两块分开冻：**引擎冻成一个 exe**，界面冻成另一个。用户机器上零 Python 依赖。

```bat
:: ① 冻结引擎 → SLCNEngine.exe（三个 CLI 入口合成一个，见 Engine/engine_main.py）
cd Engine
pyinstaller --onefile --console --name SLCNEngine engine_main.py
copy dist\SLCNEngine.exe ..\sources-win\SLCNEngine.exe

:: ② 冻结界面（把 Engine\ 与 SLCNEngine.exe 一起放进去）
cd ..\sources-win
pyinstaller --onefile --noconsole --name SLCN编辑器工具箱 ^
    --add-data "SLCNEngine.exe;." ^
    --add-data "..\Engine;Engine" ^
    --icon icon.ico main.py
```

> ⚠️ `vendor/` 里 `lxml` 是**编译件**，必须用 Windows 轮子（`pip install --target vendor lxml pypdf pypinyin` 在 Windows 上做一遍）；
> `pypdf` / `pypinyin` 是纯 Python，跨平台无碍。细节见 `Engine/docs/ENGINE_API.md` §六「平台矩阵」。

## 这个外壳与它原来的样子（改动清单）

界面骨架、中文交互（路径框 + 浏览/运行 + 底部日志）**取自 chenphilo 的
[`Editorialbox`](https://github.com/chenphilo/Editorialbox)**（MIT，见文末归属）。
**改动只有两类**，都是为了「引擎只有一份」这条纪律：

| 原版 | 现在 |
|---|---|
| 功能实现在界面进程里直接跑（`merge.py` / `outinfo.py` / `c_bibtex.py` …） | 一律**起子进程调引擎 CLI**（`engine.py`），界面不复制任何逻辑 |
| 日志靠 `out_txt.insert(...)` 就地打印 | 逐行读子进程 stdout（`PYTHONUNBUFFERED=1`），**实时**显示，可「停止」 |
| 7 个标签页各自一套实现 | 3 个功能页 + 1 个设置页；每个功能页对应一个引擎入口 |

**功能已对齐**：mac 版的每一项，本壳都有一页，且都起子进程调同一个引擎入口
（2026-10-15/16 分三批搬完：合并文件、删除临时文件、编译全部、一校核查、检查tex信息、生成目录）。
**没有任何一页在 Windows 侧另写逻辑** —— 这是"两个平台算出来的结果一致"的唯一保证。

## 测试

```bat
cd sources-win
set PYTHONPATH=..\Engine\vendor
python tests\test_bridge.py
```

`tests/test_bridge.py` 共 41 项断言，覆盖：引擎/解释器定位顺序、命令行与契约一致、
**密钥绝不进 argv**、UTF-8 与无缓冲环境、配置读写与 0600 权限、真跑一次合并（收到实时日志与契约行）、
以及**真开一次窗口**（控件参数写错只有那一步抓得到；没有显示器时自动 SKIP）。

## 归属声明（MIT）

界面骨架与部分交互取自 chenphilo 的 `Editorialbox`：

```text
MIT License

Copyright (c) 2024 chenphilo
```

本目录下 `main.py` 的 Tkinter 布局与中文交互说明沿用该项目的设计；
`engine.py` / `settings.py` / `tests/` 为本项目新写。完整第三方声明见仓库根的
`THIRD_PARTY.md`。
