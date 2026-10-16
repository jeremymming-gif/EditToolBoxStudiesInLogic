import SwiftUI
import AppKit

/// 栏目勾选列表里的一行
struct TocArticleRow: Identifiable {
    let id: String            // 报告里的文章名，如 "Jingru_Kou"
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
}

/// 「生成目录」页：选报告 → 设置本期栏目 → 勾选栏目文章 → 运行
struct ContentTab: View {

    @ObservedObject var log: LogStore
    @StateObject private var model = ContentTabModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {

                Text("""
                使用说明：请输入【检查tex信息】生成的 txt 的完整路径。
                程序会以上一期的成品目录为模板（若本期已有成品则用本期的），
                自动填入卷号、期号、年份、总期号与出版日期，然后生成：
                    Chinese Content_<时间戳>.tex 和 contents_<时间戳>.tex
                """)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

                // ---------- 报告路径 ----------
                HStack(spacing: 8) {
                    TextField("输入检查报告的路径或拖进来", text: $model.path)
                        .textFieldStyle(.roundedBorder)
                        .disabled(model.running)
                    Button("浏览…") { browse() }.disabled(model.running)
                    Button(model.running ? "运行中…" : "生成目录") { execute() }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.running || model.path.trimmingCharacters(in: .whitespaces).isEmpty)
                }

                HStack(spacing: 6) {
                    Image(systemName: "doc.text.magnifyingglass")
                    Text(model.templateNote).font(.caption).foregroundStyle(.secondary)
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
                        Text("正在生成，请查看下方运行日志…").font(.caption)
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
                    TextField("如：张建军", text: $model.settings.columnHost)
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
                TextField("35", value: $model.settings.titleWrapLimitEN, format: .number)
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

        // 篇目清单与模板说明都**由引擎算**（`toc.py --preview`）：解析口径必须与真正生成时一致，
        // 否则会出现「清单里勾了 3 篇、生成时只认 2 篇」。界面不再自己解析报告。
        if let preview = TocFeature.preview(reportPath: target) {
            model.rows = preview.articles.map { item in
                TocArticleRow(id: item.name,
                              title: item.title,
                              selected: settings.contains(item.name))
            }
            model.templateNote = preview.templateNote
        } else {
            model.rows = []
            model.templateNote = "读不到报告预览（引擎未找到或报告格式不对），请看下方日志"
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

        let logger = Logger(store: log)
        let m = model
        m.running = true
        DispatchQueue.global(qos: .userInitiated).async {
            // 生成交给引擎（`Engine/toc.py`）；本期设置刚写进 目录设置.json，引擎读的就是它。
            TocFeature.run(reportPath: target, templateFolder: settings.templateFolder, log: logger)
            DispatchQueue.main.async { m.running = false }
        }
    }
}
