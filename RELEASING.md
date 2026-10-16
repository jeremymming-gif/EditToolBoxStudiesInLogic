# 发布与拆仓手册（RELEASING）

面向**仓库所有者**（不是编辑、也不是 AI 会话）。每一步都标了「谁做」与「怎么验」。

> ⚠️ **AI 会话不执行任何 git 写操作**（`add` / `commit` / `push` / `tag` / 拆仓）——
> 这是本项目从一开始就定下的红线。所以下面凡带 🔒 的步骤都必须由人来跑；
> AI 只准备脚本、只做只读检查。

---

## 0. 先解决一个**阻断项**：引擎代码还没进版本库

2026-10-15 拆仓预演时发现：`git ls-files Engine` **一个文件都没有**——
整个 Python 引擎（`convert.py` / `checkbib.py` / `mergepdf.py` / `slcn/` / `tests/` / `docs/`，
约 80 个文件）是**未跟踪**状态，只存在这台机器上。

后果有两个，都很严重：

1. **拆不出东西**：`git subtree split` 只取已提交的内容 —— 拆出来的新仓里**没有引擎**
   （实测：新仓顶层只有 `sources-mac/ tools/ build.sh …`）。`tools/split_repo.sh` 现在会检测到这一点并**拒绝执行**。
2. **没有历史备份**：这些代码目前没有 git 历史，机器坏了就没了。

🔒 所有者先做：

```bash
cd <稿件仓>
git add Engine sources-win THIRD_PARTY.md \
        .github RELEASING.md tools/*.sh
git status --short | head          # 确认要提交的东西对
git commit -m "把初排引擎、Windows 外壳与第三方声明纳入版本库"
```

验证：`git ls-files Engine | wc -l` 应远大于 0（当前 80 个未跟踪文件都会进来）。

> 顺带确认 `Engine/.gitignore` 只忽略 `out/`、`vendor/`、`__pycache__/`——
> `vendor/` 是本地依赖（mac 轮子），**不该**进库；`out/` 是产物与冻结基线，也不进库。

---

## 1. 拆仓（🔒 所有者执行；脚本只准备、不代跑）

```bash
cd <稿件仓>/EditToolBox
bash tools/split_repo.sh              # 预演：打印要执行的命令
bash tools/split_repo.sh --apply      # 真跑：在**临时克隆**里拆，稿件仓只被读
```

脚本做的事（跑完会打印临时目录路径）：

1. `git clone` 稿件仓到临时目录（**稿件仓一个字节都不动**）；
2. `git subtree split -P EditToolBox -b edittoolbox-split` —— 保留工具那部分的提交历史；
3. 补上**引擎运行必需**的 `编辑笔记/模板文件/`（9 个文件，352K——`slcn/templates.py`
   靠它生成初排稿的 `.sty` 与骨架）与规范文档 `编辑笔记/排版手册/`、`编辑笔记/流程化经验书.md`；
4. **验证**新仓里真有 `Engine/convert.py`、`Engine/slcn/pdfmerge.py`、`Engine/engine_main.py`、`build.sh`——
   缺任何一个就报错停下（宁可不拆，也不要拆出一个空壳）。

为什么模板必须一起走：引擎按「从 `slcn/` 逐级向上找 `编辑笔记/模板文件`」定位模板
（`Engine/slcn/templates.py`）。没有它，`convert.py` 连初排稿都生成不了。

---

## 2. 在新仓里改布局（🔒 所有者执行）

```bash
cd <临时目录>/EditToolBox
bash tools/rename_to_edittoolbox.sh            # 预演
bash tools/rename_to_edittoolbox.sh --apply    # 真改 + 自检
```

改什么：`sources-mac/` → `sources-mac/`、`sources-win/` → `sources-win/`，
并把全仓路径引用里的 `Engine` → `Engine`、`sources-mac` → `sources-mac` 等
（约 200 行、55 个文件），以及三处**必须定向改**的地方
（文档看门狗的仓库根推导与路径前缀表、Swift 测试台的源码目录常量）。

脚本带**防误跑护栏**：在稿件仓里（或稿件仓的 `` 里）跑会被拒绝——
判据是「附近出现期号目录」或「存在 ``」。

**有意不改**：`Engine/docs/cases/**`（案例法是只增不改的历史记录，路径按当时的布局写才对；
看门狗已把这一目录排除在路径存在性检查外）。

---

## 3. 新仓自检（🔒 或 AI 都能跑，只读）

```bash
cd Engine && PYTHONPATH="$PWD/vendor" python3 tools/gen_docs.py          # 重算 README 的 AUTO 块
PYTHONPATH="$PWD/vendor" python3 tests/test_docs_consistency.py          # 应「失败 0 项」
cd .. && ./build.sh && ls -la dist/                                       # mac 侧 .app
cd sources-win && PYTHONPATH="$PWD/../Engine/vendor" python3 tests/test_bridge.py   # 外壳桥梁 41 项
```

2026-10-15 的实测结果（照着这套配方走一遍就有）：**35 套件全绿、看门狗失败 0 项**。
新仓里没有稿件库，因此：
- `gen_docs` 的「普查 / corpus / 占位符」三行会显示「本仓不含稿件库」，**不再是 ❌**；
- 依赖真实稿件的断言自动 **SKIP**（不是 FAIL）——这是拆仓时为它们专门加的分支；
- `test_pdfmerge.py` 与 `test_sep_style.py` 的「真实成品交叉验证」也会 SKIP（稿件不在本仓）。

---

## 4. 建远端并推（🔒 所有者执行）

```bash
cd <临时目录>/EditToolBox
git remote add origin git@github.com:<账号>/EditToolBox.git
git push -u origin edittoolbox-split:main
```

公开仓已建好（2026-10-15）：
**<https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic>**
本地克隆在 `~/Documents/GitHub/EditToolBoxStudiesInLogic`（GitHub Desktop 可直接「添加本地仓库」）。

⚠️ 以后**日常同步不用再走 step 1–4**：改完稿件仓里的 ``，直接跑

```bash
cd <稿件仓>/EditToolBox
bash tools/sync_to_public.sh --apply --push        # 预演去掉 --apply；默认目标就是上面那个克隆
```

它做的：rsync 工具目录（排除产物/依赖/缓存/内部资料）→ 补 `编辑笔记/模板文件/` →
跑改名脚本 → 重算 AUTO 块 + 跑看门狗 → 自检「没有内部资料」→ commit + push。

想**把内部资料从历史里也清掉**，用 `--rebuild`：

```bash
bash tools/sync_to_public.sh --rebuild --push
```

它会 `rm -rf` 目标目录、重新铺内容、`git init` 成**单一快照提交**再 force push ——
公开仓的历史里不会有任何内部资料的痕迹（2026-10-15 就是这么做的）。
代价：公开仓没有开发历史（历史留在稿件仓）。有协作者之后就别再用 `--rebuild`。

---

## 5. 发版（打 tag 即自动出双平台资产）

```bash
git tag v1.0.0 && git push origin v1.0.0
```

`.github/workflows/release.yml` 会：

| job | 干什么 |
|---|---|
| `test` | macos-14 与 windows-latest 各跑一遍**不依赖稿件库**的引擎套件 + 外壳桥梁测试 |
| `macos` | `./build.sh` 出 `.app` → 打成 `EditToolBox-<版本>-macOS-arm64.zip`，并附一份说明 `FIRST-OPEN-macOS.txt` |
| `windows` | `sources-win/build-win.ps1` → 冻结 `SLCNEngine.exe` + 界面 exe → `EditToolBox-<版本>-Windows-x64.zip`，并附一份说明 `FIRST-OPEN-Windows.txt` |
| `release` | 汇总资产 + 生成 `latest.json`（外壳启动时比对它，**只提示、不自动替换**），写正文（含两个平台的「首次打开」处理办法）并建 Release |

> ⚠️ **两份说明的文件名必须是纯 ASCII**：GitHub 上传资产时会把文件名里的非 ASCII 字符
> **直接删掉** —— `首次打开（macOS）.txt` 会变成 `macOS.txt`（2026-10-17 发 v1.0.1 时实测）。
> 文件**内容**是中文，只有**文件名**用 ASCII。同理，资产名里别用中文。

也可以 `workflow_dispatch` 手动跑一次：只出构建产物、不建 Release（用来验证打包脚本）。

### 签名与「下载即用」的现实（2026-10-15 初次决定；**2026-10-16 用户最终拍板：不做**）

> **已定案：本项目不做代码签名，也不做公证（notarize）。以后不必再提议，也不要在发布清单里留这一项。**
> 后果**已知并接受**：macOS 首次打开要右键 →「打开」（或 `xattr -dr com.apple.quarantine`）；
> Windows 会弹 SmartScreen →「更多信息 → 仍要运行」。资产里必须**照实说明**这两步，不许说成小问题。

- **macOS 不买 Apple Developer 账号**（$99/年）⇒ 下载的 `.app` 会被 Gatekeeper 拦
  （提示「已损坏，无法打开」）。所以资产里**附说明**，正文写清两种处理办法：
  右键 →「打开」，或 `xattr -dr com.apple.quarantine <app>`。
  **不要**把这条说成「小问题」——编辑看到「已损坏」会以为文件坏了。
- **Windows 未签名 exe** 会触发 SmartScreen：「更多信息 → 仍要运行」。
  要干净就得买代码签名证书；不买就在 README 里写明这一步。
- 想让 mac 侧「下载即用」，唯一的办法是买账号做**签名 + 公证**（notarize），
  并把证书放进 CI secrets（`CSC_LINK` / `CSC_KEY_PASSWORD` 那一套）。这是**你的决定**，不是技术限制。

---

## 6. 公开前必须拍板的两件事

1. **许可证**：新仓要放一份 `LICENSE`。建议 **MIT**——与复用的 MIT 界面骨架
   （`chenphilo/Editorialbox`）一致，且与 `vendor/` 里的 BSD-3/MIT 依赖都兼容。
   归属声明见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。
2. **模板可以公开**（2026-10-15 编辑部决定）：`编辑笔记/模板文件/` 随公开仓发布 ——
   它是**运行时必需**的（`Engine/slcn/templates.py` 靠它生成初排稿的 `.sty` 与骨架），
   不带就等于发一个跑不起来的包。它是本刊自有资产，不是第三方内容。

**公开仓有意不带的东西**（同步脚本会自动排除，看门狗对这些缺席逐条 SKIP）：

| 不带什么 | 为什么 |
|---|---|
| `Engine/HANDOFF.md` | 内部进度文档（稿件代号、待办、踩坑记录） |
| `Engine/docs/cases/` | 案例法：含作者姓名与稿件片段 |
| `编辑笔记/排版手册/`、`编辑笔记/流程化经验书.md` | 内部规范文档 |
| `dist/`、`Engine/vendor/`、`__pycache__/` | 构建产物与本地依赖 |

> 稿件仓（`Studies_in_Logic_Database`）**继续私有**：它跟踪了 1900+ 个未发表稿件文件。
> 拆仓的整个理由就是「工具可以公开，稿件不能」。
