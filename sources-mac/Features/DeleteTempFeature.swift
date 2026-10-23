import AppKit
import Foundation

/// 「删除临时文件」—— 第 98 条起改调引擎 `Engine/cleantemp.py`（B-211 扩三模式）。
///
/// 删哪些由引擎的 `slcn/clean.py` 决定，Swift 只负责「取值 → 拼参数 → 显示日志」：
/// - `.aux` 模式（默认）：清编译中间文件（`.aux .bbl .bcf .blg .out .run.xml .synctex.gz`
///   与 macOS 的 `.DS_Store`；**不删** `.tex / .pdf / .sty / .bib / .log`）。
/// - `.delivery` 模式：交付收尾，按 tex 引用闭包 + 白名单清理，引用缺失时引擎整体拒绝。
/// - `.workspace` 模式：工作区旧件，每期 `合并*.pdf` 只留最新一份。
///
/// 三种模式都**先入 `.toolbox-trash/`（可撤销）**，默认路径（不带 `--mode`）行为不变。
enum DeleteTempFeature {

    /// 清理模式。rawValue 就是传给引擎 `--mode` 的值；改这里等于改 CLI 契约。
    enum CleanMode: String, CaseIterable {
        case aux
        case delivery
        case workspace

        /// 界面 Picker 上的中文标题。
        var title: String {
            switch self {
            case .aux: return "编译临时文件"
            case .delivery: return "交付收尾（白名单）"
            case .workspace: return "工作区旧件（每期留最新合并 PDF）"
            }
        }

        /// Picker 下方的一句话说明（说清清什么 / 不碰什么 / 可撤销）。
        var detail: String {
            switch self {
            case .aux:
                return "清编译中间文件；不动 .tex/.pdf/.sty/.bib/.log，也不进 original/（作者来稿）。"
            case .delivery:
                return "交付前收尾：保留白名单与 tex 引用闭包，清 .ai前/.log/备份/核对报告/aux；引用缺失时引擎整批拒绝，一个文件都不动。"
            case .workspace:
                return "工作区旧件：每期只留最新一份「合并*.pdf」，固定交付物不动；解析不出时间时保守保留。"
            }
        }
    }

    /// 拼命令行参数。**纯函数**（`tools/cutover_test` 直接断言它）。
    ///
    /// 默认 `mode: .aux, trash: true` —— 走引擎的**移入回收目录**模式（可撤销），
    /// 与 B-211 之前逐字节一致；`trash: false` 是老的"直接删"，只在兼容老行为时用。
    /// 新模式（delivery/workspace）按契约**不带 `--trash`**：引擎侧默认就是先入回收站。
    static func arguments(folderPath: String, mode: CleanMode = .aux,
                          trash: Bool = true) -> [String] {
        switch mode {
        case .aux:
            return trash ? ["--folder", folderPath, "--trash"] : ["--folder", folderPath]
        case .delivery, .workspace:
            return ["--folder", folderPath, "--mode", mode.rawValue]
        }
    }

    /// 清空回收站：`--folder <目录> --empty-trash`（**不可撤销**，执行前另有确认框）。
    static func emptyTrashArguments(folderPath: String) -> [String] {
        ["--folder", folderPath, "--empty-trash"]
    }

    /// 撤销：把某个回收目录里的文件搬回去。**纯函数**（cutover 断言）。
    static func restoreArguments(folderPath: String, trashDir: String) -> [String] {
        ["--folder", folderPath, "--restore", trashDir]
    }

    /// 最近一次「移入回收」建出来的回收目录（`.toolbox-trash/<目录名>-<时间戳>`），
    /// 供「撤销」按钮兜底用。按 mtime 取最新，找不到返回 nil（那次没清理任何文件）。
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
    /// - Returns: 引擎退出码 0 = 正常结束；false 也可能是用户取消（日志已说明）。
    @discardableResult
    private static func runTool(_ args: [String], log: Logger) -> Bool {
        guard let tool = locateTool() else {
            log("未找到清理引擎 Engine/cleantemp.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/cleantemp.py；")
            log("② 从 app 所在目录逐级向上找 Engine/cleantemp.py\n")
            return false
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return false
        }
        let status = Shell.run(
            py,
            [tool] + args,
            workingDirectory: toolDir,
            environment: EngineLocator.environment(toolDir: toolDir),
            log: log
        )
        // U3：用户取消 → 如实说明「可能只做了一部分」，并提示撤销入口（不装成成功）
        if status == Shell.cancelledStatus {
            log("⏹ 已取消（用户终止；清理可能只做了一部分，可点横幅上的「撤销清理」还原）。\n")
            return false
        }
        return status == 0
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
        guard runTool(restoreArguments(folderPath: path, trashDir: trashDir), log: log) else { return }
        log("完成。\n")
    }

    /// 确认框：说清「删什么 / 不碰什么 / 可撤销」，按钮用动词（NN/g 确认框准则）。
    /// 文案按模式分开 —— 三种模式清掉的东西完全不同，不能共用一句含糊话。
    private static func confirm(folder: String, mode: CleanMode) -> Bool {
        let title: String
        let info: String
        switch mode {
        case .aux:
            title = "删除临时文件（可撤销）"
            info = """
            将把「\(folder)」里的编译中间文件移入同目录的 .toolbox-trash/（可撤销）。
            只处理 .aux/.bbl/.bcf/.blg/.out/.run.xml/.synctex.gz/.DS_Store；
            不会动 .tex/.pdf/.sty/.bib/.log，也不进 original/（作者来稿）。
            移入之后，可点横幅上的「撤销清理」还原。
            """
        case .delivery:
            title = "交付收尾清理（可撤销）"
            info = """
            将把「\(folder)」里的交付残留移入同目录的 .toolbox-trash/（可撤销），白名单外的一律不动。
            保留：*.tex/*.pdf/*.bib/*.sty/*.cls、tex 引用闭包（\\includegraphics/\\input/\\include/\\addbibresource/\\bibliography 指向的文件与目录）、original/、人工 *交付清单*.md。
            清理：.ai前/、.log/、*.bak、_AI*/、_文献核实.md、_回读校验报告.md、_参考文献核对.txt、_一校核查*.md、_原稿.pdf、aux 全套、.DS_Store、_待核/。
            引用缺失时引擎整体拒绝、一个文件都不动；未识别文件保留并给 warning。
            移入之后，可点横幅上的「撤销清理」还原。
            """
        case .workspace:
            title = "工作区旧件清理（可撤销）"
            info = """
            将把「\(folder)」里的工作区旧件移入同目录的 .toolbox-trash/（可撤销）。
            每期目录里的 合并*.pdf 只保留文件名时间最新的一份；固定名 合并.pdf 永不删。
            解析不出时间的唯一一份保留；多份都解析失败时全部保留并给 warning。
            固定交付物 000/、contents.*、Chinese Content.*、扉页/编委/封底/ 与人工文件永不删；
            另清 信息检查*/、*.bak/、*.coop-bak-*/、*_待核/、.DS_Store。
            移入之后，可点横幅上的「撤销清理」还原。
            """
        }
        func show() -> Bool {
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = info
            alert.alertStyle = .warning
            alert.addButton(withTitle: "移入回收目录")
            alert.addButton(withTitle: "取消")
            return alert.runModal() == .alertFirstButtonReturn
        }
        if Thread.isMainThread { return show() }
        return DispatchQueue.main.sync(execute: show)
    }

    /// 清空回收站的确认框：**不可撤销**，必须说清楚。
    private static func confirmEmptyTrash(folder: String) -> Bool {
        func show() -> Bool {
            let alert = NSAlert()
            alert.messageText = "清空回收站（不可撤销）"
            alert.informativeText = """
            将永久删除「\(folder)」下 .toolbox-trash/ 里的所有回收目录，清空后无法恢复。
            只影响回收站，不动期目录里的其它文件。
            还想撤销上一次清理的话，请先点「取消」，改用横幅上的「撤销清理」。
            """
            alert.alertStyle = .critical
            alert.addButton(withTitle: "永久清空")
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

    /// 按模式清理。默认 `.aux`，保持老调用兼容（旧签名 = `run(folderPath:log:)`）。
    static func run(folderPath rawPath: String, mode: CleanMode = .aux, log: Logger) {
        let path = PathKit.normalize(rawPath)
        if mode == .aux {
            log("删除临时文件\n")
        } else {
            log("删除临时文件（\(mode.title)）\n")
        }
        log("路径：\(path)\n")

        guard PathKit.isDirectory(path) else {
            log("路径不是文件夹：\(path)\n")
            log("程序错误\n")
            return
        }
        // 先确认，再"移入回收目录"（可撤销）—— 不直接删
        guard confirm(folder: path, mode: mode) else {
            log("已取消（一个文件都没动）\n")
            return
        }
        runTool(arguments(folderPath: path, mode: mode), log: log)
    }

    /// 清空回收站（不可撤销）。确认后调用，引擎自己的契约行如实写「清空了几个回收目录」。
    static func emptyTrash(folderPath rawPath: String, log: Logger) {
        let path = PathKit.normalize(rawPath)
        log("清空回收站\n")
        log("路径：\(path)\n")
        guard PathKit.isDirectory(path) else {
            log("路径不是文件夹：\(path)\n")
            log("程序错误\n")
            return
        }
        guard confirmEmptyTrash(folder: path) else {
            log("已取消（回收站没动）\n")
            return
        }
        if !runTool(emptyTrashArguments(folderPath: path), log: log) {
            log("清空回收站未完成（原因见上）。\n")
        }
    }

    /// 老行为（**直接删**）：只在需要兼容时用，界面不再调用。
    static func runDeletingPermanently(folderPath rawPath: String, log: Logger) {
        let path = PathKit.normalize(rawPath)
        runTool(arguments(folderPath: path, mode: .aux, trash: false), log: log)
    }
}
