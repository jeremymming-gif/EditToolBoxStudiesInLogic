import SwiftUI

/// 主窗口：上半部分是 9 个标签页（8 个功能页 + 设置），下半部分是运行日志。
/// 对应原程序的 MainWindow（ttk.Notebook + ScrolledText）。
///
/// 标签顺序按**编辑流程**排：初排 → 回读校验 → **一校核查** → 查信息 / 编译 → 做目录 → 合并成刊 → 清临时文件；
/// 「设置」是配置项、不属于流程某一步，所以放最后。
/// 「初排转换」是每篇稿子的第一步，所以放第一个；「文献回读校验」是它的直接后续
/// （编辑拿转换时生成的《文献核实任务书》改完交回 .bib，接着就做这一步），所以紧跟其后；
/// 「合并文件」是整个流程的最后一步（总负责把各篇合成一期），所以放靠后。
struct MainWindow: View {

    @StateObject private var log = LogStore()
    @StateObject private var settings = AppSettings()
    /// 「一校核查」页的界面状态（本机没有 @State 宏，状态一律由 ObservableObject 承载）
    @StateObject private var proofModel = ProofCheckModel()

    var body: some View {
        VSplitView {

            TabView {

                // ---------- 1. 初排转换 ----------
                // 传 settings：这一页要读 AI 配置（地址/密钥/模型）来决定
                // 「用 AI 处理参考文献」开关能不能真的生效。
                ConvertTab(log: log, settings: settings)
                    .tabItem { Text("初排转换") }

                // ---------- 2. 文献回读校验 ----------
                // 「初排转换」的直接后续：编辑拿《文献核实任务书》改完交回 .bib，
                // 这一页对着任务书前的原件做离线体检（不联网核实真伪）。
                CheckBibView(log: log)
                    .tabItem { Text("文献回读校验") }

                // ---------- 3. 一校核查 ----------
                // 位置紧跟「文献回读校验」：编辑一校完稿子，接着就按手册逐条核查。
                // 逻辑全在引擎里（Engine/proofcheck.py），这一页只拼参数、显示日志。
                ProofCheckView(log: log, model: proofModel)
                    .tabItem { Text("一校核查") }

                // ---------- 4. 检查tex信息 ----------
                InfoCheckView(log: log)
                    .tabItem { Text("检查tex信息") }

                // ---------- 5. 编译全部 ----------
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
                .tabItem { Text("编译全部") }

                // ---------- 6. 生成目录 ----------
                ContentTab(log: log)
                    .tabItem { Text("生成目录") }

                // ---------- 7. 合并文件 ----------
                MergeTab(log: log, settings: settings)
                    .tabItem { Text("合并文件") }

                // ---------- 8. 删除临时文件 ----------
                // UI 方案第 3 件：**先确认、再移入 .toolbox-trash（可撤销）**，不再直接删；
                // 跑完横幅上有「撤销清理」，把文件搬回原处（引擎侧保证不覆盖已有文件）。
                SimpleTab(
                    help: """
                    使用说明：输入路径，例如：
                    …/Studies_in_Logic_Database/2026年第5期(90)

                    点击运行会**先确认**，再把此文件夹内及子文件夹内的临时文件
                    **移入同目录的 .toolbox-trash/**（可撤销，不会直接删掉）：
                    .aux, .bbl, .bcf, .blg, .out, .run.xml, .synctex.gz, .DS_Store
                    不会动 .tex / .pdf / .sty / .bib / .log，也不进 original/（作者来稿）。
                    在更换设备并要修改 tex 或 bib 时使用，避免编译出现问题。
                    """,
                    pickKind: .directory,
                    log: log,
                    action: { path, logger in
                        DeleteTempFeature.run(folderPath: path, log: logger)
                    },
                    undoTitle: "撤销清理",
                    onUndo: { folder, trashDir in
                        DeleteTempFeature.restore(folderPath: folder, trashDir: trashDir,
                                                  log: Logger(store: log))
                    }
                )
                .tabItem { Text("删除临时文件") }

                // ---------- 9. 设置 ----------
                // 放最后：它不属于「编辑流程」的某一步，而是配置
                // （AI 的 API 地址/密钥/模型；也可以在别处指向 convert.py）。
                // 与合并页共用同一个 AppSettings 实例，改完立刻对
                // 「初排转换」生效，不需要重启。
                SettingsTab(settings: settings)
                    .tabItem { Text("设置") }
            }
            .frame(minHeight: 300)

            LogPane(log: log)
        }
    }
}
