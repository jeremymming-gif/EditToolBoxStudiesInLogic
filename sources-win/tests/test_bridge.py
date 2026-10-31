#!/usr/bin/env python3
"""Windows 外壳与引擎之间的桥梁（第 89 条）——**基本不开窗口**的测试。

为什么能不开窗口就测：`engine.py` / `settings.py` 里**没有一行 tkinter**
（那是刻意的设计，见 `engine.py` 开头）。界面只负责「取值 → 调这两个模块 → 显示日志」，
所以逻辑部分在 mac / Linux / CI 上都能验；只有 (g)(h) 两组会真开一次窗口（缺 tkinter 时 SKIP）。

B-309 门禁/CI 用 `SLCN_BRIDGE_SKIP_GUI=1` 跳过**只有 GUI 才需要**的三个探针：
  · (g) 真开一次窗口（含 180 秒子进程等待）；
  · (h) 真窗口那半组（跨平台夹具的 3 条断言照跑）；
  · (o) 需要 `import tabs.dirpush` 的纯函数断言（该页面 import tkinter；静态守卫照跑）。
默认不设该变量 → 行为与旧版逐项一致。

钉住这些事：
  (a) 引擎与解释器的定位顺序（环境变量 → 打包形态 → 开发形态）；
  (b) 拼出来的命令行**与 `Engine/docs/ENGINE_API.md` 的契约一致**（参数名、顺序、布尔开关）；
  (c) **密钥绝不进命令行**（只在环境变量里）；UTF-8 / 无缓冲 / vendor 也都设好；
  (d) 配置读写：密钥只落本地（**每用户**目录）、界面显示打码、坏 JSON 不炸；
  (e) 真跑一次：用桥梁调 `mergepdf.py` 合并一个假期目录，逐行日志能收到、退出码正确；
  (f) 界面文件：语法 / 导入检查（不开窗口）；
  (g) 真开一次窗口（控件参数写错只有这一步抓得到；缺 tkinter 时 SKIP）；
  (h) 日志「摘要 / 原始」两层（U4）：跨平台夹具 `tools/log_cases.json` 逐条过，
      并在 (g) 的真窗口里验证能切模式、能看到折叠提示、偏好能落盘、空状态占位不进复制内容；
  (i) 引用库（第 132 条）：笔记仓探测 / 库文件 / 参数默认零变化 / CLI 冒烟；
  (j) 两段式 AI 协同初排（第五十五批）：两段 argv 契约、默认 argv 零变化、暂存目录发现、
      建议摘要、回复导入、AI token 预算设置（纯逻辑，全部 headless）；批59 全文 AI 核查
      范围映射（默认关；仅第一段带 `--coop-fulltext context|full`）；批60 直连全文核查
      （`--llm-fulltext`，默认关、会话态；只在 ⑪ 直连通道追加）；批62 入口收敛 B
      （普通默认无 --verify-refs，`use_ai` 内部等价 ⑪；回读页长标题）；
  (k) B-281 Windows/mac 对等：mac `NavPage`（顺序/标题/分组）与 Windows `self.tabs`/
      `nav_groups` 对照**硬编码**对等清单（B-289 起两壳 15/15，差异只剩已声明两项）；
      `sources-win/README.md` 声明 15 页/13 入口/两处差异并指向《使用说明》§3.3；
      清理三模式（argv/确认文案/撤销条件）静态守卫。
  (o) B-289 目录摘要推送：Windows 页与 `Engine/dirpush.py` 的 argv 契约（年度不传
      `--with-en-toc`）、只读 JSON 冻结字段、输出根默认值/记忆键、静态守卫。
"""
from __future__ import annotations

import ast
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

# B-309：门禁/CI 只要纯逻辑证据时用它跳过「真窗口 / 需要 tkinter 的页面 import」。
# 只认 "1"（默认不设 = 旧行为）；跳过的每个探针都会打一行带变量名的 SKIP。
_SKIP_GUI = os.environ.get("SLCN_BRIDGE_SKIP_GUI") == "1"


def _read_all_shell_sources() -> str:
    """整个 Windows 外壳的源码拼接：`main.py` + `tabs/*.py` + `bibdb_ui.py`。

    B-230 把各 tab 页从 `main.py` 原样搬进 `tabs/` 后，静态守卫若只扫 `main.py`
    会漏掉页面源码（假绿或假红）。`tabs/*.py` 按文件名排序拼接（顺序固定、可复现）。
    """
    parts = [SHELL / "main.py", *sorted((SHELL / "tabs").glob("*.py")),
             SHELL / "bibdb_ui.py"]
    return "\n".join(pp.read_text(encoding="utf-8") for pp in parts if pp.exists())


all_src = _read_all_shell_sources()

import clean_logic as CL  # noqa: E402  B-281：清理三模式的纯逻辑（无 tkinter）
import coop as C  # noqa: E402  第五十五批：两段式协同的纯逻辑（无 tkinter）
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

# ── B-274（用户 m02462）：侧栏底部版本号的数据源 —— headless 可测 ──
# 版本号**只有一份**：`Engine/slcn/__init__.py::__version__`。三条路径：源码形态读文件 /
# 冻结形态解析 `SLCNEngine.exe --version` / 都找不到 → 「未知」（界面照常、不崩）。
_ver_src = (REPO / "Engine" / "slcn" / "__init__.py").read_text(encoding="utf-8")
_ver_m = re.search(r'^__version__\s*=\s*"([^"]+)"', _ver_src, re.M)
_expected_ver = _ver_m.group(1) if _ver_m else ""
E._ENGINE_VERSION_CACHE = None
check("(a) B-274：`engine_version()` 与唯一版本源 `Engine/slcn/__init__.py` 一致",
      bool(_expected_ver) and E.engine_version() == _expected_ver,
      f"{E.engine_version()} vs {_expected_ver}")
check("(a) B-274：`engine_version()` 形如 x.y.z（侧栏只显示版本号）",
      bool(re.fullmatch(r"\d+\.\d+\.\d+", E.engine_version())), E.engine_version())

_saved_slcn = E.slcn_engine
_saved_frozen = E.frozen_engine_exe
_saved_run = E.subprocess.run
_src_eng_dir = tempfile.mkdtemp(prefix="slcn_ver_engine_")
try:
    _p = pathlib.Path(_src_eng_dir)
    (_p / "slcn").mkdir()
    (_p / "convert.py").write_text("# 合成引擎目录（B-274 测试用）\n", encoding="utf-8")
    (_p / "slcn" / "__init__.py").write_text('__version__ = "9.8.7"\n', encoding="utf-8")
    E.slcn_engine = lambda: _src_eng_dir
    E.frozen_engine_exe = lambda: None
    E._ENGINE_VERSION_CACHE = None
    check("(a) B-274：源码形态从 `<Engine>/slcn/__init__.py` 读版本号（不另设第二版本源）",
          E.engine_version() == "9.8.7", E.engine_version())
finally:
    E.slcn_engine = _saved_slcn
    E.frozen_engine_exe = _saved_frozen
    shutil.rmtree(_src_eng_dir, ignore_errors=True)

class _FakeVersionProc:      # 冻结形态：`SLCNEngine.exe --version` 的输出形态（B-274）
    stdout = "SLCN 引擎 9.9.9-test（打包版）｜ 运行方式=冻结 exe\r\n"
    stderr = ""

try:
    E.slcn_engine = lambda: None
    E.frozen_engine_exe = lambda: "SLCNEngine.exe"
    E.subprocess.run = lambda *a, **k: _FakeVersionProc()
    E._ENGINE_VERSION_CACHE = None
    check("(a) B-274：冻结形态解析 `--version` 输出里的版本号",
          E.engine_version() == "9.9.9-test", E.engine_version())
finally:
    E.slcn_engine = _saved_slcn
    E.frozen_engine_exe = _saved_frozen
    E.subprocess.run = _saved_run

try:
    E.slcn_engine = lambda: None
    E.frozen_engine_exe = lambda: None
    E._ENGINE_VERSION_CACHE = None
    check("(a) B-274：两种形态都找不到 → 「未知」（侧栏显示「版本 未知」、界面不崩）",
          E.engine_version() == "未知", E.engine_version())
finally:
    E.slcn_engine = _saved_slcn
    E.frozen_engine_exe = _saved_frozen
    E._ENGINE_VERSION_CACHE = None

E._ENGINE_VERSION_CACHE = None
_v_first, _v_second = E.engine_version(), E.engine_version()
check("(a) B-274：版本号结果缓存（第二次不再读盘 / 不再起子进程）", _v_first == _v_second,
      f"{_v_first} / {_v_second}")
E._ENGINE_VERSION_CACHE = None

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
check("(b) 十三个入口名字都在表里（写错名字 = 同上；第 132 条起含引用库 bibdb.py，"
      "B-283 起含环境自检 envcheck.py，B-285b 起含失败诊断 diagnose.py，B-289 起含"
      "目录摘要推送 dirpush.py）",
      set(E.ENTRIES) == {"convert.py", "checkbib.py", "mergepdf.py",
                         "cleantemp.py", "compileall.py", "proofcheck.py",
                         "infocheck.py", "toc.py", "publish.py", "bibdb.py",
                         "envcheck.py", "diagnose.py", "dirpush.py"},
      f"{E.ENTRIES}")
check("(b) B-283/B-285b/B-289 的口径：前 10 项**顺序逐项不变**，envcheck/diagnose/"
      "dirpush 依次追加末尾（C8：默认零变化）",
      tuple(E.ENTRIES[:10]) == ("convert.py", "checkbib.py", "mergepdf.py",
                                "cleantemp.py", "compileall.py", "proofcheck.py",
                                "infocheck.py", "toc.py", "publish.py", "bibdb.py")
      and tuple(E.ENTRIES[10:]) == ("envcheck.py", "diagnose.py", "dirpush.py")
      and tuple(_DISP.ENTRIES) == tuple(E.ENTRIES),
      f"{E.ENTRIES}")

c7 = E.infocheck_call(folder=r"E:\\期\\90", preview=True)
check("(b) 检查tex信息：`--folder` + `--preview`（界面表格用）",
      c7.entry == "infocheck.py" and "--folder" in c7.argv and "--preview" in c7.argv)
check("(b) 反例：正式检查时**不**带 `--preview`（要落 txt/json 产物）",
      "--preview" not in E.infocheck_call(folder="/tmp/x").argv)
c8 = E.toc_call(report=r"C:\\出稿\\信息检查2026.txt", template=r"C:\\期\\上一期")
check("(b) 生成目录默认一键交付：`--report` + `--template` + `--place --compile`",
      c8.entry == "toc.py" and "--report" in c8.argv and "--template" in c8.argv
      and "--place" in c8.argv and "--compile" in c8.argv)
check("(b) 生成目录：模板留空 → **不传** `--template`（交给引擎找上一期）",
      "--template" not in E.toc_call(report="/tmp/x.txt").argv)
_c8r = E.toc_call(report="/tmp/x.txt", result_json="/tmp/结果.json")
check("(b) 生成目录：给了临时路径才追加 `--result-json <path>`（外壳读回 4 路径）",
      "--result-json" in _c8r.argv
      and _c8r.argv[_c8r.argv.index("--result-json") + 1] == "/tmp/结果.json"
      and "--result-json" not in E.toc_call(report="/tmp/x.txt").argv)
check("(b) 生成目录「仅生成 tex」高级开关（默认关）→ 只传 `--report`（旧行为，不落位/不编译）",
      "--place" not in E.toc_call(report="/tmp/x.txt", text_only=True).argv
      and "--compile" not in E.toc_call(report="/tmp/x.txt", text_only=True).argv
      and "--report" in E.toc_call(report="/tmp/x.txt", text_only=True).argv)
check("(b) 目录预览：`--preview`（篇目清单，不落文件）",
      "--preview" in E.toc_preview_call(report="/tmp/x.txt").argv)

# B-226：生成目录结果 JSON → 结果区的行（4 路径 / 模板来源 / 页数 / 注意事项）
_toc_payload = {
    "templateSource": "上一期", "templateNote": "模板：2026年第4期(89)",
    "tocDir": "/tmp/期/000 Table of Contents",
    "placed": {"zhTex": "/tmp/期/000 Table of Contents/Chinese Content.tex",
               "zhPdf": "/tmp/期/000 Table of Contents/Chinese Content.pdf",
               "enTex": "/tmp/期/000 Table of Contents/contents.tex",
               "enPdf": "/tmp/期/000 Table of Contents/contents.pdf"},
    "compiled": True, "pages": {"zh": 1, "en": 1},
    "column": {"enabled": True, "title": "专栏：认知悖论研究"},
    "manualEdits": [{"kind": "标题占位", "article": "08 样例", "note": "源稿标题为占位"}],
    "warnings": ["缺 000 Table of Contents，已自动创建"],
}
_toc_lines = E.toc_result_lines(_toc_payload)
check("(b) 生成目录结果 JSON → 结果区：4 个固定名路径",
      sum(1 for line in _toc_lines
          if any(name in line for name in ("Chinese Content.tex", "Chinese Content.pdf",
                                           "contents.tex", "contents.pdf"))) == 4,
      _toc_lines)
check("(b) 生成目录结果 JSON → 结果区：模板来源 / 页数 / 人工核对项 / 警告",
      any(line.startswith("模板来源：上一期") for line in _toc_lines)
      and "页数：中文 1 页／英文 1 页" in _toc_lines
      and any("需人工核对" in line and "标题占位" in line for line in _toc_lines)
      and any(line.startswith("⚠️") and "已自动创建" in line for line in _toc_lines),
      _toc_lines)
check("(b) 生成目录结果 JSON 缺字段/文件不存在 → 空结果（不猜、不炸）",
      E.toc_result_lines({}) == []
      and E.read_toc_result("/tmp/不存在/slcn-toc-无.json") == {})
_bad_json = os.path.join(tempfile.mkdtemp(prefix="slcn_toc_bad_"), "r.json")
with open(_bad_json, "w", encoding="utf-8") as fh:
    fh.write("not json")
check("(b) 生成目录结果文件坏 JSON → `{}`（界面走「没有结果 JSON」分支，不假绿）",
      E.read_toc_result(_bad_json) == {})
shutil.rmtree(os.path.dirname(_bad_json), ignore_errors=True)

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
_c_full = E.convert_call(source="a.docx", out="/tmp/o", author="李雷", name="Lei_Li",
                         vol="19", issue="5", year="2026", pages="137-152",
                         received="2025-09-19", editor="何杨", grants="国社科基金")
check("(b) convert 能把 `--author/--name` 传下去（正文读不出姓名时，这是这类稿的唯一活路）",
      "--author" in _c_full.argv and "--name" in _c_full.argv
      and _c_full.argv[_c_full.argv.index("--author") + 1] == "李雷", _c_full.display[:100])
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
      "v_searchrefs" in all_src
      and 'api"]["searchRefs"] = bool(self.v_searchrefs.get())' in all_src
      and "DeepSeek 独立 Search API" in all_src)
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
_art = os.path.join(_ic_dir, "01 Ning_Wang")
os.makedirs(_art, exist_ok=True)
open(os.path.join(_art, "Ning_Wang.tex"), "w", encoding="utf-8").write(
    "\\documentclass[b5paper,10.5t,onecolumn,twoside]{ctexart}\n"
    "\\newcommand{\\myvolnumber}{19}      % 输入卷号\n"
    "\\newcommand{\\myissnumber}{5}       % 输入当年期号\n"
    "\\newcommand{\\myyearnumber}{2026}   % 输入出版年份\n"
    "\\newcommand{\\mystartpage}{1}      % 输入起始页码\n"
    "\\newcommand{\\myendpage}{16}       % 输入终止页码\n"
    "\\newcommand{\\mypages}{16}         % 输入页数\n"
    "\\newcommand{\\mytitle}             % 请输入论文题目\n"
    "{论示例悖论}\n"
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
          and "论示例" in (_a.get("chineseTitle") or "") and "陆王" in (_a.get("firstAuthorChinese") or ""),
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
# all_src 已在文件头统一拼接（main.py + tabs/*.py + bibdb_ui.py；B-230）
import ast  # noqa: E402
ast.parse(src)
check("(f) `main.py` 语法正确", True)
check("(f) 引用库界面文件 `bibdb_ui.py` 存在且语法正确（第 132 条拆文件）",
      bool(ui_src) and (ast.parse(ui_src) or True))
# 第五十五批：两段式协同的纯逻辑单独一个文件，且**不许** import tkinter（headless 可测）。
coop_py = SHELL / "coop.py"
coop_src = coop_py.read_text(encoding="utf-8") if coop_py.exists() else ""
check("(f) 协同纯逻辑文件 `coop.py` 存在、语法正确、不 import tkinter（可 headless 测试）",
      bool(coop_src) and bool(ast.parse(coop_src))
      and not re.search(r"^\s*(?:import|from)\s+tkinter", coop_src, re.M),
      f"{len(coop_src)} 字符")
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
      "pypdf" not in all_src and "PdfWriter" not in all_src)
check("(f) 界面把逻辑都委托给 `engine.py`（八个入口各一处调用）",
      all(f"E.{fn}_call(" in all_src
          for fn in ("convert", "checkbib", "merge", "cleantemp", "compileall", "proofcheck",
                     "infocheck", "toc")), "")
check("(f) 保留了原版的中文交互（浏览/运行/停止 + 底部日志框）",
      all(k in all_src for k in ("浏览", "运行", "停止", "运行日志")))
check("(f) 界面线程与工作线程分开（Tkinter 不是线程安全的，日志靠队列回传）",
      "threading.Thread" in src and "queue.Queue" in src and "after(" in src)
# ⚠️ 下面三条是 2026-10-17 审计抓到的真缺陷的守卫 —— 从前的"守卫"是假的
#    （例如只断言 `E.infocheck_call(` 在 main.py 里出现过，而 `show_report` 是死代码）。
check("(f) 「检查tex信息」的表格**真的有人填**（`show_report` 从前全仓 0 处调用 = 表格永远空）",
      all_src.count("show_report(") >= 2 and "_info_report_payload" in all_src,
      f"show_report 出现 {all_src.count('show_report(')} 次")
check("(f) 初排转换页有「输出名 / 作者」输入框（缺了这类 LaTeX 稿整页做不了）",
      "输出名" in all_src and "v_author" in all_src and "v_name" in all_src)
check("(f) 「删除临时文件」是**先预览再移入回收目录、且可撤销**（UI 方案第 3 件）",
      "confirm_trash" in all_src and "undo_btn" in all_src and "撤销清理" in all_src
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
      "main.py + tabs/*.py 与 bibdb_ui.py 一起扫）",
      not _classic, f"还有 {len(_classic)} 处")
check("(f) `tk.Listbox` 保留（Tk 无 ttk 对等物）但色板取自 `theme.style_listbox`",
      "tk.Listbox(" in all_src and "theme.style_listbox(" in all_src
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
      and "运行任务后，日志会显示在这里" in all_src)
check("(f) 复制/保存一律给**完整日志**（含折叠行）：不读日志控件，用 `_full_log_text()`",
      src.count("self._full_log_text()") >= 2 and "self.output.get(" not in src
      and "clipboard_append(self.output" not in src)

# ── B-230（纯搬运）：`tabs/*.py` 的结构守卫 —— 依赖必须单向 `tabs → base + 引擎模块` ──
_tab_files = sorted((SHELL / "tabs").glob("*.py"))
_tab_srcs = {pp.name: pp.read_text(encoding="utf-8") for pp in _tab_files}
_bad_root = [n for n, s in _tab_srcs.items() if "class MainWindow" in s or "tk.Tk(" in s]
check("(f) B-230：`MainWindow` / `tk.Tk()` 不在 `tabs/*.py`（入口只在 main.py）",
      bool(_tab_files) and not _bad_root, _bad_root)
_bad_main = [n for n, s in _tab_srcs.items()
             if re.search(r"^\s*(?:import\s+main\b|from\s+main\b)", s, re.M)]
check("(f) B-230：`tabs/*.py` 不 import `main`（依赖单向：tabs → base + 引擎模块）",
      not _bad_main, _bad_main)
_sib = re.compile(r"^\s*(?:from\s+(?:tabs\.|\.)(\w+)|import\s+tabs\.(\w+))", re.M)
_bad_sib = {n: sorted({a or b for a, b in _sib.findall(s)} - {"base"})
            for n, s in _tab_srcs.items()}
_bad_sib = {n: v for n, v in _bad_sib.items() if v}
check("(f) B-230：`tabs/*.py` 不互为兄弟 import（只允许 `from .base import`）",
      not _bad_sib, _bad_sib)

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

if _SKIP_GUI:
    print("SKIP  (g) SLCN_BRIDGE_SKIP_GUI=1：按约定跳过真窗口（默认不跳过；门禁用）")
    tkpy = None
else:
    tkpy = _tk_python()
win_out = ""          # (g) 子进程的 stdout；(h) 组的「摘要/原始」断言要在这里找证据
if tkpy is None:
    if not _SKIP_GUI:
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
        import json, os, shutil, sys, tempfile, time
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
        # ── B-274：侧栏底部版本号（后台线程算 → 队列 → `_drain` 贴 label）──
        # 版本号可能读文件、冻结形态还会起子进程：等它回到主线程再读 label 文本。
        t0v = time.time()
        while time.time() - t0v < 10 and app.version_label.cget("text") == "版本 …":
            app.update()
            time.sleep(0.02)
        print("版本标签=" + str(app.version_label.cget("text")))
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
        app.show_tab(13)          # B-289：dirpush 插到 8 后，引用库 5 页顺移到 9..13
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
        # B-286：分组归属变了（「参考文献规范化」从单篇组迁到引用库组），直接读侧栏子节点
        print("单篇组页=" + "|".join(
            app.nav.item(x, "text") for x in app.nav.get_children(app.nav.get_children()[0])))
        print("引用库组页=" + "|".join(
            app.nav.item(x, "text") for x in app.nav.get_children(app.nav.get_children()[2])))
        print("引用库侧栏=" + "|".join(
            app.nav.item(x, "text") for x in app.nav.get_children()))
        # ── 第五十五批：两段式协同 + AI 预算（真窗口证据）──
        app.show_tab(0)
        app.update()
        ct = app.convert_tab
        # ── 批62：入口收敛 B（真窗口证据）──
        # 独立「出文献核实任务书」勾选隐藏（字段保留、默认 false）；普通初排默认 argv
        # 无 --verify-refs；勾「用 AI 处理参考文献」→ 内部自动带（⑪）；AI 说明/全文区
        # 按勾选展开收起；两段式面板有引导文案；回读页长标题在正文顶部、侧栏短名不变。
        print("入口收敛默认关=%d" % int(not ct.v_ai.get() and not ct.v_refs.get()))
        _hidden_refs = []

        def _walk_labels(w):
            for _ch in w.winfo_children():
                try:
                    if "出文献核实任务书" in str(_ch.cget("text")):
                        _hidden_refs.append(str(_ch.cget("text")))
                except Exception:  # noqa: BLE001  不是带 text 的控件
                    pass
                _walk_labels(_ch)

        _walk_labels(ct)
        _b62_off = M.E.convert_call(source="a.docx", out="/o", **ct.extra_args())
        ct.v_ai.set(True)
        ct._toggle_ai_options()
        app.update()
        _b62_on = M.E.convert_call(source="a.docx", out="/o", **ct.extra_args())
        print("入口收敛隐藏勾选=%d" % int(not _hidden_refs))
        print("入口收敛AI展开=%d" % int(bool(ct.ai_hint.winfo_ismapped())
                                        and bool(ct.llm_fulltext_box.winfo_ismapped())))
        print("入口收敛AI提示=%d" % int("已配置" in ct.ai_hint.cget("text")
                                        or "端点还没配置" in ct.ai_hint.cget("text")))
        print("入口收敛默认argv=%d" % int("--verify-refs" not in _b62_off.argv))
        print("入口收敛AIargv=%d" % int("--verify-refs" in _b62_on.argv
                                        and "--llm-fulltext" not in _b62_on.argv))
        print("入口收敛协同引导=" + ct.coop_guide.cget("text"))
        ct.v_ai.set(False)
        ct._toggle_ai_options()
        app.update()
        _b62_back = M.E.convert_call(source="a.docx", out="/o", **ct.extra_args())
        print("入口收敛取消复原=%d" % int(_b62_back.argv == _b62_off.argv
                                        and not ct.ai_hint.winfo_ismapped()))
        print("回读页标题=" + app.checkbib_tab.page_title)
        print("回读页引导=%d" % int(
            app.checkbib_tab.name == "参考文献规范化"
            and "本页不生成《文献核实任务书》" in app.checkbib_tab.instruction
            and "手动并回" in app.checkbib_tab.instruction
            and "离线体检" in app.checkbib_tab.instruction
            and "第二段自动完成" in app.checkbib_tab.instruction
            and "引用库 → 参考文献规范化" in app.checkbib_tab.instruction))
        print("协同默认关=%d" % int(not ct.v_coop.get()))
        print("协同面板默认隐藏=%d" % int(not ct.coop_panel.winfo_ismapped()))
        ct.v_coop.set(True)
        ct._toggle_coop_panel()
        app.update()
        print("协同面板显示=%d" % int(bool(ct.coop_panel.winfo_ismapped())))
        # 批59：全文 AI 核查（默认关；勾选 + 范围 → ph1 argv 追加 --coop-fulltext）
        print("全文核查默认关=%d" % int(
            not ct.v_coop_fulltext.get()
            and ct.v_coop_fulltext_scope.get() == M.C.FULLTEXT_CONTEXT))
        _ft_off = ct.coop_ph1_call("/期").argv
        ct.v_coop_fulltext.set(True)
        _ft_ctx = ct.coop_ph1_call("/期").argv
        ct.v_coop_fulltext_scope.set(M.C.FULLTEXT_FULL)
        _ft_full = ct.coop_ph1_call("/期").argv
        ct.v_coop_fulltext.set(False)
        _ft_back = ct.coop_ph1_call("/期").argv
        print("全文核查默认argv=%d" % int("--coop-fulltext" not in _ft_off))
        print("全文核查context=%d" % int(
            M.MainWindow._argv_value(_ft_ctx, "--coop-fulltext") == "context"))
        print("全文核查full=%d" % int(
            M.MainWindow._argv_value(_ft_full, "--coop-fulltext") == "full"))
        print("全文核查取消复原=%d" % int(
            "--coop-fulltext" not in _ft_back and _ft_back == _ft_off))
        print("全文核查文案=%d" % int(
            "零上传" in ct.coop_fulltext_hint.cget("text")
            and "零费用" in ct.coop_fulltext_hint.cget("text")))
        # 批60：全文 AI 核查（直连 API）——默认不勾、**会话态**；勾选 → ⑪ argv 追加
        # 无取值的 --llm-fulltext，取消后复原；settings.json 里不该出现这个偏好。
        # 批62 起全文核查区只在「用 AI 处理参考文献」勾着时展开/生效，先勾 AI 再验映射。
        print("直连全文默认关=%d" % int(not ct.v_llm_fulltext.get()))
        _llm_off = M.E.convert_call(source="a.docx", out="/o", **ct.extra_args())
        ct.v_ai.set(True)
        ct._toggle_ai_options()
        ct.v_llm_fulltext.set(True)
        _llm_on = M.E.convert_call(source="a.docx", out="/o", **ct.extra_args())
        ct.v_llm_fulltext.set(False)
        _llm_ai_only = M.E.convert_call(source="a.docx", out="/o", **ct.extra_args())
        ct.v_ai.set(False)
        ct._toggle_ai_options()
        _llm_back = M.E.convert_call(source="a.docx", out="/o", **ct.extra_args())
        print("直连全文默认argv=%d" % int("--llm-fulltext" not in _llm_off.argv))
        print("直连全文映射=%d" % int("--llm-fulltext" in _llm_on.argv))
        print("直连全文需AI=%d" % int("--llm-fulltext" not in _llm_ai_only.argv))
        print("直连全文取消复原=%d" % int(
            "--llm-fulltext" not in _llm_back.argv and _llm_back.argv == _llm_off.argv))
        print("直连全文文案=%d" % int(
            "会产生费用" in ct.llm_fulltext_hint.cget("text")
            and "不改 reference.bib" in ct.llm_fulltext_hint.cget("text")))
        print("直连全文不落盘=%d" % int(
            "llmFulltext" not in json.dumps(
                json.load(open(S.config_path(), encoding="utf-8")), ensure_ascii=False)))
        print("协同按钮齐全=%d" % int(all(
            w.cget("text") in ("打开任务书", "打开暂存目录", "打开交付目录",
                              "打开协同记录", "打开建议清单",
                              "添加回填文件…", "添加回填目录…", "从剪贴板导入",
                              "移除选中", "全部清除", "运行第二段…")
            for w in (ct.coop_taskbook_btn, ct.coop_stage_btn, ct.coop_deliver_btn,
                      ct.coop_record_btn, ct.coop_suggest_btn, ct.coop_import_btn,
                      ct.coop_remove_btn, ct.coop_clear_btn))))
        print("回填按钮存在=%d" % int(all(hasattr(ct, name) for name in (
            "_add_coop_files", "_add_coop_dir", "_import_clipboard_reply",
            "_remove_selected_coop_replies", "_clear_coop_replies"))))
        print("协同按钮默认禁用=%d" % int(all(
            str(w.cget("state")) == "disabled"
            for w in (ct.coop_taskbook_btn, ct.coop_stage_btn, ct.coop_deliver_btn,
                      ct.coop_record_btn, ct.coop_suggest_btn, ct.coop_import_btn))))
        ct.v_coop.set(False)
        ct._toggle_coop_panel()
        app.update()
        # 设置页预算：非法不落盘（红字）/ 合法（含 0 = 不限）落盘
        app.show_tab(13)
        app.update()
        st2 = app.settings_tab
        st2.v_budget.set("abc")
        st2.save()
        app.update()
        _cfg4 = json.load(open(S.config_path(), encoding="utf-8"))
        print("预算非法不落盘=%d" % int(
            _cfg4.get("api", {}).get("budgetTokens") == 30000
            and "整数" in st2.budget_error.cget("text")))
        st2.v_budget.set("0")
        st2.v_batch_budget.set("1000000")
        st2.save()
        app.update()
        _cfg5 = json.load(open(S.config_path(), encoding="utf-8"))
        print("预算落盘=%d" % int(
            _cfg5.get("api", {}).get("budgetTokens") == 0
            and "预算" not in st2.budget_error.cget("text")))
        # ── 协同状态机（真控件）：用合成暂存目录驱动「待人工核实 → 已交付」两态 ──
        app.show_tab(0)
        app.update()
        _coop_root = tempfile.mkdtemp(prefix="slcn_win_coop_ui_")
        _coop_src = os.path.join(_coop_root, "稿.docx")
        open(_coop_src, "w", encoding="utf-8").write("x")
        _coop_out = os.path.join(_coop_root, "期")
        _coop_stage = os.path.join(_coop_out, "A_待核")
        os.makedirs(_coop_stage)
        _coop_taskbook = os.path.join(_coop_stage, "A_文献核实.md")
        open(_coop_taskbook, "w", encoding="utf-8").write("# 任务书\\n")
        open(os.path.join(_coop_stage, "A_AI建议.md"), "w",
             encoding="utf-8").write("建议\\n")
        _coop_final = os.path.join(_coop_out, "A")
        os.makedirs(_coop_final)

        def _coop_state(phase, delivered="", stats=None):
            data = {"schema": 1, "phase": phase,
                    "source": {"path": os.path.abspath(_coop_src)},
                    "params": {"out_root": os.path.abspath(_coop_out), "stem": "A"},
                    "taskbook": _coop_taskbook,
                    "final_dir": _coop_final}
            if delivered:
                data["delivered_to"] = os.path.abspath(delivered)
            if stats is not None:
                data["stats"] = dict(stats)
            with open(os.path.join(_coop_stage, M.C.STATE_NAME), "w",
                      encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)

        _coop_state("ph1")
        ct.entry.delete(0, "end")
        ct.entry.insert(0, _coop_src)
        ct._coop_out = _coop_out
        ct.v_coop.set(True)
        ct._toggle_coop_panel()
        ct.apply_coop_result({"phase": "ph1", "stage": M.C.load_stage(_coop_stage)}, 0)
        app.update()
        print("协同状态待核=" + ct.v_coop_status.get())
        print("协同待核按钮=%d" % int(
            str(ct.coop_taskbook_btn.cget("state")) == "normal"
            and str(ct.coop_stage_btn.cget("state")) == "normal"
            and str(ct.coop_deliver_btn.cget("state")) == "disabled"))
        print("无回填禁用=%d" % int(
            str(ct.coop_import_btn.cget("state")) == "disabled"))
        # 批56：已选清单（文件多选 + 剪贴板可混合、累加；可移除/清空）
        _coop_r1 = os.path.join(_coop_root, "deepseek_json.json")
        _coop_r2 = os.path.join(_coop_root, "deepseek_bibtex.txt")
        for _p in (_coop_r1, _coop_r2):
            open(_p, "w", encoding="utf-8").write("x")
        ct.add_coop_replies([_coop_r1, _coop_r2])
        app.update()
        print("已选计数=%d" % ct._coop_sel.count)
        print("已选清单行数=%d" % ct.coop_list.size())
        print("已选后可运行=%d" % int(
            str(ct.coop_import_btn.cget("state")) == "normal"))
        app.clipboard_clear()
        app.clipboard_append("剪贴板回填：附一 JSON 内容")
        ct._import_clipboard_reply()
        app.update()
        print("剪贴板导入=%d" % int(
            ct._coop_sel.count == 3 and ct.coop_list.get(2).endswith(".md")))
        ct._clear_coop_replies()
        app.update()
        print("清空后禁用=%d" % int(
            ct._coop_sel.count == 0
            and str(ct.coop_import_btn.cget("state")) == "disabled"))
        # 批56：成功摘要（stats 口径）——有回填、不警告
        _coop_state("done", delivered=_coop_final,
                    stats={"bib_total": 9, "bib_replied": 9, "bib_pending": 0,
                           "low_total": 22, "low_replied": 13, "low_applied": 9,
                           "low_pending_manual": 13})
        open(os.path.join(_coop_final, M.C.RECORD_NAME), "w", encoding="utf-8").write(
            "- 并入：**9** 条")
        _stage_obj = M.C.load_stage(_coop_stage)
        ct.apply_coop_result({"phase": "ph2", "stage": _stage_obj,
                              "summary": M.C.summarize_stage(_stage_obj)}, 0)
        app.update()
        print("协同状态已交付=" + ct.v_coop_status.get())
        print("协同已交付按钮=%d" % int(
            str(ct.coop_deliver_btn.cget("state")) == "normal"
            and str(ct.coop_record_btn.cget("state")) == "normal"
            and str(ct.coop_suggest_btn.cget("state")) == "normal"
            and str(ct.coop_import_btn.cget("state")) == "disabled"))
        print("协同摘要=" + ct.v_coop_summary.get())
        print("摘要含stats=%d" % int(
            "文献回填 9/9（并入 9 条，未回填 0）" in ct.v_coop_summary.get()
            and "低置信回复 13（应用 9 / 仅记录待人工 13）" in ct.v_coop_summary.get()))
        print("无警告=%d" % int(ct.v_coop_warn.get() == ""))
        # 批56：0/9 文献回填 → 橙色警示（用户只导入了 json 的现场）
        _coop_state("done", delivered=_coop_final,
                    stats={"bib_total": 9, "bib_replied": 0, "bib_pending": 9,
                           "low_total": 22, "low_replied": 1, "low_applied": 9,
                           "low_pending_manual": 13})
        _stage_obj2 = M.C.load_stage(_coop_stage)
        ct.apply_coop_result({"phase": "ph2", "stage": _stage_obj2,
                              "summary": M.C.summarize_stage(_stage_obj2)}, 0)
        app.update()
        print("0回填警告=%d" % int(
            "本次没有文献条目回填" in ct.v_coop_warn.get()
            and "可能漏导入" in ct.v_coop_warn.get()))
        ct.v_coop.set(False)
        ct._toggle_coop_panel()
        shutil.rmtree(_coop_root, ignore_errors=True)
        app.destroy()
        print("窗口建成")
        print("标签页：" + "|".join(tabs))
    """)
    env2 = dict(os.environ)
    env2["PYTHONPATH"] = str(REPO / "Engine" / "vendor")
    env2["SLCN_BRIDGE_TEST_DIR"] = gdir
    env2["SLCN_BRIDGE_CFG"] = gcfg
    try:
        r = subprocess.run([tkpy, "-"], input=prog, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=env2, cwd=str(SHELL), timeout=180)
    except subprocess.TimeoutExpired:
        # headless（无窗口服务）时 Aqua Tk 会卡在 `update()`（本机实测；不是产品缺陷）：
        # 与下面「没有可用显示器」同一口径 SKIP，真窗口断言仍交 CI 覆盖。
        print("SKIP  (g) 真窗口子进程 180 秒未返回（本机无窗口服务 / headless；真窗口交 CI）")
        r = None
    out = (r.stdout or "") if r is not None else ""
    if r is not None and r.returncode != 0 and (
            "no display" in (r.stderr or "") or "TclError" in (r.stderr or "")):
        print(f"SKIP  (g) 没有可用显示器/窗口服务：{(r.stderr or '').strip().splitlines()[-1:]}")
    elif r is not None:
        win_out = out                       # (h) 组还要在这些输出里找 U4 的证据
        check("(g) 真开一次窗口：控件全部建成、写日志、正常退出",
              r.returncode == 0 and "窗口建成" in out,
              f"rc={r.returncode} {(r.stderr or '').strip()[-160:]}")
        check("(g) 十五个页都在（侧边栏顺序：单篇 2 + 整期 6 + 引用库 6 + 设置）",
              "初排转换|参考文献规范化|合并文件|一校核查|检查tex信息|生成目录|编译全部|"
              "删除临时文件|目录摘要推送|库概况|查库|入藏|待裁决|引用库设置|设置"
              in out, out.strip()[-240:])
        check("(g) **分组侧边栏**：两层结构（单篇稿件 / 整期装配 / 引用库 / 设置），"
              "不是 15 个平级标签",
              "侧栏=单篇稿件|整期装配|引用库|设置" in out,
              [l for l in out.splitlines() if l.startswith(("侧栏", "引用库侧栏"))])
        check("(g) 引用库 5 页仍在侧栏里（顺序：库概况/查库/入藏/待裁决/引用库设置）",
              "引用库页=库概况|查库|入藏|待裁决|引用库设置" in out,
              [l for l in out.splitlines() if l.startswith("引用库页")])
        check("(g) B-286：侧栏分组归属——单篇组 = 初排转换|一校核查；"
              "引用库组 = 库概况|查库|入藏|待裁决|参考文献规范化|引用库设置",
              "单篇组页=初排转换|一校核查" in out
              and "引用库组页=库概况|查库|入藏|待裁决|参考文献规范化|引用库设置" in out,
              [l for l in out.splitlines() if l.startswith(("单篇组页", "引用库组页"))])
        check("(g) 切页会同步侧栏选中态", "切页=tab:4" in out,
              [l for l in out.splitlines() if l.startswith("切页")])
        _vlines = [l.split("=", 1)[1] for l in out.splitlines() if l.startswith("版本标签=")]
        _vline = _vlines[0] if _vlines else ""
        check("(g) B-274：侧栏底部常驻「版本 x.y.z」（只有版本号、与引擎 __version__ 一致）",
              bool(_expected_ver) and _vline == f"版本 {_expected_ver}",
              _vline or [l for l in out.splitlines() if l.startswith("版本标签")])
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
        check("(g) 协同操作区默认隐藏：勾「AI 协同初排（两段式）」才展开，"
              "打开/回填/运行按钮默认禁用（第一段还没跑）",
              "协同默认关=1" in out and "协同面板默认隐藏=1" in out
              and "协同面板显示=1" in out and "协同按钮齐全=1" in out
              and "回填按钮存在=1" in out and "协同按钮默认禁用=1" in out,
              [l for l in out.splitlines()
               if l.startswith(("协同默认", "协同面板", "协同按钮", "回填按钮"))])
        check("(g) 批59 全文 AI 核查（真控件）：默认不勾、范围默认「引用上下文」；"
              "勾选后 ph1 argv 追加 --coop-fulltext context/full，取消后 argv 与批58"
              "逐字节相同；文案讲清「零上传、零费用」",
              "全文核查默认关=1" in out and "全文核查默认argv=1" in out
              and "全文核查context=1" in out and "全文核查full=1" in out
              and "全文核查取消复原=1" in out and "全文核查文案=1" in out,
              [l for l in out.splitlines() if l.startswith("全文核查")])
        check("(g) 批60 全文 AI 核查（直连 API，真控件）：默认不勾（会话态，不写 settings.json）；"
              "勾选后 ⑪ argv 追加 --llm-fulltext，取消后复原；提示讲清费用/不改产物",
              "直连全文默认关=1" in out and "直连全文默认argv=1" in out
              and "直连全文映射=1" in out and "直连全文取消复原=1" in out
              and "直连全文需AI=1" in out
              and "直连全文文案=1" in out and "直连全文不落盘=1" in out,
              [l for l in out.splitlines() if l.startswith("直连全文")])
        check("(g) 批62 入口收敛 B（真控件）：独立「出文献核实任务书」勾选已隐藏；普通初排默认 "
              "argv 无 --verify-refs；勾「用 AI 处理参考文献」后内部自动带（⑪），AI 说明/"
              "全文区按勾选展开收起；协同面板有引导文案；回读页（B-286 起名「参考文献规范化」）"
              "长标题在正文顶部、侧栏短名同步改名",
              "入口收敛默认关=1" in out and "入口收敛隐藏勾选=1" in out
              and "入口收敛AI展开=1" in out and "入口收敛AI提示=1" in out
              and "入口收敛默认argv=1" in out and "入口收敛AIargv=1" in out
              and "入口收敛取消复原=1" in out
              and any(l.startswith("入口收敛协同引导=") and "两段式第一段产物" in l
                      and "--verify-refs" in l for l in out.splitlines())
              and "回读页标题=参考文献规范化（高级：手动并回，离线）" in out
              and "回读页引导=1" in out,
              [l for l in out.splitlines()
               if l.startswith(("入口收敛", "回读页"))])
        check("(g) 设置页 AI 预算：非法值红字**不落盘**、合法值（0 = 不限）落盘",
              "预算非法不落盘=1" in out and "预算落盘=1" in out,
              [l for l in out.splitlines() if l.startswith("预算")])
        check("(g) 协同状态机（真控件）：ph1 → 「待人工核实（未交付）」+ 任务书/暂存"
              "按钮可用、交付按钮禁用；**没有回填时运行第二段按钮禁用**",
              any(l.startswith("协同状态待核=") and "待人工核实（未交付）" in l
                  for l in out.splitlines()) and "协同待核按钮=1" in out
              and "无回填禁用=1" in out,
              [l for l in out.splitlines()
               if l.startswith(("协同状态", "无回填"))])
        check("(g) 批56 已选清单（真控件）：文件多选 + 剪贴板可混合累加（2 → 3 项），"
              "清单可见、加入后可运行、清空后禁用",
              "已选计数=2" in out and "已选清单行数=2" in out
              and "已选后可运行=1" in out and "剪贴板导入=1" in out
              and "清空后禁用=1" in out,
              [l for l in out.splitlines()
               if l.startswith(("已选", "剪贴板", "清空"))])
        check("(g) 协同状态机（真控件）：ph2 成功 → 「已交付」+ 交付/记录/建议按钮可用、"
              "第二段按钮禁用；摘要按 stats 显示「文献回填 9/9…」且无 0 回填警告",
              any(l.startswith("协同状态已交付=") and "已交付" in l
                  for l in out.splitlines()) and "协同已交付按钮=1" in out
              and "摘要含stats=1" in out and "无警告=1" in out,
              [l for l in out.splitlines()
               if l.startswith(("协同状态", "协同已交付", "摘要", "无警告"))])
        check("(g) 批56 0/9 文献回填 → 橙色警示出现（「本次没有文献条目回填…可能漏导入」）",
              "0回填警告=1" in out,
              [l for l in out.splitlines() if l.startswith("0回填")])
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
    if _SKIP_GUI:
        print("SKIP  (h) 真窗口那一步没跑（SLCN_BRIDGE_SKIP_GUI=1）")
    else:
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
    (r"D:\稿件\2026年第2期(87)\02 Hai_Xing\reference.bib", ("2026年第2期(87)", "02 Hai_Xing")),
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

# ── B-261：高级检索 argv 契约（与 mac `queryAdvanced` 同一规则；判据仍在引擎）──
_adv_want = ["query-advanced", "--author", "Smith", "--mode", "author=fuzzy",
             "--limit", "5", "--strict", "--bibtex", "--json"]
_adv = E.bibdb_query_advanced_call(
    [{"field": "author", "mode": "fuzzy", "value": "Smith"}],
    sort="year", limit=5, strict=True)
check("(i) B-261 高级检索：每个 term 后**总是显式** `--mode 字段=模式`，"
      "再 `--limit`、`--strict`、`--bibtex --json`（逐字）",
      _adv.entry == "bibdb.py" and _adv.argv[-len(_adv_want):] == _adv_want,
      _adv.display[-160:])
_adv_default = E.bibdb_query_advanced_call(
    [{"field": "title", "mode": "contains", "value": "Theory"}])
check("(i) B-261 高级检索：非 strict 用显式 `--min-score=0.75`（不是引擎默认值）",
      "--strict" not in _adv_default.argv
      and "--min-score=0.75" in _adv_default.argv
      and "--min-score=0.9" not in _adv_default.argv, _adv_default.display[-160:])
_adv_db = E.bibdb_query_advanced_call(
    [{"field": "doi", "mode": "exact", "value": "10.1/x"}],
    min_score=0.5, db="/db/b.jsonl")
check("(i) B-261 高级检索：`--db` 在末尾、`--min-score` 带显式值、doi 用 exact",
      _adv_db.argv[-2:] == ["--db", "/db/b.jsonl"]
      and "--min-score=0.5" in _adv_db.argv
      and _adv_db.argv[_adv_db.argv.index("--doi") + 1] == "10.1/x"
      and _adv_db.argv[_adv_db.argv.index("--mode") + 1] == "doi=exact",
      _adv_db.display[-160:])
_adv_multi_want = ["query-advanced", "--title", "A", "--mode", "title=contains",
                   "--booktitle", "B", "--mode", "booktitle=prefix",
                   "--limit", "20", "--min-score=0.75", "--bibtex", "--json"]
_adv_multi = E.bibdb_query_advanced_call(
    [{"field": "title", "mode": "contains", "value": "A"},
     {"field": "booktitle", "mode": "prefix", "value": "B"}])
check("(i) B-261 高级检索：多行按界面顺序逐行拼（`--title A --mode title=contains` "
      "在前，`--booktitle B --mode booktitle=prefix` 在后）",
      _adv_multi.argv[-len(_adv_multi_want):] == _adv_multi_want,
      _adv_multi.display[-200:])
check("(i) B-261 高级检索：非法 field/mode/sort/limit/min_score/空行/value → "
      "ValueError（界面不许把错参数发给引擎）",
      _raises_value_error(E.bibdb_query_advanced_call, [])
      and _raises_value_error(E.bibdb_query_advanced_call, ["author"])
      and _raises_value_error(E.bibdb_query_advanced_call,
                              [{"field": "key", "mode": "exact", "value": "Foo"}])
      and _raises_value_error(E.bibdb_query_advanced_call,
                              [{"field": "author", "mode": "regex", "value": "Foo"}])
      and _raises_value_error(E.bibdb_query_advanced_call,
                              [{"field": "author", "mode": "contains", "value": "  "}])
      and _raises_value_error(E.bibdb_query_advanced_call,
                              [{"field": "author", "mode": "contains", "value": "Foo"}],
                              sort="date")
      and _raises_value_error(E.bibdb_query_advanced_call,
                              [{"field": "author", "mode": "contains", "value": "Foo"}],
                              limit=0)
      and _raises_value_error(E.bibdb_query_advanced_call,
                              [{"field": "author", "mode": "contains", "value": "Foo"}],
                              limit=201)
      and _raises_value_error(E.bibdb_query_advanced_call,
                              [{"field": "author", "mode": "contains", "value": "Foo"}],
                              min_score=1.5))
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

_rc, _lines = run_capture(E.bibdb_query_advanced_call(
    [{"field": "author", "mode": "contains", "value": "Foo"}], db=_cli_db))
_payload = E.parse_json_lines(_lines)
_adv_hits = (_payload or {}).get("hits") or []
_adv_matched = (_adv_hits[0].get("matched") or []) if _adv_hits else []
check("(i) CLI 冒烟：query-advanced --author Foo 命中 1 条、kind=advanced，"
      "并带回 why-hit matched[]（field/mode/score/snippet）",
      _rc == 0 and isinstance(_payload, dict) and _payload.get("kind") == "advanced"
      and len(_adv_hits) == 1 and _adv_hits[0].get("key") == "Foo2020"
      and any(m.get("field") == "author" and m.get("mode") == "contains"
              and "score" in m and "snippet" in m for m in _adv_matched),
      f"rc={_rc} hits={len(_adv_hits)} matched={_adv_matched[:1]}")
_rc, _lines = run_capture(E.bibdb_query_advanced_call(
    [{"field": "author", "mode": "contains", "value": "Nobody"}], db=_cli_db))
_payload = E.parse_json_lines(_lines)
check("(i) CLI 冒烟：query-advanced 无命中 → rc0、hits 空（不是程序错）",
      _rc == 0 and isinstance(_payload, dict) and _payload.get("hits") == []
      and _payload.get("total_matched") == 0,
      f"rc={_rc} total={(_payload or {}).get('total_matched')}")

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
print("\n── (j) 两段式 AI 协同初排（第五十五批）：argv / 状态发现 / 建议摘要 / 预算 ──")

# ── 两段 argv 契约（CLI 批54 冻结，外壳只拼参数）──
_j_base = E._base_cmd("convert.py") or []
_j_default = E.convert_call(source="a.docx", out="/o", compile_pdf=True, verify_refs=True)
check("(j) 默认 convert argv 与批54 **逐字节相同**（不勾两段式、没设预算时一个参数都不加）",
      _j_default.argv == _j_base + ["--source", "a.docx", "--out", "/o",
                                    "--compile", "--verify-refs"],
      _j_default.display[:140])
check("(j) 默认 argv 里没有任何协同/预算参数",
      not any(a.startswith("--coop") or a.startswith("--ai-budget")
              or a.startswith("--ai-batch") for a in _j_default.argv))

_j_ph1 = E.convert_call(source="/稿/a.docx", out="/期", compile_pdf=True, verify_refs=False,
                        verify_plan=True, coop_taskbook_only=True, coop_stage="/期/A_待核")
check("(j) 第一段 argv = 现有参数 + --verify-refs --no-ai-merge --coop-taskbook-only "
      "--coop-stage",
      _j_ph1.argv == _j_base + ["--source", "/稿/a.docx", "--out", "/期", "--compile",
                                "--verify-refs", "--no-ai-merge", "--coop-taskbook-only",
                                "--coop-stage", "/期/A_待核"], _j_ph1.display[:180])
check("(j) 第一段**绝不**传 --verify-plan（引擎该分支写任务书前就 return 0），"
      "也不传 --llm-*/--search-refs（外部 chat 通道不调 API）",
      "--verify-plan" not in _j_ph1.argv
      and not any(a.startswith("--llm-") or a == "--search-refs" for a in _j_ph1.argv))
check("(j) 页面没勾「出文献核实任务书」也强制 --verify-refs（第一段的全部产出就是任务书）",
      "--verify-refs" in _j_ph1.argv)
_j_ph1_no_stage = E.convert_call(source="a", out="/o", coop_taskbook_only=True)
check("(j) 没给 --coop-stage 时第一段不带它（引擎用默认 `<out>/<stem>_待核/`）",
      "--coop-stage" not in _j_ph1_no_stage.argv)

# ── 批59：全文 AI 核查（默认关；仅第一段追加 --coop-fulltext context|full）──
_j_ph1_58 = E.convert_call(source="/稿/a.docx", out="/期", compile_pdf=True,
                           verify_plan=True, coop_taskbook_only=True,
                           coop_stage="/期/A_待核")
check("(j) 批59：不勾「全文 AI 核查」→ 第一段 argv 与批58 **逐字节相同**"
      "（一个 --coop-fulltext 都没有；默认零变化）",
      _j_ph1_58.argv == _j_ph1.argv and "--coop-fulltext" not in _j_ph1_58.argv,
      _j_ph1_58.display[:190])
_j_ft_ctx = E.convert_call(source="/稿/a.docx", out="/期", compile_pdf=True,
                           coop_taskbook_only=True, coop_stage="/期/A_待核",
                           coop_fulltext="context")
check("(j) 批59：勾选「引用上下文」→ `--coop-fulltext context` 追加在 "
      "--coop-taskbook-only 之后、--coop-stage 之前（其余 argv 逐字节不变）",
      _j_ft_ctx.argv == _j_base + [
          "--source", "/稿/a.docx", "--out", "/期", "--compile", "--verify-refs",
          "--no-ai-merge", "--coop-taskbook-only", "--coop-fulltext", "context",
          "--coop-stage", "/期/A_待核"], _j_ft_ctx.display[:200])
_j_ft_full = E.convert_call(source="/稿/a.docx", out="/期", compile_pdf=True,
                            coop_taskbook_only=True, coop_stage="/期/A_待核",
                            coop_fulltext="full")
check("(j) 批59：勾选「全文」→ 同一位置是 `--coop-fulltext full`"
      "（两种范围只差取值，位置/其余参数不变）",
      _j_ft_full.argv == _j_base + [
          "--source", "/稿/a.docx", "--out", "/期", "--compile", "--verify-refs",
          "--no-ai-merge", "--coop-taskbook-only", "--coop-fulltext", "full",
          "--coop-stage", "/期/A_待核"]
      and _j_ft_full.argv.index("--coop-fulltext")
      == _j_ft_ctx.argv.index("--coop-fulltext"), _j_ft_full.display[:200])
check("(j) 批59：--coop-fulltext 只在第一段有效 —— 普通转换 / 第二段传入 → ValueError",
      _raises_value_error(E.convert_call, source="a", out="/o", coop_fulltext="full")
      and _raises_value_error(E.convert_call, source="a", out="/o",
                              coop_replies="/回填", coop_fulltext="context"))
check("(j) 批59：非法范围（引擎只认 context/full）→ ValueError，不许发出去",
      _raises_value_error(E.convert_call, source="a", out="/o",
                          coop_taskbook_only=True, coop_fulltext="正文")
      and _raises_value_error(E.convert_call, source="a", out="/o",
                              coop_taskbook_only=True, coop_fulltext="FULL"))
check("(j) 批59：`C.coop_fulltext_mode` 不勾=空串（默认零变化）；勾选按范围映射；"
      "未知范围回落 context（宁可少发正文）",
      C.coop_fulltext_mode(False, C.FULLTEXT_FULL) == ""
      and C.coop_fulltext_mode(False, C.FULLTEXT_CONTEXT) == ""
      and C.coop_fulltext_mode(True, C.FULLTEXT_CONTEXT) == "context"
      and C.coop_fulltext_mode(True, C.FULLTEXT_FULL) == "full"
      and C.coop_fulltext_mode(True, "其他") == "context")
check("(j) 批59：范围常量与引擎 argparse 取值逐字一致（context/full，面板单选顺序）",
      C.FULLTEXT_MODES == ("context", "full")
      and C.FULLTEXT_CONTEXT == "context" and C.FULLTEXT_FULL == "full")

# ── 批60：全文 AI 核查（直连 API，--llm-fulltext；默认关；仅 ⑪ 直连通道）──
_j_llm_58 = E.convert_call(source="a.docx", out="/o", compile_pdf=True, verify_refs=True)
check("(j) 批60：不勾「全文 AI 核查（直连 API）」→ ⑪ argv 与批59 **逐字节相同**"
      "（一个 --llm-fulltext 都没有）",
      _j_llm_58.argv == _j_base + ["--source", "a.docx", "--out", "/o",
                                   "--compile", "--verify-refs"]
      and "--llm-fulltext" not in _j_llm_58.argv, _j_llm_58.display[:180])
_j_llm_on = E.convert_call(source="a.docx", out="/o", compile_pdf=True,
                           verify_refs=True, llm_fulltext=True)
check("(j) 批60：勾选且跑 ⑪ → 在 --verify-refs 之后**恰好**追加一次无取值的 "
      "`--llm-fulltext`（引擎 action=store_true；后面不接取值）",
      _j_llm_on.argv == _j_base + ["--source", "a.docx", "--out", "/o", "--compile",
                                   "--verify-refs", "--llm-fulltext"]
      and _j_llm_on.argv.count("--llm-fulltext") == 1, _j_llm_on.display[:180])
check("(j) 批60：勾了但本次**不是 ⑪** → 一个字节都不加（普通转换 / 先算账 / 两段式 "
      "ph1、ph2 都不受影响；引擎会把这些组合判 exit 2，外壳在拼装点就**不制造**它们）",
      "--llm-fulltext" not in E.convert_call(source="a", out="/o",
                                             llm_fulltext=True).argv
      and "--llm-fulltext" not in E.convert_call(
          source="a", out="/o", verify_refs=True, verify_plan=True,
          llm_fulltext=True).argv
      and "--llm-fulltext" not in E.convert_call(
          source="a", out="/o", verify_refs=True, coop_taskbook_only=True,
          llm_fulltext=True).argv
      and "--llm-fulltext" not in E.convert_call(
          source="a", out="/o", coop_replies="/回填", llm_fulltext=True).argv)
_j_llm_budget = E.convert_call(source="a", out="/o", verify_refs=True,
                               llm_fulltext=True, ai_budget_tokens=0)
check("(j) 批60：全文 flag 与既有直连预算参数同路（`--llm-fulltext` 不影响 "
      "`--ai-budget-tokens` 的拼装/位置契约）",
      "--llm-fulltext" in _j_llm_budget.argv
      and _j_llm_budget.argv[_j_llm_budget.argv.index("--ai-budget-tokens") + 1] == "0")

# ── 批62：入口收敛 B（普通默认无 --verify-refs；use_ai 内部等价 ⑪）──
_j62_default = E.convert_call(source="a.docx", out="/o", compile_pdf=True)
check("(j) 批62：普通初排默认 argv **不含 --verify-refs**（入口收敛的行为变化："
      "默认不再出《文献核实任务书》；argv 与「不勾任务书」时逐字节相同）",
      _j62_default.argv == _j_base + ["--source", "a.docx", "--out", "/o", "--compile"]
      and "--verify-refs" not in _j62_default.argv, _j62_default.display[:180])
_j62_ai = E.convert_call(source="a.docx", out="/o", compile_pdf=True, use_ai=True)
check("(j) 批62：勾「用 AI 处理参考文献」→ 内部等价 ⑪：argv 与批61 的 AI 路径"
      "**逐字节相同**（内部自动带 --verify-refs，不带 --verify-plan）",
      _j62_ai.argv == _j_base + ["--source", "a.docx", "--out", "/o", "--compile",
                                 "--verify-refs"]
      and _j62_ai.argv == E.convert_call(source="a.docx", out="/o", compile_pdf=True,
                                         verify_refs=True).argv, _j62_ai.display[:180])
check("(j) 批62：`verify_refs` 字段保留兼容旧设置/旧调用（显式 True 行为不变；"
      "B 只是界面不再默认提供，普通转换默认值是 False）",
      "--verify-refs" in E.convert_call(source="a", out="/o", verify_refs=True).argv
      and "--verify-refs" not in E.convert_call(source="a", out="/o").argv)
check("(j) 批62 边界：coop ph1 仍**强制** --verify-refs（任务书是第一段产物）、ph2 绝不带、"
      "先算账时仍只带 --verify-plan（不假装核实）；这些不随入口收敛改变",
      "--verify-refs" in E.convert_call(source="a", out="/o", use_ai=True,
                                       coop_taskbook_only=True).argv
      and "--verify-refs" not in E.convert_call(source="a", out="/o",
                                                coop_replies="/r", use_ai=True).argv
      and E.convert_call(source="a", out="/o", use_ai=True,
                         verify_plan=True).argv[-1] == "--verify-plan")
check("(j) 批62：批60 全文直连也随入口收敛 —— 只有 ⑪（use_ai/verify_refs）时才追加 "
      "`--llm-fulltext`；普通转换传了 llm_fulltext=True 也不进 argv",
      "--llm-fulltext" in E.convert_call(source="a", out="/o", use_ai=True,
                                         llm_fulltext=True).argv
      and "--llm-fulltext" not in E.convert_call(source="a", out="/o",
                                                 llm_fulltext=True).argv)

_j_ph2_single = E.convert_call(source="/稿/a.docx", out="/期", coop_replies="/回填",
                               coop_stage="/期/A_待核", coop_apply=True, compile_pdf=True,
                               verify_refs=True, name="A", author="某",
                               ai_budget_tokens=0)
check("(j) 第二段单路径 argv 与批55 **逐字节相同**（最小集；页面参数/--verify-refs/"
      "--clean/预算一律不带，引擎从状态继承）",
      _j_ph2_single.argv == _j_base + ["--source", "/稿/a.docx", "--out", "/期",
                                       "--coop-replies", "/回填",
                                       "--coop-stage", "/期/A_待核",
                                       "--coop-apply", "--compile"],
      _j_ph2_single.display[:180])
_j_ph2 = E.convert_call(source="/稿/a.docx", out="/期", coop_replies="/回填",
                        coop_stage="/期/A_待核", coop_apply=True, compile_pdf=True,
                        clean=True)
check("(j) 批56：页面「清理临时文件」开着时第二段末尾带 `--clean`"
      "（交付目录不再留 .aux/.bbl/… 中间文件）",
      _j_ph2.argv[-5:] == ["--coop-stage", "/期/A_待核", "--coop-apply",
                           "--compile", "--clean"]
      and "--clean" in _j_ph2.argv, _j_ph2.display[:180])
_j_ph2_multi = E.convert_call(
    source="/稿/a.docx", out="/期",
    coop_replies=["/下载/deepseek_json_a.json", "/下载/deepseek_bibtex_b.txt"],
    coop_stage="/期/A_待核", clean=True)
check("(j) 批56：多路径按已选顺序输出**重复** `--coop-replies`（多选文件不再折叠父目录/"
      "不再复制临时目录）",
      _j_ph2_multi.argv == _j_base + [
          "--source", "/稿/a.docx", "--out", "/期",
          "--coop-replies", "/下载/deepseek_json_a.json",
          "--coop-replies", "/下载/deepseek_bibtex_b.txt",
          "--coop-stage", "/期/A_待核", "--clean"], _j_ph2_multi.display[:200])
check("(j) 3 个路径 → 3 个 `--coop-replies` flag（每个路径独立传）",
      E.convert_call(source="a", out="/o", coop_replies=["/r1", "/r2", "/r3"]).argv.count(
          "--coop-replies") == 3)
check("(j) 空列表 / 列表含空串 / 含全空白 → ValueError（不许静默丢回填）",
      _raises_value_error(E.convert_call, source="a", out="/o", coop_replies=[])
      and _raises_value_error(E.convert_call, source="a", out="/o",
                              coop_replies=["/r1", ""])
      and _raises_value_error(E.convert_call, source="a", out="/o",
                              coop_replies=["/r1", "   "]))
check("(j) 列表项去首尾空白；tuple 也接受（界面传 list，脚本可传 tuple）",
      E.convert_call(source="a", out="/o", coop_replies=(" /r1 ", " /r2 ")).argv[
          -4:] == ["--coop-replies", "/r1", "--coop-replies", "/r2"])
check("(j) 第二段 label 写明「AI 协同初排·第二段」（日志里两段分得清）",
      _j_ph2_single.label == "AI 协同初排·第二段", _j_ph2_single.label)
check("(j) 不勾「一键修改产物」时第二段不带 --coop-apply",
      "--coop-apply" not in E.convert_call(source="a", out="/o", coop_replies="/r").argv)
check("(j) 参数组合反例 → ValueError（两段同给 / 只有 --coop-apply / --coop-stage 不配两段式）",
      _raises_value_error(E.convert_call, source="a", out="/o",
                          coop_taskbook_only=True, coop_replies="/r")
      and _raises_value_error(E.convert_call, source="a", out="/o", coop_apply=True)
      and _raises_value_error(E.convert_call, source="a", out="/o", coop_stage="/s"))

# ── 预算参数：值 != 引擎默认才传；协同两段（外部 chat）一个都不传 ──
_j_budget = E.convert_call(source="a", out="/o", ai_budget_tokens=0,
                           ai_batch_budget_tokens=500)
check("(j) 预算 kwargs 只在给了值时进 argv（0 = 不限，要真的传 \"0\"）",
      _j_budget.argv[_j_budget.argv.index("--ai-budget-tokens") + 1] == "0"
      and _j_budget.argv[_j_budget.argv.index("--ai-batch-budget-tokens") + 1] == "500")
_j_ph1_budget = E.convert_call(source="a", out="/o", coop_taskbook_only=True,
                               ai_budget_tokens=0, ai_batch_budget_tokens=7)
check("(j) 协同两段是外部 chat 通道 → 预算参数一个都不传（软件零费用）",
      "--ai-budget-tokens" not in _j_ph1_budget.argv
      and "--ai-batch-budget-tokens" not in _j_ph1_budget.argv)
check("(j) 非法预算（负数/非整数/bool）→ ValueError，不许悄悄发给引擎",
      _raises_value_error(E.convert_call, source="a", out="/o", ai_budget_tokens=-1)
      and _raises_value_error(E.convert_call, source="a", out="/o", ai_budget_tokens="30000")
      and _raises_value_error(E.convert_call, source="a", out="/o",
                              ai_batch_budget_tokens=True))

# ── settings.py：预算默认值 / 解析 / 合并 / 传 CLI 规则（纯函数）──
check("(j) settings 默认预算 = 引擎 argparse 默认 30000 / 1000000（两处口径一致）",
      S.DEFAULTS["api"]["budgetTokens"] == S.AI_BUDGET_DEFAULT == 30000
      and S.DEFAULTS["api"]["batchBudgetTokens"] == S.AI_BATCH_BUDGET_DEFAULT == 1000000)
check("(j) 预算输入解析：留空 = 默认、0 = 不限、负数/非整数 = 红字不落盘",
      S.parse_budget_tokens("", 30000) == (30000, None)
      and S.parse_budget_tokens("0", 30000) == (0, None)
      and S.parse_budget_tokens("50000", 30000) == (50000, None)
      and S.parse_budget_tokens("-1", 30000)[0] is None
      and "负数" in (S.parse_budget_tokens("-1", 30000)[1] or "")
      and S.parse_budget_tokens("abc", 30000)[0] is None
      and "整数" in (S.parse_budget_tokens("abc", 30000)[1] or ""))
check("(j) 旧配置/垃圾值合并：非法预算回落默认、合法值保留（0 也算合法）",
      S._merge_defaults({})["api"]["budgetTokens"] == 30000
      and S._merge_defaults({"api": {"budgetTokens": 0}})["api"]["budgetTokens"] == 0
      and S._merge_defaults({"api": {"budgetTokens": "30000",
                                     "batchBudgetTokens": -1}})["api"]["batchBudgetTokens"]
      == 1000000)
_j_ai = {"api": {**S.DEFAULTS["api"], "base": "https://api.example.com", "model": "m"}}
check("(j) 直连 API 就绪判据 = 地址 + 模型名（与引擎 LLMConfig.ready 同口径；密钥可空）",
      S.ai_configured(_j_ai)
      and not S.ai_configured({"api": {**_j_ai["api"], "model": ""}})
      and not S.ai_configured({}))
check("(j) 预算传 CLI 规则：未配置 API / 值 == 默认 / 协同两段 → 一个都**不传**",
      S.ai_budget_convert_args({}) == {}
      and S.ai_budget_convert_args(_j_ai) == {}
      and S.ai_budget_convert_args({"api": {**_j_ai["api"], "model": ""}}) == {}
      and S.ai_budget_convert_args(
          {"api": {**_j_ai["api"], "budgetTokens": 0}}, coop=True) == {})
_j_ai2 = {"api": {**_j_ai["api"], "budgetTokens": 0, "batchBudgetTokens": 2}}
check("(j) 值 != 引擎默认才传（0 真的要传；单批不同也各自传）",
      S.ai_budget_convert_args(_j_ai2) == {"ai_budget_tokens": 0,
                                           "ai_batch_budget_tokens": 2})
check("(j) 设置 → argv 直通：`ai_budget_convert_args` 的 kwargs 真的进 convert 命令行",
      E.convert_call(source="a", out="/o", **S.ai_budget_convert_args(_j_ai2)).argv
      == _j_base + ["--source", "a", "--out", "/o",
                    "--ai-budget-tokens", "0", "--ai-batch-budget-tokens", "2"])

# ── coop.py：状态发现 / 建议摘要 / 多选回复（headless，合成暂存目录）──
_j_root = tempfile.mkdtemp(prefix="slcn_bridge_coop_")
try:
    _j_src = os.path.join(_j_root, "稿.docx")
    with open(_j_src, "w", encoding="utf-8") as _fh:
        _fh.write("x")
    _j_out = os.path.join(_j_root, "2026年第5期(99)")
    os.makedirs(_j_out, exist_ok=True)
    _j_stage = os.path.join(_j_out, "A_待核")
    os.makedirs(_j_stage, exist_ok=True)
    _j_taskbook = os.path.join(_j_stage, "A_文献核实.md")
    with open(_j_taskbook, "w", encoding="utf-8") as _fh:
        _fh.write("# 任务书\n")

    def _write_state(phase: str, *, source=None, out_root=None, delivered="") -> None:
        state = {
            "schema": 1, "phase": phase,
            "source": {"path": os.path.abspath(source or _j_src)},
            "params": {"out_root": os.path.abspath(out_root or _j_out), "stem": "A"},
            "taskbook": _j_taskbook,
            "final_dir": os.path.join(_j_out, "A"),
        }
        if delivered:
            state["delivered_to"] = os.path.abspath(delivered)
        with open(os.path.join(_j_stage, C.STATE_NAME), "w", encoding="utf-8") as _fh:
            json.dump(state, _fh, ensure_ascii=False)

    _write_state("ph1")
    _j_found = C.discover_stage(_j_out, source=_j_src)
    check("(j) 暂存目录发现：扫 `<out>/*_待核/` 按 state.source.path + params.out_root 匹配；"
          "ph1 → 「待人工核实（未交付）」",
          _j_found is not None and _j_found.stage_dir == os.path.abspath(_j_stage)
          and _j_found.status_text == C.STATUS_PENDING
          and _j_found.stem == "A" and _j_found.taskbook == _j_taskbook,
          str(_j_found))
    check("(j) 状态文案口径（两壳一致，与 mac `CoopFeature.stageStatusText` 对齐）："
          "ph1 → 待人工核实（未交付）；ph2 → 上次第二段未完成；done → 已交付；"
          "没有状态 → 尚未初排；不认识 → 状态未知（不猜）",
          (C.status_text("ph1"), C.status_text("ph2"), C.status_text("done"),
           C.status_text(None), C.status_text("weird"))
          == (C.STATUS_PENDING, C.STATUS_PH2, C.STATUS_DELIVERED,
              C.STATUS_NOT_STARTED, "状态未知（weird）"))
    check("(j) 源稿对不上 / 状态里的 out_root 对不上 → 不认（绝不猜另一个暂存目录）",
          C.discover_stage(_j_out, source=os.path.join(_j_root, "别的稿.docx")) is None)
    _write_state("ph1", out_root=os.path.join(_j_root, "另一期"))
    check("(j) out_root 不匹配 → 不认（同一输出根里可能躺着别的期目录的暂存）",
          C.discover_stage(_j_out, source=_j_src) is None)
    _write_state("done", delivered=os.path.join(_j_out, "A"))
    _j_done = C.load_stage(_j_stage)
    check("(j) phase=done → 「已交付」；交付目录只认 `delivered_to`（ph1 阶段为空，不许冒充交付）",
          _j_done is not None and _j_done.status_text == C.STATUS_DELIVERED
          and _j_done.delivery_dir == os.path.abspath(os.path.join(_j_out, "A"))
          and _j_done.record_path.endswith(C.RECORD_NAME))
    with open(os.path.join(_j_stage, "A_AI建议.jsonl"), "w", encoding="utf-8") as _fh:
        _fh.write('{"status": "accepted", "id": "L1", "field": "year"}\n'
                  '{"status": "accepted", "id": "L1", "field": "pages"}\n'
                  '{"status": "rejected", "id": "L2", "reason": "span 不在原文"}\n'
                  '{"status": "missing", "id": "L3"}\n'
                  "不是 JSON 的坏行（忽略）\n")
    _j_sum = C.suggestions_summary(_j_stage, "A")
    check("(j) 建议摘要：从 `<stage>/<stem>_AI建议.jsonl` 统计 accepted/rejected/missing"
          "（坏行忽略，不炸）",
          (_j_sum["accepted"], _j_sum["rejected"], _j_sum["missing"], _j_sum["total"])
          == (2, 1, 1, 4), str(_j_sum))
    check("(j) 建议清单文案含「接受/拒绝/未回填」三个数（清单口径，不是成功摘要）；"
          "没有清单时说清第二段后才有",
          "接受 2" in C.suggestions_text(_j_sum)
          and "拒绝 1" in C.suggestions_text(_j_sum)
          and "未回填 1" in C.suggestions_text(_j_sum)
          and "第二段成功后生成"
          in C.suggestions_text(C.suggestions_summary(_j_stage, "没有这篇")))
    # 多个候选（同一篇稿改过输出名）：取状态文件**最近修改**的一个，候选数交给界面提示
    _j_stage2 = os.path.join(_j_out, "A2_待核")
    os.makedirs(_j_stage2, exist_ok=True)
    with open(os.path.join(_j_stage, C.STATE_NAME), encoding="utf-8") as _fh:
        _j_state_done = json.load(_fh)
    with open(os.path.join(_j_stage2, C.STATE_NAME), "w", encoding="utf-8") as _fh:
        json.dump(_j_state_done, _fh, ensure_ascii=False)
    os.utime(os.path.join(_j_stage2, C.STATE_NAME),
             (os.path.getmtime(os.path.join(_j_stage, C.STATE_NAME)) + 10,) * 2)
    _j_multi = C.discover_stage(_j_out, source=_j_src)
    check("(j) 多个匹配暂存目录（同稿改过输出名）→ 取状态文件最近修改的一个，"
          "候选数交给界面提示（不静默挑错）",
          _j_multi is not None and _j_multi.stage_dir == os.path.abspath(_j_stage2)
          and _j_multi.found == 2,
          str(_j_multi and (_j_multi.stage_dir, _j_multi.found)))
    with open(os.path.join(_j_stage2, C.STATE_NAME), "w", encoding="utf-8") as _fh:
        json.dump({"schema": 1, "params": {"stem": "A2"}}, _fh)   # 没有 phase
    _j_after = C.discover_stage(_j_out, source=_j_src)
    check("(j) 状态文件没有 `phase` → 不算可续跑（与 mac parseState 同口径）",
          C.load_stage(_j_stage2) is None
          and _j_after is not None
          and _j_after.stage_dir != os.path.abspath(_j_stage2))
    shutil.rmtree(_j_stage2, ignore_errors=True)

    # ── 批56：state.stats 成功摘要 / 旧状态回退 / 0 回填警示 ──
    _j_stats_stage = os.path.join(_j_root, "统计_待核")
    _j_stats_final = os.path.join(_j_root, "统计")
    os.makedirs(_j_stats_stage, exist_ok=True)
    os.makedirs(_j_stats_final, exist_ok=True)
    _j_stats = {"bib_total": 9, "bib_replied": 9, "bib_pending": 0,
                "low_total": 22, "low_replied": 13, "low_applied": 9,
                "low_pending_manual": 13}
    _j_state_stats = {"schema": 1, "phase": "done",
                      "source": {"path": os.path.abspath(_j_src)},
                      "params": {"out_root": os.path.abspath(_j_out), "stem": "统计"},
                      "taskbook": "", "final_dir": _j_stats_final,
                      "delivered_to": _j_stats_final, "stats": dict(_j_stats),
                      "applied_low_confidence": 9}
    _j_stats_state_path = os.path.join(_j_stats_stage, C.STATE_NAME)
    with open(_j_stats_state_path, "w", encoding="utf-8") as _fh:
        json.dump(_j_state_stats, _fh, ensure_ascii=False)
    with open(os.path.join(_j_stats_final, C.RECORD_NAME), "w", encoding="utf-8") as _fh:
        _fh.write("## 二、reference.bib 并入\n\n- 并入：**9** 条\n")
    _j_stage_obj = C.load_stage(_j_stats_stage)
    check("(j) stats 读取：批56 状态文件的 7 个字段全收；与 mac 同口径（任一新字段存在即解析、"
          "缺的按 0）；完全没有 stats → None 回退旧状态",
          C.state_stats(_j_state_stats) == _j_stats
          and C.state_stats({"stats": {"bib_total": 9}}) == {
              "bib_total": 9, "bib_replied": 0, "bib_pending": 0, "low_total": 0,
              "low_replied": 0, "low_applied": 0, "low_pending_manual": 0}
          and C.state_stats({"stats": {"bib_total": 9, "low_applied": "9"}})["low_applied"] == 0
          and C.state_stats({}) is None)
    _j_summary = C.summarize_stage(_j_stage_obj)
    check("(j) 成功摘要口径（与 mac 逐字一致）：「文献回填 9/9（并入 9 条，未回填 0）｜"
          "低置信回复 13（应用 9 / 仅记录待人工 13）」",
          _j_summary["text"] == C.stats_line(_j_stats, merged=9)
          and _j_summary["text"] == ("文献回填 9/9（并入 9 条，未回填 0）｜"
                                     "低置信回复 13（应用 9 / 仅记录待人工 13）"),
          _j_summary["text"])
    check("(j) 有文献回填时**不**显示 0 回填警告（只有 bib_total>0 且 replied=0 才橙色警示）；"
          "新 stats 不标 legacy",
          _j_summary["warning"] == "" and C.bib_zero_warning(_j_stats) == ""
          and _j_summary["legacy"] is False)
    _j_zero = dict(_j_stats, bib_replied=0, bib_pending=9)
    check("(j) 0/9 文献回填 → 橙色警示点名「可能漏导入 bibtex/bib、可重新初排后再导入」",
          "本次没有文献条目回填" in C.bib_zero_warning(_j_zero)
          and "可能漏导入" in C.bib_zero_warning(_j_zero)
          and "重新初排" in C.bib_zero_warning(_j_zero))
    check("(j) bib_total=0（没有文献键）不误报 0 回填警告",
          C.bib_zero_warning({"bib_total": 0, "bib_replied": 0}) == "")
    with open(os.path.join(_j_stats_final, C.RECORD_NAME), "w", encoding="utf-8") as _fh:
        _fh.write("- 解析到 8 条，拒绝 0 条，未回填 1 条\n"
                  "- 接受 12 条，拒绝 1 条，未回填 9 条\n"
                  "- 并入：**7** 条\n")
    _j_rec = C.stats_from_record(os.path.join(_j_stats_final, C.RECORD_NAME),
                                 state=_j_state_stats)
    check("(j) 旧状态回退：解析 `_AI协同记录.md` 的解析/低置信 summary 行 + 并入数",
          _j_rec == {"bib_total": 9, "bib_replied": 8, "bib_pending": 1,
                     "low_total": 22, "low_replied": 13, "low_applied": 9,
                     "low_pending_manual": 13}, str(_j_rec))
    _j_no_stats = {key: value for key, value in _j_state_stats.items() if key != "stats"}
    with open(_j_stats_state_path, "w", encoding="utf-8") as _fh:
        json.dump(_j_no_stats, _fh, ensure_ascii=False)
    _j_old_summary = C.summarize_stage(C.load_stage(_j_stats_stage))
    check("(j) 旧状态（无 stats）仍给摘要：从 record 回退，并入 z 取记录里的 7；"
          "并标「估算」（legacy=True）",
          _j_old_summary["text"].startswith("文献回填 8/9（并入 7 条，未回填 1）")
          and _j_old_summary["legacy"] is True, _j_old_summary["text"])
    with open(_j_stats_state_path, "w", encoding="utf-8") as _fh:
        json.dump({"schema": 1, "phase": "done",
                   "source": {"path": os.path.abspath(_j_src)},
                   "params": {"out_root": os.path.abspath(_j_out), "stem": "统计"},
                   "taskbook": "", "final_dir": _j_stats_final,
                   "delivered_to": _j_stats_final,
                   "keys": {"bib": ["K1", "K2"], "low_confidence": ["L1", "L2", "L3"]},
                   "replied": ["K1"], "applied_low_confidence": 1},
                  _fh, ensure_ascii=False)
    try:
        os.remove(os.path.join(_j_stats_final, C.RECORD_NAME))
    except OSError:
        pass
    _j_deep = C.summarize_stage(C.load_stage(_j_stats_stage))
    check("(j) 最老状态（无 stats 无 record）→ 按 keys/replied/建议清单回退，仍给三组数；"
          "并入数读不到时明确说「并入条数见协同记录」（不拿回填数冒充并入数）",
          _j_deep["stats"]["bib_total"] == 2 and _j_deep["stats"]["bib_replied"] == 1
          and "文献回填 1/2" in _j_deep["text"]
          and "并入条数见协同记录" in _j_deep["text"]
          and "低置信回复" in _j_deep["text"], _j_deep["text"])
    shutil.rmtree(_j_stats_stage, ignore_errors=True)
    shutil.rmtree(_j_stats_final, ignore_errors=True)

    # ── 批56：已选回填清单 + 剪贴板回填命名（纯逻辑，与 mac 同语义）──
    _j_r1 = os.path.join(_j_root, "回复1.json")
    _j_r2 = os.path.join(_j_root, "回复2.txt")
    for _path in (_j_r1, _j_r2):
        with open(_path, "w", encoding="utf-8") as _fh:
            _fh.write("x")
    _j_sel = C.ReplySelection()
    check("(j) 已选清单：文件/目录/剪贴板可混合、累加；空串跳过；标题「已选 N 项」",
          _j_sel.add([_j_r1, _j_out, ""]) == (2, 1) and _j_sel.count == 2
          and _j_sel.label() == "已选 2 项", str(_j_sel.paths))
    check("(j) 已选清单去重（同一路径按绝对/normcase 判等；重复导入不重复传）",
          _j_sel.add([_j_r1, "  "]) == (0, 2) and _j_sel.count == 2)
    check("(j) 已选清单可按路径/下标移除、清空（界面「移除选中 / 全部清除」的纯逻辑）",
          _j_sel.remove(_j_out) and _j_sel.remove_index(0) == _j_r1
          and _j_sel.count == 0 and _j_sel.clear() == 0)
    _j_clip_dir = tempfile.mkdtemp(prefix="slcn_bridge_clip_")
    try:
        _j_clip = C.write_clipboard_reply("回填 JSON 内容", _j_clip_dir)
        _j_clip2 = C.write_clipboard_reply("回填 BibTeX 内容", _j_clip_dir)
        check("(j) 剪贴板回填命名纯逻辑：`回填剪贴板-YYYYmmdd-HHMMSS-<seq>.md`"
              "（同秒多次粘贴序号递增、不覆盖）",
              re.search(r"回填剪贴板-\d{8}-\d{6}-1\.md$",
                        os.path.basename(_j_clip)) is not None
              and re.search(r"回填剪贴板-\d{8}-\d{6}-2\.md$",
                            os.path.basename(_j_clip2)) is not None
              and open(_j_clip, encoding="utf-8").read().strip() == "回填 JSON 内容")
        check("(j) 空剪贴板/纯空白 → ValueError（界面红字提示，不生成空回填文件）",
              _raises_value_error(C.write_clipboard_reply, "  \n ", _j_clip_dir))
        check("(j) 剪贴板回填加入已选清单后按顺序作为独立 --coop-replies 传给引擎",
              C.ReplySelection().add([_j_clip, _j_clip2]) == (2, 0)
              and E.convert_call(source="a", out="/o",
                                 coop_replies=[_j_clip, _j_clip2]).argv.count(
                                     "--coop-replies") == 2)
    finally:
        shutil.rmtree(_j_clip_dir, ignore_errors=True)
finally:
    shutil.rmtree(_j_root, ignore_errors=True)

# ── main.py 源码守卫：协同 UI 的接线点（真窗口在 (g)；这里防「没接上」）──
# B-230：这些页面已搬进 `tabs/*.py`，源码守卫扫整个外壳（all_src）
check("(j) 初排转换页：默认关的「AI 协同初排（两段式）」+ 协同操作区默认隐藏",
      "AI 协同初排（两段式）" in all_src and "self.v_coop" in all_src
      and "coop_panel.pack_forget()" in all_src)
check("(j) 协同操作区按钮齐全（打开任务书/暂存目录/交付目录/协同记录/建议清单 + "
      "回填导入：添加文件/目录/剪贴板/移除选中/全部清除/运行第二段）",
      all(k in all_src for k in ("打开任务书", "打开暂存目录", "打开交付目录",
                                 "打开协同记录", "打开建议清单",
                                 "添加回填文件…", "添加回填目录…", "从剪贴板导入",
                                 "移除选中", "全部清除", "运行第二段…")))
check("(j) 批56：已选清单可见（「已选 N 项」+ Listbox），不再复制临时目录/折叠父目录",
      "已选 " in all_src and "self.v_coop_sel" in all_src
      and "self.coop_list" in all_src and "ReplySelection" in all_src
      and "prepare_replies" not in all_src)
check("(j) 批56：第二段把**已选清单**按顺序传给引擎（重复 --coop-replies）+ "
      "页面 clean 开着时带 --clean",
      "coop_replies=paths" in all_src
      and "clean=bool(self.v_clean.get())" in all_src)
check("(j) 批56：成功摘要读 `summarize_stage`（stats 口径）+ 0 回填橙色警告"
      "（theme 颜色，不写死色号）",
      "C.summarize_stage" in all_src and "v_coop_warn" in all_src
      and 'theme.COLOR["warn"]' in all_src)
check("(j) 第二段复用第一段选定的输出根（`_coop_out` + `set_coop_out`），"
      "第一段走 `coop_ph1_call`",
      "_coop_out" in all_src and "tab.set_coop_out(out)" in all_src
      and "coop_ph1_call(out)" in all_src and "coop_ph2_call(" in all_src)
check("(j) 源稿改过 → 第二段先报「请重新运行第一段」（不拿旧暂存硬续跑）",
      "源稿与第一段时不一致" in all_src)
check("(j) 「一键修改产物（--coop-apply）」默认不勾 + 说明 `.ai前` 备份；"
      "应用决定在运行前定（勾选值在拼 argv 时取）",
      "一键修改产物（--coop-apply）" in all_src and "v_coop_apply" in all_src
      and ".ai前" in all_src and "coop_apply=bool(self.v_coop_apply.get())" in all_src)
check("(j) 协同结果经队列回主线程（`__COOP__` + `apply_coop_result`），不跨线程碰控件",
      '"__COOP__"' in all_src and "apply_coop_result" in all_src
      and "C.load_stage" in all_src and "C.discover_stage" in all_src)
check("(j) 批59：全文 AI 核查面板默认不勾（复选框 + 「引用上下文/全文」单选；静态说明"
      "与 mac 逐字一致：默认不勾/零上传/零费用/不再弹二次确认/仅第一段生效）；"
      "ph1 拼 argv 走 `C.coop_fulltext_mode`",
      "全文 AI 核查（任务书附正文）" in all_src
      and "self.v_coop_fulltext = tk.BooleanVar(value=False)" in all_src
      and "引用上下文（默认" in all_src and "全文（约 8 万字上限）" in all_src
      and 'args["coop_fulltext"] = C.coop_fulltext_mode(self.v_coop_fulltext.get()' in all_src
      and "默认不勾：任务书不含正文" in all_src
      and "已获授权，不再弹二次确认" in all_src
      and "第二段续跑与交付不受影响" in all_src)
check("(j) 批60：初排页「全文 AI 核查（直连 API，会产生费用）」默认不勾；提示与 mac "
      "逐字对齐（会产生费用/每片 ≤3 万字/report-only 不改 reference.bib/未配置端点退出 2）；"
      "勾选值经 `extra_args` 交给拼装点",
      "全文 AI 核查（直连 API，会产生费用）" in all_src
      and "self.v_llm_fulltext = tk.BooleanVar(value=False)" in all_src
      and 'args["llm_fulltext"] = bool(self.v_llm_fulltext.get())' in all_src
      and "每片 ≤3 万字" in all_src and "不改 reference.bib" in all_src
      and "直接退出 2" in all_src and "report-only" in all_src)
check("(j) 批60：**会话态不落盘**（Lead 拍板：正文外发+费用属每次运行的主动同意）——"
      "`llmFulltext` 不进 settings.py / settings.json 写入路径（main.py 只准在注释里"
      "提 mac 的同名字段）；真进 argv 时日志说明「会产生费用、report-only」与"
      "「端点未配置会 exit 2」",
      "llmFulltext" not in (SHELL / "settings.py").read_text(encoding="utf-8")
      and all_src.count("llmFulltext") == 1
      and "Options.llmFulltext" in all_src
      and '"--llm-fulltext" in call.argv' in all_src
      and "但端点未配置" in all_src and "会产生费用" in all_src)
check("(j) 批62：初排页**不再暴露**独立「出文献核实任务书」勾选（`v_refs` 字段保留、"
      "默认 False）；新增「用 AI 处理参考文献（直连 API，会产生费用）」显式勾选"
      "（默认不勾、会话态），勾选值经 `extra_args` 的 `use_ai` 交给拼装点",
      '_check(row1, "出文献核实任务书"' not in all_src
      and "self.v_refs = tk.BooleanVar(value=False)" in all_src
      and "用 AI 处理参考文献（直连 API，会产生费用）" in all_src
      and "self.v_ai = tk.BooleanVar(value=False)" in all_src
      and 'args["use_ai"] = bool(self.v_ai.get())' in all_src
      and "self.v_ai.set(True)" not in all_src)   # 只由用户点勾，代码不得偷偷打开
check("(j) 批62 + B-286：两段式面板引导文案与 mac 逐字一致；回读页（现名「参考文献规范化」）"
      "定位标题随改名同步为「参考文献规范化（高级：手动并回，离线）」、侧栏短名同改、能力保留"
      "（并回/入藏/离线体检），并明确「本页不生成任务书」+ 两段式第二段自动完成 + 引用库入口",
      "文献核实任务书 = 两段式第一段产物" in all_src
      and "CLI --verify-refs（高级）" in all_src
      and 'page_title = "参考文献规范化（高级：手动并回，离线）"' in all_src
      and 'name = "参考文献规范化"' in all_src
      and "本页不生成《文献核实任务书》" in all_src
      and "第二段自动完成" in all_src and "引用库 → 参考文献规范化" in all_src
      and "并回产物 reference.bib" in all_src and "入藏到引用库" in all_src)
check("(j) 协同运行**不把 API 配置传给引擎**（`api=None`，外部 chat 零上传/零费用；"
      "与 mac 强制 useAI=false 同口径）",
      "api=None if coop_phase else" in all_src)
check("(j) 失败提示含**退出码 + 日志尾部**（`E.output_tail(tail…)`），不只一句「失败」",
      "E.output_tail(tail" in all_src)
check("(j) 设置页有单篇/单批 token 预算输入 + 红字错误位；隐私文案讲清"
      "「外部 chat 不上传任何内容、零费用」",
      "单篇 token 预算" in all_src and "单批 token 预算" in all_src
      and "budget_error" in all_src and "不上传任何内容" in all_src
      and "零费用" in all_src)
check("(j) 三个纯逻辑模块都不 import tkinter（engine/settings/coop 可 headless 测）",
      all(not re.search(r"^\s*(?:import|from)\s+tkinter", (SHELL / name).read_text(
          encoding="utf-8"), re.M) for name in ("engine.py", "settings.py", "coop.py")))

# ─────────────────────────────────────────────────────────────────────────
# ── (k) B-281：Windows 与 mac 的「页面对等」+ clean 三模式静态守卫 ──────────
# 背景：`sources-win/README.md` 从前写「与 mac 版功能对等」「功能已对齐：mac 版的每一
# 项」，但实际有三处**已声明差异**（mac 独有「目录摘要推送」；引用库第四页 mac 叫「待
# 处理」、Windows 叫「待裁决」；整期装配组内顺序不同）。这里把「允许的差异」钉成硬编
# 码清单：mac 导航（NavPage/title/分组）或 Windows 导航（main.py: self.tabs / nav_groups）
# 改了却没同步这张清单 + README + 《使用说明》§3.3 → 这组红。清单**故意硬编码**（不跟
# 源码自动漂移）：改导航必须人工审一遍并同步三处文档。
print("\n── (k) Windows/mac 对等清单 + clean 三模式静态守卫 ──")
MAC_MAIN = REPO / "sources-mac" / "Views" / "MainWindow.swift"
WIN_README = (SHELL / "README.md").read_text(encoding="utf-8")
CLEAN_SRC = (SHELL / "tabs" / "clean.py").read_text(encoding="utf-8")
MAIN_SRC = (SHELL / "main.py").read_text(encoding="utf-8")

#: mac `NavPage.allCases` 顺序 + `title`（读 MainWindow.swift 解析后必须等于这张表）。
MAC_PAGES = [
    ("convert", "初排转换"), ("checkbib", "参考文献规范化"), ("proofcheck", "一校核查"),
    ("merge", "合并文件"), ("infocheck", "检查tex信息"), ("compile", "编译全部"),
    ("toc", "生成目录"), ("cleantemp", "删除临时文件"), ("dirpush", "目录摘要推送"),
    ("bibstats", "库概况"), ("bibquery", "查库"), ("bibimport", "入藏"),
    ("bibreview", "待处理"), ("bibsettings", "引用库设置"), ("settings", "设置"),
]
#: mac 侧栏三组（Section 顺序即此顺序；设置页在组外）。
MAC_GROUPS = [
    ("单篇稿件", ["convert", "proofcheck"]),
    ("整期装配", ["infocheck", "compile", "toc", "cleantemp", "merge", "dirpush"]),
    ("引用库", ["bibstats", "bibquery", "bibimport", "bibreview", "checkbib", "bibsettings"]),
]
#: Windows 导航 15 页：顺序 = main.py `self.tabs`；标题 = 各 Tab 类的 `name`（侧栏用它）。
WIN_TITLES = [
    "初排转换", "参考文献规范化", "合并文件", "一校核查", "检查tex信息",
    "生成目录", "编译全部", "删除临时文件",
    "目录摘要推送",                                  # B-289：整期装配最后一步
    "库概况", "查库", "入藏", "待裁决", "引用库设置", "设置",
]
#: Windows 侧栏三组（= main.py `nav_groups`；索引指向 `self.tabs`；设置页 = 组外末尾）。
WIN_GROUPS = [
    ("单篇稿件", ["初排转换", "一校核查"]),
    ("整期装配", ["检查tex信息", "生成目录", "编译全部", "删除临时文件", "合并文件",
                  "目录摘要推送"]),
    ("引用库", ["库概况", "查库", "入藏", "待裁决", "参考文献规范化", "引用库设置"]),
]
WIN_GROUP_INDEX = [("单篇稿件", (0, 3)), ("整期装配", (4, 5, 6, 7, 2, 8)),
                   ("引用库", (9, 10, 11, 12, 1, 13))]


def _parse_mac_nav() -> tuple[list[tuple[str, str]], list[tuple[str, list[str]]]]:
    """从 Swift 源码抠 NavPage 的 case 顺序/标题/分组（只读文本，不跑 swiftc）。"""
    src = MAC_MAIN.read_text(encoding="utf-8")
    block = re.search(r"enum NavPage[^{]*\{(.*?)^\}", src, re.S | re.M)
    if not block:
        raise ValueError("MainWindow.swift 里找不到 `enum NavPage`")
    body = block.group(1)
    cases: list[str] = []
    for line in body.splitlines():
        m = re.match(r"\s*case\s+([a-z]+(?:\s*,\s*[a-z]+)*)\s*$", line)
        if m:
            cases += [x.strip() for x in m.group(1).split(",") if x.strip()]
    title_block = re.search(r"var title: String \{(.*?)\n    \}", body, re.S)
    if not title_block:
        raise ValueError("MainWindow.swift 里找不到 NavPage.title")
    titles: dict[str, str] = {}
    for m in re.finditer(r'case\s+\.([a-z]+):\s*return\s+"([^"]+)"', title_block.group(1)):
        titles[m.group(1)] = m.group(2)
    pages = [(c, titles[c]) for c in cases if c in titles]

    def group(name: str) -> list[str]:
        m = re.search(rf"static let {name}: \[NavPage\] = \[([^\]]*)\]", body)
        if not m:
            raise ValueError(f"MainWindow.swift 里找不到 NavPage.{name}")
        return [x.strip().lstrip(".") for x in m.group(1).split(",") if x.strip()]

    return pages, [("单篇稿件", group("singleArticle")),
                   ("整期装配", group("assembly")),
                   ("引用库", group("library"))]


def _parse_win_nav() -> tuple[list[str], list[tuple[str, tuple[int, ...]]]]:
    """从 main.py 源码 AST 抠 `self.tabs` 顺序与 `nav_groups`（不执行 tkinter）。

    页标题取各 Tab 类的 `name` 类属性（侧栏 `self.tabs[i].name` 用的就是它）；类名从
    `__init__` 里的构造调用（`self.clean_tab = _cl.CleanTab(...)`）映射出来。
    """
    tree = ast.parse(MAIN_SRC)
    win_cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    init = next(n for n in win_cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    classes: dict[str, str] = {}
    order: list[str] = []
    groups: list[tuple[str, tuple[int, ...]]] = []
    for node in ast.walk(init):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                and target.value.id == "self"):
            continue
        if target.attr == "tabs" and isinstance(node.value, ast.List):
            order = [e.attr for e in node.value.elts
                     if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name)
                     and e.value.id == "self"]
        elif target.attr == "nav_groups":
            groups = list(ast.literal_eval(node.value))
        elif isinstance(node.value, ast.Call):
            func = node.value.func
            classes[target.attr] = (func.attr if isinstance(func, ast.Attribute)
                                    else func.id if isinstance(func, ast.Name) else "")

    class_titles: dict[str, str] = {}
    sources = [*sorted((SHELL / "tabs").glob("*.py")), SHELL / "bibdb_ui.py"]
    for py in sources:
        for node in ast.parse(py.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.ClassDef):
                continue
            for st in node.body:
                if (isinstance(st, ast.Assign) and len(st.targets) == 1
                        and isinstance(st.targets[0], ast.Name)
                        and st.targets[0].id == "name"
                        and isinstance(st.value, ast.Constant)
                        and isinstance(st.value.value, str)):
                    class_titles[node.name] = st.value.value
    titles = [class_titles.get(classes.get(attr, ""), "") for attr in order]
    return titles, groups


try:
    _mac_pages, _mac_groups = _parse_mac_nav()
except (OSError, ValueError) as exc:          # 源码结构变了 → 让下面的断言红，而不是炸掉
    _mac_pages, _mac_groups = [], []
    print(f"  （mac 导航解析失败：{exc}）")
_win_titles, _win_group_index = _parse_win_nav()
_win_groups = [(name, [_win_titles[i] for i in idxs]) for name, idxs in _win_group_index]

check("(k) mac NavPage 顺序/标题 = 硬编码对等清单（改 mac 导航须同步本清单与文档）",
      _mac_pages == MAC_PAGES, f"实际 {_mac_pages}")
check("(k) mac 侧栏分组（singleArticle/assembly/library）= 硬编码清单（组顺序 + 组内页顺序）",
      _mac_groups == MAC_GROUPS, f"实际 {_mac_groups}")
check("(k) Windows 导航 15 页：顺序/标题 = 硬编码清单（标题取各 Tab 类 name 属性）",
      _win_titles == WIN_TITLES, f"实际 {_win_titles}")
check("(k) Windows 侧栏分组 = 硬编码清单（组顺序 + 组内页顺序；设置页组外）",
      _win_group_index == WIN_GROUP_INDEX and _win_groups == WIN_GROUPS,
      f"实际 {_win_group_index}")
_mac_title_set = {t for _, t in MAC_PAGES} - {"待处理"}
_win_title_set = set(WIN_TITLES) - {"待裁决"}
check("(k) 页集合对等（B-289 起两壳 15/15；只允许已声明差异：引用库第四页标题不同）",
      _win_title_set == _mac_title_set
      and len(_win_titles) == 15 and len(MAC_PAGES) == 15 and len(set(WIN_TITLES)) == 15)
_mac_title_of = dict(MAC_PAGES)
_mac_group_norm = []
for _name, _pages in MAC_GROUPS:
    _titles = [_mac_title_of[p] for p in _pages]
    if _name != "整期装配":
        _titles = ["待裁决" if t == "待处理" else t for t in _titles]
    _mac_group_norm.append((_name, _titles))
check("(k) 分组对等（组名/组顺序一致；只允许已声明差异：整期装配顺序不同、bibreview 标题不同）",
      [n for n, _ in _mac_group_norm] == [n for n, _ in _win_groups]
      and _mac_group_norm[0] == _win_groups[0]
      and sorted(_mac_group_norm[1][1]) == sorted(_win_groups[1][1])
      and _mac_group_norm[1][1] != _win_groups[1][1]      # 顺序差异存在，且已声明
      and _mac_group_norm[2] == _win_groups[2])
_mac_titles_all = [t for _, t in MAC_PAGES]
check("(k) 差异只剩已声明的两处：bibreview 标题（待处理/待裁决）/ 整期装配顺序",
      [t for t in WIN_TITLES if t not in _mac_titles_all] == ["待裁决"]
      and [t for t in _mac_titles_all if t not in WIN_TITLES] == ["待处理"]
      and len(_mac_titles_all) == len(WIN_TITLES) == 15)

check("(k) sources-win/README.md 声明 15 页 / 13 入口 / 只剩两处已声明差异",
      "## 十五个页面" in WIN_README
      and "**15 个页面**" in WIN_README
      and "十三个入口" in WIN_README
      and "只剩两处已声明差异" in WIN_README
      and "目录摘要推送" in WIN_README)
check("(k) sources-win/README.md 把差异指向《使用说明》§3.3",
      "差异见《使用说明》§3.3" in WIN_README)
check("(k) sources-win/README.md 的 clean 页说明 = 三模式 + 回收站/清空回收站",
      all(s in WIN_README for s in (
          "编译临时文件", "交付收尾（白名单）", "工作区旧件（每期留最新合并 PDF）",
          "清空回收站", ".toolbox-trash")))

check("(k) clean_logic：三模式标题/顺序与 mac `CleanMode.title` 逐字一致",
      [CL.mode_title(m) for m in CL.MODE_ORDER]
      == ["编译临时文件", "交付收尾（白名单）", "工作区旧件（每期留最新合并 PDF）"]
      and CL.mode_labels() == [CL.MODE_TITLES[m] for m in CL.MODE_ORDER])
check("(k) clean_logic：模式说明与 mac `CleanMode.detail` 逐字一致",
      CL.mode_detail("aux")
      == "清编译中间文件；不动 .tex/.pdf/.sty/.bib/.log，也不进 original/（作者来稿）。"
      and CL.mode_detail("delivery").startswith("交付前收尾：保留白名单与 tex 引用闭包")
      and CL.mode_detail("workspace").startswith("工作区旧件：每期只留最新一份「合并*.pdf」"))
check("(k) clean_logic 不 import tkinter（纯逻辑；mac/Linux/CI 可 headless 断言）",
      not re.search(r"^\s*(?:import|from)\s+tkinter",
                    (SHELL / "clean_logic.py").read_text(encoding="utf-8"), re.M))
_argv_pairs = [
    ({"trash": True}, {}),
    ({"dry_run": True, "trash": True}, {"dry_run": True}),
    ({"mode": "delivery"}, {"mode": "delivery"}),
    ({"mode": "delivery", "dry_run": True}, {"mode": "delivery", "dry_run": True}),
    ({"mode": "workspace", "delete": True}, {"mode": "workspace", "delete": True}),
]
check("(k) 清理 argv：engine `cleantemp_call` 与 clean_logic `cleantemp_args` 逐字节一致；"
      "aux 默认参数与从前完全不变",
      all(E.cleantemp_call(folder="/x", **ekw).argv[2:] == CL.cleantemp_args("/x", **ckw)
          for ekw, ckw in _argv_pairs)
      and E.cleantemp_call(folder="/x").argv[2:] == ["--folder", "/x"]
      and E.cleantemp_call(folder="/x", dry_run=True).argv[2:] == ["--folder", "/x", "--dry-run"]
      and E.cleantemp_call(folder="/x", dry_run=True, trash=True).argv[2:]
      == ["--folder", "/x", "--dry-run", "--trash"]
      and E.cleantemp_call(folder="/x", empty_trash=True).argv[2:]
      == ["--folder", "/x", "--empty-trash"]
      and E.cleantemp_call(folder="/x", restore_trash="/x/.toolbox-trash/t").argv[2:]
      == ["--folder", "/x", "--restore", "/x/.toolbox-trash/t"])
check("(k) 撤销条件：三模式真跑都有撤销；预演/永久删/清空回收站/撤销自身都没有",
      CL.undo_available(E.cleantemp_call(folder="/x", trash=True).argv)
      and CL.undo_available(E.cleantemp_call(folder="/x", mode="delivery").argv)
      and CL.undo_available(E.cleantemp_call(folder="/x", mode="workspace").argv)
      and not CL.undo_available(
          E.cleantemp_call(folder="/x", dry_run=True, trash=True).argv)
      and not CL.undo_available(
          E.cleantemp_call(folder="/x", mode="delivery", delete=True).argv)
      and not CL.undo_available(E.cleantemp_call(folder="/x", empty_trash=True).argv)
      and not CL.undo_available(
          E.cleantemp_call(folder="/x", restore_trash="/x/.toolbox-trash/t").argv))
check("(k) 确认框文案：三模式都写明「可撤销」并点名清什么；清空回收站写明"
      "「不可撤销」「永久」「无法恢复」「还想撤销…请先取消」",
      all("可撤销" in CL.confirm_text(m)[0] + CL.confirm_text(m)[1] for m in CL.MODE_ORDER)
      and "编译中间文件" in CL.confirm_text("aux")[1]
      and "交付残留" in CL.confirm_text("delivery")[1]
      and "工作区旧件" in CL.confirm_text("workspace")[1]
      and CL.empty_trash_text()[0] == "清空回收站（不可撤销）"
      and "永久" in CL.empty_trash_text()[1]
      and "无法恢复" in CL.empty_trash_text()[1]
      and "还想撤销" in CL.empty_trash_text()[1]
      and "请先点「取消」" in CL.empty_trash_text()[1])
check("(k) clean 页静态守卫：三模式下拉 + 模式说明 + 「清空回收站…」按钮 + 先预演勾选都在",
      all(s in CLEAN_SRC for s in (
          "清理模式：", "CL.mode_labels()", "mode_detail", "mode()",
          "清空回收站…", "confirm_empty_trash", "先预演（只列出会删哪些，不真删）", "v_dry")))
check("(k) main.py：运行按模式传参；撤销判定走 undo_available（--trash / --mode 两条"
      "成功路径都能挂出「撤销清理」）",
      "tab.mode()" in MAIN_SRC and "undo_available(call.argv)" in MAIN_SRC
      and '"撤销清理"' in MAIN_SRC
      and "trash=(mode == _clean.DEFAULT_MODE)" in MAIN_SRC)
check("(k) 清空回收站成功不给撤销：页内标明永久/不可撤销（empty argv 无撤销）",
      "永久删除" in CLEAN_SRC and "不可撤销" in CLEAN_SRC
      and not CL.undo_available(["cleantemp.py", "--folder", "/x", "--empty-trash"]))


# ─────────────────────────────────────────────────────────────────────────
print("\n── (l) 环境自检 envcheck（B-283：八项体检 / 诊断包 / 非阻断）──")

import inspect  # noqa: E402

import envcheck_logic as L  # noqa: E402  B-283：纯逻辑（无 tkinter / 无 engine）
_SPEC_EV = importlib.util.spec_from_file_location("_envcheck_for_test",
                                                  REPO / "Engine" / "envcheck.py")
_EV = importlib.util.module_from_spec(_SPEC_EV)
_SPEC_EV.loader.exec_module(_EV)

check("(l) 引擎与外壳的八项检查 id **同一份、同一顺序**"
      "（外壳自己判缺哪项 = 迟早与引擎分叉）",
      tuple(_EV.CHECK_IDS) == tuple(L.CHECK_IDS) == (
          "engine_bundle", "xelatex", "biber", "libreoffice",
          "git", "disk_space", "output_writable", "template_bundle"),
      f"{L.CHECK_IDS}")

check("(l) 设置页的 argv：`envcheck.py --json`（界面要解析 JSON；人类可读那份给命令行）",
      E.envcheck_call().entry == "envcheck.py"
      and E.envcheck_call().argv[-1] == "--json"
      and E.envcheck_call().argv[-2].endswith("envcheck.py"))
c_ev = E.envcheck_call(out_dir=r"C:\\tmp\\envcheck", profile="exe")
check("(l) `--out-dir` 与 `--profile` 都按顺序追加（诊断包要看输出目录可写）",
      c_ev.argv[-4:] == ["--out-dir", r"C:\\tmp\\envcheck", "--profile", "exe"], c_ev.display)
check("(l) 反例：不给 out_dir 就**不传** `--out-dir`（output_writable 只在给了才查）",
      "--out-dir" not in E.envcheck_call().argv
      and "--profile" not in E.envcheck_call().argv)
check("(l) 反例：`json_output=False` → 不传 `--json`（命令行人类可读报告）",
      "--json" not in E.envcheck_call(json_output=False).argv)

_GOOD = {
    "schema": "envcheck.v1", "profile": "source", "platform": "Darwin 27.0.0 (arm64)",
    "checks": [
        {"id": "engine_bundle", "title": "引擎运行时", "level": "required", "status": "ok",
         "version": "1.4.1", "detail": "源码模式", "hint": "", "install": {"mac": "", "windows": ""}},
        {"id": "git", "title": "git", "level": "optional", "status": "warn", "version": "",
         "detail": "未找到 git", "hint": "只影响笔记仓 / 引用库的版本与同步操作 安装：xcode-select --install",
         "install": {"mac": "xcode-select --install", "windows": "winget install --id Git.Git -e"}},
    ],
    "summary": {"ok": 1, "warn": 1, "fail": 0},
}
check("(l) 解析容忍引擎混进来的 stderr 行：取第一个 `{` 到最后一个 `}`",
      L.parse("warning: xx\n" + json.dumps(_GOOD, ensure_ascii=False) + "\n尾行")["schema"]
      == "envcheck.v1")
check("(l) 反例：不是 envcheck.v1 / 根本不是 JSON → 返回 {}（界面说「没读到」而不是崩）",
      L.parse("not json") == {} and L.parse('{"schema": "other.v1"}') == {}
      and L.parse([""]) == {})
_rows = L.check_rows(_GOOD)
check("(l) 行规范化：状态记号 / 级别文字 / Windows 安装命令都取到",
      _rows[0]["mark"] == "✅" and _rows[1]["mark"] == "⚠️"
      and _rows[1]["level_text"] == "可选"
      and _rows[1]["install_windows"] == "winget install --id Git.Git -e")
check("(l) 正文只在**缺项**时打安装提示（与引擎人类报告同口径）",
      any("→" in ln for ln in L.body_lines(_GOOD))
      and not any("→" in ln for ln in L.body_lines(
          {"schema": "envcheck.v1", "checks": [_GOOD["checks"][0]], "summary": {"ok": 1}},
      )))
check("(l) 小结三态：必需项缺失 > 可选缺失 > 全绿（退出码口径 2 / 1 / 0）",
      L.summary_line({"checks": _GOOD["checks"], "summary": {"ok": 1, "warn": 1, "fail": 0}})
      .startswith("⚠️")
      and L.summary_line({"checks": _GOOD["checks"], "summary": {"ok": 1, "warn": 0, "fail": 1}})
      .startswith("❌")
      and L.summary_line({"checks": _GOOD["checks"], "summary": {"ok": 2, "warn": 0, "fail": 0}})
      .startswith("✅"))
check("(l) 「复制安装命令」只列缺项的 Windows 命令，不是把整张表抄一遍",
      L.missing_installs(_GOOD) == [("git", "winget install --id Git.Git -e")])

_diag = L.diagnostics_text(_GOOD, app_version="1.4.1", engine_version="1.4.1",
                           platform_text="Windows 11", run_mode="发布包（exe）",
                           generated_at="2026-10-30 12:00:00")
check("(l) 诊断包正文含版本 / 系统 / 形态 / 结论，且写明不含稿件与密钥",
      all(k in _diag for k in ("App 版本：1.4.1", "引擎版本：1.4.1", "操作系统：Windows 11",
                               "运行形态：发布包（exe）", "✅ 引擎运行时", "⚠️ git",
                               "不含稿件内容、作者信息、API Key 或引用库内容")))
check("(l) 隐私守卫是**结构性的**：`diagnostics_text` 的参数表里没有配置/密钥/稿件入口",
      set(inspect.signature(L.diagnostics_text).parameters) == {
          "payload", "app_version", "engine_version", "platform_text",
          "run_mode", "generated_at"},
      f"{list(inspect.signature(L.diagnostics_text).parameters)}")

_zip_dir = tempfile.mkdtemp(prefix="bridge_envcheck_")
try:
    _zip_path = os.path.join(_zip_dir, "环境诊断.zip")
    _names = L.write_package(_zip_path, _diag, _GOOD)
    import zipfile  # noqa: E402
    with zipfile.ZipFile(_zip_path) as _zf:
        _got = _zf.namelist()
        _json_text = _zf.read("envcheck.json").decode("utf-8")
        _txt = _zf.read("diagnostics.txt").decode("utf-8")
    check("(l) 诊断包正好两个文件：diagnostics.txt + envcheck.json（C5）",
          _names == ["diagnostics.txt", "envcheck.json"]
          and sorted(_got) == ["diagnostics.txt", "envcheck.json"]
          and json.loads(_json_text)["schema"] == "envcheck.v1"
          and _txt == _diag)
finally:
    shutil.rmtree(_zip_dir, ignore_errors=True)

SETTINGS_TAB_SRC = (SHELL / "tabs" / "settings_tab.py").read_text(encoding="utf-8")
BUILD_PS1 = (SHELL / "build-win.ps1").read_text(encoding="utf-8")
check("(l) 设置页有「环境检查」「导出诊断包…」「复制安装命令」三个按钮，"
      "并走 `E.envcheck_call` + `envcheck_logic`（界面里不重写判据）",
      all(s in SETTINGS_TAB_SRC for s in (
          "环境检查", "导出诊断包…", "复制安装命令", "E.envcheck_call(",
          "envcheck_logic as L", "L.body_lines(", "L.write_package(",
          'self.cfg["envCheckDone"] = True')))
check("(l) 子线程不碰 Tk：结果经 `queue.Queue` + 主线程 `after` 轮询回来自（项目纪律）",
      "self.after(120" in SETTINGS_TAB_SRC and "queue.Queue()" in SETTINGS_TAB_SRC
      and "self._env_queue.put(" in SETTINGS_TAB_SRC)
check("(l) Windows 打包入口表也含 envcheck.py / diagnose.py / dirpush.py，"
      "且与外壳 ENTRIES **同一张表**",
      "envcheck.py" in BUILD_PS1 and "diagnose.py" in BUILD_PS1
      and "dirpush.py" in BUILD_PS1
      and "十三个入口" in BUILD_PS1
      and all(e in BUILD_PS1 for e in E.ENTRIES)
      and BUILD_PS1.count("envcheck.py") >= 2)
check("(l) settings：`envCheckDone` 默认 False、只收 bool（旧配置缺字段/垃圾值不炸）",
      S.DEFAULTS["envCheckDone"] is False
      and S._merge_defaults({})["envCheckDone"] is False
      and S._merge_defaults({"envCheckDone": True})["envCheckDone"] is True
      and S._merge_defaults({"envCheckDone": "yes"})["envCheckDone"] is False)
check("(l) 非阻断：设置页的检查只改一句状态说明，不返回、不禁用、不弹错误框"
      "（缺依赖时仍能点保存/转换）",
      "PLACEHOLDER" in (SHELL / "envcheck_logic.py").read_text(encoding="utf-8")
      and "done_text" in SETTINGS_TAB_SRC)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (m) 待裁决写库 + 本地版本层（B-282a）──")

import subprocess  # noqa: E402

import bibdb_git as GitLayer  # noqa: E402  B-282a：写库后只提交库文件

_git_src = (SHELL / "bibdb_git.py").read_text(encoding="utf-8")
check("(m) `bibdb_git.py` 存在、纯逻辑（不 import tkinter）、五个入口都在",
      bool(_git_src) and not re.search(r"^\s*(?:import|from)\s+tkinter", _git_src, re.M)
      and all(f"def {name}(" in _git_src
              for name in ("find_git", "locate_repo", "preflight", "commit_db", "status")),
      f"{len(_git_src)} 字符")
check("(m) 版本层只用列表 argv / 无 shell=True / 不 push、pull、fetch、切分支",
      "shell=True" not in _git_src
      and all(f'"{word}"' not in _git_src for word in ("push", "pull", "fetch", "checkout")))
check("(m) 库文件的唯一合法相对路径与 mac 同值",
      GitLayer.DB_RELATIVE_PATH == "bib_db/bibliography.jsonl")

check("(m) commit message 与 mac 同格式：label / 记录数 / 12 位 sha 前后",
      GitLayer.commit_message("裁决 C003 keep", 1, "a" * 64, "b" * 64)
      == "引用库：裁决 C003 keep（记录 1 条；sha aaaaaaaaaaaa→bbbbbbbbbbbb）")
check("(m) 空 label → 「写库」；note 第二行；sha 不足 12 位不补零",
      GitLayer.commit_message("", 12, "abc", "")
      == "引用库：写库（记录 12 条；sha abc→）"
      and GitLayer.commit_message("替换 C007", 1, "a" * 12, "b" * 12, note="人工核对")
      == "引用库：替换 C007（记录 1 条；sha aaaaaaaaaaaa→bbbbbbbbbbbb）\n备注：人工核对")

_git_bin = GitLayer.find_git()
check("(m) find_git()：找到的路径必须真实存在（找不到返回 None，写库仍可用）",
      _git_bin is None or os.path.isfile(_git_bin), str(_git_bin))
check("(m) 失败 soft 语义：空库路径 / 不存在文件都不抛异常、返回 ok=False + error",
      (lambda r1, r2: (r1.get("ok") is False and bool(r1.get("error"))
                       and r2.get("ok") is False and bool(r2.get("error"))
                       and r1.get("soft") is True))(GitLayer.commit_db("", "m"),
                                                      GitLayer.commit_db(
                                                          os.path.join(tempfile.gettempdir(),
                                                                       "no-such-b282.jsonl"),
                                                          "m")))

if _git_bin is None:
    check("(m) git 仓库夹具（本机没装 git：按 SKIP 记通过）", True, "SKIP: 找不到 git")
else:
    _synth = ("{\"key\": \"synthetic-b282\", \"entry_type\": \"article\", "
              "\"fields\": {\"title\": \"Synthetic\"}, \"verified\": true}\n")
    _env = os.environ.copy()
    _env.update({"GIT_AUTHOR_NAME": "B282 Test", "GIT_AUTHOR_EMAIL": "b282@example.invalid",
                 "GIT_COMMITTER_NAME": "B282 Test",
                 "GIT_COMMITTER_EMAIL": "b282@example.invalid"})

    def _git(root, *args):
        return subprocess.run([_git_bin, "-C", root, *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env=_env, shell=False)

    _root = tempfile.mkdtemp(prefix="bridge_b282_repo_")
    try:
        os.makedirs(os.path.join(_root, "bib_db"))
        _db = os.path.join(_root, "bib_db", "bibliography.jsonl")
        with open(_db, "w", encoding="utf-8") as _fh:
            _fh.write(_synth)
        _other = os.path.join(_root, "notes.txt")
        with open(_other, "w", encoding="utf-8") as _fh:
            _fh.write("synthetic\n")
        _git(_root, "init", "-q")
        _git(_root, "config", "user.name", "B282 Test")
        _git(_root, "config", "user.email", "b282@example.invalid")
        _git(_root, "add", "-A")
        _git(_root, "commit", "-qm", "fixture")
        _loc = GitLayer.locate_repo(_db)
        check("(m) locate_repo：库在仓库里的 bib_db/bibliography.jsonl 时通过",
              _loc.get("ok") is True
              and os.path.normcase(os.path.realpath(_loc.get("repo_root") or ""))
              == os.path.normcase(os.path.realpath(_root))
              and _loc.get("db_rel") == GitLayer.DB_RELATIVE_PATH, str(_loc.get("error")))
        _wrong_dir = os.path.join(_root, "data")
        os.makedirs(_wrong_dir)
        _wrong = os.path.join(_wrong_dir, "bibliography.jsonl")
        with open(_wrong, "w", encoding="utf-8") as _fh:
            _fh.write(_synth)
        _bad = GitLayer.locate_repo(_wrong)
        check("(m) locate_repo 拒绝「库不在 bib_db/bibliography.jsonl」（防误提交别的文件）",
              _bad.get("ok") is False and "bib_db/bibliography.jsonl" in _bad.get("error", ""),
              _bad.get("error", ""))
        _outside = tempfile.mkdtemp(prefix="bridge_b282_outside_")
        try:
            _out_db = os.path.join(_outside, "bibliography.jsonl")
            with open(_out_db, "w", encoding="utf-8") as _fh:
                _fh.write(_synth)
            _bad2 = GitLayer.locate_repo(_out_db)
            check("(m) locate_repo 拒绝不在 git 仓库里的库（soft：写库仍可用）",
                  _bad2.get("ok") is False and "git" in _bad2.get("error", ""))
        finally:
            shutil.rmtree(_outside, ignore_errors=True)

        _pre = GitLayer.preflight(_db)
        check("(m) preflight 只读：分支 / 身份 / 库干净 / 无 upstream（behind=None）",
              _pre.get("ok") is True and _pre.get("branch") in ("master", "main")
              and _pre.get("identity_ok") is True and not _pre.get("dirty_db")
              and _pre.get("upstream") == "" and _pre.get("behind") is None, str(_pre))
        with open(_other, "a", encoding="utf-8") as _fh:
            _fh.write("dirty\n")
        _pre2 = GitLayer.preflight(_db)
        check("(m) preflight 区分库文件与其它脏文件（其它改动不会提交）",
              _pre2.get("dirty_db") is False and _pre2.get("dirty_other") >= 1
              and "notes.txt" in (_pre2.get("dirty_paths") or []), str(_pre2.get("dirty_paths")))
        _head_before = _git(_root, "rev-parse", "HEAD").stdout.strip()
        with open(_db, "a", encoding="utf-8") as _fh:
            _fh.write(_synth.replace("synthetic-b282", "synthetic-b282-2"))
        _msg = "引用库：测试裁决（记录 1 条；sha aaaaaaaaaaaa→bbbbbbbbbbbb）"
        _res = GitLayer.commit_db(_db, _msg)
        _head_after = _git(_root, "rev-parse", "HEAD").stdout.strip()
        _subject = _git(_root, "log", "-1", "--pretty=%s").stdout.strip()
        _names = [line for line in _git(_root, "show", "--pretty=", "--name-only",
                                        "HEAD").stdout.splitlines() if line.strip()]
        check("(m) commit_db：真生成一次提交、message 精确、只含库文件一个路径",
              _res.get("ok") is True and _res.get("committed") is True
              and _res.get("sha_after") == _head_after and _head_after != _head_before
              and _subject == _msg and _names == [GitLayer.DB_RELATIVE_PATH],
              f"{_res} / {_names}")
        check("(m) commit_db：库文件无改动时不空提交",
              (lambda r: r.get("ok") is True and r.get("committed") is False
               and _git(_root, "rev-parse", "HEAD").stdout.strip() == _head_after)(
                  GitLayer.commit_db(_db, _msg)))
        check("(m) commit_db：其它脏文件仍留在工作树（确实没被 add 进来）",
              "notes.txt" in _git(_root, "status", "--porcelain").stdout)

        # S4：commit_db 不信调用方缓存的 git 状态，自己重跑探针。构造一次真实
        # merge 冲突（同一行两边改成不同内容），此时即使库文件有改动也绝不提交。
        _git(_root, "add", "-A")
        _git(_root, "commit", "-qm", "clean before merge")
        _base = _pre.get("branch") or "master"
        _git(_root, "checkout", "-q", "-b", "b282-side")
        with open(_other, "w", encoding="utf-8") as _fh:
            _fh.write("side\n")
        _git(_root, "add", "-A")
        _git(_root, "commit", "-qm", "side edit")
        _git(_root, "checkout", "-q", _base)
        with open(_other, "w", encoding="utf-8") as _fh:
            _fh.write("main\n")
        _git(_root, "add", "-A")
        _git(_root, "commit", "-qm", "main edit")
        _git(_root, "merge", "b282-side")       # 预期冲突，MERGE_HEAD 留下
        _head_in_merge = _git(_root, "rev-parse", "HEAD").stdout.strip()
        with open(_db, "a", encoding="utf-8") as _fh:
            _fh.write(_synth.replace("synthetic-b282", "synthetic-b282-merge"))
        _merge_res = GitLayer.commit_db(_db, "引用库：merge 中禁止提交")
        check("(m) S4：commit_db 独立探针——merge/冲突未完成时 soft 失败、不提交",
              _merge_res.get("ok") is False and _merge_res.get("committed") is False
              and _merge_res.get("soft") is True
              and "merge" in str(_merge_res.get("error"))
              and _git(_root, "rev-parse", "HEAD").stdout.strip() == _head_in_merge,
              str(_merge_res))
        _git(_root, "merge", "--abort")
    finally:
        shutil.rmtree(_root, ignore_errors=True)

    _root2 = tempfile.mkdtemp(prefix="bridge_b282_identity_")
    _empty_home = tempfile.mkdtemp(prefix="bridge_b282_home_")
    _saved_env = dict(os.environ)
    try:
        os.makedirs(os.path.join(_root2, "bib_db"))
        _db2 = os.path.join(_root2, "bib_db", "bibliography.jsonl")
        with open(_db2, "w", encoding="utf-8") as _fh:
            _fh.write(_synth)
        _git2_env = os.environ.copy()
        subprocess.run([_git_bin, "-C", _root2, "init", "-q"], capture_output=True,
                       text=True, env=_git2_env, shell=False)
        subprocess.run([_git_bin, "-C", _root2, "add", "-A"], capture_output=True,
                       text=True, env=_git2_env, shell=False)
        for _name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                      "GIT_COMMITTER_EMAIL", "EMAIL"):
            os.environ.pop(_name, None)
        os.environ["HOME"] = _empty_home
        os.environ["XDG_CONFIG_HOME"] = _empty_home
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        # 多数 git 会在 user.name/email 缺失时按「系统用户名@主机名」自动编一个；
        # 用 user.useConfigOnly 强制它报错，才测得到「身份缺失 = soft 失败」。
        os.environ["GIT_CONFIG_COUNT"] = "1"
        os.environ["GIT_CONFIG_KEY_0"] = "user.useConfigOnly"
        os.environ["GIT_CONFIG_VALUE_0"] = "true"
        _ident = GitLayer.commit_db(_db2, "引用库：身份缺失测试")
        check("(m) 身份缺失 = soft 失败（不抛异常；写库成功与否不受版本层影响）",
              _ident.get("ok") is False and _ident.get("soft") is True
              and "身份未配置" in str(_ident.get("error")), str(_ident))
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)
        shutil.rmtree(_root2, ignore_errors=True)
        shutil.rmtree(_empty_home, ignore_errors=True)

_c_m = E.bibdb_conflicts_call(pending_only=True, db="D")
check("(m) conflicts argv：`conflicts --pending-only --json --db D`",
      _c_m.argv[-5:] == ["conflicts", "--pending-only", "--json", "--db", "D"]
      and _c_m.entry == "bibdb.py")
_r_m = E.bibdb_resolve_call(key="K", action="adopt", incoming_sha256="SHA",
                            expect_db_sha256="DB", db="D")
check("(m) resolve argv：key/action/incoming/expect/json/db 顺序（带指纹）",
      _r_m.argv[-9:] == ["--action", "adopt", "--incoming-sha256", "SHA",
                         "--expect-db-sha256", "DB", "--json", "--db", "D"], _r_m.display)
_r2_m = E.bibdb_resolve_call(key="K", action="defer", all_fields=True, db="D")
check("(m) resolve argv：无指纹时 keep/defer 传 `--all-fields`，不带 incoming",
      _r2_m.argv[-4:] == ["--all-fields", "--json", "--db", "D"]
      and "--incoming-sha256" not in _r2_m.argv and "--dry-run" not in _r2_m.argv)
check("(m) resolve 反例：动作未知 / key 为空 → ValueError（不起子进程）",
      _raises_value_error(E.bibdb_resolve_call, key="K", action="bogus")
      and _raises_value_error(E.bibdb_resolve_call, key="  ", action="keep"))
_rep_m = E.bibdb_replace_call(key="K", bib="/tmp/x.bib", incoming_sha256="SHA",
                              dry_run=True, db="D")
check("(m) replace argv：action replace + --bib + 指纹 + dry-run 末尾",
      _rep_m.argv[-10:] == ["--action", "replace", "--incoming-sha256", "SHA",
                            "--bib", "/tmp/x.bib", "--json", "--db", "D", "--dry-run"],
      _rep_m.display)
check("(m) replace 反例：缺 bib / 缺指纹 → ValueError",
      _raises_value_error(E.bibdb_replace_call, key="K", bib="", incoming_sha256="SHA")
      and _raises_value_error(E.bibdb_replace_call, key="K", bib="/tmp/x.bib",
                              incoming_sha256=""))
_adopt_m = E.bibdb_adopt_all_call(expect_db_sha256="DB", dry_run=True, db="D")
check("(m) adopt-all argv：`resolve --adopt-all-pending --expect-db-sha256 … --json "
      "--db D --dry-run`",
      _adopt_m.argv[-7:] == ["--adopt-all-pending", "--expect-db-sha256", "DB",
                             "--json", "--db", "D", "--dry-run"], _adopt_m.display)
_ui_src_m = (SHELL / "bibdb_ui.py").read_text(encoding="utf-8")
check("(m) 待裁决页仍走 engine 包装 + 版本层（不自己拼 git 命令 / 不复制引擎判据）",
      all(s in _ui_src_m for s in ("E.bibdb_conflicts_call(", "E.bibdb_resolve_call(",
                                   "E.bibdb_replace_call(", "E.bibdb_adopt_all_call(",
                                   "G.commit_db(", "G.commit_message("))
      and "subprocess" not in _ui_src_m.split("import bibdb_git as G", 1)[1].split(
          "import engine as E", 1)[0])
check("(m) 写成功才提交：UI 只在 `written and not already` 分支进 `_start_commit`",
      "written and not already" in _ui_src_m
      and "_start_commit(meta, payload)" in _ui_src_m
      and "versionNotice" not in _ui_src_m)

# ── P1 复核（B-282 修复轮）：S1 verified 展示 / S4 写前新鲜预检 / S7 确认明细 ──
_ui_tree_m = ast.parse(_ui_src_m)


def _ui_fn_m(name: str) -> str:
    """只取 `BibDbReviewPage` 里的方法（同名 `_show_detail` 在查库页也有一个）。"""
    for node in ast.walk(_ui_tree_m):
        if isinstance(node, ast.ClassDef) and node.name == "BibDbReviewPage":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    return ast.get_source_segment(_ui_src_m, child) or ""
    return ""


def _order_ok_m(src: str, first: str, second: str) -> bool:
    first_at, second_at = src.find(first), src.find(second)
    return first_at >= 0 and second_at >= 0 and first_at < second_at


_detail_src = _ui_fn_m("_show_detail")
check("(m) S1 详情面板：显示「已核实：是/否」+ 已核实红字风险（与 mac 对等）",
      '("已核实", "是" if verified else "否")' in _detail_src
      and "库内已核实记录" in _detail_src
      and 'self.detail.tag_add("risk"' in _detail_src,
      _detail_src[:100])
check("(m) S1 红字标签在 build 里注册；汇总行显示「已核实 N 条」（有则红字）",
      'self.detail.tag_configure("risk", foreground=theme.COLOR["error"])' in _ui_src_m
      and "已核实 {verified} 条" in _ui_fn_m("_on_loaded")
      and 'theme.COLOR["error"] if verified else theme.COLOR["fg"]'
      in _ui_fn_m("_on_loaded"))

_write_src = _ui_fn_m("_write")
check("(m) S4 逐条写库：真写前重跑 G.status（refresh 在 resolve 之前），并按新状态判 blocked/behind",
      _order_ok_m(_write_src, "_refresh_git_status()", "E.bibdb_resolve_call(")
      and "_git_blocked()" in _write_src and "_behind_ok()" in _write_src)
_adopt_write_src = _ui_fn_m("_adopt_all_write")
check("(m) S4 全部采用真写：确认后重跑 status，blocked/behind 在 adopt-all 真写调用之前",
      _order_ok_m(_adopt_write_src, "_refresh_git_status()",
                  "E.bibdb_adopt_all_call(")
      and "_git_blocked()" in _adopt_write_src
      and "_behind_ok()" in _adopt_write_src)
_replace_confirm_src = _ui_fn_m("_replace_confirm")
check("(m) S4 替换真写：重跑 status，blocked/behind 在 replace 真写调用之前",
      _order_ok_m(_replace_confirm_src, "_refresh_git_status()",
                  "E.bibdb_replace_call(")
      and "_git_blocked()" in _replace_confirm_src
      and "_behind_ok()" in _replace_confirm_src)
check("(m) S4 git 行：merge 与 conflict/rebase 一样红条",
      'git.get("merge")' in _ui_fn_m("_render_git")
      and "merge 进行中" in _ui_fn_m("_render_git"))

_adopt_preview_src = _ui_fn_m("_on_adopt_preview")
check("(m) S7 全部采用：确认框用 adopt_confirm_detail（计数+已核实+逐条 old → new），预览不写库",
      "adopt_confirm_detail(" in _adopt_preview_src
      and "mb.askyesno(" in _adopt_preview_src
      and "dry_run=False" not in _adopt_preview_src)

_cd_node = next((node for node in _ui_tree_m.body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "adopt_confirm_detail"), None)
_ns_m: dict = {}
if _cd_node is not None:
    exec(compile(ast.get_source_segment(_ui_src_m, _cd_node) or "",
                 "<adopt_confirm_detail>", "exec"), _ns_m)
_cd = _ns_m.get("adopt_confirm_detail")
check("(m) S7 确认文本是模块级纯函数（不依赖 Tk，可 headless 断言）", callable(_cd))

if callable(_cd):
    _ad = [
        {"record_key": "rk1", "bib_key": "Alpha2020",
         "fields": {"title": {"old": "Old T", "new": "New T"},
                    "note": {"old": "x", "new": "", "remove": True}}},
        {"record_key": "rk2", "bib_key": "Beta2019",
         "fields": {"year": {"old": "", "new": "2020"}}},
    ]
    _conf = _cd(adopted=_ad, records=2, skipped=3, pending_before=5,
                pending_after=3, verified_keys={"rk1"})
    check("(m) S7 确认文本：记录/字段计数 + 已核实风险 + 逐条 `key: field old → new`",
          "将采用 2 条冲突 / 2 条记录 / 3 个字段；跳过 3 条。" in _conf
          and "待裁决 5 → 3 条。" in _conf
          and "⚠ 其中已核实 1 条" in _conf
          and "Alpha2020: title Old T → New T" in _conf
          and "Alpha2020: note x → （删除）" in _conf
          and "Beta2019: year （空） → 2020" in _conf, _conf)
    _conf0 = _cd(adopted=_ad, records=2, skipped=0, pending_before=5,
                 pending_after=3, verified_keys=set())
    check("(m) S7 无已核实记录时明写「已核实 0 条」",
          "其中已核实 0 条（本次不改已核实记录）。" in _conf0, _conf0)
    _many = [{"record_key": f"rk{i}", "bib_key": f"K{i:02d}",
              "fields": {"title": {"old": f"v{i}", "new": f"w{i}"}}}
             for i in range(1, 13)]
    _conf12 = _cd(adopted=_many, records=12, skipped=0, pending_before=12,
                  pending_after=0, verified_keys=set())
    check("(m) S7 超过 10 条只列前 10 条 + 「…等 N 条」",
          "…等 12 条" in _conf12
          and "K01: title v1 → w1" in _conf12
          and "K10: title v10 → w10" in _conf12
          and "K11: title v11 → w11" not in _conf12
          and "K12: title" not in _conf12)

check("(m) P2 GIT_ASKPASS 用赋值强制覆盖继承值（不 setdefault）",
      'env["GIT_ASKPASS"] = "true"' in _git_src
      and 'setdefault("GIT_ASKPASS"' not in _git_src)
check("(m) P2 身份缺失话术不再引导 mac 的「提交…」按钮",
      "提交…" not in GitLayer.IDENTITY_ERROR
      and "身份未配置" in GitLayer.IDENTITY_ERROR)
check("(m) P2 dirty_other 两侧路径统一 `/` 后比较（Windows 不误算库文件）",
      'rel_norm = os.path.normcase(rel.replace(' in _git_src
      and 'path_norm = os.path.normcase(path.replace(' in _git_src)
check("(m) P2 commit 成功但 rev-parse 失败时回退取 commit 输出 sha",
      "sha 取自 git commit 输出" in _git_src and "re.search(" in _git_src)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (n) 失败诊断卡（B-285b）：红/中性口径 + 本地诊断包 + 日志轮转 + 旧引擎回退 ──")

check("(n) 入口表：dirpush.py 追加在 diagnose.py 之后，前 12 项顺序逐项不变",
      tuple(E.ENTRIES) == ("convert.py", "checkbib.py", "mergepdf.py",
                           "cleantemp.py", "compileall.py", "proofcheck.py",
                           "infocheck.py", "toc.py", "publish.py", "bibdb.py",
                           "envcheck.py", "diagnose.py", "dirpush.py")
      and tuple(_DISP.ENTRIES) == tuple(E.ENTRIES),
      f"{E.ENTRIES}")

check("(n) 五页共用一张卡：convert / proofcheck / infocheck / compileall / checkbib",
      tuple(E.DIAGNOSIS_ENTRIES) == ("convert.py", "proofcheck.py", "infocheck.py",
                                     "compileall.py", "checkbib.py"),
      f"{E.DIAGNOSIS_ENTRIES}")

_RED = (("convert.py", 2), ("infocheck.py", 2), ("compileall.py", 1))
_NEUTRAL = (("proofcheck.py", 1), ("checkbib.py", 1))
check("(n) 红卡口径：convert exit 2 / infocheck exit 2 / compileall exit 1 / 启动失败 <0（C1）",
      all(E.diagnosis_level(e, c) == "error" for e, c in _RED)
      and E.diagnosis_level("convert.py", -1) == "error"
      and E.diagnosis_level("infocheck.py", -1) == "error"
      and E.diagnosis_level("compileall.py", -1) == "error"
      and E.diagnosis_level("proofcheck.py", -1) == "error",
      str([(e, c, E.diagnosis_level(e, c)) for e, c in _RED]))
check("(n) 中性卡：proofcheck / checkbib exit 1；exit 0 一律不出卡（C2/C3）",
      all(E.diagnosis_level(e, c) == "neutral" for e, c in _NEUTRAL)
      and all(E.diagnosis_level(e, 0) is None for e in E.DIAGNOSIS_ENTRIES)
      and E.diagnosis_level("convert.py", 1) is None
      and E.diagnosis_level("infocheck.py", 3) is None
      and E.diagnosis_level("proofcheck.py", 2) is None
      and E.diagnosis_level("checkbib.py", 2) is None
      and E.diagnosis_level("toc.py", 2) is None,
      str([(e, c, E.diagnosis_level(e, c)) for e, c in
           (("proofcheck.py", 1), ("checkbib.py", 1), ("convert.py", 0),
            ("convert.py", 1), ("proofcheck.py", 2), ("checkbib.py", 2))]))

_dcall = E.diagnose_call("convert.py", 2, "/tmp/本次.log")
_dargs = _dcall.argv
check("(n) diagnose_call：diagnose.py + `--entry/--exit-code/--log/--json`（与契约一致）",
      _dcall.entry == "diagnose.py"
      and _dargs[-7:] == ["--entry", "convert.py", "--exit-code", "2",
                          "--log", "/tmp/本次.log", "--json"],
      f"argv={_dargs}")

_RAW = ("sk-live-abcdef123456 api_key=SECRETVALUE token: TOKENVALUE "
        "Authorization: Bearer abc.def-123 正常行 tokenization 不受影响")
_MASKED = E.mask_secrets(_RAW)
check("(n) 脱敏：sk- / api_key= / token: / Bearer 都被替换（C4 的输入侧）",
      "sk-live" not in _MASKED and "SECRETVALUE" not in _MASKED
      and "TOKENVALUE" not in _MASKED and "abc.def" not in _MASKED
      and "sk-live" not in E.mask_secrets(_MASKED),
      _MASKED)
check("(n) 脱敏反例：tokenization / risk-free 这类词**不能**误伤",
      "tokenization" in _MASKED
      and E.mask_secrets("risk-free tokenization") == "risk-free tokenization",
      E.mask_secrets("risk-free tokenization"))

_REPORT = {
    "schema": "diagnose.v1", "entry": "convert.py", "exit_code": 2,
    "category": "template_unavailable", "severity": "error",
    "title": "缺少排版模板", "reason": "找不到模板文件",
    "next_steps": ["把模板目录放回编辑笔记/模板文件", "重跑转换"],
    "doc_anchor": "ENGINE_API.md §十一 失败诊断（diagnose.v1）",
    "confidence": 0.86, "engine_version": "9.9.9",
}
_PACK = E.diagnosis_pack(
    entry="convert.py", exit_code=2, level="error", report=_REPORT,
    log_tail=["最后一行：找不到模板", "sk-live-abcdef123456", "api_key=SECRETVALUE"],
    engine_version="9.9.9", generated_at="2026-10-31 12:00:00")
check("(n) 诊断包含类别/标题/原因/下一步/日志尾部，且 ≤16KB（C4/C5）",
      all(k in _PACK for k in ("template_unavailable", "缺少排版模板", "找不到模板文件",
                               "把模板目录放回", "最后一行：找不到模板", "退出码：2"))
      and len(_PACK.encode("utf-8")) <= 16 * 1024,
      f"{len(_PACK.encode('utf-8'))} bytes")
check("(n) 诊断包不含密钥（C4）、写明零上传、含说明",
      "sk-live" not in _PACK and "SECRETVALUE" not in _PACK
      and "不会上传" in _PACK and "不含稿件正文" in _PACK)
_FALLBACK = E.diagnosis_pack(entry="convert.py", exit_code=2, level="error",
                             report=None, log_tail=["最后一行报错"], engine_version="1.0")
check("(n) 旧引擎回退：diagnose.py 缺失/崩溃时仍出卡（退出码 + 日志尾部，C7）",
      "诊断器不可用" in _FALLBACK and "退出码：2" in _FALLBACK
      and "最后一行报错" in _FALLBACK and "下一步" in _FALLBACK)
check("(n) 回退解析：非 JSON / 非 diagnose.v1 / 缺键 → None（绝不猜）",
      E.parse_diagnosis("不是 JSON") is None
      and E.parse_diagnosis('{"schema": "envcheck.v1"}') is None
      and E.parse_diagnosis('{"schema": "diagnose.v1", "entry": "x.py"}') is None
      and E.parse_diagnosis(json.dumps({"schema": "diagnose.v1", "entry": "x.py",
                                        "category": "usage", "title": "t",
                                        "reason": "r", "next_steps": []}))["category"] == "usage")

# 日志轮转 + 落盘脱敏（D-05：留 20 份；原文行脱敏）
_dir = tempfile.mkdtemp(prefix="slcn_diaglog_")
try:
    for _i in range(25):
        E.save_diagnosis_log("convert.py", [f"行{_i}", "sk-live-abcdef123456"],
                             directory=_dir, stamp=f"20261031-{_i:06d}")
    _names = sorted(n for n in os.listdir(_dir) if n.endswith(".log"))
    _newest = open(os.path.join(_dir, _names[-1]), encoding="utf-8").read()
    _oldest = open(os.path.join(_dir, _names[0]), encoding="utf-8").read()
    check("(n) 日志留 20 份：连写 25 次只留最新 20（D-05）",
          len(_names) == 20 and _names[0] == "20261031-000005-convert.log"
          and _names[-1] == "20261031-000024-convert.log",
          f"{len(_names)} 份：{_names[:1]}…{_names[-1:]}")
    check("(n) 落盘日志已脱敏、仍是原始行数（不是丢弃证据）",
          "行24" in _newest and "sk-live" not in _newest
          and "行5" in _oldest and _newest.count("\n") == 2,
          _newest)
finally:
    shutil.rmtree(_dir, ignore_errors=True)

_BASE_SRC = (SHELL / "tabs" / "base.py").read_text(encoding="utf-8")
_MAIN_SRC = (SHELL / "main.py").read_text(encoding="utf-8")
check("(n) 五页共用诊断卡基座：DiagnosisCard + show_diagnosis + 三个按钮 + 证据折叠",
      all(s in _BASE_SRC for s in (
          "class DiagnosisCard", "def show_diagnosis", "def toggle_evidence",
          "打开日志", "复制诊断包", "查看文档", "grid_remove()")))
check("(n) main：worker 结果之后调 _post_diagnosis；__DIAG__ 只在主线程碰控件",
      "self._post_diagnosis(tab, call.entry, code, run_lines)" in _MAIN_SRC
      and 'self._q.put(("__DIAG__"' in _MAIN_SRC
      and 'line[0] == "__DIAG__"' in _MAIN_SRC
      and "tab.show_diagnosis(" in _MAIN_SRC)
check("(n) 每次运行先隐藏旧卡（上一次的失败不会残留在新结果旁边）",
      "tab.hide_diagnosis()\n        tab.show_running(0.0)" in _MAIN_SRC)
check("(n) 诊断自身失败不阻断：异常 → report=None 仍出回退卡，且不抛给 worker",
      "except Exception:  # noqa: BLE001  旧引擎/诊断崩溃 → 回退" in _MAIN_SRC
      and 'self._q.put(f"⚠️ 诊断包生成失败（不影响结果）：{exc}")' in _MAIN_SRC)
check("(n) 旧引擎回退走「退出码 + 日志尾部」文案（不阻断运行结果）",
      "已回退为「退出码 + 日志尾部」" in (SHELL / "engine.py").read_text(encoding="utf-8"))
check("(n) 「查看文档」的候选真实存在（契约/使用说明/FAQ 至少一份）",
      bool(E.diagnosis_doc_path()) and os.path.isfile(E.diagnosis_doc_path()),
      str(E.diagnosis_doc_path()))


# ─────────────────────────────────────────────────────────────────────────
# (o) 目录摘要推送（B-289）：Windows 页 ↔ `Engine/dirpush.py` 契约
#
# 分工与 mac `DirPushFeature` 一致：参数拼装 / JSON 解析 / 结果摘要都是**纯函数**，
# 界面只做「取值 → 调 engine → 显示」。这里不开窗，钉住四件事：
#   ① argv 与 `Engine/dirpush.py`/ENGINE_API.md 的契约逐项对齐（年度不传 --with-en-toc）；
#   ② 只读引擎 `--json` 的冻结字段（期头/篇数/摘要取到率/输出路径），界面不自算；
#   ③ 输出根默认值与记忆键（settings options.dirpushOutRoot）不漂移；
#   ④ 静态守卫：页面不联网、不写期目录、四个按钮与代码仓二次确认都在。
print("\n── (o) 目录摘要推送（B-289）：页 / 入口 / argv 契约 ──")
_DP_SRC = (SHELL / "tabs" / "dirpush.py").read_text(encoding="utf-8")
check("(o) 页面文件在、继承 TabPage、标题「目录摘要推送」、路径选择器 pick=\"dir\"",
      "class DirPushTab(TabPage)" in _DP_SRC
      and 'name = "目录摘要推送"' in _DP_SRC and 'pick = "dir"' in _DP_SRC)
check("(o) main.py：import + 实例化（cfg 共用设置页）+ self.tabs 第 9 项 + nav_groups 含索引 8"
      "（B-286：引用库组 = 9,10,11,12,1,13）",
      "dirpush as _dp" in _MAIN_SRC
      and "_dp.DirPushTab(" in _MAIN_SRC and "目录摘要推送" in _MAIN_SRC
      and "self.dirpush_tab," in _MAIN_SRC
      and '("整期装配", (4, 5, 6, 7, 2, 8))' in _MAIN_SRC
      and '("引用库", (9, 10, 11, 12, 1, 13))' in _MAIN_SRC)
check("(o) main.py：_call_for 走 E.dirpush_call（页面不自己拼 argv）、_set_busy 含新页",
      "E.dirpush_call(issue=path" in _MAIN_SRC
      and "self.compile_tab, self.clean_tab, self.dirpush_tab)" in _MAIN_SRC)
check("(o) 页面静态守卫：不联网、不写期目录、四个按钮 + 代码仓二次确认 + 英文目录仅当期",
      not re.search(r"^\s*(?:import|from)\s+(?:urllib|requests|http|socket)", _DP_SRC, re.M)
      and "预览（不写盘）" in _DP_SRC
      and "打开文件" in _DP_SRC and "打开输出目录" in _DP_SRC
      and "confirm_repo_root" in _DP_SRC and "REPO_MARKERS" in _DP_SRC
      and "with_en_toc" in _DP_SRC)
check("(o) settings：记忆键 options.dirpushOutRoot 默认空串（老配置缺键不炸）",
      S.DEFAULTS["options"].get("dirpushOutRoot") == ""
      and S._merge_defaults({})["options"].get("dirpushOutRoot") == ""
      and S._merge_defaults({"options": {"dirpushOutRoot": "D:/x"}})[
          "options"]["dirpushOutRoot"] == "D:/x")
check("(o) engine：_RESULT_1 有 dirpush.py 的退出码 1 文案（traceback 也给人看）",
      "dirpush.py" in E._RESULT_1 and "traceback" in E._RESULT_1["dirpush.py"])

_DP, _DP_ERR = None, ""
if _SKIP_GUI:
    _DP_ERR = "SLCN_BRIDGE_SKIP_GUI=1"
else:
    try:
        import tabs.dirpush as _DP  # noqa: E402  需要 tkinter；缺 → 下面几条 SKIP
    except Exception as _exc:  # noqa: BLE001
        _DP_ERR = repr(_exc)

if _DP is None:
    if _SKIP_GUI:
        print("SKIP  (o) dirpush 纯函数断言（SLCN_BRIDGE_SKIP_GUI=1；页面静态守卫仍跑）")
    else:
        print(f"SKIP  (o) dirpush 纯函数断言（import 失败：{_DP_ERR}）")
else:
    _c = E.dirpush_call(issue=r"E:\期\2026年第5期(90)", out_root=r"D:\push")
    check("(o) 当期 argv：--issue/--out/--json，默认**不**带 --with-en-toc",
          _c.entry == "dirpush.py" and _c.argv[2:] == [
              "--issue", r"E:\期\2026年第5期(90)", "--out", r"D:\push", "--json"],
          str(_c.argv))
    check("(o) 英文目录：`--with-en-toc` 插在 `--json` 之前（当期专属）",
          E.dirpush_call(issue="/x", with_en_toc=True).argv[-2:]
          == ["--with-en-toc", "--json"])
    check("(o) 预览：`--json` 之后追加 `--dry-run`（不写盘），其余参数逐项不变",
          E.dirpush_call(issue="/x", out_root="/o", dry_run=True).argv[-2:]
          == ["--json", "--dry-run"]
          and E.dirpush_call(issue="/x", out_root="/o").argv[-3:]
          == ["--out", "/o", "--json"])
    _ann = E.dirpush_call(issue="/x", out_root="/o", annual=True, year="2026",
                          include_issues="3, 1，3,0")
    check("(o) 年度 argv：--annual + --year + --include-issues（升序去重、全角逗号也认），"
          "且**绝不**传 --with-en-toc",
          _ann.argv[2:] == ["--issue", "/x", "--out", "/o", "--annual", "--year", "2026",
                            "--include-issues", "1,3", "--json"]
          and "--with-en-toc" not in _ann.argv, str(_ann.argv))
    check("(o) 年度选项缺省：年份/纳入期留空 → 对应开关不出现（交给引擎全扫描）",
          E.dirpush_call(issue="/x", annual=True).argv[2:]
          == ["--issue", "/x", "--annual", "--json"])
    check("(o) 年度标签写明「年度总目录」（日志里分得清跑的是哪个模式）",
          "年度总目录" in _ann.label and _c.label == "目录摘要推送")
    check("(o) 纳入期解析：空/0/负数/重复都清掉，只留正整数升序（与 mac 同口径）",
          E.dirpush_issues_csv("") == "" and E.dirpush_issues_csv("0, -1") == ""
          and E.dirpush_issues_csv("5,2，2,10") == "2,5,10"
          and E.dirpush_issues_csv(" 89 ") == "89")
    check("(o) 默认输出根 = ~/Documents/sysu/编辑部/目录摘要推送（与引擎 collect.py 同一个）",
          _DP.default_out_root().replace("\\", "/").endswith(
              "Documents/sysu/编辑部/目录摘要推送"))
    check("(o) resolve_out_root：配置空/缺键 → 默认根；有值 → 用户值（只读，不写盘）",
          _DP.resolve_out_root({}) == _DP.default_out_root()
          and _DP.resolve_out_root({"options": {"dirpushOutRoot": "  "}})
          == _DP.default_out_root()
          and _DP.resolve_out_root({"options": {"dirpushOutRoot": "D:/push"}}) == "D:/push")
    check("(o) 代码仓内输出根要二次确认：三个仓名都认，普通目录不打扰",
          all(_DP.repo_warning(os.path.join("C:/Users/x/Documents/GitHub", m, "push"))
              for m in _DP.REPO_MARKERS)
          and _DP.repo_warning(os.path.join(tempfile.gettempdir(), "push")) == "")
    check("(o) 摘要取到率：ok/articles；0 篇 → 0/0（不除零）",
          _DP.abstracts_rate({"counts": {"abstracts_ok": 4, "articles": 5}}) == "4/5"
          and _DP.abstracts_rate({"counts": {"articles": 0}}) == "0/0"
          and _DP.abstracts_rate(None) == "0/0")
    _payload = {"schema": 1, "ok": True, "mode": "issue", "dry_run": True,
                "outputs": ["/o/期/逻辑学研究2026年第5期目录摘要.docx"],
                "issue": {"year": 2026, "number": 5, "vol": 8, "total": 90,
                          "header_lines": ["二〇二六年第五期"], "with_en_toc": True},
                "counts": {"zh_entries": 30, "en_entries": 12, "articles": 5,
                           "abstracts_ok": 3, "abstracts_missing": 2,
                           "column_entries": 3},
                "warnings": ["第 3 期目录缺失"], "missing": [], "annual": None}
    _noisy = ["期次：2026 年第 5 期", json.dumps(_payload, ensure_ascii=False, indent=2),
              "错误：这个是 stderr 混进来的行"]
    check("(o) parse_result：stderr 混入 + 多行缩进 JSON 也能抠出对象；垃圾 → None",
          _DP.parse_result(_noisy) == _payload
          and _DP.parse_result(["没有 JSON"]) is None
          and _DP.parse_result([]) is None)
    check("(o) error_message：优先含错误关键词的最后一行，否则最后一行，再否则退出码",
          _DP.error_message(["第一行", "错误：找不到期目录", "结束"], 2) == "错误：找不到期目录"
          and _DP.error_message(["第一行", "最后一行"], 2) == "最后一行"
          and _DP.error_message(["", "  "], 7) == "引擎退出码 7")
    _summary = _DP.summary_text(_payload, 0, dry_run=True)
    check("(o) 结果摘要：期头/体例/篇数/摘要取到率/warnings/计划输出都来自引擎 JSON",
          all(s in _summary for s in ("预览（dry-run：未写任何文件）", "第 5 期", "第 8 卷",
                                      "总第 90 期", "中英文对照", "中文 30 / 英文 12",
                                      "摘要取到 3/5", "第 3 期目录缺失",
                                      "计划输出：")))
    check("(o) 失败摘要：ok:false → 红字头；没有 JSON → 明说没返回（配上尾部两行）",
          "❌ 引擎报告失败（退出码 2）" in _DP.summary_text(
              {"ok": False, "outputs": []}, 2)
          and "❌ 引擎没有返回结果 JSON" in _DP.summary_text(None, 2, tail="最后一行")
          and "最后一行" in _DP.summary_text(None, 2, tail="最后一行"))
    check("(o) 年度摘要：纳入期数/缺期只警告（不挡生成）",
          "年度：2026 年，纳入 2 期（4,5）" in _DP.summary_text(
              {"ok": True, "annual": {"year": 2026, "included": [4, 5],
                                      "missing_issues": [3], "issues": []},
               "counts": {}}, 0)
          and "只警告" in _DP.summary_text(
              {"ok": True, "annual": {"year": 2026, "included": [4, 5],
                                      "missing_issues": [3], "issues": []},
               "counts": {}}, 0))

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
