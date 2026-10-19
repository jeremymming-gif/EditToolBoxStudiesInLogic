import Foundation

/// 「初排转换」—— 调用 Engine（Python 转换引擎）把 Word 文稿 / LaTeX 源文件
/// 转成符合《逻辑学研究》模板的 LaTeX 初排稿。
///
/// 为什么要 shell 出去调 Python，而不是用 Swift 重写
/// -------------------------------------------------
/// 核心工作量在**直接解析 Word 的 XML**：OMML 公式转 LaTeX、
/// `<w:drawing>` 图片提取、`<w:tbl>` 表格转 tabular、脚注分类、引文提升……
/// Swift 生态里没有与 `python-docx` / `lxml` 相当的成熟替代，
/// 硬写一遍等于把 6600 行逻辑重做且必然引入新 bug。
/// 因此这里只做**界面 + 参数拼装 + 找路径**，真正的转换交给 Python。
enum ConvertFeature {

    /// 转换选项（对应 convert.py 的命令行参数）
    struct Options {
        var source = ""          // --source（必填）
        var out = ""             // --out（必填）
        var metadata = ""        // --metadata
        var bib = ""             // --bib
        var refs = ""            // --refs
        var author = ""          // --author
        var name = ""            // --name
        var lang = ""            // --lang（空=自动）
        var sty = ""             // --sty
        var compile = true       // --compile
        var clean = true         // --clean
        /// 生成《文献核实任务书》→ `--verify-refs`。**默认开**（批 58 与 Windows 对齐：
        /// `sources-win/main.py` 的「出文献核实任务书」勾选框默认 `True`）。理据：引擎只在
        /// `--verify-refs` 分支里查编辑部引用库，默认关会让编辑以为「初排根本没比对」。
        /// ⚠️ 这是本页开关的**初值**，不是持久化开关：本勾选框没有 UserDefaults 键，每次启动
        /// 都是新的 `Options`（`ConvertTab` 用 `@StateObject ConvertModel` 持有本次会话状态），
        /// 所以不存在「覆盖老用户已保存选择」；用户运行中取消勾选照常生效并原样传给 argv。
        /// 「查不查引用库」另有持久化开关 `BibDbSettings.lookupEnabled`（缺省 true、显式关过
        /// 读回 false）——它与本默认值正交，不会被这次改动覆盖（关着仍传 `--no-bibdb`）。
        var verifyRefs = true    // --verify-refs（生成《文献核实任务书》，批 58 默认开）
        var sourcePdf = false    // --source-pdf
        var quoteThreshold = 105 // --quote-threshold
        var scanOnly = false     // --scan
        var force = false        // --force（目标目录混有非本工具文件时不再拦截）
        // ---------- AI（可选；配置来自「设置」页 AppSettings） ----------
        /// 界面开关「用 AI 处理参考文献」：勾了 = 生成任务书 **并且** 按同一份规范
        /// 在本机直连 AI 预跑一遍（引擎侧就是在 --verify-refs 分支里读 LLM 配置）。
        var useAI = false        // 不加参数，只决定下面三个传不传 + 是否加 --verify-refs
        var llmBase = ""         // --llm-base（OpenAI 兼容 base URL）
        var llmKey = ""          // --llm-key（⚠️ 只传参，绝不打印/写日志）
        var llmModel = ""        // --llm-model
        /// 通道：`chat`（默认）/ `responses`（第 69 条；引擎侧 `--llm-protocol`）。
        /// **默认值必须留在 `chat`**：不传这个参数时命令行与从前逐字节相同。
        var llmProtocol = "chat"
        /// 联网检索开关（默认关）→ `--llm-web-search`（引擎侧加 `tools:[{type:web_search}]`）。
        /// 只在 responses 通道生效；**勾上也不代表端点真的会检索**（DeepSeek 的
        /// `deepseek-flash` 会静默忽略、`deepseek-v4-pro` 才真检索），
        /// 所以界面上要如实写清，并由「设置」页的 ⑤ 探针实测。
        var llmWebSearch = false
        /// P2（第 72 条起）：文献检索证据开关（默认关）→ `--search-refs`。
        /// 它走 DeepSeek **独立** Search API（不是端点内置检索工具），因此只在勾了 AI、
        /// 且地址/密钥可用时有意义；开启后引擎先检索、再把证据注入提示词并在合并前校验来源 URL。
        var searchRefs = false
        /// 检索工具类型名（第 70 条）→ `--llm-search-tool`。**空 = 不传这个参数**，
        /// 引擎用默认 `web_search`（于是命令行与从前逐字节相同）。
        var llmSearchTool = ""
        /// responses 路径（第 70 条）→ `--llm-responses-path`。**空 = 不传**，
        /// 引擎用默认 `/responses`。
        var llmResponsesPath = ""
        /// 界面开关「保留原 reference.bib（不并入 AI 结果）」→ `--no-ai-merge`。
        /// 只在勾了「用 AI 处理参考文献」时才显示（没勾 AI 就压根没有合并通道）。
        var keepOriginalBib = false // --no-ai-merge
        // ── 第 132 条：核实成本闸门 + 引用库（由「引用库设置」页存 UserDefaults，
        //    `ConvertTab.run()` 在运行时现取）。默认值 = **一个参数都不加**，
        //    所以没碰过这些设置的用户，命令行与接入前逐字节相同。
        /// 核实条数上限 → `--verify-max`（nil = 不传，引擎用默认 120；0 = 不限）
        var verifyMax: Int? = nil
        /// 核实前先算账 → `--verify-plan`（引擎打印核实计划后退出 0；**本次不真正核实**）
        var verifyPlan = false
        /// 允许全量核实 → `--verify-all-refs`（越过上限闸门）
        var verifyAllRefs = false
        /// 本次不查引用库 → `--no-bibdb`
        var noBibdb = false
        /// 手动指定的引用库文件 → `--bibdb-path`。空 = 不传（引擎按 `SLCN_NOTES` 自动找）。
        /// 只在「查库开着 + 库文件确实存在」时由界面填进来：指一个不存在的库只会让引擎
        /// 多打一条告警，不如不传。
        var bibdbPath = ""
        // 编辑侧出版信息（作者稿里没有；缺了 PDF 页眉会是「xxxx 年第 x 期，1–10」）
        var vol = ""             // --vol
        var issue = ""           // --issue
        var pubYear = ""         // --year
        var pages = ""           // --pages（如 137-152，自动算出页数）
        var received = ""        // --received
        var editor = ""          // --editor
        var grants = ""          // --grants
        // 正文信息手工覆盖（作者稿解析不出来、或解析得不对时的兜底输入路径）。
        // 空 = 不覆盖，由引擎沿用自动解析结果；与旧版行为完全一致。
        // 引擎侧接口：build_docx/build_latex 的 `overrides: dict | None`。
        var titleCn = ""         // --title-cn（中文题名）
        var authorCn = ""        // --author-cn（第一作者）
        var affiliationCn = ""   // --affiliation-cn（单位）
        var email = ""           // --email（邮箱）
        var abstractCn = ""      // --abstract-cn（中文摘要）
        var titleEn = ""         // --title-en（英文题名）
        var abstractEn = ""      // --abstract-en（英文摘要）
        // ── 第 55 批：两段式「AI 协同初排」（外部 chat 免费流程；默认关）
        //    默认值下 `arguments()` 一个协同参数都不加 → 命令行与批 54 逐字节相同。
        /// 界面勾选「AI 协同初排（两段式）」。**不直接进 argv**：真正跑哪一段由
        /// `coopPhase` 决定（`CoopFeature.prepare` 在运行时按段设置）。
        var coop = false
        /// 本次运行属于两段式的哪一段（`.off` = 普通转换，批 54 行为）。
        var coopPhase: CoopFeature.Phase = .off
        /// 第二段的回填输入 → **每个路径一个** `--coop-replies`（文件/目录可混合、按顺序）。
        /// 批 56 起是**列表**：用户真测时 chat 把回填拆成 JSON + BibTeX 两个文件，
        /// 批 55 只能传一个（多选被折叠成父目录）→ 丢掉了 BibTeX 回填。空 = 不传（界面会拦）。
        var coopRepliesPaths: [String] = []
        /// 第一段自定义暂存目录 → `--coop-stage`；第二段由暂存目录发现结果填。
        var coopStage = ""
        /// 第二段「一键修改产物」→ `--coop-apply`（默认不勾；**必须在跑第二段前决定**）
        var coopApply = false
        /// 第 59 批：全文 AI 核查（任务书附正文）→ 两段式第一段 `--coop-fulltext context|full`。
        /// **默认不勾**：不勾时一个参数都不加 → 普通/协同默认 argv 与批 58 逐字节相同。
        /// 只属于第一段（引擎仅在 `--coop-taskbook-only` 下识别该旗标）；第二段 argv 由
        /// `CoopFeature.secondPhaseArguments` 拼最小集，天然不含它。软件零上传/零费用：
        /// 正文只写进本机任务书，发不发由编辑自己把它复制给外部 chat。
        var coopFulltext = false
        /// 第 59 批：全文核查范围（**仅** `coopFulltext == true` 时进 argv）。
        var coopFulltextScope: FulltextScope = .context
        /// 第 55 批：直连 API 通道的单篇 token 预算 → `--ai-budget-tokens`。
        /// `nil` / 等于引擎默认（30000）时不传；仅非协同 + 勾了 AI 时传（外部 chat 零费用）。
        var aiBudgetTokens: Int? = nil
        /// 直连 API 通道的单批 token 预算 → `--ai-batch-budget-tokens`（默认 1000000）
        var aiBatchBudgetTokens: Int? = nil

        /// AI 是否已配置到「可以调用」：**地址 + 模型名**（密钥可空——本机/自建端点不要密钥）。
        /// 口径与引擎 `slcn/llm.py` 的 `LLMConfig.ready`（`base and model`）保持一致，
        /// 免得界面说「已配置」而引擎那边 `cfg.ready` 是假。
        var aiReady: Bool {
            !llmBase.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && !llmModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    /// 第 59 批：全文 AI 核查的**范围**（引擎契约 `--coop-fulltext {context,full}`）。
    ///
    /// 这里只做界面显示与参数映射，不参与任何判定：正文截断/字数上限由引擎按实际
    /// token 预算决定，壳子**不替引擎算**（否则两处口径迟早分叉）。
    enum FulltextScope: String, CaseIterable, Equatable {
        case context
        case full

        /// 界面单选文案（两壳一致；括号里的上限是引擎侧设计值，不是壳子在限制）。
        var title: String {
            switch self {
            case .context: return "引用上下文（默认，约 4 万字上限）"
            case .full: return "全文（约 8 万字上限）"
            }
        }

        /// 原样交给引擎的参数值（契约固定小写 `context` / `full`）。
        var flagValue: String { rawValue }
    }

    // MARK: - 开关联动（纯函数，headless 可验证）

    /// 三个复选框在纯逻辑里的身份（`tools/ai_settings_test` 直接按它调 `toggled`）。
    enum Checkbox { case verifyRefs, useAI, keepOriginalBib }

    /// 勾选 / 取消某个复选框之后的**新选项**。
    ///
    /// 联动规则（**方案 A**，用户 2026-10-15 决定）只有这一份实现，SwiftUI 只负责绑定：
    ///
    ///   · 勾上「用 AI 处理参考文献」→ **自动把「生成参考文献核实任务书」勾上**，
    ///     界面同时把它置灰（`verifyRefsLocked`）。为什么必须绑在一起：AI 的逐条建议
    ///     与「六、合并记录」**都写在这份任务书里**，不生成它，编辑手上就只剩一份被
    ///     机器改过的 `reference.bib`、没有任何审阅材料 —— 这正是第 64 条那套保障
    ///     要避免的局面。
    ///   · 取消 AI 勾选 → 任务书开关恢复可操作，**不改它的值**（用户明确要求：
    ///     不许顺手把编辑原来勾的选择抹掉；他想取消就自己再点一下）。
    ///   · 置灰期间任务书若被程序化改掉（旧界面、脚本、`@Published` 直接赋值），
    ///     拉回 `true` —— 「AI 开着但不出任务书」是自相矛盾的状态，合并记录会无处可写。
    static func toggled(_ which: Checkbox, to on: Bool, in o: Options) -> Options {
        var n = o
        switch which {
        case .useAI:
            n.useAI = on
            if on { n.verifyRefs = true }      // 取消时**不写** verifyRefs（保留编辑的选择）
        case .verifyRefs:
            n.verifyRefs = n.useAI ? true : on // 被 AI 锁住时只允许 true
        case .keepOriginalBib:
            n.keepOriginalBib = on
        }
        return n
    }

    /// 任务书开关是否应置灰：AI 勾上就锁住（联动规则见 `toggled`）。
    static func verifyRefsLocked(_ o: Options) -> Bool { o.useAI }

    // MARK: - 环境变量增量（纯函数，headless 可验证）

    /// 把 AI 配置映射成**只含增量**的环境变量字典。传什么、不传什么：
    ///
    ///   - `PYTHONPATH`：`vendor/` 存在才设（pypinyin，见 `run()` 的长注释）；
    ///   - `SLCN_LLM_KEY`：密钥**永远不进 argv**（argv 在 `ps` 里对同机所有用户可见），
    ///     只在这里给；没填密钥就不设（引擎 `config_from_env` 会回落到空串）。
    ///   - `SLCN_LLM_THINKING`：`跟随端点默认` 时**不设** —— 引擎侧就是「请求体里不带
    ///     这个字段」，与从前逐字节相同（见 `AIThinkingMode` 的说明）。
    ///   - `SLCN_LLM_MAX_TOKENS`：留空时**不设**（引擎默认 2048）；`0` 照发（= 不封顶）；
    ///     填了非整数也**不设**，但 `knobWarnings` 会返回一行说明 —— 不许静默。
    ///
    /// base/model 走 argv（`--llm-base/--llm-model`），不在环境里再重复一份。
    /// 没勾「用 AI 处理参考文献」时这几个 `SLCN_LLM_*` 一个都不设（与从前一致）。
    static func engineEnvironment(_ o: Options, toolDir: String,
                                  knobs: AIEngineKnobs) -> [String: String] {
        var extra: [String: String] = [:]
        let vendor = toolDir + "/vendor"
        if FileManager.default.fileExists(atPath: vendor) {
            extra["PYTHONPATH"] = vendor
        }
        guard o.useAI else { return extra }
        let aiKey = o.llmKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if !aiKey.isEmpty {
            extra["SLCN_LLM_KEY"] = aiKey
        }
        if let v = knobs.thinking.envValue {
            extra["SLCN_LLM_THINKING"] = v
        }
        if let v = AIMaxTokens.envValue(knobs.maxTokens) {
            extra["SLCN_LLM_MAX_TOKENS"] = v
        }
        return extra
    }

    /// 本次转换真正要用的两个旋钮（思考模式 / 输出上限）。
    ///
    /// 口径与 base/key/model **完全一致**：由调用方（「初排转换」页）把界面上
    /// **当前**的值现取现传 —— 设置页改完立刻生效，不必先点「保存」。
    /// `live == nil` 时回落到读**已保存**的 `ai.json`（`AppSettings.loadEngineKnobs`）：
    /// 命令行 / 自检这类没有界面状态的调用方照旧能用，行为与从前一致。
    ///
    /// ⚠️ 为什么非得由调用方传：`run()` 在**后台线程**上跑，而 `AppSettings` 的
    ///    `@Published` 值属于主线程，不能在那里读；`AIEngineKnobs` 是值类型
    ///    （`String` + `enum`），在主线程取好再跨线程传是安全的。
    static func effectiveKnobs(_ live: AIEngineKnobs?,
                               storeDirectory: URL? = nil) -> AIEngineKnobs {
        live ?? AppSettings.loadEngineKnobs(storeDirectory: storeDirectory)
    }

    /// 旋钮里「填了但用不了」的值 → 写进运行日志的一行提示（界面另有红字）。
    /// 为什么要有：不设环境变量/不传参数等于「用引擎默认」，不说一声编辑会以为填的值生效了。
    static func knobWarnings(_ knobs: AIEngineKnobs) -> [String] {
        var out: [String] = []
        if case .invalid(let hint) = AIMaxTokens.parse(knobs.maxTokens) {
            out.append("⚠ 输出上限「\(knobs.maxTokens)」不是合法整数：本次**不设置** "
                       + "SLCN_LLM_MAX_TOKENS，引擎将用默认 2048。\(hint)\n")
        }
        // 第 70 条：两个高级旋钮填了结构上不可能的值时同样说明「本次不传」——
        // 它们空着就是「用引擎默认」，只有填错才需要出声。
        for parsed in [AIAdvancedKnobs.parseSearchTool(knobs.searchTool),
                       AIAdvancedKnobs.parseResponsesPath(knobs.responsesPath)] {
            if let h = AIAdvancedKnobs.hint(parsed) {
                out.append("⚠ " + h + "\n")
            }
        }
        return out
    }

    /// 找 convert.py。**实现已下沉到 `EngineLocator`**（第 95 条：好几页都要找引擎，
    /// 各自找一遍迟早变成两个真相）。这里保留同名转发，调用方与测试都不用改。
    static func locateTool() -> String? { EngineLocator.convertTool() }

    /// 找可用的 python3（转发到 `EngineLocator`，同上）。
    static func locatePython(toolDir: String) -> String? {
        EngineLocator.locatePython(toolDir: toolDir)
    }

    /// 拼命令行参数：**数组形式**，不做 shell 字符串插值。
    ///
    /// ⚠️ 刻意**不是** `private`：`tools/ai_settings_test` 要直接断言这几个开关到 argv 的
    /// 映射（`--verify-refs` / `--no-ai-merge` / 没勾 AI 时两个都不出现），
    /// 那是「界面承诺 = 引擎真收到的东西」的唯一机器证据。其余照旧由 `run()` 调用。
    static func arguments(_ o: Options) -> [String] {
        // 第 55 批：`--scan`（只扫描源稿）不是转换，两段式对它没有意义 ——
        // 勾着协同也一律按普通扫描走（「只扫描源稿」不受 coop 影响）。
        let coop: CoopFeature.Phase = o.scanOnly ? .off : o.coopPhase
        // 第二段是**最小集**：输出名/语种/模板/元数据由引擎从暂存状态继承，
        // GUI 这里不重复拼（重复拼反而容易与 ph1 的指纹对不上）。
        if coop == .second { return CoopFeature.secondPhaseArguments(o) }
        var a: [String] = []
        func add(_ flag: String, _ value: String) {
            guard !value.isEmpty else { return }
            a += [flag, value]
        }
        add("--source", o.source)
        if !o.scanOnly { add("--out", o.out) }
        add("--metadata", o.metadata)
        add("--bib", o.bib)
        add("--refs", o.refs)
        add("--author", o.author)
        add("--name", o.name)
        add("--lang", o.lang)
        add("--sty", o.sty)
        add("--vol", o.vol)
        add("--issue", o.issue)
        add("--year", o.pubYear)
        add("--pages", o.pages)
        add("--received", o.received)
        add("--editor", o.editor)
        add("--grants", o.grants)
        // 正文信息覆盖：空值由 add() 自动跳过 —— 与不传该参数完全等价。
        add("--title-cn", o.titleCn)
        add("--author-cn", o.authorCn)
        add("--affiliation-cn", o.affiliationCn)
        add("--email", o.email)
        add("--abstract-cn", o.abstractCn)
        add("--title-en", o.titleEn)
        add("--abstract-en", o.abstractEn)
        if o.compile && !o.scanOnly { a.append("--compile") }
        if o.clean { a.append("--clean") }
        // 《文献核实任务书》本来就该出：勾了「用 AI 处理参考文献」也一并打开 ——
        // 引擎侧 AI 预跑只在 --verify-refs 分支里进行，不传这个参数就没有 AI 通道。
        // 所以「未配置 AI 却勾了开关」的结果是：只出任务书（这正是界面文案承诺的行为）。
        // 界面上的联动（`toggled`）保证了勾 AI 时这个值一定是 true；这里再兜一次，
        // 是因为 argv 是**引擎真正收到的东西**，界面状态与它必须一致。
        //
        // 第 132 条「先算账」：引擎在 `--verify-plan` 分支里打印核实计划后**直接 exit 0**，
        // 根本走不到真正的核实。所以本次到底传哪个，由本函数这一处决定 ——
        // 两个都传只会让人以为「又要核实」，日志也说不清；**只传 --verify-plan**。
        // 第 55 批：两段式第一段的三个旗标由 `CoopFeature.firstPhaseFlags` 拼
        // （纯函数、headless 有断言）。**绝不**走 --verify-plan：引擎在那个分支里
        // 打印核实计划后直接退出 0，根本走不到「写任务书」这一步，第一段会白跑。
        // 也绝不传 --llm-* / --search-refs：第一段明确不调用 API（外部 chat 零上传/零费用）。
        if coop == .first {
            a += CoopFeature.firstPhaseFlags(o)
            // 第 59 批：全文 AI 核查（任务书附正文）。**默认不勾 → 一个参数都不加**
            // （默认 argv 与批 58 逐字节相同）；勾了才把范围原样交给引擎
            // `--coop-fulltext context|full`。只属于第一段：ph2 由
            // `CoopFeature.secondPhaseArguments` 拼最小集，天然不含该旗标；
            // 普通转换（coop == .off）也走不到这里 → 不传。
            if o.coopFulltext {
                a += ["--coop-fulltext", o.coopFulltextScope.flagValue]
            }
        } else if o.verifyRefs || o.useAI {
            if o.verifyPlan {
                a.append("--verify-plan")
            } else {
                a.append("--verify-refs")
                // 「保留原 reference.bib（不并入 AI 结果）」只在 AI 勾着时有意义：
                // 没勾 AI 就压根没有合并通道，加 --no-ai-merge 只是噪声（也会让日志说谎）。
                if o.useAI && o.keepOriginalBib { a.append("--no-ai-merge") }
            }
        }
        // 成本闸门与引用库开关：只在**真的执行核实**（任务书 / AI / 两段式第一段）时传 ——
        // 引擎对这些参数单独出现时只会打一行「本次忽略」的告警，界面不该发这种噪声。
        if coop == .first || o.verifyRefs || o.useAI {
            if let maxRefs = o.verifyMax { a += ["--verify-max", String(maxRefs)] }
            if o.verifyAllRefs { a.append("--verify-all-refs") }
            if o.noBibdb {
                a.append("--no-bibdb")
            } else if !o.bibdbPath.isEmpty {
                a += ["--bibdb-path", o.bibdbPath]
            }
        }
        // 第 55 批：协同模式**不传**任何直连 API 参数（两段式全程不调用 API；
        // `CoopFeature.prepare` 还会把 useAI 置 false，这里是第二道防线）。
        if o.useAI && coop == .off {
            // 只有勾了开关才把 AI 配置传下去（不勾就完全不碰这些参数）。
            // 地址/模型为空时 add() 会自动跳过 —— 引擎侧对空值会回落到环境变量
            // SLCN_LLM_BASE/KEY/MODEL，与命令行不传该参数完全等价。
            add("--llm-base", o.llmBase)
            add("--llm-model", o.llmModel)
            // ⚠️ **密钥不走命令行**：argv 在 `ps` 里对同机所有用户都可见。
            //    密钥改由 run() 用环境变量 SLCN_LLM_KEY 传给子进程；引擎
            //    `config_from_env` 在 `--llm-key` 缺省时会自动回落到该变量，
            //    所以 AI 行为与传参完全一致。
            //    `--llm-key` 这个参数本身**保留**：命令行手工调用仍然可用。
            //
            // 第 69 条的两个开关：**默认（chat / 不检索）时一个参数都不加** ——
            // 「不传 = 引擎取默认」这条纪律与 base/model 一致，也保证默认命令行与
            // 从前逐字节相同（既有 harness 的 e1/e2 断言钉着这件事）。
            // 非默认值也走 argv 而不是环境变量：`ps` 里能看见「这一次到底联网了没有」，
            // 对可审计性反倒是好事（密钥才是不能出现在 argv 里的那个）。
            if o.llmProtocol != "chat" { add("--llm-protocol", o.llmProtocol) }
            if o.llmWebSearch { a.append("--llm-web-search") }
            // P2（第 72 条起）：检索证据（DeepSeek 独立 Search API；默认关不加参数）。
            if o.searchRefs { a.append("--search-refs") }
            // 第 70 条的两个「未来兼容」旋钮：**留空或填了用不了的值都不传**（第一种是
            // 「用引擎默认」，第二种由 `knobWarnings` 说明），所以两栏都空时命令行与从前
            // **逐字节相同**。这里刻意用解析器（而不是 `add` 的单纯非空判断），
            // 好让「界面承诺 = 引擎真收到的东西」在 argv 这一层就成立 —— 非法值到了引擎
            // 只会被回落到默认并留一条 stderr，编辑在日志里反而不容易看见。
            // 为什么也走 argv 而不是环境变量：与 --llm-protocol / --llm-web-search 保持一致，
            // 而且 `ps` 里能看见「这一次到底用哪个工具名/路径」，对可审计性反倒是好事
            // （只有密钥那个不能出现在 argv 里）。
            if let t = AIAdvancedKnobs.envValue(AIAdvancedKnobs.parseSearchTool(o.llmSearchTool)) {
                add("--llm-search-tool", t)
            }
            if let p = AIAdvancedKnobs.envValue(AIAdvancedKnobs.parseResponsesPath(o.llmResponsesPath)) {
                add("--llm-responses-path", p)
            }
            // 第 55 批：直连 API 通道的 token 预算闸。**只在「值 ≠ 引擎默认」时传**：
            // 不传 = 引擎用 30000 / 1000000，命令行与从前逐字节相同；显式设 0 要传 "0"（不限）。
            // 外部 chat 两段式流程零费用，所以协同模式下这两个参数一个都不加。
            if let v = o.aiBudgetTokens, v != AIBudgetSettings.defaultArticle {
                a += ["--ai-budget-tokens", String(v)]
            }
            if let v = o.aiBatchBudgetTokens, v != AIBudgetSettings.defaultBatch {
                a += ["--ai-batch-budget-tokens", String(v)]
            }
        }
        if o.sourcePdf { a.append("--source-pdf") }
        if o.scanOnly { a.append("--scan") }
        if o.quoteThreshold != 105 {
            a += ["--quote-threshold", String(o.quoteThreshold)]
        }
        if o.force { a.append("--force") }
        return a
    }

    /// 第 132 条：把「本次核实的成本闸门与引用库设置」写进运行日志（纯展示）。
    ///
    /// 为什么必须打：这些值存在 UserDefaults 里、由界面现取拼 argv；编辑看日志要能回答
    /// 「这次核多少条、查不查库、库在哪」。尤其「先算账」开着时引擎只打印计划就退出 0，
    /// 不写一行说明的话，日志看起来就像「转换成功但什么都没做」。
    private static func logVerifyGate(_ o: Options, log: Logger,
                                      coop: CoopFeature.Phase = .off) {
        // 两段式第一段一定会出任务书（引擎隐含 --verify-refs），所以这些设置同样要打出来。
        guard coop == .first || o.verifyRefs || o.useAI else { return }
        let maxText: String
        if let maxRefs = o.verifyMax {
            maxText = maxRefs == 0 ? "不限（--verify-max 0）" : "\(maxRefs) 条（--verify-max）"
        } else {
            maxText = "引擎默认 120 条（本次不传 --verify-max）"
        }
        log("  核实   范围上限 \(maxText)\n")
        if o.verifyPlan {
            log("  核实   **只算账**（--verify-plan）：本次只打印核实范围与预计次数，"
                + "不生成任务书、不检索、不调用 AI；要真正核实请到「引用库设置」关掉「先算账」再跑。\n")
        }
        if o.verifyAllRefs {
            log("  核实   **允许全量核实**（--verify-all-refs）：越过上限闸门，"
                + "未被正文引用的条目也会核（会多花钱）。\n")
        }
        if o.noBibdb {
            log("  引用库 本次**不查库**（--no-bibdb）：输出与接入引用库之前一致。\n")
        } else if !o.bibdbPath.isEmpty {
            log("  引用库 核实前先查库（--bibdb-path \(o.bibdbPath)）\n")
        } else {
            log("  引用库 核实前先查库（自动：按 SLCN_NOTES 找笔记仓的 bib_db/bibliography.jsonl）\n")
        }
        // 第 59 批：全文 AI 核查（任务书附正文）——勾了才打这一行，不勾不刷屏；
        // 编辑看日志要能回答「这次正文会不会进任务书」，而不是去猜复选框状态。
        if coop == .first && o.coopFulltext {
            log("  全文核查 任务书附正文（--coop-fulltext \(o.coopFulltextScope.flagValue)）："
                + "正文/引用上下文只写进本机《文献核实任务书》，软件零上传、零费用；"
                + "由你自己复制给外部 chat 核实。\n")
        }
    }

    /// 把「用 AI 处理参考文献」这一项的实际效果写进日志。
    ///
    /// 两条硬要求：
    ///   1. **不许静默忽略**：勾了开关但没配置，必须有一行明确提示说明「本次只出任务书」；
    ///   2. **密钥不进日志**：只显示前 4 位（`AppSettings.mask`），完整密钥不打印。
    ///
    /// 两个「请求体旋钮」也写在这里：编辑要能一眼看出**这次到底发了什么**
    /// （思考模式与输出上限都会被引擎读成请求体字段，不看日志就无从核对）。
    private static func logAI(_ o: Options, knobs: AIEngineKnobs, log: Logger) {
        guard o.useAI else {
            if o.verifyRefs {
                log("  文献   生成《文献核实任务书》（未启用 AI 预跑）\n")
            }
            return
        }
        if o.aiReady {
            log("  文献   生成《文献核实任务书》+ AI 预跑\n")
            log("  AI     \(o.llmModel) @ \(o.llmBase)（密钥 \(AppSettings.mask(o.llmKey))）\n")
            log("  AI     \(knobs.summary)\n")
            // **本次核实通道**（第 69 条）：编辑看日志第一眼要能回答「这次到底联网了没有」。
            // 措辞刻意分成「意图」与「端点行为」两层：我们只能保证**发出了**检索工具，
            // 端点是否真的检索由引擎在跑完后按检索证据（web_search_call）如实报告——
            // 界面这边不许把「勾了开关」说成「已经联网核实」。
            if knobs.usesWebSearch {
                log("  文献   ⚠ **本次已开启联网检索**（通道 responses + web_search）："
                    + "引擎会带上服务端检索工具，并要求模型先查官方来源。\n")
                log("  文献   是否真的检索到由**端点与模型**决定（DeepSeek 的 deepseek-flash "
                    + "会静默忽略该工具、deepseek-v4-pro 才真检索）："
                    + "跑完后看日志里的「检索证据」一行——0 次即表示本次只能凭记忆。\n")
            } else if o.llmProtocol == "responses" {
                log("  文献   ⚠ 通道 responses，但**未启用联网检索**：模型只能凭记忆作答"
                    + "（任务书里同样写明）。\n")
            } else {
                log("  文献   ⚠ **未启用联网检索**（通道 chat）：模型只能凭记忆作答；"
                    + "卷/期/页码/出版地务必人工抽查。\n")
            }
            if o.keepOriginalBib {
                // 勾了「保留原 reference.bib」：这次只出任务书（= --no-ai-merge）。
                // 必须显式说一句 —— 否则编辑会以为 bib 也被改了。
                log("  文献   ⚠ 已勾「保留原 reference.bib（不并入 AI 结果）」："
                    + "本次**只出任务书**，AI 结果不并入产物（= --no-ai-merge）\n")
            } else {
                log("  文献   AI 结果会并入 reference.bib（回滚副本 reference.bib.ai前）；"
                    + "AI 调用失败 / 一条都没通过安全校验时，bib **一字节不改**\n")
            }
        } else {
            log("  文献   生成《文献核实任务书》\n")
            log("⚠ 已勾选「用 AI 处理参考文献」，但「设置」页还没配置 API 地址与模型名，\n")
            log("  本次**不会调用 AI**，只生成《文献核实任务书》（交给编辑自己的 AI 一样能用）；\n")
            log("  reference.bib **一字节不改**。\n")
            log("  要启用：打开「设置」标签页填写 API 地址（如 https://api.deepseek.com/v1）\n")
            log("  与模型名（如 deepseek-flash），可点「测试连接 / 体检」自检。\n")
        }
    }

    /// - Parameter knobs: 界面上的**当前**旋钮值（`ConvertTab` 在主线程现取）。
    ///   `nil` = 回落到读已保存的 `ai.json`，见 `effectiveKnobs(_:storeDirectory:)`。
    /// - Returns: 引擎退出码（0 = 成功；`Shell.cancelledStatus` = 用户取消；-1 = 启动失败）。
    ///   第 55 批起返回值给「两段式」状态机用（跑完第一段后要扫描暂存目录、刷新界面）；
    ///   普通转换的调用方照旧可以忽略返回值。
    @discardableResult
    static func run(options o: Options, knobs liveKnobs: AIEngineKnobs? = nil,
                    log: Logger) -> Int32 {
        let coop: CoopFeature.Phase = o.scanOnly ? .off : o.coopPhase
        guard !o.source.isEmpty else {
            log("请先选择源文件（Word 文稿或 LaTeX 文件）。\n")
            return -1
        }
        guard let tool = locateTool() else {
            log("未找到转换引擎 Engine/convert.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/convert.py"
                + "（官方 ./build.sh 会把 Engine 打包进去）；")
            log("② 从 app 所在目录逐级向上找 Engine/convert.py"
                + "（开发时把 app 放在仓库的  下即可命中）。\n")
            log("「设置」页底部「转换引擎」一行只读显示当前解析到的实际路径："
                + "若那里显示「未找到 Engine/convert.py」，说明两处都没命中，"
                + "请重新用 build.sh 构建，或把 Engine 目录放进 app 包的 Resources 内。\n")
            return -1
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = locatePython(toolDir: toolDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return -1
        }

        log("═" .padding(toLength: 66, withPad: "═", startingAt: 0) + "\n")
        // 第 55 批：标题按段写清楚 —— 第一段**不是**交付，日志里不许出现「产物目录」。
        switch coop {
        case .first: log("AI 协同初排·第一段（出任务书；不调用 API、不交付）\n")
        case .second: log("AI 协同初排·第二段（回填 → 续跑 → 交付）\n")
        case .off: log("初排转换\n")
        }
        log("  工具   \(tool)\n")
        log("  解释器 \(py)\n")
        log("  源文件 \(o.source)\n")
        if coop == .first {
            log("  输出到 **暂存目录**（第一段不写最终交付目录；跑完自动定位 `<out>/*_待核/`）\n")
        } else if !o.scanOnly {
            log("  输出到 \(o.out)\n")
        }
        if coop == .first {
            log("  模式   AI 协同初排·**第一段**：只写暂存目录 + 《文献核实任务书》，"
                + "**不调用任何 API**、不写最终交付目录（外部 chat 由编辑自己操作："
                + "软件零上传、零费用）。\n")
            if !o.coopStage.isEmpty { log("  暂存   \(o.coopStage)（--coop-stage 自定义）\n") }
        } else if coop == .second {
            log("  回填   \(o.coopRepliesPaths.count) 项（每个各传一个 --coop-replies）\n")
            for path in o.coopRepliesPaths { log("          \(path)\n") }
            log("  暂存   \(o.coopStage)\n")
            log("  应用   " + (o.coopApply
                ? "开（--coop-apply：先备份 reference.bib.ai前；应用 BIB 文献字段、META 元数据"
                  + "与 FN 脚注匹配建议（.tex 另留 <名>.tex.ai前），其余仍列待人工）\n"
                : "关：只写建议清单，**不改产物**（要应用请在跑第二段前勾选）\n"))
            log("  清理   " + (o.clean
                ? "开（--clean：编译后清理暂存目录的中间文件，再交付）\n"
                : "关（--clean 不传：交付目录会保留编译中间文件）\n"))
        }
        // 两个「请求体旋钮」在**启动子进程前**解析一次。
        // 优先用调用方现取的界面值（与 base/key/model 同一口径：改了没保存也生效）；
        // 没传（`nil`）才回落到读「设置」页保存过的 ai.json。见 `effectiveKnobs`。
        let knobs = effectiveKnobs(liveKnobs)
        if coop == .off {
            logAI(o, knobs: knobs, log: log)
        }
        logVerifyGate(o, log: log, coop: coop)
        if coop == .off {
            for w in knobWarnings(knobs) { log(w) }
        }
        log("═" .padding(toLength: 66, withPad: "═", startingAt: 0) + "\n\n")

        // 依赖 pypinyin（项目本地 vendor/），用 PYTHONPATH 挂上。
        //
        // ⚠️ **只传增量，不要传整个 environment**。Shell.run 是以
        // `Shell.environment()`（含 /Library/TeX/texbin、/opt/homebrew/bin）为底、
        // 再用这里传的值覆盖。若把 ProcessInfo 的整份环境传进去，GUI app 那个
        // 精简的 PATH（通常只有 /usr/bin:/bin:/usr/sbin:/sbin）会把 TeX 路径
        // **覆盖掉**，Python 侧 `shutil.which("xelatex")` 就找不到，报
        // 「找不到命令 xelatex」——实测就是这个问题。
        //
        // ⚠️ AI 密钥走**环境变量**，不进 argv（argv 在 `ps` 里对同机所有用户可见）。
        //    引擎 `slcn/llm.py` 的 `config_from_env(args.llm_base, args.llm_key, args.llm_model)`
        //    在 llm_key 为 None 时回落到 SLCN_LLM_KEY，所以换到这里后行为完全等价；
        //    密钥也不进日志（日志只显示前 4 位，见 logAI）。只在勾了开关时设置。
        //    两个旋钮（SLCN_LLM_THINKING / SLCN_LLM_MAX_TOKENS）由 `engineEnvironment`
        //    一并拼进来 —— 那个函数是纯函数，`tools/ai_settings_test` 直接验证它的取值。
        let extra = engineEnvironment(o, toolDir: toolDir, knobs: knobs)

        let status = Shell.run(
            py,
            [tool] + arguments(o),
            workingDirectory: toolDir,
            environment: extra,
            log: log
        )

        log("\n")
        // U3：用户取消 → 只说「已取消」，**不许**讲成失败（产物可能只写了一半）
        if status == Shell.cancelledStatus {
            log("⏹ 已取消（用户终止；本次产物可能不完整，需要时重新运行即可）。\n")
            return status
        }
        if status == 0 {
            // 第 55 批：两段式**第一段不是交付** —— 不许出现「产物目录」这种说法；
            // 第二段才说「已交付」，且交付目录由引擎日志/状态文件给出，不由界面瞎猜。
            switch coop {
            case .first:
                log("✅ 第一段完成：**待人工核实（未交付）**。\n")
                log("   暂存目录里有初排产物 + 《文献核实任务书》；最终交付目录本次**未写**。\n")
                log("   下一步：打开任务书交给外部 chat → 导入回复 → 运行第二段（才交付）。\n")
            case .second:
                log("✅ 第二段完成：**已交付**（最终目录与协同记录见上方引擎日志）。\n")
            case .off:
                log("✅ 完成。\n")
                if !o.scanOnly {
                    log("产物目录：\(o.out)\n")
                }
            }
        } else if status == -1 {
            log("❌ 启动失败。\n")
        } else {
            switch coop {
            case .first:
                log("❌ 第一段失败（退出码 \(status)）；最终交付目录本次**未写**。\n")
            case .second:
                log("❌ 第二段失败（退出码 \(status)）；最终交付目录保持原样（引擎保证不 promote）。\n")
            case .off:
                log("❌ 转换失败（退出码 \(status)）。\n")
            }
        }
        return status
    }
}
