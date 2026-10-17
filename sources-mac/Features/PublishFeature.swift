import Foundation

/// 「本期出刊」向导：**一个入口**跑完四步（UI 方案候选 C，2026-10-17 用户拍板放引擎侧）。
///
/// 编排（失败中止 / 退出码 1 = 橙色警告继续 / 可单步重跑）全在引擎 `publish.py` 里，
/// 判据只有一份 —— 两个外壳都不许各写一遍（项目红线：一个引擎、两个外壳）。
enum PublishFeature {

    /// 拼命令行参数。**纯函数**（`tools/cutover_test` 直接断言它）。
    static func arguments(folderPath: String) -> [String] {
        ["--folder", folderPath]
    }

    /// 找 `publish.py`（引擎目录由 `EngineLocator` 统一解析）。
    static func locateTool(convertTool: String? = EngineLocator.convertTool()) -> String? {
        EngineLocator.tool(named: "publish.py", convertTool: convertTool)
    }

    static func run(folderPath rawPath: String, log: Logger) {
        let path = PathKit.normalize(rawPath)
        log("本期出刊向导\n")
        log("路径：\(path)\n")
        guard PathKit.isDirectory(path) else {
            log("路径不是文件夹：\(path)\n")
            log("程序错误\n")
            return
        }
        guard let tool = locateTool() else {
            log("未找到出刊编排引擎 Engine/publish.py。\n")
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
        // U3：用户取消 → 如实说明「只跑到一半」。前序步骤的产物保留（引擎侧原子写，不会写坏）
        if status == Shell.cancelledStatus {
            log("⏹ 已取消（用户终止；本期出刊只跑到一半，前序步骤的产物保留，需要时重新运行）。\n")
        }
    }
}
