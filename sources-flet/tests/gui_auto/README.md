# GUI 自动走查（真实 Windows 桌面）

把 P6 手动走查清单（`C:\slcn-work\p6-2026-11-01\prompts\p6-1-gui-walkthrough.md`）
逐项映射成自动检查。**默认 skip**：只有显式开关才启动真实窗口，CI / 无桌面环境不会误红。

## 运行

```powershell
# 在仓库根 D:\GitHub\EditToolBoxStudiesInLogic
py -3.14 -m pytest sources-flet/tests/gui_auto -v --run-gui

# 首次（或界面有意变化后）生成/更新视觉基线
py -3.14 -m pytest sources-flet/tests/gui_auto -v --run-gui --gui-update-baseline

# 或者用环境变量（等价）
$env:SLCN_GUI_AUTO="1"; py -3.14 -m pytest sources-flet/tests/gui_auto -v
```

可选参数：`--gui-evidence <目录>`（默认 `C:\slcn-work\p6-2026-11-01\evidence\p6-gui-auto`）、
`--gui-baseline <目录>`（默认 `<证据根>/baseline`）。

证据落在 `<证据根>/runs/<run-id>/`：`screens/`（每张截图 + 失败截图）、`sessions/<tag>/`
（markers / stdout / settings / failure.txt）、`diffs/`（基线不一致时的 diff 图与 json）、
`sessions/../<场景>.json` 与 `report.json`（机器可读结果），以及 `markers_<tag>.jsonl` 汇总。

## 覆盖矩阵（清单 → 自动检查）

| 手动清单 | 自动检查 | 类别 |
|---|---|---|
| 0 启动 | 真实进程启动、窗口标题含「编辑部工具箱」、frame/client 尺寸、DPI、engine_status 就绪、stdout 无 Traceback | B/C/E |
| 1a/1b/1c 入藏预览 | 引用库→入藏视图可渲染；**不做真实写库**（见「不能自动化」） | B/C |
| 2 待处理（C 编号/差异/建议/版本库卡） | 引用库→待处理视图可渲染；只读，不点推送/回退/采用 | B/C |
| 3 写库二次确认 | 只验证「写库说明/二次确认」文案在页面上可见；不点确认写入 | C |
| 4 排版一致性 | A：`section_title`/`option_row`/`nav_item`/`LogConsole` 的字重与字号；C：单篇两 flow、引用库 10 视图（4 组两级导航）截图 | A/C |
| 5 记录 | 脚本输出退出码 + `report.json`（含覆盖矩阵结果） | E |

## 场景

- **S1** 启动冒烟：标题/尺寸/DPI/引擎就绪/无异常。
- **S2** 六页导航：工作台/单篇稿件/整期装配/引用库/设置/帮助，逐页点击（真实鼠标）→ 等
  `nav_change` → 断言侧栏高亮位置 → 截图 diff。
- **S3** 深色切换：点侧栏 Switch，等 `theme_changed`/`titlebar` → 等 toast 消失（2.2s）→
  截图、取背景/侧栏颜色 → 切回浅色与初始图一致。
- **S4** 单篇两步骤：初排转换/一校核查两个 chip 真实点击 + 高亮位置 + 两份截图内容不同
  （固定 fixture 路径，避免截图含随机路径）；「去引用库 → 规范化」跳转按钮可点且落位。
- **S5** 引用库 10 视图 / 4 组：概况·查库（查阅）/ 规范化·入藏（采录）/ 待处理·编辑·查重合并·字段归位（治理）/
  库健康·设置（维护），等 `bib_view` 标记后截图，并在 chips 带内断言被点视图高亮（`accent_soft` 连通块=1）。
- **S10** 规范化宿主：`P2_BIB_VIEW=normalize` 启动即进「引用库 → 规范化」，面板标题与只读提示可见。
- **S9** 整期装配六页签 / 新 route / 快捷键 / 搜索（本批新增）：
  - `s9_issue_tabs`：页内顶部六个 chip 逐个真实点击 → 断言 `issue_activate`(view) 与
    `nav_change`(route)；六页签 = 生成目录/排序与改名/刷新页码/合并 PDF/清理临时文件/生成推送文案，
    对应底层步骤 infocheck|toc/order/refresh/merge/cleantemp/dirpush。
  - `s9_issue_routes`：用**正常入口** `P2_ISSUE_STEP=<step>` 启动直达整期新步骤
    （order/refresh/merge/cleantemp/dirpush），断言 `issue_step_hook` 来源为 env、
    `nav_change` 落在归一后的 route、且**不自动运行**；旧别名 compileall/produce 由 IA 归一。
  - `s9_issue_hotkeys`：Win32 注入 Ctrl+1–6 → 断言 `issue_hotkey`(slot, view) 按页签视觉顺序；
    全局 Ctrl+1–5 仍属 settings_shell（切一级页）；整期装配页内的 Ctrl+1–6 由 settings_shell 转交 IssuePage 切页签，非 issue 页仍走一级页导航。
  - `s9_search_navigation`：搜索索引含六页签条目；正常入口 Ctrl+K → ASCII 关键词 → 回车 →
    断言 `nav_change`(source=search) 落到 `issue:order` / `issue:refresh`。
  - 新场景均可单独触发：`pytest ... -v --run-gui -k s9`；坐标是 150% DPI 估位（见
    `harness.py` 的 `ISSUE_TAB_CHIPS`），Windows 复核以事件断言为准，偏差再校正常量。
- **S6** 运行/停止：空表单点「运行」→ `validation_error`，不得出现 `run_start`；
  点「停止」→ 不得出现 `cancel_requested`/`cancel_no_proc`。
- **S7** 关闭：点原生标题栏 X → `close_requested`/`settings_saved`、exit 0、无残留进程。

## 稳定性与清理

- 找窗只认**被测 python 进程的后代 pid**（`FLUTTER_RUNNER_WIN32_WINDOW`），不按标题全局找 —
  Flet 桌面壳是独立进程，按标题会命中历史孤儿窗口。
- 启动前清理「标题是编辑部工具箱、父进程已死」的 flet.exe 孤儿；每个 session 结束先真实关窗
  （WM_CLOSE / 原生 X）并等进程退出，异常路径才 `taskkill /T /F` 整个进程树，最后断言无残留 pid。
- 点击坐标用 client DIP 常量；先按截图里的 `accent_soft` 高亮位置校验，失败再 ±5/6 DIP 重试，
  仍失败才报错并留 `FAIL_*.png` + `failure.txt`。
- 截图基线文件名带窗口尺寸与 DPI：`<label>__win<W>x<H>_client<W>x<H>_dpi<DPI>.png`；
  diff = 尺寸相等 + 全图 mean/占比阈值 + 标题栏/侧栏/内容区/状态栏分区阈值（容忍字体抗锯齿）。

## 已知不能自动化的项（在 `report.md` 里再列一次）

1. **真实写库**（确认写入、自动 commit、推送/回退/采用）：不可逆、会动私有笔记仓
   `EditToolBox-notes/bib_db` 与 git 历史，本目录一律只读；只做渲染与只读点击。
2. **系统 IME 候选窗**：候选词序/位置由系统输入法决定，Win32 无稳定读取接口。
3. **原生文件选择对话框**：Flet FilePicker 在 Windows 走系统对话框，跨进程枚举控件不稳定；
   S4 用 `P2_DEMO_*` 注入固定路径规避，不点「浏览…」。
4. **审美判断**（字重是否符合设计意图）：只能断言源码 token 与渲染结果一致；
   「左栏选中加粗是否有意」在 `test_gui_a_component_weights` 的 note 里记录为有意。
5. **不同 DPI/缩放矩阵**：本机 150% 单点验证；其它比例需要改系统设置后重跑，
   基线文件名虽含 DPI，但当前只维护 144dpi 一份。
6. **托盘/最小化到托盘**：生产默认关闭，且托盘交互依赖系统通知区，不做自动点击。
