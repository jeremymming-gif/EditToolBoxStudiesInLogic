import SwiftUI
import Foundation

extension ConvertTab {
    // MARK: - 初排页 section（body 拆分；顺序 = 界面自上而下）

    /// 源文件 + 输出目录（两个必填路径）。
    var sourceAndOutputSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            // ---------- 源文件（必填） ----------
            LabeledPathRow(
                title: "源文件（必填）",
                placeholder: "Word 文稿（.docx）或 LaTeX 文件（.tex）",
                path: $model.options.source,
                kind: .file,
                logStore: log
            )

            // ---------- 输出目录（必填） ----------
            LabeledPathRow(
                title: "输出目录（必填）",
                placeholder: "初排稿的输出位置，例如 …/2026年第5期(90)（工具会在其下建 名_姓/；不要选 03 名_姓，会多套一层）",
                path: $model.options.out,
                kind: .directory,
                logStore: log
            )
        }
    }

    /// 常勾选项（编译/清理/AI/协同/原稿 PDF）。
    var commonOptionsSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            compileCleanToggles
            // 批 62 入口收敛（B 方案）：独立的「生成文献核实任务书」勾选已隐藏，
            // 本页只保留两个语义入口 —— 两段式（任务书 = 第一段产物）与 ⑪ 直连 AI
            // （内部自动带 --verify-refs，产物/行为与批61 一致）。
            // `Options.verifyRefs` 字段保留给旧脚本/既有调用方，界面不再绑定它；
            // 普通初排转换不再带 --verify-refs（默认产物无 <stem>_文献核实.md）。
            // 第 132 条：核实成本闸门提示（值来自「引用库设置」页，点击转换时现取）。
            if model.options.useAI || model.options.coop {
                VerifyGateHint(bibdb: bibdb)
            }
            aiToggleRow
            AiHintView(settings: settings,
                       usesAI: model.options.useAI,
                       usesCoop: model.options.coop)
            aiExtraTogglesSection
            coopToggleRow
            sourcePdfToggle
        }
        .toggleStyle(.checkbox)
    }

    private var compileCleanToggles: some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle("生成后立即编译 PDF（XeLaTeX → biber → XeLaTeX ×2）",
                   isOn: $model.options.compile)
            Toggle("清理编译中间文件（保留 .log）", isOn: $model.options.clean)
        }
    }

    // 「用 AI 处理参考文献」：勾上就是「任务书 + 按同一份规范在本机直连 AI 预跑」
    // （引擎侧自动带 --verify-refs；批 62 起这是普通初排唯一出任务书的入口）。
    // 批 62 文案与 Win 逐字对齐（Lead 口径）：标签点明「直连 API，会产生费用」。
    private var aiToggleRow: some View {
        Toggle("用 AI 处理参考文献（直连 API，会产生费用）",
               isOn: binding(.useAI, \.useAI))
    }

    private var sourcePdfToggle: some View {
        Toggle("导出原稿 PDF（供矢量图形人工截图）",
               isOn: $model.options.sourcePdf)
    }

    /// 勾「用 AI 处理参考文献」后才出现的两个附加开关（保留原 bib / 全文 AI 核查）。
    @ViewBuilder private var aiExtraTogglesSection: some View {
        if model.options.useAI {
            // 只在 AI 勾着时可见：没勾 AI 就没有合并通道，这个开关无从谈起。
            keepOriginalBibRow
            keepOriginalBibHint
            // ---------- 第 60 批：全文 AI 核查（直连 API，会产生费用；默认不勾） ----------
            // 默认不勾 → 一个参数都不加（默认 argv 与批 59 逐字节相同）。
            llmFulltextRow
            llmFulltextHint
        }
    }

    private var keepOriginalBibRow: some View {
        Toggle("保留原 reference.bib（不并入 AI 结果）",
               isOn: binding(.keepOriginalBib, \.keepOriginalBib))
    }

    private var keepOriginalBibHint: some View {
        Text(Self.keepBibHintText)
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.leading, 18)
    }

    private var llmFulltextRow: some View {
        Toggle("全文 AI 核查（直连 API，会产生费用）",
               isOn: $model.options.llmFulltext)
    }

    private var llmFulltextHint: some View {
        Text(Self.llmFulltextHintText)
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.leading, 18)
    }


    /// 「高级选项」整行按钮（批 63 B1：点文字/整行都能开合；状态仍是 model.showAdvanced）。
    var advancedToggleButton: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.15)) { model.showAdvanced.toggle() }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "chevron.right")
                    .rotationEffect(.degrees(model.showAdvanced ? 90 : 0))
                Text("高级选项")
                Spacer()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// 高级选项展开后的全部内容。
    var advancedOptionsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            advancedPathsSection
            advancedNamingSection
            advancedPublicationSection
            advancedMetadataSection
        }
        .padding(.top, 8)
    }

    /// 高级：覆盖开关 + 三个附件/元数据路径。
    private var advancedPathsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Toggle("覆盖已存在的输出目录（--force）",
                   isOn: $model.options.force)
                .toggleStyle(.checkbox)
            Text(Self.forceHintText)
                .font(.caption)
                .foregroundStyle(.secondary)
            LabeledPathRow(
                title: "参考文献附件",
                placeholder: "留空则自动识别（全库 37% 的稿件把文献单独放一个文件）",
                path: $model.options.refs,
                kind: .file,
                logStore: log
            )
            LabeledPathRow(
                title: "已有的 .bib",
                placeholder: "指定后不再自动生成",
                path: $model.options.bib,
                kind: .file,
                logStore: log
            )
        }
    }

    /// 高级：输出名 / 第一作者 / 语种。
    private var advancedNamingSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            outputNameRow
            sourceAuthorRow
            languageRow
        }
    }

    private var outputNameRow: some View {
        HStack(spacing: 12) {
            Text("输出名")
            TextField("留空则按第一作者自动命名（如 Li_Lei）",
                      text: $model.options.name)
                .textFieldStyle(.roundedBorder)
        }
    }

    private var sourceAuthorRow: some View {
        HStack(spacing: 12) {
            Text("第一作者")
            TextField("留空则从源文件提取",
                      text: $model.options.author)
                .textFieldStyle(.roundedBorder)
        }
    }

    private var languageRow: some View {
        HStack(spacing: 12) {
            Text("语种")
            languagePicker
        }
    }

    private var languagePicker: some View {
        Picker("", selection: $model.options.lang) {
            Text("自动判断").tag("")
            Text("中文（SLCN-MIX.sty）").tag("zh")
            Text("英文（SLEN.sty）").tag("en")
        }
        .labelsHidden()
        .frame(width: 240)
    }

    /// 高级：出版信息（卷/期/年、起止页/收稿/责编、基金项目）。
    private var advancedPublicationSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Divider().padding(.vertical, 2)
            Text("出版信息（选填；填了 PDF 页眉/页码才是最终形态）")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack(spacing: 12) {
                Text("卷 / 期 / 年")
                TextField("19", text: $model.options.vol)
                    .textFieldStyle(.roundedBorder).frame(width: 60)
                TextField("5", text: $model.options.issue)
                    .textFieldStyle(.roundedBorder).frame(width: 60)
                TextField("2026", text: $model.options.pubYear)
                    .textFieldStyle(.roundedBorder).frame(width: 70)
            }
            pagesAndEditorRow
            HStack(spacing: 12) {
                Text("基金项目")
                TextField("选填，如：国家社会科学基金项目“…”（23BZX113）。",
                          text: $model.options.grants)
                    .textFieldStyle(.roundedBorder)
            }
        }
    }

    /// 起止页 / 收稿日期 / 责编（含自定义名单与默认值按钮）。
    private var pagesAndEditorRow: some View {
        HStack(spacing: 12) {
            Text("起止页")
            TextField("137-152", text: $model.options.pages)
                .textFieldStyle(.roundedBorder).frame(width: 110)
            Text("收稿日期")
            TextField("2025-09-19", text: $model.options.received)
                .textFieldStyle(.roundedBorder).frame(width: 110)
            editorControls
        }
    }

    /// 责编控件（批63b R2：抽成独立小 View `EditorControlsRow`，避免大 ViewBuilder
    /// 上下文里的类型检查开销；行为/布局/文案不变）。
    private var editorControls: some View {
        EditorControlsRow(editors: editors, selection: $model.options.editor)
    }

    /// 高级：元数据入口（B-017：普通=表单 / 两段式=表单+折叠内联兜底）+ 引用环境阈值。
    private var advancedMetadataSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Divider().padding(.vertical, 2)
            // 多份补充信息（B-223）仍只在普通转换显示：两段式沿用既有口径（忽略，见
            // ConvertTab.applyMetadataFileVerdict），不给界面制造「能传」的错觉。
            if MetadataForm.entryMode(scanOnly: false, coop: model.options.coop) == .formOnly {
                metadataSourcesSection
            }
            metadataEntrySection
            Divider().padding(.vertical, 2)
            quoteThresholdRow
        }
    }

    /// B-017/B-287：**单一元数据入口**。路由判据只有一份——
    /// `MetadataForm.entryMode(scanOnly:coop:)`（纯逻辑，`tools/ai_settings_test` 直接断言）：
    ///   · 普通转换 → 只显示「填写元数据…」表单（旧 7 内联字段不渲染）；
    ///   · 两段式第一段 → 表单是主入口；旧 7 内联字段折叠作兜底（表单填了的字段优先）。
    @ViewBuilder private var metadataEntrySection: some View {
        if MetadataForm.entryMode(scanOnly: false, coop: model.options.coop)
            == .formWithInlineFallback {
            metadataFormSection
            Text(Self.metadataFormCoopHintText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            DisclosureGroup(Self.inlineOverrideHeaderText) {
                inlineOverrideSection
            }
            .font(.caption)
        } else {
            metadataFormSection
        }
    }

    /// 高级：引用环境阈值（原与元数据同段；B-017 拆成小 View 保持表达式小）。
    private var quoteThresholdRow: some View {
        HStack(spacing: 12) {
            Text("引用环境阈值")
            TextField("105", value: $model.options.quoteThreshold, format: .number)
                .textFieldStyle(.roundedBorder)
                .frame(width: 80)
            Text("字符（超过此长度提升为引用环境）")
                .foregroundStyle(.secondary)
        }
    }

    /// 旧「正文信息覆盖」7 个内联字段（B-017 起只作**两段式兜底**显示；普通转换不渲染）。
    /// 表单与内联不再同时可编辑；运行前 GUI 仍会按字段清零（防御旧调用方/旧状态双源）。
    private var inlineOverrideSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            // ---------- 正文信息手工覆盖（两段式兜底输入路径） ----------
            // 引擎按版面启发式解析题名/作者/摘要，总有解析不出来或
            // 解析错的稿子。这里是两段式下的**折叠兜底**：填了就覆盖自动解析结果
            // （表单填过的字段运行前会被清零，所以表单优先）。
            // 全部留空 = 不覆盖，与旧版行为一致。
            HStack(spacing: 12) {
                Text("中文题名")
                TextField("留空则用自动解析结果", text: $model.options.titleCn)
                    .textFieldStyle(.roundedBorder)
            }
            HStack(spacing: 12) {
                Text("第一作者")
                TextField("留空则用自动解析结果",
                          text: $model.options.authorCn)
                    .textFieldStyle(.roundedBorder).frame(width: 180)
                Text("单位")
                TextField("如：中山大学逻辑与认知研究所",
                          text: $model.options.affiliationCn)
                    .textFieldStyle(.roundedBorder)
            }
            HStack(spacing: 12) {
                Text("邮箱")
                TextField("如：someone@example.com", text: $model.options.email)
                    .textFieldStyle(.roundedBorder).frame(width: 240)
            }
            HStack(spacing: 12) {
                Text("中文摘要")
                TextField("留空则用自动解析结果", text: $model.options.abstractCn)
                    .textFieldStyle(.roundedBorder)
            }
            HStack(spacing: 12) {
                Text("英文题名")
                TextField("留空则用自动解析结果", text: $model.options.titleEn)
                    .textFieldStyle(.roundedBorder)
            }
            HStack(spacing: 12) {
                Text("英文摘要")
                TextField("留空则用自动解析结果", text: $model.options.abstractEn)
                    .textFieldStyle(.roundedBorder)
            }
            Text(Self.inlineOverrideWarningText)
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        // B-017/B-287：本 section 只在两段式的折叠兜底里渲染；两段式第一段会下传表单，
        // 但同一字段的表单值在运行前清零内联值 → 这里不按「表单有值」置灰。
    }

    // MARK: - B-223：元数据 / 补充信息（多份，可选；只补空、表单最高优先）
    /// 多份补充源（.docx / .tex / .json）：顺序 = 合并顺序（先到先得）；只补正文空值。
    @ViewBuilder private var metadataSourcesSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(Self.metadataSourcesTitleText).font(.caption).foregroundStyle(.secondary)
                Button("添加…") { addMetadataSource() }
                Button("预览…") { previewMetadataSources() }
                    .disabled(model.options.source.isEmpty || model.running)
            }
            Text(model.metadataSources.isEmpty
                 ? Self.metadataFilePlaceholder(for: model.options.source)
                 : Self.metadataSourcesHintText)
                .font(.caption2).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(Array(model.metadataSources.enumerated()), id: \.offset) { index, path in
                HStack(spacing: 8) {
                    Text("\(index + 1). \((path as NSString).lastPathComponent)")
                        .font(.caption).lineLimit(1).help(path)
                    Button("移除") {
                        if index < model.metadataSources.count {
                            model.metadataSources.remove(at: index)
                        }
                    }.buttonStyle(.borderless)
                }
            }
        }
    }

    private func addMetadataSource() {   // 多选入口 = 多次单选累加；同一路径不重复加
        guard let p = PanelKit.pickFile(allowedExtensions: ["docx", "tex", "json"]) else {
            return
        }
        if case .ignored(let why) = ConvertFeature.metadataSourceVerdict(
            path: p, source: model.options.source) {
            log.append("元数据 / 补充信息：\(why)。\n")
            return
        }
        guard !model.metadataSources.contains(p) else { return }
        model.metadataSources.append(p)
    }

    /// 只读预览（引擎 `--metadata-preview-out`；不建产物目录 / 不写产物 / 不写交接单）。
    private func previewMetadataSources() {
        var o = model.options; o.metadataSources = model.metadataSources
        var formTemp: String? = nil
        if model.metadataForm.hasAnyValue, let json = model.metadataForm.jsonString() {
            let p = MetadataForm.tempFilePath(stem: o.source)
            if (try? json.write(toFile: p, atomically: true, encoding: .utf8)) != nil {
                o.metadataJSON = p
                formTemp = p
            }
        }
        defer { if let p = formTemp { MetadataForm.removeTemporaryFile(p) } }
        log.append("元数据预览：读取中（只读，不写任何产物）…\n")
        if let payload = ConvertFeature.previewMetadata(o, knobs: settings.aiEngineKnobs,
                                                        log: Logger(store: log)) {
            model.metadataPreview = payload; model.showMetadataPreview = true
            log.append("元数据预览：\(payload.rows.count) 个字段；未写任何产物。\n")
        } else {
            log.append("元数据预览失败（见上方日志）；本次未写任何文件。\n")
        }
    }

    /// 批63b：「元数据文件」占位文案按**源稿通道**动态变（两通道既有能力不同，不能一刀切）。
    ///
    ///   · LaTeX 源：`.doc/.docx` 自动识别已取消（用户拍板）→ 引导用表单或同目录 `.tex`；
    ///   · Word 源：`.docx` 是既有「英文元数据第二源」（第 76 条，行为不变）→ 引导显式指定。
    private static func metadataFilePlaceholder(for source: String) -> String {
        let lowered = source.lowercased()
        if lowered.hasSuffix(".doc") || lowered.hasSuffix(".docx") {
            return Self.metadataPlaceholderWordText
        }
        return Self.metadataPlaceholderLatexText
    }

    /// 普通转换 / 两段式第一段：初排页**唯一**的人工元数据入口（表单在 sheet 里；
    /// 这里放入口 + 摘要 + 常驻上限说明）。B-017：旧 7 内联字段不再常驻渲染。
    private var metadataFormSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(Self.metadataFormTitleText)
                .font(.caption)
                .foregroundStyle(.secondary)
            metadataFormEntryRow
            Text(Self.metadataFormLimitText)
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            if model.metadataForm.hasAnyValue {
                Text(Self.metadataFormPriorityHintText)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder private var metadataFormEntryRow: some View {
        HStack(spacing: 12) {
            metadataFormButton
            if model.metadataForm.hasAnyValue {
                Text(model.metadataForm.summaryLine())
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button("清空") { model.metadataForm = MetadataForm() }
            }
        }
    }

    private var metadataFormButton: some View {
        Button {
            // B-111：打开表单时**单向预填**——仅当表单「第一作者中文姓名」为空、且高级选项
            // 「第一作者」(`options.author`) 已填时，用其预填；不覆盖已填值。
            // 只改内存里的表单会话态（不写 JSON、不改 argv），`Options.author` 语义不变。
            model.metadataForm = MetadataForm.prefillFirstAuthorCN(
                from: model.options.author,
                current: model.metadataForm)
            model.showMetadataForm = true
        } label: {
            Label("填写元数据…", systemImage: "square.and.pencil")
        }
        // B-017/B-287：表单是普通转换与两段式第一段的唯一主入口；
        // 两段式的旧 7 内联字段只作折叠兜底。
    }
}
