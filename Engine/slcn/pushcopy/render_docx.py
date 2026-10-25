r"""目录摘要推送：**确定性 docx 渲染**（空白母版 + 块原型）。

设计（B-225 契约 §3）
---------------------
  · **母版** `assets/master.docx`：由参考模板机器化去正文（只留 styles/settings/theme/sectPr），
    文档级样式全部继承母版；docProps 固定值（无真实姓名、无当前时间）。
  · **块原型** `assets/prototypes.json`：从参考实例抽取的「格式 only + 占位文本」片段
    （期头/目录标题/表格行/论文摘要块）。公开仓里**没有任何真实标题、姓名、摘要**。
  · 渲染 = 按块克隆原型、填文本；`@@TEXT@@` 是唯一占位符。
  · **确定性**：zip 条目固定时间戳(1980-01-01)与固定顺序、docProps 固定、
    不写随机 id/当前时间 → 同输入两遍**逐字节相同**。
"""

from __future__ import annotations

import json
import os
import re
import zipfile

from lxml import etree

from ..pushcopy import collect as _collect

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
q = lambda tag: "{%s}%s" % (W, tag)

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_MASTER = os.path.join(_ASSETS, "master.docx")
_PROTOS = os.path.join(_ASSETS, "prototypes.json")
_TOKEN = "@@TEXT@@"

#: 固定 zip 时间戳（与参考模板一致；确定性口径）
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


# ── 基础工具 ───────────────────────────────────────────────────────────


def _protos() -> dict:
    with open(_PROTOS, encoding="utf-8") as fh:
        return json.load(fh)


def _fragments(xml: str) -> list:
    """把一段（可能多个顶层元素的）w: 片段解析成元素列表。"""
    root = etree.fromstring(('<w:snippet xmlns:w="%s">' % W + xml + "</w:snippet>").encode("utf-8"))
    return list(root)


def _clone_par(xml: str, text: str) -> etree._Element:
    """克隆一个段落原型，把 `@@TEXT@@` 换成 text（lxml 负责 XML 转义）。"""
    root = etree.fromstring(('<w:snippet xmlns:w="%s">' % W + xml + "</w:snippet>").encode("utf-8"))
    par = root[0]
    replaced = False
    for t in par.iter(q("t")):
        if t.text is not None and _TOKEN in t.text:
            t.text = t.text.replace(_TOKEN, text)
            replaced = True
    assert replaced, "原型段落缺少 %s 占位符：%s" % (_TOKEN, xml[:80])
    return par


def _display_width(s: str) -> int:
    """粗略显示宽度（CJK/全角=2，其它=1），用于 zh 体例专栏行对齐主持人。"""
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E7F or ch in "（）【】《》“”、，。：；！？—…" else 1
    return w


def _blank(xml: str) -> etree._Element:
    root = etree.fromstring(('<w:snippet xmlns:w="%s">' % W + xml + "</w:snippet>").encode("utf-8"))
    return root[0]


# ── 表格构建 ───────────────────────────────────────────────────────────


def _build_table(mode_table: dict, rows: list[tuple[str, list[list[str]]]],
                 *, used: list[str]) -> etree._Element:
    """按行原型拼 `<w:tbl>`；`rows` = [(行原型名, 每个 cell 的文本列表)]。"""
    tbl = etree.Element(q("tbl"))
    for ch in _fragments(mode_table["open"]):
        tbl.append(ch)
    for name, cell_values in rows:
        proto = mode_table["rows"].get(name)
        if proto is None:
            used.append(f"missing-row-prototype:{name}")
            continue
        tr = etree.Element(q("tr"))
        if proto.get("trPr"):
            for ch in _fragments(proto["trPr"]):
                tr.append(ch)
        for ci, cell in enumerate(proto["cells"]):
            tc = etree.Element(q("tc"))
            if cell.get("tcPr"):
                for ch in _fragments(cell["tcPr"]):
                    tc.append(ch)
            values = cell_values[ci] if ci < len(cell_values) else []
            if cell["slot"] == "none":
                for px in cell["paras"]:
                    tc.append(_blank(px))
            else:
                if not cell["paras"]:
                    tc.append(etree.Element(q("p")))
                    continue
                tmpl = cell["paras"][0]
                for v in (values or [""]):
                    tc.append(_clone_par(tmpl, v))
            tr.append(tc)
        tbl.append(tr)
    return tbl


def _zh_host_text(issue: "_collect.IssueData") -> str:
    """zh 体例：专栏/主持人**整行合并**，用空格把主持人顶到同实例的位置。"""
    col = issue.column
    assert col is not None
    label = f"{col.label}：{col.name}"
    pad = max(2, 62 - _display_width(label))
    host = f"主持人：{col.host}" if col.host else ""
    return label + (" " * pad if host else "") + host


def _issue_table_rows(issue: "_collect.IssueData", mode_name: str, *, annual: bool
                      ) -> list[tuple[str, list[list[str]]]]:
    bi = mode_name in ("bi", "annual_bi")
    rows: list[tuple[str, list[list[str]]]] = []
    if bi:
        rows.append(("en_section", [["英文目录"]]))
        en_col = issue.en_column
        if en_col is not None and en_col.name:
            label = en_col.label or "Column"
            rows.append(("en_column", [[f"{label}: {en_col.name}"]]))
        for e in issue.en_entries:
            rows.append(("en_entry", [[" ".join(e.title_lines)], [", ".join(e.authors)]]))
        rows.append(("zh_section", [["中文目录"]]))
        if issue.column is not None:
            if _has_row(issue, mode_name, "host2"):
                rows.append(("host2", [[f"{issue.column.label}：{issue.column.name}"],
                                       [f"主持人：{issue.column.host}" if issue.column.host else ""]]))
            elif _has_row(issue, mode_name, "host_merged"):
                rows.append(("host_merged", [[_zh_host_text(issue)]]))
        for e in issue.zh_entries:
            title = list(e.title_lines) if not annual else (["".join(e.title_lines)] if e.title_lines else [""])
            rows.append(("zh_entry", [title, ["，".join(e.authors)]]))
    else:
        if issue.column is not None:
            if _has_row(issue, mode_name, "host_merged"):
                rows.append(("host_merged", [[_zh_host_text(issue)]]))
            else:
                rows.append(("host2", [[f"{issue.column.label}：{issue.column.name}"],
                                       [f"主持人：{issue.column.host}" if issue.column.host else ""]]))
        for e in issue.zh_entries:
            title = ["".join(e.title_lines)] if e.title_lines else [""]
            rows.append(("zh_entry", [title, ["，".join(e.authors)]]))
    return rows


def _has_row(issue: "_collect.IssueData", mode_name: str, row: str) -> bool:
    """该模式原型里有没有这一行（年度实例某些块没有专栏行 → 同实例省略）。"""
    return row in _TRACK["rows"].get(mode_name, set())


#: 渲染期间记录实际用到的原型（诊断用；同时让 `_has_row` 不看全局）
_TRACK: dict = {"rows": {}, "used": []}


# ── 文档骨架 ───────────────────────────────────────────────────────────


def _master_parts() -> tuple[dict, list]:
    z = zipfile.ZipFile(_MASTER)
    order = z.namelist()
    parts = {name: z.read(name) for name in order}
    return parts, order


def _document_root() -> etree._Element:
    parts, _order = _master_parts()
    return etree.fromstring(parts["word/document.xml"])


def _write_docx(root: etree._Element, out_path: str) -> None:
    """固定 zip 时间戳/顺序写出；先临时文件再 rename（覆盖不留 .bak）。

    ⚠️ 根标签必须**逐字节沿用母版原始 `<w:document …>`**（含全部 xmlns 声明）：
    lxml 重序列化只保留「实际用到」的命名空间声明，而母版根上的
    `mc:Ignorable="w14 w15 … wp14"` 仍引用这些前缀 —— 缺声明 = MCE 无效，
    Word 打开时报「发现无法读取的内容」并要求修复（B-225 交付后用户实测，2026-10-25 修复）。
    正文仍用 lxml 序列化（清理无用命名空间、保持确定性），只拼接母版根前缀。
    """
    etree.cleanup_namespaces(root)
    parts, order = _master_parts()
    master_doc = parts["word/document.xml"]
    m = re.search(rb"<w:document[^>]*>", master_doc)
    if m is None:
        raise ValueError("母版 word/document.xml 缺 <w:document> 根标签")
    body = root.find(q("body"))
    if body is None:
        raise ValueError("渲染树缺 <w:body>")
    body_xml = etree.tostring(body, encoding="UTF-8")
    doc_xml = master_doc[:m.start()] + m.group(0) + body_xml + b"</w:document>"
    parts = dict(parts)
    parts["word/document.xml"] = doc_xml
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tmp = out_path + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name in order:
                zi = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                z.writestr(zi, parts[name])
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _body(root: etree._Element) -> etree._Element:
    body = root.find(q("body"))
    for ch in list(body):
        if ch.tag != q("sectPr"):
            body.remove(ch)
    return body


def _insert(body: etree._Element, children: list) -> None:
    sect = body.find(q("sectPr"))
    at = list(body).index(sect) if sect is not None else len(body)
    for i, ch in enumerate(children):
        body.insert(at + i, ch)


# ── 当期渲染 ───────────────────────────────────────────────────────────


def _abstract_block(mode: dict, art: "_collect.Article", *, join_title: bool) -> list:
    children: list = []
    title_lines = art.title_lines
    if join_title:
        title_lines = ["".join(title_lines)] if title_lines else []
    for line in title_lines:
        children.append(_clone_par(mode["abs_title"], line))
    affs = list(art.affiliations) + [""] * len(art.authors)
    for i, author in enumerate(art.authors):
        aff = affs[i] if i < len(affs) else ""
        text = f"{author}（{aff}）" if aff else author
        children.append(_clone_par(mode["abs_author"], text))
    children.append(_clone_par(mode["abs_body"], "摘要：" + art.abstract))
    return children


def render_issue_docx(issue: "_collect.IssueData", out_path: str) -> None:
    """当期：期头 3 行 + 目录（可含英文段）+ 专栏/主持人 + 论文摘要块。"""
    protos = _protos()
    mode_name = "bi" if issue.with_en_toc else "zh"
    mode = protos["modes"][mode_name]
    _TRACK["rows"] = {name: set(m["table"]["rows"]) for name, m in protos["modes"].items()}
    _TRACK["used"] = []
    root = _document_root()
    body = _body(root)
    children: list = []
    for xml, line in zip(mode["header"], issue.header_lines):
        children.append(_clone_par(xml, line))
    children.append(_blank(mode["blank"]))
    children.append(_clone_par(mode["toc_title"],
                               "中英文对照目录" if issue.with_en_toc else "目录"))
    rows = _issue_table_rows(issue, mode_name, annual=False)
    children.append(_build_table(mode["table"], rows, used=_TRACK["used"]))
    for xml in mode["after_table"]:
        children.append(_blank(xml))
    blocks = [a for a in issue.articles if a.abstract.strip()]
    if blocks:
        # 实例顺序：目录表 → N 个空段 →「论文摘要」节标题（14pt 加粗居中）→ 论文块
        children.append(_clone_par(mode["abs_section"], "论文摘要"))
    for idx, art in enumerate(blocks):
        children.extend(_abstract_block(mode, art, join_title=not issue.with_en_toc))
        if idx < len(blocks) - 1:
            for xml in mode["abs_gap"]:
                children.append(_blank(xml))
    for xml in mode["tail"]:
        children.append(_blank(xml))
    _insert(body, children)
    _write_docx(root, out_path)


# ── 年度渲染 ───────────────────────────────────────────────────────────


def render_annual_docx(issues: list, year: int, out_path: str) -> None:
    """年度：逐期拼接「期头 + 目录」，块间空段按年度实例；不含摘要。"""
    protos = _protos()
    _TRACK["rows"] = {name: set(m["table"]["rows"]) for name, m in protos["modes"].items()}
    _TRACK["used"] = []
    root = _document_root()
    body = _body(root)
    children: list = []
    for bi, issue in enumerate(issues):
        mode_name = "annual_bi" if issue.with_en_toc else "annual_zh"
        mode = protos["modes"][mode_name]
        for xml, line in zip(mode["header"], issue.header_lines):
            children.append(_clone_par(xml, line))
        # 年度实例：期头 3 行后**直接**接目录标题（没有当期那种空段）
        children.append(_clone_par(mode["toc_title"],
                                   "中英文对照目录" if issue.with_en_toc else "目录"))
        rows = _issue_table_rows(issue, mode_name, annual=True)
        children.append(_build_table(mode["table"], rows, used=_TRACK["used"]))
        if bi < len(issues) - 1:
            gap = (mode.get("block_gap") or [])[:4]
            for xml in gap:
                children.append(_blank(xml))
        else:
            for xml in mode["tail"]:
                children.append(_blank(xml))
    _insert(body, children)
    _write_docx(root, out_path)
