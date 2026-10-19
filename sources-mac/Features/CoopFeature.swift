import Foundation

/// 第 55 批：两段式「AI 协同初排」的**纯逻辑**（argv 拼装 / 暂存目录发现 / 建议摘要）。
///
/// 引擎契约由批 54 冻结（`Engine/slcn/coop.py` + `Engine/convert.py --coop-*`），**本批不改引擎**：
///
///   · 第一段（ph1）：现有参数 + `--verify-refs --no-ai-merge --coop-taskbook-only`。
///     引擎在该模式下**不调用任何 API**，产物只写暂存目录（`<out>/<stem>_待核/`）；
///     **绝不**传 `--verify-plan`（引擎在那个分支打印计划后直接退出，根本走不到写任务书），
///     也**不传** `--llm-*` / `--search-refs`（界面文案同样要说明：外部 chat 零上传/零费用）。
///   · 第二段（ph2）：`--source --out --coop-replies --coop-stage [--coop-apply] [--compile]`，
///     其余参数（输出名/语种/模板/元数据）由引擎从暂存目录状态**继承**，GUI 不再重复拼。
///
/// 状态文件 `<暂存目录>/_AI协同状态.json` 由引擎写、GUI **只读、不手改**。字段名以
/// `slcn/coop.py: build_state()` 为准：`phase` / `source.path` / `params.out_root` /
/// `params.stem` / `taskbook` / `stage_dir` / `final_dir` / `delivered_to`。
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
    /// 顺序固定：source / out / replies / stage / apply / compile。
    /// `--coop-apply` **不在这里做决定**：调用方必须在运行 ph2 **之前**把它设好
    /// （引擎 phase=done 后同一回填重跑是幂等早退，看到建议再回头应用已经晚了）。
    static func secondPhaseArguments(_ o: ConvertFeature.Options) -> [String] {
        var a: [String] = []
        let source = normalizePath(o.source)
        if !source.isEmpty { a += ["--source", source] }
        let out = normalizePath(o.out)
        if !out.isEmpty { a += ["--out", out] }
        let replies = normalizePath(o.coopReplies)
        if !replies.isEmpty { a += ["--coop-replies", replies] }
        let stage = normalizePath(o.coopStage)
        if !stage.isEmpty { a += ["--coop-stage", stage] }
        if o.coopApply { a.append("--coop-apply") }
        if o.compile { a.append("--compile") }
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
        if !o.coopReplies.isEmpty { n.coopReplies = normalizePath(o.coopReplies) }
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

    /// 扫描 + 建议 + 记录三件事的合并结果（全部在后台算好，主线程只赋值）。
    struct StageRefresh {
        /// 匹配到的全部候选（最近修改的排最前）
        var stages: [StageState] = []
        /// 界面要显示的那一个（= stages.first）
        var selected: StageState? = nil
        /// 建议摘要；尚未跑第二段时为 nil
        var suggestions: SuggestionsSummary? = nil
        /// 协同记录文件此刻是否真的存在
        var recordExists = false
    }

    /// 跑完一段后刷新界面状态：发现暂存目录 → 读建议摘要 → 看协同记录在不在。
    static func refresh(outRoot: String, source: String) -> StageRefresh {
        var r = StageRefresh()
        r.stages = discoverStages(outRoot: outRoot, source: source)
        r.selected = r.stages.first
        if let stage = r.selected {
            r.suggestions = suggestionsSummary(stage)
            let record = stage.recordPath
            r.recordExists = !record.isEmpty && FileManager.default.fileExists(atPath: record)
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
}
