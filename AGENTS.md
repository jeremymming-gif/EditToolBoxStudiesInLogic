# AGENTS.md —— 工具箱开发者上手（公开仓/开发主场）

> 2026-10-16 起，**软件开发主场在这个仓**（`EditToolBoxStudiesInLogic`）。
> 作者来稿与人工成品在私有仓 `Studies_in_Logic_Database`（下称**稿件仓**），
> 案例法/交接说明/冻结基线在私有仓 `EditToolBox-notes`（下称**笔记仓**）。
> 三个仓的账目各自干净：这里只放**代码与可公开的文档**。

## 这是什么

《逻辑学研究》编辑部的稿件处理工具箱：把作者来稿（LaTeX / Word）转成符合本刊规范的
**可编译初排稿**，并处理编译、目录、合刊、清理、一校核查等环节。

**一个引擎，两个外壳**：所有文档逻辑都在 `Engine/`（Python，**唯一一份**）；
`sources-mac/`（SwiftUI）与 `sources-win/`（Tkinter）只做「取值 → 拼参数 → 显示日志」。
外壳里**不许**再写文档逻辑 —— 这是"两个平台算出来的结果一致"的唯一保证。

## 目录地图

```text
Engine/                 引擎（Python，唯一一份逻辑）
  ├── convert.py          初排转换          ├── proofcheck.py   一校核查（26 条规则）
  ├── checkbib.py         文献回读校验/并回  ├── infocheck.py    检查tex信息
  ├── mergepdf.py         合并一期 PDF      ├── toc.py          生成目录
  ├── compileall.py       编译全部          ├── cleantemp.py    删除临时文件
  ├── slcn/               模块（解析/判据/AI 任务书/PDF/路径锚点…）
  ├── docs/               GUIDE（用法）/ RULES（判据）/ ENGINE_API（对外壳的契约）
  │                       / PROOF_RULES（手册规范→规则，含未实现清单）
  ├── tests/              套件（每个文件独立可跑）
  └── out/                产物（gitignore）
sources-mac/            mac 界面（SwiftUI）
sources-win/            Windows 界面（Tkinter）+ 打包脚本 + 桥梁测试
tools/                  开发工具：Swift 测试台与总跑手、案例索引生成器、模板生成器…
编辑笔记/模板文件/        本刊排版模板（引擎运行时必需，随包发布）
build.sh                构建 mac 版 .app（把 Engine/ 与模板打包进 Resources）
```

## 怎么跑

```bash
# 引擎（依赖装到本地 vendor/；.app 与 Windows exe 已自带，跑 CLI 才需要）
cd Engine && python3 -m pip install --target vendor -r requirements.txt
export PYTHONPATH="$PWD/vendor"

python3 tools/gen_docs.py            # 现算并刷新 README 的 AUTO 块（数字唯一出处）
python3 tests/test_docs_consistency.py   # 文档看门狗（期望「失败 0 项」）
for f in tests/test_*.py; do python3 "$f" | tail -1; done   # 全部套件

# Swift 测试台（6 个，headless；一条命令跑完）
bash tools/run_swift_tests.sh        # 期望「全部测试台 0 FAIL」

# Windows 外壳桥梁测试（不需 Windows；真窗口那条要带 tkinter 的解释器）
PYTHONPATH=Engine/vendor python3 sources-win/tests/test_bridge.py
```

**重活验收需要稿件库**（全库普查 / 全库回归 / 代表稿）。引擎用 `Engine/slcn/paths.py`
统一解析：环境变量 `SLCN_MANUSCRIPTS` → 向上找 → **同级目录**
（默认布局：两个仓并排，无需任何配置）。找不到时那些工具会打印一行说明并**正常跳过** ——
CI 上就是这样，所以 CI 不该因为它们红。

## 红线（硬约束）

> 这些约束的**单点清单**（含产物契约、判据权威位置、三仓与环境变量）见 `Engine/docs/INVARIANTS.md` —— 本节只列最容易踩的几条，冲突时以那份清单与代码/测试为准。

1. **作者来稿只读**：`20xx年第N期(…)` 里的任何文件都不许改/删/移。要试新规则就写到 `out/` 或 `/tmp/`。
2. **冻结基线**：全库普查的结果钉在**笔记仓** `baselines/docx_audit.csv`（sha256 记在引擎 README 的 AUTO 块）。
   改解析器时不要覆盖它 —— 用 `python3 docx_audit.py --out-dir /tmp/audit_<标记>` 隔离，再与基线**逐行 diff**，
   **变坏迁移必须为 0**。
3. **提交要有节制**：开发提交落在本仓（公开），但**攒成有意义的批次**再提交（一轮工作一个提交），
   不要每改一处就提交一次。
4. **不要在公开仓里放**：作者姓名/稿件片段（案例法在笔记仓）、API 密钥、内部手册。

## 验收清单（改完必须给证据）

1. **隔离普查**：与笔记仓的冻结基线逐行 diff，**变坏迁移 = 0**。
2. **反例测试**：新规则必须配"本来不该命中"的反例；规则取舍先跑 `ablation_corpus.py` 消融
   （本项目最重要的纪律：直觉和单篇数据都会骗人）。
3. **代表稿**：Word 通道与 LaTeX 通道各至少一篇，人眼核版面与页数。
4. **全库回归**：`python3 corpus.py`（约 10 分钟），看逐篇明细而不是中位数。
5. **重建 App + 包内 cmp**：`./build.sh` 之后逐个比对包内引擎文件与仓库同名文件（"包内引擎过期"踩过）。
6. **文档一致性**：`python3 tests/test_docs_consistency.py`（含 `tools/gen_docs.py --check`）。
7. **Swift 改动**：`bash tools/run_swift_tests.sh`（6 个测试台 0 FAIL）。

> 只改文档的轮次：第 1–5 项可以不做，但必须说清"包内 README 已与仓库不同，下次 `./build.sh` 会同步"。

## 文档维护

1. **新发现写进案例法**（在**笔记仓** `cases/62+.md` 追加：根因 + 实测 + 验收），
   条目号**只增不改**（是全局引用锚点）；写完在**本仓**跑 `python3 tools/gen_case_index.py` 刷新索引。
2. **数字一律由 `tools/gen_docs.py` 注入** README 的 AUTO 块，**不要手抄**。
3. **文档里的路径引用必须真实存在**（相对本仓根 / `Engine/` 解析）—— 由看门狗执行；
   同一行含 `已删/已移除/历史/曾` 的属历史叙述，不存在是正确的。
4. **口径/判据**变 → `Engine/docs/RULES.md`；**用法/依赖/界面/限制**变 → `GUIDE.md`；
   **给外壳的接口**变 → `ENGINE_API.md`；**手册规范**变 → `PROOF_RULES.md`。别在文档里抄代码全文。
