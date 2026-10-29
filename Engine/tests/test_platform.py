#!/usr/bin/env python3
"""平台中立化（第 86 条）：引擎要在 macOS 与 Windows 上跑同一份逻辑。

这一套钉住七件事，全部是**真跑/真扫**，不是「代码看起来对」：

  (a) `utf8_stdio()` —— Windows 的 stdout 默认是控制台代码页（cp936），中文日志经管道
      会乱码甚至 `UnicodeEncodeError` **崩在转换中途**。测试用 `PYTHONIOENCODING=ascii`
      在子进程里**复现**这个失败模式，再验证修复；
  (b) `file_uri()` —— LibreOffice 的 `-env:UserInstallation=` 只认 URI，
      Windows 盘符与**带空格的路径**都必须正确转义；
  (c) `find_soffice()` —— 三个平台的常见安装位置都要在候选表里（Windows 安装器**不**写 PATH）；
  (d) **静态守卫**：交付模块里不许出现「文本模式 `open()` 却不写 `encoding=`」
      —— 那在 Windows 上会按本地编码读中文源稿，直接读成乱码；
  (e) 版本号只有一处（`slcn/__init__.py`），双平台不许漂移；
  (f) **跨盘符路径**：`paths.rel()` 不许让一句「汇报」把整个转换打断 —— Windows 上
      `os.path.relpath('C:\\…', 'D:\\…')` 会抛 `ValueError: path is on mount 'C:', …`，
      而调用它的地方全是打印产物路径（案例第 103 条：release 链路 15 次全红的真因）；
  (g) **打包 dispatcher 的入口表**：`engine_main.ENTRIES` 必须覆盖全部交付 CLI ——
      漏一个，那个入口在 Windows 包里就是「❌ 不认识的入口」退出 2（v1.0.1 实测坏了 5 页）。
"""
from __future__ import annotations

import ast
import importlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import __version__, platform as P  # noqa: E402
from slcn import office  # noqa: E402

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


PY = sys.executable


def child(code: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    return subprocess.run([PY, "-c", code], capture_output=True, env=env, cwd=str(ENGINE))


# ─────────────────────────────────────────────────────────────────────────
print("── (a) utf8_stdio：Windows 那个「中文日志崩中途」的失败模式 ──")

# 反例（复现缺陷本身）：把子进程的输出编码设成 ascii（模拟窄代码页），
# 不打补丁直接 print 中文 → 必须失败。这条证明下面的正例**有牙齿**。
BAD = "print('中文日志一行')"
r = child(BAD, {"PYTHONIOENCODING": "ascii"})
check("(a) 反例：不做任何处理、输出编码是 ascii 时，打印中文**确实会失败**",
      r.returncode != 0 and b"UnicodeEncodeError" in r.stderr,
      f"rc={r.returncode}")

GOOD = ("import sys; sys.path.insert(0, '.'); "
        "from slcn import platform as P; P.utf8_stdio(); print('中文日志一行')")
r = child(GOOD, {"PYTHONIOENCODING": "ascii"})
check("(a) 正例：先调 utf8_stdio() → 同样的环境里打印中文**成功**",
      r.returncode == 0, f"rc={r.returncode} {r.stderr[-120:]!r}")
check("(a) 而且输出是**真 UTF-8**（不是被替换成 ? 的残骸）",
      "中文日志一行".encode("utf-8") in r.stdout, r.stdout[:60])

r = child("import sys; sys.path.insert(0, '.'); "
          "from slcn import platform as P; P.utf8_stdio(); P.utf8_stdio(); print('二次调用 OK')",
          {"PYTHONIOENCODING": "ascii"})
check("(a) 幂等：连调两次不炸", r.returncode == 0 and "二次调用 OK".encode() in r.stdout)

r = child("import sys, io; sys.path.insert(0, '.'); from slcn import platform as P; "
          "sys.stdout = io.StringIO(); P.utf8_stdio(); print('没有 reconfigure 也不许抛')")
check("(a) 稳健：stdout 被换成没有 reconfigure 的对象时**不抛异常**",
      r.returncode == 0, f"rc={r.returncode} {r.stderr[-100:]!r}")

# 端到端：**打包入口** `engine_main.py` 也必须扛得住窄编码控制台。
# 它曾经漏了 utf8_stdio() —— `SLCNEngine.exe --version` 在 cp1252 上直接
# `UnicodeEncodeError: 'charmap' codec can't encode characters in position 5-6`
# 崩掉（2026-10-16 windows-latest 打包自检实测；本机用 PYTHONIOENCODING=ascii
# 复现出**同一处**（engine_main.py 的 51 行）、同一个 "position 5-6"）。
# 为什么单测它不可省：这个入口在 mac 上平时不会被跑到，而它正是用户双击的那个 exe。
r = child("import subprocess, sys;"
          "p = subprocess.run([sys.executable, 'engine_main.py', '--version'], capture_output=True);"
          "print('rc', p.returncode, '| has_unicode_error', b'UnicodeEncodeError' in p.stderr,"
          "      '| out', len(p.stdout))",
          {"PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"})
check("(a) `engine_main.py --version` 在窄编码控制台下不崩、且版本号打得出来",
      r.returncode == 0 and b"rc 0" in r.stdout and b"has_unicode_error False" in r.stdout
      and b"out 0" not in r.stdout, r.stdout.decode(errors="replace")[:90])

# 静态守卫：**每一个**交付入口都要调 utf8_stdio()，别只修被抓到的那一个。
ENTRY_FILES = ["convert.py", "checkbib.py", "mergepdf.py", "proofcheck.py", "compileall.py",
               "publish.py", "cleantemp.py", "infocheck.py", "toc.py",
               # 第 132 条：`bibdb.py` 两个平台**都**交付（mac 包 + Windows 分发器）。
               # 从前它被当成「mac 专属」，本测试还专门守卫「不进 Windows ENTRIES」——
               # Windows 版引用库做完后那条守卫必须反过来，否则一改 ENTRIES 就红。
               "bibdb.py",
               "engine_main.py"]
def _calls_utf8_stdio(tree: ast.AST) -> bool:
    """入口是否调了 `utf8_stdio()` —— 两种写法都认：

    · `_plat.utf8_stdio()`（模块方式 import，convert.py 等入口的写法）；
    · `utf8_stdio()`（`from slcn.platform import utf8_stdio`，bibdb.py 的写法）。
    只认前者会把「用 from-import 的正确实现」误判成没调（第 132 条把 bibdb.py 纳入
    交付入口时实测：它一直是调了的，但 AST 里是 Name 而不是 Attribute）。
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "attr", None) == "utf8_stdio":
            return True
        if isinstance(func, ast.Name) and func.id == "utf8_stdio":
            return True
    return False


_missing = []
for _f in ENTRY_FILES:
    _ok = _calls_utf8_stdio(ast.parse((ENGINE / _f).read_text(encoding="utf-8")))
    if not _ok:
        _missing.append(_f)
check(f"(a) {len(ENTRY_FILES)} 个交付入口**全部**调了 utf8_stdio()（漏一个就是一处崩溃）",
      not _missing, "; ".join(_missing))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) file_uri：LibreOffice 的 -env:UserInstallation 只认 URI ──")
d = tempfile.mkdtemp(prefix="slcn uri ")
uri = P.file_uri(d)
check("(b) 是 file:// 开头的 URI", uri.startswith("file:///"), uri)
check("(b) **路径里的空格被转义**（临时目录、稿件目录里空格很常见）",
      "%20" in uri and " " not in uri, uri)
check("(b) 没有反斜杠（Windows 盘符路径不能直接拼）", "\\" not in uri, uri)
check("(b) 与 pathlib 的口径一致（两个平台都对）",
      uri == pathlib.Path(d).resolve().as_uri(), uri)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) find_soffice：三平台候选 + 环境变量出口 ──")
cands = P.SOFFICE_CANDIDATES
check("(c) 候选表里有 **Windows** 的位置（.exe，安装器默认不写 PATH）",
      any(c.lower().endswith("soffice.exe") for c in cands), f"{cands}")
check("(c) 候选表里有 **macOS** 的 .app 位置", any(".app/Contents/MacOS" in c for c in cands))
check("(c) 候选表里有 **Linux** 的位置", any(c.startswith("/usr/") and c.endswith("soffice") for c in cands))

with tempfile.NamedTemporaryFile(prefix="fake_soffice_", delete=False) as fh:
    fake = fh.name
old_env = os.environ.get("SLCN_SOFFICE")
try:
    os.environ["SLCN_SOFFICE"] = fake
    check("(c) `SLCN_SOFFICE` 指到存在的文件 → 就用它（给装在奇怪位置的人一个出口）",
          P.find_soffice() == fake, f"{P.find_soffice()}")
    os.environ["SLCN_SOFFICE"] = fake + ".不存在的"
    check("(c) `SLCN_SOFFICE` 指的路径不存在 → **忽略它**，继续走 PATH/候选",
          P.find_soffice() != fake + ".不存在的", f"{P.find_soffice()}")
finally:
    if old_env is None:
        os.environ.pop("SLCN_SOFFICE", None)
    else:
        os.environ["SLCN_SOFFICE"] = old_env
    os.unlink(fake)

# 全都没有时返回 None（不许瞎猜一个路径出来）
import slcn.platform as _pmod  # noqa: E402
_saved_which, _saved_cands = _pmod.shutil.which, _pmod.SOFFICE_CANDIDATES
try:
    _pmod.shutil.which = lambda name: None            # type: ignore[assignment]
    _pmod.SOFFICE_CANDIDATES = ()                     # type: ignore[assignment]
    os.environ.pop("SLCN_SOFFICE", None)
    check("(c) 哪儿都找不到 → 返回 None（绝不返回一个不存在的路径）",
          P.find_soffice() is None, f"{P.find_soffice()}")
finally:
    _pmod.shutil.which = _saved_which                 # type: ignore[assignment]
    _pmod.SOFFICE_CANDIDATES = _saved_cands           # type: ignore[assignment]

check("(c) `office.available()` 与 `find_soffice()` 同源（不再有写死的常量）",
      office.available() == (office.find_soffice() is not None))
check("(c) `office` 模块里**没有**写死的 `SOFFICE` 常量（回归守卫：不许再写死 mac 路径）",
      not hasattr(office, "SOFFICE"))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) 静态守卫：交付模块不许有「文本模式 open() 却不写 encoding=」 ──")

SHIPPED = ["convert.py", "checkbib.py"] + [
    str(p.relative_to(ENGINE)) for p in sorted((ENGINE / "slcn").glob("*.py"))
]


def offending_opens(path: pathlib.Path) -> list[str]:
    """返回「文本模式且没写 encoding=」的 open() 行号。

    二进制模式（`"rb"`/`"wb"`/…）不算 —— 那些本来就没有编码问题（实测全库只有 4 处）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # ⚠️ 只认**内置** `open(...)`（`Name` 节点）。
        #    `z.open(src)`（zipfile）/ `p.open()`（Path）是**别的函数**，它们的
        #    mode/encoding 语义不同 —— 第一版把 `z.open` 当成了内置 open，
        #    于是 `docxxml.py` 那行 `z.open(src)` 被误报（它根本没有编码问题）。
        if not (isinstance(fn, ast.Name) and fn.id == "open"):
            continue
        if any(k.arg == "encoding" for k in node.keywords):
            continue
        mode = ""
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = str(node.args[1].value)
        for k in node.keywords:
            if k.arg == "mode" and isinstance(k.value, ast.Constant):
                mode = str(k.value.value)
        if "b" in mode:
            continue
        bad.append(f"{path.name}:{node.lineno}（mode={mode or '默认文本'}）")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None)
        if name in ("read_text", "write_text") and not any(k.arg == "encoding" for k in node.keywords):
            bad.append(f"{path.name}:{node.lineno}（{name} 没写 encoding=）")
    return bad


bad = []
for rel in SHIPPED:
    bad += offending_opens(ENGINE / rel)
check("(d) 交付模块里文本模式的 open() **全部**显式写了 encoding=", not bad, "; ".join(bad[:6]))
check("(d) 覆盖面：确实扫到了这些模块", len(SHIPPED) >= 20, f"{len(SHIPPED)} 个文件")

# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) 版本号只有一处（双平台不许漂移）──")
check("(e) `slcn.__version__` 存在且形如 x.y.z",
      isinstance(__version__, str) and len(__version__.split(".")) == 3, __version__)
r = child("import subprocess, sys; "
          "print(subprocess.run([sys.executable, 'convert.py', '--version'], "
          "capture_output=True, text=True).stdout.strip())")
check("(e) `convert.py --version` 打得出来，且**不要求 --source**",
      r.returncode == 0 and __version__ in r.stdout.decode(), r.stdout.decode().strip()[:90])
r = child("import subprocess, sys; "
          "p=subprocess.run([sys.executable, 'checkbib.py', '--version'], capture_output=True, text=True); "
          "print(p.returncode, p.stdout.strip())")
check("(e) `checkbib.py --version` 同样可用",
      r.returncode == 0 and __version__ in r.stdout.decode(), r.stdout.decode().strip()[:90])
r = child("import sys; sys.path.insert(0,'.'); from slcn import platform as P; print(P.describe())")
check("(e) `describe()` 报出系统 + Python 版本（同事报错时要这一行）",
      r.returncode == 0 and "Python" in r.stdout.decode() and len(r.stdout.decode().strip()) > 12,
      r.stdout.decode().strip())
bs = (ENGINE.parent / "build.sh").read_text(encoding="utf-8")
check("(e) `build.sh` 的版本号是**现取**的，不再写死（双平台防漂移）",
      "slcn/__init__.py" in bs and "CFBundleShortVersionString</key><string>$APP_VERSION" in bs)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (f) 跨盘符路径：`paths.rel()` 不许让一句汇报打断整个转换 ──")

from slcn import paths as _paths  # noqa: E402

# 反例（复现缺陷本身）：Windows 上 `os.path.relpath('C:\\…', 'D:\\…')` 抛
# `ValueError: path is on mount 'C:', start on mount 'D:'`。这里把 `os.path.relpath`
# 换成「必抛」，在 mac/Linux 上**复现同一个失败模式** —— 判据是「调用方会不会因此崩」，
# 与真的盘符无关（这正是本套件 (a)(b)(c) 一贯的手法）。
_saved_relpath = os.path.relpath


def _windows_like_relpath(path, start=None):
    raise ValueError("path is on mount 'C:', start on mount 'D:'")


os.path.relpath = _windows_like_relpath          # type: ignore[assignment]
try:
    _raised = False
    try:
        os.path.relpath("C:\\a\\x.tex", "D:\\repo")
    except ValueError:
        _raised = True
    got = _paths.rel("C:\\probe\\a.tex", "D:\\repo")
    _err = ""
except Exception as e:                            # 修复前：这里就是 ValueError
    _raised, got, _err = False, None, repr(e)
finally:
    os.path.relpath = _saved_relpath              # type: ignore[assignment]
check("(f) 反例：模拟跨盘符时，**裸 `os.path.relpath` 确实会抛**（证明本组有牙齿）", _raised)
check("(f) 跨盘符时 `paths.rel()` **不抛异常**，回落成绝对路径（汇报不该打断转换）",
      got is not None and _err == "", _err or repr(got))

# 行为保持：同盘符（正常情况）必须与 `os.path.relpath` **逐字节相同** ——
# 否则这次修复会悄悄改掉所有产物的路径显示。
_d = tempfile.mkdtemp(prefix="slcn rel ")
_p = os.path.join(_d, "sub", "x.tex")
os.makedirs(os.path.dirname(_p), exist_ok=True)
check("(f) 同盘符时与 `os.path.relpath` 逐字节相同（**行为保持**）",
      _paths.rel(_p, _d) == os.path.relpath(_p, _d), _paths.rel(_p, _d))
_here = os.path.join(os.getcwd(), "convert.py")
check("(f) 不传 start 时仍以 cwd 为基准（与原有调用点语义一致）",
      _paths.rel(_here) == os.path.relpath(_here), _paths.rel(_here))


# 静态守卫：本次事故的根因是交付模块里**裸调** `os.path.relpath`。修完还得防它长回来。
def bare_relpath(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # `os.path.relpath(...)`：Attribute(relpath) ← Attribute(path) ← Name(os)
        if (isinstance(f, ast.Attribute) and f.attr == "relpath"
                and isinstance(f.value, ast.Attribute) and f.value.attr == "path"):
            bad.append(f"{path.name}:{node.lineno}")
    return bad


DELIVERY = SHIPPED + ["cleantemp.py", "compileall.py", "infocheck.py", "mergepdf.py",
                      "proofcheck.py", "toc.py", "bibdb.py", "engine_main.py"]
bad_rel = []
for rel_p in DELIVERY:
    if rel_p.endswith("paths.py"):        # 唯一实现点：它包的就是 `os.path.relpath`
        continue
    bad_rel += bare_relpath(ENGINE / rel_p)
check("(f) 交付模块里没有裸的 `os.path.relpath`（跨盘符会抛，统一走 `paths.rel()`）",
      not bad_rel, "; ".join(bad_rel[:6]))
check("(f) 覆盖面：确实扫到了这些模块", len(DELIVERY) >= 25, f"{len(DELIVERY)} 个文件")

# ─────────────────────────────────────────────────────────────────────────
print("\n── (g) 打包 dispatcher 的入口表（Windows 包只认表里的入口）──")

import engine_main as _em  # noqa: E402  模块级只定义 ENTRIES/USAGE，没有副作用

CLIS = [f for f in ENTRY_FILES if f != "engine_main.py"]
# ── 打包白名单守卫（2026-10-17 macOS 实例测试）──────────────────────────
# `build.sh` 打包 Engine 用的是**白名单**：2026-10-17 新增 `publish.py` 时只改了
# `engine_main.ENTRIES`、漏了 build.sh → v1.0.10 的 mac 包里「本期出刊」直接报
# 「未找到出刊编排引擎 Engine/publish.py」（界面在、引擎不在）。
# ⚠️ 这与案例 106 同型：`gen_docs` 的「App 包内一致性」只比对**已有文件**是否一致，
#    **发现不了"少了一个文件"**。所以必须单独钉住"白名单覆盖全部交付 CLI"。
try:
    from pathlib import Path as _P
    _repo = _P(__file__).resolve().parent.parent.parent
    _build_sh = _repo / "build.sh"
    if _build_sh.exists():
        _bs = _build_sh.read_text(encoding="utf-8")
        _not_packaged = [n for n in ENTRY_FILES if n not in _bs]
        check("(g) `build.sh` 打包白名单覆盖**全部**交付 CLI"
              "（少一个 = 那个入口在 mac 包里根本不存在）",
              not _not_packaged, f"build.sh 未提及: {_not_packaged}")
except Exception as _e:  # noqa: BLE001
    print(f"SKIP  (g) build.sh 白名单守卫（{_e}）")

check("(g) dispatcher 的 `ENTRIES` 覆盖**全部**交付 CLI"
      "（漏一个 = 那个入口在 Windows 包里「不认识的入口」退出 2）",
      set(_em.ENTRIES) == set(CLIS), f"CLI {sorted(CLIS)} ／ dispatcher {sorted(_em.ENTRIES)}")
check("(g) `ENTRIES` 里每个名字都真的有对应文件（防写错名）",
      all((ENGINE / e).is_file() for e in _em.ENTRIES),
      repr([e for e in _em.ENTRIES if not (ENGINE / e).is_file()]))

# ── 两平台共同交付的 CLI（第 132 条）：bibdb.py ───────────────────────────
# 它同时进 **mac app 包**与 **Windows 包**（两边 GUI 的「引用库」5 页：库概况/查库/
# 入藏/待裁决/设置，数据源都是这一份 CLI）。所以它既在 ENTRY_FILES（交付口径：
# utf8_stdio、open() 编码、build.sh 白名单），也必须在 Windows 分发器 `ENTRIES` 里。
# ⚠️ 从前本测试钉的是「bibdb.py 刻意**不进** Windows ENTRIES（引用库只做 mac）」；
#    Windows 版做完后那条守卫必须删掉/反转 —— 留着的话，谁把 bibdb.py 加进分发器
#    都会被测试判红，而漏加的后果是 Windows「引用库」5 页全废（与 publish.py 同型）。
_MAC_ONLY_CLI = "bibdb.py"
if "_build_sh" not in globals():
    _build_sh = None                     # 读 build.sh 失败时上面已 SKIP，这里也别把测试弄崩
if _build_sh is not None and _build_sh.exists():
    check("(g) `build.sh` 打包白名单含 mac 专属 CLI `bibdb.py`"
          "（漏了 = 包里「引用库」5 页全废）",
          _MAC_ONLY_CLI in _bs, "build.sh 未提及 bibdb.py")
    try:
        _gd = (ENGINE / "tools" / "gen_docs.py").read_text(encoding="utf-8")
        check("(g) `gen_docs.py` 的 `APP_FILES` 含 `bibdb.py`"
              "（包内一致性比对覆盖它；`gen_docs` 只比已有文件，发现不了缺文件）",
              f'"{_MAC_ONLY_CLI}"' in _gd, "gen_docs.py APP_FILES 未提及 bibdb.py")
    except OSError as _e:  # noqa: BLE001
        print(f"SKIP  (g) gen_docs APP_FILES 守卫（{_e}）")
    _bibdb_text = (ENGINE / _MAC_ONLY_CLI).read_text(encoding="utf-8")
    check("(g) mac 专属 CLI `bibdb.py` 也调了 utf8_stdio()"
          "（GUI 用管道读它的中文 JSON；窄编码下不许崩）",
          "utf8_stdio(" in _bibdb_text)
check("(g) `bibdb.py` **两个平台都交付**：Windows 分发器 `ENTRIES` 也认它"
      "（漏了 = Windows「引用库」5 页全部「❌ 不认识的入口」退出 2）",
      _MAC_ONLY_CLI in _em.ENTRIES,
      f"dispatcher ENTRIES = {_em.ENTRIES}")
# 别再只断言「进不进」：名字写错（如 bibdb）也会让分发器拒收。文件存在性已由
# 上面 `all((ENGINE / e).is_file())` 守；这里再钉一次名字的**拼写**与交付口径。
check("(g) `bibdb.py` 的入口名与文件逐字一致，且在 `sources-win/engine.py` 的 ENTRIES 里"
      "（外壳/dispatcher 两份表由 sources-win/tests/test_bridge.py (b) 逐项比）",
      (_MAC_ONLY_CLI in _em.ENTRIES)
      and (ENGINE / _MAC_ONLY_CLI).is_file()
      and (_MAC_ONLY_CLI in (ENGINE.parent / "sources-win" / "engine.py")
           .read_text(encoding="utf-8")))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (h) 文档覆盖：engine CLI 的 argparse 子命令名必须写进 ENGINE_API.md ──")
# 弱断言（刻意不做「逐字契约」）：ENGINE_API.md 是给人读的散文 + 表格，这里只钉
# 「每个子命令名在文档里出现过」——新增子命令却漏写文档时，这条会点名缺失的命令。
# 用 `bibdb.build_parser()` 是**真 argparse 表**（不是另抄一份清单），漏加/改名都逃不掉；
# 解析不出子命令（返回值空）也判红，避免「解析失败 → 空缺失 → 假 PASS」。
_CLI_SUBS_CODE = (
    "import sys; sys.path.insert(0, '.')\n"
    "import bibdb\n"
    "sub = [a for a in bibdb.build_parser()._actions\n"
    "       if a.__class__.__name__ == '_SubParsersAction']\n"
    "print('SUBS=' + ','.join(sorted(sub[0].choices)) if sub else 'SUBS=')\n")
r = child(_CLI_SUBS_CODE)
_cli_subs = []
if r.returncode == 0:
    for _line in r.stdout.decode("utf-8", "replace").splitlines():
        if _line.startswith("SUBS="):
            _cli_subs = [s for s in _line[len("SUBS="):].split(",") if s]
_cli_doc = (ENGINE / "docs" / "ENGINE_API.md").read_text(encoding="utf-8")
_missing_cmds = [s for s in _cli_subs if f"`{s}`" not in _cli_doc]
check("(h) `bibdb.py` 的 argparse 子命令名全部出现在 `Engine/docs/ENGINE_API.md`"
      "（漏写文档 = 读者照契约找不到命令）",
      r.returncode == 0 and len(_cli_subs) >= 20 and not _missing_cmds,
      f"rc={r.returncode} 子命令 {len(_cli_subs)} 个；缺失：{_missing_cmds}"
      if r.returncode == 0 else f"rc={r.returncode} {r.stderr[-160:]!r}")

# ─────────────────────────────────────────────────────────────────────────
print("\n── (h) B-207 模块契约：docxsource 拆六模块 + 门面 re-export ──")
# B-207 spec §5。上方 (h) 文档覆盖组保持原样；本组沿用 spec 的「(h)」编号，四条是 (h1)–(h4)。
# 契约对象：`slcn/docxsource.py` 拆成 wordcore/wordentry/wordmeta/wordrender/wordbody/
# wordcitebody 六个静态模块 + docxsource 门面，要求**零行为变化**：旧 `slcn.docxsource`
# 命名空间一个符号都不许少，且动态取到的必须就是 owning 模块里的**同一个对象**。
# 旧符号清单由 `git show e6e4c8d:Engine/slcn/docxsource.py` 的 AST 生成：
#   顶层 def/class + 模块级常量赋值共 211 个（含私有名），外加原 head 的 9 个
#   wordcaption/wordcite 别名。清单长度被 (h1) 钉住：删名/漏名都会红。
_B207_OLD_SYMBOLS = (
    "_ZERO_WIDTH", "_strip_zero_width", "SURNAME_PINYIN", "TRANSLATED_NAMES", "TYPE_MAP",
    "PUNCT_TO_STRIP", "_LATEX_ESCAPES", "_ESCAPE_RE", "escape_latex", "_footnote_latex",
    "escape_bib_value", "RefEntry", "DocxDoc", "_REF_RE", "parse_reference_line",
    "_REF_CN_RE", "_REF_EN_RE", "_QUOTED", "_parse_reference_line_en",
    "_looks_short_bibliographic_note", "_entry_is_note_only", "_src_line_key",
    "_citation_title_year", "_src_line_keys_for_works", "_REF_EN_FULLNAME_RE",
    "_EN_AUTHOR_SPLIT", "_initials", "_parse_reference_line_en_fullname", "_REF_CN2_RE",
    "_parse_reference_line_cn2", "_parse_reference_line_cn", "_NUMBERED_PREFIX_RE",
    "_APA_YEAR_RE", "_APA_YEAR_RANGE_RE", "_EN_SENTENCE_END", "_clean_apa_authors",
    "_apa_kind", "_parse_reference_line_apa", "_CN_COLON_RE",
    "_parse_reference_line_cn_colon", "_EN_YEAR_END_RE", "_parse_reference_line_year_end",
    "_CN_APA_RE", "_parse_reference_line_cn_apa", "_QUOTED_TITLE_RE",
    "_parse_reference_line_loose", "_numbered_refs", "_make_key_core", "make_key",
    "_dedupe_keys", "_EN_JOURNAL_RE", "_EN_JOURNAL_RE2", "_KIND_BY_ETYPE",
    "_match_en_journal", "_clean_journal_name", "_match_incollection", "_en_entry_fields",
    "_EN_ORG_RE", "_finalize_en_entry", "refs_to_bib", "_split_publisher", "_bib_author",
    "_extract_publisher", "_BT_STOP_RE", "_EDITOR_MARK_RE", "_BT_NON_TITLE_RE",
    "_PUB_COLON_RE", "_bt_candidate_ok", "_editor_candidate_ok",
    "_incollection_book_editor", "_incollection_pub_addr", "_HEAD1", "_HEAD_CN_SPACE",
    "_HEAD2", "_HEAD_CN_ARABIC", "_HEAD_CN_DOT", "_HEAD_CN_BAD_TAIL", "_CN_HEAD_PUNCT",
    "_is_cn_numbered_heading", "_is_cn_dot_heading", "_ABS_LEAD", "_ABS_RE", "_KW_RE",
    "_REF_HEADS", "_ABS_LOOKAHEAD", "_ABS_MIN_CHARS", "_ABS_END_CHARS", "_META_LABEL_RE",
    "_CLC_META_RE", "_is_clc_meta_only", "MIN_BODY_CHARS", "_HEAD_EN1", "_HEAD_EN2",
    "_HEAD_EN2_HALF", "_HEAD_EN3", "META_SCAN", "_SUB_RE", "_EMAIL_RE", "_EMAIL_ANY_RE",
    "_AFFIL_HEAD_RE", "_TAIL_BLOCK_SCAN", "_TAIL_LABEL_RE", "_ADDR", "_ADDR_ALONE_RE",
    "_ADDRS_ONLY_RE", "_NAME_ADDR_RE", "_PAREN_ADDR_RE", "_AUTHOR_MARK_RE", "_TRAIL_PUNCT",
    "_PLACEHOLDER_EMAIL_RE", "_EMAIL_TAIL_FALLBACK", "_AFFIL_WORD_RE", "_AFFIL_HEAD_WORDS",
    "_AFFIL_LABEL_RE", "_AFFIL_NEVER_RE", "_AFFIL_PROSE_RE", "_AFFIL_CITY_ZIP_RE",
    "_AFFIL_LIST_ITEM_RE", "_AFFIL_UNIT_START_RE", "_AFFIL_UNIT_TAIL",
    "_AFFIL_UNIT_END_RE", "_AFFIL_UNIT_END_ANY_RE", "_AFFIL_TITLE_TAIL_RE",
    "_AFFIL_NEVER_START_RE", "_LATIN_STOPWORDS", "_AFFIL_LATIN_START_RE", "AFFIL_EN_JOIN",
    "_clean_affil_line", "_clean_affil_line_strict", "_clean_affil_by_sentence",
    "_clean_affil_cjk", "_clean_affil_latin", "_affil_same_unit", "_affil_lines_to_units",
    "_strip_author_names", "_affil_block_hits", "_affil_head_zone",
    "_looks_like_name_line", "collect_affiliations", "_norm_email", "_emails_in_text",
    "collect_emails", "_known_cn_authors", "_known_en_authors", "_is_subtitle",
    "_title_with_break", "_looks_like_person", "_label_body", "_looks_like_abs_prose",
    "_abs_body_cn", "_backtrack_block_start", "_skip_en_refs_to_title",
    "locate_tail_block", "_tail_abstract_cn", "parse_tail_metadata", "_TITLE_SMALL_WORDS",
    "_looks_plain_en_title", "classify_en_heading", "_has_cjk", "find_char_diagrams",
    "SHAPE_PLACEHOLDER", "table_env", "render_cell", "figure_env", "hoist_quote_fallback",
    "render_para", "_AUTO_NUM_FMT_OK", "_AUTO_NUM_LVL_RE", "_PRE_NUM_RE", "_xml_para_text",
    "_docx_numbering_defs", "_auto_numbered_ref_items", "_materialize_auto_numbered_refs",
    "_footnote_num2key_gate", "_footnote_bare_cite_gate_warning", "_THEOREM_ENV",
    "_NUM_CHARS", "_THEOREM_HEAD_RE", "_PROOF_HEAD_RE", "_REMARK_HEAD_RE", "_EQ_REF_RE",
    "_drop_text_prefix", "_theorem_env_of", "_QED_TOKENS", "_strip_qed_tail",
    "_proof_env_of", "_remark_prefix", "parse_docx", "_CITE_RE", "_normalize_locator",
    "_FN_BARE_NUM_RE", "_FN_SENT_END", "_FN_GAP_ASCII_OK", "_FN_GAP_TOKEN_RE",
    "_FN_GAP_PUNCT", "_FN_NONCITE_SUFFIX_RE", "_FN_INHERIT_MAX_GAP", "_fn_inherit_gap_ok",
    "link_footnote_bare_citations", "_NUM_GROUP", "_cite_nums", "_GRP_CN", "_GRP_EN",
    "link_numbered_citations", "_en_locator_cmd", "_LATEX_CMD_RE", "_NAME_TOKEN_RE",
    "_cite_name_tokens", "_author_surname_token", "_year_fallback_evidence",
    "link_citations",
)
_B207_HEAD_ALIASES = {
    "_caption_candidate": ("wordcaption", "caption_candidate"),
    "_find_captions": ("wordcaption", "find_captions"),
    "_is_figure_para": ("wordcaption", "is_figure_para"),
    "_FN_LEAD_FAR_RE": ("wordcite", "CUE_LEAD_FAR_RE"),
    "_FN_LEAD_NEAR_RE": ("wordcite", "CUE_LEAD_NEAR_RE"),
    "_cue_evidence": ("wordcite", "cue_evidence"),
    "_fn_in_math": ("wordcite", "fn_in_math"),
    "_fn_in_paren_depths": ("wordcite", "fn_in_paren_depths"),
    "_link_body_cue_citations": ("wordcite", "link_body_cue_citations"),
}
_B207_MODULES = ("wordcore", "wordentry", "wordmeta", "wordrender", "wordbody",
                 "wordcitebody")
_B207_MISSING = object()


def _b207_top_defs(path: pathlib.Path) -> dict[str, list[int]]:
    """文件里**定义**的顶层符号 → 全部定义行号（def/class/常量赋值；Import 不算）。

    返回 list 而非单个行号：**同一文件内的重复定义也必须被抓到**
    （用 dict 覆盖会让「同一文件里的第二实现」逃过 (h1)/(h4) 的重复检查）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, list[int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.setdefault(node.name, []).append(node.lineno)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out.setdefault(tgt.id, []).append(node.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.setdefault(node.target.id, []).append(node.lineno)
    return out


# ── (h1) facade re-export：完整 + 同一性 ──────────────────────────────────
try:
    from slcn import docxsource as _b207_facade  # noqa: E402
    _b207_facade_err = ""
except Exception as _exc:  # noqa: BLE001
    _b207_facade = None
    _b207_facade_err = repr(_exc)

_b207_owned: dict[str, list[tuple[str, int]]] = {}
_b207_absent_mods: list[str] = []
_b207_import_err: dict[str, str] = {}
_b207_imported: dict[str, object] = {}
for _mod in _B207_MODULES:
    _mod_path = ENGINE / "slcn" / (_mod + ".py")
    try:
        _b207_imported[_mod] = importlib.import_module("slcn." + _mod)
    except Exception as _exc:  # noqa: BLE001
        _b207_import_err[_mod] = repr(_exc)
    if not _mod_path.is_file():
        _b207_absent_mods.append(_mod)
        continue
    for _sym, _lines in _b207_top_defs(_mod_path).items():
        for _line in _lines:
            _b207_owned.setdefault(_sym, []).append((_mod, _line))

_b207_missing = [n for n in _B207_OLD_SYMBOLS
                 if _b207_facade is None or not hasattr(_b207_facade, n)]
_b207_no_owner = [n for n in _B207_OLD_SYMBOLS if not _b207_owned.get(n)]
_b207_dup = sorted(
    "%s→%s" % (n, ["%s:%d" % (m, ln) for m, ln in locs])
    for n, locs in _b207_owned.items() if len(locs) > 1)
_b207_identity_bad: list[str] = []
if _b207_facade is not None:
    for _name in _B207_OLD_SYMBOLS:
        _locs = _b207_owned.get(_name) or []
        if len(_locs) != 1:
            continue
        _owner = _b207_imported.get(_locs[0][0])
        if getattr(_b207_facade, _name, _B207_MISSING) is not getattr(
                _owner, _name, _B207_MISSING):
            _b207_identity_bad.append("%s→%s" % (_name, _locs[0][0]))
    for _alias, (_src_mod, _orig) in _B207_HEAD_ALIASES.items():
        try:
            _src = importlib.import_module("slcn." + _src_mod)
        except Exception as _exc:  # noqa: BLE001
            _b207_identity_bad.append("%s→%s(%r)" % (_alias, _src_mod, _exc))
            continue
        if getattr(_b207_facade, _alias, _B207_MISSING) is not getattr(
                _src, _orig, _B207_MISSING):
            _b207_identity_bad.append("%s→%s.%s" % (_alias, _src_mod, _orig))

check("(h1) 门面 re-export 完整且同一：旧 `docxsource` 的 211 个顶层符号 + 9 个 head 别名"
      "全部在 `slcn.docxsource` 里，且 **is** 其唯一 owning 新模块的同名对象",
      len(_B207_OLD_SYMBOLS) == 211 and len(_B207_HEAD_ALIASES) == 9
      and _b207_facade is not None and not _b207_absent_mods and not _b207_import_err
      and not _b207_missing and not _b207_no_owner and not _b207_dup
      and not _b207_identity_bad,
      "facade=%s 缺模块=%s import失败=%s 缺符号(%d)=%s 无owner=%s 重复定义=%s is不符(%d)=%s"
      % (_b207_facade_err or "ok", _b207_absent_mods, _b207_import_err,
         len(_b207_missing), _b207_missing[:5], _b207_no_owner[:5], _b207_dup[:3],
         len(_b207_identity_bad), _b207_identity_bad[:5]))

# ── (h2) 无循环 import ───────────────────────────────────────────────────
_b207_import_rc = child("import slcn.build, slcn.docxsource, slcn.footnotes")
_b207_graph: dict[str, set[str]] = {}
for _py in sorted((ENGINE / "slcn").glob("*.py")):
    _deps: set[str] = set()
    for _node in ast.parse(_py.read_text(encoding="utf-8")).body:
        if not isinstance(_node, ast.ImportFrom) or (_node.level or 0) < 1:
            continue
        if _node.module:
            _deps.add(_node.module.split(".")[0])
        else:                                    # `from . import footref` 形态
            _deps.update(_a.name.split(".")[0] for _a in _node.names)
    _b207_graph[_py.stem] = _deps


def _b207_find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    color: dict[str, int] = {}
    stack: list[str] = []
    found: list[list[str]] = []

    def visit(u: str) -> None:
        color[u] = 1
        stack.append(u)
        for v in sorted(graph.get(u, ())):
            if v not in graph:
                continue
            if color.get(v, 0) == 1:
                found.append(stack[stack.index(v):] + [v])
            elif color.get(v, 0) == 0:
                visit(v)
        stack.pop()
        color[u] = 2

    for u in sorted(graph):
        if color.get(u, 0) == 0:
            visit(u)
    return found


_b207_cycles = _b207_find_cycles(_b207_graph)
_b207_reverse = [m for m in _B207_MODULES if "docxsource" in _b207_graph.get(m, set())]
_b207_graph_absent = [m for m in _B207_MODULES + ("docxsource",) if m not in _b207_graph]
check("(h2) 无循环 import：子进程 `import slcn.build, slcn.docxsource, slcn.footnotes` rc=0；"
      "且 `slcn/` 顶层相对 import 图**无环**、六个新模块顶层都不 import docxsource",
      _b207_import_rc.returncode == 0 and not _b207_cycles and not _b207_reverse
      and not _b207_graph_absent,
      "rc=%d %r 环=%s 新模块反向import=%s 缺节点=%s"
      % (_b207_import_rc.returncode, _b207_import_rc.stderr[-120:], _b207_cycles[:2],
         _b207_reverse, _b207_graph_absent))

# ── (h3) 行数上限 ────────────────────────────────────────────────────────
_b207_limits = {"docxsource": 3000}
_b207_limits.update({m: 1500 for m in _B207_MODULES})
_b207_line_bad: list[str] = []
_b207_line_absent: list[str] = []
for _mod, _limit in _b207_limits.items():
    _mod_path = ENGINE / "slcn" / (_mod + ".py")
    if not _mod_path.is_file():
        _b207_line_absent.append(_mod)
        continue
    _n = len(_mod_path.read_text(encoding="utf-8").splitlines())
    if _n > _limit:
        _b207_line_bad.append("%s=%d>%d" % (_mod, _n, _limit))
check("(h3) 行数上限：`slcn/docxsource.py` ≤3000 行，六个新模块各 ≤1500 行"
      "（拆分不许把大文件换个名字继续长）",
      not _b207_line_bad and not _b207_line_absent,
      "超限=%s 缺文件=%s" % (_b207_line_bad, _b207_line_absent))

# ── (h4) 判据单点：def/赋值定义各恰好 1 处 ───────────────────────────────
_B207_SINGLE_POINT = ("_work_titles", "_work_count", "looks_classical",
                      "_CLASSICAL_START", "_work_segments", "_is_classical_ref",
                      "split_numbered_bib_note")
_b207_sites: dict[str, list[str]] = {n: [] for n in _B207_SINGLE_POINT}
for _py in sorted((ENGINE / "slcn").glob("*.py")):
    for _sym, _lines in _b207_top_defs(_py).items():
        if _sym in _b207_sites:
            for _line in _lines:
                _b207_sites[_sym].append("%s:%d" % (_py.name, _line))
_b207_multi = {n: s for n, s in _b207_sites.items() if len(s) != 1}
check("(h4) 判据单点：七个判据符号在 `slcn/` 全库的顶层 def/赋值定义**各恰好 1 处**"
      "（Import/ImportFrom 不算；出现第二处 = 双实现回归）",
      not _b207_multi,
      "定义位置=%s%s" % (_b207_sites, ("；不唯一=%s" % _b207_multi) if _b207_multi else ""))
# ─────────────────────────────────────────────────────────────────────────
print("\n── (i) B-229 模块契约：convert.py 拆四模块 + 门面 re-export ──")
# B-229 spec §5。契约对象：`Engine/convert.py`（v1.4.0 时 3899 行）拆成
# convert_metadata / convert_compile / convert_bib / convert_stage 四个静态模块 +
# `Engine/convert.py` 薄门面，要求**零行为变化**：旧 `convert` 命名空间的 80 个非 main
# 顶层符号一个不许少，且动态取到的必须就是 owning 模块里的**同一个对象**；`main` 仍留在
# 门面（CLI 入口 / `engine_main.convert.main()` 契约）。
# 旧符号清单由 `git show v1.4.0:Engine/convert.py` 的 AST 生成（含私有名与模块级常量）；
# 长度被 (i1) 钉住：删名/漏名都会红。
_B229_OLD_SYMBOLS = (
    "WORD_EXT", "TEX_EXT", "_OWN_FIXED", "_OWN_STEM_SUFFIXES", "_IMG_SUFFIXES", "_MID_SUFFIXES",
    "is_own_product", "_BACKUP_RE", "require_source", "die", "_has_cjk", "detect_lang",
    "author_from_source", "find_en_metadata", "mark_en_abstract_roles", "_find_db_root",
    "list_editors", "MetadataJSONError", "_METADATA_JSON_SCALARS", "_METADATA_JSON_AUTHOR_KEYS",
    "_MAX_JSON_AUTHORS", "_json_clean", "_json_affil_list", "_normalize_json_author",
    "load_metadata_json", "supports_structured", "structured_kwargs", "_ensure_vendor",
    "resolve_c_class_union", "pdf_page_count", "_STALE_COMPILE_EXTS",
    "clean_stale_compile_artifacts", "compile_pdf", "_NORM_RE", "_ref_fingerprint",
    "_bib_fingerprints", "_bib_candidates", "_pick_bib", "bib_search_dirs", "find_bib_ex",
    "find_bib", "bib_origin", "_DECL_BIBADD_RE", "_DECL_BIBLIO_RE", "declared_bib_path",
    "real_bib_path", "bib_entry_structurally_safe", "bib_attachment_entries",
    "merge_bib_attachment", "resolve_bibdb", "classify_bibdb", "bibdb_log_line",
    "_bibdb_scan_stem", "bibdb_readonly_scan", "_bibdb_apply_backfill", "bibdb_taskbook_hits",
    "resolve_bibdb_apply_store", "_FINISH_WHY", "channel_provenance", "stage_original",
    "collect_overrides", "supports_overrides", "overrides_kwargs", "apply_ai_merge",
    "_coop_find_bib", "_coop_find_tex", "_coop_locate_state", "run_coop_replies",
    "LLM_FULLTEXT_CHUNK_CHARS", "LLM_FULLTEXT_REPORT_SUFFIX", "LLM_FULLTEXT_RUBRIC",
    "split_fulltext_chunks", "_fulltext_chunk_items", "_llm_fulltext_system", "_llm_fulltext_user",
    "_llm_fulltext_review", "render_llm_fulltext_report", "_add_handoff_args", "_handoff_enabled",
    "_add_c_class_union_args", "_OVERRIDE_FIELDS",
)
_B229_MODULES = ("convert_bib", "convert_compile", "convert_metadata", "convert_stage")
# B-233 mini-split：5 个既有纯 helper（_ref_fingerprint/_bib_fingerprints/
# bib_entry_structurally_safe/bib_attachment_entries/merge_bib_attachment）从
# convert_bib 逐字搬入 bibmerge.py 以守族行数线 → owner/定义单点扫描须含 bibmerge，
# 但**族行数统计仍只算 4 模块**（族线 3977 口径不变，不弱化）。
# B-222 A 段：bib 发现 + bibdb 族 18 符号从 convert_bib 逐字外提 bibpipeline.py
# （同样不计入族行数）→ owner/定义单点扫描再扩 bibpipeline。
_B229_OWNER_MODULES = _B229_MODULES + ("bibmerge", "bibpipeline")
_B229_FAMILY_BASE = 3899              # v1.4.0 时 `Engine/convert.py` 行数
_B229_FAMILY_LIMIT = 3977             # 3899 × 1.02（spec「总量 ±2%」）
_B229_CONVERT_TARGET = 800            # 卡面目标：薄门面
_B229_MODULE_LIMIT = 1500
_B229_EXTRACTED = ("build_arg_parser", "run_refs_attach", "run_verify_refs",
                   "run_coop_taskbook")
_B229_MISSING = object()


# ── (i1) facade re-export：完整 + 同一性 ─────────────────────────────────
try:
    _b229_facade = importlib.import_module("convert")
    _b229_facade_err = ""
except Exception as _exc:  # noqa: BLE001
    _b229_facade = None
    _b229_facade_err = repr(_exc)

_b229_owned: dict[str, list[tuple[str, int]]] = {}
_b229_absent_mods: list[str] = []
_b229_import_err: dict[str, str] = {}
_b229_imported: dict[str, object] = {}
for _mod in _B229_OWNER_MODULES:
    _mod_path = ENGINE / "slcn" / (_mod + ".py")
    try:
        _b229_imported[_mod] = importlib.import_module("slcn." + _mod)
    except Exception as _exc:  # noqa: BLE001
        _b229_import_err[_mod] = repr(_exc)
    if not _mod_path.is_file():
        _b229_absent_mods.append(_mod)
        continue
    for _sym, _lines in _b207_top_defs(_mod_path).items():
        for _line in _lines:
            _b229_owned.setdefault(_sym, []).append((_mod, _line))

_b229_missing = [n for n in _B229_OLD_SYMBOLS
                 if _b229_facade is None or not hasattr(_b229_facade, n)]
_b229_no_owner = [n for n in _B229_OLD_SYMBOLS if not _b229_owned.get(n)]
_b229_dup = sorted(
    "%s→%s" % (n, ["%s:%d" % (m, ln) for m, ln in locs])
    for n, locs in _b229_owned.items() if len(locs) > 1)
_b229_identity_bad: list[str] = []
if _b229_facade is not None:
    for _name in _B229_OLD_SYMBOLS:
        _locs = _b229_owned.get(_name) or []
        if len(_locs) != 1:
            continue
        _owner = _b229_imported.get(_locs[0][0])
        if getattr(_b229_facade, _name, _B229_MISSING) is not getattr(
                _owner, _name, _B229_MISSING):
            _b229_identity_bad.append("%s→%s" % (_name, _locs[0][0]))
# 门面只许定义 `main` 一个顶层符号：旧实现必须整段搬走，不许在门面留第二份定义。
_b229_facade_defs = sorted(set(_b207_top_defs(ENGINE / "convert.py")) - {"main"})
check("(i1) 门面 re-export 完整且同一：旧 `convert.py` 的 81 个非 main 顶层符号"
      "全部在 `convert` 命名空间里，且 **is** 其唯一 owning 新模块的同名对象；"
      "门面自身只定义 `main`、`main` 仍可调用",
      len(_B229_OLD_SYMBOLS) == 81 and _b229_facade is not None
      and not _b229_absent_mods and not _b229_import_err
      and not _b229_missing and not _b229_no_owner and not _b229_dup
      and not _b229_identity_bad and not _b229_facade_defs
      and callable(getattr(_b229_facade, "main", None)),
      "facade=%s 缺模块=%s import失败=%s 缺符号(%d)=%s 无owner=%s 重复定义=%s "
      "is不符(%d)=%s 门面额外定义=%s"
      % (_b229_facade_err or "ok", _b229_absent_mods, _b229_import_err,
         len(_b229_missing), _b229_missing[:5], _b229_no_owner[:5], _b229_dup[:3],
         len(_b229_identity_bad), _b229_identity_bad[:5], _b229_facade_defs[:5]))

# ── (i2) 无循环 import ───────────────────────────────────────────────────
_b229_import_rc = child("import convert, slcn.convert_bib, slcn.convert_compile, "
                        "slcn.convert_metadata, slcn.convert_stage, slcn.bibmerge, "
                        "slcn.bibpipeline")
_b229_graph: dict[str, set[str]] = {}
for _py in sorted((ENGINE / "slcn").glob("*.py")):
    _deps: set[str] = set()
    for _node in ast.parse(_py.read_text(encoding="utf-8")).body:
        if not isinstance(_node, ast.ImportFrom) or (_node.level or 0) < 1:
            continue
        if _node.module:
            _deps.add(_node.module.split(".")[0])
        else:                                    # `from . import footref` 形态
            _deps.update(_a.name.split(".")[0] for _a in _node.names)
    _b229_graph[_py.stem] = _deps
_b229_facade_deps: set[str] = set()
for _node in ast.parse((ENGINE / "convert.py").read_text(encoding="utf-8")).body:
    if isinstance(_node, ast.ImportFrom):
        if (_node.level or 0) >= 1 and _node.module:
            _b229_facade_deps.add(_node.module.split(".")[0])
        elif _node.module and _node.module.startswith("slcn."):
            _b229_facade_deps.add(_node.module.split(".")[1])
    elif isinstance(_node, ast.Import):
        for _a in _node.names:
            if _a.name.startswith("slcn."):
                _b229_facade_deps.add(_a.name.split(".")[1])
_b229_graph["convert"] = _b229_facade_deps
# 4 个新模块的**顶层** import 里不许出现 `import convert` / `from convert import ...`
# （相对 import 图只能抓 `from .` 形态；绝对 import 必须单独用 AST 扫，变异测试 ⓑ 就是这条）。
_b229_top_import_convert: list[str] = []
for _mod in _B229_OWNER_MODULES:
    _mp = ENGINE / "slcn" / (_mod + ".py")
    if not _mp.is_file():
        continue
    for _node in ast.parse(_mp.read_text(encoding="utf-8")).body:
        if isinstance(_node, ast.Import):
            if any(_a.name == "convert" or _a.name.startswith("convert.")
                   for _a in _node.names):
                _b229_top_import_convert.append("%s:%d" % (_mod, _node.lineno))
        elif (isinstance(_node, ast.ImportFrom) and (_node.level or 0) == 0
              and _node.module and (_node.module == "convert"
                                    or _node.module.startswith("convert."))):
            _b229_top_import_convert.append("%s:%d" % (_mod, _node.lineno))
_b229_cycles = _b207_find_cycles(_b229_graph)
_b229_reverse = [m for m in _B229_OWNER_MODULES if "convert" in _b229_graph.get(m, set())]
_b229_facade_deps_bad = sorted(_b229_facade_deps - set(_B229_MODULES))
check("(i2) 无循环 import：子进程 `import convert` + 4 模块 rc=0；`slcn/`+门面的顶层"
      "相对 import 图**无环**；4 个新模块顶层都不 import `convert`；门面顶层相对 import"
      "只指向 4 个新模块",
      _b229_import_rc.returncode == 0 and not _b229_cycles and not _b229_reverse
      and not _b229_facade_deps_bad and not _b229_top_import_convert,
      "rc=%d %r 环=%s 新模块反向import=%s 顶层 import convert=%s 门面依赖=%s"
      % (_b229_import_rc.returncode, _b229_import_rc.stderr[-120:], _b229_cycles[:2],
         _b229_reverse, _b229_top_import_convert[:5], sorted(_b229_facade_deps)))

# ── (i3) 行数上限（门面/新模块/族总量）──────────────────────────────────
_b229_line_bad: list[str] = []
_b229_line_absent: list[str] = []
_b229_convert_lines = len((ENGINE / "convert.py").read_text(encoding="utf-8").splitlines())
if _b229_convert_lines > _B229_CONVERT_TARGET:
    _b229_line_bad.append("convert.py=%d>%d" % (_b229_convert_lines, _B229_CONVERT_TARGET))
_b229_mod_lines: dict[str, int] = {}
for _mod in _B229_MODULES:
    _mod_path = ENGINE / "slcn" / (_mod + ".py")
    if not _mod_path.is_file():
        _b229_line_absent.append(_mod)
        continue
    _n = len(_mod_path.read_text(encoding="utf-8").splitlines())
    _b229_mod_lines[_mod] = _n
    if _n > _B229_MODULE_LIMIT:
        _b229_line_bad.append("%s=%d>%d" % (_mod, _n, _B229_MODULE_LIMIT))
_b229_family_lines = _b229_convert_lines + sum(_b229_mod_lines.values())
check("(i3) 行数上限：`Engine/convert.py` ≤800（薄门面目标；硬代码闸是 3000）、四个新模块"
      "各 ≤1500、拆分族总量（门面+4 模块）≤3977（原 3899 的 +2%）",
      not _b229_line_bad and not _b229_line_absent
      and _b229_family_lines <= _B229_FAMILY_LIMIT,
      "超限=%s 缺文件=%s 实测=门面 %d / %s / 族 %d（限 %d）"
      % (_b229_line_bad, _b229_line_absent, _b229_convert_lines, _b229_mod_lines,
         _b229_family_lines, _B229_FAMILY_LIMIT))

# ── (i4) 定义单点：旧符号与外提函数各恰好 1 处 ──────────────────────────
_b229_sites: dict[str, list[str]] = {n: [] for n in _B229_OLD_SYMBOLS}
_b229_extracted_sites: dict[str, list[str]] = {n: [] for n in _B229_EXTRACTED}
_b229_family_files = [ENGINE / "convert.py"] + [
    ENGINE / "slcn" / (m + ".py") for m in _B229_MODULES]
# (i4) 定义单点扫描 = 门面+4 模块+bibmerge+bibpipeline（mini-split/B-222 A 段后的实际 owner 面）
_b229_owner_files = _b229_family_files + [ENGINE / "slcn" / "bibmerge.py",
                                          ENGINE / "slcn" / "bibpipeline.py"]
for _fp in _b229_owner_files:
    if not _fp.is_file():
        continue
    for _sym, _lines in _b207_top_defs(_fp).items():
        for _line in _lines:
            if _sym in _b229_sites:
                _b229_sites[_sym].append("%s:%d" % (_fp.name, _line))
            if _sym in _b229_extracted_sites:
                _b229_extracted_sites[_sym].append("%s:%d" % (_fp.name, _line))
_b229_multi = {n: s for n, s in _b229_sites.items() if len(s) != 1}
_b229_extracted_multi = {n: s for n, s in _b229_extracted_sites.items() if len(s) != 1}
check("(i4) 定义单点：81 个旧顶层符号在「门面 + 4 模块 + bibmerge + bibpipeline」内的顶层"
      " def/赋值定义各**恰好 1 处**；main 内 4 段外提函数（build_arg_parser/"
      "run_refs_attach/run_verify_refs/run_coop_taskbook）各恰好 1 处定义"
      "（Import/ImportFrom 不算）",
      not _b229_multi and not _b229_extracted_multi,
      "旧符号不唯一=%s；外提函数=%s"
      % (_b229_multi or "无", _b229_extracted_sites))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (j) B-233 `\\bibitem` 自动转换：模块 API / 开关 / CLI 契约 ──")
from slcn import bibitem_convert as _BIC  # noqa: E402
from slcn import convert_bib as _cb  # noqa: E402

check("(j1) 模块 API 齐全：convert/BibitemResult/enabled/set_enabled/"
      "looks_like_bibitems/render_entries",
      all(hasattr(_BIC, n) for n in (
          "convert", "BibitemResult", "enabled", "set_enabled",
          "looks_like_bibitems", "render_entries")))
_saved_bic = _BIC.enabled()
check("(j2) 默认转换开（enabled() is True）", _saved_bic is True)
try:
    _BIC.set_enabled(False)
    _off_ok = _BIC.enabled() is False
finally:
    _BIC.set_enabled(_saved_bic)
check("(j3) set_enabled(False) 生效、可恢复（CLI 开关语义）",
      _off_ok and _BIC.enabled() is _saved_bic)

_BIC_SRC = ("\\begin{thebibliography}{9}\n"
            "% \\bibitem{ghost} Ghost, A.: Ghost title (1999)\n"
            "\\bibitem{art2020} Smith, J.: A synthetic article title. "
            "\\textit{Journal of Synthetic Tests} \\textbf{12}(3), 45--67 (2020)\n"
            "\\end{thebibliography}")
_r1 = _BIC.convert(_BIC_SRC)
_e1 = _r1.entries[0] if _r1.entries else {}
check("(j4) convert 契约：剥注释后 n_source=n_parsed=1、key/字段/类型正确、"
      "ghost 不进 entries",
      _r1.n_source == 1 and _r1.n_parsed == 1 and len(_r1.entries) == 1
      and _e1.get("key") == "art2020" and _e1.get("_type") == "article"
      and _e1.get("author") == "Smith, J." and _e1.get("title")
      and _e1.get("year") == "2020" and "ghost" not in _r1.bib_text,
      repr({k: _e1.get(k) for k in ("key", "_type", "author", "title", "year")}))
check("(j5) looks_like_bibitems：真 bibitem 命中；注释里的/纯正文不命中",
      _BIC.looks_like_bibitems("\\bibitem{a} X") is True
      and _BIC.looks_like_bibitems("% \\bibitem{a} X") is False
      and _BIC.looks_like_bibitems("没有参考文献的正文") is False)
check("(j6) render_entries(entries) 与 convert().bib_text 一致且可被结构校验",
      _BIC.render_entries(_r1.entries) == _r1.bib_text
      and _cb.bib_entry_structurally_safe(
          _BIC.render_entries(_r1.entries).split("\n\n", 1)[1].strip()))
try:
    _r2 = _BIC.convert("\\bibitem")
    _fail_soft = True
except Exception:  # noqa: BLE001  约定 fail-soft：绝不抛
    _fail_soft = False
check("(j7) fail-soft：坏输入不抛异常、无条目时不产出 bib_text",
      _fail_soft and _BIC.convert("").entries == []
      and _BIC.convert("\\bibitem").n_source == 0)

_cv = importlib.import_module("convert")
# 门面在 main() 里用 `_bibitem.add_cli_args(build_arg_parser())` 挂参数（B-229 族净 0 行）：
# 这里按同一顺序复现，并对真实 `convert.py --help` 做一次端到端确认。
_ap_bic = _BIC.add_cli_args(_cv.build_arg_parser())
_ns_def = _ap_bic.parse_args(["--source", "x.tex", "--out", "y"])
_ns_off = _ap_bic.parse_args(["--source", "x.tex", "--out", "y", "--no-bibitem-convert"])
_help_env = dict(os.environ)
_help_env["PYTHONPATH"] = str(ENGINE) + os.pathsep + _help_env.get("PYTHONPATH", "")
_help = subprocess.run([PY, "convert.py", "--help"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env=_help_env, cwd=str(ENGINE))
check("(j8) CLI 契约：--no-bibitem-convert 存在（真实 `--help` 可见）且默认 False",
      _help.returncode == 0 and "--no-bibitem-convert" in _help.stdout
      and _ns_def.no_bibitem_convert is False and _ns_off.no_bibitem_convert is True,
      "help_rc=%d has=%s" % (_help.returncode,
                             "--no-bibitem-convert" in _help.stdout))
_saved_bic = _BIC.enabled()
try:
    _BIC.apply_cli_args(_ns_off)
    _cli_off = _BIC.enabled() is False
finally:
    _BIC.set_enabled(_saved_bic)
check("(j9) apply_cli_args(--no-bibitem-convert) → enabled() False；恢复默认后 True",
      _cli_off and _BIC.enabled() is True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (k) B-222 A 段①：B-233 解析规则收编 footref 判据单点 ──")
# spec §4：B-233 的解析/渲染规则整体迁入 `slcn/footref.py` 文件末尾新节，
# bibitem_convert 只保留开关/结果/key/主入口/CLI；私有跨模块 import 归零。
_B222A_RULE_SYMBOLS = (
    "FIELD_ORDER", "_TYPES", "_UNSAFE_RE", "_YEAR_RE", "_PYEAR_RE", "_MARK_RE",
    "_MARK_TYPE", "_THESIS_RE", "_IN_RE", "_VOL_RE", "_ITALIC_RE", "_BOLD_RE",
    "_PAGES_RE", "_collapse", "_year_of", "_clean_text", "_clean_title",
    "_split_sentence", "_join_authors", "_split_author_rest", "_fill_pub_addr",
    "_parse_marked", "_parse_cjk", "_fill_lncs_tail", "parse_bibitem_entry",
    "render_bibitem_entry", "bibitem_skeleton_fields", "empty_bibitem_fields",
    "clean_bibitem_note",
)
# 迁移前全库已有的同名私有常量（bibdb._YEAR_RE = docx 年份判据、tocdelivery._PAGES_RE =
# 日志页数解析），与 B-233 规则无关、也不在本次 5 文件范围内。判据单点按「footref 恰好 1
# 处 + 其余同名只允许这两个预存异义点」验：防的是「第二份 B-233 实现」，不是同名异义。
_B222A_PREEXISTING_HOMONYMS = {"_YEAR_RE": ("bibdb",), "_PAGES_RE": ("tocdelivery",)}
_b222a_sites: dict[str, list[str]] = {n: [] for n in _B222A_RULE_SYMBOLS}
for _py in sorted((ENGINE / "slcn").glob("*.py")):
    for _sym, _lines in _b207_top_defs(_py).items():
        if _sym in _b222a_sites:
            for _line in _lines:
                _b222a_sites[_sym].append("%s:%d" % (_py.name, _line))
_b222a_bad: dict[str, list[str]] = {}
for _sym, _locs in _b222a_sites.items():
    _own = [s for s in _locs if s.startswith("footref.py:")]
    _known = _B222A_PREEXISTING_HOMONYMS.get(_sym, ())
    _extra = [s for s in _locs if not s.startswith("footref.py:")
              and s.split(":")[0].removesuffix(".py") not in _known]
    if len(_own) != 1 or _extra:
        _b222a_bad[_sym] = _locs
check("(k1) 判据单点：B-233 的 29 个解析/渲染符号在 `slcn/` 全库的顶层 def/赋值定义"
      "**各恰好 1 处且位于 footref.py**（Import/ImportFrom 不算；额外同名只允许迁移前"
      "既存的 bibdb._YEAR_RE / tocdelivery._PAGES_RE 两个异义私有常量）",
      not _b222a_bad,
      "不满足=%s；预存同名=%s" % (_b222a_bad or "无", _B222A_PREEXISTING_HOMONYMS))

_bic_priv_imports: list[str] = []
for _node in ast.walk(ast.parse((ENGINE / "slcn" / "bibitem_convert.py").read_text(
        encoding="utf-8"))):
    if isinstance(_node, ast.ImportFrom):
        _bic_priv_imports += ["%s%s.%s" % ("." * (_node.level or 0), _node.module or "",
                                           _a.name)
                              for _a in _node.names if _a.name.startswith("_")]
    elif isinstance(_node, ast.Import):
        _bic_priv_imports += [_a.name for _a in _node.names
                              if _a.name.split(".")[-1].startswith("_")]
check("(k2) 负例：`bibitem_convert.py` 的**全部** Import/ImportFrom（含函数内）里，"
      "从模块外部导入以 `_` 开头的名字 = 0（B-233 判据不再跨模块借私有实现）",
      not _bic_priv_imports, "私有导入=%s" % _bic_priv_imports)

_B222A_FROM_FOOTREF = ("clean_bibitem_note", "empty_bibitem_fields",
                       "parse_bibitem_entry", "render_bibitem_entry",
                       "bibitem_skeleton_fields")
from slcn import footref as _fr  # noqa: E402
_bic_identity_bad = [n for n in _B222A_FROM_FOOTREF
                     if getattr(_BIC, n, _B229_MISSING) is not getattr(
                         _fr, n, _B229_MISSING)]
check("(k3) import 面/接口：bibitem_convert 从 footref 导入的 5 个公有名与 footref 同名"
      "对象 **is** 一致；`footref.parse_one` 仍在且可调用、"
      "`bibitem_convert.parse_bibitem_entry` 可调用（旧 `_parse_one` 只更名不删除）",
      not _bic_identity_bad
      and callable(getattr(_fr, "parse_one", None))
      and callable(getattr(_BIC, "parse_bibitem_entry", None)),
      "is不符=%s parse_one可调用=%r"
      % (_bic_identity_bad, callable(getattr(_fr, "parse_one", None))))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (l) B-222 A 段②：bib 管线外提 bibpipeline + convert.py 零改动 re-export ──")
_B222A_PIPELINE_SYMBOLS = (
    "_bib_candidates", "_pick_bib", "bib_search_dirs", "find_bib_ex", "find_bib",
    "bib_origin", "_DECL_BIBADD_RE", "_DECL_BIBLIO_RE", "declared_bib_path",
    "real_bib_path", "resolve_bibdb", "classify_bibdb", "bibdb_log_line",
    "_bibdb_scan_stem", "bibdb_readonly_scan", "_bibdb_apply_backfill",
    "bibdb_taskbook_hits", "resolve_bibdb_apply_store",
)
_bp = importlib.import_module("slcn.bibpipeline")
_bp_sites: dict[str, list[str]] = {n: [] for n in _B222A_PIPELINE_SYMBOLS}
for _py in sorted((ENGINE / "slcn").glob("*.py")):
    for _sym, _lines in _b207_top_defs(_py).items():
        if _sym in _bp_sites:
            for _line in _lines:
                _bp_sites[_sym].append("%s:%d" % (_py.name, _line))
_bp_bad = {n: s for n, s in _bp_sites.items()
           if len(s) != 1 or not s[0].startswith("bibpipeline.py:")}
_bp_identity_bad = [n for n in _B222A_PIPELINE_SYMBOLS
                    if getattr(_cv, n, _B229_MISSING) is not getattr(
                        _bp, n, _B229_MISSING)]
check("(l1) 外提符号单点 + 门面同一性：18 个 bib 符号在 `slcn/` 全库顶层定义各恰好 1 处"
      "且属 bibpipeline.py；`convert.<sym> is slcn.bibpipeline.<sym>`（convert.py 零改动）",
      not _bp_bad and not _bp_identity_bad,
      "定义不唯一/归属错=%s；is不符=%s" % (_bp_bad or "无", _bp_identity_bad[:5]))

# (l2) 子进程 import + 既有环图；bibpipeline 顶层不许反向 import convert/convert_bib。
_bp_import_rc = child("import convert, slcn.bibpipeline, slcn.convert_bib")
_bp_top_reverse: list[str] = []
for _node in ast.parse((ENGINE / "slcn" / "bibpipeline.py").read_text(
        encoding="utf-8")).body:
    if isinstance(_node, ast.Import):
        _mods = [_a.name for _a in _node.names]
    elif isinstance(_node, ast.ImportFrom):
        _base = _node.module or ""
        _mods = [_base] + [_base + "." + _a.name for _a in _node.names]
    else:
        continue
    for _m in _mods:
        _parts = [p for p in _m.split(".") if p and p != "slcn"]
        if _parts and _parts[0] in ("convert", "convert_bib"):
            _bp_top_reverse.append("bibpipeline.py:%d→%s" % (_node.lineno, _m))
check("(l2) 无循环 import：子进程 `import convert, slcn.bibpipeline, slcn.convert_bib`"
      " rc=0；既有 `slcn/` 顶层相对 import 环形图**无环**；bibpipeline 顶层不 import"
      " `convert`/`convert_bib`",
      _bp_import_rc.returncode == 0 and not _b229_cycles and not _bp_top_reverse,
      "rc=%d %r 环=%s 反向import=%s"
      % (_bp_import_rc.returncode, _bp_import_rc.stderr[-120:], _b229_cycles[:2],
         _bp_top_reverse[:5]))

# (l3) 族行数下降（B-222 A 段前实测 3904；B-229 硬线 3977；各模块 ≤1500）
_b222a_family_files = [ENGINE / "convert.py"] + [
    ENGINE / "slcn" / (m + ".py") for m in _B229_MODULES]
_b222a_mod_lines = {f.stem: len(f.read_text(encoding="utf-8").splitlines())
                    for f in _b222a_family_files}
_b222a_total = sum(_b222a_mod_lines.values())
_b222a_line_bad = ["%s=%d>1500" % (m, n) for m, n in _b222a_mod_lines.items() if n > 1500]
check("(l3) 族行数下降：门面+4 模块总行数 **< 3904（A 段前实测）** 且 ≤3977、各模块 ≤1500",
      not _b222a_line_bad and 0 < _b222a_total < 3904 and _b222a_total <= _B229_FAMILY_LIMIT,
      "超限=%s 实测=%s 总=%d（前 3904，限 %d）"
      % (_b222a_line_bad, _b222a_mod_lines, _b222a_total, _B229_FAMILY_LIMIT))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (m) B-222 B 段：A 字段存在即输出 / B 类型判据 / C N2 行内句点 / D N5 容器 ──")
import re as _re  # noqa: E402
from slcn import footnotes as _fn  # noqa: E402
from slcn import wordrefs as _wr  # noqa: E402
_br = importlib.import_module("slcn.bibrender")
# (m1) A1–A5：类型序只排序、不筛字段（entry 有就写，没有不造）；排序表单点在 bibrender.py。
_A = [("book", {"_type": "book", "author": "H.", "title": "T", "journal": "Synthese", "volume": "53", "number": "2", "pages": "257--272"}, ("journal", "volume", "number", "pages"), ()),
      ("incollection", {"_type": "incollection", "author": "C.", "title": "T", "booktitle": "B", "editor": "E"}, ("editor",), ()),
      ("inproceedings", {"_type": "inproceedings", "author": "S.", "title": "T", "booktitle": "BT", "editor": "E", "volume": "3", "pages": "1--2"}, ("booktitle", "editor", "volume", "pages"), ()),
      ("book", {"_type": "book", "author": "V.", "title": "T", "booktitle": "BT"}, ("booktitle",), ()),
      ("incollection", {"_type": "incollection", "author": "S.", "title": "T", "booktitle": "B", "edition": "Winter 2022"}, ("edition",), ()),
      ("book", {"_type": "book", "author": "A.", "title": "T", "publisher": "P"}, (), ("journal", "volume", "number", "pages")),
      ("book", {"_type": "book", "author": "A.", "title": "T", "confidence": "low"}, (), ("confidence", "_type"))]
_a_bad = []
for _t, _e, _must, _not in _A:
    _out = _fn.entry_to_bib("k", dict(_e))
    _keys = _re.findall(r"^  (\w+)\s+=", _out, _re.M)
    if (not _out.startswith("@%s{" % _t) or any(k not in _keys for k in _must)
            or any(k in _keys for k in _not)):
        _a_bad.append((_t, _keys))
_br_defs = _b207_top_defs(ENGINE / "slcn" / "bibrender.py")
check("(m1) A1–A5 字段存在即输出：@book 的 journal/volume/number/pages、@incollection 的 "
      "editor/edition、@inproceedings 的容器字段都照写；@book 无 journal 不凭空造；"
      "内部键（confidence/_type）不进 .bib；`bib_field_order` 单点在 bibrender.py、"
      "footnotes 不重复定义",
      not _a_bad and len(_br_defs.get("bib_field_order") or []) == 1
      and "_TYPE_ORDER" not in _b207_top_defs(ENGINE / "slcn" / "footnotes.py"),
      "不符=%s 定义=%s" % (_a_bad[:2], sorted(_br_defs)))
# (m2) B1/B2：作者串里的 `(ed.)/(eds.)` 是编著整书的作者位，不是容器线索；真析出仍 incollection。
_b1 = _fr.classify_entry_type({"author": "{Salerno (ed.)}", "publisher": "Oxford University Press", "address": "Oxford"}, raw="Salerno (ed.), New Essays on the Knowability Paradox, Oxford: Oxford University Press, 2009.")[0]
_b2 = _fr.classify_entry_type({"author": "{F. H. van Eemeren and P. Wu (eds.)}", "publisher": "John Benjamins", "address": "Amsterdam"}, raw="F. H. van Eemeren and P. Wu (eds.), Contextualizing Pragma-dialectics, Amsterdam: John Benjamins, 2017.")[0]
_b_neg1 = _fr.classify_entry_type({"booktitle": "Philosophical Logic", "publisher": "D. Reidel Publishing Company", "address": "Dordrecht", "pages": "74--88"}, raw="C. Harrison, 1969, “The Unanticipated Examination”, in J. W. Davis, D. J. Hockney and W. K. Wilson (eds.), Philosophical Logic, Dordrecht: D. Reidel Publishing Company, pp. 74-88.")[0]
_b_neg2 = _fr.classify_entry_type({}, raw="X, “T”, in Y (eds.), Book, pp. 1-2.")[0]
check("(m2) B1/B2：编著整书（作者串内 `(ed.)/(eds.)`）→ @book；真析出（`in … (eds.)`）仍 @incollection",
      (_b1, _b2, _b_neg1, _b_neg2) == ("book", "book", "incollection", "incollection"),
      "实测=%s" % ((_b1, _b2, _b_neg1, _b_neg2),))
# (m3) B3：`北京师范大学博士论文`（校名在学位类型前）→ @phdthesis；真期刊仍 @article。
_bt_raw = "王雪君，基于将来的不确定性对将来偶然命题和“意外”的研究，北京师范大学博士论文，2020年。"
_p3 = _fr.parse_numbered_item(_bt_raw)
_tf = _wr.thesis_fields(_bt_raw)
_pj = _fr.parse_numbered_item("李四，论某某问题，哲学研究，2020年第3期：1-10。")
_po = _fr.parse_numbered_item("[9] 张三，题名题名题名，博士学位论文，北京：北京大学，2020年。")
check("(m3) B3：校名+博士/硕士学位论文 → @phdthesis（journal 清空、school=校名）；"
      "真期刊名仍 @article；旧语序不在此抢判",
      _p3.etype == "phdthesis" and _p3.journal == "" and _tf is not None
      and _tf["school"] == "北京师范大学" and _tf["degree"] == "博士论文"
      and _pj.etype == "article" and _pj.journal == "哲学研究" and _po.etype == "misc",
      "B3=%s/%s 期刊=%s 旧语序=%s" % (_p3.etype, _p3.journal, _pj.etype, _po.etype))
# (m4) B5：`_MARK_TYPE` 只能是 `TYPE_MARK` 的别名（旧表在 N/R/A 发散，不得放宽）。
_fr_src = (ENGINE / "slcn" / "footref.py").read_text(encoding="utf-8")
check("(m4) B5 标记→类型单点：`_MARK_TYPE is TYPE_MARK`、逐键相等、源码无第二份字面表",
      _fr._MARK_TYPE is _fr.TYPE_MARK and dict(_fr._MARK_TYPE) == dict(_fr.TYPE_MARK)
      and _fr_src.count("_MARK_TYPE = TYPE_MARK") == 1 and "_MARK_TYPE = {" not in _fr_src,
      "is=%r N/R/A=%s" % (_fr._MARK_TYPE is _fr.TYPE_MARK, {k: _fr._MARK_TYPE[k] for k in "NRA"}))
# (m5) C（N2 行内句点）：`from URL` 是出版信息残段，不并题名；真功能词续接仍并。
check("(m5) C N2：`, from URL`/`. Cambridge` 视为出版信息（continuation=0）、真功能词续接仍并（1）",
      (_fr.title_comma_continuation(["Reason, Truth and History", "from URL"]),
       _fr.title_comma_continuation(["Language, Truth and Logic", ". Cambridge"]),
       _fr.title_comma_continuation(["Essays", "from the Edge"])) == (0, 0, 1),
      "实测=%s" % ([_fr.title_comma_continuation(s) for s in (
          ["Reason, Truth and History", "from URL"],
          ["Language, Truth and Logic", ". Cambridge"], ["Essays", "from the Edge"])],))
# (m6) D（N5）：安全 C 形态（allow_c=False 也认）；期刊/无年份短引不认；`in 编者 (eds.), 书名`
#      → booktitle；中文集刊 → incollection；bibrender 无循环；footnotes re-export is 同一对象。
_n5 = _wr.incollection_editor_list("in The Importance of What We Care About, 2005", allow_c=False)
_n5j = _wr.incollection_editor_list("in Journal of Philosophy, 68(1): 5-20", allow_c=False)
_n5s = _wr.incollection_editor_list("in The Importance of What We Care About, p. 262", allow_c=False)
_d3 = _wr.incollection_editor_list("in J. W. Davis, D. J. Hockney and W. K. Wilson (eds.), Philosophical Logic, Dordrecht: D. Reidel Publishing Company, pp. 74-88", allow_c=False)
_coll = _fr.parse_numbered_item("沃斯、费布鲁赫、库伊（著），黄婷（译），从哈克标准看知道者悖论的解决方案，逻辑学动态与评论，2025年第1辑（总第7辑）：3-36。")
check("(m6) D N5：`in 书名, 2005` 出 booktitle（allow_c=False 也认）；期刊/无年份短引不认；"
      "`in 编者 (eds.), 书名` 出 booktitle+editor；中文集刊 → @incollection；"
      "bibrender 无循环、footnotes re-export is 同一对象",
      _n5 == ("The Importance of What We Care About", "") and _n5j == ("", "") and _n5s == ("", "")
      and _d3 == ("Philosophical Logic", "J. W. Davis, D. J. Hockney and W. K. Wilson")
      and (_coll.etype, _coll.booktitle) == ("incollection", "逻辑学动态与评论")
      and child("import slcn.bibrender, slcn.footnotes, slcn.footref, slcn.wordrefs").returncode == 0
      and _fn._TYPE_ORDER is _br._TYPE_ORDER,
      "n5=%s 期刊=%s 短引=%s d3=%s 集刊=%s" % (_n5, _n5j, _n5s, _d3, _coll.booktitle))
# (m7) B-135 核心：库值覆盖（合成 bib/record/safe；hermetic，不读真实库、不联网）。
#      口径：库有非空值 → 覆盖现值/补齐缺失；库缺/空 → 保留产物；同值不重写；
#      非 HIT_SAFE / 类型表外 / 非 8 类字段 → 不动；未命中条目逐字节不变；结构异常跳过。
_bdb = importlib.import_module("slcn.bibdb")
_bap = importlib.import_module("slcn.bibdbapply")
_b1 = "\n  author = {A},\n  journal = {Old},\n  pages = {9--9}\n"
_b2 = "\n  author = {B}\n"
_b3 = "\n  author = {C},\n  journal = {Same}\n"
_b4 = "\n  author = {D},\n  journal = {Keep}\n"
_B7 = ("@article{K1," + _b1 + "}\n\n@article{K2," + _b2 + "}\n\n"
       "@article{K3," + _b3 + "}\n\n@article{K4," + _b4 + "}\n")
_SAFE7 = {"K1": {"status": _bdb.HIT_SAFE,
                 "record": {"fields": {"journal": "New Journal", "volume": "7",
                                       "pages": "1--20"}}},
          "K2": {"status": _bdb.HIT_SAFE,
                 "record": {"fields": {"journal": "Mind", "volume": ""}}},
          "K3": {"status": _bdb.HIT_SAFE, "record": {"fields": {"journal": "Same"}}},
          "K4": {"status": _bdb.HIT_CANDIDATE, "record": {"fields": {"journal": "X"}}}}
_pl7 = _bap.plan_fills(None, [("article", "K1", _b1), ("article", "K2", _b2),
                              ("article", "K3", _b3), ("article", "K4", _b4)], _SAFE7)
_p7 = {x["key"]: x for x in _pl7}
_new7, _app7 = _bap.apply_text(_B7, _pl7)
_app7d = {a["key"]: a for a in _app7}
_chk7 = _bap.format_checklist(_app7)
_ok7a = (_p7.get("K1", {}).get("fills") == {"volume": "7"}
         and _p7.get("K1", {}).get("replaces")
         == {"journal": "New Journal", "pages": "1--20"}
         and _p7.get("K2", {}).get("fills") == {"journal": "Mind"}
         and _p7.get("K2", {}).get("replaces") == {}
         and "K3" not in _p7 and "K4" not in _p7)
_ok7b = ("journal = {New Journal}" in _new7 and "volume = {7}" in _new7
         and "pages = {1--20}" in _new7 and "journal = {Old}" not in _new7
         and "journal = {Mind}" in _new7 and "journal = {Same}" in _new7
         and "journal = {Keep}" in _new7 and _new7.count("journal") == 4
         and _app7d.get("K1", {}).get("filled")
         == {"volume": "7", "journal": "New Journal", "pages": "1--20"}
         and _app7d.get("K1", {}).get("replaced")
         == {"journal": "Old", "pages": "9--9"}
         and list(_app7d) == ["K1", "K2"])
_ok7c = ("@article{K3,\n  author = {C},\n  journal = {Same}\n}" in _new7)
_ok7d = (not _bap.plan_fills(None, [("misc", "K9", "\n  author = {E},\n  journal = {Old}\n")],
                            {"K9": {"status": _bdb.HIT_SAFE,
                                    "record": {"fields": {"journal": "New"}}}})
         and not _bap.plan_fills(None, [("article", "K8", _b2)],
                                 {"K8": {"status": _bdb.HIT_CANDIDATE,
                                         "record": {"fields": {"journal": "X"}}}})
         and "volume = {}" not in _new7)
_r7 = _bap._render_entry("\n  author = {A},\n  journal = {Old}\n", {}, {"journal": "New"})
_r7bad = _bap._render_entry("\n  author = {A},\n  journal = {Old\n", {}, {"journal": "New"})
_ok7e = (_r7 is not None and "}{" not in _r7 and "journal = {New}" in _r7
         and _r7bad is None
         and "journal：（旧值 Old）→ New Journal" in _chk7
         and "volume：（缺失）→ 7" in _chk7)
check("(m7) B-135 核心：库有非空值 → 覆盖现值/补齐缺失（fills+replaces）；库缺/空保留"
      "产物值；同值不重写；候选/类型表外不动；未命中条目逐字节不变；_render_entry"
      "结构异常 fail-soft 返回 None",
      _ok7a and _ok7b and _ok7c and _ok7d and _ok7e,
      "a=%r b=%r c=%r d=%r e=%r plans=%s" % (_ok7a, _ok7b, _ok7c, _ok7d, _ok7e, _pl7))

# ─────────────────────────────────────────────────────────────────────────
# (n) 双壳页面集合一致性（B-230 壳契约；**静态解析**，不 import tkinter/tk）
#
# 口径（`notes/drafts/specs/b230-shell-split.md` §4.1 逐字核对过，**不是**「集合完全相等」）：
#   mac `NavPage` 15 页 / win `tabs` 14 页；mac 只多「目录摘要推送」（B-225 仅 mac 交付，win 无此页），
#   唯一允许的中文别名是「待处理」(mac；B-188 改名) ⇄「待裁决」(win)，其余逐字相同；
#   三个分组「单篇稿件 / 整期装配 / 引用库」逐组**集合**相等（整期装配 mac 多一页），
#   **不比较组内顺序**（win 的「生成目录」在「编译全部」之前、mac 相反 —— 已知差异，登记不修）。
#   缺文件（比如只拿了 Engine/ 的瘦检出）→ SKIP，绝不假 PASS。
#   组号用 (n) 而不是 spec 写的 (i)：a–m 已占用，i1–i4 属 B-229 门面组（卡面 §1c）。
_PAGES_ROOT = ENGINE.parent
_MAC_PAGES_SRC = _PAGES_ROOT / "sources-mac" / "Views" / "MainWindow.swift"
_WIN_TABS_DIR = _PAGES_ROOT / "sources-win" / "tabs"
_WIN_PAGES_SRC = ([_PAGES_ROOT / "sources-win" / "main.py"]
                  + sorted(_WIN_TABS_DIR.glob("*.py"))
                  + [_PAGES_ROOT / "sources-win" / "bibdb_ui.py"])
_PAGES_MISSING = [str(_p) for _p in [_MAC_PAGES_SRC, _WIN_TABS_DIR] + _WIN_PAGES_SRC
                  if not _p.exists()]

if _PAGES_MISSING:
    for _g in ("n1", "n2", "n3"):
        print(f"SKIP  ({_g}) 双壳页面集合：缺 {len(_PAGES_MISSING)} 处源文件，"
              f"本机不判（{_PAGES_MISSING[:3]}）")
else:
    _mac_src = _MAC_PAGES_SRC.read_text(encoding="utf-8")
    _win_src = [_p.read_text(encoding="utf-8") for _p in _WIN_PAGES_SRC]
    _win_all = "\n".join(_win_src)
    _win_main = _win_src[0]

    # mac 中文名：只取 `var title: String` 的 switch（`var symbol` 那段是 SF Symbol 英文名，
    # 一起收会把中文名覆盖成图标名）。
    _mac_title_block = re.search(r"var title: String \{(.*?)\n    \}", _mac_src, re.S)
    _mac_titles = dict(re.findall(r'case \.(\w+): return "([^"]+)"',
                                 _mac_title_block.group(1) if _mac_title_block else ""))
    # win 中文名：各 tab 的类属性 `name = "..."`（「设置」是组外底部页，兜一层）
    _win_names = set(re.findall(r'^\s+name = "([^"]+)"', _win_all, re.M)) | {"设置"}
    _ALIAS = {"待处理": "待裁决"}       # B-188 只改了 mac 侧 → 唯一允许的别名
    _MAC_ONLY = {"目录摘要推送"}        # B-225 只交付了 mac → 唯一允许的缺失页

    _mac_zh = {_ALIAS.get(_t, _t) for _t in _mac_titles.values()}
    _mac_side = _mac_zh - _win_names
    _win_side = _win_names - _mac_zh
    check("(n1) 双壳页面集合：mac NavPage 15 页 / win tabs 14 页，mac 仅多「目录摘要推送」，"
          "win 没有 mac 之外的页（别名「待处理」⇄「待裁决」已归一）",
          _mac_side == _MAC_ONLY and _win_side == set(),
          f"mac={len(_mac_zh)} 页 / win={len(_win_names)} 页｜mac−win={sorted(_mac_side)}｜"
          f"win−mac={sorted(_win_side)}")

    check("(n2) 允许清单自检（防白名单僵化）：目录摘要推送 在 mac、待处理 在 mac、"
          "待裁决 在 win、待处理 **不在** win（别名两侧各留一处才算没漂移）",
          "目录摘要推送" in _mac_titles.values() and "待处理" in _mac_titles.values()
          and "待裁决" in _win_names and "待处理" not in _win_names,
          f"目录摘要推送@mac={'目录摘要推送' in _mac_titles.values()} "
          f"待处理@mac={'待处理' in _mac_titles.values()} "
          f"待裁决@win={'待裁决' in _win_names} 待处理@win={'待处理' in _win_names}")

    # (n3) 分组：mac 三个 static let 名单 × win `self.nav_groups` 的索引 → 组名→中文名集合。
    # mac 侧 key（NavPage）→ 中文名（过别名）；组名按编辑流程一一对应。
    _mac_groups: dict[str, set[str]] = {}
    for _key in ("singleArticle", "assembly", "library"):
        _gm = re.search(r"static let %s: \[NavPage\] = \[(.*?)\]" % _key, _mac_src, re.S)
        _mac_groups[_key] = {_ALIAS.get(_mac_titles.get(_k, "?" + _k),
                                        _mac_titles.get(_k, "?" + _k))
                             for _k in (re.findall(r"\.(\w+)", _gm.group(1)) if _gm else [])}
    # win 侧：`self.tabs` 的属性顺序就是侧栏索引；属性 → 页类 → 类属性的 name
    _tabs_block = re.search(r"self\.tabs = \[(.*?)\]", _win_main, re.S)
    _win_attrs = re.findall(r"self\.(\w+)", _tabs_block.group(1)) if _tabs_block else []
    _win_cls = dict(re.findall(r"self\.(\w+) = (?:[A-Za-z_]\w*\.)?(\w+)\(", _win_main))
    _win_tab_names = []
    for _a in _win_attrs:
        _cm = (re.search(r'^class %s\b.*?^\s+name = "([^"]+)"' % _win_cls[_a], _win_all,
                         re.S | re.M) if _a in _win_cls else None)
        _win_tab_names.append(_cm.group(1) if _cm else "?" + _a)
    _ng_block = re.search(r"self\.nav_groups = \[(.*?)\]", _win_main, re.S)
    _win_groups: dict[str, set[str]] = {}
    for _gname, _idxs in re.findall(r'\("([^"]+)",\s*\(([\d,\s]+)\)\)',
                                    _ng_block.group(1) if _ng_block else ""):
        _win_groups[_gname] = {
            (_ALIAS.get(_win_tab_names[int(_i)], _win_tab_names[int(_i)])
             if 0 <= int(_i) < len(_win_tab_names) else "?idx" + _i.strip())
            for _i in _idxs.split(",") if _i.strip()}

    _GROUP_PAIRS = (("singleArticle", "单篇稿件"), ("assembly", "整期装配"),
                    ("library", "引用库"))
    _MAC_GROUP_EXTRA = {"assembly": {"目录摘要推送"}}   # 整期装配：mac 多「目录摘要推送」
    _group_bad: list[str] = []
    _group_detail: list[str] = []
    for _mac_key, _win_gname in _GROUP_PAIRS:
        _mg = _mac_groups.get(_mac_key, set())
        _wg = _win_groups.get(_win_gname, set())
        _extra, _miss = _mg - _wg, _wg - _mg
        _ok = _extra == _MAC_GROUP_EXTRA.get(_mac_key, set()) and _miss == set()
        _group_detail.append("%s(mac=%d/win=%d) mac−win=%s win−mac=%s%s"
                             % (_win_gname, len(_mg), len(_wg), sorted(_extra),
                                sorted(_miss), "" if _ok else " ✗"))
        if not _ok:
            _group_bad.append(_win_gname)
    check("(n3) 分组一致性：单篇稿件 / 整期装配 / 引用库 三组「组名→中文名集合」逐组相等"
          "（整期装配 mac 仅多「目录摘要推送」；不比较组内顺序）",
          not _group_bad and len(_win_groups) == 3 and all(_mac_groups.values()),
          "｜".join(_group_detail))

# ─────────────────────────────────────────────────────────────────────────
# (o) 分发不泄漏：swift 侧外壳**不许**再绑 publish.py（B-231 ④；静态扫描，不 import 外壳）
#
# 口径（根 `README.md`「一个引擎，两个外壳」+「Engine/ 引擎（Python，唯一一份逻辑）」；
# `Engine/docs/ENGINE_API.md` 与根 `README.md:116` 都把 `publish.py` 标成 **CLI-only**
# —— 2026-10-21 第 71 批 B-116 起 GUI「本期出刊」页已删，逻辑只在引擎里）：
#   · 扫描集 = 仓库根 `sources-mac/**/*.swift` + `sources-win/**/*.py`；
#   · **排除** `sources-win/engine.py` —— Windows 分发器的 `ENTRIES` **必须**含 publish.py
#     （跨平台交付事实，已由 (g) 组守，这里不重复断言）；
#   · **排除** `sources-win/tests/` —— 测试夹具故意逐字引用入口表；
#   · 命中 = `\bpublish\.py\b` 或当年的 GUI 绑定符号 `PublishTab` / `PublishFeature` /
#     `publish_call`（任一出现 = 「本期出刊」页被接回外壳，B-227 删页后的回潮）。
#
# 路径口径修正（见 `ROOT/DECISIONS.md` b231-i4）：提示词写的 `Engine/vendor/README.md` 与
# `Engine/native/slcn` 在本树里**都不存在**（`Engine/vendor/` 是被 .gitignore 的 pip 依赖
# 目录、没有 README；`git ls-files` 无任何 `native/` 路径），`tools/batch_accept.py` 也没有
# 可复用的源码扫描函数（只有 `parse_*` / `judge_gate` 证据解析）。故按上面这份**真实口径**
# 就地扫描（**未复制任何既有逻辑**，因为没有可复用的实现）。
_NOLEAK_ROOT = ENGINE.parent
_NOLEAK_MAC_DIR = _NOLEAK_ROOT / "sources-mac"
_NOLEAK_WIN_DIR = _NOLEAK_ROOT / "sources-win"
_NOLEAK_WIN_ENTRY = _NOLEAK_WIN_DIR / "engine.py"   # 分发器入口表：必须含 publish.py（(g) 组守）
_NOLEAK_MAC_FILES = sorted(_NOLEAK_MAC_DIR.rglob("*.swift")) if _NOLEAK_MAC_DIR.is_dir() else []
_NOLEAK_WIN_FILES = ([p for p in sorted(_NOLEAK_WIN_DIR.rglob("*.py"))
                      if p != _NOLEAK_WIN_ENTRY
                      and "tests" not in p.relative_to(_NOLEAK_WIN_DIR).parts]
                     if _NOLEAK_WIN_DIR.is_dir() else [])
_NOLEAK_FILES = _NOLEAK_MAC_FILES + _NOLEAK_WIN_FILES
_NOLEAK_RE = re.compile(r"\bpublish\.py\b|PublishTab|PublishFeature|publish_call")
_no_leaks: list[str] = []
for _p in _NOLEAK_FILES:
    try:
        _txt = _p.read_text(encoding="utf-8", errors="replace")
    except OSError as _e:                    # 读不动 = 红（绝不静默跳过）
        _no_leaks.append(f"{_p.relative_to(_NOLEAK_ROOT)}: <读失败 {_e}>")
        continue
    for _ln, _line in enumerate(_txt.splitlines(), 1):
        _m = _NOLEAK_RE.search(_line)
        if _m:
            _no_leaks.append(f"{_p.relative_to(_NOLEAK_ROOT)}:{_ln}:{_m.group(0)}")

check("(o1) swift 侧外壳零 publish.py 绑定：sources-mac/**/*.swift + sources-win/**/*.py"
      "（排除分发器 engine.py 与 tests/）里不许出现 publish.py / PublishTab / "
      "PublishFeature / publish_call —— 外壳只许 CLI 起引擎，不许把删掉的「本期出刊」页接回来",
      not _no_leaks,
      f"扫 {len(_NOLEAK_FILES)} 个外壳源文件（mac {len(_NOLEAK_MAC_FILES)} / "
      f"win {len(_NOLEAK_WIN_FILES)}）｜命中 {len(_no_leaks)} 处：{_no_leaks[:5]}")

# (o2) 反向防僵化：上面那条**必须真有文件可扫**，否则「零命中」是真空绿（缺目录 → 红，不 SKIP）。
#     另钉住分发引擎目录仍在原地：Swift 侧（`build.sh` 打包进 App 的 `Resources/Engine`）与
#     两个外壳共用同一份 `Engine/slcn`；提示词写作 `Engine/native/slcn`，实测只有 `Engine/slcn`。
_NOLEAK_DIST = ENGINE / "slcn"
_O2_MAC_N = len(_NOLEAK_MAC_FILES)
_O2_WIN_N = len(_NOLEAK_WIN_FILES)
_O2_DIST_N = len(sorted(_NOLEAK_DIST.glob("*.py"))) if _NOLEAK_DIST.is_dir() else 0
check("(o2) 扫描范围非空（防 (o1) 真空绿）：mac 壳 Swift 源、win 壳 py 源各 >0 且两目录都在"
      "扫描范围内；分发引擎目录 Engine/slcn 存在且非空",
      _NOLEAK_MAC_DIR.is_dir() and _NOLEAK_WIN_DIR.is_dir()
      and _O2_MAC_N > 0 and _O2_WIN_N > 0 and _O2_DIST_N > 0,
      f"mac={'有' if _NOLEAK_MAC_DIR.is_dir() else '缺'}{_O2_MAC_N} 文件｜"
      f"win={'有' if _NOLEAK_WIN_DIR.is_dir() else '缺'}{_O2_WIN_N} 文件｜"
      f"slcn={_O2_DIST_N} 个 .py｜合计扫 {len(_NOLEAK_FILES)}")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
