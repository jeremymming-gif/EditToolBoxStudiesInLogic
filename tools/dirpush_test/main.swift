// 「目录摘要推送」GUI 纯逻辑测试台（B-225 Phase B，headless）。
//
// **编译的是生产源码本身**（`sources-mac/Features/DirPushFeature.swift` + `Core/Support.swift`）。
// 不开 GUI、不起子进程、不联网、不读本机用户目录 —— 只断言：
//   ① 参数怎么拼（冻结的 CLI 契约 §3）；
//   ② 引擎 `--json` 的冻结字段能不能解回来（缺字段/坏 JSON 的容错口径）；
//   ③ 界面派生状态（摘要取到率/年份自动推导/默认输出根/仓库内输出根启发式/年度勾选）；
//   ④ 源码级接线（MainWindow 新页 + 视图里真的调了这两个函数）。
//
// 跑法（在 sources-mac 下）：
//     swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/dirpushmc \
//       -o /tmp/dirpush_test_harness \
//       Core/Support.swift Core/Log.swift Features/DirPushFeature.swift ../tools/dirpush_test/main.swift
//     /tmp/dirpush_test_harness        # 期望：SUMMARY: N PASS / 0 FAIL
//
// 统一入口是 `bash tools/run_swift_tests.sh --only dirpush`（模块缓存/编译参数都在那儿）。

import Foundation

var pass = 0
var fail = 0

func check(_ name: String, _ ok: Bool, _ detail: String = "") {
    if ok {
        pass += 1
        print("PASS  \(name)" + (detail.isEmpty ? "" : "  \(detail)"))
    } else {
        fail += 1
        print("FAIL  \(name)  \(detail)")
    }
}

let here = URL(fileURLWithPath: #filePath)
let toolbox = here.deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent()
let sources = toolbox.appendingPathComponent("sources-mac")
let tools = toolbox.appendingPathComponent("tools")

func readSource(_ rel: String) -> String {
    (try? String(contentsOf: sources.appendingPathComponent(rel), encoding: .utf8)) ?? ""
}

// ── 冻结 JSON 夹具（**合成数据**：不含任何真实作者/标题/摘要；版式字段按契约 §1）──
let issueFixture = """
{"schema":1,"ok":true,"mode":"issue","dry_run":true,
 "outputs":[],
 "issue":{"year":2026,"number":5,"vol":19,"total":90,
          "header_lines":["逻辑学研究 2026 年第 5 期",
                          "第 19 卷（总第 90 期）2026 年 10 月 18 日出版",
                          "双月刊 2008 年创刊"],
          "with_en_toc":false,
          "column":{"label":"专栏","name":"示例专栏研究","host":"示例主持人"}},
 "counts":{"zh_entries":8,"en_entries":0,"articles":8,"abstracts_ok":8,"abstracts_missing":0},
 "warnings":["WARNING: 示例告警"],
 "missing":["示例缺项"],
 "annual":null}
"""

let annualFixture = """
{"schema":1,"ok":true,"mode":"annual","dry_run":true,
 "outputs":[],
 "issue":{"year":2025,"number":1,"vol":18,"total":84,
          "header_lines":["逻辑学研究 2025 年第 1 期",
                          "第 18 卷（总第 84 期）2025 年 2 月 18 日出版",
                          "双月刊 2008 年创刊"],
          "with_en_toc":false,"column":null},
 "counts":{"zh_entries":9,"en_entries":0,"articles":9,"abstracts_ok":9,"abstracts_missing":0},
 "warnings":[],"missing":[],
 "annual":{"year":2025,"included":[1,2,3],"excluded":[4],"missing_issues":[5],
           "issues":[{"number":1,"with_en_toc":false,"status":"ok"},
                     {"number":2,"with_en_toc":false,"status":"ok"},
                     {"number":3,"with_en_toc":true,"status":"ok"},
                     {"number":4,"with_en_toc":false,"status":"unavailable"}]}}
"""

let generateFixture = """
{"schema":1,"ok":true,"mode":"issue","dry_run":false,
 "outputs":["/tmp/输出根/2026 年第 5 期/逻辑学研究2026年第5期目录摘要.docx"],
 "issue":{"year":2026,"number":5,"vol":19,"total":90,
          "header_lines":["A","B","C"],"with_en_toc":false,"column":null},
 "counts":{"zh_entries":8,"en_entries":0,"articles":8,"abstracts_ok":7,"abstracts_missing":1},
 "warnings":[],"missing":[]}
"""

// ── (a) 参数拼装（冻结 CLI 契约 §3）────────────────────────────────────────
print("── (a) 参数拼装 ──")
let baseIssueArgs = DirPushFeature.arguments(issue: "/稿件/2026年第5期(90)", outRoot: "/tmp/out")
check("(a) 当期基础 → `--issue <期目录> --out <输出根>`",
      baseIssueArgs == ["--issue", "/稿件/2026年第5期(90)", "--out", "/tmp/out"],
      "\(baseIssueArgs)")
check("(a) 当期双语 → 末尾追加 `--with-en-toc`",
      DirPushFeature.arguments(issue: "/期", outRoot: "/out", withEnToc: true)
      == ["--issue", "/期", "--out", "/out", "--with-en-toc"])
check("(a) 当期不勾英文 → 不传 `--with-en-toc`",
      !baseIssueArgs.contains("--with-en-toc"))
check("(a) 年度 → 传 `--annual`，且**不**传 `--with-en-toc`（年度体例按期还原）",
      DirPushFeature.arguments(issue: "/期", outRoot: "/out", withEnToc: true, annual: true)
      .contains("--annual")
      && !DirPushFeature.arguments(issue: "/期", outRoot: "/out", withEnToc: true, annual: true)
      .contains("--with-en-toc"))
check("(a) 年度 + 年份 → `--year 2025`",
      DirPushFeature.arguments(issue: "/期", outRoot: "/out", annual: true, year: "2025")
      .suffix(2) == ["--year", "2025"])
check("(a) 年度 + 勾选期 → `--include-issues 1,2,3`（升序去重）",
      DirPushFeature.arguments(issue: "/期", outRoot: "/out", annual: true,
                               includeIssues: [3, 1, 2, 1])
      .suffix(2) == ["--include-issues", "1,2,3"])
check("(a) 年度 + 空年份/空勾选 → 两个 flag 都不传（空 = 引擎按全部 found）",
      !DirPushFeature.arguments(issue: "/期", outRoot: "/out", annual: true,
                                year: "   ", includeIssues: [])
      .contains("--year")
      && !DirPushFeature.arguments(issue: "/期", outRoot: "/out", annual: true,
                                   year: "", includeIssues: [])
      .contains("--include-issues"))
check("(a) 预览 argv = 基础 + `--json --dry-run`（不写盘）",
      DirPushFeature.previewArguments(issue: "/期", outRoot: "/out", annual: true, year: "2025")
      .suffix(2) == ["--json", "--dry-run"])
check("(a) 生成 argv = 基础 + `--json`，**不含** `--dry-run`",
      DirPushFeature.generateArguments(issue: "/期", outRoot: "/out", withEnToc: true)
      .suffix(1) == ["--json"]
      && !DirPushFeature.generateArguments(issue: "/期", outRoot: "/out").contains("--dry-run"))
check("(a) 路径含空格/中文时数组原样传（不做 shell 插值）",
      DirPushFeature.arguments(issue: "/tmp/期 90/", outRoot: "/tmp/输出 根")
      == ["--issue", "/tmp/期 90/", "--out", "/tmp/输出 根"])
check("(a) includeIssuesArgument：升序去重 → `\"1,2,3\"`",
      DirPushFeature.includeIssuesArgument([3, 2, 1, 2]) == "1,2,3")
check("(a) includeIssuesArgument：空/全非法 → nil（不传 flag）",
      DirPushFeature.includeIssuesArgument([]) == nil
      && DirPushFeature.includeIssuesArgument([0, -2]) == nil)

// ── (b) 引擎定位 ─────────────────────────────────────────────────────────
print("\n── (b) 引擎定位（EngineLocator 统一解析）──")
check("(b) 从 convert.py 所在目录找到 dirpush.py",
      DirPushFeature.locateTool(convertTool: toolbox.appendingPathComponent("Engine/convert.py").path)
      == toolbox.appendingPathComponent("Engine/dirpush.py").path)
check("(b) 反例：没给 convert 入口 → nil（不拼半截命令）",
      DirPushFeature.locateTool(convertTool: nil) == nil)
check("(b) 反例：引擎目录里没有 dirpush.py → nil",
      DirPushFeature.locateTool(convertTool: "/tmp/不存在/convert.py") == nil)

// ── (c) JSON 契约解析（GUI 只读这些字段）────────────────────────────────
print("\n── (c) JSON 契约解析 ──")
let issuePreview = DirPushFeature.parse(issueFixture)
check("(c) 完整对象：schema/ok/mode/dry_run 解回",
      issuePreview?.schema == 1 && issuePreview?.isOK == true
      && issuePreview?.mode == "issue" && issuePreview?.dryRun == true)
check("(c) 期头 3 行原样", issuePreview?.headerLines.count == 3
      && issuePreview?.headerLines.first == "逻辑学研究 2026 年第 5 期",
      "\(issuePreview?.headerLines.count ?? -1) 行")
check("(c) 期次字段 year/number/vol/total",
      issuePreview?.issue?.year == 2026 && issuePreview?.issue?.number == 5
      && issuePreview?.issue?.vol == 19 && issuePreview?.issue?.total == 90)
check("(c) 专栏/主持人字段（label/name/host）",
      issuePreview?.issue?.column?.label == "专栏"
      && issuePreview?.issue?.column?.name == "示例专栏研究"
      && issuePreview?.issue?.column?.host == "示例主持人")
check("(c) 体例 with_en_toc 解回（默认 false）", issuePreview?.withEnTocDefault == false)
check("(c) counts 五个字段解回",
      issuePreview?.counts?.zhEntries == 8 && issuePreview?.counts?.enEntries == 0
      && issuePreview?.counts?.articles == 8 && issuePreview?.counts?.abstractsOk == 8
      && issuePreview?.counts?.abstractsMissing == 0)
check("(c) warnings / missing 数组解回",
      issuePreview?.warnings == ["WARNING: 示例告警"] && issuePreview?.missing == ["示例缺项"])
check("(c) 当期模式 annual = null", issuePreview?.annual == nil)
check("(c) column = null → nil（不炸、其它字段仍在）",
      DirPushFeature.parse(#"{"ok":true,"issue":{"header_lines":["A"],"column":null}}"#)?.issue?.column == nil
      && DirPushFeature.parse(#"{"ok":true,"issue":{"header_lines":["A"],"column":null}}"#)?.headerLines == ["A"])
check("(c) annual 对象解析（year/included/excluded/missing_issues/issues）",
      DirPushFeature.parse(annualFixture)?.annual?.included == [1, 2, 3]
      && DirPushFeature.parse(annualFixture)?.annual?.excluded == [4]
      && DirPushFeature.parse(annualFixture)?.annual?.missingIssues == [5]
      && DirPushFeature.parse(annualFixture)?.annual?.issues.count == 4)
check("(c) 缺字段容错：只有 ok → 解析成功、数组为空（不整份丢弃）",
      DirPushFeature.parse(#"{"ok":true}"#)?.headerLines.isEmpty == true
      && DirPushFeature.parse(#"{"ok":true}"#)?.warnings.isEmpty == true)
check("(c) 坏 JSON → nil（界面必须明确报错，不许当空预览）",
      DirPushFeature.parse("这不是 JSON") == nil
      && DirPushFeature.parse(#"{"ok":tru"#) == nil)
check("(c) 合并管道输出（日志 + JSON 混在一起）→ 从后往前抠出对象",
      DirPushFeature.decodeJSON(in: "采集日志…\nWARNING: 示例\n" + issueFixture + "\n收尾日志\n")
      == issuePreview)
let multilineJSON = DirPushFeature.decodeJSON(in: "日志\n{\n  \"ok\": true,\n  \"mode\": \"issue\",\n  \"outputs\": [\"/tmp/a.docx\"]\n}\n日志\n")
check("(c) 多行 JSON 兜底：换行排版也能解", multilineJSON?.outputs == ["/tmp/a.docx"])
check("(c) 生成结果 outputs 路径解回", DirPushFeature.parse(generateFixture)?.outputs
      == ["/tmp/输出根/2026 年第 5 期/逻辑学研究2026年第5期目录摘要.docx"])

// ── (d) 界面派生状态 ─────────────────────────────────────────────────────
print("\n── (d) 界面派生状态 ──")
check("(d) 摘要取到率显示 `x/y`（8/8）",
      DirPushFeature.abstractsRate(issuePreview) == "8/8")
check("(d) 0 篇不除零 → `0/0`",
      DirPushFeature.abstractsRate(DirPushFeature.parse(#"{"counts":{"articles":0,"abstracts_ok":0}}"#)) == "0/0"
      && DirPushFeature.abstractsRate(nil) == "0/0")
check("(d) 年份自动推导 = 预览 issue.year",
      DirPushFeature.autoYear(from: issuePreview) == "2026"
      && DirPushFeature.autoYear(from: DirPushFeature.parse(annualFixture)) == "2025")
check("(d) 年份缺失 → 空串（界面留空给用户填）",
      DirPushFeature.autoYear(from: DirPushFeature.parse(#"{"ok":true}"#)) == "")
let defaultRoot = DirPushFeature.defaultOutRoot
let expandedRoot = DirPushFeature.defaultOutRootPath
let home = FileManager.default.homeDirectoryForCurrentUser.path
check("(d) 默认输出根字符串 = `~/Documents/sysu/编辑部/目录摘要推送`",
      defaultRoot == "~/Documents/sysu/编辑部/目录摘要推送")
check("(d) 默认输出根展开为绝对路径（~ 已展开、去掉尾部 /）",
      expandedRoot == home + "/Documents/sysu/编辑部/目录摘要推送"
      && !expandedRoot.contains("~"), expandedRoot)
check("(d) 记忆键名冻结：`dirpush.outRoot`",
      DirPushFeature.outRootDefaultsKey == "dirpush.outRoot")
let fakeHome = "/Users/示例用户"
check("(d) 仓库内输出根启发式：GitHub 路径 → true",
      DirPushFeature.isRepoOutputRoot(fakeHome + "/Documents/GitHub/某仓/out", home: fakeHome)
      && DirPushFeature.isRepoOutputRoot(fakeHome + "/Documents/GitHub", home: fakeHome))
check("(d) 反例：普通路径 / 前缀相近目录 → false",
      !DirPushFeature.isRepoOutputRoot("/tmp/b225/B/out", home: fakeHome)
      && !DirPushFeature.isRepoOutputRoot(fakeHome + "/Documents/GitHubBackup/x", home: fakeHome)
      && !DirPushFeature.isRepoOutputRoot("/Volumes/外置盘/out", home: fakeHome))
check("(d) 仓库外不弹确认；仓库内给确认文案",
      DirPushFeature.repoOutputRootWarning(for: "/tmp/b225/B/out", home: fakeHome) == nil
      && DirPushFeature.repoOutputRootWarning(
          for: fakeHome + "/Documents/GitHub/某仓/out", home: fakeHome)?.contains("代码仓内") == true)
let annualRows = DirPushFeature.annualRows(from: DirPushFeature.parse(annualFixture))
check("(d) 年度清单：默认全选 included、体例显示、不可用期不可选",
      annualRows.count == 4
      && annualRows.filter { $0.selected }.map { $0.number } == [1, 2, 3]
      && annualRows.first { $0.number == 3 }?.styleText == "中英文对照"
      && annualRows.first { $0.number == 1 }?.styleText == "中文"
      && annualRows.first { $0.number == 4 }?.isFound == false)
check("(d) 年度勾选序列化：勾掉的期不传、升序去重",
      DirPushFeature.selectedIssues(from: annualRows) == [1, 2, 3]
      && DirPushFeature.includeIssuesArgument(DirPushFeature.selectedIssues(from: annualRows)) == "1,2,3")
var unchecked = annualRows
unchecked = unchecked.map { row in
    var r = row
    if r.number == 2 { r.selected = false }
    return r
}
check("(d) 勾掉第 2 期 → `--include-issues 1,3`",
      DirPushFeature.includeIssuesArgument(DirPushFeature.selectedIssues(from: unchecked)) == "1,3")
let missingText = DirPushFeature.missingIssuesText(from: DirPushFeature.parse(annualFixture))
check("(d) 缺期警告只警告不可选（missing_issues → 文案）",
      missingText?.contains("第 5 期") == true
      && DirPushFeature.missingIssuesText(from: issuePreview) == nil)
check("(d) 年度汇总标题含年份与将纳入期数",
      DirPushFeature.annualSummaryText(from: DirPushFeature.parse(annualFixture), selected: 3)
      == "2025 年共 4 期，将纳入 3 期")

// ── (e) 源码级接线（不编译视图：需要 SwiftUI 上下文）────────────────────
print("\n── (e) 源码级接线 ──")
let mainWindow = readSource("Views/MainWindow.swift")
let view = readSource("Views/DirPushView.swift")
let runner = (try? String(contentsOf: tools.appendingPathComponent("run_swift_tests.sh"),
                          encoding: .utf8)) ?? ""
check("(e) MainWindow：NavPage 加 `dirpush`、标题「目录摘要推送」、SF Symbol",
      mainWindow.contains("dirpush") && mainWindow.contains("return \"目录摘要推送\"")
      && mainWindow.contains("return \"megaphone\""))
check("(e) MainWindow：侧栏「整期装配」组 `.cleantemp` 之后 = `.dirpush`（「本期出刊」页已删）",
      mainWindow.contains("[.infocheck, .compile, .toc, .cleantemp, .dirpush]"))
check("(e) MainWindow：ZStack 里真的挂了 `DirPushView(log: log)` 且走 `.pageVisible`",
      mainWindow.contains("DirPushView(log: log)") && mainWindow.contains(".pageVisible(selection == .dirpush)"))
check("(e) 视图真的调了预览/生成两个引擎入口（不自己写判据）",
      view.contains("DirPushFeature.preview(") && view.contains("DirPushFeature.run("))
check("(e) 视图用统一拖拽组件（DroppablePathField + PathDrop.receive），输出根可拖",
      view.contains("DroppablePathField(") && view.contains("PathDrop.receive("))
check("(e) 生成前仓库内输出根要确认（NSAlert）：「打开」用 NSWorkspace",
      view.contains("repoOutputRootWarning(for:") && view.contains("alert.runModal()")
      && view.contains("NSWorkspace.shared.open("))
check("(e) 测试总跑手：SUITES 含 dirpush + 新增 run_one 行（8 台）",
      runner.contains("run_one dirpush") && runner.contains("dirpush_test/main.swift")
      && (runner.contains("8 台") || runner.contains("全部 8 台")))

print("\nSUMMARY: \(pass) PASS / \(fail) FAIL")
exit(fail == 0 ? 0 : 1)
