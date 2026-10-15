import Foundation

/// 「编译全部」—— 对应原程序的 compileallpdf.py。
///
/// 对期目录下每个编号子文件夹（跳过 000 目录）执行四步：
///   1. XeLaTeX
///   2. biber -l=zh_pinyin
///   3. XeLaTeX
///   4. XeLaTeX
/// 任何一步失败就停止该文章并报告。
enum CompileFeature {

    private struct Step {
        let label: String
        let tool: String
        let arguments: [String]
        let failureLabel: String
    }

    /// 对应原 compile_tex(folder, folder_name, out_txt)
    private static func compileTeX(folder: String, folderName: String, log: Logger) -> Bool {
        log("打开路径：\(folder)\n")

        let texFile = "\(folderName).tex"
        let steps: [Step] = [
            Step(label: "第一步：Xelatex \"\(texFile)\"\n",
                 tool: "xelatex",
                 arguments: ["-interaction=nonstopmode", texFile],
                 failureLabel: "Xelatex \(folderName) 失败！\n"),

            Step(label: "第二步：biber -l=zh_pinyin \"\(texFile)\"\n",
                 tool: "biber",
                 arguments: ["-l=zh_pinyin", folderName],
                 failureLabel: "biber -l=zh_pinyin \(folderName) 失败！\n"),

            Step(label: "第三步：Xelatex \"\(texFile)\"\n",
                 tool: "xelatex",
                 arguments: ["-interaction=nonstopmode", texFile],
                 failureLabel: "Xelatex \(folderName) 失败！\n"),

            Step(label: "第四步：Xelatex \"\(texFile)\"\n",
                 tool: "xelatex",
                 arguments: ["-interaction=nonstopmode", texFile],
                 failureLabel: "Xelatex \(folderName) 失败！\n"),
        ]

        for step in steps {
            log(step.label)

            guard let result = Shell.capture(step.tool,
                                             step.arguments,
                                             workingDirectory: folder) else {
                log("未找到命令 \(step.tool)，请确认已安装 TeX Live / MacTeX。\n")
                return false
            }

            if !result.succeeded {
                log(step.failureLabel)
                // ★ 与原版的差异：原版把编译输出丢进 NUL，出错时完全看不到原因。
                //   这里在失败时回显错误摘要，便于定位问题（成功时仍然保持安静）。
                log(Shell.errorExcerpt(result.output) + "\n")
                return false
            }
        }
        return true
    }

    /// 对应原 compile_pdfs(folder_path, out_txt)
    static func run(folderPath rawPath: String, log: Logger) {
        log("运行全编译的函数\n")
        let folderPath = PathKit.normalize(rawPath)
        let folders = PathKit.numberedEntries(in: folderPath)

        for name in folders {
            // 000 目录（中英文目录）不参与编译
            if name.hasPrefix("000") { continue }

            let folder = PathKit.join(folderPath, name)
            let folderName = name
                .split(whereSeparator: { $0.isWhitespace })
                .last
                .map(String.init) ?? name
            let texPath = PathKit.join(folder, "\(folderName).tex")

            if PathKit.exists(texPath) {
                _ = compileTeX(folder: folder, folderName: folderName, log: log)
            } else {
                log("没有找到\(folderName)文件！\n")
            }
        }

        log("运行结束，请确认上方是否有错。\n")
    }
}
