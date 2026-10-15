import Foundation

// ============================================================================
// Headless, on-disk verification of the REAL production type AppSettings
// (sources-mac/Core/ViewModels.swift), compiled unmodified.
// Repo is READ-ONLY for this harness: everything is written under /tmp only.
// ============================================================================

// 同 ai_settings_test：从 `#filePath` 推项目根，不再写死绝对路径。
// 拆仓改名时只改 `SRC_DIR_NAME` 这一行。
let SRC_DIR_NAME = "sources-mac"
let PROJ = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent()      // …/tools/ai_store_test
    .deletingLastPathComponent()      // …/tools
    .deletingLastPathComponent()      // …/<项目根>
let REF = PROJ.appendingPathComponent(SRC_DIR_NAME)
    .appendingPathComponent("Core/ViewModels.swift").path
let ROOT = URL(fileURLWithPath: "/tmp/ai_store_test", isDirectory: true)

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

/// Raw POSIX mode as octal string, e.g. "600" / "???" if missing.
func mode(_ path: String) -> String {
    guard let a = try? FileManager.default.attributesOfItem(atPath: path),
          let n = a[.posixPermissions] as? NSNumber else { return "??? (missing)" }
    return String(format: "%03o", n.intValue & 0o777)
}

/// Raw mode in `ls -l` style, e.g. "-rw-------"
func modeSymbolic(_ path: String) -> String {
    guard let a = try? FileManager.default.attributesOfItem(atPath: path),
          let n = a[.posixPermissions] as? NSNumber,
          let type = a[.type] as? FileAttributeType else { return "??? (missing)" }
    let v = n.intValue
    var s = (type == .typeDirectory) ? "d" : "-"
    let bits: [(Int, String)] = [(0o400, "r"), (0o200, "w"), (0o100, "x"),
                                 (0o040, "r"), (0o020, "w"), (0o010, "x"),
                                 (0o004, "r"), (0o002, "w"), (0o001, "x")]
    for (mask, ch) in bits { s += (v & mask) != 0 ? ch : "-" }
    return s
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

func wipe(_ path: String) {
    try? FileManager.default.removeItem(atPath: path)
}

print("HARNESS: headless on-disk test of AppSettings (production type)")
print("production source : \(REF)")
print("scratch root      : \(ROOT.path)")
print("real user dir     : NEVER touched (every instance gets an injected temp dir)")
print("swift             : \(#file)")

// ---------------------------------------------------------------------------
// T1: fresh temp dir that does NOT exist yet -> created by production code
// ---------------------------------------------------------------------------
section("T1  fresh directory (created by the production code)")

let t1 = "/tmp/ai_store_test/t1_fresh"
wipe(t1)   // make the parent dir not exist so aiStoreURL must create it
evidence("ai.json before anything", exists(t1 + "/ai.json") ? "present" : "absent")
evidence("store dir before anything", exists(t1) ? "present, mode \(mode(t1))" : "absent")

// (a) fresh instance, empty dir
let a1 = AppSettings(storeDirectory: URL(fileURLWithPath: t1, isDirectory: true))
evidence("a1.aiBase", "\"\(a1.aiBase)\"")
evidence("a1.aiKey", "\"\(a1.aiKey)\"")
evidence("a1.aiModel", "\"\(a1.aiModel)\"")
evidence("a1.aiStatus", "\"\(a1.aiStatus)\"")
evidence("a1.aiConfigured", "\(a1.aiConfigured)")
evidence("a1.aiDirty", "\(a1.aiDirty)")
check("a", "loadAI() on empty dir does not crash; defaults sane (all empty, aiConfigured=false)",
      a1.aiBase.isEmpty && a1.aiKey.isEmpty && a1.aiModel.isEmpty && !a1.aiConfigured,
      "base=\"\" key=\"\" model=\"\" configured=false dirty=\(a1.aiDirty) (survived init/loadAI)")

// (b) set + save
a1.aiBase = "https://api.example.invalid/v1"
a1.aiKey = "sk-TEST-ONLY-1234"
a1.aiModel = "test-model-1"
let ok1 = a1.saveAI()
evidence("saveAI() return", "\(ok1)")
evidence("aiStatus after save", "\"\(a1.aiStatus)\"")
check("b", "saveAI() returns true for base/key/model", ok1 == true,
      "saveAI() == \(ok1); aiStatus=\"\(a1.aiStatus)\"")

// (c) on-disk facts
section("T1c  on-disk facts after first save")
let f1 = t1 + "/ai.json"
let json1 = rawFile(f1)
evidence("ai.json exists", "\(exists(f1))")
evidence("ai.json mode (%03o)", mode(f1))
evidence("ai.json symlink-style", modeSymbolic(f1))
evidence("parent dir mode (%03o)", mode(t1))
evidence("parent dir symlink-style", modeSymbolic(t1))
evidence("ai.json raw bytes", "\n----8<----\n\(json1)\n---->8----")
func sizeOf(_ path: String) -> String {
    guard let a = try? FileManager.default.attributesOfItem(atPath: path),
          let n = a[.size] as? NSNumber else { return "?" }
    return "\(n.intValue) bytes"
}
evidence("ai.json size", sizeOf(f1))
evidence("aiStoreURL", a1.aiStoreURL.path)
evidence("aiStoreURL == <tempdir>/ai.json", "\(a1.aiStoreURL.path == f1)")

// Parse the ordered keys out of the raw JSON text.
func orderedKeys(_ text: String) -> [String] {
    var keys: [String] = []
    for line in text.components(separatedBy: "\n") {
        let t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("\""), let r = t.range(of: "\"", options: [], range: t.index(after: t.startIndex)..<t.endIndex) {
            let k = String(t[t.index(after: t.startIndex)..<r.lowerBound])
            if t.hasSuffix(":") || t.contains("\" :") || t.contains("\":") { keys.append(k) }
        }
    }
    return keys
}
let keys = orderedKeys(json1)
evidence("key order in file", "\(keys)")
let parsed = (try? JSONSerialization.jsonObject(with: Data(json1.utf8))) as? [String: Any] ?? [:]
evidence("parsed base", "\"\(parsed["base"] ?? "<nil>")\"")
evidence("parsed key", "\"\(parsed["key"] ?? "<nil>")\"")
evidence("parsed model", "\"\(parsed["model"] ?? "<nil>")\"")
// ⚠️ 2026-10-15 修：旧断言写的是 `keys == ["base","key","model"]`，
//    那是第 70 条（searchTool/responsesPath）与第 82 条（thinking/maxTokens/protocol/webSearch）
//    之前的形状 —— 之后 `saveAI()` 会写 9 个字段，这条断言就一直是红的。
//    现在的口径：前三个键的顺序不变；9 个键都在；**没存过方案时不许出现 `profiles` 键**
//    （第 82 条的硬保证：没用过方案的编辑，ai.json 与从前逐字节相同）。
let expectedKeys = ["base", "key", "model", "thinking", "maxTokens",
                    "protocol", "webSearch", "searchTool", "responsesPath"]
// ⚠️ 落盘的键是**按键名排序**的（`writeRaw` 走 JSONSerialization 的排序输出 → 文件逐字节可复现，
//    便于比对与 diff）。所以断言写成「等于排序后的清单」，而不是写入顺序。
//    没有 `profiles` 键这件事由 9 == 9 的集合相等蕴含（第 82 条的保证）。
let cOk = exists(f1) && mode(f1) == "600" && mode(t1) == "700"
    && keys == expectedKeys.sorted()
    && Set(keys) == Set(expectedKeys)
    && (parsed["base"] as? String) == "https://api.example.invalid/v1"
    && (parsed["key"] as? String) == "sk-TEST-ONLY-1234"
    && (parsed["model"] as? String) == "test-model-1"
check("c", "ai.json exists; file 0600; parent dir 0700; ordered keys = 9 字段（无 profiles）; exact values",
      cOk, "exists=\(exists(f1)) fileMode=\(mode(f1)) dirMode=\(mode(t1)) keys=\(keys) values ok=\((parsed["base"] as? String) == "https://api.example.invalid/v1" && (parsed["key"] as? String) == "sk-TEST-ONLY-1234" && (parsed["model"] as? String) == "test-model-1")")

// (d) brand new instance, same dir
let d1 = AppSettings(storeDirectory: URL(fileURLWithPath: t1, isDirectory: true))
evidence("d1.aiBase", "\"\(d1.aiBase)\"")
evidence("d1.aiKey", "\"\(d1.aiKey)\"")
evidence("d1.aiModel", "\"\(d1.aiModel)\"")
evidence("d1.aiConfigured", "\(d1.aiConfigured)")
evidence("d1.aiDirty (fresh load should be false)", "\(d1.aiDirty)")
check("d", "brand-new instance reads back exactly the saved values; aiConfigured == true",
      d1.aiBase == "https://api.example.invalid/v1" && d1.aiKey == "sk-TEST-ONLY-1234"
        && d1.aiModel == "test-model-1" && d1.aiConfigured && !d1.aiDirty,
      "base=\"\(d1.aiBase)\" key=\"\(d1.aiKey)\" model=\"\(d1.aiModel)\" configured=\(d1.aiConfigured) dirty=\(d1.aiDirty)")

// (h) mode survives a second save; and 0644 -> 0600 tightening
section("T1h  second save + pre-existing loose permissions")
d1.aiModel = "test-model-2"
let ok2 = d1.saveAI()
evidence("2nd saveAI() return", "\(ok2)")
evidence("mode after 2nd save", mode(f1))
check("h1", "mode 0600 still 0600 after a SECOND saveAI() (atomic rewrite)",
      ok2 && mode(f1) == "600", "saveAI()=\(ok2) mode=\(mode(f1))")

let beforeLoosen = mode(f1)
try? FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: f1)
evidence("mode manually loosened to", "\(beforeLoosen) -> \(mode(f1))")
d1.aiModel = "test-model-3"
let ok3 = d1.saveAI()
evidence("3rd saveAI() return", "\(ok3)")
evidence("mode after save on 0644 file", mode(f1))
check("h2", "pre-existing 0644 ai.json gets tightened back to 0600 by saveAI()",
      mode(f1) == "600", "0644 --saveAI()--> \(mode(f1))")

// extra: pre-existing LOOSE DIRECTORY (0755) - does aiStoreURL tighten it?
let t3 = "/tmp/ai_store_test/t3_loose_dir"
wipe(t3)
mkdir(t3, 0o755)
writeRaw(t3 + "/ai.json", "{\"base\":\"https://loose.example/v1\",\"key\":\"sk-LOOSE-0644\",\"model\":\"m\"}", perm: 0o644)
evidence("pre-existing dir mode", mode(t3))
evidence("pre-existing ai.json mode", mode(t3 + "/ai.json"))
let l1 = AppSettings(storeDirectory: URL(fileURLWithPath: t3, isDirectory: true))
evidence("loaded from loose dir", "base=\"\(l1.aiBase)\" key=\"\(l1.aiKey)\" model=\"\(l1.aiModel)\"")
evidence("dir mode after init (aiStoreURL ran)", mode(t3))
l1.aiModel = "m2"
_ = l1.saveAI()
evidence("dir mode after saveAI()", mode(t3))
evidence("file mode after saveAI()", mode(t3 + "/ai.json"))
let looseDirTightened = mode(t3) == "700"
print("NOTE  pre-existing 0755 directory tightened to 0700? \(looseDirTightened ? "YES" : "NO (stays \(mode(t3)))") ")

// (e) trimming
section("T1e  whitespace trimming")
let e1 = AppSettings(storeDirectory: URL(fileURLWithPath: t1, isDirectory: true))
e1.aiBase = "  https://trim.example/v1  \n"
e1.aiKey = "\tsk-TRIM-5678 \n"
e1.aiModel = "  trim-model\n\n"
evidence("in-memory before save", "base=\(e1.aiBase.debugDescription) key=\(e1.aiKey.debugDescription) model=\(e1.aiModel.debugDescription)")
let okE = e1.saveAI()
let jsonE = rawFile(f1)
let parsedE = (try? JSONSerialization.jsonObject(with: Data(jsonE.utf8))) as? [String: Any] ?? [:]
evidence("saveAI() return", "\(okE)")
evidence("on-disk raw", "\n----8<----\n\(jsonE)\n---->8----")
evidence("on-disk base", "\((parsedE["base"] as? String).debugDescription)")
evidence("on-disk key", "\((parsedE["key"] as? String).debugDescription)")
evidence("on-disk model", "\((parsedE["model"] as? String).debugDescription)")
evidence("aiStatus", "\"\(e1.aiStatus)\"")
evidence("inner whitespace preserved? (\"a b\" style)", "tested separately below")
let e2 = AppSettings(storeDirectory: URL(fileURLWithPath: t1, isDirectory: true))
evidence("read back into new instance", "base=\(e2.aiBase.debugDescription) key=\(e2.aiKey.debugDescription) model=\(e2.aiModel.debugDescription)")
evidence("aiDirty after reload", "\(e2.aiDirty)")
// inner whitespace check
let e3 = AppSettings(storeDirectory: URL(fileURLWithPath: t1, isDirectory: true))
e3.aiModel = "  my model with spaces  "
_ = e3.saveAI()
let parsedInner = (try? JSONSerialization.jsonObject(with: Data(rawFile(f1).utf8))) as? [String: Any] ?? [:]
evidence("inner spaces kept?", "model=\((parsedInner["model"] as? String).debugDescription)")
check("e", "leading/trailing whitespace AND trailing newline trimmed on save (JSON holds trimmed values)",
      (parsedE["base"] as? String) == "https://trim.example/v1"
        && (parsedE["key"] as? String) == "sk-TRIM-5678"
        && (parsedE["model"] as? String) == "trim-model"
        && (parsedInner["model"] as? String) == "my model with spaces",
      "base=\"https://trim.example/v1\" key=\"sk-TRIM-5678\" model=\"trim-model\"; inner spaces kept: \"my model with spaces\"")

// (f) clearAI
section("T1f  clearAI()")
let f1before = exists(f1)
let jsonBeforeClear = rawFile(f1)      // 「盘上那份」——clearAI 之后必须逐字节还是它
let f2 = AppSettings(storeDirectory: URL(fileURLWithPath: t1, isDirectory: true))
f2.clearAI()
evidence("ai.json existed before clearAI()", "\(f1before)")
evidence("ai.json exists after clearAI()", "\(exists(f1))")
evidence("ai.json raw after clearAI()", "\n----8<----\n\(rawFile(f1))\n---->8----")
evidence("ai.json mode after clearAI()", mode(f1))
evidence("in-memory after clearAI()", "base=\"\(f2.aiBase)\" key=\"\(f2.aiKey)\" model=\"\(f2.aiModel)\"")
evidence("aiConfigured after clearAI()", "\(f2.aiConfigured)")
evidence("aiDirty after clearAI()", "\(f2.aiDirty)")
evidence("aiStatus after clearAI()", "\"\(f2.aiStatus)\"")
let f3 = AppSettings(storeDirectory: URL(fileURLWithPath: t1, isDirectory: true))
evidence("reload after clearAI()", "base=\"\(f3.aiBase)\" key=\"\(f3.aiKey)\" model=\"\(f3.aiModel)\" configured=\(f3.aiConfigured)")
// ⚠️ 2026-10-15 修：旧断言要求 clearAI() 把**盘上**的 base 写成空串 —— 那是旧语义。
//    现在的口径（代码注释里写明、界面状态行也这么说）：
//    「清空」= **在界面上**恢复出厂；**盘上那份没动，要覆盖请点「保存」**。
//    所以这里改成验「文件逐字节未变 + 内存已清空」。
let clearKeepsFile = exists(f1)
let clearBytesUnchanged = rawFile(f1) == jsonBeforeClear
// 新语义的两个方向都要钉住：
//   · 界面上确实清了（`f2.aiConfigured == false`）；
//   · 盘上那份**没动** → 新开一个实例重新加载，照样是「已配置」（`f3.aiConfigured == true`），
//     这正是状态行那句「磁盘上已保存的配置没动，要覆盖请点保存」的含义。
check("f", "clearAI() = 只清界面（文件逐字节未变）; 内存已清、重新加载仍是盘上那份",
      clearKeepsFile && clearBytesUnchanged && !f2.aiConfigured && f3.aiConfigured,
      "file still exists=\(clearKeepsFile); content=\(rawFile(f1).replacingOccurrences(of: "\n", with: " ")); configured=\(f2.aiConfigured); reload configured=\(f3.aiConfigured)")

// (g) robustness
section("T1g  corrupt / ill-typed ai.json")
let t2 = "/tmp/ai_store_test/t2_corrupt"
wipe(t2)
mkdir(t2, 0o700)
writeRaw(t2 + "/ai.json", "this is not json at all {{{", perm: 0o600)
evidence("corrupt file", "\"\(rawFile(t2 + "/ai.json"))\" mode \(mode(t2 + "/ai.json"))")
let g1 = AppSettings(storeDirectory: URL(fileURLWithPath: t2, isDirectory: true))
evidence("after init on corrupt file", "base=\"\(g1.aiBase)\" key=\"\(g1.aiKey)\" model=\"\(g1.aiModel)\" configured=\(g1.aiConfigured) dirty=\(g1.aiDirty)")
check("g-i", "corrupt/non-JSON ai.json: loadAI() survives (no crash), leaves empty defaults",
      g1.aiBase.isEmpty && g1.aiKey.isEmpty && g1.aiModel.isEmpty && !g1.aiConfigured,
      "no crash; base/key/model all \"\"; configured=false")
// can it still save over the corrupt file?
g1.aiBase = "https://after-corrupt.example/v1"
g1.aiKey = "sk-TEST-ONLY-1234"
g1.aiModel = "recovered"
let okG = g1.saveAI()
evidence("save over corrupt file", "saveAI()=\(okG) mode=\(mode(t2 + "/ai.json")) content=\(rawFile(t2 + "/ai.json").replacingOccurrences(of: "\n", with: " "))")
let g1b = AppSettings(storeDirectory: URL(fileURLWithPath: t2, isDirectory: true))
evidence("reload after recovery save", "base=\"\(g1b.aiBase)\" configured=\(g1b.aiConfigured)")
check("g-i-save", "corrupt file can be overwritten by saveAI() and read back",
      okG && g1b.aiBase == "https://after-corrupt.example/v1" && g1b.aiConfigured,
      "saveAI()=\(okG) reload base=\"\(g1b.aiBase)\"")

let t2b = "/tmp/ai_store_test/t2b_wrongtypes"
wipe(t2b)
mkdir(t2b, 0o700)
writeRaw(t2b + "/ai.json", "{\"base\": 5, \"key\": [1,2,3], \"model\": null}", perm: 0o600)
evidence("ill-typed file", "\(rawFile(t2b + "/ai.json"))")
let g2 = AppSettings(storeDirectory: URL(fileURLWithPath: t2b, isDirectory: true))
evidence("after init on {\"base\":5,...}", "base=\(g2.aiBase.debugDescription) key=\(g2.aiKey.debugDescription) model=\(g2.aiModel.debugDescription) configured=\(g2.aiConfigured)")
check("g-ii", "valid JSON with non-string values: loadAI() survives, non-strings become empty",
      g2.aiBase.isEmpty && g2.aiKey.isEmpty && g2.aiModel.isEmpty,
      "base=\(g2.aiBase.debugDescription) key=\(g2.aiKey.debugDescription) model=\(g2.aiModel.debugDescription) (no crash)")

// extra ill-typed shapes
let t2c = "/tmp/ai_store_test/t2c_more"
wipe(t2c); mkdir(t2c, 0o700)
let shapes: [(String, String)] = [
    ("top-level array", "[1,2,3]"),
    ("top-level string", "\"hello\""),
    ("empty object", "{}"),
    ("empty file", ""),
    ("base numeric-string", "{\"base\":\"5\"}"),
    ("trailing garbage", "{\"base\":\"x\"} trailing"),
]
for (name, body) in shapes {
    writeRaw(t2c + "/ai.json", body, perm: 0o600)
    let inst = AppSettings(storeDirectory: URL(fileURLWithPath: t2c, isDirectory: true))
    print("   [extra] \(name): survived; base=\(inst.aiBase.debugDescription) key=\(inst.aiKey.debugDescription) model=\(inst.aiModel.debugDescription) configured=\(inst.aiConfigured)")
}

// extra: saveAI with empty strings via clear path from a configured state (already covered)
// extra: what does ai.json look like when nothing was ever saved?
let t4 = "/tmp/ai_store_test/t4_neversaved"
wipe(t4); mkdir(t4, 0o700)
let n1 = AppSettings(storeDirectory: URL(fileURLWithPath: t4, isDirectory: true))
evidence("never-saved dir ai.json", exists(t4 + "/ai.json") ? "present" : "absent (init does not create it)")
evidence("dir mode for never-saved dir (0700 pre-made)", mode(t4))
let neverSavedNoFile = !exists(t4 + "/ai.json")
check("extra-1", "init()/loadAI() does NOT create ai.json when nothing was saved",
      neverSavedNoFile, "ai.json exists=\(exists(t4 + "/ai.json"))")

// extra: does loadAI() re-create/tighten dir? pre-existing dir 0755 with NO ai.json
let t5 = "/tmp/ai_store_test/t5_dir_only"
wipe(t5); mkdir(t5, 0o755)
let k1 = AppSettings(storeDirectory: URL(fileURLWithPath: t5, isDirectory: true))
evidence("pre-existing 0755 dir, no ai.json -> dir mode after init", "0755 -> \(mode(t5))")
print("NOTE  dir-only tightening: \(mode(t5) == "700" ? "tightened" : "NOT tightened (stays \(mode(t5)))") ")

// extra failure paths: unwritable store dir, and ai.json being a DIRECTORY
section("T1i  failure paths")
let t6 = "/dev/null/nope-not-a-dir"
let u1 = AppSettings(storeDirectory: URL(fileURLWithPath: t6, isDirectory: true))
evidence("init with unwritable store dir", "survived; base=\(u1.aiBase.debugDescription)")
let okU = u1.saveAI()
evidence("saveAI() into /dev/null/nope-not-a-dir", "returned \(okU); aiStatus=\"\(u1.aiStatus)\"")
check("extra-2", "saveAI() on an unwritable path returns false (no crash) and explains itself in aiStatus",
      okU == false && !u1.aiStatus.isEmpty, "saveAI()=\(okU) aiStatus=\"\(u1.aiStatus)\"")

let t7 = "/tmp/ai_store_test/t7_json_is_a_dir"
wipe(t7); mkdir(t7, 0o700)
mkdir(t7 + "/ai.json", 0o700)
let v1 = AppSettings(storeDirectory: URL(fileURLWithPath: t7, isDirectory: true))
evidence("ai.json is a directory -> init", "survived; base=\(v1.aiBase.debugDescription) configured=\(v1.aiConfigured)")
let okV = v1.saveAI()
evidence("saveAI() when ai.json is a directory", "returned \(okV); aiStatus=\"\(v1.aiStatus)\"")
check("extra-3", "ai.json-as-directory: loadAI() survives; saveAI() reports failure instead of crashing",
      v1.aiBase.isEmpty && okV == false && !v1.aiStatus.isEmpty,
      "load survived (base=\"\"); saveAI()=\(okV) aiStatus=\"\(v1.aiStatus)\"")

print("")
print("================================================================")
print("SUMMARY: \(passCount) PASS / \(failCount) FAIL")
print("================================================================")
exit(failCount == 0 ? 0 : 1)
