import SwiftUI

extension BibDbReviewView {
    // MARK: - ② 库健康

    var healthSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Text("② 库健康").font(.callout).bold()
                if let health = model.health {
                    Text(BibDbFeature.healthStatusText(health.lint))
                        .font(.callout).bold().monospacedDigit()
                        .foregroundStyle(health.warningCount > 0 ? Color.orange : Color.secondary)
                    if health.affectedRecords > 0 {
                        Text("（受影响记录并集 \(health.affectedRecords) 条）")
                            .font(.caption).foregroundStyle(.tertiary)
                    }
                } else {
                    Text(model.running ? "正在读取（lint --with-records）…" : "（未加载）")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
            Text("数据源：引擎 `lint --with-records`（8 类固定顺序；展开分类看逐条受影响记录，"
                 + "记录多时用搜索/「再显示 200 条」；重复/近重复对可打开「对照合并」或"
                 + "「确认不同（不再提示）」，**绝不自动合并**）。")
                .font(.caption2).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            if let health = model.health {
                if health.categories.isEmpty {
                    BibDbStatusLine(kind: .ok, text: "库健康检查没有 warning。")
                } else {
                    ForEach(health.categories) { category in
                        healthCategory(category)
                    }
                }
            } else if !model.running && model.loadedOnce {
                BibDbStatusLine(kind: .warning,
                                text: "还没加载到库健康数据：点上面「刷新列表」重试。")
            }
        }
    }

    private func healthCategory(_ category: BibDbFeature.LintWarningCategory) -> some View {
        BibDbRowDisclosure(
            title: category.title,
            isExpanded: healthExpanded.contains(category.registry),
            action: { toggleHealthCategory(category.registry) }) {
            // B-202：整行可点（AX 名字 = 分类标题，如「缺字段」）；展开态沿用 healthExpanded。
            HStack(spacing: 8) {
                Text(category.title).font(.callout).bold()
                Text("\(category.warningCount) 条提示").font(.caption2).foregroundStyle(.secondary)
                let records = category.items.reduce(0) { $0 + $1.records.count }
                if records > 0 {
                    Text("受影响记录 \(records) 条").font(.caption2).foregroundStyle(.tertiary)
                }
                Spacer(minLength: 0)
                if category.registry == "duplicates" || category.registry == "near_duplicates" {
                    Text("可逐对「对照合并」或「确认不同（不再提示）」")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
        } content: {
            VStack(alignment: .leading, spacing: 8) {
                if let note = category.note {
                    Text(note).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                categoryActions(category)
                // 搜索只对「逐条记录」类（缺字段/entry_type/alias…）有意义；
                // 重复/近重复类是一对一条的 warning，不做 key 搜索（避免误导）。
                let searchableTotal = category.items.reduce(0) { sum, item in
                    sum + (mergePairKeys(category.registry, item) == nil ? item.records.count : 0)
                }
                if searchableTotal > 12 {
                    HStack(spacing: 6) {
                        TextField("在 \(searchableTotal) 条受影响记录里搜索 key…",
                                  text: healthSearchBinding(category.registry))
                            .textFieldStyle(.roundedBorder)
                            .frame(maxWidth: 360)
                        Text("\(filteredRecordCount(category)) / \(searchableTotal)")
                            .font(.caption2).foregroundStyle(.tertiary)
                        Spacer(minLength: 0)
                    }
                }
                ForEach(category.items) { item in
                    healthItem(category: category, item: item)
                }
            }
            .padding(.top, 6)
            .padding(.leading, 4)
        }
        .id("category-\(category.registry)")
        .padding(10)
        .background(Color.secondary.opacity(0.04))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.12)))
    }

    private func toggleHealthCategory(_ registry: String) {
        if healthExpanded.contains(registry) { healthExpanded.remove(registry) }
        else { healthExpanded.insert(registry) }
    }

    /// 分类顶部的批量动作：缺字段 / entry_type（共享 sheet，由 sheet 自己确认写库）。
    @ViewBuilder
    private func categoryActions(_ category: BibDbFeature.LintWarningCategory) -> some View {
        switch category.registry {
        case "missing_field":
            HStack(spacing: 8) {
                Button("批量预览归位…") {
                    Logger(store: log)("引用库待处理：打开字段归位预览（missing_field）…\n")
                    model.fieldFixTarget = ReviewFieldFixTarget(registry: "missing_field")
                }
                .disabled(model.running || model.stale)
                .help(model.stale
                      ? "库已变化：先点上方「刷新列表」重新加载（写入口临时禁用）"
                      : "来自引擎 suggest-field-fixes --registry missing_field："
                        + "勾选后逐条 edit --apply（统一 sha 闸；整批写完后自动本地提交一次）")
                Text("A 类：缺 title/缺 year 等，候选归位来自引擎；不自动应用")
                    .font(.caption2).foregroundStyle(.tertiary)
                Spacer(minLength: 0)
            }
        case "unknown_entry_type":
            HStack(spacing: 8) {
                Button("先修 entry_type/key…") {
                    Logger(store: log)("引用库待处理：打开字段归位预览（unknown_entry_type）…\n")
                    model.fieldFixTarget = ReviewFieldFixTarget(registry: "unknown_entry_type")
                }
                .disabled(model.running || model.stale)
                .help(model.stale
                      ? "库已变化：先点上方「刷新列表」重新加载（写入口临时禁用）"
                      : "来自引擎 suggest-field-fixes --registry unknown_entry_type：按 anomaly_kind 分组")
                Text("C 类：entry_type 笔误 / DOI 或占位符当 key（不自动应用）")
                    .font(.caption2).foregroundStyle(.tertiary)
                Spacer(minLength: 0)
            }
        case "conflicts":
            HStack(spacing: 8) {
                Button("去①字段冲突") { scrollTarget = "section-conflicts" }
                    .disabled(model.running)
                Text("字段冲突的逐条裁决在①区（本类此处只显示计数）。")
                    .font(.caption2).foregroundStyle(.tertiary)
                Spacer(minLength: 0)
            }
        default:
            EmptyView()
        }
    }

    private func healthSearchBinding(_ registry: String) -> Binding<String> {
        Binding(get: { healthSearch[registry] ?? "" },
                set: { healthSearch[registry] = $0 })
    }

    /// 前端过滤已加载的 records（不重跑引擎、不写库；只筛展示）。
    private func filteredRecords(_ registry: String, _ records: [String]) -> [String] {
        let query = (healthSearch[registry] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return records }
        return records.filter { $0.localizedCaseInsensitiveContains(query) }
    }

    private func filteredRecordCount(_ category: BibDbFeature.LintWarningCategory) -> Int {
        category.items.reduce(0) { $0 + filteredRecords(category.registry, $1.records).count }
    }

    /// duplicates：`item.records` 直接是 2 个 key；near_duplicates：records 是 `A≈B` 串，
    /// 按 `≈` 拆两侧（**拆展示串不是判据**，只为把两条 key 交给合并 sheet）。
    private func mergePairKeys(_ registry: String, _ item: BibDbFeature.LintWarningItem) -> [String]? {
        switch registry {
        case "duplicates":
            let keys = item.records.filter { !$0.isEmpty }
            return keys.count >= 2 ? keys : nil
        case "near_duplicates":
            guard let text = item.records.first else { return nil }
            let parts = text.components(separatedBy: "≈").map {
                $0.trimmingCharacters(in: .whitespacesAndNewlines)
            }.filter { !$0.isEmpty }
            return parts.count >= 2 ? parts : nil
        default:
            return nil
        }
    }

    /// registry → `ack-duplicates --code`（B-205）。**两张名字不同**：待处理页的 registry
    /// `duplicates` 在引擎里是「重复记录」= `duplicate_content`；`near_duplicates` 同名。
    /// 映射只此一处（别在行内再判一次）。
    private func healthAckCode(_ registry: String) -> String {
        registry == "duplicates" ? "duplicate_content" : "near_duplicates"
    }

    private func healthItem(category: BibDbFeature.LintWarningCategory,
                            item: BibDbFeature.LintWarningItem) -> some View {
        let pair = mergePairKeys(category.registry, item)
        let records = pair == nil ? filteredRecords(category.registry, item.records) : item.records
        let query = (healthSearch[category.registry] ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("· " + item.detail).font(.caption).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Text("\(item.count) 条").font(.caption2).foregroundStyle(.secondary)
            }
            if !item.action.isEmpty {
                Text("建议：\(item.action)").font(.caption2).foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if item.records.isEmpty {
                if item.registry == "conflicts" {
                    Text("待裁决 \(item.pendingRecords) 条 / 历史已裁决 \(item.resolvedRecords) 条")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            } else if !records.isEmpty || query.isEmpty {
                healthRecordList(category: category, item: item, pair: pair, records: records)
            }
        }
        .padding(.leading, 8)
    }

    @ViewBuilder
    private func healthRecordList(category: BibDbFeature.LintWarningCategory,
                                  item: BibDbFeature.LintWarningItem,
                                  pair: [String]?,
                                  records: [String]) -> some View {
        if let pair {
            // 重复/近重复：一条 warning 就是一对；两侧各带「编辑条目」，一对一个「对照合并」
            HStack(spacing: 8) {
                ForEach(Array(pair.enumerated()), id: \.offset) { indexed in
                    Text(indexed.element)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                    // B-196：这两类打开的是会写库的 sheet；stale 时一并置灰
                    Button("编辑条目…") { openEditor(indexed.element) }
                        .controlSize(.small)
                        .disabled(model.running || model.stale)
                    if indexed.offset == 0 {
                        Button("对照合并…") { openMerge(pair) }
                            .controlSize(.small)
                            .disabled(model.running || model.stale)
                        // B-205：确认「这两条是不同条目」= 保持两条、不改库；写 sidecar
                        // （bib_db/health-ignore.json）+ 自动本地 commit（不推送）+ 刷新列表。
                        BibDbHealthAckControl(
                            code: healthAckCode(category.registry),
                            keys: pair,
                            disabled: model.running || model.stale,
                            onFinished: { model.refreshAfterWrite() })
                    }
                }
                Spacer(minLength: 0)
            }
        } else {
            let limit = healthLimits[item.id] ?? 200
            VStack(alignment: .leading, spacing: 3) {
                ForEach(Array(records.prefix(limit).enumerated()), id: \.offset) { indexed in
                    HStack(spacing: 8) {
                        Text(indexed.element)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                        Spacer(minLength: 0)
                        Button("编辑条目…") { openEditor(indexed.element) }
                            .controlSize(.small)
                            .disabled(model.running || model.stale)
                    }
                }
                if records.count > limit {
                    Button("再显示 200 条（还有 \(records.count - limit) 条）") {
                        healthLimits[item.id] = limit + 200
                    }
                    .controlSize(.small)
                }
            }
        }
    }
}
