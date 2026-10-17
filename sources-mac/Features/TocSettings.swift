import Foundation

/// 「生成目录」的本期设置，保存在期目录下的 `目录设置.json`。
///
/// 为什么把栏目设置放在「生成目录」而不是「检查tex信息」：
///   · 「检查tex信息」是**校对**工具（核对各篇 tex 里填的信息与 PDF 实际页数是否相符）；
///     给它加"是不是专栏文章"的勾选列，会让它的职责变得混乱。
///   · 栏目信息是**这一期怎么排目录**的决定，属于「生成目录」的输入。
///   · 而且栏目名、主持人本来也得在同一处填，集中在一起才顺手。
struct TocSettings: Codable {

    /// 栏目类型：""（本期无栏目）/ "专栏" / "专辑"
    /// （历史数据里两种都用过：2026年第4期用的是「专辑」）
    var columnKind: String = ""
    /// 栏目名称，例如「认知悖论研究」
    var columnName: String = ""
    /// 栏目英文名，例如「Research on Epistemic Paradoxes」；留空则沿用中文名
    var columnNameEN: String = ""
    /// 栏目主持人，例如「张建军」
    var columnHost: String = ""
    /// 属于栏目的文章，用文件夹名标识，例如 ["01 Jingru_Kou", "02 Zixin_Luo"]
    var columnArticles: [String] = []
    /// 手动指定模板来源期目录；留空则自动找上一期
    var templateFolder: String = ""
    /// 标题断行上限（按显示宽度算：1 = 一个汉字宽）。0 表示不断行。
    ///
    /// 为什么需要它：目录表格的第一列是 `l`（宽度由内容决定），
    /// 只要有一篇标题太长，整张表就会被撑宽，把右边的页码列推出版心 ——
    /// 看起来就是"右侧溢出"。同事是手工把长标题断成两行的。
    var titleWrapLimit: Double = 20
    /// 英文标题断行上限（同样按汉字宽算，1 ≈ 两个西文字母）。
    ///
    /// 为什么英文要单独一个值：两套模板几何完全不同 ——
    ///   中文是 3 列 `l r r` 带负间距、11pt；英文是 2 列 `l r`、10pt。
    /// 2026-10-17 第十四批按期 90 成品重新校准：35 时整表比成品宽 21.86pt（7.7mm）、
    /// 7 处断行一处都对不上；30 时 4/7 与成品一致（详见案例第 110 条）。
    var titleWrapLimitEN: Double = 30

    static let fileName = "目录设置.json"

    var isColumnEnabled: Bool {
        !columnKind.isEmpty && !columnName.isEmpty
    }

    /// 界面上是否展示栏目相关控件（名称、主持人、文章勾选框）。
    ///
    /// 注意不能用 isColumnEnabled —— 那要求名称非空，
    /// 会导致"选了「专栏」但还没填名称时输入框被隐藏"，从而永远填不上名称。
    var columnEditing: Bool { !columnKind.isEmpty }

    /// 判断某篇文章是否属于栏目。
    /// 报告里的文章名是 `folder_name`（如 "Jingru_Kou"），不带序号；
    /// 而设置里可能存的是带序号的文件夹名（如 "01 Jingru_Kou"），所以两边都兼容。
    func contains(_ reportArticleName: String) -> Bool {
        guard isColumnEnabled else { return false }
        let target = reportArticleName.trimmingCharacters(in: .whitespaces)
        guard !target.isEmpty else { return false }
        return columnArticles.contains { stored in
            let s = stored.trimmingCharacters(in: .whitespaces)
            if s == target { return true }
            let tail = s.split(whereSeparator: { $0.isWhitespace }).last.map(String.init) ?? s
            return tail == target
        }
    }

    /// 中文目录里的栏目行文字
    var chineseColumnTitle: String { "\(columnKind)：\(columnName)" }
    /// 英文目录里的栏目行
    var englishColumnTitle: String {
        let name = columnNameEN.isEmpty ? columnName : columnNameEN
        return "Column: \(name)"
    }

    // MARK: - 读写

    private enum CodingKeys: String, CodingKey {
        case columnKind, columnName, columnNameEN, columnHost
        case columnArticles, templateFolder, titleWrapLimit, titleWrapLimitEN
    }

    /// 自己实现解码：**每个字段都允许缺失**。
    ///
    /// 必须这样做 —— Swift 合成的 Codable 遇到"JSON 里没有这个键"会整体解码失败，
    /// 而属性默认值不会生效。之前加 `titleWrapLimit` 时就是这样：
    /// 打开旧的 目录设置.json 会整份读取失败，栏目名、主持人、勾选的文章全被清空。
    /// 以后再加字段也不会再有这个问题。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        columnKind     = try c.decodeIfPresent(String.self,   forKey: .columnKind)     ?? ""
        columnName     = try c.decodeIfPresent(String.self,   forKey: .columnName)     ?? ""
        columnNameEN   = try c.decodeIfPresent(String.self,   forKey: .columnNameEN)   ?? ""
        columnHost     = try c.decodeIfPresent(String.self,   forKey: .columnHost)     ?? ""
        columnArticles = try c.decodeIfPresent([String].self, forKey: .columnArticles) ?? []
        templateFolder = try c.decodeIfPresent(String.self,   forKey: .templateFolder) ?? ""
        titleWrapLimit = try c.decodeIfPresent(Double.self,   forKey: .titleWrapLimit) ?? 20
        titleWrapLimitEN = try c.decodeIfPresent(Double.self, forKey: .titleWrapLimitEN) ?? 30
    }

    init() {}

    static func load(from folder: String) -> TocSettings {
        let path = PathKit.join(folder, fileName)
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let settings = try? JSONDecoder().decode(TocSettings.self, from: data) else {
            return TocSettings()
        }
        return settings
    }

    func save(to folder: String) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(self)
        try data.write(to: URL(fileURLWithPath: PathKit.join(folder, TocSettings.fileName)))
    }
}
