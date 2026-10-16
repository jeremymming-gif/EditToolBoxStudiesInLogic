import Foundation

// 命令行入口：复用与 App 完全相同的功能实现，用于回归验证。
// 在主线里直接调用（不切线程），这样 Logger 会同步写入日志。
//
// 用法：
//   toolbox-cli merge <期目录> [--nested]
//   toolbox-cli deletetemp <期目录>

let store = LogStore()
let logger = Logger(store: store)

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(2)
}

let args = Array(CommandLine.arguments.dropFirst())
guard let command = args.first else {
    fail("用法: toolbox-cli <merge|detect-editors|deletetemp|compile|info|content> <路径>")
}

switch command {
case "merge":
    // 第 99 条：转发引擎（逻辑在 Engine/mergepdf.py）
    guard args.count >= 2 else { fail("用法: toolbox-cli merge <期目录> [--nested] [--editors] [--backcover]") }
    guard let tool = MergeFeature.locateTool() else {
        fail("未找到引擎 Engine/mergepdf.py")
    }
    let mergeDir = (tool as NSString).deletingLastPathComponent
    guard let mergePy = EngineLocator.locatePython(toolDir: mergeDir) else { fail("未找到 python3") }
    let mergeArgs = MergeFeature.arguments(folderPath: args[1],
                                           nestOutlines: args.contains("--nested"),
                                           mergeEditorsPage: args.contains("--editors"),
                                           appendBackCover: args.contains("--backcover"))
    let mergeRC = Shell.run(mergePy, [tool] + mergeArgs,
                            workingDirectory: mergeDir,
                            environment: EngineLocator.environment(toolDir: mergeDir),
                            log: logger)
    if mergeRC != 0 { fail("合并失败（退出码 \(mergeRC)）") }

case "detect-editors":
    // 第 99 条：同样转发引擎（`--detect`），识别逻辑只剩一份
    guard args.count >= 2 else { fail("用法: toolbox-cli detect-editors <期目录>") }
    guard let probe = MergeFeature.detect(folderPath: args[1]) else {
        fail("未找到引擎 Engine/mergepdf.py，或期目录不可读")
    }
    if let c = probe.editorsPage {
        print("识别到编委信息页：\(c.fullDescription)")
    } else {
        print("未找到编委信息页")
    }
    if probe.isAmbiguous {
        print("另有疑似文件（已忽略）：\(probe.others.joined(separator: "、"))")
    }
    if let b = probe.backCover {
        print("识别到封底页：\(b.fullDescription)")
    }
    print("扉页.pdf 页数：\(probe.coverPages)")
    exit(0)

case "deletetemp":
    // 第 98 条：转发引擎（逻辑在 Engine/cleantemp.py）
    guard args.count >= 2 else { fail("用法: toolbox-cli deletetemp <期目录>") }
    guard let tool = DeleteTempFeature.locateTool() else {
        fail("未找到引擎 Engine/cleantemp.py")
    }
    let dir = (tool as NSString).deletingLastPathComponent
    guard let py = EngineLocator.locatePython(toolDir: dir) else { fail("未找到 python3") }
    let rc = Shell.run(py, [tool] + DeleteTempFeature.arguments(folderPath: args[1]),
                       workingDirectory: dir,
                       environment: EngineLocator.environment(toolDir: dir), log: logger)
    if rc != 0 { fail("清理失败（退出码 \(rc)）") }

case "compile":
    // 第 98 条：转发引擎（逻辑在 Engine/compileall.py）
    guard args.count >= 2 else { fail("用法: toolbox-cli compile <期目录>") }
    guard let tool = CompileFeature.locateTool() else {
        fail("未找到引擎 Engine/compileall.py")
    }
    let dir = (tool as NSString).deletingLastPathComponent
    guard let py = EngineLocator.locatePython(toolDir: dir) else { fail("未找到 python3") }
    let rc = Shell.run(py, [tool] + CompileFeature.arguments(folderPath: args[1]),
                       workingDirectory: dir,
                       environment: EngineLocator.environment(toolDir: dir), log: logger)
    if rc != 0 && rc != 1 { fail("编译失败（退出码 \(rc)）") }

case "info":
    // 第 96 条：这一项也已切换到引擎（`Engine/infocheck.py`）。CLI 只做转发。
    guard args.count >= 2 else { fail("用法: toolbox-cli info <期目录>") }
    guard let tool = InfoCheckFeature.locateTool() else {
        fail("未找到引擎 Engine/infocheck.py（先跑 ./build.sh，或从仓库根运行本 CLI）")
    }
    let toolDir = (tool as NSString).deletingLastPathComponent
    guard let py = EngineLocator.locatePython(toolDir: toolDir) else { fail("未找到 python3") }
    let status = Shell.run(py, [tool] + InfoCheckFeature.arguments(folderPath: args[1]),
                           workingDirectory: toolDir,
                           environment: EngineLocator.environment(toolDir: toolDir),
                           log: logger)
    if status != 0 { fail("检查失败（退出码 \(status)）") }

case "content":
    // 第 95 条：这一项已切换到引擎（`Engine/toc.py`）。CLI 只做转发，
    // 不再自己算 —— 否则命令行与界面会走两套逻辑。
    guard args.count >= 2 else { fail("用法: toolbox-cli content <检查报告txt路径> [--template <期目录>]") }
    let reportPath = PathKit.normalize(args[1])
    var tocArgs = ["--report", reportPath]
    if let ti = args.firstIndex(of: "--template"), ti + 1 < args.count {
        tocArgs += ["--template", args[ti + 1]]
    }
    guard let tool = TocFeature.locateTool() else {
        fail("未找到引擎 Engine/toc.py（先跑 ./build.sh，或从仓库根运行本 CLI）")
    }
    let toolDir = (tool as NSString).deletingLastPathComponent
    guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
        fail("未找到 python3")
    }
    let status = Shell.run(py, [tool] + tocArgs,
                           workingDirectory: toolDir,
                           environment: EngineLocator.environment(toolDir: toolDir),
                           log: logger)
    if status != 0 && status != 1 { fail("生成目录失败（退出码 \(status)）") }

default:
    fail("未知命令: \(command)")
}

print(store.text, terminator: "")
