r"""Bib 合成候选（DESIGN §6）：旧版 O + AI 新版 N + 正文引用 C → 候选文本 + 阻断项。

为什么单独一个模块（2026-11-17，A/C/D/N 批）
--------------------------------------------
「把 AI 返回的 bib 应用到目标文件」这条路上，**判据**比**代码**重要得多。设计稿
§6 把它拆成十步，每一步都对应一种「AI 顺手改坏了学术事实」的真实失败：

  ① 语法/键唯一性：N 解析不了、键重复 → 整批阻断（写下去就是坏库）；
  ② 必保集合 R：有正文时 = 正文引用到的键 **加依赖闭包**；没有正文时 = 旧版全部条目；
  ③ 安全命中（库内已核实）的条目 **连同依赖闭包与用到的 @string 定义**保持旧版冻结；
  ④ crossref / xdata 依赖 **递归**取，要有环检测（真实样本里出现过 A→B→A）；
  ⑤ 旧版有、新版没有的必保键 → **回填旧版原文**并注明「未核实」；
  ⑥ 旧版新版都没有的必保键 → 阻断并点名（不能猜）；
  ⑦ 允许键集合在 prepare 那一刻冻结；新出现的键一律隔离成「待人工」，绝不自动并入；
  ⑧ 同名 @string 定义冲突 / 依赖解析不了 → 阻断，不猜；
  ⑨ AI 改键名不能靠顺序映射（新键按未知键处理）；
  ⑩ 报告必须把「引用闭合」「AI 核实完成」「能编译」**分开**说。

本模块是**纯函数**：只吃文本、吐文本与判据，不写文件、不联网、不调模型。
这样「判据」能被逐条断言，而「落地」由 `slcn.ai_workflow` 负责（备份/原子替换/撤销）。
"""

from __future__ import annotations

import re

from . import bibcheck, refmark
from .bibparse import _FIELD_HEAD, parse_entries

SCHEMA = "slcn.ai_bib_candidate/1"

#: 依赖字段：BibTeX 里这几种字段的值是**别的条目的键**（可逗号分隔多个）。
DEP_FIELDS = ("crossref", "xdata")
_STRING_HEAD = re.compile(r"@\s*string\s*[{(]\s*([A-Za-z][\w:+-]*)\s*=", re.I)
_STRING_BLOCK = re.compile(r"@\s*string\s*[{(]", re.I)
#: BibTeX 自带的月份缩写：裸标识符但**不需要** @string 定义。
BUILTIN_MONTHS = frozenset(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))
_BARE_IDENT = re.compile(r"^[A-Za-z][\w:+-]*$")

#: 状态标签（报告与界面共用同一批字面量，别在别处另造一套）
ST_APPLIED = "applied"        # 用 AI 新版
ST_BACKFILL = "backfilled"    # 新版没有 → 回填旧版原文（未核实）
ST_FROZEN = "frozen"          # 安全命中（含旧版依赖闭包）→ 保持旧版冻结
ST_DROPPED = "dropped"        # 未引用旧条目且 N 里没有 → 不进入新文件（摘要列出）
ST_ISOLATED = "isolated"      # 未知键 → 待人工，不并入
ST_BLOCKED = "blocked"


class Blocked(Exception):
    """整批阻断（DESIGN §6 第 ⑤⑥⑦⑧ 条）。消息直接给编辑看。"""


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _def_key(body: str) -> str:
    """`@string` 定义体的**比较形态**：去掉包裹符号、折叠空白后再比。

    只比原文会误判：`{Same Pub}` 与 `{ Same   Pub }` 是同一个定义（空白差异，
    DESIGN §6 第 ⑥ 条不该因此阻断）；而 `{X} # " Press"` 与 `{X} # " Books"`
    去掉包裹符号后仍然是 `X # Press` / `X # Books`，差异照样抓得到。
    """
    flat = re.sub(r'[{}"]', " ", str(body))
    return _norm_ws(flat)


def entries_by_key(text: str) -> tuple[dict[str, object], list[str]]:
    """解析出 {key: Entry} 与阻断原因（解析失败 / 键重复）。"""
    entries, _findings = parse_entries(text or "")
    problems: list[str] = []
    out: dict[str, object] = {}
    dups: list[str] = []
    for e in entries:
        key = (e.key or "").strip()
        if not key:
            problems.append("有条目解析不出键（多半是残缺块），不能作为合成依据。")
            continue
        if e.broken:
            problems.append(f"条目 `{key}` 解析不完整（花括号/字段残缺），不能作为合成依据。")
            continue
        if key in out:
            dups.append(key)
            continue
        out[key] = e
    if dups:
        problems.append("键重复：" + "、".join(f"`{k}`" for k in sorted(set(dups)))
                        + "（同一键只能有一条，否则编译与引用都不确定）。")
    return out, problems


def _string_body(text: str, start: int) -> tuple[int, int]:
    """从 `@string` 块起点 `start` 取 (定义体起, 定义体止)，取不到返回 (-1, -1)。

    必须按**外层块的花括号**收尾，不能一见到 `}` 就停：`@string{acm = {X} # " Press"}`
    的 `{X}` 只是定义体里的一段，见到它就停会把定义截成 `{X`（差异就丢了）。
    """
    depth = 0
    j = start
    n = len(text)
    while j < n:
        ch = text[j]
        if ch in "{(":
            depth += 1
        elif ch in ")}":
            depth -= 1
            if depth <= 0:
                return -1, -1
        elif ch == "=" and depth == 1:
            k = j + 1
            d = 1
            while k < n:
                c = text[k]
                if c in "{(":
                    d += 1
                elif c in ")}":
                    d -= 1
                    if d <= 0:
                        return j + 1, k
                k += 1
            return -1, -1
        j += 1
    return -1, -1


def _string_def_at(text: str, m) -> tuple[str, int, int]:
    """给定 `_STRING_BLOCK` 的 match，返回 (小写定义名, 定义体起, 定义体止)。"""
    head = _STRING_HEAD.match(text, m.start())
    if not head:
        return "", -1, -1
    b, e = _string_body(text, m.start())
    return head.group(1).lower(), b, e


def string_defs(text: str) -> dict[str, str]:
    """取整份文本里的 `@string{name = "…"}` 定义（用于第 ④⑤ 条的定义保护）。

    `bibparse.parse_entries()` 把 @string 当**非条目块**跳过（它只报语法问题），所以
    这里按原文再扫一遍：定义保护要的是**定义体本身**，不是「有没有这个块」。
    定义体按外层块收尾（支持 `{X} # " Press"` 这类多段连接），比较前先 `_def_key()`
    归一，避免「空白差异」被当成冲突（DESIGN §6 第 ⑥ 条只该拦**真冲突**）。
    """
    out: dict[str, str] = {}
    for m in _STRING_BLOCK.finditer(text or ""):
        name, b, e = _string_def_at(text, m)
        if not name or b < 0:
            continue
        out[name] = _def_key(text[b:e])
    return out


def dep_keys(entry_fields: dict[str, str]) -> list[str]:
    """一条条目的直接依赖键（crossref / xdata，可逗号分隔）。"""
    keys: list[str] = []
    for name in DEP_FIELDS:
        raw = entry_fields.get(name, "") if entry_fields else ""
        for part in str(raw).split(","):
            key = part.strip()
            if key and key not in keys:
                keys.append(key)
    return keys


def closure(keys, table: dict[str, object], *, label: str = "") -> tuple[set[str], list[str]]:
    """对 `table`（{key: Entry}）取依赖闭包；返回 (闭包键集合, 无解依赖说明)。

    环检测用「栈上键」而不是「已访问集合」：同一键从两条路进来是正常的 DAG，
    只有真正回到**当前路径**才叫环——环本身不算错误（BibTeX 里 crossref 环会被
    报错），但要如实记下来，不能无限递归。
    """
    seen: set[str] = set()
    unresolved: list[str] = []
    cycles: list[str] = []
    for root in keys:
        stack: list[tuple[str, list[str]]] = [(str(root), [str(root)])]
        while stack:
            key, path = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            entry = table.get(key)
            if entry is None:
                if key != str(root):
                    unresolved.append(f"`{path[0]}` 依赖的 `{key}` 在{'原' if label else ''}bib 里找不到")
                continue
            for dep in dep_keys(getattr(entry, "fields", {}) or {}):
                if dep in path:
                    cycles.append(" → ".join(path + [dep]))
                    continue
                stack.append((dep, path + [dep]))
    if cycles:
        unresolved.append("依赖成环（按 BibTeX 规则会报错，请人工确认）："
                          + "；".join(cycles[:3]))
    return seen, unresolved


def observed_cited(tex_text: str) -> tuple[set[str], bool, str]:
    """从正文取引用键：返回 (键集合, 是否 \\nocite{*}, 判定说明)。

    「正文没引用」与「解析不出引用」必须分开说（DESIGN V08）：前者是真实信息
    （可能整篇没 cite），后者是解析失败。`refmark.cited_keys_from_body()` 是同一条
    判定链的单一实现，这里只补一句人话。
    """
    if not (tex_text or "").strip():
        return set(), False, "未提供正文：按「没有可参照的引用清单」处理，旧版全部条目都算必保。"
    keys, nocite_all = refmark.cited_keys_from_body(tex_text)
    if nocite_all:
        return set(), True, "正文含 `\\nocite{*}`（语义 = 列出全部条目）：旧版全部条目都算必保。"
    if not keys:
        return set(), False, ("正文里没解析出任何 cite 键：**不是**「全部都要」也**不是**「都不要」，"
                              "按旧版全部条目必保处理；请人工确认正文是否真的没有引用。")
    return keys, False, f"正文引用 {len(keys)} 个键。"


def bare_string_refs(text: str) -> set[str]:
    """候选正文里**裸标识符**形式的字段值（= 需要 @string 定义的名字，月份除外）。

    BibTeX 的 `month = jan`、`publisher = acm` 这种裸值，要么有 `@string{acm = …}`，
    要么编译期就是未定义宏。这里必须看**原文**：`parse_entries()` 交出的字段值已经剥掉
    了花括号，`author = {A}` 与 `author = A` 会看不出区别（前者根本不需要定义）。
    """
    refs: set[str] = set()
    for e in parse_entries(text or "")[0]:
        body = getattr(e, "body", "") or ""
        for m in _FIELD_HEAD.finditer(body):
            pos = m.end()
            if pos >= len(body) or body[pos] in '{"':
                continue
            tail = body[pos:]
            name = ""
            for ch in tail:
                if ch.isalnum() or ch in "_:+-":
                    name += ch
                else:
                    break
            if name and name.lower() not in BUILTIN_MONTHS and _BARE_IDENT.match(name):
                refs.add(name.lower())
    return refs


def _string_def_text(text: str, name: str) -> str:
    """取单个 `@string{name = …}` 块原文（找不到返回空串）。"""
    low = name.lower()
    for m in _STRING_BLOCK.finditer(text or ""):
        head = _STRING_HEAD.match(text, m.start())
        if not head or head.group(1).lower() != low:
            continue
        depth = 0
        j = m.end() - 1
        n = len(text)
        while j < n:
            ch = text[j]
            if ch in "{(":
                depth += 1
            elif ch in ")}":
                depth -= 1
                if depth <= 0:
                    j += 1
                    break
            j += 1
        return text[m.start():j]
    return ""


class Candidate:
    """合成结果。`text` 为 None 表示被阻断（一个字节都不能写）。"""

    def __init__(self) -> None:
        self.text: str | None = None
        self.items: list[dict] = []
        self.blocked: list[str] = []
        self.notes: list[str] = []
        self.unknown: list[str] = []
        self.missing: list[str] = []
        self.closure_closed: bool = False
        self.n_must_applied: int = 0   # 必保键里由 N 提供的条数（摘要要扣掉新条目算法）
        self.cited: list[str] = []
        self.safe_hit: list[str] = []

    # ── 统计（界面/报告共用；不要在这里编「全绿」） ──
    def count(self, status: str) -> int:
        return sum(1 for it in self.items if it.get("status") == status)

    def dropped_keys(self) -> list[str]:
        """`ST_DROPPED` 的键（按出现顺序）。RULES §6 第 9 条要求**必须列出来**。"""
        return [str(it.get("key") or "") for it in self.items
                if it.get("status") == ST_DROPPED and str(it.get("key") or "")]

    def summary_lines(self) -> list[str]:
        lines = ["**引用闭合**：%s" % ("✅ 正文引用键都在候选里" if self.closure_closed
                                       else "❗ 未闭合（见下）")]
        # RULES §6 第 9 条：未引用的旧条目允许不进入候选，但**必须列出来**，
        # 不许只给一个计数就静默丢弃（验收 D1）。键名跟在计数后面，仍在同一行，
        # 保持「三类结论恰好 3 行」的口径不变。
        dropped = self.dropped_keys()
        dropped_note = ("（键：" + "、".join("`%s`" % k for k in dropped) + "）"
                        if dropped else "")
        lines.append("**AI 核实完成**：用 AI 新版 %d 条（含新条目 %d 条）；回填旧版 %d 条"
                     "（记「保留旧版，未完成本次核查」）；安全命中冻结旧版 %d 条；待人工 %d 条；"
                     "未引用未保留 %d 条%s；阻断 %d 条"
                     % (self.count(ST_APPLIED), self.count(ST_APPLIED) - self.n_must_applied,
                        self.count(ST_BACKFILL), self.count(ST_FROZEN),
                        self.count(ST_ISOLATED), self.count(ST_DROPPED), dropped_note,
                        len(self.blocked)))
        lines.append("**能编译**：本条目不判断——是否可编译以实际编译结果为准（不在此处声称）。")
        return lines


def _render_entry(text: str, key: str) -> tuple[str, int]:
    """取一条条目在原文里的**原字节**（含其上方紧邻注释块）。返回 (片段, 起偏移)。"""
    entries, _ = parse_entries(text or "")
    for e in entries:
        if (e.key or "").strip() == key:
            start = min(e.start, e.block_start) if e.block_start else e.start
            return text[start:e.end + 1], start
    return "", -1


def build_candidate(
    *,
    old_text: str,
    new_text: str,
    tex_text: str = "",
    safe_hit_keys=(),
    allowed_keys=None,
) -> Candidate:
    """按 DESIGN §6 生成候选。

    `old_text` = 目标 bib（冻结的旧版，**只读**）；`new_text` = AI 返回的 bib；
    `tex_text` = 正文（可空；空 = 没有可参照的引用清单 → 旧版全部必保）；
    `safe_hit_keys` = 库内已核实、未送 AI 的键（保持旧版冻结）；
    `allowed_keys` = prepare 时冻结的允许键集合（None = 用旧版键集合推算）。
    """
    cand = Candidate()
    old, old_bad = entries_by_key(old_text)
    new, new_bad = entries_by_key(new_text)
    # 同一条残缺条目在旧/新版都会报一次；去重但保序（不猜，也不重复刷屏）
    problems = list(dict.fromkeys(list(old_bad) + list(new_bad)))
    if problems:
        cand.blocked.extend(problems)
        return cand

    cited, nocite_all, cited_note = observed_cited(tex_text)
    cand.notes.append(cited_note)
    cand.cited = sorted(cited)

    # ② 必保集合 R（含依赖闭包）
    #   `\nocite{*}` = 旧版全部条目；没有正文 = 旧版全部条目必保；
    #   否则 R = 正文引用的**全部**键——**不能**与「旧版 ∪ 新版」取交集：
    #   正文引了、但两版都没有的键（悬空引用）必须留在 R 里，才能走到下面
    #   `cand.missing` 去阻断；取交集会把它悄悄丢掉，摘要还谎称「引用闭合」。
    if cited or nocite_all:
        roots = set(old) if nocite_all else set(cited)
    else:
        roots = set(old)
    must, must_bad = closure(roots, old)
    cand.blocked.extend(must_bad)

    # ③ 安全命中：旧版冻结 + 依赖闭包（旧版里取）
    safe = {str(k) for k in (safe_hit_keys or ())}
    safe_closure, safe_bad = closure(safe, old)
    cand.safe_hit = sorted(safe & set(old))
    cand.blocked.extend(safe_bad)

    # ⑦ 允许键集合（prepare 冻结；未给则按旧版 ∪ 正文引用推算）
    #   必保/安全命中**优先于**允许键集合：调用方给的 allowed_keys 若窄到连必保键
    #   都不含，那不是「未知新键」，是调用方口径不一致——按 §6 第 ② 条，必保键
    #   必须留；同一个键绝不能既记「待人工」又写进候选（一个键一个终态）。
    if allowed_keys is None:
        allowed = set(old) | (cited - set(old))
    else:
        allowed = {str(k) for k in allowed_keys}
    protected_keys = set(must) | (safe_closure & set(old))
    unknown = sorted(set(new) - allowed - protected_keys)
    cand.unknown = unknown
    for key in unknown:
        cand.items.append({"key": key, "status": ST_ISOLATED,
                           "reason": "AI 回复里出现的新键（不在冻结的允许键集合里）——待人工，不自动并入"})

    # ⑤⑥ 逐条决定（DESIGN §6 第 2/3 条）：
    #   必保 R = 「必须**在**」的键 → N 里有就用 N（AI 结果），N 里没有才回填 O 原文；
    #   安全命中集 = 「**冻结旧版**」→ 连同旧版依赖闭包，一律忽略 AI 对此的改动；
    #   其余 = 用 N；A 外的新键 = 待人工。
    frozen = safe_closure & set(old)
    for key in sorted(frozen):
        cand.items.append({"key": key, "status": ST_FROZEN,
                           "reason": "库内已核实（safe hit），连同旧版依赖闭包保持旧版；不采纳 AI 对此的改动"})
    for key in sorted(must - frozen):
        if key in new:
            cand.n_must_applied += 1
            cand.items.append({"key": key, "status": ST_APPLIED,
                               "reason": "必保键（正文引用/依赖闭包）：N 里有，用 AI 结果"})
        elif key in old:
            cand.items.append({"key": key, "status": ST_BACKFILL,
                               "reason": "必保键在 N 里缺失（AI 漏交或未返回）——回填旧版原文，"
                                         "记「保留旧版，未完成本次核查」"})
        else:
            cand.missing.append(key)
    if cand.missing:
        cand.blocked.append("这些必保键在**旧版与新版里都没有**：" +
                            "、".join(f"`{k}`" for k in cand.missing) +
                            "——不能猜，请人工补或改用别的回复。")

    # ⑧ @string 定义冲突：同名不同体 → 阻断（不猜合并）
    old_defs = string_defs(old_text)
    new_defs = string_defs(new_text)
    conflicts = sorted(n for n in set(old_defs) & set(new_defs) if old_defs[n] != new_defs[n])
    if conflicts:
        cand.blocked.append("同名 `@string` 定义冲突（不猜，保持阻断）：" +
                            "、".join(f"`{n}`" for n in conflicts))

    # 其余键（非必保、非安全、非未知）→ 用 N
    decided = {it["key"] for it in cand.items}
    for key in sorted(set(new)):
        if key in decided or key in unknown:
            continue
        cand.items.append({"key": key, "status": ST_APPLIED,
                           "reason": "AI 核实结果"})
    decided = {it["key"] for it in cand.items}
    # 未引用旧条目：N 里保留就用 N；N 里没有的按 §6 第 9 条不进入新文件，但要在摘要里列出
    for key in sorted(set(old)):
        if key in decided or key in unknown:
            continue
        if key in new:
            cand.items.append({"key": key, "status": ST_APPLIED,
                               "reason": "未引用旧条目：N 里保留，用 AI 结果"})
        else:
            cand.items.append({"key": key, "status": ST_DROPPED,
                               "reason": "未引用的旧条目且 N 里没有——按 §6 第 9 条不进入新文件（摘要列出）"})

    if cand.blocked:
        return cand

    # 组装：旧版原文顺序为主（保持引用图书目顺序稳定），新键按新版的相对顺序追加
    parts: list[str] = []
    old_order = []
    for e in parse_entries(old_text or "")[0]:
        k = (e.key or "").strip()
        if k and k not in old_order:
            old_order.append(k)
    new_order = []
    for e in parse_entries(new_text or "")[0]:
        k = (e.key or "").strip()
        if k and k not in new_order:
            new_order.append(k)

    status_by_key = {}
    for it in cand.items:
        status_by_key.setdefault(it["key"], it["status"])

    # 定义保护（§6 第 6 条）在拼好正文之后做：只带**实际用到**的 @string 定义。

    for key in old_order:
        st = status_by_key.get(key)
        if st is None or st == ST_DROPPED:
            continue
        if st in (ST_FROZEN,):
            frag, _ = _render_entry(old_text, key)
        elif st in (ST_APPLIED,):
            frag, _ = _render_entry(new_text, key)
        else:
            frag, _ = _render_entry(old_text, key)
        if frag:
            parts.append(frag.rstrip("\n"))
    for key in new_order:
        if key in old_order:
            continue
        st = status_by_key.get(key)
        if st in (ST_APPLIED, ST_BACKFILL):
            frag, _ = _render_entry(new_text, key)
            if frag:
                parts.append(frag.rstrip("\n"))

    body = "\n\n".join(parts)
    used = bare_string_refs(body)
    old_defs, new_defs = string_defs(old_text), string_defs(new_text)
    missing_defs = sorted(n for n in used if n not in old_defs and n not in new_defs)
    if missing_defs:
        cand.blocked.append("候选用了未定义的 `@string` 名字（写了也编译不过，不猜）：" +
                            "、".join(f"`{n}`" for n in missing_defs))
        cand.text = None
        return cand
    if used:
        head = []
        for name in sorted(used):
            # 受保护闭包优先：旧版有定义就用旧版原文
            frag = _string_def_text(old_text, name) or _string_def_text(new_text, name)
            if frag:
                head.append(frag.rstrip("\n"))
        body = "\n".join(head) + ("\n\n" + body if body else "")

    cand.text = body + "\n"
    # 引用闭合：必保键都在候选里、且正文引用键也都在
    have = set(entries_by_key(cand.text)[0])
    need = set(must) | (set() if nocite_all else (cited & (set(old) | set(new))))
    missing_after = sorted(need - have)
    cand.closure_closed = not missing_after
    if missing_after:
        cand.blocked.append("合成后仍缺这些键（未闭合）：" +
                            "、".join(f"`{k}`" for k in missing_after))
        cand.text = None
    return cand


def _string_block(text: str) -> str:
    """取整份文本里所有 `@string{…}` 块的原文（定义保护：旧版用过就整块保留）。"""
    out: list[str] = []
    for m in _STRING_BLOCK.finditer(text or ""):
        depth = 0
        j = m.end() - 1
        n = len(text)
        while j < n:
            ch = text[j]
            if ch in "{(":
                depth += 1
            elif ch in ")}":
                depth -= 1
                if depth <= 0:
                    j += 1
                    break
            j += 1
        out.append(text[m.start():j])
    return "\n".join(out)


def check_new_bib_only(text: str) -> tuple[bool, list[str]]:
    """纯 bib 粘贴的最小校验：能解析、键唯一、没有残缺条目。

    「只导入 bib」不能声称别的类别也完成了（DESIGN §5/V03）——所以这里只回
    与 bib 文本本身有关的事实。
    """
    table, problems = entries_by_key(text)
    if problems:
        return False, problems
    if not table:
        return False, ["粘贴内容里没有解析出任何条目。"]
    return True, []


def _cited_for_report(text: str) -> str:
    keys, _nocite = refmark.cited_keys_from_body(text or "")
    return "、".join(sorted(keys)[:8]) if keys else "（无）"


__all__ = [
    "SCHEMA", "Blocked", "Candidate", "build_candidate", "check_new_bib_only",
    "closure", "dep_keys", "entries_by_key", "observed_cited", "string_defs",
    "ST_APPLIED", "ST_BACKFILL", "ST_BLOCKED", "ST_DROPPED", "ST_FROZEN", "ST_ISOLATED",
]
