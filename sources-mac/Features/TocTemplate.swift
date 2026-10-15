import Foundation

/// 目录模板的三个组成部分
struct TocTemplateParts {
    /// 从文件开头到 tabularx 的列定义行为止（含）
    var head: String
    /// 上一期的栏目块（专栏/专辑那几行），可能为空
    var columnBlock: [String]
    /// 从 `\end{tabularx}` 开始到文件末尾
    var tail: String
    /// 来源说明，写进日志
    var source: String
}

/// 目录模板：**以上一期的成品 .tex 为模板**（方案 A）。
///
/// 为什么这么做：
///   原版程序把头部（卷期号、日期、版式参数）写死在代码里，值停留在 2022 年。
///   而同事每期都是在上一期成品的基础上手工调整。
///   所以最省事的做法是直接继承上一期的头部与尾部，只替换本期的卷期信息和中间条目 ——
///   这样版式调整会自动延续，不用每期重调。
enum TocTemplate {

    static let beginTabularx = "\\begin{tabularx}"
    static let endTabularx = "\\end{tabularx}"

    // MARK: - 找上一期

    /// 由期目录名推上一期目录，例如 2026年第5期(90) → 2026年第4期(89)
    static func previousIssueFolder(of issueFolder: String) -> String? {
        let folder = PathKit.normalize(issueFolder)
        let parent = (folder as NSString).deletingLastPathComponent
        let name = (folder as NSString).lastPathComponent

        guard let range = name.range(of: #"^(20\d\d)年第(\d+)期"#, options: .regularExpression) else {
            return nil
        }
        let digits = String(name[range])
            .components(separatedBy: CharacterSet.decimalDigits.inverted)
            .filter { !$0.isEmpty }
        guard digits.count >= 2, let year = Int(digits[0]), let number = Int(digits[1]) else { return nil }

        var targetYear = year
        var targetNumber = number - 1
        if targetNumber < 1 {
            targetYear = year - 1
            targetNumber = 6      // 双月刊，每年 6 期
        }

        let prefix = "\(targetYear)年第\(targetNumber)期"
        let entries = (try? FileManager.default.contentsOfDirectory(atPath: parent)) ?? []
        guard let match = entries.filter({ $0.hasPrefix(prefix) }).sorted().first else { return nil }
        return PathKit.join(parent, match)
    }

    // MARK: - 读取模板

    /// 从某个期目录的 `000 Table of Contents/<fileName>` 读模板
    static func load(from folder: String, fileName: String) -> TocTemplateParts? {
        let path = PathKit.join(PathKit.join(folder, "000 Table of Contents"), fileName)
        guard PathKit.exists(path), let content = try? PathKit.readText(path) else { return nil }
        let normalized = content
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        return split(normalized, source: (folder as NSString).lastPathComponent)
    }

    /// 找不到上一期时的兜底：用程序内置的原始模板
    static func builtin(fileName: String) -> TocTemplateParts {
        let isChinese = fileName.hasPrefix("Chinese Content")
        let headRaw = isChinese ? ContentTemplates.chContentHead : ContentTemplates.enContentHead
        let tailRaw = isChinese ? ContentTemplates.chContentEnd : ContentTemplates.enContentEnd
        let tail = tailRaw.hasPrefix("\n") ? String(tailRaw.dropFirst()) : tailRaw

        var parts = split(headRaw + "\n\n%中间主体\n\n" + tail, source: "内置模板")
        parts.tail = tail
        return parts
    }

    /// 把一份 .tex 切成 head / 栏目块 / tail
    private static func split(_ text: String, source: String) -> TocTemplateParts {
        let lines = text.components(separatedBy: "\n")

        guard let beginIdx = lines.firstIndex(where: { $0.contains(beginTabularx) }) else {
            return TocTemplateParts(head: text, columnBlock: [], tail: "", source: source)
        }

        // 列定义行：
        //   · 英文模板把它写在同一行：\begin{tabularx}{\textwidth}{l@{\hspace{1em}}r}
        //   · 中文模板写在下一行，而且历史文件里常有一行被注释掉的旧列定义要跳过
        //
        // 判断依据是 `}{` 出现的**次数**：
        //   `\begin{tabularx}{\textwidth}`              只有 1 处（tabularx 与 \textwidth 之间）
        //   `\begin{tabularx}{\textwidth}{l@...}`       有 2 处（多出列定义那一段）
        var specIdx = beginIdx
        let braceJoints = lines[beginIdx].components(separatedBy: "}{").count - 1
        if braceJoints < 2 {
            var i = beginIdx + 1
            while i < lines.count {
                let trimmed = lines[i].trimmingCharacters(in: .whitespaces)
                if trimmed.isEmpty || trimmed.hasPrefix("%") { i += 1; continue }
                specIdx = i
                break
            }
        }

        guard let endIdx = lines.firstIndex(where: { $0.contains(endTabularx) }) else {
            return TocTemplateParts(head: lines[0...specIdx].joined(separator: "\n"),
                                    columnBlock: [], tail: "", source: source)
        }

        // 栏目块：在列定义行与 \end{tabularx} 之间挑出栏目相关的行
        var columnBlock: [String] = []
        var spacingLine: String? = nil
        for line in lines[(specIdx + 1)..<endIdx] {
            let plain = line.replacingOccurrences(of: #"^\s*%\s?"#, with: "",
                                                  options: .regularExpression)
            if plain.contains("专栏") || plain.contains("专辑") || plain.contains("Column:") {
                columnBlock.append(plain)
            } else if plain.contains("主持人") {
                columnBlock.append(plain)
            } else if spacingLine == nil, !plain.contains("vdistance"),
                      plain.range(of: #"^\s*\\\\\[\s*[\d.]+\s*(ex|mm|pt)\s*\]"#,
                                  options: .regularExpression) != nil {
                spacingLine = plain
            }
        }
        if !columnBlock.isEmpty, let spacing = spacingLine {
            columnBlock.append(spacing)
        }

        let head = lines[0...specIdx].joined(separator: "\n")
        let tail = lines[endIdx...].joined(separator: "\n")
        return TocTemplateParts(head: head, columnBlock: columnBlock, tail: tail, source: source)
    }

    // MARK: - 改写头部

    /// 把头部里的卷期号与出版日期换成本期的值。
    /// 匹配的是**形状**而不是具体数字，所以无论上一期填的是什么都能替换。
    static func patchHeader(_ head: String,
                            volume: String, issue: String, year: String,
                            totalIssue: String, publishMonth: String,
                            isChinese: Bool) -> String {
        var result = head

        if isChinese {
            // 第\ 19 卷，第\ 5 期，总第\ 90 期
            // 注意：正则替换模板里反斜杠要写成 \\\\（Swift）→ \\（ICU）→ 一个字面反斜杠
            result = result.replacingOccurrences(
                of: #"第\\\s*\d+\s*卷，第\\\s*\d+\s*期，总第\\\s*\d+\s*期"#,
                with: "第\\\\ \(volume) 卷，第\\\\ \(issue) 期，总第\\\\ \(totalIssue) 期",
                options: .regularExpression)
            // 2026 年第\ 5 期，10月\ 18 日出版
            result = result.replacingOccurrences(
                of: #"\d+\s*年第\\\s*\d+\s*期，\d+\s*月\\\s*\d+\s*日出版"#,
                with: "\(year) 年第\\\\ \(issue) 期，\(publishMonth)月\\\\ 18 日出版",
                options: .regularExpression)
        } else {
            // Volume 19, Number 5, October 2026
            let monthName = englishMonthName(Int(publishMonth) ?? 0)
            result = result.replacingOccurrences(
                of: #"Volume\s+\d+,\s*Number\s+\d+,\s*[A-Za-z]+\s+\d{4}"#,
                with: "Volume \(volume), Number \(issue), \(monthName) \(year)",
                options: .regularExpression)
        }

        return result
    }

    /// 保证 `\documentclass[...]{ctexart}` 里带 `fontset=windows`。
    ///
    /// 目的是让编译结果统一使用 Windows 字体（与期刊正文一致）。
    /// 已实测在本机能正常编译通过。如果模板里已经指定了 fontset，则原样保留。
    static func ensureWindowsFontset(_ head: String) -> String {
        guard let range = head.range(of: #"\\documentclass\[[^\]]*\]\{ctexart\}"#,
                                     options: .regularExpression) else {
            return head
        }
        var line = String(head[range])
        guard !line.contains("fontset=") else { return head }
        line = line.replacingOccurrences(of: "]{ctexart}", with: ",fontset=windows]{ctexart}")
        var result = head
        result.replaceSubrange(range, with: line)
        return result
    }

    /// 生成本期栏目块：沿用上一期的形态（保留其间距设置），只替换名称与主持人。
    static func makeColumnBlock(_ settings: TocSettings, shape: [String], isChinese: Bool) -> [String] {
        guard settings.isColumnEnabled else { return [] }

        let title = isChinese ? settings.chineseColumnTitle : settings.englishColumnTitle

        if !shape.isEmpty {
            var out: [String] = []
            for line in shape {
                var newLine = line
                if newLine.contains("主持人") {
                    newLine = newLine.replacingOccurrences(
                        of: #"主持人[：:][^}]*"#, with: "主持人：\(settings.columnHost)",
                        options: .regularExpression)
                } else if newLine.range(of: #"^\s*\\\\\[\s*[\d.]+\s*(ex|mm|pt)\s*\]"#,
                                        options: .regularExpression) == nil {
                    newLine = newLine.replacingOccurrences(
                        of: #"(专栏|专辑|Column)[：:][^}]*"#, with: title, options: .regularExpression)
                }
                out.append(newLine)
            }
            return out
        }

        // 上一期没有栏目块时用兜底形态
        if isChinese {
            return [
                "{\\large \\bf{\(title)}}",
                "&&\\textit{\\large 主持人：\(settings.columnHost)}",
                "\\\\[3.2 ex]",
            ]
        } else {
            return [
                "\\large  \\bf{\(title)} ",
                "\\\\[3mm]",
            ]
        }
    }

    private static func englishMonthName(_ month: Int) -> String {
        let names = ["", "January", "February", "March", "April", "May", "June",
                     "July", "August", "September", "October", "November", "December"]
        guard month >= 1, month <= 12 else { return "" }
        return names[month]
    }

    /// 从期目录名里取「总第 N 期」的 N，例如 2026年第5期(90) → 90
    static func totalIssueNumber(of issueFolder: String) -> String? {
        let name = (PathKit.normalize(issueFolder) as NSString).lastPathComponent
        guard let range = name.range(of: #"[（(](\d+)[）)]"#, options: .regularExpression) else { return nil }
        let digits = String(name[range]).filter { $0.isNumber }
        return digits.isEmpty ? nil : digits
    }
}
