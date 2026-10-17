#!/usr/bin/env python3
"""Windows 外壳与引擎之间的桥梁（第 89 条）——**不开窗口**的测试。

为什么能不开窗口就测：`engine.py` / `settings.py` 里**没有一行 tkinter**
（那是刻意的设计，见 `engine.py` 开头）。界面只负责「取值 → 调这两个模块 → 显示日志」，
所以逻辑部分在 mac / Linux / CI 上都能验。

钉住七件事：
  (a) 引擎与解释器的定位顺序（环境变量 → 打包形态 → 开发形态）；
  (b) 拼出来的命令行**与 `Engine/docs/ENGINE_API.md` 的契约一致**（参数名、顺序、布尔开关）；
  (c) **密钥绝不进命令行**（只在环境变量里）；UTF-8 / 无缓冲 / vendor 也都设好；
  (d) 配置读写：密钥只落本地（**每用户**目录）、界面显示打码、坏 JSON 不炸；
  (e) 真跑一次：用桥梁调 `mergepdf.py` 合并一个假期目录，逐行日志能收到、退出码正确；
  (f) 界面文件：语法 / 导入检查（不开窗口）；
  (g) 真开一次窗口（控件参数写错只有这一步抓得到；缺 tkinter 时 SKIP）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
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
check("(b) 八个入口名字都在表里（写错名字 = 同上；2026-10-17 起是八个）",
      set(E.ENTRIES) == {"convert.py", "checkbib.py", "mergepdf.py",
                         "cleantemp.py", "compileall.py", "proofcheck.py",
                         "infocheck.py", "toc.py"}, f"{E.ENTRIES}")

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
       "thinking": "off", "maxTokens": "4096", "webSearch": True}
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
      and env.get("SLCN_LLM_WEB_SEARCH") == "1")
for call in (c, c2, c3):
    check(f"(c) **密钥绝不出现在 argv 里**（{call.entry}）",
          "sk-1234567890" not in call.display and "SLCN_LLM_KEY" not in call.display)
check("(c) 没填的 AI 字段不设环境变量（不许塞空串把引擎默认值顶掉）",
      "SLCN_LLM_SEARCH_TOOL" not in E.env_for_run({"base": "x"}))

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
import ast  # noqa: E402
ast.parse(src)
check("(f) `main.py` 语法正确", True)
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
if tkpy is None:
    print("SKIP  (g) 本机没有带 tkinter 的解释器（Windows 上跑得到）")
else:
    # 给 (g) 用的合成期目录：一篇文章的 tex（不含 PDF —— 页数那条另有诚实化断言）
    gdir = tempfile.mkdtemp(prefix="slcn_bridge_issue_")
    gart = os.path.join(gdir, "01 A_Author")
    os.makedirs(gart, exist_ok=True)
    with open(os.path.join(gart, "A_Author.tex"), "w", encoding="utf-8") as fh:
        fh.write("\\documentclass{article}\n\\begin{document}\n"
                 "\\myvolnumber{19}\\myissnumber{5}\\mypubyear{2026}\n"
                 "\\title{测试题名}\\author{张三}\n\\maketitle\n正文。\n\\end{document}\n")

    prog = textwrap.dedent("""
        import os, sys, time
        sys.path.insert(0, os.getcwd())
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
        app.destroy()
        print("窗口建成")
        print("标签页：" + "|".join(tabs))
    """)
    env2 = dict(os.environ)
    env2["PYTHONPATH"] = str(REPO / "Engine" / "vendor")
    env2["SLCN_BRIDGE_TEST_DIR"] = gdir
    r = subprocess.run([tkpy, "-"], input=prog, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env2, cwd=str(SHELL), timeout=180)
    out = r.stdout or ""
    if r.returncode != 0 and ("no display" in (r.stderr or "") or "TclError" in (r.stderr or "")):
        print(f"SKIP  (g) 没有可用显示器/窗口服务：{(r.stderr or '').strip().splitlines()[-1:]}")
    else:
        check("(g) 真开一次窗口：控件全部建成、写日志、正常退出",
              r.returncode == 0 and "窗口建成" in out,
              f"rc={r.returncode} {(r.stderr or '').strip()[-160:]}")
        check("(g) 九个页都在（侧边栏顺序：单篇 4 + 整期 4 + 设置）",
              "初排转换|文献回读校验|合并文件|一校核查|检查tex信息|生成目录|编译全部|删除临时文件|设置"
              in out, out.strip()[-160:])
        check("(g) **分组侧边栏**：两层结构（单篇稿件 / 整期装配 / 设置），不是 9 个平级标签",
              "侧栏=单篇稿件|整期装配|设置" in out, [l for l in out.splitlines() if l.startswith("侧栏")])
        check("(g) 切页会同步侧栏选中态", "切页=tab:4" in out,
              [l for l in out.splitlines() if l.startswith("切页")])
        check("(g) **真跑一次「检查tex信息」→ 表格被填上**（H2 回归守卫：修之前恒为 0 行）",
              "表格行数=1" in out,
              [l for l in out.splitlines() if l.startswith("表格行数")])
        check("(g) 结束行是「✅ 完成（退出码 0）」（H3：文案由 `E.result_text` 统一给）",
              "结束行=✅ 完成（退出码 0）" in out,
              [l for l in out.splitlines() if l.startswith("结束行")])
        check("(g) 没有可比对的 PDF 页数时，顶部提示**不许**说「相符」（不许假绿）",
              "未验证" in out or "相符" not in out,
              [l for l in out.splitlines() if l.startswith("顶部提示")])
    shutil.rmtree(gdir, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
