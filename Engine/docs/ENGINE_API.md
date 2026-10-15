# 引擎接口契约（ENGINE_API）

**这份文件是什么**：`Engine/`（Python，唯一一份逻辑）与**外壳**（mac 的 SwiftUI、Windows 的
Tkinter，以及将来任何前端）之间的**契约**。外壳只做三件事：找路径、拼参数、显示日志——
**逻辑一行都不许复制进外壳**。

> ⚠️ **改引擎的 CLI / 退出码 / 产物路径 / 关键输出行 → 必须同时改这份文件与两个外壳。**
> 反之：新增参数**一律要带默认值**，老外壳不传也能跑（这条是硬要求，见 `build_docx(metadata_path=None)`
> 与 `--llm-search-tool` 的先例）。

---

## 一、引擎在哪、怎么被调用

```text
<仓库>/Engine/                 ← 唯一一份逻辑（convert.py + checkbib.py + mergepdf.py + slcn/）
<仓库>/Engine/engine_main.py   ← 打包用：把三个 CLI 冻成**一个** exe 的分发入口（第 89 条）
<仓库>/Engine/vendor/          ← 随包依赖（pypdf / pypinyin / lxml）
<仓库>/sources-mac/                ← mac 外壳（SwiftUI）
<仓库>/sources-win/           ← Windows 外壳（Tkinter，见 §七）
```

**调用方式**：外壳起一个**子进程**，用**绝对路径**调 `convert.py` / `checkbib.py` / `mergepdf.py`。
发布形态下这三个入口被冻成一个 exe，外壳改调 `SLCNEngine.exe <入口> …`（见 §八·打包）。
引擎**不做源文件自动发现**（`--source` 必填），也**不假设 CWD**（内部一律用绝对路径）。

```bash
# 外壳必须做的最小环境准备（顺序无所谓）：
PYTHONPATH=<…>/Engine/vendor      # vendor 依赖
PYTHONUNBUFFERED=1                # ⚠️ 不设的话日志会按 8KB 块缓冲，界面看着像卡死
SLCN_LLM_KEY=…                    # 密钥**只走环境变量**，绝不进 argv（argv 在 ps 里谁都能看）
PYTHONIOENCODING=utf-8            # Windows 尤其要设；引擎内部也会自己 reconfigure（见 §六）
```

- **解释器**：Python ≥ 3.11（开发机用 3.14）。Windows 打包时用 PyInstaller 把解释器一起打进去。
- **标准输出**：外壳**逐行读、实时显示**（第 82 条：块缓冲会让「联网核实」看起来像卡死）。
  stdout 与 stderr 都要收（引擎的致命错误走 stderr）。

## 二、入口 1：`convert.py`（初排转换）

```bash
python3 convert.py --source <稿件.docx|.tex> --out <输出根目录> [--compile] [--clean] …
```

| 参数 | 说明 |
|---|---|
| `--source` | **必填**。`.docx` / `.doc` / `.tex` |
| `--out` | **必填**。产物写到 `<out>/<作者名>/` |
| `--metadata` | LaTeX 多源合并的元数据源；**Word 源**时指到 `.docx` 会被当「英文元数据第二源」（第 76 条） |
| `--refs` / `--bib` / `--name` / `--author` / `--lang` / `--sty` / `--skeleton` | 见 `convert.py --help` |
| `--vol --issue --year --pages --received --editor --grants` | 编辑侧出版信息，不填就留模板默认值 |
| `--compile` `--clean` `--source-pdf` | 编译出 PDF / 清中间文件 / 导出原稿 PDF（供矢量形状截图） |
| `--verify-refs` `--no-ai-merge` | 出《文献核实任务书》/ 只出任务书不并 bib（第 64 条） |
| `--llm-base --llm-model --llm-protocol --llm-web-search --llm-search-tool --llm-responses-path` | AI 通道（密钥走环境变量） |
| `--version` | 打印 `SLCN 引擎 <版本> ｜ <系统>，Python <版本>`（外壳的「关于」直接用） |

**退出码**：`0` = 成功；`2` = 用法/IO/致命错误（`die()`，消息在 **stderr**）。
**没有 1**：中途某一步失败也算失败，一律 2 —— 外壳判「成功」只看 `0`。

**stdout 的约定**（外壳可以只认这几类，**其余原样显示**）：

| 形态 | 含义 |
|---|---|
| 行首 `①②…⑫` | 粗粒度阶段（解析 / 引用 / 模板 / 正文 / 预校正 / 写文件 / 归档 / 任务书 / 编译 / 原稿 PDF / 清理） |
| 行首三个空格 | 该阶段内的细节（外壳可缩进显示） |
| 行首 `   [i/N] <key>：正在核实…` | 第 82 条的**逐条进度**（最慢的一步在等端点时唯一的活动迹象） |
| 行首 `❌` | 致命错误（同时走 stderr） |
| 行首 `⚠` | 警告；收尾还会汇总在「⚠ 注意事项：」一节 |
| `完成。产物目录：<绝对路径>` | **最后一行**（外壳用它做「打开产物目录」按钮） |

**产物**（`<out>/<Author_Name>/`）：

```text
<Author_Name>.tex            初排稿
<Author_Name>.pdf            --compile 才有
reference.bib                参考文献（勾了 AI 并回时**已被机器改过**）
reference.bib.ai前           本次合并**之前**的那一份（回滚用；重跑会覆盖）
SLCN-MIX.sty / SLEN.sty      与 --sty 一致
original/                    源稿归档副本（只读副本，不是原件）
<Name>_文献核实.md           任务书（含「六、合并记录」）
<Name>_回读校验报告.md       并入后自检（结论一行同时打在 stdout）
<Name>_参考文献核对.txt      离线核对（缺哪些字段 / 引用对不对得上）
<Name>.log                   xelatex 日志（**故意不删**）
```

## 三、入口 2：`checkbib.py`（文献回读校验 / 并回）

```bash
python3 checkbib.py --returned <交回.bib> [--original <原件.bib>] [--tex <正文.tex>] [--out <报告.md>]
python3 checkbib.py --returned <交回.bib> --merge-into <产物 reference.bib>
pbpaste | python3 checkbib.py --returned-stdin --merge-into <产物 reference.bib>   # 粘贴模式
```

**退出码**（三种模式含义不同，口径与判据见 `docs/RULES.md` §四.6）：
体检 `0` 无问题 / `1` 有问题 / `2` 用法或 IO 错误；
并回 `0` **未改动** / `1` 已并入 ≥1 条 / `2` 用法或 IO 错误；
列表（`--list-keys`）`0` 打印了 / `2` 出错。`--version` 同 `convert.py`。

## 四、入口 3：`mergepdf.py`（合并一期的 PDF，第 88 条）

```bash
python3 mergepdf.py --folder "<期目录>" [--out <合并.pdf>] [--nest-outlines] [--editors-page] [--back-cover]
```

- **`--folder` 指的是「期目录」**（里面有 `扉页.pdf`、`000 …/`、`01 作者/` …），
  **不是**单篇稿件的目录。默认写成 `<期目录>/合并<时间戳>.pdf`（时间戳格式与 mac 外壳一致）。
- **合并顺序**：扉页 → 编委页（可选）→ `000…/Chinese Content.pdf` → `000…/contents.pdf` →
  各篇文章（按 `NN` 序号）→ 封底（可选）。**缺文件不会中断**，只在 stdout 打 `⚠ …没找到`。
- `--nest-outlines`：各篇 PDF 自带的**小节书签**挂到该篇文章的书签之下；
  **默认平铺在顶层**（与原版 PyPDF2 的行为一致，mac 外壳默认也是这个）。
- **编委页 / 封底只并页面、不导入它们自带的大纲**：实测编委页文件里留着一个陈旧书签叫
  「Chinese Content」，导进来会出现两个同名书签指向不同页。识别**只看文件名**
  （这些页是印刷厂曲线化过的，没有文字层）：编委页 `编委页.pdf` → `cneditor*` → `扉页2.pdf` →
  名字含「编委」；封底 `cover+bottom*` → 名字同时含 `cover` 与 `bottom`。
  工具箱自己生成的 `合并*.pdf` **一律排除**，不会被当成候选。
- **退出码**：`0` 合并成功（可能带 `⚠` 警告）；`2` 期目录不存在 / 写不出去。
- **最后一行**：`完成。合并文件：<绝对路径>（共 N 页）` —— 外壳靠它拿产物路径与页数。
- 逻辑在 `slcn/pdfmerge.py`。**mac 外壳目前仍走 Swift + PDFKit 那条**
  （`sources-mac/Core/PDFMerger.swift` + `sources-mac/Features/MergeFeature.swift`），
  两条路的输出已用真实成品期目录**逐条比对过**（176 页、102 条大纲、层级/名字/页码全同，
  见 `tests/test_pdfmerge.py` 的 (f) 组）；将来 mac 切到引擎这份时，以引擎为准。

## 五、开发用入口（**不随 App 分发**）

`docx_audit.py`（全库体例普查，**冻结基线**：改解析器必须 `--out-dir /tmp/…` 或 `SLCN_AUDIT_OUT`）、
`corpus.py`（全库回归，约 10 分钟）、`ablation.py` / `ablation_corpus.py`（规则消融）。
这些只在开发机跑，**不要**打进 Release 资产。

## 六、平台矩阵（第 86 条：引擎平台中立化的口径）

| 需要什么 | macOS | Windows | Linux |
|---|---|---|---|
| Python | ≥ 3.11（开发机 3.14） | 同（PyInstaller 打进去） | 同 |
| TeX | TeX Live / MacTeX（`xelatex` + `biber` 在 PATH） | TeX Live / MiKTeX（同上） | TeX Live |
| LibreOffice | 可选（只有「导出原稿 PDF」用） | 可选 | 可选 |
| `vendor/` | mac 轮子（`lxml` 是编译件） | **必须是 Windows 轮子**（打包时按平台装） | Linux 轮子 |

**引擎已经做掉的平台事**（`slcn/platform.py`）：

1. **UTF-8 标准输出**：`convert.py` / `checkbib.py` 进 `main()` 第一件事就 `utf8_stdio()`。
   Windows 的 stdout 默认是控制台代码页（cp936），中文日志经管道会乱码、甚至
   `UnicodeEncodeError` **崩在转换中途**；macOS/Linux 上是空操作。
2. **LibreOffice 定位**：`find_soffice()` = `SLCN_SOFFICE` 环境变量 → `PATH` → **三平台**常见安装位置
   （Windows 的安装器**不**把 `soffice` 写进 PATH；macOS 的 `.app` 同理）。
3. **`file://` URI**：LibreOffice 的 `-env:UserInstallation=` 只认 URI，
   Windows 盘符与**带空格的路径**都必须转义 —— 统一走 `platform.file_uri()`（`Path.as_uri()`）。
4. **文本 IO 一律显式 `encoding=`**：交付模块里**没有**一处「文本模式 `open()` 不写编码」
   （由 `tests/test_platform.py` 的静态守卫盯着）。这条是 Windows 上读中文源稿乱码的根因。

**已知的平台注意点**（还没做，写在这里免得踩）：

- **长路径**：Windows 默认 260 字符上限。稿件目录名很长（`2026年第5期(90)/07 Liangying_Zhao/original/…`）
  套上产物目录可能超限；真要踩到，用 `\\?\` 前缀或让用户在浅目录里出稿。
- **`tools/gen_docs.py` 写死了 mac 的 Python 路径**（`/opt/homebrew/bin/python3.14`）——
  它是**开发工具**，Windows 上要么改常量、要么用 `python`（不影响 App）。
- **字体**：模板 `fontset=windows` 指定 Times New Roman / KaiTi，Windows 上本来就是这套；
  macOS 用户需自备同名字体（既有行为，未变）。

## 七、外壳的义务（写下来，省得两个外壳各做一半）

1. **只调 CLI，不复制逻辑**：引擎只有一份；规则改动只在 `Engine/` 里改。
2. **UTF-8 管道**：设 `PYTHONIOENCODING=utf-8`，按 UTF-8 解码子进程输出（否则中文乱码）。
3. **实时读**：`PYTHONUNBUFFERED=1` + 逐块读 + 自动滚到底（第 82 条）。
4. **密钥只走环境变量**，绝不拼进 argv，也不写进日志（界面只显示前 4 位）。
5. **不要解析未写进本文件的输出**：白名单是 §二 的那几类；其余原样显示。
6. **退出码只看 0 / 非 0**；错误消息在 stderr，要一起显示。
7. **绝对路径**：源文件、输出目录、`--returned` 等一律传绝对路径；不依赖 CWD。
8. **别改产物**：产物目录里的文件由引擎负责，外壳只读、只打开（唯一例外是用户显式点的「删除临时文件」）。

## 八、版本、打包与升级

- **版本号只有一处**：`Engine/slcn/__init__.py` 的 `__version__`。
  `convert.py --version` / `checkbib.py --version` 打印它；mac 的 `build.sh` **现取**它写进
  `Info.plist`（从前写死 `1.0`，双平台之后必然漂移）；Windows 打包脚本同样从它取。
- **Windows 冻结分发的约定**（第 89 条）：引擎用 `engine_main.py` 冻成**一个**
  `SLCNEngine.exe`，外壳调 `SLCNEngine.exe convert.py --source …`（**第一个参数就是入口名**）：

  ```bat
  pyinstaller --onefile --console --name SLCNEngine engine_main.py
  ```

  支持三个入口 + `--version`/`--help`；不认识的入口打印用法并退出 2。
  这样用户机器上**不需要装 Python**；界面壳另冻一个 exe，两者放同一目录即可
  （外壳的 `sources-win/engine.py: frozen_engine_exe()` 会先找它）。
- **Release 资产命名建议**（同一 tag 下）：
  `SLCN-<版本>-macOS-arm64.dmg`、`SLCN-<版本>-Windows-x64-setup.exe`、
  `SLCN-<版本>-source.zip`、`latest.json`。
- **升级提示**：`latest.json` 里放 `{ "version": "1.2.0", "macos": "<url>", "windows": "<url>",
  "notes": "<一句话>" }`；外壳启动时比对 `__version__`，**只提示、不自动替换**
  （编辑部机器上自动替换会撞权限与杀软，得不偿失）。
- **签名**：macOS 侧**不买开发者账号**（2026-10-15 决定）⇒ 下载的 `.app` 会被 Gatekeeper 拦，
  所以 mac 资产只发源码/压缩包，正文里写一条 `xattr -dr com.apple.quarantine`；
  Windows 侧未签名 exe 会让 SmartScreen 提示「更多信息 → 仍要运行」。

## 九、兼容性纪律（改引擎时自查）

1. **只增不改**：新增参数必须带默认值；不改既有参数的默认行为。
2. **stdout 只增不改**：外壳认的那几类行（§二）不许改语义；新信息一律**新增**行。
3. **产物路径不许改**：`<out>/<作者名>/…` 的布局与文件名是契约的一部分。
   （历史上 `reference.bib.ai前`、`_文献核实.md`、`_回读校验报告.md` 都是这么定下来的。）
4. **退出码语义不许改**（§二/§三）。
5. 改完跑：`tests/test_platform.py`（平台）、`tests/test_docs_consistency.py`（文档），
   以及 `README.md` 固定验收链条里的那六项。
