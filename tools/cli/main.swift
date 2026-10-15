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
    guard args.count >= 2 else { fail("用法: toolbox-cli merge <期目录> [--nested] [--editors]") }
    MergeFeature.run(folderPath: args[1],
                     log: logger,
                     nestOutlines: args.contains("--nested"),
                     mergeEditorsPage: args.contains("--editors"),
                     appendBackCover: args.contains("--backcover"))

case "detect-editors":
    guard args.count >= 2 else { fail("用法: toolbox-cli detect-editors <期目录>") }
    let probeDir = PathKit.normalize(args[1])
    let detection = EditorsPageDetector.detect(in: probeDir)
    if let c = detection.chosen {
        print("识别到编委信息页：\(c.fullDescription)")
    } else {
        print("未找到编委信息页")
    }
    if detection.isAmbiguous {
        print("另有疑似文件（已忽略）：\(detection.others.joined(separator: "、"))")
    }
    print("扉页.pdf 页数：\(EditorsPageDetector.coverPageCount(in: probeDir))")
    exit(0)

case "deletetemp":
    guard args.count >= 2 else { fail("用法: toolbox-cli deletetemp <期目录>") }
    DeleteTempFeature.run(folderPath: args[1], log: logger)

case "compile":
    guard args.count >= 2 else { fail("用法: toolbox-cli compile <期目录>") }
    CompileFeature.run(folderPath: args[1], log: logger)

case "info":
    guard args.count >= 2 else { fail("用法: toolbox-cli info <期目录>") }
    _ = InfoCheckFeature.run(folderPath: args[1], log: logger)

case "content":
    guard args.count >= 2 else { fail("用法: toolbox-cli content <检查报告txt路径> [--template <期目录>]") }
    let reportPath = PathKit.normalize(args[1])
    let reportDir = (reportPath as NSString).deletingLastPathComponent
    var tocSettings = TocSettings.load(from: reportDir)
    if let ti = args.firstIndex(of: "--template"), ti + 1 < args.count {
        tocSettings.templateFolder = args[ti + 1]
    }
    _ = ContentFeature.run(txtPath: reportPath, settings: tocSettings, log: logger)

default:
    fail("未知命令: \(command)")
}

print(store.text, terminator: "")
