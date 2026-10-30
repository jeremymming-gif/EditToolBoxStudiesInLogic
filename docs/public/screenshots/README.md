# 界面截图（占位）

主页与功能页的截图在**软件优化完成后**补入。用户提供的截图暂存在与工具箱仓并排的
`EditToolBoxWebFig` 目录（同级路径，按本机实际位置替换）。

放入 `docs/public/screenshots/` 后，在 Markdown 里按如下方式引用（VitePress 的 `public` 会映射到站点根）：

```markdown
![初排转换](/screenshots/01-convert.png)
```

建议命名：

| 文件名 | 内容 |
|---|---|
| `01-convert.png` | 初排转换页（源文件/输出目录已填、日志可见） |
| `02-checkbib.png` | 参考文献规范化页（结论摘要） |
| `03-bibdb-stats.png` | 引用库 → 库概况 |
| `04-bibdb-query.png` | 引用库 → 查库结果 |
| `05-bibdb-settings.png` | 引用库 → 设置（核实上限/先算账/查库开关） |
| `06-assembly.png` | 整期装配（可选） |

注意：截图里不要出现作者真实姓名、未发表稿件内容、API key 或密钥片段。
