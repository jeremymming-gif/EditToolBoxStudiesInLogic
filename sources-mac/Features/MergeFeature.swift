import Foundation

/// 「合并文件」—— 对应原程序的 merge.merge_pdfs(folder_path, out_txt)。
///
/// 合并顺序：扉页.pdf → 000 目录(中文 Chinese Content.pdf、英文 contents.pdf)
///          → 各篇文章目录（按序号 01,02,…）。
/// 每追加一份就在大纲里登记一个书签，文章书签的取名为目录名去掉序号后的部分。
enum MergeFeature {

    static func run(folderPath rawPath: String,
                    log: Logger,
                    nestOutlines: Bool = false,
                    mergeEditorsPage: Bool = false,
                    appendBackCover: Bool = false) {
        log("运行合并文件的函数\n")
        let folderPath = PathKit.normalize(rawPath)
        log("文件夹路径：\(folderPath)\n")

        let folders = PathKit.numberedEntries(in: folderPath)
        let merger = PDFMerger()
        merger.nestSourceOutlines = nestOutlines

        // ---------- 扉页 ----------
        var coverPages = 0
        do {
            let cover = PathKit.join(folderPath, "扉页.pdf")
            log("找pdf路径：\(cover)\n")
            if PathKit.exists(cover) {
                try merger.appendWithoutTitle(cover)
                coverPages = merger.pageCount
            } else {
                log("没有找到扉页文件！\n")
            }
            merger.addTopLevelItem(title: "cover", pageIndex: 0)
        } catch {
            log("处理扉页时出错啦：\(error.localizedDescription)\n")
        }

        // ---------- 编委信息页（可选，插在扉页之后）----------
        if mergeEditorsPage {
            let detection = EditorsPageDetector.detect(in: folderPath)
            if let candidate = detection.chosen {
                let path = PathKit.join(folderPath, candidate.fileName)
                log("找编委信息页：\(path)\n")
                do {
                    try merger.appendIgnoringOutline(path, title: "编委页")
                    log("已加入编委信息页：\(candidate.fileName)（\(candidate.pageCount) 页，\(candidate.matchedBy)）\n")
                } catch {
                    log("处理编委信息页时出错啦：\(error.localizedDescription)\n")
                }
                if detection.isAmbiguous {
                    log("⚠️ 另有 \(detection.others.count) 个文件也像编委页，已忽略：\(detection.others.joined(separator: "、"))\n")
                    log("   建议只保留最终版，并命名为「编委页.pdf」。\n")
                }
            } else {
                log("未找到编委信息页文件，已跳过。建议把该文件命名为「编委页.pdf」放在期目录下。\n")
            }

            // 扉页不止一页时，可能它本身就已经含编委页，插进去就重复了 —— 只提示，不擅自决定
            if coverPages > 1 {
                log("⚠️ 扉页.pdf 有 \(coverPages) 页，可能已经包含编委信息页，请确认合并结果是否重复。\n")
            }
        }

        // ---------- 目录与正文 ----------
        for name in folders {
            let folder = PathKit.join(folderPath, name)

            if name.hasPrefix("000") {
                let chinese = PathKit.join(folder, "Chinese Content.pdf")
                log("找pdf路径：\(chinese)\n")
                let english = PathKit.join(folder, "contents.pdf")
                log("找pdf路径：\(english)\n")

                if PathKit.exists(chinese) {
                    do { try merger.append(chinese, title: "Chinese Content") }
                    catch { log("处理中文目录时出错啦：\(error.localizedDescription)\n") }
                } else {
                    log("没有找到中文目录文件！\n")
                }

                if PathKit.exists(english) {
                    do { try merger.append(english, title: "contents") }
                    catch { log("处理英文目录时出错啦：\(error.localizedDescription)\n") }
                } else {
                    log("没有找到英文目录文件！\n")
                }
            } else {
                // 目录名形如 "03 Liang_Zhang"，取最后一段作为文章名
                let folderName = name
                    .split(whereSeparator: { $0.isWhitespace })
                    .last
                    .map(String.init) ?? name
                let pdf = PathKit.join(folder, "\(folderName).pdf")
                log("找pdf路径：\(pdf)\n")

                // 先按标准名找；找不到再宽松匹配（应对 `Chen Liu.pdf` 这种把
                // 下划线写成空格的情况 —— 原版会整篇跳过且不报错，实测丢过 33 页）
                if let found = PathKit.resolveArticlePDF(folder: folder, folderName: folderName) {
                    if found.actualName != "\(folderName).pdf" {
                        log("⚠️ 实际文件名是「\(found.actualName)」，与文件夹名「\(folderName)」不一致，已按宽松匹配采用它。\n")
                    }
                    do { try merger.append(found.path, title: folderName) }
                    catch { log("处理\(folderName)时出错啦：\(error.localizedDescription)\n") }
                } else {
                    log("没有找到\(folderName)文件！\n")
                }
            }
        }

        // ---------- 封底附加页（可选，放最后）----------
        if appendBackCover {
            if let back = EditorsPageDetector.detectBackCover(in: folderPath) {
                let path = PathKit.join(folderPath, back.fileName)
                log("找封底页：\(path)\n")
                do {
                    try merger.appendIgnoringOutline(path, title: "封底")
                    log("已在末尾加入封底页：\(back.fileName)（\(back.pageCount) 页，\(back.matchedBy)）\n")
                } catch {
                    log("处理封底页时出错啦：\(error.localizedDescription)\n")
                }
            } else {
                log("未找到封底页文件，已跳过。\n")
            }
        }

        // ---------- 写出 ----------
        let output = PathKit.join(folderPath, "合并\(PathKit.timestamp()).pdf")
        do {
            try merger.write(to: output)
            log("已生成合并文件（共 \(merger.pageCount) 页）：\(output)\n")
        } catch {
            log("写出合并文件失败：\(error.localizedDescription)\n")
        }

        log("运行结束，请确认上方是否有错。\n")
    }
}
