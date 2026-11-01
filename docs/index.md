---
layout: home
hero:
  name: 编辑部工具箱
  text: 从来稿到成刊
  tagline: 《逻辑学研究》编辑部自用：Word/LaTeX 初排、参考文献核实、整期装配、编辑部引用库。桌面端 v1.5.0 起为 Flet 单壳（Windows/macOS 同一套界面）。
  image:
    src: /logo.svg
    alt: 编辑部工具箱
  actions:
    - theme: brand
      text: 快速上手
      link: /guide/quickstart
    - theme: alt
      text: 模板下载
      link: /templates/
    - theme: alt
      text: 下载最新版（Windows / macOS）
      link: https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest
features:
  - icon:
      src: /icons/single.svg
      alt: ''
    title: 单篇稿件
    details: 初排转换（Word / LaTeX → 可编译 .tex）、人工元数据表单（`--metadata-json` / 只读预览）、参考文献规范化、一校核查。
  - icon:
      src: /icons/assembly.svg
      alt: ''
    title: 整期装配
    details: 检查 tex 信息、编译全部、生成目录、清理临时文件、合并文件。
  - icon:
      src: /icons/bibdb.svg
      alt: ''
    title: 编辑部引用库
    details: 把已核实的参考文献沉淀成编辑部资产：查库、入藏、待处理清单、参考文献规范化（离线体检；手动并回仅 CLI，图形壳已关闭以防覆盖正文 tex）；命中即跳过重复检索。
  - icon:
      src: /icons/cost.svg
      alt: ''
    title: AI 辅助（可选）
    details: 文献核实与全文核查**默认关闭**；在「设置」页填 API 配置后，由单篇稿件页的选项开启。预算上限可调（单篇默认 3 万 token、批量 100 万 token，0 = 不限），费用在服务商侧计费；不开启也能完成全部离线流程。
  - icon:
      src: /icons/cost.svg
      alt: ''
    title: 成本控制
    details: 核实只核正文引用、上限可调、先算账预览；引用库命中不花钱。
---

<div class="home-section">

## 从作者来稿到可编译初排稿

<div class="steps">
  <div class="step"><span class="num">1</span><strong>选择源文件</strong><span>Word 或 LaTeX 稿件：点「浏览…」用原生对话框选择（Flet 单壳不接收系统拖放），再选输出根目录。</span></div>
  <div class="step"><span class="num">2</span><strong>勾选处理项</strong><span>编译 PDF / 清理临时文件 / 导出原稿 PDF；需要时展开「高级参数」填卷 / 期 / 年 / 页码 / 收稿。</span></div>
  <div class="step"><span class="num">3</span><strong>运行并查看日志</strong><span>得到可编译的 .tex + reference.bib；长任务日志实时滚动、可随时停止。</span></div>
</div>

## 下载与安装

<div class="download-row">
  <a href="https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest">⬇️ 下载最新版（Windows / macOS）</a>
  <a href="/EditToolBoxStudiesInLogic/guide/install#windows">🪟 Windows 安装</a>
  <a href="/EditToolBoxStudiesInLogic/guide/install">安装说明</a>
</div>

- **Windows**：下载 `EditToolBox-<版本>-Windows-x64.zip`，解压即用，**不需要另装 Python**（`编辑部工具箱.exe` 与 `SLCNEngine.exe` 放同一个文件夹）；首次运行 SmartScreen 选「更多信息 → 仍要运行」。
- **macOS**（Apple 芯片）：解压后把 `编辑部工具箱.app` 放进「应用程序」；首次打开如被拦截，**右键 → 打开**。
- 编译 LaTeX 需要本机安装 TeX Live（Windows 可用 MiKTeX，macOS 可用 MacTeX）。
- 旧版 SwiftUI / Tkinter 外壳已移除；需要旧壳源码回退时，可按 MIT 从 `v1.5.0` 标签取回。

## 文档

<div class="doc-cards">
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/guide/quickstart"><strong>快速上手</strong><span>三步完成一篇初排；整期流程与引用库入手。</span></a>
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/features/single"><strong>功能总览</strong><span>单篇稿件、整期装配、引用库与成本控制。</span></a>
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/reference/faq"><strong>常见问题</strong><span>联网与费用、API key、编译失败、打不开怎么办。</span></a>
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/reference/known-limitations"><strong>已知限制与绕行</strong><span>v1.5.0（Flet 单壳）仍存在的限制与绕行办法。</span></a>
</div>

## 模板与维护

<div class="doc-cards doc-cards-2">
  <a class="doc-card" href="/templates/"><strong>投稿与排版模板</strong><span>Word / LaTeX 模板、样式文件、参考文献体例与投稿检查清单（9 件资产可下载）。</span></a>
  <a class="doc-card" href="/maintain/quickstart"><strong>维护者指南</strong><span>本机接入、自检命令、文档站维护与故障速查（命令级，可直接照做）。</span></a>
</div>

## 界面

<div class="shot-placeholder">界面截图将在软件稳定后补充（工作台 / 单篇稿件 / 整期装配 / 引用库 / 设置）。</div>

## 安全与费用

::: tip 默认离线可用
转换、参考文献规范化、整期装配都不联网。需要联网的 AI 文献核实**默认关闭**，在「设置」页填 API 配置与预算上限后，
由单篇稿件页的选项开启；应用本身不上传稿件、不自动联网。
:::

## 历史归档

旧外壳时代的文档已归档，仅供查阅、不要照做：[归档与旧版双壳使用说明](/archive/)。

</div>
