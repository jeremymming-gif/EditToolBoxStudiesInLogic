# 测试夹具（`tools/fixtures/`）

> ⚠️ 这个目录 2026-10-15（phase 2c）从 `tools/gui_fixtures/` **改名**过来。
> 改名时删掉了「人工点击时代」的**文献回读校验**夹具（`original.bib` / `clean.bib` /
> `returned_mutated.bib` / `manuscript.tex` / `clean_case/` 与那份实跑报告）——
> 它们当年只为「在「文献回读校验」页里手工点一遍」准备，逐文件 `grep` 证明
> **没有任何代码或测试引用**（回读校验的自动覆盖在 `Engine/tests/test_checkbib.py`、
> 并回在 `Engine/tests/test_merge_into.py`，各自现场造夹具，不读这里）。
> 只留下**测试真的会读**的那一份。

## 目录里现在只有一样东西

| 文件 | 谁在读 | 读它做什么 |
|---|---|---|
| `merge_replies.json` | `Engine/tests/test_aibib.py` ⑤ 组（`FAKE = "fixtures/merge_replies.json"`） | 本地 mock 的 **replay 夹具**：按请求里的 bib key 回一段 canned 文本，用来钉死「AI 逐条回话后，工具**并哪几条、跳过哪几条、为什么**」 |

`mock_llm_server.py` 是**人**跑验收时才用它（`--replies tools/fixtures/merge_replies.json`）；
自动测试（`test_aibib.py`）在自己进程里起同一个 mock，不经过命令行。

## `merge_replies.json` 是什么

配 `mock_llm_server.py` 的 **replay 模式**（第 64 / 65 条）用的：键就是真实稿
`2026年第5期(90)/01 Meimei_Han` 的 `reference.bib` 里的 bib key，值是「AI 会怎么回」。
七条覆盖五种情形（**都不是真实核实结果**，是造出来给验收用的）：

| 键 | 回复是什么 | 应当发生什么 |
|---|---|---|
| `Burge1984` | 完整条目，字段顺序与原件略不同 | **并入**（字段值逐字段一致，只是文本重排） |
| `Kvanvig1995` | 带 `% ⚠ 待核实：number` 注释（**围栏内、条目上方**）、且**没有** `number` | **并入**，注释跟着条目进 bib，缺字段记 **warning** |
| `Chalmers2011` | 键被改成 `Chalmers2011a` | **跳过**（改名会让 `\parencite` 悬空）——正文一个字不动 |
| `Dummett1991` | 一句「查不到」的抱歉，没有 bibtex | **跳过**（回复里没有可用条目） |
| `Kaplan1989` | `author = {David Kaplan}`（名 姓）、编者也没倒装、字段顺序乱 | **并入**，作者/编者由**引擎**倒装成 `Kaplan, D.` / `Almog, J. and …`，字段按第三节固定顺序重排，改动记进合并记录 |
| `A2014` | 中文译著 `阿斯海姆（著），郑建国、万林（译）` | **并入**，中文逗号/顿号**原样保留**（不许拆成 `and`） |
| `Dang2022` | `@article` 缺 `volume`，两行说明（`% 类型已改` + `% ⚠ 待核实：volume`）写在**围栏外面、条目后面**（2026-10-15 用户实测的真实形状） | **并入**，两行注释被**搬到条目上方**跟着进 bib → 回读校验把缺的 `volume` 降为**提示**（不是问题），类型改动也被认作「已注明」 |

夹具里**没有**的键（该稿另外 13 条）由 mock 回一句「夹具未覆盖」——引擎把它记成
「回复里没有可用条目」，**绝不编假条目**（那会污染 `reference.bib`）。

## 怎么复现（那条人工验收）

```bash
# 1) 起假端点（只绑 127.0.0.1、只回放夹具里写好的那几条）
/opt/homebrew/bin/python3.14 tools/mock_llm_server.py \
    --port 8899 --replies tools/fixtures/merge_replies.json

# 2) 真实 CLI 跑一篇真实的稿子（产物写 /tmp，别落到仓库里）
cd Engine && PY=/opt/homebrew/bin/python3.14
PYTHONPATH="$PWD/vendor" SLCN_LLM_THINKING=0 $PY convert.py \
    --source "../../2026年第5期(90)/01 Meimei_Han/定稿.docx" \
    --out /tmp/p2a_e2e --verify-refs \
    --llm-base http://127.0.0.1:8899/v1 --llm-key mock --llm-model mock-model
```

期望产物（`/tmp/p2a_e2e/Meimei_Han/`）：`reference.bib`（20 条，其中 5 条被并入）、
**`reference.bib.ai前`**（= 未并入时的 `reference.bib`，逐字节相同）、
`Meimei_Han_回读校验报告.md`（结论一行会打进控制台）、
`Meimei_Han_文献核实.md` 末尾的 `## 六、合并记录`。

> ⚠️ 这里的条目内容是造的，不是核实结果；它只证明「工具的并入/跳过判据按预期工作」。
> 自动验收跑的是 `Engine/tests/test_aibib.py` 的 ⑤ 组（它自己在本进程里起同一个 mock）。

## 没被这些夹具证明的事

夹具里的字符串**不是**真实核实结果，也**不**代表官方来源；「卷/期/页码对不对」只能由编辑
按《文献核实任务书》第二节核实（第 64 条的红线：工具只保证结构不被破坏）。
