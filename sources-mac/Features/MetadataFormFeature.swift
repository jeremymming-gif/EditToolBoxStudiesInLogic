import Foundation

/// 批63b：初排页「人工填写元数据」表单的**纯逻辑**（无 SwiftUI、无进程、无全局状态）。
///
/// 为什么单独成文件：表单要生成结构化 JSON 交给引擎 `--metadata-json`。
/// 这段逻辑必须在 `tools/ai_settings_test` 里可直接编译断言（表单模型→JSON、
/// 校验、临时文件命名/清理、内联字段优先级），而视图
/// `Views/MetadataFormSheet.swift` 只做绑定与展示、不写判据。
///
/// 与引擎的契约（批63b，engine-core 定稿）：
///
///     { "schema_version": 1,
///       "title_cn": "", "title_en": "",
///       "abstract_cn": "", "abstract_en": "",
///       "keywords_cn": "", "keywords_en": "",
///       "authors": [ { "name_cn": "", "name_en": "",
///                      "affiliations_cn": [""], "affiliations_en": [""],
///                      "email": "" } ] }
///
///   · **空串 / 空数组 = 未提供 = 不覆盖** → JSON 里直接省略该键（不写 ""）；
///   · authors 1–5；**中间空作者引擎会 exit 2** → 本表单在写 JSON 前就把空作者压缩掉
///     （压掉的位置记 warning，不静默），所以 GUI 永远不会生成带空档的 JSON；
///   · 多单位数组每项一行，由引擎用 `\\` 拼接（GUI 不预拼、不设分隔符）；
///   · `running_title_cn/en` 本表单 v1 不暴露（不写键 = 未提供）；
///   · 本表单只在**普通转换**下传（两段式协同引擎对 JSON 明确 exit 2；只扫描不产出交付物）。
struct MetadataForm: Equatable {

    // MARK: - 契约常量

    /// 模板作者槽位上限（myfirstauthor…myfifthauthor）。
    static let maxAuthors = 5
    /// 每位作者每种语言最多几行单位（版式：模板 37.5em minipage，行数多了会溢版）。
    static let maxAffiliationsPerLanguage = 3
    /// 与引擎对齐的 JSON schema 版本。
    static let schemaVersion = 1

    // MARK: - B-017：单一元数据入口路由（View 只按它分支，不写第二份判据）

    /// 初排页人工元数据入口的**唯一**路由结果。
    ///
    /// - `formOnly`：只显示「填写元数据…」表单。普通转换（`coop == false`）走这里；
    ///   「只扫描源稿」也按普通处理（引擎 `--scan` 在消费 overrides 之前就 `return 0`，
    ///   人工元数据对它没有意义，入口不因此消失——扫描只是当次动作）。
    /// - `inlineFallback`：表单不适用，显示旧「正文信息覆盖」7 内联字段兜底。
    ///   两段式协同走这里：引擎对 `--metadata-json` + 协同**明确 exit 2**（批63b v1 不支持）。
    enum MetadataEntryMode: Equatable {
        case formOnly
        case inlineFallback
    }

    /// 本次运行的入口路由；与 `ConvertFeature.arguments()` 的
    /// `let coop = o.scanOnly ? .off : o.coopPhase` 同口径（只扫描时协同被忽略）。
    static func entryMode(scanOnly: Bool, coop: Bool) -> MetadataEntryMode {
        (coop && !scanOnly) ? .inlineFallback : .formOnly
    }

    // MARK: - B-017：模板上限说明与达限提示（纯逻辑；界面与日志共用这一份文案）

    /// 常驻上限说明之一：作者上限 = 模板**硬槽位**（不是可调设置）。
    static let authorLimitSummary =
        "作者上限 \(maxAuthors) 位：模板只有 \\myfirstauthor…\\myfifthauthor 五个槽位，"
        + "不是可调设置。"
    /// 常驻上限说明之二：单位行数 = 版式容量。
    static let affiliationLimitSummary =
        "单位上限：每位作者每种语言最多 \(maxAffiliationsPerLanguage) 行"
        + "（模板版式容量，第 \(maxAffiliationsPerLanguage + 1) 行起会溢版）。"
    /// 入口旁/表单内的一行式常驻说明。
    static let limitSummary = authorLimitSummary + affiliationLimitSummary

    /// 作者槽位是否已满（`addAuthor` 会被拒；界面据此显示可操作提示）。
    var reachedAuthorLimit: Bool { authors.count >= Self.maxAuthors }

    /// 某位作者某种语言的单位行是否已满（`addAffiliation` 会被拒；界面据此显示提示）。
    func reachedAffiliationLimit(authorIndex: Int,
                                 language: AffiliationLanguage) -> Bool {
        guard authorIndex >= 0, authorIndex < authors.count else { return false }
        switch language {
        case .cn: return authors[authorIndex].affiliationsCN.count
            >= Self.maxAffiliationsPerLanguage
        case .en: return authors[authorIndex].affiliationsEN.count
            >= Self.maxAffiliationsPerLanguage
        }
    }

    /// 作者达限时的可操作提示（`reachedAuthorLimit == true` 时显示）。
    static func authorLimitHint() -> String {
        "作者已达模板上限 \(maxAuthors) 位：第 \(maxAuthors + 1) 位没有槽位。"
            + "请先删掉空作者行；确需更多署名请合并同单位作者，"
            + "或把补充信息移到正文致谢/前言（模板按连续作者数排版）。"
    }

    /// 单位达限时的可操作提示（`reachedAffiliationLimit == true` 时显示）。
    static func affiliationLimitHint(authorIndex: Int,
                                     language: AffiliationLanguage) -> String {
        let lang = language == .cn ? "中文" : "英文"
        return "第 \(authorIndex + 1) 位作者的\(lang)单位已到 "
            + "\(maxAffiliationsPerLanguage) 行上限：第 "
            + "\(maxAffiliationsPerLanguage + 1) 行起会溢版。"
            + "请删掉空行，或把多个单位合并写到一行（也可移入脚注）。"
    }

    /// 全部达限提示（空数组 = 未达上限）。给测试/日志用；界面按上面两个判据就地取。
    ///
    /// 为什么单独于 `warnings()`：上限不是「内容有问题」（不阻断、不出橙色告警），
    /// 而是**模板/版式硬约束**；这里给的是「接下来能怎么做」，不是错误清单。
    /// 计数包含空行（加行按钮按槽位拦住），所以文案同时提示「先删空行」。
    func limitHints() -> [String] {
        var out: [String] = []
        if reachedAuthorLimit {
            out.append(Self.authorLimitHint())
        }
        for i in authors.indices {
            for language in AffiliationLanguage.allCases
            where reachedAffiliationLimit(authorIndex: i, language: language) {
                out.append(Self.affiliationLimitHint(authorIndex: i, language: language))
            }
        }
        return out
    }

    // MARK: - 子模型

    struct Affiliation: Identifiable, Equatable {
        let id: UUID
        var text: String

        init(id: UUID = UUID(), text: String = "") {
            self.id = id
            self.text = text
        }

        var trimmedText: String { Self.trim(text) }
        var isEmpty: Bool { trimmedText.isEmpty }

        static func trim(_ s: String) -> String {
            s.trimmingCharacters(in: .whitespacesAndNewlines)
        }
    }

    struct Author: Identifiable, Equatable {
        let id: UUID
        var nameCN: String
        var nameEN: String
        var affiliationsCN: [Affiliation]
        var affiliationsEN: [Affiliation]
        var email: String

        init(id: UUID = UUID(),
             nameCN: String = "", nameEN: String = "",
             affiliationsCN: [Affiliation] = [], affiliationsEN: [Affiliation] = [],
             email: String = "") {
            self.id = id
            self.nameCN = nameCN
            self.nameEN = nameEN
            self.affiliationsCN = affiliationsCN
            self.affiliationsEN = affiliationsEN
            self.email = email
        }

        var trimmedNameCN: String { Affiliation.trim(nameCN) }
        var trimmedNameEN: String { Affiliation.trim(nameEN) }
        var trimmedEmail: String { Affiliation.trim(email) }
        /// 非空的单位文本（CN / EN），顺序即写入 JSON 的顺序。
        var nonEmptyAffiliationsCN: [String] {
            affiliationsCN.map(\.trimmedText).filter { !$0.isEmpty }
        }
        var nonEmptyAffiliationsEN: [String] {
            affiliationsEN.map(\.trimmedText).filter { !$0.isEmpty }
        }
        /// 这一位作者有没有填任何东西（姓名 / 单位 / 邮箱）。
        var hasContent: Bool {
            !trimmedNameCN.isEmpty || !trimmedNameEN.isEmpty || !trimmedEmail.isEmpty
                || !nonEmptyAffiliationsCN.isEmpty || !nonEmptyAffiliationsEN.isEmpty
        }
    }

    enum AffiliationLanguage: String, CaseIterable {
        case cn
        case en
    }

    /// 旧界面「正文信息覆盖」的 7 个内联字段（批63b 起表单优先；GUI 侧据此逐字段清零，
    /// 避免同一字段有两个来源。判据在这里是纯函数，headless 可断言）。
    enum InlineOverrideField: String, CaseIterable {
        case titleCN, authorCN, affiliationCN, email, abstractCN, titleEN, abstractEN
    }

    // MARK: - 表单数据

    var titleCN = ""
    var titleEN = ""
    var abstractCN = ""
    var abstractEN = ""
    var keywordsCN = ""
    var keywordsEN = ""
    /// 第一位固定是「第一作者」（界面不允许删）；追加作者最多到 `maxAuthors`。
    var authors: [Author] = [Author()]

    // MARK: - 空值判定

    /// 表单里有没有任何非空内容——决定「是否写临时 JSON / 是否传 --metadata-json」。
    /// 全空 = 与没打开过表单完全一样（默认 argv 逐字节不变）。
    var hasAnyValue: Bool {
        for s in [titleCN, titleEN, abstractCN, abstractEN, keywordsCN, keywordsEN]
        where !Affiliation.trim(s).isEmpty { return true }
        return authors.contains { $0.hasContent }
    }

    /// 真正会写进 JSON 的作者（空作者被压缩掉；引擎对「中间空作者」会 exit 2，
    /// 所以这里绝不把空档留给引擎）。
    var keptAuthors: [Author] { authors.filter { $0.hasContent } }

    // MARK: - 编辑（纯函数；UI 按钮与测试共用同一判据）

    /// 追加一位空作者；已达 `maxAuthors` 返回 false。
    @discardableResult
    mutating func addAuthor() -> Bool {
        guard authors.count < Self.maxAuthors else { return false }
        authors.append(Author())
        return true
    }

    /// 删除追加作者；第一位（第一作者）永远不能删——「至少 1 位作者」由结构保证。
    @discardableResult
    mutating func removeAuthor(at index: Int) -> Bool {
        guard index > 0, index < authors.count else { return false }
        authors.remove(at: index)
        return true
    }

    /// 给某位作者加一行单位；每种语言最多 `maxAffiliationsPerLanguage` 行。
    @discardableResult
    mutating func addAffiliation(authorID: UUID, language: AffiliationLanguage) -> Bool {
        guard let i = authors.firstIndex(where: { $0.id == authorID }) else { return false }
        switch language {
        case .cn:
            guard authors[i].affiliationsCN.count < Self.maxAffiliationsPerLanguage else { return false }
            authors[i].affiliationsCN.append(Affiliation())
        case .en:
            guard authors[i].affiliationsEN.count < Self.maxAffiliationsPerLanguage else { return false }
            authors[i].affiliationsEN.append(Affiliation())
        }
        return true
    }

    @discardableResult
    mutating func removeAffiliation(authorID: UUID,
                                    language: AffiliationLanguage,
                                    affiliationID: UUID) -> Bool {
        guard let i = authors.firstIndex(where: { $0.id == authorID }) else { return false }
        switch language {
        case .cn:
            guard let j = authors[i].affiliationsCN.firstIndex(where: { $0.id == affiliationID }) else {
                return false
            }
            authors[i].affiliationsCN.remove(at: j)
        case .en:
            guard let j = authors[i].affiliationsEN.firstIndex(where: { $0.id == affiliationID }) else {
                return false
            }
            authors[i].affiliationsEN.remove(at: j)
        }
        return true
    }

    /// 表单填了这个字段吗（= 该字段的旧内联值应由 GUI 清零，以表单为准）。
    func coversInlineOverride(_ field: InlineOverrideField) -> Bool {
        switch field {
        case .titleCN: return !Affiliation.trim(titleCN).isEmpty
        case .titleEN: return !Affiliation.trim(titleEN).isEmpty
        case .abstractCN: return !Affiliation.trim(abstractCN).isEmpty
        case .abstractEN: return !Affiliation.trim(abstractEN).isEmpty
        case .authorCN:
            return !(keptAuthors.first?.trimmedNameCN.isEmpty ?? true)
        case .affiliationCN:
            return !(keptAuthors.first?.nonEmptyAffiliationsCN.isEmpty ?? true)
        case .email:
            return !(keptAuthors.first?.trimmedEmail.isEmpty ?? true)
        }
    }

    // MARK: - 校验（只提示，不阻断）

    /// 引擎侧接受的最低限度邮箱格式（只提示，永不阻断保存/运行）。
    static func looksLikeEmail(_ raw: String) -> Bool {
        let t = Affiliation.trim(raw)
        guard !t.isEmpty, !t.contains(" ") else { return false }
        let parts = t.split(separator: "@", omittingEmptySubsequences: false)
        guard parts.count == 2, !parts[0].isEmpty else { return false }
        let domain = parts[1]
        return domain.contains(".")
            && !domain.hasPrefix(".")
            && !domain.hasSuffix(".")
            && !domain.hasPrefix("-")
    }

    /// 全部「只提示不阻断」的问题；显示在 sheet footer 与运行日志里。
    func warnings() -> [String] {
        guard hasAnyValue else { return [] }
        var out: [String] = []
        let kept = keptAuthors
        if kept.isEmpty {
            out.append("未填写作者：作者信息仍由引擎自动解析（表单只覆盖已填字段）")
        } else if kept.count < authors.count {
            for (i, a) in authors.enumerated() where !a.hasContent {
                out.append("第 \(i + 1) 位作者为空，已忽略（不会写入产物）")
            }
        }
        for (i, a) in kept.enumerated() {
            if !a.trimmedEmail.isEmpty && !Self.looksLikeEmail(a.trimmedEmail) {
                out.append("第 \(i + 1) 位作者邮箱格式可疑：\(a.trimmedEmail)（只提示，不阻断）")
            }
            if a.trimmedNameCN.isEmpty && a.trimmedNameEN.isEmpty
                && (!a.trimmedEmail.isEmpty
                    || !a.nonEmptyAffiliationsCN.isEmpty
                    || !a.nonEmptyAffiliationsEN.isEmpty) {
                out.append("第 \(i + 1) 位作者没有姓名（只有单位/邮箱）：建议补姓名，"
                           + "否则该槽位会保留占位")
            }
        }
        return out
    }

    // MARK: - 统计（日志「将写入 N 位作者 / M 项字段」）

    var authorCountForWrite: Int { keptAuthors.count }

    /// M = 非空标量字段数（≤6）+ 作者姓名/邮箱/单位行的非空计数。
    var filledFieldCount: Int {
        var n = 0
        for s in [titleCN, titleEN, abstractCN, abstractEN, keywordsCN, keywordsEN]
        where !Affiliation.trim(s).isEmpty { n += 1 }
        for a in keptAuthors {
            if !a.trimmedNameCN.isEmpty { n += 1 }
            if !a.trimmedNameEN.isEmpty { n += 1 }
            if !a.trimmedEmail.isEmpty { n += 1 }
            n += a.nonEmptyAffiliationsCN.count
            n += a.nonEmptyAffiliationsEN.count
        }
        return n
    }

    /// 运行日志里那一行（纯函数，测试直接断言精确数字）。
    func summaryLine() -> String {
        "将写入 \(authorCountForWrite) 位作者 / \(filledFieldCount) 项字段"
    }

    // MARK: - 写 JSON

    /// 结构化对象；空值（空串/空数组/全空作者）一律省略键 = 「未提供 = 不覆盖」。
    /// 表单全空 → nil（不建临时文件、不传参数）。
    func jsonObject() -> [String: Any]? {
        guard hasAnyValue else { return nil }
        var obj: [String: Any] = ["schema_version": Self.schemaVersion]
        func put(_ key: String, _ value: String) {
            let t = Affiliation.trim(value)
            if !t.isEmpty { obj[key] = t }
        }
        put("title_cn", titleCN)
        put("title_en", titleEN)
        put("abstract_cn", abstractCN)
        put("abstract_en", abstractEN)
        put("keywords_cn", keywordsCN)
        put("keywords_en", keywordsEN)

        let kept = keptAuthors
        if !kept.isEmpty {
            obj["authors"] = kept.map { a -> [String: Any] in
                var d: [String: Any] = [:]
                if !a.trimmedNameCN.isEmpty { d["name_cn"] = a.trimmedNameCN }
                if !a.trimmedNameEN.isEmpty { d["name_en"] = a.trimmedNameEN }
                if !a.nonEmptyAffiliationsCN.isEmpty { d["affiliations_cn"] = a.nonEmptyAffiliationsCN }
                if !a.nonEmptyAffiliationsEN.isEmpty { d["affiliations_en"] = a.nonEmptyAffiliationsEN }
                if !a.trimmedEmail.isEmpty { d["email"] = a.trimmedEmail }
                return d
            }
        }
        return obj
    }

    /// 单行 UTF-8 JSON（`.sortedKeys` → 同一表单两次输出逐字节相同，便于断言/排障）。
    func jsonString() -> String? {
        guard let obj = jsonObject(),
              let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
              let s = String(data: data, encoding: .utf8) else { return nil }
        return s
    }

    enum MetadataFormError: Error, Equatable {
        case nothingToWrite
    }

    /// 原子写入临时 JSON；表单全空时抛 `nothingToWrite`（调用方应先看 `hasAnyValue`）。
    func writeJSON(to path: String) throws {
        guard let s = jsonString() else { throw MetadataFormError.nothingToWrite }
        try s.write(toFile: path, atomically: true, encoding: .utf8)
    }

    // MARK: - 临时文件（本次运行有效；进程被杀最多留一个 /tmp 文件，无害）

    /// `/tmp/slcn_metadata_<源稿名>_<时间戳>_<pid>.json`（与批63 B2 临时 .docx 同一命名风格）。
    static func tempFilePath(directory: String = NSTemporaryDirectory(),
                             stem: String,
                             timestamp: String = MetadataForm.timestamp(),
                             pid: Int32 = ProcessInfo.processInfo.processIdentifier) -> String {
        let name = "slcn_metadata_\(sanitizedStem(stem))_\(timestamp)_\(pid).json"
        return (directory as NSString).appendingPathComponent(name)
    }

    /// 源稿文件名 → 安全的临时文件名片段（保留中文；去掉路径分隔符、过长的截断）。
    static func sanitizedStem(_ raw: String) -> String {
        var base = (Affiliation.trim(raw) as NSString).lastPathComponent
        base = (base as NSString).deletingPathExtension
        var out = String(base.prefix(80))
        for bad in ["/", ":", "\\", "\0"] {
            out = out.replacingOccurrences(of: bad, with: "_")
        }
        out = out.trimmingCharacters(in: .whitespacesAndNewlines)
        return out.isEmpty ? "metadata" : out
    }

    static func timestamp(_ date: Date = Date()) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyyMMdd-HHmmss"
        return f.string(from: date)
    }

    /// 清理临时 JSON；重复调用/路径为空都不抛、不崩。
    static func removeTemporaryFile(_ path: String) {
        let p = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !p.isEmpty else { return }
        try? FileManager.default.removeItem(atPath: p)
    }

    /// 这次运行该不该下传 `--metadata-json`：
    /// 只扫描源稿不产出交付物 → 不传；两段式协同 → 引擎会 exit 2，**不传**。
    static func shouldAttach(scanOnly: Bool, coop: Bool) -> Bool {
        !scanOnly && !coop
    }
}
