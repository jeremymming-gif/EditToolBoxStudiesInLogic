import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// 「文献回读校验」页的界面状态。
/// 与 ConvertModel 同理，用 ObservableObject 而不是 @State ——
/// 运行在工作线程里，需要从逃逸闭包回写 running / reportPath。
final class CheckBibModel: ObservableObject {

    /// 「交回的 bib」怎么给：选文件（`--returned`）或粘贴文本（`--returned-stdin`）。
    /// 两种模式交给引擎的**判据完全相同**（体检/并回都由 checkbib.py 决定），
    /// 这里只是「文本从哪儿来」的界面开关。
    enum ReturnedMode: String, CaseIterable, Identifiable {
        case file = "选择文件"
        case paste = "粘贴文本"
        var id: String { rawValue }
    }

    @Published var returnedMode: ReturnedMode = .file

    @Published var returned = ""      // 编辑交回的 .bib 路径（文件模式；必填）
    @Published var returnedText = ""  // 粘贴的 bib 正文（粘贴模式；必填）

    /// 粘贴预览行：`20 条：Ayer1946, Burge1984, …`，或一句失败说明。
    /// 由 `checkbib.py --list-keys` **现算**（那个模式保证不写任何文件）。
    @Published var previewLine = ""
    /// 预览是否正在跑（背景线程）。用来显示「正在识别…」，不挡输入。
    @Published var previewing = false
    /// 预览的世代号：编辑连续改动时只认**最后一次**的结果，避免旧结果回来后覆盖新的。
    /// （不做防抖：预览很便宜，但结果乱序回来会显示错的条数。）
    var previewGeneration = 0

    @Published var original = ""      // 任务书前的原件 .bib（可选；**并回时它是目标**）
    @Published var tex = ""           // 正文 .tex（可选）
    /// 报告落点（可选）：留空 = 自动（交回 .bib 或原件同目录）；填**已存在的目录**则报告放进去；
    /// 填文件路径则报告写到该文件。2026-10-17 第十五批补上（与 Windows 的 --out 输入对等）。
    @Published var reportOut = ""
    @Published var running = false
    /// 体检 / 并回完成后的报告路径（成功才有值，「打开报告」按钮用它）
    @Published var reportPath: String? = nil
    /// 并回成功后记住「这一篇」的稿件目录（「重新编译该篇」按钮用它）。
    /// nil = 还没并回过（或这次没改动），此时不显示重新编译按钮。
    @Published var mergedManuscriptDir: String? = nil
    /// U3：本次运行的取消句柄（体检 / 并回 / 重新编译共用一个；运行中主按钮变成「取消」）
    var cancel: RunCancel? = nil

    // ── 第 132 条：把产物 bib 入藏到引用库 ──
    /// 是否正在入藏（与体检/并回/编译互斥：这些动作都不该并发改同一篇稿子的相关文件）
    @Published var libraryImporting = false
    /// 上一次入藏的结果话术（✅ 成功 / ⚠ 失败）
    @Published var libraryMessage = ""
}

/// 「文献回读校验」页：编辑拿《文献核实任务书》改完参考文献交回 `.bib` 后，
/// 这一页对着任务书前的原件做一次**离线体检**（语法 / 编辑部规范 /
/// 引用键闭合 / 逐字段对比），并把报告原样贴进下方运行日志。
///
/// 交回的 bib 有两种给法：**选择文件**，或**粘贴文本**（走引擎的标准输入，
/// 不落中间文件 —— 编辑常常是把 AI 的回复直接粘进来）。
struct CheckBibView: View {

    @ObservedObject var log: LogStore
    @StateObject private var model = CheckBibModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {

                Text("""
                使用说明：编辑拿《文献核实任务书》改完参考文献后交回 .bib，这一页做**离线体检**——
                语法、编辑部规范、引用键闭合，以及与任务书前原件的**逐字段对比**
                （改 key 会让正文引用悬空，是最危险的一类）。

                只查自洽性，**不联网**核实真伪（真伪由编辑核实）。

                交回的 bib 可以**选文件**，也可以**粘贴文本**（AI 的回复直接粘进来即可）：
                粘贴模式走引擎的标准输入，**不落任何中间文件**；粘贴后会先跑一次「识别」，
                把「识别到几条、键是什么」显示出来，再决定要不要体检/并回。

                体检完可以用「**并回**」把交回的 .bib 并进产物：目标就是「任务书前的原件 .bib」
                那一栏（产物里的 reference.bib）。并回的判据与「初排转换」的自动并入**同一份**
                （key 不可改、不增删条目、没交回的条目不碰），并回前会把原文件备份成
                「原文件.ai前」，并回后可以「重新编译该篇」。
                ⚠ 本页只做**离线**体检（不联网、不调任何模型），它也不能判断文献真伪；
                AI 交回的修订**须人工核对**（联网核实请在编辑自己的联网 AI 里做，或让「初排转换」
                的 AI 步骤在你配置的、具备检索能力的端点上跑）。
                """.plainMarkdown)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

                Divider()

                HStack(spacing: 10) {
                    Text("编辑交回的 .bib（必填）")
                        .font(.body)
                    Picker("", selection: $model.returnedMode) {
                        ForEach(CheckBibModel.ReturnedMode.allCases) { m in
                            Text(m.rawValue).tag(m)
                        }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(width: 200)
                    .disabled(model.running)
                    Spacer()
                }

                if model.returnedMode == .file {
                    // 文件模式：与从前**一模一样**的那一栏（拖入 / 浏览 / 手打都行）
                    LabeledPathRow(
                        title: "交回的 .bib 文件",
                        placeholder: "编辑改完交回来的 .bib，例如 …/reference_改回.bib",
                        path: $model.returned,
                        kind: .file,
                        logStore: log
                    )
                    .disabled(model.running)
                } else {
                    pasteEditor
                }

                LabeledPathRow(
                    title: "任务书前的原件 .bib（可选）",
                    placeholder: "留空则不做字段级对比（key 被改名、字段被删就查不出来）",
                    path: $model.original,
                    kind: .file,
                    logStore: log
                )
                .disabled(model.running)

                LabeledPathRow(
                    title: "正文 .tex（可选）",
                    placeholder: "留空则不查引用键闭合（悬空引用 / 从未被引用）",
                    path: $model.tex,
                    kind: .file,
                    logStore: log
                )
                .disabled(model.running)

                // 报告落点（可选，第十五批）：与 Windows 侧 U7 的三个可选输入对齐。
                // 留空 = 与从前一字未改（自动落在交回 .bib / 原件同目录）；
                // 填**已存在的目录** = 报告按默认文件名放进该目录；填文件路径 = 写到那个文件。
                HStack(spacing: 8) {
                    Text("报告输出（可选）")
                        .font(.body)
                        .frame(width: 148, alignment: .leading)
                    TextField("留空 = 自动（<名字>_回读校验报告.md）；也可填一个目录或 .md 路径",
                              text: $model.reportOut)
                        .textFieldStyle(.roundedBorder)
                        .disabled(model.running)
                }
                Text("填**已存在的目录** = 报告按默认文件名放进该目录；填文件路径 = 写到该文件；"
                     + "留空 = 与从前一样（自动落在交回 .bib / 产物目录旁边）。")
                    .font(.caption)
                    .foregroundStyle(.tertiary)

                if model.returnedMode == .file {
                    Text("三个路径都可以直接从访达拖进来。"
                         + "选好「交回的 .bib」后，同目录下能找到的另一个 .bib、"
                         + "以及同目录下唯一的那份 .tex，会自动填进下面两栏（不对就直接改）。")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                } else {
                    Text("粘贴模式没有「交回文件」可猜：并回仍以「任务书前的原件 .bib」那一栏为目标"
                         + "（所以并回必须先选它），正文 .tex 会从原件的同目录里找唯一的那份自动填。")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }

                Divider()

                HStack(spacing: 12) {
                    // U3：运行中主按钮变成「取消」（体检 / 并回 / 重新编译三者共用；取消 =
                    // 杀整棵进程树，见 `RunCancel`）。短任务可能来不及按就已经结束，正常。
                    if model.running {
                        Button {
                            model.cancel?.cancel()
                        } label: {
                            Label("取消", systemImage: "stop.circle")
                        }
                    } else {
                        Button("开始体检") { execute() }
                            .keyboardShortcut("r", modifiers: .command)
                            .buttonStyle(.borderedProminent)
                            .disabled(!hasReturnedSource)
                    }

                    if model.running {
                        ProgressView().controlSize(.small)
                        Text("正在运行，请查看下方运行日志…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    if let report = model.reportPath {
                        Button("打开报告") {
                            NSWorkspace.shared.open(URL(fileURLWithPath: report))
                        }
                    }

                    // 并回：体检成功（退出码 0/1，即 reportPath 有值）之后才出现。
                    // 目标 = 「任务书前的原件 .bib」那一栏；没填就禁用（并在体检的日志里
                    // 已经说过为什么——不许静默地让按钮灰着）。
                    if model.reportPath != nil {
                        Button("并回") { mergeBack() }
                            .disabled(model.running
                                      || model.original.trimmingCharacters(in: .whitespaces).isEmpty)
                            .help("把「编辑交回的 .bib」并回「任务书前的原件 .bib」（产物里的 "
                                  + "reference.bib）：原文件会先备份为 <原文件>.ai前")
                    }

                    // 并回成功之后才出现：重新编译这一篇（稿件目录 = 目标 bib 所在目录）。
                    if model.mergedManuscriptDir != nil {
                        Button("重新编译该篇") { recompile() }
                            .disabled(model.running)
                            .help("对该稿件目录跑 XeLaTeX → biber → XeLaTeX ×2，"
                                  + "让 PDF 反映并回后的 reference.bib")
                    }

                    // 第 132 条：把产物 bib 入藏到编辑部引用库（复用「入藏」页的 Feature）。
                    // 只用「任务书前的原件 .bib」那一栏（= 产物 reference.bib）：
                    // 编辑交回的 bib 是待核对的，不能当成品入 verified。
                    if productBibPath != nil {
                        Button("入藏到引用库") { importToLibrary() }
                            .disabled(model.running || model.libraryImporting)
                            .help("把「任务书前的原件 .bib」（产物 reference.bib）按往期成品 bib"
                                  + "入藏到引用库（verified=true）；来源期/篇从路径推断。"
                                  + "建议在并回并确认后再点。")
                    }
                    if model.libraryImporting {
                        ProgressView().controlSize(.small)
                        Text("正在入藏…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()
                }

                if !model.libraryMessage.isEmpty {
                    Text(model.libraryMessage)
                        .font(.caption)
                        .foregroundStyle(model.libraryMessage.hasPrefix("✅") ? Color.green : Color.red)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        // 案例 126：把文件拖到本页**任意空白处**也当作「交回的 .bib」。
        // 行级目标（三个路径栏）由 LabeledPathRow / DroppablePathField 负责；
        // 这里兜住页面空白区域，避免拖到页面上但没对准输入框时毫无反应。
        .onDrop(of: [UTType.fileURL], isTargeted: nil) { providers in
            guard let provider = providers.first else { return false }
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                var url: URL?
                if let data = item as? Data, let s = String(data: data, encoding: .utf8) {
                    url = URL(string: s)
                } else if let u = item as? URL {
                    url = u
                }
                guard let u = url else { return }
                DispatchQueue.main.async {
                    PathDrop.receive(u, kind: .file, label: "本页空白处", log: Logger(store: log),
                                     setPath: { model.returned = $0 },
                                     onAccept: { autofill() })
                }
            }
            return true
        }
        // 选好「交回的 .bib」（浏览、拖入、手打都算）后尽力而为地猜另两栏
        .onChange(of: model.returned) { _ in
            if model.returnedMode == .file { autofill() }
        }
        // 粘贴模式：原件一栏选好后，正文 .tex 从原件同目录里找唯一那份
        .onChange(of: model.original) { _ in
            if model.returnedMode == .paste { autofillTeXFromOriginal() }
        }
        // 粘贴内容变了就跑一次「识别」（简单直接，不做防抖；过期结果按世代号丢掉）
        .onChange(of: model.returnedText) { _ in
            if model.returnedMode == .paste { refreshPreview() }
        }
        // 换模式：清掉上一次的预览与运行结论（换来源后结论不再对应当前输入）
        .onChange(of: model.returnedMode) { mode in
            model.previewGeneration += 1
            model.previewLine = ""
            model.previewing = false
            model.reportPath = nil
            model.mergedManuscriptDir = nil
            if mode == .paste {
                refreshPreview()
                // 可能「原件」在切模式之前就选好了：这时补一次正文自动填充
                autofillTeXFromOriginal()
            }
        }
    }

    // MARK: 粘贴模式的界面块

    @ViewBuilder
    private var pasteEditor: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Text("交回的 .bib 文本")
                    .font(.body)
                Spacer()
                Button("从剪贴板粘贴") { pasteFromClipboard() }
                    .disabled(model.running)
                    .help("把剪贴板里的文本整段替换进来（AI 回复里连说明文字一起粘也没关系，"
                          + "下面的「识别」会告诉你解析出几条）")
            }
            TextEditor(text: $model.returnedText)
                .font(.system(.caption, design: .monospaced))
                .frame(minHeight: 140)
                .padding(4)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.4)))
                .disabled(model.running)

            HStack(spacing: 8) {
                if model.previewing {
                    ProgressView().controlSize(.small)
                    Text("正在识别…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if model.previewLine.isEmpty {
                    Text("粘贴后这里显示「识别到几条、键是什么」（不会写任何文件）。")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                } else {
                    Text("识别：" + model.previewLine)
                        .font(.caption)
                        .foregroundStyle(model.previewLine.hasPrefix("0 条") ? Color.orange : Color.secondary)
                        .textSelection(.enabled)
                }
                Spacer()
                if !model.returnedText.isEmpty {
                    Text("\(model.returnedText.count) 字符")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
        }
    }

    // MARK: 状态与动作

    /// 「开始体检」是否可点：按当前模式看对应的那一栏有没有内容。
    private var hasReturnedSource: Bool {
        switch model.returnedMode {
        case .file:
            return !model.returned.trimmingCharacters(in: .whitespaces).isEmpty
        case .paste:
            return !model.returnedText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    /// 当前模式的「交回来源」。
    private var source: CheckBibFeature.ReturnedSource {
        model.returnedMode == .file ? .file(model.returned) : .paste(model.returnedText)
    }

    /// 从系统剪贴板整段替换粘贴框内容（`NSPasteboard.general.string(forType: .string)`）。
    private func pasteFromClipboard() {
        guard let s = NSPasteboard.general.string(forType: .string) else {
            Logger(store: log)("（剪贴板里没有文本，什么都没粘贴。）\n")
            return
        }
        model.returnedText = s
        // onChange(of: returnedText) 会触发识别；这里不用再手动跑一次
    }

    /// 跑一次「识别」（`checkbib.py --list-keys`），只更新预览行、**不写任何文件**。
    private func refreshPreview() {
        let text = model.returnedText
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            model.previewGeneration += 1
            model.previewLine = ""
            model.previewing = false
            return
        }
        model.previewGeneration += 1
        let gen = model.previewGeneration
        let m = model
        m.previewing = true
        DispatchQueue.global(qos: .userInitiated).async {
            let line = CheckBibFeature.previewPasted(text)
            DispatchQueue.main.async {
                // 编辑又改过 / 换了模式 → 这次的结果已经过期，丢掉
                guard gen == m.previewGeneration else { return }
                m.previewLine = line
                m.previewing = false
            }
        }
    }

    /// 尽力而为的自动填充（**只在目标栏为空时填**，不覆盖编辑已经手填/拖入的值）；
    /// 猜了就在日志里说一句 —— 编辑要能看出这两栏是程序填的，不对好直接改。
    /// 猜不出来（没有别的 .bib / 不止一个 .tex）就留空，不硬猜。
    private func autofill() {
        let bib = PathKit.normalize(model.returned)
        guard PathKit.exists(bib), !PathKit.isDirectory(bib) else { return }

        let dir = (bib as NSString).deletingLastPathComponent
        let me = (bib as NSString).lastPathComponent
        let names = ((try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []).sorted()
        let logger = Logger(store: log, startedAt: Date())

        if model.original.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let others = names.filter { $0.hasSuffix(".bib") && $0 != me }
            if let pick = others.first {   // 没有别的 .bib 就别猜
                model.original = PathKit.join(dir, pick)
                let note = others.count > 1
                    ? "（同目录有 \(others.count) 个 .bib，先按文件名把 \(pick) 填进「原件」，不对请直接改）"
                    : "（同目录的 \(pick) 已填进「原件」，不对请直接改）"
                logger(note + "\n")
            }
        }

        if model.tex.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let texes = names.filter { $0.hasSuffix(".tex") }
            if texes.count == 1 {          // 恰好只有一个才填：多份 tex 猜不出哪份是正文
                model.tex = PathKit.join(dir, texes[0])
                logger("（同目录唯一的 \(texes[0]) 已填进「正文」，不对请直接改）\n")
            }
        }
    }

    /// 粘贴模式的正文自动填充：粘贴没有「交回文件」可猜，所以从**原件**的同目录里找
    /// 唯一的那份 `.tex`（同样只在「正文」为空时填、不硬猜）。
    private func autofillTeXFromOriginal() {
        guard !model.original.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        guard model.tex.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let bib = PathKit.normalize(model.original)
        guard PathKit.exists(bib), !PathKit.isDirectory(bib) else { return }
        let dir = (bib as NSString).deletingLastPathComponent
        let names = ((try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []).sorted()
        let texes = names.filter { $0.hasSuffix(".tex") }
        guard texes.count == 1 else { return }
        model.tex = PathKit.join(dir, texes[0])
        Logger(store: log)("（从原件同目录填入「正文」：\(texes[0])，不对请直接改）\n")
    }

    private func execute() {
        let m = model
        // U3：每次运行一个取消句柄；`Shell.run` 把 python 子进程注册进 logger.cancel
        let cancel = RunCancel()
        m.cancel = cancel
        let logger = Logger(store: log, startedAt: Date(), cancel: cancel)
        // 来源与路径在主线程取好再进后台：跑的过程中界面已被禁用，不会变
        let src = source
        let original = m.original
        let tex = m.tex
        let out = m.reportOut
        m.running = true
        m.reportPath = nil
        // 重新体检 = 重新开始：上一次并回留下的「重新编译该篇」不再对应当前结论。
        m.mergedManuscriptDir = nil
        DispatchQueue.global(qos: .userInitiated).async {
            let report = CheckBibFeature.run(source: src, original: original,
                                             tex: tex, out: out, log: logger)
            DispatchQueue.main.async {
                m.reportPath = report
                m.running = false
            }
        }
    }

    /// 「并回」：把交回的 .bib 并回产物（目标 = 「原件」那一栏）。
    ///
    /// 目标 bib 所在目录就是「这一篇」的稿件目录 —— 并回成功后把它记下来，
    /// 「重新编译该篇」按钮据此定位（与并回的是同一篇，不会指错稿子）。
    private func mergeBack() {
        let m = model
        let cancel = RunCancel()
        m.cancel = cancel
        let logger = Logger(store: log, startedAt: Date(), cancel: cancel)
        let src = source
        let target = m.original
        let tex = m.tex
        m.running = true
        DispatchQueue.global(qos: .userInitiated).async {
            let report = CheckBibFeature.mergeBack(source: src, target: target,
                                                   tex: tex, log: logger)
            DispatchQueue.main.async {
                // 只有真的并进去了才有新报告。未改动 / 失败时**保留**上一份体检报告，
                // 别把「打开报告」也一起抹掉（编辑还要照它处理问题）。
                if let report {
                    m.reportPath = report
                    m.mergedManuscriptDir = (PathKit.normalize(target) as NSString).deletingLastPathComponent
                }
                m.running = false
            }
        }
    }

    // MARK: - 入藏到引用库（第 132 条）

    /// 「入藏到引用库」的来源：产物里的 `reference.bib`（界面「任务书前的原件 .bib」一栏）。
    private var productBibPath: String? {
        let text = model.original.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        let path = PathKit.normalize(text)
        guard PathKit.exists(path), !PathKit.isDirectory(path) else { return nil }
        return path
    }

    /// 把产物 bib 作为「往期成品 bib」入藏（`import-bib --verified --json`）。
    /// 判据/参数拼装全在 `BibDbFeature`（与「引用库 → 入藏」页同一份），这里只负责
    /// 「取路径 → 推断期/篇 → 后台跑 → 写运行日志 → 回填状态」。
    private func importToLibrary() {
        guard let bib = productBibPath, !model.libraryImporting else { return }
        let inferred = BibDbFeature.inferIssueAndSlug(from: bib)
        let logger = Logger(store: log, startedAt: Date())
        let model = self.model
        model.libraryImporting = true
        model.libraryMessage = ""
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.importBib(bibPath: bib, issue: inferred.issue,
                                                 slug: inferred.slug, dryRun: false)
            DispatchQueue.main.async {
                model.libraryImporting = false
                if let error = outcome.error {
                    model.libraryMessage = "⚠ 入藏失败：" + error
                    logger("❌ 入藏到引用库失败：\(error)\n")
                    return
                }
                let summary = outcome.summary?.line ?? "（引擎没有返回计数）"
                model.libraryMessage = "✅ 已入藏：" + summary
                logger("引用库入藏（产物 bib → 已核实）：\(bib)\n")
                let whereText = [inferred.issue, inferred.slug]
                    .filter { !$0.isEmpty }.joined(separator: " / ")
                logger("  来源  \(whereText.isEmpty ? "（未从路径推断出期/篇）" : whereText)\n")
                logger("  " + summary + "\n")
                if !outcome.dbExisted { logger("  （本次新建了引用库文件）\n") }
                logger("  （库变了：建议到「引用库 → 待裁决」页重新生成清单。）\n")
            }
        }
    }

    /// 「重新编译该篇」：对并回的那一篇跑 XeLaTeX → biber → XeLaTeX ×2。
    private func recompile() {
        guard let dir = model.mergedManuscriptDir else { return }
        let m = model
        // U3：四步 TeX 走 `Shell.capture(cancel:)`，所以这里同样要建取消句柄
        let cancel = RunCancel()
        m.cancel = cancel
        let logger = Logger(store: log, startedAt: Date(), cancel: cancel)
        m.running = true
        DispatchQueue.global(qos: .userInitiated).async {
            CheckBibFeature.recompile(manuscriptDir: dir, log: logger)
            DispatchQueue.main.async { m.running = false }
        }
    }
}
