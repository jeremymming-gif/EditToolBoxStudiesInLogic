# 规范与判据汇编（RULES）

> **这份文件回答什么**：编辑部/本工具的**判据**分别是什么、**哪份代码说了算**、对应哪条案例。
> 案例条目（`docs/cases/`）记的是「为什么这么定」的**证据与实测数字**；本文件记的是**当前有效的口径**，
> 并用「权威实现」一列指向真正说了算的代码——**规范以代码为准，本文只做索引与摘编**。
> 两边冲突时：先跑 `tests/*.py` 与 `out/docx_audit.csv` 复核，再改代码，最后回来改这里。
>
> **纪律**：① 本文件里凡是标「照抄」的段落都是从案例条目**逐字**摘来的（含数字），改了就是改了历史，
> 要改请在原条目下追加新条目；② 不复制整份规则——`slcn/bibnorm.py` / `slcn/refmark.py` 才是权威，
> 这里只写「看哪一行、依据是哪条案例」；③ 所有**会变的数字**（测试项数、普查计数、相似度、占位符处数）
> 都不许手抄，由 `tools/gen_docs.py` 注入到 `README.md` 的 AUTO 块，见 README「当前状态」。

## 一、权威实现清单（先看这张表）

| 规范领域 | 权威实现（代码说了算） | 人读的规范文本 | 案例条目 |
|---|---|---|---|
| 字段三态 `ok`/`fail`/`absent`、体例签名 | `docx_audit.py`（`evidence()` / `entry_style()` / `classify_bib()` / `head_kinds()`） | 本文 §二、§三；`out/docx_audit.txt` 文末「判据」节 | 第 44/48/53/56/60 条 |
| 解析失败不再静默：占位符 + 覆盖键 | `slcn/build.py`（`is_parsed()` / `PLACEHOLDER_TMPL` / `REQUIRED_FIELDS` / `OVERRIDE_KEYS`） | 本文 §六 | 第 46 条（GUI 入口见第 47 条） |
| 编辑部 bib 规范（写进任务书、也是给 AI 的 system prompt） | `slcn/refmark.py`（`SPEC_MD` / `SOURCES_MD` / `FORMAT_MD`；⑪ 逐条请求 = `entry_prompt()`） | 本文 §五 | 第 41/61/68 条 |
| **条目 → 源稿原文**的映射（任务书「源稿原文」行与 AI 预跑都用它） | `slcn/docxsource.py`（`DocxDoc.ref_raw_by_key` / `_numbered_refs`）、`slcn/footref.py`（`split_numbered_raw`）、`slcn/build.py`（`BuildResult.ref_raw_lines` / `ref_raw_by_key`）、`slcn/refmark.py`（`_norm_raw_line` / `_entry_block`） | 本文 §五 | 第 68 条 |
| bib 归一化 / `userf` / `\bibitem` 转换 | `slcn/bibnorm.py`（`detect_userf()` / `normalize_bib()` / `_clean_field_value()` / `DROP_FIELDS`） | 本文 §四.1 | 第 20/32 条 |
| **bib 整行注释不算条目**（第 133 条） | `slcn/bibnorm.py`（`strip_bib_comments()` / `split_entries()`） | 本文 §四.8 | 第 133 条 |
| **参考文献附件的选择/校验/合并**（第 134 条） | `convert.py`（`real_bib_path()` / `bib_attachment_entries()` / `merge_bib_attachment()` / `bib_entry_structurally_safe()`）、`slcn/multisource.py`（`select_refs_attachment()` / `text_attachment_issue()` / `bib_attachment_issue()` / `looks_like_bibtex()`） | 本文 §四.9 | 第 134 条 |
| **AI 结果并入 `reference.bib`**（2026-10-15） | `slcn/aibib.py`（`merge()` / 注释归位 / 字段顺序重排 / `render_merge_section()`；手动通道 = `merge_returned_bib()` / `apply_merge_to_file()` / `write_merge_record()`）+ `convert.py`（`apply_ai_merge()`，回滚副本 `reference.bib.ai前`）+ `checkbib.py --merge-into` | 本文 §五 | 第 64/65 条 |
| **英文作者一律倒装**（2026-10-15） | `slcn/bibnorm.py`（`normalize_author_field()` / `author_is_inverted()`）+ `slcn/bibcheck.py`（`author_form_issues()` / `translation_marking_issues()` / `author_has_role_mark()`）+ `tests/test_authornorm.py` | 本文 §四.7 | 第 63/64 条 |
| 字段完整性 + 类型统一 + 引用双向核对 | `slcn/bibcheck.py`（`REQUIRED` / `TYPE_ALIASES` / `check()` / `check_citations()`） | 本文 §四.2–4 | 第 21/22/25/26 条 |
| **正文「（作者 年份）」→ `\parencite{key}`**（人年制通道的引用点匹配） | `slcn/docxsource.py`（`link_citations()` / `_year_fallback_evidence()`；索引 = `RefEntry.surname` × 年） | 本文 §六「引用点未匹配」 | 第 73 条 |
| **英文稿脚注/参考文献/小标题判据**（第 121 条） | `slcn/docxsource.py`（`_entry_is_note_only()` / `_looks_short_bibliographic_note()` / `_parse_reference_line_en_fullname()` / `classify_en_heading()` / `_looks_plain_en_title()`；只对全英文稿生效，中文稿逐字节不变） | 本文 §一；`docs/GUIDE.md` §3.11 | 第 121 条 |
| **LaTeX 正文图片复制 / 无花括号新命令 / `\bibliographystyle` 截断**（第 121 条） | `slcn/latex.py`（`copy_referenced_graphics()` / `find_all_macros()`）、`slcn/sources.py`、`slcn/build.py` | `docs/GUIDE.md` §3.1 | 第 121 条 |
| **英文省略号后紧跟字母要隔离**（第 121 条） | `slcn/rules.py::en-ellipsis-letter`（只在后接字母时写 `\ldots{}`，其余逐字节不变） | `docs/GUIDE.md` §3.1 | 第 121 条 |
| **LaTeX 自定义定义的位置**（第 130 条，本地 sty/cls/def/cfg、`\newcounter`、本地包行、正文区、缺失预检） | `slcn/preamble.py`（`local_support_files()` / `scan_support_definitions()` / `missing_environments()` / `plan_migration(source_dir=)`）、`slcn/build.py`（②′ 与 ⑤） | 本文 §八；`docs/GUIDE.md` §3.1 | 第 130 条 |
| **参考文献键的生成与稳定性**（钉死「不可复现」） | `slcn/docxsource.py`（`make_key()` / `_make_key_core()` / `_dedupe_keys()` / `_strip_zero_width()`；兜底键用 `zlib.crc32`，**不许**用内置 `hash()`） | 本文 §四.2 末「键的稳定性」 | 第 74 条 |
| **第一作者单位**（脚注「作者简介」句里也抓） | `slcn/docxsource.py`（`_clean_affil_line()` = 旧口径 `_clean_affil_line_strict()` 优先，空了才走 `_clean_affil_by_sentence()`） | `docs/GUIDE.md`「GUI 各页速查」 | 第 75 条 |
| **Word 通道的英文元数据第二源** | `slcn/sources.py`（`en_metadata_sheet()`）+ `slcn/build.py`（`build_docx(metadata_path=…)` / `_merge_en_metadata_source()`）+ `convert.py`（`find_en_metadata()` / `mark_en_abstract_roles()`） | `docs/GUIDE.md` §3.8 | 第 76 条 |
| 编辑交回的 `.bib` 回读校验（离线） | `checkbib.py`（薄壳：退出码 0/1/2；`--merge-into` 时 0/1 = 未改动/已并入）+ `slcn/bibparse.py`（唯一一份健壮解析器）+ `slcn/bibreport.py`（报告）+ `tests/test_checkbib.py`、`tests/test_merge_into.py` | 本文 §四.6 | 第 55/57/64 条 |
| 引用/脚注排版规范（《排校手册》） | `编辑笔记/排版手册/排校手册.pdf`、`编辑笔记/排版手册/排版手册源码/edit_handbook.tex`；宏在 `编辑笔记/模板文件/SLCN-MIX.sty` | 本文 §四.5 | 第 33/34 条 |
| 模板选择与 `\usepackage` 同步 | `slcn/templates.py`（`resolve()` / `sync_package_line()` / `_find_repo_dir()`） | `docs/GUIDE.md`「二、模板选择」 | — |
| 输出目录命名 / 覆盖判定 / 清理 | `slcn/naming.py`、`convert.py: is_own_product()`、`slcn/clean.py` | `docs/GUIDE.md`「3.1 / 3.2」 | — |

---

## 二、体例签名表（普查怎么给一篇稿子「定体例」）

**判据一律是启发式**，且刻意保守 —— 「宁可少扣作者帽子，也不把责任推给作者」。
权威实现在 `docx_audit.py`，完整判据文本由它写进 `out/docx_audit.txt` 的「判据」一节。
（照抄 `docx_audit.py` docstring ③）

> 体例判定一律是**启发式**：先按条目/段落签名分类，再按篇取 ≥60% 多数票，
> 拿不到多数票的归 `其他*`，不硬猜。

| 大类 | 签名（决定性的形态） | 篇数（132 份源稿口径） | 权威函数 |
|---|---|---|---|
| 节标题 · `N 空格` | `1 引言`、`2 否定` | 41 篇（按篇取最高层级） | `docx_audit.py: head_kinds()` + `primary_heading_style()` |
| 节标题 · `N紧跟汉字` | `1可知性悖论` | 2 篇 | 同上 |
| 节标题 · `一、` | `一、引言` | 28 篇 | 同上 |
| 节标题 · `N. 空格` | `1. Introduction` | 12 篇（英文稿主力） | 同上 |
| 节标题 · `N.M` | `2.1 ...` | 2 篇 | 同上 |
| 节标题 · `无标题` | 一篇都没有编号标题 | 47 篇 | 同上 |
| 文献 · `脚注体例` | 文末**没有**条目清单，条目不来自清单而来自脚注 | 14 篇 | `docx_audit.py: classify_bib()` |
| 文献 · `编号+引号题名` | `[N] 作者, 年, "题名", …`（期刊条目加引号） | 32 篇 | 同上（单条签名 `entry_style()`） |
| 文献 · `编号+西文人年(无引号)` | 同一份 `[N] …` 清单，题名不加引号 | 7 篇 | 同上 |
| 文献 · `GB/T7714标记` | `作者. 题名[M]. 出版地: 出版社, 年.` | 6 篇 | 同上 |
| 文献 · `人年制无编号` | `A. Capone, 2019, 题名, 出版地: 出版社` | 37 篇 | 同上 |
| 文献 · `其他:N.式编号` | `N.` 式编号清单（全库 1 篇） | 1 篇 | 同上 |
| 文献 · `其他:混排` | 篇内条目体例拿不到 60% 多数票 | —（见长尾清单⑥） | 同上 |
| 文献 · `无参考文献` | 文末无清单、也无脚注式文献 | 35 篇 | 同上 |

**粗族**（决定「要不要写兼容」看这张）：编号式人年清单 39 篇 / 人年制无编号 37 篇 /
无参考文献 35 篇 / 脚注体例 14 篇 / GB/T 7714 6 篇 / 其他 1 篇。
b 与 d **不是两种排版体例**，而是同一份清单的两种题名写法，同一篇里并存是常态（照抄第 44 条注）。

**普查的两条口径**（看数字前先看它，权威在 `docx_audit.py` docstring ①②）：

1. **范围**：只扫 `<期号>/<稿件>/*.docx` 与 `<期号>/<稿件>/original/**/*.docx`，
   跳过 `._*` / `~$*` / `.*` / `__MACOSX`；两处之外的 65 份 docx **不计入分母**，
   只在报告附录⑧给聚合结果（免得把「没扫到」误读成「不存在」）。
2. **分母按文件计**（132 份，不按稿件去重）；级别 ≥60% 多数票，否则 `其他*`。

---

## 三、字段三态口径：`ok` / `fail` / `absent`

（照抄第 44 条；权威实现：`docx_audit.py: evidence()` → `audit_one()`，列写进 `out/docx_audit.csv`）

| 状态 | 含义 | 该怎么办 |
|---|---|---|
| `ok` | 解析拿到了 | — |
| `fail` | 原文有证据、解析没拿到 | **解析失败，值得写兼容** |
| `absent` | 原文也没有证据 | **本来就没有，不必写兼容** |

三种状态**互斥、相加 = 篇数**。证据判据保守：`email` / 摘要 / 关键词的判据很硬（裸邮箱、标签），
`author` / 单位 / `title_en` 是形态判据，`fail` 里可能混少量误报 → 报告逐条给出文件名便于抽查。

**CSV 的 9 个三态列**（照抄第 48 条；权威实现：`docx_audit.py` 的 `CSV_COLUMNS` + `write_csv()`）：

```text
# 明细：每篇一行，26 列 = 前 17 列（原有，位置不动）+ 末尾 9 个三态列
# 三态列 ∈ {ok, fail, absent}：`fail` = 原稿有、解析器没拿到；`absent` = 原稿本来就没有。
```

- 列名 `<field>_state`（`title_state` … `abstract_en_state`），值 ∈ `{ok, fail, absent}`，**与 `<field>_ok` 同源**（`ok` ⇔ `1`）；
- **追加在末尾**，前 17 列位置不动 → 按列号取值的老消费者不会错位；
- 9 个字段：题名 / 副标题 / 作者 / 单位 / 邮箱 / 中文摘要 / 中文关键词 / 英文题名 / 英文摘要；
- 计数：字面写「只有 `_ok` 列时 `fail` 与 `absent` 都是 0」这种指引落不了地，才补的三态列。

> ⚠️ **判据会骗人，交叉验证**：第 53 条发现 `w:footnoteReference w:id="0"` 被误丢，
> 导致 3 行本该 `fail` 的被记成 `absent` —— 所以**三态一旦修了，必须重生成普查并做逐行 diff**，
> 确认「变坏迁移 = 0」（`ok→fail` / `fail→absent` / `ok→absent` 合计为 0）。

---

## 四、bib 规范

### 1. `userf = {cn}`：中文条目必须填

- **判定规则**（照抄第 20 条；权威实现 `slcn/bibnorm.py: detect_userf()`）：
  **含假名 → `jp`；含汉字 → `cn`；纯英文 → 不填。**
  ⚠️ **只按「作者 + 题名」判断，不要把 `note`/出版信息算进来** —— 英文条目里常夹着中文译者信息，
  会让纯英文条目被误判成 `cn`（实测 `Haack1978` / `Kripke2024` / `Maddy2002` 就这么错判过）。
- 为什么：`编辑笔记/模板文件/SLCN-MIX.sty` 的 `\cegen` 按 `userf` 决定输出中文标点（、。）还是英文（, .）；
  不填就按英文排。
- 全库验证：以人工已标注的 1642 条为基准，判定一致 1640 条 = **99.9%**；
  `normalize_bib()` 会自动给漏填的中文条目补上（全库 82 条）。
- 键生成：中文作者用 `pypinyin` → `ChenBo2005` 这种形态；**键只需内部自洽**（第 3 条）。

### 2. 字段完整性 + 类型统一

**核实范围 = 所有字段（2026-10-15，第 69 条）**

（权威实现 `slcn/refmark.py: SPEC_MD` 第一节第 2 条第一项 + `SOURCES_MD` + `FORMAT_MD` 第 11 条；
**规范正文不在这里复制**，这里只记口径与理由）

- **要核的字段**：author / title / **类型** / journal 或 booktitle / volume / number /
  pages / publisher / address / edition / year —— **一个都不许跳过**。
  为什么写死范围：编辑与 AI 都习惯只看显眼的卷/期，而 `address`（全库 `@book` 出现率仅 61%）、
  `edition`、**类型**恰恰最容易错；类型判错还会把整条的必备字段表整表换掉（本文件上表）。
- **铁律不变**：**只写来源确认过的值**；来源里查不到的字段**照旧省略**，并在条目上方写
  `% ⚠ 待核实：<字段>（原因）`——「编错的卷号比空缺更糟」。核实 ≠ 补全。
- **来源标注（新的可审计线索）**：每条**核实过的**条目上方写一行
  `% 来源：<官方页面 URL 或来源说明>`（与「待核实」同一落点：bibtex 代码块**内部**、
  紧挨条目的上一行）。**拿不到 URL 就写来源说明，绝不许编一个像 URL 的字符串**；
  只有真核过的条目才写——给没核过的条目写 `% 来源：` 等于给编辑一个假的信任信号。
  合并时这一行**随条目一起保留**（`slcn/aibib.py: _entry_block()` 收「紧邻条目的整行 `%` 注释」
  并归位到条目上方；回归见 `tests/test_llm_responses.py` 的 (f) 组）。
- **检索状态的可追溯性（第 69 条）**：一次运行到底「联网核实过」还是「模型凭记忆写的」，
  由三处**同一句话**写明（`convert.py: channel_provenance()` 是唯一出处）：
  ① 控制台 `本次核实通道：…`；② 任务书头部的同一行；③ 任务书「**六、合并记录**」里那条
  `- **本次核实通道：…**`。**只写通道不够**：开了检索但收到 **0 个 `web_search_call`** 时，
  那句话必须写成「**检索证据 0 个 → 本次结果按凭记忆对待**」——开关状态**不许**冒充已联网
  （如 DeepSeek 的 `deepseek-flash` 仍把 `web_search` 列为「忽略」的内置工具，不报错也不检索；
  `deepseek-v4-pro` 则会真检索）。
- **请求形状可配，判据不可配（第 70 条）**：responses 通道的工具类型名与路径改成配置
  （`SLCN_LLM_SEARCH_TOOL` / `SLCN_LLM_RESPONSES_PATH`，或 `--llm-search-tool` /
  `--llm-responses-path`；默认 `web_search` / `/responses`，**默认时 URL 与请求体逐字节不变**）。
  口径：这两个旋钮只改「发什么请求」，**不改**「怎么判断端点会不会联网」——
  判据**永远**是回复里有没有 `web_search_call`，与工具叫什么都不相干；日志/任务书里那行
  `本次核实通道：responses + <实际配置的工具名>（已启用联网检索）` 写的也是配置值
  （权威实现 `slcn/llm.py: resolve_search_tool()` / `resolve_responses_path()`；
  用法见 `docs/GUIDE.md` §3.7，回归见 `tests/test_llm_knobs.py`）。
- **自动检索证据是「附加线索」，不是产物（第 71 条起，`--search-refs`）**：默认关闭；
  不开启时任务书 / `reference.bib` / `.tex` 与从前**逐字节相同**（回归见
  `tests/test_search_refs.py` (h) 组：默认一次检索请求都不发）。证据来自 DeepSeek 独立
  Search API（`slcn/search.py`），只写进任务书，**不改模型提示词**。判分口径
  （标题相似度 0.55 + 作者 0.15 + 年份 0.10 + 刊名 0.10 + 域名层级 0.10；阈值
  0.75=「检索到」/ 0.55=「需判断」）属于**判据**，改动须有真实稿件对照；
  「未查到」必须回显用过的 query，**不许**写成「文献不存在」；query 含题名/作者/刊名
  （隐私口径见 `docs/GUIDE.md` §3.10）。
- **证据注入与来源 URL 白名单（第 72 条起，P2）**：开了 `--search-refs` 时，P1 的证据要以
  **数据**形式注入逐条提示词（规则：只依据证据、不得编造来源、证据「未查到」时按查不到处理），
  且合并前必须校验 `% 来源：` 行的 http(s) URL **全部属于本次实际检索到的来源**
  （本次**全部**检索结果 URL ∪ 内置检索 `url_citation`；比较口径：忽略片段/查询参数/端口/
  末尾斜杠，按「域名 + 路径前缀」判同一来源——逐字比较会误伤真实来源的 URL 变体，
  2026-10-17 真 API 验收实测 7/22 误伤、修正后 22/22 通过）；换域名/换路径视为无法核实，
  该条**不并入**
  （原文逐字节未动），原因写进任务书「六、合并记录」。白名单是第 72 条起的**第五条**硬保障，
  与「key 不可改 / 恰好一条 / 不增删条目 / 未返回条目不动」并存；「来源说明」（无 URL）照旧放行。
  默认关闭时提示词与产物与从前逐字节相同（回归见 `tests/test_evidence_injection.py`）。
- **核实条目范围 = 正文引用到的键（第 128 条，2026-10-18）**：任务书逐条清单、`--search-refs`
  检索与 AI 预跑**默认只覆盖正文引用到的条目**（LaTeX / Word 都以**产物 `.tex`** 的引用命令
  为准；`\nocite{*}` = 全量，BibTeX 语义如此）。正文解析不出引用键时**退回全量 + 警告**
  （不许静默「一条都不核」）；全量必须 `--verify-all-refs` 显式承认，本次条数默认上限
  `--verify-max`（120，`0` = 不限），超了退出 2、先看账再花钱。⚠️ 这是**条目范围**的口径，
  与上面的「核实范围 = 所有字段」（第 69 条，字段粒度）**不是一回事**，两者并存。
  权威实现 `slcn/refmark.py: resolve_verify_scope()`（范围判定只有这一份）；
  用法见 `docs/GUIDE.md` §3.10，回归见 `tests/test_verify_scope.py`。
- **先查编辑部引用库，命中已核实 → 跳过检索与 AI（第 129 条，2026-10-18）**：核实链路
  （`convert.py` ⑪，`--verify-refs` / `--verify-plan`）默认先查**编辑部自己的引用库**
  （私有笔记仓 `EditToolBox-notes/bib_db/bibliography.jsonl`；来源 = 往期已出版期的成品 bib）。
  判定**保守**（误判比漏查更糟）：只有 ① DOI 精确命中 `verified=true` 记录、或
  ② 题名高置信（bibdb 的 0.90 阈值）+ 第一作者姓/年份相容 + `verified=true` 才算
  **安全命中**，该键**不调 Search API、不调 AI**；**key 命中只算候选**（key 多为 per-paper）
  仍走检索/AI；库内未核实/低置信同样只算候选。安全命中**不改 `reference.bib`**：库版本与
  稿件条目的字段差异只写进任务书「五、库内已核实（无需再查）」，由编辑裁决；
  「六、合并记录」把「库内命中（未调 AI）」与「正文未引用（范围外）」分开统计。
  默认口径 = 执行核实且默认库存在时**自动启用**（库/笔记仓不存在时静默跳过；显式
  `--bibdb` / `--bibdb-path` 才告警），`--no-bibdb` 显式关；**不核实路径逐字节不变**。
  权威实现 `slcn/bibdb.py: Store.match_reference()`（判定只有这一份）＋
  `slcn/bibdb.py: default_db_path()`（库位置锚点）；
  用法见 `docs/GUIDE.md` §3.10，回归见 `tests/test_bibdb_verify.py`。


（照抄第 21 条；权威实现 `slcn/bibcheck.py: REQUIRED` / `check()` / `unify_types()`）

| 类型 | 必备字段 | 实测出现率（全库 193 个 bib ≈9000 条） |
|---|---|---|
| `article` | author title year journal **volume number pages** | 100/100/100/98/**87/75/94**% |
| `book` | author title year publisher **address** | 99/100/99/99/**61**% |
| `incollection` | title author booktitle year publisher **pages** | 100/100/100/99/95/**68**% |
| `inproceedings` | author title booktitle year **pages** | 100/100/88/90/**78**% |
| `thesis`（中文学位论文） | author title **type** **institution** | 100/100/**97/92**% |
| `phdthesis` | author title **school** year | 100/100/**93**/100% |

类型统一（往期写法，`TYPE_ALIASES`）：`@conference` → `@inproceedings`；
中文学位论文用 `@thesis` + `type`（硕士/博士学位论文）+ `institution` + `date`；
英文学位论文用 `@phdthesis`/`@mastersthesis` + `school` + `year`。
产出 `<Name>_参考文献核对.txt`，**只报告不擅自改内容**——缺什么报什么，由编辑补。

**《斯坦福哲学百科》（SEP）词条：固定形状（第 84 条，2026-10-15 编辑部定）**

权威文本在 `slcn/refmark.py`（第一节第 2 条，那份同时是给 AI 的规范），这里只记要点：

```bibtex
@incollection{Steinberger2022,
  author    = {Steinberger, F.},
  title     = {The Normative Status of Logic},
  booktitle = {The Stanford Encyclopedia of Philosophy},
  editor    = {Zalta, E. N. and Nodelman, U.},
  edition   = {Winter 2022},
  publisher = {Metaphysics Research Lab, Stanford University},
  year      = {2022},
  url       = {https://plato.stanford.edu/archives/win2022/entries/logic-normative/}
}
```

- **类型固定 `@incollection`**；`booktitle` / `publisher` 逐字如上；`editor` 照**该版存档页**写
  （历年编者不同：近年是 Zalta + Nodelman，更早只有 Zalta）。
- **`edition` = 存档季节 + 年份**（`Winter 2022`）；**官网写了才写，没写不补**，不许从 URL 推。
- **`url` 必须是带 `archives/` 的永久链接**，季节/年份与 `edition` 一致。
- **不写 `pages`**（词条没有页码）、**不写 `address`**。
- **判据例外（实现）**：`slcn/bibcheck.py: is_sep_entry()` 命中时把 `pages` 从 must 里去掉，
  否则每条 SEP 都会永远报「必补 pages」，编辑照着报错只会去编一个页码出来。
  判据只看 `booktitle`（不看 key），且**容忍保护花括号**（官网写 `The {Stanford} Encyclopedia…`）。
- **取数**：用词条页**自带的官方 BibTeX**，再按 `refmark` 那张对照表改形状
  （`howpublished` → `url`、去掉保护花括号、删 `urldate`、**key 换成我们的**）。
  规范化实现见 `slcn/bibnorm.py: _sep_fixup()`（**只对 SEP 生效**，非 SEP 条目逐字节不变）。
- ⚠️ **作者/编者的倒装**：仍按 §四.7 一律倒装。往期成品里有不倒装的写法（`10 Bo_Chen` 那份 bib），
  **两种写法印出来完全一样**——SLCN 样式统一按「名首字母. 姓」排印（2026-10-15 用成品 PDF 核对：
  Bo_Chen 印 `F. Steinberger`、Jingru_Kou 印 `T. Burge`，两边源文件的写法相反）。所以不必为此改规范。

**字段里的保护与固定写法（第 85 条，2026-10-15 编辑部定；权威文本 `slcn/refmark.py` 第一节第 2 条）**

**为什么必须保护——机制在 `编辑笔记/模板文件/SLCN-MIX.sty:733`，不是风格偏好：**

```latex
\DeclareFieldFormat[article,inproceedings,incollection,misc]{title}
    {\cegen{“#1”}{``\MakeSentenceCase{#1}''}}
```

`\cegen{中文}{英文}` 按 `userf` 选分支 ⇒ **只有不带 `userf = {cn}` 的条目**才会被 `\MakeSentenceCase`
处理，**没被 `{}` 保护的大写字母会被压成小写**（成品 PDF 实证：`{Two Dogmas of {E}mpiricism}`
漏掉 `{E}` 后印成 `…of empiricism`）。四类要保护（**只裹住那一个字母**）：

| # | 保护什么 | 例子 |
|---|---|---|
| 1 | 专名 / 术语首字母 | `title = {Two Dogmas of {E}mpiricism}` |
| 2 | 副标题（冒号后）首字母 | `title = {A Cognitive Approach to Human Communication: {A}n Action Assembly Theory}` |
| 3 | 罗马数字 / 编号 / 单字母变量 | `Part {I}`、`Generalizing {L}` |
| 4 | 标题里不想被当正文的括号说明 | `\textup{(}14th Edition \textup{)}` |

`@book` 的标题**不做**句首大写（只加斜体），中文条目（`userf = {cn}`）也不做 ⇒ 这两类不需要保护。

**另两条固定写法：**

- **英文书籍的版次写进 `title`**：`title = {The Uses of Argument, (Updated Edition)}`
  （**不单列 `edition`**；模板 `reference.bib` 里一个 `edition` 字段都没有）。
  ⚠️ **SEP 词条例外**：它的 `edition` 是存档季节 + 年份，照旧单列（第 84 条）。
- **只有编者、没有作者的书**：编者写进 `author`，**外层多套一层花括号**并加 `(eds.)`：
  `author = {{F. H. van Eemeren and P. Wu~(eds.)}}`（编者 > 3 人缩为 2–3 人）。
  单列 `editor` 只用于「有作者、另有编者」的情形。
- **作者 > 4 人 / 编者 > 3 人**（《排校手册》§校对规范·参考文献第 2 条）写 `{D. Lewis and others}`。
  分工：**编文集**（只有编者）用 `(eds.)` 形态；**普通多作者**的长名单用 `and others`。

**判据**：`slcn/bibcheck.py: style_form_issues()`（nice 级提示，由 `slcn/bibreport.py` 汇入回读校验报告）。
⚠️ 它**刻意不并进 `check()`** —— `check()` 喂的是冻结基线 `out/docx_audit.csv`（见本文件里
`author_form_issues` 那段注释的同一个理由）。回归见 `tests/test_bib_protection_forms.py`。

**键的稳定性（硬要求，第 74 条；权威实现 `slcn/docxsource.py: make_key()`）**

- **同一份源稿、同一版代码，两次运行的 bib 键必须逐个相同**。键进正文的 `\parencite{…}`，
  键一变，「两次运行的产物」与「`reference.bib.ai前` = 合并前那份」这两条比对就全部失效。
- 因此生成键**不许**用 Python 内置 `hash()`（受 `PYTHONHASHSEED` 随机化，**逐进程不同**）：
  兜底键用 `zlib.crc32(...)` 这类确定性函数。
- 分类前要**容忍不可见字符**：作者串可能以 U+FEFF / 零宽字符开头（Word 复制粘贴带来的），
  那会让「以 ASCII 字母开头」的判断失配。口径是**先按原串分类、判不出再剥零宽重判**
  （顺序不能反：反了会改掉「名字中间夹零宽」的既有键），仍判不出才走兜底。
- 反例（必须守住）：**既有的非兜底键逐字不变**（`ChenBo2005` / `BeallRestall2006` / `Russell1918`…）；
  回归见 `tests/test_bib_key_determinism.py`（含跨进程对照）。

### 3. 引用双向核对：三类问题

（照抄第 22 条；权威实现 `slcn/bibcheck.py: check_citations()`）

| 类型 | 说明 |
|---|---|
| ① **悬空引用** | 正文引了 `\parencite{key}` 但 bib 里没有 |
| ② **冗余条目** | bib 里有但正文从未引用 |
| ③ **疑似漏引** | 正文把主张归给某作者（`X提出…`）却无引用 |

⚠️ **③ 的判据必须收得很紧**：只在「作者名 + 归因动词」（提出/认为/指出/主张/论证…）时才报，并按键+位置去重 ——
早期版本只要出现作者名就报，误报极多。③ 的样板案例见第 22 条（`Bo_Chen` 的「陈波提出社会历史因果描述论」）。

**未匹配的引用点**（另一条线：正文「（作者 年份）」→ `\parencite{key}`，权威实现
`slcn/docxsource.py: link_citations()`，只在**人年制**通道跑）。口径三条：

1. **匹配依据只能是「姓 + 年」**（含中文译名表、`TRANSLATED_NAMES`）。**「该年份在表里唯一」
   本身不是依据**——它只是候选范围。
2. 姓+年落空后，只有拿到**姓名证据**才允许按「年份唯一」兜底（`_year_fallback_evidence()`：
   条目的姓作为词元出现在引用名里 / 条目作者整串出现在引用名里 / 引用名出现在条目作者串里，
   西文按**词边界**，防 `Li` 命中 `Little`）。命中要留警示日志。
3. 没有证据就**原样保留并进 `unmatched`**（绝不静默换成同年的另一篇文献）。
   这条是第 73 条的教训：`（Williamson 2017）` 曾被硬配成 `\parencite{Hjortland2017}`，
   而真正的 `Williamson2007` 反倒从未被引用、印出来的文献表少一条。

### 4. 核对口径：区分「未结构化」与「真缺字段」

（照抄第 25 条；权威实现 `slcn/bibcheck.py: check()` + `slcn/footref.py`）

脚注体例的参考文献解析不出来时，**按设计整条塞进 `note`**；核对器若看到没有 `author`/`title` 就报「必补」，
会把数字**虚高一倍**（第一版报 2071 项，其中一篇 44 条的稿子报出 84 项）。修正为分开统计后：

| | 修正前 | **修正后** |
|---|---|---|
| 总必补字段 | 2071 | **1339** |
| `year` | 319 | **75** |
| `author` | 318 | **74** |
| `title` | 312 | **68** |
| `number` | 454 | **454**（不变） |
| `address` / `pages` / `volume` | 228/176/169 | **不变** |
| 未结构化（新增单列） | — | **244 条（10%）** |

`author`/`title`/`year` 的虚高被剔除，而 `number`/`address`/`pages`/`volume` **纹丝不动**
—— 说明后者是**真缺字段**。那 244 条「整条在 note 里」的条目按三类拆（`slcn/footref.py`，第 26 条）。

### 5. 《排校手册》：引用 / 脚注 / 排版规范

来源（权威文本）：`编辑笔记/排版手册/排版手册源码/edit_handbook.tex` 第 276–330 行；
成稿版 `编辑笔记/排版手册/排校手册.pdf`（11 页，**比源码多出好几节**）。
模板专用宏在 `编辑笔记/模板文件/SLCN-MIX.sty`（第 40–43 行）：

```latex
\newcommand{\ct}[1]{\textup{\raise0.1em\hbox{[}\cite{#1}\raise0.1em\hbox{]}}}  % → [1]
\newcommand{\pct}[1]{\textup{（\ct{#1}）}}                                   % → （[1]）
\newcommand{\pcp}[2]{\textup{（\ct{#1}，第#2页）}}                           % → （[1]，第N页）
```

**脚注规范**（照抄第 33 条）：

| # | 规则 |
|---|---|
| 1 | 脚注在**逗号处则放于逗号前**，在**句号处则放于句号后** |
| 2 | 一句话有两处脚注且各对应一部分时，**都放在标点前面** |
| 3 | 句号后的脚注**又紧跟汉字**时，需加 `\,` 调整间隔 |
| 4 | **表格中的脚注会被吞掉**，须改用 `\footnotemark` + 表格后 `\footnotetext` |
| 5 | 脚注内容要**以句号结尾**（「没有句号的脚注是不完整的」） |

**文献引用规范**（照抄第 33 条）：

| # | 规则 |
|---|---|
| 1 | 参考文献位置与脚注同理：**通常放在逗号前 / 句号后** |
| 2 | 一般情况下文献**直接加括号写在正文中**：`（\ct{a6}，第8\cndash 12页；\ct{a5}）`；如需详细说明则写成脚注 |
| 3 | **书籍需标具体页码**，除非只是宽泛提到那本书而没谈内容 |
| 4 | **期刊文章且篇幅不长时不标页码**；即使作者写了，一般也要删掉 |
| 5 | **多个参考文献只用一个引用命令**：`\ct{a11,1,a10,a1}`，而不是 `\ct{a11}\ct{a10}\ct{a1}\ct{1}`。同一 `[cite]` 内的引用会自动排序并缩写连续序号（中英文皆然） |

**连字符（3 种，不能混用）**（照抄第 34 条）：短横 `-` 用于编号/日期/字母序号/人名；
双短横 `–` 用于**页码范围**（`第8–12 页`）；一字线 `—` 是半个破折号（`北京—上海专列`）。
工具输出 `第632\cndash 635页`，方向一致。

**英文文章的特殊规范**（照抄第 34 条）：英文引文直接用 `quotation` 环境**不加引号**；
Word 复制来的英文后引号会变成中文引号（`he's` 这种是明显错误）；
大章节标题每词首字母大写、小章节及以下仅句首大写；`et al.` / `p. 8–12` 要**缩小间隔**；
英文破折号用 `---`；行内溢出用手动连字符或 `\-`，**参考文献的溢出也用这个方法**。

**参考文献排序**（照抄第 34 条）：`B.C. van Fraassen` / `H. van Ditmarsch` 这类名字**中间的名字统一用小写**，
按 `F`/`D`/`H` 参与排序而不是按 `V`；要按 `V` 排就给名字加波浪号 `van~Fraassen`；
**同一篇文章要保持标准统一，非必要不修改**。

> ⚠️ **手册对「作者姓名要不要倒装」是沉默的**：它给出的例子反而是**名前姓后**
> （`{D. Lewis and others}`、`B. C. van Fraassen`）。所以「一律倒装」的依据**不是手册**，
> 而是**编辑的实际成品 + 用户 2026-10-15 的决定** —— 见下面 §7 与 `docs/cases/62+.md` 第 63 条。

> **实现状态**（照抄第 33 条）：引用外层括号 ✅、页码中文惯用式 ✅；
> 多引用合并为一个命令 ⏳、标点位置 ⏳、书籍标页码/期刊删页码 ⏳、脚注位置规则 1–3 ⏳、表格脚注 ⏳。

### 6. 编辑交回的 `.bib`：回读校验（`checkbib.py`）

（照抄第 55 条；权威实现 `Engine/checkbib.py`，回归 `tests/test_checkbib.py`）

```bash
python3 checkbib.py --returned 退回.bib [--original 原件.bib] [--tex 正文.tex] [--out 报告.md]
# 手动「并回」（2026-10-15）：把交回的整份 .bib 并进产物（与自动通道同一份合并核心）
python3 checkbib.py --returned 交回.bib --merge-into "…/01 Jingru_Kou/reference.bib" [--tex 正文.tex] [--out 报告.md]
# 粘贴模式（2026-10-15 phase 2c）：文本走标准输入，**不落中间文件**；预览只打一行、永不写文件
pbpaste | python3 checkbib.py --returned-stdin --merge-into "…/reference.bib"
pbpaste | python3 checkbib.py --returned-stdin --list-keys      # 例：20 条：1, 2, 3, 4, 5, 6, 7, 8…
```

**退出码（体检模式）**：`0` = 无问题 / `1` = 有问题 / `2` = 用法或 IO 错误；编码 UTF-8 → GB18030 降级。
**退出码（并回模式，加了 `--merge-into`）**：`0` = **未改动**（一条都没并进去，且不写任何文件）/
`1` = **已并入 ≥1 条** / `2` = 用法或 IO 错误 —— ⚠️ 同一个 `0/1` 在两种模式下含义不同。
**退出码（列表模式，`--list-keys`）**：`0` = 打印了那一行 / `2` = 用法或 IO 错误（它不判断 bib 好不好，**不会**返回 1）。
`--returned` 与 `--returned-stdin` **二选一**（同给 → 2）；`--returned-stdin` 空输入也是**用法错误**（2）。
并回的判据、合并记录落点与回滚副本见 §五。

| # | 检查 | 内容 |
|---|---|---|
| ① | **可解析 / 语法** | 坏条目**不崩**，报出**键与原因** |
| ② | **编辑部规范** | 必填字段；`doi` / `isbn` **不该回流**（第 41 条）；中文条目必须 `userf = {cn}`；页码 `--`；期号无前导零；月份三字母；**英文 `author`/`editor` 未倒装 = 问题**（§7）；中文译著缺「（著）/（译）」标记 = **提示** |
| ③ | **引用键闭合** | 给了 `--tex`：**正文引用的键必须都在**；反方向（bib 里有、正文没引）**只作提示** |
| ④ | **与 `--original` 的字段级 diff** | **key 改名 / 删除、字段被删、条目类型被改**一律算「**问题**」——它们会让正文引用悬空或排版错位 |

**复用关系（关键：没有另写一套规范）**：逐条规则都来自 `slcn.bibcheck`（`check` / `cited_keys` / `TYPE_ALIASES`）
与 `slcn.bibnorm`（`_clean_field_value` / `_MONTHS` / `detect_userf` / `DROP_FIELDS` /
`author_is_inverted` / `normalize_author_field`）；
**解析器是唯一一份**（`slcn/bibparse.py`，2026-10-15 从本文件抽出——`bibcheck.split_entries` 对
**坏文件**会静默误解析：假 key、吞掉后续条目、`x = y` 当字段，所以并入通道不能复用它）；
**报告也是唯一一份**（`slcn/bibreport.py: build_report_and_conclusion()`，
`checkbib.py` 与 `convert.py` 的并入通道共用，见 §五）。
**纯离线**：从不 import `slcn.llm` —— 核实文献**真伪是编辑的事**
（`tests/test_checkbib.py` 的 ⑤ 组用 AST 扫 `checkbib.py` 与它复用的三个模块
——`slcn/bibparse.py` / `slcn/bibreport.py` / `slcn/aibib.py`——的 import）。

**两条「降档/顺序」口径**（第 65 条；权威实现 `slcn/bibreport.py: check_spec()`）：

- **必填字段缺失 → 有「待核实」注释且点名了该字段才算提示**：条目上方有
  `% ⚠ 待核实：<字段>…` 且注释文本里出现该字段名 → **提示**（查不到允许省略，编辑仍需补）；
  有「待核实」注释但**没点名**这个字段、或压根没有注释 → 仍是**问题**。
- **字段顺序**按 §四 的固定顺序查（`SPEC_FIELD_ORDER`，只查「出现顺序」的相对关系）：
  不一致只在「二、编辑部规范」里汇总成**一条提示**（几百条会淹掉真问题）。
  并入通道会**自动重排**（§五），所以并过的条目不再命中这条。

### 7. 英文作者 / 编者一律倒装（2026-10-15 用户决定）

**权威实现**：`slcn/bibnorm.py` 的 `normalize_author_field()` / `author_is_inverted()`；
离线判据 `slcn/bibcheck.py` 的 `author_form_issues()`（**问题**）与
`translation_marking_issues()`（**提示**）；回归 `tests/test_authornorm.py`。

| 规则 | 例子 |
|---|---|
| 英文作者 / 编者**一律**倒装成「姓, 名缩写」 | `T. Burge` → `Burge, T.`；`D. J. Chalmers` → `Chalmers, D. J.` |
| 全名缩成首字母、**中名首字母全部保留** | `Jonathan L. Kvanvig` → `Kvanvig, J. L.`；`Jerry L Morgan` → `Morgan, J. L.` |
| 多作者用 ` and `、**每一个**都倒装 | `J. P. Dillard and C. Segrin` → `Dillard, J. P. and Segrin, C.` |
| 虚词小写、随姓氏排序（手册第 34 条） | `B. C. van Fraassen` → `van Fraassen, B. C.`；波浪号 `van~Fraassen` 保留 |
| 编者标记连姓带括号 | `J. Salerno (ed.)` → `{Salerno (ed.)}, J.` |
| **中文、日文姓名不倒装** | `党学哲`、`太田方` 原样 |
| 中文译著写「原作者（著），译者（译）」 | `阿斯海姆（著），张建军、万林（译）`（保留中文逗号/顿号，**不要**改 `and`） |

**为什么（不是凭印象）**：`2026年第5期(90)/01 Jingru_Kou` 的**编辑成品** 20 条英文条目
**20/20 是倒装式**；而工具当时按旧规范（「本名倒装仍用逗号……**两种写法都对**」+「非必要不修改」）
照抄了源稿的「名 姓」，**15 处里差 14 处**。规范改成硬规则后，同一份稿子
`reference.bib` 的 **15/15** 条英文 author 与成品逐字节相同。

**边界（宁可不改也不改错；都写在 `bibnorm.normalize_author_field` 的注释里）**：

- **判不准的整条不动**：`JC Beall`（手册点名「唯一正确写法，需特殊处理」）、
  2 字母缩写（`Jc`/`Al`）、`Sammy Davis Jr.`、逗号与 `and` 混写的列表
  （`J. Almog, J. Perry, and H. Wettstein (eds.)`）→ 一律原样返回，**不产出「半倒装」的混合条**
  （手册要求「同一条里不能混用」）。
- **规范化器与检查器同源**：`author_is_inverted(x)` 就是「`normalize_author_field(x)` 改不动它」，
  所以不会出现「报错但改不动」的假阳性。
- **`bibcheck.check()` 刻意不含这条**：`docx_audit.py` 用它量「解析出来的条目有多少必补字段」，
  而 `out/docx_audit.csv` 是**冻结基线**（README AUTO 块钉着 sha256）。把只关乎「值怎么写」的
  规则塞进 must 档会让 132 行的 `bib_must` 整列跳动，与「解析器能不能拿到字段」混在一起。
  所以另开 `author_form_issues()` 入口，由 `checkbib.py` 与 `refmark.py` 显式调用。
- **引擎自动倒装的范围**：只覆盖**从源稿解析出来的**参考文献（Word 通道：
  `docxsource.refs_to_bib` / `footref` 编号清单 / `bibnorm.expand_note_entries`）。
  **LaTeX 通道**（作者自带的 `.bib`，走 `normalize_bib`）**不动**——那是作者的原始文件，
  由《文献核实任务书》第一节要求 AI / 编辑改成倒装式。
- **实测噪声**（`out/corpus/**/reference.bib` 62 个文件）：英文未倒装 **38 个文件 / 969 条**
  （硬规则，照报）；中文译著缺标记 **4 个文件 / 8 条**（**只作提示**，不挡退出码）。
  ⚠️ 第 64 条把这条判据**放宽到任何角色标记**（`（编）（点校）` 也算）并加了「有标记却用 ` and `」
  一条，所以噪声数字比第 63 条记的那次**只会多、不会少**；判据本身写在
  `translation_marking_issues()` 的注释里（权威），这里不另记数字。
- **中文著/译体例豁免「多作者用 `and` 分隔」提示**（2026-10-15，第 64 条）：
  `bibcheck.author_has_role_mark()` 命中「（著）/（译）/（点校）/（编）…」的值，
  **不**按普通中文多作者口径建议改成 `and`——那个中文逗号与顿号是**姓名的一部分**，
  改了会毁掉著/译标记。合规性由 `translation_marking_issues()` 按
  `原作者（著），译者（译）` 校验。两处调用方：`slcn/bibreport.py: check_spec()`（豁免）、
  `slcn/footnotes.py: normalize_authors()`（不拆）、`slcn/docxsource.py`（完整形态下放行 3 字题名）。
- **中文译著解析**（2026-10-15，第 64 条）：`slcn/footref.py: _translation_author_segments()`
  ——首段（著）+ 次段（译/点校）同时成立时，前两段合起来才是作者，题名取**第三**段。
  实测修正 `2026年第5期(90)/01 Jingru_Kou` 的 3 条（`A2014` / `Ke2005` / `Wo2025`：
  原来 `title = {张建军、万林（译）}`，现在作者保留 `阿斯海姆（著），张建军、万林（译）`、
  题名是 `指称与意向性`）。

### 8. bib 文件的整行注释（第 133 条，2026-10-18）

**口径**：`.bib` 里**整行注释**（行首可选空白后以 `%` 开头，到行尾）不算条目、不算字段；
行内/字段值内的 `%`（`note = {50\%}`、`title = {A % B}`）**原样保留**，不做「聪明的行内
截断」。权威实现：`slcn/bibnorm.py: strip_bib_comments()`（`split_entries()` 拆条目前先过
一遍它）。

**为什么**：作者交来的旧模板稿 `.bib` 里常见注释掉的旧条目/空键模板
（`% @article{,`、`% @misc{z16,`）。从前 `split_entries()` 全文正则匹配 `@\w+\s*\{`、
不跳过 `%` 注释行，产物 `reference.bib` 会出现空键 `@article{,` + 重复键 → biber 语法
报错、**整份 bib 报废**（2023(71)04 实测：115 处 undefined citation、参考文献区未生成、
19 页 vs 人工 20 页）。

**两套 parser 已统一（2026-10-18 补）**：`slcn/bibcheck.py: split_entries()`（供
checkbib / bibreport / aibib / convert 的核实范围与内容指纹使用）**也**先过
`strip_bib_comments()`，与 `bibnorm` 同一口径——从前的第二套实现会把 `% @misc{z16,`
这类**非空键**注释条目算进去（checkbib 的条目总数/问题清单会凭空多一条）。
不存在的循环 import：`bibnorm` 顶层只 import `re`，对 `bibcheck` 的引用是函数内懒加载。

**边界**：只认整行注释；「字段值里整行以 `%` 开头」的极端写法会被当注释删掉（与 TeX
的整行注释口径一致）。不影响 Word 通道（`reference.bib` 由 `refs_to_bib()` 直接渲染，
`move_book_editions()` 的窄口径行为不变）。回归：`tests/test_bibnorm.py`（①–⑤ 组）。


### 9. 参考文献附件的选择 / 校验 / 合并（第 134 条，2026-10-18）

**口径**（权威实现：`convert.py` 的 `real_bib_path()` / `bib_attachment_entries()` /
`merge_bib_attachment()` / `bib_entry_structurally_safe()`，以及
`slcn/multisource.py` 的 `looks_like_bibtex()` / `text_attachment_issue()` /
`bib_attachment_issue()` / `select_refs_attachment()`）：

1. **有「真 .bib」就不自动并附件**：`--bib` 显式指定 → LaTeX 源里 `\addbibresource` /
   `\bibliography` 指向且**文件存在** → 同目录自动发现的 `.bib`（`find_bib()`）三者任一命中
   时，⑦ **只记日志、不合并**自动挑到的附件（确实要合并请显式 `--refs`）。Word 通道不走
   这条规则（它的 bib 由文档内容生成，旧行为不变）。
2. **没有真 bib 时才挑附件**，优先级：真 BibTeX（`.bib` 扩展名或内容有**非空 key 的
   `@entry`**）> 纯文本文献清单；同类再按文件名含 bib/reference/参考文献、扫描置信度、
   长度排序。每个候选都要过结构校验，**不合格的跳过并写明原因**。
3. **文本附件结构校验**（保守、只按整行判）：拒绝含 `\href{...}`、相邻花括号 `}{`、
   疑似「一行两条文献」（同一行 ≥2 个 `[J]/[M]/…` 类型标记且 ≥2 个年份，或 ≥2 个年份且
   该行 ≥500 字符）。
4. **合并**：BibTeX 附件按条目**原文直入**（键 + 「作者+年份+题名」内容指纹去重，不改写
   作者字段）；文本清单沿用 `footref` 解析，但逐条做「渲染后结构校验」，坏条目（嵌套
   花括号等）**不写**进产物。

**为什么**：71/04 的正文目录里放着 text2bib 的中间产物 `预处理/用于上传text2bib.txt`
（两条文献挤一行、含 `\href{a}{b}`），⑦ 从前「扫到就并」→ 产物 `title = {...}{...}` 嵌套
花括号 → biber 行级语法错、**整份 bib 报废**（115 处 undefined citation、19 页 vs 人工
20 页）；而该稿正文目录本来就有 `reference.bib`（源里也写了 `\addbibresource`），附件合并
纯属帮倒忙。

**边界 / 已知取舍**：① 只做**整行**判据，不做行内聪明截断；② 文本清单「一行两条」是
启发式（极长的正常单条也可能命中 500 字符规则而整份被跳过——保守优先，日志会写明原因，
可用 `--refs` 手工指定并人工整理）；③ Word 通道对 BibTeX 附件**保持旧行为**（不消费），
避免产物变化；④ 没有真 bib 且所有候选都不合格时**不合并**（宁可少条目，不让 biber 报废），
日志给「跳过 + 原因」。

**回归**：`tests/test_refs_attach.py`（①–④ 组：结构校验 / 选择优先级 / 真 bib 判定与合并
辅助 / convert 端到端）；真实稿 2023(71)04 用 convert 默认路径编译通过（biber 0 错、
undefined citation 0、参考文献区生成、20 页 = 人工 20 页）。


---

## 五、任务书规范（《参考文献核实任务书》）

**为什么是任务书而不是自动核实**：CrossRef 通道**已于 2026-10-14 按编辑部要求移除**
（中文文献覆盖差、会给出错配的「建议」、不该绑定某一家 AI）。改为：工具把材料准备好，
编辑交给自己用的 AI —— 见第 23 条（历史与教训）与第 41 条（现行路线）。
**权威实现 `slcn/refmark.py`**；规范正文就是该文件的 `SPEC_MD` / `SOURCES_MD` / `FORMAT_MD`
（**不要在这里复制**）。⑪ 开了**联网检索**（`--llm-web-search` + 通道 `responses`）时，
system prompt 还会追加该文件的 `SEARCH_MD` 一节（「必须先用检索工具、留下 `% 来源：`」），
逐条请求追加 `SEARCH_PROMPT_LINE` —— 见 §四.2「检索状态的可追溯性」与
[`GUIDE.md`](GUIDE.md) §3.7。**关着时一个字都不追加**（提示词与从前逐字节相同）。

任务书的固定骨架（`refmark.py: build()` 的行顺序，权威）：

```text
一、编辑部 bib 规范（**必须遵守**）      ← SPEC_MD
二、查证来源（按优先级，请优先用**官方**来源） ← SOURCES_MD
三、请交付**完整、可直接编译的 .bib 文本**  ← FORMAT_MD（第 11 条：每条核实过的条目上方写 `% 来源：…`）
四、逐条清单                          ← 每条以 `## \`key\`` 为标题，附该条缺什么 + （有的话）**源稿原文**
五、（可选）文献区的**原文**            ← 只在「一条都没解析出来」时追加（第 61 条）
```

- **逐条清单的「源稿原文」行（第 68 条，权威 `slcn/refmark.py: _entry_block` /
  `_norm_raw_line`；映射见 `DocxDoc.ref_raw_by_key`）**：每条能拿到源稿原文的条目，
  附带一行
  `- **源稿原文**（解析可能有遗漏，请以它为准）：\`[17] …\``。
  口径 = **原文优先、但不许编**：原文与解析出的条目不一致时按原文补全/纠正，
  原文里没有的信息仍然不许编（查不到写 `未查到`/省略）；**拿不到原文的条目一个字都不多写**
  （作者自带的 `.bib`、LaTeX 通道），行为与从前逐字节相同。
  归一化：压成一行、去首尾、**超 300 字截断并标注**（`RAW_LINE_MAX`），原文里的反引号
  不会提前关掉行内代码（`_code_span`）。

- **编号是「五」不是「四」的原因**：任务书本来就有「四、逐条清单」，另起「四」会撞号（`refmark.py: _raw_refs_section()` 注释）。
- **说明注释写在哪儿**（第 65 条）：任务书第三节第 6–9 条**明确要求**把
  `% ⚠ 待核实：<字段>（原因）` / `% 类型已改：…` 写在**那个 bibtex 代码块内部、紧挨条目的上一行**
  （权威文本 = `refmark.py: FORMAT_MD`，此处不复制全文）。为什么必须点名位置：回读校验
  （§四.6）只把「条目**上方**的注释」算作该条的一部分，写到代码块**外面**（例如条目后面）
  工具读不到，同一个缺失字段就会从**提示**被报成**问题**——编辑会以为工具在反复挑错。
  ⚠️ **规范收紧、解析器保持宽容**：`slcn/aibib.py: _entry_block()` 对围栏内/外、
  条目上/下的紧邻注释**都收**，去重后统一放到条目上方（第 65 条）。
- **第 61 条的兜底**：**不写清单解析器**（编辑部口径「编错的卷号比空缺更糟」），改为把文献区原文附进任务书；
  顺带修掉「14 篇『有文献区、`n_bib = 0`』的稿子以前根本不出任务书」的缺口（门槛 `res.bib_path` → `res.bib_path or raw_ref_lines`）。
- **AI 预跑（可选）**：配了 `SLCN_LLM_*` 才会调用（`slcn/llm.py`），结果追加进同一 markdown，
  标题写明「**AI 建议（须人工核实，工具不保证正确）**」；没配就只出任务书。
  逐条请求由 `slcn/refmark.py: entry_prompt()` 拼（**含该条的源稿原文**；没有原文时返回值与
  从前逐字节相同），形状仍是「一条请求 = 一条条目」，所以回复解析与合并规则不受影响（第 68 条）。
  `finish_reason` **非 `stop` 且非空**（截断/内容过滤/中断）→ stdout 告警 + 任务书该条插一行
  `> ⚠️ 本条 AI 返回被截断（finish_reason=length），内容不完整。`（第 54/59 条）。
  ⚠️ responses 通道把 `status` / `incomplete_details.reason` **映射成同一组结束原因**
  （`completed→stop`、`max_output_tokens→length`、`content_filter→content_filter`、
  `failed→failed`、未知原因原样带上），所以「非 stop 即标注」这条判据**两条通道通用**
  （权威 `slcn/llm.py: _responses_finish()`）。
  配了 LLM 时任务书头部还会多一行 **`本次核实通道：…`**（协议 + 是否检索 + 模型名，
  与「六、合并记录」那句同源，见 §四.2）。
- **隐私边界**：默认**不发送**正文、摘要、作者信息、稿件全文；不配置也完全可用（只出任务书）（第 58 条）。

**AI 结果并入 `reference.bib`（2026-10-15 用户决定，第 64 条；取代第 41 条的「AI 结果永不进产物」）**

权威实现：`slcn/aibib.py`（合并与四条保障）+ `convert.py: apply_ai_merge()`（落盘与告知）。
触发条件：`--verify-refs` **且**配了 LLM（界面 = 勾「用 AI 处理参考文献」并配好地址/模型）。
**关掉并入**：`--no-ai-merge`（界面 = 「保留原 reference.bib（不并入 AI 结果）」，只在勾了
AI 时显示）→ 与从前逐字节相同（只出任务书）。

**手动通道（`checkbib.py --merge-into`，2026-10-15，界面 = 「文献回读校验」页的「并回」按钮）**

编辑把任务书交给**自己的联网 AI** 改完交回一整份 `.bib` 时，走这条：
`slcn/aibib.py: merge_returned_bib()` 把整份 bib 拆成与自动通道**同一个** `replies` 形状
（`replies_from_bib()`），再用**同一个** `merge()` 合并——上表四条保障、作者倒装、
字段顺序重排、**先备份后覆盖**（`apply_merge_to_file()`）、合并记录（`write_merge_record()`）
全部共用，**没有第二份合并实现**。口径差异只有两处：

- 回复来源换成「交回文件里那一条的原文」（不是聊天回复），所以「改了 key」表现为
  「返回的键在原件里不存在 → 不新增条目」，原件里对应那条记「未返回」；两条都跳过、都留原因。
- **文本来源可以不是文件**（2026-10-15 phase 2c）：`--returned-stdin` 从**标准输入**读交回文本
  （体检与并回都支持），**不落中间文件**——临时文件名会印进报告头与合并记录，事后对不上账；
  空输入算**用法错误**（退出码 2）。预览用 `--list-keys`（只打一行「`N 条：…`」，**永不写文件**，
  见 §四.6）。界面 = 「文献回读校验」页交回那一栏的「选择文件 / 粘贴文本」分段控件。
- **合并记录落点**：同级有《文献核实任务书》（`<稿件名>_文献核实.md`，或同级唯一一份
  `*_文献核实.md`）就追加到它；没有 / 有多份分不清 → 新建 `<目标 stem>_合并记录.md`。
- **退出码含义与体检模式不同**：`0` = 未改动（一条都没并进去，**一个文件都没写**）；
  `1` = 已并入 ≥1 条；`2` = 用法 / IO 错误。（`--list-keys` 是第三种模式：`0` = 打印成功。）

| 保障 | 判据 | 违反时 |
|---|---|---|
| ① key 一律不改 | 返回条目的 key 与请求的 key 逐字符相同 | 跳过该条、保留原字节（正文 `\parencite` 不许悬空） |
| ② 恰好一条可解析条目 | `slcn/bibparse.py: parse_one()`（结构完好、有 key、无语法问题） | 跳过该条（0 条 / ≥2 条 / 半截都算不可信） |
| ③ 绝不增删条目 | 合并后再比一遍条目数与键的序列（`verify_key_sets()`） | **整份作废**、退回原件 |
| ④ 未返回的条目逐字节不动 | 按偏移**就地拼接**，不做「解析→重排→序列化」 | —（结构性保证） |
| 附：作者/编者倒装 | `bibnorm.normalize_author_field()`（幂等）在写入前跑一遍 | 不算违规，改动记进字段级 diff |
| 附：**说明注释归位**（第 65 条） | 紧邻条目的整行 `%` 注释（围栏内/外、条目上/下都算）收齐、去重后放到条目**上方** | 不算违规；只认「空行 / 代码围栏 / `%` 行」隔开的紧邻注释，散文不吞 |
| 附：**字段顺序重排**（第 65 条） | 按 `bibreport.SPEC_FIELD_ORDER`（本表 §四 的固定顺序）稳定排序，未知字段留末尾；搬原文切片，值不动 | 不算违规，新顺序记进 `FieldDiff.reordered` |
| 附：半截返回不并入 | `finish_reason` 非 `stop` 且非空（截断/内容过滤/中断） | 不并入，原因写进合并记录 |

产物与回滚（三个新文件 / 一节新内容）：

- **`reference.bib.ai前`** = 本次合并**之前**的那份 bib（**回滚副本**；重跑会覆盖它，
  它不是历史存档）；界面/报告里那句「原文件见 reference.bib.ai前」指的就是它。
- **`<stem>_回读校验报告.md`** = 并完立刻做的**离线**回读校验，与 `checkbib.py` **同一份实现**
  （`slcn/bibreport.py: build_report_and_conclusion()`）；控制台会打印它的「结论」一行。
- **任务书 `## 六、合并记录（本次已并入 reference.bib）`** = **本次核实通道**（协议 + 是否
  检索 + **检索证据**个数）+ 逐键：并入 / 跳过 + 中文原因 +
  字段级 diff + 缺必填字段 warning；原有「AI 建议（**须人工核实**…）」全文保留。
  一条都没并进去时，标题写成「（本次未并入 reference.bib）」并明说「逐字节相同」。
- **一条都没并进去 → 不动 bib、不写回滚副本、不写报告**，控制台明说「未改动」。
- ⚠️ 保障只保证**结构不被破坏**，不保证**内容正确**：卷/期/页码/出版地仍由编辑核实，
  并完后**必须人工过一遍**。

---

## 六、placeholder 与覆盖键约定

（照抄第 46 条；权威实现 `slcn/build.py`）

- **「没解析出来」的判定** `is_parsed()` 三条：① 值为空 ② 值为 null ③ **值与模板自带示例文字逐字相同**
  （作者常常直接拿模板改，字段没填就还是示例字 —— 这类值搬进产物等于没填）。
- **产物里写什么**：`【未识别：<字段中文名>，请手工填写】`（`PLACEHOLDER_TMPL`），**全角方括号，xelatex 可正常编译**；
  `myfirstauthor` 额外包一层 `{\kaishu …}`，保证占位符一定看得见。
- **字段清单** `REQUIRED_FIELDS`：中文稿 **11 项**（题名/页眉标题/第一作者/第一作者单位/第一作者邮箱/
  **第一作者英文姓名**/**第一作者英文单位**/中文摘要/中文关键词/英文题名/英文摘要）；
  英文稿 8 项（无关键词字段，多中文题名与中文摘要）。
  第一作者的两个**英文**槽位是 2026-10-15 补进去的（第 72 条）：CN 模板 1.5 节把它们预置成
  示例文字 `First Author` / `Affiliation 1`，不登记就**既不写占位符也不报「未识别」**，
  示例文字会被当成正文静默印进 PDF（第二作者那两槽登记在 `coauthor_fields()` 里，所以一直是对的）。
- **不在此列**：出版信息（`myvolnumber`/`myissnumber`/`mypubyear`/`myfirstpage`/`mylastpage`/`mypages`/`receiveddate`/`myeditor`/`mygrants`）
  —— 来源是 `--vol/--issue/--pages/--received/--editor/--grants`，混进来只会天天误报。
- **编辑兜底**：7 个覆盖键 `OVERRIDE_KEYS`：`title_cn`/`author_cn`/`affiliation_cn`/`email`/`abstract_cn`/`title_en`/`abstract_en`；
  **非空值优先于解析结果，被覆盖的字段不再算未识别、不写占位符**；不认识的键只记 warning 不报错。
  命令行入口是 `--title-cn` 等 7 个参数；GUI 在「初排转换 → 正文信息覆盖」（第 47 条）。
- **合著者槽位（2~5）必须显式化**（第 65 条；权威实现 `slcn/build.py: coauthor_fields()` /
  `en_coauthor_values()` / `coauthor_slot_count()`）：中文槽位 `my<N>author`（N=2…5）非空时，
  **中文与英文两套槽位都要算进占位符清单**——中文 `my<N>affiliation` / `my<N>email`，
  英文 `my<N>authorEN` / `my<N>affiliationEN`。为什么英文那套也要：模板 1.5 节把
  `mysecondauthorEN` 预置成 `null`、`mysecondaffiliationEN` 预置成示例文字 `affiliation 2`，
  而英文摘要页（`printtitlepageEN`）按 `mysecondauthorEN` 是否为 `null` 选单人/双人分支——
  不显式化要么整块少一个人、要么把 `affiliation 2` 印出来。
  英文值只在源稿给出**可靠**的英文作者/单位列表时按位置填（显式分隔符 `and` / `&` / `;` / `、`，
  且段数与中文作者数**完全一致**）；否则写 `【未识别：第二作者英文姓名，请手工填写】` 这一类占位符。
  **绝不猜**：不把第一作者的单位复制给第二作者（`Jingru_Kou` 里两份恰好相同是巧合，不是规则）；
  逗号**不**当分隔符（`Kou, Jingru` 是一个倒装姓名）。**单作者稿一个字段都不动**（逐字节相同）。
- **已知限制**：① 7 键只覆盖**第一作者**的单位/邮箱（合著者走上面那条机制，只能给占位符或
  源稿里显式写明的英文列表）；② 源稿自带的自定义示例文字（如 `第二单位`）与模板原值不同，无法识别；
  ③ **未启用**槽位（没有对应 `my<N>author` 的 3~5）的模板示例文字仍留在 `.tex` 里，但**不会打印**（PDF 未见）；
  ④ 判据 ③（「与模板示例文字逐字相同」）**不是对所有字段都生效**：`myfirstaffiliationEN` 的模板原值
  被 `parse_macros()` 解析成**含 `%` 注释的多行块**，不等于裸的 `Affiliation 1`，所以该字段只靠
  ①「值为空」判未识别（第 72 条实测）。若有人把 `affiliation_en` 原样留成字面量 `Affiliation 1`，
  仍会漏判——这是既有 `parse_macros` 的形状，改动它会误伤别的字段，**未修**。

---

## 七、实测基线（原 §一「实测结果」，照抄）

> **注意**：这一节的数字是 **2026-10-13 那次重测的历史基线**，留着做对照与追责用；
> **当前**的测试/普查/corpus/占位符数字一律看 `README.md` 的 AUTO 块（`tools/gen_docs.py` 注入）。
> 两者不一样是正常的：代码改过，基线不动。

基准是**编辑部的人工成品**（不是理想值——人工成品本身就含编辑判断）。

| 通道 | 篇数 | 裸相似度中位 | 均值 | p10 | ≥90% | 内容准确率中位 |
|---|---|---|---|---|---|---|
| A · LaTeX 源文件 | 21 | **98.56%** | 88.34% | 73.84% | 16 | **98.28%** |
| B · Word 文稿 | 48 | **88.48%** | 85.13% | 75.96% | 18 | 58.03% |
| **合计** | **69** | **89.11%** | 86.11% | — | 34 | — |

口径：2026-10-13 用当前代码重测（`cd Engine && python3 corpus.py`，DSH 自带 Python 3.12），
数字从 `out/corpus/corpus.csv` 复算。⚠️ **可复现性**：连跑三次，Word 中位分别 88.46% / 88.48% / 88.53%，
差异**全部**来自 `05 Yang_Hu` 一篇（引擎自身的非确定性，根因见 `HANDOFF.md` 第九节：`id()` 去重遇 GC）。
所以回归比对请看**逐篇明细**，别只看中位数。

> **2026-10-18 更新（第 125 条）**：这条非确定性已修——`slcn/docxxml.py` 的元素去重从 `id()`
> 改为**元素身份**（集合持有引用），且只登记真正处理过的 `<m:oMath>`；`05 Yang_Hu` 的公式段
> 现在稳定为 276（旧实现随机丢到 245–254）。现在同输入连跑两次 `corpus.csv` 应**逐字节一致**；
> 若再次不一致，按回归处理（根因另找，不要再把「已知抖动」当免责理由）。

**指标口径**：**裸相似度** = 不做任何归一化的字符级相似度，端到端总指标；
**内容准确率** = 只惩罚实词增删改（L3），不奖励标点修复 —— 衡量「还需要编辑动多少脑子」。
两者差距说明：**结构搬运已经解决，剩下的主要是编辑判断**。

表格 / 图片 / 矢量形状覆盖（全库）：Word 表格 19 篇 **100%**；内嵌图片 11 篇 **91%**；
**矢量形状** 9 篇 **89% 检出**（7/9 篇 100%，需人工截图）。
两篇样板稿（端到端含真编译）：`03 Liang_Zhang` 98.15% / 18 页 = 人工 18 页；
`10 Bo_Chen` 97.19% / 20 页 = 人工 20 页。完整报告 `out/REPORT.md`，全库明细 `out/corpus/corpus.csv`。

---

## 八、LaTeX 自定义定义的位置（第 130 条，2026-10-18）

**问题**：作者的自定义定义不一定在 `.tex` 导言区——可能在同目录的旧版 `.sty`（普通宏 /
算子 / 列类型 / 颜色，不只是定理环境）、本地 `.cls`、被它们 `\input` 的 `.def` / `.cfg`，
甚至写在 `\begin{document}` 之后的正文区。这些位置第 120/127 条只覆盖了一部分。

**口径**（权威实现：`slcn/preamble.py`、`slcn/build.py`）：

1. **本地支撑文件**：读作者稿同目录、由前言加载的 `.cls`（`\documentclass{本地类}`）/
   `.sty`（`\usepackage`）/ 前言里直接 `\input` 的 `.sty|.def|.cfg`，并递归读它们内部
   `\input` / `\RequirePackage` 的**同目录**文件；**只读同目录，不查 TeX 发行版**。
2. **迁移判据仍是两条**：`正文真正用到`（宏按 `\名字`、环境按 `\begin{env}`、颜色按颜色
   命令参数、列类型按 tabular 列格式）+ `模板没有`。旧文件常是旧版完整模板，整份搬会破坏
   现行模板；模板已有的名字一律不搬（模板权威）。正文用到但模板已有的本地定义会**显式
   记录**在「模板已提供、未重复搬」里。
3. **`\renewcommand` 的判据**（不许静默）：覆盖模板命令且正文用到 → **不搬**（模板优先），
   但必须进日志 +「⚠ 注意事项」；模板里**没有**对应命令 → 降为 `\newcommand` 迁入，
   并警告「请核对语义」。`\renewenvironment` 属版式重定义，**不搬**、不在日志里逐条报。
4. **本地非标准命名的旧模板 `.sty`**（第 130 条补丁）：`\usepackage{名字}` 若作者源目录
   同目录存在 `名字.sty` → **不迁该包行**（顶部加载交给现行模板），文件按本地支撑文件差集
   搬定义；日志写「包行不迁、按差集搬定义」。否则产物会 `\usepackage` 一个没被复制进产物
   目录的文件（`File … not found`）。注意这与 `\input{command.sty}` 的本地文件支持是两条路
   （后者由 `expand_inputs` 展开），互不回退。
5. **`\newcounter{X}` 依赖迁移**（第 130 条补丁）：只有「正文或已迁移定义体用到」才搬——
   用到形态 = `\theX` / `\value{X}` / `\setcounter{X}` / `\stepcounter{X}` / `\addtocounter{X}` /
   `\refstepcounter{X}` / `\usecounter{X}` / `\arabic{X}` / `\roman{X}` / `\Roman{X}` /
   `\alph{X}` / `\Alph{X}` / `\fnsymbol{X}`（计数器名不是控制序列，不能按 `\名字` 判）。
   计数器统一排在定义块之前；`\newcounter{X}[Y]` 的父计数器 Y 一起搬且排在 X 之前。
   模板已有同名计数器（显式 `\newcounter` 或 `\newtheorem` 隐式创建）不重复搬。
6. **正文区定义**：不迁移（正文整段照搬已保留），build 日志打一行
   「正文里出现 N 处自定义定义（保留原样，建议移到导言区）」；不做猜测性搬动。
7. **定义缺失预检**：编译前把正文 `\begin{env}` 集合与「模板 + 迁移结果 + 正文自身定义」
   核对，缺失 → 日志醒目警告 + 「⚠ 注意事项」，**不阻断出稿**（退出码不变）。宏级缺失
   不做预检（离线看不到宏包内部定义，误报会更多）；环境级用内置
   `BASE_ENVIRONMENTS`（内核/模板类/常用宏包环境）压误报。
8. **Word 通道零变化**：上述逻辑只在 LaTeX 通道；Word 通道的产物、日志、退出码不变。

**边界（如实说清）**：纯离线判据不解析 TeX 发行版/宏包自带的定义，`BASE_ENVIRONMENTS`
之外的宏包自带环境仍可能被预检报成缺失（只是提示）；`\def` / `\let` / `\newif` /
`\renewenvironment` 不搬；`\newcounter` 只在「正文或已迁定义体用到」时搬（没用到不搬；
跨「作者前言 ↔ 本地 sty」两组的计数器依赖不闭合）；本地非标准 `.sty` 的包行不迁后，
该文件里 `\def` 类定义与它自己 `\usepackage` 的宏包不跟着走；本地支撑文件只认**同目录**；
没有 `\begin{document}` 的纯正文稿无法区分前言/正文，沿用第 120 条旧行为（整份按正文
可达收宏）。

**回归**：`tests/test_latex_preamble.py` ⑦ 组（合成夹具：本地 sty 五类 + 普通宏、撞名
跳过与 `\renewcommand` 警告、共享计数器闭包、本地 cls + 嵌套 def、正文区提示、缺失预检、
`\newcounter` 迁移与顺序、本地非标准 `.sty` 包行不迁、Word 通道零变化）。真实稿只读抽查
（2023 年第 4 期 04 稿的 `\definitionnamechange` + `\newcounter{userinput}`、2024 年第 2 期
05 稿的 `\cp` / `\cpp`、2024 年第 4 期 04 稿的 `SLCN2.sty`）已验证迁入；公开仓不记作者
姓名与稿件片段。
