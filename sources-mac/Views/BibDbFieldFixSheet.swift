import SwiftUI

/// 字段归位（B-188 §4.5）：**共享 sheet**，两个 registry：
///   · `missing_field`（A 类）：缺 title/year → 引擎规则给候选 → 勾选行 → 串行 `edit --apply`；
///   · `unknown_entry_type`（C 类）：entry_type 笔误 / DOI-网址-占位符 key → 改类型/改 key。
///
/// 红线：候选/风险/归位值全部来自引擎 `suggest-field-fixes --json`；本 sheet 不做字段规范化、
/// 不猜 key、**不写 sidecar**。B 类（无候选）只展示、不可勾选 —— 绝不自动写库。
struct BibDbFieldFixSheet: View {
    @ObservedObject var log: LogStore
    @ObservedObject var bibdb: BibDbSettings
    /// "missing_field" | "unknown_entry_type"
    let registry: String
    var onApplied: () -> Void = {}

    @Environment(\.dismiss) private var dismiss
    @StateObject private var model = BibDbFieldFixSheetModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider()

            if model.loading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在跑引擎 suggest-field-fixes（只读）…")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            } else if model.rows.isEmpty {
                if model.error.isEmpty {
                    Text("没有需要归位的记录（registry=\(registry)）。")
                        .foregroundStyle(.secondary)
                } else {
                    BibDbStatusLine(kind: .error, text: model.error)
                }
                Text("库文件：\(dbPathText)")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                Spacer(minLength: 0)
            } else {
                listBody
            }

            Divider()
            footer
        }
        .padding(18)
        .frame(minWidth: 840, idealWidth: 1000, minHeight: 720, idealHeight: 800,
               alignment: .topLeading)
        .onAppear { model.load(registry: registry) }
        // 二次确认挂在**本 sheet 自己**上（主 view 的 alert 会被 sheet 抑制）。
        .alert("确认应用选中的字段归位？", isPresented: $model.confirmApply) {
            Button("取消", role: .cancel) {}
            Button("确认写库", role: .destructive) { model.apply(log: log, onApplied: onApplied) }
        } message: {
            Text(model.confirmText)
        }
    }

    // MARK: - 顶部

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(model.title).font(.title3).bold()
                if !model.dbSha.isEmpty {
                    Text("库 sha \(String(model.dbSha.prefix(12)))…")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("关闭") { dismiss() }
                    .disabled(model.running)
            }
            Text(model.subtitle.plainMarkdown)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 列表

    private var listBody: some View {
        ScrollView {
            // 扁平化后用 LazyVStack：A 类真实库 285 行，非 lazy 会拖慢主线程。
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(model.items) { item in
                    switch item {
                    case .group(_, title: let title, total: let total, shown: let shown):
                        groupHeader(title: title, total: total, shown: shown)
                    case .row(let row):
                        rowCard(row)
                    }
                }
            }
            .padding(.trailing, 6)
        }
    }

    private func groupHeader(title: String, total: Int, shown: Int) -> some View {
        HStack(spacing: 8) {
            Text(title).font(.callout).bold()
            Text("\(shown)/\(total) 条")
                .font(.caption2).foregroundStyle(.secondary)
            if shown < total {
                Text("（风险筛选隐藏 \(total - shown) 条）")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            Spacer()
        }
        .padding(.top, 4)
    }

    private func rowCard(_ row: BibDbFeature.FieldFixRow) -> some View {
        let selectable = model.isSelectable(row)
        let checked = model.selected.contains(row.key)
        return HStack(alignment: .top, spacing: 10) {
            Toggle("", isOn: selectionBinding(row))
                .labelsHidden()
                .toggleStyle(.checkbox)
                .disabled(!selectable || model.running)
                .help(selectable ? "勾选后可「应用选中」" : "需人工处理：不自动写库")
            VStack(alignment: .leading, spacing: 5) {
                rowTitle(row, selectable: selectable)
                rowFacts(row)
                Text(BibDbFeature.fieldFixRiskText(row))
                    .font(.caption)
                    .foregroundStyle(selectable ? Color.primary : Color.orange)
                rowIssues(row)
                rowCandidates(row)
            }
        }
        .padding(8)
        .background(checked ? Color.accentColor.opacity(0.06) : Color.secondary.opacity(0.04))
        .overlay(RoundedRectangle(cornerRadius: 6)
            .stroke(checked ? Color.accentColor.opacity(0.45) : Color.secondary.opacity(0.15)))
    }

    @ViewBuilder
    private func rowTitle(_ row: BibDbFeature.FieldFixRow, selectable: Bool) -> some View {
        HStack(spacing: 8) {
            Text(row.key)
                .font(.system(.callout, design: .monospaced)).bold()
                .textSelection(.enabled)
            Text(row.entryType.isEmpty ? "（无类型）" : row.entryType)
                .font(.caption).foregroundStyle(Color.secondary)
            if !row.anomalyKind.isEmpty {
                Text(model.anomalyTitle(row.anomalyKind))
                    .font(.caption2)
                    .padding(.horizontal, 5).padding(.vertical, 1)
                    .background(Color.orange.opacity(0.15))
                    .cornerRadius(3)
            }
            if !selectable {
                Text("需人工（不自动写库）")
                    .font(.caption2).foregroundStyle(Color.orange)
            }
            Spacer()
        }
    }

    @ViewBuilder
    private func rowFacts(_ row: BibDbFeature.FieldFixRow) -> some View {
        if !row.missing.isEmpty {
            Text("缺字段：\(row.missing.joined(separator: "、"))")
                .font(.caption).foregroundStyle(Color.orange)
        }
        let current = model.currentFieldSummary(row)
        if !current.isEmpty {
            Text("现字段：\(current)")
                .font(.caption2).foregroundStyle(Color.secondary)
                .lineLimit(3)
                .textSelection(.enabled)
        }
    }

    @ViewBuilder
    private func rowIssues(_ row: BibDbFeature.FieldFixRow) -> some View {
        if !row.issues.isEmpty {
            Text("问题：\(row.issues.joined(separator: "；"))")
                .font(.caption2).foregroundStyle(Color.secondary.opacity(0.7))
                .lineLimit(2)
        }
    }

    @ViewBuilder
    private func rowCandidates(_ row: BibDbFeature.FieldFixRow) -> some View {
        if row.anomalyKind.isEmpty {
            missingFieldCandidates(row)
        } else {
            anomalyCandidates(row)
        }
    }

    /// A 类：每个缺失字段一组候选（单选）；无候选就明说「需人工核原稿」。
    @ViewBuilder
    private func missingFieldCandidates(_ row: BibDbFeature.FieldFixRow) -> some View {
        let fields = model.candidateFields(row)
        ForEach(fields, id: \.self) { field in
            VStack(alignment: .leading, spacing: 2) {
                Text("\(model.fieldLabel(field)) 候选")
                    .font(.caption).bold()
                    .foregroundStyle(.secondary)
                let candidates = row.candidates.filter {
                    $0.field == field && !$0.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                }
                if candidates.isEmpty {
                    Text("无候选：需人工核原稿")
                        .font(.caption2).foregroundStyle(.orange)
                } else {
                    ForEach(Array(candidates.enumerated()), id: \.offset) { pair in
                        candidateRow(row: row, field: field, candidate: pair.element)
                    }
                }
            }
        }
    }

    private func candidateRow(row: BibDbFeature.FieldFixRow, field: String,
                              candidate: BibDbFeature.FieldFixCandidate) -> some View {
        let chosen = model.fieldChoices[row.key]?[field] == candidate.value
        return Button {
            model.setFieldChoice(row: row, field: field, value: candidate.value)
        } label: {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: chosen ? "largecircle.fill.circle" : "circle")
                    .foregroundStyle(chosen ? Color.accentColor : Color.secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(candidate.value)
                        .font(.caption)
                        .textSelection(.enabled)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("来源 \(candidate.source) · 风险 \(candidate.riskText)"
                         + (candidate.evidence.isEmpty ? "" : " · 证据：\(candidate.evidence)"))
                        .font(.caption2).foregroundStyle(.secondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(model.running)
    }

    /// C 类：entry_type 候选（含「不改」）+ key_candidate（旧 key 进 aliases）。
    @ViewBuilder
    private func anomalyCandidates(_ row: BibDbFeature.FieldFixRow) -> some View {
        let typeCandidates = row.candidates.filter { $0.source == "entry_type" && !$0.value.isEmpty }
        if !typeCandidates.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                Text("entry_type 候选").font(.caption).bold().foregroundStyle(.secondary)
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: (model.entryTypeChoices[row.key] ?? "").isEmpty
                          ? "largecircle.fill.circle" : "circle")
                        .foregroundStyle((model.entryTypeChoices[row.key] ?? "").isEmpty
                                         ? Color.accentColor : Color.secondary)
                    Button("不改（保留 \(row.entryType)）") {
                        model.setEntryTypeChoice(row: row, value: "")
                    }
                    .buttonStyle(.plain)
                    .font(.caption)
                }
                ForEach(Array(typeCandidates.enumerated()), id: \.offset) { pair in
                    let candidate = pair.element
                    let chosen = model.entryTypeChoices[row.key] == candidate.value
                    Button {
                        model.setEntryTypeChoice(row: row, value: candidate.value)
                    } label: {
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: chosen ? "largecircle.fill.circle" : "circle")
                                .foregroundStyle(chosen ? Color.accentColor : Color.secondary)
                            VStack(alignment: .leading, spacing: 1) {
                                Text("\(row.entryType) → \(candidate.value)")
                                    .font(.caption)
                                Text("风险 \(candidate.riskText)"
                                     + (candidate.evidence.isEmpty ? "" : " · 证据：\(candidate.evidence)"))
                                    .font(.caption2).foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }
                            Spacer(minLength: 0)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .disabled(model.running)
                }
            }
        }
        if !row.keyCandidate.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                Toggle("改用建议 key `\(row.keyCandidate)`（旧 key 自动进 aliases）",
                       isOn: keyCandidateBinding(row))
                    .toggleStyle(.checkbox)
                    .font(.caption)
                    .disabled(model.running)
                if !row.keyReason.isEmpty {
                    Text(row.keyReason)
                        .font(.caption2).foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                if !row.keySuggestion.isEmpty && row.keySuggestion != row.keyReason {
                    Text(row.keySuggestion)
                        .font(.caption2).foregroundStyle(.tertiary)
                        .lineLimit(2)
                }
            }
        }
        if typeCandidates.isEmpty && row.keyCandidate.isEmpty {
            Text("无候选：需人工核对 key / entry_type（本 sheet 不自动写库）")
                .font(.caption2).foregroundStyle(.orange)
        }
    }

    // MARK: - 底部

    private var footer: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let summary = model.summary {
                summaryBlock(summary)
            }
            if !model.error.isEmpty {
                BibDbStatusLine(kind: .error, text: model.error)
            }
            if !model.message.isEmpty && model.error.isEmpty {
                BibDbStatusLine(kind: .idle, text: model.message)
            }
            HStack(spacing: 10) {
                Text("库：\(dbPathText)")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Picker("风险", selection: $model.riskFilter) {
                    ForEach(BibDbFieldFixSheetModel.riskFilterOptions) { option in
                        Text(option.title).tag(option.value)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 240)
                .disabled(model.running)
                .help("按候选/建议 key 的风险等级筛选显示（筛选不影响已勾选）")
                Text("选中 \(model.selected.count) 条")
                    .font(.caption).foregroundStyle(.secondary)
                Button("全选当前筛选") { model.selectAllVisible() }
                    .controlSize(.small)
                    .disabled(model.running)
                Button("清空选择") { model.selected.removeAll() }
                    .controlSize(.small)
                    .disabled(model.running || model.selected.isEmpty)
                Button("刷新列表") { model.load(registry: registry) }
                    .controlSize(.small)
                    .disabled(model.running)
            }
            HStack(spacing: 10) {
                if model.running {
                    ProgressView().controlSize(.small)
                    Text(model.progress.isEmpty ? "正在写库…" : model.progress)
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("应用选中（\(model.selected.count) 条）…") {
                    model.confirmApply = true
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.running || model.selected.isEmpty
                          || model.applyChoicesIsEmpty)
                .help("逐条串行 apply；每条写前自动备份 + sha 校验；任一条失败即停")
            }
        }
    }

    private func summaryBlock(_ summary: BibDbFeature.FieldFixApplySummary) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 8) {
                Text(summary.stopped ? "已中止" : "已完成")
                    .font(.caption).bold()
                    .foregroundStyle(summary.stopped ? .orange : .green)
                Text("写入 \(summary.writtenCount) 条"
                     + (summary.stopped ? "；停在 `\(summary.failedKey)`" : ""))
                    .font(.caption)
                Spacer()
                if !summary.dbShaBefore.isEmpty {
                    Text("库 sha \(String(summary.dbShaBefore.prefix(12)))… → "
                         + "\(summary.finalSha.isEmpty ? "（未返回）" : String(summary.finalSha.prefix(12)) + "…")")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
            if !summary.writtenKeys.isEmpty {
                Text("已写：\(summary.writtenKeys.joined(separator: "、"))")
                    .font(.caption2).foregroundStyle(.secondary)
                    .lineLimit(3)
            }
            if !summary.backupPaths.isEmpty {
                Text("备份：\(summary.backupPaths.joined(separator: "、"))")
                    .font(.caption2).foregroundStyle(.tertiary)
                    .lineLimit(2)
                    .textSelection(.enabled)
            }
        }
        .padding(6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.05))
        .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.secondary.opacity(0.18)))
    }

    // MARK: - 绑定 / 文案

    private func selectionBinding(_ row: BibDbFeature.FieldFixRow) -> Binding<Bool> {
        Binding(get: { model.selected.contains(row.key) },
                set: { on in
                    if on && model.isSelectable(row) {
                        model.selected.insert(row.key)
                    } else {
                        model.selected.remove(row.key)
                    }
                })
    }

    private func keyCandidateBinding(_ row: BibDbFeature.FieldFixRow) -> Binding<Bool> {
        Binding(get: { model.keyCandidateChoices.contains(row.key) },
                set: { on in
                    if on { model.keyCandidateChoices.insert(row.key) }
                    else { model.keyCandidateChoices.remove(row.key) }
                })
    }

    private var dbPathText: String {
        let path = BibDbFeature.existingDBPath() ?? ""
        return path.isEmpty ? "（引用库文件不存在：请先到「入藏」页导入或到「引用库设置」页指定笔记仓）"
                            : path
    }
}

// MARK: - 扁平列表项（LazyVStack 要稳定的 id）

/// 风险筛选的显示项（Swift 的 tuple 不支持 key path，不能直接给 ForEach 当 id）。
private struct RiskFilterOption: Identifiable {
    let value: String
    let title: String
    var id: String { value }
}

private enum FieldFixListItem: Identifiable {
    case group(id: String, title: String, total: Int, shown: Int)
    case row(BibDbFeature.FieldFixRow)

    var id: String {
        switch self {
        case .group(let id, _, _, _): return "g-\(id)"
        case .row(let row): return "r-\(row.key)"
        }
    }
}

// MARK: - 字段归位本地状态

/// 为什么模型在自己的文件里且 `private`：sheet 冻结签名只有 4 个参数，本地选择状态自己持；
/// `private` 避免与兄弟分片新增的辅助类型撞名。
private final class BibDbFieldFixSheetModel: ObservableObject {
    @Published var loading = false
    @Published var running = false
    @Published var error = ""
    @Published var message = ""
    @Published var rows: [BibDbFeature.FieldFixRow] = []
    @Published var groups: [BibDbFeature.FieldFixGroup] = []
    @Published var selected: Set<String> = []
    /// row.key → (字段 → 采用值)
    @Published var fieldChoices: [String: [String: String]] = [:]
    /// row.key → 采用 entry_type（"" = 不改）
    @Published var entryTypeChoices: [String: String] = [:]
    /// 勾了「改用建议 key」的行
    @Published var keyCandidateChoices: Set<String> = []
    @Published var riskFilter = "all"
    @Published var dbSha = ""
    @Published var loadedSeconds: Double = 0
    @Published var confirmApply = false
    @Published var summary: BibDbFeature.FieldFixApplySummary?
    @Published var progress = ""

    static let riskFilterOptions: [RiskFilterOption] = [
        RiskFilterOption(value: "all", title: "全部"),
        RiskFilterOption(value: "low", title: "低风险"),
        RiskFilterOption(value: "medium", title: "中风险"),
        RiskFilterOption(value: "high", title: "高风险"),
    ]

    private var generation = 0
    /// 当前 registry（"missing_field" / "unknown_entry_type"）：重读时必须沿用，
    /// 不能从行特征反推（全部修完后 C 类行会消失，反推会把 A 类列表顶上）。
    private var registry = "missing_field"

    var title: String {
        registry == "unknown_entry_type" ? "字段归位 · C 类（类型 / key 异常）"
                                         : "字段归位 · A 类（缺字段）"
    }

    var subtitle: String {
        if registry == "unknown_entry_type" {
            return "C 类 = entry_type 笔误 / key 是 DOI·网址·占位符：按 `anomaly_kind` 分组；"
                + "entry_type 候选可选，`key_candidate` 非空时可「改用建议 key」（旧 key 自动进 `aliases`）。"
                + "无候选行只展示、**不自动写库**。"
        }
        return "A 类 = 题名/年份/出版信息写进了别的字段：候选、证据、风险全部来自引擎 "
            + "`suggest-field-fixes`（只读）。勾选行 →「应用选中」= 逐条 `edit --apply`"
            + "（串行 sha 链 + 每条自动备份）。无候选（B 类）只展示，**不自动写库**。"
    }

    func anomalyTitle(_ kind: String) -> String {
        switch kind {
        case "unknown_entry_type": return "entry_type 笔误"
        case "doi_key": return "DOI/网址当 key"
        case "placeholder_key": return "占位符 key"
        default: return kind.isEmpty ? "类型/key 异常" : kind
        }
    }

    func fieldLabel(_ field: String) -> String {
        switch field {
        case "title": return "题名"
        case "year": return "年份"
        case "publisher": return "出版者"
        default: return field
        }
    }

    // MARK: 加载

    func load(registry: String, keepStatus: Bool = false) {
        self.registry = registry
        generation += 1
        let gen = generation
        loading = true
        if !keepStatus {
            error = ""
            message = ""
            summary = nil
        }
        rows = []
        groups = []
        selected.removeAll()
        fieldChoices = [:]
        entryTypeChoices = [:]
        keyCandidateChoices.removeAll()
        confirmApply = false
        let db = BibDbFeature.existingDBPath() ?? ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let outcome = BibDbFeature.loadFieldFixRows(db: db, registry: registry)
            DispatchQueue.main.async {
                guard let self, gen == self.generation else { return }
                self.loading = false
                self.loadedSeconds = outcome.seconds
                guard outcome.error == nil else {
                    self.error = outcome.error ?? "读取字段归位候选失败。"
                    return
                }
                self.rows = outcome.rows
                self.groups = BibDbFeature.fieldFixGroups(outcome.rows)
                self.dbSha = outcome.dbSha256
                for row in outcome.rows {
                    self.fieldChoices[row.key] = BibDbFeature.defaultFieldFixChoices(row)
                    let bestType = row.candidates
                        .filter { $0.source == "entry_type" && !$0.value.isEmpty }
                        .min { $0.riskOrder < $1.riskOrder }
                    self.entryTypeChoices[row.key] = bestType?.value ?? ""
                }
                if !keepStatus {
                    self.message = "已读取 \(outcome.rows.count) 条候选（用时 "
                        + "\(BibDbUI.seconds(outcome.seconds))；只读）。"
                }
            }
        }
    }

    // MARK: 选择

    func isSelectable(_ row: BibDbFeature.FieldFixRow) -> Bool {
        if row.anomalyKind.isEmpty {
            return row.candidates.contains {
                !$0.field.isEmpty
                    && !$0.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            }
        }
        let hasType = row.candidates.contains {
            $0.source == "entry_type" && !$0.value.isEmpty
        }
        return hasType || !row.keyCandidate.isEmpty
    }

    func setFieldChoice(row: BibDbFeature.FieldFixRow, field: String, value: String) {
        var perRow = fieldChoices[row.key] ?? [:]
        perRow[field] = value
        fieldChoices[row.key] = perRow
    }

    func setEntryTypeChoice(row: BibDbFeature.FieldFixRow, value: String) {
        entryTypeChoices[row.key] = value
        if !isSelectable(row) && value.isEmpty { selected.remove(row.key) }
    }

    /// 要展示/可选的候选字段：**先列缺失字段，再列其余候选字段**。
    /// 为什么不能只列 `missing`：引擎会对同一条记录同时给出 publisher 之类候选
    /// （如 author 里混着「東京：理想社」），`defaultFieldFixChoices` 也会把它带进写入草稿；
    /// 不展示就等于「偷偷写了用户没看见的字段」。
    func candidateFields(_ row: BibDbFeature.FieldFixRow) -> [String] {
        var fields = row.missing
        var extras: [String] = []
        for candidate in row.candidates where !candidate.field.isEmpty {
            if !fields.contains(candidate.field) && !extras.contains(candidate.field) {
                extras.append(candidate.field)
            }
        }
        fields.append(contentsOf: extras.sorted())
        return fields
    }

    func currentFieldSummary(_ row: BibDbFeature.FieldFixRow) -> String {
        var names: [String] = []
        if row.anomalyKind.isEmpty {
            names = candidateFields(row)
            for candidate in row.candidates where !candidate.source.isEmpty
                && candidate.source != "entry_type" {
                if !names.contains(candidate.source) { names.append(candidate.source) }
            }
        } else {
            names = ["author", "title", "year", "journal", "booktitle"]
        }
        let parts: [String] = names.compactMap { name in
            let value = row.fields[name] ?? ""
            guard !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
            return "\(name)=\(value.count > 60 ? String(value.prefix(60)) + "…" : value)"
        }
        return parts.isEmpty ? "" : parts.joined(separator: "；")
    }

    // MARK: 风险筛选（只影响显示）

    private func riskOrders(_ row: BibDbFeature.FieldFixRow) -> Set<Int> {
        var orders = Set<Int>()
        for candidate in row.candidates { orders.insert(candidate.riskOrder) }
        if !row.keyCandidate.isEmpty {
            switch row.keyRisk.lowercased() {
            case "low": orders.insert(0)
            case "medium": orders.insert(1)
            case "high": orders.insert(2)
            default: orders.insert(3)
            }
        }
        return orders
    }

    private func passesRiskFilter(_ row: BibDbFeature.FieldFixRow) -> Bool {
        switch riskFilter {
        case "low": return riskOrders(row).contains(0)
        case "medium": return riskOrders(row).contains(1)
        case "high": return riskOrders(row).contains(2)
        default: return true
        }
    }

    /// 扁平化：组头 + 行（LazyVStack 直接吃这个数组）。
    var items: [FieldFixListItem] {
        var out: [FieldFixListItem] = []
        for group in groups {
            let visible = group.rows.filter { passesRiskFilter($0) }
            out.append(.group(id: group.id, title: group.title,
                              total: group.count, shown: visible.count))
            for row in visible { out.append(.row(row)) }
        }
        return out
    }

    func selectAllVisible() {
        for item in items {
            if case .row(let row) = item, isSelectable(row) {
                selected.insert(row.key)
            }
        }
    }

    // MARK: 应用

    /// 选中的行里至少有一条「有实际动作」才允许应用（避免空转写库）。
    var applyChoicesIsEmpty: Bool {
        let chosen = rows.filter { selected.contains($0.key) }
        return chosen.isEmpty || chosen.allSatisfy { row in
            let fieldValues = (fieldChoices[row.key] ?? [:]).filter {
                !$0.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            }
            let type = entryTypeChoices[row.key] ?? ""
            let newKey = keyCandidateChoices.contains(row.key)
            return fieldValues.isEmpty && type.isEmpty && !newKey
        }
    }

    var confirmText: String {
        "将逐条串行写库 \(selected.count) 条（每条：引擎 sha 校验 + 自动备份 → 原子写 → 复核）；"
            + "任一条失败即停，已写的保留。\n备份目录：\(BibDbFeature.backupDirectory)"
    }

    func apply(log: LogStore, onApplied: @escaping () -> Void) {
        let chosen = rows.filter { selected.contains($0.key) }
        guard !chosen.isEmpty else {
            error = "没有勾选要应用的条目。"
            return
        }
        var choices: [String: BibDbFeature.FieldFixChoice] = [:]
        for row in chosen {
            var values: [String: String] = [:]
            for (field, value) in fieldChoices[row.key] ?? [:] {
                if value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
                values[field] = value
            }
            let type = (entryTypeChoices[row.key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let newKey = keyCandidateChoices.contains(row.key) ? row.keyCandidate : nil
            choices[row.key] = BibDbFeature.FieldFixChoice(values: values,
                                                           entryType: type.isEmpty ? nil : type,
                                                           newKey: newKey)
        }
        let db = BibDbFeature.existingDBPath() ?? ""
        let count = chosen.count
        running = true
        error = ""
        message = ""
        summary = nil
        progress = "正在逐条写库（串行 \(count) 条）…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let outcome = BibDbFeature.applySelectedFieldFixes(db: db, rows: chosen,
                                                               choices: choices)
            DispatchQueue.main.async {
                guard let self else { return }
                self.running = false
                self.progress = ""
                self.summary = outcome
                let logger = Logger(store: log)
                logger("字段归位：写入 \(outcome.writtenCount)/\(count) 条"
                       + (outcome.error == nil ? "" : "；失败：\(outcome.error ?? "")") + "\n")
                if !outcome.writtenKeys.isEmpty {
                    logger("  已写：\(outcome.writtenKeys.joined(separator: "、"))\n")
                }
                if !outcome.backupPaths.isEmpty {
                    logger("  备份：\(outcome.backupPaths.joined(separator: "、"))\n")
                }
                logger("  库 sha：\(outcome.dbShaBefore) → \(outcome.finalSha)\n")
                if let failure = outcome.error, !failure.isEmpty {
                    self.error = "已写 \(outcome.writtenCount) 条；停在 `\(outcome.failedKey)`：\(failure)\n"
                        + "其余未写。库里已写部分不会回滚（用备份恢复见下方备份路径）。"
                } else {
                    self.message = "全部 \(outcome.writtenCount) 条已写入（库 sha 已更新）。"
                }
                if outcome.writtenCount > 0 {
                    self.selected.removeAll()
                    onApplied()
                }
                // 库变了：重读候选列表（已修好的行会自然消失）；summary 保留给用户看。
                self.load(registry: self.registry, keepStatus: true)
            }
        }
    }

}
