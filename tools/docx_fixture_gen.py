#!/usr/bin/env python3
r"""docx_fixture_gen.py —— hermetic DOCX / coop 测试夹具生成器（纯 stdlib、离线、确定性）。

为什么要有它（B-146 波2-2 / 批106）
------------------------------------
`Engine/tests/` 里二十多个测试各自手写 `make_docx()`：zipfile 手拼最小 OOXML，
脚注 / 编号 / 表格 / 核心属性的写法各有出入；两段式 coop（`--coop-taskbook-only`
→ 外部 chat 回填 → `--coop-replies`）的夹具（源 docx + 元数据 + 回填/状态 JSON）
更是散落在测试里。本工具把「造一份合法 DOCX」「造一套 coop 夹具」收成可 import
的库函数 + 一个 CLI，供测试/维护者复用；**不新增 coop 独立 CLI**（coop 只是本工具
的一个 fixture 名字）。

来源声明
--------
本文件是**外部原型（批105 外部 Claude 的 1557 行初版）的硬化重写**：能力集（最小
OOXML 包 / 脚注 / 编号 / 表格 / 核心属性 / 坏 zip / coop 目录 / `--self-test`）沿用
该原型的结构，实现与验收按本仓 DoD 独立重写并逐条实跑；关键修正见模块尾部
「相对外部原型的硬化点」。外部原型只作设计输入，不整文件拷入，也不把其自报数字
当成本工具的证据。

设计约束
--------
* **纯标准库**：只用 `argparse/ast/contextlib/hashlib/io/json/os/posixpath/re/sys/
  tempfile/zipfile/xml`；不 import `python-docx`、不 import 引擎模块、不联网；
* **确定性**：zip 条目时间固定 `(1980, 1, 1, 0, 0, 0)`、`ZIP_DEFLATED`、固定压缩
  级别 9、固定条目顺序；同一参数两次生成逐字节相同（sha256 相同）；
* **不写仓库**：输出只写调用方给的 `path` / `out_dir`；`--self-test` 全在 tempfile；
* **hermetic**：self-test 在进程内调 `main()`（不 spawn 子进程、不读真实稿件库）。

库 API
------
    make_docx(path, *, paras=(), footnotes=(), numbering=False,
              table_cells=(), meta=None, numbering_xml=None, styles=False,
              footnote_anchor=-1) -> str
    make_broken_docx(path) -> str
    make_coop_fixture(out_dir) -> dict

`paras` 的元素可以是：
  * `str`：普通段落；
  * `(text, num_id)` / `(text, num_id, ilvl)`：带 `w:numPr` 的编号段落；
  * `{"text": str, "num_id": str|int|None, "ilvl": int, "style": str|None}`。
`numbering=True` 生成默认 decimal 单级 `word/numbering.xml`；`numbering_xml=…`
直接写入调用方给的编号定义（与 `numbering` 二选一），供需要自定义 `lvlText` /
多级定义的测试复用。`footnotes` 的引用标记按 `footnote_anchor` 追加到指定正文段
（默认最后一段；该段不存在时新建空段）。`meta` 写 `docProps/core.xml`。

CLI
---
    python3 tools/docx_fixture_gen.py --list [--json]
    python3 tools/docx_fixture_gen.py --json                 # = --list --json
    python3 tools/docx_fixture_gen.py --out DIR --fixture NAME [--json]
    python3 tools/docx_fixture_gen.py --out DIR --all [--json]
    python3 tools/docx_fixture_gen.py --self-test [--json]

退出码（写死）：`0` 成功；`1` 生成后自校验失败 / I/O 失败；`2` 用法错误。
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
import posixpath
import re
import sys
import tempfile
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

FIXTURE_VERSION = "1.0.0"
#: zip 条目固定时间戳（同 `Engine/tests/test_docxxml_deterministic.py` 的确定性口径）。
ZIP_FIXED_TIME = (1980, 1, 1, 0, 0, 0)
ZIP_DEFLATED_LEVEL = 9

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"

REL_OFFICE_DOCUMENT = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
REL_FOOTNOTES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
)
REL_NUMBERING = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"
)
REL_STYLES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
)
REL_CORE_PROPERTIES = (
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
)

CT_DOCUMENT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
CT_FOOTNOTES = "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
CT_NUMBERING = "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"
CT_STYLES = "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"
CT_CORE = "application/vnd.openxmlformats-package.core-properties+xml"

_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_REQUIRED_PARTS = (
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
    "word/_rels/document.xml.rels",
)

#: 坏 zip 夹具的固定内容（非空、非 zip）。
BROKEN_DOCX_BYTES = b"not a ZIP archive: deliberate broken DOCX fixture\n"

#: coop 夹具文件名（顺序 = --json/manifest 输出顺序，保证确定性）。
COOP_SOURCE_NAME = "source.docx"
COOP_METADATA_NAME = "metadata.json"
COOP_REPLY_NAME = "chat_reply.json"
COOP_REPLY_MD_NAME = "reply.md"
COOP_STATE_NAME = "coop_state.json"
COOP_FINGERPRINT_NAME = "coop_fingerprint.json"
COOP_FILES = (
    COOP_SOURCE_NAME,
    COOP_METADATA_NAME,
    COOP_REPLY_NAME,
    COOP_REPLY_MD_NAME,
    COOP_STATE_NAME,
    COOP_FINGERPRINT_NAME,
)

_STATE_PATH_PREFIX = "<fixture>"
_COOP_STEM = "Fixture_Article"

# ── coop 夹具的固定内容（全部虚构；与真引擎 ph1 的输出对齐，见报告）─────────
#: 与外部原型相同的合成正文/脚注：真引擎 docx 通道读出的文献键为
#: `Zhang2020` / `Li2021`（作者姓 + 年份，首字母大写），第 2 条脚注产出
#: `FN-2`（citation_unmatched），模板占位字段产出 `META-myvolnumber`。
_COOP_PARAS = (
    "DOCX 夹具与协同初排（测试稿）",
    "张三",
    "摘要：本文是纯合成的测试稿，用于验证 DOCX 夹具生成与两段式协同流程。",
    "关键词：逻辑；测试；夹具",
    "正文第一段，引用文献[1]并说明测试范围。",
    "正文第二段，脚注引用用于核对作者信息与文献条目。",
    "参考文献",
    "[1] 张三. 关于测试文献的说明[J]. 逻辑学研究, 2020, 13(2): 1--10.",
    "[2] 李四. 测试文献二[J]. 逻辑学研究, 2021, 14(3): 11--20.",
)
_COOP_FOOTNOTES = (
    "作者信息：张三  示例大学哲学系；邮箱 zhangsan@example.edu.cn",
    "李四 2021",
)
#: 脚注引用挂在第 6 段（0 基索引 5）。
_COOP_FOOTNOTE_ANCHOR = 5

_COOP_METADATA = {
    "schema_version": 1,
    "title_cn": "DOCX 夹具与协同初排（测试稿）",
    "title_en": "A DOCX Fixture for Cooperative First-Proof Testing",
    "running_title_cn": "DOCX 夹具",
    "running_title_en": "DOCX Fixture",
    "abstract_cn": "本文是纯合成的测试稿，用于验证 DOCX 夹具生成与两段式协同流程。",
    "abstract_en": ("This synthetic article exercises the DOCX fixture generator and the "
                    "two-phase cooperative workflow."),
    "keywords_cn": "逻辑；测试；夹具",
    "keywords_en": "logic; testing; fixture",
    "authors": [
        {
            "name_cn": "张三",
            "name_en": "Zhang San",
            "affiliations_cn": ["示例大学哲学系"],
            "affiliations_en": ["Department of Philosophy, Example University"],
            "email": "zhangsan@example.edu.cn",
        },
        {
            "name_cn": "李四",
            "name_en": "Li Si",
            "affiliations_cn": ["示例研究院逻辑室"],
            "affiliations_en": ["Logic Division, Example Institute"],
            "email": "lisi@example.org",
        },
    ],
}

_COOP_BIB_ZHANG = (
    "@article{Zhang2020,\n"
    "  author = {张三},\n"
    "  title = {关于测试文献的说明（核实版）},\n"
    "  journal = {逻辑学研究},\n"
    "  year = {2020},\n"
    "  volume = {13},\n"
    "  number = {2},\n"
    "  pages = {1--10},\n"
    "}"
)
_COOP_BIB_LI = (
    "@article{Li2021,\n"
    "  author = {李四},\n"
    "  title = {测试文献二（核实版）},\n"
    "  journal = {逻辑学研究},\n"
    "  year = {2021},\n"
    "}"
)
_COOP_BIB_KEYS = ("Zhang2020", "Li2021")
#: 低置信项样例：与真引擎 ph1 在本夹具 source.docx 上的产出逐字段对齐
#: （`FN-2` = 第 2 条脚注 citation_unmatched；`META-myvolnumber` = 模板占位宏）。
_COOP_LOW_ITEMS = (
    {
        "id": "FN-2",
        "kind": "footnote",
        "title": "脚注 #2（citation_unmatched）",
        "evidence": "李四 2021",
        "need": "核对脚注引用与正文/文献表是否对应；只抽取，不补造。",
        "fields": ["note", "match_key"],
        "key": "",
        "status": "pending",
        "subkind": "citation_unmatched",
        "target": ".tex",
        "macro": "",
        "current": "",
    },
    {
        "id": "META-myvolnumber",
        "kind": "metadata",
        "title": "元数据 `myvolnumber`",
        "evidence": "myvolnumber",
        "need": "元数据字段 `myvolnumber` 缺失；请给出值，或回 unresolved。",
        "fields": ["myvolnumber", "value"],
        "key": "",
        "status": "pending",
        "subkind": "",
        "target": ".tex",
        "macro": "myvolnumber",
        "current": "x",
    },
)
_COOP_REPLY_OBJECTS = (
    {"id": "FN-2", "verdict": "confirmed", "span": "李四 2021"},
    {"id": "META-myvolnumber", "verdict": "corrected", "field": "value",
     "value": "13", "span": "myvolnumber"},
)

#: `meta` 允许的键 → core.xml 标签；别名重复指定同一属性 → ValueError。
_META_KEYS = {
    "title": "title",
    "subject": "subject",
    "creator": "creator",
    "author": "creator",
    "keywords": "keywords",
    "description": "description",
    "comments": "description",
    "last_modified_by": "last_modified_by",
    "revision": "revision",
    "created": "created",
    "modified": "modified",
    "category": "category",
}
_META_TAGS = (
    ("title", "dc:title"),
    ("subject", "dc:subject"),
    ("creator", "dc:creator"),
    ("keywords", "cp:keywords"),
    ("description", "dc:description"),
    ("category", "cp:category"),
)
#: 允许 import 的模块白名单（self-test 静态审计用；全为 stdlib）。
_ALLOWED_IMPORTS = frozenset({
    "argparse", "ast", "collections", "contextlib", "hashlib", "io", "json",
    "os", "posixpath", "re", "sys", "tempfile", "xml", "zipfile",
})


class FixtureError(RuntimeError):
    """生成后自校验失败（CLI 映射为退出码 1）。"""


# ── 文本 / XML 小工具 ─────────────────────────────────────────────────────


def _xesc(value: object) -> str:
    return _xml_escape(str(value))


def _text_list(value: object, label: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise TypeError(f"{label} 必须是字符串序列，不是单个字符串")
    out: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"{label}[{idx}] 必须是字符串（收到 {type(item).__name__}）")
        out.append(item)
    return out


def _normalize_paras(paras: object) -> list[dict[str, object]]:
    """把 paras 归一化成 [{"text","style","num_id","ilvl"}]；非法类型明确报错。"""
    if isinstance(paras, (str, bytes)) or not isinstance(paras, Iterable):
        raise TypeError("paras 必须是段落序列（str 或 (text, num_id[, ilvl]) 或 dict）")
    out: list[dict[str, object]] = []
    for idx, item in enumerate(paras):
        if isinstance(item, str):
            out.append({"text": item, "style": None, "num_id": None, "ilvl": 0})
            continue
        if isinstance(item, Mapping):
            unknown = sorted(k for k in item if k not in ("text", "style", "num_id", "ilvl"))
            if unknown:
                raise ValueError(f"paras[{idx}] 有未识别的键：{unknown}")
            text = item.get("text")
            if not isinstance(text, str):
                raise TypeError(f"paras[{idx}]['text'] 必须是字符串")
            style = item.get("style")
            if style is not None and not isinstance(style, str):
                raise TypeError(f"paras[{idx}]['style'] 必须是字符串或 None")
            num_id = item.get("num_id")
            if num_id is not None and not isinstance(num_id, (str, int)):
                raise TypeError(f"paras[{idx}]['num_id'] 必须是字符串/整数或 None")
            ilvl = item.get("ilvl", 0)
            if not isinstance(ilvl, int) or isinstance(ilvl, bool) or ilvl < 0:
                raise ValueError(f"paras[{idx}]['ilvl'] 必须是 >=0 的整数")
            out.append({"text": text, "style": style,
                        "num_id": None if num_id is None else str(num_id),
                        "ilvl": ilvl})
            continue
        if isinstance(item, (tuple, list)):
            if len(item) not in (2, 3):
                raise TypeError(
                    f"paras[{idx}] 元组必须是 (text, num_id) 或 (text, num_id, ilvl)")
            text, num_id = item[0], item[1]
            ilvl = item[2] if len(item) == 3 else 0
            if not isinstance(text, str):
                raise TypeError(f"paras[{idx}][0] 必须是字符串")
            if not isinstance(num_id, (str, int)) or isinstance(num_id, bool):
                raise TypeError(f"paras[{idx}][1] num_id 必须是字符串/整数")
            if not isinstance(ilvl, int) or isinstance(ilvl, bool) or ilvl < 0:
                raise ValueError(f"paras[{idx}][2] ilvl 必须是 >=0 的整数")
            out.append({"text": text, "style": None,
                        "num_id": str(num_id), "ilvl": ilvl})
            continue
        raise TypeError(f"paras[{idx}] 类型不支持：{type(item).__name__}")
    return out


def _normalize_table(table_cells: object) -> list[list[str]]:
    if isinstance(table_cells, (str, bytes)) or not isinstance(table_cells, Iterable):
        raise TypeError("table_cells 必须是「行 → 单元格」的二维字符串序列")
    rows: list[list[str]] = []
    for r_idx, row in enumerate(table_cells):
        if isinstance(row, (str, bytes)) or not isinstance(row, Iterable):
            raise TypeError(f"table_cells[{r_idx}] 必须是一行单元格，不是字符串")
        cells: list[str] = []
        for c_idx, cell in enumerate(row):
            if not isinstance(cell, str):
                raise TypeError(
                    f"table_cells[{r_idx}][{c_idx}] 必须是字符串"
                    f"（收到 {type(cell).__name__}）")
            cells.append(cell)
        rows.append(cells)
    if not rows:
        return []
    width = len(rows[0])
    if width == 0:
        raise ValueError("table_cells 的第一行为空：表格至少要有一列")
    for r_idx, row in enumerate(rows):
        if len(row) != width:
            raise ValueError(
                f"table_cells 必须是矩形：第 0 行 {width} 列，第 {r_idx} 行 {len(row)} 列")
    return rows


def _paragraph_xml(para: Mapping[str, object],
                   footnote_ids: Sequence[int] = ()) -> str:
    text = str(para["text"])
    ppr = ""
    style = para.get("style")
    if style:
        ppr += f'<w:pStyle w:val="{_xesc(style)}"/>'
    num_id = para.get("num_id")
    if num_id is not None:
        ppr += (f'<w:numPr><w:ilvl w:val="{int(para.get("ilvl") or 0)}"/>'
                f'<w:numId w:val="{_xesc(num_id)}"/></w:numPr>')
    runs = ""
    if text:
        runs += f'<w:r><w:t xml:space="preserve">{_xesc(text)}</w:t></w:r>'
    for fid in footnote_ids:
        runs += f'<w:r><w:footnoteReference w:id="{int(fid)}"/></w:r>'
    if not ppr and not runs:
        return "<w:p/>"
    if ppr:
        return f"<w:p><w:pPr>{ppr}</w:pPr>{runs}</w:p>"
    return f"<w:p>{runs}</w:p>"


_TABLE_CELL_WIDTH = 2400


def _table_xml(rows: Sequence[Sequence[str]]) -> str:
    if not rows:
        return ""
    width = len(rows[0])
    grid = "".join(f'<w:gridCol w:w="{_TABLE_CELL_WIDTH}"/>' for _ in range(width))
    body = []
    for row in rows:
        cells = []
        for cell in row:
            para = (_paragraph_xml({"text": cell}) if cell else "<w:p/>")
            cells.append(
                f'<w:tc><w:tcPr><w:tcW w:w="{_TABLE_CELL_WIDTH}" w:type="dxa"/></w:tcPr>'
                f"{para}</w:tc>")
        body.append("<w:tr>" + "".join(cells) + "</w:tr>")
    return (
        "<w:tbl>"
        '<w:tblPr><w:tblW w:w="0" w:type="auto"/>'
        '<w:tblLayout w:type="fixed"/><w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '</w:tblBorders></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>"
        + "".join(body)
        + "</w:tbl>"
    )


def _normalize_meta(meta: object) -> dict[str, str]:
    if not isinstance(meta, Mapping):
        raise TypeError("meta 必须是映射（dict）")
    norm: dict[str, str] = {}
    for key, value in meta.items():
        if key not in _META_KEYS:
            raise ValueError(f"meta 里有未识别的键：{key!r}")
        target = _META_KEYS[str(key)]
        if target in norm:
            raise ValueError(f"meta 的别名重复指定了同一属性：{target}")
        if value is None:
            continue
        if not isinstance(value, str):
            raise TypeError(f"meta[{key!r}] 必须是字符串或 None")
        norm[target] = value
    return norm


def _core_xml(meta: Mapping[str, object]) -> str:
    norm = _normalize_meta(meta)
    created = norm.get("created", "1980-01-01T00:00:00Z")
    modified = norm.get("modified", "1980-01-01T00:00:00Z")
    revision = norm.get("revision", "1")
    last_by = norm.get("last_modified_by", "docx_fixture_gen")
    parts = [
        f'<cp:coreProperties xmlns:cp="{CP_NS}" xmlns:dc="{DC_NS}" '
        f'xmlns:dcterms="{DCTERMS_NS}" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    ]
    for key, tag in _META_TAGS:
        if norm.get(key):
            parts.append(f"<{tag}>{_xesc(norm[key])}</{tag}>")
    if last_by:
        parts.append(f"<cp:lastModifiedBy>{_xesc(last_by)}</cp:lastModifiedBy>")
    if revision:
        parts.append(f"<cp:revision>{_xesc(revision)}</cp:revision>")
    parts.append(f'<dcterms:created xsi:type="dcterms:W3CDTF">{_xesc(created)}</dcterms:created>')
    parts.append(f'<dcterms:modified xsi:type="dcterms:W3CDTF">{_xesc(modified)}</dcterms:modified>')
    parts.append("</cp:coreProperties>")
    return _XML_DECL + "".join(parts)


def _content_types_xml(*, footnotes: bool, numbering: bool,
                       styles: bool, core: bool) -> str:
    overrides = [
        f'<Override PartName="/word/document.xml" ContentType="{CT_DOCUMENT}"/>'
    ]
    if footnotes:
        overrides.append(
            f'<Override PartName="/word/footnotes.xml" ContentType="{CT_FOOTNOTES}"/>')
    if numbering:
        overrides.append(
            f'<Override PartName="/word/numbering.xml" ContentType="{CT_NUMBERING}"/>')
    if styles:
        overrides.append(
            f'<Override PartName="/word/styles.xml" ContentType="{CT_STYLES}"/>')
    if core:
        overrides.append(
            f'<Override PartName="/docProps/core.xml" ContentType="{CT_CORE}"/>')
    return (
        _XML_DECL
        + f'<Types xmlns="{CT_NS}">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + "</Types>"
    )


def _root_rels_xml(*, core: bool) -> str:
    rels = [
        f'<Relationship Id="rId1" Type="{REL_OFFICE_DOCUMENT}" '
        'Target="word/document.xml"/>'
    ]
    next_id = 2
    if core:
        rels.append(
            f'<Relationship Id="rId{next_id}" Type="{REL_CORE_PROPERTIES}" '
            'Target="docProps/core.xml"/>')
    return _XML_DECL + f'<Relationships xmlns="{REL_NS}">' + "".join(rels) + "</Relationships>"


def _document_rels_xml(*, footnotes: bool, numbering: bool, styles: bool) -> str:
    rels = []
    next_id = 1
    if footnotes:
        rels.append(
            f'<Relationship Id="rId{next_id}" Type="{REL_FOOTNOTES}" '
            'Target="footnotes.xml"/>')
        next_id += 1
    if numbering:
        rels.append(
            f'<Relationship Id="rId{next_id}" Type="{REL_NUMBERING}" '
            'Target="numbering.xml"/>')
        next_id += 1
    if styles:
        rels.append(
            f'<Relationship Id="rId{next_id}" Type="{REL_STYLES}" '
            'Target="styles.xml"/>')
        next_id += 1
    return _XML_DECL + f'<Relationships xmlns="{REL_NS}">' + "".join(rels) + "</Relationships>"


def _footnotes_xml(footnotes: Sequence[str]) -> str:
    special = (
        '<w:footnote w:type="separator" w:id="-1">'
        "<w:p><w:r><w:separator/></w:r></w:p></w:footnote>"
        '<w:footnote w:type="continuationSeparator" w:id="0">'
        "<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>"
    )
    items = "".join(
        f'<w:footnote w:id="{idx}">'
        f'<w:p><w:r><w:t xml:space="preserve">{_xesc(text)}</w:t></w:r></w:p>'
        "</w:footnote>"
        for idx, text in enumerate(footnotes, start=1)
    )
    return _XML_DECL + f'<w:footnotes xmlns:w="{W_NS}">' + special + items + "</w:footnotes>"


def _default_numbering_xml() -> str:
    return (
        _XML_DECL
        + f'<w:numbering xmlns:w="{W_NS}">'
        '<w:abstractNum w:abstractNumId="0">'
        '<w:multiLevelType w:val="singleLevel"/>'
        '<w:lvl w:ilvl="0">'
        '<w:start w:val="1"/>'
        '<w:numFmt w:val="decimal"/>'
        '<w:lvlText w:val="%1."/>'
        '<w:lvlJc w:val="left"/>'
        '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>'
        "</w:lvl>"
        "</w:abstractNum>"
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        "</w:numbering>"
    )


def _styles_xml() -> str:
    def style(style_id: str, name: str) -> str:
        return (
            f'<w:style w:type="paragraph" w:styleId="{style_id}">'
            f'<w:name w:val="{name}"/><w:basedOn w:val="Normal"/><w:qFormat/>'
            "</w:style>"
        )

    return (
        _XML_DECL
        + f'<w:styles xmlns:w="{W_NS}">'
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体" w:hAnsi="Times New Roman"/>'
        '<w:sz w:val="24"/></w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/></w:style>'
        + style("Title", "Title")
        + style("Heading1", "heading 1")
        + style("Heading2", "heading 2")
        + "</w:styles>"
    )


def _zip_bytes(parts: Sequence[tuple[str, str]]) -> bytes:
    """固定条目顺序 + 固定时间戳 + 固定压缩参数 → 逐字节确定的 OOXML 包。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(
            buf, "w", compression=zipfile.ZIP_DEFLATED,
            compresslevel=ZIP_DEFLATED_LEVEL) as zf:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=ZIP_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            info.internal_attr = 0
            zf.writestr(info, text.encode("utf-8"),
                        compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=ZIP_DEFLATED_LEVEL)
    return buf.getvalue()


def _write_bytes(path: str | os.PathLike[str], data: bytes) -> str:
    p = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(p))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)
    return p


# ── 公开 API：DOCX ────────────────────────────────────────────────────────


def _build_docx_bytes(
        paras: object,
        footnotes: object,
        numbering: bool,
        table_cells: object,
        meta: Mapping[str, object] | None,
        *,
        numbering_xml: str | None = None,
        styles: bool = False,
        footnote_anchor: int = -1,
) -> bytes:
    para_list = _normalize_paras(paras)
    fn_list = _text_list(footnotes, "footnotes")
    table_rows = _normalize_table(table_cells)
    if numbering and numbering_xml is not None:
        raise ValueError("numbering=True 与 numbering_xml 不能同时给（二选一）")
    if numbering_xml is not None:
        if not isinstance(numbering_xml, str) or not numbering_xml.strip():
            raise TypeError("numbering_xml 必须是非空字符串（完整 numbering.xml 内容）")
    use_numbering = bool(numbering) or numbering_xml is not None
    use_styles = bool(styles)
    has_core = meta is not None

    if fn_list:
        if para_list:
            if not isinstance(footnote_anchor, int) or isinstance(footnote_anchor, bool):
                raise TypeError("footnote_anchor 必须是整数")
            if not -len(para_list) <= footnote_anchor < len(para_list):
                raise ValueError(
                    f"footnote_anchor={footnote_anchor} 超出段落范围 "
                    f"0..{len(para_list) - 1}")
            anchor = footnote_anchor % len(para_list)
            refs_by_index = {anchor: tuple(range(1, len(fn_list) + 1))}
        else:
            refs_by_index = {0: tuple(range(1, len(fn_list) + 1))}
            para_list = [{"text": "", "style": None, "num_id": None, "ilvl": 0}]
    else:
        refs_by_index = {}

    paragraph_xml = [
        _paragraph_xml(para, refs_by_index.get(idx, ()))
        for idx, para in enumerate(para_list)
    ]
    body = "".join(paragraph_xml) + _table_xml(table_rows)
    document_xml = (
        _XML_DECL
        + f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    )

    parts: list[tuple[str, str]] = [
        ("[Content_Types].xml", _content_types_xml(
            footnotes=bool(fn_list), numbering=use_numbering,
            styles=use_styles, core=has_core)),
        ("_rels/.rels", _root_rels_xml(core=has_core)),
    ]
    if has_core:
        parts.append(("docProps/core.xml", _core_xml(meta or {})))
    parts.append(("word/document.xml", document_xml))
    parts.append(("word/_rels/document.xml.rels", _document_rels_xml(
        footnotes=bool(fn_list), numbering=use_numbering, styles=use_styles)))
    if fn_list:
        parts.append(("word/footnotes.xml", _footnotes_xml(fn_list)))
    if use_numbering:
        parts.append(("word/numbering.xml",
                      numbering_xml if numbering_xml is not None
                      else _default_numbering_xml()))
    if use_styles:
        parts.append(("word/styles.xml", _styles_xml()))
    return _zip_bytes(parts)


def make_docx(
        path: str | os.PathLike[str],
        *,
        paras: object = (),
        footnotes: object = (),
        numbering: bool = False,
        table_cells: object = (),
        meta: Mapping[str, object] | None = None,
        numbering_xml: str | None = None,
        styles: bool = False,
        footnote_anchor: int = -1,
) -> str:
    """生成一份最小但合法、逐字节确定的 .docx；返回路径（生成后立即自校验）。"""
    p = os.fspath(path)
    data = _build_docx_bytes(
        paras, footnotes, numbering, table_cells, meta,
        numbering_xml=numbering_xml, styles=styles,
        footnote_anchor=footnote_anchor)
    _write_bytes(p, data)
    _validate_docx(p)
    return p


def make_broken_docx(path: str | os.PathLike[str]) -> str:
    """写一个确定的非 ZIP 坏文件（错误路径测试用）；返回路径。"""
    p = os.fspath(path)
    _write_bytes(p, BROKEN_DOCX_BYTES)
    return p


# ── 自校验：包结构 / 关系 / XML ───────────────────────────────────────────


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(zf.read(name))
    except (KeyError, ET.ParseError) as exc:
        raise FixtureError(f"{name} 不是合法 XML：{exc}") from exc


def _rel_map(root: ET.Element, base: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in root:
        if _local(rel.tag) != "Relationship":
            continue
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        if not target:
            raise FixtureError("Relationship 缺少 Target")
        resolved = (target.lstrip("/") if target.startswith("/")
                    else posixpath.normpath(posixpath.join(base, target)))
        out[resolved] = rel.get("Type") or ""
    return out


def _int_attr(el: ET.Element, ns: str, name: str) -> int | None:
    raw = el.get(f"{{{ns}}}{name}")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _validate_docx(path: str | os.PathLike[str]) -> dict[str, object]:
    """校验一份生成的 docx 的包结构/关系/ContentType/脚注引用；返回统计信息。"""
    p = os.fspath(path)
    if not zipfile.is_zipfile(p):
        raise FixtureError(f"不是 zip/docx：{p}")
    try:
        with zipfile.ZipFile(p) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise FixtureError(f"zip 条目 CRC 失败：{bad}")
            names = set(zf.namelist())
            missing = [name for name in _REQUIRED_PARTS if name not in names]
            if missing:
                raise FixtureError("缺少必需部件：" + "、".join(missing))
            for name in sorted(names):
                if name.endswith((".xml", ".rels")):
                    _read_xml(zf, name)

            ct_root = _read_xml(zf, "[Content_Types].xml")
            if _local(ct_root.tag) != "Types":
                raise FixtureError("[Content_Types].xml 根元素不是 Types")
            overrides = {
                el.get("PartName") or "": el.get("ContentType") or ""
                for el in ct_root if _local(el.tag) == "Override"
            }
            if overrides.get("/word/document.xml") != CT_DOCUMENT:
                raise FixtureError("document.xml 的 ContentType 不正确")

            root_map = _rel_map(_read_xml(zf, "_rels/.rels"), "")
            if REL_OFFICE_DOCUMENT not in root_map.values():
                raise FixtureError("根关系缺少 officeDocument")
            if "word/document.xml" not in root_map:
                raise FixtureError("根关系没有指向 word/document.xml")

            doc_map = _rel_map(
                _read_xml(zf, "word/_rels/document.xml.rels"), "word")
            for target in doc_map:
                if target not in names:
                    raise FixtureError(f"关系目标不存在：{target}")

            doc_root = _read_xml(zf, "word/document.xml")
            if _local(doc_root.tag) != "document":
                raise FixtureError("word/document.xml 根元素不是 document")
            body = next((el for el in doc_root if _local(el.tag) == "body"), None)
            if body is None:
                raise FixtureError("word/document.xml 没有 body")

            has_footnotes = "word/footnotes.xml" in names
            has_numbering = "word/numbering.xml" in names
            has_styles = "word/styles.xml" in names
            has_core = "docProps/core.xml" in names

            if has_footnotes:
                if overrides.get("/word/footnotes.xml") != CT_FOOTNOTES:
                    raise FixtureError("footnotes.xml 的 ContentType 不正确")
                if REL_FOOTNOTES not in doc_map.values():
                    raise FixtureError("document.xml.rels 没有 footnotes 关系")
                fn_root = _read_xml(zf, "word/footnotes.xml")
                user_ids = {
                    cid for el in fn_root
                    if _local(el.tag) == "footnote"
                    and (el.get(f"{{{W_NS}}}type") or "")
                    not in ("separator", "continuationSeparator")
                    for cid in [_int_attr(el, W_NS, "id")]
                    if cid is not None
                }
                refs = {
                    cid for el in doc_root.iter(f"{{{W_NS}}}footnoteReference")
                    for cid in [_int_attr(el, W_NS, "id")]
                    if cid is not None
                }
                if refs != user_ids:
                    raise FixtureError(
                        f"脚注引用与 footnotes.xml 不一致：refs={sorted(refs)} "
                        f"ids={sorted(user_ids)}")
            elif REL_FOOTNOTES in doc_map.values():
                raise FixtureError("有 footnotes 关系但没有 word/footnotes.xml")

            if has_numbering:
                if overrides.get("/word/numbering.xml") != CT_NUMBERING:
                    raise FixtureError("numbering.xml 的 ContentType 不正确")
                if REL_NUMBERING not in doc_map.values():
                    raise FixtureError("document.xml.rels 没有 numbering 关系")
                num_root = _read_xml(zf, "word/numbering.xml")
                if _local(num_root.tag) != "numbering":
                    raise FixtureError("numbering.xml 根元素不是 numbering")
            elif REL_NUMBERING in doc_map.values():
                raise FixtureError("有 numbering 关系但没有 word/numbering.xml")

            if has_styles:
                if overrides.get("/word/styles.xml") != CT_STYLES:
                    raise FixtureError("styles.xml 的 ContentType 不正确")
                if REL_STYLES not in doc_map.values():
                    raise FixtureError("document.xml.rels 没有 styles 关系")
                if _local(_read_xml(zf, "word/styles.xml").tag) != "styles":
                    raise FixtureError("styles.xml 根元素不是 styles")

            if has_core:
                if overrides.get("/docProps/core.xml") != CT_CORE:
                    raise FixtureError("core.xml 的 ContentType 不正确")
                if "docProps/core.xml" not in root_map:
                    raise FixtureError("根关系没有指向 docProps/core.xml")
                if REL_CORE_PROPERTIES not in root_map.values():
                    raise FixtureError("根关系缺少 core-properties 类型")
                if _local(_read_xml(zf, "docProps/core.xml").tag) != "coreProperties":
                    raise FixtureError("core.xml 根元素不是 coreProperties")

            return {
                "parts": sorted(names),
                "paragraphs": sum(1 for el in body if _local(el.tag) == "p"),
                "tables": sum(1 for el in body if _local(el.tag) == "tbl"),
                "cells": sum(1 for _el in doc_root.iter(f"{{{W_NS}}}tc")),
                "footnotes": sum(
                    1 for _el in doc_root.iter(f"{{{W_NS}}}footnoteReference")),
                "has_numbering": has_numbering,
                "has_styles": has_styles,
                "has_core": has_core,
            }
    except FixtureError:
        raise
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise FixtureError(f"读取 docx 失败：{type(exc).__name__}: {exc}") from exc


def _validate_broken(path: str | os.PathLike[str]) -> None:
    p = os.fspath(path)
    if not os.path.isfile(p):
        raise FixtureError(f"坏件不存在：{p}")
    if os.path.getsize(p) == 0:
        raise FixtureError("坏件为空；错误路径测试需要非空非 zip 文件")
    if zipfile.is_zipfile(p):
        raise FixtureError("坏件竟然是合法 zip")


# ── 确定性 JSON / 指纹 / coop ─────────────────────────────────────────────


def _json_text(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_json(path: str | os.PathLike[str], obj: object) -> str:
    return _write_bytes(path, _json_text(obj).encode("utf-8"))


def _read_json(path: str | os.PathLike[str]) -> object:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise FixtureError(
            f"{os.path.basename(os.fspath(path))} 不是合法 JSON：{exc}") from exc


def _sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parameter_fingerprint(params: Mapping[str, object]) -> str:
    """与 `Engine/slcn/coop.py: parameter_fingerprint()` 同口径。

    引擎口径：去掉 `fingerprint` 键 → `json.dumps(ensure_ascii=False,
    sort_keys=True, separators=(",", ":"))` → sha256(utf-8)。真引擎交叉验证见报告。
    """
    clean = {k: v for k, v in params.items() if k != "fingerprint"}
    canonical = json.dumps(
        clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _coop_reply_text() -> str:
    lines = [
        "示例外部 chat 回复（纯虚构，供两段式 coop 夹具测试；不联网）。",
        "",
        "```bibtex",
        _COOP_BIB_ZHANG,
        _COOP_BIB_LI,
        "```",
        "",
        "```json",
    ]
    lines += [
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for obj in _COOP_REPLY_OBJECTS
    ]
    lines += ["```", ""]
    return "\n".join(lines)


def _coop_state_params(source_sha: str) -> dict[str, object]:
    """coop 状态里的参数样例：与 `coop.build_params()` 同字段、同计算口径。

    路径一律用 `<fixture>` 占位（保证夹具可移植/逐字节确定）；真引擎验证时把
    占位符替换为真实绝对路径后，`coop.build_params()` 应逐字段相等（见报告）。
    `metadata=""`：真引擎明确拒绝 `--metadata-json` 进入两段式（convert.py 的
    `--metadata-json` 分支），所以 coop 链路的元数据由 source.docx 自身携带；
    `metadata.json` 按 GUI 人工元数据契约（`--metadata-json`）单独消费。
    """
    core: dict[str, object] = {
        "source": f"{_STATE_PATH_PREFIX}/{COOP_SOURCE_NAME}",
        "source_sha256": source_sha,
        "metadata": "",
        "metadata_sha256": "",
        "out_root": f"{_STATE_PATH_PREFIX}/out",
        "stem": _COOP_STEM,
        "lang": "zh",
        "sty": f"{_STATE_PATH_PREFIX}/SLCN-MIX.sty",
        "skeleton": f"{_STATE_PATH_PREFIX}/CN_template.tex",
        "verify_refs": True,
        "no_ai_merge": True,
        "end_list_priority": False,
        "number_cite_map": False,
        "c_class_union": True,
        "stage_dir": f"{_STATE_PATH_PREFIX}/out/{_COOP_STEM}_待核",
    }
    core["fingerprint"] = _parameter_fingerprint(core)
    return core


def _coop_state(
        source_sha: str,
        params: Mapping[str, object],
        fingerprint: str,
) -> dict[str, object]:
    bib_keys = list(_COOP_BIB_KEYS)
    low_ids = [str(item["id"]) for item in _COOP_LOW_ITEMS]
    stage_dir = f"{_STATE_PATH_PREFIX}/out/{_COOP_STEM}_待核"
    return {
        "schema": 1,
        "phase": "ph1",
        "created_at": "1980-01-01T00:00:00+0000",
        "engine_version": "docx_fixture_gen:" + FIXTURE_VERSION,
        "source": {"path": f"{_STATE_PATH_PREFIX}/{COOP_SOURCE_NAME}",
                   "sha256": source_sha},
        "params": dict(params),
        "fingerprint": fingerprint,
        "taskbook": f"{stage_dir}/{_COOP_STEM}_文献核实.md",
        "stage_dir": stage_dir,
        "final_dir": f"{_STATE_PATH_PREFIX}/out/{_COOP_STEM}",
        "keys": {"bib": bib_keys, "low_confidence": low_ids,
                 "all": bib_keys + low_ids},
        "low_confidence": [dict(item) for item in _COOP_LOW_ITEMS],
        "replied": [],
        "pending": bib_keys + low_ids,
        "budget": {
            "article_tokens": 30000,
            "batch_tokens": 1000000,
            "note": "示例值；外部 chat 通道不产生 API 费用",
        },
        "history": [],
    }


def make_coop_fixture(out_dir: str | os.PathLike[str]) -> dict[str, object]:
    """在 `out_dir` 下生成两段式 coop 的最小夹具；返回路径与 sha256 清单。

    产物（文件名固定；内容不含本机绝对路径 → 同样逐字节确定）：
      · `source.docx`：合成正文 + 2 条脚注 + 参考文献区（真引擎 Word 通道可读）；
      · `metadata.json`：`--metadata-json` 契约（2 位作者的多作者样例）；
      · `reply.md`：可直接交给 `--coop-replies` 的 chat 回填文本；
      · `chat_reply.json`：同一回复的结构化包装（reply_text + bibtex + 低置信对象）；
      · `coop_state.json` / `coop_fingerprint.json`：与 `coop.build_state()` 同 schema
        的状态/指纹样例（路径为 `<fixture>` 占位符，见模块文档）。
    """
    d = os.path.abspath(os.fspath(out_dir))
    os.makedirs(d, exist_ok=True)
    source = os.path.join(d, COOP_SOURCE_NAME)
    metadata = os.path.join(d, COOP_METADATA_NAME)
    reply_json = os.path.join(d, COOP_REPLY_NAME)
    reply_md = os.path.join(d, COOP_REPLY_MD_NAME)
    state_path = os.path.join(d, COOP_STATE_NAME)
    fingerprint_path = os.path.join(d, COOP_FINGERPRINT_NAME)

    _write_bytes(source, _build_docx_bytes(
        _COOP_PARAS, _COOP_FOOTNOTES, False, (), None,
        footnote_anchor=_COOP_FOOTNOTE_ANCHOR))
    _write_json(metadata, _COOP_METADATA)
    reply_text = _coop_reply_text()
    _write_bytes(reply_md, reply_text.encode("utf-8"))
    _write_json(reply_json, {
        "schema": "slcn.chat_reply.v1",
        "fixture": "coop",
        "note": "示例 JSON；reply_text 与同目录 reply.md 逐字节相同。",
        "reply_text": reply_text,
        "bibtex": [
            {"key": _COOP_BIB_KEYS[0], "text": _COOP_BIB_ZHANG},
            {"key": _COOP_BIB_KEYS[1], "text": _COOP_BIB_LI},
        ],
        "low_confidence": [dict(obj) for obj in _COOP_REPLY_OBJECTS],
    })

    source_sha = _sha256_file(source)
    metadata_sha = _sha256_file(metadata)
    params = _coop_state_params(source_sha)
    fp = str(params["fingerprint"])
    _write_json(state_path, _coop_state(source_sha, params, fp))
    _write_json(fingerprint_path, {
        "algorithm": "sha256",
        "canonicalization": (
            "json.dumps(params, ensure_ascii=False, sort_keys=True, "
            "separators=(',', ':')) 后 sha256（去掉 fingerprint 键）"),
        "fingerprint": fp,
        "params": params,
        "source_sha256": source_sha,
        "metadata_sha256": metadata_sha,
        "state_file": COOP_STATE_NAME,
        "note": ("路径是 <fixture> 占位符；实际续跑时由 coop.build_params() "
                 "换成真实绝对路径。"),
    })

    _validate_docx(source)
    _validate_coop_fixture(d)
    files = {
        "source": source,
        "metadata": metadata,
        "reply": reply_json,
        "reply_md": reply_md,
        "state": state_path,
        "fingerprint": fingerprint_path,
    }
    return {
        "dir": d,
        "fixture": "coop",
        "files": files,
        "sha256": {name: _sha256_file(p) for name, p in files.items()},
    }


def _fenced_json_objects(text: str) -> list[dict[str, object]]:
    match = re.search(r"```json\s*\n(.*?)```", text, re.S)
    if not match:
        return []
    out: list[dict[str, object]] = []
    for line in match.group(1).splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _check_placeholder(value: object, where: str) -> None:
    """状态/指纹样例里的**路径字段**必须是 `<fixture>` 占位符（禁止绝对路径）。"""
    if not isinstance(value, str):
        raise FixtureError(f"{where} 必须是字符串路径（收到 {type(value).__name__}）")
    if value == "":
        return
    if value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value):
        raise FixtureError(f"{where} 出现了绝对路径：{value!r}（样例必须可移植）")
    if not value.startswith(_STATE_PATH_PREFIX):
        raise FixtureError(f"{where} 的路径缺少 {_STATE_PATH_PREFIX} 占位：{value!r}")


def _validate_coop_fixture(out_dir: str | os.PathLike[str]) -> dict[str, object]:
    d = os.path.abspath(os.fspath(out_dir))
    paths = {name: os.path.join(d, name) for name in COOP_FILES}
    missing = [name for name in COOP_FILES if not os.path.isfile(paths[name])]
    if missing:
        raise FixtureError("coop 夹具缺少文件：" + "、".join(missing))

    source_info = _validate_docx(paths[COOP_SOURCE_NAME])

    metadata = _read_json(paths[COOP_METADATA_NAME])
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise FixtureError("metadata.json 必须是 schema_version=1 的对象")
    authors = metadata.get("authors")
    if not isinstance(authors, list) or len(authors) < 2:
        raise FixtureError("metadata.json 至少要列出 2 位 authors（多作者样例）")

    reply = _read_json(paths[COOP_REPLY_NAME])
    if not isinstance(reply, dict) or not isinstance(reply.get("reply_text"), str):
        raise FixtureError("chat_reply.json 缺少字符串 reply_text")
    reply_text = str(reply["reply_text"])
    reply_md_bytes = open(paths[COOP_REPLY_MD_NAME], "rb").read()
    if reply_md_bytes != reply_text.encode("utf-8"):
        raise FixtureError("reply.md 与 chat_reply.json 的 reply_text 不一致")
    objs = _fenced_json_objects(reply_text)
    if len(objs) != len(_COOP_REPLY_OBJECTS):
        raise FixtureError(
            f"reply_text 里的 JSON 对象数 {len(objs)} != {len(_COOP_REPLY_OBJECTS)}")
    if not isinstance(reply.get("bibtex"), list):
        raise FixtureError("chat_reply.json 缺少 bibtex 数组")
    if [str(it.get("key")) for it in reply["bibtex"] if isinstance(it, dict)] \
            != list(_COOP_BIB_KEYS):
        raise FixtureError("chat_reply.json 的 bibtex 键与夹具键清单不一致")

    state = _read_json(paths[COOP_STATE_NAME])
    fingerprint_doc = _read_json(paths[COOP_FINGERPRINT_NAME])
    if not isinstance(state, dict) or not isinstance(fingerprint_doc, dict):
        raise FixtureError("coop 状态/指纹必须是 JSON 对象")
    if state.get("schema") != 1 or state.get("phase") != "ph1":
        raise FixtureError("coop_state.json 必须是 schema=1 / phase=ph1")
    params = state.get("params")
    if not isinstance(params, dict):
        raise FixtureError("coop_state.json 缺少 params 对象")
    fp = _parameter_fingerprint(params)
    if state.get("fingerprint") != fp or params.get("fingerprint") != fp:
        raise FixtureError("coop_state.json 的参数指纹对不上")
    if fingerprint_doc.get("fingerprint") != fp:
        raise FixtureError("coop_fingerprint.json 与状态指纹不一致")
    if fingerprint_doc.get("source_sha256") != _sha256_file(paths[COOP_SOURCE_NAME]):
        raise FixtureError("指纹样例里的 source sha256 与 source.docx 不一致")
    if fingerprint_doc.get("metadata_sha256") != _sha256_file(paths[COOP_METADATA_NAME]):
        raise FixtureError("指纹样例里的 metadata sha256 与 metadata.json 不一致")
    source_obj = state.get("source")
    if (not isinstance(source_obj, dict)
            or source_obj.get("sha256") != _sha256_file(paths[COOP_SOURCE_NAME])):
        raise FixtureError("状态里的 source.sha256 与 source.docx 不一致")

    keys = state.get("keys")
    if not isinstance(keys, dict):
        raise FixtureError("coop_state.json 缺少 keys")
    bib_keys = [str(k) for k in keys.get("bib") or []]
    if bib_keys != list(_COOP_BIB_KEYS):
        raise FixtureError("状态里的 bib 键清单与夹具不一致")
    for key in bib_keys:
        if f"@article{{{key}," not in reply_text:
            raise FixtureError(f"回复里缺少文献键 {key}")
    low_items = state.get("low_confidence")
    if not isinstance(low_items, list) or not low_items:
        raise FixtureError("coop_state.json 缺少 low_confidence")
    by_id = {str(item.get("id")): item for item in low_items if isinstance(item, dict)}
    if [str(k) for k in keys.get("low_confidence") or []] != list(by_id):
        raise FixtureError("keys.low_confidence 与 low_confidence 顺序不一致")
    for obj in objs:
        item = by_id.get(str(obj.get("id") or ""))
        if item is None:
            raise FixtureError(f"回复里有白名单外的低置信 ID：{obj.get('id')}")
        span = str(obj.get("span") or "")
        evidence = str(item.get("evidence") or "")
        if not span or span not in evidence:
            raise FixtureError(f"{obj.get('id')} 的 span 不是 evidence 的逐字子串")
        if obj.get("verdict") not in ("confirmed", "corrected", "unresolved"):
            raise FixtureError(f"{obj.get('id')} 的 verdict 不合法")

    # 可移植性：状态/指纹样例里的**路径字段**必须是 <fixture> 占位（禁止绝对路径）。
    for where, value in (
            ("coop_state.source.path", source_obj.get("path")),
            ("coop_state.taskbook", state.get("taskbook")),
            ("coop_state.stage_dir", state.get("stage_dir")),
            ("coop_state.final_dir", state.get("final_dir")),
            ("coop_state.params.source", params.get("source")),
            ("coop_state.params.metadata", params.get("metadata")),
            ("coop_state.params.out_root", params.get("out_root")),
            ("coop_state.params.sty", params.get("sty")),
            ("coop_state.params.skeleton", params.get("skeleton")),
            ("coop_state.params.stage_dir", params.get("stage_dir")),
    ):
        _check_placeholder(value, where)
    for name in (COOP_METADATA_NAME, COOP_REPLY_NAME, COOP_STATE_NAME,
                 COOP_FINGERPRINT_NAME):
        text = open(paths[name], encoding="utf-8").read()
        if d in text:
            raise FixtureError(f"{name} 出现了 --out 绝对路径（夹具必须可移植）")

    return {
        "dir": d,
        "source": source_info,
        "files": list(COOP_FILES),
        "fingerprint": fp,
        "bib_keys": bib_keys,
        "low_ids": list(by_id),
    }


# ── fixture 定义 / 清单 / CLI ─────────────────────────────────────────────

_BASE_PARAS = (
    "DOCX 夹具",
    "测试作者",
    "摘要：这是纯合成的测试摘要。",
    "关键词：逻辑；测试；夹具",
    "正文第一段。",
    "正文第二段。",
)
_ARTICLE_PARAS = (
    "DOCX 夹具：标题、多作者与引文（测试稿）",
    "张三¹  李四²",
    "（¹示例大学哲学系；²示例研究院逻辑室）",
    "摘要：本文是纯合成的测试稿，覆盖标题、多作者、引文与参考文献区。",
    "关键词：逻辑；测试；夹具",
    "正文第一段，引用文献[1]并说明测试范围。",
    "正文第二段，引用文献[2]。",
    "参考文献",
    "[1] 张三. 关于测试文献的说明[J]. 逻辑学研究, 2020, 13(2): 1--10.",
    "[2] 李四. 测试文献二[J]. 逻辑学研究, 2021, 14(3): 11--20.",
)
_CITATION_PARAS = (
    "引文夹具",
    "正文引用文献[1]与[2]，用于核对编号映射。",
    "参考文献",
    "[1] 张三. 关于测试文献的说明[J]. 逻辑学研究, 2020, 13(2): 1--10.",
    "[2] 李四. 测试文献二[J]. 逻辑学研究, 2021, 14(3): 11--20.",
)
_META = {
    "title": "夹具标题 & <测试>",
    "author": "测试作者",
    "subject": "DOCX fixture",
    "keywords": "logic, fixture",
    "description": "A & B < C",
    "revision": "2",
    "created": "1980-01-01T00:00:00Z",
    "modified": "1980-01-01T00:00:00Z",
}

_FIXTURE_SPECS: dict[str, dict[str, object]] = {
    "minimal": {
        "kind": "docx",
        "description": "段落：最小正文（无脚注/编号/表格/核心属性/样式）",
    },
    "headings": {
        "kind": "docx",
        "description": "标题/小节：word/styles.xml（Title/Heading1/Heading2）+ pStyle 段落",
    },
    "article": {
        "kind": "docx",
        "description": "标题+多作者+摘要/关键词+正文引文+参考文献区的合成文章头",
    },
    "citation": {
        "kind": "docx",
        "description": "引文：正文 [n] 与文末编号参考文献清单（编号映射用）",
    },
    "footnotes": {
        "kind": "docx",
        "description": "脚注：正文 + 2 条脚注（关系/ContentType/引用编号齐全）",
    },
    "numbering": {
        "kind": "docx",
        "description": "编号：word/numbering.xml（decimal 单级）+ w:numPr 编号段落",
    },
    "table": {
        "kind": "docx",
        "description": "表格：正文 + 2×2 中文/转义单元格",
    },
    "meta": {
        "kind": "docx",
        "description": "元数据：docProps/core.xml 核心属性（含 XML 转义）",
    },
    "full": {
        "kind": "docx",
        "description": "全套：脚注 + 编号 + 表格 + 核心属性 + 标题样式",
    },
    "broken": {
        "kind": "broken",
        "description": "坏 zip：非空、非 ZIP 的坏文件（错误路径测试）",
    },
    "coop": {
        "kind": "dir",
        "description": (
            "两段式 coop 目录：source.docx + metadata.json + reply.md + "
            "chat_reply.json + 状态/指纹 JSON 样例"),
    },
}
_FIXTURE_NAMES = tuple(_FIXTURE_SPECS)


def _fixture_docx_kwargs(name: str) -> dict[str, object]:
    if name == "minimal":
        return {"paras": ("DOCX 夹具最小样例", "中文正文 & <测试>。")}
    if name == "headings":
        return {
            "styles": True,
            "paras": (
                {"text": "DOCX 夹具题名", "style": "Title"},
                {"text": "一、标题与段落", "style": "Heading1"},
                {"text": "正文第一段（标题样式不影响段落文字）。"},
                {"text": "1.1 小节", "style": "Heading2"},
                {"text": "正文第二段。"},
            ),
        }
    if name == "article":
        return {"paras": _ARTICLE_PARAS}
    if name == "citation":
        return {"paras": _CITATION_PARAS}
    if name == "footnotes":
        return {
            "paras": _BASE_PARAS,
            "footnotes": ("脚注一：纯合成文本。", "脚注二：含 & 与 < 的转义检查。"),
        }
    if name == "numbering":
        return {
            "paras": (
                "编号夹具",
                ("第一条编号段落。", "1"),
                ("第二条编号段落。", "1"),
                "（编号由 word/numbering.xml 的 decimal 单级定义提供。）",
            ),
            "numbering": True,
        }
    if name == "table":
        return {
            "paras": ("表格夹具",),
            "table_cells": (("甲", "乙"), ("丙 & 丁", "戊 < 己")),
        }
    if name == "meta":
        return {"paras": _BASE_PARAS[:5], "meta": dict(_META)}
    if name == "full":
        return {
            "paras": (
                "全套夹具",
                {"text": "一、全套", "style": "Heading1"},
                "正文第一段，引用文献[1]。",
                ("编号条目一", "1"),
                ("编号条目二", "1"),
            ),
            "footnotes": ("脚注一：纯合成文本。", "脚注二：含 & 与 < 的转义检查。"),
            "numbering": True,
            "styles": True,
            "table_cells": (("甲", "乙"), ("丙 & 丁", "戊 < 己")),
            "meta": dict(_META),
        }
    raise ValueError(f"未知 docx fixture：{name}")


def _relative(out_root: str, path: str) -> str:
    return os.path.relpath(path, out_root).replace(os.sep, "/")


def _file_record(out_root: str, path: str) -> dict[str, object]:
    return {
        "path": _relative(out_root, path),
        "bytes": os.path.getsize(path),
        "sha256": _sha256_file(path),
    }


def _generate_fixture(
        name: str, out_root: str, *, coop_subdir: bool = True) -> dict[str, object]:
    spec = _FIXTURE_SPECS[name]
    kind = str(spec["kind"])
    if name == "coop":
        # 单个 `--fixture coop` 直接写进 --out；`--all` 写进 --out/coop（避免与
        # minimal.docx 等平铺混在一起）。
        target = os.path.join(out_root, "coop") if coop_subdir else out_root
        info = make_coop_fixture(target)
        paths = [str(p) for p in info["files"].values()]  # type: ignore[index]
    elif name == "broken":
        path = os.path.join(out_root, "broken.docx")
        make_broken_docx(path)
        _validate_broken(path)
        paths = [path]
    else:
        path = os.path.join(out_root, name + ".docx")
        make_docx(path, **_fixture_docx_kwargs(name))
        _validate_docx(path)
        paths = [path]
    return {
        "name": name,
        "kind": kind,
        "description": spec["description"],
        "files": [_file_record(out_root, p) for p in paths],
    }


def _manifest(out_root: str, names: Sequence[str]) -> dict[str, object]:
    coop_subdir = len(names) != 1
    return {
        "ok": True,
        "fixture_version": FIXTURE_VERSION,
        "out": out_root,
        "fixtures": [
            _generate_fixture(name, out_root, coop_subdir=coop_subdir)
            for name in names
        ],
    }


def _print_manifest(doc: Mapping[str, object]) -> None:
    for fixture in doc.get("fixtures", []):  # type: ignore[union-attr]
        for rec in fixture.get("files", []):  # type: ignore[union-attr]
            print(f"{fixture['name']}\t{rec['path']}\t{rec['bytes']} bytes\t"
                  f"sha256={rec['sha256']}")


def _list_doc() -> dict[str, object]:
    return {
        "ok": True,
        "fixture_version": FIXTURE_VERSION,
        "fixtures": [
            {"name": name, "kind": _FIXTURE_SPECS[name]["kind"],
             "description": _FIXTURE_SPECS[name]["description"]}
            for name in _FIXTURE_NAMES
        ],
    }


def _run_self_test(as_json: bool = False) -> int:
    """跑 hermetic 自检；exit 0 = 全绿。

    全部在进程内完成：tempfile 临时目录 + 直接调 `main()`（不 spawn 子进程），
    不联网、不读真实稿件库、不依赖 python-docx。每个用例独立捕获异常（一条崩掉
    不会吞掉其余用例的 PASS/FAIL）。
    """
    passed = 0
    failed = 0

    def check(name: str, cond: bool, detail: str = "") -> bool:
        nonlocal passed, failed
        ok = bool(cond)
        if ok:
            passed += 1
        else:
            failed += 1
        print(("PASS  " if ok else "FAIL  ") + name
              + (("  " + str(detail)) if detail else ""))
        return ok

    def doc_text(path: str) -> str:
        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("word/document.xml"))
        return "".join(el.text or "" for el in root.iter(f"{{{W_NS}}}t"))

    def run_main(argv: Sequence[str]) -> int:
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                return int(main(list(argv)))
        except SystemExit as exc:  # argparse 用法错误
            return int(exc.code) if isinstance(exc.code, int) else 2

    with tempfile.TemporaryDirectory(prefix="docx_fixture_gen_selftest_") as td:
        def case_minimal() -> None:
            path = make_docx(os.path.join(td, "minimal.docx"),
                             paras=("DOCX 夹具", "中文正文 & <测试>"))
            info = _validate_docx(path)
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            check("① 最小 docx：四个必需部件齐全",
                  set(_REQUIRED_PARTS) <= names, str(sorted(names)))
            check("① 中文与 XML 特殊字符往返",
                  doc_text(path) == "DOCX 夹具中文正文 & <测试>", doc_text(path))
            check("① 自校验统计正确",
                  info["paragraphs"] == 2 and not info["has_numbering"]
                  and not info["has_core"] and not info["has_styles"], str(info))

        def case_deterministic() -> None:
            kwargs = dict(
                paras=("标题", "正文", ("编号段", "1")),
                footnotes=("脚注",), numbering=True,
                table_cells=(("甲", "乙"),),
                meta={"title": "确定性", "created": "1980-01-01T00:00:00Z"},
            )
            a = make_docx(os.path.join(td, "det-a.docx"), **kwargs)
            b = make_docx(os.path.join(td, "det-b.docx"), **kwargs)
            with open(a, "rb") as fa, open(b, "rb") as fb:
                same = fa.read() == fb.read()
            check("② DOCX 两次生成逐字节相同（sha256 相同）",
                  same and _sha256_file(a) == _sha256_file(b), _sha256_file(a))
            with zipfile.ZipFile(a) as zf:
                infos = zf.infolist()
            check("② 全部 zip 条目时间戳固定 (1980,1,1,0,0,0)",
                  all(i.date_time == ZIP_FIXED_TIME for i in infos),
                  str({i.date_time for i in infos}))
            check("② 全部 zip 条目 ZIP_DEFLATED 且 create_system=0",
                  all(i.compress_type == zipfile.ZIP_DEFLATED
                      and i.create_system == 0 for i in infos))

        def case_styles() -> None:
            path = make_docx(os.path.join(td, "styles.docx"), styles=True, paras=(
                {"text": "题名", "style": "Title"},
                {"text": "一节", "style": "Heading1"},
                "正文。"))
            info = _validate_docx(path)
            with zipfile.ZipFile(path) as zf:
                doc = ET.fromstring(zf.read("word/document.xml"))
                styles = zf.read("word/styles.xml").decode()
                ct = zf.read("[Content_Types].xml").decode()
                rels = zf.read("word/_rels/document.xml.rels").decode()
            used = [el.get(f"{{{W_NS}}}val")
                    for el in doc.iter(f"{{{W_NS}}}pStyle")]
            check("③ 标题：styles.xml + pStyle 引用 + ContentType/关系",
                  info["has_styles"] and used == ["Title", "Heading1"]
                  and 'w:styleId="Heading1"' in styles and CT_STYLES in ct
                  and "styles.xml" in rels, f"used={used}")

        def case_footnotes() -> None:
            path = make_docx(os.path.join(td, "fn.docx"),
                             paras=("题名", "正文。"),
                             footnotes=("脚注一", "脚注二"))
            info = _validate_docx(path)
            with zipfile.ZipFile(path) as zf:
                doc = ET.fromstring(zf.read("word/document.xml"))
                fns = ET.fromstring(zf.read("word/footnotes.xml"))
                rels = zf.read("word/_rels/document.xml.rels").decode()
                ct = zf.read("[Content_Types].xml").decode()
            refs = [el.get(f"{{{W_NS}}}id")
                    for el in doc.iter(f"{{{W_NS}}}footnoteReference")]
            texts = ["".join(el.text or "" for el in fn.iter(f"{{{W_NS}}}t"))
                     for fn in fns
                     if (fn.get(f"{{{W_NS}}}type") or "") == ""]
            check("④ 脚注：两个引用 + 两条正文脚注 + 关系/ContentType",
                  refs == ["1", "2"] and texts == ["脚注一", "脚注二"]
                  and info["footnotes"] == 2 and CT_FOOTNOTES in ct
                  and "footnotes.xml" in rels, f"refs={refs} texts={texts}")
            only = make_docx(os.path.join(td, "fn-only.docx"),
                             paras=(), footnotes=("只有脚注",))
            check("④ 只有脚注、没有正文段落也能生成（引用放空段）",
                  _validate_docx(only)["footnotes"] == 1)
            anchored = make_docx(os.path.join(td, "fn-anchor.docx"),
                                 paras=("甲", "乙", "丙"),
                                 footnotes=("脚注",), footnote_anchor=0)
            with zipfile.ZipFile(anchored) as zf:
                doc = ET.fromstring(zf.read("word/document.xml"))
            first = next(iter(doc.iter(f"{{{W_NS}}}p")))
            check("④ footnote_anchor 把引用挂到指定段",
                  any(True for _ in first.iter(f"{{{W_NS}}}footnoteReference")))

        def case_numbering() -> None:
            path = make_docx(os.path.join(td, "num.docx"),
                             paras=("题名", ("条目一", "1"), ("条目二", "1")),
                             numbering=True)
            info = _validate_docx(path)
            with zipfile.ZipFile(path) as zf:
                num = ET.fromstring(zf.read("word/numbering.xml"))
                doc = ET.fromstring(zf.read("word/document.xml"))
                rels = zf.read("word/_rels/document.xml.rels").decode()
                ct = zf.read("[Content_Types].xml").decode()
            num_ids = [el.get(f"{{{W_NS}}}val")
                       for el in doc.iter(f"{{{W_NS}}}numId")]
            check("⑤ 编号：numbering.xml + numPr + 关系/ContentType",
                  info["has_numbering"] and num_ids == ["1", "1"]
                  and [el.get(f"{{{W_NS}}}numId")
                       for el in num.iter(f"{{{W_NS}}}num")] == ["1"]
                  and CT_NUMBERING in ct and "numbering.xml" in rels,
                  f"num_ids={num_ids}")
            custom = ('<w:numbering xmlns:w="%s"><w:abstractNum w:abstractNumId="7">'
                      '<w:lvl w:ilvl="0"><w:start w:val="3"/><w:numFmt w:val="decimal"/>'
                      '<w:lvlText w:val="[%%1]"/></w:lvl></w:abstractNum>'
                      '<w:num w:numId="9"><w:abstractNumId w:val="7"/></w:num>'
                      "</w:numbering>") % W_NS
            path2 = make_docx(os.path.join(td, "num-custom.docx"),
                              paras=("题名", ("条目", "9")),
                              numbering_xml=custom)
            with zipfile.ZipFile(path2) as zf:
                raw = zf.read("word/numbering.xml").decode()
            check("⑤ 自定义 numbering_xml 原样写入（供 batch68 类测试复用）",
                  raw == custom and _validate_docx(path2)["has_numbering"])

        def case_table() -> None:
            path = make_docx(
                os.path.join(td, "table.docx"), paras=("题名",),
                table_cells=(("甲", "乙"), ("丙 & 丁", "戊 < 己")))
            info = _validate_docx(path)
            with zipfile.ZipFile(path) as zf:
                doc = ET.fromstring(zf.read("word/document.xml"))
            rows = [el for el in doc.iter(f"{{{W_NS}}}tr")]
            cells = ["".join(t.text or "" for t in tc.iter(f"{{{W_NS}}}t"))
                     for tc in doc.iter(f"{{{W_NS}}}tc")]
            check("⑥ 表格：2×2 行列与中文/转义单元格",
                  len(rows) == 2 and cells == ["甲", "乙", "丙 & 丁", "戊 < 己"]
                  and info["tables"] == 1 and info["cells"] == 4,
                  f"rows={len(rows)} cells={cells}")
            try:
                make_docx(os.path.join(td, "bad-table.docx"),
                          table_cells=(("甲", "乙"), ("丙",)))
                ragged = False
            except ValueError:
                ragged = True
            check("⑥ 非矩形表格明确报错（ValueError）", ragged)

        def case_meta() -> None:
            path = make_docx(os.path.join(td, "meta.docx"),
                             paras=("题名",),
                             meta={"title": "标题 & <测试>", "author": "测试作者",
                                   "created": "1980-01-01T00:00:00Z"})
            info = _validate_docx(path)
            with zipfile.ZipFile(path) as zf:
                core = ET.fromstring(zf.read("docProps/core.xml"))
            titles = [el.text for el in core.iter() if _local(el.tag) == "title"]
            creators = [el.text for el in core.iter() if _local(el.tag) == "creator"]
            check("⑦ 核心属性：title/creator 经 XML 转义后往返",
                  info["has_core"] and titles == ["标题 & <测试>"]
                  and creators == ["测试作者"], f"{titles} / {creators}")
            try:
                make_docx(os.path.join(td, "bad-meta.docx"),
                          meta={"unknown_key": "x"})
                rejected = False
            except ValueError:
                rejected = True
            check("⑦ 未识别的 meta 键明确报错", rejected)

        def case_article_citation() -> None:
            path = make_docx(os.path.join(td, "article.docx"),
                             paras=_fixture_docx_kwargs("article")["paras"])
            text = doc_text(path)
            check("⑧ 文章/引文夹具：标题 + 两位作者 + 参考文献区齐全",
                  "DOCX 夹具：标题、多作者与引文" in text
                  and "张三" in text and "李四" in text
                  and "参考文献" in text and "[1]" in text and "[2]" in text)
            cited = make_docx(os.path.join(td, "citation.docx"),
                              paras=_CITATION_PARAS)
            check("⑧ 引文夹具：正文 [n] 与编号参考文献两行",
                  doc_text(cited).count("[") >= 4)

        def case_broken_and_corrupt() -> None:
            path = make_broken_docx(os.path.join(td, "broken.docx"))
            _validate_broken(path)
            check("⑨ 坏件：非空、非 zip",
                  os.path.getsize(path) > 0 and not zipfile.is_zipfile(path))
            good = make_docx(os.path.join(td, "corrupt.docx"), paras=("题名",))
            with open(good, "wb") as fh:
                fh.write(BROKEN_DOCX_BYTES)
            try:
                _validate_docx(good)
                rejected = False
            except FixtureError:
                rejected = True
            check("⑨ 自校验拒绝被破坏的 docx（CLI 映射退出码 1）", rejected)

        def case_coop() -> None:
            info = make_coop_fixture(os.path.join(td, "coop"))
            check("⑩ coop：六个产物文件 + sha256 清单齐全",
                  set(info["files"]) == {"source", "metadata", "reply", "reply_md",
                                         "state", "fingerprint"}
                  and set(info["sha256"]) == set(info["files"]),
                  str(sorted(info["sha256"])))
            files = info["files"]
            assert isinstance(files, dict)
            state = _read_json(str(files["state"]))
            reply = _read_json(str(files["reply"]))
            ok_types = isinstance(state, dict) and isinstance(reply, dict)
            check("⑩ coop：状态 schema/phase/键清单/指纹齐全且自洽",
                  ok_types and state["schema"] == 1 and state["phase"] == "ph1"
                  and state["keys"]["bib"] == list(_COOP_BIB_KEYS)
                  and state["fingerprint"] == _parameter_fingerprint(state["params"]))
            check("⑩ coop：reply.md 与 chat_reply.reply_text 逐字节相同",
                  open(str(files["reply_md"]), "rb").read()
                  == str(reply["reply_text"]).encode("utf-8"))
            objs = _fenced_json_objects(str(reply["reply_text"]))
            low = state["low_confidence"]
            check("⑩ coop：回复含 BibTeX + 2 条低置信 JSON，span 均自证",
                  "@article{Zhang2020" in reply["reply_text"] and len(objs) == 2
                  and all(obj["span"] in next(
                      it["evidence"] for it in low if it["id"] == obj["id"])
                      for obj in objs))
            meta_doc = _read_json(str(files["metadata"]))
            check("⑩ coop：metadata.json 为多作者 schema_version=1",
                  isinstance(meta_doc, dict) and meta_doc.get("schema_version") == 1
                  and len(meta_doc.get("authors") or []) == 2)
            state_text = open(str(files["state"]), encoding="utf-8").read()
            check("⑩ coop：状态/指纹里没有本机绝对路径（可移植）",
                  td not in state_text and str(info["dir"]) not in state_text)

        def case_coop_deterministic() -> None:
            a = make_coop_fixture(os.path.join(td, "coop-a"))
            b = make_coop_fixture(os.path.join(td, "coop-b"))
            same = all(a["sha256"][role] == b["sha256"][role]
                       for role in a["sha256"])
            check("⑪ coop：两个目录生成的六个文件逐字节相同", same,
                  str(a["sha256"]))

        def case_cli_list() -> None:
            rc = run_main(["--list"])
            rc_json = run_main(["--list", "--json"])
            bare = run_main(["--json"])
            check("⑫ CLI --list / --list --json / 裸 --json 均退出 0",
                  (rc, rc_json, bare) == (0, 0, 0))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--list"])
            check("⑫ --list 覆盖全部 fixture 名字",
                  all(name in out.getvalue() for name in _FIXTURE_NAMES))

        def case_cli_generate() -> None:
            out_dir = os.path.join(td, "cli-min")
            rc = run_main(["--out", out_dir, "--fixture", "minimal", "--json"])
            path = os.path.join(out_dir, "minimal.docx")
            check("⑬ CLI 生成 --fixture minimal 退出 0 且文件存在",
                  rc == 0 and os.path.isfile(path))
            coop_dir = os.path.join(td, "cli-coop")
            rc2 = run_main(["--out", coop_dir, "--fixture", "coop"])
            check("⑬ CLI 单生成 coop 直接落到 --out 目录",
                  rc2 == 0 and os.path.isfile(os.path.join(coop_dir, "source.docx"))
                  and os.path.isfile(os.path.join(coop_dir, "reply.md")))
            manifest_dir = os.path.join(td, "cli-json")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc3 = main(["--out", manifest_dir, "--fixture", "full", "--json"])
            try:
                doc = json.loads(buf.getvalue())
            except ValueError:
                doc = {}
            recs = (doc.get("fixtures") or [{}])[0].get("files") or []
            check("⑬ CLI --json：可解析且 sha256 与实际文件一致",
                  rc3 == 0 and doc.get("ok") is True and len(recs) == 1
                  and recs[0]["sha256"] == _sha256_file(
                      os.path.join(manifest_dir, "full.docx")))

        def case_cli_all() -> None:
            d1, d2 = os.path.join(td, "all-1"), os.path.join(td, "all-2")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r1 = main(["--out", d1, "--all", "--json"])
            m1 = json.loads(buf.getvalue())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r2 = main(["--out", d2, "--all", "--json"])
            m2 = json.loads(buf.getvalue())

            def shas(doc: Mapping[str, object]) -> dict[tuple[object, ...], object]:
                return {(f["name"], x["path"]): x["sha256"]
                        for f in doc.get("fixtures", [])  # type: ignore[union-attr]
                        for x in f["files"]}

            s1, s2 = shas(m1), shas(m2)
            check("⑭ CLI --all 两次生成逐文件 sha256 相同",
                  r1 == 0 and r2 == 0 and bool(s1) and s1 == s2,
                  f"files={len(s1)}")
            check("⑭ --all 覆盖全部 fixture 且 coop 在子目录",
                  {name for name, _path in s1} == set(_FIXTURE_NAMES)
                  and any(path.startswith("coop/") for _n, path in s1))

        def case_cli_usage() -> None:
            no_action = run_main([])
            no_out = run_main(["--fixture", "minimal"])
            unknown = run_main(["--out", os.path.join(td, "x"), "--fixture", "nope"])
            conflict = run_main(["--self-test", "--out", os.path.join(td, "y")])
            check("⑮ CLI 用法错误统一退出 2（无动作/缺 --out/未知 fixture/冲突）",
                  (no_action, no_out, unknown, conflict) == (2, 2, 2, 2),
                  f"{no_action}/{no_out}/{unknown}/{conflict}")

        def case_cli_failure() -> None:
            blocker = os.path.join(td, "blocked-out")
            with open(blocker, "w", encoding="utf-8") as fh:
                fh.write("not a directory\n")
            rc = run_main(["--out", blocker, "--fixture", "minimal"])
            check("⑯ CLI 生成/自校验失败 → 退出 1（不误报成功）", rc == 1, f"rc={rc}")

        def case_api_types() -> None:
            try:
                make_docx(os.path.join(td, "bad-paras.docx"), paras="不是序列")
                bad = False
            except TypeError:
                bad = True
            check("⑰ API 类型错误明确报错（paras 单字符串 → TypeError）", bad)

        def case_import_audit() -> None:
            source = open(os.path.abspath(__file__), encoding="utf-8").read()
            tree = ast.parse(source)
            mods: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods.add(node.module.split(".")[0])
            third = sorted(mods - _ALLOWED_IMPORTS - set(sys.stdlib_module_names))
            check("⑱ 导入审计：只 import 标准库白名单（无第三方/引擎模块）",
                  not third, f"extra={third}")

        cases: list[tuple[str, object]] = [
            ("① minimal", case_minimal),
            ("② determinism", case_deterministic),
            ("③ styles", case_styles),
            ("④ footnotes", case_footnotes),
            ("⑤ numbering", case_numbering),
            ("⑥ table", case_table),
            ("⑦ meta", case_meta),
            ("⑧ article/citation", case_article_citation),
            ("⑨ broken/corrupt", case_broken_and_corrupt),
            ("⑩ coop", case_coop),
            ("⑪ coop determinism", case_coop_deterministic),
            ("⑫ CLI list", case_cli_list),
            ("⑬ CLI generate", case_cli_generate),
            ("⑭ CLI all", case_cli_all),
            ("⑮ CLI usage", case_cli_usage),
            ("⑯ CLI failure", case_cli_failure),
            ("⑰ API types", case_api_types),
            ("⑱ import audit", case_import_audit),
        ]
        for label, fn in cases:
            try:
                fn()  # type: ignore[operator]
            except Exception as exc:  # noqa: BLE001 —— 用例崩掉要记成 FAIL，不吞掉
                check(f"{label}（用例异常）", False, f"{type(exc).__name__}: {exc}")

    total = passed + failed
    ok = failed == 0
    if as_json:
        print(json.dumps({
            "ok": ok,
            "fixture_version": FIXTURE_VERSION,
            "cases": len(cases),
            "passed": passed,
            "failed": failed,
            "assertions": total,
        }, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"\n自检：{len(cases)} 用例 / {total} 断言，通过 {passed}，失败 {failed}")
    return 0 if ok else 1


# ── main ──────────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="docx_fixture_gen.py",
        description="DOCX / coop 测试夹具生成器（纯标准库、离线、确定性）")
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="列出全部 fixture")
    action.add_argument("--fixture", metavar="NAME", help="生成一个 fixture")
    action.add_argument("--all", action="store_true", help="生成全部 fixture")
    action.add_argument("--self-test", action="store_true", help="跑 hermetic 自检")
    ap.add_argument("--out", metavar="DIR", help="输出目录（--fixture / --all 必填）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        if args.out or args.fixture or args.all:
            ap.error("--self-test 不能与 --out / --fixture / --all 同用")
        return _run_self_test(as_json=bool(args.json))

    if args.list:
        if args.out:
            ap.error("--list 不需要 --out")
        doc = _list_doc()
        if args.json:
            print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for item in doc["fixtures"]:  # type: ignore[index]
                print(f"{item['name']}\t{item['kind']}\t{item['description']}")
        return 0

    if args.fixture is None and not args.all:
        if args.json and not args.out:
            # 裸 `--json` = `--list --json`（外壳/CI 探清单用）。
            print(json.dumps(_list_doc(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        ap.error("请给出 --list / --out DIR --fixture NAME / --out DIR --all / "
                 "--self-test")

    if not args.out:
        ap.error("--fixture / --all 需要 --out DIR")
    if args.fixture and args.fixture not in _FIXTURE_SPECS:
        ap.error(f"未知 fixture：{args.fixture!r}；可选：{', '.join(_FIXTURE_NAMES)}")

    out_root = os.path.abspath(os.fspath(args.out))
    try:
        os.makedirs(out_root, exist_ok=True)
        names = [args.fixture] if args.fixture else list(_FIXTURE_NAMES)
        doc = _manifest(out_root, [str(n) for n in names])
    except FixtureError as exc:
        print(f"自校验失败：{exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError) as exc:
        print(f"生成失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_manifest(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
