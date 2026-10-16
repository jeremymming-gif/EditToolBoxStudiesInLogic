// 「生成目录」切到引擎后的接线验证（第 95 条，headless）。
//
// **编译的是生产源码本身**（`sources-mac/Features/TocFeature.swift` + `sources-mac/Core/Support.swift`）。
// 不开 GUI、不起子进程、不联网 —— 只断言「参数怎么拼」「引擎在哪」「界面还认不认旧逻辑」。
//
// 跑法（在 sources-mac 下）：
//     swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/tocmc \
//       -o /tmp/toc_test_harness \
//       Core/Support.swift Core/Log.swift Core/ViewModels.swift \
//       Features/InfoCheckFeature.swift Features/TocSettings.swift Features/TocFeature.swift Features/MergeFeature.swift \
//       ../tools/toc_test/main.swift
//     /tmp/toc_test_harness        # 期望：SUMMARY: N PASS / 0 FAIL

import Foundation

var pass = 0
var fail = 0

func check(_ name: String, _ ok: Bool, _ detail: String = "") {
    if ok {
        pass += 1
        print("PASS  \(name)" + (detail.isEmpty ? "" : "  \(detail)"))
    } else {
        fail += 1
        print("FAIL  \(name)  \(detail)")
    }
}

let here = URL(fileURLWithPath: #filePath)
let toolbox = here.deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent()
let engineTool = toolbox.appendingPathComponent("Engine/toc.py").path
let convertTool = toolbox.appendingPathComponent("Engine/convert.py").path
let sources = toolbox.appendingPathComponent("sources-mac")

// ── (a) 参数拼装 ─────────────────────────────────────────────────────────
print("── (a) reportPath → argv ──")
check("(a) 只给报告 → `--report <路径>`，不含 `--template`",
      TocFeature.arguments(reportPath: "/tmp/期/信息检查2026.txt")
      == ["--report", "/tmp/期/信息检查2026.txt"],
      "\(TocFeature.arguments(reportPath: "/tmp/期/信息检查2026.txt"))")
check("(a) 指定模板期目录 → 追加 `--template`",
      TocFeature.arguments(reportPath: "/x/r.txt", templateFolder: "/x/上一期")
      == ["--report", "/x/r.txt", "--template", "/x/上一期"])
check("(a) 模板留空/空白 → **不传** `--template`（交给引擎自己找上一期）",
      !TocFeature.arguments(reportPath: "/x/r.txt", templateFolder: "   ").contains("--template"))
check("(a) 路径含空格与中文时不做 shell 插值（数组形式原样传）",
      TocFeature.arguments(reportPath: "/tmp/期 90/信息检查 1.txt")
      == ["--report", "/tmp/期 90/信息检查 1.txt"])

// ── (b) 引擎定位 ─────────────────────────────────────────────────────────
print("\n── (b) 引擎定位（EngineLocator 统一解析）──")
check("(b) 生产源码里的 Engine/toc.py 真的在", FileManager.default.fileExists(atPath: engineTool), engineTool)
check("(b) 从 convert.py 的目录里找到 toc.py",
      TocFeature.locateTool(convertTool: convertTool) == engineTool,
      "\(TocFeature.locateTool(convertTool: convertTool) ?? "nil")")
check("(b) 反例：引擎目录没找到 → nil（不拼半截命令）",
      TocFeature.locateTool(convertTool: nil) == nil)
check("(b) 反例：目录里没有 toc.py → nil",
      TocFeature.locateTool(convertTool: "/tmp/不存在/convert.py") == nil)
check("(b) 环境变量增量挂的是引擎目录下的 vendor/（依赖在那儿）",
      EngineLocator.environment(toolDir: "/x/Engine")["PYTHONPATH"] == "/x/Engine/vendor")

// ── (c) 界面接线（源码级：不编译视图，它要 SwiftUI 上下文）────────────────
print("\n── (c) 界面接线 ──")
func readSource(_ rel: String) -> String {
    (try? String(contentsOf: sources.appendingPathComponent(rel), encoding: .utf8)) ?? ""
}
let tab = readSource("Views/ContentTab.swift")
check("(c) 「生成目录」页已改为调引擎：`TocFeature.run(...)`",
      tab.contains("TocFeature.run(reportPath: target, templateFolder: settings.templateFolder, log: logger)"))
check("(c) 页面的篇目预览也走引擎：`TocFeature.preview(...)`",
      tab.contains("TocFeature.preview(reportPath: target)"))
check("(c) 页面**不再**引用已删除的 Swift 逻辑（`ContentFeature`）",
      !tab.contains("ContentFeature"))
check("(c) 生成前把勾选结果写进 `目录设置.json`（引擎读的就是它，只有一个真相来源）",
      tab.contains("settings.columnArticles = model.rows.filter(\\.selected).map(\\.id)")
      && tab.contains("try? settings.save(to: dir)"))
check("(c) `sources-mac/Features/ContentFeature.swift` 与 `TocTemplate.swift` 确实已删（逻辑唯一一份在引擎）",
      !FileManager.default.fileExists(atPath: sources.appendingPathComponent("Features/ContentFeature.swift").path)
      && !FileManager.default.fileExists(atPath: sources.appendingPathComponent("Features/TocTemplate.swift").path))
check("(c) `TocSettings.swift` 仍在（它只是**设置表单的模型**，不是文档逻辑）",
      FileManager.default.fileExists(atPath: sources.appendingPathComponent("Features/TocSettings.swift").path))
check("(c) Swift CLI 的 `content` 已改为转发引擎（命令行与界面同一套逻辑）",
      readSource("../tools/cli/main.swift").contains("TocFeature.locateTool()"))

print("\nSUMMARY: \(pass) PASS / \(fail) FAIL")
exit(fail == 0 ? 0 : 1)
