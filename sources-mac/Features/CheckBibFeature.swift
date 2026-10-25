import Foundation

/// 「回读/比对报告查看器（高级：手动并回，离线）」—— 调用 Engine/checkbib.py 给外部改过 /
/// 交回的 `.bib` 做一次**离线体检**（批 62 入口收敛：本页不再是「生成任务书」的入口，
/// 任务书只由两段式第一段或 CLI `--verify-refs` 产生；本页能力一个不少）。
///
/// 为什么 shell 出去调 Python，而不是用 Swift 重写
/// ------------------------------------------------
/// 核对要用**编辑部规范本身**（`slcn/bibcheck.py` 的必填字段表、`slcn/bibnorm.py`
/// 的值规范化），以及生成《文献核实任务书》时的同一套解析。Swift 里再写一遍等于
/// 把同一份规则维护两份，必然走样。所以这里只做**界面 + 参数拼装 + 找路径**，
/// 真正的核对交给 Python。全程离线、不联网、不调模型（规则由 checkbib.py 保证）。
enum CheckBibFeature {

    /// 「交回的 bib」从哪儿来。两种来源**判据完全相同**（体检与并回都由引擎决定），
    /// 差别只在「怎么把文本递给 Python」：
    ///   · `.file` → `--returned <路径>`（与从前逐字节相同的路径，界面不动它）；
    ///   · `.paste` → `--returned-stdin`（文本走子进程的标准输入，**不落中间文件**）。
    /// 为什么要有粘贴模式：编辑常常把 AI 的回复直接粘进界面；先写一个临时 .bib 再删
    /// 既多一次落盘，又会让那个假文件名印进报告头与合并记录（事后对不上账）。
    enum ReturnedSource: Equatable {
        case file(String)     // 原始输入（未规范化；PathKit.normalize 在 run 里做）
        case paste(String)    // 粘贴框里的 bib 正文（原样，不 trim —— 只判空）

        var isPaste: Bool { if case .paste = self { return true }; return false }
    }

    /// 体检输入（对应 checkbib.py 的命令行参数）
    struct Options {
        var returned = ""   // --returned（文件模式；必填）
        /// true = 走 `--returned-stdin`（粘贴模式）：此时 `returned` 不参与拼参。
        var returnedStdin = false
        var original = ""   // --original（可选：留空则不做字段级对比）
        var tex = ""        // --tex（可选：留空则不查引用键闭合）
        var out = ""        // --out（必填：报告 md 落到哪里）
    }

    /// 找 checkbib.py。
    ///
    /// 引擎目录**不另写一套查找逻辑**：先看 app 包内（build.sh 会把 Engine/ 连同
    /// checkbib.py 一起拷进 Resources），否则顺着 `ConvertFeature.locateTool()`
    /// 定位到的 Engine/ 目录取同级文件（开发时把 app 放在仓库的  下即可命中）。
    static func locateTool() -> String? {
        let fm = FileManager.default

        if let res = Bundle.main.resourcePath {
            let p = res + "/Engine/checkbib.py"
            if fm.fileExists(atPath: p) { return p }
        }

        if let convert = ConvertFeature.locateTool() {
            let p = (convert as NSString).deletingLastPathComponent + "/checkbib.py"
            if fm.fileExists(atPath: p) { return p }
        }
        return nil
    }

    /// 拼命令行参数：**数组形式**，不做 shell 字符串插值。
    /// `--out` 一定传 —— 界面运行完要把报告读出来原样贴进运行日志。
    ///
    /// 粘贴模式换成 `--returned-stdin`（文本由 `Shell.run(stdin:)` 递给子进程）；
    /// 两种来源互斥是**引擎侧**的约定（argparse 互斥组），这里不重复实现一遍判据。
    static func arguments(_ o: Options) -> [String] {
        var a: [String] = o.returnedStdin ? ["--returned-stdin"] : ["--returned", o.returned]
        if !o.original.isEmpty { a += ["--original", o.original] }
        if !o.tex.isEmpty { a += ["--tex", o.tex] }
        a += ["--out", o.out]
        return a
    }

    /// 「粘贴预览」的 argv（纯函数：映射可以直接被 harness 断言）。
    /// `--list-keys` 只解析、**永不写文件**（引擎侧保证），所以可以放心在编辑每次改动后重跑。
    struct ListOptions {
        var returned = ""       // 文件模式（界面预览只用粘贴模式，这里留着做 CLI 等价）
        var returnedStdin = false
    }

    static func listArguments(_ o: ListOptions) -> [String] {
        var a: [String] = ["--list-keys"]
        a += o.returnedStdin ? ["--returned-stdin"] : ["--returned", o.returned]
        return a
    }

    /// 报告落盘位置：与交回的 .bib **同目录**、同基名（去扩展名）+ `_回读校验报告.md`
    static func reportPath(forReturned bibPath: String) -> String {
        let dir = (bibPath as NSString).deletingLastPathComponent
        let name = (bibPath as NSString).lastPathComponent
        let base = (name as NSString).deletingPathExtension
        return PathKit.join(dir, base + "_回读校验报告.md")
    }

    /// **粘贴模式**的体检报告落点：挨着「任务书前的原件 .bib」（= 产物目录），
    /// 同基名 + `_回读校验报告.md` —— 与文件模式下「报告挨着交回的 bib」是同一条产品行为
    /// （粘贴没有交回文件，所以改用它本来就要并回的那一份原件当锚点）。
    ///
    /// 原件没填时**绝不猜一个稿件路径**：退回系统临时目录，并返回一句要打进日志的说明。
    static func pasteReportPath(forOriginal rawOriginal: String) -> (path: String, note: String) {
        let o = rawOriginal.trimmingCharacters(in: .whitespacesAndNewlines)
        if o.isEmpty {
            let p = PathKit.join(FileManager.default.temporaryDirectory.path, "checkbib_粘贴_回读校验报告.md")
            return (p, "（粘贴模式且没填「原件 .bib」：报告落到系统临时目录 \(p)；"
                    + "要让它挨着产物，请在「任务书前的原件 .bib」一栏选择产物里的 reference.bib。）")
        }
        return (reportPath(forReturned: PathKit.normalize(o)), "")
    }

    /// 空白 → 空串（= 该参数不传）；否则整理成绝对路径。
    ///
    /// ⚠️ 不能直接对空串调 `PathKit.normalize`：它会把空路径补成「当前目录 + /」，
    /// 于是「没填」变成一个存在的路径，可选参数就变成必填了。
    private static func optionalPath(_ raw: String) -> String {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? "" : PathKit.normalize(t)
    }

    // MARK: - 并回（`checkbib.py --merge-into`）

    /// 「并回」参数（对应 `checkbib.py --merge-into`）。
    struct MergeOptions {
        var returned = ""   // --returned（编辑自己的 AI 改完交回的**整份** .bib）
        /// true = 走 `--returned-stdin`（粘贴模式），文本由 `Shell.run(stdin:)` 递进去。
        var returnedStdin = false
        var target = ""     // --merge-into（= 界面「任务书前的原件 .bib」那一栏）
        var tex = ""        // --tex（可选：合并后的报告仍查引用键闭合）
        var out = ""        // --out（合并后回读校验报告的落点）
    }

    /// 并回模式的 argv（纯函数：参数映射可以直接被 harness / 复查断言）。
    static func mergeArguments(_ o: MergeOptions) -> [String] {
        var a: [String] = o.returnedStdin ? ["--returned-stdin"] : ["--returned", o.returned]
        a += ["--merge-into", o.target]
        if !o.tex.isEmpty { a += ["--tex", o.tex] }
        if !o.out.isEmpty { a += ["--out", o.out] }
        return a
    }

    /// 合并后回读校验报告的落点：与**目标**同目录、同基名 + `_合并后回读校验报告.md`。
    ///
    /// 刻意**不复用** `reportPath(forReturned:)`：那是「交回文件」的体检报告，并回后再写
    /// 同一份会把它覆盖掉——编辑还要拿它对比「并回前长什么样」。
    static func mergedReportPath(forTarget target: String) -> String {
        let dir = (target as NSString).deletingLastPathComponent
        let name = (target as NSString).lastPathComponent
        let base = (name as NSString).deletingPathExtension
        return PathKit.join(dir, base + "_合并后回读校验报告.md")
    }

    /// 把「交回的 .bib」并回产物（`checkbib.py --merge-into`）。
    ///
    /// 退出码与体检**不同**：`0` = 未改动（一条都没通过安全校验，且一个文件都没写）；
    /// `1` = 已并入 ≥1 条。成功（0 / 1）返回**合并后回读校验报告**的路径（0 时没有报告，
    /// 返回 nil）；失败返回 nil。合并判据全在引擎侧（`slcn/aibib.py` 的四条硬保障 +
    /// 作者倒装 + 字段重排），这里只拼参数、找路径、把摘要与结论打进日志。
    ///
    /// 文件模式的老入口：原样转给统一的 `mergeBack(source:)`，**行为一字不变**。
    @discardableResult
    static func mergeBack(returned rawReturned: String, target rawTarget: String,
                          tex rawTeX: String, log: Logger) -> String? {
        mergeBack(source: .file(rawReturned), target: rawTarget, tex: rawTeX, log: log)
    }

    /// 统一的并回入口：交回的文本可以来自**文件**（`.file`）或**粘贴框**（`.paste`，走 stdin）。
    @discardableResult
    static func mergeBack(source: ReturnedSource, target rawTarget: String,
                          tex rawTeX: String, log: Logger) -> String? {
        let rawT = rawTarget.trimmingCharacters(in: .whitespacesAndNewlines)
        var returned = ""
        var pasted = ""
        switch source {
        case .file(let raw):
            let rawR = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !rawR.isEmpty else {
                log("请先选择编辑交回的 .bib 文件。\n")
                return nil
            }
            returned = PathKit.normalize(rawR)
        case .paste(let text):
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                log("请先在「粘贴文本」框里粘贴交回的 .bib 文本。\n")
                return nil
            }
            pasted = text
        }
        guard !rawT.isEmpty else {
            // 「原件」栏 = 产物里的 reference.bib（任务书前的那份）；没有它就不知道并回哪儿。
            log("要并回请先选择产物里的 reference.bib（即任务书前的那份原件）"
                + "——界面「任务书前的原件 .bib」那一栏就是并回的目标。\n")
            return nil
        }
        let target = PathKit.normalize(rawT)
        if !pasted.isEmpty {
            // 粘贴模式没有文件可查；文本非空已在上面判过。
        } else {
            guard PathKit.exists(returned), !PathKit.isDirectory(returned) else {
                log("读不到交回的 .bib：\(returned)（文件不存在，或这是个文件夹）\n")
                return nil
            }
        }
        guard PathKit.exists(target), !PathKit.isDirectory(target) else {
            log("读不到并回目标：\(target)（文件不存在，或这是个文件夹）\n")
            return nil
        }
        guard let tool = locateTool() else {
            log("未找到文献回读校验脚本 Engine/checkbib.py（并回用的是同一个脚本）。\n")
            return nil
        }
        let engineDir = (tool as NSString).deletingLastPathComponent
        guard let py = ConvertFeature.locatePython(toolDir: engineDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return nil
        }
        let isPaste = !pasted.isEmpty
        let o = MergeOptions(returned: isPaste ? "" : returned,
                             returnedStdin: isPaste,
                             target: target,
                             tex: optionalPath(rawTeX),
                             out: mergedReportPath(forTarget: target))

        log("═" .padding(toLength: 66, withPad: "═", startingAt: 0) + "\n")
        log("并回（把交回的 .bib 并进产物）\n")
        log("  工具   \(tool)\n")
        log("  解释器 \(py)\n")
        // 粘贴模式说清「文本走标准输入、不落中间文件」；文件模式那行**一字未改**。
        log(isPaste
            ? "  交回   （粘贴的文本：\(pasted.count) 字符，走标准输入，不落中间文件）\n"
            : "  交回   \(o.returned)\n")
        log("  目标   \(o.target)\n")
        log("  正文   \(o.tex.isEmpty ? "（未提供：不查引用键闭合）" : o.tex)\n")
        log("  报告   \(o.out)\n")
        log("  ⚠ 将覆盖 \(o.target)，原文件备份为 \(o.target).ai前"
            + "（回滚副本，下一次并回会覆盖它）。\n")
        log("  判据与「初排转换」的自动并入**同一份**：key 不可改、不增删条目、"
            + "没交回的条目不碰；本次会写一条合并记录。\n")
        log("═" .padding(toLength: 66, withPad: "═", startingAt: 0) + "\n\n")

        var extra: [String: String] = [:]
        let vendor = engineDir + "/vendor"
        if FileManager.default.fileExists(atPath: vendor) {
            extra["PYTHONPATH"] = vendor
        }
        let status = Shell.run(
            py,
            [tool] + mergeArguments(o),
            workingDirectory: engineDir,
            environment: extra,
            stdin: isPaste ? pasted : nil,
            log: log
        )
        log("\n")

        // U3：用户取消 → 只说「已取消」，不进下面的失败分支（bib 可能只改了一部分）
        if status == Shell.cancelledStatus {
            log("⏹ 已取消（用户终止；bib 的改动可能只做了一部分，需要时重新运行）。\n")
            return nil
        }
        if status == 0 {
            // 0 = 未改动（bib 与盘上任何文件都没动）。**不许**说成成功。
            log("ℹ 未改动：没有任何条目通过安全校验，\(o.target) 一个字节都没动"
                + "（不写回滚副本、不写合并记录、不写报告）。原因见上方逐条说明。\n")
            return nil
        }
        if status != 1 {
            if status == -1 {
                log("⚠ 并回失败：没能启动 \(py)。请确认 python3 可用。\n")
            } else {
                log("⚠ 并回失败（退出码 \(status)），请检查路径与上方输出\n")
            }
            log("  （并回模式的退出码：0 = 未改动；1 = 已并入；2 = 用法/IO 错误）\n")
            return nil
        }

        log("✅ 已并回（并入/跳过条数、回滚副本路径与合并记录落点见上方摘要）\n")
        guard PathKit.exists(o.out) else {
            log("⚠ 合并后报告没有落盘：\(o.out)（bib 已并回；可重跑一次体检看现状）\n")
            return nil
        }
        if let text = try? PathKit.readText(o.out) {
            log(text)
            if !text.hasSuffix("\n") { log("\n") }
        }
        return o.out
    }

    // MARK: - 并回之后重新编译该篇

    /// 并回之后重新编译**这一篇**（xelatex → biber -l=zh_pinyin → xelatex ×2）。
    ///
    /// 为什么不直接调 `CompileFeature.run(folderPath:)`：它按**期目录**下的编号子目录遍历，
    /// 传稿子目录进去它一个都不认（会静默什么都不做），传期目录又会把整期重编一遍——
    /// 而这个按钮承诺的是「该篇」。CompileFeature 的单篇实现 `compileTeX` 是 private，
    /// 本轮不改它的源码，所以这里按**同一份步骤**单篇跑：工具、参数、失败时的错误摘要回显
    /// 都与「编译全部」一致。
    @discardableResult
    static func recompile(manuscriptDir rawDir: String, log: Logger) -> Bool {
        let dir = PathKit.normalize(rawDir)
        guard PathKit.exists(dir), PathKit.isDirectory(dir) else {
            log("找不到稿件目录：\(dir)\n")
            return false
        }
        // .tex 基名 = 目录名去掉序号前缀（`01 Meimei_Han` → `Meimei_Han`），
        // 与 CompileFeature 的同一条推导。
        let folderName = (dir as NSString).lastPathComponent
            .split(whereSeparator: { $0.isWhitespace }).last.map(String.init)
            ?? (dir as NSString).lastPathComponent
        let tex = "\(folderName).tex"
        guard PathKit.exists(PathKit.join(dir, tex)) else {
            log("没有找到 \(tex)（在 \(dir) 里）：目录名与 .tex 文件名对不上，请手工编译。\n")
            return false
        }
        log("▶ 重新编译该篇：\(dir)/\(tex)\n")
        let steps: [(String, String, [String])] = [
            ("第一步：XeLaTeX \(tex)", "xelatex", ["-interaction=nonstopmode", tex]),
            ("第二步：biber -l=zh_pinyin \(folderName)", "biber", ["-l=zh_pinyin", folderName]),
            ("第三步：XeLaTeX \(tex)", "xelatex", ["-interaction=nonstopmode", tex]),
            ("第四步：XeLaTeX \(tex)", "xelatex", ["-interaction=nonstopmode", tex]),
        ]
        for (label, cmd, args) in steps {
            log(label + "\n")
            // U3：四步 TeX 都可能被「取消」按钮打断（`Shell.capture` 也接了取消句柄）
            guard let r = Shell.capture(cmd, args, workingDirectory: dir,
                                        cancel: log.cancel) else {
                log("未找到命令 \(cmd)，请确认已安装 TeX Live / MacTeX。\n")
                return false
            }
            if !r.succeeded {
                if r.status == Shell.cancelledStatus {
                    log("⏹ 已取消（用户终止；该篇可能只编译到一半，需要时重新运行）。\n")
                } else {
                    log("❌ \(label) 失败！\n")
                    log(Shell.errorExcerpt(r.output) + "\n")
                }
                return false
            }
        }
        log("✅ 该篇编译完成：\(tex) → \(folderName).pdf\n")
        return true
    }

    /// 跑一次体检（文件模式的老入口：行为一字不变）。
    /// 成功（退出码 0 / 1 且报告已落盘）返回报告路径，失败返回 nil。
    ///
    /// 退出码口径（checkbib.py 自己的约定）：
    ///   `0` = 未发现问题；`1` = 至少一处问题（**这不是程序错误**，界面不弹错误）；
    ///   `2` = 用法 / IO 错误；被信号杀死时 `Process.terminationStatus` 拿到的是信号号。
    @discardableResult
    static func run(returned rawReturned: String,
                    original rawOriginal: String,
                    tex rawTeX: String,
                    log: Logger) -> String? {
        run(source: .file(rawReturned), original: rawOriginal, tex: rawTeX, out: "", log: log)
    }

    /// 界面「报告输出（可选）」→ 真正传给 `--out` 的路径（纯函数，harness 直接断言）。
    ///
    /// - 空 → `auto`（自动落点，**行为与从前一字不差**）；
    /// - **已存在的目录** → 报告放进该目录，文件名沿用自动落点的文件名
    ///   （=`<名字>_回读校验报告.md`）—— 引擎侧 `_resolve_out()` 也做同一件事（双保险）；
    /// - 其它（含不存在路径）→ 当文件路径原样传，母目录不存在时引擎会如实报「写不了报告」。
    static func resolvedReportPath(_ raw: String, auto: String) -> String {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return auto }
        let p = PathKit.normalize(t)
        if PathKit.isDirectory(p) {
            return PathKit.join(p, (auto as NSString).lastPathComponent)
        }
        return p
    }

    /// 统一的体检入口：交回的文本可以来自**文件**（`.file`）或**粘贴框**（`.paste`，走 stdin）。
    ///
    /// - Parameter rawOut: 界面「报告输出（可选）」的原文；空 = 自动落点（与从前一样）。
    @discardableResult
    static func run(source: ReturnedSource,
                    original rawOriginal: String,
                    tex rawTeX: String,
                    out rawOut: String = "",
                    log: Logger) -> String? {
        var returned = ""
        var pasted = ""
        switch source {
        case .file(let raw):
            let rawTrimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !rawTrimmed.isEmpty else {
                log("请先选择编辑交回的 .bib 文件。\n")
                return nil
            }
            returned = PathKit.normalize(rawTrimmed)
        case .paste(let text):
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                log("请先在「粘贴文本」框里粘贴交回的 .bib 文本。\n")
                return nil
            }
            pasted = text
        }
        let isPaste = !pasted.isEmpty
        if !isPaste {
            guard PathKit.exists(returned), !PathKit.isDirectory(returned) else {
                log("读不到交回的 .bib：\(returned)（文件不存在，或这是个文件夹）\n")
                return nil
            }
        }
        guard let tool = locateTool() else {
            log("未找到文献回读校验脚本 Engine/checkbib.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/checkbib.py"
                + "（官方 ./build.sh 会把 Engine 打包进去）；")
            log("② 顺着 Engine/convert.py 的定位结果取同级的 checkbib.py"
                + "（开发时把 app 放在仓库的  下即可命中）。\n")
            log("请重新用 build.sh 构建，或把 Engine 目录放进 app 包的 Resources 内。\n")
            return nil
        }
        let engineDir = (tool as NSString).deletingLastPathComponent
        guard let py = ConvertFeature.locatePython(toolDir: engineDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return nil
        }

        // 报告落点：文件模式 = 交回文件旁边（**一字未改**）；粘贴模式 = 原件（产物目录）
        // 旁边，没有原件时退到系统临时目录并说明。
        // 界面填了「报告输出（可选）」则覆盖自动落点（第十五批，与 Windows --out 对等）。
        let autoOut: String
        if isPaste {
            let (p, note) = pasteReportPath(forOriginal: rawOriginal)
            autoOut = p
            if !note.isEmpty { log(note + "\n") }
        } else {
            autoOut = reportPath(forReturned: returned)
        }
        let outPath = resolvedReportPath(rawOut, auto: autoOut)
        if !rawOut.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, outPath != autoOut {
            log("（报告落点按界面的「报告输出」：\(outPath)）\n")
        }

        let o = Options(returned: isPaste ? "" : returned,
                        returnedStdin: isPaste,
                        original: optionalPath(rawOriginal),
                        tex: optionalPath(rawTeX),
                        out: outPath)

        log("═" .padding(toLength: 66, withPad: "═", startingAt: 0) + "\n")
        log("回读/比对报告查看器\n")
        log("  工具   \(tool)\n")
        log("  解释器 \(py)\n")
        log(isPaste
            ? "  交回   （粘贴的文本：\(pasted.count) 字符，走标准输入，不落中间文件）\n"
            : "  交回   \(o.returned)\n")
        log("  原件   \(o.original.isEmpty ? "（未提供：不做字段级对比）" : o.original)\n")
        log("  正文   \(o.tex.isEmpty ? "（未提供：不查引用键闭合）" : o.tex)\n")
        log("  报告   \(o.out)\n")
        log("═" .padding(toLength: 66, withPad: "═", startingAt: 0) + "\n\n")

        // checkbib.py 依赖同目录的 slcn/（脚本自己会把所在目录插进 sys.path）；
        // PYTHONPATH 仍按既有做法挂上 vendor/。
        // ⚠️ **只传增量，不要传整个 environment** —— 理由见 ConvertFeature.run 的长注释。
        var extra: [String: String] = [:]
        let vendor = engineDir + "/vendor"
        if FileManager.default.fileExists(atPath: vendor) {
            extra["PYTHONPATH"] = vendor
        }

        // ⚠️ 脚本路径必须**排在参数最前面**：Shell.run 的第二个参数是子进程的完整
        //    argv，不是「脚本名之后的参数」。漏了它 Python 会把 `--returned` 当成
        //    自己的选项，报 `Unknown option: --returned`（实测踩到）。
        let status = Shell.run(
            py,
            [tool] + arguments(o),
            workingDirectory: engineDir,
            environment: extra,
            stdin: isPaste ? pasted : nil,
            log: log
        )
        log("\n")

        // U3：用户取消 → 只说「已取消」，不进下面的失败分支
        if status == Shell.cancelledStatus {
            log("⏹ 已取消（用户终止；报告可能未生成或只写了一半，需要时重新运行）。\n")
            return nil
        }
        if status == 0 {
            log("✅ 未发现问题\n")
        } else if status == 1 {
            // 1 = 脚本正常跑完、报告里确实有问题。**这不是程序错误**：
            // 不弹错误框、不写「失败」，编辑照着报告逐条处理即可。
            log("❗ 发现问题，请按报告逐条处理\n")
        } else {
            if status == -1 {
                log("⚠ 运行失败：没能启动 \(py)。请确认 python3 可用。\n")
            } else {
                log("⚠ 运行失败（退出码 \(status)），请检查路径与下方输出\n")
            }
            if !PathKit.exists(o.out) {
                // 退出码 2 是脚本自己报的用法/IO 错；其它非零（含被信号杀死时
                // terminationStatus 拿到的信号号）多半是脚本中途崩了，报告根本没落盘。
                log("  报告没有生成：\(o.out)\n")
                log("  脚本可能被信号中止（自身崩溃）或没能写出文件，"
                    + "上面最后几行输出通常就是原因。\n")
            } else {
                // 脚本是**先写报告、后返回退出码**的，所以非 0/1 就意味着这次没写；
                // 盘上那份只可能是上一次留下的 —— 说清楚，别让编辑拿旧报告当这次的结论。
                log("  注意：\(o.out) 是上一次留下的旧报告，本次没有重写，"
                    + "不要拿它当这次的结论。\n")
            }
            return nil
        }

        // 报告**原样**贴进运行日志：编辑不用另开文件就能看到全部问题。
        guard let text = try? PathKit.readText(o.out) else {
            log("⚠ 报告没有落盘或读不出来：\(o.out)\n")
            log("  上面最后几行输出通常能看出原因。\n")
            return nil
        }
        log(text)
        if !text.hasSuffix("\n") { log("\n") }   // 只补一个换行，内容本身不动
        // 体检本身不需要「原件」栏，但**并回**需要它当目标（产物里的 reference.bib）。
        // 这里说一句，免得编辑体检完想直接并回时按钮是灰的却不知道为什么。
        if o.original.isEmpty {
            log("（提示：要把这份交回的 .bib **并回**产物，请先在「任务书前的原件 .bib」一栏选择"
                + "产物里的 reference.bib —— 并回按钮需要它作为目标。）\n")
        }
        return o.out
    }

    // MARK: - 粘贴预览（`checkbib.py --list-keys`）

    /// 粘贴预览：把粘贴框里的文本喂给 `checkbib.py --list-keys`，返回引擎打的那**一行**。
    ///
    /// 成功 = `20 条：Ayer1946, Burge1984, …`；失败 = 一句中文说明（把退出码与引擎最后
    /// 一行输出带上）。为什么要预览：编辑粘进来的可能只是一段带说明文字的聊天回复，
    /// 先让他看见「识别到几条、键是什么」，再决定要不要体检/并回。
    ///
    /// ⚠️ 引擎侧保证 `--list-keys` **永不写文件**（连 `--out` 都不生效），所以这个方法可以
    /// 在编辑每次改动后重跑；它只 `Shell.capture` 取输出，**不写运行日志**（日志留给体检/并回）。
    static func previewPasted(_ text: String) -> String {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return "" }
        guard let tool = locateTool() else { return "预览不可用：未找到 Engine/checkbib.py" }
        let engineDir = (tool as NSString).deletingLastPathComponent
        guard let py = ConvertFeature.locatePython(toolDir: engineDir) else {
            return "预览不可用：未找到 python3"
        }
        let args = [tool] + listArguments(ListOptions(returnedStdin: true))
        guard let r = Shell.capture(py, args, workingDirectory: engineDir, stdin: text) else {
            return "预览不可用：没能启动 \(py)"
        }
        // capture 把 stdout 与 stderr 并到一个管道；正常情况只有一行，出错时取**最后**一行
        // （Python 的 traceback/用法错误在最后）。
        let line = r.output.split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .last(where: { !$0.isEmpty }) ?? ""
        if r.status == 0 { return line }
        return "预览失败（退出码 \(r.status)）：\(line)"
    }
}
