# ai.json 存取验证（headless，不需要开 GUI）

`sources-mac/Core/ViewModels.swift` 里的 `AppSettings` 负责把「设置」页的 AI 配置
（base / key / model）存到 `~/Library/Application Support/cn.edu.sysu.logic.toolbox/ai.json`。
这两个 harness 用**同一个生产类型**（不是复制品）跑 on-disk 验证，
全程只写注入的临时目录，**不碰真实用户目录、不需要 GUI**。

## 为什么放这里而不是 `sources-mac/`

`build.sh` 是按 `sources-mac/**/*.swift` **通配**编译的，
往 `sources-mac/` 里加一个带 `main` 的文件会直接把 App 构建搞崩。
`tools/` 下的文件不在通配范围内（`tools/build_cli.sh` 是显式文件清单），所以放这里安全。

## 1) 主验证：14 项（存 / 读回 / 权限 / 边界）

```bash
cd sources-mac
swiftc -o /tmp/ai_store_test/harness \
  Core/ViewModels.swift Core/Support.swift Core/EditorsPage.swift Core/Log.swift \
  Features/InfoCheckFeature.swift \
  ../tools/ai_store_test/main.swift
/tmp/ai_store_test/harness        # 期望：SUMMARY: 14 PASS / 0 FAIL
```

覆盖：空目录**不**建文件；保存成功；**文件 0600**；**新建实例读回逐字一致**；
`aiConfigured` 口径（base+model，密钥可空）；首尾空白 trim；`clearAI()`（不删文件、写空串）；
损坏/类型不对的 JSON 不崩；重复保存权限不放松（手工 `chmod 0644` 后再存会收紧回 0600）；
不可写路径返回 `false` 不崩。

## 2) 目录权限专项：预置 0755 的目录必须被收紧到 0700

```bash
cd sources-mac
swiftc -o /tmp/ai_store_test/dircheck \
  Core/ViewModels.swift Core/Support.swift Core/EditorsPage.swift Core/Log.swift \
  Features/InfoCheckFeature.swift \
  ../tools/ai_store_test/dir_mode/main.swift
/tmp/ai_store_test/dircheck   # 期望：实例化后 dir mode = 700、文件 600、读回一致
```

**为什么单列这一项**：`aiStoreURL` 里 `createDirectory(attributes: [.posixPermissions: 0o700])`
的权限**只在目录真的被创建时生效**——Foundation 对**已存在**的目录会忽略该参数。
而真实那个目录通常是 `EditorStore`（`sources-mac/Features/EditorStore.swift`）先建的：
实测 2026-10-14 时它是 **0755**。所以「目录也收紧」这句注释要靠
`createDirectory` 之后再 `setAttributes([.posixPermissions: 0o700], ofItemAtPath:)` 一次才成立
（`ViewModels.swift` 已按此修复；本 harness 就是它的回归测试）。

## 没被这两个 harness 证明的事

- 「设置页按钮 → `saveAI()`」这一跳只有**代码级**证据（`SettingsView` 里确实调了
  `settings.saveAI()` / `clearAI()`），没有真的在 GUI 里点过一次；
- `.atomic` 写盘过程中临时文件的权限（只在写完之后能观测到 0600）；
- 两个实例并发保存的竞态。
