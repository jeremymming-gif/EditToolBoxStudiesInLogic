import Foundation
import SwiftUI
import Combine

/// 对应原程序窗口下方的「运行日志」框（Tkinter ScrolledText）。
/// 所有功能模块的提示信息都写到这里。
final class LogStore: ObservableObject {
    @Published private(set) var text: String = ""
    /// 带级别色的富文本（UI 方案 §2-8）。纯文本 `text` 仍保留（复制/测试用）。
    @Published private(set) var attributed = AttributedString()

    /// 日志上限，防止编译大量 tex 时内存无限增长
    private let limit = 400_000

    /// 按内容判级别 —— 与 Windows 侧 `MainWindow._level_tag` **同一套词表**。
    /// 颜色只是辅助：行首本来就有 ✅/⚠/❌ 符号，色弱用户也能读（HIG：颜色不能是唯一手段）。
    static func levelColor(_ s: String) -> Color? {
        if s.contains("❌") || s.contains("程序错误") || s.contains("未找到")
            || s.contains("启动失败") {
            return .red
        }
        if s.contains("⚠") || s.contains("警告") { return .orange }
        if s.contains("✅") || s.contains("完成。") { return .green }
        return nil
    }

    func append(_ s: String) {
        text += s
        var chunk = AttributedString(s)
        chunk.foregroundColor = LogStore.levelColor(s)
        attributed += chunk
        if text.count > limit {
            text.removeFirst(text.count - limit)
            rebuildAttributed()          // 截断后丢掉旧颜色（极少发生，简单可靠）
        }
    }

    private func rebuildAttributed() {
        var rebuilt = AttributedString()
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            var chunk = AttributedString(String(line) + "\n")
            chunk.foregroundColor = LogStore.levelColor(String(line))
            rebuilt += chunk
        }
        attributed = rebuilt
    }

    func clear() {
        text = ""
        attributed = AttributedString()
    }
}

/// 对应原程序的 run_info(out_txt, ss)：追加文本、刷新、滚动到底部。
/// 功能模块在工作线程里调用它，内部会自动切回主线程。
final class Logger: @unchecked Sendable {
    private let store: LogStore
    /// 每一行旁路回调（界面用它抓**最后一行 = 契约行**去填"完成横幅"）。
    /// 存在只是为了让横幅能拿到产物路径，不改变日志本身的写入行为。
    private let onLine: ((String) -> Void)?

    /// 相对时间戳起点（UI 方案 §2-8）：跑任务时每行前面加 `+  3.2s`；nil = 不加
    /// （启动信息等非运行期日志保持干净）。与 Windows 侧 `MainWindow.log()` 同一行为。
    private let startedAt: Date?

    init(store: LogStore, startedAt: Date? = nil, onLine: ((String) -> Void)? = nil) {
        self.store = store
        self.startedAt = startedAt
        self.onLine = onLine
    }

    func callAsFunction(_ s: String) {
        var text = s
        if let t0 = startedAt, !s.isEmpty {
            text = String(format: "+%5.1fs  ", Date().timeIntervalSince(t0)) + s
        }
        onLine?(text)
        if Thread.isMainThread {
            store.append(text)
        } else {
            DispatchQueue.main.async { [store] in
                store.append(text)
            }
        }
    }
}
