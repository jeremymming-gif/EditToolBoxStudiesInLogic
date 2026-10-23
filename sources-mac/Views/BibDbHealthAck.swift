import SwiftUI

/// B-205：重复/近重复「确认不同（不再提示）」的**行内裁决入口**
/// （待处理页 → 库健康 → 「重复记录」/「同一作品多条记录」的每一对）。
///
/// 用户拍板（2026-10-23 晚）：`miaolitian≈miaolitian2` 这类误报允许用户自己裁决 ——
/// 「确认不同」= **保持两条记录、不合并、不改库**，只把这一对写进笔记仓
/// `bib_db/health-ignore.json`（可审计；撤销用 `git revert` 或删掉该条目），
/// 并自动生成一次**只含该 sidecar** 的本地提交（不推送）。刷新后该对 warning 消失。
///
/// 线程约定（与其它页一致）：引擎 `BibDbFeature.ackDuplicates` 与 git
/// `BibDbGit.commitHealthIgnore` 都是同步、起子进程 → 一律后台队列调、回主线程回填。
///
/// 默认路径零变化：不点这个按钮，不写任何文件。
struct BibDbHealthAckControl: View {
    /// 引擎 `--code`：`near_duplicates`（同一作品多条记录）/ `duplicate_content`（重复记录）。
    /// 注意与待处理页的 registry 名不同（registry `duplicates` → code `duplicate_content`），
    /// 映射只在 `BibDbReviewView.healthAckCode` 一处。
    let code: String
    /// 该对/该组的 key（近重复对一般是 2 个；引擎负责 canonical 解析与校验）。
    let keys: [String]
    /// 外部禁用（父页 `running` 或库已变化 `stale` 时一并置灰）。
    let disabled: Bool
    /// 成功（sidecar 已写）后回调：父页 `refreshAfterWrite()` 让 warning/计数/状态条刷新。
    let onFinished: () -> Void

    @State private var confirm = false
    @State private var busy = false
    @State private var notice = ""
    @State private var warning = ""

    init(code: String, keys: [String], disabled: Bool = false,
         onFinished: @escaping () -> Void = {}) {
        self.code = code
        self.keys = keys
        self.disabled = disabled
        self.onFinished = onFinished
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Button("确认不同…") { confirm = true }
                .controlSize(.small)
                .disabled(disabled || busy)
                .help("保持两条记录、不改库；写入 bib_db/health-ignore.json 并不再提示（可 git revert 撤销）")
                // 走查定位：同一页可能有多个「确认不同…」，AX 名字里带这一对的 key 才唯一。
                .accessibilityLabel(Text("确认不同 \(keys.joined(separator: "≈"))"))
                .alert(confirmTitle, isPresented: $confirm) {
                    Button("取消", role: .cancel) {}
                    Button("确认不同（不再提示）") { run() }
                } message: {
                    Text(confirmMessage)
                }
            if busy {
                ProgressView().controlSize(.small)
            }
            if !notice.isEmpty {
                Text(notice)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !warning.isEmpty {
                Text(warning)
                    .font(.caption2)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// 二次确认的标题/说明：`duplicate_content` 组可能 **>2 条**（引擎要求列全整组），
    /// 写死「这两条」会误导 —— 2 条（近重复对）沿用原固定文案（走查按标题精确定位），
    /// 其余改成「这些记录」；按钮与 AX 名字（含 keys）不变。
    private var isPair: Bool { keys.count == 2 }

    private var confirmTitle: String {
        isPair ? "确认这两条是不同条目？" : "确认这些记录是不同条目？"
    }

    private var confirmMessage: String {
        var lines: [String] = []
        lines.append(isPair
            ? "保持两条记录、不做合并，也不改 bibliography.jsonl。"
            : "保持这些记录为各自独立条目、不做合并，也不改 bibliography.jsonl。")
        lines.append("只写入笔记仓的 `bib_db/health-ignore.json`（可审计；撤销用 "
                     + "`git revert` 或删掉该条目），并自动生成一次只含该文件的本地提交"
                     + "（不推送）。")
        lines.append("")
        lines.append("条目：" + keys.joined(separator: " ≈ "))
        return lines.joined(separator: "\n")
    }

    /// 二次确认后：引擎写 sidecar → 自动本地 commit（只该文件）→ 回主线程提示 → 刷新列表。
    /// 引擎失败 → 黄字显示 error（库与 sidecar 都没动，不刷新）；提交失败 → 数据已写、黄字 fail-soft。
    private func run() {
        guard !busy, !disabled, !keys.isEmpty else { return }
        busy = true
        notice = ""
        warning = ""
        let code = self.code
        let keys = self.keys
        let db = BibDbFeature.existingDBPath() ?? ""
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = BibDbFeature.ackDuplicates(
                db: db, code: code, keys: keys,
                reason: "用户确认不同条目（GUI；B-205）")
            var commit: BibDbGit.CommitResult? = nil
            if outcome.error == nil {
                commit = BibDbGit.commitHealthIgnore(db: db,
                                                     detail: keys.joined(separator: "≈"))
            }
            DispatchQueue.main.async {
                busy = false
                if let error = outcome.error {
                    warning = "确认不同失败（库与 sidecar 未改）：" + error
                    return
                }
                let label = keys.joined(separator: " ≈ ")
                var text = outcome.added
                    ? "✅ 已确认不同（不再提示）：" + label
                    : "✅ 此前已确认不同（无变化）：" + label
                if outcome.added {
                    text += "；sidecar 条目 \(outcome.entriesBefore) → \(outcome.entriesAfter)"
                }
                notice = text
                if let commit {
                    if let error = commit.error {
                        // 提交失败绝不回滚/不清数据：sidecar 已落盘，黄字提示手动补提交。
                        warning = "已写入 sidecar，但本地提交失败（数据未丢、不影响裁决）：" + error
                    } else if commit.committed {
                        let short = commit.sha.isEmpty
                            ? "" : " " + String(commit.sha.prefix(12)) + "…"
                        notice += "；已自动本地提交" + short + "（未推送）"
                    } else {
                        notice += "；sidecar 没有新的待提交改动（未产生 commit）"
                    }
                }
                onFinished()
            }
        }
    }
}
