import SwiftUI
import AppKit

/// 「目录摘要推送」页的界面状态（本机 SwiftUI 宏不可用，状态一律由 ObservableObject 承载）。
final class DirPushModel: ObservableObject {
    @Published var issuePath = ""
    @Published var outRoot = DirPushFeature.rememberedOutRoot()
    @Published var withEnToc = false
    @Published var annualMode = false
    @Published var annualYear = ""
    @Published var annualRows: [DirPushFeature.AnnualRow] = []

    @Published var preview: DirPushFeature.DirPushPreview?
    @Published var annualPreview: DirPushFeature.DirPushPreview?
    @Published var previewError = ""
    @Published var annualError = ""
    @Published var previewLoading = false
    @Published var annualLoading = false

    @Published var running = false
    @Published var resultOutputs: [String] = []
    @Published var statusText = ""
    @Published var statusIsError = false

    /// 预览/年度预览的代次：慢请求回来时路径已经变了 → 丢弃（照「生成目录」页的写法）。
    var previewToken = 0
    var annualToken = 0
    /// 上一次自动推导的年份（用户手改过年份就不覆盖）。
    var lastAutoYear = ""
    /// U3：本次生成的取消句柄。
    var cancel: RunCancel?
}

/// 「目录摘要推送」页（B-225 Phase B）：当期文件夹 → 引擎预览 → 输出根 → 年度汇总 → 生成 docx。
///
/// 与其它页同一分工：**这一页只拼参数/显示结果**，期头解析、摘要清洗、年度拼接、
/// 缺失项与体例判定全部在 `Engine/dirpush.py`（`--json` 契约见 ENGINE_API）。
/// 输入期目录只读；输出只写用户选的输出根（默认 `~/Documents/sysu/编辑部/目录摘要推送/`）。
struct DirPushView: View {

    @ObservedObject var log: LogStore
    @StateObject private var model = DirPushModel()

    private var helpText: String {
        """
        使用说明：选一期的目录（该目录里应已有「000 Table of Contents」，
        也就是已在本机生成过中英文目录），会自动向引擎要一份预览：
        期头 3 行、专栏/主持人、目录条目数、摘要取到率、体例与缺项。

        输出根默认 ~/Documents/sysu/编辑部/目录摘要推送/，可以改成任意目录；
        生成成功后记住这里填的值。若输出根在代码仓（~/Documents/GitHub/）里，
        生成前会先弹确认（会留下未跟踪的 docx 文件）。

        只交付 .docx（PDF 请用 Word 自行导出/打印）；同名文件直接覆盖、不留备份；
        输入期目录只读，不会改动任何稿件文件。
        勾「同时生成年度汇总文案」会按期拼接当年的目录（只有目录，不含摘要），
        每期是否含英文目录按该期生成时记下的体例还原；缺期只警告、不可勾选。
        """
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text(helpText.plainMarkdown)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)

                issuePathRow

                if !model.previewError.isEmpty {
                    inlineError(model.previewError)
                }
                if let preview = model.preview {
                    previewSection(preview)
                }

                annualSection
                outRootSection
                generateRow

                if model.running {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("正在生成，请查看下方运行日志…")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                if !model.statusText.isEmpty {
                    statusBanner
                }
                if !model.resultOutputs.isEmpty {
                    resultSection
                }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        // 整页也能接拖拽（拖到输入框以外的空白处同样有效）；输入框自己那条见 DroppablePathField。
        .pathDropTarget(path: $model.issuePath, kind: .directory, label: "当期文件夹", log: nil) {
            schedulePreview()
        }
        .onChange(of: model.issuePath) { _, _ in schedulePreview() }
        .onChange(of: model.annualMode) { _, _ in annualModeChanged() }
        .onChange(of: model.annualYear) { _, _ in
            if model.annualMode { scheduleAnnualPreview() }
        }
    }

    // MARK: - 当期文件夹 / 预览

    private var issuePathRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("当期文件夹").font(.headline)
            HStack(spacing: 8) {
                DroppablePathField(text: $model.issuePath,
                                   placeholder: "输入期目录或把文件夹拖进来，例如 …/2026年第5期(90)",
                                   disabled: model.running) { url in
                    PathDrop.receive(url, kind: .directory, label: "当期文件夹", log: nil,
                                     setPath: { model.issuePath = $0 },
                                     onAccept: { schedulePreview() })
                }
                Button("浏览…") { browseIssue() }
                    .disabled(model.running)
                Button("预览") { reloadPreview() }
                    .disabled(model.running || model.previewLoading
                              || model.issuePath.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if model.previewLoading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在向引擎要预览（--json --dry-run）…")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder
    private func previewSection(_ preview: DirPushFeature.DirPushPreview) -> some View {
        Divider()
        VStack(alignment: .leading, spacing: 8) {
            Text("预览").font(.headline)

            // 期头 3 行：原样显示（引擎给出，界面不重排）
            VStack(alignment: .leading, spacing: 2) {
                ForEach(preview.headerLines, id: \.self) { line in
                    Text(line)
                        .font(.system(size: 14, weight: .semibold))
                        .textSelection(.enabled)
                }
            }

            if let column = preview.issue?.column {
                HStack(spacing: 14) {
                    Text("\(column.label)：\(column.name)").font(.callout)
                    if !column.host.isEmpty {
                        Text("主持人：\(column.host)")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                }
            }

            HStack(spacing: 18) {
                countChip("篇数", "\(preview.articlesCount)")
                countChip("目录条目", preview.enEntriesCount > 0
                          ? "\(preview.zhEntriesCount) 中 + \(preview.enEntriesCount) 英"
                          : "\(preview.zhEntriesCount)")
                countChip("摘要取到率", DirPushFeature.abstractsRate(preview))
                Spacer()
            }

            Toggle("包含英文目录（中英文对照）", isOn: $model.withEnToc)
                .toggleStyle(.checkbox)
                .disabled(model.running)
            Text("体例默认取引擎预览值（已有目录摘要设置就沿用），可改；勾选后当期按中英文对照生成。")
                .font(.caption).foregroundStyle(.secondary)

            if !preview.warnings.isEmpty || !preview.missing.isEmpty {
                warningsBlock(preview)
            }
        }
    }

    @ViewBuilder
    private func countChip(_ title: String, _ value: String) -> some View {
        HStack(spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.callout).bold()
        }
        .padding(.horizontal, 8).padding(.vertical, 4)
        .background(RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.12)))
    }

    @ViewBuilder
    private func warningsBlock(_ preview: DirPushFeature.DirPushPreview) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("warnings / 缺项（\(preview.warnings.count + preview.missing.count) 条）")
                .font(.caption).bold().foregroundStyle(.orange)
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(preview.warnings, id: \.self) { line in
                        Text("• " + line)
                    }
                    ForEach(preview.missing, id: \.self) { item in
                        Text("• 缺项：" + item)
                    }
                }
                .font(.caption)
                .foregroundStyle(.orange)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 120)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.orange.opacity(0.10)))
    }

    @ViewBuilder
    private func inlineError(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
            Text(message).font(.callout).foregroundStyle(.red)
                .fixedSize(horizontal: false, vertical: true)
            Spacer()
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.red.opacity(0.08)))
    }

    // MARK: - 年度汇总

    @ViewBuilder
    private var annualSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("同时生成年度汇总文案（只含目录，不含摘要）", isOn: $model.annualMode)
                .toggleStyle(.checkbox)
                .disabled(model.running)

            if model.annualMode {
                HStack(spacing: 10) {
                    Text("年份").font(.callout)
                    TextField("如 2025", text: $model.annualYear)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 90)
                        .disabled(model.running)
                    if model.annualLoading {
                        ProgressView().controlSize(.small)
                    }
                    Text("自动取预览里的期年份，可改；改完自动重新拉年度清单。")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                }

                if !model.annualError.isEmpty {
                    Text("年度清单获取失败：\(model.annualError)")
                        .font(.caption).foregroundStyle(.red)
                }

                if !model.annualRows.isEmpty {
                    HStack(spacing: 8) {
                        Text(DirPushFeature.annualSummaryText(
                            from: model.annualPreview,
                            selected: DirPushFeature.selectedIssues(from: model.annualRows).count))
                            .font(.callout).bold()
                        Button("全选") {
                            for i in model.annualRows.indices { model.annualRows[i].selected = true }
                        }
                        .controlSize(.small).disabled(model.running)
                        Button("全不选") {
                            for i in model.annualRows.indices { model.annualRows[i].selected = false }
                        }
                        .controlSize(.small).disabled(model.running)
                        Spacer()
                    }
                    ScrollView {
                        VStack(alignment: .leading, spacing: 3) {
                            ForEach($model.annualRows) { $row in
                                HStack(spacing: 8) {
                                    Toggle("第 \(row.number) 期", isOn: $row.selected)
                                        .toggleStyle(.checkbox)
                                        .disabled(model.running || !row.isFound)
                                    Text(row.styleText)
                                        .font(.caption).foregroundStyle(.secondary)
                                    if !row.isFound {
                                        Text("（引擎标为不可用：\(row.status)）")
                                            .font(.caption).foregroundStyle(.orange)
                                    }
                                    Spacer()
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxHeight: 150)
                } else if !model.annualLoading && model.annualError.isEmpty {
                    Text("年度清单为空（先选当期文件夹并预览，或该年没有可纳入的期）。")
                        .font(.caption).foregroundStyle(.secondary)
                }

                if let missing = DirPushFeature.missingIssuesText(from: model.annualPreview) {
                    Text(missing).font(.caption).foregroundStyle(.orange)
                }
            }
        }
    }

    // MARK: - 输出根 / 生成

    private var outRootSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("输出根").font(.headline)
            HStack(spacing: 8) {
                DroppablePathField(text: $model.outRoot,
                                   placeholder: "输出根目录（默认 ~/Documents/sysu/编辑部/目录摘要推送/）",
                                   disabled: model.running) { url in
                    PathDrop.receive(url, kind: .directory, label: "输出根", log: nil,
                                     setPath: { model.outRoot = $0 }, onAccept: nil)
                }
                Button("浏览…") { browseOutRoot() }
                    .disabled(model.running)
                Button("恢复默认") { model.outRoot = DirPushFeature.defaultOutRootPath }
                    .disabled(model.running)
            }
            Text("默认：\(DirPushFeature.defaultOutRootPath)/；生成成功后记住这里填的值。")
                .font(.caption).foregroundStyle(.secondary)
                .textSelection(.enabled)
        }
    }

    @ViewBuilder
    private var generateRow: some View {
        HStack(spacing: 10) {
            if model.running {
                Button {
                    model.cancel?.cancel()
                } label: {
                    Label("取消", systemImage: "stop.circle")
                }
            } else {
                Button("生成推送文案") { generate() }
                    .keyboardShortcut("r", modifiers: .command)
                    .buttonStyle(.borderedProminent)
                    .disabled(model.issuePath.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
    }

    @ViewBuilder
    private var statusBanner: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: model.statusIsError
                  ? "exclamationmark.triangle.fill" : "checkmark.seal.fill")
                .foregroundStyle(model.statusIsError ? Color.red : Color.green)
            Text(model.statusText)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
            Spacer()
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill((model.statusIsError ? Color.red : Color.green).opacity(0.10)))
    }

    @ViewBuilder
    private var resultSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("生成结果").font(.headline)
            ForEach(model.resultOutputs, id: \.self) { path in
                HStack(spacing: 8) {
                    Image(systemName: "doc.text")
                    Text(path)
                        .font(.system(size: 11, design: .monospaced))
                        .lineLimit(1)
                        .help(path)
                        .textSelection(.enabled)
                    Button("打开") { openResult(path) }
                        .controlSize(.small)
                    Button("在访达中显示") {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
                    }
                    .controlSize(.small)
                    Spacer()
                }
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.accentColor.opacity(0.08)))
    }

    // MARK: - 动作

    private func browseIssue() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "选择当期文件夹"
        if panel.runModal() == .OK, let url = panel.url {
            model.issuePath = url.path
        }
    }

    private func browseOutRoot() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "选择输出根"
        if panel.runModal() == .OK, let url = panel.url {
            model.outRoot = url.path
        }
    }

    /// 输入变化后稍等一下再要预览（别每敲一个字符就起一个 python）。
    private func schedulePreview() {
        let m = model
        m.previewToken += 1
        let token = m.previewToken
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
            guard m.previewToken == token else { return }
            startPreview(token: token)
        }
    }

    private func reloadPreview() {
        model.previewToken += 1
        startPreview(token: model.previewToken)
    }

    private func startPreview(token: Int) {
        let m = model
        let raw = m.issuePath.trimmingCharacters(in: .whitespacesAndNewlines)
        let target = PathKit.normalize(raw)
        guard !raw.isEmpty else {
            m.preview = nil
            m.previewError = ""
            m.previewLoading = false
            m.annualPreview = nil
            m.annualRows = []
            return
        }
        guard PathKit.isDirectory(target) else {
            m.preview = nil
            m.previewError = "不是有效文件夹：\(raw)"
            m.previewLoading = false
            m.annualPreview = nil
            m.annualRows = []
            return
        }
        let outRoot = PathKit.normalize(m.outRoot)
        m.previewLoading = true
        m.previewError = ""
        let logger = Logger(store: log)
        DispatchQueue.global(qos: .userInitiated).async {
            // 预览一律按引擎推断的体例（不传 --with-en-toc），勾选框默认取它的值。
            let outcome = DirPushFeature.preview(issue: target, outRoot: outRoot,
                                                 withEnToc: false, log: logger)
            DispatchQueue.main.async {
                guard m.previewToken == token else { return }
                m.previewLoading = false
                if outcome.succeeded, let preview = outcome.preview {
                    m.preview = preview
                    m.withEnToc = preview.withEnTocDefault
                    let auto = DirPushFeature.autoYear(from: preview)
                    if !auto.isEmpty && (m.annualYear.isEmpty || m.annualYear == m.lastAutoYear) {
                        m.lastAutoYear = auto
                        m.annualYear = auto
                    }
                    if m.annualMode { scheduleAnnualPreview() }
                } else {
                    m.preview = nil
                    m.previewError = outcome.message
                    m.annualPreview = nil
                    m.annualRows = []
                }
            }
        }
    }

    private func annualModeChanged() {
        let m = model
        if m.annualMode {
            if m.annualYear.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                m.annualYear = DirPushFeature.autoYear(from: m.preview)
            }
            scheduleAnnualPreview()
        } else {
            m.annualToken += 1                  // 丢弃在途的年度预览
            m.annualRows = []
            m.annualPreview = nil
            m.annualError = ""
            m.annualLoading = false
        }
    }

    /// 年份/当期变化后稍等一下再拉年度清单。
    private func scheduleAnnualPreview() {
        let m = model
        m.annualToken += 1
        let token = m.annualToken
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
            guard m.annualToken == token else { return }
            startAnnualPreview(token: token)
        }
    }

    private func startAnnualPreview(token: Int) {
        let m = model
        guard m.annualMode else {
            m.annualPreview = nil
            m.annualRows = []
            return
        }
        let target = PathKit.normalize(m.issuePath)
        guard PathKit.isDirectory(target) else { return }
        let outRoot = PathKit.normalize(m.outRoot)
        var year = m.annualYear.trimmingCharacters(in: .whitespacesAndNewlines)
        if year.isEmpty { year = DirPushFeature.autoYear(from: m.preview) }
        m.annualLoading = true
        m.annualError = ""
        let logger = Logger(store: log)
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = DirPushFeature.preview(issue: target, outRoot: outRoot,
                                                 annual: true, year: year, log: logger)
            DispatchQueue.main.async {
                guard m.annualToken == token else { return }
                m.annualLoading = false
                if outcome.succeeded, let preview = outcome.preview {
                    m.annualPreview = preview
                    m.annualRows = DirPushFeature.annualRows(from: preview)
                    if m.annualYear.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                       let y = preview.annual?.year {
                        m.annualYear = String(y)
                    }
                } else {
                    m.annualPreview = nil
                    m.annualRows = []
                    m.annualError = outcome.message
                }
            }
        }
    }

    private func generate() {
        let m = model
        let issue = PathKit.normalize(m.issuePath)
        guard PathKit.isDirectory(issue) else {
            m.statusText = "请先选择有效的当期文件夹。"
            m.statusIsError = true
            return
        }
        let outRoot = PathKit.normalize(m.outRoot)
        guard !outRoot.isEmpty else {
            m.statusText = "输出根不能为空。"
            m.statusIsError = true
            return
        }
        if m.annualMode && !m.annualLoading && m.annualRows.isEmpty {
            m.statusText = "年度汇总没有可纳入的期（先预览年度清单）。"
            m.statusIsError = true
            return
        }

        // 契约 §2.3：输出根在代码仓内 → 生成前先确认，取消则不跑。
        if let warning = DirPushFeature.repoOutputRootWarning(for: outRoot) {
            let alert = NSAlert()
            alert.messageText = "输出根在代码仓内"
            alert.informativeText = warning
            alert.alertStyle = .warning
            alert.addButton(withTitle: "继续生成")
            alert.addButton(withTitle: "取消")
            guard alert.runModal() == .alertFirstButtonReturn else {
                m.statusText = "已取消（输出根在代码仓内，未生成）。"
                m.statusIsError = false
                return
            }
        }

        let includeIssues = m.annualMode
            ? DirPushFeature.selectedIssues(from: m.annualRows) : []
        let year = m.annualMode ? m.annualYear.trimmingCharacters(in: .whitespacesAndNewlines) : ""
        let annualMode = m.annualMode
        let withEnToc = m.withEnToc
        let cancel = RunCancel()
        m.cancel = cancel
        let store = log
        m.running = true
        m.statusText = ""
        m.statusIsError = false
        m.resultOutputs = []
        DispatchQueue.global(qos: .userInitiated).async {
            // 引擎一次只跑一个模式（CLI 契约 mode = issue | annual）。勾了「同时生成年度汇总
            // 文案」就**顺序跑两趟**：① 当期（含 --with-en-toc）② 年度（--annual/--year/
            // --include-issues）。两份输出都只认各自 --json 的 outputs[]。
            var outputs: [String] = []
            var failures: [String] = []
            var cancelled = false

            let issueResult = DirPushFeature.run(issue: issue, outRoot: outRoot,
                                                 withEnToc: withEnToc, annual: false,
                                                 cancel: cancel, store: store)
            if issueResult.status == Shell.cancelledStatus {
                cancelled = true
            } else if issueResult.status == 0 && !issueResult.outputs.isEmpty {
                outputs += issueResult.outputs
            } else if issueResult.status == 0 {
                failures.append("当期：引擎成功但没有 outputs 路径")
            } else {
                failures.append("当期：退出码 \(issueResult.status)，\(issueResult.message)")
            }

            if annualMode && !cancelled && !cancel.isCancelled {
                let annualResult = DirPushFeature.run(issue: issue, outRoot: outRoot,
                                                      annual: true, year: year,
                                                      includeIssues: includeIssues,
                                                      cancel: cancel, store: store)
                if annualResult.status == Shell.cancelledStatus {
                    cancelled = true
                } else if annualResult.status == 0 && !annualResult.outputs.isEmpty {
                    outputs += annualResult.outputs
                } else if annualResult.status == 0 {
                    failures.append("年度：引擎成功但没有 outputs 路径")
                } else {
                    failures.append("年度：退出码 \(annualResult.status)，\(annualResult.message)")
                }
            }

            DispatchQueue.main.async {
                m.running = false
                if cancelled {
                    m.statusText = "⏹ 已取消（已写入的 docx 可能不完整，需要时重新生成）。"
                    m.statusIsError = false
                    return
                }
                if failures.isEmpty && !outputs.isEmpty {
                    // 契约 §2.3：每次**生成成功后**记住输出根。
                    DirPushFeature.rememberOutRoot(outRoot)
                    m.resultOutputs = outputs
                    m.statusText = "✅ 已生成 \(outputs.count) 份 docx（同名覆盖、不留备份）。"
                    m.statusIsError = false
                } else if !outputs.isEmpty {
                    DirPushFeature.rememberOutRoot(outRoot)
                    m.resultOutputs = outputs
                    m.statusText = "⚠️ 已生成 \(outputs.count) 份，但有部分失败："
                        + failures.joined(separator: "；")
                    m.statusIsError = true
                } else {
                    m.statusText = "❌ 生成失败："
                        + (failures.isEmpty ? "引擎没有返回 outputs 路径（见日志）。"
                                            : failures.joined(separator: "；"))
                    m.statusIsError = true
                }
            }
        }
    }

    /// 「打开」：优先打开 docx 文件本身（Word）；不存在说明路径失效，明确报错。
    private func openResult(_ path: String) {
        guard FileManager.default.fileExists(atPath: path) else {
            log.append("⚠ 生成结果路径不存在：\(path)\n")
            return
        }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }
}
