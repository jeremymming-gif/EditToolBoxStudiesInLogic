import SwiftUI

/// 条目编辑器（B-188 §4.3）：**共享 sheet**——查库详情、待处理页、库概况 warning 行都用它。
///
/// 为什么视图里看不到字段规范化/解析/key 查重：这些判据全在引擎 `bibdb.py edit` 里
/// （本仓红线「一个引擎，两个外壳」）。本 sheet 只做三件事：
///   ① 表单 ↔ `EditorDraft` 的绑定；② 把草稿交给 Wave1 API（后台队列）；③ 展示引擎结论。
/// 所以「预览」= `edit --dry-run`（零写入），「写入」= `edit --apply --backup --expect-db-sha256`。
/// 线程：Wave1 写库/预览 API 都是同步子进程调用，视图**只能**后台队列调、回主线程回填（案例 126）。
struct BibDbEditorSheet: View {
    @ObservedObject var log: LogStore
    @ObservedObject var bibdb: BibDbSettings
    /// 库内 key（也认 alias；加载后以 canonical key 展示/写库）
    let recordKey: String
    /// 写库成功后回调（调用方刷新缓存/局部失效；sheet 自己关闭）
    var onWritten: () -> Void = {}

    @Environment(\.dismiss) private var dismiss
    @StateObject private var model = BibDbEditorSheetModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider()

            if model.loading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在读库内条目（query --bibtex）…")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            } else if model.draft == nil {
                BibDbStatusLine(kind: .error,
                                text: model.error.isEmpty ? "条目没加载出来（引擎没有返回草稿）。"
                                                          : model.error)
                Text("库文件：\(dbPathText)")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                Spacer(minLength: 0)
            } else {
                editorBody
            }

            Divider()
            footer
        }
        .padding(18)
        .frame(width: 820, height: 760, alignment: .leading)
        .onAppear { model.load(key: recordKey) }
        // 二次确认挂在**本 sheet 自己**上（批73 真点缺陷 P1：主 view 上的 alert 会被 sheet 抑制）。
        .alert("确认写入引用库？", isPresented: $model.confirmApply) {
            Button("取消", role: .cancel) {}
            Button("确认写入", role: .destructive) { model.apply() }
        } message: {
            Text("将按预览的字段差异写库；写前引擎自动备份，并用库 sha 校验防并发改动。\n"
                 + "备份目录：\(BibDbFeature.backupDirectory)")
        }
        // 写库成功后：先让成功摘要可见一小会儿（详情同时进运行日志），再按契约 dismiss。
        .onChange(of: model.succeeded) { done in
            guard done else { return }
            Logger(store: log)(model.successText + "\n")
            onWritten()
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.2) { dismiss() }
        }
    }

    // MARK: - 顶部

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("编辑引用库条目").font(.title3).bold()
                if model.draft != nil {
                    Text((model.draft?.recordKey ?? ""))
                        .font(.system(.callout, design: .monospaced))
                        .textSelection(.enabled)
                }
                Spacer()
                Button("关闭") { dismiss() }
                    .disabled(model.applying)
            }
            Text(("这是**共享编辑器**：查库详情、待处理页的行都用它。预览 = 引擎 dry-run（不写库）；"
                  + "写入前自动备份 + 库 sha 校验。字段规范化/key 查重/必填字段全由引擎判定，"
                  + "本窗口只显示引擎结论。").plainMarkdown)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 主体

    private var editorBody: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                metaBlock
                keyBlock
                fieldForm
                advancedSection
                rawSection
                previewSection
                statusBlock
            }
            .padding(.trailing, 6)
        }
    }

    /// 来源期 / 核实标记 / aliases：这些是「能不能信这条」的审计线索，编辑时也要看得到。
    private var metaBlock: some View {
        VStack(alignment: .leading, spacing: 3) {
            if let meta = model.meta {
                HStack(spacing: 10) {
                    Text("来源期：\(meta.issues.isEmpty ? "（无来源记录）" : meta.issues.joined(separator: "、"))")
                    Text(meta.verified ? "已核实" : "未核实")
                        .foregroundStyle(meta.verified ? .green : .orange)
                }
                .font(.caption)
                .textSelection(.enabled)
                if !meta.sourcesText.isEmpty {
                    Text("来源：\(meta.sourcesText)")
                        .font(.caption2).foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .lineLimit(2)
                }
            } else {
                Text("来源/核实信息未读到（不影响编辑；写库判据仍由引擎给出）。")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            Text("aliases：\((model.draft?.aliases ?? []).isEmpty ? "（无）" : (model.draft?.aliases ?? []).joined(separator: "、"))")
                .font(.caption2).foregroundStyle(.secondary)
                .textSelection(.enabled)
        }
    }

    /// entry_type + 改 key。entry_type 用可编辑文本而不是本地类型表：
    /// CONTRACT §4.5 明确「类型合法性交引擎 dry-run」，Wave1 没有暴露引擎类型表，
    /// 这里**不自己维护一份**（契约缺口见报告「未决」）。
    private var keyBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Text("entry_type")
                    .foregroundStyle(.secondary)
                    .frame(width: 96, alignment: .trailing)
                TextField("article / book / incollection …", text: entryTypeBinding)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                    .frame(width: 240)
                Text("类型合法性由引擎 dry-run 校验（预览时给结论）")
                    .font(.caption2).foregroundStyle(.tertiary)
                Spacer()
            }
            HStack(spacing: 10) {
                Text("key")
                    .foregroundStyle(.secondary)
                    .frame(width: 96, alignment: .trailing)
                Text(model.draft?.recordKey ?? "")
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
                Toggle("改 key", isOn: newKeyEnabledBinding)
                    .toggleStyle(.checkbox)
                Spacer()
            }
            if model.draft?.newKeyEnabled == true {
                HStack(spacing: 10) {
                    Text("新 key")
                        .foregroundStyle(.secondary)
                        .frame(width: 96, alignment: .trailing)
                    TextField("作者姓+年份，如 Tindale2015", text: newKeyBinding)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(.body, design: .monospaced))
                        .frame(width: 280)
                    Spacer()
                }
                Text("旧 key 会自动写进记录 `aliases`（之后用旧 key 查库仍能命中）；"
                     + "key/alias 全局查重与撞车拒绝由引擎 dry-run 给出。")
                    .font(.caption2).foregroundStyle(.secondary)
                    .padding(.leading, 106)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 10) {
                Text("备注")
                    .foregroundStyle(.secondary)
                    .frame(width: 96, alignment: .trailing)
                TextField("写进库的 edits[] 留痕（可空）", text: noteBinding)
                    .textFieldStyle(.roundedBorder)
                Spacer()
            }
        }
    }

    /// 常用字段：固定顺序全列出来（缺的字段直接填就是「补字段」）。
    private var fieldForm: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("字段").font(.callout).bold()
            ForEach(BibDbEditorSheetModel.commonFields, id: \.self) { name in
                fieldRow(name)
            }
        }
    }

    private func fieldRow(_ name: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(name)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .frame(width: 96, alignment: .trailing)
            TextField("（空 = 引擎会删掉该字段）", text: fieldBinding(name))
                .textFieldStyle(.roundedBorder)
                .font(.system(.body, design: .monospaced))
        }
    }

    /// 高级字段：其余全部字段（可增删）。字段名格式最终仍由 `validateEditorDraft` + 引擎判。
    private var advancedSection: some View {
        DisclosureGroup(isExpanded: $model.showAdvanced) {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(model.advancedFieldNames, id: \.self) { name in
                    HStack(alignment: .top, spacing: 10) {
                        Text(name)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .frame(width: 96, alignment: .trailing)
                        TextField("", text: fieldBinding(name))
                            .textFieldStyle(.roundedBorder)
                            .font(.system(.body, design: .monospaced))
                        Button {
                            model.deleteField(name)
                        } label: {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.borderless)
                        .help("删除字段（写入时整表替换；空值同样会被引擎删掉）")
                    }
                }
                HStack(spacing: 10) {
                    Text("新字段")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .frame(width: 96, alignment: .trailing)
                    TextField("字段名（小写字母开头）", text: $model.newFieldName)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(.body, design: .monospaced))
                        .frame(width: 220)
                    Button("添加字段") { model.addField() }
                        .controlSize(.small)
                    if !model.newFieldError.isEmpty {
                        Text(model.newFieldError)
                            .font(.caption2).foregroundStyle(.red)
                    }
                    Spacer()
                }
            }
            .padding(.top, 6)
        } label: {
            Text("高级字段（其余 \(model.advancedFieldNames.count) 个；可增删）")
                .font(.callout).bold()
        }
    }

    /// 原始 BibTeX：解析校验**必须**回引擎（`edit --bib tmp --dry-run`），Swift 不写解析器。
    private var rawSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Text("原始 BibTeX").font(.callout).bold()
                Text("改完点「解析校验回表单」：引擎解析成功才回填，失败只红字报错，不改表单。")
                    .font(.caption2).foregroundStyle(.tertiary)
                Spacer()
                Button("解析校验回表单") { model.parseRaw() }
                    .controlSize(.small)
                    .disabled(model.running || model.applying)
            }
            TextEditor(text: $model.rawText)
                .font(.system(.caption, design: .monospaced))
                .frame(minHeight: 110, maxHeight: 170)
                .padding(4)
                .background(Color(nsColor: .textBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.secondary.opacity(0.35)))
            if !model.rawError.isEmpty {
                BibDbStatusLine(kind: .error, text: model.rawError)
            }
        }
    }

    /// 预览结果：字段级 old→new、key/类型变化、aliases、必填缺字段、库 sha。
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
                if preview.entryTypeBefore != preview.entryTypeAfter {
                    Text("entry_type：\(preview.entryTypeBefore) → \(preview.entryTypeAfter)")
                        .font(.caption)
                }
                if !preview.requestedNewKey.isEmpty || preview.newKey != preview.key {
                    Text("key：\(preview.key) → \(preview.newKey.isEmpty ? "（不变）" : preview.newKey)")
                        .font(.caption)
                }
                if preview.fieldChanges.isEmpty {
                    Text("没有字段变化（这样的写入不会产生差异）。")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("字段变化 \(preview.fieldChanges.count) 项：").font(.caption).bold()
                    ForEach(preview.fieldChanges) { change in
                        HStack(alignment: .top, spacing: 6) {
                            Text(change.name)
                                .font(.system(.caption, design: .monospaced))
                                .frame(width: 100, alignment: .leading)
                            Text(change.removal ? "（删除该字段）" : "\(short(change.old)) → \(short(change.new))")
                                .font(.caption)
                                .foregroundStyle(change.removal ? .red : .primary)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 0)
                        }
                    }
                }
                if !preview.aliasesAdded.isEmpty {
                    Text("别名将追加：\(preview.aliasesAdded.joined(separator: "、"))")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                if !preview.missingBefore.isEmpty || !preview.missingAfter.isEmpty {
                    Text("必填缺字段：\(preview.missingBefore.isEmpty ? "无" : preview.missingBefore.joined(separator: "、"))"
                         + " → \(preview.missingAfter.isEmpty ? "无" : preview.missingAfter.joined(separator: "、"))")
                        .font(.caption2)
                        .foregroundStyle(preview.missingAfter.count < preview.missingBefore.count ? .green : .secondary)
                }
                if !preview.conflictsBlocking.isEmpty {
                    Text("该记录还有待裁决冲突：\(preview.conflictsBlocking.joined(separator: "、"))"
                         + "（引擎会拒绝写入；先到「待处理」页裁决）")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text("库 sha：\(shaShort(preview.dbSha256Before)) → \(shaShort(preview.dbSha256AfterEstimate))（预计）")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
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
        let validation = model.validation
        if !validation.errors.isEmpty {
            BibDbStatusLine(kind: .error, text: validation.errors.joined(separator: "\n"))
        }
        if !validation.warnings.isEmpty {
            BibDbStatusLine(kind: .warning, text: validation.warnings.joined(separator: "\n"))
        }
        if model.draft != nil && model.preview != nil && model.previewedDraft != model.draft {
            BibDbStatusLine(kind: .warning,
                            text: "草稿在预览后改过：上次预览已作废，请重新预览再写入。")
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
            Spacer()
            if model.running || model.applying {
                ProgressView().controlSize(.small)
                Text(model.stage).font(.caption).foregroundStyle(.secondary)
            }
            Button("预览（dry-run，不写库）") { model.runPreview() }
                .disabled(model.running || model.applying || model.draft == nil
                          || model.validation.isBlocked)
            Button("写入…") { model.confirmApply = true }
                .buttonStyle(.borderedProminent)
                .disabled(!model.canWrite)
                .help("先预览；预览后改动草稿会作废预览，必须重新预览")
        }
    }

    // MARK: - 绑定

    private var entryTypeBinding: Binding<String> {
        Binding(get: { model.draft?.entryType ?? "" },
                set: { model.setEntryType($0) })
    }

    private var newKeyEnabledBinding: Binding<Bool> {
        Binding(get: { model.draft?.newKeyEnabled ?? false },
                set: { model.setNewKeyEnabled($0) })
    }

    private var newKeyBinding: Binding<String> {
        Binding(get: { model.draft?.newKey ?? "" },
                set: { model.setNewKey($0) })
    }

    private var noteBinding: Binding<String> {
        Binding(get: { model.draft?.note ?? "" },
                set: { model.setNote($0) })
    }

    private func fieldBinding(_ name: String) -> Binding<String> {
        Binding(get: { model.draft?.fields[name] ?? "" },
                set: { model.setField(name, $0) })
    }

    // MARK: - 文案小工具

    private var dbPathText: String {
        let path = BibDbFeature.existingDBPath() ?? ""
        return path.isEmpty ? "（引用库文件不存在：写库前先到「入藏」页导入或到「引用库设置」页指定笔记仓）"
                            : path
    }

    private func shaShort(_ sha: String) -> String {
        sha.isEmpty ? "（未返回）" : String(sha.prefix(12)) + "…"
    }

    private func short(_ text: String) -> String {
        let flat = text.replacingOccurrences(of: "\n", with: " ⏎ ")
        return flat.count > 90 ? String(flat.prefix(90)) + "…" : (flat.isEmpty ? "（空）" : flat)
    }
}

// MARK: - 编辑器本地状态

/// 为什么模型放在本文件且 `private`：sheet 的冻结签名只有 4 个参数，本地状态必须由 sheet 自己
/// 持有；`private` 避免与兄弟分片新增的辅助类型撞名。
private final class BibDbEditorSheetModel: ObservableObject {
    @Published var loading = false
    /// 预览/解析共用的忙标志（写库另有 applying，按钮各自置灰）
    @Published var running = false
    @Published var applying = false
    @Published var stage = ""
    @Published var error = ""
    @Published var message = ""
    @Published var draft: BibDbFeature.EditorDraft?
    @Published var meta: BibDbFeature.RecordInfo?
    @Published var rawText = ""
    @Published var rawError = ""
    @Published var preview: BibDbFeature.EditPreviewOutcome?
    /// 预览时的**草稿快照**：与当前 draft 不等即视为作废（契约：draft 一改前次预览作废）。
    @Published var previewedDraft: BibDbFeature.EditorDraft?
    @Published var confirmApply = false
    @Published var successText = ""
    @Published var succeeded = false
    @Published var loadedSha = ""
    @Published var showAdvanced = false
    @Published var newFieldName = ""
    @Published var newFieldError = ""
    @Published var loadSeconds: Double = 0

    /// 常用字段顺序（只是**表单排版**，不是引擎类型/必填判据）。
    static let commonFields = ["author", "title", "year", "journal", "booktitle",
                               "publisher", "address", "volume", "number", "pages",
                               "doi", "url", "note"]

    /// 世代号：连续加载时只认最后一次（旧结果回来不许覆盖新结果）。
    private var generation = 0

    var validation: BibDbFeature.EditorValidation {
        guard let draft else { return BibDbFeature.EditorValidation() }
        return BibDbFeature.validateEditorDraft(draft)
    }

    var canWrite: Bool {
        guard preview != nil, let draft, previewedDraft == draft else { return false }
        return !validation.isBlocked && !running && !applying && !succeeded
    }

    var advancedFieldNames: [String] {
        guard let draft else { return [] }
        let common = Set(Self.commonFields)
        return draft.sortedFieldNames.filter { !common.contains($0) }
    }

    // MARK: 加载

    func load(key: String) {
        generation += 1
        let gen = generation
        loading = true
        error = ""
        message = ""
        rawError = ""
        preview = nil
        previewedDraft = nil
        successText = ""
        succeeded = false
        let db = BibDbFeature.existingDBPath() ?? ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let outcome = BibDbFeature.loadEditorDraft(db: db, key: key)
            // 来源期/核实标记不在 Wave1 草稿里（CONTRACT 未给），这里用只读 query 补展示；
            // 读不到不阻断编辑（判据仍以引擎写库结果为准）。
            let metaOutcome = BibDbFeature.query(mode: .key, text: key)
            let sha = db.isEmpty ? "" : BibDbFeature.dbManifest(db: db).sha256
            DispatchQueue.main.async {
                guard let self, gen == self.generation else { return }
                self.loading = false
                self.loadSeconds = outcome.seconds
                self.loadedSha = sha
                guard let loaded = outcome.draft else {
                    self.error = outcome.error ?? "加载失败（引擎没有返回草稿）。"
                    return
                }
                self.draft = loaded
                self.rawText = loaded.rawBib
                self.meta = metaOutcome.hits.first?.record
            }
        }
    }

    // MARK: 草稿编辑（任何改动都作废上次预览）

    private func invalidatePreview() {
        if preview != nil || previewedDraft != nil { preview = nil; previewedDraft = nil }
        if succeeded { succeeded = false }
        if !message.isEmpty && message.hasPrefix("预览") { message = "" }
    }

    func setField(_ name: String, _ value: String) {
        guard var draft else { return }
        draft.fields[name] = value
        self.draft = draft
        invalidatePreview()
    }

    func setEntryType(_ value: String) {
        guard var draft else { return }
        draft.entryType = value
        self.draft = draft
        invalidatePreview()
    }

    func setNewKeyEnabled(_ enabled: Bool) {
        guard var draft else { return }
        draft.newKeyEnabled = enabled
        if !enabled { draft.newKey = "" }
        self.draft = draft
        invalidatePreview()
    }

    func setNewKey(_ value: String) {
        guard var draft else { return }
        draft.newKey = value
        self.draft = draft
        invalidatePreview()
    }

    func setNote(_ value: String) {
        guard var draft else { return }
        draft.note = value
        self.draft = draft
        invalidatePreview()
    }

    func deleteField(_ name: String) {
        guard var draft else { return }
        draft.fields.removeValue(forKey: name)
        self.draft = draft
        invalidatePreview()
    }

    func addField() {
        guard var draft else { return }
        let name = newFieldName.trimmingCharacters(in: .whitespacesAndNewlines)
        newFieldError = ""
        guard !name.isEmpty else { newFieldError = "先填字段名。"; return }
        guard name.range(of: #"^[a-z][a-z0-9_-]*$"#, options: .regularExpression) != nil else {
            newFieldError = "字段名只允许小写字母开头的字母/数字/下划线/连字符。"
            return
        }
        guard draft.fields[name] == nil else {
            newFieldError = "字段 `\(name)` 已存在（直接改值即可）。"
            return
        }
        draft.fields[name] = ""
        self.draft = draft
        newFieldName = ""
        showAdvanced = true
        invalidatePreview()
    }

    // MARK: 原始 BibTeX 解析校验（引擎解析，Swift 不写解析器）

    func parseRaw() {
        guard let draft else { return }
        guard !running else { return }
        let db = BibDbFeature.existingDBPath() ?? ""
        let raw = rawText
        let key = draft.recordKey
        rawError = ""
        error = ""
        running = true
        stage = "正在让引擎解析 BibTeX…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = BibDbFeature.parseRawBib(db: db, raw: raw, key: key)
            DispatchQueue.main.async {
                guard let self else { return }
                self.running = false
                self.stage = ""
                guard var parsed = result.draft else {
                    self.rawError = result.error ?? "解析失败（引擎没有返回原因）。"
                    return
                }
                // 解析只负责「bib → 字段/类型」；本地这几个开关不是 bib 内容，保留用户选择。
                parsed.newKeyEnabled = draft.newKeyEnabled
                parsed.newKey = draft.newKey
                parsed.note = draft.note
                self.draft = parsed
                self.rawText = parsed.rawBib
                self.invalidatePreview()
                self.message = "解析通过：已按引擎结果回填表单（entry_type \(parsed.entryType)，"
                    + "字段 \(parsed.fields.count) 个）。"
            }
        }
    }

    // MARK: 预览 / 写入

    func runPreview() {
        guard let draft, !running, !applying else { return }
        let validation = BibDbFeature.validateEditorDraft(draft)
        guard !validation.isBlocked else {
            error = validation.errors.joined(separator: "\n")
            return
        }
        let db = BibDbFeature.existingDBPath() ?? ""
        let expect = loadedSha
        running = true
        stage = "正在跑引擎 dry-run 预览…"
        error = ""
        message = ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let outcome = BibDbFeature.previewEdit(db: db, draft: draft,
                                                   expectDBsha256: expect)
            DispatchQueue.main.async {
                guard let self else { return }
                self.running = false
                self.stage = ""
                guard outcome.error == nil else {
                    self.preview = nil
                    self.previewedDraft = nil
                    self.error = outcome.error ?? "预览失败。"
                    return
                }
                self.preview = outcome
                self.previewedDraft = draft
                if !outcome.dbSha256Before.isEmpty { self.loadedSha = outcome.dbSha256Before }
                let changes = outcome.fieldChanges.count
                self.message = "预览完成：字段变化 \(changes) 项"
                    + (outcome.aliasesAdded.isEmpty ? "" : "，别名 +\(outcome.aliasesAdded.count)")
                    + "；确认无误后点「写入」（会二次确认）。"
            }
        }
    }

    func apply() {
        guard let draft, let preview, previewedDraft == draft, !applying else {
            error = "先预览（dry-run）再写入：预览是写库的前置条件。"
            return
        }
        let db = BibDbFeature.existingDBPath() ?? ""
        let expect = preview.dbSha256Before.isEmpty ? loadedSha : preview.dbSha256Before
        applying = true
        stage = "正在写库（备份 → 原子写 → 复核）…"
        error = ""
        message = ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let outcome = BibDbFeature.applyEdit(db: db, draft: draft,
                                                 expectDBsha256: expect)
            DispatchQueue.main.async {
                guard let self else { return }
                self.applying = false
                self.stage = ""
                guard outcome.written, outcome.error == nil else {
                    var text = outcome.error ?? "写库失败（引擎没有返回原因）。"
                    if !outcome.backupPath.isEmpty {
                        text += "\n引擎已备份到：\(outcome.backupPath)"
                        text += "\n若提示「写后复核失败」，库可能已经写了：请用该备份恢复，不要当作没写。"
                    }
                    self.error = text
                    return
                }
                var lines: [String] = []
                let renamed = !outcome.newKey.isEmpty && outcome.newKey != outcome.key
                lines.append("已写入引用库：`\(outcome.key)`" + (renamed ? " → `\(outcome.newKey)`" : ""))
                lines.append("备份：\(outcome.backupPath.isEmpty ? "（引擎未返回）" : outcome.backupPath)")
                lines.append("新库 sha：\(outcome.dbSha256After.isEmpty ? "（未返回）" : outcome.dbSha256After)")
                if let verification = outcome.verification {
                    lines.append(verification.summary)
                }
                self.successText = lines.joined(separator: "\n")
                self.succeeded = true
                self.preview = nil
                self.previewedDraft = nil
            }
        }
    }
}
