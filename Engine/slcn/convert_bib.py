"""SLCN convert 拆分（B-229）：bib 发现/附件/指纹/引用库 + ⑪ 核实主体。纯搬运自 v1.4.0 `Engine/convert.py`；另含从 `main` 外提的`run_refs_attach()`/`run_verify_refs()`。"""
from __future__ import annotations

import os, re

from slcn import aibib as _aibib, bibcheck as _bibcheck, bibdb as _bibdb
from slcn import coop as _coop, paths as _paths, refmark as _refmark, search as _search
from slcn.convert_metadata import TEX_EXT, die
from slcn.convert_stage import (
    LLM_FULLTEXT_CHUNK_CHARS, LLM_FULLTEXT_REPORT_SUFFIX, _FINISH_WHY,
    _fulltext_chunk_items, _llm_fulltext_review, _llm_fulltext_system,
    _llm_fulltext_user, apply_ai_merge, channel_provenance,
    render_llm_fulltext_report, split_fulltext_chunks,
)


# ── 参考文献「内容指纹」：跨来源去重用 ────────────────────────────────────
#
# 同一份文献可能从两条通道进来（正文文末的编号清单 / 参考文献附件），**键却不同**
# （`Beall2000` vs `a1`），只按键去重挡不住。实测 02 Lili_Zhao：正文自带 19 条
# 编号清单、附件又是同一份，于是 bib 翻倍成 38 条。
_NORM_RE = re.compile(r"[\W_]+")


def _ref_fingerprint(author: str, title: str, year: str) -> tuple[str, str, str]:
    """作者 + 年份 + 题名的归一化指纹（去掉全部标点与空白，大小写无关）。"""
    def norm(s: str) -> str:
        return _NORM_RE.sub("", (s or "").lower())

    return (norm(author), norm(year), norm(title))


def _bib_fingerprints(bib_text: str) -> set[tuple[str, str, str]]:
    """从已有 .bib 文本里取出每条目的内容指纹。"""
    if not bib_text.strip():
        return set()
    from slcn.bibcheck import fields_of, split_entries

    out = set()
    for _etype, _key, body in split_entries(bib_text):
        f = fields_of(body)
        fp = _ref_fingerprint(f.get("author", ""), f.get("title", ""), f.get("year", ""))
        if fp != ("", "", ""):
            out.add(fp)
    return out


# ── 参考文献定位（仅在同目录下查找，不做跨目录搜索） ──────────────────────


def _bib_candidates(directory: str) -> list[str]:
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [os.path.join(directory, f) for f in names if f.lower().endswith(".bib")]


def _pick_bib(cands: list[str]) -> str | None:
    if not cands:
        return None
    ref = [c for c in cands if os.path.basename(c).lower() == "reference.bib"]
    return (ref or cands)[0]


def bib_search_dirs(source: str) -> list[tuple[str, str]]:
    """`.bib` 的保守搜索链：源稿同目录 → 父目录（稿件根，限 1 层）。

    第四十五批 A2（89-05 Zhen_Liang 实测）：源稿在 `original/` 子目录里，而
    `reference.bib` 在**稿件根目录**（上一级）——旧实现只搜源目录 → 无 bib、
    15 个键全悬空、30 处 undefined citation。只加一层父目录，不做递归搜索：
    `.bib` 是作者/编辑明确放在稿件根的文件，跨目录乱搜会认错别人的 bib。
    """
    d = os.path.dirname(os.path.abspath(source))
    out = [(d, "源稿同目录")]
    parent = os.path.dirname(d)
    if parent and parent != d:
        out.append((parent, "源稿父目录（稿件根，限 1 层）"))
    return out


def find_bib_ex(source: str, explicit: str | None) -> tuple[str | None, str]:
    """同 `find_bib()`，额外返回命中来源（日志用）。"""
    if explicit:
        p = os.path.abspath(explicit)
        if not os.path.exists(p):
            die(f"--bib 指定的文件不存在：{p}")
        return p, "显式 --bib"
    for directory, label in bib_search_dirs(source):
        hit = _pick_bib(_bib_candidates(directory))
        if hit:
            return hit, label
    return None, ""


def find_bib(source: str, explicit: str | None) -> str | None:
    return find_bib_ex(source, explicit)[0]


def bib_origin(source: str, path: str | None) -> str:
    """bib 路径 → 人话来源（源稿同目录 / 父目录 / 源内声明 / 其它）。"""
    if not path:
        return ""
    d = os.path.dirname(os.path.abspath(source))
    pd = os.path.dirname(os.path.abspath(path))
    if pd == d:
        return "源稿同目录"
    if pd == os.path.dirname(d):
        return "源稿父目录（稿件根，限 1 层）"
    return "源内声明 / 显式指定"


# ── 第 134 条（2026-10-18）：参考文献附件的「选择 / 校验 / 合并」 ─────────────
#
# 真实稿 2023(71)04：convert ⑦ 自动把 `预处理/用于上传text2bib.txt`（text2bib 的中间
# 产物——两条文献挤一行、含 `\href{a}{b}`）当参考文献附件并入 → 产物出现
# `title = {...}{...}` 嵌套花括号 → biber 行级语法错、**整份 bib 报废**（19 页 vs 20 页）。
# 修法三条（保守、可解释）：
#   ① 有「真 .bib」就**不自动**合并附件：--bib / 源里 \addbibresource / \bibliography
#      指向且文件存在 / 同目录自动发现的 .bib（find_bib）；
#   ② 没有真 bib 时，候选附件先过结构校验（`multisource.select_refs_attachment()`）：
#      真 BibTeX 优先，纯文本清单次之，不合格的候选跳过并在日志写明原因；
#   ③ 合并时对 BibTeX 附件按条目原文直入（按键 + 内容指纹去重），对文本清单沿用
#      footref 解析，但逐条做「渲染后结构校验」，不合格的条目不写进产物。
_DECL_BIBADD_RE = re.compile(
    r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"
)
_DECL_BIBLIO_RE = re.compile(r"\\bibliography\s*\{([^}]+)\}")


def declared_bib_path(source: str) -> str | None:
    """LaTeX 源里 `\\addbibresource` / `\\bibliography` 声明且**文件确实存在**的 .bib。"""
    if os.path.splitext(source)[1].lower() not in TEX_EXT:
        return None
    try:
        text = open(source, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    src_dir = os.path.dirname(os.path.abspath(source))
    names: list[str] = []
    for m in _DECL_BIBADD_RE.finditer(text):
        names.append(m.group(1).strip())
    for m in _DECL_BIBLIO_RE.finditer(text):
        names.extend(x.strip() for x in m.group(1).split(",") if x.strip())
    for name in names:
        if not name:
            continue
        cand = name if os.path.splitext(name)[1] else name + ".bib"
        # 第四十五批 A2：源稿在 original/ 子目录、bib 在稿件根时也能找到（与 find_bib 同口径）
        for directory in (src_dir, os.path.dirname(os.path.abspath(src_dir))):
            path = os.path.join(directory, cand)
            if os.path.isfile(path):
                return path
    return None


def real_bib_path(source: str, explicit_bib: str | None = None) -> str | None:
    """「真 .bib」判定（第 134 条）：决定**不要**自动合并参考文献附件。

    优先级：`--bib` 显式指定 → LaTeX 源里的 `\\addbibresource` / `\\bibliography`
    （文件存在）→ 同目录自动发现（`find_bib(source, None)`，如 `reference.bib`）。
    Word 通道不走这条（它的 bib 由文档内容生成，旧行为不变）。
    """
    if os.path.splitext(source)[1].lower() not in TEX_EXT:
        return None
    if explicit_bib:
        p = os.path.abspath(explicit_bib)
        if os.path.isfile(p):
            return p
    declared = declared_bib_path(source)
    if declared:
        return declared
    return find_bib(source, None)


def bib_entry_structurally_safe(entry: str) -> bool:
    """渲染后的 BibTeX 条目最低结构校验（第 134 条）。

    花括号配平、字段区无相邻 `}{`——71/04 的坏条目就是 `title = {A}{B}` 这种形状
    （biber: "found {...}, expected end of entry"）。
    """
    if "}{" in entry:
        return False
    depth = 0
    for ch in entry:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def bib_attachment_entries(text: str) -> list[tuple[str, str, str, str]]:
    """把 BibTeX 附件拆成 [(类型, 键, 字段体, 条目原文)]。

    第 134 条：条目按**原文直入**（不改写作者字段/顺序），只做去重与结构校验。
    """
    from slcn.bibnorm import split_entries as split_raw

    out: list[tuple[str, str, str, str]] = []
    for head, fields, tail in split_raw(text):
        m = re.match(r"@(\w+)\s*\{\s*([^,]+)\s*,", head)
        if not m:
            continue
        key = m.group(2).strip()
        if not key:
            continue
        out.append((m.group(1), key, fields, head + fields + tail))
    return out


def merge_bib_attachment(bibp: str, have: str, bib_text: str) -> tuple[int, int, int]:
    """把 BibTeX 附件按条目原文并入 `bibp`；返回 (新增, 重复跳过, 结构不合格跳过)。

    第 134 条：按键 + 「作者+年份+题名」内容指纹去重；渲染后结构校验不合格的条目不写
    （错误条目会让 biber 报语法错、**整份 bib 报废**——71/04 的教训）。
    """
    from slcn.bibcheck import fields_of as _fields_of
    from slcn.bibcheck import split_entries as _split3

    have = have or ""
    have_keys = {k for _t, k, _b in _split3(have)} if have else set()
    seen = _bib_fingerprints(have) if have else set()
    added: list[str] = []
    skipped_dup = 0
    skipped_bad = 0
    for _etype, key, fields, raw in bib_attachment_entries(bib_text):
        if key in have_keys:
            skipped_dup += 1
            continue
        if not bib_entry_structurally_safe(raw):
            skipped_bad += 1
            continue
        f = _fields_of(fields)
        fp = _ref_fingerprint(f.get("author", ""), f.get("title", ""), f.get("year", ""))
        if fp != ("", "", "") and fp in seen:
            skipped_dup += 1
            continue
        seen.add(fp)
        have_keys.add(key)
        added.append(raw if raw.endswith("}") else raw + "}")
    if added:
        with open(bibp, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n% ── 由参考文献附件合并 ──\n" + "\n".join(added) + "\n")
    return len(added), skipped_dup, skipped_bad


# ── 编辑部引用库（bib_db）接入（第 129 条，2026-10-18）──────────────────────
#
# 目标：**命中已核实条目 → 该键不调 Search API、不调 AI**（省钱 + 体例统一 + 可审计）。
# 但判定必须**保守**（把不同文献误判成「已核实」比漏查更糟）：
#   · 安全命中 = DOI 精确命中已核实记录；或题名高置信（bibdb 的 0.90 阈值）+ 第一作者姓 /
#     年份相容 + verified=true；
#   · key 命中只算**候选**（BibTeX key 多为 per-paper，同 key 不保证同文献），仍走检索；
#   · 安全命中只跳过「检索/AI」，**绝不改 reference.bib**；字段差异只写进任务书提示。
# 判定实现在 `slcn/bibdb.py: Store.match_reference()`（只有一份），这里只做开关解析、
# 逐条编排与打印——与将来的其他调用方共用同一份判定，不写第二遍。


def resolve_bibdb(args):
    """解析引用库开关 → `(store|None, 库路径, 告警文本)`。

    口径（第 129 条）：
      · `--no-bibdb` → 明确关闭（本次一律不查库；输出与接入前逐字节相同）；
      · `--bibdb-path PATH` → 用指定库（隐含启用；指错/读不了 → 告警，**不中断出稿**）；
      · `--bibdb` → 启用默认库（`slcn.bibdb.default_db_path()`）；
      · **默认（不传以上参数）**：执行核实时默认库存在就自动启用；笔记仓/库不存在时
        **静默跳过**（公开仓 / CI / 没建库的机器上行为零变化），只有显式要求
        （`--bibdb` / `--bibdb-path`）才告警。
    """
    if getattr(args, "no_bibdb", False):
        return None, "", ""
    explicit = bool(getattr(args, "bibdb", False) or getattr(args, "bibdb_path", ""))
    path = str(getattr(args, "bibdb_path", "") or "")
    if not path:
        try:
            path = _bibdb.default_db_path()
        except _bibdb.BibDBError as exc:
            if explicit:
                return None, "", f"引用库不可用：{exc}"
            return None, "", ""
    if not os.path.isfile(path):
        if explicit:
            return None, path, f"引用库文件不存在：{path}——本次不查库（核实按原流程）"
        return None, path, ""
    try:
        store = _bibdb.Store.load(path)
    except _bibdb.BibDBError as exc:
        if explicit:
            return None, path, f"引用库读取失败：{exc}——本次不查库（核实按原流程）"
        return None, path, ""
    return store, path, ""


def classify_bibdb(store, entries):
    """对本次核实范围内的条目逐条查库 → `(安全命中, 候选, 未命中 keys)`（第 129 条）。

    查库是**附加通道**：任何意外都不许连累出稿——异常一律按「未命中」处理。
    """
    safe: dict[str, dict] = {}
    candidates: dict[str, dict] = {}
    missed: list[str] = []
    for etype, key, body in entries:
        try:
            match = store.match_reference(_bibcheck.fields_of(body), key=key,
                                          entry_type=etype)
        except Exception:  # noqa: BLE001  附加通道，任何意外都不许连累出稿
            missed.append(key)
            continue
        if match.get("status") == _bibdb.HIT_SAFE:
            safe[key] = match
        elif match.get("status") == _bibdb.HIT_CANDIDATE:
            candidates[key] = match
        else:
            missed.append(key)
    return safe, candidates, missed


def bibdb_log_line(safe, candidates, missed, store, path) -> str:
    """跑前那一行「引用库：命中 X / 候选 Y / 未命中 Z（库 n 条，path）」（第 129 条）。"""
    try:
        n_records = len(store.records())
    except Exception:  # noqa: BLE001
        n_records = 0
    return (f"引用库：命中 {len(safe)} 条已核实（跳过检索+AI）、"
            f"候选 {len(candidates)}、未命中 {len(missed)}"
            f"（库 {n_records} 条，{path}）")


def _bibdb_scan_stem(res) -> str:
    """产物 stem（核对清单文件名用；拿不到就空串 → 用通用名）。"""
    tex = str(getattr(res, "tex_path", "") or "")
    return os.path.splitext(os.path.basename(tex))[0] if tex else ""


def bibdb_readonly_scan(args, res) -> dict:
    """初排转换**默认**的引用库通道：比对 + **默认回填**（B-217 ⑧）。

    Lead 2026-10-20 拍板「默认开只读比对」；用户 2026-10-24 拍板升级为**默认回填**：
      · 默认**开**（无新参数时）；`--no-bibdb-verify` / `--no-bibdb` 完全关闭；
      · `--no-bibdb-apply` 关闭回填 → 只读比对（日志/产物**逐字节等于旧行为**）；
      · 回填只对 HIT_SAFE 命中、只补缺失字段（`slcn/bibdbapply.py` 单一实现），
        候选/未命中一律不动（不臆造），before/after 清单追加进「参考文献核对.txt」；
      · `--verify-refs` / `--verify-plan` 不做（核实分支自己查库并打印明细）；
      · 无 `reference.bib` / 无条目 / 库不存在 / 读库失败 → **完全静默**
        （公开仓、CI、没建库的机器上日志/产物零变化）。

    返回 `{done, store, path, entries, safe, candidates, missed, applied, filled,
    checklist}`（测试/调试用；默认回填关闭时 applied 为空）。
    """
    out = {"done": False, "store": None, "path": "", "entries": [],
           "safe": {}, "candidates": {}, "missed": [],
           "applied": [], "filled": 0, "checklist": ""}
    if getattr(args, "no_bibdb_verify", False) or getattr(args, "no_bibdb", False):
        return out
    try:
        bib_path = str(getattr(res, "bib_path", "") or "")
        if not bib_path or not os.path.isfile(bib_path):
            return out
        from slcn.bibcheck import split_entries

        with open(bib_path, encoding="utf-8", errors="replace") as f:
            bib_text = f.read()
        entries = split_entries(bib_text)
        if not entries:
            return out
        store = getattr(res, "_bibdb_store_shared", None)
        if store is not None:
            path, _warning = getattr(store, "path", ""), ""
        else:
            store, path, _warning = resolve_bibdb(args)
        if store is None:
            return out
        safe, candidates, missed = classify_bibdb(store, entries)
        applied: list[dict] = []
        if not getattr(args, "no_bibdb_apply", False) and safe:
            applied = _bibdb_apply_backfill(bib_path, bib_text, store, entries, safe)
        if applied:
            n_items = sum(len(a.get("filled") or {}) for a in applied)
            stem = _bibdb_scan_stem(res)
            print(f"   引用库比对：命中 {len(safe)} / 候选 {len(candidates)} / "
                  f"未命中 {len(missed)}（默认回填 {len(applied)} 条 / {n_items} 项，"
                  "候选与未命中不动）", flush=True)
            for a in applied:
                detail = "、".join(f"{k}=（缺失）→ {v}"
                                   for k, v in (a.get("filled") or {}).items())
                print(f"     · [{a.get('key')}] {detail}", flush=True)
            try:
                from slcn import bibdbapply
                out["checklist"] = bibdbapply.write_checklist(
                    os.path.dirname(bib_path), stem, applied)
                print(f"     回填核对清单：{os.path.basename(out['checklist'])}", flush=True)
            except Exception:  # noqa: BLE001  清单写不了不影响已回填的 bib
                out["checklist"] = ""
        else:
            print(f"   引用库比对：命中 {len(safe)} / 候选 {len(candidates)} / "
                  f"未命中 {len(missed)}（只读，不改产物）", flush=True)
        out.update(done=True, store=store, path=path, entries=entries,
                   safe=safe, candidates=candidates, missed=missed,
                   applied=applied,
                   filled=sum(len(a.get("filled") or {}) for a in applied))
    except Exception:  # noqa: BLE001  附加通道：任何意外都不许连累出稿
        return out
    return out


def _bibdb_apply_backfill(bib_path: str, bib_text: str, store, entries,
                          safe: dict) -> list[dict]:
    """默认回填（B-217 ⑧）：只补缺失字段 + 原子写回；任何结构异常 → 不写（fail-soft）。"""
    try:
        from slcn import bibdbapply
        from slcn.fsutil import atomic_write

        plans = bibdbapply.plan_fills(store, entries, safe)
        if not plans:
            return []
        new_text, applied = bibdbapply.apply_text(bib_text, plans)
        if not applied or new_text == bib_text:
            return []
        atomic_write(bib_path, new_text, newline="\n", log=None)
        return applied
    except Exception:  # noqa: BLE001  附加通道：任何意外都不许连累出稿
        return []


def bibdb_taskbook_hits(entries, safe) -> list[dict]:
    """任务书「五、库内已核实（无需再查）」的条目（按逐条清单顺序，不手抄统计）。"""
    return [safe[key] for _etype, key, _body in entries if key in safe]



def resolve_bibdb_apply_store(args):
    """B-220 N6：短引库解析用的只读 Store（与默认回填共用同一组开关）。

    任一回填/查库开关关闭（`--no-bibdb` / `--no-bibdb-verify` / `--no-bibdb-apply`）
    或库不可用 → 返回 ``None``（默认零变化：短引仍按原路径保留脚注 + 人工核对）。
    """
    if (getattr(args, "no_bibdb_verify", False)
            or getattr(args, "no_bibdb", False)
            or getattr(args, "no_bibdb_apply", False)):
        return None
    try:
        store, _path, _warning = resolve_bibdb(args)
    except Exception:  # noqa: BLE001  附加通道：库不可用不阻断出稿
        return None
    return store

def run_refs_attach(args, res, *, source, out_dir, stem, refs_auto):
    """⑦ 合并参考文献附件（B-229：从 `main` 纯搬运外提；语句逐字/顺序不变；返回 res）。"""
    from slcn import footref
    from slcn.multisource import (
        _docx_text,
        attachment_kind,
        bib_attachment_issue,
        text_attachment_issue,
    )

    print("\n⑦ 合并参考文献附件")
    _real = real_bib_path(source, args.bib)
    if refs_auto and _real:
        print(
            "   · 跳过自动参考文献附件：已有真 .bib"
            f"（{_paths.rel(_real, os.path.dirname(os.path.abspath(source)))}）"
            "——不盲目合并；确实要合并请显式 --refs"
        )
    else:
        txt = ""
        if args.refs.lower().endswith((".docx", ".doc")):
            txt, _ = _docx_text(args.refs)
        else:
            try:
                txt = open(args.refs, encoding="utf-8", errors="replace").read()
            except OSError:
                txt = ""
        _kind = attachment_kind(args.refs, txt)
        _issue = (
            bib_attachment_issue(txt) if _kind == "bib"
            else text_attachment_issue(txt)
        )
        if _issue:
            print(
                f"   ⚠ 跳过参考文献附件 {os.path.basename(args.refs)}：{_issue}"
                "（不硬并坏附件；可整理后再用 --refs 指定）"
            )
        elif _kind == "bib":
            # 真 BibTeX 附件：按条目原文直入（键 + 内容指纹去重）
            bibp = res.bib_path or os.path.join(out_dir, f"{stem}.bib")
            have = ""
            if os.path.exists(bibp):
                have = open(bibp, encoding="utf-8", errors="replace").read()
            elif not res.bib_path:
                with open(bibp, "w", encoding="utf-8", newline="\n") as f:
                    f.write("% 由参考文献附件生成\n\n")
                res = res.__class__(**{**res.__dict__, "bib_path": bibp})
            added, skipped_dup, skipped_bad = merge_bib_attachment(bibp, have, txt)
            if added:
                print(f"   从附件合并 {added} 条（BibTeX 直入）→ {os.path.basename(bibp)}")
            if skipped_dup:
                print(f"   （另有 {skipped_dup} 条与现有 bib 重复（键/内容指纹），已跳过）")
            if skipped_bad:
                print(f"   ⚠ 另有 {skipped_bad} 条结构不合格（嵌套花括号等），未写入")
            if not (added or skipped_dup or skipped_bad):
                print("   附件条目与现有 bib 无新增")
        else:
            # 纯文本清单：编号清单（`[1] 作者, 年, "题名", 刊名, 卷(期): 页`）优先；
            # 否则按「整条 in note」的脚注体例处理
            items = footref.parse_numbered_list(txt)
            if not items:
                items = footref.parse_note(txt)
            if items:
                # 正文没解析出参考文献时工具不会建 bib，这里补建一个
                bibp = res.bib_path or os.path.join(out_dir, f"{stem}.bib")
                have = ""
                if os.path.exists(bibp):
                    have = open(bibp, encoding="utf-8", errors="replace").read()
                elif not res.bib_path:
                    with open(bibp, "w", encoding="utf-8", newline="\n") as f:
                        f.write("% 由参考文献附件生成\n\n")
                    res = res.__class__(**{**res.__dict__, "bib_path": bibp})
                added = []
                skipped_bad = 0
                # 去重不能只比键：附件条目用 `a1…`，而正文文末那条编号清单解析出来的
                # 是 `Beall2000` 这种键——**同一份文献、两个键**。实测 02 Lili_Zhao：
                # 只按键去重会让参考文献翻倍成 38 条。这里按「作者+年份+题名」内容指纹去重。
                seen = _bib_fingerprints(have)
                skipped_dup = 0
                for i, r in enumerate(items, 1):
                    key = f"a{i}"
                    if f"{{{key}," in have:
                        continue
                    fp = _ref_fingerprint(r.author, r.title, r.year)
                    if fp in seen:
                        skipped_dup += 1
                        continue
                    seen.add(fp)
                    fields = "\n".join(
                        f"  {n.ljust(9)} = {{{getattr(r, n)}}},"
                        for n in ("author", "title", "journal", "booktitle", "volume",
                                  "number", "pages", "publisher", "address", "year")
                        if getattr(r, n)
                    )
                    entry_text = f"@{r.etype}{{{key},\n{fields}\n}}"
                    # 第 134 条：渲染后结构校验（坏条目会让 biber 整份报废）
                    if not bib_entry_structurally_safe(entry_text):
                        skipped_bad += 1
                        continue
                    added.append(entry_text)
                if added:
                    with open(bibp, "a", encoding="utf-8", newline="\n") as f:
                        f.write("\n% ── 由参考文献附件合并 ──\n" + "\n".join(added) + "\n")
                    print(f"   从附件合并 {len(added)} 条 → {os.path.basename(bibp)}")
                if skipped_dup:
                    print(f"   （另有 {skipped_dup} 条与正文已解析出的条目重复，已跳过）")
                if skipped_bad:
                    print(f"   ⚠ 另有 {skipped_bad} 条结构不合格（嵌套花括号等），未写入")
                if not (added or skipped_dup or skipped_bad):
                    print("   附件条目与现有 bib 无新增")
                elif added:
                    print("   ⚠ 引用键需人工对齐：附件条目键为 a1、a2…，正文引用需相应调整")
            else:
                print(f"   ⚠ 未能从 {os.path.basename(args.refs)} 解析出条目")
    return res

def run_verify_refs(args, res, *, out_dir, stem, verify_max):
    """⑪ 核实任务书 + AI 预跑/并入（B-229：从 `main` 纯搬运外提）。"""
    coop_taskbook_path = ""
    coop_bib_keys: list[str] = []
    coop_bibdb: dict = {"checked": False, "safe": None, "missed": None}
    from slcn import llm
    from slcn.bibcheck import split_entries

    bib_text = (
        open(res.bib_path, encoding="utf-8", errors="replace").read()
        if res.bib_path else ""
    )
    # 文献区**原文**：解析器一条都没认出来时，把这些行原样交给编辑的 AI 去结构化
    # （实测 14 篇稿件有文献区却 `n_bib = 0`，其中 9 篇确实有 10–27 行候选条目：
    #  8 篇人年制无编号 + 1 篇日文 `[N]` 清单。编辑部已把文献核实交给编辑各自的 AI，
    #  见 README 第 41 条——所以工具**不再写第二种清单解析器**，「编错的卷号比空缺更糟」）。
    #
    # ⚠️ 第 68 条起，这两份材料由 `build_docx` 直接带出来（`BuildResult.ref_raw_lines` /
    #    `ref_raw_by_key`），**不再**在这里重新解析一遍源稿：从前那样做的理由是
    #    「build_docx 只返回 BuildResult，原文行拿不到」，现在挂在 BuildResult 上了；
    #    而且是**逐条**原文（`ref_raw_by_key`），正常出条目的稿子也拿得到。
    #    LaTeX 通道没有源稿原文，两份都是空的（下游按「没有原文」处理）。
    raw_ref_lines: list[str] = list(res.ref_raw_lines)
    raw_by_key: dict[str, str] = dict(res.ref_raw_by_key)

    # LLM 配置（含通道与检索开关）在这里先算出来：任务书头部那行**通道说明**要用它。
    # ⚠️ `--llm-protocol` / `--llm-web-search` 缺省是 None / False，分别回落到环境变量
    #    SLCN_LLM_PROTOCOL / SLCN_LLM_WEB_SEARCH；两个入口等价（见 argparse 的 help）。
    #    `web_search=False` 与「没传」**不能**混：前者会**覆盖**环境变量，所以在命令行
    #    没给这个开关时必须显式回落（`None`），这就是下面那个三元表达式的全部理由。
    #    第 70 条的两个新参数同理：None → 环境变量 → 默认（`/responses` / `web_search`）。
    cfg = llm.config_from_env(
        args.llm_base, args.llm_key, args.llm_model,
        protocol=args.llm_protocol,
        web_search=(True if args.llm_web_search else None),
        search_tool=args.llm_search_tool,
        responses_path=args.llm_responses_path,
    )
    # 本次核实通道（**一行说清「联网了没有」**）：写进控制台、任务书头部、合并记录。
    # 为什么三种取值都写「未启用检索」：编辑真正要知道的是「模型能不能看到网络」，
    # 而不是我们发的是哪个 HTTP 路径。
    # 第 70 条：工具名也写进去（配置成什么就写什么）——默认值是 `web_search`，
    # 所以 f-string 出来的字符串与从前的字面量**逐字节相同**（既有断言不受影响）。
    if cfg.uses_web_search:
        channel = f"responses + {cfg.search_tool}（已启用联网检索）"
    elif cfg.protocol == "responses":
        channel = "responses（未启用检索：模型只能凭记忆）"
    else:
        channel = "chat（未启用检索：模型只能凭记忆）"

    # ── 第六十批：全文 AI 核查（直连 API；report-only；默认关）─────────────
    # 预算闸放在**任何请求之前**：先分片、估账，超限打印预计账并 return 2（零请求）。
    ft_chunks: list[str] = []
    ft_items_per_chunk: list[list[dict]] = []
    ft_est: dict = {}
    ft_max_tokens: int | None = None
    ft_thinking: dict | None = None
    if args.llm_fulltext:
        # 批63：正文字符上限（默认 40000；0 = 不限）。超限**显式截断**并醒目告警，
        # 不静默丢、也不 exit2（exit2 只留给非法配置/预算超限）。
        _ft_body = getattr(res, "body_text", "") or ""
        _ft_cap = int(args.llm_fulltext_max_chars or 0)
        _ft_truncated = False
        if _ft_cap > 0 and len(_ft_body) > _ft_cap:
            _ft_truncated = True
            print(f"   ⚠ 全文复核：正文 {len(_ft_body)} 字 > 上限 {_ft_cap} 字"
                  f"（--llm-fulltext-max-chars）→ **只发送前 {_ft_cap} 字**"
                  "（显式截断，退出码不变；0 = 不限）")
            _ft_body = _ft_body[:_ft_cap]
        _ft_chunk_chars = (min(LLM_FULLTEXT_CHUNK_CHARS, _ft_cap) if _ft_cap > 0
                           else LLM_FULLTEXT_CHUNK_CHARS)
        # 低置信项：与协同 ph1 同一收集器（ID 白名单沿用 coop.parse_low_confidence）。
        # 批63：META 项要能走批57 安全闸，必须带初排时 tex 里的当前值（判漂移）。
        _ft_low = _coop.collect_low_confidence(
            warnings=res.warnings,
            missing_fields=getattr(res, "missing_fields", ()) or (),
            raw_ref_lines=raw_ref_lines,
            bib_text=bib_text,
            label_targets=dict(getattr(res, "undetected_fields", ()) or ()),
            current_fields=_coop.read_tex_field_values(
                getattr(res, "tex_path", "") or ""),
        )
        ft_chunks = split_fulltext_chunks(_ft_body, _ft_chunk_chars)
        ft_items_per_chunk = [
            _fulltext_chunk_items(_chunk, _ft_low, first=(_i == 0))
            for _i, _chunk in enumerate(ft_chunks)
        ]
        _, ft_max_tokens, ft_thinking = llm.request_params_from_env()
        _ft_prompts = [
            _llm_fulltext_user(_i + 1, len(ft_chunks), _chunk, ft_items_per_chunk[_i])
            for _i, _chunk in enumerate(ft_chunks)
        ]
        ft_est = _coop.estimate_api_budget(_llm_fulltext_system(), _ft_prompts,
                                           ft_max_tokens)
        _ft_why = _coop.budget_exceeded(ft_est, args.ai_budget_tokens,
                                        args.ai_batch_budget_tokens)
        if _ft_why:
            print("\n⛔ " + _ft_why)
            print(f"   全文复核预计用量：{ft_est['n_calls']} 次调用 / "
                  f"prompt {ft_est['prompt_tokens']} + completion "
                  f"{ft_est['completion_tokens']} = {ft_est['total_tokens']} tokens")
            print("   （本次**没有发起任何请求**；放宽预算请调大参数，0 = 不限）")
            return 2
        print(f"   全文复核（report-only，不改产物）：正文 "
              f"{len(getattr(res, 'body_text', '') or '')} 字 → {len(ft_chunks)} 片；"
              f"预计 {ft_est['n_calls']} 次调用 / {ft_est['total_tokens']} tokens")

    # 任务书头部：配了 LLM 才替换掉那句「工具不做联网核实」（那时它不成立）。
    # 没配 LLM 时**不传** search_note → 头部与从前逐字节相同。
    search_note = ""
    if cfg.ready:
        search_note = (f"> 本次核实通道：{channel}；协议 `{cfg.protocol}`，"
                       f"模型 `{cfg.model}`。\n"
                       "> 逐条来源标注见每条上方的 `% 来源：…`；"
                       "本次是否真的联网核实，以下面的通道说明与合并记录为准。")

    # ── 第 128 条（2026-10-18）：核实范围 = **正文引用到的键** + 成本闸门 ──────
    #
    # 期 87 子案例 1 实测：源 bib 377 条、正文只引用 31 个键，任务书 + 检索 + AI 却把
    # 376 条全核了（检索 418 次 ≈ ¥1.845、AI 376 次调用 / 2,538,740 prompt tokens）。
    # 所以先算账、再花钱：范围判定只有 `_refmark.resolve_verify_scope()` 一份实现，
    # 这里只负责「从产物 .tex 取引用键」+ 打印 + 闸门。
    #
    # 为什么读**产物 .tex** 而不是 `BuildResult.bib_keys_used`：
    #   · LaTeX 通道的 `bib_keys_used` 与 `bibcheck.cited_keys` 同源，但**不含**
    #     `\nocite{*}`（`sources.extract_bibkeys_from_body` 的正则不认 nocite）；
    #   · Word 通道的 `bib_keys_used` 是 `{e.key for e in doc.refs}` —— 那是**文末
    #     参考文献区的全部键**，不是正文引用键（脚注体例的 bib_entries 还不在里面），
    #     直接拿来当引用键会把「没被引用的条目」也核了；
    #   · `docxsource.link_citations()` 里的 `used` 只是局部变量、没有对外暴露。
    # 产物 .tex 是 build 已替换完引用的最终正文，两个通道同一口径（见 GUIDE §3.10）。
    entries_all = list(split_entries(bib_text))
    tex_text = ""
    if res.tex_path and os.path.exists(res.tex_path):
        tex_text = open(res.tex_path, encoding="utf-8", errors="replace").read()
    cited, nocite_all = _refmark.cited_keys_from_body(tex_text)
    scope = _refmark.resolve_verify_scope(
        [k for _et, k, _b in entries_all], cited,
        nocite_all=nocite_all,
        explicit_all=bool(args.verify_all_refs),
        verify_max=verify_max,
    )
    search_enabled = bool(args.search_refs) or _search.refs_enabled_from_env()

    if args.verify_plan:
        print("\n⑪·核实计划（--verify-plan：只算账；不生成任务书、不检索、不调用 AI）")
    # 「源 bib N 条；正文引用 M 条 → 本次核实 M 条（跳过 N-M 条未引用）」
    print("   " + _refmark.scope_log_line(scope))
    if scope.mode == _refmark.MODE_FALLBACK:
        # 边界：正文一个引用键都没解析出来（没有 \parencite/\nocite、或引用没替换完）。
        # 这时**退回全量 + 大声警告**——宁可多花钱，也不能静默「一条都不核」。
        print("   ⚠ 未能从产物正文解析出引用键（正文没有 `\\parencite`/`\\nocite`，"
              "或引用没完成替换）——本次**退回全量**核实（与旧行为相同）；"
              "成本按全量计，请留意下面的上限。")
    if scope.cited_missing:
        more = " …" if len(scope.cited_missing) > 6 else ""
        print(f"   （正文有 {len(scope.cited_missing)} 个引用键在 bib 里不存在，"
              f"无从核实：{'、'.join(scope.cited_missing[:6])}{more}）")
    if scope.over_limit:
        if args.verify_plan:
            print("   ⚠ 本次核实条数**超过上限** → 实际执行会被闸门拦下（退出 2）。")
            print("      要全量请加 --verify-all-refs；要调上限用 --verify-max N（0 = 不限）。")
        else:
            die(_refmark.limit_guidance(scope, search_enabled=search_enabled))
    # 本次真正要核实的条目（`keeps()` 对全量模式恒 True；范围只有一份判定）。
    entries = [e for e in entries_all if scope.keeps(e[1])]

    # ── 第 129 条（2026-10-18）：先查编辑部引用库 ─────────────────────────
    # 命中已核实条目 → 该键**不调检索 API、不调 AI**；key 命中只算候选（BibTeX key
    # 多为 per-paper，同 key 不保证同文献）——仍走检索。判定实现在
    # `slcn/bibdb.py: Store.match_reference()`（只有一份），这里只编排/打印。
    bibdb_store, bibdb_path, bibdb_warning = None, "", ""
    bibdb_safe: dict[str, dict] = {}
    bibdb_candidates: dict[str, dict] = {}
    bibdb_missed: list[str] = []
    bibdb_store, bibdb_path, bibdb_warning = resolve_bibdb(args)
    if bibdb_warning:
        print(f"   ⚠ {bibdb_warning}")
    if bibdb_store is not None:
        bibdb_safe, bibdb_candidates, bibdb_missed = classify_bibdb(bibdb_store, entries)
        # 批79（B-131）：把本次核实范围内的库分类计数留档（供交付记录节；只读）。
        coop_bibdb = {"checked": True, "safe": len(bibdb_safe),
                      "missed": len(bibdb_missed)}
        print("   " + bibdb_log_line(bibdb_safe, bibdb_candidates, bibdb_missed,
                                   bibdb_store, bibdb_path))
        if bibdb_safe:
            print(f"   （其中 {len(bibdb_safe)} 条命中库内已核实：不调检索/AI，"
                  "字段差异只写进任务书「五、库内已核实」——**不改 reference.bib**）")
    # 真正要送检索/AI 的条目 = 本次核实范围 - 安全命中。
    ai_entries = [e for e in entries if e[1] not in bibdb_safe]

    if args.verify_plan:
        max_s = "不限" if scope.verify_max == 0 else str(scope.verify_max)
        print(f"   条数上限：{max_s}（--verify-max，默认 "
              f"{_refmark.DEFAULT_VERIFY_MAX}；0 = 不限）")
        # 第 129 条：预计检索/AI 次数按**库内命中扣减后**的条数算（省钱要能看见）。
        n_ai = len(ai_entries)
        if bibdb_store is not None and bibdb_safe:
            print(f"   引用库已核实：{len(bibdb_safe)} 条（不检索、不调 AI）"
                  f"→ 下列预计按扣减后的 {n_ai} 条算")
        if search_enabled:
            n_q = 2 * n_ai
            print(f"   预计检索：≤ {n_q} 次查询（每条最多 2 次；"
                  f"¥5/1000 次成功 → ≤¥{n_q * 5 / 1000:.2f}）")
        else:
            print("   预计检索：0 次（未启用 --search-refs）")
        if cfg.ready:
            print(f"   预计 AI：≤ {n_ai} 次调用（每条一次请求；"
                  f"token 费按模型 `{cfg.model}` 计）")
        else:
            print("   预计 AI：0 次（未配置 LLM：只出任务书，由编辑交给自己的 AI）")
        print("   （转换产物已照常写出；实际执行请用 --verify-refs）")
        return 0

    # ── P1（第 71 条起）：自动检索证据（默认关；--search-refs / SLCN_SEARCH_REFS=1）──
    # 只在生成任务书时有落点：每条文献上方多一段「🔎 自动检索证据」。
    # **不改模型提示词、不改 reference.bib/tex**（P2 才考虑把证据喂给模型）。
    # ⚠️ 第 128 条起只对**本次核实范围**内的条目检索（`entries` 已按范围过滤）——
    #    检索是**按次计费**的，没引用的条目连证据都不该买。
    evidence: dict[str, dict] | None = None
    evidence_note = ""
    if search_enabled:
        if not ai_entries:
            # 第 129 条：范围内条目全部命中库内已核实 → 检索也一起省掉（按次计费）。
            print("   检索证据：本次范围内条目"
                  + ("全部命中引用库已核实" if entries else "为空")
                  + "，跳过检索。", flush=True)
        else:
            base, skey = _search.config_from_env(args.llm_base, args.llm_key)
            if not base or not skey:
                print("   检索证据：⚠ 已要求检索（--search-refs），但没配置 API 地址/密钥"
                      "（--llm-base/--llm-key 或 SLCN_SEARCH_BASE/SLCN_SEARCH_KEY）——"
                      "跳过检索证据，任务书照常生成。", flush=True)
            else:
                print("\n⑪·检索 生成参考文献检索证据（DeepSeek Search API）", flush=True)
                print(f"   地址 {base}（{_search.resolve_endpoints(base)[0]}）；"
                      f"每条最多 2 次查询、count=5；计费 ¥5/1000 次成功。"
                      "证据默认不改任何转换产物，仅写进任务书。", flush=True)
                client = _search.SearchClient(base, skey)
                keys = [k for _et, k, _b in ai_entries]
                evidence = _search.gather_evidence(
                    bib_text, keys, client, log=lambda m: print(m, flush=True))
                summary = _search.stats_summary(client, evidence)
                print(f"   {summary}", flush=True)
                evidence_note = (
                    "### 本次检索证据（自动，DeepSeek Search API）\n\n"
                    f"> {summary}。\n"
                    "> 证据只是线索：命中的来源仍须人工核对字段；"
                    "「未查到」≠ 文献不存在（可能是题名解析错或未被收录）。")

    # ⚠️ 旧条件是 `res.bib_path`：这批「有文献区、0 条」的稿子根本没有 bib 文件，
    #    于是**连任务书都不生成**——而它们正是最需要把原文交出去的一批。改为「有 bib
    #    或有原文」都生成；两种旧情形的产物逐字节不变（第五节只在「0 条 + 有原文」时追加）。
    if res.bib_path or raw_ref_lines:
        print("\n⑪ 生成参考文献核实任务书", flush=True)
        # 第 128 条：只把**本次核实范围**内的条目列进逐条清单（`scope_keys=None` = 全量，
        # 与旧行为逐字节相同；给了集合则任务书头部多一行「本次核实范围」说明）。
        md = _refmark.build(bib_text, stem, raw_ref_lines=raw_ref_lines,
                            raw_by_key=raw_by_key, search_note=search_note,
                            evidence=evidence, evidence_note=evidence_note,
                            cite_keys=(scope.scope_keys
                                       if scope.mode == _refmark.MODE_CITED else None),
                            bibdb_hits=(bibdb_taskbook_hits(entries, bibdb_safe)
                                        if bibdb_safe else None),
                            # 第五十六批：协同任务书把第三节第 10 条限定为
                            # 「文献条目部分」，并指明附一的 JSON 按附一格式回复
                            # （非协同任务书逐字节不变）。
                            coop=bool(args.coop_taskbook_only))
        path = os.path.join(out_dir, f"{stem}_文献核实.md")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        coop_taskbook_path = path
        coop_bib_keys = [k for _et, k, _b in entries]
        print(f"   {os.path.basename(path)}（{md.count(chr(10))} 行）")
        # 逐条原文的覆盖情况当场说清楚：编辑要能看出「这份任务书里有几条是带原文的」。
        # （按**本次核实范围**数：没进任务书的条目算进来会让人以为任务书缺了内容。）
        n_with_raw = sum(1 for _et, k, _b in entries if raw_by_key.get(k))
        if raw_by_key:
            print(f"   其中 {n_with_raw} 条附上了源稿原文"
                  "（解析可能有遗漏，任务书里已写明以原文为准）")
        else:
            print("   本次没有可用的源稿原文（LaTeX 通道 / 作者自带的 .bib）")
        print("   交给编辑各自的 AI：文件里已写明规范、来源优先级与回答格式")

    if not entries:
        # 「文献区一条都没解析出来」的稿子没有可预跑的条目：必须在这里单独收口，
        # 否则会掉进下面 `n_got == 0` 的失败分支，报一次**根本没发生**的「AI 调用失败」。
        print("   （bib 里没有可核实的条目：只出任务书，由编辑交给自己的 AI）")
    elif not ai_entries:
        # ── 第 129 条：本次范围内条目**全部**命中库内已核实 → 不检索、不调 AI ──
        # reference.bib **一个字都不改**（库内命中只作核实证据；字段差异已写进任务书
        # 「五、库内已核实」）。有配置 LLM 时仍写「六、合并记录」，把「库内命中
        # （未调 AI）」与「范围外」分开留档——留档要能看出「为什么这次没花钱」。
        print(f"   ✅ 引用库：本次核实范围内 {len(entries)} 条全部命中已核实记录"
              " → **不调用检索/AI**。", flush=True)
        print("      reference.bib **未改动**（库内命中只作核实证据；"
              "字段差异见任务书「五、库内已核实」）。")
        if cfg.ready and res.bib_path and not args.no_ai_merge \
                and not args.coop_taskbook_only:
            # `--no-ai-merge`（「只出任务书」）时连合并记录也不追加——与旧语义一致。
            prov = (f"本次核实通道：**未调用 AI**（{len(bibdb_safe)} 条全部命中编辑部"
                    f"引用库 `{bibdb_path}` 的已核实记录；检索 0 次 / AI 0 次）")
            apply_ai_merge(res.bib_path, path, {}, stem, out_dir,
                           provenance=prov,
                           out_of_scope={k for _et, k, _b in entries_all
                                         if not scope.keeps(k)},
                           bibdb_hits=set(bibdb_safe))
    elif cfg.ready and not args.coop_taskbook_only:
        # 只打印地址与模型名（诊断「地址填错」全靠它）。**绝不打印 cfg.key。**
        # `flush=True`：这一节最慢（逐条联网），进度必须**当场**出现在运行日志里，
        # 不能等 Python 的块缓冲（GUI 用管道接 stdout，默认 8KB 才刷一次）。
        print(f"   已配置 LLM（{cfg.model} @ {cfg.base}）：按同一份规范自动跑 {len(ai_entries)} 条…"
              f"（每条一次请求，默认超时 60 秒；每条**开始前**会打一行 `[i/N] 键名`，"
              f"看到它就知道卡在哪一条）", flush=True)
        # **本次核实通道**：编辑看日志第一眼要能回答「这次到底联网了没有」。
        # 这一行与任务书头部、合并记录用的是同一个 `channel` 字符串（单一出处）。
        print(f"   本次核实通道：{channel}")
        # ── 第 128 条：**模型能力前置提示**（必须在 AI 批量开始**前**打）─────────
        # 「开了检索开关」只是我们发了工具；已知会静默忽略 web_search 的模型
        # （deepseek-flash / deepseek-chat 等）即使 HTTP 200 也不会真检索。等到跑完
        # 才从「0 个 web_search_call」发现，钱已经花了、任务书也生成了（期 87 子案例 1）。
        # 判据只在 llm.py（`ignores_web_search`）；这里只负责在正确的时机出声。
        # ⚠️ 事后提示（下面「检索证据」收尾）**保留**，两者不互相替代。
        if cfg.uses_web_search:
            cap = llm.web_search_capability_warning(cfg.model)
            if cap:
                print("   ⚠⚠ **模型能力前置提示**（AI 批量开始前）：" + cap, flush=True)
                if evidence:
                    print(f"      （本次另有 --search-refs 的自动检索证据 {len(evidence)} 条，"
                          "会作为数据注入逐条提示词；但模型**内置**联网仍会被忽略）",
                          flush=True)
        # 检索开关只对 responses 通道有意义（chat 通道的 tools 是 function calling，
        # 要在客户端自己执行，不是检索）——见 `slcn/llm.py: LLMConfig.uses_web_search`。
        system = _refmark.SPEC_MD + "\n" + _refmark.SOURCES_MD + "\n" + _refmark.FORMAT_MD
        if cfg.uses_web_search:
            # 只有**确实带上了检索工具**时才告诉模型「你能联网」；关着时说这句就是撒谎。
            system += "\n" + _refmark.SEARCH_MD
            print("   ⚠ 已于本次提示词中加入「必须先用检索工具核对官方来源」一节；"
                  "端点是否真的检索，跑完看下面的检索证据（0 次 = 凭记忆作答）")
        if evidence:
            # P2（第 72 条起）：把 P1 的检索证据**作为数据**注入逐条提示词，并声明
            # 「`% 来源：` 只能写证据里的 URL」。合成前还会做白名单校验（见下）。
            system += "\n" + _refmark.EVIDENCE_MD
            print(f"   ⚠ 已于本次提示词中加入「只依据自动检索证据、勿编造来源」一节"
                  f"（{len(evidence)} 条证据）；合并前会校验 `% 来源：` URL 是否在证据内")
        advice = ["", "---", "", "# AI 建议（**须人工核实**，工具不保证正确）", ""]
        # 请求参数集中由 llm.request_params_from_env() 给（只用 SLCN_LLM_MAX_TOKENS 与
        # SLCN_LLM_THINKING）：给输出封顶、并按用户意愿决定要不要思维链——
        # **别把额度耗在推理上被截半句**，截断只会表现为「bibtex 代码块残缺」，看不出真因。
        # 官方文档（2026-10）写明 `thinking` **默认 enabled**，而思考模式下
        # `temperature` 没有效果（本通道发 temperature=0 求确定性，只在非思考模式成立）、
        # 推理 token **按输出计费**且能吃掉整份 max_tokens——而 ⑪ 是纯抽取/规范化，
        # 本来就不需要思维链，所以 `SLCN_LLM_THINKING=0` 是推荐配置。
        # ⚠️ 这条通道**显式**传 response_format=None，**不读 SLCN_LLM_JSON**：
        # ⑪ 要的是**整份 bibtex 文本**（`refmark.FORMAT_MD` 第 9 条），不是 JSON；
        # 逼成 JSON 对象既违规范、会让任务书里的建议变形，又会被端点以
        # 「prompt 里没有 json 字样」400 掉（DeepSeek）。JSON 模式（SLCN_LLM_JSON）
        # 默认关闭，留给将来需要结构化返回的调用。
        _, max_tokens, thinking = llm.request_params_from_env()
        # ── 第五十四批（2026-10-19）：直接 API 通道的**预算闸** ──────────────
        # 只约束 `cfg.ready` 的自动预跑；外部 chat 通道（--coop-*）不产生 API 费用，
        # 根本不会走到这里。超限时先打印预计账、退出 2——**一个请求都不发**。
        _budget_prompts = [
            _refmark.entry_prompt(et, k, body, raw_by_key.get(k, ""),
                                  search=cfg.uses_web_search,
                                  evidence=(evidence or {}).get(k))
            for et, k, body in ai_entries
        ]
        _budget_est = _coop.estimate_api_budget(system, _budget_prompts, max_tokens)
        _budget_why = _coop.budget_exceeded(
            _budget_est, args.ai_budget_tokens, args.ai_batch_budget_tokens)
        if _budget_why:
            print("\n⛔ " + _budget_why)
            print(f"   预计用量：{_budget_est['n_calls']} 次调用 / "
                  f"prompt {_budget_est['prompt_tokens']} + completion "
                  f"{_budget_est['completion_tokens']} = "
                  f"{_budget_est['total_tokens']} tokens")
            print("   （本次**没有发起任何请求**；放宽预算请调大参数，0 = 不限）")
            return 2
        # 本次运行**实际**发送的 thinking：端点若拒收这个字段（400），本条去掉它重试
        # 一次后置 None —— 剩下的条目也不再发它，避免每条都白撞一次 400。
        send_thinking = thinking
        thinking_warned = False
        n_got = 0
        n_try = 0
        fail_streak = 0
        n_calls = 0       # 真正发出去的请求数，用于收尾的用量汇总
        tokens_in = 0
        tokens_out = 0
        tokens_reason = 0   # 推理 token（`usage.completion_tokens_details.reasoning_tokens`）
        # 检索证据累计（responses + web_search 时才有意义）：`LAST_SEARCH` 是**逐次**
        # 调用的，收尾要的是「整轮跑下来到底检索了几次、引了哪些页面」。
        # 为什么非数不可：`tools:[{"type":"web_search"}]` 只是请求，有的端点/模型（如
        # DeepSeek 的 flash）会**静默忽略**它并照样回 200 —— 不数这个，我们就会把
        # 「凭记忆」当「已联网核实」汇报。
        n_search = 0
        search_urls: list[str] = []
        # P2：白名单 = 证据包 URL ∪ 本次内置检索返回的 url_citation（都属「本次运行
        # **实际**检索到的来源」）。只在校验启用（有证据 / 有内置检索且收到了 URL）时生效。
        allowed_source_urls: set[str] = set()
        if evidence:
            for _pack in evidence.values():
                # 白名单 = top + 本次**全部**检索结果 URL（P2 真 API 验收修正：
                # 结果集第 4–5 位也是真实来源；全部纳入但**不**加进提示词，token 不涨）。
                allowed_source_urls.update(_pack.get("allowed_urls") or [])
                for _row in (_pack.get("top") or []):
                    if _row.get("url"):
                        allowed_source_urls.add(_row["url"])
        # 本次要**并入 reference.bib** 的回复（key → 原文）。只收「正常收尾」的：
        # 被截断 / 被内容过滤 / 被中断的那几条**一律不并入**——它们只是半条条目，
        # 并进去等于把残缺当完整（「假完整比明显缺失更危险」，第 54/59 条）。
        replies: dict[str, str] = {}
        unclean_replies: dict[str, str] = {}
        for et, k, body in ai_entries:
            n_try += 1
            # 进度：**发起请求之前**先打一行，这样「卡住」时能一眼看出卡在哪一条
            # （界面上一条请求最长可能等 60 秒，中间没有任何输出会很像是死掉了）。
            # `flush=True` 是硬要求：GUI 用管道收 stdout，块缓冲会把这一行压到后面去。
            print(f"   [{n_try}/{len(ai_entries)}] {k}：正在核实…", flush=True)
            # 第 68 条：把这一条的**源稿原文**一并交给模型（没有原文时 `entry_prompt`
            # 返回的字符串与从前逐字节相同）。第 66 条的教训是：解析器丢字段时，
            # 模型只能「如实」报告我们丢的信息——看上去像模型不行，其实喂的是残缺件。
            # `search=` 只在开了联网检索时追加一行引导（关着时逐字节不变）。
            prompt = _refmark.entry_prompt(et, k, body, raw_by_key.get(k, ""),
                                           search=cfg.uses_web_search,
                                           evidence=(evidence or {}).get(k))
            n_calls += 1
            # 统一入口：chat（默认，逐字节旧行为）/ responses（见 slcn/llm.py）。
            got = llm.complete(cfg, system, prompt, response_format=None,
                               max_tokens=max_tokens, thinking=send_thinking)
            # `thinking` 是**可选扩展**：老端点/自建服务不认识它时会 400
            # （文案里带 thinking）。不能为一个可选参数把整条通道打灭，所以
            # **本条**去掉该字段重试一次；成功则记住「本次运行不再发它」
            # （与当年 response_format 的降级同一个思路）。
            # ⚠️ 只认这一种 400（判据见 llm.thinking_rejected）：别的 400（上下文
            # 超限之类）与这个字段无关，重试只会每条白跑一次。重试**也**失败时照旧
            # 走下面的失败分支（fail_streak / 提前收手 / 只出任务书），绝不吞掉真失败。
            if got is None and send_thinking is not None and llm.thinking_rejected():
                if not thinking_warned:
                    print("   ⚠ 端点不支持 thinking 字段（HTTP 400），本条改为不带该字段重试；"
                          "本次运行其余条目也不再发送它")
                    thinking_warned = True
                send_thinking = None
                n_calls += 1
                got = llm.complete(cfg, system, prompt, response_format=None,
                                   max_tokens=max_tokens, thinking=None)
            if got:
                n_got += 1
                fail_streak = 0
                # 检索证据累计（responses + web_search）：`LAST_SEARCH` 每次调用都会重置，
                # 必须**当场**收走，否则收尾时只剩最后一条的数字。
                _ev = llm.LAST_SEARCH or {}
                n_search += int(_ev.get("n_calls", 0))
                search_urls += [u for u in _ev.get("urls", []) if u not in search_urls]
                # ⚠️ 端点**不报错**也不代表是完整答案：`max_tokens` 截断（length）、
                # 被内容过滤、服务端资源不足、生成被中断…… 都照样回 200 和一段半截内容
                # （判据见 slcn/llm.py 的 LAST_FINISH；取值集合来自官方文档 2026-10）。
                # 提示词要的是**整份 bibtex 代码块**，所以这些结果都是「半条条目」——
                # 若不标注就照原样追加，编辑会把残缺条目当成完整建议照抄
                # （假完整比缺失更糟）。判据 = **非 stop 且非空**；空串是「老端点没给
                # 这个字段」，不能当异常（会误报）。
                # 两处都要标：**stdout 一句告警**（当场可见）+ **任务书里一行引用**
                # （离开这次运行、别人单独看 markdown 时也看得出这条不完整）。
                finish = llm.LAST_FINISH
                unclean = finish not in ("", "stop")
                head = f"## `{k}`\n\n"
                if unclean:
                    why = _FINISH_WHY.get(finish, finish)   # 不认识的取值原样带上
                    if finish == "length":
                        # 沿用既有文案（编辑已认识这句，且它带着「调大
                        # SLCN_LLM_MAX_TOKENS」这条可操作建议）。
                        # 引用行与被警告的内容之间留一个空行：markdown 里引用行紧跟代码
                        # 围栏会被并进同一个引用块（缩进/归属出错），空行让两者各自成块。
                        head += "> ⚠️ 本条 AI 返回被截断（finish_reason=length），内容不完整。\n\n"
                    else:
                        head += (f"> ⚠️ 本条 AI 返回未正常结束——{why}"
                                 f"（finish_reason={finish}），内容可能不完整。\n\n")
                advice.append(head + f"{got.strip()}\n")
                if unclean:
                    # 半条条目**不并入**（见 replies / unclean_replies 的说明），
                    # 但原因要留下来——任务书「六、合并记录」里会逐条写清楚。
                    unclean_replies[k] = (
                        f"AI 返回未正常结束（finish_reason={finish}"
                        f"{'，即被 max_tokens 截断' if finish == 'length' else ''}），"
                        "按「假完整比明显缺失更危险」的纪律**不并入**；原文里的这一条逐字节未动"
                    )
                    if finish == "length":
                        print(f"   ⚠ {k}：返回被 max_tokens 截断（finish_reason=length），"
                              "本条**不完整**，建议调大 SLCN_LLM_MAX_TOKENS 或人工核实")
                    else:
                        print(f"   ⚠ {k}：AI 返回未正常结束——{why}"
                              f"（finish_reason={finish}），本条可能**不完整**，请人工核实")
                else:
                    replies[k] = got
                # 用量逐条打出来：额度是编辑部的真金白银，跑完只给总数看不出是哪条贵。
                # 推理 token 单独点出来（官方文档 2026-10：它按**输出**计费，混在
                # completion 里看不出钱烧在思维链上），>0 才写，免得正常跑刷一串 0。
                use = llm.LAST_USAGE
                tokens_in += use.get("prompt_tokens", 0)
                tokens_out += use.get("completion_tokens", 0)
                reason = use.get("reasoning_tokens", 0)
                tokens_reason += reason
                line = (f"   {k}：tokens {use.get('prompt_tokens', 0)}"
                        f"+{use.get('completion_tokens', 0)}")
                if reason > 0:
                    line += f"（含推理 {reason}）"
                print(line)
            else:
                fail_streak += 1
                # 连续失败多半不是「这一条内容的问题」，而是网络/地址/密钥/模型名的问题
                # （`llm.chat` 对失败一律返回 None）。与其把剩下的条目各超时一遍
                # （默认 60 秒/条，一期几十条就是几十分钟），不如早点收手并说清楚。
                if fail_streak >= 3:
                    break
        if n_calls:
            # 只在真的调用过时才报（未配置 / 条目为空时不必刷一行 0）。
            # 推理 token 合计只在 >0 时附上：它是输出计费的一部分，不点出来
            # 编辑只会看到 completion 偏高而不知钱花在思维链上。
            reason_note = f"（含推理 {tokens_reason}）" if tokens_reason > 0 else ""
            print(f"   共 {n_calls} 次调用，{tokens_in} prompt + {tokens_out} completion tokens"
                  f"{reason_note}")
        # ── 联网检索的**证据**（只有开了检索开关才说）─────────────────────────
        #
        # 这一节是「不许静默假装」的落点：`tools:[{"type":"web_search"}]` 只是**请求**，
        # 有的端点/模型（如 DeepSeek 的 flash）会静默忽略它并照样回 200。
        # 所以这里报的是**端点的行为**（收到几个 web_search_call），而不是我们的开关状态。
        if cfg.uses_web_search and n_calls:
            if n_search:
                print(f"   ✅ 检索证据：本次共收到 {n_search} 个 web_search_call"
                      f"（{len(search_urls)} 个来源 URL）——模型确实联网查过")
                for u in search_urls[:5]:
                    print(f"      · {u}")
            else:
                print("   ⚠ **本次没有任何检索证据**（0 个 web_search_call）："
                      "端点忽略了这个工具，模型只能凭记忆作答。")
                print("     注意：内置检索工具是否真的生效由端点与模型决定——"
                      "如 DeepSeek 的 deepseek-flash 会忽略它、deepseek-v4-pro 才真检索。")
                print("     请把本次结果当成**凭记忆**的修订：卷/期/页码/出版地务必人工抽查，"
                      "或把《文献核实任务书》交给编辑自己的联网 AI。")
        n_left = len(ai_entries) - n_try
        if n_got:
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(advice) + "\n")
            print(f"   已追加 {n_got} 条 AI 建议到同一个 markdown（标为「须人工核实」）")
            if fail_streak:
                print(f"   ⚠ 另有 {fail_streak + n_left} 条没有返回内容"
                      f"（连续 {fail_streak} 条失败后已提前停止，未跑完 {len(ai_entries)} 条）")

            # ── 把 AI 结果**并入 reference.bib**（2026-10-15 用户决定；第 64 条）──
            #
            # 只有拿到了「正常收尾」的回复才谈得上并入（replies 里只收那些）。
            # 一条都没收上（全是截断/内容过滤/结构坏）→ 不碰 bib，并**明说未改动**：
            # 「0 条并入」绝不能长得像成功。
            if args.no_ai_merge:
                print("   （--no-ai-merge：本次**只出任务书**，AI 结果不并入 reference.bib）")
            elif not replies:
                print("   ⚠ 没有任何一条 AI 回复可用（截断/内容过滤/没返回内容）："
                      "reference.bib **未改动**，本次只出任务书。")
            else:
                # P2（第 72 条起）：**来源 URL 白名单**校验——回复里 `% 来源：` 的
                # http(s) URL 必须来自本次**实际检索到**的来源（证据包 ∪ 内置检索的
                # url_citation）。编造的 URL 比不写更糟（假的信任信号），按纪律不并入。
                bad_src: dict[str, str] = {}
                if allowed_source_urls or (cfg.uses_web_search and search_urls):
                    _allowed = set(allowed_source_urls) | set(search_urls)
                    for _k in list(replies):
                        _bad = _aibib.bad_source_urls(replies[_k], _allowed)
                        if _bad:
                            bad_src[_k] = (
                                "AI 回复里的 `% 来源：` URL **未出现在本次检索结果里**"
                                "（换域名/换路径即视为无法核实：" + "、".join(_bad[:3]) + "）；"
                                "按纪律不并入，原文该条逐字节未动")
                            del replies[_k]
                    print(f"   来源 URL 白名单：{len(replies)} 条通过、{len(bad_src)} 条被挡下")
                rejected_all = {**unclean_replies, **bad_src}
                print("\n   把 AI 结果并入 reference.bib…")
                prov = channel_provenance(channel, cfg.protocol, cfg.model,
                                          cfg.uses_web_search, n_search)
                mg = apply_ai_merge(res.bib_path or "", path, replies,
                                    stem, out_dir, rejected=rejected_all,
                                    provenance=prov,
                                    # 第 128 条：范围外的键（正文未引用）不该出现在
                                    # 「六、合并记录」的逐条跳过里（那是「没让 AI 做」，
                                    # 不是「AI 没返回」）。
                                    out_of_scope={k for _et, k, _b in entries_all
                                                  if not scope.keeps(k)},
                                    # 第 129 条：库内命中（未调 AI）单独统计，别写成
                                    # 「AI 未返回」。
                                    bibdb_hits=set(bibdb_safe))
                if mg["n_merged"]:
                    print(f"   ✅ 已并入 {mg['n_merged']} 条 / 跳过 {mg['n_skipped']} 条"
                          f"（逐条原因见任务书「六、合并记录」）")
                    if rejected_all:
                        print(f"   ⚠ 另有 {len(rejected_all)} 条**未并入**"
                              "（返回不完整 / 来源不在证据内；原因已在合并记录里注明）")
                    print(f"   回滚副本：{os.path.basename(mg['rollback'])}"
                          "（= 本次合并**之前**的那份 reference.bib）")
                    if mg["report"]:
                        print(f"   {os.path.basename(mg['report'])}：{mg['conclusion']}")
                    print("   ⚠ **这份 reference.bib 已含 AI 结果，请人工核对；"
                          f"原文件见 {os.path.basename(mg['rollback'])}**")
                else:
                    why = mg["error"] or "没有任何条目通过安全校验"
                    print(f"   ⚠ {why}——本次 reference.bib **未改动**，"
                          "原因已逐条写进任务书「六、合并记录」。")
        else:
            # ⚠️ AI 失败**不影响出稿**：任务书在调用 AI 之前就已经写盘了，
            #    这里只把原因说清楚（编辑器/界面日志都要能看出是「连不上」还是「没配好」）。
            print("   ⚠ AI 调用失败：没有任何一条返回内容。本次**只出任务书**。")
            print(f"     请检查：地址 {cfg.base} 是否可达、密钥是否正确、模型名 {cfg.model} 是否存在。")
            if fail_streak >= 3:
                print(f"     （连续 {fail_streak} 条失败后已提前停止，共尝试 {n_try}/{len(entries)} 条；"
                      "默认超时 60 秒/条，不再逐条空等）")
            print(f"     《文献核实任务书》已生成：{path}")
            print("     可照常交给编辑自己的 AI 核实；转换产物不受影响。")
    elif args.coop_taskbook_only:
        # 外部 chat 通道：**不自动预跑**（这正是「两段式」要的暂停点）。
        print("   （AI 协同初排：本次**不调用任何 API**；"
              "请把任务书交给编辑自己的 chat 模型，回填后运行 --coop-replies）")
        print("   （「暂停/继续」的语义：初排产物只写暂存目录，最终交付目录本次不写）")
    else:
        print("   （未配置 LLM：只出任务书，由编辑交给自己的 AI）")
        print("   （未配置时**不会**并入 reference.bib：bib 与从前逐字节相同）")
        # 检索状态也要说清：没配 LLM 就谈不上本工具联网核实，任务书里也写着这句。
        print("   （未启用联网检索：本工具不联网核实，任务书由编辑的联网 AI 或人工复核）")
        print("   （要自动预跑：界面「设置」页填 API 地址/密钥/模型，"
              "或命令行 --llm-base/--llm-key/--llm-model、环境变量 SLCN_LLM_*）")

    # ── 第六十三批：全文复核的实际请求、自动应用与报告 ─────────────────────
    if args.llm_fulltext:
        _ft_result = _llm_fulltext_review(
            cfg, ft_chunks, ft_items_per_chunk,
            max_tokens=ft_max_tokens, thinking=ft_thinking)
        # 批63（用户 2026-10-20 拍板）：默认自动应用（仅 BIB/META/FN，走批57安全闸）。
        # 显式 --no-llm-fulltext-apply = 批60 的 report-only；--no-ai-merge 同样只出报告。
        _ft_apply_on = (args.llm_fulltext_apply is not False
                        and not args.no_ai_merge)
        _ft_apply: dict | None = None
        if _ft_apply_on:
            _ft_accepted = [dict(sug)
                            for _ch in _ft_result.get("chunks") or []
                            for sug in (_ch.get("accepted") or [])]
            _ft_apply = _coop.apply_fulltext_suggestions(
                accepted=_ft_accepted, items=_ft_low,
                bib_path=str(getattr(res, "bib_path", "") or ""),
                tex_path=str(getattr(res, "tex_path", "") or ""),
                taskbook_keys=[str(k) for _et, k, _b in entries])
        elif args.llm_fulltext_apply is None and args.no_ai_merge:
            print("   （--no-ai-merge：全文复核只出报告，不自动应用）")
        _ft_path = os.path.join(out_dir, f"{stem}{LLM_FULLTEXT_REPORT_SUFFIX}")
        with open(_ft_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_llm_fulltext_report(
                stem=stem, cfg=cfg, est=ft_est, result=_ft_result,
                body_chars=len(getattr(res, "body_text", "") or ""),
                max_chars=_ft_cap, truncated=_ft_truncated,
                chunk_chars=_ft_chunk_chars, apply_summary=_ft_apply))
        _ft_fail = len(_ft_result.get("failures") or [])
        if _ft_apply_on:
            print(f"   🤖 AI 全文复核（自动应用已开，仅 BIB/META/FN）："
                  f"{_ft_result.get('n_suggestions', 0)} 条建议 / "
                  f"{_ft_result.get('n_requests', 0)} 次请求、{_ft_fail} 片不可用")
            print(f"   报告：{os.path.basename(_ft_path)}"
                  "（通过校验的建议仍须人工核对；自动应用记录见报告）")
        else:
            # ⚠️ 以下两句与批60 **逐字节相同**（report-only 口径的兼容承诺）。
            print(f"   🤖 AI 全文复核（report-only，不改产物）："
                  f"{_ft_result.get('n_suggestions', 0)} 条建议 / "
                  f"{_ft_result.get('n_requests', 0)} 次请求、{_ft_fail} 片不可用")
            print(f"   报告：{os.path.basename(_ft_path)}"
                  "（通过校验的建议仍须人工核对；自动应用未开放）")
        if _ft_apply is not None:
            _n_app = int(_ft_apply.get("n_applied") or 0)
            _n_skip = int(_ft_apply.get("n_skipped") or 0)
            if _n_app:
                _bk = str(_ft_apply.get("rollback")
                          or _ft_apply.get("rollback_tex") or "")
                _kept = str(_ft_apply.get("backup_kept") or "")
                print(f"   ✅ 自动应用 {_n_app} 条建议（批57 安全闸；跳过 {_n_skip} 条）"
                      + (f"，备份 {os.path.basename(_bk)}" if _bk else "")
                      + (f"；沿用已存在的 {os.path.basename(_kept)}（未覆盖）"
                         if _kept else ""))
            else:
                print(f"   （自动应用：0 条可写（0 条不写盘），跳过 {_n_skip} 条"
                      "——原因见报告）")
            if _ft_apply.get("error"):
                print(f"   ⚠ 自动应用：{_ft_apply['error']}")
        if ft_chunks and not _ft_result.get("n_suggestions"):
            print("   ⚠ 本次没有拿到通过校验的建议（原因见报告分片明细）")
        if _ft_fail:
            print("   ⚠ 部分分片 AI 建议不可用（超时/HTTP 错/截断/JSON 不过）——"
                  "已按 fail-soft 继续出稿，产物不受影响")
    return {"taskbook_path": coop_taskbook_path, "bib_keys": coop_bib_keys,
            "bibdb": coop_bibdb}

