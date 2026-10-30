import SwiftUI
import AppKit

/// 失败诊断卡（B-285b）：红卡 = 失败；中性卡 = 有结果但不是程序出错。
///
/// 卡里的字全部来自引擎的 `diagnose.v1`（类别标题 / 人话原因 / 2–4 步下一步）；
/// 这里只负责画，并在本地完成三个动作：打开日志、复制诊断包、查看文档。
struct DiagnosisCard: View {
    let entry: String
    @ObservedObject private var center = DiagnosisCenter.shared
    @State private var evidenceShown = false

    var body: some View {
        if let card = center.card(for: entry) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: card.level == .error
                          ? "xmark.octagon.fill" : "exclamationmark.triangle.fill")
                    Text(card.title).font(.callout).fontWeight(.semibold)
                    Spacer()
                    Text("退出码 \(card.exitCode)｜\(card.category)")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .foregroundStyle(card.level == .error ? Color.red : Color.orange)

                Text(card.reason)
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)

                if !card.steps.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("下一步：").font(.caption).bold()
                        ForEach(Array(card.steps.enumerated()), id: \.offset) { index, step in
                            Text("\(index + 1). \(step)")
                                .font(.caption)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }

                HStack(spacing: 8) {
                    Button("打开日志") { openLog(card.logPath) }
                        .controlSize(.small)
                        .disabled(card.logPath == nil)
                    Button("复制诊断包") { copyPack(card.pack) }
                        .controlSize(.small)
                    Button("查看文档") { openDoc() }
                        .controlSize(.small)
                    Button(evidenceShown ? "隐藏日志证据" : "显示日志证据") {
                        evidenceShown.toggle()
                    }
                    .controlSize(.small)
                    if card.fallback {
                        Text("旧版引擎：已回退为退出码 + 日志尾部")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }

                if evidenceShown {
                    Text(card.evidence.isEmpty
                         ? "（本次没有捕获到日志行）"
                         : card.evidence.joined(separator: "\n"))
                        .font(.system(.caption2, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 10)
                .fill((card.level == .error ? Color.red : Color.orange).opacity(0.08)))
            .overlay(RoundedRectangle(cornerRadius: 10)
                .stroke((card.level == .error ? Color.red : Color.orange).opacity(0.25)))
        }
    }

    private func openLog(_ path: String?) {
        guard let path else { return }
        let url = URL(fileURLWithPath: path)
        if FileManager.default.fileExists(atPath: path) {
            NSWorkspace.shared.open(url)
        } else {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        }
    }

    private func copyPack(_ pack: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(pack, forType: .string)
    }

    private func openDoc() {
        let toolDir = EngineLocator.convertTool().map { ($0 as NSString).deletingLastPathComponent }
        guard let path = Diagnose.docPath(toolDir: toolDir) else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }
}
