// 「一校核查」页的 headless 验证（第 92 条）。
//
// **编译的是生产源码本身**（`sources-mac/Features/ProofCheckFeature.swift`），不是复制品。
// 不开 GUI、不发网络请求、不起子进程 —— 只断言「参数怎么拼」与「引擎找不找得到」。
//
// 跑法（在 sources-mac 下）：
//     swiftc -o /tmp/proofcheck_test_harness \
//       Core/Support.swift Core/Log.swift \
//       Features/ConvertFeature.swift Features/ProofCheckFeature.swift Features/MergeFeature.swift \
//       ../tools/proofcheck_test/main.swift
//     /tmp/proofcheck_test_harness        # 期望：SUMMARY: N PASS / 0 FAIL

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

// 本文件在 <仓库>/tools/proofcheck_test/ 下
let here = URL(fileURLWithPath: #filePath)                       // …/tools/proofcheck_test/main.swift
let toolbox = here.deletingLastPathComponent()                    // …/tools/proofcheck_test
    .deletingLastPathComponent()                                  // …/tools
    .deletingLastPathComponent()                                  // …/EditToolBox
let engineTool = toolbox.appendingPathComponent("Engine/proofcheck.py").path
let sources = toolbox.appendingPathComponent("sources-mac")

// ── (a) 参数拼装：纯函数，直接断言 ────────────────────────────────────────
print("── (a) options → argv ──")

var o = ProofCheckFeature.Options()
o.tex = "/tmp/期/01 作者/A.tex"
check("(a) 只给稿件 → `--tex <路径>`，不含 `--fix` / `--lang`",
      ProofCheckFeature.arguments(o) == ["--tex", "/tmp/期/01 作者/A.tex"],
      "\(ProofCheckFeature.arguments(o))")

o.fix = true
check("(a) 勾了「顺手改掉」→ 末尾加 `--fix`",
      ProofCheckFeature.arguments(o) == ["--tex", "/tmp/期/01 作者/A.tex", "--fix"],
      "\(ProofCheckFeature.arguments(o))")

o.lang = "en"
check("(a) 指定语种 → `--lang en` 出现在 `--fix` **之前**（顺序固定，便于断言）",
      ProofCheckFeature.arguments(o) == ["--tex", "/tmp/期/01 作者/A.tex", "--lang", "en", "--fix"],
      "\(ProofCheckFeature.arguments(o))")

o.lang = ""
o.fix = false
check("(a) 语种「自动」= 空串 → **不传** `--lang`（交给引擎按模板字段判）",
      !ProofCheckFeature.arguments(o).contains("--lang"))
check("(a) 路径里有空格/中文时不做 shell 插值（数组形式原样传）",
      ProofCheckFeature.arguments({ var x = ProofCheckFeature.Options(); x.tex = "/tmp/期 90/甲 乙.tex"; return x }())
        == ["--tex", "/tmp/期 90/甲 乙.tex"])

// ── (b) 引擎定位：proofcheck.py 必须与 convert.py 同目录 ──────────────────
print("\n── (b) 引擎定位 ──")

check("(b) 生产源码里的 Engine/proofcheck.py 真的在", FileManager.default.fileExists(atPath: engineTool),
      engineTool)
let convertTool = toolbox.appendingPathComponent("Engine/convert.py").path
check("(b) `locateTool(convertTool:)` 从 convert.py 的目录里找到 proofcheck.py",
      ProofCheckFeature.locateTool(convertTool: convertTool) == engineTool,
      "\(ProofCheckFeature.locateTool(convertTool: convertTool) ?? "nil")")
check("(b) 反例：convert.py 为 nil（引擎没找到）时返回 nil，不让上层拼出半截命令",
      ProofCheckFeature.locateTool(convertTool: nil) == nil)
check("(b) 反例：目录里没有 proofcheck.py 时返回 nil（不误报路径）",
      ProofCheckFeature.locateTool(convertTool: "/tmp/不存在/convert.py") == nil)

// ── (c) 页面接线（源码级检查：不编译视图，它要 SwiftUI 上下文）─────────────
print("\n── (c) 页面与标签页接线 ──")

func readSource(_ rel: String) -> String {
    (try? String(contentsOf: sources.appendingPathComponent(rel), encoding: .utf8)) ?? ""
}

let mainWindow = readSource("Views/MainWindow.swift")
let view = readSource("Views/ProofCheckView.swift")
let models = readSource("Core/ViewModels.swift")

check("(c) MainWindow 的侧边栏里有「一校核查」页",
      mainWindow.contains("proofcheck") && mainWindow.contains("return \"一校核查\""))
check("(c) MainWindow 建了 `ProofCheckModel` 并传给页面",
      mainWindow.contains("@StateObject private var proofModel = ProofCheckModel()")
      && mainWindow.contains("ProofCheckView(log: log, model: proofModel)"))
// 注意：要比的是**导航定义**的位置，不是文档注释里那几个词
// （注释里也会出现「一校核查」，第一版就栽在这上面）
// 2026-10-17 起界面从 TabView 换成「分组侧边栏」：顺序由 `NavPage.singleArticle` 定义，
// 一校核查必须在「文献回读校验」之后（编辑流程：回读校验 → 一校核查）。
// ⚠️ 要取**定义**那一行（`static let singleArticle = [...]`），不是 List 里 `ForEach(...)` 的用法行
let orderLine = mainWindow.split(separator: "\n")
    .first { $0.contains("static let singleArticle") } ?? ""
let order = orderLine.description
let iBib = order.range(of: ".checkbib")?.lowerBound
let iProof = order.range(of: ".proofcheck")?.lowerBound
// B-235：「合并文件」从「单篇稿件」移到「整期装配」，单篇组不再含 `.merge`
let assemblyLine = mainWindow.split(separator: "\n")
    .first { $0.contains("static let assembly") } ?? ""
let assemblyOrder = assemblyLine.description
check("(c) 「单篇稿件」组顺序：初排 → 文献回读校验 → **一校核查**（不含合并文件；编辑流程）",
      order.contains(".convert") && iBib != nil && iProof != nil
      && iBib! < iProof! && !order.contains(".merge"),
      order.isEmpty ? "<没找到 singleArticle 定义>" : String(order.prefix(120)))
check("(c) 「合并文件」页归「整期装配」组（B-235）",
      assemblyOrder.contains(".merge"),
      assemblyOrder.isEmpty ? "<没找到 assembly 定义>" : String(assemblyOrder.prefix(120)))
check("(c) 视图**不用 `@State`**（本机只有 Command Line Tools，SwiftUI 宏插件不可用）",
      !view.contains("@State ") && !view.contains("@State private"))
check("(c) 视图把状态交给注入的 model（`@ObservedObject var model: ProofCheckModel`）",
      view.contains("@ObservedObject var model: ProofCheckModel"))
check("(c) 模型里三个选项齐全（稿件 / 另存 / 语种）+ 运行标记",
      models.contains("final class ProofCheckModel: ObservableObject")
      && models.contains("@Published var tex: String = \"\"")
      && models.contains("@Published var fix: Bool = false")
      && models.contains("@Published var lang: String = \"自动\"")
      && models.contains("@Published var running: Bool = false"))
check("(c) 视图调的是 `ProofCheckFeature.run(options:log:)`（逻辑不在视图里）",
      view.contains("ProofCheckFeature.run(options: options, log: logger)"))
check("(c) 视图在工作线程跑、回主线程复位（界面不卡）",
      view.contains("DispatchQueue.global(qos: .userInitiated).async")
      && view.contains("DispatchQueue.main.async { model.running = false }"))
check("(c) 退出码 1（有命中）**不许**说成失败",
      readSource("Features/ProofCheckFeature.swift").contains("有命中")
      && !readSource("Features/ProofCheckFeature.swift").contains("case 1:\n            log(\"❌"))

print("\nSUMMARY: \(pass) PASS / \(fail) FAIL")
exit(fail == 0 ? 0 : 1)
