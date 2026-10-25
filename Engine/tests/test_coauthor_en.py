#!/usr/bin/env python3.14
r"""`slcn/build.py` 的**英文合著者槽位**判据（GAP 2，2026-10-15 用户实测）。

测什么
------
模板（`编辑笔记/模板文件/CN_template.tex` 1.5 节）把 `mysecondauthorEN` 预置成
`null`、`mysecondaffiliationEN` 预置成示例文字 `affiliation 2`。中文槽位
（`mysecondauthor`）有第二作者时，英文摘要页会按作者数选排版分支——英文槽位不显式化
就会把示例文字印出来（或整块少一个人）。

四组断言：
  ① `en_coauthor_values()`：显式英文作者/单位列表（`and` / `&` / `;`）才按位置填；
     **拿不准一律不填**（逗号倒装名、单一单位、条数对不上）；单作者返回空。
  ② `coauthor_fields()`：中文槽位存在时，英文宏（`my{second…}authorEN` /
     `my{second…}affiliationEN`）也进「要显式化」清单；单作者时清单为空
     （= 产物与从前逐字节相同的**结构性保证**）。
  ③ 真实稿端到端（`build_docx`）：`01 Meimei_Han` 的产物里 `affiliation 2` 消失、
     英文槽位写成占位符；把源稿的英文元数据换成显式列表时按位置填真值。
  ④ 单作者代表稿（`01 Linhe_Han`）用「旧实现」（只显式化中文槽位）与新实现各跑一次，
     `.tex` **逐字节相同**。

不联网、不写仓库（产物一律进临时目录）、不跑 corpus.py / build.sh。
缺真实稿件时 `SKIP` 而不是失败（与 `test_aibib.py` 的 ⑤ 组同一口径）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_coauthor_en.py
"""

import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build, docxsource  # noqa: E402

FAILS = []
SKIPS = []

_ROOT = os.path.dirname(os.path.dirname(_ENGINE))          # 仓库根
JINGRU = os.path.join(_ROOT, "2026年第5期(90)", "01 Meimei_Han", "定稿.docx")
LINHE = os.path.join(_ROOT, "2023年第5期(72)", "01 Linhe_Han", "original",
                     "韩林合-康德论逻辑的本性-终版.docx")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def old_coauthor_fields(field_values):
    """改动前的 `coauthor_fields()`：只显式化**中文**槽位（单作者回归用）。"""
    out = []
    for word, label in build._AUTHOR_SLOTS[1:]:
        if not build.is_parsed(field_values.get(f"my{word}author")):
            continue
        out.append((f"my{word}affiliation", f"{label}单位"))
        out.append((f"my{word}email", f"{label}邮箱"))
    return tuple(out)


def build_tex(src, out_dir, *, old=False, patch_doc=None):
    """跑一次 `build_docx`，返回 `.tex` 文本（`old=True` 时用改动前的实现）。"""
    new_cf, new_ec = build.coauthor_fields, build.en_coauthor_values
    real_parse = docxsource.parse_docx
    if old:
        build.coauthor_fields = old_coauthor_fields
        build.en_coauthor_values = lambda *a, **k: {}
    if patch_doc is not None:
        def fake(path, **kw):
            doc = real_parse(path, **kw)
            patch_doc(doc)
            return doc
        docxsource.parse_docx = fake
    try:
        res = build.build_docx(docx_path=src, out_dir=out_dir, basename="Case",
                               lang="zh", verbose=False)
        return open(res.tex_path, encoding="utf-8").read()
    finally:
        build.coauthor_fields, build.en_coauthor_values = new_cf, new_ec
        docxsource.parse_docx = real_parse


# ── ① en_coauthor_values：可靠来源才填 ────────────────────────────────────
print("── ① en_coauthor_values：可靠来源才填，拿不准一律不填 ──")

_v = build.en_coauthor_values("Meimei Han and Xiaoming Wang",
                              "College of Philosophy, Nankai University", 2)
check("① 显式 `and` 列表 → 第二作者英译名按位置填入",
      _v.get("mysecondauthorEN") == "Xiaoming Wang", f"{_v}")
check("① 单位只有一条（对不上作者数）→ **不填**单位（绝不复制第一作者的）",
      "mysecondaffiliationEN" not in _v, f"{_v}")
_v2 = build.en_coauthor_values(
    "Meimei Han & Xiaoming Wang",
    "College of Philosophy, Nankai University; College of Humanities, X University", 2)
check("① `&` 列表 + 分号分隔的单位列表 → 两者都按位置填",
      _v2.get("mysecondauthorEN") == "Xiaoming Wang"
      and _v2.get("mysecondaffiliationEN") == "College of Humanities, X University", f"{_v2}")
check("① `;` 分隔的作者列表也认", build.en_coauthor_values(
    "Meimei Han; Xiaoming Wang", "", 2).get("mysecondauthorEN") == "Xiaoming Wang")
check("① 逗号**不**当分隔符（`Han, Meimei` 是一个倒装姓名，切开就成了两个作者）",
      build.en_coauthor_values("Han, Meimei", "", 2) == {}, "")
# 案例 163 / 第四十五批 Lead 裁决：连写英文作者串**在严格条件下**按声明作者数等分。
# 背景：90-01 Meimei_Han 源稿英文作者行 `Meimei Han Xiaoming Wang`（无 and/&/;），
# 旧契约一律不猜 → 第二作者英文姓名永远占位符；新增「等分」行为，但判据收紧：
# 词数=2×声明作者数、每词 Title-case 姓名形态、无小写功能词/中缀，否则仍不猜。
check("① 连写两姓名 + 声明 2 作者（案例 163）→ 按作者数等分，第二作者 = `Xiaoming Wang`",
      build.en_coauthor_values("Meimei Han Xiaoming Wang", "", 2)
      == {"mysecondauthorEN": "Xiaoming Wang"}, "")
check("① 反例：声明 3 作者但只有 4 个词（段数不符）→ 不猜",
      build.en_coauthor_values("Meimei Han Xiaoming Wang", "", 3) == {}, "")
check("① 反例：词数对但含小写功能词/中缀（van / 小写姓名）→ 不猜",
      build.en_coauthor_values("Meimei Han van Xiaoming Wang", "", 3) == {}
      and build.en_coauthor_values("Meimei Han yetao Liu", "", 2) == {}, "")
check("① 反例：6 个词/3 作者但每段不全是 Title-case 姓名形态（小写词）→ 不猜",
      build.en_coauthor_values("jingru kou yetao liu x y", "", 3) == {}, "")
check("① 段数与中文作者数不一致 → 不填（3 个名字 / 2 个作者）",
      build.en_coauthor_values("A One and B Two and C Three", "", 2) == {}, "")
check("① 单作者稿 → 空 dict（产物逐字节不变的结构性保证）",
      build.en_coauthor_values("Meimei Han and Xiaoming Wang", "A; B", 1) == {}
      and build.en_coauthor_values("Meimei Han", "A", 0) == {}, "")
check("① 空输入不炸",
      build.en_coauthor_values("", "", 2) == {} and build.en_coauthor_values(None, None, 3) == {})


# ── ② coauthor_fields：英文槽位也进「要显式化」清单 ───────────────────────
print("\n── ② coauthor_fields：中文槽位存在 → 英文槽位也必须显式化 ──")

_SINGLE = {"myfirstauthor": "\\kaishu 韩梅梅"}
check("② 单作者：清单为空（不新增任何占位符 → 与从前逐字节相同）",
      build.coauthor_fields(_SINGLE) == (), f"{build.coauthor_fields(_SINGLE)}")
check("② 单作者槽位数 = 1", build.coauthor_slot_count(_SINGLE) == 1, "")
_TWO = {"myfirstauthor": "\\kaishu 韩梅梅", "mysecondauthor": "\\kaishu 王小明"}
_names = [n for n, _l in build.coauthor_fields(_TWO)]
check("② 有第二作者：中文单位/邮箱＋**英文姓名/英文单位**都在清单里",
      _names == ["mysecondaffiliation", "mysecondemail",
                 "mysecondauthorEN", "mysecondaffiliationEN"], f"{_names}")
check("② 英文槽位的占位符标签是人话（第二作者英文姓名 / 英文单位）",
      dict(build.coauthor_fields(_TWO))["mysecondauthorEN"] == "第二作者英文姓名"
      and dict(build.coauthor_fields(_TWO))["mysecondaffiliationEN"] == "第二作者英文单位", "")
check("② 源稿给了可靠的英译名 → 该槽位算「已解析」，不再要占位符",
      build.is_parsed({**_TWO, "mysecondauthorEN": "Xiaoming Wang"}.get("mysecondauthorEN")), "")
check("② `null`（模板预置）与模板示例文字都算「没解析出来」",
      not build.is_parsed("null") and not build.is_parsed("")
      and not build.is_parsed("affiliation 2", "mysecondaffiliationEN",
                              {"mysecondaffiliationEN": "affiliation 2"}), "")
check("② 中文作者拆分判据（`cn_author_names`）与改动前一致",
      build.cn_author_names("韩梅梅 王小明") == ["韩梅梅", "王小明"]
      and build.cn_author_names("Jiawei Zhong") == []
      and build.author_fields("韩梅梅 王小明")
      == {"myfirstauthor": "\\kaishu 韩梅梅", "mysecondauthor": "\\kaishu 王小明"}, "")


# ── ③ 真实稿端到端：占位符 / 显式来源 ─────────────────────────────────────
print("\n── ③ 真实稿端到端（build_docx）──")

if not os.path.isfile(JINGRU):
    skip("③ 端到端（Meimei_Han）", f"缺真实稿件（{JINGRU}）")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tex = build_tex(JINGRU, os.path.join(tmp, "real"))
        check("③ 模板示例文字 `affiliation 2` **不再**留在产物里",
              "affiliation 2" not in tex, "")
        check("③ 第二作者英文姓名写成可见占位符（取消注释的 \\renewcommand 行）",
              "\\renewcommand{\\mysecondauthorEN}{【未识别：第二作者英文姓名，请手工填写】}" in tex, "")
        check("③ 第二作者英文单位也写成占位符",
              "\\newcommand{\\mysecondaffiliationEN}{【未识别：第二作者英文单位，请手工填写】}" in tex, "")
        check("③ 中文槽位的占位符行为未变（第二作者单位/邮箱仍在）",
              "【未识别：第二作者单位，请手工填写】" in tex
              and "【未识别：第二作者邮箱，请手工填写】" in tex, "")

    with tempfile.TemporaryDirectory() as tmp:
        def explicit(doc):
            # 模拟「源稿英文元数据块给出显式作者/单位列表」（真实稿件那行没有分隔符）
            doc.author_en = "Meimei Han and Xiaoming Wang"
            doc.affiliation_en = ("College of Philosophy, Nankai University; "
                                  "College of Humanities, X University")
        tex2 = build_tex(JINGRU, os.path.join(tmp, "explicit"), patch_doc=explicit)
        check("③ 源稿给了显式英文列表 → 按位置填**真值**（不是占位符）",
              "{\\mysecondauthorEN}{Xiaoming Wang}" in tex2
              and "{\\mysecondaffiliationEN}{College of Humanities, X University}" in tex2
              and "第二作者英文姓名" not in tex2, "")
        check("③ 显式来源时 `affiliation 2` 同样不出现",
              "affiliation 2" not in tex2, "")


# ── ④ 单作者回归：与改动前逐字节相同 ──────────────────────────────────────
print("\n── ④ 单作者代表稿：新实现 == 旧实现（逐字节）──")

if not os.path.isfile(LINHE):
    skip("④ 单作者回归（Linhe_Han）", f"缺真实稿件（{LINHE}）")
else:
    with tempfile.TemporaryDirectory() as tmp:
        a = build_tex(LINHE, os.path.join(tmp, "old"), old=True)
        b = build_tex(LINHE, os.path.join(tmp, "new"))
        check("④ 单作者稿的 `.tex` 与改动前**逐字节相同**", a == b, f"{len(a)} vs {len(b)}")
        check("④ 单作者稿里没有冒出任何「第二作者英文」占位符",
              "第二作者英文" not in b, "")


print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
