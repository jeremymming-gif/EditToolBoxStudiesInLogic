#!/usr/bin/env python3.14
r"""`slcn/build.py` 的**中文作者拆分**判据（案例 71，2026-10-15 用户实测）。

缺陷
----
`cn_author_names()` 从前按 `re.split(r"[\s、,，]+", author)` 硬拆，于是期刊里
「两字姓名内部带空白」的排法（源稿 docx 里写作 `李  雷`，两个 ASCII 空格）被拆成
`['李', '雷']`：`author_fields()` 把「波」写成 `\mysecondauthor`，`coauthor_fields()`
再凭空产出 4 个占位符（第二作者中文单位/邮箱、英文姓名/英文单位）并印进 PDF 页眉
（`李, 雷`）。

判据
----
**单个汉字长度的片段不可能是完整中文姓名** → 把「单字片段 + 下一个片段」两两合并。
`/`、`／` 一并当分隔符（期刊里 `顺 海/汤 宁/李敏` 这种排法）。这个判据只在片段
连一个字都不够时触发，所以**不会**把 `王强 李强` 两个真姓名并成一个——这正是
`convert.py:306-313` 那条「折叠空白后不足 5 字算一个名字」的判据所缺的性质。

四组断言
--------
  ① 必配反例表（逐字用真实字符串）：单字姓名内部空白合并；真·多作者**拆分不变**；
     含英文字母整条不拆（既有约定）。
  ② 连带效应：`author_fields('李  雷')` 只写一个 `\myfirstauthor`，且
     `coauthor_fields()` 为空（= 不再凭空产出 4 个占位符；这是缺陷的完整症状）。
  ③ 非回归：内联**改动前**的实现，对一批真实多作者串逐个比对，结果必须逐字相同。
  ④ 真实稿端到端（`build_docx`）：Lei_Li 不再有假第二作者、`\myfirstauthor` 是李雷；
     Meimei_Han / Ning_Zeng 这类真·多作者稿仍然拆对。

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

def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


import test_corpus_fixture_paths as FP  # noqa: E402  「期号+篇序号」取样，不写作者名


def _main(ref):
    """按「期号+篇序号」取该篇正文稿；取不到就把取样状态打出来。

    以前这里写死 `2026年第5期(90)/10 Lei_Li/original/李雷 …docx` 这种作者名路径：
    期号与篇序号是真的，作者名是文档里的化名，真语料里那一篇的作者名跟它对不上，
    于是整组 ④/⑤ 在真机上只能 `SKIP`。取样归取样（`FP` 负责），断言仍在这里。
    """
    _r = FP.resolve_main(FP.default_root(), ref)
    if _r.fatal:
        check(f"取样代码的错（{_r.status}）：{ref}", False, _r.detail)
    return _r


_BO = _main("2026年第5期(90)/10")
_JINGRU = _main("2026年第5期(90)/01")
_ZENG = _main("2024年第1期(74)/02")
BO_CHEN = _BO.path if _BO.ok else ""
JINGRU = _JINGRU.path if _JINGRU.ok else ""
ZENG = _ZENG.path if _ZENG.ok else ""


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
    ("李  雷", ["李雷"]),          # 两个 ASCII 空格（期刊两字姓名的常见排法）
    ("屈 明", ["屈明"]),
    ("曾 宁", ["曾宁"]),        # 单字在前、三字名在后
]
_REAL_MULTI = [
    ("王强 李强", ["王强", "李强"]),        # ★ 关键反例：不许并成一个
    ("韩梅梅 王小明", ["韩梅梅", "王小明"]),
    ("张三、李四", ["张三", "李四"]),
    ("邱伟  陶明", ["邱伟", "陶明"]),
    ("曾宁   凌清", ["曾宁", "凌清"]),
    ("李海、李宁", ["李海", "李宁"]),
    ("王敏 郑海", ["王敏", "郑海"]),
]
_SLASH = [
    ("顺 海/汤 宁/李敏", ["顺海", "汤宁", "李敏"]),
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
      build.cn_author_names("Jiawei Zhong") == []
      and build.cn_author_names("Li Lei") == []
      and build.cn_author_names("李雷 Li Lei") == [], "")
check("① 空 / None 不炸", build.cn_author_names("") == []
      and build.cn_author_names("   ") == [] and build.cn_author_names(None) == [])
check("① 全角空格（U+3000）也是分隔符",
      build.cn_author_names("王强\u3000李强") == ["王强", "李强"]
      and build.cn_author_names("李\u3000雷") == ["李雷"], "")
check("① 末尾落单的单字片段原样保留（与改动前一致，不硬猜）",
      build.cn_author_names("王强 李") == ["王强", "李"], "")
check("① 连着的单字片段两两合并（4 个单字 → 2 个名字）",
      build.cn_author_names("张 三 李 四") == ["张三", "李四"], "")
check("① 纯标点片段不参与合并（`阿 · 迈农` 保持与改动前一致，不硬猜）",
      build.cn_author_names("阿 · 迈农") == old_cn_author_names("阿 · 迈农"), "")


# ── ② 连带效应：假第二作者与 4 个凭空占位符 ──────────────────────────────
print("\n── ② author_fields / coauthor_fields：不再凭空产出第二作者 ──")

_f = build.author_fields("李  雷")
check("② `李  雷` → 只写 `\\myfirstauthor`，值是没有内部空白的 `李雷`",
      _f == {"myfirstauthor": "\\kaishu 李雷"}, f"{_f}")
check("② 没有 `mysecondauthor`（假第二作者不再产生）",
      "mysecondauthor" not in _f, f"{sorted(_f)}")
check("② `coauthor_fields()` 为空 → 不再凭空产出 4 个占位符",
      build.coauthor_fields(_f) == (), f"{build.coauthor_fields(_f)}")
check("② `coauthor_slot_count()` = 1（英文合著者槽位对表也不会错位）",
      build.coauthor_slot_count(_f) == 1, "")

_f2 = build.author_fields("王强 李强")
check("② 真·两作者仍然两个槽位",
      _f2 == {"myfirstauthor": "\\kaishu 王强", "mysecondauthor": "\\kaishu 李强"}, f"{_f2}")
check("② 真·两作者的 `coauthor_fields()` 才该有 4 项（第二作者中文/英文槽位）",
      [n for n, _l in build.coauthor_fields(_f2)] == [
          "mysecondaffiliation", "mysecondemail",
          "mysecondauthorEN", "mysecondaffiliationEN"], "")
check("② 英文名不经合并、原样保留（既有约定不变）",
      build.author_fields("Jiawei Zhong") == {"myfirstauthor": "\\kaishu Jiawei Zhong"}, "")
check("② 普通单作者中文名逐字不变",
      build.author_fields("韩梅梅") == {"myfirstauthor": "\\kaishu 韩梅梅"}
      and build.author_fields("") == {"myfirstauthor": "null"}, "")


# ── ③ 非回归：真·多作者拆分与改动前逐字相同 ──────────────────────────────
print("\n── ③ 非回归：与改动前逐字相同（只在有单字片段时才允许不同）──")

_REALISTIC = [s for s, _w in _REAL_MULTI + _SINGLE_HAN_WITH_SPACE + _SLASH] + [
    "Jiawei Zhong", "李雷", "屈明", "张敏 李海宁", "夏明 欧阳明海",
    "赵丽丽 孙宁", "吴静怡 郑建国", "何嘉怡 徐强", "牛静 熊海*",
    "鲜于明1, 黄泽晨1", "南晨 王海宁",
]
_diff = [(s, old_cn_author_names(s), build.cn_author_names(s))
         for s in _REALISTIC
         if old_cn_author_names(s) != build.cn_author_names(s)]
check("③ 22 条真实作者串里，结果与改动前不同的一律**只能**是含单字片段/`/` 的那几条",
      all(re.search(r"[/／]", s) or any(len(x) == 1 for x in o) for s, o, _n in _diff),
      f"不同的：{[d for d in _diff]}")
check("③ 真·多作者（两段都 ≥2 字）拆分逐字未变",
      all(old_cn_author_names(s) == build.cn_author_names(s)
          for s in ["王强 李强", "韩梅梅 王小明", "邱伟  陶明", "曾宁   凌清",
                    "张三、李四", "李海、李宁", "张敏 李海宁",
                    "夏明 欧阳明海", "赵丽丽 孙宁", "何嘉怡 徐强"]), "")
check("③ 真·多作者的**槽位数**一个都没少",
      all(len(build.cn_author_names(s)) == len(old_cn_author_names(s))
          for s in ["王强 李强", "韩梅梅 王小明", "邱伟  陶明", "曾宁   凌清",
                    "张三、李四", "王敏 郑海", "牛静 熊海*"]), "")


# ── ④ 真实稿端到端 ───────────────────────────────────────────────────────
print("\n── ④ 真实稿端到端（build_docx）──")

if not BO_CHEN:
    skip("④ 端到端（单字姓名内部空白那一篇）", FP.skip_line(_BO))
else:
    with tempfile.TemporaryDirectory() as tmp:
        tex = build_tex(BO_CHEN, os.path.join(tmp, "bo_chen"))
        from slcn import docxsource  # noqa: E402
        _a = docxsource.parse_docx(BO_CHEN).author or ""
        _names = build.cn_author_names(_a)
        # 该稿排的是「单字姓名内部带空白」。期望值不再写死人名（那是文档化名，且
        # 真实姓名不许进公开代码），改为「解析器判成 1 个作者」+「产物里就是那 1 个」，
        # 而 `cn_author_names` 本身在 ③ 组已用 22 条真实作者串逐字钉住。
        check("④ 单字姓名内部空白那一篇被判成 1 个作者（没有被拆成单字）",
              len(_names) == 1 and len(_names[0]) >= 2, f"{_names}")
        check("④ `\\myfirstauthor` 就是那 1 个完整姓名（不再是单字）",
              bool(_names) and "\n{\\kaishu " + _names[0] + "}" in tex, f"{_names}")
        check("④ **没有**取消注释的 `\\renewcommand{\\mysecondauthor}` 行",
              not noncomment_lines(tex, "\\renewcommand{\\mysecondauthor}"),
              f"{noncomment_lines(tex, '\\renewcommand{\\mysecondauthor}')}")
        check("④ 4 个凭空占位符（第二作者单位/邮箱/英文姓名/英文单位）全部消失",
              all(f"【未识别：{lab}，请手工填写】" not in tex for lab in
                  ("第二作者单位", "第二作者邮箱", "第二作者英文姓名", "第二作者英文单位")), "")

if not JINGRU:
    skip("④ 端到端（真两作者 2026年第5期(90)/01）", FP.skip_line(_JINGRU))
else:
    with tempfile.TemporaryDirectory() as tmp:
        tex = build_tex(JINGRU, os.path.join(tmp, "jingru"))
        from slcn import docxsource  # noqa: E402
        _a2 = docxsource.parse_docx(JINGRU).author or ""
        _n2 = build.cn_author_names(_a2)
        check("④ 真两作者的槽位数仍是 2",
              len(_n2) == 2, f"{_n2}")
        check("④ 真两作者仍按位置拆开写（第一/第二作者都不是空的）",
              len(_n2) == 2
              and "\n{\\kaishu " + _n2[0] + "}" in tex
              and "\\renewcommand{\\mysecondauthor}{\\kaishu " + _n2[1] + "}" in tex, f"{_n2}")

if not ZENG:
    skip("④ 端到端（真两作者 2024年第1期(74)/02）", FP.skip_line(_ZENG))
else:
    with tempfile.TemporaryDirectory() as tmp:
        tex = build_tex(ZENG, os.path.join(tmp, "zeng"))
        from slcn import docxsource  # noqa: E402
        _a3 = docxsource.parse_docx(ZENG).author or ""
        _n3 = build.cn_author_names(_a3)
        check("④ 另一篇真两作者也仍然拆成两作者",
              len(_n3) == 2
              and "\n{\\kaishu " + _n3[0] + "}" in tex
              and "\\renewcommand{\\mysecondauthor}{\\kaishu " + _n3[1] + "}" in tex, f"{_n3}")


# ── ⑤ 单作者代表稿：产物与改动前**逐字节相同** ──────────────────────────
print("\n── ⑤ 单作者代表稿：新旧实现产物逐字节相同（`author_fields()` 也动了）──")

_LINHE = _main("2023年第5期(72)/01")
LINHE = _LINHE.path if _LINHE.ok else ""


def old_author_fields(author, *, en_names=()):
    """**改动前**的 `author_fields()`（单作者原样照抄原串），非回归组用。

    `en_names=` 是后来才有的关键字：改动前那版根本没有英文名参数（英文多作者不拆），
    所以这里显式收下并忽略，保持「旧实现」语义；不接这个参数会让 `build_docx()`
    在 `build.py:2610` 处 `TypeError`（这条只有在 ⑤ 真正跑起来才暴露）。
    """
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


if not LINHE:
    skip("⑤ 单作者逐字节回归（2023年第5期(72)/01）", FP.skip_line(_LINHE))
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
