#!/usr/bin/env python3.14
r"""`slcn/docxsource.py` 的 **bib 键可复现性**判据（案例 74，2024_01_Min_Jiao 实测）。

缺陷
----
`make_key()` 末尾原来是：

    return f"ref{abs(hash(a)) % 10000}{e.year}"

CPython 对 `str` 的 `hash()` 受 **PYTHONHASHSEED 随机化**影响，每个进程都不同。实测同一篇
`2024年第1期(74)/07 Min_Jiao`，两个进程给出完全不同的 bib 键：

    进程 1：ref56162006, ref30821998, ref10261987, …
    进程 2：ref36242006, ref46911998, ref64241987, …

后果是正文里的 `\parencite{ref…}` 与 `reference.bib` 一起抖，产物不可复现，
「`.ai前` 与合并后逐字节比对」与 corpus 回归在这类稿子上永远失败。

根因（这才是真问题）
--------------------
那批西文文献的作者串以**不可见字符**开头：`'\ufeffS. Blackburn'`、`'\ufeffR. Audi'`。
`make_key()` 里判断「以 ASCII 字母开头」的 `re.match(r"^[A-Za-z]", a)` 因此失配，
本可以正常生成 `SBlackburn1998` 的条目只好走兜底键。

四组断言
--------
  ① 反例核心：**非兜底条目的键逐字不变**（`LiLei2005`、`Beall2006`、`Xing1984`、
     译名表 `Russell…`…）。判据是「改动前的分类器本来就认得这个作者串 →
     新键必须与旧键逐字相同」。
  ② `'\ufeffS. Blackburn'` 必须生成**稳定且可读**的键（`SBlackburn1998`，不是 `refNNNN`）。
  ③ 兜底键确定：用 `crc32`（纯函数），与进程/`PYTHONHASHSEED` 无关；且**不许**再用 `hash()`。
  ④ 跨进程：把同一段代码放进**两个独立子进程**（`PYTHONHASHSEED` 不同）跑，
     ①合成串的键集合、②同一份真实稿的键集合都必须完全相等。
     还带一条 `_dedupe_keys()` 的非回归：同年同作者仍加 a/b/c 后缀。

不联网、不写仓库、不跑 corpus.py / build.sh；临时脚本只写临时目录。
缺真实稿件时 `SKIP` 而不是失败（与 `test_coauthor_en.py` 的 ③ 组同一口径）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_bib_key_determinism.py
"""

import os
import re
import subprocess
import sys
import tempfile
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import docxsource  # noqa: E402
from slcn.docxsource import RefEntry  # noqa: E402
# B-207：`make_key/_dedupe_keys` 的定义已搬进 `slcn/wordentry.py`，`docxsource` 只是门面。
# 下面 ③ 的静态守卫必须读 **owning 模块** 源码（读 99 行门面会切空 → IndexError）。
from slcn import wordentry  # noqa: E402

FAILS = []
SKIPS = []

_ROOT = os.path.dirname(os.path.dirname(_ENGINE))          # 仓库根
JIAO = os.path.join(_ROOT, "2024年第1期(74)", "07 Min_Jiao")

# 案例 74 的现场：这些作者串**以不可见字符开头**（U+FEFF，ZWNBSP/BOM）
ZW = docxsource._ZERO_WIDTH


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


# ── 改动前的实现（逐字照抄，非回归用）────────────────────────────────────
def old_make_key(e, disambiguate=""):
    """**改动前**的 `make_key()`；分类部分与现在一字不差，只是兜底用 `hash()`。"""
    a = e.author.strip()
    for cn, en in docxsource.TRANSLATED_NAMES.items():
        if a.startswith(cn):
            return f"{en}{e.year}{disambiguate}"
    m = re.match(r"^([\u4e00-\u9fff]{2,4})", a)
    if m:
        name = m.group(1)
        try:
            from slcn.naming import chinese_to_folder

            folder = chinese_to_folder(name)
            g, s = folder.split("_", 1)
            return f"{s}{g}{e.year}{disambiguate}"
        except Exception:  # noqa: BLE001
            sur = docxsource.SURNAME_PINYIN.get(name[0], name[0])
            return f"{sur}{name[1:]}{e.year}{disambiguate}"
    if re.match(r"^[A-Za-z]", a):
        parts = re.split(r"\s+and\s+|&", a)
        names = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            w = re.match(r"([A-Za-z][A-Za-z'\-]+)", p)
            if w:
                names.append(w.group(1))
        stem = "".join(names[:2]) or re.sub(r"[^A-Za-z]", "", a)[:12]
        return f"{stem}{e.year}{disambiguate}"
    return f"ref{abs(hash(a)) % 10000}{e.year}"


def old_falls_back(author: str) -> bool:
    """改动前的 `make_key()` 对这个作者串会不会落到兜底键。"""
    a = author.strip()
    if any(a.startswith(cn) for cn in docxsource.TRANSLATED_NAMES):
        return False
    if re.match(r"^[\u4e00-\u9fff]{2,4}", a):
        return False
    if re.match(r"^[A-Za-z]", a):
        return False
    return True


def K(author, year):
    return docxsource.make_key(RefEntry(raw="", author=author, year=year))


# ── ① 反例核心：非兜底条目的键逐字不变 ───────────────────────────────────
print("── ① 非兜底键逐字不变（改动前能分类的作者串，新键必须与旧键完全相同）──")

# 真实作者串（含全库实测的正常条目 + 边界串）
_NORMAL = [
    ("李雷", "2005"), ("刘靖贤", "2013"), ("曾宁", "1984"),
    ("罗素", "1918"), ("克里普克", "1980"), ("蒯因", "1970"), ("哈瑞", "1982"),
    ("Beall, J. C. and Restall, G.", "2006"),
    ("Beall, J. C.", "2006"), ("Xing", "1984"), ("Haack, S.", "1974"),
    ("Quine, W V O", "1970"),
    ("S. Blackburn", "1984"), ("S. Blackburn", "1988"),      # 不带零宽的同名条目
    ("M. Schroeder", "2008b"), ("J. Woleński", "2010"),
    ("P. R. Cohen & H. J. Levesque", "1990"),
    ("S. Blackburn", "1998"),                                 # ★ 与零宽那条同年
]
_mismatch = [(a, y, old_make_key(RefEntry(raw="", author=a, year=y), ""), K(a, y))
             for a, y in _NORMAL
             if not old_falls_back(a)
             and old_make_key(RefEntry(raw="", author=a, year=y), "") != K(a, y)]
check("① 18 条正常作者串的键与改动前**逐字完全相同**", not _mismatch, f"{_mismatch}")
check("① 点名核对：`李雷`→LiLei2005、`Beall, J. C. and Restall, G.`→BeallRestall2006、"
      "`Xing`→Xing1984",
      K("李雷", "2005") == "LiLei2005"
      and K("Beall, J. C. and Restall, G.", "2006") == "BeallRestall2006"
      and K("Xing", "1984") == "Xing1984",
      f"{K('李雷', '2005')} / {K('Beall, J. C. and Restall, G.', '2006')} / {K('Xing', '1984')}")
check("① 译名表条目不变：`罗素`→Russell1918",
      K("罗素", "1918") == old_make_key(RefEntry(raw="", author="罗素", year="1918")) == "Russell1918", "")
check("① 作者串**中间**夹零宽也不改键（顺序是「先按原串判、判不出再剥零宽重判」）",
      K("Jo\ufeffhn Smith", "2005") == old_make_key(RefEntry(raw="", author="Jo\ufeffhn Smith", year="2005"))
      == "Jo2005"
      and K("J\ufeffohn Smith", "2005") == old_make_key(RefEntry(raw="", author="J\ufeffohn Smith", year="2005"))
      == "JohnSmith2005",
      f"Jo\\ufeffhn Smith → {K('Jo\ufeffhn Smith', '2005')}（先剥再判会变成 John2005）")
check("① 改动前会落到兜底的条目，这次**一定**要变（否则等于没修）",
      sum(1 for a, y in _NORMAL if old_falls_back(a)) == 0
      and old_falls_back("\ufeffS. Blackburn") and old_falls_back("[1]Barsalou, L"), "")

# ── ② 零宽作者串：稳定且可读 ─────────────────────────────────────────────
print("\n── ② `\\ufeff` 开头的作者串：走出兜底，生成稳定可读的键 ──")

_ZW_CASES = [
    ("\ufeffS. Blackburn", "1998", "SBlackburn1998"),
    ("\ufeffR. Audi", "2006", "RAudi2006"),
    ("\ufeffM. Bratman", "1987", "MBratman1987"),
    ("\ufeffP. R. Cohen & H. J. Levesque", "1990", "PRCohenHJLev1990"),
    ("\ufeffJ. Woleński", "2010", "JWoleski2010"),
    ("\ufeffM.Ridge", "2014", "MRidge2014"),
    ("\ufeffJ.Woods", "2017", "JWoods2017"),
    ("\ufeff李雷", "2005", "LiLei2005"),          # 中文串同理
]
for a, y, want in _ZW_CASES:
    got = K(a, y)
    check(f"② {a!r} → {want}（可读、不是 refNNNN）", got == want, f"{got}")
check("② 剥掉零宽后与不带零宽的同名条目**同键**（这才是「同一作者」的正确表现）",
      K("\ufeffS. Blackburn", "1984") == K("S. Blackburn", "1984") == "SBlackburn1984", "")

# ── ③ 兜底键：crc32，纯函数，不许 hash() ─────────────────────────────────
print("\n── ③ 兜底键用 crc32：纯函数、可独立复算、不许 hash() ──")

_FB = [("[1]Barsalou, L", "1987"), ("[25]Radford A", "2018"),
       ("[24] Scheffler, I", "1960"), ("[39] N. Weaver", "1675")]
for a, y in _FB:
    want = f"ref{zlib.crc32(a.encode('utf-8')):08x}{y}"
    got = K(a, y)
    check(f"③ {a!r} → {want}（= crc32 独立复算）", got == want, f"{got}")
check("③ 同一串连续算 100 次完全相同（纯函数）",
      len({K("[1]Barsalou, L", "1987") for _ in range(100)}) == 1, "")
_WORDENTRY_SRC = open(wordentry.__file__, encoding="utf-8").read()
check("③ 源码里不再用内置 `hash()` 生成键",
      "hash(a)" not in _WORDENTRY_SRC.split("def make_key")[1].split("def _dedupe_keys")[0],
      "")
check("③ 源码里有说明「为什么不能用 hash()」的注释",
      "PYTHONHASHSEED" in _WORDENTRY_SRC, "")

# ── ④ 跨进程：两个独立进程的键集合必须完全相等 ───────────────────────────
print("\n── ④ 跨进程（两个独立子进程，PYTHONHASHSEED 不同）──")

_CHILD = r'''
import os, re, sys
ENG = sys.argv[1]
sys.path.insert(0, ENG); sys.path.insert(0, os.path.join(ENG, "vendor"))
from slcn import docxsource
from slcn.docxsource import RefEntry
ZW = ["\ufeff", "\u200b", "\u200c", "\u200d"]
CASES = [("李雷", "2005"), ("Beall, J. C. and Restall, G.", "2006"), ("Xing", "1984"),
         ("\ufeffS. Blackburn", "1998"), ("\ufeffR. Audi", "2006"),
         ("\ufeffM. Schroeder", "2008b"), ("\ufeffJ.Woods", "2017"),
         ("[1]Barsalou, L", "1987"), ("[25]Radford A", "2018")]
print("S|" + "|".join(docxsource.make_key(RefEntry(raw="", author=a, year=y)) for a, y in CASES))
# 真实稿（用 sources.identify 选正文 docx，与 convert.py 同源）
d = sys.argv[2]
if os.path.isdir(d):
    from slcn import sources
    ss = sources.identify(d)
    if ss.docx:
        doc = docxsource.parse_docx(ss.docx)
        print("D|" + "|".join(e.key for e in doc.refs))
# _dedupe_keys 非回归
rs = [RefEntry(raw="", author="\ufeffS. Blackburn", year="1998"),
      RefEntry(raw="", author="S. Blackburn", year="1998")]
for e in rs:
    e.key = docxsource.make_key(e)
docxsource._dedupe_keys(rs)
print("Z|" + "|".join(e.key for e in rs))
'''

_tmp_ctx = tempfile.TemporaryDirectory()
_child_path = os.path.join(_tmp_ctx.name, "child_keys.py")
with open(_child_path, "w", encoding="utf-8") as fh:
    fh.write(_CHILD)


def child(seed, target):
    env = dict(os.environ, PYTHONHASHSEED=seed)
    r = subprocess.run([sys.executable, _child_path, _ENGINE, target],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"子进程失败（seed={seed}）：{r.stderr[-800:]}")
    return r.stdout


_out1 = child("0", JIAO)
_out2 = child("12345", JIAO)
_out3 = child("random", JIAO)
_lines1, _lines2, _lines3 = _out1.splitlines(), _out2.splitlines(), _out3.splitlines()
check("④ 合成串的键集合在 3 个独立进程（seed=0/12345/random）里完全相同",
      _lines1[0] == _lines2[0] == _lines3[0], f"{_lines1[0][:90]}…")
check("④ `\\ufeffS. Blackburn` 在子进程里也得到 SBlackburn1998（不是 refNNNN）",
      "SBlackburn1998" in _lines1[0], _lines1[0][:90])
check("④ 兜底键在子进程里是 crc32 形态（ref+8 位十六进制）",
      all(re.fullmatch(r"ref[0-9a-f]{8}\d{4}", k) for k in _lines1[0].split("|") if k.startswith("ref")),
      f"{[k for k in _lines1[0].split('|') if k.startswith('ref')]}")
# ⚠️ 取行**一律按前缀找，不许按下标**：子进程打印的行数取决于真实稿件在不在
#    （`D|` 那行只有找到 docx 时才打），按下标会越界或错位——
#    2026-10-15 拆仓预演时正是这样炸的（`_lines1[2]` → IndexError）。
def _line(lines, prefix):
    return next((ln for ln in lines if ln.startswith(prefix)), "")


check("④ `_dedupe_keys()` 仍然给同年同作者加 a/b 后缀（含零宽/非零宽的撞键）",
      _line(_lines1, "Z|") == "Z|SBlackburn1998a|SBlackburn1998b", _line(_lines1, "Z|"))

if not _line(_lines1, "D|"):
    skip("④ 真实稿跨进程键集合（Min_Jiao）", f"缺真实稿件（{JIAO}）")
else:
    _k1, _k2, _k3 = _line(_lines1, "D|")[2:], _line(_lines2, "D|")[2:], _line(_lines3, "D|")[2:]
    check("④ **同一个 docxsource.py 在两次独立进程里**对同一份真实稿产出的键集合完全相等",
          _k1 == _k2 == _k3, f"{len(_k1.split('|'))} 条键")
    _keys = _k1.split("|")
    check("④ Min_Jiao 的 16 条零宽文献全部走出兜底（键集合里不再有 ref 形态）",
          not [k for k in _keys if k.startswith("ref")], f"{[k for k in _keys if k.startswith('ref')]}")
    check("④ 该稿的键集合与人工成品要求一致：SBlackburn1998 在列",
          "SBlackburn1998" in _keys and "RAudi2006" in _keys, "")
    check("④ 该稿键数 38（与改动前条目数一致，一条没丢）", len(_keys) == 38, f"{len(_keys)}")

# 改动前的对照证据（同一子进程代码，换成改动前的 hash() 兜底也该抖）：
_OLD_CHILD = r'''
import os, re, sys
ENG = sys.argv[1]
sys.path.insert(0, ENG); sys.path.insert(0, os.path.join(ENG, "vendor"))
print("|".join("ref%d1998" % (abs(hash(a)) % 10000) for a in ["\ufeffS. Blackburn"] * 3))
'''
_old_path = os.path.join(_tmp_ctx.name, "child_old.py")
with open(_old_path, "w", encoding="utf-8") as fh:
    fh.write(_OLD_CHILD)


def old_child(seed):
    env = dict(os.environ, PYTHONHASHSEED=seed)
    return subprocess.run([sys.executable, _old_path, _ENGINE], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env, timeout=120).stdout.strip()


check("④ 对照组：改动前的 `hash()` 兜底在两个进程里**确实**会给出不同的键（证明本测试有牙齿）",
      old_child("0") != old_child("12345"), f"{old_child('0')} vs {old_child('12345')}")
_tmp_ctx.cleanup()

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
