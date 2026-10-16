# 「检查tex信息」切到引擎后的接线验证（headless）

第 96 条：mac 界面的「检查tex信息」不再自己解析 tex（原 452 行的 `InfoCheckFeature.swift`
瘦身为「拼参数 + 解产物」），改为调引擎 `Engine/infocheck.py`。

| 生产文件 | 被验证的东西 |
|---|---|
| `sources-mac/Features/InfoCheckFeature.swift` | `folderPath → argv`；`locateTool(convertTool:)` 从 `convert.py` 的目录里找 `infocheck.py`（找不到返回 `nil`）；`newestReportJSON(in:since:)` 的**新鲜度判据**（不许把上一轮的旧报告当结果）；`decodeReport(at:)` 能把引擎产物解回来 |
| `sources-mac/Features/InfoCheckFeature.swift`（数据模型） | `ArticleInfo` / `InfoReport` 的**字段名与引擎产物逐字对齐**（这是本次切换最大的风险点）；`pageMismatch`、`display*` 展示串、`inconsistencies` 聚合、`txtPath` |
| `sources-mac/Views/InfoCheckView.swift` | 仍调 `InfoCheckFeature.run(folderPath:log:)`（签名未变，视图无需改动） |
| `tools/cli/main.swift` | `info` 子命令已改为**转发引擎**（命令行与界面同一套逻辑） |

**夹具 `fixture.json` 是引擎真跑出来的产物**（合成稿件，不含任何真实作者姓名 —— 这个目录会随公开仓发布）。
字段名若与引擎不一致，这里会当场变红。

## 跑法

```bash
cd sources-mac
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/icmc \
  -o /tmp/infocheck_test_harness \
  Core/Support.swift Core/Log.swift Core/ViewModels.swift Core/EditorsPage.swift \
  Features/InfoCheckFeature.swift ../tools/infocheck_test/main.swift
/tmp/infocheck_test_harness        # 期望：SUMMARY: 27 PASS / 0 FAIL
```

> 五个测试台一起跑用 `bash tools/run_swift_tests.sh`（统一从 `sources-mac/` 编译、每次新 module cache）。
