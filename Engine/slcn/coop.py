#!/usr/bin/env python3
r"""第五十四批：AI 协同初排（两段式）的纯逻辑。

用户 2026-10-19 拍板的工作流（**本模块只做引擎侧，不碰 GUI**）：

    ① `convert.py --verify-refs --no-ai-merge --coop-taskbook-only`
       → 初排产物与《AI 核实任务书》写进暂存目录（默认 `<out>/<stem>_待核/`），
         **不写最终交付目录**；任务书末尾多一节「初排低置信项」。
    ② 编辑把任务书交给任意 chat 模型（豆包/千问/DeepSeek…，免费、无 API），
       把模型回复粘贴/保存成文件或目录。**回填有两部分**（第三节完整 BibTeX +
       附一 JSON）；chat 客户端拆成多个文件时，第五十六批起可一次传多个路径。
    ③ `convert.py --coop-replies <FILE|DIR> [--coop-replies <FILE|DIR> …] [--coop-apply]`
       → 解析回填文本、校验、并入文献字段；`--coop-apply` 时才应用低置信建议；
         最后才编译（`--clean` 时清中间文件）并**原子 promote** 到最终交付目录。

本模块的设计纪律：

* **纯函数优先**：解析、收集、指纹、预算、字段改写、promote 都能脱离 convert.py 单测；
* **不猜键**：回填文本里出现的文献键必须在任务书/状态的白名单里，否则只进
  `unknown` 报告，不生成回复；
* **拒绝比误收好**：重复键、截断、span 不在原文里、枚举值不认识——整条拒绝并写原因；
* **默认不改产物**：低置信建议默认只写 `…_AI建议.md/.jsonl`；只有显式 `--coop-apply`
  才动 `reference.bib` / 输出 `.tex`，并且沿用 `aibib.merge()` 的四条硬保障（key 不变 / 恰好一条 /
  不增删条目 / 未返回的条目逐字节不动）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from slcn.paths import rel as _rel

SCHEMA_VERSION = 1

#: 暂存目录里的状态文件（**不**随 promote 进入最终交付目录）。
STATE_NAME = "_AI协同状态.json"
#: 最终交付目录里的协同记录（回填数/应用数/拒绝数/待人工）。
RECORD_NAME = "_AI协同记录.md"
#: 低置信建议的默认落点（后缀由调用方补 `.md` / `.jsonl`）。
SUGGESTIONS_SUFFIX = "_AI建议"

SECTION_TITLE = "## 附一、初排低置信项（AI 协同核实；机器可读 ID）"
#: 第五十六批：任务书末尾的「回填说明」——把「回填=两部分」讲清楚，防止模型/编辑
#: 只交 JSON（真实用户 2026-10-19 22:00 首测：只导入 json → 文献回填 0/9）。
SECTION_BACKFILL_TITLE = "## 附二、回填说明（AI 协同初排专用）"

VERDICTS = ("confirmed", "corrected", "unresolved")
BIB_FIELDS = (
    "type", "author", "editor", "title", "journal", "booktitle", "publisher",
    "address", "edition", "volume", "number", "pages", "year", "userf",
)
META_FIELDS = ("value",)
#: 第五十七批：--coop-apply 的应用目标（记录/jsonl 用；BIB 沿用旧口径）。
TARGET_BIB = "reference.bib"
TARGET_TEX = ".tex"
#: 第五十七批：编辑侧出版信息字段（META-<macro> 的应用白名单之一）。
EDITORIAL_TEX_FIELDS = ("myvolnumber", "myissnumber", "mypubyear",
                        "myfirstpage", "mylastpage", "mypages",
                        "receiveddate", "myeditor", "mygrants")
#: 第五十七批：只有「纯引用脚注没匹配上」允许提升为正文 \parencite（窄口径）。
FN_APPLICABLE_SUBKINDS = ("citation_unmatched",)
#: FN 项的 field 白名单：citation_unmatched 多一个 match_key（locator 放在同一 JSON 里）。
FN_FIELDS_MATCH = ("note", "match_key")
#: 页码线索：evidence 含这些而回复没给 locator → 拒绝（不丢页码）。
_PAGE_HINT_RE = re.compile(r"(第\s*\d+\s*[页頁]|[pP]{1,2}\.\s*~?\s*\d+|\d+\s*[-–—]\s*\d+\s*[页頁])")
_LOCATOR_RE = re.compile(r"^\d{1,4}(?:\s*[-–—~]\s*\d{1,4})?$")
#: 旧状态（没有 ph1 初排取值）时允许覆盖的模板默认值/占位（绝不覆盖真实编辑值）。
_TEMPLATE_PLACEHOLDERS = {"x", "xxxx", "xxxx-xx-xx", "null", "1", "10"}
#: 可直接定位到 tex 脚注的 FN 项（FN-<fid>；FN-W 只有 warning 文本，无法定位）。
_FN_FID_RE = re.compile(r"^FN-(\d+)$")

#: 低置信项种类 → 中文标签（任务书里显示）。
KIND_LABEL = {
    "footnote": "脚注/引用未匹配",
    "bib_line": "文献区原文未能解析",
    "bib_entry": "文献条目缺字段",
    "metadata": "元数据缺失",
}

#: C 类 warning 的关键词（build.py 的中文文案是唯一出处，这里只做匹配）。
_CC_KEYS = ("混合型脚注", "纯引用脚注", "mixed_manual", "citation_unmatched")
_FID_RE = re.compile(r"#(\d+)\s*「([^」]*)」")
_EDITORIAL_MISSING_RE = re.compile(r"以下编辑侧字段需人工填写[：:]\s*([^\n]+)")
_UNDETECTED_RE = re.compile(
    r"(?:请手工填写或用[^\n：:]*覆盖|未识别\s*\d+\s*个字段[^\n：:]*)[：:]\s*([^\n]+)")
_META_SOURCE_RE = re.compile(r"元数据源不存在[^\n]*")


# ── 基础小工具 ────────────────────────────────────────────────────────────


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _clip(text: str, limit: int) -> str:
    """压成一行并截断；超出加 `…`（**标注**不静默丢）。"""
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: max(1, limit - 1)] + "…"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: str, text: str) -> None:
    """同目录临时名 + `os.replace`：写一半崩掉也不会留下半个 JSON/记录。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(path)}.coop-tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_json(path: str, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _canonical(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ── 外部 chat 回复：读取文件/目录 ─────────────────────────────────────────


_REPLY_EXTS = (".md", ".markdown", ".txt", ".bib", ".json", ".jsonl", ".yaml", ".yml")


def _is_coop_artifact_name(name: str) -> bool:
    """状态/记录/任务书/建议/全文分析报告不是「回填回复」：目录输入时跳过，避免自己吃自己。"""
    return (name in (STATE_NAME, RECORD_NAME)
            or name.endswith("_文献核实.md")
            or SUGGESTIONS_SUFFIX in name
            or ANALYSIS_SUFFIX in name)


def _real_key(path: str) -> str:
    """同一真实路径的判定键（`realpath`，解析符号链接/`.`/`..`）。"""
    try:
        return os.path.realpath(os.path.abspath(path))
    except OSError:
        return os.path.abspath(path)


def dedupe_reply_paths(paths: Iterable[str]) -> list[str]:
    """按命令行顺序去重（**同一真实路径只读一次**；空串跳过）。"""
    out: list[str] = []
    seen: set[str] = set()
    for p in paths or []:
        s = str(p or "").strip()
        if not s:
            continue
        key = _real_key(s)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def format_reply_paths(paths: str | Iterable[str]) -> str:
    """给日志/报错用的一行路径清单（单个路径时与批55 的 str 打印一致）。"""
    if isinstance(paths, os.PathLike):
        return os.fspath(paths)
    if isinstance(paths, str):
        return paths
    return "、".join(str(p) for p in paths or [])


def _load_one_reply_path(path: str) -> list[tuple[str, str]]:
    """读取**一个**回填路径（批55 的行为，逐字节保持不变）。"""
    p = os.path.abspath(path)
    if os.path.isfile(p):
        files = [p]
    elif os.path.isdir(p):
        files = sorted(
            os.path.join(p, n) for n in os.listdir(p)
            if os.path.isfile(os.path.join(p, n))
            and n.lower().endswith(_REPLY_EXTS)
            and not n.startswith(".")
            and not _is_coop_artifact_name(n)
        )
    else:
        raise FileNotFoundError(f"回填路径不存在：{p}")
    out: list[tuple[str, str]] = []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                out.append((_rel(f, p if os.path.isdir(p) else os.path.dirname(p)), fh.read()))
        except OSError as e:
            out.append((os.path.basename(f), f"[无法读取：{type(e).__name__}: {e}]"))
    return out


def load_reply_texts(path: str | Iterable[str]) -> list[tuple[str, str]]:
    """读取回填**一个或多个**路径，返回 `[(显示名, 文本)]`。

    · 单个路径（str / 长度 1 的序列）→ 与批55 **完全一致**（顺序、显示名、digest 都不变）；
    · 多个路径 → 按**命令行顺序**读入；目录仍按文件名排序；目录里读出的文件显示名带
      「目录名/相对路径」前缀（如 `replies-a/x.md`），让多目录下的同名文件不混；
      显式给的文件仍只用文件名（与批55 单路径一致）；
    · 同一**真实路径**（`realpath`）出现多次 → 只读一次，顺序以第一次出现为准。
    目录里只收常见文本后缀；读不出的文件**不静默跳过**——返回一个空文本条目，
    由解析报告写明「读不了」，免得编辑以为自己的回填被吃掉了。
    """
    if isinstance(path, os.PathLike):
        path = os.fspath(path)
    if isinstance(path, str):
        return _load_one_reply_path(path)
    items = dedupe_reply_paths(path)
    if len(items) <= 1:
        return _load_one_reply_path(items[0]) if items else []
    out: list[tuple[str, str]] = []
    for p in items:
        ab = os.path.abspath(p)
        if not os.path.isdir(ab):
            # 显式给的文件：显示名就是文件名（与批55 单路径一致）。
            out += _load_one_reply_path(p)
            continue
        # 目录：显示名统一带「目录名/相对路径」（多目录下同名文件能分清来源）。
        label = os.path.basename(ab) or ab
        for name, text in _load_one_reply_path(p):
            out.append((f"{label}/{name}", text))
    return out


# ── 文献键回复：解析 ──────────────────────────────────────────────────────

_BIB_HEAD_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z]+)\s*\{\s*([^,\s{}]+)\s*,")


def _escaped(text: str, i: int) -> bool:
    n = 0
    j = i - 1
    while j >= 0 and text[j] == "\\":
        n += 1
        j -= 1
    return n % 2 == 1


@dataclass
class BibOccurrence:
    etype: str
    key: str
    text: str
    closed: bool
    start: int


def parse_bib_blocks(text: str) -> list[BibOccurrence]:
    """扫出文本里所有 BibTeX 条目（花括号配平；不闭合 = 截断）。

    只按 `@type{key,` 头定位——键来自**回复自身**，调用方必须再用任务书白名单过滤
    （`parse_replies` 负责），所以这里不是「凭模型输出猜键」。
    """
    out: list[BibOccurrence] = []
    for m in _BIB_HEAD_RE.finditer(text):
        open_at = text.find("{", m.start())
        if open_at < 0:
            continue
        depth = 0
        end = -1
        for i in range(open_at, len(text)):
            ch = text[i]
            if _escaped(text, i):
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            out.append(BibOccurrence(m.group(1), m.group(2), text[m.start():], False, m.start()))
        else:
            out.append(BibOccurrence(m.group(1), m.group(2), text[m.start():end], True, m.start()))
    return out


def _leading_comments(text: str, start: int, limit: int = 8) -> str:
    """条目上方紧邻的 `%` 注释（AI 常把 `% 来源：…` 写在这里）。

    只收连续注释行（允许中间夹空行/围栏行），最多 `limit` 行；遇到正文就停。
    """
    lines = text[:start].splitlines()
    got: list[str] = []
    j = len(lines) - 1
    while j >= 0 and len(got) < limit:
        s = lines[j].strip()
        if s.startswith("%"):
            got.append(lines[j].rstrip())
            j -= 1
        elif not s or s.startswith("```") or s.startswith("~~~"):
            j -= 1
        else:
            break
    if not got:
        return ""
    return "\n".join(reversed(got)) + "\n"


@dataclass
class ParseReport:
    """外部 chat 回复的解析报告：每键一个状态。"""

    replies: dict[str, str] = field(default_factory=dict)      # key -> 含注释的条目文本
    rejected: dict[str, str] = field(default_factory=dict)     # key -> 中文原因
    missing: list[str] = field(default_factory=list)           # 任务书有、回复里没找到
    unknown: list[str] = field(default_factory=list)           # 回复里有、任务书没有
    status: dict[str, str] = field(default_factory=dict)       # key -> ok/rejected/missing
    sources: list[str] = field(default_factory=list)

    @property
    def n_ok(self) -> int:
        return len(self.replies)

    def summary(self) -> str:
        bits = [f"解析到 {self.n_ok} 条", f"拒绝 {len(self.rejected)} 条",
                f"未回填 {len(self.missing)} 条"]
        if self.unknown:
            bits.append(f"任务书外的键 {len(self.unknown)} 个")
        return "，".join(bits)

    def as_dict(self) -> dict:
        return {
            "replies": sorted(self.replies), "rejected": dict(self.rejected),
            "missing": list(self.missing), "unknown": list(self.unknown),
            "status": dict(self.status), "sources": list(self.sources),
        }


def parse_replies(texts: Iterable[tuple[str, str]], expected_keys: Iterable[str]) -> ParseReport:
    """按**任务书的文献键白名单**切分外部 chat 回复。

    · 多文件/目录、乱序都可以：每个 `@type{key,` 条目自己说明键；
    · 同一键出现多次（哪怕内容相同）→ **整条拒绝**并报告「重复键」；
    · 花括号不闭合 → 拒绝「截断」；闭合但 `aibib.parse_ai_reply()` 认不出 → 拒绝「残缺」；
    · 白名单外的键只进 `unknown`，**不生成回复**。
    """
    expected = list(dict.fromkeys(str(k) for k in expected_keys if str(k).strip()))
    exp = set(expected)
    occ: dict[str, list[tuple[str, str]]] = {k: [] for k in expected}
    unknown: list[str] = []
    report = ParseReport(sources=[name for name, _t in texts])

    for _name, text in texts:
        for block in parse_bib_blocks(text or ""):
            key = block.key
            if key not in exp:
                if key not in unknown:
                    unknown.append(key)
                continue
            if not block.closed:
                occ[key].append(("bad", "条目被截断（花括号不闭合）"))
                continue
            reply = _leading_comments(text, block.start) + block.text
            if aibib_parse(key, reply) is None:
                occ[key].append(("bad", "无法解析为完整的 BibTeX 条目（可能残缺或格式坏）"))
            else:
                occ[key].append(("ok", reply))

    for key in expected:
        items = occ[key]
        if not items:
            report.status[key] = "missing"
            report.missing.append(key)
        elif len(items) > 1:
            report.status[key] = "rejected"
            report.rejected[key] = (
                f"重复键：同一条回复里出现 {len(items)} 次（工具拒绝猜哪一条是对的；"
                "请只保留一份后重新回填）"
            )
        elif items[0][0] == "ok":
            report.status[key] = "ok"
            report.replies[key] = items[0][1]
        else:
            report.status[key] = "rejected"
            report.rejected[key] = items[0][1]
    report.unknown = unknown
    return report


def aibib_parse(key: str, reply: str) -> str | None:
    """薄封装：延迟 import，避免 coop 被 import 时拉起整条 bib 解析链。"""
    from slcn import aibib

    return aibib.parse_ai_reply(key, reply)


# ── 低置信项：收集 / 渲染 / 解析 / 校验 ───────────────────────────────────


def _new_item(id_: str, kind: str, evidence: str, need: str, *, fields: list[str],
              key: str = "", title: str = "", subkind: str = "",
              target: str = "", macro: str = "", current: str = "") -> dict:
    """一项低置信项。第五十七批增三个**附加**键（旧状态没有就按空串处理）：

    · `subkind`：C 类子类（`citation_unmatched` / `mixed_manual`），决定 FN 能否应用；
    · `target`：可应用的产物（`reference.bib` / `.tex` / 空=只记录）；
    · `macro`：META 项要写入的输出 tex `\\my*` 宏名（LABEL 项由 ph1 映射而来）。
    """
    return {
        "id": id_, "kind": kind, "title": title or KIND_LABEL.get(kind, kind),
        "evidence": str(evidence or ""), "need": need,
        "fields": list(fields), "key": key, "status": "pending",
        "subkind": subkind, "target": target, "macro": macro, "current": current,
    }


def collect_low_confidence(*, warnings: Iterable[str] = (),
                           missing_fields: Iterable[str] = (),
                           raw_ref_lines: Iterable[str] = (),
                           bib_text: str = "",
                           label_targets: dict[str, str] | None = None,
                           current_fields: dict[str, str] | None = None,
                           max_items: int = 80) -> list[dict]:
    """从转换结果里收集「机器可读 ID」的低置信项（纯函数、顺序确定）。

    来源（对应任务书新增的一节）：
      · C 类：`mixed_manual` / `citation_unmatched`（build.py 汇总的 warning 里带
        `#<fid>「<原文片段>」`）→ `FN-<fid>`；
      · 文献解析失败行：bib 一条都没解析出来但有 `raw_ref_lines` → `BIB-LINE-NN`；
      · 已有条目缺必填字段（author/editor、title、year）→ `BIB-<key>`；
      · 元数据缺失/占位：`META-<字段>` 或 `META-LABEL-N`。

    每项都带 `evidence`（原文片段）与 `fields`（允许 AI 回复的 field 白名单），
    解析时 **span 必须是 evidence 的子串**——只抽不编。

    第五十七批附加：`label_targets` = {人话标签: 输出 tex 的 \\my* 宏名}（来自
    `BuildResult.undetected_fields`），把 `META-LABEL-N` 映射到可写字段；C 类项带
    `subkind`，只有 citation_unmatched 的 FN 项允许 `match_key` 应用。
    """
    items: dict[str, dict] = {}
    order: list[str] = []

    def add(item: dict) -> None:
        if item["id"] in items:
            # 同一项从两条 warning 里出现：只补「需要确认」的文字，证据保持第一次。
            if item["need"] and item["need"] not in items[item["id"]]["need"]:
                items[item["id"]]["need"] += "；" + item["need"]
            return
        if len(items) >= max_items:
            return
        items[item["id"]] = item
        order.append(item["id"])

    warnings = [str(w) for w in warnings or []]
    missing_fields = [str(f) for f in missing_fields or [] if str(f).strip()]
    raw_ref_lines = [str(x).strip() for x in raw_ref_lines or [] if str(x).strip()]
    bib_text = str(bib_text or "")
    label_targets = {str(k): str(v) for k, v in (label_targets or {}).items()}
    current_fields = {str(k): str(v) for k, v in (current_fields or {}).items()}

    # ① C 类脚注/引用
    seq = 0
    for w in warnings:
        if not any(k in w for k in _CC_KEYS):
            continue
        hits = _FID_RE.findall(w)
        # build.py 的两条 warning 文案（唯一出处）：纯引用脚注…「无法匹配完整条目」/
        # 混合型脚注…「说明文字+引用」。第五十七批修正：旧判据找「未匹配」，而 build 的
        # 纯引用脚注文案是「无法匹配完整条目」，于是 citation_unmatched 全被判成
        # mixed_manual、FN 永远不可应用——这里按真实文案判，并保留旧关键词兜底。
        kind = ("citation_unmatched"
                if ("纯引用脚注" in w or "citation_unmatched" in w or "未匹配" in w)
                else "mixed_manual")
        fn_fields = list(FN_FIELDS_MATCH) if kind in FN_APPLICABLE_SUBKINDS else ["note"]
        if hits:
            for fid, snippet in hits:
                add(_new_item(
                    f"FN-{fid}", "footnote", snippet,
                    f"核对该脚注引用（{kind}）与正文/文献表是否对应；只抽取，不补造。",
                    fields=fn_fields, title=f"脚注 #{fid}（{kind}）",
                    subkind=kind, target=TARGET_TEX))
        else:
            seq += 1
            add(_new_item(
                f"FN-W{seq}", "footnote", _clip(w, 400),
                f"核对该脚注/引用问题（{kind}）；原文片段见任务书。",
                fields=["note"], title=f"脚注问题（{kind}）",
                subkind=kind, target=TARGET_TEX))

    # ② 文献条目缺字段（可安全落到 reference.bib 的一类）
    entries = parse_bib_blocks(bib_text)
    by_key: dict[str, BibOccurrence] = {}
    for e in entries:
        if e.closed and e.key not in by_key:
            by_key[e.key] = e
    for key, block in by_key.items():
        fields: list[str] = []
        low = block.text.lower()
        if not re.search(r"(?mi)^\s*(author|editor)\s*=", low):
            fields.append("author")
        if not re.search(r"(?mi)^\s*title\s*=", low):
            fields.append("title")
        if not re.search(r"(?mi)^\s*year\s*=", low):
            fields.append("year")
        if not fields:
            continue
        add(_new_item(
            f"BIB-{key}", "bib_entry", block.text,
            "该条目缺必填字段（" + "、".join(fields) + "）；请只根据原文/来源补，查不到就回 unresolved。",
            fields=fields, key=key, title=f"文献条目 `{key}` 缺字段",
            target=TARGET_BIB))

    # ③ 文献区解析失败行（整片没解析出条目时逐行交给 AI 结构化）
    if not by_key and raw_ref_lines:
        for i, line in enumerate(raw_ref_lines[:max_items], start=1):
            add(_new_item(
                f"BIB-LINE-{i:02d}", "bib_line", line,
                "这一行未能解析成 BibTeX；请只抽取原文已有信息，不要补造卷/期/页码。",
                fields=list(BIB_FIELDS), title=f"文献区第 {i} 行"))

    # ④ 元数据缺失 / 占位
    for f in missing_fields:
        add(_new_item(
            f"META-{f}", "metadata", f,
            f"元数据字段 `{f}` 缺失；请给出值，或回 unresolved。",
            fields=[f] + list(META_FIELDS), title=f"元数据 `{f}`",
            target=TARGET_TEX, macro=f, current=current_fields.get(f, "")))

    label_seq = 0
    for w in warnings:
        m = _EDITORIAL_MISSING_RE.search(w)
        if m:
            for f in re.split(r"[、,，]\s*", m.group(1)):
                f = f.strip()
                if f and f not in missing_fields:
                    add(_new_item(
                        f"META-{f}", "metadata", w,
                        f"编辑侧字段 `{f}` 需人工填写；请给出值，或回 unresolved。",
                        fields=[f] + list(META_FIELDS), title=f"元数据 `{f}`",
                        target=TARGET_TEX, macro=f, current=current_fields.get(f, "")))
        m = _UNDETECTED_RE.search(w)
        if m:
            for label in re.split(r"[、,，]\s*", m.group(1)):
                label = label.strip()
                if not label:
                    continue
                label_seq += 1
                add(_new_item(
                    f"META-LABEL-{label_seq}", "metadata", label,
                    f"字段「{label}」未识别，产物里已写占位符；请给出值，或回 unresolved。",
                    fields=["value"], title=f"元数据「{label}」",
                    target=TARGET_TEX, macro=label_targets.get(label, ""),
                    current=current_fields.get(label_targets.get(label, ""), "")))
        if _META_SOURCE_RE.search(w):
            add(_new_item(
                "META-元数据源", "metadata", w,
                "元数据源缺失，正文信息可能留空；请人工确认，或回 unresolved。",
                fields=["value"], title="元数据源缺失"))

    return [items[i] for i in order]


def _code_span(text: str, limit: int = 400) -> str:
    s = _clip(text, limit).replace("`", "ˋ")
    return f"`{s}`"


def render_low_confidence_section(items: Iterable[dict]) -> str:
    """任务书里的「附一、初排低置信项」：ID + 原文片段 + **固定 JSON 回复格式**。"""
    items = list(items)
    L = ["", "---", "", SECTION_TITLE, "",
         "> 这一节是**机器可读**的：每项都有稳定 ID（`FN-…` / `BIB-…` / `META-…`）。",
         "> 请**只依据下面给出的原文片段**判断；**不要补造**任何卷/期/页码/作者。",
         "> 每条回复一个 JSON 对象（可以放在 ```json 围栏里，也可以直接贴）：",
         ">",
         "> `{\"id\": \"FN-1\", \"verdict\": \"confirmed|corrected|unresolved\", "
         "\"field\": \"\", \"value\": \"\", \"span\": \"原文片段里逐字连续的一段\", \"note\": \"\"}`",
         ">",
         "> · `verdict=corrected` 时 `field` 与 `value` 必填，且 `field` 必须是该项列出的允许值；",
         "> · **同一个 ID 可以分别回多个字段**：每个字段一个 JSON 对象"
         "（同一 `(id, field)` 只能回一次，`verdict` 必须一致）；",
         "> · `span` **必须逐字抄自该项「原文片段」**（工具会做子串校验；对不上整条拒绝）。",
         "> · `META-…` 项：`field` 用该项列出的宏名或 `value`，`value` 填要写进产物的值；",
         "> · `FN-…` 纯引用脚注项：如能确定对应文献键且该键在第三节里，"
         "回 `{\"field\": \"match_key\", \"value\": \"<键>\", \"locator\": \"<页码>\"}`"
         "（原文片段含页码时必须给 `locator`）；拿不准就回 `unresolved`，**不要猜**。"]
    if not items:
        L += ["", "本次初排**没有发现**需要外部核实的低置信项。", ""]
        return "\n".join(L)

    L += ["", f"共 **{len(items)}** 项：", ""]
    for it in items:
        L.append(f"### `{it['id']}` — {KIND_LABEL.get(it['kind'], it['kind'])}")
        L.append("")
        evidence = str(it.get("evidence") or "")
        if "\n" in evidence:
            # 多行条目（BIB-*）必须保留换行：AI 要能**逐字**引用其中一行作为 span。
            shown = evidence if len(evidence) <= 800 else evidence[:799] + "…"
            L += ["- 原文片段：", "", "  ```text"]
            L += ["  " + ln for ln in shown.splitlines()]
            L += ["  ```"]
        else:
            L.append(f"- 原文片段：{_code_span(evidence)}")
        L.append(f"- 需要确认：{it.get('need', '')}")
        fields = "、".join(f"`{f}`" for f in it.get("fields") or [])
        L.append(f"- 允许的 field：{fields or '（无）'}")
        L.append("")
    return "\n".join(L)


def render_backfill_section() -> str:
    """任务书里的「附二、回填说明」：回填**两部分**都要交（第五十六批）。

    为什么必须有这一节：真实用户首测（2026-10-19 22:00）里，DeepSeek 按任务书把回答
    拆成了「JSON 低置信回复 + 完整 BibTeX」两个文件；旧 GUI 只能导入一个路径，用户只导
    了 JSON → 文献回填 0/9。任务书本身要先把「回填=两部分、可放同一回复、多文件也行」
    写死，模型和编辑才不会只交一半。
    """
    return "\n".join([
        "", "---", "", SECTION_BACKFILL_TITLE, "",
        "> 本次回填**包含两部分，请都交回来**（只交一部分会白跑一轮核实）：",
        "",
        "1. **文献条目部分**：按第三节规范输出的**完整 BibTeX**——把所有应核条目"
        "（包括没有改动的）写进**一个**代码块（`bibtex` 围栏，格式同第三节示例）。",
        "2. **低置信项部分**：按「附一、初排低置信项」规定格式回复的 **JSON**——"
        "每项一个 JSON 对象（`verdict` / `field` / `value` / `span`），"
        "可以放在 `json` 围栏里，也可以直接贴。",
        "",
        "- **推荐**：两部分写在**同一条回复**里（同一段文字即可，前后顺序不限）。",
        "- 如果 chat 客户端把回复拆成**多个文件**（例如一个 `.json` + 一个 `.txt`/`.md`），"
        "**不影响使用**：本工具支持一次导入**多个文件/目录**"
        "（也可以把文件放进同一个目录一次导入；GUI 还支持从剪贴板导入），"
        "会把它们一起读取、一起解析。",
        "- **两块都要交**：只交 JSON 会让 `reference.bib` 一条都不并入（文献回填 0 条），"
        "本轮文献核实等于白做；只交 BibTeX 则附一的低置信项得不到回复。",
        "- **拿不准就回 `unresolved`**：附一里任何一项没有把握，请回 `verdict=unresolved`"
        "（不要猜值）；只有 `corrected` 且通过校验的建议才会在 `--coop-apply` 时写进产物。",
        "- 导入后若要把低置信建议也落到产物：运行第二段时勾选「一键修改产物」"
        "（`--coop-apply`）；不勾选时建议只写进 `…_AI建议.md` / `.jsonl`，不改产物。",
        "",
    ])


# ── 第五十九批：全文 AI 核查（通道①：任务书附三；默认关）────────────────


#: 两档模式：`context`（引用/脚注锚点窗口，默认推荐）/ `full`（正文全文）。
FULLTEXT_MODES = ("context", "full")
#: 两档的「超长提示阈值」（批63 用户 2026-10-20 拍板：chat 通道**不再截断**，
#: 这两个数只用来在任务书里提示「正文较长」；`coop.estimate_tokens` 按 3 字符/1 token 估算）。
FULLTEXT_LIMITS = {"context": 40000, "full": 80000}
#: `context` 档：每个锚点前后各取多少字符。
FULLTEXT_WINDOW = 300
#: 合并窗口时允许的最大间隙（小于它的两个窗口并成一段，避免碎片化）。
FULLTEXT_MERGE_GAP = 120
#: 任务书「附三」小节标题（与附一/附二同一层级）。
FULLTEXT_TITLE = "## 附三、正文全文/引用上下文（AI 协同初排专用）"

#: 引用/脚注锚点：\parencite/\citep/\autocite/\textcite/\footnote 等。
_FULLTEXT_ANCHOR_RE = re.compile(r"\\(?:[A-Za-z]*cite[A-Za-z]*|footnote)\b")
#: 整行 `%` 注释（本工具写的说明行）不发给外部 AI，省体积。
_TEX_COMMENT_LINE_RE = re.compile(r"(?m)^[ \t]*%[^\n]*\n?")


def extract_fulltext(body_text: str, mode: str = "context") -> dict:
    """从正文文本提取「附三」内容（第五十九批；默认关，只有 --coop-fulltext 才调用）。

    · `context`：在 `\\parencite`/`\\footnote` 等锚点前后各取 `FULLTEXT_WINDOW` 字，
      相邻/重叠窗口合并；`FULLTEXT_LIMITS["context"]` 只作**超长提示阈值**。
    · `full`：正文全文；`FULLTEXT_LIMITS["full"]` 只作**超长提示阈值**。
    · **批63（用户 2026-10-20 拍板）起不截断**：整篇交给外部 chat，只保留体积估算与
      「超长提示」；`truncated` 键保留（恒 False）以免破坏 state schema。
    整行 `%` 注释先剥掉；围栏 ``` 做替换，防止破坏任务书的 ```text 代码块。
    返回 `{mode, text, chars, truncated, tokens_est, orig_chars, windows, over_limit}`。
    """
    if mode not in FULLTEXT_MODES:
        raise ValueError(f"未知全文模式：{mode!r}（只支持 {'/'.join(FULLTEXT_MODES)}）")
    body = _TEX_COMMENT_LINE_RE.sub("", str(body_text or ""))
    body = body.replace("```", "ˋˋˋ")
    over_at = FULLTEXT_LIMITS[mode]        # 批63：只用于「超长提示」，不再截断
    windows = 0
    if mode == "full" or not body:
        text = body
        windows = 1 if body else 0
    else:
        spans: list[tuple[int, int]] = []
        for m in _FULLTEXT_ANCHOR_RE.finditer(body):
            spans.append((max(0, m.start() - FULLTEXT_WINDOW),
                          min(len(body), m.end() + FULLTEXT_WINDOW)))
        merged: list[tuple[int, int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1] + FULLTEXT_MERGE_GAP:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        windows = len(merged)
        if merged:
            # 窗口之间用「…」标记断开（提示这是节选，不是连续正文）。
            text = "\n\n…\n\n".join(body[a:b] for a, b in merged)
        else:
            # 正文里没有任何引用/脚注锚点：退回全文（仍受上限约束），不产出空附三。
            text = body
    orig = len(text)
    # 批63（用户 2026-10-20 拍板）：chat 通道**不设字符上限**——整篇交给外部 chat。
    # `truncated` 恒 False（键保留：state schema 不破坏）；超过阈值只给体积估算/超长提示。
    over_limit = orig > over_at
    return {"mode": mode, "text": text, "chars": len(text),
            "truncated": False, "tokens_est": estimate_tokens(text),
            "orig_chars": orig, "windows": windows, "over_limit": over_limit}


def render_fulltext_section(info: dict) -> str:
    """任务书「附三、正文全文/引用上下文」：正文 + 用途/边界说明（第二段任务书专用）。"""
    mode = str(info.get("mode") or "context")
    text = str(info.get("text") or "")
    chars = int(info.get("chars") or len(text))
    tokens = int(info.get("tokens_est") or 0)
    if mode == "full":
        mode_note = "正文全文"
    else:
        mode_note = ("正文节选：引用/脚注锚点前后各 %d 字窗口合并（共 %d 段）"
                     % (FULLTEXT_WINDOW, int(info.get("windows") or 0)))
    long_hint = ""
    if info.get("over_limit"):
        long_hint = ("\n> ℹ️ 正文较长（原 %d 字，超过 %d 字的常规提示阈值）："
                     "本次**不截断、整篇发送**；若外部 chat 有长度限制，请自行分段粘贴。"
                     % (int(info.get("orig_chars") or 0),
                        FULLTEXT_LIMITS.get(mode, chars)))
    return "\n".join([
        "", "---", "", FULLTEXT_TITLE, "",
        "> 下面是本次稿件正文（" + mode_note + f"；{chars} 字，约 {tokens} tokens）。",
        "> 用途：核对「附一、初排低置信项」里的引用/脚注/元数据时提供上下文。",
        "> **请只按附一规定的 JSON 格式回复**（文献条目仍按第三节 BibTeX 格式）；",
        "> **不要改写、复述或补写正文**，也不要把正文内容当成修改建议。",
        "> 拿不准的项请回 `unresolved`（不要猜）。" + long_hint,
        "", "```text", text, "```", "",
    ])


# ── 批74（B-133）：附四「初排分析」（首版严格 report-only：只报告、不写产物）────
#
# 用户 2026-10-21 晚拍板（T1 报告 §6）：①附四首版默认 **report-only**；②自动白名单只
# 3 类机械项（本卡不实现）；③**禁止改写正文文字**。所以本模块只做「指令渲染 + 解析
# + 校验 + 落报告」，**不接任何 apply 通道**：附四条目绝不写 `reference.bib` / `.tex`，
# 也不产生 `.ai前`。全部新逻辑在 `--coop-fulltext` / state.analysis 门后（默认零变化）。

#: 附四机器可读 schema：解析器据此与附一 JSON 对象分区。
ANALYSIS_SCHEMA = "slcn.fulltext.v1"
#: 任务书「附四」小节标题（与附一/附二/附三同一层级）。
ANALYSIS_TITLE = "## 附四、初排分析（只报告、不改写正文）"
#: 附四报告的默认落点（后缀由调用方补 `.md` / `.jsonl`）。
ANALYSIS_SUFFIX = "_AI全文分析"
#: 报告落盘名（工具产物；重跑/`--clean` 必须认它，否则第二次运行会被自己拦下）。
ANALYSIS_REPORT_NAMES = (f"{ANALYSIS_SUFFIX}.md", f"{ANALYSIS_SUFFIX}.jsonl")
#: 上限：每篇总条数 / 每 kind 条数（超出部分拒绝并写明原因）。
ANALYSIS_MAX_TOTAL = 30
ANALYSIS_MAX_PER_KIND = 10
#: 值 / 说明长度上限（超限整条拒绝，不静默截断）。
ANALYSIS_VALUE_LIMIT = 1200
ANALYSIS_NOTE_LIMIT = 300
#: 附四对象的三元组白名单。
ANALYSIS_TARGETS = ("reference.bib", "tex", "report")
ANALYSIS_ACTIONS = ("fill_field", "link_cite", "fill_placeholder", "rename_key", "report")
ANALYSIS_CONFIDENCES = ("high", "medium", "low")
#: 11 类 kind → 允许的 action / target（T1 报告附录 C）。**未知 kind 记录不丢弃**。
ANALYSIS_KIND_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "cite_uncovered": (("link_cite", "report"), ("tex",)),
    "dangling_marker": (("report",), ("tex", "report")),
    "cite_missing_entry": (("report",), ("report",)),
    "uncited_entry": (("report",), ("report",)),
    "footnote_cite_link": (("link_cite",), ("tex",)),
    "bib_field_fill": (("fill_field",), ("reference.bib",)),
    "bib_key_rename": (("rename_key",), ("report", "tex")),
    "meta_fill": (("fill_field",), ("tex",)),
    "placeholder_fill": (("fill_placeholder",), ("tex",)),
    "style_fix": (("report",), ("report",)),
    "lowconf_review": (("report",), ("report",)),
}
#: kind → 中文标签（报告/任务书显示）。
ANALYSIS_KIND_LABEL = {
    "cite_uncovered": "正文/脚注引用标记未转换或漏链",
    "dangling_marker": "成品悬空引用标记",
    "cite_missing_entry": "引用但文末清单缺条",
    "uncited_entry": "清单条目未被正文引用",
    "footnote_cite_link": "脚注→引用转换建议",
    "bib_field_fill": "参考文献字段/体例补全",
    "bib_key_rename": "CJK 译名 bib key 规范",
    "meta_fill": "元数据补全",
    "placeholder_fill": "表题/图题/缺引文出处占位符",
    "style_fix": "体例混用建议",
    "lowconf_review": "低置信项复核",
}
#: 将来「默认自动应用」的 3 类机械项（用户已拍板、**本卡不实现**；只写进 state 留痕）。
ANALYSIS_FUTURE_WHITELIST = ("bib_field_fill", "meta_fill", "footnote_cite_link")

# ── 批75（B-139）：附四输入补齐——「脚注清单」/「占位符清单」（只在有数据时出现）──
#
# 背景（T1 报告 §2 建议项）：附四只给正文（附三），AI 的 `locator.footnote_id` /
# `locator.placeholder` 没有定位依据、只能从正文猜。两块数据引擎本来就有：
#   · 脚注 fid + 文本摘要：初排 warning 里的 `#<fid>「<片段>」`（`_FID_RE`，与
#     `collect_low_confidence()` 同一判据）与已收下的 `FN-<fid>` 低置信项；
#   · 占位符 类型 + 位置：`BuildResult.undetected_fields`（`(人话标签, 模板 \my* 宏)`），
#     对应产物 `.tex` 里 `【未识别：<标签>，请手工填写】`（模板唯一出处 `build.PLACEHOLDER_TMPL`）。
# **不新写解析器、不落正文**；只进任务书（默认零变化），report-only 边界不变。

#: 附四输入清单（附三之后 / 附四之前；两块任一非空时出现）。
ANALYSIS_INPUTS_TITLE = "## 附三·补充、附四输入清单（脚注 / 占位符；定位用）"
ANALYSIS_FOOTNOTE_TITLE = "### 脚注清单（fid + 文本摘要）"
ANALYSIS_PLACEHOLDER_TITLE = "### 占位符清单（类型 + 位置）"
#: 脚注清单每条文本摘要的截断长度（只作定位线索，不是全文）。
ANALYSIS_FOOTNOTE_CLIP = 80
#: 产物里的未识别占位符：`【未识别：<标签>，请手工填写】`（识别用；写入逻辑仍在 build.py）。
PLACEHOLDER_MARK_RE = re.compile(r"【未识别：([^，】]{1,40})，请手工填写】")


def collect_analysis_inputs(*, warnings: Iterable[str] = (),
                            low_items: Iterable[dict] = (),
                            placeholders: Iterable[tuple[str, str]] = (),
                            ) -> tuple[list[dict], list[dict]]:
    """批75（B-139）：收集附四输入清单 → `(脚注清单, 占位符清单)`（纯函数、顺序确定）。

    · 脚注：从 `warnings` 的 `#<fid>「<片段>」`（与 `collect_low_confidence()` 同一判据）
      与 `low_items` 的 `FN-<fid>`（`evidence` 为片段）收集，按 fid 升序去重；
    · 占位符：`placeholders` = `BuildResult.undetected_fields` 的 `(人话标签, 模板宏)` 对。
    两块都可能为空——调用方只在**有数据**时渲染（默认路径不出现、逐字节不变）。
    """
    found: dict[int, dict] = {}
    for w in warnings or ():
        for fid_s, snippet in _FID_RE.findall(str(w or "")):
            try:
                fid = int(fid_s)
            except (TypeError, ValueError):
                continue
            if fid in found:
                continue
            found[fid] = {"fid": fid, "id": f"FN-{fid}",
                          "text": _clip(snippet, ANALYSIS_FOOTNOTE_CLIP)}
    for it in low_items or ():
        if not isinstance(it, dict):
            continue
        id_ = str(it.get("id") or "")
        m = _FN_FID_RE.match(id_)
        if not m:
            continue
        fid = int(m.group(1))
        if fid in found:
            continue
        found[fid] = {"fid": fid, "id": id_,
                      "text": _clip(it.get("evidence") or "", ANALYSIS_FOOTNOTE_CLIP)}
    footnotes = [found[k] for k in sorted(found)]

    ph: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for pair in placeholders or ():
        try:
            label, macro = pair
        except (TypeError, ValueError):
            continue
        label = _clip(label, 40).strip()
        macro = str(macro or "").strip()
        if not label or (label, macro) in seen:
            continue
        seen.add((label, macro))
        ph.append({"label": label, "macro": macro})
    return footnotes, ph


def _table_cell(text: str, limit: int = 120) -> str:
    """表格单元格：压一行 + 转义 `|`（markdown 表格里竖线会把列切坏）。"""
    return _clip(text, limit).replace("|", "\\|")


def render_analysis_inputs_section(*, footnotes: Iterable[dict] = (),
                                   placeholders: Iterable[dict] = ()) -> str:
    """批75（B-139）：附三之后、附四之前的两块输入清单。

    **两块都为空时返回空串**（调用方据此不追加任何内容）；只有一块有数据时只出那一块。
    """
    footnotes = list(footnotes or ())
    placeholders = list(placeholders or ())
    if not footnotes and not placeholders:
        return ""
    L = ["", "---", "", ANALYSIS_INPUTS_TITLE, "",
         "> 下面两块是引擎手里**现成的结构化输入**（初排 warning / 产物占位符），",
         "> 供「附四」的 `locator.footnote_id` / `locator.placeholder` 定位。",
         "> **不是**要求你改动它们；清单里的文字也不要当成待改的正文。", ""]
    if footnotes:
        L += [ANALYSIS_FOOTNOTE_TITLE, "",
              "| fid | 文本摘要 | 关联附一 ID |", "|---|---|---|"]
        for fn in footnotes:
            L.append(f"| `{fn.get('fid', '')}` | {_table_cell(fn.get('text', ''))} | "
                     f"`{fn.get('id', '')}` |")
        L.append("")
    if placeholders:
        L += [ANALYSIS_PLACEHOLDER_TITLE, "",
              "| 类型（未识别字段） | 位置（产物宏） |", "|---|---|"]
        for ph_ in placeholders:
            macro = str(ph_.get("macro") or "")
            shown = ("\\" + macro) if macro and not macro.startswith("\\") else macro
            L.append(f"| {_table_cell(ph_.get('label', ''), 40)} | "
                     f"{_code_span(shown, 60) if shown else '（无宏名）'} |")
        L.append("")
    return "\n".join(L)


def render_analysis_section() -> str:
    """任务书「附四、初排分析」：七项检查指令 + `slcn.fulltext.v1` schema + 硬约束。

    **只出现在 `--coop-fulltext` 且附三写出之后**（默认不加时任务书逐字节不变）。
    本版 report-only：分析建议只落 `…_AI全文分析.md/.jsonl`，不写任何产物。
    """
    kind_rows = [
        "| kind | 含义 | 允许的 action | 允许的 target |",
        "|---|---|---|---|",
    ]
    for kind, (actions, targets) in ANALYSIS_KIND_RULES.items():
        kind_rows.append(
            f"| `{kind}` | {ANALYSIS_KIND_LABEL.get(kind, kind)} | "
            f"{' / '.join(f'`{a}`' for a in actions)} | "
            f"{' / '.join(f'`{t}`' for t in targets)} |")
    schema_example = """{
  "schema": "slcn.fulltext.v1",
  "id": "AN-0001",
  "kind": "footnote_cite_link",
  "target": "tex",
  "action": "link_cite",
  "field": "",
  "value": "\\\\parencite{Gilbert2002}",
  "locator": {"footnote_id": 5},
  "anchor": "参见[12]。",
  "evidence": "……\\\\footnote{参见[12]。}……",
  "confidence": "high",
  "source": "源稿脚注",
  "note": "裸编号 [12] 未转引用；对应文末清单第 12 条"
}"""
    return "\n".join([
        "", "---", "", ANALYSIS_TITLE, "",
        "> 本节要求你以**初排检查器**的身份，结合「附三」里的正文，给出**只报告、不改写正文**的",
        "> 分析建议。**本版是 report-only**：这些建议**不会**写进 `reference.bib` 或输出 `.tex`——",
        "> 软件只把它们整理成 `…_AI全文分析.md` / `.jsonl` 供编辑人工裁决；",
        "> **不要为了让建议生效而改写正文**（正文文字一个都不许改）。",
        "",
        "### 一、请逐项检查（七项）",
        "",
        "1. **正文/脚注引用覆盖与漏链**：编号 `[n]`、作者-年式、混合体例；逐处列出未转换/悬空的"
        "引用标记与上下文（例：脚注里的裸 `参见[12]。`）。",
        "2. **脚注→引用转换建议**：纯引用脚注/裸编号可映射到哪个文献键、页码定位如何保留"
        "（例：脚注裸 `[12]` → `Gilbert2002`）。",
        "3. **参考文献字段/体例补全**：只报「任务书/源稿里能逐字找到依据」的缺失字段与体例混用。",
        "4. **元数据补全**：作者/单位/邮箱/题名/摘要/关键词——只填有依据的值，**不要猜**。",
        "5. **表题/图题/缺引文出处占位符**：逐处指出并给依据。",
        "6. **CJK 译名 bib key 规范**：优先源稿给出的英文作者名，缺才音译；不要凭软件音译定 key。",
        "7. **低置信项复核**：对「附一」里 `unresolved` / 被拒绝 / 拿不准的项补充意见"
        "（**可以只写 `note`、不带 `evidence`**）。",
        "",
        "### 二、回复格式（`slcn.fulltext.v1`；一条建议一个 JSON 对象）",
        "",
        "```json", schema_example, "```",
        "",
        "| 字段 | 必填 | 说明 |",
        "|---|---|---|",
        f"| `schema` | 是 | 固定 `{ANALYSIS_SCHEMA}`（本节对象必须带；解析器据此与附一 JSON 分区） |",
        "| `id` | 是 | `AN-####` 这类编号；同一条回复内**不可重复** |",
        "| `kind` | 是 | 下表 11 类之一；不在表内的会记入「未知 kind」（不采用） |",
        "| `target` | 是 | `reference.bib` / `tex` / `report`；须与 kind 匹配 |",
        "| `action` | 是 | `fill_field` / `link_cite` / `fill_placeholder` / `rename_key` / "
        "`report`；须与 kind 匹配 |",
        "| `field` | `fill_field` 时必填 | bib 字段名或 `\\my*` 宏名 |",
        f"| `value` | 写入类建议必填 | 建议写的值；≤{ANALYSIS_VALUE_LIMIT} 字符 |",
        "| `locator` | 否 | 结构定位：`key` / `macro` / `footnote_id` / `placeholder` / "
        "`occurrence` |",
        "| `anchor` | 否 | 目标文件里的逐字片段；**本版只报告，可留空** |",
        "| `evidence` | 是（`lowconf_review` 可用 `note` 代替） | **任务书内容里的逐字连续片段**"
        "（工具做子串校验；对不上整条拒绝） |",
        "| `confidence` | 是 | `high` / `medium` / `low`；`low` 的进「待人工」 |",
        "| `source` | 否 | `源稿正文` / `源稿脚注` / `文末清单` / `附一低置信项` / `引用库` |",
        f"| `note` | 否 | ≤{ANALYSIS_NOTE_LIMIT} 字符 |",
        "",
        *kind_rows,
        "",
        "### 三、硬约束",
        "",
        "- **本节的分析建议只按上面附四 JSON 格式回复**；文献条目仍按第三节 BibTeX、"
        "「附一」各项仍按附一 JSON（三块互不混用）。",
        "- **不得改写、复述或补写正文**：只做「发现 + 建议」，正文/脚注的文字一个都不改。",
        "- 每条必须带**逐字 `evidence`**（从本任务书**附四之前**的内容里**逐字**复制一段"
        "连续原文——附三正文 / 附一原文 / 附二附三说明均可；**本节的 schema 示例与指令不算**。"
        "换行/空白差异可容忍，文字有改动即整条拒绝）；`kind=lowconf_review` 可用 `note` 代替。",
        "- **拿不准**就写 `\"confidence\": \"low\"`，或回 `unresolved`——**不要猜**。",
        f"- 每篇**总条数 ≤{ANALYSIS_MAX_TOTAL}**、**每 kind ≤{ANALYSIS_MAX_PER_KIND}**；"
        "超出部分会被拒绝。",
        "- 同一 `id` 只一条；同一 `(kind, target, locator)` 只一条（重复/冲突的涉事条全部拒绝）。",
        "- **本版不写产物**：不要假设工具会自动修改 `reference.bib` 或 `.tex`；"
        "你的建议只供编辑人工裁决。",
        "",
    ])


@dataclass
class FulltextAnalysisReport:
    """附四解析/校验结果（report-only：只报告，不产生任何产物改动）。"""

    #: 从回复里捞出的附四 JSON 对象总数（含被拒/未知 kind）。
    total: int = 0
    #: 校验通过且 confidence 不为 low 的条目（报告一、已接受；jsonl 每行一条）。
    accepted: list[dict] = field(default_factory=list)
    #: 被拒绝的条目：{"id","kind","reason"}（报告二、逐条原因）。
    rejected: list[dict] = field(default_factory=list)
    #: 校验通过但 `unresolved` / `confidence=low` 的条目（报告三、待人工）。
    manual: list[dict] = field(default_factory=list)
    #: 未知 kind 条目：{"id","kind"}（报告四、记录不丢弃）。
    unknown_kinds: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        return "，".join([
            f"接受 {len(self.accepted)} 条",
            f"拒绝 {len(self.rejected)} 条",
            f"待人工 {len(self.manual)} 条",
            f"未知 kind {len(self.unknown_kinds)} 个",
        ])


def _analysis_id(obj: dict, idx: int) -> str:
    id_ = str(obj.get("id") or "").strip()
    return id_ or f"（第 {idx} 条无 id）"


def _analysis_locator_key(obj: dict) -> str:
    """`(kind, target, locator)` 冲突检测用的 locator 规范化键；空 locator 返回空串。"""
    loc = obj.get("locator")
    if loc in (None, "", {}, []):
        return ""
    if isinstance(loc, dict):
        try:
            return _canonical(loc)
        except (TypeError, ValueError):
            return _squeeze(str(loc))
    return _squeeze(str(loc))


def _analysis_is_unresolved(obj: dict) -> bool:
    """`unresolved` 标记（拿不准）：`verdict/status/resolution=unresolved` 或 `unresolved:true`。"""
    for key in ("verdict", "status", "resolution"):
        if str(obj.get(key) or "").strip().lower() == "unresolved":
            return True
    return bool(obj.get("unresolved"))


def _validate_analysis(obj: dict, *, evidence_squeezed: str, idx: int) -> tuple[bool, dict, str]:
    """校验一条附四对象；返回 `(ok, 规范化记录, 拒绝原因)`。"""
    id_ = str(obj.get("id") or "").strip()
    if not id_:
        return False, {}, "缺少必填字段 `id`（每条附四建议都要有 `AN-*` 编号）——整条拒绝"
    kind = str(obj.get("kind") or "").strip()
    target = str(obj.get("target") or "").strip()
    action = str(obj.get("action") or "").strip()
    rules = ANALYSIS_KIND_RULES[kind]
    actions, targets = rules
    if target not in ANALYSIS_TARGETS:
        return False, {}, (f"target `{target or '空'}` 不在允许集合 "
                           f"{list(ANALYSIS_TARGETS)} 内——整条拒绝")
    if action not in ANALYSIS_ACTIONS:
        return False, {}, (f"action `{action or '空'}` 不在允许集合 "
                           f"{list(ANALYSIS_ACTIONS)} 内——整条拒绝")
    if target not in targets or action not in actions:
        return False, {}, (
            f"kind=`{kind}` 的动作/目标不匹配：允许 action={list(actions)}、"
            f"target={list(targets)}（收到 action=`{action}`、target=`{target}`）——整条拒绝")
    confidence = str(obj.get("confidence") or "").strip().lower()
    if confidence not in ANALYSIS_CONFIDENCES:
        return False, {}, (f"confidence `{confidence or '空'}` 不在允许集合 "
                           f"{list(ANALYSIS_CONFIDENCES)} 内——整条拒绝")
    raw_note = str(obj.get("note") or "")
    if len(raw_note) > ANALYSIS_NOTE_LIMIT:
        return False, {}, (f"note 超过 {ANALYSIS_NOTE_LIMIT} 字符上限"
                           f"（{len(raw_note)} 字符）——整条拒绝")
    raw_value = str(obj.get("value") or "")
    if len(raw_value) > ANALYSIS_VALUE_LIMIT:
        return False, {}, (f"value 超过 {ANALYSIS_VALUE_LIMIT} 字符上限"
                           f"（{len(raw_value)} 字符）——整条拒绝")
    field_ = str(obj.get("field") or "").strip()
    value = raw_value.strip()
    note = _clip(raw_note, ANALYSIS_NOTE_LIMIT)
    if action == "fill_field" and not field_:
        return False, {}, "action=fill_field 但 `field` 为空——整条拒绝"
    if action != "report" and not value:
        return False, {}, f"action={action} 但 `value` 为空——整条拒绝"
    evidence = str(obj.get("evidence") or "").strip()
    if not evidence:
        if kind == "lowconf_review" and note:
            pass    # B-132 口径：低置信复核允许 note-only（无 evidence）
        else:
            return False, {}, ("缺少 `evidence`（任务书原文的逐字片段）——整条拒绝"
                               "（`lowconf_review` 可用 `note` 代替）")
    elif _squeeze(evidence) not in evidence_squeezed:
        return False, {}, ("evidence 不是任务书（附四之前内容）的逐字子串（疑似改写/编造）"
                           "——整条拒绝")
    out = {
        "schema": ANALYSIS_SCHEMA,
        "id": id_,
        "kind": kind,
        "target": target,
        "action": action,
        "field": field_,
        "value": value,
        "locator": obj.get("locator") if obj.get("locator") not in (None, "") else "",
        "anchor": _clip(obj.get("anchor") or "", 300),
        "evidence": evidence,   # 逐字原文（只报告；md 渲染时再压一行/截断）
        "confidence": confidence,
        "source": _clip(obj.get("source") or "", 100),
        "note": note,
    }
    return True, out, ""


def analysis_evidence_base(taskbook_text: str) -> str:
    """批75（B-140①）：附四 `evidence` 校验基准 = 任务书里**附四之前**的内容。

    旧口径传整份任务书 → AI 可以抄附四自身的 `slcn.fulltext.v1` schema 示例/指令
    （例如 `"……\\\\footnote{参见[12]。}……"` 或指令里的句子）冒充「逐字证据」。
    这里从第一个附四标题处截断：基准仍包含**附三正文 + 附一原文 + 附二/附三说明**
    （以及一~五节等任务书正文），只排除附四小节（含其 schema 示例与指令示例）。
    任务书里没有附四（旧 state/旧回复）→ 原样返回（行为与旧版一致）。
    """
    text = str(taskbook_text or "")
    i = text.find(ANALYSIS_TITLE)
    return text[:i] if i >= 0 else text


def parse_fulltext_analysis(texts: Iterable[tuple[str, str]],
                            evidence_base: str) -> FulltextAnalysisReport:
    """解析并逐条校验附四回复（`schema=slcn.fulltext.v1`），**只报告、不写产物**。

    分区：只认带 `schema=slcn.fulltext.v1` 的 JSON 对象（附一解析器会跳过它们，见
    `parse_low_confidence()`）；其余对象留给附一/文献部分。
    校验顺序（与 spec §2.3/§2.7 一致）：上限 → 未知 kind（记录不丢弃）→ 同 id 重复 →
    `(kind,target,locator)` 冲突 → 逐条字段/长度/evidence 校验 → 低置信/unresolved 分诊。
    `evidence_base`（批75/B-140①）= `analysis_evidence_base(任务书全文)`：**附四小节不算**
    （schema 示例/指令不能当逐字证据）；由调用方（convert.py）从任务书抽取后传入。
    """
    report = FulltextAnalysisReport()
    raw: list[dict] = []
    for _name, text in texts:
        for obj in iter_json_objects(text or ""):
            if str(obj.get("schema") or "").strip() == ANALYSIS_SCHEMA:
                raw.append(obj)
    report.total = len(raw)
    if not raw:
        return report

    # ① 上限闸：按回复里出现的顺序计数；超出部分拒绝（不静默丢）。
    candidates: list[tuple[int, dict]] = []
    seen_kind: dict[str, int] = {}
    for idx, obj in enumerate(raw, 1):
        kind = str(obj.get("kind") or "").strip()
        id_ = _analysis_id(obj, idx)
        if idx > ANALYSIS_MAX_TOTAL:
            report.rejected.append({
                "id": id_, "kind": kind,
                "reason": f"超过每篇 {ANALYSIS_MAX_TOTAL} 条上限（第 {idx} 条起）——整条拒绝"})
            continue
        if kind:
            seen_kind[kind] = seen_kind.get(kind, 0) + 1
            if seen_kind[kind] > ANALYSIS_MAX_PER_KIND:
                report.rejected.append({
                    "id": id_, "kind": kind,
                    "reason": f"超过每 kind {ANALYSIS_MAX_PER_KIND} 条上限"
                              f"（`{kind}` 第 {seen_kind[kind]} 条）——整条拒绝"})
                continue
        candidates.append((idx, obj))

    # ② 未知 kind：记录不丢弃（不进入后续白名单校验）。
    known: list[tuple[int, dict]] = []
    for idx, obj in candidates:
        kind = str(obj.get("kind") or "").strip()
        if not kind:
            report.rejected.append({
                "id": _analysis_id(obj, idx), "kind": "",
                "reason": "缺少必填字段 `kind`——整条拒绝"})
            continue
        if kind not in ANALYSIS_KIND_RULES:
            report.unknown_kinds.append({"id": _analysis_id(obj, idx), "kind": kind})
            continue
        known.append((idx, obj))

    # ③ 同 id 重复：该 id 全部拒绝；④ 同 (kind,target,locator) 冲突：涉事条全部拒绝。
    by_id: dict[str, list[int]] = {}
    for idx, obj in known:
        by_id.setdefault(str(obj.get("id") or "").strip(), []).append(idx)
    dup_ids = {id_ for id_, idxs in by_id.items() if id_ and len(idxs) > 1}
    loc_keys: dict[str, list[tuple[int, dict]]] = {}
    for idx, obj in known:
        id_ = str(obj.get("id") or "").strip()
        if id_ in dup_ids:
            continue
        lk = _analysis_locator_key(obj)
        if not lk:
            continue
        kind = str(obj.get("kind") or "").strip()
        target = str(obj.get("target") or "").strip()
        loc_keys.setdefault(f"{kind}\0{target}\0{lk}", []).append((idx, obj))
    conflict_idx: set[int] = set()
    for _lk, group in loc_keys.items():
        if len(group) > 1:
            conflict_idx.update(i for i, _o in group)
    for id_ in sorted(dup_ids):
        first = next(obj for idx, obj in known if str(obj.get("id") or "").strip() == id_)
        kind = str(first.get("kind") or "").strip()
        report.rejected.append({
            "id": id_, "kind": kind,
            "reason": f"重复 id：同一个 `id` 出现 {len(by_id[id_])} 次——该 id 全部拒绝"})

    evidence_squeezed = _squeeze(evidence_base or "")
    for idx, obj in known:
        id_ = str(obj.get("id") or "").strip()
        kind = str(obj.get("kind") or "").strip()
        if id_ in dup_ids:
            continue
        if idx in conflict_idx:
            report.rejected.append({
                "id": id_, "kind": kind,
                "reason": "冲突：同一 `(kind, target, locator)` 收到多条建议"
                          "（工具拒绝猜哪条对）——涉事条全部拒绝"})
            continue
        ok, out, why = _validate_analysis(
            obj, evidence_squeezed=evidence_squeezed, idx=idx)
        if not ok:
            report.rejected.append({"id": id_, "kind": kind, "reason": why})
            continue
        if _analysis_is_unresolved(obj) or out["confidence"] == "low":
            report.manual.append(out)
        else:
            report.accepted.append(out)
    return report


def _analysis_locator_text(rec: dict) -> str:
    loc = rec.get("locator")
    if not loc:
        return ""
    return _clip(loc if isinstance(loc, str) else _canonical(loc), 160)


def render_analysis_md(*, stem: str, report: FulltextAnalysisReport) -> str:
    """`<名>_AI全文分析.md`：四节（已接受/被拒绝/待人工/未知 kind）。"""
    L = [f"# AI 全文分析（附四，只报告）· {stem}", "",
         "> 由编辑回填的外部 chat 回复里的 `slcn.fulltext.v1` JSON 解析而来。",
         "> **首版 report-only：本报告不写任何产物**——不改 `reference.bib` / 输出 `.tex`、",
         "> 不新增 `.ai前`、不把任何建议自动应用（`--coop-apply` 也不作用于附四）。",
         "> 每条建议都要通过 `evidence` 逐字子串校验（对不上任务书原文的整条拒绝）。", "",
         f"- 已接受：**{len(report.accepted)}** 条",
         f"- 被拒绝：**{len(report.rejected)}** 条",
         f"- 待人工：**{len(report.manual)}** 条",
         f"- 未知 kind：**{len(report.unknown_kinds)}** 个", ""]

    L += ["## 一、已接受（校验通过；report-only 不写产物）", ""]
    if not report.accepted:
        L += ["（无）", ""]
    for rec in sorted(report.accepted, key=lambda r: str(r.get("id") or "")):
        L += [f"### `{rec['id']}` — {ANALYSIS_KIND_LABEL.get(rec['kind'], rec['kind'])}", "",
              f"- 动作：`{rec['action']}` → `{rec['target']}`"]
        if rec.get("field"):
            L.append(f"- 字段：`{rec['field']}`")
        if rec.get("value"):
            L.append(f"- 建议值：{_code_span(rec['value'], 300)}")
        if _analysis_locator_text(rec):
            L.append(f"- 定位：{_code_span(_analysis_locator_text(rec), 160)}")
        if rec.get("anchor"):
            L.append(f"- 锚点：{_code_span(rec['anchor'], 200)}")
        if rec.get("evidence"):
            L.append(f"- 依据：{_code_span(rec['evidence'], 300)}")
        _tail = f"- 置信度：`{rec['confidence']}`"
        if rec.get("source"):
            _tail += f"；来源：{rec['source']}"
        L.append(_tail)
        if rec.get("note"):
            L.append(f"- 说明：{rec['note']}")
        L.append("")

    L += ["## 二、被拒绝（逐条原因）", ""]
    if not report.rejected:
        L += ["（无）", ""]
    for rec in report.rejected:
        _kind = f"（`{rec['kind']}`）" if rec.get("kind") else ""
        L.append(f"- `{rec['id']}`{_kind}：{rec['reason']}")
    if report.rejected:
        L.append("")

    L += ["## 三、待人工（`unresolved` 或 `confidence=low`）", ""]
    if not report.manual:
        L += ["（无）", ""]
    for rec in sorted(report.manual, key=lambda r: str(r.get("id") or "")):
        why = "confidence=low" if rec["confidence"] == "low" else "unresolved（拿不准）"
        L += [f"### `{rec['id']}` — {ANALYSIS_KIND_LABEL.get(rec['kind'], rec['kind'])}", "",
              f"- 分流原因：{why}（请编辑人工判断，**不要**自动采用）"]
        if rec.get("value"):
            L.append(f"- 建议值：{_code_span(rec['value'], 300)}")
        if rec.get("evidence"):
            L.append(f"- 依据：{_code_span(rec['evidence'], 300)}")
        if rec.get("note"):
            L.append(f"- 说明：{rec['note']}")
        L.append("")

    L += ["## 四、未知 kind", ""]
    if not report.unknown_kinds:
        L += ["（无）", ""]
    for rec in sorted(report.unknown_kinds, key=lambda r: (str(r.get("id") or ""),
                                                           str(r.get("kind") or ""))):
        L.append(f"- `{rec['id']}`：未知 kind `{rec['kind']}`"
                 "（不在 11 类白名单内；**已记录、未采用**）")
    if report.unknown_kinds:
        L.append("")
    return "\n".join(L)


def write_analysis_report(stage_dir: str, stem: str,
                          report: FulltextAnalysisReport) -> tuple[str, str]:
    """把附四报告写进暂存目录（随 promote 进交付目录）；`.jsonl` 每行一条已接受项。

    顺序确定：`.jsonl` 按 `id` 排序（同输入同输出 sha）；0 条已接受时 `.jsonl` 为空文件。
    """
    md_path = os.path.join(stage_dir, f"{stem}{ANALYSIS_SUFFIX}.md")
    jsonl_path = os.path.join(stage_dir, f"{stem}{ANALYSIS_SUFFIX}.jsonl")
    atomic_write_text(md_path, render_analysis_md(stem=stem, report=report))
    lines = [json.dumps(rec, ensure_ascii=False)
             for rec in sorted(report.accepted, key=lambda r: str(r.get("id") or ""))]
    atomic_write_text(jsonl_path, "\n".join(lines) + ("\n" if lines else ""))
    return md_path, jsonl_path


_JSON_DECODER = json.JSONDecoder()


def iter_json_objects(text: str) -> Iterable[dict]:
    """从整段回复里捞出所有 JSON 对象（容忍前后解释、围栏、数组）。"""
    i, n = 0, len(text or "")
    while i < n:
        j = text.find("{", i)
        if j < 0:
            return
        try:
            obj, end = _JSON_DECODER.raw_decode(text[j:])
        except (ValueError, TypeError):
            i = j + 1
            continue
        if isinstance(obj, dict):
            yield obj
        i = j + max(1, end)


@dataclass
class LowConfReport:
    # id -> 校验通过的 JSON 列表（同一个 ID 可以按不同 field 回多条）
    suggestions: dict[str, list[dict]] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)       # id -> 原因
    missing: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    status: dict[str, str] = field(default_factory=dict)

    @property
    def n_ok(self) -> int:
        """接受**建议条数**（同一 ID 的多个字段各算一条）。"""
        return sum(len(v) for v in self.suggestions.values())

    def summary(self) -> str:
        bits = [f"接受 {self.n_ok} 条", f"拒绝 {len(self.rejected)} 条",
                f"未回填 {len(self.missing)} 条"]
        _note_only = sum(1 for sugs in self.suggestions.values()
                         for sug in (sugs or []) if (sug or {}).get("note_only"))
        if _note_only:
            # 批75（B-132）：note-only 已收下但按「待人工」口径计入，摘要里点名。
            bits.append(f"其中 note-only {_note_only} 条")
        if self.unknown:
            bits.append(f"白名单外 ID {len(self.unknown)} 个")
        return "，".join(bits)


def parse_low_confidence(texts: Iterable[tuple[str, str]], items: Iterable[dict]) -> LowConfReport:
    """解析并**逐条严格校验**低置信项回复；任一对象不过该 ID 整条拒绝。

    同一个 ID 允许按 field 回多条（每字段一个 JSON 对象）；同一 `(id, field)`
    重复、同一 ID 的 verdict 冲突 → 整条拒绝并写明原因。
    """
    items = list(items)
    by_id = {str(it["id"]): it for it in items}
    report = LowConfReport()
    raw: dict[str, list[dict]] = {}
    for _name, text in texts:
        for obj in iter_json_objects(text or ""):
            # 批74：附四 `slcn.fulltext.v1` 对象由 parse_fulltext_analysis() 单独解析；
            # 这里必须跳过（否则 `AN-*` 会进附一的 unknown 报告）。
            if str(obj.get("schema") or "").strip() == ANALYSIS_SCHEMA:
                continue
            # 支持模型写成 {"FN-1": {...}} 的包一层（没有顶层 id 时）。
            if "id" not in obj and len(obj) == 1:
                k, v = next(iter(obj.items()))
                if k in by_id and isinstance(v, dict):
                    obj = dict(v, id=k)
            id_ = str(obj.get("id") or "").strip()
            if not id_:
                continue
            if id_ not in by_id:
                if id_ not in report.unknown:
                    report.unknown.append(id_)
                continue
            raw.setdefault(id_, []).append(obj)

    for id_ in by_id:
        if id_ not in raw:
            report.status[id_] = "missing"
            report.missing.append(id_)
            continue
        objs = raw[id_]
        verdicts = {str(o.get("verdict") or "").strip().lower() for o in objs}
        if len(verdicts) > 1:
            report.status[id_] = "rejected"
            report.rejected[id_] = (
                "verdict 冲突：同一个 ID 收到多种 verdict（"
                + "、".join(sorted(v or "空" for v in verdicts))
                + "）——同一 ID 的多个字段回复必须使用同一个 verdict")
            continue
        fields_seen: dict[str, int] = {}
        for o in objs:
            f = str(o.get("field") or "").strip()
            fields_seen[f] = fields_seen.get(f, 0) + 1
        dups = [f for f, c in fields_seen.items() if c > 1]
        if dups:
            labels = "、".join(f"`{d}`" if d else "（未指定 field）" for d in dups)
            report.status[id_] = "rejected"
            report.rejected[id_] = (
                f"重复回复：同一个 (id, field) 出现多次：{labels}"
                "（同一字段只能回一条 JSON）")
            continue
        outs: list[dict] = []
        bad = ""
        for o in objs:
            ok, out, why = _validate_suggestion(by_id[id_], o)
            if not ok:
                if len(objs) == 1:
                    bad = why
                else:
                    f = str(o.get("field") or "").strip()
                    bad = f"字段 `{f or '—'}`：{why}"
                break
            outs.append(out)
        if bad:
            report.status[id_] = "rejected"
            report.rejected[id_] = bad
        else:
            report.status[id_] = "ok"
            report.suggestions[id_] = outs
    return report


def _validate_suggestion(item: dict, obj: dict) -> tuple[bool, dict, str]:
    verdict = str(obj.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        return False, {}, (
            f"verdict 不在允许集合 {VERDICTS} 内（收到 `{verdict or '空'}`）——整条拒绝")
    span = str(obj.get("span") or "").strip()
    evidence = str(item.get("evidence") or "")
    raw_note = str(obj.get("note") or "")
    note_only = False
    if not span:
        # 批75（B-132）：`unresolved` 允许 **note-only**（无 span）。86-08 的 FN-0
        # （Hacking1999「源稿文末清单缺条」）内容正确，旧版却因缺 span 整条拒绝、note 丢弃。
        # 只对 unresolved 放宽，而且必须带 note；corrected/confirmed 仍强制 span（不放宽）。
        if verdict == "unresolved" and raw_note.strip():
            note_only = True
            if len(raw_note) > 300:
                return False, {}, ("note 超过 300 字符上限（note-only 靠它承载说明；"
                                   f"收到 {len(raw_note)} 字符）——整条拒绝")
        else:
            return False, {}, (
                "缺少 span（原文证据）——整条拒绝"
                "（只有 `unresolved` 可以用 `note` 代替 span，其余 verdict 不行）")
    if span and span not in evidence:
        return False, {}, "span 不是「原文片段」的逐字子串（疑似改写/编造）——整条拒绝"
    field_ = str(obj.get("field") or "").strip()
    value = str(obj.get("value") or "").strip()
    allowed = [str(f) for f in item.get("fields") or []]
    if verdict == "corrected":
        if not field_:
            return False, {}, "verdict=corrected 但 field 为空——整条拒绝"
        if field_ not in allowed:
            return False, {}, f"field `{field_}` 不在允许白名单（{'、'.join(allowed) or '无'}）——整条拒绝"
        if not value:
            return False, {}, "verdict=corrected 但 value 为空——整条拒绝"
    elif field_ and field_ not in allowed:
        return False, {}, f"field `{field_}` 不在允许白名单（{'、'.join(allowed) or '无'}）——整条拒绝"
    out = {
        "id": str(item["id"]), "kind": item.get("kind", ""), "verdict": verdict,
        "field": field_, "value": value, "span": span,
        "note": _clip(raw_note, 300),
        "key": item.get("key", ""),
    }
    if note_only:
        # 只给 note-only 记录加键（普通 replied 的建议结构/jsonl 逐字节不变）。
        out["note_only"] = True
    # 第五十七批：FN 的 match_key 允许在同一 JSON 里带 `locator`（页码定位）。
    if field_ == "match_key":
        out["locator"] = _clip(obj.get("locator") or "", 40)
    return True, out, ""


def render_suggestions_md(*, stem: str, items: Iterable[dict], report: LowConfReport) -> str:
    items = list(items)
    by_id = {str(it["id"]): it for it in items}
    L = [f"# AI 协同建议 · {stem}", "",
         "> 由编辑回填的外部 chat 回复解析而来。**默认不改任何转换产物**；",
         "> 只有运行 `--coop-apply` 时，通过校验的建议才会写进产物（写前各留 `.ai前`）：",
         "> BIB 字段 → `reference.bib`；META 元数据 → 输出 `.tex` 的 `\\my*` 字段；",
         "> FN 匹配（仅 citation_unmatched + `match_key`）→ `.tex` 里的 `\\footnote` 提升为 `\\parencite`。",
         ""]
    # 批75（B-132）：`verdict=unresolved` 且无 span 的 **note-only** 回复已收下（note 保留），
    # 按「待人工」口径只列在第三节（不进第一节的「已接受建议」，避免编辑误以为可应用）。
    note_only_rows = [(id_, sug) for id_, sugs in report.suggestions.items()
                      for sug in (sugs or []) if sug.get("note_only")]
    L += [f"- 接受：**{report.n_ok}** 条", f"- 拒绝：**{len(report.rejected)}** 条",
          f"- 未回填：**{len(report.missing)}** 条",
          f"- 白名单外 ID：**{len(report.unknown)}** 个"]
    if note_only_rows:
        L.append(f"- 其中 note-only `unresolved`（保留说明、待人工）："
                 f"**{len(note_only_rows)}** 条")
    L.append("")

    listed = {id_: [sug for sug in (sugs or []) if not sug.get("note_only")]
              for id_, sugs in report.suggestions.items()}
    listed = {id_: sugs for id_, sugs in listed.items() if sugs}
    L += ["## 一、已接受建议", ""]
    if not listed:
        L += ["（无）", ""]
    for id_, sugs in listed.items():
        it = by_id.get(id_, {})
        for sug in sugs:
            L += [f"### `{id_}` — {KIND_LABEL.get(it.get('kind', ''), it.get('kind', ''))}"
                  f" — `{sug['field'] or '—'}`", "",
                  f"- verdict：`{sug['verdict']}`",
                  f"- field：`{sug['field'] or '—'}`",
                  f"- value：{_code_span(sug['value'], 300)}",
                  f"- span：{_code_span(sug['span'], 300)}"]
            if sug.get("note"):
                L.append(f"- 说明：{sug['note']}")
            L.append("")

    L += ["## 二、被拒绝", ""]
    if not report.rejected:
        L += ["（无）", ""]
    for id_, why in report.rejected.items():
        L += [f"- `{id_}`：{why}"]
    L.append("")

    L += ["## 三、待人工", ""]
    if not report.missing and not note_only_rows:
        L += ["（无）", ""]
    for id_ in report.missing:
        it = by_id.get(id_, {})
        L += [f"- `{id_}`：{it.get('title', '')} —— 回复里没有这一项"]
    if note_only_rows:
        L += ["", "### `unresolved` note-only（已收下说明，请人工判断）", ""]
        for id_, sug in note_only_rows:
            it = by_id.get(id_, {})
            L += [f"- `{id_}`：{it.get('title', '')} —— `unresolved`"
                  "（未提供 span，按 note-only 收下；**不要**自动采用）",
                  f"  - 说明：{sug.get('note') or '（无）'}"]
    if report.unknown:
        L += ["", "## 四、白名单外的 ID（已忽略）", ""]
        for id_ in report.unknown:
            L.append(f"- `{id_}`（任务书/状态里没有这个 ID；未采用）")
    L.append("")
    return "\n".join(L)


def write_suggestions(stage_dir: str, stem: str, items: Iterable[dict],
                      report: LowConfReport) -> tuple[str, str]:
    """默认通道：建议只写 `…_AI建议.md` / `.jsonl`，**不改产物**。"""
    items = list(items)
    by_id = {str(it["id"]): it for it in items}
    md_path = os.path.join(stage_dir, f"{stem}{SUGGESTIONS_SUFFIX}.md")
    jsonl_path = os.path.join(stage_dir, f"{stem}{SUGGESTIONS_SUFFIX}.jsonl")
    atomic_write_text(md_path, render_suggestions_md(stem=stem, items=items, report=report))
    def _target(it: dict) -> str:
        return {"bib_entry": TARGET_BIB, "metadata": TARGET_TEX,
                "footnote": TARGET_TEX}.get(str(it.get("kind") or ""), "")

    lines: list[str] = []
    for id_ in list(by_id):
        if id_ in report.suggestions:
            # 同一个 ID 的每个 field 各占一行。
            for sug in report.suggestions[id_]:
                # 第五十七批：加 target（可应用的产物）；旧读法只取 status/field，不受影响。
                lines.append(json.dumps(dict(sug, status="accepted",
                                             target=_target(by_id[id_])),
                                        ensure_ascii=False))
            continue
        elif id_ in report.rejected:
            rec = {"id": id_, "kind": by_id[id_].get("kind", ""),
                   "status": "rejected", "reason": report.rejected[id_]}
        else:
            rec = {"id": id_, "kind": by_id[id_].get("kind", ""), "status": "missing"}
        lines.append(json.dumps(rec, ensure_ascii=False))
    atomic_write_text(jsonl_path, "\n".join(lines) + ("\n" if lines else ""))
    return md_path, jsonl_path


# ── 低置信建议的「应用」（只有 --coop-apply 才走）─────────────────────────


def _find_field_span(entry: str, field_: str) -> tuple[int, int] | None:
    """在条目文本里找 `field = <value>` 的**值**区间；找不到返回 None。"""
    m = re.search(rf"(?mi)^[ \t]*{re.escape(field_)}[ \t]*=[ \t]*", entry)
    if not m:
        return None
    i = m.end()
    n = len(entry)
    if i >= n:
        return None
    if entry[i] == "{":
        depth = 0
        for j in range(i, n):
            if _escaped(entry, j):
                continue
            if entry[j] == "{":
                depth += 1
            elif entry[j] == "}":
                depth -= 1
                if depth == 0:
                    return i, j + 1
        return None
    if entry[i] == '"':
        j = i + 1
        while j < n:
            if entry[j] == '"' and not _escaped(entry, j):
                return i, j + 1
            j += 1
        return None
    j = i
    depth = 0
    while j < n:
        ch = entry[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        j += 1
    return i, j


def set_bib_field(entry_text: str, field_: str, value: str) -> str:
    """在一条 BibTeX 条目里替换/新增一个字段（**key 与其余字段逐字节不动**）。

    抛 `ValueError` = 输入不安全（字段名非法 / 值花括号不配平 / 条目不含 key）。
    """
    field_ = str(field_ or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field_):
        raise ValueError(f"字段名不合法：{field_!r}")
    value = str(value or "").strip()
    if not value:
        raise ValueError("字段值为空")
    if value.count("{") != value.count("}"):
        raise ValueError("字段值的花括号不配平")
    entry = str(entry_text or "")
    m = re.match(r"(?is)^\s*@([A-Za-z]+)\s*\{\s*([^,\s{}]+)\s*,", entry)
    if not m:
        raise ValueError("不是可识别的 BibTeX 条目")
    if field_ == "type":
        etype = value.lstrip("@").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", etype):
            raise ValueError(f"条目类型不合法：{value!r}")
        return entry[:m.start(1)] + etype + entry[m.end(1):]

    span = _find_field_span(entry, field_)
    if span:
        a, b = span
        return entry[:a] + "{" + value + "}" + entry[b:]

    # 新增字段：插到收尾 `}` 之前（保持条目原样，只加一行）。
    end = entry.rfind("}")
    if end < 0:
        raise ValueError("条目没有收尾花括号")
    body = entry[:end].rstrip()
    if not body.endswith(","):
        body += ","
    return body + f"\n  {field_} = {{{value}}},\n" + entry[end:]


def apply_bib_suggestions(bib_path: str, suggestions: dict[str, list[dict]],
                          items: Iterable[dict],
                          *, preserve_existing_backup: bool = False) -> dict:
    """把通过校验的低置信建议落到 `reference.bib`（复用 aibib 的四条硬保障）。

    只应用 `kind=bib_entry` 且 `verdict=corrected` 的建议；其他（脚注/元数据/文献区
    原文行）只进记录、待人工。同一个文献键的多个字段建议按顺序落到**同一份**条目
    文本上；返回 `{n_applied, n_skipped, rollback, applied, skipped, error}`，
    其中 `n_applied` 按 **(id, field) 建议条数**计。**0 条应用时不碰文件**
    （也不留回滚副本）。
    """
    from slcn import aibib

    out: dict = {"n_applied": 0, "n_skipped": 0, "rollback": "",
                 "applied": [], "skipped": [], "error": "", "backup_kept": ""}
    by_id = {str(it["id"]): it for it in items}
    if not bib_path or not os.path.exists(bib_path):
        out["error"] = "本次没有 reference.bib，无从应用低置信建议"
        return out
    original = open(bib_path, encoding="utf-8", errors="replace").read()
    blocks: dict[str, BibOccurrence] = {}
    for b in parse_bib_blocks(original):
        if b.closed and b.key not in blocks:
            blocks[b.key] = b

    working: dict[str, str] = {}          # key -> 已经叠加了前面字段建议的条目文本
    replies: dict[str, str] = {}
    intended: dict[str, list[dict]] = {}  # key -> 本次要应用的 (id, field, value) 清单
    for id_, sug_value in suggestions.items():
        # 兼容旧的单建议形状（dict），但下游一律按「每个 field 一条」处理。
        sugs = [sug_value] if isinstance(sug_value, dict) else list(sug_value or [])
        it = by_id.get(id_) or {}
        for sug in sugs:
            if sug.get("verdict") != "corrected" or it.get("kind") != "bib_entry":
                out["skipped"].append({"id": id_, "reason": "不是可安全落盘的文献字段建议（待人工）"})
                continue
            key = str(it.get("key") or sug.get("key") or "")
            block = blocks.get(key)
            if not key or block is None:
                out["skipped"].append({"id": id_, "reason": f"找不到文献键 `{key}`"})
                continue
            base = working.get(key, block.text)
            try:
                new_entry = set_bib_field(base, sug.get("field", ""), sug.get("value", ""))
            except ValueError as e:
                out["skipped"].append({"id": id_, "reason": f"{e}"})
                continue
            if aibib_parse(key, new_entry) is None:
                out["skipped"].append({"id": id_, "reason": "改写后的条目不再可解析"})
                continue
            working[key] = new_entry
            replies[key] = new_entry
            intended.setdefault(key, []).append(
                {"id": id_, "field": sug.get("field", ""), "value": sug.get("value", "")})

    if not replies:
        out["error"] = "没有可安全应用到 reference.bib 的低置信建议（产物未改动）"
        out["n_skipped"] = len(out["skipped"])
        return out

    result = aibib.merge(original, replies)
    if result.n_merged == 0:
        out["error"] = "低置信建议没有一条通过 aibib 安全校验（reference.bib 未改动）"
        out["n_skipped"] = len(out["skipped"])
        return out
    _backup = bib_path + ".ai前"
    _existing = b""
    if preserve_existing_backup and os.path.exists(_backup):
        try:
            with open(_backup, "rb") as _f:
                _existing = _f.read()
        except OSError:
            _existing = b""
    out["rollback"] = aibib.apply_merge_to_file(bib_path, result)
    if _existing:
        # 批63：已有 `.ai前`（如 ⑪ 逐条 AI 合并留下的回滚点）**不覆盖**——
        # 否则回滚点会跳到「上一次写之后」，编辑就退不回最初的原件了。
        with open(_backup, "wb") as _f:
            _f.write(_existing)
        out["rollback"] = ""
        out["backup_kept"] = _backup
    for rec in result.records:
        if rec.key not in intended:
            # 没被本次建议点名的条目只是「未返回/保持原样」，不是待人工项；
            # 记录里逐个列出来只会淹没真正需要人看的几条。
            continue
        if rec.merged:
            # 一个文献键可能叠了多个字段建议：逐字段记录（不只记键）。
            for a in intended[rec.key]:
                out["applied"].append(dict(a, key=rec.key))
        else:
            for a in intended[rec.key]:
                out["skipped"].append({"id": a.get("id", ""), "key": rec.key,
                                       "reason": rec.reason})
    out["n_applied"] = len(out["applied"])
    out["n_skipped"] = len(out["skipped"])
    return out


def restore_bib_backup(bib_path: str) -> bool:
    """把 `<bib>.ai前` 拷回去（低置信建议应用的可逆入口；测试也用它）。"""
    rb = bib_path + ".ai前"
    if not os.path.exists(rb):
        return False
    shutil.copy2(rb, bib_path)
    return True


# ── 第五十七批：META / FN 建议落到输出 .tex（只有 --coop-apply 才走）────────


def read_tex_field_values(tex_path: str) -> dict[str, str]:
    """读输出 tex 里所有 `\\my*` 字段的最终值（ph1 记进 META 项，供 ph2 判「是否被改过」）。"""
    from slcn import latex as _latex

    if not tex_path or not os.path.isfile(tex_path):
        return {}
    try:
        with open(tex_path, encoding="utf-8", errors="replace", newline="") as f:
            text = f.read()
    except OSError:
        return {}
    return {name: str(m.value or "") for name, m in _latex.parse_macros(text).items()}


def _tex_escape(text: str) -> str:
    """按 `docxsource.escape_latex` 同口径转义纯文本（锚点匹配与元数据值共用）。"""
    pairs = (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
             ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"),
             ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}"))
    s = str(text or "").replace("\\", "\x00")
    for raw, esc in pairs:
        if raw == "\\":
            continue
        s = s.replace(raw, esc)
    return s.replace("\x00", r"\textbackslash{}")


def _meta_target_macro(item: dict, id_: str) -> str:
    """META 项 → 输出 tex 的 \\my* 宏名（ph1 记录优先；旧状态从 id 后缀兜底）。"""
    m = str((item or {}).get("macro") or "").strip()
    if m:
        return m
    if id_.startswith("META-LABEL-"):
        return ""                     # 旧状态没有 label→macro 映射：只记录
    if id_.startswith("META-"):
        cand = id_[len("META-"):].strip()
        if re.fullmatch(r"my[A-Za-z0-9_]+", cand):
            return cand
    return ""


def _meta_value_reject(value: str, macro: str) -> str:
    """META 值的字段级校验；返回空串 = 可应用。"""
    v = str(value or "").strip()
    if not v:
        return "元数据值为空"
    if len(v) > 1200:
        return "元数据值过长（>1200 字符）"
    if any(ch in v for ch in "\\{}\n\r\t"):
        return "元数据值含反斜杠/花括号/换行，拒绝（避免破坏 .tex）"
    if macro in ("myvolnumber", "myissnumber"):
        if not re.fullmatch(r"\d{1,4}", v):
            return f"{macro} 只接受 1–4 位数字（收到 `{_clip(v, 20)}`）"
    elif macro == "mypubyear":
        if not re.fullmatch(r"(?:19|20)\d{2}", v):
            return "mypubyear 只接受 19xx/20xx 四位年份"
    elif macro in ("myfirstpage", "mylastpage", "mypages"):
        if not re.fullmatch(r"\d{1,5}", v) or (macro == "mypages" and int(v) <= 0):
            return f"{macro} 只接受正整数（收到 `{_clip(v, 20)}`）"
    elif macro == "receiveddate":
        if not (re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", v)
                or re.fullmatch(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", v)):
            return "receiveddate 只接受 YYYY-MM-DD 或「YYYY年M月D日」"
    return ""


def _iter_tex_footnotes(tex: str) -> Iterable[tuple[int, int, int]]:
    r"""扫出 tex 里所有 `\footnote{…}` 的 `(命令起点, 正文起点, 正文终点)`（brace 感知）。"""
    i, n = 0, len(tex)
    key = "\\footnote"
    while i < n:
        j = tex.find(key, i)
        if j < 0:
            return
        k = j + len(key)
        while k < n and tex[k] in " \t":
            k += 1
        if k >= n or tex[k] != "{":
            i = j + len(key)
            continue
        depth, m = 0, k
        while m < n:
            if _escaped(tex, m):
                m += 1
                continue
            if tex[m] == "{":
                depth += 1
            elif tex[m] == "}":
                depth -= 1
                if depth == 0:
                    break
            m += 1
        if m >= n:
            return
        yield j, k + 1, m
        i = m + 1


def _squeeze(text: str) -> str:
    return " ".join(str(text or "").split())


def apply_tex_suggestions(tex_path: str, suggestions: dict[str, list[dict]],
                          items: Iterable[dict], *, taskbook_keys: Iterable[str] = (),
                          bib_path: str = "",
                          preserve_existing_backup: bool = False) -> dict:
    """把通过校验的 META / FN 建议落到输出 `.tex`（只有 `--coop-apply` 才走）。

    只处理两类：
      · `kind=metadata` + `verdict=corrected` → 写 `\\my*` 字段（白名单 + 占位/编辑值闸）；
      · `kind=footnote` + `subkind=citation_unmatched` + `field=match_key` → 用 span 锚点
        在 tex 里**唯一定位** `\\footnote{…}`，改写为 `\\parencite[loc]{key}`。

    其余（mixed_manual / FN-W / note / confirmed / unresolved / BIB-*）一律只记录。
    **0 条可写时不碰文件、不留回滚副本**；有写入才 `copy2` 一次 `<tex>.ai前`。
    返回 `{n_applied, n_skipped, rollback_tex, applied, skipped, error}`。
    """
    from slcn import latex as _latex

    out: dict = {"n_applied": 0, "n_skipped": 0, "rollback_tex": "",
                 "applied": [], "skipped": [], "error": "", "backup_kept": ""}
    by_id = {str(it["id"]): it for it in items}
    meta_groups: dict[str, list[dict]] = {}   # macro -> [{id, sug}]
    fn_cands: list[dict] = []

    def skip(id_: str, reason: str) -> None:
        out["skipped"].append({"id": id_, "target": TARGET_TEX, "reason": reason})

    for id_, sug_value in suggestions.items():
        sugs = [sug_value] if isinstance(sug_value, dict) else list(sug_value or [])
        it = by_id.get(id_) or {}
        kind = str(it.get("kind") or "")
        if kind not in ("metadata", "footnote"):
            continue
        for sug in sugs:
            if sug.get("verdict") != "corrected":
                skip(id_, "不是 corrected（只记录，待人工）")
                continue
            if kind == "metadata":
                macro = _meta_target_macro(it, id_)
                if not macro:
                    skip(id_, "该项没有可写的模板字段（旧状态缺映射或「元数据源」），只记录")
                    continue
                meta_groups.setdefault(macro, []).append({"id": id_, "sug": sug})
            else:
                if not _FN_FID_RE.match(id_):
                    skip(id_, "FN-W 项没有可定位的脚注，只记录")
                    continue
                if str(it.get("subkind") or "") not in FN_APPLICABLE_SUBKINDS:
                    skip(id_, "只有 citation_unmatched 脚注支持提升（mixed_manual 等只记录）")
                    continue
                if str(sug.get("field") or "") != "match_key":
                    skip(id_, "脚注项只支持 field=match_key 应用（其它只记录）")
                    continue
                fn_cands.append({"id": id_, "item": it, "sug": sug})

    if not meta_groups and not fn_cands:
        out["n_skipped"] = len(out["skipped"])
        return out

    if not tex_path or not os.path.isfile(tex_path):
        for group in meta_groups.values():
            for ent in group:
                skip(ent["id"], "暂存目录里没有输出 .tex")
        for cand in fn_cands:
            skip(cand["id"], "暂存目录里没有输出 .tex")
        out["n_skipped"] = len(out["skipped"])
        out["error"] = "暂存目录里没有输出 .tex，元数据/脚注建议未应用"
        return out

    with open(tex_path, encoding="utf-8", errors="replace", newline="") as f:
        tex = f.read()
    macros = _latex.parse_macros(tex)
    applied: list[dict] = []

    # ① META：先分组查冲突/占位/编辑值，再逐字段写；每次写后回读校验。
    for macro, entries in meta_groups.items():
        # 白名单：编辑侧出版字段 ∪ ph1 记录的 META-LABEL 映射（其余一律不改）。
        allowed = macro in EDITORIAL_TEX_FIELDS or any(
            str(ent["id"]).startswith("META-LABEL-")
            and str((by_id.get(ent["id"]) or {}).get("macro") or "") == macro
            for ent in entries)
        if not allowed:
            for ent in entries:
                skip(ent["id"], f"`{macro}` 不在元数据应用白名单（只记录）")
            continue
        cand_vals = {str(ent["sug"].get("value") or "").strip() for ent in entries}
        if len(cand_vals) > 1:
            for ent in entries:
                skip(ent["id"], f"同一字段 `{macro}` 收到多个不同值，拒绝全部")
            continue
        value = next(iter(cand_vals))
        why = _meta_value_reject(value, macro)
        if why:
            for ent in entries:
                skip(ent["id"], why)
            continue
        m = macros.get(macro)
        if m is None:
            for ent in entries:
                skip(ent["id"], f"输出 .tex 里没有字段 `{macro}`")
            continue
        cur = str(m.value or "").strip()
        # 初排时该字段的值（ph1 记录）；与当前 tex 不一致 = 产物在初排后被改过 → 拒绝。
        item_cur = str((by_id.get(entries[0]["id"]) or {}).get("current") or "")
        if any(str((by_id.get(ent["id"]) or {}).get("current") or "") != item_cur
               for ent in entries):
            for ent in entries:
                skip(ent["id"], f"`{macro}` 分组内初排取值不一致，拒绝")
            continue
        if item_cur:
            if cur != item_cur:
                for ent in entries:
                    skip(ent["id"], f"`{macro}` 输出 .tex 与初排时取值不一致，拒绝覆盖")
                continue
        else:
            # 旧状态（批54–56 的 ph1 没有记录初排取值）：只允许写模板默认/空/占位值。
            if cur and cur != "null" and "未识别" not in cur \
                    and cur not in _TEMPLATE_PLACEHOLDERS:
                for ent in entries:
                    skip(ent["id"], f"`{macro}` 已有取值（非占位），拒绝覆盖")
                continue
        escaped = _tex_escape(value)
        try:
            new_tex = _latex.set_field(tex, macro, escaped)
        except (KeyError, ValueError) as e:
            for ent in entries:
                skip(ent["id"], f"字段 `{macro}` 改写失败：{e}")
            continue
        if str(_latex.get_macro_value(new_tex, macro) or "").strip() != escaped.strip():
            for ent in entries:
                skip(ent["id"], f"字段 `{macro}` 改写后校验不一致，放弃")
            continue
        tex = new_tex
        for ent in entries:
            applied.append({"id": ent["id"], "target": TARGET_TEX, "field": macro,
                            "macro": macro, "value": value})

    # ② FN：用 span 在 tex 里唯一定位 \footnote{…} → 正文 \parencite。
    tb_keys = {str(k) for k in taskbook_keys}
    bib_keys: set[str] = set()
    if bib_path and os.path.isfile(bib_path):
        try:
            with open(bib_path, encoding="utf-8", errors="replace") as f:
                bib_keys = {b.key for b in parse_bib_blocks(f.read()) if b.closed}
        except OSError:
            bib_keys = set()
    for cand in fn_cands:
        id_, it, sug = cand["id"], cand["item"], cand["sug"]
        key = str(sug.get("value") or "").strip()
        if not re.fullmatch(r"[^\s{},%#\\]+", key):
            skip(id_, f"匹配键 `{_clip(key, 40)}` 含非法字符，拒绝")
            continue
        if key not in tb_keys or (bib_keys and key not in bib_keys):
            skip(id_, f"匹配键 `{key}` 不在任务书键/当前 reference.bib 里，拒绝")
            continue
        evidence = str(it.get("evidence") or "")
        locator = str(sug.get("locator") or "").strip()
        if _PAGE_HINT_RE.search(evidence) and not locator:
            skip(id_, "原文片段含页码线索但未给 locator（不丢页码），拒绝")
            continue
        if locator:
            if not _LOCATOR_RE.match(locator):
                skip(id_, f"locator `{_clip(locator, 30)}` 不是页码/页码区间，拒绝")
                continue
            locator = re.sub(r"\s*[-–—~]\s*", "--", locator)
        needle = _squeeze(_tex_escape(evidence))
        if not needle:
            skip(id_, "该项没有可用锚点（evidence 为空），拒绝")
            continue
        hits = [(a, b, c) for (a, b, c) in _iter_tex_footnotes(tex)
                if needle in _squeeze(tex[b:c])]
        if len(hits) != 1:
            skip(id_, f"脚注定位失败（escape 后锚点命中 {len(hits)} 处，需恰好 1 处）")
            continue
        a, _b, c = hits[0]
        cmd = f"\\parencite[{locator}]{{{key}}}" if locator else f"\\parencite{{{key}}}"
        tex = tex[:a] + cmd + tex[c + 1:]
        applied.append({"id": id_, "target": TARGET_TEX, "field": "match_key",
                        "macro": "", "value": key, "key": key, "locator": locator})

    out["applied"] = applied
    out["n_applied"] = len(applied)
    out["n_skipped"] = len(out["skipped"])
    if not applied:
        out["error"] = "没有可安全应用到 .tex 的元数据/脚注建议（产物未改动）"
        return out
    rollback = tex_path + ".ai前"
    # 批63：`.ai前` 已存在（另一条 AI 通道写下的回滚点）时**不覆盖**，报告注明。
    if preserve_existing_backup and os.path.exists(rollback):
        out["backup_kept"] = rollback
    else:
        shutil.copy2(tex_path, rollback)
        out["rollback_tex"] = rollback
    with open(tex_path, "w", encoding="utf-8", newline="") as f:
        f.write(tex)
    return out


def restore_tex_backup(tex_path: str) -> bool:
    """把 `<tex>.ai前` 拷回去（第五十七批：元数据/脚注建议应用的可逆入口）。"""
    rb = tex_path + ".ai前"
    if not os.path.exists(rb):
        return False
    shutil.copy2(rb, tex_path)
    return True


# ── 第六十三批：通道②（直连 API 全文复核）的自动应用 ─────────────────────


def apply_fulltext_suggestions(*, accepted: Iterable[dict], items: Iterable[dict],
                               bib_path: str, tex_path: str,
                               taskbook_keys: Iterable[str] = ()) -> dict:
    """把通道②已通过校验的建议按 BIB/META/FN 分派到批57 安全闸（仅此三类）。

    批63（用户 2026-10-20 拍板）：默认开。**完全复用** `--coop-apply` 的两个应用器，
    因此「0 条不写盘 / 写前 `.ai前` / 冲突与漂移拒绝 / 白名单 / 逐条记录」与批57
    只有一份实现。与批57 的唯一差别：`preserve_existing_backup=True`——`.ai前`
    已存在（⑪ 逐条 AI 合并留下的回滚点）时不覆盖（报告注明）。
    返回 `merge_apply_summaries` 形状（含 applied/skipped/rollback/backup_kept/error）。
    """
    items = list(items)
    by_id = {str(it["id"]): it for it in items}
    bib_sugs: dict[str, list[dict]] = {}
    tex_sugs: dict[str, list[dict]] = {}
    other: list[dict] = []
    for sug in accepted:
        id_ = str(sug.get("id") or "")
        it = by_id.get(id_) or {}
        kind = str(it.get("kind") or sug.get("kind") or "")
        if kind == "bib_entry":
            bib_sugs.setdefault(id_, []).append(dict(sug))
        elif kind in ("metadata", "footnote"):
            tex_sugs.setdefault(id_, []).append(dict(sug))
        else:
            other.append({"id": id_,
                          "target": TARGET_BIB if kind.startswith("bib") else TARGET_TEX,
                          "reason": "只有 BIB/META/FN 会自动应用（其余只记录）"})
    parts: list[dict] = []
    if bib_sugs:
        parts.append(apply_bib_suggestions(
            bib_path, bib_sugs, items, preserve_existing_backup=True))
    if tex_sugs:
        parts.append(apply_tex_suggestions(
            tex_path, tex_sugs, items, taskbook_keys=taskbook_keys,
            bib_path=bib_path, preserve_existing_backup=True))
    out = merge_apply_summaries(*parts)
    out["skipped"] = list(out.get("skipped") or []) + other
    out["n_skipped"] = len(out["skipped"])
    return out


def merge_apply_summaries(*parts: dict | None) -> dict:
    """合并「文献字段应用」与「.tex 元数据/脚注应用」两份总结（键向后兼容）。"""
    out: dict = {"n_applied": 0, "n_skipped": 0, "rollback": "", "rollback_tex": "",
                 "applied": [], "skipped": [], "error": "", "backup_kept": ""}
    errs: list[str] = []
    for p in parts:
        if not p:
            continue
        out["n_applied"] += int(p.get("n_applied") or 0)
        out["n_skipped"] += int(p.get("n_skipped") or 0)
        if p.get("rollback"):
            out["rollback"] = p["rollback"]
        if p.get("rollback_tex"):
            out["rollback_tex"] = p["rollback_tex"]
        if p.get("backup_kept"):
            out["backup_kept"] = p["backup_kept"]
        out["applied"] += list(p.get("applied") or [])
        out["skipped"] += list(p.get("skipped") or [])
        if p.get("error"):
            errs.append(str(p["error"]))
    out["error"] = "；".join(errs)
    return out


# ── 两段式状态 / 指纹 ────────────────────────────────────────────────────


def build_params(*, source: str, metadata: str, out_root: str, stem: str,
                 lang: str, sty: str, skeleton: str,
                 verify_refs: bool, no_ai_merge: bool,
                 end_list_priority: bool = False, number_cite_map: bool = False,
                 c_class_union: bool = True, stage_dir: str = "") -> dict:
    """关键参数（源稿/元数据都带 sha256）——ph2 必须逐项对得上才允许续跑。"""
    source = os.path.abspath(source)
    metadata = os.path.abspath(metadata) if metadata else ""
    params = {
        "source": source,
        "source_sha256": sha256_file(source) if os.path.isfile(source) else "",
        "metadata": metadata,
        "metadata_sha256": (sha256_file(metadata)
                            if metadata and os.path.isfile(metadata) else ""),
        "out_root": os.path.abspath(out_root),
        "stem": stem,
        "lang": lang,
        "sty": os.path.abspath(sty) if sty else "",
        "skeleton": os.path.abspath(skeleton) if skeleton else "",
        "verify_refs": bool(verify_refs),
        "no_ai_merge": bool(no_ai_merge),
        "end_list_priority": bool(end_list_priority),
        "number_cite_map": bool(number_cite_map),
        "c_class_union": bool(c_class_union),
        "stage_dir": os.path.abspath(stage_dir) if stage_dir else "",
    }
    params["fingerprint"] = sha256_text(_canonical(params))
    return params


def parameter_fingerprint(params: dict) -> str:
    clean = {k: v for k, v in params.items() if k != "fingerprint"}
    return sha256_text(_canonical(clean))


def state_path(stage_dir: str) -> str:
    return os.path.join(stage_dir, STATE_NAME)


def build_state(*, params: dict, taskbook_path: str, stage_dir: str, final_dir: str,
                bib_keys: Iterable[str], low_items: Iterable[dict],
                budget_article: int, budget_batch: int,
                engine_version: str = "") -> dict:
    low_items = list(low_items)
    keys = list(dict.fromkeys(str(k) for k in bib_keys))
    low_ids = [str(it["id"]) for it in low_items]
    return {
        "schema": SCHEMA_VERSION,
        "phase": "ph1",
        "created_at": _now(),
        "engine_version": engine_version,
        "source": {"path": params.get("source", ""),
                   "sha256": params.get("source_sha256", "")},
        "params": params,
        "fingerprint": params.get("fingerprint", parameter_fingerprint(params)),
        "taskbook": os.path.abspath(taskbook_path) if taskbook_path else "",
        "stage_dir": os.path.abspath(stage_dir),
        "final_dir": os.path.abspath(final_dir),
        "keys": {"bib": keys, "low_confidence": low_ids,
                 "all": keys + low_ids},
        "low_confidence": low_items,
        "replied": [], "pending": keys + low_ids,
        "budget": {"article_tokens": int(budget_article),
                   "batch_tokens": int(budget_batch),
                   "note": "外部 chat 通道不产生 API 费用；预算闸只约束直接 API 通道"},
        "history": [],
    }


def save_state(stage_dir: str, state: dict) -> str:
    p = state_path(stage_dir)
    atomic_write_json(p, state)
    return p


def load_state(stage_dir: str) -> dict | None:
    p = state_path(stage_dir)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def check_ph2(state: dict | None, current_params: dict) -> tuple[bool, str]:
    """ph2 的指纹校验：源稿/元数据/输出名/模板/核实开关任何一项变了都拒绝续跑。"""
    if not state:
        return False, "暂存目录里没有可读的 `_AI协同状态.json`"
    if int(state.get("schema") or 0) != SCHEMA_VERSION:
        return False, (f"状态 schema={state.get('schema')!r} 与本引擎（{SCHEMA_VERSION}）不符；"
                       "请重新初排")
    if state.get("phase") not in ("ph1", "ph2", "done"):
        return False, f"状态 phase={state.get('phase')!r} 不认识；请重新初排"
    old = state.get("params") or {}
    cur = dict(current_params)
    # ph2 的命令行本来就不必重复写 --verify-refs / 可选开关：没显式给就**继承** ph1 的值。
    # ⚠️ `c_class_union` **不在继承表**：它有显式的 `--no-c-class-union` 回退开关，
    #    而 argparse 的 False 与「没传」无法区分；不继承才能让「显式改口径」被检出。
    for k in ("verify_refs", "end_list_priority", "number_cite_map"):
        if not cur.get(k) and old.get(k):
            cur[k] = old[k]
    labels = {
        "source": "源稿路径", "source_sha256": "源稿 sha256",
        "metadata": "元数据路径", "metadata_sha256": "元数据 sha256",
        "out_root": "输出根目录", "stem": "输出名", "lang": "语种",
        "sty": "版面样式", "skeleton": "骨架", "verify_refs": "--verify-refs",
        "end_list_priority": "--end-list-priority", "number_cite_map": "--number-cite-map",
        "c_class_union": "C 类并集口径",
    }
    bad: list[str] = []
    for k, label in labels.items():
        if (old.get(k) if k in old else cur.get(k)) != cur.get(k):
            bad.append(f"{label}（ph1={old.get(k)!r} → 本次={cur.get(k)!r}）")
    if not bad:
        expected = state.get("fingerprint") or ""
        if expected and expected != parameter_fingerprint(old):
            bad.append("状态文件自身的参数指纹对不上（可能被手工改过）")
    if bad:
        return False, ("关键参数与初排时不一致：" + "；".join(bad)
                       + "。源稿/参数变了就必须重新初排（--coop-taskbook-only），"
                       "不能在旧暂存上续跑。")
    return True, ""


def make_stats(*, bib_total: int, bib_replied: int, low_total: int,
               low_replied: int, low_applied: int) -> dict:
    """ph2 的**机器可读**回填统计（第五十六批；写进状态 JSON 的 `stats`）。

    为什么需要它：批55 的 GUI 摘要只统计 `…_AI建议.jsonl` 的 accepted，于是真实首测里
    「文献回填 0/9、低置信 22 条里 13 条待人工」被显示成「待人工 0」——编辑以为全成了。
    这里把**任务书口径**的分子分母一次算清，界面直接读数而不是自己数文件。

    字段（schema 仍是 1，旧状态没有这一节 → 外壳按「统计不可用」回退）：
      · `bib_total` / `bib_replied` / `bib_pending`：文献条目总数 / 回填成功数 / 未回填数；
      · `low_total`：任务书「附一」里的低置信**项数**（ID 数，不是回复条数）；
      · `low_replied`：通过校验的低置信**回复条数**（同一 ID 多字段各算一条）；
      · `low_applied`：其中真正落到产物的条数（`reference.bib` 字段 / 输出 `.tex` 元数据与脚注；仅 `--coop-apply`）
      · `low_pending_manual`：仅记录、待人工的回复条数（unresolved / 不可应用 / 未勾 apply）。
    """
    bib_total = max(0, int(bib_total or 0))
    bib_replied = max(0, int(bib_replied or 0))
    low_total = max(0, int(low_total or 0))
    low_replied = max(0, int(low_replied or 0))
    low_applied = max(0, int(low_applied or 0))
    return {
        "bib_total": bib_total,
        "bib_replied": bib_replied,
        "bib_pending": max(0, bib_total - bib_replied),
        "low_total": low_total,
        "low_replied": low_replied,
        "low_applied": min(low_applied, low_replied),
        "low_pending_manual": max(0, low_replied - low_applied),
    }


def mark_state_ph2(state: dict, *, replies_digest: str, replied: list[str],
                   applied: int, rejected: int, pending: list[str],
                   stats: dict | None = None) -> dict:
    st = dict(state)
    st["phase"] = "ph2"
    st["replied"] = list(dict.fromkeys(replied))
    st["pending"] = list(pending)
    st["replies_sha256"] = replies_digest
    st["applied_low_confidence"] = int(applied)
    st["rejected"] = int(rejected)
    if stats is not None:
        # 第五十六批：只增字段、不升 schema——旧外壳忽略它，新外壳从它读「x/y」口径。
        st["stats"] = {k: int(v) for k, v in stats.items()}
    st.setdefault("history", []).append(
        {"at": _now(), "event": "ph2", "replies_sha256": replies_digest,
         "applied": int(applied), "rejected": int(rejected)})
    return st


def mark_state_done(state: dict, *, final_dir: str) -> dict:
    st = dict(state)
    st["phase"] = "done"
    st["delivered_to"] = os.path.abspath(final_dir)
    st["delivered_at"] = _now()
    st.setdefault("history", []).append(
        {"at": st["delivered_at"], "event": "promote", "final_dir": st["delivered_to"]})
    return st


def replies_digest(texts: Iterable[tuple[str, str]]) -> str:
    h = hashlib.sha256()
    for name, text in texts:
        h.update(str(name).encode("utf-8"))
        h.update(b"\0")
        h.update(str(text).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


# ── 原子 promote ─────────────────────────────────────────────────────────


def promote_stage(stage_dir: str, final_dir: str, *,
                  exclude: Iterable[str] = (STATE_NAME,),
                  rename: Callable[[str, str], None] = os.replace) -> dict:
    """把暂存目录**原子**交付到最终目录：先复制到临时名，再 rename。

    失败纪律：
      · 最终目录已存在 → 先 rename 成 `<final>.coop-bak-<时间>`，再换上新目录；
        换新失败就把旧目录 rename 回去，**旧交付目录一个字节不动**；
      · 最终目录本来不存在 → 直接 rename 临时目录；失败只清理临时目录。
    返回 `{final, backup, excluded}`；抛异常时调用方负责收口（最终目录已恢复）。
    """
    stage = os.path.abspath(stage_dir)
    final = os.path.abspath(final_dir)
    if not os.path.isdir(stage):
        raise FileNotFoundError(f"暂存目录不存在：{stage}")
    parent = os.path.dirname(final) or "."
    os.makedirs(parent, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tmp = os.path.join(parent, f".{os.path.basename(final)}.coop-tmp-{os.getpid()}-{stamp}")
    exclude = set(exclude or ())
    if os.path.exists(tmp):
        shutil.rmtree(tmp, ignore_errors=True)

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in exclude or n.startswith(f".{os.path.basename(final)}.coop-tmp-")}

    shutil.copytree(stage, tmp, ignore=_ignore)
    backup = ""
    if os.path.exists(final):
        backup = f"{final}.coop-bak-{stamp}"
        n = 1
        while os.path.exists(backup):
            n += 1
            backup = f"{final}.coop-bak-{stamp}-{n}"
        rename(final, backup)
        try:
            rename(tmp, final)
        except Exception:
            # 新目录没换成功：把旧目录原样换回去，再清理临时目录。
            rename(backup, final)
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    else:
        rename(tmp, final)
    return {"final": final, "backup": backup,
            "excluded": sorted(exclude)}


def cleanup_stage(stage_dir: str) -> bool:
    """B-211：删除已交付的暂存目录。存在且删掉返回 True；不存在/失败返回 False（**不抛**）。

    只在 `promote_stage()` **成功之后**调用（convert.py）：失败时保留暂存目录，
    免得交付没成先把编辑的回填现场清掉。
    """
    try:
        if not stage_dir or not os.path.isdir(stage_dir):
            return False
        shutil.rmtree(stage_dir)
        return True
    except Exception:  # noqa: BLE001  清理是收尾动作，任何意外都不许连累交付
        return False


# ── 预算闸（只约束直接 API 通道）─────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """保守估算法：约 3 个字符 1 token（中文偏保守，英文略高估）。"""
    return max(1, (len(text or "") + 2) // 3)


def estimate_api_budget(system: str, prompts: Iterable[str],
                        max_output_tokens: int | None) -> dict:
    """单篇直接 API 通道的预计用量（prompt + 每条输出上限 × 条数）。"""
    prompts = list(prompts)
    prompt_tokens = estimate_tokens(system) + sum(estimate_tokens(p) for p in prompts)
    out_each = int(max_output_tokens or 2048)
    completion_tokens = out_each * len(prompts)
    return {"n_calls": len(prompts), "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "max_output_tokens": out_each}


def budget_exceeded(estimate: dict, article_limit: int, batch_limit: int) -> str:
    """返回空串 = 未超限；否则返回中文说明（调用方打印后退出 2）。

    `<= 0` = 不限（与 `--verify-max 0` 同一口径）。
    """
    total = int(estimate.get("total_tokens") or 0)
    msg: list[str] = []
    if article_limit and article_limit > 0 and total > article_limit:
        msg.append(f"单篇预计 {total} tokens > --ai-budget-tokens {article_limit}")
    if batch_limit and batch_limit > 0 and total > batch_limit:
        msg.append(f"单批预计 {total} tokens > --ai-batch-budget-tokens {batch_limit}")
    if not msg:
        return ""
    return ("预算闸拦下本次直接 API 调用：" + "；".join(msg)
            + f"（{estimate.get('n_calls', 0)} 次调用，预计 prompt "
              f"{estimate.get('prompt_tokens', 0)} + completion "
              f"{estimate.get('completion_tokens', 0)} tokens）。"
              "外部 chat 通道不产生 API 费用；要放宽请显式调大预算参数（0 = 不限）。")


# ── 协同记录 ──────────────────────────────────────────────────────────────


#: 批75（B-132）：协同记录「清单缺条」节的条目来源——C 类脚注 warning 的两类子类。
_MISSING_ENTRY_SUBKINDS = ("mixed_manual", "citation_unmatched")


def _missing_entry_items(low_items: Iterable[dict] | None,
                         low_report: LowConfReport | None) -> list[dict]:
    """批75（B-132）：「清单缺条」节条目——**只用现有 C 类低置信项**（不新写解析器）。

    86-08 场景：脚注引用了某文献，但源稿文末清单里没有这一条（Hacking1999）——
    该脚注在初排里落成 `FN-<fid>`（`subkind=mixed_manual` / `citation_unmatched`）的
    低置信项。这里把它列成可见小节，并带上回填里的 `note`（note-only 也不丢）。
    无该类项 → 返回空表（记录逐字节与旧版相同）。
    """
    notes: dict[str, str] = {}
    if low_report is not None:
        for id_, sugs in (low_report.suggestions or {}).items():
            for sug in sugs or ():
                n = str((sug or {}).get("note") or "").strip()
                if n:
                    notes[str(id_)] = n
                    break
    rows: list[dict] = []
    seen: set[str] = set()
    for it in low_items or ():
        if not isinstance(it, dict):
            continue
        id_ = str(it.get("id") or "")
        m = _FN_FID_RE.match(id_)
        if not m or id_ in seen:
            continue
        if str(it.get("kind") or "") != "footnote":
            continue
        sub = str(it.get("subkind") or "")
        if sub not in _MISSING_ENTRY_SUBKINDS:
            continue
        seen.add(id_)
        rows.append({"id": id_, "fid": m.group(1), "subkind": sub,
                     "text": _clip(it.get("evidence") or "", 200),
                     "note": notes.get(id_, "")})
    return rows


# ── 批79（B-131）：交付记录口径（清单 N / 引用 M / 打印 P / 未打印 U + 原因 + 库命中 H）──
#
# 用户 2026-10-21 实测（案例 195）：交付 PDF 参考文献 19 条，用户不知道「为什么是 19」。
# 这里只做**只读汇总**：所有计数都来自转换链路已有的数据（reference.bib 的条目键、
# 产物 .tex 的引用键、状态里的低置信项、核实通道的引用库分类），**不改任何判据/产物**。
# 拿不到的计数一律 `None`（渲染成 `未测` + 一句原因），禁止估算/编造。
#
# 数据来源（单一出处，不新写解析器）：
#   · 清单 N = `reference.bib` 里闭合的条目键数（`parse_bib_blocks`，同一份解析）；
#   · 引用 M = 产物 .tex 的不同引用键（`refmark.cited_keys_from_body`，与核实范围同一口径）；
#   · 打印 P = 被正文引用且清单里有条目的键（biblatex 只打印被引条目；`\nocite{*}` 时=全清单）；
#   · 未打印 U = N − P；原因分类见 `_delivery_lines()`；
#   · 库命中 H = 核实通道 `classify_bibdb()` 的 safe 条数（ph1 写进状态，拿不到=未测）。

#: 交付记录节的固定标题（**不带中文序号**：既有节 一…七 的编号随条件顺延、必须逐字节不变；
#: 本节用固定标题，既不加号也不改动任何既有节的编号）。
DELIVERY_TITLE = "## 交付记录（清单/引用/打印/未打印）"
#: 未打印明细的行数上限（超出写「另有 n 条」；spec §4：记录 ≤25 行）。
DELIVERY_DETAIL_LIMIT = 20
#: 状态里的交付记录标记 schema（**schema 仍 1**；旧状态无该键 → 不渲染本节、记录逐字节不变）。
DELIVERY_SCHEMA = 1


def build_delivery_summary(*, bib_keys: Iterable[str] | None,
                           cited_keys: Iterable[str] | None,
                           nocite_all: bool = False,
                           low_items: Iterable[dict] | None = None,
                           bibdb_checked: bool = False,
                           bibdb_safe: int | None = None,
                           bibdb_missed: int | None = None) -> dict:
    """B-131：交付口径只读汇总（纯函数、顺序确定；不写盘、不联网）。

    `bib_keys=None` / `cited_keys=None` 表示该项拿不到 → 渲染时写 `未测`。
    `bibdb_checked=False`（本次没比对引用库）→ 库命中/库未命中都写 `未测`。
    """
    bib = (list(dict.fromkeys(str(k) for k in bib_keys))
           if bib_keys is not None else None)
    cited = ({str(k) for k in cited_keys} if cited_keys is not None else None)
    n_list = len(bib) if bib is not None else None
    n_cited = len(cited) if cited is not None else None
    printed: set[str] | None = None
    unprinted: list[str] = []
    n_missing: int | None = None
    if bib is not None and cited is not None:
        bibset = set(bib)
        printed = set(bib) if nocite_all else (cited & bibset)
        unprinted = [k for k in bib if k not in printed]
        n_missing = len(cited - bibset)
    n_printed = len(printed) if printed is not None else None
    n_unprinted = (None if (n_list is None or n_printed is None)
                   else max(0, n_list - n_printed))
    parse_fail = 0
    for it in (low_items or ()):
        if str((it or {}).get("kind") or "") == "bib_line":
            parse_fail += 1
    return {
        "n_list": n_list,
        "n_cited": n_cited,
        "n_printed": n_printed,
        "n_unprinted": n_unprinted,
        "unprinted": unprinted,
        "n_missing": n_missing,
        "nocite_all": bool(nocite_all),
        "parse_fail": parse_fail,
        "bibdb_checked": bool(bibdb_checked),
        "bibdb_safe": (int(bibdb_safe) if (bibdb_checked and bibdb_safe is not None)
                       else None),
        "bibdb_missed": (int(bibdb_missed) if (bibdb_checked and bibdb_missed is not None)
                         else None),
    }


def _delivery_num(value, reason: str = "本次拿不到该计数") -> str:
    """计数渲染：拿不到（None）→ `未测（原因）`；拿到了 → `**N**`（加粗便于扫读）。"""
    if value is None:
        return f"`未测`（{reason}）"
    return f"**{int(value)}**"


def _delivery_lines(d: dict) -> list[str]:
    """交付记录节的 markdown 行（含标题；末尾带一个空行）。**不落正文**（只有键名/计数）。"""
    L = [DELIVERY_TITLE, "",
         "> 口径：打印条数 = 被正文引用且文末清单里有条目的键"
         "（biblatex 只打印被引条目；`\\nocite{*}` 时 = 全部清单条目）。"]
    n_list = d.get("n_list")
    n_cited = d.get("n_cited")
    n_printed = d.get("n_printed")
    n_unp = d.get("n_unprinted")
    L.append(f"- 源稿文末清单：{_delivery_num(n_list)} 条")
    L.append(f"- 正文引用键：{_delivery_num(n_cited)} 个")
    L.append(f"- 打印条数：{_delivery_num(n_printed)} 条")
    if n_unp is None:
        L.append("- 未打印：`未测`（缺清单或引用键计数）")
    else:
        L.append(f"- 未打印：**{int(n_unp)}** 条（= 清单 − 打印）")
    # 未打印原因分类（每项都写出数据来源；拿不到的写未测）
    L.append("- 未打印原因（分类计数）：")
    if n_unp is None:
        L.append("  - 未被正文链路引用：`未测`（缺清单或引用键计数）")
    elif d.get("nocite_all"):
        L.append(f"  - 未被正文链路引用：**0** 条（`\\nocite{{*}}`：全部清单条目都打印）")
    else:
        L.append(f"  - 未被正文链路引用：**{int(n_unp)}** 条（明细见下）")
    n_missing = d.get("n_missing")
    if n_missing is None:
        L.append("  - 正文引用但清单无对应条目：`未测`（缺清单或引用键计数）")
    else:
        L.append(f"  - 正文引用但清单无对应条目：**{int(n_missing)}** 个键"
                 "（引用键里有、文末清单找不到条目 → 不打印）")
    L.append(f"  - 解析未识别：**{int(d.get('parse_fail') or 0)}** 行"
             "（文献区原文行未解析进清单；不计入清单条数）")
    if not d.get("bibdb_checked"):
        L.append("  - 库未命中：`未测`（本次未比对编辑部引用库）")
    elif d.get("bibdb_missed") is None:
        L.append("  - 库未命中：`未测`（核实通道未返回未命中计数）")
    else:
        L.append(f"  - 库未命中：**{int(d['bibdb_missed'])}** 条（本次核实范围内）")
    # 引用库命中 H（未调 AI）
    if d.get("bibdb_checked") and d.get("bibdb_safe") is not None:
        L.append(f"- 引用库命中：**{int(d['bibdb_safe'])}** 条（未调 AI）")
    elif not d.get("bibdb_checked"):
        L.append("- 引用库命中：`未测`（本次未比对编辑部引用库）")
    else:
        L.append("- 引用库命中：`未测`（核实通道未返回命中计数）")
    # 逐条明细（≤20 行；超出写「另有 n 条」）
    keys = list(d.get("unprinted") or [])
    if n_unp is None:
        L.append("- 未打印明细：`未测`（缺清单或引用键计数）")
    elif not keys:
        L.append("- 未打印明细：无（全部清单条目都已打印）")
    else:
        shown = keys[:DELIVERY_DETAIL_LIMIT]
        head = (f"{len(keys)} 条" if len(shown) == len(keys)
                else f"共 {len(keys)} 条，显示前 {len(shown)} 条")
        L.append(f"- 未打印明细（{head}）：")
        for k in shown:
            L.append(f"  - `{k}`（未被正文引用）")
        if len(keys) > len(shown):
            L.append(f"  - …另有 {len(keys) - len(shown)} 条（略）")
    # 库 safe 与 AI 覆盖二选一口径（可复用第128/129条措辞）
    L.append("> 口径：引用库 safe 命中只作核实证据（**不调 AI、不改 `reference.bib`**）；"
             "AI 回填只覆盖回复里出现的键——两者**二选一**，交付内容以 `reference.bib` 为准。")
    L.append("")
    return L


def render_record(*, stem: str, phase: str, parse_report: ParseReport | None,
                  low_report: LowConfReport | None, merge_summary: dict | None,
                  apply_summary: dict | None, final_dir: str,
                  no_ai_merge: bool, compiled: str, backup: str = "",
                  fulltext: dict | None = None,
                  analysis_report: FulltextAnalysisReport | None = None,
                  low_items: Iterable[dict] | None = None,
                  delivery: dict | None = None) -> str:
    L = [f"# AI 协同记录 · {stem}", "",
         f"- 阶段：`{phase}`", f"- 交付目录：`{final_dir}`", ""]
    if fulltext:
        # 第五十九批：只记模式/字数/估算/是否截断，**不落正文**（全文在任务书里）。
        _ft = (f"- 任务书附三（正文）：`{fulltext.get('mode', '')}`，"
               f"{int(fulltext.get('chars') or 0)} 字"
               f"（约 {int(fulltext.get('tokens_est') or 0)} tokens）")
        if fulltext.get("truncated"):
            _ft += "，**已截断**"
        L += [_ft, ""]
    if parse_report is not None:
        L += ["## 一、外部 chat 回填（文献字段）", "",
              f"- {parse_report.summary()}",
              f"- 已接受键：{('、'.join(sorted(parse_report.replies)) or '（无）')}"]
        if parse_report.rejected:
            L.append("- 拒绝：")
            for k, why in parse_report.rejected.items():
                L.append(f"  - `{k}`：{why}")
        if parse_report.missing:
            L.append(f"- 未回填：{'、'.join(parse_report.missing)}")
        if parse_report.unknown:
            L.append(f"- 任务书外键（已忽略）：{'、'.join(parse_report.unknown)}")
        L.append("")
    if no_ai_merge:
        L += ["> `--no-ai-merge`：本次**没有**把文献字段并入 `reference.bib`。", ""]
    if merge_summary is not None:
        L += ["## 二、reference.bib 并入", "",
              f"- 并入：**{merge_summary.get('n_merged', 0)}** 条",
              f"- 跳过：**{merge_summary.get('n_skipped', 0)}** 条",
              f"- 回滚副本：`{os.path.basename(merge_summary.get('rollback') or '') or '（未写）'}`"]
        if merge_summary.get("error"):
            L.append(f"- ⚠ {merge_summary['error']}")
        L.append("")
    if low_report is not None:
        L += ["## 三、低置信建议", "", f"- {low_report.summary()}",
              f"- 默认落点：`{stem}{SUGGESTIONS_SUFFIX}.md` / `.jsonl`（不改产物）"]
        if apply_summary is not None:
            L.append(f"- `--coop-apply`：应用 **{apply_summary.get('n_applied', 0)}** 条，"
                     f"待人工 {len(apply_summary.get('skipped') or [])} 条")
            if apply_summary.get("rollback"):
                L.append(f"- 应用前备份：`{os.path.basename(apply_summary['rollback'])}`")
            if apply_summary.get("rollback_tex"):
                L.append("- 应用前备份（.tex）：`"
                         f"{os.path.basename(apply_summary['rollback_tex'])}`")
            # 「逐条记录」：哪一条、哪个键、哪个字段、改成什么，必须写得出来。
            if apply_summary.get("applied"):
                L.append("- 已应用（逐条）：")
                for a in apply_summary["applied"]:
                    if str(a.get("target") or "") == TARGET_TEX:
                        loc = f"（定位 `{a['locator']}`）" if a.get("locator") else ""
                        L.append(f"  - `{a.get('id', '')}`（.tex `"
                                 f"{a.get('macro', '') or a.get('field', '')}`）："
                                 f"`{a.get('field', '')}` ← "
                                 f"{_code_span(a.get('value', ''), 200)}{loc}")
                    else:
                        L.append(f"  - `{a.get('id', '')}`（键 `{a.get('key', '')}`）："
                                 f"`{a.get('field', '')}` ← {_code_span(a.get('value', ''), 200)}")
            if apply_summary.get("skipped"):
                L.append("- 未应用（待人工）：")
                for a in apply_summary["skipped"]:
                    L.append(f"  - `{a.get('id', '') or a.get('key', '')}`：{a.get('reason', '')}")
            if apply_summary.get("error"):
                L.append(f"- ⚠ {apply_summary['error']}")
        L.append("")
    if compiled:
        L += ["## 四、编译", "", f"- {compiled}", ""]
    if backup:
        L += ["## 五、交付备份", "",
              f"- 旧交付目录已备份为：`{backup}`（新目录交付成功后才保留）", ""]
    # 批79（B-131）：交付记录节——**只在状态里有交付标记时出现**（旧状态 → 记录逐字节不变）。
    # 位置固定在既有编号节之后、附四/清单缺条节之前；标题不带序号，既有节编号一个都不动。
    if delivery:
        L += _delivery_lines(delivery)
    if analysis_report is not None:
        # 第六节（批74）：附四 report-only 计数；其余小节逐字节不变。
        L += ["## 六、全文分析（附四，report-only）", "",
              f"- {analysis_report.summary()}"]
        if analysis_report.total:
            L.append(f"- 报告：`{stem}{ANALYSIS_SUFFIX}.md` / `.jsonl`"
                     "（只报告，不写产物）")
        else:
            L.append("- 本次回复里没有附四 `slcn.fulltext.v1` 对象（不生成报告）")
        L.append("")
    # 批75（B-132）：清单缺条可见节——**只在有 C 类未匹配脚注项时出现**（无则记录不变）。
    _missing = _missing_entry_items(low_items, low_report)
    if _missing:
        _n = "七" if analysis_report is not None else "六"
        L += [f"## {_n}、清单缺条（脚注引用未匹配到文末清单；report-only）", "",
              "> 来源：本次初排的 C 类脚注 warning（`FN-*` 低置信项）。",
              "> 可能是**源稿文末清单缺条**（正文/脚注引用了某文献，清单里没有这一条），",
              "> 也可能只是引用写法未被识别；**只报告、不补条目**，请人工核对。", ""]
        for rec in _missing:
            L.append(f"- `{rec['id']}`（脚注 #{rec['fid']}，`{rec['subkind']}`）："
                     f"{_code_span(rec['text'], 200)}")
            if rec.get("note"):
                L.append(f"  - 回填说明：{rec['note']}")
        L.append("")
    L += ["---", "",
          "> 本记录由 `convert.py --coop-replies` 自动生成；外部 chat 通道不产生 API 费用。", ""]
    return "\n".join(L)


def write_record(stage_dir: str, **kwargs) -> str:
    p = os.path.join(stage_dir, RECORD_NAME)
    atomic_write_text(p, render_record(**kwargs))
    return p
