// 「删除临时文件」「编译全部」切到引擎后的接线验证（第 98 条，headless）。
//
// 为什么这两项合成一个测试台：两者都是「一页 = 一个目录 + 一个引擎入口」，
// 断言形状完全一样（argv / 定位 / 接线 / 旧逻辑已删）。分成两个文件只是重复。
//
// **编译的是生产源码本身**。不开 GUI、不起子进程。
//
// 跑法（在 sources-mac 下）：
//     swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/ctmc \
//       -o /tmp/cutover_test_harness \
//       Core/Support.swift Core/Log.swift Core/ViewModels.swift \
//       Features/InfoCheckFeature.swift Features/DeleteTempFeature.swift Features/CompileFeature.swift \
//       ../tools/cutover_test/main.swift
//     /tmp/cutover_test_harness        # 期望：SUMMARY: N PASS / 0 FAIL

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
let convertTool = toolbox.appendingPathComponent("Engine/convert.py").path
let sources = toolbox.appendingPathComponent("sources-mac")

func readSource(_ rel: String) -> String {
    (try? String(contentsOf: sources.appendingPathComponent(rel), encoding: .utf8)) ?? ""
}

// ── (a) 删除临时文件 ─────────────────────────────────────────────────────
print("── (a) 删除临时文件 → cleantemp.py ──")
let cleanTool = toolbox.appendingPathComponent("Engine/cleantemp.py").path
check("(a) 生产源码里的 Engine/cleantemp.py 真的在", FileManager.default.fileExists(atPath: cleanTool))
check("(a) argv = `--folder <目录>`",
      DeleteTempFeature.arguments(folderPath: "/期 90") == ["--folder", "/期 90"])
check("(a) 从 convert.py 的目录里找到 cleantemp.py",
      DeleteTempFeature.locateTool(convertTool: convertTool) == cleanTool)
check("(a) 反例：引擎没找到 → nil", DeleteTempFeature.locateTool(convertTool: nil) == nil)
check("(a) 反例：目录里没有该入口 → nil",
      DeleteTempFeature.locateTool(convertTool: "/tmp/不存在/convert.py") == nil)
// 第 98 条的关键：Swift 侧不该再有一份删除清单 / 遍历逻辑
let cleanSrc = readSource("Features/DeleteTempFeature.swift")
check("(a) Swift 侧**不再自带**后缀清单（`suffixes = [...]` 已随逻辑删掉）",
      !cleanSrc.contains("static let suffixes"))
check("(a) Swift 侧**不再**直接删文件（没有 `removeItem`）", !cleanSrc.contains("removeItem"))
check("(a) 界面调用点未变（`SimpleTab` 里仍是 `DeleteTempFeature.run(folderPath:log:)`）",
      readSource("Views/MainWindow.swift").contains("DeleteTempFeature.run(folderPath: path, log: logger)"))

// ── (b) 编译全部 ─────────────────────────────────────────────────────────
print("\n── (b) 编译全部 → compileall.py ──")
let compileTool = toolbox.appendingPathComponent("Engine/compileall.py").path
check("(b) 生产源码里的 Engine/compileall.py 真的在", FileManager.default.fileExists(atPath: compileTool))
check("(b) argv = `--folder <目录>`",
      CompileFeature.arguments(folderPath: "/期 90") == ["--folder", "/期 90"])
check("(b) `only` 非空 → 追加 `--only`",
      CompileFeature.arguments(folderPath: "/期 90", only: "01,03")
      == ["--folder", "/期 90", "--only", "01,03"])
check("(b) `only` 空白 → **不传** `--only`（编全部）",
      !CompileFeature.arguments(folderPath: "/期 90", only: "   ").contains("--only"))
check("(b) 从 convert.py 的目录里找到 compileall.py",
      CompileFeature.locateTool(convertTool: convertTool) == compileTool)
check("(b) 反例：引擎没找到 → nil", CompileFeature.locateTool(convertTool: nil) == nil)
let compileSrc = readSource("Features/CompileFeature.swift")
// ⚠️ 别用 `contains("xelatex")` 这种粗判据：新代码里 `Shell.locate("xelatex")` 也含它。
//    要锁的是"四步编译"的**实质特征**：Step 数组与 xelatex 的实参。
check("(b) Swift 侧**不再自己拼四步编译**（没有 Step 数组、没有 xelatex 实参）",
      !compileSrc.contains("Step(") && !compileSrc.contains("interaction=nonstopmode"))
check("(b) Swift 侧**仍先检查 xelatex 在不在**（缺 TeX 时给出人话提示，别让引擎报错）",
      compileSrc.contains("Shell.locate(\"xelatex\")"))
check("(b) 界面调用点未变（`SimpleTab` 里仍是 `CompileFeature.run(folderPath:log:)`）",
      readSource("Views/MainWindow.swift").contains("CompileFeature.run(folderPath: path, log: logger)"))

// ── (b2) 合并文件 ────────────────────────────────────────────────────────
print("\n── (b2) 合并文件 → mergepdf.py ──")
let mergeTool = toolbox.appendingPathComponent("Engine/mergepdf.py").path
check("(b2) 生产源码里的 Engine/mergepdf.py 真的在", FileManager.default.fileExists(atPath: mergeTool))
check("(b2) 三个开关都不勾 → 只有 `--folder`（与老版一致）",
      MergeFeature.arguments(folderPath: "/期 90") == ["--folder", "/期 90"])
check("(b2) 三个开关全勾 → 三个 flag",
      MergeFeature.arguments(folderPath: "/期 90", nestOutlines: true,
                             mergeEditorsPage: true, appendBackCover: true)
      == ["--folder", "/期 90", "--nest-outlines", "--editors-page", "--back-cover"])
check("(b2) 只勾封底 → 只有 `--back-cover`",
      MergeFeature.arguments(folderPath: "/期 90", appendBackCover: true)
      == ["--folder", "/期 90", "--back-cover"])
check("(b2) 从 convert.py 的目录里找到 mergepdf.py",
      MergeFeature.locateTool(convertTool: convertTool) == mergeTool)
check("(b2) 反例：引擎没找到 → nil", MergeFeature.locateTool(convertTool: nil) == nil)

// 探测 JSON 的解码（字段名必须与引擎 `--detect` 逐字对应）
let probeJSON = """
{"editorsPage": {"fileName": "cneditors[260113]左移.pdf", "pageCount": 1, "matchedBy": "cneditor 前缀"},
 "others": ["编委页1.pdf"], "backCover": null, "coverPages": 2}
"""
if let data = probeJSON.data(using: .utf8),
   let probe = try? JSONDecoder().decode(MergeFeature.Probe.self, from: data) {
    check("(b2) Probe 解码：编委页文件名/页数/匹配依据", probe.editorsPage?.fileName == "cneditors[260113]左移.pdf"
          && probe.editorsPage?.pageCount == 1 && probe.editorsPage?.matchedBy == "cneditor 前缀")
    check("(b2) Probe 解码：`others` 非空即「有歧义」", probe.isAmbiguous && probe.others == ["编委页1.pdf"])
    check("(b2) Probe 解码：封底页为 null 时不报错", probe.backCover == nil)
    check("(b2) Probe 解码：扉页页数", probe.coverPages == 2)
    check("(b2) 描述串与引擎日志同款", probe.editorsPage?.fullDescription
          == "cneditors[260113]左移.pdf（1 页，cneditor 前缀）")
} else {
    check("(b2) Probe 能解出引擎 `--detect` 的 JSON", false, "解码失败")
}
let mergeSrc = readSource("Features/MergeFeature.swift")
check("(b2) Swift 侧**不再自带**合并实现（没有 PDFMerger / 自己写 PDF 大纲）",
      !mergeSrc.contains("PDFMerger") && !mergeSrc.contains("PDFDocument"))
// 第 99 条删掉的三份 Swift 逻辑
for gone in ["Core/PDFMerger.swift", "Core/ExtraPages.swift", "Core/EditorsPage.swift"] {
    check("(b2) 已删除 `\(gone)`（逻辑唯一一份在引擎）",
          !FileManager.default.fileExists(atPath: sources.appendingPathComponent(gone).path))
}
check("(b2) 界面调用点未变（`MergeFeature.run(folderPath:log:nestOutlines:mergeEditorsPage:appendBackCover:)`）",
      readSource("Views/MergeTab.swift").contains("MergeFeature.run(folderPath: path,"))
check("(b2) 界面预览走引擎探测（`MergeFeature.detect`），不再自查文件",
      readSource("Core/ViewModels.swift").contains("MergeFeature.detect(folderPath: trimmed)")
      && !readSource("Views/MergeTab.swift").contains("EditorsPageDetector"))

// ── (c) CLI 转发 ─────────────────────────────────────────────────────────
print("\n── (c) Swift CLI ──")
let cli = readSource("../tools/cli/main.swift")
check("(c) `deletetemp` 已转发引擎", cli.contains("DeleteTempFeature.locateTool()"))
check("(c) `compile` 已转发引擎", cli.contains("CompileFeature.locateTool()"))
check("(c) `merge` 已转发引擎", cli.contains("MergeFeature.locateTool()"))
check("(c) `detect-editors` 已转发引擎（`MergeFeature.detect`）", cli.contains("MergeFeature.detect(folderPath: args[1])"))

print("\nSUMMARY: \(pass) PASS / \(fail) FAIL")
exit(fail == 0 ? 0 : 1)
