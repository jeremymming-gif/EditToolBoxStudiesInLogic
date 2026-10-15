import SwiftUI

/// 「合并文件」页：通用路径输入 + 两个合并选项 + 编委信息页识别预览。
///
/// 识别预览的意义：让你在点「运行」之前就知道程序找到了哪个编委页文件，
/// 而不是等运行完看日志才发现找错了。
struct MergeTab: View {

    @ObservedObject var log: LogStore
    @ObservedObject var settings: AppSettings

    @StateObject private var model = PathRunnerModel()
    @StateObject private var probe = EditorsProbeModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {

            SimpleFeatureView(
                model: model,
                help: """
                使用说明：输入路径为一期的目录，例如：
                …/Studies_in_Logic_Database/2026年第5期(90)

                点击运行则会按「扉页 → 编委信息页（可选）→ 中英文目录 → 各篇文章」的顺序
                合并该目录下所有 PDF，并为每篇文章写入 PDF 书签。
                注意此时文件夹格式应该加上序号，例如：02 Ming_Xiao
                """,
                pickKind: .directory,
                log: log
            ) { path, logger in
                MergeFeature.run(folderPath: path,
                                 log: logger,
                                 nestOutlines: settings.nestOutlines,
                                 mergeEditorsPage: settings.mergeEditorsPage,
                                 appendBackCover: settings.appendBackCover)
            }

            Divider()

            VStack(alignment: .leading, spacing: 9) {
                Toggle("把各篇文章的小节书签挂到该篇文章之下（更好导航；关闭则与 Windows 版一致）",
                       isOn: $settings.nestOutlines)
                    .toggleStyle(.checkbox)

                Toggle("合并编委信息页（把编委名单页插在扉页之后）",
                       isOn: $settings.mergeEditorsPage)
                    .toggleStyle(.checkbox)

                Toggle("附加封底页（把 cover+bottom… 放在最后）",
                       isOn: $settings.appendBackCover)
                    .toggleStyle(.checkbox)

                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: hintIcon).foregroundStyle(hintColor)
                    Text(hintText)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.leading, 20)

                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: backHintIcon).foregroundStyle(backHintColor)
                    Text(backHintText)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.leading, 20)
            }
            .font(.caption)
            .padding(.horizontal, 20)
            .padding(.vertical, 10)
        }
        .task {
            // 首次出现时也刷新一次，这样默认勾选的选项一打开就有预览
            probe.refresh(path: model.path, enabled: settings.mergeEditorsPage)
        }
        .onChange(of: model.path) { _, newValue in
            probe.refresh(path: newValue, enabled: settings.mergeEditorsPage)
        }
        .onChange(of: settings.mergeEditorsPage) { _, newValue in
            probe.refresh(path: model.path, enabled: newValue)
        }
    }

    // MARK: - 封底页预览

    private var backHintText: String {
        guard settings.appendBackCover else {
            return "封底页（如 cover+bottom+Englishi_2024.pdf）不会被加入。"
        }
        let trimmed = model.path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, PathKit.isDirectory(trimmed) else {
            return "请先选择期目录，程序会自动识别 cover+bottom 这类封底页。"
        }
        guard let back = EditorsPageDetector.detectBackCover(in: trimmed) else {
            return "未找到封底页文件，运行时会自动跳过。"
        }
        return "已识别封底页：\(back.fileName)（\(back.pageCount) 页），会放在最后一页。"
    }

    private var backHintIcon: String {
        guard settings.appendBackCover else { return "info.circle" }
        let trimmed = model.path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, PathKit.isDirectory(trimmed) else { return "questionmark.circle" }
        return EditorsPageDetector.detectBackCover(in: trimmed) == nil
            ? "exclamationmark.triangle.fill" : "checkmark.seal.fill"
    }

    private var backHintColor: Color {
        guard settings.appendBackCover else { return .secondary }
        let trimmed = model.path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, PathKit.isDirectory(trimmed) else { return .secondary }
        return EditorsPageDetector.detectBackCover(in: trimmed) == nil ? .orange : .green
    }

    // MARK: - 预览文字

    private var hintText: String {
        guard settings.mergeEditorsPage else {
            return "会按「扉页.pdf → 编委页 → 中文目录 → 英文目录 → 各篇文章」的顺序合并。"
        }
        guard probe.scanned else {
            return "请先选择期目录，程序会自动识别其中的编委信息页。"
        }
        guard let candidate = probe.detection.chosen else {
            return "未找到编委信息页，运行时会自动跳过。建议把该文件命名为「编委页.pdf」放在期目录下。"
        }
        var text = "已识别：\(candidate.fileName)（\(candidate.pageCount) 页，\(candidate.matchedBy)）"
        if probe.coverPages > 1 {
            text += "　⚠️ 扉页.pdf 有 \(probe.coverPages) 页，可能已含编委页，请确认是否会重复。"
        } else if candidate.pageCount > 1 {
            text += "　⚠️ 该文件有 \(candidate.pageCount) 页，请确认它是否也含封面。"
        }
        if probe.detection.isAmbiguous {
            text += "　⚠️ 另有 \(probe.detection.others.count) 个疑似文件已忽略：\(probe.detection.others.joined(separator: "、"))"
        }
        return text
    }

    private var hintIcon: String {
        guard settings.mergeEditorsPage else { return "info.circle" }
        guard probe.scanned else { return "questionmark.circle" }
        if probe.detection.chosen == nil { return "exclamationmark.triangle.fill" }
        return probe.detection.isAmbiguous ? "exclamationmark.triangle.fill" : "checkmark.seal.fill"
    }

    private var hintColor: Color {
        guard settings.mergeEditorsPage, probe.scanned else { return .secondary }
        if probe.detection.chosen == nil { return .orange }
        return probe.detection.isAmbiguous ? .orange : .green
    }
}
