import SwiftUI

@main
struct ToolboxApp: App {
    /// 首次运行环境检查（B-283）。**只跑一次**：`runIfNeeded()` 自己看
    /// UserDefaults 的 `envCheckDone`（见 `Core/EnvCheck.swift`），跑完置位。
    /// 放在 App 这一层而不是 `MainWindow`：它必须早于用户点任何页面发生，
    /// 又不能用 `MainWindow` 的 `settings`（那是个 `@StateObject`，外部拿不到）。
    @StateObject private var firstRun = EnvCheckModel()

    var body: some Scene {
        WindowGroup("编辑部工具箱") {
            MainWindow()
                .frame(minWidth: 860, minHeight: 640)
                .task { firstRun.runIfNeeded() }
                // 非阻断：只有**必需项**缺失才弹一次，点掉照样进主界面。
                .alert("首次运行环境检查", isPresented: $firstRun.showFirstRunAlert) {
                    Button("知道了", role: .cancel) { }
                } message: {
                    Text(firstRun.firstRunAlertText)
                }
        }
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) { }
        }
    }
}
