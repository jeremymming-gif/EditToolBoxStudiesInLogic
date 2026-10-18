import SwiftUI
import AppKit

/// 「待裁决」页的界面状态：只读清单文件 + 「重新生成清单」。
final class BibDbReviewModel: ObservableObject {
    @Published var files: [BibDbFeature.ReportFile] = []
    @Published var running = false
    @Published var message: String = ""
    @Published var error: String = ""
    @Published var lastRun: BibDbFeature.ReportOutcome? = nil

    /// 只读地刷新三个清单文件的存在/大小/时间（不跑引擎，主线程即可）。
    func reloadFiles() {
        files = BibDbFeature.reportFiles()
    }

    /// 后台重新生成清单（`bibdb.py report`；**只写清单文件，不改库本体**）。
    func regenerate() {
        guard !running else { return }
        running = true
        message = ""
        error = ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let outcome = BibDbFeature.generateReports()
            DispatchQueue.main.async {
                guard let self else { return }
                self.running = false
                self.lastRun = outcome
                self.reloadFiles()
                if let error = outcome.error {
                    self.error = error
                    return
                }
                self.message = "✅ 已重新生成清单：写入/更新 \(outcome.written) 个文件；"
                    + "字段冲突 \(outcome.conflictsRecords) 条记录 / \(outcome.conflictsTotal) 处差异；"
                    + "同作品候选对 \(outcome.pairs) 对。"
            }
        }
    }

    /// 清单文件里第一个存在的（「打开清单」按钮用）
    var primaryFile: BibDbFeature.ReportFile? {
        files.first { $0.exists }
    }

    var conflictFile: BibDbFeature.ReportFile? {
        files.first { $0.name == "CONFLICTS.md" }
    }

    var duplicatesFile: BibDbFeature.ReportFile? {
        files.first { $0.name == "DUPLICATES.md" }
    }
}

/// 「待裁决」页：只读展示 `<笔记仓>/bib_db/` 下的待裁决清单，并可一键重新生成。
///
/// 产品边界（用户已定案）：**只读清单、不自动改库**。库里的字段冲突（conflicts）与
/// 「同作品不同 key」都是机器留档；清单把它们翻成中文、逐条可勾选，供编辑裁决。
/// 这一页只做三件事：显示文件状态、重新生成清单（只写 .md/.csv）、在访达/编辑器里打开。
struct BibDbReviewView: View {
    @ObservedObject var log: LogStore
    @ObservedObject var bibdb: BibDbSettings
    let isActive: Bool

    @StateObject private var model = BibDbReviewModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                BibDbPageHeader(
                    title: "引用库 · 待裁决",
                    subtitle: """
                    引用库里**已核实条目**遇到「同一作品、不同期成品 bib 字段写法不同」时，库不会
                    静默改掉旧值，只把差异留档成 `conflicts`。这一页把待裁决清单（中文、逐条可勾选）
                    的状态显示出来，并可以一键重新生成。
                    ⚠️ **这一页只读清单、不自动改库**：清单只写 `CONFLICTS.md` / `DUPLICATES.md` /
                    `duplicates.csv` 三个文件，`bibliography.jsonl` 一个字节都不动。裁决方法写在
                    清单末尾「裁决方法」一节，由维护者按清单手工改库。
                    """)

                Divider()

                pathRows

                HStack(spacing: 12) {
                    Button("重新生成清单") { regenerate() }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.running)
                        .help("跑 bibdb.py report：只重写三个清单文件，不改库本体（同一份库重复生成逐字节相同）")
                    Button("打开清单") { openPrimary() }
                        .disabled(model.primaryFile == nil)
                    Button("打开清单文件夹") { openFolder() }
                        .disabled(!folderExists)
                    Button("刷新") { model.reloadFiles() }
                        .disabled(model.running)
                    if model.running {
                        ProgressView().controlSize(.small)
                        Text("正在生成清单…").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                if !model.error.isEmpty {
                    BibDbStatusLine(kind: .error, text: model.error)
                }
                if !model.message.isEmpty {
                    BibDbStatusLine(kind: .ok, text: model.message)
                }
                if let run = model.lastRun, run.error == nil {
                    Text("用时 \(BibDbUI.seconds(run.seconds))；库：\(run.dbPath)")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .textSelection(.enabled)
                }

                Divider()

                fileRows

                Divider()

                readingGuide
            }
            .padding(20)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .onAppear {
            if isActive { model.reloadFiles() }
        }
        .onChange(of: isActive) { active in
            if active { model.reloadFiles() }
        }
        // 结果话术由 Model 回填（后台线程 → 主线程）；统一在这里落运行日志，
        // 不在按钮回调里等待（那样要轮询后台结果，反而更绕）。
        .onChange(of: model.message) { message in
            if !message.isEmpty { Logger(store: log)(message + "\n") }
        }
        .onChange(of: model.error) { error in
            if !error.isEmpty { Logger(store: log)("❌ 重新生成待裁决清单失败：" + error + "\n") }
        }
    }

    // MARK: - 路径

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

    private var notesDir: String? {
        guard let notes = BibDbFeature.notesRoot() else { return nil }
        return PathKit.join(notes, "bib_db")
    }

    private var folderExists: Bool {
        guard let dir = notesDir else { return false }
        return PathKit.isDirectory(dir)
    }

    // MARK: - 清单文件

    private var fileRows: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("清单文件（只读）").font(.callout).bold()
            if model.files.isEmpty {
                BibDbStatusLine(kind: .warning,
                                text: "找不到私有笔记仓，无法定位清单目录。请到「引用库设置」页指定笔记仓路径。")
            } else {
                ForEach(model.files) { file in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Image(systemName: file.exists ? "doc.text.fill" : "doc")
                            .foregroundStyle(file.exists ? Color.accentColor : Color.secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(file.name).font(.callout)
                            Text(file.exists
                                 ? "\(BibDbUI.size(file.bytes))；修改时间 \(BibDbUI.dateText(file.modified))"
                                 : "（还没生成）")
                                .font(.caption)
                                .foregroundStyle(file.exists ? Color.secondary : Color.orange)
                        }
                        Spacer()
                        Button("打开") { open(file) }
                            .disabled(!file.exists)
                    }
                    .padding(6)
                    .background(Color.secondary.opacity(0.04))
                    .overlay(RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.secondary.opacity(0.15)))
                }
                if model.conflictFile?.exists != true {
                    Text("提示：`CONFLICTS.md` 还没有生成 —— 点上面的「重新生成清单」；"
                         + "库刚导入过时尤其要重算一次。")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
        }
    }

    private var readingGuide: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("怎么读这三个文件").font(.callout).bold()
            Text("**CONFLICTS.md**：字段冲突待裁决清单（按期 → 篇 → 条目；每条列「已核实旧值 vs 新稿值」"
                 + "与建议，带勾选框）。默认「保留旧值」——已核实旧值是已出版成品里的人工核对结果。")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("**DUPLICATES.md**：同作品不同 key 的候选对（前 200 对，按相似度降序）。"
                 + "**仅供参考，工具不会自动合并**（误合并学术事实比重复更糟）。"
                 + "全量见 **duplicates.csv**（Excel 可直接打开）。")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("同一份库重复生成清单，三个文件**逐字节相同**（清单里不含生成时间）——"
                 + "所以重新生成后 git 里没有 diff 就说明库没变。")
                .font(.caption).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 动作

    private func regenerate() {
        Logger(store: log, startedAt: Date())("引用库待裁决清单：重新生成…\n")
        model.regenerate()
    }

    private func openPrimary() {
        guard let file = model.primaryFile else { return }
        open(file)
    }

    private func open(_ file: BibDbFeature.ReportFile) {
        Logger(store: log)(bibDbOpenInFinder(file.path))
    }

    private func openFolder() {
        guard let dir = notesDir else { return }
        Logger(store: log)(bibDbOpenInFinder(dir))
    }
}
