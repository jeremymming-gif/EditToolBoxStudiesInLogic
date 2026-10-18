import SwiftUI
import AppKit

/// 「查库」页的界面状态。
final class BibDbQueryModel: ObservableObject {
    @Published var mode: BibDbFeature.QueryMode = .key
    @Published var text: String = ""
    @Published var hits: [BibDbFeature.QueryHit] = []
    @Published var selectedID: UUID? = nil
    @Published var running = false
    @Published var note: String = ""
    @Published var error: String = ""
    @Published var seconds: Double = 0

    /// 世代号：连续查询时只认最后一次（旧结果回来不许覆盖新结果）
    private var generation = 0

    var selected: BibDbFeature.QueryHit? {
        guard let selectedID else { return nil }
        return hits.first { $0.id == selectedID }
    }

    /// 后台查库（`bibdb.py query --json --bibtex`）。**绝不在主线程跑子进程**（案例 126）。
    func search() {
        let mode = self.mode
        let text = self.text
        generation += 1
        let gen = generation
        running = true
        error = ""
        note = ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = BibDbFeature.query(mode: mode, text: text)
            DispatchQueue.main.async {
                guard let self, gen == self.generation else { return }
                self.running = false
                self.seconds = result.seconds
                if let error = result.error {
                    self.hits = []
                    self.selectedID = nil
                    self.error = error
                    return
                }
                self.hits = result.hits
                self.note = result.note
                self.selectedID = result.hits.first?.id
            }
        }
    }

    /// 换了模式/清空输入：上一次的结果不再对应当前查询，直接清掉（避免看着旧结果复制错条目）。
    func reset() {
        generation += 1
        running = false
        hits = []
        selectedID = nil
        note = ""
        error = ""
        seconds = 0
    }
}

/// 「查库」页：按 key / DOI / 题名查编辑部引用库，选中一条复制可直接粘贴的 BibTeX。
///
/// 为什么要这一页：引用库有近 8000 条已核实文献，编辑核实一条老文献之前先查一下，
/// 命中就能**直接复用**已核实条目（而不是再花检索/AI 的钱重核一遍）。
struct BibDbQueryView: View {
    @ObservedObject var log: LogStore
    @ObservedObject var bibdb: BibDbSettings
    let isActive: Bool

    @StateObject private var model = BibDbQueryModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                BibDbPageHeader(
                    title: "引用库 · 查库",
                    subtitle: """
                    按 **key**（也认别名）、**DOI** 或 **题名**（模糊匹配）查引用库。
                    命中条目带「已核实」标记与来源期；选中一条可**复制 BibTeX** 直接粘进 `.bib`。
                    这一页只读：查询不会改库，也不会调检索/AI。
                    """)

                Divider()

                searchBar

                if !model.error.isEmpty {
                    BibDbStatusLine(kind: .error, text: model.error)
                }
                if !model.note.isEmpty && model.error.isEmpty {
                    BibDbStatusLine(kind: .warning, text: model.note)
                }
                if model.running {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("正在查询…").font(.caption).foregroundStyle(.secondary)
                    }
                } else if model.hits.isEmpty && model.error.isEmpty && !model.note.isEmpty {
                    EmptyView()
                } else if !model.hits.isEmpty {
                    resultList
                    Divider()
                    selectedDetail
                } else {
                    Text("输入查询内容后按回车或点「查询」。")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(20)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .onChange(of: model.mode) { _ in model.reset() }
    }

    // MARK: - 查询条

    private var searchBar: some View {
        HStack(spacing: 10) {
            Picker("", selection: $model.mode) {
                ForEach(BibDbFeature.QueryMode.allCases) { mode in
                    Text(mode.title).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 240)
            .disabled(model.running)

            TextField(model.mode.hint, text: $model.text)
                .textFieldStyle(.roundedBorder)
                .font(.system(.body, design: .monospaced))
                .disabled(model.running)
                .onSubmit { search() }

            Button("查询") { search() }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut("f", modifiers: .command)
                .disabled(model.running
                          || model.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
    }

    // MARK: - 结果列表

    private var resultList: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("命中 \(model.hits.count) 条").font(.callout).bold()
                if model.seconds > 0 {
                    Text("（用时 \(BibDbUI.seconds(model.seconds))）")
                        .font(.caption).foregroundStyle(.tertiary)
                }
                Spacer()
            }
            ForEach(model.hits) { hit in
                BibDbHitRow(hit: hit, selected: model.selectedID == hit.id) {
                    model.selectedID = hit.id
                }
            }
        }
    }

    // MARK: - 选中条目的详情 + 复制 BibTeX

    @ViewBuilder
    private var selectedDetail: some View {
        if let hit = model.selected {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) {
                    Text("已选中：\(hit.record.key)").font(.callout).bold()
                        .textSelection(.enabled)
                    if hit.record.verified {
                        Text("已核实").font(.caption).foregroundStyle(.green)
                    } else {
                        Text("未核实").font(.caption).foregroundStyle(.orange)
                    }
                    Spacer()
                    Button("复制 BibTeX") { copyBibTeX(hit) }
                        .buttonStyle(.borderedProminent)
                    Button("在运行日志里显示") { logHit(hit) }
                }
                Text("来源：\(hit.record.sourcesText.isEmpty ? "（无来源记录）" : hit.record.sourcesText)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                if !hit.bibtex.isEmpty {
                    ScrollView {
                        Text(hit.bibtex)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(8)
                    }
                    .frame(minHeight: 90, maxHeight: 190)
                    .background(Color(nsColor: .textBackgroundColor))
                    .overlay(RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.secondary.opacity(0.35)))
                } else {
                    BibDbStatusLine(kind: .warning,
                                    text: "这一条没有 BibTeX 文本（引擎未返回）；可用「在运行日志里显示」看字段。")
                }
            }
        }
    }

    // MARK: - 动作

    private func search() {
        let text = model.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let mode = model.mode
        model.search()
        // 查询结果在后台回填；日志这里只记「发起了什么查询」，不阻塞。
        Logger(store: log)("查引用库（\(mode.title)）：\(text)\n")
    }

    private func copyBibTeX(_ hit: BibDbFeature.QueryHit) {
        let line = BibDbClipboard.copy(hit.bibtex, what: " BibTeX（\(hit.record.key)）")
        Logger(store: log)(line)
    }

    private func logHit(_ hit: BibDbFeature.QueryHit) {
        let logger = Logger(store: log)
        logger("引用库条目 `\(hit.record.key)`：\(hit.record.title) · \(hit.record.author)"
               + (hit.record.year.isEmpty ? "" : " · \(hit.record.year)") + "\n")
        logger("  来源：\(hit.record.sourcesText.isEmpty ? "（无）" : hit.record.sourcesText)\n")
        let sorted = hit.record.fields.keys.sorted()
        for name in sorted {
            logger("  \(name) = {\(hit.record.fields[name] ?? "")}\n")
        }
    }
}

/// 一条查询结果（点击选中）。
private struct BibDbHitRow: View {
    let hit: BibDbFeature.QueryHit
    let selected: Bool
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(hit.record.title)
                        .font(.callout)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(hit.record.author
                         + (hit.record.year.isEmpty ? "" : " · \(hit.record.year)"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    if !hit.record.sourcesText.isEmpty {
                        Text("来源：\(hit.record.sourcesText)")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 6)
                VStack(alignment: .trailing, spacing: 3) {
                    Text(hit.record.key)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                    Text(hit.record.verified ? "已核实" : "未核实")
                        .font(.caption2)
                        .foregroundStyle(hit.record.verified ? Color.green : Color.orange)
                    if !hit.confidence.isEmpty {
                        Text(hit.confidence == "high" ? "高置信" : "低置信")
                            .font(.caption2)
                            .foregroundStyle(hit.confidence == "high" ? Color.secondary : Color.orange)
                    }
                }
            }
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? Color.accentColor.opacity(0.12) : Color.clear)
            .contentShape(Rectangle())
            .overlay(RoundedRectangle(cornerRadius: 6)
                .stroke(selected ? Color.accentColor.opacity(0.6) : Color.secondary.opacity(0.2)))
        }
        .buttonStyle(.plain)
    }
}
