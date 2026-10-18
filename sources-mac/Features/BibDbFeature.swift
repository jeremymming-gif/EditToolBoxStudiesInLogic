import Foundation

/// 「引用库」5 页（库概况 / 查库 / 入藏 / 待裁决 / 设置）的**引擎调用层**（第 132 条）。
///
/// 为什么不在这里重写任何判据
/// --------------------------
/// 引用库的全部判据（什么算已核实、冲突怎么留档、模糊查询阈值、哪些 bib 算成品期）
/// 都在引擎 `slcn/bibdb.py`，命令行薄壳是 `Engine/bibdb.py`。Swift 这边只做四件事：
///   ① 找引擎/解释器/库文件；② 拼 argv；③ 收 stdout 里的 `--json`；④ 翻译成界面模型。
/// 一旦在这里「顺手」判一次，GUI 与核实链路就会各有一套口径 —— 本项目最忌讳的两份真相。
///
/// 线程约定（与 `CheckBibFeature` / `TocFeature` 一致）
/// ------------------------------------------------
/// 本文件里所有会起子进程的函数（`overview` / `query` / `importBib` / ...）都是**同步**的，
/// **只能在后台队列调用**；视图负责 `DispatchQueue.global(...).async` + 回主线程。
/// 绝不在 SwiftUI body / 主线程里直接调它们（案例 126：主线程同步跑子进程会卡界面、
/// 拖拽落下时甚至重入动画事务闪退）。
enum BibDbFeature {

    // MARK: - 定位：引擎 / 解释器 / 引用库文件

    /// `Engine/bibdb.py`（app 包内或仓库里，由 `EngineLocator` 统一解析）。
    static func locateTool(convertTool: String? = EngineLocator.convertTool()) -> String? {
        EngineLocator.tool(named: "bibdb.py", convertTool: convertTool)
    }

    /// 私有笔记仓（自动探测，手动指定优先 —— `EngineLocator.notesRepo()` 既有语义）。
    static func notesRoot() -> String? { EngineLocator.notesRepo() }

    /// **只做自动探测**、忽略手动指定（「引用库设置」页要同时显示两者）。
    static func detectedNotesRoot() -> String? { EngineLocator.detectedNotesRepo() }

    /// 引用库文件相对笔记仓的固定位置（与 `slcn/bibdb.py: DB_DIRNAME/DB_FILENAME` 一致）。
    static let dbRelativePath = "bib_db/bibliography.jsonl"

    /// 库文件路径：`<笔记仓>/bib_db/bibliography.jsonl`。笔记仓找不到时返回 nil。
    static func dbPath(notesRoot root: String? = nil) -> String? {
        guard let notes = root ?? notesRoot(), !notes.isEmpty else { return nil }
        return PathKit.join(PathKit.join(notes, "bib_db"), "bibliography.jsonl")
    }

    /// 库文件**真的存在**时的路径（「查库」页要据此决定按钮可不可点）。
    static func existingDBPath() -> String? {
        guard let path = dbPath(), FileManager.default.fileExists(atPath: path) else { return nil }
        return path
    }

    /// 清单文件（「待裁决」页只读展示）的固定三个名字。
    static let reportFileNames = ["CONFLICTS.md", "DUPLICATES.md", "duplicates.csv"]

    // MARK: - 公共运行壳

    /// 一次 CLI 运行的结果（`json` 是 stdout 里解析出的 JSON 对象）。
    private struct Run {
        var json: [String: Any]?
        var status: Int32 = -1
        var output: String = ""
        /// 工具/解释器没找到、启动失败之类的**本地**错误（与引擎退出码无关）
        var error: String?
        /// 有 JSON 就算拿到结论（`lint` 的 `ok:false` 也是合法 JSON + 退出码 1）
        var ok: Bool { error == nil && json != nil }
    }

    /// 拼好 `python bibdb.py <arguments>` 跑一次（**同步；只许在后台队列调**）。
    private static func runJSON(_ arguments: [String]) -> Run {
        guard let tool = locateTool() else {
            return Run(error: "未找到引用库脚本 Engine/bibdb.py。\n"
                       + "（官方 ./build.sh 会把它打进 app 包的 Resources/Engine；"
                       + "开发时把 app 放在仓库里即可命中。）")
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let python = EngineLocator.locatePython(toolDir: toolDir) else {
            return Run(error: "未找到 python3（引擎跑在它上面）。请先安装：brew install python3。")
        }
        // ⚠️ 只传**增量**环境（PYTHONPATH=vendor），理由与 `ConvertFeature.run` 的长注释一致：
        //    把整份 ProcessInfo 环境传下去会覆盖 `Shell.environment()` 里补好的 PATH。
        guard let result = Shell.capture(python, [tool] + arguments,
                                         workingDirectory: toolDir,
                                         environment: EngineLocator.environment(toolDir: toolDir)) else {
            return Run(error: "没能启动 \(python)（解释器存在但执行失败？）")
        }
        return Run(json: jsonObject(in: result.output), status: result.status,
                   output: result.output, error: nil)
    }

    /// 从 CLI 输出里找**最后一段完整 JSON**。
    ///
    /// 为什么不能直接 `JSONSerialization` 整段：`Shell.capture` 把 stdout 与 stderr
    /// 并进同一个管道，而 CLI 的 JSON 是多行缩进格式（`indent=2`）——`import-*` 的
    /// 告警会先出现在 JSON 之前。所以按「行首 `{`」逐段尝试，取第一个能解析成功的；
    /// 顺序从后往前（JSON 是最后打印的，前面即使有 `{` 也不会误取到告警正文）。
    private static func jsonObject(in output: String) -> [String: Any]? {
        let lines = output.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        for index in lines.indices.reversed() {
            let head = lines[index].trimmingCharacters(in: .whitespaces)
            guard head.hasPrefix("{") else { continue }
            let candidate = lines[index...].joined(separator: "\n")
            if let data = candidate.data(using: .utf8),
               let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                return object
            }
        }
        return nil
    }

    /// 引擎失败时的可读原因：优先最后一行非空输出（CLI 的中文报错都在那里）。
    private static func failureText(_ run: Run) -> String {
        if let error = run.error { return error }
        let tail = run.output.split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .last(where: { !$0.isEmpty }) ?? "（引擎没有任何输出）"
        return "引擎返回退出码 \(run.status)：\(tail)"
    }

    // MARK: - 小工具（JSON → 值）

    private static func intValue(_ value: Any?) -> Int {
        if let n = value as? Int { return n }
        if let n = value as? NSNumber { return n.intValue }
        if let n = value as? Double { return Int(n) }
        if let s = value as? String, let n = Int(s) { return n }
        return 0
    }

    private static func stringValue(_ value: Any?) -> String {
        if let s = value as? String { return s }
        if let n = value as? NSNumber { return n.stringValue }
        return ""
    }

    private static func fields(_ record: [String: Any]) -> [String: String] {
        guard let raw = record["fields"] as? [String: Any] else { return [:] }
        var out: [String: String] = [:]
        for (name, value) in raw { out[name] = stringValue(value) }
        return out
    }

    /// 一条库记录的展示摘要（查库结果 / 入藏明细共用）。
    struct RecordInfo {
        var key = ""
        var entryType = ""
        var title = ""
        var author = ""
        var year = ""
        var verified = false
        var fields: [String: String] = [:]
        var issues: [String] = []          // 来源期（去重、保持文内顺序）
        var sourceSlugs: [String] = []     // 来源篇目录名
        var sourcesText = ""               // 「期 / 篇 / 文件」拼接（第一条来源）
    }

    private static func recordInfo(_ record: [String: Any]) -> RecordInfo {
        let raw = fields(record)
        var info = RecordInfo()
        info.key = stringValue(record["key"])
        info.entryType = stringValue(record["entry_type"])
        info.title = raw["title"] ?? raw["booktitle"] ?? "（无题名）"
        info.author = raw["author"] ?? raw["editor"] ?? "（无作者）"
        if let year = raw["year"], !year.isEmpty {
            info.year = year
        } else if let date = raw["date"], !date.isEmpty {
            info.year = String(date.prefix(4))
        }
        info.verified = (record["verified"] as? Bool) == true
        info.fields = raw
        var seenIssue = Set<String>()
        var seenSlug = Set<String>()
        for item in (record["sources"] as? [Any]) ?? [] {
            guard let source = item as? [String: Any] else { continue }
            let issue = stringValue(source["issue"])
            let slug = stringValue(source["slug"])
            let file = stringValue(source["file"])
            if !issue.isEmpty, !seenIssue.contains(issue) {
                seenIssue.insert(issue)
                info.issues.append(issue)
            }
            if !slug.isEmpty, !seenSlug.contains(slug) {
                seenSlug.insert(slug)
                info.sourceSlugs.append(slug)
            }
            if info.sourcesText.isEmpty {
                let parts = [issue, slug, file].filter { !$0.isEmpty }
                info.sourcesText = parts.joined(separator: " / ")
            }
        }
        return info
    }

    // MARK: - 库概况（stats + lint）

    /// stats 的界面模型（只取界面要显示的字段；没有的一律 0/空）
    struct Stats {
        var path = ""
        var records = 0
        var verified = 0
        var unverified = 0
        var issues = 0
        var files = 0
        var sources = 0
        var conflictsRecords = 0
        var conflictsTotal = 0
        var bytes = 0
        var sha256 = ""
        var updatedNewest = ""
        var issueList: [String] = []
    }

    /// lint 的界面模型（errors 是**必须修**的项；warnings 不阻断）
    struct Lint {
        var ok = true
        var errors: [String] = []
        var warnings: [String] = []
        var errorCount = 0
        var warningCount = 0
        var nearDuplicates = 0
    }

    /// 库概况页的一次加载结果（stats 与 lint 分开报错：
    /// 库有 error 时 stats 仍能读，lint 退出码 1 但 JSON 有效 —— 两个都要显示）。
    struct Overview {
        var notesPath: String?
        var dbPath = ""
        var dbExists = false
        var toolPath: String?
        var stats: Stats?
        var lint: Lint?
        var error: String?
        var seconds: Double = 0
    }

    /// 读库概况：`stats --json` + `lint --json`（**同步；后台队列调**）。
    static func overview() -> Overview {
        let startedAt = Date()
        var out = Overview()
        out.notesPath = notesRoot()
        out.toolPath = locateTool()
        out.seconds = 0
        guard let db = dbPath() else {
            out.error = "找不到私有笔记仓（引用库在 <笔记仓>/bib_db/bibliography.jsonl）。\n"
                + "请到「引用库设置」页手动指定笔记仓路径（或确认笔记仓与工具仓并排）。"
            return out
        }
        out.dbPath = db
        out.dbExists = FileManager.default.fileExists(atPath: db)

        let statsRun = runJSON(["stats", "--json", "--db", db])
        if statsRun.ok, let payload = statsRun.json {
            out.stats = parseStats(payload)
        } else {
            out.error = out.dbExists
                ? "读库概况失败：\(failureText(statsRun))"
                : "引用库文件还不存在：\(db)\n"
                    + "到「入藏」页导入一份 .bib 或一期目录，确认导入时会自动建库。"
        }

        // lint 单独跑：库有 error 时它退出码 1（**不是**程序错误），JSON 照样有效。
        if out.dbExists {
            let lintRun = runJSON(["lint", "--json", "--db", db])
            if lintRun.ok, let payload = lintRun.json {
                out.lint = parseLint(payload)
            } else if out.error == nil {
                out.error = "读库体检失败：\(failureText(lintRun))"
            }
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    private static func parseStats(_ payload: [String: Any]) -> Stats {
        var stats = Stats()
        stats.path = stringValue(payload["path"])
        stats.records = intValue(payload["records"])
        stats.verified = intValue(payload["verified"])
        stats.unverified = intValue(payload["unverified"])
        stats.issues = intValue(payload["issues"])
        stats.files = intValue(payload["files"])
        stats.sources = intValue(payload["sources"])
        stats.bytes = intValue(payload["bytes"])
        stats.sha256 = stringValue(payload["sha256"])
        stats.updatedNewest = stringValue(payload["updated_newest"])
        if let conflicts = payload["conflicts"] as? [String: Any] {
            stats.conflictsRecords = intValue(conflicts["records"])
            stats.conflictsTotal = intValue(conflicts["total"])
        }
        stats.issueList = (payload["issue_list"] as? [Any])?.map { stringValue($0) } ?? []
        return stats
    }

    private static func parseLint(_ payload: [String: Any]) -> Lint {
        var lint = Lint()
        lint.ok = (payload["ok"] as? Bool) ?? true
        lint.errors = (payload["errors"] as? [Any])?.map { stringValue($0) } ?? []
        lint.warnings = (payload["warnings"] as? [Any])?.map { stringValue($0) } ?? []
        if let counts = payload["counts"] as? [String: Any] {
            lint.errorCount = intValue(counts["errors"])
            lint.warningCount = intValue(counts["warnings"])
            lint.nearDuplicates = intValue(counts["near_duplicates"])
        } else {
            lint.errorCount = lint.errors.count
            lint.warningCount = lint.warnings.count
        }
        return lint
    }

    // MARK: - 查库（query）

    /// 查库模式：key 精确 / DOI 精确 / 题名模糊（与 `bibdb.py query` 的三个互斥参数一一对应）。
    enum QueryMode: String, CaseIterable, Identifiable {
        case key, doi, title
        var id: String { rawValue }
        var title: String {
            switch self {
            case .key: return "key"
            case .doi: return "DOI"
            case .title: return "题名"
            }
        }
        var flag: String {
            switch self {
            case .key: return "--key"
            case .doi: return "--doi"
            case .title: return "--title"
            }
        }
        var hint: String {
            switch self {
            case .key: return "如 Fine1975（也认 aliases）"
            case .doi: return "如 10.1007/s10992-020-09558-5（大小写/前缀不敏感）"
            case .title: return "题名模糊匹配（返回相似度与置信度）"
            }
        }
    }

    /// 一条查询命中
    struct QueryHit: Identifiable {
        let id = UUID()
        var record = RecordInfo()
        var hitKind = ""        // key / alias / doi
        var score: Double = 0
        var confidence = ""     // high / low（仅题名查询）
        var bibtex = ""
    }

    /// 一次查询结果
    struct QueryOutcome {
        var hits: [QueryHit] = []
        var kind: QueryMode = .key
        var note = ""           // 无命中/多义等一行说明
        var error: String?
        var seconds: Double = 0
    }

    /// 查库（**同步；后台队列调**）。`limit` 只对题名模糊查询有意义。
    static func query(mode: QueryMode, text rawText: String, limit: Int = 20) -> QueryOutcome {
        let startedAt = Date()
        var out = QueryOutcome(kind: mode)
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            out.error = "先填要查的内容（\(mode.title)）。"
            return out
        }
        guard let db = existingDBPath() else {
            out.error = "引用库文件不存在或找不到笔记仓。请先到「入藏」页导入，"
                + "或到「引用库设置」页指定笔记仓路径。"
            return out
        }
        var arguments = ["query", mode.flag, text, "--bibtex", "--json", "--db", db]
        if mode == .title {
            arguments += ["--limit", String(max(1, limit))]
        }
        let run = runJSON(arguments)
        guard run.ok, let payload = run.json else {
            out.error = failureText(run)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        var hits: [QueryHit] = []
        for item in (payload["hits"] as? [Any]) ?? [] {
            guard let dict = item as? [String: Any] else { continue }
            var hit = QueryHit()
            if let record = dict["record"] as? [String: Any] {
                hit.record = recordInfo(record)
            }
            hit.hitKind = stringValue(dict["hit"])
            hit.score = (dict["score"] as? Double) ?? 0
            hit.confidence = stringValue(dict["confidence"])
            hit.bibtex = stringValue(dict["bibtex"])
            hits.append(hit)
        }
        out.hits = hits
        if hits.isEmpty {
            out.note = "没有命中。"
        } else if (payload["ambiguous"] as? Bool) == true, mode == .key {
            out.note = "⚠ 有多条同 key/alias 的记录，需人工确认。"
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    // MARK: - 入藏（import-bib / import-dir，都先 dry-run 预览）

    /// 一次导入的计数摘要（= 引擎 `ImportReport.summary()` 的界面模型）
    struct ImportSummary {
        var entries = 0
        var new = 0
        var merged = 0
        var updated = 0
        var conflicts = 0
        var disambiguated = 0
        var sourcesAdded = 0
        var findings = 0
        var skipped: [(reason: String, count: Int)] = []
        var warnings: [String] = []

        var skippedTotal: Int { skipped.reduce(0) { $0 + $1.count } }

        /// 一行摘要（与 CLI 的 `_report_line` 同一信息量，措辞不必逐字相同）
        var line: String {
            var text = "条目 \(entries)（新增 \(new) / 合并或已存在 \(merged) / "
                + "更新 \(updated) / 冲突 \(conflicts) / 跳过 \(skippedTotal)）"
            if disambiguated > 0 { text += "，key 撞车加后缀 \(disambiguated)" }
            return text
        }
    }

    /// 一条「将变化」的明细（CLI `changes` 数组）
    struct ImportChange: Identifiable {
        var key = ""
        var status = ""         // new / merged / updated / conflict
        var title = ""
        var author = ""
        var year = ""
        var verified = false
        var sourcesAdded = 0
        var conflictsAdded = 0
        var conflictFields: [String] = []

        var id: String { key }

        var statusTitle: String {
            switch status {
            case "new": return "新增"
            case "merged": return "合并来源"
            case "updated": return "更新"
            case "conflict": return "记冲突"
            default: return status
            }
        }
    }

    /// 导入结果（预览与真导入共用；`dryRun=true` 时引擎一个字节都没写）
    struct ImportOutcome {
        var dryRun = false
        var written = false
        var dbPath = ""
        var dbExisted = false
        var summary: ImportSummary?
        var changes: [ImportChange] = []
        var issues: [ImportIssueRow] = []
        var warnings: [String] = []
        var error: String?
        var seconds: Double = 0
        var rawOutput = ""
    }

    /// import-dir 的逐期明细（预览时给编辑看「哪几期、每期几份 bib」）
    struct ImportIssueRow: Identifiable {
        var issue = ""
        var finished = false
        var fileCount = 0
        var slugs: [String] = []
        var summary: ImportSummary?

        var id: String { issue }
    }

    private static func parseSummary(_ payload: [String: Any]?) -> ImportSummary? {
        guard let payload else { return nil }
        var summary = ImportSummary()
        summary.entries = intValue(payload["entries"])
        summary.new = intValue(payload["new"])
        summary.merged = intValue(payload["merged"])
        summary.updated = intValue(payload["updated"])
        summary.conflicts = intValue(payload["conflicts"])
        summary.disambiguated = intValue(payload["disambiguated"])
        summary.sourcesAdded = intValue(payload["sources_added"])
        summary.findings = intValue(payload["findings"])
        if let skipped = payload["skipped"] as? [String: Any] {
            summary.skipped = skipped
                .map { (reason: $0.key, count: intValue($0.value)) }
                .sorted { $0.reason < $1.reason }
        }
        summary.warnings = (payload["warnings"] as? [Any])?.map { stringValue($0) } ?? []
        return summary
    }

    private static func parseChanges(_ payload: [String: Any]?) -> [ImportChange] {
        guard let items = payload?["changes"] as? [Any] else { return [] }
        var rows: [ImportChange] = []
        for item in items {
            guard let dict = item as? [String: Any] else { continue }
            var row = ImportChange()
            row.key = stringValue(dict["key"])
            row.status = stringValue(dict["status"])
            row.title = stringValue(dict["title"])
            row.author = stringValue(dict["author"])
            row.year = stringValue(dict["year"])
            row.verified = (dict["verified"] as? Bool) == true
            row.sourcesAdded = intValue(dict["sources_added"])
            row.conflictsAdded = intValue(dict["conflicts_added"])
            row.conflictFields = (dict["conflict_fields"] as? [Any])?.map { stringValue($0) } ?? []
            rows.append(row)
        }
        return rows
    }

    private static func parseIssueRows(_ payload: [String: Any]?) -> [ImportIssueRow] {
        guard let items = payload?["result"] as? [String: Any],
              let issues = items["issues"] as? [Any] else { return [] }
        var rows: [ImportIssueRow] = []
        for item in issues {
            guard let dict = item as? [String: Any] else { continue }
            var row = ImportIssueRow()
            row.issue = stringValue(dict["issue"])
            row.finished = (dict["finished"] as? Bool) == true
            if let files = dict["files"] as? [Any] {
                row.fileCount = files.count
                row.slugs = files.compactMap { ($0 as? [String: Any]).map { stringValue($0["slug"]) } }
            }
            row.summary = parseSummary(dict["report"] as? [String: Any])
            rows.append(row)
        }
        return rows
    }

    /// 预览 / 导入**单篇** `.bib`（**同步；后台队列调**）。
    ///
    /// - Parameters:
    ///   - issue/slug: 来源期与篇目录名（界面自动从路径推断、可改）。给了期/篇就是
    ///     「往期成品 bib」口径，引擎会按 `published-bib` 入 `verified=true`。
    ///   - dryRun: true = 只算不写（界面「预览」按钮）。
    static func importBib(bibPath rawPath: String, issue: String, slug: String,
                          dryRun: Bool) -> ImportOutcome {
        let startedAt = Date()
        var out = ImportOutcome(dryRun: dryRun)
        let bib = PathKit.normalize(rawPath)
        guard !bib.isEmpty, FileManager.default.fileExists(atPath: bib),
              !PathKit.isDirectory(bib) else {
            out.error = "读不到要导入的 .bib：\(bib)（文件不存在，或这是个文件夹）"
            return out
        }
        guard let db = dbPath() else {
            out.error = "找不到私有笔记仓，无法确定引用库位置。请到「引用库设置」页指定笔记仓路径。"
            return out
        }
        var arguments = ["import-bib", "--bib", bib, "--verified", "--json", "--db", db]
        let issueName = issue.trimmingCharacters(in: .whitespacesAndNewlines)
        let slugName = slug.trimmingCharacters(in: .whitespacesAndNewlines)
        if !issueName.isEmpty { arguments += ["--issue", issueName] }
        if !slugName.isEmpty { arguments += ["--slug", slugName] }
        if dryRun { arguments.append("--dry-run") }
        let run = runJSON(arguments)
        guard run.ok, let payload = run.json else {
            out.error = failureText(run)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        out.dbPath = stringValue(payload["db"])
        out.written = (payload["written"] as? Bool) == true
        out.dbExisted = (payload["db_existed"] as? Bool) == true
        out.summary = parseSummary(payload["report"] as? [String: Any])
        out.changes = parseChanges(payload)
        out.rawOutput = run.output
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 预览 / 导入**整期目录**（**同步；后台队列调**）。
    ///
    /// `root` 可以给「一期目录」（如 `…/2026年第2期(87)`，只导入这一期）或
    /// 「稿件库根」（扫全部期）。判据在引擎 CLI 层（与种子导入同源）。
    static func importDirectory(root rawRoot: String, dryRun: Bool) -> ImportOutcome {
        let startedAt = Date()
        var out = ImportOutcome(dryRun: dryRun)
        let root = PathKit.normalize(rawRoot)
        guard !root.isEmpty, PathKit.isDirectory(root) else {
            out.error = "读不到目录：\(root)（不存在或不是文件夹）"
            return out
        }
        guard let db = dbPath() else {
            out.error = "找不到私有笔记仓，无法确定引用库位置。请到「引用库设置」页指定笔记仓路径。"
            return out
        }
        var arguments = ["import-dir", "--root", root, "--json", "--db", db]
        if dryRun { arguments.append("--dry-run") }
        let run = runJSON(arguments)
        guard run.ok, let payload = run.json else {
            out.error = failureText(run)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        out.dbPath = stringValue(payload["db"])
        out.written = (payload["written"] as? Bool) == true
        out.dbExisted = (payload["db_existed"] as? Bool) == true
        if let result = payload["result"] as? [String: Any] {
            out.summary = parseSummary(result["total"] as? [String: Any])
            out.warnings = (result["warnings"] as? [Any])?.map { stringValue($0) } ?? []
        }
        out.changes = parseChanges(payload)
        out.issues = parseIssueRows(payload)
        out.rawOutput = run.output
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    // MARK: - 待裁决清单（report；只写清单，不改库）

    /// 一个清单文件的状态（存在/大小/修改时间）
    struct ReportFile: Identifiable {
        var path = ""
        var name = ""
        var exists = false
        var bytes = 0
        var modified: Date?

        var id: String { name }
    }

    /// 重新生成清单的结果
    struct ReportOutcome {
        var dbPath = ""
        var reports: [ReportFile] = []
        var conflictsRecords = 0
        var conflictsTotal = 0
        var pairs = 0
        var written = 0
        var error: String?
        var seconds: Double = 0
        var rawOutput = ""
    }

    /// 清单文件当前状态（**只读**；不跑引擎）。
    static func reportFiles() -> [ReportFile] {
        guard let notes = notesRoot() else { return [] }
        let directory = PathKit.join(notes, "bib_db")
        let fm = FileManager.default
        return reportFileNames.map { name in
            var file = ReportFile(path: PathKit.join(directory, name), name: name)
            var isDir: ObjCBool = false
            if fm.fileExists(atPath: file.path, isDirectory: &isDir), !isDir.boolValue {
                file.exists = true
                let attrs = try? fm.attributesOfItem(atPath: file.path)
                file.bytes = (attrs?[.size] as? NSNumber)?.intValue ?? 0
                file.modified = attrs?[.modificationDate] as? Date
            }
            return file
        }
    }

    /// 重新生成全部待裁决清单：`bibdb.py report --out <笔记仓>/bib_db --json`。
    /// **只写 CONFLICTS.md / DUPLICATES.md / duplicates.csv，不改库本体**（引擎侧保证幂等）。
    static func generateReports() -> ReportOutcome {
        let startedAt = Date()
        var out = ReportOutcome()
        guard let notes = notesRoot() else {
            out.error = "找不到私有笔记仓，无法确定清单输出目录。请到「引用库设置」页指定笔记仓路径。"
            return out
        }
        let db = PathKit.join(PathKit.join(notes, "bib_db"), "bibliography.jsonl")
        out.dbPath = db
        guard FileManager.default.fileExists(atPath: db) else {
            out.error = "引用库文件还不存在：\(db)\n先到「入藏」页导入一份 .bib，再来生成清单。"
            return out
        }
        let outDir = PathKit.join(notes, "bib_db")
        let run = runJSON(["report", "--json", "--db", db, "--out", outDir])
        guard run.ok, let payload = run.json else {
            out.error = failureText(run)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        if let conflicts = payload["conflicts"] as? [String: Any] {
            out.conflictsRecords = intValue(conflicts["records"])
            out.conflictsTotal = intValue(conflicts["total"])
        }
        out.pairs = intValue(payload["pairs"])
        for item in (payload["reports"] as? [Any]) ?? [] {
            guard let dict = item as? [String: Any] else { continue }
            let path = stringValue(dict["path"])
            var file = ReportFile(path: path, name: (path as NSString).lastPathComponent)
            let fm = FileManager.default
            if fm.fileExists(atPath: path) {
                file.exists = true
                let attrs = try? fm.attributesOfItem(atPath: path)
                file.bytes = (attrs?[.size] as? NSNumber)?.intValue ?? intValue(dict["bytes"])
                file.modified = attrs?[.modificationDate] as? Date
            }
            if (dict["written"] as? Bool) == true { out.written += 1 }
            out.reports.append(file)
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        out.rawOutput = run.output
        return out
    }

    // MARK: - 从路径推断来源期 / 篇目录名

    /// 从 bib 路径推断「来源期」与「篇目录名」：
    /// `…/2026年第2期(87)/02 Kun_Xing/reference.bib` → (`2026年第2期(87)`, `02 Kun_Xing`)。
    ///
    /// 判据与引擎 `slcn/paths.py: ISSUE_RE`、`slcn/bibdb.py: _AUTHOR_DIR_RE` **同一口径**
    /// （期 = `20xx年第…`；篇 = 两位数字 + 空格/下划线/短横）；认不出就返回空串 ——
    /// **绝不猜一个**（猜错的来源会比空来源更糟：库里的来源是审计凭据）。
    static func inferIssueAndSlug(from rawPath: String) -> (issue: String, slug: String) {
        guard !rawPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return ("", "")
        }
        let components = (PathKit.normalize(rawPath) as NSString).pathComponents
        for (index, component) in components.enumerated() {
            guard component.range(of: #"^\d{4}年第"#, options: .regularExpression) != nil else {
                continue
            }
            var slug = ""
            if index + 1 < components.count,
               components[index + 1].range(of: #"^\d{2}[\s_-]"#,
                                            options: .regularExpression) != nil {
                slug = components[index + 1]
            }
            return (component, slug)
        }
        return ("", "")
    }
}
