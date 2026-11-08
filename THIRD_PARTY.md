# 第三方组件与归属声明

本工具箱包含或复用了下列第三方作品。**发布（公开仓库 / Release）前请先读第 5 节**。
本仓库自有代码与文档按 MIT License 发布（见 [`LICENSE`](LICENSE)，范围见 [`LICENSE-SCOPE.md`](LICENSE-SCOPE.md)）；
第三方组件各自的许可证不受影响。

---

## 1. 现役运行时与打包依赖

| 组件 | 用途 | 许可证 |
|---|---|---|
| [flet](https://github.com/flet-dev/flet)（含 `flet-desktop`） | 唯一的图形外壳（`sources-flet/`） | Apache-2.0 |
| [PyInstaller](https://pyinstaller.org/) | `flet pack` 把 Flet 应用冻结为 Windows `.exe` | GPL-2.0-or-later（含「打包非 GPL 程序」特殊例外，产物不受 GPL 传染） |
| [pystray](https://github.com/moses-palmer/pystray) | 托盘图标/后台驻留 | LGPL-3.0 |
| [Pillow](https://python-pillow.org/) | 图标与图片处理 | MIT-CMU |
| [Flutter Engine](https://flutter.dev/) | `flet-desktop` 的渲染与窗口后端 | BSD-3-Clause（Flutter 类许可） |
| [Python](https://www.python.org/) | 运行时（引擎与 Flet 壳） | PSF-2.0 |

上述包由打包/运行环境提供，许可证全文随其 wheel/发行版分发；`flet pack` 产物内也保留
PyInstaller 的许可信息。

## 2. 随包 Python 依赖（`Engine/vendor/`）

| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| [pypdf](https://github.com/py-pdf/pypdf) | 6.20.1 | BSD-3-Clause（Copyright (c) 2006-2008, Mathieu Fenniak 等） | 读写 PDF、合并与书签（第 88 条） |
| [pypinyin](https://github.com/mozillazg/python-pinyin) | 0.55.0 | MIT（Copyright (c) 2016 mozillazg） | 中文姓名/排序的拼音处理 |
| [lxml](https://lxml.de/) | 6.1.3 | BSD-3-Clause（Copyright (c) 2004 Infrae） | 解析 Word 的 OOXML |

各包的完整许可证文本随包放在 `Engine/vendor/<包名>.dist-info/licenses/` 下，**随分发一起保留**。
⚠️ `lxml` 是**编译件**：Windows 打包时必须用 Windows 轮子重新装一份
（`pip install --target vendor lxml pypdf pypinyin`），不能把 mac 那份拷过去。

## 3. 排版与模板

`编辑笔记/模板文件/`（`SLCN-MIX.sty` / `SLEN.sty` / `reference.bib` 等）是《逻辑学研究》
编辑部自己的模板，**不属于第三方**；引用的中英文字体（Times New Roman、KaiTi 等）
由用户系统提供，本项目不打包字体。

## 4. 历史归属：旧 Tk/SwiftUI 壳与 Editorialbox（仅历史版本）

- 旧 Windows（Tkinter）与 macOS（SwiftUI）单壳已于 2026-11-01（批 108）**删除**；
  历史源码仍可从 tag 取回：`git archive v1.5.0 sources-win sources-mac`，归档清单见笔记仓
  `baselines/archive/legacy-shells-2026-11-01/`。
- 旧 Windows 壳**曾**复用 chenphilo 的 [`Editorialbox`](https://github.com/chenphilo/Editorialbox)
  （MIT；Copyright (c) 2024 chenphilo）的 Tkinter 布局与中文交互：标签页 + 路径输入框 +
  浏览/运行按钮 + 底部日志框。**没有复用**其功能实现（`merge.py` / `outinfo.py` /
  `c_bibtex.py` / `compileallpdf.py` / `singlecheck.py` / `charti_build_content.py` /
  `deletetemp.py`），也没有复制其 `icon.ico`。当前仓库**不含**旧壳或 Editorialbox 代码。
- v1.0.0–v1.5.0 历史版本按 **MIT License** 发布，该授权**不可撤回**；Editorialbox 的 MIT
  归属仅适用于这些历史版本。
- G9 文案/代码比对（批 108）结论：Flet 新壳与旧壳之间**无实质复制**（连续 ≥8 汉字或
  ≥5 英文词相同、代码 token 近似均逐条人工复核），故不构成对旧壳/Editorialbox 文案的再使用；
  比对证据留存于项目档案。

## 5. 公开前的三件事

1. **许可证**：仓库根 [`LICENSE`](LICENSE) 为 **MIT**（授权范围见 [`LICENSE-SCOPE.md`](LICENSE-SCOPE.md)）；
   v1.0.0–v1.5.0 亦为 MIT，见第 4 节。
2. **稿件仓必须与工具仓分开**：稿件（未发表的作者来稿）只存在于私有稿件仓，
   **绝不能**为了发 Release 而把稿件仓公开。拆仓方案见 `Engine/docs/ENGINE_API.md` §八。
3. **署名**：公开仓库 README 的「许可与归属」一节指向第 4 节的历史 MIT 声明与上游链接。

## 6. 公式转换：OMML→MathML→LaTeX 栈（`Engine/vendor_omml/`，第四十四批②）

Word 公式（OMML）的**可选**新转换后端（`docxxml.omml_to_latex(..., use_omml_stack=True)`；
默认 `USE_OMML_STACK = False`，不改变现有产物）。源码逐字节 vendored，不装包、无新运行时：

| 组件 | 版本/提交 | 许可证 | 用途 |
|---|---|---|---|
| `omml2mml.xsl`（[scienceai/omml2mathml](https://github.com/scienceai/omml2mathml)，npm 1.3.0） | master；源自 Microsoft Office 随附 XSLT（Beta 070708） | **Apache-2.0** | OMML → MathML（XSLT 1.0，用仓库已有 `lxml.etree.XSLT`） |
| [`mathml_to_latex`](https://github.com/asnunes/py-mathml-to-latex) | `187138aac0e5c6c180906868ecb70396909516c8`（2024-11-09） | **MIT**（Copyright (c) 2024 Alexandre Nunes） | MathML → LaTeX（纯标准库） |

- 完整许可证文本：`Engine/vendor_omml/LICENSE-omml2mathml-Apache-2.0.txt`、`LICENSE-py-mathml-to-latex-MIT.md`；归属声明 `Engine/vendor_omml/NOTICE`；来源/校验和/更新策略 `Engine/vendor_omml/SOURCES.md`。
- **上游均不再维护**（XSL 2007 beta；py 2024-11 最后提交）→ 本项目**不追随上游更新**，行为由 `Engine/tests/test_omml_stack.py` 的 149 条真实公式 fixture 锁住；升级须同时更新 `SOURCES.md` 校验和与 fixture 断言。
- 打包：`flet pack` 的打包清单（`tools/gen_docs.py` 的 `APP_FILES`）在**启用 OMML 栈**（默认切换）那一批需要把 `Engine/vendor_omml/` 纳入；默认关闭时 App 包不含它也能正常工作（会自动回退旧实现）。

## 7. 外部依赖程序（条件必需）：LibreOffice（**不随包分发**）

本工具箱在两类场景（对含 MathType/OLE 公式的 Word 稿属**条件必需**）会调用用户本机安装的 LibreOffice（`soffice`）：

1. **Word 的 MathType/OLE 公式图**（预览图 WMF/EMF → PDF）。XeLaTeX 不能直接嵌入 WMF/EMF，
   引擎用 `soffice --headless --norestore --convert-to pdf` 批量转换后插入（第四十七批）；
2. 「导出原稿 PDF」（把 Word 原稿转 PDF，供矢量形状人工截图）。

- **本项目不打包、不分发 LibreOffice 的任何文件**，也不静态/动态链接它 —— 只在运行时起子进程
  调用用户自己的安装（定位顺序：`SLCN_SOFFICE` 环境变量 → `PATH` → 三平台常见安装位置，
  见 `Engine/slcn/platform.py:SOFFICE_CANDIDATES`）。
- LibreOffice 的许可证（MPL-2.0，部分组件 LGPLv3）与版权归 The Document Foundation 及其贡献者；
  用户通过各自发行渠道（官网 / Homebrew / winget / 发行版包管理器）自行安装并遵守其许可证。
- 该依赖对含 MathType/OLE 公式的 Word 稿属**条件必需**（其余稿子可不装）。
- **fail-soft**：找不到 `soffice` 时引擎不报错、不阻断出稿 —— 公式图位置显示「公式图缺失」占位
  并写初排日志 warning（`\IfFileExists` + `\fbox` 占位，见 `Engine/slcn/docxsource.py`、
  `Engine/slcn/build.py`）；「导出原稿 PDF」步骤则跳过并返回说明。检查/安装命令见
  `使用说明.md` §一「Word 公式图（MathType）与 LibreOffice（条件必需）」。
