import Foundation
import SwiftUI
import Combine
import Darwin

/// 对应原程序窗口下方的「运行日志」框（Tkinter ScrolledText）。
/// 所有功能模块的提示信息都写到这里。
///
/// U4（2026-10-17 用户拍板）：「日志分摘要/原始两层，默认折叠原始层」。
/// 摘要层只保留**关键节点与结果**（阶段标题、✅/⚠/❌、契约行、报告落点…），
/// 原始层是引擎逐行输出（xelatex 细节、逐篇路径…）。判据 `isSummaryLine(_:)`
/// 与 Windows 侧 `engine.is_summary_line` **同一套词表**，跨平台夹具是 `tools/log_cases.json`
/// （Swift 测试台与桥梁测试各自跑同一份，防止两侧词表悄悄分叉）。
final class LogStore: ObservableObject {
    @Published private(set) var text: String = ""
    /// 带级别色的富文本（UI 方案 §2-8）。纯文本 `text` 仍保留（复制/测试用）。
    @Published private(set) var attributed = AttributedString()

    /// U4：摘要层文本与富文本（只含 `isSummaryLine` 为真的行）
    @Published private(set) var summaryText: String = ""
    @Published private(set) var summaryAttributed = AttributedString()
    /// 摘要模式下被折叠的原始行数（工具条显示「已折叠 N 行原始日志」）。
    /// 用增量计数维护（每次 append 只扫新块）：日志上限 40 万字符，逐次全量重算会成 O(n²)。
    @Published private(set) var foldedLineCount = 0
    private var rawLineCount = 0
    private var keptLineCount = 0

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

    /// U4：一行日志是否属于「摘要层」。
    ///
    /// 判据 = 标记词 + 结构前缀（与 Windows `engine.is_summary_line` 逐字对应）：
    ///   ① 含结果/状态/关键字段标记（✅ ⚠ ❌ ❗ ℹ ⏹ … / 完成。/ 产物 / 报告 / 结论 …）；
    ///   ② 以 `▶` 或圈码 `①②…⑳` 开头（工具自己打的阶段行）；
    ///   ③ `[i/N]` / `（i/N）` 进度行。
    /// 宁可多收一点：摘要只是默认视图，切到「原始」永远能看到全部行。
    static let summaryMarkers = [
        "✅", "⚠", "❌", "❗", "ℹ", "⏹", "✓", "✗", "⛔",
        "完成。", "程序错误", "未找到", "启动失败", "失败", "警告", "需核对", "不一致",
        "命中", "跳过", "取消", "产物", "结论", "报告", "备份", "提示", "合计",
        "已配置", "通道", "预演", "剪贴板",
    ]

    static func isSummaryLine(_ raw: String) -> Bool {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return false }
        if summaryMarkers.contains(where: { s.contains($0) }) { return true }
        if s.hasPrefix("▶") { return true }
        if let first = s.first, "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳".contains(first) { return true }
        if s.range(of: #"^\[\d+/\d+\]"#, options: .regularExpression) != nil { return true }
        if s.range(of: #"^（\d+/\d+）"#, options: .regularExpression) != nil { return true }
        return false
    }

    func append(_ s: String) {
        text += s
        var chunk = AttributedString(s)
        chunk.foregroundColor = LogStore.levelColor(s)
        attributed += chunk

        // U4：摘要层逐行过滤（一次 append 可能含多行，例如引擎一次吐一块）
        let lines = s.split(separator: "\n", omittingEmptySubsequences: false)
        var kept = ""
        var keptRich = AttributedString()
        for line in lines where LogStore.isSummaryLine(String(line)) {
            kept += String(line) + "\n"
            var c = AttributedString(String(line) + "\n")
            c.foregroundColor = LogStore.levelColor(String(line))
            keptRich += c
        }
        if !kept.isEmpty {
            summaryText += kept
            summaryAttributed += keptRich
        }
        // 增量计数：以换行数为准（尾部未换行的半行要等下一块到了才计，显示层面无所谓）
        rawLineCount += s.reduce(0) { $1 == "\n" ? $0 + 1 : $0 }
        keptLineCount += kept.reduce(0) { $1 == "\n" ? $0 + 1 : $0 }
        foldedLineCount = max(0, rawLineCount - keptLineCount)
        if text.count > limit {
            text.removeFirst(text.count - limit)
            rebuildAttributed()          // 截断后丢掉旧颜色并重算计数（极少发生，简单可靠）
        }
    }

    private func rebuildAttributed() {
        var rebuilt = AttributedString()
        var keptRich = AttributedString()
        var kept = ""
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            var chunk = AttributedString(String(line) + "\n")
            chunk.foregroundColor = LogStore.levelColor(String(line))
            rebuilt += chunk
            if LogStore.isSummaryLine(String(line)) {
                kept += String(line) + "\n"
                var c = AttributedString(String(line) + "\n")
                c.foregroundColor = LogStore.levelColor(String(line))
                keptRich += c
            }
        }
        attributed = rebuilt
        summaryText = kept
        summaryAttributed = keptRich
        rawLineCount = rebuilt.characters.reduce(0) { $1 == "\n" ? $0 + 1 : $0 }
        keptLineCount = keptRich.characters.reduce(0) { $1 == "\n" ? $0 + 1 : $0 }
        foldedLineCount = max(0, rawLineCount - keptLineCount)
    }

    func clear() {
        text = ""
        attributed = AttributedString()
        summaryText = ""
        summaryAttributed = AttributedString()
        foldedLineCount = 0
        rawLineCount = 0
        keptLineCount = 0
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
    /// 每一行旁路回调（界面用它抓**契约行**去填"完成横幅"）。
    /// ⚠️ 传给回调的是**引擎原样输出**，**不带**相对时间戳前缀 —— 横幅要用
    /// `EngineOutput.indicatesSuccess()`（判据是行首 `完成。`），带 `+ 52.2s  ` 前缀会判不出来
    /// （2026-10-17 第十五批真机实测：编译全部 10/10 成功，横幅仍 ❌）。
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
        // 旁路回调给**原文**（不加时间戳）：契约行/产物路径的判据都以引擎原样输出为准
        onLine?(s)
        if Thread.isMainThread {
            store.append(text)
        } else {
            DispatchQueue.main.async { [store] in
                store.append(text)
            }
        }
    }
}
