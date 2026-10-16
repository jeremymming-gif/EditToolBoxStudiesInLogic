import Foundation

// MARK: - 数据结构（界面表格用；字段名与引擎产物 `信息检查*.json` **逐字对应**）

/// 一篇文章的信息。
///
/// ⚠️ 字段名不能改：它们直接映射引擎写的 `信息检查<时间戳>.json`（第 94 条起由
/// `Engine/slcn/infocheck.py` 产出，与 Swift 版逐字节一致）。改名就解不出来了。
struct ArticleInfo: Codable, Identifiable {
    var folder: String
    var folderName: String
    var volume: String = ""
    var issue: String = ""
    var year: String = ""
    var startPage: String = ""
    var endPage: String = ""
    var pages: String = ""
    var chineseTitle: String?
    var englishTitle: String?
    var firstAuthorEnglish: String?
    var firstAuthorChinese: String?
    var secondAuthorEnglish: String?
    var secondAuthorChinese: String?
    var thirdAuthorEnglish: String?
    var thirdAuthorChinese: String?
    var fontSize: String?
    var lineSpacing: String?
    var actualPDFPages: Int?
    var hasTeX: Bool = false
    var hasPDF: Bool = false

    var id: String { folder }

    /// 声明的页数与 PDF 实际页数是否不符（界面标红）
    var pageMismatch: Bool {
        guard let actual = actualPDFPages,
              let declared = Int(pages.trimmingCharacters(in: .whitespaces)),
              !pages.isEmpty else { return false }
        return declared != actual
    }

    /// 界面展示用：去掉 LaTeX 花括号与换行，便于阅读
    private static func clean(_ s: String?) -> String {
        guard let s, !s.isEmpty else { return "—" }
        var t = s.replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespaces)
        while t.hasPrefix("{") && t.hasSuffix("}") && t.count > 2 {
            t = String(t.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
        }
        return t
    }

    var displayChineseTitle: String { Self.clean(chineseTitle) }
    var displayEnglishTitle: String { Self.clean(englishTitle) }
    var displayAuthors: String {
        let names = [firstAuthorChinese, firstAuthorEnglish]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .map { Self.clean($0) }
        return names.isEmpty ? "—" : names.joined(separator: " / ")
    }
    var displayFontSize: String { Self.clean(fontSize) }
    var displayLineSpacing: String { Self.clean(lineSpacing) }
}

/// 一份检查报告。除了 `articles`，还有界面要用的两样：
/// `inconsistencies`（跨篇不一致，界面顶部提示）与 `txtPath`（「在访达中显示报告」按钮）。
struct InfoReport: Codable {
    var generatedAt: String
    var issueFolder: String
    var articles: [ArticleInfo]

    /// ⚠️ 显式列出只解码这三个键：引擎 `--preview` 的输出里还多一个 `inconsistencies`，
    /// 而产物 .json 里没有 —— 多给的字段要能安全忽略。
    private enum CodingKeys: String, CodingKey {
        case generatedAt, issueFolder, articles
    }

    var txtPath: String { PathKit.join(issueFolder, "信息检查\(generatedAt).txt") }

    /// 跨篇不一致项：卷号/期号/年份不一致、声明页数与实际页数不符。
    ///
    /// 为什么界面这边也算一遍：引擎的产物 .json **刻意不含**这一项（它必须与 Swift 版
    /// 逐字节一致，见第 94 条），而界面要在报告出来后立刻显示 —— 这是纯展示的聚合，
    /// 不涉及稿件判据（判据仍在引擎里）。
    var inconsistencies: [String] {
        var notes: [String] = []
        let volumes = Set(articles.map(\.volume).filter { !$0.isEmpty })
        let issues = Set(articles.map(\.issue).filter { !$0.isEmpty })
        let years = Set(articles.map(\.year).filter { !$0.isEmpty })
        if volumes.count > 1 { notes.append("卷号不一致：\(volumes.sorted().joined(separator: " / "))") }
        if issues.count > 1 { notes.append("期号不一致：\(issues.sorted().joined(separator: " / "))") }
        if years.count > 1 { notes.append("年份不一致：\(years.sorted().joined(separator: " / "))") }
        for a in articles where a.pageMismatch {
            notes.append("\(a.folderName)：声明页数 \(a.pages)，实际 \(a.actualPDFPages ?? -1) 页")
        }
        return notes
    }
}

// MARK: - 功能本体（第 96 条：改调引擎 `Engine/infocheck.py`）

/// 「检查tex信息」—— 逻辑在 `Engine/slcn/infocheck.py`（其上游是原版 `outinfo.py`）。
///
/// 这一页只做三件事：选期目录、把引擎输出流到日志、把产物 `信息检查<时间戳>.json`
/// 读回来喂给表格。**不在 Swift 里解析 tex** —— 判据与报告只有一个来源。
///
/// 这个功能**没有任何交互问答**：信息本来就写在各篇 `.tex` 模板里，本功能只是抄出来核对。
/// 原版的三处坑（整行取作者名会粘中文注释、标题固定取两行会写成 `None`、索引越界整份报告失败）
/// 已由引擎侧修掉，说明见 `slcn/infocheck.py` 开头。
enum InfoCheckFeature {

    /// 拼命令行参数。**纯函数**（`tools/infocheck_test` 直接断言它）。
    static func arguments(folderPath: String) -> [String] {
        ["--folder", folderPath]
    }

    /// 找 `infocheck.py`（引擎目录由 `EngineLocator` 统一解析）。
    static func locateTool(convertTool: String? = EngineLocator.convertTool()) -> String? {
        EngineLocator.tool(named: "infocheck.py", convertTool: convertTool)
    }

    /// 取目录下**最新**的 `信息检查*.json`，且必须是 `since` 之后写过的
    /// —— 否则一次失败的运行会把上一轮的旧报告读回来当结果。
    static func newestReportJSON(in folder: String, since: Date) -> String? {
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: folder) else { return nil }
        let fresh = names
            .filter { $0.hasPrefix("信息检查") && $0.hasSuffix(".json") }
            .compactMap { name -> (String, Date)? in
                let full = PathKit.join(folder, name)
                guard let attrs = try? fm.attributesOfItem(atPath: full),
                      let mtime = attrs[.modificationDate] as? Date, mtime >= since else { return nil }
                return (full, mtime)
            }
        return fresh.max(by: { $0.1 < $1.1 })?.0
    }

    /// 读产物 `信息检查*.json`。
    static func decodeReport(at jsonPath: String) -> InfoReport? {
        guard let data = FileManager.default.contents(atPath: jsonPath) else { return nil }
        return try? JSONDecoder().decode(InfoReport.self, from: data)
    }

    /// 跑一遍检查，返回报告（给界面的表格与提示）。失败返回 nil。
    @discardableResult
    static func run(folderPath rawPath: String, log: Logger) -> InfoReport? {
        let path = PathKit.normalize(rawPath)
        log("运行检查文件的函数\n")
        log("当前目录为：\(path)\n")

        guard PathKit.isDirectory(path) else {
            log("路径不是文件夹：\(path)\n")
            log("请确认目录格式为例如[03 Ming_Xiao]，内部文件为[Ming_Xiao.tex]的格式")
            return nil
        }
        guard let tool = locateTool() else {
            log("未找到检查引擎 Engine/infocheck.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/infocheck.py；")
            log("② 从 app 所在目录逐级向上找 Engine/infocheck.py\n")
            return nil
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return nil
        }

        let started = Date()
        let status = Shell.run(
            py,
            [tool] + arguments(folderPath: path),
            workingDirectory: toolDir,
            environment: EngineLocator.environment(toolDir: toolDir),
            log: log
        )
        log("\n")
        guard status == 0 else {
            log("❌ 检查失败（退出码 \(status)）。请把上面的报错发给技术人员。\n")
            return nil
        }

        guard let jsonPath = newestReportJSON(in: path, since: started),
              let report = decodeReport(at: jsonPath) else {
            log("⚠️ 报告已生成，但读不回结构化数据（表格留空）。"
                + "报告文件仍在目录里，可直接打开查看。\n")
            return nil
        }
        return report
    }
}
