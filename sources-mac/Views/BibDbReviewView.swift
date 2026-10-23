import SwiftUI
import AppKit

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

/// 「待裁决」页（批73 B-127 改造）。
///
/// 产品边界（用户 2026-10-21 拍板 P1–P7）：
///   · 逐条三选一直裁：保留旧值 / 采用新值 / 待人工（写库，重启不丢，C 编号固定）；
///   · 「一键全部采用」：高亮警示 + dry-run 逐条预览 + 二次确认，默认**不提供无确认执行**；
///   · 「编辑裁决 bib…」：预填库内条目 → TextEditor 改 → dry-run 预览差异 → 确认写库；
///   · 版本库状态区（B-190）：分支 / 库文件脏否 / 未推送提交数 / 最近 3 条库提交；
///     「提交…」「推送…」「回退到上一个提交…」都要二次确认；备份制已取消。
///   · 所有写操作串行、先预览后确认、写前 `--expect-db-sha256`；写库成功后自动本地 commit。
struct BibDbReviewView: View {
    @ObservedObject var log: LogStore
    @ObservedObject var bibdb: BibDbSettings
    let isActive: Bool

    @StateObject private var model = BibDbReviewModel()
    /// B-190：版本库状态区（版本状态/手动提交/推送/回退；全部后台 + fail-soft）
    @StateObject private var version = BibDbVersionModel()

    /// 行内三按钮的确认请求（采用新值必须二次确认；保留/待人工只改裁决元数据）
    @State private var resolveRequest: ResolveRequest? = nil

    private struct ResolveRequest: Identifiable {
        var item: BibDbFeature.ConflictItem
        var action: String        // keep / adopt / defer
        var id: String { "\(item.id)|\(action)" }
        var isAdopt: Bool { action == "adopt" }
        var title: String {
            let no = "C\(String(format: "%03d", item.stableNo))"
            switch action {
            case "keep": return "\(no) 保留旧值？"
            case "adopt": return "\(no) 采用新稿值？"
            case "defer": return "\(no) 标为待人工？"
            default: return no
            }
        }
        var message: String {
            var lines: [String] = []
            lines.append("库内记录：`\(item.recordKey)` · \(item.title)")
            if isAdopt {
                lines.append("将按新稿值修改以下字段（写库；撤销用版本区「回退到上一个提交…」）：")
                for diff in item.diff {
                    lines.append("· \(diff.name)：「\(diff.oldText)」→「\(diff.newText)」")
                }
                if item.verified {
                    lines.append("⚠️ 这是库内已核实(verified)记录：改的是已出版成品里人工核对过的字段，"
                                 + "请确认新值为准。")
                }
                lines.append("写库成功后自动本地提交库文件（不推送）。")
            } else if action == "keep" {
                lines.append("保留库内已核实旧值（不改字段值）。")
            } else {
                lines.append("标为「待人工」（先从待裁决列表移走，不改字段值）。")
            }
            if !item.hasFingerprint {
                lines.append("⚠ 该条冲突无指纹（旧数据）：将作用于该记录全部待裁决冲突。")
            }
            return lines.joined(separator: "\n")
        }
        var confirmLabel: String {
            switch action {
            case "keep": return "保留旧值"
            case "adopt": return "采用新值（写库）"
            case "defer": return "标为待人工"
            default: return "确定"
            }
        }
    }

    @State private var scrollTarget: String? = nil
    /// 库健康分类展开态（按 registry；跳转意图会把目标分类展开）
    @State private var healthExpanded: Set<String> = []
    /// 已裁决冲突行的展开态（B-202：整行可点；按 `ConflictItem.id`）
    @State private var resolvedExpanded: Set<String> = []
    /// 库健康每类的记录搜索词（按 registry；只在前端过滤已加载的 records）
    @State private var healthSearch: [String: String] = [:]
    /// 库健康每条 warning 的「再显示 200 条」上限（按 item.id）
    @State private var healthLimits: [String: Int] = [:]
    /// 跳转目标分类在库健康加载完成前还不在视图里：记下来，加载完成后补一次定位
    @State private var deferredScrollTarget: String? = nil

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                // B-188 P0：整页一个 LazyVStack（三段列表都懒加载；已处理/已裁决默认一行摘要）
                LazyVStack(alignment: .leading, spacing: 14) {
                    header
                    Divider()
                    pathRows
                    statusBar
                    actionBar
                    versionSection
                    feedback
                    Divider()
                    conflictSection.id("section-conflicts")
                    Divider()
                    healthSection.id("section-health")
                    Divider()
                    BibDbProcessedSection(model: model).id("section-processed")
                    Divider()
                    reportFilesSection
                    Divider()
                    readingGuide
                }
                .padding(20)
                .frame(maxWidth: 1000, alignment: .leading)
            }
            .onChange(of: scrollTarget) { target in
                guard let target else { return }
                DispatchQueue.main.async {
                    withAnimation { proxy.scrollTo(target, anchor: .top) }
                    scrollTarget = nil
                }
            }
        }
        // 三个共享 sheet（sheet 自己起后台任务/持有状态/自己 dismiss；写完回调本页局部失效）
        .sheet(item: $model.editorTarget) { target in
            BibDbEditorSheet(log: log, bibdb: bibdb, recordKey: target.key,
                             onWritten: { model.refreshAfterWrite() })
        }
        .sheet(item: $model.mergeTarget) { target in
            BibDbMergeSheet(log: log, bibdb: bibdb, keys: target.keys,
                            onMerged: { model.refreshAfterWrite() })
        }
        .sheet(item: $model.fieldFixTarget) { target in
            BibDbFieldFixSheet(log: log, bibdb: bibdb, registry: target.registry,
                               onApplied: { model.refreshAfterWrite() })
        }
        .sheet(isPresented: $model.showAdoptSheet) { BibDbAdoptSheet(model: model) }
        .sheet(isPresented: $model.showEditSheet) { BibDbEditSheet(model: model) }
        .onAppear {
            if isActive { activateAndConsume() }
        }
        .onChange(of: isActive) { active in
            if active { activateAndConsume() }
        }
        // 跳转意图：激活时消费；本页已激活时收到新意图也消费（一次性，取走即清）
        .onChange(of: bibdb.reviewJump) { jump in
            guard isActive, jump != nil else { return }
            consumeReviewJump()
        }
        // 首次激活时库健康还在后台加载：加载完成后补一次分类定位（展开态已在 consume 时写入）
        .onChange(of: model.loadGeneration) { _ in
            // B-190：库写成功后版本状态跟着刷新（后台读 git，失败只黄字）
            version.reload()
            guard let target = deferredScrollTarget else { return }
            deferredScrollTarget = nil
            scrollTarget = target
        }
        // 行内三按钮确认框
        .alert(item: $resolveRequest) { request in
            Alert(title: Text(request.title),
                  message: Text(request.message),
                  primaryButton: .cancel(Text("取消")),
                  secondaryButton: request.isAdopt
                      ? .destructive(Text(request.confirmLabel)) {
                            model.performResolve(request.item, action: request.action)
                        }
                      : .default(Text(request.confirmLabel)) {
                            model.performResolve(request.item, action: request.action)
                        })
        }
        // 后台结果写运行日志（与入藏页同一约定：证据留痕）
        .onChange(of: model.message) { text in
            if !text.isEmpty { Logger(store: log)(text + "\n") }
        }
        .onChange(of: model.error) { text in
            if !text.isEmpty { Logger(store: log)("❌ 引用库待处理失败：" + text + "\n") }
        }
        // B-190：写库成功后的版本层提示单独一条（失败是黄字，不属于写库失败）
        .onChange(of: model.versionNotice) { notice in
            guard let notice, !notice.text.isEmpty else { return }
            Logger(store: log)((notice.failed ? "⚠️ " : "") + notice.text + "\n")
        }
    }

    /// 激活：首次加载 / 之后只查新鲜度；同时消费一次性跳转意图（不触发引擎）。
    private func activateAndConsume() {
        model.activate()
        version.reload()
        consumeReviewJump()
    }

    /// 消费一次性跳转意图：只改本页本地开关 + 定位，**不写回任何共享开关**；取走即清。
    private func consumeReviewJump() {
        guard let jump = bibdb.reviewJump else { return }
        model.setShowResolved(jump.showResolved)
        if !jump.registry.isEmpty {
            if jump.registry == "conflicts" {
                scrollTarget = "section-conflicts"
            } else {
                healthExpanded.insert(jump.registry)
                let target = "category-\(jump.registry)"
                // 库健康还没加载时目标区还不存在：先记下，等这次加载完成再滚过去
                if model.health == nil {
                    deferredScrollTarget = target
                } else {
                    scrollTarget = target
                }
            }
        }
        bibdb.reviewJump = nil
    }

    // MARK: - 顶部

    private var header: some View {
        BibDbPageHeader(
            title: "引用库 · 待处理",
            subtitle: """
            本页把「引用库需要人工处理的事」集中到一处，三段：
            ① **字段冲突**——库里已核实条目遇到「同一作品、不同期成品 bib 写法不同」时，库不会静默
            改掉旧值，只把差异留档成 `conflicts`；逐条 keep/adopt/defer、编辑裁决 bib、一键全部采用、
            版本库状态区（提交/推送/回退）也在本页。已裁决默认隐藏，可用开关按**一行摘要**查看
            （C 编号由引擎固定、不重排）。
            ② **库健康**——引擎 `lint --with-records` 的 8 类 warning（重复记录 / 同一作品多记录 /
            缺字段 / entry_type 笔误 / alias / 未核实…）按固定顺序折叠；展开按**受影响记录**逐条列出，
            每行可「编辑条目」，重复/近重复对可「对照合并」，缺字段/entry_type 类可开批量预览归位。
            ③ **已处理**——冲突裁决 + 编辑/合并写库留痕（动作 / key / 时间），默认折叠、点开一行看明细。

            写库纪律：**dry-run 预览 → 二次确认 → 写前校验库 sha → 原子写 → 写库成功后自动本地
            git commit（只提交库文件、绝不自动 push）**；库被外部 CLI 改过时 sha 闸会拒绝写，点
            「刷新列表」后重试；撤销用版本区「回退到上一个提交…」（不 hard reset、不推送）。
            两个数据源：字段冲突来自引擎 `conflicts`，库健康来自引擎 `lint --with-records`（口径独立、
            不互相代替）；已处理的编辑/合并留痕来自记录 `edits[]`（不建 sidecar）。
            """)
    }

    /// B-190：版本库状态区（分支/脏否/未推送/最近 3 条库提交 + 提交/推送/回退）。
    private var versionSection: some View {
        BibDbVersionSection(model: version, onReverted: {
            model.reloadAll()
        })
    }

    private var pathRows: some View {
        VStack(alignment: .leading, spacing: 4) {
            BibDbInfoRow(label: "笔记仓",
                         value: bibdb.notesRepoPath.trimmingCharacters(in: .whitespacesAndNewlines)
                             .isEmpty
                             ? (BibDbFeature.detectedNotesRoot() ?? "（未找到）")
                             : bibdb.notesRepoPath,
                         monospaced: true, wraps: true)
            BibDbInfoRow(label: "清单目录",
                         value: notesDir ?? "（找不到私有笔记仓：请到「引用库设置」页指定）",
                         monospaced: true,
                         color: notesDir == nil ? .red : .secondary,
                         wraps: true)
            BibDbInfoRow(label: "库文件",
                         value: BibDbFeature.dbPath() ?? "（找不到私有笔记仓）",
                         monospaced: true,
                         color: (BibDbFeature.existingDBPath() == nil) ? .red : .secondary,
                         wraps: true)
        }
    }

    // MARK: - 状态条

    /// 状态条一句话（Wave1 纯逻辑，口径写死在 CONTRACT.md）：
    /// `待处理 N（字段冲突 P ｜ 库健康 18 条/涉及约 355 条记录）｜ 已处理 M ｜ 库 sha 前12位…`
    private var statusText: String {
        BibDbFeature.pendingStatusText(
            conflictPending: model.conflicts?.pending ?? 0,
            health: model.health?.lint,
            processedCount: model.processed.count,
            dbSha256: model.manifest?.sha256 ?? model.conflicts?.dbSha256 ?? "")
    }

    private var pendingTotal: Int {
        (model.conflicts?.pending ?? 0) + (model.health?.warningCount ?? 0)
    }

    private var statusBar: some View {
        HStack(alignment: .center, spacing: 14) {
            Text(statusText)
                .font(.callout).bold().monospacedDigit()
                .foregroundStyle(pendingTotal > 0 ? Color.orange : Color.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if model.running || model.checkingFreshness {
                ProgressView().controlSize(.small)
                Text(model.running ? model.stage : "正在检查库是否变化…")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(Color.secondary.opacity(0.05))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.15)))
    }

    private var actionBar: some View {
        HStack(spacing: 12) {
            Button("刷新列表") {
                Logger(store: log)("引用库待处理：刷新列表（字段冲突 + 库健康 + 写库留痕）…\n")
                model.reloadAll()
            }
            .disabled(model.running)
            .help("重新读 conflicts + lint --with-records + 写库留痕（外部 CLI 改过库后必须刷新，"
                  + "写前 sha 校验才不会白拦）")
            Button("重新生成清单") {
                Logger(store: log, startedAt: Date())("引用库待处理清单：重新生成…\n")
                model.regenerate()
            }
            .disabled(model.running)
            .help("跑 bibdb.py report：只重写清单文件，不改库本体（同一份库重复生成逐字节相同）")
            Button("打开清单") { openPrimary() }
                .disabled(model.primaryFile == nil)
            Button("打开清单文件夹") { openFolder() }
                .disabled(!folderExists)
            // B-188：本页本地开关（不再是共享 bibdb.showResolvedConflicts；切换 O(1) 只换缓存态）
            Toggle(BibDbFeature.ConflictListState.toggleTitle,
                   isOn: Binding(get: { model.showResolvedConflicts },
                                 set: { model.setShowResolved($0) }))
                .toggleStyle(.checkbox)
                .font(.caption)
                .disabled(model.running)
                .help("默认只列待裁决；打开后已裁决按一行摘要显示（点开明细，C 编号固定不重排）")
            Spacer()
        }
    }

    private var feedback: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !model.freshnessNotice.isEmpty {
                BibDbStatusLine(kind: .warning, text: model.freshnessNotice)
            }
            if !model.error.isEmpty {
                BibDbStatusLine(kind: .error, text: model.error)
            }
            if !model.message.isEmpty {
                BibDbStatusLine(kind: .ok, text: model.message)
            }
            // B-190：写库成功后版本层的结果（commit 失败只黄字，不混进「写库失败」）
            if let version = model.versionNotice, !version.text.isEmpty {
                BibDbStatusLine(kind: version.failed ? .warning : .idle, text: version.text)
            }
            if let run = model.lastRun, run.error == nil {
                Text("清单生成用时 \(BibDbUI.seconds(run.seconds))；库：\(run.dbPath)")
                    .font(.caption).foregroundStyle(.tertiary).textSelection(.enabled)
            }
            if let state = model.listState {
                Text("列表加载用时 \(BibDbUI.seconds(model.loadSeconds))；\(state.feedbackText)")
                    .font(.caption).foregroundStyle(.tertiary)
            } else if !model.running && model.loadedOnce {
                Text("还没加载到字段冲突列表：点上面「刷新列表」重试。")
                    .font(.caption).foregroundStyle(.tertiary)
            } else if !model.running {
                Text("首次进入本页会自动加载一次；之后切页只做新鲜度检查（不重复起引擎）。")
                    .font(.caption).foregroundStyle(.tertiary)
            }
            if let health = model.health, health.seconds > 0 {
                Text("库健康用时 \(BibDbUI.seconds(health.seconds))；写库留痕 \(model.tracesTotal) 条。")
                    .font(.caption).foregroundStyle(.tertiary)
            }
        }
    }

    // MARK: - ① 字段冲突

    private var conflictSection: some View {
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

    // MARK: - ② 库健康

    private var healthSection: some View {
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
                 + "记录多时用搜索/「再显示 200 条」；重复/近重复对可打开「对照合并」，**绝不自动合并**）。")
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
                    Text("可逐对「对照合并」").font(.caption2).foregroundStyle(.tertiary)
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

    private func openEditor(_ key: String) {
        Logger(store: log)("引用库待处理：打开条目编辑器（\(key)）…\n")
        model.editorTarget = ReviewEditorTarget(key: key)
    }

    private func openMerge(_ keys: [String]) {
        Logger(store: log)("引用库待处理：打开对照合并（\(keys.joined(separator: "、"))）…\n")
        model.mergeTarget = ReviewMergeTarget(keys: keys)
    }

    // MARK: - 一键全部采用 + 编辑裁决

    private func adoptAllSection(_ conflicts: BibDbFeature.ConflictsOutcome) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider()
            HStack(spacing: 10) {
                Text("批量操作").font(.callout).bold()
                Spacer()
            }
            // 高亮警示放在按钮**前面**（用户拍板 P3：一键采用必须高亮提醒）
            VStack(alignment: .leading, spacing: 3) {
                Text("⚠️「一键全部采用」会对所有待裁决冲突按新稿值批量改库：")
                    .font(.caption).foregroundStyle(.red).bold()
                Text("这是批量修改学术事实（其中 \(conflicts.verifiedPendingCount) 条属于库内已核实记录），"
                     + "执行前必须先看 dry-run 逐条预览、再二次确认；默认不提供无确认执行。"
                     + "写库成功后自动本地提交（不推送），撤销用版本区「回退到上一个提交…」。")
                    .font(.caption).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.red.opacity(0.06))
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.red.opacity(0.25)))
            HStack(spacing: 12) {
                Button("一键全部采用…") { model.beginAdoptAll() }
                    .buttonStyle(.borderedProminent)
                    .tint(.orange)
                    .disabled(model.running || model.stale || conflicts.pending == 0)
                    .help(model.stale
                          ? "库已变化：先点上方「刷新列表」重新加载（写入口临时禁用）"
                          : "先 dry-run 预览（逐条 old→new），二次确认后才真写")
                Button("编辑裁决 bib…") { beginEditForFirstPending(conflicts) }
                    .disabled(model.running || model.stale || conflicts.pendingItems.isEmpty)
                    .help(model.stale
                          ? "库已变化：先点上方「刷新列表」重新加载（写入口临时禁用）"
                          : "预填库内条目 → TextEditor 编辑 → dry-run 预览差异 → 确认写库")
                Spacer()
                Text("待裁决 \(conflicts.pending) 条")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func editRestoreBar(_ conflicts: BibDbFeature.ConflictsOutcome) -> some View {
        // 行内「编辑裁决 bib…」按钮：每行默认没有单独按钮（避免列表过宽），
        // 这里提供按 C 编号选择条目的入口（选中后打开同一个编辑 sheet）。
        VStack(alignment: .leading, spacing: 6) {
            if !conflicts.pendingItems.isEmpty {
                Text("「编辑裁决 bib…」也可以先在上面列表里选一条：")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }

    private func beginEditForFirstPending(_ conflicts: BibDbFeature.ConflictsOutcome) {
        guard let item = conflicts.pendingItems.first else { return }
        Logger(store: log)("引用库待处理：打开编辑裁决 bib（C"
                           + String(format: "%03d", item.stableNo) + "）…\n")
        model.beginEdit(item)
    }

    // MARK: - 清单文件（只读；保留原有能力）

    private var reportFilesSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("清单文件（只读；人工阅读用）").font(.callout).bold()
            if model.files.isEmpty {
                BibDbStatusLine(kind: .warning,
                                text: "找不到私有笔记仓，无法定位清单目录。请到「引用库设置」页指定笔记仓路径。")
            } else {
                HStack(spacing: 14) {
                    ForEach(model.files) { file in
                        HStack(spacing: 6) {
                            Image(systemName: file.exists ? "doc.text.fill" : "doc")
                                .foregroundStyle(file.exists ? Color.accentColor : Color.secondary)
                            Text(file.name).font(.caption)
                            if file.exists {
                                Text(BibDbUI.size(file.bytes)).font(.caption2)
                                    .foregroundStyle(.tertiary)
                            }
                            Button("打开") { open(file) }
                                .disabled(!file.exists)
                                .controlSize(.small)
                        }
                    }
                    Spacer()
                }
            }
        }
    }

    private var readingGuide: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("怎么读这一页").font(.callout).bold()
            Text("列表顺序 = 引擎的「期 → 篇 → C 编号」；C 编号按全部冲突（含已裁决）固定分配——"
                 + "裁决只让条目变成灰显，后面的编号不会改，你按 Cxxx 记的决定不会错位。")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("默认「保留旧值」——已核实旧值是已出版成品里的人工核对结果；只有新稿值明显更完整/更准确"
                 + "（旧值为空、旧值是笔误）时才采用新值；拿不准就标「待人工」。")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("所有写动作：预览 → 确认 → 写前 sha 校验 → 原子写 → 写库成功后自动本地 git commit"
                 + "（只提交库文件、绝不自动推送）；库被外部 CLI 改过时 sha 校验会拒绝写，点"
                 + "「刷新列表」后重试。整库回退用版本区「回退到上一个提交…」（新 commit，不 hard reset）。")
                .font(.caption).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 动作

    private var notesDir: String? {
        guard let notes = BibDbFeature.notesRoot() else { return nil }
        return PathKit.join(notes, "bib_db")
    }

    private var folderExists: Bool {
        guard let dir = notesDir else { return false }
        return PathKit.isDirectory(dir)
    }

    private func openFolder() {
        guard let dir = notesDir else { return }
        Logger(store: log)(bibDbOpenInFinder(dir))
    }

    private func openPrimary() {
        guard let file = model.primaryFile else { return }
        open(file)
    }

    private func open(_ file: BibDbFeature.ReportFile) {
        Logger(store: log)(bibDbOpenInFinder(file.path))
    }
}
