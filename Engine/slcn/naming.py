"""作者姓名 → 输出文件夹 / 文件名。

规范（由仓库现有 92 篇文章的目录名反推验证）
-------------------------------------------
    陈波      → Bo_Chen         （名 Bo + 下划线 + 姓 Chen）
    张亮      → Liang_Zhang
    寇静茹    → Jingru_Kou
    唐莹莹    → Yingying_Tang
    Michael Rathjen → Michael_Rathjen
    Wen-fang Wang   → Wenfang_Wang   （连字符去掉后并成一段）

即 **「名_姓」，中文取拼音、外文取原名**。多作者只取第一作者。

为什么必须严格
--------------
这个名字直接决定目标文件与文件夹名，错了编辑就得手工改目录。
所以本模块的原则是：**能确定才输出，不能确定就报错让用户显式指定**，
绝不靠猜（对应「强制用户提供，不要默认填充」的要求）。
"""

from __future__ import annotations

import os
import re
import sys

# 项目自带的 pypinyin（装在 Engine/vendor/，不依赖系统环境）
#
# ⚠️ **只在 pypinyin 确实缺失时才把 vendor 挂上**。vendor 里除了 pypinyin 还可能有
# lxml 这类**带编译扩展**的包，是为某个特定 Python 版本装的；无条件插到 sys.path
# 最前面，另一个版本的 Python 就会加载到不兼容的二进制——实测 Python 3.12 加载了
# 为 3.14 编译的 lxml，报 `cannot import name 'etree' from 'lxml'`，整条链断掉。
_VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    import importlib.util

    if importlib.util.find_spec("pypinyin") is None:
        # ⚠️ 用 **append 而不是 insert(0)**：vendor 里除了 pypinyin 还有 lxml 这类
        # 带编译扩展的包，只适配某个特定 Python 版本。放到最前面会让它抢先于
        # 系统已装的同名包，实测 3.12 加载到 3.14 编译的 lxml 直接崩。
        # 追加到末尾则「已装的优先，缺的才由 vendor 补」，两边都成立。
        sys.path.append(_VENDOR)

# 复姓：不能按「第一个字=姓」处理
COMPOUND_SURNAMES = {
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫", "万俟", "闻人",
    "夏侯", "诸葛", "尉迟", "公羊", "赫连", "澹台", "皇甫", "宗政", "濮阳", "公冶",
    "太叔", "申屠", "公孙", "慕容", "仲孙", "钟离", "长孙", "宇文", "司徒", "鲜于",
    "司空", "闾丘", "子车", "亓官", "司寇", "巫马", "公西", "颛孙", "壤驷", "公良",
    "漆雕", "乐正", "宰父", "谷梁", "拓跋", "夹谷", "轩辕", "令狐", "段干", "百里",
    "呼延", "东郭", "南门", "羊舌", "微生", "梁丘", "左丘", "东门", "西门", "上官",
}

# 多音字**姓氏**的正确读法。pypinyin 给的是常见读法，作姓氏时往往不同。
# 实测踩坑：单芃舒 → pypinyin 给 dan，正确是 shan（→ Pengshu_Shan）；
#           曾昭式 → pypinyin 给 ceng，正确是 zeng（→ Zhaoshi_Zeng）。
SURNAME_OVERRIDE = {
    "单": "shan", "曾": "zeng", "薄": "bo", "仇": "qiu", "解": "xie",
    "区": "ou", "查": "zha", "尉": "yu", "乐": "yue", "盖": "ge",
    "覃": "qin", "缪": "miao", "朴": "piao", "折": "she", "佘": "she",
    "郇": "huan", "逄": "pang", "仉": "zhang", "佴": "nai", "繁": "po",
    "过": "guo", "重": "chong", "相": "xiang", "员": "yun", "能": "nai",
    "阚": "kan", "都": "du", "乜": "nie", "句": "gou", "种": "chong",
    "任": "ren", "燕": "yan", "纪": "ji", "邳": "pi", "蕃": "pi",
}

# 输出名允许的字符（防止出现带空格/斜杠的目录名）
_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class AuthorNameError(ValueError):
    """无法从既有信息确定输出名——需要用户显式指定 --name 或 --author。"""


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def _pinyin(word: str) -> list[str]:
    try:
        from pypinyin import lazy_pinyin

        return lazy_pinyin(word)
    except ImportError as e:  # pragma: no cover
        raise AuthorNameError(
            "缺少 pypinyin，无法把中文姓名转成拼音。"
            "请运行 `pip install --target vendor pypinyin`，"
            "或直接用 --name 指定输出名（如 --name Bo_Chen）。"
        ) from e


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:].lower() if s else s


def split_chinese_name(name: str) -> tuple[str, str]:
    """把中文姓名拆成 (姓, 名)。支持复姓。"""
    n = re.sub(r"[\s·•]", "", name)
    if len(n) < 2:
        raise AuthorNameError(f"中文姓名「{name}」太短，无法判断姓与名")
    if n[:2] in COMPOUND_SURNAMES:
        return n[:2], n[2:]
    return n[:1], n[1:]


def surname_pinyin(sur: str) -> str:
    """姓氏转拼音，多音字以 SURNAME_OVERRIDE 为准。"""
    out = []
    for ch in sur:
        if ch in SURNAME_OVERRIDE:
            out.append(SURNAME_OVERRIDE[ch])
        else:
            out.append("".join(_pinyin(ch)))
    return "".join(out)


def chinese_to_folder(name: str) -> str:
    """陈波 → Bo_Chen。"""
    sur, given = split_chinese_name(name)
    if not given:
        raise AuthorNameError(f"中文姓名「{name}」只有姓、没有名，无法生成「名_姓」格式")
    return f"{_cap(''.join(_pinyin(given)))}_{_cap(surname_pinyin(sur))}"


# 姓名里**真的会造成歧义**的多音字（两个读音都常见）。
# 不用 pypinyin 的 heteronym 全量列表——那个会把「波 bō/bēi/bì」这类冷僻异读
# 也报出来，导致每篇都弹提醒，等于没提醒。
AMBIGUOUS_NAME_CHARS = {
    "朝": "cháo/zhāo", "长": "cháng/zhǎng", "行": "xíng/háng", "重": "chóng/zhòng",
    "乐": "lè/yuè", "华": "huá/huà", "茜": "qiàn/xī", "蕃": "fán/fān",
    "都": "dōu/dū", "相": "xiāng/xiàng", "兴": "xīng/xìng", "中": "zhōng/zhòng",
    "正": "zhèng/zhēng", "石": "shí/dàn", "好": "hǎo/hào", "传": "chuán/zhuàn",
    "强": "qiáng/jiàng", "参": "cān/shēn", "和": "hé/hè", "应": "yīng/yìng",
    "宁": "níng/nìng", "藏": "cáng/zàng", "曾": "céng/zēng", "单": "dān/shàn",
    "解": "jiě/xiè", "仇": "chóu/qiú", "区": "qū/ōu", "查": "chá/zhā",
    "尉": "wèi/yù", "盖": "gài/gě", "覃": "tán/qín", "缪": "miù/miào",
    "朴": "pǔ/piáo", "折": "zhé/shé", "燕": "yàn/yān", "纪": "jì/jǐ",
    "过": "guò/guō", "员": "yuán/yún", "能": "néng/nài", "乜": "miē/niè",
    "句": "jù/gōu", "种": "zhǒng/chóng", "舍": "shè/shě", "任": "rèn/rén",
    "陆": "lù/liù", "华": "huá/huà", "沈": "shěn/chén", "召": "zhào/shào",
}


def homographs(name: str) -> list[str]:
    """列出姓名里**真正有歧义**的多音字，供用户确认后再定名。

    多音字无法可靠自动判定（朝 可以是 zhāo 也可以是 cháo）。与其猜，
    不如把选择权交回用户——命中时 CLI 会打印两种可能并提示用 --name 覆盖。
    """
    try:
        sur, given = split_chinese_name(name)
    except AuthorNameError:
        sur, given = "", name
    hits = []
    for ch in re.sub(r"[^\u4e00-\u9fff]", "", given):
        if ch in AMBIGUOUS_NAME_CHARS:
            hits.append(f"{ch}（{AMBIGUOUS_NAME_CHARS[ch]}）")
    # 姓里的多音字若已在 SURNAME_OVERRIDE 里定过读音，就不再打扰用户
    for ch in re.sub(r"[^\u4e00-\u9fff]", "", sur):
        if ch in AMBIGUOUS_NAME_CHARS and ch not in SURNAME_OVERRIDE:
            hits.append(f"{ch}（{AMBIGUOUS_NAME_CHARS[ch]}，姓氏读法）")
    return hits


def latin_to_folder(name: str) -> str:
    """Michael Rathjen → Michael_Rathjen；Wen-fang Wang → Wenfang_Wang。

    兼容 "Rathjen, Michael"（姓, 名）与 "Michael Rathjen"（名 姓）两种写法。
    """
    n = re.sub(r"[,，].*$", "", name.strip()) if "," in name else name.strip()
    n = re.sub(r"\b(Prof|Professor|Dr|Mr|Ms|Mrs)\.?\b", "", n, flags=re.I)
    n = re.sub(r"\s+", " ", n).strip(" .")
    if not n:
        raise AuthorNameError(f"无法从「{name}」解析出姓名")

    # 「姓, 名」写法：逗号前是姓
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        surname = parts[0]
        given = parts[1] if len(parts) > 1 else ""
    else:
        parts = n.split()
        if len(parts) == 1:
            raise AuthorNameError(
                f"「{name}」只有一个词，无法判断姓与名；请用 --name 显式指定输出名"
            )
        surname = parts[-1]
        given = " ".join(parts[:-1])

    # 去掉职称尾缀（如 "Wen-fang Wang, Distinguished Professor"）
    given = re.sub(r"\b(Distinguished|Professor|Prof|Dr)\b.*$", "", given, flags=re.I).strip()
    # 连字符与空格并成一段：Wen-fang → Wenfang
    given_clean = re.sub(r"[^A-Za-z]", "", given)
    sur_clean = re.sub(r"[^A-Za-z]", "", surname)
    if not given_clean or not sur_clean:
        raise AuthorNameError(f"无法从「{name}」解析出规范的姓名")
    return f"{_cap(given_clean)}_{_cap(sur_clean)}"


def author_to_name(author: str) -> str:
    """作者姓名 → 输出名（同时用作文件夹名与文件名主体）。

    中文 → 拼音；外文 → 原名。多作者传第一作者即可。
    """
    a = author.strip()
    if not a:
        raise AuthorNameError("作者姓名为空")
    # 多个作者：只取第一个
    a = re.split(r"[、,，;；]|\s+and\s+|\s*&\s*", a)[0].strip()
    if not a:
        raise AuthorNameError(f"无法从「{author}」中取出第一作者")

    if _has_cjk(a):
        return chinese_to_folder(a)
    return latin_to_folder(a)


def validate_name(name: str) -> str:
    """校验用户显式给出的 --name 是否可用作文件夹名。"""
    n = name.strip()
    if not _SAFE_NAME.match(n):
        raise AuthorNameError(
            f"输出名「{name}」不合法。只允许字母、数字、下划线，且以字母开头（如 Bo_Chen）。"
        )
    return n


# ── 从各类源文件里抽作者 ────────────────────────────────────────────────────

_TEX_AUTHOR_RE = re.compile(r"\\kaishu\s*|\\heiti\s*|\\songti\s*|\\bf\s*|\{\s*|\}\s*")

# 中文姓名里不可能出现的标点（出现即说明这段是正文/摘要，不是人名）
_NAME_BAD_PUNCT = set("：:；;。.，,、？！?!（）()《》“”\"'…—-–/\\")


def looks_like_person_name(name: str) -> bool:
    """Word 通道「这一段像不像人名」的保守闸门（第 141 条，2026-10-18）。

    为什么需要：`convert.author_from_source()` 的 Word 启发式是「第 1–5 段里找一段
    看着像姓名的」，旧判据只看长度 ≤40、不以句号/冒号结尾——于是**把正文当作者**：

      · 期 87 子案例 2（03 Wenjing_Du）源稿没写作者，第 4 段正文被当成作者，
        输出名推导成 `Yesizhuyirenwei_Bei`（「贝叶斯主义认为」）；
      · 子案例 3（04 Fayang_Ye）第 2 段 `摘  要：…` 中间有空格，摘要行判据没命中，
        单字「摘」被当作者。

    这与 `naming.py` 的原则冲突：「能确定才输出，不能确定就报错让用户显式指定，
    绝不靠猜」。故加闸门：**中文候选**必须是 2–6 字纯汉字（含间隔号的名字放宽到
    4–14 字）且不含中文标点；**拉丁/其它候选沿用旧的宽松口径**（真实英文姓名形态多，
    收紧误伤面大，且没实测到对应缺陷）。

    反例（应当返回 False）：`贝叶斯主义认为`、`摘`、`数学基础是数学哲学研究中的重要问题`、
    `摘  要：数学基础是…`。正例：`陈波`、`唐瑞雪`、`冯光武`、`阿依古丽·买买提`、
    `Michael Rathjen`、`Wen-fang Wang`。
    """
    s = (name or "").strip()
    if not s:
        return False
    compact = re.sub(r"\s+", "", s)
    cjk = [ch for ch in compact if "\u4e00" <= ch <= "\u9fff"]
    if not cjk:
        # 拉丁姓名：每个词首字母大写（允许 van/von/de 等小写介词/冠词），允许首字母
        # 缩写点与连字符。反例：`Over the past decades`（小写实词 the/past/decades）、
        # `School of Philosophy`（of）、`1`（纯数字）、`1. 引言`（标点）。
        # 这条比中文分支宽松一点（真实英文姓名形态多），但足以挡住整句正文。
        if any(ch in _NAME_BAD_PUNCT and ch not in ".-'" for ch in s):
            return False
        tokens = [t for t in re.split(r"\s+", s.strip()) if t]
        if not tokens or len(tokens) > 5:
            return False
        particles = {"van", "von", "de", "der", "den", "da", "di", "la", "le",
                     "du", "dos", "ten", "ter", "bin", "al", "el"}
        for t in tokens:
            core = t.strip(".-'")
            if not core:
                return False
            if core.lower() in particles:
                continue
            if not core[0].isupper():
                return False
        return True
    # 中文（含少数民族间隔号姓名）：标点即正文
    if any(ch in _NAME_BAD_PUNCT for ch in compact):
        return False
    if "·" in compact or "•" in compact:
        return 4 <= len(compact) <= 14
    if len(cjk) != len(compact):
        return False  # 中英混排多半是正文片段
    return 2 <= len(cjk) <= 6


def author_from_tex_metadata(text: str) -> str:
    """从含 SLCN 模板宏的 tex 里取第一作者（\\myfirstauthor）。"""
    from .latex import get_macro_value

    for field in ("myfirstauthor", "myfirstauthorEN"):
        v = get_macro_value(text, field)
        if not v or v.strip() in ("null", ""):
            continue
        # 去掉 \kaishu 之类的字体命令与花括号
        v = _TEX_AUTHOR_RE.sub("", v).strip()
        if v:
            return v
    return ""


def resolve_output_name(
    *,
    explicit_name: str | None = None,
    explicit_author: str | None = None,
    author_from_source: str | None = None,
    source_desc: str = "",
) -> tuple[str, str]:
    """确定输出名。

    优先级：--name > --author > 从源文件读到的作者。
    三者都拿不到时**报错**，不猜——这正是「强制用户提供」的要求。

    返回 (输出名, 来源说明)
    """
    if explicit_name:
        return validate_name(explicit_name), "用户用 --name 指定"

    if explicit_author:
        return author_to_name(explicit_author), f"用户用 --author 指定：{explicit_author}"

    if author_from_source:
        return author_to_name(author_from_source), f"取自源文件：{author_from_source}"

    raise AuthorNameError(
        "无法确定输出文件夹名：源文件里读不到第一作者姓名。\n"
        f"  （源文件：{source_desc or '未注明'}）\n"
        "  请显式指定其一：\n"
        "    --author \"陈波\"      从姓名自动转拼音\n"
        "    --name Bo_Chen        直接给出输出名"
    )
