#!/usr/bin/env python3.14
r"""批71 卡4（B-118）：Word 作者信息脚注 → 元数据填空（只填空不覆盖 / 不猜）。

缺陷（第90期真稿实测；本套件只用**脱敏合成** docx，不含任何真实作者信息）
----------------------------------------------------------------------------
作者信息常写在 Word 的 `word/footnotes.xml` 里，形如：

    作者信息：张三  某某大学哲学系
    zhangsan@example.edu.cn
    基金项目：国家社科基金青年项目"测试项目"（22CZX000）。

旧实现只把它当「纯注释」丢掉：多作者只解析第一作者、第二作者起的 CN 单位/邮箱
没有来源（永远占位符）、`作者简介：` 叙事格式里的单位/邮箱取不到。第90期 39 处
占位符里 22 处是作者元数据（01/02/04/06/07/08/10 七篇）。

修法（`slcn/footnotes.py` 新增解析 + `slcn/docxsource.py`/`slcn/build.py` 落位）
------------------------------------------------------------------------------
① `footnotes.parse_author_info()`：识别 作者信息/作者简介/收稿日期/基金项目（含
   全角冒号、无空格、多段、多作者并列、中英并列、叙事格式），产出**作者级**结构化
   元数据（姓名/中英文单位/邮箱 + 收稿日期/基金项目）；无法判定不猜 + warning。
   既有 `classify()` 的引文类分支**一行未改**。
② `docxsource.parse_docx()` 把结果放到 `DocxDoc.author_meta / received / grants`；
   `doc.author / affiliation_* / email` **只填空不覆盖**；正文里独立的
   `基金项目：…` 行（90-08 型）提升为元数据并从正文移出。
③ `build.build_docx()` 用它补第二作者起的槽位（`mysecondaffiliation/email/...`）、
   收稿日期与基金项目；所有槽位以「当前是否已解析」为门 —— 人工表单/CLI/已有识别值
   一律优先。基金文本按 LaTeX 转义（`&` → `\&`）。

本套件钉住（spec §2.3 六形态 + 「只填空不覆盖」+ 负例逐字节同）
--------------------------------------------------------------
  ① 单作者 `作者信息：姓名 单位` + 邮箱 + 收稿日期/基金项目；
  ② 多作者中英并列（括号英译名/单位）+ 每位邮箱 → 第二作者槽位补齐；
  ③ 叙事格式 `作者简介：姓名，男，籍贯，单位教授。电子邮箱：…`（性别/籍贯不当单位）；
  ④ 多单位 + `通讯作者：` 独立一段；
  ⑤ 收稿日期 + 基金项目同条（含 `（项目编号：21&ZX0001）` 的清洗/转义）；
  ⑥ 无作者信息负例：产物（tex+bib）与「关闭新解析」逐字节同；
  ⑦ 只填空不覆盖：正文作者名/单位优先，脚注里另一个名字不许顶替；
  ⑧ 解析器直接断言：噪声/文献脚注不误判、标签缺失不猜、基金只在正文（90-08 型）
     只填基金不臆造作者。

不联网、不写仓库（全部 tempfile）、不跑 corpus.py / build.sh / 真实稿件库。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
        tests/test_b71_word_author_meta.py
"""

import os
import re
import sys
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build, docxsource, footnotes  # noqa: E402

FAILS = []
TMPROOT = tempfile.mkdtemp(prefix="b71_author_meta_")

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
)


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def _xesc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _p(text):
    return ('<w:p><w:r><w:t xml:space="preserve">' + _xesc(text)
            + "</w:t></w:r></w:p>")


def make_docx(paras, footnotes_=()):
    """合成最小 docx（正文段落 + 脚注段落组）；返回路径。"""
    fd, path = tempfile.mkstemp(suffix=".docx", dir=TMPROOT)
    os.close(fd)
    doc_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<w:document xmlns:w="{_W_NS}"><w:body>'
               + "".join(_p(t) for t in paras) + "</w:body></w:document>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc_xml)
        if footnotes_:
            fn_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                      f'<w:footnotes xmlns:w="{_W_NS}">'
                      + "".join(
                          f'<w:footnote w:id="{i}">'
                          + "".join(_p(t) for t in group) + "</w:footnote>"
                          for i, group in enumerate(footnotes_, start=1))
                      + "</w:footnotes>")
            z.writestr("word/footnotes.xml", fn_xml)
    return path


def body_paras(title, author, affil="", abstract="摘 要：本文是一段用于测试的合成摘要，"
               "内容足够长以便解析器认定为正文摘要。",
               keywords="关键词：测试；样例；元数据", tail="正文第一段，用于占位。"):
    paras = [title]
    if author:
        paras.append(author)
    if affil:
        paras.append(affil)
    paras.extend([abstract, keywords, tail])
    return paras


def ph(tex):
    return re.findall(r"【未识别：[^】]*】", tex)


def build_case(path, *, disable_author_info=False):
    """跑一次 build_docx；disable=True 时把新解析器关掉（= 改动前行为）。"""
    out = tempfile.mkdtemp(prefix="b71_out_", dir=TMPROOT)
    orig = footnotes.parse_author_info
    if disable_author_info:
        footnotes.parse_author_info = lambda *a, **k: None
    try:
        res = build.build_docx(docx_path=path, out_dir=out, basename="Case",
                               lang="zh", verbose=False)
        tex = open(res.tex_path, encoding="utf-8").read()
        bib_p = os.path.join(out, "reference.bib")
        bib = open(bib_p, encoding="utf-8").read() if os.path.isfile(bib_p) else ""
        return tex, bib
    finally:
        footnotes.parse_author_info = orig


def parse(path):
    return docxsource.parse_docx(path, c_class_union=True)


# ── ① 单作者：`作者信息：姓名 单位` + 邮箱 + 收稿日期/基金项目 ──────────────
print("\n── ① 单作者「作者信息：姓名 单位」+ 邮箱 ──")
_p1 = make_docx(body_paras("测试题名一", "张三"), footnotes_=[
    ["作者信息：张三  北京大学哲学系", "zhangsan@example.edu.cn",
     "收稿日期：2025-09-19  基金项目：国家社会科学基金青年项目"
     "“测试项目”（22CZX000）。"],
])
_d1 = parse(_p1)
check("① doc.author = 张三", _d1.author == "张三", repr(_d1.author))
check("① 作者记录 1 条", len(_d1.author_meta) == 1, f"{_d1.author_meta}")
_r1 = _d1.author_meta[0] if _d1.author_meta else {}
check("① 单位 = 北京大学哲学系", _r1.get("affiliation_cn") == ["北京大学哲学系"],
      f"{_r1.get('affiliation_cn')}")
check("① 邮箱落元数据", _r1.get("email") == "zhangsan@example.edu.cn",
      f"{_r1.get('email')}")
check("① 收稿日期归一为 2025-09-19", _d1.received == "2025-09-19", repr(_d1.received))
check("① 基金项目进 grants（不含『基金项目：』标签）",
      _r1 and "22CZX000" in _d1.grants and "基金项目" not in _d1.grants,
      repr(_d1.grants))
_tex1, _ = build_case(_p1)
check("① 产物无第一作者 CN 槽位占位符",
      not [x for x in ph(_tex1) if "第一作者" in x and "英文" not in x],
      f"{ph(_tex1)}")
check("① 产物含作者名/单位/邮箱",
      "张三" in _tex1 and "北京大学哲学系" in _tex1
      and "zhangsan@example.edu.cn" in _tex1)
check("① 产物含收稿日期与基金项目", "2025-09-19" in _tex1 and "22CZX000" in _tex1)
check("① 单位碎片（姓名/性别/籍贯）不进单位字段",
      not any(x in "".join(_r1.get("affiliation_cn") or []) for x in ("张三", "男", "人")))

# 无空格变体（`作者信息:张三北京大学哲学系`，半角冒号 + 姓名单位连写）
print("\n── ①b 全角/半角冒号、无空格变体 ──")
_p1b = make_docx(body_paras("测试题名一b", ""), footnotes_=[
    ["作者信息:李四北京大学哲学系", "lisi@example.edu.cn"],
])
_d1b = parse(_p1b)
check("①b 连写「姓名+单位」仍能切出姓名", bool(_d1b.author_meta)
      and _d1b.author_meta[0].get("name") == "李四", f"{_d1b.author_meta}")
check("①b 无空格变体的单位正确",
      bool(_d1b.author_meta)
      and _d1b.author_meta[0].get("affiliation_cn") == ["北京大学哲学系"],
      f"{_d1b.author_meta[:1]}")

# ── ② 多作者中英并列 ────────────────────────────────────────────────────
print("\n── ② 多作者中英并列（括号英译名/单位）──")
_p2 = make_docx(body_paras("测试题名二", "张晓 李晓"), footnotes_=[
    ["作者信息：张晓（Zhang Xiao）  北京大学哲学系（Department of Philosophy, "
     "Peking University）  zhangxiao@example.edu.cn",
     "李晓（Li Xiao）  清华大学哲学系（Department of Philosophy, Tsinghua "
     "University）  lixiao@example.edu.cn"],
])
_d2 = parse(_p2)
check("② 解析出 2 位作者", len(_d2.author_meta) == 2, f"{_d2.author_meta}")
check("② 第一作者姓名/英译名",
      _d2.author_meta[0].get("name") == "张晓"
      and _d2.author_meta[0].get("name_en") == "Zhang Xiao", f"{_d2.author_meta[:1]}")
check("② 第二作者姓名/英译名",
      _d2.author_meta[1].get("name") == "李晓"
      and _d2.author_meta[1].get("name_en") == "Li Xiao", f"{_d2.author_meta[1:]}")
check("② 第二作者中文单位 = 清华大学哲学系",
      _d2.author_meta[1].get("affiliation_cn") == ["清华大学哲学系"],
      f"{_d2.author_meta[1].get('affiliation_cn')}")
check("② 第二作者英文单位按括号解析",
      _d2.author_meta[1].get("affiliation_en") == ["Department of Philosophy, "
                                                   "Tsinghua University"],
      f"{_d2.author_meta[1].get('affiliation_en')}")
check("② 第二作者邮箱", _d2.author_meta[1].get("email") == "lixiao@example.edu.cn",
      f"{_d2.author_meta[1].get('email')}")
_tex2, _ = build_case(_p2)
_ph2 = ph(_tex2)
check("② 产物无第二作者类占位符",
      not [x for x in _ph2 if "第二作者" in x], f"{_ph2}")
check("② 产物第二作者中文槽位 = 李晓 / 清华大学哲学系",
      "李晓" in _tex2 and "清华大学哲学系" in _tex2)
check("② 产物第二作者英文槽位 = Li Xiao / Tsinghua 单位",
      "Li Xiao" in _tex2 and "Tsinghua" in _tex2)
check("② 产物第二作者邮箱", "lixiao@example.edu.cn" in _tex2)
check("② 第一作者英文姓名由脚注补上（源稿无英文作者行）", "Zhang Xiao" in _tex2)

# ── ③ 叙事格式「作者简介」─────────────────────────────────────────────────
print("\n── ③ 叙事格式「作者简介：姓名，男，…，单位教授。电子邮箱：…」──")
_p3 = make_docx(body_paras("测试题名三", "李明远"), footnotes_=[
    ["作者简介：李明远，男，江苏南京人，华东师范大学哲学系教授。"
     "电子邮箱：limingyuan@example.edu.cn"],
])
_d3 = parse(_p3)
check("③ 叙事格式仍解析出作者", _d3.author_meta
      and _d3.author_meta[0].get("name") == "李明远", f"{_d3.author_meta}")
check("③ 单位只取机构名（职衔剥离）",
      _d3.author_meta
      and _d3.author_meta[0].get("affiliation_cn") == ["华东师范大学哲学系"],
      f"{_d3.author_meta[:1]}")
check("③ 性别/籍贯不当单位",
      _d3.author_meta
      and not any(w in "".join(_d3.author_meta[0].get("affiliation_cn") or [])
                  for w in ("男", "江苏南京人", "教授")))
check("③ 电子邮箱从叙事句里解析",
      _d3.author_meta
      and _d3.author_meta[0].get("email") == "limingyuan@example.edu.cn",
      f"{_d3.author_meta[0].get('email') if _d3.author_meta else None}")
_tex3, _ = build_case(_p3)
check("③ 产物无第一作者单位/邮箱占位符",
      not [x for x in ph(_tex3) if "第一作者单位" in x or "第一作者邮箱" in x],
      f"{ph(_tex3)}")
check("③ 产物含叙事里的单位与邮箱",
      "华东师范大学哲学系" in _tex3 and "limingyuan@example.edu.cn" in _tex3)

# ── ④ 多单位 + 通讯作者 ──────────────────────────────────────────────────
print("\n── ④ 多单位 / 通讯作者 另立一段 ──")
_p4 = make_docx(body_paras("测试题名四", "王晨 赵天"), footnotes_=[
    ["作者信息：王晨  复旦大学哲学学院", "复旦大学现代哲学研究所",
     "wangchen@example.edu.cn",
     "通讯作者：赵天  同济大学人文学院", "zhaotian@example.edu.cn"],
])
_d4 = parse(_p4)
check("④ 解析出 2 位作者", len(_d4.author_meta) == 2, f"{_d4.author_meta}")
check("④ 第一作者两个单位（保持顺序）",
      _d4.author_meta[0].get("affiliation_cn")
      == ["复旦大学哲学学院", "复旦大学现代哲学研究所"],
      f"{_d4.author_meta[0].get('affiliation_cn')}")
check("④ 通讯作者姓名/单位/邮箱",
      _d4.author_meta[1].get("name") == "赵天"
      and _d4.author_meta[1].get("affiliation_cn") == ["同济大学人文学院"]
      and _d4.author_meta[1].get("email") == "zhaotian@example.edu.cn",
      f"{_d4.author_meta[1:]}")
_tex4, _ = build_case(_p4)
_ph4 = ph(_tex4)
check("④ 产物无第二作者单位/邮箱占位符",
      not [x for x in _ph4 if "第二作者单位" in x or "第二作者邮箱" in x], f"{_ph4}")
check("④ 产物第二作者两个中文单位（`\\\\` 分行）",
      "复旦大学哲学学院" in _tex4 and "复旦大学现代哲学研究所" in _tex4
      and "同济大学人文学院" in _tex4)
check("④ 通讯作者邮箱进第二作者邮箱槽位", "zhaotian@example.edu.cn" in _tex4)

# ── ⑤ 收稿日期 + 基金项目同条（清洗项目编号 + LaTeX 转义）────────────────
print("\n── ⑤ 收稿日期 + 基金项目同条 ──")
_p5 = make_docx(body_paras("测试题名五", ""), footnotes_=[
    ["收稿日期：2026-01-05  基金项目：国家社会科学基金一般项目"
     "“测试项目二”（项目编号：21&ZX0001）。"],
])
_d5 = parse(_p5)
check("⑤ 收稿日期 = 2026-01-05", _d5.received == "2026-01-05", repr(_d5.received))
check("⑤ 基金项目取到项目号", "21&ZX0001" in _d5.grants, repr(_d5.grants))
_tex5, _ = build_case(_p5)
check("⑤ 产物收稿日期已填", "2026-01-05" in _tex5)
check("⑤ 产物基金：去掉『项目编号：』", "项目编号" not in _tex5)
check("⑤ 产物基金：`&` 已转义为 `\\&`", "21\\&ZX0001" in _tex5)
check("⑤ 产物基金末尾补句号", "（21\\&ZX0001）。" in _tex5)

# ── ⑤b 基金项目只在正文（90-08 型）：只填基金、不臆造作者 ────────────────
print("\n── ⑤b 基金项目只在正文：只填基金、不臆造作者 ──")
_body5b = body_paras("测试题名五b", "")
_body5b.insert(2, "基金项目：国家社会科学基金青年项目“测试项目三”（22CZX0003）")
_p5b = make_docx(_body5b)
_d5b = parse(_p5b)
check("⑤b 正文基金行解析进 grants", "22CZX0003" in _d5b.grants, repr(_d5b.grants))
check("⑤b 正文基金行不从正文重复输出（正文段落已移出）",
      "基金项目：国家社会科学基金青年项目" not in _d5b.body)
check("⑤b 不臆造作者（无 author_meta、作者仍为空）",
      not _d5b.author_meta and not _d5b.author, f"{_d5b.author_meta!r}/{_d5b.author!r}")
_tex5b, _ = build_case(_p5b)
check("⑤b 产物基金项目已填", "22CZX0003" in _tex5b)
check("⑤b 产物作者仍是占位符（没被基金脚注臆造）",
      any("第一作者" in x for x in ph(_tex5b)), f"{ph(_tex5b)}")

# ── ⑥ 无作者信息负例：产物逐字节同 ──────────────────────────────────────
print("\n── ⑥ 无作者信息负例：产物逐字节同 ──")
_p6 = make_docx(body_paras("测试题名六", "钱七"), footnotes_=[
    ["参见某某：《测试文献》，北京：商务印书馆，2020年，第3页。"],
    ["这里是一条普通的说明性脚注，没有作者信息标签。"],
])
tex6_new, bib6_new = build_case(_p6)
tex6_old, bib6_old = build_case(_p6, disable_author_info=True)
check("⑥ 无作者信息 → 新解析产物与改动前逐字节同（tex）", tex6_new == tex6_old,
      f"{len(tex6_new)} vs {len(tex6_old)}")
check("⑥ 无作者信息 → 新解析产物与改动前逐字节同（bib）", bib6_new == bib6_old)
check("⑥ 负例不产生作者记录", not parse(_p6).author_meta)

# ── ⑦ 只填空不覆盖：正文作者优先 ────────────────────────────────────────
print("\n── ⑦ 只填空不覆盖：正文作者优先 ──")
_p7 = make_docx(body_paras("测试题名七", "张三", affil="清华大学哲学系"), footnotes_=[
    ["作者信息：李四  北京大学哲学系", "lisi@example.edu.cn"],
])
_d7 = parse(_p7)
check("⑦ doc.author 仍是正文的张三（不被脚注的李四覆盖）", _d7.author == "张三",
      repr(_d7.author))
_tex7, _ = build_case(_p7)
check("⑦ 产物第一作者 = 张三", "张三" in _tex7)
check("⑦ 产物第一作者单位保留正文明示的清华大学哲学系（旧行为的多单位合并照旧）",
      "清华大学哲学系" in _tex7)
check("⑦ 姓名对不上的脚注记录不顶替作者槽位", "李四" not in _tex7)
check("⑦ 已有识别值时新代码不新增 CN 作者占位符",
      not [x for x in ph(_tex7) if "第一作者" in x and "英文" not in x],
      f"{ph(_tex7)}")

# ── ⑧ 解析器直接断言：不猜 / 不误判 ─────────────────────────────────────
print("\n── ⑧ parse_author_info 直接断言：不猜、不误判 ──")
check("⑧ 文献脚注（含 University/年份）不当作者信息",
      footnotes.parse_author_info(
          "[1] J. Smith, The Theory, Oxford: Oxford University Press, 2020, p. 12."
      ) is None)
check("⑧ 正文句里的『基金项目』（无行首标签）不误判",
      footnotes.parse_author_info("本文受基金项目资助，特此致谢。") is None)
check("⑧ 无标签的英文引文脚注不误判",
      footnotes.parse_author_info(
          "Harry Frankfurt, “The Importance of What We Care About,” p. 262."
      ) is None)
_amb = footnotes.parse_author_info("作者信息：张三丰北京大学哲学系")
check("⑧ 未知连写姓名无法可靠切分 → 不猜（即使产生记录也不得写成张三/张三丰）",
      not _amb or not any((r.get("name") or "") in ("张三", "张三丰")
                          for r in _amb.get("authors") or []),
      f"{_amb}")
_warn = footnotes.parse_author_info("作者信息：某某某（无单位无邮箱）")
check("⑧ 只有标签+碎片 → 保持不到作者信息（不猜）",
      not _warn or not _warn.get("authors") or all(
          not r.get("affiliation_cn") and not r.get("email")
          for r in _warn.get("authors") or []), f"{_warn}")
_known_kw = dict(known_cn=("张晓", "李晓"), known_en=("Zhang Xiao", "Li Xiao"))
_cn_info = footnotes.parse_author_info(
    "作者信息：张晓  北京大学哲学系  zhangxiao@example.edu.cn\n"
    "李晓  清华大学哲学系  lixiao@example.edu.cn", **_known_kw)
_unl = footnotes.parse_author_info(
    "Zhang Xiao  Department of Philosophy, Peking University\n"
    "zhangxiao@example.edu.cn\n"
    "Li Xiao  Department of Philosophy, Tsinghua University\n"
    "lixiao@example.edu.cn", **_known_kw)
check("⑧ 无标签英文作者块（已知名单全命中）→ 解析出 2 人",
      bool(_unl) and len(_unl.get("authors") or []) == 2, f"{_unl}")
_merged = footnotes.merge_author_info([_cn_info, _unl], **_known_kw)
check("⑧ 无标签英文块按邮箱对齐中文记录（name/name_en/EN 单位合并）",
      len(_merged["authors"]) == 2
      and _merged["authors"][1].get("name") == "李晓"
      and _merged["authors"][1].get("name_en") == "Li Xiao"
      and _merged["authors"][1].get("email") == "lixiao@example.edu.cn"
      and _merged["authors"][1].get("affiliation_en")
      == ["Department of Philosophy, Tsinghua University"],
      f"{_merged['authors']}")
_fund = footnotes.parse_fund_line("基金项目：国家社科基金项目（22CZX000）")
check("⑧ parse_fund_line 取内容且不吞标签", _fund == "国家社科基金项目（22CZX000）",
      repr(_fund))
check("⑧ parse_fund_line 对正文句子不命中（不以标签开头）",
      footnotes.parse_fund_line("本文基金项目：参见附注。") == "")

# ── 汇总 ────────────────────────────────────────────────────────────────
print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
