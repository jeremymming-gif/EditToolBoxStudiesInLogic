import Foundation
print("  #filePath = \(#filePath)")
print("  CWD       = \(FileManager.default.currentDirectoryPath)")
let here = URL(fileURLWithPath: #filePath)
print("  here      = \(here.path)")
print("  上三级     = \(here.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().path)")
