#!/usr/bin/env python3
r"""B-268 S4：note 内网址与 url/doi 字段重复时的输出层去重。

覆盖（纯字符串，抽象样例）：
  · 在线资源的 note 里与 url 字段同一条链接 → 删该链接、保留 note 其它文字；
  · 尾斜杠 / arXiv `abs` vs `pdf` / DOI 形态的「同一条链接」归一；
  · 不相同的链接、没有 url 字段时 note 一字不动；
  · 正式发表条目的旧 B-189 口径（note 内嵌网址一律删）不变。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_b268_noteurl.py
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


# 1) 在线资源：note 里的 \url{} 与 url 字段相同 → 去重，保留其它文字
_f, _n = _run("misc", "k1", [
    ("title", "A Synthetic Question"),
    ("note", r"\url{https://example.org/q/1} (version: 2017-06-29)"),
    ("url", "https://example.org/q/1"),
])
check("misc: note 里的重复链接被删", _f.get("note") == "(version: 2017-06-29)", _f.get("note"))
check("misc: url 字段保留", _f.get("url") == "https://example.org/q/1")
check("misc: 有去重说明", any("note 内网址与 url/doi 重复" in x for x in _n), _n)

# 2) 参考工具条目（在线）：尾斜杠差异视为同一条链接
_f2, _n2 = _run("incollection", "k2", [
    ("booktitle", "The {Stanford} Encyclopedia of Philosophy"),
    ("note", r"\url{https://example.org/entries/topic}"),
    ("url", "https://example.org/entries/topic/"),
])
check("SEP: 尾斜杠归一后去重", "note" not in _f2, _f2)
check("SEP: url 保留", _f2.get("url") == "https://example.org/entries/topic/")

# 3) arXiv abs/pdf 形态归一
_f3, _n3 = _run("misc", "k3", [
    ("note", r"\url{https://arxiv.org/pdf/1706.00001.pdf}"),
    ("url", "http://arxiv.org/abs/1706.00001"),
])
check("arXiv: pdf/abs 归一后去重", "note" not in _f3, _f3)

# 4) doi 字段也参与判重（note 里 doi.org 链接）
_f4, _n4 = _run("online", "k4", [
    ("note", r"see \url{https://doi.org/10.1000/xyz.1}"),
    ("doi", "10.1000/xyz.1"),
    ("url", "https://example.org/paper"),
])
check("doi: note 内 https://doi.org/<doi> 去重", _f4.get("note") == "see", _f4.get("note"))

# 5) 不相同的链接 → note 原样（不去重、不洗 note）
_f5, _n5 = _run("misc", "k5", [
    ("note", r"see \url{https://example.org/other}"),
    ("url", "https://example.org/q/1"),
])
check("不同链接不动 note", _f5.get("note") == r"see \url{https://example.org/other}",
      _f5.get("note"))
check("不同链接不写去重说明", not any("重复" in x for x in _n5), _n5)

# 6) 没有 url/doi 字段 → note 原样
_f6, _n6 = _run("misc", "k6", [("note", r"\url{https://example.org/q/1}")])
check("无 url 字段不动 note", _f6.get("note") == r"\url{https://example.org/q/1}")

# 7) 正式发表条目：旧 B-189 口径不变（note 内嵌网址一律删）
_f7, _n7 = _run("article", "k7", [
    ("journal", "Journal of Examples"),
    ("volume", "1"),
    ("note", "http://dx.doi.org/10.1000/xyz.2"),
    ("url", "http://dx.doi.org/10.1000/xyz.2"),
])
check("正式发表：note 内嵌网址仍删", "note" not in _f7, _f7)
check("正式发表：url 仍删（B-189）", "url" not in _f7)
check("正式发表：说明沿用旧文案", any("正式发表条目删除 note 内嵌网址" in x for x in _n7), _n7)

# 8) _url_identity 归一本身
check("归一: \\url 包装去掉",
      bibnorm._url_identity(r"\url{https://example.org/a}") ==
      bibnorm._url_identity("https://example.org/a"))
check("归一: 协议/www/尾斜杠",
      bibnorm._url_identity("http://www.Example.org/a/") == "example.org/a")
check("归一: doi", bibnorm._url_identity("https://dx.doi.org/10.1000/XYZ") == "doi:10.1000/xyz")
check("归一: arxiv", bibnorm._url_identity("https://arxiv.org/pdf/1706.00001.pdf") ==
      bibnorm._url_identity("http://arxiv.org/abs/1706.00001"))

print()
print(f"PASS={PASS} FAIL={len(FAILS)}")
if FAILS:
    print("FAILED: " + "; ".join(FAILS))
sys.exit(len(FAILS))
