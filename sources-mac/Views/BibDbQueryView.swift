import SwiftUI
import AppKit

/// 高级检索的一行界面条件（与 `BibDbFeature.QueryAdvancedTerm` 一一对应）。
struct BibDbAdvancedTermRow: Identifiable {
    let id = UUID()
    var field: String
    var mode: String
    var value: String
}

/// 高级检索字段的中文标签（key 与引擎字段名一致）。
private let bibDbAdvancedFieldLabels: [(key: String, label: String)] = [
    ("author", "作者"), ("editor", "编者"), ("title", "题名"), ("booktitle", "书名"),
    ("journal", "期刊"), ("publisher", "出版社"), ("year", "年份"), ("doi", "DOI"),
    ("alias", "别名"), ("any", "任意"),
]

/// 高级检索匹配模式的中文标签。
private let bibDbAdvancedModeLabels: [(key: String, label: String)] = [
    ("contains", "包含"), ("prefix", "前缀"), ("exact", "精确"), ("fuzzy", "模糊"),
]

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

    // B-261：基本检索 / 高级检索
    @Published var advanced = false
    @Published var terms: [BibDbAdvancedTermRow] = [
        BibDbAdvancedTermRow(field: "author", mode: "contains", value: ""),
        BibDbAdvancedTermRow(field: "title", mode: "contains", value: ""),
    ]
    @Published var sort = "relevance"
    @Published var limit = 20
    @Published var strict = false
    @Published var totalMatched = 0
    @Published var truncated = false
    /// 与 `hits` 同序对齐的 why-hit（`matched[i]` 属于 `hits[i]`）
    @Published var matched: [[BibDbFeature.QueryAdvancedMatch]] = []

    /// 世代号：连续查询时只认最后一次（旧结果回来不许覆盖新结果）
    private var generation = 0

    var selected: BibDbFeature.QueryHit? {
        guard let selectedID else { return nil }
        return hits.first { $0.id == selectedID }
    }

    /// 选中那条命中的 why-hit（基本检索没有，返回空）
    var selectedMatched: [BibDbFeature.QueryAdvancedMatch] {
        guard let selectedID,
              let index = hits.firstIndex(where: { $0.id == selectedID }),
              index < matched.count else { return [] }
        return matched[index]
    }

    /// 至少一行填了值（决定「查询」按钮是否可点）
    var hasAdvancedValue: Bool {
        terms.contains { !$0.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
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
        matched = []
        totalMatched = 0
        truncated = false
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

    /// 后台高级检索（`bibdb.py query-advanced --json --bibtex`）。
    func searchAdvanced() {
        let terms = self.terms
            .filter { !$0.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .map { BibDbFeature.QueryAdvancedTerm(field: $0.field, mode: $0.mode, value: $0.value) }
        guard !terms.isEmpty else {
            error = "高级检索至少填一行条件（值不能为空）。"
            return
        }
        let sort = self.sort
        let limit = self.limit
        let strict = self.strict
        generation += 1
        let gen = generation
        running = true
        error = ""
        note = ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = BibDbFeature.queryAdvanced(terms: terms, sort: sort,
                                                    limit: limit, strict: strict)
            DispatchQueue.main.async {
                guard let self, gen == self.generation else { return }
                self.running = false
                self.seconds = result.seconds
                if let error = result.error {
                    self.hits = []
                    self.selectedID = nil
                    self.matched = []
                    self.totalMatched = 0
                    self.truncated = false
                    self.error = error
                    return
                }
                self.hits = result.hits
                self.matched = result.matched
                self.totalMatched = result.totalMatched
                self.truncated = result.truncated
                self.note = result.hits.isEmpty ? "高级检索没有命中。" : ""
                self.selectedID = result.hits.first?.id
            }
        }
    }

    /// 加一行条件（默认作者包含）
    func addTerm() {
        terms.append(BibDbAdvancedTermRow(field: "author", mode: "contains", value: ""))
    }

    /// 删一行条件（至少留一行）
    func removeTerm(id: UUID) {
        guard terms.count > 1 else { return }
        terms.removeAll { $0.id == id }
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
        matched = []
        totalMatched = 0
        truncated = false
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
    /// B-188：本页的「编辑此条」入口。key 在点按钮时冻结进 target——
    /// 编辑期间结果列表/选中项可能变化，写库对象不能跟着变。
    /// ⚠️ 用 `.sheet(item:)` 而不是 `isPresented + 另一个 @State`：后者在真机走查里
    /// 出现过 sheet 拿到空 key（`loadEditorDraft` 报「缺少要编辑的条目 key」×2），
    /// item 在呈现时刻整体捕获、顺序无歧义。
    private struct EditorTarget: Identifiable {
        let key: String
        var id: String { key }
    }
    @State private var editorTarget: EditorTarget? = nil

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

                modeBar

                if model.advanced {
                    advancedForm
                } else {
                    searchBar
                }

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
                    Text(model.advanced
                         ? "填好条件后点「查询」（多行条件同时满足才命中）。"
                         : "输入查询内容后按回车或点「查询」。")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(20)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .onChange(of: model.mode) { _ in model.reset() }
        .onChange(of: model.advanced) { _ in model.reset() }
        // B-188：共享条目编辑器（预览=引擎 dry-run；写入前 sha 校验；B-190 起写库成功后自动本地提交）。
        .sheet(item: $editorTarget) { target in
            BibDbEditorSheet(log: log, bibdb: bibdb, recordKey: target.key,
                             onWritten: {
                                 // 写库成功：用当前输入重查一次，刷新详情（不清输入、不换模式）。
                                 Logger(store: log)("引用库已写入；重新查询当前输入以刷新详情。\n")
                                 if !model.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                     model.search()
                                 }
                             })
        }
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

    // MARK: - 基本 / 高级切换

    private var modeBar: some View {
        Picker("", selection: $model.advanced) {
            Text("基本检索").tag(false)
            Text("高级检索").tag(true)
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .frame(width: 220)
        .disabled(model.running)
    }

    // MARK: - 高级检索条件

    private var advancedForm: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach($model.terms) { $row in
                HStack(spacing: 8) {
                    Picker("", selection: $row.field) {
                        ForEach(bibDbAdvancedFieldLabels, id: \.key) { item in
                            Text(item.label).tag(item.key)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 96)
                    .disabled(model.running)
                    .onChange(of: row.field) { newValue in
                        // 年份/DOI/别名不做模糊（与引擎 _SEARCH_NO_FUZZY 一致）
                        if BibDbFeature.queryAdvancedNoFuzzy.contains(newValue),
                           row.mode == "fuzzy" {
                            row.mode = "exact"
                        }
                    }

                    Picker("", selection: $row.mode) {
                        ForEach(modeChoices(for: row.field), id: \.key) { item in
                            Text(item.label).tag(item.key)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 88)
                    .disabled(model.running)

                    TextField("值（如 Smith / 1975 / 10.1007/…）", text: $row.value)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(.body, design: .monospaced))
                        .disabled(model.running)
                        .onSubmit { searchAdvanced() }

                    Button {
                        model.removeTerm(id: row.id)
                    } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.borderless)
                    .disabled(model.running || model.terms.count <= 1)
                    .help("删除这一行条件（至少留一行）")
                }
            }

            HStack(spacing: 12) {
                Button("+ 加一行") { model.addTerm() }
                    .buttonStyle(.bordered)
                    .disabled(model.running)

                Picker("排序", selection: $model.sort) {
                    Text("相关度").tag("relevance")
                    Text("年份").tag("year")
                    Text("作者").tag("author")
                }
                .frame(width: 150)
                .disabled(model.running)

                Stepper("最多 \(model.limit) 条", value: $model.limit, in: 1...200)
                    .disabled(model.running)

                Toggle("严格（模糊门槛 0.90）", isOn: $model.strict)
                    .toggleStyle(.checkbox)
                    .disabled(model.running)

                Spacer()

                Button("查询") { searchAdvanced() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut("f", modifiers: .command)
                    .disabled(model.running || !model.hasAdvancedValue)
            }

            Text("多行条件**同时满足**才命中；年份/DOI/别名不做模糊。")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }

    /// 某个字段可用的匹配模式（年份/DOI/别名去掉「模糊」）
    private func modeChoices(for field: String) -> [(key: String, label: String)] {
        if BibDbFeature.queryAdvancedNoFuzzy.contains(field) {
            return bibDbAdvancedModeLabels.filter { $0.key != "fuzzy" }
        }
        return bibDbAdvancedModeLabels
    }

    // MARK: - 结果列表

    private var resultList: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("命中 \(model.hits.count) 条").font(.callout).bold()
                if model.advanced && model.totalMatched > model.hits.count {
                    Text("／共 \(model.totalMatched) 条匹配\(model.truncated ? "，已截断" : "")")
                        .font(.caption).foregroundStyle(.secondary)
                }
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
                    Button("编辑此条") {
                        editorTarget = EditorTarget(key: hit.record.key)
                    }
                    .help("打开共享条目编辑器：预览（引擎 dry-run）→ 二次确认 → 写库（sha 校验；"
                          + "成功后自动本地提交库文件，不推送）")
                    Button("复制 BibTeX") { copyBibTeX(hit) }
                        .buttonStyle(.borderedProminent)
                    Button("在运行日志里显示") { logHit(hit) }
                }
                Text("来源：\(hit.record.sourcesText.isEmpty ? "（无来源记录）" : hit.record.sourcesText)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                if model.advanced && !model.selectedMatched.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("命中原因（\(model.selectedMatched.count) 项条件都满足）")
                            .font(.caption).bold()
                        ForEach(Array(model.selectedMatched.enumerated()), id: \.offset) { _, match in
                            Text("[\(match.field)] \(match.mode) · "
                                 + String(format: "%.3f", match.score)
                                 + (match.snippet.isEmpty ? "" : " · \(match.snippet)"))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                }
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

    private func searchAdvanced() {
        guard model.hasAdvancedValue else { return }
        let subject = model.terms
            .filter { !$0.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .map { "\($0.field)=\($0.value)" }
            .joined(separator: "；")
        model.searchAdvanced()
        Logger(store: log)("高级检索：\(subject)（排序 \(model.sort)，最多 \(model.limit) 条"
                           + (model.strict ? "，严格）" : "）") + "\n")
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
