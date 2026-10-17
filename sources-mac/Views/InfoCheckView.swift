import SwiftUI
import AppKit

/// 「检查tex信息」页：运行后把所有文章的 tex 信息并列成一张表，供人工校对。
/// 这是相对 Windows 原版的主要改进 —— 原版只给一份纯文本报告。
struct InfoCheckView: View {

    @ObservedObject var log: LogStore
    @StateObject private var model = InfoCheckModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {

            Text("""
            使用说明：输入路径为一期的目录，例如：
            …/Studies_in_Logic_Database/2026年第5期(90)

            点击运行则读取各篇文章 tex 里已填好的信息（卷号、期号、年份、起止页码、标题、作者等），
            汇总成检查报告，并与 PDF 的实际页数对比。

            会生成两份文件：信息检查<时间戳>.txt（与原版一致）和信息检查<时间戳>.json（结构化数据）。
            """.plainMarkdown)
            .font(.callout)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)

            HStack(spacing: 8) {
                TextField("输入路径或浏览选择路径", text: $model.path)
                    .textFieldStyle(.roundedBorder)
                    .disabled(model.running)
                Button("浏览…") { browse() }
                    .disabled(model.running)
                // U3：运行中主按钮变成「取消」（取消 = 杀整棵进程树，见 RunCancel）
                if model.running {
                    Button {
                        model.cancel?.cancel()
                    } label: {
                        Label("取消", systemImage: "stop.circle")
                    }
                } else {
                    Button("运行") { execute() }
                        .keyboardShortcut("r", modifiers: .command)
                        .buttonStyle(.borderedProminent)
                        .disabled(model.path.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }

            if model.running {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在检查，请查看下方运行日志…")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            if let report = model.report {
                Divider()
                summary(report)
                table(report)
            } else if !model.running {
                Spacer()
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .pathDropTarget(path: $model.path, kind: .directory)
    }

    // MARK: 汇总提示

    @ViewBuilder
    private func summary(_ report: InfoReport) -> some View {
        let notes = report.inconsistencies
        HStack(spacing: 8) {
            Image(systemName: notes.isEmpty ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(notes.isEmpty ? Color.green : Color.orange)
            if notes.isEmpty {
                Text("共 \(report.articles.count) 篇文章，卷号/期号/年份一致，页数与 PDF 实际页数相符。")
                    .font(.callout)
            } else {
                VStack(alignment: .leading, spacing: 2) {
                    Text("发现 \(notes.count) 处需要核对：").font(.callout).bold()
                    ForEach(notes, id: \.self) { note in
                        Text("• " + note).font(.caption).foregroundStyle(.orange)
                    }
                }
            }
            Spacer()
            Button("在访达中显示报告") {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: report.txtPath)])
            }
            .controlSize(.small)
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill(notes.isEmpty ? Color.green.opacity(0.10) : Color.orange.opacity(0.12)))
    }

    // MARK: 校对表格

    @ViewBuilder
    private func table(_ report: InfoReport) -> some View {
        // 注意：SwiftUI 的 Table 最多支持 10 列，所以「页码」与「字号/行距」做了合并
        Table(report.articles) {
            TableColumn("文件夹") { a in
                Text(a.folder).font(.system(size: 11, design: .monospaced))
            }
            .width(min: 105, ideal: 125)

            TableColumn("卷") { a in Text(a.volume.isEmpty ? "—" : a.volume) }.width(38)
            TableColumn("期") { a in Text(a.issue.isEmpty ? "—" : a.issue) }.width(38)
            TableColumn("年份") { a in Text(a.year.isEmpty ? "—" : a.year) }.width(48)

            TableColumn("页码（起–止）") { a in
                Text(a.startPage.isEmpty && a.endPage.isEmpty
                     ? "—" : "\(a.startPage.isEmpty ? "?" : a.startPage)–\(a.endPage.isEmpty ? "?" : a.endPage)")
                    .font(.system(size: 12, design: .monospaced))
            }
            .width(95)

            TableColumn("声明页数") { a in
                Text(a.pages.isEmpty ? "—" : a.pages)
                    .font(.system(size: 12, design: .monospaced))
            }
            .width(60)

            TableColumn("实际页数") { a in
                Text(a.actualPDFPages.map(String.init) ?? "—")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(a.pageMismatch ? Color.red : Color.primary)
                    .bold(a.pageMismatch)
            }
            .width(60)

            TableColumn("字号 / 行距") { a in
                Text("\(a.displayFontSize) / \(a.displayLineSpacing)")
                    .font(.system(size: 11, design: .monospaced))
                    .lineLimit(1)
            }
            .width(min: 90, ideal: 115)

            TableColumn("第一作者") { a in
                Text(a.displayAuthors).lineLimit(1).help(a.displayAuthors)
            }
            .width(min: 90, ideal: 120)

            TableColumn("中文标题") { a in
                Text(a.displayChineseTitle).lineLimit(1).help(a.displayChineseTitle)
            }
            .width(min: 150, ideal: 240)
        }
        .font(.system(size: 12))
        .frame(maxHeight: .infinity)
    }

    // MARK: 动作

    private func browse() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "选择期目录"
        if panel.runModal() == .OK, let url = panel.url {
            model.path = url.path
        }
    }

    private func execute() {
        let target = model.path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !target.isEmpty else { return }
        // U3：每次检查一个取消句柄；`Shell.run` 把 python 子进程注册进 logger.cancel
        let cancel = RunCancel()
        model.cancel = cancel
        let logger = Logger(store: log, startedAt: Date(), cancel: cancel)
        let m = model
        m.running = true
        m.report = nil
        DispatchQueue.global(qos: .userInitiated).async {
            let report = InfoCheckFeature.run(folderPath: target, log: logger)
            DispatchQueue.main.async {
                m.report = report
                m.running = false
            }
        }
    }
}
