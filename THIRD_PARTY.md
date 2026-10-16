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
