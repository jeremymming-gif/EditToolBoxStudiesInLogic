#!/usr/bin/env python3.14
r"""`slcn/docxsource.py` 的 **「作者简介」里取单位**判据（案例 75，2026_05_Bo_Chen 实测）。

缺陷
----
源稿 `2026年第5期(90)/10 Bo_Chen/original/陈波 当代逻辑哲学鸟瞰.docx` 的
`word/footnotes.xml` 里写着：

    作者简介: 陈波，男，湖南常德人，武汉大学哲学系教授。电子邮箱：chen-bo@whu.edu.cn

单位硬串「武汉大学哲学系」就在这句里，产物却写 `【未识别：第一作者单位，请手工填写】`。
根因在 `_clean_affil_line()` 的**整行否定**（旧实现），三道各自独立致命：

    ① 行内有 `。` → 整行 `return ""`；
    ② 「作者简介」句里的单字碎片 `男` 落进 `_clean_affil_cjk` 的 `else: return ""`；
    ③ 机构名以 `教授` 收尾，不满足 `_AFFIL_UNIT_END_RE` 的 `$`。

已实测（**能解但判据太严**，不是没解析出来）：

    _clean_affil_line('作者简介: 陈波，男，湖南常德人，武汉大学哲学系教授。电子邮箱：…') == ''
    _clean_affil_line('作者简介: 陈波，武汉大学哲学系') == '武汉大学哲学系'   ← 去掉「男」就能解
    _clean_affil_line('作者简介: 陈波，男，武汉大学哲学系') == ''            ← 留着「男」就整行丢

改法：stage ① 保持旧口径不动；stage ② 按 `，。；、` 切句、在**句内**取
`_AFFIL_UNIT_START_RE … ` 形态的最长机构名，允许 `教授/副教授/讲师/研究员/博士` 这类
学术职衔后缀（**剥离**，行政职务不收）。

四组断言
--------
  ① 正例：案例 75 那一行原样 → `武汉大学哲学系`；`_clean_affil_line` 的三条实测
     （含 `男` 的两种情况）全部解出来。
  ② 反例（历史教训，一个都不许破）：
     · `05 Guoping_Du` 那类：**电话号码 / 家庭住址 / 邮政编码**不许被当成单位；
     · `03 Yuanchang_He` 的 `作者简介：…563481898@qq.com`：**邮箱**不许当单位；
     · Guoping_Du 的作者简介里有 `…智能与逻辑实验室主任`：**行政职务**后面的平台不许
       被当成新单位（会把一个本来正确的值改坏）；
     · 既有的整行反例照旧全空（题名含机构词、列表项、数字起头、会议日期、网址、英文题目）。
  ③ 非回归：内联**改动前**的 `_clean_affil_line`，对
     (a) 一张真实字符串表 + (b) 真实稿里实际喂进去的**每一行** 逐个比对：
     **改动前非空的行，结果必须逐字不变**（= 本缺陷只可能「空 → 非空」）。
  ④ 端到端：`parse_docx` 对 Bo_Chen 产出 `武汉大学哲学系`；含作者简介的真实稿
     旧实现取不到、新实现取到了（收益）；且旧实现本来能取到的稿，值一个都不变。

不联网、不写仓库（全部只读源稿）、不跑 corpus.py / build.sh。
缺真实稿件时 `SKIP` 而不是失败（与 `test_coauthor_en.py` 的 ③ 组同一口径）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_affil_from_bio.py
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import docxsource as D  # noqa: E402

FAILS = []
SKIPS = []

_ROOT = os.path.dirname(os.path.dirname(_ENGINE))          # 仓库根
BO_CHEN = os.path.join(_ROOT, "2026年第5期(90)", "10 Bo_Chen", "original",
                       "陈波 当代逻辑哲学鸟瞰.docx")
# 03 Yuanchang_He 的作者简介在开头元数据区（不是脚注）：`作者简介：何远长，武汉大学哲学学院博士生，…`
YUANCHANG = os.path.join(_ROOT, "2025年第1期(80)", "03 Yuanchang_He")
# 05 Guoping_Du 的作者简介里有 联系电话 / 通信地址 / 邮政编码 / 工作单位（尾块）+ 脚注里的长简介
GUOPING = os.path.join(_ROOT, "2024年第4期(77)", "05 Guoping_Du")
# 单位本来就在开头括号行里、值已经正确的代表稿（不许被动）
OTHER_DOCS = [
    ("01 Jingru_Kou", os.path.join(_ROOT, "2026年第5期(90)", "01 Jingru_Kou")),
    ("07 Yanjing_Zhang", os.path.join(_ROOT, "2025年第4期(83)", "07 Yanjing_Zhang")),
    ("07 Kai_Qu", os.path.join(_ROOT, "2024年第2期(75)", "07 Kai_Qu")),
    ("07 Liangying_Zhao", os.path.join(_ROOT, "2026年第5期(90)", "07 Liangying_Zhao")),
]


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def old_clean_affil_line(line):
    """**改动前**的 `_clean_affil_line()`（逐字照抄，非回归组用）。"""
    t = (line or "").replace("\ufeff", "").replace("\u200b", "")
    t = re.sub(r"\s+", " ", t.strip())
    if not t or len(t) > 160:
        return ""
    if D._AFFIL_NEVER_RE.match(t):
        return ""
    if not D._AFFIL_WORD_RE.search(t):
        return ""
    if re.search(r"[。．]|《|》|网址|https?://|www\.", t) or t.endswith((";", "；")):
        return ""
    if re.search(r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日", t):
        return ""
    if D._AFFIL_LIST_ITEM_RE.match(t) or t[0].isdigit():
        return ""
    t = D._AFFIL_LABEL_RE.sub("", t)
    if D._AFFIL_NEVER_START_RE.match(t):
        return ""
    t = re.sub(r"[\[（(]\s*(?:通讯作者|通信作者|责任编辑|corresponding author)\s*[\]）)]",
               "", t, flags=re.I).strip()
    if len(t) >= 2 and (t[0], t[-1]) in (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]")):
        t = t[1:-1].strip()
    if not t:
        return ""
    if D._AFFIL_PROSE_RE.search(t):
        return ""
    return D._clean_affil_cjk(t) if D._has_cjk(t) else D._clean_affil_latin(t)


C = D._clean_affil_line

# ── ① 正例：案例 75 那一行 ────────────────────────────────────────────────
print("── ① 正例：作者简介里的单位要被抓出来 ──")

BO_LINE = ("作者简介: 陈波，男，湖南常德人，武汉大学哲学系教授。"
           "电子邮箱：chen-bo@whu.edu.cn")
check("① 案例 75 原样一行 → `武汉大学哲学系`", C(BO_LINE) == "武汉大学哲学系", repr(C(BO_LINE)))
check("① 旧实现对这一行返回空串（复现缺陷本身）",
      old_clean_affil_line(BO_LINE) == "", repr(old_clean_affil_line(BO_LINE)))
check("① `作者简介: 陈波，武汉大学哲学系`（去掉「男」就能解的那条）仍然解得出",
      C("作者简介: 陈波，武汉大学哲学系") == "武汉大学哲学系", repr(C("作者简介: 陈波，武汉大学哲学系")))
check("① `作者简介: 陈波，男，武汉大学哲学系`（留着「男」就整行丢的那条）本次也解得出",
      C("作者简介: 陈波，男，武汉大学哲学系") == "武汉大学哲学系",
      repr(C("作者简介: 陈波，男，武汉大学哲学系")))
check("① 职衔后缀被剥离（`教授/博士生/博士研究生`），不留在单位名里",
      C("作者简介：任然，（1984-）女，唐山师范学院讲师，主要研究方向：科学技术哲学。") == "唐山师范学院"
      and C("作者简介：何远长，武汉大学哲学学院博士生") == "武汉大学哲学学院"
      and C("作者简介：李宗敏，西北大学中国思想文化研究所博士研究生。") == "西北大学中国思想文化研究所",
      "")
check("① 多作者多单位按出现顺序全部取出（`\\x00` 连接）",
      C("作者简介：赵亮英，广东警官学院马克思主义学院副教授．"
        "陈晓平，华南师范大学政治与公共管理学院教授、广东财经大学智能社会与人的发展研究中心兼职教授。")
      == "广东警官学院马克思主义学院\x00华南师范大学政治与公共管理学院"
         "\x00广东财经大学智能社会与人的发展研究中心",
      repr(C("作者简介：赵亮英，广东警官学院马克思主义学院副教授．"
             "陈晓平，华南师范大学政治与公共管理学院教授、"
             "广东财经大学智能社会与人的发展研究中心兼职教授。")))
check("① 单位名里的圆括号（校区）保留：`中山大学哲学系（珠海）助理教授` → `中山大学哲学系（珠海）`",
      C("作者简介：萧浩健（1983-）,男,广东汕头人,布朗大学哲学博士,"
        "中山大学哲学系（珠海）助理教授,主要研究方向为语言哲学。hksiu@outlook.com。")
      == "中山大学哲学系（珠海）",
      repr(C("作者简介：萧浩健（1983-）,男,广东汕头人,布朗大学哲学博士,"
             "中山大学哲学系（珠海）助理教授,主要研究方向为语言哲学。hksiu@outlook.com。")))

# ── ② 反例：历史教训 ─────────────────────────────────────────────────────
print("\n── ② 反例：电话 / 住址 / 邮编 / 邮箱 / 行政职务 / 既有整行反例 ──")

_MUST_EMPTY = [
    ("联系电话：18901393186", "电话号码"),
    ("联系电话: 010-88888888", "电话号码（区号）"),
    ("通信地址：北京市通州区北苑广通小区7号楼1单元502室", "家庭住址"),
    ("家庭住址：湖北省武汉市洪山区珞喻路1037号", "家庭住址"),
    ("邮政编码：101100", "邮政编码"),
    ("E-mail：someone@example.com", "邮箱"),
    ("单位地址：北京市海淀区学院路37号", "含「学院」二字的地址"),
    # 既有的整行反例（docstring 里写着的历史教训）
    ("山西大学，2024年8月12-13日（会议信息，含日期）", "会议信息含日期"),
    ("相关地址信息：For information, address State University of New York Press…网址：www.x.com",
     "出版社地址 + 网址"),
    ("英文题目：A Historical Review. Huazhong University of Science and Technology.",
     "英文题目"),
    ("1. 尚未形成对论证行动完整生成过程的统一解释。", "列表项"),
    ("20世纪逻辑学在中山大学得到发展", "以数字起头的正文"),
    ("华中科技大学逻辑教育二十年回顾与展望", "题名本身含机构词"),
    ("本文认为，武汉大学哲学系的研究传统值得重视。", "正文口吻"),
    ("人文学院、公管学院、社会学院、法学院等学院将《逻辑学》纳入培养计划，设为必修课。",
     "正文里的单位罗列（含书名号）"),
    ("桑木严翼（1874-1946），哲学家，东京帝国大学名誉教授。著有《哲学概论》等。",
     "文献里被研究的学者"),
]
for line, why in _MUST_EMPTY:
    check(f"② 不许命中：{why} → ''", C(line) == "", f"{C(line)!r}  ←  {line[:40]!r}")

# 邮箱绝不进单位（03 Yuanchang_He 的历史教训）
_EMAIL_LINE = "作者简介：何远长，武汉大学哲学学院博士生，563481898@qq.com"
check("② `作者简介：…563481898@qq.com`：邮箱不许当单位，单位本身照取",
      C(_EMAIL_LINE) == "武汉大学哲学学院"
      and "563481898" not in C(_EMAIL_LINE) and "@" not in C(_EMAIL_LINE), repr(C(_EMAIL_LINE)))
_EMAIL_LINE2 = ("［作者简介］张燕京，河北大学哲学与社会学学院教授、博士生导师，"
                "zhangyanjing@hbu.edu.cn；王琪，河北大学哲学与社会学学院博士研究生，"
                "邮箱：15135215246@163.com。")
check("② 一行里两个邮箱 + 两个职衔：结果里既没有邮箱也没有职衔",
      C(_EMAIL_LINE2) == "河北大学哲学与社会学学院", repr(C(_EMAIL_LINE2)))

# 05 Guoping_Du：`…智能与逻辑实验室主任` 的行政职务后缀 → 整句放弃，不许冒出「实验室」
_GUOPING_BIO = ("作者简介：杜国平（1965-），男，汉族，江苏盱眙人，博士，"
                "中国社会科学院哲学研究所二级教授，博士生导师，"
                "中国社会科学院智能与逻辑实验室主任。研究方向：智能逻辑。")
check("② 05 Guoping_Du 的长简介只取到「中国社会科学院哲学研究所」",
      C(_GUOPING_BIO) == "中国社会科学院哲学研究所", repr(C(_GUOPING_BIO)))
check("② 行政职务（实验室主任）后缀的句子被整句放弃，**不**产生「…实验室」这个新单位",
      "实验室" not in C(_GUOPING_BIO), repr(C(_GUOPING_BIO)))
check("② 05 Guoping_Du 的尾块「工作单位：A、B」至少不产生电话/住址；取到的是机构名",
      all(("电话" not in x and "地址" not in x and "101100" not in x)
          for x in [C("工作单位：中国社会科学院哲学研究所、中国社会科学院大学哲学院"),
                    C("联系电话：18901393186"),
                    C("通信地址：北京市通州区北苑广通小区7号楼1单元502室"),
                    C("邮政编码：101100")]),
      repr(C("工作单位：中国社会科学院哲学研究所、中国社会科学院大学哲学院")))

# ── ③ 非回归：改动前非空的行，结果逐字不变 ───────────────────────────────
print("\n── ③ 非回归：stage ① 本来能识别的行逐字不变（只可能「空 → 非空」）──")

_REAL_LINES = [
    "浙江大学哲学学院",
    "北京大学外国哲学研究所",
    "武汉大学哲学系",
    "（兰州大学 哲学社会学院，兰州 730000）",
    "广东第二师范学院马克思主义学院 广州 510303",
    "焦雯雯 中山大学哲学系",
    "甘肃政法大学 卢俐利",
    "作者信息：周振忠 中山大学逻辑与认知研究所",
    "School of Philosophy and Social Development, Shandong University",
    "Jingru Kou   College of Philosophy, Nankai University",
    "（中国社会科学院大学哲学院，北京102401；中国社会科学院哲学研究所，北京100732）",
    "[通讯作者] 南京大学哲学学院",
    "作者简介：周志荣，男，1982-，江苏南京人，中南财经政法大学哲学院副教授。",
]
_regress = [(ln, old_clean_affil_line(ln), C(ln)) for ln in _REAL_LINES
            if old_clean_affil_line(ln) and old_clean_affil_line(ln) != C(ln)]
check("③ 14 条真实单位行里，旧实现非空的结果与现在**逐字完全相同**", not _regress, f"{_regress}")
check("③ 单独一行 `武汉大学哲学系` 结果逐字不变",
      old_clean_affil_line("武汉大学哲学系") == C("武汉大学哲学系") == "武汉大学哲学系", "")

# 真实稿里**实际喂进去的每一行**：旧非空 ⇒ 新相同（这是本缺陷的核心不变量）
_SAW: list[tuple[str, str, str]] = []
_real_clean = D._clean_affil_line


def _spy(line):
    new = _real_clean(line)
    _SAW.append((line, old_clean_affil_line(line), new))
    return new


_DOCS = [("Bo_Chen", BO_CHEN), ("Yuanchang_He", YUANCHANG), ("Guoping_Du", GUOPING)] + OTHER_DOCS
_checked_docs = []
if not os.path.isfile(BO_CHEN):
    skip("③ 真实稿逐行不变量（Bo_Chen 等）", f"缺真实稿件（{BO_CHEN}）")
else:
    from slcn import sources  # noqa: E402

    D._clean_affil_line = _spy
    try:
        for nm, src in _DOCS:
            ss = sources.identify(src) if os.path.isdir(src) else None
            docx = ss.docx if ss else (src if os.path.isfile(src) else None)
            if not docx:
                continue
            try:
                D.parse_docx(docx)
            except Exception as exc:  # noqa: BLE001
                skip(f"③ 真实稿 {nm}", f"解析失败（{exc!r}）")
                continue
            _checked_docs.append(nm)
    finally:
        D._clean_affil_line = _real_clean

_broke = [(ln, o, n) for ln, o, n in _SAW if o and o != n]
_gain_lines = sorted({(ln, n) for ln, o, n in _SAW if not o and n})
check(f"③ {len(_checked_docs)} 篇真实稿、{len(_SAW)} 次行级调用里，**旧非空却被改动的 = 0 行**",
      not _broke, f"{_broke[:5]}")
if not _checked_docs:
    # ⚠️ 一篇真实稿都没跑到（公开的工具仓里没有稿件库）→ **SKIP 而不是 FAIL**：
    #    空集上的「全部满足」是假命题，会让「缺数据」看起来像「规则坏了」。
    skip("③ 收益行判据（需要真实稿）", "本机没有稿件库，跳过")
else:
    check("③ 旧实现取不到、新实现取到了的行全部是「作者简介/单位」类（收益）",
          all(re.search(r"作者简介|工作单位|作者信息|［作者简介］", ln) for ln, _ in _gain_lines),
          f"{[ln[:28] for ln, _ in _gain_lines]}")
if _checked_docs:
    check("③ 至少有一条真实稿的收益行就是案例 75 的那一行",
          any("作者简介" in ln and "武汉大学哲学系教授" in ln for ln, _ in _gain_lines), "")

# ── ④ 端到端：parse_docx 的真实值 ────────────────────────────────────────
print("\n── ④ 端到端：parse_docx 的单位值 ──")


def aff_of(src):
    """返回 (中文单位, 英文单位)；src 可以是文件或目录（目录走 sources.identify）。"""
    if os.path.isdir(src):
        from slcn import sources

        ss = sources.identify(src)
        if not ss.docx:
            return None
        src = ss.docx
    doc = D.parse_docx(src)
    return doc


if not os.path.isfile(BO_CHEN):
    skip("④ Bo_Chen 端到端", f"缺真实稿件（{BO_CHEN}）")
else:
    _doc = aff_of(BO_CHEN)
    check("④ 案例 75：Bo_Chen 的 `affiliation_cn` = `武汉大学哲学系`（原来是空）",
          _doc.affiliation_cn == "武汉大学哲学系", repr(_doc.affiliation_cn))
    check("④ Bo_Chen 的 `affiliation_cn` 不带邮箱/职衔",
          "@" not in _doc.affiliation_cn and "教授" not in _doc.affiliation_cn,
          repr(_doc.affiliation_cn))

for nm, path in _DOCS:
    if not os.path.isdir(path):
        continue
    _old, _new = {}, {}
    for tag, impl, box in (("old", old_clean_affil_line, _old), ("new", _real_clean, _new)):
        D._clean_affil_line = impl
        try:
            doc = aff_of(path)
        finally:
            D._clean_affil_line = _real_clean
        if doc is not None:
            box["cn"], box["en"] = doc.affiliation_cn or "", doc.affiliation_en or ""
    if not _old:
        skip(f"④ {nm}", "identify() 没选出正文 docx")
        continue
    check(f"④ {nm}：旧非空的中文单位与现在逐字相同（{_old['cn']!r}）",
          (not _old["cn"]) or _old["cn"] == _new["cn"], repr(_new["cn"]))
    check(f"④ {nm}：旧非空的英文单位与现在逐字相同",
          (not _old["en"]) or _old["en"] == _new["en"], repr(_new["en"]))
    check(f"④ {nm}：不会「非空 → 空」", (not _old["cn"]) or _new["cn"], "")

# `_affil_lines_to_units` 的空白归一：只在**同一次调用内**去重，跨调用保持历史行为
print("\n── ④b 空白归一去重（案例 75 的连带修正）──")
_cn_a, _ = D._affil_lines_to_units(
    ["（河北大学 哲学与社会学学院，河北 保定 071002）",
     "［作者简介］张燕京，河北大学哲学与社会学学院教授、博士生导师。"])
check("④b 同一次调用里「带空格 / 不带空格」的同一单位只留一条",
      _cn_a == ["河北大学 哲学与社会学学院"], f"{_cn_a}")
_cn_b, _ = D._affil_lines_to_units(["中山大学 逻辑与认知研究所"])
_cn_c, _ = D._affil_lines_to_units(["中山大学逻辑与认知研究所"])
check("④b 跨调用**不**归并（2024_02_Kai_Qu 的历史重复照旧，本轮不许顺手改）",
      _cn_b == ["中山大学 逻辑与认知研究所"] and _cn_c == ["中山大学逻辑与认知研究所"], f"{_cn_b} / {_cn_c}")

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
