#!/usr/bin/env python3
r"""B-270 W1：URL/note 规范化（解析层不双写 + 输出层裸 URL 去重）。

覆盖（全部为合成字符串，不含任何真实稿件/作者信息）：
  · 输出层：note 里的裸 URL 与 `url` 字段同一条链接 → 删 URL、留文字（旧实现裸 URL 早退）；
  · `\url{}` / `\href{}{}` / `\url {…}` 包裹形态；
  · 包裹 + 裸 URL 混合形态（旧实现在这里抛 IndexError: no such group）；
  · LaTeX 转义斜杠 `\/`（`archives\/win2022` 形态）与普通斜杠视为同一身份；
  · 协议/`www.`/尾斜杠/doi.org→doi 身份归一；
  · 负例：note 纯文字、不同链接、没有 url 字段 → 一字不动；
  · 解析层：APA `Retrieved 日期, from URL` 之后 note 只留日期（URL 归 url 字段）；
  · 编号清单 `apply_numbered_extras` 写 url 后从 note 删掉同一链接；
  · 端到端 `normalize_bib(strip_urls=True)`：url+note 双写清零且幂等。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_b270_url_note.py
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibnorm  # noqa: E402
from slcn.wordrefs import apply_numbered_extras  # noqa: E402

PASS = 0
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def _run(etype: str, key: str, fields: list[tuple[str, str]]):
    notes: list[str] = []
    out = bibnorm.strip_published_urls_fields(key, etype, list(fields), notes)
    return dict(out), notes


def _identity(url: str) -> str:
    return bibnorm._url_identity(url)


# ── 输出层：裸 URL 去重（B-268 S4 的漏项） ───────────────────────────────
# 1) 实测形态：「Retrieved 日期, 裸 URL」与 url 字段同链接
_f, _n = _run("misc", "bare1", [
    ("note", "Retrieved October 30, 2025, https://example.org/word/think"),
    ("url", "https://example.org/word/think"),
])
check("裸 URL：note 里的同链接被删", _f.get("note") == "Retrieved October 30, 2025",
      _f.get("note"))
check("裸 URL：url 字段保留", _f.get("url") == "https://example.org/word/think")
check("裸 URL：有去重说明",
      any("note 内网址与 url/doi 重复" in x for x in _n), _n)

# 2) note 文字在 URL 之后的形态：只删链接，保留其它文字与句点
_f, _n = _run("misc", "bare2", [
    ("note", "Edited by A. Editor and B. Editor. Faculty of Examples. "
             "https://example.org/entry/31654"),
    ("url", "https://example.org/entry/31654"),
])
check("裸 URL：note 其它文字与句点保留",
      _f.get("note") == "Edited by A. Editor and B. Editor. Faculty of Examples.", _f.get("note"))

# 3) `\url{}` 包裹（B-268 既有形态）
_f, _n = _run("misc", "wrap1", [
    ("note", r"Winter 2022 Edition, \url{https://example.org/archives/win2022/entries/topic/}"),
    ("url", "https://example.org/archives/win2022/entries/topic/"),
])
check("包裹 \\url{}：同链接被删、文字保留",
      _f.get("note") == "Winter 2022 Edition", _f.get("note"))

# 4) `\url {…}`（命令与花括号之间有空格）
_f, _n = _run("misc", "wrap2", [
    ("note", r"see \url {https://example.org/a} (version: 2017-01-02)"),
    ("url", "https://example.org/a"),
])
check("包裹 \\url {…}：同链接被删", _f.get("note") == "see (version: 2017-01-02)",
      _f.get("note"))

# 5) `\href{…}{…}` 包裹
_f, _n = _run("misc", "wrap3", [
    ("note", r"see \href{https://example.org/a}{the page}"),
    ("url", "https://example.org/a"),
])
check("包裹 \\href{}{}：同链接整 token 删除", _f.get("note") == "see",
      _f.get("note"))

# 6) 包裹 + 裸 URL 混合（旧实现 IndexError: no such group 的回归用例）
try:
    _f, _n = _run("misc", "mixed1", [
        ("note", r"see \url{https://example.org/a} and https://example.org/a"),
        ("url", "https://example.org/a"),
    ])
    _mixed_ok = True
except Exception as e:  # noqa: BLE001
    _mixed_ok, _f, _n, _err = False, {}, [], f"{type(e).__name__}: {e}"
check("混合形态（包裹+裸）不抛 IndexError", _mixed_ok, "" if _mixed_ok else _err)
check("混合形态：两处同链接都被删", _mixed_ok and "https://" not in (_f.get("note") or ""),
      _f.get("note"))

# 7) 转义斜杠 `\/`（库 c14-6 的 `archives\/win2022` 形态）视为同一身份
_f, _n = _run("misc", "esc1", [
    ("note", r"Winter 2022 Edition, https://example.org/archives\/win2022/entries/topic/"),
    ("url", "https://example.org/archives/win2022/entries/topic/"),
])
check("转义斜杠 \\/：note 同链接被删", _f.get("note") == "Winter 2022 Edition",
      _f.get("note"))
check("转义斜杠归一",
      _identity(r"https://example.org/archives\/win2022/entries/topic/") ==
      _identity("https://example.org/archives/win2022/entries/topic/"))

# 8) 协议/http vs https + www + 尾斜杠归一
_f, _n = _run("misc", "norm1", [
    ("note", "Retrieved November 1, 2025, http://www.example.org/entry/"),
    ("url", "https://example.org/entry"),
])
check("协议/www/尾斜杠归一后去重", _f.get("note") == "Retrieved November 1, 2025",
      _f.get("note"))

# 9) doi 身份：note 内 https://doi.org/<doi> 与 doi 字段同链接
_f, _n = _run("online", "doi1", [
    ("note", "Retrieved November 1, 2025, https://doi.org/10.1000/XYZ.7"),
    ("doi", "10.1000/xyz.7"),
])
check("doi 身份归一后去重", _f.get("note") == "Retrieved November 1, 2025", _f.get("note"))

# 10) arXiv abs/pdf 归一
_f, _n = _run("misc", "arx1", [
    ("note", "see https://arxiv.org/pdf/1706.00001.pdf"),
    ("url", "http://arxiv.org/abs/1706.00001"),
])
check("arXiv abs/pdf 归一后去重", _f.get("note") == "see", _f.get("note"))

# ── 负例：不该动的一个字都不动 ─────────────────────────────────────────
_f, _n = _run("misc", "neg1", [
    ("note", "Accessible only in print (no link)."),
    ("url", "https://example.org/a"),
])
check("负例：note 纯文字原样", _f.get("note") == "Accessible only in print (no link).",
      _f.get("note"))
check("负例：纯文字不写去重说明", not _n, _n)

_f, _n = _run("misc", "neg2", [
    ("note", "see https://example.org/other"),
    ("url", "https://example.org/a"),
])
check("负例：不同链接不动 note", _f.get("note") == "see https://example.org/other",
      _f.get("note"))

_f, _n = _run("misc", "neg3", [("note", "Retrieved October 30, 2025, https://example.org/a")])
check("负例：没有 url/doi 字段时 note 原样",
      _f.get("note") == "Retrieved October 30, 2025, https://example.org/a", _f.get("note"))

# ── 解析层：编号路径写 url 时从 note 剥离同一链接（APA Retrieved … from URL） ──
# ── 编号清单解析：apply_numbered_extras 写 url 后清 note ────────────────
_entry = {"_type": "misc",
          "note": "Retrieved November 1, 2025, https://example.org/oed/2211"}
apply_numbered_extras(
    _entry, "Retrieved November 1, 2025, https://example.org/oed/2211")
check("编号清单：url 写入", _entry.get("url") == "https://example.org/oed/2211", _entry)
check("编号清单：note 同链接被删", _entry.get("note") == "Retrieved November 1, 2025",
      _entry.get("note"))

_entry2 = {"_type": "misc", "note": "Edited by A. Editor. Faculty of Examples. "
                                   "https://example.org/entry/32414"}
apply_numbered_extras(
    _entry2, "Edited by A. Editor. Faculty of Examples. https://example.org/entry/32414")
check("编号清单：句末句点保留",
      _entry2.get("note") == "Edited by A. Editor. Faculty of Examples.", _entry2.get("note"))

_entry3 = {"_type": "incollection",
           "note": "Retrieved October 30, 2025, from https://example.org/entries/synthetic/"}
apply_numbered_extras(
    _entry3, "Retrieved October 30, 2025, from https://example.org/entries/synthetic/")
check("APA 编号路径：url 归位", _entry3.get("url") ==
      "https://example.org/entries/synthetic/", _entry3)
check("APA 编号路径：note 不再含 from+URL",
      _entry3.get("note") == "Retrieved October 30, 2025", _entry3.get("note"))

# ── 端到端：normalize_bib(strip_urls=True) 清零双写且幂等 ──────────────
_BIB = ("@misc{Harper13,\n"
        "  note = {Retrieved October 30, 2025, https://example.org/word/think},\n"
        "  url = {https://example.org/word/think}\n"
        "}\n")
_new, _notes = bibnorm.normalize_bib(_BIB, strip_urls=True)
import re as _re
check("端到端：note 不再含 URL", _re.search(r"note\s*=\s*\{[^}]*https?://", _new) is None, _new)
check("端到端：url 字段保留", "url" in _new and "https://example.org/word/think" in _new)
_new2, _notes2 = bibnorm.normalize_bib(_new, strip_urls=True)
check("端到端：幂等（二次 normalize 不改）", _new2 == _new, _notes2)

print()
print(f"PASS={PASS} FAIL={len(FAILS)}")
if FAILS:
    print("FAILED: " + "; ".join(FAILS))
sys.exit(len(FAILS))
