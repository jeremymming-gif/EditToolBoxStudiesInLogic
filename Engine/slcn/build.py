"""装配器：把「旧模板稿的元数据 + 新稿的正文 + 新版模板骨架」合成一份初排稿。"""

from __future__ import annotations

from .naming import normalize_english_author
import os
import re
import shutil
from dataclasses import dataclass, field

from . import paths as P
from . import preamble as PRE
from . import rules as R
from . import templates as T
from .latex import (
    collect_custom_macros,
    copy_referenced_graphics,
    count_active_inputs,
    expand_inputs,
    find_all_macros,
    insert_body,
    parse_macros,
    set_field,
)
from .sources import extract_bibkeys_from_body, extract_body_text

HYPERREF_ANCHOR = r"\usepackage[pdfborder=0,CJKbookmarks=true]{hyperref}"

# 字段值的清理规则（来自对人工成品的实测）
# (适用字段或 None 表示全部, 正则, 替换, 说明)
FIELD_CLEANUPS: list[tuple[tuple[str, ...] | None, str, str, str]] = [
    (None, r"-{3,}", "——", "中文题名里的一串短横应为破折号"),
    (None, r"^\s*\\small\s*", "", "去掉字段开头的 \\small"),
    (("mygrants", "mythanks"), r"（项目编号[：:]\s*", "（", "基金号去掉『项目编号：』"),
    (("mygrants", "mythanks"), r"）\s*$", "）。", "基金号末尾补句号"),
]


def clean_field_value(name: str, value: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    v = value.strip()
    for scopes, pat, repl, why in FIELD_CLEANUPS:
        if scopes is not None and name not in scopes:
            continue
        new = re.sub(pat, repl, v)
        if new != v:
            notes.append(f"{name}: {why}")
            v = new
    return v, notes


# ── 解析失败必须显式：占位符 + 警告 ──────────────────────────────────────
# 模板里这些字段的默认值是**示例文字**（`第一作者` / `单位1（到系所）` /
# `xxxx@xxxx.xxx` / `Abstract` / `Title` / `在这里填上你的标题`…）。解析不出来时
# 若跳过不写，产物里就留着这些示例字——**看起来像正常出稿，实则是静默失败**：
# 实测 2026_05 Jingru_Kou（作者没给单位与邮箱）产物印着「单位1（到系所）」
# 「xxxx@xxxx.xxx」，编辑不逐字核对根本发现不了。
# 所以约定：这些字段**没能从源稿解析出来** → 写显眼的「【未识别：…，请手工填写】」
# 占位符，并记一条 warning（convert.py 会汇总打印）。编辑可用 --title-cn/--author-cn/…
# 或 `overrides=` 直接覆盖；**被覆盖的字段不再算未识别**。
#
# 「没解析出来」的判定有三条（见 is_parsed）：
#   ① 值为空；② 值为 null；③ **值与模板自带的示例文字逐字相同**——
#   作者常常直接拿模板改，字段没填就还是「第一作者」「单位1（到系所）」
#   「xxxx@xxxx.xxx」「Abstract」，这类值搬进产物跟没填一样。
#
# ⚠️ 出版信息（myvolnumber/myissnumber/mypubyear/myfirstpage/mylastpage/mypages/
# receiveddate/myeditor/mygrants）**不在此列**：那些是编辑侧字段，来源是
# --vol/--issue/--pages/--received/--editor/--grants，见 editorial_fields()。
# 把它们混进来只会天天误报。
REQUIRED_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    # 中文稿：CN 模板以中文为主，另有英文题名/摘要
    "zh": (
        ("mytitle", "中文题名"),
        ("myrunningtitle", "页眉标题"),
        ("myfirstauthor", "第一作者"),
        ("myfirstaffiliation", "第一作者单位"),
        ("myfirstemail", "第一作者邮箱"),
        # 第一作者的**英文**槽位（2026-10-15，案例 72）：CN 模板 1.5 节把它们预置成
        # 示例文字 `First Author` / `Affiliation 1`，从前不在这张表里 → 解析不出来时
        # 既不写占位符也不报「未识别」，模板示例文字被**静默**搬进产物印到 PDF 上
        # （实测 2026_05 Bo_Chen 第 20 页英文摘要页）；比写占位符更危险。
        # 对照：第二作者的英文槽位在 `coauthor_fields()` 里，所以第二作者是对的。
        ("myfirstauthorEN", "第一作者英文姓名"),
        ("myfirstaffiliationEN", "第一作者英文单位"),
        ("myabstract", "中文摘要"),
        ("mykeywords", "中文关键词"),
        ("mytitleEN", "英文题名"),
        ("myabstractEN", "英文摘要"),
    ),
    # 英文稿：EN 模板以英文为主，另有中文题名/摘要（且没有关键词字段）
    "en": (
        ("mytitle", "英文题名"),
        ("myrunningtitle", "页眉标题"),
        ("myfirstauthor", "第一作者"),
        ("myfirstaffiliation", "第一作者单位"),
        ("myfirstemail", "第一作者邮箱"),
        ("myabstract", "英文摘要"),
        ("mytitleCN", "中文题名"),
        ("myabstractCN", "中文摘要"),
    ),
}

PLACEHOLDER_TMPL = "【未识别：%s，请手工填写】"

OVERRIDE_KEYS = (
    "title_cn", "author_cn", "affiliation_cn", "email",
    "abstract_cn", "title_en", "abstract_en",
)


def missing_placeholder(name: str, label: str) -> str:
    """未识别字段写进产物里的占位符（中文 + 全角方括号，xelatex 可正常编译）。

    作者名按模板惯例包一层 `{\\kaishu …}`——`\\myfirstauthor` 在标题页正是由 sty
    用 `\\kaishu` 排版的，包上可保证字体可用、占位符一定看得见。
    """
    text = PLACEHOLDER_TMPL % label
    return "{\\kaishu %s}" % text if name == "myfirstauthor" else text


def is_parsed(value: str | None, name: str = "", defaults: dict | None = None) -> bool:
    """字段值是否算「真解析出来了」。

    空串与模板惯用的 null 都算没解析出来；**与模板自带的示例文字逐字相同**也算
    没解析出来——作者常常直接拿模板改，字段没填就还是「第一作者」「单位1（到系所）」
    「xxxx@xxxx.xxx」「Abstract」「标题」。这类值搬进产物，跟没填一样，甚至更坏
    （实测 LaTeX 通道 04 Hao_Wu 是 `First Author`/`Department 1, University 1`，
    05 Baoxun_Tu 是 `第一作者`/`单位1（到系所）`/`xxxx@xxxx.xxx`）。

    比较用模板**自己**的原值（见 template_defaults），不写死字符串——换模板代次
    （CN/SLCN-MIX4/SLEN）也不必改代码。`\\kaishu` 前缀不参与比较。
    """
    if not value or value.strip() in ("", "null"):
        return False
    if name and defaults:
        d = defaults.get(name)
        if d is not None and _norm_for_compare(value) == _norm_for_compare(d):
            return False
    return True


def _norm_for_compare(value: str) -> str:
    """比较字段值用：折叠空白、去掉作者字段的 `\\kaishu` 前缀。"""
    return " ".join(value.replace("\\kaishu", " ").split())


def template_defaults(template_text: str) -> dict[str, str]:
    """模板自带的字段原值（示例文字），用于判断「这字段到底有没有人填过」。"""
    return {name: m.value for name, m in parse_macros(template_text).items()}


# 姓名分隔符：空白（含全角空格）、顿号/逗号（中英）、以及期刊里常见的 `/`。
# ⚠️ 逗号只在**中文**姓名串里当分隔符（本函数一开始就把含英文字母的整条挡掉），
#    所以不会误伤 `Kou, Jingru` 这种倒装英文名——那是 `en_coauthor_values()` 的事。
_NAME_SEP = re.compile(r"[\s、,，/／]+")
_CJK_CHAR = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _is_single_han(s: str) -> bool:
    """是否「恰好一个汉字」。单个汉字**不可能是**一个完整的中文姓名。"""
    return len(s) == 1 and bool(_CJK_CHAR.match(s))


def cn_author_names(author: str) -> list[str]:
    """纯中文姓名串 → 姓名列表；含英文字母时返回空列表。

    为什么含英文字母就整条不拆：`Jiawei Chu` 按空白拆成 `Jiawei` / `Chu` 就错了
    （实测的教训，见 `author_fields()`）。`author_fields()` 与英文合著者槽位
    （`en_coauthor_values()` 要拿中文作者数对表）共用这一份判据。

    拆完之后还要**合并「单字片段」**（2026-10-15，案例 71）：
    期刊排两字姓名时姓名内部会带空白（`陈  波`、`屈 凯`），按空白硬拆就成了
    `['陈','波']`——`author_fields()` 把「波」写成第二作者，`coauthor_fields()`
    再凭空产出 4 个占位符（第二作者中文/英文单位、邮箱、英文姓名）印进 PDF 页眉。
    判据：**单个汉字长度的片段不可能是完整中文姓名**，所以「单字片段 + 下一个片段」
    两两合并（`陈`+`波`=`陈波`；`顺`+`真`=`顺真`）。这个判据**只可能**在片段本身
    连一个字都不够时触发，因此不会把 `王刚 李晟` 两个真姓名并成一个
    （两个片段都 ≥2 字，不满足触发条件）——这正是 `convert.py:306-313` 那条
    「折叠空白后不足 5 字算一个名字」的判据所缺的性质（它会把 `王刚 李晟` 当成一个人）。

    故意**不**做「单字片段并入前一个片段」：那样 `王刚 李` 会把已拆对的首个姓名
    吃掉；末尾落单的单字片段原样保留（与改动前一致，不会更坏）。
    """
    author = (author or "").strip()
    if not author or re.search(r"[A-Za-z]", author):
        return []
    parts = [n for n in _NAME_SEP.split(author) if n]
    names: list[str] = []
    i = 0
    while i < len(parts):
        cur = parts[i]
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        # 单字片段（且下一个片段里有汉字）→ 合并成一个名字，跳过两个片段。
        if _is_single_han(cur) and nxt is not None and _CJK_CHAR.search(nxt):
            names.append(cur + nxt)
            i += 2
            continue
        names.append(cur)
        i += 1
    return names


def author_fields(author: str) -> dict[str, str]:
    """作者姓名 → `\\myfirstauthor` / `\\mysecondauthor`…（与人工成品一致的写法）。

    多作者：第一作者进 `\\myfirstauthor`，其余进 `\\mysecondauthor…`。实测
    2026_05 Jingru_Kou 是 `{\\kaishu 寇静茹}` + `\\renewcommand{\\mysecondauthor}{\\kaishu 刘叶涛}`。
    只在**纯中文姓名**时拆分——`Jiawei Chu` 这种英文名拆开就错了。

    单作者也不能直接把原串写进宏（案例 71）：源稿里排成 `陈  波` 时，
    `cn_author_names()` 已经把它并回一个 `陈波`，可原串还带着内部空白——
    照抄原串会让页眉印成 `陈  波`。所以单作者走 `cn_names[0]`；
    只有**拆不出中文姓名**（英文名 / 空）时才用原串。
    """
    author = (author or "").strip()
    cn_names = cn_author_names(author)
    if len(cn_names) > 1:
        out = {"myfirstauthor": f"\\kaishu {cn_names[0]}"}
        for nm, ordw in zip(cn_names[1:5], _ORDINALS[1:]):
            out[f"my{ordw}author"] = f"\\kaishu {nm}"
        return out
    if not author:
        return {"myfirstauthor": "null"}
    return {"myfirstauthor": f"\\kaishu {cn_names[0] if cn_names else author}"}


# 作者槽位：\myfirstauthor … \myfifthauthor（模板最多印 5 个）。
# 槽位序号与英文宏名（`mysecondauthorEN`…）共用——两处必须同源，错位就会把第二个
# 作者的名字写进第三个作者的槽位。
_ORDINALS: tuple[str, ...] = ("", "second", "third", "fourth", "fifth")
_AUTHOR_SLOTS: tuple[tuple[str, str], ...] = (
    ("", "第一作者"),
    ("second", "第二作者"),
    ("third", "第三作者"),
    ("fourth", "第四作者"),
    ("fifth", "第五作者"),
)


def _AUTHOR_MACRO(word: str) -> str:
    """槽位词 → 作者宏名。第一作者是 `myfirstauthor`（不是 `myauthor`）——
    这里只写一次，别让调用方各自拼字符串（2026-10-15 实测踩到过）。"""
    return "myfirstauthor" if not word else f"my{word}author"

# 英文合著者槽位的分隔符判据（`en_coauthor_values()`）：**刻意保守**。
#   · 作者行只在 `and` / `&` / `;` / `、` 处切——**不用逗号**：英文元数据里的
#     `Kou, Jingru`（倒装式）逗号是姓名的一部分，切了就凭空多出一个作者；
#   · 单位行只在分号、或 `docxsource` 自己的多单位连接符（`\\　\hspace{…}`）处切。
_EN_AUTHOR_SEP = re.compile(r"\s*(?:;|、|，|&|\band\b)\s*")
_EN_AFFIL_SEP = re.compile(r"\s*;\s*|\\\\")


def _split_en_list(text: str, sep: re.Pattern) -> list[str]:
    """按 `sep` 切英文列表；切不出（没有分隔符）时就是**一段**，绝不按空格硬猜。"""
    text = (text or "").strip()
    if not text:
        return []
    return [p.strip() for p in sep.split(text) if p.strip()]


def coauthor_slot_count(field_values: dict[str, str]) -> int:
    """这份字段里**确实存在**的作者槽位数（第一作者也算）。

    判据与 `coauthor_fields()` 完全同源（`is_parsed(my<N>author)`），所以
    「槽位有几个」与「要不要写占位符」不会各判一套。
    ⚠️ 第一作者的宏名是 `myfirstauthor`（带 `first`），空槽位词拼出来会是 `myauthor`——
    这里必须走 `_AUTHOR_MACRO`，不能自己拼字符串（2026-10-15 实测踩到）。
    """
    return sum(1 for word, _label in _AUTHOR_SLOTS
               if is_parsed(field_values.get(_AUTHOR_MACRO(word))))


#: 英文姓名里不该出现在「连写作者串」中的小写功能词/中缀（Lead 裁决，案例 163）。
_EN_NAME_STOP_WORDS = frozenset({
    "and", "or", "et", "al", "van", "von", "de", "der", "den", "del", "della",
    "di", "da", "du", "la", "le", "bin", "ibn", "of", "the", "with", "for",
})


def _split_en_names_by_count(author_en: str, n: int) -> list[str]:
    r"""把**连写成一个字段**的英文作者串按声明作者数等分（2 词/人）。

    第四十五批 A（90-01 Jingru_Kou 实测）：源稿英文作者行是 `Jingru Kou Yetao Liu`
    （没有 and/&/; 分隔符），旧逻辑 `_split_en_list` 只按显式分隔符切 → 整串进不了
    第二作者槽位，模板印占位符；人工成品是 `\mysecondauthorEN{Yetao Liu}`。

    **严格判据（案例 163 / 第四十五批 Lead 裁决）**，任一不满足就**不猜**（返回 []）：
      ① 词数恰好 = 2 × 声明作者数（2–5 位作者）；
      ② 每个词都是 Title-case 姓名形态（首字母大写、其余为字母/`'`/`-`，可带缩写句点）；
      ③ 不含小写功能词/连写中缀（and/van/von/de/der/la/le/et al…）；
      ④ 每段第二个词是「姓」形态（去标点后 ≥2 字母）。
    """
    words = (author_en or "").split()
    if not (2 <= n <= 5) or len(words) != 2 * n:
        return []
    for w in words:
        if not re.fullmatch(r"[A-Z][^\W\d_]*\.?", w):
            return []
        if w.rstrip(".").lower() in _EN_NAME_STOP_WORDS:
            return []
    pairs: list[str] = []
    for i in range(n):
        given, family = words[2 * i], words[2 * i + 1]
        if len(family.rstrip(".")) < 2:
            return []
        pairs.append(f"{given} {family}")
    return pairs


def en_coauthor_values(
    author_en: str, affiliation_en: str, n_authors: int
) -> dict[str, str]:
    """源稿英文元数据 → 合著者的**英译名 / 英译单位**（取不到就不给，交给占位符）。

    为什么必须有这一步（GAP 2，2026-10-15 用户实测）：模板 1.5 节把
    `mysecondauthorEN` 预置成 `null`、`mysecondaffiliationEN` 预置成示例文字
    `affiliation 2`。中文槽位有第二作者、英文槽位却是示例文字时，英文摘要页要么
    整块少一个人、要么把 `affiliation 2` 印出来。

    判据**刻意保守**（用户要求「不许猜」，给作者改错名字/单位是学术事故）：
      · 作者行只在显式分隔符（`and` / `&` / `;` / `、`）处切，**不用逗号**；
      · 单位行只在分号或 docxsource 的多单位连接符处切；
      · **段数必须与中文作者槽位数完全一致**，才按位置取第 2…5 段；
      · **绝不**把第一作者的单位复制给第二作者——人工成品里两份恰好相同是巧合，
        不是规则（`Jingru_Kou` 正是这种巧合：第一/第二作者同属南开大学哲学院）。
    单作者稿（`n_authors <= 1`）一律返回空 dict：产物与从前逐字节相同。
    """
    out: dict[str, str] = {}
    n = int(n_authors or 0)
    if n <= 1:
        return out
    names = _split_en_list(author_en, _EN_AUTHOR_SEP)
    if len(names) == 1 and n > 1:
        # 连写的英文作者串（无显式分隔符）→ 按作者数等分；不满足保守判据就不猜。
        names = _split_en_names_by_count(author_en, n) or names
    if len(names) == n:
        for word, nm in zip(_ORDINALS[1:], names[1:]):
            if nm:
                out[f"my{word}authorEN"] = nm
    affs = _split_en_list(affiliation_en, _EN_AFFIL_SEP)
    if len(affs) == n:
        for word, af in zip(_ORDINALS[1:], affs[1:]):
            if af:
                out[f"my{word}affiliationEN"] = af
    return out


def coauthor_fields(field_values: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """合著者槽位 → 还需显式化的 (字段, 人话名)。

    模板里 `mysecondaffiliation`=「单位2（到系所）」、`mysecondemail`=「xxxx@xxxx.xxx」，
    多作者稿的标题页会按作者数选排版分支（sty 里的 oneauthors/twoauthors… 那一套），
    把这些示例文字原样印出来——实测 2026_05 Jingru_Kou 编译后 PDF 里第二作者
    就印着「单位2（到系所）」和「xxxx@xxxx.xxx」。

    只在**该槽位作者确实存在**（`mysecondauthor` 有非空、非 null 的值）时才算：
    单作者稿里 `mysecondauthor` 是 null，这些字段根本不会打印，不该打扰编辑。

    2026-10-15 扩到**英文宏**（GAP 2）：同一份模板 1.5 节里
    `mysecondauthorEN` 预置 `null`、`mysecondaffiliationEN` 预置示例文字
    `affiliation 2`，英文摘要页（`printtitlepageEN`）按 `mysecondauthorEN` 是否
    为 `null` 选单人/双人分支——中文槽位有第二作者、英文槽位却留着示例文字时，
    英文页要么少一个人、要么把 `affiliation 2` 印出来。所以中文槽位一旦存在，
    **英文槽位也必须显式化**：源稿给了可靠英译名就填（`en_coauthor_values()`），
    否则写「未识别」占位符。`my<N>email` 中英文共用同一个宏（sty 的英文分支也读
    `mysecondemail`），已经含在下面两条里，不另开槽位。
    """
    out: list[tuple[str, str]] = []
    for word, label in _AUTHOR_SLOTS[1:]:
        if not is_parsed(field_values.get(f"my{word}author")):
            continue
        out.append((f"my{word}affiliation", f"{label}单位"))
        out.append((f"my{word}email", f"{label}邮箱"))
        out.append((f"my{word}authorEN", f"{label}英文姓名"))
        out.append((f"my{word}affiliationEN", f"{label}英文单位"))
    return tuple(out)



def override_fields(
    overrides: dict | None, is_en: bool
) -> tuple[dict[str, str], list[str]]:
    """手工覆盖（`--title-cn` 等 / 界面输入）→ 模板字段。

    接口约定（与 convert.py、界面三方一致）::

        overrides = {title_cn, author_cn, affiliation_cn, email,
                     abstract_cn, title_en, abstract_en}

    **非空的值优先于解析结果**；被覆盖的字段不再算「未识别」，不写占位符。
    空值 / None / 空 dict 一律不生效——此时行为与加这个参数之前逐字一致。

    返回 `({模板字段: 值}, 没认出来的键)`。
    """
    ov = {k: (v or "").strip() for k, v in dict(overrides or {}).items()}
    unknown = sorted(k for k in ov if k not in OVERRIDE_KEYS)
    ov = {k: v for k, v in ov.items() if k in OVERRIDE_KEYS and v}
    out: dict[str, str] = {}
    # 中英题名/摘要在两种模板里落在**不同字段**：CN 模板 mytitle=中文、mytitleEN=英文；
    # EN 模板 mytitle=英文、mytitleCN=中文。填错会让摘要跑到正文里（实测把
    # 02 Lu_Jiang 的内容准确率压到 8.7%），所以这里必须按模板类型分流。
    if "title_cn" in ov:
        out["mytitleCN" if is_en else "mytitle"] = ov["title_cn"]
    if "title_en" in ov:
        out["mytitle" if is_en else "mytitleEN"] = ov["title_en"]
    if "abstract_cn" in ov:
        out["myabstractCN" if is_en else "myabstract"] = ov["abstract_cn"]
    if "abstract_en" in ov:
        out["myabstract" if is_en else "myabstractEN"] = ov["abstract_en"]
    if "author_cn" in ov:
        out.update(author_fields(ov["author_cn"]))
    if "affiliation_cn" in ov:
        out["myfirstaffiliation"] = ov["affiliation_cn"]
    if "email" in ov:
        out["myfirstemail"] = ov["email"]
    return out, unknown


def missing_note(labels: list[str]) -> str:
    """未识别字段的汇总警告（不写进产物的字段清单，只报给人看）。"""
    return (
        "以下字段未能从源稿解析，产物中已写「【未识别：…，请手工填写】」占位符，"
        "请手工填写或用 --title-cn/--author-cn/--affiliation-cn/--email/"
        "--abstract-cn/--title-en/--abstract-en 覆盖：" + "、".join(labels)
    )


def insert_custom_macros(template_text: str, macros: list[tuple[str, str, str]]) -> tuple[str, list[str]]:
    """把旧稿的自定义命令插进模板的 2.5 节。

    正文依赖这些命令（如 \\val、\\e、\\A），漏搬会直接编译失败。只搬模板里
    还没有的，避免重复定义。**例外（第 130 条）**：`\\renewcommand` 同名时允许插入——
    它就是来覆盖的（作者前言覆盖本地旧 sty 的定义，与旧稿编译顺序一致）。本地迁移
    定义插在 hyperref 之前，这里插在它之后 → 后定义生效。
    """
    existing = set(parse_macros(template_text))
    add = [
        (k, n, src) for k, n, src in macros
        if n not in existing or k == "renewcommand"
    ]
    if not add:
        return template_text, []

    block = "\n".join(src for _, _, src in add)
    lines = template_text.split("\n")
    for idx, ln in enumerate(lines):
        if HYPERREF_ANCHOR in ln or PRE.is_hyperref_line(ln):
            lines.insert(idx + 1, block)
            return "\n".join(lines), [n for _, n, _ in add]
    # 退路：插到 \begin{document} 之前
    for idx, ln in enumerate(lines):
        if ln.strip().startswith("\\begin{document}"):
            lines.insert(idx, block)
            return "\n".join(lines), [n for _, n, _ in add]
    raise ValueError("找不到自定义命令的插入点")


@dataclass
class BuildResult:
    tex_path: str
    bib_path: str | None
    sty_path: str | None
    log: list[str] = field(default_factory=list)
    rule_hits: dict[str, int] = field(default_factory=dict)
    audit: list[tuple[str, str, int, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    bib_keys_used: set[str] = field(default_factory=set)
    template: str = ""  # 本次采用的模板说明（版面样式 + 骨架）
    # 「条目 → 源稿原文行」的两份材料（第 68 条），**纯附加**、默认空：
    #   · ref_raw_lines  = 文献区原文行（`DocxDoc.ref_raw_lines`，第五节的兜底材料）；
    #   · ref_raw_by_key = {最终 bib 键: 该条的源稿原文}（逐条附给任务书与 AI 预跑）。
    # 为什么挂在 BuildResult 上：`convert.py` 的 ⑪ 手里只有 BuildResult（`build_docx`
    # 不返回 DocxDoc），从前它为了拿原文不得不在「0 条」时**再解析一遍**源稿；
    # 现在解析一次就够，且正常出条目的稿子也拿得到逐条原文。LaTeX 通道没有源稿原文，
    # 两份都留空（下游按「没有原文」处理，不写占位符）。
    ref_raw_lines: list[str] = field(default_factory=list)
    ref_raw_by_key: dict[str, str] = field(default_factory=dict)
    # 第 120 条：正文源是多文件稿时展开了几处 \input/\include（0 = 单文件 / Word）。
    expanded_inputs: int = 0


def _citation_points(source_body: str, unmatched: list[str]) -> list[tuple[str, int, str]]:
    r"""把「没转成 cite 的引用」定位到源稿段落（第 80 条）。

    `link_citations()` 只回一串引用原文（`（Beal  Restall 2006）`），**不带位置**；
    可编辑要修的是源稿里**那一段**，所以位置线索必须在这里补上。三样都是**原文里查得到**的：

      · 引用原文（逐字，`unmatched` 里那一条）；
      · 段落序号（1 起）——按 `doc.body` 的行计（`docxsource` 用 `"\n".join(out)` 拼正文，
        一行一段）；
      · 该段**开头**若干字（定位「哪一段」用，不是引用点前后文）。

    定位靠 `str.find` 顺序推进（`unmatched` 的顺序 = 正则在正文里扫描的顺序，同一次替换
    产生，所以顺序一致）；**找不到就如实留空**（段落序号 0、片段空串），不许编。
    """
    points: list[tuple[str, int, str]] = []
    cursor = 0
    for raw in unmatched:
        i = source_body.find(raw, cursor)
        if i < 0:                      # 顺序假设不成立时退一步整篇找
            i = source_body.find(raw)
        if i < 0:                      # 真找不到（源稿里没有这个片段）
            points.append((raw, 0, ""))
            continue
        cursor = i + len(raw)
        para_start = source_body.rfind("\n", 0, i) + 1
        para_end = source_body.find("\n", i)
        if para_end < 0:
            para_end = len(source_body)
        para = source_body[para_start:para_end]
        snippet = re.sub(r"\s+", " ", para).strip()[:30]
        points.append((raw, source_body.count("\n", 0, i) + 1, snippet))
    return points


def _write_bib_report(bib_path: str, out_dir: str, stem: str, say, warnings: list,
                      unmatched: list[tuple[str, int, str]] | None = None) -> None:
    """生成参考文献核对报告。

    按用户要求核对：期刊文章要有卷号/期号/页码；书籍章节要有页码/出版地/出版商；
    书籍要有出版地/出版商；学位论文与会议论文按往期样式。**只报告，不擅自改内容。**

    `unmatched`（第 80 条，**可选、纯附加**）= `_citation_points()` 的结果，即
    「正文里没转成 `\\parencite` 的引用点」。给了它（非空）时：
      · 报告里追加一节，逐条列出引用原文 + 段落序号 + 段首若干字；
      · 只要它非空就**出报告**（哪怕字段检查一个问题都没有）——这条留档正是本次要修的缺陷；
      · `bibcheck.check_citations()` 收到这批原文，② 冗余条目后面会追加「更可能是引用没转」
        的可核提示（判据与措辞见 `slcn/bibcheck.py` 的「第 80 条」一节）。
    不给 / 给空表时，报告内容与从前**逐字节相同**（连新小节标题都不会出现）。
    """
    from . import bibcheck

    text = open(bib_path, encoding="utf-8", errors="replace").read()
    rep = bibcheck.check(text)
    points = list(unmatched or [])
    # ⚠️ 门槛 = 「字段有问题」**或**「有没转成 cite 的引用」。
    #    从前只看前者，于是「bib 干净 + 正文有引用没转」的稿子连报告都不出（第 80 条）；
    #    反过来，两个条件都不满足时行为与从前一字不差（第 61 条修好的出报告门槛不受影响）。
    if not rep.issues and not points:
        say(f"   参考文献核对：{rep.total} 条，未发现问题 ✅")
        return

    path = os.path.join(out_dir, f"{stem}_参考文献核对.txt")
    body = bibcheck.format_report(rep, f"{stem} 参考文献核对")
    # 引用双向核对（悬空引用 / 冗余条目 / 疑似漏引）——需要产物 .tex，所以要报告就得先有它
    tex_p = os.path.join(out_dir, f"{stem}.tex")
    cites = []
    if os.path.exists(tex_p):
        cites = bibcheck.check_citations(
            open(tex_p, encoding="utf-8", errors="replace").read(), text,
            unmatched=[p[0] for p in points],
        )
    if cites:
        body += "\n\n  ── 引用核对（" + str(len(cites)) + " 条）──\n"
        body += "\n".join("  " + c for c in cites)
        warnings.append(f"引用核对发现 {len(cites)} 条待确认项，详见 {os.path.basename(path)}")
    if points:
        body += "\n\n  ── 正文里没转成引用的引用点（" + str(len(points)) + " 处）──\n"
        body += ("  这些位置在正文里仍是纯文本，没有变成 \\parencite{…}；对应的参考文献条目"
                 "可能因此被上面的「② 冗余条目」判为「正文从未引用」——那是这处引用没转，"
                 "不是条目多余，请人工核对。\n")
        body += ("  段落序号按引擎解析出的正文段落计（源稿的标题行与摘要/关键词区不计入），"
                 "请配合「段首」文字定位——两样都与源稿对得上。\n")
        for raw, para_no, snippet in points:
            body += f"  · 「{raw}」\n"
            if para_no and snippet:
                body += f"      源稿正文第 {para_no} 段（段首「{snippet}…」）\n"
            else:
                body += "      （未能在源稿正文里定位到该片段，请全文检索此引用原文）\n"
        warnings.append(
            f"{len(points)} 处未匹配引用的位置已写入 {os.path.basename(path)}"
            "（逐条给了源稿段落序号与段首）"
        )
    # 旧版自动备份 + 原子替换（UI 方案第 4 件；编辑可能手工改过这份报告）
    from .fsutil import atomic_write as _atomic_write
    _atomic_write(path, body + "\n", newline="\n", log=say)
    say(f"   参考文献核对：{rep.total} 条，必补 {rep.n_must} 项、建议 {rep.n_nice} 项"
        f" → {os.path.basename(path)}")
    if rep.n_must:
        warnings.append(
            f"参考文献有 {rep.n_must} 项必补字段（卷号/期号/页码/出版地等），"
            f"详见 {os.path.basename(path)}"
        )


def _prepare_template(lang: str, sty: str | None, skeleton: str | None, say) -> tuple[str, T.ResolvedTemplate]:
    """解析用户选定的模板、校验、读入骨架并同步 \\usepackage 行。"""
    t = T.resolve(lang, sty=sty, skeleton=skeleton)
    problems = T.validate(t)
    if problems:
        raise FileNotFoundError("；".join(problems))
    say(f"   采用模板：{T.describe(t)}")
    say(f"     版面样式 {os.path.basename(t.sty)}  ({os.path.getsize(t.sty)} 字节)")
    say(f"     骨架     {os.path.basename(t.skeleton)}  ({os.path.getsize(t.skeleton)} 字节)")
    tpl = open(t.skeleton, encoding="utf-8").read()
    tpl, note = T.sync_package_line(tpl, t.sty_name)
    if note:
        say(f"     ⚠ {note}")
    return tpl, t


def build_latex(
    body_path: str,
    out_dir: str,
    *,
    metadata_path: str | None = None,
    bib_path: str | None = None,
    basename: str = "output",
    lang: str = "zh",
    sty: str | None = None,
    skeleton: str | None = None,
    meta: dict | None = None,
    overrides: dict | None = None,
    verbose: bool = True,
    dash_hints: bool = True,
    bib_zero_cites_hints: bool = True,
    breakable_algorithm: bool = False,
) -> BuildResult:
    """LaTeX 通道：多源合并成初排稿。

    **所有源文件都必须由调用方显式给出，本函数不做任何自动发现。**
    这是刻意的设计——用户要求「强制提供源文件，不要替用户默认填充」。

    body_path      正文源（必填）。可以是纯正文稿，也可以是完整模板稿。
    metadata_path  元数据源（可选）。多源合并时指上一轮的模板稿；
                   不传则退回用 body_path 自身取元数据。
    bib_path       参考文献库（可选）。不传则用 body_path 同目录的 .bib。
    sty / skeleton 模板文件；不给则按 lang 取默认（中文 SLCN-MIX，英文 SLEN）。
    overrides      手工覆盖 {title_cn, author_cn, affiliation_cn, email,
                   abstract_cn, title_en, abstract_en}；**非空值优先于元数据源**，
                   被覆盖的字段不再算「未识别」。None / 空 dict = 与从前逐字一致。
    dash_hints     **ZH-08 连接号待人工判断**的日志提示（第四十三批，默认开）：
                   True = 命中数 + 前 20 条上下文 + 判断指引写进初排日志；
                   False = 不打印。**纯日志，产物逐字节不变**（引擎不做语义自动分类）。
    bib_zero_cites_hints  **B 类可发现性提示**（第四十四批④，默认开）：
                   bib 条数>0 而正文 `\\parencite/\\cite` 数=0 时（biblatex 只打印被引
                   条目 → PDF 参考文献区会是空的），在初排日志**置顶**提示 + 计数，
                   供编辑人工补引用/核对体例。**纯日志，产物逐字节不变**；False=不打印。
    breakable_algorithm  **algorithm 去浮动（第四十八批 dry-run 可选开关，默认关）**：
                   True = 源稿 `algorithm` 浮动体改为 `breakablealgorithm`（可跨页，
                   88-04 人工成品同款写法）并在导言区注入环境定义；默认 False 时
                   产物与从前逐字节一致。**默认行为改动需 Lead 批准后才接线 CLI。**
    """
    log: list[str] = []
    warnings: list[str] = []

    def say(s: str) -> None:
        log.append(s)
        if verbose:
            print("   " + s)

    if not os.path.exists(body_path):
        raise FileNotFoundError(f"正文源不存在：{body_path}")
    say("① 源文件（由调用方显式指定）")
    say(f"   正文源     {os.path.basename(body_path)}")
    meta_src = metadata_path or body_path
    if metadata_path and metadata_path != body_path:
        say(f"   元数据源   {os.path.basename(metadata_path)}")
    else:
        say("   元数据源   同正文源")
    if bib_path:
        say(f"   参考文献   {os.path.basename(bib_path)}")

    say("② 载入模板骨架（用户可选）")
    tpl, tpl_sel = _prepare_template(lang, sty, skeleton, say)
    lang = tpl_sel.lang
    tpl_fields = set(parse_macros(tpl))
    # ⚠️ 必须在**搬运元数据之前**取模板原值：搬到一半再取，字段已被覆盖，
    #    「值与模板示例文字相同」就永远成立，会把所有字段都误判成未识别。
    tpl_defaults = template_defaults(tpl)
    say(f"   模板字段 {len(tpl_fields)} 个")

    # ── ②′ 前言依赖迁移（第 120 条）─────────────────────────────────────
    # 作者常交「完整模板稿」：正文依赖前言里的宏包 / tikz 库 / 定义。换成新版模板
    # 骨架后这些不跟着走就编译失败，而且报错离真正原因很远（\llbracket undefined、
    # Unknown arrow tip kind 'Stealth'…）。只搬模板没有的，且一律不碰模板管理项。
    try:
        sty_text = open(tpl_sel.sty, encoding="utf-8", errors="replace").read()
    except OSError:
        sty_text = ""
    meta_text = (
        open(meta_src, encoding="utf-8", errors="replace").read()
        if os.path.exists(meta_src) else ""
    )
    # 前言本身也可能用 \input 拆文件（多文件稿常把宏包/命令放 preamble.tex）；
    # 展开后再做依赖/宏/元数据判据（注释掉的不展开，见 latex.expand_inputs）。
    if meta_src.lower().endswith(".tex") and count_active_inputs(meta_text):
        meta_text = expand_inputs(meta_src, meta_text)
    # 正文在这里先取一次（第 120 条）：多文件 `.tex` 稿先展开 \input/\include；
    # 后面前言依赖判据、④ 的宏筛选、⑤ 的正文迁移都复用这一份，避免重复读文件。
    raw_body = open(body_path, encoding="utf-8", errors="replace").read()
    expanded_inputs = 0
    if body_path.lower().endswith(".tex") and count_active_inputs(raw_body):
        stats: dict = {}
        raw_body = expand_inputs(body_path, raw_body, _stats=stats)
        expanded_inputs = stats.get("expanded", 0)  # 只算真正展开成功的（注释/缺失不算）
    body = extract_body_text(raw_body)
    # 第四十六批②：参考文献**之后**的附录内容（extract_body_text 会截掉，86-05 实测）
    _post_bib_tail = _latex_post_bibliography_tail(raw_body)
    pre_plan = PRE.plan_migration(
        meta_text, tpl, sty_text, tpl_sel.sty_name, body,
        source_dir=os.path.dirname(os.path.abspath(body_path)),
    )
    if pre_plan.lines:
        tpl = PRE.insert_lines(tpl, pre_plan.lines, HYPERREF_ANCHOR)
        bits = []
        manual_pkgs = [p for p in pre_plan.packages
                       if p not in pre_plan.auto_packages and p not in pre_plan.env_packages]
        if manual_pkgs:
            bits.append(f"宏包 {len(manual_pkgs)} 个（{'、'.join(manual_pkgs)}）")
        if pre_plan.tikz_libs:
            bits.append(f"tikz 库 {len(pre_plan.tikz_libs)} 个（{'、'.join(pre_plan.tikz_libs)}）")
        if pre_plan.definitions:
            bits.append(f"定义 {len(pre_plan.definitions)} 个（{'、'.join(pre_plan.definitions)}）")
        if pre_plan.auto_packages:
            bits.append(
                f"按正文符号依赖自动补宏包：{'、'.join(pre_plan.auto_packages)}"
            )
        # 第四十五批 A：正文环境用到的宏包写在作者本地 .sty 里时补迁移（86-05 实测）
        if pre_plan.env_packages:
            bits.append(
                f"按正文环境依赖补宏包：{'、'.join(pre_plan.env_packages)}"
            )
        say("   前言依赖迁移：" + "；".join(bits))
    else:
        say("   前言依赖：无需迁移")
    if pre_plan.skipped:
        show = "、".join(pre_plan.skipped[:12]) + (" …" if len(pre_plan.skipped) > 12 else "")
        say(f"   · 模板已提供、未重复搬：{show}")
    # ── 第 130 条：本地支撑文件（.cls/.sty/.def/.cfg）的定义迁移明细 ──
    if pre_plan.local_packages:
        show = "、".join(pre_plan.local_packages)
        say(f"   本地旧模板 .sty（源目录有同名文件，包行不迁、按差集搬定义）：{show}")
    if pre_plan.support_files:
        say(f"   本地支撑文件：{'、'.join(pre_plan.support_files)}")
    if pre_plan.counter_definitions:
        say(f"   计数器迁移 {len(pre_plan.counter_definitions)} 个："
            f"{'、'.join(pre_plan.counter_definitions)}")
    if pre_plan.local_definitions:
        items = [f"{name}（{origin}）" for name, origin in pre_plan.local_definitions]
        say(f"   本地旧模板定义迁入 {len(items)} 个：{'、'.join(items)}")
    if pre_plan.converted_renews:
        show = "、".join(pre_plan.converted_renews[:12])
        say(f"   · 模板缺、由 \\renewcommand 降为 \\newcommand 迁入：{show}")
        warnings.append(
            "本地旧文件里的 \\renewcommand 在新模板中没有对应命令，已降为 \\newcommand 迁入"
            f"（请核对语义）：{'、'.join(pre_plan.converted_renews)}"
        )
    if pre_plan.overrides:
        show = "、".join(pre_plan.overrides[:12])
        say(f"   ⚠ 本地旧文件重定义了模板命令、未搬（模板优先）：{show}")
        warnings.append(
            "本地旧文件重定义了模板命令、未搬（保留现行模板，正文用到时按模板定义走）"
            f"：{'、'.join(pre_plan.overrides)}；语义可能与作者原稿不同，请人工核对"
        )
    # ── 第 141 条：模板用 biblatex 时，natbib/gbt7714 包行不迁 + 正文命令改写 ──
    if pre_plan.replaced_packages:
        body, n_natbib = PRE.rewrite_natbib_commands(body)
        show = "、".join(pre_plan.replaced_packages)
        say(f"   biblatex 取代 {show}：包行未迁移；正文 natbib 引用命令改写 {n_natbib} 处")
        warnings.append(
            f"模板已用 biblatex，作者 {show} 的包行未迁移，正文 natbib 引用命令已改写为"
            f" biblatex 等价命令（{n_natbib} 处）——请核对引用样式（圆括号/叙述式）"
        )
    # ── 第 141 条：与模板已加载宏包重合同名命令的作者宏包不迁（bbding / marvosym）──
    if pre_plan.conflicts:
        show = "；".join(pre_plan.conflicts)
        say(f"   ⚠ 与模板宏包冲突、未迁移：{show}")
        warnings.append(
            f"以下作者宏包与模板已加载宏包重合同名命令，未迁移：{show}。"
            "若正文用到该宏包的专有命令，编译会报 Undefined control sequence，请按需人工补"
        )

    say("③ 搬运元数据")
    missing: list[str] = []
    src_macros: dict = {}
    # 真正从元数据源搬进来的字段 → 值（下面判「未识别」用）
    moved_vals: dict[str, str] = {}
    if os.path.exists(meta_src):
        src_macros = parse_macros(meta_text)
        moved = 0
        for name, m in src_macros.items():
            if not name.startswith("my") or name not in tpl_fields:
                continue
            val, notes = clean_field_value(name, m.value)
            for n in notes:
                say(f"   · {n}")
            if val.strip() in ("", "null"):
                continue
            tpl = set_field(tpl, name, val)
            moved_vals[name] = val
            moved += 1
        say(f"   搬入 {moved} 个字段")
    else:
        warnings.append(f"元数据源不存在，所有字段留空：{meta_src}")
    # 编辑侧字段（卷期页）作者稿里没有，需人工或从期目录读取
    for f in ("myvolnumber", "myissnumber", "mypubyear", "myfirstpage", "mylastpage", "mypages"):
        if f not in src_macros or src_macros[f].is_null:
            missing.append(f)
    ed = editorial_fields(meta)
    for k, v in ed.items():
        if k in tpl_fields:
            tpl = set_field(tpl, k, v)
    missing = [f for f in missing if f not in ed]
    if ed:
        say(f"   填入出版信息 {len(ed)} 项：{'、'.join(ed)}")
    if missing:
        warnings.append("以下编辑侧字段需人工填写：" + "、".join(missing))

    is_en = "mytitleCN" in tpl_fields
    # ── 手工覆盖：非空值优先于旧稿里的元数据（--title-cn / 界面输入）──
    ov, unknown_keys = override_fields(overrides, is_en)
    if unknown_keys:
        warnings.append("overrides 里有未识别的键（本次忽略）：" + "、".join(unknown_keys))
    dropped = [k for k in ov if k not in tpl_fields]
    if dropped:
        warnings.append("手工覆盖对应的模板字段不存在，本次忽略：" + "、".join(dropped))
    ov = {k: v for k, v in ov.items() if k in tpl_fields}
    for k, v in ov.items():
        tpl = set_field(tpl, k, v)
        moved_vals[k] = v
    if ov:
        say(f"   手工覆盖 {len(ov)} 个字段：{'、'.join(ov)}")

    # ── 必填字段解析不出来 → 写占位符 + 警告，不许留模板默认值 ──
    #    清单 = 文章级/第一作者字段 + 「确实存在的合著者」的单位与邮箱
    undetected: list[str] = []
    for name, label in REQUIRED_FIELDS["en" if is_en else "zh"] + coauthor_fields(moved_vals):
        if name not in tpl_fields:
            continue
        if is_parsed(moved_vals.get(name), name, tpl_defaults):
            continue
        tpl = set_field(tpl, name, missing_placeholder(name, label))
        undetected.append(label)
    if undetected:
        say(f"   ⚠ 未识别 {len(undetected)} 个字段，已在产物中写占位符：{'、'.join(undetected)}")
        warnings.append(missing_note(undetected))

    say("④ 搬运自定义命令（正文依赖，漏搬会编译失败）")
    # 第 120 条：先取正文（`.tex` 多文件稿先展开 \input/\include），再判「哪些宏正文
    # 真的用得到」——没用到的定义不搬（07 Yiyan_Wang 的 \ar 就是这样避开 xy 冲突的），
    # 与模板/宏包重名的宏自动改名（\prop → \slcnprop），正文同步改。
    # 第 130 条：**正文区**（\begin{document} 之后）的自定义定义不迁移——正文整段
    # 照搬已经带着它们；再从 meta_text 里搬一遍会在产物里重复定义（\newcommand 直接
    # `already defined`）。纯正文稿（没有 \begin{document}）仍按全文收，保持旧行为。
    macro_scope = (
        PRE.source_preamble(meta_text)
        if "\\begin{document}" in meta_text
        else meta_text
    )
    custom = collect_custom_macros(macro_scope)
    existing_names = PRE.defined_names(tpl) | PRE.defined_names(sty_text)
    # 第 130 条：本地支撑文件搬进来的同名宏**不算冲突**——作者前言里的 `\renewcommand`
    # 本来就是覆盖旧 sty 的那个定义（旧稿编译时也是这个先后顺序）。把它排除在改名判据外，
    # 前言里的重定义按 TeX 顺序自然覆盖；否则会被改名成一个没人用的新名字（语义丢失）。
    existing_names -= {name for name, _origin in pre_plan.local_definitions}
    # 可达文本 = 正文 + 已搬进模板的元数据字段值 + 已迁移的前言定义体：
    # 只管正文会漏掉「宏只用在摘要/题名里」的稿（会 undefined），2026-10-17 评审指出。
    reach = "\n".join(
        [body]
        + list(moved_vals.values())
        + list(pre_plan.lines)
    )
    selected, renames = PRE.select_custom_macros(custom, reach, existing_names)
    if renames:
        body = PRE.apply_renames(body, renames)
        # 元数据字段值/前言定义体已在 tpl 里 → 整份模板同步改名（词边界，不碰 \proposal 等）
        tpl = PRE.apply_renames(tpl, renames)
        warnings.append(
            "宏名冲突，已自动改名（正文与定义同步）："
            + "、".join(f"\\{o} → \\{n}" for o, n in renames.items())
        )
    renamed_macros = []
    for kind, name, src in selected:
        new_name = renames.get(name, name)
        snippet = PRE.apply_renames(src, renames)
        if new_name != name and kind == "renewcommand":
            # 改成新名字后模板里没有它，\renewcommand 会 undefined → 降为 \newcommand
            snippet = re.sub(r"^\s*\\renewcommand", r"\\newcommand", snippet, count=1)
            kind = "newcommand"
        renamed_macros.append((kind, new_name, snippet))
    tpl, added = insert_custom_macros(tpl, renamed_macros)
    say(f"   新增自定义命令 {len(added)} 个：{'、'.join(added) if added else '（无）'}")
    if len(custom) > len(selected):
        kept = {name for _kind, name, _src in selected}
        skipped = [name for _kind, name, _src in custom if name not in kept]
        say(f"   · 正文未用到、未搬：{len(skipped)} 个（{'、'.join(skipped)}）")

    say("⑤ 迁移正文")
    if expanded_inputs:
        say(f"   多文件正文源：已展开 {expanded_inputs} 处 \\input/\\include")
    # ── 第 130 条：正文区的自定义定义（不迁移，只提示）+ 定义缺失预检 ──
    # 正文是整段照搬的，正文里的定义本来就跟着走；但按本刊惯例定义该放导言区，
    # 所以只打一行提示、不做任何猜测性搬动。
    body_defs = PRE.scan_support_definitions(body)
    if body_defs:
        say(f"   正文里出现 {len(body_defs)} 处自定义定义（保留原样，建议移到导言区）")
    missing_envs = PRE.missing_environments(
        body, tpl + "\n" + sty_text, {d.name for d in body_defs}
    )
    if missing_envs:
        show = "、".join(missing_envs[:12]) + (" …" if len(missing_envs) > 12 else "")
        say(f"   ⚠ 定义缺失预检：正文用到的环境在「模板+迁移结果」里找不到定义：{show}")
        warnings.append(
            "定义缺失预检：正文用到的环境在「模板+迁移结果」里找不到定义："
            f"{show}（宏包自带的环境纯离线无法核对，可能误报；**不阻断出稿**，"
            "若编译报 Environment … undefined 请人工核对）"
        )
        # 第四十五批 A：已知对应宏包的环境给出**具体建议**；源稿没提供包时只提示，
        # 绝不替作者无脑加包（86-05 的 algorithm 已按源稿旧 .sty 迁移；这里兜底其余）。
        _env_hints = [
            e + " → " + " / ".join(
                "\\usepackage{" + _p + "}" for _p in PRE.ENV_PACKAGE_HINTS[e]
            )
            for e in missing_envs if e in PRE.ENV_PACKAGE_HINTS
        ]
        if _env_hints:
            warnings.append(
                "定义缺失预检（可修）：以下环境需要对应宏包，但模板与源稿均未加载："
                + "；".join(_env_hints)
                + "。若作者源稿确实没提供，请人工确认后再加包（不要无脑加）"
            )
    say(f"   正文 {len(body)} 字符")
    if breakable_algorithm:
        body = _to_breakable_algorithms(body)
        say("   algorithm 去浮动：algorithm → breakablealgorithm（可选开关，默认关）")
    tpl = insert_body(tpl, body)
    if breakable_algorithm:
        tpl = _inject_breakable_algorithm_def(tpl)
    if _post_bib_tail:
        tpl = _insert_post_bibliography(tpl, _post_bib_tail)
        say(f"   参考文献后内容迁移：附录/致谢 {len(_post_bib_tail)} 字符"
            "（源稿 \\printbibliography 之后）")

    say("⑥ 套用预校正规则")
    res = R.apply_rules(tpl, lang=lang, safe_only=True)
    tpl = res.text
    hit_total = sum(res.applied.values())
    say(f"   自动修复 {hit_total} 处，涉及 {len(res.applied)} 条规则")
    for rid, n in sorted(res.applied.items(), key=lambda x: -x[1]):
        say(f"   · {rid:24s} ×{n}")

    audit = R.audit(tpl, lang=lang)

    # ZH-08 连接号：只提示人工判断，不改产物（第四十三批）。
    #    ⚠️ 纯日志，异常绝不阻断出稿（与 ⑤′ 自动改同一纪律）。
    if dash_hints and lang == "zh":
        try:
            _report_dash_hints(tpl, lang, say)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"ZH-08 连接号提示未执行（不影响出稿）：{type(e).__name__}: {e}")

    # 第四十四批④：B 类可发现性提示（初排阶段可见；纯日志、产物逐字节不变）
    if bib_zero_cites_hints:
        _n_bib_tex = 0
        if bib_path:
            try:
                _n_bib_tex = len(re.findall(
                    r"@\w+\{",
                    open(bib_path, encoding="utf-8", errors="replace").read(),
                ))
            except OSError:
                _n_bib_tex = 0
        _n_end_list = (len(doc.refs) if doc.refs
                       else len(doc.ref_key_by_num)) if "doc" in locals() else 0
        try:
            _report_bib_zero_cites(tpl, _n_bib_tex, say, warnings, log,
                                   n_end_list=_n_end_list)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"B 类提示未执行（不影响出稿）：{type(e).__name__}: {e}")

    say("⑦ 写出文件")
    os.makedirs(out_dir, exist_ok=True)
    # 第 121 条：正文 `\includegraphics` 引用的图片从源目录复制进产物目录，
    # 否则 LaTeX 通道产物一定编译失败（04 Chen_Liu 真稿）。
    gfx = copy_referenced_graphics(
        body, meta_text, os.path.dirname(os.path.abspath(body_path)), out_dir, warnings
    )
    if gfx:
        say(f"   复制正文引用图片 {len(gfx)} 个：{'、'.join(gfx[:8])}")
    tex_path = os.path.join(out_dir, f"{basename}.tex")
    # 原子写 + 旧版备份（UI 方案第 4 件）: 产物可能被反复覆盖
    from .fsutil import atomic_write as _atomic_write
    _atomic_write(tex_path, tpl, newline="\n", log=say)
    say(f"   {P.rel(tex_path)}")

    used_keys = extract_bibkeys_from_body(body)
    bib_out = None
    if bib_path:
        from .bibnorm import normalize_bib

        bib_text = open(bib_path, encoding="utf-8", errors="replace").read()
        bib_text, notes = normalize_bib(bib_text)
        bib_out = os.path.join(out_dir, "reference.bib")
        _atomic_write(bib_out, bib_text, newline="\n", log=say)
        say(f"   {P.rel(bib_out)}（{len(notes)} 处规范化）")

        have = set(re.findall(r"@\w+\{([^,]+),", bib_text))
        lack = used_keys - have
        if lack:
            warnings.append(f"正文引用但 bib 里没有的键 {len(lack)} 个：" + "、".join(sorted(lack)[:8]))
    elif used_keys:
        warnings.append(
            f"源目录没有 .bib：正文有 {len(used_keys)} 个引用键，未生成 reference.bib"
            "（LaTeX 稿的参考文献库通常随稿提交；请向作者补要，或用「已有的 .bib」指定）"
        )

    sty_out = os.path.join(out_dir, os.path.basename(tpl_sel.sty))
    shutil.copy2(tpl_sel.sty, sty_out)
    say(f"   {P.rel(sty_out)}")

    return BuildResult(
        tex_path=tex_path,
        bib_path=bib_out,
        sty_path=sty_out,
        template=T.describe(tpl_sel),
        log=log,
        rule_hits=res.applied,
        audit=audit,
        warnings=warnings,
        missing_fields=missing,
        bib_keys_used=extract_bibkeys_from_body(body),
        expanded_inputs=expanded_inputs,
    )


def editorial_fields(meta: dict | None) -> dict[str, str]:
    """编辑侧**出版信息** → 模板字段。

    这些信息作者稿里没有、工具也推不出来。缺了它们的后果很显眼（09 Jiawei_Chu 实测）：
    每页页眉都是「逻辑学研究 xxxx 年第 x 期，1–10」，而人工成品是
    「逻辑学研究 2026 年第 5 期，137–152」——两份 PDF「看起来不一样」主要就是这个。

    支持的值：
      vol/issue/year   卷号、期号、出版年
      pages            `137-152`（起止页 → 自动算 mypages = 152-137+1 = 16，与人工成品一致）
                       或只给单页 `137`（此时 mypages 留空，编译后可据 PDF 页数补）
      received         收稿日期
      editor           责任编辑（只给姓名即可，会自动包成 `{\\bf （责任编辑：XXX）}`）
      grants           基金项目（走模板那行被注释的 `%\\renewcommand`，不碰 `\\newcommand`）
    """
    m = {k: (v or "").strip() for k, v in (meta or {}).items()}
    out: dict[str, str] = {}
    if m.get("vol"):
        out["myvolnumber"] = m["vol"]
    if m.get("issue"):
        out["myissnumber"] = m["issue"]
    if m.get("year"):
        out["mypubyear"] = m["year"]
    pg = m.get("pages", "")
    if pg:
        mm = re.fullmatch(r"\s*(\d+)\s*[-–—~]\s*(\d+)\s*", pg)
        if mm:
            first, last = int(mm.group(1)), int(mm.group(2))
            out["myfirstpage"] = str(first)
            out["mylastpage"] = str(last)
            out["mypages"] = str(last - first + 1)
        elif pg.strip().isdigit():
            out["myfirstpage"] = pg.strip()
    if m.get("received"):
        out["receiveddate"] = m["received"]
    if m.get("editor"):
        e = m["editor"]
        out["myeditor"] = e if "责任编辑" in e else "{\\bf （责任编辑：%s）}" % e
    if m.get("grants"):
        out["mygrants"] = m["grants"]
    return out


# ── 第四十六批②：LaTeX 稿「参考文献之后的内容」（附录/致谢）迁移 ────────────
# `sources.extract_body_text()` 会**截到参考文献之前**（第 121 条：\bibliographystyle
# 等要留在正文外）。副作用：作者把附录写在 `\printbibliography` 之后时，整段附录被
# 静默丢掉 —— 86-05 WeiNong_Li 实测丢了 3 个附录小节（人工 26 页 vs 生成 20 页，
# 正文文本只有人工的 66.7%）。这里把「参考文献命令之后到 \end{document}」的内容
# 取出来，插到模板自己的 `\printbibliography` 之后（表格/公式等原样保留）。
# 纪律：只在**确实有非注释内容**时生效；Word 通道与无附录稿逐字节不变。
_LATEX_BIB_MARKERS = (
    "\\printbibliography",
    "\\bibliographystyle",
    "\\bibliography{",
    "\\begin{thebibliography}",
)


def _active_find(text: str, needle: str, start: int = 0) -> int:
    r"""查找 `needle` 的**非注释行**出现位置（行首 `%` 视为注释；找不到返回 -1）。

    第四十六批回归（86-05 实测）：源稿在注释里贴了 biblatex 迁移示例
    （`%\bibliographystyle{…}` / `%\begin{thebibliography}` / `%\bibitem …`），
    旧实现 `body.find()` 命中注释里的 `\bibliographystyle` → 把「参考文献之后」的
    起点定在注释区，再被防御性 `\bibitem` 检查判空 → **附录整段不迁移**（页数回退）。
    这里只认非注释位置的标记。
    """
    pos = text.find(needle, start)
    while pos >= 0:
        line_start = text.rfind("\n", 0, pos) + 1
        if not text[line_start:pos].lstrip().startswith("%"):
            return pos
        pos = text.find(needle, pos + len(needle))
    return -1


def _latex_post_bibliography_tail(raw: str) -> str:
    r"""源稿 `.tex` 里「第一个参考文献命令**之后**」的内容（去注释/空行后非空才返回）。"""
    i = raw.find("\\begin{document}")
    body = raw[i + len("\\begin{document}"):] if i >= 0 else raw
    j = len(body)
    bib_env_end = -1
    for mk in _LATEX_BIB_MARKERS:
        k = _active_find(body, mk)
        if 0 <= k < j:
            j = k
            if mk == "\\begin{thebibliography}":
                bib_env_end = _active_find(body, "\\end{thebibliography}", k)
    if j < 0:
        return ""
    if bib_env_end >= 0:
        # 第四十六批回归（89-05 实测）：源稿用**手写 thebibliography**（15 个 \bibitem）。
        # 旧实现把它当参考文献「命令」→ 只去掉 begin 行、把 \bibitem 体整体迁进模板 →
        # 产物有 \bibitem 无环境 → `! Lonely \item`（47 错、rc 2）。
        # 修法：**整环境跳过**，只把 \end{thebibliography} 之后的内容当附录 tail；
        # 找不到 \end（环境不完整）→ 不迁移（保守，绝不搬 bibitem 体）。
        start = bib_env_end + len("\\end{thebibliography}")
        k_end = body.find("\\end{document}", start)
        tail = body[start:k_end] if k_end >= 0 else body[start:]
    else:
        k_end = body.find("\\end{document}", j)
        tail = body[j:k_end] if k_end >= 0 else body[j:]
        nl = tail.find("\n")                 # 去掉参考文献命令所在的那一行
        if nl < 0:
            return ""
        tail = tail[nl + 1:]
    # 4) 先去注释行，再做 \bibitem 防御（注释里的示例不能触发不迁移，86-05 实测）
    _lines = [ln for ln in tail.split("\n") if not ln.strip().startswith("%")]
    _active_tail = "\n".join(_lines)
    if _active_find(_active_tail, "\\bibitem") >= 0 or \
            _active_find(_active_tail, "\\begin{thebibliography}") >= 0:
        return ""
    # 第四十六批回归（88-04/87-1/88-05/89-02/89-06/89-07 + 89-05 实测）：
    # 作者交的「完整模板稿」在 \printbibliography 之后常跟着**模板自带尾部块**
    # （2.8 作者信息 / 2.9 编辑部信息，`\ifthenelse{\equal{\myarticletype}…}` /
    #  `\myfirstauthor`…），88-04 实测迁进产物后与模板尾部重复 → `\mytitleCN` /
    # `\printtitlepageCN` undefined、rc 0→2；89-05 的英文题名/摘要块同理（模板会
    # 自动印，迁了会重复）。这些都不是「附录」——extract_body_text 本来就该截掉。
    # 判据：**只有含 `\appendix` 的 tail 才是真附录**（86-05 实测为 `\appendix` 开头）。
    if _active_find(_active_tail, "\\appendix") < 0:
        return ""
    tail = _active_tail
    keep = [ln for ln in tail.split("\n")
            if ln.strip() and not ln.strip().startswith("%")]
    if sum(len(x) for x in keep) < 20:       # 只剩零星命令/空白 → 不迁移
        return ""
    # 附录段首的 `\clearpage`/`\newpage` 在模板里紧跟参考文献之后，会多印一页空白
    keep = [ln for ln in keep if ln.strip() not in ("\\clearpage", "\\newpage")]
    if sum(len(x) for x in keep) < 20:
        return ""
    return "\n".join(keep).strip("\n")


# ── 第四十八批④（dry-run，可选开关默认关）：algorithm 浮动体 → 可断页环境 ──
# 88-04 人工成品把源稿 3 个 `algorithm` 浮动体改成 `breakablealgorithm`（可跨页、
# 无浮动留白）；人工 tex 里自带该环境定义（不在模板/源稿里）。本开关 = 同一变换：
#   ① `\begin{algorithm}` / `\end{algorithm}` → `breakablealgorithm`；
#   ② 产物导言区注入人工同款环境定义（`\newenvironment{breakablealgorithm}`）。
# **默认关**：不改默认布局行为，等 Lead/用户拍板（dry-run 报告见批48）。
_BREAKABLE_ALGORITHM_DEF = r"""\makeatletter
\newenvironment{breakablealgorithm}
{% \begin{breakablealgorithm}
        \begin{center}
            \refstepcounter{algorithm}% New algorithm
            \hrule height.8pt depth0pt \kern2pt% \@fs@pre for \@fs@ruled
            \parskip 0pt
            \renewcommand{\caption}[2][\relax]{% Make a new \caption
                {\raggedright\textbf{\fname@algorithm~\thealgorithm} ##2\par}%
                \ifx\relax##1\relax % #1 is \relax
                \addcontentsline{loa}{algorithm}{\protect\numberline{\thealgorithm}##2}%
                \else % #1 is not \relax
                \addcontentsline{loa}{algorithm}{\protect\numberline{\thealgorithm}##1}%
                \fi
                \kern2pt\hrule\kern2pt
            }
        }
        {% \end{breakablealgorithm}
        \kern2pt\hrule\relax% \@fs@post for \@fs@ruled
    \end{center}
}
\makeatother
"""


def _to_breakable_algorithms(body: str) -> str:
    r"""把 `algorithm` 浮动体降为 `breakablealgorithm`（保留 caption/label/算法体）。"""
    body = re.sub(r"\\begin\{algorithm\}(\[[^\]]*\])?", r"\\begin{breakablealgorithm}", body)
    return body.replace("\\end{algorithm}", "\\end{breakablealgorithm}")


def _inject_breakable_algorithm_def(template_text: str) -> str:
    r"""导言区（`\begin{document}` 前）插入 breakablealgorithm 环境定义（只插一次）。"""
    if "newenvironment{breakablealgorithm}" in template_text:
        return template_text
    lines = template_text.split("\n")
    for idx, ln in enumerate(lines):
        if ln.strip().startswith("\\begin{document}"):
            lines.insert(idx, _BREAKABLE_ALGORITHM_DEF + "\n")
            return "\n".join(lines)
    return template_text


def _insert_post_bibliography(template_text: str, tail: str) -> str:
    r"""把附录内容插到模板 `\printbibliography` 之后（找不到就插在 `\end{document}` 前）。"""
    lines = template_text.split("\n")
    for idx, ln in enumerate(lines):
        if "\\printbibliography" in ln and not ln.strip().startswith("%"):
            # 与人工成品 tex 顺序一致：附录在参考文献**之前**（86-05 WeiNong_Li.tex 实测）
            lines[idx] = tail.strip("\n") + "\n\n" + ln
            return "\n".join(lines)
    j = template_text.find("\\end{document}")
    if j >= 0:
        return template_text[:j] + "\n" + tail.strip("\n") + "\n" + template_text[j:]
    return template_text.rstrip("\n") + "\n" + tail.strip("\n") + "\n"


# ── 第 76 条：英文元数据第二源用到的字段（顺序 = 日志与合并顺序）──────────────
# 只列**英文字段**：第二源是「英文摘要」docx，中文那套仍旧只认正文 docx，
# 免得把另一份文件的表述混进中文题名/摘要。
EN_SECOND_SOURCE_FIELDS = ("title_en", "subtitle_en", "abstract_en",
                           "author_en", "affiliation_en", "keywords_en")
_EN_FIELD_CN = {
    "title_en": "英文题名", "subtitle_en": "英文副题名", "abstract_en": "英文摘要",
    "author_en": "作者英文名", "affiliation_en": "英文单位", "keywords_en": "英文关键词",
}


def _merge_en_metadata_source(
    doc,
    docx_path: str,
    metadata_path: str | None,
    say,
    warnings: list[str],
) -> list[str]:
    """用第二源（同目录「英文摘要」docx）补正文 docx 里**为空**的英文字段。

    返回实际补入的字段名（空列表 = 没动任何东西）。三条纪律：

    ① **只补空值**：正文 docx 里已有的英文真值（案例 52 的文末英文块）一个字符都不动。
       所有英文字段都已解析出来时**连第二源都不读**。
    ② **判据共用**：文件是不是「英文元数据表」由 `sources.en_metadata_sheet()` 判
       ——它同时排除了纯中文稿（英文字段全空）与同目录的另一篇英文全文稿（有叙述正文）。
       纯中文的第二源 → 英文字段一个都拿不到 → 不补，绝不把中文摘要当英文摘要。
    ③ **不猜**：`metadata_path` 由调用方给出，本函数不搜索任何目录；
       路径不存在/不是 .docx/解析不出英文字段 → 记一条 warning 后原样退出
       （照旧写占位符）。
    """
    from .sources import en_metadata_sheet

    if not metadata_path:
        return []
    if os.path.abspath(metadata_path) == os.path.abspath(docx_path):
        return []

    # ① 先看正文缺什么：一个都不缺 → 第二源连读都不读（第 52 条反例：不许动真值）
    missing = [f for f in EN_SECOND_SOURCE_FIELDS
               if not (getattr(doc, f, "") or "").strip()]
    if not missing:
        return []

    name = os.path.basename(metadata_path)
    if not os.path.exists(metadata_path):
        warnings.append(f"英文元数据第二源不存在，本次忽略：{metadata_path}")
        return []
    if os.path.splitext(metadata_path)[1].lower() != ".docx":
        say(f"   第二源     {name}（不是 .docx，Word 通道不解析，本次忽略）")
        warnings.append(f"英文元数据第二源不是 .docx，本次忽略：{metadata_path}")
        return []

    fields = en_metadata_sheet(metadata_path)
    if not fields:
        say(f"   第二源     {name}（无英文题名/摘要/关键词，本次忽略）")
        warnings.append(
            f"英文元数据第二源里没有可用的英文字段（只有中文？），本次忽略：{name}"
        )
        return []

    filled: list[str] = []
    for f in EN_SECOND_SOURCE_FIELDS:
        v = fields.get(f)
        if not v or not v.strip():
            continue
        if (getattr(doc, f, "") or "").strip():
            continue        # 正文自己已有真值 → 一个字都不动（最重要的反例）
        setattr(doc, f, v)
        filled.append(f)

    have = "、".join(_EN_FIELD_CN.get(f, f) for f in fields)
    say(f"   第二源     {name}（英文元数据表，含：{have}）")
    if filled:
        say(f"   英文元数据补入 {len(filled)} 项："
            + "、".join(_EN_FIELD_CN.get(f, f) for f in filled))
    else:
        say("   正文自带的英文块优先，第二源未改动任何字段")
    return filled


# ── 一校「可自动改」子集接进初排（期 87 P1-6）────────────────────────────
#
# 为什么需要：一校核查（`slcn/proofcheck.py`）把「顿号后紧跟引号」「中文之间的半角
# 逗号/句号/冒号/分号/问叹号」标成 fixable，但**只在一校 --fix 时**才动手；期 87 生成稿
# 实测仍残留 20 处（ZH-06×19 + ZH-01×1），全库初排稿（Word 通道）残留 123 处/29 篇，
# 人工成品这些位置逐处为 0 —— 同一判据在工具内两处实现不一致（笔记仓案例 142 / P1-6）。
#
# 口径（Lead 批准）：**只用窄集 ZH-01..ZH-06**。ZH-08（连接号）、ZH-09/14、EN-01..03
# 本批没有消融与人工对照，不得默认改产物；rules-docs 的宽常量 `INITIAL_LAYOUT_FIXABLE`
# 仅供后批参考，这里**不** import 使用。
#
# 实现刻意只用 `proofcheck` 的**稳定两参数 API**（`check()` → 过滤 `Report` →
# `apply_fixes(tex, sub)`），不传 rules-docs 新增的 `rule_ids` 参数 → 回滚免疫。
_PROOF_AUTOFIX_IDS: tuple[str, ...] = ("ZH-01", "ZH-02", "ZH-03", "ZH-04", "ZH-05", "ZH-06")


# ── ZH-08 连接号：只提示、不自动分类（第四十三批）────────────────────────
#
# 用户 2026-10-22 拍板：引擎**无法可靠区分**「人名内部短横」（让-雅克·卢梭，应写
# `\edash`）与「地名/路线/区间的一字线」（北京-上海，应写一字线）——那需要语义/知识库，
# 正则与词表都会错。所以 ZH-08 **不做默认自动改**（E1 白名单也不含它），改为在初排
# 日志里给「可发现性提示」：命中数 + 前 20 条上下文 + 判断指引，交人工编辑逐条判断。
# ⚠️ 只写日志，**产物逐字节不变**（本模块所有 ZH-08 代码都不修改 tpl）。
_DASH_GUIDE = ("人名 / 编号 / 日期 / 字母序号 → 短横 \\edash；"
               "地名 / 路线 / 区间等 → 一字线（——）")


def _dash_hints(tex: str, lang: str) -> list[tuple[int, str]]:
    """收集 ZH-08（中文旁的半角短杠）命中，返回 `[(行号, 上下文)]`；**只读不改文本**。

    屏蔽区（数学/命令/注释/verbatim）由 `proofcheck.check()` 的三层掩码保证，
    这里不复刻判据（同一判据只有一份，见 INVARIANTS 四.1）。
    上下文 = 命中前后各 10 字（换行显示为 ⏎），便于日志里一眼定位。
    """
    from . import proofcheck as _pc   # 惰性 import（无环；与前两个 helper 同风格）

    rep = _pc.check(tex, lang=lang)
    out: list[tuple[int, str]] = []
    for h in rep.hits:
        if h.rule_id != "ZH-08":
            continue
        a, b = max(0, h.start - 10), min(len(tex), h.end + 10)
        out.append((h.line, tex[a:b].replace("\n", "⏎")))
    return out


def _report_dash_hints(tex: str, lang: str, say, limit: int = 20) -> int:
    """把 ZH-08 提示写进初排日志（无命中不打印），返回命中数。"""
    hints = _dash_hints(tex, lang)
    if not hints:
        return 0
    say(f"   一校提示：ZH-08 连接号待人工判断 {len(hints)} 处"
        "（只提示，未改任何字符）")
    say(f"   判断指引：{_DASH_GUIDE}")
    for line, ctx in hints[:limit]:
        say(f"   · 第{line}行：{ctx}")
    if len(hints) > limit:
        say(f"   · ……另有 {len(hints) - limit} 处（可用 proofcheck.py 看全部）")
    return len(hints)



# ── 第四十四批④：B 类可发现性提示（初排阶段：bib>0 且正文 0 引用）─────────
# 用户 2026-10-18 已同意；**纯日志，产物逐字节不变**（同 ZH-08 提示的纪律）。
# 判据刻意**不猜**「正文编号 ↔ 文献条目」映射（那需要语义/知识库，必错），只报事实：
# biblatex 只打印**被引用**的条目 → bib 再全、正文 0 引用时 PDF 参考文献区必空。
# 触发样例（O10）：90-04/90-08/87-5/87-7（源稿正文编号引用 ↔ 无编号清单体例）；
# 反例：90-06（脚注体例，正文有 30 处引用 → 不提示）、空 bib、数学区间内的反斜杠。
_BIB_ZERO_CITES_GUIDE = (
    "不自动猜「正文编号 ↔ 文献条目」映射；请人工补 \\parencite{键}，"
    "或核对源稿体例（编号引用 / 人年制）"
)

_COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")
_VERBATIM_RE = re.compile(
    r"\\begin\{((?:verbatim|lstlisting|minted|Verbatim)\*?)\}.*?\\end\{\1\}", re.S)
_MATH_ENV_RE = re.compile(
    r"\\begin\{((?:equation|align|gather|multline|eqnarray|displaymath|math)\*?)\}"
    r".*?\\end\{\1\}", re.S)
_MATH_DELIM_RE = re.compile(r"\$[^$\n]*\$|\\\[.*?\\\]|\\\(.*?\\\)", re.S)
_CITE_CMD_RE = re.compile(
    r"\\(?:parencite|Parencite|textcite|Textcite|footcite|footfullcite|Footcite|"
    r"autocite|Autocite|smartcite|supercite|cite|citep|citet|Cite|Citep|Citet|nocite)"
    r"\*?\s*(?:\[[^\]]*\]\s*)*\{"
)


def _count_cite_commands(tex: str) -> int:
    r"""数**正文**里的引用命令（`\parencite/\cite/\footcite/\nocite` 等）。

    注释 / verbatim / 数学区间先掩码：判据只认正文里真正的引用命令，
    不把「注释掉的样例」「数学区间里的反斜杠」「preamble 定义」当成引用。
    （`\nocite{*}` 也算「有引用」——它让 biblatex 打印全部条目，参考文献区不空。）
    """
    body = tex.split("\\begin{document}", 1)[-1]
    s = _COMMENT_RE.sub("", body)
    for pat in (_VERBATIM_RE, _MATH_ENV_RE, _MATH_DELIM_RE):
        s = pat.sub("", s)
    return len(_CITE_CMD_RE.findall(s))


# 正文里「裸编号引用」`[1]` / `[2-4]` / `[2,3]`（前面不能是反斜杠，避免误吃
# `\\[2pt]`、`\\[` 数学等 LaTeX 可选参数）。NO_CITES A/B 共用。
_NUMBER_CITE_RE = re.compile(r"(?<!\\)\[(\d{1,3})(?:\s*[-–—~,，]\s*(\d{1,3}))*\]")


def _number_cite_stats(tex: str) -> tuple[int, int, list[str]]:
    """正文裸编号引用统计：返回 (出现次数, 最大编号, 前 5 个示例文本)。"""
    body = tex.split("\\begin{document}", 1)[-1]
    s = _COMMENT_RE.sub("", body)
    for pat in (_VERBATIM_RE, _MATH_ENV_RE, _MATH_DELIM_RE):
        s = pat.sub("", s)
    found = _NUMBER_CITE_RE.findall(s)
    nums: list[int] = []
    for m in _NUMBER_CITE_RE.finditer(s):
        nums.append(int(m.group(1)))
        if m.group(2):
            nums.append(int(m.group(2)))
    examples = [m.group(0) for m in list(_NUMBER_CITE_RE.finditer(s))[:5]]
    return len(found), (max(nums) if nums else 0), examples


def _map_number_citations(
        body: str, ref_key_by_num: dict[int, str]) -> tuple[str, int, list[str]]:
    r"""NO_CITES-B（第四十九批，`--number-cite-map` 默认关）：裸编号引用 `[N]` → `\parencite`。

    只在「**正文只有 [N]、没有真引用命令、文末清单编号 1..N 有序**」时映射；任何一条
    不满足都**原样返回** + warning（越界/混用真引用/清单无序/编号缺条→不猜）。
    返回 (新正文, 映射处数, warnings)。纯文本替换，不改其它内容。
    """
    if not ref_key_by_num:
        return body, 0, ["编号引用映射跳过：源稿没有可映射的编号清单"]
    keys = sorted(ref_key_by_num)
    if keys != list(range(1, len(keys) + 1)):
        return body, 0, ["编号引用映射跳过：文末清单编号不是 1..N 有序（不映射）"]
    if _CITE_CMD_RE.search(body):
        return body, 0, ["编号引用映射跳过：正文已含真引用命令（混用不映射，避免二次包裹）"]
    matches = list(_NUMBER_CITE_RE.finditer(body))
    if not matches:
        return body, 0, []
    bad: list[str] = []
    for m in matches:
        if int(m.group(1)) not in ref_key_by_num:
            bad.append(m.group(0))
        if m.group(2) and int(m.group(2)) not in ref_key_by_num:
            bad.append(m.group(0))
    if bad:
        return body, 0, [
            f"编号引用映射跳过：{len(bad)} 处 [N] 越界/清单缺条"
            f"（示例：{'、'.join(bad[:3])}）"
        ]
    n_mapped = [0]

    def _repl(m: re.Match) -> str:
        n1 = int(m.group(1))
        n2 = int(m.group(2)) if m.group(2) else None
        nums = list(range(n1, n2 + 1)) if n2 is not None and n2 > n1 else [n1]
        if n2 is not None and n2 < n1:
            nums = [n1, n2]
        n_mapped[0] += 1
        ks = ",".join(ref_key_by_num[n] for n in nums)
        return f"\\parencite{{{ks}}}"

    new_body = _NUMBER_CITE_RE.sub(_repl, body)
    return new_body, n_mapped[0], [
        f"编号引用映射（--number-cite-map）：{n_mapped[0]} 处 [N] → \\parencite"
        "（第 N 条对应文末清单；请人工核对编号体例）"
    ]


def _bib_zero_cites_hint(tex: str, n_bib: int,
                         n_end_list: int = 0) -> list[str] | None:
    r"""B 类判据：生成稿 bib 条数>0 且正文引用命令数=0 → 醒目提示行；否则 None。

    第四十九批 NO_CITES-A：提示里加「正文裸编号 [N]」统计（出现次数/最大编号/
    前几个示例）与文末清单条数，让编辑一眼看出「引用其实是 [N] 编号体例」还是
    「真的一条引用都没有」。**纯日志，产物逐字节不变**。
    """
    if n_bib <= 0:
        return None
    if _count_cite_commands(tex) > 0:
        return None
    n_num, max_num, examples = _number_cite_stats(tex)
    lines = [
        f"⚠️ [B 类提示] 生成稿 bib 有 {n_bib} 条，但正文 0 处 \\parencite/\\cite"
        " → PDF 参考文献区会是空的",
    ]
    if n_num:
        lines.append(
            f"   正文裸编号引用 [N]：{n_num} 处，最大编号 [{max_num}]，"
            f"示例：{'、'.join(examples)}"
        )
    else:
        lines.append("   正文未发现裸编号引用 [N]")
    if n_end_list:
        lines.append(f"   文末清单条数：{n_end_list} 条")
    lines.append(
        f"   处理建议：{_BIB_ZERO_CITES_GUIDE}（提示可用 bib_zero_cites_hints=False 关闭）"
    )
    return lines


def _report_bib_zero_cites(tex: str, n_bib: int, say, warnings: list[str],
                           log: list[str], n_end_list: int = 0) -> bool:
    """把 B 类提示写进初排日志**顶部**与 warnings 首位；只读 tex，不改产物。"""
    lines = _bib_zero_cites_hint(tex, n_bib, n_end_list=n_end_list)
    if not lines:
        return False
    for ln in lines:
        say(ln)
    # 置顶：编辑器先看到的应该是这条（同 ZH-08 提示只写日志的纪律）
    for ln in reversed(lines):
        try:
            log.remove(ln)
        except ValueError:
            pass
        log.insert(0, ln)
    warnings.insert(0, lines[0])
    return True


def _autofix_proof(tex: str, lang: str) -> tuple[str, dict[str, int]]:
    """把一校 fixable 窄集套到初排稿上，返回 (新文本, {规则 id: 改动数})。

    ⚠️ 只动 `_PROOF_AUTOFIX_IDS`；`apply_fixes` 内部从后往前替换、与已改区间重叠的跳过，
       输入字符串不被就地修改。异常由调用方兜住（本函数不静默吞错）。
    """
    from . import proofcheck as _pc   # 惰性 import：proofcheck 只依赖 re/dataclass，无环

    rep = _pc.check(tex, lang=lang)
    sub = _pc.Report(
        hits=[h for h in rep.hits if h.rule_id in _PROOF_AUTOFIX_IDS], lang=lang
    )
    new, applied = _pc.apply_fixes(tex, sub)
    hits: dict[str, int] = {}
    for h in applied:
        hits[h.rule_id] = hits.get(h.rule_id, 0) + 1
    return new, hits


# ── 图表交叉引用接进初排（期 87 P2-2）──────────────────────────────────────
#
# Word 稿正文写的是纯文本「表1」「图1」；表格/图片渲染时才产出 `\caption` + `\label`
# （`docxsource.table_env` / `figure_env`）。人工成品会把正文改成 `表~\ref{tb1}` 这种
# 交叉引用（期 87 子案例 2 人工 11 处、生成 0 处 → 一校 ZH-16 自然不触发，见笔记仓
# 期87 报告 §P2-2）。这里在写出前做一次**保守**替换：
#   · 只在**存在对应 `\label{tab:N}`/`\label{fig:N}`** 时才替换 → 绝不产生 undefined reference；
#   · 用 `proofcheck.visible_mask` 屏蔽数学/注释/verbatim/命令名与键参数（等长掩码，坐标可直接落原 tex）；
#   · 行首题注「表N：/图N：」（Word 题注段落残留，如期 87 sub2 两处）**不碰**；
#   · 已写成 `表~\ref{…}`/`表\ref{…}` 的跳过（幂等）；
#   · 「式(N)」不做：Word 通道行内公式无 equation 编号环境（另批）。
_XREF_LABEL_RE = re.compile(r"\\label\{(tab|fig):(\d+)\}")
_XREF_CAPTION_LINE_RE = re.compile(r"(?m)^[ \t]*[表图][ \t]{0,3}\d+[ \t]{0,3}[：:]")
_XREF_NUM_RE = re.compile(r"([表图])[ \t]{0,3}(\d+)")
_XREF_KIND = {"表": "tab", "图": "fig"}


def _add_xrefs(tex: str) -> tuple[str, int, int]:
    """正文「表N/图N」→「表~\\\\ref{tab:N}」「图~\\\\ref{fig:N}」，返回 (新文本, 表数, 图数)。

    没有 label 的稿（LaTeX 通道、无图表 Word 稿）→ 原样返回，逐字节不变。
    """
    from .proofcheck import visible_mask   # 只读 import（rules-docs 冻结的公开函数）

    labels: dict[str, set[str]] = {"tab": set(), "fig": set()}
    for m in _XREF_LABEL_RE.finditer(tex):
        labels[m.group(1)].add(m.group(2))
    if not labels["tab"] and not labels["fig"]:
        return tex, 0, 0

    masked = visible_mask(tex)
    # 行首题注也挖掉（等长替换成空格；坐标仍与 tex 一一对应）
    for m in _XREF_CAPTION_LINE_RE.finditer(masked):
        masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]

    todo: list[tuple[int, int, str]] = []
    n_tab = n_fig = 0
    for m in _XREF_NUM_RE.finditer(masked):
        kind = _XREF_KIND[m.group(1)]
        num = m.group(2)
        if num not in labels[kind]:
            continue
        tail = tex[m.end():m.end() + 8].lstrip(" \t")
        if tail.startswith("~\\ref") or tail.startswith("\\ref"):
            continue                      # 幂等：已经是交叉引用
        todo.append((m.start(), m.end(), f"{m.group(1)}~\\ref{{{kind}:{num}}}"))
        if kind == "tab":
            n_tab += 1
        else:
            n_fig += 1

    out = tex
    for start, end, repl in reversed(todo):
        out = out[:start] + repl + out[end:]
    return out, n_tab, n_fig


def build_docx(
    docx_path: str,
    out_dir: str,
    *,
    basename: str = "output",
    lang: str = "zh",
    sty: str | None = None,
    skeleton: str | None = None,
    quote_threshold: int = 105,
    meta: dict | None = None,
    overrides: dict | None = None,
    metadata_path: str | None = None,
    verbose: bool = True,
    proof_autofix: bool = True,
    dash_hints: bool = True,
    bib_zero_cites_hints: bool = True,
    end_list_priority: bool = False,
    number_cite_map: bool = False,
    c_class_union: bool = False,
) -> BuildResult:
    """Word 通道：.docx → 初排稿。模板选择规则同 build_latex。

    quote_threshold 引文超过多少字才提升为 quotation 环境（默认 105 ≈ 三行）。
    overrides       手工覆盖 {title_cn, author_cn, affiliation_cn, email,
                    abstract_cn, title_en, abstract_en}；**非空值优先于解析结果**
                    （源稿里读不到时代编辑填，如 --title-cn），被覆盖的字段不再算
                    「未识别」。None / 空 dict = 与从前逐字一致。
    metadata_path   **英文元数据第二源**（可选，第 76 条）：同目录那份「英文摘要」
                    docx。正文 docx 自身读不出英文题名/摘要/作者时，用它的英文字段补；
                    正文**已有**的英文真值一个字符都不动（案例 52 的文末英文块）。
                    ⚠️ **本函数不做任何文件搜索**——路径由调用方给出（`convert.py`
                    按「仅同目录」口径发现，与 `find_bib()` 同一纪律）。
                    默认 None = 不接第二源 = 与从前逐字节相同（`corpus.py` /
                    `run_poc.py` 等既有调用方不带这个参数，行为不变）。
    proof_autofix   **一校可自动改子集接进初排**（期 87 P1-6）：True（默认）= 生成后
                    自动套用 `slcn.proofcheck` 里 ZH-01..ZH-06 这 6 条确定性替换；
                    False = 与接入前逐字节相同（`corpus.py --no-proof-autofix` 消融用）。
                    只影响中文 Word 通道；LaTeX 通道与英文稿不受影响。
    dash_hints       **ZH-08 连接号待人工判断**的日志提示（第四十三批，默认开）：
                    True = 命中数 + 前 20 条上下文（前后各 10 字）+ 判断指引；
                    False = 不打印。**纯日志，产物逐字节不变**——引擎不做语义自动分类
                    （人名短横 \\edash vs 地名/路线一字线，正则/词表都不可靠）。
    bib_zero_cites_hints  **B 类可发现性提示**（第四十四批④，默认开）：
                   bib 条数>0 而正文 `\\parencite/\\cite` 数=0 时（biblatex 只打印被引
                   条目 → PDF 参考文献区会是空的），在初排日志**置顶**提示 + 计数，
                   供编辑人工补引用/核对体例。**纯日志，产物逐字节不变**；False=不打印。
    end_list_priority  **文末清单优先（C 类可选开关，第四十六批；默认 False=关）**：
                   True 且源稿存在文末清单（人年制/编号清单）时，脚注里的文献条目
                   **不再逐条提升**为 bib（只保留编号清单键对应的条目）；默认关时
                   与现行为逐字节一致（`footnotes.footnote_promotion_allowed` 恒 True）。
                   ⚠️ 启用需与 docxsource 的脚注渲染配套（避免悬空 `\\parencite`），
                   该配套在批 47 处理；本参数只接线、不改默认。
    number_cite_map  **NO_CITES-B 编号引用映射（第四十九批；默认 False=关）**：
                   True 且「正文只有裸编号 [N]、无真引用命令、文末清单编号 1..N
                   有序」时，把 [N] 映射成 `\\parencite{第N条键}`；越界/混用/无序
                   一律不映射并 warning。默认关 → 产物逐字节不变。
    c_class_union   **C 类新口径开关（第四十九批；默认 False=关）**：
                   True 时按用户拍板新口径渲染：pure_citation/reference_entry →
                   正文 `\\parencite[页码]{键}` 并删脚注；mixed → 保留脚注 + 「请人工
                   核对」warning 列表；note_only 原样；参考文献 = 脚注引注 ∪ 文末清单
                   （bib-refs `union_entries`）。**默认关**（v7 实测非 5 篇过并：
                   87-9/88-02 等 bib 大幅缩水+undef/dangling，需先修指纹/键一致性）；
                   关时旧行为逐字节不变；`--c-class-union` 显式 opt-in。
    """
    from .docxsource import (
        link_citations,
        link_numbered_citations,
        parse_docx,
        refs_to_bib,
    )

    # 第四十九批②：C 类新口径**默认 OFF**（v7 实测非 5 篇过并，暂不默认开启）；
    # `--c-class-union` 显式 opt-in。

    log: list[str] = []
    warnings: list[str] = []

    def say(s: str) -> None:
        log.append(s)
        if verbose:
            print("   " + s)

    say("① 解析 Word 文稿")
    doc = parse_docx(docx_path, quote_threshold=quote_threshold,
                     c_class_union=c_class_union)
    warnings.extend(doc.warnings)
    # ── 第 76 条：英文元数据第二源（同目录的「英文摘要」docx）──
    # 放在「题名/作者」那几行日志之前，日志读起来就是「这次英文题名是从哪来的」。
    _merge_en_metadata_source(doc, docx_path, metadata_path, say, warnings)
    say(f"   题名《{doc.title}》 作者 {doc.author}")
    say(f"   段落正文 {len(doc.body)} 字符；章节 {len(doc.headings)} 个；公式 {doc.n_math} 处")
    # 参考文献有两条通道：文末**人年制**（doc.refs）与 编号清单/脚注（doc.bib_entries）。
    # 只报 doc.refs 会让「编号清单」型稿件显示成「参考文献 0 条」——实测 09 Jiawei_Chu
    # 明明解析出 18 条却这样打印，编辑会以为又丢了。
    if doc.bib_entries and not doc.refs:
        say(f"   参考文献 {len(doc.bib_entries)} 条（文末编号清单/脚注体例）")
    else:
        say(f"   参考文献 {len(doc.refs)} 条"
            + (f" + 编号清单/脚注 {len(doc.bib_entries)} 条" if doc.bib_entries else ""))
    # 第四十六批（C 类可选开关，默认关）：文末清单优先——脚注文献不再逐条提升。
    # 判据与过滤在 `slcn/footnotes.py`（bib-refs 实现）；默认 False 时
    # `footnote_promotion_allowed()` 恒 True → 本块不执行，产物逐字节不变。
    if end_list_priority and not c_class_union:
        from .footnotes import filter_footnote_entries, footnote_promotion_allowed

        _has_end_list = bool(doc.refs or doc.ref_raw_lines or doc.ref_key_by_num)
        if not footnote_promotion_allowed(
                has_end_list=_has_end_list, end_list_priority=end_list_priority):
            _keep = set(doc.ref_key_by_num.values())
            _before = len(doc.bib_entries)
            doc.bib_entries = filter_footnote_entries(doc.bib_entries, _keep)
            _dropped = _before - len(doc.bib_entries)
            if _dropped:
                say(f"   文末清单优先：脚注文献 {_before} 条 → 保留 "
                    f"{len(doc.bib_entries)} 条（过滤 {_dropped} 条）")
                warnings.append(
                    f"文末清单优先（--end-list-priority）：脚注文献 {_before} 条中"
                    f"过滤 {_dropped} 条，只保留与文末清单编号对应的条目")

    # 第四十九批（C 类新口径）：混合型脚注（说明文字+引用）→ 「请人工核对」清单。
    # **纯日志/warning，不改产物**。渲染已在 docxsource 保留 \footnote。
    _mixed = getattr(doc, "mixed_footnotes", None) or [] if c_class_union else []
    _cc_warns = (getattr(doc, "c_class_warnings", None) or []) if c_class_union else []
    _cc_unmatched = [w for w in _cc_warns if getattr(w, "code", "") == "citation_unmatched"]
    _cc_conflict = [w for w in _cc_warns if getattr(w, "code", "") == "field_conflict"]
    if _cc_unmatched:
        _u = "；".join(
            f"#{getattr(w, 'fid', '?')}「{str(getattr(w, 'text', ''))[:40]}」"
            for w in _cc_unmatched[:6])
        warnings.append(
            f"纯引用脚注 {len(_cc_unmatched)} 条无法匹配完整条目（不编造，脚注原文已保留）："
            f"{_u}" + ("…" if len(_cc_unmatched) > 6 else ""))
    if _cc_conflict:
        warnings.append(
            f"并集合并时 {len(_cc_conflict)} 条文献字段冲突（已按文末清单优先，请人工核对）："
            + "；".join(str(getattr(w, "text", ""))[:40] for w in _cc_conflict[:4]))
    if _mixed:
        _head = "；".join(f"#{fid}「{snippet}」" for fid, snippet in _mixed[:6])
        warnings.append(
            f"混合型脚注 {len(_mixed)} 条（说明文字+引用，已保留脚注）：请人工核对"
            f"引用与说明文字是否对应——{_head}"
            + ("…" if len(_mixed) > 6 else "")
        )
        say(f"   混合型脚注 {len(_mixed)} 条：已保留脚注，warning 列「请人工核对」清单")

    if doc.n_math:
        # ⚠️ 这条以前写的是「PoC 尚未转换」——**早就过时了**，公式其实已经转好
        # （OMML → LaTeX 数学环境，见 docxxml.omml_to_latex）。留着会让编辑误以为
        # 公式没转，白白人工返工。改成如实告知，并且**不再放进 warnings**
        # （它不是需要注意的问题）。
        say(f"   OMML 公式 {doc.n_math} 处已转为 LaTeX 数学环境")
    if not doc.refs and not doc.bib_entries:
        warnings.append("未解析出参考文献（文末与脚注都没有），引用将无法替换")

    # 第 80 条：「正文里没转成 cite 的引用点」= [(引用原文, 段落序号, 段首片段), ...]。
    # 只有人年制通道会产生它；编号清单/脚注通道按构造是空的（引用在渲染时就地生成）。
    unmatched_points: list[tuple[str, int, str]] = []
    if doc.bib_entries:
        # 参考文献来自**文末编号清单或脚注**：引用已在渲染正文时就地生成（见
        # docxsource.render_para），不能再跑一遍人年制替换，否则会再包一层。
        body = doc.body
        # 但**文末编号清单**那一路例外：正文里还是纯文本 `（[18]，第42页）`，
        # 需要换成 `\parencite`（中文定位写在括号里、英文写进命令的可选参数）。
        if doc.ref_key_by_num:
            body, n_num, num_issues = link_numbered_citations(
                body, doc.ref_key_by_num, lang
            )
            say(f"②.5 正文纯文本引用 → \\parencite：{n_num} 处")
            warnings.extend(num_issues)
        n_cite = body.count("\\parencite")
        say(f"② 引用已完成：文末编号清单/脚注文献生成 {len(doc.bib_entries)} 个 bib 条目，"
            f"正文含 {n_cite} 处 \\parencite")
        if n_cite == 0:
            # biblatex 只打印**被引用**的条目，所以 bib 再全，PDF 的参考文献区也是空的。
            # 实测 09 Jiawei_Chu 就是这样：编辑器会以为「参考文献又丢了」。
            # 不自动加 `\nocite{*}`：全库 261 篇人工成品都是显式引用，**0 篇**用 \nocite。
            warnings.append(
                f"bib 里有 {len(doc.bib_entries)} 条，但正文没有引用命令 → "
                "PDF 的参考文献区会是空的；请按人工惯例在正文补 \\parencite{键}"
            )
    else:
        say("② 正文人年制引用 → \\parencite")
        body, unmatched, citelogs = link_citations(doc.body, doc.refs)
        for s in citelogs:
            say("   " + s)
        # 第 80 条：`unmatched` 从前只进 stdout/GUI 日志（还会被界面 400k 上限挤掉），
        # 现在就地定位到源稿段落（`doc.body` 就在手上），随报告一起落盘。
        unmatched_points = _citation_points(doc.body, unmatched)
        if unmatched:
            warnings.append(f"{len(unmatched)} 处引用未能匹配到参考文献：" + "、".join(unmatched[:6]))

    # 第四十四批④：B 类可发现性提示（初排阶段可见；纯日志、产物逐字节不变）
    if bib_zero_cites_hints:
        # Word 通道没有 bib_path（bib 由 doc.refs + doc.bib_entries 生成）
        _n_bib_tex = len(doc.refs) + len(doc.bib_entries)
        # 第四十九批 NO_CITES-B（默认关）：裸编号引用 [N] → \parencite（不猜编号）
        if number_cite_map:
            try:
                body, _n_mapped, _map_warnings = _map_number_citations(
                    body, doc.ref_key_by_num)
                warnings.extend(_map_warnings)
                if _n_mapped:
                    say(f"②.6 编号引用映射（--number-cite-map）：{_n_mapped} 处 [N] → \\parencite")
            except Exception as e:  # noqa: BLE001 —— 映射失败不阻断出稿
                warnings.append(f"编号引用映射未执行（不影响出稿）：{type(e).__name__}: {e}")
        _n_end_list = (len(doc.refs) if doc.refs
                       else len(doc.ref_key_by_num))
        try:
            _report_bib_zero_cites(body, _n_bib_tex or (len(doc.refs) + len(doc.bib_entries)),
                                   say, warnings, log, n_end_list=_n_end_list)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"B 类提示未执行（不影响出稿）：{type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"B 类提示未执行（不影响出稿）：{type(e).__name__}: {e}")

    say("③ 载入模板并填入元数据（模板用户可选）")
    tpl, tpl_sel = _prepare_template(lang, sty, skeleton, say)
    lang = tpl_sel.lang
    # ⚠️ 不要在这里把空格全删掉：`寇静茹 刘叶涛` 会被压成 `寇静茹刘叶涛`（实测 2026_05_Jingru_Kou）。
    #    需要「无空格」形态的地方（键、目录名）自己处理，写进模板的必须是原样姓名。
    author = doc.author.strip()

    # 中英文稿的字段布局**完全不同**，必须按模板类型分别填：
    #   CN 模板（含 mytitleEN）：中文为主，另有 myabstractEN / mykeywords
    #   EN 模板（含 mytitleCN）：英文为主，另有 myabstractCN，且**没有关键词字段**
    # 填错字段会让摘要跑到正文里，实测把 02 Lu_Jiang 的内容准确率压到 8.7%。
    tpl_fields = set(parse_macros(tpl))
    is_en = "mytitleCN" in tpl_fields
    # 模板自带的示例文字（`第一作者`/`单位1（到系所）`/`xxxx@xxxx.xxx`/`Abstract`…）：
    # 解析出来的值若与它逐字相同，说明这字段没人填过，按「未识别」处理。
    tpl_defaults = template_defaults(tpl)

    # 题名带副标题时，人工成品是**并进同一个字段并用 `\\` 断行**的：
    #   \mytitle{论证行动如何生成？\\——一个基于广义论证理论的生成模型}
    # 页眉用的 myrunningtitle 则保持一行（不带 `\\`）。
    def _with_sub(title: str, sub: str) -> str:
        return f"{title}\\\\{sub}" if title and sub else title

    if is_en:
        fields = {
            "mytitle": _with_sub(doc.title_en or doc.title, doc.subtitle_en),
            "mytitleCN": _with_sub(doc.title_cn, doc.subtitle),
            "myrunningtitle": doc.title_en or doc.title or doc.title_cn,
            "myabstract": doc.abstract_en or doc.abstract_cn,
            "myabstractCN": doc.abstract_cn,
        }
    else:
        fields = {
            "mytitle": _with_sub(doc.title_cn or doc.title, doc.subtitle),
            "mytitleEN": _with_sub(doc.title_en, doc.subtitle_en),
            "myrunningtitle": doc.title_cn or doc.title,
            "myabstract": doc.abstract_cn or doc.abstract_en,
            "myabstractEN": doc.abstract_en,
            "mykeywords": doc.keywords_cn or doc.keywords_en,
        }
    # 多作者：人工成品的写法是「第一作者 → \myfirstauthor，其余 → \mysecondauthor…」
    # （实测 2026_05_Jingru_Kou：`{\kaishu 寇静茹}` + `\renewcommand{\mysecondauthor}{\kaishu 刘叶涛}`）。
    # 只在**纯中文姓名**时拆分——`Jiawei Chu` 这种英文名拆开就错了。
    fields.update(author_fields(author))
    # 文末中英元数据块里常同时有英文作者、单位、邮箱（09 Jiawei_Chu 就是）
    fields["myfirstauthorEN"] = normalize_english_author(doc.author_en, doc.author)
    fields["myfirstaffiliation"] = doc.affiliation_cn
    fields["myfirstaffiliationEN"] = doc.affiliation_en
    fields["myfirstemail"] = doc.email

    # ── 手工覆盖：非空值优先于解析结果（--title-cn / 界面输入，见 override_fields）──
    ov, unknown_keys = override_fields(overrides, is_en)
    if unknown_keys:
        warnings.append("overrides 里有未识别的键（本次忽略）：" + "、".join(unknown_keys))
    dropped = [k for k in ov if k not in tpl_fields]
    if dropped:
        warnings.append("手工覆盖对应的模板字段不存在，本次忽略：" + "、".join(dropped))
    ov = {k: v for k, v in ov.items() if k in tpl_fields}
    fields.update(ov)
    if ov:
        say(f"   手工覆盖 {len(ov)} 个字段：{'、'.join(ov)}")

    # ── 英文合著者槽位（GAP 2）：源稿给了**可靠**的英译名/单位就填，否则留给
    #    下面的占位符清单。放在手工覆盖之后：覆盖里的作者名才是最终的中文作者列表。
    #    ⚠️ 绝不把第一作者的单位复制给第二作者（人工成品里两份相同是巧合，不是规则）。
    n_cn_authors = coauthor_slot_count(fields)
    en_slots = en_coauthor_values(doc.author_en, doc.affiliation_en, n_cn_authors)
    if en_slots:
        fields.update(en_slots)
        say(f"   英文合著者槽位按源稿填入 {len(en_slots)} 项：{'、'.join(en_slots)}")

    # ── 必填字段解析不出来 → 写占位符 + 警告，不许留模板默认值 ──
    #    清单 = 文章级/第一作者字段 + 「确实存在的合著者」的单位与邮箱
    undetected: list[str] = []
    for name, label in REQUIRED_FIELDS["en" if is_en else "zh"] + coauthor_fields(fields):
        if name in ov or name not in tpl_fields:
            continue
        if is_parsed(fields.get(name), name, tpl_defaults):
            continue
        fields[name] = missing_placeholder(name, label)
        undetected.append(label)
    if undetected:
        say(f"   ⚠ 未识别 {len(undetected)} 个字段，已在产物中写占位符：{'、'.join(undetected)}")
        warnings.append(missing_note(undetected))

    filled = 0
    for k, v in fields.items():
        if not v or v == "null" or k not in tpl_fields:
            continue
        try:
            tpl = set_field(tpl, k, v)
            filled += 1
        except KeyError:
            pass
    say(f"   填入 {filled} 个字段（{'英文' if is_en else '中文'}模板）")
    # 编辑侧出版信息（卷/期/年/起止页/收稿日期/责任编辑/基金）
    ed = editorial_fields(meta)
    for k, v in ed.items():
        if k in tpl_fields:
            tpl = set_field(tpl, k, v)
    if ed:
        say(f"   填入出版信息 {len(ed)} 项：{'、'.join(ed)}")
    else:
        say("   出版信息未提供（--vol/--issue/--year/--pages/--received/--editor/--grants）")
    if is_en and doc.keywords_en:
        warnings.append(f"英文模板无关键词字段，英文关键词需人工放置：{doc.keywords_en[:40]}…")
    elif not is_en and doc.keywords_en and "mykeywordsEN" not in tpl_fields:
        # 实测中文稿也常带英文关键词（09 Jiawei_Chu），但 CN 模板没有对应字段，
        # 只能提示人工放置——不要静默丢掉。
        warnings.append(f"中文模板无英文关键词字段，英文关键词需人工放置：{doc.keywords_en[:40]}…")

    say("④ 迁移正文")
    tpl = insert_body(tpl, body)

    say("⑤ 套用预校正规则")
    res = R.apply_rules(tpl, lang=lang, safe_only=True, docx_mode=True)
    tpl = res.text
    say(f"   自动修复 {sum(res.applied.values())} 处，涉及 {len(res.applied)} 条规则")
    for rid, n in sorted(res.applied.items(), key=lambda x: -x[1]):
        say(f"   · {rid:24s} ×{n}")

    # ⑤′ 一校可自动改子集（期 87 P1-6）：只做 ZH-01..ZH-06 六条确定性替换。
    #    ⚠️ 失败绝不阻断出稿：只写 warning，产物照常生成（编辑宁可多检查，不能没稿）。
    _pf: dict[str, int] = {}
    if proof_autofix and lang == "zh":
        try:
            tpl, _pf = _autofix_proof(tpl, lang)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"一校可自动改子集未执行（不影响出稿）：{type(e).__name__}: {e}")
        else:
            if _pf:
                say(f"   一校可自动改子集补齐 {sum(_pf.values())} 处")
                for rid, n in sorted(_pf.items(), key=lambda x: -x[1]):
                    say(f"   · {rid:24s} ×{n}")

    # ⑤″ 图表交叉引用（期 87 P2-2）：正文「表N/图N」→「表~\ref{tab:N}」。
    #     只在存在对应 \label 时替换（无 label 稿逐字节不变；图 label 由 P2-0 保证全局唯一）。
    tpl, _nx_tab, _nx_fig = _add_xrefs(tpl)
    if _nx_tab or _nx_fig:
        say(f"   图表交叉引用：表 {_nx_tab} 处、图 {_nx_fig} 处")

    # ZH-08 连接号：只提示人工判断，不改产物（第四十三批）。
    #    ⚠️ 纯日志，异常绝不阻断出稿（与 ⑤′ 自动改同一纪律）。
    if dash_hints and lang == "zh":
        try:
            _report_dash_hints(tpl, lang, say)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"ZH-08 连接号提示未执行（不影响出稿）：{type(e).__name__}: {e}")

    audit = R.audit(tpl, lang=lang)

    say("⑥ 写出文件")
    os.makedirs(out_dir, exist_ok=True)
    tex_path = os.path.join(out_dir, f"{basename}.tex")
    # 原子写 + 旧版备份（UI 方案第 4 件）: 产物可能被反复覆盖
    from .fsutil import atomic_write as _atomic_write
    _atomic_write(tex_path, tpl, newline="\n", log=say)
    say(f"   {P.rel(tex_path)}")

    # 参考文献有两个来源，都写进同一个 .bib：
    #   ① Word 末尾的参考文献区（doc.refs）
    #   ② **脚注里的文献条目**（doc.bib_entries）——Lu_Jiang 的 68 条全在这里，
    #      早先只认 ①，导致「参考文献 0 条」、68 处引用全部悬空。
    bib_out = None
    if doc.refs or doc.bib_entries:
        from .footnotes import entry_to_bib

        bib_out = os.path.join(out_dir, "reference.bib")
        parts = []
        if doc.bib_entries:
            parts.append("% ── 由脚注 / 文末编号清单中的文献条目生成 ──\n")
            parts.append("\n".join(entry_to_bib(k, e, raw) for k, e, raw in doc.bib_entries))
        if doc.refs:
            parts.append("\n% ── 由文末参考文献区生成 ──\n")
            parts.append(refs_to_bib(doc.refs))
        # 第 86 条：**英文书籍的版次并进 title**。
        # 为什么在 Word 通道单独补这一步：`normalize_bib()`（里面也做同一件事）只跑在
        # LaTeX 通道；Word 通道的 bib 是这里直接渲染的，不补就会「同一部书、换个通道就不搬」。
        # ⚠️ 用的是**窄版本** `move_book_editions()`：只改命中的那一条，其余逐字节不动。
        from .bibnorm import move_book_editions

        bib_text, ed_notes = move_book_editions("\n".join(parts))
        if ed_notes:
            say(f"   英文书版次并入 title：{len(ed_notes)} 条")
            for n in ed_notes:
                say(f"   · {n}")
        _atomic_write(bib_out, bib_text, newline="\n", log=say)
        say(f"   {P.rel(bib_out)}（脚注/编号清单 {len(doc.bib_entries)} 条"
            f" + 文末人年制 {len(doc.refs)} 条）")

    # 图片导出：docx 里存着原图，直接取出来放进产物目录
    #   —— 比「截图 Word 区域」质量高：截图会重新采样，还可能带上 Word 的显示缩放。
    if doc.images:
        from .docxxml import extract_media

        exported = extract_media(
            docx_path, out_dir,
            {f"r{i}": src for i, (src, _n) in enumerate(doc.images)},
        )
        _miss = len(doc.images) - len(exported)
        if exported:
            say(f"   {len(exported)} 个图片：" + "、".join(sorted(exported.values())))
        if _miss > 0:
            # 第四十七批：MathType 公式图（WMF/EMF）需要 LibreOffice soffice 转换；
            # 无 soffice / 转换失败 → 图片文件不落盘，产物里是「公式图缺失」占位。
            # **不阻断出稿**（外部可选依赖，见 docxxml._soffice_bin）。
            warnings.append(
                f"{_miss} 个公式/图片未能导出（WMF/EMF 需要 LibreOffice soffice 转换；"
                "未安装或转换失败时产物里为「公式图缺失」占位，请安装 LibreOffice 后重跑）"
            )

    sty_out = os.path.join(out_dir, os.path.basename(tpl_sel.sty))
    shutil.copy2(tpl_sel.sty, sty_out)
    say(f"   {P.rel(sty_out)}")

    # 把「整条在 note 里」的脚注体例文献拆成结构化条目
    #   （全库实测 244 条，其中 188 条可拆；原文保留在 note 里可回溯）
    if bib_out:
        try:
            from .bibnorm import expand_note_entries

            before = open(bib_out, encoding="utf-8", errors="replace").read()
            after, exp_notes = expand_note_entries(before)
            if exp_notes:
                with open(bib_out, "w", encoding="utf-8", newline="\n") as f:
                    f.write(after)
                say(f"   参考文献结构化：{len(exp_notes)} 条 note 拆出结构化条目")
        except Exception as e:  # noqa: BLE001  拆不动就保持原样，不能因此中断出稿
            warnings.append(f"参考文献结构化跳过：{type(e).__name__}: {e}")

    # 参考文献核对报告
    if bib_out:
        _write_bib_report(bib_out, out_dir, basename, say, warnings,
                          unmatched=unmatched_points)

    return BuildResult(
        tex_path=tex_path,
        bib_path=bib_out,
        sty_path=sty_out,
        template=T.describe(tpl_sel),
        log=log,
        rule_hits={**res.applied, **_pf},
        audit=audit,
        warnings=warnings,
        missing_fields=[
            f for f in ("myvolnumber", "myissnumber", "mypubyear", "myfirstpage", "mylastpage", "mypages")
            if f not in editorial_fields(meta)
        ],
        bib_keys_used={e.key for e in doc.refs},
        # 第 68 条：把「条目 → 源稿原文」两份材料带出去（⑪ 的任务书与 AI 预跑要用），
        # 只做浅拷贝，不改 doc 本身。
        ref_raw_lines=list(doc.ref_raw_lines),
        ref_raw_by_key=dict(doc.ref_raw_by_key),
    )
