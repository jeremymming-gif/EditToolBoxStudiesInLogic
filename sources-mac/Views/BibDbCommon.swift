import SwiftUI
import AppKit

/// 「引用库」5 页共用的小部件与格式化工具（第 132 条）。
///
/// 为什么单独一个文件：5 页的标题/说明/状态行若各写一套，措辞与样式会很快分叉；
/// 但也都太小，不值得各建一个类型。集中在这里，改一处五页一起变。
enum BibDbUI {

    /// 文件大小（B/KB/MB），给「待裁决」页的清单文件显示。
    /// 用 `ByteCountFormatter` 而不是手写除法：单位口径与访达一致，编辑不用换算。
    static func size(_ bytes: Int) -> String {
        let formatter = ByteCountFormatter()
        formatter.countStyle = .file
        formatter.allowedUnits = [.useKB, .useMB, .useBytes]
        return formatter.string(fromByteCount: Int64(bytes))
    }

    /// 时间（y-MM-dd HH:mm），`nil` → 「—」
    static func dateText(_ date: Date?) -> String {
        guard let date else { return "—" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter.string(from: date)
    }

    /// 秒数 → 「0.3 s」（查库/预览的用时，让编辑知道没卡住）
    static func seconds(_ value: Double) -> String {
        value < 0.05 ? "很快" : String(format: "%.1f s", value)
    }
}

/// 页面标题 + 一行说明（5 页统一：标题 `title3`、说明 `callout`、可选中复制）。
struct BibDbPageHeader: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.title3).bold()
            Text(subtitle.plainMarkdown)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
    }
}

/// 一行「标签：值」——库概况/设置页大量重复的排版。
struct BibDbInfoRow: View {
    let label: String
    let value: String
    /// 值为路径时用等宽 + 可选中（方便复制去排查）
    var monospaced = false
    var color: Color = .primary
    /// 允许值换行（路径很长时不要撑破版面）
    var wraps = false

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(label)
                .foregroundStyle(.secondary)
                .frame(width: 108, alignment: .trailing)
            Text(value)
                .font(monospaced ? .system(.callout, design: .monospaced) : .callout)
                .foregroundStyle(color)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .lineLimit(wraps ? nil : 1)
                .truncationMode(.middle)
            Spacer(minLength: 0)
        }
    }
}

/// 状态条（预览/导入/生成清单的结果）：成功绿、失败红、未跑时不显示。
struct BibDbStatusLine: View {
    enum Kind { case ok, warning, error, idle }
    let kind: Kind
    let text: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: symbol)
                .foregroundStyle(color)
            Text(text)
                .font(.callout)
                .foregroundStyle(color)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            Spacer(minLength: 0)
        }
    }

    private var symbol: String {
        switch kind {
        case .ok: return "checkmark.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .error: return "xmark.octagon.fill"
        case .idle: return "info.circle"
        }
    }

    private var color: Color {
        switch kind {
        case .ok: return .green
        case .warning: return .orange
        case .error: return .red
        case .idle: return .secondary
        }
    }
}

/// 待处理页 4 处**自定义 disclosure 行**（B-202；t6 返工，脱离 `DisclosureGroup`）。
///
/// 为什么不用 `DisclosureGroup`：它的 label 只是普通内容（只有内置箭头可点），把 label 包成
/// `Button` 后 SwiftUI 仍会把子元素折叠进 `AXDisclosureTriangle`，AX 树里没有 AXButton、
/// label 文本也消失 —— 走查 `click --role AXButton --name` 无法命中，证据链不成立。
/// 这里自己拼：**整行 Button（箭头+label+空白）** + 受控 `isExpanded` 展开内容。
///
/// `title` 是给无障碍/走查用的 AX 名字（如「③ 已处理」「缺字段」）；展开内容在 Button 之外，
/// 其文本仍会出现在 AX 树里（点击后可用 assert-text 验证）。
struct BibDbRowDisclosure<Label: View, Content: View>: View {
    let title: String
    let isExpanded: Bool
    let action: () -> Void
    let label: Label
    let content: Content

    init(title: String, isExpanded: Bool, action: @escaping () -> Void,
         @ViewBuilder label: () -> Label,
         @ViewBuilder content: () -> Content) {
        self.title = title
        self.isExpanded = isExpanded
        self.action = action
        self.label = label()
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { action() }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                    label
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(Text(title))
            .accessibilityHint("展开或收起")
            if isExpanded {
                content
            }
        }
    }
}

/// 「复制到剪贴板」：成功/失败都返回一句要写进运行日志的话（不静默）。
enum BibDbClipboard {
    static func copy(_ text: String, what: String) -> String {
        guard !text.isEmpty else { return "（没有可复制的\(what)。）\n" }
        let board = NSPasteboard.general
        board.clearContents()
        let ok = board.setString(text, forType: .string)
        return ok ? "已复制\(what)到剪贴板。\n" : "复制失败：剪贴板不可写。\n"
    }
}

/// 在访达里打开（文件或文件夹）。返回一句日志话术（失败不静默）。
@discardableResult
func bibDbOpenInFinder(_ path: String) -> String {
    let fm = FileManager.default
    guard fm.fileExists(atPath: path) else {
        return "（找不到：\(path)）\n"
    }
    var isDirectory: ObjCBool = false
    _ = fm.fileExists(atPath: path, isDirectory: &isDirectory)
    let url = URL(fileURLWithPath: path)
    if isDirectory.boolValue {
        NSWorkspace.shared.open(url)                 // 文件夹：直接打开
    } else {
        NSWorkspace.shared.activateFileViewerSelecting([url])  // 文件：在访达里选中
    }
    return "已在访达中打开/显示：\(path)\n"
}
