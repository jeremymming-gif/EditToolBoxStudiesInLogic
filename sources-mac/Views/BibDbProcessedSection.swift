import SwiftUI

/// B-190：从 `BibDbReviewView.swift` **纯搬迁**的「③ 已处理」段（分页状态由本子 View 自持有）。
///
/// 备份制已取消：已处理行只显示动作/时间/记录与明细，不再展示备份 sha/path
/// （整库回退改由版本区「回退到上一个提交…」承担）。
struct BibDbProcessedSection: View {
    @ObservedObject var model: BibDbReviewModel
    @State private var limit = 100
    /// 「③ 已处理」段展开态（B-202：整行可点；默认折叠）
    @State private var sectionExpanded = false
    /// 单条已处理行的展开态（B-202：整行可点；按 `ProcessedEntry.id`）
    @State private var expandedRows: Set<String> = []

    var body: some View {
        // B-202：自定义 disclosure 行（header = AXButton「③ 已处理」，整行可点）。
        BibDbRowDisclosure(title: "③ 已处理",
                           isExpanded: sectionExpanded,
                           action: { sectionExpanded.toggle() }) {
            HStack(spacing: 8) {
                Text("③ 已处理").font(.callout).bold()
                Text("\(model.processed.count) 条留痕（冲突裁决 + 编辑/合并痕迹）")
                    .font(.caption2).foregroundStyle(.secondary)
                Spacer(minLength: 0)
                Text("默认折叠，点开看明细").font(.caption2).foregroundStyle(.tertiary)
            }
        } content: {
            VStack(alignment: .leading, spacing: 6) {
                if model.processed.isEmpty {
                    BibDbStatusLine(kind: .idle,
                                    text: "还没有写库留痕（冲突裁决 / 编辑条目 / 合并记录）。")
                }
                ForEach(Array(model.processed.prefix(limit))) { entry in
                    processedRow(entry)
                }
                if model.processed.count > limit {
                    Button("再显示 100 条（还有 \(model.processed.count - limit) 条）") {
                        limit += 100
                    }
                    .controlSize(.small)
                }
            }
            .padding(.top, 6)
        }
    }

    /// 已处理一行：默认一行摘要（动作 / key / 时间），点开看明细。
    /// B-202：自定义 disclosure 行（header = AXButton；整行可点），展开态由 `expandedRows` 受控。
    private func processedRow(_ entry: BibDbFeature.ProcessedEntry) -> some View {
        BibDbRowDisclosure(
            // AX 名字带上 key：同一动作（采用新值/合并…）可能有多行，带 key 才可按名字唯一定位。
            title: entry.actionTitle + " " + entry.key,
            isExpanded: expandedRows.contains(entry.id),
            action: { toggleRow(entry.id) }) {
            HStack(spacing: 8) {
                Text(entry.actionTitle).font(.caption).bold()
                    .lineLimit(1)
                    .frame(minWidth: 80, alignment: .leading)
                Text(entry.key)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                Spacer(minLength: 0)
                if !entry.at.isEmpty {
                    Text(entry.at)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.tertiary)
                }
            }
        } content: {
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.detail).font(.caption).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Text("记录：\(entry.key) · 类型：\(entry.kind)")
                    .font(.caption2).foregroundStyle(.tertiary).textSelection(.enabled)
            }
            .padding(.leading, 8).padding(.top, 2)
        }
        .padding(6)
        .background(Color.secondary.opacity(0.03))
        .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.secondary.opacity(0.10)))
    }

    private func toggleRow(_ id: String) {
        if expandedRows.contains(id) { expandedRows.remove(id) }
        else { expandedRows.insert(id) }
    }
}
