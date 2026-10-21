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
        /// B-143：结构化 warnings（GUI 按类下钻）；旧引擎没有 `warning_items` → 空，
        /// 界面回退到 `warnings` 字符串（`LintWarningDigest` 负责分类）。
        var warningItems: [LintWarningItem] = []
        /// B-143：字段冲突拆分（`counts.conflicts_pending` / `conflicts_resolved`）。
        /// 老键 `counts.conflicts` 仍是**全量**（引擎向后兼容），界面不再拿它当待裁决数。
        var conflictsPendingRecords = 0
        var conflictsPendingTotal = 0
        var conflictsResolvedRecords = 0
        var conflictsResolvedTotal = 0
        /// 是否读到了 B-143 新引擎的冲突拆分（旧引擎 → false，界面退回老文案）
        var hasConflictSplit = false
    }

    // MARK: - lint warnings 下钻（B-143）

    /// lint warnings 的**结构化镜像**（引擎 `warning_items`：一条 = `warnings` 里一句）。
    /// `keys_sample` 只有 ≤5 个例子（不把「缺 title 219 条」全塞进 JSON）；完整清单跑
    /// `bibdb.py lint` 看。字段冲突项另带 pending/resolved 拆分，供「待裁决 N / 历史已裁决 M」。
    struct LintWarningItem: Identifiable {
        var code = ""
        var registry = ""
        var detail = ""
        var action = ""
        var count = 0
        var keysSample: [String] = []
        // 仅 registry == "conflicts" 有意义
        var pendingRecords = 0
        var pendingTotal = 0
        var resolvedRecords = 0
        var resolvedTotal = 0
        /// 引擎同 code 可能有多条（如 8 条「内容完全相同的多条记录」），必须带 detail 才唯一
        var id: String { "\(code)|\(detail)" }
    }

    /// warning 分类的**固定顺序**（spec B-143 §2.3：重复记录 / 同一作品多记录 / 字段冲突 /
    /// 缺字段 / entry_type / alias / 未核实 / 其他）与中文标题；GUI 分类展示与计数都从这里来。
    enum LintWarningRegistry {
        static let order = ["duplicates", "near_duplicates", "conflicts", "missing_field",
                            "unknown_entry_type", "alias", "unverified", "other"]
        static func title(_ registry: String) -> String {
            switch registry {
            case "duplicates": return "重复记录（内容完全相同）"
            case "near_duplicates": return "同一作品的多条记录"
            case "conflicts": return "字段冲突"
            case "missing_field": return "缺字段"
            case "unknown_entry_type": return "entry_type 笔误"
            case "alias": return "alias 问题"
            case "unverified": return "未核实条目"
            default: return "其他"
            }
        }
        /// 分类级补充说明（尤其重复类要写清「P6 另卡、本页只报告、不合并」）
        static func note(_ registry: String) -> String? {
            switch registry {
            case "duplicates":
                return "重复记录合并属 P6 另卡，本页只报告、不合并"
            case "near_duplicates":
                return "经典文献跨期重复引用是常态；如需合并走 P6 另卡，本页只报告"
            case "conflicts":
                return "字段冲突裁决的逐条明细在「待裁决」页；本页橙字来自库健康检查 lint（两个数据源）"
            case "missing_field":
                return "回原稿/来源 bib 核对后补齐（本页只读，不改库）"
            case "unknown_entry_type":
                return "多为笔误（rticle / artical / inprocedings …），按 bibcheck 类型表改正"
            case "alias":
                return "alias 让旧 key 也能查库；撞车会让查询标 ambiguous"
            case "unverified":
                return "不阻断使用，但查询结果会标 verified=false"
            default:
                return nil
            }
        }
    }

    /// 下钻 sheet 里的一类 warning（按固定顺序排出）
    struct LintWarningCategory: Identifiable {
        var registry = ""
        var title = ""
        var items: [LintWarningItem] = []
        var id: String { registry }
        /// 这一类里「提示条数」（不是受影响记录数——缺 title 是一条 warning 管 219 条记录）
        var warningCount: Int { items.count }
        /// 受影响记录数（各条 count 之和；仅用于粗看规模）
        var affectedCount: Int { items.reduce(0) { $0 + max(0, $1.count) } }
        var note: String? { LintWarningRegistry.note(registry) }
    }

    /// warning 分类/计数的纯逻辑（headless 可测；旧引擎回退老 warnings 字符串）。
    struct LintWarningDigest {
        /// 新引擎用 `warning_items`；旧引擎（或没有结构化数据时）把老字符串按前缀归类。
        static func categories(items: [LintWarningItem],
                               legacyWarnings: [String] = []) -> [LintWarningCategory] {
            var source = items
            if source.isEmpty && !legacyWarnings.isEmpty {
                source = fallbackItems(legacyWarnings)
            }
            guard !source.isEmpty else { return [] }
            let grouped = Dictionary(grouping: source) { $0.registry }
            let known = Set(LintWarningRegistry.order)
            var result: [LintWarningCategory] = []
            for registry in LintWarningRegistry.order {
                guard let group = grouped[registry], !group.isEmpty else { continue }
                var category = LintWarningCategory()
                category.registry = registry
                category.title = LintWarningRegistry.title(registry)
                category.items = group
                result.append(category)
            }
            // 固定分类之外的新 registry（未来引擎新增）归「其他」，不丢
            let unknown = source.filter { !known.contains($0.registry) }
            if !unknown.isEmpty {
                var category = LintWarningCategory()
                category.registry = "other"
                category.title = LintWarningRegistry.title("other")
                category.items = unknown
                if let index = result.firstIndex(where: { $0.registry == "other" }) {
                    result[index].items.append(contentsOf: unknown)
                } else {
                    result.append(category)
                }
            }
            return result
        }

        /// 旧引擎回退：把 warnings 字符串按前缀/关键词归类（无法解析的数字按 1 计）。
        static func fallbackItems(_ warnings: [String]) -> [LintWarningItem] {
            warnings.map { text in
                var item = LintWarningItem()
                item.detail = text
                item.code = fallbackCode(text)
                item.registry = fallbackRegistry(text)
                item.action = fallbackAction(item.registry)
                item.count = fallbackCount(text, code: item.code)
                return item
            }
        }

        static func fallbackRegistry(_ text: String) -> String {
            if text.hasPrefix("内容完全相同的多条记录") { return "duplicates" }
            if text.hasPrefix("同一作品的多条记录") { return "near_duplicates" }
            if text.contains("字段冲突") { return "conflicts" }
            if text.hasPrefix("缺 title") || text.hasPrefix("缺 author")
                || text.hasPrefix("缺可用年份") { return "missing_field" }
            if text.contains("entry_type") { return "unknown_entry_type" }
            if text.contains("alias") { return "alias" }
            if text.hasPrefix("未核实条目") { return "unverified" }
            return "other"
        }

        static func fallbackCode(_ text: String) -> String {
            switch fallbackRegistry(text) {
            case "duplicates": return "duplicate_content"
            case "near_duplicates": return "near_duplicates"
            case "conflicts": return "conflict_pending"
            case "missing_field":
                if text.hasPrefix("缺 title") { return "missing_title" }
                if text.hasPrefix("缺 author") { return "missing_author" }
                return "missing_year"
            case "unknown_entry_type": return "unknown_entry_type"
            case "alias": return "alias_ambiguous"
            case "unverified": return "unverified"
            default: return "other"
            }
        }

        static func fallbackAction(_ registry: String) -> String {
            switch registry {
            case "duplicates":
                return "查库确认哪一条为准；重复记录合并属 P6 另卡，本页只报告、不自动合并"
            case "near_duplicates":
                return "经典文献跨期重复引用是常态；如需合并走 P6 另卡（本页只报告、不合并）"
            case "conflicts":
                return "到「待裁决」页逐条裁决（保留旧值 / 采用新值 / 待人工）"
            case "missing_field":
                return "回原稿/来源 bib 核对后补齐（本页只读，不改库）"
            case "unknown_entry_type":
                return "按 bibcheck 的类型表改正 entry_type（多为笔误）"
            case "alias":
                return "查库确认后让 alias 只指向一条记录"
            case "unverified":
                return "按入库口径核实后重新入库（AI 结果不直接入库）"
            default:
                return "跑 `bibdb.py lint` 看完整清单"
            }
        }

        static func fallbackCount(_ text: String, code: String) -> Int {
            if code == "duplicate_content" {
                guard let colon = text.range(of: "：") else { return 1 }
                let names = text[colon.upperBound...].split(separator: "、")
                return max(1, names.count)
            }
            var digits = ""
            for character in text {
                if character.isNumber {
                    digits.append(character)
                } else if !digits.isEmpty {
                    break
                }
            }
            return Int(digits) ?? 1
        }

        /// 字段冲突项的「待裁决 / 历史已裁决」一句话（GUI 明细用）
        static func conflictsSummary(_ item: LintWarningItem) -> String {
            "待裁决 \(item.pendingRecords) 条记录 / \(item.pendingTotal) 处差异 ｜ "
                + "历史已裁决 \(item.resolvedRecords) 条记录 / \(item.resolvedTotal) 处差异"
        }
    }

    /// 从「库概况」warning 明细跳到「待裁决」页的跳转动作。
    /// `showResolved` = 跳过去后待裁决页的「显示已裁决」开关态（两页共用一份状态）。
    struct ReviewJump: Identifiable, Equatable {
        var id = ""
        var title = ""
        var showResolved = false
    }

    /// 字段冲突分类下面给两个跳转：看待裁决（开关关）/ 看历史已裁决（开关开）。
    static func reviewJumps(pending: Int, resolved: Int) -> [ReviewJump] {
        [ReviewJump(id: "pending", title: "到待裁决页看（待裁决 \(pending) 条）",
                    showResolved: false),
         ReviewJump(id: "resolved", title: "查看历史已裁决（\(resolved) 条）",
                    showResolved: true)]
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

    /// `lint --json` → 界面模型（B-143 新增键都是**只加不改**：老引擎也能解析）。
    /// 注意：`counts.conflicts` 是引擎的**全量**老口径（向后兼容），待裁决/已裁决拆分只认
    /// `counts.conflicts_pending` / `conflicts_resolved`（没有就 `hasConflictSplit=false`）。
    static func parseLint(_ payload: [String: Any]) -> Lint {
        var lint = Lint()
        lint.ok = (payload["ok"] as? Bool) ?? true
        lint.errors = (payload["errors"] as? [Any])?.map { stringValue($0) } ?? []
        lint.warnings = (payload["warnings"] as? [Any])?.map { stringValue($0) } ?? []
        lint.warningItems = ((payload["warning_items"] as? [Any]) ?? []).compactMap { raw in
            guard let dict = raw as? [String: Any] else { return nil }
            var item = LintWarningItem()
            item.code = stringValue(dict["code"])
            item.registry = stringValue(dict["registry"])
            item.detail = stringValue(dict["detail"])
            item.action = stringValue(dict["action"])
            item.count = intValue(dict["count"])
            item.keysSample = (dict["keys_sample"] as? [Any])?.map { stringValue($0) } ?? []
            item.pendingRecords = intValue(dict["pending_records"])
            item.pendingTotal = intValue(dict["pending_total"])
            item.resolvedRecords = intValue(dict["resolved_records"])
            item.resolvedTotal = intValue(dict["resolved_total"])
            return item
        }
        if let counts = payload["counts"] as? [String: Any] {
            lint.errorCount = intValue(counts["errors"])
            lint.warningCount = intValue(counts["warnings"])
            lint.nearDuplicates = intValue(counts["near_duplicates"])
            if let pending = counts["conflicts_pending"] as? [String: Any] {
                lint.conflictsPendingRecords = intValue(pending["records"])
                lint.conflictsPendingTotal = intValue(pending["total"])
                lint.hasConflictSplit = true
            }
            if let resolved = counts["conflicts_resolved"] as? [String: Any] {
                lint.conflictsResolvedRecords = intValue(resolved["records"])
                lint.conflictsResolvedTotal = intValue(resolved["total"])
                lint.hasConflictSplit = true
            }
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

    // MARK: - 待裁决：冲突条目（conflicts --json；批73 B-127）
    //
    // 判据全部在引擎：这一层只把 `conflicts --json` / `resolve --json` 的字段翻成界面模型，
    // **不在 Swift 里判断「哪条该保留/哪条算冲突」**（否则两份真相）。
    // 列表顺序直接沿用引擎的「期→篇→条目」顺序；C 编号（stable_no）由引擎按全部冲突固定
    // 分配 —— GUI 裁决后**只重载数据、不重排列表**，用户按 Cxxx 记的决定不会错位。

    /// 冲突里一个字段的「库内已核实旧值 → 新稿值」。
    struct ConflictFieldDiff: Identifiable {
        var name = ""
        var old = ""
        var new = ""
        var id: String { name }
        var oldText: String { old.isEmpty ? "（空）" : old }
        var newText: String { new.isEmpty ? "（空）" : new }
    }

    /// 一条待裁决冲突（= 清单里的一个 C 编号）。
    struct ConflictItem: Identifiable {
        var stableNo = 0
        var recordKey = ""
        var bibKey = ""
        var entryType = ""
        var title = ""
        var author = ""
        var year = ""
        var issue = ""
        var slug = ""
        var file = ""
        var at = ""
        var incomingSha256 = ""
        var pending = true
        var resolution = ""
        var resolvedAt = ""
        var diff: [ConflictFieldDiff] = []
        var advice = ""
        var verified = false
        var method = ""

        var id: String { "\(stableNo)|\(recordKey)|\(incomingSha256)|\(at)" }
        var stableLabel: String { String(format: "C%03d", stableNo) }
        var fingerprintShort: String {
            incomingSha256.count > 16 ? String(incomingSha256.prefix(16)) + "…" : incomingSha256
        }
        var hasFingerprint: Bool { !incomingSha256.isEmpty }
        var diffFieldsText: String { diff.map { $0.name }.joined(separator: "、") }
        var sourceText: String {
            let parts = [issue, slug, file].filter { !$0.isEmpty }
            return parts.isEmpty ? "（未记录来源）" : parts.joined(separator: " / ")
        }
        var briefText: String {
            "\(title) · \(author)" + (year.isEmpty ? "" : " · \(year)")
        }
        /// 已裁决动作的中文（只在 `resolved_at` 存在时有意义；导入默认的 kept-existing 不算裁决）
        var resolutionTitle: String {
            guard !resolvedAt.isEmpty else { return "" }
            switch resolution {
            case "kept-existing": return "保留旧值"
            case "deferred": return "待人工"
            case "adopted": return "采用新值"
            case "superseded": return "被重叠裁决取代"
            case "forced-replaced": return "整条替换（历史）"
            case "replaced-unverified": return "被未核实值替换"
            default: return resolution.isEmpty ? "已裁决" : resolution
            }
        }
    }

    /// 冲突列表按期/篇分组（顺序 = 引擎给的顺序，不重排）。
    struct ConflictGroup: Identifiable {
        var issue = ""
        var slug = ""
        var items: [ConflictItem] = []
        var id: String { "\(issue)|\(slug)" }
        var issueTitle: String { issue.isEmpty ? "（无期——手工导入/未记录来源）" : issue }
        var slugTitle: String { slug.isEmpty ? "（无篇名）" : slug }
        var pendingCount: Int { items.filter { $0.pending }.count }
        var file: String { items.first?.file ?? "" }
    }

    /// 一次冲突列表加载结果。
    struct ConflictsOutcome {
        var dbPath = ""
        var dbSha256 = ""
        var total = 0
        var pending = 0
        var resolved = 0
        var items: [ConflictItem] = []
        var error: String?
        var seconds: Double = 0
        var rawOutput = ""

        var shaShort: String {
            dbSha256.count > 12 ? String(dbSha256.prefix(12)) + "…" : dbSha256
        }
        var pendingItems: [ConflictItem] { items.filter { $0.pending } }
        var verifiedPending: [ConflictItem] { items.filter { $0.pending && $0.verified } }
        /// 高亮提醒：这些**库内已核实**记录的字段将被改（采用新值前必须逐条看清楚）
        var verifiedPendingCount: Int { verifiedPending.count }
        /// 按期→篇分组（引擎顺序本身已按期→篇→条目，这里只做连续分组）
        var groups: [ConflictGroup] {
            var out: [ConflictGroup] = []
            for item in items {
                if var last = out.last, last.issue == item.issue, last.slug == item.slug {
                    last.items.append(item)
                    out[out.count - 1] = last
                } else {
                    out.append(ConflictGroup(issue: item.issue, slug: item.slug, items: [item]))
                }
            }
            return out
        }
    }

    /// 「待裁决」页列表的**纯逻辑状态**（B-143；headless 可测，视图只负责画）。
    ///
    /// 判据全部来自引擎：数据一次读全（`conflicts --json` 已带 `pending` 与 totals），
    /// 默认只显示 `pending=true`；开关打开才显示已裁决（灰显，审计留痕）。
    /// **C 编号沿用引擎 `stable_no`，过滤不重排** —— 用户按 Cxxx 记的决定不会错位。
    struct ConflictListState {
        var showResolved = false
        var items: [ConflictItem] = []
        var total = 0
        var pending = 0
        var resolved = 0

        init(outcome: ConflictsOutcome, showResolved: Bool = false) {
            self.showResolved = showResolved
            self.items = outcome.items
            self.total = outcome.total
            self.pending = outcome.pending
            self.resolved = outcome.resolved
        }

        /// 当前开关态下可见的条目（默认过滤已裁决；顺序/编号沿用引擎）
        var visibleItems: [ConflictItem] {
            showResolved ? items : items.filter { $0.pending }
        }

        /// 可见条目按期→篇连续分组（与 `ConflictsOutcome.groups` 同一规则，不重排）
        var visibleGroups: [ConflictGroup] {
            var out: [ConflictGroup] = []
            for item in visibleItems {
                if var last = out.last, last.issue == item.issue, last.slug == item.slug {
                    last.items.append(item)
                    out[out.count - 1] = last
                } else {
                    out.append(ConflictGroup(issue: item.issue, slug: item.slug, items: [item]))
                }
            }
            return out
        }

        /// 某组「全部条目数」（含被开关隐藏的已裁决）——分组头「待裁决 N（共 M）」的 M。
        /// 不能用 visibleGroups 里的 items.count：默认关时它只含 pending，会把 M 误缩成 N
        /// （Lead 复审发现的 spec §2.2 口径偏差，B-143 修正）。
        func groupTotal(forGroupId id: String) -> Int {
            items.filter { "\($0.issue)|\($0.slug)" == id }.count
        }

        /// 默认过滤下是否「该显示空态」：库里有冲突、但待裁决为 0（当前真实库就是这样）
        var isEmptyByFilter: Bool {
            !showResolved && !items.isEmpty && pending == 0
        }

        /// 列表区空态文案（三种情况说清，不让人误以为是加载失败/显示 bug）
        var emptyText: String {
            if total == 0 {
                return "当前库没有字段冲突（待裁决 0 / 已裁决 0）。"
            }
            if isEmptyByFilter {
                return "当前没有待裁决冲突；历史已裁决 \(resolved) 条，"
                    + "可打开「显示已裁决」查看（C 编号固定）。"
            }
            return ""
        }

        /// 状态条一句话：「待裁决 N ｜ 已裁决 M ｜ 共 T」（spec B-143 §2.2，不用易误读的 N/M 比例）
        var statusText: String {
            "待裁决 \(pending) ｜ 已裁决 \(resolved) ｜ 共 \(total)"
        }

        /// feedback 一行：说明编号固定 + 已裁决默认隐藏（与原「共 N 条（含已裁决）」区分开）
        var feedbackText: String {
            "共 \(total) 条冲突（待裁决 \(pending) / 已裁决 \(resolved)，C 编号固定）；"
                + (showResolved ? "正在显示已裁决（灰显，仅供审计留痕）。"
                                : "已裁决默认隐藏，可用「显示已裁决」查看。")
        }

        /// 分组头：「待裁决 N（共 M）」——不再用易误读的 `N / M`
        static func groupCountText(pending: Int, total: Int) -> String {
            "待裁决 \(pending)（共 \(total)）"
        }

        /// 开关文案（默认关；打开 = 显示已裁决、灰显留痕）
        static var toggleTitle: String { "显示已裁决（审计留痕，默认关）" }
    }

    private static func parseConflictItem(_ dict: [String: Any]) -> ConflictItem {
        var item = ConflictItem()
        item.stableNo = intValue(dict["stable_no"])
        item.recordKey = stringValue(dict["record_key"])
        item.bibKey = stringValue(dict["bib_key"])
        item.entryType = stringValue(dict["entry_type"])
        item.title = stringValue(dict["title"])
        item.author = stringValue(dict["author"])
        item.year = stringValue(dict["year"])
        item.issue = stringValue(dict["issue"])
        item.slug = stringValue(dict["slug"])
        item.file = stringValue(dict["file"])
        item.at = stringValue(dict["at"])
        item.incomingSha256 = stringValue(dict["incoming_sha256"])
        item.pending = (dict["pending"] as? Bool) != false
        item.resolution = stringValue(dict["resolution"])
        item.resolvedAt = stringValue(dict["resolved_at"])
        item.advice = stringValue(dict["advice"])
        item.verified = (dict["verified"] as? Bool) == true
        item.method = stringValue(dict["method"])
        if let rawDiff = dict["diff"] as? [String: Any] {
            for (name, value) in rawDiff {
                guard let values = value as? [String: Any] else { continue }
                item.diff.append(ConflictFieldDiff(
                    name: name,
                    old: stringValue(values["old"]),
                    new: stringValue(values["new"])))
            }
            item.diff.sort { $0.name < $1.name }
        }
        return item
    }

    /// 读冲突条目：`bibdb.py conflicts --json [--pending-only]`（**只读；同步；后台队列调**）。
    static func loadConflicts(pendingOnly: Bool = false) -> ConflictsOutcome {
        let startedAt = Date()
        var out = ConflictsOutcome()
        guard let db = existingDBPath() else {
            out.error = "引用库文件不存在或找不到笔记仓：\(dbPath() ?? "（未找到笔记仓）")\n"
                + "先到「入藏」页导入一份 .bib，或到「引用库设置」页指定笔记仓路径。"
            return out
        }
        out.dbPath = db
        var arguments = ["conflicts", "--json", "--db", db]
        if pendingOnly { arguments.append("--pending-only") }
        let run = runJSON(arguments)
        guard run.ok, let payload = run.json else {
            if let payload = run.json, !stringValue(payload["error"]).isEmpty {
                out.error = stringValue(payload["error"])
            } else {
                out.error = failureText(run)
            }
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        out.dbSha256 = stringValue(payload["db_sha256"])
        if let totals = payload["totals"] as? [String: Any] {
            out.total = intValue(totals["total"])
            out.pending = intValue(totals["pending"])
            out.resolved = intValue(totals["resolved"])
        }
        for raw in (payload["items"] as? [Any]) ?? [] {
            guard let dict = raw as? [String: Any] else { continue }
            out.items.append(parseConflictItem(dict))
        }
        // 引擎不在 items 里时要能自洽：总数退化为条目数
        if out.total == 0 { out.total = out.items.count }
        if out.pending == 0 { out.pending = out.items.filter { $0.pending }.count }
        out.rawOutput = run.output
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    // MARK: - 裁决写库（resolve；行内三按钮 / 编辑裁决 bib）

    /// 一个字段的「将改」变化（old → new；remove = 新稿没有该字段）。
    struct FieldChange: Identifiable {
        var name = ""
        var old = ""
        var new = ""
        var remove = false
        var id: String { name }
        var oldText: String { old.isEmpty ? "（空）" : old }
        var newText: String { remove ? "（删除）" : (new.isEmpty ? "（空）" : new) }
    }

    /// 一次裁决动作的结果（keep / defer / adopt / replace 共用）。
    struct ResolveOutcome {
        var key = ""
        var action = ""
        var resolution = ""
        var already = false
        var written = false
        var dryRun = false
        var matchedCount = 0
        var changes: [FieldChange] = []
        var supersededCount = 0
        var overlappingCount = 0
        var contentShaBefore = ""
        var contentShaAfter = ""
        var dbShaBefore = ""
        var dbShaAfter = ""
        var backupPath = ""
        var pendingAfter = -1
        var totalAfter = -1
        var error: String?
        var seconds: Double = 0
        var rawOutput = ""

        var changeSummary: String {
            if changes.isEmpty { return "无字段变化" }
            return "将改 \(changes.count) 个字段（\(changes.map { $0.name }.joined(separator: "、"))）"
        }
    }

    private static func parseFieldChanges(from value: Any?) -> [FieldChange] {
        guard let dict = value as? [String: Any] else { return [] }
        var out: [FieldChange] = []
        for (name, raw) in dict {
            guard let values = raw as? [String: Any] else { continue }
            out.append(FieldChange(name: name,
                                   old: stringValue(values["old"]),
                                   new: stringValue(values["new"]),
                                   remove: (values["remove"] as? Bool) == true))
        }
        out.sort { $0.name < $1.name }
        return out
    }

    /// 解析 resolve/replace 的 `--json`（键集见 spec §4.2；新键缺失时不假装有）。
    private static func parseResolvePayload(_ payload: [String: Any], dryRun: Bool,
                                           startedAt: Date) -> ResolveOutcome {
        var out = ResolveOutcome()
        out.dryRun = dryRun
        out.key = stringValue(payload["key"])
        out.action = stringValue(payload["action"])
        out.resolution = stringValue(payload["resolution"])
        out.already = (payload["already"] as? Bool) == true
        out.written = (payload["written"] as? Bool) == true
        out.matchedCount = intValue(payload["count"])
        out.changes = parseFieldChanges(from: payload["replaced_fields"])
        if out.changes.isEmpty { out.changes = parseFieldChanges(from: payload["adopted_fields"]) }
        if let superseded = payload["superseded"] as? [Any] {
            out.supersededCount = superseded.count
        }
        if let overlapping = payload["overlapping"] as? [Any] {
            out.overlappingCount = overlapping.count
        }
        out.contentShaBefore = stringValue(payload["content_sha256_before"])
        out.contentShaAfter = stringValue(payload["content_sha256_after"])
        out.dbShaBefore = stringValue(payload["db_sha256_before"])
        out.dbShaAfter = stringValue(payload["db_sha256_after"])
        out.backupPath = stringValue(payload["backup_path"])
        if let totals = payload["totals"] as? [String: Any] {
            out.totalAfter = intValue(totals["total"])
            out.pendingAfter = intValue(totals["pending"])
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 一次裁决：`resolve --key K --action keep|defer|adopt [--incoming-sha256 S] --json`。
    ///
    /// 写前保护（spec §4.2）：
    ///   · `expectDBsha256` 非空 → `--expect-db-sha256`（列表过期则引擎拒绝写，退出 2）；
    ///   · `backupDirectory` 非空 → `--backup <目录>`（引擎写前自动备份，仅真写时才产生）。
    /// `dryRun=true` 时只算不写（adopt 的「预览差异」用它）。
    /// **同步；只许后台队列调**。
    static func resolveConflict(key: String, action: String, incomingSha256: String,
                                fields: [String] = [],
                                expectDBsha256: String = "",
                                backupDirectory: String = "",
                                allowOverlap: Bool = false,
                                note: String = "",
                                dryRun: Bool = false) -> ResolveOutcome {
        let startedAt = Date()
        var out = ResolveOutcome()
        out.key = key
        out.action = action
        out.dryRun = dryRun
        guard let db = existingDBPath() else {
            out.error = "引用库文件不存在或找不到笔记仓（无法裁决）。"
            return out
        }
        let name = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else {
            out.error = "缺少库内记录 key（不能裁决）。"
            return out
        }
        var arguments = ["resolve", "--key", name, "--action", action, "--json", "--db", db]
        let sha = incomingSha256.trimmingCharacters(in: .whitespacesAndNewlines)
        if !sha.isEmpty {
            arguments += ["--incoming-sha256", sha]
        } else if !fields.isEmpty {
            arguments += ["--fields", fields.joined(separator: ",")]
        } else {
            arguments.append("--all-fields")
        }
        if !note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            arguments += ["--note", note]
        }
        if allowOverlap { arguments.append("--allow-overlap") }
        if dryRun { arguments.append("--dry-run") }
        if !expectDBsha256.isEmpty { arguments += ["--expect-db-sha256", expectDBsha256] }
        if !backupDirectory.isEmpty { arguments += ["--backup", backupDirectory] }
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
        out = parseResolvePayload(payload, dryRun: dryRun, startedAt: startedAt)
        out.rawOutput = run.output
        return out
    }

    /// 编辑裁决 bib：把用户编辑后的 bib 通过**临时文件**交给
    /// `resolve --action replace --incoming-sha256 S --bib TMP`（**引擎零新增 stdin 参数**）。
    ///
    /// 安全边界（spec §4.2/§4.4）：bib 必须**恰好 1 条**、key 必须等于库内 record key；
    /// 是否满足由引擎判（退出 2 + 中文原因），GUI 不重复解析 bib。临时文件用后即删。
    /// **同步；只许后台队列调**。
    static func replaceRecord(recordKey: String, bibText: String, incomingSha256: String,
                              expectDBsha256: String = "",
                              backupDirectory: String = "",
                              allowOverlap: Bool = false,
                              note: String = "",
                              dryRun: Bool = false) -> ResolveOutcome {
        let startedAt = Date()
        var out = ResolveOutcome()
        out.key = recordKey
        out.action = "replace"
        out.dryRun = dryRun
        guard let db = existingDBPath() else {
            out.error = "引用库文件不存在或找不到笔记仓（无法裁决）。"
            return out
        }
        let text = bibText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            out.error = "先编辑/粘贴要写入库的 bib 条目。"
            return out
        }
        // 临时文件：文件名固定；每次用独立临时目录避免撞名；整个目录用后即删。
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("SLCN-replace-\(UUID().uuidString)")
        let file = dir.appendingPathComponent("edited-bib.bib")
        defer { try? FileManager.default.removeItem(at: dir) }
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try text.write(to: file, atomically: true, encoding: .utf8)
        } catch {
            out.error = "写临时 bib 失败：\(error.localizedDescription)"
            return out
        }
        var arguments = ["resolve", "--key", recordKey, "--action", "replace",
                         "--incoming-sha256", incomingSha256, "--bib", file.path,
                         "--json", "--db", db]
        if !note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            arguments += ["--note", note]
        }
        if allowOverlap { arguments.append("--allow-overlap") }
        if dryRun { arguments.append("--dry-run") }
        if !expectDBsha256.isEmpty { arguments += ["--expect-db-sha256", expectDBsha256] }
        if !backupDirectory.isEmpty { arguments += ["--backup", backupDirectory] }
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
        out = parseResolvePayload(payload, dryRun: dryRun, startedAt: startedAt)
        out.rawOutput = run.output
        return out
    }

    // MARK: - 一键全部采用（resolve --adopt-all-pending）

    /// 批量采用里的一条（= 一个记录上将被改的字段集）。
    struct AdoptAllItem: Identifiable {
        var recordKey = ""
        var bibKey = ""
        var incomingSha256 = ""
        var changes: [FieldChange] = []
        var id: String { recordKey + "|" + incomingSha256 }
    }

    /// `resolve --adopt-all-pending` 的结果（dry-run 与真写共用）。
    struct AdoptAllOutcome {
        var dryRun = false
        var written = false
        var adopted: [AdoptAllItem] = []
        var supersededCount = 0
        var totalBefore = -1
        var pendingBefore = -1
        var totalAfter = -1
        var pendingAfter = -1
        var dbShaBefore = ""
        var dbShaAfter = ""
        var backupPath = ""
        var error: String?
        var seconds: Double = 0
        var rawOutput = ""

        var recordCount: Int { adopted.count }
        var fieldCount: Int { adopted.reduce(0) { $0 + $1.changes.count } }
        var shaShortBefore: String {
            dbShaBefore.count > 12 ? String(dbShaBefore.prefix(12)) + "…" : dbShaBefore
        }
    }

    private static func parseAdoptAllPayload(_ payload: [String: Any], dryRun: Bool,
                                            startedAt: Date) -> AdoptAllOutcome {
        var out = AdoptAllOutcome()
        out.dryRun = dryRun
        out.written = (payload["written"] as? Bool) == true
        for raw in (payload["adopted"] as? [Any]) ?? [] {
            guard let dict = raw as? [String: Any] else { continue }
            var item = AdoptAllItem()
            item.recordKey = stringValue(dict["record_key"])
            item.bibKey = stringValue(dict["bib_key"])
            item.incomingSha256 = stringValue(dict["incoming_sha256"])
            item.changes = parseFieldChanges(from: dict["fields"])
            out.adopted.append(item)
        }
        out.supersededCount = ((payload["superseded"] as? [Any]) ?? []).count
        if let before = payload["totals_before"] as? [String: Any] {
            out.totalBefore = intValue(before["total"])
            out.pendingBefore = intValue(before["pending"])
        }
        if let after = payload["totals_after"] as? [String: Any] {
            out.totalAfter = intValue(after["total"])
            out.pendingAfter = intValue(after["pending"])
        }
        out.dbShaBefore = stringValue(payload["db_sha256_before"])
        out.dbShaAfter = stringValue(payload["db_sha256_after"])
        out.backupPath = stringValue(payload["backup_path"])
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 一键全部采用：对待裁决冲突逐条按 adopt 语义应用 incoming 值。
    ///
    /// `dryRun=true` = 预览（逐条将改字段 + superseded 列表，库不变）——GUI 的「预览」用它，
    /// 用户二次确认后才调 `dryRun=false` 真写（单次原子写 + 单次备份）。
    /// 同记录待裁决冲突字段重叠时引擎会拒绝（退出 2 并列出冲突号），GUI 原样显示。
    /// **同步；只许后台队列调**。
    static func adoptAllPending(expectDBsha256: String = "",
                                backupDirectory: String = "",
                                dryRun: Bool = true) -> AdoptAllOutcome {
        let startedAt = Date()
        var out = AdoptAllOutcome()
        out.dryRun = dryRun
        guard let db = existingDBPath() else {
            out.error = "引用库文件不存在或找不到笔记仓（无法裁决）。"
            return out
        }
        var arguments = ["resolve", "--adopt-all-pending", "--json", "--db", db]
        if dryRun { arguments.append("--dry-run") }
        if !expectDBsha256.isEmpty { arguments += ["--expect-db-sha256", expectDBsha256] }
        if !backupDirectory.isEmpty { arguments += ["--backup", backupDirectory] }
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
        out = parseAdoptAllPayload(payload, dryRun: dryRun, startedAt: startedAt)
        out.rawOutput = run.output
        return out
    }

    // MARK: - 从备份恢复（restore）

    /// 自动备份目录（spec §5⑥）：`~/Library/Application Support/SLCN/bibdb-backups/`。
    /// **不进 git、不随仓库分发**；GUI 的写操作都往这里备份，恢复也默认从这里列。
    static var backupDirectory: String {
        let base = FileManager.default.urls(for: .applicationSupportDirectory,
                                           in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory() + "/Library/Application Support")
        return base.appendingPathComponent("SLCN").appendingPathComponent("bibdb-backups").path
    }

    /// 一个备份文件（名称/时间/大小；sha 前缀从文件名 `bibliography-<ts>-<sha12>.jsonl` 里取）。
    struct BackupFile: Identifiable {
        var path = ""
        var name = ""
        var modified: Date?
        var bytes = 0
        var shaHint = ""
        var id: String { path }
    }

    /// 列备份（**只读**；目录不存在时返回空数组，不报错）。
    /// `directory` 只为测试可隔离（界面一律用默认备份目录）。
    static func listBackups(in directory: String? = nil) -> [BackupFile] {
        let dir = directory ?? backupDirectory
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: dir) else { return [] }
        var files: [BackupFile] = []
        for name in names where name.hasSuffix(".jsonl") {
            let path = PathKit.join(dir, name)
            var file = BackupFile(path: path, name: name)
            let attrs = try? fm.attributesOfItem(atPath: path)
            if let attrs {
                file.bytes = (attrs[.size] as? NSNumber)?.intValue ?? 0
                file.modified = attrs[.modificationDate] as? Date
            }
            // 名称形如 `bibliography-20261021T120000Z-abcdef123456.jsonl`
            let parts = name.replacingOccurrences(of: ".jsonl", with: "").split(separator: "-")
            if let last = parts.last, last.count >= 8 { file.shaHint = String(last) }
            files.append(file)
        }
        files.sort { ($0.modified ?? .distantPast) > ($1.modified ?? .distantPast) }
        return files
    }

    /// 一次恢复的结果。
    struct RestoreOutcome {
        var dryRun = false
        var written = false
        var backupPath = ""
        var backupSha256 = ""     // 备份文件自身的 sha（dry-run 时「恢复后库 sha」= 它）
        var dbShaBefore = ""
        var dbShaAfter = ""
        var error: String?
        var seconds: Double = 0
        var rawOutput = ""

        /// 预览要显示的「恢复后库 sha」：dry-run 引擎返回的 after 仍是当前库 sha，
        /// 目标态要用 `backup_sha256`；真写后两者一致。
        var targetSha: String {
            if !backupSha256.isEmpty { return backupSha256 }
            return dbShaAfter
        }
    }

    /// 从备份恢复整库：`restore --backup PATH [--expect-db-sha256] [--dry-run] --json`。
    /// 校验（备份可解析 + 行级 schema + 期望 sha）全部由引擎做；失败库不变。
    /// **同步；只许后台队列调**。
    static func restore(backupPath: String, expectDBsha256: String = "",
                        dryRun: Bool = true) -> RestoreOutcome {
        let startedAt = Date()
        var out = RestoreOutcome()
        out.dryRun = dryRun
        out.backupPath = backupPath
        guard let db = existingDBPath() else {
            out.error = "引用库文件不存在或找不到笔记仓（无法恢复）。"
            return out
        }
        let path = PathKit.normalize(backupPath)
        guard !path.isEmpty, FileManager.default.fileExists(atPath: path) else {
            out.error = "读不到备份文件：\(backupPath)"
            return out
        }
        var arguments = ["restore", "--backup", path, "--json", "--db", db]
        if dryRun { arguments.append("--dry-run") }
        if !expectDBsha256.isEmpty { arguments += ["--expect-db-sha256", expectDBsha256] }
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
        out.written = (payload["written"] as? Bool) == true
        let reported = stringValue(payload["backup_path"])
        out.backupPath = reported.isEmpty ? path : reported
        out.backupSha256 = stringValue(payload["backup_sha256"])
        out.dbShaBefore = stringValue(payload["db_sha256_before"])
        out.dbShaAfter = stringValue(payload["db_sha256_after"])
        out.seconds = Date().timeIntervalSince(startedAt)
        out.rawOutput = run.output
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
