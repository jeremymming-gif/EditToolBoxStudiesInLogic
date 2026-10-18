#!/usr/bin/env python3
"""Windows 外壳与引擎之间的桥梁（第 89 条）——**基本不开窗口**的测试。

为什么能不开窗口就测：`engine.py` / `settings.py` 里**没有一行 tkinter**
（那是刻意的设计，见 `engine.py` 开头）。界面只负责「取值 → 调这两个模块 → 显示日志」，
所以逻辑部分在 mac / Linux / CI 上都能验；只有 (g)(h) 两组会真开一次窗口（缺 tkinter 时 SKIP）。

钉住这些事：
  (a) 引擎与解释器的定位顺序（环境变量 → 打包形态 → 开发形态）；
  (b) 拼出来的命令行**与 `Engine/docs/ENGINE_API.md` 的契约一致**（参数名、顺序、布尔开关）；
  (c) **密钥绝不进命令行**（只在环境变量里）；UTF-8 / 无缓冲 / vendor 也都设好；
  (d) 配置读写：密钥只落本地（**每用户**目录）、界面显示打码、坏 JSON 不炸；
  (e) 真跑一次：用桥梁调 `mergepdf.py` 合并一个假期目录，逐行日志能收到、退出码正确；
  (f) 界面文件：语法 / 导入检查（不开窗口）；
  (g) 真开一次窗口（控件参数写错只有这一步抓得到；缺 tkinter 时 SKIP）；
  (h) 日志「摘要 / 原始」两层（U4）：跨平台夹具 `tools/log_cases.json` 逐条过，
      并在 (g) 的真窗口里验证能切模式、能看到折叠提示、偏好能落盘、空状态占位不进复制内容。
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile

SHELL = pathlib.Path(__file__).resolve().parent.parent
REPO = SHELL.parent                       # ← 仓根；从前写成 `.parent.parent`（= GitHub/），
                                          #   于是 (g) 组给的 PYTHONPATH 指到了不存在的目录
sys.path.insert(0, str(SHELL))

import engine as E  # noqa: E402
import settings as S  # noqa: E402

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def clear_env(*names: str) -> dict:
    return {n: os.environ.pop(n, "") for n in names}


def _raises_value_error(func, *args, **kwargs) -> bool:
    """调用必须抛 ValueError（参数拼装层的反例测试用）。"""
    try:
        func(*args, **kwargs)
    except ValueError:
        return True
    except Exception:  # noqa: BLE001  别的异常不算「通过了反例」
        return False
    return False


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 定位引擎与解释器 ──")
eng = E.slcn_engine()
check("(a) 开发形态能找到 `../Engine`（仓库里 sources-win/ 与 Engine/ 并列）",
      eng is not None and os.path.isfile(os.path.join(eng, "convert.py")), f"{eng}")
check("(a) 找的是**绝对路径**", bool(eng) and os.path.isabs(eng))
check("(a) `slcn_python()` 认得当前解释器（外壳自己就是 Python 跑的）",
      E.slcn_python() is not None, f"{E.slcn_python()}")

saved = clear_env("SLCN_ENGINE")
try:
    os.environ["SLCN_ENGINE"] = "/tmp/绝对不存在的引擎目录"
    check("(a) `SLCN_ENGINE` 指到不存在的目录 → 忽略它，继续按开发形态找",
          E.slcn_engine() == eng, f"{E.slcn_engine()}")
    os.environ["SLCN_ENGINE"] = str(pathlib.Path(eng))
    check("(a) `SLCN_ENGINE` 指对了 → 就按它来", E.slcn_engine() == eng)
finally:
    if saved["SLCN_ENGINE"]:
        os.environ["SLCN_ENGINE"] = saved["SLCN_ENGINE"]
    else:
        os.environ.pop("SLCN_ENGINE", None)

st = E.status()
check("(a) `status()` 在本机是「可用」的（有引擎目录 + 有 Python）", st.ok, st.describe())
check("(a) `describe()` 把运行方式写清楚", "引擎目录" in st.describe(), st.describe())

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 拼命令行：必须与 ENGINE_API 的契约一致 ──")
c = E.convert_call(source=r"C:\稿\张三.docx", out=r"D:\产物", compile_pdf=True,
                   verify_refs=True, vol="5", issue="90")
check("(b) 初排转换：入口是 convert.py 且用 `--source/--out`",
      c.argv[1].endswith("convert.py") and "--source" in c.argv and "--out" in c.argv,
      c.display)
check("(b) 布尔开关只在勾选时出现（--compile / --verify-refs）",
      "--compile" in c.argv and "--verify-refs" in c.argv and "--clean" not in c.argv)
check("(b) 出版信息按 `--vol 5 --issue 90` 传", "--vol" in c.argv and "--issue" in c.argv)
check("(b) 路径**原样传**（不自己加引号，交给 subprocess 处理空格）",
      r"C:\稿\张三.docx" in c.argv, f"{c.argv}")

c2 = E.checkbib_call(returned=r"C:\稿\reference.bib", merge_into=r"D:\产物\01 张三\reference.bib")
check("(b) 回读校验：`--returned` + `--merge-into`",
      c2.entry == "checkbib.py" and "--returned" in c2.argv and "--merge-into" in c2.argv)
check("(b) 没给的可选参数**不出现**（--original/--tex/--out）",
      "--original" not in c2.argv and "--tex" not in c2.argv and "--out" not in c2.argv)

c3 = E.merge_call(folder=r"E:\期\2026年第5期(90)", editors_page=True, nest_outlines=True)
check("(b) 合并：`--folder` + 两个开关",
      c3.entry == "mergepdf.py" and "--folder" in c3.argv
      and "--editors-page" in c3.argv and "--nest-outlines" in c3.argv
      and "--back-cover" not in c3.argv)
# ⚠️ 这一条**必须去读打包 dispatcher 的表**：从前只比外壳自己的表 + 一个同名单，
#    是**假保证** —— 外壳长到 8 个入口、dispatcher 停在 3 个，测试照样绿，
#    而用户机器上 9 个标签页有 5 个「❌ 不认识的入口」退出 2（2026-10-17 v1.0.1 实测）。
_SPEC = importlib.util.spec_from_file_location("_engine_main_for_test",
                                              REPO / "Engine" / "engine_main.py")
_DISP = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_DISP)
check("(b) 外壳入口表与**打包 dispatcher** 的 `ENTRIES` 逐项一致"
      "（少一项 = 那个标签页在用户机器上直接退出 2）",
      set(E.ENTRIES) == set(_DISP.ENTRIES),
      f"外壳 {len(E.ENTRIES)} 个 {E.ENTRIES} ／ dispatcher {len(_DISP.ENTRIES)} 个 {_DISP.ENTRIES}")
check("(b) 十个入口名字都在表里（写错名字 = 同上；第 132 条起含引用库 bibdb.py）",
      set(E.ENTRIES) == {"convert.py", "checkbib.py", "mergepdf.py",
                         "cleantemp.py", "compileall.py", "proofcheck.py",
                         "infocheck.py", "toc.py", "publish.py", "bibdb.py"},
      f"{E.ENTRIES}")

c7 = E.infocheck_call(folder=r"E:\\期\\90", preview=True)
check("(b) 检查tex信息：`--folder` + `--preview`（界面表格用）",
      c7.entry == "infocheck.py" and "--folder" in c7.argv and "--preview" in c7.argv)
check("(b) 反例：正式检查时**不**带 `--preview`（要落 txt/json 产物）",
      "--preview" not in E.infocheck_call(folder="/tmp/x").argv)
c8 = E.toc_call(report=r"C:\\出稿\\信息检查2026.txt", template=r"C:\\期\\上一期")
check("(b) 生成目录：`--report` + `--template`",
      c8.entry == "toc.py" and "--report" in c8.argv and "--template" in c8.argv)
check("(b) 生成目录：模板留空 → **不传** `--template`（交给引擎找上一期）",
      "--template" not in E.toc_call(report="/tmp/x.txt").argv)
check("(b) 目录预览：`--preview`（篇目清单，不落文件）",
      "--preview" in E.toc_preview_call(report="/tmp/x.txt").argv)

c4 = E.cleantemp_call(folder=r"E:\期\2026年第5期(90)", dry_run=True)
check("(b) 删除临时文件：`--folder` + `--dry-run`",
      c4.entry == "cleantemp.py" and "--folder" in c4.argv and "--dry-run" in c4.argv)
check("(b) 反例：不勾预演时**不出现** `--dry-run`",
      "--dry-run" not in E.cleantemp_call(folder="/tmp/x").argv)
c5 = E.compileall_call(folder=r"E:\期\90", only="01,03")
check("(b) 编译全部：`--folder` + `--only 01,03`",
      c5.entry == "compileall.py" and "--only" in c5.argv and "01,03" in c5.argv)
check("(b) 编译全部：`--only` 留空时不传这个参数",
      "--only" not in E.compileall_call(folder="/tmp/x").argv)
c6 = E.proofcheck_call(tex=r"C:\出稿\Demo.tex", fix=True, lang="zh")
check("(b) 一校核查：`--tex` + `--fix` + `--lang zh`",
      c6.entry == "proofcheck.py" and "--tex" in c6.argv and "--fix" in c6.argv
      and "zh" in c6.argv)
check("(b) 一校核查：语种「自动」时不传 `--lang`（交给引擎判）",
      "--lang" not in E.proofcheck_call(tex="/tmp/x.tex").argv)

# ─────────────────────────────────────────────────────────────────────────
# H1／H3（2026-10-17 审计的两条高危）在**桥梁层**的回归守卫 —— 不需窗口即可跑
_c_full = E.convert_call(source="a.docx", out="/tmp/o", author="陈波", name="Bo_Chen",
                         vol="19", issue="5", year="2026", pages="137-152",
                         received="2025-09-19", editor="何杨", grants="国社科基金")
check("(b) convert 能把 `--author/--name` 传下去（正文读不出姓名时，这是这类稿的唯一活路）",
      "--author" in _c_full.argv and "--name" in _c_full.argv
      and _c_full.argv[_c_full.argv.index("--author") + 1] == "陈波", _c_full.display[:100])
check("(b) 编辑侧出版信息七项都能传（缺了 PDF 页眉会是「xxxx 年第 x 期」占位）",
      all(f in _c_full.argv for f in ("--vol", "--issue", "--year", "--pages",
                                      "--received", "--editor", "--grants")), _c_full.display[:100])
# 判据取**不变量的原话**：1 常常是"有结果"，外壳不许把它显示成**错误**（打分 ❌）。
# 注意 `compileall` 的 rc=1 文案里出现"编译失败"是**准确**的（有文章编译失败），
# 所以这里不能简单地禁"失败"两个字，而是禁"把它当程序失败"的那一档。
# 完成横幅的"产物路径"是从契约行里抠出来的（界面第 1 件 UI 改进）。
# 真实期目录名里带**半角括号**（`2026年第5期(90)`），靠"路径字符集"截断会截错 —— 用真实形状钉住。
_ap_dir = tempfile.mkdtemp(prefix="slcn_art_")
_ap_iss = os.path.join(_ap_dir, "2026年第5期(99)")
os.makedirs(_ap_iss, exist_ok=True)
_ap_file = os.path.join(_ap_iss, "信息检查.txt")
with open(_ap_file, "w", encoding="utf-8") as fh:
    fh.write("x")
_ap_pdf = os.path.join(_ap_dir, "合并.pdf")
with open(_ap_pdf, "wb") as fh:
    fh.write(b"%PDF-1.4\n")
_ap_cases = [
    (f"完成。产物目录：{_ap_iss}", _ap_iss),
    (f"完成。检查 2 篇（有 tex 2；不一致 0 项）（报告：{_ap_file}）", _ap_file),
    (f"完成。合并文件：{_ap_pdf}（共 176 页）", _ap_pdf),
    (f"完成。生成目录 2 份（{_ap_dir}）", _ap_dir),
    ("没有路径的一行", ""),
    # 反例：路径不存在时**不许**退回"存在的父目录"（`/tmp` 是存在的！）
    (f"完成。产物目录：{_ap_dir}/不存在的子目录_zzz", ""),
]
check("(b) 能从契约行里抠出产物路径（含带括号的期目录名 / 尾部注释 / 全角右括号）",
      all(E.artifact_path(line) == want for line, want in _ap_cases),
      [E.artifact_path(line) for line, _w in _ap_cases])
shutil.rmtree(_ap_dir, ignore_errors=True)
check("(b) cleantemp 三种模式：老行为（直接删）/ 移入回收 / 撤销",
      "--trash" not in E.cleantemp_call(folder="/x").argv
      and "--trash" in E.cleantemp_call(folder="/x", trash=True).argv
      and "--restore" in E.cleantemp_call(folder="/x", restore_trash="/x/.toolbox-trash/1").argv
      and "--dry-run" in E.cleantemp_call(folder="/x", dry_run=True, trash=True).argv)
# U7：与 mac 对等的参数（引擎早支持，界面从前没传）
_c_cb = E.checkbib_call(returned="/交回.bib", original="/原件.bib", tex="/正文.tex", out="/产物")
check("(b) 文献回读校验能传 `--original/--tex/--out`（从前界面没传，报告里一直写「未提供」）",
      "--original" in _c_cb.argv and "--tex" in _c_cb.argv and "--out" in _c_cb.argv,
      _c_cb.display[:100])
check("(b) 合并页有 `--detect` 预览（与 mac 对等；默认不跑）",
      "--detect" in E.merge_call(folder="/期", detect=True).argv
      and "--detect" not in E.merge_call(folder="/期").argv)
check("(b) 设置页有「检索证据」开关（P2，第 72 条）：勾选状态进 cfg、保存时写回",
      "v_searchrefs" in (SHELL / "main.py").read_text(encoding="utf-8")
      and 'api"]["searchRefs"] = bool(self.v_searchrefs.get())'
          in (SHELL / "main.py").read_text(encoding="utf-8")
      and "DeepSeek 独立 Search API" in (SHELL / "main.py").read_text(encoding="utf-8"))
check("(b) 运行日志里的「最后一行」被当作契约行送去抠路径",
      "_on_line" in (SHELL / "main.py").read_text(encoding="utf-8")
      and "E.artifact_path(" in (SHELL / "main.py").read_text(encoding="utf-8"))
check("(b) **退出码 1 不许打成 ❌ 失败**（INVARIANTS 三.2：1 常常是「有结果」）",
      all(not E.result_text(e, 1).startswith("❌")
          for e in ("proofcheck.py", "checkbib.py", "compileall.py", "toc.py", "convert.py"))
      and "不是程序出错" in E.result_text("proofcheck.py", 1)
      and E.result_text("proofcheck.py", 0).startswith("✅")
      and E.result_text("proofcheck.py", 2).startswith("❌"),
      f"0→{E.result_text('proofcheck.py', 0)}｜1→{E.result_text('proofcheck.py', 1)}"
      f"｜2→{E.result_text('proofcheck.py', 2)}")

print("\n── (c) 环境变量：UTF-8 + 无缓冲 + vendor + **密钥不进 argv** ──")
API = {"base": "https://api.example.com", "key": "sk-1234567890", "model": "m-1",
       "thinking": "off", "maxTokens": "4096", "webSearch": True, "searchRefs": True}
env = E.env_for_run(API)
check("(c) `PYTHONUNBUFFERED=1`（否则界面看着像卡死）", env.get("PYTHONUNBUFFERED") == "1")
check("(c) `PYTHONIOENCODING=utf-8`（Windows 控制台默认 cp936，中文会乱码）",
      env.get("PYTHONIOENCODING") == "utf-8")
check("(c) `PYTHONPATH` 带上引擎自己的 vendor（pypdf/pypinyin/lxml）",
      os.path.join(eng, "vendor") in env.get("PYTHONPATH", ""), env.get("PYTHONPATH", "")[:80])
check("(c) 密钥进了环境变量 `SLCN_LLM_KEY`", env.get("SLCN_LLM_KEY") == "sk-1234567890")
check("(c) 其余 AI 字段也对得上（base/model/thinking/maxTokens/websearch）",
      env.get("SLCN_LLM_BASE") == "https://api.example.com"
      and env.get("SLCN_LLM_MODEL") == "m-1"
      and env.get("SLCN_LLM_THINKING") == "off"
      and env.get("SLCN_LLM_MAX_TOKENS") == "4096"
      and env.get("SLCN_LLM_WEB_SEARCH") == "1"
      and env.get("SLCN_SEARCH_REFS") == "1")
for call in (c, c2, c3):
    check(f"(c) **密钥绝不出现在 argv 里**（{call.entry}）",
          "sk-1234567890" not in call.display and "SLCN_LLM_KEY" not in call.display)
check("(c) 没填的 AI 字段不设环境变量（不许塞空串把引擎默认值顶掉）",
      "SLCN_LLM_SEARCH_TOOL" not in E.env_for_run({"base": "x"}))
check("(c) 检索证据开关默认关：不设 SLCN_SEARCH_REFS（与旧版环境变量逐字节相同）",
      "SLCN_SEARCH_REFS" not in E.env_for_run({"base": "x", "webSearch": True})
      and S.DEFAULTS["api"].get("searchRefs") is False)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) 配置读写（密钥只落本地、显示打码）──")
tmp = tempfile.mkdtemp(prefix="slcn_win_cfg_")
saved_dir = os.environ.get("APPDATA")
try:
    os.environ["APPDATA"] = tmp          # Windows 分支：%APPDATA%\EditToolBox\settings.json
    if sys.platform.startswith("win"):
        p = S.config_path()
        check("(d) 配置路径在 %APPDATA%\\EditToolBox 下",
              p.startswith(tmp) and p.endswith(os.path.join("EditToolBox", "settings.json")), p)
        cfg = S.load()
        check("(d) 文件不存在时给默认值（不抛）", cfg["api"]["key"] == "" and cfg["options"]["convertCompile"])
        cfg["api"]["key"] = "sk-abcdefgh"
        written = S.save(cfg)
        check("(d) 保存后能读回来", S.load()["api"]["key"] == "sk-abcdefgh", written)
        mode = os.stat(written).st_mode & 0o777
        # ⚠️ Windows **没有 POSIX 权限位**：`os.chmod(0o600)` 在那里只退化成「清只读位」，
        #    `os.stat().st_mode & 0o777` 恒为 0o666 —— 在这里断言 `== 0o600` 只会永远红
        #    （2026-10-16 实测：windows-latest 上「失败 1 项：(d) 权限 0600」，也正是
        #    第 103 条修完之后暴露出来的**下一个** Windows 缺陷（本条第 104 条））。
        #    真正的保护来自**位置**：`%APPDATA%\<App>\` 的 ACL 默认只给本用户 + SYSTEM +
        #    Administrators，别的标准用户读不到 —— 这就是 Windows 存放密钥的通行做法。
        #    所以这里断言两件**在 Windows 上真的成立**的事，而不是假装有 0600
        #    （第 62 条：假的保证比没有保证更糟）。
        check("(d) 配置写在**每用户**目录（%APPDATA%）里 —— Windows 的保护来自那里的 ACL，"
              "没有 POSIX 权限位可言",
              os.path.isabs(written)
              and os.path.normcase(written).startswith(os.path.normcase(tmp) + os.sep)
              and os.path.basename(written) == "settings.json", written)
        check("(d) 落盘那次 chmod 不该把配置**锁成只读**（只读目标会让 os.replace 失败 → "
              "配置再也存不进去，正是第 62 条那类「用户以为存上了、其实没有」）",
              bool(mode & 0o200), oct(mode))
        open(written, "w", encoding="utf-8").write("{ 坏掉的 json")
        check("(d) 配置坏了 → 回默认值，不让界面起不来", S.load()["api"]["key"] == "")
    else:
        print("SKIP  (d) 配置目录分支只在 Windows 上走；mac 上按 Library/Application Support 走")
        cfg = S.load()
        check("(d) mac 上也能算出配置路径（开发时看得到文件在哪）",
              S.config_path().endswith("settings.json"), S.config_path())
finally:
    if saved_dir is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = saved_dir
    shutil.rmtree(tmp, ignore_errors=True)

check("(d) `mask_key` 只留前 4 位", S.mask_key("sk-abcdefgh") == "sk-a…", S.mask_key("sk-abcdefgh"))
check("(d) 空 key 打码后还是空（界面不留 `…` 这种迷惑占位）", S.mask_key("") == "")

# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) 真跑一次：桥梁 → mergepdf.py（引擎的实时日志要能收到）──")
from pypdf import PdfWriter  # noqa: E402


def mk(path, pages):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=300)
    with open(path, "wb") as fh:
        w.write(fh)


work = tempfile.mkdtemp(prefix="slcn_win_run_")
mk(f"{work}/扉页.pdf", 1)
mk(f"{work}/000 目录/Chinese Content.pdf", 1)
mk(f"{work}/01 A_Author/A_Author.pdf", 2)
out = os.path.join(work, "merged.pdf")
lines: list[str] = []
call = E.merge_call(folder=work, out=out, editors_page=False)
code = E.run_call(call, on_line=lines.append)
check("(e) 退出码 0", code == 0, f"rc={code}")
check("(e) 逐行收到了日志（不是攒到最后一次性来）", len(lines) >= 3, f"{len(lines)} 行")
check("(e) 收到契约行 `完成。合并文件：…（共 N 页）`",
      any(ln.startswith("完成。合并文件：") and "（共 4 页）" in ln for ln in lines),
      f"{[ln for ln in lines if ln.startswith('完成')]}")
check("(e) 产物真的写出来了", os.path.exists(out))
check("(e) 中文日志没有乱码（收到的是正常汉字）",
      any("文件夹路径" in ln for ln in lines), f"{lines[:3]}")

# 真跑一次「删除临时文件」（造两个临时文件，验证桥接端到端）
import pathlib as _pl  # noqa: E402
_clean_dir = tempfile.mkdtemp(prefix="slcn_win_clean_")
for _n in ("a.aux", "b.synctex.gz", "keep.tex", "keep.log"):
    open(os.path.join(_clean_dir, _n), "w", encoding="utf-8").write("x")
_lines2: list[str] = []
_rc = E.run_call(E.cleantemp_call(folder=_clean_dir), on_line=_lines2.append)
check("(e) 桥接跑「删除临时文件」：退出码 0、契约行给出删了几个",
      _rc == 0 and any("完成。已删除 2 个" in ln for ln in _lines2),
      f"rc={_rc} {[ln for ln in _lines2 if ln.startswith('完成')]}")
check("(e) `.tex` / `.log` 没被删（与 mac 版同一口径）",
      os.path.exists(os.path.join(_clean_dir, "keep.tex"))
      and os.path.exists(os.path.join(_clean_dir, "keep.log")))
shutil.rmtree(_clean_dir, ignore_errors=True)

# 真跑一次「检查tex信息」的预览：造一期合成稿件 → 引擎 → 解 JSON 填表格
import json as _json  # noqa: E402
_ic_dir = tempfile.mkdtemp(prefix="slcn_win_ic_")
_art = os.path.join(_ic_dir, "01 Lu_Wang")
os.makedirs(_art, exist_ok=True)
open(os.path.join(_art, "Lu_Wang.tex"), "w", encoding="utf-8").write(
    "\\documentclass[b5paper,10.5t,onecolumn,twoside]{ctexart}\n"
    "\\newcommand{\\myvolnumber}{19}      % 输入卷号\n"
    "\\newcommand{\\myissnumber}{5}       % 输入当年期号\n"
    "\\newcommand{\\myyearnumber}{2026}   % 输入出版年份\n"
    "\\newcommand{\\mystartpage}{1}      % 输入起始页码\n"
    "\\newcommand{\\myendpage}{16}       % 输入终止页码\n"
    "\\newcommand{\\mypages}{16}         % 输入页数\n"
    "\\newcommand{\\mytitle}             % 请输入论文题目\n"
    "{论雅辛之谜}\n"
    "\\newcommand{\\myfirstauthor}       % 请输入第一作者姓名\n"
    "{\\kaishu 陆王}\n")
_lines3: list[str] = []
_rc3 = E.run_call(E.infocheck_call(folder=_ic_dir, preview=True), on_line=_lines3.append)
_payload = None
for _ln in _lines3:
    if _ln.strip().startswith("{"):
        try:
            _payload = _json.loads(_ln)
        except ValueError:
            pass
check("(e) 桥接跑「检查tex信息 --preview」：退出码 0、能解出 JSON",
      _rc3 == 0 and isinstance(_payload, dict), f"rc={_rc3}")
check("(e) 预览**没有落文件**（界面只是看一眼表格）",
      not [f for f in os.listdir(_ic_dir) if f.startswith("信息检查")],
      f"{os.listdir(_ic_dir)}")
if isinstance(_payload, dict):
    _a = (_payload.get("articles") or [{}])[0]
    check("(e) 预览里字段齐全（卷/期/年/题目/作者）",
          _a.get("volume") == "19" and _a.get("issue") == "5" and _a.get("year") == "2026"
          and "论雅辛" in (_a.get("chineseTitle") or "") and "陆王" in (_a.get("firstAuthorChinese") or ""),
          f"{_a.get('volume')}/{_a.get('issue')}/{_a.get('year')} {_a.get('chineseTitle')}")
shutil.rmtree(_ic_dir, ignore_errors=True)

bad = E.EngineCall([sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"], "x", "假入口")
check("(e) 非 0 退出码如实返回（界面靠它判成败）", E.run_call(bad, on_line=lines.append) == 3)
check("(e) `on_proc` 能拿到进程对象（「停止」按钮要用）",
      (lambda box: (E.run_call(bad, on_line=lambda _: None, on_proc=lambda p: box.append(p)),
                    bool(box))[1])([]) is True)

shutil.rmtree(work, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (f) 界面文件：语法/导入检查（不开窗口）──")
main_py = SHELL / "main.py"
src = main_py.read_text(encoding="utf-8")
# 第 132 条：引用库 5 页在独立文件（main.py 已经 1300+ 行）；它同样属于「界面」，
# 静态守卫必须一起扫 —— 只扫 main.py 会漏掉新文件里的经典控件/写死颜色。
ui_py = SHELL / "bibdb_ui.py"
ui_src = ui_py.read_text(encoding="utf-8") if ui_py.exists() else ""
all_src = src + "\n" + ui_src
import ast  # noqa: E402
ast.parse(src)
check("(f) `main.py` 语法正确", True)
check("(f) 引用库界面文件 `bibdb_ui.py` 存在且语法正确（第 132 条拆文件）",
      bool(ui_src) and (ast.parse(ui_src) or True))
check("(f) `bibdb_ui.py` 里 5 个页面类都在（库概况/查库/入藏/待裁决/引用库设置）",
      all(f"class BibDb{name}Page(BibDbPage)" in ui_src
          for name in ("Stats", "Query", "Import", "Review", "Settings")),
      [name for name in ("Stats", "Query", "Import", "Review", "Settings")
       if f"class BibDb{name}Page(BibDbPage)" not in ui_src])
check("(f) `main.py` 只建对象、不复制引用库页面代码（5 页都从 `bibdb_ui` 建）",
      all(f"B.BibDb{name}Page(" in src
          for name in ("Stats", "Query", "Import", "Review", "Settings")))
check("(f) 引用库界面把引擎调用委托给 `engine.py`（出现 E.bibdb_*/E.run_call，"
      "不自己拼 bibdb.py 命令行）",
      "E.bibdb_stats_call(" in ui_src and "E.bibdb_query_call(" in ui_src
      and "E.bibdb_import_bib_call(" in ui_src and "E.bibdb_report_call(" in ui_src
      and "E.run_call(" in ui_src and "E.parse_json_lines(" in ui_src
      and "pypdf" not in ui_src,
      "")
check("(f) 界面里**没有**复制引擎逻辑（不 import pypdf / 不自己拼 PDF）",
      "pypdf" not in src and "PdfWriter" not in src)
check("(f) 界面把逻辑都委托给 `engine.py`（八个入口各一处调用）",
      all(f"E.{fn}_call(" in src
          for fn in ("convert", "checkbib", "merge", "cleantemp", "compileall", "proofcheck",
                     "infocheck", "toc")), "")
check("(f) 保留了原版的中文交互（浏览/运行/停止 + 底部日志框）",
      all(k in src for k in ("浏览", "运行", "停止", "运行日志")))
check("(f) 界面线程与工作线程分开（Tkinter 不是线程安全的，日志靠队列回传）",
      "threading.Thread" in src and "queue.Queue" in src and "after(" in src)
# ⚠️ 下面三条是 2026-10-17 审计抓到的真缺陷的守卫 —— 从前的"守卫"是假的
#    （例如只断言 `E.infocheck_call(` 在 main.py 里出现过，而 `show_report` 是死代码）。
check("(f) 「检查tex信息」的表格**真的有人填**（`show_report` 从前全仓 0 处调用 = 表格永远空）",
      src.count("show_report(") >= 2 and "_info_report_payload" in src,
      f"show_report 出现 {src.count('show_report(')} 次")
check("(f) 初排转换页有「输出名 / 作者」输入框（缺了这类 LaTeX 稿整页做不了）",
      "输出名" in src and "v_author" in src and "v_name" in src)
check("(f) 「删除临时文件」是**先预览再移入回收目录、且可撤销**（UI 方案第 3 件）",
      "confirm_trash" in src and "undo_btn" in src and "撤销清理" in src
      and "--trash" in (SHELL / "engine.py").read_text(encoding="utf-8"))
check("(f) 停止走**整棵进程树**（只 terminate 直接子进程时，xelatex 会继续写产物目录）",
      "E.kill_tree(" in src and "def kill_tree" in (SHELL / "engine.py").read_text(encoding="utf-8"))

# ── U4 / U5（2026-10-17 第十五批）：日志分层 + ttk 铺满的源码级守卫 ──
#    真窗口那一步在 (g)/(h)；这里守「别悄悄退回经典控件/写死颜色/把完整日志改丢」。
_theme_src = (SHELL / "theme.py").read_text(encoding="utf-8")
# ⚠️ 用 `(?<![\w.])` 卡住前缀：裸字符串 `"tk.Button("` 会命中 `ttk.Button(` 里的那几个字符，
#    于是守卫永远是绿的（假保证）。2026-10-17 第一版就这么写过，当场被自己抓到。
_classic = re.findall(r"(?<![\w.])tk\.(?:Button|Entry|Checkbutton)\(", all_src)
check("(f) 没有 Tk 经典控件按钮/输入框/勾选框了（clam 只管得到 ttk 控件的外观；"
      "main.py 与 bibdb_ui.py 一起扫）",
      not _classic, f"还有 {len(_classic)} 处")
check("(f) `tk.Listbox` 保留（Tk 无 ttk 对等物）但色板取自 `theme.style_listbox`",
      "tk.Listbox(" in src and "theme.style_listbox(" in src
      and "def style_listbox" in _theme_src)
check("(f) 日志框 `tk.Text` 保留，颜色/字体取自 theme（bg 白、等宽字体）",
      "tk.Text(" in src and 'theme.COLOR["text_bg"]' in src and "theme.mono_font()" in src)
check("(f) 界面文件里**没有**写死的颜色（#555/#666/#b00 之类）—— 一律 theme.COLOR[...]",
      not re.findall(
          r'(?:fg|bg|foreground|background|insertbackground|selectbackground)="#[0-9a-fA-F]{3,6}"',
          all_src))
check("(f) U4 接线：`is_summary_line` / `_render_log` / 摘要·原始 / `logShowRaw` / 空状态占位",
      "E.is_summary_line(" in src and "_render_log" in src and "Toolbutton" in src
      and "摘要" in src and "原始" in src and "logShowRaw" in src
      and "运行任务后，日志会显示在这里" in src)
check("(f) 复制/保存一律给**完整日志**（含折叠行）：不读日志控件，用 `_full_log_text()`",
      src.count("self._full_log_text()") >= 2 and "self.output.get(" not in src
      and "clipboard_append(self.output" not in src)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (g) 真开一次窗口（控件参数写错只有这一步抓得到）──")


def _tk_python() -> str | None:
    """找一个**带 tkinter** 的解释器：优先当前解释器，其次 Apple 自带的 3.9。"""
    for cand in (sys.executable, "/usr/bin/python3"):
        try:
            r = subprocess.run([cand, "-c", "import tkinter"],
                               capture_output=True, timeout=60)
            if r.returncode == 0:
                return cand
        except Exception:  # noqa: BLE001
            continue
    return None


import subprocess  # noqa: E402
import textwrap  # noqa: E402

tkpy = _tk_python()
win_out = ""          # (g) 子进程的 stdout；(h) 组的「摘要/原始」断言要在这里找证据
if tkpy is None:
    print("SKIP  (g) 本机没有带 tkinter 的解释器（Windows 上跑得到）")
else:
    # 给 (g) 用的合成期目录：一篇文章的 tex（不含 PDF —— 页数那条另有诚实化断言）
    gdir = tempfile.mkdtemp(prefix="slcn_bridge_issue_")
    # 给 (g) 用的临时配置目录：U4 切换「摘要/原始」会写 settings.json，别碰开发机上的真文件
    gcfg = tempfile.mkdtemp(prefix="slcn_bridge_cfg_")
    gart = os.path.join(gdir, "01 A_Author")
    os.makedirs(gart, exist_ok=True)
    with open(os.path.join(gart, "A_Author.tex"), "w", encoding="utf-8") as fh:
        fh.write("\\documentclass{article}\n\\begin{document}\n"
                 "\\myvolnumber{19}\\myissnumber{5}\\mypubyear{2026}\n"
                 "\\title{测试题名}\\author{张三}\n\\maketitle\n正文。\n\\end{document}\n")

    prog = textwrap.dedent("""
        import json, os, sys, time
        sys.path.insert(0, os.getcwd())
        import settings as S
        # U4 的「摘要/原始」切换会**写配置**：把配置路径指到临时目录，
        # 别动开发机上真实的 settings.json（测试不许有副作用）。
        _cfg = os.environ.get("SLCN_BRIDGE_CFG", "")
        if _cfg:
            S.config_path = lambda: os.path.join(_cfg, "settings.json")
        import main as M
        app = M.MainWindow()
        # 侧边栏（UI 方案候选 B）：`tab_texts()` 是替代 Notebook 的稳定接口
        tabs = app.tab_texts()
        groups = [app.nav.item(x, "text") for x in app.nav.get_children()]
        print("侧栏=" + "|".join(groups))
        app.show_tab(4)
        print("切页=" + (app.nav.selection()[0] if app.nav.selection() else ""))
        app.log("自检：写一行日志")
        app.update()
        # ⚠️ **真跑一次**「检查tex信息」：走界面真实代码路径（`start` → 工作线程 → 队列 → 填表）。
        #    这条守的是 H2：从前 `show_report` 是死代码、表格永远是「（还没检查）」，
        #    而静态断言（只查 `E.infocheck_call(` 出现过）**根本抓不到**。
        d = os.environ.get("SLCN_BRIDGE_TEST_DIR", "")
        if d:
            app.info_tab.entry.insert(0, d)
            app.start(app.info_tab)
            t0 = time.time()
            while app._busy and time.time() - t0 < 90:
                app.update()
                time.sleep(0.05)
            app.update()
            rows = app.info_tab.table.get_children()
            lines = [l for l in app.output.get("1.0", "end").splitlines() if l.strip()]
            print("表格行数=%d" % len(rows))
            print("结束行=" + (lines[-1] if lines else ""))
            print("顶部提示=" + app.info_tab.summary.cget("text"))
        # ── U4（(h) 组的真窗口证据）：摘要 / 原始两层 ──
        print("默认模式=%d" % int(app.v_log_raw.get()))
        app.log("原始层探针：xelatex 的细节行")     # 非摘要行（无标记词、无阶段前缀）
        app.log("✅ 完成。")                        # 摘要行
        app.update()
        _sum = app.output.get("1.0", "end")
        print("摘要含探针=%d" % int("原始层探针" in _sum))
        print("摘要含完成=%d" % int("✅ 完成。" in _sum))
        print("折叠标签=" + app.folded_label.cget("text"))
        print("全文含探针=%d" % int("原始层探针" in app._full_log_text()))
        app.v_log_raw.set(True)
        app._on_log_mode()                          # 切「原始」：重画 + 把偏好写回配置
        app.update()
        _raw = app.output.get("1.0", "end")
        print("原始含探针=%d" % int("原始层探针" in _raw))
        print("原始含完成=%d" % int("✅ 完成。" in _raw))
        print("原始折叠标签=" + app.folded_label.cget("text"))
        _cp = S.config_path()
        print("偏好落盘=%d" % int(os.path.isfile(_cp)
                                  and bool(json.load(open(_cp, encoding="utf-8")).get("logShowRaw"))))
        # 空状态（观察③）：清空后恢复灰占位；占位**不进**复制/保存的内容
        app.clear_log()
        app.update()
        print("清空后占位=%d" % int(M.LOG_PLACEHOLDER in app.output.get("1.0", "end")))
        print("占位不进全文=%d" % int(M.LOG_PLACEHOLDER not in app._full_log_text()))
        app.log("✅ 清空后的第一条")
        app.update()
        print("占位消失=%d" % int(M.LOG_PLACEHOLDER not in app.output.get("1.0", "end")))
        # ── 第 132 条：引用库 5 页（真窗口证据）──
        # 只做**不跑引擎**的交互：切到「引用库设置」页（on_activate 只做文件系统探测）、
        # 改两项设置并保存，验证 (a) 页能建出来 (b) 设置真的落到 settings.json
        # (c) 非法上限不落盘。跑引擎的读库/查库在 (i) 组用临时库验（不在窗口里依赖真实库）。
        app.show_tab(13)
        app.update()
        bs = app.bibdb_settings_tab
        bs.max_var.set("0")
        bs.notes_var.set("/绝对不存在的笔记仓_zzz")
        bs.save()
        app.update()
        _cfg2 = json.load(open(S.config_path(), encoding="utf-8"))
        print("引用库设置落盘=%d" % int(
            _cfg2.get("bibdb", {}).get("verifyMax") == 0
            and str(_cfg2.get("notesRepoPath", "")).endswith("绝对不存在的笔记仓_zzz")))
        bs.max_var.set("不是整数")
        bs.save()
        app.update()
        _cfg3 = json.load(open(S.config_path(), encoding="utf-8"))
        print("非法上限不落盘=%d" % int(_cfg3.get("bibdb", {}).get("verifyMax") == 0))
        print("引用库页=" + "|".join(tabs[9:14]))
        print("引用库侧栏=" + "|".join(
            app.nav.item(x, "text") for x in app.nav.get_children()))
        app.destroy()
        print("窗口建成")
        print("标签页：" + "|".join(tabs))
    """)
    env2 = dict(os.environ)
    env2["PYTHONPATH"] = str(REPO / "Engine" / "vendor")
    env2["SLCN_BRIDGE_TEST_DIR"] = gdir
    env2["SLCN_BRIDGE_CFG"] = gcfg
    r = subprocess.run([tkpy, "-"], input=prog, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env2, cwd=str(SHELL), timeout=180)
    out = r.stdout or ""
    if r.returncode != 0 and ("no display" in (r.stderr or "") or "TclError" in (r.stderr or "")):
        print(f"SKIP  (g) 没有可用显示器/窗口服务：{(r.stderr or '').strip().splitlines()[-1:]}")
    else:
        win_out = out                       # (h) 组还要在这些输出里找 U4 的证据
        check("(g) 真开一次窗口：控件全部建成、写日志、正常退出",
              r.returncode == 0 and "窗口建成" in out,
              f"rc={r.returncode} {(r.stderr or '').strip()[-160:]}")
        check("(g) 十五个页都在（侧边栏顺序：单篇 4 + 整期 5 + 引用库 5 + 设置）",
              "初排转换|文献回读校验|合并文件|一校核查|检查tex信息|生成目录|编译全部|"
              "删除临时文件|本期出刊|库概况|查库|入藏|待裁决|引用库设置|设置"
              in out, out.strip()[-220:])
        check("(g) **分组侧边栏**：两层结构（单篇稿件 / 整期装配 / 引用库 / 设置），"
              "不是 14 个平级标签",
              "侧栏=单篇稿件|整期装配|引用库|设置" in out,
              [l for l in out.splitlines() if l.startswith(("侧栏", "引用库侧栏"))])
        check("(g) 引用库 5 页在侧栏里（顺序：库概况/查库/入藏/待裁决/引用库设置）",
              "引用库页=库概况|查库|入藏|待裁决|引用库设置" in out,
              [l for l in out.splitlines() if l.startswith("引用库页")])
        check("(g) 切页会同步侧栏选中态", "切页=tab:4" in out,
              [l for l in out.splitlines() if l.startswith("切页")])
        check("(g) 「引用库设置」页能把设置写进 settings.json（第 132 条）",
              "引用库设置落盘=1" in out,
              [l for l in out.splitlines() if l.startswith("引用库设置落盘")])
        check("(g) 非法核实上限**不落盘**（界面红字提示，旧值保留）",
              "非法上限不落盘=1" in out,
              [l for l in out.splitlines() if l.startswith("非法上限不落盘")])
        check("(g) **真跑一次「检查tex信息」→ 表格被填上**（H2 回归守卫：修之前恒为 0 行）",
              "表格行数=1" in out,
              [l for l in out.splitlines() if l.startswith("表格行数")])
        check("(g) 结束行是「✅ 完成（退出码 0）」（H3：文案由 `E.result_text` 统一给；"
              "运行中的行头会带相对时间戳 `+0.5s`，所以按包含判）",
              any(l.startswith("结束行=") and "✅ 完成（退出码 0）" in l
                  for l in out.splitlines()),
              [l for l in out.splitlines() if l.startswith("结束行")])
        check("(g) 没有可比对的 PDF 页数时，顶部提示**不许**说「相符」（不许假绿）",
              "未验证" in out or "相符" not in out,
              [l for l in out.splitlines() if l.startswith("顶部提示")])
    shutil.rmtree(gdir, ignore_errors=True)
    shutil.rmtree(gcfg, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (h) 日志分层（U4）：跨平台夹具 + 真窗口里能切「摘要 / 原始」──")
_cases_file = REPO / "tools" / "log_cases.json"
_cases: list[dict] = []
try:
    _cases = json.loads(_cases_file.read_text(encoding="utf-8")).get("cases") or []
except (OSError, ValueError) as _e:
    print(f"SKIP  (h) 读不到跨平台夹具 {_cases_file}：{_e}")
check("(h) 跨平台夹具 `tools/log_cases.json` 至少有 30 条（Swift / Python 跑同一份；"
      "只增不改，增加用例时两侧仍须全过）",
      len(_cases) >= 30, f"{len(_cases)} 条")
_mismatch = [(c.get("line"), c.get("summary"), E.is_summary_line(str(c.get("line", ""))))
             for c in _cases
             if E.is_summary_line(str(c.get("line", ""))) != bool(c.get("summary"))]
check("(h) `engine.is_summary_line` 逐条通过夹具的 30 条（两侧词表不许悄悄分叉）",
      bool(_cases) and not _mismatch,
      f"不符 {len(_mismatch)} 条：{_mismatch[:3]}")
check("(h) 空行 / 纯空白**不算**摘要（否则默认摘要视图里全是空行）",
      E.is_summary_line("") is False and E.is_summary_line("   ") is False
      and E.is_summary_line("\n") is False)


def _marker(name: str) -> str:
    """取 (g) 子进程打的一行 `name=value`（没有就返回空串）。"""
    for line in win_out.splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    return ""


if not _marker("默认模式"):
    print("SKIP  (h) 真窗口那一步没跑（本机没有 tkinter / 窗口服务起不来）")
    print("      —— 夹具那 3 条仍然有效；Windows 上这组会全跑")
else:
    check("(h) 默认是**摘要**（原始层折叠）：`logShowRaw` 缺省为 False",
          _marker("默认模式") == "0", _marker("默认模式"))
    check("(h) 摘要视图只画摘要行（非摘要的引擎原文不显示）",
          _marker("摘要含探针") == "0", _marker("摘要含探针"))
    check("(h) 摘要行在摘要视图里照常显示", _marker("摘要含完成") == "1", _marker("摘要含完成"))
    check("(h) 工具条写明「已折叠 N 行原始日志」",
          "已折叠" in _marker("折叠标签") and "原始日志" in _marker("折叠标签"),
          _marker("折叠标签"))
    check("(h) 复制 / 保存给的是**完整日志**（摘要模式下也含折叠的原始行）",
          _marker("全文含探针") == "1", _marker("全文含探针"))
    check("(h) 切到「原始」后视图含全部行",
          _marker("原始含探针") == "1" and _marker("原始含完成") == "1",
          f"{_marker('原始含探针')}/{_marker('原始含完成')}")
    check("(h) 原始模式不再显示折叠提示", _marker("原始折叠标签") == "",
          _marker("原始折叠标签"))
    check("(h) 切换会持久化 `logShowRaw`（下次启动沿用）", _marker("偏好落盘") == "1",
          _marker("偏好落盘"))
    check("(h) 空状态（观察③）：清空后恢复那行灰占位", _marker("清空后占位") == "1",
          _marker("清空后占位"))
    check("(h) 占位**不进**复制 / 保存的完整日志", _marker("占位不进全文") == "1",
          _marker("占位不进全文"))
    check("(h) 第一条真实日志到达时占位让位", _marker("占位消失") == "1",
          _marker("占位消失"))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (i) 引用库（第 132 条）：笔记仓探测 / 库文件 / 参数默认零变化 / CLI 冒烟 ──")

# 用**合成路径**验自动探测：真机/CI 上笔记仓可能不存在（公开仓 checkout 里没有），
# 不能只在「开发机恰好有笔记仓」时才算过 —— 下面这套目录结构任何机器都造得出来。
_probe = tempfile.mkdtemp(prefix="slcn_notes_probe_")
_prog = os.path.join(_probe, "dist", "bin")
os.makedirs(_prog, exist_ok=True)
_syn_notes = os.path.join(_probe, "EditToolBox-notes")
os.makedirs(os.path.join(_syn_notes, "cases"), exist_ok=True)
_syn_db_dir = os.path.join(_syn_notes, "bib_db")
os.makedirs(_syn_db_dir, exist_ok=True)
_syn_db = os.path.join(_syn_db_dir, "bibliography.jsonl")
with open(_syn_db, "w", encoding="utf-8") as _fh:
    _fh.write("{}\n")
with open(os.path.join(_syn_db_dir, "CONFLICTS.md"), "w", encoding="utf-8") as _fh:
    _fh.write("xyz")

check("(i) 自动探测（纯函数，合成路径）：从程序目录向上找到同级 EditToolBox-notes",
      E.detect_notes_repo(_prog) == os.path.abspath(_syn_notes),
      f"{E.detect_notes_repo(_prog)}")
_isolated = os.path.join(tempfile.mkdtemp(prefix="slcn_no_notes_"), "a", "b")
os.makedirs(_isolated, exist_ok=True)
check("(i) 自动探测：路径里没有笔记仓 → 返回 None（不猜、不静默用错目录）",
      E.detect_notes_repo(_isolated) is None, f"{E.detect_notes_repo(_isolated)}")
check("(i) 手动指定优先：有效的笔记仓路径直接用",
      E.effective_notes_repo(_syn_notes) == os.path.abspath(_syn_notes))
check("(i) 手动指定**无效** → 回落自动探测（不会拿一个引擎会拒绝的路径去覆盖）",
      E.effective_notes_repo("/绝对不存在的笔记仓_zzz") == E.detect_notes_repo())
check("(i) 笔记仓判据要求引擎真的接受：有 cases/ 或 bib_db/ 或 HANDOFF.md，"
      "空目录不算",
      E.looks_like_notes_repo(_syn_notes)
      and not E.looks_like_notes_repo(os.path.join(_probe, "随便一个空目录")))

check("(i) 库文件位置固定为 <笔记仓>/bib_db/bibliography.jsonl（引擎定义，外壳不另立规矩）",
      E.bibdb_db_path(_syn_notes) == _syn_db)
_infos = {info["name"]: info for info in E.bibdb_report_files(_syn_notes)}
check("(i) 清单文件状态（待裁决页只读展示）：存在的给字节数、缺的标不存在",
      _infos.get("CONFLICTS.md", {}).get("exists") is True
      and _infos["CONFLICTS.md"]["bytes"] == 3
      and _infos.get("DUPLICATES.md", {}).get("exists") is False,
      str({name: (info["exists"], info["bytes"]) for name, info in _infos.items()}))

# SLCN_NOTES：外壳把它传给所有引擎子进程（核实链路与 bibdb.py 都认这一个变量）
_saved_notes_env = os.environ.pop("SLCN_NOTES", None)
try:
    _env_notes = E.env_for_run({}, notes=_syn_notes)
    check("(i) `env_for_run` 把生效的笔记仓注入 `SLCN_NOTES`（引擎靠它定位引用库）",
          _env_notes.get("SLCN_NOTES") == os.path.abspath(_syn_notes),
          _env_notes.get("SLCN_NOTES", ""))
    os.environ["SLCN_NOTES"] = "/用户显式设的笔记仓"
    _env_notes2 = E.env_for_run({}, notes=_syn_notes)
    check("(i) 环境里**已经显式设了** `SLCN_NOTES` → 不覆盖（用户/CI 优先）",
          _env_notes2.get("SLCN_NOTES") == "/用户显式设的笔记仓")
finally:
    os.environ.pop("SLCN_NOTES", None)
    if _saved_notes_env is not None:
        os.environ["SLCN_NOTES"] = _saved_notes_env

check("(i) 笔记仓/引擎脚本能定位（打包或开发形态都行；找不到时界面给可读错误）",
      bool(E.bibdb_tool()), str(E.bibdb_tool()))

# 从路径推断来源期/篇目录名：判据与引擎 paths.ISSUE_RE / bibdb 作者目录正则同一口径
_infer_cases = [
    (r"D:\稿件\2026年第2期(87)\02 Kun_Xing\reference.bib", ("2026年第2期(87)", "02 Kun_Xing")),
    ("/库/2025年第6期(85)/07-A_Author/x.bib", ("2025年第6期(85)", "07-A_Author")),
    ("/库/2026年第5期(90)/main.tex", ("2026年第5期(90)", "")),
    ("/tmp/随便一个目录/x.bib", ("", "")),
    ("", ("", "")),
]
_bad_infer = [(path, want, E.infer_issue_and_slug(path))
              for path, want in _infer_cases if E.infer_issue_and_slug(path) != want]
check("(i) 从路径推断来源期/篇目录名（认不出就留空，**绝不猜**）",
      not _bad_infer, str(_bad_infer[:2]))

check("(i) 从输出里解析 JSON：取**最后一段**可解析对象（JSON 前面可能有告警行）",
      E.parse_json_lines(["[警告] 某条 bib 有语法问题", "{", '  "written": false', "}"])
      == {"written": False}
      and E.parse_json_lines(["没有 JSON"]) is None)

# ── 命令行：引用库 CLI 的参数拼装（判据仍在引擎，这里只查 argv 契约）──
_c_dry = E.bibdb_import_bib_call(bib="/a.bib", issue="2026年第1期(86)",
                                 slug="01 A_B", dry_run=True, db="/db/b.jsonl")
check("(i) 入藏单篇：`import-bib --bib … --issue … --slug … --verified --json --db … --dry-run`",
      _c_dry.entry == "bibdb.py" and "--import-bib" not in _c_dry.argv
      and "import-bib" in _c_dry.argv and "--dry-run" in _c_dry.argv
      and "--verified" in _c_dry.argv and "--issue" in _c_dry.argv
      and "--json" in _c_dry.argv and "--db" in _c_dry.argv,
      _c_dry.display[:140])
check("(i) 确认入藏（dry_run=False）**不带** `--dry-run`（这一次真的写库）",
      "--dry-run" not in E.bibdb_import_bib_call(bib="/a.bib", dry_run=False).argv)
check("(i) 查库 key/DOI 不带 `--limit`；题名模糊查询才带（且带 --bibtex/--json）",
      "--limit" not in E.bibdb_query_call("key", "Foo2020").argv
      and "--limit" not in E.bibdb_query_call("doi", "10.1/x").argv
      and "--limit" in E.bibdb_query_call("title", "A Test").argv
      and "--bibtex" in E.bibdb_query_call("key", "Foo2020").argv
      and "--json" in E.bibdb_query_call("key", "Foo2020").argv)
check("(i) 未知查库方式 → ValueError（界面不许把错参数发给引擎）",
      _raises_value_error(E.bibdb_query_call, "author", "x"))
check("(i) 重新生成清单：`report --json --db … --out …`（只写 .md/.csv，不改库）",
      "report" in E.bibdb_report_call(out_dir="/n/bib_db", db="/db/b.jsonl").argv
      and "--out" in E.bibdb_report_call(out_dir="/n/bib_db").argv)

# ── convert：**默认一个参数都不加**（旧行为逐字节不变），设过才传 ──
_legacy = E.convert_call(source="a.docx", out="/o", verify_refs=True)
_NEW_FLAGS = ("--verify-max", "--verify-plan", "--verify-all-refs", "--no-bibdb",
              "--bibdb-path")
check("(i) 默认（没碰过引用库设置）convert 命令行**不出现**任何新参数"
      "（逐字节与接入引用库之前相同）",
      all(flag not in _legacy.argv for flag in _NEW_FLAGS) and "--verify-refs" in _legacy.argv,
      _legacy.display[:120])
_full = E.convert_call(source="a.docx", out="/o", verify_refs=True, verify_max=0,
                       verify_plan=True, verify_all_refs=True, no_bibdb=True)
check("(i) 设过引用库设置才传：`--verify-plan`（替代 --verify-refs）+ "
      "`--verify-max 0` + `--verify-all-refs` + `--no-bibdb`",
      "--verify-plan" in _full.argv and "--verify-refs" not in _full.argv
      and _full.argv[_full.argv.index("--verify-max") + 1] == "0"
      and "--verify-all-refs" in _full.argv and "--no-bibdb" in _full.argv,
      _full.display[:140])
check("(i) 不执行核实（verify_refs=False）时，成本闸门参数一个都不传"
      "（引擎只会打「本次忽略」告警，外壳不该发噪声）",
      all(flag not in E.convert_call(source="a", out="/o", verify_max=0, verify_plan=True,
                                     verify_all_refs=True, no_bibdb=True).argv
          for flag in _NEW_FLAGS))
check("(i) 非法核实上限（负数/非整数）→ ValueError，不许悄悄传给引擎",
      _raises_value_error(E.convert_call, source="a", out="/o", verify_refs=True,
                          verify_max=-1)
      and _raises_value_error(E.convert_call, source="a", out="/o", verify_refs=True,
                              verify_max="120"))

# ── settings.py：引用库设置的默认值与纯逻辑 ──
check("(i) settings 默认 = 一个参数都不加：verifyMax=None / plan=all=False / lookup=True",
      S.DEFAULTS["bibdb"] == {"verifyMax": None, "verifyPlan": False,
                              "verifyAllRefs": False, "lookupEnabled": True}
      and S.DEFAULTS["notesRepoPath"] == "")
check("(i) `bibdb_convert_args({})` 返回空 dict（默认零参数）",
      S.bibdb_convert_args({}) == {}, str(S.bibdb_convert_args({})))
check("(i) `bibdb_convert_args` 把设过的值翻成 convert 关键字参数",
      S.bibdb_convert_args({"bibdb": {"verifyMax": 0, "verifyPlan": True,
                                      "verifyAllRefs": True, "lookupEnabled": False}})
      == {"verify_max": 0, "verify_plan": True, "verify_all_refs": True, "no_bibdb": True})
check("(i) 「核实上限」输入解析：留空=不传、0=不限、负数/非整数=红字且不落盘",
      S.parse_verify_max("") == (None, None)
      and S.parse_verify_max("0") == (0, None)
      and S.parse_verify_max("120") == (120, None)
      and S.parse_verify_max("-1")[0] is None and "负数" in (S.parse_verify_max("-1")[1] or "")
      and S.parse_verify_max("abc")[0] is None
      and "整数" in (S.parse_verify_max("abc")[1] or ""))
check("(i) 旧配置文件缺 bibdb/notesRepoPath 字段 → 合并默认值不炸（向后兼容）",
      S._merge_defaults({})["bibdb"]["verifyMax"] is None
      and S._merge_defaults({})["notesRepoPath"] == ""
      and S._merge_defaults({"bibdb": {"verifyMax": -3, "verifyPlan": "x"}})["bibdb"]
      == {"verifyMax": None, "verifyPlan": False, "verifyAllRefs": False,
          "lookupEnabled": True})

# ── 真跑 bibdb.py（**临时库**，与 mac 第二十八批的 CLI 冒烟同一口径）──
def run_capture(call) -> tuple[int, list[str]]:
    collected: list[str] = []
    return E.run_call(call, on_line=collected.append), collected


_cli_root = tempfile.mkdtemp(prefix="slcn_bibdb_cli_")
_cli_db = os.path.join(_cli_root, "db", "bibliography.jsonl")
_cli_bib = os.path.join(_cli_root, "reference.bib")
with open(_cli_bib, "w", encoding="utf-8") as _fh:
    _fh.write("@article{Foo2020,\n  author = {Foo, Bar},\n"
              "  title = {A Test Article for the Library},\n"
              "  journal = {Journal of Tests},\n  year = {2020},\n"
              "  doi = {10.1000/test.1}\n}\n")
_rc, _lines = run_capture(E.bibdb_import_bib_call(
    bib=_cli_bib, issue="2026年第1期(86)", slug="01 Bar_Foo", dry_run=True, db=_cli_db))
_payload = E.parse_json_lines(_lines)
check("(i) CLI 冒烟：import-bib --dry-run --json 退出 0、JSON 可用、written=false",
      _rc == 0 and isinstance(_payload, dict) and _payload.get("dry_run") is True
      and _payload.get("written") is False,
      f"rc={_rc} tail={E.output_tail(_lines)}")
check("(i) **dry-run 绝不写库**：库文件没有被创建（连接收它的目录都不该被建出来）",
      not os.path.exists(_cli_db) and not os.path.isdir(os.path.dirname(_cli_db)),
      f"exists={os.path.exists(_cli_db)} "
      f"dir={os.path.isdir(os.path.dirname(_cli_db))}")
_rc, _lines = run_capture(E.bibdb_import_bib_call(
    bib=_cli_bib, issue="2026年第1期(86)", slug="01 Bar_Foo", dry_run=False, db=_cli_db))
_payload = E.parse_json_lines(_lines)
check("(i) CLI 冒烟：确认入藏真的写库（written=true、库文件出现）",
      _rc == 0 and isinstance(_payload, dict) and _payload.get("written") is True
      and os.path.isfile(_cli_db), f"rc={_rc} tail={E.output_tail(_lines)}")

_rc, _lines = run_capture(E.bibdb_stats_call(db=_cli_db))
_payload = E.parse_json_lines(_lines)
check("(i) CLI 冒烟：stats --json 读回「1 条、已核实 1」",
      _rc == 0 and isinstance(_payload, dict) and _payload.get("records") == 1
      and _payload.get("verified") == 1,
      f"rc={_rc} {(_payload or {}).get('records')}/{(_payload or {}).get('verified')}")

_rc, _lines = run_capture(E.bibdb_query_call("key", "Foo2020", db=_cli_db))
_payload = E.parse_json_lines(_lines)
_hits = (_payload or {}).get("hits") or []
check("(i) CLI 冒烟：query --key --bibtex 命中 1 条、带回可复制的 BibTeX",
      _rc == 0 and len(_hits) == 1 and "@article" in str(_hits[0].get("bibtex", ""))
      and "Foo2020" in str(_hits[0].get("bibtex", "")),
      f"rc={_rc} hits={len(_hits)}")

_rc, _lines = run_capture(E.bibdb_lint_call(db=_cli_db))
_payload = E.parse_json_lines(_lines)
check("(i) CLI 冒烟：lint --json 有结构（ok 字段；退出码 1 = 库有 error，不是程序错）",
      isinstance(_payload, dict) and "ok" in _payload and _rc in (0, 1),
      f"rc={_rc} ok={(_payload or {}).get('ok')}")

_cli_out = os.path.join(_cli_root, "list_out")
_rc, _lines = run_capture(E.bibdb_report_call(out_dir=_cli_out, db=_cli_db))
_payload = E.parse_json_lines(_lines)
_expected_reports = ("CONFLICTS.md", "DUPLICATES.md", "duplicates.csv")
check("(i) CLI 冒烟：report 只写三个清单文件（CONFLICTS.md / DUPLICATES.md / duplicates.csv）",
      _rc == 0 and all(os.path.isfile(os.path.join(_cli_out, name))
                       for name in _expected_reports),
      f"rc={_rc} out={sorted(os.listdir(_cli_out)) if os.path.isdir(_cli_out) else '（目录不存在）'}")

shutil.rmtree(_probe, ignore_errors=True)
shutil.rmtree(_cli_root, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
