import SwiftUI
import AppKit

/// 「路径输入框 + 文件拖拽」的 AppKit 输入框（案例 126）。
///
/// 为什么不用 SwiftUI `TextField`：本机实测（macOS 26/27）**落在 SwiftUI 文本框上的文件
/// 拖拽会被 AppKit 的 NSTextField 吞掉** —— 行级 `dropDestination` 会收到 `isTargeted=true`，
/// 但松手后 `perform` 永远不触发（日志能证明：`rowTargeted=true` 却没有任何 `rowDrop`）。
/// 把输入框换成自带 `NSDraggingDestination` 的 NSTextField 子类后，拖到框里也能落。
final class DroppableNSTextField: NSTextField {
    var onDropURL: ((URL) -> Bool)?
    private var hoverLayer: CALayer?

    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.fileURL, .URL])
        bezelStyle = .roundedBezel
        isBezeled = true
        isBordered = true
        drawsBackground = true
        font = .systemFont(ofSize: NSFont.systemFontSize)
        lineBreakMode = .byTruncatingMiddle
        cell?.wraps = false
        cell?.isScrollable = true
        setContentHuggingPriority(.defaultLow, for: .horizontal)
        setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    }
    required init?(coder: NSCoder) { fatalError() }

    private func setHover(_ on: Bool) {
        if on && hoverLayer == nil {
            wantsLayer = true
            let l = CALayer()
            l.frame = bounds.insetBy(dx: -2, dy: -2)
            l.borderColor = NSColor.controlAccentColor.cgColor
            l.borderWidth = 2
            l.cornerRadius = 5
            layer?.addSublayer(l)
            hoverLayer = l
        } else if !on, let l = hoverLayer {
            l.removeFromSuperlayer()
            hoverLayer = nil
        }
    }
    override func layout() {
        super.layout()
        hoverLayer?.frame = bounds.insetBy(dx: -2, dy: -2)
    }
    override func draggingEntered(_ s: NSDraggingInfo) -> NSDragOperation {
        guard s.draggingPasteboard.canReadObject(
            forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) else { return [] }
        setHover(true)
        return .copy
    }
    override func draggingUpdated(_ s: NSDraggingInfo) -> NSDragOperation { .copy }
    override func draggingExited(_ s: NSDraggingInfo?) { setHover(false) }
    override func prepareForDragOperation(_ s: NSDraggingInfo) -> Bool { true }
    override func performDragOperation(_ s: NSDraggingInfo) -> Bool {
        setHover(false)
        guard let urls = s.draggingPasteboard.readObjects(
            forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL],
            let u = urls.first else { return false }
        return onDropURL?(u) ?? false
    }
}

/// 自带文件拖拽接收的路径输入框。`onDrop` 返回是否接受（类型判断由调用方做，保持与行级拖拽一致）。
struct DroppablePathField: NSViewRepresentable {
    @Binding var text: String
    var placeholder: String
    var disabled: Bool = false
    var onDrop: (URL) -> Bool

    func makeNSView(context: Context) -> DroppableNSTextField {
        let f = DroppableNSTextField(frame: .zero)
        f.delegate = context.coordinator
        f.placeholderString = placeholder
        f.stringValue = text
        f.onDropURL = onDrop
        f.isEnabled = !disabled
        return f
    }
    func updateNSView(_ f: DroppableNSTextField, context: Context) {
        context.coordinator.parent = self
        f.onDropURL = onDrop
        f.isEnabled = !disabled
        f.placeholderString = placeholder
        if !context.coordinator.isEditing, f.stringValue != text {
            f.stringValue = text
        }
    }
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func sizeThatFits(_ proposal: ProposedViewSize, nsView: DroppableNSTextField, context: Context) -> CGSize? {
        let h: CGFloat = 24
        let w = proposal.width ?? nsView.intrinsicContentSize.width
        return CGSize(width: w.isFinite ? w : 200, height: h)
    }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var parent: DroppablePathField
        var isEditing = false
        init(_ p: DroppablePathField) { parent = p }
        func controlTextDidBeginEditing(_ obj: Notification) { isEditing = true }
        func controlTextDidEndEditing(_ obj: Notification) { isEditing = false }
        func controlTextDidChange(_ obj: Notification) {
            guard let f = obj.object as? NSTextField else { return }
            parent.text = f.stringValue
        }
    }
}
