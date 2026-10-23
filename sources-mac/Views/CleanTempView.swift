import SwiftUI
import AppKit

/// 「删除临时文件」页（B-211）：在原来的单模式页上加**模式 Picker** 与**清空回收站**按钮。
///
/// 与 `SimpleFeatureView` 同一套组件（`PathRunnerModel` / `ResultBanner` / `Logger`），
/// 样式保持一致；区别只有：运行时要多带一个 `CleanMode`，另外多一个「清空回收站…」动作。
/// 具体删什么仍由引擎决定，这里只「取值 → 拼参数 → 显示日志」。
struct CleanTempView: View {

    @ObservedObject var log: LogStore

    /// 运行：把当前路径 + 选中模式交给 Feature。
    let run: (_ path: String, _ mode: DeleteTempFeature.CleanMode, _ log: Logger) -> Void
    /// 清空回收站（Feature 内部还会再弹一次「不可撤销」确认框）。
    let emptyTrash: (_ path: String, _ log: Logger) -> Void
    /// 撤销清理（把回收目录搬回原处）；nil = 本页不提供撤销。
    var undo: ((_ folder: String, _ trashDir: String) -> Void)? = nil
    /// 兜底找最近回收目录（契约行里没抠到路径时用）。
    var lastTrashDir: ((_ folder: String) -> String?)? = nil

    @StateObject private var model = PathRunnerModel()
    @State private var mode: DeleteTempFeature.CleanMode = .aux
    /// 本次结果是否允许「撤销清理」（清空回收站后不能再撤销，否则会把整个目录搬回去）
    @State private var canUndo = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {

            Text(help.plainMarkdown)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

            HStack(spacing: 8) {
                Text("清理模式")
                Picker("清理模式", selection: $mode) {
                    ForEach(DeleteTempFeature.CleanMode.allCases, id: \.self) { m in
                        Text(m.title).tag(m)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .disabled(model.running)
                .help("默认「编译临时文件」；新模式都先入回收站，可撤销")
            }
            Text(mode.detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                DroppablePathField(text: $model.path, placeholder: "输入路径或浏览选择路径",
                                   disabled: model.running) { url in
                    PathDrop.receive(url, kind: .directory, label: nil, log: nil,
                                     setPath: { model.path = $0 }, onAccept: nil)
                }
                Button("浏览…") { browse() }
                    .disabled(model.running)
                if model.running {
                    Button {
                        model.cancel?.cancel()
                    } label: {
                        Label("取消", systemImage: "stop.circle")
                    }
                } else {
                    Button("运行") { runSelectedMode() }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut("r", modifiers: .command)
                        .disabled(model.path.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }

            HStack(spacing: 8) {
                Button("清空回收站…") { clearTrash() }
                    .disabled(model.running
                              || model.path.trimmingCharacters(in: .whitespaces).isEmpty)
                    .help("永久清空此目录下 .toolbox-trash/ 里的回收目录（不可恢复）")
                Text("回收站：.toolbox-trash/（清空前会再确认）")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }

            HStack(spacing: 6) {
                Image(systemName: "arrow.down.doc")
                Text("也可以把文件夹直接拖到这里")
            }
            .font(.caption)
            .foregroundStyle(.tertiary)

            if model.running {
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
                ResultBanner(
                    ok: ok,
                    cancelled: model.resultCancelled,
                    seconds: model.resultSeconds,
                    path: model.resultPath,
                    undoTitle: canUndo && undo != nil ? "撤销清理" : nil,
                    onUndo: { undoLastClean() }
                )
            }

            Spacer()
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .pathDropTarget(path: $model.path, kind: .directory)
    }

    private var help: String {
        """
        使用说明：输入一期的目录（或工作区目录），例如：
        …/Studies_in_Logic_Database/2026年第5期(90)

        先选清理模式，再点运行；运行前会弹确认框说明「清什么、不碰什么」。
        三种模式都先把文件移入同目录的 .toolbox-trash/（可撤销，不会直接删掉）：
        ① 编译临时文件：清 .aux/.bbl/… 等编译中间文件（默认，与旧版一致）；
        ② 交付收尾（白名单）：交付前清理，按 tex 引用闭包 + 白名单保留，引用缺失时整批拒绝；
        ③ 工作区旧件：每期 合并*.pdf 只留最新一份，固定交付物与人工文件不动。
        跑完横幅上有「撤销清理」；「清空回收站…」是永久删除，会再确认一次。
        """
    }

    private func browse() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.prompt = "选择文件夹"
        if panel.runModal() == .OK, let url = panel.url {
            model.path = url.path
        }
    }

    private func runSelectedMode() {
        let selected = mode
        execute(allowUndo: true) { path, logger in
            run(path, selected, logger)
        }
    }

    private func clearTrash() {
        execute(allowUndo: false) { path, logger in
            emptyTrash(path, logger)
        }
    }

    /// 撤销清理：优先用契约行里的回收目录，抠不到再退回「mtime 最新的回收目录」。
    private func undoLastClean() {
        let folder = model.path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !folder.isEmpty, let handler = undo else { return }
        guard let trashDir = model.resultPath ?? lastTrashDir?(folder) else {
            log.append("未找到回收目录，无法撤销（那次可能没有清理任何文件）。\n")
            return
        }
        handler(folder, trashDir)
    }

    /// 公共运行流程：跑完从契约行判成败、抠产物路径，填完成横幅。
    private func execute(allowUndo: Bool,
                         _ action: @escaping (String, Logger) -> Void) {
        let target = model.path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !target.isEmpty else { return }
        // 抓**契约行**（以 `完成。` 开头的那行）填横幅；不能取最后一行（Feature 后面还会补话术）。
        final class LineBox: @unchecked Sendable { var lines: [String] = [] }
        let box = LineBox()
        let started = Date()
        let cancel = RunCancel()
        let logger = Logger(store: log, startedAt: started, cancel: cancel) { line in
            let t = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { box.lines.append(t) }
        }
        let runner = model
        canUndo = false
        runner.cancel = cancel
        runner.running = true
        runner.resultOK = nil
        runner.resultPath = nil
        runner.resultCancelled = false
        runner.startedAt = started
        DispatchQueue.global(qos: .userInitiated).async {
            action(target, logger)
            let seconds = Date().timeIntervalSince(started)
            let contract = EngineOutput.contractLine(from: box.lines)
            let last = contract ?? (box.lines.last ?? "")
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
                canUndo = allowUndo && ok
            }
        }
    }
}
