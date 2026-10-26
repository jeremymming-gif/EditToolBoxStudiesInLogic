import SwiftUI
import AppKit

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

    @StateObject var model = BibDbReviewModel()
    /// B-190：版本库状态区（版本状态/手动提交/推送/回退；全部后台 + fail-soft）
    @StateObject private var version = BibDbVersionModel()

    /// 行内三按钮的确认请求（采用新值必须二次确认；保留/待人工只改裁决元数据）
    @State var resolveRequest: ResolveRequest? = nil

    struct ResolveRequest: Identifiable {
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

    @State var scrollTarget: String? = nil
    /// 库健康分类展开态（按 registry；跳转意图会把目标分类展开）
    @State var healthExpanded: Set<String> = []
    /// 已裁决冲突行的展开态（B-202：整行可点；按 `ConflictItem.id`）
    @State var resolvedExpanded: Set<String> = []
    /// 库健康每类的记录搜索词（按 registry；只在前端过滤已加载的 records）
    @State var healthSearch: [String: String] = [:]
    /// 库健康每条 warning 的「再显示 200 条」上限（按 item.id）
    @State var healthLimits: [String: Int] = [:]
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
            每行可「编辑条目」，重复/近重复对可「对照合并」或「确认不同（不再提示）」，
            缺字段/entry_type 类可开批量预览归位。
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

    func openEditor(_ key: String) {
        Logger(store: log)("引用库待处理：打开条目编辑器（\(key)）…\n")
        model.editorTarget = ReviewEditorTarget(key: key)
    }

    func openMerge(_ keys: [String]) {
        Logger(store: log)("引用库待处理：打开对照合并（\(keys.joined(separator: "、"))）…\n")
        model.mergeTarget = ReviewMergeTarget(keys: keys)
    }

    // MARK: - 一键全部采用 + 编辑裁决

    func adoptAllSection(_ conflicts: BibDbFeature.ConflictsOutcome) -> some View {
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

    func editRestoreBar(_ conflicts: BibDbFeature.ConflictsOutcome) -> some View {
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
