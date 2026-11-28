# Publish.py Cleanup — 方案 A（全量移除 + 两壳同步 + 守卫反转）

**Goal:** 从工作树 `cleanup-publish-a-20261128`（基线 `13e8f10097637a2856b4ecf18d8ecb2f794b2402`）彻底删除 `Engine/publish.py` 这条 CLI 及其全部引用：删实现与专属测试、删四处入口白名单、同步两个打包壳、把 test_platform 的守卫从"Flet 壳不许绑 publish.py"反转为"全仓零 publish.py 字面残留"，并把注释/文档口径改为"toc.py --compile 是唯一编译入口、deliver() 是唯一交付实现"。交付时 `git diff` 只含预期文件、全量门禁绿、不 commit/push/tag。

**Architecture:** 单一工作树顺序执行（禁止并发写同一工作树）。TDD 顺序：Task 1 先改守卫测试锁口径（红）→ Task 2 删实现/测试/白名单使其转绿 → Task 3 文档与注释收口 → Task 4 全量门禁 + diff 审阅。lead（本代理）亲写计划、按任务派单、逐批验收；代码任务由 codex 子代理串行执行，文档任务由独立子代理执行；每批结束 lead 亲跑 `git diff --check` 与对应套件。

**Tech Stack:** Python 3.14 (`/opt/homebrew/bin/python3.14`)、bash runner、Flet 测试用 `python3 -m pytest`、ripgrep 全仓扫描。

**Spec:** 任务卡 B-397（m03714，用户方案 A）+ 本计划 Task 1–4。主仓 AGENTS.md 为只读权威；`Engine/docs/ENGINE_API.md`、`Engine/docs/RULES.md`、`README.md`、`Engine/tests/test_platform.py` 的 (g)/(c)/(o) 组已读。

## Global Constraints（硬，逐条 binding）

1. 工作目录仅 `/private/tmp/slcn-publish-cleanup-20261128`；分支 `cleanup-publish-a-20261128` 从基线 HEAD 起。
2. **主仓** `/Users/zhangwenming/Documents/GitHub/EditToolBoxStudiesInLogic` 只读：不写、不切分支、不 commit。
3. **稿件仓** `Studies_in_Logic_Database` 零访问；**笔记仓** `EditToolBox-notes` 不碰；其他候选工作树只读。
4. `git add` 只写显式路径，**禁 `-A` / `git add . / git add -u`**；**禁 commit / push / tag**。
5. 保密：回报只写期号/数字/退出码，不写真作者名、题名、稿件内容；计划文档不写真实稿件信息。
6. 解释器固定 `/opt/homebrew/bin/python3.14`；门禁环境 `PYTHONDONTWRITEBYTECODE=1 SLCN_MANUSCRIPTS=/dev/null PYTHONPATH="$PWD/vendor"`（在 `Engine/` 下跑）。
7. `deliver()` / `fit_single_page()` 函数体一字不动（B-395 回归风险）；B-395 的 `singlePageToc` 字段与 GUI 勾选、`Engine/refreshpages.py`、Flet assembly/合并页均不可碰。
8. README 的 AUTO 测试计数不许手改；确需刷新只用 `tools/gen_docs.py` 正规生成；文档"同一行含 已删/已移除/历史/曾"属历史叙述，路径不存在是正确的（主仓 AGENTS.md）。
9. 任何计划外文件、语义决策（尤其 B-395 共享文件、稿件仓、笔记仓）先报主 lead 再动。
10. 一文件一 owner，禁止两个代理同时写工作树；子代理必须 self-contained prompt；`subagent_codex` 的 activation 返回不等于真启动，必须用 `list_agents` 确认。

## Review Focus（lead 验收清单）

- 白名单四处漏一处（`engine_main.py` / `engine_bridge.py` / 两个 sidecar）→ 打包后入口不存在；逐处 diff 核对。
- 守卫反转漏改（ENTRY_FILES、(g)、(o)、test_b285、test_envcheck）→ 全量套件红；门禁逐套确认。
- `deliver()` 被误改 → B-395 回归；diff 中该函数体必须零变化。
- README AUTO 计数陈旧 → docs_consistency 红；只走 gen_docs 正规刷新。
- 稿件仓/主仓误写 → 红线事故；每批 diff --stat 只含预期路径。
- 全仓 `\bpublish\.py\b` 零残留（排除 vendor/out/.venv/__pycache__）；历史叙述 `changelog.md:146` 若保留字面则守卫扫描范围需明确排除或改写——采用改写为不含该字面，使"零残留"断言成立（需主 lead 确认的非功能决策点，见 Task 3）。

---

## Task 1：守卫反转（TDD 先锁口径）

**Files:** `Engine/tests/test_platform.py`（ENTRY_FILES :111、(g) :375-377/:413、(o) :1397-1444）、`Engine/tests/test_b285_contract.py`（:184/:186-188）、`Engine/tests/test_envcheck.py`（:525-532）。

**Interfaces:** `ENTRY_FILES` 派生 `CLIS`；(g) 比对 `engine_main.ENTRIES` 与 sidecar `$Entries`；(o) 新语义=扫 `Engine/**` + `sources-flet/**` 的 `.py/.ps1/.md`（排除 vendor/out/.venv/__pycache__）断言 `\bpublish\.py\b` 零命中，o2 防僵化=扫描范围非空 + `Engine/slcn` 非空（不再要求 bridge 含 publish.py）。test_b285 的 expected 从 13 项变 12 项并重述文案；test_envcheck 的 `ENTRIES[:10]`→`[:9]`、`ENTRIES[10:]`→`[9:]` 并重述 C8 口径。

**Steps:**
- [ ] 改 test_platform.py：ENTRY_FILES 删 `"publish.py"`；(g) 注释对齐新口径；(o) 整组按上述新语义重写（保留 (o) 组编号与打印结构）。
- [ ] 改 test_b285_contract.py：expected 删 publish.py、文案 13→12。
- [ ] 改 test_envcheck.py：CLI 清单断言重排切片与文案。
- [ ] 跑 `PYTHONDONTWRITEBYTECODE=1 SLCN_MANUSCRIPTS=/dev/null PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_platform.py` → 预期**红**（新守卫发现当前代码仍有 publish.py 残留），记录失败数为 TDD 前置证据。
- [ ] lead 验 `git diff --check` → 0，并确认 diff 只含三个测试文件。

## Task 2：删实现 + 白名单 + 两壳同步

**Files:** 删除 `Engine/publish.py`、`Engine/tests/test_publish.py`；改 `Engine/tests/test_issue_tools.py`（删除 (e) 组 :263-305 的 6 处 `run("publish.py", ...)`，补回被覆盖的断言口径）；改 `Engine/engine_main.py:49`、`sources-flet/app/services/engine_bridge.py:86`（保留文件）、`Engine/tools/build_win_sidecar.ps1:168`、`sources-flet/packaging/build_win_sidecar_onedir.ps1:101`（四处白名单删 `"publish.py"`）。

**Interfaces:** 四处 ENTRIES 顺序一致（守卫 (g) 逐项比对）；test_issue_tools (e) 组删除后其余组断言口径不被弱化。

**Steps:**
- [ ] 删除 `Engine/publish.py` 与 `Engine/tests/test_publish.py`（`rm` 显式路径，禁 `-A`）。
- [ ] test_issue_tools.py：整块删除 (e) 组 publish.py 相关测试，检查该组附近是否有被其覆盖的通用断言需要补回（lead 复核 diff）。
- [ ] 四处白名单逐一删 `"publish.py"`，保留其余条目顺序不变。
- [ ] 跑 Task 1 的三个测试文件 + `test_issue_tools.py` → 全绿；`git diff --check` → 0。
- [ ] lead 验 `git diff --stat` 只含预期路径；确认未碰 B-395 共享文件。

## Task 3：注释与文档收口

**Files:** `Engine/slcn/tocdelivery.py`（:6/:94/:659/:673）、`Engine/toc.py:26`、`Engine/compileall.py:76`、`Engine/slcn/diagnose.py`（:231/:236/:241/:245/:250/:276/:282）、`Engine/docs/ENGINE_API.md`（:564/:572/:579/:671 + 开头两处入口数表述）、`Engine/docs/RULES.md:1132`、`README.md`（:82 入口清单、:113 仓库结构图裸列 publish）、`docs/reference/changelog.md:146`（历史叙述中的 publish.py 字面，改写为不含该字面以使全仓零残留成立）。

**Interfaces:** 口径="toc.py --compile 是唯一编译入口、deliver() 是唯一交付实现"；`deliver()` / `fit_single_page()` 函数体零变化。

**Steps:**
- [ ] 改 toc.py / compileall.py / diagnose.py / tocdelivery.py 的注释与 docstring，只改文字不动逻辑；tocdelivery.py 尤其只动注释行。
- [ ] 改 ENGINE_API.md：删 publish.py 命令行条目与契约行，toc.py 行改"deliver() 是唯一实现"；开头"十个 CLI"/"十三个入口"数字口径按实际 ENTRIES 数对齐。
- [ ] 改 RULES.md:1132 两入口改单入口；改 README.md:82/:113 去掉 publish 字面（不手改 AUTO 块）。
- [ ] 改 changelog.md:146 历史叙述为不含 publish.py 字面的等价表述（如"出刊五步向导保留为 CLI 专用"改为不含具体文件名），或若主 lead 决定保留历史字面则在 Task 1 的 (o) 扫描中显式排除 changelog.md 并在回报中标注例外——**默认按前者**。
- [ ] 跑 `rg -n '\bpublish\.py\b' --glob '!vendor/**' --glob '!out/**' --glob '!**/.venv/**' --glob '!**/__pycache__/**'` → 预期零命中。
- [ ] lead 验 `git diff --check` → 0；diff 中 `deliver()` / `fit_single_page()` 函数体零变化；changelog 改动为唯一语义决策点，回报主 lead。

## Task 4：全量门禁 + diff 审阅（lead 亲跑）

**Files:** 无新增改动（发现问题时回退对应 owner 修）。

**Steps:**
- [ ] `git -C <wt> diff --check` → 0。
- [ ] 引擎全量（后台 job 收集，别前台超时）：`cd Engine && PYTHONDONTWRITEBYTECODE=1 SLCN_MANUSCRIPTS=/dev/null PYTHONPATH="$PWD/vendor" SLCN_TEST_PYTHON=/opt/homebrew/bin/python3.14 bash tools/run_engine_tests.sh full` → 退出码 0（红 0）；单独确认 `test_platform.py` / `test_b285_contract.py` / `test_envcheck.py` / `test_issue_tools.py` 全绿（test_platform 末尾"失败 0 项（全部通过）"）。
- [ ] Flet：`python3 -m pytest sources-flet/tests -q` 用真实仓 Engine/vendor 路径 → 0 failed（报告 pass/skip 数）。
- [ ] 文档一致性：`python3 tests/test_docs_consistency.py`（含 `--strict-tests` 口径）→ 失败 0；若 AUTO 测试计数陈旧，先用 `tools/gen_docs.py` 正规刷新（解释器 /opt/homebrew/bin/python3.14）再重跑，禁手改生成物。
- [ ] 全仓 `rg '\bpublish\.py\b'`（排除 vendor/out/.venv/__pycache__）→ 零命中。
- [ ] lead 逐文件审阅 `git diff --stat` 与全文 diff，核对 Review Focus 每条；确认无 commit/push/tag、未碰主仓/稿件仓/笔记仓/其他候选树。
- [ ] 回报主 lead：①计划文档绝对路径 ②diff --stat 全文+逐文件一句话摘要 ③各门禁退出码/pass-fail 计数 ④未决风险与决策点 ⑤红线确认。