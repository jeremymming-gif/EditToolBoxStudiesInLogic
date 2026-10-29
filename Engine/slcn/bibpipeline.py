"""bib 管线（B-222 A 段）：bib 发现 + 引用库只读扫描→回填，纯搬运自 `slcn.convert_bib`。

B-222 A 段把 `convert_bib` 的 bib 发现族（`_bib_candidates`…`real_bib_path`）与 bibdb
族（`resolve_bibdb`…`resolve_bibdb_apply_store`）整段逐字外提到本模块；`convert_bib`
顶部 re-export，`convert.py` 门面与既有套件用法逐字不变。本模块不做语义改动。
"""

from __future__ import annotations

import os, re

from slcn import bibcheck as _bibcheck, bibdb as _bibdb
from slcn.convert_metadata import TEX_EXT, die


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

    第四十五批 A2（89-05 Jing_Liang 实测）：源稿在 `original/` 子目录里，而
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
    """初排转换**默认**的引用库通道：比对 + **默认回填**（B-217 ⑧ → B-135 P2）。

    Lead 2026-10-20 拍板「默认开只读比对」；用户 2026-10-24 拍板升级为**默认回填**；
    用户 2026-10-26 批准 B-135 方案 A（弱身份默认整条排除）+ P2（库值**全量应用**）：
      · 默认**开**（无新参数时）；`--no-bibdb-verify` / `--no-bibdb` 完全关闭；
      · `--no-bibdb-apply` 关闭应用 → 只读比对（日志/产物**逐字节等于旧行为**）；
      · 只对 HIT_SAFE 命中、按 28 类白名单 + doi/url/eprint（`slcn/bibdbapply.py`
        单一实现）写入：库内非空值**覆盖产物现值**或**补齐缺失字段**，库缺字段保留
        产物原值；候选/未命中一律不动（不臆造）；弱身份/重复 key/结构异常整条跳过；
        before/after 清单追加进「参考文献核对.txt」；
      · `--verify-refs` / `--verify-plan` 不做（核实分支自己查库并打印明细）；
      · 无 `reference.bib` / 无条目 / 库不存在 / 读库失败 → **完全静默**
        （公开仓、CI、没建库的机器上日志/产物零变化）。

    返回 `{done, store, path, entries, safe, candidates, missed, applied, filled,
    stats, checklist}`（测试/调试用；默认回填关闭时 applied 为空）。
    """
    out = {"done": False, "store": None, "path": "", "entries": [],
           "safe": {}, "candidates": {}, "missed": [],
           "applied": [], "filled": 0, "stats": {}, "checklist": ""}
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
        stats: dict = {}
        if not getattr(args, "no_bibdb_apply", False) and safe:
            applied, stats = _bibdb_apply_backfill(
                bib_path, bib_text, store, entries, safe)
        if applied:
            n_items = sum(len(a.get("filled") or {}) for a in applied)
            stem = _bibdb_scan_stem(res)
            print(f"   引用库比对：命中 {len(safe)} / 候选 {len(candidates)} / "
                  f"未命中 {len(missed)}（默认回填 {len(applied)} 条 / {n_items} 项，"
                  "候选与未命中不动）", flush=True)
            for a in applied:
                replaced = a.get("replaced") or {}
                parts = []
                for k, v in (a.get("filled") or {}).items():
                    if k in replaced:
                        parts.append(f"{k}=（旧值 {replaced[k]}）→ {v}")
                    else:
                        parts.append(f"{k}=（缺失）→ {v}")
                print(f"     · [{a.get('key')}] " + "、".join(parts), flush=True)
            try:
                from slcn import bibdbapply
                out["checklist"] = bibdbapply.write_checklist(
                    os.path.dirname(bib_path), stem, applied, stats=stats)
                print(f"     回填核对清单：{os.path.basename(out['checklist'])}", flush=True)
            except Exception:  # noqa: BLE001  清单写不了不影响已回填的 bib
                out["checklist"] = ""
        else:
            print(f"   引用库比对：命中 {len(safe)} / 候选 {len(candidates)} / "
                  f"未命中 {len(missed)}（只读，不改产物）", flush=True)
        out.update(done=True, store=store, path=path, entries=entries,
                   safe=safe, candidates=candidates, missed=missed,
                   applied=applied, stats=stats,
                   filled=sum(len(a.get("filled") or {}) for a in applied))
    except Exception:  # noqa: BLE001  附加通道：任何意外都不许连累出稿
        return out
    return out


def _bibdb_apply_backfill(bib_path: str, bib_text: str, store, entries,
                          safe: dict) -> tuple[list[dict], dict]:
    """默认回填（B-135 P2）：库值全量应用 + 原子写回；任何结构异常 → 不写（fail-soft）。

    返回 `(applied, stats)`：applied 与实际写回一致；stats 供日志/核对清单附注
    （类型差异 / 弱身份排除 / 重复 key / B-189 清理 / 结构异常）。
    """
    try:
        from slcn import bibdbapply
        from slcn.fsutil import atomic_write

        plans, stats = bibdbapply.plan_library_fills(store, entries, safe)
        new_text, applied = bibdbapply.apply_text(bib_text, plans, stats)
        if not applied or new_text == bib_text:
            return [], stats
        atomic_write(bib_path, new_text, newline="\n", log=None)
        return applied, stats
    except Exception:  # noqa: BLE001  附加通道：任何意外都不许连累出稿
        return [], {}


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
