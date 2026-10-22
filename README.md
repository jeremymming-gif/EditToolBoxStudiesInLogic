# 编辑部工具箱（SLCN Editorial Toolbox）

《逻辑学研究》编辑部自用的稿件处理工具箱。把作者提交的 **Word / LaTeX 文稿**转成符合本刊
排版规范的**可编译初排稿**，并处理参考文献核实、整期 PDF 合并等编辑环节。

> 🌐 **项目主页** <https://jeremymming-gif.github.io/EditToolBoxStudiesInLogic/> ｜ ⬇️ **下载** <https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest> ｜ 🪟 **Windows 安装** <https://jeremymming-gif.github.io/EditToolBoxStudiesInLogic/guide/install#windows>
>
> 主页含功能总览、Windows 优先的安装说明、快速上手与 FAQ；**Windows 版解压即用，不需要另装 Python**。

- **一个引擎，两个外壳**：所有逻辑都在 Python 引擎（`Engine/`）里，界面只负责取值、拼参数、显示日志。
- **macOS 外壳**：SwiftUI（`sources-mac/`），构建出 `dist/编辑部工具箱.app`。
- **Windows 外壳**：Tkinter（`sources-win/`），打成两个 exe，用户机器**不需要装 Python**；
  15 页（三组侧边栏 + 设置，与 macOS 版对等）：初排转换 / 文献回读校验 / 一校核查 / 合并文件 /
  检查tex信息 / 编译全部 / 生成目录 / 删除临时文件 / 本期出刊 / 库概况 / 查库 / 入藏 / 待裁决 /
  引用库设置 / 设置。
- **引用库维护闭环（B-188，macOS）**：「待裁决」页改名「待处理」= 字段冲突 + 库健康 + 已处理留痕；
  新增共享条目编辑器（表单 + 原始 BibTeX）、重复/近重复对照合并（逐字段选源，**不自动合并**）、
  缺字段归位批量预览。写库一律 dry-run 预览 → 用户确认 → `--expect-db-sha256` 校验 → 原子写 →
  成功后自动本地 git commit（只提交 `bib_db/bibliography.jsonl`，**绝不自动 push**）；「待处理」页
  版本区可手动提交/推送/回退到上一个提交（备份制已取消）。Windows 版本批未改，仍为只读「待裁决」。
- 参考文献环节可选接大模型（**用你自己的 API key**，密钥只存本机、只走环境变量，绝不进命令行）。
- **初排元数据人工填写（第 63b 批）**：题名/作者（1–5 位，可多单位）/摘要/关键词在初排页「高级选项 → 填写元数据…」
  一次填好，值**直接写入交付产物**；`.doc/.docx` 自动元数据识别已取消（`.tex` 元数据文件保留为高级兜底）。

> 这是编辑部的工作工具，不是通用软件：默认模板、字段规范、判据都按本刊体例写死，
> 换刊使用需要改 `Engine/slcn/` 下的规则与 `编辑笔记/模板文件/` 下的模板。

---

## 下载（编辑部同事看这里）

**<https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases>** ——
免安装包；**点进去最上面那个就是当前版本**（版本号也可以在 App 的「设置」页看到）：

| 平台 | 下载哪个 | 首次打开 |
|---|---|---|
| Windows | `EditToolBox-<版本>-Windows-x64.zip`（解压后**两个 exe 放在同一个文件夹**） | SmartScreen 蓝屏提示 →「更多信息」→「**仍要运行**」 |
| macOS（Apple 芯片） | `EditToolBox-<版本>-macOS-arm64.zip` | 双击提示「已损坏，无法打开」→ 右键点 App →「打开」→ 再点「打开」 |

每个版本另附 `FIRST-OPEN-Windows.txt` / `FIRST-OPEN-macOS.txt`（内容同上，照实说明），
以及 `latest.json`（App 启动时比对它，**只提示、不自动替换**）。
**两个平台都未做代码签名与公证**（编辑部 2026-10-16 定案），所以首次打开会被系统拦一下 ——
**不是文件坏了**。写给编辑的完整用法见 [`使用说明.md`](使用说明.md)。

---

## 快速开始

### 依赖

| 需要什么 | 说明 |
|---|---|
| Python ≥ 3.11 | 引擎用；按下面的命令把三个包装进 `Engine/vendor/` |
| TeX（`xelatex` + `biber`） | TeX Live / MacTeX / MiKTeX 均可，需在 PATH 里 |
| LibreOffice | **可选**：Word 稿的 MathType/OLE 公式图（WMF/EMF）转 PDF 插入成品、以及「导出原稿 PDF」（供矢量图形截图）用得到；**不随工具箱分发**，缺失时 fail-soft（公式图位置显示「公式图缺失」占位 + 日志 warning，**不阻断出稿**）。检查/安装见 [`使用说明.md`](使用说明.md) §一「Word 公式图（MathType）与 LibreOffice（可选依赖）」 |
| Xcode 命令行工具 | **可选**：只有构建 mac 外壳需要（`swiftc`） |

```bash
cd Engine
python3 -m pip install --target vendor -r requirements.txt
```

### macOS

```bash
./build.sh                      # 构建 dist/编辑部工具箱.app（把引擎与模板一起打进包内）
open dist/编辑部工具箱.app
```

下载来的 `.app` 若被 Gatekeeper 拦（提示「已损坏」），见上面的「下载」一节
（本应用未做 Apple 签名与公证；Release 里的 `FIRST-OPEN-macOS.txt` 也写了这两步）。

### Windows

```powershell
cd sources-win
pwsh -ExecutionPolicy Bypass -File build-win.ps1
# 产出：build\dist\SLCNEngine.exe（引擎，十个 CLI 冻成一个）与 build\dist\编辑部工具箱.exe（界面）
```

### 命令行（引擎单独用也成）

```bash
cd Engine
export PYTHONPATH="$PWD/vendor"

python3 convert.py  --source "作者稿件.docx" --out "输出目录" --compile --verify-refs
python3 checkbib.py --returned "编辑改回的.bib" --original "原件.bib" --tex "正文.tex"
python3 mergepdf.py --folder "2026年第5期(90)" --editors-page --nest-outlines
python3 proofcheck.py --tex "初排稿.tex"          # 一校核查（默认只报不改；--fix 另存新文件）
python3 compileall.py  --folder "2026年第5期(90)"  # 编译全部
python3 cleantemp.py   --folder "2026年第5期(90)" --dry-run
python3 infocheck.py   --folder "2026年第5期(90)"   # 检查tex信息（卷期年/页码/题目/作者 + PDF 页数）
python3 toc.py         --report "2026年第5期(90)/信息检查<时间戳>.txt"   # 生成中英文目录
python3 convert.py --version      # 引擎版本 + 运行环境
```

参数与退出码的**权威说明**见 [`Engine/docs/ENGINE_API.md`](Engine/docs/ENGINE_API.md)。

---

## 目录

```text
Engine/                  引擎（Python，唯一一份逻辑）
├── convert.py           初排转换（Word / LaTeX → 可编译初排稿）
├── checkbib.py          参考文献回读校验 / 把编辑改回的 .bib 并回产物
├── mergepdf.py          合并一期的 PDF（扉页、目录、各篇、封底 + 重建书签）
├── proofcheck.py        一校核查：按《排校手册》《流程化经验书》逐条核查初排稿
├── infocheck.py         检查 tex 信息（卷期年/页码/题目/作者 + PDF 实际页数）
├── toc.py               生成目录（中英文，模板继承上一期成品）
├── publish.py           本期出刊向导（检查信息 → 目录 → 合并 → 编译，一步跑完一期）
├── bibdb.py             编辑部引用库 CLI（两个平台的「引用库」5 页都调它）
├── compileall.py        编译一期里所有文章的 .tex（xelatex → biber → xelatex ×2）
├── cleantemp.py         删除编译临时文件（不删 .tex / .pdf / .log）
├── engine_main.py       打包入口：把上面十个 CLI 冻成一个 exe（`ENTRIES` 要与外壳一致）
├── slcn/                解析、规范、bib 判据、AI 任务书与合并等模块
├── docs/                GUIDE（用法）/ RULES（判据）/ ENGINE_API（对外壳的契约）
│                        （内部仓另有 cases/：案例法，含作者信息，不随公开仓发布）
└── tests/               套件（每个文件都是独立脚本，打印 PASS/FAIL）
sources-mac/                 macOS 外壳（SwiftUI）
sources-win/            Windows 外壳（Tkinter）+ 打包脚本 + 桥梁测试
编辑笔记/模板文件/         本刊排版模板（.sty 与骨架），引擎运行时必需
tools/                   改名/拆仓脚本、Swift 测试台、假端点等开发工具
build.sh                 macOS 构建
RELEASING.md             发布与拆仓手册（含签名、Release、拆仓流程）
THIRD_PARTY.md           第三方归属与许可证
```

## 测试

```bash
cd Engine && PYTHONPATH="$PWD/vendor" python3 tools/gen_docs.py   # 现算状态并刷新 README 的 AUTO 块
PYTHONPATH="$PWD/vendor" python3 tests/test_docs_consistency.py   # 文档看门狗
for f in tests/test_*.py; do python3 "$f" | tail -1; done          # 全部套件
PYTHONPATH="$PWD/Engine/vendor" python3 sources-win/tests/test_bridge.py
```

`Engine/README.md` 里的 **AUTO 块**是现算出来的（套件数、断言数、App 包内一致性等），
不要手抄数字——`tools/gen_docs.py --check` 会当场抓住过期。

## 许可与归属

- 本项目：**MIT**，见 [`LICENSE`](LICENSE)。
- 第三方组件（界面骨架 Editorialbox、随包依赖 pypdf / pypinyin / lxml 等）：见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。

## 注意

- **本仓是公开的工具仓**；作者来稿与编辑内部资料（排校手册等）**不在**这里。
- 参考文献的联网核实需要自备 API 配置；工具**不**内置任何密钥。
- 默认**不**自动替换已安装的应用（升级提示只提示，不动文件）。
