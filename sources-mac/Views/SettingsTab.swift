import SwiftUI
import Foundation

/// 「设置」页的界面状态。
///
/// 「测试连接 / 体检」是异步的（URLSession 回调不在主线程），结果与「运行中」标记
/// 需要有地方放；而本机只装了 Xcode Command Line Tools，SwiftUI 的 `@State`
/// 宏**不可用**（实测报错原文：
/// `external macro implementation type 'SwiftUIMacros.StateMacro' could not be found`），
/// 所以统一用 `ObservableObject` + `@StateObject`/`@ObservedObject`/`@Published`。
final class AITestModel: ObservableObject {
    @Published var running = false
    /// 测试结果文本（**不含完整密钥**，见 `AppSettings.redact`）
    @Published var result = ""
    /// nil = 还没测过
    @Published var ok: Bool? = nil
}

/// 预设下拉框的选择（同上：没有 `@State` 宏，只能挂在 ObservableObject 上）。
///
/// 与 `AppSettings` 分开：预设是**一次性动作**（选中即填表），不是要持久化的配置，
/// 所以重启后回到「自定义」，就像从没点过。
final class AIPresetModel: ObservableObject {
    @Published var choice: AIProviderPreset = .custom
}

/// 「设置」页：配置 AI（OpenAI 兼容接口）的 API 地址 / 密钥 / 模型。
///
/// 为什么要有这一页
/// ----------------
/// 引擎 `convert.py` 早就支持 `--llm-base/--llm-key/--llm-model`
/// （也读环境变量 `SLCN_LLM_BASE/SLCN_LLM_KEY/SLCN_LLM_MODEL`），
/// `--verify-refs` 在配置了这三项时会按《文献核实任务书》的同一份规范
/// 自动预跑一遍，把结果追加为「AI 建议（须人工核实）」。
/// 但编辑不会去改 shell 环境变量，所以以前这个能力等于没有入口 —— 这一页就是入口。
struct SettingsTab: View {

    @ObservedObject var settings: AppSettings
    /// 第 55 批：AI 预算闸（单篇 / 单批 token；只约束直连 API 通道）。
    /// 与「初排转换」页共用同一实例，转换时**现取**（与 AI 旋钮同一口径）。
    @ObservedObject var budget: AIBudgetSettings
    @StateObject private var tester = AITestModel()
    @StateObject private var preset = AIPresetModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                Divider()
                aiForm
                Divider()
                secretWarning
                Divider()
                privacy
                Divider()
                usage
            }
            .padding(16)
            .frame(maxWidth: 880, alignment: .leading)
        }
        // 操作条**固定在页面底部**（AUDIT-2026-10-17 §七 观察 1）：
        // 这一页很长，从前「保存 / 测试连接」在滚动区最末尾，编辑要一路滚到底才看得见，
        // 甚至以为页面没有保存按钮。放进 `safeAreaInset(edge: .bottom)` 后：
        // ① 任何时候都可见；② 滚动内容自动让位（不会被操作条盖住）。
        .safeAreaInset(edge: .bottom, spacing: 0) {
            actionBar
        }
    }

    // MARK: - 标题与厂商无关说明

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var headerText: String {
        "本刊不预设厂商：凡提供 OpenAI 兼容接口的服务都能用 —— "
        + "DeepSeek / OpenAI / 通义 / Moonshot / 智谱，"
        + "以及本机/自建端点（LM Studio 等，它们都提供 OpenAI 兼容端点）。"
    }

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var headerText2: String {
        "不配置也能用：「初排转换」照常生成《文献核实任务书》，"
        + "编辑把它交给自己的 AI 即可；配了则额外自动预跑一遍。"
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("AI 设置（可选）").font(.title3).bold()
            Text(headerText)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(headerText2)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - 预设 + 六个输入控件

    private var aiForm: some View {
        VStack(alignment: .leading, spacing: 10) {

            aiPresetSection
            aiProfilesSection
            aiChannelSection
            aiFutureKnobsSection
            aiRequestKnobsSection
            aiBudgetSection
            aiFulltextSection
        }
    }

    // ── 从 aiForm 拆出（第 101 条）：264 行的整段表达式让旧版 Swift 编译器类型检查超时
    //    （CI macos-14 报 unable to type-check ... in reasonable time）。Group 不引入布局。
    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var aiPresetText: String {
        "只填「API 地址 + 模型名」（不碰密钥），字段仍可自由编辑；"
        + "模型名会变，请以服务商文档为准。"
    }

    private var aiPresetSection: some View {
        Group {
            // ---------- 预设：只是填表快捷方式，填完照样能改 ----------
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("预设").frame(width: 76, alignment: .trailing)
                Picker("", selection: $preset.choice) {
                    ForEach(AIProviderPreset.allCases) { p in
                        Text(p.title).tag(p)
                    }
                }
                .labelsHidden()
                .frame(maxWidth: 260)
                .onChange(of: preset.choice) { _, newValue in apply(newValue) }
            }
            Text(aiPresetText)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

        }
    }

    // ── 从 aiForm 拆出（第 101 条）：264 行的整段表达式让旧版 Swift 编译器类型检查超时
    //    （CI macos-14 报 unable to type-check ... in reasonable time）。Group 不引入布局。
    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var aiProfilesText: String {
        "方案 = 地址 / 密钥 / 模型名 / 通道 / 检索 / 思考模式 / 输出上限 一整套。"
        + "选中一个会把它填进下面的输入框（还要点「保存」才生效，与「转换时用当前值」一致）。"
        + "「保存」时若「方案名」非空，就存成那一份（同名覆盖，不会越存越多）。"
    }

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var aiProfilesText2: String {
        "留空 = 只更新「当前配置」（与从前一样）；填了就会在下拉里多出一份，"
        + "下次打开 App 还在。⚠️ 方案里含密钥，与当前配置写在同一个 0600 文件里。"
    }

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var aiProfilesText3: String {
        "OpenAI 兼容的 base URL，要带服务商的版本路径"
        + "（如 https://api.deepseek.com/v1；不含 /chat/completions，本工具会自动补上）"
    }

    private var aiProfilesSection: some View {
        Group {
            // ---------- 第 82 条：保存过的配置方案（多套之间来回换） ----------
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("配置方案").frame(width: 76, alignment: .trailing)
                Picker("", selection: $settings.aiSelectedProfile) {
                    Text(settings.aiProfiles.isEmpty ? "（还没保存过方案）" : "（未选）").tag("")
                    ForEach(settings.aiProfiles) { p in
                        Text(p.name).tag(p.name)
                    }
                }
                .labelsHidden()
                .frame(maxWidth: 260)
                .onChange(of: settings.aiSelectedProfile) { _, newValue in
                    // 选中即「填进输入框」，**不落盘**（要用它还得点「保存」）
                    if !newValue.isEmpty { settings.applyProfile(named: newValue) }
                }
                Button("删除") {
                    settings.deleteProfile(named: settings.aiSelectedProfile)
                }
                .controlSize(.small)
                .disabled(settings.aiSelectedProfile.isEmpty)
            }
            Text(aiProfilesText)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("方案名").frame(width: 76, alignment: .trailing)
                TextField("例如：DeepSeek 云端 / 本机 Ollama", text: $settings.aiProfileName)
                    .textFieldStyle(.roundedBorder)
            }
            Text(aiProfilesText2)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

            Divider().padding(.leading, 86)

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("API 地址").frame(width: 76, alignment: .trailing)
                TextField("https://api.deepseek.com/v1", text: $settings.aiBase)
                    .textFieldStyle(.roundedBorder)
            }
            Text(aiProfilesText3)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("API 密钥").frame(width: 76, alignment: .trailing)
                SecureField("sk-…（本机/自建端点通常不需要密钥，可留空）",
                            text: $settings.aiKey)
                    .textFieldStyle(.roundedBorder)
            }
            HStack(spacing: 10) {
                Text("").frame(width: 76)
                Text("当前：\(AppSettings.mask(settings.aiKey))")
                    .font(.caption).foregroundStyle(.tertiary)
                Spacer()
            }

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("模型名").frame(width: 76, alignment: .trailing)
                TextField("deepseek-flash", text: $settings.aiModel)
                    .textFieldStyle(.roundedBorder)
            }
            Text("填写服务商文档里的模型 ID，例如 deepseek-flash / qwen-plus / glm-4 / gpt-4o-mini")
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

        }
    }

    // ── 从 aiForm 拆出（第 101 条）：264 行的整段表达式让旧版 Swift 编译器类型检查超时
    //    （CI macos-14 报 unable to type-check ... in reasonable time）。Group 不引入布局。
    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var aiChannelText: String {
        "`chat`（默认）= `{地址}/chat/completions`，请求体与从前逐字节相同；"
        + "`responses` = `{地址}/responses`（标准 Responses API）——"
        + "只有它才有「服务端检索工具」这个概念。"
        + "⚠️ Responses 的路径是 `{地址}/responses`：DeepSeek 文档给的 base 是 "
        + "https://api.deepseek.com（不带 /v1），OpenAI 要带 /v1，按服务商文档填。"
    }

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var aiChannelText2: String {
        "勾上 = 转换时给端点加 `tools:[{\"type\":\"web_search\"}]`，并要求模型先查官方来源、"
        + "把实际打开的页面 URL 写成每条上方的 `% 来源：…`；查不到的字段照旧省略并写 "
        + "`% ⚠ 待核实：…`（铁律不变：只写来源确认过的值）。\n"
        + "⚠️ 勾了不等于查到了：内置 `web_search` 是否真检索由端点与模型共同决定——"
        + "DeepSeek 需 `deepseek-v4-pro`（`deepseek-flash` 仍会**静默忽略**该工具："
        + "不报错、也不检索）。所以请点上面的「测试连接 / 体检」，看 ⑤ 检索探针 的实测结论——"
        + "本工具绝不假装已联网：跑完引擎会在日志与「六、合并记录」里报检索证据"
        + "（0 个 `web_search_call` = 本次只能凭记忆）。"
    }

    private var aiChannelSection: some View {
        Group {
            // ---------- 通道 + 联网检索（第 69 条：让「能不能联网」变成可选项与可实测项）----------
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("通道").frame(width: 76, alignment: .trailing)
                Picker("", selection: $settings.aiProtocol) {
                    ForEach(AIProtocol.allCases) { p in
                        Text(p.title).tag(p)
                    }
                }
                .labelsHidden()
                .frame(maxWidth: 420)
            }
            Text(aiChannelText)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("联网检索").frame(width: 76, alignment: .trailing)
                Toggle("本次真的去查官方来源（仅在 responses 通道生效）", isOn: $settings.aiWebSearch)
                    .toggleStyle(.checkbox)
                Spacer()
            }
            Text(aiChannelText2)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("检索证据").frame(width: 76, alignment: .trailing)
                Toggle("转换时先用 DeepSeek 独立 Search API 检索参考文献证据（默认关；按次计费）",
                       isOn: $settings.aiSearchRefs)
                    .toggleStyle(.checkbox)
                Spacer()
            }
            Text(aiSearchRefsText)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

        }
    }

    /// 「检索证据」开关的长文案（第 72 条起，P2）：与「联网检索」是**两件事**。
    private var aiSearchRefsText: String {
        "勾上 = 转换时先让工具自己用 **DeepSeek 独立 Search API** 逐条检索参考文献，"
        + "把候选来源（URL/站点/权威度/匹配分）写进任务书，并作为证据注入 AI 提示词；"
        + "AI 结果并入 `reference.bib` 前，引擎会校验 `% 来源：` 里的 URL 是否真在"
        + "「本次实际检索到的来源」里——**编造的 URL 会被挡下不并入**。\n"
        + "⚠️ 与上面的「联网检索」不同：它**不依赖**服务商/模型是否支持内置 `web_search`"
        + "（DeepSeek flash/v4-pro 都能用），但按成功请求计费（¥5/1000 次，每篇几十条文献"
        + "约 ¥0.1–0.2）；**默认关**，且需要上面的地址/密钥可用（否则引擎会跳过并提示）。"
    }

    // ── 从 aiForm 拆出（第 101 条）：264 行的整段表达式让旧版 Swift 编译器类型检查超时
    //    （CI macos-14 报 unable to type-check ... in reasonable time）。Group 不引入布局。
    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var aiFutureKnobsText: String {
        "一般不用改（两栏都留空就是今天的正确行为，命令行与从前逐字节相同）。"
        + "将来服务商把内置检索工具换成别的名字（例如 `web_search_preview`，"
        + "OpenAI 自己就有一个这种名字）或换了路径时，改这两栏即可，不必等新版本：\n"
        + "· 检索工具名 → 引擎 `SLCN_LLM_SEARCH_TOOL` / `--llm-search-tool`"
        + "（引擎默认 `web_search`）；\n"
        + "· 响应路径 → 引擎 `SLCN_LLM_RESPONSES_PATH` / `--llm-responses-path`"
        + "（引擎默认 `/responses`，缺前导 `/` 会自动补）。\n"
        + "⚠️ 改的是请求形状，不改变能力：端点到底会不会真的联网仍由上面的"
        + "「⑤ 检索探针」实测（判据是回复里有没有 `web_search_call`）+ 转换日志里的"
        + "「检索证据」为准——本工具绝不假装已联网。"
    }

    private var aiFutureKnobsSection: some View {
        Group {
            // ---------- 两个「未来兼容」旋钮（第 70 条；高级/次要，一般不用改）----------
            // 为什么放在「联网检索」下面而不是最上面：它们是**次要**的高级项，默认留空
            // 就等于今天的正确行为；放这里是为了让「检索相关的东西」挨在一起。
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("检索工具名").frame(width: 76, alignment: .trailing)
                TextField(AIAdvancedKnobs.defaultSearchTool, text: $settings.aiSearchTool)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 220)
                Text("留空 = 用引擎默认 \(AIAdvancedKnobs.defaultSearchTool)")
                    .font(.caption).foregroundStyle(.tertiary)
                Spacer()
            }
            if let h = AIAdvancedKnobs.hint(AIAdvancedKnobs.parseSearchTool(settings.aiSearchTool)) {
                Text("⚠ " + h)
                    .font(.caption).foregroundStyle(.red)
                    .padding(.leading, 86)
            }
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("响应路径").frame(width: 76, alignment: .trailing)
                TextField(AIAdvancedKnobs.defaultResponsesPath, text: $settings.aiResponsesPath)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 220)
                Text("留空 = 用引擎默认 \(AIAdvancedKnobs.defaultResponsesPath)")
                    .font(.caption).foregroundStyle(.tertiary)
                Spacer()
            }
            if let h = AIAdvancedKnobs.hint(AIAdvancedKnobs.parseResponsesPath(settings.aiResponsesPath)) {
                Text("⚠ " + h)
                    .font(.caption).foregroundStyle(.red)
                    .padding(.leading, 86)
            }
            Text(aiFutureKnobsText)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

        }
    }

    // ── 从 aiForm 拆出（第 101 条）：264 行的整段表达式让旧版 Swift 编译器类型检查超时
    //    （CI macos-14 报 unable to type-check ... in reasonable time）。Group 不引入布局。
    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var aiRequestKnobsText: String {
        "「跟随端点默认」= 不发送 thinking 字段（DeepSeek 文档 2026-10："
        + "thinking 默认就是开启，推理 token 按输出计费）；"
        + "参考文献核实是纯抽取/规范化任务，「关闭」既省钱也更确定。"
        + "本机/自建端点可能不认这个字段，那就保持「跟随端点默认」。"
    }

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var aiRequestKnobsText2: String {
        "默认 2048（= 引擎默认值，防啰嗦模型把预算烧在推理上、返回半截 bibtex）；"
        + "填 0 = 不封顶；留空 = 不设置该变量（效果同 2048）。"
        + "转换时用的是上面这些输入框里的当前值：地址/密钥/模型与这两个旋钮同一口径，"
        + "改完直接去「初排转换」就生效；点「保存」只是把它写进 ai.json，供下次启动读回。"
    }

    private var aiRequestKnobsSection: some View {
        Group {
            // ---------- 两个「请求体旋钮」：只有勾了 AI 开关才随子进程走 ----------
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("思考模式").frame(width: 76, alignment: .trailing)
                Picker("", selection: $settings.aiThinking) {
                    ForEach(AIThinkingMode.allCases) { m in
                        Text(m.title).tag(m)
                    }
                }
                .labelsHidden()
                .frame(maxWidth: 320)
            }
            Text(aiRequestKnobsText)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("输出上限").frame(width: 76, alignment: .trailing)
                TextField(AIMaxTokens.defaultText, text: $settings.aiMaxTokens)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 110)
                Text("tokens").font(.caption).foregroundStyle(.secondary)
                Spacer()
            }
            if let hint = AIMaxTokens.hint(settings.aiMaxTokens) {
                Text("⚠ " + hint)
                    .font(.caption).foregroundStyle(.red)
                    .padding(.leading, 86)
                Text("（填成这样时「保存」会被拒绝；运行时也不会把它发给引擎。）")
                    .font(.caption).foregroundStyle(.tertiary)
                    .padding(.leading, 86)
            }
            Text(aiRequestKnobsText2)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)

            statusLine

            if !tester.result.isEmpty {
                Text(tester.result)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .foregroundStyle(tester.ok == true ? Color.green : Color.orange)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.secondary.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            }
        }
    }

    // MARK: - 第 55 批：AI 预算闸（只约束直连 API 通道）
    //
    // 为什么放在「请求体旋钮」下面：它们回答的是同一个问题——「这次直连 API 会花多少」。
    // 外部 chat 两段式流程不产生 API 费用，所以这两栏对它**不生效**（界面必须如实说）。

    /// 预算说明长文案（提出来避免旧版 Swift 类型检查超时，见文件里同类写法）。
    private var aiBudgetText: String {
        "只约束「用 AI 处理参考文献」这条**直连 API** 通道：超过预计用量时引擎先打印账、"
        + "退出 2，不发起任何请求（不会静默超支）。默认 30000 / 1000000，0 = 不限；"
        + "留空 = 不传参数、引擎用默认。\n"
        + "外部 chat 的「AI 协同初排（两段式）」由编辑自己在豆包/千问/DeepSeek 等里操作，"
        + "**软件零上传、零 API、零费用**，这两栏的预算对它不生效。"
    }

    private var aiBudgetSection: some View {
        Group {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("单篇预算").frame(width: 76, alignment: .trailing)
                TextField(String(AIBudgetSettings.defaultArticle),
                          text: Binding(get: { budget.articleText },
                                        set: { budget.updateArticle($0) }))
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 140)
                Text("tokens（0 = 不限；默认 \(AIBudgetSettings.defaultArticle)）")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
            }
            if let hint = budget.articleHint {
                Text("⚠ " + hint)
                    .font(.caption).foregroundStyle(.red)
                    .padding(.leading, 86)
                Text("（填成这样时不会落盘，转换时也不会把它发给引擎。）")
                    .font(.caption).foregroundStyle(.tertiary)
                    .padding(.leading, 86)
            }
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("单批预算").frame(width: 76, alignment: .trailing)
                TextField(String(AIBudgetSettings.defaultBatch),
                          text: Binding(get: { budget.batchText },
                                        set: { budget.updateBatch($0) }))
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 140)
                Text("tokens（0 = 不限；默认 \(AIBudgetSettings.defaultBatch)）")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
            }
            if let hint = budget.batchHint {
                Text("⚠ " + hint)
                    .font(.caption).foregroundStyle(.red)
                    .padding(.leading, 86)
                Text("（填成这样时不会落盘，转换时也不会把它发给引擎。）")
                    .font(.caption).foregroundStyle(.tertiary)
                    .padding(.leading, 86)
            }
            Text(aiBudgetText)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)
        }
    }

    // MARK: - 第 63 批：直连 API 全文核查的上限 / 自动应用
    //
    // 放在预算闸下面：同属「这次直连 API 会花多少、会改什么」的旋钮。
    // 上限只约束直连 API（--llm-fulltext）；外部 chat 通道批 63 起不设字符上限。

    /// 说明长文案（提出来避免旧版 Swift 类型检查超时，见文件里同类写法）。
    private var aiFulltextText: String {
        "「API 核查字符上限」只约束「全文 AI 核查（直连 API）」这条通道：正文总字符超过上限时"
        + "引擎会先说明再处理。0 = 不限；留空 = 用引擎默认 \(AIBudgetSettings.defaultFulltextMaxChars)；"
        + "非法输入不落盘。外部 chat 通道不设字符上限（保留体积估算/超长提示）。\n"
        + "「自动应用」默认开：通过批 57 安全闸的 BIB/META/FN 建议会写进产物（先备份 .ai前、"
        + "0 条不写、冲突拒绝、逐条记录）；《…_AI全文复核.md》报告照常生成。关掉 = 只出报告"
        + "（与旧版 report-only 相同；转换时传 --no-llm-fulltext-apply）。"
    }

    private var aiFulltextSection: some View {
        Group {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("核查上限").frame(width: 76, alignment: .trailing)
                TextField(String(AIBudgetSettings.defaultFulltextMaxChars),
                          text: Binding(get: { budget.fulltextMaxCharsText },
                                        set: { budget.updateFulltextMaxChars($0) }))
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 140)
                Text("字符（0 = 不限；留空 = 默认 \(AIBudgetSettings.defaultFulltextMaxChars)）")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
            }
            if let hint = budget.fulltextMaxCharsHint {
                Text("⚠ " + hint)
                    .font(.caption).foregroundStyle(.red)
                    .padding(.leading, 86)
                Text("（填成这样时不会落盘，转换时也不会把它发给引擎。）")
                    .font(.caption).foregroundStyle(.tertiary)
                    .padding(.leading, 86)
            }
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("自动应用").frame(width: 76, alignment: .trailing)
                Toggle("全文核查结果自动应用（走安全闸，默认开）",
                       isOn: Binding(get: { budget.fulltextAutoApply },
                                     set: { budget.updateFulltextAutoApply($0) }))
            }
            Text(aiFulltextText)
                .font(.caption).foregroundStyle(.tertiary)
                .padding(.leading, 86)
        }
    }

    // MARK: - 固定底部操作条（保存 / 测试连接）

    /// 页面底部**常驻**操作条：位置由 `body` 的 `safeAreaInset(edge: .bottom)` 决定，
    /// 这里只管内容与材质背景。
    ///
    /// 为什么必须固定（AUDIT-2026-10-17 §七 观察 1）：这一页内容很长，从前
    /// 「保存 / 测试连接 / 清空」在滚动区的**最末尾**，编辑要一路滚到底才看得见，
    /// 实际反馈是"以为这页没有保存按钮"。固定后还顺带让 ⌘S 的目标永远在眼前。
    private var actionBar: some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: 10) {
                Button {
                    if settings.saveAI() { tester.result = ""; tester.ok = nil }
                } label: {
                    Label("保存", systemImage: "square.and.arrow.down")
                }
                // 快捷键挂在视图层级上，与按钮是否在滚动区无关，⌘S 照旧有效。
                .keyboardShortcut("s", modifiers: .command)

                Button {
                    testConnection()
                } label: {
                    Label("测试连接 / 体检", systemImage: "bolt.horizontal.circle")
                }
                .disabled(tester.running)

                Button("清空") {
                    settings.clearAI()
                    preset.choice = .custom
                    tester.result = ""
                    tester.ok = nil
                }
                // ⚠️ 语义（2026-10-15 用户实测报的 bug，第 62 条）：「清空」只清**界面上的输入框**，
                //    磁盘上已保存的 ai.json 不动（从前这里会顺手落盘，一按就把配置抹了）。
                //    说明贴在按钮旁边，免得编辑以为按了等于「把配置删了」而不敢按。
                Text("只清空输入框，不动已保存的配置；点「保存」才会覆盖".plainMarkdown)
                    .font(.caption).foregroundStyle(.tertiary)
                    .lineLimit(1)

                if tester.running {
                    ProgressView().controlSize(.small)
                    Text("正在体检…")
                        .font(.caption).foregroundStyle(.secondary)
                } else if !tester.result.isEmpty {
                    // 完整清单在表单末尾；操作条上给一行摘要，免得滚回去找。
                    Text(tester.ok == true ? "✅ 体检通过"
                                           : "⚠️ 体检有失败项（完整清单见上方）")
                        .font(.caption)
                        .foregroundStyle(tester.ok == true ? Color.green : Color.orange)
                        .lineLimit(1)
                }

                Spacer(minLength: 8)

                Text(settings.aiDirty ? "● 有未保存的修改" : "")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .lineLimit(1)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .frame(maxWidth: 880, alignment: .leading)   // 与滚动内容的宽度对齐
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(.bar)
    }


    /// 点预设：只填地址与模型名（`自定义` 时两个都是 nil，什么都不动）。
    /// **绝不碰密钥** —— 密钥是编辑自己粘的，换预设不该把它清掉。
    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var applyText: String {
        "✔ 已配置：\(settings.aiSummary)（密钥 \(AppSettings.mask(settings.aiKey))）"
        + " —— 「初排转换」页勾选「用 AI 处理参考文献」即可生效。"
    }

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var applyText2: String {
        "尚未配置：地址与模型名都填上才算配置完成；"
        + "转换时若勾了 AI 开关也只会生成《文献核实任务书》。"
    }

    private func apply(_ p: AIProviderPreset) {
        let filled = p.applied(base: settings.aiBase, model: settings.aiModel)
        settings.aiBase = filled.base
        settings.aiModel = filled.model
    }

    @ViewBuilder private var statusLine: some View {
        if !settings.aiStatus.isEmpty {
            Text(settings.aiStatus).font(.caption).foregroundStyle(.secondary)
        }
        if settings.aiConfigured {
            Text(applyText)
                .font(.caption).foregroundStyle(.green)
            Text("请求体：\(settings.aiEngineKnobs.summary)"
                 + (settings.aiDirty ? "（⚠ 有未保存的修改：转换仍用上面填的当前值，"
                                      + "但下次启动会回到已保存的值）" : ""))
                .font(.caption).foregroundStyle(settings.aiDirty ? Color.orange : Color.secondary)
        } else {
            Text(applyText2)
                .font(.caption).foregroundStyle(.orange)
        }
    }

    // MARK: - 明文存储提示（编辑部要求：必须写在界面上）

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var secretWarningText: String {
        "文件权限已收紧为 0600（仅当前用户可读写），目录为 0700。"
        + "请勿把这个文件放进网盘/云同步目录，也不要连同仓库一起分享。"
    }

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var secretWarningText2: String {
        "工具自身不会打印完整密钥：界面上只显示前 4 位（如 sk-a…），"
        + "日志里只写「已配置密钥」；密钥不进命令行参数"
        + "（走环境变量 SLCN_LLM_KEY 传给子进程，避免在 ps 里被同机其它用户看到），"
        + "也不会写进任何产物文件。"
    }

    private var secretWarning: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("⚠️ 密钥以明文保存在本机用户目录，请勿共享或同步该文件。")
                .font(.callout).bold()
                .foregroundStyle(.orange)
            Text("存放位置：\(settings.aiStoreURL.path)")
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
            Text(secretWarningText)
                .font(.caption).foregroundStyle(.secondary)
            Text(secretWarningText2)
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    // MARK: - 隐私：到底把什么发出去了

    /// 编辑部要求写清楚「上传了什么」：勾了 AI 开关以后，稿件里**哪些文字**会离开本机。
    /// 口径以引擎 `convert.py` ⑪ 块（`--verify-refs` 的 AI 预跑）为准：
    /// 只发「编辑部规范文本」+「逐条参考文献条目原文」，**不发**正文/摘要/作者信息/全文。
    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var privacyText: String {
        "① 编辑部规范文本（《文献核实任务书》里那份固定的核实口径）；\n"
        + "② 参考文献条目原文（逐条发，不拼成整篇）。"
    }

    /// 长文案：提出来是为了让旧版 Swift 的类型检查器别超时（第 101 条）；文本**逐字未动**。
    private var privacyText2: String {
        "不配置也完全可用：不填这些设置，「初排转换」照常生成《文献核实任务书》，"
        + "由编辑把它交给自己的 AI，效果一样（只是少了自动预跑这一步）。"
    }

    private var privacy: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("这个工具会上传什么").font(.callout).bold()
            Text("勾了「用 AI 处理参考文献」时，只有两样东西会发给上面填的那个端点：")
                .font(.caption).foregroundStyle(.secondary)
            Text(privacyText)
                .font(.caption).foregroundStyle(.secondary)
                .padding(.leading, 12)
            Text("不发送：正文、摘要、作者信息、稿件全文 —— 都留在本机。".plainMarkdown)
                .font(.caption).bold().foregroundStyle(.secondary)
            Text("两段式「AI 协同初排」（外部 chat，第 55 批）：软件**零上传、零 API、零费用** —— "
                 + "任务书由编辑自己交给外部 chat 模型，工具本身不发送任何内容。"
                 + "直连 API 通道只发「条目/片段」（参考文献条目原文 + 规范文本），不发正文全文。")
                .font(.caption).foregroundStyle(.secondary)
            Text(privacyText2)
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    // MARK: - 这一项用在哪 / 引擎侧对应关系

    // ── 从 usage 拆出（第 101 条）：三段 `Text("…" + "…" + …)` 的长拼接让旧版 Swift 编译器
    //    类型检查超时（CI macos-14 报 unable to type-check ... in reasonable time）。
    //    文本原样搬进 `String` 属性，视图体里只剩一个名字 —— 渲染逐字节相同。

    /// usage 第 1 段：转换命令会带上什么、密钥怎么传、AI 建议写到哪里。
    private var usageCommandText: String {
        "「初排转换」页 → 勾选「用 AI 处理参考文献」→ 转换命令会带上 "
        + "--verify-refs 与 --llm-base/--llm-model；"
        + "密钥不进命令行，而是通过环境变量 SLCN_LLM_KEY 传给子进程"
        + "（argv 在 ps 里对同机所有用户可见，环境变量不会）。"
        + "AI 建议写进同目录的《…_文献核实.md》，标为「AI 建议（须人工核实）」。"
    }

    /// usage 第 2 段：引擎侧的等价参数与环境变量（界面里填的值优先）。
    private var usageEngineParamsText: String {
        "引擎侧等价参数：--llm-base / --llm-key / --llm-model；"
        + "也可用环境变量 SLCN_LLM_BASE / SLCN_LLM_KEY / SLCN_LLM_MODEL"
        + "（界面里填的值优先于环境变量）。--llm-key 保留给命令行手工调用。"
        + "上面两个旋钮走环境变量 SLCN_LLM_THINKING / SLCN_LLM_MAX_TOKENS"
        + "（勾了 AI 开关才会设置）。"
    }

    /// usage 第 3 段：第 69/70 条那几个开关的命令行与环境变量，含手工验证端点。
    private var usageAdvancedParamsText: String {
        "第 69 条的两个开关也走命令行：通道 → `--llm-protocol chat|responses`"
        + "（也可用环境变量 SLCN_LLM_PROTOCOL）；联网检索 → `--llm-web-search`"
        + "（也可用环境变量 SLCN_LLM_WEB_SEARCH=1）。第 70 条的两个高级旋钮同理："
        + "`--llm-search-tool` / `--llm-responses-path`"
        + "（也可用环境变量 SLCN_LLM_SEARCH_TOOL / SLCN_LLM_RESPONSES_PATH）。"
        + "默认值/留空时不加任何参数：命令行与从前逐字节相同。"
        + "想手工验证端点支不支持检索，可以跑：\n"
        + "    python3 -m slcn.llm --probe --base <地址> --model <模型> --json\n"
        + "（退出码 0 支持 / 1 不支持 / 2 其他错误；密钥用 SLCN_LLM_KEY，别写进命令行。）"
    }

    private var usage: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("这些设置用在哪里").font(.callout).bold()
            Text(usageCommandText)
                .font(.caption).foregroundStyle(.secondary)
            Text(usageEngineParamsText)
                .font(.caption).foregroundStyle(.tertiary)
            Text(usageAdvancedParamsText)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.tertiary)
            Text("转换引擎：\(ConvertFeature.locateTool() ?? "未找到 Engine/convert.py")")
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(isToolFound ? Color.secondary : Color.red)
                .textSelection(.enabled)
        }
    }

    private var isToolFound: Bool { ConvertFeature.locateTool() != nil }

    // MARK: - 体检（原来只发一个 ping，现在是一张 ✓/✗/— 清单）

    private func testConnection() {
        tester.running = true
        tester.result = ""
        tester.ok = nil
        AIConnectionTester.test(base: settings.aiBase,
                                key: settings.aiKey,
                                model: settings.aiModel,
                                knobs: settings.aiEngineKnobs,
                                unsaved: settings.aiDirty) { ok, msg in
            tester.running = false
            tester.ok = ok
            tester.result = msg
        }
    }
}

/// 「测试连接 / 体检」：直接发**最多三个**最小的 OpenAI 兼容请求，把结果打成一
/// ✓/✗/— 清单（没有走引擎那条路：Swift 里发请求能拿到 **HTTP 状态码与响应原文**，
/// 比「子进程退出码」信息量大得多，而且不用先跑完一整次转换）。
///
///   ① `GET {base}/models` —— **尽力而为**：端点不提供模型列表不算失败（自建服务常见）；
///   ② 一次最小补全（`max_tokens` 很小）—— 失败时给出 HTTP 状态 + 原始错误体；
///   ③ 从 ② 的 `usage.completion_tokens_details.reasoning_tokens` 判断是不是思考模式
///      （**不发请求**；DeepSeek 文档 2026-10：thinking 默认开启，推理 token 按输出计费）；
///   ④ 只有 ① 与 ② 都成功时才试 `response_format={"type":"json_object"}`
///      （providers 要求 prompt 里出现 `json` 字样，见 `jsonProbePrompt`）。
/// 所以一共 3 个请求、每个 15s 超时 —— 体检要快速失败，不是压测。
///
/// 安全约定：请求头里的密钥**只进请求**，返回给界面的文本一律先过
/// `AppSettings.redact` 把密钥抹掉再截断（顺序不能反：先截断会把密钥截成
/// 「半把钥匙」），界面上永远只出现 `AppSettings.mask` 的前 4 位；
/// 不写任何日志文件。
enum AIConnectionTester {

    /// 每个请求的超时（秒）
    static let timeout: TimeInterval = 15

    /// ② 的 `max_tokens`：足够拿到 usage，又不至于让端点真的生成一整段
    static let chatProbeMaxTokens = 8
    /// ④ 的 `max_tokens`
    static let jsonProbeMaxTokens = 32

    /// 一个请求的结果（纯数据；`status < 0` 且 `error == nil` = 没拿到响应）
    struct Reply {
        var status: Int = -1
        var body: Data = Data()
        var error: String? = nil

        var raw: String { String(data: body, encoding: .utf8) ?? "" }
        /// 拿到了 HTTP 响应（哪怕是 404）—— 用于判断「端点活着」
        var reachedEndpoint: Bool { error == nil && status > 0 }
        var ok: Bool { (200..<300).contains(status) }
    }

    // MARK: - 纯函数（不发请求；headless harness 直接验证这几条）

    /// 结果区抬头。**密钥只出现前 4 位**（`AppSettings.mask`）。
    static func header(base: String, key: String, model: String,
                       knobs: AIEngineKnobs, unsaved: Bool) -> String {
        var lines = [
            "AI 体检（Swift 发最多 3 个请求，每个超时 \(Int(timeout))s；外加 ⑤ 引擎检索探针）",
            "地址 \(base)",
            "模型 \(model)",
            "密钥 \(AppSettings.mask(key))",
            "两个旋钮（转换时用）：\(knobs.summary)",
        ]
        if unsaved {
            lines.append("⚠ 有未保存的修改：①② 用的是上面填的地址/模型，两个旋钮同理 —— "
                         + "转换时都用当前填的值，不是已保存的值；"
                         + "点「保存」只为了让下次启动仍读回这些值。")
        }
        return lines.joined(separator: "\n")
    }

    /// ① 模型列表（尽力而为：**不提供也不算失败**）
    static func modelsLine(_ r: Reply, url: String, key: String) -> String {
        let label = "① 模型列表 GET \(url)"
        if let e = r.error {
            return "\(label)\n   — 没取到（\(e)）—— 该端点可能不提供模型列表，"
                 + "这不影响 ② 的连接判断。"
        }
        if r.ok {
            if let n = (json(r.body)?["data"] as? [Any])?.count {
                return "\(label)\n   ✓ HTTP \(r.status)，返回 \(n) 个模型"
            }
            return "\(label)\n   ✓ HTTP \(r.status)（响应不是标准 {data:[…]} 结构，仅供参考）"
        }
        return "\(label)\n   — 该端点不提供模型列表（HTTP \(r.status)）—— 不影响连接判断。\n"
            + "   原始响应：\(snippet(r, key: key))"
    }

    /// ② 最小补全。返回 (清单那几行, 是否可用, usage)
    static func chatLine(_ r: Reply, key: String) -> (text: String, ok: Bool, usage: [String: Any]?) {
        let label = "② 最小补全 POST /chat/completions（max_tokens=\(chatProbeMaxTokens)）"
        if let e = r.error {
            return ("\(label)\n   ✗ 连接失败：\(e)\n"
                    + "   （连不上不影响出稿：转换时引擎只是跳过 AI 预跑，"
                    + "《文献核实任务书》照常生成。）", false, nil)
        }
        let obj = json(r.body)
        let usage = obj?["usage"] as? [String: Any]
        guard r.ok else {
            return ("\(label)\n   ✗ HTTP \(r.status)\n   原始错误：\(snippet(r, key: key))",
                    false, usage)
        }
        let choice = (obj?["choices"] as? [[String: Any]])?.first
        let finish = choice?["finish_reason"] as? String
        var bits = ["HTTP \(r.status)"]
        if let finish { bits.append("finish_reason=\(finish)") }
        if let n = usage?["completion_tokens"] as? Int { bits.append("输出 \(n) token") }
        var text = "\(label)\n   ✓ OK（\(bits.joined(separator: "，"))）"
        if let finish, finish != "stop" {
            text += "\n   ⚠ finish_reason 不是 stop —— 可能被 max_tokens 截断，"
                 + "转换时引擎也会把这种情况标进任务书。"
        }
        return (text, true, usage)
    }

    /// ③ 思考模式判定（只看 ② 的 usage，**不发请求**）
    static func thinkingLine(usage: [String: Any]?) -> String {
        let label = "③ 思考模式（看 ② 的 usage.completion_tokens_details.reasoning_tokens）"
        guard let usage else {
            return "\(label)\n   — 端点没有回传用量（usage），无法判断是不是思考模式。"
        }
        let details = usage["completion_tokens_details"] as? [String: Any]
        let reasoning = (details?["reasoning_tokens"] as? Int) ?? 0
        if reasoning > 0 {
            return "\(label)\n   ✓ 当前是**思考模式**（reasoning_tokens=\(reasoning)）——"
                 + " 推理 token 也按输出计费，抽取任务建议把「思考模式」设为「关闭」。"
        }
        if details == nil {
            return "\(label)\n   ✓ 非思考模式"
                 + "（端点回传了 usage，但没有 completion_tokens_details.reasoning_tokens 字段）"
        }
        return "\(label)\n   ✓ 非思考模式（reasoning_tokens=0）"
    }

    /// ④ `response_format={"type":"json_object"}` 试探。⚠️ prompt 里**必须**有
    /// `json` 字样（DeepSeek 等端点会直接 `HTTP 400 Prompt must contain the word 'json'`），
    /// 所以这句话是请求的一部分，不能为了好看删掉。
    static let jsonProbePrompt =
        "Reply with a json object only: {\"ok\": true}。只输出 json，不要多余文字。"

    static func jsonProbePayload(model: String) -> [String: Any] {
        [
            "model": model,
            "messages": [["role": "user", "content": jsonProbePrompt]],
            "max_tokens": jsonProbeMaxTokens,
            "response_format": ["type": "json_object"],
        ]
    }

    static func jsonModeLine(_ r: Reply, key: String) -> String {
        let label = "④ response_format={\"type\":\"json_object\"} 试探（prompt 里带 json 字样）"
        if let e = r.error { return "\(label)\n   ✗ 请求失败：\(e)" }
        if r.ok { return "\(label)\n   ✓ 支持" }
        return "\(label)\n   ✗ 不支持（HTTP \(r.status)）\n   原始错误：\(snippet(r, key: key))"
    }

    /// 响应片段：**先脱敏、再截断**（服务端/模型有可能把请求头回显进正文 ——
    /// 实测用 mock 服务器就这么复现过；顺序反了会把密钥截成「半把钥匙」）。
    static func snippet(_ r: Reply, key: String, limit: Int = 400) -> String {
        var s = AppSettings.redact(r.raw, key: key)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return "（响应体为空）" }
        if s.count > limit { s = String(s.prefix(limit)) + "…" }
        return s
    }

    // MARK: - ⑤ 检索探针（**跑引擎**，不在这里重写协议）
    //
    // 为什么 ⑤ 与 ①②③④ 不同、必须走 Python 引擎（`python -m slcn.llm --probe --json`）：
    // 「这个端点到底会不会真的联网检索」的判据不是 HTTP 状态码，而是**回复里有没有
    // 检索证据**（`web_search_call`）。有的端点/模型（如 DeepSeek 的 deepseek-flash）会
    // **静默忽略** `tools:[{type:web_search}]` 并照样回 200，只看状态码就会误报成
    // 「支持」——那正是用户最反对的「静默假装」。
    // 这条判据（路径拼接、工具形状、检索证据的识别）**只能有一份实现**：引擎里那份。
    // 界面若自己发一遍这个请求，就会出现「GUI 说支持、引擎跑出来没检索」的分叉。

    /// 探针结果（引擎 `--json` 那一行 JSON 的映射；字段名与 `slcn/llm.py: probe_web_search` 对齐）
    struct SearchProbe: Equatable {
        var verdict = "error"          // supported | unsupported | error
        var verdictCN = "其他错误"
        var detail = ""
        var endpoint = ""
        var model = ""
        var nSearchCalls = 0
        var httpCode = 0
        var urls: [String] = []
        var body = ""
    }

    /// 引擎探针的参数（纯函数，harness 直接断言）：`python -m slcn.llm --probe --json`。
    /// **密钥不进 argv**（`ps` 可见）：由子进程环境变量 `SLCN_LLM_KEY` 传。
    static func searchProbeArguments() -> [String] {
        ["-m", "slcn.llm", "--probe", "--json"]
    }

    /// 第 70 条：探针还必须打**配置的**工具名与路径（纯函数，harness 直接断言）。
    ///
    /// 为什么非加不可：`probe_web_search()` 与真实转换用的是同一份引擎代码，但 ⑤ 是
    /// **另一个入口**（起子进程跑 CLI）。如果这里不带这两个参数，用户把工具名改成
    /// `web_search_preview` 之后，「体检说支持（测的是默认 web_search）、转换却打另一个
    /// 工具名」——那就是最坏的一种假证据。留空/用不了的值**不加参数**（引擎回落默认，
    /// 与转换的 `arguments()` 同一口径）。
    static func searchProbeKnobArguments(searchTool: String,
                                         responsesPath: String) -> [String] {
        var a: [String] = []
        if let t = AIAdvancedKnobs.envValue(AIAdvancedKnobs.parseSearchTool(searchTool)) {
            a += ["--llm-search-tool", t]
        }
        if let p = AIAdvancedKnobs.envValue(AIAdvancedKnobs.parseResponsesPath(responsesPath)) {
            a += ["--llm-responses-path", p]
        }
        return a
    }

    /// 解析引擎 `--json` 输出的那一行。**先整串、再逐行（从后往前）**试：
    /// 引擎的契约是「`--json` 时 stdout 只有一行 JSON，给人看的说明走 stderr」，
    /// 但真跑起来 stderr 的告警有可能被一起收到（实测：HTTP 400 时引擎会先往 stderr
    /// 打一行 `[llm] 调用失败：…`）——逐行兜底能把那一行 JSON 捞回来，
    /// 免得把「端点明确拒绝（不支持）」误报成「这次没测出来」。
    /// 全都解析不出来才返回 nil：界面据此说「没测出来」，**绝不瞎猜**成支持/不支持。
    static func parseSearchProbe(_ output: String) -> SearchProbe? {
        var candidates = [output]
        candidates += output.split(separator: "\n", omittingEmptySubsequences: false)
            .map(String.init)
            .filter { $0.hasPrefix("{") }
            .reversed()
        for text in candidates {
            guard let obj = json(Data(text.utf8)),
                  let verdict = obj["verdict"] as? String else { continue }
            var p = SearchProbe()
            p.verdict = verdict
            p.verdictCN = (obj["verdict_cn"] as? String) ?? verdict
            p.detail = (obj["detail"] as? String) ?? ""
            p.endpoint = (obj["endpoint"] as? String) ?? ""
            p.model = (obj["model"] as? String) ?? ""
            p.nSearchCalls = (obj["n_search_calls"] as? Int) ?? 0
            p.httpCode = (obj["http_code"] as? Int) ?? 0
            p.urls = (obj["urls"] as? [String]) ?? []
            p.body = (obj["body"] as? String) ?? ""
            return p
        }
        return nil
    }

    /// ⑤ 那一行文字（纯函数，headless 可验）。三种判词 + **本工具会不会真的用它**。
    ///
    /// 语气刻意分成两层（这是本轮最要紧的一条口径）：
    ///   · 「端点**会不会**检索」＝探针的实测结论；
    ///   · 「本次**会不会**用上」＝通道 + 开关。
    /// 两者可能不一致（端点支持但通道是 chat；或开关开了但端点忽略工具）——不一致时必须
    /// **说清是哪一种**，否则编辑会把「勾了开关」当成「已经联网核实」。
    static func searchProbeLine(_ p: SearchProbe?, engineNote: String?,
                                protocolWanted: AIProtocol, searchEnabled: Bool) -> String {
        let label = "⑤ 检索探针（跑引擎 `python -m slcn.llm --probe`，实测端点会不会真联网）"
        guard let p else {
            let why = engineNote.map { "（\($0)）" } ?? ""
            return "\(label)\n   — 跳过/没测出来\(why)：没找到引擎或解释器，"
                + "或引擎输出不是可解析的 JSON。转换时请以日志里的「检索证据」为准。"
        }
        var out = "\(label)\n"
        switch p.verdict {
        case "supported":
            out += "   ✓ **支持**：这个端点支持检索 → 会真的联网查官方来源"
            if p.nSearchCalls > 0 {
                out += "（收到 \(p.nSearchCalls) 个 web_search_call"
                    + (p.urls.isEmpty ? "）" : "，\(p.urls.count) 个来源 URL）")
            } else {
                out += "。"
            }
            if protocolWanted != .responses {
                out += "\n   ⚠ 但当前「通道」是 chat：chat 通道**不带**检索工具，"
                    + "要真联网请把「通道」设为 responses。"
            } else if !searchEnabled {
                out += "\n   ⚠ 「联网检索」开关**没勾**：本次不会带检索工具，请勾上。"
            } else {
                out += "\n   ✓ 通道与开关都就位：本次转换会**真的联网**核对官方来源。"
            }
        case "unsupported":
            out += "   ✗ **不支持** → 模型只能凭记忆"
            if p.httpCode > 0 { out += "（HTTP \(p.httpCode)）" }
            out += "\n   \(p.detail.isEmpty ? "端点没有执行检索。" : p.detail)"
            out += "\n   ⚠ 卷/期/页码/出版地**务必人工抽查**：凭记忆编错比空着更糟（本工具的纪律）。"
            if searchEnabled && protocolWanted == .responses {
                out += "\n   ⚠ 你已勾「联网检索」：本次**不会**真的查到网络——"
                    + "引擎会在日志与「六、合并记录」里写明「检索证据 0 个、按凭记忆对待」，绝不假装已联网。"
            }
        default:
            out += "   — **其他错误**：这次没测出来（既不算支持、也不算不支持）"
            out += "\n   \(p.detail.isEmpty ? "请检查地址/密钥/模型名与网络。" : p.detail)"
        }
        if !p.body.isEmpty {
            out += "\n   端点原话：\(AppSettings.redact(p.body, key: ""))"
        }
        return out
    }

    /// 探针子进程的硬性总时限（秒）：引擎自己 `--timeout 20`，这里再多给 10s 兜底。
    /// 为什么不用 `timeout`（15s，那是 HTTP 请求的超时）：探针要等引擎起 Python + 一次
    /// 联网请求，15s 会把「慢但成功」误判成超时。
    static let probeProcessTimeout = 30
    /// 传给引擎探针的 `--timeout`（整数秒；argument parser 只认整数）
    static let probeRequestTimeout = 20

    /// 真正跑引擎探针。**同步**执行（只在后台队列上调用）。
    ///
    /// 自建一个 `Process`（不走 `Shell.capture`）：需要给子进程**额外环境变量**
    /// （`PYTHONPATH` 与 `SLCN_LLM_KEY`），而 `Shell.capture` 不接受注入环境；
    /// 用它就只剩「把密钥放进 argv」这一条路，那正是本项目明确避免的做法。
    /// 另加信号量超时兜底：探针自己 20s 超时，这里再多等 10s，**绝不把体检永久卡住**。
    static func runSearchProbe(base: String, key: String, model: String,
                               timeoutSeconds: Int = probeProcessTimeout,
                               knobs: AIEngineKnobs = AIEngineKnobs()) -> (probe: SearchProbe?, note: String?) {
        guard let tool = ConvertFeature.locateTool() else {
            return (nil, "没找到 Engine/convert.py，无法定位引擎")
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = ConvertFeature.locatePython(toolDir: toolDir) else {
            return (nil, "没找到 python3")
        }
        // 第 70 条：把配置的工具名/路径也带上（留空则不加参数）——探针必须与转换
        // 打同一种请求，否则「体检说支持」不构成证据。默认参数让没有界面状态的调用方
        // （如 probe_e2e）行为一字不变。
        let args = searchProbeArguments()
            + searchProbeKnobArguments(searchTool: knobs.searchTool,
                                       responsesPath: knobs.responsesPath)
            + ["--base", base, "--model", model,
               "--timeout", String(probeRequestTimeout)]
        let engine = Process()
        engine.executableURL = URL(fileURLWithPath: py)
        engine.arguments = args
        engine.currentDirectoryURL = URL(fileURLWithPath: toolDir)
        var env = Shell.environment()
        env["PYTHONPATH"] = toolDir
        // 密钥走环境变量（不进 argv）。留空时不设置，引擎会发 "Bearer none"。
        if !key.isEmpty { env["SLCN_LLM_KEY"] = key }
        engine.environment = env

        // stdout 与 stderr **分开两根管道**：引擎的契约是「`--json` 时 stdout 只有那一行
        // JSON」，诊断/告警走 stderr（HTTP 400 时它会先往 stderr 打一行调用失败）。
        // 混在一起收，正常工作时也会把 JSON 弄脏（实测正是这么踩到的）。
        let outPipe = Pipe()
        let errPipe = Pipe()
        engine.standardOutput = outPipe
        engine.standardError = errPipe
        engine.standardInput = FileHandle.nullDevice
        do {
            try engine.run()
        } catch {
            return (nil, "启动引擎失败：\(error.localizedDescription)")
        }
        // 两根管道都要**并发**读完（只读一根，子进程写另一根写满缓冲就会挂住），
        // 再等退出；超时兜底保证体检绝不永久卡住。
        var outData = Data()
        var errData = Data()
        let group = DispatchGroup()
        let readQueue = DispatchQueue(label: "cn.edu.sysu.logic.toolbox.probe-reader")
        for (handle, isOut) in [(outPipe.fileHandleForReading, true),
                                (errPipe.fileHandleForReading, false)] {
            group.enter()
            readQueue.async {
                while true {
                    let chunk = handle.availableData
                    if chunk.isEmpty { break }
                    if isOut { outData.append(chunk) } else { errData.append(chunk) }
                }
                group.leave()
            }
        }
        if group.wait(timeout: .now() + .seconds(timeoutSeconds)) == .timedOut {
            engine.terminate()
            return (nil, "引擎探针超时（\(timeoutSeconds)s 内没有结束）")
        }
        engine.waitUntilExit()
        let text = String(data: outData, encoding: .utf8) ?? String(decoding: outData, as: UTF8.self)
        let probe = parseSearchProbe(text)
        if probe == nil {
            let tail = AppSettings.redact(text, key: key)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let errTail = AppSettings.redact(
                String(data: errData, encoding: .utf8) ?? "", key: key)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let why = tail.isEmpty ? (errTail.isEmpty ? "引擎没有输出" : String(errTail.suffix(200)))
                                    : "引擎输出无法解析：\(String(tail.suffix(200)))"
            return (nil, why)
        }
        return (probe, nil)
    }

    private static func json(_ data: Data) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    // MARK: - 真正发请求（只有这一段碰网络）

    static func test(base rawBase: String, key rawKey: String, model rawModel: String,
                     knobs: AIEngineKnobs, unsaved: Bool,
                     completion: @escaping (Bool, String) -> Void) {
        let key = rawKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let model = rawModel.trimmingCharacters(in: .whitespacesAndNewlines)

        // 与引擎 slcn/llm.py 的拼接口径保持一致：rstrip("/") 后拼 /chat/completions 与 /models
        var base = rawBase.trimmingCharacters(in: .whitespacesAndNewlines)
        while base.hasSuffix("/") { base.removeLast() }

        guard !base.isEmpty, !model.isEmpty else {
            completion(false, "请先填写「API 地址」与「模型名」（密钥可留空，"
                + "本机/自建端点通常不需要）。")
            return
        }
        guard let chatURL = URL(string: base + "/chat/completions"), chatURL.scheme != nil,
              let modelsURL = URL(string: base + "/models") else {
            completion(false, "API 地址不是合法的 URL：\(rawBase)")
            return
        }

        let head = header(base: base, key: key, model: model, knobs: knobs, unsaved: unsaved)
        // 同步发请求会阻塞：整个体检放到后台队列上跑，结果再回主线程。
        DispatchQueue.global(qos: .userInitiated).async {
            var lines: [String] = [head]

            // ① 模型列表（尽力而为）
            let models = send(modelsURL, method: "GET", key: key, payload: nil)
            lines.append(modelsLine(models, url: modelsURL.absoluteString, key: key))

            // ② 最小补全（刻意不带 thinking / response_format：测的是端点默认行为）
            let chatPayload: [String: Any] = [
                "model": model,
                "messages": [["role": "user", "content": "ping"]],
                "max_tokens": chatProbeMaxTokens,
            ]
            let chat = send(chatURL, method: "POST", key: key, payload: chatPayload)
            let (chatText, chatOK, usage) = chatLine(chat, key: key)
            lines.append(chatText)

            // ③ 不发请求，只看 ② 的 usage
            lines.append(thinkingLine(usage: usage))

            // ④ 只在 ① 与 ② 都成功时才试
            //    （① 的判定用 reachedEndpoint：拿到了 HTTP 状态就算活着）
            if models.reachedEndpoint && chatOK {
                let jsonReply = send(chatURL, method: "POST", key: key,
                                     payload: jsonProbePayload(model: model))
                lines.append(jsonModeLine(jsonReply, key: key))
            } else {
                lines.append("④ response_format 试探：— 跳过（①② 都成功时才测）")
            }

            // ⑤ 检索探针：**跑引擎**（唯一的协议实现），判据是「回复里有没有检索证据」。
            // 与 ④ 同一个闸门（①② 都活着才测）；没配地址/模型时 guard 早就返回了。
            // ⚠️ 这一条**必须实测**，不能靠「工具的开关状态」推断：有的端点/模型（如
            //    DeepSeek 的 deepseek-flash）会静默忽略 `web_search` 并照样回 200 ——
            //    只报「已启用联网检索」就是假装。
            if models.reachedEndpoint && chatOK {
                // 把**当前界面上的**旋钮一并传下去（第 70 条）：探针要打的工具名/路径
                // 就是转换时真正会用的那一份，否则体检结论对不上真实请求。
                let (probe, note) = runSearchProbe(base: base, key: key, model: model,
                                                   knobs: knobs)
                lines.append(searchProbeLine(probe, engineNote: note,
                                             protocolWanted: knobs.apiProtocol,
                                             searchEnabled: knobs.webSearch))
            } else {
                lines.append("⑤ 检索探针：— 跳过（①② 都成功时才测；它走引擎 "
                             + "`python -m slcn.llm --probe`，不在这里重写协议）")
            }

            // ⚠️ 脱敏是**最后一步、且作用在完整文本上**（正文里各片段已脱敏过一次，
            //    这里兜底；截断放在脱敏之后，绝不先截断）。
            var text = AppSettings.redact(lines.joined(separator: "\n"), key: key)
            if text.count > 4000 { text = String(text.prefix(4000)) + "\n…（体检输出过长，已截断）" }
            DispatchQueue.main.async { completion(chatOK, text) }
        }
    }

    /// 同步发一个请求 —— **只能在后台线程调用**（用信号量等回调）。
    /// 超时拆成两层：URLSession 自己的 `timeoutInterval`，外加信号量兜底，
    /// 免得某些网络栈下回调迟迟不来把「体检」永久卡在「正在体检」。
    private static func send(_ url: URL, method: String, key: String,
                            payload: [String: Any]?) -> Reply {
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = timeout
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer " + (key.isEmpty ? "none" : key), forHTTPHeaderField: "Authorization")
        if let payload { req.httpBody = try? JSONSerialization.data(withJSONObject: payload) }

        let sem = DispatchSemaphore(value: 0)
        var reply = Reply()
        URLSession.shared.dataTask(with: req) { data, resp, err in
            reply.status = (resp as? HTTPURLResponse)?.statusCode ?? -1
            reply.body = data ?? Data()
            reply.error = err?.localizedDescription
            sem.signal()
        }.resume()
        if sem.wait(timeout: .now() + timeout + 5) == .timedOut {
            reply.error = "超时（\(Int(timeout)) 秒内没有响应）"
        }
        return reply
    }
}
