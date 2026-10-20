# 工具清单（Tools Inventory；owner 制 + 生命周期；新增工具必须登记）

> 建立于批65（2026-10-20，治理小批 T4）。范围：本仓 `tools/`（22 项 = 20 个 tracked 项 + 2 个本地生成物）+ `Engine/tools/`（6 项）。
> **规则**：①每个工具必须有 owner、用途、最后提交、测试/验证方式；②状态 = **active**（在用）/ **dormant**（疑似休眠，5+ 批未用）/ **legacy**（废弃，移 `tools/legacy/`（需要时新建），**需 Lead 批准、可恢复、不物理删除**）；③**新增工具不登记不得合入**（owner+测试必须写）；④每 5 批 prune 一次，批末监理抽检；⑤"最后提交"= `git log -1 --format=%cs -- <path>`（截至 2026-10-20）。
> owner 域：engine-core（引擎工具/`Engine/tools`）、qa-release（验收/CI 工具）、mac-shell（Swift 测试台/外壳工具）、rules-docs（模板/文档生成）、case-lab（案例工具）。批65 初版盘点由 Lead 代写，后续按域 owner 维护。

## 1. 本仓 `tools/`

| 项 | 用途 | owner | 最后提交 | 测试/验证 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| `ai_settings_test/`（`main.swift` 2938 行） | mac 设置/argv 契约测试台 | mac-shell | 2026-10-20 | `bash tools/run_swift_tests.sh`（CI `ci.yml`） | active | 体量大，考虑拆分/共享 helper（单独立项，不在治理批做） |
| `ai_store_test/` | mac AI 存储测试台 | mac-shell | 2026-10-16 | `run_swift_tests.sh`（CI） | active | — |
| `cutover_test/` | mac 切换测试台 | mac-shell | 2026-10-17 | `run_swift_tests.sh`（CI） | active | — |
| `infocheck_test/` | mac 信息核查测试台 | mac-shell | 2026-10-16 | `run_swift_tests.sh`（CI） | active | — |
| `proofcheck_test/` | mac 一校测试台 | mac-shell | 2026-10-17 | `run_swift_tests.sh`（CI） | active | — |
| `toc_test/` | mac 目录测试台 | mac-shell | 2026-10-16 | `run_swift_tests.sh`（CI） | active | — |
| `run_swift_tests.sh` | 6 台 Swift 测试台入口（headless） | qa-release | 2026-10-20 | CI `ci.yml` 步骤调用；期望「全部测试台 0 FAIL」 | active | 唯一入口，不手工逐台敲 |
| `mock_llm_server.py` | 本机假 LLM 端点（零付费测试） | engine-core | 2026-10-17 | `Engine/tests/` 引用（test_aibib、test_batch60/63 等） | active | 开发期零付费的关键工具 |
| `case_audit.py` | 笔记仓案例库体检（编号/索引/状态/相似度，跨仓） | case-lab | 2026-10-18 | 自带 `--check`；`AGENTS.md`/`selfcheck.py` 引用 | active | 操作笔记仓 `cases/`；跨仓需路径说明 |
| `gen_case_index.py` | 生成/校验案例索引（跨仓） | case-lab | 2026-10-18 | CI `--check`；`selfcheck.py` 引用 | active | 操作笔记仓 `cases/` |
| `batch_case_test.py` | 期目录/子案例自动测试 + 5 期/全量基准 | case-lab | 2026-10-19 | 自有单测 `Engine/tests/test_batch_case_test.py` | active | case-lab 的基准唯一入口；5 期复测命令见 `PRODUCT-QUALITY.md` §4（笔记仓） |
| `gen_templates.py` | 从 Windows 原版程序提取 LaTeX 模板 → 生成 Swift 源 | rules-docs | 2026-10-16 | 生成物 `sources-mac/Features/ContentTemplates.swift` 在仓 | **dormant** | 5+ 批未用；仅换模板时用；保留不删 |
| `sync_templates.sh` | 稿件仓《编辑笔记/模板文件》→ 本仓同步（发布带副本） | rules-docs | 2026-10-16 | 人工 `--apply` 预演；无自动测试 | **dormant** | 5+ 批未用；模板更新时才用；保留不删 |
| `build_cli.sh` | 构建 CLI 测试工具（与 App 共用同一份功能代码） | engine-core | 2026-10-16 | `Engine/tests/test_infocheck_toc.py` 引用；产物 `toolbox-cli` | active | 与 `build.sh` 分工：只构建 CLI 回归工具 |
| `dump_outline.swift` | PDF 页数/书签大纲诊断工具 | mac-shell | 2026-10-16 | 无自动测试（诊断用） | **dormant** | 5+ 批未用；保留不删 |
| `make_icon.swift` | 生成 App 图标（CoreGraphics，一次性） | mac-shell | 2026-10-16 | 无自动测试（一次性图标生成） | **dormant** | 5+ 批未用；保留不删 |
| `cli/main.swift` | 命令行入口：复用 App 功能实现做回归（merge/deletetemp 等） | mac-shell | 2026-10-16 | 由 `build_cli.sh` 构建；`test_infocheck_toc.py` 引用 | active | 与 App 功能同源，防 GUI 回归盲区 |
| `probe_rel/main.swift` | `#filePath`/CWD 诊断探针（一次性排障） | mac-shell | 2026-10-16 | 无自动测试 | **dormant** | 5+ 批未用；排障时参考，保留不删 |
| `fixtures/` | 测试夹具（`merge_replies.json` 等；见 `fixtures/README.md`） | engine-core/qa-release | 2026-10-16 | Engine tests 引用（test_aibib/test_batch63*/test_infocheck_toc） | active | 只留「测试真的会读」的夹具，历史夹具已删 |
| `log_cases.json` | 日志用例（GUIDE/两壳日志/桥接测试引用） | rules-docs | 2026-10-17 | `Engine/docs/GUIDE.md`、`sources-win/tests/test_bridge.py` 引用 | active | 改日志文案时同步 |
| `dump_outline`（本地二进制） | `dump_outline.swift` 编译产物（Mach-O arm64） | mac-shell | 未跟踪（`.gitignore`） | 无 | legacy（本地） | **不入仓**；可删除后重编；不是源码 |
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

`gen_templates.py`、`sync_templates.sh`、`gen_content_templates.py`、`dump_outline.swift`、`make_icon.swift`、`probe_rel/`（本地 `dump_outline` 二进制不入仓）。
> 确认规则：连续 **10+ 批**未用且无测试/无引用 → 可移 `tools/legacy/`（Lead 批准、git mv、可恢复）；**本批不移动、不删除**。

## 4. 待办（首次盘点后，进 BACKLOG）

- [x] 补齐每项「最后提交」与「测试/验证方式」（本表）
- [ ] 休眠项：每 5 批 prune 时复核（进 `TECH-ADVISOR.md` §6 深审：批75）
- [ ] 跨仓工具（`case_audit.py`/`gen_case_index.py`）是否迁回笔记仓或加 `--notes-dir` 说明（P2，需单独立项）
- [ ] `ai_settings_test/main.swift`（2938 行）是否抽公共 helper/拆分（P2，需单独立项，不在治理批做）
