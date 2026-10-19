# 引擎接口契约（ENGINE_API）

**这份文件是什么**：`Engine/`（Python，唯一一份逻辑）与**外壳**（mac 的 SwiftUI、Windows 的
Tkinter，以及将来任何前端）之间的**契约**。外壳只做三件事：找路径、拼参数、显示日志——
**逻辑一行都不许复制进外壳**。

> ⚠️ **改引擎的 CLI / 退出码 / 产物路径 / 关键输出行 → 必须同时改这份文件与两个外壳。**
> 反之：新增参数**一律要带默认值**，老外壳不传也能跑（这条是硬要求，见 `build_docx(metadata_path=None)`
> 与 `--llm-search-tool` 的先例）。

---

## 一、引擎在哪、怎么被调用

```text
<仓库>/Engine/                 ← 唯一一份逻辑（convert.py + checkbib.py + mergepdf.py
                                                 + cleantemp.py + compileall.py + proofcheck.py + slcn/）
<仓库>/Engine/engine_main.py   ← 打包用：把**十个** CLI 冻成**一个** exe 的分发入口（第 89 条）
<仓库>/Engine/vendor/          ← 随包依赖（pypdf / pypinyin / lxml）
<仓库>/sources-mac/                ← mac 外壳（SwiftUI）
<仓库>/sources-win/           ← Windows 外壳（Tkinter，见 §七）
```

**调用方式**：外壳起一个**子进程**，用**绝对路径**调 `convert.py` / `checkbib.py` / `mergepdf.py`。
发布形态下这十个入口被冻成一个 exe，外壳改调 `SLCNEngine.exe <入口> …`（见 §九·打包）。
⚠️ `engine_main.py: ENTRIES` 与外壳的 `sources-win/engine.py: ENTRIES` **必须逐项一致**：
漏一个，那个外壳页在用户机器上就是「❌ 不认识的入口」退出 2（2026-10-17 实测坏了 5 页）；
配对守卫在 `sources-win/tests/test_bridge.py` 与 `Engine/tests/test_platform.py` (g) 组。
引擎**不做源文件自动发现**（`--source` 必填），也**不假设 CWD**（内部一律用绝对路径）。

```bash
# 外壳必须做的最小环境准备（顺序无所谓）：
PYTHONPATH=<…>/Engine/vendor      # vendor 依赖
PYTHONUNBUFFERED=1                # ⚠️ 不设的话日志会按 8KB 块缓冲，界面看着像卡死
SLCN_LLM_KEY=…                    # 密钥**只走环境变量**，绝不进 argv（argv 在 ps 里谁都能看）
PYTHONIOENCODING=utf-8            # Windows 尤其要设；引擎内部也会自己 reconfigure（见 §七）
```

- **解释器**：Python ≥ 3.11（开发机用 3.14）。Windows 打包时用 PyInstaller 把解释器一起打进去。
- **标准输出**：外壳**逐行读、实时显示**（第 82 条：块缓冲会让「联网核实」看起来像卡死）。
  stdout 与 stderr 都要收（引擎的致命错误走 stderr）。

## 二、入口 1：`convert.py`（初排转换）

```bash
python3 convert.py --source <稿件.docx|.tex> --out <输出根目录> [--compile] [--clean] …
```

| 参数 | 说明 |
|---|---|
| `--source` | **必填**。`.docx` / `.doc` / `.tex` |
| `--out` | **必填**。产物写到 `<out>/<作者名>/` |
| `--metadata` | LaTeX 多源合并的元数据源；**Word 源**时指到 `.docx` 会被当「英文元数据第二源」（第 76 条） |
| `--refs` / `--bib` / `--name` / `--author` / `--lang` / `--sty` / `--skeleton` | 见 `convert.py --help`。⚠️ 第 134 条起 `--refs` 的自动发现改保守：**有真 `.bib`**（`--bib` / 源内 `\addbibresource` / `\bibliography` 指向且文件存在 / 同目录 `.bib`）时**不自动合并**附件（要合并请显式 `--refs`）；无真 bib 时自动挑候选（真 BibTeX 优先，文本清单须过结构校验：拒 `\href{...}` / 相邻 `}{` / 疑似一行两条），日志逐条写「采用 / 跳过 + 原因」 |
| `--vol --issue --year --pages --received --editor --grants` | 编辑侧出版信息，不填就留模板默认值 |
| `--compile` `--clean` `--source-pdf` | 编译出 PDF / 清中间文件 / 导出原稿 PDF（供矢量形状截图） |
| `--verify-refs` `--no-ai-merge` | 出《文献核实任务书》/ 只出任务书不并 bib（第 64 条）。⚠️ 第 128 条起任务书、检索与 AI **默认只覆盖正文引用到的条目**（不是全量，见下一行） |
| `--verify-all-refs` `--verify-max N` `--verify-plan` | **核实范围与成本闸门（第 128 条）**。默认只核**正文引用到的键**（产物 `.tex` 的 cite 命令；`\nocite{*}` = 全量；解析不出引用键时退回全量并警告）。`--verify-all-refs` = 显式全量（越过闸门）；`--verify-max` = 本次条数上限（**默认 120**，`0` = 不限），超过且未显式全量 → **退出 2**（stderr 给三条可操作的路，任务书/检索/AI 一律没开始）；`--verify-plan` = 只打印范围与预计检索/AI 次数后**退出 0**（不生成任务书、不检索、不调 AI；转换产物照常写出，**不是** `--dry-run`）。外壳不传时行为与默认值相同（老外壳兼容） |
| `--search-refs` | 用 DeepSeek 独立 Search API 给**本次核实范围内**的参考文献检索证据：写进任务书 +（P2，第 72 条起）注入 AI 逐条提示词 + 合并前校验 `% 来源：` URL 白名单（默认关；开启时才计费 ¥5/1000 次；地址/密钥复用 `--llm-*`，也可 `SLCN_SEARCH_BASE` / `SLCN_SEARCH_KEY`；环境变量 `SLCN_SEARCH_REFS=1`）。外壳：mac 在 AI 运行时传 `--search-refs`；Windows 把 `SLCN_SEARCH_REFS=1` 放进子进程环境 |
| `--bibdb` `--bibdb-path PATH` `--no-bibdb` | **编辑部引用库（第 129 条）**。执行核实（`--verify-refs` / `--verify-plan`）时**默认**在默认库存在时自动查库（默认库 = 笔记仓 `bib_db/bibliography.jsonl`，`slcn.bibdb.default_db_path()`）：命中**已核实**条目的键**不调检索/AI**、**不改 `reference.bib`**，字段差异只写进任务书「五、库内已核实」。`--bibdb` = 显式启用（库缺失/读不了只告警、不中断出稿）；`--bibdb-path` = 指定库文件（隐含启用）；`--no-bibdb` = 显式关闭（输出与接入前逐字节相同）。笔记仓/默认库不存在时**静默跳过**（公开仓/CI 行为零变化）；不核实时这些参数只提示忽略。判定保守：key 命中只算候选（仍走检索），详见 `docs/GUIDE.md` §3.10。第 58 批另加 `--no-bibdb-verify` = 关闭**初排默认只读比对**（只影响日志一行；见下「引用库比对」行） |
| `--llm-base --llm-model --llm-protocol --llm-web-search --llm-search-tool --llm-responses-path` | AI 通道（密钥走环境变量） |
| `--coop-fulltext {context,full}` | 第 59 批，可选、**默认关**。只配合 `--coop-taskbook-only`：第一段任务书在「附二」后追加「附三、正文全文/引用上下文」；`context` = 引用/脚注锚点前后各 300 字窗口合并（上限 4 万字符≈13k tokens）、`full` = 正文全文（上限 8 万字符≈27k tokens），超限显式截断。只写本机任务书（软件零上传），不新增改正文类建议；其他组合退出 2。见下方「两段式协同 argv 契约」 |
| `--version` | 打印 `SLCN 引擎 <版本> ｜ <系统>，Python <版本>`（外壳的「关于」直接用） |

**退出码**：`0` = 成功；`2` = 用法/IO/致命错误（`die()`，消息在 **stderr**）。
**没有 1**：中途某一步失败也算失败，一律 2 —— 外壳判「成功」只看 `0`。
⚠️ 具体到 `--compile`：**⑨ 编译未通过 = 失败 = 退出码 2**（`.tex` 产物照样留着，
消息走 stderr；stdout 的**最后一行仍是契约行** `完成。产物目录：…`）。
这条路 2026-10-17 之前是 `return 0`：界面上显示 ✅ 完成，而 PDF 其实没编出来
（案例第 107 条）。
⚠️ 具体到 `--verify-refs`（第 128 条）：本次核实条数超过 `--verify-max` 且未加
`--verify-all-refs` → **退出码 2**（闸门在生成任务书 / 检索 / 调 AI **之前**；stderr 给出
「调上限 / 显式全量 / 先看账」三条路，stdout 已有跑前量化行）。**这不是 `--compile` 那种
「做完了但失败」**：转换产物照常写出，只是核实这一步没开始。

**产物写入（2026-10-17 起）**：生成/覆盖 `.tex`/`.bib`/报告这类产物时走 `slcn/fsutil.py` 的 `atomic_write()`
—— 先写同目录 `.tmp-<pid>`、`fsync` 后 `os.replace`（POSIX `rename(2)` 保证读者只看到完整旧版
或完整新版），替换前把旧产物备份成 `<文件>.<时间戳>.bak`（每个文件留最近 **3** 份）。
所以跑完后同目录里**可能出现 `.bak`**，那不是垃圾，是上一版（诊断「以为存上了其实没有」用）。

**stdout 的最后一行**：`完成。产物目录：<out>/<作者名>`（其余日志都在它之前）。

**stdout 的约定**（外壳可以只认这几类，**其余原样显示**）：

| 形态 | 含义 |
|---|---|
| 行首 `①②…⑫` | 粗粒度阶段（解析 / 引用 / 模板 / 正文 / 预校正 / 写文件 / 归档 / 任务书 / 编译 / 原稿 PDF / 清理） |
| 行首三个空格 | 该阶段内的细节（外壳可缩进显示） |
| 行首 `   [i/N] <key>：正在核实…` | 第 82 条的**逐条进度**（最慢的一步在等端点时唯一的活动迹象） |
| 行首 `❌` | 致命错误（同时走 stderr） |
| 行首 `⚠` | 警告；收尾还会汇总在「⚠ 注意事项：」一节 |
| 行首 `   ⚠⚠` | **模型能力前置提示**（第 128 条）：开了联网检索、且模型**已知会静默忽略** `web_search`（如 `deepseek-flash` / `deepseek-chat`）时，在 **AI 批量开始前** 打一行；跑完后「0 个 `web_search_call` → 按凭记忆对待」的事后提示**照旧保留** |
| 行首 `   引用库：` | **引用库查库统计**（第 129 条）：`引用库：命中 X 条已核实（跳过检索+AI）、候选 Y、未命中 Z（库 n 条，<path>）`。只在执行核实且启用查库时出现；`--no-bibdb` / 库缺失时不出现（老外壳不受影响） |
| 行首 `   引用库比对：` | **初排默认只读比对**（第 58 批）：未执行核实时，默认库存在且产物 bib 有条目时打一行 `引用库比对：命中 X / 候选 Y / 未命中 Z（只读，不改产物）`；无库/无 bib/读库失败静默；`--no-bibdb-verify` / `--no-bibdb` 关闭。只增这一行，产物/任务书不变 |
| 行首 `   本地支撑文件：` | **定义位置加固**（第 130 条，仅 LaTeX 通道）：本次读了哪些本地 `.cls` / `.sty` / `.def` / `.cfg`（作者稿同目录）。Word 通道不出现 |
| 行首 `   本地旧模板 .sty（源目录有同名文件` | **本地非标准命名旧模板**（第 130 条补丁）：该 `\usepackage` 的包行**不迁**（模板负责顶部加载），文件按支撑文件差集搬定义 |
| 行首 `   计数器迁移 N 个：` | **`\newcounter` 迁移**（第 130 条补丁）：只在「正文或已迁移定义体用到」时搬；先于用它的环境定义 |
| 行首 `   本地旧模板定义迁入 N 个：` | 第 130 条：从本地旧模板文件按「正文用到 + 模板没有」迁入的定义名 + 来源文件 |
| 行首 `   ⚠ 本地旧文件重定义了模板命令` | 第 130 条：本地旧文件里覆盖现行模板命令的 `\renewcommand`（正文用到）**未搬**（模板优先），同时进「⚠ 注意事项」 |
| 行首 `   正文里出现 N 处自定义定义` | 第 130 条：正文区（`\begin{document}` 之后）的定义**保留原样**（不迁移），只是提示建议移到导言区；Word 通道不出现 |
| 行首 `   ⚠ 定义缺失预检：` | 第 130 条：编译前核对正文用到的环境是否被「模板 + 迁移结果 + 正文自身定义」覆盖；缺失只警告（同时进「⚠ 注意事项」）、**不阻断出稿**，退出码不变 |
| 行首 `  · 参考文献：` / `  → 自动采用参考文献附件：` / `  · 跳过参考文献附件：` | **参考文献附件选择**（第 134 条）：有真 `.bib` 时说明直接使用、逐个列出被跳过的附件；无真 bib 时说明采用的候选；被结构校验拒绝的候选写明原因（`\href{...}` / 相邻 `}{` / 疑似一行两条） |
| 行首 `   从附件合并 N 条` / `   ⚠ 跳过参考文献附件` | 第 134 条：合并结果（BibTeX 直入 / 文本清单）与「不硬并坏附件」的警告；坏条目（嵌套花括号等）不会写进产物 |
| `完成。产物目录：<绝对路径>` | **最后一行**（外壳用它做「打开产物目录」按钮） |

**LaTeX 定义位置加固（第 130 条，2026-10-18）**：LaTeX 通道的 stdout 可能多出上表几行
（Word 通道**没有**）。它们全是**新增行**：不改任何既有契约行、退出码、产物路径与文件名；
普通稿子（没有本地支撑文件、正文无自定义定义、环境全可解析）日志与产物逐字节不变。
口径见 `docs/RULES.md` §八、`docs/GUIDE.md` §3.1；回归见 `tests/test_latex_preamble.py` ⑦ 组。

**参考文献附件选择（第 134 条，2026-10-18）**：`convert.py` ⑦ 的**自动**附件发现改为
「有真 `.bib` 就不并」（`--refs` 显式给时才并）；没有真 bib 时优先真 BibTeX、文本清单须过
结构校验，坏候选跳过并写明原因；合并结果逐条再做「渲染后结构校验」（坏条目不写，防 biber
整份报废）。全是**日志行**：不改退出码、产物路径与既有契约行；默认没有附件候选的稿子输出
逐字节不变。口径见 `docs/RULES.md` §四.9；回归见 `tests/test_refs_attach.py`；
真实稿 2023(71)04 用默认路径编过（biber 0 错、20 页 = 人工）。

**两段式协同（`--coop-*`）的 argv 契约（第 54–59 批，外壳拼参数用）**：第一段 =
`--verify-refs --no-ai-merge --coop-taskbook-only [--coop-stage DIR] [--coop-fulltext context|full]`；
第二段 = `--coop-replies <回填>… [--coop-apply] [--compile] [--clean]`（`--source` / `--out` 两段都必填）。
`--coop-fulltext` **只能随第一段**（否则 exit 2）、**默认不传**；两壳只在两段式面板勾选「全文 AI 核查」且
第一段时追加 `--coop-fulltext context|full`，第二段与普通转换绝不带（mac `tools/ai_settings_test` / win
`sources-win/tests/test_bridge.py` 钉住）。任务书 = `refmark` 任务书 + 附一 + 附二 +（可选）附三；
**附三只用于回答附一、不新增改正文类建议**，默认关时任务书/stdout 与批 58 逐字节相同；
通道②（直连 API 自动发全文）尚未实现。

**核实范围（第 128 条，2026-10-18）**：`--verify-refs` 的默认口径是「**只核正文引用到的
条目**」——⑪ 跑前会在 stdout 打一行
`源 bib N 条；正文引用 M 条 → 本次核实 M 条（跳过 N-M 条未引用）`；
任务书逐条清单、`--search-refs` 检索与 AI 预跑都只覆盖这 M 条；转换产物（`.tex` /
`reference.bib`）不受影响（不加新参数时逐字节不变）。全量核实用 `--verify-all-refs`
（越上限用 `--verify-max` 调，`0` = 不限）；`--verify-plan` 只算账退出 0。
口径细节见 `docs/GUIDE.md` §3.10，回归见 `tests/test_verify_scope.py`。

**先查编辑部引用库（第 129 条，2026-10-18）**：执行核实时，⑪ 先查编辑部自己的引用库
（私有笔记仓 `bib_db/bibliography.jsonl`；来源 = 往期已出版期的成品 bib）。**安全命中**
（DOI 精确命中已核实记录；或题名高置信 + 第一作者姓/年份相容 + `verified=true`）的键
**不调检索 API、不调 AI**，且**不改 `reference.bib`**；库版本与稿件条目的字段差异只写进
任务书「五、库内已核实（无需再查）」，由编辑裁决。**key 命中只算候选**（BibTeX key 多为
per-paper），仍走检索/AI；库内未核实/低置信同样只算候选。跑前 stdout 多一行
`引用库：命中 X 条已核实（跳过检索+AI）、候选 Y、未命中 Z（库 n 条，<path>）`；
`--verify-plan` 的预计检索/AI 次数按**扣减后**的条数算；任务书「六、合并记录」把
「库内命中（未调 AI）」与「正文未引用（范围外）」分开统计。默认口径 = 执行核实且默认库
存在时**自动启用**（笔记仓/库不存在时静默跳过）；`--bibdb` 显式启用（库缺失只告警，
不中断出稿）、`--bibdb-path` 指定库文件、`--no-bibdb` 显式关（输出与接入前逐字节相同）。
**不核实（默认）路径逐字节不变**。库的 schema/来源口径/种子导入见笔记仓
`bib_db/README.md`；用法见 `docs/GUIDE.md` §3.10；回归见 `tests/test_bibdb_verify.py`。

**产物**（`<out>/<Author_Name>/`）：

```text
<Author_Name>.tex            初排稿
<Author_Name>.pdf            --compile 才有
reference.bib                参考文献（勾了 AI 并回时**已被机器改过**）
reference.bib.ai前           本次合并**之前**的那一份（回滚用；重跑会覆盖）
SLCN-MIX.sty / SLEN.sty      与 --sty 一致
original/                    源稿归档副本（只读副本，不是原件）
<Name>_文献核实.md           任务书（含「六、合并记录」）
<Name>_回读校验报告.md       并入后自检（结论一行同时打在 stdout）
<Name>_参考文献核对.txt      离线核对（缺哪些字段 / 引用对不对得上）
<Name>.log                   xelatex 日志（**故意不删**）
```

## 三、入口 2：`checkbib.py`（文献回读校验 / 并回）

```bash
python3 checkbib.py --returned <交回.bib> [--original <原件.bib>] [--tex <正文.tex>] [--out <报告.md | 目录>]
# --out 填**已存在的目录**时，报告按默认文件名（<交回文件基名>_回读校验报告.md）放进该目录
python3 checkbib.py --returned <交回.bib> --merge-into <产物 reference.bib>
pbpaste | python3 checkbib.py --returned-stdin --merge-into <产物 reference.bib>   # 粘贴模式
```

**退出码**（三种模式含义不同，口径与判据见 `docs/RULES.md` §四.6）：
体检 `0` 无问题 / `1` 有问题 / `2` 用法或 IO 错误；
并回 `0` **未改动** / `1` 已并入 ≥1 条 / `2` 用法或 IO 错误；
列表（`--list-keys`）`0` 打印了 / `2` 出错。`--version` 同 `convert.py`。

## 四、入口 3：`mergepdf.py`（合并一期的 PDF，第 88 条）

```bash
python3 mergepdf.py --folder "<期目录>" [--out <合并.pdf>] [--nest-outlines] [--editors-page] [--back-cover]
```

- **`--folder` 指的是「期目录」**（里面有 `扉页.pdf`、`000 …/`、`01 作者/` …），
  **不是**单篇稿件的目录。默认写成 `<期目录>/合并<时间戳>.pdf`（时间戳格式与 mac 外壳一致）。
- **合并顺序**：扉页 → 编委页（可选）→ `000…/Chinese Content.pdf` → `000…/contents.pdf` →
  各篇文章（按 `NN` 序号）→ 封底（可选）。**缺文件不会中断**，只在 stdout 打 `⚠ …没找到`。
- `--nest-outlines`：各篇 PDF 自带的**小节书签**挂到该篇文章的书签之下；
  **默认平铺在顶层**（与原版 PyPDF2 的行为一致，mac 外壳默认也是这个）。
- **编委页 / 封底只并页面、不导入它们自带的大纲**：实测编委页文件里留着一个陈旧书签叫
  「Chinese Content」，导进来会出现两个同名书签指向不同页。识别**只看文件名**
  （这些页是印刷厂曲线化过的，没有文字层）：编委页 `编委页.pdf` → `cneditor*` → `扉页2.pdf` →
  名字含「编委」；封底 `cover+bottom*` → 名字同时含 `cover` 与 `bottom`。
  工具箱自己生成的 `合并*.pdf` **一律排除**，不会被当成候选。
- **退出码**：`0` 合并成功（可能带 `⚠` 警告）；`2` 期目录不存在 / 写不出去。
- **最后一行**：`完成。合并文件：<绝对路径>（共 N 页）` —— 外壳靠它拿产物路径与页数。
- 逻辑在 `slcn/pdfmerge.py`。**两个外壳都已改调它**（第 99 条）：
  mac 侧原来的 `PDFMerger.swift` / `ExtraPages.swift` / `EditorsPage.swift` **已删除**，
  `sources-mac/Features/MergeFeature.swift` 只剩「拼参数 + 转发 + 解析 `--detect` 的预览」。
  切换前两条路（Swift + PDFKit 那条与引擎这条）的输出已用真实成品期目录**逐条比对过**
  （176 页、102 条大纲、层级/名字/页码全同，见 `tests/test_pdfmerge.py` 的 (f) 组）。
- **`--detect`**：只探测不合并，输出「会插哪一份编委页/封底页、扉页页数」的 JSON ——
  界面运行前的预览用它，**别在界面里自己扫文件**（否则会出现"预览说找到 A、合并时插了 B"）。

## 五、入口 4–10：期目录级的动作、一校核查、检查tex信息、生成目录、本期出刊与引用库（第 91、92、94、132 条）

```bash
python3 cleantemp.py   --folder "<期目录>" [--dry-run] [--trash]
python3 cleantemp.py   --folder "<期目录>" --restore "<.toolbox-trash/时间戳 目录>"
python3 compileall.py  --folder "<期目录>" [--only 01,03] [--dry-run]
python3 proofcheck.py  --tex "<初排稿.tex>" [--lang zh|en] [--out <报告.md>] [--fix]
python3 infocheck.py   --folder "<期目录>" [--out-dir <目录>]
python3 infocheck.py   --folder "<期目录>" --preview   # 界面用：检查结果 JSON（不写任何文件）
python3 toc.py         --report "<信息检查.txt>" [--template <期目录>] [--settings <目录设置.json>]
python3 toc.py         --report "<信息检查.txt>" --preview     # 界面用：篇目清单 + 模板来源（JSON）
python3 publish.py     --folder "<期目录>" [--only N] [--from N]   # 本期出刊向导（一步跑完一期）
python3 bibdb.py       <子命令> [参数…]   # 编辑部引用库（子命令见下）
```

| 入口 | 干什么 | 退出码 | 最后一行（外壳靠它拿结果） |
|---|---|---|---|
| `publish.py` | **本期出刊向导**（一步跑完一期）：按序跑 ① 检查tex信息 → ② 生成目录 → ③ 合并整期 PDF → ④ 编译全部。**编排只在引擎里**（外壳不许多写一份流程；项目红线「一个引擎、两个外壳」）。失败语义（用户 2026-10-17 拍板）：某步退出码 **2 ⇒ 中止**（前 N−1 步产物保留，末行写明卡在第几步）；某步退出码 **1 ⇒ 橙色警告、继续**（INVARIANTS 三.2 的"有结果"）；`--only N` / `--from N` 单步重跑 | `0` 全部完成；`1` 完成但有警告项；`2` 中止/用法错误 | `完成。出刊：N 步都跑完[，警告 N 项]（目录：…）`／中止时 `完成。出刊：卡在第 N 步（<步骤名>）；前 N−1 步产物有效（目录：…）` |
| `cleantemp.py` | 递归处理 LaTeX 编译中间文件（`.aux .bbl .bcf .blg .out .run.xml .synctex.gz` + `.DS_Store`）；**不删** `.tex/.pdf/.sty/.bib/.log`；**且永不进入 `original/`**（作者来稿目录，红线只读 —— 作者交 LaTeX 稿时那里真可能有同名 `.bbl`/`.out`，删掉就是丢稿）。三种模式：**默认直接删**（老行为，mac 外壳走这条）；`--trash` **移入** `<目录>/.toolbox-trash/<时间戳>/`（保留相对路径 + `manifest.json`，可撤销）；`--restore <回收目录>` 搬回去（**原位置已有同名文件则跳过、不覆盖**） | `0` 成功；`2` 目录不存在 | 删：`完成。已删除 N 个临时文件（目录：…）`　移入回收：`完成。已移入回收目录 N 个临时文件（回收目录：…）`　撤销：`完成。已恢复 N 个文件（目录：…）` |
| `compileall.py` | 对每个 `NN 作者/` 子目录跑 `xelatex → biber -l=zh_pinyin → xelatex → xelatex`；跳过 `000 目录`；某篇失败**只停那一篇**并回显 `.log` 里的 `!` 摘要 | `0` 全成功；`1` 有失败；`2` 用法/目录错误 | `完成。编译 N 篇（成功 X / 失败 Y）（目录：…）` |
| `proofcheck.py` | **一校核查**：按《排校手册》《流程化经验书》逐条核查初排稿（26 条规则，见 `docs/PROOF_RULES.md`）；**默认只报不改** | `0` 无命中；`1` 有命中（正常）；`2` 用法/文件错误 | `完成。一校核查：命中 N 处（可自动改 M 处）（报告：…）` |
| `infocheck.py` | **检查tex信息**：汇总各篇 tex 里的卷期年/页码/题目/作者/字号/行距 + PDF 实际页数；`--preview` 只输出检查结果 JSON（篇目 + `inconsistencies`），**不落任何文件** | `0` 成功（或 preview 成功）；`2` 用法/目录错误 | `完成。检查 N 篇（有 tex X / 有 pdf Y；不一致 Z 项）（报告：…）` |
| `toc.py` | **生成目录**：读 `infocheck` 的报告产出中英文目录 .tex（模板继承上一期成品）；`--preview` 只输出篇目清单与模板来源的 JSON（给界面用，不写文件）。⚠️ **先校验"这确实是一份检查报告"**：解析出 0 篇就退出 2、**一个文件都不写** —— 从前喂错文件（如某篇 `.tex`）会静默产出两份「第 ? 卷，第 ? 期」的空目录并退出 0，而产物落在**报告所在目录**（真跑时就是作者目录） | `0` 两份都生成（或 preview 成功）；`1` 两份已生成但有需人工核对项（只生成了一份 / 报告不在期目录里导致「总第 ? 期」无法确定）；`2` 用法/文件错误、或报告不可用 | `完成。生成目录 N 份（<目录>）` |
| `bibdb.py` | **编辑部引用库**（第 132 条；两个平台的「引用库」5 页全调它）：`init` 建库；`import-bib` / `import-dir` 导入 `.bib` 或整期成品；`query` 查库（`--json` 给界面）；`stats` 概况；`lint` 体检；`resolve` 记录冲突裁决（keep/defer/adopt）；`normalize` / `normalize-authors` / `normalize-editors` 是 B1/B2/B3 规范化（**默认 dry-run，`--apply` 才写库**，写库前须 `--backup` 并校验 sha256）。库默认在私有笔记仓 `bib_db/bibliography.jsonl`，找不到笔记仓直接退出 2、绝不悄悄写到别处；`--db` 可显式指定 | `0` 成功；`1` 有需人工处理项（如 `lint` 有问题、`resolve` 留下 defer）；`2` 用法/库错误、或找不到笔记仓 | 人读子命令打印小结；`--json` 时 **stdout 是 JSON**（外壳读 JSON、不解析文本） |

**产物**：
- `cleantemp` / `compileall`：原地操作（前者删文件、后者生成 `.pdf` 与 `.log`）。
- `proofcheck`：同目录写 `<stem>_一校核查.md`（报告）；
  加 `--fix` 时**另写** `<stem>_一校核查后.tex`——**输入文件一个字节都不动**。
  `--fix` 只改 `fixable` 命中（`slcn/proofcheck.py` 的 `RULES` 里标 `fixable=True` 的那些）；
  `slcn/proofcheck.py` 导出 `apply_fixes(tex, rep, rule_ids=…)` —— `rule_ids` 传显式 id 子集时
  只改这些规则（E1 首批窄白名单 = `ZH-01..06`；传 `()` = 一处不改）。另导出两个常量：
  `INITIAL_LAYOUT_FIXABLE`（初排转换接线的**完整建议集**，后批再扩；**不含 ZH-08**）
  与 `OPT_IN_FIXABLE`（只在人工 `--fix` 时生效，目前是 `ZH-08` 的汉字-汉字窄子集，
  2026-10-18 起；连接号口径未拍板前默认初排不改产物）。
- `infocheck`：期目录根下写 `信息检查<时间戳>.txt`（**CRLF**，与原版/mac 版逐字节一致）
  与 `.json`（结构化，供 toc 与界面用）；可用 `--out-dir` 挪走。
- `toc`：报告同目录写 `Chinese Content_<时间戳>.tex` 与 `contents_<时间戳>.tex`
  （**先英文后中文**，与原版 `run_tab5` 一致）。
  本期设置（栏目/主持人/标题断行上限）读同目录的 `目录设置.json`，缺失字段逐项回落默认值（中文断行 20 / 英文断行 30 —— 英文值 2026-10-17 按期 90 成品校准，见案例第 110 条）。

**给外壳的注意**：`compileall` 是长任务（每篇几十秒），必须用第 82 条的实时日志；
`proofcheck` 的 `1` 表示「有命中」而不是失败，外壳不要把它显示成错误；
`toc` 的篇目预览必须调 `--preview`（**别在界面里自己解析报告**），否则会出现
「清单里勾了 3 篇、生成时只认 2 篇」这种对不上的情况。

**mac 界面的切换进度（第 95 条起，按用户定的顺序，第 99 条完成；引用库 5 页按第 132 条补）**：
**十五页全部改调引擎** ——
初排转换 / 文献回读校验 / 一校核查 / 生成目录（95）/ 检查tex信息（96）/
删除临时文件 + 编译全部（98）/ **合并文件（99）** / **本期出刊** /
**引用库 5 页**（库概况 / 查库 / 入藏 / 待裁决 / 引用库设置，第 132 条）。
`sources-mac/` 里已经**没有任何文档逻辑**：界面只做「取值 → 拼参数 → 显示日志/表格」。
切一个删一份 Swift 逻辑（`ContentFeature.swift` + `TocTemplate.swift` 已在第 95 条删除；
`InfoCheckFeature.swift` 在第 96 条瘦身成"只拼参数 + 解产物"）。

界面侧的通用约定（切过两页后总结出来的）：
· **表格/清单数据一律问引擎**（`--preview`），界面不自己解析 —— 否则表格与报告会对不上；
· 只有**展示用的聚合**（如不一致项提示串）可以留在界面，判据必须还在引擎里；
· 界面把设置写进引擎要读的那个文件（如 `目录设置.json`），**不要用命令行传设置**，避免两个真相。

## 六、开发用入口（**不随 App 分发**）

`docx_audit.py`（全库体例普查，**冻结基线**：改解析器必须 `--out-dir /tmp/…` 或 `SLCN_AUDIT_OUT`）、
`corpus.py`（全库回归，约 10 分钟）、`ablation.py` / `ablation_corpus.py`（规则消融：单篇版会过拟合、
**规则取舍以 `ablation_corpus.py` 全库抽样为准**）、`eval_quotation.py`（引文环境转换专项评测：
查全率 / 查准率）、`run_poc.py`（端到端 PoC 驱动 + 打分报告）、`refaudit.py`（全库参考文献质量普查，纯离线）。
这些只在开发机跑，**不要**打进 Release 资产。

## 七、平台矩阵（第 86 条：引擎平台中立化的口径）

| 需要什么 | macOS | Windows | Linux |
|---|---|---|---|
| Python | ≥ 3.11（开发机 3.14） | 同（PyInstaller 打进去） | 同 |
| TeX | TeX Live / MacTeX（`xelatex` + `biber` 在 PATH） | TeX Live / MiKTeX（同上） | TeX Live |
| LibreOffice | 可选（「导出原稿 PDF」＋ Word 的 MathType/OLE 公式图 WMF/EMF → PDF） | 可选（同） | 可选（同） |
| `vendor/` | mac 轮子（`lxml` 是编译件） | **必须是 Windows 轮子**（打包时按平台装） | Linux 轮子 |

**引擎已经做掉的平台事**（`slcn/platform.py`）：

1. **UTF-8 标准输出**：`convert.py` / `checkbib.py` 进 `main()` 第一件事就 `utf8_stdio()`。
   Windows 的 stdout 默认是控制台代码页（cp936），中文日志经管道会乱码、甚至
   `UnicodeEncodeError` **崩在转换中途**；macOS/Linux 上是空操作。
2. **LibreOffice 定位**：`find_soffice()` = `SLCN_SOFFICE` 环境变量 → `PATH` → **三平台**常见安装位置
   （Windows 的安装器**不**把 `soffice` 写进 PATH；macOS 的 `.app` 同理）。
   **fail-soft（第四十七批起）**：缺 `soffice` 时**不报错、不阻断出稿** —— Word 通道把 MathType/OLE
   公式的 WMF/EMF 预览图渲染成 `\IfFileExists{figN.pdf}{\includegraphics…}{\fbox{\scriptsize 公式图缺失}}`
   占位（`slcn/docxsource.py`），并在初排「注意事项」里 warning
   `N 个公式/图片未能导出（WMF/EMF 需要 LibreOffice soffice 转换…）`（`slcn/build.py`）。
   LibreOffice **不随包分发**，用户自查自装（`soffice --version`；见 `使用说明.md` §一）。
   同理，「导出原稿 PDF」（矢量形状人工截图用）也依赖它，缺它时该步骤跳过并返回说明字符串。
3. **`file://` URI**：LibreOffice 的 `-env:UserInstallation=` 只认 URI，
   Windows 盘符与**带空格的路径**都必须转义 —— 统一走 `platform.file_uri()`（`Path.as_uri()`）。
4. **文本 IO 一律显式 `encoding=`**：交付模块里**没有**一处「文本模式 `open()` 不写编码」
   （由 `tests/test_platform.py` 的静态守卫盯着）。这条是 Windows 上读中文源稿乱码的根因。

**已知的平台注意点**（还没做，写在这里免得踩）：

- **长路径**：Windows 默认 260 字符上限。稿件目录名很长（`2026年第5期(90)/07 Liangying_Zhao/original/…`）
  套上产物目录可能超限；真要踩到，用 `\\?\` 前缀或让用户在浅目录里出稿。
- **`tools/gen_docs.py` 写死了 mac 的 Python 路径**（`/opt/homebrew/bin/python3.14`）——
  它是**开发工具**，Windows 上要么改常量、要么用 `python`（不影响 App）。
- **字体**：模板 `fontset=windows` 指定 Times New Roman / KaiTi，Windows 上本来就是这套；
  macOS 用户需自备同名字体（既有行为，未变）。

## 八、外壳的义务（写下来，省得两个外壳各做一半）

1. **只调 CLI，不复制逻辑**：引擎只有一份；规则改动只在 `Engine/` 里改。
2. **UTF-8 管道**：设 `PYTHONIOENCODING=utf-8`，按 UTF-8 解码子进程输出（否则中文乱码）。
3. **实时读**：`PYTHONUNBUFFERED=1` + 逐块读 + 自动滚到底（第 82 条）。
4. **密钥只走环境变量**，绝不拼进 argv，也不写进日志（界面只显示前 4 位）。
5. **不要解析未写进本文件的输出**：白名单是 §二 的那几类；其余原样显示。
6. **退出码只看 0 / 非 0**；错误消息在 stderr，要一起显示。
7. **绝对路径**：源文件、输出目录、`--returned` 等一律传绝对路径；不依赖 CWD。
8. **别改产物**：产物目录里的文件由引擎负责，外壳只读、只打开（唯一例外是用户显式点的「删除临时文件」）。

## 九、版本、打包与升级

- **版本号只有一处**：`Engine/slcn/__init__.py` 的 `__version__`。
  `convert.py --version` / `checkbib.py --version` 打印它；mac 的 `build.sh` **现取**它写进
  `Info.plist`（从前写死 `1.0`，双平台之后必然漂移）；Windows 打包脚本同样从它取。
- **Windows 冻结分发的约定**（第 89 条）：引擎用 `engine_main.py` 冻成**一个**
  `SLCNEngine.exe`，外壳调 `SLCNEngine.exe convert.py --source …`（**第一个参数就是入口名**）：

  ```bat
  pyinstaller --onefile --console --name SLCNEngine engine_main.py
  ```

  支持十个入口 + `--version`/`--help`；不认识的入口打印用法并退出 2。
  `--hidden-import` 与打包自检都按 `ENTRIES` 逐项走（`sources-win/build-win.ps1` ②④），
  自检会**逐入口**跑 `--help` 并做一次最小转换 —— 只查 `--version` 查不出漏冻的入口。
  这样用户机器上**不需要装 Python**；界面壳另冻一个 exe，两者放同一目录即可
  （外壳的 `sources-win/engine.py: frozen_engine_exe()` 会先找它）。
- **Release 资产命名建议**（同一 tag 下）：
  `SLCN-<版本>-macOS-arm64.dmg`、`SLCN-<版本>-Windows-x64-setup.exe`、
  `SLCN-<版本>-source.zip`、`latest.json`。
- **升级提示**：`latest.json` 里放 `{ "version": "1.2.1", "macos": "<url>", "windows": "<url>",
  "notes": "<一句话>" }`；外壳启动时比对 `__version__`，**只提示、不自动替换**
  （编辑部机器上自动替换会撞权限与杀软，得不偿失）。
- **签名**：macOS 侧**不买开发者账号**（2026-10-15 决定）⇒ 下载的 `.app` 会被 Gatekeeper 拦，
  所以 mac 资产只发源码/压缩包，正文里写一条 `xattr -dr com.apple.quarantine`；
  Windows 侧未签名 exe 会让 SmartScreen 提示「更多信息 → 仍要运行」。

## 十、兼容性纪律（改引擎时自查）

1. **只增不改**：新增参数必须带默认值；不改既有参数的默认行为。
   ⚠️ **唯一的故意例外**（第 128 条，2026-10-18，用户拍板的成本闸门）：`--verify-refs` 的
   **核实范围**从「全量」改成「正文引用到的条目」（`--verify-all-refs` 显式恢复全量，
   `--verify-max` 控上限）。这不属于接口漂移：转换产物（`.tex` / `reference.bib`）**逐字节
   不变**，任务书/检索/AI 的「覆盖哪些条目」才是新口径；老外壳不传新参数也能跑。
   ⚠️ **第二个故意例外**（第 129 条，2026-10-18）：`--verify-refs` / `--verify-plan` 在
   **默认引用库存在**时**自动查库**，命中已核实条目会少调检索/AI，任务书多一节
   「五、库内已核实」、合并记录多行分类统计。转换产物（`.tex` / `reference.bib`）仍然
   **逐字节不变**；`--no-bibdb` 可恢复接入前行为（含 stdout/任务书逐字节相同）。
   老外壳不传新参数也能跑（只是会利用引用库省钱）。
2. **stdout 只增不改**：外壳认的那几类行（§二）不许改语义；新信息一律**新增**行。
3. **产物路径不许改**：`<out>/<作者名>/…` 的布局与文件名是契约的一部分。
   （历史上 `reference.bib.ai前`、`_文献核实.md`、`_回读校验报告.md` 都是这么定下来的。）
4. **退出码语义不许改**（§二/§三/§五）。
5. 改完跑：`tests/test_platform.py`（平台）、`tests/test_docs_consistency.py`（文档），
   以及 `README.md` 固定验收链条里的那六项。
