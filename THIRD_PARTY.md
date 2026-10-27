# 第三方组件与归属声明

本工具箱包含或复用了下列第三方作品。**发布（公开仓库 / Release）前请先读第 4 节**。

---

## 1. 界面骨架：`Editorialbox`（MIT）

Windows 外壳（`sources-win/`）的 Tkinter 布局与中文交互取自 chenphilo 的
[`Editorialbox`](https://github.com/chenphilo/Editorialbox)（本项目通过 fork
`jeremymming-gif/WindowsToolBox` 引入）：

```text
MIT License

Copyright (c) 2024 chenphilo
```

- **复用的是**：标签页 + 路径输入框 + 浏览/运行按钮 + 底部日志框这套交互与中文说明文字。
- **没有复用**：它的功能实现（`merge.py` / `outinfo.py` / `c_bibtex.py` / `compileallpdf.py` /
  `singlecheck.py` / `charti_build_content.py` / `deletetemp.py`）——那些逻辑由本项目的
  `Engine/` 提供，外壳一律起子进程调引擎 CLI（见 `sources-win/README.md` 的改动清单）。
- **没有复制**它的 `icon.ico`（打包时需自备图标）。
- mac 侧的 PDF 合并实现（`sources-mac/Core/PDFMerger.swift`）在注释里说明它是**按行为复刻**
  PyPDF2 `PdfMerger` 的语义，不是复制其代码。

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

## 4. 公开前的三件事（2026-10-17 复查：前两条**已办**）

1. ~~本项目自己的许可证还没选~~ **已办**：仓库根已放 [`LICENSE`](LICENSE)（**MIT**，
   与上面复用的 MIT 代码一致；第三方是 BSD-3/MIT，都兼容）。
2. **稿件仓必须与工具仓分开**：本仓库跟踪了**未发表的作者来稿**（期号目录下 1900+ 个文件），
   **绝不能**为了发 Release 而把整个仓库公开。拆仓方案见 `Engine/docs/ENGINE_API.md` §八
   与 `docs/cases/62+.md` 的相关条目。
3. **署名**：公开仓库的 README 里应保留第 1 节的 MIT 声明与上游链接。

## 5. 公式转换：OMML→MathML→LaTeX 栈（`Engine/vendor_omml/`，第四十四批②）

Word 公式（OMML）的**可选**新转换后端（`docxxml.omml_to_latex(..., use_omml_stack=True)`；
默认 `USE_OMML_STACK = False`，不改变现有产物）。源码逐字节 vendored，不装包、无新运行时：

| 组件 | 版本/提交 | 许可证 | 用途 |
|---|---|---|---|
| `omml2mml.xsl`（[scienceai/omml2mathml](https://github.com/scienceai/omml2mathml)，npm 1.3.0） | master；源自 Microsoft Office 随附 XSLT（Beta 070708） | **Apache-2.0** | OMML → MathML（XSLT 1.0，用仓库已有 `lxml.etree.XSLT`） |
| [`mathml_to_latex`](https://github.com/asnunes/py-mathml-to-latex) | `187138aac0e5c6c180906868ecb70396909516c8`（2024-11-09） | **MIT**（Copyright (c) 2024 Alexandre Nunes） | MathML → LaTeX（纯标准库） |

- 完整许可证文本：`Engine/vendor_omml/LICENSE-omml2mathml-Apache-2.0.txt`、`LICENSE-py-mathml-to-latex-MIT.md`；归属声明 `Engine/vendor_omml/NOTICE`；来源/校验和/更新策略 `Engine/vendor_omml/SOURCES.md`。
- **上游均不再维护**（XSL 2007 beta；py 2024-11 最后提交）→ 本项目**不追随上游更新**，行为由 `Engine/tests/test_omml_stack.py` 的 149 条真实公式 fixture 锁住；升级须同时更新 `SOURCES.md` 校验和与 fixture 断言。
- 打包：`build.sh` / Windows 打包白名单（`tools/gen_docs.py` 的 `APP_FILES`）在**启用 OMML 栈**（默认切换）那一批需要把 `Engine/vendor_omml/` 纳入；默认关闭时 App 包不含它也能正常工作（会自动回退旧实现）。

## 6. 外部依赖程序（条件必需）：LibreOffice（**不随包分发**）

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
