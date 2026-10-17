import AppKit
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
    ///
    /// 默认 `trash: true` —— 走引擎 2026-10-17 新增的**移入回收目录**模式（可撤销）；
    /// `trash: false` 是老的"直接删"，只有需要兼容老行为时才用。
    static func arguments(folderPath: String, trash: Bool = true) -> [String] {
        trash ? ["--folder", folderPath, "--trash"] : ["--folder", folderPath]
    }

    /// 撤销：把某个回收目录里的文件搬回去。**纯函数**（cutover 断言）。
    static func restoreArguments(folderPath: String, trashDir: String) -> [String] {
        ["--folder", folderPath, "--restore", trashDir]
    }

    /// 最近一次「移入回收」建出来的回收目录（`.toolbox-trash/<时间戳>`），供「撤销」按钮用。
    /// 找不到返回 nil（那次没清理任何文件）。
    static func lastTrashDir(folderPath: String) -> String? {
        let trashRoot = (folderPath as NSString).appendingPathComponent(".toolbox-trash")
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: trashRoot) else { return nil }
        let dirs = names.compactMap { name -> (String, Date)? in
            let full = (trashRoot as NSString).appendingPathComponent(name)
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: full, isDirectory: &isDir), isDir.boolValue else { return nil }
            let mtime = (try? fm.attributesOfItem(atPath: full)[.modificationDate] as? Date) ?? Date.distantPast
            return (full, mtime)
        }
        return dirs.max(by: { $0.1 < $1.1 })?.0
    }

    /// 跑一次 cleantemp（公共部分：找工具 / 找 python / 跑子进程）。
    private static func runTool(_ args: [String], log: Logger) {
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
        _ = Shell.run(
            py,
            [tool] + args,
            workingDirectory: toolDir,
            environment: EngineLocator.environment(toolDir: toolDir),
            log: log
        )
    }

    /// 撤销上一次清理：把回收目录里的文件搬回原处（引擎侧保证**不覆盖**已存在的文件）。
    static func restore(folderPath rawPath: String, trashDir: String, log: Logger) {
        let path = PathKit.normalize(rawPath)
        log("\n撤销清理（从回收目录搬回）\n")
        log("路径：\(path)\n")
        guard PathKit.isDirectory(path) else {
            log("路径不是文件夹：\(path)\n")
            return
        }
        guard FileManager.default.fileExists(atPath: trashDir) else {
            log("回收目录不存在：\(trashDir)\n")
            return
        }
        runTool(restoreArguments(folderPath: path, trashDir: trashDir), log: log)
        log("完成。\n")
    }

    /// 确认框：说清「删什么 / 不碰什么 / 可撤销」，按钮用动词（NN/g 确认框准则）。
    private static func confirmTrash(folder: String) -> Bool {
        func show() -> Bool {
            let alert = NSAlert()
            alert.messageText = "删除临时文件（可撤销）"
            alert.informativeText = """
            将把「\(folder)」里的编译中间文件移入同目录的 .toolbox-trash/（可撤销）。
            只处理 .aux/.bbl/.bcf/.blg/.out/.run.xml/.synctex.gz/.DS_Store；
            不会动 .tex/.pdf/.sty/.bib/.log，也不进 original/（作者来稿）。
            移入之后，可点横幅上的「撤销清理」还原。
            """
            alert.alertStyle = .warning
            alert.addButton(withTitle: "移入回收目录")
            alert.addButton(withTitle: "取消")
            return alert.runModal() == .alertFirstButtonReturn
        }
        if Thread.isMainThread { return show() }
        return DispatchQueue.main.sync(execute: show)
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
        // UI 方案第 3 件：先确认，再"移入回收目录"（可撤销）—— 不再直接删
        guard confirmTrash(folder: path) else {
            log("已取消（一个文件都没动）\n")
            return
        }
        runTool(arguments(folderPath: path), log: log)
        return
    }

    /// 老行为（**直接删**）：只在需要兼容时用，界面不再调用。
    static func runDeletingPermanently(folderPath rawPath: String, log: Logger) {
        let path = PathKit.normalize(rawPath)
        runTool(arguments(folderPath: path, trash: false), log: log)
    }

}
