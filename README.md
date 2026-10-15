# 编辑部工具箱（SLCN Editorial Toolbox）

《逻辑学研究》编辑部自用的稿件处理工具箱。把作者提交的 **Word / LaTeX 文稿**转成符合本刊
排版规范的**可编译初排稿**，并处理参考文献核实、整期 PDF 合并等编辑环节。

- **一个引擎，两个外壳**：所有逻辑都在 Python 引擎（`Engine/`）里，界面只负责取值、拼参数、显示日志。
- **macOS 外壳**：SwiftUI（`sources-mac/`），构建出 `dist/编辑部工具箱.app`。
- **Windows 外壳**：Tkinter（`sources-win/`），打成两个 exe，用户机器**不需要装 Python**。
- 参考文献环节可选接大模型（**用你自己的 API key**，密钥只存本机、只走环境变量，绝不进命令行）。

> 这是编辑部的工作工具，不是通用软件：默认模板、字段规范、判据都按本刊体例写死，
> 换刊使用需要改 `Engine/slcn/` 下的规则与 `编辑笔记/模板文件/` 下的模板。

---

## 快速开始

### 依赖

| 需要什么 | 说明 |
|---|---|
| Python ≥ 3.11 | 引擎用；按下面的命令把三个包装进 `Engine/vendor/` |
| TeX（`xelatex` + `biber`） | TeX Live / MacTeX / MiKTeX 均可，需在 PATH 里 |
| LibreOffice | **可选**：只有「导出原稿 PDF」（供矢量图形截图）用得到 |
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

下载来的 `.app` 若被 Gatekeeper 拦（提示「已损坏」），见仓库根 `RELEASING.md` 的说明
（本应用未做 Apple 签名与公证）。

### Windows

```powershell
cd sources-win
pwsh -ExecutionPolicy Bypass -File build-win.ps1
# 产出：build\dist\SLCNEngine.exe（引擎，三个 CLI 冻成一个）与 build\dist\编辑部工具箱.exe（界面）
```

### 命令行（引擎单独用也成）

```bash
cd Engine
export PYTHONPATH="$PWD/vendor"

python3 convert.py  --source "作者稿件.docx" --out "输出目录" --compile --verify-refs
python3 checkbib.py --returned "编辑改回的.bib" --original "原件.bib" --tex "正文.tex"
python3 mergepdf.py --folder "2026年第5期(90)" --editors-page --nest-outlines
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
├── engine_main.py       打包入口：把上面三个 CLI 冻成一个 exe
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
