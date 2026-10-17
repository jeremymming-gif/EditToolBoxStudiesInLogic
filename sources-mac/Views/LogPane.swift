import SwiftUI
import AppKit

/// 运行日志面板，对应原程序窗口下方那个 25 行高的 ScrolledText。
///
/// UI 方案 §2-8：等宽字体、级别色（红=错误 / 橙=警告 / 绿=完成）、自动滚动开关（上翻看旧日志
/// 时**别把人强拉到底**）、复制全部。颜色只是辅助 —— 行首本来就有 ✅/⚠/❌ 符号。
struct LogPane: View {

    @ObservedObject var log: LogStore
    /// 自动滚动：用户上翻时自动关掉，点「跳到最新」恢复
    @State private var autoScroll = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {

            HStack {
                Image(systemName: "text.alignleft")
                Text("运行日志").font(.headline)
                Spacer()
                Toggle("自动滚动", isOn: $autoScroll)
                    .toggleStyle(.checkbox)
                    .controlSize(.small)
                Button("跳到最新") {
                    autoScroll = true
                }
                .controlSize(.small)
                Button("复制全部") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(log.text, forType: .string)
                }
                .controlSize(.small)
                Button("清空") { log.clear() }
                    .controlSize(.small)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)

            Divider()

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        Text(log.attributed)
                            .font(.system(size: 11, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Color.clear.frame(height: 1).id("logBottom")
                    }
                    .padding(8)
                }
                .onChange(of: log.text) { _, _ in
                    if autoScroll {
                        proxy.scrollTo("logBottom", anchor: .bottom)
                    }
                }
            }
        }
        .frame(minHeight: 160)
    }
}
