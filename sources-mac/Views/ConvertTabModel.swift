import SwiftUI

/// 「初排转换」页的界面状态。
/// 用 ObservableObject 而不是 @State —— 运行在工作线程里，
/// 需要在逃逸闭包中回写 running，@State 在 struct 里做不到（self 不可变）。
final class ConvertModel: ObservableObject {
    @Published var options = ConvertFeature.Options()
    @Published var running = false
    @Published var showAdvanced = false
    /// 批63b：初排页「人工填写元数据」表单。值只在本次 App 会话内有效（不做持久化），
    /// 运行前写成 `/tmp` 临时 JSON 传给引擎 `--metadata-json`，跑完即删；
    /// 不填 = 与从前完全一样（默认 argv 逐字节不变）。
    @Published var metadataForm = MetadataForm()
    /// 表单 sheet 是否打开
    @Published var showMetadataForm = false
    @Published var metadataSources: [String] = []   // B-223：多份补充信息（顺序 = 合并顺序）
    @Published var showMetadataPreview = false
    @Published var metadataPreview: ConvertFeature.MetadataPreviewPayload? = nil
    /// U3：本次运行的取消句柄（运行中「开始转换」变成「取消」）
    var cancel: RunCancel? = nil

    // MARK: - 第 55 批：两段式「AI 协同初排」的界面状态机
    //
    // `coopStage` 由**扫描暂存目录**得出（只读引擎写的 `_AI协同状态.json`），
    // 是按钮组与状态文案的唯一依据：没有它 = 尚未初排；`phase=ph1` = 待人工核实
    // （**未交付**）；`phase=done` = 已交付。**绝不**把暂存目录当交付目录展示。

    /// 当前源稿在输出目录下匹配到的暂存目录（最近修改的那个）
    @Published var coopStage: CoopFeature.StageState? = nil
    /// 匹配到的候选数（>1 时界面提示「已选最近的」）
    @Published var coopCandidates = 0
    /// 「一键修改产物（--coop-apply）」—— 默认不勾；必须在跑第二段**之前**决定
    @Published var coopApply = false
    /// 第二段成功后才有的建议摘要（从 `<stem>_AI建议.jsonl` 统计；仅界面辅助信息）
    @Published var coopSuggestions: CoopFeature.SuggestionsSummary? = nil
    /// 协同记录文件（已交付且文件确实存在时非空）
    @Published var coopRecordPath = ""
    /// 最近一次协同运行的失败说明（退出码 + 日志尾部）
    @Published var coopFailure = ""
    // ---- 第 56 批：回填导入重构（多文件 / 目录 / 剪贴板 + 已选清单） ----
    /// 已选回填（文件/目录/剪贴板草稿可混合、可多次导入累加、可逐项移除/清空）。
    /// **不再折叠成父目录**：每一项按顺序各传一个 `--coop-replies`。
    @Published var replies = CoopFeature.RepliesSelection()
    /// 第二段成功后读 state.stats 的结果摘要（旧状态回退 record/旧 state）
    @Published var coopResult: CoopFeature.CoopResult? = nil
    /// 剪贴板草稿序号（每次粘贴递增，同秒重复粘贴也不会互相覆盖）
    var clipboardSequence = 0
}
