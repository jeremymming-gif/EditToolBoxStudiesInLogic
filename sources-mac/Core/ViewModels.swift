import Foundation
import Combine

/// 本机只安装了 Xcode Command Line Tools（没有完整 Xcode），
/// 因此 SwiftUI 的 @State 宏插件不可用。
/// 这里统一用 ObservableObject 承载界面状态，配合 @StateObject 使用。
///
/// 如果以后安装了完整 Xcode，可以把这些类换回 @State，代码会更简洁。

/// 「一校核查」页的界面状态（第 92 条）。
///
/// 为什么不是一个 `@State`：本机只有 Command Line Tools，SwiftUI 的 `@State` 宏插件不可用，
/// 所以界面状态一律用 `ObservableObject` 承载（见本文件开头那段说明）。
final class ProofCheckModel: ObservableObject {
    /// 初排稿 .tex 路径
    @Published var tex: String = ""
    /// 是否把「无歧义」的命中另存为新文件（输入文件不动）
    @Published var fix: Bool = false
    /// 语种：`自动` / `zh` / `en`（`自动` 交给引擎按模板字段判）
    @Published var lang: String = "自动"
    @Published var running: Bool = false
    /// U3：本次运行的取消句柄（运行中主按钮变成「取消」）
    var cancel: RunCancel? = nil
}

/// 通用功能页的界面状态：一个路径输入框 + 一个「运行中」标记
final class PathRunnerModel: ObservableObject {
    @Published var path: String = ""
    @Published var running: Bool = false
    // ── 完成横幅（UI 方案第 1 件）──
    /// 跑完的状态：nil = 还没跑过；true = 成功；false = 失败/有错误
    @Published var resultOK: Bool? = nil
    /// 从契约行里抠出的产物路径（点「在访达中显示」/「复制路径」用）
    @Published var resultPath: String? = nil
    /// 用时（秒）
    @Published var resultSeconds: Double = 0
    /// 本次运行的开始时间（运行中算"已用 X 秒"）
    @Published var startedAt: Date? = nil
    /// U3：本次运行是否是**用户主动取消**（完成横幅据此显示「⏹ 已取消」而不是 ❌ 失败）
    @Published var resultCancelled: Bool = false
    /// U3：本次运行的取消句柄（运行中主按钮变成「取消」）
    var cancel: RunCancel? = nil
}

// MARK: - AI 的两个「请求体旋钮」（设置页 → 引擎）

/// 「通道」二态 → 环境变量 `SLCN_LLM_PROTOCOL`（/ `--llm-protocol`）。
///
/// 为什么要有这个开关（2026-10-15 用户决定）：只有 **Responses API**（`{base}/responses`）
/// 才有「服务端内置工具」这个概念，联网检索（`tools:[{"type":"web_search"}]`）必须走它；
/// chat 通道（`/chat/completions`）的 `tools` 是 **function calling**，要客户端自己执行，
/// **不是检索**。默认 `chat`：老行为一字不改（请求体与从前逐字节相同）。
///
/// ⚠️ 开了 responses **不等于**能联网：内置 `web_search` 是否真检索由端点与模型决定
/// （DeepSeek 需 `deepseek-v4-pro`；`deepseek-flash` 仍忽略该工具、不报错也不检索），
/// 所以「设置」页的 ⑤ 检索探针会**实测**端点到底会不会查，结论如实显示——
/// 本工具绝不假装已联网。
enum AIProtocol: String, CaseIterable, Identifiable {
    case chat = "chat"
    case responses = "responses"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .chat: return "chat（/chat/completions，默认）"
        case .responses: return "responses（/responses，支持服务端检索工具）"
        }
    }

    /// 传给引擎的命令行/环境变量值
    var envValue: String { rawValue }

    /// 从 ai.json 读回；认不出（旧文件没这个键 / 手改坏）一律回落 `chat`。
    init(stored: Any?) {
        self = (stored as? String).flatMap(AIProtocol.init(rawValue:)) ?? .chat
    }
}

/// 「思考模式」三态 → 环境变量 `SLCN_LLM_THINKING`。
///
/// 为什么默认是「跟随端点默认」而不是「开启」：官方文档（2026-10）写明 `thinking`
/// **默认就是 enabled**，显式发 `enabled` 只等于把默认值抄一遍，却会让请求体不再与
/// 从前逐字节相同 —— 老端点/自建服务多收到一个不认识的字段可能直接 400
/// （`response_format` 当年就是这么把整条 AI 通道打挂的）。要关就显式选「关闭」。
enum AIThinkingMode: String, CaseIterable, Identifiable {
    /// 不发送该字段（请求体与从前逐字节相同）
    case followEndpoint = "follow"
    /// `{"type":"disabled"}`：纯抽取/规范化任务推荐
    case disabled = "off"
    /// `{"type":"enabled"}`：显式表态，值同端点默认
    case enabled = "on"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .followEndpoint: return "跟随端点默认（不发送该字段）"
        case .disabled: return "关闭（推荐：纯抽取任务）"
        case .enabled: return "开启"
        }
    }

    /// 传给引擎子进程的值：`nil` = **不设置** `SLCN_LLM_THINKING`
    /// （= 引擎 `request_params_from_env()` 口径里的「不发该字段」）。
    var envValue: String? {
        switch self {
        case .followEndpoint: return nil
        case .disabled: return "0"
        case .enabled: return "1"
        }
    }

    /// 从 ai.json 读回。**认不出的值（旧文件没这个键 / 将来手工改坏）一律回落默认**，
    /// 绝不让读配置失败。
    init(stored: Any?) {
        self = (stored as? String).flatMap(AIThinkingMode.init(rawValue:)) ?? .followEndpoint
    }
}

/// 「输出上限」输入框的解析口径（纯函数，headless 可验证）。
///
/// 与引擎 `request_params_from_env()` 的 `SLCN_LLM_MAX_TOKENS` 口径**故意不完全一样**：
/// 引擎拿到非整数会「按默认 2048 处理 + stderr 一条说明」，界面这边**直接拒绝保存**
/// （`saveAI()` 返回 false + 红字提示）—— 编辑在界面上填错就该当场知道，
/// 而不是等转换完去日志框里翻那一条 stderr。真正跑转换时若值仍然非法
/// （例如界面里填了非法值又直接点转换），`ConvertFeature` 也**不静默**：不设该变量 + 日志一行说明。
enum AIMaxTokens {
    /// 输入框默认值 = 引擎的 `_DEFAULT_MAX_TOKENS`
    static let defaultText = "2048"

    enum Parsed: Equatable {
        /// 留空 → **不设置** `SLCN_LLM_MAX_TOKENS`（引擎用自己的默认 2048）
        case unset
        /// 合法整数（已规范化为十进制文本；`"0"` = 不封顶）
        case value(String)
        /// 非整数 / 负数 → 拒绝，并附一句中文提示
        case invalid(String)
    }

    static func parse(_ raw: String) -> Parsed {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return .unset }
        // 只认十进制整数：`1e3` / `2048.0` / `2_048` / 溢出都不认 —— 引擎的 int()
        // 同样不认，但引擎会**静默**回落到 2048，所以界面必须当场说清楚。
        guard let n = Int(t) else {
            return .invalid("输出上限必须是整数（留空 = 用引擎默认 2048；0 = 不封顶），"
                            + "现在填的是「\(t)」—— 转换时不会发送这个值。")
        }
        guard n >= 0 else {
            return .invalid("输出上限不能是负数（要「不封顶」请填 0），现在填的是「\(t)」。")
        }
        return .value(String(n))
    }

    /// 环境变量值：`nil` = 不设置该变量（留空或非法都按「不设置」处理）
    static func envValue(_ raw: String) -> String? {
        if case .value(let v) = parse(raw) { return v }
        return nil
    }

    /// 非法输入的中文提示；合法时为 nil（界面只在非 nil 时显示红字）
    static func hint(_ raw: String) -> String? {
        if case .invalid(let h) = parse(raw) { return h }
        return nil
    }
}

/// 两个**高级**文本旋钮的取值口径（第 70 条；纯函数，headless 可验证）。
///
/// 为什么要有这两个旋钮：`slcn/llm.py` 里原本把工具类型名 `web_search` 与路径
/// `/responses` 写死了，而各家实现并不一致（DeepSeek 的 flash 仍忽略内置检索工具、
/// v4-pro 已支持）—— 名字未必都叫 `web_search`（OpenAI 自己就有 `web_search_preview`），
/// 路径也可能不是 `/responses`。做成配置之后，服务商一改，编辑改这两栏即可，**不必等新版本**。
///
/// 口径（与 `AIMaxTokens` 同一条纪律：不静默）：
///   · **留空 = 不传给引擎**（引擎用自己的默认 `web_search` / `/responses`），
///     所以两栏都空时命令行与环境变量与从前**逐字节相同**；
///   · 填了但**结构上不可能是**合法值 → 同样**不传**，并由 `hint()` 出一行中文提示。
///     为什么不直接拒绝保存：这两栏是次要旋钮，默认空着就能用，「不传 = 用默认」
///     本身就是一个正确结果；引擎侧同样是「回落默认 + stderr 一条说明」。
enum AIAdvancedKnobs {
    /// 引擎 `slcn/llm.py: DEFAULT_SEARCH_TOOL` —— 工具名留空时它用的值（界面提示用）
    static let defaultSearchTool = "web_search"
    /// 引擎 `slcn/llm.py: DEFAULT_RESPONSES_PATH` —— 路径留空时它用的值（界面提示用）
    static let defaultResponsesPath = "/responses"

    enum Parsed: Equatable {
        /// 留空 → **不传**（引擎取默认）
        case unset
        /// 合法值 → 原样传给引擎（路径缺前导 `/` 由引擎补上，界面不替它编地址）
        case value(String)
        /// 结构上不可能是合法值 → 不传 + 中文提示
        case invalid(String)
    }

    /// 工具类型名：字母/数字开头，其余只允许 `_ - . :`
    /// （与引擎 `_TOOL_NAME_RE` 同一口径 —— 两份实现必须一起改，测试钉着这一点）。
    static func parseSearchTool(_ raw: String) -> Parsed {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return .unset }
        let scalars = t.unicodeScalars
        let headOK = scalars.first.map { CharacterSet.alphanumerics.contains($0) } ?? false
        let bodyOK = scalars.allSatisfy {
            CharacterSet.alphanumerics.contains($0) || "_-.:".unicodeScalars.contains($0)
        }
        guard headOK, bodyOK else {
            return .invalid("工具名只能由字母/数字与 _ - . : 组成、且以字母或数字开头，"
                            + "现在填的是「\(t)」—— 本次**不会**把它发给引擎（用默认 "
                            + defaultSearchTool + "）。")
        }
        return .value(t)
    }

    /// responses 路径：原样传给引擎（缺前导 `/` 由引擎补，末尾 `/` 由引擎去掉），
    /// 只要不含空白 / `://` / 引号 / 花括号 / 逗号 —— 那些结构上不可能是路径。
    static func parseResponsesPath(_ raw: String) -> Parsed {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return .unset }
        let hasSpace = t.unicodeScalars.contains {
            CharacterSet.whitespacesAndNewlines.contains($0)
        }
        let bad = hasSpace || t.contains("://") || t.contains("\"") || t.contains("'")
            || t.contains("{") || t.contains("}") || t.contains(",")
        guard !bad else {
            return .invalid("路径里不许有空白 / :// / 引号 / 花括号 / 逗号，"
                            + "现在填的是「\(t)」—— 本次**不会**把它发给引擎（用默认 "
                            + defaultResponsesPath + "）。")
        }
        return .value(t)
    }

    /// 传给引擎的值：`nil` = **不传这个参数**（留空或非法都按「用引擎默认」处理）
    static func envValue(_ parsed: Parsed) -> String? {
        if case .value(let v) = parsed { return v }
        return nil
    }

    /// 非法输入的中文提示；合法/留空时为 nil（界面只在非 nil 时显示红字）
    static func hint(_ parsed: Parsed) -> String? {
        if case .invalid(let h) = parsed { return h }
        return nil
    }
}

/// 传给引擎子进程的**请求体选项**（不是连接参数）。
///
/// 为什么单独成结构、而不是塞进「地址/密钥/模型」那一组：
/// 那三个回答「连哪个端点」，这几个回答「请求体里带什么」—— 只有真正要跑 AI
/// （勾了「用 AI 处理参考文献」）时才有意义，所以也只在那个时候进环境变量/命令行。
struct AIEngineKnobs: Equatable {
    var thinking: AIThinkingMode = .followEndpoint
    /// **原样**保存用户输入（解析见 `AIMaxTokens`），默认 `"2048"`
    var maxTokens: String = AIMaxTokens.defaultText
    /// 通道：`chat`（默认，逐字节老行为）/ `responses`
    var apiProtocol: AIProtocol = .chat
    /// 是否启用**服务端联网检索**（默认关；只在 `responses` 通道生效）
    var webSearch: Bool = false
    /// 检索工具的**类型名**（第 70 条 → 引擎 `SLCN_LLM_SEARCH_TOOL` / `--llm-search-tool`）。
    /// **留空 = 不传这个参数**，引擎用默认 `web_search`（于是命令行逐字节不变）。
    var searchTool: String = ""
    /// responses 的**路径**（第 70 条 → 引擎 `SLCN_LLM_RESPONSES_PATH` / `--llm-responses-path`）。
    /// **留空 = 不传这个参数**，引擎用默认 `/responses`。
    var responsesPath: String = ""
    /// P2（第 72 条起）：是否启用「参考文献检索证据」（DeepSeek 独立 Search API，
    /// 引擎 `--search-refs`；**默认关**）。它与 `webSearch` 是**两件事**：
    /// 后者是模型内置检索工具；前者是工具自己先检索、把证据注入提示词、并在合并 bib
    /// 前校验 `% 来源：` URL——不依赖服务商/模型，但按次计费（¥5/1000 次成功）。
    var searchRefs: Bool = false

    /// 本组合**实际**会不会带检索工具（chat 通道下即使勾了也不会）。
    var usesWebSearch: Bool { apiProtocol == .responses && webSearch }

    /// 两个「未来兼容」旋钮的一行说明（第 70 条）：**只有填了才出现**；
    /// 两栏都空时返回空串，`summary` 与从前逐字节相同。
    var advancedSummary: String {
        var parts: [String] = []
        if let v = AIAdvancedKnobs.envValue(AIAdvancedKnobs.parseSearchTool(searchTool)) {
            parts.append("工具名 \(v)")
        }
        if let v = AIAdvancedKnobs.envValue(AIAdvancedKnobs.parseResponsesPath(responsesPath)) {
            parts.append("路径 \(v)")
        }
        return parts.isEmpty ? "" : "；" + parts.joined(separator: "，")
    }

    /// 一行摘要（界面/日志用，**不含密钥**）
    var summary: String {
        let t = "通道 " + apiProtocol.rawValue
            + (webSearch
               ? (apiProtocol == .responses ? " + 联网检索**已启用**" : "（勾了联网检索，但 chat 通道不带检索工具）")
               : "（未启用联网检索：模型只能凭记忆）")
        let th = "；思考模式 \(thinking.title)"
        let core: String
        switch AIMaxTokens.parse(maxTokens) {
        case .unset: core = t + th + "；输出上限 未设置（引擎默认 2048）"
        case .value(let v): core = t + th + "；输出上限 " + (v == "0" ? "0（不封顶）" : v)
        case .invalid(let h): core = t + th + "；⚠ " + h
        }
        return core + advancedSummary
    }
}

/// 「设置」页的**预设**：一键把「API 地址 + 模型名」填成常见服务商的值。
///
/// 只是填表快捷方式 —— 填完之后两个输入框照样可以改，预设**永远不碰密钥**。
/// 地址一律带服务商的版本路径（`…/v1`）：引擎 `slcn/llm.py` 是
/// `base.rstrip("/") + "/chat/completions"`，少一段版本路径就会 404。
///
/// ⚠️ 模型名是**会变**的（DeepSeek 官方 2026-10 就已把 `deepseek-chat` /
/// `deepseek-reasoner` 退役，换成 `deepseek-flash` / `deepseek-v4-pro`），
/// 所以界面上的帮助文字写明「以服务商文档为准」。
enum AIProviderPreset: String, CaseIterable, Identifiable {
    case deepseek = "deepseek"
    case qwen = "qwen"
    case glm = "glm"
    case kimi = "kimi"
    case custom = "custom"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .deepseek: return "DeepSeek"
        case .qwen: return "通义千问"
        case .glm: return "智谱 GLM"
        case .kimi: return "Kimi / Moonshot"
        case .custom: return "自定义（不动输入框）"
        }
    }

    /// 要填进「API 地址」的值；`nil` = 自定义，什么都不动
    var base: String? {
        switch self {
        case .deepseek: return "https://api.deepseek.com/v1"
        case .qwen: return "https://dashscope.aliyuncs.com/compatible-mode/v1"
        case .glm: return "https://open.bigmodel.cn/api/paas/v4"
        case .kimi: return "https://api.moonshot.cn/v1"
        case .custom: return nil
        }
    }

    /// 要填进「模型名」的值；`nil` = 自定义，什么都不动
    var model: String? {
        switch self {
        case .deepseek: return "deepseek-flash"
        case .qwen: return "qwen-plus"
        case .glm: return "glm-4"
        case .kimi: return "moonshot-v1-8k"
        case .custom: return nil
        }
    }

    /// 把预设套到现有的「地址 / 模型名」上（纯函数，headless 可验证）。
    ///
    /// ⚠️ 刻意**不接受、也不返回密钥**：换预设永远不该动密钥 ——
    /// 密钥是编辑自己粘进去的，从类型上就让它碰不到（`SettingsTab.apply` 也只写这两个字段）。
    func applied(base: String, model: String) -> (base: String, model: String) {
        (self.base ?? base, self.model ?? model)
    }
}

/// 一套**保存过的 AI 配置方案**（第 82 条）。
///
/// 一份方案 = 「设置」页上那一整套字段：地址 / 密钥 / 模型名 / 思考模式 / 输出上限 /
/// 通道 / 联网检索 / 检索工具名 / 响应路径。编辑常要在几套之间来回换
/// （本机自建端点一套、云端一套、给同事演示再一套），从前只有一份、一覆盖就没了。
///
/// ⚠️ **含密钥**：与顶层那份一起写在同一个 `0600` 文件里；`mask()` 的脱敏只作用于界面。
/// ⚠️ 用 `String` 存 thinking / protocol 的 `rawValue`：认不出的值在 `init(dict:)` 里
///    回落默认，**绝不让读配置失败**（与顶层那份同一口径）。
struct AIProfile: Identifiable, Equatable {
    /// 方案名（也当 id 用）。界面保证非空、去两端空白、同名视为**覆盖**而非新增。
    var name: String
    var base: String = ""
    var key: String = ""
    var model: String = ""
    var thinking: String = AIThinkingMode.followEndpoint.rawValue
    var maxTokens: String = AIMaxTokens.defaultText
    var protocolRaw: String = AIProtocol.chat.rawValue
    var webSearch: Bool = false
    var searchTool: String = ""
    var responsesPath: String = ""
    var searchRefs: Bool = false

    var id: String { name }

    /// 存盘用的字典（键名与顶层那份**完全一致**，便于人读与手改）。
    /// `searchRefs` 只在**开启**时写（默认关 = 与旧 ai.json 逐字节相同）。
    func toDict() -> [String: Any] {
        var d: [String: Any] = [
            "name": name,
            "base": base,
            "key": key,
            "model": model,
            "thinking": thinking,
            "maxTokens": maxTokens,
            "protocol": protocolRaw,
            "webSearch": webSearch,
            "searchTool": searchTool,
            "responsesPath": responsesPath,
        ]
        if searchRefs { d["searchRefs"] = true }
        return d
    }

    /// 从盘上的字典还原。**宽容**：名字不是非空字符串就返回 nil（该条作废），
    /// 其余字段类型不对一律回落默认值。
    init?(dict: [String: Any]) {
        guard let raw = dict["name"] as? String else { return nil }
        let nm = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !nm.isEmpty else { return nil }
        name = nm
        base = (dict["base"] as? String) ?? ""
        key = (dict["key"] as? String) ?? ""
        model = (dict["model"] as? String) ?? ""
        thinking = (dict["thinking"] as? String) ?? AIThinkingMode.followEndpoint.rawValue
        maxTokens = (dict["maxTokens"] as? String) ?? AIMaxTokens.defaultText
        protocolRaw = (dict["protocol"] as? String) ?? AIProtocol.chat.rawValue
        webSearch = (dict["webSearch"] as? Bool) ?? false
        searchRefs = (dict["searchRefs"] as? Bool) ?? false
        searchTool = (dict["searchTool"] as? String) ?? ""
        responsesPath = (dict["responsesPath"] as? String) ?? ""
    }

    /// 从当前界面字段造一份方案（`key` 是唯一会被整份带走的敏感字段）。
    /// ⚠️ 形参不能叫 `protocol`（Swift 关键字，`protocol.rawValue` 会被当成声明解析），
    /// 所以这里叫 `proto`。
    init(name: String, base: String, key: String, model: String,
         thinking: AIThinkingMode, maxTokens: String,
         proto: AIProtocol, webSearch: Bool,
         searchTool: String, responsesPath: String, searchRefs: Bool = false) {
        self.name = name
        self.base = base
        self.key = key
        self.model = model
        self.thinking = thinking.rawValue
        self.maxTokens = maxTokens
        self.protocolRaw = proto.rawValue
        self.webSearch = webSearch
        self.searchTool = searchTool
        self.responsesPath = responsesPath
        self.searchRefs = searchRefs
    }
}

/// 全局选项
///
/// 三个勾选项**默认都是选中的** —— 实际工作流里它们几乎每次都要用，
/// 默认不勾的话容易漏（尤其是「合并编委信息页」和「封底页」，
/// 漏了就要手工往 PDF 里补一页）。
/// 每次启动都是这个默认值，不会记住上次的手动取消，避免"上次取消了、这次忘了勾回来"。
///
/// AI 配置（API 地址 / 密钥 / 模型）与上面三个勾选项不同：**要持久化**。
/// 编辑配一次就该一直有效，不该每次重启重填。存放位置与 `EditorStore` 同目录：
/// `~/Library/Application Support/cn.edu.sysu.logic.toolbox/ai.json`，文件权限收紧到
/// **0600**（仅当前用户可读写）。
///
/// ⚠️ 密钥以**明文**存在本机用户目录（macOS 没有免 Keychain 的加密方案，
/// 用 Keychain 又要处理签名/迁移，对编辑部这个场景过重）。因此：
///   1. 「设置」页有醒目提示：请勿共享或同步该文件；
///   2. **任何日志/界面都只出现前 4 位**（见 `AppSettings.mask`），
///      完整密钥不打印、不写日志（日志常被截图、粘贴到群里）。
final class AppSettings: ObservableObject {
    /// 合并时是否把各篇文章自带的小节书签挂到该篇文章之下
    @Published var nestOutlines: Bool = true

    /// 合并时是否把编委信息页插到扉页之后
    @Published var mergeEditorsPage: Bool = true

    /// 合并时是否把封底页（cover+bottom…）放在最后
    @Published var appendBackCover: Bool = true

    // MARK: - AI（OpenAI 兼容接口；持久化到 ai.json）

    /// API 地址：OpenAI 兼容的 base URL，如 https://api.deepseek.com/v1
    @Published var aiBase = ""
    /// API 密钥。**可以留空** —— 本机/自建端点（LM Studio 等）不需要密钥；
    /// 留空时请求头发 `Bearer none`，与引擎 `slcn/llm.py` 的行为一致。
    @Published var aiKey = ""
    /// 模型名，如 deepseek-flash（模型名会变，以服务商文档为准）
    @Published var aiModel = ""
    /// 「设置」页的状态行（保存/读取结果、文件权限）
    @Published var aiStatus = ""

    /// 思考模式（三态）→ 勾了「用 AI 处理参考文献」时经 `SLCN_LLM_THINKING` 传给引擎
    @Published var aiThinking: AIThinkingMode = .followEndpoint
    /// 输出上限：**原样**保存用户输入（默认 "2048"）。留空 = 不设置该环境变量，
    /// `0` = 不封顶；非整数由 `saveAI()` 拒绝（见 `AIMaxTokens`）。
    @Published var aiMaxTokens: String = AIMaxTokens.defaultText

    /// 通道（chat / responses）→ 勾了「用 AI 处理参考文献」时经 `--llm-protocol` 传给引擎。
    @Published var aiProtocol: AIProtocol = .chat
    /// 联网检索开关 → 勾了 AI 时经 `--llm-web-search` 传给引擎（默认关）。
    /// ⚠️ 勾上**不代表**端点真的会检索：内置 `web_search` 是否生效由端点与模型决定
    /// （DeepSeek 的 `deepseek-flash` 会静默忽略、`deepseek-v4-pro` 才真检索），
    /// 所以「设置」页的 ⑤ 探针要实测，GUI 与引擎都会如实写明。
    @Published var aiWebSearch: Bool = false

    /// P2（第 72 条起）：文献检索证据开关 → 勾了 AI 时经 `--search-refs` 传给引擎
    /// （默认关；DeepSeek 独立 Search API，按次计费）。它**不依赖**端点是否支持内置检索。
    @Published var aiSearchRefs: Bool = false

    /// 检索工具的类型名（第 70 条）。**留空 = 不传 `--llm-search-tool`**，引擎用默认
    /// `web_search`（于是命令行逐字节不变）；填了才传，供将来服务商换名字时使用。
    @Published var aiSearchTool: String = ""
    /// responses 的路径（第 70 条）。**留空 = 不传 `--llm-responses-path`**，引擎用默认
    /// `/responses`；填了才传，供将来服务商换路径时使用。
    @Published var aiResponsesPath: String = ""

    /// 保存过的**配置方案**（第 82 条）。
    ///
    /// WHY：ai.json 从前只有「一份当前配置」——编辑换一家服务商重填之后，
    /// 上一家的地址/模型（含密钥）就被覆盖掉了，再也找不回来（用户 2026-10-15 实测反馈）。
    /// 现在 `ai.json` 多一个 `profiles` 数组，每个方案 = 一整套上面这些字段。
    ///
    /// ⚠️ 方案里**含密钥**，与顶层那份写在同一个 `0600` 文件里；界面上的脱敏只影响显示。
    /// ⚠️ 方案与「当前配置」是**两件事**：转换用的永远是界面上（或顶层那份）当前值，
    ///    选中一个方案只是把它**填进输入框**，要不要落盘仍由「保存」决定（与第 58 条一致）。
    @Published var aiProfiles: [AIProfile] = []
    /// 「存为方案 / 保存」用的名字（空串 = 不存方案，只更新当前配置）。
    @Published var aiProfileName: String = ""
    /// 下拉里**当前选中**的方案名（空串 = 未选）。与 `aiProfileName` 分开：
    /// 前者是「要存成哪一份」，后者是「现在用的是哪一份」——合成一个变量会让
    /// 手打一个新名字时下拉框跟着变成空选，看着像丢了东西。
    @Published var aiSelectedProfile: String = ""

    /// 配置目录。默认（nil）= 用户目录里的 `cn.edu.sysu.logic.toolbox`；
    /// 允许注入只为了**自动化验证**（在不写用户真实目录的前提下跑通存/读/权限），
    /// 界面一律用默认值 `AppSettings()`。
    private let storeDir: URL?

    /// B-283：首次运行环境检查做没做过（**UserDefaults，不是 ai.json**）。
    ///
    /// 为什么不进 ai.json：它是一台机器的状态，与 AI 配置无关 —— 换一份 ai.json
    /// 不该让 App 在下次启动时又弹一遍；反过来清了偏好就是「当成第一次装」。
    /// 键名在这里定义：`Core/EnvCheck.swift` 引用它（EnvCheck 只在 ai_settings 台编译，
    /// 而 ViewModels 在另外五个台也编译 —— 依赖方向只能是 EnvCheck → AppSettings）。
    static let envCheckDoneKey = "envCheckDone"
    private let defaults: UserDefaults

    /// 上次落盘的值，用于界面显示「有未保存的修改」。
    private var savedBase = ""
    private var savedKey = ""
    private var savedModel = ""
    private var savedThinking: AIThinkingMode = .followEndpoint
    private var savedMaxTokens: String = AIMaxTokens.defaultText
    /// 通道 / 联网检索也要进「有未保存的修改」的判据（与另两个旋钮同一口径）
    private var savedProtocol: AIProtocol = .chat
    private var savedWebSearch: Bool = false
    private var savedSearchRefs: Bool = false
    /// 第 70 条的两个高级文本旋钮（留空 = 不传给引擎）
    private var savedSearchTool: String = ""
    private var savedResponsesPath: String = ""

    init(storeDirectory: URL? = nil, defaults: UserDefaults = .standard) {
        storeDir = storeDirectory
        self.defaults = defaults
        loadAI()
    }

    // MARK: - 首次运行环境检查（B-283）

    /// 是否已经做过首跑自检。**计算属性而不是 @Published**：唯一的状态在 UserDefaults，
    /// 两份内存状态迟早会分叉（`EnvCheckModel` 写入时也会同步这里，界面重画即读到新值）。
    var envCheckDone: Bool { defaults.bool(forKey: AppSettings.envCheckDoneKey) }

    /// 置位（首跑跑完 / 「设置」页手动跑完都调用；只写一次，永远不会被清回 false）。
    func markEnvCheckDone() {
        defaults.set(true, forKey: AppSettings.envCheckDoneKey)
        objectWillChange.send()
    }

    // MARK: 读写

    /// 存储路径（与 EditorStore 同一个目录，自动建目录）。
    var aiStoreURL: URL {
        let base = storeDir ?? AppSettings.defaultStoreDir()
        // 目录也收紧：里面存的是密钥
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        // ⚠️ 上面那个 `attributes` 只在**目录真的被创建**时生效：Foundation 对**已存在**的目录
        //    会直接忽略它。而本机那个目录通常是 `EditorStore` 早先建的 —— 实测
        //    `~/Library/Application Support/cn.edu.sysu.logic.toolbox` 是 2026-10-14 13:54
        //    由 EditorStore 建成 **0755**，此时只靠 createDirectory 的话，密钥文件的目录
        //    会一直同机可列（文件名/大小/时间可被别人看到），与上面这句话不符。
        //    所以创建之后再显式收紧一次 —— `setAttributes` 对已存在的目录也有效。
        try? FileManager.default.setAttributes([.posixPermissions: 0o700],
                                               ofItemAtPath: base.path)
        return AppSettings.storeURL(storeDirectory: base)
    }

    /// 只算 `ai.json` 的路径：**不建目录、不收紧权限、不碰界面**
    /// （给后台线程上的 `loadEngineKnobs` 用；需要建目录的写盘路径见 `aiStoreURL`）。
    static func storeURL(storeDirectory: URL? = nil) -> URL {
        (storeDirectory ?? AppSettings.defaultStoreDir()).appendingPathComponent("ai.json")
    }

    /// 读「设置」页**已保存**的两个旋钮（思考模式 / 输出上限）——这是**兜底**路径。
    ///
    /// 正常转换**不走这里**：「初排转换」页把界面上现填的两个旋钮直接传给
    /// `ConvertFeature.run(knobs:)`（和 base/key/model 一样现取现传，2026-10-15 改活），
    /// 所以「改了旋钮但没点保存」在本次转换里**照样生效**（只影响重启后是否还在）。
    ///
    /// 什么时候会走到这里：调用方**没有**现成的旋钮可传 —— 例如将来别的入口，
    /// 或在**后台线程**上需要一份值时（`@Published` 属于主线程，不能从后台读）。
    /// 这条路径读的是上次保存进 `ai.json` 的值。
    static func loadEngineKnobs(storeDirectory: URL? = nil) -> AIEngineKnobs {
        var knobs = AIEngineKnobs()
        guard let data = try? Data(contentsOf: AppSettings.storeURL(storeDirectory: storeDirectory)),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return knobs }
        knobs.thinking = AIThinkingMode(stored: obj["thinking"])
        if let t = obj["maxTokens"] as? String { knobs.maxTokens = t }
        // 第 69 条：通道 / 检索开关也要能读回（没有现成旋钮时的兜底路径）。
        knobs.apiProtocol = AIProtocol(stored: obj["protocol"])
        knobs.webSearch = (obj["webSearch"] as? Bool) ?? false
        knobs.searchRefs = (obj["searchRefs"] as? Bool) ?? false
        // 第 70 条的两个高级旋钮：旧 ai.json 没有这两个键 → 空串（= 用引擎默认）。
        if let t = obj["searchTool"] as? String { knobs.searchTool = t }
        if let p = obj["responsesPath"] as? String { knobs.responsesPath = p }
        return knobs
    }

    /// 默认目录：`~/Library/Application Support/cn.edu.sysu.logic.toolbox/`
    static func defaultStoreDir() -> URL {
        let fm = FileManager.default
        return (try? fm.url(for: .applicationSupportDirectory, in: .userDomainMask,
                            appropriateFor: nil, create: true))?
            .appendingPathComponent("cn.edu.sysu.logic.toolbox", isDirectory: true)
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
    }

    func loadAI() {
        guard let data = try? Data(contentsOf: aiStoreURL),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            savedBase = aiBase; savedKey = aiKey; savedModel = aiModel
            savedThinking = aiThinking; savedMaxTokens = aiMaxTokens
            savedProtocol = aiProtocol; savedWebSearch = aiWebSearch
            savedSearchRefs = aiSearchRefs
            savedSearchTool = aiSearchTool; savedResponsesPath = aiResponsesPath
            aiProfiles = []          // 文件不存在：方案列表为空（第 82 条）
            aiProfileName = ""
            aiSelectedProfile = ""
            return
        }
        aiBase = (obj["base"] as? String) ?? ""
        aiKey = (obj["key"] as? String) ?? ""
        aiModel = (obj["model"] as? String) ?? ""
        // ⚠️ 向后兼容：旧 ai.json 里没有这两个键 → 用默认值（思考「跟随端点默认」、
        //    上限 2048）。类型不对/取值认不出也一律回落默认，绝不让读配置失败。
        aiThinking = AIThinkingMode(stored: obj["thinking"])
        aiMaxTokens = (obj["maxTokens"] as? String) ?? AIMaxTokens.defaultText
        // 第 69 条的两个新键：旧 ai.json 里没有 → 默认（chat / 检索关），绝不让读配置失败。
        aiProtocol = AIProtocol(stored: obj["protocol"])
        aiWebSearch = (obj["webSearch"] as? Bool) ?? false
        // P2 的检索证据开关：旧 ai.json 没有这个键 → 默认关（绝不因读配置失败）。
        aiSearchRefs = (obj["searchRefs"] as? Bool) ?? false
        // 第 70 条的两个高级旋钮：旧 ai.json 没有这两个键 / 类型不对 → 空串
        // （= 不传这个参数，引擎用默认），绝不让读配置失败。
        aiSearchTool = (obj["searchTool"] as? String) ?? ""
        aiResponsesPath = (obj["responsesPath"] as? String) ?? ""
        // 第 82 条：保存过的配置方案。旧文件没有这个键 → 空列表（**不是**错误）。
        // ⚠️ 两层 cast 缺一不可：先 `[Any]` 再逐项 `as? [String: Any]` ——
        //    直接 `as? [[String: Any]]` 时，数组里只要有**一个**不是字典的元素
        //    （手改坏了，或将来混进别的形状），**整份列表**都会 cast 失败变成空，
        //    把编辑存过的方案一次全「弄丢」。这个洞是 harness 的 m11 抓出来的。
        let rawProfiles = obj["profiles"] as? [Any] ?? []
        aiProfiles = rawProfiles
            .compactMap { $0 as? [String: Any] }
            .compactMap { AIProfile(dict: $0) }
        aiProfileName = ""
        aiSelectedProfile = ""
        savedBase = aiBase; savedKey = aiKey; savedModel = aiModel
        savedThinking = aiThinking; savedMaxTokens = aiMaxTokens
        savedProtocol = aiProtocol; savedWebSearch = aiWebSearch
        savedSearchRefs = aiSearchRefs
        savedSearchTool = aiSearchTool; savedResponsesPath = aiResponsesPath
    }

    /// 写盘并把权限固定为 0600。返回是否成功（失败原因写进 `aiStatus`）。
    ///
    /// 「输出上限」非整数时**拒绝保存**（返回 false + 中文提示）：引擎对非整数只会
    /// 静默回落到 2048，界面这边必须当场说清楚（见 `AIMaxTokens`）。
    @discardableResult
    func saveAI() -> Bool {
        let maxText: String
        switch AIMaxTokens.parse(aiMaxTokens) {
        case .invalid(let hint):
            aiStatus = "未保存：" + hint
            return false
        case .unset:
            maxText = ""
        case .value(let v):
            maxText = v      // 规范化（"02048" → "2048"），免得界面与文件里的值不一致
        }
        // ── 第 82 条：把当前这套值**同时**存成一个方案 ──────────────────────
        // 方案名留空 = 只更新「当前配置」（= 从前的行为，一个字都不多写）。
        // 同名 = **覆盖**（不新增、不重名），大小写与空白按 trim 后的字面值算。
        let wantedName = aiProfileName.trimmingCharacters(in: .whitespacesAndNewlines)
        if !wantedName.isEmpty {
            upsertProfile(AIProfile(
                name: wantedName,
                base: aiBase.trimmingCharacters(in: .whitespacesAndNewlines),
                key: aiKey.trimmingCharacters(in: .whitespacesAndNewlines),
                model: aiModel.trimmingCharacters(in: .whitespacesAndNewlines),
                thinking: aiThinking, maxTokens: maxText,
                proto: aiProtocol, webSearch: aiWebSearch,
                searchTool: aiSearchTool.trimmingCharacters(in: .whitespacesAndNewlines),
                responsesPath: aiResponsesPath.trimmingCharacters(in: .whitespacesAndNewlines),
                searchRefs: aiSearchRefs))
            aiProfileName = wantedName
            aiSelectedProfile = wantedName
        }
        var obj: [String: Any] = [
            "base": aiBase.trimmingCharacters(in: .whitespacesAndNewlines),
            "key": aiKey.trimmingCharacters(in: .whitespacesAndNewlines),
            "model": aiModel.trimmingCharacters(in: .whitespacesAndNewlines),
            "thinking": aiThinking.rawValue,
            "maxTokens": maxText,
            "protocol": aiProtocol.rawValue,
            "webSearch": aiWebSearch,
            // 第 70 条的两个高级旋钮：**原样**存（只 trim 掉两端空白）。它们允许是空串
            // （= 用引擎默认）；非法值也照样存下来（下次打开还能看见自己填了什么，
            // 界面会给红字提示，而转换时**不会**发给引擎）——绝不静默丢掉用户输入。
            "searchTool": aiSearchTool.trimmingCharacters(in: .whitespacesAndNewlines),
            "responsesPath": aiResponsesPath.trimmingCharacters(in: .whitespacesAndNewlines),
        ]
        // P2 的检索证据开关：只在**开启**时写键（关着时 ai.json 与旧版逐字节相同）。
        if aiSearchRefs { obj["searchRefs"] = true }
        // 第 82 条：保存过的配置方案。**一个方案都没有时连这个键都不写** ——
        // 没用过方案的编辑，ai.json 与从前**逐字节相同**（既有断言不受影响）；
        // 删光最后一个方案时这个键也随之消失，不留空数组。
        if !aiProfiles.isEmpty {
            obj["profiles"] = aiProfiles.map { $0.toDict() }
        }
        guard let data = try? JSONSerialization.data(withJSONObject: obj,
                                                     options: [.prettyPrinted, .sortedKeys]) else {
            aiStatus = "保存失败：配置无法序列化"
            return false
        }
        let url = aiStoreURL
        do {
            try data.write(to: url, options: [.atomic])
        } catch {
            aiStatus = "保存失败：\(error.localizedDescription)"
            return false
        }
        // ⚠️ 明文存密钥，权限必须收紧；.atomic 写会重建文件，所以权限要在写之后设。
        try? FileManager.default.setAttributes([.posixPermissions: 0o600],
                                               ofItemAtPath: url.path)
        savedBase = obj["base"] as? String ?? ""
        savedKey = obj["key"] as? String ?? ""
        savedModel = obj["model"] as? String ?? ""
        aiMaxTokens = maxText
        savedMaxTokens = maxText
        savedThinking = aiThinking
        savedProtocol = aiProtocol
        savedWebSearch = aiWebSearch
        savedSearchRefs = aiSearchRefs
        // 两个高级旋钮与界面对齐（trim 之后的值就是盘上的值，免得刚保存就显示「未保存」）
        aiSearchTool = obj["searchTool"] as? String ?? ""
        aiResponsesPath = obj["responsesPath"] as? String ?? ""
        savedSearchTool = aiSearchTool
        savedResponsesPath = aiResponsesPath
        aiStatus = "已保存到 \(url.path)（权限 \(AppSettings.posixMode(of: url))）"
        return true
    }

    /// 「清空」= **只清界面上那五个输入框**，磁盘上已保存的配置**一个字节都不动**。
    ///
    /// ⚠️ WHY（2026-10-15 **用户实测报的 bug**）：从前这里末尾直接调 `saveAI()`，
    /// 一按「清空」就把 `~/Library/Application Support/cn.edu.sysu.logic.toolbox/ai.json`
    /// 覆盖成空值 —— 编辑只是想换个服务商、重填地址，一点就把已经配好、本来能用的
    /// 配置（含密钥）抹掉了，只能重贴。语义应该是「**清空输入框 ≠ 删除配置**」。
    /// 所以现在落盘路径**唯一**：用户显式点「保存」（`SettingsTab` 的保存按钮）——
    /// 清空之后 `aiDirty == true`（界面 ≠ 盘上），想真正覆盖就再点一次「保存」。
    func clearAI() {
        aiBase = ""; aiKey = ""; aiModel = ""
        // 两个旋钮也回到引擎默认（思考「跟随端点默认」、上限 2048）：
        // 「清空」= **在界面上**恢复出厂，别把上一个服务商的调参留在输入框里；
        // 注意这句话只说界面 —— 盘上那份要等「保存」才跟着变。
        aiThinking = .followEndpoint
        aiMaxTokens = AIMaxTokens.defaultText
        aiProtocol = .chat
        aiWebSearch = false
        aiSearchRefs = false
        // 第 70 条的两个高级旋钮也回空串（= 用引擎默认）：与上面同一语义 ——
        // 「清空」是**在界面上**恢复出厂，盘上那份要等「保存」才跟着变。
        aiSearchTool = ""
        aiResponsesPath = ""
        // 状态行必须当场说清语义（不然编辑会以为配置已经被删了，或反过来以为还在用旧的）：
        aiStatus = "已清空输入框；磁盘上已保存的配置**没动**（要覆盖请点「保存」）。"
    }

    // MARK: - 配置方案（第 82 条）

    /// 同名 = **覆盖**（保持原位置，下拉顺序不跳），异名 = 追加。
    func upsertProfile(_ p: AIProfile) {
        if let i = aiProfiles.firstIndex(where: { $0.name == p.name }) {
            aiProfiles[i] = p
        } else {
            aiProfiles.append(p)
        }
    }

    /// 选中一个方案 → 把它的值**填进输入框**（只改内存，不落盘）。
    ///
    /// ⚠️ 与第 58 条同一口径：转换用的永远是界面上的**当前值**。所以这里刻意**不写盘**——
    /// 想让它成为下次转换用的配置，要再点一次「保存」（状态行会提醒）。
    @discardableResult
    func applyProfile(named name: String) -> Bool {
        guard let p = aiProfiles.first(where: { $0.name == name }) else {
            aiStatus = "没有名为「\(name)」的方案。"
            return false
        }
        aiBase = p.base
        aiKey = p.key
        aiModel = p.model
        aiThinking = AIThinkingMode(stored: p.thinking)
        aiMaxTokens = p.maxTokens
        aiProtocol = AIProtocol(stored: p.protocolRaw)
        aiWebSearch = p.webSearch
        aiSearchRefs = p.searchRefs
        aiSearchTool = p.searchTool
        aiResponsesPath = p.responsesPath
        aiProfileName = p.name
        aiSelectedProfile = p.name
        aiStatus = "已把方案「\(p.name)」填进上面的输入框；**还没落盘**——要用它请点「保存」。"
        return true
    }

    /// 删掉一个方案并**立刻落盘**（删除是明确动作，不该还要再点一次保存）。
    ///
    /// 复用 `saveAI()` 这条**唯一**写盘路径：先把两个名字变量清掉，
    /// 免得它又把刚删掉的那个名字当成「要存的方案」写回去。
    /// 落盘失败（例如「输出上限」此刻是非整数）就把内存改回去，绝不让界面与磁盘不一致。
    @discardableResult
    func deleteProfile(named name: String) -> Bool {
        guard let idx = aiProfiles.firstIndex(where: { $0.name == name }) else {
            aiStatus = "没有名为「\(name)」的方案。"
            return false
        }
        let backupProfiles = aiProfiles
        let backupName = aiProfileName
        let backupSelected = aiSelectedProfile
        aiProfiles.remove(at: idx)
        aiProfileName = ""
        aiSelectedProfile = ""
        guard saveAI() else {
            aiProfiles = backupProfiles
            aiProfileName = backupName
            aiSelectedProfile = backupSelected
            aiStatus = "删除方案「\(name)」失败：本次改动**没有落盘**，已还原。—— " + aiStatus
            return false
        }
        aiStatus = "已删除方案「\(name)」（磁盘上那份已同步）。"
        return true
    }

    // MARK: 状态查询（界面用）

    /// 是否已配置到「可以调用」的程度：**地址 + 模型名**必填。
    /// 密钥可以留空 —— 本机/自建端点（LM Studio 等）不需要密钥
    /// （引擎侧 `LLMConfig.ready` 的判定口径完全一致：base and model）。
    var aiConfigured: Bool {
        !aiBase.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !aiModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// 当前内容与上次保存的是否不同（提醒编辑点「保存」）。
    /// 两个旋钮也算：保存后才会写进 `ai.json`，下次开 App 才不用重填。
    /// （**但转换用的是现取现传的值**，见 `loadEngineKnobs` 的说明 —— 改了不存
    /// 只是「重启后丢失」，不会像以前那样「本次转换不生效」。）
    var aiDirty: Bool {
        aiBase.trimmingCharacters(in: .whitespacesAndNewlines) != savedBase
            || aiKey.trimmingCharacters(in: .whitespacesAndNewlines) != savedKey
            || aiModel.trimmingCharacters(in: .whitespacesAndNewlines) != savedModel
            || aiThinking != savedThinking
            // 比「解析结果」而不是原字符串：`2048` 与 `02048` 是同一个值，不该亮黄点
            || AIMaxTokens.parse(aiMaxTokens) != AIMaxTokens.parse(savedMaxTokens)
            || aiProtocol != savedProtocol
            || aiWebSearch != savedWebSearch
            || aiSearchRefs != savedSearchRefs
            // 第 70 条的两个高级旋钮：按 trim 后的**文本**比（它们没有「同值不同写法」，
            // 只有「留空 / 填了什么」这一种差异）
            || aiSearchTool.trimmingCharacters(in: .whitespacesAndNewlines) != savedSearchTool
            || aiResponsesPath.trimmingCharacters(in: .whitespacesAndNewlines) != savedResponsesPath
    }

    /// 两个旋钮的当前值（**按界面上的输入**，不做 I/O）
    var aiEngineKnobs: AIEngineKnobs {
        AIEngineKnobs(thinking: aiThinking, maxTokens: aiMaxTokens,
                      apiProtocol: aiProtocol, webSearch: aiWebSearch,
                      searchTool: aiSearchTool, responsesPath: aiResponsesPath,
                      searchRefs: aiSearchRefs)
    }

    /// 一行摘要，供转换页/设置页显示：`deepseek-flash @ https://api.deepseek.com/v1`
    var aiSummary: String {
        "\(aiModel.trimmingCharacters(in: .whitespacesAndNewlines)) @ "
            + aiBase.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: 密钥脱敏（唯一允许的显示方式）

    /// 只显示前 4 位 + `…`。**任何**日志/界面都走这里，绝不打印完整密钥。
    static func mask(_ key: String) -> String {
        let k = key.trimmingCharacters(in: .whitespacesAndNewlines)
        if k.isEmpty { return "（未填）" }
        if k.count <= 4 { return "…（\(k.count) 位，不显示）" }
        return String(k.prefix(4)) + "…（\(k.count) 位）"
    }

    /// 把文本里出现的密钥抹掉（服务端/模型可能把请求头回显到正文里，实测本地 mock 就能复现）。
    static func redact(_ text: String, key: String) -> String {
        var out = text
        if key.count >= 4 { out = out.replacingOccurrences(of: key, with: "****") }
        let k = key.trimmingCharacters(in: .whitespacesAndNewlines)
        if k.count >= 4 { out = out.replacingOccurrences(of: k, with: "****") }
        return out
    }

    /// 读文件的八进制权限位（如 "600"），读不到返回 "???"
    static func posixMode(of url: URL) -> String {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
              let n = attrs[.posixPermissions] as? NSNumber else { return "???" }
        return String(format: "%03o", n.intValue & 0o777)
    }
}

/// 「检查tex信息」页的界面状态：运行完成后持有结构化报告供表格显示
final class InfoCheckModel: ObservableObject {
    @Published var path: String = ""
    @Published var running: Bool = false
    @Published var report: InfoReport? = nil
    /// U3：本次运行的取消句柄（运行中主按钮变成「取消」）
    var cancel: RunCancel? = nil
}

/// 「合并文件」页的探测状态：在运行前就把"识别到哪个编委页/封底页"显示出来，
/// 这样用户点「运行」之前就知道会发生什么。
///
/// 第 99 条起，识别**问引擎**（`mergepdf.py --detect`）—— 预览与真正合并必然一致。
final class EditorsProbeModel: ObservableObject {
    @Published var probe: MergeFeature.Probe? = nil
    /// 是否已经扫描过（用于区分"没勾选"和"勾了但没找到"）
    @Published var scanned: Bool = false

    /// 上一次探测的世代号：路径连续变化时只认最后一次结果。
    private var generation = 0

    func refresh(path: String, enabled: Bool) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard enabled, !trimmed.isEmpty, PathKit.isDirectory(trimmed) else {
            generation += 1
            probe = nil
            scanned = false
            return
        }
        // 案例 126：探测要跑引擎子进程（`mergepdf.py --detect`），**绝不能在主线程同步做**——
        // 拖拽落下时 SwiftUI 在 CoreDrag 的 drop 提交动画事务里触发表单更新，主线程一阻塞
        // 就会重入提交动画并 SIGSEGV（2026-10-18 实测闪退）。放后台队列，结果回主线程。
        generation += 1
        let gen = generation
        scanned = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = MergeFeature.detect(folderPath: trimmed)
            DispatchQueue.main.async {
                guard let self, gen == self.generation else { return }
                self.probe = result
            }
        }
    }
}

/// B-188：从其它引用库页跳到「待处理」页的**一次性跳转意图**。
///
/// 为什么单独一个类型（而不是 `Bool`/多个 `@Published`）：
///   · 一次跳转 = 「定位到哪个分类」+「字段冲突区看不看已裁决」两个事实，绑成一个值，
///     ReviewView 取走一次就够，不会出现「开关开了但分类没定位」这类半应用状态；
///   · 放在 `BibDbSettings` 外面、且**不依赖 `BibDbFeature` 的任何类型**：其它 Swift 测试台
///     只编译 `Core/ViewModels.swift`（不带 `Features/BibDbFeature.swift`），这里不能出现
///     引擎层类型。`registry` 用字符串标识分类（与 `LintWarningRegistry.order` 同字面量）。
struct BibDbReviewJump: Equatable {
    /// 定位分类（conflicts/duplicates/near_duplicates/missing_field/unknown_entry_type/
    /// alias/unverified/other/""）；`""` = 普通进入，不定位
    var registry: String = ""
    /// 字段冲突区是否看「已处理/已裁决」
    var showResolved: Bool = false
}

/// 「引用库」设置（第 132 条）：核实成本闸门 + 查库开关 + 引用库（笔记仓）路径。
///
/// 为什么存 `UserDefaults` 而不是 `ai.json`：这几项是**本机行为偏好**（上限、要不要先算账、
/// 查不查库、笔记仓在哪），与 AI 连接参数/密钥不是一回事。`UserDefaults` 逐键读写，
/// 旧版本没有这些键时自然回落默认值，不需要像 `ai.json` 那样整体迁移。
///
/// ⚠️ 生效方式：本类只负责「存 / 读 / 校验」；真正生效是「初排转换」页在**运行时现取**
/// 这些值、拼进引擎 argv（见 `ConvertTab.run()` 与 `ConvertFeature.arguments(_:)`）——
/// 与 AI 那组旋钮同一口径：改了立刻影响下一次转换，不必重启 App。
/// ⚠️ 笔记仓路径的键名沿用 `notesRepoPath`（`EngineLocator.notesRepoOverrideKey`）：
/// 它原来就是「设置」页手动指定笔记仓用的，这里**不新开键**，否则同一件事会有两个真相。
final class BibDbSettings: ObservableObject {

    // MARK: - UserDefaults 键名（**不要改**：改了老用户设过的值就丢）

    /// 核实条数上限（Int；0 = 不限）。缺省 120（= 引擎 `_refmark.DEFAULT_VERIFY_MAX`）
    static let verifyMaxKey = "bibdbVerifyMax"
    /// 核实前先算账（Bool，`--verify-plan`）
    static let verifyPlanKey = "bibdbVerifyPlan"
    /// 允许全量核实（Bool，`--verify-all-refs`）
    static let verifyAllRefsKey = "bibdbVerifyAllRefs"
    /// 核实前先查引用库（Bool，缺省 true；关 → `--no-bibdb`）
    static let lookupKey = "bibdbLookupEnabled"
    /// 手动指定的笔记仓路径（String；缺省 = 自动探测）。与 `EngineLocator` 共用的键。
    static let notesRepoKey = EngineLocator.notesRepoOverrideKey

    /// 引擎侧的默认核实上限（`slcn/refmark.py: DEFAULT_VERIFY_MAX`，**只有一处**；
    /// 这里只是把「留空/没设过」的显示值与引擎对齐，不参与判定）。
    static let defaultVerifyMax = 120

    // MARK: - 界面状态（@Published；本机没有 @State 宏，见文件抬头说明）

    /// 上限输入框的**原样**文本（允许非法/空，界面负责红字提示；非法值不落盘）
    @Published var verifyMaxText: String = String(BibDbSettings.defaultVerifyMax)
    /// 核实前先算账（只算不核）
    @Published var verifyPlan: Bool = false
    /// 允许全量核实（越过上限闸门）
    @Published var verifyAllRefs: Bool = false
    /// 核实前先查引用库（关 = 本次一律不查库）
    @Published var lookupEnabled: Bool = true
    /// 手动指定的笔记仓路径（原样文本；空 = 自动探测）
    @Published var notesRepoPath: String = ""
    /// B-188：「待处理」页的**一次性跳转意图**（**仅内存、不落盘**）。
    ///
    /// 为什么不放 Bool：B-143 的 `showResolvedConflicts` 是共享开关，5 个常驻引用库页都
    /// `@ObservedObject` 它 → 开关一变全体 body 重算（spec B-188 §2 性能诊断）。
    /// 现在跳转只写这份意图，`BibDbReviewView` 激活时取走、应用成**页面本地态**后置回 nil；
    /// 其它页不再因为一次跳转重算。见 `Core/ViewModels.swift: BibDbReviewJump` 与 CONTRACT.md。
    @Published var reviewJump: BibDbReviewJump? = nil

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        // 上限：只在**用户设过**时才用盘上的值；没设过（旧版本）用引擎默认 120。
        // 用 `object(forKey:)` 而不是 `integer(forKey:)` 区分「0」与「没这个键」。
        if defaults.object(forKey: Self.verifyMaxKey) != nil {
            let stored = defaults.integer(forKey: Self.verifyMaxKey)
            verifyMaxText = String(max(0, stored))
        } else {
            verifyMaxText = String(Self.defaultVerifyMax)
        }
        verifyPlan = defaults.bool(forKey: Self.verifyPlanKey)
        verifyAllRefs = defaults.bool(forKey: Self.verifyAllRefsKey)
        // 「核实前先查引用库」的默认必须是**开**（引擎默认也是「库存在就查」）：
        // 只有用户显式关过才读回 false。
        if let stored = defaults.object(forKey: Self.lookupKey) as? Bool {
            lookupEnabled = stored
        } else {
            lookupEnabled = true
        }
        notesRepoPath = defaults.string(forKey: Self.notesRepoKey) ?? ""
    }

    // MARK: - 上限的解析口径（纯逻辑；非法值**当场上红字**、不落盘）

    /// 合法上限；`nil` = 留空或非法/负数（**不传 `--verify-max`**，引擎用默认 120）
    var verifyMaxValue: Int? {
        let text = verifyMaxText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let number = Int(text), number >= 0 else { return nil }
        return number
    }

    /// 输入非法时的中文说明（界面红字）；合法/留空时 nil
    var verifyMaxHint: String? {
        let text = verifyMaxText.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return nil }        // 留空 = 引擎默认，不算错
        guard let number = Int(text) else {
            return "上限必须是整数（0 = 不限；留空 = 引擎默认 \(Self.defaultVerifyMax)）"
        }
        guard number >= 0 else {
            return "上限不能是负数（要「不限」请填 0）"
        }
        return nil
    }

    /// 一行摘要（转换页/设置页显示）：`120 条（0 = 不限）`
    var verifyMaxSummary: String {
        guard let value = verifyMaxValue else {
            return "（输入无效：本次不传 --verify-max，引擎按默认 \(Self.defaultVerifyMax) 条）"
        }
        return value == 0 ? "不限" : "\(value) 条"
    }

    // MARK: - 写盘（逐项立即落盘；非法输入**保留在输入框**但不写盘）

    /// 更新上限输入：合法（含留空）才落盘；非法值保留在输入框让编辑改。
    func updateVerifyMax(_ raw: String) {
        verifyMaxText = raw
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty {
            defaults.removeObject(forKey: Self.verifyMaxKey)
            return
        }
        guard let number = Int(text), number >= 0 else { return }
        defaults.set(number, forKey: Self.verifyMaxKey)
    }

    func updateVerifyPlan(_ on: Bool) {
        verifyPlan = on
        defaults.set(on, forKey: Self.verifyPlanKey)
    }

    func updateVerifyAllRefs(_ on: Bool) {
        verifyAllRefs = on
        defaults.set(on, forKey: Self.verifyAllRefsKey)
    }

    func updateLookup(_ on: Bool) {
        lookupEnabled = on
        defaults.set(on, forKey: Self.lookupKey)
    }

    /// 更新手动笔记仓路径：留空 = 恢复自动探测（**删键**，别存空串占位）；
    /// 填了就把 `~` 展开后存（`EngineLocator` 只按字面路径判存在，不替用户展开）。
    func updateNotesRepoPath(_ raw: String) {
        notesRepoPath = raw
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            defaults.removeObject(forKey: Self.notesRepoKey)
            return
        }
        defaults.set((text as NSString).expandingTildeInPath, forKey: Self.notesRepoKey)
    }

    /// 手动路径是否有效：nil = 没填（走自动探测）；true/false = 填了、目录在不在。
    /// 「不合法也照样存」是刻意的：编辑可能先把路径写下来、稍后再接上那台机器；
    /// 界面负责显示红字，引擎侧 `notesRepo()` 对不存在的手动路径会自动回落自动探测。
    var manualNotesPathValid: Bool? {
        let text = notesRepoPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        return PathKit.isDirectory((text as NSString).expandingTildeInPath)
    }
}

/// 第 55 批：AI 预算闸（单篇 / 单批 token 预算）的本机偏好。
///
/// 与 `BibDbSettings` **同一模式**（UserDefaults 逐键存、输入非法当场上红字且不落盘）：
/// 为什么不用 `ai.json`：这两项是「本机花多少钱」的行为偏好，不是 AI 连接参数，
/// 与密钥那组分开存；旧版本没有这两个键时自然回落引擎默认（30000 / 1000000）。
///
/// ⚠️ 生效方式：本类只负责存 / 读 / 校验。真正传给引擎是「初排转换」页在**运行时现取**
/// （`ConvertTab.run()` → `ConvertFeature.Options.aiBudgetTokens/…` → `arguments()`），
/// 且只有**非两段式 + 勾了「用 AI 处理参考文献」+ 值 ≠ 引擎默认**时才拼进 argv ——
/// 于是「没碰过这两栏」的用户命令行与批 54 逐字节相同。外部 chat 两段式零费用，不传。
final class AIBudgetSettings: ObservableObject {

    // MARK: - UserDefaults 键名（**不要改**：改了老用户设过的值就丢）

    /// 单篇直连 API 通道的 token 预算（Int；0 = 不限）。缺省 30000（= 引擎默认）
    static let articleKey = "aiBudgetTokens"
    /// 单批直连 API 通道的 token 预算（Int；0 = 不限）。缺省 1000000（= 引擎默认）
    static let batchKey = "aiBudgetBatchTokens"

    /// 引擎侧默认值（`convert.py --ai-budget-tokens` / `--ai-batch-budget-tokens` 的 default，
    /// **只有一处**；这里只是让「不传 = 用默认」的显示值与引擎对齐，不参与判定）。
    static let defaultArticle = 30000
    static let defaultBatch = 1000000

    // MARK: - 第 63 批：直连 API「全文核查」的上限 / 自动应用（同一持久化模式）

    /// 全文核查的正文总字符上限（Int；0 = 不限）。缺省 40000（= 引擎默认）；
    /// 留空 = 不传（引擎用默认）。**不要改键名**：改了老用户设过的值就丢。
    static let fulltextMaxCharsKey = "llmFulltextMaxChars"
    /// 全文核查结果自动应用（Bool；**缺键 = true** = 引擎默认自动应用）。
    static let fulltextAutoApplyKey = "llmFulltextAutoApply"
    /// 引擎侧默认值（`convert.py --llm-fulltext-max-chars` 的 default，只有一处）。
    static let defaultFulltextMaxChars = 40000

    // MARK: - 界面状态（@Published；本机没有 @State 宏，见文件抬头说明）

    /// 单篇预算输入框的**原样**文本（允许非法/空，界面负责红字提示；非法值不落盘）
    @Published var articleText: String = String(AIBudgetSettings.defaultArticle)
    /// 单批预算输入框的原样文本
    @Published var batchText: String = String(AIBudgetSettings.defaultBatch)
    /// 第 63 批：全文核查正文总字符上限输入框的原样文本（非法/空保留在框里；非法不落盘）
    @Published var fulltextMaxCharsText: String = String(AIBudgetSettings.defaultFulltextMaxChars)
    /// 第 63 批：「全文核查结果自动应用」开关（默认开 = 引擎默认；关掉才传关闭旗标）
    @Published var fulltextAutoApply: Bool = true

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        // 用 `object(forKey:)` 而不是 `integer(forKey:)` 区分「0」与「没这个键」。
        if defaults.object(forKey: Self.articleKey) != nil {
            articleText = String(max(0, defaults.integer(forKey: Self.articleKey)))
        } else {
            articleText = String(Self.defaultArticle)
        }
        if defaults.object(forKey: Self.batchKey) != nil {
            batchText = String(max(0, defaults.integer(forKey: Self.batchKey)))
        } else {
            batchText = String(Self.defaultBatch)
        }
        // 第 63 批：全文核查上限 / 自动应用（缺键 = 引擎默认；自动应用缺键 = true）。
        if defaults.object(forKey: Self.fulltextMaxCharsKey) != nil {
            fulltextMaxCharsText = String(max(0, defaults.integer(forKey: Self.fulltextMaxCharsKey)))
        } else {
            fulltextMaxCharsText = String(Self.defaultFulltextMaxChars)
        }
        if defaults.object(forKey: Self.fulltextAutoApplyKey) != nil {
            fulltextAutoApply = defaults.bool(forKey: Self.fulltextAutoApplyKey)
        }
    }

    // MARK: - 解析口径（纯逻辑；非法值当场上红字、不落盘）

    /// 一个预算输入框的解析结果（与 `AIMaxTokens.Parsed` 同一模式）。
    enum Parsed: Equatable {
        /// 留空 → **不传**该参数（引擎用默认；等价于默认值本身）
        case unset
        /// 合法非负整数（`0` = 不限）
        case value(Int)
        /// 非整数 / 负数 → 拒绝保存，并附一句中文提示
        case invalid(String)
    }

    static func parse(_ raw: String) -> Parsed {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return .unset }
        guard let n = Int(t) else {
            return .invalid("预算必须是整数（0 = 不限；留空 = 引擎默认），现在填的是「\(t)」。")
        }
        guard n >= 0 else {
            return .invalid("预算不能是负数（要「不限」请填 0），现在填的是「\(t)」。")
        }
        return .value(n)
    }

    /// 单篇预算的引擎值：`nil` = 不传（留空或非法；引擎用默认 30000）
    var articleValue: Int? {
        if case .value(let v) = Self.parse(articleText) { return v }
        return nil
    }

    /// 单批预算的引擎值：`nil` = 不传（留空或非法；引擎用默认 1000000）
    var batchValue: Int? {
        if case .value(let v) = Self.parse(batchText) { return v }
        return nil
    }

    var articleHint: String? {
        if case .invalid(let h) = Self.parse(articleText) { return h }
        return nil
    }

    var batchHint: String? {
        if case .invalid(let h) = Self.parse(batchText) { return h }
        return nil
    }

    /// 一行摘要（转换页/设置页显示）
    var articleSummary: String {
        switch Self.parse(articleText) {
        case .unset: return "默认 \(Self.defaultArticle)（未设置）"
        case .value(let v): return v == 0 ? "不限" : "\(v) tokens"
        case .invalid: return "（输入无效：本次不传 --ai-budget-tokens，引擎按默认 \(Self.defaultArticle)）"
        }
    }

    var batchSummary: String {
        switch Self.parse(batchText) {
        case .unset: return "默认 \(Self.defaultBatch)（未设置）"
        case .value(let v): return v == 0 ? "不限" : "\(v) tokens"
        case .invalid: return "（输入无效：本次不传 --ai-batch-budget-tokens，引擎按默认 \(Self.defaultBatch)）"
        }
    }

    // MARK: - 写盘（逐项立即落盘；非法输入**保留在输入框**但不写盘）

    /// 更新单篇预算输入：合法（含留空）才落盘；非法值保留在输入框让编辑改。
    func updateArticle(_ raw: String) {
        articleText = raw
        switch Self.parse(raw) {
        case .unset:
            defaults.removeObject(forKey: Self.articleKey)
        case .value(let n):
            defaults.set(n, forKey: Self.articleKey)
        case .invalid:
            break      // 非法值不落盘（界面上有红字说明）
        }
    }

    /// 更新单批预算输入（同一口径）。
    func updateBatch(_ raw: String) {
        batchText = raw
        switch Self.parse(raw) {
        case .unset:
            defaults.removeObject(forKey: Self.batchKey)
        case .value(let n):
            defaults.set(n, forKey: Self.batchKey)
        case .invalid:
            break
        }
    }

    // MARK: - 第 63 批：全文核查上限 / 自动应用（同一解析/落盘口径）

    /// 全文核查字符上限的解析结果（与 `Parsed` 同一模式；0 = 不限、留空 = 不传）。
    static func parseFulltextMaxChars(_ raw: String) -> Parsed {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return .unset }
        guard let n = Int(t) else {
            return .invalid("上限必须是整数（0 = 不限；留空 = 引擎默认 \(Self.defaultFulltextMaxChars)），"
                            + "现在填的是「\(t)」。")
        }
        guard n >= 0 else {
            return .invalid("上限不能是负数（要「不限」请填 0），现在填的是「\(t)」。")
        }
        return .value(n)
    }

    /// 引擎值：nil = 不传（留空/非法；引擎用默认 40000）；0 = 显式不限。
    var fulltextMaxCharsValue: Int? {
        if case .value(let v) = Self.parseFulltextMaxChars(fulltextMaxCharsText) { return v }
        return nil
    }

    var fulltextMaxCharsHint: String? {
        if case .invalid(let h) = Self.parseFulltextMaxChars(fulltextMaxCharsText) { return h }
        return nil
    }

    /// 一行摘要（设置页可显示；测试也用它钉「0 = 不限」的文案）。
    var fulltextMaxCharsSummary: String {
        switch Self.parseFulltextMaxChars(fulltextMaxCharsText) {
        case .unset: return "默认 \(Self.defaultFulltextMaxChars)（未设置）"
        case .value(let v): return v == 0 ? "不限" : "\(v) 字符"
        case .invalid:
            return "（输入无效：本次不传 --llm-fulltext-max-chars，引擎按默认 \(Self.defaultFulltextMaxChars)）"
        }
    }

    /// 更新上限输入：合法（含留空）才落盘；非法值保留在输入框让编辑改。
    func updateFulltextMaxChars(_ raw: String) {
        fulltextMaxCharsText = raw
        switch Self.parseFulltextMaxChars(raw) {
        case .unset:
            defaults.removeObject(forKey: Self.fulltextMaxCharsKey)
        case .value(let n):
            defaults.set(n, forKey: Self.fulltextMaxCharsKey)
        case .invalid:
            break      // 非法值不落盘（界面上有红字说明）
        }
    }

    /// 更新「自动应用」开关：立即落盘（true 也显式存，读回口径一致；缺键 = true）。
    func updateFulltextAutoApply(_ on: Bool) {
        fulltextAutoApply = on
        defaults.set(on, forKey: Self.fulltextAutoApplyKey)
    }
}
