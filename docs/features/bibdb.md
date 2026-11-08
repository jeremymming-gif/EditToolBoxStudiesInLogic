---
title: 编辑部引用库
description: 把已核实的参考文献沉淀成编辑部资产，核实前先查库。
---

# 编辑部引用库

引用库（bib 库）把**已核实的参考文献**沉淀下来：以后核实新稿时先查库，命中就走现成版本，
不重复花钱检索、也让体例更统一。

> **Flet 单壳支持写库**：库概况与查库仍是只读；「入藏」支持 `.bib` 文件 / 整期目录 /
> 粘贴 bib 文本三种来源（先 `--dry-run` 预览新增/更新/冲突与明细，二次确认后带
> `--expect-db-sha256` 真写）；「待处理」含字段冲突逐条裁决与版本库状态区（写后自动本地
> commit；推送 / 回退都要二次确认）。「查库」分基本 / 高级两页签：高级 = 多字段 AND +
> 包含/前缀/精确/模糊 + 年份表达式 + 排序/最多/严格，命中给 why-hit 并可直接复制 BibTeX；
> 「设置」页集中库文件统计（路径 / 是否存在 / 条目数）、版本层（提交 / 推送 / 回退）与
> **核实成本闸门**（核实上限 / 先算账 / 全量核实 / 先查库，存 `settings.bibdb` 段）。
> 另有四个写库视图：**编辑**（按 key 打开、整份字段预填、dry-run 差异预览）、
> **查重合并**（候选对 → 两键对照 → 逐字段选来源 → 预览 → `merge-records --apply`）、
> **字段归位**（`suggest-field-fixes` 候选 → 勾选 → 逐条 dry-run → 合 1 条提交）、
> **库健康**（lint 计数 + 最近库提交留痕 + 批量裁决 dry-run 预览 → 二次确认 → 合 1 条提交）。

界面只有一个：Flet 单壳的**引用库 10 个视图 / 4 组**（查阅：概况、查库；采录：规范化、入藏；
治理：待处理、编辑、查重合并、字段归位；维护：库健康、设置），底层与命令行共用同一份
`Engine/bibdb.py`（随包交付）；「参考文献规范化」是采录组的**只读**视图，只出报告（见
`sources-flet/app/ui/pages.py` 与 `sources-flet/app/ui/ia_map.py`）。
「待处理」视图分字段冲突与版本库状态两区（逐条 keep/adopt/defer、编辑并替换、一键全部采用；写库成功后
自动本地 commit 库文件）；「编辑 / 查重合并 / 字段归位 / 库健康」是独立写库视图（库健康视图含 lint 计数、
已处理留痕与批量裁决 dry-run，写成功后同样自动本地 commit）。

## 页面集（10 个视图 / 4 组）

| 组 | 页面 | 作用 |
|---|---|---|
| 查阅 | **库概况** | 条数 / 期数 / 最近更新 / **字段冲突（待裁决 / 历史已裁决）** / lint 健康（**橙色 warning 可「查看明细」按类下钻**：每条给 key/原因/建议操作；**author/editor 未倒装（含机构名）只报告、不自动改**）/ 库路径；一键打开库文件夹 |
| 查阅 | **查库** | 按 key（也认别名）/ DOI / 题名查询；显示是否已核实、来源期；一键复制 BibTeX。Flet 版分**基本检索 / 高级检索**两页签：高级支持 10 个字段 × 4 种匹配（包含/前缀/精确/模糊，年份/DOI/别名不收模糊）多行 **AND**、年份表达式（`2003` / `1990-2000` / `>=2010`）、排序（相关度/年份/作者）、最多 1–200 条、严格（模糊门槛 0.90），命中行给 **why-hit**（每条条件的字段/模式/分数/片段），详情只读 |
| 采录 | **规范化** | 交回 `.bib` 文件或粘贴文本 → 识别键 / 逐字段对比 / 语法与规范检查 → 产出《回读校验报告》；只读，不并回、不写库（并回仅 CLI） |
| 采录 | **入藏** | 选单篇 `.bib` / 整期目录 / **粘贴 bib 文本** → **预览（dry-run，不写库）** → 确认导入；来源期/篇自动推断且可改；粘贴时「入库口径」单选：**未核实（默认）** / 已人工核对 / 成品 bib（期+篇） |
| 治理 | **待处理** | 字段冲突 + 版本库状态两区（详见下文「待处理中心」）；库健康 / 编辑 / 查重合并 / 字段归位是独立视图 |
| 治理 | **编辑** | 逐字段编辑单条记录 → 预览 → 二次确认 → 原子写；写后本地 commit。 |
| 治理 | **查重合并** | 重复记录候选 → 勾选 → dry-run 预览 → 合并成 1 条提交；**只报告，不自动合并**。 |
| 治理 | **字段归位** | `suggest-field-fixes` 候选 → 勾选 → 逐条 dry-run → 合 1 条提交。 |
| 维护 | **库健康** | lint 计数 + 最近库提交留痕 + 批量裁决 dry-run 预览 → 二次确认 → 合 1 条提交。 |
| 维护 | **引用库设置** | 库文件（路径 / 是否存在 / 条目数 / 大小，可打开库文件夹）+ Git 版本层（分支 / upstream / 未推送 / 脏否 / 最近 3 条提交；「提交…」「推送…」「回退到上一个提交…」都要二次确认；推送只推连续仅含 `bib_db/` 的提交、绝不 force；绝不自动 push/pull/切分支）+ **核实成本闸门**（核实上限文本框实时校验、先算账 / 允许全量核实 / 先查引用库三个开关、当前生效值预览，保存进 `settings.bibdb`；默认一个参数都不加） |

> **「待处理」与「库概况」橙字是两个数据源**：「待处理」页读引擎 `conflicts`
> （字段冲突裁决；默认只列待裁决，已裁决可开关查看、灰显、编号不重排）；「库概况」页橙色文字读引擎
> `lint`（库健康检查：重复记录 / 同一作品多记录 / 字段冲突 / 缺字段 / entry_type 笔误）。点
> 「查看 warning 明细」可按类下钻：每条给 key 例子、原因与建议操作；**重复记录合并只报告、不自动合并**；
> 字段冲突类显示「待裁决 N / 历史已裁决 M」，并可一键跳到「待处理」页。
>
> **第三个数据源是「规范化」的报告文件**：它只读交回 `.bib` 与原件，产出《回读校验报告》；
> 报告是 `.md` 文件，不进库、不写库。要入藏，先按报告用 CLI 并回，再走同一页的「入藏」。

## 查库与设置（Flet 单壳）

- **查库**是只读页：只调 `bibdb.py query|query-advanced --json`，不写库、不联网、不调 AI。
  基本检索按 key（也认别名）/ DOI / 题名（模糊匹配）三选一，回车即查；高级检索每行
  「字段 × 匹配 × 值」，行间 **AND**，空值行跳过；字段 10 项（作者/编者/题名/书名/期刊/出版社/
  年份/DOI/别名/任意），匹配 4 项（包含/前缀/精确/模糊）；年份行可直接写 `2003`、
  `1990-2000`、`>=2010`；「模糊」只对文本字段有效，年份 / DOI / 别名会自动收回「精确」。
  排序只认相关度 / 年份 / 作者，最多 1–200 条（默认 20），可勾「严格（模糊门槛 0.90）」。
- 结果固定六列（题名 / 作者 / 年份 / key / 状态 / 来源），单选看详情：已核实标记、来源期、
  高级命中的 **why-hit**、只读 BibTeX；「复制 BibTeX」「在运行日志里显示」都在详情区。
- **引用库设置**集中两件事：① 库文件（路径 / 是否存在 / 条目数 / 别名合计 / 大小，
  「打开库文件夹」）；② Git 版本层（分支、upstream、未推送 / 落后、库文件脏否、
  最近 3 条提交）。提交 / 推送 / 回退**都要二次确认**；推送只推「连续仅含 `bib_db/`
  文件」的前缀，遇到其它提交整次拒绝、绝不 force；回退 = checkout 上一个提交的库文件
  再生成新提交（不 hard reset、不推送）。git 不可用时只黄字提示，不碰库内容。

### 分页 / 库在翻页间变化 / 查询复用（P06）

查库结果**分页加载**（列表型查询：题名基本检索、高级检索），不再靠放大 `--limit` 冒充「载入更多」：

- 每页固定 **100 条**（界面常量 `PAGE_SIZE=100`；引擎单页上限仍是 1–200）。
- 首查显式 `--offset 0`；点「载入更多」用**同一条件快照**（方式 / 题名文本 / 排序 / 高级条件）+
  `--offset <已载入条数>` 续取下一页；`--limit` 始终是 100，绝不改成 200。
- 引擎返回的 `page` 六字段是分页口径的唯一来源，界面只消费不重算：
  `offset`（0 基，跳过命中记录数）、`limit`、`count`（本页条数）、`total_matched`（切片前全量）、
  `has_more`（`offset+count < total_matched`）、`next_offset`（有下一页时 = `offset+count`，否则 null）；
  另带 `db_sha256`（所读库文件字节 SHA-256，小写 hex）用于判断库是否在翻页间变化。
- 结果计数：「已载入 100 / 共 250 条」；翻到末页显示「已全部载入（共 250 条）」。
  单条定位（key / DOI）不产生 `page`，一次查完、不显示「载入更多」。
- **条件变化即重置**：改检索方式、改题名文本、改排序、提交高级检索条件，都会清空已载入结果与选中行，
  从 `offset=0` 重查（旧请求的迟到结果按世代丢弃，不拼接）。
- **库在翻页间变化**（如另一入口写了库）：续页返回的 `db_sha256` 与本轮首查记录的不一致时，
  **不合并新页**，保留已载入结果并提示「引用库已变化，请刷新后重新查询（未合并新页）」，
  页面给「刷新后重查」入口（= 按当前库从第一页重查）；引擎返回的 `page.offset` 与已载入条数不一致时
  同样拒绝拼接，提示刷新后重查。
- **查询复用**：选中身份用引擎命中的 `key`（不是表格行号），第 201 条等跨页命中同样可选中；
  来源期与「已核实 / 未核实」直接用引擎返回值，不在界面改写；「复制 BibTeX」用引擎给的
  `bibtex`（缺失时按 key 向引擎只读取一次），界面**不自己拼** BibTeX；「编辑此条」只传 key，
  编辑页打开后按当前库重新核对。查询、复制、翻页全程**零写库、零 Git**。
- 限制：分页只覆盖列表型查询；界面不缓存跨查询结果，重新查询永远从第一页开始；
  库变化的判定以整文件 sha 为口径，任何字节变化都会要求刷新。

## 写库高级能力（Flet 单壳）

「编辑 / 查重合并 / 字段归位 / 库健康」四个视图与设置页的核实成本闸门，全部走同一套写库三步：
**dry-run 预览**（记下 `db_sha256_before`）→ **二次确认** → 去掉 `--dry-run`、加 `--apply` 并带
`--expect-db-sha256` 真写 → 写后只做 **1 条**本地提交（只 `git add bib_db/bibliography.jsonl`，
绝不推送、绝不切分支）。任何一步失败都显示引擎中文错误、不回滚已写数据；版本层失败只黄字。

| 视图 | 引擎命令（Flet 参数 ↔ argv） | 关键行为 |
|---|---|---|
| 编辑 | `edit --json --key K --json-fields <完整字段字典>`；预览 `--dry-run`，真写 `--apply --expect-db-sha256 SHA`；可选 `--new-key` / `--entry-type` | 编辑器**整份替换**字段（引擎语义，不是 patch）：提交的是表单全部字段，空值 = 删除；预览逐字段 `旧 → 新`；改 key 时旧 key 自动进 `aliases`；记录有未裁决冲突时引擎拒绝编辑并提示先去「待处理」 |
| 查重合并 | `merge-records --json --keys A,B --new-key K`；逐字段 `--field-source field=key`；预览 `--dry-run`，真写 `--apply --expect-db-sha256 SHA` | 候选对来自 `report-duplicates`（引擎把 `duplicates.csv` 写进 `--out` 临时目录，读完即删，不写笔记仓）；两键对照逐字段选来源 → 预览合并后 aliases 与记录数 → 确认；合并结果可用「设置 → 回退」再退回（只提示，不自动回退） |
| 字段归位 | `suggest-field-fixes --json --registry missing_field --limit N`（只读候选）；应用 = 逐条 `edit --json-fields <原字段+选中候选>` | 每条给缺失字段与候选值/证据/风险；勾选后先逐条 dry-run 预览，确认后逐条真写（sha 链式更新，中途失败立即停），**全部写完合 1 条提交** |
| 库健康 | `lint --json` + `conflicts --json --pending-only` + 版本层最近库提交 | 记录 / 已核实 / 未核实 / 冲突（待裁决、已裁决）/ 近似重复 / 需反转（作者·编者·机构）/ errors·warnings 计数；已处理留痕 = 最近库提交 sha + 主题；「确认非重复」走 `ack-duplicates`（只写 `health-ignore.json` sidecar、不改库、不自动提交） |
| 库健康 · 批量裁决 | 全部采用：`resolve --adopt-all-pending`；所选：逐条 `resolve --action adopt --incoming-sha256 …`；都先 `--dry-run` 再 `--apply` | 先出账（待裁决 N → M、受影响记录/冲突条数、sha 前后）→ 二次确认 → 真写 → **合 1 条**提交；当前没有待裁决项时按钮置灰并提示「零写入」 |

**核实成本闸门（设置页，R6）**：`settings.bibdb` 段四个键 —— `verify_max`（留空 = 引擎默认 120 条、
不传 `--verify-max`；`0` = 不限，真传）、`verify_plan`（`--verify-plan`：只算账）、`verify_all_refs`
（`--verify-all-refs`：越过上限，勾选要二次确认）、`lookup_enabled`（关 = `--no-bibdb`）。
默认四键都是「不加参数」，普通初排 argv 与接入引用库之前逐字节相同；只有走核实链路（AI 面板）时才
注入这些参数。旧 Tk 配置的 `bibdb.{verifyMax,verifyPlan,verifyAllRefs,lookupEnabled}` 会自动迁移。

## 待处理中心

「待处理」页在保留逐条裁决能力的基础上，把「库健康」与「已处理」并入同一页，并提供三种写库入口
（都走同一套安全校验：dry-run 预览 → 用户确认 → `--expect-db-sha256` 校验 → 原子写 → 写库成功后自动本地 git commit；绝不自动 push）：

- **共享条目编辑器**（入口：查库详情「编辑此条」、库健康记录行、待处理页行）：字段表单 + 原始 BibTeX；
  改 raw 点「解析校验回表单」由引擎解析校验；「预览」= `bibdb.py edit --dry-run`（字段级 old→new、库 sha、
  aliases 变更、lint 影响）；「写入」二次确认；key 默认只读，勾「改 key」后旧 key 自动写进记录 `aliases`（可查）。
- **对照 / 合并**（入口：库健康 → 重复记录 / 同一作品多记录）：左右（窄窗口上下）字段对照、差异高亮、
  逐字段选 A/B；新规范 key 自动建议（作者姓+年份，可手改）；底部预览合并后 aliases 与库 sha；
  确认后 `bibdb.py merge-records --apply`（记录数 -1、旧 key 全进 aliases、来源合并）。**完全不自动合并**。
- **字段归位预览**（入口：库健康 → 缺 title / 缺 year）：候选来自 `bibdb.py suggest-field-fixes`
  （每条给候选字段/值/证据/风险）；勾选后「应用选中」逐条 `edit --apply`（统一 sha 校验；整批写完后自动本地提交一次）。
  无候选的只展示、不自动写；entry_type 笔误 / DOI-key / 占位符 key 可先修 entry_type / key。

性能：列表数据一次加载后进页不自动重载（按库 sha/mtime 判新鲜，「刷新列表」手动；写库成功后局部失效）；
已处理行默认一行摘要、点开明细；长列表惰性渲染；「显示已处理」是本页本地开关，不影响其它页。

## 入库规则

- 往期**已出版成品 bib** = 已核实，可入库；
- **AI 结果不直接入库**：AI 只给建议，需人工核对后才可作为已核实条目入藏；
- **粘贴 bib 文本**默认按**未核实**入库；确已人工核对才显式选「已人工核对」
  （引擎 `--verified`）；选「成品 bib（期+篇）」并填来源期/篇 = 与单篇 `.bib` 现状同一规则
  （`published-bib`，入已核实）；
- 库存储为 JSONL（进私有 git，天然有版本历史）；每条记录保留来源（期/篇/文件）、核实方式与冲突留档。

## 与成本控制的关系

核实链路会按顺序：

1. 只取**正文实际引用**的条目（默认上限 120，可调）；
2. 逐条查引用库——命中已核实条目（DOI 精确，或题名高置信 + 作者/年份相容）→ **跳过检索与 AI**；
3. 未命中才调用 DeepSeek 独立 Search API（检索证据，按成功请求计费）与/或 AI；
4. 全程不修改你的 `reference.bib`，字段差异只写进任务书待人工裁决。

::: tip 先算账
在「引用库 → 设置」打开 **先算账**，跑一次只打印核实范围与预计检索/AI 次数，**零费用预览**。
:::

## 待裁决清单

冲突清单与重复候选清单是**给人看的**（按期刊篇、逐字段旧值 vs 新值、带勾选框）；
它们不含生成时间，同一份库重复生成内容相同，可直接用 git diff 判断「有没有新冲突」。

## 待裁决与裁决

> Flet 单壳的「待处理」页支持本节的三按钮、编辑替换、一键全部采用，以及写库成功后的自动本地
> commit（只提交库文件、绝不推送；git 不可用只黄字提示）。「待处理」页保留版本库状态区，
> 「引用库 → 设置」页集中库文件统计与版本层（「推送…」「回退…」都要二次确认）。

**列表**：一条冲突 = 一个固定的 **C 编号**（按全部冲突含已裁决分配，裁决后**不重排**）；
按「期 → 篇 → C 编号」分组，行内显示库内 key / 题名 / 作者、来源（期/篇/文件）、逐字段
「已核实旧值 → 新稿值」、建议、冲突指纹（`incoming_sha256` 短码）；库内已核实(verified)
记录会高亮提醒（采用新值 = 改已出版成品里人工核对过的字段）。已裁决行灰显并显示裁决动作。

**行内三按钮**（写库；写前校验库 sha；成功后自动本地提交）：

| 按钮 | 引擎动作 | 语义 |
|---|---|---|
| 保留旧值 | `resolve --action keep` | 保留库内已核实旧值，只写裁决元数据（不改字段） |
| 采用新值 | `resolve --action adopt` | **逐字段**采用库内留档的新稿值（只改该冲突字段）；重叠冲突默认拒绝 |
| 待人工 | `resolve --action defer` | 标为待人工查证，先从待裁决列表移走（不改字段） |

**一键全部采用**：按钮前置红字高亮警示（必须先看清风险）→ 引擎 `--adopt-all-pending --dry-run`
逐条预览（old→new，库不变）→ **二次确认**后才真写（单次原子写 → 自动本地 commit 一次）。
同记录待裁决冲突字段重叠时引擎拒绝并列出，需逐条处理；默认**不提供无确认执行**。

**编辑裁决 bib…**：预填库内条目（`query --key --bibtex`）→ TextEditor 编辑（可整条改写）→
`resolve --action replace --dry-run` 预览逐字段差异（含字段删除）→ 确认写库。
审计写进冲突元数据（`resolution=adopted` + `resolved_by=custom-bib` + `resolved_bib_sha256` + note）；
**不改 key、不改 verified/sources/method**；重叠冲突默认拒绝（可显式勾选 `--allow-overlap` 标 superseded）。

**版本库状态区（git）**：库文件在私有 notes 仓里，写库成功后 GUI
自动做一次**本地 commit**（只 `git add bib_db/bibliography.jsonl`，绝不 push/pull/切分支）；
Flet 的「设置」页把库文件统计与版本层集中在一起（「待处理」页仍保留状态区）；状态显示当前
分支 / 库文件脏否 / 未推送提交数 / 最近 3 条库提交，并提供三个都要二次确认的按钮：
「提交…」（手动补提交）、「推送…」（**唯一**推送入口，失败显示 git stderr 摘要，不做 force）、
「回退到上一个提交…」（回退目标 = **上一次触及 `bib_db/bibliography.jsonl` 的提交**：按
`git rev-list HEAD -- bib_db/bibliography.jsonl` 取历史，checkout 其中上一个版本再生成新 commit；
**不是整体上一提交（HEAD 的父提交）**；**不 hard reset、不推送**）。库文件有未提交改动（`dirty_db`）时
**拒绝回退（零写）**；已到最早可回退版本、或回退后内容没有变化 → 明确返回 `ok=False`，
不伪造成功。git 不可用/非 repo/身份未配置 → **黄字 fail-soft**：写库照旧成功、
数据不回滚，只是没有版本提交。

**安全校验**：所有写操作串行（运行中按钮置灰）；先预览后确认；写前 `--expect-db-sha256`
（列表过期 → 引擎拒绝写并提示刷新）；原子写 + 写后复核；版本层失败只黄字、绝不回滚已写数据；
写库失败显示引擎中文错误。**不点这些新按钮时，CLI 与其它入口的参数/行为不变**（新能力都是加法）。

## 命令行（高级）

```bash
# 库概况（JSON，供界面/脚本用）
python3 Engine/bibdb.py stats --json

# 查一条
python3 Engine/bibdb.py query --key Fine1975 --bibtex --json

# 分页（列表型查询：题名 / 高级检索；--offset 0 基，跳过命中记录数，默认 0）
python3 Engine/bibdb.py query --title 逻辑 --limit 100 --offset 0 --bibtex --json
python3 Engine/bibdb.py query --title 逻辑 --limit 100 --offset 100 --bibtex --json

# 入藏预览（不写库）
python3 Engine/bibdb.py import-bib --bib /path/to/reference.bib --issue "2026年第2期(87)" --slug 02_Hai_Xing --dry-run --json

# 待裁决条目（JSON，界面列表的数据源；只读）
python3 Engine/bibdb.py conflicts --json --pending-only

# 逐条裁决（keep / defer / adopt；写前校验库 sha；GUI 成功后自动本地 commit）
python3 Engine/bibdb.py resolve --key Fine1975 --action keep --incoming-sha256 <冲突指纹> \
  --expect-db-sha256 <库 sha> --json

# 一键全部采用（dry-run 先看逐条 old→new；去掉 --dry-run 才写）
python3 Engine/bibdb.py resolve --adopt-all-pending --dry-run --json

# 编辑裁决 bib（恰好 1 条、key 必须等于库内记录 key；dry-run 先预览 replaced_fields）
python3 Engine/bibdb.py resolve --key Fine1975 --action replace --incoming-sha256 <冲突指纹> \
  --bib /tmp/edited.bib --dry-run --json

# 编辑一条（--json-fields 是**整份字段字典**；先 --dry-run 预览，真写加 --apply 与 sha 闸）
python3 Engine/bibdb.py edit --key Fine1975 --json-fields '{"title":"题名","author":"作者","year":"1975"}' --dry-run --json
python3 Engine/bibdb.py edit --key Fine1975 --json-fields '…' --apply --expect-db-sha256 <库 sha> --json

# 查重合并（先 dry-run 看 field_comparison 与 aliases，再 --apply）
python3 Engine/bibdb.py merge-records --keys A,B --new-key A --field-source title=B --dry-run --json

# 字段归位候选（只读）
python3 Engine/bibdb.py suggest-field-fixes --json --limit 50

# 查重报告（产物要写临时目录；默认 --out = 库目录 = 私有笔记仓）
python3 Engine/bibdb.py report-duplicates --out /tmp/bibdb-dupes --json

# 确认两键不是重复（只写库旁的 health-ignore.json，不改库）
python3 Engine/bibdb.py ack-duplicates --code near_duplicates --keys A,B --reason "同一本书" --json

# 重新生成待裁决清单
python3 Engine/bibdb.py report --out ../EditToolBox-notes/bib_db

# 回退一个库版本（Flet 在「引用库 → 设置」页点「回退到上一个提交…」，等价于）：
# 回退目标 = 上一次触及库文件的提交：取 rev-list 历史的第 2 条（history[1]），不是整体上一提交
git -C ../EditToolBox-notes rev-list HEAD -- bib_db/bibliography.jsonl   # 第 2 条即 history[1]
git -C ../EditToolBox-notes checkout <history[1] 的 sha> -- bib_db/bibliography.jsonl
git -C ../EditToolBox-notes commit -m "引用库：回退到上一个提交" -- bib_db/bibliography.jsonl
# 库文件有未提交改动（dirty_db）时拒绝回退、零写；已到最早可回退版本时明确失败（ok=False），不伪造成功；
# 不 hard reset、不 force、不 push。
# 兼容：引擎 CLI 仍保留 `restore --backup PATH`（GUI 不调用）
```

> 解释器按本机填写（Windows `py -3.14`，macOS / Linux `python3.14`）；示例路径 `/path/to/...` 换成本机路径（如 `C:\...\reference.bib`）。
> 打包版：Windows 用与界面 exe 同目录的 `SLCNEngine.exe bibdb.py …`；macOS 的引擎 sidecar 随
> `编辑部工具箱.app` 放在 `Contents/MacOS/SLCNEngine`。

## 失败时看什么（参考文献回读校验 / 引用库）

参考文献回读校验（`checkbib.py`，原名文献回读校验）退出码 1 表示「有对不上的条目，不是失败」，
Flet 单壳在日志区给末行契约（结果横幅可复制）；退出码 0 无提示。引用库页的查库 / 概况是**只读**，
入藏与待处理是真写（预览 → 二次确认 → `--expect-db-sha256` 闸 → 写后本地提交）；
查询错误直接显示在页面内（含引擎报错原文）。需要环境结论时去「设置 → 环境检查」；
「设置 → 诊断包」可导出或复制诊断包（已脱敏；导出需二次确认，写文件后打开目录）。
