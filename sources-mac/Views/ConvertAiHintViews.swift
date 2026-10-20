import SwiftUI

/// 批63b 发布修复 R2（2026-10-20）：从 `ConvertTab` 大 ViewBuilder 上下文里拆出的两个说明块。
///
/// 背景：GitHub macos-14 runner 的旧 Xcode 类型检查比本机慢一个量级，`ConvertTab` 里
/// 「大 ViewBuilder + 多段字符串拼接」会被判超时。这里把 AI 说明与核实闸门提示独立成
/// 小 View（各自文件级上下文很小），`ConvertTab` 只保留 `VerifyGateHint(...)` /
/// `AiHintView(...)` 两处简单调用。**行为 / 文案 / 布局与拆分前一致**。
struct VerifyGateHint: View {
    @ObservedObject var bibdb: BibDbSettings

    private static let verifyAllSuffixText: String = "；已允许全量核实"
    private static let verifyLimitSuffixText: String = "；超过上限会先退出 2（成本闸门）"
    private static let verifyPlanSuffixText: String = "；**先算账**（本次只打印计划，不真正核实）"
    private static let verifyDbOffText: String = "引用库：本次**不查库**（--no-bibdb）。"
    private static let verifyDbMissingText: String =
        "引用库：已开启查库，但库文件不存在（引擎会跳过查库，不影响出稿）。"
    private static let verifyDbOnText: String =
        "引用库：核实前先查库（命中已核实条目的键不调检索/AI）。"
    private static let verifyGateSourceNoteText: String =
        "以上值来自「引用库 → 引用库设置」，点击转换时现取。"

    /// 第 132 条：核实成本闸门的**当前生效值**提示（只显示，不在这里改）。
    ///
    /// 为什么挨着「生成核实任务书」放：编辑勾这个开关时就该知道「这次会核多少条」。
    /// 超上限时引擎会**先退出 2**（有意为之的成本闸门）——这句提醒必须写在开关旁边，
    /// 而不是等跑完去日志里才看见（退出 2 看起来很像程序错误）。
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(verifyRangeLine())
                .font(.caption)
                .foregroundStyle(bibdb.verifyPlan ? Color.orange : Color.secondary)
            Text(verifyDbLine())
                .font(.caption)
                .foregroundStyle(.tertiary)
            Text(Self.verifyGateSourceNoteText)
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(.leading, 18)
        .fixedSize(horizontal: false, vertical: true)
    }

    private func verifyRangeLine() -> String {
        let allSuffix = bibdb.verifyAllRefs ? Self.verifyAllSuffixText : Self.verifyLimitSuffixText
        let planSuffix = bibdb.verifyPlan ? Self.verifyPlanSuffixText : ""
        return "核实范围：上限 \(bibdb.verifyMaxSummary)" + allSuffix + planSuffix
    }

    private func verifyDbLine() -> String {
        if !bibdb.lookupEnabled { return Self.verifyDbOffText }
        return verifyDbStateText()
    }

    private func verifyDbStateText() -> String {
        // 不用 `existingDBPath() == nil`：旧 Xcode 对「Optional == nil」的首次重载解析
        // 要花 ~10ms（会算到所在函数头上）；`guard let` 版本没有这笔开销。
        guard let path = BibDbFeature.existingDBPath() else { return Self.verifyDbMissingText }
        return path.isEmpty ? Self.verifyDbMissingText : Self.verifyDbOnText
    }
}

/// AI 开关下面那行说明（勾了但没配置 / 配好了各是什么行为）。
///
/// 勾了但没配置时**必须显式提示**（编辑部要求：不许静默忽略）：
/// 说清「本次只会生成《文献核实任务书》、不会调用任何模型」，并指到「设置」页。
/// 配好了则必须写清**它到底会做什么**（按事实改文案，2026-10-15）：
///   ① 它会调 API 生成修订后的 bib，并**并入交付的 `reference.bib`**；
///   ② 它的**目的**就是按任务书规范核实（第二节：优先用官方来源查证、比对卷期页码）；
///   ③ 第 69 条起，「能不能联网」由「设置」页的两个开关决定，文案必须**分开说**；
///   ④ 失败时的安全行为：调用失败 / 回复被截断 / 一条都没通过安全校验 → bib 一字节不改。
struct AiHintView: View {
    @ObservedObject var settings: AppSettings
    /// 只有勾了「用 AI 处理参考文献」才显示（原 `aiHint` 的 if 条件）。
    var usesAI: Bool
    /// 两段式勾着时多一条「本次不传 --llm-*」的橙色提示。
    var usesCoop: Bool

    @ViewBuilder var body: some View {
        if usesAI {
            aiCoopModeHint
            if settings.aiConfigured {
                aiConfiguredHints
            } else {
                aiNotConfiguredHint
            }
        }
    }

    @ViewBuilder private var aiCoopModeHint: some View {
        if usesCoop {
            // 第 55 批：协同模式下直连 API 全程不生效（`CoopFeature.prepare` 强制关闭），
            // 不许让「已配置 AI」这行绿色文案误导编辑以为本次会调 API。
            Text(ConvertTab.aiCoopInactiveText)
                .foregroundStyle(.orange)
        }
    }

    private var aiConfiguredHints: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(aiConfiguredSummaryLine())
                .foregroundStyle(.green)
            aiChannelHint
            aiSearchRefsHint
            Text(ConvertTab.aiSafetyText)
                .foregroundStyle(.secondary)
        }
        .font(.caption)
        .padding(.leading, 18)
        .fixedSize(horizontal: false, vertical: true)
    }

    private func aiConfiguredSummaryLine() -> String {
        "✔ 已配置 \(settings.aiSummary)（密钥 \(AppSettings.mask(settings.aiKey))）"
            + ConvertTab.aiConfiguredTailText
    }

    @ViewBuilder private var aiChannelHint: some View {
        if settings.aiEngineKnobs.usesWebSearch {
            Text(ConvertTab.aiWebSearchOnText)
                .foregroundStyle(.green)
            Text(ConvertTab.aiWebSearchCaveatText)
                .foregroundStyle(.orange)
        } else if settings.aiProtocol == AIProtocol.responses {
            Text(ConvertTab.aiResponsesNoSearchText)
                .foregroundStyle(.orange)
        } else {
            Text(ConvertTab.aiChatNoSearchText)
                .foregroundStyle(.orange)
        }
    }

    @ViewBuilder private var aiSearchRefsHint: some View {
        if settings.aiSearchRefs {
            Text(ConvertTab.aiSearchRefsText)
                .foregroundStyle(.green)
        }
    }

    private var aiNotConfiguredHint: some View {
        Text(ConvertTab.aiNotConfiguredText)
            .font(.caption)
            .foregroundStyle(.orange)
            .padding(.leading, 18)
    }
}
