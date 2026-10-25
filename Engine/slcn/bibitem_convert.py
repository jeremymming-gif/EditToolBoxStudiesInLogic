r"""B-233：手写 `\bibitem` → 规范 `reference.bib`（LaTeX / Word 两通道共用）。

`bibnorm.bibitems_to_bib()` 是零调用方骨架（类型恒 @article、其余塞 note）。本模块在
**调用侧**健壮化它：先 `latex.strip_comments`（注释里的 `% \bibitem` 不算条目）、在
`\end{thebibliography}` 处截止（末条不吞环境尾），再逐条重解析提升字段/类型——
LNCS/Springer 体例（PhD thesis→@phdthesis、In:→@incollection、期刊→@article、
出版社+城市→@book）、GB/T 7714 中英文条目（`[J]/[M]/[C]/[D]` 与《题名》体例，复用
bibnorm/footref 既有解析器）；判不出的 @misc+note 原文并逐条低置信提示。渲染后逐条过
`convert_bib.bib_entry_structurally_safe`（不合格跳过+warning，不让坏 .bib 让 biber
整份报废）。key 原样保留，空/重复/非法 key 确定性兜底。

契约：`convert()` 绝不抛异常（fail-soft）；`enabled()/set_enabled()` 全局开关
（CLI `--no-bibitem-convert`）；`WARNING_AUTO` 是两通道共用的固定交接单提示。本模块
不读/写文件、不认识 docx（Word 通道由 impl-word 取 entries 后在既有单点写）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .latex import strip_comments

WARNING_AUTO = "由 \\bibitem 自动转换，请核对类型/字段"
FIELD_ORDER = ("author", "title", "journal", "booktitle", "volume", "number",
               "pages", "publisher", "address", "school", "year", "note")
_TYPES = frozenset(("article", "book", "incollection", "inproceedings",
                    "phdthesis", "mastersthesis", "misc"))
_BIBITEM_RE = re.compile(
    r"\\bibitem(?:\[[^\]]*\])?\{([^}]*)\}\s*(.*?)(?=\\bibitem|\Z)", re.S)
_END_RE = re.compile(r"\\end\{thebibliography\}|\\end\{document\}")
_KEY_BAD_RE = re.compile(r"[\s,{}%#&~\\]+")
_UNSAFE_RE = re.compile(r"(?<!\\)([&%#_])")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_PYEAR_RE = re.compile(r"\(\s*((?:19|20)\d{2})\s*\)\s*[.,;]?\s*$")
_MARK_RE = re.compile(r"\[\s*([JMCDNZRSPA])\s*\]")
_MARK_TYPE = {"J": "article", "M": "book", "C": "incollection", "D": "phdthesis",
              "N": "article", "R": "misc", "S": "misc", "P": "misc", "A": "book",
              "Z": "misc"}
_THESIS_RE = re.compile(
    r"(Ph\.?\s*D\.?|Doctoral|Master'?s?)\s+(thesis|dissertation)\b", re.I)
_IN_RE = re.compile(r"^In\s*:\s*(.+)$", re.S)
_VOL_RE = re.compile(r"\bVol\.?\s*~?\s*(\d+)", re.I)
_ITALIC_RE = re.compile(r"\\textit\{([^{}]*)\}")
_BOLD_RE = re.compile(r"\\textbf\{([^{}]*)\}")
_PAGES_RE = re.compile(
    r"(?:pp?\.\s*~?\s*|[,，]\s*)([A-Za-z]?\d{1,5})\s*(?:--|–|—|-|~)\s*([A-Za-z]?\d{1,5})")
_ENABLED = True


def enabled() -> bool:
    """全局开关：默认 True（转换开）；`--no-bibitem-convert` 置 False 回旧行为。"""
    return _ENABLED


def set_enabled(flag: bool) -> None:
    global _ENABLED
    _ENABLED = bool(flag)


@dataclass
class BibitemResult:
    """转换结果；`entries`/`bib_text` 为空表示没东西可写（调用方回旧行为）。"""

    entries: list[dict] = field(default_factory=list)
    bib_text: str = ""
    warnings: list[str] = field(default_factory=list)
    n_source: int = 0
    n_parsed: int = 0


def looks_like_bibitems(text: str) -> bool:
    """剥注释后是否含**有效** `\\bibitem`（注释示例不算；默认路径的闸门）。"""
    if not text or "\\bibitem" not in text:
        return False
    t = strip_comments(text)
    m = _END_RE.search(t)
    return "\\bibitem" in (t[:m.start()] if m else t)


# ── 文本小工具 ────────────────────────────────────────────────────────────

def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _year_of(s: str) -> str:
    m = _PYEAR_RE.search(s or "")
    return m.group(1) if m else ((_YEAR_RE.findall(s or "") or [""])[-1])


def _clean_text(t: str) -> str:
    return _collapse(t).strip(" .,;:")


def _clean_title(t: str) -> str:
    t = _clean_text(t)
    m = re.fullmatch(r"\\textit\{(.+)\}", t, re.S)
    t = m.group(1).strip() if m else t
    return re.sub(r"^[\s:：.]+", "", t).strip()


def _fields_of_body() -> dict:
    return {"key": "", "_type": "misc", **dict.fromkeys(FIELD_ORDER, "")}


def _split_sentence(s: str) -> tuple[str, str]:
    """第一个 `. `（花括号深度 0、非转义）处切题名/后半段；切不出返回 `(s, "")`。"""
    depth, i, n = 0, 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == "." and depth == 0 and i + 1 < n and s[i + 1] in " \n\t":
            j = i + 1
            while j < n and s[j] in " \n\t":
                j += 1
            if j < n and (s[j].isupper() or s[j] in "\\{" or s[j].isdigit()):
                return s[:i].strip(), s[j:].strip()
        i += 1
    return s.strip(), ""


# ── 作者串 → BibTeX `and` ────────────────────────────────────────────────

def _join_authors(blob: str) -> str:
    """`Zhen, L., Seligman, J.` → `Zhen, L. and Seligman, J.`（不改姓名部件）。"""
    s = _collapse(blob).strip().strip(",;")
    if not s:
        return ""
    if re.search(r"[\u4e00-\u9fa5，、；]", s):  # 中文条目：逗号/顿号即作者分隔
        return " and ".join(p.strip() for p in re.split(r"[,，、;；]\s*", s) if p.strip())
    out, cur = [], ""
    for tok in (t.strip() for t in re.split(r",\s*", s) if t.strip()):
        # 缩写点被当分隔符吃掉（GB/T 英文条目 `Smith, J`）→ 补回
        if re.fullmatch(r"[A-Z]\.(?:[-\s]*[A-Z]\.)*|[A-Z]{1,3}", tok):
            init = tok if tok.endswith(".") else tok + "."
            out.append(f"{cur}, {init}" if cur else init)
            cur = ""
        else:
            if cur:
                out.append(cur)
            cur = tok
    if cur:
        out.append(cur)
    return " and ".join(out) if out else s


def _split_author_rest(s: str) -> tuple[str, str]:
    """LNCS 的 `作者列表: 正文` 切分；切不出返回 `("", s)`。"""
    m = re.search(r"\.\s*:\s+", s)
    if m:
        return s[: m.start() + 1].strip(), s[m.end():].strip()
    m = re.match(r"^([^:：]{2,240}?)\s*[：:]\s+(.+)$", s, re.S)
    if m:
        head = m.group(1).strip()
        if all(len(t) <= 40 for t in re.split(r",\s*", head)):
            return head, m.group(2).strip()
    return "", s


# ── GB/T 7714 / 中文条目 ─────────────────────────────────────────────────

def _fill_pub_addr(d: dict, s: str) -> None:
    """`出版地: 出版社` / `出版社, 城市` → address/publisher（拆不出留空）。"""
    m = _PYEAR_RE.search(s or "")
    s = (s[:m.start()] if m else (s or "")).strip(" ,.;")
    m = re.match(r"^(?P<a>[^:：,，]{2,60}?)\s*[:：]\s*(?P<p>[^,，.。]{2,100})", s)
    if m:
        d["address"], d["publisher"] = _clean_text(m.group("a")), _clean_text(m.group("p"))
        return
    parts = [p.strip() for p in re.split(r"[,，]", s) if p.strip()]
    if parts:
        d["publisher"] = _clean_text(parts[0])
        if len(parts) > 1:
            d["address"] = _clean_text(", ".join(parts[1:]))


def _parse_marked(raw: str) -> dict | None:
    """GB/T 7714 条目（中英文都认）：类型看 `[X]` 标记，字段复用 footref 行解析器。"""
    mark = _MARK_RE.search(raw)
    if not mark:
        return None
    e = _fields_of_body()
    e["_type"] = _MARK_TYPE.get(mark.group(1).upper(), "misc")
    try:
        from .footref import parse_one
        p = parse_one(raw)
    except Exception:  # noqa: BLE001  解析器异常只是「这条不提升」
        p = None
    if p is not None:
        for name in ("author", "title", "journal", "booktitle", "volume", "number",
                     "pages", "publisher", "address", "year"):
            val = getattr(p, name, "")
            if val:
                e[name] = str(val)
    if not e["author"]:  # 行内兜底：`作者. 题名[X]. ...` 的最后一段 `. ` 切作者/题名
        head = raw[: mark.start()]
        last = None
        for m in re.finditer(r"[.。]\s+", head):
            last = m
        author, title = (head[:last.start()], head[last.end():]) if last else ("", head)
        e["author"] = _join_authors(author.strip(" .。"))
        e["title"] = e["title"] or _clean_title(title.strip(" .。"))
    if not e["year"]:
        e["year"] = _year_of(raw)
    if e["_type"] == "phdthesis" and not e["school"]:
        tail = raw[mark.end():]
        mt = _THESIS_RE.search(tail)
        school = tail[mt.end():] if mt else tail
        school = re.sub(r"[\s,，.。;；]*(?:19|20)\d{2}\s*[.。]?\s*$", "", school)
        e["school"] = _clean_text(school.strip(" ,.;"))
    return e


def _parse_cjk(raw: str) -> dict | None:
    """中文条目：复用 `bibnorm._parse_cn_numbered_line`（《题名》/plain book 体例）。"""
    try:
        from .bibnorm import _parse_cn_numbered_line
        d = _parse_cn_numbered_line(raw)
    except Exception:  # noqa: BLE001
        d = None
    if not d:
        return None
    e = _fields_of_body()
    e.update({k: v for k, v in d.items() if k in e and v})
    return e


# ── LNCS / Springer 数字体例 ──────────────────────────────────────────────

def _fill_lncs_tail(e: dict, tail: str) -> None:
    mt = _THESIS_RE.search(tail)
    if mt:
        e["_type"] = "mastersthesis" if "master" in mt.group(0).lower() else "phdthesis"
        school = _clean_text(tail[mt.end():])
        pm = _PYEAR_RE.search(school)
        e["school"] = (school[: pm.start()] if pm else school).strip(" ,.;")
        e["school"] = re.sub(r"^(?:of|at|,)\s+", "", e["school"], flags=re.I)
        return
    mi = _IN_RE.match(tail)
    if mi:  # 章节：In: \textit{书名}, pp. N--M. 出版社, 城市 (年)
        e["_type"] = "incollection"
        s = mi.group(1).strip()
        pm = _PYEAR_RE.search(s)
        s = (s[: pm.start()] if pm else s).strip(" ,.;")
        im = _ITALIC_RE.search(s)
        if im:
            e["booktitle"] = _clean_text(im.group(1))
            s = (s[: im.start()] + " " + s[im.end():]).strip()
        else:
            bt, s2 = _split_sentence(s)
            e["booktitle"], s = _clean_title(bt), s2
        vm = _VOL_RE.search(s)
        if vm:
            e["volume"] = vm.group(1)
        pm = _PAGES_RE.search(s)
        if pm:
            e["pages"] = f"{pm.group(1)}--{pm.group(2)}"
            s = s[pm.end():]
        else:
            s = ""
        left = re.sub(r"^[\s,.;]+", "", s)
        pm = _PYEAR_RE.search(left)
        if pm:
            left = left[: pm.start()]
        if left.strip(" ,.;"):
            _fill_pub_addr(e, left)
        return
    if _PAGES_RE.search(tail) or "\\textbf{" in tail or "\\textit{" in tail:
        e["_type"] = "article"
        im, bm = _ITALIC_RE.search(tail), _BOLD_RE.search(tail)
        if im:
            e["journal"] = _clean_text(im.group(1))
        if bm:
            e["volume"] = _clean_text(bm.group(1))
        if not im and not bm:  # 无标记体例：`题名. 刊名 卷(期), 页 (年)`
            gm = re.match(
                r"^(?P<j>.+?)\s+(?P<v>\d{1,4})\s*(?:\((?P<n>[^)]{1,12})\))?\s*,\s*"
                r"(?P<p>[A-Za-z]?\d{1,5}\s*(?:--|–|—|-|~)\s*[A-Za-z]?\d{1,5})", tail)
            if gm:
                e["journal"], e["volume"] = _clean_text(gm.group("j")), gm.group("v")
                e["number"] = gm.group("n") or ""
                e["pages"] = re.sub(r"\s*(?:--|–|—|-|~)\s*", "--", gm.group("p"))
            return
        pm = _PAGES_RE.search(tail)
        if pm:
            e["pages"] = f"{pm.group(1)}--{pm.group(2)}"
        if not e["volume"]:
            vm = _VOL_RE.search(tail)
            if vm:
                e["volume"] = vm.group(1)
        for num in re.findall(r"\(\s*([^()]{1,12})\s*\)", tail):
            if re.fullmatch(r"\d{1,3}[a-z]?", num.strip()):
                e["number"] = num.strip()
                break
        return
    if tail:  # 书：\textit{题名}. 出版社, 城市 (年)
        e["_type"] = "book"
        _fill_pub_addr(e, tail)
        if e["publisher"]:
            return
    e["_type"], e["note"] = "misc", _clean_text(tail)


# ── 单条解析 / 渲染 / key / 主入口 ───────────────────────────────────────

def _parse_one(body: str) -> dict:
    raw = _collapse(body)
    if not raw:
        return _fields_of_body()
    d = _parse_marked(raw)
    if d is None and re.search(r"[\u4e00-\u9fa5]", raw):
        d = _parse_cjk(raw)
    if d is None:  # LNCS/Springer：`作者列表: 题名. 后端信息`
        author, rest = _split_author_rest(raw)
        if rest:
            d = _fields_of_body()
            d["author"], d["year"] = _join_authors(author), _year_of(raw)
            title, tail = _split_sentence(rest)
            d["title"] = _clean_title(title)
            if tail:
                _fill_lncs_tail(d, tail)
            else:
                d["note"] = _clean_text(rest)
    if d is not None:
        return d
    e = _fields_of_body()
    e["note"] = _clean_text(raw)
    title, _tail = _split_sentence(raw)
    e["title"] = _clean_title(title) if title and title != raw else ""
    e["year"] = _year_of(raw)
    return e


def _skeleton_fields(skel_bib: str) -> dict[str, dict]:
    """`bibitems_to_bib` 骨架 → {key: {title, year}}，仅作解析失败时的兜底。"""
    out: dict[str, dict] = {}
    for k, t, y in re.findall(
            r"@\w+\{([^,]*),\s*title\s*=\s*\{(.*?)\},\s*year\s*=\s*\{([^}]*)\}",
            skel_bib or "", re.S):
        out[k.strip()] = {"title": _clean_title(t), "year": y.strip()}
    return out


def _render_one(e: dict) -> str:
    etype = str(e.get("_type") or "misc").lower()
    fields: list[str] = []
    for name in FIELD_ORDER:
        raw = _collapse(e.get(name) or "")
        # 作者串末位缩写点（`Seligman, J.`）是数据，不当句末标点剥
        val = _UNSAFE_RE.sub(r"\\\1", raw.strip(" ,;") if name == "author"
                             else _clean_text(raw))
        if val:
            fields.append(f"  {name} = {{{val}}}")
    return "@%s{%s,\n%s\n}" % (etype if etype in _TYPES else "misc",
                               e.get("key") or "bibitem", ",\n".join(fields))


def render_entries(entries: list[dict]) -> str:
    """entries（统一 dict）→ .bib 文本；空条目跳过。结构安全由 convert() 把关。"""
    lines = ["% " + WARNING_AUTO + "。", ""]
    for e in entries or ():
        raw = _render_one(e)
        if raw:
            lines.extend((raw, ""))
    return "\n".join(lines)


def _assign_keys(raw_keys: list[str]) -> list[str]:
    used: set[str] = set()
    out: list[str] = []
    for i, raw in enumerate(raw_keys, 1):
        key = _KEY_BAD_RE.sub("_", (raw or "").strip()) or f"bibitem{i}"
        base, n = key, 2
        while key in used:
            key = f"{base}-{n}"
            n += 1
        used.add(key)
        out.append(key)
    return out


def convert(text: str, *, strip: bool = True) -> BibitemResult:
    """手写 `\\bibitem` 文本 → `BibitemResult`（fail-soft：异常只记 warning）。"""
    res = BibitemResult()
    if not text:
        return res
    try:
        t = strip_comments(text) if strip else text
        m = _END_RE.search(t)
        t = t[:m.start()] if m else t
        items = _BIBITEM_RE.findall(t)
        res.n_source = len(items)
        if not items:
            return res
        try:  # 复用零调用方骨架：key 顺序 + 重解析失败时 title/year 兜底
            from .bibnorm import bibitems_to_bib
            skeleton = _skeleton_fields(bibitems_to_bib(t)[0])
        except Exception:  # noqa: BLE001
            skeleton = {}
        keys = _assign_keys([k for k, _b in items])
        entries: list[dict] = []
        warns: list[str] = []
        for idx, (raw_key, body) in enumerate(items):
            key, rawk = keys[idx], (raw_key or "").strip()
            if rawk != key:
                san = _KEY_BAD_RE.sub("_", rawk)
                if not rawk:
                    warns.append(f"{key}: 源 \\bibitem 为空 key，已按出现顺序兜底")
                elif san != rawk:
                    warns.append(f"{key}: key 含空白/非法字符，已兜底改写（原 {rawk!r}）")
                else:
                    warns.append(f"{key}: 源 key 重复，已确定性改名保留（原 {rawk!r}）")
            try:
                e = _parse_one(body)
            except Exception as exc:  # noqa: BLE001
                e = _fields_of_body()
                e["note"] = _clean_text(_collapse(body))
                warns.append(f"{key}: 解析异常，已保留原文（{type(exc).__name__}）")
            e["key"] = key
            sk = skeleton.get(rawk) or {}
            if not e.get("title") and sk.get("title"):
                e["title"] = sk["title"]
            if not e.get("year") and re.fullmatch(r"(?:19|20)\d{2}", sk.get("year") or ""):
                e["year"] = sk["year"]
            if not (e.get("author") or e.get("title")):
                e["_type"] = "misc"
                e["note"] = e.get("note") or _clean_text(_collapse(body))
            miss = [f for f in ("author", "title", "year") if not e.get(f)]
            if e.get("_type") == "misc" or miss:
                bits = ([f"缺 {'/'.join(miss)}"] if miss else [])
                if e["_type"] == "misc":
                    bits.append("类型未判定")
                warns.append(f"{key}: 低置信（{'，'.join(bits)}），请人工核对")
            entries.append(e)
        good: list[dict] = []
        for e in entries:
            raw_entry = _render_one(e)
            try:
                from .convert_bib import bib_entry_structurally_safe
                ok = bool(bib_entry_structurally_safe(raw_entry)) if raw_entry else False
            except Exception:  # noqa: BLE001  校验器异常不能变成「整篇不转换」
                ok = bool(raw_entry)
            if ok:
                good.append(e)
            else:
                warns.append(f"{e.get('key')}: 结构校验未过，已跳过"
                             "（避免坏 .bib 让 biber 整份报废）")
        res.entries, res.n_parsed = good, len(good)
        res.bib_text = render_entries(good)
        if res.n_parsed != res.n_source:
            warns.insert(0, f"源 \\bibitem {res.n_source} 条，写入 {res.n_parsed} 条"
                            "（其余未通过校验或为空）")
        res.warnings = warns
        return res
    except Exception as exc:  # noqa: BLE001  fail-soft：绝不把转换异常抛给主流程
        res.warnings.append(f"\\bibitem 转换失败，保留原文：{type(exc).__name__}: {exc}")
        return res


# ── CLI 接线（B-229 族总量行数上限：门面只加两处调用，逻辑都在本模块）──────

def add_cli_args(ap):
    """给 `convert.py` 的 parser 挂 `--no-bibitem-convert`（默认转换开）。返回 ap。"""
    ap.add_argument("--no-bibitem-convert", action="store_true",
                    help="源稿没有 .bib、只有手写 \\bibitem 时**不**自动转换出"
                         " reference.bib（默认转换；转换结果请核对类型/字段）")
    return ap


def apply_cli_args(args):
    """按解析结果设全局开关；返回 args（供门面一行调用）。"""
    if getattr(args, "no_bibitem_convert", False):
        set_enabled(False)
    return args
