import SwiftUI

/// 「责编」控件：选择 / 自定义名单 / 设为默认。
///
/// 批63b 发布修复 R2：从 `ConvertTab` 的大 ViewBuilder 上下文里抽出来（旧 Xcode 在
/// 大上下文里对带条件分支的 ViewBuilder 类型检查代价高）。行为 / 文案 / 布局与拆分前一致：
/// 在 `pagesAndEditorRow` 的 HStack 里，它展开成「责编 + Picker + [新责编姓名 + 添加] + 设为默认」。
struct EditorControlsRow: View {
    @ObservedObject var editors: EditorStore
    @Binding var selection: String

    var body: some View {
        Text("责编")
        editorPicker
        customEditorControls
        setDefaultEditorButton
    }

    private var editorPicker: some View {
        Picker("", selection: $selection) { editorPickerItems }
            .labelsHidden()
            .frame(width: 130)
    }

    @ViewBuilder private var editorPickerItems: some View {
        Text("未指定").tag("")
        ForEach(editors.allNames, id: \.self) { name in
            Text(editorOptionTitle(name)).tag(name)
        }
        Text("自定义…").tag("__custom__")
    }

    /// 默认责编在名单里加「（默认）」。抽成方法，让 ViewBuilder 里只剩 `Text(String)`。
    private func editorOptionTitle(_ name: String) -> String {
        editors.defaultName == name ? "\(name)（默认）" : name
    }

    @ViewBuilder private var customEditorControls: some View {
        if selection == "__custom__" {
            customEditorNameField
            addEditorButton
        }
    }

    private var customEditorNameField: some View {
        TextField("新责编姓名", text: $editors.newName)
            .textFieldStyle(.roundedBorder).frame(width: 90)
    }

    private var addEditorButton: some View {
        Button("添加") { addCustomEditor() }
    }

    private func addCustomEditor() {
        editors.addCustom(editors.newName)
        let name = editors.newName.trimmingCharacters(in: .whitespacesAndNewlines)
        if !name.isEmpty { selection = name }
        editors.newName = ""
    }

    private var setDefaultEditorButton: some View {
        Button(setDefaultEditorTitle()) { setDefaultEditor() }
            .disabled(selection.isEmpty || selection == "__custom__")
    }

    private func setDefaultEditorTitle() -> String {
        editors.defaultName == selection && !selection.isEmpty
            ? "已设为默认 ✓" : "设为默认"
    }

    private func setDefaultEditor() {
        editors.setDefault(selection)
    }
}

/// 一行「标签 + 路径输入 + 浏览按钮」。与 SimpleFeatureView 的选择方式保持一致（NSOpenPanel）。
struct LabeledPathRow: View {
    let title: String
    let placeholder: String
    @Binding var path: String
    let kind: PickKind
    /// 批63b：可选的扩展名白名单（如 `["tex", "docx"]`）。空数组 = 不限制（既有调用方零变化）。
    /// 只影响「浏览…」的 NSOpenPanel 过滤；拖拽类型由 `PathDrop` 只判文件/目录，
    /// 更细的通行/拦截由各页自己的纯函数判定（如 `ConvertFeature.metadataPathVerdict`）。
    var allowedExtensions: [String] = []
    /// 拖拽落地 / 被拒时写进运行日志（可为 nil）。不能静默——
    /// 编辑拖了没反应时，日志里要能看出是「类型不对」还是「压根没收到拖拽」。
    var logStore: LogStore? = nil

    var body: some View {
        let logger = logStore.map { Logger(store: $0) }
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.callout).bold()
            HStack(spacing: 8) {
                DroppablePathField(text: $path, placeholder: placeholder) { url in
                    PathDrop.receive(url, kind: kind, label: title, log: logger,
                                     setPath: { path = $0 }, onAccept: nil)
                }
                Button("浏览…") { browse() }
                if !path.isEmpty {
                    Button {
                        path = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                }
            }
        }
        .pathDropTarget(path: $path, kind: kind, label: title, log: logger)
    }

    private func browse() {
        // 批63b R2：NSOpenPanel / ModalResponse 相关代码集中在 `PanelKit.swift`
        // （旧 Xcode 首次解析 AppKit 运算符的开销不再算到本文件头上）。
        let picked: String?
        if kind == .file {
            // 批63b：只让 picker 显示白名单类型（取舍判据仍在各页的纯函数里，不在这里）。
            picked = PanelKit.pickFile(allowedExtensions: allowedExtensions)
        } else {
            picked = PanelKit.pickDirectory()
        }
        if let picked = picked {
            path = picked
        }
    }
}
