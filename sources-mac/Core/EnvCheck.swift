import Foundation

/// 首次运行环境自检（B-283）：调 `Engine/envcheck.py --json`，把结论排版成人话，并打诊断包。
///
/// 为什么判据不在这里：查什么、什么算 ok/warn/fail、退出码几，全部在
/// `Engine/envcheck.py`（Windows 壳读的是同一份输出）。这一层只做四件事：
/// 解析 JSON、排成给人看的几行、首跑只做一次、导出 zip。
///
/// **非阻断**（spec §4）：缺依赖不挡任何页面 —— 不装 LibreOffice 也能转换纯 LaTeX 稿。
/// 所以这里**没有**「检查失败就禁用某某按钮」的分支；首跑有必需项缺失时只弹一次提示，
/// 弹完照样进主界面，之后随时能在「设置」页重跑。
enum EnvCheck {

    /// 引擎 `--json` 的 schema 标识（不是这个值就当没读到）。
    static let schema = "envcheck.v1"

    /// `envCheckDone` 的键名**只有这一处**（`AppSettings.envCheckDoneKey` 与它同值，
    /// 因为 `ViewModels.swift` 要能在不编译本文件的测试台里活着 —— 由 ai_settings 台断言一致）。
    static var doneKey: String { AppSettings.envCheckDoneKey }

    /// 检查项固定顺序（与 `Engine/envcheck.py: CHECK_IDS` 同一份；ai_settings 台逐项比对）。
    static let checkIDs = [
        "engine_bundle", "xelatex", "biber", "libreoffice",
        "git", "disk_space", "output_writable", "template_bundle",
    ]

    /// 诊断包里的两个文件。C5：解压出来必须有这两样。
    static let diagnosticsName = "diagnostics.txt"
    static let reportName = "envcheck.json"
    static var packageEntries: [String] { [diagnosticsName, reportName] }

    /// 结论记号（与 Windows `envcheck_logic.MARKS` 逐字一致）。
    static func mark(_ status: String) -> String {
        switch status {
        case "ok": return "✅"
        case "warn": return "⚠️"
        case "fail": return "❌"
        default: return "·"
        }
    }

    static func levelText(_ level: String) -> String {
        switch level {
        case "required": return "必需"
        case "optional": return "可选"
        case "info": return "信息"
        default: return level
        }
    }

    /// 一项检查（字段与 JSON 一一对应；`install` 里只要当前平台用得上，但两个平台都留着）。
    struct Item: Equatable {
        var id: String
        var title: String
        var level: String
        var status: String
        var version: String
        var detail: String
        var hint: String
        var installMac: String
        var installWindows: String

        var mark: String { EnvCheck.mark(status) }
        var levelText: String { EnvCheck.levelText(level) }
    }

    /// 一份报告。`ok/warn/fail` 是引擎算好的计数（这一层不重算判据）。
    struct Report: Equatable {
        var schema: String
        var profile: String
        var platform: String
        var items: [Item]
        var ok: Int
        var warn: Int
        var fail: Int

        /// 必需项缺失数（首跑弹窗的触发条件就是它，不是「有 warn」）。
        var requiredFailures: [Item] {
            items.filter { $0.level == "required" && $0.status != "ok" }
        }
    }

    /// 从引擎输出里抠出报告；读不到返回 nil（界面说「没读到」而不是崩）。
    ///
    /// ⚠️ `Shell.capture` 把 stdout 与 stderr 合进同一个管道，引擎的告警行会混在
    /// JSON 前后 —— 所以按「第一个 `{` 到最后一个 `}`」取子串，与 Windows 侧同一口径。
    static func parse(_ text: String) -> Report? {
        guard let start = text.firstIndex(of: "{"),
              let end = text.lastIndex(of: "}"), start < end else { return nil }
        let body = String(text[start...end])
        guard let data = body.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              (obj["schema"] as? String) == schema else { return nil }
        let items = (obj["checks"] as? [[String: Any]] ?? []).map { raw -> Item in
            let install = raw["install"] as? [String: Any] ?? [:]
            return Item(id: raw["id"] as? String ?? "",
                        title: raw["title"] as? String ?? "",
                        level: raw["level"] as? String ?? "",
                        status: raw["status"] as? String ?? "",
                        version: raw["version"] as? String ?? "",
                        detail: raw["detail"] as? String ?? "",
                        hint: raw["hint"] as? String ?? "",
                        installMac: install["mac"] as? String ?? "",
                        installWindows: install["windows"] as? String ?? "")
        }
        let summary = obj["summary"] as? [String: Any] ?? [:]
        return Report(schema: obj["schema"] as? String ?? "",
                      profile: obj["profile"] as? String ?? "",
                      platform: obj["platform"] as? String ?? "",
                      items: items,
                      ok: summary["ok"] as? Int ?? 0,
                      warn: summary["warn"] as? Int ?? 0,
                      fail: summary["fail"] as? Int ?? 0)
    }

    /// 结果区正文：每项一行 + 缩进的说明；**只有缺项**才打安装提示（与 Windows 同口径）。
    static func humanLines(_ report: Report) -> [String] {
        var lines: [String] = []
        for item in report.items {
            let version = item.version.isEmpty ? "" : "  \(item.version)"
            let level = item.level == "required" ? "" : "（\(item.levelText)）"
            lines.append("\(item.mark) \(item.title)\(level)\(version)")
            if !item.detail.isEmpty { lines.append("    \(item.detail)") }
            if item.status != "ok" && !item.hint.isEmpty { lines.append("    → \(item.hint)") }
        }
        if lines.isEmpty { lines = ["没读到检查结果（引擎输出不是 envcheck.v1 JSON）。"] }
        return lines
    }

    /// 一行小结：先讲结论，再报计数（退出码 2 / 1 / 0 的口径）。与 Windows 逐字一致。
    static func summaryLine(_ report: Report) -> String {
        if report.items.isEmpty { return "没读到检查结果。" }
        if report.fail > 0 { return "❌ 有必需项缺失（\(report.fail) 项）——按提示装好再重跑。" }
        if report.warn > 0 {
            return "⚠️ 必需项全部就绪；\(report.warn) 项可选依赖缺失，相关功能受限。"
        }
        return "✅ 全部就绪（\(report.ok) 项）。"
    }

    /// 首跑提示的正文：只列必需项缺失 + 一句「不挡其它功能」。
    static func firstRunAlertText(_ report: Report) -> String {
        let missing = report.requiredFailures
        guard !missing.isEmpty else { return "" }
        let titles = missing.map(\.title).joined(separator: "、")
        return "本机还缺 \(missing.count) 项必需依赖：\(titles)。\n\n"
            + "装好后在「设置」页点「环境检查」重跑即可；缺依赖不挡任何页面，"
            + "转换与核对照旧能用。安装命令在「设置」→「环境检查」里可以直接复制。"
    }

    /// 首跑要不要做：只在 **没做过** 时做一次（手动重跑永远可用，不走这个判断）。
    static func shouldRunFirstCheck(done: Bool) -> Bool { !done }

    /// 引擎参数（`Shell.capture(python, [envcheckScript] + arguments(outDir:))`）。
    static func arguments(outDir: String) -> [String] {
        ["--json", "--out-dir", outDir]
    }

    /// 诊断包里的 `diagnostics.txt`。
    ///
    /// ⚠️ **隐私纪律**：只拼「版本 / 系统 / 形态 / 检查结论」。不得写入稿件路径或标题、
    /// 作者信息、API Key、引用库内容 —— 函数签名里就**没有**传这些的入口，以后也不许加
    /// （`payload` 是 envcheck 报告，本身不含它们）。
    static func diagnosticsText(report: Report, version: String, generatedAt: String) -> String {
        var lines = [
            "编辑部工具箱 · 环境诊断",
            "生成时间：\(generatedAt.isEmpty ? "（未记录）" : generatedAt)",
            "App 版本：\(version.isEmpty ? "未知" : version)",
            "运行形态：\(report.profile == "exe" ? "发布包（exe）" : "源码（Python）")",
            "操作系统：\(report.platform.isEmpty ? "未知" : report.platform)",
            "",
            "—— 检查结果 ——",
        ]
        lines += humanLines(report)
        lines += ["", summaryLine(report), "",
                  "（本文件不含稿件内容、作者信息、API Key 或引用库内容。）"]
        return lines.joined(separator: "\n") + "\n"
    }

    /// 把诊断包的两个文件写进目录，返回文件 URL（顺序 = `packageEntries`）。
    ///
    /// `envcheck.json` 不直接转发引擎 stdout：诊断包要能**单独**看懂，
    /// 也不该把引擎日志里的杂行带进去（`Shell.capture` 合并了 stderr）。
    static func writeDiagnostics(report: Report, version: String, generatedAt: String,
                                 in directory: URL) throws -> [URL] {
        let text = diagnosticsText(report: report, version: version, generatedAt: generatedAt)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes]
        let payload = EnvCheckJSON(
            schema: report.schema, profile: report.profile, platform: report.platform,
            checks: report.items.map { item in
                EnvCheckJSON.Item(id: item.id, title: item.title, level: item.level,
                                  status: item.status, version: item.version,
                                  detail: item.detail, hint: item.hint,
                                  install: EnvCheckJSON.Install(mac: item.installMac,
                                                                windows: item.installWindows))
            },
            summary: EnvCheckJSON.Summary(ok: report.ok, warn: report.warn, fail: report.fail))
        let encoded = try encoder.encode(payload)
        let json = String(data: encoded, encoding: .utf8) ?? "{}"
        let files = [
            (diagnosticsName, text),
            (reportName, json + "\n"),
        ]
        var urls: [URL] = []
        for (name, body) in files {
            let url = directory.appendingPathComponent(name)
            try body.data(using: .utf8)?.write(to: url, options: .atomic)
            urls.append(url)
        }
        return urls
    }

    /// 用系统的 `/usr/bin/zip` 打包（Foundation 没有 zip API；不引第三方依赖）。
    static func zip(_ files: [URL], to url: URL) -> Bool {
        guard !files.isEmpty else { return false }
        try? FileManager.default.removeItem(at: url)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/zip")
        process.arguments = ["-j", "-q", url.path] + files.map(\.path)
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return false
        }
        process.waitUntilExit()
        return process.terminationStatus == 0
            && FileManager.default.fileExists(atPath: url.path)
    }

    /// 默认文件名：`环境诊断-20261030-120000.zip`（时间只用于区分，不含任何内容）。
    static func defaultZipName(_ date: Date = Date()) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return "环境诊断-\(formatter.string(from: date)).zip"
    }
}

/// 诊断包里 `envcheck.json` 的编码结构：键名与 `envcheck.v1` **逐字一致**。
///
/// 为什么另立一个类型而不转发引擎的原始 JSON：`Shell.capture` 把 stderr 并进了
/// stdout，原始输出里可能混着告警行；诊断包是给人看的证据，必须能单独解析。
/// （文件作用域 + private：`.swift` 里声明在函数内的嵌套类型在旧编译器上会多余报错。）
private struct EnvCheckJSON: Encodable {
    struct Install: Encodable {
        let mac: String
        let windows: String
    }

    struct Item: Encodable {
        let id: String
        let title: String
        let level: String
        let status: String
        let version: String
        let detail: String
        let hint: String
        let install: Install
    }

    struct Summary: Encodable {
        let ok: Int
        let warn: Int
        let fail: Int
    }

    let schema: String
    let profile: String
    let platform: String
    let checks: [Item]
    let summary: Summary
}

/// 「设置」页与首跑共用的状态机：跑一次自检、解析、置位 `envCheckDone`。
///
/// 首跑与手动重跑**同一条路径**（`start(firstRun:)`），区别只有两处：
/// 首跑结束后无条件置位 `envCheckDone`（**只弹一次**），且只有必需项缺失才弹提示。
/// 所有 `@Published` 只在主线程改（`DispatchQueue.main.async` 回投）。
final class EnvCheckModel: ObservableObject {
    @Published private(set) var running = false
    @Published private(set) var lines: [String] = ["点「环境检查」看本机缺什么依赖（不联网、不改任何文件、不挡任何功能）。"]
    @Published private(set) var statusLine = ""
    @Published private(set) var report: EnvCheck.Report?
    /// 首跑提示（非阻断 Alert）；手动重跑**不**弹。
    @Published var showFirstRunAlert = false
    @Published var firstRunAlertText = ""

    private let defaults: UserDefaults
    /// 首跑时若窗口那边的 `AppSettings` 在，就把「已检查」同步到它的内存状态；
    /// 不在（App 刚启动）也照样写 UserDefaults —— 下次启动读到就是 true。
    weak var settings: AppSettings?

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    var done: Bool { defaults.bool(forKey: EnvCheck.doneKey) }

    func markDone() {
        defaults.set(true, forKey: EnvCheck.doneKey)
        settings?.markEnvCheckDone()
    }

    /// 首跑入口：没做过才跑（`ToolboxApp` 的 `.task` 调它）。
    func runIfNeeded() {
        guard EnvCheck.shouldRunFirstCheck(done: done), !running else { return }
        start(firstRun: true)
    }

    /// 「设置」页的手动重跑（随时可用，不受 `envCheckDone` 限制）。
    func rerun() { start(firstRun: false) }

    /// 导出 / 复制之后往状态行写一句结果（视图不直接改 `@Published`，保持单一写入口）。
    func note(_ text: String) { statusLine = text }

    /// 找引擎与解释器；找不到就只给一句状态（**不弹错误框** —— 检查是辅助功能）。
    static func locate() -> (python: String, script: String)? {
        guard let engine = EngineLocator.convertTool(),
              let python = EngineLocator.locatePython(toolDir: (engine as NSString).deletingLastPathComponent),
              let tool = EngineLocator.tool(named: "envcheck.py", convertTool: engine) else {
            return nil
        }
        return (python, tool)
    }

    func start(firstRun: Bool) {
        guard !running else { return }
        guard let located = EnvCheckModel.locate() else {
            statusLine = "❌ 找不到引擎或 Engine/envcheck.py：请先在「设置」里指定 Engine 目录。"
            if firstRun { markDone() }
            return
        }
        running = true
        statusLine = "⏳ 检查中…（几秒；不联网、不改任何文件）"
        lines = ["检查中…"]
        let outDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("slcn-envcheck-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        let engineDir = (located.script as NSString).deletingLastPathComponent
        let python = located.python
        let script = located.script
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Shell.capture(python, [script] + EnvCheck.arguments(outDir: outDir.path),
                                       environment: EngineLocator.environment(toolDir: engineDir))
            let text = result?.output ?? ""
            let parsed = EnvCheck.parse(text)
            try? FileManager.default.removeItem(at: outDir)
            DispatchQueue.main.async {
                self.running = false
                self.report = parsed
                if let parsed {
                    self.lines = EnvCheck.humanLines(parsed) + ["", EnvCheck.summaryLine(parsed)]
                    self.statusLine = EnvCheck.summaryLine(parsed)
                } else {
                    self.lines = ["没读到检查结果（引擎没有输出 envcheck.v1 JSON）。"]
                    self.statusLine = "❌ 没读到检查结果。"
                }
                if firstRun {
                    self.markDone()
                    if let parsed, parsed.fail > 0 {
                        self.firstRunAlertText = EnvCheck.firstRunAlertText(parsed)
                        self.showFirstRunAlert = true
                    }
                }
            }
        }
    }

    /// 「设置」页导出用：把这次结果写成两个文件并打包（返回 zip 路径或 nil）。
    func exportPackage(to url: URL, version: String) -> URL? {
        guard let report else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        let dir = url.deletingLastPathComponent()
        guard let files = try? EnvCheck.writeDiagnostics(
            report: report, version: version,
            generatedAt: formatter.string(from: Date()), in: dir) else { return nil }
        if EnvCheck.zip(files, to: url) {
            for file in files { try? FileManager.default.removeItem(at: file) }
            return url
        }
        for file in files { try? FileManager.default.removeItem(at: file) }
        return nil
    }
}
