import Foundation

/// 「生成目录」—— 调引擎的 `Engine/toc.py`（第 95 条：mac 界面切换到引擎）。
///
/// **逻辑不在 Swift 里**：报告解析、目录条目生成、模板继承、落位、编译与页数适配全部在
/// `Engine/slcn/toc.py` + `slcn/toctemplate.py`（B-226 起由引擎统一做「一键最终交付」）。
/// 这一页只做四件事：
/// 选报告、把界面上的本期设置写进 `目录设置.json`、把引擎输出流到日志、
/// 把引擎写回的**结果 JSON**（4 个固定名路径 / 模板来源 / 页数 / 注意事项）显示出来。
enum TocFeature {

    /// 拼命令行参数。**纯函数**（`tools/toc_test` 直接断言它）。
    ///
    /// 默认 = 一键最终交付：引擎生成 tex → 缺 `000 Table of Contents` 自动创建 →
    /// 落位成固定名 → `xelatex ×2` 出 PDF（落位与编译逻辑都在引擎里，外壳不重写）。
    /// 高级开关「仅生成 tex（不落位）」打开时退回旧行为：**只传 `--report`**。
    ///
    /// 注意：本期设置不通过命令行传，而是由界面先写进报告同目录的 `目录设置.json`
    /// —— 引擎读的就是那个文件，两边只有一个真相来源。
    static func arguments(reportPath: String,
                          templateFolder: String = "",
                          textOnly: Bool = false,
                          resultJSONPath: String = "") -> [String] {
        var args = ["--report", reportPath]
        let template = templateFolder.trimmingCharacters(in: .whitespaces)
        if !template.isEmpty {
            args += ["--template", template]
        }
        guard !textOnly else { return args }
        args += ["--place", "--compile"]
        let resultPath = resultJSONPath.trimmingCharacters(in: .whitespaces)
        if !resultPath.isEmpty {
            args += ["--result-json", resultPath]
        }
        return args
    }

    /// 每次运行一个新的结果 JSON 临时路径（读完即删；产物不落在稿件目录里）。
    static func newResultJSONPath() -> String {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("slcn-toc-\(UUID().uuidString).json").path
    }

    // MARK: - 引擎结果 JSON（外壳只显示，不判来源）

    /// `toc.py --result-json` 的契约（字段全部按「可能缺」解析：旧引擎/半途失败都不崩）。
    ///
    /// 模板来源**只认引擎返回的 `templateSource` / `templateNote`** ——
    /// 界面不再自己按目录猜「本期/上一期/内置」，否则两处口径迟早对不上。
    struct Result: Decodable {
        struct Placed: Decodable {
            let zhTex: String?
            let zhPdf: String?
            let enTex: String?
            let enPdf: String?
        }
        struct Pages: Decodable {
            let zh: Int?
            let en: Int?
        }
        struct Column: Decodable {
            let enabled: Bool?
            let title: String?
        }
        struct ManualEdit: Decodable {
            let kind: String?
            let article: String?
            let note: String?
        }
        let templateSource: String?
        let templateNote: String?
        let tocDir: String?
        let placed: Placed?
        let compiled: Bool?
        let pages: Pages?
        let column: Column?
        let manualEdits: [ManualEdit]?
        let warnings: [String]?

        /// 4 个固定名产物路径（有就列；缺的字段不显示，不猜路径）。
        var placedPaths: [String] {
            guard let placed else { return [] }
            return [placed.zhTex, placed.zhPdf, placed.enTex, placed.enPdf]
                .compactMap { value -> String? in
                    guard let text = value?.trimmingCharacters(in: .whitespaces), !text.isEmpty
                    else { return nil }
                    return text
                }
        }
    }

    /// 解析引擎写的结果 JSON。**纯函数**（`tools/toc_test` 用契约样例断言）。
    static func decodeResult(from data: Data) -> Result? {
        try? JSONDecoder().decode(Result.self, from: data)
    }

    /// 读结果 JSON；文件不存在/不是 JSON（例如引擎 rc=2 没写）→ nil。
    static func loadResult(at path: String) -> Result? {
        guard !path.isEmpty,
              let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else { return nil }
        return decodeResult(from: data)
    }

    /// 结果区要显示的行（**纯函数**）。只把引擎给的事实翻译成人话，不在这里判断任何来源。
    static func displayLines(for result: Result) -> [String] {
        var lines: [String] = []

        let source = result.templateSource?.trimmingCharacters(in: .whitespaces) ?? ""
        if !source.isEmpty {
            lines.append("模板来源：\(source)")
        }
        if let note = result.templateNote?.trimmingCharacters(in: .whitespaces), !note.isEmpty {
            lines.append(note)
        }

        if let placed = result.placed {
            func append(_ label: String, _ path: String?) {
                guard let text = path?.trimmingCharacters(in: .whitespaces), !text.isEmpty
                else { return }
                lines.append("\(label)：\(text)")
            }
            append("中文目录 PDF", placed.zhPdf)
            append("中文目录 tex", placed.zhTex)
            append("英文目录 PDF", placed.enPdf)
            append("英文目录 tex", placed.enTex)
        }

        if let pages = result.pages {
            var parts: [String] = []
            if let zh = pages.zh { parts.append("中文 \(zh) 页") }
            if let en = pages.en { parts.append("英文 \(en) 页") }
            if !parts.isEmpty {
                lines.append("页数：" + parts.joined(separator: "／"))
            }
        }

        if let column = result.column, column.enabled == true {
            let title = column.title?.trimmingCharacters(in: .whitespaces) ?? ""
            lines.append("栏目：" + (title.isEmpty ? "已启用" : title))
        }

        for edit in result.manualEdits ?? [] {
            let kind = edit.kind?.trimmingCharacters(in: .whitespaces) ?? ""
            var text = "需人工核对" + (kind.isEmpty ? "" : "（\(kind)）")
            if let article = edit.article?.trimmingCharacters(in: .whitespaces), !article.isEmpty {
                text += "：\(article)"
            }
            if let note = edit.note?.trimmingCharacters(in: .whitespaces), !note.isEmpty {
                text += " — \(note)"
            }
            lines.append(text)
        }

        for warning in result.warnings ?? [] {
            let text = warning.trimmingCharacters(in: .whitespaces)
            if !text.isEmpty { lines.append("⚠️ \(text)") }
        }
        return lines
    }

    /// 失败时从引擎输出里挑「可操作原因」：退出码 2 的原因由引擎写在 stderr，
    /// `Shell.run` 把 stdout/stderr 合在一起流给日志，这里取最后几条报错行。
    ///
    /// **纯函数**（`tools/toc_test` 断言）；找不到报错行就返回空串，界面显示「见运行日志」。
    static func failureReason(from output: String) -> String {
        let needles = ["❌", "错误", "失败", "找不到", "缺少", "未找到", "Error", "error:", "Traceback"]
        let lines = output.split(whereSeparator: \.isNewline).map(String.init)
        var picked: [String] = []
        for index in stride(from: lines.count - 1, through: 0, by: -1) {
            let trimmed = lines[index].trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty { continue }
            guard needles.contains(where: { trimmed.contains($0) }) else { continue }
            if picked.first != trimmed { picked.insert(trimmed, at: 0) }
            // 紧跟报错行的一行往往是补救办法（例如「请安装字体后重试。」），一并显示。
            if index + 1 < lines.count {
                let next = lines[index + 1].trimmingCharacters(in: .whitespaces)
                if !next.isEmpty && !picked.contains(next) { picked.append(next) }
            }
            if picked.count >= 3 { break }
        }
        return picked.joined(separator: "\n")
    }

    // MARK: - 预览

    /// 报告预览（篇目清单 + 模板来源）。**由引擎算**，不在这里重写一份解析逻辑 ——
    /// 否则会出现「清单里勾了 3 篇、生成时只认 2 篇」这种对不上。
    struct Preview: Decodable {
        struct Article: Decodable {
            let name: String
            let title: String
        }
        let articles: [Article]
        let templateNote: String
    }

    /// 找 `toc.py`：引擎目录由 `EngineLocator` 统一解析（用户指定 → app 包内 → 往上找仓库）。
    static func locateTool(convertTool: String? = EngineLocator.convertTool()) -> String? {
        EngineLocator.tool(named: "toc.py", convertTool: convertTool)
    }

    /// 向引擎要预览。失败返回 nil（界面显示空清单）。
    static func preview(reportPath: String) -> Preview? {
        guard let tool = locateTool() else { return nil }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else { return nil }

        guard let result = Shell.capture(py,
                                         [tool, "--report", reportPath, "--preview"],
                                         workingDirectory: toolDir,
                                         environment: EngineLocator.environment(toolDir: toolDir)),
              result.succeeded else { return nil }
        // 引擎的 stdout 里只有一行 JSON；保险起见取最后一个非空行
        let line = result.output.split(separator: "\n").last.map(String.init) ?? result.output
        guard let data = line.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(Preview.self, from: data)
    }

    // MARK: - 运行

    /// 一次运行的结果：退出码 + 引擎结果 JSON（可能没有）。
    struct RunOutcome {
        let status: Int32
        let result: Result?

        var cancelled: Bool { status == Shell.cancelledStatus }
        /// 退出码 1 = 有需人工核对项（`ENGINE_API` 三.2：不许显示成失败）。
        var needsReview: Bool { status == 1 }
    }

    /// 跑引擎。返回退出码与结果 JSON；日志实时写进 `log`（含引擎 stderr）。
    @discardableResult
    static func run(reportPath: String,
                    templateFolder: String,
                    textOnly: Bool = false,
                    resultJSONPath: String = "",
                    log: Logger) -> RunOutcome {
        let target = reportPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !target.isEmpty else {
            log("请先选择检查报告（信息检查<时间戳>.txt）。\n")
            return RunOutcome(status: 2, result: nil)
        }
        guard FileManager.default.fileExists(atPath: target) else {
            log("找不到文件：\(target)\n")
            return RunOutcome(status: 2, result: nil)
        }
        guard let tool = locateTool() else {
            log("未找到目录引擎 Engine/toc.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/toc.py"
                + "（官方 ./build.sh 会把它打包进去）；")
            log("② 从 app 所在目录逐级向上找 Engine/toc.py\n")
            return RunOutcome(status: 2, result: nil)
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return RunOutcome(status: 2, result: nil)
        }

        log("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n")
        log("生成目录（\(textOnly ? "仅生成 tex，不落位/不编译" : "生成并交付")）\n")
        log("  工具   \(tool)\n")
        log("  解释器 \(py)\n")
        log("  报告   \(target)\n")
        if !textOnly {
            log("  交付   000 Table of Contents（缺则自动创建；落位固定名并编译 PDF）\n")
        }
        log("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n\n")

        let status = Shell.run(
            py,
            [tool] + arguments(reportPath: target, templateFolder: templateFolder,
                               textOnly: textOnly, resultJSONPath: resultJSONPath),
            workingDirectory: toolDir,
            environment: EngineLocator.environment(toolDir: toolDir),
            log: log
        )

        log("\n")
        // U3：用户取消 → 只说「已取消」，不许讲成失败（可能一份目录都没写，或只写了一半）
        if status == Shell.cancelledStatus {
            log("⏹ 已取消（用户终止；目录可能未生成或只写了一半，需要时重新运行）。\n")
            return RunOutcome(status: status, result: nil)
        }

        let result = loadResult(at: resultJSONPath)
        if let result {
            for line in displayLines(for: result) {
                log("  \(line)\n")
            }
            log("\n")
        }
        switch status {
        case 0:
            if textOnly {
                log("✅ 完成。中英文目录 .tex 已生成（与报告同目录；未落位、未编译）。\n")
            } else {
                log("✅ 完成。目录已落位到 000 Table of Contents 并编译成 PDF。\n")
            }
        case 1:
            // 1 = 有需人工核对项（只生成了一份 / 报告不在期目录里导致「总第 ? 期」等）
            log("⚠️ 已完成，但有需要人工核对的地方（见上面的日志）。\n")
        case 2:
            log("❌ 生成失败（退出码 2，已中止；没有落位/编译不完整的产物）。\n")
            log("   原因见上方引擎报错；处理后可重试。\n")
        default:
            log("❌ 生成失败（退出码 \(status)）。请把上面的报错发给技术人员。\n")
        }
        return RunOutcome(status: status, result: result)
    }
}
