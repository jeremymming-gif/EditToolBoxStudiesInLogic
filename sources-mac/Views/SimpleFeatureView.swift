import SwiftUI
import AppKit

enum PickKind {
    case directory
    case file
}

/// 通用功能页：使用说明 + 路径输入 + 浏览 + 运行。
/// 对应原程序的 TabPage 基类。
///
/// 界面状态由外部注入，这样「合并文件」页可以在同一个 model 上再叠加自己的选项。
struct SimpleFeatureView: View {

    @ObservedObject var model: PathRunnerModel
    let help: String
    let pickKind: PickKind
    @ObservedObject var log: LogStore
    /// 点击「运行」时执行；在工作线程中被调用
    let action: (_ path: String, _ log: Logger) -> Void
    /// 「撤销」按钮（只有「删除临时文件」页用；移入回收目录后可一键还原）
    var undoTitle: String? = nil
    var onUndo: ((_ folder: String, _ artifactPath: String) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {

            Text(help.plainMarkdown)     // SwiftUI 的 Text(String) 不解析 markdown（见 String.plainMarkdown）
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

            HStack(spacing: 8) {
                DroppablePathField(text: $model.path, placeholder: "输入路径或浏览选择路径",
                                   disabled: model.running) { url in
                    PathDrop.receive(url, kind: pickKind, label: nil, log: nil,
                                     setPath: { model.path = $0 }, onAccept: nil)
                }
                Button("浏览…") { browse() }
                    .disabled(model.running)
                // U3：运行中主按钮变成「取消」（不是把主按钮灰掉、另放一个不起眼的按钮）。
                // 取消 = 杀整棵进程树（见 `RunCancel`）；短任务可能来不及取消，那就自然结束。
                if model.running {
                    Button {
                        model.cancel?.cancel()
                    } label: {
                        Label("取消", systemImage: "stop.circle")
                    }
                } else {
                    Button("运行") { execute() }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut("r", modifiers: .command)   // UI 方案 §5：⌘R 运行
                        .disabled(model.path.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }

            HStack(spacing: 6) {
                Image(systemName: "arrow.down.doc")
                Text(pickKind == .directory
                     ? "也可以把文件夹直接拖到这里"
                     : "也可以把文件直接拖到这里")
            }
            .font(.caption)
            .foregroundStyle(.tertiary)

            if model.running {
                // UI 方案第 2 件：不确定进度条 + 秒表（不知道总量就不编百分比，只证明"在动"）
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("正在运行，请查看下方运行日志…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if let t0 = model.startedAt {
                            TimelineView(.periodic(from: t0, by: 0.5)) { context in
                                Text(String(format: "已用 %.1f 秒",
                                            context.date.timeIntervalSince(t0)))
                                    .font(.caption)
                                    .monospacedDigit()
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    ProgressView().progressViewStyle(.linear)
                }
            } else if let ok = model.resultOK {
                // 完成横幅（UI 方案第 1 件）：状态 + 用时 + 产物路径（可点开 / 可复制）
                ResultBanner(
                    ok: ok,
                    cancelled: model.resultCancelled,
                    seconds: model.resultSeconds,
                    path: model.resultPath,
                    undoTitle: undoTitle,
                    onUndo: {
                        if let folder = Optional(model.path.trimmingCharacters(in: .whitespacesAndNewlines)),
                           let p = model.resultPath, let handler = onUndo {
                            handler(folder, p)
                        }
                    }
                )
            }

            Spacer()
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .pathDropTarget(path: $model.path, kind: pickKind)
    }

    private func browse() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = pickKind == .file
        panel.canChooseDirectories = pickKind == .directory
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.prompt = pickKind == .directory ? "选择文件夹" : "选择文件"
        if panel.runModal() == .OK, let url = panel.url {
            model.path = url.path
        }
    }

    private func execute() {
        let target = model.path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !target.isEmpty else { return }
        // 抓**契约行**（= 引擎最后一行、以 `完成。` 开头）填完成横幅的状态与产物路径。
        // ⚠️ 不能取"最后一行"：Feature 在引擎结束后还会补自己的话术（「运行结束，请确认…」），
        //    取最后一行会把成功运行判成失败（第十五批真机实测，见 `EngineOutput.contractLine`）。
        final class LineBox: @unchecked Sendable { var lines: [String] = [] }
        let box = LineBox()
        let started = Date()
        // U3：每次运行一个取消句柄；`Shell.run` 把子进程注册进来，主按钮据此变「取消」。
        let cancel = RunCancel()
        let logger = Logger(store: log, startedAt: started, cancel: cancel) { line in
            let t = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { box.lines.append(t) }
        }
        let runner = model
        runner.cancel = cancel
        runner.running = true
        runner.resultOK = nil
        runner.resultPath = nil
        runner.resultCancelled = false
        runner.startedAt = started
        DispatchQueue.global(qos: .userInitiated).async {
            action(target, logger)
            let seconds = Date().timeIntervalSince(started)
            // 契约行优先；没有契约行（例如引擎在启动前就被拒绝）才退回最后一行
            let contract = EngineOutput.contractLine(from: box.lines)
            let last = contract ?? (box.lines.last ?? "")
            // U3：用户取消 → 成败按「已取消」显示（用户主动终止不是失败）
            let cancelled = cancel.isCancelled
            let ok = !cancelled && EngineOutput.indicatesSuccess(last)
            let path = ok ? EngineOutput.artifactPath(from: last) : nil
            DispatchQueue.main.async {
                runner.running = false
                runner.startedAt = nil
                runner.resultSeconds = seconds
                runner.resultPath = path
                runner.resultCancelled = cancelled
                runner.resultOK = ok
            }
        }
    }
}

/// 自带界面状态的普通功能页（「编译全部」「生成目录」「删除临时文件」用）
struct SimpleTab: View {

    let help: String
    let pickKind: PickKind
    @ObservedObject var log: LogStore
    let action: (_ path: String, _ log: Logger) -> Void
    var undoTitle: String? = nil
    var onUndo: ((_ folder: String, _ artifactPath: String) -> Void)? = nil

    @StateObject private var model = PathRunnerModel()

    var body: some View {
        SimpleFeatureView(model: model, help: help, pickKind: pickKind, log: log,
                          action: action, undoTitle: undoTitle, onUndo: onUndo)
    }
}

/// 完成横幅（UI 方案第 1 件）：状态 + 用时 + 产物路径（在访达中显示 / 复制路径）。
///
/// 编辑最常问的两句是「跑完了吗」「产物在哪」—— 引擎的契约行里本来就有产物路径，
/// 界面把它做成**常驻 + 可点开 + 可复制**，比让人翻日志强得多。
struct ResultBanner: View {

    let ok: Bool
    /// U3：用户主动取消（显示「⏹ 已取消」，**不显示成失败**；此时 ok == false）
    var cancelled: Bool = false
    let seconds: Double
    let path: String?
    var undoTitle: String? = nil
    var onUndo: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(cancelled ? "⏹ 已取消" : (ok ? "✅ 完成" : "❌ 失败 / 有错误"))
                .font(.callout).fontWeight(.medium)
                .foregroundStyle(cancelled ? Color.orange : (ok ? Color.green : Color.red))
            if cancelled {
                Text("（用户终止；产物可能不完整，需要时重新运行）")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if seconds >= 1 {
                Text(String(format: "用时 %.1f 秒", seconds))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let p = path {
                Text(p)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(p)
                    .textSelection(.enabled)
            }
            Spacer()
            if let p = path {
                Button("在访达中显示") {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: p)])
                }
                Button("复制路径") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(p, forType: .string)
                }
            }
            if !cancelled, let title = undoTitle, let onUndo {
                Button(title, action: onUndo)
            }
        }
        .padding(.vertical, 2)
    }
}
