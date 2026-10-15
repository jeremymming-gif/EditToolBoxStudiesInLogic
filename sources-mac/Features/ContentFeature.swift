import Foundation

/// 从检查报告里解析出的一篇文章
struct TocEntry {
    var folderName: String        // 例如 "03 Liang_Zhang"，用于判断是否栏目文章
    var page: String
    var chineseTitle: String
    var englishTitle: String
    var chineseAuthors: [String]
    var englishAuthors: [String]
    /// 标题在报告里是字面量 "None"（原版 extract_title 返回 None 时会这样写）
    var titleParseFailed: Bool = false
}

/// 「生成目录」—— 对应原程序的 charti_build_content.py，但做了三处结构性修正。
///
/// 与原版的差异：
///   1. **模板改为继承上一期的成品 .tex**（方案 A），不再使用写死的 2022 年旧模板；
///      卷号/期号/年份/总期号/出版日期自动填。
///   2. **修掉副标题的排版 bug**：原版把副标题排在页码行之后，会多出一行且错位；
///      现在按成品的写法，把副标题放在同一单元格内用 `\\` 换行。
///   3. 两字姓名不再插入 `\hspace{1em}`；最后一篇之后不再多一个 `\vdistance`。
enum ContentFeature {

    // MARK: - 姓名处理

    /// 对应 clean_name(name)：只保留 CJK 统一表意文字（U+4E00–U+9FFF）
    static func cleanName(_ name: String) -> String {
        var result = String.UnicodeScalarView()
        for scalar in name.unicodeScalars where scalar.value >= 0x4E00 && scalar.value <= 0x9FFF {
            result.append(scalar)
        }
        return String(result)
    }

    // MARK: - 工具

    /// 对应 Python 的 open(..., 'r').readlines()：通用换行归一后按 \n 切分并保留换行符
    static func pythonReadlines(_ content: String) -> [String] {
        let normalized = content
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        var lines: [String] = []
        var current = ""
        for ch in normalized {
            current.append(ch)
            if ch == "\n" { lines.append(current); current = "" }
        }
        if !current.isEmpty { lines.append(current) }
        return lines
    }

    private static func strip(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// 对应 Python 的 line[a:]
    private static func dropPrefix(_ s: String, _ count: Int) -> String {
        String(s.dropFirst(count))
    }

    /// 对应 extract_title(text)
    static func extractTitle(_ text: String) -> String? {
        var flag = 0
        let chars = Array(text)
        for (i, char) in chars.enumerated() {
            if char == "{" {
                if i == 0 { continue }
                flag += 1
            } else if char == "}" {
                flag -= 1
            }
            if flag == -1 {
                let end = max(1, min(i, chars.count))
                return String(chars[1..<end]).replacingOccurrences(of: "\n", with: "")
            }
        }
        if let range = text.range(of: "\\{([^{}]+)\\}", options: .regularExpression) {
            return String(String(text[range]).dropFirst().dropLast())
        }
        return nil
    }

    /// 对应 get_line_num(lines, keyword)
    static func getLineNum(_ lines: [String], _ keyword: String) -> String {
        for line in lines where line.contains(keyword) {
            return String(line.filter { $0.isNumber })
        }
        return ""
    }

    /// 从检查报告里解析出本期的卷号/期号/年份。
    ///
    /// 注意：报告里这三项写在**同一行**上：
    ///     `卷号：19  期号：5  年份：2026`
    /// 所以不能直接用 getLineNum（它会把整行的数字连成一串），必须按字段拆。
    static func parseIssueInfo(_ lines: [String]) -> (volume: String, issue: String, year: String) {
        for line in lines where line.contains("卷号：") {
            var volume = "", issue = "", year = ""
            for field in line.split(whereSeparator: { $0.isWhitespace }) {
                let f = String(field)
                if f.hasPrefix("卷号：") {
                    volume = String(f.dropFirst(3)).filter { $0.isNumber }
                } else if f.hasPrefix("期号：") {
                    issue = String(f.dropFirst(3)).filter { $0.isNumber }
                } else if f.hasPrefix("年份：") {
                    year = String(f.dropFirst(3)).filter { $0.isNumber }
                }
            }
            return (volume, issue, year)
        }
        return ("", "", "")
    }

    // MARK: - 切分报告

    /// 按「起始页码」行把报告切成一篇篇。
    ///
    /// 每篇报告的块结构是：
    ///     文件夹名
    ///     卷号：… 期号：… 年份：…
    ///     起始页码：… 终止页码：… 页数：…
    ///     …
    /// 所以文件夹名在「起始页码」行往前数第 2 行。
    ///
    /// 注：原版在这里用 end_idx 而不是 start_idx 收尾，会重复带上上一篇的一行；
    /// 这里改为按块边界精确切分，行为更清晰。
    static func splitArticles(_ lines: [String]) -> [(folderName: String, block: [String])] {
        var starts: [Int] = []
        for (i, line) in lines.enumerated() where line.contains("起始页码") {
            starts.append(i)
        }

        var result: [(String, [String])] = []
        for (index, start) in starts.enumerated() {
            let blockStart = max(0, start - 2)
            let blockEnd = (index + 1 < starts.count)
                ? max(blockStart, starts[index + 1] - 2)
                : lines.count
            guard blockStart < blockEnd else { continue }
            let folderName = start - 2 >= 0 ? strip(lines[start - 2]) : ""
            result.append((folderName, Array(lines[blockStart..<blockEnd])))
        }
        return result
    }

    /// 从一篇报告块里解析出条目
    static func parseEntry(folderName: String, block: [String]) -> TocEntry {
        var page = ""
        var chineseTitle: String? = nil
        var englishTitle: String? = nil
        var titleFailed = false
        var chineseAuthors: [String] = []
        var englishAuthors: [String] = []

        for line in block {
            if line.contains("起始页码：") {
                let firstField = line.split(whereSeparator: { $0.isWhitespace }).first.map(String.init) ?? ""
                if let n = Int(strip(dropPrefix(firstField, 5))) { page = String(n) }

            } else if line.contains("中文标题:") {
                let raw = strip(line.components(separatedBy: "中文标题:").last ?? "")
                if raw == "None" { titleFailed = true } else { chineseTitle = raw }

            } else if line.contains("英文标题") {
                let raw = strip(line.components(separatedBy: "英文标题:").last ?? "")
                if raw == "None" { titleFailed = true } else { englishTitle = raw }

            } else if line.contains("第一作者英文:") {
                englishAuthors.append(strip(dropPrefix(line, 7)))
            } else if line.contains("第一作者中文:") {
                chineseAuthors.append(cleanName(strip(dropPrefix(line, 7))))
            } else if line.contains("第二作者英文:") {
                englishAuthors.append(strip(dropPrefix(line, 7)))
            } else if line.contains("第二作者中文:") {
                chineseAuthors.append(cleanName(strip(dropPrefix(line, 7))))
            } else if line.contains("第三作者英文:") {
                englishAuthors.append(strip(dropPrefix(line, 7)))
            } else if line.contains("第三作者中文:") {
                chineseAuthors.append(cleanName(strip(dropPrefix(line, 7))))
            }
        }

        /// 标题清洗：去掉残留的 LaTeX 命令。
        /// ★ 原版只去掉 `\\`、`\Large`、`\large`，像 `\zihao{4}` 这种会留在标题里，
        ///   成品里就出现过 `\large \zihao{4}Reasoning Disposition...` 这样的情况。
        func cleanTitle(_ raw: String?, keepBraces: Bool) -> String {
            guard var t = raw else { return "" }
            for token in ["\\\\", "\\Large", "\\large"] {
                t = t.replacingOccurrences(of: token, with: "")
            }
            t = t.replacingOccurrences(of: #"\\[a-zA-Z]+\s*\{[^}]*\}"#, with: "",
                                       options: .regularExpression)
            if !keepBraces {
                t = t.replacingOccurrences(of: "{", with: "").replacingOccurrences(of: "}", with: "")
            }
            return strip(t)
        }

        return TocEntry(folderName: folderName,
                        page: page,
                        chineseTitle: cleanTitle(chineseTitle, keepBraces: false),
                        englishTitle: cleanTitle(englishTitle, keepBraces: true),
                        chineseAuthors: chineseAuthors.filter { !$0.isEmpty },
                        englishAuthors: englishAuthors.filter { !$0.isEmpty },
                        titleParseFailed: titleFailed)
    }

    // MARK: - 标题断行

    /// 估算显示宽度：汉字与全角标点算 1，ASCII 字母数字算 0.5，空格算 0.25
    static func displayWidth(_ s: String) -> Double {
        var width = 0.0
        for scalar in s.unicodeScalars {
            switch scalar.value {
            case 0x4E00...0x9FFF, 0x3000...0x303F, 0xFF00...0xFFEF: width += 1.0
            case 0x20: width += 0.25
            default: width += 0.5
            }
        }
        return width
    }

    /// 把过长的标题断成两行。
    ///
    /// 目录第一列是内容自适应的 `l` 列，只要有一篇标题太长，整张表就会被撑宽，
    /// 页码列被推出版心 —— 看起来就是右侧溢出。所以需要主动断行
    /// （同事在成品里也是这么手工处理的）。
    ///
    /// 断点选择：中文优先在标点处断，否则在不超过上限的位置尽量靠右；
    /// 英文则在不超过上限的最后一个空格处断。两侧各至少留 3 个字，避免孤字行。
    static func wrapTitle(_ title: String, limit: Double, isEnglish: Bool) -> (String, String)? {
        guard limit > 0, displayWidth(title) > limit else { return nil }
        let chars = Array(title)
        guard chars.count >= 8 else { return nil }

        let minSide = 3
        var bestIndex: Int? = nil

        if isEnglish {
            for i in stride(from: chars.count - minSide, through: minSide, by: -1) where chars[i] == " " {
                if displayWidth(String(chars[0..<i])) <= limit { bestIndex = i; break }
            }
        } else {
            let punctuation: Set<Character> = ["：", "，", "、", "；", "。", "！", "？"]
            for i in stride(from: chars.count - minSide, through: minSide, by: -1) {
                guard punctuation.contains(chars[i - 1]) else { continue }
                // 标点太靠前会造成"第一行很短、第二行很长"的难看版式，这种情况不用它
                guard Double(i) >= Double(chars.count) * 0.4 else { continue }
                if displayWidth(String(chars[0..<i])) <= limit { bestIndex = i; break }
            }
            if bestIndex == nil {
                // 退而求其次：在不超过上限的前提下尽量靠右
                for i in stride(from: chars.count - minSide, through: minSide, by: -1) {
                    if displayWidth(String(chars[0..<i])) <= limit { bestIndex = i; break }
                }
            }
            if let index = bestIndex {
                // 两边太不均衡就改用居中断：20/3 这种「孤字行」在目录里很难看
                let firstRatio = Double(index) / Double(chars.count)
                if firstRatio < 0.35 || firstRatio > 0.65 {
                    let middle = chars.count / 2
                    if middle >= minSide, chars.count - middle >= minSide { bestIndex = middle }
                }
            }
        }

        guard let index = bestIndex, index >= minSide, chars.count - index >= minSide else { return nil }
        let head = strip(String(chars[0..<index]))
        let tail = strip(String(chars[index..<chars.count]))
        guard !head.isEmpty, !tail.isEmpty else { return nil }
        return (head, tail)
    }

    // MARK: - 生成条目

    /// 中文目录的一条
    static func chineseEntry(_ e: TocEntry, inColumn: Bool, wrapLimit: Double) -> String {
        let prefix = inColumn ? "~~~~ " : ""
        var titleLines = [e.chineseTitle]

        if e.chineseTitle.contains("——") {
            let parts = e.chineseTitle.components(separatedBy: "——")
            titleLines = [strip(parts[0]),
                          "——" + strip(parts.dropFirst().joined(separator: "——"))]
        } else if let (head, tail) = wrapTitle(e.chineseTitle,
                                               // 上限里要扣掉 `~~~~ ` 缩进前缀的宽度，
                                               // 否则量的是标题、渲染出来却多了前缀，实际会超
                                               limit: wrapLimit - displayWidth(prefix),
                                               isEnglish: false) {
            // 没有副标题但太长：主动断成两行，避免把表格撑宽
            titleLines = [head, tail]
        }

        let authors = "&\\textit{\\large \(prefix)\(e.chineseAuthors.joined(separator: "，"))}"

        if titleLines.count == 2 {
            // 第二行在同一个单元格内换行 —— 这是成品的写法
            return "{\\large \(prefix)\(titleLines[0])}\n"
                 + "\\\\{\\large \(prefix)\(titleLines[1])}\n"
                 + "\(authors)\n& \(e.page)\\\\"
        }
        return "{\\large \(prefix)\(titleLines[0])}\n\(authors)\n& \(e.page)\\\\"
    }

    /// 英文目录的一条
    static func englishEntry(_ e: TocEntry, inColumn: Bool, wrapLimit: Double) -> String {
        // 成品里只有**第一位**作者带 ` ~~~~ ` 前缀，其余作者不带
        let authors = e.englishAuthors.enumerated()
            .map { index, name in "{\(index == 0 && inColumn ? " ~~~~ " : "")\(name)}" }
            .joined(separator: ", ")
        let prefix = inColumn ? "~~~~ " : ""

        // 英文标题过长时在空格处断行（英文是独立的上限，见 TocSettings 里的说明）
        // 同样要扣掉 `~~~~ ` 前缀的宽度
        if let (head, tail) = wrapTitle(e.englishTitle,
                                        limit: wrapLimit - displayWidth(prefix),
                                        isEnglish: true) {
            return "\\large \(prefix)\(head)\\\\\n\\large \(prefix)\(tail)\\\\\n"
                 + "\\textit{\(authors)}\n& \(e.page)\\\\"
        }
        return "\\large \(prefix)\(e.englishTitle)\\\\\n\\textit{\(authors)}\n& \(e.page)\\\\"
    }

    // MARK: - 生成文件

    @discardableResult
    static func generateTeX(reportPath: String,
                            flag: String,
                            settings: TocSettings,
                            log: Logger) -> String? {
        let isChinese = (flag == "ch")
        let label = isChinese ? "中文" : "英文"
        let baseName = isChinese ? "Chinese Content" : "contents"
        let dateStr = PathKit.timestamp("yyyy-MM-dd_HH-mm-ss")

        let reportDir = (PathKit.normalize(reportPath) as NSString).deletingLastPathComponent
        log("开始生成\(label)目录")

        do {
            let content = try PathKit.readText(reportPath)
            let lines = pythonReadlines(content)
            let articles = splitArticles(lines)

            // ---- 本期版面信息 ----
            let info = parseIssueInfo(lines)
            let volume = info.volume
            let issue = info.issue
            let year = info.year
            let totalIssue = TocTemplate.totalIssueNumber(of: reportDir) ?? ""
            // 双月刊：第 N 期 → 2N 月出版（2、4、6、8、10、12 月）
            let publishMonth = issue.isEmpty ? "" : String((Int(issue) ?? 0) * 2)

            // ---- 模板（方案 A：继承成品）----
            // 优先级：本期 000 目录里已有的成品 → 上一期 → 内置模板。
            // 本期已有成品时优先用它，是因为同事可能已经在本期调过版式，
            // 重新生成时应当继承那一次调整，而不是退回上一期的版式。
            let templateFolder = settings.templateFolder.isEmpty
                ? TocTemplate.previousIssueFolder(of: reportDir)
                : PathKit.normalize(settings.templateFolder)

            var parts: TocTemplateParts
            if let own = TocTemplate.load(from: reportDir, fileName: "\(baseName).tex") {
                parts = own
                log("模板来源：本期 000 Table of Contents 里已有的 \(baseName).tex\n")
            } else if let folder = templateFolder,
                      let loaded = TocTemplate.load(from: folder, fileName: "\(baseName).tex") {
                parts = loaded
                log("模板来源：上一期 \(loaded.source) 的 \(baseName).tex\n")
            } else {
                parts = TocTemplate.builtin(fileName: baseName)
                log("未找到可继承的模板，改用内置模板（卷期号等仍需手工核对）\n")
            }

            // ---- 头部：填入本期卷期信息，并统一使用 Windows 字体 ----
            let head = TocTemplate.ensureWindowsFontset(
                TocTemplate.patchHeader(parts.head,
                                        volume: volume.isEmpty ? "?" : volume,
                                        issue: issue.isEmpty ? "?" : issue,
                                        year: year.isEmpty ? "?" : year,
                                        totalIssue: totalIssue.isEmpty ? "?" : totalIssue,
                                        publishMonth: publishMonth.isEmpty ? "?" : publishMonth,
                                        isChinese: isChinese))

            // ---- 栏目块 ----
            let columnBlock = TocTemplate.makeColumnBlock(settings,
                                                          shape: parts.columnBlock,
                                                          isChinese: isChinese)
            if settings.isColumnEnabled {
                let title = isChinese ? settings.chineseColumnTitle : settings.englishColumnTitle
                log("本期栏目：\(title)（\(settings.columnArticles.count) 篇）\n")
            }

            // ---- 正文条目 ----
            var entries: [String] = []
            for (folderName, block) in articles {
                let entry = parseEntry(folderName: folderName, block: block)
                if entry.titleParseFailed {
                    log("⚠️ \(folderName)：标题在检查报告里是「None」，"
                        + "请回「检查tex信息」核对该篇 tex 的标题填写。\n")
                }
                let inColumn = settings.contains(folderName)
                entries.append(isChinese
                               ? chineseEntry(entry, inColumn: inColumn, wrapLimit: settings.titleWrapLimit)
                               : englishEntry(entry, inColumn: inColumn, wrapLimit: settings.titleWrapLimitEN))
            }
            let body = entries.joined(separator: "\n\n\\vdistance\n\n")

            // ---- 拼装 ----
            var result = head
            if columnBlock.isEmpty {
                result += "\n\n%中间主体\n\n"
            } else {
                result += "\n" + columnBlock.joined(separator: "\n") + "\n\n\n%中间主体\n\n"
            }
            result += body + "\n\n" + parts.tail

            let outPath = PathKit.join(reportDir, "\(baseName)_\(dateStr).tex")
            try result.write(toFile: outPath, atomically: true, encoding: .utf8)

            log("\(label)目录TeX文件已生成\n")
            return outPath
        } catch {
            log("\(error.localizedDescription)\n")
            log("\(label)目录TeX文件生成失败\n")
            return nil
        }
    }

    /// 供界面使用：从检查报告里列出所有文章（名称 + 中文标题），用于栏目勾选
    static func listArticles(reportPath: String) -> [(name: String, title: String)] {
        guard let content = try? PathKit.readText(PathKit.normalize(reportPath)) else { return [] }
        let lines = pythonReadlines(content)
        return splitArticles(lines).map { folder, block in
            let entry = parseEntry(folderName: folder, block: block)
            return (folder, entry.chineseTitle)
        }
    }

    /// 供界面使用：推断本期的模板来源说明（本期已有 → 上一期 → 内置）
    static func templateSourceNote(reportPath: String, settings: TocSettings) -> String {
        let dir = (PathKit.normalize(reportPath) as NSString).deletingLastPathComponent
        if TocTemplate.load(from: dir, fileName: "Chinese Content.tex") != nil {
            return "本期 000 Table of Contents 里已有成品，将继承它的版式"
        }
        let folder = settings.templateFolder.isEmpty
            ? TocTemplate.previousIssueFolder(of: dir)
            : PathKit.normalize(settings.templateFolder)
        if let folder, TocTemplate.load(from: folder, fileName: "Chinese Content.tex") != nil {
            let name = (folder as NSString).lastPathComponent
            return "将继承上一期 \(name) 的版式"
        }
        return "找不到可继承的成品，将使用内置模板（卷期号等需手工核对）"
    }

    /// 供界面调用：**先英文后中文**（已核对原版 main.py 的 run_tab5，顺序就是 en 再 ch）
    static func run(txtPath rawPath: String, settings: TocSettings, log: Logger) -> [String] {
        log("运行生成目录的函数\n")
        let path = PathKit.normalize(rawPath)
        var outputs: [String] = []
        if let en = generateTeX(reportPath: path, flag: "en", settings: settings, log: log) {
            outputs.append(en)
        }
        if let ch = generateTeX(reportPath: path, flag: "ch", settings: settings, log: log) {
            outputs.append(ch)
        }
        return outputs
    }
}
