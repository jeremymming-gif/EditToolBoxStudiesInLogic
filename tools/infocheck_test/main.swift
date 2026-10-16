// 「检查tex信息」切到引擎后的接线验证（第 96 条，headless）。
//
// **编译的是生产源码本身**（`sources-mac/Features/InfoCheckFeature.swift` + `sources-mac/Core/Support.swift`）。
// 不开 GUI、不起子进程 —— 只断言「参数怎么拼」「引擎在哪」「产物字段能不能解回来」。
//
// ⚠️ 最要紧的是**字段名对齐**：Swift 的 `ArticleInfo` 直接映射引擎写的
// `信息检查<时间戳>.json`。夹具 `fixture.json` 是引擎**真跑出来**的产物（合成稿件，
// 不含任何真实作者姓名），字段名对不上这里就会红。
//
// 跑法（在 sources-mac 下）：
//     swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/icmc \
//       -o /tmp/infocheck_test_harness \
//       Core/Support.swift Core/Log.swift Core/ViewModels.swift \
//       Features/InfoCheckFeature.swift ../tools/infocheck_test/main.swift
//     /tmp/infocheck_test_harness        # 期望：SUMMARY: N PASS / 0 FAIL

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
let engineTool = toolbox.appendingPathComponent("Engine/infocheck.py").path
let convertTool = toolbox.appendingPathComponent("Engine/convert.py").path
let sources = toolbox.appendingPathComponent("sources-mac")
let fixture = here.deletingLastPathComponent().appendingPathComponent("fixture.json").path

// ── (a) 参数与定位 ───────────────────────────────────────────────────────
print("── (a) folderPath → argv ──")
check("(a) `--folder <期目录>`", InfoCheckFeature.arguments(folderPath: "/tmp/期 90")
      == ["--folder", "/tmp/期 90"], "\(InfoCheckFeature.arguments(folderPath: "/tmp/期 90"))")
check("(a) 路径含空格/中文时不做 shell 插值",
      InfoCheckFeature.arguments(folderPath: "/tmp/2026年第5期(90)").last == "/tmp/2026年第5期(90)")

print("\n── (b) 引擎定位（EngineLocator）──")
check("(b) 生产源码里的 Engine/infocheck.py 真的在", FileManager.default.fileExists(atPath: engineTool))
check("(b) 从 convert.py 的目录里找到 infocheck.py",
      InfoCheckFeature.locateTool(convertTool: convertTool) == engineTool,
      "\(InfoCheckFeature.locateTool(convertTool: convertTool) ?? "nil")")
check("(b) 反例：引擎没找到 → nil", InfoCheckFeature.locateTool(convertTool: nil) == nil)
check("(b) 反例：目录里没有 infocheck.py → nil",
      InfoCheckFeature.locateTool(convertTool: "/tmp/不存在/convert.py") == nil)

// ── (c) 产物解码：字段名与引擎**逐字对齐** ────────────────────────────────
print("\n── (c) 解码引擎产物（字段名对齐）──")
let report = InfoCheckFeature.decodeReport(at: fixture)
check("(c) 能解出 InfoReport（不改字段名就能解）", report != nil, fixture)
if let r = report {
    check("(c) 篇数正确", r.articles.count == 2, "\(r.articles.count) 篇")
    let a = r.articles[0]
    check("(c) 卷/期/年/起止页/页数 都解出来了",
          a.volume == "19" && a.issue == "5" && a.year == "2026"
          && a.startPage == "1" && a.endPage == "16" && a.pages == "16",
          "\(a.volume)/\(a.issue)/\(a.year) \(a.startPage)–\(a.endPage) 共\(a.pages)")
    check("(c) 中英文标题解出来（副标题破折号保留）",
          (a.chineseTitle ?? "").contains("——") && (a.englishTitle ?? "").contains("Yashin"),
          a.chineseTitle ?? "nil")
    check("(c) 作者字段解出来（中文作者带 `\\kaishu`，与引擎产物一致）",
          (a.firstAuthorChinese ?? "").contains("陆王")
          && (a.secondAuthorChinese ?? "").contains("王二"),
          "\(a.firstAuthorChinese ?? "nil") / \(a.secondAuthorChinese ?? "nil")")
    check("(c) 字号与行距解出来", a.fontSize == "10.5t" && a.lineSpacing == "13",
          "\(a.fontSize ?? "nil") / \(a.lineSpacing ?? "nil")")
    check("(c) hasTeX=true、无 pdf 时 actualPDFPages=nil 且**不误报**页数不符",
          a.hasTeX && a.actualPDFPages == nil && !a.pageMismatch)
    // ⚠️ 展示串**保留** `\kaishu`（切换前就是这样：`clean()` 只去花括号与换行）。
    //    切换的目标是"行为等价"，所以这里不趁机改界面文案 —— 要改另开一条。
    check("(c) 界面用的展示串：去掉花括号但保留 `\\kaishu`（与原行为一致）",
          !a.displayAuthors.contains("{") && a.displayAuthors.contains("陆王")
          && a.displayAuthors.contains("\\kaishu"), a.displayAuthors)
    check("(c) `txtPath` 由 issueFolder + generatedAt 拼出（「在访达中显示报告」按钮要用）",
          r.txtPath.hasSuffix("信息检查\(r.generatedAt).txt") && r.txtPath.contains(r.issueFolder),
          r.txtPath)
    check("(c) 卷期年一致时 `inconsistencies` 为空", r.inconsistencies.isEmpty,
          "\(r.inconsistencies)")

    // 反例：造一处「声明页数与实际不符」，界面应标红并给出提示
    var fake = r.articles[0]
    fake.actualPDFPages = 12          // 声明 16、实际 12
    check("(c) 反例：页数不符 → `pageMismatch` 为真", fake.pageMismatch)
    let bad = InfoReport(generatedAt: r.generatedAt, issueFolder: r.issueFolder,
                         articles: [fake, r.articles[1]])
    check("(c) 反例：`inconsistencies` 报出「声明页数 X，实际 Y」",
          bad.inconsistencies.contains { $0.contains("声明页数 16，实际 12") },
          "\(bad.inconsistencies)")
    // 反例：卷号不一致
    var other = r.articles[1]
    other.volume = "20"
    let mixed = InfoReport(generatedAt: r.generatedAt, issueFolder: r.issueFolder,
                           articles: [r.articles[0], other])
    check("(c) 反例：卷号不一致 → 提示里列出两个值",
          mixed.inconsistencies.contains { $0.hasPrefix("卷号不一致") && $0.contains("19") && $0.contains("20") },
          "\(mixed.inconsistencies)")
}

// ── (d) 只读最新产物（避免把上一轮的旧报告当结果）─────────────────────────
print("\n── (d) 最新产物的新鲜度判据 ──")
let tmp = NSTemporaryDirectory() + "slcn_ic_\(UUID().uuidString)"
try? FileManager.default.createDirectory(atPath: tmp, withIntermediateDirectories: true)
check("(d) 空目录 → nil", InfoCheckFeature.newestReportJSON(in: tmp, since: Date(timeIntervalSince1970: 0)) == nil)
let old = (tmp as NSString).appendingPathComponent("信息检查2020_01_01_00_00_00.json")
FileManager.default.createFile(atPath: old, contents: Data("{}".utf8))
check("(d) 只有旧产物、要求 since=现在 → nil（**不把上一轮的报告当结果**）",
      InfoCheckFeature.newestReportJSON(in: tmp, since: Date()) == nil)
check("(d) since 放宽到过去 → 能取到那个产物",
      InfoCheckFeature.newestReportJSON(in: tmp, since: Date(timeIntervalSince1970: 0)) == old)
try? FileManager.default.removeItem(atPath: tmp)

// ── (e) 界面与 CLI 接线（源码级）────────────────────────────────────────
print("\n── (e) 接线 ──")
func readSource(_ rel: String) -> String {
    (try? String(contentsOf: sources.appendingPathComponent(rel), encoding: .utf8)) ?? ""
}
check("(e) 「检查tex信息」页仍调 `InfoCheckFeature.run(folderPath:log:)`（签名未变，视图无需改）",
      readSource("Views/InfoCheckView.swift").contains("InfoCheckFeature.run(folderPath: target, log: logger)"))
check("(e) Swift 侧**不再解析 tex**（没有 processFolders / extractLaTeXValue / pythonSplitlines）",
      !readSource("Features/InfoCheckFeature.swift").contains("processFolders")
      && !readSource("Features/InfoCheckFeature.swift").contains("extractLaTeXValue")
      && !readSource("Features/InfoCheckFeature.swift").contains("pythonSplitlines"))
check("(e) Swift 侧**不再用 PDFKit** 数 PDF 页数（那是引擎的活）",
      !readSource("Features/InfoCheckFeature.swift").contains("import PDFKit"))
check("(e) Swift CLI 的 `info` 已改为转发引擎（命令行与界面同一套逻辑）",
      readSource("../tools/cli/main.swift").contains("InfoCheckFeature.locateTool()"))

print("\nSUMMARY: \(pass) PASS / \(fail) FAIL")
exit(fail == 0 ? 0 : 1)
