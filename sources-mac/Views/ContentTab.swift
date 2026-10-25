import SwiftUI
import AppKit

/// 栏目勾选列表里的一行
struct TocArticleRow: Identifiable {
    let id: String            // 报告里的文章名，如 "Meimei_Han"
    let title: String
    var selected: Bool
}

/// 「生成目录」页的界面状态
final class ContentTabModel: ObservableObject {
    @Published var path: String = ""
    @Published var running: Bool = false
    @Published var settings = TocSettings()
    @Published var rows: [TocArticleRow] = []
    @Published var templateNote: String = "请先选择「检查tex信息」生成的 txt 报告"
    @Published var settingsLoaded = false
    /// 高级开关：打开 = 旧行为（只生成带时间戳的 .tex，不落位、不编译）。默认关。
    @Published var textOnly: Bool = false
    /// 选中报告后发现的提示（例如缺 000 目录 —— 生成时会自动创建）。
    @Published var notices: [String] = []
    /// 结果区（由引擎结果 JSON 填；界面不自己判断模板来源）
    @Published var resultTitle: String = ""
    @Published var resultLines: [String] = []
    @Published var resultDir: String = ""
    @Published var resultStatus: Int32 = 0
    /// U3：本次生成的取消句柄（运行中主按钮变成「取消」）
    var cancel: RunCancel? = nil

    var hasResult: Bool { !resultTitle.isEmpty }
}

/// 收集引擎输出末尾几条（失败时把 stderr 的可操作原因显示到结果区）。
/// 回调来自 `Shell.run` 的读线程；用锁保护，读永远在同一个后台线程结束后发生。
private final class TocOutputCapture: @unchecked Sendable {
    private let lock = NSLock()
    private var text = ""

    func append(_ chunk: String) {
        lock.lock()
        text += chunk
        if text.count > 200_000 {
            text = String(text.suffix(100_000))
        }
        lock.unlock()
    }

    func snapshot() -> String {
        lock.lock()
        defer { lock.unlock() }
        return text
    }
}

/// 「生成目录」页：选报告 → 设置本期栏目 → 勾选栏目文章 → 一键生成并交付。
struct ContentTab: View {

    @ObservedObject var log: LogStore
    @StateObject private var model = ContentTabModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {

                Text("""
                使用说明：选择【检查tex信息】生成的 txt 报告，点「生成并交付」一次完成：
                缺少 000 Table of Contents 时自动创建 → 生成中英文目录 → 落位成固定文件名
                （Chinese Content.tex/.pdf、contents.tex/.pdf）→ 用 XeLaTeX 编译成 PDF。
                结果区会列出 4 个成品路径与页数，可直接打开目录。
                模板来源由引擎判断并显示（本期成品 → 上一期 → 内置模板），界面不再另猜。
                高级开关「仅生成 tex（不落位）」打开时只写带时间戳的 .tex（旧行为），不落位、不编译。
                """.plainMarkdown)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

                // ---------- 报告路径 ----------
                HStack(spacing: 8) {
                    DroppablePathField(text: $model.path, placeholder: "输入检查报告的路径或拖进来",
                                       disabled: model.running) { url in
                        PathDrop.receive(url, kind: .file, label: nil, log: nil,
                                         setPath: { model.path = $0 }, onAccept: { loadReport() })
                    }
                    Button("浏览…") { browse() }.disabled(model.running)
                    // U3：运行中主按钮变成「取消」（取消 = 杀整棵进程树，见 RunCancel）
                    if model.running {
                        Button {
                            model.cancel?.cancel()
                        } label: {
                            Label("取消", systemImage: "stop.circle")
                        }
                    } else {
                        Button(model.textOnly ? "仅生成 tex" : "生成并交付") { execute() }
                            .keyboardShortcut("r", modifiers: .command)
                            .buttonStyle(.borderedProminent)
                            .disabled(model.path.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }

                HStack(spacing: 6) {
                    Toggle("仅生成 tex（不落位、不编译）", isOn: $model.textOnly)
                        .toggleStyle(.checkbox)
                        .disabled(model.running)
                    Spacer()
                }
                Text("高级选项：默认关闭。打开后恢复旧行为 —— 只在报告同目录写带时间戳的目录 .tex，"
                     + "不放进 000 Table of Contents、不编译 PDF。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                ForEach(model.notices, id: \.self) { notice in
                    HStack(spacing: 6) {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        Text(notice).font(.caption).foregroundStyle(.orange)
                    }
                }

                HStack(spacing: 6) {
                    Image(systemName: "doc.text.magnifyingglass")
                    Text(model.templateNote).font(.caption).foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                // ---------- 结果区（引擎结果 JSON：4 路径 / 模板来源 / 页数 / 注意事项）----------
                if model.hasResult {
                    resultCard
                }

                if !model.rows.isEmpty {
                    Divider()
                    columnSettings
                    Divider()
                    articleList
                }

                if model.running {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("正在生成并编译，请查看下方运行日志…").font(.caption)
                    }
                }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .pathDropTarget(path: $model.path, kind: .file) {
            loadReport()
        }
        .onChange(of: model.path) { _, _ in
            // 手动输入路径时也自动读取（路径存在才生效），
            // 这样不用非得点「浏览」，栏目设置与文章列表也会出来
            if model.rows.isEmpty { loadReport() }
        }
    }

    // MARK: - 结果区

    private var resultColor: Color {
        switch model.resultStatus {
        case 0: return .green
        case 1: return .orange
        default: return .red
        }
    }

    @ViewBuilder
    private var resultCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(model.resultTitle).font(.headline).foregroundStyle(resultColor)
                if !model.resultDir.isEmpty {
                    Button("打开目录") {
                        NSWorkspace.shared.open(URL(fileURLWithPath: model.resultDir))
                    }
                    .controlSize(.small)
                }
                Spacer()
            }
            ForEach(model.resultLines, id: \.self) { line in
                Text(line)
                    .font(.callout)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .textBackgroundColor))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8).stroke(resultColor.opacity(0.35), lineWidth: 1)
        )
    }

    // MARK: - 栏目设置

    @ViewBuilder
    private var columnSettings: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("本期栏目").font(.headline)

            HStack(spacing: 10) {
                Text("类型").font(.callout)
                Picker("", selection: $model.settings.columnKind) {
                    Text("无").tag("")
                    Text("专栏").tag("专栏")
                    Text("专辑").tag("专辑")
                }
                .labelsHidden()
                .frame(width: 90)

                if model.settings.columnEditing {
                    Text("名称").font(.callout)
                    TextField("如：认知悖论研究", text: $model.settings.columnName)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 190)
                    Text("英文名").font(.callout)
                    TextField("如：Research on Epistemic Paradoxes", text: $model.settings.columnNameEN)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 240)
                    Text("主持人").font(.callout)
                    TextField("如：郑建国", text: $model.settings.columnHost)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 120)
                }
                Spacer()
            }

            if model.settings.columnEditing && model.settings.columnName.isEmpty {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    Text("请填写栏目名称，否则不会生成栏目行。").font(.caption).foregroundStyle(.orange)
                }
            }

            if model.settings.columnEditing {
                HStack(spacing: 8) {
                    Text("栏目文章：在下面勾选，勾中的会在目录里缩进（\(model.rows.filter(\.selected).count) 篇）")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("全选") { setAll(true) }.controlSize(.small)
                    Button("全不选") { setAll(false) }.controlSize(.small)
                }
            }

            Divider()

            HStack(spacing: 8) {
                Text("标题断行上限").font(.callout)
                Text("中文").font(.caption)
                TextField("20", value: $model.settings.titleWrapLimit, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 56)
                Text("英文").font(.caption)
                TextField("30", value: $model.settings.titleWrapLimitEN, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 56)
                Text("按汉字宽算。超过上限的标题会自动断成两行，避免把目录表格撑宽、页码被挤出页面；填 0 表示不断行。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
            }
        }
    }

    // MARK: - 文章列表

    @ViewBuilder
    private var articleList: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("文章列表").font(.headline)
            ForEach($model.rows) { $row in
                HStack(spacing: 8) {
                    if model.settings.columnEditing {
                        Toggle("", isOn: $row.selected)
                            .toggleStyle(.checkbox)
                            .labelsHidden()
                    } else {
                        Text("·").foregroundStyle(.tertiary).frame(width: 14)
                    }
                    Text(row.id)
                        .font(.system(size: 11, design: .monospaced))
                        .frame(width: 150, alignment: .leading)
                    Text(row.title).font(.callout).lineLimit(1)
                    Spacer()
                }
            }
        }
    }

    // MARK: - 动作

    private func setAll(_ value: Bool) {
        for i in model.rows.indices { model.rows[i].selected = value }
    }

    private func browse() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowedFileTypes = ["txt"]
        panel.prompt = "选择检查报告"
        if panel.runModal() == .OK, let url = panel.url {
            model.path = url.path
            loadReport()
        }
    }

    /// 读入报告：列出文章、并载入本期已有的目录设置
    private func loadReport() {
        let target = PathKit.normalize(model.path)
        guard PathKit.exists(target) else { return }
        let dir = (target as NSString).deletingLastPathComponent

        var settings = TocSettings.load(from: dir)
        model.settings = settings
        model.settingsLoaded = true
        // 前置提示（失败 UX 提前）：缺 000 目录时引擎会自动创建，先把话说清楚。
        if FileManager.default.fileExists(atPath: PathKit.join(dir, "000 Table of Contents")) {
            model.notices = []
        } else {
            model.notices = ["这一期还没有「000 Table of Contents」目录；生成时会自动创建，"
                             + "并把中英文目录落位、编译到里面。"]
        }

        // 篇目清单与模板说明都**由引擎算**（`toc.py --preview`）：解析口径必须与真正生成时一致，
        // 否则会出现「清单里勾了 3 篇、生成时只认 2 篇」。界面不再自己解析报告。
        // 案例 126：这里要跑引擎子进程，**不能**在主线程同步做（拖拽落下时会把 main runloop
        // 卡在 drop 提交动画事务里 → 闪退）。放后台，结果回主线程；路径已变则丢弃。
        let m = model
        DispatchQueue.global(qos: .userInitiated).async {
            let preview = TocFeature.preview(reportPath: target)
            DispatchQueue.main.async {
                guard PathKit.normalize(m.path) == target else { return }
                if let preview {
                    m.rows = preview.articles.map { item in
                        TocArticleRow(id: item.name,
                                      title: item.title,
                                      selected: settings.contains(item.name))
                    }
                    m.templateNote = preview.templateNote
                } else {
                    m.rows = []
                    m.templateNote = "读不到报告预览（引擎未找到或报告格式不对），请看下方日志"
                }
            }
        }
    }

    private func execute() {
        let target = model.path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !target.isEmpty else { return }

        // 把勾选结果写回设置并保存到报告所在目录
        let dir = (PathKit.normalize(target) as NSString).deletingLastPathComponent
        var settings = model.settings
        settings.columnArticles = model.rows.filter(\.selected).map(\.id)
        if settings.isColumnEnabled && settings.columnHost.isEmpty {
            settings.columnHost = ""
        }
        try? settings.save(to: dir)

        // U3：每次生成一个取消句柄；`Shell.run` 把 python 子进程注册进 logger.cancel
        let cancel = RunCancel()
        model.cancel = cancel
        let capture = TocOutputCapture()
        let logger = Logger(store: log, startedAt: Date(), cancel: cancel,
                            onLine: { capture.append($0) })
        let m = model
        let textOnly = model.textOnly
        let resultJSONPath = textOnly ? "" : TocFeature.newResultJSONPath()
        m.running = true
        m.resultTitle = ""
        m.resultLines = []
        m.resultDir = ""
        DispatchQueue.global(qos: .userInitiated).async {
            // 生成与交付交给引擎（`Engine/toc.py`）；本期设置刚写进 目录设置.json，引擎读的就是它。
            let outcome = TocFeature.run(reportPath: target,
                                        templateFolder: settings.templateFolder,
                                        textOnly: textOnly,
                                        resultJSONPath: resultJSONPath,
                                        log: logger)
            let output = capture.snapshot()
            if !resultJSONPath.isEmpty {
                try? FileManager.default.removeItem(atPath: resultJSONPath)
            }
            DispatchQueue.main.async {
                m.running = false
                apply(outcome: outcome, output: output, reportDir: dir, textOnly: textOnly)
            }
        }
    }

    /// 把一次运行的结果填进结果区。失败原因来自引擎 stderr（界面只显示，不自己判定）。
    private func apply(outcome: TocFeature.RunOutcome,
                       output: String,
                       reportDir: String,
                       textOnly: Bool) {
        if outcome.cancelled {
            model.resultStatus = Shell.cancelledStatus
            model.resultTitle = "⏹ 已取消"
            model.resultLines = ["目录可能未生成或只写了一半，需要时重新运行。"]
            model.resultDir = ""
            return
        }
        model.resultStatus = outcome.status
        if let result = outcome.result {
            model.resultTitle = outcome.status == 0
                ? "✅ 生成并交付完成"
                : "⚠️ 已交付，但有需要人工核对的地方（见下）"
            var lines = TocFeature.displayLines(for: result)
            if lines.isEmpty {
                lines = ["（引擎没有返回可显示的详情；产物位置见运行日志）"]
            }
            model.resultLines = lines
            model.resultDir = result.tocDir ?? reportDir
            return
        }
        switch outcome.status {
        case 0, 1:
            model.resultTitle = textOnly
                ? "✅ 已生成 tex（仅生成，未落位/未编译）"
                : (outcome.status == 0 ? "✅ 完成" : "⚠️ 已完成，但有需要人工核对的地方")
            model.resultLines = textOnly
                ? ["产物在报告同目录（带时间戳的 .tex）。"]
                : ["（引擎没有返回结果 JSON；产物位置见运行日志）"]
            model.resultDir = reportDir
        default:
            model.resultTitle = "❌ 生成失败（退出码 \(outcome.status)，已中止）"
            let reason = TocFeature.failureReason(from: output)
            model.resultLines = [reason.isEmpty ? "原因见下方运行日志（引擎 stderr）。" : reason]
            model.resultDir = ""
        }
    }
}
