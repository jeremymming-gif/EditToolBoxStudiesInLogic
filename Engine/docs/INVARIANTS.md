# 不变量（INVARIANTS）

**这份文件是什么**：把「必须一直成立、任何改动都不许打破」的约束收成**单点清单**。
每条 ≤3 行，并给出**可核实的位置**（文件 / 参数 / 测试组），改完代码可逐条自查。

**不回答什么**：怎么用 → `Engine/docs/GUIDE.md`；某条判据为什么这么定 → `Engine/docs/RULES.md`；
给外壳的接口细节 → `Engine/docs/ENGINE_API.md`；改代码的流程与验收清单 → 仓库根 `AGENTS.md`。
本文件与别处冲突时，以本文件指向的**代码与测试**为准（它们是机器可复核的）。

---

## 一、红线（破了就不能发布）

1. **作者来稿只读**：`20xx年第N期(…)` 及 `original/` 里的文件不许改 / 删 / 移；试新规则一律写到 `Engine/out/` 或 `/tmp/`。
   位置：仓库根 `AGENTS.md`「红线（硬约束）」1；期号目录的识别正则在 `Engine/slcn/paths.py`（`ISSUE_RE`）。
2. **冻结基线不许就地覆盖**：全库普查结果是冻结基线（现钉在笔记仓 `baselines/docx_audit.csv`，sha256 见 `Engine/README.md` 的 AUTO 块）。
   改解析器时用 `python3 docx_audit.py --out-dir /tmp/audit_<标记>`（或环境变量 `SLCN_AUDIT_OUT`）隔离，再与基线逐行 diff，**变坏迁移必须为 0**。
   位置：`Engine/docx_audit.py` 的 `--out-dir`；`Engine/docs/ENGINE_API.md` 六（开发用入口）。
3. **密钥不进 argv**：API 密钥只走环境变量 `SLCN_LLM_KEY`，绝不拼进命令行，也不写进日志（界面只显示前 4 位）。
   位置：`Engine/slcn/llm.py`（`--key` 的帮助文本明写「别写进命令行」）；`Engine/docs/ENGINE_API.md` 一、八.4。
4. **公开仓不放作者姓名、稿件片段、内部手册**（案例法在笔记仓）；`编辑笔记/` 下只公开 `编辑笔记/模板文件/`。
   位置：仓库根 `AGENTS.md`「红线（硬约束）」4；`Engine/tests/test_docs_consistency.py` 的 (b) 组对这两类引用单独统计、不判失败。
5. **提交与推送分仓对待**（2026-10-17 用户拍板）：工具箱仓与笔记仓可以由会话自行提交，但要**攒批次**
   （一轮工作一条提交）、不要频繁推；**稿件仓任何改动先征得用户同意**才提交 / 推送。
   位置：仓库根 `AGENTS.md`「红线（硬约束）」3；`RELEASING.md` 抬头；`Engine/docs/GUIDE.md`「三、用法」末节。

## 二、架构：文档逻辑只有一份

1. **所有文档逻辑在 `Engine/`（Python），只有一份**；外壳只做「取值 → 拼参数 → 显示日志」，外壳里不许再写文档规则。
   位置：仓库根 `AGENTS.md`「一个引擎，两个外壳」；`Engine/docs/ENGINE_API.md` 八（外壳的义务 1）。
2. **mac 侧证据**：`sources-mac/Features/MergeFeature.swift` 自述「识别逻辑**不在 Swift 里**」——
   合并顺序与附加页识别只剩 `Engine/slcn/pdfmerge.py` 一份（同名 Swift 实现已删）。
3. **Windows 侧证据**：`sources-win/engine.py` 是外壳与引擎之间的**唯一**桥梁（不 import tkinter，可离窗口测试），
   入口名单 `ENTRIES` 与 `Engine/docs/ENGINE_API.md` 一致。
4. **界面要的数据一律问引擎**（`--preview` / `--detect`），界面不自己解析报告；否则会出现「预览说 A、运行时是 B」。
   位置：`Engine/docs/ENGINE_API.md` 五「给外壳的注意」。

## 三、产物契约（给外壳的接口，语义不许改）

1. **退出码**：`0` = 成功；`2` = 用法 / IO / 目录错误（消息在 stderr）。`Engine/convert.py` **没有 1**——中途某一步失败也算 2。
   位置：`Engine/docs/ENGINE_API.md` 二；`Engine/tests/test_issue_tools.py` 断言「目录不存在 → 2」。
2. **`1` 常常是「有结果」而不是失败**：`checkbib` 并回 = 已并入 ≥1 条、`compileall` = 有失败、`proofcheck` = 有命中、`toc` = 只生成一份；
   外壳不许把它显示成错误。位置：`Engine/docs/ENGINE_API.md` 三、五；`Engine/tests/test_proofcheck.py`。
3. **每个入口的「最后一行」是契约**（外壳靠它拿产物路径与计数），格式不许改；新信息一律**新增行**，不改既有行的语义。
   位置：`Engine/docs/ENGINE_API.md` 二、四、五、十.2；涉及 `Engine/convert.py`、`Engine/checkbib.py`、`Engine/mergepdf.py`、
   `Engine/cleantemp.py`、`Engine/compileall.py`、`Engine/proofcheck.py`、`Engine/infocheck.py`、`Engine/toc.py`。
4. **这些契约行有测试钉着**：`Engine/tests/test_pdfmerge.py` 的 (e)(f) 校验「最后一行 = 契约行 + 页数」；
   `Engine/tests/test_issue_tools.py` 校验 `cleantemp --dry-run` 的契约行落在最后一行、`compileall --only` 空集退出 0。
5. **产物布局与文件名是契约的一部分**（`<out>/<作者名>/…`、`reference.bib.ai前`、`_文献核实.md`、`_回读校验报告.md`），不许改。
   位置：`Engine/docs/ENGINE_API.md` 二「产物」、十.3。

## 四、判据与体例的权威位置

1. **代码说了算**：体例签名、bib 规范、作者倒装、引用点匹配等判据的权威实现都在 `Engine/slcn/`；
   `Engine/docs/RULES.md` **只做索引与摘编**（其「权威实现清单」逐项指向代码），两者冲突先跑测试复核。
2. **一校核查**：规则表 = `Engine/docs/PROOF_RULES.md`（手册规范 → 可执行规则 + 未实现清单），权威实现 = `Engine/slcn/proofcheck.py`。
3. **文档分工不许串**：口径 / 判据 → `Engine/docs/RULES.md`；用法 / 依赖 / 界面 / 限制 → `Engine/docs/GUIDE.md`；
   给外壳的接口 → `Engine/docs/ENGINE_API.md`；手册规范 → `Engine/docs/PROOF_RULES.md`。位置：仓库根 `AGENTS.md`「文档维护」4。
4. **会变的数字不许手抄**：测试项数 / 普查计数 / sha256 一律由 `Engine/tools/gen_docs.py` 现算注入 `Engine/README.md` 的 AUTO 块。
   位置：`Engine/tools/gen_docs.py --check`（由 `Engine/tests/test_docs_consistency.py` 的 (a) 组调用）。

## 五、三仓分工与环境变量

1. **公开仓（本仓）= 开发主场**，只放代码与可公开文档；`EditToolBox-notes`（私有）= 案例法 / 交接说明 / 冻结基线；
   `Studies_in_Logic_Database`（私有）= 作者来稿与编辑部规范。位置：仓库根 `AGENTS.md` 抬头。
2. **稿件库锚点只有一处**：`Engine/slcn/paths.py`。顺序 = 环境变量 `SLCN_MANUSCRIPTS` → 向上找 → 引擎仓的同级目录；
   **显式配置优先且严格**（设了指错就返回 None，不回退）；判据是「真的有 `20xx年第N期(…)` 期号目录」，不是「目录存在」。
3. **没有稿件库时必须优雅跳过（退出码 0）**，不许报错——公开仓与 CI 本来就没有稿件库。
   位置：`Engine/slcn/paths.py` 的 `require()`；`Engine/tests/test_paths.py` 的 (c)(d)。
4. **笔记仓同理**：环境变量 `SLCN_NOTES`，判据是「里面有 `cases/` 目录」；找不到同样优雅跳过。位置：`Engine/slcn/paths.py` 的 `notes_root()`。

## 六、改动纪律（兼容性、版本、守卫）

1. **只增不改**：新增 CLI 参数必须带默认值（老外壳不传也能跑），不改既有参数的默认行为与默认路径。
   位置：`Engine/docs/ENGINE_API.md` 十.1；先例 `build_docx(metadata_path=None)`。
2. **版本号只有一处**：`Engine/slcn/__init__.py` 的 `__version__`；仓库根的 `build.sh` 与 Windows 打包脚本**现取**它，不许写死。
3. **平台中立**：交付模块里文本模式 `open()` 必须显式写 `encoding=`（Windows 读中文源稿乱码的根因）；
   静态守卫在 `Engine/tests/test_platform.py` 的 (d) 组。
4. **改完要跑的守卫**：`Engine/tests/test_docs_consistency.py`（AUTO 块不过期 / 引用路径存在 / 条目号可查 / 案例零丢失）、
   `Engine/tests/test_platform.py`；重活还要走 `Engine/README.md` 的「固定验收链条」。本文件自己的路径引用也在 (b) 组检查范围内。
5. **LaTeX 前言依赖迁移只增不改**（第 120 条）：只搬模板没有的宏包 / tikz 库 / 定义；模板管理的包
   （ctex / fontspec / geometry / hyperref / biblatex / 本刊 sty）不搬；Word 通道产物必须零变化。
   位置：`Engine/slcn/preamble.py`；`Engine/tests/test_latex_preamble.py`。
6. **英文稿判据只作用于全英文稿**（第 121 条）：英文脚注降级 / 英文全名参考文献 / 无样式小标题
   三条判据都以「正文无汉字」为前提，或以「不含中文」为逐条条件——中文稿（含中英混排）产物
   必须逐字节不变（用例一 10 篇回归 10/10 相同）。位置：`Engine/slcn/docxsource.py`
   （`doc_is_en` / `_entry_is_note_only` / `_looks_short_bibliographic_note`）；
   `Engine/tests/test_english_manuscripts.py`。
7. **解析去重不许用 `id()`/`hash()` 记住 lxml 元素**（第 125 条）：lxml 的元素代理是临时对象，
   被回收后同一 XML 节点会换新代理、`id()` 漂移且会被其它节点复用 → 产物随机丢/重公式（R5）。
   要记住元素就存**元素本身**（集合持有引用 = 身份稳定）；且去重只覆盖「真正处理过的节点」
   （`_para_segments` 只跳过 `_run_segments` 收了直接子 `<m:oMath>` 的那些）。
   位置：`Engine/slcn/docxxml.py` 的 `_run_segments` / `_para_segments`；
   `Engine/tests/test_docxxml_deterministic.py`（AST 静态守卫 + 合成夹具 + 真实稿抽查）。

## 七、这份清单自己怎么维护

1. **只增不改**：条目要删改，先说明理由；「曾经怎样」写进案例法（笔记仓 `cases/62+.md`），不在这里留历史叙述。
2. **新增条目**必须给出可核实的位置（文件 / 参数 / 测试组），并在本仓跑 `Engine/tests/test_docs_consistency.py` 确认「失败 0 项」。
