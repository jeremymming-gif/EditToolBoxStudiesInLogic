r"""编辑部引用库（`bib_db`）：把「已核实的参考文献」沉淀成可查、可审计的库。

为什么要有它
------------
核实参考文献（`slcn/aibib.py` / `slcn/search.py` / 联网 AI）是**最花钱**的一步：
同一条经典文献，这一期查过、下一期作者又列一遍，还要再查。编辑部于是决定建一个
**自己的引用库**：以后核实文献**先查库**，命中已核实条目就不必再调 Search API / AI
（省钱 + 体例统一 + 可审计）。库放在**私有笔记仓** `EditToolBox-notes/bib_db/`
（JSONL 进 git，自带版本历史），由本模块与开发用 CLI `Engine/bibdb.py`、
种子工具 `Engine/tools/bibdb_seed.py` 三处共用。

来源口径（谁能算「已核实」）
----------------------------
  · **往期已出版期的成品 bib**（各期作者目录里、终稿 `.tex` 实际引用的那份 `.bib`，
    人工核对过）→ `verified=true`、`method="published-bib"`；
  · 编辑**人工核实**过的条目 → `source.kind="verified"`；
  · **AI 结果不得直接入 verified**（AI 的答案只是候选，要么人工复核，要么等它随某期
    出版成为成品 bib —— 这是本库唯一的价值前提）。
未核实条目也可以入库（`verified=false`），但查询结果会明确标出，调用方不得据此跳过核实。

一条记录（JSONL 一行；`sort_keys` 固定顺序，便于 git diff）
------------------------------------------------------------
    {
      "version": 1,                       # 记录 schema 版本（见 README 的迁移约定）
      "key": "Smith2020",                 # 库内唯一键：首次入库的 BibTeX key；
                                          #   跨期撞 key 且不是同一作品时加 `-2`/`-3` 后缀
      "entry_type": "article",
      "fields": {"author": "...", ...},   # 规范化后的字段（见 canonical_fields()）
      "verified": true,
      "verified_at": "2026-10-18T12:00:00+08:00",
      "method": "published-bib",          # published-bib / verified；未核实为 null
      "content_sha256": "…",              # entry_type+fields 的规范化摘要（幂等/去重用）
      "aliases": ["Smith20"],             # 同一记录用过的其它 BibTeX key（全库唯一时才记）
      "sources": [{"kind": "published-bib", "issue": "...", "slug": "...",
                   "file": "...", "key": "Smith2020",       # 原始 bib 里的 key
                   "entry_sha256": "…",                     # 该来源条目入库时的内容摘要
                   "imported_at": "..."}],
      "conflicts": [{"at": "...", "resolution": "kept-existing",
                     "field_diff": {"pages": {"existing": "...", "incoming": "..."}},
                     "existing_sha256": "...", "incoming_sha256": "...",
                     "source": {...}}],
      "created_at": "...", "updated_at": "..."
    }

`source` 为什么是**列表**：同一条经典文献会在不同期出现，需要保留**每一份来源**
（可追溯、冲突裁决时能看出是哪一期带来的差异）；任务书里的 `source` 单数字段是
单来源简化版，这里按「保留两份来源」的实际要求落成 `sources: []`。

幂等与冲突（upsert 的全部规则）
-------------------------------
  1. **内容先行**：库里已有同内容条目 → 只并来源（不新建记录、不碰字段）；
  1b. 同一份**来源条目**重跑（`sources[].entry_sha256` 与本次内容摘要相同）→ **跳过**，
      不重写时间戳、不重复追加来源（种子工具第二次运行 git 零 diff 靠的就是它）；
  2. 同 key 不同内容、且能判定是**同一作品**（题名归一化相似度 ≥0.90 + 第一作者姓 +
     年份相容）→ 记录 `conflicts`，**已核实条目的字段不静默覆盖**（保留原字段 +
     追加来源），由人裁决；
  3. 同 key 不同内容、判定不是同一作品（跨期 paper-local key 撞车，如两篇都用 `a1`）→
     **新条目加后缀**（`a1-2`），不丢任何一条（`aliases` 由来源原始 key 推导；
     撞车的 key 不记别名，避免按 key 假命中）；
  4. 内容完全相同的不同 key → 合并为一条，另一个 key 若全库唯一则进 `aliases`；
  5. 未核实条目被已核实内容命中 → 升级为 `verified`（并保留旧字段快照）；
  6. `force=True`（CLI `--force`）才允许覆盖已核实条目，且旧字段全量进 `conflicts`。

查询（为什么按这个优先级）
--------------------------
  · **key 精确**最快，但 BibTeX key 常常是**per-paper**的（`a1`…），所以还要查 aliases；
  · **DOI 精确**最可靠（归一化大小写、`https://doi.org/` / `doi:` 前缀、尾标点）；
  · **题名 + 第一作者姓 + 年份模糊**（`difflib` 字符相似度与词重叠取大，阈值 0.90）：
    低于阈值只标 `confidence="low"` 返回，**绝不**当成「已核实命中」；
  · **译著/CJK 安全命中**（批58）：题名相似度 ≥0.98 + 年份一致 + 出版社一致/包含 +
    责任标记（`（著）/（译）`）归一后第一作者姓名相容 → `hit="translation"`（仍属
    `safe`，只用于跳过检索/AI，**不改稿**）；任一条缺/矛盾 → 照旧候选；
  · **写法归一二判 / 内容指纹**（批61）：题名写法变体（全角标点/书名号/副标题/版次/
    尾部括号/「, in 书名」）≥0.98 且 ≥2 条见证、任何矛盾即否决 →
    `hit="title-normalized"`；与已核实记录**逐字段内容指纹一致** → `hit="content-identical"`
    （都仍属 `safe`，只跳过检索/AI，不改稿）；
  · 返回值一律带 `score` 与 `verified`，调用方自己决定要不要省掉 API。

只读/写入的边界
----------------
  · 本模块**不读稿件仓以外的任何东西**、不联网、不调模型（只 import 标准库 +
    `slcn.bibparse` / `slcn.bibnorm` / `slcn.paths` / `slcn.fsutil`）；
  · 扫描稿件仓（`scan_manuscripts()` / `import_manuscripts()`）**只读**，一个字节都不写；
  · 写库是**原子写**（`slcn.fsutil.atomic_write`，不产生 `.bak` 垃圾），按 key 排序输出。

常用入口
--------
    from slcn import bibdb
    db = bibdb.Store.load(bibdb.default_db_path())          # 默认 笔记仓/bib_db/bibliography.jsonl
    db.query_doi("10.1000/xyz")                             # → 命中列表（含 verified/score）
    db.query_title("The Title", author="Smith", year="2020")
    db.query_advanced(bibdb.SearchSpec((bibdb.SearchTerm("title", "logic"),)))  # B-259
    db.import_bib_text(text, {"kind": "published-bib", "issue": "...", "slug": "...",
                              "file": "reference.bib"}, verified=True)
    db.stats(); db.lint()
"""

from __future__ import annotations

import datetime as _dt
import difflib
import hashlib
import json
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from . import bibcheck, bibnorm, bibparse, fsutil, paths

#: 记录 schema 版本（改动字段语义/新增必填字段时 +1，README 里写迁移约定）
SCHEMA_VERSION = 1

#: 默认存储位置（私有笔记仓）：`<notes>/bib_db/bibliography.jsonl`
DB_DIRNAME = "bib_db"
DB_FILENAME = "bibliography.jsonl"

#: B-204：人工裁决「两条不同条目，不要再提示」的 sidecar（与库**同目录**、随笔记仓提交）。
#: 它不是库格式的一部分：缺文件 = 无效果；lint 只读不写，库字节 sha 不变。
HEALTH_IGNORE_FILENAME = "health-ignore.json"
#: sidecar 顶层版本（结构变更时 +1；引擎只认这一版，别的版本 fail-soft 成一条 warning）
HEALTH_IGNORE_VERSION = 1
#: sidecar 允许静音的告警 code（只覆盖「重复」两类：其它 warning 是待办，不许被忽略）
HEALTH_IGNORE_CODES = ("near_duplicates", "duplicate_content")

#: 来源种类：往期成品 bib / 人工标记已核实 / 普通导入（未核实）
KIND_PUBLISHED = "published-bib"
KIND_VERIFIED = "verified"
KIND_IMPORTED = "imported"
_SOURCE_KINDS = (KIND_PUBLISHED, KIND_VERIFIED, KIND_IMPORTED)

#: 模糊查询：题名相似度的「高置信」阈值；低于它只标 low，不当命中
FUZZY_TITLE_THRESHOLD = 0.90
#: 模糊查询：低于这个综合分的候选**不返回**（宁缺毋滥）
FUZZY_MIN_SCORE = 0.60
#: 模糊查询：题名本身至少要有这么像（否则作者+年份再准也不可疑）
_TITLE_GATE = 0.50

#: 译著/CJK 安全命中（批58，2026-10-20）：题名相似度下限。译著作者名常带
#: `（著）/（译）` 责任标记，或稿件只写姓氏而库内是「罗姆·哈瑞（著） and 邱仁宗（译）」，
#: 姓名字面比较必然失败；只有在**题名几乎相同 + 年份/出版社双重见证**时才认作同一作品。
TRANSLATION_TITLE_THRESHOLD = 0.98
#: 译著/CJK 作者名里的责任方式标记（半角括号 `(eds.)` 由 `first_author_surname` 先剥；
#: 这里补**全角括号**与常见中英文标记）。只用于姓名比较，不改库字段。
_ROLE_MARKER_RE = re.compile(
    r"[（(]\s*(?:著|译|編|编|编著|主编|校注|校订|校|修订|注|"
    r"eds?\.?|ed\.?|trans\.?|comp(?:s|iled)?\.?|hrsg\.?)\s*[）)]", re.I)

#: 批61（2026-10-20）：题名写法归一二判的门槛（与译著专项同口径：只认「几乎同一题名」）
TITLE_SAME_THRESHOLD = 0.98
#: 版次后缀（第2版 / 修订版 / (2nd) / 2nd ed. …）：只从题名**末尾**剥，匹配用、不改库值
_EDITION_TAIL_RE = re.compile(
    r"(?:\s*[,，;；]\s*)?(?:"
    r"[（(]\s*(?:第[一二三四五六七八九十0-9]+版|修订版|增订版|第[一二三四五六七八九十0-9]+次印刷|"
    r"\d+(?:st|nd|rd|th)?\s*(?:ed(?:ition)?|rev(?:ised)?)?\.?|"
    r"(?:second|third|fourth|fifth|sixth|revised|new|updated)\s*(?:ed(?:ition)?)?\.?)\s*[）)]"
    r"|第[一二三四五六七八九十0-9]+版|修订版|增订版"
    r"|[,，]\s*\d+(?:st|nd|rd|th)\s*(?:ed(?:ition)?)?\.?"
    r")\s*$", re.I)
#: 题名尾部括号说明（`(2nd)` / `（修订版）` / `(上)`…）：剥掉后做题名变体（只用于匹配）
_TAIL_PAREN_RE = re.compile(r"\s*[（(][^（）()]{0,80}[）)]\s*$")
#: 「章节题名, in 书名」的容器尾巴：比较前截掉容器部分（只用于匹配）
_IN_CONTAINER_RE = re.compile(r"[,，;；]\s*[\"“”'‘’]?\s*(?:in|In|IN|见|载)\s+")
#: 匹配用的全角/中式标点折叠（只影响比较，不改库字段）：全角→半角、CJK 标点→空白
_MATCH_PUNCT_MAP = str.maketrans({
    "：": ":", "，": " ", "。": " ", "、": " ", "；": " ", "！": " ", "？": " ",
    "（": "(", "）": ")", "“": '"', "”": '"', "‘": "'", "’": "'",
    "《": " ", "》": " ", "〈": " ", "〉": " ", "「": " ", "」": " ",
    "『": " ", "』": " ", "—": " ", "–": " ", "·": " ", "…": " ", "\u00a0": " ",
})

#: 期刊/软件元数据字段：对「这是一条什么文献」没有意义，入库时直接丢掉
#: （前一半复用 `slcn.bibnorm.DROP_FIELDS`，后半是 JabRef/Zotero 的私有字段）
_DROP_FIELDS = frozenset(bibnorm.DROP_FIELDS) | frozenset({
    "date-added", "date-modified", "sortkey", "key", "bibsource", "biburl",
    "bdsk-url-1", "bdsk-file-1", "bdsk-url-2", "bdsk-file-2",
})

#: 计算 content_sha256 时忽略的字段：同一作品在不同期的排版层差异（标点偏好、访问日期）
_IDENTITY_IGNORE = frozenset({"userf", "urldate"})

#: lint 用的「已知 BibTeX 类型」（含编辑部往期写法的几种）；其它类型只警告不阻断
KNOWN_ENTRY_TYPES = frozenset(bibcheck.REQUIRED) | frozenset({
    "collection", "dataset", "manual", "booklet", "periodical", "online",
    "unpublished", "conference", "electronic", "www", "webpage", "inbook",
})

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015]")
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?")
_AUTHOR_SEP_RE = re.compile(r"\s+and\s+|\s*;\s*|\s*&\s*", re.I)
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
_DOI_IN_URL_RE = re.compile(r"doi\.org/([^\s#?]+)", re.I)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_YEAR_RE = re.compile(r"(1[5-9]\d{2}|20\d{2})")

#: 稿件仓目录识别（与 `slcn/paths.py` 同一判据）：`20xx年第N期(…)`
_AUTHOR_DIR_RE = re.compile(r"^\d{2}[\s_-]")
_ISSUE_YEAR_RE = re.compile(r"^(\d{4})年第")
_BIBREF_RE = re.compile(
    r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}"
    r"|\\bibliography\s*\{([^}]*)\}"
)


# ── 异常 ────────────────────────────────────────────────────────────────────


class BibDBError(Exception):
    """引用库的用法 / IO / 结构错误。CLI 捕获后打中文说明并以退出码 2 结束。"""


class NotesRootMissing(BibDBError):
    """找不到私有笔记仓（默认库在 `EditToolBox-notes/bib_db/`）。"""


def _now_iso() -> str:
    """本地时区、到秒的 ISO 时间戳（人读 + 可排序；跟 `fsutil` 的时间戳口径一致）。"""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ── 默认路径 ────────────────────────────────────────────────────────────────


def default_db_path(notes_root: str | None = None) -> str:
    """默认库文件：`<笔记仓>/bib_db/bibliography.jsonl`。

    ⚠️ **笔记仓找不到时明确报错**（`NotesRootMissing`），绝不悄悄写到别处 ——
    这是任务书点名的要求：引用库一旦写错位置，就在 git 里丢了审计历史。
    笔记仓的判据沿用 `slcn/paths.py`（环境变量 `SLCN_NOTES` 优先且严格；否则同级目录，
    要求里面有 `cases/`）。
    """
    root = notes_root if notes_root is not None else paths.notes_root()
    if not root:
        raise NotesRootMissing(
            "找不到私有笔记仓，无法确定引用库位置（默认放在 EditToolBox-notes/bib_db/）。\n"
            "  请设环境变量 SLCN_NOTES=<笔记仓路径>（该目录下要有 cases/ 子目录），"
            "或用 --db <库文件路径> 显式指定。\n"
            f"  {paths.describe_notes()}"
        )
    return os.path.join(os.path.abspath(root), DB_DIRNAME, DB_FILENAME)


def init_db(path: str | None = None) -> tuple[str, bool]:
    """初始化库文件（父目录 + 空 JSONL）。返回 `(路径, 是否新建)`。

    已存在时**一个字都不动**（幂等；重复 init 不是错误）。
    """
    db_path = os.path.abspath(path or default_db_path())
    if os.path.isfile(db_path):
        return db_path, False
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    fsutil.atomic_write(db_path, "", keep_backups=0)
    return db_path, True


def db_sha256(path: str) -> str:
    """库文件的内容 sha256（空串表示文件不存在）。"""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


# ── B-204：重复告警忽略 sidecar（人工裁决「两条不同条目」）──────────────────
#
# 为什么放在库旁边而不是库里：静音一条「库健康」提示是**编辑决定**、要可审计，
# 但改记录（加 x-ack 标记 / 改 fields）会动 content_sha256 与 bib 事实。sidecar 与
# `bibliography.jsonl` 同目录、随笔记仓版本化：删条目 / git revert 即恢复告警，
# 库字节一个都不动。**只有 lint 读它**；`report-duplicates` 仍全量审计（不静音）。
#
# 口径（设计卡 §4 / 证据 `/tmp/b201/evidence/q4-dup-ack.md`）：
#   · 缺文件 / sidecar 不存在 = 空集，零新 warning；
#   · key 先按 canonical（`_by_key`）解析、再按 alias（`_by_alias` 唯一 owner）；
#     找不到 / alias 多指 / keys 重复 → 该条 ack 不生效；
#   · `near_duplicates` ack = 一条 pair（2 个 key，无序）；
#   · `duplicate_content` ack = 一组内容完全相同的记录（keys 必须列全该组，升序集合相等）；
#   · 文件坏 / version 不对 / 结构坏 / 有无法解析的 key → **不抛异常、lint errors 仍 0**，
#     只额外产生一条聚合 warning（code=`health_ignore`），坏条目不生效、好条目照常生效；
#   · 两条 key 解析到同一条记录（多半 ack 后已合并）→ ack 自然失效，但**不算文件坏**
#     （不新增 warning：用户做了合并这个正确动作，不该反被「清单有问题」提示）。


def health_ignore_path(db_path: str) -> str:
    """`<库目录>/health-ignore.json`（B-204）；库路径为空时返回空串（= 无 sidecar）。"""
    if not db_path:
        return ""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), HEALTH_IGNORE_FILENAME)


def _resolve_health_key(raw: str, by_key: dict, by_alias: dict) -> tuple[str, str]:
    """sidecar 里的一个 key → canonical key。返回 `(canonical, 问题说明)`；问题非空 = 不生效。"""
    if raw in by_key:
        return raw, ""
    owners = by_alias.get(raw) or []
    canonical = {str(record.get("key") or "") for record in owners
                 if isinstance(record, dict)}
    canonical.discard("")
    if len(canonical) == 1:
        return next(iter(canonical)), ""
    if len(canonical) > 1:
        return "", f"key `{raw}` 是 {len(canonical)} 条记录的别名（多指）"
    return "", f"key `{raw}` 不在库里（也不是唯一别名）"


def load_health_ignore(db_path: str, by_key: dict, by_alias: dict) -> dict:
    """读 sidecar（B-204）。**fail-soft**：任何结构问题只记进 `problems`，永不抛异常。

    返回 `{"path", "exists", "near_duplicates": set[frozenset[str]],
    "duplicate_content": set[tuple[str, ...]], "acks": [...], "problems": [...]}`：
      · `near_duplicates` / `duplicate_content` 是**已解析成 canonical key** 的生效集合；
      · `acks` 保留每条条目的解析结果（`effective` 标记是否生效；reason / decided_at
        仅供审计，不参与判定）；
      · `problems` 每条 = 一处问题（由 lint 聚合成一条 warning，`count` = 问题数）。
    """
    path = health_ignore_path(db_path)
    result: dict = {"path": path, "exists": False, "near_duplicates": set(),
                    "duplicate_content": set(), "acks": [], "problems": []}
    if not path or not os.path.isfile(path):
        return result
    result["exists"] = True
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    # t5（verifier t3 发现）：`JSONDecodeError`（坏 JSON）与 `UnicodeDecodeError`（非法
    # UTF-8 字节，在解码阶段就抛）都是 `ValueError` 子类 —— 两种都算「文件坏」走
    # fail-soft；`OSError` 覆盖读不到（权限/IO）。这里绝不能漏成 traceback。
    except (OSError, ValueError) as exc:
        result["problems"].append(f"读不了 / 不是合法 UTF-8 或 JSON（{exc}）")
        return result
    if not isinstance(payload, dict):
        result["problems"].append("顶层不是 JSON 对象")
        return result
    version = payload.get("version")
    if version != HEALTH_IGNORE_VERSION:
        result["problems"].append(
            f"version 不是 {HEALTH_IGNORE_VERSION}（实际 {version!r}）：本引擎不认这个文件")
        return result
    entries = payload.get("acknowledged")
    if not isinstance(entries, list):
        result["problems"].append("`acknowledged` 不是数组")
        return result
    for index, entry in enumerate(entries, start=1):
        label = f"第 {index} 条 ack"
        if not isinstance(entry, dict):
            result["problems"].append(f"{label}不是 JSON 对象")
            continue
        code = entry.get("code")
        if code not in HEALTH_IGNORE_CODES:
            result["problems"].append(
                f"{label} 的 code {code!r} 不受支持（只允许 {'/'.join(HEALTH_IGNORE_CODES)}）")
            continue
        keys = entry.get("keys")
        if (not isinstance(keys, list) or len(keys) < 2
                or any(not isinstance(key, str) or not key.strip() for key in keys)):
            result["problems"].append(f"{label} 的 keys 必须是 ≥2 个非空字符串")
            continue
        raw_keys = [key.strip() for key in keys]
        if len(set(raw_keys)) != len(raw_keys):
            result["problems"].append(f"{label} 的 keys 有重复项：{'、'.join(raw_keys)}")
            continue
        resolved: list[str] = []
        problem = ""
        for raw in raw_keys:
            canonical, problem = _resolve_health_key(raw, by_key, by_alias)
            if problem:
                break
            resolved.append(canonical)
        if problem:
            result["problems"].append(f"{label}：{problem}（该条不生效）")
            continue
        resolved_unique = sorted(set(resolved))
        if len(resolved_unique) < 2:
            # 两条 key 现在指向同一条记录（最常见：ack 之后把这两条合并了）——ack 天然
            # 失效，但**不算文件结构坏**（不新增 warning，免得用户做了正确的事反被提示）。
            result["acks"].append({
                "code": code, "keys": raw_keys, "resolved": resolved_unique,
                "effective": False,
                "reason": str(entry.get("reason") or ""),
                "decided_at": str(entry.get("decided_at") or ""),
            })
            continue
        if code == "near_duplicates" and (len(raw_keys) != 2 or len(resolved_unique) != 2):
            result["problems"].append(
                f"{label}：near_duplicates 必须恰好 2 个 key（实际 {len(raw_keys)}）")
            continue
        result["acks"].append({
            "code": code, "keys": raw_keys, "resolved": resolved_unique,
            "effective": True,
            "reason": str(entry.get("reason") or ""),
            "decided_at": str(entry.get("decided_at") or ""),
        })
        if code == "near_duplicates":
            result["near_duplicates"].add(frozenset(resolved_unique))
        else:
            result["duplicate_content"].add(tuple(resolved_unique))
    return result


def load_health_ignore_entries(db_path: str) -> dict:
    """读 sidecar 的**原文条目**（B-205 写动作专用；不解析 key、不改变 lint 口径）。

    `load_health_ignore()` 把条目解析成「生效/不生效」的集合，适合 lint 只读；写动作要
    **原样保留**既有条目的 `reason` / `decided_at`（以及将来可能加的审计字段），所以另给
    这个原文读取口。

    返回 `{"path", "exists", "version", "acknowledged": [原文 dict...], "problems": [...]}`：
      · 缺文件 = 空集、无问题（首次 ack 的常态）；
      · `problems` 非空 = 文件存在但**不能安全重写**（读不了 / 坏 JSON / 顶层不是 JSON
        对象 / version 不是本引擎认的 / `acknowledged` 不是数组 / 某条不是 JSON 对象）→
        写动作必须拒绝覆盖，提示用户先修文件。这和 `load_health_ignore` 的 fail-soft 是
        **相反**的取舍：lint 可以忽略坏条目继续报库健康，写动作宁可不写，也不能把用户
        手写的条目悄悄丢掉。
    """
    path = health_ignore_path(db_path)
    result: dict = {"path": path, "exists": False, "version": None,
                    "acknowledged": [], "problems": []}
    if not path or not os.path.isfile(path):
        return result
    result["exists"] = True
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        result["problems"].append(f"读不了 / 不是合法 UTF-8 或 JSON（{exc}）")
        return result
    if not isinstance(payload, dict):
        result["problems"].append("顶层不是 JSON 对象")
        return result
    version = payload.get("version")
    result["version"] = version
    if version != HEALTH_IGNORE_VERSION:
        result["problems"].append(
            f"version 不是 {HEALTH_IGNORE_VERSION}（实际 {version!r}）：本引擎不认这个文件")
        return result
    entries = payload.get("acknowledged")
    if not isinstance(entries, list):
        result["problems"].append("`acknowledged` 不是数组")
        return result
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            result["problems"].append(f"第 {index} 条 ack 不是 JSON 对象")
            continue
        result["acknowledged"].append(entry)
    return result


def write_health_ignore_entries(db_path: str, entries: list[dict]) -> str:
    """原子重写 sidecar（B-205 写动作）：`version: 1` + 条目原文，pretty JSON + 末尾换行。

    只写 `<库目录>/health-ignore.json`，**绝不碰 bibliography.jsonl**：同目录临时文件 +
    `fsync` + `os.replace`（失败时旧文件完好），`keep_backups=0` 不落 `.bak` 垃圾
    （sidecar 本身归笔记仓 git 审计，撤销用 `git revert` / 删条目）。返回 sidecar 路径。

    调用方负责**先校验**（`load_health_ignore_entries` 与 `load_health_ignore` 都没有
    problems）；本函数只按给定条目序列化，不猜、不修、不丢。
    """
    path = health_ignore_path(db_path)
    if not path:
        raise BibDBError("库路径为空，无法确定 health-ignore.json 的位置")
    cleaned: list[dict] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise BibDBError(f"第 {index} 条 ack 不是 JSON 对象：拒绝写 sidecar")
        cleaned.append(dict(entry))
    payload = {"version": HEALTH_IGNORE_VERSION, "acknowledged": cleaned}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fsutil.atomic_write(path, text, keep_backups=0)
    return path


# ── 字段规范化（唯一实现：复用 slcn/bibnorm.py 的编辑规则） ─────────────────


def canonical_field_name(name: str) -> str:
    """字段名小写 + 编辑部别名归一（`issue→number`、`journaltitle→journal`…）。"""
    low = str(name or "").strip().lower()
    return bibnorm.FIELD_ALIASES.get(low, low)


def _squash(value: object) -> str:
    """折叠空白：同一份 bib 换了换行/缩进不该被当成「内容变了」。"""
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def canonical_fields(fields: dict) -> dict[str, str]:
    """条目字段 → 规范化字段（丢软件元数据、别名归一、值清理、键排序）。

    **值清理复用** `slcn.bibnorm._clean_field_value()`：体例规则只允许有一份实现
    （`bibnorm` 是编辑部规范的权威实现，`checkbib.py` 也走它）。
    """
    out: dict[str, str] = {}
    for name, value in (fields or {}).items():
        canon = canonical_field_name(name)
        if not canon or canon in _DROP_FIELDS:
            continue
        raw = str(value if value is not None else "")
        cleaned = bibnorm._clean_field_value(canon, raw)
        if cleaned is None:
            continue
        value_norm = _squash(cleaned)
        if not value_norm:
            continue
        if canon == "year" and not re.fullmatch(r"\d{4}", value_norm):
            # 源 bib 有 `\hspace{-0.3em}2021` 这类脏值：bibnorm 的「只留数字」规则会得到
            # `032021`。入库字段是**给人看的核实记录**，这里退回抽第一个四位年份；
            # 抽不出就当脏值原样留着（lint 会警告）。
            match = _YEAR_RE.search(raw)
            if match:
                value_norm = match.group(1)
        if canon == "doi":
            value_norm = normalize_doi(value_norm)
            if not value_norm:
                continue
        out[canon] = value_norm
    return dict(sorted(out.items()))


def content_sha256(entry_type: str, fields: dict) -> str:
    """条目的规范化内容摘要（幂等去重与「同 key 同内容」判据的唯一实现）。

    排除 `_IDENTITY_IGNORE`（排版层差异）；对规范化后的字段按名排序，所以
    同一份 bib 重排字段/换行不会变摘要。
    """
    canon = canonical_fields(fields or {})
    payload = [
        str(entry_type or "").strip().lower(),
        [[name, value] for name, value in sorted(canon.items())
         if name not in _IDENTITY_IGNORE],
    ]
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── 查询用的文本归一（题名 / 作者 / 年份 / DOI） ────────────────────────────


def _latex_plain(value: object) -> str:
    """把 BibTeX 值里常见的 LaTeX 装饰降成纯文本 —— **只用于比较**，不改库里的字段。"""
    s = str(value if value is not None else "")
    s = re.sub(r"\\href\s*\{[^}]*\}\s*\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\url\s*\{([^}]*)\}", r"\1", s)
    s = _LATEX_CMD_RE.sub("", s)          # `\textup` / `\emph` / `\hspace` …
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"\\(.)", r"\1", s)         # `\"o` → `"o`（之后标点过滤会清掉引号）
    return s


def normalize_title(value: object) -> str:
    """题名归一：去 LaTeX/花括号、去重音（NFKD）、小写、去标点、压空白。

    只用于**相似度比较**；库里的 `fields.title` 保持规范化的原文（不清洗内容）。
    """
    s = _latex_plain(value)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _DASH_RE.sub("-", s.lower())
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_search_text(value: object) -> str:
    """检索用归一（B-259）：在 `normalize_title` 之前先折叠全角/CJK 标点。

    与 `_match_title_forms` 用同一张 `_MATCH_PUNCT_MAP`（，。：；！？（）《》 → ASCII），
    再走 `normalize_title` 的 LaTeX/NFKD/小写/去标点/压空白，保证「高级检索」与旧
    `query_title` 对同一串给出同一归一结果；CJK 不切词（直接子串/前缀匹配）。
    """
    return normalize_title(_latex_plain(value).translate(_MATCH_PUNCT_MAP))


def _tokens(text: str) -> list[str]:
    return [t for t in text.split() if t]


def _seq_ratio(a: str, b: str) -> float:
    """`difflib` 字符相似度；`real_quick_ratio` 只用于剪掉明显不相似的对（是上界）。"""
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < 0.4:
        return matcher.real_quick_ratio()
    return matcher.ratio()


def title_similarity(a: object, b: object) -> float:
    """两个题名的相似度（0–1）：字符比例与词重叠取大；一方完整包含另一方取 0.95。

    包含判据加了长度门槛（较短题名 ≥12 字且占较长题名 ≥60%）——否则题名 `Logic`
    会命中 `Logic and Language` 这种完全不同的作品（假高置信比低置信更糟）。
    """
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 12 and len(shorter) >= 0.6 * len(longer) and shorter in longer:
        return 0.95
    ratio = _seq_ratio(na, nb)
    ta, tb = _tokens(na), _tokens(nb)
    if ta and tb:
        dice = 2.0 * len(set(ta) & set(tb)) / (len(ta) + len(tb))
        ratio = max(ratio, dice)
    return round(ratio, 6)


def first_author_surname(value: object) -> str:
    """第一位作者的「姓」（比较用）：`Last, First` 取逗号前；`First Last` 取末词；
    含汉字/假名则用整个姓名（中文姓名不分倒装）。"""
    text = _latex_plain(value)
    text = re.sub(r"\([^)]*\)", " ", text)          # `(eds.)` / `（著）` 之类
    first = _AUTHOR_SEP_RE.split(text.strip())[0].strip() if text.strip() else ""
    if not first:
        return ""
    if _CJK_RE.search(first):
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", first)
    if "," in first:
        surname = first.split(",")[0]
    else:
        toks = [t for t in first.split() if t]
        while len(toks) > 1 and toks[-1].lower().rstrip(".") in {
            "jr", "sr", "ii", "iii", "iv",
        }:
            toks.pop()
        surname = toks[-1] if toks else ""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", surname.lower())


def entry_year(fields: dict) -> str:
    """条目年份：`year` 优先，`date` 兜底；从值里抽第一个四位年份（源 bib 有
    `\\hspace{-0.3em}2021` 这类脏值，不能直接当数字用）。"""
    for name in ("year", "date"):
        raw = str((fields or {}).get(name) or "")
        match = _YEAR_RE.search(raw)
        if match:
            return match.group(1)
    return ""


def _surnames_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if _CJK_RE.search(a + b):          # 中文/日文姓名：只认完全相同，不做包含判断
        return False
    if min(len(a), len(b)) >= 3:
        return a in b or b in a        # `van Fraassen` vs `Fraassen`
    return False


def strip_author_roles(value: object) -> str:
    """去掉姓名里的**责任方式标记**（`（著）`/`（译）`/`(eds.)`…），只用于姓名比较。

    典型差异（批58 审计的 3 条译著候选）：稿件 `哈瑞`、库内成品 bib
    `罗姆·哈瑞（著） and 邱仁宗（译）`。库里的 `fields.author` 保持原样，不做清洗。
    """
    return _ROLE_MARKER_RE.sub(" ", _latex_plain(value))


def _cjk_author_surname(value: object) -> str:
    """CJK 第一作者姓名（先剥责任标记）：`罗姆·哈瑞（著）` → `罗姆哈瑞`。"""
    return first_author_surname(strip_author_roles(value))


def _translation_surname_match(entry_author: object, record_author: object) -> bool:
    """译著/CJK 第一作者姓名相容（**保守**，批58）：

      · 两侧都必须含 CJK（英文姓名差异不走这条，避免放宽普通作者匹配）；
      · 剥掉责任标记（著/译/编）后**相等**；或
      · 短名（≥2 字）是长名的一截，**且**原始 author 里确实出现过责任标记
        （`哈瑞` ⊂ `罗姆·哈瑞（著）`；防止把两个普通不同姓名当成同一人）。
    """
    a = _cjk_author_surname(entry_author)
    b = _cjk_author_surname(record_author)
    if not a or not b:
        return False
    if not (_CJK_RE.search(a) and _CJK_RE.search(b)):
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 2 and short in long:
        raw = f"{_latex_plain(entry_author)}\n{_latex_plain(record_author)}"
        if _ROLE_MARKER_RE.search(raw):
            return True
    return False


def _publisher_key(value: object) -> str:
    """出版社归一（比较用）：去 LaTeX/重音、小写、去标点空白。"""
    s = _latex_plain(value)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s.lower())


def _publishers_match(entry_fields: dict, record_fields: dict) -> bool:
    """出版社一致/包含（译著安全命中的**第二重见证**，批58）。

    不接受空值：一方没写出版社就无法作证，退回候选（宁缺毋滥）。
    包含只在较短一方够长时才认（ASCII ≥4、CJK ≥2），防 `Press` 这类泛词误配。
    """
    a = _publisher_key((entry_fields or {}).get("publisher") or "")
    b = _publisher_key((record_fields or {}).get("publisher") or "")
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    need = 2 if _CJK_RE.search(short) else 4
    return len(short) >= need and short in long


def _translation_safe_hit(entry_fields: dict, hit: dict) -> bool:
    """题名命中能否升级为**译著/CJK 安全命中**（批58；`match_reference` 专用）。

    五重见证全满足才返回 True（任何一条缺/矛盾 → False，仍走检索）：
      ① 库记录 `verified=true`；
      ② 题名相似度 ≥ `TRANSLATION_TITLE_THRESHOLD`（0.98）；
      ③ 年份**双方都有且相等**；
      ④ 出版社一致/包含；⑤ 第一作者按 `_translation_surname_match` 相容。
    """
    record = (hit or {}).get("record") or {}
    record_fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    if record.get("verified") is not True:
        return False
    if float((hit or {}).get("title_similarity") or 0.0) < TRANSLATION_TITLE_THRESHOLD:
        return False
    entry_year_value = entry_year(entry_fields or {})
    record_year_value = entry_year(record_fields)
    if not entry_year_value or not record_year_value or entry_year_value != record_year_value:
        return False
    if not _publishers_match(entry_fields or {}, record_fields):
        return False
    entry_author = (entry_fields or {}).get("author") or (entry_fields or {}).get("editor")
    record_author = record_fields.get("author") or record_fields.get("editor")
    return _translation_surname_match(entry_author, record_author)


def _match_title_forms(value: object) -> set[str]:
    """题名的**写法变体**集合（批61，只用于匹配）：全角/中式标点折叠、书名号/引号、
    版次后缀、尾部括号说明、副标题分隔符、章节题名的「, in 书名」尾巴、去空白。

    库里 `fields.title` 一个字节都不改；这里只生成比较用的规范形。
    """
    raw = _latex_plain(value).translate(_MATCH_PUNCT_MAP)
    forms: set[str] = set()

    def add(text: str) -> None:
        norm = normalize_title(text)
        if norm:
            forms.add(norm)

    add(raw)
    folded = raw
    for _ in range(3):                       # 反复剥「(2nd)」「, 修订版」这类尾巴
        stripped = _EDITION_TAIL_RE.sub("", _TAIL_PAREN_RE.sub("", folded))
        if stripped == folded:
            break
        folded = stripped
    add(folded)
    add(_IN_CONTAINER_RE.split(folded, 1)[0])
    for sep in (":", "："):
        if sep in folded:
            add(folded.split(sep, 1)[0])
            break
    for form in list(forms):                 # 再补「去空白」形（CJK 排版常多空格）
        forms.add(form.replace(" ", ""))
    return forms


def _match_first_author_name(value: object) -> str:
    """第一作者「姓」的匹配归一（批61），比 `first_author_surname` 多处理：

      · 中文作者分隔符（`，`/`、`/`；`）与尾部「等/等著/等译」；
      · 英文「姓在前、名缩写在后」写法（`Ujjwal K. C.` → `ujjwal`）；
    只用于匹配，不改库字段。返回空串 = 无法作证。
    """
    text = strip_author_roles(value)
    first = _AUTHOR_SEP_RE.split(text.strip())[0] if text.strip() else ""
    if not first:
        return ""
    if _CJK_RE.search(first):
        for sep in ("，", "、", ",", "；", ";"):
            first = first.split(sep)[0]
        first = re.sub(r"(?:等|等著|等编|等译|et\s+al\.?|etc\.?)$",
                       "", first.strip(), flags=re.I)
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", first)
    toks = [t for t in first.split() if t]
    if len(toks) >= 2:
        tails = toks[1:]
        if (all(len(t.strip(".").replace(".", "")) <= 2 for t in tails)
                and len(toks[0]) >= 2 and not toks[0][0].islower()):
            return first_author_surname(toks[0])
    return first_author_surname(first)


def _match_authors_compatible(entry_author: object, record_author: object) -> bool | None:
    """作者姓名相容（匹配用，批61）：None = 一方为空、无法作证。"""
    a = _match_first_author_name(entry_author)
    b = _match_first_author_name(record_author)
    if not a or not b:
        return None
    if a == b:
        return True
    if _CJK_RE.search(a + b):
        return _translation_surname_match(entry_author, record_author)
    return _surnames_match(a, b)


def _match_title_witnesses(entry_fields: dict, record: dict) -> list[str] | None:
    """题名写法归一二判的见证检查（批61，保守）。返回见证名列表，None = 不通过。

    通过条件（缺一不可）：① 题名变体有交集（只回收「几乎同一题名」）；
    ② 至少**两条**见证（题名 + 年份/作者/出版社之一）；③ **任何一方有值却矛盾 → 否决**
    （年份不同 / 作者不符 / 出版社不符——宁缺毋滥，矛盾时保持候选仍走检索/AI）。
    """
    record_fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    entry_title = str((entry_fields or {}).get("title") or "")
    record_title = str(record_fields.get("title") or "")
    if not entry_title.strip() or not record_title.strip():
        return None
    entry_forms = _match_title_forms(entry_title)
    record_forms = _match_title_forms(record_title)
    if not entry_forms or not record_forms:
        return None
    matched = max((title_similarity(a, b)
                   for a in entry_forms for b in record_forms), default=0.0)
    if matched < TITLE_SAME_THRESHOLD:
        return None
    witnesses = ["title"]
    year_a = entry_year(entry_fields or {})
    year_b = entry_year(record_fields)
    if year_a and year_b:
        if year_a != year_b:
            return None
        witnesses.append("year")
    author_a = ((entry_fields or {}).get("author")
                or (entry_fields or {}).get("editor") or "")
    author_b = record_fields.get("author") or record_fields.get("editor") or ""
    compatible = _match_authors_compatible(author_a, author_b)
    if compatible is False:
        return None
    if compatible is True:
        witnesses.append("author")
    publisher_a = str((entry_fields or {}).get("publisher") or "").strip()
    publisher_b = str(record_fields.get("publisher") or "").strip()
    if publisher_a and publisher_b:
        if not _publishers_match(entry_fields or {}, record_fields):
            return None
        witnesses.append("publisher")
    if len(witnesses) < 2:
        return None
    return witnesses


def normalize_doi(value: object) -> str:
    """DOI 归一（精确查询/入库都用它）：小写、去 `https://doi.org/` 与 `doi:` 前缀、
    去尾标点。DOI 本身大小写不敏感。"""
    s = _squash(value)
    s = _DOI_PREFIX_RE.sub("", s)
    s = s.strip().strip(".,;)")
    return s.lower()


def _doi_from_url(value: object) -> str:
    """从 `url` 字段里抽 DOI（作者常把 DOI 写成 `https://doi.org/…`）。"""
    match = _DOI_IN_URL_RE.search(str(value or ""))
    return normalize_doi(match.group(1)) if match else ""


def works_match(fields_a: dict, fields_b: dict) -> bool:
    """两条记录是否**同一作品**（用于「同 key 不同字段」的冲突分类）。

    判据（全满足才算；宁可判成「不同作品」多留一条，也不误合并两份学术事实）：
      ① 题名归一后相似度 ≥ 0.90；
      ② 第一作者姓相同（双方都有时才检查；用包含关系容忍 `van Fraassen`/`Fraassen`）；
      ③ 年份相容（双方都有时必须相同 —— 1944 与 1964 很可能是不同版本/不同文献）。
    """
    title_a = (fields_a or {}).get("title")
    title_b = (fields_b or {}).get("title")
    if not normalize_title(title_a) or not normalize_title(title_b):
        return False
    if title_similarity(title_a, title_b) < FUZZY_TITLE_THRESHOLD:
        return False
    surname_a = first_author_surname((fields_a or {}).get("author")
                                     or (fields_a or {}).get("editor"))
    surname_b = first_author_surname((fields_b or {}).get("author")
                                     or (fields_b or {}).get("editor"))
    if surname_a and surname_b and not _surnames_match(surname_a, surname_b):
        return False
    year_a = entry_year(fields_a or {})
    year_b = entry_year(fields_b or {})
    if year_a and year_b and year_a != year_b:
        return False
    return True


def _score_record_title(wanted_title: str, surname: str, wanted_year: str,
                        record: dict, *,
                        threshold: float = FUZZY_TITLE_THRESHOLD,
                        min_score: float = FUZZY_MIN_SCORE) -> dict | None:
    """给「稿件题名 vs 库记录」打一个命中分（`query_title` 与 `match_reference` 共用）。

    返回 None = 题名太不像/综合分不到 `min_score`；否则返回带 `score` / `confidence` /
    `verified` / `author_match` / `year_match` 的命中 dict（**判定只有这一份**）。
    `confidence="high"` 的充要条件：题名相似度 ≥ `threshold`，且作者/年份（若查询方提供）
    没有冲突——「高置信」是 `match_reference` 敢跳过检索/AI 的前提。
    """
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    record_title = fields.get("title")
    if not record_title:
        return None
    similarity = title_similarity(wanted_title, record_title)
    if similarity < _TITLE_GATE:
        return None
    parts: dict[str, float] = {"title": round(similarity, 3)}
    weight_sum = 0.6
    score_sum = 0.6 * similarity
    author_ok: bool | None = None
    year_ok: bool | None = None
    if surname:
        record_surname = first_author_surname(fields.get("author") or fields.get("editor"))
        author_ok = bool(record_surname) and _surnames_match(surname, record_surname)
        parts["author"] = 1.0 if author_ok else 0.0
        weight_sum += 0.25
        score_sum += 0.25 * parts["author"]
    if wanted_year:
        record_year = entry_year(fields)
        year_ok = bool(record_year) and record_year == wanted_year
        parts["year"] = 1.0 if year_ok else 0.0
        weight_sum += 0.15
        score_sum += 0.15 * parts["year"]
    score = score_sum / weight_sum if weight_sum else 0.0
    if score < min_score:
        return None
    high = (similarity >= threshold
            and author_ok is not False and year_ok is not False)
    return {
        "key": record["key"],
        "score": round(score, 4),
        "confidence": "high" if high else "low",
        "verified": record.get("verified") is True,
        "title_similarity": round(similarity, 4),
        "author_match": author_ok,
        "year_match": year_ok,
        "score_parts": parts,
        "record": record,
    }


# ── 查库命中判定（convert.py ⑪ 核实链路用；第 129 条）─────────────────────────
#
# 口径（**保守**：把不同文献误判成「已核实」比漏查更糟）：
#   · 安全命中 = DOI 精确命中**已核实**记录；或题名高置信（≥0.90）+ 第一作者姓/年份
#     **相容** + verified=true；或译著/CJK 专项（批58：题名 ≥0.98 + 年份/出版社一致 +
#     责任标记归一后作者姓名相容，`hit="translation"`）；或题名写法归一二判 / 内容指纹
#     （批61：≥0.98 变体 + ≥2 见证无矛盾 → `hit="title-normalized"`；逐字段内容指纹一致
#     → `hit="content-identical"`）。只有安全命中的键才跳过检索与 AI；
#   · key 命中只算**候选**：BibTeX key 多为 per-paper（`a1`…），同 key 不保证同文献，
#     仍走检索；候选只用于日志/任务书提示；
#   · 命中但库内未核实 / 题名低置信 → 候选（不跳过任何步骤）。
# 判定只有本模块这一份实现；convert.py 只负责编排。

HIT_SAFE = "safe"            # 安全命中：可跳过检索+AI
HIT_CANDIDATE = "candidate"  # 只是候选：仍走检索+AI
HIT_MISS = "miss"            # 库内没有线索

#: 冲突裁决状态（第 130 条，2026-10-18；task-11 起持久化到 conflicts[]）：
#:   · kept-existing     —— 人工裁决「保留库内已核实旧值」（导入时默认值也是这个，
#:                          但**只有带 resolved_at 的才算已裁决**）
#:   · deferred          —— 人工标记「待人工查证」（已裁决但未定论；先从待裁决清单移走）
#:   · forced-replaced   —— 人工 `import-bib --force` 采用新稿值（旧字段留档）
#:   · replaced-unverified —— 内部状态：未核实条目被新导入替换（不是人工裁决）
RESOLUTION_KEPT = "kept-existing"
RESOLUTION_DEFERRED = "deferred"
RESOLUTION_FORCED = "forced-replaced"
#: 第 135 条（task-13，2026-10-18）：**逐字段**采用新稿值（只改该冲突 field_diff 的字段）
RESOLUTION_ADOPTED = "adopted"
#: 同记录其它待裁决冲突的字段被 adopt 覆盖、旧基线已过期 → 标「被取代」（不算人工裁决对象）
RESOLUTION_SUPERSEDED = "superseded"
ACTION_KEEP = "keep"
ACTION_DEFER = "defer"
ACTION_ADOPT = "adopt"
#: B-127（批73）：采用**人工裁决 bib**（用户直接编辑/输入的整条条目）→ 写 entry_type+
#: fields、重算 content_sha256；冲突 resolution 仍记 `adopted`（采用新值），但另记
#: `resolved_by="custom-bib"` 区分「值来自人工裁决 bib」而非新稿留档值。
ACTION_REPLACE = "replace"
#: 人工可用的裁决动作：keep / defer / adopt / replace（整条替换仍走 import-bib --force）
RESOLVE_ACTIONS = {ACTION_KEEP: RESOLUTION_KEPT,
                   ACTION_DEFER: RESOLUTION_DEFERRED,
                   ACTION_ADOPT: RESOLUTION_ADOPTED,
                   ACTION_REPLACE: RESOLUTION_ADOPTED}
#: 冲突里的裁决来源标记（`conflicts[].resolved_by`）：replace 写入的冲突带这个值，
#: 便于报告/审计区分「采用新稿留档值」（adopt）与「采用人工裁决 bib」（replace）。
RESOLVED_BY_CUSTOM_BIB = "custom-bib"


def _fields_equivalent(name: str, first: str, second: str) -> bool:
    """两个字段值是否**语义等价**（大小写/花括号/页码破折号这类排版差异不算差异）。

    为什么需要：任务书里的「库版本字段差异」是给编辑裁决用的提示；把
    `{A}n algebraic` 与 `an algebraic`、`45-67` 与 `45--67` 也列出来只会淹没真正的差异。
    """
    if first == second:
        return True
    if not first or not second:
        return False
    if name == "title":
        return normalize_title(first) == normalize_title(second)
    if name in ("author", "editor"):
        return _squash(first).lower() == _squash(second).lower()
    if name in ("year", "date"):
        return bool(entry_year({"year": first})) and \
            entry_year({"year": first}) == entry_year({"year": second})
    if name == "doi":
        return normalize_doi(first) == normalize_doi(second)
    if name in ("pages", "volume", "number", "edition"):
        strip = lambda v: re.sub(r"[\s\-\u2010-\u2015]+", "-", _squash(v).lower())  # noqa: E731
        return strip(first) == strip(second)
    return False


def entry_field_diff(entry_fields: dict, record_fields: dict,
                     *, entry_type: str = "") -> dict[str, dict]:
    """稿件条目 vs 库记录的**规范化字段差异**（只提示，绝不据此改稿）。

    返回 `{字段: {"library": 库值, "manuscript": 稿件值}}`；`entry_type` 不同时以
    `$entry_type` 单列。`userf`/`urldate` 这类排版层差异不算（与内容摘要同一口径），
    大小写/花括号/页码破折号等**语义等价**的写法差异也不算（`_fields_equivalent`）。
    """
    library = canonical_fields(record_fields or {})
    manuscript = canonical_fields(entry_fields or {})
    diff: dict[str, dict] = {}
    for name in sorted(set(library) | set(manuscript)):
        if name in _IDENTITY_IGNORE:
            continue
        before = library.get(name, "")
        after = manuscript.get(name, "")
        if not _fields_equivalent(name, before, after):
            diff[name] = {"library": before, "manuscript": after}
    return diff


# ── 记录结构 ────────────────────────────────────────────────────────────────


def validate_record(record: object) -> list[str]:
    """检查一条记录的**结构**（不重算 hash；`lint()` 才做内容级核对）。

    返回问题列表（空 = 合格）。这些判据也是「库被手工改坏」时的第一道看门狗。
    """
    if not isinstance(record, dict):
        return ["不是 JSON 对象"]
    problems: list[str] = []
    key = record.get("key")
    if not isinstance(key, str) or not key.strip():
        problems.append("缺少非空字符串 key")
    version = record.get("version")
    if not isinstance(version, int) or version < 1:
        problems.append("缺少合法的 version")
    if not isinstance(record.get("entry_type"), str):
        problems.append("entry_type 不是字符串")
    if not isinstance(record.get("fields"), dict):
        problems.append("fields 不是对象")
    if not isinstance(record.get("verified"), bool):
        problems.append("verified 不是布尔值")
    sha = record.get("content_sha256")
    if not isinstance(sha, str) or not _HEX64_RE.match(sha):
        problems.append("content_sha256 不是 64 位十六进制")
    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        problems.append("缺来源（sources 为空）")
    elif not all(isinstance(s, dict) and str(s.get("kind") or "").strip()
                 for s in sources):
        problems.append("sources 里有条目缺 kind")
    if record.get("verified") is True:
        if not str(record.get("verified_at") or "").strip():
            problems.append("verified=true 但没有 verified_at（核实记录缺失）")
        if not str(record.get("method") or "").strip():
            problems.append("verified=true 但没有 method（核实方式缺失）")
    if not isinstance(record.get("aliases", []), list):
        problems.append("aliases 不是数组")
    conflicts = record.get("conflicts", [])
    if not isinstance(conflicts, list):
        problems.append("conflicts 不是数组")
    else:
        for index, conflict in enumerate(conflicts):
            if not isinstance(conflict, dict):
                problems.append(f"conflicts[{index}] 不是对象")
                continue
            if "resolution" in conflict and not isinstance(conflict["resolution"], str):
                problems.append(f"conflicts[{index}].resolution 不是字符串")
            if "resolved_at" in conflict and not isinstance(conflict["resolved_at"], str):
                problems.append(f"conflicts[{index}].resolved_at 不是字符串")
            if "note" in conflict and not isinstance(conflict["note"], str):
                problems.append(f"conflicts[{index}].note 不是字符串")
            if "field_diff" in conflict and not isinstance(conflict["field_diff"], dict):
                problems.append(f"conflicts[{index}].field_diff 不是对象")
    return problems


def _record_line(record: dict) -> str:
    """记录 → 一行 JSONL（键排序 + 紧凑分隔，保证同样内容永远是同样的字节）。"""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clean_source(source: object, now: str) -> dict:
    """来源字典规范化（缺省字段补 null；`imported_at` 只在首次入库时确定）。"""
    if not isinstance(source, dict):
        raise BibDBError("source 必须是字典")
    kind = str(source.get("kind") or "").strip()
    if kind not in _SOURCE_KINDS:
        raise BibDBError(f"未知 source.kind：{kind!r}（允许 {_SOURCE_KINDS}）")
    return {
        "kind": kind,
        "issue": source.get("issue") or None,
        "slug": source.get("slug") or None,
        "file": source.get("file") or None,
        #: 这条来源在**原始 bib 里的 key**（可能因跨期撞车被加后缀；alias 由它推导）
        "key": str(source.get("key") or "").strip() or None,
        #: 该来源条目**入库时的内容摘要**：重跑时据此判断「这条来源已经处理过、内容没变」
        #: → 直接跳过（这是种子导入逐字节幂等的关键，见 import_bib_text）
        "entry_sha256": str(source.get("entry_sha256") or "") or None,
        "imported_at": str(source.get("imported_at") or now),
    }


def _source_identity(source: dict) -> tuple[str, str, str, str, str]:
    """来源同一性（忽略 imported_at）——重复导入同一份 bib 不重复追加来源。

    含原始 key：同一份 bib 里两条**不同 key** 的条目若内容相同，来源仍要各记各的
    （否则 alias 推导会丢一半）。
    """
    return (
        str(source.get("kind") or ""),
        str(source.get("issue") or ""),
        str(source.get("slug") or ""),
        str(source.get("file") or ""),
        str(source.get("key") or ""),
    )


def _new_record(key: str, entry_type: str, fields: dict[str, str], sha: str,
                source: dict, *, verified: bool, now: str) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "key": key,
        "entry_type": str(entry_type or "").strip().lower(),
        "fields": fields,
        "verified": bool(verified),
        "verified_at": now if verified else None,
        "method": (str(source.get("kind") or "") or None) if verified else None,
        "content_sha256": sha,
        "aliases": [],
        "sources": [source],
        "conflicts": [],
        "created_at": now,
        "updated_at": now,
    }


def _field_diff(record: dict, new_fields: dict, new_etype: str) -> dict[str, dict]:
    """逐字段 diff（冲突记录用）：只列值不同的字段；类型不同单列 `$entry_type`。"""
    old_fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    diff: dict[str, dict] = {}
    for name in sorted(set(old_fields) | set(new_fields)):
        before = old_fields.get(name, "")
        after = new_fields.get(name, "")
        if before != after:
            diff[name] = {"existing": before, "incoming": after}
    if str(record.get("entry_type") or "").lower() != str(new_etype or "").lower():
        diff["$entry_type"] = {"existing": record.get("entry_type"),
                               "incoming": str(new_etype or "").lower()}
    return diff


def conflict_is_pending(conflict: object) -> bool:
    """这个冲突**是否待人工裁决**：以 `resolved_at` 为准（不是看 resolution）。

    为什么要看 `resolved_at`：导入时「已核实条目保留原字段」也会写
    `resolution="kept-existing"`（这是导入策略，不是人的裁决），那时还没有 `resolved_at`。
    只有 `bibdb.py resolve` / `import-bib --force` 写入的裁决才带 `resolved_at`——
    两者分开，才能既向后兼容旧库、又把「待裁决」算得准。
    """
    if not isinstance(conflict, dict):
        return False
    return not str(conflict.get("resolved_at") or "").strip()


def conflict_fields(conflict: object) -> list[str]:
    """这条冲突涉及哪些字段（`field_diff` 的键名，`$entry_type` 表示条目类型）。"""
    if not isinstance(conflict, dict):
        return []
    diff = conflict.get("field_diff")
    if not isinstance(diff, dict):
        return []
    return sorted(str(name) for name in diff)


def match_conflict_by_sha(record_key: str, conflicts: list,
                          sha_prefix: str) -> list[tuple[int, dict]]:
    """按**冲突指纹前缀**在一条记录里精确定位冲突（含已裁决——幂等判断也要用）。

    与 `resolve_conflicts` 的指纹选择**同一实现**（adopt / replace 共用）：
    至少 8 位十六进制前缀，必须在本记录冲突里唯一。
    """
    prefix = str(sha_prefix or "").strip().lower()
    if len(prefix) < 8 or len(prefix) > 64 \
            or any(ch not in "0123456789abcdef" for ch in prefix):
        raise BibDBError(
            "--incoming-sha256 需要至少 8 位十六进制前缀"
            "（完整值见 query --key … --json 的 conflicts[].incoming_sha256）")
    matched = [
        (index, conflict) for index, conflict in enumerate(conflicts)
        if isinstance(conflict, dict)
        and str(conflict.get("incoming_sha256") or "").lower().startswith(prefix)
    ]
    if not matched:
        raise BibDBError(
            f"记录 `{record_key}` 里没有以 `{prefix}` 开头的冲突指纹"
            "（用 query --key … --json 看 conflicts[].incoming_sha256）")
    if len(matched) > 1:
        raise BibDBError(
            f"冲突指纹前缀 `{prefix}` 匹配到 {len(matched)} 条冲突，请多给几位")
    return matched


def overlapping_pending_conflicts(record: dict, target: dict,
                                  adopted_fields: set[str]) -> list[dict]:
    """同记录里与本次裁决**字段重叠**的其它**待裁决**冲突（adopt / replace 共用判据）。

    为什么要有它：本次裁决会更新这些字段的旧值基线，其它待裁决冲突的 `existing`
    就过期了——默认拒绝（先 keep/defer 逐条处理），或 `--allow-overlap` 标 superseded。
    """
    overlapping: list[dict] = []
    for index, other in enumerate(record.get("conflicts") or []):
        if not isinstance(other, dict) or other is target:
            continue
        if not conflict_is_pending(other):
            continue
        shared = sorted(set(conflict_fields(other)) & adopted_fields)
        if shared:
            overlapping.append({
                "index": index,
                "fields": conflict_fields(other),
                "shared": shared,
                "incoming_sha256": str(other.get("incoming_sha256") or ""),
            })
    return overlapping


class UpsertResult:
    """一次 upsert 的结果（给导入统计与测试用）。"""

    __slots__ = ("status", "key", "reason", "sources_added", "conflict", "disambiguated")

    def __init__(self, status: str, key: str = "", *, reason: str = "",
                 sources_added: int = 0, conflict: bool = False,
                 disambiguated: bool = False):
        self.status = status
        self.key = key
        self.reason = reason
        self.sources_added = sources_added
        self.conflict = conflict
        self.disambiguated = disambiguated


class ImportReport:
    """一次导入（一份 bib / 一批期目录）的统计。"""

    def __init__(self) -> None:
        self.entries = 0            # 解析出的条目数（含被跳过的）
        self.new = 0                # 新入库
        self.merged = 0             # 命中已有条目（含幂等跳过、别名/来源追加）
        self.updated = 0            # 已有条目被更新（升级为已核实 / 强制覆盖）
        self.conflicts = 0          # 记录了字段冲突的条目数
        self.disambiguated = 0      # 跨期 key 撞车 → 加后缀的新条目
        self.sources_added = 0      # 追加到已有条目上的来源数
        self.findings = 0           # bibparse 报的语法发现数（不阻断导入，如实计数）
        self.skipped: Counter[str] = Counter()
        self.warnings: list[str] = []

    def add_result(self, result: UpsertResult) -> None:
        if result.status == "skipped":
            self.skipped[result.reason or "其它"] += 1
            return
        if result.status == "new":
            self.new += 1
            if result.disambiguated:
                self.disambiguated += 1
        elif result.status == "merged":
            self.merged += 1
        elif result.status == "updated":
            self.updated += 1
        elif result.status == "conflict":
            self.conflicts += 1
        self.sources_added += result.sources_added

    def merge(self, other: "ImportReport") -> None:
        for name in ("entries", "new", "merged", "updated", "conflicts",
                     "disambiguated", "sources_added", "findings"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.skipped.update(other.skipped)
        self.warnings.extend(other.warnings)

    def summary(self) -> dict:
        return {
            "entries": self.entries,
            "new": self.new,
            "merged": self.merged,
            "updated": self.updated,
            "conflicts": self.conflicts,
            "disambiguated": self.disambiguated,
            "sources_added": self.sources_added,
            "findings": self.findings,
            "skipped": dict(sorted(self.skipped.items())),
            "warnings": list(self.warnings),
        }

# ── 高级检索（B-259：P1 Engine API） ────────────────────────────────────────
#
# 只读检索层：把「字段 × 模式 × 年份表达式」的 AND 条件打到内存库上，返回带
# why-hit（`matched`）的分页结果。刻意不建索引、不改 `_by_key`；7k 条库单次
# 查询（不含进程启动）在 300ms 内（spec §2.9）。旧 `query_key/query_doi/query_title`
# 一行不改 —— 本层只复用它们的归一化/姓氏/年份/DOI 工具。

#: 高级检索支持的字段（spec §2.1）；`any` = 题名/作者/书名/期刊/出版社任一命中
SEARCH_FIELDS: tuple[str, ...] = (
    "author", "editor", "title", "booktitle", "journal",
    "publisher", "year", "doi", "key", "alias", "any",
)
#: 文本字段的四种匹配模式（默认 `contains`）
SEARCH_MODES: tuple[str, ...] = ("contains", "prefix", "exact", "fuzzy")
#: 排序方式（spec §2.6）
SEARCH_SORTS: tuple[str, ...] = ("relevance", "year", "author")
#: 模糊检索默认下限与 `--strict` 的下限（旧 `query_title` 的 0.90 不放宽）
SEARCH_MIN_SCORE_DEFAULT = 0.75
SEARCH_MIN_SCORE_STRICT = 0.90
#: `any` 依次尝试的字段（任一命中即算命中；这是 spec 允许的唯一内部 OR）
_ANY_SEARCH_FIELDS: tuple[str, ...] = ("title", "author", "booktitle", "journal",
                                       "publisher")
#: 按条目字段取值的文本字段（年份/标识符/别名另走专用路径）
_SEARCH_TEXT_FIELDS = frozenset({"author", "editor", "title", "booktitle", "journal",
                                 "publisher"})
#: 这些字段不允许 fuzzy（标识符/年份没有「相似」语义，spec §2.5）
_SEARCH_NO_FUZZY = frozenset({"year", "doi", "key", "alias"})
#: 模式严格度（越小越严格，spec §2.6 的第二排序键）
_SEARCH_MODE_RANK = {"exact": 0, "prefix": 1, "contains": 2, "fuzzy": 3}
#: 模糊 needle 归一后至少 2 字符（防 `li` 命中 `liu`，spec §2.5）
_SEARCH_MIN_NEEDLE = 2
#: 年份表达式：`2003` / `=2003` / `1990-2000` / `>=2010` / `<=1999` / `>2010` / `<2010`
_SEARCH_YEAR_EXPR_RE = re.compile(
    r"^(?:(?P<op>>=|<=|>|<|=)?\s*(?P<year>\d{4})"
    r"|(?P<lo>\d{4})\s*[-–—]\s*(?P<hi>\d{4}))$")


def _search_similarity(needle: str, candidate: str) -> float:
    """两个**已归一**串的相似度（与 `title_similarity` 同口径，供 fuzzy 打分）。"""
    if not needle or not candidate:
        return 0.0
    if needle == candidate:
        return 1.0
    short, long = ((needle, candidate) if len(needle) <= len(candidate)
                   else (candidate, needle))
    if len(short) >= 12 and len(short) >= 0.6 * len(long) and short in long:
        return 0.95
    a, b = _tokens(needle), _tokens(candidate)
    dice = 2.0 * len(set(a) & set(b)) / (len(a) + len(b)) if a and b else 0.0
    return round(max(_seq_ratio(needle, candidate), dice), 6)


def _parse_year_expression(value: object) -> tuple[str, int, int]:
    """解析年份表达式 → `(op, low, high)`；非法抛 `BibDBError`（CLI rc2）。"""
    raw = _squash(value)
    match = _SEARCH_YEAR_EXPR_RE.match(raw)
    if match is None:
        raise BibDBError(
            f"年份表达式非法：{raw!r}（支持 2003 / =2003 / 1990-2000 / "
            ">=2010 / <=1999 / >2010 / <2010）")
    if match.group("year"):
        year = int(match.group("year"))
        return (match.group("op") or "=", year, year)
    low, high = int(match.group("lo")), int(match.group("hi"))
    if low > high:
        raise BibDBError(f"年份区间非法：{raw!r}（起点 {low} > 终点 {high}）")
    return ("range", low, high)


def _search_year_matches(expression: tuple[str, int, int], year: str) -> bool:
    """记录年份（`entry_year`：year 优先、date 兜底）是否满足年份表达式。"""
    if not (year and year.isdigit()):
        return False                       # 缺年份不命中（spec §2.3）
    value = int(year)
    op, low, high = expression
    if op == "=":
        return value == low
    if op == "range":
        return low <= value <= high
    if op == ">=":
        return value >= low
    if op == "<=":
        return value <= low
    if op == ">":
        return value > low
    return value < low


@dataclass(frozen=True)
class SearchTerm:
    """一个检索条件：在 `field` 上按 `mode` 匹配 `value`（构造即校验）。

    `field` ∈ `SEARCH_FIELDS`；`mode` ∈ `SEARCH_MODES`（默认 contains）。`year` 的
    `value` 必须是年份表达式；`doi/key/alias/year` 禁止 fuzzy（`BibDBError`）。
    """

    field: str
    value: str
    mode: str = "contains"

    def __post_init__(self) -> None:
        field = str(self.field or "").strip().lower()
        mode = str(self.mode or "").strip().lower()
        value = _squash(self.value)
        if field not in SEARCH_FIELDS:
            raise BibDBError(
                f"高级检索字段非法：{self.field!r}（支持 {'/'.join(SEARCH_FIELDS)}）")
        if mode not in SEARCH_MODES:
            raise BibDBError(
                f"高级检索模式非法：{self.mode!r}（支持 {'/'.join(SEARCH_MODES)}）")
        if not value:
            raise BibDBError(f"高级检索条件为空：--{field}")
        if mode == "fuzzy" and field in _SEARCH_NO_FUZZY:
            raise BibDBError(f"`{field}` 不支持 fuzzy（标识符/年份请用 exact/prefix/contains）")
        if field == "year":
            _parse_year_expression(value)
        if mode == "fuzzy" and len(normalize_search_text(value)) < _SEARCH_MIN_NEEDLE:
            raise BibDBError(
                f"模糊检索词过短：{value!r}（归一后至少 {_SEARCH_MIN_NEEDLE} 字符）")
        object.__setattr__(self, "field", field)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class SearchSpec:
    """一次高级检索：多个 term 之间 **AND**；`sort`/`limit`/`min_score` 同 spec §2。

    `min_score` 只作用于 fuzzy 条件（相似度下限；默认 0.75，`--strict` 用 0.90）；
    非 fuzzy 条件命中即 1.0，不受它影响。构造即校验，非法抛 `BibDBError`。
    """

    terms: tuple[SearchTerm, ...]
    sort: str = "relevance"
    limit: int = 20
    min_score: float = SEARCH_MIN_SCORE_DEFAULT

    def __post_init__(self) -> None:
        terms = tuple(self.terms)
        if not terms:
            raise BibDBError("高级检索至少要有一个条件（--author/--title/… 或 --any）")
        for term in terms:
            if not isinstance(term, SearchTerm):
                raise BibDBError(f"高级检索条件必须是 SearchTerm：{term!r}")
        sort = str(self.sort or "").strip().lower()
        if sort not in SEARCH_SORTS:
            raise BibDBError(f"排序方式非法：{self.sort!r}（支持 {'/'.join(SEARCH_SORTS)}）")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise BibDBError(f"limit 必须是整数：{self.limit!r}")
        if not 1 <= self.limit <= 200:
            raise BibDBError(f"limit 超出范围：{self.limit}（1–200）")
        try:
            min_score = float(self.min_score)
        except (TypeError, ValueError):
            raise BibDBError(f"min_score 必须是 0–1 的数：{self.min_score!r}") from None
        if not 0.0 <= min_score <= 1.0:
            raise BibDBError(f"min_score 超出范围：{self.min_score}（0–1）")
        object.__setattr__(self, "terms", terms)
        object.__setattr__(self, "sort", sort)
        object.__setattr__(self, "min_score", min_score)


def _search_author_candidates(raw: object) -> list[str]:
    """作者字段的候选串：整串 + 每个成员的「全名 + 姓氏」（spec §2.5 的 OR）。

    先按 `_AUTHOR_SEP_RE`（and / ; / &）拆全员、逐个去角色词；`CJK` 姓名由
    `first_author_surname` 原样返回（不分词）。
    """
    text = strip_author_roles(_latex_plain(raw))
    members = [part.strip() for part in _AUTHOR_SEP_RE.split(text) if part.strip()]
    candidates: list[str] = []
    for item in [normalize_search_text(text)] + [
            value for part in members
            for value in (normalize_search_text(part), first_author_surname(part))]:
        if item and item not in candidates:
            candidates.append(item)
    return candidates


def _search_candidates(field: str, record: dict, cache: dict[str, list[str]]) -> list[str]:
    """某字段的归一化候选串（同一条记录内缓存，多 term 复用；只读）。"""
    if field in cache:
        return cache[field]
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    if field in ("author", "editor"):
        items = _search_author_candidates(fields.get(field) or "")
    elif field == "doi":
        items = [normalize_doi(fields.get("doi")) or _doi_from_url(fields.get("url"))]
    elif field == "key":
        items = [str(record.get("key") or "").lower()]
    elif field == "alias":
        items = [str(alias or "").lower() for alias in record.get("aliases") or []]
    else:
        items = [normalize_search_text(fields.get(field) or "")]
    cache[field] = [item for item in items if item]
    return cache[field]


def _search_mode_score(needle: str, candidates: list[str], mode: str) -> float:
    """单字段按模式打分：命中 1.0，fuzzy 取相似度最大，其余 0.0（空 needle 不算命中）。"""
    if not needle:
        return 0.0
    if mode == "fuzzy":
        return max((_search_similarity(needle, cand) for cand in candidates),
                   default=0.0)
    best = 0.0
    for cand in candidates:
        if mode == "exact":
            score = 1.0 if cand == needle else 0.0
        elif mode == "prefix":
            score = 1.0 if cand.startswith(needle) else 0.0
        else:                                   # contains
            score = 1.0 if needle in cand else 0.0
        if score > best:
            best = score
            if best == 1.0:
                break
    return best


def _search_snippet(raw: object) -> str:
    """why-hit 的上下文：字段原文前 80 字（spec §2.7）。"""
    return str(raw if raw is not None else "")[:80]


def _search_record_term(term: SearchTerm, needle: str,
                         year_expr: tuple[str, int, int] | None, record: dict,
                         cache: dict[str, list[str]]) -> tuple[float, list[dict]] | None:
    """单条件对一条记录打分 → `(score, matched)`；不命中返回 None。"""
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    if term.field == "year":
        year = entry_year(fields)
        if not _search_year_matches(year_expr or ("=", 0, 0), year):
            return None
        return 1.0, [{"field": "year", "mode": term.mode, "score": 1.0,
                      "snippet": _search_snippet(year)}]
    if term.field == "any":
        best: tuple[float, str] | None = None
        for name in _ANY_SEARCH_FIELDS:
            score = _search_mode_score(needle, _search_candidates(name, record, cache),
                                       term.mode)
            if score > 0.0 and (best is None or score > best[0]):
                best = (score, name)
        if best is None:
            return None
        score, name = best
        return score, [{"field": name, "mode": term.mode, "score": round(score, 4),
                        "snippet": _search_snippet(fields.get(name) or "")}]
    if term.field in ("doi", "key", "alias"):
        score = _search_mode_score(needle, _search_candidates(term.field, record, cache),
                                   term.mode)
        if score <= 0.0:
            return None
        if term.field == "doi":
            snippet = fields.get("doi") or fields.get("url") or ""
        elif term.field == "key":
            snippet = record.get("key") or ""
        else:
            snippet = " / ".join(str(alias or "") for alias in record.get("aliases") or [])
        return score, [{"field": term.field, "mode": term.mode, "score": round(score, 4),
                        "snippet": _search_snippet(snippet)}]
    score = _search_mode_score(needle, _search_candidates(term.field, record, cache),
                               term.mode)
    if score <= 0.0:
        return None
    return score, [{"field": term.field, "mode": term.mode, "score": round(score, 4),
                    "snippet": _search_snippet(fields.get(term.field) or "")}]


def _search_hit(record: dict, score: float, matched: list[dict]) -> dict:
    """一条命中：键固定为 spec §2.8 的九个（`--bibtex` 由 CLI 另加 `bibtex`）。"""
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    return {
        "key": str(record.get("key") or ""),
        "score": round(float(score), 4),
        "matched": matched,
        "verified": record.get("verified") is True,
        "title": str(fields.get("title") or ""),
        "author": str(fields.get("author") or fields.get("editor") or ""),
        "year": entry_year(fields),
        "record": record,
    }


def _search_sort_key(hit: dict, sort: str, strictness: int) -> tuple:
    """排序键（spec §2.6）。`strictness` 是**整条查询**的模式严格度（各 term 秩和）。

    模式是 term 的属性而不是命中记录的属性，同一查询下它对所有命中相同；放在分数
    之后是为了在「同分」时保持确定的比较口径（年份/作者/key 继续决胜）。
    """
    fields = hit["record"].get("fields") if isinstance(hit["record"].get("fields"), dict) else {}
    surname = first_author_surname(fields.get("author") or fields.get("editor"))
    year = hit["year"]
    year_key = (0, -int(year)) if year and year.isdigit() else (1, 0)
    if sort == "year":
        return (year_key, surname, hit["key"])
    if sort == "author":
        return (surname, year_key, hit["key"])
    return (-hit["score"], strictness, year_key, surname, hit["key"])

# ── 引用库本体 ──────────────────────────────────────────────────────────────


class Store:
    """一个 JSONL 引用库。`load()` 读入内存，`save()` 原子写回（按 key 排序）。"""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path) if path else ""
        self._records: list[dict] = []
        self._by_key: dict[str, dict] = {}
        self._by_alias: dict[str, list[dict]] = {}
        self._by_content: dict[str, dict] = {}
        self._by_source: dict[tuple, tuple[dict, str]] = {}   # 来源身份 → (记录, 入库摘要)
        # 查库用的懒建索引（`convert.py` ⑪ 逐条匹配时才建一次；只读，导入期间不建）
        self._doi_index: dict[str, list[dict]] | None = None
        self._block_surname: dict[str, list[dict]] | None = None
        self._block_year: dict[str, list[dict]] | None = None
        self._title_match_index: dict[str, list[dict]] | None = None
        self.problems: list[str] = []
        self.dirty = False
        self.aliases_dirty = False     # aliases 只在 save()/refresh_aliases() 时按来源重算

    # -- 读写 ---------------------------------------------------------------

    @classmethod
    def load(cls, path: str, *, create: bool = False, lenient: bool = False) -> "Store":
        """读库。`create=True` 时文件不存在返回空库（导入用）；`lenient=True` 时
        结构问题只记进 `problems` 不抛异常（`lint` 用）。"""
        store = cls(path)
        if not os.path.isfile(store.path):
            if create:
                return store
            raise BibDBError(
                f"引用库不存在：{store.path}\n"
                "  先跑 `bibdb.py init`，或 `tools/bibdb_seed.py` 生成种子库。"
            )
        store._read()
        if store.problems and not lenient:
            preview = "；".join(store.problems[:3])
            more = (f"……另有 {len(store.problems) - 3} 处"
                    if len(store.problems) > 3 else "")
            raise BibDBError(
                f"引用库有 {len(store.problems)} 处结构问题（{preview}{more}）。"
                "先跑 `bibdb.py lint` 看全部。"
            )
        return store

    def _read(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError as exc:
            raise BibDBError(f"读不了引用库：{self.path}（{exc}）") from exc
        for lineno, line in enumerate(lines, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                self.problems.append(f"第 {lineno} 行不是合法 JSON：{exc}")
                continue
            if not isinstance(record, dict):
                self.problems.append(f"第 {lineno} 行不是 JSON 对象")
                continue
            for problem in validate_record(record):
                self.problems.append(f"第 {lineno} 行：{problem}")
            self._records.append(record)
        self._reindex()

    def _reindex(self) -> None:
        self._by_key = {}
        self._by_alias = {}
        self._by_content = {}
        self._by_source = {}
        for index, record in enumerate(self._records, start=1):
            key = record.get("key") if isinstance(record, dict) else None
            if not isinstance(key, str) or not key:
                continue
            if key in self._by_key:
                self.problems.append(f"第 {index} 条记录：key 重复 `{key}`（后者被忽略）")
                continue
            self._by_key[key] = record
            sha = record.get("content_sha256")
            if isinstance(sha, str) and sha:
                self._by_content.setdefault(sha, record)
            aliases = record.get("aliases")
            if isinstance(aliases, list):
                for alias in aliases:
                    if isinstance(alias, str) and alias:
                        self._by_alias.setdefault(alias, []).append(record)
            for source in record.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                identity = _source_identity(source)
                if identity not in self._by_source:
                    self._by_source[identity] = (
                        record, str(source.get("entry_sha256") or ""))

    def refresh_aliases(self) -> bool:
        """按各来源里的**原始 BibTeX key** 重算 aliases（确定性：只依赖最终来源集）。

        规则：只有**全库唯一指向一条记录**的 key 才记成别名。paper-local key（两篇都用
        `a1` 指不同文献）**绝不能**当别名 —— 否则将来「先查库按 key 命中就跳过核实」会
        假命中另一篇文献的引用，这是比漏查更糟的错误。撞车的 key 不记；查询请走 DOI/题名。

        **B-188 增补（2026-10-22）：记录里已有的 aliases 必须保留（并集）**。
        为什么不能只按来源重算：`edit --new-key` / `merge-records` 会把**旧 key** 写进
        `aliases`（设计卡 §1.4「旧 key 自动进 aliases」），它没有对应来源，纯重算就会把
        改名的追溯链**静默清掉**。实测真实库一旦调用 `save()`，按旧口径会丢掉 79 条记录
        的 254 个别名（如 `13-9` 的 `12-9`）——所以改成两遍分配：
          ① 先分来源派生的别名（真值，优先占位；同一别名只给唯一持有者）；
          ② 再并入记录已有的别名（`edit`/`merge` 写入的历史 key），全局撞车的不记
             （同一别名只允许一条记录持有；与别的 canonical key 同名的不记）。
        """
        owners: dict[str, set[str]] = {}
        for record in self._by_key.values():
            key = str(record.get("key") or "")
            if not key:
                continue
            owners.setdefault(key, set()).add(key)
            for source in record.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                source_key = source.get("key")
                if isinstance(source_key, str) and source_key:
                    owners.setdefault(source_key, set()).add(key)
        # ① 来源派生别名（按 key 排序，保证确定性）
        claimed: set[str] = set()
        derived: dict[str, set[str]] = {}
        for key in sorted(self._by_key):
            got = {alias for alias, group in owners.items()
                   if len(group) == 1 and key in group and alias != key}
            derived[key] = got
            claimed |= got
        # ② 并集：保留记录原有 aliases（改名/合并写进去的历史 key）
        changed = False
        for key in sorted(self._by_key):
            record = self._by_key[key]
            existing = {alias for alias in (record.get("aliases") or [])
                        if isinstance(alias, str) and alias and alias != key}
            kept = set(derived[key])
            for alias in sorted(existing):
                if alias in claimed or alias in self._by_key:
                    continue
                kept.add(alias)
                claimed.add(alias)
            aliases = sorted(kept)
            if record.get("aliases") != aliases:
                record["aliases"] = aliases
                changed = True
        if changed:
            self.aliases_dirty = True
            self._by_alias = {}
            for record in self._by_key.values():
                for alias in record.get("aliases") or []:
                    self._by_alias.setdefault(alias, []).append(record)
        return changed

    def save(self, *, force: bool = False) -> bool:
        """按 key 排序原子写回。返回是否真的写了（无改动时不重写 → git 零 diff）。"""
        if self.problems:
            raise BibDBError(
                f"库有 {len(self.problems)} 处结构问题，拒绝覆盖写（先 lint 修好）："
                f"{'；'.join(self.problems[:3])}"
            )
        self.refresh_aliases()
        if not self.dirty and not self.aliases_dirty and not force \
                and os.path.isfile(self.path):
            return False
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        text = "".join(_record_line(self._by_key[key]) + "\n"
                       for key in sorted(self._by_key))
        fsutil.atomic_write(self.path, text, keep_backups=0)
        self.dirty = False
        self.aliases_dirty = False
        return True

    # -- 程序化修改 ---------------------------------------------------------

    def _insert(self, record: dict) -> None:
        self._records.append(record)
        self._by_key[record["key"]] = record
        self._by_content.setdefault(record["content_sha256"], record)
        for source in record.get("sources") or []:
            if isinstance(source, dict):
                identity = _source_identity(source)
                self._by_source.setdefault(
                    identity, (record, str(source.get("entry_sha256") or "")))
        self.dirty = True

    def _append_source(self, record: dict, source: dict) -> bool:
        sources = record.setdefault("sources", [])
        wanted = _source_identity(source)
        for existing in sources:
            if isinstance(existing, dict) and _source_identity(existing) == wanted:
                return False
        sources.append(source)
        self._by_source.setdefault(
            wanted, (record, str(source.get("entry_sha256") or "")))
        return True

    def _processed_sha(self, source: dict) -> tuple[dict | None, str]:
        """这条来源之前处理过吗？返回 (记录, 当时的内容摘要) 或 (None, "")。"""
        return self._by_source.get(_source_identity(source), (None, ""))

    def _mark_verified(self, record: dict, source: dict, now: str) -> None:
        record["verified"] = True
        record["verified_at"] = now
        kind = str(source.get("kind") or "")
        record["method"] = kind if kind in (KIND_PUBLISHED, KIND_VERIFIED) else KIND_VERIFIED

    def _replace_content(self, record: dict, entry_type: str, fields: dict,
                         sha: str) -> None:
        record["entry_type"] = str(entry_type or "").strip().lower()
        record["fields"] = fields
        record["content_sha256"] = sha

    def _record_conflict(self, record: dict, diff: dict, *, old_sha: str,
                         incoming_sha: str, source: dict, note: str,
                         resolution: str, now: str,
                         max_conflicts: int = 50,
                         resolved: bool = False) -> bool:
        """记一条字段冲突（同一个 incoming 内容只记一次 → 重跑幂等）。

        `resolved=True` 时同时写 `resolved_at`（人工裁决产生的新冲突，例如
        `import-bib --force` 采用新稿值的记录）；导入时自动记的冲突**不带**它，
        表示「待人工裁决」。
        """
        conflicts = record.setdefault("conflicts", [])
        for existing in conflicts:
            if (isinstance(existing, dict)
                    and existing.get("incoming_sha256") == incoming_sha
                    and existing.get("resolution") == resolution):
                return False
        if len(conflicts) >= max_conflicts:
            return False
        if not diff:
            diff = {"$content": {"existing": old_sha, "incoming": incoming_sha}}
        entry = {
            "at": now,
            "resolution": resolution,
            "note": note,
            "existing_sha256": old_sha,
            "incoming_sha256": incoming_sha,
            "field_diff": diff,
            "source": source,
        }
        if resolved:
            entry["resolved_at"] = now
            # 第 130 条：`--force` 采用新稿值时另记的**审计条目**（旧值留档）。
            # 它只是那次替换的证据、不是一条独立的「待裁决项」，所以清单/编号把它排除；
            # 否则每做一次 force 都会在中间插一条，把后面条目的 C 编号挤动。
            entry["recorded_by"] = "force"
        conflicts.append(entry)
        return True

    # -- 删除记录（B-188 merge-records 用）----------------------------------

    def remove_record(self, key: str) -> dict:
        """删掉一条记录（按 canonical key），重建索引并标 dirty。

        只给 `merge-records` 用（合并后删去被并入的记录）；**不做** alias 重指
        （旧 key 由调用方先并进幸存记录的 aliases）。不在这里落盘。
        """
        record = self._by_key.get(key)
        if record is None:
            raise BibDBError(f"库里没有 key `{key}`")
        self._records = [item for item in self._records if item is not record]
        self.dirty = True
        self._reindex()
        return record

    # -- 冲突裁决（第 130 条，2026-10-18）-----------------------------------

    def _apply_adopt(self, record: dict, conflict: dict, result: dict, now: str,
                     *, note: str, dry_run: bool,
                     allow_overlap: bool) -> dict:
        """`resolve --action adopt` 的执行部分：**逐字段**把 incoming 写进记录。

        只改这条冲突 `field_diff` 涉及的字段：`incoming` 有值 → 覆盖；为空 → 从记录里
        删掉（新稿没有这个字段；`$entry_type` 除外——条目类型为空时不动）。随后重算
        `content_sha256`，把冲突标 `adopted`；重叠的其它待裁决冲突按 `allow_overlap`
        决定「拒绝」还是标 `superseded`。`dry_run` 只算不写（不碰记录/冲突/时间戳）。
        """
        key = str(record.get("key") or "")
        diff = conflict.get("field_diff")
        if not isinstance(diff, dict) or not diff:
            raise BibDBError(
                f"记录 `{key}` 的这条冲突没有字段差异，无法 adopt（可改用 keep/defer）")
        planned: dict[str, dict] = {}
        for name, values in diff.items():
            pair = values if isinstance(values, dict) else {}
            old_value = pair.get("existing", "")
            new_value = pair.get("incoming", "")
            planned[name] = {
                "old": old_value,
                "new": new_value,
                "remove": name != "$entry_type" and not str(new_value or "").strip(),
            }
        adopted_fields = set(planned)
        overlapping = overlapping_pending_conflicts(record, conflict, adopted_fields)
        # 预算 adopt 后的 entry_type/fields/content_sha256（dry-run 也要能报出「将变成什么」）
        candidate_etype = str(record.get("entry_type") or "")
        candidate_fields = dict(record.get("fields") or {})
        for name, change in planned.items():
            if name == "$entry_type":
                if str(change["new"] or "").strip():
                    candidate_etype = str(change["new"]).strip().lower()
            elif change["remove"]:
                candidate_fields.pop(name, None)
            else:
                candidate_fields[name] = str(change["new"])
        candidate_fields = dict(sorted(candidate_fields.items()))
        sha_before = str(record.get("content_sha256") or "")
        sha_after = content_sha256(candidate_etype, candidate_fields)
        result.update({
            "adopted_fields": planned,
            "adopted_field_names": [name for name in planned if name != "$entry_type"],
            "content_sha256_before": sha_before,
            "content_sha256_after": sha_after,
            "overlapping": overlapping,
            "superseded": [],
        })
        if overlapping and not allow_overlap:
            detail = "；".join(
                f"#{item['index']}（共享 {('、'.join(item['shared']))}，"
                f"指纹 {item['incoming_sha256'][:12]}）" for item in overlapping)
            raise BibDBError(
                f"记录 `{key}` 还有 {len(overlapping)} 条待裁决冲突与本条 adopt 的字段重叠："
                f"{detail}。这些冲突的旧值会被本次更新覆盖——请先用 keep/defer 或按指纹"
                "逐条处理，或加 `--allow-overlap` 把重叠冲突标 `superseded` 后再 adopt。")
        if dry_run:
            return result

        record["entry_type"] = candidate_etype
        record["fields"] = candidate_fields
        record["content_sha256"] = sha_after
        field_text = "、".join(f"`{name}`" for name in planned)
        old_note = str(conflict.get("note") or "")
        conflict["resolution"] = RESOLUTION_ADOPTED
        conflict["resolved_at"] = now
        conflict["note"] = (
            (old_note + "；" if old_note else "")
            + f"人工裁决：采用新稿值（逐字段：{field_text}）"
            + (f"；备注：{note.strip()}" if note.strip() else ""))
        for item in overlapping:
            other = record["conflicts"][item["index"]]
            other_note = str(other.get("note") or "")
            other["resolution"] = RESOLUTION_SUPERSEDED
            other["resolved_at"] = now
            other["note"] = (
                (other_note + "；" if other_note else "")
                + "被同一记录 adopt 的裁决取代（共享字段："
                + "、".join(item["shared"]) + "）：旧基线已更新，本条差异不再适用")
            result["superseded"].append(item)
        record["updated_at"] = now
        self.dirty = True
        result["changed"] = True
        return result

    def resolve_conflicts(self, key: str, action: str, *, fields=None,
                          all_fields: bool = False, note: str = "",
                          dry_run: bool = False,
                          incoming_sha256: str = "",
                          allow_overlap: bool = False) -> dict:
        """把一条记录的**待裁决**冲突标记为已裁决。

        `action="keep"`  → `resolution="kept-existing"`（保留库内已核实旧值，**不改字段**）
        `action="defer"` → `resolution="deferred"`（待人工查证：先移出待裁决清单）
        `action="adopt"` → **逐字段采用新稿值**：按冲突指纹精确定位**一条**冲突，把该冲突
        `field_diff` 里每个字段的 `incoming` 写进库记录 `fields`（`incoming` 为空 = 该字段
        在新稿里没有 → 从记录里删掉；`$entry_type` = 改条目类型），重算 `content_sha256`，
        并把该冲突标 `resolution="adopted"` + `resolved_at`。
        ⚠️ adopt **必须**给 `incoming_sha256`（一条冲突 = 一个指纹），不给就报错。
        整条替换仍走 `import-bib --force`（那会把被替换的旧冲突标 `forced-replaced`）。

        **adopt 的重叠保护**（第 135 条）：同一记录其它**待裁决**冲突若与本条 adopt 的字段
        有交集，它们的 `existing` 旧值已过期 → 默认**拒绝并报错**（先 keep/defer 或按指纹
        逐条处理）；`allow_overlap=True` 时把这些重叠冲突标 `resolution="superseded"` +
        `resolved_at`（报告里注明「被更新的裁决取代」），再执行 adopt。

        选择规则（三选一，CLI 层强制互斥）：
          · `--all-fields`：该记录**所有待裁决**冲突；
          · `--fields title,author`：只选 `field_diff` 里包含这些字段的待裁决冲突
            （一条冲突可能跨多个字段，选中即整条标记）；
          · `--incoming-sha256 SHA`：按**冲突指纹**精确选**一条**（至少 8 位十六进制
            前缀，须在本记录冲突里唯一）——同一记录有多条来源不同的冲突、字段又重叠时，
            只有指纹能避免 `--fields` 误伤其它条。
        幂等：同一动作重复执行 → `already=True, count=0`，不报错、不改库。
        未知 key / 未知字段 / 没有冲突 → 抛 `BibDBError`（CLI 转退出码 2）。

        返回 dict（`count` / `matched` / `changed` / `already` / `dry_run` /
        `resolution` / `resolved_at` / `available_fields` …），供 CLI 打印与 `--json`。
        """
        record_key = str(key or "").strip()
        if not record_key:
            raise BibDBError("resolve 需要 --key（库内记录 key，见 CONFLICTS.md）")
        if action not in RESOLVE_ACTIONS:
            raise BibDBError(
                f"未知裁决动作 {action!r}（只支持 keep / defer / adopt；"
                "整条替换请用 import-bib --force）")
        if allow_overlap and action != ACTION_ADOPT:
            raise BibDBError("--allow-overlap 只用于 --action adopt（其它动作没有重叠问题）")
        record = self._by_key.get(record_key)
        if record is None:
            raise BibDBError(
                f"引用库里没有记录 key `{record_key}`——请用 CONFLICTS.md 里"
                "「库内记录」那个键（跨期撞车的记录可能带 -2 后缀）")
        conflicts = record.get("conflicts")
        if not isinstance(conflicts, list) or not conflicts:
            raise BibDBError(f"记录 `{record_key}` 没有字段冲突，无需裁决")

        sha_prefix = str(incoming_sha256 or "").strip().lower()
        if action == ACTION_ADOPT and not sha_prefix:
            raise BibDBError(
                "--action adopt 必须用 --incoming-sha256（冲突指纹）精确定位**一条**冲突；"
                "字段/整条之外的批量 adopt 不支持（避免误改）")
        if action == ACTION_ADOPT and (all_fields or fields):
            raise BibDBError(
                "--action adopt 只接受 --incoming-sha256；不要同时给 --fields / --all-fields")
        wanted: list[str] = []
        available = sorted({
            field for conflict in conflicts if isinstance(conflict, dict)
            for field in conflict_fields(conflict)
        })
        preselected: list[tuple[int, dict]] = []
        if sha_prefix:
            # 精确指定「一条」冲突：同一记录可能有多条待裁决冲突（来源不同），
            # `--fields` 只能按字段挑，会同时命中字段重叠的其它条；指纹才能精确到条。
            preselected = match_conflict_by_sha(record_key, conflicts, sha_prefix)
        else:
            for name in (fields or []):
                name = str(name).strip()
                if not name:
                    continue
                if name == "entry_type":   # 对编辑友好：--fields entry_type = $entry_type
                    name = "$entry_type"
                wanted.append(name)
            wanted = sorted(set(wanted))
            if not all_fields and not wanted:
                raise BibDBError(
                    "resolve 必须给 --fields（逗号分隔，如 title,author）、--all-fields，"
                    "或 --incoming-sha256（按冲突指纹精确定位）")
            if wanted:
                unknown = [name for name in wanted if name not in available]
                if unknown:
                    raise BibDBError(
                        f"记录 `{record_key}` 的冲突里没有这些字段：{'、'.join(unknown)}"
                        f"（可用字段：{'、'.join(available) or '无'}）")
            preselected = [(index, conflict) for index, conflict in enumerate(conflicts)
                           if isinstance(conflict, dict)]

        selected: list[tuple[int, dict]] = []
        for index, conflict in preselected:
            if not conflict_is_pending(conflict):
                continue
            if sha_prefix or all_fields or (set(conflict_fields(conflict)) & set(wanted)):
                selected.append((index, conflict))

        now = _now_iso()
        target = RESOLVE_ACTIONS[action]
        result: dict = {
            "key": record_key,
            "action": action,
            "resolution": target,
            "all_fields": bool(all_fields),
            "fields": wanted,
            "available_fields": available,
            "incoming_sha256": sha_prefix,
            "dry_run": bool(dry_run),
            "count": len(selected),
            "matched": [
                {"index": index,
                 "fields": conflict_fields(conflict),
                 "incoming_sha256": str(conflict.get("incoming_sha256") or ""),
                 "resolution_before": str(conflict.get("resolution") or "")}
                for index, conflict in selected
            ],
            "changed": False,
            "already": False,
            "resolved_at": "",
        }
        if not selected:
            # 幂等：这些冲突/字段此前已裁决过 → 无变化成功（不是错误）。
            resolved_conflicts = [
                conflict for conflict in conflicts
                if isinstance(conflict, dict) and not conflict_is_pending(conflict)
            ]
            if sha_prefix:
                hit = [conflict for _index, conflict in preselected
                       if not conflict_is_pending(conflict)]
            elif all_fields:
                hit = resolved_conflicts
            else:
                hit = [conflict for conflict in resolved_conflicts
                       if set(conflict_fields(conflict)) & set(wanted)]
            if hit:
                actions = sorted({str(conflict.get("resolution") or "")
                                  for conflict in hit})
                if action == ACTION_ADOPT and actions != [RESOLUTION_ADOPTED]:
                    raise BibDBError(
                        f"记录 `{record_key}` 的这条冲突已裁决为 "
                        f"{'、'.join(actions)}，不能再用 adopt 改判；"
                        "如需改为采用新稿值，请人工处理（先撤销裁决）")
                result["already"] = True
                result["resolved_count"] = len(hit)
                result["resolved_actions"] = actions
                return result
            raise BibDBError(f"记录 `{record_key}` 当前没有可裁决的冲突")

        result["resolved_at"] = now
        if action == ACTION_ADOPT:
            return self._apply_adopt(record, selected[0][1], result, now,
                                     note=note, dry_run=bool(dry_run),
                                     allow_overlap=bool(allow_overlap))
        if dry_run:
            return result     # --dry-run：只算不写库（不改冲突、不动 updated_at）

        adjudication_note = (
            "人工裁决：保留库内已核实旧值" if action == ACTION_KEEP
            else "人工裁决：待人工查证（先从待裁决清单移走）")
        extra = f"；备注：{note.strip()}" if note.strip() else ""
        for _index, conflict in selected:
            old_note = str(conflict.get("note") or "")
            conflict["resolution"] = target
            conflict["resolved_at"] = now
            conflict["note"] = (old_note + "；" if old_note else "") + adjudication_note + extra
        record["updated_at"] = now
        self.dirty = True
        result["changed"] = True
        return result

    def resolve_replace(self, key: str, entry_type: str, fields: dict, *,
                        incoming_sha256: str, bib_sha256: str = "",
                        note: str = "", dry_run: bool = False,
                        allow_overlap: bool = False) -> dict:
        """`resolve --action replace`（B-127）：把**人工裁决 bib** 整条写进库记录。

        与 `adopt` 的区别：adopt 只能采用冲突里留档的新稿值（逐字段）；replace 采用用户在
        GUI 里编辑/输入的**整条 bib 条目**——以它的 `entry_type`+`fields` 整条替换库内记录
        对应内容（**key 不变**；`verified` / `sources` / `method` / `aliases` 不动），重算
        `content_sha256`，并把目标冲突标 `resolution="adopted"` + `resolved_by="custom-bib"`
        + `resolved_bib_sha256`（人工裁决 bib 文件字节 sha256）+ note。

        重叠保护与 adopt **同一判据**（`overlapping_pending_conflicts`）：本次实际改动的
        字段若与同记录其它**待裁决**冲突的字段有交集 → 默认拒绝；`allow_overlap=True` 时
        把它们标 `superseded`。

        幂等：同一指纹 + 同一 bib sha256 重复执行 → `already=True`（不写库）；已裁决为其它
        结果 / 其它 bib → 报错（**不做 reopen**，改判请用备份 restore 整库恢复）。
        """
        record_key = str(key or "").strip()
        if not record_key:
            raise BibDBError("resolve 需要 --key（库内记录 key，见 CONFLICTS.md）")
        record = self._by_key.get(record_key)
        if record is None:
            raise BibDBError(
                f"引用库里没有记录 key `{record_key}`——请用 CONFLICTS.md 里"
                "「库内记录」那个键（跨期撞车的记录可能带 -2 后缀）")
        conflicts = record.get("conflicts")
        if not isinstance(conflicts, list) or not conflicts:
            raise BibDBError(f"记录 `{record_key}` 没有字段冲突，无需裁决")
        sha_prefix = str(incoming_sha256 or "").strip().lower()
        if not sha_prefix:
            raise BibDBError(
                "--action replace 必须配合 --incoming-sha256（冲突指纹）精确定位**一条**冲突")
        selected = match_conflict_by_sha(record_key, conflicts, sha_prefix)
        conflict_index, conflict = selected[0]

        new_etype = str(entry_type or "").strip().lower()
        new_fields = canonical_fields(fields or {})
        old_etype = str(record.get("entry_type") or "")
        old_fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        sha_before = str(record.get("content_sha256") or "")
        sha_after = content_sha256(new_etype, new_fields)
        planned: dict[str, dict] = {}
        for name in sorted(set(old_fields) | set(new_fields)):
            old_value = old_fields.get(name, "")
            new_value = new_fields.get(name, "")
            if old_value == new_value:
                continue
            planned[name] = {"old": old_value, "new": new_value,
                             "remove": not str(new_value or "").strip()}
        if old_etype.lower() != new_etype:
            planned["$entry_type"] = {"old": old_etype, "new": new_etype,
                                      "remove": False}
        available = sorted({
            field for item in conflicts if isinstance(item, dict)
            for field in conflict_fields(item)
        })
        result: dict = {
            "key": record_key,
            "action": ACTION_REPLACE,
            "resolution": RESOLUTION_ADOPTED,
            "resolved_by": RESOLVED_BY_CUSTOM_BIB,
            "all_fields": False,
            "fields": [],
            "available_fields": available,
            "incoming_sha256": sha_prefix,
            "selected_incoming_sha256": str(conflict.get("incoming_sha256") or ""),
            "resolved_bib_sha256": str(bib_sha256 or ""),
            "entry_type": new_etype,
            "dry_run": bool(dry_run),
            "count": 1,
            "matched": [{
                "index": conflict_index,
                "fields": conflict_fields(conflict),
                "incoming_sha256": str(conflict.get("incoming_sha256") or ""),
                "resolution_before": str(conflict.get("resolution") or ""),
            }],
            "changed": False,
            "already": False,
            "resolved_at": "",
            # GUI「预览差异」契约（spec §4.2 批73 补）：逐字段 old→new + 删除标记
            "replaced_fields": planned,
            "content_sha256_before": sha_before,
            "content_sha256_after": sha_after,
            "overlapping": [],
            "superseded": [],
        }
        if not conflict_is_pending(conflict):
            same_bib = (
                str(conflict.get("resolution") or "") == RESOLUTION_ADOPTED
                and str(conflict.get("resolved_by") or "") == RESOLVED_BY_CUSTOM_BIB
                and str(conflict.get("resolved_bib_sha256") or "") == str(bib_sha256 or "")
            )
            if same_bib:
                result["already"] = True
                result["resolved_count"] = 1
                result["resolved_actions"] = [str(conflict.get("resolution") or "")]
                return result
            detail = str(conflict.get("resolution") or "（未标记）")
            raise BibDBError(
                f"记录 `{record_key}` 的这条冲突已裁决为 {detail}，不能再用 replace 改判；"
                "如需改判，请用 `bibdb.py restore --backup …` 整库恢复到裁决前再重新裁决"
                "（不提供 reopen）")
        now = _now_iso()
        adopted_fields = set(planned)
        overlapping = overlapping_pending_conflicts(record, conflict, adopted_fields)
        result["resolved_at"] = now
        result["overlapping"] = overlapping
        if overlapping and not allow_overlap:
            detail = "；".join(
                f"#{item['index']}（共享 {('、'.join(item['shared']))}，"
                f"指纹 {item['incoming_sha256'][:12]}）" for item in overlapping)
            raise BibDBError(
                f"记录 `{record_key}` 还有 {len(overlapping)} 条待裁决冲突与本次 replace 的"
                f"字段重叠：{detail}。这些冲突的旧值会被本次更新覆盖——请先用 keep/defer "
                "或按指纹逐条处理，或加 `--allow-overlap` 把重叠冲突标 `superseded` 后"
                "再 replace。")
        if dry_run:
            return result

        record["entry_type"] = new_etype
        record["fields"] = new_fields
        record["content_sha256"] = sha_after
        field_text = "、".join(f"`{name}`" for name in planned) or "（与库内一致）"
        old_note = str(conflict.get("note") or "")
        conflict["resolution"] = RESOLUTION_ADOPTED
        conflict["resolved_at"] = now
        conflict["resolved_by"] = RESOLVED_BY_CUSTOM_BIB
        conflict["resolved_bib_sha256"] = str(bib_sha256 or "")
        conflict["note"] = (
            (old_note + "；" if old_note else "")
            + f"人工裁决：采用自定义裁决 bib（整条 entry_type+fields：{field_text}）"
            + (f"；bib sha256={str(bib_sha256 or '')[:12]}…" if bib_sha256 else "")
            + (f"；备注：{note.strip()}" if note.strip() else ""))
        for item in overlapping:
            other = record["conflicts"][item["index"]]
            other_note = str(other.get("note") or "")
            other["resolution"] = RESOLUTION_SUPERSEDED
            other["resolved_at"] = now
            other["note"] = (
                (other_note + "；" if other_note else "")
                + "被同一记录 replace 的裁决取代（共享字段："
                + "、".join(item["shared"]) + "）：旧基线已更新，本条差异不再适用")
            result["superseded"].append(item)
        record["updated_at"] = now
        self.dirty = True
        result["changed"] = True
        return result

    def adopt_all_pending(self, *, note: str = "", dry_run: bool = False) -> dict:
        """`resolve --adopt-all-pending`（B-127）：对**全部待裁决冲突**逐条按 adopt 语义应用。

        规则（spec §4.2）：
          · 同一记录的多条待裁决冲突若**字段有重叠** → **整体拒绝**（`BibDBError`，列出
            冲突 #）——后应用的裁决会让先应用的旧值基线失效，批量无法可靠；
          · 其余逐条应用：每个冲突按 `field_diff` 逐字段 incoming 覆盖/删除
            （`$entry_type` 改条目类型），重算 `content_sha256`，标
            `resolution="adopted"` + `resolved_at` + note；
          · `dry_run=True` 只算不写（不碰记录/冲突/时间戳）；真写由调用方**单次**
            `store.save()` 原子落盘（本方法只改内存）。
        返回 dict：`adopted` 逐条明细（record_key/bib_key/incoming_sha256/fields）、
        `skipped`（无 field_diff 无法应用的冲突）、`records`/`conflicts`/`changed` 等。
        """
        now = _now_iso()
        note_text = note.strip()
        plan: list[tuple[dict, int, dict, dict]] = []
        blocked: list[dict] = []
        skipped: list[dict] = []
        for record in self.records():
            conflicts = record.get("conflicts")
            if not isinstance(conflicts, list) or not conflicts:
                continue
            record_key = str(record.get("key") or "")
            pending = [(index, conflict) for index, conflict in enumerate(conflicts)
                       if isinstance(conflict, dict) and conflict_is_pending(conflict)]
            if not pending:
                continue
            # 同记录待裁决冲突两两检查字段重叠（整批拒绝 → 提示逐条处理）
            overlaps: list[dict] = []
            for position, (index, conflict) in enumerate(pending):
                for other_index, other in pending[position + 1:]:
                    shared = sorted(set(conflict_fields(conflict))
                                    & set(conflict_fields(other)))
                    if shared:
                        overlaps.append({"conflict": index, "other_conflict": other_index,
                                         "shared": shared})
            if overlaps:
                blocked.append({"record_key": record_key, "overlaps": overlaps})
                continue
            for index, conflict in pending:
                diff = conflict.get("field_diff")
                if not isinstance(diff, dict) or not diff:
                    skipped.append({
                        "record_key": record_key,
                        "conflict_index": index,
                        "incoming_sha256": str(conflict.get("incoming_sha256") or ""),
                        "reason": "这条冲突没有字段差异（field_diff 为空），无法按 adopt 应用",
                    })
                    continue
                planned: dict[str, dict] = {}
                for name, values in diff.items():
                    pair = values if isinstance(values, dict) else {}
                    old_value = pair.get("existing", "")
                    new_value = pair.get("incoming", "")
                    planned[str(name)] = {
                        "old": old_value,
                        "new": new_value,
                        "remove": name != "$entry_type" and not str(new_value or "").strip(),
                    }
                plan.append((record, index, conflict, planned))
        if blocked:
            parts: list[str] = []
            for row in blocked:
                pairs = "、".join(
                    f"#{item['conflict']} ↔ #{item['other_conflict']}"
                    f"（共享 {'、'.join(item['shared'])}）"
                    for item in row["overlaps"])
                parts.append(f"记录 `{row['record_key']}`：{pairs}")
            raise BibDBError(
                f"有 {len(blocked)} 条记录的待裁决冲突**字段重叠**，不能一键全部采用："
                + "；".join(parts)
                + "。请改用逐条处理（--key + --incoming-sha256，必要时配 --allow-overlap）。")
        adopted: list[dict] = []
        for record, index, conflict, planned in plan:
            record_key = str(record.get("key") or "")
            source = conflict.get("source")
            source = source if isinstance(source, dict) else {}
            adopted.append({
                "record_key": record_key,
                "bib_key": str(source.get("key") or record_key),
                "incoming_sha256": str(conflict.get("incoming_sha256") or ""),
                "conflict_index": index,
                "fields": planned,
            })
            if dry_run:
                continue
            candidate_etype = str(record.get("entry_type") or "")
            candidate_fields = dict(record.get("fields") or {})
            for name, change in planned.items():
                if name == "$entry_type":
                    if str(change["new"] or "").strip():
                        candidate_etype = str(change["new"]).strip().lower()
                elif change["remove"]:
                    candidate_fields.pop(name, None)
                else:
                    candidate_fields[name] = str(change["new"])
            record["entry_type"] = candidate_etype
            record["fields"] = dict(sorted(candidate_fields.items()))
            record["content_sha256"] = content_sha256(candidate_etype,
                                                      record["fields"])
            old_note = str(conflict.get("note") or "")
            field_text = "、".join(f"`{name}`" for name in planned)
            conflict["resolution"] = RESOLUTION_ADOPTED
            conflict["resolved_at"] = now
            conflict["note"] = (
                (old_note + "；" if old_note else "")
                + f"人工裁决：一键全部采用新稿值（逐字段：{field_text}）"
                + (f"；备注：{note_text}" if note_text else ""))
            record["updated_at"] = now
        if not dry_run and plan:
            self.dirty = True
        return {
            "adopted": adopted,
            "skipped": skipped,
            # 同记录字段重叠在批量模式下一律整体拒绝（不标 superseded），保留空列表让
            # 外壳契约与单条 adopt/replace 对齐（spec §4.2「dry-run 输出 ... superseded」）。
            "superseded": [],
            "records": len({row["record_key"] for row in adopted}),
            "conflicts": len(adopted),
            "dry_run": bool(dry_run),
            "resolved_at": now,
            "changed": bool(plan) and not dry_run,
        }

    # -- upsert -------------------------------------------------------------

    def import_entry(self, raw_key: str, entry_type: str, raw_fields: dict,
                     source: dict, *, verified: bool, force: bool = False,
                     now: str | None = None) -> UpsertResult:
        """导入一条条目（幂等 upsert；规则见模块头「幂等与冲突」）。

        ⚠️ **内容判据先行**：只要库里已有同内容条目就直接并来源，不管这个 key 当前有没有
        被占用。这是「同一批 bib 用不同顺序/重复导入都得到同一个库」的关键 —— 否则
        首轮导入时某条 key 还没被占用、库里也还没有那条内容，条目会先建成一条记录，
        第二轮同样的输入却命中「已存在的内容」走另一条分支，文件就不再逐字节相同。
        """
        now = now or _now_iso()
        key = str(raw_key or "").strip()
        if not key:
            return UpsertResult("skipped", reason="没有 key")
        entry_type = str(entry_type or "").strip().lower()
        fields = canonical_fields(raw_fields or {})
        sha = content_sha256(entry_type, fields)
        source = _clean_source(source, now)

        # ① 同内容 → 并来源（幂等；也吸收「不同期不同 key 指同一作品」的重复）
        content_hit = self._by_content.get(sha)
        if content_hit is not None:
            return self._merge_into(content_hit, source, verified, now)
        # ② 同 key → 冲突 / 覆盖 / key 撞车
        existing = self._by_key.get(key)
        if existing is not None:
            return self._upsert_existing(existing, entry_type, fields, sha,
                                         source, verified, force, now)
        # ③ 全新条目
        return self._insert_new(key, entry_type, fields, sha, source, verified, now)

    def _insert_new(self, key: str, entry_type: str, fields: dict, sha: str,
                    source: dict, verified: bool, now: str) -> UpsertResult:
        record = _new_record(key, entry_type, fields, sha, source,
                             verified=verified, now=now)
        self._insert(record)
        return UpsertResult("new", key)

    def _disambiguate(self, key: str, entry_type: str, fields: dict, sha: str,
                      source: dict, verified: bool, now: str) -> UpsertResult:
        """跨期 key 撞车（不同作品同名 key）→ 新条目加 `-2`/`-3` 后缀。

        原 key 不写死进 aliases：alias 由 `refresh_aliases()` 从各来源的原始 key 推导，
        只有「全库唯一指向一条记录」的 key 才当别名（详见该方法的注释）。
        """
        suffix = 2
        while f"{key}-{suffix}" in self._by_key:
            suffix += 1
        new_key = f"{key}-{suffix}"
        result = self._insert_new(new_key, entry_type, fields, sha, source,
                                  verified, now)
        result.disambiguated = True
        return result

    def _merge_into(self, record: dict, source: dict, verified: bool,
                    now: str) -> UpsertResult:
        """内容相同 → 只补来源（或未核实→已核实升级），不动字段、不重复建条目。"""
        changed = self._append_source(record, source)
        sources_added = 1 if changed else 0
        if verified and record.get("verified") is not True:
            self._mark_verified(record, source, now)
            changed = True
        if changed:
            record["updated_at"] = now
            self.dirty = True
        return UpsertResult("merged", str(record.get("key") or ""),
                            sources_added=sources_added)

    def _upsert_existing(self, record: dict, entry_type: str, fields: dict, sha: str,
                         source: dict, verified: bool, force: bool,
                         now: str) -> UpsertResult:
        if record.get("content_sha256") == sha:     # 上游内容判据的兜底（防御式）
            return self._merge_into(record, source, verified, now)
        old_sha = str(record.get("content_sha256") or "")
        diff = _field_diff(record, fields, entry_type)

        if force:                                   # 人工裁决：强制覆盖（旧字段留档）
            # 第 130 条：`--force` 的语义 = 人工**采用新稿值**。因此：
            #   ① 记录里原来「待裁决」的冲突（都基于被替换掉的旧基线）不再适用 ——
            #      统一标 `forced-replaced` 并写 `resolved_at`，否则它们会以「待裁决」
            #      身份继续挂在清单里，和新基线对照毫无意义；
            #   ② 这次替换本身记一条 `forced-replaced` 冲突（旧字段全量留档），也带
            #      `resolved_at`，可审计。
            for old_conflict in record.get("conflicts") or []:
                if not isinstance(old_conflict, dict) or not conflict_is_pending(old_conflict):
                    continue
                old_note = str(old_conflict.get("note") or "")
                old_conflict["resolution"] = RESOLUTION_FORCED
                old_conflict["resolved_at"] = now
                old_conflict["note"] = (
                    (old_note + "；" if old_note else "")
                    + "随 import-bib --force 采用新稿值：旧基线已被替换，本条差异不再适用"
                )
            self._replace_content(record, entry_type, fields, sha)
            self._append_source(record, source)
            if verified:
                self._mark_verified(record, source, now)
            self._record_conflict(record, diff, old_sha=old_sha, incoming_sha=sha,
                                  source=source, note="人工 --force 覆盖（采用新稿值）",
                                  resolution=RESOLUTION_FORCED, now=now, resolved=True)
            record["updated_at"] = now
            self.dirty = True
            return UpsertResult("updated", record["key"])

        if not works_match(record.get("fields") or {}, fields):
            # 同 key 但**不是同一作品**（paper-local key 撞车）→ 各留各的
            return self._disambiguate(self._key_of(record), entry_type, fields, sha,
                                      source, verified, now)
        if record.get("verified") is not True:
            # 未核实条目 → 新导入的内容更新它（若新来源已核实，同时升级）
            self._replace_content(record, entry_type, fields, sha)
            self._append_source(record, source)
            if verified:
                self._mark_verified(record, source, now)
            self._record_conflict(record, diff, old_sha=old_sha, incoming_sha=sha,
                                  source=source, note="未核实条目被新导入替换",
                                  resolution="replaced-unverified", now=now)
            record["updated_at"] = now
            self.dirty = True
            return UpsertResult("updated", record["key"])
        # 已核实条目：**不静默覆盖**，记冲突留人裁决
        added = self._record_conflict(record, diff, old_sha=old_sha, incoming_sha=sha,
                                      source=source, note="已核实条目保留原字段",
                                      resolution="kept-existing", now=now)
        appended = self._append_source(record, source)
        if appended or added:
            record["updated_at"] = now
            self.dirty = True
        return UpsertResult("conflict", record["key"], conflict=added,
                            sources_added=1 if appended else 0)

    @staticmethod
    def _key_of(record: dict) -> str:
        return str(record.get("key") or "")

    # -- 导入 ---------------------------------------------------------------

    def import_bib_text(self, text: str, source: dict, *, verified: bool,
                        force: bool = False) -> ImportReport:
        """导入一份 .bib 文本；任何输入都不抛异常（坏条目记进 skipped）。"""
        report = ImportReport()
        entries, findings = bibparse.parse_entries(text or "")
        report.entries = len(entries)
        report.findings = len(findings)
        now = _now_iso()
        base_source = _clean_source(source, now)
        seen_keys: set[str] = set()
        for entry in entries:
            if entry.broken:
                report.skipped["条目结构损坏（花括号/引号不配平）"] += 1
                continue
            key = (entry.key or "").strip()
            if not key:
                report.skipped["没有 key"] += 1
                continue
            if key in seen_keys:
                # BibTeX 语义：同一份 bib 里重复的 key 只有第一条生效（往期 bib 里确实有
                # 这种情况，如 2024年第3期(76)/06 的 reference.bib 拼接了两版）。
                report.skipped["同一份 bib 里 key 重复"] += 1
                continue
            seen_keys.add(key)
            sha = content_sha256(entry.etype, entry.fields)
            entry_source = dict(base_source)
            entry_source["key"] = key
            entry_source["entry_sha256"] = sha
            record, processed_sha = self._processed_sha(entry_source)
            if record is not None and processed_sha == sha and not force:
                # 这条来源上一轮已经按**同样的内容**处理过（重跑种子库的常态）：
                # 直接记「已存在」并跳过，不再触碰库 —— 保证同输入重跑逐字节相同。
                report.add_result(UpsertResult("merged", str(record.get("key") or "")))
                continue
            result = self.import_entry(key, entry.etype, entry.fields, entry_source,
                                       verified=verified, force=force, now=now)
            report.add_result(result)
        return report

    # -- 查询 ---------------------------------------------------------------

    def records(self) -> list[dict]:
        """按 key 排序的全部记录（只读快照；统计/测试用，别直接改返回的 dict）。"""
        return [self._by_key[key] for key in sorted(self._by_key)]

    def get(self, key: str) -> dict | None:
        """按库内 key 精确取一条（不含 alias）。"""
        return self._by_key.get(key)

    def query_key(self, key: str) -> dict:
        """key 精确查询（先 canonical key，再 aliases；多条 alias 命中标 ambiguous）。"""
        wanted = str(key or "").strip()
        if not self._by_alias and any(
                isinstance(source, dict) and source.get("key")
                for record in self._by_key.values()
                for source in record.get("sources") or []):
            self.refresh_aliases()          # 刚导入还没 save 时也能按别名查
        hits: list[dict] = []
        record = self._by_key.get(wanted)
        if record is not None:
            hits.append({"key": record["key"], "hit": "key", "record": record})
        for record in self._by_alias.get(wanted, []):
            if record.get("key") == wanted:
                continue
            hits.append({"key": record["key"], "hit": "alias", "record": record})
        return {
            "query": {"key": wanted},
            "hits": hits,
            "count": len(hits),
            "ambiguous": len(hits) > 1,
        }

    def query_doi(self, doi: str) -> dict:
        """DOI 精确查询（大小写/前缀归一；`url` 里写 doi.org 链接的也认）。"""
        wanted = normalize_doi(doi)
        hits: list[dict] = []
        if wanted:
            for record in self._by_key.values():
                fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
                stored = normalize_doi(fields.get("doi") or "")
                if stored and stored == wanted:
                    hits.append({"key": record["key"], "hit": "doi", "record": record})
                    continue
                if not stored and _doi_from_url(fields.get("url")) == wanted:
                    hits.append({"key": record["key"], "hit": "url-doi", "record": record})
        hits.sort(key=lambda hit: (hit["record"].get("verified") is not True,
                                   hit["key"]))
        return {"query": {"doi": wanted or str(doi or "")}, "hits": hits,
                "count": len(hits), "ambiguous": len(hits) > 1}

    # -- 查库命中（第 129 条；convert.py ⑪ 核实链路）-------------------------

    def _ensure_indexes(self) -> None:
        """懒建 DOI / 作者姓 / 年份索引（只读；首次 match_reference 时建一次）。"""
        if self._doi_index is not None:
            return
        doi_index: dict[str, list[dict]] = {}
        block_surname: dict[str, list[dict]] = {}
        block_year: dict[str, list[dict]] = {}
        for record in self._by_key.values():
            fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
            entry_doi = normalize_doi(fields.get("doi") or "") or _doi_from_url(fields.get("url"))
            if entry_doi:
                doi_index.setdefault(entry_doi, []).append(record)
            surname = first_author_surname(fields.get("author") or fields.get("editor"))
            if surname:
                block_surname.setdefault(surname, []).append(record)
            year = entry_year(fields)
            if year:
                block_year.setdefault(year, []).append(record)
        title_match_index: dict[str, list[dict]] = {}
        for record in self._by_key.values():
            # 只有已核实记录才值得做「写法归一题名」索引（未核实记录不允许跳过检索/AI）
            if record.get("verified") is not True:
                continue
            fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
            for form in _match_title_forms(fields.get("title") or ""):
                title_match_index.setdefault(form, []).append(record)
        for bucket in list(doi_index.values()):
            bucket.sort(key=lambda r: (r.get("verified") is not True, str(r.get("key") or "")))
        self._doi_index = doi_index
        self._block_surname = block_surname
        self._block_year = block_year
        self._title_match_index = title_match_index

    def _candidate_records(self, surname: str, year: str) -> list[dict]:
        """题名模糊匹配用的候选池：先按（第一作者姓, 年份）收窄，收不到再退回年份/全库。

        收窄只是为了**快**（库 8000 条逐条 `difflib` 要 0.4 秒/条）；判据一字不少——
        收窄漏掉的（姓氏拼写不同）由下面的退回逻辑兜底。
        """
        self._ensure_indexes()
        assert self._block_surname is not None and self._block_year is not None
        if surname:
            pool = list(self._block_surname.get(surname, ()))
            if pool:
                if year:
                    year_pool = [r for r in pool
                                 if entry_year(r.get("fields") or {}) == year]
                    if year_pool:
                        return year_pool
                return pool
        if year:
            pool = list(self._block_year.get(year, ()))
            if pool:
                return pool
        return list(self._by_key.values())

    def _content_identity_record(self, canon: dict, entry_type: str) -> dict | None:
        """批61：与库内**已核实**记录的**内容指纹完全一致** → 同一条记录（保守判定）。

        内容指纹 = `content_sha256()`（逐字段规范化后的 SHA256，入库幂等判据同一份实现）。
        它比「题名 ≥0.98」更强：所有字段（作者/年份/出版社/期刊/卷页/题名…）都相等，
        不存在模糊空间；所以题名写法差异、甚至题名缺失（成品 bib 里旧的中文条目常有
        整条引文塞进 `author`、title 为空）的条目，只要内容与历期成品 bib 里那条完全
        相同，就是**同一条记录**（库里也没有第二份判别依据）。要求：
          · 库记录 `verified=true`；
          · 至少有 author/editor/title 之一作为身份锚（防止只有 type 的空条目对空）。
        """
        sha = content_sha256(entry_type, canon)
        record = self._by_content.get(sha)
        if record is None or record.get("verified") is not True:
            return None
        if not any(str((canon or {}).get(k) or "").strip()
                   for k in ("author", "editor", "title")):
            return None
        return record

    def _match_title_normalized(self, canon: dict) -> dict | None:
        """批61：题名写法归一二判（`hit="title-normalized"`）→ `{"record","witnesses"}`。

        用写法变体（全角标点/书名号/副标题/版次/尾部括号/「, in 书名」…）做 O(1) 索引，
        命中后按 `_match_title_witnesses` 保守复核（≥2 条见证、任何矛盾即否决）。
        只在已核实记录里找；`query_title`（GUI 查库）不走这里，口径不放宽。
        """
        title = str((canon or {}).get("title") or "").strip()
        if not title:
            return None
        self._ensure_indexes()
        assert self._title_match_index is not None
        best: tuple[int, dict, list[str]] | None = None
        for form in _match_title_forms(title):
            for record in self._title_match_index.get(form, ()):
                witnesses = _match_title_witnesses(canon, record)
                if not witnesses:
                    continue
                candidate = (len(witnesses), record, witnesses)
                if best is None or candidate[0] > best[0] or (
                        candidate[0] == best[0]
                        and str(record.get("key")) < str(best[1].get("key"))):
                    best = candidate
        if best is None:
            return None
        return {"record": best[1], "witnesses": best[2]}

    def match_reference(self, fields: dict, *, key: str = "",
                        entry_type: str = "") -> dict:
        """把**稿件里的一条 bib 条目**拿去查库，返回命中判定（第 129 条）。

        返回 dict（`status` ∈ `safe` / `candidate` / `miss`）：

          · `status="safe"`：可以**跳过检索 API 与 AI** —— DOI 精确命中已核实记录，
            或题名高置信 + 作者/年份相容 + `verified=true`，或译著/CJK 专项
            （`hit="translation"`：题名 ≥0.98 + 年份/出版社一致 + 责任标记归一后作者相容），
            或题名写法归一二判（`hit="title-normalized"`）、内容指纹一致
            （`hit="content-identical"`）；
          · `status="candidate"`：库里有线索但不够安全（key 命中 / 未核实 / 低置信），
            **仍要走检索+AI**；`hit` 说明是哪种线索；
          · `status="miss"`：库内没有线索。

        另带 `record_key`（库内的键，可能因撞车带 `-2` 后缀）、`record`（整条库记录）、
        `title_similarity` / `score` / `reason`（中文说明，写进日志/任务书），以及
        `field_diff`（稿件 vs 库记录的字段差异，**只提示、不改稿**）。
        """
        self._ensure_indexes()
        assert self._doi_index is not None
        canon = canonical_fields(fields or {})
        entry_key = str(key or "").strip()
        out: dict = {
            "status": HIT_MISS, "key": entry_key, "record_key": "", "hit": "",
            "title_similarity": 0.0, "score": 0.0, "field_diff": {},
            "reason": "引用库未命中", "record": None,
        }

        def _fill(record: dict, *, status: str, hit: str, score: float,
                  title_sim: float, reason: str) -> dict:
            out.update({
                "status": status, "hit": hit, "record": record,
                "record_key": str(record.get("key") or ""), "score": round(score, 4),
                "title_similarity": round(title_sim, 4),
                "field_diff": entry_field_diff(canon, record.get("fields") or {},
                                               entry_type=entry_type),
                "reason": reason,
            })
            return out

        # ① DOI 精确：DOI 是作品的唯一标识，命中已核实记录即可跳过检索+AI。
        entry_doi = normalize_doi(canon.get("doi") or "") or _doi_from_url(canon.get("url"))
        if entry_doi:
            doi_hits = self._doi_index.get(entry_doi) or []
            verified = [r for r in doi_hits if r.get("verified") is True]
            if verified:
                record = verified[0]
                sim = title_similarity(canon.get("title") or "",
                                       (record.get("fields") or {}).get("title") or "")
                return _fill(record, status=HIT_SAFE, hit="doi", score=1.0,
                             title_sim=sim,
                             reason=f"DOI `{entry_doi}` 精确命中库内已核实条目")
            if doi_hits:
                record = doi_hits[0]
                return _fill(record, status=HIT_CANDIDATE, hit="doi", score=0.5,
                             title_sim=title_similarity(
                                 canon.get("title") or "",
                                 (record.get("fields") or {}).get("title") or ""),
                             reason=f"DOI `{entry_doi}` 命中库内条目但**未核实**（仍走检索）")

        # ①b 批61：与库内已核实记录的**内容指纹完全一致** → 同一条记录（逐字段相等，
        #     比题名匹配更强；题名缺失/写法差异的条目靠这条，见 `_content_identity_record`）。
        content_record = self._content_identity_record(canon, entry_type)
        if content_record is not None:
            return _fill(content_record, status=HIT_SAFE, hit="content-identical",
                         score=1.0,
                         title_sim=title_similarity(
                             canon.get("title") or "",
                             (content_record.get("fields") or {}).get("title") or ""),
                         reason="全字段规范化内容与库内已核实记录完全一致"
                                "（内容指纹命中，即同一条记录）")

        # ② 题名高置信 + 第一作者姓/年份相容 + 已核实。
        title = canon.get("title") or ""
        if title:
            author = canon.get("author") or canon.get("editor") or ""
            year = entry_year(canon)
            surname = first_author_surname(author)
            scored_hits: list[dict] = []
            for record in self._candidate_records(surname, year):
                hit = _score_record_title(title, surname, year, record)
                if hit is not None:
                    scored_hits.append(hit)
            scored_hits.sort(key=lambda h: (-h["score"], not h["verified"], h["key"]))
            safe_hits = [h for h in scored_hits if h["verified"] and h["confidence"] == "high"]
            if safe_hits:
                best = safe_hits[0]
                return _fill(best["record"], status=HIT_SAFE, hit="title",
                             score=best["score"],
                             title_sim=best["title_similarity"],
                             reason=(f"题名高置信（相似度 {best['title_similarity']:.3f}）+ "
                                     "作者/年份相容，库内已核实"))
            # ②b 译著/CJK（批58）：题名几乎相同 + 年份/出版社双重见证 + 责任标记
            #     （`（著）/（译）`）归一后作者相容 → 安全命中。只救「作者写法差异」这一类；
            #     年份/出版社矛盾、作者完全不同的照旧留候选（下面 ②c 的候选分支）。
            translation_hits = [h for h in scored_hits if _translation_safe_hit(canon, h)]
            if translation_hits:
                best = sorted(translation_hits,
                              key=lambda h: (-h["title_similarity"], -h["score"], h["key"]))[0]
                return _fill(best["record"], status=HIT_SAFE, hit="translation",
                             score=best["score"],
                             title_sim=best["title_similarity"],
                             reason=(f"译著/CJK 作者写法差异：题名相似度 "
                                     f"{best['title_similarity']:.3f} + 年份/出版社一致，"
                                     "责任标记（著/译）归一后第一作者姓名相容，库内已核实"))
            # ②c 批61：题名写法归一二判（per-paper key 碰撞 / 题名写法差异）。只在
            #     题名变体≥0.98 且 ≥2 条见证、无矛盾证据时判 safe（见 `_match_title_witnesses`）。
            normalized = self._match_title_normalized(canon)
            if normalized is not None:
                record = normalized["record"]
                return _fill(record, status=HIT_SAFE, hit="title-normalized",
                             score=0.98,
                             title_sim=title_similarity(
                                 title, (record.get("fields") or {}).get("title") or ""),
                             reason=("题名写法归一后一致（全角/书名号/副标题/版次等）"
                                     " + 独立见证（"
                                     + "、".join(normalized["witnesses"][1:])
                                     + "），无矛盾证据，库内已核实"))
            if scored_hits:
                best = scored_hits[0]
                why = ("库内未核实" if not best["verified"]
                       else "题名置信度不足或作者/年份不符")
                return _fill(best["record"], status=HIT_CANDIDATE, hit="title",
                             score=best["score"],
                             title_sim=best["title_similarity"],
                             reason=f"题名有库内候选，但{why}（仍走检索）")

        # ③ key 命中只算候选（per-paper key 不保证同一文献）——仍走检索+AI。
        if entry_key:
            key_hits = self.query_key(entry_key)["hits"]
            if key_hits:
                record = key_hits[0]["record"]
                how = "库内 key（或别名）命中" if key_hits[0]["hit"] == "key" else "别名命中"
                return _fill(record, status=HIT_CANDIDATE, hit="key", score=0.4,
                             title_sim=title_similarity(
                                 canon.get("title") or "",
                                 (record.get("fields") or {}).get("title") or ""),
                             reason=f"{how}，但 BibTeX key 多为 per-paper，只作候选（仍走检索）")

        return out

    def query_title(self, title: str, *, author: str = "", year: str = "",
                    limit: int = 10, threshold: float = FUZZY_TITLE_THRESHOLD,
                    min_score: float = FUZZY_MIN_SCORE) -> dict:
        """题名（+第一作者姓+年份）模糊查询：返回带 `score`/`confidence` 的候选。

        `confidence="high"` 的充要条件：题名相似度 ≥ `threshold`（默认 0.90），
        且没提供的约束不算违背。低置信度**照实返回**（编辑自己判断），绝不冒充命中。
        """
        wanted_title = _squash(title)
        query = {"title": wanted_title, "author": _squash(author),
                 "year": entry_year({"year": year}) or _squash(year),
                 "threshold": threshold, "min_score": min_score}
        if not wanted_title:
            return {"query": query, "hits": [], "count": 0, "ambiguous": False,
                    "error": "题名为空"}
        surname = first_author_surname(author)
        wanted_year = entry_year({"year": year}) or ""
        weighted: list[tuple[float, float, dict]] = []
        for record in self._by_key.values():
            hit = _score_record_title(wanted_title, surname, wanted_year, record,
                                      threshold=threshold, min_score=min_score)
            if hit is not None:
                weighted.append((hit["score"], hit["title_similarity"], hit))
        weighted.sort(key=lambda item: (-item[0], not item[2]["verified"], item[2]["key"]))
        hits = [item[2] for item in weighted[:max(0, int(limit))]]
        return {
            "query": query,
            "hits": hits,
            "count": len(hits),
            "high": sum(1 for hit in hits if hit["confidence"] == "high"),
            "ambiguous": len(hits) > 1,
        }

    def query_advanced(self, spec: "SearchSpec") -> dict:
        """高级检索（B-259 P1；**纯读**，不改库/不建索引）。

        多条 `SearchTerm` 之间 AND；`any` 内部对五个文本字段取最佳命中（OR）。
        fuzzy 条件按 `spec.min_score` 过滤；总分 = 各 term 分的均分（4 位小数）。
        返回 `query/hits/count/total_matched/truncated`（键见 spec §2.8）。
        """
        if not isinstance(spec, SearchSpec):
            raise BibDBError(f"query_advanced 需要 SearchSpec：{spec!r}")
        plan: list[tuple[SearchTerm, str, tuple[str, int, int] | None]] = []
        for term in spec.terms:
            expression = (_parse_year_expression(term.value)
                          if term.field == "year" else None)
            if term.field == "doi":
                needle = normalize_doi(term.value)
            elif term.field in ("key", "alias"):
                needle = term.value.lower()
            elif term.field == "year":
                needle = ""
            else:
                needle = normalize_search_text(term.value)
            plan.append((term, needle, expression))
        strictness = sum(_SEARCH_MODE_RANK[term.mode] for term in spec.terms)
        total = 0
        hits: list[dict] = []
        # 按 key 升序遍历（records() 的口径）：同输入重复逐字节一致
        for key in sorted(self._by_key):
            record = self._by_key[key]
            cache: dict[str, list[str]] = {}
            scores: list[float] = []
            matched: list[dict] = []
            for term, needle, expression in plan:
                outcome = _search_record_term(term, needle, expression, record, cache)
                if outcome is None:
                    break
                score, items = outcome
                if term.mode == "fuzzy" and score < spec.min_score:
                    break                       # fuzzy 未达下限 = 该条不命中
                scores.append(score)
                matched.extend(items)
            else:
                total += 1
                hits.append(_search_hit(record, sum(scores) / len(scores), matched))
        hits.sort(key=lambda hit: _search_sort_key(hit, spec.sort, strictness))
        page = hits[:spec.limit]
        return {
            "query": {
                "terms": [{"field": term.field, "value": term.value, "mode": term.mode}
                          for term in spec.terms],
                "sort": spec.sort,
                "limit": spec.limit,
                "min_score": spec.min_score,
            },
            "hits": page,
            "count": len(page),
            "total_matched": total,
            "truncated": total > len(page),
        }

    # -- 统计 / lint --------------------------------------------------------

    def stats(self) -> dict:
        """库概况（CLI `stats` 与 README/报告的数字唯一来源）。"""
        records = [self._by_key[key] for key in sorted(self._by_key)]
        total = len(records)
        verified = sum(1 for record in records if record.get("verified") is True)
        methods: Counter[str] = Counter()
        types: Counter[str] = Counter()
        issues: set[str] = set()
        files: set[tuple] = set()
        source_count = 0
        alias_records = alias_total = 0
        conflict_records = conflict_total = 0
        created: list[str] = []
        updated: list[str] = []
        shas: list[str] = []
        for record in records:
            method = str(record.get("method") or "")
            if method:
                methods[method] += 1
            types[str(record.get("entry_type") or "")] += 1
            for source in record.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                source_count += 1
                if source.get("issue"):
                    issues.add(str(source["issue"]))
                if source.get("issue") or source.get("file"):
                    files.add((source.get("issue"), source.get("slug"), source.get("file")))
            aliases = record.get("aliases")
            if isinstance(aliases, list) and aliases:
                alias_records += 1
                alias_total += len(aliases)
            conflicts = record.get("conflicts")
            if isinstance(conflicts, list) and conflicts:
                conflict_records += 1
                conflict_total += len(conflicts)
            for name, bucket in (("created_at", created), ("updated_at", updated)):
                value = record.get(name)
                if isinstance(value, str) and value:
                    bucket.append(value)
            sha = record.get("content_sha256")
            if isinstance(sha, str) and sha:
                shas.append(sha)
        size = os.path.getsize(self.path) if os.path.isfile(self.path) else 0
        return {
            "path": self.path,
            "exists": os.path.isfile(self.path),
            "bytes": size,
            "sha256": db_sha256(self.path) if os.path.isfile(self.path) else "",
            "records": total,
            "verified": verified,
            "unverified": total - verified,
            "methods": dict(sorted(methods.items())),
            "entry_types": dict(types.most_common()),
            "sources": source_count,
            "issues": len(issues),
            "issue_list": sorted(issues),
            "files": len(files),
            "aliases": {"records": alias_records, "total": alias_total},
            "conflicts": {"records": conflict_records, "total": conflict_total},
            "duplicate_content": len(shas) - len(set(shas)),
            "created_range": [min(created), max(created)] if created else [],
            "updated_newest": max(updated) if updated else "",
            "dirty": self.dirty,
        }

    def _near_duplicates(self, records: list[dict], *, cap: int = 10) -> tuple[list, int]:
        """疑似重复条目：同一作品（题名+第一作者+年份）用了不同 key（lint 警告用）。

        分桶判据 =（第一作者姓, 年份），桶内两两算题名相似度；桶太大（>200 条）跳过
        ——那种桶只可能是异常数据，逐对算没有意义。
        """
        blocks: dict[tuple[str, str], list[dict]] = {}
        for record in records:
            fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
            surname = first_author_surname(fields.get("author") or fields.get("editor"))
            year = entry_year(fields)
            if not surname and not year:
                continue
            blocks.setdefault((surname, year), []).append(record)
        pairs: list[tuple[str, str]] = []
        for block in blocks.values():
            if len(block) < 2 or len(block) > 200:
                continue
            for i in range(len(block)):
                for j in range(i + 1, len(block)):
                    first, second = block[i], block[j]
                    if first.get("content_sha256") == second.get("content_sha256"):
                        continue
                    if works_match(first.get("fields") or {}, second.get("fields") or {}):
                        pairs.append((str(first.get("key")), str(second.get("key"))))
        pairs.sort()
        return pairs[:cap], len(pairs)

    def _health_ignore(self) -> dict:
        """本库的「已裁决为不同条目」sidecar（B-204；缺文件 = 空集，坏文件 fail-soft）。"""
        return load_health_ignore(self.path, self._by_key, self._by_alias)

    def lint(self, *, with_records: bool = False) -> dict:
        """库体检：重复 key / 字段冲突 / 缺来源 / verified 无核实记录 / 摘要不符 …
        返回 `{"ok": bool, "errors": [...], "warnings": [...], "warning_items": [...],
        "counts": {...}}`。

        `with_records=True`（B-188，CLI 的 `lint --with-records`）时给每个
        `warning_items[]` 额外加 `records`（受影响的 key 列表，最多 100 条；条目类
        warning 的 `records` 是记录 key，重复/近重复是 `A≈B`，alias 类是 alias 本身）。
        **不加 flag 时输出与旧版逐字节相同**（`records` 键根本不出现）。

        **errors 必须为 0** 才能当「库是好的」；warnings 是给人看的待办。

        B-143（批76）增补（**只加不改**，老键/老 warnings 字符串仍在）：
          · 「字段冲突待裁决」warning 只计 **pending**（判据 `conflict_is_pending`，
            与 `conflicts --json` 的 totals 同源），文本另给历史已裁决数；
          · `counts.conflicts` 保持老语义（**全量**冲突，向后兼容），新增
            `counts.conflicts_pending` / `counts.conflicts_resolved` 拆分（拆分相加 =
            全量；records 允许同一记录同时计入两侧——它既有待裁决也有已裁决）；
          · `warning_items` 是 warnings 的结构化镜像（大致同序）：每条
            `{code, registry, detail, action, count, keys_sample}`（冲突条目另带
            pending/resolved 拆分），供 GUI 按类下钻；`warnings` 字符串列表不变。

        B-204 增补（人工裁决「两条不同条目」→ 不再提示；sidecar 见 `load_health_ignore`）：
          · 读 `<库目录>/health-ignore.json`；缺文件 = 零变化、零新 warning；
          · `near_duplicates`：命中的 pair 剔除，`counts.near_duplicates` 只计未 ack；
            新增 `counts.near_duplicates_acknowledged`（被抑制 pair 数，仅审计）；
            全被 ack 时该 warning 整条不出现（`counts.warnings` 相应减 1）；
          · `duplicate_content`：一组内容相同的记录被 ack（keys 列全该组）→ 整组不提示；
          · sidecar 坏 / version 不对 / key 解析不了 → **不抛异常、errors 仍 0**，只多一条
            聚合 warning（`warning_items[].code="health_ignore"`，count = 问题数）。
        """
        errors: list[str] = list(self.problems)
        warnings: list[str] = []
        warning_items: list[dict] = []
        records = [record for record in self._records if isinstance(record, dict)]
        health = self._health_ignore()
        acked_pairs: set = health["near_duplicates"]
        acked_groups: set = health["duplicate_content"]
        canonical_keys = set(self._by_key)
        sha_owner: dict[str, list[str]] = {}
        alias_owner: dict[str, list[str]] = {}
        conflicts_records = conflicts_total = 0
        conflicts_pending_records = conflicts_pending_total = 0
        conflicts_resolved_records = conflicts_resolved_total = 0
        unverified = 0
        missing_title = missing_author = missing_year = bad_doi = 0
        missing_title_keys: list[str] = []
        missing_author_keys: list[str] = []
        missing_year_keys: list[str] = []
        bad_doi_keys: list[str] = []
        unverified_keys: list[str] = []
        conflict_pending_keys: list[str] = []
        unknown_types: Counter[str] = Counter()
        unknown_type_keys: dict[str, list[str]] = {}
        conflict_pending_rows: list[str] = []

        def _item(code: str, registry: str, detail: str, action: str, count: int,
                  keys_sample: "list[str] | None" = None,
                  records: "list[str] | None" = None) -> dict:
            """warnings 里同一句人读文本的结构化镜像（registry 分类固定给 GUI 下钻）。"""
            item = {"code": code, "registry": registry, "detail": detail,
                    "action": action, "count": count,
                    "keys_sample": list(keys_sample or [])[:5]}
            if with_records and records is not None:
                item["records"] = list(records)
            warning_items.append(item)
            return item
        for record in records:
            key = str(record.get("key") or "（无 key）")
            fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
            stored = record.get("content_sha256")
            if isinstance(stored, str) and _HEX64_RE.match(stored):
                actual = content_sha256(str(record.get("entry_type") or ""), fields)
                if actual != stored:
                    errors.append(f"{key}：content_sha256 与字段内容不一致（库被手工改过？）")
                else:
                    sha_owner.setdefault(actual, []).append(key)
            aliases = [alias for alias in (record.get("aliases") or [])
                       if isinstance(alias, str) and alias]
            if len(set(aliases)) != len(aliases):
                detail = f"{key}：aliases 里有重复项"
                warnings.append(detail)
                _item("alias_duplicate", "alias", detail,
                      "去掉记录内重复的 alias（同一别名只留一次）", 1, [key])
            for alias in aliases:
                alias_owner.setdefault(alias, []).append(key)
                if alias == key:
                    detail = f"{key}：aliases 里出现了自己的 key"
                    warnings.append(detail)
                    _item("alias_self", "alias", detail,
                          "删掉指向自身的 alias（canonical key 已可直接查）", 1, [key])
            conflicts = record.get("conflicts") or []
            if isinstance(conflicts, list) and conflicts:
                conflicts_records += 1
                conflicts_total += len(conflicts)
                pending_here = [c for c in conflicts if conflict_is_pending(c)]
                resolved_here = len(conflicts) - len(pending_here)
                if pending_here:
                    conflicts_pending_records += 1
                    conflicts_pending_total += len(pending_here)
                    conflict_pending_keys.append(key)
                    if len(conflict_pending_rows) < 5:
                        conflict_pending_rows.append(f"{key}（{len(pending_here)} 条）")
                if resolved_here:
                    conflicts_resolved_records += 1
                    conflicts_resolved_total += resolved_here
            if record.get("verified") is not True:
                unverified += 1
                unverified_keys.append(key)
            if not fields.get("title"):
                missing_title += 1
                missing_title_keys.append(key)
            if not (fields.get("author") or fields.get("editor")):
                missing_author += 1
                missing_author_keys.append(key)
            if not entry_year(fields):
                missing_year += 1
                missing_year_keys.append(key)
            doi = fields.get("doi") or ""
            if doi and not normalize_doi(doi):
                bad_doi += 1
                bad_doi_keys.append(key)
            etype = str(record.get("entry_type") or "").lower()
            if etype not in KNOWN_ENTRY_TYPES:
                unknown_types[etype] += 1
                unknown_type_keys.setdefault(etype, []).append(key)
        # 内容完全重复（正常 upsert 不会产生；只可能是手工编辑/旧版本遗留）
        # B-204：组内 key 升序集合与 sidecar 的 duplicate_content ack **完全一致**才整组
        # 不提示（2 条组 = pair；3 条组必须 ack 列全 3 个 key；部分 ack 不抑制）。
        for owners in sha_owner.values():
            if len(owners) > 1:
                if tuple(sorted(owners)) in acked_groups:
                    continue
                detail = f"内容完全相同的多条记录：{'、'.join(owners[:5])}"
                warnings.append(detail)
                _item("duplicate_content", "duplicates", detail,
                      "查库确认哪一条为准；重复记录合并属 P6 另卡，本页只报告、不自动合并",
                      len(owners), owners, owners)
        # alias 撞车：alias 指向多条记录，或 alias 与别的记录的 canonical key 相同
        for alias, owners in sorted(alias_owner.items()):
            unique = sorted(set(owners))
            if len(unique) > 1:
                detail = (f"alias `{alias}` 指向多条记录（{ '、'.join(unique[:5]) }）"
                          "：按 alias 查询会标 ambiguous")
                warnings.append(detail)
                _item("alias_ambiguous", "alias", detail,
                      "让 alias 只指向一条记录（查库确认后删掉多余的 alias）",
                      len(unique), unique, unique)
            elif alias in canonical_keys and alias not in unique:
                detail = f"alias `{alias}` 与另一条记录的 key 重名"
                warnings.append(detail)
                _item("alias_conflict", "alias", detail,
                      "改掉与 canonical key 重名的 alias（否则按 alias 查询有歧义）",
                      1, [alias], sorted(set(unique) | {alias}))
        # B-204：先取全量 pair，剔除 sidecar 已 ack 的（counts.near_duplicates 只计未 ack；
        # 被抑制数另记 counts.near_duplicates_acknowledged，仅审计）。全被 ack 时整条
        # warning 不出现（warnings 计数相应减 1），GUI「待处理 N」随之下降。
        raw_pairs, _raw_total = self._near_duplicates(records, cap=1_000_000)
        shown_pairs = [(first, second) for first, second in raw_pairs
                       if frozenset((first, second)) not in acked_pairs]
        pair_total = len(shown_pairs)
        near_acked = len(raw_pairs) - pair_total
        pairs = shown_pairs if with_records else shown_pairs[:10]
        if pair_total:
            sample = "；".join(f"{first}≈{second}" for first, second in pairs[:5])
            detail = (f"同一作品的多条记录（不同 key）{pair_total} 对：{sample}"
                      + ("……" if pair_total > 5 else "")
                      + "（经典文献在多期被重复引用是常态；查询会按题名/DOI 命中，"
                        "需要合并时另开批次做冲突裁决，不算错误）")
            warnings.append(detail)
            _item("near_duplicates", "near_duplicates", detail,
                  "经典文献跨期重复引用是常态；如需合并走 P6 另卡（本页只报告、不合并）",
                  pair_total, [f"{first}≈{second}" for first, second in pairs[:5]],
                  [f"{first}≈{second}" for first, second in pairs])
        if conflicts_records:
            # B-143：待裁决口径 = pending（与 conflicts --json 的 totals 同源）；
            # 历史已裁决只作审计留痕写在文本里，不再算「待办」。
            if conflicts_pending_total:
                detail = (f"字段冲突待裁决：{conflicts_pending_records} 条记录 / "
                          f"{conflicts_pending_total} 处差异"
                          f"（历史已裁决 {conflicts_resolved_records} 条记录 / "
                          f"{conflicts_resolved_total} 处差异"
                          + (f"；例：{'、'.join(conflict_pending_rows[:5])}"
                             if conflict_pending_rows else "") + "）")
                action = "到「待裁决」页逐条裁决（保留旧值 / 采用新值 / 待人工）"
            else:
                detail = (f"字段冲突：待裁决 0 条 / 历史已裁决 "
                          f"{conflicts_resolved_records} 条记录"
                          f"（{conflicts_resolved_total} 处差异，审计留痕，无需处理）")
                action = ("无需处理（历史已裁决留痕；可在「待裁决」页打开"
                          "「显示已裁决」查看）")
            warnings.append(detail)
            conflict_item = _item("conflict_pending", "conflicts", detail, action,
                                  conflicts_pending_total, conflict_pending_rows,
                                  conflict_pending_keys)
            conflict_item.update({
                "pending_records": conflicts_pending_records,
                "pending_total": conflicts_pending_total,
                "resolved_records": conflicts_resolved_records,
                "resolved_total": conflicts_resolved_total,
                "all_records": conflicts_records,
                "all_total": conflicts_total,
            })
        if unverified:
            detail = f"未核实条目 {unverified} 条（查询结果会标 verified=false）"
            warnings.append(detail)
            _item("unverified", "unverified", detail,
                  "按入库口径核实后重新入库（AI 结果不直接入库）", unverified,
                  unverified_keys[:5], unverified_keys)
        if missing_title:
            detail = f"缺 title 的条目 {missing_title} 条"
            warnings.append(detail)
            _item("missing_title", "missing_field", detail,
                  "回原稿/来源 bib 核对后补齐 title", missing_title, missing_title_keys,
                  missing_title_keys)
        if missing_author:
            detail = f"缺 author/editor 的条目 {missing_author} 条"
            warnings.append(detail)
            _item("missing_author", "missing_field", detail,
                  "回原稿/来源 bib 核对后补齐 author（或 editor）",
                  missing_author, missing_author_keys, missing_author_keys)
        if missing_year:
            detail = f"缺可用年份的条目 {missing_year} 条"
            warnings.append(detail)
            _item("missing_year", "missing_field", detail,
                  "回原稿/来源 bib 核对后补齐 year（或 date）",
                  missing_year, missing_year_keys, missing_year_keys)
        if bad_doi:
            detail = f"doi 字段归一后为空（疑似脏值）{bad_doi} 条"
            warnings.append(detail)
            _item("bad_doi", "other", detail,
                  "核对 DOI 是否写错/多了前缀；查库确认后修正字段", bad_doi, bad_doi_keys,
                  bad_doi_keys)
        for etype, count in unknown_types.most_common(10):
            detail = (f"不常见的 entry_type `{etype}`：{count} 条（可能是笔误，"
                      "见 bibcheck 的类型表）")
            warnings.append(detail)
            _item("unknown_entry_type", "unknown_entry_type", detail,
                  "按 bibcheck 的类型表改正 entry_type（多为笔误）",
                  count, unknown_type_keys.get(etype, []), unknown_type_keys.get(etype, []))
        # B-204：sidecar 有问题时**只加一条聚合 warning**（fail-soft：lint error 仍 0，
        # 坏条目忽略、好条目照常生效；未 ack 的告警照常输出）。
        problems = health["problems"]
        if problems:
            summary = "；".join(problems[:5]) + ("……" if len(problems) > 5 else "")
            detail = (f"已确认不同清单 `{os.path.basename(health['path'])}` 有 "
                      f"{len(problems)} 处问题（相关条目未生效）：{summary}")
            warnings.append(detail)
            _item("health_ignore", "other", detail,
                  "修正或删除库同目录的 health-ignore.json（缺文件 = 无效果；"
                  "文件坏了不阻塞 lint，但 ack 可能不生效）",
                  len(problems), [os.path.basename(health["path"])],
                  [os.path.basename(health["path"])])
        counts = {
            "records": len(self._by_key),
            "raw_lines": len(records),
            "verified": len(self._by_key) - unverified,
            "unverified": unverified,
            "errors": len(errors),
            "warnings": len(warnings),
            # 老键：**全量**冲突（向后兼容，语义不变）；待裁决/已裁决看下面两个新键
            "conflicts": {"records": conflicts_records, "total": conflicts_total},
            "conflicts_pending": {"records": conflicts_pending_records,
                                  "total": conflicts_pending_total},
            "conflicts_resolved": {"records": conflicts_resolved_records,
                                   "total": conflicts_resolved_total},
            "near_duplicates": pair_total,
            # B-204：被 sidecar 抑制的 pair 数（审计；不影响「待办」口径）
            "near_duplicates_acknowledged": near_acked,
        }
        return {"ok": not errors, "errors": errors[:200],
                "warnings": warnings[:100], "warning_items": warning_items[:100],
                "counts": counts}


# ── 稿件仓只读扫描（种子导入 / import-dir 共用） ────────────────────────────


class BibFile:
    """稿件仓里「某篇稿子的成品 bib」：终稿 `.tex` 实际引用的那一份。"""

    __slots__ = ("issue", "slug", "file", "path", "how", "warning")

    def __init__(self, issue: str, slug: str, file: str, path: str,
                 how: str, warning: str = "") -> None:
        self.issue = issue
        self.slug = slug
        self.file = file
        self.path = path
        self.how = how            # tex-ref / tex-ref-basename / fallback-author / …
        self.warning = warning

    @property
    def rel(self) -> str:
        return f"{self.slug}/{self.file}"


class IssueScan:
    """一期只读扫描的结果（成品期证据 + 要导入的 bib + 跳过的作者目录）。"""

    __slots__ = ("issue", "path", "finished", "evidence", "files", "skipped")

    def __init__(self, issue: str, path: str, finished: bool, evidence: str,
                 files: list[BibFile], skipped: list[dict]) -> None:
        self.issue = issue
        self.path = path
        self.finished = finished
        self.evidence = evidence
        self.files = files
        self.skipped = skipped


def _norm_token(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(text or "").lower())


def _primary_tex_files(author_dir: str, slug: str) -> list[str]:
    """优先取与作者目录同名的那份终稿 `.tex`（老期有多份辅助 tex 的干扰）。"""
    texs = sorted(
        name for name in os.listdir(author_dir)
        if name.lower().endswith(".tex") and os.path.isfile(os.path.join(author_dir, name))
    )
    tag = _norm_token(slug[2:]) if len(slug) > 2 else _norm_token(slug)
    matched = [name for name in texs if _norm_token(os.path.splitext(name)[0]) == tag]
    return matched or texs


def _bib_refs(tex_text: str) -> list[str]:
    """从 `.tex` 里取 `\\addbibresource{…}` / `\\bibliography{…}` 的文件名（补 `.bib`）。"""
    refs: list[str] = []
    for match in _BIBREF_RE.finditer(tex_text or ""):
        value = (match.group(1) or match.group(2) or "").strip()
        for one in re.split(r"[,\s]+", value):
            one = one.strip()
            if not one:
                continue
            refs.append(one if one.lower().endswith(".bib") else one + ".bib")
    return refs


def _select_bib(author_dir: str, slug: str) -> tuple[str | None, str, str]:
    """挑出这篇稿子的「成品 bib」。返回 `(文件名|None, 判据, 告警)`。

    判据优先级（前三级是**终稿 tex 自己指定**的那份，最可信）：
      1. `tex-ref`：tex 引用的文件名在作者目录里一字不差存在；
      2. `tex-ref-basename` / `tex-ref-case`：大小写/相对路径差异；
      3. `fallback-author`：bib 名与作者目录同名（老期 `Wenzhong Ma.bib` 这种）；
      4. `fallback-only` / `fallback-reference`：目录里只有一份 / 叫 `reference.bib`。
    找不到返回 `(None, 原因, "")`——**不猜**，让调用方如实跳过。
    """
    try:
        names = os.listdir(author_dir)
    except OSError:
        return None, "作者目录读不了", ""
    bibs = sorted(name for name in names
                  if name.lower().endswith(".bib")
                  and os.path.isfile(os.path.join(author_dir, name)))
    texs = _primary_tex_files(author_dir, slug)
    refs: list[str] = []
    for name in texs:
        try:
            with open(os.path.join(author_dir, name), encoding="utf-8",
                      errors="replace") as handle:
                refs.extend(_bib_refs(handle.read()))
        except OSError:
            continue
    if not bibs:
        return None, "作者目录里没有 .bib", ""
    for ref in refs:
        if os.path.isfile(os.path.join(author_dir, ref)):
            return ref, "tex-ref", ""
        base = os.path.basename(ref)
        if os.path.isfile(os.path.join(author_dir, base)):
            return base, "tex-ref-basename", ""
    lower_map = {name.lower(): name for name in bibs}
    for ref in refs:
        hit = lower_map.get(os.path.basename(ref).lower())
        if hit:
            return hit, "tex-ref-case", ""
    tag = _norm_token(slug[2:]) if len(slug) > 2 else _norm_token(slug)
    for name in bibs:
        if _norm_token(os.path.splitext(name)[0]) == tag:
            return name, "fallback-author", "终稿 tex 引用的 bib 不在目录里，按文件名匹配"
    if len(bibs) == 1:
        return bibs[0], "fallback-only", "终稿 tex 引用的 bib 不在目录里，取目录里唯一的 .bib"
    if "reference.bib" in bibs:
        return "reference.bib", "fallback-reference", \
            "终稿 tex 引用的 bib 不在目录里，退回 reference.bib（需人工确认）"
    return None, f"无法判定成品 bib（目录里有 {len(bibs)} 份：{', '.join(bibs)}）", ""


def _issue_is_finished(issue_path: str) -> tuple[bool, str]:
    """一期是否「成品期」（可入 verified）的证据：`000 …` 目录（中英文目录）存在。

    为什么用这条：本刊每期定稿后都会在期目录建 `000 Table of Contents/`（往期 27 期
    全部符合，命名规范见稿件仓 README）；没有目录的期还在排，bib 可能继续改，
    不能算「已出版期」。证据字符串原样进报告，便于人工复核。
    """
    try:
        names = os.listdir(issue_path)
    except OSError:
        return False, "期目录读不了"
    toc = next((name for name in sorted(names)
                if name.startswith("000") and os.path.isdir(
                    os.path.join(issue_path, name))), "")
    if toc:
        return True, toc
    return False, "缺 `000 …` 目录（未定稿/非成品期）"


def scan_manuscripts(root: str, *, through_year: int | None = None,
                     finished_only: bool = False) -> list[IssueScan]:
    """**只读**扫描稿件仓各期，返回每期要导入的成品 bib 与跳过原因。

    判据与 `slcn/paths.py` 同源（`20xx年第N期(…)` 是期目录；`NN ` 开头是文章目录），
    不写任何文件。`through_year` 只扫 ≤ 指定年份的期（对应「往期已出版」口径）；
    `finished_only` 跳过没有成品期证据的期。
    """
    if not os.path.isdir(root):
        raise BibDBError(f"稿件库根目录不存在或不是目录：{root}")
    scans: list[IssueScan] = []
    for name in sorted(os.listdir(root)):
        issue_path = os.path.join(root, name)
        if not (paths.ISSUE_RE.match(name) and os.path.isdir(issue_path)):
            continue
        year_match = _ISSUE_YEAR_RE.match(name)
        year = int(year_match.group(1)) if year_match else 0
        if through_year and year > through_year:
            continue
        finished, evidence = _issue_is_finished(issue_path)
        if finished_only and not finished:
            continue
        files: list[BibFile] = []
        skipped: list[dict] = []
        for slug in sorted(os.listdir(issue_path)):
            author_dir = os.path.join(issue_path, slug)
            if not (_AUTHOR_DIR_RE.match(slug) and os.path.isdir(author_dir)):
                continue
            selected, how, warning = _select_bib(author_dir, slug)
            if selected is None:
                skipped.append({"slug": slug, "reason": how})
                continue
            files.append(BibFile(name, slug, selected, os.path.join(author_dir, selected),
                                 how, warning))
        scans.append(IssueScan(name, issue_path, finished, evidence, files, skipped))
    return scans


def read_bib_text(path: str) -> tuple[str, str]:
    """读一份 `.bib` 的文本（UTF-8），返回 `(文本, 告警)`。

    往期 bib 里存在**源文件本身带 U+FFFD 替换字符**的文件（2026年第4期(89)/01
    Fenrong_Liu 的 reference.bib 就有 2 个）——它不是编码错误，但字段确实可能不完整，
    所以如实告警、照常导入。真正非 UTF-8 的文件用替换字符读入并告警（不因一个坏字节
    丢掉整份参考文献）。
    """
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except UnicodeDecodeError:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        return text, "不是 UTF-8 编码，已按替换字符读入（字段可能不完整）"
    except OSError as exc:
        raise BibDBError(f"读不了 bib：{path}（{exc}）") from exc
    if "\ufffd" in text:
        return text, "含 U+FFFD 替换字符（源文件可能曾被错误编码保存过，字段可能不完整）"
    return text, ""


def import_manuscripts(store: Store, root: str, *, force: bool = False,
                       through_year: int | None = None,
                       finished_only: bool = False) -> dict:
    """只读扫描稿件仓 → 入 `store`（内存；是否落盘由调用方 `store.save()` 决定）。

    成品期（有 `000 …` 目录）的 bib 入 `verified=true / published-bib`；未定稿期只入
    `verified=false`。返回逐期明细 + 总计，供种子工具/CLI 打印。
    """
    scans = scan_manuscripts(root, through_year=through_year,
                             finished_only=finished_only)
    total = ImportReport()
    issue_rows: list[dict] = []
    warnings: list[str] = []
    for scan in scans:
        issue_report = ImportReport()
        file_rows: list[dict] = []
        for bib_file in scan.files:
            try:
                text, text_warning = read_bib_text(bib_file.path)
            except BibDBError as exc:
                issue_report.skipped["文件读不了"] += 1
                warnings.append(f"{scan.issue}/{bib_file.rel}：{exc}")
                continue
            if text_warning:
                warnings.append(f"{scan.issue}/{bib_file.rel}：{text_warning}")
            if bib_file.warning:
                warnings.append(f"{scan.issue}/{bib_file.rel}：{bib_file.warning}")
            source = {
                "kind": KIND_PUBLISHED if scan.finished else KIND_IMPORTED,
                "issue": scan.issue,
                "slug": bib_file.slug,
                "file": bib_file.file,
                "imported_at": _now_iso(),
            }
            report = store.import_bib_text(text, source, verified=scan.finished,
                                           force=force)
            issue_report.merge(report)
            file_rows.append({"slug": bib_file.slug, "file": bib_file.file,
                              "how": bib_file.how, "report": report.summary()})
        total.merge(issue_report)
        issue_rows.append({
            "issue": scan.issue,
            "path": scan.path,
            "finished": scan.finished,
            "evidence": scan.evidence,
            "files": file_rows,
            "skipped": scan.skipped,
            "report": issue_report.summary(),
        })
    return {"root": os.path.abspath(root), "issues": issue_rows,
            "total": total.summary(), "warnings": warnings}
