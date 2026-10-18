# 截图占位说明

主页 `index.md` 将来引用本目录的截图。请把真实界面截图放到这里，命名如下（PNG，宽度建议 1600–2000px，
去掉桌面背景、只截应用窗口）：

| 文件名 | 内容 |
|---|---|
| `01-convert.png` | 初排转换页：源文件/输出目录已填、运行日志可见 |
| `02-checkbib.png` | 文献回读校验页：结论摘要 |
| `03-bibdb-stats.png` | 引用库 → 库概况：条数/期数/健康状态 |
| `04-bibdb-query.png` | 引用库 → 查库：一次查询结果 |
| `05-bibdb-settings.png` | 引用库 → 设置：核实上限/先算账/查库开关 |
| `06-assembly.png` | 整期装配页（可选） |

放好后在 `index.md` 的「截图」小节加上：

```markdown
[![初排转换](assets/screenshots/01-convert.png)](assets/screenshots/01-convert.png)
```

## 注意

- 截图里**不要出现**作者真实姓名、未发表稿件内容、API key 或密钥片段；
- 示例稿件请用公开/已发表的文献，或把敏感字段打码。
