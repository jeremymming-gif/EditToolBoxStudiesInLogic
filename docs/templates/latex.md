---
title: LaTeX 模板与编译链
description: 本刊 LaTeX 投稿模板（CN/EN）、样式文件与 XeLaTeX + biber 编译链；附示例 PDF。
---

# LaTeX 模板与编译链

自带 `.tex` 稿件的作者可使用本刊 LaTeX 模板；模板已预置版式（B5 页面、正文宋体 10.5pt/固定行距 16.4pt、参考文献 9pt 悬挂缩进、三线表、定理环境、图题/表题位置），并给出一份可直接编译的示范稿。

## 下载

| 文件 | 说明 |
|---|---|
| [CN_template.tex](/templates/CN_template.tex?download=1) · [CN_template.pdf](/templates/CN_template.pdf) | 中文稿模板 + 示例 PDF |
| [EN_template.tex](/templates/EN_template.tex?download=1) · [EN_template.pdf](/templates/EN_template.pdf) | 英文稿模板 + 示例 PDF |
| [SLCN.sty](/templates/SLCN.sty?download=1) · [SLCN-MIX.sty](/templates/SLCN-MIX.sty?download=1) · [SLCN-MIX4.sty](/templates/SLCN-MIX4.sty?download=1) | 中文样式（与 `CN_template.tex` 同目录） |
| [SLEN.sty](/templates/SLEN.sty?download=1) | 英文样式（与 `EN_template.tex` 同目录） |
| [reference.bib](/templates/reference.bib?download=1) | 参考文献库示例（biblatex） |

> 下载方式同 [模板总览](/templates/) 的说明（404 时强制刷新或用 GitHub 源文件直链）。**`.sty` 必须与 `.tex` 放在同一目录**，否则编译会在导入样式处报错。

## 编译链（XeLaTeX + biber）

```bash
xelatex  CN_template.tex     # 第一遍：生成 .aux/.bcf
biber    CN_template         # 生成参考文献（biblatex 后端）
xelatex  CN_template.tex     # 第二遍：回填引用
xelatex  CN_template.tex     # 第三遍：回填目录/交叉引用
```

英文稿把文件名换成 `EN_template` 即可。要求本机安装 **TeX Live**（Windows 亦可用 MiKTeX），且 `xelatex`、`biber` 在 PATH 里（安装与自检见[环境检查与诊断](/reference/environment-check)）。

## 版式与写法要点

- **页面/边距/行距**：模板已设好（B5 176×250mm、正文区 130×203mm、固定行距 16.4pt），不要改 `geometry` 与行距。
- **标题与作者**：多作者同一行；中文作者之间用全角空格、英文用半角逗号；作者信息写标题页脚注（逐位「姓名，单位，E-mail：…」）。
- **正文引用**：本刊用**括号编号制**。在 `biblatex` 下用 `\parencite`（如 `\parencite[第45 页]{key}`）；引导语「参见文献[4]」用裸编号形式时不要重复加括号。
- **定理/证明**：模板的定理环境一段一条，段首标签与环境名由排版统一生成；「注」不编号。
- **图与表**：图题在图下方、表题在表格下方，各只写一次；表格用三线表，不合并单元格。
- **参考文献**：文末只写一行「参考文献」；`.bib` 随稿提交（初排优先采用）；印刷体例见[本刊参考文献印刷体例](/templates/reference-style)。
- **公式**：行内/独立公式都用 LaTeX 数学环境；独立公式居中单独成段，正文用「式（1）」引用，编号由排版统一处理。
- **中文与字体**：模板用 XeLaTeX 编译（UTF-8 源文件）；缺字体时按 TeX 发行版提示安装，不要改成 pdfLaTeX。

> 示例 PDF 仅用于对照版式；正式投稿以 `.tex` + `.bib`（+ `.sty`）为准。若模板与已刊版式有出入，以编辑部确认的模板为准。
