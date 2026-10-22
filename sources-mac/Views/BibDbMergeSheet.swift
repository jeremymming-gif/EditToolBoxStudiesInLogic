import SwiftUI

/// 对照 / 合并（B-188 §4.4）：**共享 sheet**——重复对、近重复对都从这里走。
///
/// 红线：**完全不自动合并**。本 sheet 只提供「逐字段选源 + 新 key + 预览 + 二次确认」；
/// 合并后的字段取值、alias 保留、记录数变化、key 查重全部由引擎 `merge-records` 判定。
/// 近重复（`Segerberg1971` / `Segerberg1971b` 之类）同样只按用户选择合并，不自动判同。
struct BibDbMergeSheet: View {
    @ObservedObject var log: LogStore
    @ObservedObject var bibdb: BibDbSettings
    /// 2+ 条记录（重复对/近重复对；也认 alias，加载时解析成 canonical key）
    let keys: [String]
    var onMerged: () -> Void = {}

    @Environment(\.dismiss) private var dismiss
    @StateObject private var model = BibDbMergeSheetModel()
    /// 嵌套打开「编辑此条」的目标；编辑写库后本 sheet 的对照材料必须重读。
    @State private var editorTarget: MergeEditorTarget?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider()

            if model.loading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在逐条读库（query --bibtex）…")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            } else if model.records.isEmpty {
                BibDbStatusLine(kind: .error,
                                text: model.loadError.isEmpty ? "没有读到对照材料。" : model.loadError)
                Text("库文件：\(dbPathText)")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                Spacer(minLength: 0)
            } else {
                mergeBody
            }

            Divider()
            footer
        }
        .padding(18)
        .frame(minWidth: 840, idealWidth: 1000, minHeight: 720, idealHeight: 780,
               alignment: .topLeading)
        .onAppear { model.load(keys: keys) }
        .sheet(item: $editorTarget) { target in
            // 嵌套 sheet（允许）：编辑写库后本页不能继续用旧对照材料，提示重读。
            BibDbEditorSheet(log: log, bibdb: bibdb, recordKey: target.key,
                             onWritten: { model.markRecordsChanged() })
        }
        // 二次确认挂在**本 sheet 自己**上（主 view 的 alert 会被 sheet 抑制）。
        .alert("确认合并？", isPresented: $model.confirmMerge) {
            Button("取消", role: .cancel) {}
            Button("确认合并（写库）", role: .destructive) { model.apply() }
        } message: {
            Text(model.confirmText)
        }
        .onChange(of: model.succeeded) { done in
            guard done else { return }
            Logger(store: log)(model.successText + "\n")
            onMerged()
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.6) { dismiss() }
        }
    }

    // MARK: - 顶部

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("对照 / 合并记录").font(.title3).bold()
                if !model.records.isEmpty {
                    Text(model.records.map(\.key).joined(separator: "  ×  "))
                        .font(.system(.callout, design: .monospaced))
                        .textSelection(.enabled)
                }
                Spacer()
                Button("关闭") { dismiss() }
                    .disabled(model.applying)
            }
            Text(("**绝不自动合并**：逐字段选来源、新 key 可改，先跑引擎 dry-run 预览（不写库），"
                  + "再二次确认写入。写前自动备份 + 库 sha 校验；旧 key 全部保留进 `aliases`。")
                .plainMarkdown)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 主体

    private var mergeBody: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if model.recordsChanged {
                    BibDbStatusLine(kind: .warning,
                                    text: "记录已变（刚才编辑写库）：旧对照材料与旧预览已作废。"
                                        + "请点「重新读取对照材料」后再合并。")
                }
                recordCards
                keySection
                fieldTable
                previewSection
                statusBlock
            }
            .padding(.trailing, 6)
        }
    }

    @ViewBuilder
    private var recordCards: some View {
        if model.records.count <= 2 {
            HStack(alignment: .top, spacing: 12) {
                ForEach(model.records, id: \.key) { record in
                    recordCard(record)
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(model.records, id: \.key) { record in
                    recordCard(record)
                }
            }
        }
    }

    private func recordCard(_ record: BibDbFeature.MergeRecord) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(record.key)
                    .font(.system(.callout, design: .monospaced)).bold()
                    .textSelection(.enabled)
                Text(record.entryType)
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("编辑此条") {
                    editorTarget = MergeEditorTarget(key: record.key)
                }
                .controlSize(.small)
                .disabled(model.applying || model.running)
            }
            if let meta = model.metas[record.key] {
                HStack(spacing: 8) {
                    Text("来源期：\(meta.issues.isEmpty ? "（无来源记录）" : meta.issues.joined(separator: "、"))")
                    Text(meta.verified ? "已核实" : "未核实")
                        .foregroundStyle(meta.verified ? .green : .orange)
                }
                .font(.caption2).foregroundStyle(.secondary)
                .textSelection(.enabled)
                if !meta.sourcesText.isEmpty {
                    Text("来源：\(meta.sourcesText)")
                        .font(.caption2).foregroundStyle(.tertiary)
                        .textSelection(.enabled)
                        .lineLimit(2)
                }
            } else {
                Text("来源/核实信息未读到（不影响合并判据）")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            Text("aliases：\(record.aliases.isEmpty ? "（无）" : record.aliases.joined(separator: "、"))")
                .font(.caption2).foregroundStyle(.secondary)
                .textSelection(.enabled)
            if !record.bibtex.isEmpty {
                DisclosureGroup("原始 BibTeX") {
                    Text(record.bibtex)
                        .font(.system(.caption2, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(6)
                        .background(Color(nsColor: .textBackgroundColor))
                }
                .font(.caption2)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.05))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.18)))
    }

    /// 新 key + 可选的 entry_type（两条类型不一致时引擎要求显式 `--entry-type`）。
    private var keySection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Text("合并后新 key")
                    .foregroundStyle(.secondary)
                    .frame(width: 110, alignment: .trailing)
                TextField("作者姓+年份，如 Tindale2015", text: newKeyBinding)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                    .frame(width: 280)
                Text("默认建议 = 第一作者姓 + 年份（引擎同口径）；可手改")
                    .font(.caption2).foregroundStyle(.tertiary)
                Spacer()
            }
            if model.entryTypeMismatch {
                HStack(spacing: 10) {
                    Text("entry_type")
                        .foregroundStyle(.secondary)
                        .frame(width: 110, alignment: .trailing)
                    TextField("", text: entryTypeBinding)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(.body, design: .monospaced))
                        .frame(width: 200)
                    Text("两侧 entry_type 不同（"
                         + Set(model.records.map(\.entryType)).sorted().joined(separator: " / ")
                         + "）：引擎要求显式指定合并后的类型")
                        .font(.caption2).foregroundStyle(.orange)
                    Spacer()
                }
            }
            let errors = model.validationErrors
            if !errors.isEmpty {
                BibDbStatusLine(kind: .error, text: errors.joined(separator: "\n"))
            }
        }
    }

    /// 字段行对齐 + 差异高亮 + 逐字段选来源。
    private var fieldTable: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text("字段").font(.caption).bold().frame(width: 110, alignment: .leading)
                ForEach(model.records, id: \.key) { record in
                    Text(record.key)
                        .font(.system(.caption2, design: .monospaced)).bold()
                        .frame(minWidth: 160, maxWidth: .infinity, alignment: .leading)
                }
                Text("采用").font(.caption).bold().frame(width: 160, alignment: .leading)
            }
            .padding(.vertical, 3)
            Divider()
            ForEach(model.rows) { row in
                HStack(alignment: .top, spacing: 8) {
                    Text(row.field)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(row.differs ? .primary : .secondary)
                        .frame(width: 110, alignment: .leading)
                    ForEach(model.records, id: \.key) { record in
                        Text(valueText(row.values[record.key] ?? ""))
                            .font(.caption)
                            .frame(minWidth: 160, maxWidth: .infinity, alignment: .leading)
                            .padding(5)
                            .background(cellBackground(row: row, key: record.key))
                            .overlay(RoundedRectangle(cornerRadius: 4)
                                .stroke(row.differs ? Color.orange.opacity(0.35) : Color.clear))
                    }
                    Picker("", selection: choiceBinding(row.field)) {
                        ForEach(model.records, id: \.key) { record in
                            Text(record.key).tag(record.key)
                        }
                    }
                    .labelsHidden()
                    .controlSize(.small)
                    .frame(width: 160)
                }
                .padding(.vertical, 2)
                Divider()
            }
            Text("橙色 = 两侧值不同；每行的「采用」= 引擎 `--field-source 字段=key`。"
                 + "相同值默认取第一侧（引擎默认口径）。")
                .font(.caption2).foregroundStyle(.tertiary)
                .padding(.top, 2)
        }
    }

    /// 预览（dry-run）：合并后 key/字段、aliases、记录数、库 sha；确认前**必须**有它。
    @ViewBuilder
    private var previewSection: some View {
        if let preview = model.preview {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 10) {
                    Label("预览（引擎 dry-run：库未写）", systemImage: "eye")
                        .font(.callout).bold()
                    if preview.seconds > 0 {
                        Text("用时 \(BibDbUI.seconds(preview.seconds))")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                    Spacer()
                }
                Text("合并后：`\(preview.newKey)`（\(preview.entryType)）；"
                     + "记录 \(preview.recordsBefore) → \(preview.recordsAfter)")
                    .font(.caption)
                    .textSelection(.enabled)
                Text("aliases（旧 key 全保留）：\(preview.aliases.isEmpty ? "（无）" : preview.aliases.joined(separator: "、"))")
                    .font(.caption2).foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Text("库 sha：\(shaShort(preview.dbSha256Before)) → \(shaShort(preview.dbSha256AfterEstimate))（预计）")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                let merged = preview.fieldRows.filter { !$0.chosenValue.isEmpty }
                if merged.isEmpty {
                    Text("合并后没有非空字段（请检查预览）。")
                        .font(.caption2).foregroundStyle(.orange)
                } else {
                    Text("合并后字段 \(merged.count) 项：").font(.caption).bold()
                    ForEach(merged) { row in
                        HStack(alignment: .top, spacing: 6) {
                            Text(row.field)
                                .font(.system(.caption, design: .monospaced))
                                .frame(width: 100, alignment: .leading)
                            Text(valueText(row.chosenValue))
                                .font(.caption)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 0)
                        }
                    }
                }
            }
            .padding(8)
            .background(Color.accentColor.opacity(0.05))
            .overlay(RoundedRectangle(cornerRadius: 6)
                .stroke(Color.accentColor.opacity(0.25)))
        }
    }

    @ViewBuilder
    private var statusBlock: some View {
        if !model.error.isEmpty {
            BibDbStatusLine(kind: .error, text: model.error)
        }
        if !model.message.isEmpty && model.error.isEmpty {
            BibDbStatusLine(kind: .idle, text: model.message)
        }
        if model.succeeded && !model.successText.isEmpty {
            BibDbStatusLine(kind: .ok, text: model.successText)
        }
        if model.preview != nil && model.previewedInputs != model.currentInputs
            && !model.recordsChanged {
            BibDbStatusLine(kind: .warning,
                            text: "新 key / 字段选择 / entry_type 改过：上次预览已作废，请重新预览再合并。")
        }
    }

    // MARK: - 底部动作

    private var footer: some View {
        HStack(spacing: 10) {
            Text("库：\(dbPathText)")
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
            Button("重新读取对照材料") { model.reload() }
                .controlSize(.small)
                .disabled(model.running || model.applying)
            Spacer()
            if model.running || model.applying {
                ProgressView().controlSize(.small)
                Text(model.stage).font(.caption).foregroundStyle(.secondary)
            }
            Button("预览（dry-run，不写库）") { model.runPreview() }
                .disabled(model.running || model.applying || model.recordsChanged
                          || !model.validationErrors.isEmpty)
            Button("合并…") { model.confirmMerge = true }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .disabled(!model.canMerge)
                .help("必须先预览；预览后改动新 key/字段选择会作废预览")
        }
    }

    // MARK: - 绑定

    private var newKeyBinding: Binding<String> {
        Binding(get: { model.newKey }, set: { model.setNewKey($0) })
    }

    private var entryTypeBinding: Binding<String> {
        Binding(get: { model.entryType }, set: { model.setEntryType($0) })
    }

    private func choiceBinding(_ field: String) -> Binding<String> {
        Binding(get: { model.choices[field] ?? model.records.first?.key ?? "" },
                set: { model.setChoice(field: field, sourceKey: $0) })
    }

    // MARK: - 文案小工具

    private func cellBackground(row: BibDbFeature.MergeFieldRow, key: String) -> Color {
        let chosen = model.choices[row.field] ?? row.chosen
        if chosen == key { return Color.accentColor.opacity(0.16) }
        if row.differs { return Color.orange.opacity(0.07) }
        return Color.clear
    }

    private var dbPathText: String {
        let path = BibDbFeature.existingDBPath() ?? ""
        return path.isEmpty ? "（引用库文件不存在：请先到「入藏」页导入或到「引用库设置」页指定笔记仓）"
                            : path
    }

    private func valueText(_ value: String) -> String {
        value.isEmpty ? "（空）" : value
    }

    private func shaShort(_ sha: String) -> String {
        sha.isEmpty ? "（未返回）" : String(sha.prefix(12)) + "…"
    }
}

/// 嵌套编辑器的 item 包装（`String` 不满足 Identifiable）。
private struct MergeEditorTarget: Identifiable {
    let key: String
    var id: String { key }
}

// MARK: - 合并本地状态

private final class BibDbMergeSheetModel: ObservableObject {
    /// 预览与「当前输入」绑定：任一输入改了，上次预览即作废（契约要求）。
    struct Inputs: Equatable {
        var newKey: String
        var entryType: String
        var fieldSources: [String: String]
    }

    @Published var loading = false
    @Published var running = false
    @Published var applying = false
    @Published var stage = ""
    @Published var error = ""
    @Published var message = ""
    @Published var records: [BibDbFeature.MergeRecord] = []
    /// key → 来源期/核实标记（Wave1 对照材料不带，只读 query 补展示）。
    @Published var metas: [String: BibDbFeature.RecordInfo] = [:]
    @Published var rows: [BibDbFeature.MergeFieldRow] = []
    /// 字段 → 采用哪条记录的值（引擎 `--field-source 字段=key`）
    @Published var choices: [String: String] = [:]
    @Published var newKey = ""
    @Published var entryType = ""
    @Published var entryTypeMismatch = false
    @Published var loadedSha = ""
    @Published var preview: BibDbFeature.MergePreviewOutcome?
    @Published var previewedInputs: Inputs?
    @Published var confirmMerge = false
    @Published var successText = ""
    @Published var succeeded = false
    /// 嵌套编辑器写库后置 true：本页旧材料/旧预览都不再可信（不自动重载，避免吞掉用户选择）。
    @Published var recordsChanged = false
    /// 加载失败的原因（与「预览/合并失败」的 error 分开：列表空时只显示这条）。
    @Published var loadError = ""

    private var generation = 0
    /// 用户最初传进来的 keys（重读时沿用；alias 解析后仍能查到 canonical）。
    private var requestedKeys: [String] = []

    var currentInputs: Inputs {
        Inputs(newKey: newKey.trimmingCharacters(in: .whitespacesAndNewlines),
               entryType: entryType.trimmingCharacters(in: .whitespacesAndNewlines),
               fieldSources: choices)
    }

    var validationErrors: [String] {
        BibDbFeature.validateMerge(keys: records.map(\.key), newKey: newKey)
    }

    var canMerge: Bool {
        guard preview != nil, previewedInputs == currentInputs else { return false }
        return !recordsChanged && !running && !applying && !succeeded
    }

    var confirmText: String {
        "将把 \(records.map(\.key).joined(separator: "、")) 合并为 `\(newKey)`（entry_type \(entryType.isEmpty ? "沿用第一条" : entryType)）；"
            + "旧 key 全部保留进 aliases，记录数 -\(max(0, records.count - 1))。\n"
            + "写前引擎自动备份（\(BibDbFeature.backupDirectory)）并校验库 sha。"
    }

    // MARK: 加载 / 重读

    func load(keys: [String]) {
        requestedKeys = keys
        generation += 1
        let gen = generation
        loading = true
        error = ""
        message = ""
        loadError = ""
        records = []
        metas = [:]
        rows = []
        choices = [:]
        preview = nil
        previewedInputs = nil
        successText = ""
        succeeded = false
        recordsChanged = false
        let db = BibDbFeature.existingDBPath() ?? ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let outcome = BibDbFeature.loadMergeDraft(db: db, keys: keys)
            let sha = db.isEmpty ? "" : BibDbFeature.dbManifest(db: db).sha256
            var metas: [String: BibDbFeature.RecordInfo] = [:]
            if outcome.error == nil {
                for record in outcome.records {
                    let query = BibDbFeature.query(mode: .key, text: record.key)
                    if let hit = query.hits.first { metas[record.key] = hit.record }
                }
            }
            DispatchQueue.main.async {
                guard let self, gen == self.generation else { return }
                self.loading = false
                self.loadedSha = sha
                guard outcome.error == nil else {
                    self.loadError = outcome.error ?? "读取对照材料失败。"
                    return
                }
                self.records = outcome.records
                self.metas = metas
                self.rows = BibDbFeature.mergeFieldRows(records: outcome.records)
                var defaults: [String: String] = [:]
                for row in self.rows { defaults[row.field] = row.chosen }
                self.choices = defaults
                self.newKey = BibDbFeature.suggestedMergeKey(records: outcome.records)
                self.entryTypeMismatch = Set(outcome.records.map(\.entryType)).count > 1
                self.entryType = self.entryTypeMismatch ? (outcome.records.first?.entryType ?? "") : ""
                self.message = "已读取 \(outcome.records.count) 条对照材料（用时 "
                    + "\(BibDbUI.seconds(outcome.seconds))）。逐字段选来源后可预览。"
            }
        }
    }

    func reload() {
        load(keys: requestedKeys)
    }

    /// 嵌套编辑器写库成功后的失效：不自动重载（避免吞掉用户已做的字段选择），
    /// 由用户点「重新读取对照材料」显式重读。
    func markRecordsChanged() {
        recordsChanged = true
        preview = nil
        previewedInputs = nil
        error = ""
        message = "记录已变：请点「重新读取对照材料」后再合并。"
    }

    // MARK: 输入变化（作废预览）

    private func invalidatePreview() {
        if preview != nil || previewedInputs != nil {
            preview = nil
            previewedInputs = nil
        }
    }

    func setNewKey(_ value: String) {
        newKey = value
        invalidatePreview()
    }

    func setEntryType(_ value: String) {
        entryType = value
        invalidatePreview()
    }

    func setChoice(field: String, sourceKey: String) {
        choices[field] = sourceKey
        invalidatePreview()
    }

    // MARK: 预览 / 合并

    func runPreview() {
        guard !running, !applying, !records.isEmpty else { return }
        let errors = validationErrors
        guard errors.isEmpty else {
            error = errors.joined(separator: "\n")
            return
        }
        let db = BibDbFeature.existingDBPath() ?? ""
        let keys = records.map(\.key)
        let input = currentInputs
        let expect = loadedSha
        running = true
        stage = "正在跑引擎 dry-run 预览…"
        error = ""
        message = ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let outcome = BibDbFeature.previewMerge(db: db, keys: keys,
                                                    newKey: input.newKey,
                                                    entryType: input.entryType,
                                                    fieldSources: input.fieldSources,
                                                    expectDBsha256: expect)
            DispatchQueue.main.async {
                guard let self else { return }
                self.running = false
                self.stage = ""
                guard outcome.error == nil else {
                    self.preview = nil
                    self.previewedInputs = nil
                    self.error = outcome.error ?? "预览失败。"
                    return
                }
                self.preview = outcome
                self.previewedInputs = input
                if !outcome.dbSha256Before.isEmpty { self.loadedSha = outcome.dbSha256Before }
                self.message = "预览完成：记录 \(outcome.recordsBefore) → \(outcome.recordsAfter)"
                    + "；确认无误后点「合并」（会二次确认）。"
            }
        }
    }

    func apply() {
        guard let preview, previewedInputs == currentInputs, !recordsChanged else {
            error = "先把当前选择预览一次（dry-run）再合并：预览是写库的前置条件。"
            return
        }
        let db = BibDbFeature.existingDBPath() ?? ""
        let keys = records.map(\.key)
        let input = currentInputs
        let expect = preview.dbSha256Before.isEmpty ? loadedSha : preview.dbSha256Before
        applying = true
        stage = "正在合并（备份 → 原子写 → 复核）…"
        error = ""
        message = ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let outcome = BibDbFeature.applyMerge(db: db, keys: keys,
                                                  newKey: input.newKey,
                                                  entryType: input.entryType,
                                                  fieldSources: input.fieldSources,
                                                  expectDBsha256: expect)
            DispatchQueue.main.async {
                guard let self else { return }
                self.applying = false
                self.stage = ""
                guard outcome.written, outcome.error == nil else {
                    var text = outcome.error ?? "合并失败（引擎没有返回原因）。"
                    if !outcome.backupPath.isEmpty {
                        text += "\n引擎已备份到：\(outcome.backupPath)"
                        text += "\n若提示「写后复核失败」，库可能已经写了：请用该备份恢复，不要当作没写。"
                    }
                    self.error = text
                    return
                }
                var lines: [String] = []
                lines.append("已合并为 `\(outcome.newKey)`；记录 \(outcome.recordsAfter) 条")
                lines.append("aliases：\(outcome.aliases.isEmpty ? "（无）" : outcome.aliases.joined(separator: "、"))")
                lines.append("备份：\(outcome.backupPath.isEmpty ? "（引擎未返回）" : outcome.backupPath)")
                lines.append("新库 sha：\(outcome.dbSha256After.isEmpty ? "（未返回）" : outcome.dbSha256After)")
                if let verification = outcome.verification {
                    lines.append(verification.summary)
                }
                self.successText = lines.joined(separator: "\n")
                self.succeeded = true
                self.preview = nil
                self.previewedInputs = nil
            }
        }
    }
}
