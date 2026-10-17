import SwiftUI
import AppKit

/// U4「摘要 / 原始」两层的界面状态（本机没有 `@State` 宏，状态用 ObservableObject 承载）。
final class LogViewModel: ObservableObject {
    /// false = 摘要（默认：原始层折叠）；true = 原始（完整日志）。
    /// 选择记在 UserDefaults 里，下次启动沿用（AUDIT U4 要求「保留折叠状态」）。
    @Published var showRaw: Bool {
        didSet { UserDefaults.standard.set(showRaw, forKey: "slcn.showRawLog") }
    }

    init() {
        showRaw = UserDefaults.standard.bool(forKey: "slcn.showRawLog")
    }
}

/// 运行日志面板，对应原程序窗口下方那个 25 行高的 ScrolledText。
///
/// UI 方案 §2-8：等宽字体、级别色（红=错误 / 橙=警告 / 绿=完成）、自动滚动开关（上翻看旧日志
/// 时**别把人强拉到底**）、复制全部。颜色只是辅助 —— 行首本来就有 ✅/⚠/❌ 符号。
///
/// U4（2026-10-17）：日志分「摘要 / 原始」两层，默认摘要（原始折叠）——
/// 整期编译时原始行很多，编辑先看关键节点即可；要排错切「原始」就能看到全部。
/// 「复制全部 / 保存日志」始终给**完整日志**（含折叠行），避免留证据时漏掉细节。
struct LogPane: View {

    @ObservedObject var log: LogStore
    /// 自动滚动：用户上翻时自动关掉，点「跳到最新」恢复
    @State private var autoScroll = true
    @StateObject private var model = LogViewModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {

            HStack(spacing: 8) {
                Image(systemName: "text.alignleft")
                Text("运行日志").font(.headline)

                // U4：摘要 / 原始（默认摘要）
                Picker("", selection: $model.showRaw) {
                    Text("摘要").tag(false)
                    Text("原始").tag(true)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 120)
                .controlSize(.small)
                .help("摘要 = 只留关键节点与结果；原始 = 引擎逐行输出（含 TeX 细节）")

                if !model.showRaw, log.foldedLineCount > 0 {
                    Text("已折叠 \(log.foldedLineCount) 行原始日志")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }

                Spacer()
                Toggle("自动滚动", isOn: $autoScroll)
                    .toggleStyle(.checkbox)
                    .controlSize(.small)
                Button("跳到最新") {
                    autoScroll = true
                }
                .controlSize(.small)
                Button("复制全部") {
                    // 始终复制**完整日志**（含摘要模式下折叠的行）——留证据时不能漏
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
                        if log.text.isEmpty {
                            emptyState
                        } else if model.showRaw {
                            Text(log.attributed)
                                .font(.system(size: 11, design: .monospaced))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        } else if log.summaryAttributed.characters.isEmpty {
                            Text("（本次日志没有可归入摘要的行；切换到「原始」查看全部）")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.vertical, 8)
                        } else {
                            Text(log.summaryAttributed)
                                .font(.system(size: 11, design: .monospaced))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
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

    /// 空状态（观察③）：未跑任务时不再是一片纯白，给一句「日志会出现在这里」。
    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: "text.alignleft")
                .font(.title3)
                .foregroundStyle(.tertiary)
            Text("运行任务后，日志会显示在这里…")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 120)
        .padding(.top, 24)
    }
}
