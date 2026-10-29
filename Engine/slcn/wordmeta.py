"""文档解析与元数据（B-207 从 docxsource 纯搬运）：标题识别、摘要/关键词/CLC、文末中英文元数据块、邮箱、单位、作者信息。"""

from __future__ import annotations

import re

from .wordcore import (SURNAME_PINYIN, _has_cjk, _strip_zero_width)
from .wordentry import (_NUMBERED_PREFIX_RE)

# ── 文档解析 ──────────────────────────────────────────────────────────────

_HEAD1 = re.compile(r"^[一二三四五六七八九十]+[、\.]\s*")
# 访谈体/无顿号稿的「一 标题」型（87-9 实测：`一 非形式逻辑` / `二 修辞论证理论`）。
# 与 `一、` 同族，但作者用空格分隔；只在**短行、无句末标点、后接汉字标题**时认，
# 避免把正文里的「一 个…」误判成标题（判据在 ⑥ 的 branch 里）。
_HEAD_CN_SPACE = re.compile(r"^([一二三四五六七八九十]{1,2})\s+(?P<t>[\u4e00-\u9fff][^。！？；]{1,38})$")
_HEAD2 = re.compile(r"^（[一二三四五六七八九十]+）\s*")

# 新式中文标题：`1 引言` / `2 论证行动生成问题研究现状`（阿拉伯数字 + 空格，**不带点**）。
#
# ⚠️ 同一篇稿子里「标题」和「列表项」长得很像，必须分开：
#       [5]  '1 引言'                        ← 标题
#       [19] '1. 尚未形成对论证行动完整生成过程的统一解释。三条径路…'  ← 列表项（带点 + 整句）
#   实测 09 Jiawei_Zhong：6 个节标题全是前者，旧规则只认「一、引言」那种老式编号，
#   于是标题原样留在正文里，产物里**一个 \section 都没有**（人工成品有 6 个）。
# ⚠️ 数字与标题之间**可以没有空格**：实测 2026_05_Meimei_Han 写的是
#    `1示例悖论的逻辑结构与严格性问题`（`4  结语` 那种带空格的也有），
#    旧规则要求 `\s+`，于是 6 个节标题只认出 1 个。
_HEAD_CN_ARABIC = re.compile(r"^\d{1,2}(?:\s+|(?=[\u4e00-\u9fff]))(?P<t>\S.*)$")
# `N. 标题`（阿拉伯数字 + **点**）—— 与 `N 标题` 同族，只是编号后多一个点。
# 实测 04 Lei_Li：一篇 6 个小节全写成 `1. 莱布尼茨` / `5. 维特根斯坦`（人名小节），
# 旧判据那一句 `rest[:1].isupper()` 是为英文 `1. Introduction` 写的，于是 6 个标题
# 全留在正文里，产物**一个 \section 都没有**（人工成品 Lei_Li.tex 里 6 个都是
# `\section` —— 所以它们是标题，不是正文里的名单）。
# ⚠️ 点后必须直接是**汉字**：英文 `1. Introduction` 仍走 _HEAD_EN1，行为逐字不变；
#    `N.M`（`2.2 标题`）也仍在 _HEAD_EN3 之前被挡住（点后是数字，这里不匹配）。
_HEAD_CN_DOT = re.compile(r"^\d{1,2}\.\s*(?P<t>[\u4e00-\u9fff]\S*)$")
# 数字后紧跟这些字的多半不是标题（`20世纪…`、`2022年第6期…`），排除掉
_HEAD_CN_BAD_TAIL = ("年", "月", "日", "世纪", "年代", "页", "卷", "期", "个", "种", "位", "条")
# B-217⑦（B-215 模板实测）：`、` **不再一律拒绝**——「3 定理、引理与证明」是
# 合格短标题（长度判据已在 `_is_cn_numbered_heading`：2–40 字）。句末标点
# （。！？；）、逗号/冒号/西文标点仍拒绝；长标题超 40 字仍拒绝。
# 默认路径：不含顿号的标题判定逐字节不变。
_CN_HEAD_PUNCT = "。；，：；！？,;:!?"


def _is_cn_numbered_heading(t: str) -> bool:
    """`N 标题` 里 N 后面那截像不像标题（而不是句子或列表项）。"""
    body = t.strip()
    if not (2 <= len(body) <= 40):
        return False
    if any(ch in body for ch in _CN_HEAD_PUNCT):
        return False
    if body[-1].isdigit():
        return False
    if body.startswith(_HEAD_CN_BAD_TAIL):
        return False
    # 至少要有一个汉字或字母，避免把「3 个」这类残句当标题
    return any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in body)


def _is_cn_dot_heading(t: str) -> bool:
    """`N. 中文标题` 的形态判据 —— **复用** `_is_cn_numbered_heading`，不另写一套。

    ⚠️ 这一层就是防「编号列表项被当成标题」的闸门。正文里与标题同形的列表项实测有：
        `1. 尚未形成对论证行动完整生成过程的统一解释。三条径路…`（09 Jiawei_Zhong）
        `1.命题（宗）：“此山有火。”`（08 Jing_Ren 的因明五支）
        `1. 第一页第二段，提出周延问题的文献建议列出原始文献，…`（04 Min_Zhang）
    它们全部被「2–40 字 + 无句读 + 不像 20 世纪」挡住——判据与 `N 标题` 那条路径
    是**同一套**，所以两种写法的误判边界一致，不会出现「同样一句话在 `1 引言` 里
    挡住了、在 `1. 引言` 里却放行」。
    """
    m = _HEAD_CN_DOT.match(t)
    if not m:
        return False
    rest = m.group("t")
    # 与 _HEAD_EN1 分支同一条：句中带句点的多半是文献条目/编号说明，不是标题
    return "." not in rest and _is_cn_numbered_heading(rest)

# 结构标记必须**中英双认**。实测 01 Xue_Wang 是英文稿，用的是
# `Abstract:` / `Keywords:`；只认中文标记会让正文区直接取空（相似度掉到 1%）。
#
# ⚠️ 段首允许有空白（`\s` 在 Python 3 里本来就含全角空格 U+3000，这里显式写出来只是
#    让判据一眼可读）：实测 2023年第5期(72) 03 Jie_Song 的摘要写成
#    `    摘要：决策论哲学中的…`（段首四个半角空格），旧规则要求标签紧贴段首，
#    于是中文摘要 fail。**只放宽段首空白**——`^` 仍锚定段首，句中出现的
#    「摘要/Abstract」一律不算标签（这是这条正则的语义，不是附加条件）。
_ABS_LEAD = r"^[\s\u3000]*"
# ⚠️ 「摘要」与正文之间**可以没有分隔符**（实测 2025年第6期(85) 04 Chen_Xu 的汉语
#    摘要写成 `摘要理想的人工通用智能系统…`，全库仅此 1 处），所以不能一律要求冒号；
#    但也不能无条件认：`摘要式阅读`/`摘要性…`/`摘要化…` 里的「摘要」只是**构词语素**，
#    不是标签（全库实测这三个后缀字在标签位置 0 命中，这条纯粹是反例防线；
#    万一认错，代价是 fail——不会把正文写进 `\myabstractCN`）。
_ABS_RE = re.compile(_ABS_LEAD + r"(?:摘\s*要(?![式性化])|Abstract)\s*[:：]?\s*", re.I)
# 关键词标签**不**跟着放宽段首空白：关键词有 7 篇 fail，不在本轮验收范围内，而
# `kw_blocks` 里「先出现的空正文块」会把后面有正文的块挡掉（`next()` 取第一个 cjk 块），
# 放宽它可能凭空造出 ok→fail。摘要这条路另写了「向下取正文」的判据（见 _abs_body_cn）。
_KW_RE = re.compile(r"^(?:关键词|Keywords?|Key\s*words?)\s*[:：]?\s*", re.I)
_REF_HEADS = {"参考文献", "references", "参考书目", "bibliography", "works cited"}

# ── 「摘要」标签独占一段：向下取正文（判据见 _abs_body_cn）─────────────────
#
# 实测 11 篇源稿把标签单独写一段（`摘 要 ` / `摘    要` / `摘  要` / `摘要` / `摘要：`），
# 正文在**下一段**。旧实现 `_label_body` 对中文标签**故意**不向下看（怕把中英两套摘要
# 里那套英文写进 `\myabstractCN`），于是这些稿子的 `\myabstractCN` 是空的
# （普查 abstract_cn fail 16 篇里的 11 篇）。
# 判据（宁 fail 不误判，绝不凭空造正文）：
#   ① 空段跳过，最多取 _ABS_LOOKAHEAD 段，且最多往后看 _ABS_LOOKAHEAD*2 段；
#   ② 只取**含汉字**的段落——语言判据与 _label_body / parse_tail_metadata ① 同一套。
#      实测 2026年第1期(86) 08 Min_Cai 的补充材料是 `摘要：` 后跟**英文**正文
#      （那一段在「英文标题及摘要」小标题之下，是英文块的标签），这条专门挡它抢中文槽；
#   ③ 撞上下一个标签（关键词/摘要/题名/作者/单位/邮箱…）就收手，绝不把关键词吞成摘要；
#   ④ 每段都要像正文（够长 + 有成句的标点）——节标题（`一、引言`）不是摘要正文。
_ABS_LOOKAHEAD = 3
_ABS_MIN_CHARS = 20
_ABS_END_CHARS = "。；！？，,.;!?"
# ③ 的「下一个元数据标签」清单（摘要/关键词那两类由 _ABS_RE/_KW_RE 判，不重复）
_META_LABEL_RE = re.compile(
    _ABS_LEAD + r"(?:标题|题目|题名|副标题|作者|单位|所属单位|邮箱|电子邮箱|通讯作者|"
    r"通信作者|作者简介|作者信息|中图分类号|文献(?:标识|标志)码|基金项目|收稿日期|"
    r"Title|Author|Affiliation|E-?mail)\s*(?:[:：]|$)", re.I)


# ── B-219③：正文里只由「中图分类号/文献标识码」组成的整段 → 丢弃 ─────────────
#
# 本刊标题页由 SLCN-MIX.sty 固定印刷 `中图分类号：B81` / `文献标识码：A`
# （SLCN-MIX.sty 标题页命令），正文里再来一行就是重复。判据（spec §2③）：
# **整段只由**这些元数据组成（标签 + 值，可两行合一/分两段），值限
# 字母数字与 `.-—/+`；段里只要还夹着正文句子就不动（GIGO 不猜）。
_CLC_META_RE = re.compile(
    r"^(?:(?:中图分类号|文献(?:标识|标志)码)\s*(?:[:：]\s*)?[A-Za-z0-9][A-Za-z0-9.\-—/+]*"
    r"\s*(?:[；;，,、]\s*)?)+$"
)


def _is_clc_meta_only(text: str) -> bool:
    """整段是否只由「中图分类号/文献标识码」元数据组成（B-219③）。"""
    t = (text or "").strip()
    return bool(t) and bool(_CLC_META_RE.fullmatch(t))

# 低于这个字符数就不可能是正文（例如剩下的只是一张缩写对照表）
MIN_BODY_CHARS = 1500

# 英文稿的章节编号：`1. Title` 是节，`1）Title` 是全角右括号的子节。
# 实测 02 Ning_Jiang：docx 用 `1. Modern Approaches…`，人工成品是
# `\section{Modern Approaches…}`（去掉编号）。识别不出来会让整篇 token 对齐错位——
# 一个缺失的 \section 能凭空造出 1400 多个假的「实质差异」。
_HEAD_EN1 = re.compile(r"^\d{1,2}\.\s+(?P<t>\S.*)$")
_HEAD_EN2 = re.compile(r"^\d{1,2}）\s*(?P<t>\S.*)$")
# 第 121 条（期 88 / 02 Ning_Jiang 真稿）：英文子标题写 `2) Problems with …`（半角右括号）。
_HEAD_EN2_HALF = re.compile(r"^\d{1,2}\)\s*(?P<t>[A-Z].*)$")
_HEAD_EN3 = re.compile(r"^\d{1,2}\.\d{1,2}\s+(?P<t>\S.*)$")  # 1.2 Title

# 元数据块只可能出现在开头这么多段之内
META_SCAN = 20

# ── 副标题 / 文末中英元数据块 ─────────────────────────────────────────────
#
# 实测 09 Jiawei_Zhong（2026年第5期）：原稿结构是
#     0 示例论证如何生成？          ← 题名
#     1 ——一个基于示例论证理论的生成模型 ← 副标题（另起一段！）
#     2 钟嘉伟                      ← 作者
#     …
#     96 参考文献 + 97–114 编号清单（`[N] 作者, 年, "题名", 刊名, 卷(期): 页`）
#     115–119 英文题名/副题/作者/摘要/关键词   ← 在文末！
#     120–125 邮箱 + 中英文所属单位
# 旧实现把「第 1 段」无条件当作者，于是副标题变成 `\myfirstauthor`；
# 元数据只扫前 20 段，于是英文题名/摘要永远是模板占位符 `Title`/`Abstract`；
# 参考文献只认人年制，于是那 18 条编号清单一条都没进来。
_SUB_RE = re.compile(r"^\s*(?:—{1,3}|–{1,2}|―{1,2}|-{1,2})\s*")
_EMAIL_RE = re.compile(r"(?:邮箱|E-?mail)\s*[:：]\s*([^\s，,；;]+@[^\s，,；;]+)", re.I)
# 裸邮箱（不带「邮箱：」标签）——实测 41 篇源稿的地址就这么写（README 第 45 条④）
_EMAIL_ANY_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_AFFIL_HEAD_RE = re.compile(r"(?:所属单位|单位|affiliation)", re.I)

# ── 文末英文元数据块的兜底定位（见 locate_tail_block）──────────────────────
#
# 实测 16 篇源稿把英文题名/摘要/关键词块写在**文末**，却没有「参考文献」标题
# （或标题排在英文块**之后**，01 Ting_Lu 就是这样）。旧实现要求 `ref_start >= 0`
# 才去回溯这个块，于是这些稿子的 `tail_start` 永远是 -1：英文题名 11 行、
# 英文摘要 15 行 fail，连写明的「电子邮箱：」都用不上（README 第 45 条④）。
#
# 兜底判据（宁 fail 不误判）：
#   ① 标签只在**文末 _TAIL_BLOCK_SCAN 段**里找。英文块贴着文末是它的定义；
#      正文中段的 `Abstract` 是双语稿的开头元数据，归开头那套管，不该被当文末块。
#   ② 标签必须**带冒号或独占一段**（`Abstract:` / `Abstract` / `Key words：…`），
#      所以文献条目里的 `Abstracts of…`、正文里的 `Abstract art in…` 命不中。
#   ③ 标签段本身不含汉字（英文块；中文那一套在开头，不走这里）。
_TAIL_BLOCK_SCAN = 30
_TAIL_LABEL_RE = re.compile(r"^(?:Abstract|Keywords?|Key\s*words?)\s*(?:[:：]|$)", re.I)

# ── 裸邮箱的行形态判据（见 collect_emails）────────────────────────────────
#
# 地址本体沿用 _EMAIL_ANY_RE，免得两处正则各自演化。实测地址的写法有六类：
#   ① 独占一段 `author011@sample.com`；② 一行两个地址（06 Jie Rong）；
#   ③ 作者信息/单位行内（03 Ming_He 的 `作者简介：…author005@sample.com`）；
#   ④ 作者名与地址同行（06 Hao_Chen `Hao Chen  author003@sample.edu.cn`）；
#   ⑤ `Name (addr):`（05 Jing_Niu）；⑥ 短行以地址结尾（04 Chen_Xu 的通信地址行）。
# 判据本身要求**完整地址**（`local@domain.tld`），所以正文里当符号用的 `@`
# 无论怎么贴都命不中；范围限制在 collect_emails 里。
_ADDR = _EMAIL_ANY_RE.pattern
# ① 整行就是一个地址（可带括号、可带尾随标点）
_ADDR_ALONE_RE = re.compile(
    r"^[\s\u3000]*[（(【\[<]?\s*(?P<e>" + _ADDR + r")\s*[）)】\]>]?[\s\u3000]*[。.，,；;]?$")
# ② 整行只有地址（空白/顿号/分号分隔多个）
_ADDRS_ONLY_RE = re.compile(
    r"^[\s\u3000]*(?:" + _ADDR + r"[\s\u3000]*[/、;；,，]?[\s\u3000]*)+$")
# ③「作者名 + 地址」同行且地址在行尾（前缀还要过 _looks_like_name_line）
_NAME_ADDR_RE = re.compile(
    r"^(?P<name>.{1,40}?)[\s\u3000]+(?P<e>" + _ADDR + r")[\s\u3000]*$")
# ④ MDPI 式 `Name (addr):`
_PAREN_ADDR_RE = re.compile(
    r"(?<=[A-Za-z\u4e00-\u9fff])\s*[（(]\s*(?P<e>" + _ADDR + r")\s*[）)]\s*[:：]")
# ⑤ 行内有这些词 = 作者信息/单位行，行内**第一个**地址就是作者邮箱
_AUTHOR_MARK_RE = re.compile(
    r"作者信息|作者简介|通讯作者|通信作者|Corresponding|所属单位|单位|"
    r"[Uu]niversity|College|Institute|Department|大学|学院|研究院|研究所")
# 地址后常紧跟中文句号（实测 `author007@sample.com。`、`author001@sample.com。`）：_EMAIL_RE
# 的字符类没排除它，值会带脏尾，所以取值一律先剥掉包裹括号与尾标点。
_TRAIL_PUNCT = "。．.，,；;：:）)】」》>"
# 模板占位符不是真邮箱（实测 02 Wei_Zhao 的 `xxx@xxx.xxx`）：宁可不填，
# 也不能把假地址印进 `\myfirstemail`。黑名单**故意比任务要求宽**（连带拒
# `@example.*` / `@test.*` / `@yourdomain`）——全库 0 命中，宁 fail 不误判。
_PLACEHOLDER_EMAIL_RE = re.compile(
    r"(?:xxx|yyy|zzz|abc|your|name|example|test)@|"
    r"@(?:xxx|yyy|zzz|example|test|yourdomain)\b", re.I)
# 文末块没被定位（tail_start < 0）时，只在文末这几段里找邮箱
_EMAIL_TAIL_FALLBACK = 6

# ── 单位（机构）识别 ──────────────────────────────────────────────────────
#
# 旧实现只认「文末元数据块里带 `单位：` 标签的行」，于是全库 132 份源稿只拿到 1 份
# （docx_audit 第 44 条：单位 1/132、fail 54）。实测体例有四类：
#   ① 题目/作者附近独立一行：`浙江大学哲学学院`、`（兰州大学 哲学社会学院，兰州 730000）`
#   ② 作者与单位同一行：`焦敏 中山大学哲学系`、`甘肃政法大学 卢婷`
#   ③ 文末中英元数据块（无标签）：`作者信息：周明 中山大学逻辑与认知研究所`
#   ④ 脚注里的作者信息：`作者信息：吴静怡  南京大学哲学学院`、`Meimei Han  College of …`
#
# 精度优先（宁可 fail 不误判）：行内出现句号/分号/书名号/网址/日期/编号一律不算；
# 中文行按标点分组、组内剔掉「人名」「城市+邮编」这类碎片；西文行只剥行首作者名。
_AFFIL_WORD_RE = re.compile(
    r"大学|学院|研究院|研究所|实验室|科学院|社会科学院|哲学系|"
    r"Department|University|College|Institute|School\s+of|Academy|Center|Centre",
    re.I,
)
# 机构名开头词（西文剥作者名时用来刹车：`School of …` 的 `School` 不能当人名剥掉）
_AFFIL_HEAD_WORDS = {
    "department", "school", "institute", "college", "university", "academy",
    "center", "centre", "faculty", "laboratory", "lab", "chair", "division",
    "graduate", "program", "programme", "research", "group",
}
# 带标签的作者信息行：把标签剥掉再取单位
_AFFIL_LABEL_RE = re.compile(
    r"^\s*(?:作者信息|作者简介|作者单位|单位信息|所属单位|通信作者|通讯作者|单位|"
    r"中文|英文|Affiliation|affiliation)\s*[:：]\s*"
)
# 这些标签行本身就是题名/摘要/关键词，不是单位（实测踩到：
# `英文题目：A Historical Review … Huazhong University of Science and Technology …`）
_AFFIL_NEVER_RE = re.compile(
    r"^\s*(?:(?:英文|中文|English|Chinese)\s*)?"
    r"(?:题目|标题|题名|摘要|关键词|Abstract|Keywords?|Key\s+words?)\s*[:：.]", re.I
)
# 正文口吻的词：机构行里不会出现（`研究所` 的「研究」不算，故用整词列表）
_AFFIL_PROSE_RE = re.compile(
    r"作者|学者|笔者|认为|指出|提出|本文|我们|因此|然而|但是|例如|如果|参见|所谓|"
    r"探讨|表明|显示|值得|应该|可能|一般|通常|往往|不仅|而且|因为|所以|"
    r"关于|对于|通过|根据|介绍|说明"
)
_AFFIL_CITY_ZIP_RE = re.compile(r"^[\u4e00-\u9fff]{2,8}[\s\d]*\d{6}$|^\d{6}$|^[\u4e00-\u9fff]{2,6}$")
_AFFIL_LIST_ITEM_RE = re.compile(r"^\s*(?:\[\s*\d{1,3}\s*\]|\d{1,3}\s*[.、）)])")
# 中文单位行必须**以机构名起头、以机构后缀收尾**（`华中科技大学逻辑教育二十年回顾与展望`
# 这样的标题行因此被挡掉：它起头像机构、结尾是「展望」）。
_AFFIL_UNIT_START_RE = re.compile(
    r"^[\u4e00-\u9fff·]{2,10}(?:大学|学院|研究院|研究所|实验室|科学院|社会科学院|哲学系)"
)
# 机构名的收尾后缀。**只此一份**，两个正则共用：
#   · `_AFFIL_UNIT_END_RE`（`$` 锚定）——「整行/整段必须以机构名收尾」，历史口径；
#   · `_AFFIL_UNIT_END_ANY_RE`（不锚定）——案例 75 的「句内取最长的机构名」，
#     要在 `武汉大学哲学系教授` 里把机构名的**结束位置**找出来。
_AFFIL_UNIT_TAIL = r"大学|学院|研究院|研究所|实验室|科学院|社会科学院|学系|系|中心"
_AFFIL_UNIT_END_RE = re.compile(r"(?:" + _AFFIL_UNIT_TAIL + r")$")
_AFFIL_UNIT_END_ANY_RE = re.compile(r"(?:" + _AFFIL_UNIT_TAIL + r")")
# 案例 75：机构名后面常紧跟**职衔**（`武汉大学哲学系教授`、`武汉大学哲学学院博士生`）。
# 职衔**剥离**（单位字段是给排版用的，人工成品里没有职衔），只认下列**学术职衔**：
#   一级/二级/三级/四级/特聘/客座/兼职/终身/名誉/助理/副 + 教授|研究员|讲师|助教
#   博士|硕士（生）（研究生）（导师）| 博导 | 硕导
# **行政职务故意不收**（主任/院长/所长/处长/秘书…）。理由：实测 05 Hai_Du 的作者简介
# 里有 `中国社会科学院智能与逻辑实验室主任`——那个实验室是作者**主持的平台**，
# 而他的既有单位（开头括号行）是「中国社会科学院大学哲学院 + 中国社会科学院哲学研究所」。
# 把「主任」也算职衔，就会凭空多出一个单位、把一个本来正确的值改坏。宁 fail 不误判。
_AFFIL_TITLE_TAIL_RE = re.compile(
    r"^(?:[（(][^）)]{0,16}[）)])?"                      # 可选的整体注记「（博士生导师）」
    r"(?:一级|二级|三级|四级|特聘|客座|兼职|终身|名誉|助理|副)?"
    r"(?:(?:博士|硕士)(?:研究生|生)?(?:导师)?"
    r"|(?:教授|研究员|讲师|助教)"
    r"|博导|硕导)?"
    r"[）)】\]]?$"                                       # 切句残留的右括号
)
# 行首是这些词 → 是正文/引述，不是单位行
_AFFIL_NEVER_START_RE = re.compile(
    r"^(?:参见|参阅|引自|转引自|关于|对于|本文|作者|学者|根据|通过|例如|如果|所谓|"
    r"因此|然而|但是|我们|他们|其中|主要|一般|通常|往往|不仅|而且|以及|同时|"
    r"此外|后来|此后|当时|目前|如今|现在|已经|曾经|由于|为了|按照|依据|上述|以下|如下|详见)"
)
# 西文剥作者名时的刹车词：`See University of Chicago Press for details.` 的 `See`
# 不能被当成人名剥掉，否则剩下的串起头就是机构词、会被误收。
_LATIN_STOPWORDS = {
    "see", "cf", "ibid", "in", "for", "the", "a", "an", "this", "that", "these",
    "those", "on", "of", "to", "and", "or", "but", "however", "also", "as", "at",
    "by", "with", "from", "about", "according", "press", "journal", "vol", "no",
    "pp", "ed", "eds",
}
# 西文单位行必须以机构词起头（剥掉行首作者名之后）
_AFFIL_LATIN_START_RE = re.compile(
    r"^(?:Department|School|Institute|College|University|Academy|Center|Centre|"
    r"Faculty|Laboratory|Lab|Research|Graduate|Division|Chair|Program)\b",
    re.I,
)
# 英文单位的多行连接（末尾那个空格是 PDF 里的 1.88pt，见 README 第 40 条②）
AFFIL_EN_JOIN = "\\\\　\\hspace{1em}\\phantom{\\myfirstauthorEN}\\hspace{1em} "


def _clean_affil_line(line: str) -> str:
    """从一行文字里抽出单位名；不像单位行则返回空串。

    两道：
      **stage ①** `_clean_affil_line_strict` —— 历史口径的**整行**判据，一字不改。
      **stage ②** `_clean_affil_by_sentence` —— 案例 75 新增：整行被否掉之后，
      按 `，。；、` 切句、在**句内**找机构名。

    ⚠️ 顺序是「先 stage ①，它没结果才 stage ②」，这是为了守住一条不变量：
      **stage ① 本来能认出来的行，结果逐字不变。**
    因此本缺陷**只可能**让「原来是空串」的行变成非空（收益），不可能把既有值改掉或清掉
    （风险）；全库逐篇对照见 `tests/test_affil_from_bio.py` 的 ③ 组。
    """
    # 去 BOM / 零宽字符：Word 第一段常带 `\ufeff`（实测 2024_02_Ming_Qu 的脚注）
    t = _strip_zero_width(line or "")
    t = re.sub(r"\s+", " ", t.strip())
    if not t or len(t) > 160:
        return ""
    got = _clean_affil_line_strict(t)
    if got:
        return got
    return _clean_affil_by_sentence(t)


def _clean_affil_line_strict(t: str) -> str:
    """stage ①：**整行**判据（历史口径，案例 75 一个字都没动）。

    实测量的正例：
      浙江大学哲学学院 / 北京大学外国哲学研究所
      （兰州大学 哲学社会学院，兰州 730000） / 广东第二师范学院马克思主义学院 广州 510303
      焦敏 中山大学哲学系 / 甘肃政法大学 卢婷 / 作者信息：周明 中山大学逻辑与认知研究所
      School of Philosophy and Social Development, Shandong University
      Meimei Han   College of Philosophy, Nankai University
    实测量的反例（必须返回空串）：
      山西大学，2024年8月12-13日（会议信息，含日期）
      相关地址信息：For information, address State University of New York Press…网址：www…
      英文题目：A Historical Review … Huazhong University of Science and Technology …
      1. 尚未形成对论证行动完整生成过程的统一解释。（列表项）
      20世纪逻辑学在中山大学得到发展（以数字起头的正文）
      华中科技大学逻辑教育二十年回顾与展望（题名本身含机构词）

    ⚠️ 它是**坏掉的三道闸门**的所在地（案例 75 的根因）：
      ① 行内有 `。` → 整行 `return ""`（第 18 行）；
      ② 「作者简介」句里的单字碎片（`男`）落进 `_clean_affil_cjk` 的 `else: return ""`；
      ③ 机构名以职衔收尾（`…哲学系教授`）不满足 `_AFFIL_UNIT_END_RE` 的 `$`。
    """
    if not t or len(t) > 160:
        return ""
    if _AFFIL_NEVER_RE.match(t):
        return ""
    if not _AFFIL_WORD_RE.search(t):
        return ""
    # 句号/书名号/网址/日期/编号行一律不算；行尾的分号也是残句
    if re.search(r"[。．]|《|》|网址|https?://|www\.", t) or t.endswith((";", "；")):
        return ""
    if re.search(r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日", t):
        return ""
    if _AFFIL_LIST_ITEM_RE.match(t) or t[0].isdigit():
        return ""
    # 标签先剥（`作者信息：周明 中山大学…` 的「作者」不是正文口吻），再判行首词
    t = _AFFIL_LABEL_RE.sub("", t)
    if _AFFIL_NEVER_START_RE.match(t):
        return ""
    # 方括号/圆括号里的角色注记（`[通讯作者]`）不是单位
    t = re.sub(r"[\[（(]\s*(?:通讯作者|通信作者|责任编辑|corresponding author)\s*[\]）)]",
               "", t, flags=re.I).strip()
    if len(t) >= 2 and (t[0], t[-1]) in (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]")):
        t = t[1:-1].strip()
    if not t:
        return ""
    if _AFFIL_PROSE_RE.search(t):
        return ""
    return _clean_affil_cjk(t) if _has_cjk(t) else _clean_affil_latin(t)


def _clean_affil_by_sentence(t: str) -> str:
    r"""stage ②（案例 75）：整行否掉后，按句切分 + 在**句内**取机构名。

    缺陷（案例 75，2026_05_Lei_Li 的 `word/footnotes.xml`）
    ------------------------------------------------------
    源稿脚注里明明写着单位：

        作者简介: 李雷，男，湖南常德人，武汉大学哲学系教授。电子邮箱：li-lei@sample.edu.cn

    产物却写 `【未识别：第一作者单位，请手工填写】`。根因是 stage ① 的**整行连坐**，
    三道各自独立致命（已实测）：
        `_clean_affil_line_strict('作者简介: 李雷，男，湖南常德人，武汉大学哲学系教授。电子邮箱：…')` → ''
        `_clean_affil_line_strict('作者简介: 李雷，武汉大学哲学系')` → '武汉大学哲学系'  ← 去掉「男」就能解
        `_clean_affil_line_strict('作者简介: 李雷，男，武汉大学哲学系')` → ''           ← 留着「男」就整行丢
    即：**是「能解但判据太严」，不是「没解析出来」。**

    判据（按 `，,。．；;、` 切句，逐句独立判定；句内任何一条不过就丢**这一句**，不连坐）
    ------------------------------------------------------------------------------
      ⓪ 整行级闸门（题目/摘要/关键词标签、书名号/网址、日期、编号、正文口吻、超长）
         仍需先过。这里**故意重抄**而不抽公共函数：stage ① 是冻结的历史口径，
         抽函数会让「已能识别的行逐字不变」这条不变量难以验证。
         ⚠️ 只有 `。` 从整行级**降到句级**（那正是案例 75 的致命闸门①）；
         `《》` 与网址**必须留在整行级**——实测把它们也降级会放进正文段落与
         文献条目：`人文学院、公管学院、…等学院将《逻辑学》…纳入培养计划`、
         `桑木严翼（1874-1946），哲学家，东京帝国大学名誉教授。著有《哲学概论》…`
         （前者是正文里的单位罗列，后者是文献里被研究的学者，都不是作者单位）。
      ① 句内必须有机构词（`_AFFIL_WORD_RE`）：电话/邮编/籍贯/人名句因此天然不可达；
      ② 句内不许有正文口吻词（`本文/参见/认为…`）、书名号、网址、日期、邮箱；
      ③ 句首必须是 `_AFFIL_UNIT_START_RE` 形态，取**以机构后缀收尾的最长前缀**为单位名
         （`武汉大学哲学系教授` → `武汉大学哲学系`）；
      ④ 单位名后面**只允许**跟 `_AFFIL_TITLE_TAIL_RE` 的学术职衔（可剥离；行政职务不收，
         理由见该正则处的注释）。后面跟着别的东西就丢这一句——这是「宁 fail 不误判」的闸门：
         `…哲学系author005@sample.com`（邮箱）、`…实验室主任`（行政职务）、`…大学任教`（残句）。
    多句命中就按出现顺序用 `\x00` 连接（与 `_clean_affil_cjk` 的多单位口径一致，
    由 `_affil_lines_to_units` 再拆开、剥作者名、按中/英分桶）。
    """
    if not t or len(t) > 160:
        return ""
    if _AFFIL_NEVER_RE.match(t):
        return ""
    if not _AFFIL_WORD_RE.search(t):
        return ""
    if re.search(r"《|》|网址|https?://|www\.", t):
        return ""
    if t.endswith((";", "；")):
        return ""
    if re.search(r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日", t):
        return ""
    if _AFFIL_LIST_ITEM_RE.match(t) or t[0].isdigit():
        return ""
    t = _AFFIL_LABEL_RE.sub("", t)
    if _AFFIL_NEVER_START_RE.match(t):
        return ""
    t = re.sub(r"[\[（(]\s*(?:通讯作者|通信作者|责任编辑|corresponding author)\s*[\]）)]",
               "", t, flags=re.I).strip()
    if len(t) >= 2 and (t[0], t[-1]) in (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]")):
        t = t[1:-1].strip()
    if not t:
        return ""

    out: list[str] = []
    for seg in re.split(r"[，,。．；;、]", t):
        s = seg.strip().lstrip("（(【[ \u3000").strip()
        if not s or not _AFFIL_WORD_RE.search(s):
            continue
        if _AFFIL_PROSE_RE.search(s):
            continue
        if re.search(r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日", s):
            continue
        if _EMAIL_ANY_RE.search(s):          # `作者简介：…author005@sample.com` 不许当单位
            continue
        if not _AFFIL_UNIT_START_RE.match(s):
            continue
        end = 0
        for m in _AFFIL_UNIT_END_ANY_RE.finditer(s):
            if m.end() > end:
                end = m.end()                # 以机构后缀收尾的**最长**前缀
        if not end:
            continue
        # 机构名后面紧跟的圆括号：内容**不是**职衔时算机构名的一部分——
        # `中山大学哲学系（珠海）助理教授` → `中山大学哲学系（珠海）`
        # （人工成品 2024_05_Chen_Xiao 写的就是带（珠海）的）。
        # 内容是职衔时（`武汉大学哲学系（教授）`）留给下一步的职衔剥离。
        m = re.match(r"[（(]([^）)]{1,16})[）)]", s[end:])
        if m and not _AFFIL_TITLE_TAIL_RE.match(m.group(1)):
            end += m.end()
        unit, tail = s[:end].strip(" 　·"), s[end:].strip()
        if not unit or (tail and not _AFFIL_TITLE_TAIL_RE.match(tail)):
            continue
        if unit not in out:
            out.append(unit)
    return "\x00".join(out)



def _clean_affil_cjk(t: str) -> str:
    """中文行：按「，；、」分组（= 多个单位），组内剔掉人名与城市邮编碎片。"""
    groups: list[str] = []
    for g in re.split(r"[，,；;、]", t):
        frags: list[str] = []
        for f in re.split(r"[ \t\u3000]+", g.strip()):
            f = f.strip(" 　·")
            if not f:
                continue
            if _AFFIL_WORD_RE.search(f):
                if not _AFFIL_UNIT_START_RE.match(f):
                    return ""               # 机构词在中间（`…在中山大学…`）→ 不是单位行
                frags.append(f)
            elif _AFFIL_CITY_ZIP_RE.match(f):
                continue                    # 人名 / 城市 / 邮编 / 纯数字
            else:
                return ""                   # 有说不清的碎片 → 整行放弃（宁 fail 不误判）
        if frags:
            one = " ".join(frags)
            if not _AFFIL_UNIT_END_RE.search(one):
                return ""                   # 结尾不是机构后缀（`…回顾与展望`）→ 不是单位行
            groups.append(one)
    if not groups:
        return ""
    # 组内碎片是同一个单位的组成部分（`兰州大学 哲学社会学院`），用一个空格连接；
    # 组与组之间才是两个单位，交给调用方用 `\\ ` 分行。
    return "\x00".join(groups)


def _clean_affil_latin(t: str) -> str:
    """西文行：剥掉行首的作者名（`Meimei Han  College of …`），其余原样保留。"""
    t = re.sub(r"^The\s+(?=[A-Z])", "", t)      # `The University of Hong Kong`
    words = t.split()
    n = 0
    # 最多剥 2 个词（`Given Family Inst…`）。剥更多会把校名开头的词吃掉——
    # 实测 2025_05_Wenzhi_Xia 的 `Sonam Qinghai Minzu University, Research Center…`
    # 会被剥成 `University, Research Center…`（错的），所以宁可整行放弃。
    while n < 2 and n < len(words) - 1:
        w = words[n].strip(",.&;:。")
        if w.lower() in _LATIN_STOPWORDS:
            break
        if not re.fullmatch(r"(?:[A-Z][A-Za-z'’\-]*\.?|[A-Z]\.)", w):
            break
        if w.lower() in _AFFIL_HEAD_WORDS or _AFFIL_WORD_RE.search(w):
            break
        n += 1
    # 只剥**成对**的「名 姓」（≥2 个词）：单剥一个词会把 `Tibet University, Research Center…`
    # 剥成 `University, Research Center…`（实测 2025_05_Wenzhi_Xia），宁可整行放弃。
    if n >= 2:
        rest = " ".join(words[n:])
        if rest and _AFFIL_WORD_RE.search(rest):
            t = rest
    if not _AFFIL_LATIN_START_RE.match(t):
        return ""
    if t.endswith(".") and not re.search(r"\b[A-Z]\.$", t):
        t = t[:-1].strip()              # 脚注里的单位行常以句号收尾，不是缩写点
    return t


def _affil_same_unit(one: str, bucket: list[str]) -> bool:
    """同一单位的不同**空白写法**算同一个单位。

    案例 75 的连带修正：实测 2025_04_Min_Zhang 的开头括号行写
    `（河北大学 哲学与社会学学院，河北 保定 071002）`（Word 两个 run，读出来带空格），
    文末作者简介写 `河北大学哲学与社会学学院`（不带空格）。单纯放宽判据会让新解出来的
    第二条并进列表，`\\myfirstaffiliation` 就印出**两行同一个单位**——把「改前非空」的值改坏。

    ⚠️ 只在**同一次调用内**按空白归一（`_affil_lines_to_units` 的桶）；跨调用（`collect_affiliations`
    的 `extend`）仍然是精确匹配——那是有意为之：`2024_02_Ming_Qu` 开头行与脚注行本就
    重复印着同一个单位，那是**历史行为**，本轮不许顺手改掉（改了就是一个「改前非空后变了」）。
    """
    key = re.sub(r"\s+", "", one)
    return any(re.sub(r"\s+", "", x) == key for x in bucket)


def _affil_lines_to_units(lines: list[str], names: list[str] | None = None) -> tuple[list[str], list[str]]:
    """把若干行整理成 (中文单位列表, 英文单位列表)，去重且保持出现顺序。

    去重按**空白归一**后的形态（见 `_affil_same_unit`）。
    """
    names = names or []
    cn: list[str] = []
    en: list[str] = []
    for ln in lines:
        got = _clean_affil_line(ln)
        if not got:
            continue
        for one in got.split("\x00"):
            one = _strip_author_names(one.strip(), names)
            if not one:
                continue
            bucket = cn if _has_cjk(one) else en
            if not _affil_same_unit(one, bucket):
                bucket.append(one)
    return cn, en


def _strip_author_names(one: str, names: list[str]) -> str:
    """剥掉**粘在单位上的作者名**。

    实测 2026_05_Lili_Zhao 原稿写的是 `赵丽丽西安交通大学马克思主义学院`
    （Word 里是两个 run，纯文本读出来没有分隔符），不剥会得到带人名的单位。
    只用**已知作者名**去剥（doc.author），所以不会误伤 `南开大学哲学院` 这种。
    """
    for nm in names:
        if not (2 <= len(nm) <= 4) or not one.startswith(nm):
            continue
        if _AFFIL_WORD_RE.search(nm):
            continue          # 名字片段里带「大学/学院」→ 是校名的一部分，不能剥
        if len(nm) == 2 and nm[0] not in SURNAME_PINYIN:
            continue          # 两字前缀必须是常见姓氏，避免把「南开」「中国」当人名
        rest = one[len(nm):].strip()
        if not rest:
            continue
        if _has_cjk(one) and _AFFIL_UNIT_START_RE.match(rest):
            return rest
    return one


def _affil_block_hits(lines: list[str], min_run: int = 2) -> list[str]:
    """从「连续若干行都是单位行/人名行/邮箱行」的块里取单位。

    实测两类来路：① 英文摘要之后的作者信息块
    （2022_06A_Zhaoxing_Xu：`Xu Department of … / 邮箱 / Ouyang Department of … / 邮箱`）；
    ② 脚注里的作者信息（`Meimei Han  College of … / 邮箱 / …`）。
    正文脚注里顺口提到某个大学不会形成这样的连续块，因此不会被误收。
    """
    out: list[str] = []
    run_hits: list[str] = []
    run_len = 0

    def flush() -> None:
        nonlocal run_hits, run_len
        if run_len >= min_run:
            out.extend(run_hits)
        run_hits, run_len = [], 0

    for ln in lines:
        ln = ln.strip()          # Word 里邮箱段常以全角空格收尾/起头（实测 Zhaoxing_Xu）
        got = _clean_affil_line(ln)
        if got or _looks_like_name_line(ln) or _EMAIL_ANY_RE.fullmatch(ln.rstrip(".")):
            run_len += 1
            if got:
                run_hits.append(got)
        else:
            flush()
    flush()
    return out


def _affil_head_zone(plains: list[str]) -> range:
    """开头元数据区：题名之后 → 第一处摘要/关键词标签（没有标签时取前 7 段）。"""
    first = next((i for i in range(1, min(len(plains), META_SCAN))
                  if _ABS_RE.match(plains[i]) or _KW_RE.match(plains[i])), -1)
    end = first if first >= 0 else min(len(plains), 7)
    return range(1, max(end, 1))


def _looks_like_name_line(t: str) -> bool:
    """整行就是一个（或几个）人名——脚注「无标签但整条都是作者信息」判据用。"""
    t = t.strip()
    if not t or len(t) > 40:
        return False
    if re.fullmatch(r"[\u4e00-\u9fff·]{2,4}(?:[ 、,，]{1,2}[\u4e00-\u9fff·]{2,4})*", t):
        return True
    toks = [x for x in re.split(r"[ \u3000]+", t) if x]
    return bool(toks) and all(
        re.fullmatch(r"[A-Z][A-Za-z'’\-]*\.?|[A-Z]\.", x) for x in toks
    )


def collect_affiliations(
    plains: list[str], tail_start: int, fn_texts: list[str], names: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """收集单位：开头元数据区 + 摘要后的作者信息块 + 文末元数据块 + 脚注（README 第 45 条）。"""
    hz = _affil_head_zone(plains)
    lines = [plains[i] for i in hz]
    if tail_start >= 0:
        lines.extend(plains[tail_start:])
    cn, en = _affil_lines_to_units(lines, names)

    # 摘要之后的作者信息块（连续 ≥2 行都是单位/人名/邮箱才收）
    after = [plains[i] for i in range(hz.stop, min(len(plains), hz.stop + 16)) if plains[i].strip()]
    more_cn, more_en = _affil_lines_to_units(_affil_block_hits(after), names)
    cn.extend(x for x in more_cn if x not in cn)
    en.extend(x for x in more_en if x not in en)

    for text in fn_texts:
        if not text.strip():
            continue
        fn_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not fn_lines:
            continue
        # 带标签（作者信息：/作者简介：/单位：）的一行就够；没有标签的要连续 ≥2 行。
        labeled = any(_AFFIL_LABEL_RE.match(ln) for ln in fn_lines)
        hits = _affil_block_hits(fn_lines, min_run=1 if labeled else 2)
        if not hits:
            continue
        more_cn, more_en = _affil_lines_to_units(hits, names)
        cn.extend(x for x in more_cn if x not in cn)
        en.extend(x for x in more_en if x not in en)
    return cn, en


def _norm_email(tok: str) -> str:
    """剥掉地址外包裹的括号与尾随标点（实测 `author007@sample.com。`、`<taho@…>`）。"""
    return tok.strip().strip("<>（）()【】[]").rstrip(_TRAIL_PUNCT)


def _emails_in_text(text: str) -> list[str]:
    """按字符位置（= 文档顺序）收出一段/一条脚注里的地址，判据见上面的行形态。"""
    found: list[tuple[int, str]] = []
    for m in _EMAIL_RE.finditer(text):        # 带标签：邮箱：/E-mail:
        found.append((m.start(1), _norm_email(m.group(1))))
    base = 0
    for raw in text.splitlines():
        start = text.index(raw, base)         # splitlines 也会在 \x0b 等处断行，故重新定位
        base = start + len(raw)
        line = raw.strip()
        if "@" not in line:
            continue
        off = start + len(raw) - len(raw.lstrip())
        m = _ADDR_ALONE_RE.match(line)
        if m:
            found.append((off + m.start("e"), _norm_email(m.group("e"))))
            continue
        if _ADDRS_ONLY_RE.match(line):
            found.extend((off + t.start(), _norm_email(t.group(0)))
                         for t in _EMAIL_ANY_RE.finditer(line))
            continue
        if _AUTHOR_MARK_RE.search(line):
            t = _EMAIL_ANY_RE.search(line)
            if t:
                found.append((off + t.start(), _norm_email(t.group(0))))
                continue
        m = _NAME_ADDR_RE.match(line)
        if m and _looks_like_name_line(m.group("name")):
            found.append((off + m.start("e"), _norm_email(m.group("e"))))
            continue
        m = _PAREN_ADDR_RE.search(line)
        if m:
            found.append((off + m.start("e"), _norm_email(m.group("e"))))
            continue
        # ⑥ 短行以地址结尾（实测 04 Chen_Xu：`Shanghai 200433, P. R. China yjxu@…`）。
        # 地址前出现句末标点说明这是正文句子，不收。
        if len(line) <= 80:
            m = re.search(r"(?P<e>" + _ADDR + r")[\s\u3000]*[。.]?$", line)
            if m and not re.search(r"[。；！？]", line[: m.start("e")]):
                found.append((off + m.start("e"), _norm_email(m.group("e"))))
    return [tok for _, tok in sorted(found, key=lambda x: x[0])]


def collect_emails(plains: list[str], tail_start: int, fn_texts: list[str]) -> list[str]:
    """收集作者邮箱：开头元数据区 + 文末元数据块 + 脚注（README 第 45 条④）。

    旧路径（parse_tail_metadata 的 `邮箱：` 标签）只在「文末块被定位」时可用，而实测
    41 篇 fail 源稿里 25 篇的地址只在脚注、12 篇文末块根本没被定位（`tail_start = -1`，
    其中 4 篇连标签都白写了）、11 篇在开头元数据区。

    ⚠️ 只扫这三段是**结构性防护**，不要为了多救几行放宽：
      · 参考文献区（ref_start..tail_start）与正文中段不在任何一段里——01 Meimei_Han
        正文 9 处「现实世界 @」这种当符号用的 @ 因此不可达；
      · 表格（w:tbl）在 parse_docx 里就被滤掉、从不进 plains——04 Ming_Xianyu 表格
        单元格里的审稿人邮箱因此不可达；
      · 伪脚注（`w:type` = `separator` / `continuationSeparator`）由 read_footnotes 丢掉；
        而**真实的 `w:id="0"` 脚注会读到**（本库 23 篇是这种，正文确实引用它）——
        所以这里的输入口径与单位解析（`collect_affiliations`）完全一致。
    返回按文档顺序去重；第一个即第一作者（模板字段就是「第一作者邮箱」）。
    """
    lines = [plains[i] for i in range(1, min(len(plains), META_SCAN))]
    if tail_start >= 0:
        lines.extend(plains[i] for i in range(max(tail_start, META_SCAN), len(plains)))
    else:
        lines.extend(plains[max(META_SCAN, len(plains) - _EMAIL_TAIL_FALLBACK):])

    out: list[str] = []
    for text in lines + list(fn_texts):
        if not text.strip():
            continue
        for tok in _emails_in_text(text):
            if _PLACEHOLDER_EMAIL_RE.search(tok) or tok in out:
                continue      # 占位符宁可留空让人工填，也不能印出 xxx@xxx.xxx
            out.append(tok)
    return out


# ── 作者信息脚注 → 结构化元数据（批71 B-118）──────────────────────────────
#
# 解析与格式判据在 `slcn/footnotes.py::parse_author_info`（单点维护）；这里只负责
# 「已知作者名」的归一与落位。已知名单是**安全阀**：脚注里的连写「姓名单位」
# （`张三某某大学…`）、无标签英文块（`Zhang San  College of …`）只有在
# 姓名命中已知名单时才敢切分——不在名单里就整条不解析（不猜）。
def _known_cn_authors(author: str) -> list[str]:
    """中文作者串 → 姓名列表（`陈  松` 这类单字片段并回一个名字）。"""
    parts = [x for x in re.split(r"[\s、,，·]+", author or "") if x]
    out: list[str] = []
    i = 0
    while i < len(parts):
        cur = parts[i]
        if len(cur) == 1 and i + 1 < len(parts) and re.fullmatch(
                r"[\u4e00-\u9fff]", parts[i + 1]):
            out.append(cur + parts[i + 1])
            i += 2
            continue
        if len(cur) >= 2:
            out.append(cur)
        i += 1
    return out


def _known_en_authors(author_en: str) -> list[str]:
    """英文作者串 → 姓名列表（只认「每段 2 个 Title-case 词」的连写，拿不准不拆）。"""
    words = re.split(r"\s+", (author_en or "").strip())
    if len(words) < 2 or len(words) % 2:
        return []
    if not all(re.fullmatch(r"[A-Z][^\W\d_]*\.?", w) for w in words):
        return []
    return [" ".join(words[i:i + 2]) for i in range(0, len(words), 2)]


def _is_subtitle(t: str) -> bool:
    return bool(_SUB_RE.match(t))


def _title_with_break(title: str, subtitle: str) -> str:
    """题名 + 副标题写成 LaTeX 字段值（人工成品用 `\\\\` 断行）。"""
    if not subtitle:
        return title
    return f"{title}\\\\{subtitle}"


def _looks_like_person(t: str) -> bool:
    """英文作者行：短、无句末标点、无逗号（`Jiawei Zhong`）。"""
    return (
        0 < len(t) <= 40
        and not re.search(r"[.?!。？！:：]", t)
        and "," not in t
        and not _has_cjk(t)
    )


# ── 英文题名 / 作者行的形态校验（B-268 S1）────────────────────────────────
#
# 背景：旧实现把「摘要前最后一条非中文短行」无条件当英文题名、把「紧邻摘要前的
# 那一行」无条件当作者。源稿在这些位置放的可能是**邮箱行**（74/02、87/09）、
# **纯单位行**（66/02、87/09）、甚至**模板页眉**（82/02），于是 `\mytitleEN` 印出
# `alex.sample@example.org`、`\myfirstauthorEN` 永远是占位符。
#
# 判据收成下面这组单点函数，wordbody 的 head（③/③.4）与 parse_tail_metadata 的
# tail（②）共用同一套：**拿不准就返回空**，交给模板占位符让编辑手工填——
# 给作者印错名字/单位是学术事故，宁可 fail 不可误判（禁止猜名）。
_TEMPLATE_HEADER_RE = re.compile(r"^\s*Studies\s+in\s+Logic\b[^\n]*\bVol\b", re.I)
_PII_RE = re.compile(r"^\s*PII\s*[:：]", re.I)
_EMAIL_LINE_RE = re.compile(
    r"^\s*[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+\s*$")
# 单位行的**强形态**：段首机构词，或行内有 `Department of / School of …`，
# 或以机构名收尾（`… University` / `… University, City`）。
_AFFIL_START_RE = re.compile(
    r"^\s*(?:School|Department|Dept\.?|Institute|College|Faculty|"
    r"Centre|Center|Academy|Laboratory|Research\s+(?:Center|Centre|Institute))\b", re.I)
_AFFIL_PHRASE_RE = re.compile(
    r"\b(?:Department|School|Institute|Faculty|College|Centre|Center)\s+of\b", re.I)
_ORG_TAIL_RE = re.compile(
    r"(?:University|Universit[ée]|Institute|College|Academy)\s*(?:,.*)?$", re.I)
_ORG_HEAD_RE = re.compile(
    r"^\s*(?:[A-Z][^\s,]{1,20}(?:\s+[A-Z][^\s,]{1,20}){0,4})\s+"
    r"(?:University|Institute|College|Academy)\b", re.I)
# 作者行里的姓名形态：Title-case（允许全大写姓 `SAMPLE`、连字符 `Mei-lin`、缩写句点）
_PERSON_NAME_TOKEN_RE = re.compile(r"[A-Z][A-Za-z'’\-]*\.?$")
_NAME_PARTICLES = frozenset({"van", "von", "de", "der", "den", "del", "della",
                             "di", "da", "du", "la", "le", "bin", "ibn"})
# 作者行里不可能是姓名成分的词（头衔/机构/占位符）——宁可判不出，也不把
# `Distinguished Professor`、`First Author` 拼进 `\myfirstauthorEN`。
# 模板自带的示例行（CN 模板自转换要逐字保留，S1 校验不得把它们判成非法）
_TPL_SAMPLE_TITLE = "english title"
_TPL_SAMPLE_AUTHOR_RE = re.compile(r"^(?:First|Second|Third|Fourth|Fifth)\s+Author\b", re.I)
_NOT_NAME_WORDS = frozenset({
    "professor", "prof", "dr", "department", "dept", "university", "institute",
    "college", "school", "faculty", "academy", "philosophy", "research",
    "center", "centre", "chair", "distinguished", "emeritus", "lecturer",
    "dean", "author", "authors", "title",
})
# 作者行上的上标标记（`Alex Sample 1, Mei Lin 2\uf02a`；\uf02a 是私用区星号）
_SUPERSCRIPT_RE = re.compile(
    r"[\uf020-\uf0ff\u00b9\u00b2\u00b3\u2070-\u207f\u2020\u2021\u00a7\u00b6*∗†‡]")


def _is_email_line(t: str) -> bool:
    """整段就是一个邮箱地址（74/02、87/09 文末块的形态）。"""
    return bool(_EMAIL_LINE_RE.match(t or ""))


def _looks_like_template_header(t: str) -> bool:
    """模板页眉（`Studies in Logic, Vol. x, No. x (xxxx): 1–10 PII: …`，实测 82/02）。"""
    s = (t or "").strip()
    return bool(_TEMPLATE_HEADER_RE.match(s) or _PII_RE.match(s))


def _looks_like_affiliation(t: str) -> bool:
    """英文单位行（纯机构名）；不是题名、不是姓名。"""
    s = (t or "").strip()
    if not s or _has_cjk(s) or len(s) > 160 or "@" in s:
        return False
    if _AFFIL_START_RE.match(s) or _AFFIL_PHRASE_RE.search(s):
        return True
    return len(s.split()) <= 12 and bool(_ORG_TAIL_RE.search(s) or _ORG_HEAD_RE.match(s))


def _looks_like_person_name(t: str) -> bool:
    """单个姓名段：2–6 个 Title-case 词、无句读/括号、无机构/头衔词。"""
    s = (t or "").strip()
    if not s or len(s) > 60 or _has_cjk(s) or "@" in s:
        return False
    if _TPL_SAMPLE_AUTHOR_RE.match(s):
        return True
    if any(c in s for c in ".?!。？！:：;；()（）[]{}/\\|<>"):
        return False
    toks = s.split()
    if not 2 <= len(toks) <= 6:
        return False
    for w in toks:
        core = w.rstrip(".")
        if core.lower() in _NAME_PARTICLES:
            continue
        if core.lower() in _NOT_NAME_WORDS:
            return False
        if not _PERSON_NAME_TOKEN_RE.match(w):
            return False
    return True


def _strip_author_markers(t: str) -> str:
    """去掉作者行上的上标星号/编号（`Alex Sample 1, Mei Lin 2` → `Alex Sample, Mei Lin`）。"""
    s = _SUPERSCRIPT_RE.sub(" ", t or "")
    s = re.sub(r"\s*\b\d{1,2}\b\s*", " ", s)
    return re.sub(r"\s+", " ", s).strip(" ,，;；")


def _author_row_name(t: str) -> str:
    """英文作者行里的姓名部分；判不出返回 ""（不猜）。

    覆盖三种实测形态：
      · 单段姓名 → 原样（`Alex Sample`、`Mei Lin   Jie Chen`；
        连写的多作者串交给 build.py 的 `_split_en_names_by_count` 按作者数等分）；
      · 多段且**每段都是姓名** → 用**空格**连接（85/05 `Alex Sample 1, Mei Lin 2`）：
        这与仓内既有的连写作者串形态一致（72/04、83/05），build 的
        `_split_en_names_by_count` 按「2×作者数」等分即可切出第二作者英文槽；
        不用分号是因为它会原样进 `\myfirstauthorEN`，且仓内无此先例；
      · 姓名 + 单位/头衔写在同一行 → 只取第一段（79/01、88/02）。
    """
    s = (t or "").strip()
    if not s or len(s) > 120 or _has_cjk(s) or "@" in s:
        return ""
    if _looks_like_template_header(s):
        return ""
    s = _strip_author_markers(s)
    if not s:
        return ""
    parts = [p.strip() for p in re.split(r"[,，;；]", s) if p.strip()]
    if not parts or not _looks_like_person_name(parts[0]):
        return ""
    if len(parts) == 1:
        # 单段：整行必须是姓名形态，单位行（含 Institution/University…）不算
        return "" if _looks_like_affiliation(s) else parts[0]
    if all(_looks_like_person_name(p) for p in parts[1:]):
        return " ".join(parts)
    return parts[0]


def _is_title_candidate(t: str) -> bool:
    """英文题名候选的校验（不得是邮箱/单位/模板页眉/占位符/标签行）。"""
    s = (t or "").strip()
    if not s or len(s) > 300 or "@" in s:
        return False
    if _looks_like_template_header(s) or _looks_like_affiliation(s):
        return False
    if _ABS_RE.match(s) or _KW_RE.match(s) or _META_LABEL_RE.match(s):
        return False
    if not re.search(r"[A-Za-z]", s):
        return False
    low = s.lower()
    if low == _TPL_SAMPLE_TITLE:
        return True
    return not low.startswith(("received ", "first author", "second author", "title"))


def _author_followed_by_block(lines: list[str], j: int) -> bool:
    """作者行之后紧跟单位/邮箱/英文摘要/中文块才认（挡住「题名被拆成两段」的误判）。"""
    seen = 0
    while j < len(lines) and seen < 2:
        t = lines[j].strip()
        j += 1
        if not t:
            continue
        seen += 1
        if (_ABS_RE.match(t) or _KW_RE.match(t) or _META_LABEL_RE.match(t)
                or _looks_like_affiliation(t) or _is_email_line(t) or _has_cjk(t)):
            return True
        return False
    return False


def head_author_after(lines: list[str], title_idx: int) -> str:
    """英文题名之后紧邻的作者行（head 英文块）；判不出返回 ""。

    只在「题名下一段」的窗口里找（最多跳过 2 个空段）；遇到摘要/关键词标签、
    单位行、邮箱行之外的非姓名行就收手——不往下扫正文，避免把节标题当人名。
    """
    if title_idx < 0 or title_idx >= len(lines):
        return ""
    j = title_idx + 1
    seen = 0
    while j < len(lines) and seen < 3:
        t = lines[j].strip()
        j += 1
        if not t:
            continue
        seen += 1
        if _ABS_RE.match(t) or _KW_RE.match(t) or _META_LABEL_RE.match(t):
            return ""
        name = _author_row_name(t)
        if name:
            return name if _author_followed_by_block(lines, j) else ""
        if _looks_like_affiliation(t) or _is_email_line(t):
            continue
        return ""
    return ""


def head_title_index(lines: list[str]) -> int:
    """题名段落下标：跳过开头空段与模板页眉；全是页眉/空段时返回 0（旧行为）。"""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and _looks_like_template_header(lines[i]):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
    return i if i < len(lines) else 0


def _label_body(lines: list[str], i: int, label_re) -> str:
    """元数据标签的正文：同一段冒号之后的内容；标签**独占一段**时取下一段。

    ⚠️ 实测英文元数据常把标签单独写一段（2024_07_Min_Jiao 的 `Abstract`、
    2025_06_Chen_Xu 的 `Abstract` 与 `Keywords:`）。旧实现只取 `label_re.sub`
    的结果，正文就丢了，`\\myabstractEN` 退回模板占位符 `Abstract`。
    判据与 parse_tail_metadata ① 同一套：下一段要**同语言**（英文标签 → 不含汉字）
    且不是另一个标签。

    ⚠️ 中文标签（`摘要`/`关键词`）**不**向下补：后面跟英文段的是「中英两套摘要」的
    另一套（实测 2026_01_Min_Cai 的补充材料），补进来就把英文写进 `\\myabstractCN`
    了——宁 fail 不误判。
    段首的 BOM（U+FEFF，实测 2024_02_Ming_Qu 的英文摘要段）不是正文，去掉。
    """
    body = label_re.sub("", lines[i])
    if body.strip() or i + 1 >= len(lines) or _has_cjk(lines[i]):
        return body.lstrip("\ufeff")
    nxt = lines[i + 1].strip()
    if nxt and not _has_cjk(nxt) and not _ABS_RE.match(nxt) and not _KW_RE.match(nxt):
        return nxt.lstrip("\ufeff")
    return ""


def _looks_like_abs_prose(t: str) -> bool:
    """像摘要正文（而不是节标题/列表项/标签行）：够长，且有成句的标点。"""
    return len(t) >= _ABS_MIN_CHARS and any(c in t for c in _ABS_END_CHARS)


def _abs_body_cn(lines: list[str], i: int) -> str:
    """摘要标签的正文（中文槽专用）；标签**独占一段**时向下取（判据见 _ABS_LOOKAHEAD 处）。

    ⚠️ 无汉字的标签段（英文 `Abstract`）原样交给 `_label_body`——它的「向下看一段、
    要同语言」判据本来就在那里，这里一个字都不改（**逐字节**同旧行为）。

    ⚠️ 标签与正文同段时**逐字节保持改动前的取值**（就是 `_label_body` 对中文标签的
    原样输出：`_ABS_RE.sub` 之后不做 strip，尾随空白也不动）——普查里那 68 篇
    `abstract_cn=ok` 全是这条路径的既成事实，动一个字节都可能翻状态。
    只有**新放宽**的段首缩进路径才追加「正文必须含汉字」：实测 10 Ming Xu 的
    `摘要： The logical education…`（无缩进、正文是英文）现在就算中文摘要，
    缩进版不能因为放宽形态而把英文正文写进 `\\myabstractCN`（宁 fail 不误判）。
    """
    raw = lines[i]
    if not _ABS_RE.match(raw):
        return ""                      # 不是标签段（调用方都会先 match，这里只是安全网）
    if not _has_cjk(raw):
        return _label_body(lines, i, _ABS_RE)
    body = _ABS_RE.sub("", raw).lstrip("\ufeff")
    if body.strip():
        if not raw[:1].isspace() or _has_cjk(body):
            return body
        return ""
    out: list[str] = []
    j = i + 1
    # 空段跳过（不占额度），但最多往后看 _ABS_LOOKAHEAD*2 段，免得一路扫进正文
    while j < len(lines) and len(out) < _ABS_LOOKAHEAD and j - i <= _ABS_LOOKAHEAD * 2:
        t = lines[j].strip()
        j += 1
        if not t:
            continue
        if _ABS_RE.match(t) or _KW_RE.match(t) or _META_LABEL_RE.match(t):
            break                      # ③ 撞上下一个标签就收手（关键词绝不吞）
        if not (_has_cjk(t) and _looks_like_abs_prose(t)):
            break                      # ②④ 不同语言 / 不像正文 → 宁 fail 不误判
        out.append(t)
    return " ".join(out)


def _backtrack_block_start(lines: list[str], abs_idx: int, stop: int) -> int:
    """从英文摘要往上回溯到块首（题名）：连续的非空、非中文、≤160 字的行。

    `stop` 是下界，回溯到它为止（不含）：原有路径是「参考文献」标题段，
    兜底路径是文档头（-1）。遇到汉字、空段或超长行也停——那是正文/文献区。
    """
    j = abs_idx - 1
    while j > stop and lines[j].strip() and not _has_cjk(lines[j]) and len(lines[j]) <= 160:
        j -= 1
    return j + 1


def _skip_en_refs_to_title(lines: list[str], start: int, abs_idx: int) -> int:
    """把「穿过英文参考文献清单」的块首回推到英文题名（批76 B-120）。

    背景：`_backtrack_block_start` 的判据是「英文、非中文、≤160 字」，而文末**英文
    参考文献条目**同样满足该形态；于是从英文 Abstract 上溯会穿过整段英文清单，
    清单尾部被当成「文末元数据块」切走 → 编号清单解析残缺（83/01 3/9、83/02 2/35、
    75/05 14/18、83/06 46/47；全库 213 篇解析稿只此 4 篇，见批76 报告）。

    编号行 `[N]`/`（N）`/`N.` 是参考文献清单的**硬证据**：候选块里出现编号行，
    块首就回推到英文题名——从英文 Abstract 往上：跳过括号包的单位行，再去掉
    作者行 / 副题行（与 `parse_tail_metadata` ② 同源判据），下限是最后一条编号行
    之后。没有编号行（正常形态）→ 原样返回，**默认行为零变化**。
    """
    if start < 0 or abs_idx <= start:
        return start
    marks = [i for i in range(start, abs_idx)
             if _NUMBERED_PREFIX_RE.match(lines[i].strip())]
    if not marks:
        return start
    j = abs_idx - 1
    while j > start and lines[j].strip().startswith(("(", "（")):
        j -= 1
    if j <= start:
        return start
    # B-268 S1：剥掉摘要正上方的邮箱行/单位行（74/02、87/09 的形态），
    # 再按「作者 → 副题」回推题名；没有这些行时逐字节同旧行为。
    if j > start and _is_email_line(lines[j].strip()):
        j -= 1
    if j > start and _looks_like_affiliation(lines[j].strip()):
        j -= 1
    if j > start and _author_row_name(lines[j].strip()):
        j -= 1
    if j > start and _is_subtitle(lines[j].strip()):
        j -= 1
    return max(j, marks[-1] + 1)


def locate_tail_block(plains: list[str], ref_start: int) -> int:
    """文末中英元数据块的起点段落下标；找不到返回 -1。

    ① 原有路径（先用，行为不变）：「参考文献」标题之后的第一处英文 Abstract，
       再向上回溯，让题名也落进这一块；
    ② 兜底路径：文末 _TAIL_BLOCK_SCAN 段内的英文 Abstract/Keywords 标签——
       「参考文献」标题不存在、或英文块排在它之前的稿子走这条（判据见常量处）。
    """
    tail_start = -1
    if ref_start >= 0:
        tail_start = next(
            (i for i in range(ref_start + 1, len(plains))
             if _ABS_RE.match(plains[i]) and not _has_cjk(plains[i])), -1)
        if tail_start >= 0:
            _abs_idx = tail_start
            tail_start = _backtrack_block_start(plains, tail_start, ref_start)
            # 批76 B-120：回溯可能「穿过」文末英文参考文献清单，把清单尾部切出参考
            # 文献区（见 `_skip_en_refs_to_title`）；无编号行时原样返回、行为不变。
            tail_start = _skip_en_refs_to_title(plains, tail_start, _abs_idx)
    if tail_start >= 0:
        return tail_start
    lo = max(0, len(plains) - _TAIL_BLOCK_SCAN)
    hit = next((i for i in range(lo, len(plains))
                if _TAIL_LABEL_RE.match(plains[i]) and not _has_cjk(plains[i])), -1)
    if hit < 0:
        return -1
    return _backtrack_block_start(plains, hit, -1)


def _tail_abstract_cn(lines: list[str], ref_start: int) -> str:
    """**参考文献区之后**的中文摘要；没有则返回 ""（正文全英文、中文摘要另附文末的稿子）。

    实测 7 篇：2023年第6期(73) 06 Lei_Zhang ×2、2025年第6期(85) 06 Ning_Gan、
    2026年第3期(88) 06 Hao_Chen ×3、2024年第6期(79) 01 Xue_Wang——它们的
    `摘    要` / `摘  要` / `摘要：…` 都不在开头 META_SCAN 段之内，而文末那条路径
    （parse_tail_metadata）**只看非中文标签**（见该函数 ①），于是中文摘要一直 fail。

    这是同一个标签形态判据的**第二处位置**，不是新判据：段首标签 + 正文必须含汉字
    （所以文末的英文块不会被误当中文摘要，实测 Min_Cai 那种 `摘要：`+英文 也挡住）。
    只在开头那套**空手而归**时才跑（调用处判断），只补空槽、绝不覆盖已有取值。
    """
    lo = ref_start + 1 if ref_start >= 0 else max(0, len(lines) - _TAIL_BLOCK_SCAN)
    for i in range(lo, len(lines)):
        if not _ABS_RE.match(lines[i]):
            continue
        body = _abs_body_cn(lines, i)
        if body and _has_cjk(body):
            return body
    return ""


def parse_tail_metadata(lines: list[str], start: int) -> dict:
    """解析文末的中英元数据块（英文题名/摘要/关键词、作者、单位、邮箱）。

    只认「参考文献区之后」的那一块——中文摘要/关键词在开头，不要重扫。
    """
    out: dict[str, str] = {}
    if start < 0:
        return out
    seg = [(i, t.strip()) for i, t in enumerate(lines) if i >= start and t.strip()]

    # ① 英文摘要 / 英文关键词（内容判据同开头那套：只看有没有汉字）
    # ⚠️ 标签可能**独占一段**（实测 2026_05_Meimei_Han：`[111]` 只有 `Abstract`，
    #    正文在下一段），这时取下一段的正文，别把模板占位符 `Abstract` 留在产物里。
    for pos, (_i, t) in enumerate(seg):
        if _ABS_RE.match(t) and not _has_cjk(t) and not out.get("abstract_en"):
            body = _ABS_RE.sub("", t).strip()
            if not body and pos + 1 < len(seg):
                nxt = seg[pos + 1][1]
                if not _has_cjk(nxt) and not _ABS_RE.match(nxt) and not _KW_RE.match(nxt):
                    body = nxt
            out["abstract_en"] = body.lstrip("\ufeff")
        elif _KW_RE.match(t) and not _has_cjk(t) and not out.get("keywords_en"):
            body = _KW_RE.sub("", t).strip()
            if not body and pos + 1 < len(seg):
                nxt = seg[pos + 1][1]
                if not _has_cjk(nxt) and not _ABS_RE.match(nxt) and not _KW_RE.match(nxt):
                    body = nxt
            out["keywords_en"] = body.lstrip("\ufeff")

    # ② 英文题名 + 副题 + 作者：从英文摘要往上回溯连续的非中文短行
    abs_idx = next((i for i, t in seg if _ABS_RE.match(t) and not _has_cjk(t)), None)
    if abs_idx is not None:
        back: list[tuple[int, str]] = []
        for i, t in seg:
            if i >= abs_idx:
                break
            if _has_cjk(t) or len(t) > 160:
                back.clear()
                continue
            back.append((i, t))
        # ⚠️ 作者行下面常紧跟一行**括号包起来的英文单位**（实测 05 Hai_Du：
        #    `Du Guo-ping` / `(Institute of Philosophy, …)`）。它既不像人名也不是题名；
        #    旧逻辑只看「像不像人名」，于是把单位印成了 `\mytitleEN`。
        # B-268 S1：除括号单位行外，**裸邮箱行**（74/02、87/09）与**纯单位行**
        #    （66/02、87/09）也挡在摘要正上方，必须先剥掉再判作者/题名；否则邮箱或
        #    机构名会被印成 `\mytitleEN`。剥完仍要过形态校验（_author_row_name /
        #    _is_title_candidate），判不出就留空交给占位符——不猜。
        while back and back[-1][1].lstrip().startswith(("(", "（")):
            back = back[:-1]
        while back and _is_email_line(back[-1][1]):
            back = back[:-1]
        while back and _looks_like_affiliation(back[-1][1]):
            back = back[:-1]
        # 紧邻摘要的那一行若像人名，就是英文作者（作者行只可能在题名之上还有内容时
        # 出现；只剩一行时那行就是题名，不能当作者吃掉）。
        # ⚠️ 旧判据 `_looks_like_person` 命中时**原样保留**（它认的连写作者串/多空格
        #    是既成产物的一部分，逐字节不能动）；只在旧判据失败时才走新形态恢复。
        if len(back) >= 2:
            _line = back[-1][1]
            _name = _line if (_looks_like_person(_line)
                              and _looks_like_person_name(_line)) else _author_row_name(_line)
            if _name:
                out["author_en"] = _name
                back = back[:-1]
        if back:
            # 最后一行是题名，其后若以破折号开头则为副题
            title = back[-1][1]
            sub = ""
            if len(back) >= 2 and _is_subtitle(title):
                sub = title
                title = back[-2][1]
            if _is_title_candidate(title):
                out["title_en"] = title
                out["subtitle_en"] = sub

    # ③ 邮箱 + 中英文所属单位
    for _i, t in seg:
        m = _EMAIL_RE.search(t)
        if m and not out.get("email"):
            out["email"] = m.group(1)
    aff_idx = next((i for i, t in seg if _AFFIL_HEAD_RE.search(t) and len(t) <= 30), None)
    if aff_idx is not None:
        cn, en = [], []
        for i, t in seg:
            if i <= aff_idx or len(t) > 120:
                continue
            if _EMAIL_RE.search(t):
                continue
            # ⚠️ 单位标签后面常紧跟同一份「作者简介」的**其它**标签行（实测 05 Hai_Du：
            #    `联系电话：…` / `通信地址：…` / `邮政编码：…`）。旧逻辑把它们全当单位，
            #    `\myaffiliation` 会印成电话号码和家庭住址。带标签的行只认「单位/机构/
            #    affiliation」这一种；没有标签的行（单位名本身）照收。
            lab = re.match(r"^([\u4e00-\u9fffA-Za-z]{2,8})\s*[:：]", t)
            if lab and "单位" not in lab.group(1) and "affiliation" not in lab.group(1).lower():
                continue
            if _has_cjk(t):
                cn.append(t)
            elif t:
                en.append(t)
        # 中文单位：人工成品用 `\\ ` 换行（`中山大学逻辑与认知研究所\\ 中山大学哲学系`）
        out["affiliation_cn"] = "\\\\ ".join(cn)
        # ⚠️ 英文单位：不能只用 `\\`。人工成品与模板的写法是
        #   `\\　\hspace{1em}\phantom{\myfirstauthorEN}\hspace{1em} ` ← **末尾有个空格**
        # 即第二行用「全角空格 + 作者名宽度的 phantom + 空格 + 一个普通空格」缩进。
        # 末尾那个空格不能省：它正好是 PDF 里的 1.88pt（实测 09 Jiawei_Zhong：
        # 人工第二机构 x=38.64，漏了空格就是 x=36.76，首字母 D 看着偏左一点点）。
        out["affiliation_en"] = AFFIL_EN_JOIN.join(en)
    return out

