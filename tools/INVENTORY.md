# 工具清单（Tools Inventory；owner 制 + 生命周期；新增工具必须登记）

> 建立于批65（2026-10-20，治理小批 T4）；**批103（公开仓恢复批）同步；批104（B-157）登记 `inventory_check.py` 并实测校正计数**。范围：本仓 `tools/`（**28 项 = 26 个工具项 + 2 个本地生成物**；批78 新增 `batch_accept.py`/`ci_summary.py`/`regression_diff.py`/`tool_fixtures.py` 4 项；**批104 实测 `git ls-files tools` = 38 个 tracked 路径**（含 Swift 测试台/夹具等嵌套文件；本批新增工具 commit 前不入索引，提交后 = 39），其中 4 件一次性辅助已归档 `archive/`〔B-164；路径相对 `tools/`〕）+ `Engine/tools/`（6 项）。
> **数字口径（批104 起由 `tools/inventory_check.py --check` 机器校验，不再手抄）**：第 1 节 **28 行 = 26 个工具项（含 `archive/` 4 件）+ 2 个本地生成物**；「工具项 + 本清单」= **27 个 tracked 条目**（批103 口径为 26 = 25+1，本批 +1 为新工具）。`git ls-files` 原样计数含嵌套文件，只作实测输出、不作硬判据。
> **规则**：①每个工具必须有 owner、用途、最后提交、测试/验证方式；②状态 = **active**（在用）/ **dormant**（疑似休眠，5+ 批未用）/ **legacy**（废弃，移 `tools/legacy/`（需要时新建），**需 Lead 批准、可恢复、不物理删除**）/ **archived**（已移 `tools/archive/`，**可 `git mv` 恢复、不物理删除、不占 active 名额**）；③**新增工具不登记不得合入**（owner+测试必须写）；④每 5 批 prune 一次，批末监理抽检；⑤"最后提交"= `git log -1 --format=%cs -- <path>`（截至 2026-10-22，批104 复核）。
> owner 域：engine-core（引擎工具/`Engine/tools`）、qa-release（验收/CI 工具）、mac-shell（Swift 测试台/外壳工具）、rules-docs（模板/文档生成）、case-lab（案例工具）。批65 初版盘点由 Lead 代写，后续按域 owner 维护。

## 1. 本仓 `tools/`

| 项 | 用途 | owner | 最后提交 | 测试/验证 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| `ai_settings_test/`（`main.swift` **3517 行**，批103 实测 `wc -l`） | mac 设置/argv 契约测试台 | mac-shell | 2026-10-21 | `bash tools/run_swift_tests.sh`（CI `ci.yml`） | active | 体量大，考虑拆分/共享 helper（单独立项，不在治理批做） |
| `ai_store_test/` | mac AI 存储测试台 | mac-shell | 2026-10-16 | `run_swift_tests.sh`（CI） | active | — |
| `cutover_test/` | mac 切换测试台 | mac-shell | 2026-10-17 | `run_swift_tests.sh`（CI） | active | — |
| `infocheck_test/` | mac 信息核查测试台 | mac-shell | 2026-10-16 | `run_swift_tests.sh`（CI） | active | — |
| `proofcheck_test/` | mac 一校测试台 | mac-shell | 2026-10-17 | `run_swift_tests.sh`（CI） | active | — |
| `toc_test/` | mac 目录测试台 | mac-shell | 2026-10-16 | `run_swift_tests.sh`（CI） | active | — |
| `run_swift_tests.sh` | 7 台 Swift 测试台入口（headless） | qa-release | 2026-10-21 | CI `ci.yml` 步骤调用；期望「全部测试台 0 FAIL」 | active | 唯一入口，不手工逐台敲 |
| `bibdb_ux_test/`（`main.swift`） | 引用库 GUI 纯逻辑测试台（B-143：待裁决默认过滤/开关/计数/空态 + warning 明细分类/计数/跳转动作 + lint JSON 向后兼容解析） | mac-shell | 2026-10-21 | `run_swift_tests.sh`（CI，第 7 台） | active | 只编译 Foundation 的 `Core/Support.swift`+`Core/Log.swift`+`Features/BibDbFeature.swift`；headless、不起子进程、不读真实库 |
| `mock_llm_server.py` | 本机假 LLM 端点（零付费测试） | engine-core | 2026-10-17 | `Engine/tests/` 引用（test_aibib、test_batch60/63 等） | active | 开发期零付费的关键工具 |
| `case_audit.py` | 笔记仓案例库体检（编号/索引/状态/相似度，跨仓） | case-lab | 2026-10-18 | 自带 `--check`；`AGENTS.md`/`selfcheck.py` 引用 | active | 操作笔记仓 `cases/`；跨仓需路径说明 |
| `gen_case_index.py` | 生成/校验案例索引（跨仓） | case-lab | 2026-10-18 | CI `--check`；`selfcheck.py` 引用 | active | 操作笔记仓 `cases/` |
| `batch_case_test.py` | 期目录/子案例自动测试 + 5 期/全量基准 | case-lab | 2026-10-21 | 自有单测 `Engine/tests/test_batch_case_test.py`（118 项）；`--self-test`（50 项） | active | case-lab 的基准唯一入口；5 期复测命令见 `PRODUCT-QUALITY.md` §4（笔记仓）；**B-019 判据分级已落库（PAGE_DIFF→P1、88-06 案例级→P1，2026-10-21）**；**B-117（批71）起默认基线无关 L1–L4 口径（源稿覆盖 + L3 三分类归因；页数差/版式差降 P2），`--legacy-final` 复现旧口径** |
| `batch_accept.py`（**v1.2.0**；实测 1333 行） | 验收证据聚合（B-145 波1；批79 B-149 补 `cwd` 呈现；**批84 B-152 补 `kind=generic` 字段解析器 + 证据格式约定**）：读 `gate_plan.json` + 证据目录 → 校验**必跑**门禁的证据存在性与关键字段 → `accept.md`（≤40 行，可直接进 ACCEPT）+ `accept.json`；缺证据判 FAIL、解析不出去判 UNKNOWN，**绝不静默 PASS**；门禁 `cwd` 仅呈现（缺失→ `—` + WARN，**不改判据**）；**generic 门禁（`corpus_two`/`docx_audit`/`build`/`ci_watch`）不再恒 UNKNOWN**——按「通用键值行 `key: value` + 各门禁专用形态（shasum 行 / cmp / o5 / gh JSON）」解析，两遍 sha 不等、`diff_rows>0`、`docx fail>0`、`cmp≠28/28`、`o5≠1`、CI 非 success → FAIL | qa-release | 批84 | `python3 tools/batch_accept.py --self-test`（**19/19 PASS**，批103-A 实测；用例：缺证据/写后红/字段缺失反例 + rc 格式兼容 + cwd 呈现 + **generic 解析/两遍 sha 不一致 FAIL/阈值 FAIL/字段取不到仍 UNKNOWN/键值行形态/gen_docs 幂等写/无 docs run 不判 UNKNOWN**） | active | 调用路径：每批验收链 `gate_plan.py` → 跑门禁 → **本工具**聚合 → Lead 在 ACCEPT 确认跳过项。只读输入、只写 `--out`；不跑门禁、不联网。**证据格式约定见文件头 docstring §证据格式约定**（`EXIT=` 末行 / 键值行 / `AUTO测试行:`+`*-readme-diff*.log` / lint 日志内 `db_sha_before|after:` / 同批两轮用 `--map` 声明权威轮）。与笔记仓 `toolbox_audit.py` 分工：本工具聚合门禁证据，后者审台账/收口完整性，互不调用 |
| `ci_summary.py`（**v1.0.0**；实测 441 行） | CI 结果摘要（B-145 波1）：读 `gh run view --json name,status,conclusion,jobs` 落盘 JSON（+可选 job 日志）→ `ci_summary.md/json`（job 表 / 失败 step / 归因：测试红·依赖安装失败·文档 AUTO 过期·超时·取消·其它） | qa-release | 批78 | `python3 tools/ci_summary.py --self-test`（**10/10 PASS**，批103-A 实测；用例：绿 + 6 类归因 + 降级 + 确定性） | **dormant**（批91-A/B-171；写死再激活：本仓有 push 或 CI run/结论变化时必调一次，不占 active 名额） | 调用路径：push 后 `gh run view <id> --json …> run.json` → **本工具** → EVIDENCE「CI」行。**本工具不调用 `gh`、不联网**；字段缺失降级 UNKNOWN+WARN |
| `regression_diff.py`（**v1.0.0**；实测 547 行） | 基线 vs 本批回归差（B-145 波1）：两份 JSON（如 `batch-kpi.json` 两行）→ 字段级变化表 + 分类（improved/expected/regression/skip/unknown）+ 环比 pp（四舍五入到整百分点）；`--fail-on-regression` 时 exit 1 | qa-release | 批78 | `python3 tools/regression_diff.py --self-test`（**8/8 PASS**，批103-A 实测；用例：成本改善/回归/跳旗标/未测/容器选行/确定性） | active（批100 实跑恢复） | 调用路径：收口时 Lead 比本批 vs 上批 KPI 行 → EVIDENCE「vs 上批」行 + `baselines/batch-kpi.json` 校验。null 一律 skip（禁估算）；sha 变化/未分类字段判 unknown 交人工 |
| `tool_fixtures.py`（569 行） | hermetic 夹具（B-145 波1；**批84 B-152 补 generic 门禁证据：corpus 两遍 sha / docx 普查 / build cmp·o5 / CI `gh --json` 形态**）：在 `tempfile.TemporaryDirectory()` 里造假 gate plan / 假证据日志 / 假 CI run JSON / 假 KPI 行，供上述三件 `--self-test` 共用 | qa-release | 批84 | 由三件工具的 `--self-test` 间接验证（无独立 CLI；`--dump <dir>` 供抽查） | active | 不硬编码真实路径、无网络、不读真实库/会话；新增验收/CI 工具的自检请复用它 |
| `inventory_check.py`（**v1.0.0**；实测 1057 行） | INVENTORY 看门狗（B-157；批104 采纳外部试点能力、按本仓 DoD 重写，非整文件复制）：读本清单表 1/表 2 + 盘面（可选 `git ls-files`）→ 校验登记项存在性、未登记项（`archive/` 逐件）、行数注记漂移、状态与必填字段、私有仓路径、路径口径（表内相对 `tools/`）、表头计数；`--check` 人类可读（不一致 exit 1）、`--json` 机器可读（**退出码语义与 `--check` 分离**：分析完成即 0）；**只调 `git ls-files`，失败降级 WARN、不崩溃** | qa-release | 待首提（批104，未 commit） | `python3 tools/inventory_check.py --self-test`（**22/22 PASS**）；`python3 tools/inventory_check.py --check`（期望 exit 0） | active | 只读/离线/确定性/纯 stdlib/单文件；self-test 用 tempfile 假仓 + 注入 tracked provider（**不初始化 git、不启子进程**）；计数口径见文件头 docstring；不代替人工判 dormant/archived 取舍 |
| `gen_templates.py` | 从 Windows 原版程序提取 LaTeX 模板 → 生成 Swift 源 | rules-docs | 2026-10-16 | 生成物 `sources-mac/Features/ContentTemplates.swift` 在仓 | **dormant** | 5+ 批未用；仅换模板时用；保留不删 |
| `archive/sync_templates.sh` | 稿件仓《编辑笔记/模板文件》→ 本仓同步（发布带副本） | rules-docs | 2026-10-16 | 人工 `--apply` 预演；无自动测试 | **archived**（批103 B-164） | 自建成后无真实调用 → `git mv` 归档；恢复：`git mv tools/archive/sync_templates.sh tools/`；模板更新时才用 |
| `build_cli.sh` | 构建 CLI 测试工具（与 App 共用同一份功能代码） | engine-core | 2026-10-16 | `Engine/tests/test_infocheck_toc.py` 引用；产物 `toolbox-cli` | active | 与 `build.sh` 分工：只构建 CLI 回归工具 |
| `archive/dump_outline.swift` | PDF 页数/书签大纲诊断工具 | mac-shell | 2026-10-16 | 无自动测试（诊断用） | **archived**（批103 B-164） | 自建成后无真实调用 → `git mv` 归档；恢复：`git mv tools/archive/dump_outline.swift tools/` |
| `archive/make_icon.swift` | 生成 App 图标（CoreGraphics，一次性） | mac-shell | 2026-10-16 | 无自动测试（一次性图标生成） | **archived**（批103 B-164） | 一次性任务已完成 → `git mv` 归档；恢复：`git mv tools/archive/make_icon.swift tools/` |
| `cli/main.swift` | 命令行入口：复用 App 功能实现做回归（merge/deletetemp 等） | mac-shell | 2026-10-16 | 由 `build_cli.sh` 构建；`test_infocheck_toc.py` 引用 | active | 与 App 功能同源，防 GUI 回归盲区 |
| `archive/probe_rel/main.swift` | `#filePath`/CWD 诊断探针（一次性排障） | mac-shell | 2026-10-16 | 无自动测试 | **archived**（批103 B-164） | 自建成后无真实调用 → `git mv` 归档；恢复：`git mv tools/archive/probe_rel tools/`；排障时参考 |
| `fixtures/` | 测试夹具（`merge_replies.json` 等；见 `fixtures/README.md`） | engine-core/qa-release | 2026-10-16 | Engine tests 引用（test_aibib/test_batch63*/test_infocheck_toc） | active | 只留「测试真的会读」的夹具，历史夹具已删 |
| `log_cases.json` | 日志用例（GUIDE/两壳日志/桥接测试引用） | rules-docs | 2026-10-17 | `Engine/docs/GUIDE.md`、`sources-win/tests/test_bridge.py` 引用 | active | 改日志文案时同步 |
| `dump_outline`（本地二进制） | `archive/dump_outline.swift` 编译产物（Mach-O arm64） | mac-shell | 未跟踪（`.gitignore`） | 无 | legacy（本地） | **不入仓**；可删除后重编；不是源码 |
| `__pycache__/`（本地） | Python 字节码缓存 | — | 未跟踪（`.gitignore`） | 无 | 非工具 | 忽略，不计入清单 |

## 2. `Engine/tools/`（6 项）

| 项 | 用途 | owner | 最后提交 | 测试/验证 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| `gen_docs.py` | 文档 AUTO 块生成/校验（数字唯一出处） | rules-docs + qa-release | 2026-10-19 | CI `--check`（严格模式跑套件）；`selfcheck` 引用 | active | 与测试耦合；`--no-test-runs` 供测试内复用 |
| `selfcheck.py` | 引擎一键自查（环境/import/套件/文档看门狗/代码健康/案例/App cmp） | qa-release | 2026-10-19 | 自有单测 `Engine/tests/test_selfcheck.py` | active | `quick`/`--full` 两档 |
| `run_engine_tests.sh` | 引擎套件入口（quick/full） | qa-release | 2026-10-17 | CI `ci.yml` + release workflow 调用 | active | 唯一套件入口，不写行内循环 |
| `run_record.py` | 运行记录写 `runs/*.json` | qa-release | 2026-10-19 | 自有单测 `test_run_record.py`；`build.sh` 调用 | active | 证据/审计用 |
| `bibdb_seed.py` | 引用库种子数据 | bib-refs | 2026-10-18 | `Engine/docs/GUIDE.md` 流程引用；bibdb 初始化 | active | 改库结构时同步 |
| `gen_content_templates.py` | 内容模板生成（toctemplate/content_templates） | rules-docs | 2026-10-16 | 生成物在 `Engine/slcn/`（`content_templates.py`/`toctemplate.py`） | **dormant** | 5+ 批未用；模板更新时才用；保留不删 |

## 3. 休眠候选汇总（**保留不删除**；批65 已标 dormant，待 Lead/用户确认后维持或转 legacy）

`gen_templates.py`、`gen_content_templates.py`（仍 dormant，保留不删）。
> **批103 已处置（B-164）**：`sync_templates.sh`/`dump_outline.swift`/`make_icon.swift`/`probe_rel/` 4 件一次性辅助**自建成后无真实调用**（仓内 `grep` 仅命中自述注释）→ `git mv` 至 `tools/archive/` + 本表状态改 **archived**（表内路径相对 `tools/` 写作 `archive/…`）（可恢复、不物理删除）。
> 确认规则：连续 **10+ 批**未用且无测试/无引用 → 可移 `tools/legacy/` 或 `tools/archive/`（Lead 批准、git mv、可恢复）。

## 4. 待办（首次盘点后，进 BACKLOG）

- [x] 补齐每项「最后提交」与「测试/验证方式」（本表）
- [x] 休眠项：每 5 批 prune 时复核（进 `TECH-ADVISOR.md` §6 深审：批75）
- [x] **B-164（批103）**：4 件一次性 build 辅助 `git mv` → `tools/archive/`、本表状态 archived（判据：目录语义噪声降低；`grep` 零引用；可恢复）
- [ ] 跨仓工具（`case_audit.py`/`gen_case_index.py`）是否迁回笔记仓或加 `--notes-dir` 说明（P2，需单独立项）
- [ ] `ai_settings_test/main.swift`（3517 行，批104 实测 `wc -l`）是否抽公共 helper/拆分（P2，需单独立项，不在治理批做）
