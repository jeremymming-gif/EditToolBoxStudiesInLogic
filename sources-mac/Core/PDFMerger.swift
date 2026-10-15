import Foundation
import PDFKit

/// 复刻 PyPDF2 `PdfMerger` 的行为：按顺序拼接页面，并重建书签大纲。
///
/// 书签结构说明（与 Windows 原版输出一致）：
///   · 本程序添加的书签（cover / Chinese Content / contents / 各篇文章名）排在大纲最前面；
///   · 各篇 PDF 自带的小节书签被保留，原版里它们平铺在大纲顶层且排在上述书签之后。
///
/// 打开 `nestSourceOutlines` 后，每篇文章自带的小节书签会挂到该篇文章的书签之下，
/// 导航时更符合直觉（这是本 App 增加的选项，默认关闭以保持与原版一致）。
final class PDFMerger {

    struct OutlineNode {
        var label: String
        /// 合并后文档中的页码（0 起）；-1 表示无法确定
        var pageIndex: Int
        var children: [OutlineNode]
    }

    private struct TopItem {
        var label: String
        var pageIndex: Int
        var children: [OutlineNode]
    }

    private let document = PDFDocument()
    /// 本程序登记的书签，按登记顺序排列
    private var topItems: [TopItem] = []
    /// 各源 PDF 自带的大纲节点，按追加顺序排列
    private var sourceNodes: [OutlineNode] = []

    var nestSourceOutlines = false

    var pageCount: Int { document.pageCount }

    // MARK: - 追加页面

    /// 把一个源文档的全部页面插入到合并文档末尾。
    /// 先记下页数：PDFKit 的 insert 会把页对象从源文档中取走，
    /// 边插边读 source.pageCount 会错位。
    private func insert(_ source: PDFDocument) {
        let count = source.pageCount
        for index in 0..<count {
            guard let page = source.page(at: index) else { continue }
            document.insert(page, at: document.pageCount)
        }
    }

    /// 追加一个 PDF 的全部页面，返回其起始页码与它自带的大纲
    private func appendPages(_ path: String) throws -> (start: Int, outline: [OutlineNode]) {
        guard let source = PDFDocument(url: URL(fileURLWithPath: path)) else {
            throw ToolboxError.cannotOpenPDF(path)
        }
        let start = document.pageCount
        // 先收集大纲，再插入页面（页对象被取走后源文档就不完整了）
        let outline = PDFMerger.collectOutline(of: source, offset: start)
        insert(source)
        sourceNodes.append(contentsOf: outline)
        return (start, outline)
    }

    /// 追加并登记一个顶层书签（对应原版的 append + add_outline_item）
    func append(_ path: String, title: String) throws {
        let result = try appendPages(path)
        topItems.append(TopItem(label: title, pageIndex: result.start, children: result.outline))
    }

    /// 只追加页面、把书签登记交给 addTopLevelItem（原版处理扉页的方式）
    func appendWithoutTitle(_ path: String) throws {
        _ = try appendPages(path)
    }

    /// 追加页面并登记一个顶层书签，但**不导入该文件自带的大纲**。
    ///
    /// 用于编委信息页这类单页前置件：它内部往往留着制作时产生的陈旧书签
    /// （实测 `cneditors[260113]左移.pdf` 自带一个叫「Chinese Content」的书签，
    /// 与真正的目录页重名，导进来会造成两个同名书签指向不同页面）。
    func appendIgnoringOutline(_ path: String, title: String) throws {
        guard let source = PDFDocument(url: URL(fileURLWithPath: path)) else {
            throw ToolboxError.cannotOpenPDF(path)
        }
        let start = document.pageCount
        insert(source)
        // 注意：不收集大纲，也不写入 sourceNodes
        topItems.append(TopItem(label: title, pageIndex: start, children: []))
    }

    /// 在指定页码处登记一个顶层书签
    func addTopLevelItem(title: String, pageIndex: Int) {
        topItems.append(TopItem(label: title, pageIndex: pageIndex, children: []))
    }

    // MARK: - 大纲

    /// 把源文档的大纲读出来，并把页码平移到合并文档的坐标系
    private static func collectOutline(of document: PDFDocument, offset: Int) -> [OutlineNode] {
        guard let root = document.outlineRoot else { return [] }

        func convert(_ node: PDFOutline) -> [OutlineNode] {
            var result: [OutlineNode] = []
            for i in 0..<node.numberOfChildren {
                guard let child = node.child(at: i) else { continue }
                var pageIndex = -1
                if let page = child.destination?.page {
                    let index = document.index(for: page)
                    if index >= 0 { pageIndex = index + offset }
                }
                result.append(OutlineNode(label: child.label ?? "",
                                          pageIndex: pageIndex,
                                          children: convert(child)))
            }
            return result
        }

        return convert(root)
    }

    private func makeNode(_ label: String, _ pageIndex: Int) -> PDFOutline {
        let node = PDFOutline()
        node.label = label
        if pageIndex >= 0, let page = document.page(at: pageIndex) {
            // 目标点放在页面顶端，书签点击后跳到该页开头
            let top = page.bounds(for: .mediaBox).maxY
            node.destination = PDFDestination(page: page, at: CGPoint(x: 0, y: top))
        }
        return node
    }

    private func attach(_ nodes: [OutlineNode], to parent: PDFOutline) {
        for node in nodes {
            let child = makeNode(node.label, node.pageIndex)
            parent.insertChild(child, at: parent.numberOfChildren)
            attach(node.children, to: child)
        }
    }

    private func buildOutline() -> PDFOutline? {
        if topItems.isEmpty && sourceNodes.isEmpty { return nil }

        let root = PDFOutline()

        for item in topItems {
            let node = makeNode(item.label, item.pageIndex)
            root.insertChild(node, at: root.numberOfChildren)
            if nestSourceOutlines {
                attach(item.children, to: node)
            }
        }

        if !nestSourceOutlines {
            // 原版行为：各源文件自带的书签平铺在大纲顶层，排在本程序书签之后
            attach(sourceNodes, to: root)
        }

        return root
    }

    // MARK: - 写出

    func write(to path: String) throws {
        if let outline = buildOutline() {
            document.outlineRoot = outline
        }
        guard document.write(to: URL(fileURLWithPath: path)) else {
            throw ToolboxError.cannotWrite(path)
        }
    }
}
