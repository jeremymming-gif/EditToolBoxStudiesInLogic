#!/usr/bin/env python3.14
"""R3 初排暂存、源对照、候选合成及整目录交付。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import time
import tempfile
from pathlib import Path
from typing import Any

from slcn import ai_bib_candidate, bibcheck, clean, coop, convert_compile, latex

__all__ = [
    "add_convert_args",
    "normalize_convert_args",
    "dispatch_taskbook",
    "deliver",
    "preview_typeset",
    "undo_delivery",
]

# ── 常量 ──────────────────────────────────────────────────────────────────
JOURNAL_FILENAME = "delivery-journal.json"
CHECK_NOTE_FILENAME = "AI交付检查说明.md"
UNDO_KEEP_DIRNAME = "undo-keep"

# 旧分片/回填参数：新模式与之互斥（检测属性，兼容 store 与 append 两种 argparse action）。
_LEGACY_MIX_ATTRS = (
    "coop_replies",
    "coop_slice",
    "coop_split",
    "coop_stage",
    "coop_taskbook_only",
    "coop_apply",
    "verify_refs",
    "verify_plan",
)

# 允许保留的人类可读 locator 前缀（仅页码/页码区间，绝不注入任意 TeX）。
_PAGE_PREFIX_RE = re.compile(
    r"(?:p{1,2}\.\s*~?\s*|第\s*)(\d{1,4}(?:\s*[-–—~]\s*\d{1,4})?)\s*[页頁]?",
    re.IGNORECASE,
)
_PURE_PAGE_RE = re.compile(r"\d{1,4}(?:\s*[-–—~]\s*\d{1,4})?")
_PAGE_MARKER_RE = re.compile(r"\bpp?\.|第\s*(?=\d)", re.IGNORECASE)
_PAGE_RANGE_SEPARATOR_RE = re.compile(r"(?<=\d)\s*(?:--|[-–—~])\s*(?=\d)")


# ── 参数适配 ──────────────────────────────────────────────────────────────
def _split_categories(value: Any) -> list[str]:
    """把 ``--ai-execute`` 的逗号串（或已有序列）拆成类别列表。"""
    if value is None:
        return []
    parts: list[str] = []
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            parts.extend(str(item).split(","))
    else:
        parts = str(value).split(",")
    return [p.strip() for p in parts if p and p.strip()]


def _add_arg_if_absent(parser: argparse.ArgumentParser, *names: str, **kwargs: Any):
    """幂等注册：option 串已存在则跳过（父代理会在真实 parser 上二次调用）。"""
    wanted = {n for n in names if isinstance(n, str) and n.startswith("-")}
    for action in getattr(parser, "_actions", ()):
        if wanted.intersection(getattr(action, "option_strings", ())):
            return None
    try:
        return parser.add_argument(*names, **kwargs)
    except argparse.ArgumentError:
        return None


def add_convert_args(parser: argparse.ArgumentParser) -> None:
    """给普通转换 parser 加 R3 新选项；同时在裸 parser 上补齐测试所需的既有 flag。

    真实 ``build_arg_parser()`` 已定义的 flag 会被幂等跳过，不会覆盖其 action/default
    （尤其是 ``--coop-replies`` 的 ``action="append"``）。
    """
    # 三个新选项 —— 固定签名/默认值。
    _add_arg_if_absent(
        parser, "--ai-taskbook-version", type=int, default=None,
        metavar="N", help="R3 任务书 schema 版本；3 = 新版分类执行闭环。",
    )
    _add_arg_if_absent(
        parser, "--ai-execute", type=_split_categories, default=None,
        metavar="CAT,CAT", help="R3 要执行的类别（逗号分隔）：bib_verify,typeset,text,…。",
    )
    _add_arg_if_absent(
        parser, "--ai-work-dir", default=None, metavar="DIR",
        help="R3 任务根目录（任务书/状态/候选副本/撤销保留区均在此）。",
    )

    # 裸 parser 兜底：测试直接在本函数返回的 parser 上 parse 这些既有 flag。
    # 真实 parser 上同名 flag 已存在 → 全部跳过，零副作用。
    _add_arg_if_absent(parser, "--source", default=None, metavar="PATH")
    _add_arg_if_absent(parser, "--out", default=None, metavar="DIR")
    _add_arg_if_absent(parser, "--lang", default=None, metavar="LANG")
    _add_arg_if_absent(parser, "--compile", action="store_true", default=False)
    _add_arg_if_absent(parser, "--source-pdf", action="store_true", default=False)
    _add_arg_if_absent(parser, "--clean", action="store_true", default=False)
    _add_arg_if_absent(parser, "--coop-replies", default=None, metavar="PATH")


def normalize_convert_args(args: argparse.Namespace) -> dict:
    """把 R3 新模式映射到既有暂存第一段；与旧分片/回填参数混用即抛 ``ValueError``。

    非新模式返回 ``{"ai_mode": False, ...}``，交还旧 ``run_coop_taskbook``；
    新模式返回同一个 args 对象（不改 compile/source_pdf/clean 任何选择）。
    """
    version = getattr(args, "ai_taskbook_version", None)
    execute = _split_categories(getattr(args, "ai_execute", None))
    is_new = (version == 3 and bool(execute))
    if not is_new and (version is not None or execute or getattr(args, "ai_work_dir", None)):
        raise ValueError("请同时指定 --ai-taskbook-version 3、--ai-execute 与 --ai-work-dir")
    if not is_new:
        return {"ai_mode": False, "args": args, "version": version, "execute": execute}

    invalid = set(execute) - {"bib_verify", "typeset", "text"}
    if invalid:
        raise ValueError("暂不支持执行类别：" + "、".join(sorted(invalid)))
    mixed = [name for name in _LEGACY_MIX_ATTRS if getattr(args, name, None)]
    if mixed:
        pretty = ", ".join("--" + name.replace("_", "-") for name in mixed)
        raise ValueError(
            "R3 新模式（--ai-taskbook-version 3 + --ai-execute）不能与旧分片/回填参数"
            f"混用：{pretty}"
        )
    return {
        "ai_mode": True,
        "args": args,
        "version": version,
        "execute": list(dict.fromkeys(execute)),
        "work_dir": str(getattr(args, "ai_work_dir", "") or ""),
    }


# ── 第一阶段分流 ──────────────────────────────────────────────────────────
def _overlaps(left: str, right: str) -> bool:
    left, right = Path(left).resolve(), Path(right).resolve()
    return left.is_relative_to(right) or right.is_relative_to(left)


def conversion_stage(work_dir: str, final_dir: str, source: str, *, dry_run=False) -> str:
    if not work_dir.strip():
        raise ValueError("R3 初排需要 --ai-work-dir")
    work = Path(work_dir).resolve()
    if (os.path.islink(final_dir) or _overlaps(work, final_dir)
            or _overlaps(work, source) or _overlaps(final_dir, source)):
        raise ValueError("R3 任务目录须与源稿、最终交付目录分离")
    stage = work / "stage"
    if any(os.path.lexists(path) for path in (stage, work / ".aiw", work / "state.json")):
        raise ValueError("R3 任务目录已占用，请使用新任务目录")
    if not dry_run:
        stage.mkdir(parents=True, exist_ok=False)
    return str(stage)


def _tex_dependencies(path: str, *, depth: int = 0):
    if depth > 6:
        return
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for match in latex._active_input_matches(text):
        name = match.group(1).strip()
        child = os.path.join(os.path.dirname(path), name if os.path.splitext(name)[1] else name + ".tex")
        if os.path.isfile(child):
            if depth < 6:
                yield child
            yield from _tex_dependencies(child, depth=depth + 1)


def freeze_conversion_inputs(source, args, *, template, bib_path, metadata_paths):
    source = os.path.abspath(source)
    metas = [path for path in metadata_paths if path and os.path.isfile(path)]
    tex_sources = [path for path in [source, *metas] if path.lower().endswith(".tex")]
    files = [source, *metas, template.sty, template.skeleton]
    files.extend(path for path in (bib_path, getattr(args, "refs", None))
                 if path and os.path.isfile(path))
    for tex_source in tex_sources:
        files.extend(_tex_dependencies(tex_source))
    if source in tex_sources:
        root = os.path.dirname(source)
        expanded = latex.expand_inputs(source)
        preamble = latex.expand_inputs(metas[-1]) if metas and metas[-1] in tex_sources else expanded
        search_dirs = latex.graphics_search_dirs(preamble) + [""]
        for raw in latex.includegraphics_paths(expanded):
            name = raw.strip().strip('"')
            for directory in search_dirs:
                proposed = os.path.join(root, directory, name)
                extensions = ("",) if os.path.splitext(proposed)[1] else latex._GRAPHIC_EXTS
                found = next((proposed + ext for ext in extensions
                              if os.path.isfile(proposed + ext)), "")
                if found:
                    if not Path(os.path.abspath(found)).is_relative_to(root):
                        raise ValueError("R3 初排图片路径超出源目录，无法安全暂存：" + name)
                    files.append(found)
                    break
    files = dict.fromkeys(os.path.abspath(path) for path in files)
    return {"source": source, "files": {path: coop.sha256_file(path) for path in files},
            "fingerprint": coop.input_fingerprint(source, args)}


def _validate_inputs(inputs: dict) -> None:
    for path, digest in inputs["files"].items():
        if not os.path.isfile(path) or coop.sha256_file(path) != digest:
            raise ValueError(f"冻结输入已变化：{os.path.basename(path)}")


def _cited_keys_of(tex_path: str) -> set[str]:
    """产物 .tex 正文实际引用的键（LaTeX 与 DOCX 两入口统一口径）。

    DOCX 通道在渲染时已把正文引用就地改写成 `\\parencite{键}`（编号清单通道
    `link_numbered_citations` / 人年制 `link_citations`），所以这里读到的是
    **正文真的用到**的键，而不是文末文献区的全部键。`\\nocite{*}` 只作信号，
    不进范围（`*` 被丢掉）。
    """
    keys = bibcheck.cited_keys(_read(tex_path))
    keys.discard("*")
    return keys


def _read(path: str) -> str:
    return Path(path).read_bytes().decode("utf-8", errors="replace") if path else ""


# 文末编号清单的条目原文前缀（`[1] Alpha, A. …`）。全角方括号也认（Word 里混用很常见）。
_NUM_REF_PREFIX = re.compile(r"^\s*[\[［]\s*(\d{1,3})\s*[\]］]")

# 正文里的纯文本编号引用。方括号里可以有多个编号段，段间用逗号/顿号/分号
# （半角或全角）分隔；段内可用区间符表示**闭区间**。实测源稿出现过这些写法：
#   `[1]` `[1,2]` `[1，2]` `[1、2]` `[1;2]` `[1-3]` `[1–3]`（en dash）
#   `[1—3]`（em dash）`[1~3]` `[1～3]` `[1－3]`（全角减）`[1−3]`（U+2212）
#   `［1-3］`（全角方括号）`[1, 2-4]`（列表里混区间）`[1] and [2-3]`（多个方括号）
#   `[1]-[3]` / `[1]–[3]`（区间两端各带一个方括号）`[1--3]`（TeX 的 en dash）
#   `[1至3]` / `[1到3]`（中文区间词）
# 也认「编号 + 定位符」共存（`[1, p. 398]`、`[1，第 398 页]`）：取编号部分，**不**把
# 页码/卷页当编号（口径：支持的定位符继续用现有识别，不猜）。
_NUM_LIST_SEP = r"[，,、;；]"
_NUM_RANGE_SEP = r"(?:---|--|[-–—~～－−]|至|到)"
_NUM_PIECE = r"\d{1,3}(?:\s*%s\s*\d{1,3})?" % _NUM_RANGE_SEP
_NUM_BRACKET = r"[\[［]"
_NUM_BRACKET_END = r"[\]］]"
# 方括号里到底是什么，先粗取出来再逐段解析：这样 `1, p. 398` 这种「编号 + 定位符」
# 共存形态能取到 `1`，而 `[sic]`、`[Ed.]` 这类非编号内容自然解析不出编号（不置位）。
_BODY_NUM_BRACKET = re.compile(
    r"%s\s*(?P<inner>[^\[\]［］]{1,80}?)\s*%s" % (_NUM_BRACKET, _NUM_BRACKET_END)
)
# 区间两端各带一个方括号：`[1]-[3]`、`[1]–[3]`、`[1] 至 [3]`。这类写法单独扫一遍
# 把中间那段补上（`[1]`、`[3]` 本身仍会被 `_BODY_NUM_BRACKET` 各自命中，取并集）。
_BODY_NUM_SPAN = re.compile(
    r"%s\s*(\d{1,3})\s*%s\s*%s\s*%s\s*(\d{1,3})\s*%s"
    % (_NUM_BRACKET, _NUM_BRACKET_END, _NUM_RANGE_SEP, _NUM_BRACKET, _NUM_BRACKET_END)
)
# 一段里的**前导**编号（后面允许跟定位符残文）：`1` / `1-3` / `1 p. 398`。
_NUM_LEADING = re.compile(
    r"^\s*(\d{1,3})(?!\d)(?:\s*%s\s*(\d{1,3})(?!\d))?" % _NUM_RANGE_SEP
)
# 非方括号编号形态。它们**是**编号标记（松判据要认），但**不**据此解析成引用键：
# Vancouver `(1)` 与夹注年份 `(2020)` 同形、圈码与清单编号的对应各刊不一，
# 猜错就会把「未确认引用」当「已引用」（红线 4 反方向）。判据松、委派严。
_PAREN_NUM = re.compile(r"\(\s*(\d{1,3})\s*\)")
_CIRCLED_NUM = re.compile(r"[\u2460-\u2473]")

# 产物 tex 的正文是**转义后**的 LaTeX：源稿里的 `~` 会变成 `\textasciitilde{}`，
# 于是 `[1~3]` 在正文里写作 `[1\textasciitilde{}3]`。识别前先把这几个转义还原，
# 否则区间符被藏进宏里，整段判不出（V-A 的 `range_tilde` 用例就是这么漏的）。
_TEX_CHAR_ESCAPES = (
    ("\\textasciitilde{}", "~"), ("\\textasciitilde", "~"), ("\\~{}", "~"),
    ("\\textendash{}", "–"), ("\\textendash", "–"),
    ("\\textemdash{}", "—"), ("\\textemdash", "—"),
    ("\\textminus{}", "−"), ("\\textminus", "−"),
)


def _num_scan_text(body_text: str) -> str:
    """编号识别用的正文：先剥 LaTeX 注释，再还原会藏住区间符的字符转义。"""
    text = bibcheck.strip_comments(str(body_text or ""))
    for escaped, plain in _TEX_CHAR_ESCAPES:
        text = text.replace(escaped, plain)
    return text


def _expand_number_piece(piece: str) -> list[int]:
    r"""把 `3` / `1-3` / `1–3` 之类展开成编号列表（区间是闭区间，逐号展开）。

    逆序区间（`3-1`）不猜语义，按「两个端点」处理并交由调用方记入未匹配集合。
    """
    match = re.fullmatch(r"\s*(\d{1,3})\s*(?:%s\s*(\d{1,3}))?\s*" % _NUM_RANGE_SEP, piece)
    if not match:
        return []
    start = int(match.group(1))
    if match.group(2) is None:
        return [start]
    end = int(match.group(2))
    if end < start:
        return [start, end]
    return list(range(start, end + 1))


def _piece_numbers(piece: str) -> list[int]:
    r"""一段编号文字 → 编号列表。整段是编号/区间就整段展开；否则只取**前导**编号。

    后者覆盖「编号 + 定位符」共存（`1, p. 398` 被切段后 `p. 398` 无前导编号 →
    空；`1 p. 398` 未切段时取到 `1`）。定位符里的页码因此不会被当成编号。
    """
    numbers = _expand_number_piece(piece)
    if numbers:
        return numbers
    match = _NUM_LEADING.match(piece)
    if not match:
        return []
    return _expand_number_piece(match.group(0))


def _number_cited_keys(raw_by_key, body_text: str) -> tuple[set[str], set[int]]:
    r"""编号清单**没有**「编号→键」映射时，用条目原文前缀 + 正文编号反推被引用键。

    实况：Word 源稿常见「文末是纯文本 `[1] …` 清单、正文写 `[1]`」，这时
    `doc.ref_key_by_num` 为空（`wordbody` 的编号通道要求条目数严格多于人年制通道），
    产物 tex 正文里也就没有 `\parencite`，`_cited_keys_of()` 读不到任何键。

    返回 `(被引用键集合, 正文里出现但对应不到条目的编号集合)`。第二个返回值是
    「判不全」的证据：区间/列表里只要有一个号对不上清单，就不能假装范围已经判全
    （否则漏掉的条目会在第二段被当成「未引用键」丢掉，见 `synthesize_bib`）。
    """
    num2key: dict[int, str] = {}
    for key, text in (raw_by_key or {}).items():
        match = _NUM_REF_PREFIX.match(str(text or ""))
        if match:
            num2key.setdefault(int(match.group(1)), str(key))
    cited: set[str] = set()
    unmatched: set[int] = set()
    searchable = _num_scan_text(body_text)

    def absorb(numbers) -> None:
        for num in numbers:
            key = num2key.get(num)
            if key is None:
                unmatched.add(num)
            else:
                cited.add(key)

    for match in _BODY_NUM_BRACKET.finditer(searchable):
        for piece in re.split(r"\s*%s\s*" % _NUM_LIST_SEP, match.group("inner")):
            absorb(_piece_numbers(piece))
    # `[1]-[3]` 这类区间两端各带方括号：补中间那段（端点本来就会被上面命中）。
    for match in _BODY_NUM_SPAN.finditer(searchable):
        absorb(_expand_number_piece("%s-%s" % (match.group(1), match.group(2))))
    return cited, unmatched


_FOOTNOTE_HEAD = re.compile(r"\\(?:footnote|footnotetext)\s*(?:\[[^\]]*\])?\s*\{")
_CITE_CUE = re.compile(r"(?:参见|参阅|见|转引自|引自|Cf\.|cf\.|See|see|e\.g\.|例如)")
_YEAR = re.compile(r"(?:19|20)\d{2}")
_AUTHOR_YEAR_LATIN = re.compile(
    r"([A-Z][A-Za-z'\-]{1,30})\s*(?:等|et al\.?)?\s*[，,]\s*((?:19|20)\d{2})")
_AUTHOR_YEAR_CJK = re.compile(
    r"([\u4e00-\u9fff]{2,4})\s*[，,]?\s*((?:19|20)\d{2})")


def _footnote_bodies(tex_text: str) -> list[str]:
    r"""产物 tex 里所有 `\\footnote{…}` / `\\footnotetext{…}` 的**花括号内原文**。

    Word 通道的脚注引用在产物 tex 里是纯文本（`\\footnote{参见 Alpha，2020，第 5 页。}`），
    没有 `\\parencite`，所以 `bibcheck.cited_keys()` 读不到——引用范围必须单独扫脚注体，
    否则「正文只在脚注里引用」的稿子会被判成零引用（红线 2 泄漏面）。
    """
    text = str(tex_text or "")
    bodies: list[str] = []
    for match in _FOOTNOTE_HEAD.finditer(text):
        depth = 1
        index = match.end()
        start = index
        while index < len(text) and depth:
            char = text[index]
            if char == "\\":
                index += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if not depth:
                    break
            index += 1
        if depth == 0:
            bodies.append(text[start:index])
    return bodies


def _author_year_index(raw_by_key) -> dict[tuple[str, str], str]:
    r"""`(姓, 年) → 键` 索引，供脚注里的作者-年引用映射回条目键。

    姓与年都**只从条目原文抽**（`[1] Alpha, A. 2020. …`），不编造：抽不出就不建索引项。
    """
    index: dict[tuple[str, str], str] = {}
    for key, text in (raw_by_key or {}).items():
        raw = str(text or "")
        year = _YEAR.search(raw)
        if not year:
            continue
        tail = re.sub(r"^\s*[\[［]\s*\d{1,3}\s*[\]］]\s*", "", raw).strip()
        surname = re.split(r"[,，.。]", tail)[0].strip()
        surname = re.sub(r"^(?:[\u4e00-\u9fff]{0,2}著|by)\s*", "", surname).strip()
        if not surname or len(surname) > 30:
            continue
        index.setdefault((surname.lower(), year.group(0)), str(key))
    return index


def _footnote_cited_keys(raw_by_key, tex_text: str) -> tuple[set[str], bool]:
    r"""脚注体里能可靠映射到条目的引用键 + 「像引用却判不出」标记。

    两档：① 脚注里带方括号编号 → 复用 `_number_cited_keys`；
          ② 作者-年（`参见 Alpha，2020，第 5 页。`）→ 查 `_author_year_index`。
    判不出但**看得出像引用**（有引导语或年份）时返回 `True`，由调用方置
    `scope_unresolved`（零委派 + 提示人工核对），**不**退回全量。
    """
    cited: set[str] = set()
    unresolved = False
    if not raw_by_key:
        # LaTeX 通道没有「文末原文逐条留档」：没有映射表，但脚注里仍可能看得出是
        # 引用（引导语/年份）。判不出就置 unresolved，让任务书如实说「判不出」，
        # 而不是说「未识别到可核引用」——后者会让作者以为稿子真没引用。
        # **只置标记、不解析任何键**：委派仍为零（判据松、委派严）。
        for body in _footnote_bodies(tex_text):
            if _CITE_CUE.search(body) or _YEAR.search(body):
                unresolved = True
        return cited, unresolved
    index = _author_year_index(raw_by_key)
    for body in _footnote_bodies(tex_text):
        numbers, unmatched = _number_cited_keys(raw_by_key, body)
        if numbers or unmatched:
            cited |= numbers
            unresolved = unresolved or bool(unmatched)
            continue
        hits: set[str] = set()
        for match in _AUTHOR_YEAR_LATIN.finditer(body):
            key = index.get((match.group(1).lower(), match.group(2)))
            if key:
                hits.add(key)
        for match in _AUTHOR_YEAR_CJK.finditer(body):
            key = index.get((match.group(1).lower(), match.group(2)))
            if key:
                hits.add(key)
        if hits:
            cited |= hits
        elif _CITE_CUE.search(body) or _YEAR.search(body):
            # 有引导语/年份却没有可映射的条目：判不出，交给第二道闸（零委派）。
            unresolved = True
    return cited, unresolved


def _has_number_marks(body_text: str) -> bool:
    r"""正文里是否有编号标记（松判据：方括号编号 / `(N)` / 圈码）。

    只回答「有没有标记」，**不**回答「能不能解析成引用键」——判据松、委派严：
    有标记而解析不出时走 `scope_unresolved`（零委派 + 提示），绝不退回全量。
    """
    text = _num_scan_text(body_text)
    for match in _BODY_NUM_BRACKET.finditer(text):
        if _piece_numbers(str(match.group("inner")).split("\n")[0]):
            return True
    return bool(_PAREN_NUM.search(text) or _CIRCLED_NUM.search(text))


def _has_unresolvable_marks(body_text: str) -> bool:
    r"""正文里是否有「本轮**有意**不解析成引用键」的编号形态（`(N)` / 圈码）。

    Vancouver `(1)` 与夹注年份 `(2020)` 同形、圈码与清单编号的对应各刊不一；
    无差别认普通括号会把年份当编号、把未确认引用当已引用（红线 4 反方向）。
    所以这类形态只用来置位 `scope_unresolved`（判不出 → 零委派 + 提示人工核对）。
    """
    text = _num_scan_text(body_text)
    return bool(_PAREN_NUM.search(text) or _CIRCLED_NUM.search(text))


_UNCITED_DETAIL_HINT = "未被正文链路引用"


def _scope_warning(warning: str) -> str:
    r"""转换告警可以给线索，但**不得夹带未引用条目的原文**。

    实况：`bibnorm.end_list_uncited_warning()` 会写「文末有 N 条未被正文链路引用…
    ：Beta2021（标题. 刊名.）」——那串明细正是未引用条目的原文（Word 人年制稿
    必然触发）。范围裁剪（B-222 A）保留「有 N 条未引用」这个线索，把明细抹掉。
    """
    text = str(warning or "")
    if _UNCITED_DETAIL_HINT in text and "：" in text:
        head = text.split("：", 1)[0]
        return f"{head}：（明细不在此披露）未引用条目原文不进任务书。"
    return text


def _strip_bibliography(source_text: str) -> str:
    r"""源侧正文材料里**去掉**参考文献环境本身（条目原文另有 REF-SOURCE 按引用提供）。

    范围裁剪（B-222 A）：`\begin{thebibliography}…\end{thebibliography}` 里的
    `\bibitem` 是**文献条目原文**，不是待校对的正文；整段送进「源侧正文」等于把
    未被正文引用的条目也递给 AI。这里只留一行说明，条目仍按引用键走 REF-SOURCE。
    """
    text = str(source_text or "")
    if not text.strip():
        return text
    note = "% [范围裁剪] 源稿参考文献区（thebibliography）不在此重复；被正文引用的条目另见 REF-SOURCE。"
    text = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}",
                  note, text, flags=re.S)
    return text


def _placeholder(value: str) -> bool:
    value = value.strip()
    return not value or value == "null" or value in coop._TEMPLATE_PLACEHOLDERS or bool(
        coop.PLACEHOLDER_MARK_RE.search(value))


def _source_material(source, metadata_paths, lang):
    from slcn import docxsource, metadocx, texfront

    source_text = ""
    source_fields = {}
    support = {}
    for index, path in enumerate(dict.fromkeys([source, *metadata_paths]), start=1):
        if path.lower().endswith(".tex"):
            raw = latex.expand_inputs(path)
            values = {key: macro.value for key, macro in latex.parse_macros(raw).items()
                      if key.startswith("my") or key in coop.EDITORIAL_TEX_FIELDS}
            front = texfront.parse_tex_frontmatter(raw)
            if front and front.title:
                values.setdefault("mytitle", front.title)
            if path == source:
                source_text = latex.split_body(raw)[1]
        elif path.lower().endswith(".docx"):
            from zipfile import BadZipFile
            try:
                doc = docxsource.parse_docx(path)
            except (OSError, ValueError, BadZipFile):
                continue
            if path == source:
                source_text = doc.body
            values = metadocx._macros_for(doc, lang=lang)
        else:
            continue
        for macro, value in values.items():
            value = value.strip()
            if _placeholder(value):
                continue
            evidence_id = f"SRC-META-{index}-{macro}"
            support.setdefault(macro, {})[evidence_id] = value
            source_fields[evidence_id] = {
                "位置": macro, "值": value, "材料侧别": "源稿" if path == source else "元数据附件",
            }
    return source_text, source_fields, support


def _taskbook_evidence(args, res, *, execution, tex_path, source, bib_source,
                       metadata_paths, lang):
    """Reuse conversion data already in memory and the frozen output fields."""
    evidence: dict[str, Any] = {}
    registered = {"fn_items": [], "meta_items": []}
    body = str(getattr(res, "body_text", "") or "")
    has_body = bool(body.strip())
    with_body = "typeset" in execution or "text" in execution
    if with_body and has_body:
        evidence["body_units"] = [{"unit_id": "BODY-1", "text": body, "side": "转换侧正文"}]

    if "bib_verify" in execution or "typeset" in execution:
        # R3 第一段范围裁剪（B-222 A）：任务书只暴露**源稿正文实际引用**的条目原文。
        # 旧实现在这里把整份 .bib / 整段 Word 文末文献区原文（`ref_raw_lines`）塞进
        # `raw_refs`，作者额外 Bib 条目、未被引用的尾部参考文献因此全被送给 AI。
        # 现在只用「正文实际引用键」过滤「条目 → 源稿原文」映射，且**不再产出
        # `raw_refs`**（无键可映射时给缺件提示，绝不整段倾倒）。
        cited = _cited_keys_of(tex_path)
        raw_by_key = dict(getattr(res, "ref_raw_by_key", {}) or {})
        unresolved: set[int] = set()
        if not cited and raw_by_key:
            # Word 纯文本 `[N]` 清单：产物 tex 正文没有 `\parencite`，用条目原文的
            # `[N]` 前缀与正文 `[N]` 反推被引用键（见 `_number_cited_keys`）。
            cited, unresolved = _number_cited_keys(raw_by_key, body)
        # 脚注里的引用：Word 通道的脚注体在产物 tex 里是纯文本（没有 `\parencite`），
        # 只扫正文会把「只在脚注里引用」的稿子判成零引用。这里单独扫脚注体，
        # 编号形态与作者-年形态都试；看得出像引用却映射不到条目时置 scope_unresolved。
        fn_cited, fn_unresolved = _footnote_cited_keys(
            raw_by_key, _read(tex_path) if os.path.exists(tex_path) else "")
        cited |= fn_cited
        if (unresolved or fn_unresolved or _has_unresolvable_marks(body)
                or (_has_number_marks(body) and not cited)):
            # 第二道闸：正文里**有**编号标记，却判不出（或判不全）编号→条目映射。
            # 这时绝不能退回「保守全量」把全部条目倒给 AI；只按已识别的键裁剪，
            # 并把这个状态显式交给任务书说明（见 `build_context` 的 scope_unresolved）。
            # `_has_unresolvable_marks` 覆盖本轮**有意**不解析的形态（`(N)` / 圈码）：
            # 判据松、委派严——认得出「有标记」，但不猜它对应哪条。
            evidence["scope_unresolved"] = True
        scoped = {key: text for key, text in raw_by_key.items()
                  if key in cited and str(text).strip()}
        if scoped:
            evidence["refs_by_key"] = scoped
        elif cited and bib_source:
            # LaTeX 通道没有「文末原文逐条留档」，源侧原文就在冻结 bib 的条目块里；
            # 只取被引用键的条目块（同样不含未引用条目）。
            raw = _read(bib_source)
            evidence["refs_by_key"] = {
                block.key: block.text for block in coop.parse_bib_blocks(raw)
                if block.closed and block.key in cited
            }
        if cited:
            # 交给 build_context / state_v3 判「引用范围判得出」用，避免退回保守全量。
            evidence["cited_keys"] = sorted(cited)
    if "bib_verify" in execution and has_body:
        generated_contexts: dict[str, str] = {}
        searchable = bibcheck.strip_comments(body)
        for match in bibcheck._CITE.finditer(searchable):
            if with_body:
                snippet = f"BODY-1，正文内引用锚点 {match.group()}"
            else:
                start = searchable.rfind("\n", 0, match.start()) + 1
                end = searchable.find("\n", match.end())
                snippet = searchable[start:] if end < 0 else searchable[start:end]
            for key in bibcheck.cited_keys(match.group()) - {"*"}:
                generated_contexts.setdefault(key, snippet)
        evidence["generated_cite_contexts"] = generated_contexts

    if "typeset" in execution:
        source_body, source_fields, source_support = _source_material(source, metadata_paths, lang)
        if source_body.strip():
            # 范围裁剪（B-222 A）：源侧正文里的参考文献环境整段剔掉——`\bibitem`
            # 是条目原文而非待校对正文，被引用的条目另见 REF-SOURCE。
            evidence.setdefault("body_units", []).insert(
                0, {"unit_id": "SOURCE-BODY", "text": _strip_bibliography(source_body),
                    "side": "源侧正文"})
        warnings = list(getattr(res, "warnings", ()) or ())
        targets = dict(getattr(res, "undetected_fields", ()) or ())
        current = coop.read_tex_field_values(tex_path)
        items = coop.collect_low_confidence(
            warnings=warnings, missing_fields=getattr(res, "missing_fields", ()) or (),
            label_targets=targets, current_fields=current,
            max_items=len(current) + len(targets) + sum(len(w) for w in warnings) + 1,
        )
        paths = [getattr(args, name, None) for name in ("source", "metadata", "out", "ai_work_dir")]
        paths.extend((tex_path, getattr(res, "bib_path", None), getattr(res, "handoff_path", None)))
        names = {part for path in paths if path for part in (str(path), os.path.basename(path)) if part}

        def redact(value: str) -> str:
            value = re.sub(r"或用 --.*? 覆盖", "", value)
            value = re.sub(r"(?<!\w)--[a-z][a-z0-9-]*(?:/--[a-z][a-z0-9-]*)*", "〔本机选项〕", value)
            for name in sorted(names, key=lambda n: (-len(n), n)):
                value = value.replace(name, "〔本机文件〕")
            value = re.sub(r"(?<![\w:/])(?:[A-Za-z]:[\\/]|/)[^\s，；：）]+", "〔本机路径〕", value)
            return re.sub(r"[^\s：，；（）/\\]+\.(?:docx?|tex|bib|pdf|md|log|sty)\b",
                          "〔本机文件〕", value, flags=re.I)

        evidence["conversion_warnings"] = {f"WARN-{index}": _scope_warning(redact(warning))
                                           for index, warning in enumerate(warnings, start=1)}
        evidence["typeset_missing"] = [
            "未取得图表、基金或作者信息逐项对应的部分仍需人工核对。"
        ]
        if not source_fields:
            evidence["typeset_missing"].append("未取得可定位的源侧元数据字段。")
        evidence["current_fields"] = {
            name: current[name] for name in current
            if name in coop.EDITORIAL_TEX_FIELDS or name.startswith(
                ("mytitle", "myshorttitle", "myfirstauthor", "mysecondauthor", "mythirdauthor",
                 "myotherauthor", "myheadtitle", "myrunningtitle", "myfund", "mygrants", "myack", "myauthor")
            ) or name in targets.values()
        }
        evidence["current_fields"]["源字段（按来源登记）"] = source_fields
        override_opts = {
            "vol": ("myvolnumber",), "issue": ("myissnumber",), "year": ("mypubyear",),
            "pages": ("myfirstpage", "mylastpage", "mypages"), "received": ("receiveddate",),
            "editor": ("myeditor",), "grants": ("mygrants", "mythanks"),
        }
        overridden = {macro for option, macros in override_opts.items()
                      if getattr(args, option, None) for macro in macros}
        for item in items:
            if item["kind"] == "footnote":
                registered["fn_items"].append(item)
            elif item["kind"] == "metadata" and item.get("macro") in current:
                macro = item["macro"]
                direct = {} if macro in overridden else source_support.get(macro, {})
                item["source_support"] = direct
                registered["meta_items"].append(item)
        evidence["fn_items"] = [
            {"item_id": item["id"], "位置": item["title"], "转换警告片段": redact(item["evidence"]),
             "可建议": ("footnote_link" if item["subkind"] in coop.FN_APPLICABLE_SUBKINDS
                       and "match_key" in item["fields"] else "report_only")}
            for item in registered["fn_items"]
        ]
        evidence["meta_items"] = [
            {"item_id": item["id"], "字段": item["title"], "位置": item["macro"],
             "当前值": item["current"], "源证据": item["source_support"],
             "可建议": ("metadata_fill" if item["source_support"] and _placeholder(item["current"])
                       and coop._meta_target_macro(item, item["id"]) else "report_only")}
            for item in registered["meta_items"]
        ]
    return evidence, registered


def dispatch_taskbook(args: argparse.Namespace, res: Any, **stage: Any) -> int | None:
    norm = normalize_convert_args(args)
    if not norm["ai_mode"]:
        return None

    from slcn import ai_workflow

    try:
        work = str(Path(norm["work_dir"]).resolve())
        stage_dir = stage["out_dir"]
        tex_path = str(res.tex_path)
        bib_path = str(res.bib_path or "")
        for artifact in filter(None, (tex_path, bib_path)):
            if Path(artifact).resolve().parent != Path(stage_dir):
                raise ValueError("转换产物不在任务暂存目录")
        inputs = stage["inputs"]
        _validate_inputs(inputs)
        if any(_overlaps(stage["final_dir"], path) or _overlaps(stage_dir, path)
               for path in inputs["files"]):
            raise ValueError("原始输入与暂存或最终交付目录重叠")
        execute = norm["execute"]
        evidence, registered = _taskbook_evidence(
            args, res, execution=execute, tex_path=tex_path, source=stage["source"],
            bib_source=stage["bib_source"], metadata_paths=stage["metadata_paths"],
            lang=stage["lang"],
        )
        _validate_inputs(inputs)
        _coop_fp = inputs["fingerprint"]
        coop._write_source_fingerprint(stage_dir, stage["stem"], _coop_fp)
        conversion = {
            "stage_dir": stage_dir, "final_dir": str(Path(stage["final_dir"]).resolve()),
            "stem": stage["stem"], "tex_name": os.path.basename(tex_path),
            "bib_name": os.path.basename(bib_path),
            "stage_sha256s": _tree_shas(stage_dir), "inputs": inputs,
            "options": {name: getattr(args, name, False)
                        for name in ("compile", "source_pdf", "clean", "force")},
            **registered,
        }
        conversion["input_identity"] = {
            "files": [(os.path.basename(path), digest) for path, digest in inputs["files"].items()],
            "stage": conversion["stage_sha256s"],
            "options": conversion["options"],
        }
        prepared = ai_workflow.prepare(
            work_dir=work, entry="convert", bib_text=_read(bib_path),
            tex_text=_read(tex_path), stem=stage["stem"], source_path=stage["source"],
            target_path=bib_path, tex_path=tex_path, rule_version="3",
            scope=ai_workflow.Scope(exec=set(execute), adopt=set()), taskbook_version=3,
            safe_hit_keys=stage["bibdb_info"].get("safe", {}),
            conversion=conversion, source_evidence=evidence,
            request_options={
                "require_search": bool(getattr(args, "search_refs", False)),
                "article_budget": getattr(args, "ai_budget_tokens", 0),
                "batch_budget": getattr(args, "ai_batch_budget_tokens", 0),
                "verify_max": getattr(args, "verify_max", None),
            },
        )
        print("\n⑬ R3 初排·第一段（已暂存，等待回填）")
        print(f"暂存目录：{stage_dir}")
        print(f"task_id={prepared.task_id}")
        print(f"taskbook={prepared.taskbook_path}")
        print(f"work_dir={work}")
        return 0
    except (OSError, ValueError, ai_workflow.WorkflowError) as exc:
        print(f"✗ R3 初排失败：{exc}")
        return 2


# ── locator 安全归一 ──────────────────────────────────────────────────────
def _normalize_locator(raw: Any) -> tuple[str, str, bool]:
    """返回 ``(coop_locator, final_locator, ok)``。

    ``coop.apply_tex_suggestions`` 只接受纯页码/页码区间；人类可读前缀（如 ``p. 1``、
    ``第1页``）先抽出纯页码交给 coop 安全闸，写后再把安全前缀还原，避免把任意文本
    注入 ``\\parencite[…]``。非法 locator 直接拒绝（ok=False）。
    """
    loc = str(raw or "").strip()
    if not loc:
        return "", "", True
    if _PURE_PAGE_RE.fullmatch(loc):
        return loc, loc, True
    m = _PAGE_PREFIX_RE.fullmatch(loc)
    if m:
        return m.group(1), loc, True
    return "", "", False


# ── 真正分类采纳 ──────────────────────────────────────────────────────────
def _edit(before: str, after: str) -> dict:
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    tail = 0
    while tail < min(len(before), len(after)) - start and before[-1 - tail] == after[-1 - tail]:
        tail += 1
    return {"start": start, "end": len(before) - tail, "new": after[start:len(after) - tail]}


def _apply_edits(text, edits):
    for edit in sorted(edits, key=lambda edit: edit["start"], reverse=True):
        text = text[:edit["start"]] + edit["new"] + text[edit["end"]:]
    return text


def _intersects(first, second):
    return (first["start"] < second["end"] and second["start"] < first["end"]
            or first["start"] == second["start"])


def _typeset_edit(tex_text, tex_path, frozen, item, bib_keys):
    action = item.get("action")
    iid = str(item["item_id"])
    applied = {"item_id": iid, "action": action}
    span = None
    if action == "footnote_link":
        if frozen.get("subkind") not in coop.FN_APPLICABLE_SUBKINDS or "match_key" not in frozen["fields"]:
            raise ValueError("脚注没有冻结的引用定位")
        if item.get("field", "match_key") != "match_key" or item.get("old") != frozen["evidence"]:
            raise ValueError("脚注建议与冻结的片段或字段不符")
        key = str(item.get("key") or "")
        pure, displayed, valid = _normalize_locator(item.get("locator"))
        if key not in bib_keys or not valid:
            raise ValueError("匹配键不在最终 Bib，或页码定位无效")
        source_snippet = frozen["evidence"]
        page_hint = _PAGE_MARKER_RE.search(source_snippet) or coop._PAGE_HINT_RE.search(source_snippet)
        source_locator = ""
        if page_hint:
            suffix = source_snippet[page_hint.start():].rstrip(" \t.。")
            suffix = _PAGE_RANGE_SEPARATOR_RE.sub("-", suffix)
            if not _PAGE_MARKER_RE.match(suffix):
                suffix = suffix.removesuffix("页").removesuffix("頁")
            source_locator, _, certain = _normalize_locator(suffix)
            if not certain or not source_locator:
                raise ValueError("原脚注页码无法唯一确定，交人工核对")
        if source_locator != _PAGE_RANGE_SEPARATOR_RE.sub("-", pure):
            raise ValueError("建议页码必须与原脚注页码等价")
        needle = coop._squeeze(coop._tex_escape(source_snippet))
        spans = [(a, b, c) for a, b, c in coop._iter_tex_footnotes(tex_text)
                 if needle and needle == coop._squeeze(
                     re.sub(r"\\[,;:]|(?<!\\)~", " ", tex_text[b:c]))]
        if len(spans) != 1:
            raise ValueError("冻结脚注必须恰好定位一次")
        start, body_start, body_end = spans[0]
        span = (start, body_end + 1)
        if needle not in coop._squeeze(tex_text[body_start:body_end]) and page_hint:
            # 排版器可能在页码中插入细空格。已核对脚注全文，用不变的前缀定位。
            anchor = source_snippet[:page_hint.start()].strip()
            frozen = {**frozen, "evidence": anchor}
        suggestion = {"verdict": "corrected", "field": "match_key", "value": key, "locator": pure}
        applied.update(key=key, locator=displayed)
    elif action == "metadata_fill":
        macro = frozen["macro"]
        current = frozen["current"]
        new = str(item.get("new") or "").strip()
        support = frozen.get("source_support") or {}
        if item.get("target") != macro or str(item.get("old") or "").strip() != current.strip() or not _placeholder(current):
            raise ValueError("仅允许在登记的空值或占位元数据槽补全")
        evidence_ids = item.get("evidence_ids") or []
        if (not new or not isinstance(evidence_ids, list) or not evidence_ids
                or any(not isinstance(eid, str) or support.get(eid) != new for eid in evidence_ids)):
            raise ValueError("新值需要登记的同一源字段直接支持")
        suggestion = {"verdict": "corrected", "value": new}
        applied.update(macro=macro, value=new)
    else:
        raise ValueError("该项只交人工核对")
    coop.atomic_write_text(tex_path, tex_text)
    result = coop.apply_tex_suggestions(tex_path, {iid: [suggestion]}, [frozen],
                                        taskbook_keys=bib_keys, preserve_existing_backup=True)
    candidate = _read(tex_path)
    if not result["applied"] or tex_text == candidate:
        failed = result["skipped"]
        raise ValueError(failed[0]["reason"] if failed else "该项没有可安全写入的变化")
    if span:
        start, end = span
        cut = len(candidate) - (len(tex_text) - end)
        if candidate[:start] != tex_text[:start] or candidate[cut:] != tex_text[end:]:
            raise ValueError("安全应用器的写入位置与冻结脚注不符")
        replacement = candidate[start:cut]
        if displayed != pure and displayed:
            replacement = f"\\parencite[{displayed}]{{{key}}}"
        if item.get("new") != replacement:
            raise ValueError("new 必须与引擎生成的引用完全一致")
        return {"start": start, "end": end, "new": replacement, **applied}
    return {**_edit(tex_text, candidate), **applied}


def preview_typeset(*, state: dict, tex_text: str, bib_text: str, items: list[dict],
                    text_result: dict | None = None) -> dict:
    """在同一冻结 TeX 定位两类局部候选，冲突双方留待人工。"""
    conversion = state["conversion"]
    registered = {item["id"]: item for name in ("fn_items", "meta_items")
                  for item in conversion.get(name, [])}
    bib_keys = set(ai_bib_candidate.entries_by_key(bib_text)[0])
    edits, rejected = [], []
    with tempfile.TemporaryDirectory() as folder:
        tex_path = os.path.join(folder, "candidate.tex")
        for item in items:
            iid = str(item.get("item_id") or "")
            frozen = registered.get(iid) or {}
            try:
                if {"metadata_fill": "metadata", "footnote_link": "footnote"}.get(
                        item.get("action"), "report") != frozen.get("kind"):
                    raise ValueError("未登记的初排项或动作")
                edits.append(_typeset_edit(tex_text, tex_path, frozen, item, bib_keys))
            except (ValueError, KeyError) as exc:
                rejected.append({**item, "reason": str(exc)})
    text_edits = list((text_result or {}).get("applied") or [])
    all_edits = edits + text_edits
    conflicts = {id(a) for index, a in enumerate(all_edits)
                 for j, b in enumerate(all_edits) if index != j and _intersects(a, b)}
    reason = "同一冻结位置涉及多个建议，均交人工核对"
    rejected.extend({**edit, "reason": reason} for edit in edits if id(edit) in conflicts)
    applied = [edit for edit in edits if id(edit) not in conflicts]
    text_applied = [edit for edit in text_edits if id(edit) not in conflicts]
    if text_result is not None:
        text_result = {**text_result, "applied": text_applied, "unchanged": not text_applied,
                       "text": _apply_edits(tex_text, text_applied),
                       "rejected": [*(text_result.get("rejected") or []),
                                    *({**edit, "reason": reason} for edit in text_edits
                                      if id(edit) in conflicts)]}
    return {"text": _apply_edits(tex_text, [*applied, *text_applied]),
            "applied": applied, "rejected": rejected, "text_result": text_result}


#: 系统元数据噪音：Finder 在目录里随手写 `.DS_Store`，与稿件内容无关。
#: 只认**精确名**，不做 `.` 前缀通配 —— `.aiw`/`.ai前`/`previous.txt` 各有既有语义。
#: 交付复制（`_publication_ignore`）与指纹（`_tree_shas`）两处必须一致忽略，否则
#: 复制时被滤掉、指纹却算进去，第一段冻结的 stage 指纹会在交付时对不上，
#: 报出与真实原因无关的「冻结产物已变化」。
_METADATA_NOISE = frozenset({".DS_Store"})


def _tree_shas(root: str) -> dict[str, str]:
    """最终目录的完整文件指纹（相对路径 → sha256），用于撤销前外改检测。"""
    out: dict[str, str] = {}
    if not os.path.isdir(root):
        return out
    if os.path.islink(root):
        raise ValueError("交付目录不能是符号链接")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _METADATA_NOISE)
        filenames = [name for name in filenames if name not in _METADATA_NOISE]
        # 空目录标记：判据是**过滤后**的有效子项。否则「只装了 `.DS_Store` 的目录」
        # 与「真·空目录」指纹不同——Finder 随手写一个 `.DS_Store` 就会让第一段
        # 冻结的 stage 指纹对不上，报出与真实原因无关的「冻结产物已变化」。
        if dirpath != root and not [*dirnames, *filenames]:
            out[Path(dirpath).relative_to(root).as_posix() + "/"] = ""
        for name in [*dirnames, *filenames]:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                raise ValueError(f"交付目录包含符号链接：{name}")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = Path(full).relative_to(root).as_posix()
            out[rel] = coop.sha256_file(full)
    return out


def _shas_delta(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """两个指纹之间**真实**的差异文件名（供错误文案定位，不报泛化结论）。"""
    changed = {rel for rel in before.keys() & after.keys() if before[rel] != after[rel]}
    return sorted(changed | (before.keys() ^ after.keys()))


def _changed_message(before: dict[str, str], after: dict[str, str], *, what: str) -> str:
    """「冻结产物已变化」的可定位版本：点名具体文件，而不是让人猜。"""
    delta = _shas_delta(before, after)
    if not delta:
        return what
    shown = "、".join(delta[:5])
    more = f" 等 {len(delta)} 个文件" if len(delta) > 5 else ""
    return f"{what}：{shown}{more}"

def _render_check_note(state: dict, *, use_original: bool, n_applied: int,
                       adopted: list[dict], deferred: list[dict],
                       compile_status: str) -> str:
    execution = state.get("scope", {}).get("exec", [])
    coverage = state.get("category_results") or {}
    cov_parts = []
    for cat in execution:
        cov_parts.append(f"- {cat}：{coverage.get(cat, {}).get('status', '未记录')}")
    adopt_lines = [f"- {a.get('item_id')}（{a.get('action')}）" for a in adopted] or ["- （无）"]
    defer_lines = []
    for d in deferred:
        loc = f" @ {d['locator']}" if d.get("locator") else ""
        defer_lines.append(f"- {d.get('item_id', '?')}（{d.get('action', '?')}）：{d.get('reason', '待人工')}{loc}")
    if not defer_lines:
        defer_lines = ["- （无）"]
    return "\n".join([
        "# R3 交付检查说明",
        "",
        f"- 任务编号：{state.get('task_id', '')}；预览编号：{(state.get('preview') or {}).get('preview_id', '')}",
        f"- 执行覆盖：{len(execution)} 类",
        *cov_parts,
        f"- 实际采纳写入：{n_applied} 条" + ("（--use-original：跳过 AI 采纳）" if use_original else ""),
        "",
        "## 已采纳",
        *adopt_lines,
        "",
        "## 未采纳 / 带定位待人工",
        *defer_lines,
        "",
        "## 编译状态",
        f"- {compile_status}",
        "",
        "> 编译成功 / 引文闭合 / 模型自报通过 / 实际交付为四个独立事实，此处只记录本模块的真实写入与调用结果。",
        "",
    ])


# ── 联合交付 ──────────────────────────────────────────────────────────────
def _read_candidate(work_dir, state, preview, *, kind):
    if kind == "tex":
        path, digest = preview["candidate_text"], preview["text_new_sha256"]
    else:
        path, digest = preview["candidate_bib"], preview["bib_new_sha256"]
        if not path and digest == coop.sha256_text(""):
            return None
    path = Path(path)
    task = Path(work_dir, ".aiw", state["task_id"]).resolve()
    if path.is_symlink() or path.resolve().parent != task or not path.is_file():
        raise ValueError(f"缺少已校验候选 {kind}")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"已校验候选 {kind} 已变化")
    return raw


def _check_references(tex_path, bib_path):
    from .ai_workflow_taskbook import _string_references

    tex, bib_text = _read(tex_path), _read(bib_path)
    table, problems = ai_bib_candidate.entries_by_key(bib_text)
    cited = bibcheck.cited_keys(tex)
    needed = set(table) if "*" in cited else cited
    cited.discard("*")
    used, missing = ai_bib_candidate.closure(needed, table, label="交付候选")
    defs = ai_bib_candidate.string_defs(bib_text)
    pending = set().union(*(_string_references(entry.body)
                           for key, entry in table.items() if key in used))
    seen = set()
    while pending:
        name = pending.pop()
        seen.add(name)
        if name in defs:
            definition = ai_bib_candidate._string_def_text(bib_text, name)
            pending.update(_string_references("=" + definition.partition("=")[2][:-1]) - seen)
    unknown_strings = seen - defs.keys()
    if cited - set(table):
        missing.append("缺少正文引用键 " + "、".join(sorted(cited - set(table))))
    if unknown_strings:
        missing.append("缺少 Bib 字符串定义 " + "、".join(sorted(unknown_strings)))
    if problems or missing:
        raise ValueError("最终引文未闭合：" + "；".join([*problems, *missing]))


def _source_pdf(candidate, conversion):
    from slcn import office

    source = conversion["inputs"]["source"]
    if Path(source).suffix.lower() not in (".doc", ".docx"):
        return "源稿为 LaTeX，原稿 PDF 导出不适用"
    try:
        pdf, message = office.docx_to_pdf(source, candidate)
        if not pdf:
            raise ValueError(f"原稿 PDF 导出失败：{message}")
        output = os.path.join(candidate, conversion["stem"] + "_原稿.pdf")
        if os.path.abspath(pdf) != output:
            os.replace(pdf, output)
        return "原稿 PDF 导出成功"
    finally:
        office.cleanup_profile(candidate)


def _journal_path(work_dir):
    return os.path.join(work_dir, ".aiw", JOURNAL_FILENAME)


def _read_journal(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def _move_to_keep(work_dir, final):
    root = os.path.join(work_dir, UNDO_KEEP_DIRNAME)
    os.makedirs(root, exist_ok=True)
    keep = os.path.join(root, f"{os.path.basename(final)}-{time.time_ns()}")
    os.replace(final, keep)
    return keep


def _recover_delivery(work_dir, transaction):
    """未记录成功的推广回滚到上次交付，保留已移动的新目录。"""
    final, backup = transaction["final"], transaction.get("backup", "")
    coop._recover_promote_journal(final)
    exists = os.path.isdir(final)
    prior = transaction["prior_shas"]
    if exists == transaction["had_prior_final"] and (not exists or _tree_shas(final) == prior):
        return
    if exists and _tree_shas(final) == transaction["shas"]:
        if transaction["had_prior_final"] and (
                not backup or not os.path.isdir(backup) or _tree_shas(backup) != prior):
            raise ValueError("中断后交付备份已外改，需要人工恢复")
        keep = _move_to_keep(work_dir, final)
        if backup:
            try:
                os.replace(backup, final)
            except OSError:
                os.replace(keep, final)
                raise
        return
    if not exists and backup and os.path.isdir(backup) and _tree_shas(backup) == prior:
        os.replace(backup, final)
        return
    raise ValueError("中断后交付目录已外改，需要人工恢复")


def _publication_ignore(_directory, names):
    return {name for name in names
            if name in _METADATA_NOISE or name in {".aiw", coop.STATE_NAME, JOURNAL_FILENAME}
            or name.endswith(".ai前")}


def _record_delivery_state(work, state, journal):
    from slcn import ai_workflow

    state["delivery"] = journal
    state["phase"] = "undone" if journal.get("undone") else "delivered"
    state["history"].append({"at": ai_workflow._now(), "event": state["phase"]})
    ai_workflow.save_state(work, state)


def deliver(*, work_dir: str, preview_id: str, use_original: bool = False) -> dict:
    """只交付冻结的原始产物，或上次预览校验过的 TeX/Bib 文件。"""
    from slcn import ai_workflow
    from slcn.ai_workflow_state_v3 import request_lock

    if not os.path.isfile(ai_workflow.state_path(work_dir)):
        return {"ok": False, "delivered": False, "error": "工作区没有 R3 初排任务"}
    with request_lock(work_dir) as acquired:
        if not acquired:
            return {"ok": False, "delivered": False, "error": "任务正在处理"}
        return _deliver(work_dir=work_dir, preview_id=preview_id, use_original=use_original)


def _deliver(*, work_dir: str, preview_id: str, use_original: bool) -> dict:
    from slcn import ai_workflow
    from slcn.convert_metadata import is_own_product

    work = str(Path(work_dir).resolve())
    path = _journal_path(work)
    transaction = {}
    try:
        state = ai_workflow.load_state(work) or {}
        conversion = state.get("conversion") or {}
        if state.get("entry") != "convert" or not conversion:
            raise ValueError("工作区没有 R3 初排任务")
        old = _read_journal(path)
        if old.get("delivered") and not old.get("undone"):
            if (state.get("delivery") != old and os.path.isdir(old["final"])
                    and _tree_shas(old["final"]) == old["shas"]):
                _record_delivery_state(work, state, old)
            raise ValueError("任务已经交付，须先撤销才能再次交付")
        if old.get("promoting"):
            _recover_delivery(work, old)
            coop.atomic_write_json(path, {**old, "promoting": False, "recovered": True})
        preview = state.get("preview") or {}
        if use_original and preview_id:
            raise ValueError("原始交付与预览采纳只能选一项")
        if not use_original and (not preview_id or not preview.get("ok")
                                 or preview_id != preview.get("preview_id")
                                 or preview.get("reply_sha256", "") !=
                                 (state.get("active_reply") or {}).get("sha256", "")):
            raise ValueError("preview_id 不是最后校验通过的活动预览")
        stage, final = conversion["stage_dir"], conversion["final_dir"]
        if not os.path.isdir(stage) or _overlaps(work, final) or not Path(stage).is_relative_to(work):
            raise ValueError("暂存目录或最终目录与冻结任务不符")
        inputs, opts = conversion["inputs"], conversion["options"]
        _validate_inputs(inputs)
        stage_now = _tree_shas(stage)
        if stage_now != conversion["stage_sha256s"]:
            raise ValueError(_changed_message(
                conversion["stage_sha256s"], stage_now, what="冻结产物已变化"))
        stem = conversion["stem"]
        if os.path.lexists(final) and (os.path.islink(final) or not os.path.isdir(final)):
            raise ValueError("最终交付路径不是可替换的目录")
        had_final = os.path.isdir(final)
        prior_shas = _tree_shas(final)
        if os.path.isdir(final) and not opts["force"]:
            if any(not name.startswith(".") and name != CHECK_NOTE_FILENAME
                   and not is_own_product(name, stem)
                   for name in os.listdir(final)):
                raise ValueError("最终目录包含其他文件，拒绝覆盖")
        blocked = coop.final_delivery_check(final, inputs["source"], force=opts["force"],
                                            incoming=inputs["fingerprint"])
        if blocked:
            raise ValueError(blocked)

        candidate = tempfile.mkdtemp(prefix="delivery-", dir=os.path.join(work, ".aiw"))
        transaction = {"delivered": False, "candidate": candidate, "final": final,
                       "task_id": state["task_id"], "preview_id": preview_id}
        coop.atomic_write_json(path, transaction)
        shutil.copytree(stage, candidate, dirs_exist_ok=True, ignore=_publication_ignore)
        stage_after = _tree_shas(stage)
        if stage_after != conversion["stage_sha256s"]:
            raise ValueError(_changed_message(
                conversion["stage_sha256s"], stage_after,
                what="复制期间冻结产物已变化"))
        tex_path = os.path.join(candidate, conversion["tex_name"])
        bib_name = conversion["bib_name"]
        bib_path = os.path.join(candidate, bib_name) if bib_name else ""
        adopted, deferred = [], []
        if not use_original:
            Path(tex_path).write_bytes(_read_candidate(work, state, preview, kind="tex"))
            bib_output = _read_candidate(work, state, preview, kind="bib")
            if bib_output is not None:
                if not bib_path:
                    bib_path = os.path.join(candidate, "reference.bib")
                Path(bib_path).write_bytes(bib_output)
            elif bib_path:
                Path(bib_path).write_bytes(b"")
            typeset = preview.get("typeset") or {}
            text_items = preview.get("text_items") or {}
            adopted.extend(typeset.get("applied", []))
            adopted.extend({**item, "action": "text"} for item in text_items.get("applied", []))
            bib_applied = {"applied"}
            adopted.extend({"item_id": item["key"], "action": "bib_verify"}
                           for item in preview.get("bib_items", [])
                           if item.get("status") in bib_applied)
            deferred.extend(typeset.get("rejected", []))
            deferred.extend(text_items.get("reported", []) + text_items.get("rejected", []))
        _check_references(tex_path, bib_path)
        validated_tex = coop.sha256_file(tex_path)
        validated_bib = coop.sha256_file(bib_path) if bib_path else ""

        compile_status = "未要求编译"
        if opts["compile"]:
            ok, msg = convert_compile.compile_pdf(candidate, stem)
            if not ok:
                details = []
                for label, filename in (("候选 .tex", tex_path),
                                        ("编译 .log", os.path.join(candidate, stem + ".log"))):
                    if os.path.isfile(filename):
                        details.append(f"{label}：{os.path.abspath(filename)}")
                suffix = ("；" + "；".join(details)) if details else ""
                raise ValueError(f"编译失败：{msg}{suffix}")
            compile_status = "编译成功"
        source_pdf_status = _source_pdf(candidate, conversion) if opts["source_pdf"] else "未要求导出原稿 PDF"
        if opts["clean"]:
            clean.clean(candidate)
        if (coop.sha256_file(tex_path) != validated_tex
                or (bib_path and coop.sha256_file(bib_path) != validated_bib)):
            raise ValueError("准备交付时已核对的候选文件被修改")
        _validate_inputs(inputs)
        if os.path.isdir(final) != had_final or _tree_shas(final) != prior_shas:
            raise ValueError("准备交付时最终目录已被外部修改")
        latest = ai_workflow.load_state(work) or {}
        if latest != state:
            raise ValueError("准备交付时任务状态已变动")
        note = _render_check_note(state, use_original=use_original, n_applied=len(adopted),
                                  adopted=adopted, deferred=deferred,
                                  compile_status=compile_status + "；" + source_pdf_status)
        coop.atomic_write_text(os.path.join(candidate, CHECK_NOTE_FILENAME), note)
        transaction.update(promoting=True, had_prior_final=had_final,
                           prior_shas=prior_shas, shas=_tree_shas(candidate))
        coop.atomic_write_json(path, transaction)

        def rename(src, dst):
            if src == final:
                if not had_final or _tree_shas(final) != prior_shas:
                    raise ValueError("准备推广时最终目录已被外部修改")
                transaction["backup"] = dst
                coop.atomic_write_json(path, transaction)
            os.replace(src, dst)

        # Legacy promoter consults this transient payload to carry the source fingerprint.
        # Its default exclusion keeps the payload out of the actual delivery.
        coop.atomic_write_json(coop.state_path(candidate), {
            "input_fingerprint": inputs["fingerprint"], "params": {"stem": stem},
        })
        promotion = coop.promote_stage(candidate, final, rename=rename)
        if _tree_shas(final) != transaction["shas"]:
            raise ValueError("实际交付文件与候选不一致")
        completed = {**transaction, "promoting": False, "delivered": True, "ok": True,
                     "backup": promotion["backup"], "n_applied": len(adopted),
                     "adopted": adopted, "deferred": deferred, "use_original": use_original,
                     "compile_status": compile_status, "source_pdf_status": source_pdf_status}
        coop.atomic_write_json(path, completed)
        _record_delivery_state(work, state, completed)
        return completed
    except (OSError, ValueError, KeyError, ai_workflow.WorkflowError) as exc:
        error = str(exc)
        if transaction:
            try:
                if transaction.get("promoting"):
                    _recover_delivery(work, transaction)
                    transaction["promoting"] = False
                coop.atomic_write_json(path, {**transaction, "error": error})
            except (OSError, ValueError) as recovery:
                error += f"；恢复中断：{recovery}"
        return {"ok": False, "delivered": False, "n_applied": 0, "error": error}


def undo_delivery(*, work_dir: str) -> dict:
    """核对交付后的目录身份，保留本次产物并恢复旧交付。"""
    from slcn import ai_workflow
    from slcn.ai_workflow_state_v3 import request_lock

    if not os.path.isfile(ai_workflow.state_path(work_dir)):
        return {"ok": False, "undone": False, "reason": "工作区没有 R3 初排任务"}
    with request_lock(work_dir) as acquired:
        if not acquired:
            return {"ok": False, "undone": False, "reason": "任务正在处理"}
        return _undo_delivery(work_dir=work_dir)


def _undo_delivery(*, work_dir: str) -> dict:
    from slcn import ai_workflow

    work = str(Path(work_dir).resolve())
    path = _journal_path(work)
    try:
        state = ai_workflow.load_state(work) or {}
        journal = _read_journal(path)
        if not journal.get("delivered") or journal.get("undone") or journal.get("task_id") != state.get("task_id"):
            raise ValueError("没有可撤销的成功交付")
        final, backup = journal["final"], journal["backup"]
        if not os.path.isdir(final) or _tree_shas(final) != journal["shas"]:
            raise ValueError("交付目录已经外改，无法撤销")
        if backup and (not os.path.isdir(backup) or _tree_shas(backup) != journal["prior_shas"]):
            raise ValueError("上次交付备份已经外改，无法撤销")
        keep = _move_to_keep(work, final)
        try:
            if backup:
                os.replace(backup, final)
        except OSError:
            os.replace(keep, final)
            raise
        journal.update(undone=True, ok=True, undo_keep=keep, restored_backup=bool(backup))
        coop.atomic_write_json(path, journal)
        _record_delivery_state(work, state, journal)
        return journal
    except (OSError, ValueError, KeyError, ai_workflow.WorkflowError) as exc:
        return {"ok": False, "undone": False, "reason": str(exc)}
