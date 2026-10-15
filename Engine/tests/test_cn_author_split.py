#!/usr/bin/env python3.14
r"""`slcn/build.py` 的**中文作者拆分**判据（案例 71，2026-10-15 用户实测）。

缺陷
----
`cn_author_names()` 从前按 `re.split(r"[\s、,，]+", author)` 硬拆，于是期刊里
「两字姓名内部带空白」的排法（源稿 docx 里写作 `陈  波`，两个 ASCII 空格）被拆成
`['陈', '波']`：`author_fields()` 把「波」写成 `\mysecondauthor`，`coauthor_fields()`
再凭空产出 4 个占位符（第二作者中文单位/邮箱、英文姓名/英文单位）并印进 PDF 页眉
（`陈, 波`）。

判据
----
**单个汉字长度的片段不可能是完整中文姓名** → 把「单字片段 + 下一个片段」两两合并。
`/`、`／` 一并当分隔符（期刊里 `顺 真/汤 伟/李炜钰` 这种排法）。这个判据只在片段
连一个字都不够时触发，所以**不会**把 `王刚 李晟` 两个真姓名并成一个——这正是
`convert.py:306-313` 那条「折叠空白后不足 5 字算一个名字」的判据所缺的性质。

四组断言
--------
  ① 必配反例表（逐字用真实字符串）：单字姓名内部空白合并；真·多作者**拆分不变**；
     含英文字母整条不拆（既有约定）。
  ② 连带效应：`author_fields('陈  波')` 只写一个 `\myfirstauthor`，且
     `coauthor_fields()` 为空（= 不再凭空产出 4 个占位符；这是缺陷的完整症状）。
  ③ 非回归：内联**改动前**的实现，对一批真实多作者串逐个比对，结果必须逐字相同。
  ④ 真实稿端到端（`build_docx`）：Bo_Chen 不再有假第二作者、`\myfirstauthor` 是陈波；
     Jingru_Kou / Zhaoshi_Zeng 这类真·多作者稿仍然拆对。

不联网、不写仓库（产物一律进临时目录）、不跑 corpus.py / build.sh。
缺真实稿件时 `SKIP` 而不是失败（与 `test_coauthor_en.py` 的 ③ 组同一口径）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_cn_author_split.py
"""

import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build  # noqa: E402

FAILS = []
SKIPS = []

_ROOT = os.path.dirname(os.path.dirname(_ENGINE))          # 仓库根
BO_CHEN = os.path.join(_ROOT, "2026年第5期(90)", "10 Bo_Chen", "original",
                       "陈波 当代逻辑哲学鸟瞰.docx")
JINGRU = os.path.join(_ROOT, "2026年第5期(90)", "01 Jingru_Kou", "定稿.docx")
ZENG = os.path.join(_ROOT, "2024年第1期(74)", "02 Zhaoshi_Zeng", "original",
                    "原文--论栾调甫中国逻辑研究范式.docx")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def old_cn_author_names(author):
    """**改动前**的实现，逐字照抄（非回归组用）。"""
    author = (author or "").strip()
    if not author or re.search(r"[A-Za-z]", author):
        return []
    return [n for n in re.split(r"[\s、,，]+", author) if n]


def build_tex(src, out_dir, patch_doc=None):
    """跑一次 `build_docx`，返回 `.tex` 文本。"""
    from slcn import docxsource

    real_parse = docxsource.parse_docx
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
        docxsource.parse_docx = real_parse


def noncomment_lines(tex, needle):
    """tex 里**未被注释掉**的、含 needle 的行。`%` 行不算（模板的示例行都是注释）。"""
    return [ln for ln in tex.split("\n") if needle in ln and not ln.lstrip().startswith("%")]


# ── ① 必配反例表 ─────────────────────────────────────────────────────────
print("── ① cn_author_names：必配反例表（真实字符串）──")

_SINGLE_HAN_WITH_SPACE = [
    ("陈  波", ["陈波"]),          # 两个 ASCII 空格（期刊两字姓名的常见排法）
    ("屈 凯", ["屈凯"]),
    ("曾 昭式", ["曾昭式"]),        # 单字在前、三字名在后
]
_REAL_MULTI = [
    ("王刚 李晟", ["王刚", "李晟"]),        # ★ 关键反例：不许并成一个
    ("寇静茹 刘叶涛", ["寇静茹", "刘叶涛"]),
    ("张三、李四", ["张三", "李四"]),
    ("邱德钧  陶景侃", ["邱德钧", "陶景侃"]),
    ("曾昭式   凌敏", ["曾昭式", "凌敏"]),
    ("李友广、李宗敏", ["李友广", "李宗敏"]),
    ("王奇琦 郑伟平", ["王奇琦", "郑伟平"]),
]
_SLASH = [
    ("顺 真/汤 伟/李炜钰", ["顺真", "汤伟", "李炜钰"]),
]

for src, want in _SINGLE_HAN_WITH_SPACE:
    got = build.cn_author_names(src)
    check(f"① {src!r} → 1 个名字 {want[0]}", got == want, f"{got}")

for src, want in _REAL_MULTI:
    got = build.cn_author_names(src)
    check(f"① 真·多作者 {src!r} → {len(want)} 个名字", got == want, f"{got}")

for src, want in _SLASH:
    got = build.cn_author_names(src)
    check(f"① `/` 分隔 {src!r} → {len(want)} 个名字", got == want, f"{got}")

check("① 含英文字母整条不拆（既有约定，见 docstring）",
      build.cn_author_names("Jiawei Chu") == []
      and build.cn_author_names("Chen Bo") == []
      and build.cn_author_names("陈波 Chen Bo") == [], "")
check("① 空 / None 不炸", build.cn_author_names("") == []
      and build.cn_author_names("   ") == [] and build.cn_author_names(None) == [])
check("① 全角空格（U+3000）也是分隔符",
      build.cn_author_names("王刚\u3000李晟") == ["王刚", "李晟"]
      and build.cn_author_names("陈\u3000波") == ["陈波"], "")
check("① 末尾落单的单字片段原样保留（与改动前一致，不硬猜）",
      build.cn_author_names("王刚 李") == ["王刚", "李"], "")
check("① 连着的单字片段两两合并（4 个单字 → 2 个名字）",
      build.cn_author_names("张 三 李 四") == ["张三", "李四"], "")
check("① 纯标点片段不参与合并（`阿 · 迈农` 保持与改动前一致，不硬猜）",
      build.cn_author_names("阿 · 迈农") == old_cn_author_names("阿 · 迈农"), "")


# ── ② 连带效应：假第二作者与 4 个凭空占位符 ──────────────────────────────
print("\n── ② author_fields / coauthor_fields：不再凭空产出第二作者 ──")

_f = build.author_fields("陈  波")
check("② `陈  波` → 只写 `\\myfirstauthor`，值是没有内部空白的 `陈波`",
      _f == {"myfirstauthor": "\\kaishu 陈波"}, f"{_f}")
check("② 没有 `mysecondauthor`（假第二作者不再产生）",
      "mysecondauthor" not in _f, f"{sorted(_f)}")
check("② `coauthor_fields()` 为空 → 不再凭空产出 4 个占位符",
      build.coauthor_fields(_f) == (), f"{build.coauthor_fields(_f)}")
check("② `coauthor_slot_count()` = 1（英文合著者槽位对表也不会错位）",
      build.coauthor_slot_count(_f) == 1, "")

_f2 = build.author_fields("王刚 李晟")
check("② 真·两作者仍然两个槽位",
      _f2 == {"myfirstauthor": "\\kaishu 王刚", "mysecondauthor": "\\kaishu 李晟"}, f"{_f2}")
check("② 真·两作者的 `coauthor_fields()` 才该有 4 项（第二作者中文/英文槽位）",
      [n for n, _l in build.coauthor_fields(_f2)] == [
          "mysecondaffiliation", "mysecondemail",
          "mysecondauthorEN", "mysecondaffiliationEN"], "")
check("② 英文名不经合并、原样保留（既有约定不变）",
      build.author_fields("Jiawei Chu") == {"myfirstauthor": "\\kaishu Jiawei Chu"}, "")
check("② 普通单作者中文名逐字不变",
      build.author_fields("寇静茹") == {"myfirstauthor": "\\kaishu 寇静茹"}
      and build.author_fields("") == {"myfirstauthor": "null"}, "")


# ── ③ 非回归：真·多作者拆分与改动前逐字相同 ──────────────────────────────
print("\n── ③ 非回归：与改动前逐字相同（只在有单字片段时才允许不同）──")

_REALISTIC = [s for s, _w in _REAL_MULTI + _SINGLE_HAN_WITH_SPACE + _SLASH] + [
    "Jiawei Chu", "陈波", "屈凯", "张燕京 李邢睿", "夏吾太 项智多杰",
    "雒自新 王璇", "黄海婷 张建军", "赵亮英 陈晓平", "牛子涵 熊明辉*",
    "鲜于波1, 黄伟鑫1", "南亚伶 王克喜",
]
_diff = [(s, old_cn_author_names(s), build.cn_author_names(s))
         for s in _REALISTIC
         if old_cn_author_names(s) != build.cn_author_names(s)]
check("③ 22 条真实作者串里，结果与改动前不同的一律**只能**是含单字片段/`/` 的那几条",
      all(re.search(r"[/／]", s) or any(len(x) == 1 for x in o) for s, o, _n in _diff),
      f"不同的：{[d for d in _diff]}")
check("③ 真·多作者（两段都 ≥2 字）拆分逐字未变",
      all(old_cn_author_names(s) == build.cn_author_names(s)
          for s in ["王刚 李晟", "寇静茹 刘叶涛", "邱德钧  陶景侃", "曾昭式   凌敏",
                    "张三、李四", "李友广、李宗敏", "张燕京 李邢睿",
                    "夏吾太 项智多杰", "雒自新 王璇", "赵亮英 陈晓平"]), "")
check("③ 真·多作者的**槽位数**一个都没少",
      all(len(build.cn_author_names(s)) == len(old_cn_author_names(s))
          for s in ["王刚 李晟", "寇静茹 刘叶涛", "邱德钧  陶景侃", "曾昭式   凌敏",
                    "张三、李四", "王奇琦 郑伟平", "牛子涵 熊明辉*"]), "")


# ── ④ 真实稿端到端 ───────────────────────────────────────────────────────
print("\n── ④ 真实稿端到端（build_docx）──")

if not os.path.isfile(BO_CHEN):
    skip("④ 端到端（Bo_Chen）", f"缺真实稿件（{BO_CHEN}）")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tex = build_tex(BO_CHEN, os.path.join(tmp, "bo_chen"))
        check("④ `\\myfirstauthor` 是完整的 `陈波`（不再是 `陈`）",
              "\n{\\kaishu 陈波}" in tex, "")
        check("④ **没有**取消注释的 `\\renewcommand{\\mysecondauthor}` 行",
              not noncomment_lines(tex, "\\renewcommand{\\mysecondauthor}"),
              f"{noncomment_lines(tex, '\\renewcommand{\\mysecondauthor}')}")
        check("④ 4 个凭空占位符（第二作者单位/邮箱/英文姓名/英文单位）全部消失",
              all(f"【未识别：{lab}，请手工填写】" not in tex for lab in
                  ("第二作者单位", "第二作者邮箱", "第二作者英文姓名", "第二作者英文单位")), "")

if not os.path.isfile(JINGRU):
    skip("④ 端到端（Jingru_Kou 真两作者）", f"缺真实稿件（{JINGRU}）")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tex = build_tex(JINGRU, os.path.join(tmp, "jingru"))
        check("④ Jingru_Kou 仍然拆成两作者（第一作者寇静茹 + 第二作者刘叶涛）",
              "\n{\\kaishu 寇静茹}" in tex
              and "\\renewcommand{\\mysecondauthor}{\\kaishu 刘叶涛}" in tex, "")

if not os.path.isfile(ZENG):
    skip("④ 端到端（Zhaoshi_Zeng 真两作者）", f"缺真实稿件（{ZENG}）")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tex = build_tex(ZENG, os.path.join(tmp, "zeng"))
        check("④ Zhaoshi_Zeng（`曾昭式 凌敏`）仍然拆成两作者",
              "\n{\\kaishu 曾昭式}" in tex
              and "\\renewcommand{\\mysecondauthor}{\\kaishu 凌敏}" in tex, "")


# ── ⑤ 单作者代表稿：产物与改动前**逐字节相同** ──────────────────────────
print("\n── ⑤ 单作者代表稿：新旧实现产物逐字节相同（`author_fields()` 也动了）──")

LINHE = os.path.join(_ROOT, "2023年第5期(72)", "01 Linhe_Han", "original",
                     "韩林合-康德论逻辑的本性-终版.docx")


def old_author_fields(author):
    """**改动前**的 `author_fields()`（单作者原样照抄原串），非回归组用。"""
    author = (author or "").strip()
    cn = old_cn_author_names(author)
    if len(cn) > 1:
        out = {"myfirstauthor": f"\\kaishu {cn[0]}"}
        for nm, ordw in zip(cn[1:5], build._ORDINALS[1:]):
            out[f"my{ordw}author"] = f"\\kaishu {nm}"
        return out
    return {"myfirstauthor": f"\\kaishu {author}" if author else "null"}


def build_tex_old_impl(src, out_dir):
    real = build.author_fields
    build.author_fields = old_author_fields
    try:
        return build_tex(src, out_dir)
    finally:
        build.author_fields = real


if not os.path.isfile(LINHE):
    skip("⑤ 单作者逐字节回归（Linhe_Han）", f"缺真实稿件（{LINHE}）")
else:
    from slcn import docxsource  # noqa: E402

    with tempfile.TemporaryDirectory() as tmp:
        a = build_tex_old_impl(LINHE, os.path.join(tmp, "old"))
        b = build_tex(LINHE, os.path.join(tmp, "new"))
        check("⑤ 单作者代表稿的 `.tex` 与改动前**逐字节相同**", a == b, f"{len(a)} vs {len(b)}")
    # 内容断言**跟着解析器走**（这个稿的 `author` 目前解析成单位名，写死名字会假失败）
    _auth = docxsource.parse_docx(LINHE).author
    check("⑤ `\\myfirstauthor` = `author_fields()` 对该稿作者串的结果（单作者不多不少）",
          "\n{" + build.author_fields(_auth)["myfirstauthor"] + "}" in b, f"{_auth!r}")
    check("⑤ 单作者稿里没有取消注释的 `\\renewcommand{\\mysecondauthor}` 行",
          not noncomment_lines(b, "\\renewcommand{\\mysecondauthor}"), "")


print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
