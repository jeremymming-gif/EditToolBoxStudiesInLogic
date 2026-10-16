# 「生成目录」切到引擎后的接线验证（headless）

第 95 条：mac 界面的「生成目录」不再自己算（原 `sources-mac/Features/ContentFeature.swift` +
`TocTemplate.swift` 已删），改为调引擎 `Engine/toc.py`。这个测试台守住这次切换的接线
（**编译生产源码本身**，不开 GUI、不起子进程）。

| 生产文件 | 被验证的东西 |
|---|---|
| `sources-mac/Features/TocFeature.swift` | `reportPath/templateFolder → argv` 的纯函数映射（模板留空不传 `--template`）；`locateTool(convertTool:)` 从 `convert.py` 的目录里找 `toc.py`，找不到返回 `nil` |
| `sources-mac/Core/Support.swift` | `EngineLocator`：搜索顺序（用户指定 → app 包内 → 往上找仓库）、`environment(toolDir:)` 挂 `vendor/` |
| `sources-mac/Views/ContentTab.swift` | 已改调 `TocFeature.run` / `TocFeature.preview`；**不再**引用 `ContentFeature`；生成前把勾选写进 `目录设置.json`（引擎读的就是它） |
| `sources-mac/Features/*` | `ContentFeature.swift` 与 `TocTemplate.swift` **确实已删**（逻辑唯一一份在引擎）；`TocSettings.swift` 仍在（它只是设置表单的模型） |
| `tools/cli/main.swift` | Swift CLI 的 `content` 子命令已改为**转发引擎**（命令行与界面同一套逻辑） |

## 跑法

```bash
cd sources-mac
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/tocmc \
  -o /tmp/toc_test_harness \
  Core/Support.swift Core/Log.swift Core/ViewModels.swift Core/EditorsPage.swift \
  Features/InfoCheckFeature.swift Features/TocSettings.swift Features/TocFeature.swift \
  ../tools/toc_test/main.swift
/tmp/toc_test_harness        # 期望：SUMMARY: 16 PASS / 0 FAIL
```

## 产物回归靠什么

切换之后 Swift 参照物没了，输出改由**冻结快照**守：
`Engine/tests/fixtures/golden_toc/`（由 `tests/test_infocheck_toc.py` 的 (b2) 段比对；
要重新冻结就跑 `SLCN_FREEZE_GOLDEN=1 python3 tests/test_infocheck_toc.py`）。
快照里的夹具覆盖「继承本期成品 + 改表头」这条生产路径，表头里的卷期号会被换成本期的值。
