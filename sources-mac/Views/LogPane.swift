import SwiftUI

/// 运行日志面板，对应原程序窗口下方那个 25 行高的 ScrolledText。
struct LogPane: View {

    @ObservedObject var log: LogStore

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {

            HStack {
                Image(systemName: "text.alignleft")
                Text("运行日志").font(.headline)
                Spacer()
                Button("清空") { log.clear() }
                    .controlSize(.small)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)

            Divider()

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        Text(log.text)
                            .font(.system(size: 11, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Color.clear.frame(height: 1).id("logBottom")
                    }
                    .padding(8)
                }
                .onChange(of: log.text) { _, _ in
                    proxy.scrollTo("logBottom", anchor: .bottom)
                }
            }
        }
        .frame(minHeight: 160)
    }
}
