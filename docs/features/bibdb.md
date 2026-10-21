---
title: 编辑部引用库
description: 把已核实的参考文献沉淀成编辑部资产，核实前先查库。
---

# 编辑部引用库

引用库（bib 库）把**已核实的参考文献**沉淀下来：以后核实新稿时先查库，命中就走现成版本，
不再重复花钱检索、也让体例更统一。

两个平台的界面都有同一套**五个页面**（库概况 / 查库 / 入藏 / 待裁决 / 引用库设置），
底层是同一份 `Engine/bibdb.py`（macOS 包与 Windows 包都随包交付）。
**批73（B-127）起 macOS 版「入藏 / 待裁决」增加写库裁决与粘贴入藏能力（mac 先行；
Windows 外壳本批未改，接入另卡）** —— 见下文「待裁决与裁决」「粘贴 bib 入藏」。

## 五个页面

| 页面 | 作用 |
|---|---|
| **库概况** | 条数 / 期数 / 最近更新 / 冲突数 / lint 健康 / 库路径；一键打开库文件夹 |
| **查库** | 按 key / DOI / 题名查询；显示是否已核实、来源期；一键复制 BibTeX |
| **入藏** | 选单篇 `.bib` / 整期目录 / **粘贴 bib 文本** → **预览（dry-run，不写库）** → 确认导入；来源期/篇自动推断且可改；粘贴时「入库口径」单选：**未核实（默认）** / 已人工核对 / 成品 bib（期+篇） |
| **待裁决** | **列出全部冲突条目**（按期→篇→C 编号，已裁决行灰显）；逐条「保留旧值 / 采用新值 / 待人工」写库；**一键全部采用**（dry-run 逐条预览 + 二次确认）；「编辑裁决 bib…」；「从备份恢复…」；冲突/重复候选清单仍可重新生成/打开。**DUPLICATES 仍只读**（不自动合并） |
| **引用库设置** | 核实上限、先算账、允许全量核实、先查库开关、库路径 |

## 入库口径

- 往期**已出版成品 bib** = 已核实，可入库；
- **AI 结果不直接入库**：AI 只给建议，需人工核对后才可作为已核实条目入藏；
- **粘贴 bib 文本**（mac 版，批73）默认按**未核实**入库；确已人工核对才显式选「已人工核对」
  （引擎 `--verified`）；选「成品 bib（期+篇）」并填来源期/篇 = 与单篇 `.bib` 现状同一口径
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
它们不含生成时间，同一份库重复生成逐字节相同，可直接用 git diff 判断「有没有新冲突」。

## 待裁决与裁决（mac 版，批73 B-127）

**列表**：一条冲突 = 一个固定的 **C 编号**（按全部冲突含已裁决分配，裁决后**不重排**）；
按「期 → 篇 → C 编号」分组，行内显示库内 key / 题名 / 作者、来源（期/篇/文件）、逐字段
「已核实旧值 → 新稿值」、建议、冲突指纹（`incoming_sha256` 短码）；库内已核实(verified)
记录会高亮提醒（采用新值 = 改已出版成品里人工核对过的字段）。已裁决行灰显并显示裁决动作。

**行内三按钮**（写库；写前校验库 sha + 自动备份）：

| 按钮 | 引擎动作 | 语义 |
|---|---|---|
| 保留旧值 | `resolve --action keep` | 保留库内已核实旧值，只写裁决元数据（不改字段） |
| 采用新值 | `resolve --action adopt` | **逐字段**采用库内留档的新稿值（只改该冲突字段）；重叠冲突默认拒绝 |
| 待人工 | `resolve --action defer` | 标为待人工查证，先从待裁决列表移走（不改字段） |

**一键全部采用**：按钮前置红字高亮警示（必须先看清风险）→ 引擎 `--adopt-all-pending --dry-run`
逐条预览（old→new + 备份路径，库不变）→ **二次确认**后才真写（单次原子写 + 单次备份）。
同记录待裁决冲突字段重叠时引擎拒绝并列出，需逐条处理；默认**不提供无确认执行**。

**编辑裁决 bib…**：预填库内条目（`query --key --bibtex`）→ TextEditor 编辑（可整条改写）→
`resolve --action replace --dry-run` 预览逐字段差异（含字段删除）→ 确认写库。
审计写进冲突元数据（`resolution=adopted` + `resolved_by=custom-bib` + `resolved_bib_sha256` + note）；
**不改 key、不改 verified/sources/method**；重叠冲突默认拒绝（可显式勾选 `--allow-overlap` 标 superseded）。

**从备份恢复…**：自动备份目录为 `~/Library/Application Support/SLCN/bibdb-backups/`
（不进 git；只有写库动作成功时才产生 `bibliography-<UTC时间>-<库sha12>.jsonl`）；
列出名称/时间/大小 → 校验/预览（dry-run：备份可解析 + 当前库 sha）→ 确认整库恢复。
恢复 = **整库回退**（含裁决状态），**不提供 reopen**；失败时库不变。

**安全闸**：所有写操作串行（运行中按钮置灰）；先预览后确认；写前 `--expect-db-sha256`
（列表过期 → 引擎拒绝写并提示刷新）；自动备份 + 原子写；失败显示引擎中文错误。
**默认零变化**：不点这些新按钮时，CLI 与旧入口的参数/行为与从前完全一致（新能力都是加法）。

## 命令行（开发用）

```bash
# 库概况（JSON，供界面/脚本用）
python3 Engine/bibdb.py stats --json

# 查一条
python3 Engine/bibdb.py query --key Fine1975 --bibtex --json

# 入藏预览（不写库）
python3 Engine/bibdb.py import-bib --bib /path/to/reference.bib --issue "2026年第2期(87)" --slug 02_Kun_Xing --dry-run --json

# 待裁决条目（JSON，界面列表的数据源；只读）
python3 Engine/bibdb.py conflicts --json --pending-only

# 逐条裁决（keep / defer / adopt；写前校验库 sha + 自动备份）
python3 Engine/bibdb.py resolve --key Fine1975 --action keep --incoming-sha256 <冲突指纹> \
  --expect-db-sha256 <库 sha> --backup ~/Library/Application\ Support/SLCN/bibdb-backups --json

# 一键全部采用（dry-run 先看逐条 old→new；去掉 --dry-run 才写）
python3 Engine/bibdb.py resolve --adopt-all-pending --dry-run --json

# 编辑裁决 bib（恰好 1 条、key 必须等于库内记录 key；dry-run 先预览 replaced_fields）
python3 Engine/bibdb.py resolve --key Fine1975 --action replace --incoming-sha256 <冲突指纹> \
  --bib /tmp/edited.bib --dry-run --json

# 重新生成待裁决清单
python3 Engine/bibdb.py report --out ../EditToolBox-notes/bib_db

# 从自动备份恢复整库（先 --dry-run 校验）
python3 Engine/bibdb.py restore --backup ~/Library/Application\ Support/SLCN/bibdb-backups/bibliography-*.jsonl --dry-run --json
```

> Windows 上把 `python3` 换成 `python`，示例路径 `/path/to/...` 换成本机路径（如 `C:\...\reference.bib`）。
> 打包版：Windows 用与界面 exe 同目录的 `SLCNEngine.exe bibdb.py …`；macOS 的引擎随 App 放在
> `Contents/Resources/Engine/`，用 `python3` 直接跑其中的 `bibdb.py` 即可。
> 批73 的 GUI 裁决/粘贴/恢复能力为 **mac 先行**；Win 外壳接入另卡（引擎命令两边同一份）。
