import Foundation

/// 「删除临时文件」—— 第 98 条：改调引擎 `Engine/cleantemp.py`。
///
/// 删哪些由引擎的 `slcn/clean.py` 决定（`.aux .bbl .bcf .blg .out .run.xml .synctex.gz`
/// 与 macOS 的 `.DS_Store`；**不删** `.tex / .pdf / .sty / .bib / .log`）——
/// 这份清单原来在 Swift 里另有一份，属于"两套实现"，现在只剩引擎一份。
///
/// 界面（`MainWindow` 的「删除临时文件」页）走的还是同一个
/// `SimpleTab { path, logger in DeleteTempFeature.run(...) }`，签名特意保持不变。
enum DeleteTempFeature {

    /// 拼命令行参数。**纯函数**（`tools/cutover_test` 直接断言它）。
    static func arguments(folderPath: String) -> [String] {
        ["--folder", folderPath]
    }

    /// 找 `cleantemp.py`（引擎目录由 `EngineLocator` 统一解析）。
    static func locateTool(convertTool: String? = EngineLocator.convertTool()) -> String? {
        EngineLocator.tool(named: "cleantemp.py", convertTool: convertTool)
    }

    static func run(folderPath rawPath: String, log: Logger) {
        let path = PathKit.normalize(rawPath)
        log("删除临时文件\n")
        log("路径：\(path)\n")

        guard PathKit.isDirectory(path) else {
            log("路径不是文件夹：\(path)\n")
            log("程序错误\n")
            return
        }
        guard let tool = locateTool() else {
            log("未找到清理引擎 Engine/cleantemp.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/cleantemp.py；")
            log("② 从 app 所在目录逐级向上找 Engine/cleantemp.py\n")
            return
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return
        }

        let status = Shell.run(
            py,
            [tool] + arguments(folderPath: path),
            workingDirectory: toolDir,
            environment: EngineLocator.environment(toolDir: toolDir),
            log: log
        )
        if status != 0 {
            log("❌ 清理失败（退出码 \(status)）。\n")
        }
    }
}
