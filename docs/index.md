---
layout: home
hero:
  name: 编辑部工具箱
  text: 从来稿到成刊
  tagline: 《逻辑学研究》编辑部自用：Word/LaTeX 初排、参考文献核实、整期装配、编辑部引用库。桌面端 Windows 与 macOS 同一套界面、同一份产物。
  image:
    src: /logo.svg
    alt: 编辑部工具箱
  actions:
    - theme: brand
      text: 三分钟上手
      link: /guide/quickstart
    - theme: alt
      text: 下载最新版（Windows / macOS）
      link: https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest
    - theme: alt
      text: 安装与首次打开
      link: /guide/install
---

<div class="home-section">

## 第一次来？

- [安装与首次打开](/guide/install)：解压、首次打开、系统提示怎么处理。
- [三分钟上手](/guide/quickstart)：拿到一篇来稿后的最短路径。
- [系统要求](/guide/system-requirements)：TeX、LibreOffice 与磁盘空间。
- [常见问题](/reference/faq)：联网与费用、编译失败、打不开。
- [版本记录](/reference/changelog)：每个版本的人读改动。

## 我当前要做的事

<div class="doc-cards doc-cards-2">
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/features/single"><strong>处理一篇稿件</strong><span>初排转换、一校核查、AI 辅助编译修复：编译失败先本机自修，修不好才需要你逐篇点「确认并发送一次」。</span></a>
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/features/assembly"><strong>装配一整期</strong><span>整期工作台一页搞定：生成目录、编译并合并、清理临时文件、生成推送文案；转换时把源稿整个文件夹归档到 original/。</span></a>
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/features/bibdb"><strong>治理引用库</strong><span>查库、入藏、AI 辅助处理 Bib、字段归位与字段差异批量套用：全选库值或全选 AI 值，一次改一整列。</span></a>
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/features/settings"><strong>调整设置</strong><span>设置中心九个页面：外观、引擎与环境、路径、AI 与模型、网络代理、日志与诊断、高级与关于、窗口与托盘、语言与地区。</span></a>
</div>

想直接找某个功能？按 `⌘K` / `Ctrl+K` 打开命令面板，支持按平台搜索，不必一层层翻菜单。

## 为什么用它

<div class="steps">
  <div class="step"><strong>稿件与产物留在你本机</strong><span>转换、核查、装配都在本机完成，应用不上传稿件、不自动联网。</span></div>
  <div class="step"><strong>按本刊规范可配置</strong><span>模板、样式文件、参考文献体例与投稿检查清单随附，规则可核对、可替换。</span></div>
  <div class="step"><strong>离线可跑，AI 需授权</strong><span>需要联网的 AI 核实默认关闭；开启后也只在你的授权范围内发送必要片段。</span></div>
</div>

## 开始之前

- 编译 PDF 需要本机有 TeX：`xelatex` 与 `biber`（Windows 可用 MiKTeX，macOS 可用 MacTeX）。
- 把 Word 稿件转成可编译初排稿时，建议装有 LibreOffice。
- 预留约 2GB 磁盘空间给应用、引擎与临时产物。

## 下载与安装

<div class="download-row">
  <a href="https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest">⬇️ 下载最新版（Windows / macOS）</a>
  <a href="/EditToolBoxStudiesInLogic/guide/install">安装与首次打开</a>
</div>

- **Windows**：解压 zip 即用，不需要另装 Python。主程序 `编辑部工具箱.exe` 与引擎入口
  `SLCNEngine/SLCNEngine.exe` 请保持解压后的目录结构；首次运行 SmartScreen 选「更多信息 → 仍要运行」。
- **macOS**（Apple 芯片）：解压后把 `编辑部工具箱.app` 放进「应用程序」；首次打开如被拦截，**右键 → 打开**。

## 文档与反馈

- **文档**：从[三分钟上手](/guide/quickstart)开始，按任务读[功能](/features/single)各页。
- **问题与反馈**：在 [GitHub Issues](https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/issues) 报告，附上日志里的诊断信息（文本）。
- **版本记录**：[版本记录](/reference/changelog)按版本列出人读改动。
- **获取帮助**：[常见问题](/reference/faq)与[已知限制与边界](/reference/known-limitations)先看这两页。

</div>
