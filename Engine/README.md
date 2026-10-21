# SLCN 稿件转换器

把作者提交的 **LaTeX 源文件**或 **Word 文稿**，转换为符合《逻辑学研究》标准的**可编译初排稿**
（`.tex` + `.bib` + `.pdf`）。引擎是 Python（`convert.py` + `slcn/`），界面是 SwiftUI
（`sources-mac/`，15 个页面/四组侧边栏），构建时引擎被打包进 App 的 `Resources/Engine/`。
它**只服务「初排」这一个环节**：一校~四校、目录序号前缀（三校/蓝样后才由人工加）、蓝样都不归它管。

**本文件只是入口**：这是什么 → 红线 → 怎么跑 → 该读哪份文档 → 当前状态 → 验收链条 → 维护规则。
细节在 `docs/` 下**按读者分档**（案例法是只增不改的证据，不跟使用指南混放）。
**不要再往本文件里追加内容** —— 规则见文末「文档维护规则」。

**它在做什么（两条通道）**

| 通道 | 输入 | 做法 |
|---|---|---|
| A · LaTeX | 作者交的 `.tex`（常是「新正文 + 上一轮模板稿」两个文件） | **多源合并**：元数据取自旧模板稿、正文取自新稿、骨架用新版模板 |
| B · Word | 作者的 `.docx` | 直接读 Word XML 解析（OMML 公式 / 斜体 / 脚注 / 图片 / 表格），再按《排校手册》套规则 |

产物长这样（`--out` 下按作者姓名建目录，**初排阶段刻意不加数字前缀** —— 序号是三校/蓝样后才由人工加的）：

```text
Bo_Chen/
├── Bo_Chen.tex      ← 目标 tex
├── Bo_Chen.pdf      ← --compile 才有
├── reference.bib
├── SLCN-MIX.sty     ← 与 --sty 一致
└── original/        ← 源稿归档（只读副本）
    └── 陈波 当代逻辑哲学鸟瞰.docx
```

**仓库地图（目录级）**：`Engine/` 引擎（Python，唯一一份逻辑）｜`sources-mac/` 界面
（SwiftUI，15 个页面）｜`Engine/docs/` 文档｜`<期号>目录/` 真实稿件（**只读**）｜
`编辑笔记/` 模板与《排校手册》（规范的权威文本）。

---

## ⛔ 红线（两条，先记牢）

1. **原稿只读** —— 全程不修改任何作者来稿；`original/` 只是归档副本。产出写在 `out/`（已 gitignore）
   与 `--out` 指定的输出根目录。
2. **稿件仓不做任何 git 写操作** —— 作者来稿所在的 `Studies_in_Logic_Database` 里禁止
   `git add` / `git commit` / `git push`，以及任何改写历史或远端引用的操作；要动先征得用户同意。
   只读的 `git status` / `git log` / `git diff` / `git rev-parse` 允许用于自查。
   **本仓（工具箱）与笔记仓的口径不同**：会话可以自行提交推送，但要**攒批次**（一轮工作一条提交）、
   不要频繁推 —— 单点清单见仓库根 `AGENTS.md`「红线（硬约束）」3。

> 原文与完整措辞在 [`docs/GUIDE.md`](docs/GUIDE.md)「三、用法」末节（版本控制红线）；
> 提交节奏与稿件仓的例外见仓库根 `AGENTS.md` 红线 3。

---

## 怎么跑（最短路径）

```bash
cd Engine
PY=/opt/homebrew/bin/python3.14        # 跑普查/测试用的解释器（依赖在项目自己的 vendor/）

# Word 文稿 → 初排稿（陈波）
$PY convert.py \
    --source "/…/10 Bo_Chen/original/陈波 当代逻辑哲学鸟瞰.docx" \
    --out    "/…/2026年第5期(90)" \
    --compile

# LaTeX 多源合并（正文取自新稿 + 元数据取自上一轮模板稿）
$PY convert.py --source "/…/main.tex" --metadata "/…/yalcin puzzle.tex" \
               --out "/…/2026年第5期(90)" --compile

# 先看会发生什么，不落盘
$PY convert.py --source X.docx --out Y --dry-run

# 一键自查（第二十二批；--full 为发版前全套）
$PY tools/selfcheck.py
$PY tools/selfcheck.py --full
```

**第一次跑先装依赖**（三个包，装进项目自带的 `vendor/`）：

```bash
cd Engine
python3 -m pip install --target vendor -r requirements.txt
```

**可选外部依赖：LibreOffice（`soffice`）** —— Word 稿里的 MathType/OLE 公式预览图是 WMF/EMF，
XeLaTeX 不能直接嵌入，引擎用 `soffice --headless --convert-to pdf` 批量转 PDF 后再 `\includegraphics`。
**fail-soft**：未安装/找不到 `soffice`（`SLCN_SOFFICE` → PATH → 三平台常见安装位置）时**不报错、不阻断出稿**，
Word 通道把公式图渲染为 `\IfFileExists{figN.pdf}{…}{\fbox{\scriptsize 公式图缺失}}` 占位，
并在初排日志的「注意事项」里 warning（`N 个公式/图片未能导出（WMF/EMF 需要 LibreOffice soffice 转换…）`）。
LibreOffice 免费开源但**不随工具箱打包/分发**；检查/安装命令见 `../使用说明.md` §一
「Word 公式图（MathType）与 LibreOffice（可选依赖）」，平台/定位细节见 `docs/ENGINE_API.md` §七。
同理，「导出原稿 PDF」（矢量形状人工截图用）也依赖它，缺它时该步骤跳过并说明。

**源文件必须显式给（`--source` 必填）**，工具不做自动发现；`run_poc.py` / `corpus.py` 的自动发现
**只给回归用**。完整参数表、依赖、GUI 各页在干什么、报错对照，见
[`docs/GUIDE.md`](docs/GUIDE.md) 与 **[`使用说明.md`](../使用说明.md)**（后者是面向编辑的操作手册）。
GUI 一句话：`dist/编辑部工具箱.app` 可直接拷走运行，15 个页面按编辑流程分组排（单篇稿件 → 整期装配 → 引用库 → 设置）。

---

## 文档地图（想干什么 → 读哪个文件）

| 想干什么 | 读哪个文件 |
|---|---|
| 第一次接触这个项目（人或 AI） | `AGENTS.md`（仓库根：必读顺序 + 禁止事项 + 验收清单）→ 本文件 |
| **跑起来**：依赖、CLI 参数、GUI 各页、常见报错对照 | [`Engine/docs/GUIDE.md`](docs/GUIDE.md) |
| **查判据/口径**：三态、体例签名、bib 规范、任务书、占位符与覆盖键 | [`Engine/docs/RULES.md`](docs/RULES.md) |
| **写给外壳的契约**：CLI / 退出码 / 环境变量 / 产物路径 / 平台要求（mac 外壳与 Windows 外壳都按它调） | [`Engine/docs/ENGINE_API.md`](docs/ENGINE_API.md) |
| **一校核查规则表**：《排校手册》《流程化经验书》的规范 → 可执行规则（含**未实现清单与原因**） | [`Engine/docs/PROOF_RULES.md`](docs/PROOF_RULES.md) |
| 查某个 bug/规则的**根因 + 实测 + 验收**（第 1–30 条） | [`Engine/docs/cases/01-30.md`](docs/cases/01-30.md) |
| 同上（第 31–61 条；含 task-7 清账与并入记录） | [`Engine/docs/cases/31-61.md`](docs/cases/31-61.md) |
| 同上（**第 62 条及以后**：新发现一律追加在这里，一条一节、编号连续） | [`Engine/docs/cases/62+.md`](docs/cases/62+.md) |
| 接手干活：已完成到哪、下一步做什么、还有什么没做 | [`Engine/HANDOFF.md`](HANDOFF.md) |
| 最近几轮到底发生了什么（**只追加**的历史日志，§一–§十七+） | [`Engine/out/SESSION_HANDOFF.md`](out/SESSION_HANDOFF.md) |
| 编辑视角的操作手册（可以直接发给同事的那份） | [`使用说明.md`](../使用说明.md) |
| 当前状态的数字**怎么算出来的 / 怎么更新** | [`Engine/tools/gen_docs.py`](tools/gen_docs.py) |
| 文档自检：AUTO 块是否过期、引用路径是否存在、条目号是否可查、零丢失 | [`Engine/tests/test_docs_consistency.py`](tests/test_docs_consistency.py) |

---

## 当前状态（自动生成）

> 两个指标口径：**裸相似度** = 端到端字符级相似度（不做归一化）；**内容准确率** = 只惩罚实词增删改（L3）。
> 完整定义与 2026-10-13 的历史基线见 [`docs/RULES.md`](docs/RULES.md) §七。

> 下面这一块由 [`tools/gen_docs.py`](tools/gen_docs.py) **现算**并就地更新，
> **不要手改**（校验：`python3.14 tools/gen_docs.py --check`；改了代码忘了更新它，
> `tests/test_docs_consistency.py` 会红）。块内不含时间戳——否则 `--check` 永远失败；
> 新鲜度看块内各数据源的 sha256。

<!-- AUTO:STATE:BEGIN -->
**测试**（`python3.14` + `PYTHONPATH=Engine/vendor`）：**95 套件 / 3686 项断言 / FAIL 0 行** ✅（不含 `test_docs_consistency.py` 自身：它调用本脚本，属自指）

套件明细：`test_abstract_label` 45 ｜ `test_affil_from_bio` 33 ｜ `test_aibib` 68 ｜ `test_author_plausible` 33
`test_authornorm` 73 ｜ `test_bare_email` 30 ｜ `test_batch45_bib` 10 ｜ `test_batch46_bib` 10
`test_batch47b` 4 ｜ `test_batch48` 5 ｜ `test_batch49_cclass` 32 ｜ `test_batch49_cclass_render` 8
`test_batch49_no_cites` 10 ｜ `test_batch52_default_on` 16 ｜ `test_batch52_u1` 11 ｜ `test_batch54_coop` 100
`test_batch56_coop` 54 ｜ `test_batch57_coop_apply` 66 ｜ `test_batch58_bibdb_auto` 38 ｜ `test_batch58_bibdb_hits` 54
`test_batch59_coop_fulltext` 56 ｜ `test_batch60_llm_fulltext` 66 ｜ `test_batch61_bibdb_rules` 54 ｜ `test_batch63_api_char_cap` 14
`test_batch63_chat_no_limit` 17 ｜ `test_batch63_fulltext_apply` 36 ｜ `test_batch63_metadata_docx` 27 ｜ `test_batch63b_metadata_json` 70
`test_batch68_bibparse` 36 ｜ `test_batch68_p0fix` 16 ｜ `test_batch68_word_numbering` 24 ｜ `test_batch70_b001` 46
`test_batch_case_test` 0 ｜ `test_bib_key_determinism` 26 ｜ `test_bib_protection_forms` 45 ｜ `test_bibdb` 272
`test_bibdb_verify` 54 ｜ `test_bibnorm` 27 ｜ `test_bibreport_note_and_clause` 49 ｜ `test_checkbib` 105
`test_citation_year_fallback` 27 ｜ `test_cn_author_split` 28 ｜ `test_cn_dot_heading` 39 ｜ `test_cn_journal_numbered` 35
`test_coauthor_en` 19 ｜ `test_convert_compile_clean` 5 ｜ `test_convert_proof_autofix` 53 ｜ `test_docx_labels` 7
`test_docx_math_spacing` 45 ｜ `test_docx_quote_footnote` 13 ｜ `test_docxxml_deterministic` 14 ｜ `test_duplicates_merge_plan` 26
`test_en_abstract_sibling` 62 ｜ `test_en_slot_placeholder` 9 ｜ `test_english_manuscripts` 28 ｜ `test_evidence_injection` 27
`test_footnote_id0` 12 ｜ `test_incollection_fields` 36 ｜ `test_infocheck_toc` 63 ｜ `test_issue_tools` 69
`test_latex_appendix` 10 ｜ `test_latex_preamble` 117 ｜ `test_llm_error` 10 ｜ `test_llm_finish_reason` 65
`test_llm_knobs` 21 ｜ `test_llm_request_params` 12 ｜ `test_llm_responses` 62 ｜ `test_llm_thinking_and_reasoning` 56
`test_llm_usage_and_jsonmode` 61 ｜ `test_merge_into` 52 ｜ `test_merge_record_provenance` 51 ｜ `test_mock_keyed_responses` 18
`test_multisource_discovery` 22 ｜ `test_omml_stack` 17 ｜ `test_omml_structures` 22 ｜ `test_paths` 19
`test_pdfmerge` 36 ｜ `test_platform` 40 ｜ `test_proofcheck` 99 ｜ `test_ref_raw_lines` 34
`test_refmark_raw_refs` 29 ｜ `test_refs_attach` 29 ｜ `test_refs_batch45a2` 25 ｜ `test_refs_p0_2026_10_18` 21
`test_refs_p1_2026_10_18` 52 ｜ `test_run_record` 31 ｜ `test_search_refs` 54 ｜ `test_selfcheck` 25
`test_sep_style` 25 ｜ `test_table_images` 6 ｜ `test_tail_block` 45 ｜ `test_unmatched_in_report` 70
`test_verify_scope` 75 ｜ `test_vml_footnotes` 4 ｜ `test_xref_autoref` 14

**普查**（`out/docx_audit.csv`，132 份源稿；三态互斥、相加 = 篇数）：

| 字段 | `ok` | `fail` | `absent` |
|---|---|---|---|
| 题名 `title` | 131 | 0 | 1 |
| 副标题 `subtitle` | 18 | 1 | 113 |
| 作者 `author` | 73 | 3 | 56 |
| 单位 `affiliation` | 59 | 3 | 70 |
| 邮箱 `email` | 45 | 1 | 86 |
| 中文摘要 `abstract_cn` | 81 | 3 | 48 |
| 中文关键词 `keywords_cn` | 67 | 7 | 58 |
| 英文题名 `title_en` | 79 | 0 | 53 |
| 英文摘要 `abstract_en` | 75 | 0 | 57 |

- `n_headings` 合计 **587**；`n_bib` 合计 **2221**
- sha256[:12]：`docx_audit.csv` = `76ff3ba8b46f`，`docx_audit.txt` = `88c831752131`（与 `_ok` 列交叉核对，不一致 **0** 行 ✅）

**corpus 回归**（`out/corpus/corpus.csv`）：成功 **69 / 90** 篇；裸相似度中位 LaTeX **98.56%**、Word **89.23%**（第 125 条起去重确定性：同输入连跑两遍 `corpus.csv` 应逐字节一致，不一致即回归）

**占位符**（`out/corpus/**/*.tex` 里 `【未识别：…】`）：**248 处 / 55 篇**

**红线只读快照**（只跑 `git rev-parse` / `git status`，不写）：`.git/index` mtime `2026-10-21 12:46:05`；`HEAD` = `343726a`；`git status --porcelain` **6** 行
<!-- AUTO:STATE:END -->

---

## 固定验收链条（本轮/以后每次改完都要走完，缺一项不算完）

1. **普查逐行 diff** —— `python3 docx_audit.py` 全库复查，与上一版 `out/docx_audit.csv` **逐行**比对；
   **变坏迁移必须为 0**（哪些算变坏见 [`docs/RULES.md`](docs/RULES.md) §三）。
2. **反例测试** —— 新规则必须**同时**给出「不该命中的反例」，写进 `tests/test_*.py`；
   规则取舍先跑 `ablation_corpus.py` 消融，别凭直觉（这是本项目最重要的纪律）。
3. **代表稿** —— `convert.py` 跑三篇代表稿：`03 Liang_Zhang`（LaTeX 通道）、
   `10 Bo_Chen`（Word 通道）、`Lu_Jiang`（脚注体例 + 英文稿），人眼核版面与页数。
4. **`corpus.py` 回归** —— `$PY corpus.py`（默认 `--jobs auto` 并行；`--jobs 1` = 旧串行）；
   判据看**逐篇明细**而不是中位数。R5 的「`docxxml` 用 `id()` 去重、`05 Yang_Hu` ±0.03pp 抖动」
   已在**第 125 条**修掉（去重改用元素身份）：现在同输入连跑两次 `corpus.csv` 应**逐字节一致**，
   不一致即回归。
5. **重建 App + 包内 `cmp`** —— `cd EditToolBox && ./build.sh`，然后逐个 `cmp`
   包内 `Resources/Engine/` 的引擎文件与仓库同名文件（「包内引擎过期」是本项目踩过的坑）。
   ⚠️ `build.sh` 是**共享资源**：并行会话时必须**串行**，不要两边同时构建。
6. **文档一致性** —— `$PY tests/test_docs_consistency.py`（内含 `tools/gen_docs.py --check`，
   AUTO 块不许过期；引用路径必须存在；每处 `第 N 条` 必须可查；案例条目零丢失）。
7. **一键自查** —— `$PY tools/selfcheck.py`（quick）/`--full`：把 1/2/5/6 的离线部分 +
   代码健康（超长函数/死代码候选/TODO）+ 案例引用/案例库体检（`tools/case_audit.py`，
   需笔记仓）收进一条命令；输出 PASS/FAIL/WARN。

---

## 文档维护规则

1. **新发现写进 [`docs/cases/`](docs/cases/01-30.md)，不要往 README 里追加** ——
   第 1–30 条在 `01-30.md`、第 31–61 条在 `31-61.md`（这两个**初始文件一字不动**），
   **第 62 条及以后一律追加到 [`docs/cases/62+.md`](docs/cases/62+.md)**；
   每条 = 根因 + 实测 + 验收；条目号**只增不改**（`第 N 条` 是全局引用锚点，散落在
   README / AGENTS.md / HANDOFF.md / SESSION_HANDOFF.md 里，重编号会让所有引用失效）。
2. **数字一律由 [`tools/gen_docs.py`](tools/gen_docs.py) 注入**（本文件的 AUTO 块），**不要手抄** ——
   手抄的数字必然过期，而 `--check` 会当场抓住。
3. **新增/移动文件**后，回来更新上面的**文档地图**与 `AGENTS.md` 的必读顺序；
   `tests/test_docs_consistency.py` 会检查文档里出现的路径**真实存在**
   （**历史叙述豁免**：所在行 ±2 行含 `已删`/`删除`/`退役`/`历史`/`曾`/`当时` 等词，或含
   `*`/`…`/`{}` 的 glob 与省略写法；规则同时写在测试注释与 `AGENTS.md` 里）。
4. **口径/判据**变 → 改 [`docs/RULES.md`](docs/RULES.md) 并指向权威实现文件；
   **用法/依赖/GUI/限制**变 → 改 [`docs/GUIDE.md`](docs/GUIDE.md)；两处都不要复制代码里的规则全文。
5. **本文件保持 ≈200 行**：它是入口不是仓库。写长了就该把内容移进 `docs/` 并留一行指针。
