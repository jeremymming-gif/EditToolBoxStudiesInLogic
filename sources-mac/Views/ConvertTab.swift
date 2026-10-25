import SwiftUI
import UniformTypeIdentifiers

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
    /// 批63b：初排页「人工填写元数据」表单。值只在本次 App 会话内有效（不做持久化），
    /// 运行前写成 `/tmp` 临时 JSON 传给引擎 `--metadata-json`，跑完即删；
    /// 不填 = 与从前完全一样（默认 argv 逐字节不变）。
    @Published var metadataForm = MetadataForm()
    /// 表单 sheet 是否打开
    @Published var showMetadataForm = false
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

    // MARK: - 文案常量（View 里只写 `Text(Self.xxxText)`）
    //
    // 批63b 发布修复 R2（2026-10-20）：GitHub macos-14 runner 的旧 Xcode 在 ViewBuilder 里
    // 对「多段字符串拼接 + 修饰符链」的类型检查代价比新编译器高一个量级（CI 报
    // `aiExtraTogglesSection` 超时）。因此所有长文案 / 多段拼接**一律移出 View body**，
    // View 里只留下 `Text(Self.xxxText)` 这种零推导成本的写法。
    // **改文案改这里**；界面上不要再写拼接字符串，也不要再在 View 里做长表达式。
    private static let dragHintText: String =
        "源文件与输出目录都可以直接从访达拖进来（高级选项里的路径同理）"
    private static let forceHintText: String =
        "目标目录里混着非本工具的文件时（例如手工 latexmk 留下的 "
        + ".fdb_latexmk/.fls），默认会被拦下以防误伤已有稿件目录；"
        + "勾选后照常写入：同名产物被覆盖，目录里的其它文件不会被删。"
    private static let keepBibHintText: String =
        "勾上 = 只出《文献核实任务书》（引擎侧 --no-ai-merge）：AI 结果不写进产物，"
        + "reference.bib 一个字节都不动。不勾时 AI 结果会并入交付的 reference.bib，"
        + "合并之前的那份会先备份成 reference.bib.ai前（要回滚就把它覆盖回去）。"
    private static let llmFulltextHintText: String =
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
    private static let inlineOverrideHeaderText: String =
        "正文信息覆盖（两段式兜底；只覆盖第一作者/题名/摘要/单位/邮箱；"
        + "多作者请改用 `.tex` 元数据文件）"
    private static let inlineOverrideWarningText: String =
        "⚠ 手工覆盖的生效取决于引擎版本：用旧版引擎时日志会提示"
        + "「尚不支持 overrides 参数」，此时不影响转换本身。"
    private static let metadataPlaceholderWordText: String =
        "留空自动找英文元数据第二源；.docx 可显式指定（.doc 请先另存为 .docx）"
    private static let metadataPlaceholderLatexText: String =
        "留空用人工填写或同目录 .tex；.doc/.docx 自动识别已取消（请用「填写元数据…」）"
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
    private static let metadataFormTitleText: String =
        "元数据（推荐人工填写；值直接写入交付产物，留空 = 不覆盖）"
    // B-017：普通转换的常驻上限说明（与表单内、docs 同口径；明细来自纯逻辑常量）。
    private static let metadataFormLimitText: String = MetadataForm.limitSummary
    private static let metadataFormCoopFallbackTitleText: String =
        "元数据（两段式协同：人工元数据表单不适用）"
    private static let metadataFormCoopHintText: String =
        "⚠ 两段式协同暂不支持人工填写元数据（引擎会直接退出 2）：表单入口按模式让位，"
        + "本次不会下传表单内容。请按下面「正文信息覆盖」兜底字段填第一作者/题名/摘要/单位/邮箱"
        + "（第一段生效；第二段从暂存状态继承，不用重填），或改用普通转换/`.tex` 元数据文件。"
    private static let metadataFormPriorityHintText: String =
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
    private static let coopMetadataUnsupportedLogText: String =
        "元数据：⚠ 两段式协同暂不支持人工填写元数据（引擎会直接退出 2），"
        + "本次**未下传**表单内容；请改用普通转换，或在「正文信息覆盖」兜底字段里填，"
        + "或保留 .tex 元数据文件。\n"
    private static let scanOnlyMetadataIgnoredLogText: String =
        "元数据：只扫描源稿不产出交付物，本次不下传人工填写内容"
        + "（用「开始转换」跑才生效）。\n"
    private static let coopFirstPhaseDoneLogText: String =
        "（界面）第一段完成：状态「待人工核实（**未交付**）」。"
        + "下一步：打开任务书 → 外部 chat 核实 → 导入回复 → 运行第二段。\n"
    private static let coopTaskbookNoteText: String =
        "文献核实任务书 = 两段式第一段产物；普通转换如需任务书请用两段式或 CLI "
        + "--verify-refs（高级）。"
    private static let coopToggleHintText: String =
        "软件不上传、不调用 API、零费用：第一段只在本机生成暂存目录 + 任务书，"
        + "任务书由编辑自己交给外部 chat（豆包/千问/DeepSeek 等）核实；"
        + "把回复导入后跑第二段，**这时才交付**。\n"
        + "⚠ 回填**必须含两部分**：① 第三节完整 BibTeX（一个代码块）② 附一 JSON 逐项；"
        + "可放同一文件，也可拆成多个文件/目录甚至粘贴剪贴板，一起导入即可。\n"
        + "⚠ 勾着两段式时「用 AI 处理参考文献」的直连 API 通道本次不生效（不传 --llm-*）；"
        + "「只扫描源稿」不受影响。"
    private static let coopNoStageText: String =
        "尚未初排：点上方「开始第一段（出任务书）」→ 打开任务书去外部 chat 核实 → "
        + "回来「添加文件…/添加目录…/从剪贴板导入」→ 运行第二段。第一段跑完前不会写最终交付目录。"
    private static let coopApplyHintText: String =
        "勾上 = 引擎 `--coop-apply`：把**通过校验的建议**落到产物——文献字段（BIB）→ reference.bib；"
        + "元数据（META）→ 输出 .tex 的 `\\my*` 字段；纯引用脚注匹配（FN，仅 citation_unmatched）"
        + "→ .tex 正文 `\\parencite`。写前分别备份 `reference.bib.ai前` / `<名>.tex.ai前`（可恢复）；"
        + "编辑侧已有值不覆盖，混合型脚注 / 文献区原文等仍**只列清单待人工**。\n"
        + "⚠ 这个决定必须在跑第二段**之前**做：引擎 phase=done 后同一回填重跑是幂等早退，"
        + "看到建议再回头勾已经晚了。"
    private static let coopSuggestionsTimingText: String =
        "建议清单（`_AI建议.md/.jsonl`）在第二段跑完后才生成；现在看不到是正常的。"
        + "第二段成功后这里会按引擎统计显示「文献回填 x/y」与「低置信回复」计数。"
    private static let coopRepliesHintText: String =
        "多个文件 / 目录 / 剪贴板草稿都会**一起**交给引擎（按下面清单顺序逐个传 --coop-replies），"
        + "不会再折叠成父目录。回填=完整 BibTeX + 附一 JSON 两部分，缺一部分会导致漏回填。"
    private static let coopDeliveredText: String =
        "已交付。若还要改回填内容或换了源稿：重新跑第一段会重置暂存目录状态；"
        + "对同一份回填重跑第二段是幂等早退（不会重复应用）。"
    private static let coopFulltextHintText: String =
        "默认不勾：任务书不含正文，与从前一致（软件零上传、零费用）。\n"
        + "勾上后引擎会把正文/引用上下文写进本机《文献核实任务书》（范围见上）："
        + "文件只留在本机，由你自己复制给外部 chat 做全文核查——软件仍不上传、不调用 API。"
        + "已获授权，不再弹二次确认。\n"
        + "⚠ 只对「开始第一段（出任务书）」生效；第二段续跑与交付不受影响。"
    private static let coopDeliveredLegacyNoteText: String =
        "（旧状态文件没有 stats 字段：部分计数为估算，准确数字见协同记录与建议清单。）"
    private static let coopFirstPhaseNoteText: String =
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
    }

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

    // MARK: - 初排页 section（body 拆分；顺序 = 界面自上而下）

    /// 源文件 + 输出目录（两个必填路径）。
    private var sourceAndOutputSection: some View {
        VStack(alignment: .leading, spacing: 14) {
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
        }
    }

    /// 常勾选项（编译/清理/AI/协同/原稿 PDF）。
    private var commonOptionsSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            compileCleanToggles
            // 批 62 入口收敛（B 方案）：独立的「生成文献核实任务书」勾选已隐藏，
            // 本页只保留两个语义入口 —— 两段式（任务书 = 第一段产物）与 ⑪ 直连 AI
            // （内部自动带 --verify-refs，产物/行为与批61 一致）。
            // `Options.verifyRefs` 字段保留给旧脚本/既有调用方，界面不再绑定它；
            // 普通初排转换不再带 --verify-refs（默认产物无 <stem>_文献核实.md）。
            // 第 132 条：核实成本闸门提示（值来自「引用库设置」页，点击转换时现取）。
            if model.options.useAI || model.options.coop {
                VerifyGateHint(bibdb: bibdb)
            }
            aiToggleRow
            AiHintView(settings: settings,
                       usesAI: model.options.useAI,
                       usesCoop: model.options.coop)
            aiExtraTogglesSection
            coopToggleRow
            sourcePdfToggle
        }
        .toggleStyle(.checkbox)
    }

    private var compileCleanToggles: some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle("生成后立即编译 PDF（XeLaTeX → biber → XeLaTeX ×2）",
                   isOn: $model.options.compile)
            Toggle("清理编译中间文件（保留 .log）", isOn: $model.options.clean)
        }
    }

    // 「用 AI 处理参考文献」：勾上就是「任务书 + 按同一份规范在本机直连 AI 预跑」
    // （引擎侧自动带 --verify-refs；批 62 起这是普通初排唯一出任务书的入口）。
    // 批 62 文案与 Win 逐字对齐（Lead 口径）：标签点明「直连 API，会产生费用」。
    private var aiToggleRow: some View {
        Toggle("用 AI 处理参考文献（直连 API，会产生费用）",
               isOn: binding(.useAI, \.useAI))
    }

    private var sourcePdfToggle: some View {
        Toggle("导出原稿 PDF（供矢量图形人工截图）",
               isOn: $model.options.sourcePdf)
    }

    /// 勾「用 AI 处理参考文献」后才出现的两个附加开关（保留原 bib / 全文 AI 核查）。
    @ViewBuilder private var aiExtraTogglesSection: some View {
        if model.options.useAI {
            // 只在 AI 勾着时可见：没勾 AI 就没有合并通道，这个开关无从谈起。
            keepOriginalBibRow
            keepOriginalBibHint
            // ---------- 第 60 批：全文 AI 核查（直连 API，会产生费用；默认不勾） ----------
            // 默认不勾 → 一个参数都不加（默认 argv 与批 59 逐字节相同）。
            llmFulltextRow
            llmFulltextHint
        }
    }

    private var keepOriginalBibRow: some View {
        Toggle("保留原 reference.bib（不并入 AI 结果）",
               isOn: binding(.keepOriginalBib, \.keepOriginalBib))
    }

    private var keepOriginalBibHint: some View {
        Text(Self.keepBibHintText)
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.leading, 18)
    }

    private var llmFulltextRow: some View {
        Toggle("全文 AI 核查（直连 API，会产生费用）",
               isOn: $model.options.llmFulltext)
    }

    private var llmFulltextHint: some View {
        Text(Self.llmFulltextHintText)
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.leading, 18)
    }

    /// 两段式「AI 协同初排」开关（默认关）+ 勾上后的说明。
    @ViewBuilder private var coopToggleRow: some View {
        // 默认关 → 协同相关字段一个都不进 argv，转换行为与批 54 逐字节相同。
        Toggle("AI 协同初排（两段式；外部 chat 免费流程）",
               isOn: $model.options.coop)
            .onChange(of: model.options.coop) { on in
                if on { refreshCoopStage() }
            }
        if model.options.coop {
            Text(Self.coopToggleHintText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.leading, 18)
        }
    }

    /// 「高级选项」整行按钮（批 63 B1：点文字/整行都能开合；状态仍是 model.showAdvanced）。
    private var advancedToggleButton: some View {
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
    }

    /// 高级选项展开后的全部内容。
    private var advancedOptionsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            advancedPathsSection
            advancedNamingSection
            advancedPublicationSection
            advancedMetadataSection
        }
        .padding(.top, 8)
    }

    /// 高级：覆盖开关 + 三个附件/元数据路径。
    private var advancedPathsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Toggle("覆盖已存在的输出目录（--force）",
                   isOn: $model.options.force)
                .toggleStyle(.checkbox)
            Text(Self.forceHintText)
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
                // 批63b：placeholder 按源稿通道动态变（LaTeX / Word 的既有能力不同）。
                placeholder: Self.metadataFilePlaceholder(for: model.options.source),
                path: $model.options.metadata,
                kind: .file,
                allowedExtensions: ["tex", "docx"],
                logStore: log
            )
            LabeledPathRow(
                title: "已有的 .bib",
                placeholder: "指定后不再自动生成",
                path: $model.options.bib,
                kind: .file,
                logStore: log
            )
        }
    }

    /// 高级：输出名 / 第一作者 / 语种。
    private var advancedNamingSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            outputNameRow
            sourceAuthorRow
            languageRow
        }
    }

    private var outputNameRow: some View {
        HStack(spacing: 12) {
            Text("输出名")
            TextField("留空则按第一作者自动命名（如 Li_Lei）",
                      text: $model.options.name)
                .textFieldStyle(.roundedBorder)
        }
    }

    private var sourceAuthorRow: some View {
        HStack(spacing: 12) {
            Text("第一作者")
            TextField("留空则从源文件提取",
                      text: $model.options.author)
                .textFieldStyle(.roundedBorder)
        }
    }

    private var languageRow: some View {
        HStack(spacing: 12) {
            Text("语种")
            languagePicker
        }
    }

    private var languagePicker: some View {
        Picker("", selection: $model.options.lang) {
            Text("自动判断").tag("")
            Text("中文（SLCN-MIX.sty）").tag("zh")
            Text("英文（SLEN.sty）").tag("en")
        }
        .labelsHidden()
        .frame(width: 240)
    }

    /// 高级：出版信息（卷/期/年、起止页/收稿/责编、基金项目）。
    private var advancedPublicationSection: some View {
        VStack(alignment: .leading, spacing: 10) {
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
            pagesAndEditorRow
            HStack(spacing: 12) {
                Text("基金项目")
                TextField("选填，如：国家社会科学基金项目“…”（23BZX113）。",
                          text: $model.options.grants)
                    .textFieldStyle(.roundedBorder)
            }
        }
    }

    /// 起止页 / 收稿日期 / 责编（含自定义名单与默认值按钮）。
    private var pagesAndEditorRow: some View {
        HStack(spacing: 12) {
            Text("起止页")
            TextField("137-152", text: $model.options.pages)
                .textFieldStyle(.roundedBorder).frame(width: 110)
            Text("收稿日期")
            TextField("2025-09-19", text: $model.options.received)
                .textFieldStyle(.roundedBorder).frame(width: 110)
            editorControls
        }
    }

    /// 责编控件（批63b R2：抽成独立小 View `EditorControlsRow`，避免大 ViewBuilder
    /// 上下文里的类型检查开销；行为/布局/文案不变）。
    private var editorControls: some View {
        EditorControlsRow(editors: editors, selection: $model.options.editor)
    }

    /// 高级：元数据入口（B-017：普通=表单 / 两段式=内联兜底）+ 引用环境阈值。
    private var advancedMetadataSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Divider().padding(.vertical, 2)
            metadataEntrySection
            Divider().padding(.vertical, 2)
            quoteThresholdRow
        }
    }

    /// B-017：**单一元数据入口**。路由判据只有一份——
    /// `MetadataForm.entryMode(scanOnly:coop:)`（纯逻辑，`tools/ai_settings_test` 直接断言）：
    ///   · 普通转换 → 只显示「填写元数据…」表单（旧 7 内联字段不渲染）；
    ///   · 两段式协同 → 表单不适用（引擎对 JSON + 协同明确 exit 2），只显示旧 7 内联兜底。
    @ViewBuilder private var metadataEntrySection: some View {
        if MetadataForm.entryMode(scanOnly: false, coop: model.options.coop) == .inlineFallback {
            coopMetadataFallbackSection
        } else {
            metadataFormSection
        }
    }

    /// 两段式：人工元数据表单不适用 → 说明 + 「已填表单不下传」提醒 + 旧内联兜底。
    private var coopMetadataFallbackSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(Self.metadataFormCoopFallbackTitleText)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(Self.metadataFormCoopHintText)
                .font(.caption)
                .foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
            if model.metadataForm.hasAnyValue {
                coopFormValuesRow
            }
            inlineOverrideSection
        }
    }

    /// 两段式下表单已填内容不会下传：明确提醒 + 一键清空（绝不静默丢值）。
    private var coopFormValuesRow: some View {
        HStack(spacing: 12) {
            Text(coopFormValuesNotAttachedText())
                .font(.caption)
                .foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
            Button("清空表单") { model.metadataForm = MetadataForm() }
        }
    }

    private func coopFormValuesNotAttachedText() -> String {
        "已填表单：" + model.metadataForm.summaryLine() + "（两段式不下传，本次不会写进产物）"
    }

    /// 高级：引用环境阈值（原与元数据同段；B-017 拆成小 View 保持表达式小）。
    private var quoteThresholdRow: some View {
        HStack(spacing: 12) {
            Text("引用环境阈值")
            TextField("105", value: $model.options.quoteThreshold, format: .number)
                .textFieldStyle(.roundedBorder)
                .frame(width: 80)
            Text("字符（超过此长度提升为引用环境）")
                .foregroundStyle(.secondary)
        }
    }

    /// 旧「正文信息覆盖」7 个内联字段（B-017 起只作**两段式兜底**显示；普通转换不渲染）。
    /// 表单与内联不再同时可编辑；运行前 GUI 仍会按字段清零（防御旧调用方/旧状态双源）。
    private var inlineOverrideSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            // ---------- 正文信息手工覆盖（两段式兜底输入路径） ----------
            // 引擎按版面启发式解析题名/作者/摘要，总有解析不出来或
            // 解析错的稿子。这里是两段式下的兜底：填了就覆盖自动解析结果。
            // 全部留空 = 不覆盖，与旧版行为一致。
            Text(Self.inlineOverrideHeaderText)
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
            Text(Self.inlineOverrideWarningText)
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        // B-017：本 section 只在两段式模式渲染，而两段式**不下传**表单 →
        // 不能再按「表单有值」置灰（否则用户两段式下两个入口都不可用）。
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

    /// 批63b：「元数据文件」占位文案按**源稿通道**动态变（两通道既有能力不同，不能一刀切）。
    ///
    ///   · LaTeX 源：`.doc/.docx` 自动识别已取消（用户拍板）→ 引导用表单或同目录 `.tex`；
    ///   · Word 源：`.docx` 是既有「英文元数据第二源」（第 76 条，行为不变）→ 引导显式指定。
    private static func metadataFilePlaceholder(for source: String) -> String {
        let lowered = source.lowercased()
        if lowered.hasSuffix(".doc") || lowered.hasSuffix(".docx") {
            return Self.metadataPlaceholderWordText
        }
        return Self.metadataPlaceholderLatexText
    }

    /// 普通转换：初排页**唯一**的人工元数据入口（表单在 sheet 里；这里放入口 + 摘要 +
    /// 常驻上限说明）。B-017：旧 7 内联字段在此模式不再渲染。
    private var metadataFormSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(Self.metadataFormTitleText)
                .font(.caption)
                .foregroundStyle(.secondary)
            metadataFormEntryRow
            Text(Self.metadataFormLimitText)
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            if model.metadataForm.hasAnyValue {
                Text(Self.metadataFormPriorityHintText)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder private var metadataFormEntryRow: some View {
        HStack(spacing: 12) {
            metadataFormButton
            if model.metadataForm.hasAnyValue {
                Text(model.metadataForm.summaryLine())
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button("清空") { model.metadataForm = MetadataForm() }
            }
        }
    }

    private var metadataFormButton: some View {
        Button {
            // B-111：打开表单时**单向预填**——仅当表单「第一作者中文姓名」为空、且高级选项
            // 「第一作者」(`options.author`) 已填时，用其预填；不覆盖已填值。
            // 只改内存里的表单会话态（不写 JSON、不改 argv），`Options.author` 语义不变。
            model.metadataForm = MetadataForm.prefillFirstAuthorCN(
                from: model.options.author,
                current: model.metadataForm)
            model.showMetadataForm = true
        } label: {
            Label("填写元数据…", systemImage: "square.and.pencil")
        }
        // B-017：普通模式唯一入口。两段式模式**整段不渲染**（引擎对 JSON + 协同 exit 2），
        // 不再靠「禁用按钮」表达——禁用但旁边的兜底字段也置灰时用户会卡死。
    }

    /// 复选框 → 选项的绑定：联动规则的**唯一**实现在 `ConvertFeature.toggled(_:to:in:)`
    /// （纯函数，`tools/ai_settings_test` 直接验证它）。SwiftUI 这边只做绑定，
    /// 不写任何 if/联动逻辑 —— 界面照抄一份判据，迟早会与引擎侧分叉。
    private func binding(_ which: ConvertFeature.Checkbox,
                         _ keyPath: WritableKeyPath<ConvertFeature.Options, Bool>) -> Binding<Bool> {
        Binding(get: { model.options[keyPath: keyPath] },
                set: { model.options = ConvertFeature.toggled(which, to: $0, in: model.options) })
    }

    // MARK: - 第 55 批：两段式「AI 协同初排」
    // （说明文案统一在上方「文案常量」区：coopToggleHintText / coopNoStageText /
    //  coopApplyHintText / coopSuggestionsTimingText / coopRepliesHintText /
    //  coopDeliveredText / coopFulltextHintText 等）
    /// 两段式的状态区：状态行 + 按钮组（按钮可见性完全由 `coopStage.phase` 决定）。
    private var coopPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider()
            Text("AI 协同初排（两段式）").font(.callout).bold()
            // 批 62 入口收敛：任务书从此只有“两段式第一段”这一个界面入口（plus ⑪ 直连 AI）。
            Text(Self.coopTaskbookNoteText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            coopFailureRow
            // ---------- 第 59 批：全文 AI 核查（任务书附正文；默认不勾） ----------
            // 默认不勾 → 一个参数都不加（默认 argv 与批 58 逐字节相同）。
            Divider()
            coopFulltextToggleRow
            coopFulltextScopePicker
            Text(Self.coopFulltextHintText)
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            coopStageSection
        }
        .padding(10)
        .background(Color.secondary.opacity(0.07))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder private var coopFailureRow: some View {
        if !model.coopFailure.isEmpty {
            Text(model.coopFailure)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.red)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var coopFulltextToggleRow: some View {
        Toggle("全文 AI 核查（任务书附正文）", isOn: $model.options.coopFulltext)
            .toggleStyle(.checkbox)
    }

    @ViewBuilder private var coopFulltextScopePicker: some View {
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
    }

    @ViewBuilder private var coopStageSection: some View {
        if let stage = model.coopStage {
            coopStagePanel(stage)
        } else {
            Text(Self.coopNoStageText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// 暂存目录状态面板。**第一段状态只叫「暂存目录」**，绝不写成交付目录。
    @ViewBuilder private func coopStagePanel(_ stage: CoopFeature.StageState) -> some View {
        Text(statusLine(stage))
            .font(.callout).bold()
        coopCandidatesNote(stage)
        coopStageBody(stage)
    }

    private func statusLine(_ stage: CoopFeature.StageState) -> String {
        "状态：" + CoopFeature.stageStatusText(stage)
    }

    @ViewBuilder private func coopCandidatesNote(_ stage: CoopFeature.StageState) -> some View {
        if model.coopCandidates > 1 {
            Text("发现 \(model.coopCandidates) 个匹配的暂存目录，已选最近修改的一个：\(stage.stageDir)")
                .font(.caption).foregroundStyle(.orange)
        }
    }

    @ViewBuilder private func coopStageBody(_ stage: CoopFeature.StageState) -> some View {
        if stage.isDelivered {
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
        Text(deliveredDirLine(stage))
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
        HStack(spacing: 10) {
            Button("打开交付目录") { openDeliveredDir(stage) }
            deliveredRecordButton
            deliveredSuggestionsButton
        }
        deliveredResultSection
        Text(Self.coopDeliveredText)
            .font(.caption).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func deliveredDirLine(_ stage: CoopFeature.StageState) -> String {
        "交付目录：\(stage.deliveredTo.isEmpty ? stage.finalDir : stage.deliveredTo)"
    }

    private func openDeliveredDir(_ stage: CoopFeature.StageState) {
        openPath(stage.deliveredTo.isEmpty ? stage.finalDir : stage.deliveredTo)
    }

    @ViewBuilder private var deliveredRecordButton: some View {
        if !model.coopRecordPath.isEmpty {
            Button("打开协同记录") { openPath(model.coopRecordPath) }
        }
    }

    @ViewBuilder private var deliveredSuggestionsButton: some View {
        if let result = model.coopResult,
           !result.suggestionsMarkdownPath.isEmpty,
           FileManager.default.fileExists(atPath: result.suggestionsMarkdownPath) {
            Button("打开建议清单") { openPath(result.suggestionsMarkdownPath) }
        }
    }

    @ViewBuilder private var deliveredResultSection: some View {
        if let result = model.coopResult {
            Text(result.summaryLine)
                .font(.callout).bold()
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            deliveredZeroBibWarning(result)
            deliveredLegacyNote(result)
        }
    }

    @ViewBuilder private func deliveredZeroBibWarning(_ result: CoopFeature.CoopResult) -> some View {
        if let warning = result.zeroBibWarning {
            Text(warningLine(warning))
                .font(.callout).bold()
                .foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private func deliveredLegacyNote(_ result: CoopFeature.CoopResult) -> some View {
        if result.stats.legacy {
            Text(Self.coopDeliveredLegacyNoteText)
                .font(.caption).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func warningLine(_ text: String) -> String { "⚠ " + text }

    /// 待人工核实（ph1 / ph2 未完成）：暂存目录 + 任务书 + 回填导入（多文件/目录/剪贴板）
    /// + 已选清单（可移除/清空）+ 一键修改产物 + 第二段。
    @ViewBuilder private func coopPendingPanel(_ stage: CoopFeature.StageState) -> some View {
        Text(pendingStageLine(stage))
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
        pendingStageButtons(stage)
        Divider().padding(.vertical, 2)
        Text("回填导入").font(.callout).bold()
        pendingRepliesImportRow
        Text(model.replies.summary)
            .font(.caption).bold()
            .foregroundStyle(model.replies.isEmpty ? Color.secondary : Color.primary)
        pendingRepliesList
        Text(Self.coopRepliesHintText)
            .font(.caption).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        pendingApplySection
        pendingSecondPhaseSection(stage)
        Text(Self.coopSuggestionsTimingText)
            .font(.caption).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
        Text(Self.coopFirstPhaseNoteText)
            .font(.caption).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func pendingStageLine(_ stage: CoopFeature.StageState) -> String {
        "暂存目录（**未交付**，不是最终产物）：\(stage.stageDir)"
    }

    @ViewBuilder private func pendingStageButtons(_ stage: CoopFeature.StageState) -> some View {
        HStack(spacing: 10) {
            pendingTaskbookButton(stage)
            Button("打开暂存目录") { openStageDir(stage) }
            pendingRunningSpinner
        }
    }

    private func openStageDir(_ stage: CoopFeature.StageState) {
        openPath(stage.stageDir)
    }

    @ViewBuilder private func pendingTaskbookButton(_ stage: CoopFeature.StageState) -> some View {
        if !stage.taskbook.isEmpty {
            Button("打开任务书") { openPath(stage.taskbook) }
        }
    }

    @ViewBuilder private var pendingRunningSpinner: some View {
        if model.running {
            ProgressView().controlSize(.small)
        }
    }

    private var pendingRepliesImportRow: some View {
        HStack(spacing: 8) {
            Button("添加文件…") { addReplyFiles() }
            Button("添加目录…") { addReplyDirectory() }
            Button("从剪贴板导入") { importClipboard() }
            clearRepliesButton
            Spacer()
        }
        .disabled(model.running)
    }

    @ViewBuilder private var clearRepliesButton: some View {
        if !model.replies.isEmpty {
            Button("全部清除") { clearReplies() }
        }
    }

    private var pendingRepliesList: some View {
        ForEach(Array(model.replies.items.enumerated()), id: \.element.path) { index, item in
            pendingReplyRow(index: index, item: item)
        }
    }

    private func pendingReplyRow(index: Int, item: CoopFeature.ReplyItem) -> some View {
        HStack(spacing: 6) {
            Text(replyIcon(item))
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

    private func replyIcon(_ item: CoopFeature.ReplyItem) -> String {
        CoopFeature.isClipboardDraft(item.path) ? "📋" : (item.isDirectory ? "📁" : "📄")
    }

    private var pendingApplySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("一键修改产物（--coop-apply；默认不勾）", isOn: $model.coopApply)
                .toggleStyle(.checkbox)
            Text(Self.coopApplyHintText)
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func pendingSecondPhaseSection(_ stage: CoopFeature.StageState) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            secondPhaseButton(stage)
            secondPhaseDisabledHint(stage)
        }
    }

    private func secondPhaseButton(_ stage: CoopFeature.StageState) -> some View {
        Button {
            runSecondPhase()
        } label: {
            Label("运行第二段（续跑 → 编译 → 交付）", systemImage: "square.and.arrow.down.on.square")
        }
        .disabled(model.running
                  || !CoopFeature.canRunSecondPhase(replyCount: model.replies.count, stage: stage))
    }

    @ViewBuilder private func secondPhaseDisabledHint(_ stage: CoopFeature.StageState) -> some View {
        if let hint = CoopFeature.secondPhaseDisabledHint(replyCount: model.replies.count, stage: stage) {
            Text(hint)
                .font(.caption)
                .foregroundStyle(model.replies.isEmpty ? Color.orange : Color.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
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
    private func addReplyDirectory() {
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
    }

    private enum MetadataJSONAttachment {
        case notNeeded
        case attached(String)
        case aborted
    }

    /// 批63b：人工填写元数据 → 临时 JSON（--metadata-json，引擎侧优先级最高）。
    /// 只在普通转换下传（两段式引擎会 exit 2；只扫描不产出交付物）；写失败直接中止——
    /// 绝不静默丢掉编辑刚填的元数据。
    private func attachMetadataJSON(scanOnly: Bool, coopOn: Bool,
                                    to o: inout ConvertFeature.Options) -> MetadataJSONAttachment {
        guard model.metadataForm.hasAnyValue else { return .notNeeded }
        guard MetadataForm.shouldAttach(scanOnly: scanOnly, coop: coopOn) else {
            // 两段式 / 只扫描都不下传表单；**不静默**（B-017：只扫描这条也补一句）。
            if coopOn { log.append(Self.coopMetadataUnsupportedLogText) }
            else if scanOnly { log.append(Self.scanOnlyMetadataIgnoredLogText) }
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
    /// 只在表单**真的下传**时清零：两段式/只扫描下传不了表单，内联值必须原样保留。
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

/// 「责编」控件：选择 / 自定义名单 / 设为默认。
///
/// 批63b 发布修复 R2：从 `ConvertTab` 的大 ViewBuilder 上下文里抽出来（旧 Xcode 在
/// 大上下文里对带条件分支的 ViewBuilder 类型检查代价高）。行为 / 文案 / 布局与拆分前一致：
/// 在 `pagesAndEditorRow` 的 HStack 里，它展开成「责编 + Picker + [新责编姓名 + 添加] + 设为默认」。
private struct EditorControlsRow: View {
    @ObservedObject var editors: EditorStore
    @Binding var selection: String

    var body: some View {
        Text("责编")
        editorPicker
        customEditorControls
        setDefaultEditorButton
    }

    private var editorPicker: some View {
        Picker("", selection: $selection) { editorPickerItems }
            .labelsHidden()
            .frame(width: 130)
    }

    @ViewBuilder private var editorPickerItems: some View {
        Text("未指定").tag("")
        ForEach(editors.allNames, id: \.self) { name in
            Text(editorOptionTitle(name)).tag(name)
        }
        Text("自定义…").tag("__custom__")
    }

    /// 默认责编在名单里加「（默认）」。抽成方法，让 ViewBuilder 里只剩 `Text(String)`。
    private func editorOptionTitle(_ name: String) -> String {
        editors.defaultName == name ? "\(name)（默认）" : name
    }

    @ViewBuilder private var customEditorControls: some View {
        if selection == "__custom__" {
            customEditorNameField
            addEditorButton
        }
    }

    private var customEditorNameField: some View {
        TextField("新责编姓名", text: $editors.newName)
            .textFieldStyle(.roundedBorder).frame(width: 90)
    }

    private var addEditorButton: some View {
        Button("添加") { addCustomEditor() }
    }

    private func addCustomEditor() {
        editors.addCustom(editors.newName)
        let name = editors.newName.trimmingCharacters(in: .whitespacesAndNewlines)
        if !name.isEmpty { selection = name }
        editors.newName = ""
    }

    private var setDefaultEditorButton: some View {
        Button(setDefaultEditorTitle()) { setDefaultEditor() }
            .disabled(selection.isEmpty || selection == "__custom__")
    }

    private func setDefaultEditorTitle() -> String {
        editors.defaultName == selection && !selection.isEmpty
            ? "已设为默认 ✓" : "设为默认"
    }

    private func setDefaultEditor() {
        editors.setDefault(selection)
    }
}

/// 一行「标签 + 路径输入 + 浏览按钮」。与 SimpleFeatureView 的选择方式保持一致（NSOpenPanel）。
struct LabeledPathRow: View {
    let title: String
    let placeholder: String
    @Binding var path: String
    let kind: PickKind
    /// 批63b：可选的扩展名白名单（如 `["tex", "docx"]`）。空数组 = 不限制（既有调用方零变化）。
    /// 只影响「浏览…」的 NSOpenPanel 过滤；拖拽类型由 `PathDrop` 只判文件/目录，
    /// 更细的通行/拦截由各页自己的纯函数判定（如 `ConvertFeature.metadataPathVerdict`）。
    var allowedExtensions: [String] = []
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
        // 批63b R2：NSOpenPanel / ModalResponse 相关代码集中在 `PanelKit.swift`
        // （旧 Xcode 首次解析 AppKit 运算符的开销不再算到本文件头上）。
        let picked: String?
        if kind == .file {
            // 批63b：只让 picker 显示白名单类型（取舍判据仍在各页的纯函数里，不在这里）。
            picked = PanelKit.pickFile(allowedExtensions: allowedExtensions)
        } else {
            picked = PanelKit.pickDirectory()
        }
        if let picked = picked {
            path = picked
        }
    }
}
