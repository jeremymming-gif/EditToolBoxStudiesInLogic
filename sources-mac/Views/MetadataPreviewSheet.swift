import SwiftUI

/// 批223：初排页「元数据 / 补充信息」的**只读预览** sheet。
///
/// 数据来自引擎 dry-run（`--metadata-preview-out` 写出的临时 JSON，界面读完即删）：
/// 这里只做**展示**，不发命令、不写文件 —— 预览动作本身在
/// `ConvertTab.previewMetadataSources()`，判据全在引擎侧（`Engine/slcn/metadata_source.py`）。
/// 「取消 / 关闭」没有任何副作用：补充源与表单都还在界面上，随时可以再来一次。
struct MetadataPreviewSheet: View {
    /// 引擎预览结果（nil = 预览失败，只显示说明）
    let payload: ConvertFeature.MetadataPreviewPayload?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let payload {
                        rowsSection(payload)
                        if !payload.conflicts.isEmpty {
                            linesSection("冲突（正文/靠前的补充源已有该字段，未采用）",
                                         payload.conflicts, .orange)
                        }
                        if !payload.unidentified.isEmpty {
                            linesSection("未识别（源里的标签/字段没对上本刊模板槽位）",
                                         payload.unidentified, .secondary)
                        }
                        if !payload.warnings.isEmpty {
                            linesSection("提示", payload.warnings, .orange)
                        }
                    } else {
                        Text("预览失败：没有拿到引擎的预览结果（原因见主窗口日志）。"
                             + "本次未写任何文件。")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(18)
            }
            Divider()
            footer
        }
        .frame(minWidth: 780, idealWidth: 880, minHeight: 520, idealHeight: 700)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text("元数据 / 补充信息预览").font(.title3).bold()
            Text(hintText)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }

    private var hintText: String {
        guard let payload else { return "只读预览；关闭不写任何文件。" }
        let channel = payload.channel == "word" ? "Word 通道" : "LaTeX 通道"
        return "\(channel) · 语种 \(payload.lang) · 共 \(payload.rows.count) 行；"
            + "只读预览：不建产物目录、不写产物、不写交接单。"
    }

    private var footer: some View {
        HStack(spacing: 12) {
            Text("状态：已填（正文解析 / 补充文件 / 表单）· 冲突-未采用 · 未识别 · 缺项。"
                 + "「取消」不写任何文件。")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Button("关闭") { dismiss() }
                .keyboardShortcut(.defaultAction)
            Button("取消") { dismiss() }
                .keyboardShortcut(.cancelAction)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }

    // MARK: - 行表

    private func rowsSection(_ payload: ConvertFeature.MetadataPreviewPayload) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                Text("字段").frame(width: 132, alignment: .leading)
                Text("生效值").frame(maxWidth: .infinity, alignment: .leading)
                Text("来源").frame(width: 168, alignment: .leading)
                Text("状态").frame(width: 124, alignment: .leading)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            ForEach(payload.rows) { row in
                HStack(alignment: .top, spacing: 10) {
                    Text(row.label).frame(width: 132, alignment: .leading)
                    Text(row.value.isEmpty ? "—" : row.value)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text(row.source).frame(width: 168, alignment: .leading)
                    Text(row.status)
                        .frame(width: 124, alignment: .leading)
                        .foregroundStyle(statusColor(row.status))
                    if let note = row.note, !note.isEmpty {
                        Text(note).font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                .font(.callout)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                Divider()
            }
        }
    }

    private func statusColor(_ status: String) -> Color {
        if status.hasPrefix("冲突") { return .orange }
        if status == "未识别" || status == "缺项" { return .secondary }
        return .primary
    }

    private func linesSection(_ title: String, _ lines: [String], _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                Text("· " + line)
                    .font(.callout)
                    .foregroundStyle(color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
