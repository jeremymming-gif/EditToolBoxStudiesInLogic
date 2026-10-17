
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
let convertTabSrc = readSrc("Views/ConvertTab.swift")
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

// i6: 源码级接线：SwiftUI 只绑定纯函数，联动逻辑只有一份
let tabSrc = readSrc("Views/ConvertTab.swift")
check("i6-bind", "ConvertTab 的复选框绑定走 `ConvertFeature.toggled(...)` + `verifyRefsLocked(...)`",
      tabSrc.contains("ConvertFeature.toggled(") && tabSrc.contains("ConvertFeature.verifyRefsLocked("),
      "toggled=\(tabSrc.contains("ConvertFeature.toggled(")) locked=\(tabSrc.contains("verifyRefsLocked("))")
check("i6-ui", "界面有：置灰说明 + 新开关「保留原 reference.bib（不并入 AI 结果）」",
      tabSrc.contains("自动勾上并锁定") && tabSrc.contains("保留原 reference.bib（不并入 AI 结果）"),
      "锁定说明=\(tabSrc.contains("自动勾上并锁定")) 新开关=\(tabSrc.contains("保留原 reference.bib（不并入 AI 结果）"))")
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
                     "Features/PublishFeature.swift",
                     "Features/TocFeature.swift",
                     "Features/InfoCheckFeature.swift",
                     "Features/CheckBibFeature.swift",
                     "Features/MergeFeature.swift"]
let uMissed = uFeatureFiles.filter { !readSrc($0).contains("Shell.cancelledStatus") }
check("u4-all-features", "全部 9 个 Feature 都处理了 `Shell.cancelledStatus`（取消不补「失败」话术）",
      uMissed.isEmpty,
      uMissed.isEmpty ? "9/9" : "未处理：\(uMissed.joined(separator: "、"))")
let uViewFiles = ["Views/SimpleFeatureView.swift", "Views/CheckBibView.swift",
                  "Views/ConvertTab.swift", "Views/ProofCheckView.swift",
                  "Views/InfoCheckView.swift", "Views/ContentTab.swift"]
let uNoCancel = uViewFiles.filter { !readSrc($0).contains("model.cancel?.cancel()") }
check("u5-views-have-cancel", "6 个可运行页的主按钮都变成「取消」（`model.cancel?.cancel()`）",
      uNoCancel.isEmpty,
      uNoCancel.isEmpty ? "6/6" : "缺：\(uNoCancel.joined(separator: "、"))")
check("u6-cancelled-banner", "完成横幅把「用户取消」显示成「⏹ 已取消」而不是 ❌ 失败",
      readSrc("Views/SimpleFeatureView.swift").contains("⏹ 已取消")
        && readSrc("Views/SimpleFeatureView.swift").contains("resultCancelled")
        && readSrc("Core/ViewModels.swift").contains("resultCancelled"),
      "")

// ===========================================================================
// (k) 第 69 条：通道（chat/responses）+ 联网检索开关 + ⑤ 检索探针
//
//     为什么这批断言重要：用户的要求是「让工具**真的**去联网核实」。
//     界面能保证的只有「把 `--llm-protocol responses --llm-web-search` 发出去」，
//     **端点到底会不会检索**必须实测（`slcn/llm.py: probe_web_search`，判据是回复里
//     有没有 `web_search_call`）——因为 DeepSeek 会**静默忽略**这个工具并照样回 200。
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

// k9 ⑤ 的接线：跑引擎、参数固定、密钥走环境变量（不进 argv）
check("k9-probe-argv", "⑤ 探针拼的是引擎 CLI：`-m slcn.llm --probe --json`",
      AIConnectionTester.searchProbeArguments() == ["-m", "slcn.llm", "--probe", "--json"],
      "\(AIConnectionTester.searchProbeArguments())")
let probeSrc = body(after: "static func runSearchProbe(", in: settingsSrc)
check("k9-key-via-env", "⑤ 把密钥放进**子进程环境**（SLCN_LLM_KEY），**不进 argv**",
      probeSrc.contains("env[\"SLCN_LLM_KEY\"] = key") && !probeSrc.contains("\"--key\""),
      "")
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
      + "（点名 DeepSeek 把 web_search 当「忽略」的内置工具）+ 「绝不假装已联网」",
      settingsSrc.contains("$settings.aiProtocol") && settingsSrc.contains("$settings.aiWebSearch")
        && settingsSrc.contains("忽略")
        && settingsSrc.contains("绝不假装已联网"),
      "picker=\(settingsSrc.contains("$settings.aiProtocol")) toggle=\(settingsSrc.contains("$settings.aiWebSearch")) "
        + "忽略=\(settingsSrc.contains("忽略")) 不假装=\(settingsSrc.contains("绝不假装已联网"))")
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
