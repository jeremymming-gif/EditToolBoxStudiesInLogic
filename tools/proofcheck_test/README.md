# 「一校核查」页验证（headless，不开 GUI、不起子进程）

验证 `sources-mac/Features/ProofCheckFeature.swift`、`sources-mac/Views/ProofCheckView.swift`、
`sources-mac/Core/ViewModels.swift` 里「一校核查」这一页的接线（**编译的是生产源码本身**）。

| 生产文件 | 被验证的东西 |
|---|---|
| `sources-mac/Features/ProofCheckFeature.swift` | `Options → argv` 的**纯函数**映射（`--tex` / `--lang` / `--fix`，顺序固定；语种「自动」= 空串 → 不传 `--lang`）；路径含空格/中文时不做 shell 插值；`locateTool(convertTool:)` 从 `convert.py` 的目录里找 `proofcheck.py`，找不到就返回 `nil`（不拼出半截命令） |
| `sources-mac/Views/ProofCheckView.swift` | **不用 `@State`**（本机只有 Command Line Tools，SwiftUI 宏插件不可用 —— 这条限制踩过）；状态交给注入的 `ProofCheckModel`；调 `ProofCheckFeature.run(options:log:)`（逻辑不在视图里）；工作线程跑 + 回主线程复位；勾选项与语种都绑到 model |
| `sources-mac/Views/MainWindow.swift` | 有「一校核查」页（`NavPage` 枚举）；「单篇稿件」组内顺序在「文献回读校验」之后、且该组不含「合并文件」（比的是 `NavPage.singleArticle` 定义行，不是注释里的词）；「合并文件」在「整期装配」组（B-235）；建了 `ProofCheckModel` 并传进页面 |
| `sources-mac/Core/ViewModels.swift` | `ProofCheckModel` 四个字段齐全（稿件 / 另存 / 语种 / 运行中） |

## 为什么放 `tools/`

`build.sh` 按 `sources-mac/**/*.swift` **通配**编译，往 `sources-mac/` 里放带 `main`
的文件会把 App 构建搞崩。`tools/` 不在通配范围内（与 `tools/ai_settings_test/` 同理）。

## 跑法

```bash
cd sources-mac
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -module-cache-path /tmp/pcmc \
  -o /tmp/proofcheck_test_harness \
  Core/ViewModels.swift Core/Support.swift Core/Log.swift Core/EditorsPage.swift \
  Features/InfoCheckFeature.swift Features/ConvertFeature.swift Features/ProofCheckFeature.swift \
  ../tools/proofcheck_test/main.swift
/tmp/proofcheck_test_harness        # 期望：SUMMARY: 19 PASS / 0 FAIL
```

> ⚠️ 别把 `-module-cache-path` 指到仓库里的 `.mc/`：`build.sh` 也用那个目录，
> 两条路径解析成同一个文件时 swiftc 会报 `module '_DarwinFoundation1' is defined in both`。
