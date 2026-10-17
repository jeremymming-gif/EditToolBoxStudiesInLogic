import Foundation
import Combine

/// 对应原程序窗口下方的「运行日志」框（Tkinter ScrolledText）。
/// 所有功能模块的提示信息都写到这里。
final class LogStore: ObservableObject {
    @Published private(set) var text: String = ""

    /// 日志上限，防止编译大量 tex 时内存无限增长
    private let limit = 400_000

    func append(_ s: String) {
        text += s
        if text.count > limit {
            text.removeFirst(text.count - limit)
        }
    }

    func clear() {
        text = ""
    }
}

/// 对应原程序的 run_info(out_txt, ss)：追加文本、刷新、滚动到底部。
/// 功能模块在工作线程里调用它，内部会自动切回主线程。
final class Logger: @unchecked Sendable {
    private let store: LogStore
    /// 每一行旁路回调（界面用它抓**最后一行 = 契约行**去填"完成横幅"）。
    /// 存在只是为了让横幅能拿到产物路径，不改变日志本身的写入行为。
    private let onLine: ((String) -> Void)?

    init(store: LogStore, onLine: ((String) -> Void)? = nil) {
        self.store = store
        self.onLine = onLine
    }

    func callAsFunction(_ s: String) {
        onLine?(s)
        if Thread.isMainThread {
            store.append(s)
        } else {
            DispatchQueue.main.async { [store] in
                store.append(s)
            }
        }
    }
}
