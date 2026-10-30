---
title: 环境检查与诊断包
description: 首次运行自检检查哪八项、缺依赖怎么装、诊断包里有什么。
---

# 环境检查与诊断包

工具箱跑的是**本机**的 TeX 与 LibreOffice。以前「能不能用」要等第一次转换失败才知道；
现在第一次打开 App 会**自动做一次环境检查**，把缺什么、怎么装、缺了会影响哪一功能一次讲清。

- **macOS**：首次打开自动检查；只要有**必需项缺失**就弹一个窗口列出安装命令（只弹一次）。
  之后在「设置」页随时点「环境检查」重跑。
- **Windows**：在「设置」页点「环境检查」；结果直接显示在页面里。
- **命令行**：见下面「命令行怎么用」。

检查是**非阻断**的：缺项不挡任何页面，转换、核对、装配都能照原来的方式跑；
它只在你点开时给结论。检查**不联网、不自动安装、不改任何文件**（`--out-dir` 那一项会写一个探针文件，
写完立即删掉）。

## 八项检查

| id | 检查 | 必需 / 可选 | 缺了会怎样 |
|---|---|---|---|
| `engine_bundle` | 引擎运行时（发布包内置 Python，或源码模式的 Python 3.14 + 依赖） | 必需 | 任何功能都起不来 |
| `xelatex` | XeLaTeX | 必需 | LaTeX 稿的初排稿编译不出 PDF |
| `biber` | BibTeX 后端 biber | 必需 | 参考文献（biblatex）不能排序与回读 |
| `libreoffice` | LibreOffice（`soffice`） | 可选 | 只影响含 OLE 对象 / MathType 公式的 Word 稿；纯 LaTeX 稿不受影响 |
| `git` | git | 可选 | 只影响笔记仓 / 引用库的版本与同步操作 |
| `disk_space` | 磁盘空间 ≥ 2 GB | 必需 | 编译与合并中途写不下 |
| `output_writable` | 输出目录可写（只在给了 `--out-dir` 时查） | 必需 | 产物/报告落不了盘 |
| `template_bundle` | 排版模板（`编辑笔记/模板文件`） | 必需 | 初排稿套不上本刊版式 |

`engine_bundle` 的两种形态：

- **发布包（exe）**：Python 运行时**随包内置**，不要求本机装 Python。这是 Windows 两个 exe 与
  macOS `.app` 的常态。
- **源码（source）**：要求 PATH（或环境变量 `SLCN_PYTHON`）上有 **Python 3.14**，且
  `lxml` / `pypinyin` / `pypdf` 可导入（通常装在 `Engine/vendor/`）。

形态默认自动判断（`sys.frozen` 或 exe 同目录有 `SLCNEngine`）；命令行可用 `--profile` 强制。

## 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 必需项全部就绪（可选项可以缺） |
| `1` | 必需项就绪，但**可选项**缺失——相关功能受限，其它照常 |
| `2` | **必需项**缺失——按提示装好再重跑 |

注意与引擎其它入口同一个口径：**退出码 1 不是程序出错**。

## 安装命令

自检的提示里给的就是**当前平台**的命令：

| 缺什么 | macOS | Windows |
|---|---|---|
| TeX（`xelatex`） | `brew install --cask mactex-no-gui` | `winget install --id MiKTeX.MiKTeX -e` |
| biber | `brew install biber` | 随 MiKTeX / TeX Live 安装 |
| LibreOffice | `brew install --cask libreoffice` | `winget install --id TheDocumentFoundation.LibreOffice -e` |
| git | `xcode-select --install` | `winget install --id Git.Git -e` |
| Python 3.14（仅源码模式） | `brew install python@3.14` | `winget install --id Python.Python.3.14 -e` |

装完**重跑一次检查**，不用重装 App。手工安装细节见仓库根的 `使用说明.md` §2。

## 命令行怎么用

```bash
cd Engine
export PYTHONPATH="$PWD/vendor"        # 源码模式：引擎依赖

python3 envcheck.py                    # 人类可读的报告
python3 envcheck.py --json             # 只输出 JSON（外壳读的就是它）
python3 envcheck.py --profile exe      # 强制按发布包形态判定
python3 envcheck.py --out-dir /tmp/envcheck   # 顺带检查该目录可写 + 落一份 envcheck.json
```

发布包里同样的检查走 dispatcher：`SLCNEngine.exe envcheck.py --json --profile exe`。

`--json` 的结构（schema = `envcheck.v1`）：

```json
{
  "schema": "envcheck.v1",
  "profile": "source",
  "platform": "Darwin 27.0.0 (arm64)，Python 3.14.8",
  "checks": [
    {
      "id": "xelatex", "title": "XeLaTeX（xelatex）", "level": "required",
      "status": "ok", "version": "3.141592653-2.6-0.999997",
      "detail": "已找到：/Library/TeX/texbin/xelatex",
      "hint": "", "install": {"mac": "…", "windows": "…"}
    }
  ],
  "summary": {"ok": 8, "warn": 0, "fail": 0}
}
```

## 诊断包

「设置」页的「导出诊断包…」把这次检查的结果打成一个 zip，方便发给维护者：

- `diagnostics.txt`：App 版本、构建提交、引擎版本、操作系统、运行形态、八项检查的结论；
- `envcheck.json`：上面那份 JSON，原样。

诊断包里**只有这些**：不含稿件内容、不含作者信息、不含 API Key（密钥只在本机配置文件里，
诊断包从不读它）、不含引用库（`bib_db`）内容。命令行导出时把 `--out-dir` 指到一个空目录即可：

```bash
python3 envcheck.py --json --out-dir /tmp/envcheck
cd /tmp/envcheck && zip 环境诊断.zip diagnostics.txt envcheck.json
```

## 相关文档

- `使用说明.md`（仓库根）：安装、首次打开与各页用法
- [已知限制与绕行](known-limitations.md)：当前版本仍存在的限制
- [常见问题](faq.md)

## 和环境检查一起用

运行失败时诊断卡与环境检查互补：卡片说「这次为什么失败」，环境检查说「机器是否装齐」。
卡片上的「复制诊断包」是本地纯文本（版本、类别、原因、下一步、脱敏日志尾部、环境自检），
≤16KB、零上传；「打开日志」定位应用缓存中的本次运行日志（保留最近 20 份）。
