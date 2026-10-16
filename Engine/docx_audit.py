#!/usr/bin/env python3
"""全库 Word 源稿体例普查（只读）。

对编辑部数据库里所有 Word 源稿跑一遍**现有解析器**（`slcn.docxsource.parse_docx`），
统计「识别到了什么 / 没识别到什么」，产出：

  · `out/docx_audit.csv` —— 每篇一行：字段识别标志 + 节标题数 + 文献数 + 文献体例
  · `out/docx_audit.txt` —— 人可读汇总：字段识别率、节标题体例分布、
                            参考文献体例分布、长尾清单、判据

用途：**决定后续为哪些体例写兼容、不为哪些写**。所以本脚本只做「体检」：
不修改任何源稿、不改 `slcn/`、不跑 git。

两条口径（报告里也写在开头，看数字前先看它）
--------------------------------------------
① 源稿位置：只扫任务书指定的两处

      · `<期号>/<稿件>/*.docx`
      · `<期号>/<稿件>/original/**/*.docx`

   跳过 `._*` / `~$*` / `.*` / `__MACOSX`。
   ⚠ macOS 文件系统不区分大小写，`<稿件>/original` 把同目录下的 `Original/` 也扫了进来
   （程序里显式按 `name.lower() == "original"` 取，行为可复现）。
   这两处之外还有别的 docx（`原文/`、`origin/`、`校对/`…）：**不计入主表分母**，
   只在附录里给出数量与聚合体例分布——免得把「没扫到」误读成「不存在」。

② 「本来就没有」与「解析失败」分开：每个字段除了看 `parse_docx` 拿没拿到，
   还**独立地在原文纯文本上查证据**（判据见下与报告末「判据」一节）。
   于是每个字段三种状态，互斥且相加 = 篇数：

      ok      解析拿到了
      fail    原文有证据、解析没拿到   →「解析失败」，值得写兼容
      absent  原文也没有证据           →「本来就没有」，不必写兼容

   证据判据是**保守**的（宁可少扣作者帽子，也不把责任推给作者），因此：
      · `email` / `abstract_*` / `keywords_*` 的判据很硬（裸邮箱、标签），fail 基本可信；
      · `affiliation` / `author` / `title_en` 是形态判据，`fail` 里可能混少量误报，
        报告里逐条给出文件名，便于抽查。

③ 体例判定一律是**启发式**：先按条目/段落签名分类，再按篇取 ≥60% 多数票，
   拿不到多数票的归 `其他*`，不硬猜。判据在报告末逐条写明。

用法
----
    cd Engine
    python3 docx_audit.py
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# vendor 是打包用的本地依赖：**append**（缺什么补什么），不要 insert(0)，
# 否则会拿为别的 Python 版本编译的 lxml 覆盖已装的（README「并入过程中踩到的问题②」）。
_VENDOR = os.path.join(_HERE, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.append(_VENDOR)

from slcn.docxsource import parse_docx, parse_tail_metadata, refs_to_bib  # noqa: E402
from slcn.docxxml import Table, read_blocks, read_footnotes, segs_to_text  # noqa: E402
from slcn.footnotes import entry_to_bib  # noqa: E402
from slcn import bibcheck, footref  # noqa: E402

# 稿件库锚点（第 100 条）：开发主场搬到公开仓后，稿件库可能在别处 —— 由 `slcn/paths.py` 统一解析。
# ⚠️ 找不到时**保持 None**，由 main() 打印说明并正常退出；**不要**回退到"Engine 上两级"，
#    那在公开仓里会指向 <GitHub> 之类的无关目录，扫出莫名其妙的结果。
from slcn import paths as _paths  # noqa: E402
REPO = _paths.manuscripts_root()
OUT_DIR = os.path.join(_HERE, "out")
OUT_CSV = os.path.join(OUT_DIR, "docx_audit.csv")
OUT_TXT = os.path.join(OUT_DIR, "docx_audit.txt")

ISSUE_RE = re.compile(r"^\d{4}年第")
SKIP_PREFIX = ("._", "~$", ".~", ".")
ORIGINAL_DIR = "original"          # 大小写不敏感（macOS），见模块 docstring 口径①

# ── 原文证据用的正则（与 parse_docx 内部那套**分开**，才能发现解析失败）────────
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
ABS_RE = re.compile(r"^(?:摘\s*要|中文摘要|英文摘要|Abstract)\s*[:：]?\s*", re.I)
KW_RE = re.compile(r"^(?:关键词|中文关键词|英文关键词|Keywords?|Key\s*words?)\s*[:：]?\s*", re.I)
SUB_RE = re.compile(r"^\s*(?:—{1,3}|–{1,2}|―{1,2}|-{1,2})\s*\S")
EMAIL_ANY_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
AFFIL_WORD_RE = re.compile(
    r"大学|学院|研究院|研究所|实验室|科学院|社会科学院|哲学系|"
    r"Department|University|College|Institute|School\s+of|Academy|Center|Centre",
    re.I,
)
AFFIL_HEAD_RE = re.compile(r"(?:所属单位|作者单位|单位|affiliation)\s*[:：]", re.I)
REF_HEAD_RE = re.compile(
    r"^\s*(参考文献|参考书目|引用文献|征引文献|主要参考文献|文献|注释|"
    r"References?|Bibliography|Works\s+Cited)\s*[:：]?\s*$",
    re.I,
)
NUM_ITEM_RE = re.compile(r"^\s*[\[\(（]\s*(\d{1,3})\s*[\]\)）]")
# 兜底定位文献区时只认**半角方括号**编号：[N] 是文献清单的写法，
# （N）/(N)/【N】 多是正文列表项与公式编号（实测踩到：`（3）如果是白天…`、`(31) p(qr)`）。
SQUARE_ITEM_RE = re.compile(r"^\s*\[\s*(\d{1,3})\s*\]")
DOT_ITEM_RE = re.compile(r"^\s*(\d{1,3})\s*[.、]\s*(?!\d)")
YEAR_RE = re.compile(r"(?:1[89]|20)\d{2}")
QUOTE_RE = re.compile(r"[“”\"]")
GBT_MARK_RE = re.compile(r"\[\s*[MJCNDRGSPA]\s*\]")
TRANS_RE = re.compile(r"[（(]\s*(?:著|译|编|编著|主编|校)\s*[）)]|(?:著|译)[，,、]")

# 人名形态判据里的停用词：中文标题/元数据常长得像「2–4 个汉字」，靠这批词挡掉
PERSON_STOP = (
    "引言", "绪论", "结语", "结论", "摘要", "关键词", "目录", "附录", "致谢", "参考文献",
    "问题", "背景", "研究", "分析", "理论", "方法", "思想", "视域", "视角", "综述", "述评",
    "说明", "意见", "回复", "回应", "简评", "书评", "编后", "稿约", "更正", "内容提要",
)

# ── 节标题体例 ────────────────────────────────────────────────────────────
H_N_SPACE = re.compile(r"^(\d{1,2})[ \t\u3000]+(\S.*)$")
H_N_CJK = re.compile(r"^(\d{1,2})(?=[\u4e00-\u9fff])(.*)$")
H_NM = re.compile(r"^\d{1,2}\.\d{1,2}(?!\d)")
H_N_DOT = re.compile(r"^\d{1,2}\.[ \t\u3000]*(\S.*)$")
H_CN1 = re.compile(r"^[一二三四五六七八九十]{1,3}、")
H_CN2 = re.compile(r"^[（(][一二三四五六七八九十]{1,3}[）)]")
H_N_PAREN = re.compile(r"^\d{1,2}[）)][ \t\u3000]*(\S.*)$")

HEAD_CN_BAD_TAIL = ("年", "月", "日", "世纪", "年代", "页", "卷", "期", "个", "种", "位", "条")
HEAD_PUNCT = "。；，、：；！？,;:!?"

# 层级权重：数字/前缀式一级标题优先于括号式子节（(a) 表按权重取「主体例」）
HEAD_PRIORITY = [
    ("N 空格", 6), ("N紧跟汉字", 5), ("一、", 4),
    ("N. 空格", 3), ("N.M", 2), ("（一）", 1), ("N）", 0),
]

FIELDS = [
    ("title", "题名"),
    ("subtitle", "副标题"),
    ("author", "作者"),
    ("affiliation", "单位"),
    ("email", "邮箱"),
    ("abstract_cn", "中文摘要"),
    ("keywords_cn", "中文关键词"),
    ("title_en", "英文题名"),
    ("abstract_en", "英文摘要"),
]

# bib_style 细分 → 粗族（粗族表给「值不值得写兼容」用）
STYLE_FAMILY = {
    "脚注体例": "脚注体例",
    "脚注体例+编号清单": "脚注体例+编号清单",
    "编号+引号题名": "编号式人年清单",
    "编号+西文人年(无引号)": "编号式人年清单",
    "中文译著": "中文译著/整理本",
    "GB/T7714标记": "GB/T 7714",
    "人年制无编号": "人年制无编号",
    "其他:编号无年份": "其他",
    "其他:N.式编号": "其他",
    "其他:中文无年份": "其他",
    "其他:无法判定": "其他",
    "其他:混排": "其他",
    "无参考文献": "无参考文献",
}
STYLE_ORDER = [
    "脚注体例", "脚注体例+编号清单", "编号+引号题名", "编号+西文人年(无引号)",
    "中文译著", "GB/T7714标记", "人年制无编号", "其他:编号无年份", "其他:N.式编号",
    "其他:中文无年份", "其他:无法判定", "其他:混排", "无参考文献",
]


# ══ 源稿枚举 ═══════════════════════════════════════════════════════════════

def _docx_files(dirpath: str, recursive: bool) -> list[str]:
    out: list[str] = []
    if recursive:
        for dp, dirs, files in os.walk(dirpath):
            dirs[:] = [d for d in dirs if d != "__MACOSX"]
            for f in sorted(files):
                if f.lower().endswith(".docx") and not f.startswith(SKIP_PREFIX):
                    out.append(os.path.join(dp, f))
    else:
        for f in sorted(os.listdir(dirpath)):
            p = os.path.join(dirpath, f)
            if os.path.isfile(p) and f.lower().endswith(".docx") and not f.startswith(SKIP_PREFIX):
                out.append(p)
    return out


def _original_dirs(slug_dir: str) -> list[str]:
    """稿件目录下所有「名字等于 original（不分大小写）」的子目录。"""
    return [
        os.path.join(slug_dir, d) for d in sorted(os.listdir(slug_dir))
        if d.lower() == ORIGINAL_DIR and os.path.isdir(os.path.join(slug_dir, d))
    ]


def iter_sources() -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """返回 (口径内源稿, 口径外 docx)；两项都是 (期号, 稿件, 绝对路径)。"""
    primary: list[tuple[str, str, str]] = []
    others: list[tuple[str, str, str]] = []
    for issue in sorted(os.listdir(REPO)):
        ip = os.path.join(REPO, issue)
        if not ISSUE_RE.match(issue) or not os.path.isdir(ip):
            continue
        for slug in sorted(os.listdir(ip)):
            sp = os.path.join(ip, slug)
            if not os.path.isdir(sp) or slug == "__MACOSX":
                continue
            for p in _docx_files(sp, recursive=False):            # 位置一：稿件目录下
                primary.append((issue, slug, p))
            for od in _original_dirs(sp):                          # 位置二：original/**
                for p in _docx_files(od, recursive=True):
                    primary.append((issue, slug, p))
            for p in _docx_files(sp, recursive=True):              # 口径外：其余子目录
                rel = os.path.relpath(p, sp)
                parts = rel.split(os.sep)
                if len(parts) == 1 or parts[0].lower() == ORIGINAL_DIR:
                    continue
                others.append((issue, slug, p))
    primary.sort()
    others.sort()
    return primary, others


# ══ 原文证据 ═══════════════════════════════════════════════════════════════

def loc_tail_start(plains: list[str], ref_head_idx: int) -> int:
    """文末中英元数据块的起点（英文摘要往上回溯连续短非中文行）。

    与 `parse_docx` 同法，但用本脚本自己的参考文献标题定位，
    这样「解析器没定位到 tail 块」这件事才看得出来。
    """
    if ref_head_idx < 0:
        return -1
    abs_idx = next(
        (i for i in range(ref_head_idx + 1, len(plains))
         if ABS_RE.match(plains[i]) and not CJK_RE.search(plains[i])),
        -1,
    )
    if abs_idx < 0:
        return -1
    j = abs_idx - 1
    while j > ref_head_idx and plains[j].strip() and not CJK_RE.search(plains[j]) \
            and len(plains[j]) <= 160:
        j -= 1
    return j + 1


def reflike_numbered(t: str) -> bool:
    """像一条编号文献：`[N]`（≥25 字或含年份）或 `N.`（≥30 字且含年份）。

    正文里的 `（3）如果是白天…`、`(31) p(qr)`、`1. 尚未形成…` 这类列表项要被挡住。
    """
    t = t.strip()
    if SQUARE_ITEM_RE.match(t):
        return len(t) >= 25 or bool(YEAR_RE.search(t))
    if DOT_ITEM_RE.match(t):
        return len(t) >= 30 and bool(YEAR_RE.search(t))
    return False


def ref_section(plains: list[str]) -> tuple[int, int, str]:
    """宽松定位参考文献区，返回 (条目区起点, 结束下标, 标题原文)；找不到 (-1, -1, "")。

    **返回值约定**：`plains[start:end]` 就是文献条目所在的段落，`start - 1` 是标题段
    （没标题的兜底情形里 `start` 就是第一条 `[N]` 行的下标）。

    「宽松」= 独占一段的 参考文献/References/Bibliography…（含带冒号）都算，
    比 `parse_docx` 的精确匹配宽，以便暴露「有文献区但一条都没解析出来」。
    没有标题时退回「文末连续的 `[N]` 行」（半角方括号、间隔 ≤5 段、行内像条目）当文献区。
    """
    idx = [i for i, t in enumerate(plains) if REF_HEAD_RE.match(t) and len(t.strip()) <= 25]
    if idx:
        i, head, start = idx[-1], plains[idx[-1]].strip(), idx[-1] + 1
    else:
        nums = [i for i, t in enumerate(plains) if reflike_numbered(t)]
        run = [nums[-1]] if nums else []
        for j in reversed(nums[:-1]):
            if run[-1] - j <= 5:
                run.append(j)
            else:
                break
        if len(run) < 2:
            return -1, -1, ""
        i, head, start = min(run) - 1, "", min(run)
    end = loc_tail_start(plains, i)
    if end <= start:                  # 没有英文摘要：退化为遇到英文标签收口
        end = len(plains)
        for j in range(start, len(plains)):
            t = plains[j].strip()
            if re.match(r"^(英文题目|英文标题|英文题名|题目|单位|作者|E-?mail|邮箱)\s*[:：]", t) \
                    and not CJK_RE.search(t):
                end = j
                break
    return start, end, head


def split_entries(lines: list[str]) -> list[str]:
    """把文献区段落切成条目。

    编号清单（`[N]` / `N.`）里，不以编号起头的行并入上一条（长条目会换段）；
    非编号清单（人年制、古籍式）一律按段落一条，不合并（否则会把两条并成一条）。
    """
    lines = [t.strip() for t in lines if t.strip()]
    if not lines:
        return []
    numbered = sum(1 for t in lines if NUM_ITEM_RE.match(t) or DOT_ITEM_RE.match(t))
    as_list = numbered >= max(2, 0.5 * len(lines))
    out: list[str] = []
    for t in lines:
        if not out or NUM_ITEM_RE.match(t) or DOT_ITEM_RE.match(t):
            out.append(t)
        elif as_list:
            out[-1] = out[-1] + " " + t
        else:
            out.append(t)
    return out


def entry_style(t: str) -> str:
    """单条条目的体例签名。`[N]` 才算「编号清单」——（N）/(N) 多为正文列表项。"""
    numbered = bool(SQUARE_ITEM_RE.match(t))
    if GBT_MARK_RE.search(t):
        return "GB/T7714标记"
    if numbered and CJK_RE.search(t) and TRANS_RE.search(t) and YEAR_RE.search(t):
        return "中文译著"
    if numbered and QUOTE_RE.search(t) and YEAR_RE.search(t):
        return "编号+引号题名"
    if numbered and YEAR_RE.search(t):
        return "编号+西文人年(无引号)"
    if numbered:
        return "其他:编号无年份"
    if DOT_ITEM_RE.match(t):
        return "其他:N.式编号"
    if YEAR_RE.search(t):
        return "人年制无编号"
    if CJK_RE.search(t) and len(t) > 15:
        return "其他:中文无年份"
    return "其他:无法判定"


def classify_bib(entries: list[str], n_fn_ref: int) -> tuple[str, collections.Counter]:
    """篇级文献体例：条目签名取多数票（需 ≥60%），否则 其他*；无条目时看脚注。

    `[N] 作者, 年, 题名, 出版信息` 这种清单里，期刊条目题名带引号、专著条目不带，
    同一篇两种并存是常态，所以把「有引号题名」记 b、「整份都没有引号」记 d，
    两者都不算「拿不到多数票」。
    """
    cnt: collections.Counter = collections.Counter(entry_style(t) for t in entries)
    n = len(entries)
    if n == 0:
        return ("脚注体例" if n_fn_ref > 0 else "无参考文献"), cnt
    # b/d 合并成一族判断：有引号题名 → b；一条都没有 → d
    num = cnt.get("编号+引号题名", 0) + cnt.get("编号+西文人年(无引号)", 0)
    quote = cnt.get("编号+引号题名", 0)
    merged = collections.Counter(cnt)
    if num:
        merged.pop("编号+引号题名", None)
        merged.pop("编号+西文人年(无引号)", None)
        key = "编号+引号题名" if quote > 0 else "编号+西文人年(无引号)"
        merged[key] = num
    top, c = merged.most_common(1)[0]
    if n and c >= 0.6 * n:
        return top, cnt
    if n_fn_ref > 0:
        return "脚注体例+编号清单", cnt
    return "其他:混排", cnt


def head_kinds(t: str) -> set[str]:
    """一段文字命中的节标题体例（在段落实测，不依赖解析器）。"""
    kinds: set[str] = set()

    def ok(rest: str) -> bool:
        """标题正文形态：短、无句读、不像「20 世纪」这类残句。"""
        rest = rest.strip()
        return (2 <= len(rest) <= 40
                and not any(c in rest for c in HEAD_PUNCT)
                and not rest.endswith(("。", "；", "，", "、"))
                and not rest.startswith(HEAD_CN_BAD_TAIL))

    m = H_N_SPACE.match(t)
    if m and ok(m.group(2)):
        kinds.add("N 空格")
    m = H_N_CJK.match(t)
    if m and ok(m.group(2)):
        kinds.add("N紧跟汉字")
    if H_NM.match(t) and ok(t[3:] if len(t) > 3 else ""):
        kinds.add("N.M")
    else:
        m = H_N_DOT.match(t)
        if m and ok(m.group(1)):
            kinds.add("N. 空格")
    if H_CN1.match(t):
        kinds.add("一、")
    if H_CN2.match(t):
        kinds.add("（一）")
    else:
        m = H_N_PAREN.match(t)
        if m and ok(m.group(1)):
            kinds.add("N）")
    return kinds


def looks_like_person(t: str) -> bool:
    """像「一行作者名」：中文 2–4 字姓名（可用空格/顿号连排），或 2–5 个西文名/缩写。"""
    t = re.sub(r"\s+", " ", EMAIL_ANY_RE.sub(" ", t)).strip()
    if not t or len(t) > 40:
        return False
    if re.search(r"[。.！!？?；;：:（）()\[\]【】《》<>@]", t):
        return False
    if any(w in t for w in PERSON_STOP):
        return False
    if CJK_RE.search(t):
        return bool(re.fullmatch(r"[\u4e00-\u9fff·•]{2,4}(?:[ 、,，]{1,2}[\u4e00-\u9fff·•]{2,4}){0,4}", t))
    toks = [x for x in re.split(r"[ \u3000]+", t) if x]
    if not (2 <= len(toks) <= 5):
        return False
    return all(re.fullmatch(r"[A-Z]\.?(?:[a-z'’\-]+)?", x) for x in toks)


def looks_like_affil_line(t: str) -> bool:
    """像「一行单位」：含机构词、短、不以句点收尾，且不是文献条目/副标题。"""
    t = t.strip()
    if not t or len(t) > 160 or "。" in t or "．" in t:
        return False
    if t.endswith((".", "．", ";", "；")) or any(c in t for c in "《》"):
        return False
    if NUM_ITEM_RE.match(t) or DOT_ITEM_RE.match(t) or GBT_MARK_RE.search(t) or SUB_RE.match(t):
        return False
    return bool(AFFIL_WORD_RE.search(t))


def plain_text(path: str) -> tuple[list[str], list[str]]:
    """(全部段落纯文本（空段保留，下标与 parse_docx 对齐）, 脚注纯文本)。"""
    blocks = read_blocks(path)
    plains = [b.plain for b in blocks if not isinstance(b, Table)]
    fntexts = [segs_to_text(s) for s in read_footnotes(path).values()]
    return plains, fntexts


def evidence(plains: list[str], fntexts: list[str], ref_info: tuple[int, int, str]) -> dict:
    """在原文纯文本上独立查证据（判据见模块 docstring 与报告末「判据」）。"""
    ri, _re, _rh = ref_info
    seg = [(i, t.strip()) for i, t in enumerate(plains) if t.strip()]
    texts = [t for _, t in seg]
    fn_lines = [ln.strip() for t in fntexts for ln in t.splitlines() if ln.strip()]
    raw = "\n".join(texts)
    ev: dict = {}

    ev["title"] = bool(texts)
    ev["subtitle"] = any(SUB_RE.match(t) for t in texts[:4]) or \
        any(SUB_RE.match(t) and not CJK_RE.search(t) for t in texts)

    # 作者：题名段之后、第一处「摘要/关键词」标签之前，存在一行像人名的短行
    first_meta = next((k for k, t in enumerate(texts) if ABS_RE.match(t) or KW_RE.match(t)), -1)
    if first_meta >= 2:
        zone = texts[1:first_meta]
    elif first_meta == 1:
        zone = texts[1:4]
    else:
        zone = texts[1:9]
    ev["author"] = any(looks_like_person(t) for t in zone)

    # 单位：只看「元数据可能出现的位置」——开头元数据区（题名后到第一处摘要/关键词标签）、
    #       文末中英元数据块（参考文献区之后到文末）、带标签的行（单位：/所属单位：/作者单位：）
    #       与脚注里不像文献的行。正文句子与文献条目一律不算。
    if first_meta >= 1:
        head_zone = texts[1:first_meta]
    else:
        head_zone = texts[1:6]
    ts = loc_tail_start(plains, ri)
    if ts >= 0:
        tail_zone = [t for i, t in seg if i >= ts]
    elif ri < 0:
        tail_zone = texts[-12:]          # 没有文献区时才退回「文末若干段」
    else:
        tail_zone = []
    ev["affiliation"] = bool(
        any(looks_like_affil_line(t) for t in head_zone + tail_zone)
        or any(AFFIL_HEAD_RE.search(t) for t in texts)
        or any(looks_like_affil_line(t) and not footref.looks_like_reference(t) for t in fn_lines))

    ev["email"] = bool(EMAIL_ANY_RE.search(raw)) or bool(EMAIL_ANY_RE.search("\n".join(fn_lines)))
    ev["abstract_cn"] = any(ABS_RE.match(t) and CJK_RE.search(t) for t in texts)
    ev["keywords_cn"] = any(KW_RE.match(t) and CJK_RE.search(t) for t in texts)
    ev["abstract_en"] = any(ABS_RE.match(t) and not CJK_RE.search(t) for t in texts)
    ev["keywords_en"] = any(KW_RE.match(t) and not CJK_RE.search(t) for t in texts)
    # 英文题名：第一处「英文摘要/关键词」标签之前有 5–200 字的非中文行
    ti = next((k for k, t in enumerate(texts)
               if (ABS_RE.match(t) or KW_RE.match(t)) and not CJK_RE.search(t)), -1)
    ev["title_en"] = ti > 0 and any(
        5 <= len(t) <= 200 and not CJK_RE.search(t) for t in texts[:ti])
    ev["n_fn_ref"] = sum(1 for t in fntexts if footref.looks_like_reference(t))
    ev["ref_section"] = bool(
        any(REF_HEAD_RE.match(t) and len(t) <= 25 for t in texts)
        or sum(1 for t in texts if reflike_numbered(t)) >= 2
        or ev["n_fn_ref"] > 0)
    return ev


# ══ 单篇体检 ═══════════════════════════════════════════════════════════════

def audit_one(issue: str, slug: str, path: str) -> dict:
    rel = os.path.relpath(path, REPO).replace(os.sep, "/")
    rec: dict = {
        "issue": issue, "slug": slug, "file": rel, "ok": False, "error": "",
        "chars": 0, "title": "", "author": "", "n_headings": 0, "n_bib": 0,
        "bib_style": "", "n_entries": 0, "missing": [], "status": {}, "head_kinds": {},
        "head_parsed": False, "bib_must": 0, "bib_unstructured": 0, "bib_total": 0,
        "ev": {}, "tail_found": False, "ref_head": "", "entry_mix": {},
        "num_item_lines": 0, "quote_n": 0, "num_n": 0, "aux": False,
    }
    try:
        plains_all, fntexts = plain_text(path)
    except Exception as e:                       # 不是 zip / 坏了 / 权限
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec
    try:
        doc = parse_docx(path)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    plains = [t.strip() for t in plains_all]
    rec["ok"] = True
    rec["chars"] = sum(len(t) for t in plains if t) + sum(len(t) for t in fntexts)
    rec["title"] = doc.title
    rec["author"] = doc.author
    rec["n_headings"] = len(doc.headings)
    rec["n_bib"] = len(doc.bib_entries) + len(doc.refs)

    ri, re_, rh = ref_section(plains)
    rec["ref_head"] = rh
    ev = evidence(plains, fntexts, (ri, re_, rh))
    rec["ev"] = ev

    ts = loc_tail_start(plains, ri)
    rec["tail_found"] = ts >= 0
    rec["tail"] = parse_tail_metadata(plains_all, ts) if ts >= 0 else {}

    def val(name: str) -> str:
        if name == "subtitle":
            return doc.subtitle or doc.subtitle_en or ""
        if name == "affiliation":
            return doc.affiliation_cn or doc.affiliation_en or ""
        return getattr(doc, name, "") or ""

    for name, label in FIELDS:
        got = bool(val(name).strip())
        rec["status"][name] = "ok" if got else ("fail" if ev.get(name) else "absent")
        if not got:
            rec["missing"].append(label)

    body_end = max(ri - 1, 0) if ri >= 0 else len(plains)   # 文献区（含标题段）之前算正文
    kinds: collections.Counter = collections.Counter()
    for t in plains[:body_end]:
        if t:
            kinds.update(head_kinds(t))
    rec["head_kinds"] = dict(kinds)
    rec["head_parsed"] = bool(doc.headings)

    entries = split_entries(plains[ri:re_]) if ri >= 0 else []
    style, cnt = classify_bib(entries, ev["n_fn_ref"])
    rec["bib_style"] = style
    rec["n_entries"] = len(entries)
    rec["entry_mix"] = dict(cnt)
    rec["num_item_lines"] = sum(1 for t in plains[body_end:] if NUM_ITEM_RE.match(t))
    rec["num_n"] = sum(1 for t in entries if SQUARE_ITEM_RE.match(t) and YEAR_RE.search(t))
    rec["quote_n"] = sum(1 for t in entries
                         if SQUARE_ITEM_RE.match(t) and YEAR_RE.search(t) and QUOTE_RE.search(t))

    # 用既有 bibcheck 量「解析出来的条目本身有多少必补字段 / 未结构化」
    try:
        parts = []
        if doc.refs:
            parts.append(refs_to_bib(doc.refs))
        for key, ent, rawnote in doc.bib_entries:
            parts.append(entry_to_bib(key, ent, rawnote))
        bib_text = "\n".join(parts)
        if bib_text.strip():
            rep = bibcheck.check(bib_text)
            rec["bib_total"] = rep.total
            rec["bib_must"] = rep.n_must
            rec["bib_unstructured"] = rep.n_unstructured
    except Exception as e:
        rec["bibcheck_error"] = f"{type(e).__name__}: {e}"

    rec["aux"] = bool(rec["chars"] < 3000 and not ev["ref_section"]
                      and not ev["abstract_cn"] and not ev["abstract_en"])
    return rec


# ══ 输出 ═══════════════════════════════════════════════════════════════════

CSV_COLUMNS = [
    "issue", "slug", "file", "字符数",
    "title_ok", "subtitle_ok", "author_ok", "affiliation_ok", "email_ok",
    "abstract_cn_ok", "keywords_cn_ok", "title_en_ok", "abstract_en_ok",
    "n_headings", "n_bib", "bib_style", "缺失字段列表",
    # 三态列（ok/fail/absent）**追加在末尾**，不动上面 17 列的位置：
    # 老消费者按列号取值也不会错位。加这 9 列的原因见 README 第 48 条——
    # 报告里多处写「逐篇状态见 out/docx_audit.csv」，但只有 `_ok` 列时
    # `fail`（原稿有、解析器没拿到）与 `absent`（原稿本来就没有）**分不开**，
    # 那句指引落不了地（task-4 验收 follow-up a）。
    *[f"{name}_state" for name, _ in FIELDS],
]


def write_csv(records: list[dict]) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in records:
            if not r["ok"]:
                continue
            w.writerow([
                r["issue"], r["slug"], r["file"], r["chars"],
                *[1 if r["status"][n] == "ok" else 0 for n, _ in FIELDS],
                r["n_headings"], r["n_bib"], r["bib_style"],
                "|".join(r["missing"]),
                # 与上面 `_ok` 列同源，同一个 r["status"]；三态互斥（见报告开头口径）
                *[r["status"][n] for n, _ in FIELDS],
            ])


def primary_heading_style(kinds: dict) -> str:
    if not kinds:
        return "无标题"
    best, best_w = "无标题", -1
    for name, w in HEAD_PRIORITY:
        if kinds.get(name) and w > best_w:
            best, best_w = name, w
    return best


def build_report(records: list[dict], others: list[dict]) -> str:
    n = len(ok := [r for r in records if r["ok"]])
    bad = [r for r in records if not r["ok"]]
    L: list[str] = []
    add = L.append

    add("══ 全库 Word 源稿体例普查 ══")
    add("")
    add("【口径，先看这段再看数字】")
    add("  · 范围：<期号>/<稿件>/*.docx 与 <期号>/<稿件>/original/**/*.docx 两处；")
    add("    跳过 ._* / ~$* / .* / __MACOSX。macOS 大小写不敏感，original 实际也含 Original/。")
    add("    口径外的 docx（原文/、origin/、校对/…）不计入分母，见文末附录⑧（附聚合体例分布）。")
    add(f"  · 篇数：命中 {len(records)} 份，成功解析 {n} 份、解析失败 {len(bad)} 份；")
    add("    分母一律用**成功解析**篇数；解析失败的单列，不与「本来就没有」混算。")
    add("  · 字段三态（互斥，相加 = 成功解析篇数）：")
    add("      ok     = 解析拿到了")
    add("      fail   = 原文有证据但解析没拿到（解析失败，值得写兼容）")
    add("      absent = 原文也没有（本来就没有，不必写兼容）")
    add("    fail 是**保守**判据：email/摘要/关键词的判据很硬，author/单位/title_en 是形态判据。")
    add("  · 同一篇稿件常有多份 docx（多版本/分文件），本表按**文件**计，不按稿件去重。")
    add("")

    add("── ① 打不开 / 解析失败的 docx ──")
    if not bad:
        add("  无")
    for r in bad:
        add(f"  {r['file']}：{r['error'][:80]}")

    add("")
    add(f"── ② 字段识别率（分母 = 成功解析的 {n} 篇）──")
    add("")
    add(f"  {'字段':<10}{'识别成功':>10}{'解析失败':>10}{'本来就没有':>12}{'识别率':>8}")
    for name, label in FIELDS:
        good = sum(1 for r in ok if r["status"][name] == "ok")
        fail = sum(1 for r in ok if r["status"][name] == "fail")
        absent = n - good - fail
        add(f"  {label:<10}{f'{good}/{n}':>10}{fail:>10}{absent:>12}"
            f"{(f'{100.0 * good / n:.0f}%' if n else '-'):>8}")
    add("")
    aux = [r for r in ok if r["aux"]]
    if aux:
        add(f"  （参考）扣掉 ⑦ 的 {len(aux)} 篇辅助/说明类文档后：")
        for name, label in FIELDS:
            sub = [r for r in ok if not r["aux"]]
            good = sum(1 for r in sub if r["status"][name] == "ok")
            fail = sum(1 for r in sub if r["status"][name] == "fail")
            if sub:
                add(f"      {label:<10}{f'{good}/{len(sub)}':>10}{fail:>10}"
                    f"{(f'{100.0 * good / len(sub):.0f}%' if sub else '-'):>8}")

    add("")
    add("── ③ 参考文献识别 ──")
    ev_ref = sum(1 for r in ok if r["ev"]["ref_section"])
    ref_parsed = [r for r in ok if r["n_bib"] > 0]
    ref_zero = [r for r in ok if r["n_bib"] == 0]
    ref_fail = [r for r in ref_zero if r["ev"]["ref_section"]]
    add(f"  原文有文献区证据的：{ev_ref}/{n}")
    add(f"  解析出 ≥1 条条目的：{len(ref_parsed)}/{n}")
    add(f"  一条都没解析出来的：{len(ref_zero)} 篇，其中**原文其实有文献区**的 "
        f"{len(ref_fail)} 篇（= 真解析失败）")
    add(f"  文献来自脚注的：{sum(1 for r in ok if r['bib_style'].startswith('脚注体例'))} 篇")
    add(f"  解析出的条目合计：{sum(r['n_bib'] for r in ok)} 条；"
        f"经 bibcheck 离线核对，必补字段 {sum(r['bib_must'] for r in ok)} 项、"
        f"「整条在 note 里未结构化」{sum(r['bib_unstructured'] for r in ok)} 项")

    add("")
    add("── ④ 节标题体例分布 ──")
    add("")
    add("  (a) 按篇：每篇取层级最高的一种（相加 = 篇数）")
    prim = collections.Counter(primary_heading_style(r["head_kinds"]) for r in ok)
    for name, _w in HEAD_PRIORITY:
        if prim.get(name):
            add(f"      {name:<10}{prim[name]:>4} 篇")
    add(f"      {'无标题':<10}{prim.get('无标题', 0):>4} 篇")
    add("")
    add("  (b) 按命中（一篇可命中多种，用于看混用）")
    bykind: collections.Counter = collections.Counter()
    for r in ok:
        for k in r["head_kinds"]:
            bykind[k] += 1
    for name, _w in HEAD_PRIORITY:
        if bykind.get(name):
            add(f"      {name:<10}{bykind[name]:>4} 篇")
    add("")
    n_hp = sum(1 for r in ok if r["head_parsed"])
    raw_has = sum(1 for r in ok if r["head_kinds"])
    fail_head = [r for r in ok if r["head_kinds"] and not r["head_parsed"]]
    add(f"  解析器认出节标题的：{n_hp}/{n} 篇；原文实测有编号标题的：{raw_has}/{n} 篇；")
    add(f"  原文有、解析器一个都没认出的：{len(fail_head)} 篇")
    for r in fail_head:
        add(f"      - {r['file']}（原文实测：{','.join(sorted(r['head_kinds']))}）")

    add("")
    add("── ⑤ 参考文献体例分布 ──")
    add("")
    st = collections.Counter(r["bib_style"] for r in ok)
    add("  (a) 细分（判据见文末）")
    for name in STYLE_ORDER:
        if st.get(name):
            add(f"      {name:<18}{st[name]:>4} 篇")
    for name, v in st.most_common():
        if name not in STYLE_ORDER:
            add(f"      {name[:58]:<18}{v:>4} 篇")
    add("")
    add("  (b) 粗族（决定「要不要写兼容」看这张）")
    fam = collections.Counter(STYLE_FAMILY.get(r["bib_style"], "其他") for r in ok)
    for name, v in fam.most_common():
        add(f"      {name:<18}{v:>4} 篇")
    add("")
    bdocs = [r for r in ok if r["bib_style"] == "编号+引号题名"]
    mixed_share = [r for r in bdocs if r["num_n"] and 0.25 <= r["quote_n"] / r["num_n"] <= 0.75]
    add("  注：b「编号+引号题名」与 d「编号+西文人年(无引号)」不是两种排版体例，而是")
    add("      同一份 `[N] 作者, 年, 题名, 出版信息` 清单的两种题名写法——期刊条目按本刊")
    add("      惯例给题名加引号，专著条目不加，**同一篇里两种并存是常态**。")
    add(f"      本表按「清单里有没有引号题名」二分：有 → b（{len(bdocs)} 篇，其中 "
        f"{len(mixed_share)} 篇引号占比在 25%–75% 之间）、一条都没有 → d。")
    uniq = [r for r in ok if r["bib_style"] == "其他:混排"]
    if uniq:
        add(f"  篇内条目体例拿不到 60% 多数票、判 其他:混排 的：{len(uniq)} 篇（见⑥）")
    trans = [r for r in ok if r["entry_mix"].get("中文译著")]
    if trans:
        add(f"  清单里**含**中文译著/整理本条目的：{len(trans)} 篇"
            f"（其中判为篇级体例「中文译著」的 {st.get('中文译著', 0)} 篇）")
        for r in trans[:10]:
            add(f"      - {r['file']}（中文译著条目 {r['entry_mix']['中文译著']} 条）")
        if len(trans) > 10:
            add(f"      … 另 {len(trans) - 10} 篇")

    add("")
    add("── ⑥ 长尾清单（只影响 1–3 篇的体例）──")
    add("  「值不值得为它写兼容」就看这一段：影响面 ≥4 篇的体例不在此列。")
    add("")
    add("  [节标题体例]")
    lt_head = [(k, v) for k, v in bykind.items() if 1 <= v <= 3]
    if not lt_head:
        add("      无")
    for k, v in sorted(lt_head, key=lambda x: (x[1], x[0])):
        add(f"      {k}（{v} 篇）：")
        for r in ok:
            if r["head_kinds"].get(k):
                add(f"          - {r['file']}")
    add("")
    add("  [参考文献体例]")
    lt_bib = [(k, v) for k, v in st.items() if 1 <= v <= 3]
    if not lt_bib:
        add("      无")
    for k, v in sorted(lt_bib, key=lambda x: (x[1], x[0])):
        add(f"      {k}（{v} 篇）：")
        for r in ok:
            if r["bib_style"] == k:
                add(f"          - {r['file']}")
                if k.startswith("其他:") and r["entry_mix"]:
                    add(f"              篇内条目：" + "、".join(
                        f"{a}×{b}" for a, b in sorted(r["entry_mix"].items(), key=lambda x: -x[1])))
    add("")
    add("  [其它长尾]")
    tail_unread = [r for r in ok if not r["tail_found"]
                   and (r["ev"]["title_en"] or r["ev"]["abstract_en"])
                   and r["status"]["abstract_en"] != "ok"]
    tails = [
        (f"原稿有文献区、一条都没解析出来（其中 {sum(1 for r in ref_fail if r['n_entries'])} 篇"
         f"文献区里确实有候选条目，{sum(1 for r in ref_fail if not r['n_entries'])} 篇只有标题/脚注）",
         [r["file"] for r in ref_fail]),
        ("原文有编号标题、解析器一个都没认出",
         [r["file"] for r in fail_head]),
        ("文末英文元数据块没被读取（稿子没有「参考文献」标题时 tail 块不定位）",
         [r["file"] for r in tail_unread]),
        ("原稿有邮箱、解析器没拿到（裸写邮箱/脚注里的邮箱）",
         [r["file"] for r in ok if r["status"]["email"] == "fail"]),
        ("原稿有单位、解析器没拿到（单位在开头块或脚注，parse 只认文末块里的「单位」标签）",
         [r["file"] for r in ok if r["status"]["affiliation"] == "fail"]),
        ("原稿有英文题名、解析器没拿到",
         [r["file"] for r in ok if r["status"]["title_en"] == "fail"]),
        ("原稿有英文摘要、解析器没拿到",
         [r["file"] for r in ok if r["status"]["abstract_en"] == "fail"]),
        ("原稿有中文摘要、解析器没拿到（「摘要」标签独占一段或段首有缩进）",
         [r["file"] for r in ok if r["status"]["abstract_cn"] == "fail"]),
        ("原稿有作者行、解析器没拿到",
         [r["file"] for r in ok if r["status"]["author"] == "fail"]),
    ]
    for label, who in tails:
        add(f"      {label}：{len(who)} 篇")
        for x in who[:10]:
            add(f"          - {x}")
        if len(who) > 10:
            add(f"          … 另 {len(who) - 10} 篇（判据可复现，逐篇状态见 out/docx_audit.csv）")

    add("")
    add(f"── ⑦ 疑似非正文稿（辅助/说明类，{len(aux)} 篇）──")
    add("  判据（仅供参考，不进上面的分母口径）：字符数 <3000 且无文献区证据、无中英文摘要。")
    add("  这些文件混在源稿位置里，看识别率时先扣掉它们更接近真实水平（见②的参考行）。")
    for r in aux:
        add(f"      {r['chars']:>6} 字  {r['file']}")

    add("")
    add(f"── ⑧ 附录：口径外位置的 docx（{len(others)} 份，未计入上面的数字）──")
    add("  同一个稿件目录下、但不在任务书指定两处之内的文件（原文/、origin/、校对/…）。")
    add("  ⚠ 它们很可能是同一篇的源稿，若多数如此，上面的篇数就偏低——是否扩口径由编辑决定。")
    grp: collections.Counter = collections.Counter()
    for r in others:
        base = os.path.join(REPO, r["issue"], r["slug"])
        parts = os.path.relpath(os.path.join(REPO, r["file"]), base).split(os.sep)
        grp[parts[0] if len(parts) > 1 else "(稿件目录下其它文件)"] += 1
    add("  按一级子目录计数：")
    for k, v in grp.most_common():
        add(f"      {k:<26}{v:>4} 份")
    add(f"  这些文件跑同一套体检的聚合结果（只给总数，不进 CSV）：")
    if others:
        add(f"      可解析 {sum(1 for r in others if r['ok'])}/{len(others)}；"
            f"有文献区证据 {sum(1 for r in others if r['ok'] and r['ev']['ref_section'])} 份；"
            f"解析出条目 {sum(r['n_bib'] for r in others if r['ok'])} 条")
        ofam: collections.Counter = collections.Counter()
        for r in others:
            if r["ok"]:
                ofam[STYLE_FAMILY.get(r["bib_style"], "其他")] += 1
        for k, v in ofam.most_common():
            add(f"      {k:<18}{v:>4} 篇")

    add("")
    add("── ⑨ 判据（照这里复核）──")
    add("  1) 字段「原文有没有」的证据（在本脚本自己读出的段落纯文本上判，不看 parse_docx 结果）")
    add("     title      文档至少有一个非空段落")
    add("     subtitle   前 4 段内有以 —/–/- 起头的段落，或全文中存在以破折号起头的非中文行")
    add("     author     题名段之后、第一处「摘要/关键词」标签之前，存在一行像人名的短行：")
    add("                中文 2–4 字姓名（可用空格/顿号连排）或 2–5 个西文名/缩写，")
    add("                总长 ≤40、不含句读与括号、不含单位词、不含 引言/结语/研究… 等标题词。")
    add("                没有摘要标签时退化为「前 9 段」；第一处标签就在第 2 段时改用「前 4 段」。")
    add("                只看元数据区——文末的落款/签名行不算（`absent` 里因此可能漏掉极少数）")
    add("     affiliation 有「一行单位」：含 大学|学院|研究院|研究所|实验室|科学院|哲学系|"
        "Department|University|College|Institute|School of|Academy|Center，")
    add("                长度 ≤160、不含句号、不以句点收尾、不含《》；`[N]`/`N.` 编号行与")
    add("                以破折号起头的副题行一律排除。只查四处：开头元数据区（题名后到第一处")
    add("                摘要/关键词标签）、文末中英元数据块、带 单位：/所属单位：/作者单位：/"
        "affiliation: 标签的行、脚注里不像文献的行——正文句子与文献条目不算")
    add("     email      全文或脚注出现裸邮箱 x@y.z（**不要求**「邮箱：」标签——")
    add("                解析器只认带标签的，这个差别正是要普查出来的东西）")
    add("     abstract_cn / keywords_cn  有「摘要/关键词」标签且该段含汉字")
    add("     abstract_en / keywords_en  有 Abstract/摘要、Keywords/关键词 标签且该段不含汉字")
    add("     title_en   第一处英文摘要/关键词标签之前有 5–200 字的非中文行")
    add("     ref_section 有独占一段的 参考文献/References/Bibliography… 标题（≤25 字），")
    add("                或全篇 ≥2 行 `[N]` 编号行，或脚注里 ≥1 条像文献的条目")
    add("                （footref.looks_like_reference）")
    add("     文献区定位 先取最后一处上述标题；没有标题时退回「文末连续的 `[N]` 行」")
    add("                （半角方括号、行内 ≥25 字或含年份、相邻间隔 ≤5 段，且 ≥2 行）")
    add("  2) 节标题体例（在段落纯文本上直接匹配，不用解析结果）：")
    add("     N 空格     ^\\d{1,2}[ 　]+\\S 且标题正文 2–40 字、无句读、不像「20 世纪」")
    add("     N紧跟汉字  ^\\d{1,2}(?=汉字) + 同上形态约束")
    add("     N.M        ^\\d{1,2}\\.\\d{1,2} + 同上形态约束")
    add("     N. 空格    ^\\d{1,2}\\.[ 　]*\\S + 同上形态约束（英文式一级标题，单列）")
    add("     （一）     ^[（(][一二三四五六七八九十]{1,3}[）)]（不要求无句读：子节标题常带整句）")
    add("     一、       ^[一二三四五六七八九十]{1,3}、")
    add("     N）        ^\\d{1,2}[）)] + 形态约束（全角右括号式子节）")
    add("     无标题     以上都不命中；(a) 表一篇多命中时取层级权重最高者")
    add("  3) 参考文献体例（先按条目签名，再按篇取多数票，需 ≥60% 否则 其他*）：")
    add("     GB/T7714标记         条目含 [M]/[J]/[C]/[D]/[N]/[R]/… 类型标记")
    add("     中文译著              有 [N] 编号 + 含汉字 + 含（著）/（译）/著，/译，角色标记 + 含年份")
    add("     编号+引号题名 (b)     有 [N] 编号 + 含 “ ”/\" 引号 + 含 19xx/20xx 年")
    add("     编号+西文人年(无引号) (d) 有 [N] 编号 + 含年份 + 全篇一条引号题名都没有")
    add("     人年制无编号          无编号 + 含年份（如「A. Capone, 2019, …」）")
    add("     其他:编号无年份       有 [N] 编号但条目里找不到年份")
    add("     其他:N.式编号         用 N./N、 编号而不是 [N]")
    add("     其他:中文无年份       像中文条目但没有年份")
    add("     其他:无法判定         以上都不像")
    add("     其他:混排             篇内条目体例拿不到 60% 多数票（条目构成写在⑥里）")
    add("     脚注体例              文末没有条目清单，条目不来自清单而来自脚注")
    add("     脚注体例+编号清单     两处都有，且清单拿不到 60% 多数票")
    add("     无参考文献            一条条目都没有、脚注里也没有像文献的条目")
    add("     条目切分：编号清单里不以编号起头的行并入上一条；非编号清单按段落一条")
    add("  4) 分母：一律「成功解析的篇数」；打不开的 docx 单列，不计入识别率。")
    add("  5) 局限：① 证据判据保守，absent 里可能藏着少量漏判；② 按文件统计，多版本稿件重复计；")
    add("     ③ 主表未含「原文/Original（大小写之外）/origin」等口径外目录，其聚合结果见附录⑧；")
    add("     ④ 未做人工逐条抽检——长尾每条都给了文件名，便于抽查。")
    add("")
    add(f"明细 CSV：{os.path.relpath(OUT_CSV, REPO)}"
        f"（{n} 行有效数据；每字段三态列 `*_state` = ok/fail/absent，逐篇可查）")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="全库 Word 源稿体例普查（默认写 Engine/out/docx_audit.{csv,txt}）")
    ap.add_argument("--out-dir", default=os.environ.get("SLCN_AUDIT_OUT", ""),
                    help="输出目录（默认 Engine/out/）。**并行跑多份普查时用隔离目录**，"
                         "否则会互相覆盖同一份 csv/txt；也可用环境变量 SLCN_AUDIT_OUT。")
    args = ap.parse_args(argv)
    if REPO is None:
        # 公开仓/CI 上没有稿件库是**正常情况**：说明一句、正常退出，不报错
        print(f"≡ docx_audit：{_paths.describe()}")
        return 0
    if args.out_dir:
        # 只改输出位置，判据/口径一律不变（为了不改动冻结的快照也能复核）
        global OUT_DIR, OUT_CSV, OUT_TXT
        OUT_DIR = os.path.abspath(args.out_dir)
        OUT_CSV = os.path.join(OUT_DIR, "docx_audit.csv")
        OUT_TXT = os.path.join(OUT_DIR, "docx_audit.txt")
    primary, others = iter_sources()
    print(f"口径内源稿 {len(primary)} 份；口径外 docx {len(others)} 份", flush=True)
    records: list[dict] = []
    for i, (issue, slug, path) in enumerate(primary, 1):
        rec = audit_one(issue, slug, path)
        flag = "ok " if rec["ok"] else "ERR"
        print(f"  [{i:3d}/{len(primary)}] {flag} {rec['file'][:66]:68s} "
              f"字 {rec['chars']:>6} 标题 {rec['n_headings']:>2} 文献 {rec['n_bib']:>3} "
              f"{rec['bib_style'][:16]}", flush=True)
        records.append(rec)
    print(f"  跑口径外 {len(others)} 份（只做聚合）…", flush=True)
    extra = [audit_one(i, s, p) for i, s, p in others]
    write_csv(records)
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(build_report(records, extra))
    n_ok = sum(1 for r in records if r["ok"])
    print(f"\n→ {os.path.relpath(OUT_CSV, REPO)}")
    print(f"→ {os.path.relpath(OUT_TXT, REPO)}")
    print(f"口径内成功解析 {n_ok}/{len(records)} 篇")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
