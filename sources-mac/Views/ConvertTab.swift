import SwiftUI

/// 「初排转换」页：选源文件 → 选输出目录 → 转换出符合期刊模板的 LaTeX 初排稿。
///
/// 界面刻意做得简单：**必填的只有源文件和输出目录**，其余（metadata/bib/refs/
/// 语种/模板）都收在「高级」里。因为多数情况工具能自动判断
/// （语种、模板、参考文献附件都会自动识别），不需要编辑每次填一堆东西。
/// 「初排转换」页的界面状态。
/// 用 ObservableObject 而不是 @State —— 运行在工作线程里，
/// 需要在逃逸闭包中回写 running，@State 在 struct 里做不到（self 不可变）。
final class ConvertModel: ObservableObject {
    @Published var options = ConvertFeature.Options()
    @Published var running = false
    @Published var showAdvanced = false
    /// U3：本次运行的取消句柄（运行中「开始转换」变成「取消」）
    var cancel: RunCancel? = nil
}

struct ConvertTab: View {

    @ObservedObject var log: LogStore
    /// AI 配置（地址/密钥/模型）来自「设置」页，与 MainWindow 共用同一个实例：
    /// 设置页改完立刻在这里生效，不用重启 App。
    @ObservedObject var settings: AppSettings

    @StateObject private var model = ConvertModel()
    /// 责编名单（采集 + 自定义 + 默认），见 EditorStore.swift
    @StateObject private var editors = EditorStore()
    
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {

            ScrollView {
                VStack(alignment: .leading, spacing: 14) {

                    helpBlock

                    Divider()

                    // ---------- 源文件（必填） ----------
                    LabeledPathRow(
                        title: "源文件（必填）",
                        placeholder: "Word 文稿（.docx）或 LaTeX 文件（.tex）",
                        path: $model.options.source,
                        kind: .file,
                        logStore: log
                    )

                    // ---------- 输出目录（必填） ----------
                    LabeledPathRow(
                        title: "输出目录（必填）",
                        placeholder: "初排稿的输出位置，例如 …/2026年第5期(90)（工具会在其下建 名_姓/；不要选 03 名_姓，会多套一层）",
                        path: $model.options.out,
                        kind: .directory,
                        logStore: log
                    )

                    Text("源文件与输出目录都可以直接从访达拖进来（高级选项里的路径同理）")
                        .font(.caption)
                        .foregroundStyle(.tertiary)

                    // ---------- 常勾选项 ----------
                    VStack(alignment: .leading, spacing: 6) {
                        Toggle("生成后立即编译 PDF（XeLaTeX → biber → XeLaTeX ×2）",
                               isOn: $model.options.compile)
                        Toggle("清理编译中间文件（保留 .log）", isOn: $model.options.clean)
                        Toggle("生成参考文献核实任务书（交给自己的 AI 核实）",
                               isOn: binding(.verifyRefs, \.verifyRefs))
                            // 勾了「用 AI 处理参考文献」时这份任务书**必出**（用户 2026-10-15 方案 A）：
                            // AI 的逐条建议与「六、合并记录」都写在里面，不生成就没有审阅材料。
                            .disabled(ConvertFeature.verifyRefsLocked(model.options))
                        if ConvertFeature.verifyRefsLocked(model.options) {
                            Text("↑ 已随「用 AI 处理参考文献」自动勾上并锁定 —— AI 的逐条建议与"
                                 + "「六、合并记录」都写在这份《文献核实任务书》里，不生成它就没有审阅材料。"
                                 + "取消下面那行的 AI 勾选即可重新操作。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.leading, 18)
                        }
                        // 与之配套的 AI 开关：勾上就是「任务书 + 按同一份规范在本机直连 AI 预跑」。
                        // 不勾也能单独出任务书，所以两个开关不冲突：勾哪个都会加 --verify-refs。
                        Toggle("用 AI 处理参考文献（需在「设置」里配置）",
                               isOn: binding(.useAI, \.useAI))
                        aiHint
                        if model.options.useAI {
                            // 只在 AI 勾着时可见：没勾 AI 就没有合并通道，这个开关无从谈起。
                            Toggle("保留原 reference.bib（不并入 AI 结果）",
                                   isOn: binding(.keepOriginalBib, \.keepOriginalBib))
                            Text("勾上 = 只出《文献核实任务书》（引擎侧 --no-ai-merge）：AI 结果不写进产物，"
                                 + "reference.bib 一个字节都不动。不勾时 AI 结果会并入交付的 reference.bib，"
                                 + "合并之前的那份会先备份成 reference.bib.ai前（要回滚就把它覆盖回去）。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.leading, 18)
                        }
                        Toggle("导出原稿 PDF（供矢量图形人工截图）",
                               isOn: $model.options.sourcePdf)
                    }
                    .toggleStyle(.checkbox)

                    // ---------- 高级 ----------
                    DisclosureGroup("高级选项", isExpanded: $model.showAdvanced) {
                        VStack(alignment: .leading, spacing: 10) {
                            Toggle("覆盖已存在的输出目录（--force）",
                                   isOn: $model.options.force)
                                .toggleStyle(.checkbox)
                            Text("目标目录里混着非本工具的文件时（例如手工 latexmk 留下的 "
                                 + ".fdb_latexmk/.fls），默认会被拦下以防误伤已有稿件目录；"
                                 + "勾选后照常写入：同名产物被覆盖，目录里的其它文件不会被删。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            LabeledPathRow(
                                title: "参考文献附件",
                                placeholder: "留空则自动识别（全库 37% 的稿件把文献单独放一个文件）",
                                path: $model.options.refs,
                                kind: .file,
                                logStore: log
                            )
                            LabeledPathRow(
                                title: "元数据文件",
                                placeholder: "从已有的 .tex 里取标题/作者等信息",
                                path: $model.options.metadata,
                                kind: .file,
                                logStore: log
                            )
                            LabeledPathRow(
                                title: "已有的 .bib",
                                placeholder: "指定后不再自动生成",
                                path: $model.options.bib,
                                kind: .file,
                                logStore: log
                            )

                            HStack(spacing: 12) {
                                Text("输出名")
                                TextField("留空则按第一作者自动命名（如 Chen_Bo）",
                                          text: $model.options.name)
                                    .textFieldStyle(.roundedBorder)
                            }
                            HStack(spacing: 12) {
                                Text("第一作者")
                                TextField("留空则从源文件提取",
                                          text: $model.options.author)
                                    .textFieldStyle(.roundedBorder)
                            }
                            HStack(spacing: 12) {
                                Text("语种")
                                Picker("", selection: $model.options.lang) {
                                    Text("自动判断").tag("")
                                    Text("中文（SLCN-MIX.sty）").tag("zh")
                                    Text("英文（SLEN.sty）").tag("en")
                                }
                                .labelsHidden()
                                .frame(width: 240)
                            }
                            Divider().padding(.vertical, 2)
                            Text("出版信息（选填；填了 PDF 页眉/页码才是最终形态）")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            HStack(spacing: 12) {
                                Text("卷 / 期 / 年")
                                TextField("19", text: $model.options.vol)
                                    .textFieldStyle(.roundedBorder).frame(width: 60)
                                TextField("5", text: $model.options.issue)
                                    .textFieldStyle(.roundedBorder).frame(width: 60)
                                TextField("2026", text: $model.options.pubYear)
                                    .textFieldStyle(.roundedBorder).frame(width: 70)
                            }
                            HStack(spacing: 12) {
                                Text("起止页")
                                TextField("137-152", text: $model.options.pages)
                                    .textFieldStyle(.roundedBorder).frame(width: 110)
                                Text("收稿日期")
                                TextField("2025-09-19", text: $model.options.received)
                                    .textFieldStyle(.roundedBorder).frame(width: 110)
                                Text("责编")
                                Picker("", selection: $model.options.editor) {
                                    Text("未指定").tag("")
                                    ForEach(editors.allNames, id: \.self) { n in
                                        Text(editors.defaultName == n ? "\(n)（默认）" : n).tag(n)
                                    }
                                    Text("自定义…").tag("__custom__")
                                }
                                .labelsHidden()
                                .frame(width: 130)
                                if model.options.editor == "__custom__" {
                                    TextField("新责编姓名", text: $editors.newName)
                                        .textFieldStyle(.roundedBorder).frame(width: 90)
                                    Button("添加") {
                                        editors.addCustom(editors.newName)
                                        let n = editors.newName.trimmingCharacters(in: .whitespacesAndNewlines)
                                        if !n.isEmpty { model.options.editor = n }
                                        editors.newName = ""
                                    }
                                }
                                Button(editors.defaultName == model.options.editor && !model.options.editor.isEmpty
                                       ? "已设为默认 ✓" : "设为默认") {
                                    editors.setDefault(model.options.editor)
                                }
                                .disabled(model.options.editor.isEmpty || model.options.editor == "__custom__")
                            }
                            HStack(spacing: 12) {
                                Text("基金项目")
                                TextField("选填，如：国家社会科学基金项目“…”（23BZX113）。",
                                          text: $model.options.grants)
                                    .textFieldStyle(.roundedBorder)
                            }
                            Divider().padding(.vertical, 2)
                            // ---------- 正文信息手工覆盖（兜底输入路径） ----------
                            // 引擎按版面启发式解析题名/作者/摘要，总有解析不出来或
                            // 解析错的稿子。这里是编辑的兜底：填了就覆盖自动解析结果，
                            // 不用等程序修。全部留空 = 不覆盖，与旧版行为一致。
                            Text("正文信息覆盖（选填；解析失败或解析不对时手工填入，"
                                 + "填了即以人工值为准）")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            HStack(spacing: 12) {
                                Text("中文题名")
                                TextField("留空则用自动解析结果", text: $model.options.titleCn)
                                    .textFieldStyle(.roundedBorder)
                            }
                            HStack(spacing: 12) {
                                Text("第一作者")
                                TextField("留空则用自动解析结果",
                                          text: $model.options.authorCn)
                                    .textFieldStyle(.roundedBorder).frame(width: 180)
                                Text("单位")
                                TextField("如：中山大学逻辑与认知研究所",
                                          text: $model.options.affiliationCn)
                                    .textFieldStyle(.roundedBorder)
                            }
                            HStack(spacing: 12) {
                                Text("邮箱")
                                TextField("如：someone@example.com", text: $model.options.email)
                                    .textFieldStyle(.roundedBorder).frame(width: 240)
                            }
                            HStack(spacing: 12) {
                                Text("中文摘要")
                                TextField("留空则用自动解析结果", text: $model.options.abstractCn)
                                    .textFieldStyle(.roundedBorder)
                            }
                            HStack(spacing: 12) {
                                Text("英文题名")
                                TextField("留空则用自动解析结果", text: $model.options.titleEn)
                                    .textFieldStyle(.roundedBorder)
                            }
                            HStack(spacing: 12) {
                                Text("英文摘要")
                                TextField("留空则用自动解析结果", text: $model.options.abstractEn)
                                    .textFieldStyle(.roundedBorder)
                            }
                            Text("⚠ 手工覆盖的生效取决于引擎版本：用旧版引擎时日志会提示"
                                 + "「尚不支持 overrides 参数」，此时不影响转换本身。")
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                            HStack(spacing: 12) {
                                Text("引用环境阈值")
                                TextField("105", value: $model.options.quoteThreshold, format: .number)
                                    .textFieldStyle(.roundedBorder)
                                    .frame(width: 80)
                                Text("字符（超过此长度提升为引用环境）")
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.top, 8)
                    }

                    Divider()

                    // ---------- 运行 ----------
                    HStack(spacing: 12) {
                        Button {
                            run(scanOnly: true)
                        } label: {
                            Label("只扫描源稿", systemImage: "doc.text.magnifyingglass")
                        }
                        .disabled(model.running || model.options.source.isEmpty)

                        // U3：运行中主按钮变成「取消」（不是灰掉另放一个按钮）。
                        // 取消 = 杀整棵进程树（Python 起的 xelatex 一起停，见 `RunCancel`）。
                        if model.running {
                            Button {
                                model.cancel?.cancel()
                            } label: {
                                Label("取消", systemImage: "stop.circle")
                            }
                        } else {
                            Button {
                                run(scanOnly: false)
                            } label: {
                                Label("开始转换", systemImage: "wand.and.stars")
                            }
                            .keyboardShortcut(.return, modifiers: .command)
                            .disabled(model.options.source.isEmpty || model.options.out.isEmpty)
                        }

                        if model.running {
                            ProgressView().controlSize(.small)
                            Text("运行中…（点「取消」可中止）").foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(16)
            }
        }
    }

    private var helpBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("把作者交来的 Word 文稿或 LaTeX 源文件，转成符合《逻辑学研究》模板的初排稿。")
                .font(.callout)
            Text("会自动处理：公式（OMML→LaTeX）、表格（→tabular）、内嵌图片（直接提取原图）、"
                 + "矢量图形（检出并置占位）、脚注、引用环境、参考文献"
                 + "（生成《文献核实任务书》；在「设置」里配好 AI 还可自动预跑一遍，"
                 + "并把通过安全校验的结果并入 reference.bib）。")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("⚠ 工具只做初排。数字前缀、目录顺序在三校/蓝样阶段再加。".plainMarkdown)
                .font(.caption)
                .foregroundStyle(.orange)
        }
        .onAppear { syncEditors() }
        // 选好源文件后重新采集一次：数据库根目录是从源文件路径往上找的
        .onChange(of: model.options.source) { _ in syncEditors() }
    }

    /// 载入自定义名单与默认值；采集全库责编（需要源文件路径来定位数据库根目录）。
    private func syncEditors() {
        editors.load()
        if model.options.editor.isEmpty, !editors.defaultName.isEmpty {
            model.options.editor = editors.defaultName
        }
        guard let tool = ConvertFeature.locateTool() else { return }
        let dir = (tool as NSString).deletingLastPathComponent
        guard let py = ConvertFeature.locatePython(toolDir: dir) else { return }
        let vendor = dir + "/vendor"
        editors.harvest(source: model.options.source, tool: tool, python: py, toolDir: dir,
                        vendor: FileManager.default.fileExists(atPath: vendor) ? vendor : nil)
    }

    /// 复选框 → 选项的绑定：联动规则的**唯一**实现在 `ConvertFeature.toggled(_:to:in:)`
    /// （纯函数，`tools/ai_settings_test` 直接验证它）。SwiftUI 这边只做绑定，
    /// 不写任何 if/联动逻辑 —— 界面照抄一份判据，迟早会与引擎侧分叉。
    private func binding(_ which: ConvertFeature.Checkbox,
                         _ keyPath: WritableKeyPath<ConvertFeature.Options, Bool>) -> Binding<Bool> {
        Binding(get: { model.options[keyPath: keyPath] },
                set: { model.options = ConvertFeature.toggled(which, to: $0, in: model.options) })
    }

    /// AI 开关下面那行说明。
    ///
    /// 勾了但没配置时**必须显式提示**（编辑部要求：不许静默忽略）：
    /// 说清「本次只会生成《文献核实任务书》、不会调用任何模型」，并指到「设置」页。
    /// 配好了则必须写清**它到底会做什么**（按事实改文案，2026-10-15）：
    ///   ① 它会调 API 生成修订后的 bib，并**并入交付的 `reference.bib`**；
    ///   ② 它的**目的**就是按任务书规范核实（第二节：优先用官方来源查证、比对卷期页码）；
    ///   ③ 第 69 条起，「能不能联网」由「设置」页的两个开关决定，文案必须**分开说**：
    ///      · 开了「联网检索」+ 通道 responses → 本次**确实**会带上服务端检索工具，
    ///        请模型先查官方来源；但**端点是否真的检索**由端点与模型决定（DeepSeek 的
    ///        `deepseek-flash` 会**静默忽略**、`deepseek-v4-pro` 才真检索），所以要去
    ///        「设置」页点「测试连接 / 体检」看 ⑤ 检索探针的实测结论，并以引擎日志的
    ///        「检索证据」为准；
    ///      · 没开 / 端点不支持 → 模型只能凭记忆，卷期页码凭记忆编错比空着更糟。
    ///   ④ 失败时的安全行为：调用失败 / 回复被截断 / 一条都没通过安全校验 → bib 一字节不改。
    @ViewBuilder private var aiHint: some View {
        if model.options.useAI {
            if settings.aiConfigured {
                VStack(alignment: .leading, spacing: 2) {
                    Text("✔ 已配置 \(settings.aiSummary)（密钥 \(AppSettings.mask(settings.aiKey))）"
                         + " —— 转换时加 --verify-refs：在本机直连这个端点，按《文献核实任务书》的同一份"
                         + "规范逐条生成修订后的 bib，并把通过安全校验的条目并入交付的 reference.bib"
                         + "（合并前先备份成 reference.bib.ai前；理由与逐条记录写进《…_文献核实.md》"
                         + "的「六、合并记录」）。")
                        .foregroundStyle(.green)
                    if settings.aiEngineKnobs.usesWebSearch {
                        Text("🌐 本次已开启联网检索（通道 responses + web_search）：引擎会带上"
                             + "服务端检索工具，并要求模型先查官方来源、把打开的页面 URL 写进每条上方的 "
                             + "`% 来源：` 标注，查不到的字段照旧省略并写 `% ⚠ 待核实：…`。")
                            .foregroundStyle(.green)
                        Text("⚠ 但「勾了开关」不等于「端点真的会查」：内置 `web_search` 是否真检索"
                             + "由端点与模型决定——DeepSeek 需 `deepseek-v4-pro`（`deepseek-flash` "
                             + "仍会静默忽略该工具）。请到「设置」页点「测试连接 / 体检」看 ⑤ 检索探针"
                             + " 的实测结论；本次跑完引擎会在日志与「六、合并记录」里报检索证据"
                             + "（0 次 = 只能凭记忆）——本工具绝不假装已联网。")
                            .foregroundStyle(.orange)
                    } else if settings.aiProtocol == .responses {
                        Text("⚠ 通道是 responses，但没开「联网检索」：本次模型只能凭记忆作答"
                             + "（任务书里同样写明）。要真联网请在「设置」页勾上「联网检索」，"
                             + "并先跑一次 ⑤ 检索探针确认端点支持。")
                            .foregroundStyle(.orange)
                    } else {
                        Text("⚠ 本次不是联网核实（通道 chat、未启用检索）：模型只能凭记忆作答。"
                             + "卷/期/页码/出版地想真查官方来源，请到「设置」页把「通道」改成 responses、"
                             + "勾上「联网检索」，并先跑一次 ⑤ 检索探针确认端点支持（DeepSeek 的 "
                             + "deepseek-flash 仍忽略检索工具、deepseek-v4-pro 才支持，探针会如实实测）。")
                            .foregroundStyle(.orange)
                    }
                    if settings.aiSearchRefs {
                        Text("🔎 本次会先用 DeepSeek **独立检索**（Search API）逐条查参考文献证据，"
                             + "把证据写进任务书并注入 AI 提示词；并入前会校验 `% 来源：` URL "
                             + "是否真在证据里（按次计费 ¥5/1000 次成功；默认关）。")
                            .foregroundStyle(.green)
                    }
                    Text("AI 调用失败、回复被截断、或没有任何一条通过安全校验时，"
                         + "reference.bib 一个字节都不会改（详见下方日志）。"
                         + "无论哪种通道，并完之后都必须人工过一遍。")
                        .foregroundStyle(.secondary)
                }
                .font(.caption)
                .padding(.leading, 18)
                .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("⚠ 未配置 AI，本次只会生成《文献核实任务书》（不会调用任何模型，"
                     + "reference.bib 一个字节不改）。请到「设置」标签页填写 API 地址与模型名，"
                     + "「测试连接」可用后再回来勾选。")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .padding(.leading, 18)
            }
        }
    }

    private func run(scanOnly: Bool) {
        var o = model.options
        o.scanOnly = scanOnly
        // AI 配置在**运行时**从「设置」页现取：设置页改了立刻生效，不用重启 App。
        // 密钥只放进命令行参数，不打印（日志里走 AppSettings.mask）。
        o.llmBase = settings.aiBase.trimmingCharacters(in: .whitespacesAndNewlines)
        o.llmKey = settings.aiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        o.llmModel = settings.aiModel.trimmingCharacters(in: .whitespacesAndNewlines)
        // 第 69 条：通道与联网检索开关也**现取**（与上面三项同一口径）。默认（chat/关）
        // 时 `arguments()` 一个参数都不加 —— 命令行与从前逐字节相同。
        o.llmProtocol = settings.aiProtocol.rawValue
        o.llmWebSearch = settings.aiWebSearch
        o.searchRefs = settings.aiSearchRefs
        // 第 70 条：两个高级旋钮（工具名 / responses 路径）也**现取**，同一口径。
        // 留空 = `arguments()` 不加参数 → 引擎用默认（`web_search` / `/responses`），
        // 命令行与从前逐字节相同。填了非法值时 `arguments()` 会跳过它、`knobWarnings`
        // 在日志里说明「本次不传」（界面另有红字）。
        o.llmSearchTool = settings.aiSearchTool.trimmingCharacters(in: .whitespacesAndNewlines)
        o.llmResponsesPath = settings.aiResponsesPath.trimmingCharacters(in: .whitespacesAndNewlines)
        // 两个「请求体旋钮」（思考模式 / 输出上限）同样**现取**：与上面三项同一口径，
        // 设置页改完立刻生效，不必先点「保存」（保存只影响下次启动读回的值）。
        // ⚠️ 必须在主线程上取好再传：@Published 的值属于主线程，而 run() 在后台队列
        //    上执行（AIEngineKnobs 是值类型，跨线程传是安全的）。
        let knobs = settings.aiEngineKnobs
        // U3：每次转换一个取消句柄；`Shell.run` 把 python 子进程注册进 logger.cancel。
        let cancel = RunCancel()
        model.cancel = cancel
        model.running = true
        let logger = Logger(store: log, startedAt: Date(), cancel: cancel)
        let m = model
        DispatchQueue.global(qos: .userInitiated).async {
            ConvertFeature.run(options: o, knobs: knobs, log: logger)
            DispatchQueue.main.async { m.running = false }
        }
    }
}

/// 一行「标签 + 路径输入 + 浏览按钮」。与 SimpleFeatureView 的选择方式保持一致（NSOpenPanel）。
struct LabeledPathRow: View {
    let title: String
    let placeholder: String
    @Binding var path: String
    let kind: PickKind
    /// 拖拽落地 / 被拒时写进运行日志（可为 nil）。不能静默——
    /// 编辑拖了没反应时，日志里要能看出是「类型不对」还是「压根没收到拖拽」。
    var logStore: LogStore? = nil

    var body: some View {
        let logger = logStore.map { Logger(store: $0) }
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.callout).bold()
            HStack(spacing: 8) {
                DroppablePathField(text: $path, placeholder: placeholder) { url in
                    PathDrop.receive(url, kind: kind, label: title, log: logger,
                                     setPath: { path = $0 }, onAccept: nil)
                }
                Button("浏览…") { browse() }
                if !path.isEmpty {
                    Button {
                        path = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                }
            }
        }
        .pathDropTarget(path: $path, kind: kind, label: title, log: logger)
    }

    private func browse() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = (kind == .file)
        panel.canChooseDirectories = (kind == .directory)
        panel.allowsMultipleSelection = false
        if kind == .file {
            panel.allowedContentTypes = []
            panel.allowsOtherFileTypes = true
        }
        if panel.runModal() == .OK, let url = panel.url {
            path = url.path
        }
    }
}
