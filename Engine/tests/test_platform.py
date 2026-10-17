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
import os
import pathlib
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
                "publish.py",
               "cleantemp.py", "infocheck.py", "toc.py", "engine_main.py"]
_missing = []
for _f in ENTRY_FILES:
    _ok = any(isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "utf8_stdio"
              for n in ast.walk(ast.parse((ENGINE / _f).read_text(encoding="utf-8"))))
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
                      "proofcheck.py", "toc.py", "engine_main.py"]
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

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
