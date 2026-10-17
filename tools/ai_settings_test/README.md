# AI 设置页升级验证（headless，不开 GUI、不发网络请求）

验证 `sources-mac/` 里「设置页升级」与后续修复（两个旋钮改为转换时**现取**）
这几处改动（**编译的是生产源码本身，不是复制品**）：

| 生产文件 | 被验证的东西 |
|---|---|
| `sources-mac/Core/ViewModels.swift` | `AIProviderPreset`（预设 → 地址/模型）、`AIThinkingMode`（三态 → `SLCN_LLM_THINKING`）、`AIProtocol`（chat/responses，第 69 条）、`AIMaxTokens`（输出上限解析）、**`AIAdvancedKnobs`**（第 70 条：检索工具名 / responses 路径的解析与「用不了就不传」）、`AIEngineKnobs`（含 `apiProtocol` / `webSearch` / `usesWebSearch` / `searchTool` / `responsesPath`）、`AppSettings` 的**六个**持久化字段 + `loadEngineKnobs` |
| `sources-mac/Features/ConvertFeature.swift` | `engineEnvironment`（给子进程的环境变量增量）、`knobWarnings`（非法上限 / 非法高级旋钮不许静默）、`effectiveKnobs`（旋钮的「现取 or 回落」）、`toggled` / `verifyRefsLocked`（开关联动的**纯函数**）、`arguments`（开关 → `--verify-refs` / `--no-ai-merge` / `--llm-protocol` / `--llm-web-search` / `--llm-search-tool` / `--llm-responses-path` 的映射；2026-10-15 phase 2b 起刻意不是 private） |
| `sources-mac/Views/ConvertTab.swift` | `run()` 现取 `settings.aiEngineKnobs`（含通道/检索/工具名/路径）并以 `run(options:knobs:log:)` 传给 `ConvertFeature`；复选框绑定走 `ConvertFeature.toggled(...)` / `verifyRefsLocked(...)`（源码级接线检查，见 T-g / T-i / T-l） |
| `sources-mac/Views/SettingsTab.swift` | `AIConnectionTester` 的清单拼装（①②③④ 的 `Reply → 文本` 映射）、**⑤ 检索探针**（`searchProbeArguments` / `parseSearchProbe` / `searchProbeLine` 三条纯函数；**真跑**见 `probe_e2e/`）、密钥脱敏，以及「转换时用当前值」的文案与 T-g 实测一致；第 70 条的两个高级输入框（`检索工具名` / `响应路径`，留空即引擎默认）见 T-l 的源码级检查 |
| `sources-mac/Features/CheckBibFeature.swift` | 「文献回读校验」的参数拼装（**phase 2c 起真的编译它**）：体检 / 并回 / 列表三种 argv 的映射、粘贴模式的报告落点 `pasteReportPath` —— 都是纯函数，可直接断言（j1–j9） |

> **T-i（2026-10-15 phase 2b）** 另做**源码级**检查（不编译 `CheckBibView.swift`，因为它要
> SwiftUI 视图上下文）：`sources-mac/Features/CheckBibFeature.swift` 里「并回」拼的是
> `--returned … --merge-into <目标>`、并回前明确写「将覆盖 <target>，原文件备份为
> `<target>.ai前`」、没填原件时的提示语、退出码 0（未改动）不说成成功；
> `sources-mac/Views/CheckBibView.swift` 里有「并回」「重新编译该篇」按钮与它们的
> 显示/禁用条件。
>
> **j（2026-10-15 phase 2c）** 覆盖**粘贴模式**：体检 / 并回 / 预览三种 argv 的
> `--returned-stdin` 映射（文件模式逐字符不变）、`pasteReportPath` 的两种落点、
> `Support.swift` 的 stdin 管道接线（`run` / `capture` 各一处调用同一个 `feedStdin`）、
> 以及 `CheckBibView.swift` 的粘贴界面接线（TextEditor / 剪贴板 / 模式切换 / 预览世代号 /
> 空文本禁用 / 正文自动填充）。**仍然不启动任何子进程**。

## 为什么放 `tools/`

`build.sh` 按 `sources-mac/**/*.swift` **通配**编译，往 `sources-mac/` 里放带
`main` 的文件会把 App 构建搞崩。`tools/` 不在通配范围内（与 `tools/ai_store_test/` 同理）。

## 跑法

```bash
cd sources-mac
swiftc -o /tmp/ai_settings_test_harness \
  Core/ViewModels.swift Core/Support.swift Core/Log.swift Core/EditorsPage.swift \
  Features/InfoCheckFeature.swift Features/ConvertFeature.swift Features/CheckBibFeature.swift \
  Views/SettingsTab.swift \
  ../tools/ai_settings_test/main.swift
/tmp/ai_settings_test_harness        # 期望：SUMMARY: 207 PASS / 0 FAIL
```

`SettingsTab.swift` 里 `import SwiftUI`，但只是在**编译**时链接系统框架 ——
这个 harness 是命令行可执行文件，不启动 App、不建窗口。

## 覆盖

- **(a) 预设**：5 个预设逐个核对 base/model 逐字一致；`自定义` 原样保留用户输入；
  预设结构上碰不到密钥（`applied(base:model:)`）。
- **(b) 思考模式三态**：`follow → unset`（**该环境变量不存在**）、`off → "0"`、`on → "1"`，
  既有纯映射，也有 `engineEnvironment` 出来的真实字典。
- **(c) 输出上限**：`2048 → "2048"`、`` → unset、`0 → "0"`、`abc → 拒绝 + 中文提示`；
  边界还有 `-5` / `2048.0` / `1e3` / 溢出 / `" 2048 "` / `02048` / `+64`。
- **(d) ai.json**：新字段存/读、权限仍是 0600/0700、三态 round-trip、
  **旧文件（只有 base/key/model）→ 默认值 follow/2048**、在旧文件上保存会补上新键、
  留空存成空串且读回仍是「不设置」、类型坏掉的文件不崩、
  非法上限**拒绝保存**（文件一个字节没动 + `aiStatus` 有中文提示）、
  `loadEngineKnobs` 的三条路径（已保存 / 文件不存在 / 旧文件）。
- **(e) 环境字典**：已配置 / 未勾开关 / 密钥留空 / vendor 在不在 —— 逐键核对
  `SLCN_LLM_*` 与 `PYTHONPATH`；非法上限不发变量但 `knobWarnings` 有一行说明；
  端到端 `ai.json → loadEngineKnobs → engineEnvironment`。
- **(g) 两个旋钮「现取」**（本轮修复）：盘上存 follow/2048、界面上改成 on/512 且
  **不保存** → `effectiveKnobs(live)` 用现取值、`engineEnvironment` 发出
  `SLCN_LLM_THINKING="1"`/`SLCN_LLM_MAX_TOKENS="512"`；反证「只读已保存」那条路此刻发的是
  follow/2048。`knobs == nil` 回落读已保存的 `ai.json`（与 `loadEngineKnobs` 逐字段一致，
  目录不存在 → 默认值且不建目录）。另加源码级接线检查（`ConvertTab.run()` 传
  `settings.aiEngineKnobs`；`run()` 用 `effectiveKnobs(liveKnobs)`）与**文案一致性**检查：
  设置页里不允许再出现「旋钮读已保存的值 / 要点保存才生效」这类已失效说法，
  且「转换时用上面填的当前值」这句必须与 g 的实测结果同时成立。
- **(f) 体检清单**：喂固定的 HTTP 响应给纯函数，验证 ① 不给模型列表 = 「—」不是失败、
  ② 400 打印 HTTP 状态 + 原始错误体、③ 三个分支（推理 token >0 / =0 / 无 usage）、
  ④ 支持/不支持（原始错误），以及 ④ 的 prompt **含 `json` 字样**、
  抬头只出现 `AppSettings.mask` 前 4 位、响应体回显密钥时被抹成 `****`（先脱敏再截断）。
- **(k) 通道 + 联网检索 + ⑤ 检索探针**（2026-10-15 第 69 条）：默认 = chat + 检索关
  （`usesWebSearch == false`，chat 通道即使勾了检索也不算，因为 chat 的 `tools` 是
  function calling 不是检索）；`summary` 两种取值都要把通道与「有没有检索」写出来；
  **argv 默认一个参数都不加**（与从前逐字节相同）、非默认才加 `--llm-protocol responses` /
  `--llm-web-search`；`engineEnvironment` 不因新开关改变（检索走 argv）；
  `ai.json` 的 `protocol`/`webSearch` 存读、旧文件与坏值回落默认、`loadEngineKnobs` 也读它们；
  `clearAI()` 回默认且**不动盘**（第 62 条不被破坏）；⑤ 拼的是 `-m slcn.llm --probe --json`、
  **密钥走子进程环境变量不进 argv**、走与转换同一条引擎定位路径；`parseSearchProbe` 的
  JSON → 结构与「垃圾输入 → nil，绝不瞎猜」；`searchProbeLine` 的六种判词
  （支持 / 支持但通道是 chat / 不支持 / 不支持但用户勾了检索 / 其他错误 / 测不了）。
- **(i) 开关联动 + argv 映射**（2026-10-15 phase 2b）：`toggled(.useAI, to: true)` 自动勾上
  任务书、`toggled(.useAI, to: false)` **不改任务书的值**（含反例：本来没勾就仍然没勾）、
  AI 勾着时把任务书改 false 会被拉回 true、`verifyRefsLocked` = `useAI`；
  `arguments` 的四种组合：没勾 → 两个参数都不出现、只勾任务书 → 只有 `--verify-refs`、
  勾 AI → 有 `--verify-refs`、勾 AI + 保留原 bib → 两个都在、AI 关着时不给 `--no-ai-merge`、
  密钥仍不进 argv。加上 `ConvertTab` / `CheckBibFeature` / `CheckBibView` 的源码级检查
  （联动逻辑只有一份实现；并回的目标与危险动作声明；两个按钮的显示条件）。
- **(l) 两个「未来兼容」旋钮**（2026-10-15 第 70 条）：`AIAdvancedKnobs` 的解析口径
  （留空 = `unset`；`web_search_preview` 这类合法值 = `value`；含空白/花括号/`://` 的
  = `invalid` 且 `hint()` 有中文说明）；`envValue` 的「unset/invalid → nil = **不传**」；
  **argv 默认一个参数都不加**（两栏留空时命令行与从前逐字节相同）、填了才加
  `--llm-search-tool` / `--llm-responses-path`、填错也**不传**；`engineEnvironment`
  里没有这两个变量（走 argv）；`knobWarnings` 对两栏非法值各出一行「不会传」而合法/留空
  时没有多余警告；`ai.json` 的 `searchTool`/`responsesPath` 存读（trim 后落盘）、
  旧文件与坏类型回落空串、`loadEngineKnobs` 也读它们、`clearAI()` 回空串且**不动盘**；
  `summary` 只在填了时追加「工具名 …，路径 …」（留空时与 (k3) 那份**逐字相同**）；
  **⑤ 探针也必须带上这两个旋钮**（`searchProbeKnobArguments` 的空/填了/填错三种取值，
  以及「`runSearchProbe` 真的把它们拼进 argv、`test()` 把界面旋钮传下去」的源码级检查）
  ——否则体检测的是默认值、转换打的是另一个名字，那是最坏的一种假证据；
  以及 `SettingsTab` / `ConvertTab` 的源码级接线（两栏真的绑到 `aiSearchTool` /
  `aiResponsesPath`，转换页现取现传）。

## `probe_e2e/`（⑤ 检索探针的**真跑**验证）

`main.swift` 里所有断言都不启动子进程、不发请求 —— 所以 ⑤「真的把引擎跑起来了吗」
不能在那里证明。`probe_e2e/main.swift` 单独做这件事：它用**同一份生产源码**
（`AIConnectionTester.runSearchProbe`）对着 `tools/mock_llm_server.py`
这个本地假端点跑三种剧本，打印三种判词：

| mock 模式 | 期望判词 |
|---|---|
| `responses_search` | ✓ 支持（收到 `web_search_call`） |
| `responses_ignore_search` | ✗ 不支持 —— **DeepSeek flash 的真实行为**：收到 `tools` 也静默忽略 |
| `responses_reject_search` | ✗ 不支持（HTTP 400 + 端点原话） |

**第 70 条又加了一段**（同一个可执行文件）：用 `AIEngineKnobs(searchTool: "web_search_preview")`
再跑一次 `responses_search` 剧本，期望 `verdict=supported`、`endpoint` 是配置拼出来的地址；
然后 `GET /__stats` 核对 `n_tools_requested` 里出现的就是 `web_search_preview`
（实测输出：`["web_search_preview": 1, "web_search": 3]`，前三个是三种剧本用默认名字留下的）。
这一段证明「界面填的工具名 → ⑤ 的 argv → 引擎 → 端点」**整条链路真的通了**——
只断言「拼出来的 argv 长什么样」是不够的。

跑法与编译命令写在 `probe_e2e/main.swift` 的文件头（先起 mock，再 `swiftc` 那 9 个文件）。
它**只打 127.0.0.1**，不出网、不花额度、不碰用户目录（只在自己进程里设 `convertToolPath`）。

## `preedit_diffs/`

改这三个生产文件**之前**存下的原始副本与改动后的 `diff -u`（证据用，可随时删除）。
每个 `.diff` 的首行写着基准副本的路径（`/tmp/preedit/*.orig`）：

```bash
head -2 preedit_diffs/ViewModels.swift.diff
```

用「改动前的副本」做基准，所以它只包含**本次改动**，不含仓库里其它会话对别的文件的改动
（`git diff` 做不到这一点：这个仓库的工作区里还有别人未提交的改动）。
基准副本的时间戳是 2026-10-15 00:20，所以现在的三份 `.diff` 覆盖的是**第 69 条 + 第 70 条
两轮**的改动（第 70 条重新生成过一次）。
⚠️ 第 70 条还改了第四个文件 `sources-mac/Views/ConvertTab.swift`（现取两个高级旋钮），
但 `/tmp/preedit/` 里**没有**它的基准副本，所以没有对应的 `.diff` ——它的验证只有
T-l 的源码级接线断言（`l11-converttab-live`）。

## 没被这个 harness 证明的事（重要）

- **URLSession 那条路一次都没跑过**：`AIConnectionTester.test` / `send` 需要真实端点，
  本 harness 一次 HTTP 请求都没发。被验证的只是 `Reply → 文本` 的纯映射，
  所以「请求真的发得出去、超时真的按 15s 生效」只有代码级证据。
- `d6-dirty` 固定的是**回落路径**（`knobs == nil`，即没有界面状态的调用方）：
  `loadEngineKnobs` 只认盘上的值，界面内存里改了它不受影响。GUI 那条路不走回落 ——
  `ConvertTab.run()` 现取现传（T-g 验证的是这一条），所以「改了没保存 → 转换用旧值」
  这个旧行为**已经不存在**。
- **`ConvertFeature.run()` 本身没有真的跑过**：它要定位并启动 Python 子进程
  （`Shell.run`），harness 不启动任何子进程。被验证的是它调用的那两段纯逻辑
  （`effectiveKnobs` → `engineEnvironment`）加上源码级接线检查；
  「子进程真的收到 `SLCN_LLM_*`」只有代码级证据。
- 「设置页按钮 → `saveAI()` / `loadEngineKnobs()`」这一跳是代码级证据，
  没有真的在 GUI 里点过一次（本任务不允许运行 App）。
- **「并回」「重新编译该篇」两个按钮没有真的跑过**：它们要启动 Python / xelatex 子进程
  （`Shell.run` / `Shell.capture`），本 harness 不启动任何子进程；被验证的只有
  「拼出来的 argv 是什么」与「界面在什么条件下显示/禁用按钮」（源码级）。
  并回**引擎侧**的行为由 `tests/test_merge_into.py`（含 ⑧/⑨ 粘贴与列表模式）与
  `tests/test_checkbib.py` 的 ⑥ 组覆盖；**粘贴模式的 Swift 侧接线**另有命令行真跑证据
  （case 67：假 app 目录 + 真引擎调 `previewPasted` / `run(.paste:)` / `mergeBack(.paste:)`）。
- **粘贴界面本身没有在真实窗口里点过**：`TextEditor`、剪贴板按钮、分段控件、
  `onChange` 的触发时机都只有源码级证据（j17–j21）与「同一份 Feature 代码在命令行真跑」
  的旁证；SwiftUI 的渲染与交互没有机器验证。
- `.atomic` 写盘瞬间的临时文件权限、并发保存的竞态，都不在这里。
- **⑤ 的真跑只覆盖「引擎 + 假端点」这一层**（见 `probe_e2e/`）：`AIConnectionTester.test`
  里 ⑤ 与 ①②③④ 的串接（同一后台队列、同一 `completion` 回收）仍是源码级证据；
  SwiftUI 的 Picker / Toggle 与「测试连接」按钮没有在真实窗口里点过。
- **真端点行为已用真 API key 实测（2026-10-17 P0）**：DeepSeek 的 `deepseek-v4-pro`
  在 Responses 上会真检索（`/responses` 与 `/v1/responses` 都返回 `web_search_call`），
  `deepseek-flash` 仍静默忽略 `web_search`（HTTP 200、无证据）。mock 的
  `responses_ignore_search` 继续覆盖「flash 型静默忽略 → 探针必须报不支持」这一格。
- **第 70 条的两个高级旋钮也没有对着真端点跑过**（本 harness 一次网络请求都不发）：
  被验证的是「解析口径 / argv 映射 / 存读 / summary 文案」这些纯逻辑，
  以及引擎侧 `tests/test_llm_knobs.py` 的本地假端点证据（含 `web_search_preview` 的
  端到端）。**将来真有服务商实现内置检索时，工具名与路径该填什么仍以该服务商的文档为准**
  ——这两栏只解决「不必改代码」，不解决「名字/路径猜得对不对」。
