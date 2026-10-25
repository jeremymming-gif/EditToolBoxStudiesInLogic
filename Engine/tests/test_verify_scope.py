#!/usr/bin/env python3.14
"""参考文献核实「成本闸门」（第 128 条，2026-10-18）的离线判据测试。

背景（期 87 子案例 1 实测）：源 `.bib` **377 条**、正文只引用 **31 个**唯一键，但
《文献核实任务书》+ 检索 + AI 把 **376 条**全核了 —— 检索 418 次请求（≈¥1.845）、
AI 376 次调用 / 2,538,740 prompt tokens；而且模型 `deepseek-flash` 忽略 `web_search`，
**0 条真实检索证据**。本套件守住的正是这一轮修的东西：

  (a) 范围判定的纯函数（`refmark.resolve_verify_scope` / `cited_keys_from_body`）：
      只核引用键 / `\\nocite{*}` = 全量 / `--verify-all-refs` 显式全量 / 解析不出引用键
      时**退回全量 + 警告** / 超上限标记 / `--verify-max 0` = 不限；
  (b) 任务书按 `cite_keys` 过滤：只列引用键、头部写明「本次核实范围」、离线统计也只算
      范围内的条目；**默认调用（cite_keys=None）逐字节不变**（向后兼容）；
  (c) 模型能力前置提示：已知忽略 `web_search` 的模型命中、未列入的模型**不猜**；
  (d) `convert.py` 端到端（合成 LaTeX 源；**不联网、不依赖稿件库**）：
      只核引用键 / `\\nocite{*}` 全量 / 超上限退出 2（拦在生成任务书与调 AI 之前）/
      `--verify-all-refs` 越过闸门 / `--verify-plan` 只算账退出 0 / 默认路径产物逐字节不变；
  (e) Word 通道端到端（合成最小 docx）：引用键来自**产物 .tex**（`docxsource` 没有对外
      暴露引用键集合），未引用条目不进任务书；
  (f) 本地 mock LLM：只对范围内条目调 AI；已知忽略 web_search 的模型在 **AI 批量开始前**
      打警告，且事后「0 个检索证据」的提示仍然保留。

不写 `out/`、不联网、不跑 corpus.py / build.sh。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_verify_scope.py
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import aibib, llm, refmark  # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ══ (a) 范围判定纯函数 ═════════════════════════════════════════════════════
print("── (a) 范围判定（cited_keys_from_body / resolve_verify_scope）──")

k, nocite = refmark.cited_keys_from_body(
    r"见\parencite{A2020,B2019} 与 \textcite[第 3 页]{C2021}")
check("(a) 普通 cite 命令（含可选参数、多键）都抽出", k == {"A2020", "B2019", "C2021"}, repr(k))
check("(a) 普通 cite 不是全量", nocite is False)
k, nocite = refmark.cited_keys_from_body(r"\nocite{*}")
check("(a) `\\nocite{*}` → 全量标记、键集为空", k == set() and nocite is True, repr((k, nocite)))
k, nocite = refmark.cited_keys_from_body(r"\nocite{A2020}\parencite{B2019}")
check("(a) `\\nocite{键}` 与普通 cite 同口径（不是全量）",
      k == {"A2020", "B2019"} and nocite is False, repr((k, nocite)))
check("(a) 空正文 → 空键集、非全量", refmark.cited_keys_from_body("") == (set(), False))

BIB_KEYS = ["A2020", "B2019", "C2018", "D2017"]
s = refmark.resolve_verify_scope(BIB_KEYS, {"A2020", "C2018"})
check("(a) 默认（正文引用键在 bib 里存在）→ 只核这些", s.mode == refmark.MODE_CITED
      and s.n_bib == 4 and s.n_cited == 2 and s.n_scope == 2 and s.skipped == 2, repr(s))
check("(a) scope_keys 就是引用键 ∩ bib 键；未引用键不在范围",
      s.scope_keys == frozenset({"A2020", "C2018"}) and s.keeps("A2020") and not s.keeps("B2019"))
check("(a) 正常路径的量化行 = 需求原话", refmark.scope_log_line(s) ==
      "源 bib 4 条；正文引用 2 条 → 本次核实 2 条（跳过 2 条未引用）",
      repr(refmark.scope_log_line(s)))

s = refmark.resolve_verify_scope(BIB_KEYS, {"A2020"}, verify_max=0)
check("(a) `--verify-max 0` = 不限（1 条也不会被标记超限）",
      s.n_scope == 1 and s.over_limit is False, repr(s))

s = refmark.resolve_verify_scope({"A", "B", "C"}, {"A", "B", "C"}, verify_max=2)
check("(a) 引用条数 > 上限 → over_limit", s.over_limit is True, repr(s))
s = refmark.resolve_verify_scope({"A", "B", "C"}, {"A", "B", "C"}, verify_max=3)
check("(a) 正好等于上限 → 放行（上限含等号）", s.over_limit is False)

s = refmark.resolve_verify_scope(BIB_KEYS, set(), nocite_all=True)
check("(a) `\\nocite{*}` → 全量（n_cited 也按全量报）",
      s.mode == refmark.MODE_NOCITE and s.n_scope == 4 and s.n_cited == 4
      and s.scope_keys is None and s.over_limit is False, repr(s))
s = refmark.resolve_verify_scope(BIB_KEYS, set(), nocite_all=True, verify_max=2)
check("(a) `\\nocite{*}` 仍受上限闸门约束（不是显式承认）", s.over_limit is True, repr(s))

s = refmark.resolve_verify_scope(BIB_KEYS, {"A2020"}, explicit_all=True, verify_max=1)
check("(a) `--verify-all-refs` = 显式全量，**越过**闸门",
      s.mode == refmark.MODE_ALL_REFS and s.n_scope == 4 and s.over_limit is False
      and s.explicit_all is True, repr(s))

s = refmark.resolve_verify_scope({"A", "B"}, set())
check("(a) 正文没有引用键 → 退回全量（边界：不许静默一条都不核）",
      s.mode == refmark.MODE_FALLBACK and s.n_scope == 2 and s.scope_keys is None
      and s.over_limit is False, repr(s))
check("(a) fallback 的量化行写明「退回全量」", "退回全量" in refmark.scope_log_line(s))
s = refmark.resolve_verify_scope({"A", "B"}, {"Z2020"})
check("(a) 引用的键一个都不在 bib 里 → 同样退回全量 + 记下缺失键",
      s.mode == refmark.MODE_FALLBACK and s.cited_missing == ("Z2020",)
      and s.n_cited_missing == 1 and s.n_cited == 0, repr(s))
s = refmark.resolve_verify_scope({"A", "B"}, {"A", "Z2020"})
check("(a) 部分缺失：只核存在的那个键，缺失键单独报",
      s.mode == refmark.MODE_CITED and s.n_scope == 1
      and s.n_cited_missing == 1 and s.cited_missing == ("Z2020",), repr(s))
s = refmark.resolve_verify_scope(set(), {"A2020"})
check("(a) bib 一条都没有 → 什么都不核（emptied，不误报超限）",
      s.mode == refmark.MODE_EMPTY and s.n_scope == 0 and s.over_limit is False, repr(s))
try:
    refmark.resolve_verify_scope({"A"}, {"A"}, verify_max=-1)
    _neg = False
except ValueError:
    _neg = True
check("(a) 负上限 → ValueError（CLI 层会先拦）", _neg)

s = refmark.resolve_verify_scope({"A", "B", "C"}, {"A"})
guide = refmark.limit_guidance(s, search_enabled=True)
check("(a) 超限文案给三条路（调上限 / 显式全量 / 先看账）",
      "--verify-max" in guide and "--verify-all-refs" in guide
      and "--verify-plan" in guide and "三条路" in guide, guide[:200])
check("(a) 超限文案带预计检索次数与费用（开了 --search-refs 时）",
      "≤ 2 次查询" in guide and "¥0.01" in guide, guide[-160:])
check("(a) 未开检索时文案不假装会检索",
      "0 次" in refmark.limit_guidance(s, search_enabled=False))

# ══ (b) 任务书按 cite_keys 过滤（默认逐字节不变）═══════════════════════════
print("\n── (b) 任务书过滤与向后兼容 ──")
BIB = """\
@article{A2020,
  author  = {Alpha, A.},
  title   = {A Paper},
  journal = {Journal},
  volume  = {1},
  number  = {2},
  pages   = {1--2},
  year    = {2020}
}

@book{B2019,
  author = {Beta, B.},
  title  = {B Book},
  year   = {2019}
}
"""
md_old = refmark.build(BIB, "Some_Stem")
check("(b) 默认调用 == 显式 cite_keys=None（旧签名调用逐字节相同）",
      md_old == refmark.build(BIB, "Some_Stem", cite_keys=None))
check("(b) 默认（全量）没有「本次核实范围」行", "本次核实范围" not in md_old)
check("(b) 默认（全量）统计全部条目：2 条、必补 2 项（B2019 缺 publisher/address）",
      "- 条目总数：**2**" in md_old and "必补字段 **2** 项" in md_old)

md_scope = refmark.build(BIB, "Some_Stem", cite_keys={"A2020"})
check("(b) 只列范围内的条目（未引用的 B2019 不出现）",
      "### `A2020`" in md_scope and "### `B2019`" not in md_scope)
check("(b) 条目总数 = 范围内条数（不是源 bib 条数）",
      "- 条目总数：**1**" in md_scope and "- 条目总数：**2**" not in md_scope)
check("(b) 头部写明范围与跳过条数（数字现算，不许手抄）",
      "本次核实范围" in md_scope and "只核**正文引用到的 1 条**" in md_scope
      and "源 bib 共 2 条" in md_scope and "跳过 1 条" in md_scope, md_scope.split("\n")[8])
check("(b) 离线统计也只算范围内条目（B2019 的缺字段不再算进来）",
      "必补字段 **0** 项" in md_scope and "必补字段 **2** 项" not in md_scope)
PACK = {"verdict": "evidence", "score": 0.9, "queries": ["q"],
        "top": [{"title": "B Book", "url": "https://example.org/b"}]}
md_ev = refmark.build(BIB, "Some_Stem", cite_keys={"A2020"}, evidence={"B2019": PACK})
check("(b) 范围外的证据也不渲染（P1 证据按条目渲染，条目已被过滤）",
      "https://example.org/b" not in md_ev and "🔎 **自动检索证据**" not in md_ev)

# 第 128 条：合并记录里「范围外」≠「AI 未返回」——不分开写，346 条未引用条目会被
# 误读成 346 次 AI 失败。
_REPLY_A = """@article{A2020,
  author  = {Alpha, A.},
  title   = {A Paper},
  journal = {Journal},
  volume  = {1},
  number  = {2},
  pages   = {1--2},
  year    = {2020}
}"""
_result = aibib.merge(BIB, {"A2020": _REPLY_A})
_sec_plain = aibib.render_merge_section(_result, provenance="")
_sec_scope = aibib.render_merge_section(_result, provenance="", out_of_scope={"B2019"})
check("(b) 默认（不给 out_of_scope）：跳过清单照旧逐条列「AI 未返回」",
      "### 已跳过（1 条）" in _sec_plain and "AI 未返回这一条" in _sec_plain
      and "共 2 个键" in _sec_plain and "范围外" not in _sec_plain)
check("(b) 给了 out_of_scope：范围外条目不逐条列，改写成一行汇总",
      "### 已跳过（0 条）" in _sec_scope and "`B2019`" not in _sec_scope
      and "范围外" in _sec_scope and "另有 **1 条**" in _sec_scope
      and "共 1 个键" in _sec_scope)
check("(b) 范围外汇总说明「没有交给 AI 处理、逐字节未动」",
      "没有**交给 AI 处理" in _sec_scope or "没有**交给 AI 处理" in _sec_scope
      or "交给 AI 处理" in _sec_scope)
check("(b) 给空集合 == 不给（默认路径逐字节不变）",
      aibib.render_merge_section(_result, provenance="", out_of_scope=set())
      == _sec_plain)

# ══ (c) 模型能力前置提示 ═══════════════════════════════════════════════════
print("\n── (c) 模型能力前置提示（已知忽略 web_search 的模型）──")
check("(c) deepseek-flash 已知会忽略内置检索", llm.ignores_web_search("deepseek-flash"))
check("(c) deepseek-chat / 带版本后缀 / 带提供商前缀同样命中（包含匹配、大小写无关）",
      llm.ignores_web_search("DeepSeek-Chat")
      and llm.ignores_web_search("deepseek/deepseek-flash-2026-01"))
check("(c) deepseek-v4-pro 已知会真检索 → 不报",
      not llm.ignores_web_search("deepseek-v4-pro"))
check("(c) 未列入的模型一律不猜（空串 / 别家模型）",
      not llm.ignores_web_search("") and not llm.ignores_web_search("gpt-5")
      and not llm.ignores_web_search("mock-model"))
warn = llm.web_search_capability_warning("deepseek-flash")
check("(c) 警告点名模型、说明「静默忽略 / 0 真实检索证据」并给出出路",
      "deepseek-flash" in warn and "静默忽略" in warn
      and "web_search_call" in warn and "deepseek-v4-pro" in warn, warn[:160])
check("(c) 未列入的模型返回空串（不许吓唬人）",
      llm.web_search_capability_warning("deepseek-v4-pro") == ""
      and llm.web_search_capability_warning("mock-model") == "")

# ══ 公共夹具：合成 LaTeX 源 + bib、子进程跑 convert.py ════════════════════
TEMPLATES = os.path.join(_REPO, "编辑笔记", "模板文件")
HAVE_TEMPLATES = os.path.isdir(TEMPLATES)

TMP = tempfile.mkdtemp(prefix="slcn verify scope ")
# ⚠️ 源稿与产物**分开放**：`convert.py` 会扫描源稿所在目录（递归）判定「参考文献附件」，
#    产物目录里的 .tex 会被当成候选源，混在同级会让 Word 通道误合并出双份 bib
#    （2026-10-18 本套件初版实测：源 bib 3 条变成 6 条）。
_SRC = os.path.join(TMP, "src")
_WORD_SRC = os.path.join(TMP, "word")
_OUT = os.path.join(TMP, "outs")
for _d in (_SRC, _WORD_SRC, _OUT):
    os.makedirs(_d, exist_ok=True)
TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文引用\parencite{Quine1970}，以及\parencite[第 3 页]{Li2022}。

\printbibliography
\end{document}
"""
TEX_NOCITE = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
本文声明引用全部文献。
\nocite{*}
\printbibliography
\end{document}
"""
BIB = """\
@article{Quine1970,
  author  = {Quine, W. V. O.},
  title   = {Philosophy of Logic},
  journal = {Mind},
  volume  = {79},
  pages   = {1--20},
  year    = {1970}
}

@book{Li2022,
  author    = {李雷},
  title     = {逻辑例外论},
  publisher = {北京大学出版社},
  address   = {北京},
  year      = {2022},
  userf     = {cn}
}

@article{Uncited2001,
  author  = {Nobody, A.},
  title   = {Uncited Paper One},
  journal = {Journal of Irrelevance},
  volume  = {1},
  pages   = {1--2},
  year    = {2001}
}

@book{Uncited2002,
  author    = {Nobody, B.},
  title     = {Uncited Book Two},
  publisher = {Nobody Press},
  year      = {2002}
}
"""
_tex_path = os.path.join(_SRC, "main.tex")
_nocite_path = os.path.join(_SRC, "main_nocite.tex")
_bib_path = os.path.join(TMP, "refs.bib")
for _p, _txt in ((_tex_path, TEX), (_nocite_path, TEX_NOCITE), (_bib_path, BIB)):
    with open(_p, "w", encoding="utf-8") as _f:
        _f.write(_txt)

# 干净环境：外层 shell 里若恰好 export 了 SLCN_*，测的就不是默认口径了。
_ENV = {k: v for k, v in os.environ.items() if not k.startswith("SLCN_")}
_ENV["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
_ENV["PYTHONIOENCODING"] = "utf-8"


def _run(out_root, extra, source=_tex_path):
    # ⚠️ 第 129 条起 `convert.py --verify-refs` 会在默认引用库存在时**自动查库**；本套件量的
    #    是范围/成本闸门（与引用库无关），而本机一旦建过种子库，部分合成条目（如 Quine1970
    #    的《Philosophy of Logic》）会被安全命中 → AI 次数变少。显式 `--no-bibdb` 把引用库
    #    钉死为关，保证本套件在任何机器（建没建库）上判据一致。引用库接入另有
    #    `tests/test_bibdb_verify.py`。
    return subprocess.run(
        [sys.executable, "convert.py", "--source", source, "--bib", _bib_path,
         "--out", out_root, "--name", "Test_Author", "--author", "张三", "--lang", "zh",
         "--no-bibdb"]
        + extra,
        cwd=_ENGINE, env=_ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)


def _products(out_root):
    d = os.path.join(out_root, "Test_Author")
    tb = glob.glob(os.path.join(d, "*_文献核实.md"))
    return d, (tb[0] if tb else "")


def _read(path):
    return open(path, encoding="utf-8", errors="replace").read() if path else ""


# ══ (d) convert 端到端（LaTeX 通道）════════════════════════════════════════
print("\n── (d) convert 端到端（LaTeX 通道；合成源，不联网）──")
if not HAVE_TEMPLATES:
    SKIPS.append("模板目录不在本机（编辑笔记/模板文件），跳过 convert 端到端")
    print("SKIP  convert 端到端（模板目录不在本机）")
else:
    # 默认路径（无 --verify-refs）：不生成任务书，产物照旧
    p0 = os.path.join(_OUT, "p0")
    r0 = _run(p0, [])
    d0, tb0 = _products(p0)
    check("(d) 默认（无 --verify-refs）：rc=0、**不生成**任务书",
          r0.returncode == 0 and not tb0, f"rc={r0.returncode} tb={tb0}")
    check("(d) 默认：产物 tex/bib 齐全",
          os.path.exists(os.path.join(d0, "Test_Author.tex"))
          and os.path.exists(os.path.join(d0, "reference.bib")))

    # --verify-refs：只核正文引用到的 2 条
    p1 = os.path.join(_OUT, "p1")
    r1 = _run(p1, ["--verify-refs"])
    d1, tb1 = _products(p1)
    md1 = _read(tb1)
    check("(d) --verify-refs：rc=0、生成任务书", r1.returncode == 0 and bool(tb1),
          r1.stdout[-300:] if r1.returncode else "")
    check("(d) 跑前量化行 = 需求原话（源 bib 4 条；正文引用 2 条 → 本次核实 2 条，跳过 2 条）",
          "源 bib 4 条；正文引用 2 条 → 本次核实 2 条（跳过 2 条未引用）" in r1.stdout,
          r1.stdout[-500:])
    check("(d) 任务书只列引用到的 2 条，未引用条目（Uncited*）不出现",
          md1.count("### `") == 2 and "Quine1970" in md1 and "Li2022" in md1
          and "Uncited2001" not in md1 and "Uncited2002" not in md1)
    check("(d) 任务书头部写明「本次核实范围」与跳过条数",
          "本次核实范围" in md1 and "跳过 2 条" in md1 and "源 bib 共 4 条" in md1)
    check("(d) 默认路径产物逐字节不变：tex / bib 与无 flag 那次完全相同",
          open(os.path.join(d0, "Test_Author.tex"), "rb").read()
          == open(os.path.join(d1, "Test_Author.tex"), "rb").read()
          and open(os.path.join(d0, "reference.bib"), "rb").read()
          == open(os.path.join(d1, "reference.bib"), "rb").read())

    # \nocite{*} = 全量
    p2 = os.path.join(_OUT, "p2")
    r2 = _run(p2, ["--verify-refs"], source=_nocite_path)
    _d2, tb2 = _products(p2)
    md2 = _read(tb2)
    check("(d) \\nocite{*} → 全量核实（任务书 4 条）",
          r2.returncode == 0 and md2.count("### `") == 4
          and all(k in md2 for k in ("Quine1970", "Li2022",
                                    "Uncited2001", "Uncited2002")),
          f"rc={r2.returncode} n={md2.count('### `')}")
    check("(d) \\nocite{*} 的量化行写明了语义（不是悄悄全量）",
          "语义 = 引用全部" in r2.stdout and "本次核实 4 条（跳过 0 条）" in r2.stdout,
          r2.stdout[-400:])

    # 超上限：退出 2，拦在生成任务书/检索/AI 之前
    p3 = os.path.join(_OUT, "p3")
    r3 = _run(p3, ["--verify-refs", "--verify-max", "1"])
    _d3, tb3 = _products(p3)
    check("(d) 超上限（2 > 1）：退出码 2", r3.returncode == 2, f"rc={r3.returncode}")
    check("(d) 超上限：stderr 给出三条可操作的路",
          all(s in r3.stderr for s in ("--verify-max 2", "--verify-all-refs", "--verify-plan")),
          r3.stderr[-400:])
    check("(d) 超上限：不生成任务书（闸门在花钱之前）", not tb3)
    check("(d) 超上限：stdout 已有量化行（先看账，再报错）",
          "本次核实 2 条（跳过 2 条未引用）" in r3.stdout)
    check("(d) 超上限：没有调用 AI（无 LLM 配置也不假装跑过）",
          "已配置 LLM" not in r3.stdout)

    # --verify-all-refs：显式全量，越过闸门
    p4 = os.path.join(_OUT, "p4")
    r4 = _run(p4, ["--verify-refs", "--verify-all-refs", "--verify-max", "1"])
    _d4, tb4 = _products(p4)
    md4 = _read(tb4)
    check("(d) --verify-all-refs 越过上限：rc=0、任务书 4 条",
          r4.returncode == 0 and md4.count("### `") == 4, f"rc={r4.returncode}")
    check("(d) --verify-all-refs 的量化行写明「显式全量」",
          "explicit" not in r4.stdout and "显式全量" in r4.stdout, r4.stdout[-300:])

    # --verify-plan：只算账、退出 0、不生成任务书
    p5 = os.path.join(_OUT, "p5")
    r5 = _run(p5, ["--verify-plan"])
    d5, tb5 = _products(p5)
    check("(d) --verify-plan：退出码 0", r5.returncode == 0, r5.stdout[-300:])
    check("(d) --verify-plan：打印条数 / 上限 / 预计检索 / 预计 AI",
          all(s in r5.stdout for s in ("核实计划", "源 bib 4 条；正文引用 2 条",
                                       "条数上限", "预计检索", "预计 AI")),
          r5.stdout[-500:])
    check("(d) --verify-plan：不生成任务书（只算账）", not tb5)
    check("(d) --verify-plan：转换产物照常写出（不是 --dry-run）",
          os.path.exists(os.path.join(d5, "Test_Author.tex")))

    # --verify-plan 在「会超限」时也只算账、退出 0（提示实际执行会被拦下，但不是错误）
    p5b = os.path.join(_OUT, "p5b")
    r5b = _run(p5b, ["--verify-plan", "--verify-max", "1"])
    _d5b, tb5b = _products(p5b)
    check("(d) --verify-plan 超上限也只算账、退出 0（提示会被闸门拦下）",
          r5b.returncode == 0 and "会被闸门拦下" in r5b.stdout and not tb5b,
          r5b.stdout[-300:])

    # 负上限是用法错误：argparse 层就退出 2（不开始转换、更不花钱）
    rbad = _run(os.path.join(_OUT, "pbad"), ["--verify-refs", "--verify-max", "-1"])
    check("(d) 负上限 → 用法错误退出 2",
          rbad.returncode == 2 and "--verify-max" in rbad.stderr, rbad.stderr[-200:])

# ══ (e) Word 通道：引用键以产物 .tex 为准 ═══════════════════════════════════
print("\n── (e) Word 通道端到端（合成最小 docx）──")
if not HAVE_TEMPLATES:
    print("SKIP  Word 端到端（模板目录不在本机）")
else:
    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _paras = ["论参考文献核实范围", "张三", "摘要：测试。", "关键词：测试"]
    _paras += [f"正文第{i}段，讨论逻辑与论证。" for i in range(12)]
    _paras += ["最近的研究（林小雨 2021）讨论了极小主义，另有（李雷 2022）论及逻辑例外论。"]
    _paras += [
        "参考文献",
        "林小雨. 2021. 极小主义视域下的真谓词[J]. 逻辑学研究, 2021(4): 27-36.",
        "李雷. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50.",
        "郑建国. 2014. 逻辑悖论研究引论[M]. 北京: 人民出版社.",
    ]
    _ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                  for t in _paras)
    _docx = os.path.join(_WORD_SRC, "demo.docx")
    with zipfile.ZipFile(_docx, "w", zipfile.ZIP_DEFLATED) as _z:
        _z.writestr("word/document.xml",
                    f'<w:document xmlns:w="{W_NS}"><w:body>{_ps}</w:body></w:document>')
    pw = os.path.join(_OUT, "pw")
    rw = subprocess.run(
        [sys.executable, "convert.py", "--source", _docx, "--out", pw,
         "--name", "Word_Author", "--author", "张三", "--lang", "zh", "--verify-refs",
         "--no-bibdb"],
        cwd=_ENGINE, env=_ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)
    _tbw = glob.glob(os.path.join(pw, "Word_Author", "*_文献核实.md"))
    mdw = _read(_tbw[0] if _tbw else "")
    check("(e) Word：rc=0、生成任务书", rw.returncode == 0 and bool(_tbw), rw.stdout[-300:])
    check("(e) Word：量化行 = 源 bib 3 条 / 正文引用 2 条 / 跳 1 条",
          "源 bib 3 条；正文引用 2 条 → 本次核实 2 条（跳过 1 条未引用）" in rw.stdout,
          rw.stdout[-500:])
    check("(e) Word：任务书只列正文引用到的 2 条（未引用的郑建国 2014 不进清单）",
          mdw.count("### `") == 2 and "逻辑悖论研究引论" not in mdw)

# ══ (f) 本地 mock LLM：AI 批量范围 + 模型能力前置提示 ══════════════════════
print("\n── (f) mock LLM：只核范围内条目 + 模型能力前置提示（不联网）──")


class MockLLM:
    """本地假模型端点：同时认 chat（/chat/completions）与 responses（/responses）。

    回复内容无关紧要（本组量的是**调用次数/顺序/提示词**）；只把请求原文记下来。
    responses 形状的回复里**故意不给** `web_search_call` —— 这样「事后检索证据 0」
    的现有提示必须照旧出现（本任务要求保留它）。
    """

    def __init__(self):
        self.requests = []   # [(path, 请求体 dict), ...]
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append((self.path, body))
                text = json.dumps(body, ensure_ascii=False)
                m = re.search(r"@\w+\{([^,\s]+),", text)
                key = m.group(1) if m else "K2020"
                reply = (f"@article{{{key},\n  author = {{作者}},\n  title = {{题名}},\n"
                         "  journal = {测试期刊},\n  year = {2020}\n}")
                if self.path.endswith("/chat/completions"):
                    payload = {"choices": [{"message": {"content": reply}}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
                else:
                    payload = {"status": "completed",
                               "output": [{"type": "message", "role": "assistant",
                                           "content": [{"type": "output_text",
                                                        "text": reply}]}],
                               "usage": {"input_tokens": 1, "output_tokens": 1}}
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def ai_requests(self):
        return [b for p, b in self.requests
                if p.endswith("/chat/completions") or p.endswith("/responses")]

    def close(self):
        self.srv.shutdown()


if not HAVE_TEMPLATES:
    print("SKIP  mock LLM 端到端（模板目录不在本机）")
else:
    # (f1) chat 通道：只对范围内的 2 条调 AI
    m1 = MockLLM()
    try:
        p6 = os.path.join(_OUT, "p6")
        r6 = _run(p6, ["--verify-refs", "--no-ai-merge",
                       "--llm-base", m1.base, "--llm-key", "k-test",
                       "--llm-model", "mock-chat"])
        reqs = m1.ai_requests()
        joined = "\n".join(json.dumps(b, ensure_ascii=False) for b in reqs)
        check("(f) chat：只对范围内条目调 AI（2 次，不是源 bib 的 4 次）",
              r6.returncode == 0 and len(reqs) == 2, f"rc={r6.returncode} n={len(reqs)}")
        check("(f) chat：逐条提示词里没有未引用条目的键",
              "Quine1970" in joined and "Li2022" in joined
              and "Uncited2001" not in joined and "Uncited2002" not in joined)
        check("(f) chat：日志里的逐条进度是 [1/2] / [2/2]",
              "[1/2]" in r6.stdout and "[2/2]" in r6.stdout and "[3/" not in r6.stdout,
              r6.stdout[-400:])
    finally:
        m1.close()

    # (f2) responses + 联网检索 + 已知会忽略 web_search 的模型 → 批量开始**前**警告
    m2 = MockLLM()
    try:
        p7 = os.path.join(_OUT, "p7")
        r7 = _run(p7, ["--verify-refs", "--no-ai-merge",
                       "--llm-protocol", "responses", "--llm-web-search",
                       "--llm-base", m2.base, "--llm-key", "k-test",
                       "--llm-model", "deepseek-flash"])
        out7 = r7.stdout
        i_warn = out7.find("模型能力前置提示")
        i_first = out7.find("[1/")
        check("(f) 已知忽略 web_search 的模型：在 **AI 批量开始前** 打醒目警告",
              r7.returncode == 0 and i_warn >= 0 and i_first >= 0 and i_warn < i_first,
              f"warn={i_warn} first={i_first}")
        check("(f) 前置警告点名模型、说明后果、并给出可操作出路",
              "deepseek-flash" in out7 and "静默忽略" in out7
              and "web_search_call" in out7 and "deepseek-v4-pro" in out7)
        check("(f) 事后提示**保留**：收到 0 个 web_search_call → 按凭记忆对待",
              "本次没有任何检索证据" in out7 and "凭记忆" in out7, out7[-500:])
        check("(f) 检索通道开了也只对范围内 2 条调 AI",
              len(m2.ai_requests()) == 2, f"n={len(m2.ai_requests())}")
    finally:
        m2.close()

    # (f3) 未列入的模型：不猜、不打能力警告
    m3 = MockLLM()
    try:
        p8 = os.path.join(_OUT, "p8")
        r8 = _run(p8, ["--verify-refs", "--no-ai-merge",
                       "--llm-protocol", "responses", "--llm-web-search",
                       "--llm-base", m3.base, "--llm-key", "k-test",
                       "--llm-model", "some-other-model"])
        check("(f) 未列入的模型：不打模型能力警告（只对已知能力边界出声）",
              r8.returncode == 0 and "模型能力前置提示" not in r8.stdout,
              r8.stdout[-300:])
    finally:
        m3.close()

    # (f4) 合并记录：范围外条目不被写成「AI 未返回」（第 128 条端到端）
    m4 = MockLLM()
    try:
        p9 = os.path.join(_OUT, "p9")
        r9 = _run(p9, ["--verify-refs",
                       "--llm-base", m4.base, "--llm-key", "k-test",
                       "--llm-model", "mock-chat"])
        _d9, tb9 = _products(p9)
        md9 = _read(tb9)
        check("(f) 合并记录把「范围外」与「AI 未返回」分开（未引用条目不污染记录）",
              r9.returncode == 0 and "六、合并记录" in md9
              and "范围外" in md9 and "另有 **2 条**" in md9
              and "AI 未返回这一条" not in md9,
              md9[-400:] if md9 else r9.stdout[-300:])
    finally:
        m4.close()

# ── 清理 ──────────────────────────────────────────────────────────────────
shutil.rmtree(TMP, ignore_errors=True)

print()
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("失败 0 项（全部通过）")
