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
<仓库>/Engine/                 ← 唯一一份逻辑（convert.py + checkbib.py + mergepdf.py
                                                 + cleantemp.py + compileall.py + proofcheck.py + slcn/）
<仓库>/Engine/engine_main.py   ← 打包用：把**八个** CLI 冻成**一个** exe 的分发入口（第 89 条）
<仓库>/Engine/vendor/          ← 随包依赖（pypdf / pypinyin / lxml）
<仓库>/sources-mac/                ← mac 外壳（SwiftUI）
<仓库>/sources-win/           ← Windows 外壳（Tkinter，见 §七）
```

**调用方式**：外壳起一个**子进程**，用**绝对路径**调 `convert.py` / `checkbib.py` / `mergepdf.py`。
发布形态下这八个入口被冻成一个 exe，外壳改调 `SLCNEngine.exe <入口> …`（见 §八·打包）。
⚠️ `engine_main.py: ENTRIES` 与外壳的 `sources-win/engine.py: ENTRIES` **必须逐项一致**：
漏一个，那个外壳页在用户机器上就是「❌ 不认识的入口」退出 2（2026-10-17 实测坏了 5 页）；
配对守卫在 `sources-win/tests/test_bridge.py` 与 `Engine/tests/test_platform.py` (g) 组。
引擎**不做源文件自动发现**（`--source` 必填），也**不假设 CWD**（内部一律用绝对路径）。

```bash
# 外壳必须做的最小环境准备（顺序无所谓）：
PYTHONPATH=<…>/Engine/vendor      # vendor 依赖
PYTHONUNBUFFERED=1                # ⚠️ 不设的话日志会按 8KB 块缓冲，界面看着像卡死
SLCN_LLM_KEY=…                    # 密钥**只走环境变量**，绝不进 argv（argv 在 ps 里谁都能看）
PYTHONIOENCODING=utf-8            # Windows 尤其要设；引擎内部也会自己 reconfigure（见 §七）
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
⚠️ 具体到 `--compile`：**⑨ 编译未通过 = 失败 = 退出码 2**（`.tex` 产物照样留着，
消息走 stderr；stdout 的**最后一行仍是契约行** `完成。产物目录：…`）。
这条路 2026-10-17 之前是 `return 0`：界面上显示 ✅ 完成，而 PDF 其实没编出来
（案例第 107 条）。

**stdout 的最后一行**：`完成。产物目录：<out>/<作者名>`（其余日志都在它之前）。

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
- 逻辑在 `slcn/pdfmerge.py`。**两个外壳都已改调它**（第 99 条）：
  mac 侧原来的 `PDFMerger.swift` / `ExtraPages.swift` / `EditorsPage.swift` **已删除**，
  `sources-mac/Features/MergeFeature.swift` 只剩「拼参数 + 转发 + 解析 `--detect` 的预览」。
  切换前两条路（Swift + PDFKit 那条与引擎这条）的输出已用真实成品期目录**逐条比对过**
  （176 页、102 条大纲、层级/名字/页码全同，见 `tests/test_pdfmerge.py` 的 (f) 组）。
- **`--detect`**：只探测不合并，输出「会插哪一份编委页/封底页、扉页页数」的 JSON ——
  界面运行前的预览用它，**别在界面里自己扫文件**（否则会出现"预览说找到 A、合并时插了 B"）。

## 五、入口 4–8：期目录级的动作、一校核查、检查tex信息与生成目录（第 91、92、94 条）

```bash
python3 cleantemp.py   --folder "<期目录>" [--dry-run]
python3 compileall.py  --folder "<期目录>" [--only 01,03] [--dry-run]
python3 proofcheck.py  --tex "<初排稿.tex>" [--lang zh|en] [--out <报告.md>] [--fix]
python3 infocheck.py   --folder "<期目录>" [--out-dir <目录>]
python3 infocheck.py   --folder "<期目录>" --preview   # 界面用：检查结果 JSON（不写任何文件）
python3 toc.py         --report "<信息检查.txt>" [--template <期目录>] [--settings <目录设置.json>]
python3 toc.py         --report "<信息检查.txt>" --preview     # 界面用：篇目清单 + 模板来源（JSON）
```

| 入口 | 干什么 | 退出码 | 最后一行（外壳靠它拿结果） |
|---|---|---|---|
| `cleantemp.py` | 递归删 LaTeX 编译中间文件（`.aux .bbl .bcf .blg .out .run.xml .synctex.gz` + `.DS_Store`）；**不删** `.tex/.pdf/.sty/.bib/.log`；**且永不进入 `original/`**（作者来稿目录，红线只读 —— 作者交 LaTeX 稿时那里真可能有同名 `.bbl`/`.out`，删掉就是丢稿） | `0` 成功；`2` 目录不存在 | `完成。已删除 N 个临时文件（目录：…）` |
| `compileall.py` | 对每个 `NN 作者/` 子目录跑 `xelatex → biber -l=zh_pinyin → xelatex → xelatex`；跳过 `000 目录`；某篇失败**只停那一篇**并回显 `.log` 里的 `!` 摘要 | `0` 全成功；`1` 有失败；`2` 用法/目录错误 | `完成。编译 N 篇（成功 X / 失败 Y）（目录：…）` |
| `proofcheck.py` | **一校核查**：按《排校手册》《流程化经验书》逐条核查初排稿（26 条规则，见 `docs/PROOF_RULES.md`）；**默认只报不改** | `0` 无命中；`1` 有命中（正常）；`2` 用法/文件错误 | `完成。一校核查：命中 N 处（可自动改 M 处）（报告：…）` |
| `infocheck.py` | **检查tex信息**：汇总各篇 tex 里的卷期年/页码/题目/作者/字号/行距 + PDF 实际页数；`--preview` 只输出检查结果 JSON（篇目 + `inconsistencies`），**不落任何文件** | `0` 成功（或 preview 成功）；`2` 用法/目录错误 | `完成。检查 N 篇（有 tex X / 有 pdf Y；不一致 Z 项）（报告：…）` |
| `toc.py` | **生成目录**：读 `infocheck` 的报告产出中英文目录 .tex（模板继承上一期成品）；`--preview` 只输出篇目清单与模板来源的 JSON（给界面用，不写文件）。⚠️ **先校验"这确实是一份检查报告"**：解析出 0 篇就退出 2、**一个文件都不写** —— 从前喂错文件（如某篇 `.tex`）会静默产出两份「第 ? 卷，第 ? 期」的空目录并退出 0，而产物落在**报告所在目录**（真跑时就是作者目录） | `0` 两份都生成（或 preview 成功）；`1` 只生成一份；`2` 用法/文件错误、或报告不可用 | `完成。生成目录 N 份（<目录>）` |

**产物**：
- `cleantemp` / `compileall`：原地操作（前者删文件、后者生成 `.pdf` 与 `.log`）。
- `proofcheck`：同目录写 `<stem>_一校核查.md`（报告）；
  加 `--fix` 时**另写** `<stem>_一校核查后.tex`——**输入文件一个字节都不动**。
- `infocheck`：期目录根下写 `信息检查<时间戳>.txt`（**CRLF**，与原版/mac 版逐字节一致）
  与 `.json`（结构化，供 toc 与界面用）；可用 `--out-dir` 挪走。
- `toc`：报告同目录写 `Chinese Content_<时间戳>.tex` 与 `contents_<时间戳>.tex`
  （**先英文后中文**，与原版 `run_tab5` 一致）。
  本期设置（栏目/主持人/标题断行上限）读同目录的 `目录设置.json`，缺失字段逐项回落默认值。

**给外壳的注意**：`compileall` 是长任务（每篇几十秒），必须用第 82 条的实时日志；
`proofcheck` 的 `1` 表示「有命中」而不是失败，外壳不要把它显示成错误；
`toc` 的篇目预览必须调 `--preview`（**别在界面里自己解析报告**），否则会出现
「清单里勾了 3 篇、生成时只认 2 篇」这种对不上的情况。

**mac 界面的切换进度（第 95 条起，按用户定的顺序，第 99 条完成）**：**九页全部改调引擎** ——
初排转换 / 文献回读校验 / 一校核查 / 生成目录（95）/ 检查tex信息（96）/
删除临时文件 + 编译全部（98）/ **合并文件（99）**。
`sources-mac/` 里已经**没有任何文档逻辑**：界面只做「取值 → 拼参数 → 显示日志/表格」。
切一个删一份 Swift 逻辑（`ContentFeature.swift` + `TocTemplate.swift` 已在第 95 条删除；
`InfoCheckFeature.swift` 在第 96 条瘦身成"只拼参数 + 解产物"）。

界面侧的通用约定（切过两页后总结出来的）：
· **表格/清单数据一律问引擎**（`--preview`），界面不自己解析 —— 否则表格与报告会对不上；
· 只有**展示用的聚合**（如不一致项提示串）可以留在界面，判据必须还在引擎里；
· 界面把设置写进引擎要读的那个文件（如 `目录设置.json`），**不要用命令行传设置**，避免两个真相。

## 六、开发用入口（**不随 App 分发**）

`docx_audit.py`（全库体例普查，**冻结基线**：改解析器必须 `--out-dir /tmp/…` 或 `SLCN_AUDIT_OUT`）、
`corpus.py`（全库回归，约 10 分钟）、`ablation.py` / `ablation_corpus.py`（规则消融）。
这些只在开发机跑，**不要**打进 Release 资产。

## 七、平台矩阵（第 86 条：引擎平台中立化的口径）

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

## 八、外壳的义务（写下来，省得两个外壳各做一半）

1. **只调 CLI，不复制逻辑**：引擎只有一份；规则改动只在 `Engine/` 里改。
2. **UTF-8 管道**：设 `PYTHONIOENCODING=utf-8`，按 UTF-8 解码子进程输出（否则中文乱码）。
3. **实时读**：`PYTHONUNBUFFERED=1` + 逐块读 + 自动滚到底（第 82 条）。
4. **密钥只走环境变量**，绝不拼进 argv，也不写进日志（界面只显示前 4 位）。
5. **不要解析未写进本文件的输出**：白名单是 §二 的那几类；其余原样显示。
6. **退出码只看 0 / 非 0**；错误消息在 stderr，要一起显示。
7. **绝对路径**：源文件、输出目录、`--returned` 等一律传绝对路径；不依赖 CWD。
8. **别改产物**：产物目录里的文件由引擎负责，外壳只读、只打开（唯一例外是用户显式点的「删除临时文件」）。

## 九、版本、打包与升级

- **版本号只有一处**：`Engine/slcn/__init__.py` 的 `__version__`。
  `convert.py --version` / `checkbib.py --version` 打印它；mac 的 `build.sh` **现取**它写进
  `Info.plist`（从前写死 `1.0`，双平台之后必然漂移）；Windows 打包脚本同样从它取。
- **Windows 冻结分发的约定**（第 89 条）：引擎用 `engine_main.py` 冻成**一个**
  `SLCNEngine.exe`，外壳调 `SLCNEngine.exe convert.py --source …`（**第一个参数就是入口名**）：

  ```bat
  pyinstaller --onefile --console --name SLCNEngine engine_main.py
  ```

  支持八个入口 + `--version`/`--help`；不认识的入口打印用法并退出 2。
  `--hidden-import` 与打包自检都按 `ENTRIES` 逐项走（`sources-win/build-win.ps1` ②④），
  自检会**逐入口**跑 `--help` 并做一次最小转换 —— 只查 `--version` 查不出漏冻的入口。
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

## 十、兼容性纪律（改引擎时自查）

1. **只增不改**：新增参数必须带默认值；不改既有参数的默认行为。
2. **stdout 只增不改**：外壳认的那几类行（§二）不许改语义；新信息一律**新增**行。
3. **产物路径不许改**：`<out>/<作者名>/…` 的布局与文件名是契约的一部分。
   （历史上 `reference.bib.ai前`、`_文献核实.md`、`_回读校验报告.md` 都是这么定下来的。）
4. **退出码语义不许改**（§二/§三/§五）。
5. 改完跑：`tests/test_platform.py`（平台）、`tests/test_docs_consistency.py`（文档），
   以及 `README.md` 固定验收链条里的那六项。
