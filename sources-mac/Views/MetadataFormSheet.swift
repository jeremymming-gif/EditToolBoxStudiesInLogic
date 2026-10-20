import SwiftUI

/// 批63b：初排页「人工填写元数据」表单（sheet）。
///
/// 这里只做**绑定与展示**；判据（表单→JSON、校验、上限、临时文件命名/清理）
/// 全部在 `Features/MetadataFormFeature.swift` 的纯逻辑里（headless 有断言），
/// 视图不写第二份判据。
///
/// 本次运行有效：值不持久化（App 退出即丢，同一会话内多篇可复用）；
/// 运行前由 `ConvertTab.run()` 写成 `/tmp` 临时 JSON（`--metadata-json`），跑完即删。
/// 两段式协同 v1 不支持（引擎会 exit 2）：入口按钮在协同开启时禁用，见 `ConvertTab`。
struct MetadataFormSheet: View {
    @Binding var form: MetadataForm
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    titleSection
                    abstractSection
                    keywordsSection
                    authorsSection
                }
                .padding(18)
            }
            Divider()
            footer
        }
        .frame(minWidth: 760, idealWidth: 820, minHeight: 560, idealHeight: 720)
    }

    // MARK: - 头部 / 底部

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text("填写元数据").font(.title3).bold()
            Text("值会直接写入交付产物；留空 = 不覆盖（沿用引擎自动解析）。")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 6) {
            // 校验：只提示、不阻断（邮箱格式可疑 / 空作者被忽略 / 作者缺姓名）。
            ForEach(Array(form.warnings().enumerated()), id: \.offset) { _, warning in
                Text("⚠ " + warning)
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 12) {
                Text(form.hasAnyValue
                     ? form.summaryLine() + "（运行结束删除临时 JSON）"
                     : "留空 = 不覆盖，与没打开过表单完全一样")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("清空表单") { form = MetadataForm() }
                    .disabled(!form.hasAnyValue)
                Button("取消") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("完成") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }

    // MARK: - 标题 / 摘要 / 关键词

    private var titleSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            sectionTitle("标题")
            labeledField("中文题名", $form.titleCN, "留空则用自动解析结果")
            labeledField("英文题名", $form.titleEN, "留空则用自动解析结果")
        }
    }

    private var abstractSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            sectionTitle("摘要")
            multilineField("中文摘要", $form.abstractCN)
            multilineField("英文摘要", $form.abstractEN)
        }
    }

    private var keywordsSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            sectionTitle("关键词")
            labeledField("中文关键词", $form.keywordsCN, "如：逻辑学；语义学")
            labeledField("英文关键词", $form.keywordsEN, "如：logic; semantics")
            Text("中文模板只有中文关键词槽；英文关键词两个模板都没有槽位，"
                 + "引擎会在日志里提示人工放置（填了不会丢，只是不会自动落版）。")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 作者（1–5；第一作者锁删；每位多单位 CN/EN）

    private var authorsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            authorsHeader
            Text(MetadataForm.limitSummary)
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(form.authors.indices, id: \.self) { index in
                authorCard(index: index)
            }
            if form.reachedAuthorLimit {
                Text(MetadataForm.authorLimitHint())
                    .font(.caption2)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var authorsHeader: some View {
        HStack {
            sectionTitle("作者（最多 \(MetadataForm.maxAuthors) 位）")
            Text("\(form.authors.count)/\(MetadataForm.maxAuthors)")
                .font(.caption2)
                .foregroundStyle(form.reachedAuthorLimit ? .orange : .secondary)
            Spacer()
            addAuthorButton
        }
    }

    private var addAuthorButton: some View {
        Button {
            _ = form.addAuthor()
        } label: {
            Label("添加作者", systemImage: "plus.circle")
        }
        .disabled(form.reachedAuthorLimit)
        // 达限时鼠标悬停给出原因与降级建议（按钮已灰，不点也看得到为什么）。
        .help(form.reachedAuthorLimit
              ? MetadataForm.authorLimitHint()
              : "作者最多 \(MetadataForm.maxAuthors) 位（模板槽位上限）")
    }

    private func authorCard(index: Int) -> some View {
        let isFirst = index == 0
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(isFirst ? "第一作者" : "作者 \(index + 1)")
                    .font(.callout).bold()
                Spacer()
                if !isFirst {
                    Button(role: .destructive) {
                        _ = form.removeAuthor(at: index)
                    } label: {
                        Label("删除", systemImage: "trash")
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                }
            }
            HStack(spacing: 12) {
                TextField("中文姓名", text: $form.authors[index].nameCN)
                    .textFieldStyle(.roundedBorder)
                TextField("英文姓名（拼音/英文名）", text: $form.authors[index].nameEN)
                    .textFieldStyle(.roundedBorder)
                TextField("邮箱", text: $form.authors[index].email)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 220)
            }
            affiliationEditor(index: index, language: .cn)
            affiliationEditor(index: index, language: .en)
        }
        .padding(10)
        .background(Color.secondary.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// 单位区间：每种语言独立动态行（「添加单位」/ 行内删除；上限 3 行，防 PDF 溢版）。
    private func affiliationEditor(index: Int,
                                   language: MetadataForm.AffiliationLanguage) -> some View {
        let title = language == .cn ? "单位（中文）" : "单位（英文）"
        let placeholder = language == .cn
            ? "如：中山大学逻辑与认知研究所"
            : "如：Institute of Logic and Cognition, Sun Yat-sen University"
        let reached = form.reachedAffiliationLimit(authorIndex: index, language: language)
        return VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title).font(.caption).foregroundStyle(.secondary)
                Text("\(affiliationCount(index: index, language: language))"
                     + "/\(MetadataForm.maxAffiliationsPerLanguage)")
                    .font(.caption2)
                    .foregroundStyle(reached ? .orange : .secondary)
                Spacer()
                Button {
                    _ = form.addAffiliation(authorID: form.authors[index].id, language: language)
                } label: {
                    Label("添加单位", systemImage: "plus")
                }
                .buttonStyle(.borderless)
                .font(.caption)
                .disabled(reached)
                .help(reached
                      ? MetadataForm.affiliationLimitHint(authorIndex: index, language: language)
                      : "每位作者每种语言最多 \(MetadataForm.maxAffiliationsPerLanguage) 行单位")
            }
            ForEach(affiliationIDs(index: index, language: language), id: \.self) { affID in
                HStack(spacing: 8) {
                    TextField(placeholder,
                              text: affiliationBinding(index: index, language: language, id: affID))
                        .textFieldStyle(.roundedBorder)
                    Button {
                        _ = form.removeAffiliation(authorID: form.authors[index].id,
                                                   language: language, affiliationID: affID)
                    } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                }
            }
            if reached {
                Text(MetadataForm.affiliationLimitHint(authorIndex: index, language: language))
                    .font(.caption2)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.leading, 6)
    }

    private func affiliationIDs(index: Int,
                                language: MetadataForm.AffiliationLanguage) -> [UUID] {
        guard index < form.authors.count else { return [] }
        let author = form.authors[index]
        return (language == .cn ? author.affiliationsCN : author.affiliationsEN).map(\.id)
    }

    private func affiliationCount(index: Int,
                                  language: MetadataForm.AffiliationLanguage) -> Int {
        affiliationIDs(index: index, language: language).count
    }

    private func affiliationBinding(index: Int,
                                    language: MetadataForm.AffiliationLanguage,
                                    id: UUID) -> Binding<String> {
        Binding(
            get: {
                guard index < form.authors.count else { return "" }
                let list = language == .cn ? form.authors[index].affiliationsCN
                                           : form.authors[index].affiliationsEN
                return list.first(where: { $0.id == id })?.text ?? ""
            },
            set: { newValue in
                guard index < form.authors.count else { return }
                if language == .cn {
                    if let j = form.authors[index].affiliationsCN.firstIndex(where: { $0.id == id }) {
                        form.authors[index].affiliationsCN[j].text = newValue
                    }
                } else {
                    if let j = form.authors[index].affiliationsEN.firstIndex(where: { $0.id == id }) {
                        form.authors[index].affiliationsEN[j].text = newValue
                    }
                }
            }
        )
    }

    // MARK: - 小部件

    private func sectionTitle(_ text: String) -> some View {
        Text(text).font(.headline)
    }

    private func labeledField(_ label: String, _ text: Binding<String>,
                              _ placeholder: String) -> some View {
        HStack(spacing: 12) {
            Text(label).frame(width: 90, alignment: .trailing)
            TextField(placeholder, text: text)
                .textFieldStyle(.roundedBorder)
        }
    }

    private func multilineField(_ label: String, _ text: Binding<String>) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(label).frame(width: 90, alignment: .trailing).padding(.top, 6)
            TextField("留空则用自动解析结果", text: text, axis: .vertical)
                .lineLimit(3...8)
                .textFieldStyle(.roundedBorder)
        }
    }
}
