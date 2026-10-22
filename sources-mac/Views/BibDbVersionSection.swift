import SwiftUI

/// B-190：写库成功后的**版本层结果**（只用于 UI 提示，不属于「写库失败」语义）。
struct BibDbVersionWriteNotice: Equatable {
    /// 真的产生了新 commit
    var committed = false
    /// 版本层失败（写库已成功、数据绝不回滚）
    var failed = false
    var text = ""
}

/// B-190：写库成功回调里调用的**自动本地 commit**（同步；调用方必须已在后台队列）。
///
/// 只 add/commit `bib_db/bibliography.jsonl`、绝不 push（见 `BibDbGit` 的源码断言）。
/// 返回值只用于黄字/日志提示：**commit 失败绝不进入写库错误语义**。
enum BibDbVersionCommit {

    static func afterWrite(db: String, action: String, recordCount: Int,
                           shaBefore: String, shaAfter: String,
                           note: String = "") -> BibDbVersionWriteNotice {
        guard !db.isEmpty else {
            return BibDbVersionWriteNotice(
                committed: false, failed: true,
                text: "写库成功，但版本层没拿到库文件路径，无法自动提交（不影响库数据）。")
        }
        let result = BibDbGit.commitDBVersion(db: db, action: action,
                                              recordCount: recordCount,
                                              shaBefore: shaBefore, shaAfter: shaAfter,
                                              note: note)
        if let error = result.error {
            return BibDbVersionWriteNotice(
                committed: false, failed: true,
                text: "写库成功，但版本层提交失败（数据已落盘、未回滚）：" + error)
        }
        if result.committed {
            let short = result.sha.isEmpty ? "" : " " + String(result.sha.prefix(12)) + "…"
            return BibDbVersionWriteNotice(
                committed: true, failed: false,
                text: "写库成功；已自动本地提交" + short + "（未推送）。")
        }
        return BibDbVersionWriteNotice(
            committed: false, failed: false,
            text: "写库成功；库文件没有新的待提交改动（未产生 commit）。")
    }
}

/// 「待处理」页版本库状态区的状态（B-190 §3.2）。
///
/// 线程约定：所有会起子进程的 `BibDbGit.*` 一律后台队列调、回主线程回填；
/// git 不可用 / 非 repo / 无 upstream / push 失败 **只黄字 fail-soft**，不弹错误框、不阻断写库。
final class BibDbVersionModel: ObservableObject {

    @Published var status: BibDbGit.VersionStatus? = nil
    @Published var loading = false
    @Published var busy = false
    @Published var notice = ""        // 成功/中性一句话
    @Published var warning = ""       // 黄字（fail-soft）
    @Published var manualMessage = "引用库：手动提交"
    @Published var seconds: Double = 0

    // 三个按钮的二次确认
    @Published var confirmCommit = false
    @Published var confirmPush = false
    @Published var confirmRevert = false

    var canOperate: Bool {
        guard let status else { return false }
        return status.loaded && !busy
    }

    /// 读当前版本库状态（后台；失败只黄字）。
    /// `keepWarning=true` 时不清除操作失败留下的黄字（如 push 的 stderr 摘要）——
    /// 否则后台刷新回来会把刚显示的失败原因覆盖成「无 upstream」之类的普通提示。
    func reload(keepWarning: Bool = false) {
        let db = BibDbFeature.existingDBPath() ?? ""
        guard !db.isEmpty else {
            status = nil
            warning = "引用库文件不存在：版本库状态不可用（不影响写库）。"
            return
        }
        loading = true
        let model = self
        DispatchQueue.global(qos: .utility).async {
            let result = BibDbGit.versionStatus(db: db)
            DispatchQueue.main.async {
                model.loading = false
                model.status = result
                model.seconds = result.seconds
                if let error = result.error {
                    model.warning = error
                } else if !keepWarning {
                    model.warning = result.unpushedError ?? ""
                }
            }
        }
    }

    /// 手动补提交（二次确认后）；只提交库文件。
    func runManualCommit() {
        guard !busy else { return }
        let db = BibDbFeature.existingDBPath() ?? ""
        guard !db.isEmpty else {
            warning = "引用库文件不存在：无法提交。"
            confirmCommit = false
            return
        }
        let message = manualMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        busy = true
        notice = ""
        warning = ""
        let model = self
        DispatchQueue.global(qos: .utility).async {
            let result = BibDbGit.manualCommit(db: db,
                                               message: message.isEmpty ? "引用库：手动提交" : message)
            DispatchQueue.main.async {
                model.busy = false
                model.confirmCommit = false
                if let error = result.error {
                    model.warning = "版本提交失败：" + error
                } else if result.committed {
                    let short = result.sha.isEmpty ? "" : " " + String(result.sha.prefix(12)) + "…"
                    model.notice = "已本地提交" + short + "（未推送）。"
                } else {
                    model.notice = "库文件没有未提交改动（未产生新 commit）。"
                }
                model.reload(keepWarning: result.error != nil)
            }
        }
    }

    /// 推送（二次确认后；失败把 git stderr 摘要放黄字）。**绝不 force**。
    func runPush() {
        guard !busy else { return }
        let db = BibDbFeature.existingDBPath() ?? ""
        guard !db.isEmpty else {
            warning = "引用库文件不存在：无法推送。"
            confirmPush = false
            return
        }
        busy = true
        notice = ""
        warning = ""
        let model = self
        DispatchQueue.global(qos: .utility).async {
            let result = BibDbGit.push(db: db)
            DispatchQueue.main.async {
                model.busy = false
                model.confirmPush = false
                if let error = result.error {
                    model.warning = error
                } else if result.pushed {
                    model.notice = "已推送到远端（推送是手动动作，永不自动发生）。"
                }
                model.reload(keepWarning: result.error != nil)
            }
        }
    }

    /// 回退到上一个提交（二次确认后）：整库回退 + 新 commit，**不 hard reset、不 push**。
    /// 成功后调用 `onReverted`，让待处理页重载库数据。
    func runRevert(onReverted: @escaping () -> Void) {
        guard !busy else { return }
        let db = BibDbFeature.existingDBPath() ?? ""
        guard !db.isEmpty else {
            warning = "引用库文件不存在：无法回退。"
            confirmRevert = false
            return
        }
        busy = true
        notice = ""
        warning = ""
        let model = self
        DispatchQueue.global(qos: .utility).async {
            let result = BibDbGit.revertToPreviousCommit(db: db)
            DispatchQueue.main.async {
                model.busy = false
                model.confirmRevert = false
                if let error = result.error {
                    model.warning = "回退未执行：" + error
                } else if result.reverted {
                    let commit = result.commitSha.isEmpty
                        ? "（新 commit）" : String(result.commitSha.prefix(12)) + "…"
                    model.notice = "已回退到上一个提交：新 commit " + commit
                        + "；库 sha " + String(result.dbSha256.prefix(12)) + "…（未推送）。"
                    onReverted()
                }
                model.reload(keepWarning: result.error != nil)
            }
        }
    }
}

/// 待处理页底部的「版本库」状态区（B-190 §3.2）：
/// 当前分支 / 库文件脏否 / 未推送提交数 / 最近 3 条库提交 + 三个二次确认按钮。
///
/// 备份制已取消：「回退到上一个提交…」承担整库回退（`checkout HEAD~1 -- 库文件` + 新 commit）。
struct BibDbVersionSection: View {
    @ObservedObject var model: BibDbVersionModel
    /// 回退成功后让父页重载库数据（裁决/冲突/新鲜度）
    var onReverted: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Text("版本库（git）").font(.callout).bold()
                Text("写库成功后自动本地 commit；推送是手动动作，永不自动 push")
                    .font(.caption2).foregroundStyle(.tertiary)
                Spacer()
                if model.loading || model.busy {
                    ProgressView().controlSize(.small)
                }
                Button("刷新版本状态") { model.reload() }
                    .controlSize(.small)
                    .disabled(model.loading || model.busy)
            }
            if let status = model.status, status.loaded {
                statusRows(status)
            }
            if !model.warning.isEmpty {
                BibDbStatusLine(kind: .warning, text: model.warning)
            }
            if !model.notice.isEmpty {
                BibDbStatusLine(kind: .idle, text: model.notice)
            }
            HStack(spacing: 12) {
                // 三个按钮各自挂二次确认：同一 view 上叠多个 .alert 时 SwiftUI 只认最后一个。
                Button("提交…") { model.confirmCommit = true }
                    .disabled(!model.canOperate)
                    .help("手动补提交：只 add/commit bib_db/bibliography.jsonl（二次确认，不推送）")
                    .alert("手动提交库文件？", isPresented: $model.confirmCommit) {
                        TextField("提交说明", text: $model.manualMessage)
                        Button("取消", role: .cancel) {}
                        Button("提交") { model.runManualCommit() }
                    } message: {
                        Text("只提交 `bib_db/bibliography.jsonl`；绝不自动推送。")
                    }
                Button("推送…") { model.confirmPush = true }
                    .disabled(!model.canOperate)
                    .help("把当前分支的未推送提交推到远端（二次确认；失败显示 git stderr 摘要；不做 force）")
                    .alert("推送到远端？", isPresented: $model.confirmPush) {
                        Button("取消", role: .cancel) {}
                        Button("推送") { model.runPush() }
                    } message: {
                        Text("将把当前分支的未推送提交推到远端（不使用 force）；失败会显示 git 的 stderr 摘要。")
                    }
                Button("回退到上一个提交…") { model.confirmRevert = true }
                    .disabled(!model.canOperate)
                    .help("把库文件恢复为上一个库提交的内容，并生成一个新 commit；不 hard reset、不推送")
                    .alert("回退到上一个提交？", isPresented: $model.confirmRevert) {
                        Button("取消", role: .cancel) {}
                        Button("确认回退", role: .destructive) { model.runRevert(onReverted: onReverted) }
                    } message: {
                        Text("将把库文件恢复为上一个库提交的内容，并生成一个新 commit「回退到上一个提交」；"
                             + "不 hard reset、不推送。回退后本页会自动重载库数据。")
                    }
                Spacer()
            }
        }
        .padding(10)
        .background(Color.secondary.opacity(0.04))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.15)))
    }

    private func statusRows(_ status: BibDbGit.VersionStatus) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 14) {
                Label("分支 \(status.branch.isEmpty ? "（未知）" : status.branch)",
                      systemImage: "arrow.triangle.branch")
                    .font(.callout)
                Text(status.dbDirty ? "库文件：有未提交改动" : "库文件：干净")
                    .font(.callout)
                    .foregroundStyle(status.dbDirty ? Color.orange : Color.secondary)
                Text(unpushedText(status))
                    .font(.callout)
                    .foregroundStyle(status.unpushedCount > 0 ? Color.orange : Color.secondary)
                Spacer()
                if status.seconds > 0 {
                    Text("读取用时 \(BibDbUI.seconds(status.seconds))")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
            Text("仓库：\(status.repo)")
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(.tertiary)
                .textSelection(.enabled)
                .lineLimit(1).truncationMode(.middle)
            if status.recentCommits.isEmpty {
                Text("最近库提交：暂无（还没有引用库提交）。")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text("最近库提交（最多 3 条）：").font(.caption).foregroundStyle(.secondary)
                ForEach(status.recentCommits) { commit in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(commit.sha)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                        Text(commit.subject.isEmpty ? "（无说明）" : commit.subject)
                            .font(.caption)
                            .lineLimit(1).truncationMode(.middle)
                        Spacer(minLength: 0)
                    }
                    .padding(.leading, 8)
                }
            }
        }
    }

    private func unpushedText(_ status: BibDbGit.VersionStatus) -> String {
        if status.unpushedCount < 0 { return "未推送：未知（无 upstream）" }
        return "未推送提交：\(status.unpushedCount) 条"
    }
}
