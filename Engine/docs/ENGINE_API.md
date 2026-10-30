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
| `--metadata` | 元数据文件（上一轮模板稿等）。**第 63b 批起元数据推荐路线 = GUI「填写元数据…」表单 → `--metadata-json`**；**`.doc/.docx` 自动元数据识别已取消**（B2 方案回退、GUI 入口撤下）。**Word 源**指到 `.docx` 仍当「英文元数据第二源」（第 76 条，行为不变）。**第 63 批 E2 的 `.docx` 能力保留、但降级为高级兜底**：**仅 LaTeX 通道**支持 `.tex` / `.docx`（`.docx` → Word 解析器提取题名/作者/单位/邮箱/摘要/关键词 → 临时 `.tex`；多作者只填第一作者槽位；不承诺 Word→LaTeX 全文转换、不搬前言依赖；0 字段/坏 docx/未知二进制 → exit 2）；**`.docx` = CLI 高级、GUI 不提供、不推荐**；老式 `.doc` 任何通道都不再转换（直接给 → exit 2 提示另存 `.docx`/`.tex`；第 63b 批起 GUI 的 `textutil` 转换已撤）；**两段式 + Word 元数据文件 → exit 2**。`.tex`/无元数据逐字节不变。详见下「元数据文件契约」 |
| `--metadata-json` | **第 63b 批（GUI 人工元数据）**：结构化 JSON 文件（多作者 1–5 / 每位多单位 / 中英题名摘要关键词 / 邮箱），供 GUI「填写元数据…」表单下传，也是 CLI 高级入口。**优先级最高**（> 旧 7 个覆盖参数 > `--metadata` 元数据文件）；空值不覆盖、缺字段仍写「【未识别…】」占位；坏 JSON/文件不存在/类型错/作者 >5/作者跳号/两段式同用 → **exit 2 且不产生产物**；不传时产物/argv/stdout 与批 63 逐字节相同。结构/映射/错误清单见下「`--metadata-json` 契约」 |
| `--refs` / `--bib` / `--name` / `--author` / `--lang` / `--sty` / `--skeleton` | 见 `convert.py --help`。⚠️ 第 134 条起 `--refs` 的自动发现改保守：**有真 `.bib`**（`--bib` / 源内 `\addbibresource` / `\bibliography` 指向且文件存在 / 同目录 `.bib`）时**不自动合并**附件（要合并请显式 `--refs`）；无真 bib 时自动挑候选（真 BibTeX 优先，文本清单须过结构校验：拒 `\href{...}` / 相邻 `}{` / 疑似一行两条），日志逐条写「采用 / 跳过 + 原因」 |
| `--vol --issue --year --pages --received --editor --grants` | 编辑侧出版信息，不填就留模板默认值 |
| `--compile` `--clean` `--source-pdf` | 编译出 PDF / 清中间文件 / 导出原稿 PDF（供矢量形状截图） |
| `--verify-refs` `--no-ai-merge` | 出《文献核实任务书》/ 只出任务书不并 bib（第 64 条）。**GUI 契约（第 62 批）**：普通初排默认 argv **不含** `--verify-refs`；两段式第一段固定带 `--verify-refs --no-ai-merge --coop-taskbook-only`；GUI 勾「用 AI 处理参考文献（直连 API，会产生费用）」时内部自动带 `--verify-refs`。CLI 行为完全兼容、未改。⚠️ 第 128 条起任务书、检索与 AI **默认只覆盖正文引用到的条目**（不是全量，见下一行） |
| `--verify-all-refs` `--verify-max N` `--verify-plan` | **核实范围与成本闸门（第 128 条）**。默认只核**正文引用到的键**（产物 `.tex` 的 cite 命令；`\nocite{*}` = 全量；解析不出引用键时退回全量并警告）。`--verify-all-refs` = 显式全量（越过闸门）；`--verify-max` = 本次条数上限（**默认 120**，`0` = 不限），超过且未显式全量 → **退出 2**（stderr 给三条可操作的路，任务书/检索/AI 一律没开始）；`--verify-plan` = 只打印范围与预计检索/AI 次数后**退出 0**（不生成任务书、不检索、不调 AI；转换产物照常写出，**不是** `--dry-run`）。外壳不传时行为与默认值相同（老外壳兼容） |
| `--search-refs` | 用 DeepSeek 独立 Search API 给**本次核实范围内**的参考文献检索证据：写进任务书 +（P2，第 72 条起）注入 AI 逐条提示词 + 合并前校验 `% 来源：` URL 白名单（默认关；开启时才计费 ¥5/1000 次；地址/密钥复用 `--llm-*`，也可 `SLCN_SEARCH_BASE` / `SLCN_SEARCH_KEY`；环境变量 `SLCN_SEARCH_REFS=1`）。外壳：mac 在 AI 运行时传 `--search-refs`；Windows 把 `SLCN_SEARCH_REFS=1` 放进子进程环境 |
| `--bibdb` `--bibdb-path PATH` `--no-bibdb` | **编辑部引用库（第 129 条）**。执行核实（`--verify-refs` / `--verify-plan`）时**默认**在默认库存在时自动查库（默认库 = 笔记仓 `bib_db/bibliography.jsonl`，`slcn.bibdb.default_db_path()`）：命中**已核实**条目的键**不调检索/AI**、**不改 `reference.bib`**，字段差异只写进任务书「五、库内已核实」。`--bibdb` = 显式启用（库缺失/读不了只告警、不中断出稿）；`--bibdb-path` = 指定库文件（隐含启用）；`--no-bibdb` = 显式关闭（输出与接入前逐字节相同）。笔记仓/默认库不存在时**静默跳过**（公开仓/CI 行为零变化）；不核实时这些参数只提示忽略。判定保守：key 命中只算候选（仍走检索），详见 `docs/GUIDE.md` §3.10。第 58 批另加 `--no-bibdb-verify` = 关闭**初排默认只读比对**（只影响日志一行；见下「引用库比对」行） |
| `--llm-base --llm-model --llm-protocol --llm-web-search --llm-search-tool --llm-responses-path` | AI 通道（密钥走环境变量） |
| `--llm-fulltext` | **直连 API 全文核查通道②（第 60 批，可选、默认关；第 63 批起自动应用）**：只配合 `--verify-refs` 的 ⑪ 直连路径；把正文分片发到配置端点（每片一次请求、**会产生 API 费用**），生成 `<名>_AI全文复核.md`（报告不落正文全文）。**第 63 批起默认把通过批57安全闸的 BIB/META/FN 建议自动应用到产物**（`.ai前` 备份、`.ai前` 已存在不覆盖、0 条不写、冲突/漂移拒绝、逐条记录；报告照常生成）；`--no-llm-fulltext-apply` 关闭（= 第 60 批 report-only）。未配置端点 / 预算超限 / 与 `--verify-plan` 或两段式同用 → 退出 2 且零请求；单片失败 fail-soft（退出码 0）。不勾时正文一个字节都不外发；不加此参数时 argv/产物与批62 逐字节相同 |
| `--llm-fulltext-apply` / `--no-llm-fulltext-apply` | **第 63 批**：全文核查结果是否自动应用。默认**开**（仅 BIB/META/FN、全走批57安全闸）；`--no-llm-fulltext-apply` = 只看报告、不改产物（第 60 批 report-only 行为；报告与批60 逐字节相同）；**`--no-ai-merge` 会强制 report-only**（即使默认开）；单独给本开关而无 `--llm-fulltext` → 忽略 + stderr 提示；`.ai前` 已存在时**不覆盖**（回滚点为更早那份，报告注明）；自动应用记录写进报告「四、自动应用记录」（标题「…自动应用」） |
| `--llm-fulltext-max-chars N` | **第 63 批**：直连 API 全文核查**单篇发送字符上限**，**默认 40000**；超限**显式截断 + 醒目警告**（不退出，照常出报告/自动应用；分片大小取 min(30000, N)）；**`0` = 不限**（整篇发送，费用只受 token 预算闸约束，**风险自担**）；负值 → 退出 2。GUI「设置」页「API 核查字符上限」对应本参数 |
| `--coop-fulltext {context,full}` | 第 59 批，可选、**默认关**；第 63 批起**取消 4 万/8 万字符截断**。只配合 `--coop-taskbook-only`：第一段任务书在「附二」后追加「附三、正文全文/引用上下文」；`context` = 引用/脚注锚点前后各 300 字窗口合并、`full` = 正文全文，**整篇写入、不再截断**（保留体积估算/超长提示）。只写本机任务书（软件零上传），不新增改正文类建议。**第 74 批（B-133）起附三之后再追加「附四、初排分析（只报告、不改写正文）」**：`slcn.fulltext.v1` schema + 七项指令 + 硬约束；第二段解析后只落 `<名>_AI全文分析.md/.jsonl`（四节）+ 协同记录「六、全文分析」计数，**严格 report-only（不写 `reference.bib`/`.tex`、不接 `--coop-apply`）**。**第 75 批（B-139/B-140①）**：附三之后/附四之前再补「脚注清单（fid+文本摘要）」「占位符清单（类型+位置）」两块输入（只在有数据时出现；任务书增量按字符数记日志）；附四 `evidence` 基准收紧为**任务书里附四之前的内容**（附三正文 + 附一原文 + 附二/附三说明，抄附四 schema 示例/指令 → 整条拒绝）。其他组合退出 2。见下方「两段式协同 argv 契约」 |
| `--version` | 打印 `SLCN 引擎 <版本> ｜ <系统>，Python <版本>`（外壳的「关于」直接用） |

**元数据文件（`--metadata`）契约（第 63 批 E2；第 63b 批降级为高级兜底）**：

> **第 63b 批口径修订（用户 2026-10-20 拍板）**：**元数据推荐路线 = GUI 人工填写**（初排页「填写元数据…」→
> `--metadata-json`，契约见下）。**`.doc/.docx` 自动元数据识别已取消**（B2 方案回退：用户真测两例 Word 稿均
> 未识别出元数据）；Mac GUI 的 `.doc/.docx` 元数据入口与 `.doc→.docx` 的 `textutil` 转换已撤下。
> 引擎侧 `.docx` 通道**保留**（不改代码、不删既有能力），但用途降级为 **CLI 高级兜底、GUI 不提供、不推荐**；
> `.tex` 元数据文件保留为高级兜底。下文 `.docx` 细节仍然成立（面向 CLI/脚本）。

**仅 LaTeX 通道**（`.tex` 源）。
`.docx` → 用 Word 通道解析器提取题名/作者/单位/邮箱/摘要/关键词（中英都取）→ 按目标模板语种合成
**临时 `.tex`**（只写 `\my*` 元数据宏，`atexit` 清理）；stdout 打
`元数据源（Word）  <abs>` / `  提取字段 N 个：…` / `  临时 .tex：<path>（运行结束自动清理）`。
**多作者只填第一作者槽位**（`myfirstauthor`）；**只取元数据，不做 Word→LaTeX 全文转换、不搬前言依赖**。
错误一律 exit 2（`die()`，stderr）：提取 0 字段 / `.docx` 损坏或不是有效 Word / 未知二进制文件 / 老式 `.doc`
（提示另存 `.docx`/`.tex`；第 63b 批起 GUI 不再自动转 `.doc`）/ **两段式（`--coop-taskbook-only`/`--coop-replies`）+ Word 元数据文件**
（临时文件无法跨进程复用；改用 `.tex` 元数据或先跑普通转换）。`.tex` 元数据 / 不给 `--metadata` → 与批62
逐字节相同；Word 源 + `.docx` 英文第二源（第 76 条）不变。GUI↔CLI 见下。


**`--metadata-json` 契约（第 63b 批，GUI 人工元数据；给外壳与脚本用）**：

`--metadata-json <file>` 读一个 **UTF-8（可带 BOM）JSON 文件**，把人工填写的元数据写进产物（`.tex` 的 `\my*` 宏；
Word 通道同样生效）。这是 GUI「填写元数据…」表单的下传方式，也是 CLI 高级入口。JSON 文件引擎**只读**，
不改不删（临时文件由 GUI 自己清理）。

结构（**全部可选；空串/空数组/缺键 = 未提供 = 不覆盖**）：

```json
{"schema_version": 1,
 "title_cn": "", "title_en": "",
 "running_title_cn": "", "running_title_en": "",
 "abstract_cn": "", "abstract_en": "",
 "keywords_cn": "", "keywords_en": "",
 "authors": [{"name_cn": "", "name_en": "",
              "affiliations_cn": ["", ""], "affiliations_en": ["", ""],
              "email": ""}]}
```

**归一化与校验**：

- 顶层必须是对象；`title_*` / `running_title_*` / `abstract_*` / `keywords_*` 必须是字符串；缺省 / `null` = 未提供；
  空白串折叠后为空 = 未提供（**不会用空值覆盖**自动解析结果）。
- `authors` 必须是数组（可省略）；每项是对象，`name_cn` / `name_en` / `email` 为字符串；
  `affiliations_*` 为**字符串或字符串数组**（数组逐项去空白、丢空项；多单位由引擎用 `\\` 拼接，见映射表）。
- **作者 1–5 位**（模板 `myfirstauthor…myfifthauthor` 上限）：数组**尾部空作者**由引擎丢弃；
  **中间空作者 → exit 2**（槽位不能跳号，否则模板按连续作者数选排版分支、后面作者不落版）；
  **>5 位 → exit 2**（不静默截断）。
- `schema_version` 不是 1 → warning 后**仍按 1 解析**；未知键 → warning（列出键名、本次忽略），不报错。
- 全部字段为空 / 没有可写作者 → warning「没有可写入的字段 / 作者（按未提供处理）」，产物与不传该参数一致。
- 文件不存在 / 不可读 / JSON 语法错（报 `文件:行:列`）/ 顶层不是对象 / 字段类型错（报路径，如
  `authors[1].affiliations_cn 必须是字符串数组`）→ **exit 2，不产生产物目录**。
- **两段式协同（`--coop-taskbook-only` / `--coop-replies`）+ `--metadata-json` → exit 2**（v1 不支持：
  第二段按暂存状态重跑，JSON 无法跨进程保留）。

**映射（`is_en = "mytitleCN" in tpl_fields`，与 `build.py` 现有分流同源）**：

| JSON | CN 模板（中文稿）落点 | EN 模板（英文稿）落点 |
|---|---|---|
| `title_cn` / `title_en` | `mytitle` / `mytitleEN` | `mytitleCN` / `mytitle` |
| `abstract_cn` / `abstract_en` | `myabstract` / `myabstractEN` | `myabstractCN` / `myabstract` |
| `keywords_cn` | `mykeywords` | 无槽位 → warning「英文模板无关键词字段，关键词需人工放置」 |
| `keywords_en` | 无槽位 → warning（中文模板无英文关键词槽） | 无槽位 → warning（英文模板全篇无关键词槽位） |
| `running_title_cn` / `_en` | `myrunningtitle`（缺省 = 主语言标题首行） | 同左（缺省 = `title_en` 首行） |
| `authors[i].name_cn` | i=0 `myfirstauthor`（带 `\kaishu ` 前缀）；i≥1 `my{second…fifth}author` | i=0 `myfirstauthorCN`；i≥1 `my{…}authorCN` |
| `authors[i].name_en` | i=0 `myfirstauthorEN`；i≥1 `my{…}authorEN` | i=0 `myfirstauthor`；i≥1 `my{…}author` |
| `authors[i].affiliations_cn[]` | i=0 `myfirstaffiliation`；i≥1 `my{…}affiliation`（`\\` 连接） | i=0 `myfirstaffiliationCN`；i≥1 `my{…}affiliationCN` |
| `authors[i].affiliations_en[]` | i=0 `myfirstaffiliationEN`；i≥1 `my{…}affiliationEN` | i=0 `myfirstaffiliation`；i≥1 `my{…}affiliation` |
| `authors[i].email` | `my{first…fifth}email`（中英页共用） | 同左 |

补充口径：

- **转义**：题名/摘要/关键词折叠空白后转义 `& % $ # _ { } ~ ^ \`；单位逐项转义后按 `\\` 连接（首尾不留 `\\`）。
- **缺字段仍占位**：声明了某作者但缺该语言姓名/单位/邮箱时，对该作者**模板里存在的槽位**写
  `【未识别：…，请手工填写】`（与自动解析的占位文案同源）；EN 模板的 `my<N>authorCN/affiliationCN` 补充占位
  **只在给了本参数时**生效（默认路径不动）。
- **优先级（低→高）**：源稿解析（**B-276 起**：本刊模板 `\my*` 优先；源稿**一个 `\my*` 都搬不到**时，
  回退识别**标准 LaTeX 前言**——`\title`、第一作者 `\author`（剥 `\thanks`；单位/基金从 `\thanks` 分类）、
  `\begin{abstract}`、关键词行；**不填 EN 槽**、不推导英文翻译；实现 `slcn/texfront.py`，识别不了返回 None 不猜）
  < `--metadata` 元数据文件（含 Word 元数据/英文第二源）< 旧 7 个覆盖参数
  < **`--metadata-json`**。同时给旧覆盖参数与本参数时，重复字段以本参数为准（warning 列出字段）；
  本参数作者数少于源稿解析出的作者槽位时**不清空**未声明槽位，只 warning「源稿/元数据里还有第 N 位作者槽位」。
- **输出名/作者名推导不变**（仍看 `--source` / `--name` / `--author`）；源文件一路的来源优先级是
  `--name` > `--author` > `\myfirstauthor` > 普通 `\author{}` 的第一作者（无模板宏时回退，剥 `\thanks`；
  拿不准仍报错、要求显式指定）；JSON 只影响产物字段。
- **stdout**：convert 层多一行 `人工元数据（--metadata-json）：N 位作者、M 项输入字段[（优先于 --metadata 元数据文件[；重复字段以本参数为准]）]`；
  build 层多一行 `人工元数据：写入 N 位作者 / X 个模板字段`；未知键/关键词无处落版等走 `⚠`。
- **默认零变化**：不传 `--metadata-json` 时产物/argv/stdout 与批 63 **逐字节相同**（JSON 全空同样按未提供处理）。
- **新旧引擎错配**：新 GUI + 旧引擎会因 argparse 不认识 `--metadata-json` 直接 **exit 2**（`unrecognized arguments`）——
  官方 App 包内引擎与仓库一致时不会遇到（GUI 侧不做 `--help` 探测）。

**GUI 契约（第 62 批入口收敛 B）**：初排页移除独立「生成/出文献核实任务书」勾选，普通初排默认 argv 不含
`--verify-refs`（**不再默认生成 `<Name>_文献核实.md`**）；需要任务书 → 两段式第一段，或 CLI `--verify-refs`（高级）。
Windows 新增显式「用 AI 处理参考文献（直连 API，会产生费用）」勾选（默认不勾、会话态），勾上后内部带
`--verify-refs`（行为/产物与批61 一致）；此前「配好端点即隐式跑 AI」的行为取消。`--no-ai-merge` / `checkbib.py`
CLI 全兼容；「文献回读校验」页（第 62 批页顶标题「回读/比对报告查看器（高级：手动并回，离线）」，侧栏短名不变）
仍调用独立的 `checkbib.py`，能力全保留。

**GUI↔CLI 契约（第 63 批，第 63b 批修订）**：Mac「设置」页「API 核查字符上限」→ `--llm-fulltext-max-chars N`（默认 40000，
0 = 不限；与默认相同则两壳都不拼）；「全文核查结果自动应用」→ 开 = 不拼（引擎默认开）/ 关 = `--no-llm-fulltext-apply`。
初排页「高级选项」整行/文字可点（B1，纯 UI、不改 argv）。

**元数据 GUI↔CLI（第 63b 批）**：

- **推荐路线 =「填写元数据…」表单**：初排页「高级选项」内按钮 → sheet（中英文题名 / 多行中英文摘要 / 中英文关键词；
  作者 1–5，每位中英文姓名 + 邮箱 + 单位 CN/EN 动态行）。表单非空 → GUI 写临时 JSON
  `/tmp/slcn_metadata_<源稿名>_<时间戳>_<pid>.json` → `--metadata-json`；**运行结束（含失败/取消）即删**；
  表单全空 → 不建文件、不传参（默认 argv 逐字节不变）。GUI 日志先打 `元数据：将写入 N 位作者 / M 项字段（…）`
  （与引擎的 `人工元数据  …` 行不同层）。
- **表单优先**：表单激活时内联 7 个覆盖字段置灰，且 GUI 对表单**已填的同名字段**逐字段清零、不再拼旧参数
  （同一字段只有一个来源）；`.tex` 元数据文件优先级低于表单（引擎按 `--metadata-json` 最高处理并 warning 说明）。
- **`.doc/.docx` 自动识别已撤下**：LaTeX 源的「元数据文件」**只接受 `.tex`**（picker 显示 `.tex`/`.docx`；选中/拖入
  `.docx` 等其它类型时界面立即拦下、写日志并清空该路径）；**Word 源 `.docx` 的「英文元数据第二源」
  （第 76 条）行为不变**，可显式指定；`.doc` 不再转临时 `.docx`（提示另存 `.docx`）。CLI 高级兜底见上「元数据文件契约」。
- **两段式协同 v1 不支持人工元数据**：协同模式下「填写元数据…」按钮禁用、运行时不传 `--metadata-json` 并写日志
  （引擎对二者同用也明确 exit 2）；请改用普通转换或 `.tex` 元数据文件。
- **本次运行有效**：表单值不持久化（只在当前 App 会话内存里）；不改变默认路径。
默认路径零变化：不加 `--llm-fulltext`、两栏保持默认、表单全空且元数据文件留空时，argv/产物/stdout 与批 63 逐字节相同。

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
| 行首 `   全文复核（` / `   🤖 AI 全文复核` | **直连 API 全文核查（第 60 批，`--llm-fulltext`，默认关；第 63 批起默认自动应用）**：跑前打「正文 N 字 → M 片；预计 X 次调用 / Y tokens」，跑完打「N 条建议 / M 次请求、K 片不可用」+ 自动应用逐条记录；产物 `<名>_AI全文复核.md`；第 63 批起通过批57安全闸的 BIB/META/FN **默认写入产物**（`--no-llm-fulltext-apply` 关）；单片失败 fail-soft（退出码 0）；预算超限 / 未配置端点 → 退出 2、零请求 |
| 行首 `   引用库比对：` | **初排默认只读比对**（第 58 批）：未执行核实时，默认库存在且产物 bib 有条目时打一行 `引用库比对：命中 X / 候选 Y / 未命中 Z（只读，不改产物）`；无库/无 bib/读库失败静默；`--no-bibdb-verify` / `--no-bibdb` 关闭。只增这一行，产物/任务书不变 |
| 行首 `人工元数据（--metadata-json）：` / `   人工元数据：` | **人工元数据（第 63b 批）**：给了 `--metadata-json` 才出现。convert 层：`人工元数据（--metadata-json）：N 位作者、M 项输入字段[（优先于 --metadata 元数据文件[；重复字段以本参数为准]）]`；build 层：`人工元数据：写入 N 位作者 / X 个模板字段`。未知键/关键词无处落版/作者槽位少于源稿等提示走 `⚠`。不给该参数时 stdout 与批 63 逐字节相同 |
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

**Word 编号体例稿脚注裸编号（B-130，批73，2026-10-21；**无契约变化**）**：编号体例稿
（文末 `[N]` 清单 + 正文 `（[n]）`）的**脚注文本**里，裸 `[N]`（`参见[12]。`）在满足
「`ref_key_by_num` 是**可靠 1..N 映射**（Word 自动编号安全闸物化成功，或文末显式 `[N]`
清单解析一致且编号 1..N 连续）」+「逐处有引用引导语证据」时保守转成 `\parencite{键}`
（只换编号本身，前后中文与页码定位保留）；越界/无引导语 → 原样保留 + warning（不猜）。
**不改任何 CLI 参数、stdout 契约行、退出码、产物路径与文件名**；命中稿 `.tex` 只是脚注行
里多出 `\parencite`（`.bib` 不变），未命中稿产物逐字节不变；正文裸编号不在本卡。
**批75 B-137 残余扩展（同句引导语继承/枚举续引）仍无契约变化**：一句内（分号/逗号不结束
句子）已有引用按引导语转换后，同句内只隔位置/连接语的后续裸 `[N]` 继承引导语继续转换
（每个 N 仍独立校验映射与越界；拿不准不转）；仅命中稿脚注行多出 `\parencite`，
未命中稿产物逐字节不变。
口径见 `docs/GUIDE.md`「Word 编号体例稿：脚注文本里的裸 `[N]` → `\parencite`」；
回归见 `tests/test_b73_footnote_bare_cite.py`、`tests/test_b75_b137_cite_residual.py`。

**批76 B-120（编号清单尾部解析修复；**无契约变化**）**：文末英文元数据块（题名/Abstract/
Keywords）紧跟在**英文参考文献清单**之后时，旧实现从英文 Abstract 向上回溯「英文短行」会
穿过清单、把清单尾部当元数据块切走 → `ref_key_by_num`（`num2key`）只剩前几条、脚注裸编号
报「不在清单范围」。现在候选块含编号行 `[N]` 时块首回推到英文题名
（`docxsource.locate_tail_block` + `_skip_en_refs_to_title`；无编号行行为不变）。
**不改任何 CLI 参数、stdout 契约行、退出码、产物路径与文件名**；命中稿 `.tex` 仅多出预期
`\parencite`、`.bib` 仅**新增**此前丢失的条目（既有条目值不变、键不改名，`make_key` 规则未动）；
未命中稿产物逐字节不变。全库只读 dry-run：`num2key` 1561→1605、N∉可靠映射 14→0、
解析变化稿 4/213（41 篇回归案例集 0 变化）。口径见 `docs/GUIDE.md`「Word 编号体例稿…」
B-120 段；回归见 `tests/test_tail_block.py`。

**两段式协同（`--coop-*`）的 argv 契约（第 54–59 批，外壳拼参数用）**：第一段 =
`--verify-refs --no-ai-merge --coop-taskbook-only [--coop-stage DIR] [--coop-fulltext context|full]`；
第二段 = `--coop-replies <回填>… [--coop-apply] [--compile] [--clean]`（`--source` / `--out` 两段都必填）。
`--coop-fulltext` **只能随第一段**（否则 exit 2）、**默认不传**；两壳只在两段式面板勾选「全文 AI 核查」且
第一段时追加 `--coop-fulltext context|full`，第二段与普通转换绝不带（mac `tools/ai_settings_test` / win
`sources-win/tests/test_bridge.py` 钉住）。任务书 = `refmark` 任务书 + 附一 + 附二 +（可选）附三 +（可选）附三·补充（附四输入清单：脚注 fid+摘要 /
占位符 类型+位置，第 75 批 B-139；只在有数据时出现）+（可选）附四；
**附三只用于回答附一**；**附四（第 74 批 B-133，report-only）只要求 AI 结合全文给出初排分析建议、只报告不写产物**
（ph2 落 `<名>_AI全文分析.md/.jsonl` + 协同记录「六、全文分析」计数），默认关时任务书/stdout 与批 58 逐字节相同。
**第 75 批（B-132）**：附一 `unresolved` 允许 **note-only**（无 `span` 带 `note` → 收下、进建议清单「三、待人工」；
`corrected` 仍强制 span/field/value 与白名单，不放宽）；`_AI协同记录.md` 在存在 C 类未匹配脚注项
（`FN-*` 的 `mixed_manual`/`citation_unmatched`）时新增可见「清单缺条」节（只报告、不补条目；无该类项时
记录与旧版逐字节相同）。**第 75 批（B-140①）**：附四 evidence 校验基准 = 任务书**附四之前**的内容
（`coop.analysis_evidence_base()`；附四 schema 示例/指令不再能当逐字证据）。
通道②（直连 API 全文核查）由第 60 批的 `--llm-fulltext` 提供：默认关、会外发正文并产生费用。**第 63 批起默认自动应用**（仅 BIB/META/FN、全走批57安全闸；`--no-llm-fulltext-apply` 关；`--llm-fulltext-max-chars N` 默认 40000、0 = 不限）；两壳「设置」页新增「API 核查字符上限」「全文核查结果自动应用」两栏，GUI↔CLI 映射见下。

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
<Name>_文献核实.md           任务书（含「六、合并记录」；两段式第一段 / CLI --verify-refs 才生成，第 62 批起普通初排默认不生成）
<Name>_AI全文复核.md        全文复核报告（--llm-fulltext 才有；不落正文全文；第63批起默认自动应用 BIB/META/FN，报告照常生成）
<Name>_回读校验报告.md       并入后自检（结论一行同时打在 stdout）
<Name>_参考文献核对.txt      离线核对（缺哪些字段 / 引用对不对得上）
<Name>.log                   xelatex 日志（**故意不删**）
```

## 三、入口 2：`checkbib.py`（文献回读校验 / 并回）

> GUI（第 62 批）：页顶标题「**回读/比对报告查看器（高级：手动并回，离线）**」，侧栏短名仍「文献回读校验」；
> 本页不生成任务书，体检/粘贴预览/并回/入藏引用库/重新编译该篇等能力全部保留；CLI 与本页调用方式未变。

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

## 五、入口 4–10：期目录级的动作、一校核查、检查tex信息、生成目录与引用库（第 91、92、94、132 条）

```bash
python3 cleantemp.py   --folder "<期目录>" [--dry-run] [--trash]
python3 cleantemp.py   --folder "<期目录>" --restore "<.toolbox-trash/<目录名>-<时间戳> 目录>"
python3 cleantemp.py   --folder "<目录>" --mode delivery|workspace [--dry-run] [--delete]
python3 cleantemp.py   --folder "<期目录>" --empty-trash
python3 compileall.py  --folder "<期目录>" [--only 01,03] [--dry-run]
python3 proofcheck.py  --tex "<初排稿.tex>" [--lang zh|en] [--out <报告.md>] [--fix]
python3 templatecheck.py --input "<产品目录|初排稿.tex>" [--out <报告.md>] [--template <名称|.sty 路径>]
python3 infocheck.py   --folder "<期目录>" [--out-dir <目录>]
python3 infocheck.py   --folder "<期目录>" --preview   # 界面用：检查结果 JSON（不写任何文件）
python3 toc.py         --report "<信息检查.txt>" [--template <期目录>] [--settings <目录设置.json>]
python3 toc.py         --report "<信息检查.txt>" --preview     # 界面用：篇目清单 + 模板来源（JSON）
python3 toc.py         --report "<信息检查.txt>" [--place] [--compile] [--result-json <结果.json>]
                       # 一键最终交付：落位固定名 → 字体检查 → xelatex ×2 → 单页适配
python3 publish.py     --folder "<期目录>" [--only N] [--from N] [--template <期目录>] [--skip-toc-pdf] \
                       [--no-nest-outlines] [--no-editors-page] [--no-back-cover]
                       # 本期出刊向导（CLI-only；GUI 已删该页，合并走「合并文件」页；一步跑完一期，5 步）
python3 bibdb.py       <子命令> [参数…]   # 编辑部引用库（子命令见下）
```

| 入口 | 干什么 | 退出码 | 最后一行（外壳靠它拿结果） |
|---|---|---|---|
| `publish.py` | **本期出刊向导（CLI-only；GUI 已删「本期出刊」页、合并能力只保留 GUI「合并文件」页）**（一步跑完一期；2026-10-21 第 71 批 B-116 起 **5 步**）：按序跑 ① **检查tex信息**（`infocheck.py`）→ ② **生成目录**（`toc.py`，读①最新的报告；**不再硬传 `--template <本期>`** —— 模板解析与「生成目录」页同一套「本期成品→显式指定→上一期→内置」，需要时用 `--template <期目录>` 显式指定）→ ③ **编译全部**（`compileall.py`，只编论文、跳过 `000` 目录）→ ④ **目录 PDF 落位**（调 `slcn/tocdelivery.py`，与 `toc.py --place --compile` **同一份实现**：取期目录根最新 `Chinese Content_<ts>.tex` / `contents_<ts>.tex` → 复制为 `000*/Chinese Content.tex` / `contents.tex`（覆盖旧的；根下带时间戳的 `.tex` 保留，供下一期继承）→ Windows 字体前置检查 → 在 `000*` 内 `xelatex ×2` 出同名 `.pdf` → 页数 > 1 时一次性压缩行距做单页适配；该期**缺 `000*` 目录时自动创建** `000 Table of Contents`，不再 rc2）→ ⑤ **合并整期 PDF**（`mergepdf.py`，输出 `<期目录>/合并.pdf`）。**编排只在引擎里**（外壳不许多写一份流程；项目红线「一个引擎，两个外壳」）。失败语义（用户 2026-10-17 拍板，INVARIANTS 三.2）：某步退出码 **2 ⇒ 中止**（前 N−1 步产物保留，末行写明卡在第几步）；某步退出码 **1 ⇒ 橙色警告、继续**（"有结果"）；`0` 继续。`--only N` / `--from N` 对 **1–5** 都生效（单步重跑/续跑）；①的报告缺失 ⇒ ② **退出码 2 中止**（不静默跳过）。**⑤ 显式传 `--nest-outlines --editors-page --back-cover`**（默认开，与 GUI「合并文件」页三开关默认一致；`mergepdf.py` 自身默认值**不变，仍是全关**），`--no-nest-outlines` / `--no-editors-page` / `--no-back-cover` 逐个关闭。④：目录 `.tex` 编译失败 / 字体检查失败 / 单页适配失败 ⇒ 退出码 2（**绝不拿旧 PDF 静默合并**）；该期没有 `000` 开头的目录 ⇒ **自动创建** `000 Table of Contents` 后照常落位（B-226；`--skip-toc-pdf` 时仍整步跳过、不创建）；`--skip-toc-pdf` 跳过④并对 `000` 里缺失的目录 PDF **明确告警**（计入 rc1 警告） | `0` 全部完成；`1` 完成但有警告项；`2` 中止/用法错误 | `完成。出刊：N 步都跑完[，警告 N 项]（目录：…）`／`--only` 时 `完成。出刊：只跑了第 N 步（<步骤名>）（目录：…）`／中止时 `完成。出刊：卡在第 N 步（<步骤名>）；前 N−1 步产物有效（目录：…）`。**契约行永远是 stdout 最后一行**（警告行在其前） |
| `cleantemp.py` | 递归处理 LaTeX 编译中间文件（`.aux .bbl .bcf .blg .out .run.xml .synctex.gz` + `.DS_Store`）；**不删** `.tex/.pdf/.sty/.bib/.log`；**且永不进入 `original/`**（作者来稿目录，红线只读）。aux 模式：**默认直接删**（老行为，mac 外壳走这条）；`--trash` 移入 `<目录>/.toolbox-trash/<目录名>-<时间戳>/`；`--restore <回收目录>` 搬回（**原位置已有同名文件则跳过、不覆盖**；**旧 v1 manifest 兼容**）。**B-211 起**另有 `--mode delivery`（交付收尾：先预检 `\includegraphics/\input/\include/\addbibresource/\bibliography` 引用闭包，缺失 → **整体拒绝、零写入**；白名单 `*.tex/*.pdf/*.bib/*.sty/*.cls`、闭包文件与目录、`original/` 系、`*交付清单*.md`；清 aux 全套/`.ai前/.log/*.bak/*_AI*/_文献核实/_回读校验报告/_参考文献核对/_一校核查/_原稿.pdf/_待核/`；未识别保留 + warning）与 `--mode workspace`（每期顶层 `合并*.pdf` 只留最新、固定名 `合并.pdf` 不删、解析不出时间保守保留；递归清 `信息检查*/ *.bak/*.coop-bak-*/ *_待核/ .DS_Store`；`000*/contents.*/Chinese Content.*/扉页*/编委*/封底*` 永不删）；两模式默认移入回收站（manifest schema=2，文件+目录条目），`--delete` 永久删；`--empty-trash` 清空回收站（B-281 起：清空后若 `.toolbox-trash/` 已空，该目录本身也一并删除；目录非空/权限失败时静默保留；dry-run 不删目录。契约行**逐字不变**，删目录的提示行 `已删除空的回收站目录：.toolbox-trash` 只会出现在契约行之前的 stdout） | `0` 成功；`2` 目录/预检错误（错误在 stderr，预检失败零写入） | aux：`完成。已删除 N 个临时文件（目录：…）`／`完成。已移入回收目录 N 个临时文件（回收目录：…）`／`完成。已恢复 N 个文件（目录：…）`；delivery/workspace：`完成。已移入回收目录 N 项（回收目录：…）`（dry-run「将移入」）／`--delete`：`完成。已删除 N 项（目录：…）`；empty-trash：`完成。已清空回收站 N 个回收目录（目录：…）` |
| `compileall.py` | 对每个 `NN 作者/` 子目录跑 `xelatex → biber -l=zh_pinyin → xelatex → xelatex`；跳过 `000 目录`；某篇失败**只停那一篇**并回显 `.log` 里的 `!` 摘要 | `0` 全成功；`1` 有失败；`2` 用法/目录错误 | `完成。编译 N 篇（成功 X / 失败 Y）（目录：…）` |
| `proofcheck.py` | **一校核查**：按《排校手册》《流程化经验书》逐条核查初排稿（29 条规则，见 `docs/PROOF_RULES.md`）；**默认只报不改**：报告只列需人工判断的命中，可自动改项由初排通道默认处理（`--fix` 可补写新文件） | `0` 无命中；`1` 有命中（正常）；`2` 用法/文件错误 | `完成。一校核查：命中 N 处（可自动改 M 处）（报告：…）` |
| `templatecheck.py` | **模板合规校验（report-only MVP，B-002）**：核查产品目录或初排稿的 8 条模板/初排规范（T1–T8，见 `docs/TEMPLATE_RULES.md`）；**只报不改，无 `--fix`**；不注册 App/GUI/打包入口（`--self-test` 走内建夹具） | `0` 无 FAIL；`1` ≥1 FAIL；`2` 用法/输入/写报告错误 | `完成。模板合规：PASS p / FAIL f / SKIP s（报告：…）` |
| `infocheck.py` | **检查tex信息**：汇总各篇 tex 里的卷期年/页码/题目/作者/字号/行距 + PDF 实际页数；`--preview` 只输出检查结果 JSON（篇目 + `inconsistencies`），**不落任何文件** | `0` 成功（或 preview 成功）；`2` 用法/目录错误 | `完成。检查 N 篇（有 tex X / 有 pdf Y；不一致 Z 项）（报告：…）` |
| `toc.py` | **生成目录**：读 `infocheck` 的报告产出中英文目录 .tex（模板继承上一期成品）；`--preview` 只输出篇目清单与模板来源的 JSON（给界面用，不写文件）。**B-226 起支持一键最终交付**（默认仍只生成 tex，向后兼容）：`--place` 生成后**落位固定名**（缺 `000*` 时**自动创建** `000 Table of Contents`）→ `000*/Chinese Content.tex`／`contents.tex`（覆盖旧成品；根下带时间戳的 tex 保留供下一期继承）；`--compile` 落位后先做 **Windows 字体前置检查**（SimSun/SimHei/KaiTi，缺字体 **rc=2**、**绝不静默换字体**）→ `xelatex ×2` → 页数 > 1 时**一次性压缩行距**做单页适配（`\vdistance` 中英同一机制；本期成品小行距按 40pt 压、继承模板的大行距按「压到人工量级」估算；总压缩上限 320pt；压到上限仍 > 1 页 ⇒ rc=2 + 原因；`--compile` 隐含 `--place`）；`--result-json <路径>` 写 GUI 用的结构化结果：`templateSource`（`本期成品`/`上一期`/`内置模板`/`显式指定`）、`templateNote`、`tocDir`、`placed{zhTex,zhPdf,enTex,enPdf}`、`compiled`、`pages{zh,en}`、`column{enabled,title}`、`manualEdits[{kind,article,note}]`、`warnings`。落位/编译/单页适配/字体检查的唯一实现在 `slcn/tocdelivery.py`（`publish.py` 第 ④ 步共用同一份，禁止第二份实现）。模板优先级：本期成品 → 显式 `--template` → 上一期 → 内置。内置模板来源与升级：源头是 `sources-mac/Features/ContentTemplates.swift`，由 `Engine/tools/gen_content_templates.py` 生成 `Engine/slcn/content_templates.py`（生成物禁手改）；**2026-10-27 起内置中文模板 = 2026 年第 3 期(88) 版式**（B-231 ③-A，用户批准）；**B-243 起内置英文模板 = 同期(88) 英文版式**（用户 2026-10-27 定序），仍是最后兜底、每期按需人工微调，**不是版式标准**；`Engine/tests/test_infocheck_toc.py` 的 `(n1)(n2)` 常驻校验 Swift↔py 逐字节一致（要改模板只改 Swift 源再重生成）。**显式不做**（B-231 ③-B，用户 2026-10-27 **不批**）：回退到内置模板时不改 `rc`（仍 0）、不 fail-hard、不报错，唯一后续待定项是用 `--check` 在报告期做显式告警。决策见 notes 仓 `drafts/b231-card.md` 与 `drafts/specs/b231-template-hardening.md` §2③。⚠️ **先校验"这确实是一份检查报告"**：解析出 0 篇就退出 2、**一个文件都不写** —— 从前喂错文件（如某篇 `.tex`）会静默产出两份「第 ? 卷，第 ? 期」的空目录并退出 0，而产物落在**报告所在目录**（真跑时就是作者目录） | `0` 两份都生成（或 preview 成功）；`1` 两份已生成但有需人工核对项（只生成了一份 / 报告不在期目录里导致「总第 ? 期」无法确定）；`2` 用法/文件错误、或报告不可用 | `完成。生成目录 N 份（<目录>）` |
| `bibdb.py` | **编辑部引用库**（第 132 条；两个平台的「引用库」5 页全调它）：`init` 建库；`import-bib` / `import-dir` 导入 `.bib` 或整期成品；`query` 查库（`--json` 给界面）；**B-259 增补** `query-advanced` 高级检索（11 字段 × contains/prefix/exact/fuzzy、年份表达式、排序/分页/why-hit；**只读**，旧三模式零改动）；`stats` 概况；`lint` 体检；`rehash` 规范化规则升级后重算 `content_sha256`（B-126；默认 dry-run、`--apply` 才写）；`conflicts` 列冲突条目（B-127；只读，GUI 待裁决页数据源）；`resolve` 记录冲突裁决（keep / defer / adopt / **replace**；写前可选 `--expect-db-sha256` + `--backup DIR`；`--adopt-all-pending` 一键全部采用）；`restore --backup` 从备份整库恢复（不 reopen；**CLI 保留向后兼容，B-190 起 GUI 不再调用/不再有备份入口**）；`normalize` / `normalize-authors` / `normalize-editors` 是 B1/B2/B3 规范化（**默认 dry-run，`--apply` 才写库**，写库前须 `--backup`）；**B-188 增补**：`edit`（通用条目编辑，可 `--new-key`，旧 key 自动进 aliases）、`merge-records`（合并 2+ 条重复记录）、`suggest-field-fixes`（只读：缺字段归位候选）；**B-190 起 `edit`/`merge-records` 的 `--backup` 改可选**（不传 = 不写备份文件；GUI 改用 git 版本层，最后写库仍是同一套 sha 闸 + 原子写 + 写后复核）；`lint --with-records` 给全量受影响 key。库默认在私有笔记仓 `bib_db/bibliography.jsonl`，找不到笔记仓直接退出 2、绝不悄悄写到别处；`--db` 可显式指定。**新子命令/新参数全部是加法：不传时老行为与老 `--json` 键不变** | `0` 成功；`1` 有需人工处理项（如 `lint` 有问题、`resolve` 留下 defer）；`2` 用法/库错误、或找不到笔记仓 | 人读子命令打印小结；`--json` 时 **stdout 是 JSON**（外壳读 JSON、不解析文本） |

**产物**：
- `cleantemp` / `compileall`：原地操作（前者删文件、后者生成 `.pdf` 与 `.log`）。
- `proofcheck`：同目录写 `<stem>_一校核查.md`（报告，只列需人工判断的命中）；
  加 `--fix` 时**另写** `<stem>_一校核查后.tex`——**输入文件一个字节都不动**。
  `--fix` 只改 `fixable` 命中（`slcn/proofcheck.py` 的 `RULES` 里标 `fixable=True` 的那些）；
  `slcn/proofcheck.py` 导出 `apply_fixes(tex, rep, rule_ids=…)` —— `rule_ids` 传显式 id 子集时
  只改这些规则（传 `()` = 一处不改）。另导出三个常量：
  `_PROOF_AUTOFIX_IDS`（16 条窄集，初排 Word/LaTeX **两条通道**默认在写盘前套用、只改产物不碰源稿；ZH-23 自 B-280 起降为只报、已移出）、
  `INITIAL_LAYOUT_FIXABLE`（初排转换接线的完整建议集，14 条）、`OPT_IN_FIXABLE`（当前空元组）。
- `templatecheck`：默认写 `<产品目录>/<stem>_模板合规.md`（`--out` 可挪走）；
  **report-only，没有 `--fix`**，输入/产品文件字节零变化；canonical 模板取自 public 仓
  `编辑笔记/模板文件/`（`slcn/templates.py` 的 `TPL_DIR` / `AVAILABLE_STY`；`--template <path>` 可覆盖），
  稿件仓副本不作 canonical（两仓分叉见 B-189）。
- `infocheck`：期目录根下写 `信息检查<时间戳>.txt`（**CRLF**，与原版/mac 版逐字节一致）
  与 `.json`（结构化，供 toc 与界面用）；可用 `--out-dir` 挪走。
- `toc`：报告同目录写 `Chinese Content_<时间戳>.tex` 与 `contents_<时间戳>.tex`
  （**先英文后中文**，与原版 `run_tab5` 一致）。
  本期设置（栏目/主持人/标题断行上限）读同目录的 `目录设置.json`，缺失字段逐项回落默认值（中文断行 20 / 英文断行 30 —— 英文值 2026-10-17 按期 90 成品校准，见案例第 110 条）。
- `publish`：按 ①→⑤ 顺序在期目录里落产物：①《信息检查<时间戳>.txt/.json》；② 两份带时间戳的目录 `.tex`；
  ③ 各篇 `.pdf`（重编译）；④ **覆盖写入** `000*/Chinese Content.tex/.pdf`、`000*/contents.tex/.pdf`
  （目录 `.tex` 由第②步产物复制而来，供下一期继承；**这是合并的输入**，所以④必须排在⑤之前）；
  ⑤ `<期目录>/合并.pdf`。`--skip-toc-pdf` 时④完全不写（沿用 000 里现有 PDF）。

**给外壳的注意**：`compileall` 是长任务（每篇几十秒），必须用第 82 条的实时日志；
`publish` 全流程更长（③ 编译各篇 + ④ 编译目录）；外壳只拼 `--folder` / `--only` / `--from` /
`--skip-toc-pdf` / `--no-*`，**不要在界面里补开关默认值**（第⑤步的三开关默认由引擎显式传）。
`proofcheck` 的 `1` 表示「有命中」而不是失败，外壳不要把它显示成错误；
`toc` 的篇目预览必须调 `--preview`（**别在界面里自己解析报告**），否则会出现
「清单里勾了 3 篇、生成时只认 2 篇」这种对不上的情况。

### `bibdb.py` 子命令与 `--json` 契约（第 132 条；B-126 / B-127 批73 增补标 ★）

| 子命令 | 干什么 | 关键参数 | `--json` 输出 / 退出码 |
|---|---|---|---|
| `init` / `import-bib` / `import-dir` | 建库；导入一份 `.bib` 或多条 bib 的整期成品 | `--dry-run` 预览；`--force` 整条替换（旧值留档） | 老契约不变（`report`/`changes`/`db_existed`…） |
| `query` / `stats` / `lint` | 查库（key/DOI/题名）/ 库概况 / 体检 | `--bibtex` 附可复制条目 | 老契约不变；`lint` rc1 = 有 error；**B-143 起 `lint` 增补 `warning_items` 与 `counts.conflicts_pending/resolved`（只加不改）**，见下 |
| `query-advanced` ★（B-259） | **高级检索**（只读；旧 `query` 三模式零改动）：11 字段 `author/editor/title/booktitle/journal/publisher/year/doi/key/alias/any` × 4 模式 `contains/prefix/exact/fuzzy`（文本默认 contains；doi/key/alias 默认 exact），term 之间 **AND**、`any` 对题名/作者/书名/期刊/出版社任一命中（内部 OR）；`--year` 收 `2003`/`=2003`/`1990-2000`/`>=2010`/`<=1999`/`>2010`/`<2010`；`fuzzy` 相似度 ≥ `min_score`（默认 0.75；`--strict` 抬到 0.90；doi/key/alias/year 禁 fuzzy；needle 归一后 <2 字符拒绝）；排序 = 分数降序 → 模式严格度 → 年份降序 → 第一作者姓升序 → key 升序（`--sort year|author` 另有口径）；默认 `--limit 20`（1–200），返回 `count/total_matched/truncated`；命中带 why-hit `matched` | `--author/--editor/--title/--booktitle/--journal/--publisher/--year/--doi/--key/--alias/--any`（可重复 = 多个 AND term）、`--mode FIELD=MODE`（必须同时给该字段条件）、`--sort relevance\|year\|author`、`--limit N`、`--min-score X`/`--strict`（互斥）、`--bibtex`、`--json`、`--db PATH` | `{db, kind:"advanced", query:{terms:[{field,value,mode}],sort,limit,min_score}, hits:[{key,score,matched:[{field,mode,score,snippet}],verified,title,author,year,record[,bibtex]}], count, total_matched, truncated}`；0 命中仍 rc0（人读给放宽提示）；非法字段/模式/年份表达式/互斥参数 rc2 |
| `rehash` ★ | 规范化规则升级后重算 `content_sha256`（B-126） | 默认 dry-run；`--apply` 才写（只改 `content_sha256`+`updated_at`） | `{db, dry_run, applied, records, stale, changes, sha256_before, sha256_after, updated_at, notes}` |
| `conflicts` ★（B-127） | **列出冲突条目**（只读；GUI 待裁决页数据源）。C 编号与 `CONFLICTS.md` 同源 | `--pending-only` 只列待裁决 | `{db, db_sha256, totals:{total,pending,resolved}, items:[{stable_no,record_key,bib_key,entry_type,title,author,year,issue,slug,file,at,incoming_sha256,pending,resolution,resolved_at,diff:{字段:{old,new}},advice,verified,method}]}`；库缺失/损坏 rc2 |
| `resolve`（keep/defer/adopt） | 逐条裁决：keep=保留旧值 / defer=待人工 / adopt=按指纹**逐字段**采用新稿留档值 | `--key --action (--all-fields\|--fields\|--incoming-sha256)`；**写前安全闸**见下 | 老键不变 + ★新增 `db_sha256_before/db_sha256_after/backup_path` |
| `resolve --action replace` ★（B-127） | **人工裁决 bib**：解析**恰好 1 条** bib（0/多条 rc2），bib key 必须等于库内 record key（**不改 key**）；以该条目 entry_type+fields 写库、重算 `content_sha256`；`verified/sources/method` 不动；目标冲突标 `resolution="adopted"` + `resolved_by="custom-bib"` + `resolved_bib_sha256` + `note` | `--incoming-sha256`（冲突指纹）+ `--bib PATH`；重叠冲突默认拒绝、`--allow-overlap` 标 superseded；`--dry-run` 预览 | `action=replace`、`replaced_fields:{字段:{old,new,remove}}`（dry-run 也有，GUI「预览差异」用）、`incoming_sha256`、`content_sha256_before/after`、`overlapping`/`superseded`、`resolved_by`、`resolved_bib_sha256` + 通用 3 键 |
| `resolve --adopt-all-pending` ★（B-127） | **一键全部采用**：全部待裁决冲突逐条按 adopt 语义应用 incoming 值；真写**单次原子写 + 单次备份**，每项 `adopted`+`resolved_at`；**同记录待裁决冲突字段重叠 → 整体拒绝 rc2 并列出冲突 #**（请逐条处理）；`--dry-run` 逐条预览不写库；与 `--key/--action` 互斥 | `--dry-run`、`--note`、写前安全闸 | `adopted:[{record_key,bib_key,incoming_sha256,conflict_index,fields:{字段:{old,new,remove}}}]`、`skipped`、`totals_before/after`、`db_sha256_before/after`、`backup_path`、`written` |
| `restore --backup PATH` ★（B-127） | 从备份**整库恢复**：备份须能被 `Store.load()`（行级 schema）校验通过；`--expect-db-sha256` 与当前库一致才写；原子写回；**不做 reopen**（已裁决冲突回到备份时状态）。**CLI 保留（向后兼容）；B-190 起 GUI 删除「从备份恢复…」入口、不再调用本命令**，回退改走 git 版本层 | `--dry-run` 只校验；`--expect-db-sha256` | `{db, backup_path, written, dry_run, records, backup_sha256, db_sha256_before, db_sha256_after}` |
| `report` / `report-conflicts` / `report-duplicates` | 生成中文待裁决清单（`CONFLICTS.md` / `DUPLICATES.md` / `duplicates.csv`），只写清单文件、不改库 | `--out DIR` | 老契约不变（`{db, reports, conflicts, totals[, pairs]}`） |
| `edit` ★（B-188） | **编辑一条库记录**（表单/单条 bib 输入）：`--bib PATH` 或 `--json-fields JSON`；默认 dry-run（逐字段 old→new、库 sha 前后、必填缺字段影响）；`--apply` 才写（sha 闸 +（可选）`--backup DIR` 自动备份 → 原子写 → 重新读库复核）。**B-190 起 `--backup` 可选**：不传 = 不写备份文件（`backup_path=""`、`edits[].backup_sha256=""`；GUI 改用 git 版本层），传了行为逐字节不变。`--new-key` 改 key（默认不勾）；旧 key 自动写进记录 `aliases`（key+alias 全局查重；撞车退 2）；该记录还有待裁决冲突 → 拒绝编辑并列出 C 编号。写库痕迹追加记录 `edits[]`（action/at/note/backup_sha256/字段级 old→new） | `--key K`（定位用；bib 里 key 必须等于它）`(--bib PATH | --json-fields JSON)` `[--new-key NEW] [--entry-type T]` `[--dry-run|--apply] [--expect-db-sha256 SHA] [--backup DIR] [--note TEXT] [--json]` | `{command, dry_run, db, key, new_key, entry_type_before/after, field_changes:{字段:{old,new}}, aliases_added, missing_before/after, db_sha256_before, db_sha256_after_estimate, db_sha256_after, backup_path, written, verification:{ok,problems,records,lint_errors,lint_warnings}}`；apply 后 `db_sha256_after` 为实测值；rc2 零写入 |
| `merge-records` ★（B-188） | **合并 2+ 条重复记录为 1 条**：默认 dry-run（逐字段对照 + 合并后 aliases）；`--apply` 才写（同 edit 的安全闸；**B-190 起 `--backup` 同样可选**，不传 = 不写备份、`backup_path=""`）。新规范 key 必须给（**B-201 起可等于 `--keys` 之一**，即沿用其中一条被合并键；其余旧 key 全进 `aliases`，正文引用不悬空）；两条旧 key 全部进 `aliases`；来源合并去重；记录数 -N+1；被合并记录 entry_type 不一致时必须 `--entry-type`；`--field-source 字段=KEY` 逐字段选源（近重复用；不给的字段按 `--keys` 顺序取第一条非空值）；待裁决冲突记录/新 key 撞车 → 退 2 零写入。写库痕迹 `edits[]`（action=merge、merged_keys、merged_entry_sha256、field_sources） | `--keys A,B[,...] --new-key K [--entry-type T] [--field-source FIELD=KEY ...] [--dry-run|--apply] [--expect-db-sha256 SHA] [--backup DIR] [--note TEXT] [--json]` | `{command, dry_run, db, keys, new_key, entry_type, records_before, records_after, field_comparison:{字段:{KEY:值, chosen, value}}, fields, aliases, db_sha256_before/after(_estimate), backup_path, written, verification}` |
| `ack-duplicates` ★（B-205） | **确认「两条不同条目」（不再提示）**：把一对/一组重复候选写进库同目录 `health-ignore.json`（B-204 sidecar）；**不改库**（命令前后 bibliography sha 不变）。默认 dry-run，`--apply` 才写；key 先 canonical 后唯一 alias；已确认过 → rc0 `added=false` 零写入（幂等）；当前不是 lint 正在提示的 pair/组 / sidecar 坏·version≠1·条目非法 → 退 2 零写入（绝不覆盖坏文件）。写盘保留原有条目、`version:1`、canonical key 排序、`reason`/`decided_at`。GUI「待处理 → 库健康 → 重复/近重复」行内「确认不同…」消费本命令并自动本地 commit（只 sidecar、不推送）。 | `--code near_duplicates\|duplicate_content --keys A,B[,...] [--reason TEXT] [--apply] [--json]` | `{command, dry_run, db, sidecar_path, code, keys, canonical_keys, added, written, entries_before, entries_after, db_sha256}` |
| `suggest-field-fixes` ★（B-188） | **只读**：缺 title/year 的字段归位候选（A 类 265 条口径：`\textit`/`\emph`/《》/「」题名标记、年份正则、`：`/出版社/書房 出版者形态；每条带 source/evidence/risk）与 **C 类「键/类型异常」**建议（`unknown_entry_type` registry = entry_type 不在已知表 **或** key 是 DOI/网址/占位符形态；entry_type 用 difflib 近似，key 给 `key_candidate`=作者姓+年份、`key_reason`）。**不写库、不调 AI**，GUI「字段归位预览」与它同源 | `--registry missing_field\|unknown_entry_type` `[--limit N] [--json]` | `{db, registry, count, rows:[{key, entry_type, missing, candidates:[{field,value,source,evidence,risk}], issues, sources, fields, key_suggestion?}]}` |
| `normalize` / `normalize-authors` / `normalize-editors` | B1/B2/B3 规范化（默认 dry-run） | `--apply --backup FILE`（写前须备份且 sha 与当前库一致） | 老契约不变 |

**`normalize-bib`（B-209，只读外部 `.bib`；不碰引用库）**：
- 命令：`python3 bibdb.py normalize-bib (--bib PATH | --stdin) [--json]`；**没有 `--db`**，与 `bib_db`/笔记仓无关，不写任何文件。
- 唯一实现：逐条调用 `slcn.bibnorm.normalize_bib()`（默认 `strip_urls=False`，与 `normalize` 同源）；不改 key、不新增规范化判据。
- 默认模式：stdout **只**输出规范文本，逐字节 = `normalize_bib(text)[0]`（UTF-8）；计数/诊断只走 stderr（不打印 bib 正文/字段值）。
- `--json`：stdout 只一个对象 `{entries_in, entries_out, changed_entries, unchanged_entries, kept_entries, normalized_text, entries:[{key, entry_type, status, changed_fields:[{name,old,new}], notes, risks, missing_required}], notes}`；`changed_fields` 只列变化（新增 `old=null`、删除 `new=null`、字段改名 = 旧名删除 + 新名新增，不猜映射；同名重复以首个为准），`missing_required` 复用 `slcn.bibcheck.check()` 的 must 级 `missing`/`empty`。
- 拒绝（rc2，**stdout 为空**，stderr 一行原因）：输入含 `@string{` / `@preamble{` / `@comment{`（大小写不敏感；先剥整行 `%` 注释）、0 条目（空/纯注释）、文件不存在/不可读、stdin 非 UTF-8。
- 引号未闭合（`parse_fields` 抛 `ValueError`）：**逐条**降级——坏条目 `status="kept"` 原样保留（`risks` 提示人工核对），好条目照常规范化；rc0，条目数/key 集合不变。
- 只读/隐私：运行期不创建/修改文件、不需要 notes 仓；stderr 只允许计数/key/字段名，不出现 bib 正文或字段值。退出码：`0` 成功（含 kept）、`2` 拒绝/用法错误。

**`lint` 的 B-143（批76）增补（只加不改；面向 GUI 库概况橙字下钻）**：
- `warnings` 字符串列表**原样保留**（人读唯一权威）。其中「字段冲突」一句改为 **pending 口径**
  （判据 `conflict_is_pending` = `resolved_at` 为空，与 `conflicts --json` 的 totals 同源）：
  有待裁决时写「字段冲突待裁决：N 条记录 / M 处差异（历史已裁决 X 条记录 / Y 处差异）」；
  pending=0 时写「字段冲突：待裁决 0 条 / 历史已裁决 X 条记录（Y 处差异，审计留痕，无需处理）」。
- 新增 `warning_items:[{code,registry,detail,action,count,keys_sample}]`：`warnings` 的结构化镜像
  （`detail` 与 warnings 字符串同文；`keys_sample` ≤5 个例子，**不塞全量**）。`registry` 是固定分类：
  `duplicates` / `near_duplicates` / `conflicts` / `missing_field` / `unknown_entry_type` / `alias` /
  `unverified` / `other`；字段冲突项另带 `pending_records/pending_total/resolved_records/resolved_total`。
- `counts` 新增 `conflicts_pending` / `conflicts_resolved`（各为 `{records,total}`；`records` 允许同一记录
  两侧都出现——它既有待裁决也有已裁决）。**老键 `counts.conflicts` 语义不变 = 全量冲突**（向后兼容），
  GUI 要显示待裁决数请用新拆分键。
- `conflicts` 子命令与其 `--json` **零变化**（B-143 不碰裁决逻辑）。

- **B-188 增补（只加不改）**：`lint --with-records` 时每个 `warning_items[]` 另带 `records`（受影响的**全量** key 列表；条目类 = 记录 key，`duplicate_content` = 重复组内 key，`near_duplicates` = `A≈B` 串，alias 类 = alias 本身）。**不带 flag 时输出与旧版逐字节相同**（`records` 键根本不出现）。GUI「待处理 → 库健康」展开按记录列出时用它。

- **B-204 增补（只加不改；人工裁决「两条不同条目」→ 不再提示）**：`lint` 读**库同目录** `health-ignore.json`
  （sidecar；缺文件 = 零变化、不新增 warning）。结构 `{"version":1,"acknowledged":[{"code":"near_duplicates|duplicate_content","keys":[...],"reason","decided_at"}]}`；
  key 先按 canonical、再按 library 内唯一 alias 解析（改名后仍命中）。`near_duplicates` 命中 ack 的 pair 从
  `warnings`/`warning_items` 剔除，`counts.near_duplicates` **只计未 ack**，新增 `counts.near_duplicates_acknowledged`
  （被抑制 pair 数，仅审计）；`duplicate_content` 组被 ack（keys 列全该组、升序集合相等）即整组不提示。
  sidecar 坏 / version 不对 / key 解析不了 → **lint errors 仍 0**、不抛异常，只多一条
  `warning_items[].code="health_ignore"`（registry=`other`，count=问题数）；坏条目忽略、好条目照常生效。
  `report-duplicates` 仍**全量审计**（不读 sidecar）；lint 只读，库字节 sha 不变。

**写前安全闸（B-127，`resolve` 全部写动作通用；`restore` 用同名单个参数）**：
- `--expect-db-sha256 SHA`：**完整** 64 位十六进制，与库当前字节 sha256 一致才写；不符/格式错 → 退出 2 且**零写入**（GUI 列表/指纹过期防线）。
- `--backup DIR`：PATH 是**目录**（不存在则写前创建；已存在同名文件 → 退出 2）→ 真的会产生写入时，写前复制一份 `bibliography-<UTCts>-<库sha前12位>.jsonl`（UTC 时间戳、定长）；dry-run / 幂等无变化**不产生备份**；同名备份已存在 → 退出 2（绝不覆盖旧备份）。
- **B-190：`edit` / `merge-records --apply` 的 `--backup` 也走本口径，但改为「可选」** —— 不传 = 完全不写备份文件（payload `backup_path=""`、`edits[].backup_sha256=""`），sha 闸 / 原子写 / 写后 reload 复核 / `edits[]` **一律保留**；传了则与 B-188 行为逐字节相同。GUI B-190 起用 git 仓库层做版本与回退，不再传 `--backup`；`normalize*` / `merge-duplicates` 的 `--backup` 仍必填（文件语义，本次未改）。
- **`--json` 键只加不改**：写动作统一带 `db_sha256_before` / `db_sha256_after` / `backup_path`（无备份时空串；`restore` 的 `backup_path` = 输入的备份文件）；退出 2 且给了 `--json` 时 stdout 尽量另出 `{"error":"中文原因"}`（GUI 有 stderr 回退）。
- 老调用（不传新参数）的**人读输出与老 JSON 键不变**；全部新能力都走新子命令/新可选参数。

**B-188 写库痕迹（`edits[]`；不写 sidecar）**：`edit` / `merge-records --apply` 在幸存的记录里追加一条
`{"at", "action":"edit|merge", "note", "old_key", "new_key", "entry_type_before/after", "changed_fields", "merged_keys", "merged_entry_sha256", "field_sources", "aliases", "backup_path", "backup_sha256"}`；
**B-190 起不带 `--backup` 时 `backup_path` / `backup_sha256` 均为空串**（其余键不变）；
只作审计/「已处理」展示，**不进 `content_sha256`**（摘要仍只由 entry_type+fields 决定）。
（B-204 的 `bib_db/health-ignore.json` 是**库外的重复告警裁决清单**，与本条 `edits[]` 无关、不改库字节。）
同时 `refresh_aliases()` 改为**保留记录里已有 aliases（并集）**：改名/合并写进去的旧 key 不会在下次 `save()` 被清掉（真实库实测旧口径会丢 79 条记录的 254 个别名）；全局唯一性仍强制（同一 alias 只允许一条记录持有、alias 不得与别的 canonical key 同名）。

**mac 界面的切换进度（第 95 条起，按用户定的顺序，第 99 条完成；引用库 5 页按第 132 条补）**：
**十四页全部改调引擎** ——
初排转换 / 文献回读校验 / 一校核查 / 生成目录（95）/ 检查tex信息（96）/
删除临时文件 + 编译全部（98）/ **合并文件（99）** /
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
| LibreOffice | **条件必需**（仅「导出原稿 PDF」＋ Word 的 MathType/OLE 公式图 WMF/EMF → PDF 时需要；LaTeX 稿/OMML 稿可不装） | 条件必需（同） | 条件必需（同） |
| `vendor/` | mac 轮子（`lxml` 是编译件） | **必须是 Windows 轮子**（打包时按平台装） | Linux 轮子 |

**引擎已经做掉的平台事**（`slcn/platform.py`）：

1. **UTF-8 标准输出**：`convert.py` / `checkbib.py` 进 `main()` 第一件事就 `utf8_stdio()`。
   Windows 的 stdout 默认是控制台代码页（cp936），中文日志经管道会乱码、甚至
   `UnicodeEncodeError` **崩在转换中途**；macOS/Linux 上是空操作。
2. **LibreOffice 定位**：`find_soffice()` = `SLCN_SOFFICE` 环境变量 → `PATH` → **三平台**常见安装位置
   （Windows 的安装器**不**把 `soffice` 写进 PATH；macOS 的 `.app` 同理）。
   **条件必需 + fail-soft（第四十七批起）**：Word 稿含 MathType/OLE 公式（或要做「导出原稿 PDF」）时
   LibreOffice 是**必需依赖**；缺 `soffice` 时**不报错、不阻断出稿** —— Word 通道把 MathType/OLE
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

- **长路径**：Windows 默认 260 字符上限。稿件目录名很长（`2026年第5期(90)/07 Jiayi_He/original/…`）
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
   例：**`--metadata-json`（第 63b 批）**不传时产物/argv/stdout 与批 63 逐字节相同；
   老外壳不传它也能跑（只是用不上人工填写的元数据）。
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

## 十一、失败诊断：`diagnose.py`（`diagnose.v1`，B-285a）

失败之后先跑它：把「入口名 + 退出码 + 日志文件」翻成**一句结论 + 证据 + 2–4 条下一步**。
只读（只读日志尾部 ≤16KB），不写产物、不联网；诊断本身不改变任何既有退出码。

```bash
python3 Engine/diagnose.py --entry convert.py --exit-code 2 --log "/tmp/convert-失败.log"
python3 Engine/diagnose.py --entry compileall.py --exit-code 1 --log "…" --json
```

- `--entry` 必填：**入口文件名**（如 `convert.py`）；带路径/以 `.` 开头的会被拒。
- `--exit-code` 必填：那次运行的退出码；`0` 表示没有失败，也给报告但不硬猜（`unknown` 低置信）。
- `--log` 必填：失败那次运行的日志；只读尾部 ≤16KB（更早的旧行不参与分类）。
- `--json`：stdout **恰好一个 JSON 对象**；默认是人类可读摘要。
- 逐字节确定：同一日志 + 同一 `--generated-at` → 同一字节（复现/测试用）。

| 退出码 | 含义 |
| --- | --- |
| `0` | 诊断成功（**不代表**被诊断那次运行成功） |
| `2` | 参数错误（日志不存在、入口名不是文件名等用法错误） |

既有入口的退出码语义（§二/§三/§五）一律不变。

`diagnose.v1` 顶层 13 个契约键（顺序即 JSON 键序）：

```json
{
  "schema": "diagnose.v1",
  "entry": "compileall.py",
  "exit_code": 1,
  "category": "compile_tex_error",
  "severity": "error",
  "title": "编译时报错（TeX 源码问题）",
  "reason": "为什么判成这一类（一句中文，不含 traceback）",
  "evidence": {
    "log_tail": ["日志尾部，最多 10 行"],
    "matched_lines": ["参与判类的行，最多 10 行"],
    "artifacts": ["日志里提到的产物路径，最多 10 个"]
  },
  "next_steps": ["编辑照着做的下一步，2–4 条"],
  "doc_anchor": "ENGINE_API.md §十一 失败诊断（diagnose.v1）",
  "confidence": 0.79,
  "generated_at": "2026-10-30T12:00:00+0800",
  "engine_version": "1.4.1"
}
```

`category` 只有下面 17 个值；**无法归类一律 `unknown` 且 `confidence < 0.5`**，不硬猜：

| category | 一句话 | 典型证据（真实日志写法） |
| --- | --- | --- |
| `usage` | 用法/参数不对 | `usage:`、`error: the following arguments are required` |
| `source_missing` | 找不到源文件 | `找不到源文件`、`No such file or directory` |
| `source_type` | 源文件类型不支持 | `不支持的文件类型`、只支持 `.tex` / `.docx` |
| `source_empty` | 源文件是空的 | `源文件是空的`、`0 字节` |
| `metadata_invalid` | 稿件基本信息读不出来 | `JSONDecodeError`、`稿件基本信息读取失败` |
| `template_unavailable` | 模板缺失/不可用 | `SLCN-MIX.sty`、`找不到模板` |
| `output_conflict` | 输出位置冲突，拒绝覆盖 | `输出目录已存在`、`拒绝覆盖` |
| `compile_tool_missing` | 没装编译工具 | `找不到命令 xelatex`、`请确认已安装 TeX Live` |
| `compile_tex_error` | 编译时报 TeX 错误 | `! Undefined control sequence.`、`xelatex 第 N 步失败` |
| `compile_bib_error` | 参考文献处理失败 | `biber`、`I couldn't open database file` |
| `compile_no_pdf` | 编译结束但没有 PDF | `未生成 PDF` |
| `report_read_error` | 报告/JSON 读不出来 | `报告文件解析失败`、`JSONDecodeError` |
| `permission_denied` | 权限不够 | `Permission denied`、`Errno 13` |
| `file_locked` | 文件被别的程序占用 | `WinError 32`、`另一个程序正在使用此文件` |
| `disk_full` | 磁盘满了 | `No space left on device`、`Errno 28` |
| `cancelled` | 被用户/信号中断 | 退出码 130/143 或负数、`进程被用户终止` |
| `unknown` | 还不知道为什么失败 | 没有任何已知关键字命中 |

### 报告怎么读（四类报告的统一摘要头）

一校核查 / 回读校验 / 信息检查 / 编译四类报告，最前面都是**同一个摘要块**：
`**摘要**：一句结论` → `**计数**：…` → `**最需处理（top 5）**` → 折叠的
`<details><summary>全部明细（N 条）</summary>`。结论先行、计数定规模、top5 决定先看哪几条；
要逐条核对再展开 `<details>`。旧正文一字未删，在摘要块之后按原顺序排列。

| 报告 | 摘要头落在 |
| --- | --- |
| `<stem>_一校核查.md` | 就地插在最前，旧正文不动 |
| `<stem>_回读校验报告.md` / `合并后回读校验报告.md` | 就地插在最前，旧正文不动 |
| `信息检查<时间戳>.txt` | **逐字节不变**（旧产物契约） |
| `信息检查<时间戳>_摘要.md` | 新增文件：摘要头 + 逐篇明细 |
| `<期目录>/编译报告.md` | 新增文件：结论 + 成功/失败计数 + 每篇失败首条 `!` 行与 `.log` 路径 |

`engine_main.py` 的 ENTRIES（11→12）与双壳接线在 **B-285b** 完成；本批 `diagnose.py`
可直接运行（`python3 Engine/diagnose.py`），不依赖打包 dispatcher。

## 十五、入口：`dirpush.py`（目录摘要推送，B-225）

外壳只拼参数、只读 stdout/stderr；**所有判据在引擎**（`slcn/pushcopy/`）。

```bash
python3 Engine/dirpush.py --issue <期目录> [--out <输出根>] [--with-en-toc] [--json] [--dry-run]
python3 Engine/dirpush.py --issue <任一期目录> --annual [--year <yyyy>] [--include-issues 1,2,3] \
    [--out <根>] [--json] [--dry-run]
```

- `--issue` 必填：当期=具体期目录；年度=该年任一期目录（扫其父目录 `YYYY年第N期*`）。
- `--out` 缺省 `~/Documents/sysu/编辑部/目录摘要推送/`（不写稿件仓）。
- 输出（契约冻结）：当期 `<out>/<YYYY> 年第 <N> 期/逻辑学研究<YYYY>年第<N>期目录摘要.docx`；
  年度 `<out>/<YYYY> 年/逻辑学研究<YYYY>年总目录.docx`；同名**直接覆盖、不留 .bak**；不产 PDF。
- 每期生成成功后写 `<该期输出目录>/目录摘要设置.json`：`{"schema":1,"year":<y>,"issue":<n>,"with_en_toc":<bool>}`
  （无时间戳；年度模式**只读**它还原各期体例，从不写）。
- `--dry-run`：只采集+校验、**不写任何文件**；`outputs` 给计划路径（GUI 预览用）。
- `--json`：stdout **恰好一个 JSON 对象**（日志全走 stderr），字段：
  `schema/ok/mode/dry_run/outputs[]/issue{year,number,vol,total,header_lines[],with_en_toc,column}/
  counts{zh_entries,en_entries,articles,abstracts_ok,abstracts_missing}/warnings[]/missing[]/
  annual{year,included[],excluded[],missing_issues[],issues[{number,with_en_toc,status}]}|null`。
  `with_en_toc` 缺省推断 = 输出目录已有设置则沿用，否则 false；`--with-en-toc` 仅当期模式。
  输入/用法错误时 stdout 仍给同 schema 对象（`ok:false`、issue/annual=null），stderr 中文原因。
- 退出码：0 成功（含 warning）/ 2 输入或用法错误（无 `000 Table of Contents`、参数冲突、年度 0 期）/
  1 内部异常（traceback 走 stderr）。
- 计数口径：`abstracts_ok` = 有可用**中文**摘要的篇数；`abstracts_missing` = 跳过篇数
  （中文摘要为模板占位符时按缺失处理，不回落英文摘要；年度模式两项均为 0）。
- 只读保证：跑前跑后输入期目录 sha256 清单一致；同输入两遍 docx 逐字节相同（固定 zip 时间戳/docProps）。
