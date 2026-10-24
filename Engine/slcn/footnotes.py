r"""脚注处理：分类、拆分、页码定位、生成 bib 条目。

背景（实测 Lu_Jiang，90 条脚注）
--------------------------------
作者用**脚注式引用**，把参考文献全写在脚注里。逐条核对人工成品后得到四类处理：

  ① 纯文献引用（65 条）
     例：`Catarina Dutilh Novaes and Stephen Read, "Introduction", in The Cambridge
          Companion to Medieval Logic, ... p. 14.`
     人工：**从脚注提升到正文**，变成 `(\parencite[14]{c05})`

  ② 混合：文献 + 注释（4 条）
     例（用户指出的脚注 6）：
         `James W. Garson, Modal Logic for Philosophers, Cambridge: Cambridge
          University Press, 2nd ed., 2013, pp. 57-58, pp. 63-64.
          The formal definition of the modality □ is on page 64.`
     人工：`\\footnote{cf. \\parencite[63--64]{c04}. The formal definition of the
            modality $\\Box$ is on page 64. }`
     ── **文献部分剥离成 \\parencite，注释部分原样保留，合并在同一个 \\footnote 内**

  ③ 纯注释（21 条）
     例：`Here we are talking about propositonal modal logic, ...`
     人工：原样 `\\footnote{...}`

  ④ 古典文献（Ockham 等一手文献）
     例：`Ockham, Exp. in libr. Perih. II, 5, OP II, 465.`
     人工：**原样保留在脚注里**，不进 bib（`\\footnote{Ockham, \\textit{Exp. in
            libr. Perih.} II, 5, \\textit{OP} II, 465.}`）
     判据：没有 4 位年份、没有出版社 —— 不是现代出版物条目。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── 文献成分识别 ──────────────────────────────────────────────────────────

# 出现这些说明是**现代出版物条目**（可以进 bib）
BIB_MARKERS = [
    (r"\b(?:19|20)\d{2}\b", "年份"),
    (r"\bpp?\.\s*\d+", "页码缩写"),
    (r"\b(?:Press|Publishing|Verlag|Routledge|Blackwell|Wiley|Springer)\b", "出版社"),
    (r"出版社|印书馆|译文出版社|人民出版社|大学出版社", "中文出版社"),
    (r"\b(?:Vol|vol|no|No)\.\s*\d+", "卷期"),
    (r"\d+\s*\(\d+\)", "卷(期)"),
    (r"\[[MJC]\]", "文献类型标识"),
]

_SENT_END = re.compile(r"[.。]\s+|$")
#: 作者信息/基金/通讯类脚注：即使含年份、单位名，也**绝不能**当文献提升（87-7 实测：
#: 「作者简介：任然，（1984-）… 唐山师范学院…」曾被当成 ref → 多出 junk bib 条目）
_AUTHOR_INFO = re.compile(
    r"作者简介|作者信息|基金项目|通讯作者|通信作者|收稿日期|录用日期|"
    r"E-?mail|电子邮箱|邮箱[:：]|\bDOI\b.*作者|项目编号")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


@dataclass
class Footnote:
    fid: int
    text: str
    kind: str = ""  # 'pure_ref' | 'mixed' | 'pure_note' | 'classical'
    ref: str = ""  # 文献部分
    note: str = ""  # 注释部分
    locator: str = ""  # \parencite[...] 里的页码定位
    #: 第四十九批 C 类新口径：`classify_citation()` 的 kind
    #: （pure_citation / reference_entry / mixed / note_only）。
    kind_new: str = ""
    #: 纯引用体原文（新口径：把「文献引用那段」原样带出，供并集/warning 用）
    ref_raw: str = ""
    # 第 121 条：英文纯注释的「已转义 + 引用已替换」版本（空 = 渲染时按 text 转义）
    note_latex: str = ""
    key: str = ""  # bib 键（生成后回填）
    entry: dict = field(default_factory=dict)  # 解析出的 bib 字段
    # ── B-199 脚注引用提升（2026-10-23）──────────────────────────────────
    #: 真 mixed 拆分：引用**之前**的说明文字（含 `参见/参阅/See` 引导语）
    note_before: str = ""
    #: 真 mixed 拆分：引用**之后**的说明文字
    note_after: str = ""
    #: 由 note_only 探测提升的短引用（docxsource 用 `strict=True` 唯一匹配）
    from_note_probe: bool = False
    #: 引导语（参阅/参见/See/Cf.）后紧跟完整条目 → 剥引导语后按条目提升
    cue_stripped: bool = False
    #: 拆分出的引用里含**多部作品**（≥2 个题名）：保留原文，只在末尾追加 parencite
    multi_work: bool = False
    #: 多作品兜底：脚注整段原文保留（note_before 设为全文），只在末尾注入 parencite
    keep_whole: bool = False
    #: 拆分脚注的渲染片段（docxsource 预先 escape/link_citations；渲染时只用这两段）
    note_before_latex: str = ""
    note_after_latex: str = ""


def ref_span(text: str) -> int:
    """找出「文献部分」的结束位置；-1 表示整体不像文献条目。"""
    last = -1
    for pat, _ in BIB_MARKERS:
        for m in re.finditer(pat, text):
            if m.end() > last:
                last = m.end()
    if last < 0:
        return -1
    m = _SENT_END.search(text, last)
    return m.end() if m else len(text)


#: 强标记：中文期刊/出版机构/学位论文/论文集（单靠「第N页」会被正文定位误伤——90-08 实测
#: 脚注里的「（[20]，第36页；[21]，第8页）」是引用定位，不是文献条目）
_CN_STRONG = re.compile(
    r"第\s*\d+\s*期|学报|期刊|出版社|印书馆|书局|博士论文|硕士学位论文|硕士论文|"
    r"论文集|载于|收录于|人民出版社|大学出版社|论坛|会议|大会|报告")
#: 年份后可以是「年」，也可以直接是「第N期」（86-03 实测 `2010第1期` 无「年」字）
_CN_YEAR = re.compile(r"(?:19|20)\d{2}\s*(?:年|第)")


def _looks_cn_biblio(text: str) -> bool:
    """中文脚注文献判据：**年份 + 强标记**，且不是作者信息/纯定位。"""
    t = text.strip()
    if not t or len(t) > 220:
        return False
    if not _CN_YEAR.search(t) or not _CN_STRONG.search(t):
        return False
    # 纯引用定位（`（[20]，第36页；[21]，第8页）`）没有年份/强标记，已被上一条排除；
    # 这里再要求句末是句号/页码结尾，避免把「……2013年，参见第5页。」式注释当文献。
    return t.endswith(("。", "页。", "页", "。]", "．", "."))


def looks_classical(ref: str) -> bool:
    """判断是不是古典/一手文献（Ockham, SL II, 1, OP I, 242.）。

    这类引用人工是**原样留在脚注里**的，不进参考文献表。
    """
    if _YEAR.search(ref):
        return False
    if any(re.search(p, ref) for p, _ in BIB_MARKERS if "出版社" in _ or "Press" in _):
        return False
    # 含罗马数字卷次 + 短缩写（SL / OP / Exp.）是典型的古典文献引用
    roman = re.search(r"\b[IVX]+\b\s*[,.]", ref)
    abbrev = re.search(r"\b(?:SL|OP|Exp|Summa|Sent|Quodl|Ord)\b", ref)
    return bool(roman or abbrev)


def classify(text: str) -> Footnote:
    """给一条脚注分类并拆分。"""
    t = re.sub(r"\s+", " ", text).strip()
    fn = Footnote(fid=-1, text=t)
    if _AUTHOR_INFO.search(t):
        fn.kind = "pure_note"
        fn.note = t
        return fn
    end = ref_span(t)
    if end < 0 and _looks_cn_biblio(t):
        # 中文脚注文献：`杨正文：鼓藏节仪式与苗族社会组织，西南民族学院学报，2000年第5期，
        # 第13-26页。`——旧判据的年份 `\b(19|20)\d{2}\b` 在「2000年」处没有词边界，
        # 也没有「第N期」标记 → 整条被判 pure_note 丢掉（86-03 实测 34 条中文脚注文献只
        # 认出 8 条，人工成品 22 条）。这里补一条**中文期刊/出版社**判据，且要求年份+强标记。
        fn.kind = "pure_ref"
        fn.ref = re.sub(r"^\s*\[\s*\d+\s*\]\s*", "", t)
        return fn
    if end < 0:
        fn.kind = "pure_note"
        fn.note = t
        return fn
    head, tail = t[:end].strip(), t[end:].strip()
    if looks_classical(head):
        fn.kind = "classical"
        fn.note = t
        return fn
    fn.ref = head
    fn.note = tail
    fn.kind = "mixed" if tail else "pure_ref"
    fn.locator = extract_locator(head)
    return fn


# ── 作者信息脚注（批71 B-118）──────────────────────────────────────────────
#
# Word 稿常把作者信息写成**脚注**（第 90 期 90-01/02/04/07/10 实测）：`作者信息：` /
# `作者简介：` / `收稿日期：` / `基金项目：` + 每作者「姓名 单位」行 + 邮箱行。它们与
# 文献脚注的格式完全不同，`classify()` 已按 `_AUTHOR_INFO` 判成 pure_note（本组函数
# **不改 classify 的任何分支**），这里只把这些文本抽成结构化元数据，供
# `docxsource.parse_docx` 落到 DocxDoc（build 再**只填空不覆盖**地写模板槽位）。
#
# 实测形态（脱敏后钉在 tests/test_b71_word_author_meta.py）：
#   ① 单作者：  `作者信息：张三  北京大学哲学系` + 邮箱行
#   ② 多作者中英并列：每作者「姓名 单位」+ 邮箱，英文名/单位写在括号里或另起一行
#   ③ 叙事：    `作者简介：张三，男，某省某市人，某大学某系教授。电子邮箱：…`
#   ④ 多单位：  单位另起一行；`通讯作者：` 可另立一段
#   ⑤ 同条：    `收稿日期：2025-09-19  基金项目：国家社科基金…（22CZX064）。`
#   ⑥ 无标签英文块：`Zhang San  College of Philosophy, …` + 邮箱（**必须**每条命中的
#      姓名都在已知作者名单里，且整块无文献标记，宁 fail 不误判）
#   ⑥b（B-217 ④⑥）无标签英文作者块（不依赖已知名单）：逐作者一行
#      `San Zhang, Department of Philosophy, Example University, E-mail: …`
#      （也兼容 v7 旧格式「姓名 单位…」不带姓后逗号）→ 姓名/单位/邮箱；判据见
#      `_ai_en_name_and_unit()` / `_ai_en_block_prefix()`，宁 fail 不误判。
#
# 判据纪律（用户 2026-10-21 拍板：**不许猜**）：
#   · 姓名只取「已知作者名 / 行首姓名 + 单位或邮箱 / 括号里的英文名」；凭空 token 不收；
#   · 单位必须含机构词（大学/学院/研究所/… 或 University/College/…）且以机构后缀收尾；
#   · 姓名与单位**无法可靠切分**（无分隔连写且不在已知名单）→ 不解析 + warning；
#   · 绝不把第一作者的字段复制给其他作者；第一作者槽位与正文姓名一致时 build 以
#     `author_meta[0]` 覆盖 legacy 合并值（B-217 ⑤），其余仍「只填空不覆盖」。
_AI_NAME_LABEL = (
    r"作者信息|作者简介|作者单位|单位信息|所属单位|作者署名|作者姓名|"
    r"通讯作者|通信作者|Corresponding\s+author"
)
_AI_RECEIVED_LABEL = r"收稿日期|录用日期|投稿日期|修回日期|修订日期"
_AI_FUND_LABEL = r"基金项目|基金资助|资助项目|项目资助"
_AI_LABEL_LEAD_RE = re.compile(
    r"(?:^|\n)[ \t\u3000]*(?:" + _AI_NAME_LABEL + "|" + _AI_RECEIVED_LABEL
    + "|" + _AI_FUND_LABEL + r")[ \t\u3000]*[:：]", re.M | re.I)
#: 段标签只认**行首**（或前面 ≥2 个空格 = 同一条里并列的多个标签）。不认行中间的
#: `基金项目`（90-02 的基金名里就含「国家社会科学基金项目重点项目」，不能切开）。
_AI_SECTION_RE = re.compile(
    r"(?:\n|^|[ \t\u3000]{2,})(?P<label>(?:" + _AI_NAME_LABEL + r")|(?:"
    + _AI_RECEIVED_LABEL + r")|(?:" + _AI_FUND_LABEL + r"))[ \t\u3000]*[:：][ \t\u3000]*",
    re.M | re.I)
_AI_NAMES_SET = {"作者信息", "作者简介", "作者单位", "单位信息", "所属单位",
                 "作者署名", "作者姓名", "通讯作者", "通信作者"}
_AI_RECEIVED_SET = {"收稿日期", "录用日期", "投稿日期", "修回日期", "修订日期"}
_AI_FUND_SET = {"基金项目", "基金资助", "资助项目", "项目资助"}
_AI_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_AI_EMAIL_LABEL_RE = re.compile(r"(?:电子邮箱|邮箱|E-?mail)\s*[：:]", re.I)
_AI_DATE_RE = re.compile(
    r"((?:19|20)\d{2})\s*[-–—./年]\s*(\d{1,2})\s*[-–—./月]\s*(\d{1,2})")
_AI_PAREN_RE = re.compile(r"[（(]([^（）()]{2,120})[）)]")
_AI_LATIN_NAME_RE = re.compile(
    r"(?:[A-Z][A-Za-z'’\-]*\.?\s+){1,2}[A-Z][A-Za-z'’\-]*")
_AI_CN_UNIT_START_RE = re.compile(
    r"^[\u4e00-\u9fff·]{2,10}(?:大学|学院|研究院|研究所|实验室|科学院|"
    r"社会科学院|学系|哲学系|中心|学部)")
_AI_CN_INST_WORD_RE = re.compile(
    r"大学|学院|研究院|研究所|实验室|科学院|社会科学院|学系|哲学系|中心|学部")
_AI_CN_UNIT_TAIL_RE = re.compile(
    r"(?:大学|学院|研究院|研究所|实验室|科学院|社会科学院|学系|哲学系|系|中心|学部)$")
#: 学术职衔（**剥离**，单位字段只留机构名；行政职务故意不收 —— 与 docxsource 同一口径）
_AI_TITLE_TAIL_RE = re.compile(
    r"(?:一级|二级|三级|四级|特聘|客座|兼职|终身|名誉|助理|副)?"
    r"(?:教授|研究员|讲师|助教|博士生导师|硕士生导师|博导|硕导|博士研究生|"
    r"硕士研究生|博士|硕士|研究生|生)$")
_AI_NAME_BAD_RE = re.compile(
    r"大学|学院|研究院|研究所|实验室|科学院|中心|男|女|人$|省|市|县|区|系|"
    r"所|院|教授|博士|研究|学$")
#: 常见中文姓氏（只用于**未知连写**的切分守卫：首字不在表里就不猜）。
#: 不必求全——名单外的姓名会走「无法判定 → warning」而不是猜错。
_AI_SURNAMES = frozenset(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田"
    "董袁潘蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹"
    "孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
_AI_COMPOUND_SURNAMES = frozenset(
    ("欧阳", "司马", "诸葛", "上官", "东方", "皇甫", "尉迟", "公孙", "慕容",
     "长孙", "宇文", "司徒", "令狐", "夏侯"))


def _ai_plausible_name(cand: str) -> bool:
    """未知连写的姓名守卫：2–4 个汉字、不含机构/角色词、首字是常见姓氏。"""
    if not re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", cand or ""):
        return False
    if _AI_NAME_BAD_RE.search(cand):
        return False
    if len(cand) >= 3 and cand[:2] in _AI_COMPOUND_SURNAMES:
        return True
    return cand[0] in _AI_SURNAMES


#: 英文机构词（单位判据的**唯一**来源：`_AI_EN_INST_RE` 与「姓名+单位」切分共用）
_AI_EN_INST_WORD = (
    r"University|College|Institute|School|Department|Academy|Center|"
    r"Centre|Faculty|Laboratory|Lab")
_AI_EN_INST_RE = re.compile(r"\b(?:" + _AI_EN_INST_WORD + r")\b", re.I)
#: 无标签英文块里**绝不能出现**的文献标记（出现即当文献脚注，宁 fail 不误判）
_AI_CITE_CUE_RE = re.compile(
    r"[“”\"‘’]|\b(?:19|20)\d{2}\b|\bpp?\.\s*\d|\bVol\.|\bPress\b|\bJournal\b|"
    r"出版社|学报|期刊|https?://")


def looks_like_author_info(text: str) -> bool:
    """是否「作者信息类」脚注（行首有 作者信息/作者简介/收稿日期/基金项目 标签）。

    只在 docxsource 里用来决定「解析不出来时要不要报 warning」——**不改** `classify()`。
    """
    t = text or ""
    if not t.strip() or len(t) > 6000:
        return False
    return bool(_AI_LABEL_LEAD_RE.search(t))


def _ai_kind(label: str) -> str:
    lab = re.sub(r"\s+", " ", (label or "").strip()).lower()
    if lab in _AI_NAMES_SET or lab.startswith("corresponding"):
        return "authors"
    if lab in _AI_RECEIVED_SET:
        return "received"
    if lab in _AI_FUND_SET:
        return "funds"
    return "authors"


def _ai_clean_text(s: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", (s or "").replace("\u3000", " ")).strip()


def _ai_norm_name(name: str) -> str:
    return re.sub(r"[\s\u3000\\{}kaishu]+", "", str(name or ""))


def _ai_new_record(records: list[dict], *, name: str = "", name_en: str = "") -> dict:
    rec = {"name": name, "name_en": name_en,
           "affiliation_cn": [], "affiliation_en": [], "email": ""}
    records.append(rec)
    return rec


def _ai_strip_title(frag: str) -> str:
    """剥掉学术职衔与包裹标点；返回可能是机构名的片段（不做机构词判据）。"""
    s = frag.strip(" \u3000,，.。．;；:：()（）[]【】")
    for _ in range(2):
        s2 = _AI_TITLE_TAIL_RE.sub("", s).strip(" \u3000,，.。．;；:：()（）[]【】")
        if s2 == s:
            break
        s = s2
    return s


def _ai_add_units(rec: dict, text: str) -> None:
    """把一段「单位文字」收进 rec；不猜：碎片必须含机构词且以机构后缀/机构词收尾。"""
    t = _ai_clean_text(text).strip(" ,，.。．;；:：")
    if not t:
        return
    latin = re.search(r"[A-Za-z]", t)
    han = re.search(r"[\u4e00-\u9fff]", t)
    if latin and not han:
        # 纯西文：整段就是一个单位，**不按逗号切**（`College of Philosophy, Nankai University`）
        for seg in re.split(r"[;；]|\\\\\\\\", t):
            seg = seg.strip(" .,;，。")
            if (seg and len(seg) >= 8 and _AI_EN_INST_RE.search(seg)
                    and seg not in rec["affiliation_en"]):
                rec["affiliation_en"].append(seg)
        return
    if latin and han:
        # 中英混排：先摘出西文单位段，剩下的按中文规则处理
        for seg in re.findall(r"[A-Za-z][A-Za-z ,&.\-'’]{7,}", t):
            seg2 = seg.strip(" .,;")
            if len(seg2) >= 8 and _AI_EN_INST_RE.search(seg2) \
                    and seg2 not in rec["affiliation_en"]:
                rec["affiliation_en"].append(seg2)
            t = t.replace(seg, " ")
    for frag in re.split(r"[，,。．.；;、]+", t):
        core = _ai_strip_title(frag)
        if not core or len(core) < 4 or not re.fullmatch(r"[\u4e00-\u9fff·]+", core):
            continue
        if _AI_CN_INST_WORD_RE.search(core) and _AI_CN_UNIT_TAIL_RE.search(core):
            if core not in rec["affiliation_cn"]:
                rec["affiliation_cn"].append(core)


def _ai_split_unknown_name(line: str) -> tuple[str, str] | None:
    """行首「姓名 + 单位」的保守切分；多个切法都成立 → 不猜（None）。"""
    t = _ai_clean_text(line)
    m = re.match(r"^([\u4e00-\u9fff·]{2,4})[ \u3000]{2,}(.+)$", t)
    if m and _AI_CN_INST_WORD_RE.search(m.group(2)):
        return m.group(1), m.group(2)
    m_en = re.match(
        r"^(" + _AI_LATIN_NAME_RE.pattern + r")\s{2,}(.+)$", t)
    if m_en and _AI_EN_INST_RE.search(m_en.group(2)):
        return "", ""  # 英文姓名不是中文切分；交给调用方的英文分支
    cands: list[tuple[str, str]] = []
    for k in (2, 3, 4):
        cand, rest = t[:k], t[k:]
        # 4 字姓名只可能是复姓（欧阳…），普通单姓最多 3 字 —— 否则「李四北京大学…」
        # 会同时切出「李四」「李四北京」两种，反而把能切的切没了。
        if k == 4 and cand[:2] not in _AI_COMPOUND_SURNAMES:
            continue
        if not _ai_plausible_name(cand):
            continue
        rest = rest.lstrip(" \u3000")
        if not rest or not _AI_CN_UNIT_START_RE.match(rest):
            continue
        # 单位起头必须是「2–6 字地名/校名前缀 + 机构词」（北京大学 ✓ / 京大学 ✗）：
        # 否则「李四北京大学…」会同时切出「李四」「李四北」两种，把能切的切没了。
        _inst = _AI_CN_INST_WORD_RE.search(rest)
        if not _inst or not (2 <= _inst.start() <= 6):
            continue
        if not _AI_CN_UNIT_TAIL_RE.search(rest):
            continue
        cands.append((cand, rest))
    return cands[0] if len(cands) == 1 else None


def _ai_known_prefix(line: str, known_cn: tuple[str, ...],
                     known_en: tuple[str, ...]) -> tuple[str, str, str] | None:
    """行首是否是已知作者名 → (kind, name, rest)；kind ∈ {'cn','en'}。"""
    t = _ai_clean_text(line)
    best: tuple[str, str, str] | None = None
    for nm in known_cn:
        if len(nm) >= 2 and t.startswith(nm):
            if best is None or len(nm) > len(best[1]):
                best = ("cn", nm, t[len(nm):])
    for nm in known_en:
        if len(nm) >= 4 and t.startswith(nm):
            if best is None or len(nm) > len(best[1]):
                best = ("en", nm, t[len(nm):])
    if best is None:
        return None
    rest = best[2].strip(" \u3000,，.。．;；:：")
    # 已知名后面必须紧跟分隔（空格/标点）或直接是机构名，否则视为误命中
    if best[2] and best[2][0] not in " \u3000,，.。．;；:：)" \
            and not _AI_CN_INST_WORD_RE.search(best[2]):
        return None
    return best[0], best[1], rest


def _ai_add_emails(rec: dict, emails: list[str]) -> None:
    for e in emails:
        if not e:
            continue
        if not rec["email"]:
            rec["email"] = e
        else:
            break


def _ai_is_unit_line(line: str) -> bool:
    t = _ai_clean_text(line)
    if not t:
        return False
    if _AI_CN_UNIT_START_RE.match(t) and _AI_CN_INST_WORD_RE.search(t):
        return True
    return bool(_AI_EN_INST_RE.search(t) and re.search(r"[A-Za-z]", t))


# ── 英文作者块（B-217 ④⑥，与中文作者信息**同源判据**）──────────────────────
#
# 英文稿模板的写法（`drafts/template-engine-contract.md` §2 L1-2）：
#     San Zhang, Department of Philosophy, Example University, E-mail: san@example.edu
#     Si Li, School of Philosophy, Example University, E-mail: li@example.edu
# 逐作者一行（也兼容同一条里 `；` 并列、以及 v7 旧格式「姓名 单位…」不带姓后逗号）。
# 判据纪律与中文块一致（用户 2026-10-21 拍板「不许猜」）：
#   · 姓名必须整段是拉丁姓名形态（`_AI_LATIN_NAME_RE`，1–3 个词）；
#   · 单位必须含机构词（`_AI_EN_INST_RE`）且够长，纯 `E-mail` 残尾剥掉；
#   · 每行不得带文献线索（`_AI_CITE_CUE_RE`：年份/pp./Press/Journal/引号…）；
#   · 单独一位作者必须带邮箱才认（否则宁可 fail 不误判）。
_AI_EN_LABEL_TAIL_RE = re.compile(r"[\s,，;；]*(?:E-?mail|Email)\s*$", re.I)


def _ai_en_name_and_unit(text: str) -> tuple[str, str] | None:
    """`姓名, 单位…`（或旧格式 `姓名 单位…`）→ (姓名, 单位)；拿不准 → None。"""
    t = _ai_clean_text(text)
    parts = [p.strip() for p in re.split(r"[,，]", t) if p.strip()]
    name = ""
    unit = ""
    if len(parts) >= 2 and len(parts[0].split()) <= 3 \
            and _AI_LATIN_NAME_RE.fullmatch(parts[0]):
        name = parts[0]
        unit = ", ".join(parts[1:])
    else:
        words = t.split()
        for k in (2, 3):
            if len(words) <= k:
                break
            cand = " ".join(words[:k])
            rest = " ".join(words[k:])
            if _AI_LATIN_NAME_RE.fullmatch(cand) and re.match(
                    r"^(?:" + _AI_EN_INST_WORD + r")\b", rest, re.I):
                name, unit = cand, rest
                break
    if not name:
        return None
    unit = _AI_EN_LABEL_TAIL_RE.sub("", unit).strip(" ,，;；")
    if len(unit) < 8 or not _AI_EN_INST_RE.search(unit):
        return None
    return name, unit


def _ai_en_block_prefix(text: str) -> str | None:
    """无标签英文作者块的**开头连续段**；不是作者块 → None。

    只收开头连续的「作者行 / 单位续行 / 邮箱行」，遇到第一处别的内容就停
    （模板脚注后面的 `Acknowledgements: …` 不参与、也不整块否决——与中文块
    「尾部标签另算」同一思路）。要求：至少 1 位作者，且「≥2 位作者」或「带邮箱」。
    """
    lines = [_ai_clean_text(x) for x in re.split(r"[\n\r]+", text or "")]
    lines = [x for x in lines if x]
    if not lines:
        return None
    kept: list[str] = []
    n_auth = 0
    have_email = False
    for ln in lines:
        emails = _AI_EMAIL_RE.findall(ln)
        core = _ai_clean_text(_AI_EMAIL_LABEL_RE.sub(" ", _AI_EMAIL_RE.sub(" ", ln)))
        core = core.strip(" ,，.。;；:：")
        if not core:                       # 纯邮箱行（逐作者一行体例的续行）
            if not n_auth:
                return None
            kept.append(ln)
            have_email = True
            continue
        if _ai_en_name_and_unit(core) and not _AI_CITE_CUE_RE.search(core):
            n_auth += 1
            have_email = have_email or bool(emails)
            kept.append(ln)
            continue
        if n_auth and _ai_is_unit_line(core) and not _AI_CITE_CUE_RE.search(core):
            kept.append(ln)
            continue
        break
    if not n_auth or (n_auth < 2 and not have_email):
        return None
    return "\n".join(kept)


def _ai_consume_narrative(
    line: str, emails: list[str], records: list[dict],
    known_cn: tuple[str, ...], warnings: list[str],
) -> dict:
    """叙事型作者简介：`姓名，性别，籍贯，单位+职衔。`（多作者用 `．`/`。` 分隔）。"""
    boundaries: list[tuple[int, str]] = []
    for nm in known_cn:
        start = 0
        while True:
            i = line.find(nm, start)
            if i < 0:
                break
            boundaries.append((i, nm))
            start = i + len(nm)
    boundaries.sort()
    # 去掉重叠（长名字优先）
    uniq: list[tuple[int, str]] = []
    for pos, nm in boundaries:
        if uniq and pos < uniq[-1][0] + len(uniq[-1][1]):
            continue
        uniq.append((pos, nm))
    boundaries = uniq
    if not boundaries:
        m = re.match(r"^([\u4e00-\u9fff·]{2,4})[，,]", line)
        if not m or not _ai_plausible_name(m.group(1)):
            return {}
        boundaries = [(0, m.group(1))]
    made: list[dict] = []
    for idx, (pos, nm) in enumerate(boundaries):
        end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(line)
        seg = line[pos + len(nm):end]
        rec = _ai_new_record(records, name=nm)
        _ai_add_units(rec, seg)
        made.append(rec)
    if emails:
        if len(emails) == len(made):
            for rec, e in zip(made, emails):
                _ai_add_emails(rec, [e])
        else:
            _ai_add_emails(made[0], emails)
    return made[-1] if made else {}


def _ai_consume_line(
    line: str, records: list[dict], cur: dict | None,
    known_cn: tuple[str, ...], known_en: tuple[str, ...], warnings: list[str],
) -> dict | None:
    """处理作者块里的一行：新作者 / 单位续行 / 邮箱行。返回新的「当前作者」。"""
    raw = _ai_clean_text(line)
    if not raw:
        return cur
    emails = _AI_EMAIL_RE.findall(raw)
    text = _AI_EMAIL_RE.sub(" ", raw)
    text = _AI_EMAIL_LABEL_RE.sub(" ", text)
    parens = [p.strip() for p in _AI_PAREN_RE.findall(text)]
    text = _AI_PAREN_RE.sub(" ", text)
    text = _ai_clean_text(text).strip(" ,，.。．;；:：")
    if not text:
        if emails:
            if cur is None:
                cur = _ai_new_record(records)
            _ai_add_emails(cur, emails)
        return cur
    # 叙事型（含 2+ 逗号 + 机构词 + 已知作者名或开头姓名）：姓名、单位混在句子里
    if text.count("，") + text.count(",") >= 2 and _AI_CN_INST_WORD_RE.search(text) \
            and (any(text.startswith(nm) for nm in known_cn) or "，男" in text
                 or "，女" in text or "教授" in text or "研究员" in text):
        made = _ai_consume_narrative(text, emails, records, known_cn, warnings)
        if made:
            return made
    # 英文作者行（B-217 ④⑥）：`姓名, 单位…` / 旧格式 `姓名 单位…`。放在「已知名单」
    # 之前——英文稿的 `_known_cn_authors()` 会把拉丁作者串拆成 `San`/`Zhang` 这类
    # 假名单，先走已知分支会把姓名切成碎片（实测 EN 模板稿）。
    _en = _ai_en_name_and_unit(text)
    if _en is not None:
        name, unit = _en
        rec = _ai_new_record(records, name_en=name)
        _ai_add_units(rec, unit)
        _ai_add_emails(rec, emails)
        return rec
    known = _ai_known_prefix(text, known_cn, known_en)
    if known:
        kind, name, rest = known
        if kind == "en":
            rec = _ai_new_record(records, name_en=name)
        else:
            rec = _ai_new_record(records, name=name)
        for p in parens:
            if _AI_LATIN_NAME_RE.fullmatch(p):
                rec["name_en"] = rec["name_en"] or p
            elif _AI_EN_INST_RE.search(p):
                _ai_add_units(rec, p)
        _ai_add_units(rec, rest)
        _ai_add_emails(rec, emails)
        return rec
    # 单位续行（`南京大学现代逻辑与逻辑应用研究所` 这类）：整行本身就是一个单位时
    # 先按续行处理，避免被「姓名+单位」硬切出「南京大」这种碎片。
    if cur is not None and _ai_is_unit_line(text) and not _ai_split_unknown_name(text):
        _ai_add_units(cur, text)
        _ai_add_emails(cur, emails)
        return cur
    split = _ai_split_unknown_name(text)
    if split:
        name, rest = split
        rec = _ai_new_record(records, name=name)
        for p in parens:
            if _AI_LATIN_NAME_RE.fullmatch(p):
                rec["name_en"] = rec["name_en"] or p
            elif _AI_EN_INST_RE.search(p):
                _ai_add_units(rec, p)
        _ai_add_units(rec, rest)
        _ai_add_emails(rec, emails)
        return rec
    if _ai_plausible_name(text):
        rec = _ai_new_record(records, name=text)
        _ai_add_emails(rec, emails)
        return rec
    if _ai_is_unit_line(text):
        if cur is None:
            cur = _ai_new_record(records)
        _ai_add_units(cur, text)
        _ai_add_emails(cur, emails)
        return cur
    # 认不出的碎片：不猜（丢掉，但只在整条像是作者块时提醒一次由调用方做）
    return cur


def _ai_split_inline_chunks(content: str) -> list[str]:
    """同一条里的多个作者用 `；`/`;` 并列时切开（每段都必须有邮箱或机构词）。"""
    content = content or ""
    if not re.search(r"[；;]", content):
        return [content]
    parts = [p for p in re.split(r"[；;]", content) if p.strip()]
    if len(parts) < 2:
        return [content]
    if all("@" in p or _AI_CN_INST_WORD_RE.search(p) for p in parts):
        return parts
    return [content]


def _ai_parse_authors(content: str, known_cn: tuple[str, ...],
                      known_en: tuple[str, ...], warnings: list[str]) -> list[dict]:
    records: list[dict] = []
    cur: dict | None = None
    for raw_line in re.split(r"[\n\r]+", content or ""):
        for chunk in _ai_split_inline_chunks(raw_line):
            if not chunk.strip():
                continue
            cur = _ai_consume_line(chunk, records, cur, known_cn, known_en, warnings)
    return records


def _ai_unlabeled_ok(text: str, known_cn: tuple[str, ...],
                     known_en: tuple[str, ...]) -> bool:
    """无标签英文作者块判据：**每条命中的姓名都必须是已知作者名**，整块无文献标记。"""
    if not known_cn and not known_en:
        return False
    if _AI_CITE_CUE_RE.search(text or ""):
        return False
    lines = [_ai_clean_text(x) for x in re.split(r"[\n\r]+", text or "")]
    lines = [x for x in lines if x]
    if len(lines) < 2:
        return False
    name_lines = 0
    have_email = False
    for ln in lines:
        if _AI_EMAIL_RE.fullmatch(ln.strip(" .,;，。")):
            have_email = True
            continue
        if _ai_known_prefix(ln, known_cn, known_en) or _ai_is_unit_line(ln) \
                or _AI_EMAIL_RE.search(ln):
            if _ai_known_prefix(ln, known_cn, known_en):
                name_lines += 1
            continue
        return False
    return name_lines >= 2 or (name_lines >= 1 and have_email)


def parse_author_info(
    text: str, *, known_cn: tuple[str, ...] = (), known_en: tuple[str, ...] = ()
) -> dict | None:
    """解析「作者信息类」脚注。

    返回 `{"authors": [...], "received": "", "grants": "", "warnings": [...]}`；
    不是作者信息脚注（或整条无法判定）→ `None`。每个作者 dict：
    `{"name", "name_en", "affiliation_cn": [...], "affiliation_en": [...], "email"}`。
    **本函数不猜**：认不出的碎片丢弃；叙事里的性别/籍贯/职衔不当单位。
    """
    t = text or ""
    if not t.strip() or len(t) > 6000:
        return None
    sections: list[tuple[str, str]] = []
    if looks_like_author_info(t):
        ms = list(_AI_SECTION_RE.finditer(t))
        for i, m in enumerate(ms):
            end = ms[i + 1].start() if i + 1 < len(ms) else len(t)
            sections.append((_ai_kind(m.group("label")), t[m.end():end]))
        if not sections:
            return None
    elif _ai_unlabeled_ok(t, known_cn, known_en):
        sections = [("authors", t)]
    else:
        # 无标签英文作者块（B-217 ④⑥）：姓名/单位/邮箱**自带判据**，不依赖已知名单
        # （英文稿的已知名单常为空或为拆碎的拉丁串）。
        _en_block = _ai_en_block_prefix(t)
        if not _en_block:
            return None
        sections = [("authors", _en_block)]
    authors: list[dict] = []
    received = ""
    grants = ""
    warnings: list[str] = []
    has_author_section = any(k == "authors" for k, _ in sections)
    for kind, content in sections:
        if kind == "authors":
            got = _ai_parse_authors(content, known_cn, known_en, warnings)
            authors.extend(got)
        elif kind == "received" and not received:
            c = content.strip()
            m = _AI_DATE_RE.search(c)
            if m:
                received = "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)),
                                               int(m.group(3)))
            elif c and not authors:
                # 只有标签、没有日期是正常体例（作者没填）；有内容却认不出 → 提醒
                left = _AI_LABEL_LEAD_RE.sub("", c).strip()
                if left:
                    warnings.append(f"收稿日期无法判定（不猜）：{c[:40]}")
        elif kind == "funds" and not grants:
            for ln in _AI_EMAIL_RE.sub(" ", content).splitlines():
                ln = ln.strip()
                if ln:
                    grants = ln
                    break
    if not authors and grants:
        # 只有基金项目（90-08 型）：正常，不警告
        pass
    elif has_author_section and not any(
            a.get("name") or a.get("name_en") for a in authors):
        cue = _AI_LABEL_LEAD_RE.sub("", t).strip()
        if len(cue) >= 2:
            warnings.append(
                "作者信息脚注未能判定作者姓名（保持占位符，请人工填写）：" + cue[:60])
    return {"authors": authors, "received": received, "grants": grants,
            "warnings": warnings}


def parse_fund_line(text: str) -> str:
    """从正文/脚注的**独立一行** `基金项目：…` 取基金内容（90-08 实测：只在正文）。"""
    t = (text or "").strip()
    if not t or len(t) > 300:
        return ""
    m = re.match(r"^(?:" + _AI_FUND_LABEL + r")[ \t\u3000]*[:：][ \t\u3000]*(\S.*)$", t)
    if not m:
        return ""
    return m.group(1).strip().rstrip()


def merge_author_info(
    infos: list[dict], *, known_cn: tuple[str, ...] = (),
    known_en: tuple[str, ...] = (),
) -> dict:
    """合并多条作者信息脚注的解析结果（同名合并、英文块按邮箱/已知名单对齐中文记录）。"""
    all_recs: list[dict] = []
    received = ""
    grants = ""
    warnings: list[str] = []
    for info in infos or []:
        all_recs.extend(dict(r) for r in (info.get("authors") or []))
        received = received or str(info.get("received") or "")
        grants = grants or str(info.get("grants") or "")
        warnings.extend(info.get("warnings") or [])

    def _merge_into(tgt: dict, src: dict) -> None:
        for k in ("affiliation_cn", "affiliation_en"):
            for v in src.get(k) or []:
                if v not in tgt.setdefault(k, []):
                    tgt[k].append(v)
        if not tgt.get("email") and src.get("email"):
            tgt["email"] = src["email"]
        if not tgt.get("name_en") and src.get("name_en"):
            tgt["name_en"] = src["name_en"]
        if not tgt.get("name") and src.get("name"):
            tgt["name"] = src["name"]

    def _find_cn(name: str) -> dict | None:
        key = _ai_norm_name(name)
        for r in out:
            if r.get("name") and _ai_norm_name(r["name"]) == key:
                return r
        return None

    def _find_email(email: str) -> dict | None:
        e = (email or "").strip().lower()
        if not e:
            return None
        for r in out:
            if (r.get("email") or "").strip().lower() == e:
                return r
        return None

    out: list[dict] = []
    anon: list[dict] = []
    for rec in all_recs:
        if rec.get("name"):
            tgt = _find_cn(rec["name"])
            if tgt is None:
                _ai_new_record(out, name=rec["name"])
                tgt = out[-1]
            _merge_into(tgt, rec)
        elif rec.get("name_en"):
            anon.append(rec)          # 英文块：先放着，等中文记录齐了对齐
        else:
            anon.append(rec)          # 匿名单位/邮箱块
    en_pos = {_ai_norm_name(nm): i for i, nm in enumerate(known_en)}
    for rec in anon:
        tgt = _find_email(rec.get("email"))
        if tgt is None and rec.get("name_en") and len(known_cn) == len(known_en):
            i = en_pos.get(_ai_norm_name(rec["name_en"]))
            if i is not None and i < len(out) \
                    and _ai_norm_name(out[i].get("name")) == _ai_norm_name(known_cn[i]):
                tgt = out[i]
        if tgt is not None:
            _merge_into(tgt, rec)
        elif rec.get("name_en") and not out:
            _ai_new_record(out, name_en=rec["name_en"])
            _merge_into(out[-1], rec)
        elif not rec.get("name_en") and out:
            # 匿名单位/邮箱：只可能是第一作者的续行，归给第一条记录
            _merge_into(out[0], rec)
        elif rec.get("name_en"):
            out.append(rec)
    return {"authors": out, "received": received, "grants": grants,
            "warnings": warnings}


# ── C 类新口径（第 49 批，用户 2026-10-19 拍板；新 API 默认即新口径）────────────
#
# 最终参考文献 = 脚注引注文献 ∪ 文末清单；纯引用型脚注 → 正文 `\parencite[locator]{key}`
# （脚注不保留）；混合型（说明+引用）→ 脚注保留 + warning；note_only 原样。
# ⚠️ 兼容：旧 `classify()` 仍返回旧 kind（pure_ref/mixed/pure_note/classical），
#    现有 docxsource 渲染未切换前不会被新 API 影响；engine-core 接线时改用
#    `classify_citation()` + `union_entries()` 即可（接口见批 49 报告）。

CITATION_PURE = "pure_citation"       # 纯引用（短引用/页码级，可整体转正文 parencite）
CITATION_ENTRY = "reference_entry"    # 脚注里是完整文献条目
CITATION_MIXED = "mixed"              # 说明文字 + 引用（脚注保留 + 人工核对）
CITATION_MIXED_SPLIT = "mixed_split"  # 说明文字 + 可解析引用 → 拆分（B-199）
CITATION_NOTE = "note_only"           # 纯说明/古典文献（不进参考文献）

#: 散文判据：正文+引用的混合型脚注常见特征（批 47 论述性脚注实测）
_PROSE_CUES = re.compile(
    r"(笔者|作者认为|我们认为|参见|参阅|指出|所谓|这里|正如|不过|因此|当然|显然|"
    r"据此|也就是说|例如|即|换言之|反过来说|一方面|另一方面|总之|可见|"
    r"认为|指出|主张|强调)")


def _looks_prose(text: str) -> bool:
    """「说明文字 + 引用」判据：句子标点/篇幅/论述性用语。"""
    t = text.strip()
    if not t:
        return False
    if len(t) > 260:
        return True
    # 单句末「。」是中文文献条目的正常结尾，**不能**当散文判据（86-03 回归）：
    # 只认「≥2 个句末标点」或论述性用语。
    if t.rstrip().count("。") >= 2:
        return True
    return bool(_PROSE_CUES.search(t[:120]))


def _entry_complete(entry: dict | None) -> bool:
    """完整条目判据：题名 + 作者 + 出版语境（年/刊名/书名/出版社/卷）。

    只有「作者+题名+页码」的短引用（如 `Cf. Watson, “Free Agency,” p. 215.`）**不是**
    完整条目 → pure_citation，交给 resolve_citation 匹配；避免把页码级引注当新文献。
    """
    if not entry:
        return False
    if not str(entry.get("title") or "").strip():
        return False
    if not str(entry.get("author") or entry.get("editor") or "").strip():
        return False
    context = any(str(entry.get(name) or "").strip() for name in
                  ("year", "journal", "booktitle", "publisher", "address", "volume"))
    return context


# ── B-199 脚注引用提升：假 mixed 修正 / note_only 探测 / 真 mixed 拆分 ──────────
#
# 依据（B-198 批 A 分诊，2026-10-23）：
#   · 90-07 的 4 条「参阅 X, …」纯引用被 `_PROSE_CUES` 的「参阅」误判 mixed
#     （引导语在 prose 判定之前没有先试 parse）；
#   · 90-07 Kuhn / 90-06 Tolstoy 无年份条目被 legacy `classify()` 判 pure_note 后
#     `classify_citation()` 直接静默 note_only（日志零记录）；
#   · 86-08 混合脚注里的 Hacking 1999、88-02 混合脚注里的 Novaes 2018 不拆 →
#     ref 不入 bib、脚注内也没有 `\parencite`。
# 纪律（继承「不编造」）：所有字段都来自原文；只有「唯一命中」才提升；古典文献
# （SL/OP/罗马卷次）与引者注（笔者/本文/转引自…）不探测、不拆分。

#: 引用引导语（脚注引文的常见前缀；`See also` 必须先于 `See` 匹配）
_CUE_PREFIX = re.compile(
    r"^(?:(?:See also|see also|See|see|Cf\.|cf\.)\s*[，,、:：]?\s*|"
    r"(?:参阅|参见|又见|亦见|转引自|引自)\s*[，,、:：]?\s*)")

#: 句中引导语（非锚定；`_split_mixed_note_ref` 用，英文字界防误伤）
_CUE_MID = re.compile(
    r"(?:\bSee also\b|\bsee also\b|\bSee\b|\bsee\b|\bCf\.|\bcf\.|参阅|参见|转引自|引自)"
    r"\s*[，,、:：]?\s*")

#: 古典文献/缩写开头（即使条目里带年份也不拆分，保持脚注原样：88-02 #24/#85 实测）
_CLASSICAL_START = re.compile(
    r"^\s*(?:[A-Z][A-Za-z'’\-]+,\s*)?(?:SL|OP|Exp|Summa|Sent|Quodl|Ord)\b")

#: note_only 探测的引者注/说明注守卫（命中即不探测，保持纯注释）
_NOTE_PROBE_BAD = re.compile(
    r"笔者|我们|本文|拙文|作者认为|此注|前注|同上|见前|转引自|感谢|基金|"
    r"作者简介|收稿日期|电子信箱|上文|下文|参见前")

#: note_only 探测的中文散文守卫：书名号**前**的片段里出现论述性动词 =
#: 「库恩在《…》中指出…」型散文注，不是文献条目（独立验收者实测漏网）。
_NOTE_PROBE_PROSE_VERB = re.compile(
    r"在|认为|指出|主张|强调|写道|说道|提到|提及|表示|称|所述|所说|所论|"
    r"论述|讨论|谈到|分析|说明|告诉我们|可见")

#: 「作者, 名. (年) 题名. 地: 出版社.」式英文条目（86-08 Hacking 型）
_APA_ENTRY = re.compile(
    r"^(?P<author>[A-Z][A-Za-z'’\-]+(?:\s*,\s*[A-Z][A-Za-z.'’\-]+){1,3})\.\s*"
    r"\((?P<year>(?:19|20)\d{2})\)\s*"
    r"(?P<title>.+?)(?P<tend>[.!?])\s+"
    r"(?P<address>[A-Z][A-Za-z.'’\- ]{1,40}):\s*"
    r"(?P<publisher>[^.]{2,90})\.?$")

#: 拆分候选里「散文/引文串」开头词（这些绝不是文献条目的开头）
_REF_PROSE_START = re.compile(
    r"^(?:We|The|This|These|It|In|But|And|If|Our|His|Her|They|There|Here|"
    r"According|For|As|Since|However|Moreover|Note|Exactly|Consequently|Thus|"
    r"‘|“)")

#: 多作品拆分：中文书名号 / 英文题名引号
_WORK_CN_TITLE = re.compile(r"《([^》]{2,120})》")
_WORK_EN_TITLE = re.compile(r"[“\"]([^”\"]{2,120})[”\"]")


def strip_leading_cue(text: str) -> tuple[str, bool]:
    """剥掉开头的引用引导语（参阅/参见/See/Cf.）；返回 (余下文本, 是否剥过)。"""
    t = str(text or "").strip()
    m = _CUE_PREFIX.match(t)
    if not m:
        return t, False
    rest = t[m.end():].lstrip()
    return (rest, bool(rest)) if rest else (t, False)


def _parse_apa_entry(raw: str) -> dict | None:
    """「Hacking, Ian. (1999) The Social Construction of What? Cambridge: Harvard
    University Press.」式条目（作者. (年) 题名. 地: 出版社.）——只认完整形态。"""
    m = _APA_ENTRY.match(str(raw or "").strip())
    if not m:
        return None
    title = m.group("title").strip()
    if len(title) < 3:
        return None
    if m.group("tend") in "!?":
        title += m.group("tend")
    return {"_type": "book", "author": m.group("author").strip(),
            "title": title, "year": m.group("year"),
            "address": m.group("address").strip(),
            "publisher": m.group("publisher").strip().rstrip(".")}


_AUTHOR_PARTICLE = re.compile(r"^(?:van|von|de|der|den|la|le|di|da|du|ten|ter)\s+\S")
_AUTHOR_STOP = {"eg", "e.g", "e.g.", "ie", "i.e", "i.e.", "see", "cf", "cf.",
                "for", "and", "but", "the", "in", "if", "this", "we", "our",
                "his", "her", "they", "there"}


def _author_is_plausible(author: str) -> bool:
    """作者串像不像作者（拆分/探测闸门；88-01 实测 `eg`、`Buridan 1976, p` 是散文碎片）。"""
    a = str(author or "").strip()
    if not a or len(a) > 40:
        return False
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", a):
        return False
    if re.search(r"\d", a):
        return False
    first = a.split(",")[0].strip().lower()
    if first in _AUTHOR_STOP:
        return False
    if a[0].islower() and not _AUTHOR_PARTICLE.match(a):
        return False
    return True


_TITLE_BAD_START = re.compile(r"^(?:[§第\d]|[a-z])")
_TITLE_AUTHOR_YEAR = re.compile(
    r"^[A-Z][A-Za-z'’\-]+(?:\s*,\s*[A-Z][A-Za-z.'’\-]+)?\s*,?\s*\(?(?:19|20)\d{2}\)?\.?$")


def _title_is_plausible(title: str) -> bool:
    """题名像不像「作品题名」（拆分/探测闸门；防把短引用残渣当题名建条目）。

    负例（88-01/86-07 实测）：`§§ 147 and 148.`、`23; Tractatus …`、
    `Paul 1961`、`see Schupp 1988` —— 这些是页码/段号/裸作者年，不是题名。
    """
    t = str(title or "").strip()
    if len(_norm_fp(t)) < 4:
        return False
    if _TITLE_BAD_START.match(t):
        return False
    if _TITLE_AUTHOR_YEAR.match(t):
        return False
    if not re.search(r"[\u4e00-\u9fff]", t) and not any(c.islower() for c in t):
        return False
    return True


def _entry_quality(entry: dict | None) -> int:
    """完整条目「干净度」评分：字段多分高；题名臃肿/不像题名时扣分或直接不合格。

    只用于在多个解析通道的结果里挑一个（全部字段仍来自原文，不编造）。
    """
    if not _entry_complete(entry):
        return -1
    if not _title_is_plausible(str(entry.get("title") or "")):
        return -1
    if not _author_is_plausible(str(entry.get("author") or entry.get("editor") or "")):
        return -1
    score = sum(1 for name in ("author", "title", "year", "journal", "booktitle",
                               "publisher", "address", "volume")
                if str(entry.get(name) or "").strip())
    title = str(entry.get("title") or "")
    if len(title) > 90:
        score -= 3
    if any(mark in title for mark in ("”, in", '", in', "”in", '"in',
                                      "edited by", "eds.", ", in ")):
        score -= 3
    author = str(entry.get("author") or "")
    if len(author) > 40:
        score -= 3
    return score


def _best_complete_entry(raw: str) -> dict | None:
    """多通道解析，取「最干净的完整条目」；都不完整 → None（绝不拼字段）。"""
    t = str(raw or "").strip()
    if not t:
        return None
    candidates: list[dict] = []
    try:
        candidates.append(parse_entry(t))
    except Exception:  # noqa: BLE001 —— 解析失败按短引用处理
        pass
    apa = _parse_apa_entry(t)
    if apa:
        candidates.append(apa)
    try:
        from . import footref
        parsed = footref.parse_one(t)
        if parsed is None:
            _many = footref.parse_note(t)
            parsed = _many[0] if _many else None
        if parsed is not None:
            import dataclasses as _dc
            candidate = {k: v for k, v in _dc.asdict(parsed).items() if v}
            if candidate.get("etype") and not candidate.get("_type"):
                candidate = dict(candidate)
                candidate["_type"] = candidate.pop("etype")
            candidates.append(candidate)
    except Exception:  # noqa: BLE001
        pass
    extra = _extract_raw_fields(t)
    if extra:
        candidates.append(extra)
    best: dict | None = None
    best_score = 0
    for candidate in candidates:
        score = _entry_quality(candidate)
        if score > best_score:
            best, best_score = candidate, score
    return best


def _looks_like_citation_ref(ref: str) -> bool:
    """这段文本像不像「文献条目/引注」（拆分候选的闸门，防把散文/引文切进 bib）。"""
    t = str(ref or "").strip()
    if not t or len(t) < 8 or len(t) > 420:
        return False
    if looks_classical(t) or _CLASSICAL_START.match(t):
        return False
    if _REF_PROSE_START.match(t):
        return False
    if _WORK_CN_TITLE.search(t):
        return True
    if re.search(r"[\u4e00-\u9fff]", t):
        # 中文/中英混排：引号里是强调或引文（86-07「形式行」型），不构成题名证据；
        # 只认书名号（上面已查），否则不拆（宁可不拆，不可把散文切进 bib）。
        return False
    if _WORK_EN_TITLE.search(t):
        return True
    if _parse_apa_entry(t):
        return True
    return _entry_quality(_best_complete_entry(t)) >= 3


def _sentences(text: str) -> list[str]:
    """按**中文**句末标点切句（保留标点）。

    英文句点不可靠：人名缩写（`Sara L. Uckelmann`、`Hacking, Ian.`）会把条目切断，
    凡英文片段一律整段处理（拆不动就保持 mixed，宁可不拆不可错拆）。
    """
    t = str(text or "")
    out: list[str] = []
    start = 0
    for i, ch in enumerate(t):
        if ch in "。！？；":
            out.append(t[start:i + 1])
            start = i + 1
    if start < len(t):
        out.append(t[start:])
    return [s for s in out if s.strip()]


def _work_titles(ref: str) -> list[dict]:
    """从引用片段里抽「(题名, 年份)」列表（多作品拆分用）。

    与 `_work_count` 同一口径：分号分段；中文段只取**第一个**书名号（同段后面的
    《期刊名》/《丛书名》是容器，不是第二部作品）；英文段只取「“题名”, in …」形态。
    年份取该题名后、下一段之前的最近 4 位数。
    """
    t = str(ref or "")
    found: list[dict] = []
    offset = 0
    for seg in re.split(r"([；;])", t):
        if not seg or seg in ("；", ";"):
            offset += len(seg)
            continue
        seg_start = offset + (len(t[offset:]) - len(t[offset:].lstrip()))
        if re.search(r"[\u4e00-\u9fff]", seg):
            m = _WORK_CN_TITLE.search(seg)
            found_one = [(m.group(1).strip(), m.end(), m.start())] if m else []
        else:
            found_one = [(m.group(1).strip(), m.end(), m.start())
                         for m in _EN_WORK_TITLE.finditer(seg)]
        for title, end, start in found_one:
            tail = seg[end:]
            ym = re.search(r"(?:19|20)\d{2}", tail)
            found.append({"title": title, "year": ym.group(0) if ym else "",
                          "pos": seg_start + start})
        offset += len(seg)
    found.sort(key=lambda x: x["pos"])
    return found


_EN_WORK_TITLE = re.compile(r"[“\"]([^”\"]{2,120})[”\"]\s*,?\s*in\b")


def _work_count(ref: str) -> int:
    """引用片段里的**作品数**（只用于「多作品混合」判定，不解析字段）。

    · 分号分段，中文段按书名号计 1 部（同段的《期刊名》算容器，不算第二部）；
    · 英文段只认「“题名”, in …」形态的析出文献题名（中文文本里的引号是强调，
      不是作品，90-07 #6 实测：3 处引号会把纯引注误判成多作品）。
    """
    t = str(ref or "")
    total = 0
    for seg in re.split(r"[；;]", t):
        seg = seg.strip()
        if not seg:
            continue
        if re.search(r"[\u4e00-\u9fff]", seg):
            if _WORK_CN_TITLE.search(seg):
                total += 1
            continue
        en = len(_EN_WORK_TITLE.findall(seg))
        if en:
            total += en
        elif _parse_apa_entry(seg) or _looks_like_citation_ref(seg):
            total += 1
    return total


def _split_mixed_note_ref(text: str) -> dict | None:
    """把「说明文字 + 引用」文本拆成 (note_before, ref, note_after)。

    顺序：① 引导语处切（`参见/参阅/See/Cf.` 后紧跟文献段）② 逐句窗口找
    「像引注」的句子。都不成立 → None（保持 mixed，不拆）。
    """
    t = str(text or "").strip()
    if not t:
        return None
    # ① 引导语切分：从后往前试（句中最后一个引导语更可能是引文起点）
    cue_spans = [m for m in _CUE_MID.finditer(t) if m.start() > 0]
    for m in reversed(cue_spans):
        ref = t[m.end():].strip()
        if not _looks_like_citation_ref(ref):
            continue
        return {"note_before": t[:m.end()].strip(), "ref": ref,
                "note_after": "", "multi": _work_count(ref) >= 2}
    # ② 逐句窗口（单句/连续多句）找引注
    sents = _sentences(t)
    n = len(sents)
    for width in (1, 2, 3):
        for i in range(n - width + 1):
            ref = "".join(sents[i:i + width]).strip()
            if not _looks_like_citation_ref(ref):
                continue
            before = "".join(sents[:i]).strip()
            after = "".join(sents[i + width:]).strip()
            if not before and not after:
                continue
            return {"note_before": before, "ref": ref, "note_after": after,
                    "multi": _work_count(ref) >= 2}
    return None


def _note_only_probe(text: str) -> dict | None:
    """note_only 引用探测：纯注释里若含「作者/年份 + 题名」证据 → 可试唯一 resolve。

    守卫（宁可不探测，不可误提升）：
      · 作者信息/引者注（笔者/本文/前注/转引自…）不探测；
      · 古典文献（SL/OP/罗马卷次）不探测；
      · 中文：必须有**书名号题名**；只有《刊名》+年份+期号（无作者）不探测；
      · 英文：必须有年份（无年份的英文引注留给既有匹配通道，不额外探测）。
    """
    t = str(text or "").strip()
    if not t or len(t) > 160:
        return None
    if _AUTHOR_INFO.search(t):
        return None
    if _NOTE_PROBE_BAD.search(t):
        return None
    if t.count("。") > 1:
        return None
    if looks_classical(t) or _CLASSICAL_START.match(t):
        return None
    body, _had = strip_leading_cue(t)
    fields = _extract_raw_fields(body)
    title = str(fields.get("title") or "").strip()
    if len(_norm_fp(title)) < 3 or not _title_is_plausible(title):
        return None
    author = str(fields.get("author") or "").strip()
    year = str(fields.get("year") or "").strip()
    if re.search(r"[\u4e00-\u9fff]", body):
        m = _WORK_CN_TITLE.search(body)
        if not m:
            return None
        # 书名号前必须是「作者名」形态（不出现论述性动词/句子成分）
        if _NOTE_PROBE_PROSE_VERB.search(body[:m.start()]):
            return None
        if not author and re.search(
                r"《[^》]{2,}》\s*[，,]?\s*(?:19|20)\d{2}\s*年第", body):
            return None
    else:
        if not year:
            return None
        if author and not _author_is_plausible(author):
            return None
    if not (author or year):
        return None
    return {"fields": fields, "body": body}


def classify_citation(text: str) -> Footnote:
    r"""C 类新口径分类（第四十九批接口，engine-core 接线用）。

    返回 `Footnote`，`kind_new` ∈ {pure_citation, reference_entry, mixed, note_only,
    mixed_split}，并带 `ref_raw` / `entry` / `note` / `locator`：
      · pure_citation：短引用（作者+年/页码），`entry=None`，待 resolve 匹配；
      · reference_entry：脚注里是完整条目，`entry` = 解析字段；
      · mixed：说明文字 + 引用但**引用部分无法安全解析/匹配**（脚注保留 + 人工核对）；
      · mixed_split：说明文字 + 可解析引用 → 拆成 note_before/ref/note_after，
        ref 入 bib（或匹配既有键）、脚注保留并在原位注入 `\parencite`（B-199）；
      · note_only：纯说明/古典文献。

    B-199 变更（2026-10-23，依据 B-198 批 A 分诊）：① 先剥引导语（参阅/参见/See/
    Cf.）再判条目，引导语后紧跟完整文献字段时不再整条判 mixed；② 完整条目即使
    篇幅长（>260 字）或被散文判据命中，也不误判 mixed；③ legacy pure_note 里有
    「作者/年份 + 题名」证据且无引者注/古典标记时，升级为待匹配短引用（strict
    唯一匹配，匹配不上仍按纯注释保留）；④ 真 mixed 用 note/ref 拆分。
    """
    legacy = classify(text)
    fn = Footnote(fid=legacy.fid, text=legacy.text, kind=legacy.kind, ref=legacy.ref,
                  note=legacy.note, locator=legacy.locator, note_latex=legacy.note_latex)
    if legacy.kind in ("pure_note", "classical"):
        if legacy.kind == "pure_note":
            probe = _note_only_probe(legacy.text)
            if probe:
                fn.kind_new = CITATION_PURE
                fn.ref_raw = probe["body"]
                fn.from_note_probe = True
                fn.locator = extract_locator(probe["body"]) or legacy.locator
                return fn
        fn.kind_new = CITATION_NOTE
        fn.note = legacy.note or legacy.text
        fn.ref_raw = ""
        return fn
    if legacy.kind == "mixed":
        # 真 mixed（legacy 已切出 ref/note）：ref 能安全解析 → 拆分；否则保持 mixed。
        ref = legacy.ref or ""
        if _looks_like_citation_ref(ref):
            entry = _best_complete_entry(ref)
            fn.kind_new = CITATION_MIXED_SPLIT
            fn.ref_raw = ref
            fn.entry = entry or {}
            fn.note_after = legacy.note or ""
            fn.multi_work = _work_count(ref) >= 2
            fn.locator = extract_locator(ref) or legacy.locator
            return fn
        fn.kind_new = CITATION_MIXED
        fn.ref_raw = ref
        return fn
    head = legacy.ref or ""
    if _looks_prose(head):
        stripped, had_cue = strip_leading_cue(head)
        multi_whole = _work_count(head) >= 2
        # ① 先试拆 note/ref：句中有引导语（86-08 Hacking「…参见Hacking, Ian. (1999)…」、
        #    88-02 Freddoso「… See Alfred Freddoso…」）或中文献句（90-07 #6）。
        split = _split_mixed_note_ref(head)
        if split and _looks_like_citation_ref(split["ref"]):
            entry = _best_complete_entry(split["ref"])
            fn.kind_new = CITATION_MIXED_SPLIT
            fn.ref_raw = split["ref"]
            fn.entry = entry or {}
            fn.note_before = split["note_before"]
            fn.note_after = split["note_after"]
            fn.multi_work = bool(split["multi"])
            fn.locator = extract_locator(split["ref"]) or extract_locator(head) \
                or legacy.locator
            if fn.multi_work and not fn.note_before and not fn.note_after:
                fn.keep_whole = True
            return fn
        # ② 整段就是一条完整条目（90-07「参阅 R. Camap…」型；长条目 88-02 #27 型）
        #   → 按完整条目提升（引导语在开头时先剥掉）。
        if not multi_whole and _looks_like_citation_ref(stripped if had_cue else head):
            entry = _best_complete_entry(stripped if had_cue else head)
            if entry:
                fn.kind_new = CITATION_ENTRY
                fn.entry = entry
                fn.ref_raw = stripped if had_cue else head
                fn.cue_stripped = had_cue
                fn.locator = extract_locator(fn.ref_raw) or legacy.locator
                return fn
        # ③ 整段多作品引用（88-02 Novaes 型：一条脚注混两部书，无说明文字）→
        #    保留原文、另按作品解析条目（docxsource 能把各作品唯一匹配到既有键时只注入 cite）。
        if multi_whole:
            fn.kind_new = CITATION_MIXED_SPLIT
            fn.ref_raw = head
            fn.entry = _best_complete_entry(head) or {}
            fn.multi_work = True
            fn.keep_whole = True
            fn.locator = extract_locator(head) or legacy.locator
            return fn
        fn.kind_new = CITATION_MIXED
        fn.ref_raw = head
        fn.note = legacy.note or ""
        return fn
    entry = parse_entry(head)
    if not _entry_complete(entry):
        # 英文脚注条目：parse_entry 的严格通道可能只给 note；这里补 footref 的宽松解析，
        # 只用于「是不是完整条目」判定与并集候选（不编造字段）。
        try:
            from . import footref
            parsed = footref.parse_one(head)
            if parsed is None:
                _many = footref.parse_note(head)
                parsed = _many[0] if _many else None
            if parsed is not None:
                import dataclasses as _dc
                candidate = {k: v for k, v in _dc.asdict(parsed).items() if v}
                if _entry_complete(candidate):
                    entry = candidate
        except Exception:  # noqa: BLE001 —— 解析失败就按短引用处理
            pass
    if not _entry_complete(entry):
        # 中文脚注条目：parse_entry/parse_one 可能只给 note；用原文抽字段兜底
        # （只用于判定「完整条目」与并集候选；字段全部来自原文，不编造）
        extra = _extract_raw_fields(head)
        if _entry_complete(extra):
            entry = extra
    if _entry_complete(entry):
        fn.kind_new = CITATION_ENTRY
        fn.entry = entry
    else:
        fn.kind_new = CITATION_PURE
    fn.ref_raw = head
    fn.locator = extract_locator(head) or legacy.locator
    return fn


#: 第四十九/五十批 C 类新口径**总开关**：第五十二批（v9b 全量 PASS 后，用户拍板）
#: **默认开**。切换依据：v9b 41/41 rc=0、compile_ok、undef/dangling 与 v8 相同、
#: P0 集合与 v8 相同、四专项/回归全过、U1（脚注公式二次转义）已修。
#: 显式传 False（CLI `--no-c-class-union` / build_docx(c_class_union=False)）回退旧行为，
#: 旧行为逐字节不变（v8 基线仍可复现，供回退/对照）。
#: ⚠️ 本文件未改旧 `classify()`；低层 `parse_docx()` 默认仍 False（docx_audit
#: 冻结基线不能变），只有 build_docx / convert CLI 这一层把 None 解析成默认开。
C_CLASS_UNION_DEFAULT = True


def c_class_union_enabled(flag: bool | None = None) -> bool:
    """C 类新口径是否启用（**默认开**；传 False 回退旧行为，传 True 显式开）。"""
    return C_CLASS_UNION_DEFAULT if flag is None else bool(flag)


# ── 并集构建（第四十九批接口）────────────────────────────────────────────────
#
# 指纹 =（第一作者姓归一 + 题名归一 + 4 位年份 + 类型族），**忽略**
# pages/page/edition/volume/number/publisher/address/locator；
# 同文献仅页码不同 → 合并为 1 条参考文献（正文各处各自 parencite[locator]）。
# 并集：文末清单优先（字段更全/文末版本），脚注补充缺失字段；冲突记 warning。

_TYPE_FAMILY = {
    "article": "article", "incollection": "incollection", "inproceedings": "incollection",
    "inbook": "incollection", "book": "book", "collection": "book", "proceedings": "book",
    "phdthesis": "thesis", "mastersthesis": "thesis", "thesis": "thesis",
    "techreport": "report", "report": "report", "misc": "misc", "unpublished": "misc",
    "online": "misc", "dataset": "misc", "booklet": "misc",
}


_CN_DIGITS = {"零": "0", "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
              "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
              "上": "上", "下": "下", "中": "中"}


def _norm_volume(value: object) -> str:
    """卷号/册号归一：`第一卷`/`第1卷`/`Vol.1`/`1`/`Volume 1` → 统一数字串。

    ⚠️ 用户 2026-10-19 澄清：卷号不同的同书是**不同文献**（如《亚里士多德全集》
    第一卷 vs 第二卷），必须参与判重；页码/页码定位仍不进指纹。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    low = text.lower()
    digits = re.search(r"\d+", low)
    if digits:
        return str(int(digits.group(0)))
    for cn, num in _CN_DIGITS.items():
        if cn in text:
            return num
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", low)[:12]


def _norm_edition(value: object) -> str:
    """版次归一：`2nd ed.`/`第二版`/`2` → 数字串；无版次 → ""。"""
    text = str(value or "").strip()
    if not text:
        return ""
    low = text.lower()
    digits = re.search(r"\d+", low)
    if digits:
        return str(int(digits.group(0)))
    for cn, num in _CN_DIGITS.items():
        if cn in text:
            return num
    if "first" in low:
        return "1"
    if "second" in low:
        return "2"
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", low)[:12]


@dataclass
class FootnoteWarning:
    """C 类并集/分类的 warning（build/report 消费；不编造、不静默）。"""
    code: str                    # mixed_manual_check / citation_unmatched / field_conflict
    fid: int = -1
    text: str = ""
    reason: str = ""
    author: str = ""
    title: str = ""
    year: str = ""
    locator: str = ""


def _norm_fp(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(text or "").lower())


_PLACEHOLDER_TOKENS = {"未识别", "无", "none", "null", "n/a", "na", "tbd", "untitled",
                       "佚名", "unknown", "佚名氏"}


def entry_fingerprint(entry: dict) -> tuple | None:
    """去重指纹：第一作者姓 + 题名 + 年份 + 类型族 + **卷号** + **版次**。

    ⚠️ **安全闸（第五十批修 v7 过并）**：
      · author 空/占位、或 title 空/占位/仅页码 → 返回 **None**（该条禁止参与合并）；
      · 归一后题名 < 4 字（如「逻辑」「Table」）→ **None**（不足单独作依据）；
      · 卷号/版次参与判重；页码/页码定位不参与（同文献不同页码必须能合并）。
    """
    entry = entry or {}
    author = str(entry.get("author") or entry.get("editor") or "").strip()
    title = str(entry.get("title") or entry.get("booktitle") or "").strip()
    if not author or not title:
        return None
    author_norm = _norm_fp(author)
    title_norm = _norm_fp(title)
    if author_norm in _PLACEHOLDER_TOKENS or title_norm in _PLACEHOLDER_TOKENS:
        return None
    if len(title_norm) < 4:
        return None
    if re.fullmatch(r"(?:pp?\.?\s*\d+(?:\s*[-\u2013]\s*\d+)?|\d+(?:\s*[-\u2013]\s*\d+)?)", title.strip()):
        return None
    first = re.split(r"\s+and\s+", author, flags=re.I)[0].strip()
    if "," in first:
        surname = first.split(",")[0]
    else:
        toks = [t for t in re.split(r"\s+", first) if t]
        surname = toks[-1] if toks else ""
    year = re.sub(r"\D", "", str(entry.get("year") or ""))[:4]
    etype = str(entry.get("etype") or entry.get("entry_type") or "").lower()
    return (_norm_fp(surname), title_norm, year, _TYPE_FAMILY.get(etype, etype),
            _norm_volume(entry.get("volume")), _norm_edition(entry.get("edition")))


def union_entries(end_list_entries: list[tuple[str, dict, str]],
                  footnote_entries: list[tuple[str, dict, str]],
                  *, warnings: list[FootnoteWarning] | None = None,
                  keymap: dict[str, str] | None = None) -> list[tuple[str, dict, str]]:
    """并集构建：文末清单 ∪ 脚注条目（按指纹合并，文末版本优先）。

    · 返回 `[(key, entry, raw), ...]`（可直接赋给 `doc.bib_entries` 风格）；
    · 同指纹：文末条目保留、脚注缺字段补进文末条目；字段冲突记 field_conflict warning；
    · 同文献仅页码/版次不同 → 指纹相同 → 合并 1 条（脚注条目含 locator 由正文 parencite 带走）；
    · 不做「编造」：调用方无法解析的短引用不在这里生成条目（用 FootnoteWarning 报）。
    """
    warnings = warnings if warnings is not None else []
    keymap = keymap if keymap is not None else {}
    merged: list[tuple[str, dict, str]] = []
    by_fp: dict[tuple, int] = {}
    by_base: dict[tuple, list[int]] = {}

    def _add(key, entry, raw):
        fp = entry_fingerprint(entry)
        if fp is None:
            merged.append((key, dict(entry), raw))   # 指纹不可用 → 永不合并
            return
        by_fp.setdefault(fp, len(merged))
        by_base.setdefault(fp[:4], []).append(len(merged))
        merged.append((key, dict(entry), raw))

    for key, entry, raw in end_list_entries:
        fp = entry_fingerprint(entry)
        if fp is not None and fp in by_fp:
            _fill_missing(merged[by_fp[fp]][1], entry)
            keymap.setdefault(key, merged[by_fp[fp]][0])
            continue
        _add(key, entry, raw)
    for key, entry, raw in footnote_entries:
        fp = entry_fingerprint(entry)
        if fp is None:
            # 指纹不可用（author/title 空/占位/仅页码/题名<4 字）→ 禁止合并
            merged.append((key, dict(entry), raw))
            continue
        vol = fp[4]
        ed = fp[5]
        # ① 完全同指纹（含卷号/版次）→ 合并；文末版本优先、脚注补缺、冲突记 warning
        if fp is not None and fp in by_fp:
            target = merged[by_fp[fp]][1]
            keymap.setdefault(key, merged[by_fp[fp]][0])
            for name, value in entry.items():
                if not value:
                    continue
                existing = target.get(name)
                if not existing:
                    target[name] = value
                elif str(existing) != str(value) and name not in (
                        "locator", "pages", "page", "volume", "edition"):
                    warnings.append(FootnoteWarning(
                        "field_conflict", text=str(raw)[:200],
                        reason=f"文末与脚注字段 {name} 不一致，保留文末值",
                        title=str(entry.get("title") or ""), year=str(entry.get("year") or "")))
            continue
        # ② 基础身份相同（作者/题名/年/类型）但卷号或版次不同 → 处理规则（用户拍板）：
        base = fp[:4] if fp is not None else None
        if base is None:
            _add(key, entry, raw)
            continue
        partial = False
        for index in by_base.get(base, []):
            other = merged[index][1]
            other_vol = _norm_volume(other.get("volume"))
            other_ed = _norm_edition(other.get("edition"))
            if other_vol and vol and other_vol != vol:
                partial = True   # 卷号都不同 → 不同文献（合法，不 warning）
                continue
            if other_ed and ed and other_ed != ed:
                partial = True   # 版次不同 → 不同文献（合法）
                continue
            if (bool(other_vol) != bool(vol)) or (bool(other_ed) != bool(ed)):
                # 一方有卷号/版次、另一方没有 → **不自动合并**，记录 warning 交调用方决定
                warnings.append(FootnoteWarning(
                    "field_conflict", text=str(raw)[:200],
                    reason=("卷号信息不完整（文末与脚注一方缺失），不自动合并，保留两条待人工确认"
                            if bool(other_vol) != bool(vol)
                            else "版次信息不完整（文末与脚注一方缺失），不自动合并，保留两条待人工确认"),
                    title=str(entry.get("title") or ""), year=str(entry.get("year") or "")))
                partial = True
                continue
        _add(key, entry, raw)
    return merged


def _title_similarity(a: str, b: str) -> float:
    import difflib
    na, nb = _norm_fp(a), _norm_fp(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _surname(entry_or_author) -> str:
    if isinstance(entry_or_author, dict):
        author = str(entry_or_author.get("author") or entry_or_author.get("editor") or "")
    else:
        author = str(entry_or_author or "")
    first = re.split(r"\s+and\s+", author, flags=re.I)[0].strip()
    if "," in first:
        return _norm_fp(first.split(",")[0])
    toks = [t for t in re.split(r"\s+", first) if t]
    return _norm_fp(toks[-1]) if toks else ""


def _completeness(entry: dict) -> int:
    return sum(1 for name in ("author", "title", "year", "journal", "booktitle",
                              "publisher", "address", "volume", "pages")
               if str(entry.get(name) or "").strip())


_FULLWIDTH = str.maketrans({src: dst for src, dst in zip(
    "０１２３４５６７８９（）［］，。；：、“”‘’",
    "0123456789()[],.;:," + '"' + '"' + "'" + "'" )})


def _author_surname(text: str) -> str:
    """作者归一：中文去「主编/著/编/译/等」；英文取姓（逗号前 / 末词）；全半角+标点归一。"""
    text = str(text or "").translate(_FULLWIDTH).strip()
    text = re.sub(r"^(Cf\.|cf\.|参见|参阅|转引自|引自)\s*", "", text)
    if re.search(r"[\u4e00-\u9fff]", text):
        head = re.split(r"[，,。；;：:（(《]| and | 等", text)[0]
        head = re.sub(r"(主编|编著|编译|著|编|译|校|等)+$", "", head.strip())
        return _norm_fp(head)[:12]
    first = re.split(r"\s+and\s+", text, flags=re.I)[0]
    if "," in first:
        first = first.split(",")[0]
    toks = [t for t in re.split(r"\s+", first.strip(" .,;:")) if t]
    if not toks:
        return ""
    surname = toks[-1]
    # 荷兰/德语虚词并入姓（van Fraassen / von Wright）
    if len(toks) >= 2 and toks[-2].lower() in ("van", "von", "de", "der", "den", "la", "le"):
        surname = toks[-2] + surname
    return _norm_fp(surname)[:12]


def _extract_raw_fields(raw: str) -> dict:
    """从原文（脚注引用/note-only 条目）抽 author/title/year/volume/edition（不编造）。"""
    fields: dict = {}
    raw = str(raw or "").strip()
    if not raw:
        return fields
    if re.search(r"[\u4e00-\u9fff]", raw):
        head = re.split(r"[：:《（(]", raw)[0]
        head = re.sub(r"(主编|编著|编译|著|编|译|校|等)+$", "", head.strip())
        if head:
            fields["author"] = head[:30]
        # 中文「作者：题名，期刊…」式：冒号后到下一个逗号/句号是题名
        m = re.match(r"[^：:]*[：:]([^，,。；;]{2,80})", raw)
        if m:
            fields.setdefault("title", m.group(1).strip())
    else:
        body = re.sub(r"^(Cf\.|cf\.|See|see|参见|参阅|转引自|引自)\s*", "", raw)
        head = re.split(r"[,”\"‘《,]", body)[0]
        words = [w for w in re.split(r"\s+", head.strip()) if w]
        name = []
        for word in words[:6]:
            if re.fullmatch(r"[A-Z]\.?", word) or re.fullmatch(r"[A-Z][a-z'\-]+", word) \
                    or re.fullmatch(r"[A-Z][A-Z'\-]+", word):
                name.append(word)
            else:
                break
        if name:
            fields["author"] = " ".join(name)
    for pat in (r"[“\"‘]([^”\"’]{2,120})[”\"’]", r"《([^》]{2,120})》"):
        m = re.search(pat, raw)
        if m:
            fields["title"] = m.group(1)
            break
    if not fields.get("title") and not re.search(r"[\u4e00-\u9fff]", raw):
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) >= 2 and 2 < len(parts[1]) <= 120 \
                and not re.match(r"^(?:pp?\.\s*\d|[0-9]+\s*[-\u2013]\s*\d+)", parts[1]):
            fields["title"] = parts[1]
    m = re.search(r"(?:19|20)\d{2}", raw)
    if m:
        fields["year"] = m.group(0)
    m = re.search(r"(第\s*[0-9一二三四五六七八九十]+\s*[卷册]|(?:Vol|vol|Volume)\.?\s*[0-9]+)", raw)
    if m:
        fields["volume"] = m.group(1)
    m = re.search(r"(第\s*[0-9一二三四五六七八九十]+\s*版|[0-9]+(?:st|nd|rd|th)\s*(?:ed\.?)?)", raw, re.I)
    if m:
        fields["edition"] = m.group(1)
    return fields


def _enrich_entry(entry: dict, raw: str) -> dict:
    """候选扩展：note-only 条目用原文补 author/title/year（不覆盖已有字段）。"""
    merged = dict(entry or {})
    if not merged.get("author") or not merged.get("title"):
        extra = _extract_raw_fields(str(merged.get("note") or raw or ""))
        for name, value in extra.items():
            merged.setdefault(name, value)
    return merged


def _extract_citation_fields(citation: Footnote) -> dict:
    """从短引用原文抽 author/title/year/volume/edition（不编造：只抽原文可见信息）。"""
    raw = str(citation.ref_raw or citation.ref or citation.text or "").strip()
    fields = dict(citation.entry or {})
    if not fields.get("author") or not fields.get("title"):
        for name, value in _extract_raw_fields(raw).items():
            fields.setdefault(name, value)
    if False:  # 旧内联解析保留为空（已由 _extract_raw_fields 覆盖）
        pass
    if not fields.get("author"):
        if re.search(r"[\u4e00-\u9fff]", raw):
            head = re.split(r"[：:《（(]", raw)[0]
            head = re.sub(r"(主编|编著|编译|著|编|译|校|等)+$", "", head.strip())
            if head:
                fields["author"] = head[:30]
        else:
            body = re.sub(r"^(Cf\.|cf\.|See|see|参见|参阅|转引自|引自)\s*", "", raw)
            m = re.match(r"((?:[A-Z]\.\s*)*[A-Z][A-Za-z'\-]+(?:\s+(?:[A-Z]\.\s*)?[A-Z][A-Za-z'\-]+)*)", body)
            if m:
                fields["author"] = m.group(1)
    if not fields.get("title"):
        for pat in (r"[“\"‘]([^”\"’]{2,120})[”\"’]", r"《([^》]{2,120})》"):
            m = re.search(pat, raw)
            if m:
                fields["title"] = m.group(1)
                break
        if not fields.get("title") and not re.search(r"[\u4e00-\u9fff]", raw):
            # `Author, Title, Journal…` 无引号写法：取第一个逗号后到第二个逗号/年份
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) >= 2 and 2 < len(parts[1]) <= 120 \
                    and not re.match(r"^(?:pp?\.\s*\d|[0-9]+\s*[-\u2013]\s*\d+)", parts[1]):
                fields["title"] = parts[1]
    if not fields.get("year"):
        m = re.search(r"(?:19|20)\d{2}", raw)
        if m:
            fields["year"] = m.group(0)
    if not fields.get("volume"):
        m = re.search(r"(第\s*[0-9一二三四五六七八九十]+\s*[卷册]|(?:Vol|vol|Volume)\.?\s*[0-9]+)", raw)
        if m:
            fields["volume"] = m.group(1)
    if not fields.get("edition"):
        m = re.search(r"(第\s*[0-9一二三四五六七八九十]+\s*版|[0-9]+(?:st|nd|rd|th)\s*(?:ed\.?)?)", raw, re.I)
        if m:
            fields["edition"] = m.group(1)
    return fields


def _surname_similarity(a: str, b: str) -> float:
    import difflib
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _title_match_tier(cit_title: str, cand_title: str) -> float:
    """题名相似度：精确 1.0；包含 0.95；token/序列 0~1。"""
    a, b = _norm_fp(cit_title), _norm_fp(cand_title)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.95
    import difflib
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    # token 交集（英文词级）补充
    ta = set(re.findall(r"[a-z]+", cit_title.lower()))
    tb = set(re.findall(r"[a-z]+", cand_title.lower()))
    if ta and tb:
        score = len(ta & tb) / max(1, min(len(ta), len(tb)))
        ratio = max(ratio, score)
    return ratio


def build_citation_index(entries: list[tuple[str, dict, str]]) -> dict:
    """作者-年索引（surname → 条目下标；surname+year → 下标），供 resolve 候选扩展。"""
    index: dict = {"by_surname": {}, "by_surname_year": {}}
    for i, (_key, entry, _raw) in enumerate(entries):
        entry = _enrich_entry(entry, _raw)
        surname = _author_surname(entry.get("author") or entry.get("editor") or "")
        year = re.sub(r"\D", "", str(entry.get("year") or ""))[:4]
        if surname:
            index["by_surname"].setdefault(surname, []).append(i)
            if year:
                index["by_surname_year"].setdefault((surname, year), []).append(i)
    return index


def resolve_citation(citation: Footnote,
                     entries: list[tuple[str, dict, str]],
                     *, title_threshold: float = 0.9,
                     index: dict | None = None,
                     stats: dict | None = None,
                     keymap: dict[str, str] | None = None,
                     strict: bool = False) -> tuple[str | None, FootnoteWarning | None]:
    """短引用 → 已存在条目 key（分档匹配：作者-年+题名相似度+字段完整度）；不编造。

    分档：① 完整指纹（含卷/版）② 姓+年+题名≥阈值 ③ 姓+题名≥阈值（一方缺年）
          ④ 姓+年唯一候选（候选仅 1 条）；题目/年份/卷版冲突直接拒绝。
    B-199：题名不匹配时允许 **container/booktitle** 匹配（候选条目的题名作为容器
    题名在引注原文里逐字出现）。
    `strict=True`（note_only 探测用）：必须题名匹配、tier≤3、相似度 ≥0.92、
    最佳档候选**唯一**，否则不提升（宁可不提升，不可误提升）。
    返回 `(key|None, warning|None)`；失败 = `citation_unmatched`（调用方保留原脚注/正文）。
    """
    fields = _extract_citation_fields(citation)
    surname = _author_surname(fields.get("author") or "")
    title = str(fields.get("title") or "")
    year = re.sub(r"\D", "", str(fields.get("year") or ""))[:4]
    vol = _norm_volume(fields.get("volume"))
    ed = _norm_edition(fields.get("edition"))
    if index is None:
        index = build_citation_index(entries)
    candidates: list[int] = []
    if surname:
        candidates = list(index["by_surname"].get(surname, []))
        if not candidates:
            # 姓拼写容错（Waston/Watson 类）：相似度 ≥0.8 或前缀 4 字符相同
            candidates = [i for i, (_k, entry, _r) in enumerate(entries)
                          if _surname_similarity(
                              surname, _author_surname(_enrich_entry(entry, _r).get("author")
                                                       or _enrich_entry(entry, _r).get("editor") or "")) >= 0.8]
    if not candidates:
        return None, FootnoteWarning(
            "citation_unmatched", fid=citation.fid, text=citation.text[:200],
            reason="纯引用无法匹配完整条目（作者不匹配，不编造）",
            author=str(fields.get("author") or ""), title=title, year=year,
            locator=citation.locator)
    scored: list[tuple[tuple, str]] = []
    volume_ambiguous = False
    container_keys: set[str] = set()
    for i in candidates:
        key, entry, _raw = entries[i]
        entry = _enrich_entry(entry, _raw)
        cand_surname = _author_surname(entry.get("author") or entry.get("editor") or "")
        cand_year = re.sub(r"\D", "", str(entry.get("year") or ""))[:4]
        cand_title = str(entry.get("title") or "")
        if year and cand_year and year != cand_year:
            continue                      # 年份强约束：不同年不匹配
        cand_vol = _norm_volume(entry.get("volume"))
        cand_ed = _norm_edition(entry.get("edition"))
        if vol and cand_vol and vol != cand_vol:
            continue                      # 卷号强约束
        if ed and cand_ed and ed != cand_ed:
            continue                      # 版次强约束
        if not vol and not cand_vol and title:
            # 题名相同但对方有卷号 → 无法唯一确定是哪一卷 → 不猜
            same_title = [j for j in candidates
                          if _norm_fp(str(entries[j][1].get("title") or "")) == _norm_fp(title)
                          and _norm_volume(entries[j][1].get("volume"))]
            if len(same_title) >= 1 and not vol:
                volume_ambiguous = True
        sim = _title_match_tier(title, cand_title) if title else 0.0
        # B-199：container/booktitle 匹配（90-06 的 `“Volitional Necessities”, in
        # Agency and Answerability, pp. 106-107.` 型）——析出文献题名与 bib 条目的
        # **书名/容器**题名不同，但容器题名在引注原文里逐字出现。只在「作者姓匹配
        # + 容器题名归一后 ≥8 字符 + 唯一命中」时启用；不编造、不改条目字段。
        _container_hit = False
        if title and sim < title_threshold:
            _raw_all = str(citation.ref_raw or citation.ref or citation.text or "")
            _cnorm = _norm_fp(cand_title)
            if len(_cnorm) >= 8 and _cnorm in _norm_fp(_raw_all):
                sim = max(sim, 0.95)
                _container_hit = True
        surname_ok = _surname_similarity(surname, cand_surname) >= 0.8
        if not surname_ok:
            continue
        if title:
            if sim < title_threshold:
                continue
            tier = 1 if (year and cand_year == year and sim >= 0.999) else (
                2 if (year and cand_year == year) else 3)
        else:
            # 无题名（仅作者+页码）：姓+年精确，或同姓唯一候选（作者可唯一确定）
            if year and cand_year == year:
                tier = 4
            elif not year:
                same_surname = [j for j in candidates
                                if _surname_similarity(surname, _author_surname(
                                    _enrich_entry(entries[j][1], entries[j][2]).get("author")
                                    or _enrich_entry(entries[j][1], entries[j][2]).get("editor") or "")) >= 0.8]
                # 候选扩展：同姓候选里**有题名**的若只剩一种作品 → 可唯一确定
                named = {}
                for j in same_surname:
                    e = _enrich_entry(entries[j][1], entries[j][2])
                    title_j = str(e.get("title") or "")
                    if title_j:
                        named.setdefault(entry_fingerprint(e), (j, e))
                if len(named) == 1:
                    only = next(iter(named.values()))
                    scored.append(((4, -_completeness(only[1]), 0.0), entries[only[0]][0]))
                    continue
                elif len(same_surname) == 1:
                    tier = 4
                else:
                    continue
            else:
                continue
        scored.append(((tier, -sim, -_completeness(entry)), key))
        if _container_hit:
            container_keys.add(key)
    if stats is not None:
        stats["candidates"] = len(candidates)
    if not scored:
        if stats is not None:
            stats["tier"] = 0
        reason = ("多个候选卷号无法唯一确定（不猜）" if volume_ambiguous
                  else "纯引用无法匹配完整条目（作者/题名/年份/卷版不匹配，不编造）")
        return None, FootnoteWarning(
            "citation_unmatched", fid=citation.fid, text=citation.text[:200],
            reason=reason, author=str(fields.get("author") or ""), title=title,
            year=year, locator=citation.locator)
    scored.sort(key=lambda item: (item[0], item[1]))
    best_tier, best_key = scored[0][0][0], scored[0][1]
    # 同档多候选且卷号不一致 → 歧义，不猜
    top = [key for score, key in scored if score[0] == best_tier]
    if len(top) > 1 and best_tier <= 3:
        vols = {_norm_volume(next(e for k, e, _ in entries if k == key).get("volume"))
                for key in top}
        if len(vols) > 1:
            return None, FootnoteWarning(
                "citation_unmatched", fid=citation.fid, text=citation.text[:200],
                reason="同作者/年/题名有多个卷号候选，无法唯一确定（不猜）",
                author=str(fields.get("author") or ""), title=title, year=year,
                locator=citation.locator)
    if best_key in container_keys and len(container_keys) > 1:
        # container 匹配命中多个候选 → 无法唯一确定是哪部作品（不猜）
        return None, FootnoteWarning(
            "citation_unmatched", fid=citation.fid, text=citation.text[:200],
            reason="多个容器题名候选，无法唯一确定（不猜）",
            author=str(fields.get("author") or ""), title=title, year=year,
            locator=citation.locator)
    if strict:
        # B-199 note_only 探测：必须题名命中、非「仅作者+年」档、相似度 ≥0.92、
        # 最佳档候选唯一（多条不同条目同档 → 歧义，不猜）。
        best_sim = -scored[0][0][1]
        distinct = set(top)
        if (not title or best_tier not in (1, 2, 3)
                or best_sim < 0.92 or len(distinct) != 1):
            return None, FootnoteWarning(
                "citation_unmatched", fid=citation.fid, text=citation.text[:200],
                reason="note_only 探测非唯一命中（不提升，不编造）",
                author=str(fields.get("author") or ""), title=title, year=year,
                locator=citation.locator)
    if stats is not None:
        stats["tier"] = best_tier
    # 被并键不得再被 cite：返回并集**最终键**（union_entries 的 old→new 映射）
    final_key = (keymap or {}).get(best_key, best_key)
    return final_key, None


def _fill_missing(target: dict, source: dict) -> None:
    for name, value in source.items():
        if value and not target.get(name):
            target[name] = value


# ── C 类口径开关（第 46 批；**默认关**，等编辑部/用户拍板后才启用）──────────────
#
# 背景（90-06/90-07、86-07 实测）：Word 稿同时有「文末参考文献区/编号清单」与
# 「脚注式引注」时，引擎把**两路都**生成 bib 条目 → 参考文献区比人工成品多出
# 10–47 条（人工只保留文末清单）。编辑部尚未拍板是否一律「文末清单优先」，
# 因此这里只提供**判据**（默认行为不变），接线由 build.py 在拍板后加 3–5 行：
#
#     from .footnotes import footnote_promotion_allowed
#     has_end_list = bool(doc.refs or doc.ref_raw_lines or doc.ref_key_by_num)
#     if not footnote_promotion_allowed(has_end_list=has_end_list,
#                                       end_list_priority=getattr(args, "end_list_priority", False)):
#         keep = set(doc.ref_key_by_num.values())
#         doc.bib_entries = [e for e in doc.bib_entries if e[0] in keep]
#
# ⚠️ 拍板启用时还需 docxsource 的渲染一并改（脚注引注仍渲染为脚注文本，而不是
#    留下悬空 `\parencite`）——那是 engine-core 的写范围，见批 46 报告。


def footnote_promotion_allowed(*, has_end_list: bool,
                               end_list_priority: bool = False) -> bool:
    """脚注文献是否**逐条提升**为 bib 条目（C 类口径判据）。

    · 默认（`end_list_priority=False`）→ 始终 True：与现行为逐字节一致；
    · 开启且**存在文末清单**（人年制/编号清单任一路）→ False：只保留文末清单；
    · 开启但**没有文末清单**（纯脚注体例，如 86-03）→ 仍 True：否则参考文献全空。
    """
    if end_list_priority and has_end_list:
        return False
    return True


def filter_footnote_entries(entries: list[tuple[str, dict, str]],
                            keep_keys: set[str]) -> list[tuple[str, dict, str]]:
    """给 build.py 接线用：文末清单优先时，只保留编号清单键对应的 bib 条目。

    （脚注条目与编号清单条目在 `doc.bib_entries` 里混排；编号清单键来自
    `doc.ref_key_by_num.values()`，人年制条目不在 `bib_entries` 里。）
    """
    return [entry for entry in entries if entry[0] in keep_keys]


# ── 页码定位 ──────────────────────────────────────────────────────────────

# `pp. 57-58, pp. 63-64` → 人工取了**最后一个**区间（63--64），因为那才是被引的具体页
_PAGE_RANGE = re.compile(r"\bpp?\.\s*(\d+)\s*[-–—]\s*(\d+)")
_PAGE_SINGLE = re.compile(r"\bpp?\.\s*(\d+)(?!\s*[-–—])")
_PAGE_CN = re.compile(r"[:：]\s*(\d+)\s*[-–—]\s*(\d+)")


def extract_locator(ref: str) -> str:
    """从文献条目里取 \\parencite[...] 的页码定位。

    人工惯例：多个页码区间时取**最后一个**（对应正文实际引用的那几页）。
    """
    ranges = _PAGE_RANGE.findall(ref)
    if ranges:
        a, b = ranges[-1]
        return f"{a}--{b}"
    singles = _PAGE_SINGLE.findall(ref)
    if singles:
        return singles[-1]
    cn = _PAGE_CN.findall(ref)
    if cn:
        a, b = cn[-1]
        return f"{a}--{b}"
    return ""


# ── 文献条目 → bib 字段 ───────────────────────────────────────────────────

# 期刊文章：Author, "Title", Journal vol (year), pp. X-Y.
_ART_EN = re.compile(
    r"^(?P<author>.+?),?\s*[“\"](?P<title>[^”\"]+)[”\"]\s*,?\s*"
    r"(?:in\s+)?(?P<journal>[^,]+?)\s*(?:(?P<vol>\d+)\s*)?\((?P<year>(?:19|20)\d{2})\)"
    r"\s*,?\s*(?P<pages>pp?\.\s*[\d\-–—]+)?",
)
# 书：Author, Author2, Title, Place: Publisher, year.
# ⚠️ 作者可能有多个且用逗号分隔（`Nino B. Cocchiarella, Max A. Freund, Modal Logic...`），
#    单纯按第一个逗号切会把第二作者当成书名——实测就是这么错的。
#    这里先切出「连续的姓名段」，遇到明显像书名的成分才停。
_NAME_SEG = re.compile(r"^[A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*)*$")


def _split_authors(s: str) -> tuple[str, str]:
    """把 `作者1, 作者2, 书名, 出版地: 出版社, 年.` 切成 (作者串, 其余)。"""
    parts = [p.strip() for p in s.split(",")]
    authors = []
    for i, p in enumerate(parts):
        if not p:
            continue
        # 姓名段：全部由 2~4 个首字母大写的词组成，且不含冒号/句点结尾的句子特征
        words = p.split()
        # 关键判据：人名的每个词都首字母大写；书名里通常夹着小写虚词
        # （`Modal Logic for Philosophers` 的 for、`An Introduction to Its Syntax`
        # 的 to）。少了这一条，书名会被误判成第二作者。
        looks_name = (
            1 <= len(words) <= 4
            and all(w[:1].isupper() for w in words)
            and not p.endswith(".")
            and ":" not in p
        )
        if looks_name and len(p) <= 42 and i < 6:
            authors.append(p)
        else:
            return ", ".join(authors), ", ".join(parts[i:])
    return ", ".join(authors), ""


# 在「书名, 出版地: 出版社, [版次,] 年」里**搜索**出版信息段；
# 它前面的一切就是书名。用 search 而不是 match——书名在出版信息之前，
# 锚定开头会直接匹配失败（实测三条都掉进了 misc）。
_BOOK_EN = re.compile(
    r"(?P<place>[^:,]{2,40}?):\s*(?P<publisher>[^,]{2,60}?),\s*"
    r"(?:(?P<edition>\d+(?:st|nd|rd|th)\s+ed\.?,?\s*))?"
    r"(?P<year>(?:19|20)\d{2})",
)
# 中文期刊：作者. 年份. 题名[J]. 期刊, 年(期): 页.
_ART_CN = re.compile(
    r"^(?P<author>.+?)[.。]\s*(?P<year>(?:19|20)\d{2})[a-z]?[.。]\s*"
    r"(?P<title>.+?)\[(?P<kind>[MJC])\][.。]\s*(?P<rest>.+)$",
)


def _parse_entry_impl(ref: str) -> dict:
    """把一条文献解析成 bib 字段（旧实现；出口的 P1-2 类型订正见 `parse_entry`）。"""
    s = re.sub(r"\s+", " ", ref).strip().rstrip(".")

    m = _ART_CN.match(s)
    if m:
        d = {
            "author": m.group("author"),
            "title": m.group("title"),
            "year": m.group("year"),
        }
        kind = {"M": "book", "J": "article", "C": "incollection"}.get(m.group("kind"), "book")
        rest = m.group("rest")
        jm = re.match(r"\s*([^,，]+)[,，]\s*\d{4}\s*\((?P<n>[^)]*)\)\s*[:：]\s*(?P<p>[\d\-–—]+)", rest)
        if jm and kind == "article":
            d["journal"] = jm.group(1).strip()
            d["number"] = jm.group("n")
            d["pages"] = jm.group("p").replace("-", "--")
        else:
            d["note"] = rest
        d["_type"] = kind
        return d

    m = _ART_EN.match(s)
    if m and m.group("journal"):
        return {
            "_type": "article",
            "author": m.group("author").strip(),
            "title": m.group("title").strip(),
            "journal": m.group("journal").strip(),
            "year": m.group("year"),
            "pages": (m.group("pages") or "").replace("pp.", "").replace("p.", "").strip().replace("-", "--"),
        }

    # 先切作者，再在**剩余部分**上匹配书名与出版信息。
    # 若反过来（让 _BOOK_EN 的 .+? 自己找断点），多作者条目会把第二作者
    # 当成书名的一部分——实测 `Nino B. Cocchiarella, Max A. Freund, Modal Logic...`
    # 的 address 被填成了 `Max A. Freund, Modal Logic. An Introduction...`。
    authors, remainder = _split_authors(s)
    if authors and remainder:
        m = _BOOK_EN.search(remainder)
        if m:
            title = remainder[: m.start()].strip().rstrip(",").strip()
            return {
                "_type": "book",
                "author": authors,
                "title": title,
                "address": m.group("place").strip(),
                "publisher": m.group("publisher").strip(),
                "year": m.group("year"),
                "edition": (m.group("edition") or "").strip().rstrip(","),
            }

    # 解析不出来：保留原文，人工/AI 再订正
    return {"_type": "misc", "note": s}


def parse_entry(ref: str) -> dict:
    """把一条文献解析成 bib 字段；解析不出来就整条塞进 note，绝不丢内容。

    P1-2（2026-10-18）：出口统一走 `footref.refine_entry_fields()`——把「刊名格其实是
    出版地/出版社」的挪回 `address`/`publisher`，并按**出版信息特征**重判 `_type`
    （`Oxford: Clarendon` → @book、`in … (eds.), pp.` → @incollection 等）。
    判据只有一份：`slcn/footref.classify_entry_type()`（下一卡 docxsource 复用同一函数）。
    """
    from . import footref

    return footref.refine_entry_fields(_parse_entry_impl(ref), raw=ref)


# ── 生成键 ────────────────────────────────────────────────────────────────

_CN_SURNAME = re.compile(r"^([\u4e00-\u9fff])")


def make_key(entry: dict, seq: int, used: set[str]) -> str:
    """生成 bib 键。

    本刊两种惯例并存（`reference.bib` 83 篇 vs 与作者同名 60 篇），键的**具体形式
    并不重要**——因为工具同时产出 .tex 和 .bib，只要两边自洽即可。
    这里用「首作者姓 + 年份」，重复则追加 a/b/c。
    """
    author = entry.get("author", "")
    year = entry.get("year", "")
    stem = ""
    if author:
        # 先切出第一作者。注意 "Cocchiarella, Nino B." 是「姓, 名」，
        # 而 "Nino B. Cocchiarella" 是「名 姓」——搞反了就会得到 Nino2008 这种键。
        first = re.split(r"\s+and\s+|&", author.strip())[0].strip()
        m = _CN_SURNAME.match(first)
        if m:
            from .naming import surname_pinyin

            stem = surname_pinyin(m.group(1)).capitalize()
        elif "," in first:
            stem = re.sub(r"[^A-Za-z]", "", first.split(",", 1)[0])[:14]
        else:
            words = [w for w in re.split(r"\s+", first) if w]
            # 跳过缩写（Nino B. → 取最后一个完整词）
            words = [w for w in words if not re.fullmatch(r"[A-Za-z]\.?", w)] or words
            stem = re.sub(r"[^A-Za-z]", "", words[-1])[:14] if words else ""
    if not stem:
        stem = "ref"
    stem = stem.strip()
    base = f"{stem}{year}" if year else f"{stem}{seq:02d}"
    key = base
    n = 0
    while key in used:
        n += 1
        key = f"{base}{chr(ord('a') + n - 1)}" if n <= 26 else f"{base}{n}"
    used.add(key)
    return key


# ── bib 字段 → .bib 文本 ──────────────────────────────────────────────────

_TYPE_ORDER = {
    "book": ["author", "title", "edition", "publisher", "address", "year", "userf", "note"],
    "article": ["author", "title", "journal", "volume", "number", "year", "pages", "userf", "note"],
    "incollection": ["author", "title", "booktitle", "publisher", "address", "year",
                     "pages", "url", "userf", "note"],
    # B-217②（防御性补齐，编号清单→entry_to_bib 通道）：中文学位论文按引用库既有口径
    # （`bib_db` 实测 67+15 条）：`school=学校`、`address=学位类型原字串`（「博士论文」/
    # 「硕士学位论文」）。`thesis` 是往期成品里的另一种写法（institution/type）。
    "phdthesis": ["author", "title", "school", "address", "year", "userf", "note"],
    "mastersthesis": ["author", "title", "school", "address", "year", "userf", "note"],
    "thesis": ["author", "title", "type", "institution", "school", "address",
               "year", "userf", "note"],
    "misc": ["author", "title", "year", "url", "userf", "note"],
}


# ── 作者串 → BibTeX 的 `and` 分隔 ────────────────────────────────────────
#
# BibTeX 里**逗号是「姓, 名」的倒装符**：`author = {J. P. Dillard, C. Segrin and J. M. Harden}`
# 会被读成两个人，第一个人的姓变成 `C. Segrin J. P.`，编译出来是
# 「C. S. J. P. Dillard and J. M. Harden」（编辑实测 2026-10-14）。
# 源稿里这种「逗号 + and」混用很常见，必须规范化。
#
# ⚠️ 但**真正的倒装名不能动**：人工 bib 里倒装式占 13%（1573/12577），
#    如 `Folina, J.`、`Cocchiarella, Nino B.`、`van Eemeren, F. H.`。
_AUTHOR_INITIAL = re.compile(r"^[A-Za-z]\.?$")
_AUTHOR_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "phd", "dr", "prof"}


def _author_has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def _author_strip_note(s: str) -> str:
    """剥掉作者名后的方括号/圆括号注记（实测形如 `Li, Jinxia [李晋霞]`），只用于判断。"""
    return re.sub(r"\s*[\[（(][^\]）)]*[\]）)]\s*", " ", s).strip()


def _author_given_like(part: str) -> bool:
    """逗号右边这一截像不像「名」（倒装名的后半段）。

    要认得出这些形态（都是实测踩到的）：
      `J. P.`、`Nino B.`、`J.-Y.`（连字符缩写）、`B.~F.`（波浪号）、`III`（后缀）
    """
    # `~` 是不换行空格（手册要求写 `van~Fraassen`），分析时当空格看，输出仍保留
    toks = [t for t in _author_strip_note(part).replace("~", " ").split() if t]
    if not toks or len(toks) > 4:
        return False
    for t in toks:
        if t.lower().rstrip(".") in _AUTHOR_SUFFIX:
            continue
        if _AUTHOR_INITIAL.match(t):
            continue
        # 缩写可带连字符与句点：J.-Y. / H.-G.；或就是一个首字母大写的词：Nino
        if t[:1].isupper() and all(c.isalpha() or c in ".-" for c in t):
            continue
        return False
    return True


def _author_is_inverted(group: str) -> bool:
    """`Dillard, J. P.` / `Cocchiarella, Nino B.` / `van Eemeren, F. H.` → 倒装，保持原样。

    而 `J. P. Dillard, C. Segrin` **不是**倒装——逗号两边各是一位作者的全名，
    左边带缩写就是判据。
    """
    if "," not in group and "，" not in group:
        return False
    if _author_has_cjk(_author_strip_note(group)):
        return False  # 中文不用倒装式（注记里的汉字不算，如 `Li, Jinxia [李晋霞]`）
    left, right = re.split(r"[,，]", _author_strip_note(group), maxsplit=1)
    left, right = left.strip(), right.strip()
    if not left or not right:
        return False
    if any(_AUTHOR_INITIAL.match(t) for t in left.split()):
        return False
    return _author_given_like(right)


def _split_names(g: str) -> list[str]:
    """按 `,` `，` `、` 拆作者，但**不碰方括号/圆括号里的注记**。

    实测 `Sixu Li [韩笑、李思旭]`：括号里的顿号不是作者分隔符，
    直接 re.split 会把名字拆成 `Sixu Li [韩笑` 和 `李思旭]`。
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in g:
        if ch in "[（(":
            depth += 1
        elif ch in "]）)":
            depth = max(0, depth - 1)
        if ch in ",，、" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [x.strip() for x in parts if x.strip()]


def normalize_authors(a: str) -> str:
    r"""把作者（或编者）串规范成 BibTeX 的 `and` 分隔。

    · `A, B and C` → `A and B and C`
    · `A，B`（中文）→ `A and B`
    · 倒装名保持：`Dillard, J. P.`、`Cocchiarella, Nino B.`、`van Eemeren, F. H.`
    """
    s = re.sub(r"\s+", " ", (a or "").replace("\\&", " & ").replace("＆", " & ").strip())
    if not s:
        return ""
    groups = [g.strip() for g in re.split(r"\s+and\s+|\s*&\s*", s, flags=re.I) if g.strip()]
    out: list[str] = []
    for g in groups:
        if _author_is_inverted(g):
            out.append(g)
            continue
        # ⚠️ 带花括号/LaTeX 命令的**一律不拆**：实测 `Melli{\`{e}}s, Paul{-}Andr{\'{e}}`
        #    被拆成 `Melli{\`{e}}s and Paul{-}Andr{\'{e}}`，姓就丢了。
        #    宁可漏改（留着逗号），也不能改坏数据。
        if "{" in g or "\\" in g:
            out.append(g)
            continue
        # ⚠️ 中文著/译体例（2026-10-15，第 64 条）：`（著）`/`（译）`/`（点校）`/`（编）`
        #    是**姓名的一部分**，段里的中文逗号与顿号**不是**作者分隔符。
        #    实测最刺眼的一例：`阿斯海姆（著），张建军、万林（译）` 会被拆成
        #    `阿斯海姆（著） and 张建军 and 万林（译）`——著/译标记就此毁掉，
        #    而 `{沃斯、费布鲁赫、库伊（著），黄海婷（译）}` 正是规范里写明的写法
        #    （《排校手册》源码的实例是 `焦循（著），沈文倬（点校）`）。
        #    判据复用 `bibcheck.author_has_role_mark()`（与离线检查器同源）。
        from .bibcheck import author_has_role_mark

        if author_has_role_mark(g):
            out.append(g)
            continue
        out.extend(_split_names(g))
    return " and ".join(out) if out else s


def entry_to_bib(key: str, entry: dict, raw: str = "") -> str:
    """把解析出的字段写成 .bib 条目。"""
    etype = entry.get("_type", "misc")
    order = _TYPE_ORDER.get(etype, _TYPE_ORDER["misc"])
    # 中文条目补 userf={cn}（SLCN-MIX.sty 据此改用中文标点）
    if not entry.get("userf"):
        from .bibnorm import detect_userf

        uf = detect_userf(str(entry.get("author", "")), str(entry.get("title", "")))
        if uf:
            entry = dict(entry, userf=uf)
    lines = [f"@{etype}{{{key},"]
    width = max((len(k) for k in order if entry.get(k)), default=4)
    wrote = False
    for k in order:
        v = entry.get(k)
        if not v:
            continue
        if k == "author":
            # P1-3（2026-10-18）：author 是编辑部硬规则（英文一律倒装），出口统一到一处，
            # 与 bibnorm.normalize_author_field 同源；先拆 `逗号+and` 混写/`&`（同
            # docxsource._bib_author 的顺序），再倒装；中文/日文与花括号保护由 bibnorm 跳过。
            from .bibnorm import normalize_author_field

            v = normalize_author_field(normalize_authors(str(v)))
        elif k == "editor":
            # 本卡只改 author；editor 归 B3 批处理（此处保持旧行为：只规范 and 分隔）
            v = normalize_authors(str(v))
        lines.append(f"  {k.ljust(width)} = {{{_bib_escape(str(v))}}},")
        wrote = True
    if not wrote and raw:
        lines.append(f"  note      = {{{_bib_escape(raw)}}},")
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("}")
    return "\n".join(lines) + "\n"


def _bib_escape(s: str) -> str:
    """转义 bib 值里的 LaTeX 特殊字符，保留已有的转义。"""
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            out.append(s[i : i + 2])
            i += 2
            continue
        if ch in "&%$#_":
            out.append("\\" + ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)
