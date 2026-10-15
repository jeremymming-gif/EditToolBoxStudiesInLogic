import SwiftUI

@main
struct ToolboxApp: App {
    var body: some Scene {
        WindowGroup("编辑部工具箱") {
            MainWindow()
                .frame(minWidth: 860, minHeight: 640)
        }
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) { }
        }
    }
}
