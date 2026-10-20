import Foundation

/// 第 55 批：两段式「AI 协同初排」的**纯逻辑**（argv 拼装 / 暂存目录发现 / 建议摘要）。
/// 第 56 批扩展：**多路径回填**（`--coop-replies` 可重复）、剪贴板草稿、已选清单、
/// 第二段 `--clean`、成功摘要读 `state.stats`（旧状态优雅回退）。
///
/// 引擎契约由批 54 冻结（`Engine/slcn/coop.py` + `Engine/convert.py --coop-*`）；
/// 批 56 引擎侧新增：`--coop-replies` 可重复、ph2 支持 `--clean`、state JSON 增 `stats`。
///
///   · 第一段（ph1）：现有参数 + `--verify-refs --no-ai-merge --coop-taskbook-only`。
///     引擎在该模式下**不调用任何 API**，产物只写暂存目录（`<out>/<stem>_待核/`）；
///     **绝不**传 `--verify-plan`（引擎在那个分支打印计划后直接退出，根本走不到写任务书），
///     也**不传** `--llm-*` / `--search-refs`（界面文案同样要说明：外部 chat 零上传/零费用）。
///   · 第二段（ph2）：`--source --out` + **每个已选回填各一个** `--coop-replies`
///     + `--coop-stage [--coop-apply] [--compile] [--clean]`，
///     其余参数（输出名/语种/模板/元数据）由引擎从暂存目录状态**继承**，GUI 不再重复拼。
///
/// 状态文件 `<暂存目录>/_AI协同状态.json` 由引擎写、GUI **只读、不手改**。字段名以
/// `slcn/coop.py: build_state()` 为准：`phase` / `source.path` / `params.out_root` /
/// `params.stem` / `taskbook` / `stage_dir` / `final_dir` / `delivered_to`；
/// 批 56 起还有 `stats`（`bib_total` / `bib_replied` / `bib_pending` /
/// `low_total` / `low_replied` / `low_applied` / `low_pending_manual`，旧状态没有）。
///
/// 本文件只放**纯函数**（可 headless 验证，见 `tools/ai_settings_test/main.swift`）；
/// SwiftUI 只负责取值与显示，不在这里写任何界面逻辑 —— 界面照抄一份判据，迟早会与引擎分叉。
enum CoopFeature {

    /// 本次运行属于两段式的哪一段。`.off` = 普通转换（行为与批 54 逐字节相同）。
    enum Phase: String, Equatable {
        case off
        case first
        case second
    }

    // MARK: - 引擎契约常量（改名必须同步 `slcn/coop.py`）

    /// 暂存目录里的状态文件（`coop.STATE_NAME`；**不**随交付进入最终目录）
    static let stateFileName = "_AI协同状态.json"
    /// 默认暂存目录后缀：`<out>/<stem>_待核/`
    static let stageSuffix = "_待核"
    /// 最终交付目录里的协同记录（`coop.RECORD_NAME`）
    static let recordFileName = "_AI协同记录.md"
    /// 低置信建议清单后缀（`coop.SUGGESTIONS_SUFFIX`，后面补 `.md` / `.jsonl`）
    static let suggestionsSuffix = "_AI建议"

    // MARK: - 路径标准化

    /// 与 `PathKit.normalize` 同一口径：展开 `~`、去首尾空白、标准化、相对路径补 CWD。
    static func normalizePath(_ raw: String) -> String {
        // 空串必须保持空串：`PathKit.normalize("")` 会把空路径补成 CWD（相对路径口径），
        // 而这里「空 = 没填/不传」有含义（coopStage/coopReplies），不能被补成当前目录。
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }
        return PathKit.normalize(trimmed)
    }

    /// 两个路径是否指同一处（两边按同一口径标准化后再比）——
    /// 用于把界面里的源稿路径与引擎写进状态文件的 `source.path` 对上。
    static func samePath(_ a: String, _ b: String) -> Bool {
        guard !a.isEmpty, !b.isEmpty else { return false }
        return normalizePath(a) == normalizePath(b)
    }

    // MARK: - 第一次/第二次运行要传给引擎的参数

    /// ph1 的专属旗标（追加在「现有参数」之后）。
    ///
    /// 三个旗标是引擎契约里第一段的**全部**新增项；这里刻意不接收 `Options` 里的
    /// `--verify-plan` / `--llm-*` / `--search-refs` 字段 —— 第一段不调用 API，
    /// 传了只会让日志与事实不符（引擎虽然会忽略，但「界面承诺 = 引擎真收到的东西」）。
    static func firstPhaseFlags(_ o: ConvertFeature.Options) -> [String] {
        var a = ["--verify-refs", "--no-ai-merge", "--coop-taskbook-only"]
        let stage = normalizePath(o.coopStage)
        if !stage.isEmpty { a += ["--coop-stage", stage] }
        return a
    }

    /// ph2 的完整 argv（引擎允许的**最小集**；其余参数从暂存状态继承）。
    ///
    /// 顺序固定：source / out / 每个回填各一个 --coop-replies（按已选顺序）/
    /// stage / apply / compile / clean。
    ///
    /// 批 56：`--coop-replies` **可重复**——用户真测时 DeepSeek 把回填拆成
    /// `…a89ebb.json`（附一低置信 JSON）+ `…b4a25e.txt`（9 条 BibTeX）两个文件；
    /// 批 55 把多选折叠成父目录（Downloads）既危险又会读进无关文本，本批改为
    /// **每个路径一个 flag**，不再折叠、不复制临时目录。
    ///
    /// `--coop-apply` **不在这里做决定**：调用方必须在运行 ph2 **之前**把它设好
    /// （引擎 phase=done 后同一回填重跑是幂等早退，看到建议再回头应用已经晚了）。
    /// `--clean` 与页面「清理编译中间文件」开关一致（批 56 起 ph2 也尊重它，否则
    /// 交付目录会留下 .aux/.bbl/.bcf/.blg/.out/.run.xml）。
    static func secondPhaseArguments(_ o: ConvertFeature.Options) -> [String] {
        var a: [String] = []
        let source = normalizePath(o.source)
        if !source.isEmpty { a += ["--source", source] }
        let out = normalizePath(o.out)
        if !out.isEmpty { a += ["--out", out] }
        for raw in o.coopRepliesPaths {
            let path = normalizePath(raw)
            if !path.isEmpty { a += ["--coop-replies", path] }
        }
        let stage = normalizePath(o.coopStage)
        if !stage.isEmpty { a += ["--coop-stage", stage] }
        if o.coopApply { a.append("--coop-apply") }
        if o.compile { a.append("--compile") }
        if o.clean { a.append("--clean") }
        return a
    }

    /// 把「界面现取的选项」整理成真正要跑的协同选项：路径标准化 + 指定本次阶段。
    ///
    /// `.off` 时**原样返回**（一个字段都不改）——这是「不勾两段式时 argv 与批 54
    /// 逐字节相同」的第一道保障。协同模式下强制 `useAI/searchRefs = false`：
    /// 两段式第一段不调用 API，第二段更不会调；界面也如实说明这一点。
    static func prepare(_ o: ConvertFeature.Options, phase: Phase) -> ConvertFeature.Options {
        var n = o
        guard phase != .off else { return n }
        n.coopPhase = phase
        n.source = normalizePath(o.source)
        n.out = normalizePath(o.out)
        n.coopRepliesPaths = o.coopRepliesPaths.map { normalizePath($0) }.filter { !$0.isEmpty }
        if !o.coopStage.isEmpty { n.coopStage = normalizePath(o.coopStage) }
        n.useAI = false
        n.searchRefs = false
        return n
    }

    // MARK: - 暂存目录状态（只读 `_AI协同状态.json`）

    /// 一个 ph1 暂存目录的状态摘要（**不是交付目录**；GUI 不许把它当产物目录展示）。
    struct StageState: Equatable {
        var stageDir = ""
        var taskbook = ""
        var finalDir = ""
        var stem = ""
        /// 引擎写的阶段：`ph1` / `ph2` / `done`
        var phase = ""
        /// 交付后才有（`mark_state_done` 写入）
        var deliveredTo = ""
        var sourcePath = ""
        var outRoot = ""
        /// 状态文件的 mtime（秒）；发现多个候选时按它取最近的。
        var stateModified: TimeInterval = 0
        /// 批 56：引擎写入的统计（旧状态没有 → nil，界面走回退口径）。
        var stats: CoopStats? = nil
        /// 旧状态回退用：`keys.bib` 条数 / `replied` 条数 / `keys.low_confidence` 条数 /
        /// `applied_low_confidence`。批 56 之后 state 有 stats，这些只在旧状态用到。
        var legacyBibTotal = 0
        var legacyBibReplied = 0
        var legacyLowTotal = 0
        var legacyAppliedLow = 0

        /// 是否已经完成第二段并交付。
        var delivered: Bool { phase == "done" && !deliveredTo.isEmpty }

        /// 协同记录路径：交付后优先 `delivered_to`，否则 `final_dir`
        /// （未交付时这个文件通常还不存在 —— 界面用 `FileManager` 再判一次）。
        var recordPath: String {
            let dir = deliveredTo.isEmpty ? finalDir : deliveredTo
            return dir.isEmpty ? "" : (dir as NSString).appendingPathComponent(CoopFeature.recordFileName)
        }

        /// `<暂存目录>/<stem>_AI建议.jsonl`（ph2 才生成）
        var suggestionsJSONLPath: String {
            guard !stageDir.isEmpty, !stem.isEmpty else { return "" }
            return (stageDir as NSString)
                .appendingPathComponent(stem + CoopFeature.suggestionsSuffix + ".jsonl")
        }

        /// `<暂存目录>/<stem>_AI建议.md`（ph2 才生成）
        var suggestionsMarkdownPath: String {
            guard !stageDir.isEmpty, !stem.isEmpty else { return "" }
            return (stageDir as NSString)
                .appendingPathComponent(stem + CoopFeature.suggestionsSuffix + ".md")
        }
    }

    /// 读一个暂存目录的状态文件；文件不存在 / 不可读 / JSON 坏 / 没有 `phase` → nil。
    static func loadStage(stageDir rawDir: String) -> StageState? {
        let dir = normalizePath(rawDir)
        guard !dir.isEmpty else { return nil }
        let statePath = (dir as NSString).appendingPathComponent(stateFileName)
        guard let data = FileManager.default.contents(atPath: statePath),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return nil
        }
        return parseState(obj, stageDir: dir, stateModified: modificationTime(statePath))
    }

    /// 从已解析的 JSON 对象构造状态（单独抽出来：测试可以直接喂字典）。
    static func parseState(_ obj: [String: Any], stageDir rawDir: String,
                           stateModified: TimeInterval = 0) -> StageState? {
        guard let phase = obj["phase"] as? String, !phase.isEmpty else { return nil }
        let source = obj["source"] as? [String: Any] ?? [:]
        let params = obj["params"] as? [String: Any] ?? [:]
        var s = StageState()
        s.stageDir = rawDir.isEmpty ? stringValue(obj["stage_dir"]) : normalizePath(rawDir)
        s.taskbook = stringValue(obj["taskbook"])
        s.finalDir = stringValue(obj["final_dir"])
        s.phase = phase
        s.deliveredTo = stringValue(obj["delivered_to"])
        s.sourcePath = stringValue(source["path"])
        s.outRoot = stringValue(params["out_root"])
        s.stateModified = stateModified
        s.stats = CoopStats.fromJSON(obj["stats"])
        let keys = obj["keys"] as? [String: Any] ?? [:]
        s.legacyBibTotal = stringArray(keys["bib"]).count
        s.legacyLowTotal = stringArray(keys["low_confidence"]).count
        let replied = stringArray(obj["replied"])
        // 旧 state 的 `replied` 是**文献键**列表（低置信 ID 另有 pending 记账）：
        // 只把确实落在 bib 白名单里的算「文献回填」，避免把 FN-/META- ID 混进 bib 计数。
        let bibKeys = Set(stringArray(keys["bib"]))
        s.legacyBibReplied = bibKeys.isEmpty
            ? replied.count
            : replied.filter { bibKeys.contains($0) }.count
        s.legacyAppliedLow = intValue(obj["applied_low_confidence"]) ?? 0
        s.stem = stringValue(params["stem"])
        if s.stem.isEmpty && !s.stageDir.isEmpty {
            // 兜底：默认暂存目录名 = `<stem>_待核`
            let name = (s.stageDir as NSString).lastPathComponent
            if name.hasSuffix(stageSuffix) {
                s.stem = String(name.dropLast(stageSuffix.count))
            }
        }
        return s
    }

    private static func stringValue(_ value: Any?) -> String {
        if let s = value as? String { return s }
        if let n = value as? NSNumber { return n.stringValue }
        return ""
    }

    private static func stringArray(_ value: Any?) -> [String] {
        (value as? [Any] ?? []).compactMap { item in
            let s = stringValue(item)
            return s.isEmpty ? nil : s
        }
    }

    private static func intValue(_ value: Any?) -> Int? {
        if let n = value as? NSNumber { return n.intValue }
        if let s = value as? String, let n = Int(s) { return n }
        return nil
    }

    private static func modificationTime(_ path: String) -> TimeInterval {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
              let date = attrs[.modificationDate] as? Date else { return 0 }
        return date.timeIntervalSince1970
    }

    // MARK: - 暂存目录发现

    /// 扫 `<outRoot>/*_待核/_AI协同状态.json`，按 `source.path` 与 `params.out_root`
    /// 匹配出**属于当前源稿**的暂存目录（0 个 / 多个都合法），最近修改的排最前。
    ///
    /// 为什么不按目录名猜：输出名可以由 `--name` / 作者名推导，界面这边算一遍就可能
    /// 与引擎写下的 `stem` 不一致；状态文件里的 `source.path` + `params.out_root` 才是
    /// 引擎自己认的「同一份稿、同一个输出根」。多个候选（例如同一篇改过输出名）时
    /// 取最近修改的那个，并把候选数交给界面提示。
    static func discoverStages(outRoot rawOut: String, source rawSource: String) -> [StageState] {
        let outRoot = normalizePath(rawOut)
        let source = normalizePath(rawSource)
        guard !outRoot.isEmpty, !source.isEmpty else { return [] }
        let names = (try? FileManager.default.contentsOfDirectory(atPath: outRoot)) ?? []
        var found: [StageState] = []
        for name in names.sorted() where name.hasSuffix(stageSuffix) {
            let dir = (outRoot as NSString).appendingPathComponent(name)
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: dir, isDirectory: &isDir),
                  isDir.boolValue else { continue }
            guard let state = loadStage(stageDir: dir) else { continue }
            guard samePath(state.sourcePath, source), samePath(state.outRoot, outRoot) else { continue }
            found.append(state)
        }
        return found.sorted { $0.stateModified > $1.stateModified }
    }

    // MARK: - 建议清单摘要（ph2 之后才有）

    /// `<stem>_AI建议.jsonl` 的逐行统计。引擎在 ph2 里写：每条**通过校验**的字段建议
    /// 一行 `status=accepted`；整条被拒的 `status=rejected`；没回填的 `status=missing`。
    struct SuggestionsSummary: Equatable {
        var accepted = 0
        var rejected = 0
        var missing = 0
        /// 连 JSON 都解析不了的行（理论上不该有；有就说明文件被手工改坏了）
        var malformed = 0

        var total: Int { accepted + rejected + missing }

        /// 一行摘要（界面/日志用）。清单存在但为空（本篇没有低置信项）也要说清楚，
        /// 免得编辑以为「没生成」。
        var summary: String {
            if total == 0 && malformed == 0 {
                return "低置信建议 0 条（本篇没有低置信项；建议清单照常生成）"
            }
            return "建议 \(total) 条：通过校验 \(accepted) / 拒绝 \(rejected) / 待人工 \(missing)"
                + (malformed > 0 ? "（另有 \(malformed) 行无法解析）" : "")
        }
    }

    /// 统计暂存目录里的建议清单；文件不存在（还没跑第二段）→ nil。
    static func suggestionsSummary(_ stage: StageState) -> SuggestionsSummary? {
        suggestionsSummary(path: stage.suggestionsJSONLPath)
    }

    /// 直接按路径统计（测试用；`nil` = 文件不存在/不可读）。
    static func suggestionsSummary(path rawPath: String) -> SuggestionsSummary? {
        let path = normalizePath(rawPath)
        guard !path.isEmpty,
              let text = try? String(contentsOfFile: path, encoding: .utf8) else { return nil }
        var s = SuggestionsSummary()
        for rawLine in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty else { continue }
            guard let data = line.data(using: .utf8),
                  let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let status = obj["status"] as? String else {
                s.malformed += 1
                continue
            }
            switch status {
            case "accepted": s.accepted += 1
            case "rejected": s.rejected += 1
            case "missing": s.missing += 1
            default: s.malformed += 1
            }
        }
        return s
    }

    // MARK: - 一次「刷新」的结果（跑完 ph1/ph2 后由界面消费）

    /// 扫描 + 建议 + 记录 + 统计的合并结果（全部在后台算好，主线程只赋值）。
    struct StageRefresh {
        /// 匹配到的全部候选（最近修改的排最前）
        var stages: [StageState] = []
        /// 界面要显示的那一个（= stages.first）
        var selected: StageState? = nil
        /// 建议摘要；尚未跑第二段时为 nil
        var suggestions: SuggestionsSummary? = nil
        /// 协同记录文件此刻是否真的存在
        var recordExists = false
        /// 交付后的统计摘要（批 56；只有 phase=done 才计算）
        var result: CoopResult? = nil
    }

    /// 跑完一段后刷新界面状态：发现暂存目录 → 读建议摘要 → 看协同记录/统计。
    static func refresh(outRoot: String, source: String) -> StageRefresh {
        var r = StageRefresh()
        r.stages = discoverStages(outRoot: outRoot, source: source)
        r.selected = r.stages.first
        if let stage = r.selected {
            r.suggestions = suggestionsSummary(stage)
            let record = stage.recordPath
            r.recordExists = !record.isEmpty && FileManager.default.fileExists(atPath: record)
            // 批 56：交付成功后把 stats 摘要算好（旧状态没有 stats 时 loadResult 内部回退）。
            if stage.phase == "done" {
                r.result = loadResult(stage)
            }
        }
        return r
    }

    // MARK: - 界面文案（纯函数，便于测试钉住「未交付」口径）

    /// 状态行：**绝不**把 ph1 说成交付。
    static func stageStatusText(_ stage: StageState?) -> String {
        guard let s = stage else { return "尚未初排" }
        switch s.phase {
        case "ph1": return "待人工核实（未交付）"
        case "ph2": return "上次第二段未完成（可重跑第二段）"
        case "done": return s.delivered ? "已交付" : "状态文件显示已完成（未记录交付目录）"
        default: return "状态未知（\(s.phase)）"
        }
    }

    /// 失败时给界面用的「日志尾部」：取最后若干非空行、压掉时间戳前缀、逐行截断。
    /// 失败提示必须有退出码 + 日志尾部（只有一句「失败」等于让编辑去猜）。
    static func logTail(_ text: String, maxLines: Int = 8, maxCharsPerLine: Int = 160) -> String {
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        guard !lines.isEmpty else { return "" }
        return lines.suffix(max(1, maxLines)).map { line -> String in
            let clipped = line.count > maxCharsPerLine
                ? String(line.prefix(maxCharsPerLine)) + "…"
                : line
            // 去掉 Logger 加的 `+  3.2s  ` 前缀（它只对实时日志有意义，回看时是噪声）
            if let range = clipped.range(of: #"^\+\s*[0-9.]+s\s{2}"#, options: .regularExpression) {
                return String(clipped[range.upperBound...])
            }
            return clipped
        }.joined(separator: "\n")
    }
    // MARK: - 批 56：回填已选清单（多文件 + 目录 + 剪贴板，可移除/清空）

    /// 已选的一项回填（文件或目录）。`isDirectory` 只用于界面显示与文案，
    /// 传引擎时两者一视同仁（引擎 `--coop-replies` 对文件/目录都支持）。
    struct ReplyItem: Equatable {
        var path: String
        var isDirectory: Bool

        var displayName: String {
            let name = (path as NSString).lastPathComponent
            return name.isEmpty ? path : name
        }
        /// 界面文案（文件 / 目录）
        var kindText: String { isDirectory ? "目录" : "文件" }
    }

    /// 已选回填的**纯逻辑**列表：可多次导入累加、去重、逐项移除、全部清空。
    ///
    /// 为什么去重：同一路径重复加入不会让引擎多读到什么（引擎对同一真实路径去重），
    /// 只会让「已选 N 项」看起来比实际多；界面据此提示「已在清单里」。
    struct RepliesSelection: Equatable {
        private(set) var items: [ReplyItem] = []

        var count: Int { items.count }
        var isEmpty: Bool { items.isEmpty }
        var paths: [String] { items.map { $0.path } }
        var fileCount: Int { items.filter { !$0.isDirectory }.count }
        var directoryCount: Int { items.filter { $0.isDirectory }.count }
        var clipboardDraftCount: Int { items.filter { CoopFeature.isClipboardDraft($0.path) }.count }

        /// 「已选 N 项（文件 a / 目录 b [ / 剪贴板草稿 k]）」；空清单返回明确提示（不许静默）。
        var summary: String {
            guard !isEmpty else { return "尚未选择任何回填" }
            var text = "已选 \(count) 项（文件 \(fileCount) / 目录 \(directoryCount)"
            if clipboardDraftCount > 0 { text += " / 剪贴板草稿 \(clipboardDraftCount)" }
            return text + "）"
        }

        /// 加入一项；返回 false = 同一路径已在清单里（不重复添加）。
        @discardableResult
        mutating func add(_ item: ReplyItem) -> Bool {
            guard !item.path.isEmpty else { return false }
            guard !items.contains(where: { CoopFeature.samePath($0.path, item.path) }) else {
                return false
            }
            items.append(item)
            return true
        }

        /// 逐项移除（界面每行的「移除」按钮）。返回被移除的项（便于删剪贴板草稿）。
        @discardableResult
        mutating func remove(at index: Int) -> ReplyItem? {
            guard items.indices.contains(index) else { return nil }
            return items.remove(at: index)
        }

        @discardableResult
        mutating func remove(path: String) -> ReplyItem? {
            guard let i = items.firstIndex(where: { CoopFeature.samePath($0.path, path) }) else {
                return nil
            }
            return items.remove(at: i)
        }

        mutating func clear() { items.removeAll() }
    }

    // MARK: - 批 56：剪贴板导入（临时 .md 的命名是纯逻辑，便于 headless 验证）

    /// 剪贴板草稿文件名前缀（移除/清空时据此识别，**不误删用户自己选的文件**）。
    static let clipboardDraftPrefix = "slcn-coop-clipboard-"
    /// 草稿扩展名：引擎 `load_reply_texts` 认 `.md`，编辑在 Finder 里也认得出。
    static let clipboardDraftExtension = "md"

    /// 时间戳格式（文件名用；本地时间、秒级 —— 同秒重复粘贴靠序号区分）。
    static func clipboardTimestamp(_ date: Date = Date()) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter.string(from: date)
    }

    /// 草稿文件名：`slcn-coop-clipboard-<时间戳>-<序号>.md`。
    /// `index` 由界面每次粘贴递增（重复粘贴生成多个文件，互不覆盖）。
    static func clipboardDraftFileName(timestamp: String, index: Int) -> String {
        let stamp = timestamp.isEmpty ? "00000000-000000" : timestamp
        return clipboardDraftPrefix + stamp + "-" + String(max(1, index)) + "." + clipboardDraftExtension
    }

    /// 草稿完整路径（目录通常是 `NSTemporaryDirectory()`；测试传 /tmp 子目录）。
    static func clipboardDraftPath(directory: String, timestamp: String, index: Int) -> String {
        let dir = normalizePath(directory)
        guard !dir.isEmpty else { return clipboardDraftFileName(timestamp: timestamp, index: index) }
        return (dir as NSString).appendingPathComponent(
            clipboardDraftFileName(timestamp: timestamp, index: index))
    }

    /// 这个路径是不是「剪贴板草稿」——移除/清空时据此删临时文件，**不碰用户选的文件**。
    static func isClipboardDraft(_ path: String) -> Bool {
        let name = (path as NSString).lastPathComponent
        return name.hasPrefix(clipboardDraftPrefix) && name.hasSuffix("." + clipboardDraftExtension)
    }

    /// 剪贴板文本是否可导入：nil/空白 → nil（界面给红字提示）；否则原样返回（保留换行）。
    static func clipboardText(_ raw: String?) -> String? {
        guard let raw, !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return raw
    }

    // MARK: - 批 56：第二段成功摘要（读 state.stats；旧状态回退 record/旧 state）

    /// 引擎批 56 写入 `state["stats"]` 的七个计数（旧状态没有 → `loadResult` 回退推算）。
    struct CoopStats: Equatable {
        var bibTotal = 0
        var bibReplied = 0
        var bibPending = 0
        var lowTotal = 0
        var lowReplied = 0
        var lowApplied = 0
        var lowPendingManual = 0
        /// true = 数字来自旧状态/协同记录的回退推算（界面注明「部分计数为估算」）
        var legacy = false

        /// 文献回填 0/N：批 56 用户真测最痛的点（只导入 JSON、丢了 BibTeX 回填）。
        var zeroBibReplies: Bool { bibTotal > 0 && bibReplied == 0 }

        /// `bib_pending` 缺失时用 total - replied 兜底。
        var effectiveBibPending: Int {
            bibPending > 0 || bibTotal == 0 ? bibPending : max(0, bibTotal - bibReplied)
        }

        /// 从 state JSON 的 `stats` 对象解析；对象缺失/字段全缺 → nil（= 旧状态）。
        static func fromJSON(_ value: Any?) -> CoopStats? {
            guard let obj = value as? [String: Any] else { return nil }
            let names = ["bib_total", "bib_replied", "bib_pending", "low_total",
                         "low_replied", "low_applied", "low_pending_manual"]
            guard names.contains(where: { obj[$0] != nil }) else { return nil }
            var s = CoopStats()
            s.bibTotal = CoopFeature.intValue(obj["bib_total"]) ?? 0
            s.bibReplied = CoopFeature.intValue(obj["bib_replied"]) ?? 0
            s.bibPending = CoopFeature.intValue(obj["bib_pending"]) ?? 0
            s.lowTotal = CoopFeature.intValue(obj["low_total"]) ?? 0
            s.lowReplied = CoopFeature.intValue(obj["low_replied"]) ?? 0
            s.lowApplied = CoopFeature.intValue(obj["low_applied"]) ?? 0
            s.lowPendingManual = CoopFeature.intValue(obj["low_pending_manual"]) ?? 0
            return s
        }
    }

    /// 第二段成功后界面上要显示的一段结果（全部来自只读文件）。
    struct CoopResult: Equatable {
        var stats: CoopStats
        /// `reference.bib` 实际并入条数（从 `_AI协同记录.md` 读；读不到时 nil）
        var merged: Int?
        /// 「文献回填 x/y（并入 z 条，未回填 n）｜低置信回复 a（应用 b / 仅记录待人工 c）」
        var summaryLine: String
        /// 文献回填 0/N 时的橙色警告；正常时 nil
        var zeroBibWarning: String?
        /// 「打开建议清单」用（`<stem>_AI建议.md`；暂存目录优先，交付目录兜底）
        var suggestionsMarkdownPath = ""
    }

    /// 0 回填警告的**唯一**文案（界面/日志共用；用户真测就栽在这上面）。
    static let zeroBibRepliesWarning = "本次没有文献条目回填，reference.bib 未并入任何 AI 修订；"
        + "若外部 chat 已返回 bibtex/bib，可能漏导入（可重新初排后再导入全部回填）。"

    /// 把计数格式化成批 56 的固定摘要口径（纯函数，测试直接钉字符串）。
    static func formatResult(_ stats: CoopStats, merged: Int?) -> String {
        let mergedText: String
        if let merged = merged {
            mergedText = "并入 \(merged) 条"
        } else {
            mergedText = "并入条数见协同记录"
        }
        return "文献回填 \(stats.bibReplied)/\(stats.bibTotal)（\(mergedText)，"
            + "未回填 \(stats.effectiveBibPending)）｜低置信回复 \(stats.lowReplied)"
            + "（应用 \(stats.lowApplied) / 仅记录待人工 \(stats.lowPendingManual)）"
    }

    /// 读 state.stats（旧状态回退：协同记录 → 旧 state 字段 → 建议 jsonl），并算摘要/警告。
    static func loadResult(_ stage: StageState) -> CoopResult {
        let record = readText(stage.recordPath)
        var merged: Int?
        if let record = record {
            merged = firstCapture(#"并入：\*\*(\d+)\*\* 条"#, in: record).flatMap { Int($0) }
        }
        let stats = stage.stats ?? fallbackStats(stage, record: record)
        if merged == nil && stats.bibReplied == 0 { merged = 0 }
        return CoopResult(
            stats: stats,
            merged: merged,
            summaryLine: formatResult(stats, merged: merged),
            zeroBibWarning: stats.zeroBibReplies ? zeroBibRepliesWarning : nil,
            suggestionsMarkdownPath: suggestionsMarkdownPath(stage)
        )
    }

    /// 旧状态（没有 stats）的回退口径：能精确读的从协同记录/建议 jsonl 拿，
    /// 拿不到的用旧 state 字段估算，并标 `legacy=true`（界面注明「部分计数为估算」）。
    private static func fallbackStats(_ stage: StageState, record: String?) -> CoopStats {
        var s = CoopStats()
        s.legacy = true
        // ① 文献：协同记录「一、外部 chat 回填」那行是精确的（解析到/拒绝/未回填）。
        if let record = record,
           let replied = firstCapture(#"解析到 (\d+) 条，拒绝 \d+ 条，未回填 \d+ 条"#, in: record)
                            .flatMap({ Int($0) }),
           let rejected = firstCapture(#"解析到 \d+ 条，拒绝 (\d+) 条，未回填 \d+ 条"#, in: record)
                            .flatMap({ Int($0) }),
           let missing = firstCapture(#"解析到 \d+ 条，拒绝 \d+ 条，未回填 (\d+) 条"#, in: record)
                            .flatMap({ Int($0) }) {
            s.bibTotal = replied + rejected + missing
            s.bibReplied = replied
            s.bibPending = missing
        } else {
            s.bibTotal = stage.legacyBibTotal
            s.bibReplied = min(stage.legacyBibReplied, stage.legacyBibTotal)
            s.bibPending = max(0, s.bibTotal - s.bibReplied)
        }
        // ② 低置信：有建议 jsonl 时 total = 清单里的 ID 数（引擎对每个低置信项都写行）。
        s.lowTotal = stage.legacyLowTotal
        if let ids = suggestionIDs(path: stage.suggestionsJSONLPath) {
            s.lowTotal = ids.allCount
            s.lowReplied = ids.acceptedCount
        }
        s.lowApplied = stage.legacyAppliedLow
        // 旧状态没有 low_pending_manual：未应用的按「仅记录待人工」估算（界面会标估算）。
        s.lowPendingManual = max(0, s.lowTotal - s.lowApplied)
        return s
    }

    private struct SuggestionIDs {
        var acceptedCount = 0
        var allCount = 0
    }

    /// 建议 jsonl 里的「低置信 ID」统计（旧状态回退用；新状态直接用 stats）。
    private static func suggestionIDs(path rawPath: String) -> SuggestionIDs? {
        let path = normalizePath(rawPath)
        guard !path.isEmpty,
              let text = try? String(contentsOfFile: path, encoding: .utf8) else { return nil }
        var seen = Set<String>()
        var accepted = Set<String>()
        for rawLine in text.split(separator: "\n", omittingEmptySubsequences: true) {
            guard let data = rawLine.data(using: .utf8),
                  let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let id = obj["id"] as? String, !id.isEmpty else { continue }
            seen.insert(id)
            if (obj["status"] as? String) == "accepted" { accepted.insert(id) }
        }
        return SuggestionIDs(acceptedCount: accepted.count, allCount: seen.count)
    }

    /// 建议清单 md 的打开路径：暂存目录优先（引擎交付前就写在这里），交付目录兜底。
    static func suggestionsMarkdownPath(_ stage: StageState) -> String {
        let stagePath = stage.suggestionsMarkdownPath
        if !stagePath.isEmpty && FileManager.default.fileExists(atPath: stagePath) {
            return stagePath
        }
        let finalDir = stage.deliveredTo.isEmpty ? stage.finalDir : stage.deliveredTo
        guard !finalDir.isEmpty, !stage.stem.isEmpty else { return stagePath }
        let fallback = (finalDir as NSString)
            .appendingPathComponent(stage.stem + suggestionsSuffix + ".md")
        return FileManager.default.fileExists(atPath: fallback) ? fallback : stagePath
    }

    /// 第二段能不能跑（界面按钮 disabled 的唯一判据；纯函数便于测试）。
    static func canRunSecondPhase(replyCount: Int, stage: StageState?) -> Bool {
        guard let stage = stage else { return false }
        return replyCount > 0 && stage.phase != "done"
    }

    /// 第二段按钮禁用时的明确提示（nil = 可以跑）。
    static func secondPhaseDisabledHint(replyCount: Int, stage: StageState?) -> String? {
        if stage == nil { return "还没有可续跑的暂存目录：请先运行第一段。" }
        if stage?.phase == "done" { return "已交付：若要改回填内容请重新初排第一段。" }
        if replyCount <= 0 {
            return "尚未选择任何回填：请先「添加文件…」「添加目录…」或「从剪贴板导入」。"
        }
        return nil
    }

    private static func readText(_ path: String) -> String? {
        let p = normalizePath(path)
        guard !p.isEmpty else { return nil }
        return try? String(contentsOfFile: p, encoding: .utf8)
    }

    /// 正则第一个捕获组（Foundation 的 range(of:options:) 不带捕获组，这里用 NSRegularExpression）。
    private static func firstCapture(_ pattern: String, in text: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return nil }
        let ns = text as NSString
        guard let m = re.firstMatch(in: text, options: [],
                                    range: NSRange(location: 0, length: ns.length)),
              m.numberOfRanges > 1 else { return nil }
        let r = m.range(at: 1)
        return r.location == NSNotFound ? nil : ns.substring(with: r)
    }

}

// 批63b 发布修复 R2：把「阶段 == "done"」的字符串比较放在 feature 层。
// 旧 Xcode 对 String 相等比较的**首次**重载解析要花 ~6ms，且会被算进首个使用它的函数；
// 放进 CoopFeature 后，ConvertTab 的 View 函数不再承担这笔一次性开销。
extension CoopFeature.StageState {
    var isDelivered: Bool { phase == "done" }
}
