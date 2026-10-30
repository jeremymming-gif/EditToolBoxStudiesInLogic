"""SLCN convert 拆分（B-229）：初排/协同/LLM 全文核查 + CLI 构造。纯搬运自 v1.4.0 `Engine/convert.py`；另含从 `main` 外提的`build_arg_parser()`/`run_coop_taskbook()`。"""
from __future__ import annotations

import argparse, inspect, os, re, shutil

from slcn import __version__ as ENGINE_VERSION, clean as _clean
from slcn import coop as _coop, platform as _plat, refmark as _refmark
from slcn.build import build_docx, build_latex  # 门面 main 直接调用（契约 i2：门面只 import 4 模块）
from slcn.convert_compile import compile_pdf
from slcn.convert_metadata import _OVERRIDE_FIELDS, die, is_own_product, resolve_c_class_union

# ⑪ 里 `finish_reason` → 中文说明。官方文档（2026-10，
# https://api-docs.deepseek.com/zh-cn/api/create-chat-completion ）给的取值集合是
# `stop` / `length` / `content_filter` / `tool_calls` / `insufficient_system_resource`
# / `aborted`。
#
# 为什么整本字典都要有：只有 `stop`（以及老端点「没给这个字段」= 空串）算干净收尾，
# 其余每一个都意味着**这次回复不是完整答案**。从前只盯 `length`，于是被内容过滤、
# 服务端资源不足、被中断的半截 bibtex 会**照样**追加进《文献核实任务书》，编辑看不出
# 它不完整——假完整比明显缺失更危险。`length` 的文案在 ⑪ 里单独保留（沿用既有说法，
# 编辑已经认识那句，且它带着「调大 SLCN_LLM_MAX_TOKENS」这条可操作建议）。
_FINISH_WHY = {
    "content_filter": "被内容过滤",
    "insufficient_system_resource": "服务端资源不足被中断",
    "aborted": "生成被中断",
    "tool_calls": "意外返回了工具调用",
    # responses 通道（`slcn/llm.py: _responses_finish()`）多出来的两种：端点的
    # `status` 直接就是失败/不明不完整，同样**不算完整答案**，必须标注（第 54/59 条）。
    "failed": "端点报告本次响应失败（status=failed）",
    "incomplete": "端点报告本次响应不完整（status=incomplete，未给原因）",
}


def channel_provenance(channel: str, protocol: str, model: str,
                       web_search: bool, n_search: int) -> str:
    """「六、合并记录」里那行**本次核实通道**说明（纯函数，便于逐字断言）。

    为什么要把「检索证据」数进去、而不只是写「已启用联网检索」：开关开着**不等于**
    端点真的搜了（DeepSeek 的 `deepseek-flash` 会把 `web_search` 静默忽略；
    `deepseek-v4-pro` 才会真检索）。如果这行只说「已启用联网检索」，
    编辑事后翻合并记录会以为这批条目是联网核实的——那正是本项目最反对的「静默假装」。
    所以：`n_search == 0` 时必须**明写**「没有检索证据、按凭记忆对待」。
    """
    prov = f"本次核实通道：{channel}（协议 `{protocol}`，模型 `{model}`）"
    if not web_search:
        return prov
    if n_search:
        return prov + f"；**检索证据**：收到 {n_search} 个 `web_search_call`，确属联网核实"
    return (prov + "；⚠ **检索证据 0 个 `web_search_call`** —— 端点忽略了这个工具，"
            "本次结果按**凭记忆**对待（仍须人工抽查卷/期/页码/出版地）")


# ── 源稿归档到 original/ ──────────────────────────────────────────────────


def stage_original(
    out_dir: str,
    files: list[tuple[str, str]],
    copy_dir: bool = False,
    verbose: bool = True,
) -> tuple[str, list[str]]:
    """把本次用到的源文件复制到 `<out_dir>/original/`。

    目录名 `original` 与往期各期保持一致（实测全库 92 篇文章用的都是这个名字；
    仓库根 README 早期写作「原稿」，现行为 `original/`）。

    files     [(路径, 角色说明), ...]，即 --source / --metadata / --bib
    copy_dir  True 时把源文件所在**整个目录**也复制进来。
              LaTeX 投稿常是「一个文件夹里放 main.tex + 旧稿 + bib + sty + pdf」，
              往期就是这么整包归档的（如 03 Jie_Zhou/original/03示例悖论（定稿）/）。
    """
    dest = os.path.join(out_dir, "original")
    os.makedirs(dest, exist_ok=True)
    dest_abs = os.path.abspath(dest)
    copied: list[str] = []
    copied_dir = ""

    # 先复制整目录（若启用）——这样后面的单文件复制就能判断「是否已包含在内」，
    # 避免同一个文件既平铺一份、又随目录一份。
    if copy_dir and files:
        parent = os.path.dirname(os.path.abspath(files[0][0]))
        parent_abs = os.path.abspath(parent) if parent else ""
        if parent and os.path.isdir(parent):
            if parent_abs == dest_abs or parent_abs.startswith(dest_abs + os.sep):
                # 源目录本来就在 original/ 内（重跑上次归档的多文件稿）→ 不能再整目录复制：
                # 否则 sub 恰等于 parent，下面 rmtree(sub) 会把源目录删掉（第 120 条评审发现）。
                if verbose:
                    print("   · 源文件已在 original/ 内：跳过整目录归档")
            elif not dest_abs.startswith(parent_abs + os.sep):
                sub = os.path.join(dest, os.path.basename(parent))
                sub_abs = os.path.abspath(sub)
                if sub_abs not in (parent_abs, dest_abs) and not parent_abs.startswith(
                    sub_abs + os.sep
                ):
                    if os.path.exists(sub):
                        shutil.rmtree(sub)
                    shutil.copytree(parent, sub)
                    copied_dir = parent_abs
                    n = sum(len(f) for _, _, f in os.walk(sub))
                    copied.append(f"{os.path.basename(parent)}/")
                    if verbose:
                        print(f"   · {os.path.basename(parent)}/  （整目录，{n} 个文件）")

    for path, role in files:
        if not path or not os.path.exists(path):
            continue
        src = os.path.abspath(path)
        # ① 已随整目录复制进去 → 不重复
        if copied_dir and src.startswith(copied_dir + os.sep):
            continue
        # ② 源文件本来就在目标 original/ 里（重跑已有稿件目录）→ 不重复
        if src.startswith(dest_abs + os.sep):
            if verbose:
                print(f"   · {os.path.basename(src)}（已在 original/ 内，跳过）")
            continue
        target = os.path.join(dest, os.path.basename(src))
        if os.path.exists(target) and os.path.samefile(src, target):
            continue
        shutil.copy2(src, target)
        copied.append(os.path.basename(src))
        if verbose:
            print(f"   · {os.path.basename(src)}  ← {role}")

    return dest, copied


def collect_overrides(args: argparse.Namespace) -> dict[str, str]:
    """把 7 个「正文信息覆盖」参数收成一个 dict；**空值一律不进来**。

    「空值不进来」很关键：只有 overrides 非空才谈得上传参，
    于是「一个都不填」的运行与旧版**逐字等价**（build 侧连关键字都收不到）。
    """
    out: dict[str, str] = {}
    for key in _OVERRIDE_FIELDS:
        val = (getattr(args, key, None) or "").strip()
        if val:
            out[key] = val
    return out


def supports_overrides(fn) -> bool:
    """探测 build 入口是否接受 `overrides=` 关键字（向后兼容的关键一步）。"""
    try:
        return "overrides" in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # 取不到签名（内建/C 实现）→ 当作不支持
        return False


def overrides_kwargs(fn, overrides: dict[str, str]) -> dict:
    """按入口是否支持 `overrides=` 组装关键字；不支持就提示后不传（不崩）。"""
    if not overrides:
        return {}
    if supports_overrides(fn):
        return {"overrides": overrides}
    print(f"  ⚠ {getattr(fn, '__name__', 'build')} 尚不支持 overrides 参数，"
          "本次手工覆盖未生效（需 build.py 增加 overrides: dict | None = None）")
    return {}


# ── ⑪ 的收尾：把 AI 结果并入 reference.bib（2026-10-15 用户决定）───────────
#
# 旧口径是「AI 结果永不进产物」（README 第 41 条）：工具只出任务书，建议由编辑自己搬。
# 用户 2026-10-15 决定改掉它 —— AI 返回的条目要**直接并进 reference.bib**，这样
# `--compile` 出来的 PDF 就已经反映了核实结果。代价是「机器直接改学术事实」，所以：
#
#   · 合并的全部安全判据在 `slcn/aibib.py`（四条硬保障：key 不可改 / 恰好一条 /
#     不增删条目 / 未返回的条目逐字节不动），这里只负责落盘与告知；
#   · **回滚副本 `reference.bib.ai前`** = 本次合并之前的那一份（重跑会覆盖它）；
#   · 并完立刻跑一遍**离线**回读校验（`slcn/bibreport.py`，与 checkbib.py 同一份实现），
#     报告落在 `<stem>_回读校验报告.md`，结论一行打进控制台；
#   · 任务书追加「六、合并记录」：哪几条并了、改了什么、哪几条被挡下、为什么；
#   · **一条都没并进去时不动 bib、不写回滚副本**，并明说「未改动」——
#     「0 条并入」绝不能长得像成功。
#
# ⚠️ 整个函数**不许抛异常**：AI 通道是附加功能，它出问题不能连累出稿。


def apply_ai_merge(bib_path: str, taskbook_path: str, replies: dict[str, str],
                   stem: str, out_dir: str,
                   *, rejected: dict[str, str] | None = None,
                   provenance: str = "",
                   out_of_scope: set[str] | None = None,
                   bibdb_hits: set[str] | None = None) -> dict:
    """并入 + 回滚副本 + 回读校验报告 + 任务书「合并记录」。

    `replies` 只放**正常收尾**的回复；`rejected` 放「AI 确实回了、但返回不完整
    （截断/内容过滤/中断）」的键 → 中文原因，它们**不并入**，但原因要写进合并记录。
    `provenance` = 本次核实通道的一行说明（协议 + 是否联网检索 + 模型名），写进
    「六、合并记录」——**留档**要让编辑事后能分清「联网查过的」与「凭记忆写的」。
    `out_of_scope` = **本次核实范围之外**的 bib 键（正文未引用；第 128 条）：传下去让
    「六、合并记录」把它们与「AI 没返回/被挡下」分开统计——346 条未引用条目不该被写成
    346 次 AI 失败。默认 None = 全量范围（旧行为，输出逐字节相同）。
    `bibdb_hits` = **命中编辑部引用库、本次没调 AI** 的 bib 键（第 129 条）：传下去让
    「六、合并记录」把「库内命中（未调 AI）」单独统计一行，而不是把它们写成「AI 未返回」。
    默认 None / 空集 = 旧输出逐字节相同。

    返回一份中文摘要 dict（`n_merged` / `n_skipped` / `rollback` / `report` /
    `conclusion` / `error`），由调用方决定怎么打印（0 条并入时不许像成功）。
    """
    from slcn import aibib, bibreport

    out: dict = {"n_merged": 0, "n_skipped": 0, "rollback": "", "report": "",
                 "conclusion": "", "error": ""}
    try:
        if not bib_path or not os.path.exists(bib_path):
            out["error"] = "本次没有 reference.bib，无从并入"
            return out
        original = open(bib_path, encoding="utf-8", errors="replace").read()
        result = aibib.merge(original, replies)
        for k, why in (rejected or {}).items():
            rec = result.by_key(k)
            if rec is not None and not rec.merged:
                rec.reason = why
        out["n_merged"], out["n_skipped"] = result.n_merged, result.n_skipped

        # 任务书的「合并记录」：**不管并进去几条都写**——被挡下来的原因必须留下来给人看。
        # ⚠️ 第 128 条：范围外的键单独传下去（否则 346 条「正文没引用」会被写成
        #    346 次「AI 未返回」）。
        if taskbook_path and os.path.exists(taskbook_path):
            with open(taskbook_path, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n" + aibib.render_merge_section(
                    result, provenance=provenance, out_of_scope=out_of_scope,
                    bibdb_hits=bibdb_hits) + "\n")

        if result.n_merged == 0:
            out["error"] = "没有任何条目通过安全校验，reference.bib **未改动**"
            return out

        # ① + ② 回滚副本（先）与写回（后）：与手动「并回」通道（`checkbib.py --merge-into`）
        #    **共用**同一次落盘 —— `slcn/aibib.py: apply_merge_to_file()`。
        #    「先备份、后覆盖」这条安全轨只留一份实现，两条通道不许各写一遍。
        rollback = aibib.apply_merge_to_file(bib_path, result)
        out["rollback"] = rollback

        # ③ 立刻离线回读校验（与 checkbib.py 同一份报告实现）
        tex_path = os.path.join(out_dir, f"{stem}.tex")
        tex_text = None
        if os.path.exists(tex_path):
            tex_text = open(tex_path, encoding="utf-8", errors="replace").read()
        md, conclusion = bibreport.build_report_and_conclusion(
            result.text, original, tex_text,
            "合并后参考文献核对报告",
            returned_path=bib_path,
            original_path=rollback,
            tex_path=tex_path if tex_text is not None else "",
            source_note=bibreport.SOURCE_NOTE_MERGE,
        )
        report_path = os.path.join(out_dir, f"{stem}_回读校验报告.md")
        with open(report_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(md + "\n")
        out["report"], out["conclusion"] = report_path, conclusion
        return out
    except Exception as e:  # noqa: BLE001  附加通道，任何意外都不许连累出稿
        out["error"] = f"{type(e).__name__}: {e}"
        return out


# ── 第五十四批：AI 协同初排·第二段（回填 → 并入/建议 → 编译 → 原子交付）──────


def _coop_find_bib(state: dict, stage_dir: str, stem: str) -> str:
    """找暂存目录里的 reference.bib；状态里有绝对路径就优先信它。"""
    p = str(state.get("reference_bib") or "")
    if p and os.path.isfile(p):
        return p
    for cand in ("reference.bib", f"{stem}.bib"):
        q = os.path.join(stage_dir, cand)
        if os.path.isfile(q):
            return q
    return ""


def _coop_find_tex(state: dict, stage_dir: str, stem: str) -> str:
    """找暂存目录里的输出 .tex；状态里有绝对路径就优先信它（第五十七批）。"""
    p = str(state.get("tex") or "")
    if p and os.path.isfile(p):
        return p
    cand = os.path.join(stage_dir, f"{stem}.tex")
    if os.path.isfile(cand):
        return cand
    try:
        texs = [os.path.join(stage_dir, n) for n in sorted(os.listdir(stage_dir))
                if n.endswith(".tex") and os.path.isfile(os.path.join(stage_dir, n))]
    except OSError:
        return ""
    return texs[0] if len(texs) == 1 else ""


def _coop_locate_state(args) -> tuple[str, dict | None, list[str]]:
    """找 ph1 暂存目录：显式 --coop-stage > --name 推默认 > 扫 `<out>/*_待核/`。

    返回 `(选中目录, 状态, 所有候选目录)`；候选 >1 且用户没指定时返回状态 None
    （让调用方报「请用 --coop-stage/--name 明确一个」）。
    """
    from slcn import coop

    out_root = os.path.abspath(args.out)
    cands: list[str] = []
    if args.coop_stage:
        cands.append(os.path.abspath(args.coop_stage))
    else:
        if args.name:
            cands.append(os.path.join(out_root, f"{args.name}_待核"))
        if os.path.isdir(out_root):
            cands += [
                os.path.join(out_root, d) for d in sorted(os.listdir(out_root))
                if d.endswith("_待核") and os.path.isdir(os.path.join(out_root, d))
            ]
    seen: list[str] = []
    found: list[tuple[str, dict]] = []
    for c in cands:
        if c in seen:
            continue
        seen.append(c)
        st = coop.load_state(c)
        if st is not None:
            found.append((c, st))
    if len(found) == 1:
        return found[0][0], found[0][1], seen
    if not found:
        return "", None, seen
    if args.coop_stage or args.name:
        return found[0][0], found[0][1], seen
    return "", None, seen


def run_coop_replies(args, *, source: str, metadata: str, out_root: str,
                     final_dir: str, stage_dir: str, stem: str,
                     lang: str, sty: str, skeleton: str,
                     structured: dict | None = None,
                     structured_origin: str = "") -> int:
    """`--coop-replies` 的全部收尾逻辑（见模块头注释的第三段）。

    任何一步失败都**不 promote**：最终交付目录要么是旧的那一份，要么根本不存在。
    """
    from slcn import coop

    print("\n⑬ AI 协同初排·第二段（外部 chat 回填）")
    if args.dry_run:
        print("（--dry-run：只显示将要做什么，不解析/不并入/不交付）")
        return 0
    state = coop.load_state(stage_dir)
    if state is None:
        die(f"暂存目录里没有可读的 `{coop.STATE_NAME}`：{stage_dir}\n"
            "  请先运行第一段：--verify-refs --no-ai-merge --coop-taskbook-only")

    params = coop.build_params(
        source=source, metadata=metadata, out_root=out_root, stem=stem,
        lang=lang, sty=sty, skeleton=skeleton,
        verify_refs=bool(args.verify_refs), no_ai_merge=bool(args.no_ai_merge),
        end_list_priority=bool(getattr(args, "end_list_priority", False)),
        number_cite_map=bool(getattr(args, "number_cite_map", False)),
        c_class_union=resolve_c_class_union(args), stage_dir=stage_dir,
        # B-287：ph2 不必重复给 JSON；显式给了才把 sha 带进本次参数（不参与
        # check_ph2 的逐项比较，单独校验——见下）。
        structured_sha256=(coop.structured_metadata_sha256(structured)
                          if structured else ""),
    )
    ok, why = coop.check_ph2(state, params)
    if not ok:
        die(why)
    # B-287：第二段不能改人工元数据。显式给了 JSON 时与初排记录比 sha256；
    # 不给则沿用暂存 `.tex` 里的值（promote 直接交付，不重排版）。
    _ok_sm, _why_sm = coop.check_structured_metadata(state, structured)
    if not _ok_sm:
        die(_why_sm)
    if structured:
        print(f"   人工元数据与初排一致（sha256 {coop.structured_metadata_sha256(structured)[:12]}…；"
              f"来源 {structured_origin or 'cli-json'}）")

    # 最终目录已有内容时，只有「全是本工具产物」才允许原子替换；否则要 --force。
    if os.path.isdir(final_dir) and not args.force:
        foreign = [c for c in sorted(os.listdir(final_dir))
                   if not is_own_product(c, stem) and not c.startswith(".")
                   and c not in ("_AI协同记录.md",)
                   and c != f"{stem}_文献核实.md"
                   and not c.startswith(f"{stem}_AI建议.")]
        if foreign:
            die(f"最终交付目录已存在，且含非本工具的文件，拒绝原子替换：{final_dir}\n"
                f"  不属于本工具产物的 {len(foreign)} 项："
                f"{'、'.join(foreign[:6])}{' …' if len(foreign) > 6 else ''}\n"
                "  确认要覆盖请加 --force。")

    try:
        texts = coop.load_reply_texts(args.coop_replies)
    except OSError as e:
        die(f"读不了回填路径：{e}")
    if not texts:
        die(f"回填路径里没有可读文本：{coop.format_reply_paths(args.coop_replies)}"
            "（支持 .md/.txt/.bib/.json 等）")

    # 批74（B-133）：附四「初排分析」——只在 ph1 启用 --coop-fulltext 时解析
    # （state.analysis）；旧 state 无该键 → 按未启用处理（行为与旧版逐字节相同）。
    # **report-only**：解析结果只落报告，不进入任何 apply 通道（`--coop-apply` 也不碰它）。
    _analysis_state = state.get("analysis") or {}
    _analysis_on = (bool(_analysis_state.get("enabled"))
                    and str(_analysis_state.get("schema") or "") == coop.ANALYSIS_SCHEMA)
    analysis_report = None
    if _analysis_on:
        _tb_text = ""
        _tb_path = str(state.get("taskbook") or "")
        if _tb_path and os.path.isfile(_tb_path):
            try:
                with open(_tb_path, encoding="utf-8", errors="replace") as _f:
                    _tb_text = _f.read()
            except OSError as e:
                print(f"   ⚠ 附四：读不了任务书，evidence 校验将全部拒绝：{e}")
        # 批75（B-140①）：evidence 校验基准**不再是整份任务书**（旧口径下 AI 可抄附四
        # schema 示例/指令当逐字证据）；只传「附四之前」的内容（附三正文 + 附一原文 +
        # 附二/附三说明），由 `analysis_evidence_base()` 截断（附四小节整段排除）。
        analysis_report = coop.parse_fulltext_analysis(
            texts, coop.analysis_evidence_base(_tb_text))

    keys = [str(k) for k in (state.get("keys") or {}).get("bib") or []]
    low_items = list(state.get("low_confidence") or [])
    parse_report = coop.parse_replies(texts, keys)
    low_report = coop.parse_low_confidence(texts, low_items)
    digest = coop.replies_digest(texts)

    # 第五十六批：--coop-replies 可重复。回填来源要能看出「读了几个路径」，否则
    # 「chat 把回复拆成 json+txt 两个文件、只导了一个」在日志里看不出来。
    _n_paths = len(args.coop_replies or [])
    print(f"   回填来源：{len(texts)} 个文本 / {_n_paths} 个路径"
          "（多路径按命令行顺序；目录内按文件名排序；同一路径去重）")
    print(f"   文献字段：{parse_report.summary()}")
    print(f"   低置信项：{low_report.summary()}")
    if analysis_report is not None:
        print(f"   全文分析（附四，report-only）：{analysis_report.summary()}")

    if state.get("phase") == "done":
        if state.get("replies_sha256") == digest:
            print("   （幂等：这份回填已经处理并交付过，本次未重复应用任何改动）")
            print("═" * 74)
            print(f"完成。产物目录：{state.get('delivered_to') or final_dir}")
            return 0
        die("这个暂存目录已经完成并交付过，且本次回填内容与上次不同；"
            "为避免重复并入，拒绝继续。要重新处理请先重新初排（--coop-taskbook-only）。")

    for k, why2 in parse_report.rejected.items():
        print(f"   ⚠ 拒绝 `{k}`：{why2}")
    for k, why2 in low_report.rejected.items():
        print(f"   ⚠ 拒绝低置信项 `{k}`：{why2}")
    if parse_report.unknown:
        print(f"   ⚠ 任务书外的文献键 {len(parse_report.unknown)} 个（已忽略，不猜）："
              + "、".join(parse_report.unknown[:6]))
    if low_report.unknown:
        print(f"   ⚠ 任务书外的低置信 ID {len(low_report.unknown)} 个（已忽略，不猜）："
              + "、".join(low_report.unknown[:6]))
    for rec in (analysis_report.rejected if analysis_report is not None else [])[:6]:
        print(f"   ⚠ 拒绝附四建议 `{rec.get('id', '')}`：{rec.get('reason', '')}")
    if analysis_report is not None and analysis_report.unknown_kinds:
        print(f"   ⚠ 附四未知 kind {len(analysis_report.unknown_kinds)} 个（已记录、未采用）："
              + "、".join(f"{r.get('kind', '')}" for r in analysis_report.unknown_kinds[:6]))

    if not parse_report.replies and not low_report.suggestions and not (
            analysis_report is not None and analysis_report.total):
        # 批74：仅附四回复（附一/文献部分全空）**不算**「回复全部无效」——附四本身
        # 就是一次有效回填（report-only），不能在这里退出 2。
        die("回填回复**全部无效**或没有命中任务书里的任何键/ID；"
            "最终交付目录与 reference.bib 均未改动。请检查回填文件后重试。")

    # 第五十六批：真实首测（2026-10-19 22:00）里，chat 把回复拆成「JSON + BibTeX」两个
    # 文件，GUI 只导入了 JSON → 文献回填 0/9，而摘要只显示低置信的 accepted（22），
    # 编辑以为全成了。所以「一篇 bib 都没回填」必须在解析后、交付前**醒目**打出来。
    if len(keys) > 0 and parse_report.n_ok == 0:
        print("   " + "⚠" * 3 + " 本次未收到任何文献条目回填，reference.bib 不会并入 AI 修订；"
              "若外部 chat 已返回 bibtex/bib，可能漏导入——"
              "--coop-replies 可重复传多个文件/目录")

    bib_path = _coop_find_bib(state, stage_dir, stem)
    taskbook = str(state.get("taskbook") or "")
    merge_summary: dict | None = None

    # ① 文献字段并入（复用既有的 apply_ai_merge：四条硬保障 + 回滚副本 + 回读报告）
    if parse_report.replies and not args.no_ai_merge:
        if not bib_path:
            merge_summary = {"n_merged": 0, "n_skipped": 0, "rollback": "", "report": "",
                             "conclusion": "", "error": "暂存目录里没有 reference.bib，无从并入"}
            print("   ⚠ 暂存目录里没有 reference.bib：文献字段未并入")
        else:
            print(f"   把 {len(parse_report.replies)} 条回填回复并入 {os.path.basename(bib_path)}…")
            merge_summary = apply_ai_merge(
                bib_path, taskbook, parse_report.replies, stem, stage_dir,
                rejected=parse_report.rejected,
                provenance="外部 chat 回填（编辑粘贴；本工具**未调用 API**）",
            )
            if merge_summary.get("n_merged"):
                print(f"   ✅ 已并入 {merge_summary['n_merged']} 条 / 跳过 "
                      f"{merge_summary['n_skipped']} 条（逐条原因见任务书「六、合并记录」）")
                if merge_summary.get("rollback"):
                    print(f"   回滚副本：{os.path.basename(merge_summary['rollback'])}")
            else:
                print(f"   ⚠ {merge_summary.get('error') or '没有任何条目通过安全校验'}"
                      "（reference.bib 未改动）")
    elif parse_report.replies and args.no_ai_merge:
        print("   （--no-ai-merge：本次**不**把回填的文献字段并入 reference.bib）")

    # ② 低置信建议：默认只写文件；--coop-apply 才动产物
    #    BIB-* → reference.bib（第五十四批）；META-* / FN-* → 输出 .tex（第五十七批）。
    apply_summary: dict | None = None
    if args.coop_apply:
        if args.no_ai_merge:
            print("   （--coop-apply 被 --no-ai-merge 覆盖：低置信建议只记录，不改产物）")
        else:
            _apply_parts: list[dict] = []
            _low_by_id = {str(it["id"]): it for it in low_items}
            # 只把文献字段建议交给 bib 应用器（其余交给 tex 应用器，避免重复记「待人工」）。
            _bib_sugs = {
                _id: _sugs for _id, _sugs in low_report.suggestions.items()
                if str((_low_by_id.get(_id) or {}).get("kind") or "") == "bib_entry"
            }
            if not bib_path:
                print("   ⚠ --coop-apply：暂存目录里没有 reference.bib，文献字段建议只记录")
            elif _bib_sugs:
                _bib_apply = coop.apply_bib_suggestions(bib_path, _bib_sugs, low_items)
                _apply_parts.append(_bib_apply)
                if _bib_apply.get("n_applied"):
                    print(f"   ✅ --coop-apply：已应用 {_bib_apply['n_applied']} 条文献字段建议"
                          f"（备份 {os.path.basename(_bib_apply.get('rollback') or '')}）")
                else:
                    print(f"   ⚠ --coop-apply：{_bib_apply.get('error') or '没有可应用的建议'}")
            _tex_path = _coop_find_tex(state, stage_dir, stem)
            if not _tex_path:
                print("   ⚠ --coop-apply：暂存目录里找不到输出 .tex，元数据/脚注建议只记录")
            else:
                _tex_apply = coop.apply_tex_suggestions(
                    _tex_path, low_report.suggestions, low_items,
                    taskbook_keys=keys, bib_path=bib_path)
                _apply_parts.append(_tex_apply)
                if _tex_apply.get("n_applied"):
                    print(f"   ✅ --coop-apply：已应用到 .tex {_tex_apply['n_applied']} 条"
                          f"（备份 {os.path.basename(_tex_apply.get('rollback_tex') or '')}）")
                elif _tex_apply.get("error"):
                    print(f"   ⚠ --coop-apply：{_tex_apply['error']}")
            if _apply_parts:
                apply_summary = coop.merge_apply_summaries(*_apply_parts)
    sug_md, sug_jsonl = coop.write_suggestions(stage_dir, stem, low_items, low_report)
    print(f"   低置信建议（默认不改产物）：{os.path.basename(sug_md)} / "
          f"{os.path.basename(sug_jsonl)}")
    # 批74（B-133）：附四报告（report-only）——写进暂存目录，随 promote 进交付目录。
    # 只有收到 ≥1 个附四对象才落盘（旧回复/未启用 = 不生成报告文件，行为与旧版相同）。
    if analysis_report is not None and analysis_report.total:
        ana_md, ana_jsonl = coop.write_analysis_report(stage_dir, stem, analysis_report)
        print(f"   全文分析报告（report-only，不写产物）：{os.path.basename(ana_md)} / "
              f"{os.path.basename(ana_jsonl)}")

    # ③ 编译（必须在 promote 之前；编译失败时不交付）
    compiled = "未请求 --compile"
    if args.compile:
        print("\n⑨ 编译（暂存目录）")
        ok, info = compile_pdf(stage_dir, stem)
        if not ok:
            die(f"编译未通过，**未交付**：{info}\n"
                f"  暂存目录保留在 {stage_dir}；最终交付目录未改动。")
        compiled = f"✅ {os.path.basename(info)}（{max(1, os.path.getsize(info) // 1024)} KB）"
        print(f"   {compiled}")

    # ③′ --clean（第五十六批）：先把暂存目录里的编译中间文件清掉，再原子 promote。
    #    批55 的 ph2 完全不看 --clean，真实用户交付目录里多出了
    #    .aux/.bbl/.bcf/.blg/.out/.run.xml 六个中间文件；清理规则复用 slcn.clean
    #    （只删后缀表里的，.tex/.bib/.pdf/.log/状态/记录/建议/.ai前 一律不动）。
    if args.clean:
        print("\n⑫ 清理编译中间文件（--clean；再原子交付）")
        _n_cleaned, _ = _clean.clean(stage_dir, log=lambda s: print(f"   {s}"))
        if _n_cleaned == 0:
            print("   （没有需要清理的文件）")

    # ④ 记录 + 状态（记录随交付走；状态文件留在暂存目录）
    pending = [k for k in keys if k in parse_report.missing]
    pending += [str(it["id"]) for it in low_items
                if str(it["id"]) not in low_report.suggestions]
    # 第五十六批：机器可读统计（schema 仍 1，仅增字段）——GUI 靠它显示
    # 「文献回填 x/y（未回填 n）｜低置信回复 a（应用 b / 仅记录待人工 c）」，
    # 而不是只数 jsonl 的 accepted（那会把 0/9 回填显示成「待人工 0」）。
    _stats = coop.make_stats(
        bib_total=len(keys), bib_replied=parse_report.n_ok,
        low_total=len(low_items), low_replied=low_report.n_ok,
        low_applied=int((apply_summary or {}).get("n_applied") or 0))
    try:
        st2 = coop.mark_state_ph2(
            state, replies_digest=digest,
            replied=sorted(parse_report.replies), applied=int(
                (apply_summary or {}).get("n_applied") or 0),
            rejected=len(parse_report.rejected) + len(low_report.rejected),
            pending=pending, stats=_stats)
        coop.save_state(stage_dir, st2)
    except OSError as e:
        print(f"   ⚠ 状态文件写入失败（不影响本次交付）：{type(e).__name__}: {e}")

    # ⑤ 原子 promote：失败不破坏既有最终目录
    print(f"\n⑬ 原子交付 → {final_dir}")
    try:
        promoted = coop.promote_stage(stage_dir, final_dir, exclude=(coop.STATE_NAME,))
    except Exception as e:  # noqa: BLE001  交付失败必须收口，最终目录保持原样
        die(f"原子交付失败（最终交付目录保持原样）：{type(e).__name__}: {e}\n"
            f"  可修复后重跑 --coop-replies；暂存目录仍在 {stage_dir}")
    # 批79（B-131）：交付记录口径——**只读汇总**（不写产物；不新写解析器）。
    # 旧状态无 delivery 标记 → `None` → 记录与旧版逐字节相同（来源：新 ph1 才写该标记）。
    _delivery = None
    _dm = state.get("delivery")
    if isinstance(_dm, dict) and int(_dm.get("schema") or 0) == coop.DELIVERY_SCHEMA:
        _dbib = _coop_find_bib(state, stage_dir, stem)
        _dtex = _coop_find_tex(state, stage_dir, stem)
        _dbib_keys = None
        if _dbib and os.path.isfile(_dbib):
            try:
                with open(_dbib, encoding="utf-8", errors="replace") as _f:
                    _dbib_keys = [b.key for b in coop.parse_bib_blocks(_f.read())
                                  if b.closed]
            except OSError:
                _dbib_keys = None
        _dcited, _dnocite = None, False
        if _dtex and os.path.isfile(_dtex):
            try:
                with open(_dtex, encoding="utf-8", errors="replace") as _f:
                    _dcited, _dnocite = _refmark.cited_keys_from_body(_f.read())
            except OSError:
                _dcited = None
        _delivery = coop.build_delivery_summary(
            bib_keys=_dbib_keys, cited_keys=_dcited, nocite_all=_dnocite,
            low_items=low_items,
            bibdb_checked=bool(_dm.get("bibdb_checked")),
            bibdb_safe=_dm.get("bibdb_safe"),
            bibdb_missed=_dm.get("bibdb_missed"))
    record = coop.write_record(
        stage_dir, stem=stem, phase="done", parse_report=parse_report,
        low_report=low_report, merge_summary=merge_summary,
        apply_summary=apply_summary, final_dir=final_dir,
        no_ai_merge=bool(args.no_ai_merge), compiled=compiled,
        backup=promoted.get("backup", ""),
        fulltext=state.get("fulltext"),
        # 批74：协同记录「六」节只在**真的收到附四对象**时出现（spec §2.4）；
        # 启用附四但回复里没有附四对象 → 与旧版记录逐字节相同（旧回复兼容）。
        analysis_report=(analysis_report if analysis_report is not None
                         and analysis_report.total else None),
        # 批75（B-132）：记录「清单缺条」节的数据源（现有 C 类低置信项；无则记录不变）。
        low_items=low_items,
        # 批79（B-131）：交付记录节（清单/引用/打印/未打印/库命中；旧状态=None）。
        delivery=_delivery)
    # 记录是在 promote 之后写的：把它也放进最终目录（暂存目录里同样留一份）。
    try:
        final_record = os.path.join(final_dir, coop.RECORD_NAME)
        with open(record, encoding="utf-8") as f:
            text = f.read()
        with open(final_record, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except OSError as e:
        print(f"   ⚠ 协同记录复制进交付目录失败：{type(e).__name__}: {e}")

    try:
        st3 = coop.mark_state_done(st2, final_dir=final_dir)
        coop.save_state(stage_dir, st3)
    except OSError as e:
        print(f"   ⚠ 状态文件写入失败：{type(e).__name__}: {e}")

    # B-211：交付成功后清理暂存目录（promote 成功才走得到这里；失败保留现场）。
    if coop.cleanup_stage(stage_dir):
        print(f"  暂存目录已清理：{stage_dir}")
    else:
        print(f"   ⚠ 暂存目录未清理（保留）：{stage_dir}")

    if promoted.get("backup"):
        print(f"   旧交付目录已备份：{os.path.basename(promoted['backup'])}")
    # 第五十六批：最终摘要统一为「x/y + 可应用/待人工」口径（数字全部来自状态 stats）。
    print(f"   回填：{_stats['bib_replied']} 条（未回填 {_stats['bib_pending']} 条）；"
          f"低置信回复：{_stats['low_replied']} 条"
          f"（可应用 {_stats['low_applied']} 条 / "
          f"仅记录待人工 {_stats['low_pending_manual']} 条）")
    print(f"   协同记录：{coop.RECORD_NAME}")
    print("═" * 74)
    print(f"完成。产物目录：{final_dir}")
    return 0


# ── 第六十批：全文 AI 核查·通道②（直连 API；report-only；默认关）────────────
#
# 用户 2026-10-20 授权全文外发 AI，但要求**可选项**：`--llm-fulltext` 默认不带=关，
# 只在 ⑪（`--verify-refs` 直连 AI 路径）生效。本通道**只出报告、不改产物**
# （reference.bib / .tex 一字不动）；自动应用需用户另行拍板，本批不做。
# 回复只接受附一 JSON 契约里的**既有低置信 ID**（白名单沿用 coop.parse_low_confidence），
# 不新增改正文类建议；超时/HTTP 错/截断/JSON 不过一律 fail-soft（该片标不可用、照常出稿）。

#: 每片正文的上限字符数（≈10k tokens；片数随正文长度增加，预算闸按片数×单片估算）。
LLM_FULLTEXT_CHUNK_CHARS = 30000
#: report-only 报告文件名后缀（`<stem>_AI全文复核.md`，属本工具产物，--clean 不清）。
LLM_FULLTEXT_REPORT_SUFFIX = "_AI全文复核.md"

#: 全文复核专用提示词（追加在 ⑪ 现有规范材料之后；不改既有 entry_prompt 口径）。
LLM_FULLTEXT_RUBRIC = """\
## 全文复核专用说明（本次用户已显式授权发送稿件正文）

> 用户消息里会给出**稿件正文的节选**（可能分多片，每片单独一次请求）与本片的低置信项清单。
> 请只依据给出的正文与清单，按「附一、初排低置信项」规定的 JSON 格式逐条回复；
> 文献条目部分仍按第三节 BibTeX 规范输出。
> **不要改写、复述或补写正文**，也不要把正文里没有依据的推断当成建议；拿不准就回 `unresolved`。
> 引用键只能取自任务书/正文里已有的键，**不许新造 key**。"""


def split_fulltext_chunks(body: str, max_chars: int = LLM_FULLTEXT_CHUNK_CHARS) -> list[str]:
    """按段落把正文切成 ≤max_chars 字符的片（单段超长则硬切；顺序保存、不丢内容）。

    为什么按段落：LaTeX/Word 正文一行一段，按段落切不会把一句/一个公式切两半；
    单段本身超过上限（极长的表格/整篇不分段）时只能按字符硬切——**显式接受**这种
    边界，因为 AI 读片不需要结构完整，而“不丢内容”比“切得漂亮”重要。
    """
    text = str(body or "").strip()
    if not text:
        return []
    max_chars = max(1000, int(max_chars or LLM_FULLTEXT_CHUNK_CHARS))
    chunks: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        piece = para.strip("\n")
        if not piece:
            continue
        while len(piece) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(piece[:max_chars])
            piece = piece[max_chars:]
        if not cur:
            cur = piece
        elif len(cur) + 2 + len(piece) <= max_chars:
            cur += "\n\n" + piece
        else:
            chunks.append(cur)
            cur = piece
    if cur:
        chunks.append(cur)
    return chunks


def _fulltext_chunk_items(chunk: str, low_items: list[dict], *, first: bool) -> list[dict]:
    """挑出「本片涉及」的低置信项：evidence 片段能在本片正文里找到的（前缀命中）。

    元数据/文献条目类（evidence 是字段名/条目原文，不在正文里）统一挂到**第一片**——
    否则后面的片永远看不到它们，AI 也不会回。顺序保持 low_items 原序（结果确定）。
    """
    squeezed = " ".join(str(chunk or "").split())
    picked: list[dict] = []
    for it in low_items:
        ev = " ".join(str(it.get("evidence") or "").split())
        if ev and ev[:80] in squeezed:
            picked.append(it)
    if first:
        seen = {str(it.get("id")) for it in picked}
        for it in low_items:
            if str(it.get("id")) in seen:
                continue
            if str(it.get("kind") or "") in ("metadata", "bib_entry", "bib_line"):
                picked.append(it)
    return picked


def _llm_fulltext_system() -> str:
    """全文通道的 system：沿用 ⑪ 既有全稿复核规范 + 全文专用说明（不改既有常量）。"""
    return "\n".join((_refmark.SPEC_MD, _refmark.SOURCES_MD, _refmark.FORMAT_MD,
                      LLM_FULLTEXT_RUBRIC))


def _llm_fulltext_user(idx: int, total: int, chunk: str, items: list[dict]) -> str:
    """全文通道的 user：本片正文 + 本片涉及的低置信项（附一格式清单）。"""
    parts = [f"## 本片正文（第 {idx}/{total} 片，共 {len(chunk)} 字符）", "",
             "```text", chunk, "```", ""]
    if items:
        parts += ["## 本片需要核对的低置信项（只按附一 JSON 格式逐条回复）", "",
                  _coop.render_low_confidence_section(items)]
    else:
        parts += ["## 本片没有需要核对的低置信项（无需回复 JSON）"]
    return "\n".join(parts)


def _llm_fulltext_review(cfg, chunks: list[str], items_per_chunk: list[list[dict]],
                         *, max_tokens: int | None, thinking: dict | None,
                         timeout: int | None = None) -> dict:
    """逐片调用直连 LLM 做全文复核（report-only）；任何单片失败都 fail-soft。

    返回 `{n_chunks, n_requests, n_suggestions, chunks, failures}`；每个 chunk 记录
    `{index, chars, n_items, status(ok|unavailable), accepted, rejected, unknown, error}`。
    `accepted` 里的每条建议都带 `id/verdict/field/value/span/note/locator`（来自
    `coop.parse_low_confidence` 的校验结果，**未通过校验的一律不进来**）。
    """
    from slcn import llm as _llm

    result: dict = {"n_chunks": len(chunks), "n_requests": 0, "n_suggestions": 0,
                    "chunks": [], "failures": []}
    system = _llm_fulltext_system()
    send_thinking = thinking
    thinking_warned = False
    for i, chunk in enumerate(chunks, 1):
        items = items_per_chunk[i - 1] if i - 1 < len(items_per_chunk) else []
        user = _llm_fulltext_user(i, len(chunks), chunk, items)
        entry: dict = {"index": i, "chars": len(chunk), "n_items": len(items),
                       "status": "", "accepted": [], "rejected": {},
                       "unknown": [], "error": "", "tokens": {}}
        kwargs: dict = {"response_format": None, "max_tokens": max_tokens,
                        "thinking": send_thinking}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            got = _llm.complete(cfg, system, user, **kwargs)
        except Exception as e:  # noqa: BLE001  附加通道：任何意外都不许连累出稿
            got = None
            entry["error"] = f"{type(e).__name__}: {e}"
        result["n_requests"] += 1
        # thinking 是可选扩展：端点 400 拒收时（判据在 llm.thinking_rejected）本条
        # 去掉字段重试一次，成功则本次运行余下分片也不再发它（与 ⑪ 逐条通道同口径）。
        if got is None and send_thinking is not None and _llm.thinking_rejected():
            if not thinking_warned:
                print("   ⚠ 端点不支持 thinking 字段（HTTP 400），全文复核改为不带该字段重试；"
                      "本次运行其余分片也不再发送它")
                thinking_warned = True
            send_thinking = None
            kwargs["thinking"] = None
            result["n_requests"] += 1
            try:
                got = _llm.complete(cfg, system, user, **kwargs)
            except Exception as e:  # noqa: BLE001
                got = None
                entry["error"] = f"{type(e).__name__}: {e}"
        if not got:
            entry["status"] = "unavailable"
            entry["error"] = entry["error"] or str(_llm.LAST_ERROR or "连接失败/超时/空回复")
            result["failures"].append(entry)
            result["chunks"].append(entry)
            continue
        finish = str(getattr(_llm, "LAST_FINISH", "") or "")
        if finish not in ("", "stop"):
            entry["status"] = "unavailable"
            entry["error"] = f"返回未正常结束（finish_reason={finish}），内容不完整"
            result["failures"].append(entry)
            result["chunks"].append(entry)
            continue
        try:
            use = dict(getattr(_llm, "LAST_USAGE", {}) or {})
        except Exception:  # noqa: BLE001
            use = {}
        entry["tokens"] = {k: int(v) for k, v in use.items()
                           if k in ("prompt_tokens", "completion_tokens",
                                    "reasoning_tokens", "total_tokens")}
        # 先判「回复里到底有没有 JSON 对象」：没有任何 JSON = 该片不可用；
        # 有 JSON 但只覆盖部分 ID 时仍算成功（未覆盖的项就是 missing，不报错）。
        _n_json = sum(1 for _ in _coop.iter_json_objects(got))
        if _n_json == 0:
            entry["status"] = "unavailable"
            entry["error"] = "回复里没有可解析的 JSON 低置信项（不是附一格式）"
            result["failures"].append(entry)
            result["chunks"].append(entry)
            continue
        report = _coop.parse_low_confidence([(f"chunk-{i}", got)], items)
        entry["status"] = "ok"
        entry["accepted"] = [dict(sug) for sug_list in report.suggestions.values()
                             for sug in sug_list]
        entry["rejected"] = dict(report.rejected)
        entry["unknown"] = list(report.unknown)
        result["n_suggestions"] += report.n_ok
        result["chunks"].append(entry)
    return result


def render_llm_fulltext_report(*, stem: str, cfg, est: dict, result: dict,
                               body_chars: int, max_chars: int = 0,
                               truncated: bool = False,
                               chunk_chars: int = LLM_FULLTEXT_CHUNK_CHARS,
                               apply_summary: dict | None = None) -> str:
    """`<stem>_AI全文复核.md`：参数/分片/预算/逐条建议（**不落正文全文**）。

    批63：`apply_summary` 不为 None = 自动应用已开启（默认）；为 None 时报告与批60
    **逐字节相同**（`--no-llm-fulltext-apply` 的 report-only 兼容承诺）。
    """
    if apply_summary is None:
        _title = "AI 全文复核（直连 API，report-only）"
        _notes = ["> **本报告只列建议，不自动修改 `reference.bib` / 输出 `.tex`**；"
                  "自动应用需用户另行拍板（本批不开放）。",
                  "> 通过校验的建议仍须人工核对；未通过校验/未解析的回复已按 fail-soft 跳过。",
                  ""]
    else:
        _n_app = int(apply_summary.get("n_applied") or 0)
        _n_skip = int(apply_summary.get("n_skipped") or 0)
        _bk = str(apply_summary.get("rollback")
                  or apply_summary.get("rollback_tex") or "")
        _kept = str(apply_summary.get("backup_kept") or "")
        _title = "AI 全文复核（直连 API，自动应用）"
        _notes = ["> **自动应用已开启（批63 默认开）**：只有 BIB/META/FN 会写进产物，"
                  "且全走批57 安全闸（0 条不写、写前 `.ai前`、冲突/漂移拒绝、逐条记录）。",
                  f"> 本次自动应用 **{_n_app}** 条 / 跳过 **{_n_skip}** 条"
                  + (f"；回滚副本 `{os.path.basename(_bk)}`" if _bk else "")
                  + (f"；沿用已存在的 `{os.path.basename(_kept)}`（未覆盖）"
                     if _kept else "") + "。",
                  "> 通过校验的建议仍须人工核对；未通过校验/未解析的回复已按 fail-soft 跳过。",
                  ""]
    L = [f"# {_title}· {stem}", ""] + _notes + [
         f"- 端点：`{cfg.base}`（模型 `{cfg.model}`，通道 `{cfg.protocol}`）",
         f"- 正文：{int(body_chars)} 字 → **{result.get('n_chunks', 0)} 片**"
         f"（每片 ≤{chunk_chars} 字符）",
         f"- 预算估算：{est.get('n_calls', 0)} 次调用 / prompt {est.get('prompt_tokens', 0)}"
         f" + completion {est.get('completion_tokens', 0)} = {est.get('total_tokens', 0)} tokens",
         f"- 结果：**{result.get('n_suggestions', 0)}** 条通过校验建议；"
         f"{len(result.get('failures') or [])} 片 AI 建议不可用", ""]
    if truncated:
        L.append(f"- ⚠ **字符上限**：正文 {int(body_chars)} 字 > "
                 f"`--llm-fulltext-max-chars` {int(max_chars)} 字 → 本次"
                 f"**只覆盖前 {int(max_chars)} 字**（显式截断，不静默；0 = 不限）")
    L += ["## 一、逐条建议（通过校验）", ""]
    n = result.get("n_chunks", 0)
    found = False
    for ch in result.get("chunks") or []:
        for sug in ch.get("accepted") or []:
            found = True
            L += [f"### `{sug.get('id', '')}` — 来源分片 {ch['index']}/{n}", "",
                  f"- verdict：`{sug.get('verdict', '')}`",
                  f"- field：`{sug.get('field') or '—'}`",
                  f"- value：{_coop._code_span(str(sug.get('value') or ''), 300)}",
                  f"- span：{_coop._code_span(str(sug.get('span') or ''), 300)}"]
            if sug.get("locator"):
                L.append(f"- locator：`{sug['locator']}`")
            if sug.get("note"):
                L.append(f"- 说明：{sug['note']}")
            L.append("")
    if not found:
        L += ["（无）", ""]
    L += ["## 二、分片明细", ""]
    for ch in result.get("chunks") or []:
        if ch.get("status") == "ok":
            note = (f"✅ 通过校验 {len(ch.get('accepted') or [])} 条"
                    f"、被拒 {len(ch.get('rejected') or {})} 条"
                    f"、白名单外 {len(ch.get('unknown') or [])} 个")
        else:
            note = f"⚠ AI 建议不可用（{ch.get('error') or '未知原因'}）"
        tk = ch.get("tokens") or {}
        tk_s = (f"，tokens {tk.get('prompt_tokens', 0)}+{tk.get('completion_tokens', 0)}"
                if tk else "")
        L.append(f"- 分片 {ch['index']}/{n}：{ch['chars']} 字符、"
                 f"{ch['n_items']} 条低置信 ID → {note}{tk_s}")
    L += ["", "## 三、被拒绝 / 白名单外（仅记录）", ""]
    any_rej = False
    for ch in result.get("chunks") or []:
        for rid, why in (ch.get("rejected") or {}).items():
            any_rej = True
            L.append(f"- `{rid}`（分片 {ch['index']}）：{why}")
        for uid in ch.get("unknown") or []:
            any_rej = True
            L.append(f"- `{uid}`（分片 {ch['index']}）：白名单外 ID（任务书里没有，已忽略）")
    if not any_rej:
        L += ["（无）"]
    if apply_summary is not None:
        L += ["", "## 四、自动应用记录（批63；仅 BIB/META/FN，批57 安全闸）", ""]
        _applied = list(apply_summary.get("applied") or [])
        if _applied:
            for _a in _applied:
                L.append(f"- ✅ `{_a.get('id', '')}` → "
                         f"{_a.get('target') or _a.get('macro') or '产物'}"
                         f"：`{_a.get('field') or _a.get('macro') or ''}`"
                         f" = {_coop._code_span(str(_a.get('value') or ''), 120)}")
        else:
            L.append("（无：0 条不写盘）")
        _skipped = list(apply_summary.get("skipped") or [])
        if _skipped:
            L.append(f"- 跳过 {len(_skipped)} 条（逐条原因）：")
            for _s in _skipped:
                L.append(f"  - `{_s.get('id', '')}`：{_s.get('reason') or '待人工'}")
        L.append("")
    L += ["", "> 本报告由 `convert.py --llm-fulltext` 生成；"
          "正文内容不写入本报告（只在本次请求中发送给所配置的端点）。", ""]
    return "\n".join(L)


def _add_c_class_union_args(ap: argparse.ArgumentParser) -> None:
    """注册 C 类口径两个开关（抽成函数供测试直接跑 argparse 语义）。

    默认 None 而不是 False：最终开/关由 `C_CLASS_UNION_DEFAULT` 决定，
    这样「默认 ON」只有一处出处（第五十二批 phaseB 切换）。
    """
    ap.add_argument("--c-class-union", dest="c_class_union", action="store_true",
                    default=None,
                    help="C 类新口径（默认开）：纯引用脚注→正文 \\parencite、"
                         "并集参考文献、混合脚注保留+人工核对 warning")
    ap.add_argument("--no-c-class-union", dest="c_class_union", action="store_false",
                    help="回退旧口径（C 类关闭，等价于 v8 行为；对照/应急用）")


def _add_handoff_args(ap: argparse.ArgumentParser) -> None:
    """注册「初排交接单」开关（B-221 C1；抽成函数供测试直接跑 argparse 语义）。

    默认**生成**（`handoff=True`）——Word→初排每次默认带一份 `<stem>_初排交接单.txt`；
    `--no-handoff` 关闭时产物目录与接入前逐字节相同。LaTeX 直读通道接受该参数但 no-op
    （交接单只汇总 Word 解析/初排阶段的数据）。
    """
    ap.add_argument("--no-handoff", dest="handoff", action="store_false",
                    default=True,
                    help="不生成 <stem>_初排交接单.txt（默认生成；关闭后产物目录与旧行为"
                         "逐字节相同）")


def _handoff_enabled(args) -> bool:
    """C1 开关取值（默认开；旧调用方/测试名空间没有该键也按开处理）。"""
    return bool(getattr(args, "handoff", True))

def build_arg_parser():
    """构造 CLI 参数解析器（B-229：从 `main` 首段纯搬运外提；参数/文案/prog 逐字不变）。"""
    ap = argparse.ArgumentParser(
        prog="convert.py",
        description="SLCN 稿件转换器：源文件 → 符合规范的初排稿（源文件必须显式提供）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # 引擎版本 + 运行环境：界面「关于」、Release 说明、同事报错时都要这一行。
    # 版本号只有一处（`slcn/__init__.py`），App 的 Info.plist 也从那里取。
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {ENGINE_VERSION} ｜ {_plat.describe()}")
    ap.add_argument("--source", required=True, help="【必填】源文件（.docx / .doc / .tex）")
    ap.add_argument("--out", default=None, help="【必填】输出根目录，将在其下建立 <作者名>/ 文件夹")
    ap.add_argument("--metadata", action="append", default=None, metavar="文件",
                    help="元数据 / 补充信息源（可给多份，按给出顺序先到先得；只补正文缺失项、"
                         "不覆盖已有内容）。.docx：标签作者信息块/英文元数据表/首部元数据块；"
                         ".tex：\\my* 元数据宏；.json：结构化人工元数据。LaTeX 通道多份会合并成"
                         "一份临时元数据；单份时与旧版逐字节相同。老式 `.doc` 请先另存 `.docx`/`.tex`。")
    ap.add_argument("--bib", default=None, help="参考文献库 .bib（默认取源文件同目录下的）")
    ap.add_argument("--author", default=None, help="第一作者姓名（源里读不到时必须提供）")
    ap.add_argument("--name", default=None, help="直接指定输出名，如 Lei_Li（跳过姓名推导）")
    ap.add_argument("--lang", choices=["zh", "en"], default=None, help="稿件语种（默认自动判断）")
    ap.add_argument("--sty", default=None, help="版面样式文件（默认中文 SLCN-MIX.sty / 英文 SLEN.sty）")
    ap.add_argument("--skeleton", default=None, help="骨架模板 .tex（默认 CN_template / EN_template）")
    # 编辑侧出版信息：作者稿里没有，缺了 PDF 页眉会是「xxxx 年第 x 期，1–10」
    ap.add_argument("--vol", default=None, help="卷号（编辑侧，如 19）")
    ap.add_argument("--issue", default=None, help="期号（编辑侧，如 5）")
    ap.add_argument("--year", default=None, help="出版年（编辑侧，如 2026）")
    ap.add_argument("--pages", default=None,
                    help="起止页（如 137-152，自动算出页数 16；也可只给首页 137）")
    ap.add_argument("--received", default=None, help="收稿日期（如 2025-09-19）")
    ap.add_argument("--editor", default=None, help="责任编辑姓名（自动包成「（责任编辑：XXX）」）")
    ap.add_argument("--grants", default=None, help="基金项目（写入模板被注释的 \\renewcommand 行）")
    # 正文信息手工覆盖（编辑兜底）：解析失败或解析不对时当场覆盖，不必等程序修。
    # 空值 = 不覆盖 —— 与「压根没填」完全等价，7 个全空时行为与旧版一字不差。
    ap.add_argument("--title-cn", default=None, help="【覆盖】中文题名")
    ap.add_argument("--author-cn", default=None,
                    help="【覆盖】第一作者（正文信息；输出名推导仍看 --author / --name）")
    ap.add_argument("--affiliation-cn", default=None, help="【覆盖】作者单位")
    ap.add_argument("--email", default=None, help="【覆盖】通信邮箱")
    ap.add_argument("--abstract-cn", default=None, help="【覆盖】中文摘要")
    ap.add_argument("--title-en", default=None, help="【覆盖】英文题名")
    ap.add_argument("--abstract-en", default=None, help="【覆盖】英文摘要")
    # 批63b（用户 2026-10-20 拍板）：GUI「填写元数据」表单 → 临时 JSON。
    # 多作者/多单位/中英标题摘要关键词一次给全；优先级最高（> --metadata 与上面
    # 7 个覆盖参数）。空值不覆盖；缺字段仍写「【未识别…】」占位。
    ap.add_argument("--metadata-json", default=None,
                    help="【GUI 人工元数据】结构化 JSON（多作者 1–5 / 多单位 / 中英标题"
                         "摘要关键词）。结构见 ENGINE_API「--metadata-json 契约」；"
                         "优先级最高（> --metadata 与 --title-cn 等）；空值不覆盖；"
                         "坏 JSON/不存在/超 5 作者/作者跳号 → exit 2")
    ap.add_argument("--metadata-preview-out", default=None, metavar="临时JSON",
                    help="【只读预览】把「正文解析＋补充文件＋人工元数据」的合并结果写成 JSON"
                         "（字段/生效值/来源/状态/冲突/未识别/缺项），写完立即退出："
                         "不建产物目录、不写产物、不写交接单、不留临时文件。供界面「预览」按钮用。")
    ap.add_argument("--list-editors", action="store_true",
                    help="只列出全库出现过的「责任编辑」姓名（每行「姓名<TAB>篇数」），供界面下拉用")
    ap.add_argument("--compile", action="store_true", help="生成后立即编译出 PDF")
    _add_handoff_args(ap)
    ap.add_argument("--scan", action="store_true",
                    help="只扫描源文件所在目录，列出各文件角色（正文/参考文献/脚注/图）后退出")
    ap.add_argument("--refs", default=None,
                    help="参考文献附件（docx/txt/bib）。全库 28%% 的稿件把参考文献放在单独文件里，"
                         "用这个参数挂上才能替换正文引用。第 134 条起自动发现改保守：有真 .bib"
                         "（--bib / 源内 addbibresource / 同目录 .bib）时不自动合并附件，需要"
                         "合并请显式给本参数；无真 bib 时自动挑（真 BibTeX 优先，文本清单须过"
                         "结构校验），日志写采用/跳过原因")
    ap.add_argument("--verify-refs", action="store_true",
                    help="生成《文献核实任务书》markdown（写明编辑部规范、查证来源优先级与回答格式）。"
                         "若另配了 --llm-base/--llm-key/--llm-model（或环境变量 SLCN_LLM_*），"
                         "再按同一份规范自动跑一遍：结果除追加为「AI 建议（须人工核实）」外，"
                         "还会把通过安全校验的条目**并入 reference.bib**（回滚副本 reference.bib.ai前）"
                         "并出一份《回读校验报告》；要只出任务书、不并入，加 --no-ai-merge")
    # ⚠️ 2026-10-15 用户决定：AI 结果**要进产物**（改掉 README 第 41 条「AI 结果永不进产物」）。
    #    「并入」是改学术事实的动作，所以留一个显式的闸门：加了它，本次就只出任务书
    #    （与旧行为逐字节相同）。界面（Swift）也加了同一个开关：
    #    「保留原 reference.bib（不并入 AI 结果）」→ 只勾了 AI 时才显示。
    ap.add_argument("--no-ai-merge", action="store_true",
                    help="只出《文献核实任务书》，**不**把 AI 结果并入 reference.bib"
                         "（旧行为；界面「保留原 reference.bib（不并入 AI 结果）」= 这个开关）")
    # ── 第五十四批（2026-10-19 用户拍板）：两段式「AI 协同初排」──────────────
    # 外部 chat 通道（豆包/千问/DeepSeek 等，免费、无 API）：初排只出任务书并暂停在
    # 暂存目录；编辑回填回复后第二次运行才继续转换、并入、交付。GUI 下一批接契约。
    ap.add_argument("--coop-taskbook-only", action="store_true",
                    help="AI 协同初排·第一段：初排产物 + 《AI 核实任务书》只写暂存目录"
                         "（默认 `<out>/<stem>_待核/`，可 --coop-stage 覆盖），"
                         "**不写最终交付目录、不打印交付成功、不自动调用 API**。"
                         "隐含 --verify-refs；任务书末尾增加「初排低置信项」机器可读一节。")
    ap.add_argument("--coop-fulltext", choices=["context", "full"], default=None,
                    metavar="{context,full}",
                    help="（第五十九批；第六十三批起**不设字符上限**）第一段任务书在附二之后"
                         "追加「附三、正文全文/引用上下文」，供外部 chat 核对附一各项："
                         "context=引用/脚注锚点前后各 300 字窗口合并（约 4 万字符起只提示）；"
                         "full=正文全文（约 8 万字符起只提示）。**不截断、整篇发送**，"
                         "只保留体积估算与超长提示（外部 chat 有长度限制时请自行分段）。"
                         "仅配合 --coop-taskbook-only；不新增改正文类建议（回复格式/校验/"
                         "`--coop-apply` 与从前一致）。")
    ap.add_argument("--coop-stage", default=None, metavar="DIR",
                    help="协同初排的暂存目录（默认 `<out>/<stem>_待核/`）；"
                         "状态文件 `_AI协同状态.json` 写在这里，不随交付进入最终目录。")
    ap.add_argument("--coop-replies", action="append", default=None, metavar="PATH",
                    help="AI 协同初排·第二段：读入编辑回填的外部 chat 回复（文件或目录，"
                         "目录按文件名排序）。**可重复**：--coop-replies A --coop-replies B "
                         "一次传多个文件/目录（chat 客户端把 JSON 与 BibTeX 拆成两个文件时"
                         "两块都要传进来；同一真实路径自动去重）。校验指纹后解析/并入"
                         "文献字段、写低置信建议，编译并原子交付到最终目录；"
                         "回复全部无效/指纹不符时退出 2、产物不动。")
    ap.add_argument("--coop-apply", action="store_true",
                    help="配合 --coop-replies：把**通过校验**的低置信建议也应用到产物"
                         "（先备份 `reference.bib.ai前`、逐条记录；只应用可安全落盘的"
                         "文献字段建议，脚注/元数据建议仍列待人工）。默认只写 `…_AI建议.md`/jsonl。")
    # 预算闸：只约束**直接 API 通道**（--llm-* 自动预跑）。外部 chat 通道无 API 费用。
    ap.add_argument("--ai-budget-tokens", type=int, default=30000, metavar="N",
                    help="单篇直接 API 通道的 token 预算（默认 30000；0 = 不限）。"
                         "超过预计用量时先打印账、退出 2，不发起任何请求。")
    ap.add_argument("--ai-batch-budget-tokens", type=int, default=1000000, metavar="N",
                    help="单批直接 API 通道的 token 预算（默认 1000000；0 = 不限）。"
                         "超过预计用量时先打印账、退出 2。外部 chat 通道不产生费用。")
    # ── 第 128 条（2026-10-18）：核实**范围**与成本闸门──────────────────────────
    # 期 87 子案例 1 实测：源 bib 377 条、正文只引用 31 个键，但任务书 + 检索 + AI 把
    # 376 条全核了（检索 418 次 ≈ ¥1.845、AI 376 次调用 / 2,538,740 prompt tokens）。
    # 默认口径改为「只核正文引用到的键」；要全量必须**显式**说（--verify-all-refs），
    # 且默认有上限（--verify-max，超了先退出 2 看账）。范围判定在 slcn/refmark.py，
    # 这里只负责读产物 tex、打印与闸门（判定只有一份实现）。
    ap.add_argument("--verify-all-refs", action="store_true",
                    help="核实 bib 里的**全部**条目（显式全量；默认只核实正文引用到的键）。"
                         "越过 --verify-max 闸门；正文 `\\nocite{*}` 本身也要求全量。")
    ap.add_argument("--verify-max", type=int, default=None,
                    help=f"本次核实条数上限（默认 {_refmark.DEFAULT_VERIFY_MAX}）：超过且未加 "
                         "--verify-all-refs → 退出 2 并给出指引；0 = 不限。")
    ap.add_argument("--verify-plan", action="store_true",
                    help="只打印核实范围与预计检索/AI 次数后退出 0（不生成任务书、不检索、"
                         "不调用 AI；转换产物照常写出）。可单独使用，不必再加 --verify-refs。")
    # ── 第 129 条（2026-10-18）：编辑部引用库（bib_db）─────────────────────────
    # 命中已核实条目 → 该键不调检索 API / 不调 AI（省钱 + 体例统一 + 可审计）。
    # **默认口径**：执行核实（--verify-refs / --verify-plan）且默认库存在时**自动启用**
    # （默认库 = `slcn.bibdb.default_db_path()`，即笔记仓 bib_db/bibliography.jsonl）；
    # 笔记仓/库不存在时优雅跳过（公开仓/CI 行为零变化）。--no-bibdb 显式关。
    ap.add_argument("--bibdb", action="store_true",
                    help="启用编辑部引用库查库（默认：执行核实时若默认库存在就自动启用；"
                         "命中已核实条目的键不调检索/AI，仍逐条写进任务书「五、库内已核实」）。"
                         "库缺失/读不了时只告警、不中断出稿。")
    ap.add_argument("--bibdb-path", default="", metavar="PATH",
                    help="引用库文件路径（隐含启用；默认 笔记仓/bib_db/bibliography.jsonl）。"
                         "指错/读不了 → 告警并按原流程核实（不中断）。")
    # 第五十二批 phaseB（用户拍板，v9b 全量 PASS 后）：C 类新口径**默认 ON**。
    # `--no-c-class-union` 回退旧行为（v8 基线对照/应急）；`--c-class-union` 显式开。
    _add_c_class_union_args(ap)
    ap.add_argument("--number-cite-map", action="store_true",
                    help="NO_CITES-B（默认关）：正文裸编号 [N] 映射为第 N 条 \\parencite")
    ap.add_argument("--end-list-priority", action="store_true",
                    help="C 类可选开关（默认关）：文末清单优先，脚注文献不再逐条提升")
    ap.add_argument("--no-bibdb", action="store_true",
                    help="关闭引用库查库（本次一律不查库；输出与接入前逐字节相同）。")
    ap.add_argument("--no-bibdb-verify", action="store_true",
                    help="关闭「初排默认引用库比对/回填」（默认开：安全命中即回填缺失字段"
                         "并打一行汇总；无引用库时自动静默跳过；"
                         "不影响 --verify-refs/--verify-plan 的核实行为）。")
    ap.add_argument("--no-bibdb-apply", action="store_true",
                    help="关闭「库值全量应用」（默认开：安全命中的已核实条目按白名单"
                         "覆盖/补齐库内非空值，并写 before/after 核对清单；"
                         "候选/未命中不动；无引用库时静默跳过）。"
                         "关闭后回到只读引用库比对口径"
                         "（只打一行汇总、不改产物，逐字节等于旧行为）。")
    # P1（2026-10-17，第 71 条起）：自动检索证据 = DeepSeek Search API（独立原始 JSON API），
    # **不改模型提示词、不改转换产物**，只把「每条文献在公开网页上的候选来源」写进任务书。
    # 默认关；不开启时产物与从前的调用**逐字节相同**（任务书里一个字段都不多）。
    ap.add_argument("--search-refs", action="store_true",
                    help="（附加，默认关）用 DeepSeek Search API 为**本次核实范围内**的参考文献"
                         "检索证据（默认只含正文引用到的条目，见 --verify-all-refs/--verify-max），"
                         "写进《文献核实任务书》（来源 URL/权威度/匹配分/用过的 query）。"
                         "地址与密钥复用 --llm-base/--llm-key（或 SLCN_LLM_*；也可用 "
                         "SLCN_SEARCH_BASE/SLCN_SEARCH_KEY 单独指定）；"
                         "也可用环境变量 SLCN_SEARCH_REFS=1。⚠️ 会按次计费（¥5/1000 次成功）。")
    ap.add_argument("--llm-base", default=None, help="OpenAI 兼容 API 的 base URL（也可用环境变量 SLCN_LLM_BASE）")
    ap.add_argument("--llm-key", default=None, help="API 密钥（环境变量 SLCN_LLM_KEY）")
    ap.add_argument("--llm-model", default=None, help="模型名（环境变量 SLCN_LLM_MODEL）")
    # 两条通道 + 联网检索（2026-10-15）：**默认值与不传这个参数时，请求体与从前逐字节
    # 相同**（chat、无 tools）——老调用方与既有套件都钉着这条。缺省（None）时回落到环境
    # 变量 SLCN_LLM_PROTOCOL / SLCN_LLM_WEB_SEARCH，所以命令行与环境变量两条入口等价。
    ap.add_argument("--llm-protocol", choices=["chat", "responses"], default=None,
                    help="通道：chat（默认，{base}/chat/completions）| "
                         "responses（{base}/responses，标准 Responses 协议）；"
                         "也可用环境变量 SLCN_LLM_PROTOCOL。不传=chat。")
    ap.add_argument("--llm-web-search", action="store_true",
                    help="本次**启用服务端联网检索**（只在 responses 通道生效：加 "
                         "tools:[{type:<--llm-search-tool>}]）；也可用环境变量 SLCN_LLM_WEB_SEARCH=1。"
                         "⚠️ 端点是否真的支持要跑检索探针（python -m slcn.llm --probe）——"
                         "不支持时模型只能凭记忆，本工具会如实报告，绝不假装已联网。")
    # 第 70 条的两个「未来兼容」旋钮：把硬编码的工具名与路径换成配置。
    # **默认 = 今天的取值**，不传这两个参数时命令行与 URL 与从前逐字节相同。
    # 为什么做成文本而不是布尔开关：将来服务商怎么命名、路径怎么改，现在无从预知
    # （各家实现并不一致：DeepSeek 的 flash 仍忽略内置检索工具、v4-pro 已支持；
    # 名字也未必都叫 web_search）。
    ap.add_argument("--llm-search-tool", default=None,
                    help="responses 通道 tools[].type 的工具名（默认 web_search；也可用环境变量 "
                         "SLCN_LLM_SEARCH_TOOL）。将来服务商换了名字（如 web_search_preview）"
                         "只改这一项，不必改代码。")
    ap.add_argument("--llm-fulltext", action="store_true",
                    help="（第六十批；第六十三批起**默认自动应用**）全文 AI 核查的"
                         "**直连 API**通道：把稿件正文分片发给已配置的 LLM，逐片核对"
                         "低置信项，生成 `<输出名>_AI全文复核.md`。默认把通过校验的"
                         "BIB/META/FN 建议自动应用到产物（走与 --coop-apply 相同的安全闸："
                         "0 条不写、写前 `.ai前`、冲突拒绝、逐条记录）；要批60 的"
                         "report-only 口径加 --no-llm-fulltext-apply。"
                         "受 --ai-budget-tokens 预算闸约束（超限退出 2、零请求）；"
                         "超时/HTTP 错/截断/JSON 不过按 fail-soft 继续出稿。"
                         "只配合 --verify-refs；不能与 --verify-plan/协同两段同用。")
    ap.add_argument("--llm-fulltext-apply", dest="llm_fulltext_apply",
                    action="store_true", default=None,
                    help="（第六十三批）配合 --llm-fulltext：把通过校验的低置信建议"
                         "**自动应用**到产物（默认开；只有 --llm-fulltext 时生效）。"
                         "仅 BIB/META/FN，全走批57 安全闸（0 条不写、写前 `.ai前`、"
                         "冲突/漂移拒绝、逐条记录）；`.ai前` 已存在时不覆盖。")
    ap.add_argument("--no-llm-fulltext-apply", dest="llm_fulltext_apply",
                    action="store_false",
                    help="配合 --llm-fulltext：只出报告、不改产物"
                         "（= 第六十批的 report-only 口径）。")
    ap.add_argument("--llm-fulltext-max-chars", type=int, default=40000, metavar="N",
                    help="（第六十三批）--llm-fulltext 发送正文的字符上限"
                         "（默认 40000；0 = 不限）。超限时**显式截断**前 N 字并在"
                         "日志/报告里醒目告警（不静默、退出码不变）；"
                         "分片大小取 min(30000, N)。")
    ap.add_argument("--llm-responses-path", default=None,
                    help="responses 的路径，拼在 base 之后（默认 /responses；也可用环境变量 "
                         "SLCN_LLM_RESPONSES_PATH）。缺前导 / 会自动补上。")
    ap.add_argument("--quote-threshold", type=int, default=105,
                    help="引文超过多少字才放进 quotation 环境（默认 105 ≈ 三行）。"
                         "调低会把短引文也转换。")
    ap.add_argument("--clean", action="store_true",
                    help="清理编译中间文件（.aux/.bbl/.bcf/.blg/.out/.run.xml/.synctex.gz/"
                         ".DS_Store），规则与 EditToolBox「删除临时文件」完全一致；"
                         "故意不删 .log、.tex、.pdf、.sty、.bib")
    ap.add_argument("--source-pdf", action="store_true",
                    help="用 LibreOffice 把 Word 原稿转成 PDF 一并输出，"
                         "供「矢量形状」类对象人工截图（全库 235 个，需截图或重画）")
    ap.add_argument("--no-original", action="store_true",
                    help="不把源稿归档到 original/（默认会归档）")
    ap.add_argument("--copy-dir", action="store_true",
                    help="连源文件所在整个目录一起归档（LaTeX 投稿常是一个文件夹）；多文件 LaTeX 稿自动启用")
    ap.add_argument("--dry-run", action="store_true", help="只显示将要做什么，不写任何文件")
    ap.add_argument("--force", action="store_true", help="目标文件夹已存在时覆盖（默认拒绝）")
    ap.add_argument("--list-templates", action="store_true", help="列出可用模板后退出")
    return ap

def run_coop_taskbook(args, res, *, source, metadata, out_root, out_dir, final_dir,
                      stem, lang, sel, coop_taskbook_path, coop_bib_keys, coop_bibdb,
                      structured_metadata=None, structured_origin=""):
    """⑬ 协同初排第一段收尾（B-229：从 `main` 纯搬运外提；语句逐字/顺序不变）。"""
    bib_text2 = ""
    if res.bib_path and os.path.isfile(res.bib_path):
        bib_text2 = open(res.bib_path, encoding="utf-8", errors="replace").read()
    # 第五十七批：META-LABEL-* 需要「人话标签 → 输出 tex 的 \\my* 宏名」映射，
    # 才能在 --coop-apply 时真正写进产物（build 的 undetected_fields 提供）；
    # META 项同时记下初排时 tex 里的字段值，ph2 应用前比对「产物有没有被改过」。
    _tex_cur: dict[str, str] = {}
    try:
        _tex_cur = _coop.read_tex_field_values(getattr(res, "tex_path", "") or "")
    except OSError:
        _tex_cur = {}
    coop_low_items = _coop.collect_low_confidence(
        warnings=res.warnings,
        missing_fields=getattr(res, "missing_fields", ()) or (),
        raw_ref_lines=getattr(res, "ref_raw_lines", ()) or (),
        bib_text=bib_text2,
        label_targets=dict(getattr(res, "undetected_fields", ()) or ()),
        current_fields=_tex_cur,
    )
    section = _coop.render_low_confidence_section(coop_low_items)
    if not coop_taskbook_path:
        # 极端情形：bib 与文献区原文都没有（任务书主流程不生成）——仍给一份
        # 只含低置信项的任务书，保证「第一段一定有交付物」。
        coop_taskbook_path = os.path.join(out_dir, f"{stem}_文献核实.md")
        _coop.atomic_write_text(
            coop_taskbook_path,
            f"# 参考文献核实任务书 · {stem}\n\n"
            "> 本次没有可解析的参考文献条目；下面只列初排低置信项。\n"
            "> 请按 ID 逐条回复 JSON（格式见下）。\n")
    with open(coop_taskbook_path, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n" + section + "\n")
        # 第五十六批：附二把「回填=完整 BibTeX + 附一 JSON 两部分」写死，
        # 防止模型/编辑只交一半（真实首测：只导入 JSON → 文献回填 0/9）。
        f.write("\n" + _coop.render_backfill_section() + "\n")
    # 第五十九批：附三、正文全文/引用上下文（--coop-fulltext；默认关）。
    # **只进任务书**：回复 JSON 契约/校验/--coop-apply 完全不变，不新增改正文类建议。
    coop_fulltext_info: dict | None = None
    if args.coop_fulltext:
        try:
            _ft = _coop.extract_fulltext(
                getattr(res, "body_text", "") or "", mode=args.coop_fulltext)
        except (ValueError, TypeError) as e:  # noqa: BLE001  附加通道，不连累出稿
            print(f"   ⚠ 附三跳过：{type(e).__name__}: {e}")
            _ft = {}
        if _ft.get("chars"):
            with open(coop_taskbook_path, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n" + _coop.render_fulltext_section(_ft) + "\n")
            coop_fulltext_info = {k: _ft[k] for k in
                                  ("mode", "chars", "truncated", "tokens_est")}
            _ft_note = ("超长（未截断，整篇发送）" if _ft.get("over_limit")
                        else (f"{_ft.get('windows', 0)} 段窗口"
                              if args.coop_fulltext == "context" else "全文"))
            print(f"   任务书附三：正文{_ft_note}，{_ft['chars']} 字"
                  f"（约 {_ft['tokens_est']} tokens）")
        else:
            print("   任务书附三跳过：本次没有可用正文（源稿解析失败/LaTeX 无正文）")
        # 批74（B-133）：附四「初排分析」——**只在附三成功写出之后**追加（位置=附三之后）；
        # 无可用正文时附三/附四一起跳过。首版严格 report-only：只写指令，不写任何产物。
        if coop_fulltext_info is not None:
            # 批75（B-139，T1 报告 §2）：附三之后、附四之前补「脚注清单（fid+摘要）」
            # 与「占位符清单（类型+位置）」两块（数据现成：初排 warning 的
            # `#fid「片段」` + BuildResult.undetected_fields）；**只在有数据时出现**，
            # 无 `--coop-fulltext` 的路径完全不走这里 → 任务书逐字节不变。
            _fn_rows, _ph_rows = _coop.collect_analysis_inputs(
                warnings=res.warnings,
                low_items=coop_low_items,
                placeholders=getattr(res, "undetected_fields", ()) or ())
            _inputs_sec = _coop.render_analysis_inputs_section(
                footnotes=_fn_rows, placeholders=_ph_rows)
            if _inputs_sec:
                with open(coop_taskbook_path, "a", encoding="utf-8",
                          newline="\n") as f:
                    f.write("\n" + _inputs_sec + "\n")
                print(f"   任务书附四输入清单：脚注 {len(_fn_rows)} 条 / "
                      f"占位符 {len(_ph_rows)} 个（+{len(_inputs_sec)} 字符）")
            with open(coop_taskbook_path, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n" + _coop.render_analysis_section() + "\n")
            print("   任务书附四：初排分析指令（report-only；只报告、不写产物）")
    if not coop_bib_keys and bib_text2:
        coop_bib_keys = [b.key for b in _coop.parse_bib_blocks(bib_text2) if b.closed]
    coop_params = _coop.build_params(
        source=source, metadata=metadata or "", out_root=out_root, stem=stem,
        lang=lang, sty=sel.sty, skeleton=sel.skeleton,
        verify_refs=bool(args.verify_refs), no_ai_merge=bool(args.no_ai_merge),
        end_list_priority=bool(getattr(args, "end_list_priority", False)),
        number_cite_map=bool(getattr(args, "number_cite_map", False)),
        c_class_union=resolve_c_class_union(args), stage_dir=out_dir,
        structured_sha256=(_coop.structured_metadata_sha256(structured_metadata)
                           if structured_metadata else ""),
    )
    # B-287：人工元数据跨进程保留——值+sha256 进状态；ph2 不重新排版，只用它
    # 校验「第二段有没有偷偷改元数据」。没有人工元数据时状态与旧版逐字节相同。
    _sm = _coop.make_structured_metadata(structured_metadata,
                                         origin=structured_origin or "cli-json")
    coop_state = _coop.build_state(
        params=coop_params, taskbook_path=coop_taskbook_path, stage_dir=out_dir,
        final_dir=final_dir, bib_keys=coop_bib_keys, low_items=coop_low_items,
        budget_article=args.ai_budget_tokens,
        budget_batch=args.ai_batch_budget_tokens,
        engine_version=ENGINE_VERSION,
        structured_metadata=_sm,
    )
    coop_state["reference_bib"] = os.path.abspath(res.bib_path) if res.bib_path else ""
    coop_state["tex"] = os.path.abspath(res.tex_path) if getattr(res, "tex_path", "") else ""
    # 批79（B-131）：交付记录标记（**只读汇总的开关 + 核实通道的库分类计数**）。
    # schema 仍 1、不入参数指纹；旧状态无该键 → ph2 不渲染交付记录节（逐字节不变）。
    # 计数本身在 ph2 用**交付时的产物**现算（引用/打印以最终 .tex/.bib 为准）。
    coop_state["delivery"] = {
        "schema": _coop.DELIVERY_SCHEMA,
        "bibdb_checked": bool(coop_bibdb.get("checked")),
        "bibdb_safe": coop_bibdb.get("safe"),
        "bibdb_missed": coop_bibdb.get("missed"),
    }
    # 第五十九批：附三元信息（模式/字数/截断/估算）——**不落正文**；
    # 只在启用 --coop-fulltext 时写，旧状态无此键按「未启用」处理；schema 仍 1。
    if coop_fulltext_info is not None:
        coop_state["fulltext"] = coop_fulltext_info
        # 批74（B-133）：附四启用标记（**schema 不升版、不入参数指纹**）。
        # 旧 state 无该键 → ph2 按「未启用」处理（行为与旧版逐字节相同）；
        # `auto_whitelist_future` 只是给「将来自动应用默认语义」留痕，本卡不实现。
        coop_state["analysis"] = {
            "schema": _coop.ANALYSIS_SCHEMA,
            "enabled": True,
            "default_policy": "report-only",
            "auto_whitelist_future": list(_coop.ANALYSIS_FUTURE_WHITELIST),
        }
    state_file = _coop.save_state(out_dir, coop_state)
    print(f"\n⑬ AI 协同初排·第一段（已暂停，等待回填）")
    print(f"   暂存目录：{out_dir}")
    print(f"   核实任务书：{os.path.basename(coop_taskbook_path)}"
          f"（含「初排低置信项」{len(coop_low_items)} 项）")
    print(f"   状态文件：{os.path.basename(state_file)}")
    print("   最终交付目录本次**未写**；把任务书交给任意 chat 模型，"
          "回填后运行 --coop-replies <文件或目录> 继续。")

# 兼容垫片：`convert_metadata.is_own_product()` 沿用旧口径引用本模块的
# `LLM_FULLTEXT_REPORT_SUFFIX`（顶层反向 import 会成环，故加载时补属性，函数体不动）。
from slcn import convert_metadata as _convert_metadata
_convert_metadata.LLM_FULLTEXT_REPORT_SUFFIX = LLM_FULLTEXT_REPORT_SUFFIX
