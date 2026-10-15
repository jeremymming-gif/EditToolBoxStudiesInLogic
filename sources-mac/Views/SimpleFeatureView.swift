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

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {

            Text(help)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

            HStack(spacing: 8) {
                TextField("输入路径或浏览选择路径", text: $model.path)
                    .textFieldStyle(.roundedBorder)
                    .disabled(model.running)
                Button("浏览…") { browse() }
                    .disabled(model.running)
                Button(model.running ? "运行中…" : "运行") { execute() }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.running || model.path.trimmingCharacters(in: .whitespaces).isEmpty)
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
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在运行，请查看下方运行日志…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
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
        let logger = Logger(store: log)
        let runner = model
        runner.running = true
        DispatchQueue.global(qos: .userInitiated).async {
            action(target, logger)
            DispatchQueue.main.async { runner.running = false }
        }
    }
}

/// 自带界面状态的普通功能页（「编译全部」「生成目录」「删除临时文件」用）
struct SimpleTab: View {

    let help: String
    let pickKind: PickKind
    @ObservedObject var log: LogStore
    let action: (_ path: String, _ log: Logger) -> Void

    @StateObject private var model = PathRunnerModel()

    var body: some View {
        SimpleFeatureView(model: model, help: help, pickKind: pickKind, log: log, action: action)
    }
}
