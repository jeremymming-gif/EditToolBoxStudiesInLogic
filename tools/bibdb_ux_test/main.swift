// B-143（批76）+ B-188（GUI Wave2）引用库纯逻辑测试台（headless）。
//
// **编译的是生产源码本身**：`Core/Support.swift`（EngineLocator/PathKit）+ `Core/Log.swift`
// + `Features/BibDbFeature.swift`。不开 GUI、不起子进程、不读真实库/稿件仓、不联网 ——
// 只断言抽出来的纯逻辑：默认过滤 / 开关 / 计数 / 空态 / warning 明细分类顺序 /
// 跳转动作标识 / lint JSON 向后兼容解析，以及 B-188 新增的
// ConflictListState/ReviewCacheState 缓存语义、lint records / 库健康 / 待处理状态条、
// 已处理留痕（processedEntries/EditTrace）、编辑器与合并校验、suggestedMergeKey/
// mergeFieldRows、字段归位（groups/defaults/risk/buildDraft）、isStale 新鲜度。
// B-190：(o) 段是 git 版本层纯逻辑/源码接线（commit message、命令构造、status/log 解析、
// fail-soft 文案、只 add 库文件、无自动 push）。
// B-201：(k) 段是合并建议 key / 校验的新语义（新 key 可沿用被合并键之一，不再追加 Merged）。
// B-203：(o2) 段是推送预检（parsePushLog/isBibOnly/planPush、只推 bib-only 前缀、代理重试、
// refspec 无 force）。
// B-196：(p) 段是「待处理」页新鲜度四态 reviewFreshness（stale 时视图临时禁用写库入口；
// 判定口径与 isStale 同一份）。
// B-205：(o3) 段是重复/近重复「确认不同（不再提示）」：引擎 argv（--apply --json）、
// JSON 解析、sidecar 的 git 命令（pathspec 只该文件、无 force/无 push）、视图接线。
// B-209：(q) 段是「外部 bib → 规范 bib」只读预览：normalize-bib JSON 解析、日志隐私、
// 文件/粘贴 argv 与临时文件清理、入藏页入口/sheet/送入 dry-run 的源码接线。
// (f) 段是**生产源码接线断言**（按 /tmp/b188-gui/CONTRACT.md 与 BRIEF §5 的目标态）：
// 视图层（SwiftUI）本身不在本台编译，由 Lead 的 typecheck/build/走查覆盖。
//
// 跑法（在 sources-mac 下）：
//     swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/buxmc \
//       -o /tmp/bibdb_ux_test Core/Support.swift Core/Log.swift Features/BibDbFeature.swift \
//       Features/BibDbFeature+Import.swift Features/BibDbGit.swift \
//       ../tools/bibdb_ux_test/main.swift
//     /tmp/bibdb_ux_test        # 期望：SUMMARY: N PASS / 0 FAIL
//
// 或直接跑总入口：bash tools/run_swift_tests.sh

import Foundation

var pass = 0
var fail = 0

func check(_ name: String, _ ok: Bool, _ detail: String = "") {
    if ok {
        pass += 1
        print("PASS  \(name)" + (detail.isEmpty ? "" : "  \(detail)"))
    } else {
        fail += 1
        print("FAIL  \(name)  \(detail)")
    }
}

let here = URL(fileURLWithPath: #filePath)
let toolbox = here.deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent()
let sources = toolbox.appendingPathComponent("sources-mac")

func readSource(_ relative: String) -> String {
    (try? String(contentsOf: sources.appendingPathComponent(relative), encoding: .utf8)) ?? ""
}

// ── 夹具 ─────────────────────────────────────────────────────────────────
func makeItem(_ no: Int, pending: Bool, issue: String = "2023年第1期(68)",
              slug: String = "01 x", verified: Bool = false)
    -> BibDbFeature.ConflictItem {
    var item = BibDbFeature.ConflictItem()
    item.stableNo = no
    item.recordKey = "Key\(no)"
    item.title = "Title \(no)"
    item.issue = issue
    item.slug = slug
    item.pending = pending
    item.verified = verified
    if !pending {
        item.resolution = "adopted"
        item.resolvedAt = "2026-10-18T02:00:00Z"
    }
    return item
}

func makeOutcome(_ items: [BibDbFeature.ConflictItem]) -> BibDbFeature.ConflictsOutcome {
    var out = BibDbFeature.ConflictsOutcome()
    out.items = items
    out.pending = items.filter { $0.pending }.count
    out.resolved = items.count - out.pending
    out.total = items.count
    out.dbSha256 = "0123456789abcdef0123456789abcdef"
    return out
}

func makeWarningItem(code: String, registry: String, detail: String, action: String,
                     count: Int, keys: [String] = [], records: [String] = [],
                     pendingRecords: Int = 0,
                     pendingTotal: Int = 0, resolvedRecords: Int = 0,
                     resolvedTotal: Int = 0) -> BibDbFeature.LintWarningItem {
    var item = BibDbFeature.LintWarningItem()
    item.code = code
    item.registry = registry
    item.detail = detail
    item.action = action
    item.count = count
    item.keysSample = keys
    item.records = records
    item.pendingRecords = pendingRecords
    item.pendingTotal = pendingTotal
    item.resolvedRecords = resolvedRecords
    item.resolvedTotal = resolvedTotal
    return item
}

// ── B-188 夹具（纯内存构造；不读库/不起子进程）────────────────────────────

func makeLint(_ items: [BibDbFeature.LintWarningItem],
              warningCount: Int? = nil) -> BibDbFeature.Lint {
    var lint = BibDbFeature.Lint()
    lint.warningItems = items
    lint.warningCount = warningCount ?? items.count
    lint.warnings = items.map { $0.detail }
    return lint
}

/// 已裁决冲突（默认带 title/year 两处 diff；`resolution` 中文由 ConflictItem 翻）。
func makeResolvedConflict(no: Int, resolution: String = "adopted",
                          resolvedAt: String = "2026-10-18T02:00:00Z",
                          fields: [String] = ["title", "year"])
    -> BibDbFeature.ConflictItem {
    var item = makeItem(no, pending: false)
    item.resolution = resolution
    item.resolvedAt = resolvedAt
    item.diff = fields.map { name in
        var diff = BibDbFeature.ConflictFieldDiff()
        diff.name = name
        diff.old = "old-\(name)"
        diff.new = "new-\(name)"
        return diff
    }
    return item
}

func makeEditTrace(recordKey: String, action: String, at: String,
                   note: String = "", oldKey: String = "", newKey: String = "",
                   changedFields: [(String, String, String)] = [],
                   mergedKeys: [String] = [],
                   backupSha256: String = "") -> BibDbFeature.EditTrace {
    var trace = BibDbFeature.EditTrace()
    trace.recordKey = recordKey
    trace.action = action
    trace.at = at
    trace.note = note
    trace.oldKey = oldKey
    trace.newKey = newKey
    trace.changedFields = changedFields.map { (name, old, new) in
        var change = BibDbFeature.EditTraceFieldChange()
        change.name = name
        change.old = old
        change.new = new
        return change
    }
    trace.mergedKeys = mergedKeys
    trace.backupSha256 = backupSha256
    return trace
}

func makeMergeRecord(key: String, entryType: String = "article",
                     fields: [String: String] = [:],
                     aliases: [String] = []) -> BibDbFeature.MergeRecord {
    var record = BibDbFeature.MergeRecord()
    record.key = key
    record.entryType = entryType
    record.fields = fields
    record.aliases = aliases
    return record
}

func makeCandidate(field: String, value: String, source: String = "",
                   risk: String) -> BibDbFeature.FieldFixCandidate {
    var candidate = BibDbFeature.FieldFixCandidate()
    candidate.field = field
    candidate.value = value
    candidate.source = source
    candidate.risk = risk
    switch risk {
    case "low": candidate.riskOrder = 0
    case "medium": candidate.riskOrder = 1
    case "high": candidate.riskOrder = 2
    default: candidate.riskOrder = 3
    }
    return candidate
}

func makeFieldFixRow(key: String, entryType: String = "article",
                     missing: [String] = [], fields: [String: String] = [:],
                     candidates: [BibDbFeature.FieldFixCandidate] = [],
                     issues: [String] = [], anomalyKind: String = "",
                     keyCandidate: String = "", keyRisk: String = "")
    -> BibDbFeature.FieldFixRow {
    var row = BibDbFeature.FieldFixRow()
    row.key = key
    row.entryType = entryType
    row.missing = missing
    row.fields = fields
    row.candidates = candidates
    row.issues = issues
    row.anomalyKind = anomalyKind
    row.keyCandidate = keyCandidate
    row.keyRisk = keyRisk
    return row
}

func makeManifest(path: String = "/tmp/b188-gui/bibliography.jsonl",
                  exists: Bool = true, size: Int64 = 6_000_000,
                  mtime: TimeInterval = 1_700_000_000,
                  sha256: String = String(repeating: "a", count: 64))
    -> BibDbFeature.DBManifest {
    var manifest = BibDbFeature.DBManifest()
    manifest.path = path
    manifest.exists = exists
    manifest.size = size
    manifest.mtime = mtime
    manifest.sha256 = sha256
    return manifest
}

func makeEditorDraft(recordKey: String = "K1", entryType: String = "article",
                     fields: [String: String] = ["title": "T"],
                     newKeyEnabled: Bool = false, newKey: String = "")
    -> BibDbFeature.EditorDraft {
    var draft = BibDbFeature.EditorDraft()
    draft.recordKey = recordKey
    draft.entryType = entryType
    draft.fields = fields
    draft.newKeyEnabled = newKeyEnabled
    draft.newKey = newKey
    return draft
}

// ── (a) 待裁决页默认过滤 / 开关 / 计数 ───────────────────────────────────
print("── (a) 默认过滤与计数 ──")
let mixed = makeOutcome([makeItem(1, pending: true),
                         makeItem(2, pending: false),
                         makeItem(3, pending: true)])
let filtered = BibDbFeature.ConflictListState(outcome: mixed)
check("(a) 默认只列 pending（2 条，不是全部 3 条）",
      filtered.visibleItems.map { $0.stableNo } == [1, 3],
      "\(filtered.visibleItems.map { $0.stableNo })")
check("(a) 已裁决默认隐藏（C2 不在默认列表）",
      !filtered.visibleItems.contains { $0.stableNo == 2 })
check("(a) 打开开关后显示全部（3 条，含已裁决灰显）",
      BibDbFeature.ConflictListState(outcome: mixed, showResolved: true)
          .visibleItems.map { $0.stableNo } == [1, 2, 3])
check("(a) 过滤不重排：C 编号沿用引擎 stable_no（1、3 保持原号）",
      filtered.visibleItems.map { $0.stableLabel } == ["C001", "C003"])
check("(a) 状态条口径「待裁决 2 ｜ 已裁决 1 ｜ 共 3」",
      filtered.statusText == "待裁决 2 ｜ 已裁决 1 ｜ 共 3", filtered.statusText)
check("(a) 分组头「待裁决 N（共 M）」不再用 N / M 比例写法",
      BibDbFeature.ConflictListState.groupCountText(pending: 2, total: 3)
          == "待裁决 2（共 3）"
      && !filtered.visibleGroups.isEmpty,
      BibDbFeature.ConflictListState.groupCountText(pending: 2, total: 3))
check("(a) 默认过滤下按时/篇分组（同一期同篇合为一组）",
      filtered.visibleGroups.count == 1 && filtered.visibleGroups[0].items.count == 2,
      "\(filtered.visibleGroups.count) 组")
let twoGroups = makeOutcome([makeItem(1, pending: true),
                             makeItem(2, pending: true, slug: "02 y")])
check("(a) 不同篇分到不同组（过滤后仍按期→篇顺序）",
      BibDbFeature.ConflictListState(outcome: twoGroups).visibleGroups.count == 2)
// Lead 复审修正（spec §2.2）：分组头的 M = 该组全部条目数（含默认隐藏的已裁决），
// 不能用 visibleGroups 过滤后条数——否则混合组会错显「待裁决 1（共 1）」。
let mixedGroup = makeOutcome([makeItem(1, pending: true),
                              makeItem(2, pending: false),
                              makeItem(3, pending: false)])
let mixedState = BibDbFeature.ConflictListState(outcome: mixedGroup)
let mixedVisibleGroup = mixedState.visibleGroups.first
check("(a) 默认关：混合组只可见 1 条 pending，但 groupTotal 仍是 3（含 2 条已裁决）",
      mixedState.visibleGroups.count == 1
      && mixedVisibleGroup?.items.count == 1
      && mixedVisibleGroup.map { mixedState.groupTotal(forGroupId: $0.id) } == 3,
      "visible=\(mixedVisibleGroup?.items.count ?? -1) "
        + "total=\(mixedVisibleGroup.map { mixedState.groupTotal(forGroupId: $0.id) } ?? -1)")
check("(a) 默认关：分组头「待裁决 1（共 3）」不是「待裁决 1（共 1）」",
      mixedVisibleGroup.map {
          BibDbFeature.ConflictListState.groupCountText(
              pending: $0.pendingCount,
              total: mixedState.groupTotal(forGroupId: $0.id))
      } == "待裁决 1（共 3）",
      mixedVisibleGroup.map {
          BibDbFeature.ConflictListState.groupCountText(
              pending: $0.pendingCount,
              total: mixedState.groupTotal(forGroupId: $0.id))
      } ?? "")
let mixedOpen = BibDbFeature.ConflictListState(outcome: mixedGroup, showResolved: true)
check("(a) 开关打开：同组 3 条都在，分组头仍是「待裁决 1（共 3）」",
      mixedOpen.visibleItems.count == 3
      && mixedOpen.visibleGroups.count == 1
      && mixedOpen.visibleGroups.first.map {
          BibDbFeature.ConflictListState.groupCountText(
              pending: $0.pendingCount,
              total: mixedOpen.groupTotal(forGroupId: $0.id))
      } == "待裁决 1（共 3）")
check("(a) 开关文案写明「审计留痕，默认关」",
      BibDbFeature.ConflictListState.toggleTitle.contains("审计留痕")
      && BibDbFeature.ConflictListState.toggleTitle.contains("默认关"),
      BibDbFeature.ConflictListState.toggleTitle)

// ── (b) 空态文案 ─────────────────────────────────────────────────────────
print("\n── (b) 空态 ──")
let allResolved = makeOutcome([makeItem(1, pending: false), makeItem(2, pending: false)])
let emptyDefault = BibDbFeature.ConflictListState(outcome: allResolved)
check("(b) 当前真实数据形态（pending=0/已裁决>0）：默认列表空、命中空态",
      emptyDefault.visibleItems.isEmpty && emptyDefault.isEmptyByFilter)
check("(b) 空态文案讲清「没有待裁决」+「历史已裁决 N 条」+「可开关查看」",
      emptyDefault.emptyText.contains("当前没有待裁决冲突")
      && emptyDefault.emptyText.contains("历史已裁决 2 条")
      && emptyDefault.emptyText.contains("显示已裁决"), emptyDefault.emptyText)
check("(b) 打开开关后不显示空态（历史已裁决可见）",
      !BibDbFeature.ConflictListState(outcome: allResolved, showResolved: true)
          .isEmptyByFilter
      && BibDbFeature.ConflictListState(outcome: allResolved, showResolved: true)
          .emptyText.isEmpty)
let none = makeOutcome([])
check("(b) 库里根本没有冲突：空态文案是「当前库没有字段冲突」",
      BibDbFeature.ConflictListState(outcome: none).emptyText
          == "当前库没有字段冲突（待裁决 0 / 已裁决 0）。",
      BibDbFeature.ConflictListState(outcome: none).emptyText)
check("(b) feedback 说明编号固定 + 已裁决默认隐藏",
      emptyDefault.feedbackText.contains("待裁决 0 / 已裁决 2")
      && emptyDefault.feedbackText.contains("C 编号固定")
      && emptyDefault.feedbackText.contains("默认隐藏"), emptyDefault.feedbackText)
check("(b) 开关键打开时 feedback 明说「正在显示已裁决」",
      BibDbFeature.ConflictListState(outcome: allResolved, showResolved: true)
          .feedbackText.contains("正在显示已裁决"))

// ── (c) warning 明细分类顺序 / 计数 / 冲突拆分 / 跳转动作 ────────────────
print("\n── (c) warning 明细分类与跳转 ──")
let warnings: [BibDbFeature.LintWarningItem] = [
    makeWarningItem(code: "unverified", registry: "unverified", detail: "未核实条目 3 条",
                    action: "按口径核实", count: 3),
    makeWarningItem(code: "conflict_pending", registry: "conflicts",
                    detail: "字段冲突：待裁决 0 条 / 历史已裁决 181 条记录（203 处差异，审计留痕，无需处理）",
                    action: "无需处理（历史已裁决留痕）", count: 0,
                    pendingRecords: 0, pendingTotal: 0,
                    resolvedRecords: 181, resolvedTotal: 203),
    makeWarningItem(code: "duplicate_content", registry: "duplicates",
                    detail: "内容完全相同的多条记录：A、B",
                    action: "查库确认；重复记录合并属 P6 另卡，本页只报告、不自动合并",
                    count: 2, keys: ["A", "B"]),
    makeWarningItem(code: "unknown_entry_type", registry: "unknown_entry_type",
                    detail: "不常见的 entry_type `rticle`：1 条", action: "改正 entry_type",
                    count: 1),
    makeWarningItem(code: "missing_title", registry: "missing_field",
                    detail: "缺 title 的条目 219 条", action: "补齐 title", count: 219),
    makeWarningItem(code: "near_duplicates", registry: "near_duplicates",
                    detail: "同一作品的多条记录（不同 key）2 对", action: "P6 另卡", count: 2),
    makeWarningItem(code: "alias_duplicate", registry: "alias",
                    detail: "X：aliases 里有重复项", action: "去重", count: 1),
    makeWarningItem(code: "other_x", registry: "future_registry",
                    detail: "未来新增分类", action: "看完整清单", count: 1),
]
let categories = BibDbFeature.LintWarningDigest.categories(items: warnings)
let expectedOrder = ["duplicates", "near_duplicates", "conflicts", "missing_field",
                     "unknown_entry_type", "alias", "unverified", "other"]
check("(c) 分类顺序固定 = 重复记录/同一作品/字段冲突/缺字段/entry_type/alias/未核实/其他",
      categories.map { $0.registry } == expectedOrder,
      "\(categories.map { $0.registry })")
check("(c) 未来新增 registry 归入「其他」，不丢",
      categories.last?.items.contains { $0.code == "other_x" } == true)
check("(c) 分类计数只数提示条数（缺 title 219 条是 1 条 warning）",
      categories.first { $0.registry == "missing_field" }?.warningCount == 1
      && categories.first { $0.registry == "missing_field" }?.affectedCount == 219)
check("(c) 重复类分类说明含「P6 另卡、本页只报告、不合并」",
      categories.first { $0.registry == "duplicates" }?.note?.contains("P6 另卡") == true
      && categories.first { $0.registry == "duplicates" }?.note?.contains("不合并") == true,
      categories.first { $0.registry == "duplicates" }?.note ?? "")
check("(c) 字段冲突分类说明「两个数据源」（待裁决页 vs 库健康检查）",
      categories.first { $0.registry == "conflicts" }?.note?.contains("两个数据源") == true)
let conflictItem = warnings.first { $0.registry == "conflicts" } ?? warnings[0]
check("(c) 字段冲突明细「待裁决 0 / 历史已裁决 181（203 处差异）」",
      BibDbFeature.LintWarningDigest.conflictsSummary(conflictItem)
          == "待裁决 0 条记录 / 0 处差异 ｜ 历史已裁决 181 条记录 / 203 处差异",
      BibDbFeature.LintWarningDigest.conflictsSummary(conflictItem))
check("(c) 分类把未知 registry 归「其他」后总数仍守恒",
      categories.reduce(0) { $0 + $1.items.count } == warnings.count)
let jumps = BibDbFeature.reviewJumps(pending: 0, resolved: 203)
check("(c) 跳转动作标识：pending → 开关关、resolved → 开关开",
      jumps.map { $0.id } == ["pending", "resolved"]
      && jumps.map { $0.showResolved } == [false, true],
      "\(jumps.map { $0.id }) \(jumps.map { $0.showResolved })")
check("(c) 跳转按钮标题带条数（看待裁决 0 / 看历史已裁决 203）",
      jumps[0].title.contains("0") && jumps[1].title.contains("203"),
      "\(jumps.map { $0.title })")

// ── (d) 旧引擎回退：warning_items 缺失时按老 warnings 字符串分类 ─────────
print("\n── (d) 旧引擎回退（老 warnings 字符串）──")
let legacy = [
    "内容完全相同的多条记录：1-24、t15",
    "同一作品的多条记录（不同 key）2 对：A≈B；C≈D（经典文献…）",
    "字段冲突待裁决：181 条记录 / 203 处差异（例：X（1 条））",
    "缺 title 的条目 219 条",
    "缺 author/editor 的条目 14 条",
    "缺可用年份的条目 152 条",
    "不常见的 entry_type `rticle`：1 条（可能是笔误，见 bibcheck 的类型表）",
    "alias `x` 指向多条记录（a、b）：按 alias 查询会标 ambiguous",
    "未核实条目 3 条（查询结果会标 verified=false）",
]
let fallback = BibDbFeature.LintWarningDigest.categories(items: [], legacyWarnings: legacy)
check("(d) 旧引擎回退：9 条字符串全部有分类（不丢条目）",
      fallback.reduce(0) { $0 + $1.items.count } == legacy.count,
      "\(fallback.reduce(0) { $0 + $1.items.count })")
check("(d) 旧引擎回退：分类顺序仍按固定顺序",
      fallback.map { $0.registry } == ["duplicates", "near_duplicates", "conflicts",
                                       "missing_field", "unknown_entry_type", "alias",
                                       "unverified"],
      "\(fallback.map { $0.registry })")
check("(d) 旧引擎回退：重复记录按「、」数出 2 条（不是把 key 里的 1 当计数）",
      fallback.first { $0.registry == "duplicates" }?.items.first?.count == 2,
      "\(fallback.first { $0.registry == "duplicates" }?.items.first?.count ?? -1)")
check("(d) 旧引擎回退：缺 title 计数 219 解析正确",
      fallback.first { $0.registry == "missing_field" }?.items
          .first { $0.code == "missing_title" }?.count == 219)
check("(d) 旧引擎回退：字段冲突项 action 指向待裁决页",
      fallback.first { $0.registry == "conflicts" }?.items.first?.action.contains("待裁决") == true)

// ── (e) lint --json 解析向后兼容（老键/新键）────────────────────────────
print("\n── (e) lint JSON 解析兼容 ──")
let oldPayload: [String: Any] = [
    "ok": true,
    "errors": [] as [Any],
    "warnings": ["字段冲突待裁决：1 条记录 / 2 处差异（例：X）", "缺 title 的条目 3 条"],
    "counts": [
        "errors": 0,
        "warnings": 2,
        "near_duplicates": 2,
        "conflicts": ["records": 1, "total": 2],
    ] as [String: Any],
]
let parsedOld = BibDbFeature.parseLint(oldPayload)
check("(e) 旧引擎 JSON：老字段照旧解析（warnings/计数/同作品对数）",
      parsedOld.warningCount == 2 && parsedOld.warnings.count == 2
      && parsedOld.nearDuplicates == 2)
check("(e) 旧引擎 JSON：没有 warning_items → 明确标记（GUI 回退老字符串）",
      parsedOld.warningItems.isEmpty)
check("(e) 旧引擎 JSON：没有 conflicts_pending → hasConflictSplit=false（不误报待裁决）",
      !parsedOld.hasConflictSplit && parsedOld.conflictsPendingRecords == 0)
check("(e) 旧引擎 JSON：老 counts.conflicts 不再被当成待裁决数",
      parsedOld.conflictsPendingTotal == 0)
let oldFallbackCategories = BibDbFeature.LintWarningDigest.categories(
    items: parsedOld.warningItems, legacyWarnings: parsedOld.warnings)
check("(e) 旧引擎 JSON：GUI 仍能按老字符串下钻分类",
      oldFallbackCategories.map { $0.registry } == ["conflicts", "missing_field"],
      "\(oldFallbackCategories.map { $0.registry })")

let newPayload: [String: Any] = [
    "ok": true,
    "errors": [] as [Any],
    "warnings": ["字段冲突：待裁决 0 条 / 历史已裁决 181 条记录"
                 + "（203 处差异，审计留痕，无需处理）"],
    "warning_items": [[
        "code": "conflict_pending",
        "registry": "conflicts",
        "detail": "字段冲突：待裁决 0 条 / 历史已裁决 181 条记录"
                  + "（203 处差异，审计留痕，无需处理）",
        "action": "无需处理（历史已裁决留痕；可在「待裁决」页打开「显示已裁决」查看）",
        "count": 0,
        "keys_sample": [] as [Any],
        "pending_records": 0,
        "pending_total": 0,
        "resolved_records": 181,
        "resolved_total": 203,
    ] as [String: Any]],
    "counts": [
        "errors": 0,
        "warnings": 1,
        "near_duplicates": 2,
        "conflicts": ["records": 181, "total": 203],
        "conflicts_pending": ["records": 0, "total": 0],
        "conflicts_resolved": ["records": 181, "total": 203],
    ] as [String: Any],
]
let parsedNew = BibDbFeature.parseLint(newPayload)
check("(e) 新引擎 JSON：warning_items 解析出结构化项",
      parsedNew.warningItems.count == 1
      && parsedNew.warningItems[0].registry == "conflicts")
check("(e) 新引擎 JSON：conflicts_pending/resolved 拆分解析正确",
      parsedNew.hasConflictSplit && parsedNew.conflictsPendingRecords == 0
      && parsedNew.conflictsResolvedRecords == 181
      && parsedNew.conflictsResolvedTotal == 203)
check("(e) 新引擎 JSON：老 counts.conflicts 仍解析为全量（向后兼容）",
      parsedNew.nearDuplicates == 2)
check("(e) 新引擎 JSON：明细分类直接用结构化 registry",
      BibDbFeature.LintWarningDigest.categories(items: parsedNew.warningItems,
                                                legacyWarnings: parsedNew.warnings)
          .map { $0.registry } == ["conflicts"])

// ── (f) 生产源码接线（B-188 目标态；防「纯逻辑在、视图没用上」）──────────
print("\n── (f) 生产源码接线（B-188 目标态）──")
let reviewSource = readSource("Views/BibDbReviewView.swift")
let statsSource = readSource("Views/BibDbStatsView.swift")
let mainWindowSource = readSource("Views/MainWindow.swift")
let querySource = readSource("Views/BibDbQueryView.swift")
let viewModelsSource = readSource("Core/ViewModels.swift")
let editorSheetSource = readSource("Views/BibDbEditorSheet.swift")
let mergeSheetSource = readSource("Views/BibDbMergeSheet.swift")
let fieldFixSheetSource = readSource("Views/BibDbFieldFixSheet.swift")

check("(f) 跳转意图类型在 ViewModels.swift（不依赖 BibDbFeature）：struct BibDbReviewJump: Equatable",
      viewModelsSource.contains("struct BibDbReviewJump: Equatable"))
check("(f) BibDbSettings 有一次性跳转意图 @Published var reviewJump",
      viewModelsSource.contains("@Published var reviewJump: BibDbReviewJump?"))
check("(f) 共享开关已删除：ViewModels 不再声明，视图不再绑定/写入 bibdb.showResolvedConflicts",
      !viewModelsSource.contains("var showResolvedConflicts")
      && !reviewSource.contains("$bibdb.showResolvedConflicts")
      && !reviewSource.contains("showResolved: bibdb.showResolvedConflicts")
      && !statsSource.contains("$bibdb.showResolvedConflicts")
      && !statsSource.contains("showResolved: bibdb.showResolvedConflicts")
      && !mainWindowSource.contains("bibdb.showResolvedConflicts ="))
check("(f) 待处理页消费 reviewJump 并在取走后置 nil（一次性意图）",
      reviewSource.contains("reviewJump") && reviewSource.contains("reviewJump = nil"))
check("(f) 待处理页激活时消费（onAppear + onChange(of: isActive)）",
      reviewSource.contains(".onAppear") && reviewSource.contains("onChange(of: isActive)"))
check("(f) 进页不无条件 refreshAll：先比 manifest 用 isStale 判新鲜",
      reviewSource.contains("isStale")
      && !reviewSource.contains("if isActive { model.refreshAll() }"))
check("(f) 待处理页标题/页头为「待处理」（不再叫「待裁决」页）",
      reviewSource.contains("待处理"))
check("(f) 三段列表用 LazyVStack（大列表不全量渲染）",
      reviewSource.contains("LazyVStack"))
check("(f) 字段冲突列表走缓存 listState + visibleGroups（不每帧重算/重排）",
      reviewSource.contains("listState") && reviewSource.contains("visibleGroups")
      && !reviewSource.contains("conflicts?.groups"))
check("(f) 开关切换只换缓存态（ReviewCacheState 两态预计算）",
      reviewSource.contains("ReviewCacheState") || reviewSource.contains("reviewCache"))
check("(f) 状态条走 pendingStatusText（N/P/W/M/sha 同一口径）",
      reviewSource.contains("pendingStatusText"))
check("(f) 分组头走 groupCountText（M = 该组全部条目数）",
      reviewSource.contains("groupCountText"))
check("(f) 已处理段走 processedEntries（冲突裁决 + 编辑/合并留痕同源）",
      reviewSource.contains("processedEntries"))
check("(f) 库健康段调 loadHealth 并用 records 展开逐条记录",
      reviewSource.contains("loadHealth") && reviewSource.contains(".records"))
check("(f) 库健康段用分类摘要（LintWarningDigest / health.categories）",
      reviewSource.contains("LintWarningDigest") || reviewSource.contains("categories"))
check("(f) 待处理页接了三个共享 sheet 入口（编辑/对照合并/批量归位）",
      reviewSource.contains("BibDbEditorSheet") && reviewSource.contains("BibDbMergeSheet")
      && reviewSource.contains("BibDbFieldFixSheet"))
check("(f) 待处理页同时展示两数据源（字段冲突 / 库健康）",
      reviewSource.contains("字段冲突") && reviewSource.contains("库健康"))
check("(f) 库概况橙字用 LintWarningDigest 下钻（分类明细）",
      statsSource.contains("LintWarningDigest"))
check("(f) 库概况→待处理页 onOpenReview 签名改为 () -> Void 且 MainWindow 接线",
      statsSource.contains("onOpenReview: () -> Void")
      && mainWindowSource.contains("onOpenReview")
      && mainWindowSource.contains("selection = .bibreview"))
check("(f) 库概况跳转先写 bibdb.reviewJump 再 onOpenReview（一次性意图）",
      statsSource.contains("bibdb.reviewJump") || statsSource.contains("reviewJump ="))
check("(f) 查库详情「编辑此条」直接开 BibDbEditorSheet",
      querySource.contains("BibDbEditorSheet") && querySource.contains("编辑此条"))
// 三个新 sheet 文件（Wave2 gui-sheets）：未落盘时给清晰 FAIL，便于 Lead 判集成状态。
check("(f) BibDbEditorSheet.swift：存在 + 冻结签名（recordKey / onWritten / 自 dismiss）",
      editorSheetSource.contains("struct BibDbEditorSheet: View")
      && editorSheetSource.contains("log: LogStore")
      && editorSheetSource.contains("bibdb: BibDbSettings")
      && editorSheetSource.contains("recordKey: String")
      && editorSheetSource.contains("onWritten")
      && editorSheetSource.contains("dismiss"),
      editorSheetSource.isEmpty ? "（文件尚未落盘：Views/BibDbEditorSheet.swift）" : "")
check("(f) BibDbMergeSheet.swift：存在 + 冻结签名（keys / onMerged / 自 dismiss）",
      mergeSheetSource.contains("struct BibDbMergeSheet: View")
      && mergeSheetSource.contains("log: LogStore")
      && mergeSheetSource.contains("bibdb: BibDbSettings")
      && mergeSheetSource.contains("keys: [String]")
      && mergeSheetSource.contains("onMerged")
      && mergeSheetSource.contains("dismiss"),
      mergeSheetSource.isEmpty ? "（文件尚未落盘：Views/BibDbMergeSheet.swift）" : "")
check("(f) BibDbFieldFixSheet.swift：存在 + 冻结签名（registry / onApplied / 自 dismiss）",
      fieldFixSheetSource.contains("struct BibDbFieldFixSheet: View")
      && fieldFixSheetSource.contains("log: LogStore")
      && fieldFixSheetSource.contains("bibdb: BibDbSettings")
      && fieldFixSheetSource.contains("registry: String")
      && fieldFixSheetSource.contains("onApplied")
      && fieldFixSheetSource.contains("dismiss"),
      fieldFixSheetSource.isEmpty ? "（文件尚未落盘：Views/BibDbFieldFixSheet.swift）" : "")

// ── (g) B-188：ConflictListState 存量缓存 / ReviewCacheState 双态缓存 ────
print("\n── (g) 冲突列表缓存（B-188 P0）──")
let cacheOutcome: BibDbFeature.ConflictsOutcome = mixed
var cacheState = BibDbFeature.ReviewCacheState(outcome: cacheOutcome)
check("(g) 默认态优先用 pending 缓存（2 条，不是全部 3 条）",
      cacheState.listState.visibleItems.map { $0.stableNo } == [1, 3],
      "\(cacheState.listState.visibleItems.map { $0.stableNo })")
check("(g) 两种态 precompute：pendingState 2 条 / resolvedState 3 条",
      cacheState.pendingState.visibleItems.count == 2
      && cacheState.resolvedState.visibleItems.count == 3)
cacheState.showResolved = true
check("(g) 本地开关置 true → listState 立刻返回已缓存全量（不重建状态）",
      cacheState.listState.visibleItems.map { $0.stableNo } == [1, 2, 3],
      "\(cacheState.listState.visibleItems.map { $0.stableNo })")
check("(g) 两种缓存态的分组头 M 都含被隐藏的已裁决（groupTotal=3）",
      cacheState.pendingState.visibleGroups.first.map {
          cacheState.pendingState.groupTotal(forGroupId: $0.id)
      } == 3
      && cacheState.resolvedState.visibleGroups.first.map {
          cacheState.resolvedState.groupTotal(forGroupId: $0.id)
      } == 3)
var staleCache = BibDbFeature.ConflictListState(outcome: mixed)
let cachedVisibleBefore = staleCache.visibleItems.map { $0.stableNo }
staleCache.showResolved = true
check("(g) 存量语义：直接改 ConflictListState.showResolved 不重算（换态须重建/换缓存）",
      staleCache.visibleItems.map { $0.stableNo } == cachedVisibleBefore,
      "\(staleCache.visibleItems.map { $0.stableNo })")
check("(g) groupTotals 是 init 预计算字典（混合组 1 个 id / 该组 3 条）",
      staleCache.groupTotals.count == 1 && staleCache.groupTotals.values.first == 3,
      "\(staleCache.groupTotals)")
check("(g) groupTotal 边界：未知分组 id / 空库 → 0",
      staleCache.groupTotal(forGroupId: "no-such-group") == 0
      && BibDbFeature.ConflictListState(outcome: makeOutcome([]))
          .groupTotal(forGroupId: "x") == 0)
var loadedOutcome = makeOutcome([makeItem(1, pending: true)])
loadedOutcome.dbPath = "/tmp/b188-gui/bibliography.jsonl"
check("(g) ReviewCacheState.loaded：读到过库（dbPath 非空 + 无 error）→ true",
      BibDbFeature.ReviewCacheState(outcome: loadedOutcome).loaded)
var errorOutcome = loadedOutcome
errorOutcome.error = "引擎失败"
check("(g) ReviewCacheState.loaded：error / 空 dbPath / 默认构造 → false",
      !BibDbFeature.ReviewCacheState(outcome: errorOutcome).loaded
      && !BibDbFeature.ReviewCacheState(outcome: BibDbFeature.ConflictsOutcome()).loaded
      && !BibDbFeature.ReviewCacheState().loaded)

// ── (h) B-188：lint records / 库健康 / 待处理状态条 ───────────────────────
print("\n── (h) 库健康与待处理状态条 ──")
let recordsPayload: [String: Any] = [
    "ok": true,
    "errors": [] as [Any],
    "warnings": ["缺 title 的条目 2 条", "缺可用年份的条目 1 条"],
    "warning_items": [
        ["code": "missing_title", "registry": "missing_field",
         "detail": "缺 title 的条目 2 条", "action": "补齐 title", "count": 2,
         "keys_sample": ["K1"], "records": ["K1", "K2"]] as [String: Any],
        ["code": "missing_year", "registry": "missing_field",
         "detail": "缺可用年份的条目 1 条", "action": "补齐年份", "count": 1] as [String: Any],
    ] as [Any],
]
let parsedRecords = BibDbFeature.parseLint(recordsPayload)
check("(h) parseLint：warning_items[].records 全量清单解析",
      parsedRecords.warningItems.first?.records == ["K1", "K2"],
      "\(parsedRecords.warningItems.first?.records ?? [])")
check("(h) parseLint：老引擎缺 records 键 → []（向后兼容，不误报）",
      parsedRecords.warningItems.count == 2
      && parsedRecords.warningItems[1].records.isEmpty)
let healthItems = [
    makeWarningItem(code: "missing_title", registry: "missing_field",
                    detail: "缺 title 的条目 3 条", action: "补齐", count: 3,
                    records: ["A", "B", "C"]),
    makeWarningItem(code: "missing_year", registry: "missing_field",
                    detail: "缺可用年份的条目 3 条", action: "补齐", count: 3,
                    records: ["C", "D", "E"]),
    makeWarningItem(code: "unverified", registry: "unverified",
                    detail: "未核实条目 2 条", action: "核实", count: 2,
                    keys: ["E", "Y"]),
]
let healthLint = makeLint(healthItems, warningCount: 3)
check("(h) healthAffectedRecords：records 并集去重（A-E）+ 无 records 退回 keys_sample（E/Y）→ 6",
      BibDbFeature.healthAffectedRecords(healthLint) == 6,
      "\(BibDbFeature.healthAffectedRecords(healthLint))")
let duplicateRecordLint = makeLint([
    makeWarningItem(code: "x", registry: "missing_field", detail: "d", action: "a",
                    count: 2, records: ["A", "A"]),
], warningCount: 1)
check("(h) healthAffectedRecords：同一 item 内重复 key 只算一次",
      BibDbFeature.healthAffectedRecords(duplicateRecordLint) == 1)
check("(h) healthAffectedRecords：空 lint → 0",
      BibDbFeature.healthAffectedRecords(makeLint([], warningCount: 0)) == 0)
check("(h) healthStatusText(nil) == 库健康（未加载）",
      BibDbFeature.healthStatusText(nil) == "库健康（未加载）",
      BibDbFeature.healthStatusText(nil))
check("(h) healthStatusText：条数用 warningCount（3）、记录数用并集（6）",
      BibDbFeature.healthStatusText(healthLint) == "库健康 3 条/涉及约 6 条记录",
      BibDbFeature.healthStatusText(healthLint))
let zeroCountLint = makeLint([healthItems[0]], warningCount: 0)
check("(h) healthStatusText：warningCount=0 时退回 warningItems.count",
      BibDbFeature.healthStatusText(zeroCountLint) == "库健康 1 条/涉及约 3 条记录",
      BibDbFeature.healthStatusText(zeroCountLint))
check("(h) pendingStatusText：精确文案（health nil / sha 空占位）",
      BibDbFeature.pendingStatusText(conflictPending: 0, health: nil,
                                     processedCount: 0, dbSha256: "")
          == "待处理 0（字段冲突 0 ｜ 库健康（未加载））｜ 已处理 0 ｜ 库 sha（未加载）",
      BibDbFeature.pendingStatusText(conflictPending: 0, health: nil,
                                     processedCount: 0, dbSha256: ""))
let smallHealth = makeLint([
    makeWarningItem(code: "missing_title", registry: "missing_field", detail: "d",
                    action: "a", count: 2, records: ["A", "B"]),
], warningCount: 3)
check("(h) pendingStatusText：N=P+W、M、sha 前 12 位 + …（CONTRACT §7 示例）",
      BibDbFeature.pendingStatusText(conflictPending: 2, health: smallHealth,
                                     processedCount: 7, dbSha256: "abcdef0123456789")
          == "待处理 5（字段冲突 2 ｜ 库健康 3 条/涉及约 2 条记录）｜ 已处理 7 ｜ 库 sha abcdef012345…",
      BibDbFeature.pendingStatusText(conflictPending: 2, health: smallHealth,
                                     processedCount: 7, dbSha256: "abcdef0123456789"))
check("(h) pendingStatusText：sha 不足 12 位原样显示（不补点）",
      BibDbFeature.pendingStatusText(conflictPending: 0, health: smallHealth,
                                     processedCount: 0, dbSha256: "abc123")
          .hasSuffix("库 sha abc123"))
check("(h) pendingStatusText：负数 P/M 归零（不给用户看负数）",
      BibDbFeature.pendingStatusText(conflictPending: -2, health: nil,
                                     processedCount: -3, dbSha256: "")
          == "待处理 0（字段冲突 0 ｜ 库健康（未加载））｜ 已处理 0 ｜ 库 sha（未加载）")
let healthCategories = BibDbFeature.healthCategories(from: healthLint)
check("(h) healthCategories：复用固定顺序，records 随 category.items 带出",
      healthCategories.map { $0.registry } == ["missing_field", "unverified"]
      && healthCategories[0].items.first?.records == ["A", "B", "C"],
      "\(healthCategories.map { $0.registry })")

// ── (i) B-188：已处理留痕（冲突裁决 + 编辑/合并 traces）──────────────────
print("\n── (i) 已处理留痕 ──")
let processedConflicts = makeOutcome([
    makeResolvedConflict(no: 12, resolution: "kept-existing",
                         resolvedAt: "2026-10-18T02:00:00Z",
                         fields: ["title", "year"]),
    makeResolvedConflict(no: 13, resolution: "adopted",
                         resolvedAt: "2026-10-18T05:00:00Z",
                         fields: ["author"]),
])
let editTrace = makeEditTrace(recordKey: "KeyEdit", action: "edit",
                              at: "2026-10-19T09:00:00Z", note: "手工补 title",
                              oldKey: "OldKey", newKey: "NewKey",
                              changedFields: [("year", "2000", "2001"),
                                              ("title", "旧", "新")],
                              backupSha256: String(repeating: "b", count: 64))
let mergeTrace = makeEditTrace(recordKey: "Tindale2015", action: "merge",
                               at: "2026-10-20T08:00:00Z", note: "合并重复",
                               mergedKeys: ["1-24", "t15"],
                               backupSha256: "abcdef0123456789")
let processed = BibDbFeature.processedEntries(conflicts: processedConflicts,
                                              traces: [editTrace, mergeTrace])
check("(i) processedEntries：冲突已裁决 2 条 + edit/merge 痕迹 2 条 = 4 条",
      processed.count == 4, "\(processed.count)")
check("(i) processedEntries：时间倒序（merge 10-20 → edit 10-19 → C013 05:00 → C012 02:00）",
      processed.map { $0.at } == ["2026-10-20T08:00:00Z",
                                  "2026-10-19T09:00:00Z",
                                  "2026-10-18T05:00:00Z",
                                  "2026-10-18T02:00:00Z"],
      "\(processed.map { $0.at })")
check("(i) processedEntries：冲突行中文动作 = resolutionTitle（采用新值/保留旧值）",
      processed.filter { $0.kind == "conflict" }.map { $0.actionTitle }
          == ["采用新值", "保留旧值"],
      "\(processed.filter { $0.kind == "conflict" }.map { $0.actionTitle })")
check("(i) processedEntries：冲突行 detail = C 编号 · 字段（C012 是 title、year）",
      processed.first { $0.key == "Key12" }?.detail == "C012 · 字段 title、year",
      processed.first { $0.key == "Key12" }?.detail ?? "")
check("(i) processedEntries：冲突裁决不虚构备份 sha（引擎不记）",
      processed.filter { $0.kind == "conflict" }.allSatisfy { $0.backupSha256.isEmpty })
check("(i) processedEntries：edit 行（新 key / 中文动作 / sha12… / 改字段+改 key+备注）",
      processed.first { $0.kind == "edit" }.map {
          $0.key == "NewKey" && $0.actionTitle == "编辑条目"
          && $0.backupSha256 == "bbbbbbbbbbbb…"
          && $0.detail == "改字段：year、title；key：OldKey → NewKey；备注：手工补 title"
      } == true,
      processed.first { $0.kind == "edit" }?.detail ?? "")
check("(i) processedEntries：merge 行（merged_keys 摘要 + 新 key + 备注 + sha12…）",
      processed.first { $0.kind == "merge" }.map {
          $0.key == "Tindale2015" && $0.actionTitle == "合并记录"
          && $0.detail == "合并 1-24、t15 → Tindale2015；备注：合并重复"
          && $0.backupSha256 == "abcdef012345…"
      } == true,
      processed.first { $0.kind == "merge" }?.detail ?? "")
check("(i) processedCount 与 processedEntries 同口径（4）",
      BibDbFeature.processedCount(conflicts: processedConflicts,
                                  traces: [editTrace, mergeTrace]) == 4)
check("(i) 空冲突 + 空 traces → 0 条（不误报）",
      BibDbFeature.processedEntries(conflicts: nil, traces: []).isEmpty
      && BibDbFeature.processedCount(conflicts: nil, traces: []) == 0)
var pendingWithResolvedAt = makeItem(1, pending: true)
pendingWithResolvedAt.resolvedAt = "2026-10-18T02:00:00Z"
var resolvedWithoutAt = makeItem(2, pending: false)
resolvedWithoutAt.resolvedAt = ""
let weirdProcessed = BibDbFeature.processedEntries(
    conflicts: makeOutcome([pendingWithResolvedAt, resolvedWithoutAt]), traces: [])
check("(i) 已裁决口径：pending 或 resolvedAt 为空的不算已处理",
      weirdProcessed.isEmpty, "\(weirdProcessed.count)")
let tieOutcome = makeOutcome([makeResolvedConflict(no: 1,
                                                   resolvedAt: "2026-10-21T00:00:00Z",
                                                   fields: ["title"])])
let tieEdit = makeEditTrace(recordKey: "K1", action: "edit", at: "2026-10-21T00:00:00Z")
let tieMerge = makeEditTrace(recordKey: "K2", action: "merge", at: "2026-10-21T00:00:00Z",
                             mergedKeys: ["K2", "K3"])
check("(i) 同一时间稳定排序：kind 字典序（conflict < edit < merge）",
      BibDbFeature.processedEntries(conflicts: tieOutcome, traces: [tieMerge, tieEdit])
          .map { $0.kind } == ["conflict", "edit", "merge"],
      "\(BibDbFeature.processedEntries(conflicts: tieOutcome, traces: [tieMerge, tieEdit]).map { $0.kind })")
check("(i) EditTrace：actionTitle 中文化 + 未知动作原样 + 空动作占位",
      makeEditTrace(recordKey: "K", action: "merge", at: "t").actionTitle == "合并记录"
      && makeEditTrace(recordKey: "K", action: "edit", at: "t").actionTitle == "编辑条目"
      && makeEditTrace(recordKey: "K", action: "weird", at: "t").actionTitle == "weird"
      && makeEditTrace(recordKey: "K", action: "", at: "t").actionTitle == "写库痕迹")
check("(i) EditTrace：backupShaShort 前 12 位 + … / 短 sha 原样",
      editTrace.backupShaShort == "bbbbbbbbbbbb…"
      && makeEditTrace(recordKey: "K", action: "edit", at: "t",
                       backupSha256: "abc123").backupShaShort == "abc123")
check("(i) EditTrace：changedFieldsText 用「、」拼接字段名",
      editTrace.changedFieldsText == "year、title"
      && makeEditTrace(recordKey: "K", action: "edit", at: "t").changedFieldsText.isEmpty)
check("(i) EditTrace / ProcessedEntry：id 稳定且列表内唯一",
      editTrace.id == editTrace.id && editTrace.id.contains("KeyEdit|edit|")
      && Set(processed.map { $0.id }).count == processed.count)
let editWithoutFields = makeEditTrace(recordKey: "K", action: "edit",
                                      at: "2026-10-17T00:00:00Z")
let noFieldsRow = BibDbFeature.processedEntries(conflicts: nil,
                                                traces: [editWithoutFields]).first
check("(i) edit 痕迹无字段变化 → detail「字段无变化」",
      noFieldsRow?.detail == "字段无变化", noFieldsRow?.detail ?? "")

// ── (j) B-188：编辑器纯 UI 级校验 validateEditorDraft ────────────────────
print("\n── (j) 编辑器校验 ──")
let validDraft = makeEditorDraft(recordKey: "K1", fields: ["title": "T", "year": "2015"])
let validResult = BibDbFeature.validateEditorDraft(validDraft)
check("(j) 合法 draft：不 blocked、无 errors",
      !validResult.isBlocked && validResult.errors.isEmpty,
      "\(validResult.errors)")
let emptyFieldsDraft = makeEditorDraft(fields: [:])
let emptyFieldsResult = BibDbFeature.validateEditorDraft(emptyFieldsDraft)
check("(j) 空字段表 → errors 含「至少要保留 1 个非空字段」",
      emptyFieldsResult.isBlocked
      && emptyFieldsResult.errors.contains { $0.contains("至少要保留 1 个非空字段") })
check("(j) 全空白字段值等同空字段（blocked）",
      BibDbFeature.validateEditorDraft(makeEditorDraft(fields: ["title": "   "])).isBlocked)
let badFieldName = makeEditorDraft(fields: ["Title": "T", "1title": "X", "bad name": "Y"])
let badFieldResult = BibDbFeature.validateEditorDraft(badFieldName)
check("(j) 非法字段名（大写/数字开头/空白）逐条报错",
      badFieldResult.errors.filter { $0.contains("字段名") }.count == 3,
      "\(badFieldResult.errors)")
check("(j) 合法字段名（小写开头 + 数字/下划线/连字符）不报错",
      !BibDbFeature.validateEditorDraft(
          makeEditorDraft(fields: ["title": "T", "entry_type": "x", "x-y_2": "z"])).isBlocked)
check("(j) 缺记录 key → errors 含「缺少库内记录 key」",
      BibDbFeature.validateEditorDraft(makeEditorDraft(recordKey: "  "))
          .errors.contains { $0.contains("缺少库内记录 key") })
let newKeyEmpty = makeEditorDraft(newKeyEnabled: true, newKey: "   ")
let newKeyEmptyResult = BibDbFeature.validateEditorDraft(newKeyEmpty)
check("(j) 勾改 key 但新 key 为空 → blocked",
      newKeyEmptyResult.isBlocked
      && newKeyEmptyResult.errors.contains { $0.contains("新 key 为空") })
let illegalKeys = ["A B", "A,B", "A{B}", "A(B)", "A\"B"]
check("(j) 新 key 含空白/逗号/花括号/引号/括号 → 全部 blocked",
      illegalKeys.allSatisfy { key in
          let result = BibDbFeature.validateEditorDraft(
              makeEditorDraft(newKeyEnabled: true, newKey: key))
          return result.isBlocked
              && result.errors.contains { $0.contains("不是合法 BibTeX key") }
      }, "\(illegalKeys)")
let sameKeyResult = BibDbFeature.validateEditorDraft(
    makeEditorDraft(recordKey: "K1", newKeyEnabled: true, newKey: "K1"))
check("(j) 新 key 与当前 key 相同 → 只 warning、不 blocked",
      !sameKeyResult.isBlocked
      && sameKeyResult.warnings.contains { $0.contains("新 key 与当前 key 相同") })
let emptyValueResult = BibDbFeature.validateEditorDraft(
    makeEditorDraft(fields: ["title": "T", "year": ""]))
check("(j) 空值只 warning 不 block，且 warning 列出字段名",
      !emptyValueResult.isBlocked
      && emptyValueResult.warnings.contains {
          $0.contains("year") && $0.contains("引擎会删掉该字段")
      }, "\(emptyValueResult.warnings)")
check("(j) 不判必填字段：只有 title 也能过 UI 级校验（必填交引擎 dry-run）",
      !BibDbFeature.validateEditorDraft(
          makeEditorDraft(fields: ["title": "T"])).isBlocked)
check("(j) 不判 entry_type 合法性：rticle 也能进预览（引擎 dry-run 判）",
      !BibDbFeature.validateEditorDraft(
          makeEditorDraft(entryType: "rticle", fields: ["title": "T"])).isBlocked)
let sortedDraft = makeEditorDraft(fields: ["year": "2015", "author": "A", "title": "T"])
check("(j) EditorDraft.sortedFieldNames 按字段名排序（视图渲染稳定）",
      sortedDraft.sortedFieldNames == ["author", "title", "year"],
      "\(sortedDraft.sortedFieldNames)")

// ── (k) B-188：合并建议 key / 合并校验 ───────────────────────────────────
print("\n── (k) 合并建议 key 与校验 ──")
check("(k) suggestedMergeKey：Tindale, C. W. + 2015 → Tindale2015",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "1-24",
          fields: ["author": #"Tindale, C. W."#, "year": "2015"])]) == "Tindale2015")
check("(k) suggestedMergeKey：Segerberg, K. + 1971 → Segerberg1971",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "x", fields: ["author": #"Segerberg, K."#, "year": "1971"])])
          == "Segerberg1971")
check("(k) suggestedMergeKey：无 author/editor 且无 year → 回退第一条 key（B-201 不再加 Merged）",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "miaolitian", fields: ["title": "x"])]) == "miaolitian")
check("(k) suggestedMergeKey：有作者无年份 → 回退第一条 key（B-201）",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "1-24", fields: ["author": #"Tindale, C. W."#])]) == "1-24")
check("(k) suggestedMergeKey：有年份无作者 → 回退第一条 key（B-201）",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "x", fields: ["year": "2015"])]) == "x")
check("(k) suggestedMergeKey：候选等于自身 key → 直接返回该 key（B-201 不加 Merged）",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "Tindale2015",
          fields: ["author": #"Tindale, C. W."#, "year": "2015"])]) == "Tindale2015")
check("(k) suggestedMergeKey：候选命中另一条待合并 key → 返回该 key（B-201）",
      BibDbFeature.suggestedMergeKey(records: [
          makeMergeRecord(key: "1-24", fields: ["author": #"Tindale, C. W."#, "year": "2015"]),
          makeMergeRecord(key: "Tindale2015", fields: ["author": "X", "year": "2000"])])
          == "Tindale2015")
check("(k) suggestedMergeKey：重音 folding（Émile → Emile）",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "x", fields: ["author": "Émile, J.", "year": "1999"])]) == "Emile1999")
check("(k) suggestedMergeKey：LaTeX 重音/大括号清理（{\\'E}mile → Emile）",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "x", fields: ["author": #"{\'E}mile, J."#, "year": "1999"])])
          == "Emile1999")
check("(k) suggestedMergeKey：\\textit{Tindale} 去命令后取姓",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "x", fields: ["author": #"\textit{Tindale}, C. W."#, "year": "2015"])])
          == "Tindale2015")
check("(k) suggestedMergeKey：多作者取第一作者（Tindale … and Segerberg …）",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "x", fields: ["author": #"Tindale, C. W. and Segerberg, K."#,
                             "year": "2015"])]) == "Tindale2015")
check("(k) suggestedMergeKey：无逗号时取最后一个 token（Charles Tindale → Tindale）",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "x", fields: ["author": "Charles Tindale", "year": "2015"])])
          == "Tindale2015")
check("(k) suggestedMergeKey：无 author 时用 editor",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "x", fields: ["editor": #"Segerberg, K."#, "year": "1971"])])
          == "Segerberg1971")
check("(k) suggestedMergeKey：year 缺失时从 date 取第一个 4 位年份",
      BibDbFeature.suggestedMergeKey(records: [makeMergeRecord(
          key: "x", fields: ["author": #"Tindale, C. W."#, "date": "2015-03"])])
          == "Tindale2015")
check("(k) suggestedMergeKey：以第一条记录为准（第二条不参与）",
      BibDbFeature.suggestedMergeKey(records: [
          makeMergeRecord(key: "a", fields: ["author": #"Segerberg, K."#, "year": "1971"]),
          makeMergeRecord(key: "b", fields: ["author": #"Tindale, C. W."#, "year": "2015"]),
      ]) == "Segerberg1971")
check("(k) suggestedMergeKey：空记录 → 空串",
      BibDbFeature.suggestedMergeKey(records: []).isEmpty)
check("(k) validateMerge：<2 条 → 报错",
      BibDbFeature.validateMerge(keys: ["A"], newKey: "X")
          .contains { $0.contains("至少要选 2 条") })
check("(k) validateMerge：重复 key（trim 后也算）→ 报错",
      BibDbFeature.validateMerge(keys: ["A", "A"], newKey: "X")
          .contains { $0.contains("有重复") }
      && BibDbFeature.validateMerge(keys: ["A", " A "], newKey: "X")
          .contains { $0.contains("有重复") })
check("(k) validateMerge：空 newKey → 报错",
      BibDbFeature.validateMerge(keys: ["A", "B"], newKey: "  ")
          .contains { $0.contains("必须给新 key") })
check("(k) validateMerge：非法 newKey 字符（空白/逗号/花括号/引号/括号）→ 全部报错",
      ["A B", "A,B", "A{B}", "A(B)", "A\"B"].allSatisfy {
          !BibDbFeature.validateMerge(keys: ["A", "B"], newKey: $0).isEmpty
      })
check("(k) validateMerge：合法输入返回 []（trim 后 ≥2 条）",
      BibDbFeature.validateMerge(keys: [" A ", "B"], newKey: "Tindale2015").isEmpty)
check("(k) validateMerge：新 key 等于任一待合并旧 key → 允许（B-201，trim 后也允许）",
      BibDbFeature.validateMerge(keys: ["A", "B"], newKey: "A").isEmpty
      && BibDbFeature.validateMerge(keys: ["A", "B"], newKey: " B ").isEmpty)
check("(k) validateMerge：<2 条 + 空 newKey 同时报 2 个错",
      BibDbFeature.validateMerge(keys: ["A"], newKey: "").count == 2)
check("(k) validateMerge：全空白 key 过滤后视作不足 2 条",
      !BibDbFeature.validateMerge(keys: ["A", "  "], newKey: "X").isEmpty)

// ── (l) B-188：合并字段对照 mergeFieldRows ──────────────────────────────
print("\n── (l) 合并字段对照 ──")
let mergeRecA = makeMergeRecord(key: "A", fields: ["title": "Same", "year": "2000",
                                                   "journal": "JA"])
let mergeRecB = makeMergeRecord(key: "B", fields: ["title": "Same", "year": "2001",
                                                   "note": "NB"])
let fieldRows = BibDbFeature.mergeFieldRows(records: [mergeRecA, mergeRecB])
check("(l) mergeFieldRows：字段并集、按字段名排序",
      fieldRows.map { $0.field } == ["journal", "note", "title", "year"],
      "\(fieldRows.map { $0.field })")
check("(l) mergeFieldRows：每行 values 覆盖全部记录 key（缺字段 = 空串，列稳定）",
      fieldRows.allSatisfy { $0.values.keys.sorted() == ["A", "B"] }
      && fieldRows.first { $0.field == "note" }?.values["A"] == ""
      && fieldRows.first { $0.field == "note" }?.values["B"] == "NB")
check("(l) mergeFieldRows：chosen = 第一条非空（note 只有 B 有 → B）",
      fieldRows.first { $0.field == "note" }?.chosen == "B"
      && fieldRows.first { $0.field == "note" }?.chosenValue == "NB")
check("(l) mergeFieldRows：相同值不标差异（title Same/Same → differs=false）",
      fieldRows.first { $0.field == "title" }?.differs == false)
check("(l) mergeFieldRows：值不同 / 单边缺失都标差异",
      fieldRows.first { $0.field == "year" }?.differs == true
      && fieldRows.first { $0.field == "journal" }?.differs == true)
check("(l) mergeFieldRows：records 顺序决定 chosen（交换后 title 取 B）",
      BibDbFeature.mergeFieldRows(records: [mergeRecB, mergeRecA])
          .first { $0.field == "title" }?.chosen == "B")
check("(l) mergeFieldRows：空表 → []；单条记录 chosen 回退到该 key",
      BibDbFeature.mergeFieldRows(records: []).isEmpty
      && BibDbFeature.mergeFieldRows(
          records: [makeMergeRecord(key: "A", fields: ["title": ""])])
          .first?.chosen == "A")
check("(l) mergeFieldRows：chosenValue 读 values[chosen]",
      fieldRows.first { $0.field == "journal" }?.chosenValue == "JA")

// ── (m) B-188：字段归位（分组/默认勾选/风险文案/草稿）───────────────────
print("\n── (m) 字段归位 ──")
let acRow = makeFieldFixRow(
    key: "K1", missing: ["title"],
    fields: ["author": "A", "year": "2015"],
    candidates: [makeCandidate(field: "title", value: "候选-中", source: "author",
                               risk: "medium"),
                 makeCandidate(field: "title", value: "候选-高", source: "journal",
                               risk: "high")])
check("(m) fieldFixRiskText A 类：题名候选 2 个（风险 中高）…",
      BibDbFeature.fieldFixRiskText(acRow)
          == "题名候选 2 个（风险 中高），请人工选一个",
      BibDbFeature.fieldFixRiskText(acRow))
let multiMissingRow = makeFieldFixRow(
    key: "K2", missing: ["title", "year"], fields: ["author": "A"],
    candidates: [makeCandidate(field: "title", value: "T", risk: "medium"),
                 makeCandidate(field: "year", value: "2015", risk: "low")])
check("(m) fieldFixRiskText A 类：多缺失字段按「；」连",
      BibDbFeature.fieldFixRiskText(multiMissingRow)
          == "题名候选 1 个（风险 中），请人工选一个；年份候选 1 个（风险 低），请人工选一个",
      BibDbFeature.fieldFixRiskText(multiMissingRow))
let noCandidateFieldRow = makeFieldFixRow(
    key: "K3", missing: ["title", "publisher"],
    candidates: [makeCandidate(field: "title", value: "T", risk: "low")])
check("(m) fieldFixRiskText A 类：某个缺失字段无候选 → 「出版者无候选：需人工核原稿」",
      BibDbFeature.fieldFixRiskText(noCandidateFieldRow)
          == "题名候选 1 个（风险 低），请人工选一个；出版者无候选：需人工核原稿",
      BibDbFeature.fieldFixRiskText(noCandidateFieldRow))
check("(m) fieldFixRiskText A 类：整行无候选 → 「无候选：需人工核原稿」",
      BibDbFeature.fieldFixRiskText(makeFieldFixRow(key: "K4", missing: ["title"]))
          == "无候选：需人工核原稿")
check("(m) fieldFixRiskText A 类：missing 为空时按候选字段聚合",
      BibDbFeature.fieldFixRiskText(makeFieldFixRow(
          key: "K5",
          candidates: [makeCandidate(field: "year", value: "2001", risk: "medium")]))
          == "年份候选 1 个（风险 中），请人工选一个")
let cTypeRow = makeFieldFixRow(
    key: "rticle-key", entryType: "rticle",
    candidates: [makeCandidate(field: "", value: "artical", source: "entry_type",
                               risk: "high"),
                 makeCandidate(field: "", value: "article", source: "entry_type",
                               risk: "medium")],
    anomalyKind: "unknown_entry_type")
check("(m) fieldFixRiskText C 类：entry_type 笔误取最小风险候选（article 中风险）",
      BibDbFeature.fieldFixRiskText(cTypeRow)
          == "entry_type 笔误：rticle → article（中风险）",
      BibDbFeature.fieldFixRiskText(cTypeRow))
let cDoiRow = makeFieldFixRow(key: "doi-key", anomalyKind: "doi_key",
                              keyCandidate: "Lorini2020", keyRisk: "medium")
check("(m) fieldFixRiskText C 类：key 异常（DOI/网址当 key）",
      BibDbFeature.fieldFixRiskText(cDoiRow)
          == "key 异常（DOI/网址当 key）：建议 Lorini2020（中风险）",
      BibDbFeature.fieldFixRiskText(cDoiRow))
let cPlaceholderRow = makeFieldFixRow(key: "placeholder", anomalyKind: "placeholder_key",
                                      keyCandidate: "Lorini2019", keyRisk: "low")
check("(m) fieldFixRiskText C 类：占位符 key",
      BibDbFeature.fieldFixRiskText(cPlaceholderRow)
          == "key 异常（占位符 key）：建议 Lorini2019（低风险）",
      BibDbFeature.fieldFixRiskText(cPlaceholderRow))
check("(m) fieldFixRiskText C 类：无候选/key 建议 → 人工核对",
      BibDbFeature.fieldFixRiskText(makeFieldFixRow(key: "x", anomalyKind: "doi_key"))
          == "需人工核对 key / entry_type")
check("(m) FieldFixCandidate.riskText：low/medium/high/未知/原样",
      makeCandidate(field: "f", value: "v", risk: "low").riskText == "低"
      && makeCandidate(field: "f", value: "v", risk: "medium").riskText == "中"
      && makeCandidate(field: "f", value: "v", risk: "high").riskText == "高"
      && makeCandidate(field: "f", value: "v", risk: "").riskText == "未知"
      && makeCandidate(field: "f", value: "v", risk: "odd").riskText == "odd")
let defaultChoices = BibDbFeature.defaultFieldFixChoices(makeFieldFixRow(
    key: "K6", missing: ["title"],
    candidates: [makeCandidate(field: "title", value: "T-中", risk: "medium"),
                 makeCandidate(field: "title", value: "T-低1", risk: "low"),
                 makeCandidate(field: "title", value: "T-低2", risk: "low")]))
check("(m) defaultFieldFixChoices：同字段选 riskOrder 最小者；同风险取第一个",
      defaultChoices == ["title": "T-低1"], "\(defaultChoices)")
let multiFieldChoices = BibDbFeature.defaultFieldFixChoices(makeFieldFixRow(
    key: "K7", missing: ["title", "year"],
    candidates: [makeCandidate(field: "title", value: "T-高", risk: "high"),
                 makeCandidate(field: "title", value: "T-低", risk: "low"),
                 makeCandidate(field: "year", value: "Y-高", risk: "high"),
                 makeCandidate(field: "year", value: "Y-中", risk: "medium")]))
check("(m) defaultFieldFixChoices：逐字段各选最小风险",
      multiFieldChoices == ["title": "T-低", "year": "Y-中"], "\(multiFieldChoices)")
check("(m) defaultFieldFixChoices：C 类 entry_type 候选（field 空）不进字段选择",
      BibDbFeature.defaultFieldFixChoices(cTypeRow).isEmpty
      && BibDbFeature.defaultFieldFixChoices(makeFieldFixRow(key: "K8")).isEmpty)
let aRows = [acRow, multiMissingRow]
let aGroups = BibDbFeature.fieldFixGroups(aRows)
check("(m) fieldFixGroups：A 类全部 anomalyKind 空 → 单组 missing_field",
      aGroups.count == 1 && aGroups[0].id == "missing_field"
      && aGroups[0].title == "缺字段归位（按记录）" && aGroups[0].count == 2,
      "\(aGroups.map { $0.id })")
let otherKindRow = makeFieldFixRow(key: "ok", anomalyKind: "")
let cRows = [cPlaceholderRow, otherKindRow,
             makeFieldFixRow(key: "type", anomalyKind: "unknown_entry_type"),
             makeFieldFixRow(key: "doi", anomalyKind: "doi_key"),
             makeFieldFixRow(key: "weird", anomalyKind: "future_kind")]
let cGroups = BibDbFeature.fieldFixGroups(cRows)
check("(m) fieldFixGroups：C 类固定顺序 unknown_entry_type/doi_key/placeholder_key/其他",
      cGroups.map { $0.id } == ["unknown_entry_type", "doi_key", "placeholder_key", "other"],
      "\(cGroups.map { $0.id })")
check("(m) fieldFixGroups：分组标题中文化 + count 正确 + 不丢「其他」",
      cGroups.map { $0.title } == ["entry_type 笔误", "DOI/网址当 key",
                                   "占位符 key", "其他异常"]
      && cGroups.map { $0.count } == [1, 1, 1, 2]
      && cGroups.reduce(0) { $0 + $1.count } == cRows.count,
      "\(cGroups.map { $0.title }) \(cGroups.map { $0.count })")
check("(m) fieldFixGroups：空行 → []", BibDbFeature.fieldFixGroups([]).isEmpty)
let aDraft = BibDbFeature.buildFieldFixDraft(
    row: makeFieldFixRow(key: "K1", entryType: "article", missing: ["title"],
                         fields: ["title": "旧", "year": "2015"]),
    choice: BibDbFeature.FieldFixChoice(values: ["title": "新", "year": ""]))
check("(m) buildFieldFixDraft：fields 合并、空值不覆盖、recordKey/entryType 沿用",
      aDraft.recordKey == "K1" && aDraft.entryType == "article"
      && aDraft.fields["title"] == "新" && aDraft.fields["year"] == "2015"
      && !aDraft.newKeyEnabled && aDraft.note == "字段归位：补 title",
      "\(aDraft.fields) note=\(aDraft.note)")
let cDraft = BibDbFeature.buildFieldFixDraft(
    row: cTypeRow,
    choice: BibDbFeature.FieldFixChoice(values: [:], entryType: "article",
                                        newKey: "Lorini2020"))
check("(m) buildFieldFixDraft：entryType 覆盖 + 非空 newKey 启用改 key + 备注",
      cDraft.entryType == "article" && cDraft.newKeyEnabled
      && cDraft.newKey == "Lorini2020"
      && cDraft.note.contains("修 entry_type") && cDraft.note.contains("改 key")
      && cDraft.note.hasPrefix("字段归位"), "\(cDraft.note)")
check("(m) buildFieldFixDraft：newKey 空串不启用改 key；choice.values 可新增字段",
      { let draft = BibDbFeature.buildFieldFixDraft(
            row: makeFieldFixRow(key: "K9", fields: ["title": "T"]),
            choice: BibDbFeature.FieldFixChoice(values: ["publisher": "P"],
                                                entryType: nil, newKey: ""))
        return !draft.newKeyEnabled && draft.fields["publisher"] == "P"
            && draft.entryType == "article" }())

// ── (n) B-188：新鲜度 isStale / 写后复核文案 / DBManifest ────────────────
print("\n── (n) 新鲜度与写后复核 ──")
let baseManifest = makeManifest()
check("(n) DBManifest.shaShort：64 位 sha → 前 12 位 + …",
      baseManifest.shaShort == "aaaaaaaaaaaa…", baseManifest.shaShort)
check("(n) DBManifest.shaShort：短 sha / 空 sha 原样",
      makeManifest(sha256: "abc123").shaShort == "abc123"
      && makeManifest(sha256: "").shaShort.isEmpty)
check("(n) isStale：没有缓存 → true",
      BibDbFeature.isStale(cached: nil, current: baseManifest))
check("(n) isStale：完全相同 → false",
      !BibDbFeature.isStale(cached: baseManifest, current: makeManifest()))
check("(n) isStale：路径变 → true",
      BibDbFeature.isStale(cached: baseManifest,
                           current: makeManifest(path: "/tmp/other.jsonl")))
check("(n) isStale：存在性变 → true",
      BibDbFeature.isStale(cached: makeManifest(exists: false),
                           current: baseManifest)
      && BibDbFeature.isStale(cached: baseManifest,
                              current: makeManifest(exists: false)))
check("(n) isStale：两边都不存在且路径相同 → false（不触发重载）",
      !BibDbFeature.isStale(cached: makeManifest(exists: false),
                            current: makeManifest(exists: false)))
check("(n) isStale：size 变 → true",
      BibDbFeature.isStale(cached: baseManifest,
                           current: makeManifest(size: 6_000_001)))
check("(n) isStale：mtime 变 > 0.001s → true",
      BibDbFeature.isStale(cached: baseManifest,
                           current: makeManifest(mtime: 1_700_000_005)))
check("(n) isStale：mtime 差在 0.001s 容差内 → false（文件系统精度）",
      !BibDbFeature.isStale(cached: makeManifest(mtime: 1_700_000_000.0004),
                            current: makeManifest(mtime: 1_700_000_000.0000)))
check("(n) isStale：sha 变 → true",
      BibDbFeature.isStale(cached: baseManifest,
                           current: makeManifest(sha256: String(repeating: "b",
                                                                count: 64))))
check("(n) DBManifest Equatable：字段全同 → true；sha 不同 → false",
      makeManifest() == makeManifest()
      && makeManifest(sha256: "x") != makeManifest())
var verifyOK = BibDbFeature.WriteVerification()
verifyOK.ok = true
verifyOK.records = 7050
verifyOK.lintErrors = 0
verifyOK.lintWarnings = 18
check("(n) WriteVerification.summary：通过文案带记录数/lint 计数",
      verifyOK.summary == "写后复核通过（记录 7050 条；lint error 0 / warning 18）",
      verifyOK.summary)
var verifyBad = BibDbFeature.WriteVerification()
verifyBad.ok = false
verifyBad.problems = ["记录数 7051 → 7049", "lint 新增 error"]
check("(n) WriteVerification.summary：失败文案列出引擎问题",
      verifyBad.summary == "写后复核失败：记录数 7051 → 7049；lint 新增 error",
      verifyBad.summary)
check("(n) WriteVerification.summary：没有 problems 时给占位原因",
      BibDbFeature.WriteVerification().summary == "写后复核失败：（引擎未给原因）")

// ── (o) B-190：git 版本层（纯逻辑 + 源码接线；不起子进程/不碰真实库）────────
print("\n── (o) git 版本层（B-190）──")
let gitSource = readSource("Features/BibDbGit.swift")
let modelSource = readSource("Features/BibDbFeature.swift")
let importSource = readSource("Features/BibDbFeature+Import.swift")
let runnerSource = (try? String(contentsOf: toolbox.appendingPathComponent("tools/run_swift_tests.sh"), encoding: .utf8)) ?? ""
let gitCode = gitSource.split(separator: "\n").map { $0.components(separatedBy: "//").first ?? "" }.joined(separator: "\n")
func sourceSlice(_ text: String, _ from: String, _ to: String) -> String { guard let a = text.range(of: from), let b = text.range(of: to, range: a.upperBound..<text.endIndex) else { return "" }; return String(text[a.upperBound..<b.lowerBound]) }
let shaA = String(repeating: "a", count: 64), shaB = String(repeating: "b", count: 64), dbRel = "bib_db/bibliography.jsonl"
check("(o) BibDbGit 只 import Foundation（无 SwiftUI/Combine）", gitCode.contains("import Foundation") && !gitCode.contains("import SwiftUI") && !gitCode.contains("import Combine"))
check("(o) commit message 固定格式", BibDbGit.commitMessage(action: "编辑条目", recordCount: 3, shaBefore: shaA, shaAfter: shaB) == "引用库：编辑条目（记录 3 条；sha aaaaaaaaaaaa→bbbbbbbbbbbb）")
check("(o) commit message：note 只追加第二行", BibDbGit.commitMessage(action: "", recordCount: 0, shaBefore: "", shaAfter: "", note: "补 title") == "引用库：写库（记录 0 条；sha →）\n备注：补 title")
check("(o) shortSha / revertMessage", BibDbGit.shortSha(shaA) == "aaaaaaaaaaaa" && BibDbGit.shortSha("abc") == "abc" && BibDbGit.revertMessage(shaBefore: shaA, shaAfter: shaB) == "引用库：回退到上一个提交（sha aaaaaaaaaaaa→bbbbbbbbbbbb）")
let commitArgs = BibDbGit.commitArguments(message: "m")
check("(o) add/commit 的 pathspec 只有库文件（没别的文件）", BibDbGit.addArguments() == ["add", "--", dbRel] && commitArgs == ["commit", "-m", "m", "--", dbRel] && commitArgs.filter { $0.contains("/") } == [dbRel])
check("(o) status/log/ahead/branch 命令口径", BibDbGit.statusArguments() == ["status", "--porcelain", "--", dbRel] && BibDbGit.logArguments() == ["log", "--oneline", "-3", "--", dbRel] && BibDbGit.aheadArguments() == ["rev-list", "--count", "@{upstream}..HEAD"] && BibDbGit.branchArguments() == ["rev-parse", "--abbrev-ref", "HEAD"])
check("(o) push 命令：`push <remote> <tip>:refs/heads/<branch>`（无 force）", BibDbGit.pushArguments(remote: "origin", tip: "abcdef123456", branch: "main") == ["push", "origin", "abcdef123456:refs/heads/main"] && !BibDbGit.pushArguments(remote: "origin", tip: "abcdef123456", branch: "main").contains("--force"))
check("(o) 回退命令：checkout HEAD~1 + 库文件（不 hard reset）", BibDbGit.revertCheckoutArguments() == ["checkout", "HEAD~1", "--", dbRel] && !BibDbGit.revertCheckoutArguments().contains("--hard"))
check("(o) 回退前校验：≥2 库提交 / HEAD 有库文件", BibDbGit.commitCountArguments() == ["rev-list", "--count", "HEAD", "--", dbRel] && BibDbGit.headFileCheckArguments() == ["cat-file", "-e", "HEAD:" + dbRel])
check("(o) 仓库定位命令：git -C <db 目录> rev-parse --show-toplevel", BibDbGit.repoRootArguments(dbDirectory: "/tmp/x") == ["-C", "/tmp/x", "rev-parse", "--show-toplevel"])
check("(o) parseRepoRoot：去空白 / fatal 拒绝", BibDbGit.parseRepoRoot("/tmp/notes\n") == "/tmp/notes" && BibDbGit.parseRepoRoot("fatal: not a git repository") == nil)
check("(o) relativeDBPath：约定路径通过、仓库外 nil", BibDbGit.relativeDBPath(repoRoot: "/tmp/repo", dbPath: "/tmp/repo/bib_db/bibliography.jsonl") == dbRel && BibDbGit.relativeDBPath(repoRoot: "/tmp/repo", dbPath: "/tmp/other/bib_db/bibliography.jsonl") == nil)
check("(o) isDBDirty：空/空白 = 干净", !BibDbGit.isDBDirty("") && !BibDbGit.isDBDirty(" \n") && BibDbGit.isDBDirty(" M bib_db/bibliography.jsonl"))
check("(o) parseUnpushedCount：3/0/坏值（无 upstream fail-soft）", BibDbGit.parseUnpushedCount("3\n") == 3 && BibDbGit.parseUnpushedCount("0") == 0 && BibDbGit.parseUnpushedCount("fatal: no upstream") == nil)
check("(o) parseBranch / parseCommitCount", BibDbGit.parseBranch("main\n") == "main" && BibDbGit.parseCommitCount("2\n") == 2 && BibDbGit.parseCommitCount("") == nil)
check("(o) parseLogOneline：最多 3 条 + sha/说明拆分", BibDbGit.parseLogOneline("a1 第一次\nb2 第二次\nc3 第三次\nd4 第四次").map { $0.sha } == ["a1", "b2", "c3"] && BibDbGit.parseLogOneline("a1 第一次").first?.subject == "第一次")
check("(o) parseShasum：64 hex 通过、坏格式 nil", BibDbGit.parseShasum(shaA + "  /tmp/f\n") == shaA && BibDbGit.parseShasum("deadbeef  /tmp/f") == nil)
check("(o) failureText：优先错误行并截断（push 失败带 stderr 摘要）", BibDbGit.failureText("To x\n ! [rejected] main -> main (fetch first)\nerror: failed to push some refs").contains("rejected") && BibDbGit.failureText("a\nb\nc\nd\ne", maxLines: 2) == "d ｜ e")
check("(o) isIdentityError：身份未配置可识别", BibDbGit.isIdentityError("*** Please tell me who you are.") && !BibDbGit.isIdentityError("fatal: not a git repository"))
check("(o) fail-soft 默认值：unpushed=-1 / loaded=false", BibDbGit.VersionStatus().unpushedCount == -1 && !BibDbGit.VersionStatus().loaded && BibDbGit.VersionStatus(repo: "/r").loaded)
check("(o) git 源码无 hard reset/pull/fetch/rebase/switch/force", !gitCode.contains("--hard") && !gitCode.contains("reset") && !gitCode.contains("\"pull\"") && !gitCode.contains("\"fetch\"") && !gitCode.contains("\"rebase\"") && !gitCode.contains("\"switch\"") && !gitCode.contains("--force"))
check("(o) commitDBVersion 源码不 push（push 只能显式调用）", !sourceSlice(gitSource, "static func commitDBVersion", "static func manualCommit").contains("push") && sourceSlice(gitSource, "static func push(db: String)", "static func revertToPreviousCommit").contains("pushArguments(remote:"))
let commitSrc = sourceSlice(gitSource, "private static func commitDBFile", "static func versionStatus")
check("(o) 提交源码：脏否→add→commit(pathspec)→HEAD，且不 push", commitSrc.contains("addArguments()") && commitSrc.contains("commitArguments(message: message)") && commitSrc.contains("isDBDirty") && !commitSrc.contains("pushArguments("))
check("(o) 模型/入藏源码不再引用备份制 API", !modelSource.contains("backupDirectory") && !modelSource.contains("listBackups") && !modelSource.contains("func restore(") && !importSource.contains("backupDirectory") && !modelSource.contains("\"--backup\"") && !importSource.contains("\"--backup\"") && modelSource.contains("--expect-db-sha256"))
check("(o) 入藏段已纯搬迁到 extension（类型不再在 model 声明）", importSource.contains("extension BibDbFeature") && importSource.contains("struct ImportOutcome") && !modelSource.contains("struct ImportOutcome") && importSource.contains("func importBib(") && importSource.contains("importPastedBib("))
check("(o) 测试台编译清单含新文件", runnerSource.contains("Features/BibDbGit.swift") && runnerSource.contains("Features/BibDbFeature+Import.swift"))

// ── (o2) B-203：推送只推 bib_db 连续前缀 + 代理重试 ─────────────────────
print("\n── (o2) B-203 推送预检（只推 bib_db）──")
func makePushCommit(_ sha: String, parents: [String] = ["p0"], subject: String,
                    paths: [String]) -> BibDbGit.PushCommit {
    BibDbGit.PushCommit(sha: sha, parents: parents, subject: subject, paths: paths)
}
let pushLogText = "\u{1e}aaaaaaaa1\u{1f}p0\u{1f}引用库：写库 A\nbib_db/bibliography.jsonl\n\n"
    + "\u{1e}bbbbbbbb2\u{1f}aaaaaaaa1\u{1f}notes：补 drafts\ndrafts/probe.md\nextra.md\n"
let parsedPushLog = BibDbGit.parsePushLog(pushLogText)
check("(o2) parsePushLog：\\x1e/\\x1f 记录/字段分隔、路径逐行、顺序保留",
      parsedPushLog.count == 2
      && parsedPushLog[0].sha == "aaaaaaaa1"
      && parsedPushLog[0].parents == ["p0"]
      && parsedPushLog[0].subject == "引用库：写库 A"
      && parsedPushLog[0].paths == ["bib_db/bibliography.jsonl"]
      && parsedPushLog[1].paths == ["drafts/probe.md", "extra.md"],
      "\(parsedPushLog)")
check("(o2) parsePushLog：空提交 0 路径；空日志 → []；坏记录跳过",
      BibDbGit.parsePushLog("\u{1e}cccccccc3\u{1f}bbbbbbbb2\u{1f}空提交\n\n")
          .first?.paths.isEmpty == true
      && BibDbGit.parsePushLog("").isEmpty
      && BibDbGit.parsePushLog("\u{1e}not-a-record").isEmpty)
check("(o2) pushLogArguments：旧→新、%H/%P/%s + --name-only、@{upstream}..HEAD",
      BibDbGit.pushLogArguments() == ["log", "--reverse",
                                      "--format=%x1e%H%x1f%P%x1f%s", "--name-only",
                                      "@{upstream}..HEAD"])
let bibCommit1 = makePushCommit("1111111", subject: "bib1",
                                paths: ["bib_db/bibliography.jsonl"])
let bibCommit2 = makePushCommit("2222222", parents: ["1111111"], subject: "bib2",
                                paths: ["bib_db/bibliography.jsonl"])
let notesCommit = makePushCommit("3333333", parents: ["2222222"], subject: "notes 提交",
                                 paths: ["drafts/probe.md"])
let bibCommit3 = makePushCommit("4444444", parents: ["3333333"], subject: "bib3",
                                paths: ["bib_db/bibliography.jsonl"])
let mergeCommit = makePushCommit("5555555", parents: ["4444444", "deadbee"],
                                 subject: "merge", paths: [])
check("(o2) isBibOnly：全 bib_db/（含空提交）→ true；含 notes 文件 → false",
      BibDbGit.isBibOnly(bibCommit1)
      && BibDbGit.isBibOnly(makePushCommit("6666666", subject: "空提交", paths: []))
      && !BibDbGit.isBibOnly(makePushCommit("7777777", subject: "混",
                                            paths: ["bib_db/x", "notes/y.md"]))
      && !BibDbGit.isBibOnly(makePushCommit("8888888", subject: "notes",
                                            paths: ["notes/y.md"])))
check("(o2) isBibOnly：merge 提交一律 false（路径全 bib 也不算）",
      !BibDbGit.isBibOnly(mergeCommit)
      && BibDbGit.firstNonBibPath(makePushCommit("9999999", subject: "混",
                                                 paths: ["bib_db/x", "notes/y.md"]))
          == "notes/y.md")
check("(o2) planPush：全 bib → tip=最后一条 / count=2 / 无 remainder",
      BibDbGit.planPush(commits: [bibCommit1, bibCommit2], upstreamRemote: "origin",
                        upstreamBranch: "main")
          == .pushable(tip: bibCommit2, count: 2, remainder: []))
check("(o2) planPush：首条 notes → blocked（sha/subject/非 bib 路径都在）",
      BibDbGit.planPush(commits: [notesCommit, bibCommit3], upstreamRemote: "origin",
                        upstreamBranch: "main") == .blocked(notesCommit)
      && notesCommit.shortSha == "3333333"
      && notesCommit.subject == "notes 提交"
      && BibDbGit.firstNonBibPath(notesCommit) == "drafts/probe.md")
check("(o2) planPush：bib,notes,bib → 前缀 1 + remainder 2（后面的 bib 不代推）",
      BibDbGit.planPush(commits: [bibCommit1, notesCommit, bibCommit3],
                        upstreamRemote: "origin", upstreamBranch: "main")
          == .pushable(tip: bibCommit1, count: 1,
                       remainder: [notesCommit, bibCommit3]))
check("(o2) planPush：merge 首条 → blocked（不把另一父历史带过去）",
      BibDbGit.planPush(commits: [mergeCommit], upstreamRemote: "origin",
                        upstreamBranch: "main") == .blocked(mergeCommit))
check("(o2) planPush：bib 后 merge → 前缀在 merge 处截断",
      BibDbGit.planPush(commits: [bibCommit1, bibCommit2, mergeCommit],
                        upstreamRemote: "origin", upstreamBranch: "main")
          == .pushable(tip: bibCommit2, count: 2, remainder: [mergeCommit]))
let emptyBibCommit = makePushCommit("6666666", subject: "空提交", paths: [])
check("(o2) planPush：空日志 → nothingToPush；空提交也算 bib-only",
      BibDbGit.planPush(commits: [], upstreamRemote: "origin", upstreamBranch: "main")
          == .nothingToPush
      && BibDbGit.planPush(commits: [emptyBibCommit], upstreamRemote: "origin",
                           upstreamBranch: "main")
          == .pushable(tip: emptyBibCommit, count: 1, remainder: []))
check("(o2) pushArguments：refspec 限定 refs/heads/<branch>、无 force/-f",
      BibDbGit.pushArguments(remote: "origin", tip: "abcdef123456", branch: "main")
          == ["push", "origin", "abcdef123456:refs/heads/main"]
      && !BibDbGit.pushArguments(remote: "origin", tip: "abcdef123456", branch: "main")
          .contains("--force")
      && !BibDbGit.pushArguments(remote: "origin", tip: "abcdef123456", branch: "main")
          .contains("-f"))
check("(o2) upstreamArguments：rev-parse --abbrev-ref --symbolic-full-name @{upstream}",
      BibDbGit.upstreamArguments()
          == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
check("(o2) parseUpstream：origin/main、含 / 的分支、fatal/空 → nil",
      BibDbGit.parseUpstream("origin/main\n")?.remote == "origin"
      && BibDbGit.parseUpstream("origin/main\n")?.branch == "main"
      && BibDbGit.parseUpstream("origin/feature/x\n")?.branch == "feature/x"
      && BibDbGit.parseUpstream("fatal: no upstream configured for branch 'main'") == nil
      && BibDbGit.parseUpstream("") == nil)
check("(o2) looksLikeProxyFailure：代理死（Failed to connect … port）/ Could not resolve / CONNECT / Unsupported",
      BibDbGit.looksLikeProxyFailure(
          "fatal: unable to access 'https://github.com/x': Failed to connect to 127.0.0.1 port 10808 after 0 ms: Couldn't connect to server")
      && BibDbGit.looksLikeProxyFailure("fatal: Could not resolve proxy: socks5")
      && BibDbGit.looksLikeProxyFailure("error: proxy CONNECT aborted")
      && BibDbGit.looksLikeProxyFailure("fatal: Unsupported proxy syntax in config"))
check("(o2) looksLikeProxyFailure：普通失败不误判（rejected / HTTP 403 / 身份）",
      !BibDbGit.looksLikeProxyFailure("! [rejected] main -> main (non-fast-forward)")
      && !BibDbGit.looksLikeProxyFailure("fatal: unable to access 'https://github.com/x': The requested URL returned error: 403")
      && !BibDbGit.looksLikeProxyFailure("*** Please tell me who you are."))
check("(o2) proxyOverrideArguments：清 github proxy + http.proxy（-c 配对）",
      BibDbGit.proxyOverrideArguments()
          == ["-c", "http.https://github.com.proxy=", "-c", "http.proxy="])
check("(o2) PushResult 默认值：未推送/count 0/无 blocked·remainder/未重试/非空操作",
      { let r = BibDbGit.PushResult()
        return !r.pushed && r.pushedCount == 0 && r.blockedCommit == nil
            && r.remainderCommit == nil && !r.proxyRetried && !r.nothingToDo
            && r.upstream.isEmpty }())
let pushSrc = sourceSlice(gitSource, "static func push(db: String)",
                          "static func revertToPreviousCommit")
check("(o2) push 源码：planPush + refspec push + 代理重试都接线；绝不 force",
      pushSrc.contains("planPush(commits:") && pushSrc.contains("pushArguments(remote:")
      && pushSrc.contains("looksLikeProxyFailure") && pushSrc.contains("proxyOverrideArguments()")
      && !pushSrc.contains("--force"))
check("(o2) push 源码：无 upstream / log 坏输出 / blocked 都 fail-soft（不崩、不动本地）",
      pushSrc.contains("未设置 upstream") && pushSrc.contains("本地提交不受影响")
      && pushSrc.contains("无法解析") && pushSrc.contains("blockedCommit")
      && pushSrc.contains("nothingToDo"))

// ── (p) B-196：reviewFreshness 四态（stale 时写入口临时禁用）────────────────
print("\n── (p) B-196 新鲜度四态 ──")
// 用 DBManifest() 构造一份与 baseManifest 完全相同的当前清单（参考本文件现有夹具写法）。
var sameManifest = BibDbFeature.DBManifest()
sameManifest.path = baseManifest.path
sameManifest.exists = baseManifest.exists
sameManifest.size = baseManifest.size
sameManifest.mtime = baseManifest.mtime
sameManifest.sha256 = baseManifest.sha256
check("(p) reviewFreshness：current nil → .missingDB（库路径缺失/读不到）",
      BibDbFeature.reviewFreshness(cached: baseManifest, current: nil) == .missingDB)
check("(p) reviewFreshness：cached nil → .noBaseline（没成功加载过，不当作过期）",
      BibDbFeature.reviewFreshness(cached: nil, current: makeManifest()) == .noBaseline)
check("(p) reviewFreshness：两边都 nil → .missingDB（current 优先）",
      BibDbFeature.reviewFreshness(cached: nil, current: nil) == .missingDB)
check("(p) reviewFreshness：sha 变 → .stale（跨页写库后的实测形态）",
      BibDbFeature.reviewFreshness(
          cached: baseManifest,
          current: makeManifest(sha256: String(repeating: "b", count: 64))) == .stale)
check("(p) reviewFreshness：size 变 → .stale",
      BibDbFeature.reviewFreshness(cached: baseManifest,
                                   current: makeManifest(size: 6_000_001)) == .stale)
check("(p) reviewFreshness：mtime 变 → .stale",
      BibDbFeature.reviewFreshness(cached: baseManifest,
                                   current: makeManifest(mtime: 1_700_000_005)) == .stale)
check("(p) reviewFreshness：完全一致 → .fresh",
      BibDbFeature.reviewFreshness(cached: baseManifest, current: sameManifest) == .fresh)
check("(p) reviewFreshness：fresh ⇔ !isStale（判定口径只有一份）",
      (BibDbFeature.reviewFreshness(cached: baseManifest, current: makeManifest()) == .fresh)
      == !BibDbFeature.isStale(cached: baseManifest, current: makeManifest()))

// ── (o3) B-205：确认不同（不再提示）——引擎 argv/解析 + sidecar git + 视图接线 ──
print("\n── (o3) B-205 确认不同（不再提示）──")
let healthAckSource = readSource("Views/BibDbHealthAck.swift")
let sidecarRel = "bib_db/health-ignore.json"
let ackDB = "/tmp/b205/bib_db/bibliography.jsonl"
let ackArgs = BibDbFeature.ackDuplicatesArguments(db: ackDB, code: "near_duplicates",
                                                 keys: ["A2019", "B2020"],
                                                 reason: "用户确认不同条目（GUI；B-205）")
check("(o3) ackDuplicatesArguments：精确 argv（--apply --json、code/keys/reason 位置）",
      ackArgs == ["ack-duplicates", "--db", ackDB, "--code", "near_duplicates",
                  "--keys", "A2019,B2020", "--reason", "用户确认不同条目（GUI；B-205）",
                  "--apply", "--json"], "\(ackArgs)")
check("(o3) ackDuplicatesArguments：固定 --apply（GUI 只走真写）、无 --dry-run、--json 恰 1 个",
      ackArgs.first == "ack-duplicates" && ackArgs.contains("--apply")
      && !ackArgs.contains("--dry-run") && ackArgs.filter { $0 == "--json" }.count == 1)
let ackDefault = BibDbFeature.AckDuplicatesOutcome()
check("(o3) AckDuplicatesOutcome 默认值：未写/无 sidecar/计数 0/无 error",
      !ackDefault.added && !ackDefault.written && ackDefault.sidecarPath.isEmpty
      && ackDefault.entriesBefore == 0 && ackDefault.entriesAfter == 0
      && ackDefault.error == nil && ackDefault.seconds == 0)
let ackPayload: [String: Any] = [
    "command": "ack-duplicates", "dry_run": false, "db": ackDB,
    "sidecar_path": "/tmp/b205/bib_db/health-ignore.json",
    "code": "near_duplicates", "keys": ["A2019", "B2020"],
    "canonical_keys": ["A2019", "B2020"],
    "added": true, "written": true, "entries_before": 0, "entries_after": 1,
]
let ackParsed = BibDbFeature.parseAckDuplicates(ackPayload)
check("(o3) parseAckDuplicates：added/written/sidecar_path/entries_before→after",
      ackParsed.added && ackParsed.written
      && ackParsed.sidecarPath == "/tmp/b205/bib_db/health-ignore.json"
      && ackParsed.entriesBefore == 0 && ackParsed.entriesAfter == 1
      && ackParsed.error == nil)
let ackIdempotent = BibDbFeature.parseAckDuplicates(["added": false, "written": false,
                                                     "entries_before": 3,
                                                     "entries_after": 3])
check("(o3) parseAckDuplicates：幂等（added=false/written=false、条目数不变）",
      !ackIdempotent.added && !ackIdempotent.written
      && ackIdempotent.entriesBefore == 3 && ackIdempotent.entriesAfter == 3
      && ackIdempotent.error == nil)
let ackFailed = BibDbFeature.parseAckDuplicates(
    ["error": "sidecar 损坏：先修 bib_db/health-ignore.json"])
check("(o3) parseAckDuplicates：error 取 JSON error（缺键时其余取默认值）",
      ackFailed.error == "sidecar 损坏：先修 bib_db/health-ignore.json"
      && !ackFailed.added && !ackFailed.written && ackFailed.entriesAfter == 0)
let ackRunSrc = sourceSlice(modelSource, "static func ackDuplicates(db: String",
                            "// MARK: - 从路径推断来源期")
check("(o3) ackDuplicates 源码：runJSON + 精确 argv + parseAckDuplicates + failureText（同步）",
      ackRunSrc.contains("runJSON(ackDuplicatesArguments(")
      && ackRunSrc.contains("parseAckDuplicates(payload)")
      && ackRunSrc.contains("failureText(run)")
      && ackRunSrc.contains("existingExplicitDB(db)")
      && !ackRunSrc.contains("DispatchQueue") && !ackRunSrc.contains("--dry-run"),
      ackRunSrc.isEmpty ? "（源码切片为空：签名或 MARK 变了）" : "")
check("(o3) ackDuplicates 源码：引擎 rc2 优先用 JSON error、无 JSON 才 failureText",
      ackRunSrc.contains("guard let payload = run.json")
      && ackRunSrc.contains("out.error == nil && run.status != 0"))
check("(o3) healthIgnoreRelativePath = bib_db/health-ignore.json",
      BibDbGit.healthIgnoreRelativePath == sidecarRel)
check("(o3) health-ignore 三条 git 命令：pathspec 只该文件",
      BibDbGit.healthIgnoreStatusArguments() == ["status", "--porcelain", "--", sidecarRel]
      && BibDbGit.healthIgnoreAddArguments() == ["add", "--", sidecarRel]
      && BibDbGit.healthIgnoreCommitArguments(message: "m")
          == ["commit", "-m", "m", "--", sidecarRel]
      && BibDbGit.healthIgnoreCommitArguments(message: "m").filter { $0.contains("/") }
          == [sidecarRel])
check("(o3) healthIgnoreCommitMessage：`<keys>；B-205` 固定格式",
      BibDbGit.healthIgnoreCommitMessage(detail: "A2019≈B2020")
          == "引用库：确认不同（不再提示）（A2019≈B2020；B-205）")
check("(o3) healthIgnoreCommitMessage：空 detail 用 sidecar 路径兜底",
      BibDbGit.healthIgnoreCommitMessage(detail: "  ")
          == "引用库：确认不同（不再提示）（bib_db/health-ignore.json；B-205）")
let sidecarRange = gitSource.range(of: "static func commitHealthIgnore")
let sidecarCommitSrc = sidecarRange.map { String(gitSource[$0.upperBound...]) } ?? ""
check("(o3) commitHealthIgnore 源码：locateRepo→status 脏否→add→commit→HEAD（pathspec 只 sidecar）",
      sidecarCommitSrc.contains("locateRepo(db: db)")
      && sidecarCommitSrc.contains("healthIgnoreStatusArguments()")
      && sidecarCommitSrc.contains("isDBDirty(status.output)")
      && sidecarCommitSrc.contains("healthIgnoreAddArguments()")
      && sidecarCommitSrc.contains("healthIgnoreCommitArguments(message: message)")
      && sidecarCommitSrc.contains("headShaArguments()")
      && !sidecarCommitSrc.contains("dbRelativePath"),
      sidecarCommitSrc.isEmpty ? "（源码切片为空：函数名变了）" : "")
check("(o3) commitHealthIgnore 源码：无 push/fetch/reset/force（只本地提交）",
      !sidecarCommitSrc.contains("pushArguments(") && !sidecarCommitSrc.contains("\"push\"")
      && !sidecarCommitSrc.contains("fetch") && !sidecarCommitSrc.contains("reset")
      && !sidecarCommitSrc.contains("--force") && !sidecarCommitSrc.contains("--hard"))
check("(o3) commitHealthIgnore 源码：身份错误用 identityErrorText、无改动不空 commit（fail-soft）",
      sidecarCommitSrc.contains("isIdentityError(commit.output)")
      && sidecarCommitSrc.contains("identityErrorText")
      && sidecarCommitSrc.contains("guard isDBDirty(status.output) else")
      && sidecarCommitSrc.contains("queryUnpushedCount(repo: repo)"))
check("(o3) 新视图文件存在：BibDbHealthAckControl（按钮 + 二次确认 alert + 自查状态）",
      healthAckSource.contains("struct BibDbHealthAckControl: View")
      && healthAckSource.contains("Button(\"确认不同…\")")
      && healthAckSource.contains(".controlSize(.small)")
      && healthAckSource.contains("@State private var confirm")
      && healthAckSource.contains("@State private var busy")
      && healthAckSource.contains("@State private var notice")
      && healthAckSource.contains("@State private var warning"),
      healthAckSource.isEmpty ? "（文件尚未落盘：Views/BibDbHealthAck.swift）" : "")
check("(o3) 视图：AX 名字带 keys（走查唯一定位）+ help 提到 sidecar/git revert",
      healthAckSource.contains(
          ".accessibilityLabel(Text(\"确认不同 \\(keys.joined(separator: \"≈\"))\"))")
      && healthAckSource.contains("bib_db/health-ignore.json")
      && healthAckSource.contains("git revert"))
check("(o3) 视图：二次确认标题按 keys.count 动态（2 条原文案不变、组用「这些记录」）+ 按钮不变",
      healthAckSource.contains("keys.count == 2")
      && healthAckSource.contains(".alert(confirmTitle, isPresented: $confirm)")
      && healthAckSource.contains("\"确认这两条是不同条目？\"")
      && healthAckSource.contains("\"确认这些记录是不同条目？\"")
      && healthAckSource.contains("保持两条记录、不做合并")
      && healthAckSource.contains("保持这些记录为各自独立条目")
      && healthAckSource.contains("Button(\"取消\", role: .cancel)")
      && healthAckSource.contains("Button(\"确认不同（不再提示）\")")
      && healthAckSource.contains("不改 bibliography.jsonl")
      && healthAckSource.contains("不推送"))
check("(o3) 视图：后台 ackDuplicates(reason: 用户确认不同条目) → commitHealthIgnore → onFinished",
      healthAckSource.contains("BibDbFeature.ackDuplicates(")
      && healthAckSource.contains("reason: \"用户确认不同条目（GUI；B-205）\"")
      && healthAckSource.contains("BibDbGit.commitHealthIgnore(")
      && healthAckSource.contains("onFinished()")
      && healthAckSource.contains("DispatchQueue.global"))
check("(o3) 视图：busy/disabled 置灰、引擎失败黄字、提交失败 fail-soft 黄字",
      healthAckSource.contains(".disabled(disabled || busy)")
      && healthAckSource.contains("warning = \"确认不同失败（库与 sidecar 未改）：\" + error")
      && healthAckSource.contains("本地提交失败"))
check("(o3) ReviewView 接线：BibDbHealthAckControl(code/keys/disabled/onFinished 四项齐全)",
      reviewSource.contains("BibDbHealthAckControl(")
      && reviewSource.contains("code: healthAckCode(category.registry)")
      && reviewSource.contains("keys: pair")
      && reviewSource.contains("disabled: model.running || model.stale")
      && reviewSource.contains("onFinished: { model.refreshAfterWrite() }"))
check("(o3) ReviewView：registry→code 映射（duplicates→duplicate_content / near_duplicates）",
      reviewSource.contains(
          "registry == \"duplicates\" ? \"duplicate_content\" : \"near_duplicates\""))
check("(o3) ReviewView：按钮在「对照合并…」之后；说明文案同步「确认不同（不再提示）」",
      ({ () -> Bool in
          guard let merge = reviewSource.range(of: "Button(\"对照合并…\")"),
                let ack = reviewSource.range(of: "BibDbHealthAckControl(") else { return false }
          return merge.lowerBound < ack.lowerBound
      }())
      && reviewSource.components(separatedBy: "确认不同（不再提示）").count - 1 >= 3)

// ── (q) B-209：外部 bib → 规范 bib（只读预览；纯逻辑 + 源码接线）──────────
print("\n── (q) B-209 外部 bib → 规范 bib ──")
let rawNormalized = "\n@article{A2024,\n  title = {Secret Title α},\n}\n\n"
let normPayload: [String: Any] = [
    "entries_in": 3, "entries_out": 3,
    "changed_entries": 1, "unchanged_entries": 1, "kept_entries": 1,
    "normalized_text": rawNormalized,
    "entries": [
        [
            "key": "A2024", "entry_type": "article", "status": "changed",
            "changed_fields": [
                ["name": "title", "old": "Old Secret Value", "new": "New Secret Value"],
                ["name": "doi", "old": NSNull(), "new": "10.1000/secret"],
                ["name": "note", "old": "Drop Secret Value", "new": NSNull()],
            ],
            "notes": ["Secret Note"],
            "risks": [],
            "missing_required": ["year", "pages"],
        ],
        [
            "key": "B2020", "entry_type": "book", "status": "unchanged",
            "changed_fields": [], "notes": [], "risks": [], "missing_required": [],
        ],
        [
            "key": "C2019", "entry_type": "article", "status": "kept",
            "changed_fields": [],
            "notes": ["字段值含未配对引号：on"],
            "risks": ["字段值含未配对的引号，引擎原样保留"],
            "missing_required": [],
        ],
    ],
]
let norm = BibDbFeature.parseNormalizePayload(normPayload)
let firstNorm = norm.entries.first
check("(q) parseNormalizePayload：计数/entries/normalized_text 全解析",
      norm.entriesIn == 3 && norm.entriesOut == 3 && norm.changedEntries == 1
      && norm.unchangedEntries == 1 && norm.keptEntries == 1
      && norm.entries.count == 3
      && firstNorm?.key == "A2024" && firstNorm?.entryType == "article"
      && firstNorm?.status == "changed"
      && firstNorm?.changedFields.count == 3
      && firstNorm?.changedFields.first?.name == "title"
      && firstNorm?.changedFields.first?.old == "Old Secret Value"
      && firstNorm?.changedFields.first?.new == "New Secret Value"
      && norm.normalizedText == rawNormalized,
      "entries=\(norm.entries.count)")
check("(q) parseNormalizePayload：changed_fields 的 null old/new → 新增/删除",
      firstNorm?.changedFields.count == 3
      && firstNorm?.changedFields[1].old == nil
      && firstNorm?.changedFields[1].new == "10.1000/secret"
      && firstNorm?.changedFields[1].line.contains("（新增）") == true
      && firstNorm?.changedFields[2].new == nil
      && firstNorm?.changedFields[2].old == "Drop Secret Value"
      && firstNorm?.changedFields[2].line.contains("（删除）") == true)
check("(q) statusTitle：changed/unchanged/kept → 变化/未变/原样保留",
      norm.entries.map { $0.statusTitle } == ["变化", "未变", "原样保留"])
let keptNorm = norm.entries.first { $0.status == "kept" }
check("(q) kept 条目：kept_entries + risks 含「引号」",
      norm.keptEntries == 1 && keptNorm != nil
      && keptNorm?.risks.contains(where: { $0.contains("引号") }) == true
      && keptNorm?.notes.contains(where: { $0.contains("引号") }) == true)
check("(q) missingRequiredLine：缺必填：year、pages",
      firstNorm?.missingRequiredLine == "缺必填：year、pages"
      && firstNorm?.missingRequired == ["year", "pages"])
let emptyNorm = BibDbFeature.parseNormalizePayload(nil)
check("(q) parseNormalizePayload(nil)：不崩、空模型",
      emptyNorm.entries.isEmpty && emptyNorm.entriesIn == 0 && emptyNorm.entriesOut == 0
      && emptyNorm.changedEntries == 0 && emptyNorm.unchangedEntries == 0
      && emptyNorm.keptEntries == 0 && emptyNorm.normalizedText.isEmpty
      && emptyNorm.summaryLine == "条目 0（变化 0 / 未变 0 / 原样保留 0）")
let normLog = BibDbFeature.normalizeLogLines(norm)
let normLogText = normLog.joined(separator: "\n")
check("(q) normalizeLogLines：计数摘要 + 每条 key（状态）",
      normLog.first == "外部 bib 规范化：条目 3（变化 1 / 未变 1 / 原样保留 1）"
      && normLog.contains("  · A2024（变化）")
      && normLog.contains("  · B2020（未变）")
      && normLog.contains("  · C2019（原样保留）")
      && normLog.count == 4)
check("(q) normalizeLogLines 隐私：题名/字段值/notes/risks 文本不进日志",
      !normLogText.contains("Secret Title")
      && !normLogText.contains("Old Secret Value")
      && !normLogText.contains("New Secret Value")
      && !normLogText.contains("Drop Secret Value")
      && !normLogText.contains("10.1000/secret")
      && !normLogText.contains("Secret Note")
      && !normLogText.contains("引号"))
check("(q) 复制文本逐字节 == normalizedText（不 trim、不加 header/尾注）",
      Array(norm.normalizedText.utf8) == Array(rawNormalized.utf8)
      && norm.normalizedText == rawNormalized
      && !norm.normalizedText.contains("规范化")
      && norm.normalizedText.hasSuffix("\n\n"))
let normArgs = BibDbFeature.normalizeBibArguments(bibPath: "/tmp/x/ref.bib")
check("(q) normalizeBibArguments：精确 argv（--json、无 --db）",
      normArgs == ["normalize-bib", "--bib", "/tmp/x/ref.bib", "--json"]
      && !normArgs.contains("--db") && normArgs.filter { $0 == "--json" }.count == 1,
      "\(normArgs)")
let normFileSrc = sourceSlice(importSource, "static func normalizeExternalBib(bibPath",
                              "static func normalizeExternalBib(text")
let normTextSrc = sourceSlice(importSource, "static func normalizeExternalBib(text",
                              "static func parseNormalizePayload")
check("(q) 文件模式源码：同步 runJSON(normalize-bib) + failureText；不碰 dbPath/DispatchQueue",
      normFileSrc.contains("runJSON(normalizeBibArguments(bibPath: bib))")
      && normFileSrc.contains("failureText(run)")
      && normFileSrc.contains("parseNormalizePayload(payload)")
      && !normFileSrc.contains("dbPath")
      && !normFileSrc.contains("DispatchQueue"),
      normFileSrc.isEmpty ? "（源码切片为空：签名变了）" : "")
check("(q) 粘贴模式源码：SLCN-normalize-<UUID>/pasted-bib.bib + defer 清理；不打印正文",
      normTextSrc.contains("SLCN-normalize-")
      && normTextSrc.contains("pasted-bib.bib")
      && normTextSrc.contains("defer { try? FileManager.default.removeItem(at: dir) }")
      && normTextSrc.contains("normalizeExternalBib(bibPath: file.path)")
      && !normTextSrc.contains("Logger")
      && !normTextSrc.contains("print(")
      && !normTextSrc.contains("runJSON"),
      normTextSrc.isEmpty ? "（源码切片为空：签名变了）" : "")
let importViewSource = readSource("Views/BibDbImportView.swift")
check("(q) 入藏页入口：按钮标题精确 + sheet 接线（任何模式可见）",
      importViewSource.contains(
          "Button(\"外部 bib → 规范 bib…\") { showNormalizeSheet = true }")
      && importViewSource.contains(".sheet(isPresented: $showNormalizeSheet)")
      && importViewSource.contains("Text(\"外部 bib → 规范 bib（只读预览）\")"))
check("(q) sheet：来源「文件 / 粘贴」+ 四个按钮标题精确",
      importViewSource.contains("case file = \"文件\"")
      && importViewSource.contains("case paste = \"粘贴\"")
      && importViewSource.contains("Button(\"规范化预览\")")
      && importViewSource.contains("Button(\"复制规范文本\")")
      && importViewSource.contains("Button(\"送入入藏预览\")")
      && importViewSource.contains("Button(\"关闭\")"))
check("(q) 复制：NSPasteboard clearContents + setString(normalizedText, .string)",
      importViewSource.contains("NSPasteboard.general.clearContents()")
      && importViewSource.contains(
          "NSPasteboard.general.setString(result.normalizedText, forType: .string)"))
let sendNormSrc = sourceSlice(importViewSource, "private func sendNormalizedToImport",
                              "// MARK: - 哪条路进来的")
check("(q) 送入入藏预览：切 .paste + 写 pasteText + 显式 preview()（dry-run）",
      sendNormSrc.contains("model.mode = .paste")
      && sendNormSrc.contains("model.pasteText = text")
      && sendNormSrc.contains("preview()")
      && !sendNormSrc.contains("dryRun: false"),
      sendNormSrc.isEmpty ? "（源码切片为空：函数名变了）" : "")
check("(q) onChange 防清空：pasteChanged/mode 都以 previewMatchesInputs 兜底",
      importViewSource.contains("if previewMatchesInputs { return }")
      && importViewSource.contains("if !model.previewMatchesInputs { model.invalidatePreview() }"))
let sheetNormIndex = importViewSource.range(of: "struct BibDbNormalizeSheet: View")
let sheetNormSrc = sheetNormIndex.map { String(importViewSource[$0.lowerBound...]) } ?? ""
check("(q) sheet 切片：无 import/confirm 写库调用、无 dbPath、无 dryRun:false",
      !sheetNormSrc.isEmpty
      && sheetNormSrc.contains("BibDbFeature.normalizeExternalBib(")
      && !sheetNormSrc.contains("importPastedBib(")
      && !sheetNormSrc.contains("importBib(")
      && !sheetNormSrc.contains("confirm()")
      && !sheetNormSrc.contains("dbPath")
      && !sheetNormSrc.contains("dryRun: false"),
      sheetNormSrc.isEmpty ? "（源码切片为空：struct 名变了）" : "")
check("(q) 引擎契约接线：normalize-bib 只读（无 --apply/--db）",
      importSource.contains("\"normalize-bib\"")
      && !normFileSrc.contains("--apply") && !normTextSrc.contains("--apply"))

print("\nSUMMARY: \(pass) PASS / \(fail) FAIL")
exit(fail == 0 ? 0 : 1)
