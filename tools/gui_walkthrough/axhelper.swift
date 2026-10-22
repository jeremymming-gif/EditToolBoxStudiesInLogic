// axhelper.swift —— gui_walkthrough 的 AX 原语（B-191 T1；公开仓 tools/gui_walkthrough/）
//
// 为什么是自己的 Swift 薄包装，而不是 pyobjc/atomac/cliclick：
//   · 本机**未安装** pyobjc / atomac / cliclick（2026-10-23 实测），装 = 联网；AXorcist 要 SwiftPM 拉依赖。
//   · ApplicationServices（AXUIElement）是 macOS 系统框架，`swiftc` 已在 `run_swift_tests.sh` 里天天用，
//     **零第三方运行时依赖、零联网**。
//   · 设计输入是本项目既有 AX recipes（B-188 报告 §3、B-190 临时 axdump/axtool），不是第三方代码拷贝。
//
// 设计要点（与 B-190 临时工具的区别 / 硬化）：
//   1) 只输出 **JSON**（键排序、确定性），解析交给 Python（可 hermetic 自测）。
//   2) 元素定位 = AX 路径 或 角色+名称（title/desc/value/help 任一）；**多命中一律拒绝执行**（除非显式 --nth），
//      绝不因索引漂移误点（B-190 曾 2 次按索引点错 alert 按钮）。
//   3) `press --expect-name`：按下前复核该路径上的元素名字仍匹配 —— 防「快照→点击」之间界面漂移。
//   4) `alerts` 只**报告** alert 候选（sheet/dialog + 文本 + 按钮名），匹配与断言在 Python（name-based）。
//   5) 退出码与 Python 侧一致：0 成功；1 未找到/动作失败；2 用法；3 缺辅助功能权限；4 环境（无此进程）。
//
// 编译（由 gui_walkthrough.py 自动做，产物落 /tmp 缓存、不入库）：
//   swiftc -O -swift-version 5 -o /tmp/gui-walkthrough-helper/axhelper axhelper.swift
//
// 用法：
//   axhelper trusted
//   axhelper dump    --pid P [--max-depth 8] [--role AXStaticText] [--contains 文本] [--path 0.1]
//   axhelper resolve --pid P [--path P | --role AXButton --name 推送…] [--nth 0] [--exact] [--max-depth 30]
//   axhelper press   --pid P <locator> [--expect-name 文本]
//   axhelper select  --pid P <locator> [--focus] [--selected] [--fallback-press]
//   axhelper setvalue --pid P <locator> --value 文本
//   axhelper scroll  --pid P <locator-for-area> (--value 0.5 | --delta 0.25)
//   axhelper alerts  --pid P [--max-depth 8]
//   axhelper window  --pid P [--title 文本]
//   axhelper texts   --pid P [--role AXStaticText] [--contains 文本] [--max-depth 30] [--limit 5000]
//
// `<locator>` = `--path 0.6.3` 或 `--role AXButton --name 确认回退`（外加 --title/--desc/--value/--help-text/
//               --within/--nth/--exact/--max-depth）。

import ApplicationServices
import Foundation

// MARK: - 输出

func emitJSON(_ obj: [String: Any], _ code: Int32) -> Never {
    let data: Data
    if let d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) {
        data = d
    } else {
        data = Data("{\"ok\":false,\"error\":\"encode\"}".utf8)
    }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
    exit(code)
}

func ok(_ extra: [String: Any] = [:]) -> Never {
    var d: [String: Any] = ["ok": true]
    for (k, v) in extra { d[k] = v }
    emitJSON(d, 0)
}

func fail(_ error: String, _ message: String, _ code: Int32, extra: [String: Any] = [:]) -> Never {
    var d: [String: Any] = ["ok": false, "error": error, "message": message]
    for (k, v) in extra { d[k] = v }
    emitJSON(d, code)
}

// MARK: - AX 属性

func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
    var v: CFTypeRef?
    guard AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success else { return nil }
    return v
}

func strAttr(_ el: AXUIElement, _ name: String) -> String {
    guard let v = attr(el, name) else { return "" }
    if let s = v as? String { return s }
    if let n = v as? NSNumber { return n.stringValue }
    return ""
}

func boolAttr(_ el: AXUIElement, _ name: String) -> Bool? {
    guard let v = attr(el, name), let n = v as? NSNumber else { return nil }
    return n.boolValue
}

func numAttr(_ el: AXUIElement, _ name: String) -> Double? {
    guard let v = attr(el, name), let n = v as? NSNumber else { return nil }
    return n.doubleValue
}

func kids(_ el: AXUIElement) -> [AXUIElement] {
    if let a = attr(el, kAXChildrenAttribute) as? [AXUIElement] { return a }
    return []
}

func clip(_ s: String, _ max: Int = 2000) -> String {
    if s.count <= max { return s }
    return String(s.prefix(max)) + "…[+\(s.count - max)]"
}

func role(_ el: AXUIElement) -> String { strAttr(el, kAXRoleAttribute) }

/// 名字口径：title / description / value / help 任一（SwiftUI 把按钮文字放 desc，AppKit 放 title）。
func names(_ el: AXUIElement) -> [String] {
    [strAttr(el, kAXTitleAttribute), strAttr(el, kAXDescriptionAttribute),
     strAttr(el, kAXValueAttribute), strAttr(el, kAXHelpAttribute)]
}

func primaryName(_ el: AXUIElement) -> String {
    for s in names(el) where !s.isEmpty { return s }
    return ""
}

func elementInfo(_ el: AXUIElement, path: String, depth: Int) -> [String: Any] {
    var d: [String: Any] = [
        "path": path,
        "depth": depth,
        "role": role(el),
    ]
    let sub = strAttr(el, kAXSubroleAttribute)
    if !sub.isEmpty { d["subrole"] = sub }
    let title = strAttr(el, kAXTitleAttribute)
    if !title.isEmpty { d["title"] = clip(title) }
    let desc = strAttr(el, kAXDescriptionAttribute)
    if !desc.isEmpty { d["desc"] = clip(desc) }
    let value = strAttr(el, kAXValueAttribute)
    if !value.isEmpty { d["value"] = clip(value) }
    let help = strAttr(el, kAXHelpAttribute)
    if !help.isEmpty { d["help"] = clip(help) }
    if let b = boolAttr(el, kAXSelectedAttribute) { d["selected"] = b }
    if let b = boolAttr(el, kAXFocusedAttribute) { d["focused"] = b }
    if let b = boolAttr(el, kAXEnabledAttribute) { d["enabled"] = b }
    return d
}

func frame(_ el: AXUIElement) -> [String: Any]? {
    guard let p = attr(el, kAXPositionAttribute), let s = attr(el, kAXSizeAttribute),
          CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
    var pt = CGPoint.zero
    var sz = CGSize.zero
    guard AXValueGetValue(p as! AXValue, .cgPoint, &pt) else { return nil }
    guard AXValueGetValue(s as! AXValue, .cgSize, &sz) else { return nil }
    return ["x": Double(pt.x), "y": Double(pt.y), "w": Double(sz.width), "h": Double(sz.height)]
}

// MARK: - 定位

struct Locator {
    var path = ""
    var role = ""
    var title = ""
    var name = ""
    var desc = ""
    var value = ""
    var helpText = ""
    var within = ""
    var nth = -1
    var exact = false
    var subtree = false
    var maxDepth = 30
    var pid: pid_t = 0
}

func parseUInt(_ args: [String], _ i: inout Int, _ key: String) -> String {
    if i + 1 < args.count {
        i += 2
        return args[i - 1]
    }
    fail("usage", "缺参数值：\(key)", 2)
}

func parseLocator(_ args: [String], _ start: Int, _ loc_in: inout Locator) -> Int {
    var i = start
    while i < args.count {
        switch args[i] {
        case "--pid": loc_in.pid = pid_t(parseUInt(args, &i, "--pid")) ?? 0
        case "--path": loc_in.path = parseUInt(args, &i, "--path")
        case "--role": loc_in.role = parseUInt(args, &i, "--role")
        case "--title": loc_in.title = parseUInt(args, &i, "--title")
        case "--name": loc_in.name = parseUInt(args, &i, "--name")
        case "--desc": loc_in.desc = parseUInt(args, &i, "--desc")
        case "--value": loc_in.value = parseUInt(args, &i, "--value")
        case "--help-text": loc_in.helpText = parseUInt(args, &i, "--help-text")
        case "--within": loc_in.within = parseUInt(args, &i, "--within")
        case "--nth": loc_in.nth = Int(parseUInt(args, &i, "--nth")) ?? -1
        case "--max-depth": loc_in.maxDepth = Int(parseUInt(args, &i, "--max-depth")) ?? 30
        case "--contains": loc_in.name = parseUInt(args, &i, "--contains")
        case "--exact": loc_in.exact = true; i += 1
        case "--subtree": loc_in.subtree = true; i += 1
        default: i += 1
        }
    }
    return i
}

func roleMatches(_ el: AXUIElement, _ want: String) -> Bool {
    if want.isEmpty { return true }
    let r = role(el)
    if want.hasPrefix("AX") { return r.caseInsensitiveCompare(want) == .orderedSame }
    return r.lowercased().contains(want.lowercased())
}

func fieldMatches(_ actual: String, _ want: String, _ exact: Bool) -> Bool {
    if want.isEmpty { return true }
    if exact { return actual == want }
    return actual.range(of: want, options: [.caseInsensitive, .diacriticInsensitive]) != nil
}

func nameMatches(_ el: AXUIElement, _ want: String, _ exact: Bool) -> Bool {
    if want.isEmpty { return true }
    for f in names(el) where fieldMatches(f, want, exact) { return true }
    return false
}

/// 子树里（深度 ≤ depth）有没有某个后代的名字匹配？用于 `--subtree`（行/单元格场景）。
func descendantNameMatches(_ el: AXUIElement, _ want: String, _ exact: Bool, _ depth: Int = 2) -> Bool {
    if depth <= 0 { return false }
    for c in kids(el) {
        if nameMatches(c, want, exact) { return true }
        if descendantNameMatches(c, want, exact, depth - 1) { return true }
    }
    return false
}

func matches(_ el: AXUIElement, _ loc: Locator) -> Bool {
    if !roleMatches(el, loc.role) { return false }
    if !fieldMatches(strAttr(el, kAXTitleAttribute), loc.title, loc.exact) { return false }
    if !fieldMatches(strAttr(el, kAXDescriptionAttribute), loc.desc, loc.exact) { return false }
    if !fieldMatches(strAttr(el, kAXValueAttribute), loc.value, loc.exact) { return false }
    if !fieldMatches(strAttr(el, kAXHelpAttribute), loc.helpText, loc.exact) { return false }
    if !nameMatches(el, loc.name, loc.exact) {
        if !(loc.subtree && descendantNameMatches(el, loc.name, loc.exact)) { return false }
    }
    return true
}

func resolvePath(_ root: AXUIElement, _ path: String) -> AXUIElement? {
    if path.isEmpty || path == "-" { return root }
    var cur = root
    for comp in path.split(separator: ".") {
        guard let idx = Int(comp), idx >= 0 else { return nil }
        let cs = kids(cur)
        guard idx < cs.count else { return nil }
        cur = cs[idx]
    }
    return cur
}

func search(_ root: AXUIElement, _ loc: Locator, prefix: String = "") -> [(String, AXUIElement)] {
    var out: [(String, AXUIElement)] = []
    func walk(_ el: AXUIElement, _ path: String, _ depth: Int) {
        if depth > loc.maxDepth { return }
        if matches(el, loc) { out.append((path, el)) }
        for (i, c) in kids(el).enumerated() {
            walk(c, path.isEmpty ? "\(i)" : "\(path).\(i)", depth + 1)
        }
    }
    walk(root, "", 0)
    if prefix.isEmpty { return out }
    return out.map { (p: String, el: AXUIElement) -> (String, AXUIElement) in
        (p.isEmpty ? prefix : prefix + "." + p, el)
    }
}

/// 定位唯一目标：--path 直接解析；否则按 locator 搜索；0 命中 → not_found(1)；多命中且无 --nth → ambiguous(1)。
func pickOne(_ app: AXUIElement, _ loc: Locator) -> (String, AXUIElement) {
    if !loc.path.isEmpty {
        if let el = resolvePath(app, loc.path) { return (loc.path, el) }
        fail("not_found", "AX 路径不存在：\(loc.path)", 1, extra: ["path": loc.path])
    }
    var root = app
    var prefix = ""
    if !loc.within.isEmpty {
        guard let w = resolvePath(app, loc.within) else {
            fail("not_found", "within 路径不存在：\(loc.within)", 1, extra: ["path": loc.within])
        }
        root = w
        prefix = loc.within
    }
    let found = search(root, loc, prefix: prefix)
    if found.isEmpty {
        fail("not_found", "未找到匹配元素（role=\(loc.role) name=\(loc.name) title=\(loc.title)）", 1)
    }
    if loc.nth >= 0 {
        if loc.nth >= found.count {
            fail("not_found", "--nth \(loc.nth) 越界：仅 \(found.count) 个命中", 1, extra: ["count": found.count])
        }
        return found[loc.nth]
    }
    if found.count > 1 {
        let sample = found.prefix(8).map { ["path": $0.0, "role": role($0.1), "name": primaryName($0.1)] }
        fail("ambiguous", "命中 \(found.count) 个元素，拒绝盲目点击；请加 --nth 或更精确的 --name/--role",
             1, extra: ["count": found.count, "matches": sample])
    }
    return found[0]
}

// MARK: - 进程

func appFor(_ pid: pid_t) -> AXUIElement {
    if pid <= 0 { fail("usage", "缺 --pid 或 pid 非法", 2) }
    if kill(pid, 0) != 0 { fail("no_process", "没有该进程（pid=\(pid)）；App 是否已启动？", 4) }
    return AXUIElementCreateApplication(pid)
}

// MARK: - 树遍历 / alert / 窗口

func collect(_ root: AXUIElement, _ maxDepth: Int) -> [(String, AXUIElement)] {
    var out: [(String, AXUIElement)] = []
    func walk(_ el: AXUIElement, _ path: String, _ depth: Int) {
        out.append((path, el))
        if depth >= maxDepth { return }
        for (i, c) in kids(el).enumerated() {
            walk(c, path.isEmpty ? "\(i)" : "\(path).\(i)", depth + 1)
        }
    }
    walk(root, "", 0)
    return out
}

func isAlertRoot(_ el: AXUIElement) -> Bool {
    let r = role(el)
    let sub = strAttr(el, kAXSubroleAttribute)
    if r == "AXSheet" || r == "AXDialog" || r == "AXSystemDialog" { return true }
    if sub == "AXDialog" || sub == "AXSystemDialog" { return true }
    return false
}

func textsIn(_ el: AXUIElement, _ maxDepth: Int = 4) -> [String] {
    var out: [String] = []
    func walk(_ e: AXUIElement, _ depth: Int) {
        if role(e) == "AXStaticText" {
            let v = strAttr(e, kAXValueAttribute)
            let t = strAttr(e, kAXTitleAttribute)
            let s = v.isEmpty ? t : v
            if !s.isEmpty { out.append(clip(s)) }
        }
        if depth >= maxDepth { return }
        for c in kids(e) { walk(c, depth + 1) }
    }
    walk(el, 0)
    return out
}

func buttonsIn(_ el: AXUIElement, _ maxDepth: Int = 4, prefix: String = "") -> [[String: Any]] {
    var out: [[String: Any]] = []
    func walk(_ e: AXUIElement, _ path: String, _ depth: Int) {
        if role(e) == "AXButton" {
            let full = prefix.isEmpty ? path : (path.isEmpty ? prefix : prefix + "." + path)
            var d: [String: Any] = ["path": full, "role": "AXButton", "name": primaryName(e)]
            let t = strAttr(e, kAXTitleAttribute)
            if !t.isEmpty { d["title"] = clip(t, 200) }
            let de = strAttr(e, kAXDescriptionAttribute)
            if !de.isEmpty { d["desc"] = clip(de, 200) }
            let v = strAttr(e, kAXValueAttribute)
            if !v.isEmpty { d["value"] = clip(v, 200) }
            if let b = boolAttr(e, kAXEnabledAttribute) { d["enabled"] = b }
            out.append(d)
        }
        if depth >= maxDepth { return }
        for (i, c) in kids(e).enumerated() {
            walk(c, path.isEmpty ? "\(i)" : "\(path).\(i)", depth + 1)
        }
    }
    walk(el, "", 0)
    return out
}

func alertsPayload(_ app: AXUIElement, _ maxDepth: Int) -> [[String: Any]] {
    var alerts: [[String: Any]] = []
    for (path, el) in collect(app, maxDepth) where isAlertRoot(el) {
        var d: [String: Any] = [
            "path": path,
            "role": role(el),
            "texts": textsIn(el),
            "buttons": buttonsIn(el, 4, prefix: path),
        ]
        let sub = strAttr(el, kAXSubroleAttribute)
        if !sub.isEmpty { d["subrole"] = sub }
        let title = strAttr(el, kAXTitleAttribute)
        if !title.isEmpty { d["title"] = clip(title, 400) }
        let desc = strAttr(el, kAXDescriptionAttribute)
        if !desc.isEmpty { d["desc"] = clip(desc, 400) }
        alerts.append(d)
    }
    return alerts
}

// MARK: - 入口

let argv = CommandLine.arguments
guard argv.count >= 2 else {
    fail("usage", "usage: axhelper <trusted|dump|resolve|press|select|setvalue|scroll|alerts|window|texts> …", 2)
}
let command = argv[1]

if command == "trusted" {
    ok(["trusted": AXIsProcessTrusted(), "helper_pid": Int(getpid()), "platform": "macos"])
}

let known = ["dump", "resolve", "press", "select", "setvalue", "scroll", "alerts", "window", "texts"]
guard known.contains(command) else {
    fail("usage", "未知命令：\(command)", 2)
}

func argValue(_ key: String) -> String {
    if let i = argv.firstIndex(of: key), i + 1 < argv.count { return argv[i + 1] }
    return ""
}
func hasFlag(_ key: String) -> Bool { argv.contains(key) }

var loc = Locator()
_ = parseLocator(argv, 2, &loc)
let app = appFor(loc.pid)

switch command {
case "dump":
    let root = loc.path.isEmpty ? app : (resolvePath(app, loc.path) ?? app)
    let base = loc.path
    var elements: [[String: Any]] = []
    for (path, el) in collect(root, loc.maxDepth) {
        if !roleMatches(el, loc.role) { continue }
        if !nameMatches(el, loc.name, loc.exact) {
            if !(loc.subtree && descendantNameMatches(el, loc.name, loc.exact)) { continue }
        }
        let full = base.isEmpty ? path : (path.isEmpty ? base : base + "." + path)
        elements.append(elementInfo(el, path: full, depth: full.isEmpty ? 0 : full.split(separator: ".").count))
    }
    ok(["elements": elements, "count": elements.count])

case "resolve":
    if !loc.path.isEmpty {
        let picked = pickOne(app, loc)
        ok(["matches": [elementInfo(picked.1, path: picked.0, depth: picked.0.split(separator: ".").count)],
            "count": 1])
    }
    var root = app
    var prefix = ""
    if !loc.within.isEmpty {
        guard let w = resolvePath(app, loc.within) else {
            fail("not_found", "within 路径不存在：\(loc.within)", 1)
        }
        root = w
        prefix = loc.within
    }
    let found = search(root, loc, prefix: prefix)
    if found.isEmpty {
        fail("not_found", "未找到匹配元素（role=\(loc.role) name=\(loc.name)）", 1, extra: ["count": 0])
    }
    var listed: [[String: Any]] = []
    for (p, el) in found.prefix(50) {
        listed.append(elementInfo(el, path: p, depth: p.split(separator: ".").count))
    }
    ok(["matches": listed, "count": found.count])

case "press":
    let picked = pickOne(app, loc)
    let expect = argValue("--expect-name")
    if !expect.isEmpty {
        let okName = nameMatches(picked.1, expect, true) || nameMatches(picked.1, expect, false)
        if !okName {
            fail("drift", "按钮漂移：路径 \(picked.0) 上的元素名不再是「\(expect)」（实际「\(primaryName(picked.1))」）；拒绝点击",
                 1, extra: ["path": picked.0, "actual": primaryName(picked.1)])
        }
    }
    let err = AXUIElementPerformAction(picked.1, kAXPressAction as CFString)
    if err != .success {
        fail("press_failed", "AXPress 失败：\(err.rawValue)", 1,
             extra: ["path": picked.0, "ax_error": Int(err.rawValue)])
    }
    ok(["path": picked.0, "role": role(picked.1), "name": primaryName(picked.1), "action": "AXPress"])

case "select":
    let picked = pickOne(app, loc)
    let explicitFocus = hasFlag("--focus")
    let explicitSelected = hasFlag("--selected")
    let wantFocus = explicitFocus || !explicitSelected
    let wantSelected = explicitSelected || !explicitFocus
    var result: [String: Any] = ["path": picked.0, "role": role(picked.1), "name": primaryName(picked.1)]
    if wantFocus {
        let e = AXUIElementSetAttributeValue(picked.1, kAXFocusedAttribute as CFString, kCFBooleanTrue as CFTypeRef)
        result["focus_set"] = (e == .success)
    }
    if wantSelected {
        let e = AXUIElementSetAttributeValue(picked.1, kAXSelectedAttribute as CFString, kCFBooleanTrue as CFTypeRef)
        result["selected_set"] = (e == .success)
    }
    if hasFlag("--fallback-press") {
        let focusedOK = (result["focus_set"] as? Bool) ?? true
        let selectedOK = (result["selected_set"] as? Bool) ?? true
        if !(focusedOK && selectedOK) {
            let e = AXUIElementPerformAction(picked.1, kAXPressAction as CFString)
            result["fallback_press"] = (e == .success)
        }
    }
    // verified 语义：显式要什么就验什么；都没显式（默认）时「selected 或 focused 至少一个生效」即算成功
    // （AXRow/AXOutlineRow 只支持 selected、不支持 focused —— B-191 真点实测）
    let focusOK = boolAttr(picked.1, kAXFocusedAttribute) == true
    let selectedOK = boolAttr(picked.1, kAXSelectedAttribute) == true
    var verified: Bool
    if explicitFocus && explicitSelected { verified = focusOK && selectedOK }
    else if explicitFocus { verified = focusOK }
    else if explicitSelected { verified = selectedOK }
    else { verified = focusOK || selectedOK }
    result["verified"] = verified
    result["focus_ok"] = focusOK
    result["selected_ok"] = selectedOK
    if let b = boolAttr(picked.1, kAXFocusedAttribute) { result["focused"] = b }
    if let b = boolAttr(picked.1, kAXSelectedAttribute) { result["selected"] = b }
    ok(result)

case "setvalue":
    let picked = pickOne(app, loc)
    let value = argValue("--value")
    if value.isEmpty && !hasFlag("--value") { fail("usage", "缺 --value（空字符串请显式 --value \"\"）", 2) }
    let e = AXUIElementSetAttributeValue(picked.1, kAXValueAttribute as CFString, value as CFString)
    if e != .success {
        fail("set_failed", "AXValue 写入失败：\(e.rawValue)", 1,
             extra: ["path": picked.0, "ax_error": Int(e.rawValue)])
    }
    let readback = strAttr(picked.1, kAXValueAttribute)
    ok(["path": picked.0, "role": role(picked.1), "requested": clip(value, 400),
        "readback": clip(readback, 400), "verified": readback == value])

case "scroll":
    let picked = pickOne(app, loc)
    var bars = search(picked.1, Locator(role: "AXScrollBar", maxDepth: 6))
    if bars.isEmpty {
        bars = search(app, Locator(role: "AXScrollBar", maxDepth: 10))
    }
    guard let bar = bars.first else {
        fail("no_scrollbar", "未找到 AXScrollBar（区域 \(picked.0)）", 1, extra: ["path": picked.0])
    }
    let before = numAttr(bar.1, kAXValueAttribute) ?? 0
    var target: Double
    if hasFlag("--delta") {
        target = before + (Double(argValue("--delta")) ?? 0)
    } else {
        guard hasFlag("--value") else { fail("usage", "缺 --value 0..1 或 --delta", 2) }
        target = Double(argValue("--value")) ?? before
    }
    target = min(max(target, 0.0), 1.0)
    let e = AXUIElementSetAttributeValue(bar.1, kAXValueAttribute as CFString, target as CFTypeRef)
    if e != .success {
        fail("scroll_failed", "scrollbar AXValue 写入失败：\(e.rawValue)", 1,
             extra: ["path": bar.0, "ax_error": Int(e.rawValue)])
    }
    let after = numAttr(bar.1, kAXValueAttribute) ?? -1
    ok(["area_path": picked.0, "scrollbar_path": bar.0, "before": before, "requested": target,
        "after": after, "verified": abs(after - target) < 0.02])

case "alerts":
    let alerts = alertsPayload(app, max(loc.maxDepth, 8))
    ok(["alerts": alerts, "count": alerts.count])

case "window":
    var wins = search(app, Locator(role: "AXWindow", maxDepth: 6))
    if !loc.name.isEmpty {
        wins = wins.filter { nameMatches($0.1, loc.name, false) }
    }
    if wins.isEmpty { fail("not_found", "未找到窗口（AXWindow）", 1) }
    var out: [[String: Any]] = []
    for (p, el) in wins {
        var d = elementInfo(el, path: p, depth: p.split(separator: ".").count)
        if let f = frame(el) { d["frame"] = f }
        out.append(d)
    }
    ok(["windows": out, "count": out.count])

case "texts":
    let limit = Int(argValue("--limit")) ?? 5000
    var entries: [[String: Any]] = []
    for (path, el) in collect(app, loc.maxDepth) {
        if !roleMatches(el, loc.role) { continue }
        if !nameMatches(el, loc.name, loc.exact) { continue }
        var fields: [String: String] = [:]
        let t = strAttr(el, kAXTitleAttribute)
        if !t.isEmpty { fields["title"] = clip(t) }
        let d = strAttr(el, kAXDescriptionAttribute)
        if !d.isEmpty { fields["desc"] = clip(d) }
        let v = strAttr(el, kAXValueAttribute)
        if !v.isEmpty { fields["value"] = clip(v) }
        let h = strAttr(el, kAXHelpAttribute)
        if !h.isEmpty { fields["help"] = clip(h) }
        if fields.isEmpty { continue }
        entries.append(["path": path, "role": role(el), "fields": fields])
        if entries.count >= limit { break }
    }
    ok(["entries": entries, "count": entries.count, "limit": limit])

default:
    fail("usage", "未知命令：\(command)", 2)
}
