import Foundation

/// 「生成目录」—— 调引擎的 `Engine/toc.py`（第 95 条：mac 界面切换到引擎）。
///
/// **逻辑不在 Swift 里**：报告解析、目录条目生成、模板继承与表头改写全部在
/// `Engine/slcn/toc.py` + `slcn/toctemplate.py`。这一页只做三件事：
/// 选报告、把界面上的本期设置写进 `目录设置.json`、把引擎输出流到日志。
///
/// 切换前做过逐字节差分：同一份报告，Swift 那份与引擎那份产出的
/// `Chinese Content_*.tex` / `contents_*.tex` **完全相同**（第 94 条），
/// 所以这次切换是行为等价的。
enum TocFeature {

    /// 拼命令行参数。**纯函数**（`tools/toc_test` 直接断言它）。
    ///
    /// 注意：本期设置不通过命令行传，而是由界面先写进报告同目录的 `目录设置.json`
    /// —— 引擎读的就是那个文件，两边只有一个真相来源。
    static func arguments(reportPath: String, templateFolder: String = "") -> [String] {
        var args = ["--report", reportPath]
        if !templateFolder.trimmingCharacters(in: .whitespaces).isEmpty {
            args += ["--template", templateFolder]
        }
        return args
    }

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
                                         [tool] + arguments(reportPath: reportPath) + ["--preview"],
                                         workingDirectory: toolDir,
                                         environment: EngineLocator.environment(toolDir: toolDir)),
              result.succeeded else { return nil }
        // 引擎的 stdout 里只有一行 JSON；保险起见取最后一个非空行
        let line = result.output.split(separator: "\n").last.map(String.init) ?? result.output
        guard let data = line.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(Preview.self, from: data)
    }

    static func run(reportPath: String, templateFolder: String, log: Logger) {
        let target = reportPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !target.isEmpty else {
            log("请先选择检查报告（信息检查<时间戳>.txt）。\n")
            return
        }
        guard FileManager.default.fileExists(atPath: target) else {
            log("找不到文件：\(target)\n")
            return
        }
        guard let tool = locateTool() else {
            log("未找到目录引擎 Engine/toc.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/toc.py"
                + "（官方 ./build.sh 会把它打包进去）；")
            log("② 从 app 所在目录逐级向上找 Engine/toc.py\n")
            return
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return
        }

        log("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n")
        log("生成目录\n")
        log("  工具   \(tool)\n")
        log("  解释器 \(py)\n")
        log("  报告   \(target)\n")
        log("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n\n")

        let status = Shell.run(
            py,
            [tool] + arguments(reportPath: target, templateFolder: templateFolder),
            workingDirectory: toolDir,
            environment: EngineLocator.environment(toolDir: toolDir),
            log: log
        )

        log("\n")
        // U3：用户取消 → 只说「已取消」，不许讲成失败（可能一份目录都没写，或只写了一份）
        if status == Shell.cancelledStatus {
            log("⏹ 已取消（用户终止；目录可能未生成或只写了一半，需要时重新运行）。\n")
            return
        }
        switch status {
        case 0:
            log("✅ 完成。中英文目录都已生成（与报告同目录）。\n")
        case 1:
            log("⚠️ 只生成了一份目录，请看上面的日志确认原因。\n")
        case -1:
            log("❌ 启动失败。\n")
        default:
            log("❌ 生成失败（退出码 \(status)）。请把上面的报错发给技术人员。\n")
        }
    }
}
