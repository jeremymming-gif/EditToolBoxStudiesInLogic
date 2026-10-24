import Foundation

/// B-190：引用库「入藏」段（`import-bib` / `import-dir` / 粘贴 bib）——
/// 从 `BibDbFeature.swift` **纯搬迁**（BRIEF §2 单文件 ≤3000 行闸；逻辑逐行等价）。
///
/// 共享 helper（`runJSON` / `Run` / `failureText` / `intValue` / `stringValue`）
/// 在 `BibDbFeature.swift` 里改为 internal，扩展里直接复用；本文件不新增判据。
extension BibDbFeature {

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

    // MARK: - 粘贴 bib 文本（入藏页新来源模式；引擎零改动）
    //
    // 做法：把粘贴文本写进**临时文件**（引擎 import-bib 只吃文件路径，本批不做 stdin 参数），
    // 调现有 `import-bib --dry-run --json` 预览 → 确认后去掉 --dry-run 真写。
    // 临时文件用后即删；运行日志只记计数/键，不回显 bib 正文（隐私）。

    /// 粘贴入库的「入库口径」（用户拍板 P2：默认未核实）。
    enum PasteImportMode: String, CaseIterable, Identifiable {
        case unverified
        case verified
        case published

        var id: String { rawValue }
        var title: String {
            switch self {
            case .unverified: return "未核实（默认）"
            case .verified: return "已人工核对（按已核实入库）"
            case .published: return "成品 bib（期+篇）"
            }
        }
        var hint: String {
            switch self {
            case .unverified: return "手工输入/检索结果：记成未核实来源，AI/网络结果不得默认标已核实"
            case .verified: return "人工已逐条核对过：按已核实入库（引擎 --verified）"
            case .published: return "往期已出版成品 bib：填来源期+篇，按 published-bib 入已核实"
            }
        }
    }

    /// 预览 / 入藏**粘贴的 bib 文本**（1..N 条）：写临时文件 → `import-bib ... --json`。
    ///
    /// - Parameters:
    ///   - text: 用户粘贴的 bib 正文（只在内存与临时文件里，**不写日志**）。
    ///   - issue/slug: `published` 模式必填期（篇可选）。
    ///   - mode: 入库口径（默认未核实）。
    ///   - dryRun: true = 预览（不写库）。
    /// **同步；只许后台队列调**。
    static func importPastedBib(text rawText: String, issue: String, slug: String,
                                mode: PasteImportMode, dryRun: Bool) -> ImportOutcome {
        let startedAt = Date()
        var out = ImportOutcome(dryRun: dryRun)
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            out.error = "先粘贴至少 1 条 bib 正文，再预览/入藏。"
            return out
        }
        guard let db = dbPath() else {
            out.error = "找不到私有笔记仓，无法确定引用库位置。请到「引用库设置」页指定笔记仓路径。"
            return out
        }
        let issueName = issue.trimmingCharacters(in: .whitespacesAndNewlines)
        let slugName = slug.trimmingCharacters(in: .whitespacesAndNewlines)
        if mode == .published, issueName.isEmpty {
            out.error = "入库口径选了「成品 bib（期+篇）」：先填来源期（例 2026年第2期(87)）。"
            return out
        }
        // 临时文件：文件名固定 `pasted-bib.bib`（幂等重复粘贴时不新增来源），
        // 每次用独立临时目录避免撞名；整个目录用后即删。
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("SLCN-pasted-\(UUID().uuidString)")
        let file = dir.appendingPathComponent("pasted-bib.bib")
        defer { try? FileManager.default.removeItem(at: dir) }
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try text.write(to: file, atomically: true, encoding: .utf8)
        } catch {
            out.error = "写粘贴内容的临时文件失败：\(error.localizedDescription)"
            return out
        }
        var arguments = ["import-bib", "--bib", file.path, "--json", "--db", db]
        switch mode {
        case .unverified:
            arguments.append("--unverified")      // 显式未核实（给了期/篇也不会被升成 verified）
        case .verified:
            arguments.append("--verified")
        case .published:
            arguments.append("--verified")        // 与「单篇 .bib」现状同一口径（成品 bib）
            if !issueName.isEmpty { arguments += ["--issue", issueName] }
            if !slugName.isEmpty { arguments += ["--slug", slugName] }
        }
        if dryRun { arguments.append("--dry-run") }
        let run = runJSON(arguments)
        guard let payload = run.json else {
            out.error = failureText(run)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        if !stringValue(payload["error"]).isEmpty {
            out.error = stringValue(payload["error"])
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        if run.status != 0 {
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

    // MARK: - 外部 bib → 规范 bib（B-209；只读，判据全在引擎 `normalize-bib`）
    //
    // 外壳只做「拼参数 → 显示」：文件/粘贴 → 引擎 `normalize-bib --json` → 逐条 diff。
    // **不写库**（不需要 `dbPath()`），也不在这里判断「什么算规范」——那是引擎的事。

    /// 一处字段级差异；`old`/`new` 为 nil = 该字段新增/删除。
    struct NormalizeFieldChange: Identifiable, Equatable {
        var name = ""
        var old: String?
        var new: String?

        var id: String {
            name + "\u{1}" + (old ?? "\u{0}") + "\u{1}" + (new ?? "\u{0}")
        }

        /// 界面/预览里的「name: old → new」；新增/删除在值前标「（新增）」「（删除）」。
        var line: String {
            if let old, let new { return "\(name): \(old) → \(new)" }
            if let new { return "\(name): （新增）\(new)" }
            if let old { return "\(name): （删除）\(old)" }
            return "\(name): （无变化值）"
        }
    }

    /// 一条外部 bib 条目的规范化结论（引擎 `entries[]` 一项）。
    struct NormalizeEntry: Identifiable, Equatable {
        var key = ""
        var entryType = ""
        var status = ""          // changed / unchanged / kept
        var changedFields: [NormalizeFieldChange] = []
        var notes: [String] = []
        var risks: [String] = []
        var missingRequired: [String] = []

        var id: String { key }

        var statusTitle: String {
            switch status {
            case "changed": return "变化"
            case "unchanged": return "未变"
            case "kept": return "原样保留"
            default: return status
            }
        }

        /// `缺必填：a、b`（无缺失时为空串，界面据此决定显示与否）。
        var missingRequiredLine: String {
            missingRequired.isEmpty ? "" : "缺必填：" + missingRequired.joined(separator: "、")
        }
    }

    /// `normalize-bib --json` 的界面模型（一行摘要 + 逐条 diff + 规范全文）。
    struct NormalizeResult {
        var entries: [NormalizeEntry] = []
        var entriesIn = 0
        var entriesOut = 0
        var changedEntries = 0
        var unchangedEntries = 0
        var keptEntries = 0
        /// 规范 bib 全文（复制/送入入藏预览都**原样**用它，不 trim、不加头尾）。
        var normalizedText = ""

        /// `条目 N（变化 X / 未变 Y / 原样保留 Z）`
        var summaryLine: String {
            "条目 \(entriesIn)（变化 \(changedEntries) / 未变 \(unchangedEntries)"
                + " / 原样保留 \(keptEntries)）"
        }
    }

    /// 一次规范化的结果壳（同步函数用；错误文案来自 `failureText`）。
    struct NormalizeOutcome {
        var result: NormalizeResult?
        var error: String?
        var seconds: Double = 0
        var rawOutput = ""
    }

    /// 引擎 argv（`normalize-bib` 只读，**没有** `--db`）。
    static func normalizeBibArguments(bibPath: String) -> [String] {
        ["normalize-bib", "--bib", bibPath, "--json"]
    }

    /// 规范化一份外部 `.bib`（**同步；只许后台队列调**）。只读，不需要笔记仓。
    static func normalizeExternalBib(bibPath rawPath: String) -> NormalizeOutcome {
        let startedAt = Date()
        var out = NormalizeOutcome()
        let bib = PathKit.normalize(rawPath)
        guard !bib.isEmpty, FileManager.default.fileExists(atPath: bib),
              !PathKit.isDirectory(bib) else {
            out.error = "读不到要规范化的 .bib：\(bib)（文件不存在，或这是个文件夹）"
            return out
        }
        let run = runJSON(normalizeBibArguments(bibPath: bib))
        guard let payload = run.json else {
            out.error = failureText(run)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        if !stringValue(payload["error"]).isEmpty {
            out.error = stringValue(payload["error"])
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        guard run.status == 0 else {
            out.error = failureText(run)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        out.result = parseNormalizePayload(payload)
        out.rawOutput = run.output
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 规范化**粘贴**的 bib 文本（**同步；只许后台队列调**）：
    /// 写临时文件 → 走同一个 `normalize-bib --bib … --json`，`defer` 用后即删。
    /// 运行日志只允许 `normalizeLogLines` 的计数/key 行——正文不落日志。
    static func normalizeExternalBib(text rawText: String) -> NormalizeOutcome {
        let startedAt = Date()
        var out = NormalizeOutcome()
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            out.error = "先粘贴至少 1 条 bib 正文，再规范化预览。"
            return out
        }
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("SLCN-normalize-\(UUID().uuidString)")
        let file = dir.appendingPathComponent("pasted-bib.bib")
        defer { try? FileManager.default.removeItem(at: dir) }
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try text.write(to: file, atomically: true, encoding: .utf8)
        } catch {
            out.error = "写粘贴内容的临时文件失败：\(error.localizedDescription)"
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        var fileOutcome = normalizeExternalBib(bibPath: file.path)
        fileOutcome.seconds = Date().timeIntervalSince(startedAt)
        return fileOutcome
    }

    /// JSON → 界面模型（**纯函数**，供测试；payload 为 nil/缺字段时给空模型，不崩）。
    static func parseNormalizePayload(_ payload: [String: Any]?) -> NormalizeResult {
        var result = NormalizeResult()
        guard let payload else { return result }
        result.entriesIn = intValue(payload["entries_in"])
        result.entriesOut = intValue(payload["entries_out"])
        result.changedEntries = intValue(payload["changed_entries"])
        result.unchangedEntries = intValue(payload["unchanged_entries"])
        result.keptEntries = intValue(payload["kept_entries"])
        result.normalizedText = stringValue(payload["normalized_text"])
        guard let items = payload["entries"] as? [Any] else { return result }
        for item in items {
            guard let dict = item as? [String: Any] else { continue }
            var entry = NormalizeEntry()
            entry.key = stringValue(dict["key"])
            entry.entryType = stringValue(dict["entry_type"])
            entry.status = stringValue(dict["status"])
            if let fields = dict["changed_fields"] as? [Any] {
                entry.changedFields = fields.compactMap { raw in
                    guard let field = raw as? [String: Any] else { return nil }
                    return NormalizeFieldChange(
                        name: stringValue(field["name"]),
                        old: optionalStringValue(field["old"]),
                        new: optionalStringValue(field["new"]))
                }
            }
            entry.notes = stringArray(dict["notes"])
            entry.risks = stringArray(dict["risks"])
            entry.missingRequired = stringArray(dict["missing_required"])
            result.entries.append(entry)
        }
        return result
    }

    /// 运行日志行（**隐私**：只允许计数/条目 key，绝不带题名/字段值/正文）。
    static func normalizeLogLines(_ result: NormalizeResult) -> [String] {
        var lines = ["外部 bib 规范化：" + result.summaryLine]
        for entry in result.entries {
            lines.append("  · \(entry.key)（\(entry.statusTitle)）")
        }
        return lines
    }

    /// JSON 里的 old/new：缺键或 `null` → nil；其它走 `stringValue`。
    private static func optionalStringValue(_ value: Any?) -> String? {
        guard let value, !(value is NSNull) else { return nil }
        return stringValue(value)
    }

    private static func stringArray(_ value: Any?) -> [String] {
        (value as? [Any])?.map { stringValue($0) } ?? []
    }
}
