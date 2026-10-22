# gui_walkthrough —— macOS GUI「真点走查」最小工具（B-191 T1）

**定位**：替换 archived `gui_fast_test.py`（B-85，初排转换批处理专用驱动）在工具清单里的角色。
本工具补的是 B-188/B-190 两次手工走查暴露出来的那条链：**按名字定位 → 点/填/滚/截图/断言 →
固定命名 evidence pack**；不碰 App 内部实现、不做录制回放框架。

**为什么不是第 9 件 active 工具**：它以「替换 archived `gui_fast_test`」立项（B-191 §1.5），
投用后 active 口径仍为 **8/8**；没有新增第 9 件。

## 1. 调研结论（先调研，再决定 复用 / 改造 / 新造）

| 候选 | 本机可用性（2026-10-23 实测） | 结论 |
|---|---|---|
| 本项目 `notes/drafts/archive/gui_fast_test.py`（B-85） | 在库（archived） | **只复用配方**（AppleScript 直路径 + pbcopy/⌘A/⌘V 填 CJK），不拷代码；它是「初排转换」批处理，不是通用走查 |
| 本项目 B-188 报告 §3 AX recipes | 在库 | **复用**：侧栏 `set selected`、`AXPress`、scrollbar `AXValue`、窗口截图 |
| 本项目 B-190 临时 `axdump/axtool.swift`（验证者手搓） | `/tmp/b190/evidence/gui/tools/`（不落仓） | **改造**：合并成一个 `axhelper.swift`（JSON 输出 + 多命中拒绝 + `--expect-name` 漂移复核 + alert 候选报告），实现与用法全部重写 |
| [pyobjc](https://pypi.org/project/pyobjc/) | **未安装**（`import ApplicationServices` 失败）；装 = 联网 | 不用：违反「不联网安装依赖」 |
| [atomac](https://github.com/dgvncsz0f/pyatom) / [pyatomac](https://pypi.org/project/pyatomac/2.0.0/) | **未安装**；且依赖 pyobjc、久未维护 | 不用 |
| [cliclick](https://github.com/BlueM/cliclick) | **未安装**（brew 装 = 联网）；且是**坐标**点击 | 不用：坐标点击正是要避免的（B-190 走查全程用 AX，不用坐标） |
| [AXorcist](https://github.com/openclaw/AXorcist) | Swift 包，需 SwiftPM 拉依赖 = 联网 | 不用 |
| SikuliX（图像识别） | 过重（B-188 §8.2 已判 no-go） | 不用 |

**结论 = 新造（薄封装）**，底层只用**系统自带**能力，零第三方运行时依赖、零联网：
`swiftc`（Xcode CLT，`run_swift_tests.sh` 已在用）把 `axhelper.swift` 编译到 `$TMPDIR` 缓存（**产物不入库**），
Python 侧只用 stdlib + `osascript` / `pbcopy` / `screencapture` / `defaults` / `git`。
不整段复制任何第三方代码；本目录是唯一新增实现。

## 2. 环境要求

- macOS + Xcode Command Line Tools（`swiftc`；首次运行自动编译 helper，约 2 s，缓存在
  `$TMPDIR/gui-walkthrough-helper/axhelper-<源码 sha 前 16 位>`）。
- **辅助功能（Accessibility）授权**：AX 请求来自**实际调用进程**（终端 / 你的 shell / 上层 App）。
  缺权时工具**不会假 PASS**：打印「系统设置 → 隐私与安全性 → 辅助功能」可行动提示并 **exit 3**。
- 屏幕**不要休眠**：休眠/锁屏会让部分 App 的 AX 树退化成「AXApplication 自身嵌套」（B-191 实测），
  工具会明确报这条（先 `caffeinate -u -t 2` 唤醒再跑）。
- 真点走查的 App：默认 `dist/编辑部工具箱.app`（可用 `--app` / `--pid` 换）。

## 3. 子命令

| 子命令 | 作用 |
|---|---|
| `select` | 定位并 `selected`/`focused`（默认「任一生效即 PASS」；`--focus`/`--selected` 可指定；`--fallback-press`） |
| `click` | `AXPress`；按下前用 `--expect-name` 复核名字（防「快照→点击」漂移） |
| `field` | 写文本：CJK 走 `pbcopy` + ⌘A/⌘V；ASCII 先试 AX 写入 + readback，不符自动回退粘贴；最终 readback 不符 → exit 1 |
| `scroll` | scrollbar `AXValue`（`--value 0..1` 或 `--delta`），readback 校验 |
| `shot` | 窗口矩形（AX `position/size`）截图；`--full` 整屏；落 evidence `.png` |
| `assert-text` | AX 文本 `--contains` / `--equals`，轮询到 `--timeout`；失败 exit 1 |
| `assert-alert` | 按 **alert 标题 + 按钮名** 断言/点击；**禁索引**（`--index` 直接 exit 2，理由见 §4） |
| `dump` | AX 树（限深），写 `.log` + `.json`（退化树 → exit 4 + 休眠提示） |
| `fixture` | `/tmp` 隔离夹具：bare clone notes → clone → `defaults write … notesRepoPath <clone>`；`--teardown` 恢复 + 清理 + 校验 |

定位参数（`select`/`click`/`field`/`scroll`/`shot`/`assert-text`/`dump` 共用）：

```text
--path 0.6.3                      # AX 数字路径（与名称定位二选一）
--role AXButton --name 推送…       # 角色 + 名字（title/desc/value/help 任一，默认子串；--exact 精确）
--subtree                         # 名字也匹配子树（行/单元格文字在子节点）→ 自动只取最深命中
--within 0.6                      # 限定子树
--nth 2                           # 多命中时显式取第 N 个（默认：多命中一律拒绝，绝不猜）
```

## 4. alert 为什么只按名字（B-190 教训）

B-190 真点走查里 **2 次按索引点错 alert 按钮**：同一个 App 的确认框有 2 个或 3 个按钮时，
「确认」按钮的索引会漂移（`0.6.2/0.6.3` vs `0.6.3/0.6.4`）。本工具的规则写死在代码里：

- `assert-alert --title <标题> --button <按钮名> [--click]`：**标题必填**；`--click` 必须同时给按钮名；
- 给了 `--index` → 用法错误 exit 2（提示里写死 B-190 那两次误点）；
- 按钮名默认**精确匹配**；0 命中 / 多命中 → 拒绝执行并列出实际按钮名（绝不猜）；
- 点完默认校验 alert 是否消失（`--no-verify-dismiss` 可关），防「点空」；
- 按钮路径必须是该 alert 的**后代绝对路径**（`button_path_sane`，B-191 实测过 helper 返回相对路径的坑）。

## 5. evidence pack（固定命名 + 一个 VERDICT 汇总）

- 目录：`--evidence-dir`（默认 `$TMPDIR/gui-walkthrough-evidence`），跨命令共享 `state.json` 续编号。
  **只接受绝对路径**（相对路径 / `.` → exit 2，防止把证据写进 cwd 污染工作树 —— V1 F2）；建议显式传绝对路径。
- 每步固定命名：`NN-<cmd>-<slug>.log`（人读）+ `NN-<cmd>-<slug>.json`（机器读）/ `.png`（截图）；
  `NN` 两位零填充，`<slug>` 由 `--slug` 或目标名生成（CJK 保留）。
- 每步都重写 `VERDICT-SUMMARY.txt` + `VERDICT-SUMMARY.json`：`VERDICT: PASS/FAIL/SKIP`、每步状态/耗时/证据文件名。
- `--json` 输出机器可读结果；`--reset-evidence` 把旧证据归档到 `archive-<ts>/` 再从 01 编号（不删证据）。

一次典型走查（B-191 实测：7 步 / 约 3 s / 8 个证据文件；对照 B-190 verifier GUI 段 135 步 / 61 文件 / 2 次误点）。

> **click 前等 UI settle（≈1.5–2 s 或等刷新完成）**：V1 dogfood 实测页面异步刷新会让 click 目标漂移
> （工具按 drift 保护**拒绝点击**、不会误点）；本工具**不做自动重试**（控风险），序列里自己 `sleep 2` 或先 `assert-text` 等刷新落地。

```bash
GW="python3 tools/gui_walkthrough/gui_walkthrough.py"
$GW fixture --root /tmp/gw-run                      # 1) /tmp 夹具 + defaults 指向测试 clone
open -n dist/编辑部工具箱.app
$GW select  --role AXRow --name 待处理 --subtree     # 2) 侧栏切页（按名字，不按索引）
$GW assert-text --contains "库 sha"                  # 3) 页签名断言
sleep 2                                              # 4a) 等 UI settle（≈1.5–2s 或等刷新完成）再点，页面异步刷新会让目标漂移
$GW click   --role AXButton --name "提交…" --exact    # 4) 打开二次确认
$GW assert-alert --title "手动提交库文件？" --button 取消 --click   # 5) 按名字点「取消」并校验消失
$GW shot --slug 待处理页                             # 6) 窗口截图
pkill -x Toolbox
$GW fixture --teardown --root /tmp/gw-run           # 7) 恢复 defaults + 清理 + 校验真实库未动
cat "$TMPDIR/gui-walkthrough-evidence/VERDICT-SUMMARY.txt"
```

## 6. fixture 的安全护栏（不可绕过）

1. 根只准在 `/tmp` 或 `$TMPDIR` 下；与真实 notes 仓**互相嵌套**直接拒绝（`guard_fixture_root`）。
2. 流程：`git clone --bare <真实 notes> <root>/remote.git` → `git clone <root>/remote.git <root>/notes`
   → `defaults write <domain> <key> <root>/notes` → readback。
3. **先写状态再执行**：`state.json`（含原值 `prior`、真实库 sha、真实 notes HEAD + status 摘要）先落盘，
   中途崩了也能 `--teardown` 收拾。
4. `--teardown`：恢复 defaults（有原值就还原，无原值就 `defaults delete`）→ 校验 readback →
   校验**真实 notes HEAD/status 摘要/真实库 sha 与建夹具前逐项相同** → `rm -rf` /tmp 夹具。
   任何一项不符 → exit 1 并提示「立刻人工核查」。
5. 默认 domain/key 是 App 的 `cn.edu.sysu.logic.toolbox` / `notesRepoPath`（B-190 `gui/20`、`gui/21` 同流程）；
   测完必须 `--teardown`（B-190 的收尾口径：key 不存在 + /tmp 副本删净）。

## 7. 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 / PASS（含显式 `--optional` 的 SKIP、`fixture --dry-run`） |
| 1 | 断言失败 / 目标不存在 / 动作未生效（FAIL） |
| 2 | 用法错误（argparse、定位参数矛盾、`assert-alert --index`） |
| 3 | 缺「辅助功能」权限（打印可行动提示，不静默假 PASS） |
| 4 | 环境问题（App 未启动 / 缺 swiftc / git、defaults 失败 / fixture 状态缺失 / AX 树退化） |

## 8. `--self-test`（hermetic，30 例）

```bash
python3 tools/gui_walkthrough/gui_walkthrough.py --self-test   # 末行 N/N PASS，rc 0
```

**hermetic 的机器证明**：跑用例前把 `subprocess.run` 替换成「一调用就抛」的哨兵 ——
任何漏网的真实子进程（= 起 App / 读真实库 / 联网 / 写真实 App defaults 的必经之路）都会让自测红。
用例只写 `tempfile`；helper 编译用注入的假 runner（不调 `swiftc`）。
覆盖：证据命名/slug、helper 输出解析、定位 argv 与护栏、alert 解析（B-190 真实形态 / 多命中拒绝 /
标题不匹配 / 禁索引）、文本断言语义、VERDICT 汇总、证据续编号与 `--reset` 归档、fixture 计划与根护栏、
teardown 恢复计划、计划执行失败即停、权限提示与退出码、截图 argv 与临时命名、AppleScript 构造、
helper 编译缓存、helper 源码契约、dump 渲染、快照比对、退化树识别、`--subtree` 最深命中、pid 精确匹配、evidence-dir 只收绝对路径。

## 9. 已知限制（刻意不做的）

- **不做**通用 GUI 录制/回放、图像识别、坐标点击、CI 里的 GUI 测试（真点仍要有屏幕 + 辅助功能授权）。
- 文本比对按 AX 文本前 **2000 字符**（helper 截断）；超长正文断言请用更短的锚点。
- 截图 `-R` 用 AX frame 的**点**坐标（Retina 下像素图更大）；`-R` 在屏幕休眠时会失败（会提示先唤醒）。
- 一个进程一个 evidence pack 状态；并行跑两套走查请给不同 `--evidence-dir`。

## 10. 文件布局

| 文件 | 内容 |
|---|---|
| `gui_walkthrough.py` | 命令行入口（子命令/参数、evidence 记录、退出码） |
| `gwcore.py` | 常量、错误类型、纯函数（定位 argv、alert 解析、命名、fixture 计划、快照比对）、evidence pack |
| `gwdriver.py` | `Ctx`（pid/AX/AppleScript/pbcopy）+ 9 个子命令实现 |
| `gwselftest.py` | hermetic 自测（30 例；哨兵证明零子进程） |
| `axhelper.swift` | AX 原语（JSON 输出）：trusted/dump/resolve/press/select/setvalue/scroll/alerts/window/texts |

自测口径：`python3 tools/gui_walkthrough/gui_walkthrough.py --self-test`（`tools/INVENTORY.md` 登记同一行；30/30 PASS）。
上游相关工具：`tools/run_swift_tests.sh`（Swift 测试台，headless）、`tools/inventory_check.py`（清单看门狗）。
