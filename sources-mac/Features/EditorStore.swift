import Foundation

/// 责任编辑名单：**已发表稿件里出现过的**（引擎 `--list-editors` 采集）+ 用户自定义 + 记住默认值。
///
/// 实测全库只有 3 个名字（执子 58 篇、袁之 52 篇、映之 36 篇），所以做成下拉最省事。
/// 自定义名字与「默认」存在用户目录（`~/Library/Application Support/…/editors.json`），
/// **不写进仓库**——仓库是编辑部数据库，原稿只读。
final class EditorStore: ObservableObject {

    /// 采集到的责编（按篇数从多到少）
    @Published private(set) var harvested: [(name: String, count: Int)] = []
    /// 用户自己加的名字
    @Published private(set) var custom: [String] = []
    /// 默认选中项（下次打开时自动用它）
    @Published var defaultName = ""
    @Published var status = ""
    /// 「自定义…」输入框的内容（放在这里而不是 View 的 @State：本机只有
    /// Command Line Tools，SwiftUI 的 @State 宏不可用，实测编译报
    /// `external macro implementation type 'SwiftUIMacros.StateMacro' could not be found`）
    @Published var newName = ""

    /// 下拉候选：采集到的在前，自定义的在后，去重
    var allNames: [String] {
        var seen = Set<String>()
        return (harvested.map { $0.name } + custom).filter { seen.insert($0).inserted }
    }

    private var storeURL: URL {
        let fm = FileManager.default
        let base = (try? fm.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                appropriateFor: nil, create: true))?
            .appendingPathComponent("cn.edu.sysu.logic.toolbox", isDirectory: true)
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        try? fm.createDirectory(at: base, withIntermediateDirectories: true)
        return base.appendingPathComponent("editors.json")
    }

    // MARK: - 持久化（自定义 + 默认）

    func load() {
        guard let data = try? Data(contentsOf: storeURL),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }
        custom = (obj["custom"] as? [String]) ?? []
        defaultName = (obj["default"] as? String) ?? ""
    }

    func save() {
        let obj: [String: Any] = ["custom": custom, "default": defaultName]
        if let data = try? JSONSerialization.data(withJSONObject: obj,
                                                  options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: storeURL)
        }
    }

    func addCustom(_ name: String) {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !n.isEmpty, !allNames.contains(n) else { return }
        custom.append(n)
        save()
    }

    func setDefault(_ name: String) {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !n.isEmpty else { return }
        addCustom(n)
        defaultName = n
        save()
        status = "默认责编：\(n)"
    }

    // MARK: - 采集（引擎扫描全库）

    /// 跑 `convert.py --source <稿子> --list-editors`，输出形如 `执子\t58`。
    func harvest(source: String, tool: String, python: String, toolDir: String, vendor: String?) {
        status = "正在采集全库责编…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: python)
            p.arguments = [tool, "--source", source.isEmpty ? "placeholder.docx" : source,
                           "--list-editors"]
            var env = ProcessInfo.processInfo.environment
            if let vendor { env["PYTHONPATH"] = vendor }
            p.environment = env
            p.currentDirectoryURL = URL(fileURLWithPath: toolDir)
            let out = Pipe()
            p.standardOutput = out
            p.standardError = Pipe()
            var rows: [(String, Int)] = []
            do {
                try p.run()
                let data = out.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                let text = String(data: data, encoding: .utf8) ?? ""
                for line in text.split(separator: "\n") {
                    let cols = line.split(separator: "\t")
                    if cols.count == 2, let n = Int(cols[1].trimmingCharacters(in: .whitespaces)) {
                        rows.append((String(cols[0]), n))
                    }
                }
            } catch {
                rows = []
            }
            DispatchQueue.main.async {
                if !rows.isEmpty {
                    self.harvested = rows.map { (name: $0.0, count: $0.1) }
                    self.status = "已采集 \(rows.count) 位责编（来自全库已发表稿件）"
                } else {
                    self.status = "未能采集责编名单（可选源文件后自动重试）"
                }
            }
        }
    }
}
