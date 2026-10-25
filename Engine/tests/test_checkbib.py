#!/usr/bin/env python3.14
"""退回版 .bib 核对（`checkbib.py`）的判据测试。

四组断言：
  ① 解析/健壮性：真实 bib 全条可解析；空文件、坏条目、孤立 `@`、引号不配平、
     奇数截断模糊测试——**任何输入都要出报告，不许 traceback**。
  ② 规范核对：真实 bib 的干净样本（0 问题 → 退出码 0）；缺 `userf`、`doi` 回流、
     页码单短横、期号前导零、月份写数字、手写反例。
  ③ 引用闭合：真实 bib+tex 无悬空引用；把 bib 里一个被引用的 key 改名 → 悬空引用。
  ④ 与 --original 的 diff：key 改名、整条删除、字段被删、类型被改；未改动条目只汇总。
  ⑤ 约束：`checkbib.py` 只 import 离线模块（AST 扫 import，不许出现 slcn.llm/网络库）。
  ⑥ 粘贴模式与列表模式（phase 2c）：`--returned-stdin` 的体检结果与 `--returned <同一份文件>`
     **同结论同退出码**（报告只差「退回文件」那一行）；空 stdin / 两个来源同给 = 用法错误；
     GB18030 的 stdin 照旧有编码提示；`--list-keys` 只打印一行、**永不写文件**。

不写 out/、不联网、不跑 corpus.py / build.sh。语料 fixture 的 sha256 会被打印出来；
语料若被别的会话改动，测试会用 WARN 提示，并尽量按模式定位而不写死内容。

用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_checkbib.py
"""

import ast
import contextlib
import glob
import hashlib
import io
import os
import random
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import checkbib  # noqa: E402
from slcn import bibcheck, bibnorm  # noqa: E402

CORPUS = os.path.join(_ENGINE, "out", "corpus")
CHECKBIB_PY = os.path.join(_ENGINE, "checkbib.py")

# 本套件依赖的语料文件（读了 shasum 便于别的会话改动后对账；不符只 WARN）
FIXTURE_SHA256 = {
    "2026_08_Ran_Ren/reference.bib": "3fc0debccf906551ae18a7d80154ff31101de5c737e8031227a859e9557d3eca",
    "2026_09_Runcheng_Liang/reference.bib": "21abef5e3ea8d115bebac9c152fdf2af9ca43eec64ba2bc770c4476e486145b2",
}

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def read(path):
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="replace")


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ── 语料挑选（动态：语料被改动也不会让测试全崩）────────────────────────────


def _bib_entries(text):
    return checkbib.parse_entries(text)[0]


def real_pair(need_cites=True, min_entries=3, require_closed=True):
    """找一份「有 .tex 的真实 reference.bib」。返回 (bib_path, tex_path)。"""
    if not os.path.isdir(CORPUS):
        return None, None
    for d in sorted(os.listdir(CORPUS)):
        bib = os.path.join(CORPUS, d, "reference.bib")
        if not os.path.isfile(bib):
            continue
        texs = sorted(glob.glob(os.path.join(CORPUS, d, "*.tex")))
        if not texs:
            continue
        text = read(bib)
        if len(_bib_entries(text)) < min_entries:
            continue
        if need_cites:
            ck = bibcheck.cited_keys(read(texs[0]))
            if not ck:
                continue
            if require_closed and not ck <= bibcheck.entry_keys(text):
                continue
        return bib, texs[0]
    return None, None


def clean_real_bib(min_entries=3):
    """找一份**零问题**的真实 bib（现为 2026_08_Ran_Ren：15 条、0 问题）。"""
    if not os.path.isdir(CORPUS):
        return None
    best = None
    for d in sorted(os.listdir(CORPUS)):
        bib = os.path.join(CORPUS, d, "reference.bib")
        if not os.path.isfile(bib):
            continue
        rep = checkbib.analyze(read(bib))
        if rep.n_problem == 0 and rep.total >= min_entries:
            if best is None or rep.total > best[1]:
                best = (bib, rep.total)
    return best[0] if best else None


def cjk_bib():
    """找一份含中文条目（有 userf）的真实 bib。"""
    if not os.path.isdir(CORPUS):
        return None
    for d in sorted(os.listdir(CORPUS), reverse=True):
        bib = os.path.join(CORPUS, d, "reference.bib")
        if not os.path.isfile(bib):
            continue
        text = read(bib)
        if "userf" in text and re.search(r"[\u4e00-\u9fff]", text):
            return bib
    return None


# ── CLI / 结构助手 ────────────────────────────────────────────────────────


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = checkbib.main(list(argv))
    except SystemExit as e:  # argparse 用法错误：main 之外的退出路径
        code = e.code if isinstance(e.code, int) else 2
    return code, out.getvalue(), err.getvalue()


class _StdinStub:
    """假 stdin：`checkbib._read_stdin()` 只读 `sys.stdin.buffer`（原始字节）。"""

    def __init__(self, data: bytes):
        self.buffer = io.BytesIO(data)


def run_cli_stdin(stdin_text, *argv, encoding="utf-8"):
    """把 `stdin_text` 当作**标准输入**跑 CLI（`--returned-stdin` 的等价物）。"""
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdin
    sys.stdin = _StdinStub(stdin_text.encode(encoding))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = checkbib.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    finally:
        sys.stdin = old
    return code, out.getvalue(), err.getvalue()


def problems(rep, needle=None, key=None):
    return [
        f
        for f in rep.findings
        if f.level == checkbib.LEVEL_PROBLEM
        and (needle is None or needle in f.detail)
        and (key is None or f.key == key)
    ]


def infos(rep, needle=None, key=None):
    return [
        f
        for f in rep.findings
        if f.level == checkbib.LEVEL_INFO
        and (needle is None or needle in f.detail)
        and (key is None or f.key == key)
    ]


def entry_span(text, key):
    """返回条目 `key` 在文本里的 [start, end)（含结尾花括号），找不到 None。"""
    m = re.search(r"@\w+\s*\{\s*" + re.escape(key) + r"\s*,", text)
    if not m:
        return None
    i = text.index("{", m.start())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return m.start(), j + 1
        j += 1
    return m.start(), len(text)


def write(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


_MISSING = object()


def drop_field(text, key, field):
    """删掉 `key` 这条里的 `field = {…}`（含行尾逗号）。找不到返回 None。"""
    span = entry_span(text, key)
    if not span:
        return None
    head, body, tail = text[: span[0]], text[span[0] : span[1]], text[span[1] :]
    m = re.search(
        r"[ \t]*" + re.escape(field) + r"[ \t]*=[ \t]*\{[^{}]*\}[ \t]*,?[ \t]*\n?", body
    )
    if not m:
        return None
    return head + body[: m.start()] + body[m.end() :] + tail


# ── ① 解析 / 健壮性 ───────────────────────────────────────────────────────

print("══ ① 解析与健壮性 ══")

bib, tex = real_pair()
if bib is None:
    skip("真实 bib+tex", "本机 out/corpus 里没有同时有 reference.bib 与 .tex 的稿件")
else:
    print(f"      语料 fixture：{os.path.relpath(bib, _ENGINE)}  "
          f"sha256={sha256(bib)[:16]}…")
    btext, ttext = read(bib), read(tex)
    rep = checkbib.analyze(btext, tex_text=ttext, returned_path=bib, tex_path=tex)
    syn = [f for f in rep.findings if f.section == "syntax" and f.level == checkbib.LEVEL_PROBLEM]
    check(
        "真实 bib 全部条目可解析（无语法问题）",
        not syn,
        f"{os.path.relpath(bib, _ENGINE)}：{rep.total} 条，语法问题 {len(syn)}",
    )
    check(
        "解析出的条目数 = bibcheck.split_entries 的条目数",
        len(_bib_entries(btext)) == len(bibcheck.split_entries(btext)),
        f"{len(_bib_entries(btext))} vs {len(bibcheck.split_entries(btext))}",
    )

# 空文件
with tempfile.TemporaryDirectory() as tmp:
    empty = write(tmp, "empty.bib", "")
    code, out, _ = run_cli("--returned", empty)
    check("空文件 → 退出码 1", code == 1, f"code={code}")
    check("空文件 → 报告说「空的」", "空的" in out, out.splitlines()[2] if len(out.splitlines()) > 2 else "")
    check("空文件 → 结论行在", "**结论：" in out)

    # 只有注释、没有条目
    connly = write(tmp, "comments.bib", "% 退回文件\n% ⚠ 待核实：什么都没有\n")
    code2, out2, _ = run_cli("--returned", connly)
    check("只有注释 → 退出码 1 且报「没有解析到任何条目」", code2 == 1 and "没有解析到任何条目" in out2)

# 坏条目：每种坏法单独一个文件（花括号不配平的条目会吞掉它后面的内容）
GOOD = """\
@article{Good2026,
  author = {张三},
  title  = {正常条目},
  journal = {某刊},
  volume = {1},
  number = {2},
  pages  = {1--10},
  year   = {2026},
  userf  = {cn}
}
"""
BROKEN_CASES = {
    "花括号不配平": GOOD + "\n@article{Broken2026,\n  author = {李四},\n  title  = {这条永远不闭合,\n",
    "key 后面没有逗号": GOOD + "\n@book{nocomma\n  title = {key 后面没有逗号}\n}\n",
    "不是合法条目头": GOOD + "\n@ @@ not-an-entry @\n",
    # 真实语料里的写法（out/corpus/2026_04_Zuojun_Xiong 等：`url` 被误替换成了数字）
    "字段名不合法": GOOD + "\n@article{BadField2026,\n  title = {字段名是数字},\n"
    "  1     = {https://doi.org/10.1017/S1755020325100981}\n}\n",
    "引号没有闭合": GOOD
    + '\n@misc{Quote2026,\n  author = {王五},\n  title  = {引号没有闭合},\n'
      '  note   = "这里开了引号却没关},\n  year   = {2026}\n}\n',
}
with tempfile.TemporaryDirectory() as tmp:
    code, out, _ = run_cli("--returned", write(tmp, "good.bib", GOOD))
    check("基准：只有一条正常条目 → 退出码 0", code == 0, f"code={code}")
    check("基准：正常条目仍被解析出来", any(e.key == "Good2026" for e in checkbib.parse_entries(GOOD)[0]))
    for why, text in BROKEN_CASES.items():
        p = write(tmp, "broken.bib", text)
        code, out, _ = run_cli("--returned", p)
        check(f"坏条目（{why}）→ 退出码 1 且不抛异常", code == 1, f"code={code}")
        hint = next((l.strip() for l in out.splitlines() if why in l), "")[:100]
        check(f"坏条目（{why}）→ 报告点名该坏法", why in out, hint)
        check(f"坏条目（{why}）→ 附录仍有逐条状态表", "## 附录：逐条状态" in out)

# 解析器回归（真实语料里踩过的两个坑）
REGRESSION = """\
% JabRef 会在文件末尾写 @Comment{jabref-meta: …}：不是文献条目，不许报语法问题
@Comment{jabref-meta: databaseType:bibtex;}

@article{WithQuotes2019,
  author  = {J. Author},
  title   = {Referee Reports on {F}itch's "A Definition of Value"},
  journal = {J. Logic},
  volume  = {5},
  number  = {1},
  pages   = {1--10},
  year    = {2019}
}

@article{Brace{2019,
  author  = {X. Author},
  title   = {这条的 author 里少一个花括号: Alchourr\\'{o}n, C. and G{\\},
  journal = {J. Logic},
  year    = {1985}
}

@book{After2019,
  author    = {Y. Author},
  title     = {坏条目后面的条目仍要被核对},
  publisher = {某社},
  address   = {北京},
  year      = {2019}
}
"""
with tempfile.TemporaryDirectory() as tmp:
    p = write(tmp, "regression.bib", REGRESSION)
    rep = checkbib.analyze(REGRESSION, returned_path=p)
    keys = [e.key for e in rep.entries]
    check(
        "@Comment{jabref-meta: …} 不报语法问题（当结构性块跳过）",
        not problems(rep, "@Comment") and not any("jabref-meta" in f.detail and f.level == "problem" for f in rep.findings),
        f"keys={keys[:4]}",
    )
    check("非文献块跳过有汇总提示", len(infos(rep, "非文献块")) == 1)
    check("花括号值里的引号不影响解析（{F}itch's \"A Definition…\"）", "WithQuotes2019" in keys)
    check("坏条目不吞掉后面的条目（跳过去继续核对）", "After2019" in keys, f"keys={keys}")
    check("坏条目 → 花括号不配平是问题级", len(problems(rep, "花括号不配平")) >= 1)

    # 全库不变量：只要没有「花括号不配平」，我的条目数就该和 bibcheck.split_entries 一致
    if os.path.isdir(CORPUS):
        n_files = n_bad = 0
        for d in sorted(os.listdir(CORPUS)):
            bp = os.path.join(CORPUS, d, "reference.bib")
            if not os.path.isfile(bp):
                continue
            text = read(bp)
            ents, syn = checkbib.parse_entries(text)
            n_files += 1
            unbalanced = any("花括号不配平" in f.detail for f in syn)
            if len(ents) != len(bibcheck.split_entries(text)) and not unbalanced:
                n_bad += 1
                print(f"      MISMATCH {d}: mine={len(ents)} bibcheck={len(bibcheck.split_entries(text))}")
        check(f"全库 {n_files} 份真实 bib：条目数与 bibcheck.split_entries 一致", n_bad == 0, f"不一致 {n_bad} 份")


# 重复键
DUP = """\
@article{Same2019,
  author = {A. Author}, title = {T1}, journal = {J}, volume = {1}, number = {1},
  pages = {1--2}, year = {2019}
}
@article{Same2019,
  author = {B. Author}, title = {T2}, journal = {J}, volume = {2}, number = {2},
  pages = {3--4}, year = {2020}
}
"""
with tempfile.TemporaryDirectory() as tmp:
    d = write(tmp, "dup.bib", DUP)
    rep = checkbib.analyze(DUP, returned_path=d)
    check("重复引用键 → 问题级", len(problems(rep, "引用键重复")) == 1, str(problems(rep, "引用键重复")))

# 模糊测试：真实 bib 任意截断 / 随机破坏，都不许抛异常
if bib is not None:
    rnd = random.Random(20261014)
    cuts = list(range(0, len(btext), max(1, len(btext) // 40)))
    cuts += [rnd.randrange(len(btext) + 1) for _ in range(20)]
    bad = 0
    for i in cuts:
        frag = btext[:i] + rnd.choice(["", "}", "{", '"', "@", "\\"])
        try:
            r = checkbib.analyze(frag, btext, ttext)
            if not isinstance(checkbib.render(r), str):
                bad += 1
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"      截断点 {i} 抛异常：{type(e).__name__}: {e}")
    check(f"截断/破坏模糊测试（{len(cuts)} 个变体）不抛异常", bad == 0, f"异常 {bad} 个")


# ── ② 规范核对 ───────────────────────────────────────────────────────────

print("\n══ ② 编辑部规范 ══")

cb = clean_real_bib()
if cb is None:
    skip("干净真实 bib", "本机 corpus 里没有 0 问题的 reference.bib")
else:
    print(f"      语料 fixture：{os.path.relpath(cb, _ENGINE)}  sha256={sha256(cb)[:16]}…")
    if os.path.basename(os.path.dirname(cb)) in FIXTURE_SHA256:
        want = FIXTURE_SHA256[os.path.basename(os.path.dirname(cb)) + "/reference.bib"]
        if sha256(cb) != want:
            print("      WARN  该语料已与记录不符（别的会话改过），测试按模式继续")
    rep = checkbib.analyze(read(cb), returned_path=cb)
    code, out, _ = run_cli("--returned", cb)
    check("干净真实 bib → 0 处问题", rep.n_problem == 0, f"{rep.total} 条 / 问题 {rep.n_problem} / 提示 {rep.n_info}")
    check("干净真实 bib → 退出码 0", code == 0, f"code={code}")
    check("干净真实 bib → 结论行写 0 处问题", "**结论：0 处问题" in out and "✅" in out)

# 缺 userf（在真实中文 bib 上删掉 userf 行）
cjk = cjk_bib()
if cjk is None:
    skip("缺 userf", "找不到含中文条目的真实 bib")
else:
    ctext = read(cjk)
    stripped = re.sub(r"(?m)^[ \t]*userf[ \t]*=[ \t]*\{cn\}[ \t]*,?[ \t]*\n?", "", ctext)
    n_removed = len(re.findall(r"userf[ \t]*=[ \t]*\{cn\}", ctext))
    with tempfile.TemporaryDirectory() as tmp:
        p = write(tmp, "no_userf.bib", stripped)
        rep = checkbib.analyze(stripped, returned_path=p)
        hits = problems(rep, "userf = {cn}")
        check(
            "中文条目缺 userf = {cn} → 问题级",
            len(hits) >= 1 and len(hits) == n_removed,
            f"删了 {n_removed} 处 userf，报出 {len(hits)} 处问题（{os.path.relpath(cjk, _ENGINE)}）",
        )
        check("缺 userf 的键都被点名", all(h.key for h in hits), str([h.key for h in hits][:3]))
        code, _, _ = run_cli("--returned", p)
        check("缺 userf → 退出码 1", code == 1, f"code={code}")

# doi 回流（在干净真实 bib 的第一条里塞一个 doi）
if cb is not None:
    ctext = read(cb)
    first = _bib_entries(ctext)[0]
    span = entry_span(ctext, first.key)
    with_doi = ctext[: span[0]] + ctext[span[0] :].replace(
        f"@{first.etype}{{{first.key},",
        f"@{first.etype}{{{first.key},\n  doi   = {{10.1000/xyz123}},",
        1,
    )
    check("测试夹具：能往真实条目里塞回 doi", "doi" in with_doi and with_doi != ctext, first.key)
    with tempfile.TemporaryDirectory() as tmp:
        p = write(tmp, "with_doi.bib", with_doi)
        rep = checkbib.analyze(with_doi, returned_path=p)
        hits = problems(rep, "不该写进 bib", key=first.key)
        check("doi 回流 → 问题级并点名该键", len(hits) == 1, f"key={first.key} hits={len(hits)}")
        code, out, _ = run_cli("--returned", p)
        check("doi 回流 → 退出码 1", code == 1, f"code={code}")

# 手写反例：页码单短横 / 期号前导零 / 月份写数字 / 缺 userf
BAD = """\
@article{Bad2026,
  author  = {张三},
  title   = {字段写法全错的样例},
  journal = {逻辑学研究},
  volume  = {03},
  number  = {03},
  pages   = {1-26},
  month   = {11},
  doi     = {10.1000/abc},
  year    = {2026}
}

@article{BadEn2026,
  author  = {J. P. Dillard, C. Segrin and J. M. Harden},
  title   = {English entry with userf},
  journal = {Communication Monographs},
  volume  = {56},
  number  = {1},
  pages   = {19--38},
  year    = {1989},
  userf   = {cn}
}
"""
with tempfile.TemporaryDirectory() as tmp:
    p = write(tmp, "bad_style.bib", BAD)
    rep = checkbib.analyze(BAD, returned_path=p)
    check("页码单短横 → 问题级（复用 bibnorm 规则）", len(problems(rep, "页码写法不对", key="Bad2026")) == 1)
    check("期号前导零 → 问题级", len(problems(rep, "期号有前导零", key="Bad2026")) == 1)
    check("卷号前导零 → 提示级", len(infos(rep, "卷号有前导零", key="Bad2026")) == 1)
    check("月份写数字 → 问题级（复用 bibnorm._MONTHS）", len(problems(rep, "月份应写三字母缩写", key="Bad2026")) == 1)
    check("doi 回流 → 问题级", len(problems(rep, "不该写进 bib", key="Bad2026")) == 1)
    check("中文条目缺 userf → 问题级", len(problems(rep, "userf = {cn}", key="Bad2026")) == 1)
    check("纯英文条目填了 userf → 提示级", len(infos(rep, "userf", key="BadEn2026")) == 1)
    check(
        "作者逗号+and 混用 → 提示级（任务书第一节的错名案例）",
        len(infos(rep, "逗号与 and 混用", key="BadEn2026")) == 1,
    )
    code, _, _ = run_cli("--returned", p)
    check("手写反例 → 退出码 1", code == 1, f"code={code}")

# `% ⚠ 待核实` 注释：缺字段但有注释 → 提示（不是问题）
PENDING = """\
% ⚠ 待核实：volume（官网只有在线优先版）
@article{Pending2026,
  author  = {张三},
  title   = {待核实注释样例},
  journal = {逻辑学研究},
  number  = {3},
  pages   = {1--26},
  year    = {2026},
  userf   = {cn}
}
"""
with tempfile.TemporaryDirectory() as tmp:
    p = write(tmp, "pending.bib", PENDING)
    rep = checkbib.analyze(PENDING, returned_path=p)
    probs = problems(rep, key="Pending2026")
    check("缺字段但上方有「待核实」注释 → 只报提示，不报问题", not probs, str([f.detail for f in probs]))
    check("缺字段且有「待核实」注释 → 提示里说明符合任务书", len(infos(rep, "已按任务书第三节第 6 条注明", key="Pending2026")) == 1)
    check("待核实注释本身算提示", rep.n_info >= 1, f"提示 {rep.n_info} 处")

# ── ②b 中文译著体例：不能被「多作者用 `and` 分隔」的建议毁掉（2026-10-15，第 64 条）──
#
# 真实成品形态：`{阿斯海姆（著），郑建国、万林（译）}`（《排校手册》源码的实例是
# `焦循（著），沈文倬（点校）`）。那里的**中文逗号与顿号是姓名的一部分**，
# 不是多作者分隔符；按普通中文多作者的口径去建议，会把它改成
# `{阿斯海姆（著） and 郑建国 and 万林（译）}` —— 著/译标记当场毁掉。
print("\n── ②b 中文著/译体例：豁免 `and` 建议 + 按体例校验 ──")

TR_MARKED = ("@book{A2014,\n  author = {阿斯海姆（著），郑建国、万林（译）},\n"
             "  title = {指称与意向性},\n  publisher = {南京大学出版社},\n"
             "  address = {南京},\n  year = {2014},\n  userf = {cn}\n}\n")
TR_PLAIN = ("@book{X,\n  author = {刘淘宁，顿新国},\n  title = {x},\n"
            "  publisher = {P},\n  address = {A},\n  year = {2000},\n  userf = {cn}\n}\n")
TR_AND = ("@book{W,\n  author = {沃斯等（著） and 吴静怡（译）},\n  title = {x},\n"
          "  publisher = {P},\n  address = {A},\n  year = {2000},\n  userf = {cn}\n}\n")
TR_ED_AND = ("@book{G,\n  author = {古文献研究室（编） and 某某},\n  title = {x},\n"
             "  publisher = {P},\n  address = {A},\n  year = {2000},\n  userf = {cn}\n}\n")

rep_tr = checkbib.analyze(TR_MARKED, returned_path="trans.bib")
check("②b 译著体例（（著），（译））：**不再**提示改成 `and`",
      not infos(rep_tr, "多作者用 `and` 分隔", key="A2014"),
      str([f.detail for f in infos(rep_tr, key="A2014")]))
check("②b 译著体例值里的中文逗号不是多作者分隔符（判据函数）",
      bibcheck.author_has_role_mark("阿斯海姆（著），郑建国、万林（译）")
      and bibcheck.author_has_role_mark("焦循（著），沈文倬（点校）")
      and bibcheck.author_has_role_mark("古文献研究室（编）")
      and not bibcheck.author_has_role_mark("刘淘宁，顿新国"))

rep_pl = checkbib.analyze(TR_PLAIN, returned_path="plain.bib")
check("②b 反例：普通中文多作者**仍然**提示改成 `and`（豁免不能过宽）",
      len(infos(rep_pl, "多作者用 `and` 分隔", key="X")) == 1,
      str([f.detail for f in infos(rep_pl, key="X")]))

rep_and = checkbib.analyze(TR_AND, returned_path="trans_and.bib")
check("②b 误用 `and` 的译著 → 按体例报「不该用 and」（而不是建议改成 and）",
      len(infos(rep_and, "中文著/译体例不该用 `and` 分隔", key="W")) == 1
      and not infos(rep_and, "多作者用 `and` 分隔", key="W"),
      str([f.detail for f in infos(rep_and, key="W")]))

rep_ed = checkbib.analyze(TR_ED_AND, returned_path="ed_and.bib")
check("②b `（编）` 同样受豁免、同样按体例校验",
      len(infos(rep_ed, "中文著/译体例不该用 `and` 分隔", key="G")) == 1
      and not infos(rep_ed, "多作者用 `and` 分隔", key="G"),
      str([f.detail for f in infos(rep_ed, key="G")]))

# 反例：译著体例的提示**只是提示**，不挡退出码（与第 63 条同一条纪律）
with tempfile.TemporaryDirectory() as tmp:
    p = write(tmp, "trans_and.bib", TR_AND)
    code, _, _ = run_cli("--returned", p)
    check("②b 译著体例问题 → 只提示（退出码 0）", code == 0, f"code={code}")


# 结构：结论 + 分节 + 附录
if cb is not None:
    md = checkbib.render(checkbib.analyze(read(cb), returned_path=cb))
    check(
        "报告结构：结论 / 四节标题 / 附录",
        all(
            s in md
            for s in (
                "**结论：",
                "## 一、可解析性（语法）",
                "## 二、编辑部规范",
                "## 附录：逐条状态",
                "| 键 | 类型 | 问题 | 提示 | 与原件 |",
            )
        ),
    )


# ── ③ 引用键闭合 ─────────────────────────────────────────────────────────

print("\n══ ③ 引用键闭合（--tex）══")

if bib is None:
    skip("引用闭合", "没有真实 bib+tex 语料")
else:
    rep = checkbib.analyze(btext, tex_text=ttext, returned_path=bib, tex_path=tex)
    cited = rep.cited
    check("真实语料：正文引用命令被识别出来", len(cited) >= 3, f"引用键 {len(cited)} 个")
    check("真实语料：该 bib 对正文没有悬空引用（原状）", not problems(rep, "悬空引用"), str(problems(rep, "悬空引用"))[:1])
    check("cite 命令计数 > 0", rep.n_cite_cmds > 0, f"{rep.n_cite_cmds} 条 cite 命令")

    old = sorted(cited)[0]
    renamed = btext.replace("{" + old + ",", "{" + old + "RENAMED,", 1)
    with tempfile.TemporaryDirectory() as tmp:
        p = write(tmp, "renamed.bib", renamed)
        rep2 = checkbib.analyze(renamed, tex_text=ttext, returned_path=p, tex_path=tex)
        hits = problems(rep2, "悬空引用")
        check("被引用的 key 改名 → 悬空引用（问题级）", len(hits) == 1 and hits[0].key == old, f"{old}：{len(hits)} 处")
        check("悬空引用 → 退出码 1", run_cli("--returned", p, "--tex", tex)[0] == 1)
        check(
            "未被引用的键 → 提示级",
            any(f.key.endswith("RENAMED") for f in infos(rep2, "正文从未引用")),
            f"{old}RENAMED",
        )

    # 正文里没有 cite 命令的稿子 → 提示一句，不报问题
    with tempfile.TemporaryDirectory() as tmp:
        p = write(tmp, "nocites.tex", "\\documentclass{article}\\begin{document}没有引用\\end{document}")
        rep3 = checkbib.analyze(btext, tex_text=read(p), returned_path=bib, tex_path=p)
        check("正文无 cite 命令 → 只提示不报问题", not problems(rep3, "悬空引用") and len(infos(rep3, "没有找到任何")) == 1)


# ── ④ 与 --original 的 diff ──────────────────────────────────────────────

print("\n══ ④ 与 --original 的 diff ══")

if bib is None:
    skip("diff", "没有真实 bib 语料")
else:
    entries = _bib_entries(btext)
    keys = [e.key for e in entries]
    rename_key = None
    for k in keys:
        if k in bibcheck.cited_keys(ttext):
            rename_key = k
            break
    rename_key = rename_key or keys[0]
    victim_key = next((e.key for e in entries if "pages" in e.fields), None)
    drop_key = next((k for k in keys if k not in (victim_key, rename_key)), None)

    mutated = btext.replace("{" + rename_key + ",", "{" + rename_key + "_v2,", 1)
    dropped = drop_field(mutated, victim_key, "pages") if victim_key else None
    check("测试夹具：能从真实条目里删掉 pages 字段", dropped is not None, f"victim={victim_key}")
    if dropped:
        mutated = dropped
    sp = entry_span(mutated, drop_key) if drop_key else None
    check("测试夹具：能整条删掉一个条目", sp is not None, f"drop={drop_key}")
    if sp:
        mutated = mutated[: sp[0]] + mutated[sp[1] :]

    with tempfile.TemporaryDirectory() as tmp:
        orig = os.path.join(tmp, "original.bib")
        ret = os.path.join(tmp, "returned.bib")
        with open(orig, "w", encoding="utf-8") as fh:
            fh.write(btext)
        with open(ret, "w", encoding="utf-8") as fh:
            fh.write(mutated)

        code, out, _ = run_cli("--returned", ret, "--original", orig)
        check("diff 用例 → 退出码 1", code == 1, f"code={code}")
        check("diff：key 改名 → 问题级", f"疑似改了 key：`{rename_key}` → `{rename_key}_v2`" in out)
        check("diff：整条删除 → 问题级", f"条目被删除：原件有 `{drop_key}`" in out)
        check("diff：字段被删 → 问题级", f"字段被删掉：`pages`" in out)
        check("diff：未改动条目只给一行汇总", "未改动的条目" in out and "条：" in out)
        check("diff：附录里有「与原件」列", "| 与原件 |" in out)

        # 类型被改
        first = entries[0]
        newtype = "book" if first.etype.lower() != "book" else "misc"
        sp = entry_span(btext, first.key)
        typed = btext[: sp[0]] + btext[sp[0] : sp[1]].replace(f"@{first.etype}{{", f"@{newtype}{{", 1) + btext[sp[1] :]
        t_ret = write(tmp, "typed.bib", typed)
        rep = checkbib.analyze(typed, btext, returned_path=t_ret, original_path=orig)
        hits = problems(rep, "条目类型被改", key=first.key)
        check("diff：类型被改 → 问题级", len(hits) == 1, f"@{first.etype} → @{newtype}（{first.key}）")
        # 但若条目上方写了 `% 类型已改` 注释 → 降为提示
        typed2 = typed.replace(f"@{newtype}{{{first.key},", f"% 类型已改：{first.etype} → {newtype}（理由）\n@{newtype}{{{first.key},", 1)
        rep2 = checkbib.analyze(typed2, btext, returned_path=t_ret, original_path=orig)
        check(
            "diff：类型已改但有 % 类型已改 注释 → 降为提示",
            not problems(rep2, "条目类型") and len(infos(rep2, "类型已改", key=first.key)) >= 1,
        )

        # 字段值改动 → 提示级，不是问题级（另起一个只改值的 original→returned 对）
        old_val = entries[0].fields.get("title", "") or entries[0].fields.get("author", "")
        changed_txt = btext.replace(old_val, old_val + "X", 1) if old_val else btext
        check("测试夹具：能改掉一个字段值", bool(old_val) and changed_txt != btext, entries[0].key)
        c_ret = write(tmp, "changed.bib", changed_txt)
        rep3 = checkbib.analyze(changed_txt, btext, returned_path=c_ret, original_path=orig)
        check(
            "diff：字段值改动 → 提示级（不是问题级）",
            len(infos(rep3, "字段值有改动")) >= 1 and not problems(rep3, "字段值有改动"),
            f"{entries[0].key}",
        )

        # doi 被删掉是「正确」而不是问题
        if cb is not None:
            ctext2 = read(cb)
            f0 = _bib_entries(ctext2)[0]
            sp = entry_span(ctext2, f0.key)
            with_doi = ctext2[: sp[0]] + ctext2[sp[0] :].replace(
                f"@{f0.etype}{{{f0.key},", f"@{f0.etype}{{{f0.key},\n  doi = {{10.1000/xyz}},", 1
            )
            rep4 = checkbib.analyze(ctext2, with_doi, returned_path=ret, original_path=orig)
            check(
                "diff：doi 被去掉 → 提示「正确」而不是问题",
                not problems(rep4, "doi") and len(infos(rep4, "去掉 `doi`", key=f0.key)) == 1,
            )

    # 原件本身坏掉时，diff 也不许崩
    with tempfile.TemporaryDirectory() as tmp:
        bad_orig = write(tmp, "bad_orig.bib", btext[: len(btext) // 2] + "\n@article{tail,\n title = {x")
        code, out, _ = run_cli("--returned", write(tmp, "ok.bib", btext), "--original", bad_orig)
        check("原件损坏 → 仍然出报告（不 traceback）", code in (0, 1) and "**结论：" in out, f"code={code}")


# ── ⑤ 约束：纯离线 ───────────────────────────────────────────────────────

print("\n══ ⑤ 约束（纯离线 / 只读）══")

src = read(CHECKBIB_PY)
tree = ast.parse(src)
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
FORBIDDEN = {"slcn.llm", "requests", "urllib", "urllib.request", "http", "http.client", "socket", "openai", "corpus", "slcn.corpus"}
bad_imports = sorted(i for i in imported if i in FORBIDDEN)
check("checkbib.py 不 import 任何联网/模型/语料模块", not bad_imports, f"违规：{bad_imports}" if bad_imports else f"imports={sorted(imported)}")
ALLOWED_ROOTS = {"argparse", "os", "re", "sys", "dataclasses", "__future__", "slcn"}
# 2026-10-15 起允许的 slcn 子模块：解析器搬去了 `slcn/bibparse.py`、核对与报告搬去了
# `slcn/bibreport.py`（两者都要被 `convert.py` 复用——同一份报告不能有两份实现）。
# 这是**有意放宽**，所以同一条纪律必须**跟着新模块走**：下面紧接着对这两个模块
# 各扫一遍 import 与网络字样，禁止表一字不改（「核实文献真伪是编辑的事」）。
# 只比到「子模块名」这一层：`from slcn.bibparse import Entry` 会被 AST 展成
# `slcn.bibparse.Entry`，逐字面比会误判。
# 2026-10-15 phase 2b 又加了 `aibib`：`checkbib.py --merge-into`（手动「并回」）必须走
# 与自动通道**同一个**合并核心，不许在 CLI 里再写一份合并。同一条纪律照旧跟着它走
# ——下面的 ⑤b 扫网络字样时 `aibib` 也在名单里。
# 2026-10-15（第 87 条）再加 `platform`：Windows 版要求 CLI 进 main() 先切 UTF-8 标准输出，
# 而 `slcn/platform.py` **只 import 标准库**（os / platform / shutil / sys / pathlib），
# 没有任何联网或模型调用 —— 所以它照样要过下面 ⑤b 的网络字样扫描。
ALLOWED_SLCN = {"bibcheck", "bibnorm", "bibparse", "bibreport", "aibib", "platform"}
bad_roots = sorted({i.split(".")[0] for i in imported} - ALLOWED_ROOTS)
bad_slcn = sorted(i for i in imported
                  if i.startswith("slcn.") and i.split(".")[1] not in ALLOWED_SLCN)
check(
    "只 import 标准库 + slcn.bibcheck / slcn.bibnorm / slcn.bibparse / slcn.bibreport / slcn.aibib / slcn.platform",
    not bad_roots and not bad_slcn,
    f"越界：{bad_roots + bad_slcn}" if (bad_roots or bad_slcn) else f"{sorted(imported)}",
)
check("slcn.llm 没被拉进内存", "slcn.llm" not in sys.modules)
check("源码里没有网络调用字样（urlopen/post/get）", not re.search(r"urlopen|requests\.|\.post\(|\.get\(['\"]http", src))

# ⑤b 新拆出去的两个模块也必须是纯离线的（否则「放宽允许清单」就成了后门）。
# `aibib` 是 2026-10-15 phase 2b 加进来的（并回的合并核心），同样要过这一关。
for _mod in ("bibparse", "bibreport", "aibib", "platform"):
    _s = read(os.path.join(_ENGINE, "slcn", f"{_mod}.py"))
    _t = ast.parse(_s)
    _imps = set()
    for _n in ast.walk(_t):
        if isinstance(_n, ast.Import):
            _imps.update(a.name for a in _n.names)
        elif isinstance(_n, ast.ImportFrom):
            if _n.module:
                _imps.add(_n.module)
                _imps.update(f"{_n.module}.{a.name}" for a in _n.names)
    _bad = sorted(i for i in _imps if i in FORBIDDEN)
    check(f"slcn/{_mod}.py 也是纯离线（不联网 / 不调模型）", not _bad,
          f"违规：{_bad}" if _bad else f"imports={sorted(_imps)}")
    check(f"slcn/{_mod}.py 没有网络调用字样",
          not re.search(r"urlopen|requests\.|\.post\(|\.get\(['\"]http", _s))

# ⑤c 报告只有**一份**实现：`checkbib.py` 与 `convert.py` 必须走同一个入口
from slcn import bibreport as _bibreport  # noqa: E402

check("checkbib.py 的报告函数就是 slcn/bibreport.build_report（同一份实现）",
      checkbib.build_report is _bibreport.build_report
      and checkbib.render is _bibreport.render)
check("build_report 的默认结论与 checkbib CLI 一致（同一份口径）",
      _bibreport.build_report_and_conclusion("")[1] == checkbib.conclusion_line(checkbib.analyze("")))


# GB18030 编码的退回文件：给出编码提示，不崩
if cjk is not None:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "gbk.bib")
        with open(p, "wb") as fh:
            fh.write(read(cjk).encode("gb18030"))
        code, out, _ = run_cli("--returned", p)
        check("GB18030 退回文件 → 提示编码且不崩", code in (0, 1) and "gb18030" in out, f"code={code}")

# 退出码 2：文件不存在 / 目录 / 缺参数
with tempfile.TemporaryDirectory() as tmp:
    code, _, err = run_cli("--returned", os.path.join(tmp, "nope.bib"))
    check("退回文件不存在 → 退出码 2", code == 2, f"code={code} err={err.strip()[:60]}")
    code, _, err = run_cli("--returned", tmp)
    check("退回路径是目录 → 退出码 2", code == 2, f"code={code}")
    if cb is not None:
        code, _, err = run_cli("--original", cb)
        check("缺 --returned → 退出码 2（argparse）", code == 2, f"code={code}")
        code, _, err = run_cli("--returned", cb, "--tex", os.path.join(tmp, "nope.tex"))
        check("--tex 不存在 → 退出码 2", code == 2, f"code={code}")

# --out 写文件（临时目录），stdout 只给一行结论
if cb is not None:
    with tempfile.TemporaryDirectory() as tmp:
        outp = os.path.join(tmp, "报告.md")
        code, out, _ = run_cli("--returned", cb, "--out", outp)
        body = read(outp) if os.path.exists(outp) else ""
        check("--out 写出报告文件", os.path.isfile(outp) and "**结论：" in body and "## 附录：逐条状态" in body)
        check("--out 时 stdout 只给一行结论", "结论：" in out and len(out.strip().splitlines()) == 1, repr(out[:60]))
        check("--out 时退出码仍按问题数给", code == 0, f"code={code}")

# ── ⑥ 粘贴模式（`--returned-stdin`）与列表模式（`--list-keys`）phase 2c ─────

print("\n══ ⑥ 粘贴模式 / 列表模式 ══")

# 自带的合成 bib：不依赖 out/corpus，任何机器上都能跑「stdin == 文件」的对照。
PASTE_BIB = """\
@article{Ayer1946,
  author  = {Ayer, A. J.},
  title   = {A Probe},
  journal = {Mind},
  volume  = {55},
  number  = {217},
  pages   = {1--10},
  year    = {1946}
}

@book{Burge1984,
  author    = {Burge, T.},
  title     = {Probe Two},
  publisher = {Some Press},
  address   = {Oxford},
  year      = {1984}
}

@misc{Garbage2099,
  title   = {No Author Here},
  year    = {2099}
}
"""


def conclusion_of(text):
    for line in text.splitlines():
        if line.startswith("**结论："):
            return line
    return ""


def without_returned_path(text):
    """去掉报告头那一行「退回文件：…」——文件模式与 stdin 模式只有这一行该不同。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("- 退回文件："))


with tempfile.TemporaryDirectory() as tmp:
    pb = write(tmp, "paste.bib", PASTE_BIB)
    out_f, out_s = os.path.join(tmp, "f.md"), os.path.join(tmp, "s.md")
    code_f, stdout_f, _ = run_cli("--returned", pb, "--out", out_f)
    code_s, stdout_s, _ = run_cli_stdin(PASTE_BIB, "--returned-stdin", "--out", out_s)
    check("⑥ 同一段文本：stdin 体检与 `--returned <文件>` **退出码相同**",
          code_f == code_s, f"file={code_f} stdin={code_s}")
    check("⑥ 同一段文本：**结论行相同**（判据一条没变）",
          conclusion_of(read(out_f)) == conclusion_of(read(out_s)) != "",
          conclusion_of(read(out_s)))
    check("⑥ 报告正文除「退回文件」那一行外**逐字节相同**",
          without_returned_path(read(out_f)) == without_returned_path(read(out_s)))
    check("⑥ stdin 模式的报告头写「(stdin/字符串)」而不是一个假文件名",
          "(stdin/字符串)" in read(out_s),
          "｜".join(ln for ln in read(out_s).splitlines() if ln.startswith("- 退回文件")))

    # 不给 --out 时报告整份打到 stdout，结论与文件模式相同
    code2, stdout2, _ = run_cli_stdin(PASTE_BIB, "--returned-stdin")
    code_p, stdout_p, _ = run_cli("--returned", pb)
    check("⑥ 不给 --out → 报告打到 stdout（与文件模式同结论、除报告头外逐字节相同）",
          code2 == code_p and conclusion_of(stdout2) == conclusion_of(stdout_p) != ""
          and without_returned_path(stdout2) == without_returned_path(stdout_p))

    # --original 在 stdin 模式下照旧做字段级 diff
    orig = write(tmp, "orig.bib", PASTE_BIB.replace("  title   = {A Probe},", "  title   = {A Probe Changed},"))
    code3, stdout3, _ = run_cli_stdin(PASTE_BIB, "--returned-stdin", "--original", orig)
    check("⑥ stdin + --original 照旧做字段级 diff（改 key/删字段仍报问题级）",
          code3 == 1 and "字段值有改动" in stdout3, f"code={code3}")

# 空 stdin / 互斥 / 编码：三条都是「用法错误或提示」，绝不许静默出绿灯
with tempfile.TemporaryDirectory() as tmp:
    outp = os.path.join(tmp, "x.md")
    code, out, err = run_cli_stdin(" \n\t\n", "--returned-stdin", "--out", outp)
    check("⑥ 空 stdin → 退出码 2 + 中文用法错误，且**不写报告**",
          code == 2 and "--returned-stdin 收到的是空输入" in err and not os.path.exists(outp), f"code={code}")
    code, _, err = run_cli_stdin("", "--returned-stdin", "--list-keys")
    check("⑥ 空 stdin + --list-keys → 也是退出码 2（预览不能报「0 条」绿灯）",
          code == 2 and "--returned-stdin 收到的是空输入" in err, f"code={code}")
    code, _, err = run_cli_stdin(PASTE_BIB, "--returned-stdin", "--returned", os.path.join(tmp, "x.bib"))
    check("⑥ 两个来源同给 → 退出码 2（互斥）", code == 2, f"code={code}")

    code, out, _ = run_cli_stdin(PASTE_BIB, "--returned-stdin", "--list-keys", encoding="gb18030")
    check("⑥ GB18030 的 stdin：编码提示不进 stdout（预览必须恰好一行）",
          code == 0 and out.strip() == "3 条：Ayer1946, Burge1984, Garbage2099", repr(out))

with tempfile.TemporaryDirectory() as tmp:
    # GB18030 的 stdin（带中文）在**体检**模式下要留下编码提示，与读文件的降级口径一致
    gbk_text = "% ── 中文注释：粘贴来的 bib ──\n\n" + PASTE_BIB
    code, out, _ = run_cli_stdin(gbk_text, "--returned-stdin", encoding="gb18030")
    check("⑥ GB18030 的 stdin → 报告里留下编码提示（与读文件同一口径）",
          code in (0, 1) and "已按 gb18030 读入" in out, f"code={code}")

# --list-keys 的「永不写文件」：目录里一个新文件都不许出现
with tempfile.TemporaryDirectory() as tmp:
    pb = write(tmp, "paste.bib", PASTE_BIB)
    before = set(os.listdir(tmp))
    code, out, _ = run_cli("--returned", pb, "--list-keys", "--out", os.path.join(tmp, "never.md"))
    code2, out2, _ = run_cli_stdin(PASTE_BIB, "--returned-stdin", "--list-keys")
    check("⑥ --list-keys 两种来源输出同一行、退出码 0",
          code == 0 and code2 == 0 and out.strip() == out2.strip() == "3 条：Ayer1946, Burge1984, Garbage2099",
          repr(out.strip()))
    check("⑥ --list-keys **一个新文件都没写**（--out 也不生效）",
          set(os.listdir(tmp)) == before, f"{sorted(set(os.listdir(tmp)) - before)}")

# `--out` 也接受**已存在的目录**（2026-10-17 第十五批：与界面「产物目录 / 报告输出」对齐）
with tempfile.TemporaryDirectory() as tmp:
    pb = write(tmp, "paste.bib", PASTE_BIB)
    report_dir = os.path.join(tmp, "产物目录")
    os.makedirs(report_dir)
    code, out, _ = run_cli("--returned", pb, "--out", report_dir)
    produced = sorted(os.listdir(report_dir))
    check("--out 给**目录** → 报告按默认文件名放进该目录",
          produced == ["paste_回读校验报告.md"] and "**结论：" in read(os.path.join(report_dir, produced[0])),
          f"{produced}")
    check("--out 给目录时 stdout 报最终文件路径", "paste_回读校验报告.md" in out, repr(out[:100]))
    # 不存在的「目录样」路径仍按文件路径处理：照实报错（2），不静默换落点
    code_bad, _, err_bad = run_cli("--returned", pb, "--out", os.path.join(tmp, "不存在的目录", "x.md"))
    check("--out 父目录不存在 → 退出码 2 且报「写不了报告」（不静默）",
          code_bad == 2 and "写不了报告" in err_bad, f"code={code_bad}｜{err_bad[-80:]}")

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）") + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
