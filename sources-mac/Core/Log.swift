import Foundation
import SwiftUI
import Combine
import Darwin

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

/// U3 取消按钮：一次运行的**取消句柄**（mac 侧「运行中主按钮变取消」的底座）。
///
/// 生命周期：视图在每次运行前建一个、交给这一轮的 `Logger`；`Shell.run` /
/// `Shell.capture` 启动子进程后把 `Process` 注册进来，子进程结束时注销。
/// 用户点「取消」→ 视图调 `cancel()`。跑完后视图用 `isCancelled` 决定显示
/// 「⏹ 已取消」还是「❌ 失败」（用户主动取消**不是**失败，见 INVARIANTS 三.2 的同一精神）。
///
/// 为什么必须杀**整棵进程树**（与 Windows 侧 `kill_tree` 同一判据，案例 M3/W1）：
/// 直接子进程是 python，它起的 `xelatex` 是孙进程。只 terminate 直接子进程时，
/// xelatex 会继续写产物目录（Windows 上实测又跑了约 12 秒），编辑再点一次运行
/// 就有两组 TeX 同写一个目录。macOS 的 Foundation `Process` 不能直接 setsid，
/// 所以按 `ps` 的 ppid 关系手工扫出全部后代，逐个 SIGTERM，最后再终止根进程。
final class RunCancel: @unchecked Sendable {

    private let lock = NSLock()
    private var process: Process?
    private var cancelled = false

    /// `Shell.run` / `Shell.capture` 在子进程刚起来时调用（`nil` = 已结束/没起来）。
    /// 若用户在这个极窄的窗口里已经点过取消，这里**立刻补杀**，不静默忽略。
    func attach(_ p: Process?) {
        lock.lock()
        let alreadyCancelled = cancelled
        process = p
        lock.unlock()
        if alreadyCancelled, let p { Self.killTree(p) }
    }

    /// 点「取消」：返回是否真的发出了终止（任务已结束 / 还没启动 → false）。
    @discardableResult
    func cancel() -> Bool {
        lock.lock()
        guard let p = process, !cancelled else {
            lock.unlock()
            return false
        }
        cancelled = true
        lock.unlock()
        // 扫进程树要起 `ps` 子进程：按钮回调在主线程，不能卡界面，放到后台队列。
        DispatchQueue.global(qos: .userInitiated).async { Self.killTree(p) }
        return true
    }

    /// 本次运行是否被用户取消（视图据此显示「⏹ 已取消」而不是「❌ 失败」）。
    var isCancelled: Bool {
        lock.lock()
        defer { lock.unlock() }
        return cancelled
    }

    // MARK: - 进程树

    /// 杀整棵进程树：先 SIGTERM 全部后代，再 terminate 根进程（python）。
    static func killTree(_ root: Process) {
        let pid = root.processIdentifier
        guard pid > 0 else { return }
        for child in descendants(of: pid).reversed() {
            kill(child, SIGTERM)
        }
        root.terminate()
    }

    /// 快照当前全部后代 pid（`ps -axo pid=,ppid=` 一次读全表，再按 ppid 展开成树）。
    /// 一次快照就够：引擎起 TeX 的动作发生在启动瞬间之后，取消时那些进程已经在跑；
    /// 根进程收到 SIGTERM 后也不会再起新子进程。
    private static func descendants(of pid: pid_t) -> [pid_t] {
        let ps = Process()
        ps.executableURL = URL(fileURLWithPath: "/bin/ps")
        ps.arguments = ["-axo", "pid=,ppid="]
        let pipe = Pipe()
        ps.standardOutput = pipe
        ps.standardError = FileHandle.nullDevice
        do {
            try ps.run()
        } catch {
            return []
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        ps.waitUntilExit()

        var children: [pid_t: [pid_t]] = [:]
        for raw in String(decoding: data, as: UTF8.self).split(separator: "\n") {
            let cols = raw.split(whereSeparator: { $0 == " " || $0 == "\t" })
            guard cols.count >= 2,
                  let child = pid_t(String(cols[0])),
                  let parent = pid_t(String(cols[1])) else { continue }
            children[parent, default: []].append(child)
        }
        var out: [pid_t] = []
        var queue = children[pid] ?? []
        while let next = queue.popLast() {
            out.append(next)
            queue.append(contentsOf: children[next] ?? [])
        }
        return out
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

    /// U3：本次运行的取消句柄（视图建；`Shell.run` 往里注册子进程）。
    /// nil = 这次 `Logger` 不可取消（启动/自动填充之类的非运行期日志）。
    let cancel: RunCancel?

    init(store: LogStore, startedAt: Date? = nil, cancel: RunCancel? = nil,
         onLine: ((String) -> Void)? = nil) {
        self.store = store
        self.startedAt = startedAt
        self.cancel = cancel
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
