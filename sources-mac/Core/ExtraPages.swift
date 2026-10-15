import Foundation
import PDFKit

// MARK: - 文章 PDF 的宽松匹配

extension PathKit {

    /// 在一篇文章文件夹里找出该篇的 PDF。
    ///
    /// 标准命名是 `<文件夹名末段>.pdf`，例如 `04 Chen_Liu/Chen_Liu.pdf`。
    /// 但实际工作中常出现下划线被写成空格的情况 —— 实测 2026年第3期 的
    /// `04 Chen_Liu/Chen Liu.pdf` 就是这样：原版判定"没有找到"并**整篇跳过**，
    /// 那一篇 33 页（印刷页码 59–91）就这样静默丢失，而且不报错。
    ///
    /// 所以这里在精确名找不到时，再按"忽略下划线/空格/连字符与大小写"匹配一次。
    static func resolveArticlePDF(folder: String, folderName: String) -> (path: String, actualName: String)? {
        let exact = join(folder, "\(folderName).pdf")
        if exists(exact) { return (exact, "\(folderName).pdf") }

        let files = ((try? FileManager.default.contentsOfDirectory(atPath: folder)) ?? []).sorted()
        let pdfs = files.filter { $0.lowercased().hasSuffix(".pdf") }
        let target = looseKey(folderName)

        if let hit = pdfs.first(where: { looseKey(($0 as NSString).deletingPathExtension) == target }) {
            return (join(folder, hit), hit)
        }
        return nil
    }

    /// 归一化文件名用于宽松比较：去掉下划线、半角/全角空格、连字符，统一小写。
    /// 这样 `Chen_Liu` / `Chen Liu` / `chen-liu` 会被视为同一个名字。
    static func looseKey(_ s: String) -> String {
        s.replacingOccurrences(of: "_", with: "")
            .replacingOccurrences(of: " ", with: "")
            .replacingOccurrences(of: "\u{3000}", with: "")
            .replacingOccurrences(of: "-", with: "")
            .lowercased()
    }
}

// MARK: - 封底附加页

extension EditorsPageDetector {

    /// 找"封底/封面"附加页，形如 `cover+bottom+Englishi_2024.pdf`。
    ///
    /// 这类页也是纯图片、**没有文字层**，所以同样只能按文件名识别。
    /// 它在正式成品里被放在**最后一页**（实测 2026年第3期 的第 160 页）。
    static func detectBackCover(in folderPath: String) -> EditorsPageCandidate? {
        guard PathKit.isDirectory(folderPath) else { return nil }

        let names = (try? FileManager.default.contentsOfDirectory(atPath: folderPath)) ?? []
        let pdfs = names
            .filter { $0.lowercased().hasSuffix(".pdf") }
            .filter { !$0.hasPrefix("合并") }

        // 优先级：`cover+bottom` 前缀 → 名字里同时含 cover 与 bottom
        let tiers: [(String, [String])] = [
            ("cover+bottom 前缀", pdfs.filter { $0.lowercased().hasPrefix("cover+bottom") }.sorted()),
            ("名字含 cover 与 bottom", pdfs.filter {
                let l = $0.lowercased()
                return l.contains("cover") && l.contains("bottom")
            }.sorted()),
        ]

        for (reason, matches) in tiers where !matches.isEmpty {
            let name = matches[0]
            let path = PathKit.join(folderPath, name)
            let pages = PDFDocument(url: URL(fileURLWithPath: path))?.pageCount ?? -1
            return EditorsPageCandidate(fileName: name, pageCount: pages, matchedBy: reason)
        }
        return nil
    }
}
