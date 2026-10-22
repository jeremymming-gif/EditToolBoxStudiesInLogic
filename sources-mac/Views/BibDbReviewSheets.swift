import SwiftUI

/// B-190：从 `BibDbReviewView.swift` **纯搬迁**的「一键全部采用」预览 sheet（行为不变）。
///
/// 备份制已取消：文案改为「写库成功后自动本地 commit（不推送）；撤销用版本区『回退到上一个提交…』」。
struct BibDbAdoptSheet: View {
    @ObservedObject var model: BibDbReviewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("一键全部采用 · 预览（dry-run，未写库）").font(.title3).bold()
                Spacer()
                Button("关闭") { model.showAdoptSheet = false }
                    .disabled(model.running)
            }
            if let preview = model.adoptPreview {
                let verifiedRisk = model.conflicts?.verifiedPendingCount ?? 0
                highRiskBanner(preview: preview, verifiedRisk: verifiedRisk)
                HStack(spacing: 18) {
                    statText("将改记录", "\(preview.recordCount) 条")
                    statText("将改字段", "\(preview.fieldCount) 个")
                    statText("风险（已核实）", "\(verifiedRisk) 条",
                             color: verifiedRisk > 0 ? .red : .secondary)
                    statText("重叠 superseded", "\(preview.supersededCount) 条",
                             color: preview.supersededCount > 0 ? .orange : .secondary)
                    Spacer()
                }
                Text("写库成功后自动本地 git commit（只提交库文件；不推送）；dry-run 不写库、不产生提交。")
                    .font(.caption).foregroundStyle(.secondary)
                    .textSelection(.enabled)
                Divider()
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(preview.adopted) { item in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.recordKey)
                                    .font(.system(.caption, design: .monospaced)).bold()
                                ForEach(item.changes) { change in
                                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                                        Text("· \(change.name)：").font(.caption).foregroundStyle(.secondary)
                                        Text(change.oldText)
                                            .font(.system(.caption, design: .monospaced))
                                            .foregroundStyle(.secondary)
                                        Text("→").font(.caption).foregroundStyle(.tertiary)
                                        Text(change.newText)
                                            .font(.system(.caption, design: .monospaced))
                                    }
                                }
                            }
                            .padding(6)
                        }
                    }
                    .padding(2)
                }
                .frame(minHeight: 120, maxHeight: 320)
            } else {
                BibDbStatusLine(kind: .warning, text: "还没有 dry-run 预览数据。")
            }
            if !model.error.isEmpty {
                BibDbStatusLine(kind: .error, text: model.error)
            }
            if let version = model.versionNotice, !version.text.isEmpty {
                BibDbStatusLine(kind: version.failed ? .warning : .idle, text: version.text)
            }
            Divider()
            HStack(spacing: 12) {
                Button("取消") { model.showAdoptSheet = false }
                    .disabled(model.running)
                Spacer()
                if model.running {
                    ProgressView().controlSize(.small)
                    Text(model.stage).font(.caption).foregroundStyle(.secondary)
                }
                Button("确认全部采用（写库）…") { model.confirmAdoptAll = true }
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                    .disabled(model.running
                              || !model.adoptChecked
                              || (model.adoptPreview?.adopted.isEmpty ?? true))
                    .help("点击后再弹一次确认框（列将改条数/字段数）；成功后自动本地提交库文件")
            }
        }
        .padding(20)
        .frame(width: 760, height: 560, alignment: .leading)
        // 二次确认挂在**本 sheet 自己**上：挂在主 view 上会被「正在呈现 sheet」抑制
        // （批73 真点缺陷 P1：sheet 内确认框不弹）。
        .alert("确认一键全部采用？", isPresented: $model.confirmAdoptAll) {
            Button("取消", role: .cancel) {}
            Button("确认全部采用（写库）", role: .destructive) { model.performAdoptAll() }
        } message: {
            if let preview = model.adoptPreview {
                Text("将改 \(preview.recordCount) 条记录 / \(preview.fieldCount) 个字段。\n"
                     + "逐条 old→new 见预览列表。写库成功后自动本地提交库文件（不推送）；"
                     + "撤销用版本区「回退到上一个提交…」。")
            } else {
                Text("将按新稿值批量修改引用库。")
            }
        }
    }

    private func highRiskBanner(preview: BibDbFeature.AdoptAllOutcome, verifiedRisk: Int) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text("⚠️ 二次确认前请逐条核对下面的 old → new：")
                .font(.caption).bold().foregroundStyle(.red)
            Text("本操作会一次性修改 \(preview.recordCount) 条记录；其中库内已核实记录 \(verifiedRisk) 条。"
                 + "写库成功后自动本地提交（不推送）；撤销用版本区「回退到上一个提交…」。")
                .font(.caption).foregroundStyle(.red)
                .fixedSize(horizontal: false, vertical: true)
            if preview.pendingBefore >= 0 {
                Text("库内冲突：待裁决 \(preview.pendingBefore) 条 → 采用后 \(preview.pendingAfter) 条")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.06))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.red.opacity(0.25)))
    }

    private func statText(_ title: String, _ value: String, color: Color = .primary) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.callout).bold().foregroundStyle(color).monospacedDigit()
        }
    }
}

/// B-190：从 `BibDbReviewView.swift` **纯搬迁**的「编辑裁决 bib」sheet（行为不变）。
struct BibDbEditSheet: View {
    @ObservedObject var model: BibDbReviewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("编辑裁决 bib · \(model.editItem.map { "C" + String(format: "%03d", $0.stableNo) } ?? "")")
                    .font(.title3).bold()
                Spacer()
                Button("关闭") { model.showEditSheet = false }
                    .disabled(model.running)
            }
            Text("库内记录：\(model.editKey) · \(model.editTitle)")
                .font(.callout).textSelection(.enabled)
            Text("冲突指纹：\(model.editIncomingSHA.isEmpty ? "（无：该条冲突无指纹，引擎会拒绝 replace）" : model.editIncomingSHA)")
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            if model.editLoading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在预填库内条目（query --key --bibtex）…").font(.caption)
                }
            }
            TextEditor(text: $model.editBibText)
                .font(.system(.body, design: .monospaced))
                .disabled(model.running || model.editLoading)
                .frame(minHeight: 220)
                .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.secondary.opacity(0.3)))
            Toggle("接受覆盖重叠旧基线（--allow-overlap：把同记录字段重叠的其它待裁决冲突标 superseded）",
                   isOn: $model.editAllowOverlap)
                .disabled(model.running)
                .font(.caption)
            if let preview = model.editPreview {
                Divider()
                VStack(alignment: .leading, spacing: 4) {
                    Text("预览差异（dry-run，库未改）").font(.callout).bold()
                    if preview.changes.isEmpty {
                        Text("与库内条目无字段差异。").font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(preview.changes) { change in
                        HStack(alignment: .firstTextBaseline, spacing: 4) {
                            Text("· \(change.name)：").font(.caption).foregroundStyle(.secondary)
                            Text(change.oldText)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.secondary)
                            Text("→").font(.caption).foregroundStyle(.tertiary)
                            Text(change.newText)
                                .font(.system(.caption, design: .monospaced))
                        }
                    }
                    if preview.overlappingCount > 0 {
                        Text("⚠ 有 \(preview.overlappingCount) 条重叠冲突"
                             + (preview.supersededCount > 0 ? "（将标 superseded）" : "（默认拒绝，需勾选 allow-overlap）"))
                            .font(.caption).foregroundStyle(.orange)
                    }
                    if !preview.dbShaBefore.isEmpty {
                        Text("库 sha：\(preview.dbShaBefore.prefix(12))… → \(preview.dbShaAfter.prefix(12))…")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
            if !model.editError.isEmpty {
                BibDbStatusLine(kind: .error, text: model.editError)
            }
            if !model.editMessage.isEmpty {
                BibDbStatusLine(kind: model.editPreview == nil ? .ok : .warning, text: model.editMessage)
            }
            if let version = model.versionNotice, !version.text.isEmpty {
                BibDbStatusLine(kind: version.failed ? .warning : .idle, text: version.text)
            }
            Divider()
            HStack(spacing: 12) {
                Button("预览差异（dry-run，不写库）") { model.previewEdit() }
                    .disabled(model.running || model.editLoading || model.editBibText.isEmpty)
                Spacer()
                if model.running {
                    ProgressView().controlSize(.small)
                    Text(model.stage).font(.caption).foregroundStyle(.secondary)
                }
                Button("确认应用（写库）…") { model.confirmEditApply = true }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.running || !model.editPreviewMatchesInputs)
                    .help("先预览；预览后改动 bib / 勾选会作废预览，必须重新预览")
            }
        }
        .padding(20)
        .frame(width: 720, height: 620, alignment: .leading)
        // 二次确认挂在**本 sheet 自己**上（批73 真点缺陷 P1：主 view 上的 alert 会被 sheet 抑制）。
        .alert("确认应用编辑后的 bib？", isPresented: $model.confirmEditApply) {
            Button("取消", role: .cancel) {}
            Button("确认写库", role: .destructive) { model.performEditApply() }
        } message: {
            let count = model.editPreview?.changes.count ?? 0
            Text("将改 \(count) 个字段；写库成功后自动本地提交库文件（不推送）。"
                 + "撤销用版本区「回退到上一个提交…」。")
        }
    }
}
