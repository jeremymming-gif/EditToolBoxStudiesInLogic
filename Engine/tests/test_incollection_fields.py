#!/usr/bin/env python3.14
"""`@incollection`（`[C]//`）**已经解析到的字段被丢掉**（第 81 条）。

真实来稿 `2026年第5期(90)/10 Bo_Chen` 的源稿行：

    塔斯基. 2019. 真理的语义概念和语义学的基础[C]// 马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.

`parse_reference_line()` 把 `rest` 整段拿全了（`马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.`），
`refs_to_bib()` 的字段表里却**从来没有 booktitle/editor**，而 address/publisher 又被
`if etype == "book"` 挡住 —— 于是**已经解析到手**的「商务印书馆 / 北京」被静默丢掉。
回读校验报告因此在这条上写「❗必补：booktitle」，而它下一行贴出的源稿原文里**明明写着书名**
—— 自相矛盾（与第 66 / 68 条同一型事故：喂给 AI 的本来就是残缺件）。

本测试守住（宁缺勿编是红线，第 61 条口径「编错的字段比空缺更糟」）：
  ① 正例：Tarski2019 → `@incollection` + `booktitle` / `address` / `publisher`，值逐字来自源稿；
  ② 正例：Steinberger2022 的 `… (eds.), 书名, https:…` 形态 → `booktitle` + `editor`，
     且 URL 的冒号**不许**被当成「出版地: 出版者」；
  ③ **反例 1**：`[M]` / `[J]` 条目**不许**获得 `booktitle`（booktitle 只属于 incollection）；
  ④ **反例 2**：`[C]//` 但书名拿不准 → **不许编** `booktitle`（6 种残缺/歧义形态）；
  ⑤ **反例 3**：没有编者标记的条目**不许**冒出 `editor`；
  ⑥ **反例 4**：`@book` / `@article` 的既有输出**逐字不变**（黄金字面量；含「版次/中译加注
     本轮不动」的钉子 `Haack1974`）；
  ⑦ **反例 5**：条目数与键序列不变（`make_key` 一个字没动）；
  ⑧ 端到端：现造最小 docx + 真实稿 `10 Bo_Chen`（语料不在本机则 SKIP）。

不写 out/、不联网、不跑 corpus.py / build.sh。用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_incollection_fields.py
"""
import os
import re
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_ENGINE))
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.docxsource import make_key, parse_docx, parse_reference_line, refs_to_bib  # noqa: E402

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


def bib_of(line: str) -> str:
    e = parse_reference_line(line)
    assert e is not None, f"解析失败：{line}"
    return refs_to_bib([e])


def fields_of(bib: str) -> list[str]:
    return re.findall(r"^\s*([A-Za-z_]+)\s*=", bib, re.M)


# ══ ① 正例：Tarski2019（真实源稿行）══════════════════════════════════════════
print("── ① 正例：`[C]//` 的 booktitle / address / publisher ──")

TARSKI = ("塔斯基. 2019. 真理的语义概念和语义学的基础[C]// "
          "马蒂尼奇. 语言哲学[M]. 牟博, 译. 北京: 商务印书馆.")
TARSKI_BIB = (
    "% 由 Word 参考文献自动生成，请核对类型与字段。\n\n"
    "@incollection{Tarski2019,\n"
    "  author    = {塔斯基},\n"
    "  title     = {真理的语义概念和语义学的基础},\n"
    "  booktitle = {语言哲学},\n"
    "  publisher = {商务印书馆},\n"
    "  address   = {北京},\n"
    "  year      = {2019},\n"
    "  userf     = {cn}\n"
    "}\n"
)
_t = parse_reference_line(TARSKI)
check("① 类型 = @incollection", _t is not None and _t.kind == "C" and _t.key == "Tarski2019",
      f"kind={_t.kind if _t else None} key={_t.key if _t else None}")
_tb = bib_of(TARSKI)
check("① 整条输出 = 黄金字面量（booktitle/publisher/address 都在，键不变）",
      _tb == TARSKI_BIB, repr(_tb))
check("① 有 booktitle = 《语言哲学》", "booktitle = {语言哲学}" in _tb)
check("① 有 publisher = {商务印书馆}", "publisher = {商务印书馆}" in _tb)
# 旧 `_split_publisher()` 在这条上会把地址取成「译. 北京」（逗号把它截在半截上）——
# 这是本条的第二个真缺陷，地址必须是干净的「北京」。
check("① 有 address = {北京}（不是「译. 北京」）",
      "address   = {北京}" in _tb and "译. 北京" not in _tb)
check("① 字段序照规范：title → booktitle → publisher → address → year",
      fields_of(_tb) == ["author", "title", "booktitle", "publisher", "address", "year", "userf"],
      repr(fields_of(_tb)))

# ══ ② 正例：Steinberger2022（`(eds.)` 明确编者形态 + URL）════════════════════
print("\n── ② 正例：明确编者标记 `(eds.)` 与 URL 的冒号 ──")

STEIN = ("Steinberger, F. 2022. The Normative Status of Logic[C]// "
         "Zalta, E N. & Nodelman, U (eds.), The Stanford Encyclopedia of Philosophy, "
         "https://plato.stanford.edu/archives/win2022/entries/logic-normative/.")
STEIN_BIB = (
    "% 由 Word 参考文献自动生成，请核对类型与字段。\n\n"
    "@incollection{Steinberger2022,\n"
    "  author    = {Steinberger, F},\n"
    "  title     = {The Normative Status of Logic},\n"
    "  booktitle = {The Stanford Encyclopedia of Philosophy},\n"
    "  editor    = {Zalta, E N. and Nodelman, U},\n"
    "  year      = {2022}\n"
    "}\n"
)
_sb = bib_of(STEIN)
check("② 整条输出 = 黄金字面量（booktitle + editor）", _sb == STEIN_BIB, repr(_sb))
check("② booktitle = The Stanford Encyclopedia of Philosophy（不是编者串）",
      "booktitle = {The Stanford Encyclopedia of Philosophy}" in _sb)
check("② editor = 编者倒装规范化（`&` → `and`）",
      "editor    = {Zalta, E N. and Nodelman, U}" in _sb)
check("② URL 没有被当成出版信息（不写 address/publisher）",
      "address" not in _sb and "publisher" not in _sb and "//plato" not in _sb
      and "https" not in _sb)

# ══ ③ 反例 1：`[M]` / `[J]` 不许有 booktitle ════════════════════════════════
print("\n── ③ 反例 1：`[M]` / `[J]` 与 booktitle 无关 ──")

BOOK_BEALL = "Beall, J C, Restall, G. 2006. Logical Pluralism[M]. Oxford: Oxford University Press."
ART_HAACK76 = "Haack, S. 1976. Justification of Deduction[J]. Mind, 85(337): 112-119."
for _name, _line in (("book", BOOK_BEALL), ("article", ART_HAACK76)):
    _b = bib_of(_line)
    check(f"③ {_name} 条目**无** booktitle", "booktitle" not in _b, repr(_b))
# 书名里带 `[C]//` 字样也不该被 incollection 逻辑波及（kind 不是 C）
_b = bib_of("Smith, J. 2005. Chapters and Selections[M]. Oxford: Oxford University Press.")
check("③ `[M]` 条目的 rest 再像「章节」也**无** booktitle", "booktitle" not in _b)

# ══ ④ 反例 2：`[C]//` 但书名拿不准 → 不许编 ═════════════════════════════════
print("\n── ④ 反例 2：书名拿不准时**不许编** booktitle（宁缺勿编）──")

UNCLEAR = [
    ("句读之间夹空白（`甲 乙`，分不清责任者与书名）",
     "Smith, J. 2020. A Chapter[C]// 甲 乙[M]. 北京: 商务印书馆."),
    ("末段是版次（`第2版`）",
     "Smith, J. 2020. A Chapter[C]// 第二版[M]. 北京: 商务印书馆."),
    ("没有 `[M]`/`[A]`/`[D]` 收尾、也没有编者标记（只有出版信息）",
     "Smith, J. 2020. A Chapter[C]// 北京: 商务印书馆."),
    ("`[C]// [M]`：书名区为空",
     "Smith, J. 2020. A Chapter[C]// [M]. 北京: 商务印书馆."),
    ("书名区是 URL",
     "Smith, J. 2020. A Chapter[C]// https://example.org/a[M]. 北京: 商务印书馆."),
    ("`[C]// 责任者.[M]`：句读后没有书名（形态残缺）",
     "Smith, J. 2020. A Chapter[C]// 马蒂尼奇.[M]. 北京: 商务印书馆."),
    ("末段是版次（跟在书名后）",
     "Smith, J. 2020. A Chapter[C]// 马蒂尼奇. 语言哲学. 第2版[M]. 北京: 商务印书馆."),
]
for _why, _line in UNCLEAR:
    _b = bib_of(_line)
    check(f"④ 不编 booktitle：{_why}", "booktitle" not in _b, repr(_b))

# ══ ⑤ 反例 3：没有编者信息的条目不许冒出 editor ═══════════════════════════
print("\n── ⑤ 反例 3：`editor` 只在源稿**明确**写了编者时才出现 ──")

# Tarski 的 `马蒂尼奇` 在 `[C]//` 体例里确像主编，但源稿**没写**编者标记，
# 按「拿不准不写」的口径不许补。
check("⑤ 无编者标记 → 无 editor（`马蒂尼奇` 不当编者写）",
      "editor" not in _tb, repr(_tb))
check("⑤ 无编者标记 → 无 editor（book/article 更不该有）",
      "editor" not in bib_of(BOOK_BEALL) and "editor" not in bib_of(ART_HAACK76))
# 反之：明确写了编者标记 → 必须写（否则就是另一个方向的丢字段）
for _why, _line in (
    ("`（编）`", "Smith, J. 2020. A Chapter[C]// 马蒂尼奇（编）. 语言哲学[M]. 北京: 商务印书馆."),
    ("`主编`", "Smith, J. 2020. A Chapter[C]// 马蒂尼奇, 主编. 语言哲学[M]. 北京: 商务印书馆."),
    ("`(eds.)`", STEIN),
):
    _b = bib_of(_line)
    check(f"⑤ 明确编者标记 {_why} → 写 editor", "editor" in _b, repr(_b))
# `(eds.)` 后面没有书名时：editor 仍写，但绝不编 booktitle
_b = bib_of("Smith, J. 2020. A Chapter[C]// Zalta, E N. (eds.), https://x.example/y.")
check("⑤ `(eds.)` 后无可辨识书名 → 有 editor、无 booktitle",
      "editor" in _b and "booktitle" not in _b, repr(_b))

# ══ ⑥ 反例 4：`@book` / `@article` 既有输出逐字不变 ════════════════════════
print("\n── ⑥ 反例 4：book / article 分支一个字没动（黄金字面量）──")

BEALL_GOLD = (
    "% 由 Word 参考文献自动生成，请核对类型与字段。\n\n"
    "@book{Beall2006,\n"
    "  author    = {Beall and J C and Restall and G},\n"
    "  title     = {Logical Pluralism},\n"
    "  address   = {Oxford},\n"
    "  publisher = {Oxford University Press},\n"
    "  year      = {2006}\n"
    "}\n"
)
HAACK76_GOLD = (
    "% 由 Word 参考文献自动生成，请核对类型与字段。\n\n"
    "@article{Haack1976,\n"
    "  author  = {Haack, S},\n"
    "  title   = {Justification of Deduction},\n"
    "  journal = {Mind},\n"
    "  volume  = {85},\n"
    "  number  = {337},\n"
    "  pages   = {112--119},\n"
    "  year    = {1976}\n"
    "}\n"
)
# `Haack1974` 的 rest 里还粘着 `Second, expanded edition: … 1996`，本轮**不动** note
# （改它必须动 @book 分支输出，与本轮「book 逐字不变」的验收冲突 —— 第 81 条遗留①）。
HAACK74 = ("Haack, S. 1974. Deviant Logic[M]. Cambridge: Cambridge University Press. "
           "Second, expanded edition: Deviant logics, Fuzzy Logic: Beyond the Formalism[M]. "
           "Chicago: University of Chicago Press, 1996.")
HAACK74_GOLD = (
    "% 由 Word 参考文献自动生成，请核对类型与字段。\n\n"
    "@book{Haack1974,\n"
    "  author    = {Haack, S},\n"
    "  title     = {Deviant Logic},\n"
    "  address   = {Cambridge},\n"
    "  publisher = {Cambridge University Press},\n"
    "  year      = {1974}\n"
    "}\n"
)
check("⑥ @book{Beall2006} 输出逐字不变", bib_of(BOOK_BEALL) == BEALL_GOLD, repr(bib_of(BOOK_BEALL)))
check("⑥ @article{Haack1976} 输出逐字不变",
      bib_of(ART_HAACK76) == HAACK76_GOLD, repr(bib_of(ART_HAACK76)))
check("⑥ @book{Haack1974} 输出逐字不变（版次加注仍不进 note —— 本轮不动，已记录）",
      bib_of(HAACK74) == HAACK74_GOLD, repr(bib_of(HAACK74)))
check("⑥ book/article 分支的字段表里没有书名字段（接线只加在 incollection 上）",
      fields_of(bib_of(BOOK_BEALL)) == ["author", "title", "address", "publisher", "year"]
      and fields_of(bib_of(ART_HAACK76))
      == ["author", "title", "journal", "volume", "number", "pages", "year"])

# ══ ⑦ 反例 5：条目数与键序列不变 ═══════════════════════════════════════════
print("\n── ⑦ 反例 5：条目数 / 键序列 / make_key ──")

MIXED_LINES = [
    BOOK_BEALL, ART_HAACK76, TARSKI, STEIN,
    "陈波. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50.",
]
MIXED = [parse_reference_line(x) for x in MIXED_LINES]
MIXED_BIB = refs_to_bib(MIXED)
_emitted = re.findall(r"^@(\w+)\{([^,]+),", MIXED_BIB, re.M)
check("⑦ 条目数不变（1 输入 = 1 输出，5 条）", len(_emitted) == len(MIXED) == 5,
      repr(_emitted))
check("⑦ 键序列逐字不变（`[C]//` 修字段**不**改键）",
      [k for _t, k in _emitted] == [e.key for e in MIXED]
      == ["Beall2006", "Haack1976", "Tarski2019", "Steinberger2022", "ChenBo2022"],
      repr([k for _t, k in _emitted]))
check("⑦ 类型序列不变（C → incollection，其余照旧）",
      [t for t, _k in _emitted] == ["book", "article", "incollection", "incollection", "article"],
      repr([t for t, _k in _emitted]))
# `make_key` 一个字没动：既有的确定性形态照旧
check("⑦ make_key 逐字未动：中文译名 / 中文全拼音 / 西文姓",
      make_key(parse_reference_line(TARSKI)) == "Tarski2019"
      and make_key(parse_reference_line("陈波. 2022. 逻辑例外论的演变[J]. 逻辑学研究, 2022(5): 38-50."))
      == "ChenBo2022"
      and make_key(parse_reference_line(ART_HAACK76)) == "Haack1976")

# ══ ⑧ 端到端 ═══════════════════════════════════════════════════════════════
print("\n── ⑧ 端到端：现造最小 docx + 真实稿 ──")

TMP = []
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(paras):
    ps = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
                 for t in paras)
    doc = f'<w:document xmlns:w="{W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
    TMP.append(path)
    return path


BODY = [f"正文第{i}段，讨论数据库逻辑与数据库理论之间的关系。" for i in range(12)]
p_min = make_docx(["论数据库逻辑", "张三", "摘要：本文讨论数据库逻辑。", "关键词：数据库；逻辑"]
                  + BODY + ["参考文献", TARSKI])
d_min = parse_docx(p_min)
check("⑧ 最小 docx：文献区解析出 1 条、键 Tarski2019",
      [e.key for e in d_min.refs] == ["Tarski2019"], repr([e.key for e in d_min.refs]))
check("⑧ 最小 docx：端到端产出 = 黄金字面量", refs_to_bib(d_min.refs) == TARSKI_BIB,
      repr(refs_to_bib(d_min.refs)))

REAL = os.path.join(_REPO, "2026年第5期(90)", "10 Bo_Chen", "original", "陈波 当代逻辑哲学鸟瞰.docx")
if not os.path.exists(REAL):
    skip("真实稿 10 Bo_Chen", "语料不在本机")
else:
    d_real = parse_docx(REAL)
    by_key = {e.key: e for e in d_real.refs}
    check("⑧ 真实稿：Tarski2019 源稿原行逐字等于主诉那条",
          by_key.get("Tarski2019") is not None and by_key["Tarski2019"].raw == TARSKI,
          repr(by_key.get("Tarski2019").raw if by_key.get("Tarski2019") else None))
    real_bib = refs_to_bib(d_real.refs)
    check("⑧ 真实稿：Tarski2019 = @incollection + 语言哲学 / 商务印书馆 / 北京",
          "booktitle = {语言哲学}" in real_bib
          and "publisher = {商务印书馆}" in real_bib
          and "address   = {北京}" in real_bib)
    check("⑧ 真实稿：Steinberger2022 = @incollection + booktitle + editor",
          "booktitle = {The Stanford Encyclopedia of Philosophy}" in real_bib
          and "editor    = {Zalta, E N. and Nodelman, U}" in real_bib)
    check("⑧ 真实稿：键序列里两条 @incollection 仍是 Tarski2019 / Steinberger2022",
          [k for k, t, _f in
           [(m.group(2), m.group(1), None) for m in
            re.finditer(r"^@(\w+)\{([^,]+),", real_bib, re.M)] if t == "incollection"]
          == ["Steinberger2022", "Tarski2019"])
    check("⑧ 真实稿：条目数不变（25 条），且 book/article 一条都没被丢",
          len(d_real.refs) == 25
          and sum(1 for t, _k in re.findall(r"^@(\w+)\{([^,]+),", real_bib, re.M)) == 25)

for _f in TMP:
    try:
        os.unlink(_f)
    except OSError:
        pass

print()
if SKIPS:
    print(f"SKIP {len(SKIPS)} 项：" + "；".join(SKIPS))
if FAILS:
    print(f"失败 {len(FAILS)} 项：" + repr(FAILS))
    sys.exit(1)
print("失败 0 项（全部通过）")
