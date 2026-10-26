import SwiftUI

/// 条目编辑器 sheet 的目标（`sheet(item:)` 需要 Identifiable；view 只负责展示，sheet 自己起任务）。
struct ReviewEditorTarget: Identifiable, Equatable {
    var key: String
    var id: String { key }
}

/// 对照/合并 sheet 的目标（2+ 条 key；重复对 / 近重复对）。
struct ReviewMergeTarget: Identifiable, Equatable {
    var keys: [String]
    var id: String { keys.joined(separator: "|") }
}

/// 字段归位 sheet 的目标（`"missing_field"` / `"unknown_entry_type"`）。
struct ReviewFieldFixTarget: Identifiable, Equatable {
    var registry: String
    var id: String { registry }
}

/// 「待处理」页的界面状态（批73 B-127：列表 + 行内三按钮 + 一键全部采用 + 编辑裁决 bib；
/// B-188：三段式 + 性能 P0 缓存；B-190：版本库状态区 + 写库成功自动本地 commit（绝不自动 push））。
///
/// 线程约定与其它页一致：会起子进程的 `BibDbFeature.*` 一律在后台队列调，回主线程回填。
/// **所有写操作串行**：`running` 期间所有按钮/行内按钮置灰（同一份库不能有两个写进同一个窗口）。
final class BibDbReviewModel: ObservableObject {

    // 清单文件 + 状态条
    @Published var files: [BibDbFeature.ReportFile] = []
    @Published var conflicts: BibDbFeature.ConflictsOutcome? = nil
    @Published var running = false          // 任何写/加载中（全部按钮置灰）
    @Published var stage: String = ""
    @Published var message: String = ""     // 成功话术（写运行日志）
    @Published var error: String = ""       // 失败话术（写运行日志；引擎中文错误）
    @Published var lastRun: BibDbFeature.ReportOutcome? = nil
    @Published var lastAction: String = ""  // 最近一次裁决/恢复的一句话结果（不写日志）
    @Published var loadSeconds: Double = 0

    // MARK: - B-188 P0 缓存（一次算好；body 不再重算）

    /// 冲突列表的**双态缓存**（Wave1 `ReviewCacheState`：默认 / 含已裁决各预计算一份）。
    /// 开关切换只换缓存态（O(1)），不重跑引擎、不在 body 里重建 `ConflictListState`。
    @Published var reviewCache: BibDbFeature.ReviewCacheState? = nil
    /// 本页本地开关（B-188：**不再**是共享 `bibdb.showResolvedConflicts`；跳转意图只写这里）
    @Published var showResolvedConflicts = false
    /// 库健康（`lint --with-records`，含 8 类 warning 与受影响记录全量）
    @Published var health: BibDbFeature.HealthOutcome? = nil
    /// 库内写库留痕（记录 `edits[]`，只读扫描）+ 已处理列表（M 与③区同源）
    @Published var traces: [BibDbFeature.EditTrace] = []
    @Published var processed: [BibDbFeature.ProcessedEntry] = []
    /// 加载完成时的库清单（进页新鲜度检查、状态条 sha 用）
    @Published var manifest: BibDbFeature.DBManifest? = nil
    @Published var healthSeconds: Double = 0
    @Published var tracesTotal = 0
    /// 进页发现库变了 → 提示点「刷新列表」，**不自动起引擎**（B-188）
    @Published var freshnessNotice = ""
    /// B-196：库已过期（`.stale`）→ 本页所有写库入口临时置灰；复查 `.fresh` / 刷新成功回填后清除。
    /// 只影响界面入口，写前 `--expect-db-sha256` 闸不变。
    @Published var stale = false
    /// 是否完成过一次加载（首次激活加载；之后只查新鲜度）
    @Published var loadedOnce = false
    /// 每次完整加载完成的世代号（视图据此消费「加载后才可能定位到」的跳转目标）
    @Published var loadGeneration = 0
    /// 后台新鲜度检查中（只 stat + sha，不写库；不挡写按钮）
    @Published var checkingFreshness = false

    // 三个共享 sheet 的展示目标（sheet 自己起后台任务/自己 dismiss；这里只记「打开谁」）
    @Published var editorTarget: ReviewEditorTarget? = nil
    @Published var mergeTarget: ReviewMergeTarget? = nil
    @Published var fieldFixTarget: ReviewFieldFixTarget? = nil

    // 行内裁决：确认框用的待执行请求（view 侧 .alert）
    // 一键全部采用：预览 sheet（dry-run 结果）+ 二次确认
    @Published var showAdoptSheet = false
    @Published var adoptPreview: BibDbFeature.AdoptAllOutcome? = nil
    @Published var adoptPreviewSHA = ""     // 预览时的库 sha（真写时作 --expect-db-sha256 前置校验用）
    @Published var adoptChecked = false     // 是否已完成 dry-run 预览
    @Published var confirmAdoptAll = false

    // 编辑裁决 bib sheet
    @Published var showEditSheet = false
    @Published var editItem: BibDbFeature.ConflictItem? = nil
    @Published var editKey = ""
    @Published var editIncomingSHA = ""
    @Published var editTitle = ""
    @Published var editBibText = ""
    @Published var editLoading = false
    @Published var editPreview: BibDbFeature.ResolveOutcome? = nil
    @Published var editPreviewSHA = ""      // 预览时输入的 sha 快照（输入一改即作废）
    @Published var editPreviewSignature = ""
    @Published var editAllowOverlap = false
    @Published var editError = ""
    @Published var editMessage = ""
    @Published var confirmEditApply = false

    /// B-190：写库成功后版本层（自动本地 commit）的结果提示（黄字 fail-soft，不属于写库失败）。
    @Published var versionNotice: BibDbVersionWriteNotice? = nil

    // MARK: - 读（清单文件 / 冲突条目）

    /// 只读刷新三个清单文件的存在/大小/时间（不跑引擎，主线程即可）。
    func reloadFiles() {
        files = BibDbFeature.reportFiles()
    }

    /// 清单文件里第一个存在的（「打开清单」按钮用）
    var primaryFile: BibDbFeature.ReportFile? {
        files.first { $0.exists }
    }

    /// 当前本地开关态对应的列表状态（Wave1 已缓存的 `ConflictListState`，O(1)）。
    /// body 只读它，不再每次重建 `ConflictListState(outcome:showResolved:)`。
    var listState: BibDbFeature.ConflictListState? { reviewCache?.listState }

    /// 本地开关切换：只换预计算好的缓存态，不重跑引擎、不触发任何子进程。
    func setShowResolved(_ value: Bool) {
        guard showResolvedConflicts != value else { return }
        showResolvedConflicts = value
        reviewCache?.showResolved = value
    }

    // MARK: - 加载策略（B-188 P0：进页不自动重载）

    /// 激活本页：首次跑一次完整加载；之后只做**新鲜度检查**（库变了只提示，不自动起引擎）。
    func activate() {
        guard !running else { return }
        if !loadedOnce {
            reloadAll()
            return
        }
        checkFreshness()
    }

    /// 新鲜度检查：后台算 `dbManifest`（6 MB 流式 sha），用 `reviewFreshness`（内部即 `isStale` 口径）分四态。
    /// 过期 → 提示 + **临时禁用本页写库入口**；仍然**绝不自动重跑引擎**（B-188 设计保留）。
    func checkFreshness() {
        guard !running, !checkingFreshness else { return }
        guard let db = BibDbFeature.existingDBPath(), !db.isEmpty else {
            // B-196 .missingDB：保持 B-188 原有提示；不当作过期
            stale = false
            freshnessNotice = "引用库文件不存在：先到「入藏」页导入 .bib，或到「引用库设置」页指定笔记仓。"
            return
        }
        let cached = manifest
        checkingFreshness = true
        let model = self
        DispatchQueue.global(qos: .utility).async {
            let current = BibDbFeature.dbManifest(db: db)
            DispatchQueue.main.async {
                model.checkingFreshness = false
                switch BibDbFeature.reviewFreshness(cached: cached, current: current) {
                case .stale:
                    // B-196：只提示 + 禁用写入口，不自动重跑引擎
                    model.stale = true
                    model.freshnessNotice = "库已变化（外部 CLI 改过库？）：本页写库已临时禁用，"
                        + "点「刷新列表」重新加载。"
                case .fresh:
                    model.stale = false
                    model.freshnessNotice = ""
                case .noBaseline:
                    // B-196：首次加载失败 / 库不存在，不当作「过期」；保持 B-188 原有提示
                    model.stale = false
                    model.freshnessNotice = "还没成功加载过（或库文件不存在）：点「刷新列表」重试。"
                case .missingDB:
                    // 异步路径里 current 由 dbManifest 返回、恒非 nil；保留兜底文案
                    model.stale = false
                    model.freshnessNotice = "引用库文件不存在：先到「入藏」页导入 .bib，"
                        + "或到「引用库设置」页指定笔记仓。"
                }
            }
        }
    }

    /// 完整加载 / 手动「刷新列表」/ 写库成功后**局部失效**：一次后台**串行**读
    /// conflicts + health + traces + manifest，回主线程一次性回填。
    /// 不清本页本地开关、定位与其它页面状态（P0 局部失效口径）。
    func reloadAll() {
        guard !running else { return }
        running = true
        stage = "正在读取待处理数据（字段冲突 + 库健康 + 写库留痕）…"
        error = ""
        freshnessNotice = ""
        let db = BibDbFeature.existingDBPath() ?? ""
        let model = self
        DispatchQueue.global(qos: .userInitiated).async {
            let conflicts = BibDbFeature.loadConflicts()
            let health = BibDbFeature.loadHealth(db: db)
            let scan = BibDbFeature.scanEditTraces(db: db)
            let manifest = health.manifest ?? (db.isEmpty ? nil : BibDbFeature.dbManifest(db: db))
            DispatchQueue.main.async {
                model.running = false
                model.stage = ""
                model.loadedOnce = true
                model.loadSeconds = conflicts.seconds
                model.healthSeconds = health.seconds
                var errors: [String] = []
                if let error = conflicts.error { errors.append("字段冲突：" + error) }
                if let error = health.error { errors.append("库健康：" + error) }
                model.error = errors.joined(separator: "\n")
                if conflicts.error == nil {
                    model.conflicts = conflicts
                    model.reviewCache = BibDbFeature.ReviewCacheState(
                        outcome: conflicts, showResolved: model.showResolvedConflicts)
                    // B-196：字段冲突列表已按当前库回填（写库入口的安全依据）→ 解除写入口禁用。
                    // 库健康段是展示数据；它失败不影响写库 sha 闸，保守起见仍在 error 里报。
                    model.stale = false
                }
                if health.error == nil { model.health = health }
                model.traces = scan.traces
                model.tracesTotal = scan.total
                model.processed = BibDbFeature.processedEntries(conflicts: model.conflicts,
                                                                traces: scan.traces)
                if let manifest { model.manifest = manifest }
                model.loadGeneration += 1
                model.reloadFiles()
            }
        }
    }

    /// 写库成功后**局部失效**：只重载 conflicts / health / processed / manifest（一次后台串行）。
    func refreshAfterWrite() {
        reloadAll()
    }

    /// 后台重新生成清单（`bibdb.py report`；**只写清单文件，不改库本体**）。
    func regenerate() {
        guard !running else { return }
        running = true
        stage = "正在重新生成清单…"
        message = ""
        error = ""
        let model = self
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.generateReports()
            DispatchQueue.main.async {
                model.running = false
                model.stage = ""
                model.lastRun = outcome
                model.reloadFiles()
                if let error = outcome.error {
                    model.error = error
                    return
                }
                model.message = "✅ 已重新生成清单：写入/更新 \(outcome.written) 个文件；"
                    + "字段冲突 \(outcome.conflictsRecords) 条记录 / \(outcome.conflictsTotal) 处差异；"
                    + "同作品候选对 \(outcome.pairs) 对。"
            }
        }
    }

    // MARK: - 写：行内三按钮（keep / adopt / defer）

    /// 行内裁决执行。`action` = `keep` / `adopt` / `defer`。
    ///
    /// 安全闸（spec §4.4/§5）：写前带 `--expect-db-sha256`（列表加载时的库 sha）；列表过期时
    /// 引擎退出 2、库不变 → 提示刷新。B-190：写库成功后自动本地 commit（失败只黄字）。
    func performResolve(_ item: BibDbFeature.ConflictItem, action: String) {
        guard !running else { return }
        let expectSHA = conflicts?.dbSha256 ?? ""
        let db = BibDbFeature.existingDBPath() ?? ""
        let label = "C" + String(format: "%03d", item.stableNo) + " " + action
        running = true
        stage = "正在写库（\(Self.actionTitle(action)) C\(String(format: "%03d", item.stableNo))）…"
        error = ""
        message = ""
        lastAction = ""
        versionNotice = nil
        let model = self
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.resolveConflict(
                key: item.recordKey, action: action,
                incomingSha256: item.incomingSha256,
                expectDBsha256: expectSHA)
            var version: BibDbVersionWriteNotice? = nil
            if outcome.error == nil, outcome.written {
                version = BibDbVersionCommit.afterWrite(
                    db: db, action: "裁决 " + label, recordCount: 1,
                    shaBefore: outcome.dbShaBefore, shaAfter: outcome.dbShaAfter)
            }
            DispatchQueue.main.async {
                model.running = false
                model.stage = ""
                if let error = outcome.error {
                    model.error = error
                    return
                }
                model.versionNotice = version
                model.lastAction = Self.resolveLine(item: item, action: action, outcome: outcome)
                model.message = model.lastAction
                model.refreshAfterWrite()
            }
        }
    }

    private static func actionTitle(_ action: String) -> String {
        switch action {
        case "keep": return "保留旧值"
        case "adopt": return "采用新值"
        case "defer": return "待人工"
        default: return action
        }
    }

    private static func resolveLine(item: BibDbFeature.ConflictItem,
                                    action: String,
                                    outcome: BibDbFeature.ResolveOutcome) -> String {
        let no = "C\(String(format: "%03d", item.stableNo))"
        var text = "✅ \(no) \(actionTitle(action))：" + (outcome.already ? "此前已裁决（无变化）" : "已写库")
        text += "；库内记录 `\(item.recordKey)`"
        if !outcome.dbShaAfter.isEmpty { text += "；库 sha " + String(outcome.dbShaAfter.prefix(12)) + "…" }
        if outcome.pendingAfter >= 0 { text += "；待裁决 \(outcome.pendingAfter)" }
        return text
    }

    // MARK: - 写：一键全部采用（dry-run 预览 → 二次确认 → 真写）

    /// 打开「一键全部采用」：先跑 dry-run 预览（库不变），把逐条 old→new 摆给用户看。
    func beginAdoptAll() {
        guard !running else { return }
        let expectSHA = conflicts?.dbSha256 ?? ""
        running = true
        stage = "正在预览「全部采用」（dry-run，不写库）…"
        error = ""
        message = ""
        lastAction = ""
        let model = self
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.adoptAllPending(
                expectDBsha256: expectSHA, dryRun: true)
            DispatchQueue.main.async {
                model.running = false
                model.stage = ""
                if let error = outcome.error {
                    model.error = "一键全部采用：预览失败（库未改）。\n" + error
                    return
                }
                model.adoptPreview = outcome
                model.adoptPreviewSHA = expectSHA
                model.adoptChecked = true
                model.showAdoptSheet = true
            }
        }
    }

    /// 二次确认后真写（单次原子写）；成功后自动本地 commit 一次并刷新列表（C 编号固定、不重排）。
    func performAdoptAll() {
        guard !running, let preview = adoptPreview else { return }
        guard !preview.adopted.isEmpty else { return }
        let expectSHA = adoptPreviewSHA
        let db = BibDbFeature.existingDBPath() ?? ""
        running = true
        stage = "正在全部采用（写库）…"
        error = ""
        versionNotice = nil
        let model = self
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.adoptAllPending(
                expectDBsha256: expectSHA, dryRun: false)
            var version: BibDbVersionWriteNotice? = nil
            if outcome.error == nil, outcome.written {
                version = BibDbVersionCommit.afterWrite(
                    db: db, action: "一键采用 \(outcome.recordCount) 条",
                    recordCount: outcome.recordCount,
                    shaBefore: outcome.dbShaBefore, shaAfter: outcome.dbShaAfter)
            }
            DispatchQueue.main.async {
                model.running = false
                model.stage = ""
                if let error = outcome.error {
                    model.error = "一键全部采用：写库失败（库未改或已回滚）。\n" + error
                    return
                }
                model.versionNotice = version
                var text = "✅ 已全部采用：改 \(outcome.recordCount) 条记录 / "
                    + "\(outcome.fieldCount) 个字段"
                if outcome.supersededCount > 0 {
                    text += "；\(outcome.supersededCount) 条重叠冲突标 superseded"
                }
                if outcome.pendingAfter >= 0 {
                    text += "；待裁决 \(outcome.pendingBefore) → \(outcome.pendingAfter)"
                }
                model.lastAction = text
                model.message = text
                model.showAdoptSheet = false
                model.adoptPreview = nil
                model.adoptChecked = false
                model.refreshAfterWrite()
            }
        }
    }

    // MARK: - 写：编辑裁决 bib

    /// 打开编辑 sheet 并预填库内条目（`query --key --bibtex`；引擎已具备、零改动）。
    func beginEdit(_ item: BibDbFeature.ConflictItem) {
        guard !running else { return }
        editItem = item
        editKey = item.recordKey
        editIncomingSHA = item.incomingSha256
        editTitle = item.briefText
        editBibText = ""
        editPreview = nil
        editPreviewSHA = ""
        editPreviewSignature = ""
        editAllowOverlap = false
        editError = ""
        editMessage = ""
        editLoading = true
        showEditSheet = true
        let key = item.recordKey
        let model = self
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.query(mode: .key, text: key)
            DispatchQueue.main.async {
                model.editLoading = false
                if let error = outcome.error {
                    model.editError = "预填库内条目失败：" + error
                    return
                }
                guard let hit = outcome.hits.first else {
                    model.editError = "库里找不到 key 为 `\(key)` 的记录（列表可能过期，先刷新）。"
                    return
                }
                model.editBibText = hit.bibtex
                if !hit.record.key.isEmpty { model.editKey = hit.record.key }
                if model.editTitle.isEmpty { model.editTitle = hit.record.title }
            }
        }
    }

    private var editSignature: String {
        "\(editKey)|\(editIncomingSHA)|\(editAllowOverlap)|\(editBibText)"
    }

    /// 预览差异：`resolve --action replace --dry-run --json`（库不变）。
    func previewEdit() {
        guard !running, !editLoading else { return }
        guard !editBibText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            editError = "先编辑/粘贴要写入库的 bib 条目。"
            return
        }
        let signature = editSignature
        let expectSHA = conflicts?.dbSha256 ?? ""
        let key = editKey
        let sha = editIncomingSHA
        let text = editBibText
        let allowOverlap = editAllowOverlap
        running = true
        editError = ""
        editMessage = ""
        editPreview = nil
        editPreviewSHA = ""
        editPreviewSignature = ""
        let model = self
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.replaceRecord(
                recordKey: key, bibText: text, incomingSha256: sha,
                expectDBsha256: expectSHA, allowOverlap: allowOverlap, dryRun: true)
            DispatchQueue.main.async {
                model.running = false
                if let error = outcome.error {
                    model.editError = "预览失败（库未改）：\n" + error
                    return
                }
                model.editPreview = outcome
                model.editPreviewSHA = expectSHA
                model.editPreviewSignature = signature
                model.editMessage = "预览（dry-run）：未写库。"
                    + (outcome.changes.isEmpty
                       ? "该 bib 与库内条目无字段差异。"
                       : outcome.changeSummary + "。")
            }
        }
    }

    var editPreviewMatchesInputs: Bool {
        editPreview != nil && !editPreviewSignature.isEmpty && editPreviewSignature == editSignature
    }

    /// 确认应用：`resolve --action replace`（带 --expect-db-sha256；真写 + 自动本地 commit）。
    func performEditApply() {
        guard !running, editPreviewMatchesInputs else { return }
        let expectSHA = conflicts?.dbSha256 ?? ""
        let key = editKey
        let sha = editIncomingSHA
        let text = editBibText
        let allowOverlap = editAllowOverlap
        let db = BibDbFeature.existingDBPath() ?? ""
        let actionLabel = "裁决 " + (editItem.map {
            "C" + String(format: "%03d", $0.stableNo)
        } ?? editKey) + " replace"
        running = true
        stage = "正在写库（编辑裁决 bib）…"
        editError = ""
        versionNotice = nil
        let model = self
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.replaceRecord(
                recordKey: key, bibText: text, incomingSha256: sha,
                expectDBsha256: expectSHA,
                allowOverlap: allowOverlap, dryRun: false)
            var version: BibDbVersionWriteNotice? = nil
            if outcome.error == nil, outcome.written {
                version = BibDbVersionCommit.afterWrite(
                    db: db, action: actionLabel, recordCount: 1,
                    shaBefore: outcome.dbShaBefore, shaAfter: outcome.dbShaAfter)
            }
            DispatchQueue.main.async {
                model.running = false
                model.stage = ""
                if let error = outcome.error {
                    model.editError = "写库失败（库未改或已回滚）：\n" + error
                    return
                }
                model.versionNotice = version
                var text = "✅ 编辑裁决已写库：记录 `\(key)`"
                if !outcome.changes.isEmpty { text += "；" + outcome.changeSummary }
                if !outcome.dbShaAfter.isEmpty {
                    text += "；库 sha " + String(outcome.dbShaAfter.prefix(12)) + "…"
                }
                model.editMessage = text
                model.lastAction = text
                model.message = text
                if !outcome.dbShaAfter.isEmpty { model.conflicts?.dbSha256 = outcome.dbShaAfter }
                model.showEditSheet = false
                model.refreshAfterWrite()
            }
        }
    }

}
