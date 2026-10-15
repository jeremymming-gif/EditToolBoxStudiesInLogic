// 诊断工具：打印一个 PDF 的页数与书签大纲结构
import Foundation
import PDFKit

let path = CommandLine.arguments[1]
guard let doc = PDFDocument(url: URL(fileURLWithPath: path)) else {
    print("无法打开: \(path)"); exit(1)
}
print("文件: \((path as NSString).lastPathComponent)")
print("总页数: \(doc.pageCount)")
print("书签数量: \(doc.outlineRoot?.numberOfChildren ?? 0)")
func walk(_ node: PDFOutline, _ depth: Int) {
    for i in 0..<node.numberOfChildren {
        guard let child = node.child(at: i) else { continue }
        let pageIdx = child.destination?.page.map { doc.index(for: $0) } ?? -1
        print(String(repeating: "  ", count: depth) + "• \(child.label ?? "(无标题)")  -> 第 \(pageIdx + 1) 页")
        walk(child, depth + 1)
    }
}
if let root = doc.outlineRoot { walk(root, 0) }
