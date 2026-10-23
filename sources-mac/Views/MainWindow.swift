import SwiftUI

/// 主窗口：**左侧分组导航 + 右侧页面 + 下半部分运行日志**。
///
/// 对应 Windows 版的 `MainWindow`（`ttk.Treeview` 侧栏）与原程序（`ttk.Notebook`）。
///
/// 为什么从 `TabView` 换成 `NavigationSplitView`（UI 方案候选 B，2026-10-17 用户批准）：
/// Apple HIG 说侧边栏用于顶层集合导航、**层级不超过两层**；WinUI 的判据是「左侧导航适合
/// 5–10 个**同等重要**的分类」。我们的 9 页其实是三层（单篇生产 / 整期装配 / 维护设置），
/// 9 个平级标签会让人找不到"一次转换"该从哪进去 —— 与 Windows 侧同一套结构。
///
/// 页面的排列顺序就是**编辑流程**的顺序；「设置」是配置、不属于流程某一步，单独放在组外底部。
struct MainWindow: View {

    @StateObject private var log = LogStore()
    @StateObject private var settings = AppSettings()
    /// 「引用库」分组的设置（核实成本闸门 / 查库开关 / 笔记仓路径，第 132 条）。
    /// 与 `settings` 分开：这些是引用库自己的行为偏好，不混进 AI 配置那一组。
    @StateObject private var bibdb = BibDbSettings()
    /// 第 55 批：AI 预算闸（单篇/单批 token；只约束直连 API 通道）。
    /// 与「设置」「初排转换」两页共用同一实例，转换时现取。
    @StateObject private var budget = AIBudgetSettings()
    /// 「一校核查」页的界面状态（本机没有 @State 宏，状态一律由 ObservableObject 承载）
    @StateObject private var proofModel = ProofCheckModel()

    @State private var selection: NavPage = .convert

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                Section("单篇稿件") {
                    ForEach(NavPage.singleArticle) { page in
                        Label(page.title, systemImage: page.symbol).tag(page)
                    }
                }
                Section("整期装配") {
                    ForEach(NavPage.assembly) { page in
                        Label(page.title, systemImage: page.symbol).tag(page)
                    }
                }
                // 第 132 条：引用库是与「单篇稿件 / 整期装配」**平行**的顶级分组，
                // 放在整期装配之后、「设置」之前（设置仍是组外的配置入口）。
                Section("引用库") {
                    ForEach(NavPage.library) { page in
                        Label(page.title, systemImage: page.symbol).tag(page)
                    }
                }
                Section {
                    Label(NavPage.settings.title, systemImage: NavPage.settings.symbol)
                        .tag(NavPage.settings)
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 160, ideal: 190)
        } detail: {
            VSplitView {
                // 全部页面都建好、只显示当前那页：这样切页**不会丢状态**
                // （编辑正在敲的路径/选项都还在 —— 与原来 TabView 的行为一致）。
                ZStack {
                    ConvertTab(log: log, settings: settings, bibdb: bibdb, budget: budget)
                        .pageVisible(selection == .convert)
                    CheckBibView(log: log)
                        .pageVisible(selection == .checkbib)
                    ProofCheckView(log: log, model: proofModel)
                        .pageVisible(selection == .proofcheck)
                    MergeTab(log: log, settings: settings)
                        .pageVisible(selection == .merge)
                    InfoCheckView(log: log)
                        .pageVisible(selection == .infocheck)
                    compilePage
                        .pageVisible(selection == .compile)
                    ContentTab(log: log)
                        .pageVisible(selection == .toc)
                    cleanPage
                        .pageVisible(selection == .cleantemp)
                    publishPage
                        .pageVisible(selection == .publish)
                    // 第 132 条：引用库 5 页。同样走 `.pageVisible`（案例 126：隐藏页会抢
                    // 拖拽命中，必须让隐藏页整体移出命中范围；**不要**改成条件渲染，
                    // 那会丢页面状态、也会让切换页时重新读库）。
                    // B-188：库概况橙字下钻「去处理（定位分类）」→ 只切页；
                    // 定位/「显示已裁决」由 StatsView 先写一次性 `bibdb.reviewJump`，
                    // 待处理页激活时取走（不再有共享 showResolvedConflicts 开关）。
                    BibDbStatsView(log: log, bibdb: bibdb, isActive: selection == .bibstats,
                                   onOpenReview: { selection = .bibreview })
                        .pageVisible(selection == .bibstats)
                    BibDbQueryView(log: log, bibdb: bibdb, isActive: selection == .bibquery)
                        .pageVisible(selection == .bibquery)
                    BibDbImportView(log: log, bibdb: bibdb, isActive: selection == .bibimport)
                        .pageVisible(selection == .bibimport)
                    BibDbReviewView(log: log, bibdb: bibdb, isActive: selection == .bibreview)
                        .pageVisible(selection == .bibreview)
                    BibDbSettingsView(log: log, bibdb: bibdb)
                        .pageVisible(selection == .bibsettings)
                    SettingsTab(settings: settings, budget: budget)
                        .pageVisible(selection == .settings)
                }
                .frame(minHeight: 300)

                LogPane(log: log)
            }
            .frame(minWidth: 720)
        }
    }

    // MARK: - 两个用 SimpleTab 拼的页面（与原来逐字相同的参数）

    /// 编译全部：输入一期的目录，逐篇 XeLaTeX → biber → XeLaTeX ×2。
    private var compilePage: some View {
        SimpleTab(
            help: """
            使用说明：输入路径为一期的目录，例如：
            …/Studies_in_Logic_Database/2026年第5期(90)

            点击运行则会编译所有子文件夹中的 tex（XeLaTeX → biber → XeLaTeX → XeLaTeX）。

            ⚠️ 注意：本功能会就地覆盖各篇文章目录里已有的 pdf，请确认 tex 已改好再运行。
            """,
            pickKind: .directory,
            log: log
        ) { path, logger in
            CompileFeature.run(folderPath: path, log: logger)
        }
    }

    /// 本期出刊向导：一个入口跑完五步（编排与判据在引擎 `publish.py`，只有一份）。
    private var publishPage: some View {
        SimpleTab(
            help: """
            使用说明：选一期的目录，点运行按顺序跑完五步：
            ① 检查tex信息 → ② 生成目录 → ③ 编译全部 → ④ 目录 PDF 落位 → ⑤ 合并整期 PDF。

            失败即中止（前 N−1 步的产物保留，日志末行写明卡在第几步）；
            退出码 1（有结果/需人工核对，例如有文章编译失败）只算橙色警告并继续。
            要单步重跑某一环节，仍用左边的单个页面。
            """,
            pickKind: .directory,
            log: log
        ) { path, logger in
            PublishFeature.run(folderPath: path, log: logger)
        }
    }

    /// 删除临时文件（B-211）：模式选择 + 运行 / 撤销 / 清空回收站。
    /// 三种模式都**先确认、再移入 .toolbox-trash（可撤销）**，不再直接删；
    /// 跑完横幅上有「撤销清理」，把文件搬回原处（引擎侧保证不覆盖已有文件）。
    private var cleanPage: some View {
        CleanTempView(
            log: log,
            run: { path, mode, logger in
                DeleteTempFeature.run(folderPath: path, mode: mode, log: logger)
            },
            emptyTrash: { path, logger in
                DeleteTempFeature.emptyTrash(folderPath: path, log: logger)
            },
            undo: { folder, trashDir in
                DeleteTempFeature.restore(folderPath: folder, trashDir: trashDir,
                                          log: Logger(store: log))
            },
            lastTrashDir: { DeleteTempFeature.lastTrashDir(folderPath: $0) }
        )
    }
}

/// 侧边栏的一页（两层结构：组 → 页）。
enum NavPage: String, CaseIterable, Identifiable {
    case convert, checkbib, proofcheck, merge
    case infocheck, compile, toc, cleantemp
    case publish
    // 第 132 条：引用库（库概况 / 查库 / 入藏 / 待处理（B-188 由「待裁决」改名）/ 引用库设置）
    case bibstats, bibquery, bibimport, bibreview, bibsettings
    case settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .convert: return "初排转换"
        case .checkbib: return "文献回读校验"
        case .proofcheck: return "一校核查"
        case .merge: return "合并文件"
        case .infocheck: return "检查tex信息"
        case .compile: return "编译全部"
        case .toc: return "生成目录"
        case .cleantemp: return "删除临时文件"
        case .publish: return "本期出刊"
        case .bibstats: return "库概况"
        case .bibquery: return "查库"
        case .bibimport: return "入藏"
        case .bibreview: return "待处理"
        case .bibsettings: return "引用库设置"
        case .settings: return "设置"
        }
    }

    /// 侧栏图标（SF Symbols：HIG 建议侧栏用熟悉的符号，且默认跟随 accent color）
    var symbol: String {
        switch self {
        case .convert: return "doc.text.magnifyingglass"
        case .checkbib: return "text.book.closed"
        case .proofcheck: return "checkmark.seal"
        case .merge: return "square.stack.3d.up"
        case .infocheck: return "doc.text.below.ecg"
        case .compile: return "hammer"
        case .toc: return "list.number"
        case .cleantemp: return "trash"
        case .publish: return "square.stack.3d.up.forward"
        case .bibstats: return "books.vertical"
        case .bibquery: return "text.magnifyingglass"
        case .bibimport: return "tray.and.arrow.down"
        case .bibreview: return "exclamationmark.triangle"
        case .bibsettings: return "slider.horizontal.3"
        case .settings: return "gearshape"
        }
    }

    /// 「单篇稿件」组（按编辑流程排）：初排 → 回读校验 → 一校核查 → 合并成刊。
    ///
    /// ⚠️ 顺序本身被 `tools/proofcheck_test` 断言（一校核查必须在回读校验之后）——
    /// 那是编辑流程：编辑拿《文献核实任务书》改完交回 .bib，接着就按手册逐条核查。
    static let singleArticle: [NavPage] = [.convert, .checkbib, .proofcheck, .merge]

    /// 「整期装配」组：查信息 → 编译 → 做目录 → 清临时文件。
    static let assembly: [NavPage] = [.infocheck, .compile, .toc, .cleantemp, .publish]

    /// 「引用库」组（第 132 条，与另两组平行）：看概况 → 查库 → 入藏 → 待处理 → 设置。
    /// 排序就是使用顺序：先知道库里有什么，再查/入藏，要人工处理才去「待处理」页。
    static let library: [NavPage] = [.bibstats, .bibquery, .bibimport, .bibreview, .bibsettings]
}

/// 只显示当前页：隐藏的页面保留状态但不可点、不进无障碍树
/// （`opacity(0)` 不改变布局，所以各个页面的宽度不会因为切页而跳动）。
private struct PageVisible: ViewModifier {
    let visible: Bool

    func body(content: Content) -> some View {
        content
            .opacity(visible ? 1 : 0)
            .allowsHitTesting(visible)
            .accessibilityHidden(!visible)
            // 案例 126：`opacity(0)+allowsHitTesting(false)` **挡不住拖拽命中测试**——
            // 隐藏页仍会被选为拖拽目标、把拖到可见页上的文件/文件夹吃掉（探针实测：
            // 隐藏页 dropDestination 收到 DROP，可见页毫无反应）。
            // 两个保险一起上：① 把隐藏页整体平移到窗口外（结构上让拖拽/鼠标命中不到它）；
            // ② 可见页放到 z 轴最上层。
            .offset(x: visible ? 0 : -100_000)
            .zIndex(visible ? 1 : 0)
    }
}

extension View {
    /// 侧边栏切页用（见 `PageVisible`）。
    func pageVisible(_ visible: Bool) -> some View {
        modifier(PageVisible(visible: visible))
    }
}
