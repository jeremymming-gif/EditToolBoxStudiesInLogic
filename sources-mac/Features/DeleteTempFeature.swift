import Foundation

/// 「删除临时文件」—— 对应原程序的 deletetemp.py。
/// 在原版基础上增加了 macOS 的 `.DS_Store`（访达自动生成的目录元数据，
/// 同步仓库时会混进 git）。
enum DeleteTempFeature {

    /// 与原版一致：不含 .log，也不含 .pdf/.tex
    /// 末尾的 `.DS_Store` 是本版新增的
    static let suffixes = [".aux", ".bbl", ".bcf", ".blg", ".out", ".run.xml", ".synctex.gz",
                          ".DS_Store"]

    static func run(folderPath rawPath: String, log: Logger) {
        log("删除临时文件\n")
        let root = PathKit.normalize(rawPath)
        let fm = FileManager.default

        guard PathKit.isDirectory(root) else {
            log("路径不是文件夹：\(root)\n")
            log("程序错误\n")
            return
        }

        do {
            var count = 0
            guard let enumerator = fm.enumerator(atPath: root) else {
                log("程序错误\n")
                return
            }

            for case let relative as String in enumerator {
                let full = PathKit.join(root, relative)
                var isDir: ObjCBool = false
                guard fm.fileExists(atPath: full, isDirectory: &isDir), !isDir.boolValue else {
                    continue
                }
                let lower = relative.lowercased()
                // 两边都转小写再比，这样列表里可以写成易读的 ".DS_Store"
                guard suffixes.contains(where: { lower.hasSuffix($0.lowercased()) }) else { continue }

                do {
                    try fm.removeItem(atPath: full)
                    log("Deleted: \(relative)\n")
                    count += 1
                } catch {
                    log("Error deleting \(relative): \(error.localizedDescription)\n")
                }
            }

            log("删除完成，共删除\(count)个文件。\n")
        } catch {
            log("程序错误\n")
        }
    }
}
