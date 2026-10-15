import Foundation
import SwiftUI

/// 路径字段的「拖进来就能用」支持。
///
/// 为什么单独抽一个 modifier：四个页面各写一遍 `dropDestination` 会走样——
/// 实测「初排转换」页的五个路径框（含「源文件」「输出目录」）当初就漏了，
/// 而使用说明里已经写着「可以直接把文件夹（或文件）拖进来」，
/// 编辑照着做却没反应。现在统一走这里。
///
/// ⚠️ 悬停高亮的状态**不能用 `@State`**：本机只装了 Command Line Tools，
/// SwiftUI 的 `State` 宏（SwiftUIMacros 插件）不可用，编译直接报
/// `external macro implementation type 'SwiftUIMacros.StateMacro' could not be found`。
/// 所以和其余界面一样，用 `ObservableObject` + `@StateObject`。
enum PathDrop {

    /// 这个 URL 是文件还是文件夹；两者都不是（不存在）时返回 nil。
    static func kind(of url: URL) -> PickKind? {
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir) else {
            return nil
        }
        return isDir.boolValue ? .directory : .file
    }

    /// 拖进来的东西类型对不对：文件夹字段只收文件夹，文件字段只收文件。
    ///
    /// 不做类型过滤的话，把 `.docx` 拖到「输出目录」也会被接受，
    /// 然后要到运行的最后一刻才报一句离得很远的错。
    /// （注意用 `Self.` 前缀：参数名 `kind` 会把这个同名方法遮住，
    ///   直接写 `kind(of: url)` 会编译不过——实测踩到。）
    static func accepts(_ url: URL, kind: PickKind) -> Bool {
        Self.kind(of: url) == kind
    }

    static func describe(_ kind: PickKind) -> String {
        kind == .directory ? "文件夹" : "文件"
    }
}

/// 拖动悬停状态（见上面关于 `@State` 的说明）
private final class DropHover: ObservableObject {
    @Published var targeted = false
}

private struct PathDropModifier: ViewModifier {

    @Binding var path: String
    let kind: PickKind
    /// 日志里怎么称呼这个字段（「输出目录（必填）」…）
    let label: String?
    /// 落地与被拒都记一行——**不能静默**：编辑拖了没反应时，
    /// 至少日志里能看出是「类型不对」还是「压根没收到拖拽」。
    let log: Logger?
    /// 落地后顺带要做的事（例如「生成目录」页要立刻把报告读一遍）
    let onAccept: (() -> Void)?

    @StateObject private var hover = DropHover()

    func body(content: Content) -> some View {
        content
            // 没有 contentShape 的话，只有子视图（输入框、按钮）占住的地方能接住拖拽，
            // 行内的空白与标题区域是"透明"的 —— 拖到那儿会毫无反应。
            .contentShape(Rectangle())
            // 拖动悬停时描一圈，让人知道这里能放
            .overlay {
                RoundedRectangle(cornerRadius: 6)
                    .strokeBorder(
                        Color.accentColor,
                        style: StrokeStyle(lineWidth: 2, dash: [5, 3])
                    )
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Color.accentColor.opacity(0.08))
                    )
                    .opacity(hover.targeted ? 1 : 0)
                    .allowsHitTesting(false)
            }
            .dropDestination(for: URL.self) { urls, _ in
                guard let url = urls.first else { return false }
                let where_ = label.map { "「\($0)」" } ?? ""
                guard PathDrop.accepts(url, kind: kind) else {
                    let got = PathDrop.kind(of: url).map(PathDrop.describe) ?? "无法识别的项目"
                    log?("（拖入的\(got)\(where_)用不上：这里要\(PathDrop.describe(kind))）\n")
                    return false
                }
                path = url.path
                log?("拖入\(where_)：\(url.path)\n")
                onAccept?()
                return true
            } isTargeted: { flag in
                hover.targeted = flag
            }
            .animation(.easeOut(duration: 0.12), value: hover.targeted)
    }
}

extension View {

    /// 让这一块视图接收 Finder 拖来的文件 / 文件夹，并写进 `path`。
    func pathDropTarget(path: Binding<String>,
                        kind: PickKind,
                        label: String? = nil,
                        log: Logger? = nil,
                        onAccept: (() -> Void)? = nil) -> some View {
        modifier(PathDropModifier(path: path, kind: kind, label: label,
                                  log: log, onAccept: onAccept))
    }
}
