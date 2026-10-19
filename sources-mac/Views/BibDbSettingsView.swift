import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// 「引用库设置」页：核实成本闸门 + 查库开关 + 引用库（笔记仓）路径。
///
/// 为什么这些设置单独一页（而不是塞进「设置」页）
/// --------------------------------------------
/// 它们**不是 AI 连接参数**：核实上限、先算账、全量核实、查不查库，回答的是
/// 「这次核实的成本与范围」；引用库路径回答的是「库在哪」。用户已定案：引用库
/// 作为与「单篇稿件 / 整期装配」平行的顶级分组，它的设置也放在这个分组里。
///
/// 生效链路（只有一条）：本页写 `UserDefaults` → `ConvertTab.run()` 在点击转换时
/// **现取** → `ConvertFeature.arguments(_:)` 拼进 argv → 引擎 `convert.py` 的
/// `--verify-max / --verify-plan / --verify-all-refs / --no-bibdb / --bibdb-path`。
/// 改完**不需要重启**，下一次转换就生效（与 AI 设置那组旋钮同一口径）。
/// 「先算账」的语义要说清：引擎在打印核实计划后**直接 exit 0**，本次不生成任务书、
/// 不检索、不调用 AI —— 所以要真正核实必须把它关掉再跑一次。
struct BibDbSettingsView: View {
    @ObservedObject var log: LogStore
    @ObservedObject var bibdb: BibDbSettings

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                BibDbPageHeader(
                    title: "引用库 · 设置",
                    subtitle: """
                    核实成本闸门（上限 / 先算账 / 全量核实）、「核实前先查引用库」开关，
                    以及引用库（私有笔记仓）路径。这些值存在本机 `UserDefaults`，
                    由「初排转换」页在**运行时现取**并拼进引擎 argv：改完下一次转换立即生效，不必重启。
                    ⚠️ 它们只在（a）两段式协同第一段（文献核实任务书）或（b）勾选
                    「用 AI 处理参考文献（直连 API，会产生费用）」时才会被传给引擎 ——
                    本页设置不会自己发起核实。
                    """)

                Divider()
                verifySection
                Divider()
                lookupSection
                Divider()
                notesSection
                Divider()
                effectiveSection
            }
            .padding(20)
            .frame(maxWidth: 940, alignment: .leading)
        }
    }

    // MARK: - ① 核实成本闸门

    private var verifySection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("① 核实成本闸门").font(.callout).bold()
            Text("第 128 条起默认只核实**正文引用到**的条目，并对条数设上限："
                 + "超过上限且没开「允许全量核实」时，引擎会**先退出 2** 并打印范围与预计检索/AI 次数。"
                 + "看到退出 2 不是程序错误，是让你先看账再决定。")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("核实上限").frame(width: 108, alignment: .trailing)
                TextField("120", text: verifyMaxBinding)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                    .frame(width: 90)
                Text("条（0 = 不限；留空 = 引擎默认 \(BibDbSettings.defaultVerifyMax)）")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Spacer()
            }
            if let hint = bibdb.verifyMaxHint {
                Text("⚠ " + hint)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.leading, 118)
            }

            Toggle("核实前先算账（--verify-plan：只算账，不花钱）",
                   isOn: verifyPlanBinding)
                .toggleStyle(.checkbox)
            Text("开了它，转换照常写出产物，但**不生成任务书、不检索、不调用 AI**，"
                 + "只打印「本次会核实多少条、预计检索/AI 多少次、约多少钱」后退出 0。"
                 + "看完成本账**关掉它**再跑一次才会真正核实。")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.leading, 18)
                .fixedSize(horizontal: false, vertical: true)

            Toggle("允许全量核实（--verify-all-refs：越过上限闸门）",
                   isOn: verifyAllRefsBinding)
                .toggleStyle(.checkbox)
            Text("越过上限闸门（正文 `\\nocite{*}` 本身也要求全量）。**默认不开**："
                 + "全量核实会为没被正文引用的条目也花检索/AI 的钱（期 87 实测：376 条 $1.8+）。")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.leading, 18)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var verifyPlanBinding: Binding<Bool> {
        Binding(get: { bibdb.verifyPlan }, set: { bibdb.updateVerifyPlan($0) })
    }

    private var verifyAllRefsBinding: Binding<Bool> {
        Binding(get: { bibdb.verifyAllRefs }, set: { bibdb.updateVerifyAllRefs($0) })
    }

    private var verifyMaxBinding: Binding<String> {
        Binding(get: { bibdb.verifyMaxText }, set: { bibdb.updateVerifyMax($0) })
    }

    // MARK: - ② 查引用库

    private var lookupSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("② 核实前先查引用库（省钱 + 体例统一）").font(.callout).bold()
            Toggle("核实前先查引用库（默认开）", isOn: lookupBinding)
                .toggleStyle(.checkbox)
            Text("开着：执行核实时若引用库存在，先按 DOI / 题名 / key 查库 —— "
                 + "命中**已核实**条目的键不调检索 API、不调 AI（仍逐条写进任务书「五、库内已核实」，"
                 + "字段差异只提示、不改 `reference.bib`）。"
                 + "关掉 = 本次一律不查库（传 `--no-bibdb`，输出与接入引用库之前逐字节相同）。")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.leading, 18)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                BibDbInfoRow(label: "库文件",
                             value: BibDbFeature.dbPath() ?? "（找不到私有笔记仓，见下面 ③）",
                             monospaced: true,
                             color: BibDbFeature.existingDBPath() == nil ? .orange : .secondary,
                             wraps: true)
                Button("打开库文件夹") { openLibraryFolder() }
                    .disabled(BibDbFeature.dbPath() == nil)
            }
        }
    }

    private var lookupBinding: Binding<Bool> {
        Binding(get: { bibdb.lookupEnabled }, set: { bibdb.updateLookup($0) })
    }

    // MARK: - ③ 引用库路径（笔记仓）

    private var notesSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("③ 引用库路径（私有笔记仓）").font(.callout).bold()
            BibDbInfoRow(label: "自动探测",
                         value: BibDbFeature.detectedNotesRoot() ?? "（未探测到）",
                         monospaced: true,
                         color: BibDbFeature.detectedNotesRoot() == nil ? .orange : .secondary,
                         wraps: true)

            HStack(spacing: 8) {
                Text("手动指定").frame(width: 108, alignment: .trailing)
                TextField("留空 = 自动探测；例如 ~/Documents/GitHub/EditToolBox-notes",
                          text: notesPathBinding)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                Button("浏览…") { browseNotes() }
                Button("恢复自动探测") { bibdb.updateNotesRepoPath("") }
                    .disabled(bibdb.notesRepoPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if let valid = bibdb.manualNotesPathValid {
                if valid {
                    BibDbStatusLine(kind: .ok, text: "手动路径有效（它的 `bib_db/` 就是引用库位置）。")
                } else {
                    BibDbStatusLine(kind: .error,
                                    text: "手动路径不存在或不是文件夹：引擎会**回落自动探测**"
                                        + "（不会写到别处、也不会静默新建目录）。")
                }
            } else {
                Text("未手动指定：用自动探测到的笔记仓。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 118)
            }
            Text("手动路径存 `UserDefaults`（键 `notesRepoPath`），外壳会把它作为环境变量 "
                 + "`SLCN_NOTES` 传给它启动的所有引擎子进程（引用库、核实链路都认这一个变量）；"
                 + "「初排转换」在库文件存在时还会显式传 `--bibdb-path`（`ps` 里能看见这次用的是哪个库）。"
                 + "笔记仓目录里需要有 `bib_db/`（引用库）—— 引擎要求该路径真实存在，"
                 + "指错时不会静默写到别处。")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .padding(.leading, 118)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var notesPathBinding: Binding<String> {
        Binding(get: { bibdb.notesRepoPath }, set: { bibdb.updateNotesRepoPath($0) })
    }

    // MARK: - ④ 当前生效值（一眼可核对）

    private var effectiveSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("④ 当前生效值（下一次「初排转换」会用这些）").font(.callout).bold()
            let db = BibDbFeature.existingDBPath()
            BibDbInfoRow(label: "核实上限", value: bibdb.verifyMaxSummary,
                         color: bibdb.verifyMaxHint == nil ? .primary : .red)
            BibDbInfoRow(label: "先算账", value: bibdb.verifyPlan
                         ? "开（本次只算账，不生成任务书/不检索/不调 AI）"
                         : "关（照常核实）")
            BibDbInfoRow(label: "全量核实", value: bibdb.verifyAllRefs
                         ? "开（越过上限闸门）" : "关（受上限约束）",
                         color: bibdb.verifyAllRefs ? .orange : .primary)
            BibDbInfoRow(label: "先查引用库", value: bibdb.lookupEnabled
                         ? (db == nil ? "开（但库文件不存在：引擎会优雅跳过查库）"
                                      : "开（库：\(db ?? "")）")
                         : "关（本次传 --no-bibdb）",
                         color: bibdb.lookupEnabled ? .primary : .orange)
            BibDbInfoRow(label: "库文件", value: db ?? "（不存在或找不到笔记仓）",
                         monospaced: true,
                         color: db == nil ? .orange : .secondary,
                         wraps: true)
            Text("补充：这些值只在「初排转换」勾了核实（任务书 / AI）时进 argv；"
                 + "「先算账」开着时引擎只打印核实计划就退出 0，本次不会真正核实。")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 动作

    private func openLibraryFolder() {
        guard let db = BibDbFeature.dbPath() else { return }
        Logger(store: log)(bibDbOpenInFinder((db as NSString).deletingLastPathComponent))
    }

    private func browseNotes() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "选择笔记仓"
        panel.message = "选私有笔记仓（EditToolBox-notes）的根目录；它的 bib_db/ 就是引用库位置"
        if panel.runModal() == .OK, let url = panel.url {
            bibdb.updateNotesRepoPath(url.path)
            Logger(store: log)("引用库路径（手动指定）：\(url.path)\n")
        }
    }
}
