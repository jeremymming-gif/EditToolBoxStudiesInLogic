# 模板合规规则表（TEMPLATE_RULES）

> 工具：`Engine/templatecheck.py`（**report-only MVP**，B-002）。本表是判据的**单一出处**：
> 改判据先改这里 → 改 `Engine/templatecheck.py` 的 `RULES` → 补
> `Engine/tests/test_proofcheck.py` 的「(e) 模板合规」段（正例 + 逐条反例）。

## 一、入口

```bash
cd Engine
PYTHONPATH=.:vendor python3 templatecheck.py --input "<产品目录|初排稿.tex>" \
    [--out <报告.md>] [--template <名称|.sty 路径>]
PYTHONPATH=.:vendor python3 templatecheck.py --self-test   # 8 条规则正例 + 3 条反例
PYTHONPATH=.:vendor python3 templatecheck.py --version
```

- `--input` 产品目录：优先同名 `<目录名>.tex`；只有一个 `.tex` 时用它；多个且无法判断 → **rc2**（不替用户猜）。
- `--out` 缺省写 `<产品目录>/<stem>_模板合规.md`；给了就必须是 `.md`。
- `--template` 缺省按初排稿的 `\usepackage` 自动识别（`SLCN-MIX`/`SLCN-MIX4`/`SLCN`/`SLEN`），也可给名字或 `.sty` 路径。
- **退出码**：`0` = 无 FAIL（PASS 或 PASS+SKIP）；`1` = ≥1 FAIL；`2` = 用法/输入/写报告错误。
- **stdout 最后一行**：`完成。模板合规：PASS p / FAIL f / SKIP s（报告：path）`。
- **只读**：没有 `--fix`；不写 `.tex/.sty/.bib`；输入与产品目录字节零变化（测试断言）。

## 二、8 条规则（T1–T8）

| 编号 | 名称 | 判据 | 级别 |
|---|---|---|---|
| T1 | 文档结构 | `\begin{document}` / `\end{document}` 成对；document 环境里除注释外有正文 | 缺/空 → FAIL |
| T2 | 元数据 | 题名/第一作者/第一单位/摘要必填且非空、非占位符（`xxx`、`【未识别…】`、`（请填写…）`）；第二作者及以后为 `null` 或被注释 → 允许 | 缺/占位 → FAIL |
| T3 | 摘要与关键词 | 摘要非空（长度只报字数 INFO，无阈值）；中文关键词 3–8 个（`；`/`;`/`，` 分隔）；英文稿关键词不适用 | 越界/空 → FAIL；EN → SKIP |
| T4 | 参考文献 | `.bib` 字段按 entry_type 补必填子集（`article`: author/title/journal/year；`book`: (author\|editor)/title/publisher/year；`misc`: note/howpublished/url 至少一；未知类型 → FAIL）；`\cite` 键闭合；重复 key → FAIL；未引用条目 → INFO；无 `.bib` 且无 cite → SKIP | 见左 |
| T5 | 图表题 | 每个 `figure` 环境有非空 `\caption`（被注释算没有），`\begin`/`\end` 闭合；verbatim 内不算 | 缺 → FAIL |
| T6 | 模板 `\usepackage` | 初排稿的模板包名与 `--template` 精确一致（`SLCN-MIX` ≠ `SLCN-MIX4`） | 缺失/不一致 → FAIL |
| T7 | 模板 sha | 产品目录 `.sty` 与 canonical（public 仓 `编辑笔记/模板文件/`）sha256 一致 | 缺/不一致 → FAIL |
| T8 | 图片素材 | 每个 `\includegraphics` 的文件存在（相对产品目录 / `\graphicspath`）；`\IfFileExists` 兜底视为通过；注释不算 | 缺 → FAIL |

## 三、实测与降级

**4 处降级**（手册/经验书无出处，或 MVP 阶段不可行）：

1. **摘要长度**：手册写「200–300 字」但各期实测跨度大 → 只报字数 **INFO**，不做阈值判定。
2. **关键词 3–8 个**：**task79-B-plan 内部口径（手册/经验书无出处）**；D-08 决定保留 FAIL，
   并在本表与报告「依据」行注明来源，便于日后有据可依时复核。
3. **图题**：只查非空与闭合，不查「图 X.Y」编号、图题位置（图下）、字号 —— 需要版式判定，MVP 不做。
4. **参考文献**：只查字段子集，不查著录格式（GB/T 7714 的标点、大小写、作者缩写）。

**真实抽样**（2026-10-27，只读，报告在 `/tmp/b002-20261027/`）：

- `2022_02_Xiaoliu_Du`（Word/中文）PASS 5 / FAIL 1 / SKIP 2：T4 的 `@misc` 缺 note/howpublished/url。
- `2023_04_Hao_Wu`（LaTeX/英文）PASS 4 / FAIL 2 / SKIP 2：T2 未识别占位符、T4 缺 year。
- `2023_01_Yanjin_Chen`（Word/中文）PASS 7 / FAIL 1 / SKIP 0：T4 缺 publisher / journal。
- `2024_02_JiaHong_Guo`（Word/中文）PASS 6 / FAIL 2 / SKIP 0：T2 `xxx` 占位符、T5 6 个 figure 无 caption。

**canonical 模板 sha 源 = public 仓**：`EditToolBoxStudiesInLogic/编辑笔记/模板文件/`（经
`slcn/templates.py` 的 `TPL_DIR` / `AVAILABLE_STY` 定位；`--template <path>` 可覆盖）。稿件仓里的
同名副本**不作 canonical**：两仓已分叉（B-189），产品目录 `.sty` 与 public canonical 不一致时
报告会明确写「产品目录 .sty ≠ public canonical」并 FAIL，不静默放过。

**自检**：`--self-test` 用内建夹具跑 8 条规则正例（CN 全 PASS、EN 关键词 SKIP）+ 3 条反例
（T1 缺 `\begin{document}`、T4 缺字段、T8 缺图），不依赖真实产品，CI 可跑。

## 四、怎么加一条规则

1. 判据先写进本表（编号 / 来源 / 降级说明）——**没有出处就写「内部口径」**，别伪装成手册规范。
2. 在 `Engine/templatecheck.py` 的 `RULES` 加 `Rule(id, title, basis)`，实现
   `check_xxx(ctx) -> RuleResult`；判定一律用 `ctx.masked`（`slcn.proofcheck.raw_mask` 之后），
   避免注释 / verbatim 误报。
3. 挂进 `CHECKS` 元组（顺序 = 报告顺序）；新规则默认 `PASS`，只有证据充分才 `add_fail`。
4. 在 `Engine/tests/test_proofcheck.py` 的「(e) 模板合规」段加**正例 + 逐条反例**；
   反例必须断言具体失败理由（`f.issue`），禁止只写「不崩」。
5. `--self-test` 至少补该规则的正例与一条反例；跑 `tests/test_proofcheck.py` 与
   `tools/gen_docs.py`（AUTO 数字唯一出处）。
