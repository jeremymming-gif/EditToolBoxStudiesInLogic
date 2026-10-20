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

    // MARK: - 第 55 批：两段式「AI 协同初排」的界面状态机
    //
    // `coopStage` 由**扫描暂存目录**得出（只读引擎写的 `_AI协同状态.json`），
    // 是按钮组与状态文案的唯一依据：没有它 = 尚未初排；`phase=ph1` = 待人工核实
    // （**未交付**）；`phase=done` = 已交付。**绝不**把暂存目录当交付目录展示。

    /// 当前源稿在输出目录下匹配到的暂存目录（最近修改的那个）
    @Published var coopStage: CoopFeature.StageState? = nil
    /// 匹配到的候选数（>1 时界面提示「已选最近的」）
    @Published var coopCandidates = 0
    /// 「一键修改产物（--coop-apply）」—— 默认不勾；必须在跑第二段**之前**决定
    @Published var coopApply = false
    /// 第二段成功后才有的建议摘要（从 `<stem>_AI建议.jsonl` 统计；仅界面辅助信息）
    @Published var coopSuggestions: CoopFeature.SuggestionsSummary? = nil
    /// 协同记录文件（已交付且文件确实存在时非空）
    @Published var coopRecordPath = ""
    /// 最近一次协同运行的失败说明（退出码 + 日志尾部）
    @Published var coopFailure = ""
    // ---- 第 56 批：回填导入重构（多文件 / 目录 / 剪贴板 + 已选清单） ----
    /// 已选回填（文件/目录/剪贴板草稿可混合、可多次导入累加、可逐项移除/清空）。
    /// **不再折叠成父目录**：每一项按顺序各传一个 `--coop-replies`。
    @Published var replies = CoopFeature.RepliesSelection()
    /// 第二段成功后读 state.stats 的结果摘要（旧状态回退 record/旧 state）
    @Published var coopResult: CoopFeature.CoopResult? = nil
    /// 剪贴板草稿序号（每次粘贴递增，同秒重复粘贴也不会互相覆盖）
    var clipboardSequence = 0
}

struct ConvertTab: View {

    @ObservedObject var log: LogStore
    /// AI 配置（地址/密钥/模型）来自「设置」页，与 MainWindow 共用同一个实例：
    /// 设置页改完立刻在这里生效，不用重启 App。
    @ObservedObject var settings: AppSettings
    /// 第 132 条：引用库设置（核实上限 / 先算账 / 全量 / 查不查库 / 手动库路径）。
    /// 与「引用库设置」页共用同一实例；本页在**点击转换时现取**（与 AI 旋钮同一口径）。
    @ObservedObject var bibdb: BibDbSettings
    /// 第 55 批：AI 预算闸（单篇/单批 token）。点击转换时现取；只约束直连 API 通道。
    @ObservedObject var budget: AIBudgetSettings

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
                        // 批 62 入口收敛（B 方案）：独立的「生成文献核实任务书」勾选已隐藏，
                        // 本页只保留两个语义入口 —— 两段式（任务书 = 第一段产物）与 ⑪ 直连 AI
                        // （内部自动带 --verify-refs，产物/行为与批61 一致）。
                        // `Options.verifyRefs` 字段保留给旧脚本/既有调用方，界面不再绑定它；
                        // 普通初排转换不再带 --verify-refs（默认产物无 <stem>_文献核实.md）。
                        // 第 132 条：核实成本闸门提示（值来自「引用库设置」页，点击转换时现取）。
                        // 只有真的会核实（⑪ / 两段式）时才显示，免得普通转换看到不相干的数字。
                        if model.options.useAI || model.options.coop {
                            verifyGateHint
                        }
                        // 「用 AI 处理参考文献」：勾上就是「任务书 + 按同一份规范在本机直连 AI 预跑」
                        // （引擎侧自动带 --verify-refs；批 62 起这是普通初排唯一出任务书的入口）。
                        // 批 62 文案与 Win 逐字对齐（Lead 口径）：标签点明「直连 API，会产生费用」。
                        Toggle("用 AI 处理参考文献（直连 API，会产生费用）",
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
                            // ---------- 第 60 批：全文 AI 核查（直连 API，会产生费用；默认不勾） ----------
                            // 默认不勾 → 一个参数都不加（默认 argv 与批 59 逐字节相同）。
                            Toggle("全文 AI 核查（直连 API，会产生费用）",
                                   isOn: $model.options.llmFulltext)
                            Text("⚠ 勾上后会产生费用：引擎把稿件正文按片直接发送到"
                                 + "「设置」里配置的 AI 端点，每片一次请求；不勾（默认）时正文一个字节都不外发。\n"
                                 + "范围：通过批 57 安全闸的 BIB/META/FN 建议会**自动应用**"
                                 + "（先备份 .ai前、0 条不写、冲突拒绝；可在「设置 → 全文核查结果自动应用」关闭），"
                                 + "《…_AI全文复核.md》报告照常生成；两段式协同模式下本轮不生效。\n"
                                 + "上限：「设置」里的「API 核查字符上限」默认 40000 字符（0 = 不限）；"
                                 + "「单篇预算」（默认 30000 tokens）不够时请按日志提示调大。\n"
                                 + "⚠ 前提：需先在「设置」页配置端点；未配置时引擎直接退出 2（不会发出任何请求）。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.leading, 18)
                        }
                        // ---------- 第 55 批：两段式「AI 协同初排」开关（默认关） ----------
                        // 默认关 → 协同相关字段一个都不进 argv，转换行为与批 54 逐字节相同。
                        Toggle("AI 协同初排（两段式；外部 chat 免费流程）",
                               isOn: $model.options.coop)
                            .onChange(of: model.options.coop) { on in
                                if on { refreshCoopStage() }
                            }
                        if model.options.coop {
                            Text(coopToggleHintText)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.leading, 18)
                        }
                        Toggle("导出原稿 PDF（供矢量图形人工截图）",
                               isOn: $model.options.sourcePdf)
                    }
                    .toggleStyle(.checkbox)

                    // ---------- 高级 ----------
                    // 批 63 B1：系统 DisclosureGroup 在 macOS 上只有 chevron 是命中区，
                    // 点「高级选项」文字/整行没反应（用户反馈）。改成普通 Button(.plain)
                    // 铺满整行：点文字或行内空白都能开合；withAnimation 保留展开动画；
                    // 状态仍是 `model.showAdvanced`（全仓仅此一处引用），无其它回归。
                    Button {
                        withAnimation(.easeInOut(duration: 0.15)) { model.showAdvanced.toggle() }
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "chevron.right")
                                .rotationEffect(.degrees(model.showAdvanced ? 90 : 0))
                            Text("高级选项")
                            Spacer()
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if model.showAdvanced {
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
                                placeholder: "从已有的 .tex / .doc / .docx 里取标题/作者等信息",
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
                                Label(model.options.coop
                                      ? "开始第一段（出任务书）"
                                      : "开始转换",
                                      systemImage: model.options.coop
                                      ? "doc.badge.gearshape" : "wand.and.stars")
                            }
                            .keyboardShortcut(.return, modifiers: .command)
                            .disabled(model.options.source.isEmpty || model.options.out.isEmpty)
                        }

                        if model.running {
                            ProgressView().controlSize(.small)
                            Text("运行中…（点「取消」可中止）").foregroundStyle(.secondary)
                        }
                    }

                    // ---------- 第 55 批：两段式状态区 ----------
                    if model.options.coop {
                        coopPanel
                    }
                }
                .padding(16)
            }
        }
        // 第 55 批：勾着协同开关时，源稿/输出目录一改就重新定位暂存目录
        // （编辑可能先初排、再换输出目录继续——状态区必须跟着事实走）。
        .onChange(of: model.options.source) { _ in
            if model.options.coop { refreshCoopStage() }
        }
        .onChange(of: model.options.out) { _ in
            if model.options.coop { refreshCoopStage() }
        }
    }

    private var helpBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("把作者交来的 Word 文稿或 LaTeX 源文件，转成符合《逻辑学研究》模板的初排稿。")
                .font(.callout)
            Text("会自动处理：公式（OMML→LaTeX）、表格（→tabular）、内嵌图片（直接提取原图）、"
                 + "矢量图形（检出并置占位）、脚注、引用环境、参考文献"
                 + "（默认只做引用库只读比对；需要《文献核实任务书》请用「AI 协同初排（两段式）」"
                 + "第一段，或在「设置」里配好 AI 勾「用 AI 处理参考文献」在本机直连预跑，"
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

    /// 第 132 条：核实成本闸门的**当前生效值**提示（只显示，不在这里改）。
    ///
    /// 为什么挨着「生成核实任务书」放：编辑勾这个开关时就该知道「这次会核多少条」。
    /// 超上限时引擎会**先退出 2**（有意为之的成本闸门）——这句提醒必须写在开关旁边，
    /// 而不是等跑完去日志里才看见（退出 2 看起来很像程序错误）。
    @ViewBuilder private var verifyGateHint: some View {
        let db = BibDbFeature.existingDBPath()
        VStack(alignment: .leading, spacing: 2) {
            Text("核实范围：上限 \(bibdb.verifyMaxSummary)"
                 + (bibdb.verifyAllRefs ? "；已允许全量核实" : "；超过上限会先退出 2（成本闸门）")
                 + (bibdb.verifyPlan ? "；**先算账**（本次只打印计划，不真正核实）" : ""))
                .font(.caption)
                .foregroundStyle(bibdb.verifyPlan ? Color.orange : Color.secondary)
            Text(bibdb.lookupEnabled
                 ? (db == nil
                    ? "引用库：已开启查库，但库文件不存在（引擎会跳过查库，不影响出稿）。"
                    : "引用库：核实前先查库（命中已核实条目的键不调检索/AI）。")
                 : "引用库：本次**不查库**（--no-bibdb）。")
                .font(.caption)
                .foregroundStyle(.tertiary)
            Text("以上值来自「引用库 → 引用库设置」，点击转换时现取。")
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(.leading, 18)
        .fixedSize(horizontal: false, vertical: true)
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
            if model.options.coop {
                // 第 55 批：协同模式下直连 API 全程不生效（`CoopFeature.prepare` 强制关闭），
                // 不许让「已配置 AI」这行绿色文案误导编辑以为本次会调 API。
                Text("⚠ 已勾「AI 协同初排（两段式）」：本次**不传任何 --llm-* 参数**，"
                     + "直连 API 不生效（两段式全程不调用 API；外部 chat 由编辑自己操作）。")
                    .foregroundStyle(.orange)
            }
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

    // MARK: - 第 55 批：两段式「AI 协同初排」

    /// 勾上两段式后、开关下面那行说明。**必须说清三件事**（用户 2026-10-19 口径）：
    /// 软件零上传/零费用；第一段不交付；直连 API 开关在协同模式下不生效。
    private var coopToggleHintText: String {
        "软件不上传、不调用 API、零费用：第一段只在本机生成暂存目录 + 任务书，"
        + "任务书由编辑自己交给外部 chat（豆包/千问/DeepSeek 等）核实；"
        + "把回复导入后跑第二段，**这时才交付**。\n"
        + "⚠ 回填**必须含两部分**：① 第三节完整 BibTeX（一个代码块）② 附一 JSON 逐项；"
        + "可放同一文件，也可拆成多个文件/目录甚至粘贴剪贴板，一起导入即可。\n"
        + "⚠ 勾着两段式时「用 AI 处理参考文献」的直连 API 通道本次不生效（不传 --llm-*）；"
        + "「只扫描源稿」不受影响。"
    }

    /// 还没找到暂存目录时的一行说明。
    private var coopNoStageText: String {
        "尚未初排：点上方「开始第一段（出任务书）」→ 打开任务书去外部 chat 核实 → "
        + "回来「添加文件…/添加目录…/从剪贴板导入」→ 运行第二段。第一段跑完前不会写最终交付目录。"
    }

    /// 「一键修改产物」勾选框旁边必须写清的限制（引擎批 57 现状，不夸大）。
    private var coopApplyHintText: String {
        "勾上 = 引擎 `--coop-apply`：把**通过校验的建议**落到产物——文献字段（BIB）→ reference.bib；"
        + "元数据（META）→ 输出 .tex 的 `\\my*` 字段；纯引用脚注匹配（FN，仅 citation_unmatched）"
        + "→ .tex 正文 `\\parencite`。写前分别备份 `reference.bib.ai前` / `<名>.tex.ai前`（可恢复）；"
        + "编辑侧已有值不覆盖，混合型脚注 / 文献区原文等仍**只列清单待人工**。\n"
        + "⚠ 这个决定必须在跑第二段**之前**做：引擎 phase=done 后同一回填重跑是幂等早退，"
        + "看到建议再回头勾已经晚了。"
    }

    /// 建议清单的时间点说明 —— 建议 jsonl 是**第二段**才生成的，别在导入时就承诺能看到。
    private var coopSuggestionsTimingText: String {
        "建议清单（`_AI建议.md/.jsonl`）在第二段跑完后才生成；现在看不到是正常的。"
        + "第二段成功后这里会按引擎统计显示「文献回填 x/y」与「低置信回复」计数。"
    }

    /// 回填导入区的一句话说明（多选/目录/剪贴板都会一起交给引擎）。
    private var coopRepliesHintText: String {
        "多个文件 / 目录 / 剪贴板草稿都会**一起**交给引擎（按下面清单顺序逐个传 --coop-replies），"
        + "不会再折叠成父目录。回填=完整 BibTeX + 附一 JSON 两部分，缺一部分会导致漏回填。"
    }

    /// 已交付状态下的一行提示。
    private var coopDeliveredText: String {
        "已交付。若还要改回填内容或换了源稿：重新跑第一段会重置暂存目录状态；"
        + "对同一份回填重跑第二段是幂等早退（不会重复应用）。"
    }

    /// 两段式的状态区：状态行 + 按钮组（按钮可见性完全由 `coopStage.phase` 决定）。
    @ViewBuilder private var coopPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider()
            Text("AI 协同初排（两段式）").font(.callout).bold()
            // 批 62 入口收敛：任务书从此只有“两段式第一段”这一个界面入口（plus ⑪ 直连 AI）。
            Text("文献核实任务书 = 两段式第一段产物；普通转换如需任务书请用两段式或 CLI "
                 + "--verify-refs（高级）。")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !model.coopFailure.isEmpty {
                Text(model.coopFailure)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // ---------- 第 59 批：全文 AI 核查（任务书附正文；默认不勾） ----------
            // 默认不勾 → 一个参数都不加（默认 argv 与批 58 逐字节相同）。
            Divider()
            Toggle("全文 AI 核查（任务书附正文）", isOn: $model.options.coopFulltext)
                .toggleStyle(.checkbox)
            if model.options.coopFulltext {
                Picker("核查范围", selection: $model.options.coopFulltextScope) {
                    ForEach(ConvertFeature.FulltextScope.allCases, id: \.self) { scope in
                        Text(scope.title).tag(scope)
                    }
                }
                .pickerStyle(.radioGroup)
                .labelsHidden()
                .padding(.leading, 18)
            }
            Text("默认不勾：任务书不含正文，与从前一致（软件零上传、零费用）。\n"
                 + "勾上后引擎会把正文/引用上下文写进本机《文献核实任务书》（范围见上）："
                 + "文件只留在本机，由你自己复制给外部 chat 做全文核查——软件仍不上传、不调用 API。"
                 + "已获授权，不再弹二次确认。\n"
                 + "⚠ 只对「开始第一段（出任务书）」生效；第二段续跑与交付不受影响。")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let stage = model.coopStage {
                coopStagePanel(stage)
            } else {
                Text(coopNoStageText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .background(Color.secondary.opacity(0.07))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// 暂存目录状态面板。**第一段状态只叫「暂存目录」**，绝不写成交付目录。
    @ViewBuilder private func coopStagePanel(_ stage: CoopFeature.StageState) -> some View {
        Text("状态：" + CoopFeature.stageStatusText(stage))
            .font(.callout).bold()
        if model.coopCandidates > 1 {
            Text("发现 \(model.coopCandidates) 个匹配的暂存目录，已选最近修改的一个：\(stage.stageDir)")
                .font(.caption).foregroundStyle(.orange)
        }
        if stage.phase == "done" {
            coopDeliveredPanel(stage)
        } else {
            coopPendingPanel(stage)
        }
    }

    /// 已交付：只显示**最终目录**与协同记录（暂存目录可以仍在，但不作为交付物展示）。
    ///
    /// 第 56 批：成功摘要改读 `state.stats`（旧状态回退 record/旧 state），口径固定为
    /// 「文献回填 x/y（并入 z 条，未回填 n）｜低置信回复 a（应用 b / 仅记录待人工 c）」；
    /// 文献回填 0/N 时**橙色警告**（用户真测就栽在「只导入 JSON、丢了 BibTeX」却看到全绿）。
    @ViewBuilder private func coopDeliveredPanel(_ stage: CoopFeature.StageState) -> some View {
        Text("交付目录：\(stage.deliveredTo.isEmpty ? stage.finalDir : stage.deliveredTo)")
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
        HStack(spacing: 10) {
            Button("打开交付目录") { openPath(stage.deliveredTo.isEmpty ? stage.finalDir : stage.deliveredTo) }
            if !model.coopRecordPath.isEmpty {
                Button("打开协同记录") { openPath(model.coopRecordPath) }
            }
            if let result = model.coopResult,
               !result.suggestionsMarkdownPath.isEmpty,
               FileManager.default.fileExists(atPath: result.suggestionsMarkdownPath) {
                Button("打开建议清单") { openPath(result.suggestionsMarkdownPath) }
            }
        }
        if let result = model.coopResult {
            Text(result.summaryLine)
                .font(.callout).bold()
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if let warning = result.zeroBibWarning {
                Text("⚠ " + warning)
                    .font(.callout).bold()
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if result.stats.legacy {
                Text("（旧状态文件没有 stats 字段：部分计数为估算，准确数字见协同记录与建议清单。）")
                    .font(.caption).foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        Text(coopDeliveredText)
            .font(.caption).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// 待人工核实（ph1 / ph2 未完成）：暂存目录 + 任务书 + 回填导入（多文件/目录/剪贴板）
    /// + 已选清单（可移除/清空）+ 一键修改产物 + 第二段。
    @ViewBuilder private func coopPendingPanel(_ stage: CoopFeature.StageState) -> some View {
        Text("暂存目录（**未交付**，不是最终产物）：\(stage.stageDir)")
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
        HStack(spacing: 10) {
            if !stage.taskbook.isEmpty {
                Button("打开任务书") { openPath(stage.taskbook) }
            }
            Button("打开暂存目录") { openPath(stage.stageDir) }
            if model.running {
                ProgressView().controlSize(.small)
            }
        }
        Divider().padding(.vertical, 2)
        Text("回填导入").font(.callout).bold()
        HStack(spacing: 8) {
            Button("添加文件…") { addReplyFiles() }
            Button("添加目录…") { addReplyDirectory() }
            Button("从剪贴板导入") { importClipboard() }
            if !model.replies.isEmpty {
                Button("全部清除") { clearReplies() }
            }
            Spacer()
        }
        .disabled(model.running)
        Text(model.replies.summary)
            .font(.caption).bold()
            .foregroundStyle(model.replies.isEmpty ? Color.secondary : Color.primary)
        ForEach(Array(model.replies.items.enumerated()), id: \.element.path) { index, item in
            HStack(spacing: 6) {
                Text(CoopFeature.isClipboardDraft(item.path) ? "📋" : (item.isDirectory ? "📁" : "📄"))
                    .font(.caption)
                Text(item.displayName).font(.caption)
                Text(item.path)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                Spacer(minLength: 4)
                Button("移除") { removeReply(at: index) }
                    .buttonStyle(.borderless)
                    .disabled(model.running)
            }
            .padding(.leading, 12)
        }
        Text(coopRepliesHintText)
            .font(.caption).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        Toggle("一键修改产物（--coop-apply；默认不勾）", isOn: $model.coopApply)
            .toggleStyle(.checkbox)
        Text(coopApplyHintText)
            .font(.caption).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        Button {
            runSecondPhase()
        } label: {
            Label("运行第二段（续跑 → 编译 → 交付）", systemImage: "square.and.arrow.down.on.square")
        }
        .disabled(model.running
                  || !CoopFeature.canRunSecondPhase(replyCount: model.replies.count, stage: stage))
        if let hint = CoopFeature.secondPhaseDisabledHint(replyCount: model.replies.count, stage: stage) {
            Text(hint)
                .font(.caption)
                .foregroundStyle(model.replies.isEmpty ? Color.orange : Color.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        Text(coopSuggestionsTimingText)
            .font(.caption).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
        Text("第一段不写最终交付目录；第二段成功后才交付。失败时最终目录保持原样。")
            .font(.caption).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// 扫描当前源稿对应的暂存目录并刷新状态（勾开关 / 改源稿 / 改输出目录 / 跑完一段时调用）。
    private func refreshCoopStage() {
        guard !model.running else { return }
        let source = CoopFeature.normalizePath(model.options.source)
        let out = CoopFeature.normalizePath(model.options.out)
        guard !source.isEmpty, !out.isEmpty else {
            model.coopStage = nil
            model.coopCandidates = 0
            model.coopResult = nil
            return
        }
        apply(CoopFeature.refresh(outRoot: out, source: source))
    }

    /// 把一次扫描结果写进界面状态（主线程；IO 已在后台线程完成）。
    private func apply(_ refresh: CoopFeature.StageRefresh) {
        model.coopStage = refresh.selected
        model.coopCandidates = refresh.stages.count
        model.coopSuggestions = refresh.suggestions
        model.coopResult = refresh.result
        model.coopRecordPath = refresh.recordExists ? (refresh.selected?.recordPath ?? "") : ""
    }

    /// 在访达里打开文件/目录（失败不静默：写进运行日志）。
    private func openPath(_ path: String) {
        guard !path.isEmpty else {
            log.append("⚠ 要打开的路径为空。\n")
            return
        }
        let url = URL(fileURLWithPath: path)
        if FileManager.default.fileExists(atPath: path) {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        } else {
            log.append("⚠ 路径不存在：\(path)\n")
        }
    }

    /// 「添加文件…」：支持多选；**每个文件各算一项**（不再折叠父目录、不复制临时目录）。
    /// 引擎批 56 起 `--coop-replies` 可重复，界面按已选顺序为每个路径各传一个 flag。
    private func addReplyFiles() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.title = "选择一个或多个 AI 回复文件（可按住 ⌘ 多选；JSON 与 BibTeX 可分开选）"
        guard panel.runModal() == .OK else { return }
        var added = 0
        var duplicated = 0
        for url in panel.urls {
            let item = CoopFeature.ReplyItem(path: url.path, isDirectory: false)
            if model.replies.add(item) { added += 1 } else { duplicated += 1 }
        }
        model.coopFailure = ""
        log.append("回填：新增 \(added) 个文件"
                   + (duplicated > 0 ? "（\(duplicated) 个已在清单里，未重复添加）" : "")
                   + "；\(model.replies.summary)\n")
    }

    /// 「添加目录…」：目录整体作为一个路径（引擎会读入该目录下全部可读文本）。
    private func addReplyDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.title = "选择一个 AI 回复目录（目录下全部 .md/.txt/.bib/.json 会被一起读入）"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        let item = CoopFeature.ReplyItem(path: url.path, isDirectory: true)
        model.coopFailure = ""
        if model.replies.add(item) {
            log.append("回填：已添加目录 \(url.path)；\(model.replies.summary)\n")
        } else {
            log.append("回填：该目录已在清单里，未重复添加。\n")
        }
    }

    /// 「从剪贴板导入」：读 UTF-8 文本 → 存成临时 `.md` → 加入已选清单。
    /// 空/非文本给红字提示（不静默）；重复粘贴生成多个临时文件（序号递增）。
    private func importClipboard() {
        guard let text = CoopFeature.clipboardText(NSPasteboard.general.string(forType: .string)) else {
            model.coopFailure = "剪贴板里没有可导入的文本：先把外部 chat 的回复复制到剪贴板再点这里。"
            return
        }
        let stamp = CoopFeature.clipboardTimestamp()
        model.clipboardSequence += 1
        let path = CoopFeature.clipboardDraftPath(directory: NSTemporaryDirectory(),
                                                  timestamp: stamp,
                                                  index: model.clipboardSequence)
        do {
            try text.write(toFile: path, atomically: true, encoding: .utf8)
        } catch {
            model.coopFailure = "剪贴板文本写入临时文件失败：\(error.localizedDescription)"
            return
        }
        model.replies.add(CoopFeature.ReplyItem(path: path, isDirectory: false))
        model.coopFailure = ""
        log.append("回填：剪贴板文本已存成临时文件并加入清单：\(path)\n")
    }

    /// 移除已选清单里的一项（剪贴板草稿顺带删掉临时文件；用户自己选的文件**不删**）。
    private func removeReply(at index: Int) {
        guard let item = model.replies.remove(at: index) else { return }
        if CoopFeature.isClipboardDraft(item.path) {
            try? FileManager.default.removeItem(atPath: item.path)
        }
        log.append("回填：已移除 \(item.path)\n")
    }

    /// 清空已选清单（只清清单与剪贴板临时文件，不碰用户文件）。
    private func clearReplies() {
        let drafts = model.replies.items
            .filter { CoopFeature.isClipboardDraft($0.path) }
            .map { $0.path }
        let count = model.replies.count
        model.replies.clear()
        for path in drafts { try? FileManager.default.removeItem(atPath: path) }
        model.coopFailure = ""
        log.append("回填：已清空已选清单（\(count) 项；\(drafts.count) 个剪贴板临时文件已删）。\n")
    }

    /// 统一的运行外壳：后台跑引擎 → 后台扫描暂存目录 → 主线程收尾。
    /// （第 55 批把「跑完第一段后刷新状态」集中在这里，普通转换也走同一条路。）
    private func startRun(_ o: ConvertFeature.Options, knobs: AIEngineKnobs,
                          onFinish: @escaping (Int32) -> Void) {
        // U3：每次转换一个取消句柄；`Shell.run` 把 python 子进程注册进 logger.cancel。
        let cancel = RunCancel()
        model.cancel = cancel
        model.running = true
        let logger = Logger(store: log, startedAt: Date(), cancel: cancel)
        let m = model
        let coop = o.coopPhase
        let source = o.source
        let outRoot = o.out
        DispatchQueue.global(qos: .userInitiated).async {
            let status = ConvertFeature.run(options: o, knobs: knobs, log: logger)
            // 第 55 批：协同模式跑完后在**后台线程**读文件（JSON/建议清单都很小），
            // 主线程只做赋值 —— 不在主线程做文件 IO。
            var refresh: CoopFeature.StageRefresh? = nil
            if coop != .off {
                refresh = CoopFeature.refresh(outRoot: outRoot, source: source)
            }
            DispatchQueue.main.async {
                m.running = false
                if let refresh = refresh { self.apply(refresh) }
                onFinish(status)
            }
        }
    }

    private func appendFailure(_ text: String) {
        let tail = CoopFeature.logTail(log.text)
        model.coopFailure = text + (tail.isEmpty ? "" : "\n日志尾部：\n" + tail)
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
        // 第 132 条：核实成本闸门 + 引用库开关同样**现取**（设置页改完立刻生效）。
        // `verifyMaxValue` 对非法输入返回 nil = 不传该参数（引擎用默认 120；界面另有红字）。
        o.verifyMax = bibdb.verifyMaxValue
        o.verifyPlan = bibdb.verifyPlan
        o.verifyAllRefs = bibdb.verifyAllRefs
        o.noBibdb = !bibdb.lookupEnabled
        // 手动库文件只在「查库开着 + 文件真的存在」时显式传 --bibdb-path：
        // 指一个不存在的库只会让引擎多打一条告警，不如不传（自动探测/ SLCN_NOTES 兜底）。
        o.bibdbPath = (bibdb.lookupEnabled ? (BibDbFeature.existingDBPath() ?? "") : "")
        // 第 55 批：AI 预算闸（直连 API 通道；默认值不传、协同模式不传）。
        // 非法输入 → nil = 不传该参数（引擎用默认；设置页另有红字）。
        o.aiBudgetTokens = budget.articleValue
        o.aiBatchBudgetTokens = budget.batchValue
        // 批 63：全文核查的字符上限 / 自动应用也**现取**（同口径；只约束 --llm-fulltext）。
        // `fulltextMaxCharsValue`：nil = 不传（引擎默认 40000）；0 = 显式不限。
        o.llmFulltextMaxChars = budget.fulltextMaxCharsValue
        o.llmFulltextApply = budget.fulltextAutoApply
        // 批 63 B2：元数据文件选的是老式 .doc → 先转成临时 .docx 再交给引擎。
        // 纯判定/路径在 ConvertFeature.metadataConversionPlan（headless 有断言）；
        // 这里只做「调 textutil + 失败提示」，临时文件在本次运行结束时清理。
        var metadataTempPath: String? = nil
        if let plan = ConvertFeature.metadataConversionPlan(o.metadata) {
            guard let temp = convertMetadataDoc(plan) else { return }
            o.metadata = plan.output
            metadataTempPath = temp
        }
        // 两个「请求体旋钮」（思考模式 / 输出上限）同样**现取**：与上面三项同一口径，
        // 设置页改完立刻生效，不必先点「保存」（保存只影响下次启动读回的值）。
        // ⚠️ 必须在主线程上取好再传：@Published 的值属于主线程，而 run() 在后台队列
        //    上执行（AIEngineKnobs 是值类型，跨线程传是安全的）。
        let knobs = settings.aiEngineKnobs
        // 第 55 批：两段式。**扫描模式忽略协同**（只扫描源稿不转换，勾着也按普通扫描走）。
        let coopOn = o.coop && !scanOnly
        o = CoopFeature.prepare(o, phase: coopOn ? .first : .off)
        model.coopFailure = ""
        if coopOn { model.coopSuggestions = nil }   // 新一轮初排：旧建议不算数
        startRun(o, knobs: knobs) { status in
            // 批 63 B2：临时 .docx 只服务本次运行，跑完（含失败/取消）立刻清理。
            if let temp = metadataTempPath {
                try? FileManager.default.removeItem(atPath: temp)
            }
            guard coopOn else { return }
            if status == 0 {
                log.append("（界面）第一段完成：状态「待人工核实（**未交付**）」。"
                           + "下一步：打开任务书 → 外部 chat 核实 → 导入回复 → 运行第二段。\n")
            } else if status != Shell.cancelledStatus {
                appendFailure("第一段失败（退出码 \(status)）；最终交付目录本次未写。")
            }
        }
    }

    /// 批 63 B2：把用户选的 `.doc` 用 macOS 自带 `textutil` 转成临时 `.docx`。
    ///
    /// - Returns: 临时文件路径（供运行结束清理）；转换失败/启动失败返回 nil 并**写日志**
    ///   提示改用 `.docx` 或 `.tex`（本次直接不跑，不把二进制 .doc 交给引擎）。
    ///   原文件一个字节都不动；**不做 Word→LaTeX 全文转换**（只取元数据）。
    private func convertMetadataDoc(_ plan: ConvertFeature.MetadataConversion) -> String? {
        let result = Shell.capture("/usr/bin/textutil", plan.textutilArguments)
        let wrote = FileManager.default.fileExists(atPath: plan.output)
        guard let result, result.status == 0, wrote else {
            let why = result.map { "退出码 \($0.status)" } ?? "无法启动 /usr/bin/textutil"
            try? FileManager.default.removeItem(atPath: plan.output)   // 清掉可能的半成品
            log.append("元数据文件：.doc 转 .docx 失败（\(why)）。\n"
                       + "  请把该文件另存为 .docx 或 .tex 后在「元数据文件」里重选；本次未开始转换。\n")
            return nil
        }
        log.append("元数据文件：.doc 已转成临时 .docx（原文件不动）：\(plan.output)\n")
        return plan.output
    }

    /// 第二段：用当前暂存目录 + 已选回填（每个路径一个 --coop-replies）续跑并交付。
    ///
    /// 第 56 批要点：
    ///   · 回填 = `model.replies` 里的**每一项**（文件/目录/剪贴板可混合、按顺序），
    ///     不再折叠成父目录；空清单由按钮 disabled 拦住，这里再兜一次并说明原因。
    ///   · `--coop-apply` 必须在**启动前**由勾选框决定（引擎 phase=done 后重跑是幂等早退）。
    ///   · `--clean` 随页面「清理编译中间文件」开关走（ph2 也生效，否则交付目录会留中间文件）。
    private func runSecondPhase() {
        guard let stage = model.coopStage else {
            model.coopFailure = "还没有可续跑的暂存目录：请先运行第一段。"
            return
        }
        guard !model.options.source.isEmpty, !model.options.out.isEmpty else {
            model.coopFailure = "第二段需要源文件与输出目录（与第一段相同）。"
            return
        }
        let paths = model.replies.paths
        guard !paths.isEmpty else {
            model.coopFailure = CoopFeature.secondPhaseDisabledHint(replyCount: 0, stage: stage)
                ?? "尚未选择任何回填。"
            return
        }
        var o = model.options
        o.scanOnly = false
        o.coopRepliesPaths = paths
        o.coopStage = stage.stageDir
        o.coopApply = model.coopApply     // ⚠ 运行前定格：之后改勾选不影响本次
        o = CoopFeature.prepare(o, phase: .second)
        model.coopFailure = ""
        model.coopSuggestions = nil       // 新摘要等第二段成功后从 state.stats/jsonl 读
        model.coopResult = nil
        startRun(o, knobs: settings.aiEngineKnobs) { status in
            if status == 0 {
                if let result = model.coopResult {
                    log.append("（界面）第二段完成：已交付。\(result.summaryLine)\n")
                    if let warning = result.zeroBibWarning {
                        log.append("（界面）⚠ \(warning)\n")
                    }
                } else {
                    log.append("（界面）第二段完成：已交付（未读到 state.stats，详见协同记录）。\n")
                }
                if model.coopRecordPath.isEmpty {
                    log.append("（界面）⚠ 已交付但没找到 `_AI协同记录.md`，请查看交付目录。\n")
                }
            } else if status != Shell.cancelledStatus {
                appendFailure("第二段失败（退出码 \(status)）；最终交付目录保持原样（引擎保证不 promote）。")
            }
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
