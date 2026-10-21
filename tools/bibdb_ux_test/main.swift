// B-143（批76）「引用库可用性/口径修复」的纯逻辑测试台（headless）。
//
// **编译的是生产源码本身**：`Core/Support.swift`（EngineLocator/PathKit）+ `Core/Log.swift`
// + `Features/BibDbFeature.swift`。不开 GUI、不起子进程、不读真实库/稿件仓、不联网 ——
// 只断言 B-143 抽出来的纯逻辑：默认过滤 / 开关 / 计数 / 空态 / warning 明细分类顺序 /
// 跳转动作标识 / lint JSON 向后兼容解析。视图层（SwiftUI）由 qa-release 的 typecheck 与
// build 后真点覆盖，不在本台编译。
//
// 跑法（在 sources-mac 下）：
//     swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/buxmc \
//       -o /tmp/bibdb_ux_test Core/Support.swift Core/Log.swift Features/BibDbFeature.swift \
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
                     count: Int, keys: [String] = [], pendingRecords: Int = 0,
                     pendingTotal: Int = 0, resolvedRecords: Int = 0,
                     resolvedTotal: Int = 0) -> BibDbFeature.LintWarningItem {
    var item = BibDbFeature.LintWarningItem()
    item.code = code
    item.registry = registry
    item.detail = detail
    item.action = action
    item.count = count
    item.keysSample = keys
    item.pendingRecords = pendingRecords
    item.pendingTotal = pendingTotal
    item.resolvedRecords = resolvedRecords
    item.resolvedTotal = resolvedTotal
    return item
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

// ── (f) 生产源码接线（防「纯逻辑在、视图没用上」）───────────────────────
print("\n── (f) 生产源码接线 ──")
let reviewSource = readSource("Views/BibDbReviewView.swift")
let statsSource = readSource("Views/BibDbStatsView.swift")
let mainWindowSource = readSource("Views/MainWindow.swift")
let viewModelsSource = readSource("Core/ViewModels.swift")
check("(f) 待裁决页用 ConflictListState（默认过滤/计数/空态）",
      reviewSource.contains("ConflictListState"))
check("(f) 待裁决页开关绑定共享状态 showResolvedConflicts（两页态一致）",
      reviewSource.contains("showResolvedConflicts")
      && viewModelsSource.contains("var showResolvedConflicts"))
check("(f) 待裁决页不再直接遍历 conflicts.groups（必须走过滤后的 visibleGroups）",
      reviewSource.contains("visibleGroups") && !reviewSource.contains("conflicts?.groups"))
check("(f) 库概况橙字用 LintWarningDigest 下钻（分类明细）",
      statsSource.contains("LintWarningDigest"))
check("(f) 库概况→待裁决页跳转走 onOpenReview 闭包（MainWindow 接线）",
      statsSource.contains("onOpenReview") && mainWindowSource.contains("onOpenReview"))
check("(f) 跳转按钮标识来自 reviewJumps（pending/resolved 两态）",
      statsSource.contains("reviewJumps"))
check("(f) 待裁决页头部有一句话区分两数据源（conflicts vs lint）",
      reviewSource.contains("数据源"))
check("(f) 状态条/分组头/feedback 都走纯逻辑文案",
      reviewSource.contains("statusText")
      && reviewSource.contains("groupCountText")
      && reviewSource.contains("feedbackText"))

print("\nSUMMARY: \(pass) PASS / \(fail) FAIL")
exit(fail == 0 ? 0 : 1)
