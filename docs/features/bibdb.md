---
title: 编辑部引用库
description: 把已核实的参考文献沉淀成编辑部资产，核实前先查库。
---

# 编辑部引用库

引用库（bib 库）把**已核实的参考文献**沉淀下来：以后核实新稿时先查库，命中就走现成版本，
不重复花钱检索、也让体例更统一。

两个平台的界面都有同一套**六个页面**（库概况 / 查库 / 入藏 / 待处理·待裁决 / 参考文献规范化 / 引用库设置），
底层是同一份 `Engine/bibdb.py`（macOS 包与 Windows 包都随包交付）。macOS 版的「待处理」页
功能完整（字段冲突 + 库健康 + 已处理，见下）；Windows 版「待裁决」页已对齐其中的字段冲突
部分（逐条 keep/adopt/defer、编辑并替换、一键全部采用；写库成功后自动本地 commit 库文件）。

## 六个页面

| 页面 | 作用 |
|---|---|
| **库概况** | 条数 / 期数 / 最近更新 / **字段冲突（待裁决 / 历史已裁决）** / lint 健康（**橙色 warning 可「查看明细」按类下钻**：每条给 key/原因/建议操作；**author/editor 未倒装（含机构名）只报告、不自动改**）/ 库路径；一键打开库文件夹 |
| **查库** | 按 key / DOI / 题名查询；显示是否已核实、来源期；一键复制 BibTeX |
| **入藏** | 选单篇 `.bib` / 整期目录 / **粘贴 bib 文本** → **预览（dry-run，不写库）** → 确认导入；来源期/篇自动推断且可改；粘贴时「入库口径」单选：**未核实（默认）** / 已人工核对 / 成品 bib（期+篇） |
| **待处理**（macOS；Windows 版叫「待裁决」，只含字段冲突部分） | 字段冲突 + 库健康 + 已处理三段（详见下文「待处理中心」） |
| **参考文献规范化**（原「文献回读校验」） | 离线体检外部改回的整份 .bib（规范/引用闭合/逐字段 diff）、手动并回产物、入藏引用库；不生成任务书 |
| **引用库设置** | 核实上限、先算账、允许全量核实、先查库开关、库路径 |

> **「待裁决」与「库概况」橙字是两个数据源**：「待裁决」页读引擎 `conflicts`
> （字段冲突裁决；默认只列待裁决，已裁决可开关查看、灰显、编号不重排）；「库概况」页橙色文字读引擎
> `lint`（库健康检查：重复记录 / 同一作品多记录 / 字段冲突 / 缺字段 / entry_type 笔误）。点
> 「查看 warning 明细」可按类下钻：每条给 key 例子、原因与建议操作；**重复记录合并只报告、不自动合并**；
> 字段冲突类显示「待裁决 N / 历史已裁决 M」，并可一键跳到「待处理」页。

## 待处理中心（macOS）

macOS 版在保留逐条裁决能力的基础上，把「库健康」与「已处理」并入同一页，并提供三种写库入口
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
已处理行默认一行摘要、点开明细；长列表走 `LazyVStack`；「显示已处理」是本页本地开关，不影响其它页。

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

## 待裁决与裁决（macOS / Windows）

> Windows 版「待裁决」页同样支持本节的三按钮、编辑替换、一键全部采用，以及写库成功后的
> 自动本地 commit（只提交库文件、绝不推送；git 不可用只黄字提示）。Windows 侧不含「推送 / 回退 /
> 提交…」按钮，库健康与已处理两段仍只在 macOS。

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
状态区显示当前分支 / 库文件脏否 / 未推送提交数 / 最近 3 条库提交，并提供三个都要二次确认的按钮：
「提交…」（手动补提交）、「推送…」（**唯一**推送入口，失败显示 git stderr 摘要，不做 force）、
「回退到上一个提交…」（`git checkout HEAD~1 -- bib_db/bibliography.jsonl` + 新 commit，
**不 hard reset、不推送**）。git 不可用/非 repo/身份未配置 → **黄字 fail-soft**：写库照旧成功、
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

# 重新生成待裁决清单
python3 Engine/bibdb.py report --out ../EditToolBox-notes/bib_db

# 整库回退（GUI 用「待处理」页版本区「回退到上一个提交…」，等价于）：
git -C ../EditToolBox-notes checkout HEAD~1 -- bib_db/bibliography.jsonl
git -C ../EditToolBox-notes commit -m "引用库：回退到上一个提交" -- bib_db/bibliography.jsonl
# 兼容：引擎 CLI 仍保留 `restore --backup PATH`（GUI 不调用）
```

> Windows 上把 `python3` 换成 `python`，示例路径 `/path/to/...` 换成本机路径（如 `C:\...\reference.bib`）。
> 打包版：Windows 用与界面 exe 同目录的 `SLCNEngine.exe bibdb.py …`；macOS 的引擎随 App 放在
> `Contents/Resources/Engine/`，用 `python3` 直接跑其中的 `bibdb.py` 即可。

## 失败诊断（参考文献规范化 / 引用库）

参考文献规范化（`checkbib.py`，原名文献回读校验）退出码 1 表示「有对不上的条目，不是失败」，出**中性卡**；
退出码 0 不出卡。卡片给出类别、原因与下一步，证据为脱敏后的日志尾部；
诊断包只存本地、复制到剪贴板，不会上传引用库或稿件内容。
