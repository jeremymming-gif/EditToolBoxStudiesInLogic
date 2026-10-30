import SwiftUI
import UniformTypeIdentifiers

/// 「初排转换」页：选源文件 → 选输出目录 → 转换出符合期刊模板的 LaTeX 初排稿。
///
/// 界面刻意做得简单：**必填的只有源文件和输出目录**，其余（metadata/bib/refs/
/// 语种/模板）都收在「高级」里。因为多数情况工具能自动判断
/// （语种、模板、参考文献附件都会自动识别），不需要编辑每次填一堆东西。

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

    @StateObject var model = ConvertModel()
    /// 责编名单（采集 + 自定义 + 默认），见 EditorStore.swift
    @StateObject var editors = EditorStore()

    // MARK: - 文案常量（View 里只写 `Text(Self.xxxText)`）
    //
    // 批63b 发布修复 R2（2026-10-20）：GitHub macos-14 runner 的旧 Xcode 在 ViewBuilder 里
    // 对「多段字符串拼接 + 修饰符链」的类型检查代价比新编译器高一个量级（CI 报
    // `aiExtraTogglesSection` 超时）。因此所有长文案 / 多段拼接**一律移出 View body**，
    // View 里只留下 `Text(Self.xxxText)` 这种零推导成本的写法。
    // **改文案改这里**；界面上不要再写拼接字符串，也不要再在 View 里做长表达式。
    private static let dragHintText: String =
        "源文件与输出目录都可以直接从访达拖进来（高级选项里的路径同理）"
    static let forceHintText: String =
        "目标目录里混着非本工具的文件时（例如手工 latexmk 留下的 "
        + ".fdb_latexmk/.fls），默认会被拦下以防误伤已有稿件目录；"
        + "勾选后照常写入：同名产物被覆盖，目录里的其它文件不会被删。"
    static let keepBibHintText: String =
        "勾上 = 只出《文献核实任务书》（引擎侧 --no-ai-merge）：AI 结果不写进产物，"
        + "reference.bib 一个字节都不动。不勾时 AI 结果会并入交付的 reference.bib，"
        + "合并之前的那份会先备份成 reference.bib.ai前（要回滚就把它覆盖回去）。"
    static let llmFulltextHintText: String =
        "⚠ 勾上后会产生费用：引擎把稿件正文按片直接发送到"
        + "「设置」里配置的 AI 端点，每片一次请求；不勾（默认）时正文一个字节都不外发。\n"
        + "范围：通过批 57 安全闸的 BIB/META/FN 建议会**自动应用**"
        + "（先备份 .ai前、0 条不写、冲突拒绝；可在「设置 → 全文核查结果自动应用」关闭），"
        + "《…_AI全文复核.md》报告照常生成；两段式协同模式下本轮不生效。\n"
        + "上限：「设置」里的「API 核查字符上限」默认 40000 字符（0 = 不限）；"
        + "「单篇预算」（默认 30000 tokens）不够时请按日志提示调大。\n"
        + "⚠ 前提：需先在「设置」页配置端点；未配置时引擎直接退出 2（不会发出任何请求）。"
    // 批67（B-017）：旧「正文信息覆盖」不再是常驻入口——只在**两段式**模式作兜底显示
    // （普通转换的唯一人工元数据入口是「填写元数据…」表单）。
    static let inlineOverrideHeaderText: String =
        "正文信息覆盖（折叠兜底；表单优先，只覆盖第一作者/题名/摘要/单位/邮箱；"
        + "多作者请填上面的表单）"
    static let inlineOverrideWarningText: String =
        "⚠ 手工覆盖的生效取决于引擎版本：用旧版引擎时日志会提示"
        + "「尚不支持 overrides 参数」，此时不影响转换本身。"
    static let metadataPlaceholderWordText: String =
        "留空自动找英文元数据第二源；.docx 可显式指定（.doc 请先另存为 .docx）"
    static let metadataPlaceholderLatexText: String =
        "留空用人工填写或同目录 .tex；.doc/.docx 自动识别已取消（请用「填写元数据…」）"
    static let metadataSourcesTitleText: String = "元数据 / 补充信息（可选）"
    static let metadataSourcesHintText: String =
        "可一次选择多份 .docx / .tex / .json；顺序就是合并顺序（先到先得）：只补正文"
        + "缺失项、不覆盖正文已有内容；表单里填过的字段最高优先。"
    private static let metadataSourcesIgnoredLogText: String =
        "元数据 / 补充信息：两段式协同与「只扫描源稿」不下传补充信息，本次忽略。\n"
    private static let helpIntroText: String =
        "把作者交来的 Word 文稿或 LaTeX 源文件，转成符合《逻辑学研究》模板的初排稿。"
    private static let helpAutoText: String =
        "会自动处理：公式（OMML→LaTeX）、表格（→tabular）、内嵌图片（直接提取原图）、"
        + "矢量图形（检出并置占位）、脚注、引用环境、参考文献"
        + "（默认只做引用库只读比对；需要《文献核实任务书》请用「AI 协同初排（两段式）」"
        + "第一段，或在「设置」里配好 AI 勾「用 AI 处理参考文献」在本机直连预跑，"
        + "并把通过安全校验的结果并入 reference.bib）。"
    private static let helpInitialLayoutNoteText: String =
        "⚠ 工具只做初排。数字前缀、目录顺序在三校/蓝样阶段再加。"
    static let metadataFormTitleText: String =
        "元数据（推荐人工填写；值直接写入交付产物，留空 = 不覆盖）"
    // B-017：普通转换的常驻上限说明（与表单内、docs 同口径；明细来自纯逻辑常量）。
    static let metadataFormLimitText: String = MetadataForm.limitSummary
    static let metadataFormCoopHintText: String =
        "两段式：表单随**第一段**写进初排并在暂存状态记下 sha256；第二段沿用初排元数据"
        + "（第二段里再改表单不生效）。表单填了的字段优先于「正文信息覆盖」折叠兜底。"
    static let metadataFormPriorityHintText: String =
        "表单优先于 `.tex` 元数据文件；临时 JSON 只在本次运行有效，跑完即删。"
    // AI 说明文案（批63b R2：View 本体在 `ConvertAiHintViews.swift`，文案常量留在本文件，
    // 因为既有源码级断言（tools/ai_settings_test i6-hint）按 ConvertTab 源码检查这些句子）。
    static let aiCoopInactiveText: String =
        "⚠ 已勾「AI 协同初排（两段式）」：本次**不传任何 --llm-* 参数**，"
        + "直连 API 不生效（两段式全程不调用 API；外部 chat 由编辑自己操作）。"
    static let aiConfiguredTailText: String =
        " —— 转换时加 --verify-refs：在本机直连这个端点，按《文献核实任务书》的同一份"
        + "规范逐条生成修订后的 bib，并把通过安全校验的条目并入交付的 reference.bib"
        + "（合并前先备份成 reference.bib.ai前；理由与逐条记录写进《…_文献核实.md》"
        + "的「六、合并记录」）。"
    static let aiWebSearchOnText: String =
        "🌐 本次已开启联网检索（通道 responses + web_search）：引擎会带上"
        + "服务端检索工具，并要求模型先查官方来源、把打开的页面 URL 写进每条上方的 "
        + "`% 来源：` 标注，查不到的字段照旧省略并写 `% ⚠ 待核实：…`。"
    static let aiWebSearchCaveatText: String =
        "⚠ 但「勾了开关」不等于「端点真的会查」：内置 `web_search` 是否真检索"
        + "由端点与模型决定——DeepSeek 需 `deepseek-v4-pro`（`deepseek-flash` "
        + "仍会静默忽略该工具）。请到「设置」页点「测试连接 / 体检」看 ⑤ 检索探针"
        + " 的实测结论；本次跑完引擎会在日志与「六、合并记录」里报检索证据"
        + "（0 次 = 只能凭记忆）——本工具绝不假装已联网。"
    static let aiResponsesNoSearchText: String =
        "⚠ 通道是 responses，但没开「联网检索」：本次模型只能凭记忆作答"
        + "（任务书里同样写明）。要真联网请在「设置」页勾上「联网检索」，"
        + "并先跑一次 ⑤ 检索探针确认端点支持。"
    static let aiChatNoSearchText: String =
        "⚠ 本次不是联网核实（通道 chat、未启用检索）：模型只能凭记忆作答。"
        + "卷/期/页码/出版地想真查官方来源，请到「设置」页把「通道」改成 responses、"
        + "勾上「联网检索」，并先跑一次 ⑤ 检索探针确认端点支持（DeepSeek 的 "
        + "deepseek-flash 仍忽略检索工具、deepseek-v4-pro 才支持，探针会如实实测）。"
    static let aiSearchRefsText: String =
        "🔎 本次会先用 DeepSeek **独立检索**（Search API）逐条查参考文献证据，"
        + "把证据写进任务书并注入 AI 提示词；并入前会校验 `% 来源：` URL "
        + "是否真在证据里（按次计费 ¥5/1000 次成功；默认关）。"
    static let aiSafetyText: String =
        "AI 调用失败、回复被截断、或没有任何一条通过安全校验时，"
        + "reference.bib 一个字节都不会改（详见下方日志）。"
        + "无论哪种通道，并完之后都必须人工过一遍。"
    static let aiNotConfiguredText: String =
        "⚠ 未配置 AI，本次只会生成《文献核实任务书》（不会调用任何模型，"
        + "reference.bib 一个字节不改）。请到「设置」标签页填写 API 地址与模型名，"
        + "「测试连接」可用后再回来勾选。"
    private static let coopSecondPhaseMetadataNoteText: String =
        "元数据：第二段沿用初排元数据（从暂存状态继承），表单里后来的改动不生效；"
        + "要改元数据请重跑第一段。\n"
    private static let scanOnlyMetadataIgnoredLogText: String =
        "元数据：只扫描源稿不产出交付物，本次不下传人工填写内容"
        + "（用「开始转换」跑才生效）。\n"
    private static let coopFirstPhaseDoneLogText: String =
        "（界面）第一段完成：状态「待人工核实（**未交付**）」。"
        + "下一步：打开任务书 → 外部 chat 核实 → 导入回复 → 运行第二段。\n"
    static let coopTaskbookNoteText: String =
        "文献核实任务书 = 两段式第一段产物；普通转换如需任务书请用两段式或 CLI "
        + "--verify-refs（高级）。"
    static let coopToggleHintText: String =
        "软件不上传、不调用 API、零费用：第一段只在本机生成暂存目录 + 任务书，"
        + "任务书由编辑自己交给外部 chat（豆包/千问/DeepSeek 等）核实；"
        + "把回复导入后跑第二段，**这时才交付**。\n"
        + "⚠ 回填**必须含两部分**：① 第三节完整 BibTeX（一个代码块）② 附一 JSON 逐项；"
        + "可放同一文件，也可拆成多个文件/目录甚至粘贴剪贴板，一起导入即可。\n"
        + "⚠ 勾着两段式时「用 AI 处理参考文献」的直连 API 通道本次不生效（不传 --llm-*）；"
        + "「只扫描源稿」不受影响。"
    static let coopNoStageText: String =
        "尚未初排：点上方「开始第一段（出任务书）」→ 打开任务书去外部 chat 核实 → "
        + "回来「添加文件…/添加目录…/从剪贴板导入」→ 运行第二段。第一段跑完前不会写最终交付目录。"
    static let coopApplyHintText: String =
        "勾上 = 引擎 `--coop-apply`：把**通过校验的建议**落到产物——文献字段（BIB）→ reference.bib；"
        + "元数据（META）→ 输出 .tex 的 `\\my*` 字段；纯引用脚注匹配（FN，仅 citation_unmatched）"
        + "→ .tex 正文 `\\parencite`。写前分别备份 `reference.bib.ai前` / `<名>.tex.ai前`（可恢复）；"
        + "编辑侧已有值不覆盖，混合型脚注 / 文献区原文等仍**只列清单待人工**。\n"
        + "⚠ 这个决定必须在跑第二段**之前**做：引擎 phase=done 后同一回填重跑是幂等早退，"
        + "看到建议再回头勾已经晚了。"
    static let coopSuggestionsTimingText: String =
        "建议清单（`_AI建议.md/.jsonl`）在第二段跑完后才生成；现在看不到是正常的。"
        + "第二段成功后这里会按引擎统计显示「文献回填 x/y」与「低置信回复」计数。"
    static let coopRepliesHintText: String =
        "多个文件 / 目录 / 剪贴板草稿都会**一起**交给引擎（按下面清单顺序逐个传 --coop-replies），"
        + "不会再折叠成父目录。回填=完整 BibTeX + 附一 JSON 两部分，缺一部分会导致漏回填。"
    static let coopDeliveredText: String =
        "已交付。若还要改回填内容或换了源稿：重新跑第一段会重置暂存目录状态；"
        + "对同一份回填重跑第二段是幂等早退（不会重复应用）。"
    static let coopFulltextHintText: String =
        "默认不勾：任务书不含正文，与从前一致（软件零上传、零费用）。\n"
        + "勾上后引擎会把正文/引用上下文写进本机《文献核实任务书》（范围见上）："
        + "文件只留在本机，由你自己复制给外部 chat 做全文核查——软件仍不上传、不调用 API。"
        + "已获授权，不再弹二次确认。\n"
        + "⚠ 只对「开始第一段（出任务书）」生效；第二段续跑与交付不受影响。"
    static let coopDeliveredLegacyNoteText: String =
        "（旧状态文件没有 stats 字段：部分计数为估算，准确数字见协同记录与建议清单。）"
    static let coopFirstPhaseNoteText: String =
        "第一段不写最终交付目录；第二段成功后才交付。失败时最终目录保持原样。"

    
    // 批63b 发布修复 R2（2026-10-20）：GitHub macos-14 runner 的旧 Xcode 类型检查比本机
    // 慢一个量级。`body`/所有 section 已拆成小表达式，View 里不再写长字符串拼接
    // （一律 `Text(Self.xxxText)`，常量见上方「文案常量」）。新增界面请沿用这个拆法。
    var body: some View {
        pageBody
            .onChange(of: model.options.source) { _ in onSourceChanged() }
            .onChange(of: model.options.out) { _ in onOutChanged() }
            .onChange(of: model.options.metadata) { path in onMetadataPathChanged(path) }
            .sheet(isPresented: $model.showMetadataForm) { metadataFormSheet }
            .sheet(isPresented: $model.showMetadataPreview) {
                MetadataPreviewSheet(payload: model.metadataPreview) }    }

    private var pageBody: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollView { pageColumn }
        }
    }

    private var pageColumn: some View {
        VStack(alignment: .leading, spacing: 14) {
            helpBlock
            Divider()
            sourceAndOutputSection
            Text(Self.dragHintText)
                .font(.caption)
                .foregroundStyle(.tertiary)
            commonOptionsSection
            advancedToggleButton
            if model.showAdvanced {
                advancedOptionsSection
            }
            Divider()
            runButtonsSection
            // ---------- 第 55 批：两段式状态区 ----------
            if model.options.coop {
                coopPanel
            }
            // B-285b：失败诊断卡（红卡 = 转换 exit 2 / 启动失败；成功与取消不出卡）
            DiagnosisCard(entry: "convert.py")
        }
        .padding(16)
    }

    private var metadataFormSheet: some View {
        MetadataFormSheet(form: $model.metadataForm)
    }

    // 第 55 批：勾着协同开关时，源稿/输出目录一改就重新定位暂存目录
    // （编辑可能先初排、再换输出目录继续——状态区必须跟着事实走）。
    private func onSourceChanged() {
        if model.options.coop { refreshCoopStage() }
    }

    private func onOutChanged() {
        if model.options.coop { refreshCoopStage() }
    }

    // 批63b：`.doc/.docx` 自动识别已撤下（Word 源的 .docx「第二源」照旧）。picker 已限
    // .tex/.docx，拖拽/粘贴/旧草稿仍可能带进别的类型 → 立刻拦下并写日志（不静默）。
    // run() 里按「源稿通道」还有第二道判定（选稿之前 picker 可能先选元数据文件）。
    private func onMetadataPathChanged(_ path: String) {
        guard !path.isEmpty else { return }
        if case .ignored(let why) = ConvertFeature.metadataPathVerdict(
            metadata: path, source: model.options.source) {
            log.append("元数据文件：\(why)。\n")
            model.options.metadata = ""
        }
    }

    /// 运行按钮区（只扫描 / 开始转换 / 取消 / 进度）。
    private var runButtonsSection: some View {
        HStack(spacing: 12) {
            scanOnlyButton
            primaryRunButton
            runningProgress
        }
    }

    private var scanOnlyButton: some View {
        Button {
            run(scanOnly: true)
        } label: {
            Label("只扫描源稿", systemImage: "doc.text.magnifyingglass")
        }
        .disabled(model.running || model.options.source.isEmpty)
    }

    /// U3：运行中主按钮变成「取消」（不是灰掉另放一个按钮）。
    /// 取消 = 杀整棵进程树（Python 起的 xelatex 一起停，见 `RunCancel`）。
    @ViewBuilder private var primaryRunButton: some View {
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
    }

    @ViewBuilder private var runningProgress: some View {
        if model.running {
            ProgressView().controlSize(.small)
            Text("运行中…（点「取消」可中止）").foregroundStyle(.secondary)
        }
    }


    private var helpBlock: some View {
        helpTexts
            .onAppear { syncEditors() }
            // 选好源文件后重新采集一次：数据库根目录是从源文件路径往上找的
            .onChange(of: model.options.source) { _ in syncEditors() }
    }

    private var helpTexts: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(Self.helpIntroText)
                .font(.callout)
            Text(Self.helpAutoText)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(Self.helpInitialLayoutNoteText.plainMarkdown)
                .font(.caption)
                .foregroundStyle(.orange)
        }
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
    func binding(_ which: ConvertFeature.Checkbox,
                         _ keyPath: WritableKeyPath<ConvertFeature.Options, Bool>) -> Binding<Bool> {
        Binding(get: { model.options[keyPath: keyPath] },
                set: { model.options = ConvertFeature.toggled(which, to: $0, in: model.options) })
    }

    func refreshCoopStage() {
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
    func openPath(_ path: String) {
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
    func addReplyFiles() {
        guard let urls = PanelKit.pickReplyFiles() else { return }
        let counts = addReplyItems(urls)
        model.coopFailure = ""
        log.append(Self.replyImportLogText(added: counts.added,
                                           duplicated: counts.duplicated,
                                           summary: model.replies.summary))
    }

    private func addReplyItems(_ urls: [URL]) -> (added: Int, duplicated: Int) {
        var added = 0
        var duplicated = 0
        for url in urls {
            let item = CoopFeature.ReplyItem(path: url.path, isDirectory: false)
            if model.replies.add(item) { added += 1 } else { duplicated += 1 }
        }
        return (added, duplicated)
    }

    private static func replyImportLogText(added: Int, duplicated: Int,
                                           summary: String) -> String {
        "回填：新增 \(added) 个文件"
            + (duplicated > 0 ? "（\(duplicated) 个已在清单里，未重复添加）" : "")
            + "；\(summary)\n"
    }

    /// 「添加目录…」：目录整体作为一个路径（引擎会读入该目录下全部可读文本）。
    func addReplyDirectory() {
        guard let url = PanelKit.pickReplyDirectory() else { return }
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
    func importClipboard() {
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
    func removeReply(at index: Int) {
        guard let item = model.replies.remove(at: index) else { return }
        if CoopFeature.isClipboardDraft(item.path) {
            try? FileManager.default.removeItem(atPath: item.path)
        }
        log.append("回填：已移除 \(item.path)\n")
    }

    /// 清空已选清单（只清清单与剪贴板临时文件，不碰用户文件）。
    func clearReplies() {
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

    /// 「开始转换 / 只扫描 / 第一段」的总入口。
    ///
    /// 批63b 发布修复 R2：为了旧 Xcode 类型检查，拆成「现取设置 → 元数据文件判定 →
    /// 人工元数据临时 JSON → 清内联覆盖 → prepare → 启动 → 收尾」若干小函数。
    private func run(scanOnly: Bool) {
        var o = model.options
        o.scanOnly = scanOnly
        applyRuntimeSettings(to: &o)
        applyMetadataFileVerdict(to: &o)
        // 两个「请求体旋钮」（思考模式 / 输出上限）同样**现取**：与 AI 配置同一口径，
        // 设置页改完立刻生效，不必先点「保存」（保存只影响下次启动读回的值）。
        // ⚠️ 必须在主线程上取好再传：@Published 的值属于主线程，而 run() 在后台队列
        //    上执行（AIEngineKnobs 是值类型，跨线程传是安全的）。
        let knobs = settings.aiEngineKnobs
        // 第 55 批：两段式。**扫描模式忽略协同**（只扫描源稿不转换，勾着也按普通扫描走）。
        let coopOn = o.coop && !scanOnly
        var metadataJSONTempPath: String? = nil
        switch attachMetadataJSON(scanOnly: scanOnly, coopOn: coopOn, to: &o) {
        case .notNeeded:
            break
        case .aborted:
            return
        case .attached(let path):
            metadataJSONTempPath = path
            clearOverriddenInlineFields(in: &o)
        }
        o = CoopFeature.prepare(o, phase: coopOn ? .first : .off)
        model.coopFailure = ""
        if coopOn { model.coopSuggestions = nil }   // 新一轮初排：旧建议不算数
        startRun(o, knobs: knobs) { status in
            finishRun(status: status, coopOn: coopOn,
                      metadataJSONTempPath: metadataJSONTempPath)
        }
    }

    /// AI 配置在**运行时**从「设置」页现取：设置页改了立刻生效，不用重启 App。
    /// 密钥只放进命令行参数，不打印（日志里走 AppSettings.mask）。
    private func applyRuntimeSettings(to o: inout ConvertFeature.Options) {
        applyAISettings(to: &o)
        applyVerifyGateSettings(to: &o)
        applyBudgetSettings(to: &o)
    }

    private func applyAISettings(to o: inout ConvertFeature.Options) {
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
    }

    private func applyVerifyGateSettings(to o: inout ConvertFeature.Options) {
        // 第 132 条：核实成本闸门 + 引用库开关同样**现取**（设置页改完立刻生效）。
        // `verifyMaxValue` 对非法输入返回 nil = 不传该参数（引擎用默认 120；界面另有红字）。
        o.verifyMax = bibdb.verifyMaxValue
        o.verifyPlan = bibdb.verifyPlan
        o.verifyAllRefs = bibdb.verifyAllRefs
        o.noBibdb = !bibdb.lookupEnabled
        o.bibdbPath = verifyDbPathForRun()
    }

    /// 手动库文件只在「查库开着 + 文件真的存在」时显式传 --bibdb-path：
    /// 指一个不存在的库只会让引擎多打一条告警，不如不传（自动探测/ SLCN_NOTES 兜底）。
    private func verifyDbPathForRun() -> String {
        guard bibdb.lookupEnabled, let path = BibDbFeature.existingDBPath() else { return "" }
        return path
    }

    private func applyBudgetSettings(to o: inout ConvertFeature.Options) {
        // 第 55 批：AI 预算闸（直连 API 通道；默认值不传、协同模式不传）。
        // 非法输入 → nil = 不传该参数（引擎用默认；设置页另有红字）。
        o.aiBudgetTokens = budget.articleValue
        o.aiBatchBudgetTokens = budget.batchValue
        // 批 63：全文核查的字符上限 / 自动应用也**现取**（同口径；只约束 --llm-fulltext）。
        // `fulltextMaxCharsValue`：nil = 不传（引擎默认 40000）；0 = 显式不限。
        o.llmFulltextMaxChars = budget.fulltextMaxCharsValue
        o.llmFulltextApply = budget.fulltextAutoApply
    }

    /// 批63b：元数据文件按「源稿通道」处置（三通道口径见 ConvertFeature.metadataPathVerdict）：
    /// LaTeX 源只收 .tex；Word 源的 .docx（英文元数据第二源，第 76 条）与 .tex 照旧；
    /// .doc 任何通道都不再转（textutil 整体删除）。拦下时写日志，不静默。
    private func applyMetadataFileVerdict(to o: inout ConvertFeature.Options) {
        if case .ignored(let why) = ConvertFeature.metadataPathVerdict(
            metadata: o.metadata, source: o.source) {
            log.append("元数据文件：\(why)。\n")
            o.metadata = ""
        }
        guard !model.metadataSources.isEmpty else { return }   // B-223：多份补充信息
        guard !o.scanOnly && !(o.coop && !o.scanOnly) else {
            log.append(Self.metadataSourcesIgnoredLogText)
            return
        }
        o.metadataSources = model.metadataSources.filter { path in
            guard case .ignored(let why) = ConvertFeature.metadataSourceVerdict(
                path: path, source: o.source) else { return true }
            log.append("元数据 / 补充信息：\(why)。\n")
            return false
        }
    }

    private enum MetadataJSONAttachment {
        case notNeeded
        case attached(String)
        case aborted
    }

    /// 批63b：人工填写元数据 → 临时 JSON（--metadata-json，引擎侧优先级最高）。
    /// 普通转换与两段式**第一段**都下传（B-287：引擎接受 JSON + 协同，值进暂存 .tex）；
    /// 第二段走最小集、不经过这里；只扫描不产出交付物。写失败直接中止——
    /// 绝不静默丢掉编辑刚填的元数据。
    private func attachMetadataJSON(scanOnly: Bool, coopOn: Bool,
                                    to o: inout ConvertFeature.Options) -> MetadataJSONAttachment {
        guard model.metadataForm.hasAnyValue else { return .notNeeded }
        guard MetadataForm.shouldAttach(scanOnly: scanOnly, coop: coopOn) else {
            // 只剩「只扫描」不下传表单；**不静默**（B-017）。
            if scanOnly { log.append(Self.scanOnlyMetadataIgnoredLogText) }
            return .notNeeded
        }
        guard let json = model.metadataForm.jsonString() else { return .notNeeded }
        let path = MetadataForm.tempFilePath(stem: o.source)
        do {
            try json.write(toFile: path, atomically: true, encoding: .utf8)
        } catch {
            log.append("元数据：临时 JSON 写入失败（\(error.localizedDescription)）；"
                       + "本次未开始转换（不会静默丢掉已填内容）。\n")
            return .aborted
        }
        o.metadataJSON = path
        log.append(metadataAttachedLogText())
        for warning in model.metadataForm.warnings() {
            log.append("元数据：⚠ \(warning)\n")
        }
        return .attached(path)
    }

    private func metadataAttachedLogText() -> String {
        "元数据：" + model.metadataForm.summaryLine()
            + "（人工填写表单优先于 `.tex` 元数据文件；临时 JSON 运行结束即删除）\n"
    }

    /// 批63b：表单优先于旧 7 个内联覆盖字段——表单**填了的字段**在 GUI 侧清零对应内联值。
    /// 逐字段清零而不是一刀切：避免丢掉「先填内联、后只填了表单作者」这类混合输入的值。
    /// 只在表单**真的下传**时清零：只扫描下传不了表单，内联值必须原样保留
    /// （两段式第一段也下传，所以同样按字段清零）。
    /// B-017 起普通模式已不渲染内联字段，本函数保留作**防御**（旧状态/旧调用方双源保护）。
    private func clearOverriddenInlineFields(in o: inout ConvertFeature.Options) {
        for field in MetadataForm.InlineOverrideField.allCases
        where model.metadataForm.coversInlineOverride(field) {
            switch field {
            case .titleCN: o.titleCn = ""
            case .authorCN: o.authorCn = ""
            case .affiliationCN: o.affiliationCn = ""
            case .email: o.email = ""
            case .abstractCN: o.abstractCn = ""
            case .titleEN: o.titleEn = ""
            case .abstractEN: o.abstractEn = ""
            }
        }
    }

    /// 跑完的收尾：批63b 临时 JSON 只服务本次运行（成功/失败/取消都清理）；
    /// 第一段再补一句状态提示／失败说明。
    private func finishRun(status: Int32, coopOn: Bool, metadataJSONTempPath: String?) {
        cleanupMetadataJSON(metadataJSONTempPath)
        guard coopOn else { return }
        reportFirstPhaseStatus(status)
    }

    /// 批63b：临时 JSON 只服务本次运行（成功/失败/取消都清理）。
    private func cleanupMetadataJSON(_ path: String?) {
        if let temp = path {
            MetadataForm.removeTemporaryFile(temp)
        }
    }

    /// 第一段的状态提示／失败说明（从 finishRun 拆出，避免单函数类型检查过重）。
    /// Int32 比较走 `ConvertFeature.isSuccessStatus/isCancelledStatus`（见那边注释）。
    private func reportFirstPhaseStatus(_ status: Int32) {
        if ConvertFeature.isSuccessStatus(status) {
            log.append(Self.coopFirstPhaseDoneLogText)
        } else if !ConvertFeature.isCancelledStatus(status) {
            appendFailure("第一段失败（退出码 \(status)）；最终交付目录本次未写。")
        }
    }

    /// 第二段：用当前暂存目录 + 已选回填（每个路径一个 --coop-replies）续跑并交付。
    ///
    /// 第 56 批要点：
    ///   · 回填 = `model.replies` 里的**每一项**（文件/目录/剪贴板可混合、按顺序），
    ///     不再折叠成父目录；空清单由按钮 disabled 拦住，这里再兜一次并说明原因。
    ///   · `--coop-apply` 必须在**启动前**由勾选框决定（引擎 phase=done 后重跑是幂等早退）。
    ///   · `--clean` 随页面「清理编译中间文件」开关走（ph2 也生效，否则交付目录会留中间文件）。
    func runSecondPhase() {
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
        if model.metadataForm.hasAnyValue {
            log.append(Self.coopSecondPhaseMetadataNoteText)
        }
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
