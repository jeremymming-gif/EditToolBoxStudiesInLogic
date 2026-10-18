import SwiftUI
import AppKit

/// 「入藏」页的界面状态。
final class BibDbImportModel: ObservableObject {

    /// 入藏来源：单篇 `.bib` / 整期目录（含稿件库根）。
    enum Mode: String, CaseIterable, Identifiable, Equatable {
        case single = "单篇 .bib"
        case directory = "整期目录"
        var id: String { rawValue }
    }

    @Published var mode: Mode = .single
    @Published var bibPath: String = ""
    @Published var directoryPath: String = ""
    /// 来源期 / 篇目录名（单篇模式：从路径推断，可手改）
    @Published var issue: String = ""
    @Published var slug: String = ""
    /// 预览结果（dry-run；**未写库**）
    @Published var preview: BibDbFeature.ImportOutcome? = nil
    /// 真导入结果
    @Published var imported: BibDbFeature.ImportOutcome? = nil
    @Published var running = false
    @Published var stage: String = ""
    @Published var error: String = ""
    @Published var notice: String = ""
    /// 「重新生成清单」的结果话术
    @Published var reportMessage: String = ""
    @Published var reportRunning = false

    /// 预览时用的输入签名：输入一改，之前的预览就作废（绝不让编辑照着旧预览点确认）
    private var previewSignature = ""

    var currentPath: String {
        (mode == .single ? bibPath : directoryPath)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var currentSignature: String {
        [mode.rawValue, currentPath,
         issue.trimmingCharacters(in: .whitespacesAndNewlines),
         slug.trimmingCharacters(in: .whitespacesAndNewlines)].joined(separator: "|")
    }

    /// 预览是否对应当前输入（「确认入藏」按钮据此启用）
    var previewMatchesInputs: Bool {
        preview != nil && !previewSignature.isEmpty && previewSignature == currentSignature
    }

    /// 目录模式下按目录名猜一个「期」（只用于界面提示，真正的期名由引擎从目录读）
    var detectedIssueName: String {
        let name = (currentPath as NSString).lastPathComponent
        return name.range(of: #"^\d{4}年第"#, options: .regularExpression) != nil ? name : ""
    }

    /// 路径变了：作废旧预览；单篇模式顺手推断「来源期 / 篇目录名」。
    func pathChanged() {
        invalidatePreview()
        imported = nil
        notice = ""
        if mode == .single {
            let inferred = BibDbFeature.inferIssueAndSlug(from: bibPath)
            if !inferred.issue.isEmpty { issue = inferred.issue }
            slug = inferred.slug
        }
    }

    /// 期/篇被改了（手填或自动推断）：作废旧预览。
    func metadataChanged() {
        invalidatePreview()
        imported = nil
    }

    func invalidatePreview() {
        preview = nil
        previewSignature = ""
        error = ""
        notice = ""
        stage = ""
    }

    /// 记下预览结果与当时的输入签名。
    func setPreview(_ outcome: BibDbFeature.ImportOutcome) {
        preview = outcome
        previewSignature = currentSignature
        imported = nil
        error = outcome.error ?? ""
        notice = ""
    }

    func setImported(_ outcome: BibDbFeature.ImportOutcome) {
        imported = outcome
        error = outcome.error ?? ""
        notice = ""
        reportMessage = ""
    }
}

/// 「入藏」页：把一份成品 `.bib` 或一整期目录导进编辑部引用库。
///
/// 产品行为（用户已定案）：
///   · **先预览 = dry-run**：引擎照常解析/查重/算冲突，但**一个字节都不写**；
///   · 预览满意才「确认入藏」：真正写库（单篇传 `--verified`，按往期成品 bib 入已核实）；
///   · 来源期 / 篇目录名从路径自动推断（`…/2026年第2期(87)/02 Kun_Xing/…`），可手改；
///   · 成功后提示「重新生成清单」——清单是给人看的待裁决文件，库一变就该重算。
struct BibDbImportView: View {
    @ObservedObject var log: LogStore
    @ObservedObject var bibdb: BibDbSettings
    let isActive: Bool

    @StateObject private var model = BibDbImportModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                BibDbPageHeader(
                    title: "引用库 · 入藏",
                    subtitle: """
                    把**往期成品 bib**（或一整期目录）导入编辑部引用库：给了来源期/篇的条目按
                    「往期已出版、人工核对过」入 `verified=true`（AI 结果**不要**走这个入口）。
                    先点「预览（dry-run）」——引擎照常解析/查重/算冲突，但**库一个字节都不写**；
                    看清楚了再点「确认入藏」。
                    """)

                Divider()

                modePicker
                pathRow

                if model.mode == .single {
                    metadataRows
                } else {
                    directoryHint
                }

                targetRow
                actionBar

                if !model.error.isEmpty {
                    BibDbStatusLine(kind: .error, text: model.error)
                }
                if !model.notice.isEmpty {
                    BibDbStatusLine(kind: .warning, text: model.notice)
                }
                if model.running {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(model.stage).font(.caption).foregroundStyle(.secondary)
                    }
                }

                outcomeSection
            }
            .padding(20)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .onChange(of: model.mode) { _ in
            model.invalidatePreview()
            model.imported = nil
        }
        .onChange(of: model.bibPath) { _ in
            if model.mode == .single { model.pathChanged() }
        }
        .onChange(of: model.directoryPath) { _ in
            if model.mode == .directory { model.pathChanged() }
        }
        .onChange(of: model.issue) { _ in model.metadataChanged() }
        .onChange(of: model.slug) { _ in model.metadataChanged() }
    }

    // MARK: - 哪条路进来的

    private var modePicker: some View {
        HStack(spacing: 10) {
            Text("入藏来源")
            Picker("", selection: $model.mode) {
                ForEach(BibDbImportModel.Mode.allCases) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 240)
            .disabled(model.running)
            Spacer()
        }
    }

    @ViewBuilder
    private var pathRow: some View {
        if model.mode == .single {
            LabeledPathRow(
                title: "成品 .bib",
                placeholder: "例如 …/2026年第2期(87)/02 Kun_Xing/reference.bib",
                path: $model.bibPath,
                kind: .file,
                logStore: log
            )
            .disabled(model.running)
        } else {
            LabeledPathRow(
                title: "整期目录",
                placeholder: "例如 …/2026年第2期(87)（也可选稿件库根，导入全部期）",
                path: $model.directoryPath,
                kind: .directory,
                logStore: log
            )
            .disabled(model.running)
        }
    }

    /// 单篇模式：来源期 / 篇目录名（从路径推断，可手改）
    private var metadataRows: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Text("来源期").frame(width: 108, alignment: .trailing)
                TextField("例：2026年第2期(87)（留空 = 不记录来源）", text: $model.issue)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                    .disabled(model.running)
                Text("篇目录名").frame(width: 76, alignment: .trailing)
                TextField("例：02 Kun_Xing", text: $model.slug)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                    .disabled(model.running)
            }
            Text("从路径自动推断（`…/2026年第2期(87)/02 Kun_Xing/…`）；认不出就留空，"
                 + "别硬猜——来源是库里的审计凭据，写错比不写更糟。改完要重新预览。")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .padding(.leading, 118)
        }
    }

    private var directoryHint: some View {
        VStack(alignment: .leading, spacing: 4) {
            if !model.detectedIssueName.isEmpty {
                Text("识别到这是**一期目录**：`\(model.detectedIssueName)`"
                     + "（只导入这一期；期名/篇名由引擎按目录名读取）")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("整期目录模式：选一期目录（如 `2026年第2期(87)`）只导入这一期；"
                     + "选稿件库根则导入其下全部期。哪份 bib 算成品 bib、成品期是否 verified，"
                     + "判据与种子导入**同一份**（引擎 `scan_manuscripts`）。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.leading, 118)
    }

    private var targetRow: some View {
        VStack(alignment: .leading, spacing: 2) {
            BibDbInfoRow(label: "将写入",
                         value: BibDbFeature.dbPath() ?? "（找不到私有笔记仓：请到「引用库设置」页指定）",
                         monospaced: true,
                         color: BibDbFeature.dbPath() == nil ? .red : .secondary,
                         wraps: true)
        }
    }

    private var actionBar: some View {
        HStack(spacing: 12) {
            // 预览/导入都是秒级短操作（读 6.7MB 的库 + 内存解析），不放取消按钮；
            // 运行中两个按钮都置灰，避免同一份输入被重复提交。
            Button("预览（dry-run，不写库）") { preview() }
                .buttonStyle(.borderedProminent)
                .disabled(model.running || model.currentPath.isEmpty)
            Button("确认入藏") { confirm() }
                .disabled(model.running || !model.previewMatchesInputs)
                .help(model.previewMatchesInputs
                      ? "真正写库（单篇按往期成品 bib 入 verified）"
                      : "先预览，且预览之后不要再改输入")
            if !model.previewMatchesInputs && model.preview != nil && !model.running {
                Text("输入已改动：请重新预览")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
            Spacer()
        }
    }

    // MARK: - 结果区

    @ViewBuilder
    private var outcomeSection: some View {
        if let imported = model.imported, imported.error == nil {
            VStack(alignment: .leading, spacing: 8) {
                BibDbStatusLine(kind: .ok, text: "✅ 已入藏到引用库"
                    + (imported.dbExisted ? "" : "（库文件是本次新建的）")
                    + "，用时 \(BibDbUI.seconds(imported.seconds))。")
                outcomeBody(imported)
                HStack(spacing: 10) {
                    Button("重新生成清单") { regenerateReports() }
                        .disabled(model.reportRunning)
                    if model.reportRunning {
                        ProgressView().controlSize(.small)
                        Text("正在生成 CONFLICTS.md / DUPLICATES.md …")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Text("库变了，待裁决清单就该重算（清单只写文件、不改库）。")
                        .font(.caption).foregroundStyle(.tertiary)
                }
                if !model.reportMessage.isEmpty {
                    BibDbStatusLine(kind: .ok, text: model.reportMessage)
                }
            }
            Divider()
        }
        if let preview = model.preview, preview.error == nil {
            VStack(alignment: .leading, spacing: 8) {
                BibDbStatusLine(kind: .warning,
                                text: "预览（dry-run）：**未写库**，库一个字节都没动，用时 "
                                    + BibDbUI.seconds(preview.seconds)
                                    + "。确认无误再点「确认入藏」。")
                outcomeBody(preview)
            }
        }
    }

    @ViewBuilder
    private func outcomeBody(_ outcome: BibDbFeature.ImportOutcome) -> some View {
        if let summary = outcome.summary {
            Text(summary.line).font(.callout).bold().textSelection(.enabled)
            if summary.findings > 0 {
                Text("（bibparse 语法发现 \(summary.findings) 处，未阻断导入）")
                    .font(.caption).foregroundStyle(.orange)
            }
            if !summary.skipped.isEmpty {
                Text("跳过原因："
                     + summary.skipped.map { "\($0.reason)×\($0.count)" }.joined(separator: "、"))
                    .font(.caption).foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            if !summary.warnings.isEmpty {
                ForEach(Array(summary.warnings.prefix(6).enumerated()), id: \.offset) { item in
                    Text("⚠ " + item.element)
                        .font(.caption).foregroundStyle(.orange)
                        .textSelection(.enabled)
                }
            }
        }
        if !outcome.issues.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(outcome.issues) { issue in
                    Text("[\(issue.finished ? "成品期" : "未定稿")] \(issue.issue)："
                         + "\(issue.fileCount) 份 bib"
                         + (issue.summary.map { " —— \($0.line)" } ?? ""))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
        }
        if !outcome.warnings.isEmpty {
            ForEach(Array(outcome.warnings.prefix(6).enumerated()), id: \.offset) { item in
                Text("⚠ " + item.element).font(.caption).foregroundStyle(.orange)
                    .textSelection(.enabled)
            }
        }
        if !outcome.changes.isEmpty {
            Text("逐条变化（\(outcome.changes.count) 条，最多列 60）：")
                .font(.caption).bold()
            VStack(alignment: .leading, spacing: 2) {
                ForEach(outcome.changes.prefix(60)) { change in
                    changeRow(change)
                }
            }
            if outcome.changes.count > 60 {
                Text("……另有 \(outcome.changes.count - 60) 条（完整明细见运行日志 / CLI --json）。")
                    .font(.caption).foregroundStyle(.tertiary)
            }
        }
    }

    private func changeRow(_ change: BibDbFeature.ImportChange) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(change.statusTitle)
                .font(.caption)
                .foregroundStyle(statusColor(change.status))
                .frame(width: 56, alignment: .leading)
            Text(change.key)
                .font(.system(.caption, design: .monospaced))
            Text(change.title)
                .font(.caption)
                .lineLimit(1)
            if !change.author.isEmpty || !change.year.isEmpty {
                Text("（\(change.author)\(change.year.isEmpty ? "" : " " + change.year)）")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            if change.verified {
                Text("已核实").font(.caption2).foregroundStyle(.green)
            }
            if change.status == "conflict" && !change.conflictFields.isEmpty {
                Text("冲突字段：" + change.conflictFields.joined(separator: "、"))
                    .font(.caption2).foregroundStyle(.orange)
            } else if change.status == "merged" && change.sourcesAdded > 0 {
                Text("+\(change.sourcesAdded) 来源").font(.caption2).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "new": return .green
        case "conflict": return .orange
        case "updated": return .blue
        default: return .secondary
        }
    }

    // MARK: - 动作（后台队列跑引擎，日志写运行日志）

    private func preview() {
        guard !model.running else { return }
        let mode = model.mode
        let path = model.currentPath
        let issue = model.issue
        let slug = model.slug
        let logger = Logger(store: log, startedAt: Date())
        model.running = true
        model.stage = "正在预览（dry-run，不写库）…"
        model.error = ""
        model.imported = nil
        model.preview = nil
        logger("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n")
        logger("引用库入藏 · 预览（dry-run：不写库）\n")
        logger("  来源   \(mode.rawValue)：\(path)\n")
        if mode == .single {
            logger("  期/篇  \(issue.isEmpty ? "（未填）" : issue) / "
                   + (slug.isEmpty ? "（未填）" : slug) + "\n")
        }
        logger("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n\n")

        // 强引用 model（它是 @StateObject 持有的 class）：视图结构体不能 weak self，
        // 而这些闭包只活一次子进程的时长，不存在循环引用问题（与 CheckBibView 同一写法）。
        let model = self.model
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome: BibDbFeature.ImportOutcome
            switch mode {
            case .single:
                outcome = BibDbFeature.importBib(bibPath: path, issue: issue, slug: slug, dryRun: true)
            case .directory:
                outcome = BibDbFeature.importDirectory(root: path, dryRun: true)
            }
            DispatchQueue.main.async {
                model.running = false
                model.stage = ""
                if let error = outcome.error {
                    model.error = error
                    logger("❌ 预览失败：\(error)\n")
                    return
                }
                model.setPreview(outcome)
                Self.logOutcome(outcome, dryRun: true, log: logger)
                if let summary = outcome.summary, summary.entries == 0 {
                    model.notice = "这次没有解析出任何条目（检查文件是否真是 .bib、"
                        + "或这一期目录里有没有成品 bib）。"
                }
            }
        }
    }

    private func confirm() {
        guard !model.running, model.previewMatchesInputs else { return }
        let mode = model.mode
        let path = model.currentPath
        let issue = model.issue
        let slug = model.slug
        let logger = Logger(store: log, startedAt: Date())
        model.running = true
        model.stage = "正在写入引用库…"
        model.error = ""
        logger("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n")
        logger("引用库入藏 · 确认导入（真正写库）\n")
        logger("  来源   \(mode.rawValue)：\(path)\n")
        logger("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n\n")

        let model = self.model
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome: BibDbFeature.ImportOutcome
            switch mode {
            case .single:
                outcome = BibDbFeature.importBib(bibPath: path, issue: issue, slug: slug, dryRun: false)
            case .directory:
                outcome = BibDbFeature.importDirectory(root: path, dryRun: false)
            }
            DispatchQueue.main.async {
                model.running = false
                model.stage = ""
                if let error = outcome.error {
                    model.error = error
                    logger("❌ 入藏失败：\(error)\n")
                    return
                }
                model.setImported(outcome)
                Self.logOutcome(outcome, dryRun: false, log: logger)
            }
        }
    }

    private func regenerateReports() {
        guard !model.reportRunning else { return }
        let logger = Logger(store: log, startedAt: Date())
        model.reportRunning = true
        model.reportMessage = ""
        let model = self.model
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.generateReports()
            DispatchQueue.main.async {
                model.reportRunning = false
                if let error = outcome.error {
                    model.reportMessage = "⚠ 生成清单失败：" + error
                    logger("❌ 重新生成待裁决清单失败：\(error)\n")
                    return
                }
                let text = "✅ 已重新生成清单（更新 \(outcome.written) 个文件；"
                    + "字段冲突 \(outcome.conflictsRecords) 条 / \(outcome.conflictsTotal) 处差异，"
                    + "同作品候选对 \(outcome.pairs) 对）。"
                model.reportMessage = text
                logger(text + "\n")
                for file in outcome.reports {
                    logger("  \(file.exists ? "已写入" : "未生成")：\(file.path)\n")
                }
            }
        }
    }

    /// 把一次预览/导入的结果写进运行日志（编辑要能事后对账）。
    private static func logOutcome(_ outcome: BibDbFeature.ImportOutcome,
                                   dryRun: Bool, log: Logger) {
        if let summary = outcome.summary {
            log((dryRun ? "（预览，未写库）" : "✅ 已写库：") + summary.line + "\n")
            if !summary.skipped.isEmpty {
                log("  跳过原因："
                    + summary.skipped.map { "\($0.reason)×\($0.count)" }.joined(separator: "、")
                    + "\n")
            }
            for warning in summary.warnings { log("  ⚠ \(warning)\n") }
        }
        for issue in outcome.issues {
            log("  [\(issue.finished ? "成品期" : "未定稿")] \(issue.issue)：\(issue.fileCount) 份 bib"
                + (issue.summary.map { " —— \($0.line)" } ?? "") + "\n")
        }
        for warning in outcome.warnings { log("  ⚠ \(warning)\n") }
        if outcome.changes.isEmpty {
            log("  逐条变化：无（库与输入完全一致）\n")
        } else {
            log("  逐条变化 \(outcome.changes.count) 条：\n")
            for change in outcome.changes.prefix(200) {
                log("    · \(change.statusTitle) `\(change.key)`：\(change.title)"
                    + (change.conflictFields.isEmpty
                       ? "" : "（冲突字段：\(change.conflictFields.joined(separator: "、"))）")
                    + "\n")
            }
        }
        log("库：\(outcome.dbPath)"
            + (outcome.dbExisted ? "" : "（本次新建）") + "\n")
        if dryRun {
            log("（预览结束：库文件一个字节都没写。确认无误再点「确认入藏」。）\n")
        } else {
            log("（入藏完成：建议到「待裁决」页重新生成清单。）\n")
        }
        log("\n")
    }
}
