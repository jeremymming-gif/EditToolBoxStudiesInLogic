"""元数据 / 补充信息统一读取器（B-223）：提取 → 合并 → 预览 → 补空。

优先级：正文解析 < 补充文件（.docx/.tex/.json，按传入顺序先到先得）< 表单（--metadata-json）。
**不重写既有判据**：英文元数据表复用 `sources.en_metadata_sheet()`（含 200 字符正文守卫）、
首部元数据块复用 `docxsource.parse_docx()`、.tex 复用 `latex.parse_macros()`、.json 复用
`convert_metadata.load_metadata_json()`；正文 ≥200 字符的 .docx 只认标签作者信息块。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import docxxml, latex, sources

# 规范化字段 → (中文名, 模板槽位)，顺序即展示顺序
_CANON = {
    "title_cn": ("中文题名", "mytitle"), "subtitle": ("中文副题名", "mysubtitle"),
    "title_en": ("英文题名", "mytitleEN"), "subtitle_en": ("英文副题名", "mysubtitleEN"),
    "author_cn": ("第一作者", "myfirstauthor"), "author_en": ("第一作者英文姓名", "myfirstauthorEN"),
    "affiliation_cn": ("第一作者单位", "myfirstaffiliation"),
    "affiliation_en": ("第一作者英文单位", "myfirstaffiliationEN"),
    "email": ("第一作者邮箱", "myfirstemail"),
    "abstract_cn": ("中文摘要", "myabstract"), "abstract_en": ("英文摘要", "myabstractEN"),
    "keywords_cn": ("中文关键词", "mykeywords"), "keywords_en": ("英文关键词", "mykeywordsEN"),
    "grants": ("基金项目", "mygrants"), "thanks": ("致谢", "mythanks"),
}
FIELD_LABELS = {k: v[0] for k, v in _CANON.items()}
# 与 footref.FIELD_ORDER（参考文献字段顺序）语义不同：本常量是元数据字段的展示/合并顺序，
# 改名以保持 B-233「解析/渲染符号单点定义」判据（同名字符只允许在 footref.py 出现一次）。
_PREVIEW_FIELD_ORDER = tuple(_CANON)
_REQUIRED = ("title_cn", "author_cn", "affiliation_cn", "email", "abstract_cn", "keywords_cn")
_EXPECT = ("author_cn", "affiliation_cn", "email", "author_en", "affiliation_en",
           "title_en", "abstract_en", "keywords_en")
STATUS_BODY, STATUS_SUPP = "已填（正文解析）", "已填（补充文件）"
STATUS_FORM, STATUS_CONFLICT = "已填（表单）", "冲突-未采用"
STATUS_UNKNOWN, STATUS_MISSING = "未识别", "缺项"
_WORD_EXT, _LATEX_EXT, _JSON_EXT = (".docx",), (".tex",), (".json",)
_EN_MAX_BODY = sources._EN_SHEET_MAX_BODY  # 200：复用既有「正文长度」守卫
_MAX_FIELD = 2000
_DOC_ATTR = {k: ("author" if k == "author_cn" else k) for k in _CANON
             if k not in ("grants", "thanks")}
# 规范化字段 → 该语种模板槽位（另一语种加 CN/EN 后缀；中文稿用其余槽位）
_CANON_MACRO = {
    "zh": dict(zip(_PREVIEW_FIELD_ORDER, ("mytitle", "mysubtitle", "mytitleEN", "mysubtitleEN",
        "myfirstauthor", "myfirstauthorEN", "myfirstaffiliation", "myfirstaffiliationEN",
        "myfirstemail", "myabstract", "myabstractEN", "mykeywords", "mykeywordsEN",
        "mygrants", "mythanks"))),
    "en": dict(zip(_PREVIEW_FIELD_ORDER, ("mytitle", "mysubtitle", "mytitleCN", "mysubtitleCN",
        "myfirstauthor", "myfirstauthorCN", "myfirstaffiliation", "myfirstaffiliationCN",
        "myfirstemail", "myabstract", "myabstractCN", "mykeywords", "mykeywordsCN",
        "mygrants", "mythanks"))),
}
_MACRO_CANON = {lg: {m: k for k, m in _CANON_MACRO[lg].items()} for lg in ("zh", "en")}


@dataclass
class Extracted:
    """一份补充文件的识别结果（fields 键见 FIELD_LABELS；unidentified 不静默丢）。"""
    path: str
    fields: dict = field(default_factory=dict)
    detail: str = ""
    unidentified: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    label: str = ""

    @property
    def name(self) -> str: return os.path.basename(self.path)


# 标签作者信息块（spec §2 词表）：`作者名字：X` / `邮箱：X` / `单位信息：X` …
_LABEL_KEYS = {
    "作者名字": "author_cn", "作者姓名": "author_cn", "姓名": "author_cn", "作者": "author_cn",
    "通讯作者": "author_cn", "邮箱": "email", "电子邮箱": "email", "Email": "email",
    "E-Mail": "email", "email": "email", "e-mail": "email", "单位信息": "affiliation_cn",
    "作者单位": "affiliation_cn", "工作单位": "affiliation_cn", "所属单位": "affiliation_cn",
    "单位": "affiliation_cn", "通信地址": "affiliation_cn",
}
_LABEL_RE = re.compile(r"^\s*(?P<label>" + "|".join(
    sorted(map(re.escape, _LABEL_KEYS), key=len, reverse=True))
    + r")\s*[：:]\s*(?P<value>.*)$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def _put(fields: dict, key: str, value, notes: list) -> None:
    """同文件内先到先得；同字段不同值 → 记「文件内冲突」，不静默覆盖。"""
    value = _clean(str(value or ""))[:_MAX_FIELD]
    if not value:
        return
    old = fields.get(key)
    if old is None:
        fields[key] = value
    elif old != value:
        notes.append(f"「{FIELD_LABELS.get(key, key)}」出现两个值：取「{old}」，未采用「{value}」")


def _sanitize(canon: str, value: str) -> str:
    """首部元数据块的过滤：标签串 / 邮箱串 / 超长串一律不当字段值。"""
    value = _clean(value)
    ok = value and len(value) <= _MAX_FIELD and not _LABEL_RE.match(value)
    bad = canon in ("author_cn", "author_en") and "@" in (value or "")
    return value if ok and not bad else ""


def _extract_docx(path: str) -> Extracted:
    ex = Extracted(path=path)
    try:
        paras = [p.plain.strip() for p in docxxml.read_body(path) if p.plain.strip()]
    except Exception as e:  # 坏 zip/非 docx：Word 通道旧语义是「忽略」，只告警
        ex.notes.append(f"读取 Word 文件失败（本次忽略）：{e}")
        return ex
    labeled = False
    for i, text in enumerate(paras):  # ① 标签作者信息块
        m = _LABEL_RE.match(text)
        if not m:
            continue
        label, value = _clean(m.group("label")), _clean(m.group("value"))
        if not value and i + 1 < len(paras) and not _LABEL_RE.match(paras[i + 1]):
            value = _clean(paras[i + 1])
        key = _LABEL_KEYS.get(label)
        if key is None or not value:
            ex.unidentified.append(f"未识别的标签「{label}」")
            continue
        labeled = True; ex.detail = "标签作者信息块"   # 日志用（补充信息行会显示来源类型）
        _put(ex.fields, key, value, ex.notes)
    for key, value in sources.en_metadata_sheet(path).items():  # ② 英文元数据表（200 守卫）
        _put(ex.fields, key, value, ex.notes)
        ex.detail = "英文元数据表"
    if labeled:
        return ex
    try:  # ③ 首部元数据块：无标签块且无叙述正文时才启用
        from .docxsource import parse_docx
        doc = parse_docx(path)
    except Exception as e:
        ex.notes.append(f"首部元数据块解析失败（本次忽略）：{e}")
        return ex
    body_len = len((doc.body or "").strip())
    if body_len >= _EN_MAX_BODY:
        ex.notes.append(f"正文 {body_len} 字符（≥{_EN_MAX_BODY}），按正文处理，不从中提取元数据")
        return ex
    parsed = {
        "title_cn": doc.title_cn or ("" if _has_cjk(doc.title_en) else doc.title),
        "title_en": doc.title_en or ("" if _has_cjk(doc.title) else doc.title),
        "subtitle": getattr(doc, "subtitle", ""), "subtitle_en": getattr(doc, "subtitle_en", ""),
        "abstract_cn": doc.abstract_cn, "abstract_en": doc.abstract_en,
        "keywords_cn": doc.keywords_cn, "keywords_en": doc.keywords_en,
        "author_cn": doc.author, "author_en": doc.author_en,
        "affiliation_cn": doc.affiliation_cn, "affiliation_en": doc.affiliation_en,
        "email": doc.email}
    got = False
    for key, value in parsed.items():
        value = _sanitize(key, value)
        if value and key not in ex.fields:
            ex.fields[key] = value
            got = True
    if got:
        ex.detail = f"{ex.detail}＋首部元数据块" if ex.detail else "首部元数据块"
    if not ex.detail:
        ex.notes.append("没有识别到可用的元数据字段（标签块/英文元数据表/首部块都没有）")
    return ex


def _extract_tex(path: str, lang: str = "zh") -> Extracted:
    ex = Extracted(path=path, detail="\\my* 宏")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            macros = list(latex.parse_macros(f.read()).items())
    except OSError as e:
        ex.notes.append(f"读取 .tex 文件失败（本次忽略）：{e}")
        return ex
    canon_map = _MACRO_CANON["en" if lang == "en" else "zh"]
    for name, macro in macros:
        canon = canon_map.get(name)
        if canon:
            _put(ex.fields, canon, macro.value, ex.notes)
        elif name.startswith("my"):
            ex.unidentified.append(f"未识别的元数据宏 \\{name}")
    if not ex.fields:
        ex.notes.append("该 .tex 里没有可识别的 \\my* 元数据宏")
    return ex


def _extract_json(path: str) -> Extracted:
    from .convert_metadata import load_metadata_json
    data, warns = load_metadata_json(path)  # 坏 JSON/作者跳号 → 调用方 exit 2
    ex = Extracted(path=path, detail="结构化 JSON", notes=list(warns))
    if not data:
        ex.notes.append("JSON 里没有非空字段（按未提供处理）")
    for key, value in form_fields(data).items():
        _put(ex.fields, key, value, ex.notes)
    return ex


def extract(path: str, *, lang: str = "zh") -> Extracted:
    """按扩展名分派（.docx/.tex/.json）；其它类型明确记「不支持」，不静默丢。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in _WORD_EXT:
        return _extract_docx(path)
    if ext in _LATEX_EXT:
        return _extract_tex(path, lang)
    if ext in _JSON_EXT:
        return _extract_json(path)
    ex = Extracted(path=path)
    ex.notes.append(f"不支持的补充信息来源（只认 .docx/.tex/.json）：{ext or '（无扩展名）'}")
    return ex


@dataclass
class Merged:
    fields: dict = field(default_factory=dict)
    source_of: dict = field(default_factory=dict)
    status_of: dict = field(default_factory=dict)
    rejected: list = field(default_factory=list)
    unidentified: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _take(m: Merged, key: str, value, source: str, status: str) -> None:
    value = _clean(str(value or ""))
    if not value:
        return
    old = m.fields.get(key)
    if old is None:
        m.fields[key], m.source_of[key], m.status_of[key] = value, source, status
    elif old != value:
        m.rejected.append({"field": key, "label": FIELD_LABELS.get(key, key),
                           "value": value, "source": source, "kept": old,
                           "kept_source": m.source_of.get(key, "")})


def merge(body_fields: dict, supplements: list, form_fields_: dict | None = None) -> Merged:
    """优先级：正文解析 < 补充文件（按传入顺序先到先得）< 表单；只补空不覆盖正文。"""
    m = Merged()
    for key, value in (body_fields or {}).items():
        _take(m, key, value, "正文解析", STATUS_BODY)
    for idx, ex in enumerate(supplements or [], 1):
        source = ex.label or f"补充文件：{ex.name}（第 {idx} 个）"
        for key, value in (ex.fields if ex else {}).items():
            _take(m, key, value, source, STATUS_SUPP)
        m.warnings.extend(f"{ex.name}：{w}" for w in ex.notes)
        m.unidentified.extend(f"{ex.name}：{x}" for x in ex.unidentified)
    for key, value in (form_fields_ or {}).items():
        value = _clean(str(value or ""))
        if not value:
            continue
        old = m.fields.get(key)
        if old is not None and old != value:
            m.rejected.append({"field": key, "label": FIELD_LABELS.get(key, key),
                               "value": old, "source": m.source_of.get(key, ""),
                               "kept": value, "kept_source": "表单"})
        m.fields[key], m.status_of[key] = value, STATUS_FORM
        m.source_of[key] = "表单（--metadata-json）"
    return m


def body_fields_word(doc) -> dict:
    """Word 正文侧字段视图（预览用；不另立解析，直接读 parse_docx 结果）。"""
    if doc is None:
        return {}
    return {"title_cn": doc.title_cn or ("" if _has_cjk(doc.title_en) else doc.title),
            "title_en": doc.title_en or ("" if _has_cjk(doc.title) else doc.title),
            "subtitle": getattr(doc, "subtitle", ""),
            "subtitle_en": getattr(doc, "subtitle_en", ""),
            "author_cn": doc.author, "author_en": doc.author_en, "email": doc.email,
            "affiliation_cn": doc.affiliation_cn, "affiliation_en": doc.affiliation_en,
            "abstract_cn": doc.abstract_cn, "abstract_en": doc.abstract_en,
            "keywords_cn": doc.keywords_cn, "keywords_en": doc.keywords_en}


def form_fields(structured: dict | None) -> dict:
    """`--metadata-json` 结构 → 规范化字段（作者只取第一作者；空值不覆盖）。"""
    if not structured:
        return {}
    out = {k: str(structured[k]) for k in _CANON if structured.get(k)}
    first = (structured.get("authors") or [{}])[0]
    if isinstance(first, dict):
        for src, canon in (("name_cn", "author_cn"), ("name_en", "author_en"), ("email", "email")):
            if first.get(src):
                out[canon] = str(first[src])
        for src, canon in (("affiliations_cn", "affiliation_cn"),
                           ("affiliations_en", "affiliation_en")):
            values = first.get(src) or []
            if values and isinstance(values[0], str):
                out[canon] = values[0]
    return out


def _clip(text: str, n: int = 50) -> str:
    text = _clean(text)
    return text if len(text) <= n else text[:n] + "…"


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _rows(m: Merged) -> list:
    rows = []
    for key in _PREVIEW_FIELD_ORDER:
        if key in m.fields:
            rows.append({"field": key, "label": FIELD_LABELS[key], "value": m.fields[key],
                         "source": m.source_of.get(key, ""),
                         "status": m.status_of.get(key, STATUS_BODY)})
        elif key in _REQUIRED or key in _EXPECT:
            rows.append({"field": key, "label": FIELD_LABELS[key], "value": "", "source": "",
                         "status": STATUS_MISSING})
    rows += [dict(r, status=STATUS_CONFLICT,
                  note=f"已有 {r['kept_source']}「{r['kept']}」") for r in m.rejected]
    return rows


def preview(source: str, paths, *, form: dict | None = None, lang: str | None = None,
            channel: str | None = None) -> dict:
    """dry-run 预览：**只读**，返回可渲染行结构；生效值与产物同一套判据（只补空）。"""
    ext, lang = os.path.splitext(source)[1].lower(), lang or "zh"
    channel = channel or ("word" if ext in _WORD_EXT else "latex")
    paths, warnings = [p for p in (paths or []) if p], []
    results = [extract(p, lang=lang) for p in paths]
    if channel == "word":  # Word 通道只认 .docx（与产物通道同一口径）
        keep = [os.path.splitext(p)[1].lower() in _WORD_EXT for p in paths]
        if not all(keep):
            warnings.append("Word 通道的补充信息只认 .docx；.tex 本次忽略"
                            "（.json 按人工元数据/表单口径处理）")
        results = [r for r, ok in zip(results, keep) if ok]
        # 与 build_docx 的 _merge_en_metadata_source 同一发现口径：同目录英文第二源先补空，
        # 补充源再补；预览行序 = 正文解析 → 自动第二源 → 补充源 → 表单。
        from . import convert_metadata as _cm
        _explicit = next((p for p in paths if os.path.splitext(p)[1].lower() in _WORD_EXT
                          and sources.en_metadata_sheet(p)), None)
        try:
            _auto = _cm.find_en_metadata(source, _explicit)
        except Exception:  # noqa: BLE001 预览不因第二源发现失败而中断
            _auto = None
        if _auto and os.path.abspath(_auto) not in (os.path.abspath(source),
                                                    *(os.path.abspath(p) for p in paths)):
            _sheet = sources.en_metadata_sheet(_auto)
            if _sheet:
                _ax = Extracted(path=_auto, fields=dict(_sheet), detail="同目录自动第二源")
                _ax.label = f"同目录自动第二源：{_ax.name}"
                results = [_ax] + results
    body: dict = {}
    if channel == "word" and ext in _WORD_EXT:
        try:
            from .docxsource import parse_docx
            body = body_fields_word(parse_docx(source))
        except Exception as e:  # 预览不因正文解析失败而中断（产物通道会报错）
            warnings.append(f"正文解析失败（预览仍继续）：{e}")
    elif ext == ".tex":
        try:
            with open(source, encoding="utf-8", errors="replace") as f:
                _tex_text = f.read()
        except OSError:
            _tex_text = ""
        for _name, _macro in latex.parse_macros(_tex_text).items():
            _canon = _MACRO_CANON[lang].get(_name)
            if _canon and _macro.value.strip():
                body.setdefault(_canon, _macro.value.strip())
    m = merge(body, results, form_fields(form))
    conflicts = [f"{r['label']}：{r['source']}「{r['value']}」未采用"
                 f"（已有 {r['kept_source']}「{r['kept']}」）" for r in m.rejected]
    return {"schema": 1, "source": os.path.abspath(source), "channel": channel,
            "lang": lang, "rows": _rows(m), "conflicts": conflicts,
            "unidentified": list(m.unidentified), "warnings": warnings + list(m.warnings)}


def apply_word_supplements(doc, paths, *, say=None, warnings=None) -> tuple:
    """把补充文件的字段**只补正文空值**（不覆盖正文解析），并如实打印/告警。

    返回 `(来源行, 冲突行)`：来源行给初排交接单②汇总「哪份补了什么」，冲突行是
    「正文已有该字段、补充值未采用」清单（不静默丢）。Word 通道只认 .docx。
    """
    say = say or (lambda *a, **k: None)
    warnings = warnings if warnings is not None else []
    keep = [p for p in (paths or []) if os.path.splitext(p)[1].lower() in _WORD_EXT]
    for path in (p for p in (paths or []) if p not in keep):
        warnings.append(f"Word 通道的补充信息只认 .docx，本次忽略：{os.path.basename(path)}")
    used, conflicts = [], []
    for path in keep:
        ex = extract(path)
        for note in list(ex.notes) + list(ex.unidentified):
            warnings.append(f"补充信息 {ex.name}：{note}")
        if not ex.fields:
            say(f"   补充信息   {ex.name}（未识别到可用字段，本次忽略）")
            continue
        say(f"   补充信息   {ex.name}（{ex.detail}，含："
            + "、".join(FIELD_LABELS[k] for k in _PREVIEW_FIELD_ORDER if k in ex.fields) + "）")
        added, skipped = [], []
        for key in _PREVIEW_FIELD_ORDER:
            value, attr = ex.fields.get(key), _DOC_ATTR.get(key)
            if not value:
                continue
            old = (getattr(doc, attr, "") or "").strip() if attr else ""
            if attr is None:  # 基金/致谢在 Word 通道没有独立槽位
                skipped.append(f"{FIELD_LABELS[key]}（暂无对应槽位，未应用）")
            elif old:
                if old != value:
                    skipped.append(f"{FIELD_LABELS[key]}（正文已有「{_clip(old)}」，"
                                   f"未采用「{_clip(value)}」）")
            else:
                setattr(doc, attr, value)
                added.append(key)
        if added:
            say(f"   补充信息补入 {len(added)} 项：" + "、".join(FIELD_LABELS[k] for k in added))
        for item in skipped:
            warnings.append(f"补充信息 {ex.name}：{item}")
        used.append(f"{ex.name}（{ex.detail}）："
                    + (f"补入 {'、'.join(FIELD_LABELS[k] for k in added)}" if added
                       else "无可补字段"))
        conflicts.extend(f"{ex.name}：{item}" for item in skipped if "未采用" in item)
    return used, conflicts


def compose_latex(paths, *, lang: str = "zh") -> tuple:
    """多份补充源 → 合成一份临时元数据 .tex（先到先得、只补空），返回 (路径, 字段名, 警告)。"""
    import atexit
    import tempfile

    results = [extract(p, lang=lang) for p in (paths or [])]
    m = merge({}, results)
    from .docxsource import escape_latex  # 复用旧单值 .docx 路径的转义（不新写判据）
    _plain = {r.name for r in results if os.path.splitext(r.path)[1].lower() in _WORD_EXT}
    macro_map = _CANON_MACRO.get("en" if lang == "en" else "zh", _CANON_MACRO["zh"])
    lines = ["% slcn 元数据补充源合并（运行结束自动清理）",
             "% 来源：" + "、".join(r.name for r in results if r)]
    names: list = []
    for key in _PREVIEW_FIELD_ORDER:
        value, macro = m.fields.get(key), macro_map.get(key)
        if value and macro:
            _src = m.source_of.get(key, "")
            if "：" in _src and _src.split("：", 1)[1].rsplit("（", 1)[0] in _plain:
                value = escape_latex(value)  # .docx 是明文；.tex 宏值保持原样
            names.append(FIELD_LABELS[key])
            lines.append("\\newcommand{\\" + macro + "}{" + value + "}")
    fd, path = tempfile.mkstemp(prefix="slcn-meta-", suffix=".tex")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    atexit.register(_unlink, path)
    conflicts = [f"{r['label']}：{r['source']}「{r['value']}」未采用"
                 f"（已有 {r['kept_source']}「{r['kept']}」）" for r in m.rejected]
    return path, names, conflicts + list(m.warnings)
