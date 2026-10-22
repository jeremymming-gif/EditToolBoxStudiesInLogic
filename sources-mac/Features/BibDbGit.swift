import Foundation

/// B-190：引用库的 **git 版本层**（Foundation-only；**禁止** import SwiftUI / Combine）。
///
/// 设计卡：`EditToolBox-notes/drafts/specs/b190-bibdb-git-vcs.md` §3.1 / §3.4。
///
/// 为什么要独立成文件
/// ------------------
/// ① BRIEF §2 的单文件行数闸：`BibDbFeature.swift` 已到 3000 行上限，git 逻辑必须单独落；
/// ② 它只做两件事 —— **拼 git 命令 + 解析输出**，与「引擎 argv/JSON」层分开；
/// ③ 纯解析/构造函数可以**直接**在第 7 台断言（不起子进程、不读真实库）。
///
/// 红线（写死在代码里，第 7 台用源码断言守）
/// ------------------------------------------
/// · commit **只** `git add bib_db/bibliography.jsonl`，提交也用 pathspec 限定这一个文件
///   （工作区里其它脏文件绝不带进提交）；
/// · **绝不自动 push / pull / fetch / 切分支**（`push` 只能由用户点「推送…」显式调用）；
/// · 回退 = `git checkout HEAD~1 -- bib_db/bibliography.jsonl` + **新 commit**，
///   不 hard reset、不 force、不 push；
/// · git 不可用 / 非 repo / 库文件不在约定路径 / 身份未配置 → 返回中文 `error`，
///   **绝不抛异常、绝不回滚已经写成功的数据**（fail-soft）。
enum BibDbGit {

    /// 库文件相对仓库根的固定路径（与引擎 `slcn/bibdb.py: DB_DIRNAME/DB_FILENAME` 一致）。
    static let dbRelativePath = "bib_db/bibliography.jsonl"

    /// 用绝对路径找 git / shasum：GUI 的 PATH 与终端不同，不走 `env`。
    static let gitPath = "/usr/bin/git"
    static let shasumPath = "/usr/bin/shasum"

    /// git 身份未配置时的统一文案（写库成功、提交失败，绝不回滚数据）。
    static let identityErrorText = "git 身份未配置（user.name / user.email）："
        + "本次写库已成功，但未生成版本提交；配置好后可用「提交…」补提交。"

    // MARK: - 数据模型（第 7 台纯内存构造）

    /// 一条库提交（`git log --oneline` 的一行）。
    struct CommitEntry: Identifiable, Equatable {
        var sha = ""          // --oneline 的短 sha
        var subject = ""      // 提交说明
        var id: String { sha + "|" + subject }
    }

    /// 版本库状态（「版本库状态区」的数据源）。
    struct VersionStatus {
        var repo = ""
        var branch = ""
        var dbDirty = false
        /// 未推送提交数；`-1` = 未知（无 upstream / 命令失败，见 `unpushedError`）。
        var unpushedCount = -1
        /// fail-soft 文案：无 upstream 等（不阻断状态区其余信息展示）。
        var unpushedError: String?
        var recentCommits: [CommitEntry] = []
        /// 致命错误：git 缺失 / 非 repo / 库不在约定路径；此时其余字段为空。
        var error: String?
        var seconds: Double = 0
        var loaded: Bool { error == nil && !repo.isEmpty }
    }

    /// 写库后自动 commit（或手动 commit）的结果。
    struct CommitResult {
        var committed = false
        var sha = ""
        var message = ""
        /// 提交后的未推送提交数（`-1` = 未知）。UI fail-soft 展示。
        var unpushedCount = -1
        var error: String?
        var seconds: Double = 0
    }

    /// 推送结果（失败必须带 stderr 摘要，见 `failureText`）。
    struct PushResult {
        var pushed = false
        var output = ""
        var error: String?
        var seconds: Double = 0
    }

    /// 回退到上一个提交的结果。
    struct RevertResult {
        var reverted = false
        var commitSha = ""
        var dbSha256 = ""
        var error: String?
        var seconds: Double = 0
    }

    /// 仓库定位结果（`failed` 带中文原因，绝不 throws）。
    enum RepoLookup {
        case ok(repo: String)
        case failed(String)
    }

    // MARK: - 纯函数：命令构造（第 7 台直接断言）

    /// `git -C <db 目录> rev-parse --show-toplevel`：从**库文件所在目录**定位仓库。
    static func repoRootArguments(dbDirectory: String) -> [String] {
        ["-C", dbDirectory, "rev-parse", "--show-toplevel"]
    }

    /// commit 前的 add：**只有库文件**（`--` 之后是 pathspec）。
    static func addArguments() -> [String] {
        ["add", "--", dbRelativePath]
    }

    /// commit：`--` 限定只提交库文件（其它已暂存文件绝不带进这次提交）。
    static func commitArguments(message: String) -> [String] {
        ["commit", "-m", message, "--", dbRelativePath]
    }

    /// `git status --porcelain -- <库文件>`：只看库文件脏否。
    static func statusArguments() -> [String] {
        ["status", "--porcelain", "--", dbRelativePath]
    }

    /// 最近 3 条库提交。
    static func logArguments(limit: Int = 3) -> [String] {
        ["log", "--oneline", "-\(max(1, limit))", "--", dbRelativePath]
    }

    static func branchArguments() -> [String] {
        ["rev-parse", "--abbrev-ref", "HEAD"]
    }

    /// 未推送提交数：`@{upstream}..HEAD`（无 upstream → 命令退出非 0 → fail-soft）。
    static func aheadArguments() -> [String] {
        ["rev-list", "--count", "@{upstream}..HEAD"]
    }

    /// 推送（**仅用户显式点「推送…」时调用**；不带 force）。
    static func pushArguments() -> [String] {
        ["push"]
    }

    /// 回退第一步：把库文件恢复到上一个提交（`HEAD~1`），**不** hard reset。
    static func revertCheckoutArguments() -> [String] {
        ["checkout", "HEAD~1", "--", dbRelativePath]
    }

    /// 库文件的提交数（回退前校验「至少 2 个」）。
    static func commitCountArguments() -> [String] {
        ["rev-list", "--count", "HEAD", "--", dbRelativePath]
    }

    /// 回退前校验：当前 HEAD 里有库文件（`cat-file -e HEAD:<路径>`）。
    static func headFileCheckArguments() -> [String] {
        ["cat-file", "-e", "HEAD:" + dbRelativePath]
    }

    static func headShaArguments() -> [String] {
        ["rev-parse", "HEAD"]
    }

    /// 取库文件字节 sha256（Foundation-only：用系统 `shasum -a 256`，不引 CryptoKit）。
    static func shasumArguments(_ path: String) -> [String] {
        ["-a", "256", path]
    }

    // MARK: - 纯函数：消息 / 解析

    /// sha 前 12 位（短 sha 原样；空串仍空）。
    static func shortSha(_ sha: String) -> String {
        let text = sha.trimmingCharacters(in: .whitespacesAndNewlines)
        return text.count > 12 ? String(text.prefix(12)) : text
    }

    /// 写库后自动 commit 的说明（设计卡 §3.1 固定格式）：
    /// `引用库：<动作摘要>（记录 N 条；sha <before12>→<after12>）`
    /// `note` 非空时追加第二行 `备注：…`（第一行格式不变）。
    static func commitMessage(action: String, recordCount: Int, shaBefore: String,
                              shaAfter: String, note: String = "") -> String {
        let what = action.trimmingCharacters(in: .whitespacesAndNewlines)
        let label = what.isEmpty ? "写库" : what
        let line = "引用库：\(label)（记录 \(max(0, recordCount)) 条；sha "
            + "\(shortSha(shaBefore))→\(shortSha(shaAfter))）"
        let extra = note.trimmingCharacters(in: .whitespacesAndNewlines)
        return extra.isEmpty ? line : line + "\n备注：" + extra
    }

    /// 回退提交的说明（设计卡 §1.5「新 commit『回退…』」）。
    static func revertMessage(shaBefore: String, shaAfter: String) -> String {
        "引用库：回退到上一个提交（sha \(shortSha(shaBefore))→\(shortSha(shaAfter))）"
    }

    /// 输出里的第一行非空内容（去掉首尾空白）。
    static func firstNonEmptyLine(_ output: String) -> String {
        output.split(separator: "\n", omittingEmptySubsequences: true)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .first(where: { !$0.isEmpty }) ?? ""
    }

    /// `rev-parse --show-toplevel` 的输出 → 仓库绝对路径；`fatal:` 等错误 → nil。
    static func parseRepoRoot(_ output: String) -> String? {
        let line = firstNonEmptyLine(output)
        guard !line.isEmpty else { return nil }
        let lower = line.lowercased()
        guard !lower.hasPrefix("fatal:"), !lower.contains("not a git repository") else {
            return nil
        }
        return line
    }

    /// 解析 `rev-parse HEAD` 的结果（7+ 位十六进制）。
    static func parseHeadSha(_ output: String) -> String? {
        let line = firstNonEmptyLine(output)
        guard line.count >= 7, line.allSatisfy({ $0.isHexDigit }) else { return nil }
        return line
    }

    /// `git status --porcelain -- <库文件>`：空（或只有空白）= 无改动。
    static func isDBDirty(_ porcelain: String) -> Bool {
        !porcelain.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// `rev-list --count @{upstream}..HEAD` → 整数；无 upstream 时命令失败（调用方 fail-soft）。
    static func parseUnpushedCount(_ output: String) -> Int? {
        parseCommitCount(output)
    }

    /// `rev-list --count` → 非负整数。
    static func parseCommitCount(_ output: String) -> Int? {
        let line = firstNonEmptyLine(output)
        guard let value = Int(line), value >= 0 else { return nil }
        return value
    }

    /// `rev-parse --abbrev-ref HEAD` → 分支名（detached 时是 `HEAD`）。
    static func parseBranch(_ output: String) -> String {
        let line = firstNonEmptyLine(output)
        guard !line.lowercased().hasPrefix("fatal:") else { return "" }
        return line
    }

    /// `git log --oneline` → 最多 `limit` 条（sha + 说明；无说明时 subject 为空）。
    static func parseLogOneline(_ output: String, limit: Int = 3) -> [CommitEntry] {
        var rows: [CommitEntry] = []
        for raw in output.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty else { continue }
            let parts = line.split(separator: " ", maxSplits: 1,
                                   omittingEmptySubsequences: true)
            guard let sha = parts.first.map(String.init), !sha.isEmpty else { continue }
            let subject = parts.count > 1
                ? parts[1].trimmingCharacters(in: .whitespaces) : ""
            rows.append(CommitEntry(sha: sha, subject: subject))
            if rows.count >= max(1, limit) { break }
        }
        return rows
    }

    /// `shasum -a 256` → 64 位小写 sha；格式不对 → nil。
    static func parseShasum(_ output: String) -> String? {
        let line = firstNonEmptyLine(output)
        guard !line.isEmpty else { return nil }
        let token = line.split(whereSeparator: { $0 == " " || $0 == "\t" })
            .first.map(String.init) ?? ""
        guard token.count == 64, token.allSatisfy({ $0.isHexDigit }) else { return nil }
        return token.lowercased()
    }

    /// git 输出里的一行是不是错误行（push/commit 失败摘要挑行用）。
    static func isGitErrorLine(_ line: String) -> Bool {
        let lower = line.lowercased()
        return lower.hasPrefix("fatal:") || lower.hasPrefix("error:")
            || lower.contains("rejected") || lower.contains("permission denied")
            || lower.contains("no such remote") || lower.contains("failed to push")
            || lower.contains("could not read") || lower.contains("does not appear to be a git repository")
    }

    /// 失败摘要：优先从第一条错误行起取 `maxLines` 行；没有错误行就取末尾几行。
    /// 给 UI 直接展示（推送失败必须带这段 stderr 摘要）。
    static func failureText(_ output: String, maxLines: Int = 3) -> String {
        let lines = output.split(separator: "\n", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        guard !lines.isEmpty else { return "（git 没有输出）" }
        let count = max(1, maxLines)
        if let index = lines.firstIndex(where: { isGitErrorLine($0) }) {
            return lines[index...].prefix(count).joined(separator: " ｜ ")
        }
        return lines.suffix(count).joined(separator: " ｜ ")
    }

    /// git 身份未配置的典型输出（fail-soft：写库成功、提交失败）。
    static func isIdentityError(_ output: String) -> Bool {
        let lower = output.lowercased()
        return lower.contains("please tell me who you are")
            || lower.contains("unable to auto-detect email address")
            || lower.contains("empty ident name")
    }

    // MARK: - 纯函数：路径

    /// 规范化 + 解析符号链接（/tmp → /private/tmp 也一致）。
    static func normalizePath(_ path: String) -> String {
        let text = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return "" }
        return URL(fileURLWithPath: text).standardizedFileURL
            .resolvingSymlinksInPath().path
    }

    /// 库文件相对仓库根的路径；不在仓库内 → nil（调用方据此 fail-soft）。
    static func relativeDBPath(repoRoot: String, dbPath: String) -> String? {
        let root = normalizePath(repoRoot)
        let db = normalizePath(dbPath)
        guard !root.isEmpty, !db.isEmpty else { return nil }
        if db == root { return "" }
        let prefix = root.hasSuffix("/") ? root : root + "/"
        guard db.hasPrefix(prefix) else { return nil }
        return String(db.dropFirst(prefix.count))
    }

    // MARK: - 进程层（同步；只许后台队列调；测试台不触发）

    private static func runGit(repo: String, _ arguments: [String]) -> Shell.CommandResult? {
        Shell.capture(gitPath, ["-C", repo] + arguments)
    }

    /// 库文件字节 sha256（失败返回空串，不抛异常）。
    static func fileSha256(_ path: String) -> String {
        let normalized = normalizePath(path)
        guard !normalized.isEmpty,
              let result = Shell.capture(shasumPath, shasumArguments(normalized)),
              result.status == 0 else { return "" }
        return parseShasum(result.output) ?? ""
    }

    /// 从**库文件所在仓库**定位并校验（`bib_db/bibliography.jsonl` 固定相对路径）。
    static func locateRepo(db: String) -> RepoLookup {
        let path = normalizePath(db)
        guard !path.isEmpty else {
            return .failed("缺少引用库文件路径：无法定位 git 仓库（写库不受影响）。")
        }
        let directory = (path as NSString).deletingLastPathComponent
        guard !directory.isEmpty, PathKit.isDirectory(directory) else {
            return .failed("引用库目录不存在：\(directory)（版本层不可用；写库不受影响）。")
        }
        guard let result = Shell.capture(gitPath, repoRootArguments(dbDirectory: directory)) else {
            return .failed("未找到 git（\(gitPath)）：版本层不可用（写库不受影响）。")
        }
        guard result.status == 0, let repo = parseRepoRoot(result.output) else {
            return .failed("引用库不在 git 仓库内（\(directory) 及其上级没有 .git）："
                           + "版本层不可用（写库不受影响）。")
        }
        guard let relative = relativeDBPath(repoRoot: repo, dbPath: path) else {
            return .failed("库文件不在仓库目录内（\(path)）：版本层不可用（写库不受影响）。")
        }
        guard relative == dbRelativePath else {
            return .failed("库文件不在约定的 \(dbRelativePath)（实际 \(relative)）："
                           + "拒绝执行版本层操作（写库不受影响）。")
        }
        return .ok(repo: repo)
    }

    private static func queryUnpushedCount(repo: String) -> Int {
        guard let result = runGit(repo: repo, aheadArguments()), result.status == 0,
              let count = parseUnpushedCount(result.output) else { return -1 }
        return count
    }

    /// 只提交库文件：脏否 → add → commit（pathspec 限定）→ HEAD sha + 未推送数。
    private static func commitDBFile(repo: String, message: String) -> CommitResult {
        var out = CommitResult()
        out.message = message
        guard let status = runGit(repo: repo, statusArguments()) else {
            out.error = "未找到 git（\(gitPath)）：版本层不可用（写库不受影响）。"
            return out
        }
        guard status.status == 0 else {
            out.error = "读不到库文件状态：" + failureText(status.output)
            return out
        }
        guard isDBDirty(status.output) else {
            return out                              // 无改动：不产生空 commit（error = nil）
        }
        guard let add = runGit(repo: repo, addArguments()) else {
            out.error = "git add 无法启动（版本层不可用；写库不受影响）。"
            return out
        }
        guard add.status == 0 else {
            out.error = "git add 失败：" + failureText(add.output)
            return out
        }
        guard let commit = runGit(repo: repo, commitArguments(message: message)) else {
            out.error = "git commit 无法启动（版本层不可用；写库不受影响）。"
            return out
        }
        guard commit.status == 0 else {
            out.error = isIdentityError(commit.output)
                ? identityErrorText
                : "git commit 失败：" + failureText(commit.output)
            return out
        }
        out.committed = true
        if let head = runGit(repo: repo, headShaArguments()), head.status == 0 {
            out.sha = parseHeadSha(head.output) ?? ""
        }
        out.unpushedCount = queryUnpushedCount(repo: repo)
        return out
    }

    /// 版本库状态：分支 / 库文件脏否 / 未推送提交数 / 最近 3 条库提交。
    /// 全部 fail-soft：定位失败 → `error`；无 upstream → `unpushedCount=-1` + `unpushedError`。
    static func versionStatus(db: String) -> VersionStatus {
        let startedAt = Date()
        var out = VersionStatus()
        switch locateRepo(db: db) {
        case .failed(let reason):
            out.error = reason
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        case .ok(let repo):
            out.repo = repo
        }
        if let branch = runGit(repo: out.repo, branchArguments()), branch.status == 0 {
            out.branch = parseBranch(branch.output)
        }
        if let status = runGit(repo: out.repo, statusArguments()), status.status == 0 {
            out.dbDirty = isDBDirty(status.output)
        }
        if let ahead = runGit(repo: out.repo, aheadArguments()), ahead.status == 0,
           let count = parseUnpushedCount(ahead.output) {
            out.unpushedCount = count
        } else {
            out.unpushedCount = -1
            out.unpushedError = "未设置 upstream（无法统计未推送提交数）；可先 `git push -u`。"
        }
        if let log = runGit(repo: out.repo, logArguments()), log.status == 0 {
            out.recentCommits = parseLogOneline(log.output)
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 写库成功后自动本地 commit（**绝不 push**）。
    ///
    /// - Parameters:
    ///   - db: 库文件路径（`<笔记仓>/bib_db/bibliography.jsonl`；GUI 真点用 /tmp clone 同布局）。
    ///   - action: 动作摘要（如「编辑条目」「合并记录」「字段归位（3 条）」）。
    ///   - recordCount: 本次写库影响的记录数（edit=1、merge=合并条数、批量=选中条数）。
    ///   - shaBefore/shaAfter: 写库前后的库字节 sha256（message 里取前 12 位）。
    ///   - note: 可选备注（message 第二行）。
    /// **同步；只许后台队列调**。失败只填 `error`，绝不影响已写库的数据。
    static func commitDBVersion(db: String, action: String, recordCount: Int,
                                shaBefore: String, shaAfter: String,
                                note: String = "") -> CommitResult {
        let startedAt = Date()
        var out = CommitResult()
        switch locateRepo(db: db) {
        case .failed(let reason):
            out.error = reason
        case .ok(let repo):
            let message = commitMessage(action: action, recordCount: recordCount,
                                        shaBefore: shaBefore, shaAfter: shaAfter,
                                        note: note)
            out = commitDBFile(repo: repo, message: message)
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 手动补提交（用户点「提交…」；二次确认后调用）：只提交库文件。
    static func manualCommit(db: String, message: String) -> CommitResult {
        let startedAt = Date()
        var out = CommitResult()
        switch locateRepo(db: db) {
        case .failed(let reason):
            out.error = reason
        case .ok(let repo):
            let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
            out = commitDBFile(repo: repo,
                               message: text.isEmpty ? "引用库：手动提交" : text)
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 推送（**仅用户显式点「推送…」+二次确认后调用**）。失败带 stderr 摘要。
    static func push(db: String) -> PushResult {
        let startedAt = Date()
        var out = PushResult()
        switch locateRepo(db: db) {
        case .failed(let reason):
            out.error = reason
        case .ok(let repo):
            guard let result = runGit(repo: repo, pushArguments()) else {
                out.error = "未找到 git（\(gitPath)）：无法推送。"
                out.seconds = Date().timeIntervalSince(startedAt)
                return out
            }
            out.output = result.output
            if result.status == 0 {
                out.pushed = true
            } else {
                out.error = "推送失败：" + failureText(result.output)
            }
        }
        out.seconds = Date().timeIntervalSince(startedAt)
        return out
    }

    /// 回退到上一个库提交：
    /// 校验（≥2 个库提交、HEAD 有库文件）→ `checkout HEAD~1 -- 库文件` → **新 commit**（「回退…」）。
    /// **不 hard reset、不 push**；成功后返回新 commit sha 与库新 sha（供 UI 重载）。
    static func revertToPreviousCommit(db: String) -> RevertResult {
        let startedAt = Date()
        var out = RevertResult()
        func finish() -> RevertResult {
            out.seconds = Date().timeIntervalSince(startedAt)
            return out
        }
        switch locateRepo(db: db) {
        case .failed(let reason):
            out.error = reason
            return finish()
        case .ok(let repo):
            guard let counted = runGit(repo: repo, commitCountArguments()),
                  counted.status == 0, let count = parseCommitCount(counted.output) else {
                out.error = "读不到库文件的提交历史：回退已取消（库未被改动）。"
                return finish()
            }
            guard count >= 2 else {
                out.error = "库文件只有 \(count) 个提交：没有「上一个提交」可回退。"
                return finish()
            }
            guard let head = runGit(repo: repo, headFileCheckArguments()),
                  head.status == 0 else {
                out.error = "当前 HEAD 里没有库文件：无法回退（库未被改动）。"
                return finish()
            }
            let path = normalizePath(db)
            let beforeSha = fileSha256(path)
            guard !beforeSha.isEmpty else {
                out.error = "读不到库文件 sha（回退已取消；库未被改动）。"
                return finish()
            }
            guard let checkout = runGit(repo: repo, revertCheckoutArguments()) else {
                out.error = "未找到 git（\(gitPath)）：回退已取消（库未被改动）。"
                return finish()
            }
            guard checkout.status == 0 else {
                out.error = "git checkout HEAD~1 失败：" + failureText(checkout.output)
                return finish()
            }
            guard let status = runGit(repo: repo, statusArguments()), status.status == 0 else {
                out.error = "回退后读不到库文件状态：请检查后手动提交。"
                return finish()
            }
            guard isDBDirty(status.output) else {
                out.error = "上一个提交里的库内容与当前相同：无需回退（未产生新提交）。"
                return finish()
            }
            let afterSha = fileSha256(path)
            guard !afterSha.isEmpty else {
                out.error = "回退后读不到库文件 sha：请检查后手动提交。"
                return finish()
            }
            let message = revertMessage(shaBefore: beforeSha, shaAfter: afterSha)
            let committed = commitDBFile(repo: repo, message: message)
            guard committed.committed else {
                out.error = committed.error
                    ?? "回退后的新提交没有生成：库文件已回到上一个提交的内容，请手动提交。"
                return finish()
            }
            out.reverted = true
            out.commitSha = committed.sha
            out.dbSha256 = fileSha256(path)
            return finish()
        }
    }
}
