import Foundation

enum ToolboxError: LocalizedError {
    case cannotOpenPDF(String)
    case cannotWrite(String)
    case toolMissing(String)
    case notADirectory(String)

    var errorDescription: String? {
        switch self {
        case .cannotOpenPDF(let p): return "无法打开 PDF：\(p)"
        case .cannotWrite(let p): return "无法写入：\(p)"
        case .toolMissing(let t): return "未找到命令 \(t)（请确认已安装 TeX Live / MacTeX）"
        case .notADirectory(let p): return "路径不是文件夹：\(p)"
        }
    }
}

/// 路径与文件的小工具。原程序的路径拼接、glob、时间戳都集中在这里。
enum PathKit {

    static func exists(_ path: String) -> Bool {
        FileManager.default.fileExists(atPath: path)
    }

    static func isDirectory(_ path: String) -> Bool {
        var isDir: ObjCBool = false
        let ok = FileManager.default.fileExists(atPath: path, isDirectory: &isDir)
        return ok && isDir.boolValue
    }

    static func join(_ a: String, _ b: String) -> String {
        (a as NSString).appendingPathComponent(b)
    }

    /// 把用户输入整理成绝对路径（支持 ~ 展开、去掉首尾空白）
    static func normalize(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let expanded = (trimmed as NSString).expandingTildeInPath
        let standardized = (expanded as NSString).standardizingPath
        guard !standardized.hasPrefix("/") else { return standardized }
        // 相对路径：按当前工作目录补成绝对路径。
        // 否则「上一期目录在哪」「总第 N 期是几」这类靠目录名推断的逻辑会失效。
        return (FileManager.default.currentDirectoryPath as NSString)
            .appendingPathComponent(standardized)
    }

    /// 等价于 Python 的 glob.glob(dir + '/[0-9][0-9]*')：
    /// 取名字以两位以上数字开头的条目。
    ///
    /// ★ 与原版的差异：原版依赖文件系统返回顺序，这里显式排序。
    ///   原版在 Windows 上恰好按 NTFS 顺序拿到 01…10，而在 macOS 上目录顺序不保证，
    ///   所以必须排序才能保证合并后的页序正确。
    static func numberedEntries(in dir: String) -> [String] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []
        return names
            .filter { $0.range(of: "^[0-9][0-9]", options: .regularExpression) != nil }
            .sorted()
    }

    /// 对应 strftime。默认格式与原版生成合并文件名时一致。
    static func timestamp(_ format: String = "yyyy_MM_dd_HH_mm_ss") -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = format
        return formatter.string(from: Date())
    }

    static func readText(_ path: String) throws -> String {
        try String(contentsOfFile: path, encoding: .utf8)
    }

    static func writeText(_ text: String, to path: String) throws {
        try text.write(toFile: path, atomically: true, encoding: .utf8)
    }
}

/// 调用外部命令（XeLaTeX、biber 等）。
///
/// ★ 与原版的差异：原版用 os.system 并写 `>> NUL`（Windows 的空设备）。
///   macOS 上必须改成 /dev/null，并且要显式补齐 PATH ——
///   从 Finder 双击启动的 App 不会继承终端里的 PATH，
///   而 TeX Live 装在 /Library/TeX/texbin，不在默认 PATH 里。
/// 引擎定位：**只此一份**。
///
/// 为什么下沉到这里：mac 界面有好几页要调引擎（初排转换 / 文献回读校验 / 一校核查 / 生成目录…），
/// 每个都自己找一遍引擎，迟早出现「设置里指了自定义路径，某一页却还去包里找」这种两个真相的局面。
/// 搜索顺序与顺序本身一样重要 —— 用户指定 → app 包内 → 从 app 所在目录逐级向上找仓库。
enum EngineLocator {

    /// 「设置」页里指定的自定义引擎路径（键名沿用历史的 `convertToolPath`，不要改，
    /// 否则老用户的设置会丢）。
    static let overrideKey = "convertToolPath"

    /// 找引擎目录下的某个入口（如 `convert.py` / `toc.py` / `proofcheck.py`）。
    static func tool(named fileName: String, convertTool: String? = convertTool()) -> String? {
        guard let convert = convertTool else { return nil }
        let dir = (convert as NSString).deletingLastPathComponent
        let candidate = (dir as NSString).appendingPathComponent(fileName)
        return FileManager.default.fileExists(atPath: candidate) ? candidate : nil
    }

    /// 找 `convert.py`：用户指定 → app 包内 → 从 app 往上找仓库。
    static func convertTool() -> String? {
        let fm = FileManager.default

        if let custom = UserDefaults.standard.string(forKey: overrideKey),
           !custom.isEmpty, fm.fileExists(atPath: custom) {
            return custom
        }
        if let res = Bundle.main.resourcePath {
            let p = res + "/Engine/convert.py"
            if fm.fileExists(atPath: p) { return p }
        }
        var dir = Bundle.main.bundleURL.deletingLastPathComponent()
        for _ in 0..<6 {
            let p = dir.path + "/Engine/convert.py"
            if fm.fileExists(atPath: p) { return p }
            let parent = dir.deletingLastPathComponent()
            if parent.path == dir.path { break }
            dir = parent
        }
        return nil
    }

    /// 「设置」页里可选的私有笔记仓路径（键名固定；老用户没有这个键时自动探测）。
    static let notesRepoOverrideKey = "notesRepoPath"

    /// 找私有笔记仓（案例法 / 引用库 `bib_db/` 所在）。
    ///
    /// 为什么外壳要找它：引用库接进核实链路后（第 129 条），引擎要按 `SLCN_NOTES` 定位
    /// `<笔记仓>/bib_db/bibliography.jsonl`。仓库并排布局时笔记仓是**引擎仓的同级目录**
    /// （`GitHub/EditToolBox-notes`），而 app 包内引擎在 `dist/…/Resources/Engine`，
    /// 引擎自己的 `paths.py` 从包内往上 4 层找不到仓库根 —— 所以由外壳算好、用环境变量传进去。
    /// 顺序：用户指定 → 从引擎目录逐级向上找「同级 `EditToolBox-notes`」。
    static func notesRepo() -> String? {
        let fm = FileManager.default
        if let custom = UserDefaults.standard.string(forKey: notesRepoOverrideKey),
           !custom.isEmpty, fm.fileExists(atPath: custom) {
            return custom
        }
        guard let engine = convertTool() else { return nil }
        var dir = (engine as NSString).deletingLastPathComponent
        for _ in 0..<8 {
            let parent = (dir as NSString).deletingLastPathComponent
            for candidate in [
                (parent as NSString).appendingPathComponent("EditToolBox-notes"),
                (dir as NSString).appendingPathComponent("EditToolBox-notes"),
            ] where looksLikeNotesRepo(candidate) {
                return candidate
            }
            if parent == dir { break }
            dir = parent
        }
        return nil
    }

    /// 判据：有引用库或交接说明才算笔记仓（避免把同名空目录当真）。
    private static func looksLikeNotesRepo(_ path: String) -> Bool {
        let fm = FileManager.default
        return fm.fileExists(atPath: path + "/bib_db/bibliography.jsonl")
            || fm.fileExists(atPath: path + "/HANDOFF.md")
    }

    /// 找可用的 python3（引擎跑在它上面）。
    static func locatePython(toolDir: String) -> String? {
        let candidates = [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ]
        for c in candidates where FileManager.default.fileExists(atPath: c) {
            return c
        }
        return nil
    }

    // MARK: - Python 版本校验（U8）
    /// 引擎要 **Python ≥ 3.10**（3.9 会 ImportError，或更糟：某些路径静默给错结果）。
    /// 体检发现外壳的候选列表里就有 `/usr/bin/python3` = 3.9.6。这里把"版本不对"变成一句明确警告。
    /// 只在**真正要跑引擎之前**探一次（结果缓存）；启动时不探。
    private static var versionCache: [String: String] = [:]
    static func pythonVersion(_ path: String) -> String {
        if let cached = versionCache[path] { return cached }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: path)
        proc.arguments = ["-c", "import sys;print('%d.%d' % sys.version_info[:2])"]
        let pipe = Pipe(); proc.standardOutput = pipe; proc.standardError = Pipe()
        var version = "?"
        do {
            try proc.run(); proc.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            if let t = String(data: data, encoding: .utf8) {
                version = t.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        } catch { version = "?" }
        versionCache[path] = version
        return version
    }
    static func pythonVersionWarning(_ path: String) -> String? {
        guard path.lowercased().contains("python") else { return nil }
        let parts = pythonVersion(path).split(separator: ".").compactMap { Int($0) }
        guard parts.count >= 2 else { return nil }
        if parts[0] > 3 || (parts[0] == 3 && parts[1] >= 10) { return nil }
        return "⚠ 这个解释器是 Python \(parts[0]).\(parts[1])（引擎需要 3.10+）：\n"
            + "  它可能 ImportError，或在某些路径上静默给出错结果。\n"
            + "  装一个 3.10+：brew install python@3.12（之后重开 App 即可）。\n"
    }

    /// 给引擎子进程的环境变量增量：项目本地 `vendor/`（引擎依赖装在那里）。
    static func environment(toolDir: String) -> [String: String] {
        ["PYTHONPATH": (toolDir as NSString).appendingPathComponent("vendor")]
    }
}

extension String {
    /// 去掉界面文案里的 markdown 记号（`\*\*强调\*\*`、反引号代码）。
    ///
    /// SwiftUI 的 `Text(String)` **不解析** markdown（只有 `LocalizedStringKey` 才解析），
    /// 字符串里写着 `**一期的目录**` 就会把星号原样画出来 —— 编辑看到「选**一期的目录**」。
    /// 与 Windows 侧同一个毛病（那边 2026-10-17 截图发现后加了 `plain()`）。只用于**界面文案**；
    /// 运行日志保持引擎原文（那是证据，两侧一致）。
    var plainMarkdown: String {
        replacingOccurrences(of: "**", with: "")
            .replacingOccurrences(of: "`", with: "")
    }
}

/// 从引擎的**契约行**（stdout 最后一行）里抠出产物路径 —— 与 Windows 侧
/// `sources-win/engine.py` 的 `artifact_path()` 同一套判据（两平台不许有两份判据）：
/// 对每个「路径起点」取**文件系统上真的存在的最长前缀**，这样路径里的括号、尾巴上的
/// `（共 176 页）` / `）` / `。` 都不会误伤；期目录名里就带半角括号（`2026年第5期(90)`）。
enum EngineOutput {

    /// 路径后面允许出现、且表示"路径到此为止"的字符（与 Windows 侧 `_PATH_BOUNDARY` 同一套）。
    /// ⚠️ 必须同时含**左括号**：尾部注释形如 `（共 176 页）`，路径紧挨的是 `（` 不是 `）`。
    static let pathBoundary: Set<Character> = Set("（）()「」『』【】[]{}”。.…，,；;：: \t\n\r\"'")

    static func artifactPath(from line: String) -> String? {
        guard !line.isEmpty else { return nil }
        let scalars = Array(line)
        var best = ""
        var i = 0
        while i < scalars.count {
            var start: Int? = nil
            if scalars[i] == "/" {
                start = i
            } else if scalars[i].isASCII, scalars[i].isLetter,
                      i + 1 < scalars.count, scalars[i + 1] == ":",
                      i + 2 < scalars.count, scalars[i + 2] == "/" || scalars[i + 2] == "\\" {
                start = i                       // Windows 盘符（C:\ 或 C:/）
            }
            guard let begin = start else { i += 1; continue }
            var end = scalars.count
            while end > begin {
                let candidate = String(scalars[begin..<end])
                    .trimmingCharacters(in: CharacterSet(charactersIn: "。.…"))
                // ⚠️ 候选必须停在**边界字符**上（结尾 / 空格 / 括号 / 标点），
                //    否则「路径不存在」会退化成"存在的父目录"（`/tmp/x` → `/tmp`），
                //    横幅显示一个错路径比不显示更糟。与 Windows 侧同一判据（2026-10-17）。
                let nextScalar = end < scalars.count ? scalars[end] : nil
                let atBoundary = nextScalar == nil || EngineOutput.pathBoundary.contains(nextScalar!)
                if atBoundary, FileManager.default.fileExists(atPath: candidate) {
                    if candidate.count > best.count { best = candidate }
                    break
                }
                end -= 1
            }
            i += 1
        }
        return best.isEmpty ? nil : best
    }

    /// 契约行是不是「成功」：引擎成功时最后一行固定以 `完成。` 开头（`docs/ENGINE_API.md` 五）。
    static func indicatesSuccess(_ line: String) -> Bool {
        line.trimmingCharacters(in: .whitespaces).hasPrefix("完成。")
    }

    /// 从一串日志行里挑出**契约行**（最后一条以 `完成。` 开头的行）；没有返回 nil。
    ///
    /// ⚠️ 完成横幅**必须**用它，不能取"最后一行"：各 Feature 在引擎结束后还会补一句
    /// 自己话术（例如 `CompileFeature` 的「运行结束，请确认上方是否有错。」），
    /// 取最后一行就会把成功的运行判成失败 —— 2026-10-17 第十五批真机实测：
    /// 「编译全部」10/10 全部成功、日志末行是 `完成。编译 10 篇（成功 10 / 失败 0）…`，
    /// 横幅却显示「❌ 失败 / 有错误」。（同类：合并文件页。）
    static func contractLine(from lines: [String]) -> String? {
        for line in lines.reversed() where indicatesSuccess(line) {
            return line
        }
        return nil
    }
}

enum Shell {

    /// U3：用户按「取消」时 `Shell.run` / `Shell.capture` 的返回值。
    ///
    /// 为什么不用真实退出码：被 SIGTERM 终止的子进程 `terminationStatus` 是 **15**，
    /// 而引擎契约里 ≥2 表示"失败" —— 照搬就会把「用户主动取消」讲成「失败」。
    /// 单独一个哨兵，各 Feature 据此**不再补"失败"话术**、视图显示「⏹ 已取消」。
    /// 与 -1（启动失败）互不冲突：真实退出码不会是负数。
    static let cancelledStatus: Int32 = -2

    static let extraPaths = [
        "/Library/TeX/texbin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]

    static func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        var parts = extraPaths
        if let existing = env["PATH"] {
            for p in existing.split(separator: ":") where !parts.contains(String(p)) {
                parts.append(String(p))
            }
        }
        env["PATH"] = parts.joined(separator: ":")
        env["LANG"] = "en_US.UTF-8"
        env["LC_ALL"] = "en_US.UTF-8"
        // 子进程的 stdout 是**管道**（不是终端）→ Python 默认按 8KB **块缓冲**：
        // 运行日志要等缓冲满或进程结束才刷得出来，界面看着就像「卡住了」。
        // 设这个变量让引擎逐行输出，日志才是实时的（GUI 侧本来就是逐块读，见 run()）。
        env["PYTHONUNBUFFERED"] = "1"
        // 第 129 条：把私有笔记仓路径传给引擎 → 参考文献核实可先查编辑部引用库
        // （`<笔记仓>/bib_db/bibliography.jsonl`）。用户已显式设了 SLCN_NOTES 就不覆盖。
        if env["SLCN_NOTES"] == nil, let notes = EngineLocator.notesRepo() {
            env["SLCN_NOTES"] = notes
        }
        return env
    }

    static func locate(_ tool: String) -> String? {
        // 传进来的若**已经是绝对路径**，直接用。
        // 否则下面会把它当命令名拼到每个 PATH 目录后面，变成
        // `/opt/homebrew/bin//opt/homebrew/bin/python3` 这种不存在的路径，
        // 报「未找到命令」——实测「初排转换」就是这么启动失败的。
        if tool.hasPrefix("/") {
            return FileManager.default.isExecutableFile(atPath: tool) ? tool : nil
        }
        guard let path = environment()["PATH"] else { return nil }
        for dir in path.split(separator: ":") {
            let candidate = (String(dir) as NSString).appendingPathComponent(tool)
            if FileManager.default.isExecutableFile(atPath: candidate) {
                return candidate
            }
        }
        return nil
    }

    /// 运行外部命令并把输出实时写入日志。返回退出码，-1 表示启动失败。
    ///
    /// - Parameter stdin: 要递给子进程的**标准输入**。给了就写进一个 `Pipe`（写完关闭，
    ///   子进程读到 EOF）；**不给则与从前完全一样**（不碰 `standardInput`），既有调用方
    ///   的行为一个字节都不变。
    ///   为什么需要它：「文献回读校验」的**粘贴模式**要把编辑粘的 bib 通过标准输入递给
    ///   Python 引擎（`checkbib.py --returned-stdin`），而不是先落一个临时文件再删——
    ///   临时文件既多一次落盘，又会让那个假文件名印进报告头与合并记录，事后对不上账。
    @discardableResult
    static func run(_ tool: String,
                    _ arguments: [String],
                    workingDirectory: String? = nil,
                    discardOutput: Bool = false,
                    environment extraEnv: [String: String]? = nil,
                    stdin: String? = nil,
                    log: Logger?) -> Int32 {
        // U8：引擎调用前检查解释器版本（`Shell.run` 是唯一的调用入口，一处就够）
        if let warning = EngineLocator.pythonVersionWarning(tool) { log?(warning) }
        guard let executable = locate(tool) else {
            log?("未找到命令 \(tool)，请确认已安装 TeX Live / MacTeX。\n")
            return -1
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        var env = environment()
        if let extraEnv {
            for (k, v) in extraEnv { env[k] = v }
        }
        process.environment = env
        if let workingDirectory {
            process.currentDirectoryURL = URL(fileURLWithPath: workingDirectory)
        }

        let pipe = Pipe()
        if discardOutput {
            // 原版的 `>> NUL`
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
        } else {
            process.standardOutput = pipe
            process.standardError = pipe
        }

        // stdin：只有显式给了才接管道。⚠️ 写的动作必须与「读子进程输出」并发，
        // 否则 bib 文本超过管道缓冲（64 KiB）时两边互等 —— 经典管道死锁。
        let stdinPipe: Pipe?
        if stdin != nil {
            let p = Pipe()
            process.standardInput = p
            stdinPipe = p
        } else {
            stdinPipe = nil
        }

        do {
            try process.run()
        } catch {
            log?("启动 \(tool) 失败：\(error.localizedDescription)\n")
            return -1
        }

        // U3：把子进程交给这一轮的取消句柄（「取消」按钮 → 杀整棵进程树，见 RunCancel）。
        // defer 保证每条返回路径都注销：任务结束后句柄里不留已死亡的 Process 引用。
        log?.cancel?.attach(process)
        defer { log?.cancel?.attach(nil) }

        feedStdin(stdin, to: stdinPipe, log: log)

        if !discardOutput {
            let handle = pipe.fileHandleForReading
            while true {
                let data = handle.availableData
                if data.isEmpty { break }
                if let s = String(data: data, encoding: .utf8) {
                    log?(s)
                } else if let s = String(data: data, encoding: .isoLatin1) {
                    log?(s)
                }
            }
        }

        process.waitUntilExit()
        // U3：用户取消 → 返回哨兵（各 Feature 收到后不再说"失败"，视图显示「已取消」）
        if log?.cancel?.isCancelled == true { return Shell.cancelledStatus }
        return process.terminationStatus
    }

    /// 把 `text` 写进子进程的 stdin 管道，然后关闭写端（子进程收到 EOF）。
    ///
    /// 放在后台队列：主线程接下来要一路读到子进程输出结束，写端与读端必须并发。
    /// 用 Foundation 的**可抛出**写法 `write(contentsOf:)`：子进程若提前退出（管道破裂），
    /// 只记一行日志，不让一次写失败把界面带崩。
    private static func feedStdin(_ text: String?, to pipe: Pipe?, log: Logger?) {
        guard let text, let pipe else { return }
        let data = Data(text.utf8)
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try pipe.fileHandleForWriting.write(contentsOf: data)
                try pipe.fileHandleForWriting.close()
            } catch {
                log?("（提示：向子进程标准输入写入失败：\(error.localizedDescription)；"
                     + "若子进程已提前退出，这条可忽略）\n")
            }
        }
    }

    /// 一次外部命令的执行结果
    struct CommandResult {
        let status: Int32
        let output: String
        var succeeded: Bool { status == 0 }
    }

    /// 运行外部命令并捕获全部输出（用于编译：成功时保持安静，失败时回显原因）。
    ///
    /// 两个与原版不同的地方，都是为了在 macOS 上更稳：
    ///   1. 标准输入接到 /dev/null，避免 LaTeX 出错时等待输入把界面卡死；
    ///   2. xelatex 以 -interaction=nonstopmode 运行，同样是为了不挂起。
    ///
    /// - Parameter stdin: 与 `run` 同一个口径：给了就写进管道（用于「粘贴预览」把文本喂给
    ///   `checkbib.py --list-keys`，再把那**一行**输出收回来）；不给仍是 `/dev/null`（一字不变）。
    /// - Parameter cancel: U3 取消句柄。长操作（「重新编译该篇」的四步 TeX）要能取消，
    ///   所以 `capture` 也注册子进程；短操作（`--detect` / 列表预览）不传，行为不变。
    static func capture(_ tool: String,
                        _ arguments: [String],
                        workingDirectory: String? = nil,
                        stdin: String? = nil,
                        environment extraEnv: [String: String]? = nil,
                        cancel: RunCancel? = nil) -> CommandResult? {
        guard let executable = locate(tool) else { return nil }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        var env = environment()
        if let extraEnv {
            for (k, v) in extraEnv { env[k] = v }   // ⚠️ 只叠加增量，见 run() 里的同款说明
        }
        process.environment = env
        let stdinPipe: Pipe?
        if stdin != nil {
            let p = Pipe()
            process.standardInput = p
            stdinPipe = p
        } else {
            process.standardInput = FileHandle.nullDevice
            stdinPipe = nil
        }
        if let workingDirectory {
            process.currentDirectoryURL = URL(fileURLWithPath: workingDirectory)
        }

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe

        do {
            try process.run()
        } catch {
            return nil
        }

        // U3：与 `run` 同一口径 —— 长操作（重新编译）期间「取消」按钮要能杀掉这棵进程树
        cancel?.attach(process)
        defer { cancel?.attach(nil) }

        feedStdin(stdin, to: stdinPipe, log: nil)

        var data = Data()
        let handle = pipe.fileHandleForReading
        while true {
            let chunk = handle.availableData
            if chunk.isEmpty { break }
            data.append(chunk)
        }
        process.waitUntilExit()

        let text = String(data: data, encoding: .utf8) ?? String(decoding: data, as: UTF8.self)
        let status: Int32 = (cancel?.isCancelled == true)
            ? Shell.cancelledStatus : process.terminationStatus
        return CommandResult(status: status, output: text)
    }

    /// 从编译器输出里挑出错误行，供日志显示
    static func errorExcerpt(_ output: String, maxLines: Int = 20) -> String {
        let lines = output.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        var indexes = Set<Int>()

        for (i, line) in lines.enumerated() {
            let isError = line.hasPrefix("! ")
                || line.hasPrefix("!")
                || line.contains("Error")
                || line.contains("error:")
                || line.contains("Fatal")
            if isError {
                for j in i..<min(i + 3, lines.count) { indexes.insert(j) }
            }
            if indexes.count >= maxLines { break }
        }

        if indexes.isEmpty { return "（未能捕获到明确错误信息，可查看该目录下的 .log 文件）" }

        let picked = indexes.sorted().prefix(maxLines).map { lines[$0] }
        return "—— 错误摘要 ——\n" + picked.joined(separator: "\n")
    }
}
