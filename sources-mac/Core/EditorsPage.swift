import Foundation
import PDFKit

/// 识别出的编委信息页
struct EditorsPageCandidate {
    /// 文件名（不含路径）
    let fileName: String
    /// 该文件的页数
    let pageCount: Int
    /// 命中的规则，用于在界面/日志里说明"为什么认为它是编委页"
    let matchedBy: String

    var fullDescription: String {
        "\(fileName)（\(pageCount) 页，\(matchedBy)）"
    }
}

/// 识别结果
struct EditorsPageDetection {
    /// 将要使用的文件；nil 表示没找到
    let chosen: EditorsPageCandidate?
    /// 同样像编委页、但未被选用的其它文件（通常是遗留的旧版本或改名前的老文件）
    let others: [String]

    var isAmbiguous: Bool { !others.isEmpty }

    static let none = EditorsPageDetection(chosen: nil, others: [])
}

/// 编委信息页（编委会名单那一页）的自动识别。
///
/// 为什么不靠"打开文件看内容"来判断：
///   实测 2024–2025 年用的 `cneditor左移_2024.pdf` **没有文字层**（印刷厂曲线化文件），
///   程序读不出任何文字；2024 年第 2 期的 `扉页.pdf` 第 2 页同样没有文字层。
///   靠内容识别时灵时不灵，所以改为**按文件名识别**。
///
/// 约定：把编委信息页命名为 `编委页.pdf`，放在期目录根下。
/// 同时兼容历史上出现过的各种命名，老期数不用改名。
enum EditorsPageDetector {

    /// 新约定名
    static let preferredName = "编委页.pdf"

    /// 在一期目录里找编委信息页
    static func detect(in folderPath: String) -> EditorsPageDetection {
        guard PathKit.isDirectory(folderPath) else { return .none }

        let names = (try? FileManager.default.contentsOfDirectory(atPath: folderPath)) ?? []
        // 只考虑 PDF，且排除工具箱自己生成的合并文件
        let pdfs = names
            .filter { $0.lowercased().hasSuffix(".pdf") }
            .filter { !$0.hasPrefix("合并") }

        func make(_ name: String, _ reason: String) -> EditorsPageCandidate {
            let path = PathKit.join(folderPath, name)
            let pages = PDFDocument(url: URL(fileURLWithPath: path))?.pageCount ?? -1
            return EditorsPageCandidate(fileName: name, pageCount: pages, matchedBy: reason)
        }

        // 按优先级分档；第一档命中即采用，其余档的命中作为"疑似遗留文件"报告出来
        let tiers: [(String, [String])] = [
            ("约定名", pdfs.contains(preferredName) ? [preferredName] : []),
            ("cneditor 前缀", pdfs.filter { $0.lowercased().hasPrefix("cneditor") }.sorted()),
            ("2022 年命名", pdfs.contains("扉页2.pdf") ? ["扉页2.pdf"] : []),
            ("名字含「编委」", pdfs
                .filter { $0.contains("编委") && !$0.contains("封面") && !$0.contains("英文") }
                .sorted()),
        ]

        var chosen: EditorsPageCandidate? = nil
        var others: [String] = []

        for (reason, matches) in tiers where !matches.isEmpty {
            if chosen == nil {
                chosen = make(matches[0], reason)
                // 同一档里还有别的候选，也算"疑似遗留"
                others.append(contentsOf: matches.dropFirst())
            } else {
                others.append(contentsOf: matches)
            }
        }

        return EditorsPageDetection(chosen: chosen, others: others)
    }

    /// 扉页本身是否可能已经含编委页（扉页超过 1 页时就需要人工确认）
    static func coverPageCount(in folderPath: String) -> Int {
        let cover = PathKit.join(folderPath, "扉页.pdf")
        guard PathKit.exists(cover) else { return 0 }
        return PDFDocument(url: URL(fileURLWithPath: cover))?.pageCount ?? 0
    }
}
