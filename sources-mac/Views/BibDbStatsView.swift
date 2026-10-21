import SwiftUI
import AppKit

/// 「库概况」页的界面状态：一次 stats + lint 的结果。
/// 与其他页同理：没有 `@State` 宏（本机只有 Command Line Tools），状态用 ObservableObject。
final class BibDbStatsModel: ObservableObject {
    @Published var loading = false
    @Published var overview: BibDbFeature.Overview? = nil

    /// 世代号：连续点「刷新」时只认最后一次（旧结果回来不许覆盖新结果）
    private var generation = 0

    /// 后台读库概况（stats + lint），结果回主线程。**绝不在主线程跑子进程**（案例 126）。
    func reload() {
        generation += 1
        let gen = generation
        loading = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = BibDbFeature.overview()
            DispatchQueue.main.async {
                guard let self, gen == self.generation else { return }
                self.overview = result
                self.loading = false
            }
        }
    }
}

/// 「库概况」页：引用库里有多少条、多少期、最近一次更新、待裁决冲突、lint 有没有 error。
///
/// 数据全部来自引擎（`bibdb.py stats --json` / `lint --json`），这一页只负责显示；
/// 「打开库文件夹」是给编辑去核对/手工排查用的入口（**本页不提供任何写库动作**）。
struct BibDbStatsView: View {
    @ObservedObject var log: LogStore
    @ObservedObject var bibdb: BibDbSettings
    /// 当前是不是显示着这一页：切到本页才去读库（5 页同时存在，见 `MainWindow` 的 ZStack）
    let isActive: Bool

    @StateObject private var model = BibDbStatsModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                BibDbPageHeader(
                    title: "引用库 · 库概况",
                    subtitle: """
                    编辑部引用库（`bib_db`）把**已核实的参考文献**沉淀成可查、可审计的库：
                    以后核实文献**先查库**，命中已核实条目的就不再调检索 API / AI（省钱 + 体例统一）。
                    `verified` 只认「往期已出版期的成品 bib」或人工核实，**AI 结果不会直接入库**。
                    """)

                Divider()

                HStack(spacing: 10) {
                    Button("刷新") { model.reload() }
                        .disabled(model.loading)
                    if model.loading {
                        ProgressView().controlSize(.small)
                        Text("正在读取库概况（stats + lint）…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("打开库文件夹") { openLibraryFolder() }
                        .disabled(libraryFolder == nil)
                        .help("在访达里打开 <笔记仓>/bib_db/（库文件与待裁决清单都在这里）")
                    Button("打开库文件") { openLibraryFile() }
                        .disabled(overview?.dbExists != true)
                }

                content
            }
            .padding(20)
            .frame(maxWidth: 920, alignment: .leading)
        }
        // 切到本页才读库：5 个页面在启动时就全建好了（切页不丢状态），
        // 若用 onAppear 会在**启动时**就跑两次 Python（stats + lint），没有必要。
        .onAppear { if isActive { model.reload() } }
        .onChange(of: isActive) { active in
            if active { model.reload() }
        }
    }

    // MARK: - 内容

    @ViewBuilder
    private var content: some View {
        if let overview = model.overview {
            VStack(alignment: .leading, spacing: 8) {
                if let error = overview.error {
                    BibDbStatusLine(kind: overview.dbExists ? .error : .warning, text: error)
                }
                if let stats = overview.stats {
                    statsRows(stats)
                } else {
                    BibDbInfoRow(label: "库文件", value: overview.dbPath,
                                 monospaced: true, wraps: true)
                    BibDbInfoRow(label: "笔记仓", value: notesText)
                }
                Divider()
                lintRows(overview)
                Divider()
                footerRows(overview)
            }
        } else if model.loading {
            Text("正在读取…").foregroundStyle(.secondary)
        } else {
            BibDbStatusLine(kind: .idle, text: "点「刷新」读取库概况。")
        }
    }

    @ViewBuilder
    private func statsRows(_ stats: BibDbFeature.Stats) -> some View {
        BibDbInfoRow(label: "库文件", value: stats.path.isEmpty ? model.overview?.dbPath ?? "" : stats.path,
                     monospaced: true, wraps: true)
        BibDbInfoRow(label: "条目", value: "\(stats.records) 条"
                     + "（已核实 \(stats.verified) / 未核实 \(stats.unverified)）")
        BibDbInfoRow(label: "期数", value: "\(stats.issues) 期（\(stats.files) 份成品 bib，"
                     + "\(stats.sources) 条来源记录）")
        BibDbInfoRow(label: "最近更新", value: stats.updatedNewest.isEmpty ? "—" : stats.updatedNewest)
        BibDbInfoRow(label: "冲突", value: "\(stats.conflictsRecords) 条记录 / "
                     + "\(stats.conflictsTotal) 处字段差异"
                     + (stats.conflictsTotal == 0 ? "（无待裁决项）" : "（到「待裁决」页看清单）"),
                     color: stats.conflictsTotal == 0 ? .primary : .orange)
        if !stats.issueList.isEmpty {
            let first = stats.issueList.first ?? ""
            let last = stats.issueList.last ?? ""
            BibDbInfoRow(label: "期范围", value: first == last ? first : "\(first) … \(last)")
        }
        BibDbInfoRow(label: "库大小", value: "\(BibDbUI.size(stats.bytes))"
                     + (stats.sha256.isEmpty ? "" : "；sha256 \(stats.sha256.prefix(12))…"),
                     monospaced: true)
    }

    @ViewBuilder
    private func lintRows(_ overview: BibDbFeature.Overview) -> some View {
        if let lint = overview.lint {
            BibDbInfoRow(label: "lint 结果",
                         value: "error \(lint.errorCount) 项 / warning \(lint.warningCount) 项"
                         + (lint.ok ? "（库可用）" : "——库有 error，多为规范化规则升级后未迁移"),
                         color: lint.ok ? (lint.warningCount > 0 ? .orange : .green) : .red)
            if !lint.ok {
                // 可操作说明（B-115）：库 error 不是「手工改坏了」这么吓人，最常见原因是
                // 引擎的规范化规则升级后**真实库还没迁移**；给出确切的修复命令，别让人猜。
                Text("修复：在终端运行 `python3 Engine/bibdb.py rehash --db <库> --apply`，"
                     + "再点「刷新」；逐条 error 见下方列表。")
                    .font(.caption)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
                    .padding(.leading, 118)
            }
            BibDbInfoRow(label: "同作品多记录", value: "\(lint.nearDuplicates) 对"
                         + "（经典文献跨期重复引用是常态；仅供参考，不自动合并）")
            ForEach(Array(lint.errors.prefix(6).enumerated()), id: \.offset) { item in
                Text("✗ " + item.element)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
                    .padding(.leading, 118)
            }
            if lint.errors.count > 6 {
                Text("……另有 \(lint.errors.count - 6) 条 error，跑 `bibdb.py lint` 看全部。")
                    .font(.caption).foregroundStyle(.red).padding(.leading, 118)
            }
            ForEach(Array(lint.warnings.prefix(4).enumerated()), id: \.offset) { item in
                Text("! " + item.element)
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .textSelection(.enabled)
                    .padding(.leading, 118)
            }
            if lint.warnings.count > 4 {
                Text("……另有 \(lint.warnings.count - 4) 条 warning（不影响使用）。")
                    .font(.caption).foregroundStyle(.secondary).padding(.leading, 118)
            }
        } else if overview.stats != nil {
            Text("lint：没读到结果（库文件存在但 lint 失败，见上方错误）。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func footerRows(_ overview: BibDbFeature.Overview) -> some View {
        BibDbInfoRow(label: "笔记仓", value: notesText, monospaced: true, wraps: true)
        if let tool = overview.toolPath {
            BibDbInfoRow(label: "引擎脚本", value: tool, monospaced: true, wraps: true)
        } else {
            BibDbInfoRow(label: "引擎脚本", value: "未找到 Engine/bibdb.py（请用 ./build.sh 重建 app）",
                         monospaced: true, color: .red, wraps: true)
        }
        Text("读库用时 \(BibDbUI.seconds(overview.seconds))。"
             + "这一页只读：不写库、不改清单。要生成/更新待裁决清单请到「待裁决」页。")
            .font(.caption)
            .foregroundStyle(.tertiary)
    }

    // MARK: - 动作

    /// 当前实际使用的库文件（用于两个「打开…」按钮的可用性）
    private var overview: BibDbFeature.Overview? { model.overview }

    private var libraryFolder: String? {
        guard let path = overview?.dbPath, !path.isEmpty else {
            // 还没读出来时给一个更宽松的默认：按当前解析到的笔记仓算
            guard let db = BibDbFeature.dbPath() else { return nil }
            return (db as NSString).deletingLastPathComponent
        }
        return (path as NSString).deletingLastPathComponent
    }

    private var notesText: String {
        if let notes = overview?.notesPath { return notes }
        return BibDbFeature.notesRoot() ?? "（未找到私有笔记仓）"
    }

    private func openLibraryFolder() {
        guard let folder = libraryFolder else { return }
        Logger(store: log)(bibDbOpenInFinder(folder))
    }

    private func openLibraryFile() {
        guard let path = overview?.dbPath, !path.isEmpty else { return }
        Logger(store: log)(bibDbOpenInFinder(path))
    }
}
