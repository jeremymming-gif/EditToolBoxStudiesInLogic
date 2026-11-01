# sources-flet —— Flet 单壳（桌面端默认产品线）

自 **v1.5.0** 起，编辑部工具箱的 Windows / macOS 桌面端统一为这里的 **Flet 单壳**。
旧外壳 `sources-win/`（Tkinter）与 `sources-mac/`（SwiftUI）已 **frozen**：保留源码作回退参考，
不再随发布线构建（详见仓库根 `README.md` 与 `RELEASING.md`）。

## 结构

```
app/
  main.py        应用入口与外壳（导航、主题/缩放、运行编排、窗口与退出语义）
  ui/            tokens.py（设计 tokens）/ components.py（组件库）/ ia_map.py（15 页 IA 映射）/ pages.py（页面）
  services/      engine_bridge.py（引擎 argv/日志/取消；sidecar 定位）/ settings_store.py（设置兼容与落盘）
tests/           pytest（不启动 GUI）：引擎桥 argv、摘要解析、设置迁移、IA 映射
packaging/       发布/打包共用材料：FIRST-OPEN-*.txt、sidecar_check.py、README.md
```

## 开发

```bash
cd sources-flet
python3 -m flet run app/main.py          # 开发运行（热重载）
python3 -m pytest tests -q               # 单元测试（不启动 GUI）
```

约定：外壳只做「取值 → 拼参数 → 显示日志」；所有文档判定逻辑都在 `Engine/`，不在界面里复制规则。
界面语言的唯一入口是 `app/ui/tokens.py`（深浅色两套语义色、间距/圆角/字号），页面不得写死颜色。

## 打包

发布口径由仓库根 `.github/workflows/build-flet.yml`（只产 artifact）与 `.github/workflows/release.yml`
（打 tag 才建 Release）共用：引擎 sidecar 与 `flet pack` 应用打包，产物 zip 内
`编辑部工具箱.exe` / `编辑部工具箱.app` 与引擎 sidecar（`SLCNEngine.exe` / `Contents/MacOS/SLCNEngine`）
必须保持同目录/同 App 内布局。本地复刻命令见 [`packaging/README.md`](packaging/README.md)。
