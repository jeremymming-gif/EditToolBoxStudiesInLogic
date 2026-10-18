---
title: 编辑部引用库
description: 把已核实的参考文献沉淀成编辑部资产，核实前先查库。
---

# 编辑部引用库

引用库（bib 库）把**已核实的参考文献**沉淀下来：以后核实新稿时先查库，命中就走现成版本，
不再重复花钱检索、也让体例更统一。

两个平台的界面都有同一套**五个页面**（库概况 / 查库 / 入藏 / 待裁决 / 引用库设置），
底层是同一份 `Engine/bibdb.py`（macOS 包与 Windows 包都随包交付）。

## 五个页面

| 页面 | 作用 |
|---|---|
| **库概况** | 条数 / 期数 / 最近更新 / 冲突数 / lint 健康 / 库路径；一键打开库文件夹 |
| **查库** | 按 key / DOI / 题名查询；显示是否已核实、来源期；一键复制 BibTeX |
| **入藏** | 选单篇 `.bib` 或整期目录 → **预览（dry-run，不写库）** → 确认导入；来源期/篇自动推断且可改 |
| **待裁决** | 只读清单：冲突与重复候选（人类可读），支持重新生成/打开清单；**不自动改库** |
| **引用库设置** | 核实上限、先算账、允许全量核实、先查库开关、库路径 |

## 入库口径

- 往期**已出版成品 bib** = 已核实，可入库；
- **AI 结果不直接入库**：AI 只给建议，需人工核对后才可作为已核实条目入藏；
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

## 命令行（开发用）

```bash
# 库概况（JSON，供界面/脚本用）
python3 Engine/bibdb.py stats --json

# 查一条
python3 Engine/bibdb.py query --key Fine1975 --bibtex --json

# 入藏预览（不写库）
python3 Engine/bibdb.py import-bib --bib /path/to/reference.bib --issue "2026年第2期(87)" --slug 02_Kun_Xing --dry-run --json

# 重新生成待裁决清单
python3 Engine/bibdb.py report --out ../EditToolBox-notes/bib_db
```

> Windows 上把 `python3` 换成 `python`，示例路径 `/path/to/...` 换成本机路径（如 `C:\...\reference.bib`）。
> 打包版：Windows 用与界面 exe 同目录的 `SLCNEngine.exe bibdb.py …`；macOS 的引擎随 App 放在
> `Contents/Resources/Engine/`，用 `python3` 直接跑其中的 `bibdb.py` 即可。
