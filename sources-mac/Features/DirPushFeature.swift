import Foundation

/// 「目录摘要推送」（B-225）—— 调引擎 `Engine/dirpush.py` 生成 docx 推送文案。
///
/// **判据全在引擎**：期头/专栏/目录条目/摘要清洗、年度扫描/缺期/体例还原、
/// 输出路径与同名覆盖都在 `Engine/slcn/pushcopy/` + `Engine/dirpush.py`。
/// 这一层只做三件事（与「生成目录」页 `TocFeature` 同一套分工）：
///   ① 把界面状态拼成命令行参数 —— **纯函数**，`tools/dirpush_test` 直接断言；
///   ② 解析引擎 `--json` 的**冻结契约字段**（GUI 只读这些，不自己算）；
///   ③ 用 `Shell` 跑引擎（预览 `--json --dry-run`、生成 `--json`）。
///
/// Foundation-only（不引 SwiftUI）：测试台可以 headless 编译生产源码本身。
enum DirPushFeature {

    /// 引擎入口文件名（`EngineLocator` 在引擎目录里找它）。
    static let engineFileName = "dirpush.py"

    /// 默认输出根（spec §2.5；`~` 由 `PathKit.normalize` 展开）。
    static let defaultOutRoot = "~/Documents/sysu/编辑部/目录摘要推送"

    /// 记住「上次输出根」的 UserDefaults 键（契约 §2.3；**不要改**，改了老用户的记忆就丢）。
    static let outRootDefaultsKey = "dirpush.outRoot"

    /// 默认输出根展开后的绝对路径。
    static var defaultOutRootPath: String { PathKit.normalize(defaultOutRoot) }

    /// 记忆的输出根：存过且非空 → 用存的；否则默认根。
    static func rememberedOutRoot(defaults: UserDefaults = .standard) -> String {
        if let saved = defaults.string(forKey: outRootDefaultsKey) {
            let trimmed = saved.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed }
        }
        return defaultOutRootPath
    }

    /// 生成成功后记住输出根（契约 §2.3：**每次生成成功后**写入）。
    static func rememberOutRoot(_ raw: String, defaults: UserDefaults = .standard) {
        let path = PathKit.normalize(raw)
        guard !path.isEmpty else { return }
        defaults.set(path, forKey: outRootDefaultsKey)
    }

    // MARK: - 仓库内输出根启发式（契约 §2.3）

    /// 输出根是否落在代码仓（`<home>/Documents/GitHub/`）下面。
    ///
    /// 判据只有这一条（契约冻结）：路径规范化后以 `<home>/Documents/GitHub/` 开头 →
    /// 生成物会变成未跟踪文件，界面在生成前先弹确认框；是否阻断由用户点。
    /// `home` 可显式传（测试台用它做 hermetic 断言，不依赖本机真实主目录）。
    static func isRepoOutputRoot(_ outRoot: String,
                                 home: String = FileManager.default.homeDirectoryForCurrentUser.path) -> Bool {
        let normalized = PathKit.normalize(outRoot)
        let base = (home as NSString).appendingPathComponent("Documents/GitHub")
        return normalized == base || normalized.hasPrefix(base + "/")
    }

    /// 生成前确认框的文案；不在仓库内 → nil（不用弹）。
    static func repoOutputRootWarning(for outRoot: String,
                                      home: String = FileManager.default.homeDirectoryForCurrentUser.path) -> String? {
        guard isRepoOutputRoot(outRoot, home: home) else { return nil }
        return "输出根在代码仓内：\n\(PathKit.normalize(outRoot))\n\n"
            + "生成会在仓库里留下未跟踪的 docx 文件（污染工作树）。仍要继续？"
    }

    // MARK: - 参数拼装（纯函数；契约 §3）

    /// 勾选期号规范化：只留正数，升序去重（`[3,1,2,1]` → `[1,2,3]`）。
    static func normalizedIssueList(_ numbers: [Int]) -> [Int] {
        Array(Set(numbers.filter { $0 > 0 })).sorted()
    }

    /// `--include-issues` 的值：`"1,2,3"`；空 → nil（**不传** = 引擎按全部 found 处理）。
    static func includeIssuesArgument(_ numbers: [Int]) -> String? {
        let list = normalizedIssueList(numbers)
        guard !list.isEmpty else { return nil }
        return list.map(String.init).joined(separator: ",")
    }

    /// 引擎 argv（冻结契约 §3）。调用方再按用途追加 `--json` / `--dry-run`。
    ///
    /// - 基础：`["--issue", issue, "--out", outRoot]`
    /// - 当期：`withEnToc` → 末尾 `["--with-en-toc"]`
    /// - 年度：`["--annual"]`；`year` 非空 → `["--year", year]`；
    ///   `includeIssues` 非空 → `["--include-issues", "1,2,3"]`（升序去重）
    ///
    /// 年度模式不传 `--with-en-toc`（引擎契约：它只作用于当期模式；年度体例按期还原）。
    static func arguments(issue: String,
                          outRoot: String,
                          withEnToc: Bool = false,
                          annual: Bool = false,
                          year: String = "",
                          includeIssues: [Int] = []) -> [String] {
        var args = ["--issue", issue, "--out", outRoot]
        if annual {
            args.append("--annual")
            let y = year.trimmingCharacters(in: .whitespacesAndNewlines)
            if !y.isEmpty { args += ["--year", y] }
            if let list = includeIssuesArgument(includeIssues) {
                args += ["--include-issues", list]
            }
        } else if withEnToc {
            args.append("--with-en-toc")
        }
        return args
    }

    /// 预览 argv：基础 + `["--json", "--dry-run"]`（dry-run 不写盘）。
    static func previewArguments(issue: String,
                                 outRoot: String,
                                 withEnToc: Bool = false,
                                 annual: Bool = false,
                                 year: String = "",
                                 includeIssues: [Int] = []) -> [String] {
        arguments(issue: issue, outRoot: outRoot, withEnToc: withEnToc,
                  annual: annual, year: year, includeIssues: includeIssues) + ["--json", "--dry-run"]
    }

    /// 生成 argv：基础 + `["--json"]`（stdout 恰好一个 JSON 对象，日志走 stderr）。
    static func generateArguments(issue: String,
                                  outRoot: String,
                                  withEnToc: Bool = false,
                                  annual: Bool = false,
                                  year: String = "",
                                  includeIssues: [Int] = []) -> [String] {
        arguments(issue: issue, outRoot: outRoot, withEnToc: withEnToc,
                  annual: annual, year: year, includeIssues: includeIssues) + ["--json"]
    }

    // MARK: - 引擎定位（照 TocFeature）

    /// 找 `Engine/dirpush.py`（用户指定 → app 包内 → 往上找仓库）。
    static func locateTool(convertTool: String? = EngineLocator.convertTool()) -> String? {
        EngineLocator.tool(named: engineFileName, convertTool: convertTool)
    }

    // MARK: - JSON 契约模型（契约 §1：GUI 只读这些字段）

    /// 引擎 `--json` 输出（**容错解析**：缺字段/类型不对按默认值处理，不整份丢弃；
    /// 整份不是 JSON → nil，界面明确报错，绝不静默当成空预览）。
    struct DirPushPreview: Decodable, Equatable {

        struct Column: Decodable, Equatable {
            var label: String
            var name: String
            var host: String
            enum CodingKeys: String, CodingKey { case label, name, host }
            init(from decoder: Decoder) throws {
                let c = try decoder.container(keyedBy: CodingKeys.self)
                label = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? ""
                name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
                host = (try? c.decodeIfPresent(String.self, forKey: .host)) ?? ""
            }
        }

        struct Issue: Decodable, Equatable {
            var year: Int?
            var number: Int?
            var vol: Int?
            var total: Int?
            var headerLines: [String]
            var withEnToc: Bool?
            var column: Column?
            enum CodingKeys: String, CodingKey {
                case year, number, vol, total
                case headerLines = "header_lines"
                case withEnToc = "with_en_toc"
                case column
            }
            init(from decoder: Decoder) throws {
                let c = try decoder.container(keyedBy: CodingKeys.self)
                year = try? c.decodeIfPresent(Int.self, forKey: .year)
                number = try? c.decodeIfPresent(Int.self, forKey: .number)
                vol = try? c.decodeIfPresent(Int.self, forKey: .vol)
                total = try? c.decodeIfPresent(Int.self, forKey: .total)
                headerLines = (try? c.decodeIfPresent([String].self, forKey: .headerLines)) ?? []
                withEnToc = try? c.decodeIfPresent(Bool.self, forKey: .withEnToc)
                column = try? c.decodeIfPresent(Column.self, forKey: .column)
            }
        }

        struct Counts: Decodable, Equatable {
            var zhEntries: Int?
            var enEntries: Int?
            var articles: Int?
            var abstractsOk: Int?
            var abstractsMissing: Int?
            enum CodingKeys: String, CodingKey {
                case articles
                case zhEntries = "zh_entries"
                case enEntries = "en_entries"
                case abstractsOk = "abstracts_ok"
                case abstractsMissing = "abstracts_missing"
            }
            init(from decoder: Decoder) throws {
                let c = try decoder.container(keyedBy: CodingKeys.self)
                zhEntries = try? c.decodeIfPresent(Int.self, forKey: .zhEntries)
                enEntries = try? c.decodeIfPresent(Int.self, forKey: .enEntries)
                articles = try? c.decodeIfPresent(Int.self, forKey: .articles)
                abstractsOk = try? c.decodeIfPresent(Int.self, forKey: .abstractsOk)
                abstractsMissing = try? c.decodeIfPresent(Int.self, forKey: .abstractsMissing)
            }
        }

        struct AnnualIssue: Decodable, Equatable {
            var number: Int?
            var withEnToc: Bool?
            var status: String?
            enum CodingKeys: String, CodingKey {
                case number, status
                case withEnToc = "with_en_toc"
            }
            init(from decoder: Decoder) throws {
                let c = try decoder.container(keyedBy: CodingKeys.self)
                number = try? c.decodeIfPresent(Int.self, forKey: .number)
                withEnToc = try? c.decodeIfPresent(Bool.self, forKey: .withEnToc)
                status = try? c.decodeIfPresent(String.self, forKey: .status)
            }
        }

        struct Annual: Decodable, Equatable {
            var year: Int?
            var included: [Int]
            var excluded: [Int]
            var missingIssues: [Int]
            var issues: [AnnualIssue]
            enum CodingKeys: String, CodingKey {
                case year, included, excluded, issues
                case missingIssues = "missing_issues"
            }
            init(from decoder: Decoder) throws {
                let c = try decoder.container(keyedBy: CodingKeys.self)
                year = try? c.decodeIfPresent(Int.self, forKey: .year)
                included = (try? c.decodeIfPresent([Int].self, forKey: .included)) ?? []
                excluded = (try? c.decodeIfPresent([Int].self, forKey: .excluded)) ?? []
                missingIssues = (try? c.decodeIfPresent([Int].self, forKey: .missingIssues)) ?? []
                issues = (try? c.decodeIfPresent([AnnualIssue].self, forKey: .issues)) ?? []
            }
        }

        var schema: Int?
        var ok: Bool?
        var mode: String?
        var dryRun: Bool?
        var outputs: [String]
        var issue: Issue?
        var counts: Counts?
        var warnings: [String]
        var missing: [String]
        var annual: Annual?

        enum CodingKeys: String, CodingKey {
            case schema, ok, mode, outputs, issue, counts, warnings, missing, annual
            case dryRun = "dry_run"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            schema = try? c.decodeIfPresent(Int.self, forKey: .schema)
            ok = try? c.decodeIfPresent(Bool.self, forKey: .ok)
            mode = try? c.decodeIfPresent(String.self, forKey: .mode)
            dryRun = try? c.decodeIfPresent(Bool.self, forKey: .dryRun)
            outputs = (try? c.decodeIfPresent([String].self, forKey: .outputs)) ?? []
            issue = try? c.decodeIfPresent(Issue.self, forKey: .issue)
            counts = try? c.decodeIfPresent(Counts.self, forKey: .counts)
            warnings = (try? c.decodeIfPresent([String].self, forKey: .warnings)) ?? []
            missing = (try? c.decodeIfPresent([String].self, forKey: .missing)) ?? []
            annual = try? c.decodeIfPresent(Annual.self, forKey: .annual)
        }

        // MARK: 只读便捷访问（界面用；判据仍是引擎字段）

        var isOK: Bool { ok ?? false }
        var headerLines: [String] { issue?.headerLines ?? [] }
        var withEnTocDefault: Bool { issue?.withEnToc ?? false }
        var articlesCount: Int { counts?.articles ?? 0 }
        var zhEntriesCount: Int { counts?.zhEntries ?? 0 }
        var enEntriesCount: Int { counts?.enEntries ?? 0 }
        var abstractsOKCount: Int { counts?.abstractsOk ?? 0 }
        var issueYear: Int? { issue?.year }
        var issueNumber: Int? { issue?.number }
    }

    /// 解析一段文本里的**最后一个** JSON 对象（stdout 一个对象；stderr 日志也在同一根管道里，
    /// 所以从后往前找 —— 引擎契约保证 stdout 只有这一个对象）。
    static func decodeJSON(in text: String) -> DirPushPreview? {
        let decoder = JSONDecoder()
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty, let data = trimmed.data(using: .utf8),
           let preview = try? decoder.decode(DirPushPreview.self, from: data) {
            return preview
        }
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
        for line in lines.reversed() {
            let s = line.trimmingCharacters(in: .whitespaces)
            guard s.hasPrefix("{"), s.hasSuffix("}") else { continue }
            if let data = s.data(using: .utf8),
               let preview = try? decoder.decode(DirPushPreview.self, from: data) {
                return preview
            }
        }
        // 多行 JSON 兜底：从后往前找 "{"，取到最后一个 "}" 为止再解
        let chars = Array(text)
        guard !chars.isEmpty else { return nil }
        var start = chars.count - 1
        while start >= 0 {
            if chars[start] == "{" {
                let candidate = String(chars[start...])
                if let data = candidate.data(using: .utf8),
                   let preview = try? decoder.decode(DirPushPreview.self, from: data) {
                    return preview
                }
                if let end = candidate.lastIndex(of: "}") {
                    let cut = String(candidate[...end])
                    if let data = cut.data(using: .utf8),
                       let preview = try? decoder.decode(DirPushPreview.self, from: data) {
                        return preview
                    }
                }
            }
            start -= 1
        }
        return nil
    }

    static func parse(_ json: String) -> DirPushPreview? { decodeJSON(in: json) }

    /// 引擎失败时从合并输出里取一条**给编辑看**的原因（stderr 的中文错误行优先）。
    /// 找不到明确的错误行时退回最后一条非空日志，再不行给退出码。
    static func errorMessage(from output: String, status: Int32) -> String {
        let lines = output
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { line in
                !line.isEmpty && !line.hasPrefix("{") && !line.hasPrefix("[")
            }
        let markers = ["错误", "ERROR", "Error:", "error:", "Traceback", "usage:",
                       "无法", "缺少", "不存在", "不是"]
        for line in lines.reversed() where markers.contains(where: { line.contains($0) }) {
            return line
        }
        if let last = lines.last { return last }
        return "引擎退出码 \(status)"
    }

    // MARK: - 界面派生状态（纯函数）

    /// 摘要取到率：`abstracts_ok/articles`，显示 `x/y`。0 篇不除零 → `"0/0"`。
    static func abstractsRate(_ preview: DirPushPreview?) -> String {
        let total = preview?.counts?.articles ?? 0
        let ok = preview?.counts?.abstractsOk ?? 0
        return "\(ok)/\(total)"
    }

    /// 年份自动推导：预览 `issue.year`（契约 §2.4：自动 = 预览年份，可改）；拿不到 → `""`。
    static func autoYear(from preview: DirPushPreview?) -> String {
        guard let year = preview?.issueYear else { return "" }
        return String(year)
    }

    /// 年度清单的一行（界面勾选状态；`missing_issues` 只警告、不在这里可选）。
    struct AnnualRow: Identifiable, Equatable {
        let number: Int
        let withEnToc: Bool
        let status: String
        var selected: Bool

        var id: Int { number }
        /// 体例显示（契约 §2.4：每项显示「第 N 期」+ 体例「中英文对照/中文」）
        var styleText: String { withEnToc ? "中英文对照" : "中文" }
        /// 引擎 `issues[].status` 是否「找到、可用」（"ok"；空字符串按可用容错）。
        var isFound: Bool { status.isEmpty || status == "ok" }
    }

    /// 年度纳入清单：默认**全选 found**；引擎 `included` 给了就按它（它才是判据）。
    static func annualRows(from preview: DirPushPreview?) -> [AnnualRow] {
        guard let annual = preview?.annual else { return [] }
        let included = Set(annual.included)
        return annual.issues.compactMap { item in
            guard let number = item.number else { return nil }
            let withEnToc = item.withEnToc ?? false
            let status = item.status ?? ""
            let found = status.isEmpty || status == "ok"
            let selected = found && (included.isEmpty ? true : included.contains(number))
            return AnnualRow(number: number, withEnToc: withEnToc, status: status, selected: selected)
        }
    }

    /// 生成时传给 `--include-issues` 的勾选期号（勾掉的期不传；空 = 全部 found）。
    static func selectedIssues(from rows: [AnnualRow]) -> [Int] {
        normalizedIssueList(rows.filter { $0.selected }.map { $0.number })
    }

    /// 缺期警告文案（`missing_issues` 只警告、不可选）；没有缺期 → nil。
    static func missingIssuesText(from preview: DirPushPreview?) -> String? {
        let missing = preview?.annual?.missingIssues ?? []
        guard !missing.isEmpty else { return nil }
        return "缺期："
            + missing.sorted().map { "第 \($0) 期" }.joined(separator: "、")
            + "（只警告，不可勾选；生成时会跳过）"
    }

    /// 年度清单的标题（`2025 年共 N 期，将纳入 M 期`）。
    static func annualSummaryText(from preview: DirPushPreview?, selected: Int) -> String {
        guard let annual = preview?.annual else { return "年度清单为空（先预览）" }
        let year = annual.year.map(String.init) ?? ""
        return "\(year) 年共 \(annual.issues.count) 期，将纳入 \(selected) 期"
    }

    // MARK: - 调引擎：预览（--json --dry-run，不写盘）

    struct PreviewOutcome {
        let status: Int32
        let preview: DirPushPreview?
        let message: String
        var succeeded: Bool { status == 0 && preview != nil }
    }

    static func preview(issue: String,
                        outRoot: String,
                        withEnToc: Bool = false,
                        annual: Bool = false,
                        year: String = "",
                        includeIssues: [Int] = [],
                        log: Logger? = nil) -> PreviewOutcome {
        guard let tool = locateTool() else {
            let msg = "未找到引擎 Engine/dirpush.py"
                + "（app 包内 Contents/Resources/Engine/ 或从 app 往上找仓库）。"
            log?(msg + "\n")
            return PreviewOutcome(status: -1, preview: nil, message: msg)
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            let msg = "未找到 python3。请先安装（brew install python3）。"
            log?(msg + "\n")
            return PreviewOutcome(status: -1, preview: nil, message: msg)
        }
        let args = [tool] + previewArguments(issue: PathKit.normalize(issue),
                                             outRoot: PathKit.normalize(outRoot),
                                             withEnToc: withEnToc,
                                             annual: annual,
                                             year: year,
                                             includeIssues: includeIssues)
        guard let result = Shell.capture(py,
                                        args,
                                        workingDirectory: toolDir,
                                        environment: EngineLocator.environment(toolDir: toolDir)) else {
            let msg = "启动引擎失败（python：\(py)）。"
            log?(msg + "\n")
            return PreviewOutcome(status: -1, preview: nil, message: msg)
        }
        let preview = decodeJSON(in: result.output)
        if result.status == 0, let preview {
            return PreviewOutcome(status: result.status, preview: preview, message: "")
        }
        let msg: String
        if result.status == 0 {
            msg = "引擎已运行，但没能解析 --json 输出（契约不一致，见日志）。"
        } else {
            msg = errorMessage(from: result.output, status: result.status)
        }
        log?("预览失败（退出码 \(result.status)）：\(msg)\n")
        return PreviewOutcome(status: result.status, preview: preview, message: msg)
    }

    // MARK: - 调引擎：生成（--json）

    struct RunResult {
        let status: Int32
        let preview: DirPushPreview?
        let outputs: [String]
        let message: String

        var succeeded: Bool { status == 0 && preview?.isOK == true && !outputs.isEmpty }
    }

    /// 生成 docx。结果路径**只认引擎 `outputs[]`**（界面不自己拼路径）。
    ///
    /// `store` 用来建一个带 `onLine` 旁路的 `Logger`：既把引擎日志实时写进界面日志，
    /// 又攒住原始输出，结束后从里面抠出那个 JSON 对象（stdout/stderr 同一根管道）。
    @discardableResult
    static func run(issue: String,
                    outRoot: String,
                    withEnToc: Bool = false,
                    annual: Bool = false,
                    year: String = "",
                    includeIssues: [Int] = [],
                    cancel: RunCancel? = nil,
                    store: LogStore) -> RunResult {
        guard let tool = locateTool() else {
            let msg = "未找到引擎 Engine/dirpush.py（先构建 app，或用仓库内的 Engine）。"
            Logger(store: store)("❌ \(msg)\n")
            return RunResult(status: -1, preview: nil, outputs: [], message: msg)
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            let msg = "未找到 python3。请先安装（brew install python3）。"
            Logger(store: store)("❌ \(msg)\n")
            return RunResult(status: -1, preview: nil, outputs: [], message: msg)
        }

        let normalizedIssue = PathKit.normalize(issue)
        let normalizedOut = PathKit.normalize(outRoot)
        let args = [tool] + generateArguments(issue: normalizedIssue,
                                              outRoot: normalizedOut,
                                              withEnToc: withEnToc,
                                              annual: annual,
                                              year: year,
                                              includeIssues: includeIssues)

        var raw = ""
        let logger = Logger(store: store, startedAt: Date(), cancel: cancel) { chunk in
            raw += chunk
        }
        logger("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n")
        logger("目录摘要推送\n")
        logger("  工具   \(tool)\n")
        logger("  解释器 \(py)\n")
        logger("  期目录 \(normalizedIssue)\n")
        logger("  输出根 \(normalizedOut)\n")
        logger("  模式   \(annual ? "当期 + 年度汇总" : "当期")"
               + (annual ? "（\(year.isEmpty ? "按预览年份" : "\(year) 年")，勾选 \(includeIssues.count) 期）" : "") + "\n")
        logger("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n\n")

        let status = Shell.run(py,
                               args,
                               workingDirectory: toolDir,
                               environment: EngineLocator.environment(toolDir: toolDir),
                               log: logger)
        logger("\n")
        if status == Shell.cancelledStatus {
            logger("⏹ 已取消（已写入的 docx 可能不完整，需要时重新生成）。\n")
            return RunResult(status: status, preview: nil, outputs: [], message: "已取消")
        }

        let preview = decodeJSON(in: raw)
        if status == 0 {
            let outputs = preview?.outputs ?? []
            if outputs.isEmpty {
                logger("⚠️ 引擎成功退出，但 --json 结果里没有 outputs 路径（见上方引擎日志）。\n")
            } else {
                logger("✅ 完成，生成 \(outputs.count) 份 docx：\n")
                for path in outputs { logger("   \(path)\n") }
            }
            return RunResult(status: status, preview: preview, outputs: outputs, message: "")
        }
        let msg = errorMessage(from: raw, status: status)
        logger("❌ 生成失败（退出码 \(status)）：\(msg)\n")
        return RunResult(status: status, preview: preview, outputs: [], message: msg)
    }
}
