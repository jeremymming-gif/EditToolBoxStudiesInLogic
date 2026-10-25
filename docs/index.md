---
layout: home
hero:
  name: 编辑部工具箱
  text: 从来稿到成刊
  tagline: 《逻辑学研究》编辑部自用：Word/LaTeX 初排、参考文献核实、整期装配、编辑部引用库。
  image:
    src: /logo.svg
    alt: 编辑部工具箱
  actions:
    - theme: brand
      text: 快速上手
      link: /guide/quickstart
    - theme: alt
      text: 下载最新版（Windows / macOS）
      link: https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest
features:
  - icon:
      src: /icons/single.svg
      alt: ''
    title: 单篇稿件
    details: 初排转换（Word / LaTeX → 可编译 .tex）、文献回读校验、一校核查、合并文件。
  - icon:
      src: /icons/assembly.svg
      alt: ''
    title: 整期装配
    details: 检查 tex 信息、编译全部、生成目录、清理临时文件、合并文件。
  - icon:
      src: /icons/bibdb.svg
      alt: ''
    title: 编辑部引用库
    details: 把已核实的参考文献沉淀成编辑部资产：查库、入藏、待裁决清单；命中即跳过重复检索。
  - icon:
      src: /icons/cost.svg
      alt: ''
    title: 成本控制
    details: 核实只核正文引用、上限可调、先算账预览；引用库命中不花钱。
---

<div class="home-section">

## 从作者来稿到可编译初排稿

<div class="steps">
  <div class="step"><span class="num">1</span><strong>拖入 / 选择源文件</strong><span>Word 或 LaTeX 稿件拖进「初排转换」（Windows 版点「浏览…」选择），再选输出目录。</span></div>
  <div class="step"><span class="num">2</span><strong>补齐信息</strong><span>用「高级选项 → 填写元数据…」填题名/作者（1–5 位、可多单位）/摘要/关键词；出版信息可一次填好。</span></div>
  <div class="step"><span class="num">3</span><strong>运行并编译</strong><span>得到可编译的 .tex + reference.bib，每一步都有日志。</span></div>
</div>

## 下载与安装

<div class="download-row">
  <a href="https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest">⬇️ 下载最新版（Windows / macOS）</a>
  <a href="/EditToolBoxStudiesInLogic/guide/install#windows">🪟 Windows 安装</a>
  <a href="/EditToolBoxStudiesInLogic/guide/install">安装说明</a>
</div>

- **Windows**：下载 `EditToolBox-<版本>-Windows-x64.zip`，解压即用，**不需要另装 Python**（两个 exe 放同一个文件夹）；首次运行 SmartScreen 选「更多信息 → 仍要运行」。
- **macOS**（Apple 芯片）：解压后把 App 放进「应用程序」；首次打开如被拦截，**右键 → 打开**。
- 编译 LaTeX 需要本机安装 TeX Live（Windows 可用 MiKTeX，macOS 可用 MacTeX）。

## 文档

<div class="doc-cards">
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/guide/quickstart"><strong>快速上手</strong><span>三步完成一篇初排；整期流程与引用库入手。</span></a>
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/features/single"><strong>功能总览</strong><span>单篇稿件、整期装配、引用库与成本控制。</span></a>
  <a class="doc-card" href="/EditToolBoxStudiesInLogic/reference/faq"><strong>常见问题</strong><span>联网与费用、API key、编译失败、打不开怎么办。</span></a>
</div>

## 界面

<div class="shot-placeholder">界面截图将在软件稳定后补充（初排转换 / 引用库 / 核实设置）。</div>

## 安全与费用

::: tip 默认离线可用
转换、回读校验、整期装配都不联网。只有你主动勾选「用 AI 处理参考文献」才会调用模型或检索：
**检索证据**按成功请求计费（¥5/1000 次），核实默认只核正文引用（上限可调），引用库命中会跳过检索与 AI；
想先看账，可在「引用库 → 设置」打开**先算账**做零费用预览。
:::

</div>
