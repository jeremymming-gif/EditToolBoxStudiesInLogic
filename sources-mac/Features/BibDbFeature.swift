import Foundation
import CryptoKit   // B-188：dbManifest 的库 sha（流式 SHA256；纯本地、不起子进程）

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
        /// B-188：`lint --with-records` 给的全量受影响 key 列表（旧引擎没有这个键 → `[]`）。
        /// 条目类 = 记录 key；`duplicate_content` = 重复组内 key；`near_duplicates` = `A≈B` 串；
        /// alias 类 = alias 本身。**只用于展开逐条处理，不在这里判任何东西**。
        var records: [String] = []
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
            // B-188：`lint --with-records` 才有的全量清单；老引擎缺这个键 → 空数组（向后兼容）
            item.records = (dict["records"] as? [Any])?.map { stringValue($0) } ?? []
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
        // B-188（P0，spec §2 诊断）：可见条目 / 按期→篇分组 / 每组全部条目数都改成
        // **init 时一次算好的 stored property**。原来 `visibleItems`/`visibleGroups`/
        // `groupTotal` 是每次访问都重算的 computed property —— 视图 body 每次访问
        // `listState` 都 new 一个状态，打开「显示已裁决」时同步构建 181+ 行、主线程卡。
        // 公开 API/语义/C 编号顺序/分组头计数与旧实现**完全一致**（第 7 台大量断言它）。
        var visibleItems: [ConflictItem] = []
        var visibleGroups: [ConflictGroup] = []
        /// 分组 id → 该组**全部**条目数（含被开关隐藏的已裁决）；分组头「待裁决 N（共 M）」的 M。
        var groupTotals: [String: Int] = [:]

        init(outcome: ConflictsOutcome, showResolved: Bool = false) {
            self.showResolved = showResolved
            self.items = outcome.items
            self.total = outcome.total
            self.pending = outcome.pending
            self.resolved = outcome.resolved
            // 一次算好：① 可见条目（默认过滤已裁决）② 按期→篇连续分组（不重排）
            let visible = showResolved ? outcome.items : outcome.items.filter { $0.pending }
            self.visibleItems = visible
            var groups: [ConflictGroup] = []
            for item in visible {
                if var last = groups.last, last.issue == item.issue, last.slug == item.slug {
                    last.items.append(item)
                    groups[groups.count - 1] = last
                } else {
                    groups.append(ConflictGroup(issue: item.issue, slug: item.slug, items: [item]))
                }
            }
            self.visibleGroups = groups
            // ③ 每组「全部条目数」：必须用**未过滤**的 items 算，否则默认关时
            //    混合组会把 M 误缩成 N（B-143 Lead 复审修正，口径不能退回去）。
            var totals: [String: Int] = [:]
            for item in outcome.items {
                totals["\(item.issue)|\(item.slug)", default: 0] += 1
            }
            self.groupTotals = totals
        }

        /// 当前开关态下可见的条目（默认过滤已裁决；顺序/编号沿用引擎）
        // B-188：`visibleItems` 由 computed 改为 init 预计算（见上），调用点不用改。

        /// 可见条目按期→篇连续分组（与 `ConflictsOutcome.groups` 同一规则，不重排）
        // B-188：`visibleGroups` 由 computed 改为 init 预计算（见上）。

        /// 某组「全部条目数」（含被开关隐藏的已裁决）——分组头「待裁决 N（共 M）」的 M。
        /// 不能用 visibleGroups 里的 items.count：默认关时它只含 pending，会把 M 误缩成 N。
        func groupTotal(forGroupId id: String) -> Int {
            groupTotals[id] ?? 0
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

    /// B-188：`ConflictListState` 的**双态缓存**（给「待处理」页用；纯逻辑、非 ObservableObject）。
    ///
    /// 一次 `conflicts --json` 加载后，两种开关态各预计算一次（181 条量级、便宜），
    /// 之后切换本地开关**只查缓存**，不再重算过滤与分组；视图把本地 `showResolved` 换掉即可。
    struct ReviewCacheState {
        var outcome = ConflictsOutcome()
        var showResolved = false
        var pendingState = ConflictListState(outcome: ConflictsOutcome())
        var resolvedState = ConflictListState(outcome: ConflictsOutcome())

        init() {}

        init(outcome: ConflictsOutcome, showResolved: Bool = false) {
            self.outcome = outcome
            self.showResolved = showResolved
            self.pendingState = ConflictListState(outcome: outcome, showResolved: false)
            self.resolvedState = ConflictListState(outcome: outcome, showResolved: true)
        }

        /// 当前本地开关态对应的列表状态（已缓存，O(1)）
        var listState: ConflictListState { showResolved ? resolvedState : pendingState }

        /// 这份缓存是否真的读到过库（错误/空库时视图该显示空态而不是「加载中」）
        var loaded: Bool { outcome.error == nil && outcome.seconds >= 0 && !outcome.dbPath.isEmpty }
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

    // MARK: - B-188：显式库路径 / 新鲜度 / 待处理 / 编辑 / 合并 / 字段归位
    //
    // 这一段的约定（B-188 GUI 分片；对外签名与口径见 `/tmp/b188-gui/CONTRACT.md`）：
    //   · 所有**会起子进程**的函数都显式接受 `db: String`，绝不回落到 `existingDBPath()`；
    //     `db` 为空 / 文件不存在 → 直接返回 error、**不起子进程**（防误写真实库）。
    //   · 写库函数（`applyEdit` / `applyMerge` / `applySelectedFieldFixes`）**一律**把
    //     `--backup DIR` 与 `--expect-db-sha256 SHA` 传给引擎（空串由引擎按「不校验」处理；
    //     `--backup` 空串会被引擎拒绝 → 宁可写不进去，也不静默无备份写库）。
    //   · 全部是**同步**函数 → 视图只许在后台队列调、回主线程回填（文件抬头的线程约定不变）。
    //   · 判据仍然只在引擎：这里只拼 argv / 收 `--json` / 翻界面模型；**不重写**字段规范化、
    //     key 查重、必填字段、bib 解析。

    /// `db` 参数为空/不存在的统一错误文案（**绝不**偷偷换成 `existingDBPath()`）。
    private static func dbPathError(_ raw: String) -> String {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty {
            return "没有指定引用库路径（db 为空）：为防误写真实库，本次不调用引擎。"
        }
        return "引用库文件不存在：\(PathKit.normalize(text))\n"
            + "（B-188 引擎调用只对显式 --db 的库操作，不会回落到笔记仓真实库。）"
    }

    /// 解析并校验显式传给引擎的 `db`：空/不存在 → nil（调用方返回 `dbPathError`、不起子进程）。
    private static func existingExplicitDB(_ raw: String) -> String? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        let path = PathKit.normalize(text)
        guard FileManager.default.fileExists(atPath: path) else { return nil }
        return path
    }

    // MARK: B-188-7：库文件新鲜度（进页不自动重载的依据）

    /// 库文件清单：路径 / 是否存在 / 字节数 / mtime / sha256（B-188 §4.1）。
    /// 视图缓存一份（进页先算当前 manifest，`isStale` 为 false 就**不重跑引擎**）。
    struct DBManifest: Equatable {
        var path = ""
        var exists = false
        var size: Int64 = 0
        /// mtime（Unix 秒；`TimeInterval`）
        var mtime: TimeInterval = 0
        var sha256 = ""

        var shaShort: String {
            sha256.count > 12 ? String(sha256.prefix(12)) + "…" : sha256
        }
    }

    /// 读库清单（**同步；后台队列调**）。6 MB 库整读算 SHA256 约十几毫秒，可接受；
    /// 不存在的文件返回 `exists=false`（path 仍给规范化后的目标，供 isStale 比较）。
    static func dbManifest(db: String) -> DBManifest {
        var manifest = DBManifest()
        let path = PathKit.normalize(db)
        manifest.path = path
        guard !path.isEmpty else { return manifest }
        let fm = FileManager.default
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: path, isDirectory: &isDir), !isDir.boolValue else {
            return manifest
        }
        manifest.exists = true
        if let attrs = try? fm.attributesOfItem(atPath: path) {
            manifest.size = (attrs[.size] as? NSNumber)?.int64Value ?? 0
            if let date = attrs[.modificationDate] as? Date {
                manifest.mtime = date.timeIntervalSince1970
            }
        }
        if let handle = FileHandle(forReadingAtPath: path) {
            defer { try? handle.close() }
            var hasher = SHA256()
            while true {
                let chunk = handle.readData(ofLength: 1 << 20)   // 1 MiB 一块，流式
                if chunk.isEmpty { break }
                hasher.update(data: chunk)
            }
            manifest.sha256 = hasher.finalize().map { String(format: "%02x", $0) }.joined()
        }
        return manifest
    }

    /// 缓存是否过期（**纯逻辑**）：没缓存 / 路径变 / 存在性变 / size 或 mtime 变 / sha 变 → true。
    static func isStale(cached: DBManifest?, current: DBManifest) -> Bool {
        guard let cached else { return true }
        if cached.path != current.path { return true }
        if cached.exists != current.exists { return true }
        guard current.exists else { return false }   // 两边都「不存在」且路径相同 → 不算过期
        if cached.size != current.size { return true }
        if abs(cached.mtime - current.mtime) > 0.001 { return true }  // 文件系统时间精度容差
        if cached.sha256 != current.sha256 { return true }
        return false
    }

    // MARK: B-188-1：库健康（lint --with-records 的结构化 records）

    /// 一次库健康加载结果（「待处理」页状态条 + 「库健康」段的数据源）。
    struct HealthOutcome {
        var lint: Lint?
        var categories: [LintWarningCategory] = []
        var warningCount = 0
        var affectedRecords = 0
        var dbSha256 = ""
        var error: String?
        var seconds: Double = 0
        /// 顺带带回的库清单（视图缓存它判新鲜，避免再读一遍 6 MB 文件）
        var manifest: DBManifest?
    }

    /// 读库健康：`lint --json --with-records`（**只读；同步；后台队列调**）。
    /// `lint` 有 warning 时引擎退出码 1 但 JSON 有效 —— 与既有 `overview()` 同一口径。
    static func loadHealth(db: String) -> HealthOutcome {
        let startedAt = Date()
        var out = HealthOutcome()
        guard let path = existingExplicitDB(db) else {
            out.error = dbPathError(db)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let run = runJSON(["lint", "--json", "--with-records", "--db", path])
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
        let lint = parseLint(payload)
        out.lint = lint
        out.categories = healthCategories(from: lint)
        out.warningCount = lint.warningCount > 0 ? lint.warningCount : lint.warningItems.count
        out.affectedRecords = healthAffectedRecords(lint)
        let manifest = dbManifest(db: path)
        out.manifest = manifest
        out.dbSha256 = manifest.sha256
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 库健康分类（**纯逻辑**）：复用 `LintWarningDigest.categories` 的固定顺序与中文标题；
    /// 每个 item 自带 `records` 全量清单（`lint --with-records` 解析而来）。
    static func healthCategories(from lint: Lint) -> [LintWarningCategory] {
        LintWarningDigest.categories(items: lint.warningItems,
                                     legacyWarnings: lint.warnings)
    }

    /// 受影响记录数（**纯逻辑**）：所有 `item.records` 去重计数（真实库 ≈355，含 2 个近重复
    /// `A≈B` 串）。旧引擎没有 `records` 时退回 `keys_sample`（只是粗看规模，不假装全量）。
    static func healthAffectedRecords(_ lint: Lint) -> Int {
        var seen = Set<String>()
        for item in lint.warningItems {
            if item.records.isEmpty {
                for key in item.keysSample where !key.isEmpty { seen.insert(key) }
            } else {
                for key in item.records where !key.isEmpty { seen.insert(key) }
            }
        }
        return seen.count
    }

    /// 库健康一句话（**纯逻辑**）：`库健康 18 条/涉及约 355 条记录`；
    /// 未加载 → `库健康（未加载）`。条数 = warning 条数（不是受影响记录数）。
    static func healthStatusText(_ lint: Lint?) -> String {
        guard let lint else { return "库健康（未加载）" }
        let warnings = lint.warningCount > 0 ? lint.warningCount : lint.warningItems.count
        return "库健康 \(warnings) 条/涉及约 \(healthAffectedRecords(lint)) 条记录"
    }

    // MARK: B-188-2：「待处理」状态条（纯逻辑，testbench 可断言）

    /// 状态条：`待处理 N（字段冲突 P ｜ 库健康 18 条/涉及约 355 条记录）｜ 已处理 M ｜ 库 sha <12位>…`
    ///
    /// 口径（与设计卡 §4.2 一致，写死在 CONTRACT.md）：
    ///   · N = P + 库健康 warning 条数（warning 条数，不是受影响记录数）；
    ///   · M = 已处理条目数（冲突已裁决 + 编辑/合并留痕，见 `processedCount`）；
    ///   · sha 取前 **12** 位 + `…`；未加载的占位符是 `（未加载）`。
    static func pendingStatusText(conflictPending: Int, health: Lint?,
                                  processedCount: Int, dbSha256: String) -> String {
        let pending = max(0, conflictPending)
        let warnings = health.map { $0.warningCount > 0 ? $0.warningCount : $0.warningItems.count } ?? 0
        let total = pending + max(0, warnings)
        let sha = dbSha256.trimmingCharacters(in: .whitespacesAndNewlines)
        let shaText = sha.isEmpty
            ? "库 sha（未加载）"
            : "库 sha " + (sha.count > 12 ? String(sha.prefix(12)) + "…" : sha)
        return "待处理 \(total)（字段冲突 \(pending) ｜ \(healthStatusText(health))）"
            + "｜ 已处理 \(max(0, processedCount)) ｜ \(shaText)"
    }

    // MARK: B-188-3：「已处理」留痕（扫描记录 edits[]，不建 sidecar）

    /// 编辑痕迹里的一个字段变化（`edits[].changed_fields`）。
    struct EditTraceFieldChange: Identifiable, Equatable {
        var name = ""
        var old = ""
        var new = ""
        var id: String { name }
    }

    /// 一条写库痕迹（= 记录里 `edits[]` 的一项；`edit` / `merge` 共用）。
    ///
    /// 数据只来自库 JSONL 的记录 `edits[]`（引擎 `edit`/`merge-records --apply` 写的），
    /// GUI **不建 sidecar**、不重算判据。`edit` 的 `changed_fields` 与 `merge` 的
    /// `merged_keys` 只会有一个非空。
    struct EditTrace: Identifiable {
        var recordKey = ""       // 痕迹所在记录（merge 后 = 新 key）
        var action = ""          // edit / merge
        var at = ""
        var note = ""
        var oldKey = ""
        var newKey = ""
        var changedFields: [EditTraceFieldChange] = []
        var mergedKeys: [String] = []
        var backupPath = ""
        var backupSha256 = ""

        var id: String { "\(recordKey)|\(action)|\(at)|\(oldKey)|\(newKey)" }
        var actionTitle: String {
            switch action {
            case "merge": return "合并记录"
            case "edit": return "编辑条目"
            default: return action.isEmpty ? "写库痕迹" : action
            }
        }
        var backupShaShort: String {
            backupSha256.count > 12 ? String(backupSha256.prefix(12)) + "…" : backupSha256
        }
        var changedFieldsText: String {
            changedFields.map { $0.name }.joined(separator: "、")
        }
    }

    /// 只读扫描库 JSONL 的 `edits[]`（**同步；后台队列调**；6 MB / 7051 行）。
    ///
    /// 用字符串预筛（行里含 `"edits"`）再逐行 `JSONSerialization`；**不写库、不做判据**，
    /// 单行坏数据/坏痕迹跳过（不中断）。返回 `(traces: 写库痕迹, total: 痕迹条数)`；
    /// 按时间**倒序**（ISO 8601 到秒，同格式字符串可比较），同一时间按 key/action 稳定排序。
    static func scanEditTraces(db: String) -> (traces: [EditTrace], total: Int) {
        guard let path = existingExplicitDB(db) else { return ([], 0) }
        guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return ([], 0) }
        var traces: [EditTrace] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            guard line.contains("\"edits\"") else { continue }        // 字符串预筛（绝大多数行跳过）
            guard let data = String(line).data(using: .utf8),
                  let record = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            else { continue }                                        // 坏行跳过，不中断扫描
            let recordKey = stringValue(record["key"])
            guard let edits = record["edits"] as? [Any] else { continue }
            for raw in edits {
                guard let dict = raw as? [String: Any] else { continue }
                var trace = EditTrace()
                trace.recordKey = recordKey
                trace.action = stringValue(dict["action"])
                trace.at = stringValue(dict["at"])
                trace.note = stringValue(dict["note"])
                trace.oldKey = stringValue(dict["old_key"])
                trace.newKey = stringValue(dict["new_key"])
                trace.backupPath = stringValue(dict["backup_path"])
                trace.backupSha256 = stringValue(dict["backup_sha256"])
                trace.mergedKeys = (dict["merged_keys"] as? [Any])?.map { stringValue($0) } ?? []
                if let changed = dict["changed_fields"] as? [String: Any] {
                    var rows: [EditTraceFieldChange] = []
                    for (name, value) in changed {
                        guard let pair = value as? [String: Any] else { continue }
                        rows.append(EditTraceFieldChange(name: name,
                                                         old: stringValue(pair["old"]),
                                                         new: stringValue(pair["new"])))
                    }
                    trace.changedFields = rows.sorted { $0.name < $1.name }
                }
                traces.append(trace)
            }
        }
        traces.sort { lhs, rhs in
            if lhs.at != rhs.at { return lhs.at > rhs.at }
            if lhs.recordKey != rhs.recordKey { return lhs.recordKey < rhs.recordKey }
            return lhs.action < rhs.action
        }
        return (traces, traces.count)
    }

    /// 「已处理」列表的一行（冲突已裁决 + 编辑/合并留痕统一形状；视图只负责画）。
    struct ProcessedEntry: Identifiable, Equatable {
        var kind = ""            // conflict / edit / merge
        var key = ""
        var actionTitle = ""
        var at = ""
        var backupSha256 = ""    // 短 sha；冲突裁决没有备份 sha（引擎不记）
        var detail = ""
        var id = ""
    }

    /// 已处理条目（**纯逻辑**）：①冲突已裁决项（中文动作 + resolved_at + C 编号/key）；
    /// ②编辑/合并痕迹（动作/时间/备份 sha/字段或 merged_keys 摘要）。
    /// 按时间**倒序**、稳定排序；不校验、不补判据。
    static func processedEntries(conflicts: ConflictsOutcome?, traces: [EditTrace]) -> [ProcessedEntry] {
        var rows: [ProcessedEntry] = []
        if let conflicts {
            for item in conflicts.items where !item.pending && !item.resolvedAt.isEmpty {
                var row = ProcessedEntry()
                row.kind = "conflict"
                row.key = item.recordKey
                row.actionTitle = item.resolutionTitle.isEmpty ? "已裁决" : item.resolutionTitle
                row.at = item.resolvedAt
                row.detail = "\(item.stableLabel) · 字段 \(item.diffFieldsText.isEmpty ? "（无）" : item.diffFieldsText)"
                row.id = "conflict|\(item.id)"
                rows.append(row)
            }
        }
        for trace in traces {
            var row = ProcessedEntry()
            row.kind = trace.action.isEmpty ? "edit" : trace.action
            row.key = trace.newKey.isEmpty ? trace.recordKey : trace.newKey
            row.actionTitle = trace.actionTitle
            row.at = trace.at
            row.backupSha256 = trace.backupShaShort
            if trace.action == "merge" {
                var detail = "合并 \(trace.mergedKeys.joined(separator: "、")) → \(row.key)"
                if !trace.note.isEmpty { detail += "；备注：\(trace.note)" }
                row.detail = detail
            } else {
                var detail = trace.changedFieldsText.isEmpty
                    ? "字段无变化" : "改字段：\(trace.changedFieldsText)"
                if trace.oldKey != trace.newKey && !trace.newKey.isEmpty {
                    detail += "；key：\(trace.oldKey) → \(trace.newKey)"
                }
                if !trace.note.isEmpty { detail += "；备注：\(trace.note)" }
                row.detail = detail
            }
            row.id = "trace|\(trace.id)|\(row.key)"
            rows.append(row)
        }
        rows.sort { lhs, rhs in
            if lhs.at != rhs.at { return lhs.at > rhs.at }
            if lhs.kind != rhs.kind { return lhs.kind < rhs.kind }
            if lhs.key != rhs.key { return lhs.key < rhs.key }
            return lhs.id < rhs.id
        }
        return rows
    }

    /// 已处理条目数（**纯逻辑**）：与 `processedEntries` 同一口径；状态条的 M 用它。
    static func processedCount(conflicts: ConflictsOutcome?, traces: [EditTrace]) -> Int {
        processedEntries(conflicts: conflicts, traces: traces).count
    }

    // MARK: B-188-4：条目编辑器（引擎唯一判据；这里只有 argv + JSON + UI 级校验）

    /// 一条记录的编辑草稿（编辑器 sheet 的表单状态）。
    struct EditorDraft: Equatable {
        var recordKey = ""
        var entryType = ""
        /// 全量字段表（`--json-fields` 是**整表替换**，所以这里永远带全量）
        var fields: [String: String] = [:]
        var aliases: [String] = []
        var newKeyEnabled = false
        var newKey = ""
        var note = ""
        /// 原始 BibTeX（`query --bibtex` 或 raw 区解析后的原文）
        var rawBib = ""

        /// 字段名排序（视图渲染用；字典本身无序）
        var sortedFieldNames: [String] { fields.keys.sorted() }
    }

    /// 编辑器**纯 UI 级**校验结果（不判必填/key 撞车/类型合法性 —— 那些交引擎 dry-run）。
    struct EditorValidation {
        var errors: [String] = []
        var warnings: [String] = []
        var isBlocked: Bool { !errors.isEmpty }
    }

    /// 编辑器纯逻辑校验（headless 可测）：
    ///   · 至少 1 个非空字段；字段名 `^[a-z][a-z0-9_-]*$`；
    ///   · 空值允许（引擎会清理/删字段）但给 warning；
    ///   · 勾了改 key 时新 key 非空、不含空白/逗号/花括号/引号。
    /// **不**判必填字段、key 是否撞车、entry_type 是否合法（引擎 dry-run 会给）。
    static func validateEditorDraft(_ draft: EditorDraft) -> EditorValidation {
        var out = EditorValidation()
        if draft.recordKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            out.errors.append("缺少库内记录 key。")
        }
        let nonEmpty = draft.fields.filter {
            !$0.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
        if nonEmpty.isEmpty {
            out.errors.append("至少要保留 1 个非空字段（引擎会拒绝没有字段的记录）。")
        }
        let namePattern = #"^[a-z][a-z0-9_-]*$"#
        for name in draft.fields.keys.sorted()
        where name.range(of: namePattern, options: .regularExpression) == nil {
            out.errors.append("字段名 `\(name)` 不合法：只允许小写字母开头的字母/数字/下划线/连字符。")
        }
        let empties = draft.fields.filter {
            $0.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }.keys.sorted()
        if !empties.isEmpty {
            out.warnings.append("这些字段值为空（引擎会删掉该字段）：\(empties.joined(separator: "、"))")
        }
        if draft.newKeyEnabled {
            let key = draft.newKey.trimmingCharacters(in: .whitespacesAndNewlines)
            if key.isEmpty {
                out.errors.append("勾了「改 key」但新 key 为空。")
            } else if key.range(of: #"[\s,{}()\"]"#, options: .regularExpression) != nil {
                out.errors.append("新 key `\(key)` 含空白/逗号/花括号/引号，不是合法 BibTeX key。")
            } else if key == draft.recordKey {
                out.warnings.append("新 key 与当前 key 相同：这次不会改名（引擎按同一 key 处理）。")
            }
        }
        return out
    }

    /// 编辑器加载结果。
    struct EditorLoadOutcome {
        var draft: EditorDraft?
        var error: String?
        var seconds: Double = 0
    }

    /// 按 key（认 alias）加载一条记录为编辑草稿：`query --key K --json --bibtex`
    /// （**只读；同步；后台队列调**）。0 命中 / ambiguous → error，不猜。
    static func loadEditorDraft(db: String, key: String) -> EditorLoadOutcome {
        let startedAt = Date()
        var out = EditorLoadOutcome()
        guard let path = existingExplicitDB(db) else {
            out.error = dbPathError(db)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let name = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else {
            out.error = "缺少要编辑的条目 key。"
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let run = runJSON(["query", "--key", name, "--json", "--bibtex", "--db", path])
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
        let hits = (payload["hits"] as? [Any]) ?? []
        if hits.isEmpty {
            out.error = "库里没有 key/alias `\(name)`。"
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        if hits.count > 1 || (payload["ambiguous"] as? Bool) == true {
            out.error = "key/alias `\(name)` 命中 \(hits.count) 条记录（ambiguous）：请检查 alias 撞车。"
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        guard let dict = hits[0] as? [String: Any],
              let record = dict["record"] as? [String: Any] else {
            out.error = "引擎返回的命中缺少 record 对象。"
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        var draft = EditorDraft()
        draft.recordKey = stringValue(record["key"])        // alias 查询 → canonical key
        draft.entryType = stringValue(record["entry_type"])
        draft.fields = fields(record)
        draft.aliases = (record["aliases"] as? [Any])?.map { stringValue($0) } ?? []
        draft.rawBib = stringValue(dict["bibtex"])
        out.draft = draft
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 编辑器里的一个字段变化（`edit --json` 的 `field_changes`）。
    struct EditorFieldChange: Identifiable, Equatable {
        var name = ""
        var old = ""
        var new = ""
        var id: String { name }
        /// 新值为空 = 该字段将被删除（`--json-fields` 整表替换）
        var removal: Bool { new.isEmpty }
    }

    /// `edit --dry-run --json` 的界面模型（预览=字段 diff / 类型变化 / 必填缺字段 / sha）。
    struct EditPreviewOutcome {
        var key = ""
        var newKey = ""
        var requestedNewKey = ""
        var entryTypeBefore = ""
        var entryTypeAfter = ""
        var fieldChanges: [EditorFieldChange] = []
        var aliasesAdded: [String] = []
        var missingBefore: [String] = []
        var missingAfter: [String] = []
        var dbSha256Before = ""
        var dbSha256AfterEstimate = ""
        var conflictsBlocking: [String] = []
        var error: String?
        var seconds: Double = 0
    }

    /// 写库后复核（`verification`；edit 与 merge-records 同一形状）。
    struct WriteVerification: Equatable {
        var ok = false
        var problems: [String] = []
        var records = 0
        var lintErrors = 0
        var lintWarnings = 0
        var pendingConflicts = 0

        var summary: String {
            if !ok {
                return "写后复核失败：" + (problems.isEmpty ? "（引擎未给原因）" : problems.joined(separator: "；"))
            }
            return "写后复核通过（记录 \(records) 条；lint error \(lintErrors) / warning \(lintWarnings)）"
        }
    }

    /// `edit --apply` 的结果（写库统一形状）。
    struct EditApplyOutcome {
        var written = false
        var key = ""
        var newKey = ""
        var dbSha256Before = ""
        var dbSha256After = ""
        var backupPath = ""
        var verification: WriteVerification?
        var error: String?
        var seconds: Double = 0
    }

    /// `EditorDraft` → `--json-fields` 的 JSON 文本（**全量字段**，键排序保证可复现）。
    private static func editorFieldsJSON(_ draft: EditorDraft) -> String? {
        let payload: [String: Any] = ["entry_type": draft.entryType, "fields": draft.fields]
        guard let data = try? JSONSerialization.data(withJSONObject: payload,
                                                     options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8) else { return nil }
        return text
    }

    /// 拼 `edit` 的 argv（dry-run / apply 共用）。`--backup`/`--expect-db-sha256`
    /// 在 apply 时**永远**传（空串由引擎拒绝/不校验）。
    private static func editArguments(db: String, draft: EditorDraft,
                                      expectDBsha256: String, backupDir: String,
                                      apply: Bool) -> (arguments: [String], error: String?) {
        guard let fieldsText = editorFieldsJSON(draft) else {
            return ([], "编辑器字段无法序列化成 JSON（内部错误）：请重试。")
        }
        let key = draft.recordKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { return ([], "缺少库内记录 key。") }
        var arguments = ["edit", "--key", key, "--json-fields", fieldsText,
                         "--json", "--db", db]
        if apply {
            arguments.append("--apply")
            arguments += ["--backup", backupDir]
            arguments += ["--expect-db-sha256", expectDBsha256]
        } else {
            arguments.append("--dry-run")
            if !expectDBsha256.isEmpty {
                arguments += ["--expect-db-sha256", expectDBsha256]
            }
        }
        if draft.newKeyEnabled {
            let newKey = draft.newKey.trimmingCharacters(in: .whitespacesAndNewlines)
            if !newKey.isEmpty { arguments += ["--new-key", newKey] }
        }
        let note = draft.note.trimmingCharacters(in: .whitespacesAndNewlines)
        if !note.isEmpty { arguments += ["--note", draft.note] }
        return (arguments, nil)
    }

    private static func editorFieldChanges(from value: Any?) -> [EditorFieldChange] {
        guard let dict = value as? [String: Any] else { return [] }
        var rows: [EditorFieldChange] = []
        for (name, raw) in dict {
            guard let pair = raw as? [String: Any] else { continue }
            rows.append(EditorFieldChange(name: name,
                                          old: stringValue(pair["old"]),
                                          new: stringValue(pair["new"])))
        }
        rows.sort { $0.name < $1.name }
        return rows
    }

    private static func stringArray(_ value: Any?) -> [String] {
        (value as? [Any])?.map { stringValue($0) } ?? []
    }

    private static func parseWriteVerification(_ value: Any?) -> WriteVerification? {
        guard let dict = value as? [String: Any], !dict.isEmpty else { return nil }
        var out = WriteVerification()
        out.ok = (dict["ok"] as? Bool) == true
        out.problems = stringArray(dict["problems"])
        out.records = intValue(dict["records"])
        out.lintErrors = intValue(dict["lint_errors"])
        out.lintWarnings = intValue(dict["lint_warnings"])
        out.pendingConflicts = intValue(dict["pending_conflicts"])
        return out
    }

    /// 预览编辑：`edit --dry-run --json`（**同步；后台队列调**）。
    /// `--json-fields` 传**全量字段**；`newKeyEnabled && !newKey.isEmpty` 才带 `--new-key`。
    static func previewEdit(db: String, draft: EditorDraft,
                            expectDBsha256: String = "") -> EditPreviewOutcome {
        let startedAt = Date()
        var out = EditPreviewOutcome()
        out.key = draft.recordKey
        guard let path = existingExplicitDB(db) else {
            out.error = dbPathError(db)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let built = editArguments(db: path, draft: draft,
                                  expectDBsha256: expectDBsha256,
                                  backupDir: BackupDirectoryPlaceholder.none,
                                  apply: false)
        if let buildError = built.error {
            out.error = buildError
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let run = runJSON(built.arguments)
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
        out.key = stringValue(payload["key"])
        out.newKey = stringValue(payload["new_key"])
        out.requestedNewKey = stringValue(payload["requested_new_key"])
        out.entryTypeBefore = stringValue(payload["entry_type_before"])
        out.entryTypeAfter = stringValue(payload["entry_type_after"])
        out.fieldChanges = editorFieldChanges(from: payload["field_changes"])
        out.aliasesAdded = stringArray(payload["aliases_added"])
        out.missingBefore = stringArray(payload["missing_before"])
        out.missingAfter = stringArray(payload["missing_after"])
        out.dbSha256Before = stringValue(payload["db_sha256_before"])
        out.dbSha256AfterEstimate = stringValue(payload["db_sha256_after_estimate"])
        if out.dbSha256AfterEstimate.isEmpty {
            out.dbSha256AfterEstimate = stringValue(payload["db_sha256_after"])
        }
        out.conflictsBlocking = stringArray(payload["conflicts_blocking"])
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 写入编辑：`edit --apply --backup DIR --expect-db-sha256 SHA --json`
    /// （**同步；后台队列调**）。失败原样展示引擎中文 error（sha 过期/key 撞车/待裁决冲突…）。
    static func applyEdit(db: String, draft: EditorDraft, expectDBsha256: String,
                          backupDir: String = BibDbFeature.backupDirectory) -> EditApplyOutcome {
        let startedAt = Date()
        var out = EditApplyOutcome()
        out.key = draft.recordKey
        out.dbSha256Before = expectDBsha256
        guard let path = existingExplicitDB(db) else {
            out.error = dbPathError(db)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let validation = validateEditorDraft(draft)
        if validation.isBlocked {
            out.error = validation.errors.joined(separator: "\n")
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let built = editArguments(db: path, draft: draft,
                                  expectDBsha256: expectDBsha256,
                                  backupDir: backupDir, apply: true)
        if let buildError = built.error {
            out.error = buildError
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let run = runJSON(built.arguments)
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
        out.key = stringValue(payload["key"])
        out.newKey = stringValue(payload["new_key"])
        out.dbSha256Before = stringValue(payload["db_sha256_before"])
        out.dbSha256After = stringValue(payload["db_sha256_after"])
        out.backupPath = stringValue(payload["backup_path"])
        out.verification = parseWriteVerification(payload["verification"])
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 解析「原始 BibTeX 区」：写临时 `.bib`（`/tmp/b188-gui/editor-<uuid>.bib`，用后即删）→
    /// `edit --key K --bib tmp --dry-run --json` 让**引擎**解析校验（**禁止**在 Swift 里写解析器）。
    ///
    /// 成功时用「库内当前 record.fields + `field_changes`」重建整表（`new==""` → 删除该字段；
    /// `entry_type` 用 `entry_type_after`），返回 `EditorDraft`（`rawBib` = 原文；key 用 canonical）。
    /// 失败（语法错/多条/key 不符/库内无此 key）→ `(nil, 引擎中文 error)`。
    static func parseRawBib(db: String, raw: String, key: String) -> (draft: EditorDraft?, error: String?) {
        guard let path = existingExplicitDB(db) else { return (nil, dbPathError(db)) }
        let name = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return (nil, "缺少要解析的条目 key。") }
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return (nil, "先粘贴/编写一条 BibTeX 再解析。") }
        // ① 先查库内当前记录：重建整表要用它，aliases 也顺带保留
        let queryRun = runJSON(["query", "--key", name, "--json", "--bibtex", "--db", path])
        guard let queryPayload = queryRun.json else { return (nil, failureText(queryRun)) }
        if !stringValue(queryPayload["error"]).isEmpty {
            return (nil, stringValue(queryPayload["error"]))
        }
        let hits = (queryPayload["hits"] as? [Any]) ?? []
        guard hits.count == 1, let dict = hits[0] as? [String: Any],
              let record = dict["record"] as? [String: Any] else {
            return (nil, "解析失败：库里没有 key/alias `\(name)`（或命中多条，需人工确认）。")
        }
        let canonicalKey = stringValue(record["key"])
        var currentFields = fields(record)
        let aliases = (record["aliases"] as? [Any])?.map { stringValue($0) } ?? []
        // ② 临时 bib 文件；整个流程同步，用后立即删除
        let dir = "/tmp/b188-gui"
        let file = PathKit.join(dir, "editor-\(UUID().uuidString).bib")
        defer { try? FileManager.default.removeItem(atPath: file) }
        do {
            try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try text.write(toFile: file, atomically: true, encoding: .utf8)
        } catch {
            return (nil, "写临时 bib 失败：\(error.localizedDescription)")
        }
        let run = runJSON(["edit", "--key", canonicalKey, "--bib", file,
                           "--dry-run", "--json", "--db", path])
        guard let payload = run.json else { return (nil, failureText(run)) }
        if !stringValue(payload["error"]).isEmpty {
            return (nil, stringValue(payload["error"]))
        }
        if run.status != 0 { return (nil, failureText(run)) }
        for change in editorFieldChanges(from: payload["field_changes"]) {
            if change.new.isEmpty {
                currentFields.removeValue(forKey: change.name)
            } else {
                currentFields[change.name] = change.new
            }
        }
        var draft = EditorDraft()
        draft.recordKey = stringValue(payload["key"]).isEmpty
            ? canonicalKey : stringValue(payload["key"])
        draft.entryType = stringValue(payload["entry_type_after"])
        draft.fields = currentFields
        draft.aliases = aliases
        draft.rawBib = raw
        return (draft, nil)
    }

    /// `previewEdit` 的 dry-run 不需要备份目录；用一个语义化的空串占位（不进 argv）。
    private enum BackupDirectoryPlaceholder {
        static let none = ""
    }

    // MARK: B-188-5：对照 / 合并（merge-records）

    /// 一条参与对照/合并的库记录。
    struct MergeRecord {
        var key = ""
        var entryType = ""
        var fields: [String: String] = [:]
        var aliases: [String] = []
        var bibtex = ""
    }

    /// 逐 key 读对照材料的结果。
    struct MergeLoadOutcome {
        var records: [MergeRecord] = []
        var error: String?
        var seconds: Double = 0
    }

    /// 读对照材料：逐 key `query --key K --json --bibtex`（**只读；同步；后台队列调**）。
    /// alias 自动解析到 canonical key；任一缺失 → error 列出；两条 key 指向同一条 → error。
    static func loadMergeDraft(db: String, keys: [String]) -> MergeLoadOutcome {
        let startedAt = Date()
        var out = MergeLoadOutcome()
        guard let path = existingExplicitDB(db) else {
            out.error = dbPathError(db)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let names = keys.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard names.count >= 2 else {
            out.error = "对照/合并至少要 2 条记录。"
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        var seen = Set<String>()
        var missing: [String] = []
        for name in names {
            let run = runJSON(["query", "--key", name, "--json", "--bibtex", "--db", path])
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
            let hits = (payload["hits"] as? [Any]) ?? []
            guard hits.count == 1, let dict = hits[0] as? [String: Any],
                  let record = dict["record"] as? [String: Any] else {
                missing.append(name)
                continue
            }
            let canonical = stringValue(record["key"])
            if !seen.insert(canonical).inserted {
                out.error = "key `\(names.joined(separator: "`、`"))` 解析后指向同一条记录 `\(canonical)`：不能和自己合并。"
                out.seconds = Date().timeIntervalSince(startedAt)
                return out
            }
            var material = MergeRecord()
            material.key = canonical
            material.entryType = stringValue(record["entry_type"])
            material.fields = fields(record)
            material.aliases = (record["aliases"] as? [Any])?.map { stringValue($0) } ?? []
            material.bibtex = stringValue(dict["bibtex"])
            out.records.append(material)
        }
        if !missing.isEmpty {
            out.error = "库里没有这些 key/alias：\(missing.joined(separator: "、"))"
            out.records = []
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 合并后的建议新 key（**纯逻辑**）：第一作者家族姓（去 LaTeX/重音，只留 A-Za-z）+ 第一条的
    /// 4 位 year；无作者/无年份 → 第一条 key。例：`Tindale, C. W.` + 2015 → `Tindale2015`。
    static func suggestedMergeKey(records: [MergeRecord]) -> String {
        guard let first = records.first else { return "" }
        let author = first.fields["author"] ?? first.fields["editor"] ?? ""
        let surname = plainAuthorSurname(author)
        let year = firstFourDigitYear(first.fields["year"] ?? first.fields["date"] ?? "")
        guard !surname.isEmpty, !year.isEmpty else { return first.key }
        return surname + year
    }

    /// `Tindale, C. W.` → `Tindale`（第一作者；含逗号取逗号前，否则最后一个 token），
    /// 然后去 LaTeX 命令/括号/重音、只保留 A-Za-z。
    private static func plainAuthorSurname(_ raw: String) -> String {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return "" }
        let firstAuthor: String
        if let range = text.range(of: #"\s+and\s+"#,
                                  options: [.regularExpression, .caseInsensitive]) {
            firstAuthor = String(text[..<range.lowerBound])
        } else {
            firstAuthor = text
        }
        let bare: String
        if let comma = firstAuthor.firstIndex(of: ",") {
            bare = String(firstAuthor[..<comma])
        } else {
            bare = firstAuthor.split(whereSeparator: { $0.isWhitespace }).last.map(String.init)
                ?? firstAuthor
        }
        return latinLettersOnly(bare)
    }

    /// 去 LaTeX 命令/括号/重音后只留 A-Za-z（重音先 folding 成基字母：ü → u）。
    private static func latinLettersOnly(_ raw: String) -> String {
        var text = raw
        // 去 `\textit` / `\'` / `\c` 之类命令（保留被修饰的字母本身）
        text = text.replacingOccurrences(of: #"\\[a-zA-Z]+\*?"#,
                                         with: "", options: .regularExpression)
        // 只去重音（**不要** caseInsensitive：那会把 Tindale 折成 tindale，key 惯例是首字母大写）
        text = text.folding(options: [.diacriticInsensitive],
                            locale: Locale(identifier: "en_US_POSIX"))
        return text.filter { character in
            guard character.unicodeScalars.count == 1,
                  let scalar = character.unicodeScalars.first else { return false }
            return (scalar.value >= 65 && scalar.value <= 90)
                || (scalar.value >= 97 && scalar.value <= 122)
        }
    }

    /// 第一个 4 位年份（1500–2099）；没有 → `""`。
    private static func firstFourDigitYear(_ raw: String) -> String {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let range = text.range(of: #"(?<!\d)(1[5-9]\d{2}|20[0-2]\d)(?!\d)"#,
                                     options: .regularExpression) else { return "" }
        return String(text[range])
    }

    /// 合并字段对照的一行（纯逻辑；字段并集、键排序）。
    struct MergeFieldRow: Identifiable {
        var field = ""
        /// 记录 key → 该条在该字段上的值（没有该字段 → 空串，保证列稳定）
        var values: [String: String] = [:]
        /// 默认取值的来源记录 key（引擎 `field_comparison[field].chosen` 同一口径）
        var chosen = ""
        /// 各条值不全相同（至少两列有差异）
        var differs = false

        var id: String { field }
        var chosenValue: String { values[chosen] ?? "" }
    }

    /// 合并对照行（**纯逻辑**）：字段并集、键排序；默认取第一条非空来源；`differs` = 值不全相同。
    static func mergeFieldRows(records: [MergeRecord]) -> [MergeFieldRow] {
        guard !records.isEmpty else { return [] }
        let keys = records.map { $0.key }
        let byKey = Dictionary(uniqueKeysWithValues: records.map { ($0.key, $0.fields) })
        var names = Set<String>()
        for record in records { names.formUnion(record.fields.keys) }
        var rows: [MergeFieldRow] = []
        for field in names.sorted() {
            var row = MergeFieldRow(field: field)
            for key in keys { row.values[key] = byKey[key]?[field] ?? "" }
            row.chosen = keys.first(where: { !(row.values[$0] ?? "").isEmpty })
                ?? (keys.first ?? "")
            row.differs = Set(keys.map { row.values[$0] ?? "" }).count > 1
            rows.append(row)
        }
        return rows
    }

    /// 合并前纯逻辑校验（其余交引擎）：≥2 条、不重复、新 key 非空且无空白/逗号/花括号/引号。
    static func validateMerge(keys: [String], newKey: String) -> [String] {
        var errors: [String] = []
        let cleaned = keys.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        if cleaned.count < 2 { errors.append("合并至少要选 2 条记录。") }
        if Set(cleaned).count != cleaned.count { errors.append("合并的 key 有重复。") }
        let key = newKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if key.isEmpty {
            errors.append("合并必须给新 key（作者姓+年份，如 Tindale2015）。")
        } else if key.range(of: #"[\s,{}()\"]"#, options: .regularExpression) != nil {
            errors.append("新 key `\(key)` 含空白/逗号/花括号/引号，不是合法 BibTeX key。")
        }
        return errors
    }

    /// `merge-records --dry-run --json` 的界面模型。
    struct MergePreviewOutcome {
        var newKey = ""
        var entryType = ""
        var recordsBefore = 0
        var recordsAfter = 0
        var fieldRows: [MergeFieldRow] = []
        var aliases: [String] = []
        var dbSha256Before = ""
        var dbSha256AfterEstimate = ""
        var error: String?
        var seconds: Double = 0
    }

    /// `merge-records --apply` 的结果。
    struct MergeApplyOutcome {
        var written = false
        var newKey = ""
        var recordsAfter = 0
        var aliases: [String] = []
        var dbSha256Before = ""
        var dbSha256After = ""
        var backupPath = ""
        var verification: WriteVerification?
        var error: String?
        var seconds: Double = 0
    }

    private static func mergeArguments(db: String, keys: [String], newKey: String,
                                       entryType: String, fieldSources: [String: String],
                                       expectDBsha256: String, backupDir: String,
                                       apply: Bool) -> [String] {
        var arguments = ["merge-records", "--keys", keys.joined(separator: ","),
                         "--new-key", newKey, "--json", "--db", db]
        let type = entryType.trimmingCharacters(in: .whitespacesAndNewlines)
        if !type.isEmpty { arguments += ["--entry-type", type] }
        for field in fieldSources.keys.sorted() {
            arguments += ["--field-source", "\(field)=\(fieldSources[field] ?? "")"]
        }
        if apply {
            arguments.append("--apply")
            arguments += ["--backup", backupDir]
            arguments += ["--expect-db-sha256", expectDBsha256]
        } else {
            arguments.append("--dry-run")
            if !expectDBsha256.isEmpty {
                arguments += ["--expect-db-sha256", expectDBsha256]
            }
        }
        return arguments
    }

    private static func parseMergeFieldRows(_ value: Any?, keys: [String]) -> [MergeFieldRow] {
        guard let dict = value as? [String: Any] else { return [] }
        var rows: [MergeFieldRow] = []
        for field in dict.keys.sorted() {
            guard let entry = dict[field] as? [String: Any] else { continue }
            var row = MergeFieldRow(field: field)
            for key in keys { row.values[key] = stringValue(entry[key]) }
            row.chosen = stringValue(entry["chosen"])
            if row.chosen.isEmpty {
                row.chosen = keys.first(where: { !(row.values[$0] ?? "").isEmpty }) ?? (keys.first ?? "")
            }
            row.differs = Set(keys.map { row.values[$0] ?? "" }).count > 1
            rows.append(row)
        }
        return rows
    }

    /// 预览合并：`merge-records --dry-run --json`（**同步；后台队列调**）。
    /// `fieldSources` → 重复 `--field-source 字段=KEY`；`entryType` 非空才传 `--entry-type`。
    static func previewMerge(db: String, keys: [String], newKey: String,
                             entryType: String = "", fieldSources: [String: String] = [:],
                             expectDBsha256: String = "") -> MergePreviewOutcome {
        let startedAt = Date()
        var out = MergePreviewOutcome()
        guard let path = existingExplicitDB(db) else {
            out.error = dbPathError(db)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let errors = validateMerge(keys: keys, newKey: newKey)
        if !errors.isEmpty {
            out.error = errors.joined(separator: "\n")
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let cleaned = keys.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        let name = newKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let arguments = mergeArguments(db: path, keys: cleaned, newKey: name,
                                       entryType: entryType, fieldSources: fieldSources,
                                       expectDBsha256: expectDBsha256,
                                       backupDir: BackupDirectoryPlaceholder.none,
                                       apply: false)
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
        out.newKey = stringValue(payload["new_key"])
        out.entryType = stringValue(payload["entry_type"])
        out.recordsBefore = intValue(payload["records_before"])
        out.recordsAfter = intValue(payload["records_after"])
        out.fieldRows = parseMergeFieldRows(payload["field_comparison"], keys: cleaned)
        out.aliases = stringArray(payload["aliases"])
        out.dbSha256Before = stringValue(payload["db_sha256_before"])
        out.dbSha256AfterEstimate = stringValue(payload["db_sha256_after_estimate"])
        if out.dbSha256AfterEstimate.isEmpty {
            out.dbSha256AfterEstimate = stringValue(payload["db_sha256_after"])
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 写入合并：`merge-records --apply --backup DIR --expect-db-sha256 SHA --json`
    /// （**同步；后台队列调**）。完全不自动合并 —— 只有用户确认后才调这个。
    static func applyMerge(db: String, keys: [String], newKey: String,
                           entryType: String = "", fieldSources: [String: String] = [:],
                           expectDBsha256: String,
                           backupDir: String = BibDbFeature.backupDirectory) -> MergeApplyOutcome {
        let startedAt = Date()
        var out = MergeApplyOutcome()
        out.newKey = newKey
        out.dbSha256Before = expectDBsha256
        guard let path = existingExplicitDB(db) else {
            out.error = dbPathError(db)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let errors = validateMerge(keys: keys, newKey: newKey)
        if !errors.isEmpty {
            out.error = errors.joined(separator: "\n")
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let cleaned = keys.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        let name = newKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let arguments = mergeArguments(db: path, keys: cleaned, newKey: name,
                                       entryType: entryType, fieldSources: fieldSources,
                                       expectDBsha256: expectDBsha256,
                                       backupDir: backupDir, apply: true)
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
        out.newKey = stringValue(payload["new_key"])
        out.recordsAfter = intValue(payload["records_after"])
        out.aliases = stringArray(payload["aliases"])
        out.dbSha256Before = stringValue(payload["db_sha256_before"])
        out.dbSha256After = stringValue(payload["db_sha256_after"])
        out.backupPath = stringValue(payload["backup_path"])
        out.verification = parseWriteVerification(payload["verification"])
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    // MARK: B-188-6：字段归位预览（suggest-field-fixes；A/C 类）

    /// 一条归位候选（引擎 `candidates[]`；`source=="entry_type"` 的是 C 类类型候选）。
    struct FieldFixCandidate: Equatable {
        var field = ""            // C 类 entry_type 候选没有 field（空串）
        var value = ""
        var source = ""           // author / journal / booktitle / note / publisher / address / entry_type
        var evidence = ""
        var risk = ""             // low / medium / high
        var riskOrder = 3         // 0 低 / 1 中 / 2 高 / 3 未知（同 risk 的排序用）

        var riskText: String {
            switch risk.lowercased() {
            case "low": return "低"
            case "medium": return "中"
            case "high": return "高"
            default: return risk.isEmpty ? "未知" : risk
            }
        }
    }

    /// 字段归位预览的一行（= 一条记录）。
    struct FieldFixRow: Identifiable {
        var key = ""
        var entryType = ""
        var missing: [String] = []
        var fields: [String: String] = [:]
        var candidates: [FieldFixCandidate] = []
        var issues: [String] = []
        /// C 类：unknown_entry_type / doi_key / placeholder_key；A 类为空
        var anomalyKind = ""
        var keyCandidate = ""
        var keyReason = ""
        var keyRisk = ""
        var keySuggestion = ""

        var id: String { key }
    }

    /// `suggest-field-fixes --json` 的加载结果。
    struct FieldFixOutcome {
        var rows: [FieldFixRow] = []
        var dbSha256 = ""
        var error: String?
        var seconds: Double = 0
    }

    private static func candidateRiskOrder(_ risk: String) -> Int {
        switch risk.lowercased() {
        case "low": return 0
        case "medium": return 1
        case "high": return 2
        default: return 3
        }
    }

    private static func anomalyTitle(_ kind: String) -> String {
        switch kind {
        case "unknown_entry_type": return "entry_type 笔误"
        case "doi_key": return "DOI/网址当 key"
        case "placeholder_key": return "占位符 key"
        default: return kind.isEmpty ? "类型/key 异常" : kind
        }
    }

    private static func parseFieldFixRow(_ dict: [String: Any]) -> FieldFixRow {
        var row = FieldFixRow()
        row.key = stringValue(dict["key"])
        row.entryType = stringValue(dict["entry_type"])
        row.missing = stringArray(dict["missing"])
        row.fields = fields(dict)
        row.issues = stringArray(dict["issues"])
        row.anomalyKind = stringValue(dict["anomaly_kind"])
        row.keyCandidate = stringValue(dict["key_candidate"])
        row.keyReason = stringValue(dict["key_reason"])
        row.keyRisk = stringValue(dict["key_risk"])
        row.keySuggestion = stringValue(dict["key_suggestion"])
        for raw in (dict["candidates"] as? [Any]) ?? [] {
            guard let candidate = raw as? [String: Any] else { continue }
            var item = FieldFixCandidate()
            item.field = stringValue(candidate["field"])
            item.value = stringValue(candidate["value"])
            item.source = stringValue(candidate["source"])
            item.evidence = stringValue(candidate["evidence"])
            item.risk = stringValue(candidate["risk"])
            item.riskOrder = candidateRiskOrder(item.risk)
            row.candidates.append(item)
        }
        return row
    }

    /// 读字段归位预览：`suggest-field-fixes --registry ... --json`（**只读；同步；后台队列调**）。
    /// `registry` 只允许 `missing_field` / `unknown_entry_type`（其它值直接 error、不起子进程）。
    static func loadFieldFixRows(db: String, registry: String) -> FieldFixOutcome {
        let startedAt = Date()
        var out = FieldFixOutcome()
        let name = registry.trimmingCharacters(in: .whitespacesAndNewlines)
        guard name == "missing_field" || name == "unknown_entry_type" else {
            out.error = "字段归位只支持 missing_field / unknown_entry_type（收到 `\(registry)`）。"
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        guard let path = existingExplicitDB(db) else {
            out.error = dbPathError(db)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let run = runJSON(["suggest-field-fixes", "--registry", name, "--json", "--db", path])
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
        for raw in (payload["rows"] as? [Any]) ?? [] {
            guard let dict = raw as? [String: Any] else { continue }
            out.rows.append(parseFieldFixRow(dict))
        }
        out.dbSha256 = dbManifest(db: path).sha256
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 字段归位分组（**纯逻辑**）：A 类（`missing_field`）一行一组的单组；C 类按 `anomaly_kind`
    /// 分组，固定顺序 `unknown_entry_type / doi_key / placeholder_key / 其他`。
    struct FieldFixGroup: Identifiable {
        var id = ""
        var title = ""
        var rows: [FieldFixRow] = []
        var count: Int { rows.count }
    }

    static func fieldFixGroups(_ rows: [FieldFixRow]) -> [FieldFixGroup] {
        guard !rows.isEmpty else { return [] }
        if rows.allSatisfy({ $0.anomalyKind.isEmpty }) {
            return [FieldFixGroup(id: "missing_field", title: "缺字段归位（按记录）", rows: rows)]
        }
        let fixedOrder = ["unknown_entry_type", "doi_key", "placeholder_key"]
        var groups: [FieldFixGroup] = []
        for kind in fixedOrder {
            let matched = rows.filter { $0.anomalyKind == kind }
            if !matched.isEmpty {
                groups.append(FieldFixGroup(id: kind, title: anomalyTitle(kind), rows: matched))
            }
        }
        let others = rows.filter { !fixedOrder.contains($0.anomalyKind) }
        if !others.isEmpty {
            groups.append(FieldFixGroup(id: "other", title: "其他异常", rows: others))
        }
        return groups
    }

    /// 默认勾选（**纯逻辑**）：每个字段选 `riskOrder` 最小者；同字段多候选取第一个（引擎顺序）。
    static func defaultFieldFixChoices(_ row: FieldFixRow) -> [String: String] {
        var order: [String] = []
        var best: [String: FieldFixCandidate] = [:]
        for candidate in row.candidates where !candidate.field.isEmpty {
            if best[candidate.field] == nil {
                order.append(candidate.field)
                best[candidate.field] = candidate
            } else if candidate.riskOrder < (best[candidate.field]?.riskOrder ?? 3) {
                best[candidate.field] = candidate
            }
        }
        var out: [String: String] = [:]
        for field in order { out[field] = best[field]?.value ?? "" }
        return out
    }

    /// 一行归位的风险说明（**纯逻辑**；视图直接显示）。
    ///   · A 类：`题名候选 4 个（风险 中高），请人工选一个` / `无候选：需人工核原稿`
    ///   · C 类：`entry_type 笔误：rticle → article（中风险）` / `key 异常（DOI/网址当 key）：建议 Lorini2020`
    static func fieldFixRiskText(_ row: FieldFixRow) -> String {
        if !row.anomalyKind.isEmpty {
            let typeCandidates = row.candidates.filter { $0.source == "entry_type" }
            if let best = typeCandidates.min(by: { $0.riskOrder < $1.riskOrder }) {
                return "entry_type 笔误：\(row.entryType) → \(best.value)（\(best.riskText)风险）"
            }
            if !row.keyCandidate.isEmpty {
                let risk: String
                switch row.keyRisk.lowercased() {
                case "low": risk = "低"
                case "medium": risk = "中"
                case "high": risk = "高"
                default: risk = row.keyRisk.isEmpty ? "未知" : row.keyRisk
                }
                return "key 异常（\(anomalyTitle(row.anomalyKind))）：建议 \(row.keyCandidate)（\(risk)风险）"
            }
            return "需人工核对 key / entry_type"
        }
        if row.candidates.isEmpty { return "无候选：需人工核原稿" }
        let labels: [String: String] = ["title": "题名", "year": "年份", "publisher": "出版者"]
        let fields: [String]
        if row.missing.isEmpty {
            fields = Array(Set(row.candidates.map { $0.field })).sorted()
        } else {
            fields = row.missing
        }
        var parts: [String] = []
        for field in fields {
            let label = labels[field] ?? field
            let matched = row.candidates.filter { $0.field == field }
            if matched.isEmpty {
                parts.append("\(label)无候选：需人工核原稿")
                continue
            }
            var risks: [String] = []
            for candidate in matched where !risks.contains(candidate.riskText) {
                risks.append(candidate.riskText)
            }
            let ordered = ["低", "中", "高", "未知"].filter { risks.contains($0) }
            parts.append("\(label)候选 \(matched.count) 个（风险 \(ordered.joined())），请人工选一个")
        }
        return parts.isEmpty ? "无候选：需人工核原稿" : parts.joined(separator: "；")
    }

    /// 用户对一行归位的选择（字段值 + 可选 entry_type / 新 key）。
    struct FieldFixChoice {
        var values: [String: String] = [:]
        var entryType: String?
        var newKey: String?
    }

    /// 选择 → 编辑草稿（**纯逻辑**）：fields = row.fields + choice.values（空值不覆盖），
    /// entryType = choice.entryType ?? row.entryType，newKey 非空才启用改 key。
    static func buildFieldFixDraft(row: FieldFixRow, choice: FieldFixChoice) -> EditorDraft {
        var draft = EditorDraft()
        draft.recordKey = row.key
        draft.entryType = choice.entryType ?? row.entryType
        var fields = row.fields
        for (name, value) in choice.values {
            if value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
            fields[name] = value
        }
        draft.fields = fields
        let newKey = (choice.newKey ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        draft.newKeyEnabled = !newKey.isEmpty
        draft.newKey = newKey
        var note = "字段归位"
        if !row.missing.isEmpty { note += "：补 \(row.missing.joined(separator: "、"))" }
        if choice.entryType != nil { note += "；修 entry_type" }
        if !newKey.isEmpty { note += "；改 key" }
        draft.note = note
        return draft
    }

    /// 应用一条字段归位（复用 `applyEdit`；**同步；后台队列调**）。
    static func applyFieldFix(db: String, row: FieldFixRow, choice: FieldFixChoice,
                              expectDBsha256: String,
                              backupDir: String = BibDbFeature.backupDirectory) -> EditApplyOutcome {
        applyEdit(db: db, draft: buildFieldFixDraft(row: row, choice: choice),
                  expectDBsha256: expectDBsha256, backupDir: backupDir)
    }

    /// 批量应用字段归位的运行摘要（串行逐条写；失败即停、保留已写条目报告）。
    struct FieldFixApplySummary {
        var dbShaBefore = ""
        var writtenKeys: [String] = []
        var failedKey = ""
        var error: String?
        var finalSha = ""
        var backupPaths: [String] = []
        var seconds: Double = 0

        var stopped: Bool { !failedKey.isEmpty }
        var writtenCount: Int { writtenKeys.count }
    }

    /// 批量应用选中条目（**串行**）：第一条 `expect` = 当前库 sha（`dbManifest`），之后每条用
    /// 上一条返回的 `dbSha256After`；任一条失败即停，保留 `writtenKeys` / `failedKey` / 备份路径。
    /// **同步；后台队列调**。
    static func applySelectedFieldFixes(db: String, rows: [FieldFixRow],
                                        choices: [String: FieldFixChoice],
                                        backupDir: String = BibDbFeature.backupDirectory)
        -> FieldFixApplySummary {
        let startedAt = Date()
        var out = FieldFixApplySummary()
        guard let path = existingExplicitDB(db) else {
            out.error = dbPathError(db)
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        let selected = rows.filter { choices[$0.key] != nil }
        guard !selected.isEmpty else {
            out.error = "没有勾选要应用的条目。"
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        var sha = dbManifest(db: path).sha256
        guard !sha.isEmpty else {
            out.error = "读不到库 sha（文件不存在？）：本次未写库。"
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        out.dbShaBefore = sha
        for row in selected {
            guard let choice = choices[row.key] else { continue }
            let result = applyFieldFix(db: path, row: row, choice: choice,
                                       expectDBsha256: sha, backupDir: backupDir)
            if let error = result.error, !result.written {
                out.failedKey = row.key
                out.error = error
                out.finalSha = sha
                out.seconds = Date().timeIntervalSince(startedAt)
                return out
            }
            out.writtenKeys.append(result.newKey.isEmpty ? row.key : result.newKey)
            if !result.backupPath.isEmpty { out.backupPaths.append(result.backupPath) }
            sha = result.dbSha256After.isEmpty
                ? dbManifest(db: path).sha256 : result.dbSha256After
        }
        out.finalSha = sha
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
