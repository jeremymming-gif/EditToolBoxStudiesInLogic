import Foundation

func mode(_ p: String) -> String {
    let a = try? FileManager.default.attributesOfItem(atPath: p)
    let n = (a?[.posixPermissions] as? NSNumber)?.intValue ?? -1
    return String(n, radix: 8)
}

let base = URL(fileURLWithPath: "/tmp/ai_store_test/preexist755")
try? FileManager.default.removeItem(at: base)
// 复现真实情况：目录由 EditorStore 先建成 0755
try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true,
                                        attributes: [.posixPermissions: 0o755])
print("[1] 预先存在的目录 0755 -> 实测 dir mode = \(mode(base.path))")
let s = AppSettings(storeDirectory: base)
print("[2] 实例化 AppSettings 后 dir mode = \(mode(base.path))  （期望 700）")
s.aiBase = "https://x.example.invalid/v1"
s.aiKey = "sk-DIRCHECK-1"
s.aiModel = "dircheck-model"
let ok = s.saveAI()
print("[3] saveAI()=\(ok)  file mode = \(mode(base.path + "/ai.json"))  （期望 600）")
let t = AppSettings(storeDirectory: base)
print("[4] 新实例读回 base=\(t.aiBase) model=\(t.aiModel) configured=\(t.aiConfigured)")
s.clearAI()
print("[5] clearAI 后 file mode = \(mode(base.path + "/ai.json"))  configured=\(t.aiConfigured)")
try? FileManager.default.removeItem(at: base)
print("[6] 清理 temp 目录完成")
