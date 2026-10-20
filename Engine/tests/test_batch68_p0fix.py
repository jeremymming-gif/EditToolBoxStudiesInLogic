#!/usr/bin/env python3.14
r"""批68 A（B-018）：抽引用键前先剥 LaTeX 注释——90-03 `p985` 假 DANGLING 回归。

根因（批67 定性，`EditToolBox-notes/drafts/specs/b67-B018-p0-defects.md`）：
`p985` 只出现在**整行 `%` 开头的注释草稿**（`main.tex:40`，生成稿/人工 tex 同样注释），
biber 23 键、`.bcf`/PDF 无 p985；但 `slcn/bibcheck.py::cited_keys` 与
`slcn/sources.py::extract_bibkeys_from_body` 用正则扫全文、不剥注释 → 假悬空引用，
且 convert 出假告警「正文引用但 bib 里没有的键 1 个：p985」（`build.py` 走后者）。
批68 修复：两处抽键前复用 `slcn/latex.py::strip_comments`（已处理 `\%` 转义）。

钉住（本套件不读真实语料、不跑 xelatex、不读稿件库，秒级）：
  ① 整行注释 / 行中 `%` 之后的 cite 不计；
  ② `\%` 转义不误剥：其后的 cite 仍计入；
  ③ 非注释 cite 全部保留（无注释文本与旧口径逐键相同）；
  ④ `check_citations` 不再报注释里的键「悬空」；
  ⑤ 批测试 `citations_closure` 同口径：`%\cite{C}` + `\cite{A}` → dangling=[]、n_cited=1；
  ⑥ `build.py` 的告警路径与 `sources.extract_bibkeys_from_body` 是**同一函数**（不另起正则）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" \
      /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/test_batch68_p0fix.py
"""

import importlib.util
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # 不在 tools/ 下留 __pycache__

_HERE = Path(__file__).resolve().parent          # Engine/tests
_ENGINE = _HERE.parent                            # Engine
_ROOT = _ENGINE.parent                            # 公开仓根
for _p in (str(_ENGINE), str(_ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if not cond and detail else ""))


from slcn import build, sources                     # noqa: E402
from slcn.bibcheck import check_citations, cited_keys  # noqa: E402

# 批测试工具的闭合判据（复用 slcn.bibcheck 同一口径）；按路径导入避免依赖 cwd
_spec = importlib.util.spec_from_file_location("batch_case_test", _ROOT / "tools" / "batch_case_test.py")
assert _spec and _spec.loader, "找不到 tools/batch_case_test.py"
batch = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = batch  # dataclass 依赖 cls.__module__ 在 sys.modules 里
_spec.loader.exec_module(batch)

_BIB_A = "@article{A,\n title={t}\n}"

print("── ① cited_keys：注释里的 cite 不计 ──")
check("整行注释 `%\\cite{C}` 不计", cited_keys("%\\cite{C}\n\\cite{A}") == {"A"})
check("行中 `%` 之后的 cite 不计",
      cited_keys("\\parencite{A} % \\parencite{B}\n\\textcite{C}") == {"A", "C"})
check("整行注释 + 行中注释混合", cited_keys("% \\cite{C}\n\\cite{A} 5\\% \\parencite{B}") == {"A", "B"})

print("── ② `\\%` 转义不误剥 ──")
check("`\\cite{A} 100\\% \\cite{B}` → 两个键都计（百分号不是注释）",
      cited_keys("\\cite{A} 100\\% \\cite{B}") == {"A", "B"})
check("`\\%` 出现在带可选参数的 cite 前不影响后续",
      cited_keys("\\parencite[第 5\\% 节]{A}\n\\textcite{B}") == {"A", "B"})

print("── ③ 非注释 cite 保留（默认口径零变化）──")
check("无注释文本：parencite 多键 + textcite 星号+可选参数",
      cited_keys("\\parencite{A,B}\n\\textcite*[p.1]{C}") == {"A", "B", "C"})
check("空文本 / 只有注释 → 空集",
      cited_keys("") == set() and cited_keys("% \\cite{C}\n") == set())

print("── ④ check_citations：注释键不再报悬空 ──")
_problems = check_citations("%\\parencite{C}\n\\parencite{A}", _BIB_A)
check("bib 有 A、正文非注释引 A → 无问题（C 只在注释里）",
      _problems == [], repr(_problems))
check("（对照）真有悬空引用仍然报 ①",
      any("D" in p and "悬空" in p for p in check_citations("\\parencite{D}", _BIB_A)))
check("（对照）0 引用的 ② 提示仍在",
      any("0 引用" in p for p in check_citations("正文没有任何引用命令", _BIB_A)))

print("── ⑤ 批测试 citations_closure 同口径（spec §2.1 验收例）──")
_cl = batch.citations_closure("%\\cite{C}\n" + "\\cite{A}", _BIB_A)
check("`%\\cite{C}` + `\\cite{A}` → n_cited=1、dangling=[]",
      _cl["n_cited"] == 1 and _cl["dangling"] == [], repr(_cl))
_cl2 = batch.citations_closure("\\cite{A} % \\cite{B}\n\\cite{C2}", _BIB_A)
check("行中注释 cite 不计入闭合（C2 仍算真悬空）",
      _cl2["n_cited"] == 2 and _cl2["dangling"] == ["C2"], repr(_cl2))

print("── ⑥ sources.extract_bibkeys_from_body（convert/build 告警路径）──")
check("整行注释键不提取", sources.extract_bibkeys_from_body("%\\parencite{C}\n\\cite{A}") == {"A"})
check("行中注释键不提取",
      sources.extract_bibkeys_from_body("\\cite{A} % \\cite{B}\n\\textcite{C}") == {"A", "C"})
check("`\\%` 转义后 cite 保留",
      sources.extract_bibkeys_from_body("100\\% 完成\\parencite{A}\n\\footcite{B}") == {"A", "B"})
check("build.py 与 sources 的抽键是同一函数（告警随修复同源消失）",
      build.extract_bibkeys_from_body is sources.extract_bibkeys_from_body)

print()
print("=" * 78)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
