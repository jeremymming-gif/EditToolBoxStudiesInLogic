import Foundation

/// 「合并文件」—— 第 99 条：改调引擎 `Engine/mergepdf.py`。
///
/// 合并顺序与规则都在引擎里（`slcn/pdfmerge.py`，其上游是原版 `merge.py`）：
/// 扉页 → 编委信息页（可选）→ 中文目录 → 英文目录 → 各篇文章（按序号）→ 封底（可选），
/// 并在大纲里为每篇登记书签；文章 PDF 找不到时报出来而不是整篇跳过
/// （原版会静默跳过，实测丢过 33 页）。
///
/// 这一页只做两件事：
///   ① **运行前预览** —— 问引擎 `--detect`「会插哪一份编委页/封底页」，让你点「运行」前就知道结果；
///   ② 运行 —— 把引擎输出流到日志。
///
/// 识别逻辑**不在 Swift 里**：原来 `EditorsPage.swift`（按文件名识别）与 `ExtraPages.swift`
/// （封底页）各有一份，现在只剩引擎一份 —— 预览与真正合并必然一致。
enum MergeFeature {

    /// 引擎探测到的附加页（编委页 / 封底页）。
    struct ExtraPageInfo: Decodable {
        let fileName: String
        let pageCount: Int
        let matchedBy: String

        /// 与引擎日志同款的一句话描述
        var fullDescription: String { "\(fileName)（\(pageCount) 页，\(matchedBy)）" }
    }

    /// 运行前预览（字段名与引擎 `--detect` 的 JSON **逐字对应**）。
    struct Probe: Decodable {
        let editorsPage: ExtraPageInfo?
        let others: [String]
        let backCover: ExtraPageInfo?
        let coverPages: Int

        var isAmbiguous: Bool { !others.isEmpty }
    }

    /// 拼命令行参数。**纯函数**（`tools/cutover_test` 直接断言它）。
    static func arguments(folderPath: String,
                          nestOutlines: Bool = false,
                          mergeEditorsPage: Bool = false,
                          appendBackCover: Bool = false) -> [String] {
        var args = ["--folder", folderPath]
        if nestOutlines { args.append("--nest-outlines") }
        if mergeEditorsPage { args.append("--editors-page") }
        if appendBackCover { args.append("--back-cover") }
        return args
    }

    /// 找 `mergepdf.py`（引擎目录由 `EngineLocator` 统一解析）。
    static func locateTool(convertTool: String? = EngineLocator.convertTool()) -> String? {
        EngineLocator.tool(named: "mergepdf.py", convertTool: convertTool)
    }

    /// 运行前预览：问引擎「会插哪一份编委页/封底页、扉页有几页」。失败返回 nil（界面显示"未找到"）。
    static func detect(folderPath: String) -> Probe? {
        let path = PathKit.normalize(folderPath)
        guard PathKit.isDirectory(path), let tool = locateTool() else { return nil }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else { return nil }
        guard let result = Shell.capture(py,
                                         [tool] + arguments(folderPath: path) + ["--detect"],
                                         workingDirectory: toolDir,
                                         environment: EngineLocator.environment(toolDir: toolDir)),
              result.succeeded else { return nil }
        let line = result.output.split(separator: "\n").last.map(String.init) ?? result.output
        guard let data = line.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(Probe.self, from: data)
    }

    static func run(folderPath rawPath: String,
                    log: Logger,
                    nestOutlines: Bool = false,
                    mergeEditorsPage: Bool = false,
                    appendBackCover: Bool = false) {
        let path = PathKit.normalize(rawPath)
        log("运行合并文件的函数\n")
        log("文件夹路径：\(path)\n")

        guard PathKit.isDirectory(path) else {
            log("路径不是文件夹：\(path)\n")
            log("程序错误\n")
            return
        }
        guard let tool = locateTool() else {
            log("未找到合并引擎 Engine/mergepdf.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/mergepdf.py；")
            log("② 从 app 所在目录逐级向上找 Engine/mergepdf.py\n")
            return
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return
        }

        let status = Shell.run(
            py,
            [tool] + arguments(folderPath: path,
                               nestOutlines: nestOutlines,
                               mergeEditorsPage: mergeEditorsPage,
                               appendBackCover: appendBackCover),
            workingDirectory: toolDir,
            environment: EngineLocator.environment(toolDir: toolDir),
            log: log
        )
        log("\n")
        // U3：用户取消 → 只说「已取消」，不许讲成失败
        if status == Shell.cancelledStatus {
            log("⏹ 已取消（用户终止；合并可能只写了一部分，需要时重新运行）。\n")
            return
        }
        if status == 0 {
            log("运行结束，请确认上方是否有错。\n")
        } else {
            log("❌ 合并失败（退出码 \(status)）。\n")
        }
    }
}
