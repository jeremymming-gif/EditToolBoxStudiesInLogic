import Foundation

/// 「编译全部」—— 第 98 条：改调引擎 `Engine/compileall.py`。
///
/// 规则在引擎里：对期目录下每个编号子文件夹（跳过 `000 目录`）执行
/// `xelatex → biber -l=zh_pinyin → xelatex → xelatex`；**某一篇失败只停那一篇**，
/// 并把该篇 `.log` 里的 `!` 错误摘要回显出来（原版把输出丢进 NUL，出错时什么都看不到）。
///
/// 这里只做三件事：检查路径、拼参数、把引擎输出流到日志。
/// 界面（`MainWindow` 的「编译全部」页）仍用 `SimpleTab { path, logger in CompileFeature.run(...) }`。
enum CompileFeature {

    /// 拼命令行参数。**纯函数**（`tools/cutover_test` 直接断言它）。
    static func arguments(folderPath: String, only: String = "") -> [String] {
        var args = ["--folder", folderPath]
        if !only.trimmingCharacters(in: .whitespaces).isEmpty {
            args += ["--only", only.trimmingCharacters(in: .whitespaces)]
        }
        return args
    }

    /// 找 `compileall.py`（引擎目录由 `EngineLocator` 统一解析）。
    static func locateTool(convertTool: String? = EngineLocator.convertTool()) -> String? {
        EngineLocator.tool(named: "compileall.py", convertTool: convertTool)
    }

    static func run(folderPath rawPath: String, log: Logger) {
        let path = PathKit.normalize(rawPath)
        log("运行全编译的函数\n")
        log("文件夹路径：\(path)\n")

        guard PathKit.isDirectory(path) else {
            log("路径不是文件夹：\(path)\n")
            log("程序错误\n")
            return
        }
        guard let tool = locateTool() else {
            log("未找到编译引擎 Engine/compileall.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/compileall.py；")
            log("② 从 app 所在目录逐级向上找 Engine/compileall.py\n")
            return
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return
        }
        guard Shell.locate("xelatex") != nil else {
            log("未找到命令 xelatex，请确认已安装 TeX Live / MacTeX。\n")
            return
        }

        let status = Shell.run(
            py,
            [tool] + arguments(folderPath: path),
            workingDirectory: toolDir,
            environment: EngineLocator.environment(toolDir: toolDir),
            log: log
        )
        log("\n")
        // U3：用户取消 → 只说「已取消」，不许讲成失败（已编完的篇目仍保留 PDF）
        if status == Shell.cancelledStatus {
            log("⏹ 已取消（用户终止；已编译完的篇目仍保留 PDF，需要时重新运行）。\n")
            return
        }
        switch status {
        case 0:
            log("运行结束，请确认上方是否有错。\n")
        case 1:
            // 引擎的约定：1 = 有文章编译失败（已逐篇回显错误摘要），不是启动失败
            log("⚠️ 有文章编译失败（上面每篇都给了 `.log` 里的错误摘要）。\n")
        default:
            log("❌ 编译失败（退出码 \(status)）。\n")
        }
    }
}
