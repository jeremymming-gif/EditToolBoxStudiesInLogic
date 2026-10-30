import SwiftUI
import AppKit

/// 「一校核查」页（第 92 条）。
///
/// 页面本身只做三件事：选 `.tex`、勾选项、把引擎输出显示到下方日志。
/// 规则与判据都在 `Engine/slcn/proofcheck.py`（mac / Windows 两个外壳共用同一份）。
struct ProofCheckView: View {

    @ObservedObject var log: LogStore
    /// 界面状态由外部持有（`@StateObject` 在 MainWindow 里建；本机没有 `@State` 宏）
    @ObservedObject var model: ProofCheckModel

    private let help = """
    使用说明：选**初排稿的 .tex**（一般在一校之后跑）。

    这一页按《排校手册》与《流程化经验书》的规范逐条核查 26 条规则：脚注与文献引用的位置
    （本刊规范：放在**逗号前、句号后**）、半角标点混进中文、顿号后紧跟引号、集合竖线、
    图表引用间隔、英文稿里的弯引号……每条给出**行号 + 原文 + 建议 + 依据**。

    报告写在稿件同目录的 `<名字>_一校核查.md`。

    **默认只报不改**（《流程化经验书》原话「并非修改所有匹配项，稍作观察」）。
    勾了下面那个选项，会把**无歧义**的那一小撮另存为 `<名字>_一校核查后.tex`，
    **输入文件一个字节都不动** —— 建议拿它与原文 diff，逐条确认后再替换。

    规则清单与「哪些手册规范没做成机器检查（附原因）」见 Engine/docs/PROOF_RULES.md。
    """

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {

            Text(help.plainMarkdown)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

            HStack(spacing: 8) {
                DroppablePathField(text: $model.tex, placeholder: "输入初排稿 .tex 的路径，或浏览选择",
                                   disabled: model.running) { url in
                    PathDrop.receive(url, kind: .file, label: nil, log: nil,
                                     setPath: { model.tex = $0 }, onAccept: nil)
                }
                Button("浏览…") { browse() }
                    .disabled(model.running)
                // U3：运行中主按钮变成「取消」（取消 = 杀整棵进程树，见 RunCancel）
                if model.running {
                    Button {
                        model.cancel?.cancel()
                    } label: {
                        Label("取消", systemImage: "stop.circle")
                    }
                } else {
                    Button("运行") { execute() }
                        .keyboardShortcut("r", modifiers: .command)
                        .buttonStyle(.borderedProminent)
                        .disabled(model.tex.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }

            HStack(spacing: 18) {
                Toggle("顺手把无歧义的改掉（另存新文件，输入文件不动）", isOn: $model.fix)
                    .disabled(model.running)

                HStack(spacing: 6) {
                    Text("语种")
                    Picker("", selection: $model.lang) {
                        Text("自动").tag("自动")
                        Text("中文").tag("zh")
                        Text("英文").tag("en")
                    }
                    .labelsHidden()
                    .frame(width: 90)
                }
                .disabled(model.running)
            }

            HStack(spacing: 6) {
                Image(systemName: "arrow.down.doc")
                Text("也可以把 .tex 文件直接拖进来")
            }
            .font(.caption)
            .foregroundStyle(.tertiary)

            if model.running {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在核查，请查看下方运行日志…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            DiagnosisCard(entry: "proofcheck.py")

            Spacer()
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .pathDropTarget(path: $model.tex, kind: .file)
    }

    private func browse() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedFileTypes = ["tex"]
        panel.prompt = "选择初排稿"
        if panel.runModal() == .OK, let url = panel.url {
            model.tex = url.path
        }
    }

    private func execute() {
        let target = model.tex.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !target.isEmpty else { return }

        var options = ProofCheckFeature.Options()
        options.tex = target
        options.fix = model.fix
        options.lang = (model.lang == "自动") ? "" : model.lang

        // U3：每次核查一个取消句柄；`Shell.run` 把 python 子进程注册进 logger.cancel
        let cancel = RunCancel()
        model.cancel = cancel
        model.running = true
        let logger = Logger(store: log, startedAt: Date(), cancel: cancel)
        DispatchQueue.global(qos: .userInitiated).async {
            ProofCheckFeature.run(options: options, log: logger)
            DispatchQueue.main.async { model.running = false }
        }
    }
}
