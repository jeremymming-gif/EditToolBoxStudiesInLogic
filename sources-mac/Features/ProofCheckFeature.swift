import Foundation

/// 「一校核查」—— 调引擎的 `Engine/proofcheck.py`（第 92 条：2026-10-15 新增）。
///
/// **逻辑不在 Swift 里**：26 条规则的权威实现在 `Engine/slcn/proofcheck.py`，
/// 这一页只负责选文件、拼参数、把引擎输出流到日志 —— 与「初排转换」同一套路子。
/// 这样 mac 与 Windows 两个外壳算出来的结论必然一致（引擎只有一份）。
/// 规则的完整清单与「哪些手册规范没有做成机器检查」见 `Engine/docs/PROOF_RULES.md`。
enum ProofCheckFeature {

    /// 核查选项（对应 proofcheck.py 的命令行参数）
    struct Options {
        /// 要核查的初排稿 .tex（**必填**）
        var tex: String = ""
        /// 是否把「无歧义」的命中改到一个新文件（输入文件不动）
        var fix: Bool = false
        /// 语种：`""` = 交给引擎按模板字段（mytitleCN/mytitleEN）判
        var lang: String = ""
    }

    /// 找 `proofcheck.py`（顺序由 `EngineLocator` 统一规定：用户指定 → app 包内 → 往上找仓库）。
    ///
    /// 从引擎目录里取同名入口：这样「设置」里指了自定义引擎路径时，各页一致。
    static func locateTool(convertTool: String? = EngineLocator.convertTool()) -> String? {
        EngineLocator.tool(named: "proofcheck.py", convertTool: convertTool)
    }

    /// 拼命令行参数。**纯函数**（`tools/proofcheck_test` 直接断言它），
    /// 数组形式、不做 shell 字符串插值。
    static func arguments(_ o: Options) -> [String] {
        var args = ["--tex", o.tex]
        if !o.lang.isEmpty { args += ["--lang", o.lang] }
        if o.fix { args.append("--fix") }
        return args
    }

    static func run(options o: Options, log: Logger) {
        let tex = o.tex.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !tex.isEmpty else {
            log("请先选择初排稿 .tex 文件。\n")
            return
        }
        guard FileManager.default.fileExists(atPath: tex) else {
            log("找不到文件：\(tex)\n")
            return
        }
        guard let tool = locateTool() else {
            log("未找到核查引擎 Engine/proofcheck.py。\n")
            log("按顺序找两处：① app 包内 Contents/Resources/Engine/proofcheck.py"
                + "（官方 ./build.sh 会把它打包进去）；")
            log("② 从 app 所在目录逐级向上找 Engine/proofcheck.py"
                + "（开发时把 app 放在仓库的  下即可命中）。\n")
            return
        }
        let toolDir = (tool as NSString).deletingLastPathComponent
        guard let py = EngineLocator.locatePython(toolDir: toolDir) else {
            log("未找到 python3。请先安装（brew install python3）。\n")
            return
        }

        log("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n")
        log("一校核查\n")
        log("  工具   \(tool)\n")
        log("  解释器 \(py)\n")
        log("  稿件   \(tex)\n")
        log("  模式   \(o.fix ? "核查 + 把无歧义的改到新文件（输入文件不动）" : "只报不改")\n")
        log("  语种   \(o.lang.isEmpty ? "自动（按模板字段判）" : o.lang)\n")
        log("═".padding(toLength: 66, withPad: "═", startingAt: 0) + "\n\n")

        // 依赖在项目本地 vendor/ 里（与初排转换同一口径）。
        // ⚠️ 只传增量：`Shell.run` 以 `Shell.environment()` 为底（含 TeX 路径），
        //    传整份 ProcessInfo 环境会把 PATH 覆盖掉。
        let extra = EngineLocator.environment(toolDir: toolDir)

        let status = Shell.run(
            py,
            [tool] + arguments(o),
            workingDirectory: toolDir,
            environment: extra,
            log: log
        )

        log("\n")
        switch status {
        case 0:
            log("✅ 完成：没有命中任何规则。\n")
        case 1:
            // 退出码 1 在这里是**正常结果**（有命中），不是失败 —— 别显示成错误。
            log("✅ 完成：有命中。报告已写到稿件同目录的 `<名字>_一校核查.md`。\n")
            if o.fix {
                log("   改后的稿子在 `<名字>_一校核查后.tex`；"
                    + "建议与原文 diff 一遍，逐条确认。\n")
            }
        case -1:
            log("❌ 启动失败。\n")
        default:
            log("❌ 核查失败（退出码 \(status)）。请把上面的报错发给技术人员。\n")
        }
    }
}
