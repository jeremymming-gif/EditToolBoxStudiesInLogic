import Foundation
import AppKit

/// 失败诊断（B-285b）：把引擎的 `diagnose.v1` 画成卡片。
///
/// 分工与 Windows 壳逐字对齐：
///   · **判据**（17 类 / 标题 / 人话原因 / 下一步 / 置信度）只在
///     `Engine/slcn/diagnose.py` —— 两个平台调同一份引擎，结论必然一致；
///   · 这里只做四件事：判「要不要出卡」（红/中性）、把本次运行日志落进应用缓存、
///     调 `diagnose.py --json`、拼一份**零上传**的本地诊断包文本；
///   · 旧引擎没有 `diagnose.py`、或诊断本身失败 → 回退为「退出码 + 日志尾部」，
///     **不阻断**原来的运行结果（D-04）。
///
/// 红卡口径（C1/C2）：转换 exit 2、信息检查 exit 2、全编译 exit 1、启动失败 <0；
/// 中性卡：一校核查 / 文献回读 exit 1；exit 0 一律不出卡。

enum DiagnosisLevel: String {
    case error       // ❌ 红卡：这次运行失败了
    case neutral     // ⚠️ 中性卡：有需要处理的结果，但不是程序出错
}

struct DiagnosisEvidence: Codable, Equatable {
    var logTail: [String] = []
    var matchedLines: [String] = []
    var artifacts: [String] = []

    enum CodingKeys: String, CodingKey {
        case logTail = "log_tail"
        case matchedLines = "matched_lines"
        case artifacts
    }
}

/// `diagnose.py --json` 的 13 键（ENGINE_API §十一）。
struct DiagnosisReport: Codable, Equatable {
    var schema: String = "diagnose.v1"
    var entry: String
    var exitCode: Int
    var category: String
    var severity: String
    var title: String
    var reason: String
    var evidence: DiagnosisEvidence
    var nextSteps: [String]
    var docAnchor: String
    var confidence: Double?
    var generatedAt: String
    var engineVersion: String?

    enum CodingKeys: String, CodingKey {
        case schema, entry
        case exitCode = "exit_code"
        case category, severity, title, reason, evidence
        case nextSteps = "next_steps"
        case docAnchor = "doc_anchor"
        case confidence
        case generatedAt = "generated_at"
        case engineVersion = "engine_version"
    }
}

/// 一张诊断卡要显示的全部内容（视图不自己判对错，只照着画）。
struct DiagnosisCardModel {
    let entry: String
    let level: DiagnosisLevel
    let exitCode: Int
    let title: String
    let category: String
    let reason: String
    let steps: [String]
    let evidence: [String]
    let logPath: String?
    let pack: String
    let fallback: Bool
}

/// 诊断卡状态中心：四个页面各观察自己入口的那一张。
/// （`ViewModels.swift` 不在本轮写域，因此不改各 Model，用这个中心对象。）
final class DiagnosisCenter: ObservableObject {
    static let shared = DiagnosisCenter()

    @Published private(set) var cards: [String: DiagnosisCardModel] = [:]

    func card(for entry: String) -> DiagnosisCardModel? { cards[entry] }

    func publish(_ card: DiagnosisCardModel, for entry: String) {
        update { $0.cards[entry] = card }
    }

    func clear(entry: String) {
        update { $0.cards.removeValue(forKey: entry) }
    }

    private func update(_ body: @escaping (DiagnosisCenter) -> Void) {
        let work = { [weak self] in
            guard let self else { return }
            body(self)
        }
        if Thread.isMainThread { work() } else { DispatchQueue.main.async(execute: work) }
    }
}

/// 一轮运行开始/结束时与诊断打交道（Feature 里各调一次）。
enum DiagnoseRun {
    /// 每次进入 Feature 先调：清掉上一张卡，并重置原始行缓冲。
    static func begin(entry: String) {
        ShellRunLog.begin()
        DiagnosisCenter.shared.clear(entry: entry)
    }

    /// 运行结束调：只有该入口/退出码需要出卡时才落日志、调诊断器、发布卡片。
    @discardableResult
    static func finish(entry: String, exitCode: Int32, toolDir: String?) -> DiagnosisCardModel? {
        guard let level = Diagnose.level(entry: entry, exitCode: Int(exitCode)) else {
            return nil
        }
        let lines = ShellRunLog.snapshot()
        let logPath = Diagnose.saveLog(entry: entry, lines: lines)
        var report: DiagnosisReport?
        if let logPath {
            report = Diagnose.invoke(entry: entry, exitCode: Int(exitCode),
                                     logPath: logPath, toolDir: toolDir)
        }
        let card = Diagnose.makeCard(entry: entry, exitCode: Int(exitCode), level: level,
                                     report: report, lines: lines, logPath: logPath)
        DiagnosisCenter.shared.publish(card, for: entry)
        return card
    }
}

/// 纯函数 + 本地落盘；判据一律来自引擎。
enum Diagnose {
    /// 会出卡的五个入口（与 `sources-win/engine.py: DIAGNOSIS_ENTRIES` 同一张表）。
    static let entries: Set<String> = [
        "convert.py", "proofcheck.py", "infocheck.py", "compileall.py", "checkbib.py",
    ]
    static let redExits: [String: Set<Int>] = [
        "convert.py": [2], "infocheck.py": [2], "compileall.py": [1],
    ]
    static let neutralExits: [String: Set<Int>] = [
        "proofcheck.py": [1], "checkbib.py": [1],
    ]

    static let packLimit = 16 * 1024
    static let logKeep = 20

    // MARK: - 红 / 中性口径

    /// 要不要出卡、出哪一种。`nil` = 不出卡。
    static func level(entry: String, exitCode: Int) -> DiagnosisLevel? {
        guard entries.contains(entry), exitCode != 0 else { return nil }
        // 用户取消不是失败（Shell.cancelledStatus = -2）
        if exitCode == Int(Shell.cancelledStatus) { return nil }
        if exitCode < 0 { return .error }
        if redExits[entry]?.contains(exitCode) == true { return .error }
        if neutralExits[entry]?.contains(exitCode) == true { return .neutral }
        return nil
    }

    // MARK: - 脱敏（与引擎 `_SECRET_PATTERNS` 同一口径）

    private static let secretPatterns: [NSRegularExpression] = {
        let raw = [
            #"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{3,}"#,
            #"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|slcn_llm_key)\s*[=:]\s*[^\s,，;；]+"#,
            #"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"#,
        ]
        return raw.compactMap { try? NSRegularExpression(pattern: $0) }
    }()

    /// 把日志/诊断包里的密钥字样替换成 `***`（确定性、幂等）。
    static func maskSecrets(_ text: String) -> String {
        var out = text
        for re in secretPatterns {
            // 每次替换后字符串长度可能变化，range 必须重算（否则会越界）。
            let range = NSRange(out.startIndex..<out.endIndex, in: out)
            out = re.stringByReplacingMatches(in: out, options: [], range: range,
                                              withTemplate: "***")
        }
        return out
    }

    // MARK: - 应用缓存日志（D-05）

    static func logsDirectory() -> String {
        let fm = FileManager.default
        let base: URL
        if let support = try? fm.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                     appropriateFor: nil, create: true) {
            base = support
        } else {
            base = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".local/share")
        }
        let dir = base.appendingPathComponent("编辑部工具箱/logs").path
        try? fm.createDirectory(atPath: dir, withIntermediateDirectories: true)
        return dir
    }

    /// 日志轮转的纯函数：按文件名时间戳前缀排序，删除除最后 `keep` 个之外的全部。
    static func logsToDelete(names: [String], keep: Int = Diagnose.logKeep) -> [String] {
        let stamped = names.filter { name in
            name.range(of: #"^\d{8}-\d{6}-"#, options: .regularExpression) != nil
        }.sorted()
        guard keep >= 0, stamped.count > keep else { return [] }
        return Array(stamped.prefix(stamped.count - keep))
    }

    /// 落盘一次运行的日志（已脱敏），并只留最近 20 份。返回文件路径。
    static func saveLog(entry: String, lines: [String], directory: String? = nil,
                        stamp: String? = nil, keep: Int = Diagnose.logKeep) -> String? {
        let folder = directory ?? logsDirectory()
        let fm = FileManager.default
        try? fm.createDirectory(atPath: folder, withIntermediateDirectories: true)
        let stem = entry.hasSuffix(".py") ? String(entry.dropLast(3)) : entry
        let name = "\(stamp ?? timestamp())-\(stem).log"
        let path = (folder as NSString).appendingPathComponent(name)
        let body = lines.map { $0.trimmingCharacters(in: .newlines) + "\n" }.joined()
        do {
            try maskSecrets(body).write(toFile: path, atomically: true, encoding: .utf8)
        } catch {
            return nil
        }
        if let names = try? fm.contentsOfDirectory(atPath: folder) {
            for victim in logsToDelete(names: names, keep: keep) {
                try? fm.removeItem(atPath: (folder as NSString).appendingPathComponent(victim))
            }
        }
        return path
    }

    private static func timestamp() -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyyMMdd-HHmmss"
        return f.string(from: Date())
    }

    // MARK: - 调引擎诊断器（旧引擎 → nil，走回退）

    static func invoke(entry: String, exitCode: Int, logPath: String,
                       toolDir: String?) -> DiagnosisReport? {
        guard let toolDir,
              let py = EngineLocator.locatePython(toolDir: toolDir),
              let tool = EngineLocator.tool(named: "diagnose.py",
                                            convertTool: PathKit.join(toolDir, "convert.py"))
        else { return nil }
        let args = [tool, "--entry", entry, "--exit-code", String(exitCode),
                    "--log", logPath, "--json"]
        guard let result = Shell.capture(py, args, workingDirectory: toolDir,
                                         environment: EngineLocator.environment(toolDir: toolDir)),
              result.succeeded,
              let data = result.output.data(using: .utf8),
              let report = try? JSONDecoder().decode(DiagnosisReport.self, from: data),
              report.schema == "diagnose.v1",
              !report.entry.isEmpty, !report.category.isEmpty,
              !report.title.isEmpty, !report.reason.isEmpty
        else { return nil }
        return report
    }

    /// 「查看文档」：契约 → 使用说明 → FAQ / 已知限制，取第一个真实存在的。
    static func docPath(toolDir: String?) -> String? {
        var candidates: [String] = []
        if let toolDir {
            candidates.append(PathKit.join(toolDir, "docs/ENGINE_API.md"))
            let repo = (toolDir as NSString).deletingLastPathComponent
            candidates.append(PathKit.join(repo, "使用说明.md"))
            candidates.append(PathKit.join(repo, "docs/reference/faq.md"))
            candidates.append(PathKit.join(repo, "docs/reference/known-limitations.md"))
        }
        return candidates.first { FileManager.default.fileExists(atPath: $0) }
    }

    // MARK: - 卡片与诊断包

    static func makeCard(entry: String, exitCode: Int, level: DiagnosisLevel,
                         report: DiagnosisReport?, lines: [String],
                         logPath: String?) -> DiagnosisCardModel {
        let fallback = report == nil
        let title = report?.title ?? (level == .error ? "本次运行失败" : "本次运行有需要处理的结果")
        let category = report?.category ?? "old-engine"
        let reason = report?.reason ?? (
            "诊断器不可用（旧版引擎没有 diagnose.py，或诊断本身失败）："
            + "先按退出码与下面的日志尾部排查；升级后会有完整诊断。")
        let steps = (report?.nextSteps.isEmpty == false ? report!.nextSteps : defaultSteps)
        let evidence = (report?.evidence.logTail.isEmpty == false
                        ? report!.evidence.logTail : Array(lines.suffix(10)))
        let pack = pack(entry: entry, exitCode: exitCode, level: level, report: report,
                        logTail: Array(lines.suffix(10)),
                        engineVersion: report?.engineVersion ?? "",
                        generatedAt: report?.generatedAt)
        return DiagnosisCardModel(entry: entry, level: level, exitCode: exitCode,
                                  title: title, category: category, reason: reason,
                                  steps: Array(steps.prefix(4)), evidence: evidence,
                                  logPath: logPath, pack: pack, fallback: fallback)
    }

    static let defaultSteps = [
        "看「日志证据」里最后几行的报错，按提示修正输入或环境后重跑。",
        "仍失败就点「复制诊断包」，把这份本地文本发给维护者。",
        "需要完整契约时点「查看文档」看 ENGINE_API.md §十一。",
    ]

    /// 本地诊断包（零上传）：类别/原因/下一步 + 脱敏日志尾部；硬上限 16KB。
    static func pack(entry: String, exitCode: Int, level: DiagnosisLevel,
                     report: DiagnosisReport?, logTail: [String],
                     engineVersion: String = "", generatedAt: String? = nil,
                     envcheckJSON: String = "") -> String {
        let stamp = generatedAt ?? displayTimestamp()
        var head = [
            "编辑部工具箱 · 失败诊断包（本地生成，不会上传）",
            "生成时间：\(stamp)",
            "入口：\(entry)",
            "退出码：\(exitCode)",
            "卡片：\(level == .error ? "红色（失败）" : "中性（有结果）")",
        ]
        if let report {
            head += [
                "类别：\(report.category)",
                "标题：\(report.title)",
                "原因：\(report.reason)",
                "置信度：\(report.confidence.map { String($0) } ?? "")",
                "引擎版本：\(report.engineVersion ?? engineVersion)",
                "下一步：",
            ]
            head += report.nextSteps.enumerated().map { "  \($0.offset + 1). \($0.element)" }
            head.append("依据文档：\(report.docAnchor)")
        } else {
            head += [
                "类别：（诊断器不可用：旧版引擎没有 diagnose.py，或本次诊断本身失败）",
                "标题：本次运行没有成功",
                "原因：只拿到退出码。先看下面的日志尾部；升级到带 diagnose.py 的版本后会有完整诊断。",
                "下一步：",
                "  1. 看「日志尾部」最后几行的报错，按提示修正输入或环境后重跑。",
                "  2. 若反复失败，点「复制诊断包」把这份文本发给维护者；"
                + "日志文件在应用缓存目录（「打开日志」按钮可直接打开）。",
                "  3. 需要完整契约时看 Engine/docs/ENGINE_API.md §十一。",
                "引擎版本：\(engineVersion)",
            ]
        }
        var footer = ["环境自检：见「设置 → 环境检查」；诊断包不含 envcheck.json 原文。"]
        if !envcheckJSON.isEmpty {
            footer[0] = "环境自检（节选）：\n" + String(maskSecrets(envcheckJSON).prefix(2000))
        }
        footer.append(
            "说明：本文件只含退出码、诊断结论与脱敏后的日志尾部；不含稿件正文、"
            + "作者信息、bib 条目或 API Key；不会上传任何地方。")
        var evidence = logTail.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .map { maskSecrets($0) }

        func render() -> String {
            var body = ["证据（日志末尾，已脱敏）："]
            body += evidence.isEmpty ? ["（本次没有捕获到日志行）"] : evidence
            return (head + [""] + body + footer).joined(separator: "\n") + "\n"
        }

        var text = render()
        while text.utf8.count > packLimit && evidence.count > 1 {
            evidence.removeFirst()
            text = render()
        }
        if text.utf8.count > packLimit {
            // 按**字节**截断（中文一个字 3 字节，按字符数截会超 16KB）
            let budget = packLimit - 64
            var used = 0
            var end = text.startIndex
            for ch in text {
                let n = String(ch).utf8.count
                if used + n > budget { break }
                used += n
                end = text.index(after: end)
            }
            text = String(text[..<end]) + "\n…（已截断）\n"
        }
        return text
    }

    private static func displayTimestamp() -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f.string(from: Date())
    }
}
