import SwiftUI

extension ConvertTab {
    /// 两段式「AI 协同初排」开关（默认关）+ 勾上后的说明。
    @ViewBuilder var coopToggleRow: some View {
        // 默认关 → 协同相关字段一个都不进 argv，转换行为与批 54 逐字节相同。
        Toggle("AI 协同初排（两段式；外部 chat 免费流程）",
               isOn: $model.options.coop)
            .onChange(of: model.options.coop) { on in
                if on { refreshCoopStage() }
            }
        if model.options.coop {
            Text(Self.coopToggleHintText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.leading, 18)
        }
    }


    // MARK: - 第 55 批：两段式「AI 协同初排」
    // （说明文案统一在上方「文案常量」区：coopToggleHintText / coopNoStageText /
    //  coopApplyHintText / coopSuggestionsTimingText / coopRepliesHintText /
    //  coopDeliveredText / coopFulltextHintText 等）
    /// 两段式的状态区：状态行 + 按钮组（按钮可见性完全由 `coopStage.phase` 决定）。
    var coopPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider()
            Text("AI 协同初排（两段式）").font(.callout).bold()
            // 批 62 入口收敛：任务书从此只有“两段式第一段”这一个界面入口（plus ⑪ 直连 AI）。
            Text(Self.coopTaskbookNoteText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            coopFailureRow
            // ---------- 第 59 批：全文 AI 核查（任务书附正文；默认不勾） ----------
            // 默认不勾 → 一个参数都不加（默认 argv 与批 58 逐字节相同）。
            Divider()
            coopFulltextToggleRow
            coopFulltextScopePicker
            Text(Self.coopFulltextHintText)
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            coopStageSection
        }
        .padding(10)
        .background(Color.secondary.opacity(0.07))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder private var coopFailureRow: some View {
        if !model.coopFailure.isEmpty {
            Text(model.coopFailure)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.red)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var coopFulltextToggleRow: some View {
        Toggle("全文 AI 核查（任务书附正文）", isOn: $model.options.coopFulltext)
            .toggleStyle(.checkbox)
    }

    @ViewBuilder private var coopFulltextScopePicker: some View {
        if model.options.coopFulltext {
            Picker("核查范围", selection: $model.options.coopFulltextScope) {
                ForEach(ConvertFeature.FulltextScope.allCases, id: \.self) { scope in
                    Text(scope.title).tag(scope)
                }
            }
            .pickerStyle(.radioGroup)
            .labelsHidden()
            .padding(.leading, 18)
        }
    }

    @ViewBuilder private var coopStageSection: some View {
        if let stage = model.coopStage {
            coopStagePanel(stage)
        } else {
            Text(Self.coopNoStageText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// 暂存目录状态面板。**第一段状态只叫「暂存目录」**，绝不写成交付目录。
    @ViewBuilder private func coopStagePanel(_ stage: CoopFeature.StageState) -> some View {
        Text(statusLine(stage))
            .font(.callout).bold()
        coopCandidatesNote(stage)
        coopStageBody(stage)
    }

    private func statusLine(_ stage: CoopFeature.StageState) -> String {
        "状态：" + CoopFeature.stageStatusText(stage)
    }

    @ViewBuilder private func coopCandidatesNote(_ stage: CoopFeature.StageState) -> some View {
        if model.coopCandidates > 1 {
            Text("发现 \(model.coopCandidates) 个匹配的暂存目录，已选最近修改的一个：\(stage.stageDir)")
                .font(.caption).foregroundStyle(.orange)
        }
    }

    @ViewBuilder private func coopStageBody(_ stage: CoopFeature.StageState) -> some View {
        if stage.isDelivered {
            coopDeliveredPanel(stage)
        } else {
            coopPendingPanel(stage)
        }
    }

    /// 已交付：只显示**最终目录**与协同记录（暂存目录可以仍在，但不作为交付物展示）。
    ///
    /// 第 56 批：成功摘要改读 `state.stats`（旧状态回退 record/旧 state），口径固定为
    /// 「文献回填 x/y（并入 z 条，未回填 n）｜低置信回复 a（应用 b / 仅记录待人工 c）」；
    /// 文献回填 0/N 时**橙色警告**（用户真测就栽在「只导入 JSON、丢了 BibTeX」却看到全绿）。
    @ViewBuilder private func coopDeliveredPanel(_ stage: CoopFeature.StageState) -> some View {
        Text(deliveredDirLine(stage))
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
        HStack(spacing: 10) {
            Button("打开交付目录") { openDeliveredDir(stage) }
            deliveredRecordButton
            deliveredSuggestionsButton
        }
        deliveredResultSection
        Text(Self.coopDeliveredText)
            .font(.caption).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func deliveredDirLine(_ stage: CoopFeature.StageState) -> String {
        "交付目录：\(stage.deliveredTo.isEmpty ? stage.finalDir : stage.deliveredTo)"
    }

    private func openDeliveredDir(_ stage: CoopFeature.StageState) {
        openPath(stage.deliveredTo.isEmpty ? stage.finalDir : stage.deliveredTo)
    }

    @ViewBuilder private var deliveredRecordButton: some View {
        if !model.coopRecordPath.isEmpty {
            Button("打开协同记录") { openPath(model.coopRecordPath) }
        }
    }

    @ViewBuilder private var deliveredSuggestionsButton: some View {
        if let result = model.coopResult,
           !result.suggestionsMarkdownPath.isEmpty,
           FileManager.default.fileExists(atPath: result.suggestionsMarkdownPath) {
            Button("打开建议清单") { openPath(result.suggestionsMarkdownPath) }
        }
    }

    @ViewBuilder private var deliveredResultSection: some View {
        if let result = model.coopResult {
            Text(result.summaryLine)
                .font(.callout).bold()
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            deliveredZeroBibWarning(result)
            deliveredLegacyNote(result)
        }
    }

    @ViewBuilder private func deliveredZeroBibWarning(_ result: CoopFeature.CoopResult) -> some View {
        if let warning = result.zeroBibWarning {
            Text(warningLine(warning))
                .font(.callout).bold()
                .foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private func deliveredLegacyNote(_ result: CoopFeature.CoopResult) -> some View {
        if result.stats.legacy {
            Text(Self.coopDeliveredLegacyNoteText)
                .font(.caption).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func warningLine(_ text: String) -> String { "⚠ " + text }

    /// 待人工核实（ph1 / ph2 未完成）：暂存目录 + 任务书 + 回填导入（多文件/目录/剪贴板）
    /// + 已选清单（可移除/清空）+ 一键修改产物 + 第二段。
    @ViewBuilder private func coopPendingPanel(_ stage: CoopFeature.StageState) -> some View {
        Text(pendingStageLine(stage))
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
        pendingStageButtons(stage)
        Divider().padding(.vertical, 2)
        Text("回填导入").font(.callout).bold()
        pendingRepliesImportRow
        Text(model.replies.summary)
            .font(.caption).bold()
            .foregroundStyle(model.replies.isEmpty ? Color.secondary : Color.primary)
        pendingRepliesList
        Text(Self.coopRepliesHintText)
            .font(.caption).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        pendingApplySection
        pendingSecondPhaseSection(stage)
        Text(Self.coopSuggestionsTimingText)
            .font(.caption).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
        Text(Self.coopFirstPhaseNoteText)
            .font(.caption).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func pendingStageLine(_ stage: CoopFeature.StageState) -> String {
        "暂存目录（**未交付**，不是最终产物）：\(stage.stageDir)"
    }

    @ViewBuilder private func pendingStageButtons(_ stage: CoopFeature.StageState) -> some View {
        HStack(spacing: 10) {
            pendingTaskbookButton(stage)
            Button("打开暂存目录") { openStageDir(stage) }
            pendingRunningSpinner
        }
    }

    private func openStageDir(_ stage: CoopFeature.StageState) {
        openPath(stage.stageDir)
    }

    @ViewBuilder private func pendingTaskbookButton(_ stage: CoopFeature.StageState) -> some View {
        if !stage.taskbook.isEmpty {
            Button("打开任务书") { openPath(stage.taskbook) }
        }
    }

    @ViewBuilder private var pendingRunningSpinner: some View {
        if model.running {
            ProgressView().controlSize(.small)
        }
    }

    private var pendingRepliesImportRow: some View {
        HStack(spacing: 8) {
            Button("添加文件…") { addReplyFiles() }
            Button("添加目录…") { addReplyDirectory() }
            Button("从剪贴板导入") { importClipboard() }
            clearRepliesButton
            Spacer()
        }
        .disabled(model.running)
    }

    @ViewBuilder private var clearRepliesButton: some View {
        if !model.replies.isEmpty {
            Button("全部清除") { clearReplies() }
        }
    }

    private var pendingRepliesList: some View {
        ForEach(Array(model.replies.items.enumerated()), id: \.element.path) { index, item in
            pendingReplyRow(index: index, item: item)
        }
    }

    private func pendingReplyRow(index: Int, item: CoopFeature.ReplyItem) -> some View {
        HStack(spacing: 6) {
            Text(replyIcon(item))
                .font(.caption)
            Text(item.displayName).font(.caption)
            Text(item.path)
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(.tertiary)
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
            Spacer(minLength: 4)
            Button("移除") { removeReply(at: index) }
                .buttonStyle(.borderless)
                .disabled(model.running)
        }
        .padding(.leading, 12)
    }

    private func replyIcon(_ item: CoopFeature.ReplyItem) -> String {
        CoopFeature.isClipboardDraft(item.path) ? "📋" : (item.isDirectory ? "📁" : "📄")
    }

    private var pendingApplySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("一键修改产物（--coop-apply；默认不勾）", isOn: $model.coopApply)
                .toggleStyle(.checkbox)
            Text(Self.coopApplyHintText)
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func pendingSecondPhaseSection(_ stage: CoopFeature.StageState) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            secondPhaseButton(stage)
            secondPhaseDisabledHint(stage)
        }
    }

    private func secondPhaseButton(_ stage: CoopFeature.StageState) -> some View {
        Button {
            runSecondPhase()
        } label: {
            Label("运行第二段（续跑 → 编译 → 交付）", systemImage: "square.and.arrow.down.on.square")
        }
        .disabled(model.running
                  || !CoopFeature.canRunSecondPhase(replyCount: model.replies.count, stage: stage))
    }

    @ViewBuilder private func secondPhaseDisabledHint(_ stage: CoopFeature.StageState) -> some View {
        if let hint = CoopFeature.secondPhaseDisabledHint(replyCount: model.replies.count, stage: stage) {
            Text(hint)
                .font(.caption)
                .foregroundStyle(model.replies.isEmpty ? Color.orange : Color.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// 扫描当前源稿对应的暂存目录并刷新状态（勾开关 / 改源稿 / 改输出目录 / 跑完一段时调用）。
}
