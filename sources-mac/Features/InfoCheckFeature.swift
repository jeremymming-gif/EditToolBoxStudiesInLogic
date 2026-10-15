import Foundation
import PDFKit

// MARK: - 数据结构（用于界面表格与 JSON 输出）

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

    /// 声明的页数与 PDF 实际页数是否不符（用于界面标红）
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

struct InfoReport: Codable {
    var generatedAt: String
    var issueFolder: String
    var articles: [ArticleInfo]

    var txtPath: String { PathKit.join(issueFolder, "信息检查\(generatedAt).txt") }
    var jsonPath: String { PathKit.join(issueFolder, "信息检查\(generatedAt).json") }

    /// 所有文章里出现过的不一致项，供界面提示
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

// MARK: - 功能本体（对应原 outinfo.py）

/// 「检查tex信息」—— 对应原程序的 outinfo.py。
///
/// 注意：这个功能**没有任何交互问答**。文章的各项信息（卷号、期号、年份、
/// 起止页码、标题、作者等）本来就写在 `.tex` 模板里，本功能把它们读出来汇总成
/// 一份检查报告，并与 PDF 的实际页数对比。
///
/// 输出两份文件（★ 本 App 的改进）：
///   1. `信息检查<时间戳>.txt`  —— 与原版逐字节一致（含 CRLF 换行），便于比对
///   2. `信息检查<时间戳>.json` —— 结构化数据，供「生成目录」和界面表格使用
enum InfoCheckFeature {

    // MARK: 原版工具函数

    /// 对应 no_chinese(s)：不含中文返回 true
    static func noChinese(_ s: String) -> Bool {
        s.range(of: "[\u{4E00}-\u{9FA5}]", options: .regularExpression) == nil
    }

    /// 从 tex 的一行里取出「真正的值」：
    ///   1. 去掉 LaTeX 注释 —— 第一个未转义的 `%` 及其后面的内容全部丢弃
    ///   2. 取最后一个 `{...}` 花括号组的内容
    ///
    /// 原版直接拿**整行**当作者名，于是：
    ///   · 报告里出现「\renewcommand{\mysecondauthor}{\kaishu 刘叶涛}  % 若需要，请输入第二作者姓名…」
    ///   · 生成目录时那串中文注释残留下来，变成「刘叶涛若需要请输入第二作者姓名并取消该行注释」
    ///   · 更严重的是：由于注释本身是中文，`no_chinese` 判定为「含中文」，
    ///     英文作者名也被写进了「第二作者中文」字段，英文目录里该作者直接丢失
    ///
    /// 这里改为只取花括号里的值，上述三个问题一并解决。
    static func extractLaTeXValue(_ line: String) -> String {
        // ---- 第 1 步：去掉注释 ----
        var code = line
        let chars = Array(line)
        var i = 0
        while i < chars.count {
            if chars[i] == "\\" {      // \% 是转义的百分号，跳过
                i += 2
                continue
            }
            if chars[i] == "%" {
                code = String(chars[0..<i])
                break
            }
            i += 1
        }

        // ---- 第 2 步：取最后一个花括号组 ----
        if let open = code.lastIndex(of: "{") {
            let after = code.index(after: open)
            if let close = code[after...].lastIndex(of: "}") {
                return String(code[after..<close]).trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }

        return code.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// 对应 Python 的 str.splitlines()：
    /// 按 12 种行界切分，且**结尾不产生多余空串**。
    /// Swift 自带的 split 会吞掉空行，直接用会导致 lines[i+1] 全部错位。
    static func pythonSplitlines(_ s: String) -> [String] {
        var result: [String] = []
        var current = String.UnicodeScalarView()
        let scalars = Array(s.unicodeScalars)
        let boundaries: Set<Unicode.Scalar> = [
            "\u{0B}", "\u{0C}", "\u{1C}", "\u{1D}", "\u{1E}", "\u{85}", "\u{2028}", "\u{2029}"
        ]

        var i = 0
        while i < scalars.count {
            let c = scalars[i]
            if c == "\r" {
                result.append(String(current)); current = String.UnicodeScalarView()
                if i + 1 < scalars.count && scalars[i + 1] == "\n" { i += 2 } else { i += 1 }
                continue
            }
            if c == "\n" || boundaries.contains(c) {
                result.append(String(current)); current = String.UnicodeScalarView()
                i += 1
                continue
            }
            current.append(c)
            i += 1
        }
        if !current.isEmpty { result.append(String(current)) }
        return result
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
                // text[1:i] —— 不含 i 处的 '}'，并删除全部换行
                let end = max(1, min(i, chars.count))
                return String(chars[1..<end]).replacingOccurrences(of: "\n", with: "")
            }
        }
        // 回退到正则 '{([^{}]+)}'
        if let range = text.range(of: "\\{([^{}]+)\\}", options: .regularExpression) {
            let matched = String(text[range])
            let inner = matched.dropFirst().dropLast()
            return String(inner)
        }
        return nil
    }

    /// 对应 get_line_num(lines, keyword)：
    /// 返回第一条包含关键词的行里**所有数字字符按原顺序拼接**的结果。
    static func getLineNum(_ lines: [String], _ keyword: String) -> String {
        for line in lines where line.contains(keyword) {
            return String(line.filter { $0.isNumber })
        }
        return ""
    }

    // MARK: 主流程

    private static func safeLine(_ lines: [String], _ index: Int, folderName: String, log: Logger) -> String {
        guard index >= 0 && index < lines.count else {
            // 原版在这里会抛 IndexError 并导致整份报告生成失败；
            // 这里改为留空并提示，避免因为一个文件的小问题丢掉整份报告。
            log("⚠️ \(folderName).tex 第 \(index + 1) 行不存在（关键词出现在文件末尾），该字段留空。\n")
            return ""
        }
        return lines[index]
    }

    /// 从关键词行之后收集标题文本，**一直取到花括号配平为止**。
    ///
    /// 原版固定只取两行（`lines[i+1]` 与 `lines[i+2]`）。
    /// 但有的文章把闭合花括号 `}` 单独放在第三行，例如：
    ///     \newcommand{\mytitleEN}                 % 请输入英文标题
    ///     {\large The Basic Method ...: \\
    ///     Review of Reichenbach's ... Induction
    ///     }
    /// 两行取不到 `}`，`extract_title` 花括号永远配不平 → 返回 None
    /// → 报告里写成字面量 "None" → 生成的目录里标题整行消失。
    /// 改成按配平收集后，两行、三行、更多行都能正确处理。
    private static func collectBracedLines(_ lines: [String], startingAfter index: Int,
                                           maxLines: Int = 12) -> String {
        var depth = 0
        var started = false
        var collected: [String] = []
        let end = min(index + 1 + maxLines, lines.count)

        var i = index + 1
        while i < end {
            let line = lines[i]
            collected.append(line)
            for ch in line {
                if ch == "{" { depth += 1; started = true }
                else if ch == "}" { depth -= 1 }
            }
            if started && depth <= 0 { break }
            i += 1
        }
        return collected.joined(separator: "\n")
    }

    static func processFolders(path: String, outputDir: String, log: Logger) -> InfoReport {
        var output = ""
        var articles: [ArticleInfo] = []

        let entries = (try? FileManager.default.contentsOfDirectory(atPath: path)) ?? []
        let folders = entries
            .filter { name in
                guard let first = name.first, first.isNumber else { return false }
                return PathKit.isDirectory(PathKit.join(path, name))
            }
            .sorted()

        for folder in folders {
            let folderPath = PathKit.join(path, folder)
            let folderName = folder
                .split(whereSeparator: { $0.isWhitespace })
                .last
                .map(String.init) ?? folder
            if folderName == "Contents" { continue }

            output += "\(folderName)\n"
            var article = ArticleInfo(folder: folder, folderName: folderName)

            // ---------- 读 .tex ----------
            let texFile = PathKit.join(folderPath, "\(folderName).tex")
            if PathKit.exists(texFile) {
                article.hasTeX = true
                let content = (try? PathKit.readText(texFile)) ?? ""
                let lines = pythonSplitlines(content)

                let volNum = getLineNum(lines, "输入卷号")
                if !volNum.isEmpty { output += "卷号：\(volNum)  "; article.volume = volNum }

                let issueNum = getLineNum(lines, "输入当年期号")
                if !issueNum.isEmpty { output += "期号：\(issueNum)  "; article.issue = issueNum }

                let yearNum = getLineNum(lines, "输入出版年份")
                if !yearNum.isEmpty { output += "年份：\(yearNum)\n"; article.year = yearNum }

                let startPage = getLineNum(lines, "输入起始页码")
                if !startPage.isEmpty { output += "起始页码：\(startPage)  "; article.startPage = startPage }

                let endPage = getLineNum(lines, "输入终止页码")
                if !endPage.isEmpty { output += "终止页码：\(endPage)  "; article.endPage = endPage }

                let totalPages = getLineNum(lines, "输入页数")
                if !totalPages.isEmpty { output += "页数：\(totalPages)\n"; article.pages = totalPages }

                // 逐行扫描：命中关键词就追加一条
                for (i, line) in lines.enumerated() {

                    if line.contains("请输入论文题目") || line.contains("Insert Chinese title") {
                        let tmp = collectBracedLines(lines, startingAfter: i)
                        let t = extractTitle(tmp)
                        if t == nil {
                            log("⚠️ \(folderName)：中文标题解析失败，报告里会写成 None。\n")
                        }
                        output += "中文标题: \(t ?? "None")\n"
                        article.chineseTitle = t
                        continue
                    }

                    if line.contains("请输入英文标题") || line.contains("Insert the title of your paper.") {
                        let tmp = collectBracedLines(lines, startingAfter: i)
                        let t = extractTitle(tmp)
                        if t == nil {
                            log("⚠️ \(folderName)：英文标题解析失败，报告里会写成 None。\n")
                        }
                        output += "英文标题: \(t ?? "None")\n"
                        article.englishTitle = t
                        continue
                    }

                    if line.contains("第一作者姓名") || line.contains("Insert the first author name.") {
                        let name = extractLaTeXValue(safeLine(lines, i + 1, folderName: folderName, log: log))
                        if noChinese(name) {
                            output += "第一作者英文: \(name)\n"
                            article.firstAuthorEnglish = name
                        } else {
                            output += "第一作者中文: \(name)\n"
                            article.firstAuthorChinese = name
                        }
                        continue
                    }

                    // 注意：原版第二/三作者取的是**当前行本身**（关键词所在行），
                    // 且要求行首不是注释符 %。
                    if (line.contains("第二作者姓名") || line.contains("Insert the second author if")),
                       line.first != "%" {
                        let name = extractLaTeXValue(line)
                        if noChinese(name) {
                            output += "第二作者英文: \(name)\n"
                            article.secondAuthorEnglish = name
                        } else {
                            output += "第二作者中文: \(name)\n"
                            article.secondAuthorChinese = name
                        }
                        continue
                    }

                    if (line.contains("第三作者姓名") || line.contains("Insert the third author if")),
                       line.first != "%" {
                        let name = extractLaTeXValue(line)
                        if noChinese(name) {
                            output += "第三作者英文: \(name)\n"
                            article.thirdAuthorEnglish = name
                        } else {
                            output += "第三作者中文: \(name)\n"
                            article.thirdAuthorChinese = name
                        }
                        continue
                    }

                    if line.contains("documentclass") {
                        // 原版取逗号分隔后的第 2 段作为字号，例如
                        // \documentclass[b5paper,10.5t,...] -> 10.5t
                        let parts = line.components(separatedBy: ",")
                        if parts.count > 1 {
                            let zihao = parts[1]
                            output += "字号 \(zihao)\n"
                            article.fontSize = zihao
                        }
                        continue
                    }
                }
            }

            // ---------- 读 .sty（行距） ----------
            let styFiles = ((try? FileManager.default.contentsOfDirectory(atPath: folderPath)) ?? [])
                .filter { $0.hasSuffix(".sty") }
                .sorted()
            if let sty = styFiles.first {
                let content = (try? PathKit.readText(PathKit.join(folderPath, sty))) ?? ""
                let baseline = getLineNum(pythonSplitlines(content), "全文行间距")
                if !baseline.isEmpty {
                    output += "全文行距: \(baseline)\n"
                    article.lineSpacing = baseline
                }
            }

            // ---------- PDF 实际页数 ----------
            let pdfFile = PathKit.join(folderPath, "\(folderName).pdf")
            if PathKit.exists(pdfFile) {
                article.hasPDF = true
                if let doc = PDFDocument(url: URL(fileURLWithPath: pdfFile)) {
                    article.actualPDFPages = doc.pageCount
                    output += "--------------------------实际pdf页数: \(doc.pageCount)\n"
                } else {
                    log("⚠️ 无法打开 \(folderName).pdf，跳过页数检查。\n")
                }
            }

            output += "\n"
            articles.append(article)
        }

        // ---------- 写出 ----------
        let stamp = PathKit.timestamp()
        let baseName = "信息检查\(stamp)"

        // 1) txt —— 与原版一致（原版在 Windows 的文本模式下写盘是 CRLF）
        let txtPath = PathKit.join(outputDir, "\(baseName).txt")
        let crlf = output.replacingOccurrences(of: "\n", with: "\r\n")
        try? crlf.write(toFile: txtPath, atomically: true, encoding: .utf8)

        // 2) json —— 本 App 新增
        let report = InfoReport(generatedAt: stamp, issueFolder: path, articles: articles)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        if let data = try? encoder.encode(report) {
            let jsonPath = PathKit.join(outputDir, "\(baseName).json")
            try? data.write(to: URL(fileURLWithPath: jsonPath))
        }

        return report
    }

    /// 对应原 outinfo.main(script_path, out_txt)
    @discardableResult
    static func run(folderPath rawPath: String, log: Logger) -> InfoReport? {
        log("运行检查文件的函数\n")
        let path = PathKit.normalize(rawPath)
        log("当前目录为：\(path)\n")
        do {
            guard PathKit.isDirectory(path) else {
                throw ToolboxError.notADirectory(path)
            }
            let report = processFolders(path: path, outputDir: path, log: log)
            log("检查文件生成成功，请查看。\n")
            return report
        } catch {
            log("\(error.localizedDescription)\n")
            log("请确认目录格式为例如[03 Ming_Xiao]，内部文件为[Ming_Xiao.tex]的格式")
            return nil
        }
    }
}
