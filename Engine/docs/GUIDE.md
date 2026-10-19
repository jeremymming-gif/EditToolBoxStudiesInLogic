# 使用与运维指南（GUIDE）

> 本文件是 `README.md` 的**细节层**：用法、依赖、GUI 各页、目录结构、已知限制、合并记录、
> 下一步。README 只回答「这是什么 / 先读哪份」。
>
> **来源**：由原 `README.md`（sha256 `e5352181f303…`）的 §〇/§二/§三/§五/§六/§六·五/§七/§八/§九
> **整节逐字搬运**而来（不改写、不删减），只做了三件小事：① 加本段说明与下面两张速查表；
> ② 标题层级保持原样（便于与历史记录对照）；③ 三处**指回本文件的**交叉引用改写与一处陈旧措辞更正
> （§九末尾的 task-7 排期句 —— task-7 已清账，见 `docs/cases/31-61.md` 第 45 条）。
> 原 §一「实测结果」与 §四/§六·五 的案例条目分别搬去了 [`RULES.md`](RULES.md) 与
> [`cases/`](cases/01-30.md)（案例法），本文件不再重复。
>
> ⚠️ **两条红线**（原文在下面「三、用法」末尾）：**原稿只读**；**稿件仓不做任何 git 写操作**
> （工具箱仓与笔记仓的提交口径另见仓库根 `AGENTS.md` 红线 3）。

## GUI 各页在干什么（速查，15 页）

App（`dist/编辑部工具箱.app`）主窗口是「**左侧分组侧边栏 + 右侧当前功能页 + 下方运行日志**」
（`sources-mac/Views/MainWindow.swift`，Windows 版同一套结构）。侧边栏按用途分三组——
「单篇稿件」「整期装配」「引用库」——外加一个「设置」：「初排转换」是每篇稿子的第一步，「文献回读校验」是它的直接后续
（拿转换时生成的《文献核实任务书》改完 `.bib` 交回来），「本期出刊」是整期装配的一键入口；
「设置」是配置项、不属于流程某一步，所以单独放组外底部。面向编辑的操作步骤见 `使用说明.md`。

> **日志两层（2026-10-17，两平台同一套判据）**：运行日志默认「摘要」（只留阶段/结果/关键行），
> 可切「原始」看引擎逐行输出；「复制全部 / 保存日志」始终给完整日志。
> 判据在 mac 的 `LogStore.isSummaryLine` 与 Windows 的 `engine.is_summary_line`，
> 两侧跑同一份夹具 `tools/log_cases.json`（Swift 测试台 + 桥梁测试各一份断言，防止词表分叉）。

> **运行中可取消（2026-10-17，两平台语义一致）**
>
> mac 版：任务跑起来后主按钮变成**「取消」**；Windows 版：主按钮旁是**「停止」**（`Esc`）。
> 两者都停**整棵进程树**（Python 引擎 + 它起的 `xelatex`），已写出的产物保留；
> 取消**不是失败**：横幅显示「⏹ 已取消」，日志不再补「失败」话术。

| 页面 | 干什么 | 细节/权威实现 |
|---|---|---|
| 初排转换 | 选源文件 → 出初排稿（本文件「三、用法」的 GUI 形态）；第 58 批起两壳「生成参考文献核实任务书」**默认勾选**（会话态、非持久化；CLI `--verify-refs` 默认仍关），初排**默认只读比对引用库**（日志一行，见 §3.10）；勾「用 AI 处理参考文献」且「设置」页配好端点时，**AI 返回的条目会按四条安全校验并入 `reference.bib`**（回滚副本 `reference.bib.ai前`，见 §3.5）。**能不能联网核实**由「设置」页的「通道 / 联网检索」决定，并写进任务书头部与「六、合并记录」（§3.7）。⚠ 勾上它 → **自动勾上并锁定**「生成参考文献核实任务书」（AI 建议与「六、合并记录」都写在那份任务书里，不生成就没有审阅材料）；想只要任务书、不动 bib，勾「**保留原 reference.bib（不并入 AI 结果）**」（= `--no-ai-merge`，只在勾了 AI 时显示）。第 55 批起另有**默认关闭**的「AI 协同初排（两段式）」：第一段只写暂存目录 + 任务书 → 导入外部 chat 回复 → 第二段才交付（见 §3.1「两段式 AI 协同初排」）；第 56 批起回填支持**多文件/目录/剪贴板**一起导入、第二段沿用页面「清理编译中间文件」（`--clean`），摘要按「文献回填 x/y｜低置信回复 a」显示、0 回填有橙色警告 | `sources-mac/Views/ConvertTab.swift`、`sources-mac/Features/ConvertFeature.swift`、`sources-mac/Features/CoopFeature.swift`、第 64 条 |
| 文献回读校验 | 编辑交回的 `.bib` 做离线体检（语法/规范/引用闭合/与原件 diff，**不联网核实真伪**）；可填「报告输出（可选）」= 文件路径或已存在的目录（留空 = 自动落点，行为不变）；**英文作者未倒装算问题**、中文译著缺「（著）/（译）」算提示（第 63 条）。体检成功后出现「**并回**」按钮：把交回的 `.bib` 并进「原件 .bib」那一栏指定的产物 `reference.bib`（与自动通道同一份合并核心；原文件先备份为 `.ai前`），并回后可「**重新编译该篇**」。报告与「初排转换」并入后自检的报告是**同一份实现**（第 64 条） | `checkbib.py`、`slcn/bibreport.py`、`slcn/aibib.py`、第 55/57/63/64 条 |
| 一校核查 | 选初排稿 `.tex`，按 26 条规则逐条核查（脚注/引用位置、半角标点、图表引用间隔、英文弯引号…），默认只报不改；可勾「顺手把无歧义的改掉」（另存新文件，输入文件不动）。报告写稿件同目录 `<名字>_一校核查.md` | `sources-mac/Features/ProofCheckFeature.swift`、`Engine/slcn/proofcheck.py`、`Engine/docs/PROOF_RULES.md` |
| 合并文件 | 把各篇合成一期（扉页 → 编委页 → 中英目录 → 各篇 → 封底，重建书签） | `sources-mac/Features/MergeFeature.swift` |
| 检查 tex 信息 | 查 tex 里编辑关心的字段是否齐（并核对 PDF 实际页数） | `sources-mac/Features/InfoCheckFeature.swift` |
| 编译全部 | 对一期所有稿子跑 xelatex（进度 + 失败清单） | `sources-mac/Features/CompileFeature.swift` |
| 生成目录 | 按模板继承生成中英文目录（条目断行/栏目行在引擎侧） | `sources-mac/Features/TocFeature.swift`、`Engine/slcn/toc.py`、`Engine/slcn/toctemplate.py` |
| 删除临时文件 | 清编译中间文件（与 `slcn/clean.py` 逐项一致） | `sources-mac/Features/DeleteTempFeature.swift` |
| 本期出刊 | 一个入口按顺序跑四步（检查tex信息 → 生成目录 → 合并整期 → 编译全部）；失败即中止，退出码 1 = 橙色警告并继续（规则在引擎侧） | `sources-mac/Features/PublishFeature.swift`、`Engine/publish.py` |
| 库概况（引用库） | 引用库体检：条目数 / 已核实占比 / 重复 key / 字段冲突 / 缺来源 / 清单文件状态；数字来自 `bibdb.py stats` 的 JSON，界面不自己算 | `sources-mac/Views/BibDbStatsView.swift`、`sources-win/bibdb_ui.py`、`Engine/bibdb.py`，第 132 条 |
| 查库（引用库） | 按 key / DOI / 题名 / 作者+年 查已沉淀的已核实条目（命中即跳过重复检索的判据就在这里）；结果只读，不改库 | `sources-mac/Views/BibDbQueryView.swift`、`sources-win/bibdb_ui.py`、`Engine/bibdb.py`，第 132 条 |
| 入藏（引用库） | 把一份成品 `.bib` 或一整期目录导进编辑部引用库：先预览、确认才写库（单篇按「往期成品 bib」入已核实）；写库后提示重算待裁决清单 | `sources-mac/Views/BibDbImportView.swift`、`sources-win/bibdb_ui.py`、`Engine/bibdb.py`，第 132 条 |
| 待裁决（引用库） | 冲突裁决清单：逐条 keep / defer / adopt（`adopt` 必须用指纹、重叠冲突默认拒绝）；裁决结果落库，清单是给人看的文件 | `sources-mac/Views/BibDbReviewView.swift`、`sources-win/bibdb_ui.py`、`Engine/bibdb.py`，第 132 条 |
| 引用库设置（引用库） | 引用库路径 / 数据库位置 / 规范化（B1/B2/B3）的 dry-run 与 apply 入口；本机偏好写 `settings.json`，**只有 dry-run 默认安全** | `sources-mac/Views/BibDbSettingsView.swift`、`sources-win/bibdb_ui.py`、`Engine/bibdb.py`，第 132 条 |
| 设置 | AI 端点配置（明文存本机用户目录）+ 请求旋钮（思考模式 / 输出上限 / **通道** / **联网检索**）+ 体检（①②③④ + **⑤ 检索探针**）+ 隐私文案；**第 55 批新增「单篇预算 / 单批预算」token 预算闸**（默认 30000 / 1000000，0 = 不限，留空 = 不传；只约束直连 API，外部 chat 两段式不生效）；「保存」才落盘、「清空」只清输入框（见下表后说明）。保存/测试按钮**固定在本页底部**、不需要滚到页面末尾 | `sources-mac/Views/SettingsTab.swift`、`sources-win/settings.py`，第 47/58/62/69 条 |

> **「保存」与「清空」的语义（2026-10-15 用户实测报的 bug，见第 62 条）**
>
> - **「保存」= 唯一的落盘动作**：把输入框里的 API 地址 / 密钥 / 模型名 + 两个旋钮（思考模式、输出上限）
>   写进 `~/Library/Application Support/cn.edu.sysu.logic.toolbox/ai.json`（文件 0600、目录 0700）。
>   不点保存，磁盘上那份配置一个字都不会变。
> - **「清空」= 只清界面上的五个输入框**（顺带把预设打回「自定义」、清掉上一次体检结果），
>   磁盘上**已保存**的配置**一个字节都不动**。清空后状态行会写明
>   「已清空输入框；磁盘上已保存的配置**没动**（要覆盖请点「保存」）。」，右边亮「● 有未保存的修改」。
> - 想**真正**抹掉配置：清空之后再点一次「保存」（空框上点也行）。
> - ⚠️ **清空只动内存里这份界面状态**（`AppSettings` 是 App 启动时建的那一个对象），
>   所以清空后**既不点保存、也不重启**时，设置页显示的是空框 —— 那是内存里的一份，盘上那份没丢。
>   要让盘上的配置回到界面：**退出 App（⌘Q）再打开**（启动时 `loadAI()` 读回），或者把五个框重填一遍；
>   在页面之间切来切去**不会**重新读盘。
> - 清空后到重启前，若去「初排转换」勾着 AI 开关跑稿子，用的是界面上**现取**的空值
>   （第 58 条口径）→ 只会生成《文献核实任务书》、不会调用 AI；重启后盘上那份配置照旧生效。

## 常见问题与报错对照（速查）

| 症状 / 报错 | 真正的原因与处置 | 原文位置 |
|---|---|---|
| `File 'SLCN-MIX.sty' not found` | 换 sty 时骨架里的 `\usepackage` 行没跟着改；`slcn/templates.py: sync_package_line()` 会自动改写 | 「二、模板选择」 |
| 「目标文件夹已存在」拒绝出稿 | 目录里有**不属于本工具**的文件（更可能是编辑部已有稿件目录）。确认无误会再 `--force` | 「3.1 正式工具」 |
| `ImportError: cannot import name 'etree' from 'lxml'` | vendor 里是给另一个 Python 版本编译的 lxml；挂载必须用 `append()` 而不是 `insert(0)` | 「六·五」并入过程① |
| `ValueError: badly formed help string` | `argparse` 的 help 串里 `%` 没转义成 `%%`（Python 3.14 直接起不来） | 「六·五」并入过程③ |
| 拿不到作者名时报错退出 | **刻意的**：不给作者就不猜，用 `--author` / `--name` 指定 | 「3.2」命名规则 |
| 编译报错 | `.log` 是**故意不删**的（`--clean` 也保留），先看它 | 「3.2」输出目录规范 |
| 矢量图形变占位符 | 矢量形状（非内嵌图片）只能检出 + 占位 + 导出原稿 PDF，**需人工对照截图** | 「六、已知限制」 |

## 〇、本工具在编辑流程中的位置（先读这一段）

编辑部的工作按排校手册分为多个环节，**本工具只服务其中的「初排」环节**：

| 环节 | 谁在做 | 本工具的角色 |
|---|---|---|
| 分配稿件 → **初排** | 编辑用本工具 | ✅ **生成 `Bo_Chen/Bo_Chen.tex` + `Bo_Chen/original/`** |
| 一校 / 二校 / 三校 / 四校 | 人工 | ❌ 不参与 |
| 做中英文目录（后两次校对时） | 人工 + 工具箱 | ❌ 不参与 |
| **确定目录顺序后加序号前缀** | **人工** | ❌ **不参与** |
| 蓝样校对 / 交付印刷 | 人工 | ❌ 不参与 |

**⚠️ 最常见的误解：目录名的数字前缀不归本工具管。**

```text
初排阶段    →  Bo_Chen/Bo_Chen.tex        ← 本工具的产出，无前缀
              Bo_Chen/original/          ← 源稿归档
三校/蓝样后 →  10 Bo_Chen/Bo_Chen.tex     ← 人工改名（陈波排目录第 10 篇）
```

序号要等**目录顺序定下来**才有意义，而那是在三校或蓝样校对阶段才确定的，
所以初排时不可能、也不应该预先填。仓库根 `README.md` 记录的工作流与此一致：
初排阶段新建的文件夹就是 `Ming_Xiao/`，没有前缀。

---


---

## 二、模板选择（用户可指定）

生成时采用的模板**可由用户选择**，默认按稿件语种决定：

| 语种 | 版面样式（默认） | 骨架（默认） |
|---|---|---|
| 中文稿件 | `编辑笔记/模板文件/SLCN-MIX.sty` | `编辑笔记/模板文件/CN_template.tex` |
| 英文稿件 | `编辑笔记/模板文件/SLEN.sty` | `编辑笔记/模板文件/EN_template.tex` |

```bash
python3 run_poc.py --list-templates              # 列出可用模板
python3 run_poc.py --only tex                    # 用默认模板
python3 run_poc.py --sty SLCN.sty                # 换成历史版本样式
python3 run_poc.py --sty ./my.sty --skeleton ./my_template.tex   # 完全自定义
python3 corpus.py --sty SLEN.sty                 # 全库回归时统一覆盖
```

**⚠️ 一个必须注意的坑**：骨架 tex 里写死了 `\usepackage{SLCN-MIX}`（CN）或
`\usepackage{SLEN}`（EN）。用户换成别的 sty 时，若 `\usepackage` 不跟着改，
编译会失败，而报错（`File 'SLCN-MIX.sty' not found`）离真正原因很远。
所以 `slcn/templates.py: sync_package_line()` 会在选定 sty 后**自动改写该行**。

> 实测确认：英文文章的成品用 `\usepackage{SLEN}`、目录里放 `SLEN.sty`。
> 早期 PoC 给英文文章复制的是 `SLCN-MIX.sty`，是错的，已修正。

---


---

## 三、用法

### 3.1 正式工具：`convert.py`

**源文件必须显式提供**（`--source` 必填），工具不做任何自动发现，也不替用户默认填充。
输出目录按作者姓名规范命名。

```bash
cd Engine
PY=/Users/zhangwenming/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3

# Word 文稿：陈波 → 生成 Bo_Chen/Bo_Chen.tex
$PY convert.py \
    --source "/…/10 Bo_Chen/original/陈波 当代逻辑哲学鸟瞰.docx" \
    --out    "/…/2026年第5期(90)" \
    --compile

# LaTeX 源文件：多源合并（正文来自新稿 + 元数据来自上一轮模板稿）
$PY convert.py \
    --source   "/…/03雅辛之谜（定稿）/main.tex" \
    --metadata "/…/03雅辛之谜（定稿）/yalcin puzzle.tex" \
    --out      "/…/2026年第5期(90)" \
    --compile

# 先看会发生什么，不落盘
$PY convert.py --source X.docx --out Y --dry-run
```

#### LaTeX 通道补充说明（第 120 条，2026-10-17 基于期 89 五篇真实 LaTeX 来稿）

作者交来的 `.tex` 可以是**纯正文稿**（配合 `--metadata` 给上一轮模板稿），也可以是
**完整模板稿**（正文、元数据、前言依赖都在同一份文件里）；引擎在 ①～⑦ 里多做三件事：

1. **多文件正文自动展开**：正文里的 `\input{...}` / `\include{...}` 会递归读入（找不到的
   原样保留并在编译时报错）；日志写「已展开 N 处」。这种情况**源文件所在整个目录**会一起
   归档到 `original/`（等价 `--copy-dir`，多文件稿只归档主文件会丢分节稿）。
2. **前言依赖迁移**：从作者稿前言（`--metadata` 给了旧模板稿时取旧模板稿的前言）搬走
   模板没有的东西——`\usepackage`（模板已加载的同名包不重复、`ctex`/`fontspec`/`geometry`/
   `hyperref`/`biblatex`/本刊 sty 等模板管理项不搬；带选项的包保留选项）、
   `\usetikzlibrary{...}`、`\newtheorem` / `\newenvironment`（按环境名去重）、
   `\DeclareMathOperator`、`\newcolumntype`、`\definecolor`。另外**正文用到的常见符号宏**
   会按内置对照补包（`\llbracket`/`\rrbracket` → `stmaryrd`、`\mathscr` → `mathrsfs`、
   `\multirow` → `multirow`、`\bm` → `bm`、`\textcolor` → `xcolor`…；只补符号类，
   不碰版面级宏包）。日志会列出「前言依赖迁移：宏包 N 个…」与「模板已提供、未重复搬：…」。
3. **自定义命令按正文可达搬 + 冲突自动改名**：只搬**正文真的用到**的 `\newcommand`
   （传递闭包：A 用到 B 就 B 也搬；正文没用到的不搬——实测 07 Yiyan_Wang 的 `\ar` 就是
   这样避开 xy 宏包冲突的）；与模板/宏包已定义名字冲突的（如模板 `\newtheorem{prop}`
   占住 `\prop`）自动改名（`\prop → \slcnprop`）并**正文与定义同步改**，
  日志与「⚠ 注意事项」里明写改名记录。

**边界（如实说清）**：这是**纯离线判据**，不解析 TeX 发行版里宏包自带的定义。若作者前言
**重定义了宏包已提供的命令**且正文确实用到（不是 `\prop` 这种模板文本能看到的），仍可能报
`already defined`——这种情况日志会保留原始 TeX 报错，由编辑手工处理。源目录没有 `.bib`
而正文有 `\cite` 时，会明确警告「未生成 reference.bib（需作者补交参考文献库）」，不再静默。

**第 121 条补丁（2026-10-17，期 88 七篇英文来稿实测）**：
- `\bibliographystyle` 也当成参考文献区起点截掉（03 Zhentao_Zhang：作者写在
  `\begin{document}` 之后，残留会与模板 biblatex 冲突 `'\bibliographystyle' invalid for 'biblatex'`）。
- 宏解析支持**无花括号**写法 `\newcommand\revieww[1]{…}` 与 `\newcommand*`（04 Chen_Liu）；
- 正文 `\includegraphics` 引用到的图片会从**源文件所在目录**（含 `\graphicspath`）复制进产物目录，
  保持相对路径；找不到的记「⚠ 正文引用的图片找不到，未复制」交给编辑（04 Chen_Liu 的 `Archit.pdf`/`Figs/*`）；
- 英文规则 `en-ellipsis` 只在「`...` 后紧跟字母」时输出 `\ldots{}` 做隔离，其余形态逐字节不变
  （05 YouAn_Su：源稿字面 `...x_n` 以前被粘成 `\ldotsx_n` → 未定义命令）；
- 重跑自己的产物目录时，`<产物>.<时间戳>.bak` 视为本工具产物，不再需要 `--force`。

**第 130 条加固（2026-10-18，定义"放在哪"都兼容）**：作者的定义不一定在 `.tex` 导言区：

- **本地支撑文件**：同目录里被前言的 `\usepackage{...}` 引用的旧 `.sty`、`\documentclass{本地类}`
  对应的 `.cls`、以及这些文件里再 `\input{...}` / `\RequirePackage{...}` 的同目录 `.def` / `.cfg`，
  都会按「**正文真正用到 + 模板没有**」迁移——五类声明（`\newtheorem` / `\newenvironment` /
  `\DeclareMathOperator` / `\newcolumntype` / `\definecolor`）之外，**普通宏**
  （`\newcommand` / `\renewcommand` / `\providecommand`）也搬（第 127 条只处理定理环境）。
  日志会写「本地支撑文件：…」与「本地旧模板定义迁入 N 个：…」。
- **本地非标准命名的旧模板 `.sty`**（如 `SLCN2.sty`）：若作者源目录里就有 `\usepackage{名字}`
  对应的 `名字.sty`，**不迁这一行 `\usepackage`**（顶部加载交给现行模板），文件按「本地支撑文件」
  差集搬定义——否则产物会引用一个没被复制进产物目录的文件（`File … not found`）。日志写
  「本地旧模板 .sty（源目录有同名文件，包行不迁、按差集搬定义）：…」。
- **`\newcounter{X}` 依赖迁移**（第 130 条补丁）：只有「正文或已迁移定义体用到」才搬
  （`\theX` / `\value{X}` / `\setcounter{X}` / `\stepcounter{X}` / `\arabic{X}` 等形态；
  `\newcounter` 的名字不是控制序列）。计数器统一排在定义块之前；`\newcounter{X}[Y]`
  的父计数器一起搬并排在 X 之前。日志写「计数器迁移 N 个：…」。
- 前言里 `\input{*.sty}` / `\input{*.def}` / `\input{*.cfg}` 会先由多文件展开读入
  （与 `.tex` 分节稿同一套机制），其中的 `\newcommand` 照旧按正文可达迁移。
- **`\renewcommand` 覆盖模板命令**：模板优先、不搬，但日志与「⚠ 注意事项」会**明写**
  哪些命令被跳过（不再静默改变语义）；新模板里**没有**对应命令的 `\renewcommand`
  降为 `\newcommand` 迁入并警告（请人工核对语义）。
- **正文区定义**（`\begin{document}` 之后）：**不迁移**（正文整段照搬已保留），只打一行
  「正文里出现 N 处自定义定义（保留原样，建议移到导言区）」。
- **定义缺失预检**：编译前把正文用到的 `\begin{env}` 与「模板 + 迁移结果 + 正文自身定义」
  核对，缺失的环境打醒目警告（同时进「⚠ 注意事项」），但**不阻断出稿**（退出码不变）。

边界（如实说清）：纯离线**看不到 TeX 发行版/宏包内部定义**，所以预检对宏包自带的环境
（不在内置 `BASE_ENVIRONMENTS` 白名单里的）可能误报——它只是提示，最终以编译报错为准；
`\def` / `\let` / `\newif` / `\renewenvironment` 这类定义**不搬**（改语义风险高）；
`\newcounter` 只在「正文或已迁定义体用到」时搬（没用到的不搬；跨「作者前言 ↔ 本地 sty」
两组的计数器依赖不闭合）；本地非标准 `.sty` 的包行不迁后，该 sty 里 `\def` 类定义与它自己
`\usepackage` 的宏包不会跟着走（只按文本差集搬五类+宏）；只读**同目录**支撑文件，不查
TeX 发行版；没有 `\begin{document}` 的纯正文稿无法区分前言/正文，沿用旧行为（整份按正文
可达收宏，见第 120 条）。

#### C 类新口径：脚注分类 + 并集参考文献（第 52 批，2026-10-19 默认开启）

C 类口径从第 52 批起**默认开启**（v9b 41 篇全量 PASS 后用户拍板），只影响 **Word 通道**；
LaTeX 通道不变。开关解析链：`convert.py` → `build.build_docx(c_class_union=None)` →
`slcn.footnotes.c_class_union_enabled()` —— None 取总开关 `C_CLASS_UNION_DEFAULT=True`，
显式 False（`--no-c-class-union`）回退旧行为（v8 基线逐字节不变）。低层 `parse_docx()` 默认仍
False：`docx_audit` 的冻结基线不随总开关变。

- **四类分类**（`classify_citation()`）：`pure_citation` 纯引用 → 正文 `\parencite[页码]{键}`、
  删脚注；`reference_entry` 完整文献条目 → 删脚注 + 正文 parencite，条目进并集 bib；
  `mixed` 说明+引用 → 保留脚注 + `mixed_manual_check` warning（build 汇总成「请人工核对」清单）；
  `note_only` 纯说明/古典文献 → 原样保留。`pure_citation` 匹配不到完整条目 →
  `citation_unmatched`：保留脚注、**不编造**，出 warning。
- **并集 bib**（`union_entries()`）：文末清单 ∪ 脚注条目；指纹 = 第一作者姓 + 题名 + 4 位年份 +
  类型族 + 卷号 + 版次（页码/定位不参与），同作品仅页码不同合并 1 条、正文各处
  `\parencite[页码]`；文末版本优先、缺字段互补，字段冲突记 `field_conflict` warning；并集改键
  通过 keymap 回写正文，避免 undef/dangling。
- **验收（第 52 批 v9b）**：41/41 rc=0、compile_ok、undef/dangling 与 v8 相同、P0 集合无新增；
  专项验证纯引用→parencite、混合脚注保留 + warning、并集 bib 去重、无脚注稿零变化。
- **回退**：命令行 `--no-c-class-union`；GUI 无开关，默认即新口径。

#### 两段式 AI 协同初排（第五十四～五十七批；外部 chat 免费流程）

编辑用自己的 chat 模型（豆包/千问/DeepSeek 等）核实任务书；**软件本身不上传任何内容、不调用 API、
不产生费用**。只有走 `--llm-*` 直连 API 通道时才有 token 预算闸（见下）。

```bash
# 第一段（ph1）：只写暂存目录 + 任务书，最终交付目录本次不写；不调用任何 API
$PY convert.py --source "<源稿.docx>" --out "<期目录>" \
    --verify-refs --no-ai-merge --coop-taskbook-only
# → <期目录>/<名_姓>_待核/：初排产物 + <名>_文献核实.md
#    任务书末尾含「附一、初排低置信项」与「附二、回填说明」（第 56 批）
#    编辑把任务书交给自己的 chat 模型；回复存成 .md/.txt/.bib/.json（单文件/目录/多文件均可）

# 第二段（ph2）：校验回填指纹 → 并入文献字段 → 低置信建议 →（--coop-apply）→ 编译 →（--clean）→ 原子交付
# 第 56 批：--coop-replies 可重复（chat 把 JSON 与 BibTeX 拆成两个文件时两块都传）
$PY convert.py --source "<源稿.docx>" --out "<期目录>" \
    --coop-replies "<回填A>" [--coop-replies "<回填B>" …] [--coop-apply] [--compile] [--clean]
# → 最终目录 + _AI协同记录.md；--compile 才编译；--coop-apply 才改 reference.bib 与输出 .tex
```

- **ph1**：初排产物、任务书、`_AI协同状态.json` 只写暂存目录（默认 `<out>/<名>_待核/`，
  `--coop-stage` 可改）；**最终交付目录不写、不打印交付成功**。任务书末尾多「附一、初排低置信项」
  机器可读节（ID `FN-…` / `BIB-…` / `META-…`；第 57 批起 FN 纯引用项给 `match_key` 白名单、
  META 项记目标 `\my*` 宏名，旧状态缺映射的只记录）；第 56 批起还多「附二、回填说明」——把回填
  **=完整 BibTeX（一个代码块）+ 附一 JSON 两部分、都要交**写死，并说明 chat 拆成多文件也可一起导入。
  协同任务书里第三节第 10 条限定为「**文献条目部分**只输出 bibtex 代码块」，附一 JSON 按附一格式
  单独回复（非协同任务书逐字节不变）。隐含 `--verify-refs`；与 `--no-ai-merge` 组合时
  即使配了 LLM 也**不并入、不自动调 API**，只出任务书。
- **ph2**：`--source` 仍**必填**（重算参数用）；从暂存目录读 `_AI协同状态.json` 校验**指纹**
  （源稿/参数变了拒绝继续，退出 2、产物不动）；解析回填（文献键回复用 `@type{键, …}` bibtex 块；
  低置信项按任务书 JSON 格式，同一 ID 可分字段多条）→ 并入 `reference.bib` → 低置信建议默认只写
  `<名>_AI建议.md/.jsonl`（第 57 批起 accepted 行带 `target`）；`--coop-apply` 时才再应用
  META/FN 建议到输出 `.tex`（见下条）；编译在 promote 之前（需 `--compile`）；**原子交付**（旧目录先备份、
  失败不破坏既有产物）；成功后写 `_AI协同记录.md`（回填/并入/拒绝/待人工计数）。
  **第 56 批起**：① `--coop-replies` **可重复**（按命令行顺序读入、同一真实路径去重；多目录时显示名
  带目录前缀；**只传一个路径的旧行为逐字节不变**）；② `--clean` 生效——编译后先按 `slcn.clean`
  清 `.aux/.bbl/.bcf/.blg/.out/.run.xml` 再 promote，`.log/.tex/.bib/.pdf/状态/记录/建议/.ai前` 保留；
  ③ 状态 JSON 增 `stats`（7 键：`bib_total/bib_replied/bib_pending/low_total/low_replied/low_applied/
  low_pending_manual`，**schema 仍是 1**，旧状态无此节 → 外壳回退估算）；④ 解析后若「显式给了回填、
  但一条文献条目都没确认」→ 日志 `⚠⚠⚠` 醒目 warning（可能漏导入 bibtex/bib），并写 `stats` 警示口径；
  ⑤ 最终摘要统一为「回填：x 条（未回填 y 条）；低置信回复：z 条（可应用 a / 仅记录待人工 b）」。
- **`--coop-apply`（第 57 批起三类应用）**：只把**通过校验**的低置信建议也应用到产物——
  ① `BIB-*`（`corrected` 的文献字段）→ `reference.bib`，先备份 `reference.bib.ai前`，沿用
  `aibib.merge()` 硬保障（键不变 / 恰好一条 / 不增删条目）；
  ② `META-*`（`corrected`）→ 输出 `.tex` 的 `\my*` 字段：只改编辑侧出版字段白名单 ∪ 初排记录的
  「未识别字段→宏」；编辑侧已有值、或初排后产物被改过的不覆盖（只填空/模板占位），值过格式校验
  （非空、无换行/花括号/反斜杠；卷/期/年/页/日期须符合数字格式）；
  ③ `FN-*` 窄口径：仅 `citation_unmatched` 纯引用脚注 + 回复 `field=match_key` + 键在任务书与当前
  `reference.bib` 里 → 用原文片段锚点**唯一命中** tex 里的 `\footnote{…}`，提升为
  `\parencite[loc]{key}`（原文含页码线索时必须给 `locator`，不丢页码）。
  有写入才把输出 `.tex` 备份成 `<名>.tex.ai前`（0 条不写盘、不留备份）。其余（`mixed_manual` /
  `FN-W` / `note` / `BIB-LINE` / `confirmed` / `unresolved`）只列清单待人工。**不加此开关，
  产物不因低置信建议改变**。若同时给 `--no-ai-merge`，`--coop-apply` 不生效（只记录建议）。
- **失败边界**：回填全部无效、指纹不符、编译失败 → 非 0 退出，最终交付目录**保持原样**。
- **预算闸只约束直连 API 通道**（`--llm-*` 自动预跑）：`--ai-budget-tokens`（单篇，默认 30000）/
  `--ai-batch-budget-tokens`（单批，默认 1000000），0 = 不限；超预计用量先打印账、退出 2、
  不发起任何请求。**外部 chat 回填零费用**。
- **GUI 接入（第 55–56 批，mac / Windows 两壳对等）**：「初排转换」页新增**默认关闭**的
  「AI 协同初排（两段式；外部 chat 免费流程）」勾选（mac：`sources-mac/Views/ConvertTab.swift` +
  `sources-mac/Features/CoopFeature.swift`；Win：`sources-win/main.py` + `sources-win/coop.py`）。
  勾上后出现说明与状态区：第一段跑完状态 **「待人工核实（未交付）」**，按钮「打开任务书」「打开暂存目录」；
  第 56 批起回填区改为**已选清单**：mac「添加文件…／添加目录…／从剪贴板导入」+ 每项「移除」+「全部清除」，
  Win「添加回填文件…／添加回填目录…／从剪贴板导入」+「移除选中」+「全部清除」——多文件 / 目录 / 剪贴板
  可混合、可累加，每个路径按已选顺序各传一个 `--coop-replies`（**不再折叠父目录**；剪贴板落临时 `.md`
  草稿，移除时一并删除、不碰用户选的文件）。之后勾「一键修改产物（--coop-apply；默认不勾）」再点
  「运行第二段」（Win 按钮「运行第二段…」；**两壳都是先加清单、再运行**）。成功后状态变 **「已交付」**，
  摘要显示「**文献回填 x/y（并入 z 条，未回填 n）｜低置信回复 a（应用 b / 仅记录待人工 c）**」，
  可打开交付目录 / 协同记录 / 建议清单；**文献回填 0/y 时橙色警告**（可能漏导入 bibtex/bib）；
  旧状态无 `stats` 时注明「部分计数为估算」；失败显示**退出码 + 日志尾部**。页面的
  「清理编译中间文件」在第二段同样生效（→ `--clean`）。界面只**扫描暂存目录**读
  `_AI协同状态.json` 得出状态（按钮可见性全由 `phase` 决定），判据仍只有引擎一份，两壳不重写。
- **「设置」页 token 预算（第 55 批）**：「单篇预算 / 单批预算」默认 30000 / 1000000，0 = 不限，
  留空 = 不传（引擎用默认）；两壳只在值 ≠ 默认时才拼 `--ai-budget-tokens` /
  `--ai-batch-budget-tokens`，**没动过预算的用户命令行与批 54 逐字节相同**；只约束直连 API，
  外部 chat 两段式零费用不生效。合法值写本机设置（mac 逐项立即落 UserDefaults、无保存按钮；
  Windows 点「保存」写 settings.json），非法值不落盘也不传给引擎。隐私文案同步写明「两段式零上传」。
- **GUI 限制（如实，不夸大）**：① 建议清单 / 建议摘要**第二段跑完才生成**（`_AI建议.md/.jsonl`）；
  ② `--coop-apply` 的决定**必须在点「运行第二段」之前**做——`phase=done` 后同一回填重跑是**幂等早退**，
  回头再勾无效，需重跑第一段（两壳都是「先加回填清单 → 勾 apply → 运行第二段」）；
  ③ `--coop-apply` 应用到产物的是三类：`BIB-*`→`reference.bib`；`META-*`→输出 `.tex` 的 `\my*`；
     `FN-*` 仅 `citation_unmatched` 纯引用脚注→`.tex` 正文 `\parencite`（写前各留 `.ai前`；
     其余 `mixed_manual` / `FN-W` / `BIB-LINE` 及未通过校验项只列建议待人工）；
  ④ GUI **未做任务书自动分片**（多片回填需手动拆分，或多文件 / 目录 / 剪贴板一起导入）；
  ⑤ 第一段不写最终交付目录，**未跑第二段不算交付**；
  ⑥ **回填两部分都要交**（完整 BibTeX + 附一 JSON）：只交一部分 → 文献回填 0/y + 橙色警告，
  别当成功（真实首测就是这么只导入了 JSON）。
- **不加协同开关时旧行为逐字节不变**（默认路径不写暂存目录、不生成 `_AI协同记录.md`）。

| 参数 | 说明 |
|---|---|
| `--source` | **必填**。`.docx` / `.doc` / `.tex` |
| `--out` | **必填**。输出根目录，其下建立 `<作者名>/` |
| `--metadata` | LaTeX 多源合并时的元数据源（上一轮模板稿） |
| `--bib` | 参考文献库；默认取源文件同目录下的 `.bib` |
| `--author` | 第一作者姓名（源里读不到时） |
| `--name` | 直接指定输出名，跳过姓名推导 |
| `--lang` | `zh` / `en`；默认**按题名**自动判断 |
| `--sty` / `--skeleton` | 覆盖模板（见第二节） |
| `--compile` | 生成后立即 xelatex → biber → xelatex×2 |
| `--dry-run` | 只打印计划，不写文件 |
| `--verify-refs` | 生成《参考文献核实任务书》markdown（交给编辑各自的 AI 核实）。**配了 LLM 时**还会自动预跑一遍，并把通过安全校验的条目**并入 `reference.bib`**（见 §3.5） |
| `--no-ai-merge` | 只出任务书，**不**把 AI 结果并入 `reference.bib`（旧行为，逐字节相同）。GUI 里对应「保留原 reference.bib（不并入 AI 结果）」（只在勾了 AI 时显示） |
| `--no-bibdb-verify` | 关闭**初排默认**的只读引用库比对（第 58 批，默认开）：只影响日志一行，`.tex`/`reference.bib`/任务书/argv 不变；无库时本来就静默。**不影响** `--verify-refs` / `--verify-plan` 的核实行为（见 §3.10） |
| `--vol` / `--issue` / `--year` | 卷号 / 期号 / 出版年（**编辑侧**，作者稿里没有） |
| `--pages` | 起止页，如 `137-152` → 自动算 `myfirstpage/mylastpage/mypages`（152-137+1=16） |
| `--received` | 收稿日期，如 `2025-09-19` |
| `--editor` | 责任编辑姓名（自动包成 `{\bf （责任编辑：XXX）}`） |
| `--grants` | 基金项目（写模板里被注释的那行 `%\renewcommand{\mygrants}`） |
| `--llm-base` / `--llm-key` / `--llm-model` | 大模型兜底（也可用同名环境变量 `SLCN_LLM_*`） |
| `--llm-protocol` | 通道：`chat`（默认，`/chat/completions`）/ `responses`（`/responses`，标准 Responses 协议）。也可用环境变量 `SLCN_LLM_PROTOCOL`；**不传 = chat，命令行与从前逐字节相同** |
| `--llm-web-search` | 本次启用**服务端联网检索**（只在 responses 通道生效：加 `tools:[{type:web_search}]`）。也可用环境变量 `SLCN_LLM_WEB_SEARCH=1`。⚠️ 端点是否真的会查要先用探针实测（§3.7） |
| `--llm-search-tool` | responses 通道 `tools[].type` 的**工具名**（默认 `web_search`）。也可用环境变量 `SLCN_LLM_SEARCH_TOOL`。**将来服务商换了名字**（如 `web_search_preview`）改这一项即可（§3.7，第 70 条） |
| `--llm-responses-path` | responses 的**路径**，拼在 base 之后（默认 `/responses`）。也可用环境变量 `SLCN_LLM_RESPONSES_PATH`；缺前导 `/` 会自动补。**默认/不传时 URL 与请求体逐字节不变**（§3.7，第 70 条） |
| `--clean` | 清理编译中间文件，规则与 EditToolBox「删除临时文件」一致；**第 56 批起协同第二段（ph2）同样生效**：编译后先清 `.aux/.bbl/.bcf/.blg/.out/.run.xml` 再原子交付，`.log` 等保留 |
| `--copy-dir` | 连源文件所在整个目录一起归档到 `original/`（多文件 LaTeX 稿自动启用，无需手工加） |
| `--no-original` | 不建立 `original/`（默认建立） |
| `--force` | 目标文件夹已存在时覆盖（默认**拒绝**，防误伤已有稿件目录）。GUI 里对应「高级选项 → 覆盖已存在的输出目录（--force）」 |
| `--c-class-union` | 显式启用 C 类新口径（**默认已开**，一般不用加；脚本里显式写便于自证）。见本节「C 类新口径」 |
| `--no-c-class-union` | **回退旧行为**：脚注文献按旧规则提升、不做并集（产物与 v8 基线逐字节一致）。GUI 无开关，默认即新口径 |
| `--coop-taskbook-only` | **AI 协同初排第一段**：初排产物 + 《AI 核实任务书》只写暂存目录（默认 `<out>/<名>_待核/`），**不写最终交付目录、不调用任何 API**；隐含 `--verify-refs`，任务书末尾加「附一、初排低置信项」；第 56 批起另加「附二、回填说明」（回填=完整 BibTeX + 附一 JSON 两部分），协同任务书第三节第 10 条只限定文献条目部分 |
| `--coop-stage` | 协同初排的暂存目录（默认 `<out>/<名>_待核/`），ph1/ph2 通用；状态文件 `_AI协同状态.json` 写在这里、不随交付进最终目录 |
| `--coop-replies` | **第二段**：读入外部 chat 回填（`.md/.txt/.bib/.json` 等文件或目录，目录按文件名排序）→ 校验指纹 → 并入文献字段 / 写低置信建议 →（`--compile` 时）编译 →（`--clean` 时先清中间文件）→ 原子交付。**第 56 批起可重复**（`--coop-replies A --coop-replies B`，按命令行顺序、同一真实路径去重；只传一个路径行为不变）。回填全无效或指纹不符退出 2、产物不动；`--source` 仍必填 |
| `--coop-apply` | 配合 `--coop-replies`：把**通过校验**的低置信建议也应用到产物——`BIB-*`→`reference.bib`（先备份 `reference.bib.ai前`）、`META-*`→输出 `.tex` 的 `\my*` 元数据（编辑侧已有值不覆盖）、`FN-*` 仅 `citation_unmatched` 纯引用脚注→`.tex` 正文 `\parencite`（`.tex` 写前备份 `<名>.tex.ai前`；0 条不写盘）。其余（`mixed_manual` / `FN-W` / `BIB-LINE`）只列待人工。默认只写 `<名>_AI建议.md/.jsonl`；给 `--no-ai-merge` 时不生效 |
| `--ai-budget-tokens` | 单篇**直连 API 通道** token 预算（默认 30000；0 = 不限）；超预计用量先打印账、退出 2、不发起请求。外部 chat 回填不产生费用 |
| `--ai-batch-budget-tokens` | 单批**直连 API 通道** token 预算（默认 1000000；0 = 不限），行为同上 |

### 开发者用的环境变量（不装工具箱也用不到）

| 变量 | 作用 |
|---|---|
| `SLCN_MANUSCRIPTS` | **稿件库**（各期作者来稿）的位置。重活工具（`docx_audit.py` 全库普查、`corpus.py` 全库回归、`run_poc.py` 代表稿）需要它。不设时自动探测（向上找 → 同级目录，默认两仓并排就命中）；**没有稿件库时这些工具打印一行说明并正常退出（0）** —— 公开仓与 CI 上就是这样。 |
| `SLCN_NOTES` | **私有笔记仓**（案例法 / 交接说明 / 冻结基线）的位置。默认自动探测同级目录 `EditToolBox-notes/`。 |
| `SLCN_AUDIT_OUT` | 全库普查的输出目录（等价于 `--out-dir`）。**并行跑多份普查时必须用它隔离**，否则会互相覆盖同一份 csv/txt。 |
| `SLCN_SOFFICE` | 指定 LibreOffice 可执行文件（「导出原稿 PDF」＋ Word 的 MathType/OLE 公式图 WMF/EMF → PDF 用）。**fail-soft**：找不到时**不报错、不阻断出稿** —— 公式图位置显示「公式图缺失」占位（`\IfFileExists` 兜底）+ 初排日志 warning，「导出原稿 PDF」步骤则跳过并说明；检查/安装见 `使用说明.md` §一。 |

> 前两个只在开发/回归时用得到；编辑日常使用 `.app` / `.exe` 时不需要设任何环境变量。

#### 「目标文件夹已存在」的判定口径（`convert.py: is_own_product()`）

- **算「自己的」**：`<输出名>.tex/.pdf/.bib/.log` 与几份核对报告、`reference.bib`、
  `original/`、任意 `.sty`、`fig1.png` 这类从 docx 提取的图片，以及**编译中间文件**
  （`.aux/.bbl/.bcf/.blg/.out/.run.xml/.synctex.gz`，外加 latexmk 的
  `.fdb_latexmk/.fls/.xdv`）。
- **只有目录里出现别的文件时才拦** —— 那更可能是编辑部已有的稿件目录。
- 2026-10-13 的改动：latexmk 那三样原先不算「自己的」，于是编辑部手工跑过一次
  latexmk 就会让重跑被拦（实测踩到）。改动后**全库 228 个真实稿件目录逐一比对：
  「会被拦下」的目录数一个都没减少**（199 个仍拦；另外 29 个本来就只含工具产物）。
  同一改动也顺手把分类器抽成了模块级函数 `is_own_product()`，便于这样批量核对。

### 3.2 输出目录规范

> 本节讲的是**单篇工具的输出目录**（`<输出目录>/<名_姓>/`）。一期里文章目录怎么编号、
> `000 Table of Contents` / 扉页 / 编委页 / `original/` / `agreement/` 怎么放，
> 编辑侧的权威规范在**稿件仓 README.md** 的「期目录与文件命名规范」一节（案例第 115 条）。

作者「陈波」→ `Bo_Chen/`，其中：

```
Bo_Chen/
├── Bo_Chen.tex      ← 目标 tex
├── Bo_Chen.pdf      ← --compile 才有
├── reference.bib
├── SLCN-MIX.sty     ← 与 --sty 一致
└── original/        ← 源稿归档，命名与往期各期一致
    └── 陈波 当代逻辑哲学鸟瞰.docx
```

`original/` 与仓库现有 92 篇文章的命名完全一致。默认会把本次用到的源文件
（`--source` / `--metadata`）复制进去：

- `--copy-dir` — 连源文件所在**整个目录**一起归档。LaTeX 投稿常是「一个文件夹里
  放 main.tex + 旧稿 + bib + sty + pdf」，往期就是这么整包归档的
  （如 `Liang_Zhang/original/03雅辛之谜（定稿）/`）。
- `--no-original` — 不建立 `original/`。
- 自动去重：整目录已包含的文件不会再平铺复制；源文件本就在 `original/` 内时跳过，
  重跑已有稿件目录不会自己复制自己。

`--clean` 会删掉编译中间文件，让产出目录**与人工成品完全一致**。规则逐项对齐
EditToolBox 的 `DeleteTempFeature.swift`：

| | 文件 |
|---|---|
| 删除 | `.aux` `.bbl` `.bcf` `.blg` `.out` `.run.xml` `.synctex.gz` `.DS_Store` |
| **保留** | `.tex` `.pdf` `.sty` `.bib` **`.log`** |

`.log` 是**故意不删**的——编译出错要靠它排查，工具箱的「删除临时文件」也不删它。
实测效果（03 Liang_Zhang / 10 Bo_Chen）：

| | 人工成品 | 工具产出（`--compile --clean`） |
|---|---|---|
| `10 Bo_Chen` | `.tex .pdf .log .sty .bib original/` | 同上，**零差异** |
| `03 Liang_Zhang` | `.tex .pdf .sty .bib original/` | 多一个 `.log` |

删掉 `.bbl`/`.bcf` 不影响后续重编——已验证再跑一遍
`xelatex → biber → xelatex ×2` 仍得到 18 页、零报错。

#### 审阅件集合 + 交付清单（2026-10-19，task-79 方案 C）

- **审阅件集合**（一篇交付/交接时应齐）：`<名_姓>.tex`、`<名_姓>.pdf`（`--compile` 时）、
  `reference.bib`、模板 `.sty`、`.log`、`original/`；本次生成过的另带 `<名>_文献核实.md`、
  `<名>_一校核查.md`、`<名>_回读校验报告.md`、`<名>_参考文献核对.txt`。
- **保留策略**：沿用上面 `--clean` 删除表——只删编译中间件
  （`.aux .bbl .bcf .blg .out .run.xml .synctex.gz .DS_Store`），保留 `.tex/.pdf/.sty/.bib/.log`
  与 `original/`；**`.log` 不删**（排错要）、**`original/` 不动**（工具只读、永不删）。
- **交付清单**：每篇一份 `交付清单.md`（复制模板：[`DELIVERY-CHECKLIST.md`](DELIVERY-CHECKLIST.md)），
  记录稿件标识、模板/源稿 sha256、审阅件、已生成/已跳过、人工确认勾选（bib 已核 / 一校已处理 /
  图表公式已看 / 三校后编号目录已定）、保留清理状态与备注。**清单只记录：不自动生成、不改变
  任何现有行为**；sha 用 `shasum -a 256 <文件>`（macOS）或 `certutil -hashfile <文件> SHA256`（Windows）。
- 模板原始文件：`编辑笔记/模板文件/SLCN-MIX.sty`（中文默认）/ `SLEN.sty`（英文默认）；记录时可对照
  产物内 `.sty` 的 sha 是否一致。

#### ⚠️ 目录名不带序号前缀——这是刻意的

本工具**只用于初排环节**，输出 `Bo_Chen/`，**不加数字前缀**。

按编辑部流程，序号是在**三校 / 蓝样校对**阶段、文章目录顺序确定之后才由人工加上去的
（例：陈波这篇排在目录第 10 篇 → 那时才把 `Bo_Chen` 改成 `10 Bo_Chen`）。
那一步不属于本工具的职责范围。仓库根 `README.md` 记录的工作流与此一致：
初排阶段新建的文件夹结构就是 `Ming_Xiao/`（无前缀）。

#### 命名规则

命名规则为 **「名_姓」**（`slcn/naming.py`），由仓库 92 篇文章的目录名反推并全量验证：

| 姓名 | 输出名 | 姓名 | 输出名 |
|---|---|---|---|
| 陈波 | `Bo_Chen` | 张亮 | `Liang_Zhang` |
| 寇静茹 | `Jingru_Kou` | 唐莹莹 | `Yingying_Tang` |
| Michael Rathjen | `Michael_Rathjen` | Wen-fang Wang | `Wenfang_Wang` |

- 多作者**只取第一作者**
- 中文取拼音，外文取原名；`Rathjen, Michael`（姓, 名）也能正确解析
- 复姓（欧阳、司马…）与**多音字姓氏**（单 shàn、曾 zēng、薄 bó）有专门处理
- 名字里出现**真歧义**的多音字（朝 cháo/zhāo、长 cháng/zhǎng）时**会提醒确认**，
  不静默猜——如读音不对请用 `--name` 覆盖

**拿不到作者时不猜**：直接报错并要求 `--author` 或 `--name`。例如只给 `main.tex`
（纯正文、无元数据）时会拒绝执行——这是刻意设计。

### 3.3 测试台（会自动发现源文件，仅供回归用）

```bash
# 两条通道各跑一篇 + 打分报告
$PY run_poc.py
python3 run_poc.py --only tex

# 全库回归（68 篇，约 10 分钟）
$PY corpus.py [--sty X.sty]

# 规则消融：单篇版快，全库版准（抽样 30 篇，避免单篇过拟合）
python3 ablation.py
$PY ablation_corpus.py
```

> `run_poc.py` / `corpus.py` 会自动扫描 `original/` 找源文件——这对回归测试是必要的，
> 但**不是给编辑用的入口**。正式使用请用 `convert.py`。

### 3.3.1 引擎测试两档：`quick` / `full`

日常改一行跑 quick（秒级，约 1–2 秒）；改了 `slcn/` 判据、改了产物契约（bib / tex / 报告字段）或发版前，跑 full：

```bash
cd Engine && bash tools/run_engine_tests.sh quick   # 秒级几套：不碰稿件库 / TeX / LibreOffice，也不起长期子进程
cd Engine && bash tools/run_engine_tests.sh full    # 默认档 = 全部 tests/test_*.py；稿件库在本机时约几分钟
```

- 每套一行 `PASS/FAIL` + 它自己的末行摘要；结尾汇总「N 套件，红 M」，**有红退出码非 0**。
- quick 白名单写在脚本注释里；quick 全绿不等于能发版 —— **full 才是回归的完整口径**。
- 解释器优先 `/opt/homebrew/bin/python3.14`，找不到回落 `python3`；`PYTHONPATH` 自动指向 `Engine/vendor`。

### 3.4 依赖

Word 解析需要 `python-docx`（DSH 自带解释器已含），中文姓名转拼音需要 `pypinyin`：

```bash
$PY -m pip install --target vendor pypinyin    # 装在项目自己的 vendor/，不动系统环境
```

`vendor/` 已 gitignore。缺失时工具会给出上面这条命令，不会静默失败。

**可选外部依赖 LibreOffice（`soffice`）**：Word 稿里若用早期 MathType 写公式（OLE 对象 + 预览图 WMF/EMF），
XeLaTeX 不能直接嵌入，引擎用 `soffice --headless --norestore --convert-to pdf` 批量转 PDF 后再插入。
**fail-soft**：找不到 `soffice`（定位顺序 `SLCN_SOFFICE` → PATH → 三平台常见安装位置）时**不报错、不阻断出稿**——
Word 通道渲染成 `\IfFileExists{figN.pdf}{…}{\fbox{\scriptsize 公式图缺失}}` 占位并在初排日志 warning；
「导出原稿 PDF」（矢量形状人工截图用）步骤则跳过并返回说明。LibreOffice **不随工具箱打包/分发**，
检查/安装命令见仓库根的 `使用说明.md` §一「Word 公式图（MathType）与 LibreOffice（可选依赖）」。

产物写在 `out/`（已 gitignore，不会污染稿件目录），**全程只读原稿，不修改任何现有文件**。

### 3.5 AI 结果**并入** `reference.bib`（2026-10-15 用户决定，第 64 条）

> 这一节是**编辑最需要知道的一条**：勾了「用 AI 处理参考文献」（命令行 = `--verify-refs` + 配好
> `--llm-base/--llm-model`）之后，**产物里的 `reference.bib` 会被 AI 的结果直接修改**——
> 不再是「只看建议、自己搬」。

**它做了什么**（只在真的拿到 AI 回复时才发生）：

1. 把**合并前**的 `reference.bib` 复制成 **`reference.bib.ai前`**（回滚副本）；
2. 按 `slcn/aibib.py` 的**四条安全校验**并入（见下），写回 `reference.bib`；
3. 立刻出一份**离线**回读校验报告 **`<stem>_回读校验报告.md`**，结论一行打进控制台；
4. 在《文献核实任务书》里追加 **`## 六、合并记录（本次已并入 reference.bib）`**：
   逐键写清**并入了什么**、**跳过了什么、为什么**、字段级 diff、缺字段 warning；
5. 控制台最后给一句：「**这份 reference.bib 已含 AI 结果，请人工核对；原文件见 reference.bib.ai前**」。

**四条安全校验**（任何一条不通过 → 跳过该条、保留原字节、记中文原因）：

| # | 保障 |
|---|---|
| ① | 返回的条目 **key 必须与请求的 key 逐字符相同**（改名会让正文 `\parencite` 全部悬空） |
| ② | 回复里必须**恰好一条**可解析条目（回一整份 bib / 半截条目都不采信） |
| ③ | **绝不新增、绝不删除条目**：条目数与键的序列必须与原件一致，否则整份作废、退回原件 |
| ④ | AI **没返回**的条目**逐字节不动** |

另外两条不是「跳过」但要知道的：**缺必备字段照样并入**（记成 warning）；
**作者/编者字段由引擎按编辑部硬规则倒装**（`bibnorm.normalize_author_field()`，幂等）——
AI 会照抄源稿的「名 姓」，这一步是引擎负责的。而 `finish_reason` 不是 `stop` 的那几条
（被截断/内容过滤/中断）**一律不并入**，只在任务书的「AI 建议」区留着。

**怎么回滚 / 怎么不要它**：

- 回滚：把 `reference.bib.ai前` 覆盖回 `reference.bib` 即可（它就是本次合并**之前**那一份）。
  ⚠️ **它只有一份、会被下一次运行覆盖**，不是历史存档；要留历史请合并前自己另存。
- 不要并入：命令行加 `--no-ai-merge`，或 GUI 勾「**保留原 reference.bib（不并入 AI 结果）**」
  （与从前逐字节相同）；或者**别配 LLM**（没配就只出任务书，bib 一个字都不动）。
- **一条都没并进去**（AI 全截断/全垃圾）时：`reference.bib` 一个字节都不动、
  不写回滚副本、不写报告，控制台明说「未改动」——「0 条并入」不会长得像成功。

**手动通道：「并回」（`checkbib.py --merge-into`，2026-10-15）**

自动通道要编辑在本机配好端点；多数编辑的做法是**把任务书交给自己的联网 AI**，拿回一整份
改好的 `.bib`。`--merge-into` 就是给这条路用的**同一个合并核心**（`slcn/aibib.py`，
不是第二份实现）：

```bash
cd Engine
PY=/opt/homebrew/bin/python3.14
# 把「交回的整份 .bib」并进产物里的 reference.bib（原文件先备份为 reference.bib.ai前）
$PY checkbib.py --returned 交回.bib --merge-into "…/2026年第5期(90)/01 Jingru_Kou/reference.bib"
# 可选：--tex 正文.tex（合并后的报告仍查引用键闭合）、--out 报告.md（把报告落到文件）
```

- 用的判据与 §3.5 上面那张表**一字不差**：key 逐字符相同、绝不增删条目、
  交回文件里没出现的条目**逐字节不动**、`author`/`editor` 由引擎倒装、字段按第三节重排。
- **先备份、后覆盖**：写回滚副本 `<目标>.ai前`（已存在就覆盖），再写目标。
- **合并记录**：同级有《文献核实任务书》（`<稿件名>_文献核实.md`，含同级唯一一份）就**追加**到它；
  没有 / 有多份分不清 → 新建 `<目标 stem>_合并记录.md`。控制台会说明落在哪一份。
- 并完立刻重跑一遍**离线**回读校验，把**结论一行**打出来；`--out` 给了就同时落一份
  「合并后参考文献核对报告」。
- **退出码与体检模式不同**：`0` = **未改动**（一条都没并进去，且一个文件都没写）；
  `1` = **已并入 ≥1 条**；`2` = 用法 / IO 错误。脚本调用请按模式判读。
- GUI 里这是「文献回读校验」页体检成功后出现的「**并回**」按钮（目标 = 「任务书前的
  原件 .bib」那一栏）；并回后可以「重新编译该篇」让 PDF 反映新 bib。

**粘贴并回 / 粘贴预览（2026-10-15 phase 2c）**

编辑常常把 AI 的回复**直接粘进界面**。为了不让「先写个临时 .bib 再删」多一次落盘、
也不让那个临时文件名印进报告与合并记录，交回的文本可以**从标准输入**喂进来：

```bash
cd Engine
PY=/opt/homebrew/bin/python3.14
# 粘贴并回（**不落中间文件**）：交回的整份 .bib 走 stdin
pbpaste | $PY checkbib.py --returned-stdin --merge-into "…/2026年第5期(90)/01 Jingru_Kou/reference.bib"
# 粘贴预览：只打印一行「识别到几条、键是什么」，**永不写文件**
pbpaste | $PY checkbib.py --returned-stdin --list-keys      # 例：20 条：1, 2, 3, 4, 5, 6, 7, 8…
# 体检也一样：--original / --tex / --out 照旧可用
pbpaste | $PY checkbib.py --returned-stdin --original "…/reference.bib" --tex "…/X.tex" --out /tmp/报告.md
```

- `--returned-stdin` 与 `--returned` **二选一**（同给两个 → argparse 当场报错，退出码 2）；
  **空输入 = 用法错误**（退出码 2）——空文本不许长得像「0 条、没问题」。
- 编码口径与读文件完全一致（UTF-8 → GB18030 降级）；报告头写 `(stdin/字符串)`（没有假文件名）。
- `--list-keys` 是**预览模式**：只打一行「`N 条：key1, key2, …`」（前 8 个键，多的用 `…`），
  退出码 `0`（它不判断 bib 好不好）；**永不写文件**——哪怕同时给了 `--out` / `--merge-into`
  也只打印那一行、绝不落盘（预览不许有副作用）。
- 界面里对应「文献回读校验」页交回那一栏的**「选择文件 / 粘贴文本」分段控件**：
  粘贴模式显示多行编辑器 + 「从剪贴板粘贴」，粘贴后（以及每次改动）跑一次 `--list-keys`
  显示预览；报告挨着「原件 .bib」（产物目录），**没填原件就退到系统临时目录并在日志里说明**。

⚠️ 安全校验只保证**结构不被破坏**（key 不改、条目不增删、未返回的不动），
**不保证内容正确**：卷/期/页码/出版地是否与官方来源一致，仍由编辑按任务书第二节核实。
并完之后**必须人工过一遍**，`reference.bib.ai前` 就是给这一步留的退路。

⚠️ **「凭记忆」还是「联网查过」——看通道说明**（2026-10-15，第 69 条）：并进去的条目
到底是不是**联网核实**出来的，由「六、合并记录」里那行 **`本次核实通道：…`** 与任务书
头部的同一行说明（协议 + 是否启用检索 + **检索证据**几个 `web_search_call`）。见 §3.7。

### 3.7 联网检索：协议、开关、探针、工具名/路径（2026-10-15，第 69 / 70 条）

> 起因：任务书第二节要求「优先用**官方**来源查证卷/期/页码」，可工具从前发的是**标准
> chat 请求、不带任何检索参数** —— 模型只能凭记忆作答，规范与实现自相矛盾（用户指出）。

**两条通道**（`slcn/llm.py`；默认 `chat`，**不设开关时请求体与从前逐字节相同**）：

| 通道 | 打到哪里 | 说明 |
|---|---|---|
| `chat`（默认） | `{base}/chat/completions` | 老行为一字不改。这里的 `tools` 是 **function calling**（要在客户端自己执行），**不是**检索 |
| `responses` | `{base}/responses` | 标准 OpenAI Responses 协议；**只有它**才有「服务端内置工具」 |

**联网检索开关**（只在 `responses` 通道生效）：

```bash
cd Engine
PY=/opt/homebrew/bin/python3.14
# 命令行（还有环境变量 SLCN_LLM_PROTOCOL / SLCN_LLM_WEB_SEARCH=1，两个入口等价）
$PY convert.py --source X.docx --out Y --verify-refs \
    --llm-base https://api.deepseek.com --llm-model deepseek-flash \
    --llm-protocol responses --llm-web-search
```

开了它，引擎会在**每一次**请求里带 `tools:[{"type":"web_search"}]`，并在 system prompt
里加一节「必须先用检索工具核对官方来源、把实际打开的页面 URL 写进每条上方的 `% 来源：…`」；
逐条请求也追加一行同样的引导（`slcn/refmark.py: SEARCH_MD` / `SEARCH_PROMPT_LINE`）。
**没开时这些一个字都不加**（`entry_prompt(search=False)` 与旧调用逐字节相同）。

**两个「未来兼容」旋钮：工具名与路径可配**（2026-10-15，第 70 条）

> 起因：上面那一句里的 `web_search` 与 `{base}/responses` 从前是**硬编码**。DeepSeek 的
> flash 仍忽略内置检索工具（v4-pro 已支持），各家实现也各不相同，名字未必都叫 `web_search`
> （OpenAI 自己就有一个 `web_search_preview`），路径也可能不是 `/responses`。做成配置之后，
> 服务商一改，编辑**改一个设置**即可，不必等新版本。

| 旋钮 | 环境变量 | 命令行 | 默认值 | 是什么 |
|---|---|---|---|---|
| 检索工具名 | `SLCN_LLM_SEARCH_TOOL` | `--llm-search-tool` | `web_search` | 请求里 `tools:[{"type": …}]` 用的类型名 |
| responses 路径 | `SLCN_LLM_RESPONSES_PATH` | `--llm-responses-path` | `/responses` | 拼在 base 之后的路径（`{base}{路径}`） |

```bash
# 例：端点认的是 web_search_preview、且路径多了个前缀时
$PY convert.py --source X.docx --out Y --verify-refs \
    --llm-base https://api.example.com/v1 --llm-model some-model \
    --llm-protocol responses --llm-web-search \
    --llm-search-tool web_search_preview --llm-responses-path /v1/responses

# 探针（⑤ 体检调的就是它）也认同一对旋钮，命令行/环境变量都可以：
$PY -m slcn.llm --probe --base https://api.example.com/v1 --model some-model \
    --llm-search-tool web_search_preview --llm-responses-path /v1/responses --json
```

- **默认值 = 今天的取值**：两个都不设时，URL 与请求体与从前**逐字节相同**
  （回归见 `tests/test_llm_knobs.py` 的 (b) 组：路径与请求体对冻结字面量逐字节比对）。
- **空值 / 用不了的值一律按「用默认」处理**（引擎 `resolve_search_tool` /
  `resolve_responses_path`）：工具名只允许字母/数字与 `_ - . :`；路径不许含空白 / `://` /
  引号 / 花括号；路径缺前导 `/` 会自动补、末尾 `/` 会去掉。**都会在 stderr 留一条说明**
  （不让一个手滑的配置静默生效、也不让它打灭整条 `--verify-refs`）。
- **界面**（「设置」页 AI 区，「联网检索」下面那两栏「检索工具名」「响应路径」）：留空 =
  不加任何参数；填了才传。**探针用的是同一份配置**——否则「体检说支持、转换却打另一个
  地址」就是最坏的假证据。
- ⚠️ 这两栏只改变**请求形状**，**不改变能力**：端点到底会不会真的联网，仍以
  「⑤ 检索探针」的实测（判据 = 回复里有没有 `web_search_call`）与转换日志里的
  **检索证据**为准。控制台/任务书那行 `本次核实通道：responses + <工具名>（已启用联网检索）`
  会写出**实际配置的工具名**（默认时仍是 `web_search`）。

⚠️ **能力边界（务必看完，不然会以为「勾了就联网了」）**：`tools` 只是一个**请求**。
端点是否真的检索，由端点与模型共同决定：OpenAI 的 Responses `web_search` 真会联网；
**DeepSeek 按模型区分**（2026-10-17 官方文档 + 本机实测）——`deepseek-v4-pro` 会真检索
并返回 `web_search_call` + 来源 URL；`deepseek-flash` 仍**静默忽略**（HTTP 200、无检索
证据），官方兼容性明细也仍把 `file_search` / `code_interpreter` 等其它内置工具列为「忽略」。
**所以同一条开关在不同服务商/模型上效果完全不同，而且不报错。**
唯一诚实的判据是**回复里有没有检索证据**：

**探针（唯一一份实现，在引擎里）**：

```bash
cd Engine
# 人看的清单（退出码：0 支持 / 1 不支持 / 2 其他错误或用法错误）
$PY -m slcn.llm --probe --base https://api.deepseek.com --model deepseek-flash
# GUI ⑤ 用的机器可读形态：stdout 只有一行 JSON（说明走 stderr）
$PY -m slcn.llm --probe --base https://api.deepseek.com --model deepseek-flash --json
# ⚠️ 密钥用环境变量 SLCN_LLM_KEY（别写进命令行：argv 在 ps 里对同机所有用户可见）
```

三种判词（`verdict`）：`supported`（HTTP 200 **且**收到 `web_search_call`）/
`unsupported`（HTTP 4xx **明确拒绝**，**或** 200 但没有检索证据 = 静默忽略）/
`error`（连不上、超时、5xx、回复不是合法 JSON、没配置 —— **既不算支持也不算不支持**）。
GUI 的「设置」页「测试连接 / 体检」里的 **⑤ 检索探针** 直接调这条 CLI（不在 Swift 里重写
协议），把结论翻译成人话并说明「端点会不会查」与「本次会不会用」两层。
跑完之后，`convert.py` ⑪ 还会打印 **`✅ 检索证据：本次共收到 N 个 web_search_call`**
或 **`⚠ 本次没有任何检索证据（0 个）：端点忽略了这个工具，模型只能凭记忆作答`**，
并把同样的话写进任务书头部的「本次核实通道」与「六、合并记录」。

**DeepSeek 检索能力现状（2026-10-17 更新）**

- **内置 `web_search`（Responses 通道）**：仅 `deepseek-v4-pro` 真检索；`deepseek-flash`
  忽略（实测：v4-pro 在 `/responses` 与 `/v1/responses` 都返回 `web_search_call`；单次
  输入 14k–57k token、耗时 16–38 s，按模型 token 计费——贵而慢，只适合当可选捷径）。
- **独立 Search / Fetch API**（推荐主路径；工具**已接入**：§3.10 的 `--search-refs`，
  给每条文献写「检索证据」）：
  `POST {base}/v1/tools/search`、`POST {base}/v1/tools/fetch`（base = `https://api.deepseek.com`）。
  请求字段：`query`（1–128 字，支持 `site:` / `-site:` / `-词` / `"短语"` / `lang:` /
  `after:` / `before:`）、`count`（1–50，默认 10）、`need_content`（`content` 返回正文）。
  每条结果带 `title / url / snippet / summary / date / site_name / authority(0–4) /
  rerank_score(0–3)`。按成功请求计费（Search / Fetch 各 ¥5 / 1000 次，失败不收费），
  账号级 QPS 各 5。⚠️ 检索 query 会包含题名/作者/刊名——与「把条目原文发给模型」同级别的
  外发，需在设置页与文档写明。

**开关/路径两条容易踩的点**：

- **路径只拼一次 `{base}{路径}`（默认 `/responses`），不猜要不要补 `/v1`**：DeepSeek 文档给的
  Responses `base_url` 是 `https://api.deepseek.com`（不带 /v1），OpenAI 要
  `https://api.openai.com/v1`。按服务商文档填；探针结果里的 `endpoint` 字段能看到实际打的
  地址。路径本身可配（第 70 条，`SLCN_LLM_RESPONSES_PATH` / `--llm-responses-path`）。
  （DeepSeek 2026-10-17 实测：`/responses` 与 `/v1/responses` 都可用；按服务商文档填最稳。）
- **Response 的输出上限字段名不同**：chat 是 `max_tokens`，responses 是
  `max_output_tokens`（写错会被静默忽略 → 表现为「没封顶」）。引擎已按通道各用各的。
- `SLCN_LLM_THINKING=0` 在 responses 上映射成 `reasoning:{"effort":"none"}`；
  「开启 / 跟随默认」**不发** `reasoning` —— `effort` 没有「模型默认」这一档，猜一个
  会**静默改变**成本（chat 那边的 `{"type":"enabled"}` 正好等于默认值）。

### 3.6 任务书逐条清单里的「源稿原文」（2026-10-15，第 68 条）

勾「生成参考文献核实任务书」（`--verify-refs`）时，**每条从源稿解析出来的文献**都会在
「四、逐条清单」的那一条下面多一行：

```markdown
- **源稿原文**（解析可能有遗漏，请以它为准）：`[17] 党学哲，“论作为证据逻辑的核证逻辑”，湖南科技大学学报（社会科学版），2022年第6期，第29-38页。`
```

- **它是什么**：这一条**被解析时所依据的源稿文字**——编号清单给整条 `[N] …`，
  脚注体例给那条文献脚注，文末人年制区给那一行。映射的键是**引擎生成的 bib 键**
  （三条通道一视同仁，见 `slcn/docxsource.py: DocxDoc.ref_raw_by_key`）。
  为什么要给：解析器丢一个字段（第 66 条实测丢了刊名），任务书与 AI 看到的就只是残缺件。
- **口径**：编辑 / AI 看到「解析得到的条目」与「源稿原文」不一致（原文里有刊名/卷期/页码
  而条目缺）时，**按源稿原文补全或纠正**；⚠️ **原文里没有的信息仍然不许编**
  （查不到就写 `未查到` / 省略该字段）——「以原文为准」是上限，不是「可以拿常识补」的许可。
- **拿不到原文的条目不写这一行**（作者自带的 `.bib`、LaTeX 通道）：与从前逐字节相同。
- **归一化**：原文压成**一行**（换行/制表符/全角空格 → 单个空格）、去首尾空白，
  **超过 300 字截断并标注 `…（已截断）`**——异常输入不会把任务书刷爆，也不静默丢内容。
- **AI 预跑**（配了 LLM 时）的逐条请求里同样带这段原文，形状仍是「一条请求 = 一条条目」，
  回复解析与 §3.5 的合并规则**一个字都没变**。想核对某次调用到底发了什么：本地 mock 端点的
  `GET /__stats` 里 `last_user_prompts` = 端点**实际收到**的 user 消息（最近 20 条）。
- 命令行会打印覆盖情况：「其中 N 条附上了源稿原文」，或
  「本次没有可用的源稿原文（LaTeX 通道 / 作者自带的 .bib）」。

### ⛔ 版本控制红线（务必遵守）

**稿件仓（作者来稿所在的那个仓）永不执行 git 写操作。** 未经仓库所有者明确同意，禁止在稿件仓里
`git add` / `git commit` / `git push`，以及任何改写历史或远端引用的操作。

- 允许：读取状态（`git status` / `git log` / `git diff`）用于自查
- 禁止（稿件仓）：`git add`、`git commit`、`git push`，以及改写历史或远端引用的操作

**工具箱仓（引擎与界面的开发主场）与笔记仓的口径不同**（2026-10-17 用户拍板）：
会话**可以**自行提交 / 推送，但要**攒批次**（一轮工作一条提交）、**不要频繁推**；
推 `v*` tag 发版前先把 CI 跑绿。单点清单见仓库根 `AGENTS.md`「红线（硬约束）」3。

### 3.8 Word 通道的「英文元数据第二源」（2026-10-15，第 76 条）

**一句话**：中文稿的 Word 源稿常常**没有**英文题名/摘要（作者另外交一份「英文摘要」docx）。
工具现在会**在源文件同目录**自动找那份文件，把正文缺的英文字段补上——不必再由编辑手工粘贴。

**它怎么挑文件**（判据单一出处 `slcn/sources.py: en_metadata_sheet()`，两个条件都要满足）：

| 条件 | 为什么 |
|---|---|
| 解析出 `title_en` / `abstract_en` / `keywords_en` **至少一个** | 没有英文字段的 docx 补不了任何东西 |
| `doc.body` 叙述正文 **< 200 字符** | 排除「同目录的另一篇**英文全文稿**」——否则会把**另一篇文章**的英文元数据塞进本篇 |

- **只找同目录**（与 `find_bib()` 同一个纪律），**绝不跨目录搜索**；同目录有多份时按「字段更全 > `abstract_en` 更长 > 文件名序」确定性地挑一份。
- `--scan` 现在把它标成 **`[英文元数据]`**（以前是【未识别】）——它是**可选第二源**，永远不会被选成正文档。
- 补进来的字段与来源文件会**写进运行日志**：`第二源 当代逻辑哲学鸟瞰英文摘要.docx（英文元数据表，含：…）` +
  `英文元数据补入 N 项：…`；一个字段都不缺时写「正文自带的英文块优先，第二源未改动任何字段」。

**它不会做什么**（这些是硬口径）：

- **只补空值，绝不覆盖**：正文 docx 自己解析出来的英文题名/摘要/作者**一个字符都不动**
  （正文自带完整英文块时，第二源连读都不读）。
- **不猜**：文件里没有的字段照旧写 `【未识别：…】` 占位符。典型例子是 `affiliation_en`
  （英文单位常常两份文件里都没有）。
- **中文栏位不取第二源**：只搬 `*_en`，避免把另一份文件的表述混进中文的题名/摘要/作者/单位。

**不想用它 / 想指定别的**：

- 不想用：把那份英文摘要 docx **移出源文件所在目录**（或改名成非 `.docx`）即可——工具不会去别处找。
- 想指定：命令行 `--metadata <英文摘要.docx>`。⚠️ 这是**有意的行为扩展**：以前 Word 通道会**静默忽略**
  `--metadata`，现在它会被当第二源；指到非 `.docx` 时打印一行提示并退回「同目录自动找」。
- 想直接给真值：用「正文信息覆盖」的 7 个覆盖键（`--title-en` / `--abstract-en` 等），
  覆盖值**优先于**第二源（见本文 §六「placeholder 与覆盖键约定」）。

**边界**：`en_metadata_sheet()` 的「没有叙述正文」判据是**保守**的——宁可拒绝一份可疑文件（退回占位符），
也不把另一篇文章的元数据塞进本篇。全库实测：73 篇「单 docx 正文」的稿子里只有 2 篇能发现第二源，
其中只有 1 篇真正补入了字段（`10 Bo_Chen`）。

### 3.9 合并一期的 PDF：`mergepdf.py`（2026-10-15，第 88 条）

GUI「合并文件」页的逻辑原来长在 mac 外壳里（Swift + PDFKit）。做 Windows 版的前提是
**引擎只有一份逻辑**，所以整套合并规则搬进了引擎，命令行入口就是它 —— 两个外壳都来调这一个。
（mac 外壳**暂时**仍走自己那份 Swift；两条路的输出已用真实成品期目录**逐条比对过**：
176 页、102 条大纲、层级/名字/页码全同，见 `docs/cases/62+.md` 第 88 条。）

```bash
cd Engine
PYTHONPATH="$PWD/vendor" python3 mergepdf.py --folder "<期目录>" --editors-page --nest-outlines
```

- `--folder` 是**期目录**（含 `扉页.pdf`、`000 …/`、`01 作者/` …），**不是**单篇稿件的目录；
- 默认写成 `<期目录>/合并<时间戳>.pdf`（与 GUI 的命名一致），`--out` 可指定别处；
- `--editors-page` 把编委页插在扉页之后、`--back-cover` 把封底放到最后、
  `--nest-outlines` 把各篇自带的小节书签挂到该篇文章之下（**默认平铺**，与原版 PyPDF2 一致）；
- 编委页/封底按**文件名**识别（这些页没有文字层）：`编委页.pdf` → `cneditor*` → `扉页2.pdf` →
  名字含「编委」；封底 `cover+bottom*` → 名字同时含 cover 与 bottom。工具箱自己的 `合并*.pdf` 一律排除；
- **缺文件不会中断**（缺哪篇打哪篇的 `⚠`），最后一行是 `完成。合并文件：<路径>（共 N 页）`。

### 3.10 自动检索证据与核实范围：`--search-refs`（P1/P2，第 71/72 条起）＋ 成本闸门（第 128 条）

背景（P0 实测见案例 117）：DeepSeek 2026-10-17 上线**独立 Search / Fetch API**
（`POST {base}/v1/tools/search`、`/v1/tools/fetch`；结果带 `authority` / `rerank_score` /
`date` / `summary`，支持 `site:` / `lang:` / `after:` 等语法；Search / Fetch 各
**¥5 / 1000 次成功请求**、账号级 QPS 各 5）。本工具用它给**每条参考文献**补一份
「公开网页上有没有这条文献、长什么样」的**证据**，写进《文献核实任务书》。

```bash
$PY convert.py --source X.docx --out Y --verify-refs --search-refs \
    --llm-base https://api.deepseek.com/v1 --llm-key <key>   # 密钥也可走 SLCN_LLM_KEY
# 单独指定检索服务：SLCN_SEARCH_BASE / SLCN_SEARCH_KEY；等价开关：SLCN_SEARCH_REFS=1
```

**它做什么**：每条文献最多 2 次查询（题名加引号 → 题名+作者+年份+刊名、整体不加引号），
按「题名相似度 0.55 + 作者命中 0.15 + 年份 0.10 + 刊名/出版社命中 0.10 + 域名层级 0.10」
打分，把 top3 候选（URL / 站点 / authority / 匹配分）写进任务书每条上方的
「🔎 自动检索证据」，任务书头部附一行检索统计（次数 / 命中分布 / 预估费用）；控制台
`⑪·检索` 逐条打印判词。

**P2（第 72 条起）起还做两件事**——把「证据」真正用起来，但**不改 `.tex`**：
1. **注入提示词**：开了 AI 时，逐条请求里附「【本次自动检索证据】」（候选 URL / 站点 /
   authority / 匹配分），并声明「`% 来源：` 只能写证据里出现过的 URL，不得编造」；
   证据说「未查到」时明确要求按「查不到」处理（省略字段 + 待核实，不许拿记忆补齐）。
2. **来源 URL 白名单**：AI 结果并入 `reference.bib` **之前**，引擎抽取回复里 `% 来源：`
   行的 http(s) URL，逐一核对是否属于**本次实际检索到的来源**（本次全部检索结果 URL ∪
   内置检索的 `url_citation`）；**换域名或换路径的 URL 视为无法核实**，该条**不并入**
   （原文逐字节未动），原因写进任务书「六、合并记录」。比较口径（2026-10-17 真 API 验收
   修正）：**忽略片段 / 查询参数 / 端口 / 末尾斜杠**，按「域名 + 路径前缀」判定同一来源
   ——此前逐字比较会把「同一来源的变体」误判成编造（实测 7/22 误伤，修正后 22/22 通过）。
   这是第 72 条起的新硬保障，与四条旧保障（key 不可改 / 恰好一条 / 不增删条目 /
   未返回的条目不动）**并存**。「来源说明」（不含 URL）照旧放行。

**默认关闭**：不开启时任务书、提示词、`reference.bib` 与从前**逐字节相同**
（回归见 `tests/test_search_refs.py` (h) 与 `tests/test_evidence_injection.py` (c)：
默认一次检索请求都不发、提示词里没有证据块）。

**口径与限制**（别把证据当结论）：
- 「未查到」≠ 文献不存在（可能是题名解析错、或该文献未被收录）；任务书会回显用过的
  query，让编辑能分辨「query 烂」还是「文献真不在」；
- 命中的来源仍须**人工核对字段**；权威度只是线索（P0 实测 94/100 条结果 authority≥3，
  单独使用没有判别力），真正的判据是标题/作者/年份/刊名/域名综合分；
- 隐私：query 由题名/作者/刊名拼成，会发到 DeepSeek 搜索服务（与「把条目原文发给模型」
  同级别的外发）；
- 自测：`$PY -m slcn.search --probe --json`（P2 界面的检索探针直接复用这条命令）。

**界面入口（P2）**：「设置」页 AI 区的**「检索证据」**开关（mac `aiSearchRefs` /
Windows `api.searchRefs` → 环境变量 `SLCN_SEARCH_REFS=1`）。⚠️ 它复用 AI 的地址/密钥，
所以要先勾「用 AI 处理参考文献」并配好地址/密钥；**它与「联网检索」是两个独立开关**：
后者是模型内置工具（需 v4-pro 才真查），前者是工具自己的 Search API（flash/v4-pro 都能用）。
mac 走 `--search-refs` argv、Windows 走 `SLCN_SEARCH_REFS` 环境变量，默认都**不加**——
默认命令行/环境变量与从前逐字节相同。

**核实范围与成本闸门（第 128 条，2026-10-18；`--verify-all-refs` / `--verify-max` /
`--verify-plan`）**：期 87 子案例 1 实测——源 `.bib` 377 条、正文只引用 **31 个**唯一键，
但任务书 + 检索 + AI 把 **376 条**全核了（检索 418 次 ≈ ¥1.845、AI 376 次调用 /
2,538,740 prompt tokens；且模型 `deepseek-flash` 忽略 `web_search` → 0 条真实检索证据）。
所以 ⑪ 在**花钱之前**先算范围、先看账：

```bash
$PY convert.py --source X.docx --out Y --verify-refs                     # 默认：只核正文引用到的条目
$PY convert.py --source X.docx --out Y --verify-refs --verify-plan       # 只算账（不生成任务书/不检索/不调 AI）
$PY convert.py --source X.docx --out Y --verify-refs --verify-all-refs --verify-max 0   # 显式全量、不限
```

- **默认只核「正文引用到的键」**：范围 = 产物 `.tex` 里 `\parencite` 等引用命令的键 ∩ bib 的键；
  正文里的 `\nocite{*}` = 全量（BibTeX 语义：声明引用全部条目）。跑前日志打一行
  「源 bib N 条；正文引用 M 条 → 本次核实 M 条（跳过 N-M 条未引用）」；任务书头部也写一行
  「本次核实范围：只核正文引用到的 M 条（源 bib 共 N 条；跳过 N-M 条）」，编辑一眼能看出
  这份**不是全量**；AI 并入后的任务书「六、合并记录」也把**范围外**的条目单独写一行汇总，
  不会把它们逐条记成「AI 未返回」（那是「没让 AI 做」，不是「AI 做失败」）。
- **Word 通道同样以产物 `.tex` 为准**：Word 的引用是 `build_docx` 替换完才落到 `.tex` 里的
  （`docxsource.link_citations()` 的 `used` 只是局部变量、没对外暴露；`BuildResult.bib_keys_used`
  在 Word 通道是「文末参考文献区的**全部**键」而不是引用键，两者都不能直接当范围）。
- **边界：正文解析不出引用键**（没有 `\parencite`、引用没替换完、或引用的键都不在 bib 里）→
  **退回全量 + 醒目警告**（与旧行为相同），绝不静默「一条都不核」；量化行会写明「退回全量」。
- **上限闸门**：本次核实条数默认上限 = `--verify-max`（默认 120；`0` = 不限）；超了且没加
  `--verify-all-refs` → **退出码 2**（stderr 给三条路：`--verify-max N` 调上限 /
  `--verify-all-refs` 显式全量 / `--verify-plan` 先看账），**没生成任务书、没检索、没调 AI**。
  `--verify-all-refs` = 显式承认全量、越过闸门。
- **`--verify-plan`**：只打印核实范围、上限与预计检索/AI 次数后**退出 0**（不生成任务书、
  不检索、不调 AI；**转换产物照常写出**）。⚠️ 它不是 `--dry-run`：引用键要等转换把
  「（作者 年份）」替换成 `\parencite` 之后才拿得准，所以 `--dry-run` 里算不出这个数。
- **模型能力前置提示（第 128 条）**：开了「联网检索」、且模型**已知会静默忽略** `web_search`
  （`deepseek-flash` / `deepseek-chat` 这类；判据在 `slcn/llm.py: ignores_web_search()`）时，
  在 **AI 批量开始前**打一行 `⚠⚠ 模型能力前置提示`：说明本次几乎不会有真实检索证据、
  结果只能按「凭记忆」对待，并给出「换 `deepseek-v4-pro`」或「把任务书交给编辑自己的联网 AI」
  两条路。**事后提示照旧保留**：跑完若收到 0 个 `web_search_call`，仍会明写「按凭记忆对待」。
- **默认路径零变化**：不加新参数时 `.tex` / `reference.bib` **逐字节不变**（回归见
  `tests/test_verify_scope.py` (d)：同一份源稿加不加 `--verify-refs`，转换产物逐字节相同）。

**先查编辑部引用库（第 129 条，2026-10-18）**：核实链路第一步先查**编辑部自己的引用库**
（私有笔记仓 `EditToolBox-notes/bib_db/bibliography.jsonl`；内容 = 往期已出版期的成品 bib
沉淀，schema/来源口径/维护方式见该目录 `README.md`）。命中**已核实**条目 → 该键
**不调检索 API、不调 AI**（省钱 + 体例统一 + 可审计）。判定刻意**保守**——把不同文献
误判成「已核实」比漏查更糟，所以：

- **安全命中（才允许跳过检索/AI）**：① DOI 精确命中 `verified=true` 记录；或
  ② 题名高置信（bibdb 的 0.90 阈值）+ **第一作者姓 / 年份相容** + `verified=true`；
  ③ 第 58 批新增**译著 / CJK 作者安全命中**：题名相似度 ≥0.98 + 年份相同 + 出版社一致 +
  第一作者按「（著）/（译）/（编）/（eds.）」责任标记归一后相容（仍须 `verified=true`；命中仍只跳过
  检索/AI、**不改稿**。实测 84 篇成品 bib 计数不变，Bo_Chen 初排产物 safe 84%→96%、候选 4→1）；
- **key 命中只算候选**：BibTeX key 多为 per-paper（`a1`…），同 key 不保证同文献 →
  **仍走检索 + AI**；库内未核实、题名低置信同样只算候选；
- 安全命中**不改 `reference.bib`**：库版本与稿件条目的字段差异只写进任务书
  「**五、库内已核实（无需再查）**」一节（列出 key / 库内来源期·篇·文件 / 差异提示），
  是否按库版本统一由编辑裁决；
- 跑前 stdout 多一行：`引用库：命中 X 条已核实（跳过检索+AI）、候选 Y、未命中 Z（库 n 条，<path>）`；
  `--verify-plan` 的**预计检索 / 预计 AI 次数按扣减后的条数算**（日志里另起一行写明扣减了多少）；
- 任务书「六、合并记录」把「**库内命中（未调 AI）**」与「正文未引用（范围外）」**分开统计**，
  不把它们写成「AI 未返回/失败」；全部命中时**一次 AI 都不调**，只写这一节留档。

**初排默认只读比对（第 58 批，2026-10-20）**：不执行核实时（不传 `--verify-refs` / `--verify-plan`），
初排也会读默认库比对产物 `reference.bib`，**只**在默认库存在且有条目时打一行
`引用库比对：命中 X / 候选 Y / 未命中 Z（只读，不改产物）`；`--no-bibdb-verify`（或 `--no-bibdb`）
关闭，无库 / 无 bib / 读库失败**完全静默**。只增日志：`.tex` / `reference.bib` / 任务书 / argv 一律不变；
执行核实时仍走上面旧口径（打印 `引用库：命中 X 条已核实…` 并写任务书第五节），不重复跑。

开关（**默认口径 = 执行核实且默认库存在时自动启用**）：

```bash
$PY convert.py --source X.docx --out Y --verify-refs                  # 默认：有库就自动查
$PY convert.py --source X.docx --out Y --verify-refs --verify-plan    # 计划里已按扣减后算
$PY convert.py --source X.docx --out Y --verify-refs --bibdb          # 显式要求查库（库缺失只告警）
$PY convert.py --source X.docx --out Y --verify-refs --bibdb-path /path/to/bibliography.jsonl
$PY convert.py --source X.docx --out Y --verify-refs --no-bibdb       # 显式关：与接入前逐字节相同
$PY convert.py --source X.docx --out Y --no-bibdb-verify             # 第 58 批：关闭初排默认只读比对（不核实路径）
```

- 默认库 = `slcn.bibdb.default_db_path()`（`<笔记仓>/bib_db/bibliography.jsonl`；笔记仓由
  `SLCN_NOTES` / 同级目录解析）。**笔记仓或默认库不存在时静默跳过**——公开仓/CI/没建库的
  机器上行为与从前零变化；只有显式 `--bibdb` / `--bibdb-path` 指错时才告警（且**不中断出稿**）。
- **不核实（默认）路径产物逐字节不变**；第 58 批起日志默认多一行只读比对（见上），`--no-bibdb-verify` 或 `--no-bibdb` 关掉后，stdout/任务书/产物与接入前逐字节相同。
- 引用库怎么来：公开仓 `Engine/tools/bibdb_seed.py` 只读扫描稿件仓往期成品 bib 生成
  （可重复运行幂等），维护/冲突裁决见笔记仓 `bib_db/README.md`；
  开发用 CLI 是 `Engine/bibdb.py`（`stats` / `lint` / `query`，不进发布包）。
- 保守判定的代价要如实说：**库内没有的新文献一律走原流程**（不会因为查库失败而跳过核实）；
  跨库同名作品/简写差异可能命中不到（宁可漏，不可错）。回归见 `tests/test_bibdb_verify.py`。

---

---

### 3.11 英文来稿通道（2026-10-17，第 121 条，期 88 七篇实测）

期 88 七篇英文来稿（Word 3 + LaTeX 4）实测暴露的英文判据，已拆成**只对全英文稿生效**的补丁，
中文稿（含中英混排的常见中文稿）产物**逐字节不变**（用例一 10 篇回归 10/10 相同）：

| 判据 | 行为 | 权威实现 | 测试 |
|---|---|---|---|
| 英文**论述性脚注**误判 | `classify()` 把含年份的散文脚注判成文献；`parse_entry()` 只能给出 note（无 author/title）时：全英文稿且文末有参考文献区 → 降级为**纯注释**（正文脚注原样保留、不进 bib）；中文稿/无文末区的短引注保留旧行为 | `slcn/docxsource.py::_entry_is_note_only()` / `_looks_short_bibliographic_note()` / `doc_is_en` | `tests/test_english_manuscripts.py` ⑥ |
| 英文文末参考文献**「全名 + 年份在后」**格式 | 如 `Jacobs, Scott, Jackson, Sally, 1989. Title…` / `Van Eemeren, Frans et al. 2014. …` → 解析成编辑体例作者（`Jacobs, S. and Jackson, S.`）、年份、题名；不动既有 GB 英文/中文格式 | `slcn/docxsource.py::_parse_reference_line_en_fullname()` | ⑦ |
| 无样式英文小标题 | `N)` 半角编号（02 Lu_Jiang 型）；以及**无编号、无样式、Title Case 短段**（07 Hu_Liu 访谈稿的 `On Logic Pluralism` 等），要求前后都是成段正文——只对全英文稿启用 | `classify_en_heading()` / `_looks_plain_en_title()` + ⑥ 前的上下文判据 | ⑧ |
| 英文纯注释里的引用 | 全英文稿的纯注释文本也做「（作者 年份）→ `\parencite{key}`」替换（人工成品同样做）；中文脚注不动 | `docxsource.link_citations()` 在 ⑤.3b 的预跑 | ⑥ |

**已知残留（如实说清）**：英文散文脚注里的「叙述式引用」（如 `see King 2001, where…`，
不带括号）不会自动变 `\parencite`；06 型文末参考文献约 10 条（`et al.` 前缺逗号等变体）
暂未解析。这两类由编辑按〈参考文献核实任务书〉手工补。

## 五、目录结构

```
                      ← 唯一的顶层项目（界面 + 引擎，2026-10 合并）
├── sources-mac/                     Swift 界面
├── Engine/                      ← Python 转换引擎（原 slcn-convert/）
│   ├── convert.py           ★ 正式工具：源文件 → 规范命名的初排稿
│   ├── run_poc.py           端到端驱动 + 打分报告
│   ├── corpus.py            全库回归（90 篇有基准可比对，69 篇可跑通）
│   ├── ablation.py          规则消融·单篇版（快，但会过拟合）
│   ├── ablation_corpus.py   规则消融·全库抽样版（准，规则取舍以此为准）
│   ├── refaudit.py          全库参考文献质量普查（纯离线核对）
│   ├── docx_audit.py        全库 Word 源稿体例普查（识别率 / 标题与文献体例 / 长尾）
│   ├── eval_quotation.py    引文环境专项评测（查全率 / 查准率）
│   ├── checkbib.py          《文献核实任务书》回读校验：语法 / 规范 / 引用闭合 / 与原件 diff（纯离线；薄壳，解析在 slcn/bibparse.py，报告在 slcn/bibreport.py）；`--merge-into` = 手动「并回」（与自动通道同一份合并核心）
│   ├── HANDOFF.md           交接说明（下一个对话先读这份）
│   ├── README.md            本文档
│   ├── out/                 产物（gitignore）
│   ├── vendor/              本地依赖 pypinyin / lxml / pypdf（gitignore）
│   ├── tests/               单元测试（**套件数与逐套件明细见 README 的 AUTO 块**，不要手抄）：
│   │                        test_aibib.py（第 64 条：AI 结果并入的四条保障 + 端到端）、
│   │                        test_authornorm.py（第 63/64 条：英文作者一律倒装、中文著/译体例）、
│   │                        test_checkbib.py（回读校验的 4 类判据 + 只 import 离线模块）、
│   │                        test_merge_into.py（第 64 条手动通道：`checkbib.py --merge-into` 的合并/回滚/记录/退出码）、
│   │                        test_abstract_label.py、test_llm_finish_reason.py、
│   │                        test_llm_usage_and_jsonmode.py、test_cn_dot_heading.py、
│   │                        test_tail_block.py、test_llm_thinking_and_reasoning.py，
│   │                        另有 5 套既有（bare_email / footnote_id0 / llm_error /
│   │                        llm_request_params / refmark_raw_refs）
│   └── slcn/
│       ├── templates.py     模板选择：按语种取默认 sty，并同步 \usepackage 行
│       ├── naming.py        作者姓名 → 输出名（名_姓），多音字/复姓/外文名处理
│       ├── clean.py         删编译中间文件（与 EditToolBox DeleteTempFeature 行为一致）
│       ├── docxxml.py       直接读 Word XML：OMML 公式 / 斜体 / 脚注 / 图片 / 表格
│       ├── footnotes.py     脚注分类与拆分：纯文献 / 混合 / 纯注释 / 古典文献
│       ├── quotations.py    引用环境：三行阈值、\hfill 右对齐、缺文献/页码标注
│       ├── office.py        LibreOffice 无头转换：Word 原稿 → PDF（供矢量形状人工截图）
│       ├── bibcheck.py      参考文献核对：字段完整性 + 引用双向核对 + 英文作者倒装/中文译著标记（第 63/64 条）
│       ├── bibparse.py      健壮的 .bib 解析器（**唯一一份**）：切条目/顶层字段/语法问题 + 单条解析（第 64 条）
│       ├── bibreport.py     回读校验报告（**唯一一份**）：`build_report_and_conclusion()`，checkbib 与并入自检共用（第 64 条）
│       ├── aibib.py         把 AI 返回的条目并入 reference.bib：四条保障 + 字段级 diff + 合并记录（第 64 条）；**也是手动「并回」的合并核心**（`merge_returned_bib()` / `apply_merge_to_file()` / `write_merge_record()`）
│       ├── refmark.py       生成《文献核实任务书》markdown（规范+来源+逐条清单）
│       ├── llm.py           可选大模型通道：OpenAI 兼容的云端/自建端点（默认只抽取不补全）
│       ├── footref.py       把「整条在 note 里」的脚注体例文献拆成结构化条目
│       ├── multisource.py   多文件源稿：按内容判定正文/参考文献/脚注/图/分页稿
│       ├── latex.py         LaTeX 感知的解析：括号配平、注释剥离、\newcommand 提取与改写
│       ├── sources.py       源识别：判定 original/ 下每个文件扮演什么角色
│       ├── docxsource.py    Word 解析：章节层级、人年制引用、参考文献结构化
│       ├── bibnorm.py       .bib 规范化 + 英文作者一律倒装（`normalize_author_field`）+ \bibitem 转换
│       ├── rules.py         预校正规则库（每条标注 safe 与否）
│       ├── build.py         装配器：模板 + 元数据 + 正文 + 规则
│       └── score.py         打分器：两级比对 + L2/L3 分档
├── build.sh                 构建 app（把 Engine/ 打包进 Resources）
├── 一键构建.command          双击构建（给不熟悉命令行的同事）
└── 使用说明.md               面向编辑的使用说明
```

---


---

## 六、已知限制（第 1 期待办）

| 项 | 现状 |
|---|---|
| ~~**OMML 公式**~~ | ✅ **已修**：`docxxml.py` 直读 `<m:oMath>`，陈波稿 7 个符号已正确还原为 `$\neg$` 等 |
| ~~**脚注丢失**~~ | ✅ **已修**：`docxxml.py` 读 `footnotes.xml`；Lu_Jiang 的 90 条脚注 → 68 个 bib 条目、68 处 `\parencite` |
| ~~**斜体丢失**~~ | ✅ **已修**：run 级 `<w:i/>` → `\textit{}`；Lu_Jiang 从 0 处增至 323 处（人工 182） |
| **斜体略多** | Lu_Jiang 323 处 vs 人工 182 —— 人工没有把每处斜体都包起来，属编辑取舍，待观察 |
| **引文阈值偏差** | 07 Liangying_Zhao 生成 24 个 quotation，人工 37 个。人工把短于三行的引文也放进了引用环境。用户已确认**不调低阈值**；实测调低到 40 字后 quotation 数 38≈37 精确匹配，但**页数反而多 1 页**，说明阈值不影响版面 |
| **字符拼图需人工重画** | 工具只能检出并占位，**不能自动生成图片**。07 Liangying_Zhao 的两张图需人工参照原稿重画 |
| **合并单元格需核对** | 92 张表里 19 张（21%）有合并单元格。`gridSpan` 已转 `\multicolumn`，但 `vMerge`（纵向合并）只做提示、未转 `\multirow` |
| **表题需人工补** | 只能写 `\caption{【需补充表题】}` |
| ~~**`userf={cn}` 会使版面变紧**~~ | ✅ **已解决**：陈波稿回到 20 页 = 人工 20 页。先前是 bib 解析 bug 导致条目内容偏少，修好后自然对齐 |
| ~~**多文件源稿不支持**~~ | ✅ **已支持**：`--scan` 列角色，出稿时自动合并参考文献附件 |
| ~~**第三种参考文献格式**~~ | ✅ **已支持**：`[N] 作者, 年份, "题名", 刊名, 卷(期): 页` |
| **引用键需人工对齐** | 附件条目键为 `a1`、`a2`…，正文引用写的是作者-年份，两者**自动对不上**，需人工接线 |
| **Liang_Zhang 多 1 页** | 19 页 vs 人工 18 页。已排除 `userf`（两边标记的 8 条**完全相同**）、`month`/`number`/`author`/`publisher` 归一化差异。剩余 bib 差异**全是编辑判断**：`Almog1989` 改 author→editor、`Kratzer2020` 把 institution 并入 note、`Ninan2016` 修正年份（2016→2018）、`Schlenker2009a` 改拼写错误、`Wittgenstein2005` 的「~著，~译」→「（著），（译）」。**这些工具不应复刻**，1 页差是累积效应 |
| **图题/表题需人工补** | 只能写 `\caption{【需补充图题】}` / `【需补充表题】}`，内容得人工填（实测人工会写「苦苦茶视频广告设计的四个功能模块」这类） |
| **矢量形状不能自动截图** | 已能检出 + 占位 + 导出原稿 PDF，但**定位图形在 PDF 里的坐标需要版面引擎信息**，目前只能人工对照截图 |
| **行内公式仍偏少** | 07 Liangying_Zhao：工具 16 处 vs 人工 119 处。作者把大量数学写成纯文本（`Fn(A,B)`、`xi`、`fn`），下标/上标的判定（`xi` 是 `$x_i$` 还是 `$x^i$`？）无法可靠自动化，属编辑判断 |
| **`\cndash` 偏少** | 07 Liangying_Zhao：工具 8 处 vs 人工 31 处；页码区间的识别仍需加强 |
| **脚注略多** | 27 处 vs 人工 15 —— 部分「纯注释」人工可能并入了正文 |
| **编辑侧字段** | 卷号/期号/年份/起止页/页数 作者稿里没有，需人工填或从期目录读 |
| **拼音** | 未装 `pypinyin`，只覆盖 40 个常见姓氏 |
| **译名对照** | 人工会补「（Gottlob Frege）」这类外文原名括注，10 Bo_Chen 有 **22 处**。这是可复用的资产，应从历史语料建表 |
| **公式重排** | 人工会把 `split` 改成 `aligned` 并重排，属编辑判断 |
| **英文稿章节层级** | 已能认 `1.` / `1）`，但部分英文稿（如 01 Wenfang_Wang、06 Zhixi_Chen）仍有大量 L3，疑似元数据或参考文献结构差异，**待个案排查** |
| **PDF 源稿** | 22 篇只有 PDF，本期不处理（需 OCR / Mathpix 路线） |
| **LaTeX 前言依赖** | ✅ **已支持迁移**（第 120 条）：多文件 `\input` 展开、宏包 / tikz 库 / `\newtheorem` 等定义、符号宏自动补包；**边界**：纯离线不解析宏包内部定义，作者重定义宏包命令且正文被用到时仍可能 `already defined`（日志保留原报错，交编辑手工处理） |
| **LaTeX 定义位置** | ✅ **已支持加固**（第 130 条）：本地 `.sty` 五类定义 + 普通宏、本地 `.cls` / `.def` / `.cfg`、正文区定义提示（不迁移）、编译前环境缺失预检；**边界**：纯离线不解析宏包内部定义（预检对宏包自带环境可能误报、只警告不阻断），`\def` / `\let` / `\renewenvironment` 不搬 |
| **规则覆盖率** | 净命中规则 6 条；L2 仍有约 100–150 块/篇待处理，多为语义驱动的标点重排 |
| **两段式协同 GUI（第 55–57 批）** | 建议清单/建议摘要**第二段跑完才生成**；`--coop-apply` 须在点「运行第二段」**前**勾（`phase=done` 后重跑幂等早退）；第 57 批起三类应用（`BIB-*`→bib、`META-*`→`.tex` 的 `\my*`、`FN-*` 窄口径→`.tex` 的 `\parencite`；写前各留 `.ai前`；其余只列建议）；GUI 未做任务书自动分片；第一段只写暂存目录、未跑第二段不算交付；**回填两部分都要交**（完整 BibTeX + 附一 JSON），只交一部分 → 文献回填 0/y + 橙色警告（第 56 批） |

---


---

## 六·五、已并入 EditToolBox（2026-10 完成结构合并）

`EditToolBox` 里新增了「**初排转换**」标签页，可直接调用本工具。
**2026-10 起引擎搬到 `Engine/`**，磁盘上不再有 `slcn-convert/` 这个顶层
文件夹（迁移记录见 `HANDOFF.md` 第 0 条）。

**为什么 shell 出去调 Python，而不是用 Swift 重写**
核心工作量在**直接解析 Word 的 XML**：OMML 公式转 LaTeX、`<w:drawing>` 图片提取、
`<w:tbl>` 表格转 tabular、脚注分类、引文提升……Swift 生态里没有与
`python-docx` / `lxml` 相当的成熟替代，硬写一遍等于把 6600 行逻辑重做且必然引入新 bug。
所以 Swift 侧只做**界面 + 参数拼装 + 找路径**。

```bash
cd EditToolBox && ./build.sh
# → dist/编辑部工具箱.app（26 MB）
```

`build.sh` 会把 `Engine/`（含 `vendor/`）与 `编辑笔记/模板文件/` 一并打进 app 的
Resources（app 内分别是 `Resources/Engine/` 与 `Resources/编辑笔记/模板文件/`），
因此 **app 可以单独拷走运行**，不依赖仓库路径。


> 📌 **本节（原 §六·五）里的第 45–61 条案例已迁到
> [`docs/cases/31-61.md`](cases/31-61.md)**（案例法是只增不改的证据，不跟使用指南混放）。
> 本节余下部分是合并记录与结论，仍然有效。

### 并入过程中踩到的问题（都值得记）

**① `pypinyin` 与 `lxml` 抢 `sys.path`**
vendor 里既有纯 Python 的 `pypinyin`，也有**带编译扩展**的 `lxml`（为某个 Python
版本编译）。早先把 vendor `insert(0, ...)` 到 `sys.path` 最前面，导致 Python 3.12
加载了为 3.14 编译的 lxml：
```
ImportError: cannot import name 'etree' from 'lxml'
```
**改法：用 `append()` 而不是 `insert(0)`** —— 已装的优先，缺的才由 vendor 补，
两种 Python 版本都能跑。同时 `_ensure_vendor()` / `naming.py` 都改成按需挂载。

**② 顺手去掉了 `python-docx` 依赖**
原先有 3 处用 `docx.Document(...).paragraphs`，全部换成自己的 `read_blocks()`
（本来就是为此写的——直接读 XML，还能拿到 OMML 公式）。**app 打包时少一个依赖。**

**③ argparse 的 help 串里 `%` 要转义成 `%%`**
`28% 的稿件` 会被当成格式说明符，Python 3.14 直接抛
`ValueError: badly formed help string`，整个程序起不来。

**④ 模板文件要一起打包**
工具按「**从 `slcn/` 逐级向上找 `编辑笔记/模板文件`**」定位
（`slcn/templates.py: _find_repo_dir()`），所以 `build.sh` 把模板放到 Resources
下同名位置即可，无需改代码。这个上溯写法还有个好处：引擎无论待在仓库里
（`Engine/`）还是 app 包里（`Resources/Engine/`），**两种深度都能命中**
——2026-10 合并时正是靠它避免把层数写死。

**验证（2026-10-13 结构合并后重做）**：

① app 包内、用 **`/opt/homebrew/bin/python3` 3.14**（系统里没有 lxml，靠 `vendor/`
   回退）、在 `/tmp` 这种**中立目录**下完整实跑 Bo_Chen → **20 页**，与人工成品一致；
② 用**真实的 `ConvertFeature.swift` 源码**编一个探针放进 app 包内运行，
   `locateTool()` 两条分支都命中并各自跑完 Bo_Chen 全文（编译 0 报错、20 页）：

   | 分支 | 命中路径 |
   |---|---|
   | app 包内（打包的那份） | `…/编辑部工具箱.app/Contents/Resources/Engine/convert.py` |
   | 从 app 位置逐级上溯（未打包时） | `<仓库>/Engine/convert.py` |


---

## 七、与 EditToolBox 合并（✅ 2026-10 已完成，本节留作设计记录）

用户计划后续把本工具并入 EditToolBox（SwiftUI 原生 app）。为此已做如下准备：

| 措施 | 说明 |
|---|---|
| **引擎与界面分离** | 全部逻辑在 `slcn/` 包内，`convert.py` 只是薄薄一层 CLI，没有任何界面依赖 |
| **清理规则对齐** | `slcn/clean.py` 与 `sources-mac/Features/DeleteTempFeature.swift` 的后缀列表、递归方式、大小写处理、日志文案**逐项一致**，合并时不会出现两套行为 |
| **模板选择可编程** | `slcn/templates.py: resolve(lang, sty, skeleton)` 可直接被 Swift 侧调用（经 CLI 或内嵌解释器） |
| **单篇函数化** | `build_latex(...)` / `build_docx(...)` 都是纯函数式调用，接收显式路径并返回结果对象，便于包进 app 的功能标签页 |
| **日志可注入** | `clean.clean(root, log=...)` 接受回调，对应 EditToolBox 的 `Logger` |

合并时的接入方式有二，各有取舍：

1. **Shell out 到 Python**（推荐先做）：EditToolBox 调 `convert.py`，改动最小；
   但需要在 app 内捆绑 Python 运行时（或用系统 python3 + 依赖检查）。
2. **移植到 Swift**：docx 解析与规则引擎需重写，工作量大，但无运行时依赖。
   风险点是 `python-docx` 的 OMML、样式处理没有等价的成熟 Swift 库。

**结果（2026-10）**：采用了**方案 1（shell out）**——引擎整体搬进 `Engine/`，
Swift 侧只做界面 + 参数拼装 + 找路径，构建时把引擎打包进 app 的 `Resources/Engine/`。
**方案 2 不要做**：理由见 `HANDOFF.md` 第 0 条（Swift 生态没有 `lxml` 的等价替代，
硬写等于把 6600 行逻辑重做且必然引入新 bug）。

---


---

## 八、典型改善：Lu_Jiang

这篇稿子最能说明前述三项修复的价值（人工成品为基准）：

| 指标 | 人工 | 修复前 | **修复后** |
|---|---|---|---|
| 字符数 | 81689 | 72265 | **80284** |
| `\parencite` | 72 | **0** | **68** |
| 带页码定位 | 68 | 0 | **62** |
| `\textit` | 182 | 3 | **323** |
| 行内公式 | 229 | 36 | 66 |
| bib 条目 | 68 | **0** | **68** |
| 页数 | 25 | 21 | **25** ✅ |

参考文献全部来自**脚注**——修复前连 `reference.bib` 都没生成，页数也少 4 页。
修复后**页数与人工成品完全一致**。

三篇样板的终局对照：

| 稿件 | 工具 | 人工 |
|---|---|---|
| Bo_Chen（中文，Word） | 20 页 | 20 页 ✅ |
| Liang_Zhang（中文，LaTeX） | 18 页 | 18 页 ✅ |
| Lu_Jiang（英文，Word） | **25 页** | **25 页** ✅ |

---


---

## 九、下一步

1. **第 1 期**：把 PoC 固化成引擎，补 OMML 公式转换、译名对照表、编辑侧字段读取；
   彻查剩余英文稿的低分原因
2. **第 2 期**：接入云端 API 做 L3 层的「建议 + 人工确认」（**绝不自动改稿**）
3. **第 3 期**：~~并入 EditToolBox 或独立 app~~ ✅ **2026-10 已完成**——
   引擎在 `Engine/`，与界面同属一个项目（见第 0 条迁移记录）；
   剩余：Windows 版复用同一 Python 引擎
4. **候选任务（未排期，2026-10-14 深夜发现）**：**语料 bib 语法体检** —— `out/corpus/**/reference.bib`
   **62 份**里 **11 份（18%）语法/结构本身就有问题**（**6 份花括号不配平 / 5 份重复 key /
   3 份字段名是数字**）；两个选项（**修语料** 或 **给 `bibcheck.split_entries` 加健壮性处理**）
   见第 45 条的候选任务块。
5. **候选任务（未排期，2026-10-15 凌晨发现，做「摘要」标签时顺带记录的既有长尾）**：
   ① 校对说明行（如 `摘要部分，共2处`）仍会被当成摘要（2 份**口径外**文件）；
   ② `Abstract. ` 这种「标签后只有一个句点」让 `abstract_en` 取到 `'. '`，而普查按"标签存在"仍记 `ok`
   —— 判据与取值不一致。两条都是**既有**问题（与第 56 条的改动无关），见第 56 条末段。
   （task-7 已清账；PDF-only 21 篇见第 45 条、`HANDOFF.md` §六。）
