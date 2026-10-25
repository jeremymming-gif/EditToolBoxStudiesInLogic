// 「生成目录」切到引擎后的接线验证（第 95 条）+ B-226 一键交付（headless）。
//
// **编译的是生产源码本身**（`sources-mac/Features/TocFeature.swift` + `sources-mac/Core/Support.swift`）。
// 不开 GUI、不起子进程、不联网 —— 只断言「参数怎么拼」「结果 JSON 怎么读/怎么显示」「界面还认不认旧逻辑」。
//
// 跑法（在 sources-mac 下）：
//     swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/tocmc \
//       -o /tmp/toc_test_harness \
//       Core/Support.swift Core/Log.swift Core/ViewModels.swift \
//       Features/InfoCheckFeature.swift Features/TocSettings.swift Features/TocFeature.swift Features/MergeFeature.swift \
//       ../tools/toc_test/main.swift
//     /tmp/toc_test_harness        # 期望：SUMMARY: N PASS / 0 FAIL
//
// 统一入口是 `bash tools/run_swift_tests.sh --only toc`（模块缓存/编译参数都在那儿）。

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
let tmpJSON = "/tmp/slcn-toc-结果.json"
check("(a) 默认 = 一键最终交付：`--report` + `--place --compile --result-json <tmp>`",
      TocFeature.arguments(reportPath: "/tmp/期/信息检查2026.txt", resultJSONPath: tmpJSON)
      == ["--report", "/tmp/期/信息检查2026.txt", "--place", "--compile",
          "--result-json", tmpJSON],
      "\(TocFeature.arguments(reportPath: "/tmp/期/信息检查2026.txt", resultJSONPath: tmpJSON))")
check("(a) 指定模板期目录 → 追加 `--template`（仍带落位/编译）",
      TocFeature.arguments(reportPath: "/x/r.txt", templateFolder: "/x/上一期",
                           resultJSONPath: tmpJSON)
      == ["--report", "/x/r.txt", "--template", "/x/上一期", "--place", "--compile",
          "--result-json", tmpJSON])
check("(a) 模板留空/空白 → **不传** `--template`（交给引擎自己找上期）",
      !TocFeature.arguments(reportPath: "/x/r.txt", templateFolder: "   ").contains("--template"))
check("(a) 高级开关「仅生成 tex」打开 → 只传 `--report`（旧行为，不落位/不编译/不写结果 JSON）",
      TocFeature.arguments(reportPath: "/x/r.txt", textOnly: true, resultJSONPath: tmpJSON)
      == ["--report", "/x/r.txt"])
check("(a) 「仅生成 tex」+ 指定模板 → 只有 `--report --template`",
      TocFeature.arguments(reportPath: "/x/r.txt", templateFolder: "/x/上期", textOnly: true)
      == ["--report", "/x/r.txt", "--template", "/x/上期"])
check("(a) 没给结果 JSON 路径时不传半截参数（仍落位+编译）",
      TocFeature.arguments(reportPath: "/x/r.txt")
      == ["--report", "/x/r.txt", "--place", "--compile"])
check("(a) 路径含空格与中文时不做 shell 插值（数组形式原样传）",
      TocFeature.arguments(reportPath: "/tmp/期 90/信息检查 1.txt", resultJSONPath: tmpJSON)
      == ["--report", "/tmp/期 90/信息检查 1.txt", "--place", "--compile",
          "--result-json", tmpJSON])
check("(a) 结果 JSON 临时路径每次不同、在临时目录（不落稿件目录）",
      { let a = TocFeature.newResultJSONPath(); let b = TocFeature.newResultJSONPath()
        return a != b && a.contains("slcn-toc-") && a.hasSuffix(".json") }())

// ── (b) 引擎结果 JSON 解析与展示（外壳只显示，不判模板来源）─────────────
print("\n── (b) 结果 JSON（契约字段）──")
let resultJSON = """
{"templateSource":"上一期","templateNote":"模板：2026年第4期(89)",
 "tocDir":"/tmp/期/000 Table of Contents",
 "placed":{"zhTex":"/tmp/期/000 Table of Contents/Chinese Content.tex",
           "zhPdf":"/tmp/期/000 Table of Contents/Chinese Content.pdf",
           "enTex":"/tmp/期/000 Table of Contents/contents.tex",
           "enPdf":"/tmp/期/000 Table of Contents/contents.pdf"},
 "compiled":true,"pages":{"zh":1,"en":1},
 "column":{"enabled":true,"title":"专栏：认知悖论研究"},
 "manualEdits":[{"kind":"标题占位","article":"08 样例","note":"源稿标题为占位，人工版手写"}],
 "warnings":["缺 000 Table of Contents，已自动创建"]}
"""
let decoded = TocFeature.decodeResult(from: Data(resultJSON.utf8))
check("(b) 结果 JSON 能按契约解析", decoded != nil)
check("(b) 结果 JSON 4 个固定名路径 + 模板来源 + 页数 + 栏目",
      decoded?.placedPaths.count == 4
      && decoded?.templateSource == "上一期"
      && decoded?.tocDir == "/tmp/期/000 Table of Contents"
      && decoded?.compiled == true
      && decoded?.pages?.zh == 1 && decoded?.pages?.en == 1
      && decoded?.column?.title == "专栏：认知悖论研究",
      "\(decoded?.placedPaths ?? [])")
let shown = TocFeature.displayLines(for: decoded!)
check("(b) 结果区显示模板来源（只显示引擎返回的 templateSource，不自己判断）",
      shown.contains("模板来源：上一期"))
check("(b) 结果区显示 4 个固定名路径（中英 tex/pdf）",
      shown.contains { $0.contains("Chinese Content.pdf") }
      && shown.contains { $0.contains("Chinese Content.tex") }
      && shown.contains { $0.contains("contents.pdf") }
      && shown.contains { $0.contains("contents.tex") },
      shown.joined(separator: " ｜ "))
check("(b) 结果区显示页数（中文 1 页／英文 1 页）",
      shown.contains("页数：中文 1 页／英文 1 页"))
check("(b) `manualEdits` / `warnings` 在结果区列出（只显示，不改行为）",
      shown.contains { $0.contains("需人工核对") && $0.contains("标题占位") }
      && shown.contains { $0.hasPrefix("⚠️") && $0.contains("已自动创建") })
check("(b) 字段缺失的旧结果 JSON 不炸（全部可选）",
      TocFeature.decodeResult(from: Data("{}".utf8)) != nil
      && TocFeature.displayLines(for: TocFeature.decodeResult(from: Data("{}".utf8))!).isEmpty)
check("(b) 非法 JSON → nil（界面走「没有结果 JSON」分支，不假绿）",
      TocFeature.decodeResult(from: Data("not json".utf8)) == nil)
check("(b) 结果 JSON 文件不存在 → nil（rc=2 不写 JSON 时也不崩）",
      TocFeature.loadResult(at: "/tmp/不存在的目录/slcn-toc-无.json") == nil)

// ── (b2) 失败原因（引擎 rc=2 的原因在 stderr，Shell.run 混流给日志）──────
print("\n── (b2) 失败原因抽取 ──")
let failOut = """
运行生成目录的函数

❌ 编译前字体检查失败：缺少字体 SimSun（不自动换字体）
请安装字体后重试。
"""
let reason = TocFeature.failureReason(from: failOut)
check("(b2) 从引擎输出里挑出可操作报错（字体缺失原因）",
      reason.contains("缺少字体 SimSun") && reason.contains("请安装字体后重试"))
check("(b2) 不把正常日志当报错（输出里没有报错行 → 空串）",
      TocFeature.failureReason(from: "生成中文目录…\n生成英文目录…\n").isEmpty)

// ── (c) 引擎定位 ─────────────────────────────────────────────────────────
print("\n── (c) 引擎定位（EngineLocator 统一解析）──")
check("(c) 生产源码里的 Engine/toc.py 真的在", FileManager.default.fileExists(atPath: engineTool), engineTool)
check("(c) 从 convert.py 的目录里找到 toc.py",
      TocFeature.locateTool(convertTool: convertTool) == engineTool,
      "\(TocFeature.locateTool(convertTool: convertTool) ?? "nil")")
check("(c) 反例：引擎目录没找到 → nil（不拼半截命令）",
      TocFeature.locateTool(convertTool: nil) == nil)
check("(c) 反例：目录里没有 toc.py → nil",
      TocFeature.locateTool(convertTool: "/tmp/不存在/convert.py") == nil)
check("(c) 环境变量增量挂的是引擎目录下的 vendor/（依赖在那儿）",
      EngineLocator.environment(toolDir: "/x/Engine")["PYTHONPATH"] == "/x/Engine/vendor")

// ── (d) 界面接线（源码级：不编译视图，它要 SwiftUI 上下文）────────────────
print("\n── (d) 界面接线 ──")
func readSource(_ rel: String) -> String {
    (try? String(contentsOf: sources.appendingPathComponent(rel), encoding: .utf8)) ?? ""
}
let tab = readSource("Views/ContentTab.swift")
check("(d) 「生成目录」页**只调这一个入口**：`TocFeature.run(...)`（落位/编译不在 Swift 里）",
      tab.contains("TocFeature.run(reportPath: target")
      && tab.contains("textOnly: textOnly")
      && tab.contains("resultJSONPath: resultJSONPath"))
check("(d) 界面上**没有第二份**落位/编译逻辑（不直接起进程、不写 xelatex）",
      !tab.contains("Shell.run(") && !tab.contains("Process(")
      && !tab.contains("xelatex") && !tab.contains("000 Table of Contents/"),
      "ContentTab 只读目录存在性做提示")
check("(d) 页面的篇目预览也走引擎：`TocFeature.preview(...)`",
      tab.contains("TocFeature.preview(reportPath: target)"))
check("(d) 高级开关「仅生成 tex（不落位、不编译）」存在且默认关（`textOnly = false`）",
      tab.contains("仅生成 tex（不落位、不编译）")
      && readSource("Views/ContentTab.swift").contains("textOnly: Bool = false"))
check("(d) 结果区显示引擎结果：4 路径 / 模板来源 / 页数 + 「打开目录」",
      tab.contains("resultTitle") && tab.contains("打开目录")
      && tab.contains("TocFeature.displayLines(for: result)"))
check("(d) 失败时显示引擎 stderr 原因（`TocFeature.failureReason`），不是白报错",
      tab.contains("TocFeature.failureReason(from: output)"))
check("(d) 页面**不再**引用已删除的 Swift 逻辑（`ContentFeature`）",
      !tab.contains("ContentFeature"))
check("(d) 生成前把勾选结果写进 `目录设置.json`（引擎读的就是它，只有一个真相来源）",
      tab.contains("settings.columnArticles = model.rows.filter(\\.selected).map(\\.id)")
      && tab.contains("try? settings.save(to: dir)"))
check("(d) `sources-mac/Features/ContentFeature.swift` 与 `TocTemplate.swift` 确实已删（逻辑唯一一份在引擎）",
      !FileManager.default.fileExists(atPath: sources.appendingPathComponent("Features/ContentFeature.swift").path)
      && !FileManager.default.fileExists(atPath: sources.appendingPathComponent("Features/TocTemplate.swift").path))
check("(d) `TocSettings.swift` 仍在（它只是**设置表单的模型**，不是文档逻辑）",
      FileManager.default.fileExists(atPath: sources.appendingPathComponent("Features/TocSettings.swift").path))
check("(d) Swift CLI 的 `content` 已改为转发引擎（命令行与界面同一套逻辑）",
      readSource("../tools/cli/main.swift").contains("TocFeature.locateTool()"))

// ── (e) B-227：本期出刊页已删（合并能力只在「合并文件」页）────────────────
print("\n── (e) B-227 本期出刊页已删 ──")
let mainWindow = readSource("Views/MainWindow.swift")
check("(e) `PublishFeature.swift` 已删、MainWindow 不再引用它",
      !FileManager.default.fileExists(atPath: sources.appendingPathComponent("Features/PublishFeature.swift").path)
      && !mainWindow.contains("PublishFeature"))
check("(e) MainWindow 不再有「本期出刊」页 / `.publish` 导航项",
      !mainWindow.contains("本期出刊") && !mainWindow.contains("case .publish")
      && !mainWindow.contains("publishPage"))
check("(e) 侧栏「整期装配」= 检查tex信息 → 编译全部 → 生成目录 → 删除临时文件 → 合并文件 → 目录摘要推送",
      mainWindow.contains("[.infocheck, .compile, .toc, .cleantemp, .merge, .dirpush]"))

print("\nSUMMARY: \(pass) PASS / \(fail) FAIL")
exit(fail == 0 ? 0 : 1)
