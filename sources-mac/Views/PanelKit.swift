import AppKit
import UniformTypeIdentifiers

/// 批63b 发布修复 R2（2026-10-20）：`NSOpenPanel` 的调用集中在这个小文件里。
///
/// 为什么单独成文件：GitHub macos-14 runner 的旧 Xcode 对 `NSApplication.ModalResponse == .OK`
/// 等 AppKit 运算符的**首次**重载解析要花 ~10ms 量级，而且这笔一次性开销会算到「第一个
/// 使用它的函数」头上 —— 那个函数如果在 `ConvertTab.swift` 里，就会被 CI 判成「类型检查
/// 超时」。集中到本文件后，`ConvertTab` 只调用 `PanelKit.pickXxx()`（一行普通函数调用），
/// 不再出现任何 AppKit 响应/运算符。行为、文案、过滤器与拆分前完全一致。
enum PanelKit {

    /// 「浏览…」单文件（`allowedExtensions` 非空时按扩展名过滤；空 = 不限制）。
    static func pickFile(allowedExtensions: [String]) -> String? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if allowedExtensions.isEmpty {
            panel.allowedContentTypes = []
            panel.allowsOtherFileTypes = true
        } else {
            panel.allowedContentTypes = allowedExtensions.compactMap {
                UTType(filenameExtension: $0)
            }
            panel.allowsOtherFileTypes = false
        }
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        return url.path
    }

    /// 「浏览…」单目录。
    static func pickDirectory() -> String? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        return url.path
    }

    /// 「添加文件…」多选（AI 回填；每个文件各算一项）。
    static func pickReplyFiles() -> [URL]? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.title = "选择一个或多个 AI 回复文件（可按住 ⌘ 多选；JSON 与 BibTeX 可分开选）"
        guard panel.runModal() == .OK else { return nil }
        return panel.urls
    }

    /// 「添加目录…」单选目录（AI 回填）。
    static func pickReplyDirectory() -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.title = "选择一个 AI 回复目录（目录下全部 .md/.txt/.bib/.json 会被一起读入）"
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        return url
    }
}
