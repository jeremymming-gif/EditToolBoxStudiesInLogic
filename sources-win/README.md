# WindowsToolBox（Windows 外壳）

**这是什么**：编辑部工具箱的 **Windows 版界面**。它是个**壳**——稿件处理逻辑一行都不在里面，
全部在隔壁的 [`Engine/`](../Engine/)（Python 引擎，唯一一份）。mac 版是同一个引擎的另一层壳。

```text
sources-win/            ← 本目录：Tkinter 界面（取值 → 拼参数 → 显示日志）
├── main.py              界面：分组侧边栏（三组 + 设置）+ 底部实时日志
├── bibdb_ui.py          第 132 条：引用库 5 页（库概况/查库/入藏/待裁决/设置）
├── theme.py             clam 主题 + 色板 + 字阶/间距（两平台同一套观感的前提）
├── engine.py            **与引擎之间的唯一桥梁**（不含 tkinter，所以能离开窗口测试）
├── settings.py          本地配置（%APPDATA%\EditToolBox\settings.json；POSIX 上 0600，Windows 靠该目录的 ACL）
└── tests/test_bridge.py 桥梁测试（不开窗口；mac / CI / Windows 上都跑）
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

## 十五个页面（与 mac 版功能对等）

界面用**分组侧边栏**（2026-10-17 起；UI 方案候选 B）：一层是组、二层是页 ——
「单篇稿件」= 初排转换 / 文献回读校验 / 合并文件 / 一校核查；
「整期装配」= 检查tex信息 / 生成目录 / 编译全部 / 删除临时文件 / 本期出刊；
「引用库」= 库概况 / 查库 / 入藏 / 待裁决 / 引用库设置（第 132 条）；
「设置」单独在底部。
（从前是 9 个平级标签：WinUI 的判据是「左侧导航适合 5–10 个同等重要分类」，而它们
其实分层，平级呈现会让人找不到"一次转换"该从哪进去。）

| 标签页 | 调哪个引擎入口 | 说明 |
|---|---|---|
| 初排转换 | `convert.py --source … --out …` | 作者稿件（.docx/.doc/.tex）→ 符合本刊规范的初排稿；可同时出《文献核实任务书》 |
| 文献回读校验 | `checkbib.py --returned …` | 编辑改完交回的 .bib 体检；可**并回**产物 `reference.bib`（并入前自动备份 `.ai前`）；产物 bib 可一键「入藏到引用库」 |
| 合并文件 | `mergepdf.py --folder …` | 把一期的扉页/目录/各篇/封底合并成一个 PDF 并重建书签（编委页、封底、嵌套书签都是开关） |
| **一校核查** | `proofcheck.py --tex …` | 一校之后按《排校手册》《流程化经验书》逐条核查（26 条规则）；默认只报不改，可另存改后稿 |
| **检查tex信息** | `infocheck.py --folder …` | 汇总各篇卷期年/页码/题目/作者/字号/行距并与 PDF 实际页数对比；界面用 `--preview` 填表格（不落文件） |
| **生成目录** | `toc.py --report …` | 从中英文检查报告生成两份目录 .tex（模板继承上一期成品）；「读取篇目」用 `--preview`，勾选写入 `目录设置.json` |
| **编译全部** | `compileall.py --folder …` | 对每个 `NN 作者/` 跑 xelatex → biber → xelatex ×2；跳过 `000 目录`，某篇失败只停那一篇 |
| **删除临时文件** | `cleantemp.py --folder …` | 递归删编译中间文件（不删 `.tex/.pdf/.log`）；默认先预演 |
| **库概况** | `bibdb.py stats/lint --json` | 引用库条数/期数/最近更新/冲突数/lint 结果；只读，可打开库文件夹 |
| **查库** | `bibdb.py query --key/--doi/--title --bibtex --json` | 按 key（含别名）/DOI/题名模糊查，选中复制 BibTeX；只读 |
| **入藏** | `bibdb.py import-bib/import-dir --dry-run --json` | 先 dry-run 预览（**不写库**）再确认导入（`--verified`）；来源期/篇从路径推断、可改 |
| **待裁决** | `bibdb.py report --out … --json` | 只读展示并重新生成 `CONFLICTS.md` / `DUPLICATES.md` / `duplicates.csv`（不改库本体） |
| **引用库设置** | —（写 settings.json，由初排转换拼参数） | 核实上限 `--verify-max`、先算账 `--verify-plan`、全量 `--verify-all-refs`、先查库开关（关 = `--no-bibdb`）、笔记仓路径；**默认一个参数都不加** |
| 设置 | — | 引擎状态、API 地址/Key/模型、检索工具；密钥只落本机并打码显示 |

参数与退出码的权威说明在 [`Engine/docs/ENGINE_API.md`](../Engine/docs/ENGINE_API.md)。

## 打包（PyInstaller）

两块分开冻：**引擎冻成一个 exe**，界面冻成另一个。用户机器上零 Python 依赖。

**不要手打 PyInstaller 命令** —— 用 `build-win.ps1`（唯一一份逻辑：`--hidden-import` 逐入口、
模板一起打包，末尾还会**逐入口 + 一次最小转换**自检；第 132 条起入口表含
`bibdb.py`，漏了它引用库 5 页在用户机器上全是「不认识的入口」）：

```powershell
pwsh -ExecutionPolicy Bypass -File build-win.ps1
# → build\dist\SLCNEngine.exe（引擎，十个入口冻成一个）
# → build\dist\编辑部工具箱.exe（界面；它会在自己旁边找 SLCNEngine.exe）
```

- 两个 exe **必须放在同一个目录**（界面 exe 按这个约定找引擎）；
- 用户机器上零 Python 依赖。

> ⚠️ 这里从前贴过一段手工配方（`pyinstaller --onefile --console --name SLCNEngine engine_main.py`
> 之类），**照做会冻出坏包**：dispatcher 是动态 import，漏掉 `--hidden-import` 的话每个入口都
> `ModuleNotFoundError`（2026-10-17 实测同类的后果是 9 个标签页坏 5 个）。已删除，只留脚本。

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
| 7 个标签页各自一套实现 | **十五个页面**，每页都起子进程调同一个引擎入口（界面里不放文档逻辑） |

**功能已对齐**：mac 版的每一项，本壳都有一页，且都起子进程调同一个引擎入口
（2026-10-15/16 分三批搬完：合并文件、删除临时文件、编译全部、一校核查、检查tex信息、
生成目录；第 132 条补齐引用库 5 页）。
**没有任何一页在 Windows 侧另写逻辑** —— 这是"两个平台算出来的结果一致"的唯一保证。

## 测试

```bat
cd sources-win
set PYTHONPATH=..\Engine\vendor
python tests\test_bridge.py
```

`tests/test_bridge.py` 覆盖：引擎/解释器定位顺序、命令行与契约一致、
**密钥绝不进 argv**、UTF-8 与无缓冲环境、配置读写（**POSIX 上权限 0600；Windows 上
没有 POSIX 权限位，靠 `%APPDATA%` 的用户 ACL —— 见 `settings.py` 抬头**）、
真跑一次合并（收到实时日志与契约行）、
以及**真开一次窗口**（控件参数写错只有那一步抓得到；没有显示器时自动 SKIP）。
（本机 mac 上 100+ 项断言，含引用库 CLI 的临时库冒烟；Windows 上还会多跑 `(d)` 的配置目录分支。）

## 归属声明（MIT）

界面骨架与部分交互取自 chenphilo 的 `Editorialbox`：

```text
MIT License

Copyright (c) 2024 chenphilo
```

本目录下 `main.py` 的 Tkinter 布局与中文交互说明沿用该项目的设计；
`engine.py` / `settings.py` / `tests/` 为本项目新写。完整第三方声明见仓库根的
`THIRD_PARTY.md`。
