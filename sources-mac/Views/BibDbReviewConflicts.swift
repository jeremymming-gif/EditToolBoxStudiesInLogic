import SwiftUI

extension BibDbReviewView {
    // MARK: - ① 字段冲突

    var conflictSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Text("① 字段冲突").font(.callout).bold()
                if let conflicts = model.conflicts {
                    Text(model.showResolvedConflicts
                         ? "按期 → 篇 → C 编号（引擎顺序；当前：显示全部，已裁决为一行摘要、点开明细）"
                         : "按期 → 篇 → C 编号（引擎顺序；当前：只看待裁决）")
                        .font(.caption).foregroundStyle(.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    if conflicts.verifiedPendingCount > 0 {
                        Text("⚠️ 其中 \(conflicts.verifiedPendingCount) 条是库内已核实记录，"
                             + "采用新值会改已出版核对结果")
                            .font(.caption).foregroundStyle(.red)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    Spacer(minLength: 0)
                }
            }
            if let state = model.listState, !state.emptyText.isEmpty {
                BibDbStatusLine(kind: .ok, text: state.emptyText)
            }
            if model.conflicts == nil && !model.running {
                BibDbStatusLine(kind: .warning,
                                text: "还没加载到字段冲突：点上面「刷新列表」（或先导入引用库）。")
            }
            ForEach(model.listState?.visibleGroups ?? []) { group in
                conflictGroup(group)
            }
            if let conflicts = model.conflicts, !conflicts.items.isEmpty {
                adoptAllSection(conflicts)
                editRestoreBar(conflicts)
            }
        }
    }

    private func conflictGroup(_ group: BibDbFeature.ConflictGroup) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text("期：\(group.issueTitle)").font(.caption).bold()
                Text("篇：\(group.slugTitle)").font(.caption).bold()
                if !group.file.isEmpty {
                    Text("（\(group.file)）").font(.caption2).foregroundStyle(.tertiary)
                }
                Spacer()
                // 分组头「待裁决 N（共 M）」：M 用已缓存的 groupTotals（含被开关隐藏的已裁决）
                Text(BibDbFeature.ConflictListState.groupCountText(
                    pending: group.pendingCount,
                    total: model.listState?.groupTotal(forGroupId: group.id) ?? group.items.count))
                    .font(.caption2).foregroundStyle(.secondary)
            }
            ForEach(group.items) { item in
                conflictRow(item)
            }
        }
        .padding(10)
        .background(Color.secondary.opacity(0.03))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.12)))
    }

    @ViewBuilder
    private func conflictRow(_ item: BibDbFeature.ConflictItem) -> some View {
        if item.pending {
            HStack(alignment: .top, spacing: 10) {
                conflictDetail(item)
                Spacer(minLength: 0)
                VStack(alignment: .trailing, spacing: 6) {
                    // B-196：库已过期（stale）→ 行内写入口全部临时置灰，先「刷新列表」
                    Button("保留旧值") { askResolve(item, action: "keep") }
                        .disabled(model.running || model.stale)
                        .help(model.stale
                              ? "库已变化：先点上方「刷新列表」重新加载（写入口临时禁用）"
                              : "keep：保留库内已核实旧值（只写裁决元数据，不改字段）")
                    Button("采用新值") { askResolve(item, action: "adopt") }
                        .disabled(model.running || model.stale || !item.hasFingerprint)
                        .help(model.stale
                              ? "库已变化：先点上方「刷新列表」重新加载（写入口临时禁用）"
                              : (item.hasFingerprint
                                 ? "adopt：逐字段采用新稿值（写库；会二次确认；成功后自动本地提交）"
                                 : "该条冲突无指纹（旧数据），引擎无法精确定位，需用编辑裁决 bib"))
                    Button("待人工") { askResolve(item, action: "defer") }
                        .disabled(model.running || model.stale)
                        .help(model.stale
                              ? "库已变化：先点上方「刷新列表」重新加载（写入口临时禁用）"
                              : "defer：标为待人工查证，先从待裁决列表移走（不改字段）")
                }
                .frame(width: 96)
            }
            .padding(8)
            .background(Color.clear)
            .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.secondary.opacity(0.12)))
        } else {
            resolvedConflictRow(item)
        }
    }

    /// 已裁决行：默认**一行摘要**（C 编号 / 动作 / key / 时间），点开展开明细（审计留痕）。
    /// B-202：自定义 disclosure 行（header = AXButton；箭头与文字都能开合），展开态由 `resolvedExpanded` 受控。
    private func resolvedConflictRow(_ item: BibDbFeature.ConflictItem) -> some View {
        BibDbRowDisclosure(
            title: item.stableLabel + (item.resolutionTitle.isEmpty
                                       ? " 已裁决" : " " + item.resolutionTitle),
            isExpanded: resolvedExpanded.contains(item.id),
            action: { toggleResolved(item.id) }) {
            HStack(spacing: 8) {
                Text(item.stableLabel)
                    .font(.system(.caption, design: .monospaced)).bold()
                    .foregroundStyle(.secondary)
                Text(item.resolutionTitle.isEmpty ? "已裁决" : item.resolutionTitle)
                    .font(.caption2)
                    .padding(.horizontal, 5).padding(.vertical, 1)
                    .background(Color.secondary.opacity(0.15))
                    .foregroundStyle(.secondary)
                    .clipShape(Capsule())
                Text(item.recordKey)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                Text(item.briefText)
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Spacer(minLength: 0)
                if !item.resolvedAt.isEmpty {
                    Text(item.resolvedAt)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.tertiary)
                }
            }
        } content: {
            HStack(alignment: .top, spacing: 10) {
                conflictDetail(item)
                Spacer(minLength: 0)
            }
            .padding(.top, 4)
        }
        .padding(8)
        .background(Color.secondary.opacity(0.06))
        .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.secondary.opacity(0.06)))
    }

    private func toggleResolved(_ id: String) {
        if resolvedExpanded.contains(id) { resolvedExpanded.remove(id) }
        else { resolvedExpanded.insert(id) }
    }

    /// 冲突条目明细（待裁决行的主体 / 已裁决行展开后的明细，共用同一套渲染）。
    private func conflictDetail(_ item: BibDbFeature.ConflictItem) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(item.stableLabel)
                    .font(.system(.callout, design: .monospaced)).bold()
                    .foregroundStyle(item.pending ? Color.primary : Color.secondary)
                Text(item.recordKey)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                if item.verified {
                    Text("已核实").font(.caption2)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(Color.green.opacity(0.15))
                        .foregroundStyle(.green)
                        .clipShape(Capsule())
                }
                if item.pending {
                    Text("待裁决").font(.caption2).foregroundStyle(.orange)
                }
            }
            Text(item.briefText)
                .font(.callout)
                .foregroundStyle(item.pending ? Color.primary : Color.secondary)
                .lineLimit(2)
            Text("来源（新稿）：\(item.sourceText)")
                .font(.caption).foregroundStyle(.secondary)
                .lineLimit(1).textSelection(.enabled)
            ForEach(item.diff) { diff in
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text("· \(diff.name)：").font(.caption).foregroundStyle(.secondary)
                    Text(diff.oldText)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                    Text("→").font(.caption).foregroundStyle(.tertiary)
                    Text(diff.newText)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(item.pending ? Color.primary : Color.secondary)
                        .lineLimit(2)
                }
            }
            if !item.advice.isEmpty {
                Text("建议：\(item.advice)").font(.caption2).foregroundStyle(.tertiary)
            }
            HStack(spacing: 10) {
                if !item.hasFingerprint {
                    Text("无冲突指纹（旧数据）").font(.caption2).foregroundStyle(.orange)
                } else {
                    Text("指纹 \(item.fingerprintShort)")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.tertiary)
                        .textSelection(.enabled)
                }
                if !item.method.isEmpty {
                    Text("核实方式 \(item.method)").font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
        }
    }

    private func askResolve(_ item: BibDbFeature.ConflictItem, action: String) {
        resolveRequest = ResolveRequest(item: item, action: action)
    }
}
