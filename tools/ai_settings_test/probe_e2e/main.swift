import Foundation

// ============================================================================
// ⑤ 检索探针的**真跑**验证（第 69 条）—— 起真子进程、跑真引擎、打真端点。
//
// 为什么不放进 `../main.swift`：那个 harness 刻意**不启动任何子进程、不发任何请求**
// （它只验纯函数与源码级接线）。而 ⑤ 的全部价值恰恰在「真把引擎跑起来」——所以单独一份，
// 对着 `tools/mock_llm_server.py` 这个**本地假端点**跑三种剧本：
//
//   responses_search        → ✓ 支持（收到 web_search_call）
//   responses_ignore_search → ✗ 不支持（**DeepSeek flash 的真实行为**：收到 tools 也静默忽略）
//   responses_reject_search → ✗ 不支持（HTTP 400 + 端点原话）
//
// 第 70 条又加了一段：用**自定义工具名**（`web_search_preview`）再跑一次支持检索的剧本，
// 并从 mock 的 `GET /__stats` 里核对 `n_tools_requested` 记到的就是这个名字 ——
// 证明「界面上填的工具名 → ⑤ 的 argv → 引擎 → 端点」整条链路真的通了
// （只验「拼出来的 argv 长什么样」是不够的）。
//
// 跑法（先起 mock，再按下面的目录形状编译运行）：
//
//   cd <仓库根>
//   python3 tools/mock_llm_server.py --port 8891 --mode responses_search &
//   mkdir -p /tmp/probe_run/bin
//   ln -sfn "$PWD/Engine" /tmp/probe_run/Engine      # ← 关键：见下面「为什么这样放」
//   cd sources-mac
//   swiftc -o /tmp/probe_run/bin/probe_e2e \
//     Core/ViewModels.swift Core/Support.swift Core/Log.swift Core/EditorsPage.swift \
//     Features/InfoCheckFeature.swift Features/ConvertFeature.swift Features/CheckBibFeature.swift \
//     Views/SettingsTab.swift ../tools/ai_settings_test/probe_e2e/main.swift
//   /tmp/probe_run/bin/probe_e2e
//
// **为什么把可执行文件放在 `/tmp/probe_run/bin/` 并做那个符号链接**：
// `ConvertFeature.locateTool()` 的查找顺序是「UserDefaults 里的自定义路径 → app 包内 →
// **从 app 所在目录逐级向上找 `Engine/convert.py`**」。命令行可执行文件没有 app 包，
// 用它**最后那一条**（向上找）才不用往 `UserDefaults` 写东西 —— 而写 `UserDefaults` 会在
// 用户目录里留下 `~/Library/Preferences/<可执行文件名>.plist`（实测过：键删掉之后
// cfprefsd 还会补一个空 plist，`HOME=` 重定向也拦不住）。验收不该在用户目录里留垃圾。
// 可执行文件在 `/tmp/probe_run/bin/` 时，`bundleURL` = `/tmp/probe_run/bin`，
// 于是它先找 `/tmp/probe_run/Engine/convert.py` —— 正是上面那个符号链接。
//
// ⚠️ 全程只打 127.0.0.1，不出网、不花额度、不写用户目录、不碰 ai.json。
// ⚠️ 密钥是**假的**：它只是为了证明「密钥走子进程环境变量、不进 argv」这条接线。
// ============================================================================

// 这个环境变量是**可选**的旁路（`SLCN_ENGINE_CONVERT` 只被本文件用来打印诊断；
// 真正决定 `locateTool()` 命中的是上面那个目录形状）。给了就核对一下它是否真的存在。
if let hint = ProcessInfo.processInfo.environment["SLCN_ENGINE_CONVERT"],
   !FileManager.default.fileExists(atPath: hint) {
    print("❌ SLCN_ENGINE_CONVERT 指向的文件不存在：\(hint)")
    exit(2)
}

let located = ConvertFeature.locateTool() ?? "<nil>"
print("引擎（locateTool()）：\(located)")
print("解释器：\(ConvertFeature.locatePython(toolDir: (located as NSString).deletingLastPathComponent) ?? "<nil>")")
guard located != "<nil>" else {
    print("❌ 没找到 Engine/convert.py：请按文件头的说明做那个符号链接（/tmp/probe_run/Engine）")
    exit(2)
}
print("")

let base = "http://127.0.0.1:8891/v1"
let modes = ["responses_search", "responses_ignore_search", "responses_reject_search"]
print("端点：\(base)（mock_llm_server.py）")
print("")

for want in modes {
    // 用 curl 切 mock 的剧本（不引 URLSession，避免和被测代码混在一起）。
    _ = Shell.capture("/usr/bin/curl", ["-s", "-XPOST", "127.0.0.1:8891/__mode",
                                        "-d", "{\"mode\":\"\(want)\"}"])
    print("### mock 模式 \(want)")
    let (probe, note) = AIConnectionTester.runSearchProbe(base: base, key: "sk-FAKE-1234",
                                                          model: "mock-model")
    print(AIConnectionTester.searchProbeLine(probe, engineNote: note,
                                             protocolWanted: .responses, searchEnabled: true))
    print("   [parsed] verdict=\(probe?.verdict ?? "<nil>") http=\(probe?.httpCode ?? -1) "
          + "n_search_calls=\(probe?.nSearchCalls ?? -1) urls=\(probe?.urls ?? [])")
    print("")
}

// ── 第 70 条：自定义工具名也要一路走到端点 ──────────────────────────────────
_ = Shell.capture("/usr/bin/curl", ["-s", "-XPOST", "127.0.0.1:8891/__mode",
                                    "-d", "{\"mode\":\"responses_search\"}"])
print("### 第 70 条：自定义工具名 web_search_preview（mock 模式 responses_search）")
let (probe70, note70) = AIConnectionTester.runSearchProbe(
    base: base, key: "sk-FAKE-1234", model: "mock-model",
    knobs: AIEngineKnobs(apiProtocol: .responses, webSearch: true,
                         searchTool: "web_search_preview", responsesPath: "/responses"))
print(AIConnectionTester.searchProbeLine(probe70, engineNote: note70,
                                         protocolWanted: .responses, searchEnabled: true))
print("   [parsed] verdict=\(probe70?.verdict ?? "<nil>") endpoint=\(probe70?.endpoint ?? "<nil>") "
      + "n_search_calls=\(probe70?.nSearchCalls ?? -1)")
let stats = Shell.capture("/usr/bin/curl", ["-s", "127.0.0.1:8891/__stats"])?.output ?? ""
if let d = stats.data(using: .utf8),
   let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
   let tools = obj["n_tools_requested"] as? [String: Int] {
    print("   [mock /__stats] n_tools_requested=\(tools)")
    print(tools["web_search_preview"] != nil
          ? "   ✅ 端点收到的工具名就是配置的那个（web_search_preview）"
          : "   ❌ 端点上没看到自定义工具名")
} else {
    print("   ❌ 取不到 mock 的 /__stats")
}
print("")
