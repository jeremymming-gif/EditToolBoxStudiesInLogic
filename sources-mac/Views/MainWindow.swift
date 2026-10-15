import SwiftUI

/// 主窗口：上半部分是 8 个标签页（7 个功能页 + 设置），下半部分是运行日志。
/// 对应原程序的 MainWindow（ttk.Notebook + ScrolledText）。
///
/// 标签顺序按**编辑流程**排：初排 → 回读校验 → 查信息 / 编译 → 做目录 → 合并成刊 → 清临时文件；
/// 「设置」是配置项、不属于流程某一步，所以放最后。
/// 「初排转换」是每篇稿子的第一步，所以放第一个；「文献回读校验」是它的直接后续
/// （编辑拿转换时生成的《文献核实任务书》改完交回 .bib，接着就做这一步），所以紧跟其后；
/// 「合并文件」是整个流程的最后一步（总负责把各篇合成一期），所以放靠后。
struct MainWindow: View {

    @StateObject private var log = LogStore()
    @StateObject private var settings = AppSettings()

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

                // ---------- 3. 检查tex信息 ----------
                InfoCheckView(log: log)
                    .tabItem { Text("检查tex信息") }

                // ---------- 4. 编译全部 ----------
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

                // ---------- 5. 生成目录 ----------
                ContentTab(log: log)
                    .tabItem { Text("生成目录") }

                // ---------- 6. 合并文件 ----------
                MergeTab(log: log, settings: settings)
                    .tabItem { Text("合并文件") }

                // ---------- 7. 删除临时文件 ----------
                SimpleTab(
                    help: """
                    使用说明：输入路径，例如：
                    …/Studies_in_Logic_Database/2026年第5期(90)

                    点击运行则可删除此文件夹内及子文件夹内所有临时文件：
                    .aux, .bbl, .bcf, .blg, .out, .run.xml, .synctex.gz, .DS_Store
                    在更换设备并要修改 tex 或 bib 时使用，避免编译出现问题；
                    也会顺手清掉 macOS 访达生成的 .DS_Store。
                    """,
                    pickKind: .directory,
                    log: log
                ) { path, logger in
                    DeleteTempFeature.run(folderPath: path, log: logger)
                }
                .tabItem { Text("删除临时文件") }

                // ---------- 8. 设置 ----------
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
