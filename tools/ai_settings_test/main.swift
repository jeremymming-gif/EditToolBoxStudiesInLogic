
// ── 路径助手：**不再写死某台机器的绝对路径**（2026-10-15，拆仓前修）────────────
// 从本文件的**编译期路径**（`#filePath`）往上三级推出项目根：
//     <项目根>/tools/ai_settings_test/main.swift
// 拆仓改名时**只改下面这一行**（`Sources` → `sources-mac`），其余全用它。
let SRC_DIR_NAME = "sources-mac"
let PROJ = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent()      // …/tools/ai_settings_test
    .deletingLastPathComponent()      // …/tools
    .deletingLastPathComponent()      // …/<项目根>
func projFile(_ rel: String) -> String { PROJ.appendingPathComponent(rel).path }
func srcFile(_ rel: String) -> String { projFile(SRC_DIR_NAME + "/" + rel) }
func readSrc(_ rel: String) -> String {
    (try? String(contentsOfFile: srcFile(rel), encoding: .utf8)) ?? ""
}
/// B-230：同族多文件（`Views/ConvertTab*.swift` 这类**非递归** glob，与 `build.sh` 的 4 条
/// `*.swift` glob 同口径）。拆分后一个页面的源码散在同一层多个文件里，源码级断言必须看整族；
/// **顺序固定** = 按文件名排序（`ConvertTab.swift` 排在同族其余文件之前）。
func srcGlobFiles(_ pattern: String) -> [String] {
    guard pattern.contains("*") else { return [pattern] }
    let ns = pattern as NSString
    let parts = ns.lastPathComponent.components(separatedBy: "*")
    guard parts.count == 2 else { return [] }
    let all = (try? FileManager.default.contentsOfDirectory(atPath: srcFile(ns.deletingLastPathComponent))) ?? []
    return all.filter { $0.hasPrefix(parts[0]) && $0.hasSuffix(parts[1]) }.sorted()
}
/// 把整族源文件按固定顺序拼起来读。任一文件读不到 → 整体返回 ""（正向断言必然红，不假 PASS）。
func readSrcGlob(_ pattern: String) -> String {
    guard pattern.contains("*") else { return readSrc(pattern) }
    let dir = (pattern as NSString).deletingLastPathComponent
    let names = srcGlobFiles(pattern)
    guard !names.isEmpty else { return "" }
    let texts = names.map {
        try? String(contentsOfFile: srcFile(dir + "/" + $0), encoding: .utf8)
    }
    guard !texts.contains(where: { $0 == nil }) else { return "" }
    return texts.compactMap { $0 }.joined(separator: "\n")
}

import Foundation
import CryptoKit          // (h1) 只用来算 ai.json 的 sha256（纯本地，不碰网络）

// ============================================================================
// Headless verification of the AI-settings upgrade, compiled against the REAL
// production sources (unmodified):
//
//   sources-mac/Core/ViewModels.swift     AIProviderPreset / AIThinkingMode /
//                                     AIMaxTokens / AIEngineKnobs / AppSettings
//   sources-mac/Features/ConvertFeature.swift   engineEnvironment / knobWarnings /
//                                     effectiveKnobs（两个旋钮的「现取 or 回落」）
//   sources-mac/Features/CoopFeature.swift  第 55 批两段式协同的纯逻辑（argv / 暂存目录
//                                     发现 / 建议 jsonl 摘要 / 路径标准化）
//   sources-mac/Views/ConvertTab.swift    run(): settings.aiEngineKnobs → run(knobs:)
//                                     （源码级接线检查，见 T-g）
//   sources-mac/Views/SettingsTab.swift   AIConnectionTester's pure checklist builders
//
//   sources-mac/Features/CheckBibFeature.swift  「文献回读校验」的参数拼装（phase 2c 起**真的编译**：
//                                    体检 / 并回 / 列表三种 argv 与粘贴模式的报告落点都是纯函数）
//
// ⚠️ NO NETWORK. The URLSession probe path (`AIConnectionTester.test` / `send`)
//    is never called: it is exercised only through the pure Reply→text
//    functions with canned HTTP responses. Everything on disk goes under /tmp.
// ⚠️ NO CHILD PROCESS either: `Shell.run` / `Shell.capture` are never invoked here,
//    so the stdin pipe wiring is only checked at source level (see j10–j21).
// ============================================================================

var passCount = 0
var failCount = 0

func section(_ title: String) {
    print("")
    print("================================================================")
    print("== \(title)")
    print("================================================================")
}

func evidence(_ label: String, _ value: String) {
    print("   [evidence] \(label): \(value)")
}

func check(_ id: String, _ desc: String, _ ok: Bool, _ detail: String) {
    let tag = ok ? "PASS" : "FAIL"
    if ok { passCount += 1 } else { failCount += 1 }
    print("\(tag)  [\(id)] \(desc)")
    print("        -> \(detail)")
}

func mode(_ path: String) -> String {
    guard let a = try? FileManager.default.attributesOfItem(atPath: path),
          let n = a[.posixPermissions] as? NSNumber else { return "??? (missing)" }
    return String(format: "%03o", n.intValue & 0o777)
}

/// 文件内容的 sha256（十六进制）—— 用来证明「一个字节都没动」。
/// 纯本地计算（CryptoKit），与 `shasum -a 256` 逐字符一致（本 harness 实测过）。
func sha256(_ path: String) -> String {
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else { return "<unreadable>" }
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

/// mtime（秒 + 毫秒）—— 「没落盘」的第二重证据（sha 相同已足够，mtime 是旁证）。
func modTime(_ path: String) -> String {
    guard let a = try? FileManager.default.attributesOfItem(atPath: path),
          let d = a[.modificationDate] as? Date else { return "<missing>" }
    return String(format: "%.3f", d.timeIntervalSince1970)
}

/// 取 `signature` 之后、到第一次「缩进 4 空格的右花括号」之间的源码
/// （用于源码级检查：某个函数体里**不许**出现某调用）。取不到就返回哨兵串。
func body(after signature: String, in text: String) -> String {
    guard let start = text.range(of: signature) else { return "<signature not found>" }
    let rest = text[start.upperBound...]
    guard let end = rest.range(of: "\n    }") else { return String(rest) }
    return String(rest[..<end.lowerBound])
}

func exists(_ path: String) -> Bool { FileManager.default.fileExists(atPath: path) }

func rawFile(_ path: String) -> String {
    guard let d = try? Data(contentsOf: URL(fileURLWithPath: path)) else { return "<unreadable>" }
    return String(data: d, encoding: .utf8) ?? "<non-utf8 \(d.count) bytes>"
}

func mkdir(_ path: String, _ perm: Int) {
    try? FileManager.default.createDirectory(atPath: path, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: perm])
    try? FileManager.default.setAttributes([.posixPermissions: perm], ofItemAtPath: path)
}

func writeRaw(_ path: String, _ text: String, perm: Int = 0o600) {
    try? text.data(using: .utf8)!.write(to: URL(fileURLWithPath: path))
    try? FileManager.default.setAttributes([.posixPermissions: perm], ofItemAtPath: path)
}

func wipe(_ path: String) { try? FileManager.default.removeItem(atPath: path) }

func json(_ text: String) -> [String: Any] {
    (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] ?? [:]
}

/// sorted "k=v" dump of a dictionary, so dictionary ordering can never hide a diff
func dump(_ d: [String: String]) -> String {
    d.keys.sorted().map { "\($0)=\(d[$0]!)" }.joined(separator: " | ")
}

// ---------------------------------------------------------------------------
// Environment check: every SLCN_LLM_* variable must be neutralised, otherwise
// the harness would be testing the ambient shell rather than the code.
// ---------------------------------------------------------------------------
section("T0  ambient environment")
let ambient = ["SLCN_LLM_BASE", "SLCN_LLM_KEY", "SLCN_LLM_MODEL",
               "SLCN_LLM_JSON", "SLCN_LLM_MAX_TOKENS", "SLCN_LLM_THINKING"]
for k in ambient {
    evidence("shell env \(k)", ProcessInfo.processInfo.environment[k] ?? "<unset>")
}
print("NOTE  `engineEnvironment` returns a *delta* dict only (what ConvertFeature hands to")
print("      Shell.run), so ambient SLCN_LLM_* cannot leak into the assertions below;")
print("      PYTHONPATH presence is driven by a vendor/ dir we create ourselves.")

let base = URL(fileURLWithPath: "/tmp/ai_settings_test", isDirectory: true)
wipe(base.path)
mkdir(base.path, 0o700)

print("")
print("HARNESS: headless test of the AI-settings upgrade (production types)")
print("production sources: sources-mac/Core/ViewModels.swift, sources-mac/Features/ConvertFeature.swift,")
print("                    sources-mac/Views/SettingsTab.swift  (compiled as-is, same module)")
print("scratch root      : \(base.path)   (real user dir NEVER touched)")
print("network           : NONE (no URLSession call is made by this harness)")

// ===========================================================================
// (a) presets → exact base/model pair
// ===========================================================================
section("T-a  预设 → 地址/模型名（AIProviderPreset.applied）")

let expectedPresets: [(AIProviderPreset, String, String)] = [
    (.deepseek, "https://api.deepseek.com/v1", "deepseek-flash"),
    (.qwen, "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    (.glm, "https://open.bigmodel.cn/api/paas/v4", "glm-4"),
    (.kimi, "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
]
var presetAllOK = true
for (p, wantBase, wantModel) in expectedPresets {
    let r = p.applied(base: "OLD-BASE", model: "OLD-MODEL")
    let ok = r.base == wantBase && r.model == wantModel
    presetAllOK = presetAllOK && ok
    check("a-\(p.rawValue)", "\(p.title) → base/model 逐字一致",
          ok, "base=\"\(r.base)\"  model=\"\(r.model)\"")
    evidence("  stored raw value", p.rawValue)
}

// 自定义 = 一点都不动
let custom = AIProviderPreset.custom
let c = custom.applied(base: "https://keep.me/v1", model: "keep-model")
check("a-custom", "自定义 → 两个字段原样保留（不覆盖用户输入）",
      c.base == "https://keep.me/v1" && c.model == "keep-model",
      "base=\"\(c.base)\" model=\"\(c.model)\"")

// 预设枚举里没有「密钥」这个概念：只有 base/model 两个可选字段
check("a-nokey", "预设结构上碰不到密钥（applied 只吃/只吐 base+model）",
      presetAllOK && AIProviderPreset.allCases.count == 5,
      "5 presets = \(AIProviderPreset.allCases.map { $0.rawValue })；applied 签名 = (base:model:) -> (base:model:)")
evidence("deepseek base includes version path", AIProviderPreset.deepseek.base ?? "<nil>")
evidence("retired names absent from presets",
         AIProviderPreset.allCases.compactMap { $0.model }
            .filter { $0 == "deepseek-chat" || $0 == "deepseek-reasoner" }
            .isEmpty ? "yes (no deepseek-chat/-reasoner)" : "NO — stale model name present")

// ===========================================================================
// (b) 思考模式 three states → SLCN_LLM_THINKING
// ===========================================================================
section("T-b  思考模式三态 → SLCN_LLM_THINKING")

let thinkingCases: [(AIThinkingMode, String?)] = [
    (.followEndpoint, nil),
    (.disabled, "0"),
    (.enabled, "1"),
]
var thinkingOK = true
for (m, want) in thinkingCases {
    let got = m.envValue
    let ok = got == want
    thinkingOK = thinkingOK && ok
    check("b-\(m.rawValue)", "「\(m.title)」→ \(want.map { "SLCN_LLM_THINKING=\"\($0)\"" } ?? "**unset**（不设置该变量）")",
          ok, "envValue = \(got == nil ? "nil (unset)" : "\"\(got!)\"")")
}

// 经 ConvertFeature 落到真正的环境字典里（含 unset 的情况）
let envDir = base.appendingPathComponent("t_b_env").path
wipe(envDir); mkdir(envDir, 0o700)
var optB = ConvertFeature.Options()
optB.useAI = true
optB.llmBase = "https://api.deepseek.com/v1"
optB.llmModel = "deepseek-flash"
optB.llmKey = "sk-THINKING-TEST"
var envB: [String: String] = [:]
for (m, want) in thinkingCases {
    let knobs = AIEngineKnobs(thinking: m, maxTokens: "2048")
    let env = ConvertFeature.engineEnvironment(optB, toolDir: envDir, knobs: knobs)
    let got = env["SLCN_LLM_THINKING"]
    let ok = got == want
    thinkingOK = thinkingOK && ok
    check("b-env-\(m.rawValue)", "engineEnvironment: \(m.rawValue) → SLCN_LLM_THINKING \(want == nil ? "不存在" : "= \"\(want!)\"")",
          ok, "dict = { \(dump(env)) }")
    envB = env
}
evidence("keys of the LAST dict in the loop（enabled）", "\(envB.keys.sorted())")
check("b-consistency", "三态互不相同（不是三个值映射到同一个结果）",
      Set(thinkingCases.map { $0.1 ?? "<unset>" }).count == 3,
      "values = \(thinkingCases.map { $0.1 ?? "<unset>" })")

// ===========================================================================
// (c) 输出上限 parsing
// ===========================================================================
section("T-c  输出上限（AIMaxTokens.parse / envValue / hint）")

let maxCases: [(String, String)] = [
    ("2048", "value(\"2048\")"),
    ("", "unset"),
    ("0", "value(\"0\")"),
    ("abc", "invalid"),
]
var maxOK = true
for (input, want) in maxCases {
    let got = AIMaxTokens.parse(input)
    var ok = false
    switch (want, got) {
    case ("value(\"2048\")", .value(let v)): ok = (v == "2048")
    case ("unset", .unset): ok = true
    case ("value(\"0\")", .value(let v)): ok = (v == "0")
    case ("invalid", .invalid(let h)): ok = !h.isEmpty
    default: ok = false
    }
    maxOK = maxOK && ok
    let rendered: String
    switch got {
    case .unset: rendered = "unset（不设置 SLCN_LLM_MAX_TOKENS）"
    case .value(let v): rendered = "value(\"\(v)\")"
    case .invalid(let h): rendered = "invalid(\(h))"
    }
    check("c-\(input.isEmpty ? "empty" : input)", "输入 \"\(input)\" → \(want)",
          ok, "parse = \(rendered)")
}

// envValue / hint side of the same table
let envOf: [(String, String?)] = [("2048", "2048"), ("", nil), ("0", "0"), ("abc", nil)]
for (input, want) in envOf {
    let got = AIMaxTokens.envValue(input)
    check("c-env-\(input.isEmpty ? "empty" : input)",
          "envValue(\"\(input)\") → \(want.map { "\"\($0)\"" } ?? "nil")",
          got == want, "envValue = \(got == nil ? "nil" : "\"\(got!)\"")")
}
let hintABC = AIMaxTokens.hint("abc")
evidence("hint(\"abc\")", hintABC ?? "<nil>")
check("c-hint-abc", "abc 被拒绝且**带一句中文提示**（不是静默丢弃）",
      hintABC != nil && !(hintABC!.isEmpty) && hintABC!.contains("abc"),
      "hint = \(hintABC ?? "<nil>")")
check("c-hint-ok", "合法值不显示红字（hint == nil）",
      AIMaxTokens.hint("2048") == nil && AIMaxTokens.hint("0") == nil
        && AIMaxTokens.hint("") == nil,
      "hint(\"2048\")=\(String(describing: AIMaxTokens.hint("2048"))) hint(\"0\")=\(String(describing: AIMaxTokens.hint("0"))) hint(\"\")=\(String(describing: AIMaxTokens.hint("")))")
// 边界：负数 / 小数 / 溢出 / 空白
for (input, isInvalid) in [("-5", true), ("2048.0", true), ("1e3", true),
                           ("99999999999999999999", true), ("  2048  ", false),
                           ("02048", false), ("+64", false)] {
    let p = AIMaxTokens.parse(input)
    var invalid = false
    if case .invalid = p { invalid = true }
    check("c-edge-\(input.trimmingCharacters(in: .whitespaces))",
          "边界 \"\(input)\" → \(isInvalid ? "拒绝" : "接受")",
          invalid == isInvalid, "parse = \(p)")
}

// ===========================================================================
// (d) ai.json round-trip + backward compatibility + 0600/0700
// ===========================================================================
section("T-d  ai.json：两个新字段的存/读 + 旧文件的向后兼容")

// ---- d1: fresh save, both new fields non-default -------------------------
let d1 = base.appendingPathComponent("d1_roundtrip").path
wipe(d1)
let s1 = AppSettings(storeDirectory: URL(fileURLWithPath: d1, isDirectory: true))
s1.aiBase = "https://api.deepseek.com/v1"
s1.aiKey = "sk-ROUNDTRIP-1234"
s1.aiModel = "deepseek-flash"
s1.aiThinking = .disabled
s1.aiMaxTokens = "4096"
let saved1 = s1.saveAI()
let f1 = d1 + "/ai.json"
let raw1 = rawFile(f1)
evidence("ai.json raw", "\n----8<----\n\(raw1)\n---->8----")
evidence("file mode / dir mode", "\(mode(f1)) / \(mode(d1))")
evidence("saveAI()", "\(saved1); aiStatus=\"\(s1.aiStatus)\"")
let obj1 = json(raw1)
check("d1-save", "saveAI() 写入 thinking/maxTokens 两个新键，取值与界面一致",
      saved1 && (obj1["thinking"] as? String) == "off" && (obj1["maxTokens"] as? String) == "4096",
      "thinking=\"\(obj1["thinking"] ?? "<nil>")\" maxTokens=\"\(obj1["maxTokens"] ?? "<nil>")\"")
check("d1-perm", "仍是 0600 文件 / 0700 目录（新增字段没有放松权限）",
      mode(f1) == "600" && mode(d1) == "700", "file=\(mode(f1)) dir=\(mode(d1))")
let s1b = AppSettings(storeDirectory: URL(fileURLWithPath: d1, isDirectory: true))
check("d1-reload", "新实例读回：base/key/model/thinking/maxTokens 逐字一致，aiDirty=false",
      s1b.aiBase == s1.aiBase && s1b.aiKey == s1.aiKey && s1b.aiModel == s1.aiModel
        && s1b.aiThinking == .disabled && s1b.aiMaxTokens == "4096" && !s1b.aiDirty,
      "thinking=\(s1b.aiThinking.rawValue) maxTokens=\"\(s1b.aiMaxTokens)\" dirty=\(s1b.aiDirty)")

// every tri-state survives a disk round-trip
var triOK = true
for m in AIThinkingMode.allCases {
    let inst = AppSettings(storeDirectory: URL(fileURLWithPath: d1, isDirectory: true))
    inst.aiThinking = m
    _ = inst.saveAI()
    let back = AppSettings(storeDirectory: URL(fileURLWithPath: d1, isDirectory: true))
    triOK = triOK && (back.aiThinking == m)
    evidence("round-trip \(m.rawValue)", "on disk=\"\(json(rawFile(f1))["thinking"] ?? "<nil>")\" → read back \(back.aiThinking.rawValue)")
}
check("d1-tristate", "思考模式三态逐个存盘/读回都不变形", triOK, "3/3 round-tripped")

// ---- d2: backward compatibility (old file: only base/key/model) ----------
section("T-d2  旧 ai.json（没有新键）→ 默认值；再保存会补上新键")
let d2 = base.appendingPathComponent("d2_old_file").path
wipe(d2); mkdir(d2, 0o700)
writeRaw(d2 + "/ai.json", "{\n  \"base\" : \"https://old.example/v1\",\n  \"key\" : \"sk-OLD-0001\",\n  \"model\" : \"old-model\"\n}", perm: 0o600)
evidence("old ai.json (byte-identical to what the previous version wrote)",
         "\n----8<----\n\(rawFile(d2 + "/ai.json"))\n---->8----")
let s2 = AppSettings(storeDirectory: URL(fileURLWithPath: d2, isDirectory: true))
evidence("after loadAI()", "base=\"\(s2.aiBase)\" key=\"\(s2.aiKey)\" model=\"\(s2.aiModel)\" thinking=\(s2.aiThinking.rawValue) maxTokens=\"\(s2.aiMaxTokens)\"")
check("d2-load", "旧文件加载成功：三个老字段照旧，两个新字段取默认（follow / 2048）",
      s2.aiBase == "https://old.example/v1" && s2.aiKey == "sk-OLD-0001"
        && s2.aiModel == "old-model"
        && s2.aiThinking == .followEndpoint && s2.aiMaxTokens == AIMaxTokens.defaultText,
      "thinking=\(s2.aiThinking.rawValue) maxTokens=\"\(s2.aiMaxTokens)\"（默认值）")
check("d2-clean", "刚读进来的旧配置不算「有未保存的修改」（aiDirty=false）",
      !s2.aiDirty, "aiDirty=\(s2.aiDirty)")
let saved2 = s2.saveAI()
let obj2 = json(rawFile(d2 + "/ai.json"))
check("d2-upgrade", "在旧文件上保存一次 → 新键被补上，老值未被改动",
      saved2 && (obj2["thinking"] as? String) == "follow"
        && (obj2["maxTokens"] as? String) == "2048"
        && (obj2["base"] as? String) == "https://old.example/v1"
        && (obj2["key"] as? String) == "sk-OLD-0001",
      "thinking=\"\(obj2["thinking"] ?? "<nil>")\" maxTokens=\"\(obj2["maxTokens"] ?? "<nil>")\" base unchanged=\((obj2["base"] as? String) == "https://old.example/v1")")

// 空白/空串语义：maxTokens "" 存成 ""（= unset），读回仍是 unset
s2.aiMaxTokens = ""
let saved2b = s2.saveAI()
let obj2b = json(rawFile(d2 + "/ai.json"))
let s2c = AppSettings(storeDirectory: URL(fileURLWithPath: d2, isDirectory: true))
check("d2-empty", "输出上限留空 → 存成空串、读回仍是「不设置该变量」（不是变成 0）",
      saved2b && (obj2b["maxTokens"] as? String) == ""
        && s2c.aiMaxTokens.isEmpty && AIMaxTokens.envValue(s2c.aiMaxTokens) == nil,
      "on disk=\"\(obj2b["maxTokens"] ?? "<nil>")\" → reload \"\(s2c.aiMaxTokens)\" → envValue=\(String(describing: AIMaxTokens.envValue(s2c.aiMaxTokens)))")

// ---- d3: ill-typed / unknown stored values -------------------------------
section("T-d3  存坏的 ai.json（类型不对 / 认不出的取值）")
let d3 = base.appendingPathComponent("d3_bad_types").path
wipe(d3); mkdir(d3, 0o700)
let d3Cases: [(String, String)] = [
    ("thinking 是数字、maxTokens 是数字", "{\"base\":\"b\",\"key\":\"k\",\"model\":\"m\",\"thinking\":5,\"maxTokens\":2048}"),
    ("thinking 认不出、maxTokens 是空串", "{\"base\":\"b\",\"key\":\"k\",\"model\":\"m\",\"thinking\":\"maybe\",\"maxTokens\":\"\"}"),
    ("thinking 是 null、maxTokens 是 null", "{\"base\":\"b\",\"key\":\"k\",\"model\":\"m\",\"thinking\":null,\"maxTokens\":null}"),
]
for (i, c) in d3Cases.enumerated() {
    writeRaw(d3 + "/ai.json", c.1, perm: 0o600)
    let inst = AppSettings(storeDirectory: URL(fileURLWithPath: d3, isDirectory: true))
    let t = inst.aiThinking.rawValue
    let mt = inst.aiMaxTokens
    let sane = (t == "follow" || t == "off" || t == "on")
    print("   [extra] \(c.0): thinking=\(t) maxTokens=\"\(mt)\" (no crash=\(sane))")
    check("d3-\(i + 1)", "\(c.0)：不崩，且取值落在合法集合里", sane,
          "thinking=\(t) maxTokens=\"\(mt)\"")
}

// ---- d4: invalid 输出上限 is REJECTED at save time -----------------------
section("T-d4  非法「输出上限」在界面上被拒绝（不是静默写盘）")
let d4 = base.appendingPathComponent("d4_reject").path
wipe(d4); mkdir(d4, 0o700)
let s4 = AppSettings(storeDirectory: URL(fileURLWithPath: d4, isDirectory: true))
s4.aiBase = "https://api.deepseek.com/v1"
s4.aiKey = "sk-REJECT-9999"
s4.aiModel = "deepseek-flash"
s4.aiMaxTokens = "2048"
_ = s4.saveAI()
let before = rawFile(d4 + "/ai.json")
let beforeMode = mode(d4 + "/ai.json")
s4.aiMaxTokens = "abc"
let okReject = s4.saveAI()
let after = rawFile(d4 + "/ai.json")
evidence("saveAI() with maxTokens=abc", "returned \(okReject); aiStatus=\"\(s4.aiStatus)\"")
evidence("ai.json unchanged", "\(before == after) (mode \(beforeMode) → \(mode(d4 + "/ai.json")))")
check("d4-reject", "saveAI() 返回 false、aiStatus 是中文提示、盘上文件一个字节没动",
      !okReject && !s4.aiStatus.isEmpty && before == after && s4.aiStatus.contains("未保存"),
      "aiStatus=\"\(s4.aiStatus)\" fileUnchanged=\(before == after)")
check("d4-still-dirty", "被拒绝后仍显示「有未保存的修改」（不会假装存好了）",
      s4.aiDirty, "aiDirty=\(s4.aiDirty)")
// 规范化：02048 → 2048
s4.aiMaxTokens = "02048"
let okNorm = s4.saveAI()
check("d4-normalize", "保存时规范化（\"02048\" → \"2048\"，界面与文件一致）",
      okNorm && s4.aiMaxTokens == "2048"
        && (json(rawFile(d4 + "/ai.json"))["maxTokens"] as? String) == "2048",
      "in-memory=\"\(s4.aiMaxTokens)\" on disk=\"\(json(rawFile(d4 + "/ai.json"))["maxTokens"] ?? "<nil>")\"")
check("d4-normalize-dirty", "规范化后不再算「有未保存的修改」",
      !s4.aiDirty, "aiDirty=\(s4.aiDirty)")

// ---- d5: clearAI resets the two knobs（**只清界面内存**，不落盘 —— 见 (h)） ----
section("T-d5  清空（只清界面内存；磁盘不动 —— 2026-10-15 用户报的 bug，第 62 条）")
let s5 = AppSettings(storeDirectory: URL(fileURLWithPath: d4, isDirectory: true))
s5.aiThinking = .enabled
s5.aiMaxTokens = "0"
_ = s5.saveAI()
s5.clearAI()
check("d5-clear", "clearAI() 把两个旋钮还原成引擎默认（follow / 2048）—— 指的是**界面内存**里这一份",
      s5.aiThinking == .followEndpoint && s5.aiMaxTokens == AIMaxTokens.defaultText
        && !s5.aiConfigured,
      "in-memory thinking=\(s5.aiThinking.rawValue) maxTokens=\"\(s5.aiMaxTokens)\" configured=\(s5.aiConfigured)")
// ⚠️ 本条在 2026-10-15 之前断言的是「重建实例读盘 → follow/2048」—— 那其实是**把 bug 当规格钉住了**：
//    盘上之所以是默认值，是因为 clearAI() 末尾直接 saveAI() 把配置抹了。修好后清空不落盘，
//    重建实例读回的必然是**清空前保存的**那份配置（这才是用户要的语义）。
let s5b = AppSettings(storeDirectory: URL(fileURLWithPath: d4, isDirectory: true))
check("d5-disk-untouched", "语义变更：清空**不落盘** —— 另建实例重新读盘，读回的是清空前保存的 on / 0（配置还在）",
      s5b.aiThinking == .enabled && s5b.aiMaxTokens == "0" && s5b.aiConfigured,
      "reload from disk: thinking=\(s5b.aiThinking.rawValue) maxTokens=\"\(s5b.aiMaxTokens)\" configured=\(s5b.aiConfigured)")

// ---- d6: loadEngineKnobs (what ConvertFeature reads at launch) ----------
section("T-d6  AppSettings.loadEngineKnobs()（转换启动时读的那两个值）")
let d6 = base.appendingPathComponent("d6_knobs").path
wipe(d6)
let s6 = AppSettings(storeDirectory: URL(fileURLWithPath: d6, isDirectory: true))
s6.aiBase = "https://api.deepseek.com/v1"
s6.aiModel = "deepseek-flash"
s6.aiThinking = .enabled
s6.aiMaxTokens = "0"
_ = s6.saveAI()
let k6 = AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: d6, isDirectory: true))
check("d6", "读已保存的 ai.json → thinking=on / maxTokens=0",
      k6.thinking == .enabled && k6.maxTokens == "0",
      "thinking=\(k6.thinking.rawValue) maxTokens=\"\(k6.maxTokens)\"")
let k6b = AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: base.appendingPathComponent("d6_missing").path, isDirectory: true))
check("d6-missing", "目录/文件不存在时回落默认，不建目录、不崩",
      k6b == AIEngineKnobs() && !exists(base.appendingPathComponent("d6_missing").path),
      "knobs=\(k6b.thinking.rawValue)/\"\(k6b.maxTokens)\" dirCreated=\(exists(base.appendingPathComponent("d6_missing").path))")
let d6b = base.appendingPathComponent("d6_legacy").path
wipe(d6b); mkdir(d6b, 0o700)
writeRaw(d6b + "/ai.json", "{\"base\":\"b\",\"model\":\"m\"}", perm: 0o600)
let k6c = AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: d6b, isDirectory: true))
check("d6-legacy", "旧 ai.json → 两个旋钮取默认（不因为缺键就不设环境变量）",
      k6c == AIEngineKnobs(), "knobs=\(k6c.thinking.rawValue)/\"\(k6c.maxTokens)\"")

// ⚠ 这一条固定的是**回落路径**（没有界面状态的调用方）：这两个旋钮读的是**已保存**
//    的值。GUI 那条路（ConvertTab）现取现传，见下面 T-g。二者互不干扰。
let s6dirty = AppSettings(storeDirectory: URL(fileURLWithPath: d6, isDirectory: true))
s6dirty.aiThinking = .disabled            // 只改内存，不 saveAI()
let k6d = AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: d6, isDirectory: true))
check("d6-dirty", "回落路径：loadEngineKnobs 只认盘上的值（界面内存里改了它不受影响 —— GUI 已改为现取现传）",
      s6dirty.aiDirty && k6d.thinking == .enabled,
      "in-memory=\(s6dirty.aiThinking.rawValue) dirty=\(s6dirty.aiDirty) → loadEngineKnobs=\(k6d.thinking.rawValue)")

// ===========================================================================
// (e) ConvertFeature.engineEnvironment — exact SLCN_LLM_* keys
// ===========================================================================
section("T-e  engineEnvironment：环境变量增量逐键核对")

let toolPlain = base.appendingPathComponent("t_e_tool_plain").path     // no vendor/
let toolVendor = base.appendingPathComponent("t_e_tool_vendor").path   // with vendor/
wipe(toolPlain); mkdir(toolPlain, 0o700)
wipe(toolVendor); mkdir(toolVendor, 0o700)
mkdir(toolVendor + "/vendor", 0o700)

func configuredOptions(key: String = "sk-ENV-TEST-1234") -> ConvertFeature.Options {
    var o = ConvertFeature.Options()
    o.useAI = true
    o.llmBase = "https://api.deepseek.com/v1"
    o.llmModel = "deepseek-flash"
    o.llmKey = key
    return o
}

// e1: configured, thinking off, cap 2048, no vendor dir
let e1 = ConvertFeature.engineEnvironment(configuredOptions(), toolDir: toolPlain,
                                         knobs: AIEngineKnobs(thinking: .disabled, maxTokens: "2048"))
evidence("e1 configured/off/2048/no-vendor", dump(e1))
check("e1", "已配置 + 关闭思考 + 2048：恰好 SLCN_LLM_KEY/THINKING/MAX_TOKENS 三个键",
      e1 == ["SLCN_LLM_KEY": "sk-ENV-TEST-1234", "SLCN_LLM_THINKING": "0",
             "SLCN_LLM_MAX_TOKENS": "2048"],
      "{ \(dump(e1)) }")

// e2: same but with vendor/ present → PYTHONPATH added, SLCN_* untouched
let e2 = ConvertFeature.engineEnvironment(configuredOptions(), toolDir: toolVendor,
                                         knobs: AIEngineKnobs(thinking: .disabled, maxTokens: "2048"))
evidence("e2 configured/off/2048/with-vendor", dump(e2))
check("e2", "vendor/ 存在时多一个 PYTHONPATH，SLCN_LLM_* 不变",
      e2 == ["SLCN_LLM_KEY": "sk-ENV-TEST-1234", "SLCN_LLM_THINKING": "0",
             "SLCN_LLM_MAX_TOKENS": "2048", "PYTHONPATH": toolVendor + "/vendor"],
      "{ \(dump(e2)) }")

// e3: unset semantics (follow endpoint + empty cap)
let e3 = ConvertFeature.engineEnvironment(configuredOptions(), toolDir: toolVendor,
                                         knobs: AIEngineKnobs(thinking: .followEndpoint, maxTokens: ""))
evidence("e3 configured/follow/empty-cap", dump(e3))
check("e3", "跟随端点默认 + 留空上限：THINKING 与 MAX_TOKENS **两个键都不存在**（= 引擎用自己的默认）",
      e3 == ["SLCN_LLM_KEY": "sk-ENV-TEST-1234", "PYTHONPATH": toolVendor + "/vendor"],
      "{ \(dump(e3)) }")

// e4: 0 = no cap still sent
let e4 = ConvertFeature.engineEnvironment(configuredOptions(), toolDir: toolVendor,
                                         knobs: AIEngineKnobs(thinking: .enabled, maxTokens: "0"))
evidence("e4 configured/on/0", dump(e4))
check("e4", "开启思考 + 0：SLCN_LLM_THINKING=\"1\"、SLCN_LLM_MAX_TOKENS=\"0\"（不封顶也要发出去）",
      e4["SLCN_LLM_THINKING"] == "1" && e4["SLCN_LLM_MAX_TOKENS"] == "0",
      "{ \(dump(e4)) }")

// e5: not configured (checkbox off) → no SLCN_LLM_* at all
var optOff = ConvertFeature.Options()   // useAI == false, everything empty
optOff.llmKey = "sk-SHOULD-NOT-APPEAR"
let e5 = ConvertFeature.engineEnvironment(optOff, toolDir: toolVendor,
                                         knobs: AIEngineKnobs(thinking: .enabled, maxTokens: "0"))
evidence("e5 unconfigured (useAI=false)", dump(e5))
check("e5", "没勾「用 AI 处理参考文献」：一个 SLCN_LLM_* 都不设（与从前一致），只有 PYTHONPATH",
      e5 == ["PYTHONPATH": toolVendor + "/vendor"]
        && e5.keys.filter { $0.hasPrefix("SLCN_LLM_") }.isEmpty,
      "{ \(dump(e5)) }")

// e6: configured but empty key → no SLCN_LLM_KEY (engine falls back to Bearer none / env)
let e6 = ConvertFeature.engineEnvironment(configuredOptions(key: "   "), toolDir: toolPlain,
                                         knobs: AIEngineKnobs(thinking: .disabled, maxTokens: "2048"))
evidence("e6 configured/no-key", dump(e6))
check("e6", "密钥留空（本机端点）：不设 SLCN_LLM_KEY，其余照发",
      e6 == ["SLCN_LLM_THINKING": "0", "SLCN_LLM_MAX_TOKENS": "2048"],
      "{ \(dump(e6)) }")

// e7: invalid cap → not sent, but not silent either
let badKnobs = AIEngineKnobs(thinking: .disabled, maxTokens: "abc")
let e7 = ConvertFeature.engineEnvironment(configuredOptions(), toolDir: toolPlain, knobs: badKnobs)
let w7 = ConvertFeature.knobWarnings(badKnobs)
evidence("e7 invalid cap env", dump(e7))
evidence("e7 knobWarnings", w7.map { $0.replacingOccurrences(of: "\n", with: "⏎") }.joined())
check("e7", "非法上限：不发 SLCN_LLM_MAX_TOKENS（引擎将用 2048），但 knobWarnings 有一行说明（不静默）",
      e7["SLCN_LLM_MAX_TOKENS"] == nil && w7.count == 1
        && w7[0].contains("abc") && w7[0].contains("不设置"),
      "env={ \(dump(e7)) } warnings=\(w7.count) 行")
check("e7-ok", "合法上限没有多余的警告",
      ConvertFeature.knobWarnings(AIEngineKnobs(thinking: .disabled, maxTokens: "2048")).isEmpty
        && ConvertFeature.knobWarnings(AIEngineKnobs(thinking: .followEndpoint, maxTokens: "")).isEmpty,
      "warnings(2048)=\(ConvertFeature.knobWarnings(AIEngineKnobs(maxTokens: "2048")).count) warnings(\"\")=\(ConvertFeature.knobWarnings(AIEngineKnobs(maxTokens: "")).count)")

// e8: end-to-end GUI path: ai.json → loadEngineKnobs → engineEnvironment
let e8 = ConvertFeature.engineEnvironment(configuredOptions(key: "sk-E2E-4321"),
                                         toolDir: toolVendor,
                                         knobs: AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: d6, isDirectory: true)))
evidence("e8 ai.json → knobs → env", dump(e8))
check("e8", "整条链路（ai.json 里 thinking=on/0 → 环境变量 \"1\"/\"0\"）",
      e8["SLCN_LLM_THINKING"] == "1" && e8["SLCN_LLM_MAX_TOKENS"] == "0"
        && e8["SLCN_LLM_KEY"] == "sk-E2E-4321",
      "{ \(dump(e8)) }")

// e9: the two knobs must NOT become argv flags (env only) — checked at source level,
//     because `ConvertFeature.arguments` is private (deliberately not widened for tests).
let convSrc = readSrc("Features/ConvertFeature.swift")
check("e9", "两个新旋钮**不走 argv**（源码里 argv 拼装段没有 thinking/maxTokens，也没有新 flag）",
      !convSrc.contains("\"--llm-thinking\"") && !convSrc.contains("--llm-max-tokens")
        && convSrc.contains("let extra = engineEnvironment(o, toolDir: toolDir, knobs: knobs)"),
      "argv 段未新增 flag；run() 用 engineEnvironment(...) 拼环境变量（源码级检查，非运行时）")

// ===========================================================================
// (f) 体检 checklist: pure Reply → text builders (NO network)
// ===========================================================================
section("T-f  AIConnectionTester 的清单拼装（喂固定的 HTTP 响应，不发请求）")

let KEY = "sk-PROBE-SECRET-7777"
let chatOKBody = """
{"id":"x","choices":[{"index":0,"message":{"role":"assistant","content":"pong"},"finish_reason":"stop"}],
 "usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4,
          "completion_tokens_details":{"reasoning_tokens":0}}}
"""
let chatThinkingBody = """
{"choices":[{"message":{"content":""},"finish_reason":"length"}],
 "usage":{"prompt_tokens":3,"completion_tokens":8,"total_tokens":11,
          "completion_tokens_details":{"reasoning_tokens":37}}}
"""
let chatNoDetailsBody = """
{"choices":[{"message":{"content":"pong"},"finish_reason":"stop"}],
 "usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}
"""
let errorBody = "{\"error\":{\"message\":\"Authentication Fails, Your api key: \(KEY) is invalid\",\"type\":\"authentication_error\"}}"

// ① best effort
let m404 = AIConnectionTester.modelsLine(
    .init(status: 404, body: Data("{\"error\":\"not found\"}".utf8), error: nil),
    url: "https://api.deepseek.com/v1/models", key: KEY)
let m200 = AIConnectionTester.modelsLine(
    .init(status: 200, body: Data("{\"data\":[{\"id\":\"a\"},{\"id\":\"b\"}]}".utf8), error: nil),
    url: "https://api.deepseek.com/v1/models", key: KEY)
let mNet = AIConnectionTester.modelsLine(
    .init(status: -1, body: Data(), error: "The Internet connection appears to be offline."),
    url: "https://api.deepseek.com/v1/models", key: KEY)
print("   [① 404]\n\(m404)")
print("   [① 200]\n\(m200)")
print("   [① offline]\n\(mNet)")
check("f-1a", "① 端点不给模型列表 → 说「该端点不提供模型列表」且**不是** ✗（尽力而为）",
      m404.contains("该端点不提供模型列表") && m404.hasPrefix("① ")
        && !m404.contains("✗") && m404.contains("404"),
      m404.replacingOccurrences(of: "\n", with: "⏎"))
check("f-1b", "① 给列表 → ✓ + 模型个数",
      m200.contains("✓") && m200.contains("2 个模型"),
      m200.replacingOccurrences(of: "\n", with: "⏎"))
check("f-1c", "① 连不上 → 打「—」并说明不影响 ② 的判断",
      mNet.contains("—") && !mNet.contains("✗"),
      mNet.replacingOccurrences(of: "\n", with: "⏎"))

// ② chat
let cOK = AIConnectionTester.chatLine(.init(status: 200, body: Data(chatOKBody.utf8), error: nil), key: KEY)
let c400 = AIConnectionTester.chatLine(.init(status: 400, body: Data(errorBody.utf8), error: nil), key: KEY)
let cNet = AIConnectionTester.chatLine(.init(status: -1, body: Data(), error: "Could not connect to the server."), key: KEY)
print("   [② 200]\n\(cOK.text)")
print("   [② 400]\n\(c400.text)")
print("   [② offline]\n\(cNet.text)")
check("f-2a", "② 200 → ✓ OK（带 finish_reason 与输出 token 数），ok=true，usage 被取到",
      cOK.ok && cOK.text.contains("✓ OK") && cOK.text.contains("finish_reason=stop")
        && cOK.usage?["completion_tokens"] as? Int == 1,
      cOK.text.replacingOccurrences(of: "\n", with: "⏎"))
check("f-2b", "② 400 → ✗ + HTTP 状态 + **原始错误体**，ok=false",
      !c400.ok && c400.text.contains("✗ HTTP 400")
        && c400.text.contains("Authentication Fails"),
      c400.text.replacingOccurrences(of: "\n", with: "⏎"))
check("f-2c", "② 连不上 → ✗ 连接失败 + 说明不影响出稿",
      !cNet.ok && cNet.text.contains("✗ 连接失败") && cNet.text.contains("文献核实任务书"),
      cNet.text.replacingOccurrences(of: "\n", with: "⏎"))
check("f-2d", "② 非 stop 的 finish_reason 有告警（引擎也会把这种情况标进任务书）",
      AIConnectionTester.chatLine(.init(status: 200, body: Data(chatThinkingBody.utf8), error: nil), key: KEY)
        .text.contains("finish_reason 不是 stop"),
      "length → 告警行存在")

// ③ thinking verdict: three branches
let usage37 = ((try? JSONSerialization.jsonObject(with: Data(chatThinkingBody.utf8))) as? [String: Any])?["usage"] as? [String: Any]
let usageNoDetails = ((try? JSONSerialization.jsonObject(with: Data(chatNoDetailsBody.utf8))) as? [String: Any])?["usage"] as? [String: Any]
let t37 = AIConnectionTester.thinkingLine(usage: usage37)
let t0 = AIConnectionTester.thinkingLine(usage: cOK.usage)
let tNoField = AIConnectionTester.thinkingLine(usage: usageNoDetails)
let tNone = AIConnectionTester.thinkingLine(usage: nil)
print("   [③ reasoning 37]\n\(t37)")
print("   [③ reasoning 0]\n\(t0)")
print("   [③ usage without details]\n\(tNoField)")
print("   [③ no usage]\n\(tNone)")
check("f-3a", "③ reasoning_tokens>0 → 说「当前是思考模式」并提示按输出计费 / 建议关闭",
      t37.contains("思考模式") && t37.contains("reasoning_tokens=37")
        && t37.contains("按输出计费") && t37.contains("关闭"),
      t37.replacingOccurrences(of: "\n", with: "⏎"))
check("f-3b", "③ reasoning_tokens=0（有 usage）→ 非思考模式",
      t0.contains("非思考模式") && t0.contains("reasoning_tokens=0"),
      t0.replacingOccurrences(of: "\n", with: "⏎"))
check("f-3c", "③ 有 usage 但没有 completion_tokens_details 字段 → 非思考模式",
      tNoField.contains("非思考模式"),
      tNoField.replacingOccurrences(of: "\n", with: "⏎"))
check("f-3d", "③ 完全没有 usage → 「端点没有回传用量」，不瞎猜",
      tNone.contains("端点没有回传用量"),
      tNone.replacingOccurrences(of: "\n", with: "⏎"))

// ④ json_object probe
let j200 = AIConnectionTester.jsonModeLine(.init(status: 200, body: Data("{}".utf8), error: nil), key: KEY)
let j400 = AIConnectionTester.jsonModeLine(.init(status: 400, body: Data(errorBody.utf8), error: nil), key: KEY)
print("   [④ 200]\n\(j200)")
print("   [④ 400]\n\(j400)")
check("f-4a", "④ 200 → 支持", j200.contains("✓ 支持"), j200.replacingOccurrences(of: "\n", with: "⏎"))
check("f-4b", "④ 400 → 不支持（原始错误）",
      j400.contains("✗ 不支持") && j400.contains("原始错误") && j400.contains("Authentication Fails"),
      j400.replacingOccurrences(of: "\n", with: "⏎"))
let payload = AIConnectionTester.jsonProbePayload(model: "deepseek-flash")
let payloadJSON = String(data: (try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])) ?? Data(), encoding: .utf8) ?? ""
let content = ((payload["messages"] as? [[String: Any]])?.first?["content"] as? String) ?? ""
evidence("④ payload", payloadJSON)
check("f-4c", "④ 请求体带 response_format={\"type\":\"json_object\"}，且 prompt **含 json 字样**",
      (payload["response_format"] as? [String: Any])?["type"] as? String == "json_object"
        && content.contains("json")
        && payload["max_tokens"] as? Int == AIConnectionTester.jsonProbeMaxTokens,
      "prompt=\"\(content)\" contains json=\(content.contains("json"))")

// ①/②/④ = 3 requests max — count the network calls in the source instead of hitting the wire
let src = readSrc("Views/SettingsTab.swift")
let sendCalls = src.components(separatedBy: "send(").count - 1
check("f-4d", "体检最多 3 个请求（源码里 send(...) 的调用点 + 信号量超时兜底）",
      sendCalls == 4 && src.contains("sem.wait(timeout: .now() + timeout + 5)"),
      "send( 出现 \(sendCalls) 次（1 处定义 + 3 处调用）；每请求 timeout=\(Int(AIConnectionTester.timeout))s")
check("f-4e", "④ 只在 ① 与 ② 都成功时才发（跳过时给一行「—」说明）",
      src.contains("if models.reachedEndpoint && chatOK") && src.contains("④ response_format 试探：— 跳过"),
      "gated on models.reachedEndpoint && chatOK")

// 抬头 + 脱敏：界面文本里绝不能出现完整密钥
let head = AIConnectionTester.header(base: "https://api.deepseek.com/v1", key: KEY,
                                     model: "deepseek-flash",
                                     knobs: AIEngineKnobs(thinking: .disabled, maxTokens: "2048"),
                                     unsaved: true)
print("   [header]\n\(head)")
check("f-5a", "抬头只显示前 4 位（AppSettings.mask），**没有**完整密钥",
      head.contains("sk-P…") && !head.contains(KEY),
      "mask 行 = \(head.components(separatedBy: "\n").first { $0.hasPrefix("密钥") } ?? "<missing>")")
check("f-5b", "抬头写出这次要用的两个旋钮（思考模式/输出上限）",
      head.contains("思考模式") && head.contains("2048"),
      head.components(separatedBy: "\n").first { $0.hasPrefix("两个旋钮") } ?? "<missing>")
check("f-5c", "有未保存修改时抬头有提醒（并说明转换仍用当前值 —— 见 T-g）",
      head.contains("有未保存的修改"), "unsaved=true → 提醒存在")
check("f-5d", "没有未保存修改时不出现那句提醒",
      !AIConnectionTester.header(base: "b", key: "", model: "m", knobs: AIEngineKnobs(), unsaved: false)
        .contains("有未保存的修改"),
      "unsaved=false → 无提醒")

// 响应体回显密钥的情况：先脱敏再截断
let echo = AIConnectionTester.snippet(.init(status: 400, body: Data(errorBody.utf8), error: nil), key: KEY)
print("   [snippet with echoed key] \(echo)")
check("f-6", "服务端把密钥回显进正文时，界面片段里是 ****（不是半把钥匙）",
      !echo.contains(KEY) && echo.contains("****"),
      echo)
let long = AIConnectionTester.snippet(.init(status: 400, body: Data(String(repeating: "x", count: 900).utf8), error: nil), key: KEY)
check("f-7", "过长的响应体被截断（并带省略号）",
      long.count < 420 && long.hasSuffix("…"), "snippet 长度 = \(long.count) 字符")
check("f-8", "空响应体给一句可读的说明，不留空白",
      AIConnectionTester.snippet(.init(status: 500, body: Data(), error: nil), key: KEY) == "（响应体为空）",
      "empty body → \(AIConnectionTester.snippet(.init(status: 500, body: Data(), error: nil), key: KEY))")

// ===========================================================================
// (g) 旋钮改为「转换时现取」：与 base/key/model 同一口径（本次修复）
//
//     旧行为：`ConvertFeature.run()` 自己去读**已保存**的 ai.json →
//             编辑改了「思考模式 / 输出上限」但没点「保存」，转换仍用旧值。
//     新行为：`ConvertTab.run()` 把界面上的**当前**值经 `run(knobs:)` 传进来；
//             只有 `knobs == nil`（无界面状态的调用方）才回落读文件。
// ===========================================================================
section("T-g  两个旋钮现取（live），不再读「已保存」文件")

// 盘上的「已保存」值故意与界面上的当前值不同，且改完**不保存**
let g = base.appendingPathComponent("g_live_knobs").path
wipe(g)
let sg = AppSettings(storeDirectory: URL(fileURLWithPath: g, isDirectory: true))
sg.aiBase = "https://api.deepseek.com/v1"
sg.aiModel = "deepseek-flash"
sg.aiKey = "sk-LIVE-0001"
sg.aiThinking = .followEndpoint        // 保存：follow
sg.aiMaxTokens = "2048"                // 保存：2048
_ = sg.saveAI()

// 编辑随后在界面上改成 思考=开启 / 上限=512，**没有**点「保存」
sg.aiThinking = .enabled
sg.aiMaxTokens = "512"
let gStore = URL(fileURLWithPath: g, isDirectory: true)
let gOnDisk = json(rawFile(g + "/ai.json"))
evidence("ai.json（已保存）",
         "thinking=\"\(gOnDisk["thinking"] ?? "<nil>")\" maxTokens=\"\(gOnDisk["maxTokens"] ?? "<nil>")\"")
evidence("界面当前值（未保存）",
         "thinking=\(sg.aiThinking.rawValue) maxTokens=\"\(sg.aiMaxTokens)\" aiDirty=\(sg.aiDirty)")
check("g-precondition", "前提：改的是**未保存**的值（aiDirty=true，盘上仍是 follow/2048）",
      sg.aiDirty && (gOnDisk["thinking"] as? String) == "follow"
        && (gOnDisk["maxTokens"] as? String) == "2048",
      "on disk=\(gOnDisk["thinking"] ?? "<nil>")/\(gOnDisk["maxTokens"] ?? "<nil>")"
        + "  live=\(sg.aiThinking.rawValue)/\"\(sg.aiMaxTokens)\"")

// (a) 现取值优先：ConvertTab.run() 取的正是 settings.aiEngineKnobs 这一份
let gLive = sg.aiEngineKnobs
let gResolved = ConvertFeature.effectiveKnobs(gLive, storeDirectory: gStore)
evidence("effectiveKnobs(live)", "thinking=\(gResolved.thinking.rawValue) maxTokens=\"\(gResolved.maxTokens)\"")
check("g-live-resolved", "(a) 传了现取值 → 用现取的（**不是**盘上的 follow/2048）",
      gResolved == gLive && gResolved.thinking == .enabled && gResolved.maxTokens == "512",
      "resolved=\(gResolved.thinking.rawValue)/\"\(gResolved.maxTokens)\"（盘上=follow/2048）")

// …一路进到真正的环境变量字典（engineEnvironment 就是 run() 调的那个函数）
let gEnv = ConvertFeature.engineEnvironment(configuredOptions(key: "sk-LIVE-KEY"),
                                            toolDir: toolPlain, knobs: gResolved)
evidence("env from live knobs", dump(gEnv))
check("g-live-env", "(a) 未保存的 思考=开启/上限=512 进到 engineEnvironment：THINKING=\"1\"、MAX_TOKENS=\"512\"",
      gEnv["SLCN_LLM_THINKING"] == "1" && gEnv["SLCN_LLM_MAX_TOKENS"] == "512",
      "{ \(dump(gEnv)) }")

// 反证：同一时刻若只读已保存的 ai.json，进环境的会是旧值 —— 这个差别就是本次修复
let gEnvSaved = ConvertFeature.engineEnvironment(
    configuredOptions(key: "sk-LIVE-KEY"), toolDir: toolPlain,
    knobs: AppSettings.loadEngineKnobs(storeDirectory: gStore))
evidence("env from saved-only（修复前的那条路）", dump(gEnvSaved))
check("g-old-path-differs", "反证：老路径（只读已保存）此刻会发 follow + 2048 ≠ 现取路径的 on + 512",
      gEnvSaved["SLCN_LLM_THINKING"] == nil && gEnvSaved["SLCN_LLM_MAX_TOKENS"] == "2048"
        && gEnv["SLCN_LLM_THINKING"] != gEnvSaved["SLCN_LLM_THINKING"]
        && gEnv["SLCN_LLM_MAX_TOKENS"] != gEnvSaved["SLCN_LLM_MAX_TOKENS"],
      "live={ \(dump(gEnv)) }  vs  saved-only={ \(dump(gEnvSaved)) }")

// (b) nil 回落：没有界面状态的调用方（命令行/自检）照旧读已保存的 ai.json
let gNil = ConvertFeature.effectiveKnobs(nil, storeDirectory: gStore)
evidence("effectiveKnobs(nil)", "thinking=\(gNil.thinking.rawValue) maxTokens=\"\(gNil.maxTokens)\"")
check("g-nil-fallback", "(b) knobs=nil → 回落读已保存的 ai.json（follow / 2048），行为与从前一致",
      gNil.thinking == .followEndpoint && gNil.maxTokens == "2048",
      "resolved=\(gNil.thinking.rawValue)/\"\(gNil.maxTokens)\"")
check("g-nil-same-as-loader", "回落值与 AppSettings.loadEngineKnobs(storeDirectory:) 逐字段一致（同一实现）",
      gNil == AppSettings.loadEngineKnobs(storeDirectory: gStore),
      "effectiveKnobs(nil)=\(gNil.thinking.rawValue)/\"\(gNil.maxTokens)\"")
let gMissing = base.appendingPathComponent("g_missing").path
check("g-nil-missing", "nil + 目录不存在 → 默认值（follow/2048），不建目录、不崩",
      ConvertFeature.effectiveKnobs(nil, storeDirectory: URL(fileURLWithPath: gMissing,
                                                             isDirectory: true)) == AIEngineKnobs()
        && !exists(gMissing),
      "knobs=\(ConvertFeature.effectiveKnobs(nil, storeDirectory: URL(fileURLWithPath: gMissing, isDirectory: true)).thinking.rawValue)"
        + " dirCreated=\(exists(gMissing))")

// 接线（源码级）：调用方确实把当前值传了进来，run() 确实把它交给 engineEnvironment
let convertTabSrc = readSrcGlob("Views/ConvertTab*.swift")
check("g-caller-wiring", "(a') ConvertTab.run() 从同一个 settings 现取旋钮，并以 run(knobs:) 传入",
      convertTabSrc.contains("let knobs = settings.aiEngineKnobs")
        && convertTabSrc.contains("ConvertFeature.run(options: o, knobs: knobs, log: logger)"),
      "ConvertTab.run(): settings.aiEngineKnobs → ConvertFeature.run(options:knobs:log:)（源码级，非运行时）")
check("g-run-wiring", "run() 用 effectiveKnobs(liveKnobs) 解析，并把结果交给 engineEnvironment",
      convSrc.contains("let knobs = effectiveKnobs(liveKnobs)")
        && convSrc.contains("engineEnvironment(o, toolDir: toolDir, knobs: knobs)"),
      "legacy 单参调用 run(options:log:) 仍可用（knobs 有默认值 nil）")

// (c) 界面文案现在说的是真话：不许再留「旋钮要保存才生效」那套说法
let settingsSrc = readSrc("Views/SettingsTab.swift")
let staleClaims = ["这两个旋钮读的是已保存的值",
                   "否则「初排转换」用的还是上一次保存的值",
                   "两个旋钮则要点「保存」之后才随转换生效",
                   "转换时用的是已保存的值",
                   "才随转换生效"]
let leftovers = staleClaims.filter { settingsSrc.contains($0) }
check("g-ui-no-stale-claim", "(c) 设置页里**没有**「旋钮读已保存的值 / 要点保存才生效」这类已失效的说法",
      leftovers.isEmpty,
      leftovers.isEmpty ? "5 个旧句式逐个查过：0 处残留" : "残留：\(leftovers.joined(separator: " / "))")
check("g-ui-truth-vs-behaviour", "(c) 设置页声称「转换时用的是上面这些输入框里的当前值」—— 与 g-live-env 实测一致",
      settingsSrc.contains("转换时用的是上面这些输入框里的当前值")
        && gEnv["SLCN_LLM_THINKING"] == "1" && gEnv["SLCN_LLM_MAX_TOKENS"] == "512"
        && sg.aiDirty,
      "文案存在=\(settingsSrc.contains("转换时用的是上面这些输入框里的当前值"))；未保存值确实进了环境=\(gEnv["SLCN_LLM_MAX_TOKENS"] ?? "<none>")")
check("g-ui-dirty-note", "(c) 「有未保存的修改」只说「转换仍用当前值、只影响下次启动」（不再是「用已保存的值」）",
      settingsSrc.contains("转换仍用上面填的当前值")
        && settingsSrc.contains("下次启动会回到已保存的值"),
      "状态行：请求体 ……（⚠ 有未保存的修改：转换仍用上面填的当前值，但下次启动会回到已保存的值）")
let gHead = AIConnectionTester.header(base: "https://api.deepseek.com/v1", key: "sk-LIVE-0001",
                                      model: "deepseek-flash", knobs: sg.aiEngineKnobs,
                                      unsaved: sg.aiDirty)
print("   [header unsaved]\n\(gHead)")
check("g-ui-header-truth", "(c) 体检抬头不再说「两个旋钮要保存之后才生效」，改说现取值 + 保存只为下次启动",
      gHead.contains("有未保存的修改") && gHead.contains("转换时都用当前填的值")
        && gHead.contains("不是已保存的值") && !gHead.contains("才随转换生效")
        && !gHead.contains("要点「保存」"),
      gHead.components(separatedBy: "\n").last ?? "<missing>")

// ===========================================================================
// (h) 「清空」不得落盘：**用户实测报的 bug**（2026-10-15，第 62 条）
//
//     用户实测：在设置页配好 AI、点过「保存」之后，一按「清空」
//     → 盘上的 ai.json 立刻被空值覆盖（密钥一起没了，只能重贴）。
//     期望语义：**「清空」只清界面上的输入框，磁盘上已保存的配置不动**；
//     只有清空之后**再点一次「保存」**才真正覆盖。
//
//     判据是**字节**：清空前后 ai.json 的 sha256 必须逐字符相同（mtime 是旁证）。
//     落盘路径从此**唯一** = 显式点「保存」（`SettingsTab` 的保存按钮）。
// ===========================================================================
section("T-h  「清空」不得落盘（用户实测 bug，第 62 条）")

let h = base.appendingPathComponent("h_clear_no_write").path
wipe(h)
let sh = AppSettings(storeDirectory: URL(fileURLWithPath: h, isDirectory: true))
sh.aiBase = "https://api.deepseek.com/v1"
sh.aiKey = "sk-CLEAR-0042"
sh.aiModel = "deepseek-flash"
sh.aiThinking = .enabled
sh.aiMaxTokens = "4096"
let hSaved = sh.saveAI()
let hFile = h + "/ai.json"
check("h-precondition", "前提：先「保存」一份非空配置（base/key/model + 旋钮 on/4096）",
      hSaved && exists(hFile) && !sh.aiDirty,
      "saved=\(hSaved) aiDirty=\(sh.aiDirty) aiStatus=\"\(sh.aiStatus)\"")
evidence("ai.json（清空前）", "\n----8<----\n\(rawFile(hFile))\n---->8----")
let hShaBefore = sha256(hFile)
let hMtimeBefore = modTime(hFile)
evidence("清空前", "sha256=\(hShaBefore)  mtime=\(hMtimeBefore)")

sh.clearAI()

let hShaAfter = sha256(hFile)
check("h1", "清空后 ai.json 的 sha256 **逐字节不变**（回归测试：从前这里会把已保存的配置抹成空）",
      hShaBefore == hShaAfter && hShaBefore != "<unreadable>",
      "before=\(hShaBefore)\n        after =\(hShaAfter)  字节相同=\(hShaBefore == hShaAfter)")
check("h1-mtime", "清空后 mtime 也没变（旁证：连一次写盘都没发生过）",
      modTime(hFile) == hMtimeBefore, "mtime \(hMtimeBefore) → \(modTime(hFile))")

check("h2", "清空后五个表单值都回到默认、aiDirty=true、状态行说清「输入框清了 + 盘上没动 + 要覆盖请点保存」",
      sh.aiBase.isEmpty && sh.aiKey.isEmpty && sh.aiModel.isEmpty
        && sh.aiThinking == .followEndpoint && sh.aiMaxTokens == AIMaxTokens.defaultText
        && sh.aiDirty
        && sh.aiStatus.contains("清空") && sh.aiStatus.contains("没动") && sh.aiStatus.contains("保存"),
      "base=\"\(sh.aiBase)\" key=\"\(sh.aiKey)\" model=\"\(sh.aiModel)\" "
        + "thinking=\(sh.aiThinking.rawValue) maxTokens=\"\(sh.aiMaxTokens)\" "
        + "aiDirty=\(sh.aiDirty) aiStatus=\"\(sh.aiStatus)\"")
evidence("状态行（界面原样显示这一行）", sh.aiStatus)

// (h3) 清空后**不保存** → 模拟重启：读回来的还是清空前那份配置
let hLiveBase = sh.aiBase        // 清空后的内存值（应当是空）
let hLiveModel = sh.aiModel
sh.loadAI()
check("h3", "清空后**不保存**、loadAI()（模拟重启）→ 读回的是清空前那份配置，什么都没丢",
      sh.aiBase == "https://api.deepseek.com/v1" && sh.aiKey == "sk-CLEAR-0042"
        && sh.aiModel == "deepseek-flash" && sh.aiThinking == .enabled
        && sh.aiMaxTokens == "4096" && !sh.aiDirty,
      "loadAI 前内存里是空（base=\"\(hLiveBase)\" model=\"\(hLiveModel)\"）；"
        + "loadAI 后 base=\"\(sh.aiBase)\" key=\"\(sh.aiKey)\" model=\"\(sh.aiModel)\" "
        + "thinking=\(sh.aiThinking.rawValue) maxTokens=\"\(sh.aiMaxTokens)\" aiDirty=\(sh.aiDirty)")
let shRestart = AppSettings(storeDirectory: URL(fileURLWithPath: h, isDirectory: true))
check("h3-restart", "同上：另建实例（= 真的重启 App）读到的也是旧配置",
      shRestart.aiBase == "https://api.deepseek.com/v1" && shRestart.aiModel == "deepseek-flash"
        && shRestart.aiThinking == .enabled && shRestart.aiMaxTokens == "4096",
      "fresh instance: base=\"\(shRestart.aiBase)\" model=\"\(shRestart.aiModel)\" "
        + "thinking=\(shRestart.aiThinking.rawValue) maxTokens=\"\(shRestart.aiMaxTokens)\"")

// (h4) 清空后**再点「保存」** → 这时才真正覆盖（用户要的语义）
sh.clearAI()
let h4Saved = sh.saveAI()
let h4Obj = json(rawFile(hFile))
let shAfterSave = AppSettings(storeDirectory: URL(fileURLWithPath: h, isDirectory: true))
check("h4", "清空后**再点「保存」** → 文件这时才被覆盖：base/key/model 空、旋钮回默认",
      h4Saved && (h4Obj["base"] as? String) == "" && (h4Obj["key"] as? String) == ""
        && (h4Obj["model"] as? String) == ""
        && (h4Obj["thinking"] as? String) == "follow"
        && (h4Obj["maxTokens"] as? String) == AIMaxTokens.defaultText
        && shAfterSave.aiBase.isEmpty && shAfterSave.aiModel.isEmpty,
      "base=\"\(h4Obj["base"] ?? "<nil>")\" key=\"\(h4Obj["key"] ?? "<nil>")\" "
        + "model=\"\(h4Obj["model"] ?? "<nil>")\" thinking=\"\(h4Obj["thinking"] ?? "<nil>")\" "
        + "maxTokens=\"\(h4Obj["maxTokens"] ?? "<nil>")\"；重读 base 空=\(shAfterSave.aiBase.isEmpty)")
check("h4-sha-changed", "反证：这一次显式保存**确实**写了盘（sha256 变了）—— 清空本身不写、保存才写",
      sha256(hFile) != hShaBefore,
      "清空前 sha=\(hShaBefore)\n        保存后 sha=\(sha256(hFile))")

// (h5) 空配置点「测试连接」：guard 当场回话，**一个请求都不发**
var h5ok: Bool? = nil
var h5msg = ""
var h5onMain = false
AIConnectionTester.test(base: "", key: "", model: "",
                        knobs: AIEngineKnobs(), unsaved: false) { ok, msg in
    h5ok = ok; h5msg = msg; h5onMain = Thread.isMainThread
}
check("h5", "AIConnectionTester.test(base:\"\",key:\"\",model:\"\") → 当场回「请先填写…」，**一个请求都不发**",
      h5ok == false && h5msg.contains("请先填写") && h5msg.contains("地址")
        && h5msg.contains("模型") && h5onMain,
      "ok=\(String(describing: h5ok)) 回调在 test() 返回**之前**就执行完了（只有 guard 这条同步路；"
        + "发请求那条路要先 DispatchQueue.async，本 harness 没有 runloop 根本不会回调）"
        + " msg=\"\(h5msg)\"")
check("h5-guard-before-net", "源码级：空地址 guard 在**网络那段之前**就 return（不通过 = 连 URLSession 都不碰）",
      body(after: "static func test(base rawBase: String", in: settingsSrc)
        .contains("guard !base.isEmpty, !model.isEmpty else")
        && (src.range(of: "guard !base.isEmpty, !model.isEmpty else")?.lowerBound ?? src.endIndex)
           < (src.range(of: "DispatchQueue.global(qos: .userInitiated).async")?.lowerBound ?? src.startIndex),
      "guard 位置 < DispatchQueue.global 位置（源码级；运行时那一条由 h5 的同步回调证明）")

// (h6) 边界：落盘路径**唯一** = 显式点「保存」
let hVmSrc = readSrc("Core/ViewModels.swift")
let hClearBody = body(after: "func clearAI() {", in: hVmSrc)
check("h6-clear-no-save", "源码级：clearAI() 函数体里**没有** saveAI() 调用",
      hClearBody != "<signature not found>" && !hClearBody.isEmpty && !hClearBody.contains("saveAI("),
      "clearAI() 体 \(hClearBody.count) 字符，含 saveAI( = \(hClearBody.contains("saveAI("))")
let hWriteSites = hVmSrc.components(separatedBy: "data.write(to:").count - 1
check("h6-single-writer", "源码级：ViewModels.swift 里写 ai.json 的地方**只有 1 处**（saveAI 的 data.write）",
      hWriteSites == 1, "data.write(to: 出现 \(hWriteSites) 次")

// (h7) 界面文案：这句说明必须就在「清空」按钮旁边（编辑看得见）
check("h7-ui-caption", "界面文案：「清空」旁写明「只清空输入框，**不动**已保存的配置；点「保存」才会覆盖」",
      settingsSrc.contains("只清空输入框") && settingsSrc.contains("不动")
        && settingsSrc.contains("保存"),
      "SettingsTab.swift 含「只清空输入框」=\(settingsSrc.contains("只清空输入框"))")

// ===========================================================================
// (i) 开关联动（纯函数）+ 三个开关到 argv 的映射 —— 2026-10-15 phase 2b
//
//   用户决定（方案 A）：「用 AI 处理参考文献」勾上 → **自动把**「生成参考文献核实任务书」
//   勾上并置灰（AI 的逐条建议与「六、合并记录」都写在那份任务书里，不生成就没有审阅材料）；
//   取消 AI 勾选 → 任务书开关恢复可操作，**不改它的值**。
//   新增「保留原 reference.bib（不并入 AI 结果）」→ `--no-ai-merge`，只在 AI 勾选时可见。
// ===========================================================================
section("T-i  开关联动（纯函数）+ argv 映射")

func opts(_ verify: Bool = false, _ ai: Bool = false, _ keep: Bool = false,
          key: String = "") -> ConvertFeature.Options {
    var o = ConvertFeature.Options()
    o.verifyRefs = verify
    o.useAI = ai
    o.keepOriginalBib = keep
    o.llmKey = key
    return o
}

func argv(_ o: ConvertFeature.Options) -> [String] { ConvertFeature.arguments(o) }

// i1: 勾 AI → 任务书自动勾上
let i1 = ConvertFeature.toggled(.useAI, to: true, in: opts(false, false, false))
check("i1", "勾「用 AI 处理参考文献」→ 自动把「生成参考文献核实任务书」勾上",
      i1.useAI && i1.verifyRefs, "useAI=\(i1.useAI) verifyRefs=\(i1.verifyRefs)")

// i2: 取消 AI → 任务书恢复可操作，值不动
let i2 = ConvertFeature.toggled(.useAI, to: false, in: opts(true, true, false))
check("i2", "取消 AI 勾选 → 任务书恢复可操作，且**不动它的值**（联动勾上的 true 保留）",
      !i2.useAI && i2.verifyRefs && !ConvertFeature.verifyRefsLocked(i2),
      "useAI=\(i2.useAI) verifyRefs=\(i2.verifyRefs) locked=\(ConvertFeature.verifyRefsLocked(i2))")
let i2b = ConvertFeature.toggled(.useAI, to: false, in: opts(false, true, false))
check("i2b", "反例：AI 开着时任务书本来没勾 → 取消 AI 后仍然没勾（不代编辑做决定）",
      !i2b.verifyRefs && !i2b.useAI, "verifyRefs=\(i2b.verifyRefs)")

// i3: 置灰期间任务书只允许 true
let i3 = ConvertFeature.toggled(.verifyRefs, to: false, in: opts(false, true, false))
check("i3", "AI 勾着时把任务书改成 false（旧界面 / 脚本直接赋值）→ 拉回 true",
      i3.verifyRefs, "verifyRefs=\(i3.verifyRefs)")
check("i3-lock", "置灰判据 = useAI（勾上就锁住；取消就解锁）",
      ConvertFeature.verifyRefsLocked(opts(false, true, false))
        && !ConvertFeature.verifyRefsLocked(opts(false, false, false)),
      "locked(ai)=\(ConvertFeature.verifyRefsLocked(opts(false, true, false))) "
        + "locked(no-ai)=\(ConvertFeature.verifyRefsLocked(opts(false, false, false)))")
let i3b = ConvertFeature.toggled(.verifyRefs, to: true, in: opts(false, false, false))
check("i3b", "没勾 AI 时任务书照旧可以单独勾（保持旧行为：不勾 AI 也能只出任务书）",
      i3b.verifyRefs && !i3b.useAI, "verifyRefs=\(i3b.verifyRefs) useAI=\(i3b.useAI)")

// i4: 新开关自己无联动
let i4 = ConvertFeature.toggled(.keepOriginalBib, to: true, in: opts(true, true, false))
check("i4", "「保留原 reference.bib」只写自己的值，不碰另两个开关",
      i4.keepOriginalBib && i4.useAI && i4.verifyRefs,
      "keep=\(i4.keepOriginalBib) useAI=\(i4.useAI) verifyRefs=\(i4.verifyRefs)")

// i5: argv 映射
let i5off = argv(opts(false, false, false))
check("i5-off", "没勾 AI、没勾任务书 → `--verify-refs` 与 `--no-ai-merge` **都不出现**",
      !i5off.contains("--verify-refs") && !i5off.contains("--no-ai-merge"),
      "argv=\(i5off)")
let i5tb = argv(opts(true, false, false))
check("i5-taskbook", "只勾任务书 → 有 --verify-refs，没有 --no-ai-merge",
      i5tb.contains("--verify-refs") && !i5tb.contains("--no-ai-merge"), "argv=\(i5tb)")
let i5ai = argv(opts(true, true, false))
check("i5-ai", "勾 AI（任务书被联动勾上）→ 有 --verify-refs，没有 --no-ai-merge",
      i5ai.contains("--verify-refs") && !i5ai.contains("--no-ai-merge"), "argv=\(i5ai)")
let i5keep = argv(opts(true, true, true))
check("i5-keep", "勾 AI + 保留原 bib → `--verify-refs` 与 `--no-ai-merge` **都在**",
      i5keep.contains("--verify-refs") && i5keep.contains("--no-ai-merge"), "argv=\(i5keep)")
let i5keep2 = argv(opts(true, false, true))
check("i5-keep-without-ai", "AI 关着时不给 --no-ai-merge（没勾 AI 就没有合并通道，加上是说谎）",
      i5keep2.contains("--verify-refs") && !i5keep2.contains("--no-ai-merge"), "argv=\(i5keep2)")
let i5key = argv(opts(false, true, false, key: "sk-MUST-NOT-BE-IN-ARGV"))
check("i5-nokey", "密钥**永远不进 argv**（仍只走环境变量 SLCN_LLM_KEY）",
      !i5key.contains { $0.contains("sk-") }, "argv=\(i5key)")

// i5b（P2，第 72 条起）：检索证据开关 → `--search-refs`；只在勾了 AI 时出现
var optRefs = ConvertFeature.Options()
optRefs.useAI = true
optRefs.searchRefs = true
let i5refsOn = argv(optRefs)
check("i5b-searchrefs", "勾了「用 AI」+「检索证据」→ argv 里有 --search-refs",
      i5refsOn.contains("--search-refs"), "argv=\(i5refsOn)")
let i5refsDefault = argv(opts(false, true, false))
check("i5b-searchrefs-default", "默认（不勾检索证据）→ 没有 --search-refs（默认命令行逐字节不变）",
      !i5refsDefault.contains("--search-refs"), "argv=\(i5refsDefault)")
var optRefsNoAI = ConvertFeature.Options()
optRefsNoAI.searchRefs = true      // 故意只勾它、不勾 AI
check("i5b-searchrefs-needs-ai", "没勾「用 AI」时**不传** --search-refs（它复用 AI 的地址/密钥）",
      !argv(optRefsNoAI).contains("--search-refs"), "argv=\(argv(optRefsNoAI))")

// i6: 源码级接线：SwiftUI 只绑定纯函数，联动逻辑只有一份
let tabSrc = readSrcGlob("Views/ConvertTab*.swift")
check("i6-bind", "ConvertTab 的复选框绑定走 `ConvertFeature.toggled(...)`；批62 起不再绑定独立任务书勾选",
      tabSrc.contains("ConvertFeature.toggled(")
        && !tabSrc.contains("verifyRefsLocked(")
        && !tabSrc.contains("binding(.verifyRefs"),
      "toggled=\(tabSrc.contains("ConvertFeature.toggled(")) "
        + "locked=\(tabSrc.contains("verifyRefsLocked(")) "
        + "bindVerifyRefs=\(tabSrc.contains("binding(.verifyRefs"))")
check("i6-ui", "界面保留「保留原 reference.bib（不并入 AI 结果）」开关；批62 起独立任务书勾选/置灰说明已移除",
      tabSrc.contains("保留原 reference.bib（不并入 AI 结果）")
        && !tabSrc.contains("自动勾上并锁定")
        && !tabSrc.contains("生成参考文献核实任务书（交给自己的 AI 核实）"),
      "keepBib=\(tabSrc.contains("保留原 reference.bib（不并入 AI 结果）")) "
        + "lockText=\(tabSrc.contains("自动勾上并锁定")) "
        + "oldToggle=\(tabSrc.contains("生成参考文献核实任务书（交给自己的 AI 核实）"))")
check("i6-hint", "AI 开关的说明按事实把「意图」与「限制」分开写（第 69 条改文案）："
      + "并入交付文件 / 未开检索时**明说这次不是联网核实** / 失败时 bib 不改 / "
      + "开了检索时说的是「真的联网」并指明要看 ⑤ 探针",
      tabSrc.contains("并入交付的 reference.bib")
        && tabSrc.contains("本次不是联网核实")
        && tabSrc.contains("一个字节都不会改")
        && tabSrc.contains("本次已开启联网检索")
        && tabSrc.contains("⑤ 检索探针"),
      "并入=\(tabSrc.contains("并入交付的 reference.bib")) 非联网核实=\(tabSrc.contains("本次不是联网核实")) "
        + "失败不改=\(tabSrc.contains("一个字节都不会改")) 已开启联网=\(tabSrc.contains("本次已开启联网检索")) "
        + "⑤探针=\(tabSrc.contains("⑤ 检索探针"))")

// i7: 「文献回读校验」页的并回按钮（源码级：并回复用同一个 checkbib.py，且危险动作先声明）
let mtDir = projFile(SRC_DIR_NAME)
let cbfSrc = (try? String(contentsOfFile: mtDir + "/Features/CheckBibFeature.swift", encoding: .utf8)) ?? ""
let cbvSrc = (try? String(contentsOfFile: mtDir + "/Views/CheckBibView.swift", encoding: .utf8)) ?? ""
check("i7-merge-argv", "CheckBibFeature 拼出 `--returned … --merge-into <目标>`（并回复用同一个 checkbib.py）",
      cbfSrc.contains("\"--merge-into\", o.target") && cbfSrc.contains("static func locateTool()"),
      "merge-into=\(cbfSrc.contains("\"--merge-into\", o.target"))")
check("i7-cover-warning", "并回前在日志里明说「将覆盖 <target>，原文件备份为 <target>.ai前」",
      cbfSrc.contains("将覆盖") && cbfSrc.contains(".ai前"),
      "含覆盖/备份声明=\(cbfSrc.contains("将覆盖"))")
check("i7-ui", "CheckBibView 有「并回」按钮（体检成功才出现、没填原件就禁用）与「重新编译该篇」按钮",
      cbvSrc.contains("Button(\"并回\")") && cbvSrc.contains("Button(\"重新编译该篇\")")
        && cbvSrc.contains("model.reportPath != nil") && cbvSrc.contains("model.mergedManuscriptDir != nil"),
      "并回=\(cbvSrc.contains("Button(\"并回\")")) 重编=\(cbvSrc.contains("Button(\"重新编译该篇\")"))")
check("i7-target-hint", "没填「原件」时的日志说明含「要并回请先选择产物里的 reference.bib」",
      cbfSrc.contains("要并回请先选择产物里的 reference.bib（即任务书前的那份原件）"),
      "提示=\(cbfSrc.contains("要并回请先选择产物里的 reference.bib"))")
check("i7-unmerged-not-success", "并回退出码 0（未改动）不说成成功：日志明说「未改动…一个字节都没动」",
      cbfSrc.contains("未改动：没有任何条目通过安全校验")
        && cbvSrc.contains("未改动 / 失败时**保留**上一份体检报告"),
      "未改动文案=\(cbfSrc.contains("未改动：没有任何条目通过安全校验"))")

// j（2026-10-15 phase 2c）：粘贴模式（`checkbib.py --returned-stdin`）的**纯逻辑**——
//   argv 映射（体检 / 并回 / 列表三种）、报告落点、以及 Support.swift 里 stdin 的接线。
//   ⚠️ harness 不启动任何子进程：这里断言的是「拼出来的参数长什么样」与源码级接线；
//   「Python 那边真的按同一份判据体检/并回」由 Engine/tests/test_merge_into.py 与
//   Engine/tests/test_checkbib.py 的 ⑥/⑧/⑨ 组用真 CLI 覆盖。
let jOptsPaste = CheckBibFeature.Options(returnedStdin: true,
                                         original: "/tmp/j/ref.bib",
                                         tex: "/tmp/j/A.tex",
                                         out: "/tmp/j/r.md")
let jArgvPaste = CheckBibFeature.arguments(jOptsPaste)
check("j1-paste-argv", "粘贴模式体检拼 `--returned-stdin`（**不出现** `--returned`）",
      jArgvPaste == ["--returned-stdin", "--original", "/tmp/j/ref.bib",
                     "--tex", "/tmp/j/A.tex", "--out", "/tmp/j/r.md"],
      "argv=\(jArgvPaste)")

let jArgvFile = CheckBibFeature.arguments(
    CheckBibFeature.Options(returned: "/tmp/j/ret.bib", out: "/tmp/j/r.md"))
check("j2-file-argv-unchanged", "文件模式体检的 argv 与从前**逐字符相同**（老路径不动）",
      jArgvFile == ["--returned", "/tmp/j/ret.bib", "--out", "/tmp/j/r.md"], "argv=\(jArgvFile)")

let jMergePaste = CheckBibFeature.mergeArguments(
    CheckBibFeature.MergeOptions(returnedStdin: true, target: "/tmp/j/ref.bib",
                                 tex: "/tmp/j/A.tex", out: "/tmp/j/m.md"))
check("j3-paste-merge-argv", "粘贴模式并回拼 `--returned-stdin --merge-into <目标>`",
      jMergePaste == ["--returned-stdin", "--merge-into", "/tmp/j/ref.bib",
                      "--tex", "/tmp/j/A.tex", "--out", "/tmp/j/m.md"],
      "argv=\(jMergePaste)")

let jMergeFile = CheckBibFeature.mergeArguments(
    CheckBibFeature.MergeOptions(returned: "/tmp/j/ret.bib", target: "/tmp/j/ref.bib"))
check("j4-file-merge-argv-unchanged", "文件模式并回的 argv 与从前逐字符相同",
      jMergeFile == ["--returned", "/tmp/j/ret.bib", "--merge-into", "/tmp/j/ref.bib"],
      "argv=\(jMergeFile)")

check("j5-list-argv", "预览拼 `--list-keys --returned-stdin`（引擎侧保证不写文件）",
      CheckBibFeature.listArguments(CheckBibFeature.ListOptions(returnedStdin: true))
        == ["--list-keys", "--returned-stdin"],
      "argv=\(CheckBibFeature.listArguments(CheckBibFeature.ListOptions(returnedStdin: true)))")

check("j6-list-argv-file", "预览也支持文件来源（`--list-keys --returned <文件>`）",
      CheckBibFeature.listArguments(CheckBibFeature.ListOptions(returned: "/tmp/j/ret.bib"))
        == ["--list-keys", "--returned", "/tmp/j/ret.bib"], "")

let jTmp = NSTemporaryDirectory() + "p2c_checkbib_j"
wipe(jTmp)
mkdir(jTmp, 0o700)
let jOrig = jTmp + "/reference.bib"
writeRaw(jOrig, "@book{x,\n  title = {t}\n}\n")
let jNorm = PathKit.normalize(jOrig)
let (jReport, jNote) = CheckBibFeature.pasteReportPath(forOriginal: jOrig)
check("j7-paste-report-next-to-original", "粘贴模式的体检报告落在**原件旁边**（产物目录）",
      jReport == (jNorm as NSString).deletingLastPathComponent + "/reference_回读校验报告.md"
        && jNote.isEmpty,
      "path=\(jReport)")
let (jReport2, jNote2) = CheckBibFeature.pasteReportPath(forOriginal: "   ")
check("j8-paste-report-temp-fallback", "没填原件时**不猜稿件路径**：退回系统临时目录并给一句说明",
      jReport2.contains("checkbib_粘贴_回读校验报告.md")
        && jNote2.contains("临时目录") && jNote2.contains("原件"),
      "path=\(jReport2) note=\(jNote2.prefix(40))…")
check("j9-report-path-unchanged", "文件模式的报告落点（交回文件旁边）一字未改",
      CheckBibFeature.reportPath(forReturned: "/tmp/j/ret_改回.bib")
        == "/tmp/j/ret_改回_回读校验报告.md",
      "path=\(CheckBibFeature.reportPath(forReturned: "/tmp/j/ret_改回.bib"))")

// j10+：源码级接线（Support.swift 的 stdin 通道 / Feature 的 stdin 传递 / View 的粘贴界面）
let supportSrc = rawFile(srcFile("Core/Support.swift"))
check("j10-run-stdin-param", "`Shell.run` 有 `stdin: String? = nil`（不给 = 与从前一样，不碰 standardInput）",
      supportSrc.contains("stdin: String? = nil") && supportSrc.contains("if stdin != nil {"),
      "含参数=\(supportSrc.contains("stdin: String? = nil"))")
// ⚠️ 第 95 条给 capture 加了 `environment:`（引擎预览要挂 PYTHONPATH=vendor），
//    所以这里**只断言 stdin 参数还在**，不锁死它后面有没有别的参数。
check("j11-capture-stdin-param", "`Shell.capture` 也有 `stdin:`（预览要收回那一行输出）；不给仍是 /dev/null",
      supportSrc.contains("stdin: String? = nil")
        && supportSrc.contains("process.standardInput = FileHandle.nullDevice"),
      "")
check("j12-feed-pipe", "stdin 写进 Pipe 并**在后台队列**写完关闭（避免管道死锁）",
      supportSrc.contains("try pipe.fileHandleForWriting.write(contentsOf: data)")
        && supportSrc.contains("try pipe.fileHandleForWriting.close()")
        && supportSrc.contains("DispatchQueue.global(qos: .userInitiated).async"),
      "")
check("j13-feed-called-both", "run 与 capture 都调用同一个 `feedStdin`（只有一份写管道的实现）",
      supportSrc.components(separatedBy: "feedStdin(stdin, to: stdinPipe").count - 1 == 2,
      "调用点=\(supportSrc.components(separatedBy: "feedStdin(stdin, to: stdinPipe").count - 1)")
check("j14-feature-passes-stdin", "CheckBibFeature 把粘贴文本走 `stdin:` 传给引擎（体检 + 并回各一处）",
      cbfSrc.components(separatedBy: "stdin: isPaste ? pasted : nil").count - 1 == 2,
      "出现次数=\(cbfSrc.components(separatedBy: "stdin: isPaste ? pasted : nil").count - 1)")
check("j15-feature-no-tempfile", "粘贴文本**不落中间文件**：Feature 里没有把 pasted 写盘的调用",
      !cbfSrc.contains("pasted.write(") && !cbfSrc.contains("writeText(pasted")
        && cbfSrc.contains("--returned-stdin"),
      "")
check("j16-preview-uses-list", "`previewPasted` 走 `--list-keys` + `Shell.capture`，取最后一行输出",
      cbfSrc.contains("listArguments(ListOptions(returnedStdin: true))")
        && cbfSrc.contains("previewPasted") && cbfSrc.contains("Shell.capture(py, args")
        && cbfSrc.contains("预览失败（退出码"),
      "")
check("j17-view-editor", "CheckBibView 有粘贴文本框（`TextEditor`）与「从剪贴板粘贴」按钮",
      cbvSrc.contains("TextEditor(text: $model.returnedText)")
        && cbvSrc.contains("从剪贴板粘贴")
        && cbvSrc.contains("NSPasteboard.general.string(forType: .string)"),
      "TextEditor=\(cbvSrc.contains("TextEditor(text: $model.returnedText)"))")
check("j18-view-mode-switch", "界面有「选择文件 / 粘贴文本」两种模式的切换（分段控件）",
      cbvSrc.contains("Picker(\"\", selection: $model.returnedMode)")
        && cbvSrc.contains("case file = \"选择文件\"") && cbvSrc.contains("case paste = \"粘贴文本\""),
      "")
check("j19-view-preview-wiring", "粘贴内容一变就跑预览（onChange）+ 世代号丢掉过期结果",
      cbvSrc.contains(".onChange(of: model.returnedText)")
        && cbvSrc.contains("previewGeneration") && cbvSrc.contains("guard gen == m.previewGeneration"),
      "")
// ⚠️ U3（2026-10-17，mac 取消按钮）起：运行中主按钮**变成「取消」**，所以
//    `开始体检` 的禁用判据不再需要 `model.running ||`（按钮在运行时根本不显示）；
//    「文本为空 → 禁用」这条保证仍在，只是判据变成 `!hasReturnedSource`。
//    同时钉住「运行中同一位置是取消按钮且能杀进程树」——这才是 U3 的接线条。
check("j20-view-empty-disables", "「开始体检」在文本为空时禁用（两种模式各看各的那一栏）；运行中同一位置是「取消」",
      cbvSrc.contains(".disabled(!hasReturnedSource)") && cbvSrc.contains("hasReturnedSource")
        && cbvSrc.contains("model.cancel?.cancel()"),
      "")
check("j21-view-tex-autofill", "粘贴模式从**原件**同目录自动填「正文 .tex」",
      cbvSrc.contains("autofillTeXFromOriginal")
        && cbvSrc.contains("if model.returnedMode == .paste { autofillTeXFromOriginal() }"),
      "")

// ===========================================================================
// (u) U3 取消按钮（2026-10-17 第十四批）：mac 取消 = 杀整棵进程树；取消 ≠ 失败
//
//     为什么要单独钉：Windows 侧踩过「只 terminate 直接子进程 → xelatex 继续写产物目录」
//     （M3/W1）；mac 是同一风险 —— python 起的 xelatex 是**孙进程**。这条接线一旦漏掉，
//     表现是「界面说取消了、TeX 还在后台写同一目录」——比不提供取消更危险。
//     真机实测记录在笔记仓 AUDIT §七（编译全部页启动 2.5s 后取消，1.5s 内进程树消失）。
// ===========================================================================
section("T-u  U3 取消按钮（mac）")

let logSrc = readSrc("Core/Log.swift")
check("u1-runcancel", "Log.swift 有 `RunCancel`：加锁取消句柄（attach / cancel / isCancelled）",
      logSrc.contains("final class RunCancel")
        && logSrc.contains("func attach(_ p: Process?)")
        && logSrc.contains("func cancel() -> Bool")
        && logSrc.contains("var isCancelled: Bool")
        && logSrc.contains("private let lock = NSLock()"),
      "")
check("u2-kill-tree", "取消杀**整棵进程树**：`ps -axo pid=,ppid=` 快照 + 后代 SIGTERM + 根进程 terminate",
      logSrc.contains("\"-axo\", \"pid=,ppid=\"")
        && logSrc.contains("kill(child, SIGTERM)")
        && logSrc.contains("root.terminate()"),
      "")
check("u3-shell-wiring", "`Shell.run` 与 `Shell.capture` 都把子进程注册进取消句柄；取消返回哨兵 -2（≠ 失败码）",
      supportSrc.components(separatedBy: ".attach(process)").count - 1 == 2
        && supportSrc.contains("static let cancelledStatus: Int32 = -2")
        && supportSrc.contains("if log?.cancel?.isCancelled == true { return Shell.cancelledStatus }"),
      "attach(process) 次数 = \(supportSrc.components(separatedBy: ".attach(process)").count - 1)（run + capture）")

// 每个 Shell.run 调用点都要认这个哨兵 —— 漏一处，那一页取消后仍会补一句「失败」
let uFeatureFiles = ["Features/ConvertFeature.swift",
                     "Features/ProofCheckFeature.swift",
                     "Features/CompileFeature.swift",
                     "Features/DeleteTempFeature.swift",
                     "Features/TocFeature.swift",
                     "Features/InfoCheckFeature.swift",
                     "Features/CheckBibFeature.swift",
                     "Features/MergeFeature.swift"]
let uMissed = uFeatureFiles.filter { !readSrc($0).contains("Shell.cancelledStatus") }
check("u4-all-features", "全部 8 个 Feature 都处理了 `Shell.cancelledStatus`（取消不补「失败」话术）",
      uMissed.isEmpty,
      uMissed.isEmpty ? "8/8" : "未处理：\(uMissed.joined(separator: "、"))")
let uViewFiles = ["Views/SimpleFeatureView.swift", "Views/CheckBibView.swift",
                  "Views/ConvertTab*.swift", "Views/ProofCheckView.swift",
                  "Views/InfoCheckView.swift", "Views/ContentTab.swift"]
// B-230：初排页拆成同层多文件 → 这一项按**整族**读（仍是 6 个「页」，不弱化）
let uNoCancel = uViewFiles.filter { !readSrcGlob($0).contains("model.cancel?.cancel()") }
check("u5-views-have-cancel", "6 个可运行页的主按钮都变成「取消」（`model.cancel?.cancel()`）",
      uNoCancel.isEmpty,
      uNoCancel.isEmpty ? "6/6" : "缺：\(uNoCancel.joined(separator: "、"))")
check("u6-cancelled-banner", "完成横幅把「用户取消」显示成「⏹ 已取消」而不是 ❌ 失败",
      readSrc("Views/SimpleFeatureView.swift").contains("⏹ 已取消")
        && readSrc("Views/SimpleFeatureView.swift").contains("resultCancelled")
        && readSrc("Core/ViewModels.swift").contains("resultCancelled"),
      "")

// ===========================================================================
// (v) 报告输出（可选）：mac「文献回读校验」补 `--out`（2026-10-17 第十五批）
//
//     与 Windows 侧 U7 的三个可选输入对齐：留空 = 自动落点（一字未改），
//     填已存在的目录 = 报告放进该目录（引擎 `_resolve_out()` 同一判据）。
// ===========================================================================
section("T-v  报告输出（可选，mac --out）")

check("v1-view-field", "CheckBibView 有「报告输出（可选）」输入并传给 run(out:)",
      cbvSrc.contains("报告输出（可选）") && cbvSrc.contains("model.reportOut")
        && cbvSrc.contains("out: out"),
      "")
check("v2-feature-param", "CheckBibFeature.run 接受 `out rawOut` 且有 `resolvedReportPath`",
      cbfSrc.contains("out rawOut: String = \"\"") && cbfSrc.contains("resolvedReportPath(rawOut, auto: autoOut)"),
      "")
// 运行时：空 = 自动落点；目录 = 放进该目录（文件名沿用自动落点的基名）；文件 = 原样
let vAuto = "/tmp/投稿/reference_回读校验报告.md"
check("v3-empty-means-auto", "留空 → 自动落点（与从前一字不差）",
      CheckBibFeature.resolvedReportPath("", auto: vAuto) == vAuto
        && CheckBibFeature.resolvedReportPath("   ", auto: vAuto) == vAuto,
      "")
let vDir = FileManager.default.temporaryDirectory
    .appendingPathComponent("slcn_out_dir_\(UUID().uuidString)").path
try? FileManager.default.createDirectory(atPath: vDir, withIntermediateDirectories: true)
check("v4-dir-joins-default-name", "填已存在的目录 → 报告放进该目录、文件名沿用默认基名",
      CheckBibFeature.resolvedReportPath(vDir, auto: vAuto) == vDir + "/reference_回读校验报告.md",
      CheckBibFeature.resolvedReportPath(vDir, auto: vAuto))
let vFile = "/tmp/投稿/自定义报告.md"
check("v5-file-passes", "填文件路径 → 原样使用", CheckBibFeature.resolvedReportPath(vFile, auto: vAuto) == vFile, "")
try? FileManager.default.removeItem(atPath: vDir)

// ===========================================================================
// (w) U4 日志「摘要 / 原始」两层（2026-10-17 第十五批，用户拍板：默认折叠原始层）
//
//     两侧必须**同一套判据**：Swift 用 LogStore.isSummaryLine，Windows 用
//     engine.is_summary_line，都跑 `tools/log_cases.json` 这份跨平台夹具。
//     这里跑夹具 = 与 Windows 桥梁测试逐条对齐；词表分叉会两边同时变红。
// ===========================================================================
section("T-w  U4 日志摘要 / 原始两层（mac）")
let logSrc2 = readSrc("Core/Log.swift")
check("w1-store", "LogStore 有摘要层（summaryAttributed）、折叠计数与逐行分类",
      logSrc2.contains("summaryAttributed") && logSrc2.contains("foldedLineCount")
        && logSrc2.contains("static func isSummaryLine"),
      "")
var wWrong: [String] = []
var wCaseCount = 0
let wCasesPath = projFile("tools/log_cases.json")
if let wData = FileManager.default.contents(atPath: wCasesPath),
   let wObj = try? JSONSerialization.jsonObject(with: wData) as? [String: Any],
   let wCases = wObj["cases"] as? [[String: Any]] {
    wCaseCount = wCases.count
    for c in wCases {
        let line = (c["line"] as? String) ?? ""
        let want = (c["summary"] as? Bool) ?? false
        if LogStore.isSummaryLine(line) != want {
            wWrong.append("\(want ? "应摘要" : "应原始")「\(line)」")
        }
    }
    check("w2-fixture", "LogStore.isSummaryLine 逐条通过 tools/log_cases.json（与 Windows 同一份夹具，\(wCaseCount) 条）",
          wWrong.isEmpty, wWrong.prefix(3).joined(separator: "；"))
} else {
    check("w2-fixture", "读不到 tools/log_cases.json", false, wCasesPath)
}
let paneSrc = readSrc("Views/LogPane.swift")
check("w3-pane", "LogPane 有「摘要/原始」切换、折叠行提示与空状态提示；复制仍给完整日志",
      paneSrc.contains("Text(\"摘要\")") && paneSrc.contains("Text(\"原始\")")
        && paneSrc.contains("已折叠") && paneSrc.contains("运行任务后，日志会显示在这里")
        && paneSrc.contains("setString(log.text"),
      "")

// (x) 完成横幅的「契约行」判据（2026-10-17 第十五批真机实测抓到的真 bug）
//     现象：编译全部 10/10 成功、日志里也有「完成。编译 10 篇…」，横幅却显示 ❌ 失败。
//     根因：横幅取的是**最后一行**，而 Feature 在引擎结束后还会补「运行结束，请确认上方是否有错。」。
//     修法：`EngineOutput.contractLine(from:)` 取最后一条以 `完成。` 开头的行。
section("T-x  完成横幅：契约行 ≠ 最后一行")
let xContract = "完成。编译 10 篇（成功 10 / 失败 0）（目录：/tmp/2026年第5期(90)）"
check("x1-contract-line", "contractLine：跳过 Feature 补的那句「运行结束…」，取到真正的契约行",
      EngineOutput.contractLine(from: [xContract, "运行结束，请确认上方是否有错。"]) == xContract
        && EngineOutput.contractLine(from: ["运行结束，请确认上方是否有错。"]) == nil
        && EngineOutput.contractLine(from: []) == nil,
      "")
check("x2-view-uses-contract", "SimpleFeatureView 用 `contractLine(from:)` 判成败（不再取最后一行）",
      readSrc("Views/SimpleFeatureView.swift").contains("EngineOutput.contractLine(from: box.lines)"),
      "")
// 运行时：Logger 的旁路回调必须给**未加时间戳的原文** —— 横幅要按行首 `完成。` 判，
// 若回调收到 "+ 52.2s  完成。…" 就永远判不成成功（第十五批真机实测的第二个真因）。
let xStore = LogStore()
var xSeen: [String] = []
let xLogger = Logger(store: xStore, startedAt: Date(), onLine: { xSeen.append($0) })
xLogger("完成。编译 10 篇（成功 10 / 失败 0）（目录：/tmp/2026年第5期(90)）")
check("x3-online-raw", "Logger.onLine 收到原文（没有 `+  0.0s  ` 前缀），契约行判据可用",
      xSeen.count == 1
        && xSeen.first == "完成。编译 10 篇（成功 10 / 失败 0）（目录：/tmp/2026年第5期(90)）"
        && EngineOutput.indicatesSuccess(xSeen.first ?? ""),
      xSeen.first ?? "<empty>")

// ===========================================================================
// (k) 第 69 条：通道（chat/responses）+ 联网检索开关 + ⑤ 检索探针
//
//     为什么这批断言重要：用户的要求是「让工具**真的**去联网核实」。
//     界面能保证的只有「把 `--llm-protocol responses --llm-web-search` 发出去」，
//     **端点到底会不会检索**必须实测（`slcn/llm.py: probe_web_search`，判据是回复里
//     有没有 `web_search_call`）——因为有的端点/模型（如 DeepSeek 的 deepseek-flash）
//     会**静默忽略**这个工具并照样回 200（2026-10-17 起同厂 deepseek-v4-pro 会真检索）。
//     所以这里要同时钉住：默认逐字节不变、两个开关的接线、以及 ⑤ 的三态判词。
// ===========================================================================
section("T-k  通道 / 联网检索开关（第 69 条）")

// k1 默认值：不设时必须是 chat + 关（老行为一字不改）
check("k1-defaults", "AIEngineKnobs 默认 = chat + 检索关；usesWebSearch=false",
      AIEngineKnobs().apiProtocol == .chat && AIEngineKnobs().webSearch == false
        && AIEngineKnobs().usesWebSearch == false,
      "protocol=\(AIEngineKnobs().apiProtocol.rawValue) webSearch=\(AIEngineKnobs().webSearch)")

// k2 chat 通道下即使勾了检索也不算「会用检索」（chat 的 tools 是 function calling）
check("k2-chat-never-searches", "chat 通道 + 勾了检索 → usesWebSearch 仍为 false（通道优先）",
      AIEngineKnobs(apiProtocol: .chat, webSearch: true).usesWebSearch == false
        && AIEngineKnobs(apiProtocol: .responses, webSearch: true).usesWebSearch == true,
      "")

// k3 summary：通道与「有没有检索」必须写进那一行（编辑靠它核对）
let kSummary = AIEngineKnobs(apiProtocol: .responses, webSearch: true).summary
let kSummaryOff = AIEngineKnobs().summary
print("   [k3 summary on ] \(kSummary)")
print("   [k3 summary off] \(kSummaryOff)")
check("k3-summary-on", "开了检索：summary 写「responses」「联网检索**已启用**」，且仍含两个老旋钮",
      kSummary.contains("responses") && kSummary.contains("联网检索**已启用**")
        && kSummary.contains("思考模式") && kSummary.contains("2048"), kSummary)
check("k3-summary-off", "默认：summary 明说「未启用联网检索：模型只能凭记忆」",
      kSummaryOff.contains("chat") && kSummaryOff.contains("未启用联网检索"), kSummaryOff)

// k4 argv：默认**一个参数都不加**（与从前逐字节相同）；非默认才加
var kOpt = optB
kOpt.llmProtocol = "chat"; kOpt.llmWebSearch = false
let kArgvDefault = ConvertFeature.arguments(kOpt)
kOpt.llmProtocol = "responses"; kOpt.llmWebSearch = true
let kArgvOn = ConvertFeature.arguments(kOpt)
kOpt.llmProtocol = "responses"; kOpt.llmWebSearch = false
let kArgvRespOnly = ConvertFeature.arguments(kOpt)
print("   [k4 argv default] \(kArgvDefault.suffix(6))")
print("   [k4 argv on     ] \(kArgvOn.suffix(4))")
check("k4-argv-default-untouched", "默认（chat/关）时 argv 里**没有** --llm-protocol / --llm-web-search",
      !kArgvDefault.contains("--llm-protocol") && !kArgvDefault.contains("--llm-web-search"),
      "\(kArgvDefault.suffix(4))")
check("k4-argv-on", "responses + 检索 → 恰好 `--llm-protocol responses --llm-web-search`",
      kArgvOn.contains("--llm-protocol") && kArgvOn.contains("responses")
        && kArgvOn.contains("--llm-web-search"),
      "\(kArgvOn.suffix(4))")
check("k4-argv-responses-only", "只切通道不勾检索 → 有 --llm-protocol，**没有** --llm-web-search",
      kArgvRespOnly.contains("--llm-protocol") && !kArgvRespOnly.contains("--llm-web-search"),
      "\(kArgvRespOnly.suffix(4))")

// k5 engineEnvironment 不因新开关而改变（检索走 argv；环境变量仍是那三个）
var kOptEnv = optB
kOptEnv.llmProtocol = "responses"; kOptEnv.llmWebSearch = true
let kEnv = ConvertFeature.engineEnvironment(kOptEnv, toolDir: envDir,
                                            knobs: AIEngineKnobs(thinking: .disabled, maxTokens: "2048",
                                                                 apiProtocol: .responses, webSearch: true))
check("k5-env-unchanged", "engineEnvironment 仍只给 SLCN_LLM_KEY/THINKING/MAX_TOKENS（+PYTHONPATH）："
      + "检索开关走 argv，不在这里再发一份",
      kEnv["SLCN_LLM_WEB_SEARCH"] == nil && kEnv["SLCN_LLM_PROTOCOL"] == nil,
      dump(kEnv))

// k6 ai.json round-trip：两个新键存得下、读得回
let dk = "/tmp/ai_settings_test/k"
wipe(dk)
let sk = AppSettings(storeDirectory: URL(fileURLWithPath: dk))
sk.aiBase = "https://api.example.com/v1"; sk.aiModel = "m"; sk.aiKey = "sk-K-1234"
sk.aiProtocol = .responses; sk.aiWebSearch = true
let skSaved = sk.saveAI()
let kObj = json(rawFile(dk + "/ai.json"))
let skBack = AppSettings(storeDirectory: URL(fileURLWithPath: dk))
evidence("k6 ai.json", "\n----8<----\n\(rawFile(dk + "/ai.json"))\n---->8----")
check("k6-save", "saveAI() 写入 protocol=\"responses\" / webSearch=true",
      skSaved && (kObj["protocol"] as? String) == "responses" && (kObj["webSearch"] as? Bool) == true,
      "protocol=\(kObj["protocol"] ?? "<nil>") webSearch=\(kObj["webSearch"] ?? "<nil>")")
check("k6-reload", "新实例读回 protocol/webSearch 逐字一致，aiDirty=false",
      skBack.aiProtocol == .responses && skBack.aiWebSearch && !skBack.aiDirty,
      "protocol=\(skBack.aiProtocol.rawValue) webSearch=\(skBack.aiWebSearch) dirty=\(skBack.aiDirty)")
check("k6-knobs", "aiEngineKnobs 把两个新值也带上（转换时现取的就是它）",
      skBack.aiEngineKnobs.apiProtocol == .responses && skBack.aiEngineKnobs.webSearch,
      skBack.aiEngineKnobs.summary)

// k7 旧 ai.json（没有这两个键）→ 默认 chat / 关；坏值也不崩
let dk2 = "/tmp/ai_settings_test/k2"
wipe(dk2)
writeRaw(dk2 + "/ai.json", "{\"base\":\"b\",\"key\":\"\",\"model\":\"m\",\"thinking\":\"off\",\"maxTokens\":\"2048\"}", perm: 0o600)
let sk2 = AppSettings(storeDirectory: URL(fileURLWithPath: dk2))
check("k7-old-file-defaults", "旧文件（只有 base/key/model/thinking/maxTokens）→ 默认 chat + 检索关",
      sk2.aiProtocol == .chat && sk2.aiWebSearch == false && !sk2.aiDirty,
      "protocol=\(sk2.aiProtocol.rawValue) webSearch=\(sk2.aiWebSearch) dirty=\(sk2.aiDirty)")
writeRaw(dk2 + "/ai.json", "{\"base\":\"b\",\"model\":\"m\",\"protocol\":\"gpt5\",\"webSearch\":\"yes\"}", perm: 0o600)
let sk2b = AppSettings(storeDirectory: URL(fileURLWithPath: dk2))
check("k7-bad-values", "认不出的 protocol / 类型不对的 webSearch → 回落默认（读配置绝不失败）",
      sk2b.aiProtocol == .chat && sk2b.aiWebSearch == false,
      "protocol=\(sk2b.aiProtocol.rawValue) webSearch=\(sk2b.aiWebSearch)")
check("k7-loadEngineKnobs", "loadEngineKnobs 也读回这两个值（后台兜底路径）",
      AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: dk)).apiProtocol == .responses
        && AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: dk)).webSearch,
      "")

// k8 clearAI 也要把两个新旋钮回默认（与另两个旋钮同一口径；且**不落盘**，见第 62 条）
let dk3 = "/tmp/ai_settings_test/k3"
wipe(dk3)
let sk3 = AppSettings(storeDirectory: URL(fileURLWithPath: dk3))
sk3.aiBase = "b"; sk3.aiModel = "m"; sk3.aiProtocol = .responses; sk3.aiWebSearch = true
_ = sk3.saveAI()
let kBefore = rawFile(dk3 + "/ai.json")
sk3.clearAI()
check("k8-clear", "清空后界面回 chat/关、aiDirty=true，且磁盘 sha 未变（第 62 条不被破坏）",
      sk3.aiProtocol == .chat && sk3.aiWebSearch == false && sk3.aiDirty
        && rawFile(dk3 + "/ai.json") == kBefore,
      "protocol=\(sk3.aiProtocol.rawValue) webSearch=\(sk3.aiWebSearch) dirty=\(sk3.aiDirty)")

// ── k14–k18（P2，第 72 条起）：参考文献检索证据开关 ──────────────────────
// 与「联网检索」是两件事：searchRefs 走 DeepSeek 独立 Search API（`--search-refs`），
// 默认关；关着时 ai.json 与 argv 都与旧版逐字节相同。
check("k14-default", "检索证据开关默认关（旧 ai.json 没有这个键也是关）",
      AIEngineKnobs().searchRefs == false
        && AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: dk)).searchRefs == false,
      "knob=\(AIEngineKnobs().searchRefs)")

let dkRefs = "/tmp/ai_settings_test/k_refs"
wipe(dkRefs)
let skRefs = AppSettings(storeDirectory: URL(fileURLWithPath: dkRefs))
skRefs.aiBase = "b"; skRefs.aiModel = "m"
skRefs.aiSearchRefs = true
let refsSaved = skRefs.saveAI()
let refsObj = json(rawFile(dkRefs + "/ai.json"))
check("k15-save", "开启后 saveAI() 把 `searchRefs: true` 写进 ai.json",
      refsSaved && (refsObj["searchRefs"] as? Bool) == true,
      "searchRefs=\(refsObj["searchRefs"] ?? "<nil>")")
let skRefsBack = AppSettings(storeDirectory: URL(fileURLWithPath: dkRefs))
check("k16-reload", "新实例读回 searchRefs=true，且 aiDirty=false；后台兜底路径也读得到",
      skRefsBack.aiSearchRefs && !skRefsBack.aiDirty
        && skRefsBack.aiEngineKnobs.searchRefs
        && AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: dkRefs)).searchRefs,
      "ui=\(skRefsBack.aiSearchRefs) dirty=\(skRefsBack.aiDirty)")
// 关着时**不写键**：旧 ai.json 的「逐字节相同」这条硬要求不能被新字段破坏
let dkRefsOff = "/tmp/ai_settings_test/k_refs_off"
wipe(dkRefsOff)
let skRefsOff = AppSettings(storeDirectory: URL(fileURLWithPath: dkRefsOff))
skRefsOff.aiBase = "b"; skRefsOff.aiModel = "m"
_ = skRefsOff.saveAI()
let refsOffObj = json(rawFile(dkRefsOff + "/ai.json"))
check("k17-absent-when-off", "关着时不写 searchRefs 键（与旧版 ai.json 逐字节相同）",
      refsOffObj["searchRefs"] == nil,
      "keys=\(refsOffObj.keys.sorted())")
// 配置方案：开启才写键、读回一致；applyProfile 能把它带回界面
let profTrue = AIProfile(name: "有证据", base: "b", key: "k", model: "m",
                         thinking: .followEndpoint, maxTokens: "2048",
                         proto: .chat, webSearch: false, searchTool: "",
                         responsesPath: "", searchRefs: true)
let profFalse = AIProfile(name: "无证据", base: "b", key: "k", model: "m",
                          thinking: .followEndpoint, maxTokens: "2048",
                          proto: .chat, webSearch: false, searchTool: "", responsesPath: "")
let skProf = AppSettings(storeDirectory: URL(fileURLWithPath: dkRefs))
skProf.aiProfiles = [profTrue, profFalse]
let profApplied = skProf.applyProfile(named: "有证据")
check("k18-profile", "方案 toDict：开着才带 searchRefs 键、关着不带；applyProfile 能把它填回界面",
      (profTrue.toDict()["searchRefs"] as? Bool) == true
        && profFalse.toDict()["searchRefs"] == nil
        && profApplied && skProf.aiSearchRefs,
      "true=\(profTrue.toDict()["searchRefs"] ?? "<nil>") false=\(profFalse.toDict()["searchRefs"] ?? "<nil>") applied=\(skProf.aiSearchRefs)")

// k9 ⑤ 的接线：跑引擎、参数固定、密钥走环境变量（不进 argv）
check("k9-probe-argv", "⑤ 探针拼的是引擎 CLI：`-m slcn.llm --probe --json`",
      AIConnectionTester.searchProbeArguments() == ["-m", "slcn.llm", "--probe", "--json"],
      "\(AIConnectionTester.searchProbeArguments())")
let probeSrc = body(after: "static func runSearchProbe(", in: settingsSrc)
check("k9-key-via-env", "⑤ 把密钥放进**子进程环境**（SLCN_LLM_KEY），**不进 argv**",
      probeSrc.contains("env[\"SLCN_LLM_KEY\"] = key") && !probeSrc.contains("\"--key\""),
      "")
check("k19-ui", "设置页有「检索证据」开关（绑定 aiSearchRefs）+ 说明写清「独立 Search API/按次计费/默认关」",
      settingsSrc.contains("$settings.aiSearchRefs")
        && settingsSrc.contains("DeepSeek 独立 Search API")
        && settingsSrc.contains("按次计费"),
      "toggle=\(settingsSrc.contains("$settings.aiSearchRefs")) "
        + "独立=\(settingsSrc.contains("DeepSeek 独立 Search API")) "
        + "计费=\(settingsSrc.contains("按次计费"))")
check("k20-converttab", "转换页把 aiSearchRefs 现取现传（与 base/model 同一口径）+ 说明有据可查",
      tabSrc.contains("o.searchRefs = settings.aiSearchRefs")
        && tabSrc.contains("settings.aiSearchRefs"),
      "wired=\(tabSrc.contains("o.searchRefs = settings.aiSearchRefs"))")
check("k9-engine-located", "⑤ 走与转换同一条查找路径（locateTool/locatePython），找不到就诚实说「没测出来」",
      probeSrc.contains("ConvertFeature.locateTool()") && probeSrc.contains("ConvertFeature.locatePython(")
        && probeSrc.contains("没找到 Engine/convert.py"),
      "")

// k10 parseSearchProbe：JSON → 结构；垃圾输入 → nil（不许瞎猜）
let kJson = "{\"verdict\":\"supported\",\"verdict_cn\":\"支持\",\"detail\":\"d\","
    + "\"endpoint\":\"http://x/v1/responses\",\"model\":\"m\",\"n_search_calls\":2,"
    + "\"http_code\":0,\"urls\":[\"https://a\"],\"body\":\"\"}"
let kParsed = AIConnectionTester.parseSearchProbe(kJson)
print("   [k10 parsed] \(String(describing: kParsed))")
check("k10-parse-ok", "解析引擎 JSON：verdict/verdict_cn/n_search_calls/urls/endpoint 都对上",
      kParsed?.verdict == "supported" && kParsed?.verdictCN == "支持"
        && kParsed?.nSearchCalls == 2 && kParsed?.urls == ["https://a"]
        && kParsed?.endpoint == "http://x/v1/responses",
      String(describing: kParsed))
check("k10-parse-garbage", "不是 JSON / 没有 verdict → nil（界面说「没测出来」，绝不猜成支持）",
      AIConnectionTester.parseSearchProbe("Traceback...") == nil
        && AIConnectionTester.parseSearchProbe("{}") == nil,
      "")

// k11 ⑤ 的三态判词 + 「端点会不会查」与「本次会不会用」两层分开说
let pSup = AIConnectionTester.SearchProbe(verdict: "supported", verdictCN: "支持",
                                          detail: "端点真的检索了", endpoint: "e", model: "m",
                                          nSearchCalls: 3, httpCode: 0,
                                          urls: ["https://a", "https://b"], body: "")
let pUn = AIConnectionTester.SearchProbe(verdict: "unsupported", verdictCN: "不支持",
                                         detail: "端点返回 200，但没有任何检索证据——静默忽略",
                                         endpoint: "e", model: "m", nSearchCalls: 0,
                                         httpCode: 0, urls: [], body: "{\"error\":\"x\"}")
let pBad = AIConnectionTester.SearchProbe(verdict: "error", verdictCN: "其他错误",
                                          detail: "超时", endpoint: "e", model: "m")
let lineSupOK = AIConnectionTester.searchProbeLine(pSup, engineNote: nil,
                                                   protocolWanted: .responses, searchEnabled: true)
let lineSupChat = AIConnectionTester.searchProbeLine(pSup, engineNote: nil,
                                                     protocolWanted: .chat, searchEnabled: false)
let lineUnOff = AIConnectionTester.searchProbeLine(pUn, engineNote: nil,
                                                   protocolWanted: .chat, searchEnabled: false)
let lineUnOn = AIConnectionTester.searchProbeLine(pUn, engineNote: nil,
                                                  protocolWanted: .responses, searchEnabled: true)
let lineBad = AIConnectionTester.searchProbeLine(pBad, engineNote: nil,
                                                 protocolWanted: .responses, searchEnabled: true)
let lineSkip = AIConnectionTester.searchProbeLine(nil, engineNote: "没找到 python3",
                                                  protocolWanted: .responses, searchEnabled: true)
for (n, l) in [("sup-ok", lineSupOK), ("sup-chat", lineSupChat), ("un-off", lineUnOff),
               ("un-on", lineUnOn), ("bad", lineBad), ("skip", lineSkip)] {
    print("   [k11 \(n)] \(l.replacingOccurrences(of: "\n", with: "⏎"))")
}
check("k11-supported-plain", "支持 + responses + 勾了 → 明说「会真的联网查官方来源」且不再有警告",
      lineSupOK.contains("✓ **支持**") && lineSupOK.contains("会真的联网查官方来源")
        && lineSupOK.contains("会**真的联网**") && !lineSupOK.contains("⚠ 但当前「通道」"),
      "")
check("k11-supported-but-chat", "支持但通道是 chat → 指出 chat 不带检索工具（不能报成已联网）",
      lineSupChat.contains("✓ **支持**") && lineSupChat.contains("但当前「通道」是 chat"),
      "")
check("k11-unsupported-plain", "不支持 → 明说「模型只能凭记忆」+ 提醒人工抽查",
      lineUnOff.contains("✗ **不支持**") && lineUnOff.contains("模型只能凭记忆")
        && lineUnOff.contains("人工抽查"),
      "")
check("k11-unsupported-but-switch-on", "不支持而用户勾了检索 → 明说本次**不会**真的查到网络（不假装）",
      lineUnOn.contains("不会**真的查到网络") || lineUnOn.contains("不会**真的**查到网络")
        || lineUnOn.contains("本次**不会**"),
      "")
check("k11-error-is-not-a-verdict", "其他错误 → 「这次没测出来」（既不算支持也不算不支持）",
      lineBad.contains("**其他错误**") && lineBad.contains("没测出来"),
      "")
check("k11-skip-is-honest", "测不了（没引擎/超时）→ 说清原因 + 让用户以日志的检索证据为准",
      lineSkip.contains("跳过/没测出来") && lineSkip.contains("检索证据"),
      "")
check("k11-quotes-endpoint-body", "不支持/错误时把端点原话带上（编辑要知道它到底说了什么）",
      lineUnOn.contains("端点原话"), "")

// k12 抬头与文案：⑤ 必须出现在抬头里
let kHead = AIConnectionTester.header(base: "https://api.deepseek.com/v1", key: "sk-K-1234",
                                      model: "deepseek-flash",
                                      knobs: AIEngineKnobs(apiProtocol: .responses, webSearch: true),
                                      unsaved: false)
check("k12-header-probe", "抬头点出「外加 ⑤ 引擎检索探针」，且两个旋钮那一行含通道与检索状态",
      kHead.contains("⑤") && kHead.contains("responses") && kHead.contains("联网检索"),
      kHead.replacingOccurrences(of: "\n", with: "⏎"))

// k13 源码级：设置页真的有这两个控件，且转换页把现值取过去
check("k13-ui-controls", "设置页有「通道」Picker 与「联网检索」开关 + 说明"
      + "（点名 DeepSeek 按模型区分：flash 仍忽略、v4-pro 才真检索）+ 「绝不假装已联网」",
      settingsSrc.contains("$settings.aiProtocol") && settingsSrc.contains("$settings.aiWebSearch")
        && settingsSrc.contains("忽略") && settingsSrc.contains("deepseek-v4-pro")
        && settingsSrc.contains("绝不假装已联网"),
      "picker=\(settingsSrc.contains("$settings.aiProtocol")) toggle=\(settingsSrc.contains("$settings.aiWebSearch")) "
        + "忽略=\(settingsSrc.contains("忽略")) v4pro=\(settingsSrc.contains("deepseek-v4-pro")) "
        + "不假装=\(settingsSrc.contains("绝不假装已联网"))")
check("k13-converttab-live", "转换页现取 settings.aiProtocol / aiWebSearch（与 base/model 同一口径）",
      tabSrc.contains("o.llmProtocol = settings.aiProtocol.rawValue")
        && tabSrc.contains("o.llmWebSearch = settings.aiWebSearch"),
      "")

// ===========================================================================
// (l) 第 70 条：两个「未来兼容」旋钮（检索工具名 / responses 路径）
//
//     为什么这批断言重要：第 69 条把「联网检索」做成开关后，请求里还有两处硬编码
//     （`tools:[{"type":"web_search"}]` 与 `{base}/responses`）。DeepSeek 将来若真的
//     实现内置检索，名字/路径都可能变（OpenAI 就有 `web_search_preview`）。所以本轮让
//     编辑能改这两处，而**默认留空 = 一个参数都不加**（命令行与从前逐字节相同）——
//     这批断言同时钉住「默认不变」与「填了才传、填错不传且出声」。
// ===========================================================================
section("T-l  检索工具名 / responses 路径（第 70 条）")

// l1 默认值：两栏都空（= 不传 → 引擎默认 web_search / /responses）
check("l1-defaults", "AIEngineKnobs 默认两栏都是空串（空 = 用引擎默认、不传参数）",
      AIEngineKnobs().searchTool.isEmpty && AIEngineKnobs().responsesPath.isEmpty,
      "searchTool=\(AIEngineKnobs().searchTool.debugDescription) "
        + "responsesPath=\(AIEngineKnobs().responsesPath.debugDescription)")

// l2 解析口径：留空 = unset；合法 = value；结构上不可能 = invalid（与引擎同口径）
let lToolOk = AIAdvancedKnobs.parseSearchTool(" web_search_preview ")
let lToolBad = AIAdvancedKnobs.parseSearchTool("web search")
let lToolBad2 = AIAdvancedKnobs.parseSearchTool("{x}")
let lPathOk = AIAdvancedKnobs.parseResponsesPath("/v1/responses")
let lPathNoSlash = AIAdvancedKnobs.parseResponsesPath("responses")
let lPathBad = AIAdvancedKnobs.parseResponsesPath("https://host/responses")
let lPathBad2 = AIAdvancedKnobs.parseResponsesPath("/a b")
check("l2-tool-parse", "工具名：留空=unset、合法 trim 后取值、含空白/花括号=invalid",
      AIAdvancedKnobs.parseSearchTool("") == .unset
        && lToolOk == .value("web_search_preview")
        && AIAdvancedKnobs.hint(lToolBad) != nil && AIAdvancedKnobs.hint(lToolBad2) != nil,
      "ok=\(lToolOk) bad=\(String(describing: AIAdvancedKnobs.hint(lToolBad)))")
check("l2-path-parse", "路径：留空=unset、`responses`（缺前导 /）算合法（引擎补）、"
      + "带 :// 或空白=invalid",
      AIAdvancedKnobs.parseResponsesPath("") == .unset
        && lPathOk == .value("/v1/responses") && lPathNoSlash == .value("responses")
        && AIAdvancedKnobs.hint(lPathBad) != nil && AIAdvancedKnobs.hint(lPathBad2) != nil,
      "ok=\(lPathOk) noslash=\(lPathNoSlash) bad=\(String(describing: AIAdvancedKnobs.hint(lPathBad)))")
check("l2-envvalue", "envValue：unset/invalid → nil（= **不传**），value → 字符串",
      AIAdvancedKnobs.envValue(AIAdvancedKnobs.parseSearchTool("")) == nil
        && AIAdvancedKnobs.envValue(lToolBad) == nil
        && AIAdvancedKnobs.envValue(lToolOk) == "web_search_preview"
        && AIAdvancedKnobs.envValue(AIAdvancedKnobs.parseResponsesPath("/responses")) == "/responses",
      "")

// l3 argv：默认/留空**一个参数都不加**；填了才加；填错也不加（界面承诺 = 引擎收到的东西）
var lOpt = optB
lOpt.llmSearchTool = ""; lOpt.llmResponsesPath = ""
let lArgvDefault = ConvertFeature.arguments(lOpt)
lOpt.llmSearchTool = "web_search_preview"; lOpt.llmResponsesPath = "/v1/responses"
let lArgvOn = ConvertFeature.arguments(lOpt)
lOpt.llmSearchTool = "web search"; lOpt.llmResponsesPath = "https://host/x"
let lArgvBad = ConvertFeature.arguments(lOpt)
print("   [l3 argv default] \(lArgvDefault.suffix(4))")
print("   [l3 argv on     ] \(lArgvOn.suffix(4))")
print("   [l3 argv bad    ] \(lArgvBad.suffix(4))")
check("l3-argv-default-untouched", "两栏留空：argv 里**没有** --llm-search-tool / --llm-responses-path"
      + "（默认命令行与从前逐字节相同）",
      !lArgvDefault.contains("--llm-search-tool") && !lArgvDefault.contains("--llm-responses-path"),
      "\(lArgvDefault.suffix(4))")
check("l3-argv-on", "填了 → 恰好 `--llm-search-tool web_search_preview` 与 "
      + "`--llm-responses-path /v1/responses`",
      lArgvOn.contains("--llm-search-tool") && lArgvOn.contains("web_search_preview")
        && lArgvOn.contains("--llm-responses-path") && lArgvOn.contains("/v1/responses"),
      "\(lArgvOn.suffix(4))")
check("l3-argv-bad-not-passed", "填了用不了的值（空白工具名 / :// 路径）→ **不传**这两个参数"
      + "（由 knobWarnings 出声，不静默失效）",
      !lArgvBad.contains("--llm-search-tool") && !lArgvBad.contains("--llm-responses-path"),
      "\(lArgvBad.suffix(4))")

// l4 这两个旋钮走 argv，不进环境变量（与 protocol / web-search 同一口径）
let lEnv = ConvertFeature.engineEnvironment(lOpt, toolDir: toolVendor,
                                            knobs: AIEngineKnobs(searchTool: "web_search_preview",
                                                                 responsesPath: "/v1/responses"))
check("l4-env-unchanged", "engineEnvironment 里没有 SLCN_LLM_SEARCH_TOOL / SLCN_LLM_RESPONSES_PATH"
      + "（走 argv，与协议/检索开关一致）",
      lEnv["SLCN_LLM_SEARCH_TOOL"] == nil && lEnv["SLCN_LLM_RESPONSES_PATH"] == nil,
      dump(lEnv))

// l5 knobWarnings：填错要有一行说明；留空/合法没有多余警告
var lBadKnobs = AIEngineKnobs(thinking: .disabled, maxTokens: "2048")
lBadKnobs.searchTool = "web search"; lBadKnobs.responsesPath = "https://host/x"
let lWarns = ConvertFeature.knobWarnings(lBadKnobs)
for w in lWarns { print("   [l5 warn] \(w.replacingOccurrences(of: "\n", with: "⏎"))") }
check("l5-warn-invalid", "两个旋钮填错 → 各一行中文说明（含「不会」），且不影响合法上限",
      lWarns.count == 2 && lWarns.allSatisfy { $0.contains("不会") },
      "warnings=\(lWarns.count) 行")
check("l5-warn-clean", "留空 / 合法值 → 没有多余警告（空就是「用引擎默认」，不必打扰）",
      ConvertFeature.knobWarnings(AIEngineKnobs()).isEmpty
        && ConvertFeature.knobWarnings(AIEngineKnobs(searchTool: "web_search_preview",
                                                     responsesPath: "/responses")).isEmpty,
      "default=\(ConvertFeature.knobWarnings(AIEngineKnobs()).count) "
        + "ok=\(ConvertFeature.knobWarnings(AIEngineKnobs(searchTool: "web_search_preview", responsesPath: "/responses")).count)")

// l6 ai.json round-trip：两栏存得下、读得回；旧文件/坏类型 → 空串
let dl = "/tmp/ai_settings_test/l"
wipe(dl)
let sl = AppSettings(storeDirectory: URL(fileURLWithPath: dl))
sl.aiBase = "https://api.example.com/v1"; sl.aiModel = "m"; sl.aiKey = "sk-L-1234"
sl.aiProtocol = .responses; sl.aiWebSearch = true
sl.aiSearchTool = "  web_search_preview  "; sl.aiResponsesPath = "responses"
let slSaved = sl.saveAI()
let lObj = json(rawFile(dl + "/ai.json"))
let slBack = AppSettings(storeDirectory: URL(fileURLWithPath: dl))
evidence("l6 ai.json", "\n----8<----\n\(rawFile(dl + "/ai.json"))\n---->8----")
check("l6-save", "saveAI() 写入 searchTool / responsesPath（trim 之后），且两栏不再是「未保存」",
      slSaved && (lObj["searchTool"] as? String) == "web_search_preview"
        && (lObj["responsesPath"] as? String) == "responses" && !sl.aiDirty,
      "searchTool=\(lObj["searchTool"] ?? "<nil>") responsesPath=\(lObj["responsesPath"] ?? "<nil>") "
        + "dirty=\(sl.aiDirty)")
check("l6-reload", "新实例读回两栏逐字一致、aiDirty=false、aiEngineKnobs 带上它们",
      slBack.aiSearchTool == "web_search_preview" && slBack.aiResponsesPath == "responses"
        && !slBack.aiDirty
        && slBack.aiEngineKnobs.searchTool == "web_search_preview"
        && slBack.aiEngineKnobs.responsesPath == "responses",
      "searchTool=\(slBack.aiSearchTool) responsesPath=\(slBack.aiResponsesPath) dirty=\(slBack.aiDirty)")

let dl2 = "/tmp/ai_settings_test/l2"
wipe(dl2)
writeRaw(dl2 + "/ai.json",
         "{\"base\":\"b\",\"key\":\"\",\"model\":\"m\",\"thinking\":\"off\",\"maxTokens\":\"2048\"}",
         perm: 0o600)
let sl2 = AppSettings(storeDirectory: URL(fileURLWithPath: dl2))
check("l7-old-file-defaults", "旧 ai.json（没有这两个键）→ 两栏空串、aiDirty=false（读配置绝不失败）",
      sl2.aiSearchTool.isEmpty && sl2.aiResponsesPath.isEmpty && !sl2.aiDirty,
      "searchTool=\(sl2.aiSearchTool.debugDescription) responsesPath=\(sl2.aiResponsesPath.debugDescription) dirty=\(sl2.aiDirty)")
writeRaw(dl2 + "/ai.json",
         "{\"base\":\"b\",\"model\":\"m\",\"searchTool\":123,\"responsesPath\":[\"x\"]}",
         perm: 0o600)
let sl2b = AppSettings(storeDirectory: URL(fileURLWithPath: dl2))
check("l7-bad-types", "类型不对的 searchTool / responsesPath → 回落空串（读配置绝不崩）",
      sl2b.aiSearchTool.isEmpty && sl2b.aiResponsesPath.isEmpty,
      "searchTool=\(sl2b.aiSearchTool.debugDescription) responsesPath=\(sl2b.aiResponsesPath.debugDescription)")
let dl3 = "/tmp/ai_settings_test/l3"
wipe(dl3)
let sl3 = AppSettings(storeDirectory: URL(fileURLWithPath: dl3))
sl3.aiBase = "b"; sl3.aiModel = "m"; sl3.aiSearchTool = "web_search_preview"
sl3.aiResponsesPath = "/responses"
_ = sl3.saveAI()
check("l8-loadEngineKnobs", "loadEngineKnobs（后台兜底路径）也读回这两栏",
      AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: dl3)).searchTool == "web_search_preview"
        && AppSettings.loadEngineKnobs(storeDirectory: URL(fileURLWithPath: dl3)).responsesPath == "/responses",
      "")

// l9 clearAI：两栏回空串；且**不落盘**（第 62 条不被破坏）
let lBefore = rawFile(dl3 + "/ai.json")
sl3.clearAI()
check("l9-clear", "清空后两栏回空串、aiDirty=true，且磁盘 sha 未变（第 62 条不被破坏）",
      sl3.aiSearchTool.isEmpty && sl3.aiResponsesPath.isEmpty && sl3.aiDirty
        && rawFile(dl3 + "/ai.json") == lBefore,
      "searchTool=\(sl3.aiSearchTool.debugDescription) dirty=\(sl3.aiDirty)")

// l10 summary：填了才出现（默认那一行与 k3 断言的那份逐字相同）
let lSumDefault = AIEngineKnobs(apiProtocol: .responses, webSearch: true).summary
let lSumCustom = AIEngineKnobs(apiProtocol: .responses, webSearch: true,
                               searchTool: "web_search_preview",
                               responsesPath: "/v1/responses").summary
print("   [l10 summary default] \(lSumDefault)")
print("   [l10 summary custom ] \(lSumCustom)")
check("l10-summary-default-unchanged", "两栏留空时 summary 与 k3 那份**逐字相同**（不多一个字）",
      lSumDefault == kSummary, lSumDefault)
check("l10-summary-custom", "填了时 summary 写明工具名与路径（编辑/日志能核对这次发了什么）",
      lSumCustom.contains("工具名 web_search_preview") && lSumCustom.contains("路径 /v1/responses"),
      lSumCustom)
check("l10-summary-invalid-omitted", "填了用不了的值时 summary **不**写它（只写真正会发出去的东西）",
      !AIEngineKnobs(apiProtocol: .responses, webSearch: true,
                     searchTool: "web search", responsesPath: "https://host/x")
        .summary.contains("工具名"),
      AIEngineKnobs(apiProtocol: .responses, webSearch: true,
                    searchTool: "web search", responsesPath: "https://host/x").summary)

// l11 源码级：设置页真的有这两栏（带默认提示），转换页把现值取过去
check("l11-ui-controls", "设置页有「检索工具名」「响应路径」两栏，绑定 aiSearchTool / aiResponsesPath，"
      + "占位符就是引擎默认值、并写明「留空 = 用引擎默认」",
      settingsSrc.contains("$settings.aiSearchTool") && settingsSrc.contains("$settings.aiResponsesPath")
        && settingsSrc.contains("AIAdvancedKnobs.defaultSearchTool")
        && settingsSrc.contains("AIAdvancedKnobs.defaultResponsesPath")
        && settingsSrc.contains("留空 = 用引擎默认"),
      "tool=\(settingsSrc.contains("$settings.aiSearchTool")) path=\(settingsSrc.contains("$settings.aiResponsesPath"))")
check("l11-converttab-live", "转换页现取 settings.aiSearchTool / aiResponsesPath（与 base/model 同一口径）",
      tabSrc.contains("o.llmSearchTool = settings.aiSearchTool")
        && tabSrc.contains("o.llmResponsesPath = settings.aiResponsesPath"),
      "")

// l12 ⑤ 探针也必须带上这两个旋钮：否则「体检说支持」测的是默认值，而转换打的是另一个
//     工具名/路径 —— 那是最坏的一种假证据（界面承诺与真实请求对不上）。
check("l12-probe-knobs-empty", "⑤ 探针：两栏留空 → **不加**这两个参数（与从前逐字节相同）",
      AIConnectionTester.searchProbeKnobArguments(searchTool: "", responsesPath: "").isEmpty,
      "\(AIConnectionTester.searchProbeKnobArguments(searchTool: "", responsesPath: ""))")
check("l12-probe-knobs-on", "⑤ 探针：填了 → 恰好带上 `--llm-search-tool … --llm-responses-path …`",
      AIConnectionTester.searchProbeKnobArguments(searchTool: "web_search_preview",
                                                  responsesPath: "responses")
        == ["--llm-search-tool", "web_search_preview", "--llm-responses-path", "responses"],
      "\(AIConnectionTester.searchProbeKnobArguments(searchTool: "web_search_preview", responsesPath: "responses"))")
check("l12-probe-knobs-bad", "⑤ 探针：填了用不了的值 → 也不加参数（引擎回落默认，与转换同口径）",
      AIConnectionTester.searchProbeKnobArguments(searchTool: "web search",
                                                  responsesPath: "https://host/x").isEmpty,
      "\(AIConnectionTester.searchProbeKnobArguments(searchTool: "web search", responsesPath: "https://host/x"))")
let probeBody = body(after: "static func runSearchProbe(", in: settingsSrc)
check("l12-probe-wiring", "⑤ 的跑法真的把那两个参数拼进 argv，并且 test() 把界面旋钮传下去",
      probeBody.contains("searchProbeKnobArguments(searchTool: knobs.searchTool")
        && settingsSrc.contains("knobs: knobs)"),
      "body=\(probeBody.contains("searchProbeKnobArguments(searchTool: knobs.searchTool")) "
        + "call=\(settingsSrc.contains("knobs: knobs)"))")

// ═══════════════════════════════════════════════════════════════════════════
// (m) 第 82 条：保存过的**配置方案**（多套 API 之间来回换）
//     用户实测反馈：换一家服务商重填之后，上一家的配置（含密钥）就再也找不到了。
// ═══════════════════════════════════════════════════════════════════════════
print("\n── (m) 配置方案：存 / 选 / 覆盖 / 删 / 旧文件兼容 ──")

let dm = "/tmp/ai_settings_test/m"
wipe(dm)
let sm = AppSettings(storeDirectory: URL(fileURLWithPath: dm))

// m1 留空方案名保存 → **不写 profiles 键**（没用过方案的编辑，文件与从前逐字节相同）
sm.aiBase = "https://api.deepseek.com/v1"; sm.aiKey = "sk-M-1111"; sm.aiModel = "deepseek-flash"
check("m1-save-noname", "方案名留空 → 只写「当前配置」，**不出现** profiles 键",
      sm.saveAI() && json(rawFile(dm + "/ai.json"))["profiles"] == nil,
      "\(json(rawFile(dm + "/ai.json"))["profiles"] ?? "<无 profiles 键>")")

// m2 填了方案名 → 存成一份，并立刻选中它
sm.aiProfileName = "DeepSeek 云端"
check("m2-save-named", "填了方案名 → profiles 里出现一份，且被选中",
      sm.saveAI() && sm.aiProfiles.count == 1
        && sm.aiProfiles[0].name == "DeepSeek 云端"
        && sm.aiProfiles[0].key == "sk-M-1111"
        && sm.aiSelectedProfile == "DeepSeek 云端",
      "\(sm.aiProfiles.map { $0.name }) selected=\(sm.aiSelectedProfile)")

// m3 换一套再存 → 两份都在（第一份**不许被动**）
sm.aiBase = "http://127.0.0.1:11434/v1"; sm.aiKey = ""; sm.aiModel = "qwen-plus"
sm.aiProtocol = .responses; sm.aiWebSearch = true
sm.aiProfileName = "本机 Ollama"
check("m3-second", "换一套再存 → 两份都在，第一份逐字未动",
      sm.saveAI() && sm.aiProfiles.count == 2
        && sm.aiProfiles[0].name == "DeepSeek 云端" && sm.aiProfiles[0].model == "deepseek-flash"
        && sm.aiProfiles[0].key == "sk-M-1111"
        && sm.aiProfiles[1].name == "本机 Ollama" && sm.aiProfiles[1].protocolRaw == "responses"
        && sm.aiProfiles[1].webSearch,
      "\(sm.aiProfiles.map { "\($0.name)/\($0.model)" })")

// m4 重启后两份都还在（真正落盘了）
let smBack = AppSettings(storeDirectory: URL(fileURLWithPath: dm))
check("m4-reload", "新实例读回两份方案，字段逐字一致（含密钥、通道、检索开关）",
      smBack.aiProfiles.count == 2
        && smBack.aiProfiles[0].key == "sk-M-1111"
        && smBack.aiProfiles[1].base == "http://127.0.0.1:11434/v1"
        && smBack.aiProfiles[1].protocolRaw == "responses",
      "\(smBack.aiProfiles.map { $0.name })")

// m5 同名再存 = **覆盖**（不新增、不重名）
smBack.aiModel = "deepseek-reasoner"; smBack.aiProfileName = "DeepSeek 云端"
check("m5-upsert", "同名再存 → 仍然两份，那一份被更新（不重名、原位置不变）",
      smBack.saveAI() && smBack.aiProfiles.count == 2
        && smBack.aiProfiles[0].name == "DeepSeek 云端"
        && smBack.aiProfiles[0].model == "deepseek-reasoner"
        && smBack.aiProfiles[1].name == "本机 Ollama",
      "\(smBack.aiProfiles.map { "\($0.name)/\($0.model)" })")

// m6 选一个方案 → 填进输入框，但**不落盘**（与「转换时用当前值」一致）
let dm2 = "/tmp/ai_settings_test/m2"
wipe(dm2)
let sm2 = AppSettings(storeDirectory: URL(fileURLWithPath: dm2))
sm2.aiBase = "https://a.example.com/v1"; sm2.aiKey = "sk-A"; sm2.aiModel = "model-a"
sm2.aiProfileName = "A 家"
_ = sm2.saveAI()
sm2.aiBase = "https://b.example.com/v1"; sm2.aiKey = "sk-B"; sm2.aiModel = "model-b"
sm2.aiProfileName = "B 家"
_ = sm2.saveAI()
let beforeApply = rawFile(dm2 + "/ai.json")
sm2.aiBase = "改坏了"; sm2.aiKey = "改坏了"; sm2.aiModel = "改坏了"
let applied = sm2.applyProfile(named: "A 家")
check("m6-apply", "选中方案 → 地址/密钥/模型被填回输入框",
      applied && sm2.aiBase == "https://a.example.com/v1" && sm2.aiKey == "sk-A"
        && sm2.aiModel == "model-a" && sm2.aiProfileName == "A 家",
      "\(sm2.aiBase) / \(sm2.aiModel) / name=\(sm2.aiProfileName)")
check("m6-apply-nosave", "选中方案**不写盘**（要用它还得点「保存」）",
      rawFile(dm2 + "/ai.json") == beforeApply,
      "文件是否变化：\(rawFile(dm2 + "/ai.json") != beforeApply)")

// m7 删除 → 立刻落盘，另一份不受影响
let delOK = sm2.deleteProfile(named: "B 家")
check("m7-delete", "删除方案 → 列表少一份、磁盘同步、另一份完好",
      delOK && sm2.aiProfiles.count == 1 && sm2.aiProfiles[0].name == "A 家"
        && sm2.aiProfiles[0].key == "sk-A"
        && !rawFile(dm2 + "/ai.json").contains("B 家"),
      "\(sm2.aiProfiles.map { $0.name })")
let sm2Back = AppSettings(storeDirectory: URL(fileURLWithPath: dm2))
check("m7-delete-persist", "重启后删除仍然生效（不是只改了内存）",
      sm2Back.aiProfiles.count == 1 && sm2Back.aiProfiles[0].name == "A 家",
      "\(sm2Back.aiProfiles.map { $0.name })")
check("m7-delete-missing", "删一个不存在的名字 → 返回 false + 中文说明，不崩",
      !sm2.deleteProfile(named: "查无此方案") && sm2.aiProfiles.count == 1,
      sm2.aiStatus)

// m8 删光最后一个方案 → profiles 键随之消失（不留空数组）
sm2.deleteProfile(named: "A 家")
check("m8-empty", "删光最后一个 → profiles 键从文件里消失（回到从前的形状）",
      sm2.aiProfiles.isEmpty && json(rawFile(dm2 + "/ai.json"))["profiles"] == nil,
      "\(json(rawFile(dm2 + "/ai.json"))["profiles"] ?? "<无 profiles 键>")")

// m9 「清空」不许删方案（第 62 条语义 + 第 82 条：清空只清输入框）
let dm3 = "/tmp/ai_settings_test/m3"
wipe(dm3)
let sm3 = AppSettings(storeDirectory: URL(fileURLWithPath: dm3))
sm3.aiBase = "https://keep.example.com/v1"; sm3.aiKey = "sk-KEEP"; sm3.aiModel = "keep"
sm3.aiProfileName = "要保住的方案"
_ = sm3.saveAI()
sm3.clearAI()
check("m9-clear-keeps", "「清空」清掉输入框，但**方案一份都不少**、磁盘未动",
      sm3.aiBase.isEmpty && sm3.aiProfiles.count == 1
        && sm3.aiProfiles[0].key == "sk-KEEP"
        && rawFile(dm3 + "/ai.json").contains("sk-KEEP"),
      "profiles=\(sm3.aiProfiles.count) base=「\(sm3.aiBase)」")

// m10 旧 ai.json（没有 profiles 键）→ 空列表、不崩
let dm4 = "/tmp/ai_settings_test/m4"
wipe(dm4)
mkdir(dm4, 0o700)
writeRaw(dm4 + "/ai.json", "{\"base\":\"b\",\"key\":\"\",\"model\":\"m\",\"thinking\":\"off\",\"maxTokens\":\"2048\"}", perm: 0o600)
let sm4 = AppSettings(storeDirectory: URL(fileURLWithPath: dm4))
check("m10-legacy", "旧 ai.json 没有 profiles → 空列表（不是错误），当前配置照常读回",
      sm4.aiProfiles.isEmpty && sm4.aiBase == "b" && sm4.aiModel == "m",
      "profiles=\(sm4.aiProfiles.count) base=\(sm4.aiBase)")

// m11 坏类型：profiles 不是数组 / 项里没有合法名字 → 丢掉坏项，好的照收，绝不崩
let dm5 = "/tmp/ai_settings_test/m5"
wipe(dm5)
mkdir(dm5, 0o700)
writeRaw(dm5 + "/ai.json",
         "{\"base\":\"b\",\"model\":\"m\",\"profiles\":["
         + "{\"name\":\"好的\",\"model\":\"m1\",\"maxTokens\":123,\"webSearch\":\"yes\"},"
         + "{\"base\":\"没有名字\"},{\"name\":\"   \"},{\"name\":123},\"不是字典\"]}",
         perm: 0o600)
let sm5 = AppSettings(storeDirectory: URL(fileURLWithPath: dm5))
check("m11-baditems", "坏项被丢掉、好项照收；坏字段回落默认（maxTokens 用 2048、webSearch=false）",
      sm5.aiProfiles.count == 1 && sm5.aiProfiles[0].name == "好的"
        && sm5.aiProfiles[0].maxTokens == AIMaxTokens.defaultText
        && sm5.aiProfiles[0].webSearch == false,
      "\(sm5.aiProfiles.map { "\($0.name)/\($0.maxTokens)/\($0.webSearch)" })")
let dm6 = "/tmp/ai_settings_test/m6"
wipe(dm6)
mkdir(dm6, 0o700)
writeRaw(dm6 + "/ai.json", "{\"base\":\"b\",\"profiles\":\"不是数组\"}", perm: 0o600)
let sm6p = AppSettings(storeDirectory: URL(fileURLWithPath: dm6))
check("m11-notarray", "profiles 根本不是数组 → 空列表，不崩",
      sm6p.aiProfiles.isEmpty, "profiles=\(sm6p.aiProfiles.count)")

// m12 方案里含密钥 —— 界面只显示前 4 位（脱敏不受方案影响）
check("m12-mask", "方案里的密钥只经 mask() 显示（前 4 位），与顶层那份同一口径",
      AppSettings.mask("sk-M-1111") == AppSettings.mask("sk-M-1111")
        && !AppSettings.mask("sk-M-1111").contains("1111"),
      "mask(\(sm.aiProfiles[0].key))=\(AppSettings.mask(sm.aiProfiles[0].key))")

// ═══════════════════════════════════════════════════════════════════════════
// (n) 第 55 批：两段式「AI 协同初排」—— argv / 暂存目录发现 / 建议摘要 / 预算设置
//     引擎契约由批 54 冻结（Engine/ 零 diff），这里钉住「界面承诺 = 引擎真收到的东西」。
// ═══════════════════════════════════════════════════════════════════════════
section("T-n  第 55 批：两段式协同 argv（ph1/ph2 / 默认零变化 / 预算闸）")

let coopRoot = base.appendingPathComponent("coop").path
wipe(coopRoot); mkdir(coopRoot, 0o700)
let coopSrc = coopRoot + "/main.docx"
let coopOut = coopRoot + "/issue90"
let coopReplies = coopRoot + "/replies"
let coopStage = coopOut + "/Lei_Li_待核"

// n0 默认基线 —— 批 62 入口收敛：普通初排**不再默认带 --verify-refs**（独立任务书勾选已隐藏），
// 默认 argv 回到编译/清理两项；任务书只由 ⑪（useAI）或两段式第一段产生。
let n0Defaults = ConvertFeature.Options()
check("n0-default-value", "批62：不碰任何开关时内部 verifyRefs=false（独立勾选已隐藏；字段保留兼容旧脚本）",
      n0Defaults.verifyRefs == false, "verifyRefs=\(n0Defaults.verifyRefs)")
let n0Default = ConvertFeature.arguments(n0Defaults)
check("n0-default", "普通初排默认 argv = --compile --clean（不再含 --verify-refs；批62 入口收敛）",
      n0Default == ["--compile", "--clean"], "argv=\(n0Default)")
var n0 = ConvertFeature.Options()
n0.source = coopSrc; n0.out = coopOut
n0.verifyRefs = true; n0.useAI = true; n0.llmBase = "https://api.example.com/v1"
n0.llmModel = "m"; n0.aiBudgetTokens = AIBudgetSettings.defaultArticle
n0.aiBatchBudgetTokens = AIBudgetSettings.defaultBatch
let n0Argv = ConvertFeature.arguments(n0)
let n0Forbidden = ["--coop-taskbook-only", "--coop-stage", "--coop-replies", "--coop-apply",
                   "--ai-budget-tokens", "--ai-batch-budget-tokens"]
check("n0-no-coop-flags", "普通转换（勾 AI、预算=引擎默认）：没有任何 coop 旗标，也没有预算参数",
      n0Forbidden.allSatisfy { !n0Argv.contains($0) } && n0Argv.contains("--verify-refs"),
      "argv=\(n0Argv)")

// n1 ph1：现有参数 + --verify-refs --no-ai-merge --coop-taskbook-only；
//    **绝不** --verify-plan / --llm-* / --search-refs（第一段不调用 API）
var n1 = ConvertFeature.Options()
n1.source = coopSrc; n1.out = coopOut; n1.compile = false
n1.verifyRefs = true; n1.verifyPlan = true; n1.useAI = true; n1.keepOriginalBib = true
n1.llmBase = "https://api.example.com/v1"; n1.llmModel = "m"; n1.searchRefs = true
n1 = CoopFeature.prepare(n1, phase: .first)
let n1Argv = ConvertFeature.arguments(n1)
check("n1-ph1-flags", "ph1：三个契约旗标都在，顺序固定（追加在现有参数之后）",
      n1Argv.suffix(3) == ["--verify-refs", "--no-ai-merge", "--coop-taskbook-only"],
      "tail=\(Array(n1Argv.suffix(3)))")
let n1Forbidden = ["--verify-plan", "--llm-base", "--llm-model", "--llm-protocol",
                   "--llm-web-search", "--search-refs", "--ai-budget-tokens"]
check("n1-ph1-no-plan-no-api", "ph1：**不传** --verify-plan，也不传任何 --llm-*/--search-refs/预算参数",
      n1Forbidden.allSatisfy { !n1Argv.contains($0) }, "argv=\(n1Argv)")
check("n1-prepare", "CoopFeature.prepare(.first)：路径标准化 + 强制 useAI/searchRefs=false（两段式不调 API）",
      n1.useAI == false && n1.searchRefs == false && n1.source == CoopFeature.normalizePath(coopSrc),
      "useAI=\(n1.useAI) searchRefs=\(n1.searchRefs) source=\(n1.source)")

// n1b 自定义暂存目录 → --coop-stage
var n1b = ConvertFeature.Options()
n1b.source = coopSrc; n1b.out = coopOut; n1b.coopStage = coopStage
n1b = CoopFeature.prepare(n1b, phase: .first)
let n1bArgv = ConvertFeature.arguments(n1b)
let n1bStage = CoopFeature.normalizePath(coopStage)
check("n1b-ph1-stage", "ph1 + 自定义暂存目录 → 恰好带 `--coop-stage <标准化绝对路径>`",
      n1bArgv.contains("--coop-stage") && n1bArgv.last == n1bStage,
      "tail=\(Array(n1bArgv.suffix(2)))")

// ═══════════════════════════════════════════════════════════════════════════
// (n59) 第 59 批：全文 AI 核查（任务书附正文）—— 默认零变化 / context|full 映射 / 仅 ph1
// ═══════════════════════════════════════════════════════════════════════════
var n59Default = ConvertFeature.Options()
n59Default.source = coopSrc; n59Default.out = coopOut
n59Default = CoopFeature.prepare(n59Default, phase: .first)
let n59DefaultArgv = ConvertFeature.arguments(n59Default)
check("n59-default-off", "批59：默认不勾「全文 AI 核查」→ ph1 argv 仍是批58 的三个契约旗标（不含 --coop-fulltext）",
      !n59DefaultArgv.contains("--coop-fulltext")
        && n59DefaultArgv.suffix(3) == ["--verify-refs", "--no-ai-merge", "--coop-taskbook-only"],
      "tail=\(Array(n59DefaultArgv.suffix(3))) fulltext=\(n59DefaultArgv.contains("--coop-fulltext"))")
check("n59-b58-default-unchanged", "批62 起普通默认 argv = --compile --clean（不再默认带 --verify-refs；批62 入口收敛）",
      ConvertFeature.arguments(ConvertFeature.Options()) == ["--compile", "--clean"],
      "argv=\(ConvertFeature.arguments(ConvertFeature.Options()))")
check("n59-scope-default", "范围默认 = 引用上下文（引擎值 context；上限数字只是文案，壳子不替引擎算）",
      ConvertFeature.Options().coopFulltextScope == .context
        && ConvertFeature.FulltextScope.context.flagValue == "context"
        && ConvertFeature.FulltextScope.full.flagValue == "full",
      "default=\(ConvertFeature.Options().coopFulltextScope.rawValue)")
var n59Ctx = n59Default; n59Ctx.coopFulltext = true; n59Ctx.coopFulltextScope = .context
let n59CtxArgv = ConvertFeature.arguments(n59Ctx)
check("n59-context", "勾选 + 引用上下文 → 恰好追加 `--coop-fulltext context`（在 ph1 旗标之后）",
      n59CtxArgv.suffix(2) == ["--coop-fulltext", "context"], "tail=\(Array(n59CtxArgv.suffix(5)))")
var n59Full = n59Default; n59Full.coopFulltext = true; n59Full.coopFulltextScope = .full
let n59FullArgv = ConvertFeature.arguments(n59Full)
check("n59-full", "勾选 + 全文 → 恰好追加 `--coop-fulltext full`",
      n59FullArgv.suffix(2) == ["--coop-fulltext", "full"], "tail=\(Array(n59FullArgv.suffix(5)))")
var n59Ph2 = ConvertFeature.Options()
n59Ph2.source = coopSrc; n59Ph2.out = coopOut
n59Ph2.coopRepliesPaths = [coopReplies]; n59Ph2.coopStage = coopStage
n59Ph2.coopFulltext = true; n59Ph2.coopFulltextScope = .full
n59Ph2 = CoopFeature.prepare(n59Ph2, phase: .second)
check("n59-ph2-ignores", "第二段不受影响：即使勾着全文核查，ph2 argv 也不含 --coop-fulltext",
      !ConvertFeature.arguments(n59Ph2).contains("--coop-fulltext"),
      "argv=\(ConvertFeature.arguments(n59Ph2))")
var n59Plain = ConvertFeature.Options(); n59Plain.coopFulltext = true
check("n59-plain-ignores", "普通转换（非两段式）不传 --coop-fulltext（引擎只在 --coop-taskbook-only 下识别）",
      !ConvertFeature.arguments(n59Plain).contains("--coop-fulltext"),
      "argv=\(ConvertFeature.arguments(n59Plain))")
let n59TabSrc = readSrcGlob("Views/ConvertTab*.swift")
let n59ConvSrc = readSrc("Features/ConvertFeature.swift")
check("n59-ui-wiring", "源码级接线：ConvertTab 有「全文 AI 核查（任务书附正文）」复选框 + 范围单选，"
      + "ConvertFeature 拼 --coop-fulltext",
      n59TabSrc.contains("全文 AI 核查（任务书附正文）")
        && n59TabSrc.contains("coopFulltextScope")
        && n59ConvSrc.contains("\"--coop-fulltext\""),
      "tab=\(n59TabSrc.contains("全文 AI 核查（任务书附正文）")) "
        + "picker=\(n59TabSrc.contains("coopFulltextScope")) flag=\(n59ConvSrc.contains("\"--coop-fulltext\""))")

// ═══════════════════════════════════════════════════════════════════════════
// (n60) 第 60 批：全文 AI 核查（直连 API）—— 默认不勾 / --llm-fulltext 映射 / 仅 ⑪
// ═══════════════════════════════════════════════════════════════════════════
check("n60-default-off", "批60：默认不勾「全文 AI 核查（直连 API）」→ 默认 argv 不含 --llm-fulltext",
      ConvertFeature.Options().llmFulltext == false
        && !ConvertFeature.arguments(ConvertFeature.Options()).contains("--llm-fulltext"),
      "llmFulltext=\(ConvertFeature.Options().llmFulltext) "
        + "argv=\(ConvertFeature.arguments(ConvertFeature.Options()))")
check("n60-b59-default-unchanged", "批62 起普通默认 argv = --compile --clean（不再默认带 --verify-refs）",
      ConvertFeature.arguments(ConvertFeature.Options()) == ["--compile", "--clean"],
      "argv=\(ConvertFeature.arguments(ConvertFeature.Options()))")
// ⑪ = 勾「用 AI 处理参考文献」+ 已配置端点 + 非两段式（batch59 的 coop 通道另有 --coop-fulltext）
var n60AI = ConvertFeature.Options()
n60AI.useAI = true; n60AI.llmBase = "https://api.example.com/v1"; n60AI.llmModel = "m"
check("n60-ai-off", "⑪ 已配置端点但**不勾**全文核查 → argv 不含 --llm-fulltext",
      !ConvertFeature.arguments(n60AI).contains("--llm-fulltext"),
      "argv=\(ConvertFeature.arguments(n60AI))")
var n60On = n60AI; n60On.llmFulltext = true
let n60OnArgv = ConvertFeature.arguments(n60On)
check("n60-ai-on", "⑪ 勾选全文核查 → argv 恰好含一次 --llm-fulltext（--verify-refs/--llm-* 契约不变）",
      n60OnArgv.filter { $0 == "--llm-fulltext" }.count == 1
        && n60OnArgv.contains("--verify-refs") && n60OnArgv.contains("--llm-base"),
      "argv=\(n60OnArgv)")
var n60NoAI = ConvertFeature.Options(); n60NoAI.llmFulltext = true
check("n60-needs-ai", "没勾「用 AI 处理参考文献」时**不传** --llm-fulltext（只有 ⑪ 通道识别）",
      !ConvertFeature.arguments(n60NoAI).contains("--llm-fulltext"),
      "argv=\(ConvertFeature.arguments(n60NoAI))")
var n60Ph1 = ConvertFeature.Options()
n60Ph1.source = coopSrc; n60Ph1.out = coopOut; n60Ph1.llmFulltext = true
n60Ph1 = CoopFeature.prepare(n60Ph1, phase: .first)
check("n60-coop-ph1-ignores", "两段式第一段不传 --llm-fulltext（通道① 只走 --coop-fulltext）",
      !ConvertFeature.arguments(n60Ph1).contains("--llm-fulltext"),
      "argv=\(ConvertFeature.arguments(n60Ph1))")
var n60Ph2 = ConvertFeature.Options()
n60Ph2.source = coopSrc; n60Ph2.out = coopOut
n60Ph2.coopRepliesPaths = [coopReplies]; n60Ph2.coopStage = coopStage; n60Ph2.llmFulltext = true
n60Ph2 = CoopFeature.prepare(n60Ph2, phase: .second)
check("n60-coop-ph2-ignores", "两段式第二段不传 --llm-fulltext",
      !ConvertFeature.arguments(n60Ph2).contains("--llm-fulltext"),
      "argv=\(ConvertFeature.arguments(n60Ph2))")
let n60TabSrc = readSrcGlob("Views/ConvertTab*.swift")
let n60ConvSrc = readSrc("Features/ConvertFeature.swift")
check("n60-ui-wiring", "源码级接线：ConvertTab 有「全文 AI 核查（直连 API，会产生费用）」复选框（默认不勾），"
      + "ConvertFeature 拼 --llm-fulltext",
      n60TabSrc.contains("全文 AI 核查（直连 API，会产生费用）")
        && n60TabSrc.contains("llmFulltext")
        && n60ConvSrc.contains("\"--llm-fulltext\""),
      "tab=\(n60TabSrc.contains("全文 AI 核查（直连 API，会产生费用）")) "
        + "bind=\(n60TabSrc.contains("llmFulltext")) flag=\(n60ConvSrc.contains("\"--llm-fulltext\""))")
// 设置持久化：会话态（与批59 coopFulltext 同模式），不新增 UserDefaults/ai.json 键 → 老用户设置零覆盖
check("n60-no-hidden-default", "不新增持久化键/隐藏默认：该开关只在 Options 会话态，源码无 llmFulltext 持久化键",
      !n60ConvSrc.contains("llmFulltextKey") && !n60TabSrc.contains("llmFulltextKey"),
      "convKeys=\(n60ConvSrc.contains("llmFulltextKey")) tabKeys=\(n60TabSrc.contains("llmFulltextKey"))")

// n2 「只扫描源稿」不受两段式影响：勾着协同也只走普通 --scan 路径
var n2 = ConvertFeature.Options()
n2.source = coopSrc; n2.scanOnly = true; n2.coopPhase = .first; n2.verifyRefs = true
let n2Argv = ConvertFeature.arguments(n2)
check("n2-scan-ignores-coop", "scanOnly + 两段式：本次按普通扫描走（--scan），**没有**任何 coop 旗标",
      n2Argv.contains("--scan")
        && !n2Argv.contains("--coop-taskbook-only") && !n2Argv.contains("--coop-stage")
        && !n2Argv.contains("--coop-replies"),
      "argv=\(n2Argv)")

// n3 ph2：最小集（source/out/replies/stage/apply/compile/clean），其余普通参数一个都不带。
//    批 56 起：回填是**列表**（每项一个 --coop-replies）；--clean 随页面「清理编译中间文件」。
var n3 = ConvertFeature.Options()
n3.source = coopSrc; n3.out = coopOut
n3.coopRepliesPaths = [coopReplies]; n3.coopStage = coopStage
n3.coopPhase = .second; n3.coopApply = true; n3.compile = false
n3.verifyRefs = true; n3.useAI = true; n3.llmBase = "https://api.example.com/v1"
n3.clean = true; n3.verifyMax = 5; n3.name = "X"; n3.lang = "zh"; n3.sourcePdf = true
let n3Argv = ConvertFeature.arguments(n3)
let n3Want = ["--source", CoopFeature.normalizePath(coopSrc),
              "--out", CoopFeature.normalizePath(coopOut),
              "--coop-replies", CoopFeature.normalizePath(coopReplies),
              "--coop-stage", CoopFeature.normalizePath(coopStage),
              "--coop-apply",
              "--clean"]
check("n3-ph2-minimal", "ph2：argv 恰好是最小集（compile 关 → 无 --compile；无 --verify-refs/--name/--llm-*）",
      n3Argv == n3Want, "argv=\(n3Argv)")
var n3c = n3
n3c.compile = true
let n3cArgv = ConvertFeature.arguments(n3c)
check("n3b-ph2-compile", "ph2 + 页面「生成后立即编译」→ --compile 在 --clean 之前",
      n3cArgv == n3Want.dropLast() + ["--compile", "--clean"],
      "tail=\(Array(n3cArgv.suffix(3)))")

// n4 预算闸：仅「非协同 + 勾 AI + 值 ≠ 引擎默认」时传；0 要传 "0"
var n4 = ConvertFeature.Options()
n4.source = coopSrc; n4.out = coopOut; n4.useAI = true; n4.llmBase = "b"; n4.llmModel = "m"
n4.aiBudgetTokens = 12345; n4.aiBatchBudgetTokens = 500000
let n4Argv = ConvertFeature.arguments(n4)
check("n4-budget-custom", "非协同 + 勾 AI + 自定义预算 → 两个预算参数都在",
      n4Argv.contains("--ai-budget-tokens") && n4Argv.contains("12345")
        && n4Argv.contains("--ai-batch-budget-tokens") && n4Argv.contains("500000"),
      "argv=\(n4Argv)")
var n4z = n4; n4z.aiBudgetTokens = 0; n4z.aiBatchBudgetTokens = 0
let n4zArgv = ConvertFeature.arguments(n4z)
check("n4-budget-zero", "预算填 0（= 不限）→ 原样传 \"0\"（不会被当成「用默认」而丢掉）",
      n4zArgv.contains("--ai-budget-tokens") && n4zArgv.contains("0")
        && n4zArgv.contains("--ai-batch-budget-tokens"),
      "argv=\(n4zArgv)")
var n4noAI = ConvertFeature.Options()
n4noAI.source = coopSrc; n4noAI.out = coopOut; n4noAI.useAI = false
n4noAI.aiBudgetTokens = 12345; n4noAI.aiBatchBudgetTokens = 500000
check("n4-budget-needs-ai", "没勾 AI → 预算参数不传（它只约束直连 API 通道）",
      !ConvertFeature.arguments(n4noAI).contains("--ai-budget-tokens"),
      "argv=\(ConvertFeature.arguments(n4noAI))")
var n4coop = ConvertFeature.Options()
n4coop.source = coopSrc; n4coop.out = coopOut; n4coop.verifyRefs = true
n4coop.aiBudgetTokens = 12345; n4coop.aiBatchBudgetTokens = 500000
n4coop = CoopFeature.prepare(n4coop, phase: .first)
check("n4-budget-no-coop", "两段式（外部 chat 零费用）→ 预算参数一个都不传",
      !ConvertFeature.arguments(n4coop).contains("--ai-budget-tokens")
        && !ConvertFeature.arguments(n4coop).contains("--ai-batch-budget-tokens"),
      "argv=\(ConvertFeature.arguments(n4coop))")

// ═══════════════════════════════════════════════════════════════════════════
// (o) 第 55 批：AI 预算设置（AIBudgetSettings，UserDefaults 逐键；非法不落盘）
// ═══════════════════════════════════════════════════════════════════════════
section("T-o  第 55 批：AI 预算设置（单篇 / 单批）")

check("o-parse", "解析口径：空=不传；0=不限；负数/非整数=拒绝",
      AIBudgetSettings.parse("") == .unset
        && AIBudgetSettings.parse("0") == .value(0)
        && AIBudgetSettings.parse(" 12345 ") == .value(12345)
        && { if case .invalid = AIBudgetSettings.parse("-5") { return true }; return false }()
        && { if case .invalid = AIBudgetSettings.parse("1e5") { return true }; return false }(),
      "parse(\"\")=\(AIBudgetSettings.parse("")) parse(\"0\")=\(AIBudgetSettings.parse("0"))")

let budgetSuite = "cn.edu.sysu.logic.toolbox.tests.aiBudget"
let bDefaults = UserDefaults(suiteName: budgetSuite)!
bDefaults.removePersistentDomain(forName: budgetSuite)
let b0 = AIBudgetSettings(defaults: bDefaults)
check("o-defaults", "默认 30000 / 1000000（= 引擎默认）；不传参数 → 命令行与批54 相同",
      b0.articleText == "30000" && b0.batchText == "1000000"
        && b0.articleValue == AIBudgetSettings.defaultArticle
        && b0.batchValue == AIBudgetSettings.defaultBatch
        && b0.articleHint == nil && b0.batchHint == nil,
      "article=\(b0.articleText) batch=\(b0.batchText)")

b0.updateArticle("abc")
check("o-invalid", "非法输入：当场红字提示、value=nil，且**不落盘**",
      b0.articleHint != nil && b0.articleValue == nil
        && bDefaults.object(forKey: AIBudgetSettings.articleKey) == nil,
      "hint=\(b0.articleHint ?? "<nil>") stored=\(bDefaults.object(forKey: AIBudgetSettings.articleKey) ?? "<nil>")")
b0.updateBatch("-1")
check("o-negative", "负数同样拒绝（0 = 不限，负数不是）",
      b0.batchHint != nil && b0.batchValue == nil
        && bDefaults.object(forKey: AIBudgetSettings.batchKey) == nil,
      "hint=\(b0.batchHint ?? "<nil>")")

b0.updateArticle("0"); b0.updateBatch("50000")
let bBack = AIBudgetSettings(defaults: bDefaults)
check("o-persist", "合法值立刻逐键落盘（0 也存；0 显示为「不限」）",
      bDefaults.integer(forKey: AIBudgetSettings.articleKey) == 0
        && bBack.articleText == "0" && bBack.articleValue == 0
        && bBack.articleSummary.contains("不限")
        && bBack.batchText == "50000",
      "article=\(bBack.articleText) batch=\(bBack.batchText)")
b0.updateArticle("")
let bCleared = AIBudgetSettings(defaults: bDefaults)
check("o-clear", "留空 = 恢复「不传参数」（删键，不存空串占位）",
      bDefaults.object(forKey: AIBudgetSettings.articleKey) == nil
        && bCleared.articleText == "30000" && bCleared.articleValue == AIBudgetSettings.defaultArticle,
      "article=\(bCleared.articleText)")
bDefaults.removePersistentDomain(forName: budgetSuite)

// ═══════════════════════════════════════════════════════════════════════════
// (q63) 第 63 批：全文核查上限 / 自动应用 / B1 整行可点 / B2 .doc 元数据
// ═══════════════════════════════════════════════════════════════════════════
section("T-q63  第 63 批：API 核查上限 / 自动应用 / B1 整行可点 / B2 .doc 元数据")

// ── argv：--llm-fulltext-max-chars（引擎默认 40000；0 显式传；只属于 --llm-fulltext）
var q63AI = ConvertFeature.Options()
q63AI.useAI = true; q63AI.llmBase = "https://api.example.com/v1"; q63AI.llmModel = "m"
q63AI.llmFulltext = true
check("q63-maxchars-default", "批63：上限留空/默认 40000 → 不传 --llm-fulltext-max-chars（默认 argv 不变）",
      q63AI.llmFulltextMaxChars == nil
        && AIBudgetSettings.defaultFulltextMaxChars == 40000
        && !ConvertFeature.arguments(q63AI).contains("--llm-fulltext-max-chars"),
      "default=\(AIBudgetSettings.defaultFulltextMaxChars) argv=\(ConvertFeature.arguments(q63AI))")
var q63Custom = q63AI; q63Custom.llmFulltextMaxChars = 50000
let q63CustomArgv = ConvertFeature.arguments(q63Custom)
check("q63-maxchars-custom", "自定义上限 50000 → 恰好一组 --llm-fulltext-max-chars 50000",
      q63CustomArgv.filter { $0 == "--llm-fulltext-max-chars" }.count == 1
        && q63CustomArgv.contains("50000"),
      "argv=\(q63CustomArgv)")
var q63Zero = q63AI; q63Zero.llmFulltextMaxChars = 0
let q63ZeroArgv = ConvertFeature.arguments(q63Zero)
let q63ZeroIdx = q63ZeroArgv.firstIndex(of: "--llm-fulltext-max-chars")
check("q63-maxchars-zero", "上限填 0（= 不限）→ 显式传 \"0\"（不能被当成「用默认」丢掉）",
      q63ZeroIdx != nil && q63ZeroArgv[q63ZeroIdx! + 1] == "0",
      "argv=\(q63ZeroArgv)")
var q63DefaultValue = q63AI; q63DefaultValue.llmFulltextMaxChars = 40000
check("q63-maxchars-default-value", "显式填 40000（= 引擎默认）→ 也不传（与留空等价）",
      !ConvertFeature.arguments(q63DefaultValue).contains("--llm-fulltext-max-chars"),
      "argv=\(ConvertFeature.arguments(q63DefaultValue))")
var q63NoFulltext = ConvertFeature.Options()
q63NoFulltext.useAI = true; q63NoFulltext.llmBase = "https://api.example.com/v1"
q63NoFulltext.llmModel = "m"; q63NoFulltext.llmFulltextMaxChars = 50000
check("q63-maxchars-needs-fulltext", "没勾全文核查 → 上限参数不传（只属于 --llm-fulltext）",
      !ConvertFeature.arguments(q63NoFulltext).contains("--llm-fulltext-max-chars"),
      "argv=\(ConvertFeature.arguments(q63NoFulltext))")

// ── argv：--no-llm-fulltext-apply（自动应用默认开 → 不传；关掉才传）
check("q63-apply-default-on", "批63：自动应用默认开（llmFulltextApply=true）→ 不传 --no-llm-fulltext-apply",
      q63AI.llmFulltextApply
        && !ConvertFeature.arguments(q63AI).contains("--no-llm-fulltext-apply"),
      "apply=\(q63AI.llmFulltextApply) argv=\(ConvertFeature.arguments(q63AI))")
var q63ApplyOff = q63AI; q63ApplyOff.llmFulltextApply = false
let q63ApplyOffArgv = ConvertFeature.arguments(q63ApplyOff)
check("q63-apply-off", "关掉自动应用 → 恰好一个 --no-llm-fulltext-apply",
      q63ApplyOffArgv.filter { $0 == "--no-llm-fulltext-apply" }.count == 1,
      "argv=\(q63ApplyOffArgv)")
var q63ApplyNoFulltext = q63NoFulltext; q63ApplyNoFulltext.llmFulltextApply = false
check("q63-apply-needs-fulltext", "没勾全文核查 → 也不传 --no-llm-fulltext-apply",
      !ConvertFeature.arguments(q63ApplyNoFulltext).contains("--no-llm-fulltext-apply"),
      "argv=\(ConvertFeature.arguments(q63ApplyNoFulltext))")
var q63Coop = ConvertFeature.Options()
q63Coop.source = coopSrc; q63Coop.out = coopOut; q63Coop.llmFulltext = true
q63Coop.llmFulltextMaxChars = 50000; q63Coop.llmFulltextApply = false
q63Coop = CoopFeature.prepare(q63Coop, phase: .first)
let q63CoopArgv = ConvertFeature.arguments(q63Coop)
check("q63-coop-ignores-new-flags", "两段式第一段：上限/自动应用旗标都不传（只走 --coop-fulltext）",
      !q63CoopArgv.contains("--llm-fulltext-max-chars")
        && !q63CoopArgv.contains("--no-llm-fulltext-apply"),
      "argv=\(q63CoopArgv)")

// ── 设置持久化：UserDefaults（key llmFulltextMaxChars / llmFulltextAutoApply）
let q63Suite = "cn.edu.sysu.logic.toolbox.tests.aiFulltext"
let q63Defaults = UserDefaults(suiteName: q63Suite)!
q63Defaults.removePersistentDomain(forName: q63Suite)
let q63b0 = AIBudgetSettings(defaults: q63Defaults)
check("q63-settings-defaults", "批63 设置默认：上限 40000、自动应用开；缺键不落盘（老用户零覆盖）",
      q63b0.fulltextMaxCharsText == "40000"
        && q63b0.fulltextMaxCharsValue == AIBudgetSettings.defaultFulltextMaxChars
        && q63b0.fulltextAutoApply
        && q63b0.fulltextMaxCharsHint == nil
        && q63Defaults.object(forKey: AIBudgetSettings.fulltextMaxCharsKey) == nil
        && q63Defaults.object(forKey: AIBudgetSettings.fulltextAutoApplyKey) == nil,
      "maxChars=\(q63b0.fulltextMaxCharsText) apply=\(q63b0.fulltextAutoApply)")
q63b0.updateFulltextMaxChars("abc")
check("q63-settings-invalid", "非法上限：红字提示、value=nil、**不落盘**",
      q63b0.fulltextMaxCharsHint != nil && q63b0.fulltextMaxCharsValue == nil
        && q63Defaults.object(forKey: AIBudgetSettings.fulltextMaxCharsKey) == nil,
      "hint=\(q63b0.fulltextMaxCharsHint ?? "<nil>")")
q63b0.updateFulltextMaxChars("-1")
check("q63-settings-negative", "负数上限拒绝（0 = 不限，负数不是）",
      q63b0.fulltextMaxCharsHint != nil && q63b0.fulltextMaxCharsValue == nil,
      "hint=\(q63b0.fulltextMaxCharsHint ?? "<nil>")")
q63b0.updateFulltextMaxChars("0"); q63b0.updateFulltextAutoApply(false)
let q63bBack = AIBudgetSettings(defaults: q63Defaults)
check("q63-settings-persist", "0 也落盘（摘要显示「不限」）；自动应用关掉后读回 false",
      q63Defaults.integer(forKey: AIBudgetSettings.fulltextMaxCharsKey) == 0
        && q63bBack.fulltextMaxCharsValue == 0
        && q63bBack.fulltextMaxCharsSummary.contains("不限")
        && q63bBack.fulltextAutoApply == false
        && q63Defaults.bool(forKey: AIBudgetSettings.fulltextAutoApplyKey) == false,
      "maxChars=\(q63bBack.fulltextMaxCharsText) apply=\(q63bBack.fulltextAutoApply)")
q63b0.updateFulltextMaxChars("")
q63b0.updateFulltextAutoApply(true)
let q63bCleared = AIBudgetSettings(defaults: q63Defaults)
check("q63-settings-clear", "留空 = 删键回默认（不存空串）；自动应用可恢复 true",
      q63Defaults.object(forKey: AIBudgetSettings.fulltextMaxCharsKey) == nil
        && q63bCleared.fulltextMaxCharsText == "40000"
        && q63bCleared.fulltextAutoApply == true
        && q63Defaults.bool(forKey: AIBudgetSettings.fulltextAutoApplyKey) == true,
      "maxChars=\(q63bCleared.fulltextMaxCharsText) apply=\(q63bCleared.fulltextAutoApply)")
q63Defaults.removePersistentDomain(forName: q63Suite)

// ── 源码级接线（不依赖窗口手点；屏幕锁定时作为等价证据）
let q63TabSrc = readSrcGlob("Views/ConvertTab*.swift")
let q63ConvSrc = readSrc("Features/ConvertFeature.swift")
let q63SettingsSrc = readSrc("Views/SettingsTab.swift")
let q63VmSrc = readSrc("Core/ViewModels.swift")
check("q63-b1-hit-area", "B1：高级选项换成整行 Button(.plain)（chevron 旋转 + contentShape + withAnimation）",
      q63TabSrc.contains("model.showAdvanced.toggle()")
        && q63TabSrc.contains(".contentShape(Rectangle())")
        && q63TabSrc.contains("withAnimation(")
        && !q63TabSrc.contains("DisclosureGroup(\"高级选项\""),
      "toggle=\(q63TabSrc.contains("model.showAdvanced.toggle()")) "
        + "shape=\(q63TabSrc.contains(".contentShape(Rectangle())"))")
check("q63-copy-no-limit", "批63 文案：外部 chat 不再写「约 4/8 万字上限」，改为不设上限",
      !q63ConvSrc.contains("约 4 万字上限") && !q63ConvSrc.contains("约 8 万字上限")
        && q63ConvSrc.contains("不设上限"),
      "noLimit=\(q63ConvSrc.contains("不设上限"))")
check("q63-copy-auto-apply", "批63 文案/日志：直连通道写明自动应用（安全闸），不再说 report-only 不改产物",
      q63ConvSrc.contains("--no-llm-fulltext-apply")
        && q63ConvSrc.contains("自动应用")
        && q63TabSrc.contains("自动应用")
        && !q63ConvSrc.contains("只生成《…_AI全文复核.md》核查报告"),
      "flag=\(q63ConvSrc.contains("--no-llm-fulltext-apply"))")
check("q63-settings-ui", "设置页新增 aiFulltextSection：API 核查字符上限 + 自动应用 Toggle（默认开）",
      q63SettingsSrc.contains("aiFulltextSection")
        && q63SettingsSrc.contains("核查上限")
        && q63SettingsSrc.contains("全文核查结果自动应用")
        && q63SettingsSrc.contains("fulltextAutoApply")
        && q63VmSrc.contains("llmFulltextAutoApply")
        && q63VmSrc.contains("llmFulltextMaxChars"),
      "section=\(q63SettingsSrc.contains("aiFulltextSection"))")

// ═══════════════════════════════════════════════════════════════════════════
// (q63b) 批63b：初排页人工元数据表单（多作者/多单位）→ 临时 JSON → --metadata-json
//        + `.doc/.docx` 自动识别撤下（Word 源 .docx「第二源」既有能力保留）
// ═══════════════════════════════════════════════════════════════════════════
section("T-q63b  批63b：人工元数据表单 / JSON / argv / 元数据文件三通道 / 临时文件")

// ── 表单模型：默认空、作者/单位上限、首作者锁删
var q63bF = MetadataForm()
check("q63b-default-empty",
      "空白表单：hasAnyValue=false、authors 恒 1 位（第一作者槽位）、jsonString=nil（不建文件不传参）",
      !q63bF.hasAnyValue && q63bF.authors.count == 1 && q63bF.jsonString() == nil
        && q63bF.authorCountForWrite == 0 && q63bF.filledFieldCount == 0
        && MetadataForm.maxAuthors == 5 && MetadataForm.maxAffiliationsPerLanguage == 3,
      "hasAny=\(q63bF.hasAnyValue) authors=\(q63bF.authors.count) "
        + "json=\(String(describing: q63bF.jsonString()))")

var q63bCap = MetadataForm()
var q63bCapAdded = 0
for _ in 0..<10 { if q63bCap.addAuthor() { q63bCapAdded += 1 } }
check("q63b-author-cap",
      "作者上限 5（模板槽位）：addAuthor 到 5 停，第 6 次返回 false 且数量不变",
      q63bCapAdded == MetadataForm.maxAuthors - 1
        && q63bCap.authors.count == MetadataForm.maxAuthors,
      "added=\(q63bCapAdded) count=\(q63bCap.authors.count)")

var q63bLock = MetadataForm()
let q63bLockID = q63bLock.authors[0].id
let q63bRmFirst = q63bLock.removeAuthor(at: 0)
check("q63b-author-first-locked",
      "第一位（第一作者）不可删：removeAuthor(0)=false 且身份不变（结构上保证「至少 1 位作者」）",
      q63bRmFirst == false && q63bLock.authors.count == 1
        && q63bLock.authors[0].id == q63bLockID,
      "removed=\(q63bRmFirst) count=\(q63bLock.authors.count)")

var q63bDel = MetadataForm()
_ = q63bDel.addAuthor(); _ = q63bDel.addAuthor()
q63bDel.authors[0].nameCN = "甲"; q63bDel.authors[1].nameCN = "乙"; q63bDel.authors[2].nameCN = "丙"
let q63bRmMiddle = q63bDel.removeAuthor(at: 1)
check("q63b-author-remove",
      "删除追加作者：removeAuthor(1) 生效，后面的作者保持顺序/身份（不塌索引）",
      q63bRmMiddle && q63bDel.authors.count == 2 && q63bDel.authors[1].nameCN == "丙",
      "ok=\(q63bRmMiddle) names=\(q63bDel.authors.map(\.nameCN))")

// ── 单位动态行：每种语言上限 3、可删、CN/EN 互不影响、纯空白视为空
var q63bAff = MetadataForm()
let q63bAffAuthor = q63bAff.authors[0].id
var q63bAffAdded = 0
for _ in 0..<10 { if q63bAff.addAffiliation(authorID: q63bAffAuthor, language: .cn) { q63bAffAdded += 1 } }
check("q63b-aff-cap",
      "单位行上限 3/语言：第 4 次 addAffiliation 被拒（防 PDF 溢版；引擎侧数组不设上限）",
      q63bAffAdded == MetadataForm.maxAffiliationsPerLanguage
        && q63bAff.authors[0].affiliationsCN.count == MetadataForm.maxAffiliationsPerLanguage,
      "added=\(q63bAffAdded)")
let q63bAffID = q63bAff.authors[0].affiliationsCN[1].id
let q63bAffRemoved = q63bAff.removeAffiliation(authorID: q63bAffAuthor, language: .cn,
                                              affiliationID: q63bAffID)
check("q63b-aff-edit",
      "单位动态行可删除；CN / EN 两个语言列表互不影响",
      q63bAffRemoved && q63bAff.authors[0].affiliationsCN.count == 2
        && q63bAff.authors[0].affiliationsEN.isEmpty,
      "cn=\(q63bAff.authors[0].affiliationsCN.count) en=\(q63bAff.authors[0].affiliationsEN.count)")

var q63bBlank = MetadataForm()
q63bBlank.authors[0].nameCN = "张三"
_ = q63bBlank.addAffiliation(authorID: q63bBlank.authors[0].id, language: .cn)
_ = q63bBlank.addAffiliation(authorID: q63bBlank.authors[0].id, language: .en)
q63bBlank.authors[0].affiliationsCN[0].text = "   "
q63bBlank.authors[0].affiliationsEN[0].text = "\n"
let q63bBlankJSON = q63bBlank.jsonString() ?? ""
check("q63b-blank-trim",
      "纯空白单位/姓名视为空：不出现在 JSON（空值=不覆盖），也不会让作者变「非空」",
      !q63bBlankJSON.contains("affiliations_cn") && !q63bBlankJSON.contains("affiliations_en")
        && q63bBlankJSON.contains("\"name_cn\":\"张三\"")
        && (q63bBlank.jsonObject()?["authors"] as? [[String: Any]])?.count == 1,
      "json=\(q63bBlankJSON)")

// ── 空档/尾部空作者：压缩（引擎对「中间空作者」明确 exit 2，GUI 不产生空档）
var q63bGap = MetadataForm()
_ = q63bGap.addAuthor(); _ = q63bGap.addAuthor()
q63bGap.authors[0].nameCN = "甲"
q63bGap.authors[2].nameCN = "丙"
let q63bGapAuthors = (q63bGap.jsonObject()?["authors"] as? [[String: Any]]) ?? []
let q63bGapWarn = q63bGap.warnings()
check("q63b-gap-compressed",
      "空档作者压缩：中间全空作者被忽略，作者顺序 甲→丙 保持，并给 warning（引擎直传会 exit 2）",
      q63bGapAuthors.count == 2
        && (q63bGapAuthors.first?["name_cn"] as? String) == "甲"
        && (q63bGapAuthors.last?["name_cn"] as? String) == "丙"
        && q63bGapWarn.contains { $0.contains("第 2 位作者为空") },
      "authors=\(q63bGapAuthors.count) warnings=\(q63bGapWarn)")

var q63bTail = MetadataForm()
_ = q63bTail.addAuthor()
q63bTail.authors[0].nameCN = "甲"
check("q63b-tail-empty",
      "尾部空作者丢弃：JSON 只有 1 位作者，summaryLine 报「1 位作者」",
      (q63bTail.jsonObject()?["authors"] as? [[String: Any]])?.count == 1
        && q63bTail.authorCountForWrite == 1,
      "N=\(q63bTail.authorCountForWrite)")

// ── JSON schema（engine-core 定稿：schema_version + 平铺字段 + authors[]）
var q63bFull = MetadataForm()
q63bFull.titleCN = " 中文题名 "
q63bFull.titleEN = "English Title"
q63bFull.abstractCN = "摘要第一行\n摘要第二行"
q63bFull.abstractEN = "Abstract body"
q63bFull.keywordsCN = "逻辑；语义"
q63bFull.keywordsEN = "logic; semantics"
q63bFull.authors[0].nameCN = "张三"
q63bFull.authors[0].nameEN = "Zhang San"
q63bFull.authors[0].email = "zhang@example.com"
_ = q63bFull.addAffiliation(authorID: q63bFull.authors[0].id, language: .cn)
_ = q63bFull.addAffiliation(authorID: q63bFull.authors[0].id, language: .cn)
_ = q63bFull.addAffiliation(authorID: q63bFull.authors[0].id, language: .en)
q63bFull.authors[0].affiliationsCN[0].text = "中山大学逻辑与认知研究所"
q63bFull.authors[0].affiliationsCN[1].text = "中山大学哲学系"
q63bFull.authors[0].affiliationsEN[0].text = "Institute of Logic and Cognition, SYSU"
let q63bFullText = q63bFull.jsonString() ?? ""
let q63bFullObj = q63bFull.jsonObject() ?? [:]
let q63bFullAuthors = (q63bFullObj["authors"] as? [[String: Any]]) ?? []
let q63bFullA0 = q63bFullAuthors.first ?? [:]
check("q63b-json-schema",
      "JSON schema（engine-core 定稿）：schema_version=1 + 中英题名/摘要/关键词（已 trim）+ "
        + "authors[{name_cn,name_en,affiliations_cn[],affiliations_en[],email}]",
      (q63bFullObj["schema_version"] as? Int) == 1
        && (q63bFullObj["title_cn"] as? String) == "中文题名"
        && (q63bFullObj["title_en"] as? String) == "English Title"
        && (q63bFullObj["abstract_cn"] as? String) == "摘要第一行\n摘要第二行"
        && (q63bFullObj["abstract_en"] as? String) == "Abstract body"
        && (q63bFullObj["keywords_cn"] as? String) == "逻辑；语义"
        && (q63bFullObj["keywords_en"] as? String) == "logic; semantics"
        && q63bFullAuthors.count == 1
        && (q63bFullA0["name_cn"] as? String) == "张三"
        && (q63bFullA0["name_en"] as? String) == "Zhang San"
        && (q63bFullA0["email"] as? String) == "zhang@example.com"
        && (q63bFullA0["affiliations_cn"] as? [String])
            == ["中山大学逻辑与认知研究所", "中山大学哲学系"]
        && (q63bFullA0["affiliations_en"] as? [String])
            == ["Institute of Logic and Cognition, SYSU"],
      "json=\(q63bFullText)")
check("q63b-json-deterministic",
      "同一表单两次 jsonString() 逐字节相同（.sortedKeys；断言/排障可复现）",
      q63bFull.jsonString() == q63bFullText
        && q63bFullText.contains("\"schema_version\":1"),
      "len=\(q63bFullText.count)")

var q63bMin = MetadataForm()
q63bMin.titleCN = "只有题名"
let q63bMinObj = q63bMin.jsonObject() ?? [:]
check("q63b-json-omit-empty",
      "空值=不覆盖：没填的键一律不出现（不写空串/空数组；authors 全空时连键都不写）",
      q63bMinObj["authors"] == nil
        && q63bMinObj["title_en"] == nil && q63bMinObj["abstract_cn"] == nil
        && q63bMinObj["keywords_en"] == nil
        && (q63bMinObj["schema_version"] as? Int) == 1,
      "keys=\(q63bMinObj.keys.sorted())")

var q63bEsc = MetadataForm()
q63bEsc.titleCN = "引号「测试」与\"双引号\"及\\反斜杠"
q63bEsc.abstractCN = "第一行\n第二行\t制表"
let q63bEscText = q63bEsc.jsonString() ?? ""
let q63bEscParsed = json(q63bEscText)
check("q63b-json-escape",
      "中文/引号/换行/制表符走 JSONSerialization 编码后可被标准 JSON 解析（不手拼字符串）",
      (q63bEscParsed["title_cn"] as? String) == q63bEsc.titleCN
        && (q63bEscParsed["abstract_cn"] as? String) == q63bEsc.abstractCN,
      "parsed=\(q63bEscParsed.keys.sorted())")

// ── 临时 JSON：命名 / 原子写盘 / 清理（含所有结束路径的清理由 ConvertTab.run 驱动）
let q63bDir = "/tmp/q63b_swift_test"
wipe(q63bDir); mkdir(q63bDir, 0o700)
let q63bPath = MetadataForm.tempFilePath(directory: q63bDir, stem: "/x/张三 论文.docx",
                                         timestamp: "20261020-183000", pid: 4242)
check("q63b-temp-path",
      "临时 JSON 命名：slcn_metadata_<源稿名>_<时间戳>_<pid>.json（中文保留、扩展名去掉）",
      q63bPath == q63bDir + "/slcn_metadata_张三 论文_20261020-183000_4242.json",
      "path=\(q63bPath)")
let q63bPath2 = MetadataForm.tempFilePath(directory: q63bDir, stem: "目录/张三:论文.docx",
                                          timestamp: "20261020-183001", pid: 4242)
check("q63b-temp-path-sanitize",
      "临时文件名去掉路径分隔符/冒号；两次（不同时间戳）不撞名",
      q63bPath2 == q63bDir + "/slcn_metadata_张三_论文_20261020-183001_4242.json"
        && q63bPath2 != q63bPath,
      "path2=\(q63bPath2)")

var q63bWrite = MetadataForm()
q63bWrite.titleCN = "临时文件测试"
var q63bWrote = false
do { try q63bWrite.writeJSON(to: q63bPath); q63bWrote = true } catch { q63bWrote = false }
let q63bJSONBack = json(rawFile(q63bPath))
check("q63b-temp-write",
      "临时 JSON 原子写入后磁盘上可解析（UTF-8），内容与 jsonObject 一致",
      q63bWrote && exists(q63bPath) && (q63bJSONBack["title_cn"] as? String) == "临时文件测试",
      "wrote=\(q63bWrote) exists=\(exists(q63bPath))")

var q63bEmptyThrow = false
do { try MetadataForm().writeJSON(to: q63bDir + "/should_not_exist.json") }
catch { q63bEmptyThrow = true }
check("q63b-temp-empty",
      "空表单不写文件：writeJSON 抛 nothingToWrite（不会生成空 JSON / 空参数）",
      q63bEmptyThrow && !exists(q63bDir + "/should_not_exist.json"),
      "threw=\(q63bEmptyThrow)")

MetadataForm.removeTemporaryFile(q63bPath)
MetadataForm.removeTemporaryFile(q63bPath)   // 幂等
MetadataForm.removeTemporaryFile("")         // 空路径不崩
check("q63b-temp-cleanup",
      "临时 JSON 清理：删除后不存在；重复清理 / 空路径都不崩（成功/失败/取消都走同一清理）",
      !exists(q63bPath),
      "exists=\(exists(q63bPath))")
wipe(q63bDir)

// ── 校验：只提示不阻断
var q63bEmail = MetadataForm()
q63bEmail.authors[0].nameCN = "张三"
q63bEmail.authors[0].email = "bad@@example"
let q63bEmailWarn = q63bEmail.warnings()
check("q63b-validate-email",
      "邮箱格式可疑：只 warning 不阻断（hasAnyValue 仍 true、JSON 里的值照写）",
      q63bEmailWarn.contains { $0.contains("邮箱格式可疑") } && q63bEmail.hasAnyValue
        && ((q63bEmail.jsonObject()?["authors"] as? [[String: Any]])?.first?["email"] as? String)
            == "bad@@example",
      "warnings=\(q63bEmailWarn)")

var q63bEmailOK = MetadataForm()
q63bEmailOK.authors[0].nameCN = "张三"
q63bEmailOK.authors[0].email = "a@example.com"
check("q63b-validate-email-ok",
      "合法邮箱 / 空邮箱都不产生邮箱警告（姓名齐全时 warnings 为空）",
      q63bEmailOK.warnings().isEmpty,
      "warnings=\(q63bEmailOK.warnings())")

var q63bNoName = MetadataForm()
_ = q63bNoName.addAffiliation(authorID: q63bNoName.authors[0].id, language: .cn)
q63bNoName.authors[0].affiliationsCN[0].text = "某大学"
check("q63b-validate-noname",
      "作者只有单位没有姓名：warning 提示补姓名（不阻断；引擎写占位）",
      q63bNoName.warnings().contains { $0.contains("没有姓名") },
      "warnings=\(q63bNoName.warnings())")

var q63bNoAuthor = MetadataForm()
q63bNoAuthor.titleCN = "只有标题"
check("q63b-validate-noauthor",
      "只填标题/摘要、不填作者：warning 说明作者仍由引擎解析；JSON 不写 authors 键",
      q63bNoAuthor.warnings().contains { $0.contains("未填写作者") }
        && q63bNoAuthor.jsonObject()?["authors"] == nil,
      "warnings=\(q63bNoAuthor.warnings())")

// ── 统计行（运行日志精确数字）：标题(1) + 作者1(姓名2+邮箱1+单位CN2+单位EN1=6) + 作者2(姓名CN 1) = 8
var q63bSum = MetadataForm()
q63bSum.titleCN = "题名"
q63bSum.authors[0].nameCN = "张三"
q63bSum.authors[0].nameEN = "Zhang San"
q63bSum.authors[0].email = "z@e.cn"
_ = q63bSum.addAffiliation(authorID: q63bSum.authors[0].id, language: .cn)
_ = q63bSum.addAffiliation(authorID: q63bSum.authors[0].id, language: .cn)
_ = q63bSum.addAffiliation(authorID: q63bSum.authors[0].id, language: .en)
q63bSum.authors[0].affiliationsCN[0].text = "单位一"
q63bSum.authors[0].affiliationsCN[1].text = "单位二"
q63bSum.authors[0].affiliationsEN[0].text = "Aff 1"
_ = q63bSum.addAuthor()
q63bSum.authors[1].nameCN = "李四"
check("q63b-summary",
      "运行日志「将写入 N 位作者 / M 项字段」：N=2、M=8（纯函数，精确数字）",
      q63bSum.summaryLine() == "将写入 2 位作者 / 8 项字段",
      "line=\(q63bSum.summaryLine())")

// ── 内联 7 覆盖字段：表单填了的字段才覆盖（GUI 逐字段清零的依据）
check("q63b-inline-priority-model",
      "表单优先判据：题目/第一作者姓名/单位/邮箱填了 → 对应内联字段清零；没填的（英文题名/摘要）不动",
      q63bSum.coversInlineOverride(.titleCN)
        && q63bSum.coversInlineOverride(.authorCN)
        && q63bSum.coversInlineOverride(.affiliationCN)
        && q63bSum.coversInlineOverride(.email)
        && !q63bSum.coversInlineOverride(.titleEN)
        && !q63bSum.coversInlineOverride(.abstractCN),
      "covers=\(MetadataForm.InlineOverrideField.allCases.map { "\($0.rawValue)=\(q63bSum.coversInlineOverride($0))" })")

// ── argv 映射：无表单逐字节不变；有表单紧跟 --metadata；协同/扫描不传
let q63bBase = ConvertFeature.Options()
check("q63b-argv-default-identical",
      "不填表单的 argv 与批63 逐字节相同：默认 = --compile --clean（没有 --metadata-json）",
      ConvertFeature.arguments(q63bBase) == ["--compile", "--clean"],
      "argv=\(ConvertFeature.arguments(q63bBase))")

var q63bArgvOpts = ConvertFeature.Options()
q63bArgvOpts.source = "/tmp/a.docx"
q63bArgvOpts.out = "/tmp/out"
q63bArgvOpts.metadata = "/tmp/meta.tex"
q63bArgvOpts.metadataJSON = "/tmp/meta.json"
let q63bArgs = ConvertFeature.arguments(q63bArgvOpts)
let q63bMetaIdx = q63bArgs.firstIndex(of: "--metadata") ?? -1
check("q63b-argv-json",
      "--metadata-json 紧跟在 --metadata 之后（引擎优先级 JSON > 旧 7 参数 > --metadata）；"
        + "空路径时 add() 自动跳过",
      q63bMetaIdx >= 0 && q63bMetaIdx + 3 < q63bArgs.count
        && q63bArgs[q63bMetaIdx + 1] == "/tmp/meta.tex"
        && q63bArgs[q63bMetaIdx + 2] == "--metadata-json"
        && q63bArgs[q63bMetaIdx + 3] == "/tmp/meta.json",
      "argv=\(q63bArgs)")

var q63bScanOpts = q63bArgvOpts; q63bScanOpts.scanOnly = true
var q63bPh1Opts = q63bArgvOpts; q63bPh1Opts.coopPhase = .first
var q63bPh2Opts = q63bArgvOpts; q63bPh2Opts.coopPhase = .second
check("q63b-argv-not-coop-scan",
      "两段式（ph1/ph2）与「只扫描源稿」都不传 --metadata-json（引擎对 JSON+协同 exit 2；扫描不产出交付物）",
      !ConvertFeature.arguments(q63bScanOpts).contains("--metadata-json")
        && !ConvertFeature.arguments(q63bPh1Opts).contains("--metadata-json")
        && !ConvertFeature.arguments(q63bPh2Opts).contains("--metadata-json"),
      "scan=\(ConvertFeature.arguments(q63bScanOpts)) ph2=\(ConvertFeature.arguments(q63bPh2Opts))")
check("q63b-attach-rule",
      "下传规则纯函数：普通转换 true；只扫描 / 两段式 false（GUI 与 argv 用同一判据）",
      MetadataForm.shouldAttach(scanOnly: false, coop: false)
        && !MetadataForm.shouldAttach(scanOnly: true, coop: false)
        && !MetadataForm.shouldAttach(scanOnly: false, coop: true),
      "ok")

// ── 元数据文件三通道（Lead 修正：不能一刀切砍掉 Word 源 .docx「英文元数据第二源」）
var q63bLatexDocxWhy = ""
if case .ignored(let why) = ConvertFeature.metadataPathVerdict(metadata: "/x/英摘.docx",
                                                               source: "/x/稿.tex") {
    q63bLatexDocxWhy = why
}
check("q63b-meta-latex-docx",
      "LaTeX 源 + .docx/.doc 元数据：拦下并明确「已取消 .doc/.docx 自动元数据识别」，引导人工表单/ .tex",
      q63bLatexDocxWhy.contains("已取消 .doc/.docx 自动元数据识别")
        && q63bLatexDocxWhy.contains("填写元数据")
        && q63bLatexDocxWhy.contains("未使用该文件"),
      "why=\(q63bLatexDocxWhy)")
check("q63b-meta-latex-tex",
      "LaTeX 源 + .tex 元数据：照旧透传（高级兜底）",
      ConvertFeature.metadataPathVerdict(metadata: "/x/m.tex", source: "/x/稿.tex") == .accepted,
      "ok")
check("q63b-meta-word-docx",
      "Word 源 + .docx / .tex：照旧透传（.docx = 第 76 条「英文元数据第二源」，行为不变）",
      ConvertFeature.metadataPathVerdict(metadata: "/x/英摘.docx", source: "/x/稿.docx") == .accepted
        && ConvertFeature.metadataPathVerdict(metadata: "/x/m.tex", source: "/x/稿.DOCX") == .accepted,
      "ok")
var q63bWordDocWhy = ""
if case .ignored(let why) = ConvertFeature.metadataPathVerdict(metadata: "/x/元数据.doc",
                                                               source: "/x/稿.docx") {
    q63bWordDocWhy = why
}
check("q63b-meta-word-doc",
      "Word 源 + .doc：拦下并提示先另存为 .docx（`.doc→.docx` textutil 转换已整体删除）",
      q63bWordDocWhy.contains("另存为 .docx") && q63bWordDocWhy.contains("未使用该文件"),
      "why=\(q63bWordDocWhy)")
check("q63b-meta-empty",
      "空元数据路径 = 没选 = 通过；源稿未选时先放行（run() 按当时源稿再判一次）",
      ConvertFeature.metadataPathVerdict(metadata: "", source: "/x/稿.tex") == .accepted
        && ConvertFeature.metadataPathVerdict(metadata: "   ", source: "") == .accepted,
      "ok")

// ── 源码级接线（等价证据；Web/CI 上不开窗口）
let q63bFormSrc = readSrc("Features/MetadataFormFeature.swift")
let q63bSheetSrc = readSrc("Views/MetadataFormSheet.swift")
check("q63b-ui-wiring",
      "初排页：高级选项内「填写元数据…」+ sheet + 摘要/清空（B-017 起普通模式唯一入口）",
      q63TabSrc.contains("填写元数据…")
        && q63TabSrc.contains("model.showMetadataForm = true")
        && q63TabSrc.contains(".sheet(isPresented: $model.showMetadataForm)")
        && q63TabSrc.contains("MetadataFormSheet(form: $model.metadataForm)")
        && q63TabSrc.contains("summaryLine()"),
      "button=\(q63TabSrc.contains("填写元数据…")) sheet=\(q63TabSrc.contains(".sheet("))")
check("q63b-ui-fields",
      "sheet 字段齐全：中英文题名/摘要/关键词 + 中英文姓名/邮箱 + 添加作者 + 添加单位（两语言）",
      ["中文题名", "英文题名", "中文摘要", "英文摘要", "中文关键词", "英文关键词",
       "添加作者", "添加单位", "中文姓名", "英文姓名", "邮箱", "第一作者"]
        .allSatisfy { q63bSheetSrc.contains($0) },
      "len=\(q63bSheetSrc.count)")
check("q67-ui-inline-fallback",
      "B-017：旧 7 内联字段保留但只在两段式兜底渲染；不再被『表单有值』置灰"
        + "（两段式不下传表单，置灰会让两个入口都不可用）",
      !q63TabSrc.contains(".disabled(model.metadataForm.hasAnyValue)")
        && q63TabSrc.contains("两段式兜底")
        && q63TabSrc.contains("表单优先"),
      "disabled=\(q63TabSrc.contains(".disabled(model.metadataForm.hasAnyValue)"))")
check("q63b-doc-entry-removed",
      "`.doc/.docx` 识别入口彻底撤下：无 textutil / metadataConversionPlan / convertMetadataDoc / .doc 转临时 docx 文案",
      !q63TabSrc.contains("/usr/bin/textutil")
        && !q63TabSrc.contains("metadataConversionPlan")
        && !q63TabSrc.contains("convertMetadataDoc")
        && !q63TabSrc.contains(".doc 已转成临时 .docx")
        && !q63ConvSrc.contains("metadataConversionPlan")
        && !q63ConvSrc.contains("MetadataConversion"),
      "tab_clean=\(!q63TabSrc.contains("/usr/bin/textutil")) conv_clean=\(!q63ConvSrc.contains("metadataConversionPlan"))")
check("q63b-metadata-file-ui",
      "元数据文件 picker 限 .tex/.docx（批223 升级为多份入口：.docx/.tex/.json）；"
        + "placeholder 按源稿通道动态变（LaTeX 写已取消、Word 写第二源）",
      q63TabSrc.contains("allowedExtensions: [\"docx\", \"tex\", \"json\"]")
        && q63TabSrc.contains("metadataFilePlaceholder")
        && q63TabSrc.contains(".doc/.docx 自动识别已取消")
        && q63TabSrc.contains("英文元数据第二源"),
      "picker=\(q63TabSrc.contains("allowedExtensions")) placeholder=\(q63TabSrc.contains("metadataFilePlaceholder"))")
func q223Accepted(_ v: ConvertFeature.MetadataPathVerdict) -> Bool {
    if case .accepted = v { return true }
    return false
}
func q223Why(_ v: ConvertFeature.MetadataPathVerdict) -> String {
    if case .ignored(let w) = v { return w }
    return ""
}
var q223Multi = ConvertFeature.Options()
q223Multi.source = "/tmp/a.docx"
q223Multi.out = "/tmp/out"
q223Multi.metadataSources = ["/tmp/补充一.docx", "/tmp/补充二.tex"]
let q223MultiArgs = ConvertFeature.arguments(q223Multi)
let q223MetaIdxs = q223MultiArgs.indices.filter { q223MultiArgs[$0] == "--metadata" }
check("q223-argv-multi-metadata",
      "多份补充信息 → 多个 --metadata（顺序 = 合并顺序）；默认 argv 仍逐字节不变",
      q223MetaIdxs.count == 2
        && q223MetaIdxs.map { q223MultiArgs[$0 + 1] } == ["/tmp/补充一.docx", "/tmp/补充二.tex"]
        && ConvertFeature.arguments(ConvertFeature.Options()) == ["--compile", "--clean"],
      "argv=\(q223MultiArgs)")
check("q223-verdict-multi",
      "补充信息判据：两通道都收 .docx/.tex/.json；.doc 要另存、其它拒；"
        + "旧单值 metadataPathVerdict 并存不改（旧断言逐条仍绿）",
      q223Accepted(ConvertFeature.metadataSourceVerdict(path: "/x/作者信息.docx", source: "/x/正文.docx"))
        && q223Accepted(ConvertFeature.metadataSourceVerdict(path: "/x/英摘.docx", source: "/x/正文.tex"))
        && q223Accepted(ConvertFeature.metadataSourceVerdict(path: "/x/meta.tex", source: "/x/正文.tex"))
        && q223Accepted(ConvertFeature.metadataSourceVerdict(path: "/x/meta.json", source: "/x/正文.docx"))
        && q223Why(ConvertFeature.metadataSourceVerdict(path: "/x/old.doc", source: "/x/正文.docx"))
            .contains("另存为 .docx")
        && q223Why(ConvertFeature.metadataSourceVerdict(path: "/x/pic.pdf", source: "/x/正文.tex"))
            .contains(".docx / .tex / .json"),
      "docx=\(q223Accepted(ConvertFeature.metadataSourceVerdict(path: "/x/作者信息.docx", source: "/x/正文.docx")))"
        + " json=\(q223Accepted(ConvertFeature.metadataSourceVerdict(path: "/x/meta.json", source: "/x/正文.docx")))")
let q223SampleJSON = """
{"schema":1,"source":"/tmp/正文.docx","channel":"word","lang":"zh",
 "rows":[{"field":"author_cn","label":"第一作者","value":"唐敏",
          "source":"补充文件: 作者信息.docx（第 1 个）","status":"已填（补充文件）"},
         {"field":"author_en","label":"第一作者英文姓名","value":"","source":"","status":"未识别"}],
 "conflicts":["第一作者：正文已有「张三」，未采用「唐敏」"],
 "unidentified":["字段「通讯作者」没有对应模板槽位"],
 "warnings":["补充信息只补空、不覆盖正文"]}
"""
let q223Decoded = try? JSONDecoder().decode(ConvertFeature.MetadataPreviewPayload.self,
                                            from: Data(q223SampleJSON.utf8))
check("q223-preview-decode",
      "预览 sheet 的数据模型能吃引擎 --metadata-preview-out 的 JSON（行 / 冲突 / 未识别 / 警告）",
      q223Decoded?.rows.count == 2
        && q223Decoded?.rows.first?.status == "已填（补充文件）"
        && q223Decoded?.rows.last?.status == "未识别"
        && q223Decoded?.conflicts.count == 1
        && q223Decoded?.unidentified.count == 1
        && q223Decoded?.warnings.count == 1,
      "rows=\(q223Decoded?.rows.count ?? -1)")
let q223SheetSrc = readSrc("Views/MetadataPreviewSheet.swift")
check("q223-ui-multi-source",
      "GUI：单入口「元数据 / 补充信息（可选）」= 多选列表 + 添加/预览 + 预览 sheet；新 View 只展示",
      q63TabSrc.contains("元数据 / 补充信息（可选）")
        && q63TabSrc.contains("metadataSourcesSection")
        && q63TabSrc.contains("Button(\"添加…\")")
        && q63TabSrc.contains("Button(\"预览…\")")
        && q63TabSrc.contains("MetadataPreviewSheet(payload: model.metadataPreview)")
        && q223SheetSrc.contains("struct MetadataPreviewSheet: View")
        && q223SheetSrc.contains("payload.rows"),
      "tab=\(q63TabSrc.contains("metadataSourcesSection")) sheet=\(q223SheetSrc.contains("MetadataPreviewSheet"))")
check("q223-preview-wiring",
      "预览 = 引擎 dry-run（--metadata-preview-out）+ 临时 JSON 读完即删；预览不写产物/交接单",
      q63ConvSrc.contains("--metadata-preview-out")
        && q63ConvSrc.contains("func previewMetadata(")
        && q63ConvSrc.contains("slcn-meta-preview-")
        && q63TabSrc.contains("previewMetadataSources()")
        && q63TabSrc.contains("MetadataForm.removeTemporaryFile(p)")
        && q63TabSrc.contains("未写任何产物"),
      "conv=\(q63ConvSrc.contains("--metadata-preview-out")) tab=\(q63TabSrc.contains("previewMetadataSources"))")
check("q223-coop-scan-unchanged",
      "两段式 / 只扫描不下传补充信息（旧行为不变）：同一 guard + 明确日志",
      q63TabSrc.contains("!o.scanOnly && !(o.coop && !o.scanOnly)")
        && q63TabSrc.contains("metadataSourcesIgnoredLogText"),
      "ok")
check("q63b-run-wiring",
      "run()：verdict 双拦截 + 临时 JSON（写失败中止不静默）+ 内联逐字段清零 + 日志「N 位作者 / M 项字段」+ 结束清理",
      q63TabSrc.contains("metadataPathVerdict")
        && q63TabSrc.contains("metadataJSONTempPath")
        && q63TabSrc.contains("MetadataForm.removeTemporaryFile(temp)")
        && q63TabSrc.contains("不会静默丢掉已填内容")
        && q63TabSrc.contains("coversInlineOverride")
        && q63TabSrc.contains("两段式协同暂不支持人工填写元数据"),
      "verdict=\(q63TabSrc.contains("metadataPathVerdict")) cleanup=\(q63TabSrc.contains("removeTemporaryFile(temp)"))")
check("q63b-conv-wiring",
      "ConvertFeature：Options.metadataJSON + arguments() 加 --metadata-json（仅 coop==off && !scanOnly）",
      q63ConvSrc.contains("var metadataJSON = \"\"")
        && q63ConvSrc.contains("add(\"--metadata-json\", o.metadataJSON)")
        && q63ConvSrc.contains("if coop == .off && !o.scanOnly"),
      "opt=\(q63ConvSrc.contains("var metadataJSON")) flag=\(q63ConvSrc.contains("--metadata-json"))")
check("q63b-form-log-text",
      "统计/日志文案在纯逻辑里：包含「将写入」「位作者」「项字段」",
      q63bFormSrc.contains("将写入") && q63bFormSrc.contains("位作者")
        && q63bFormSrc.contains("项字段"),
      "len=\(q63bFormSrc.count)")

// ═══════════════════════════════════════════════════════════════════════════
// (q67) 批67 B-017：初排页元数据入口合并（单一入口）+ 模板上限说明/达限提示
//        方案A：不改模板/引擎；普通转换只显示表单，两段式只显示旧 7 内联兜底。
// ═══════════════════════════════════════════════════════════════════════════
section("T-q67  B-017：单一元数据入口（路由判据）/ 模板上限说明 / 达限可操作提示")

check("q67-entry-mode",
      "单入口路由纯逻辑：普通转换 → formOnly；两段式 → inlineFallback；只扫描忽略协同 → formOnly"
        + "（与 arguments() 的「只扫描时协同被忽略」同口径）",
      MetadataForm.entryMode(scanOnly: false, coop: false) == .formOnly
        && MetadataForm.entryMode(scanOnly: false, coop: true) == .inlineFallback
        && MetadataForm.entryMode(scanOnly: true, coop: true) == .formOnly,
      "normal=\(MetadataForm.entryMode(scanOnly: false, coop: false)) "
        + "coop=\(MetadataForm.entryMode(scanOnly: false, coop: true)) "
        + "scan=\(MetadataForm.entryMode(scanOnly: true, coop: true))")

check("q67-limit-summary",
      "上限常驻说明（入口旁/表单内同口径）：作者 5 位=模板 \\myfirstauthor…\\myfifthauthor 硬槽位、"
        + "不是可调设置；每位作者每语言单位 ≤3 行（版式容量，第 4 行溢版）",
      MetadataForm.limitSummary.contains("5 位")
        && MetadataForm.limitSummary.contains("myfirstauthor")
        && MetadataForm.limitSummary.contains("3 行")
        && MetadataForm.limitSummary.contains("不是可调设置")
        && MetadataForm.limitSummary.contains("溢版"),
      "limit=\(MetadataForm.limitSummary)")

var q67NoLimit = MetadataForm()
check("q67-limit-hints-empty",
      "未达上限：limitHints() 为空、两个 reached 判据为 false（界面不显示多余提示）",
      q67NoLimit.limitHints().isEmpty && !q67NoLimit.reachedAuthorLimit
        && !q67NoLimit.reachedAffiliationLimit(authorIndex: 0, language: .cn)
        && !q67NoLimit.reachedAffiliationLimit(authorIndex: 0, language: .en),
      "hints=\(q67NoLimit.limitHints())")

var q67Author = MetadataForm()
for _ in 0..<3 { _ = q67Author.addAuthor() }   // 4 位（未达限）
let q67Author4 = q67Author.limitHints()
_ = q67Author.addAuthor()                      // 5 位（达限；第 6 次会被拒）
let q67Author5 = q67Author.limitHints()
check("q67-author-limit-hint",
      "作者达限（5 位）：addAuthor 第 6 次被拒（阻止）+ 可操作提示（删空行/合并同单位作者/移致谢）；"
        + "未达限时无提示",
      q67Author4.isEmpty
        && q67Author.reachedAuthorLimit
        && !q67Author.addAuthor()
        && q67Author5.count == 1
        && q67Author5[0].contains("作者已达模板上限 5 位")
        && q67Author5[0].contains("合并同单位作者")
        && q67Author5[0].contains("致谢"),
      "hints4=\(q67Author4) hints5=\(q67Author5)")

var q67Aff = MetadataForm()
let q67AffID = q67Aff.authors[0].id
_ = q67Aff.addAffiliation(authorID: q67AffID, language: .cn)
_ = q67Aff.addAffiliation(authorID: q67AffID, language: .cn)
let q67Aff2 = q67Aff.limitHints()
_ = q67Aff.addAffiliation(authorID: q67AffID, language: .cn)
let q67Aff3 = q67Aff.limitHints()
check("q67-aff-limit-hint",
      "单位达限（每语言 3 行）：第 4 次 addAffiliation 被拒（阻止）+ 可操作提示"
        + "（删空行/合并写到一行/脚注）；中文满不影响英文",
      q67Aff2.isEmpty
        && q67Aff.reachedAffiliationLimit(authorIndex: 0, language: .cn)
        && !q67Aff.reachedAffiliationLimit(authorIndex: 0, language: .en)
        && !q67Aff.addAffiliation(authorID: q67AffID, language: .cn)
        && q67Aff3.count == 1
        && q67Aff3[0].contains("中文单位已到 3 行上限")
        && q67Aff3[0].contains("合并写到一行")
        && q67Aff3[0].contains("脚注"),
      "hints2=\(q67Aff2) hints3=\(q67Aff3)")

check("q67-ui-single-entry",
      "ConvertTab 接线路由：唯一判据 entryMode → 普通渲染表单 / 两段式渲染内联兜底；"
        + "入口旁常驻上限说明；两段式「人工元数据表单不适用」说明与已填表单不下传提醒；"
        + "只扫描也有不下传日志（不静默）",
      q63TabSrc.contains("MetadataForm.entryMode(scanOnly: false, coop: model.options.coop)")
        && q63TabSrc.contains("metadataFormSection")
        && q63TabSrc.contains("coopMetadataFallbackSection")
        && q63TabSrc.contains("MetadataForm.limitSummary")
        && q63TabSrc.contains("人工元数据表单不适用")
        && q63TabSrc.contains("已填表单")
        && q63TabSrc.contains("只扫描源稿不产出交付物，本次不下传人工填写内容"),
      "entry=\(q63TabSrc.contains("MetadataForm.entryMode")) fallback=\(q63TabSrc.contains("coopMetadataFallbackSection"))")

check("q67-ui-sheet-limits",
      "表单内上限交互：作者区/单位行显示计数与常驻上限说明；达限时橙色可操作提示 + 按钮 help（无需猜原因）",
      q63bSheetSrc.contains("MetadataForm.limitSummary")
        && q63bSheetSrc.contains("reachedAuthorLimit")
        && q63bSheetSrc.contains("MetadataForm.authorLimitHint()")
        && q63bSheetSrc.contains("reachedAffiliationLimit")
        && q63bSheetSrc.contains("MetadataForm.affiliationLimitHint(")
        && q63bSheetSrc.contains(".help("),
      "len=\(q63bSheetSrc.count)")

check("q67-argv-default-zero-change",
      "B-017 默认路径零变化：不填任何输入时 argv 仍 = [--compile, --clean]（无 --metadata-json / 无 7 覆盖参数），"
        + "Options() 的 7 个内联字段全空",
      ConvertFeature.arguments(ConvertFeature.Options()) == ["--compile", "--clean"]
        && !ConvertFeature.arguments(ConvertFeature.Options()).contains("--metadata-json")
        && ConvertFeature.Options().titleCn.isEmpty
        && ConvertFeature.Options().abstractEn.isEmpty,
      "argv=\(ConvertFeature.arguments(ConvertFeature.Options()))")

// ═══════════════════════════════════════════════════════════════════════════
// (q69) 批69 B-111：高级选项「第一作者」→ 表单第一作者中文姓名（单向预填）
//        spec: EditToolBox-notes/drafts/specs/b69-B111-author-prefill.md
//        只读 Options.author（语义不变：仍只用于输出名/--author）；只改表单会话态 →
//        不写 JSON、不改 argv；默认转换 argv/产物逐字节不变。
// ═══════════════════════════════════════════════════════════════════════════
section("T-q69  B-111：第一作者单向预填（空则填 / 已填不覆盖 / 双空不变 / 作者空不变）")

// ① 表单空 + 高级选项非空 → 预填第一作者中文姓名
let q69Empty = MetadataForm()
let q69Filled = MetadataForm.prefillFirstAuthorCN(from: "张三", current: q69Empty)
check("q69-prefill-when-empty",
      "① 表单第一作者中文姓名为空 + 高级选项「第一作者」非空 → 预填该值"
        + "（只动第一作者中文姓名；英文姓名/题名等其余状态不变）",
      q69Filled.authors.count == 1
        && q69Filled.authors[0].nameCN == "张三"
        && q69Filled.authors[0].trimmedNameCN == "张三"
        && q69Filled.authors[0].nameEN.isEmpty
        && q69Filled.titleCN.isEmpty,
      "nameCN=\(q69Filled.authors[0].nameCN)")

// ② 表单已填 → 不覆盖（含用户手动改过的值）
var q69Manual = MetadataForm()
q69Manual.titleCN = "已有题名"
q69Manual.authors[0].nameCN = "李四"
let q69Kept = MetadataForm.prefillFirstAuthorCN(from: "张三", current: q69Manual)
check("q69-no-overwrite",
      "② 表单第一作者中文姓名已填 → 不覆盖（以表单当前值为准；用户手动改过的值同样保留）",
      q69Kept.authors[0].nameCN == "李四"
        && q69Kept.titleCN == "已有题名"
        && q69Kept == q69Manual,
      "nameCN=\(q69Kept.authors[0].nameCN)")

// ③ 两处都空 → 不变（表单仍视为全空 = 不下传 --metadata-json）
let q69BothEmpty = MetadataForm()
let q69BothEmptyAfter = MetadataForm.prefillFirstAuthorCN(from: "", current: q69BothEmpty)
check("q69-both-empty",
      "③ 表单空 + 高级选项空 → 完全不变（hasAnyValue 仍 false → 不写临时 JSON/不传 --metadata-json）",
      q69BothEmptyAfter == q69BothEmpty
        && !q69BothEmptyAfter.hasAnyValue
        && !q69BothEmpty.hasAnyValue,
      "hasAnyValue=\(q69BothEmptyAfter.hasAnyValue)")

// ④ 高级选项空 / 全空白 → 不变（不把空白当姓名）
let q69OnlyEmptyAuthor = MetadataForm()
let q69OnlyEmptyAuthorAfter = MetadataForm.prefillFirstAuthorCN(from: "   ",
                                                                current: q69OnlyEmptyAuthor)
check("q69-author-empty-no-change",
      "④ 高级选项「第一作者」为空 / 全空白 → 不变（不能把空白写成姓名）",
      q69OnlyEmptyAuthorAfter == q69OnlyEmptyAuthor
        && q69OnlyEmptyAuthorAfter.authors[0].nameCN.isEmpty,
      "nameCN='\(q69OnlyEmptyAuthorAfter.authors[0].nameCN)'")

// 接线（等价证据）：唯一入口在 showMetadataForm = true 之前调用纯逻辑预填
let q69PreRange = q63TabSrc.range(of: "MetadataForm.prefillFirstAuthorCN(")
let q69ShowRange = q63TabSrc.range(of: "model.showMetadataForm = true")
let q69OrderOK = q69PreRange != nil && q69ShowRange != nil
    && q69PreRange!.lowerBound < q69ShowRange!.lowerBound
check("q69-ui-wiring",
      "打开表单入口（ConvertTab「填写元数据…」）在 showMetadataForm = true 前调用纯逻辑预填；"
        + "表单仍只有这一个打开入口（不新增入口，B-017 单一入口不变）",
      q69OrderOK
        && q63TabSrc.contains("from: model.options.author")
        && q63TabSrc.contains("current: model.metadataForm")
        && q63TabSrc.contains("MetadataFormSheet(form: $model.metadataForm)"),
      "prefillBeforeShow=\(q69OrderOK)")

// Options.author 语义不变：仍只用于输出名 → --author（不产生 --author-cn / --metadata-json）
var q69Opts = ConvertFeature.Options()
q69Opts.author = "张三"
let q69OptsArgv = ConvertFeature.arguments(q69Opts)
check("q69-options-author-semantics",
      "Options.author 语义不变：仍只喂 --author（输出名推导）；不自动产生 --author-cn、"
        + "不写 --metadata-json（预填只在 GUI 表单会话态，不入 argv）",
      q69OptsArgv == ["--author", "张三", "--compile", "--clean"]
        && !q69OptsArgv.contains("--author-cn")
        && !q69OptsArgv.contains("--metadata-json")
        && q63ConvSrc.contains("add(\"--author\", o.author)"),
      "argv=\(q69OptsArgv)")

// 默认零变化：不填任何输入时 argv 仍 = [--compile, --clean]
check("q69-default-argv-zero-change",
      "B-111 默认路径零变化：Options() 默认 argv 仍 = [--compile, --clean]"
        + "（预填纯函数不读/不改 Options，也不触发临时 JSON）",
      ConvertFeature.arguments(ConvertFeature.Options()) == ["--compile", "--clean"]
        && !ConvertFeature.arguments(ConvertFeature.Options()).contains("--metadata-json")
        && MetadataForm.prefillFirstAuthorCN(from: "", current: MetadataForm()).jsonString() == nil,
      "argv=\(ConvertFeature.arguments(ConvertFeature.Options()))")

// ═══════════════════════════════════════════════════════════════════════════
// (p) 第 55 批：暂存目录发现（只读 `_AI协同状态.json`）+ 建议摘要
// ═══════════════════════════════════════════════════════════════════════════
section("T-p  第 55 批：暂存目录发现 / 建议摘要 / 状态文案")

func writeCoopState(_ dir: String, source: String, outRoot: String, stem: String,
                    phase: String, deliveredTo: String = "", mtime: TimeInterval? = nil) {
    mkdir(dir, 0o700)
    var obj: [String: Any] = [
        "schema": 1,
        "phase": phase,
        "source": ["path": source, "sha256": "deadbeef"],
        "params": ["out_root": outRoot, "stem": stem, "source": source],
        "taskbook": dir + "/" + stem + "_文献核实.md",
        "stage_dir": dir,
        "final_dir": outRoot + "/" + stem,
    ]
    if !deliveredTo.isEmpty { obj["delivered_to"] = deliveredTo }
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
    writeRaw(dir + "/" + CoopFeature.stateFileName, String(data: data, encoding: .utf8)!)
    if let mtime = mtime {
        try? FileManager.default.setAttributes(
            [.modificationDate: Date(timeIntervalSince1970: mtime)],
            ofItemAtPath: dir + "/" + CoopFeature.stateFileName)
    }
}

let discRoot = coopRoot + "/discovery"
let dOutA = discRoot + "/issue90"
let dOutB = discRoot + "/issue91"
let dSrc = discRoot + "/src/main.docx"
mkdir(discRoot, 0o700)
writeCoopState(dOutA + "/Lei_Li_待核", source: dSrc, outRoot: dOutA,
               stem: "Lei_Li", phase: "ph1", mtime: 1000)
writeCoopState(dOutA + "/Li_Si_待核", source: discRoot + "/src/other.docx",
               outRoot: dOutA, stem: "Li_Si", phase: "ph1", mtime: 2000)
writeCoopState(dOutB + "/Lei_Li_待核", source: dSrc, outRoot: dOutB,
               stem: "Lei_Li", phase: "ph1", mtime: 3000)
mkdir(dOutA + "/Broken_待核", 0o700)
writeRaw(dOutA + "/Broken_待核/" + CoopFeature.stateFileName, "not a json {")

let coopD1 = CoopFeature.discoverStages(outRoot: dOutA, source: dSrc)
check("p1-discover", "按 `source.path` + `params.out_root` 匹配：只找到属于当前源稿的那个暂存目录",
      coopD1.count == 1 && coopD1[0].stem == "Lei_Li" && coopD1[0].phase == "ph1"
        && coopD1[0].stageDir == CoopFeature.normalizePath(dOutA + "/Lei_Li_待核")
        && coopD1[0].taskbook.hasSuffix("Lei_Li_文献核实.md"),
      "found=\(coopD1.map { "\($0.stem)@\($0.phase)" })")
check("p2-decoy", "反例：别的源稿 / 别的输出根 / 坏 JSON 都不算（不猜）",
      !coopD1.contains { $0.stem == "Li_Si" }
        && CoopFeature.discoverStages(outRoot: dOutB, source: dSrc).count == 1,
      "outA=\(coopD1.count) outB=\(CoopFeature.discoverStages(outRoot: dOutB, source: dSrc).count)")

// 路径标准化：状态文件里写 `./` / `..` 变体也要认得出是同一份
writeCoopState(dOutA + "/Lei_Li2_待核", source: discRoot + "/src/../src/main.docx",
               outRoot: dOutA + "/.", stem: "Lei_Li2", phase: "done",
               deliveredTo: dOutA + "/Lei_Li2", mtime: 5000)
let coopD2 = CoopFeature.discoverStages(outRoot: dOutA, source: dSrc)
check("p3-normalize", "路径标准化：`..` / `./` 变体仍匹配同一源稿与输出根；多个候选取最近修改的",
      coopD2.count == 2 && coopD2[0].stem == "Lei_Li2" && coopD2[0].delivered,
      "found=\(coopD2.map { $0.stem })")
check("p4-done-record", "phase=done → delivered=true，协同记录指向 `delivered_to/_AI协同记录.md`",
      coopD2[0].recordPath == CoopFeature.normalizePath(dOutA + "/Lei_Li2") + "/" + CoopFeature.recordFileName
        && CoopFeature.stageStatusText(coopD2[0]) == "已交付"
        && CoopFeature.stageStatusText(coopD1[0]).contains("未交付")
        && CoopFeature.stageStatusText(nil) == "尚未初排",
      "record=\(coopD2[0].recordPath) status=\(CoopFeature.stageStatusText(coopD2[0]))")
check("p5-load-missing", "没有状态文件 / 坏 JSON → nil（界面显示「尚未初排」而不是崩）",
      CoopFeature.loadStage(stageDir: discRoot + "/nope") == nil
        && CoopFeature.loadStage(stageDir: dOutA + "/Broken_待核") == nil,
      "missing=\(CoopFeature.loadStage(stageDir: discRoot + "/nope") == nil)")

// 建议摘要：ph2 之后才有的 jsonl（accepted 按**字段**计数；rejected/missing 按条）
let coopSugStage = coopD1[0]
let coopSugPath = coopSugStage.suggestionsJSONLPath
let coopSugLines = [
    "{\"id\":\"BIB-1\",\"field\":\"year\",\"status\":\"accepted\"}",
    "{\"id\":\"BIB-1\",\"field\":\"pages\",\"status\":\"accepted\"}",
    "{\"id\":\"FN-3\",\"kind\":\"footnote\",\"status\":\"rejected\",\"reason\":\"证据不足\"}",
    "{\"id\":\"META-4\",\"kind\":\"metadata\",\"status\":\"missing\"}",
    "这一行不是 JSON（坏行要被计数而不是静默吞掉）",
]
writeRaw(coopSugPath, coopSugLines.joined(separator: "\n") + "\n")
let coopSug = CoopFeature.suggestionsSummary(coopSugStage)
check("q1-suggestions", "建议 jsonl 摘要：accepted 按字段计 2 / rejected 1 / missing 1 / 坏行 1",
      coopSug != nil && coopSug!.accepted == 2 && coopSug!.rejected == 1 && coopSug!.missing == 1
        && coopSug!.malformed == 1 && coopSug!.total == 4
        && coopSug!.summary.contains("通过校验 2"),
      "summary=\(coopSug?.summary ?? "<nil>")")
check("q2-suggestions-missing", "还没有建议清单（未跑第二段）→ nil，界面不显示假摘要",
      CoopFeature.suggestionsSummary(path: coopRoot + "/nope.jsonl") == nil
        && CoopFeature.suggestionsSummary(coopD1[0]).map { $0.malformed } == 1,
      "nil-path=\(CoopFeature.suggestionsSummary(path: coopRoot + "/nope.jsonl") == nil)")

// refresh：发现 + 建议 + 记录三件事的合并结果（界面刷新状态只用这一个入口）
let coopRefresh = CoopFeature.refresh(outRoot: dOutA, source: dSrc)
check("q3-refresh", "refresh()：selected = 最近修改的候选，建议摘要与记录存在性一并带出",
      coopRefresh.selected?.stem == "Lei_Li2" && coopRefresh.suggestions == nil
        && coopRefresh.recordExists == false && coopRefresh.stages.count == 2,
      "selected=\(coopRefresh.selected?.stem ?? "<nil>") stages=\(coopRefresh.stages.count)")

// 失败提示：日志尾部（退出码之外的诊断信息，界面必须给）
let coopTailText = "x\n\n  步骤  \n+  1.2s  ❌ 编译未通过\n+ 12.3s  y\n"
check("q4-logtail", "失败日志尾部：取最后 N 行、去掉时间戳前缀、逐行截断",
      CoopFeature.logTail(coopTailText, maxLines: 2) == "❌ 编译未通过\ny",
      "tail=\(CoopFeature.logTail(coopTailText, maxLines: 2))")

// 源码级接线：UI 必须真的走这些纯函数（界面照抄判据 = 双份真相）
let convTabSrc2 = readSrcGlob("Views/ConvertTab*.swift")
let convFeatSrc2 = readSrc("Features/ConvertFeature.swift")
let setTabSrc2 = readSrc("Views/SettingsTab.swift")
check("r1-ui-coop", "ConvertTab：有「AI 协同初排（两段式）」开关 + 多路回填导入（批56）+ --coop-apply 勾选 + 「未交付」口径",
      convTabSrc2.contains("AI 协同初排（两段式；外部 chat 免费流程）")
        && convTabSrc2.contains("添加文件…")
        && convTabSrc2.contains("添加目录…")
        && convTabSrc2.contains("从剪贴板导入")
        && convTabSrc2.contains("--coop-apply")
        && convTabSrc2.contains("未交付")
        && convTabSrc2.contains("CoopFeature.refresh("),
      "toggle=\(convTabSrc2.contains("AI 协同初排（两段式")) files=\(convTabSrc2.contains("添加文件…")) "
        + "clipboard=\(convTabSrc2.contains("从剪贴板导入")) apply=\(convTabSrc2.contains("--coop-apply"))")
check("r2-feature-wiring", "ConvertFeature.arguments：ph1/ph2 两段分派都交给 CoopFeature（判据只有一份）",
      convFeatSrc2.contains("CoopFeature.firstPhaseFlags(o)")
        && convFeatSrc2.contains("CoopFeature.secondPhaseArguments(o)"),
      "first=\(convFeatSrc2.contains("CoopFeature.firstPhaseFlags(o)")) "
        + "second=\(convFeatSrc2.contains("CoopFeature.secondPhaseArguments(o)"))")
check("r3-settings-budget", "SettingsTab：单篇/单批预算输入走 update 方法（非法不落盘）+ 隐私口径「零上传/零费用」",
      setTabSrc2.contains("budget.updateArticle(")
        && setTabSrc2.contains("budget.updateBatch(")
        && setTabSrc2.contains("外部 chat")
        && setTabSrc2.contains("零上传")
        && setTabSrc2.contains("条目/片段"),
      "article=\(setTabSrc2.contains("budget.updateArticle(")) "
        + "batch=\(setTabSrc2.contains("budget.updateBatch(")) "
        + "零上传=\(setTabSrc2.contains("零上传"))")
let testRunnerSrc = (try? String(contentsOfFile: projFile("tools/run_swift_tests.sh"),
                                 encoding: .utf8)) ?? ""
// 批76 B-143：新增第 7 台 `bibdb_ux`（引用库 GUI 纯逻辑）——台数断言同步 6 → 7。
// B-225：新增第 8 台 `dirpush`（目录摘要推送 GUI 纯逻辑）——台数断言同步 7 → 8。
check("r4-runner", "run_swift_tests.sh 的编译清单已加入 CoopFeature.swift（现 8 台）",
      testRunnerSrc.contains("Features/CoopFeature.swift")
        && testRunnerSrc.contains("Features/DirPushFeature.swift")
        && testRunnerSrc.components(separatedBy: "run_one ").count - 1 == 8,
      "listed=\(testRunnerSrc.contains("Features/CoopFeature.swift")) "
        + "harnesses=\(testRunnerSrc.components(separatedBy: "run_one ").count - 1)")

// ═══════════════════════════════════════════════════════════════════════════
// (s) 第 56 批：回填导入重构（多路径 / 剪贴板 / 已选清单 / stats 摘要 / 0 回填警示）
//     用户真测：DeepSeek 回了 json + txt 两个文件，批55 多选折叠成父目录 → 只导入了 json、
//     丢掉 9 条 BibTeX → 文献回填 0/9 却显示「待人工 0」；ph2 也没传 --clean。
// ═══════════════════════════════════════════════════════════════════════════
section("T-s  第 56 批：多路径回填 / 剪贴板 / 已选清单 / stats 摘要 / 0 回填警示")

let s56Root = base.appendingPathComponent("batch56").path
wipe(s56Root); mkdir(s56Root, 0o700)
let s56Json = s56Root + "/deepseek_json_20261019_a89ebb.json"
let s56Txt = s56Root + "/deepseek_bibtex_20261019_b4a25e.txt"
let s56Dir = s56Root + "/replies_dir"
writeRaw(s56Json, "[]"); writeRaw(s56Txt, "@article{key,\n}\n"); mkdir(s56Dir, 0o700)
func cp(_ raw: String) -> String { CoopFeature.normalizePath(raw) }

// s1 多路径 ph2：**每个已选路径各一个 --coop-replies**（顺序 = 已选顺序），不再折叠父目录；
//    --clean 随页面「清理编译中间文件」开关（批56 起 ph2 也生效）。
var s56a = ConvertFeature.Options()
s56a.source = coopSrc; s56a.out = coopOut; s56a.coopStage = coopStage
s56a.coopRepliesPaths = [s56Json, s56Txt, s56Dir]
s56a.coopPhase = .second; s56a.coopApply = true; s56a.compile = true; s56a.clean = true
let s56aArgv = ConvertFeature.arguments(s56a)
let s56aWant = ["--source", cp(coopSrc), "--out", cp(coopOut),
                "--coop-replies", cp(s56Json),
                "--coop-replies", cp(s56Txt),
                "--coop-replies", cp(s56Dir),
                "--coop-stage", cp(coopStage), "--coop-apply", "--compile", "--clean"]
check("s1-multi-replies", "多路径回填：三个路径各一个 --coop-replies（按已选顺序）+ --coop-apply/--compile/--clean",
      s56aArgv == s56aWant, "argv=\(s56aArgv)")
var s56b = s56a; s56b.compile = false; s56b.clean = false
let s56bArgv = ConvertFeature.arguments(s56b)
check("s1b-clean-off", "页面「清理编译中间文件」关 → ph2 **不传** --clean（批55 行为；批56 才支持该开关）",
      !s56bArgv.contains("--clean") && !s56bArgv.contains("--compile")
        && s56bArgv.filter { $0 == "--coop-replies" }.count == 3,
      "argv=\(s56bArgv)")
var s56c = s56a; s56c.coopRepliesPaths = []
check("s1c-no-replies", "回填清单为空 → argv 里没有一个 --coop-replies（按钮也会 disabled，这里只钉 argv 不发明参数）",
      ConvertFeature.arguments(s56c).filter { $0 == "--coop-replies" }.isEmpty,
      "argv=\(ConvertFeature.arguments(s56c))")
// 单路径仍只有一个 flag（不重复、不折叠）
var s56d = s56a; s56d.coopRepliesPaths = [s56Txt]
check("s1d-single", "单路径：恰好一个 --coop-replies（与批55 的语义衔接）",
      ConvertFeature.arguments(s56d).filter { $0 == "--coop-replies" }.count == 1
        && ConvertFeature.arguments(s56d).contains(cp(s56Txt)),
      "argv=\(ConvertFeature.arguments(s56d))")

// s2 已选清单纯逻辑：累加 / 去重 / 移除 / 清空 / 文件与目录混合计数
var s56sel = CoopFeature.RepliesSelection()
check("s2-empty", "空清单：summary 明确说「尚未选择任何回填」（界面据此显示橙色提示）",
      s56sel.isEmpty && s56sel.summary == "尚未选择任何回填"
        && s56sel.fileCount == 0 && s56sel.directoryCount == 0,
      "summary=\(s56sel.summary)")
let s56okJson = s56sel.add(CoopFeature.ReplyItem(path: s56Json, isDirectory: false))
let s56okTxt = s56sel.add(CoopFeature.ReplyItem(path: s56Txt, isDirectory: false))
let s56okDir = s56sel.add(CoopFeature.ReplyItem(path: s56Dir, isDirectory: true))
check("s2-accumulate", "多次导入累加：文件 2 + 目录 1 → 「已选 3 项（文件 2 / 目录 1）」",
      s56okJson && s56okTxt && s56okDir
        && s56sel.count == 3 && s56sel.fileCount == 2 && s56sel.directoryCount == 1
        && s56sel.summary == "已选 3 项（文件 2 / 目录 1）",
      "summary=\(s56sel.summary)")
let s56dup = s56sel.add(CoopFeature.ReplyItem(path: s56Json, isDirectory: false))
check("s2-dedupe", "同一路径重复加入 → 返回 false、清单不重复（引擎侧也会按真实路径去重）",
      !s56dup && s56sel.count == 3, "count=\(s56sel.count) dupAdded=\(s56dup)")
let s56rm = s56sel.remove(path: s56Txt)
check("s2-remove", "逐项移除：移除一项后清单 2 项、顺序保持",
      s56rm?.path == cp(s56Txt) && s56sel.count == 2
        && s56sel.paths == [s56Json, s56Dir],
      "paths=\(s56sel.paths)")
s56sel.clear()
check("s2-clear", "「全部清除」→ 空清单（界面据此把第二段按钮 disable）",
      s56sel.isEmpty && s56sel.paths.isEmpty, "summary=\(s56sel.summary)")

// s3 剪贴板：临时 .md 命名（纯逻辑）+ 文本校验 + 草稿识别
let s56stamp = "20261019-221530"
let s56clip1 = CoopFeature.clipboardDraftPath(directory: s56Root, timestamp: s56stamp, index: 1)
let s56clip2 = CoopFeature.clipboardDraftPath(directory: s56Root, timestamp: s56stamp, index: 2)
check("s3-naming", "剪贴板草稿命名：前缀 + 时间戳 + 序号 + .md（同秒重复粘贴不覆盖）",
      s56clip1.hasSuffix("/slcn-coop-clipboard-20261019-221530-1.md")
        && s56clip2.hasSuffix("/slcn-coop-clipboard-20261019-221530-2.md")
        && s56clip1 != s56clip2
        && CoopFeature.clipboardDraftFileName(timestamp: "", index: 0)
            == "slcn-coop-clipboard-00000000-000000-1.md",
      "1=\((s56clip1 as NSString).lastPathComponent) 2=\((s56clip2 as NSString).lastPathComponent)")
let s56clipPath = CoopFeature.clipboardDraftPath(directory: s56Root, timestamp: s56stamp, index: 3)
writeRaw(s56clipPath, "# 剪贴板回填\n@article{a,\n}\n")
var s56sel2 = CoopFeature.RepliesSelection()
s56sel2.add(CoopFeature.ReplyItem(path: s56clipPath, isDirectory: false))
s56sel2.add(CoopFeature.ReplyItem(path: s56Json, isDirectory: false))
check("s3-draft", "草稿识别：只有剪贴板临时 .md 算草稿（移除/清空时只删它，不碰用户文件）",
      CoopFeature.isClipboardDraft(s56clipPath) && !CoopFeature.isClipboardDraft(s56Json)
        && s56sel2.clipboardDraftCount == 1
        && s56sel2.summary == "已选 2 项（文件 2 / 目录 0 / 剪贴板草稿 1）",
      "draft=\(CoopFeature.isClipboardDraft(s56clipPath)) jsonDraft=\(CoopFeature.isClipboardDraft(s56Json))")
check("s3-text", "剪贴板文本校验：空/纯空白 → nil（红字提示）；正常文本保留换行",
      CoopFeature.clipboardText(nil) == nil
        && CoopFeature.clipboardText("   \n\t") == nil
        && CoopFeature.clipboardText("a\nb") == "a\nb",
      "nil→nil text=\(String(describing: CoopFeature.clipboardText("a\nb")))")

// s4 state.stats 解析 + 成功摘要口径（用户真测：9/9 + 低置信 22 项）
let s56Out = s56Root + "/issue90"
let s56Final = s56Out + "/Lei_Li"
let s56Stage = s56Out + "/Lei_Li_待核"
mkdir(s56Stage, 0o700); mkdir(s56Final, 0o700)
let s56State: [String: Any] = [
    "schema": 1, "phase": "done",
    "source": ["path": coopSrc, "sha256": "x"],
    "params": ["out_root": s56Out, "stem": "Lei_Li"],
    "taskbook": s56Stage + "/Lei_Li_文献核实.md",
    "stage_dir": s56Stage, "final_dir": s56Final, "delivered_to": s56Final,
    "stats": ["bib_total": 9, "bib_replied": 9, "bib_pending": 0,
              "low_total": 22, "low_replied": 9, "low_applied": 9,
              "low_pending_manual": 13],
]
let s56StateData = try! JSONSerialization.data(withJSONObject: s56State, options: [.sortedKeys])
writeRaw(s56Stage + "/" + CoopFeature.stateFileName, String(data: s56StateData, encoding: .utf8)!)
writeRaw(s56Final + "/" + CoopFeature.recordFileName,
         "# AI 协同记录 · Lei_Li\n\n## 一、外部 chat 回填（文献字段）\n\n"
         + "- 解析到 9 条，拒绝 0 条，未回填 0 条\n\n## 二、reference.bib 并入\n\n"
         + "- 并入：**9** 条\n- 跳过：**0** 条\n")
writeRaw(s56Stage + "/Lei_Li_AI建议.md", "# 建议清单\n")
let s56Loaded = CoopFeature.loadStage(stageDir: s56Stage)
check("s4-stats-parse", "state.stats 解析：文献 9/9、低置信 22（回复 9 / 应用 9 / 待人工 13）",
      s56Loaded?.stats == CoopFeature.CoopStats(
        bibTotal: 9, bibReplied: 9, bibPending: 0,
        lowTotal: 22, lowReplied: 9, lowApplied: 9, lowPendingManual: 13),
      "stats=\(String(describing: s56Loaded?.stats))")
let s56Result = s56Loaded.map { CoopFeature.loadResult($0) }
check("s4-summary", "成功摘要固定口径：「文献回填 9/9（并入 9 条，未回填 0）｜低置信回复 9（应用 9 / 仅记录待人工 13）」",
      s56Result?.summaryLine == "文献回填 9/9（并入 9 条，未回填 0）｜低置信回复 9（应用 9 / 仅记录待人工 13）"
        && s56Result?.merged == 9 && s56Result?.zeroBibWarning == nil
        && s56Result?.stats.legacy == false,
      "summary=\(s56Result?.summaryLine ?? "<nil>")")
check("s4-suggestions-path", "「打开建议清单」路径：暂存目录里的 `<stem>_AI建议.md` 优先",
      s56Result?.suggestionsMarkdownPath == s56Stage + "/Lei_Li_AI建议.md",
      "path=\(s56Result?.suggestionsMarkdownPath ?? "<nil>")")

// s5 文献回填 0/N → 橙色警告文案固定（不再出现误导性的「待人工 0」）
let s56ZeroState: [String: Any] = [
    "schema": 1, "phase": "done",
    "source": ["path": coopSrc, "sha256": "x"],
    "params": ["out_root": s56Out, "stem": "Lei_Li0"],
    "taskbook": "", "stage_dir": s56Stage + "0", "final_dir": s56Final + "0",
    "delivered_to": s56Final + "0",
    "stats": ["bib_total": 9, "bib_replied": 0, "bib_pending": 9,
              "low_total": 22, "low_replied": 9, "low_applied": 9,
              "low_pending_manual": 13],
]
mkdir(s56Stage + "0", 0o700); mkdir(s56Final + "0", 0o700)
let s56ZeroData = try! JSONSerialization.data(withJSONObject: s56ZeroState, options: [.sortedKeys])
writeRaw(s56Stage + "0/" + CoopFeature.stateFileName,
         String(data: s56ZeroData, encoding: .utf8)!)
writeRaw(s56Final + "0/" + CoopFeature.recordFileName,
         "# AI 协同记录 · Lei_Li0\n\n## 一、外部 chat 回填（文献字段）\n\n"
         + "- 解析到 0 条，拒绝 0 条，未回填 9 条\n")
let s56ZeroLoaded = CoopFeature.loadStage(stageDir: s56Stage + "0")
let s56ZeroResult = s56ZeroLoaded.map { CoopFeature.loadResult($0) }
check("s5-zero-warning", "文献回填 0/9 → 橙色警告（固定文案），摘要明确 0/9、并入 0 条",
      s56ZeroResult?.zeroBibWarning == CoopFeature.zeroBibRepliesWarning
        && s56ZeroResult?.summaryLine.contains("文献回填 0/9（并入 0 条，未回填 9）") == true
        && s56ZeroResult?.stats.zeroBibReplies == true,
      "warning=\(s56ZeroResult?.zeroBibWarning ?? "<nil>")")
check("s5-no-fake-pending", "反例：不是 0 回填时**不显示**警告（9/9 那组 zeroBibWarning=nil）",
      s56Result?.zeroBibWarning == nil,
      "zero=\(s56Result?.zeroBibWarning ?? "<nil>")")

// s6 旧状态（没有 stats）优雅回退：从协同记录/旧 state 字段推算，并标 legacy
let s56LegacyStage = s56Out + "/Legacy_待核"
mkdir(s56LegacyStage, 0o700)
mkdir(s56Final + "/Legacy", 0o700)
let s56LegacyState: [String: Any] = [
    "schema": 1, "phase": "done",
    "source": ["path": coopSrc, "sha256": "x"],
    "params": ["out_root": s56Out, "stem": "Legacy"],
    "taskbook": "", "stage_dir": s56LegacyStage,
    "final_dir": s56Final + "/Legacy", "delivered_to": s56Final + "/Legacy",
    "keys": ["bib": ["a", "b", "c", "d"], "low_confidence": ["FN-1", "META-2"]],
    "replied": ["a", "b"], "applied_low_confidence": 1,
]
let s56LegacyData = try! JSONSerialization.data(withJSONObject: s56LegacyState, options: [.sortedKeys])
writeRaw(s56LegacyStage + "/" + CoopFeature.stateFileName,
         String(data: s56LegacyData, encoding: .utf8)!)
writeRaw(s56Final + "/Legacy/" + CoopFeature.recordFileName,
         "# AI 协同记录 · Legacy\n\n## 一、外部 chat 回填（文献字段）\n\n"
         + "- 解析到 2 条，拒绝 1 条，未回填 1 条\n\n## 二、reference.bib 并入\n\n"
         + "- 并入：**2** 条\n")
let s56LegacyLoaded = CoopFeature.loadStage(stageDir: s56LegacyStage)
let s56LegacyResult = s56LegacyLoaded.map { CoopFeature.loadResult($0) }
check("s6-legacy-fallback", "旧状态无 stats → 从协同记录推算文献 2/4（并入 2），标 legacy（界面注明估算）",
      s56LegacyResult?.stats.legacy == true
        && s56LegacyResult?.stats.bibTotal == 4
        && s56LegacyResult?.stats.bibReplied == 2
        && s56LegacyResult?.stats.bibPending == 1
        && s56LegacyResult?.merged == 2
        && s56LegacyResult?.summaryLine.contains("文献回填 2/4") == true,
      "summary=\(s56LegacyResult?.summaryLine ?? "<nil>")")
check("s6-stats-fromjson", "CoopStats.fromJSON：没有 stats 对象 → nil（旧状态不崩）；字段全缺也 nil",
      CoopFeature.CoopStats.fromJSON(nil) == nil
        && CoopFeature.CoopStats.fromJSON(["foo": 1]) == nil
        && CoopFeature.CoopStats.fromJSON(["bib_total": 3])?.bibTotal == 3,
      "nil→nil partial=\(String(describing: CoopFeature.CoopStats.fromJSON(["bib_total": 3])))")

// s7 第二段按钮启用/禁用判据（无回填必须 disable 且提示明确）
let s56RunStage = s56Loaded
check("s7-can-run", "第二段判据：有回填 + 未交付 → true；无回填 / 无暂存 / 已交付 → false",
      CoopFeature.canRunSecondPhase(replyCount: 2, stage: s56Loaded) == false  // phase=done
        && CoopFeature.canRunSecondPhase(replyCount: 2, stage: nil) == false
        && CoopFeature.canRunSecondPhase(replyCount: 0, stage: s56LegacyLoaded) == false,
      "done=\(CoopFeature.canRunSecondPhase(replyCount: 2, stage: s56Loaded)) "
        + "nil=\(CoopFeature.canRunSecondPhase(replyCount: 2, stage: nil))")
// 造一个 ph1 状态的 stage：有回填才允许跑
var s56Ph1 = CoopFeature.StageState()
s56Ph1.phase = "ph1"
check("s7-ph1-can-run", "ph1 暂存目录 + 已选 2 项 → 可以跑第二段；0 项 → 禁用并给「尚未选择任何回填」提示",
      CoopFeature.canRunSecondPhase(replyCount: 2, stage: s56Ph1)
        && !CoopFeature.canRunSecondPhase(replyCount: 0, stage: s56Ph1)
        && CoopFeature.secondPhaseDisabledHint(replyCount: 0, stage: s56Ph1)?
            .contains("尚未选择任何回填") == true
        && CoopFeature.secondPhaseDisabledHint(replyCount: 2, stage: s56Ph1) == nil,
      "hint=\(CoopFeature.secondPhaseDisabledHint(replyCount: 0, stage: s56Ph1) ?? "<nil>")")

// s8 源码级接线：界面真的用纯逻辑（防「两套真相」）
let s56TabSrc = readSrcGlob("Views/ConvertTab*.swift")
let s56ConvSrc = readSrc("Features/ConvertFeature.swift")
check("s8-tab-wiring", "ConvertTab：多文件/目录/剪贴板导入 + 已选清单移除/清除 + 第二段判据 + 0 回填警告 + 打开建议清单",
      s56TabSrc.contains("添加文件…") && s56TabSrc.contains("添加目录…")
        && s56TabSrc.contains("从剪贴板导入") && s56TabSrc.contains("全部清除")
        && s56TabSrc.contains("CoopFeature.canRunSecondPhase(")
        && s56TabSrc.contains("zeroBibWarning") && s56TabSrc.contains("打开建议清单")
        && s56TabSrc.contains("CoopFeature.clipboardDraftPath("),
      "files=\(s56TabSrc.contains("添加文件…")) clipboard=\(s56TabSrc.contains("从剪贴板导入")) "
        + "clear=\(s56TabSrc.contains("全部清除")) warn=\(s56TabSrc.contains("zeroBibWarning"))")
check("s8-feature-wiring", "ConvertFeature：ph2 传回填列表（o.coopRepliesPaths）且日志按项列出；不再有单值字段",
      s56ConvSrc.contains("o.coopRepliesPaths") && s56ConvSrc.contains("每个各传一个 --coop-replies")
        && !s56ConvSrc.contains("var coopReplies = "),
      "paths=\(s56ConvSrc.contains("o.coopRepliesPaths")) log=\(s56ConvSrc.contains("每个各传一个 --coop-replies"))")
check("s8-no-parent-folding", "源码里不再有「折叠成父目录」的旧实现（批55 的 importReplies 已删）",
      !s56TabSrc.contains("private func importReplies")
        && !s56TabSrc.contains("导入 AI 回复…")
        && !s56TabSrc.contains("引擎按目录读入"),
      "oldFunc=\(s56TabSrc.contains("private func importReplies")) "
        + "oldButton=\(s56TabSrc.contains("导入 AI 回复…"))")
// 普通转换零变化：coop 列表字段不影响默认路径；批62 入口收敛后默认 argv 不含 --verify-refs
check("s8-default-argv", "默认 Options 的 argv = --compile --clean"
      + "（批56 列表字段不碰默认路径；批62 普通转换不再默认出任务书）",
      ConvertFeature.arguments(ConvertFeature.Options()) == ["--compile", "--clean"],
      "argv=\(ConvertFeature.arguments(ConvertFeature.Options()))")

// ═══════════════════════════════════════════════════════════════════════════
// (m62) 第 62 批：文献核实入口收敛 B —— 隐藏独立勾选 / 普通转换不带 --verify-refs /
//       ⑪(useAI) 与 coop ph1 仍带 / CheckBib 页改为回读比对查看器（能力不减）
// ═══════════════════════════════════════════════════════════════════════════
// 默认值：内部 false（字段保留兼容旧脚本），普通转换 argv 只有编译/清理
let m62Default = ConvertFeature.Options()
check("m62-default-field", "批62：Options.verifyRefs 默认 false（字段保留兼容旧调用方/脚本）",
      m62Default.verifyRefs == false, "verifyRefs=\(m62Default.verifyRefs)")
check("m62-plain-no-flag", "普通初排转换不再带 --verify-refs（默认产物无 <stem>_文献核实.md）",
      !ConvertFeature.arguments(m62Default).contains("--verify-refs"),
      "argv=\(ConvertFeature.arguments(m62Default))")
// ⑪ 直连 AI：内部仍自动带 --verify-refs（行为/产物与批61 一致）
var m62AI = ConvertFeature.Options()
m62AI.useAI = true; m62AI.llmBase = "https://api.example.com/v1"; m62AI.llmModel = "m"
check("m62-ai-keeps-taskbook", "⑪（useAI）内部仍自动带 --verify-refs（产物与批61 完全一致）",
      ConvertFeature.arguments(m62AI).contains("--verify-refs"),
      "argv=\(ConvertFeature.arguments(m62AI))")
var m62AIkeep = m62AI; m62AIkeep.keepOriginalBib = true
check("m62-ai-keep-original", "⑪ + 保留原 bib → --verify-refs 与 --no-ai-merge 都在（行为不变）",
      ConvertFeature.arguments(m62AIkeep).contains("--verify-refs")
        && ConvertFeature.arguments(m62AIkeep).contains("--no-ai-merge"),
      "argv=\(ConvertFeature.arguments(m62AIkeep))")
// 两段式：ph1 仍带、ph2 不带
var m62Ph1 = ConvertFeature.Options(); m62Ph1.source = coopSrc; m62Ph1.out = coopOut
m62Ph1 = CoopFeature.prepare(m62Ph1, phase: .first)
check("m62-coop-ph1", "两段式第一段仍带 --verify-refs（任务书 = ph1 产物）",
      ConvertFeature.arguments(m62Ph1).contains("--verify-refs"),
      "argv=\(ConvertFeature.arguments(m62Ph1))")
var m62Ph2 = ConvertFeature.Options(); m62Ph2.source = coopSrc; m62Ph2.out = coopOut
m62Ph2.coopRepliesPaths = [coopReplies]; m62Ph2.coopStage = coopStage
m62Ph2 = CoopFeature.prepare(m62Ph2, phase: .second)
check("m62-coop-ph2", "两段式第二段不带 --verify-refs（最小集契约不变）",
      !ConvertFeature.arguments(m62Ph2).contains("--verify-refs"),
      "argv=\(ConvertFeature.arguments(m62Ph2))")
// 源码级：独立勾选已从界面移除；字段/toggled 分支保留兼容；两段式面板有引导文案
let m62TabSrc = readSrcGlob("Views/ConvertTab*.swift")
let m62ConvSrc = readSrc("Features/ConvertFeature.swift")
check("m62-ui-hidden", "批62：初排页不再暴露独立「生成文献核实任务书」勾选（无 Toggle/绑定/置灰说明）",
      !m62TabSrc.contains("生成参考文献核实任务书（交给自己的 AI 核实）")
        && !m62TabSrc.contains("binding(.verifyRefs") && !m62TabSrc.contains("自动勾上并锁定"),
      "oldToggle=\(m62TabSrc.contains("生成参考文献核实任务书（交给自己的 AI 核实）")) "
        + "bind=\(m62TabSrc.contains("binding(.verifyRefs"))")
check("m62-legacy-compat", "Options.verifyRefs 字段与 toggled(.verifyRefs) 分支保留（旧设置/脚本兼容）",
      m62ConvSrc.contains("var verifyRefs = false") && m62ConvSrc.contains("case .verifyRefs"),
      "field=\(m62ConvSrc.contains("var verifyRefs = false")) "
        + "case=\(m62ConvSrc.contains("case .verifyRefs"))")
check("m62-coop-guidance", "两段式面板有引导：任务书 = 两段式第一段产物；普通转换请用两段式或 CLI --verify-refs",
      m62TabSrc.contains("文献核实任务书 = 两段式第一段产物")
        && m62TabSrc.contains("--verify-refs（高级）"),
      "guidance=\(m62TabSrc.contains("文献核实任务书 = 两段式第一段产物"))")
// CheckBib 页：标题/引导改为回读比对查看器；能力（体检/并回/入藏/重新编译）一个不少
let m62CheckBibSrc = readSrc("Views/CheckBibView.swift")
let m62CheckBibFeat = readSrc("Features/CheckBibFeature.swift")
check("m62-checkbib-title", "CheckBib 页标题 = 回读/比对报告查看器（高级：手动并回，离线）；不再引导“生成任务书”",
      m62CheckBibSrc.contains("回读/比对报告查看器（高级：手动并回，离线）")
        && !m62CheckBibSrc.contains("使用说明：编辑拿《文献核实任务书》改完参考文献后交回"),
      "title=\(m62CheckBibSrc.contains("回读/比对报告查看器（高级：手动并回，离线）"))")
check("m62-checkbib-capabilities", "CheckBib 页能力保留：并回 / 入藏引用库 / 重新编译该篇入口仍在，Feature 仍调 checkbib.py（--merge-into）",
      m62CheckBibSrc.contains("并回") && m62CheckBibSrc.contains("入藏")
        && m62CheckBibSrc.contains("重新编译")
        && m62CheckBibFeat.contains("--merge-into") && m62CheckBibFeat.contains("checkbib.py"),
      "view 并回=\(m62CheckBibSrc.contains("并回")) 入藏=\(m62CheckBibSrc.contains("入藏")) "
        + "重编译=\(m62CheckBibSrc.contains("重新编译")) feature=\(m62CheckBibFeat.contains("--merge-into"))")
check("m62-checkbib-log-title", "CheckBib 运行日志标题同步为「回读/比对报告查看器」（只改定位文案）",
      m62CheckBibFeat.contains("log(\"回读/比对报告查看器"),
      "logTitle=\(m62CheckBibFeat.contains("log(\"回读/比对报告查看器"))")

// ===========================================================================
// (B-230) 拆分后「同族源文件」齐全性
//
//     ConvertTab 拆成 5 个**同层**文件（`build.sh` 的 `Views/*.swift` 非递归 glob 自动收，
//     构建脚本零改动）：ConvertTab.swift（保留名：页面骨架 + 运行/收尾）+
//     ConvertTabModel.swift（`ConvertModel` 类型本体）/ ConvertTabForm.swift /
//     ConvertTabCoop.swift / ConvertTabRows.swift。
//     上面 (g)/(i6)/(n59)/(n60)/(q63)/(r1)/(s8)/(m62) 的源码级断言改成读**整族**
//     （`readSrcGlob`）。这里守住「整族真的读到了、一个都没少」—— 否则文件缺失会让
//     `readSrcGlob` 回 ""，纯负向断言（`!src.contains(...)`）会假 PASS。
// ===========================================================================
section("B-230  mac 视图分包：同族源文件齐全")
let b230TabFamily = srcGlobFiles("Views/ConvertTab*.swift")
check("b230-tab-family", "ConvertTab 族源文件齐全（拆分后同层多文件，glob 拼接非空）",
      Set(["ConvertTab.swift", "ConvertTabCoop.swift", "ConvertTabForm.swift",
           "ConvertTabModel.swift", "ConvertTabRows.swift"]).isSubset(of: Set(b230TabFamily))
        && !readSrcGlob("Views/ConvertTab*.swift").isEmpty,
      "files=\(b230TabFamily.joined(separator: ","))")

print("")
print("================================================================")
print("SUMMARY: \(passCount) PASS / \(failCount) FAIL")
print("================================================================")
print("NOT COVERED BY THIS HARNESS: the URLSession probe itself (AIConnectionTester.test/send)")
print("was never called — no HTTP request was made. Only Reply→text mapping is verified.")
print("NOT COVERED (phase 2c): no child process is ever launched here, so `--returned-stdin`")
print("end-to-end and the paste UI were not exercised — see Engine/tests/test_merge_into.py ⑧/⑨")
print("and Engine/tests/test_checkbib.py ⑥, and the /tmp CLI e2e in case 67.")
exit(failCount == 0 ? 0 : 1)
