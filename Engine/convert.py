#!/usr/bin/env python3
"""SLCN 稿件转换器 · 正式命令行入口。

与 run_poc.py / corpus.py 的区别
--------------------------------
那两者是**测试台**，会自动扫描 original/ 去找源文件——那对回归测试是必要的。
本脚本是**给编辑用的正式工具**，按用户要求：

  1. **源文件必须由用户显式提供**（--source），不做任何自动发现，也不默认填充。
     找不到、读不出作者时**直接报错**，把选择权交回用户，绝不猜。
  2. **输出目录按规范命名**：作者「李雷」→ 生成 `Lei_Li/Lei_Li.tex`
     （多作者只取第一个）。见 slcn/naming.py。

     同时建立 `Lei_Li/original/` 并把源稿归档进去，命名与往期各期一致
     （实测全库 92 篇文章都用 `original/`）。可用 --no-original 关闭。

     本工具**只负责初排环节**，因此目录名**不带序号前缀**。按编辑部流程，
     序号（如 `10 Lei_Li` 的 `10`）是在三校 / 蓝样校对阶段、文章目录顺序
     确定之后才由人工加上去的——那一步不归本工具管。

用法
----
    # Word 文稿
    python3 convert.py \\
        --source "…/10 Lei_Li/original/李雷 当代逻辑论题鸟瞰.docx" \\
        --out    "…/2026年第5期(90)" \\
        --compile

    # LaTeX 源文件（多源合并：正文 + 上一轮模板稿的元数据）
    python3 convert.py \\
        --source   "…/03示例悖论（定稿）/main.tex" \\
        --metadata "…/03示例悖论（定稿）/example puzzle.tex" \\
        --out      "…/2026年第5期(90)" \\
        --compile

    # 解析不对时当场手工覆盖正文信息（空值 = 不覆盖）
    python3 convert.py --source X.docx --out Y \\
        --title-cn "中文题名" --author-cn "张三" --affiliation-cn "中山大学" \\
        --email "a@b.cn" --abstract-cn "…" --title-en "…" --abstract-en "…"

    # 先看会做什么，不落盘
    python3 convert.py --source X.docx --out Y --dry-run
"""

from __future__ import annotations

import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import clean as _clean, platform as _plat, refmark as _refmark  # noqa: E402
from slcn import naming as N, paths as _paths, sources as _sources, templates as T, bibitem_convert as _bibitem  # noqa: E402  B-233 转换开关
from slcn import coop as _coop  # noqa: E402  B-287：人工元数据来源/摘要（状态记录）

# ── B-229：旧 convert 的 81 个顶层符号全部从 owning 模块显式 re-export（`is` 同一对象）──
from slcn.convert_metadata import (  # noqa: E402
    WORD_EXT, TEX_EXT, _OWN_FIXED, _OWN_STEM_SUFFIXES, _IMG_SUFFIXES, _MID_SUFFIXES, is_own_product, _BACKUP_RE, require_source, die, _has_cjk, detect_lang, author_from_source, find_en_metadata, mark_en_abstract_roles, _find_db_root, list_editors,
    MetadataJSONError, _METADATA_JSON_SCALARS, _METADATA_JSON_AUTHOR_KEYS, _MAX_JSON_AUTHORS, _json_clean, _json_affil_list, _normalize_json_author, load_metadata_json, supports_structured, structured_kwargs, _ensure_vendor, resolve_c_class_union,
    _OVERRIDE_FIELDS,
)
from slcn.convert_compile import (  # noqa: E402
    pdf_page_count, _STALE_COMPILE_EXTS, clean_stale_compile_artifacts, compile_pdf,
)
from slcn.convert_stage import (  # noqa: E402
    _FINISH_WHY, channel_provenance, stage_original, collect_overrides, supports_overrides, overrides_kwargs, apply_ai_merge, _coop_find_bib, _coop_find_tex, _coop_locate_state, run_coop_replies, LLM_FULLTEXT_CHUNK_CHARS, LLM_FULLTEXT_REPORT_SUFFIX,
    LLM_FULLTEXT_RUBRIC, split_fulltext_chunks, _fulltext_chunk_items, _llm_fulltext_system, _llm_fulltext_user, _llm_fulltext_review, render_llm_fulltext_report, _add_handoff_args, _handoff_enabled, _add_c_class_union_args, build_arg_parser,
    run_coop_taskbook, build_docx, build_latex,
)
from slcn.convert_bib import (  # noqa: E402
    _NORM_RE, _ref_fingerprint, _bib_fingerprints, _bib_candidates, _pick_bib, bib_search_dirs, find_bib_ex, find_bib, bib_origin, _DECL_BIBADD_RE, _DECL_BIBLIO_RE, declared_bib_path, real_bib_path, bib_entry_structurally_safe, bib_attachment_entries,
    merge_bib_attachment, resolve_bibdb, classify_bibdb, bibdb_log_line, _bibdb_scan_stem, bibdb_readonly_scan, _bibdb_apply_backfill, bibdb_taskbook_hits, resolve_bibdb_apply_store, run_refs_attach, run_verify_refs,
)


def main() -> int:
    # 第 86 条：Windows 的 stdout 默认是控制台代码页（cp936），中文日志会被管道接走后
    # 乱码甚至 UnicodeEncodeError 崩在半路。这里**第一步**就切到 UTF-8（mac 上是空操作）。
    _plat.utf8_stdio()
    ap = _bibitem.add_cli_args(build_arg_parser())  # B-233：--no-bibitem-convert
    args = _bibitem.apply_cli_args(ap.parse_args())
    if not args.scan and not args.out and not args.list_editors:
        ap.error("除 --scan / --list-editors 外，必须指定 --out")
    if args.list_editors:
        for name, n in list_editors(args.source):
            print(f"{name}\t{n}")
        return 0

    # ── 第五十四批：AI 协同初排的参数组合（先校验，别让半截运行改产物）────────
    # 用户口径（2026-10-19）：默认不改产物；`--coop-apply` 是显式的「一键修改产物」。
    if args.coop_taskbook_only and args.coop_replies:
        ap.error("--coop-taskbook-only 与 --coop-replies 是两个阶段，不能同时给")
    if args.coop_apply and not args.coop_replies:
        ap.error("--coop-apply 只配合 --coop-replies（先回填回复，才谈得上应用建议）")
    if args.coop_fulltext and not args.coop_taskbook_only:
        ap.error("--coop-fulltext 只配合 --coop-taskbook-only（第一段任务书附正文；"
                 "回填解析/应用阶段不使用全文）")
    if args.llm_fulltext:
        # 第六十批：全文 AI 核查（直连 API）只在 ⑪ 的 --verify-refs 路径生效。
        if args.coop_taskbook_only or args.coop_replies:
            ap.error("--llm-fulltext 只走直连 API 的 ⑪ 核实路径，不能与协同两段式"
                     "（--coop-taskbook-only/--coop-replies）同用")
        if args.verify_plan:
            ap.error("--llm-fulltext 不能与 --verify-plan 同用（只算账模式不发起请求）")
        if not args.verify_refs:
            ap.error("--llm-fulltext 只配合 --verify-refs（直连 AI 的 ⑪ 核实路径；"
                     "单独给本开关不会触发任何核查）")
        from slcn import llm as _llm_chk

        _cfg_chk = _llm_chk.config_from_env(
            args.llm_base, args.llm_key, args.llm_model,
            protocol=args.llm_protocol,
            web_search=(True if args.llm_web_search else None),
            search_tool=args.llm_search_tool,
            responses_path=args.llm_responses_path,
        )
        if not _llm_chk.available(_cfg_chk):
            die("--llm-fulltext 需要可用的直连 LLM 配置，本次**未发起任何请求**。\n"
                "  请先在「设置」页或命令行配置 --llm-base/--llm-key/--llm-model"
                "（或用环境变量 SLCN_LLM_BASE/SLCN_LLM_KEY/SLCN_LLM_MODEL）；"
                "也可改用通道①：--coop-taskbook-only --coop-fulltext {context,full}")
    if args.llm_fulltext_max_chars is not None and args.llm_fulltext_max_chars < 0:
        ap.error("--llm-fulltext-max-chars 必须 ≥ 0（0 = 不限）")
    if args.llm_fulltext_apply is not None and not args.llm_fulltext:
        print("⚠ --llm-fulltext-apply/--no-llm-fulltext-apply 只配合 --llm-fulltext；"
              "本次忽略。", file=sys.stderr, flush=True)
    if args.ai_budget_tokens is not None and args.ai_budget_tokens < 0:
        ap.error("--ai-budget-tokens 必须 ≥ 0（0 = 不限）")
    if args.ai_batch_budget_tokens is not None and args.ai_batch_budget_tokens < 0:
        ap.error("--ai-batch-budget-tokens 必须 ≥ 0（0 = 不限）")
    if args.coop_stage and not (args.coop_taskbook_only or args.coop_replies):
        print("⚠ --coop-stage 只配合 --coop-taskbook-only / --coop-replies；本次忽略。",
              file=sys.stderr, flush=True)
    if args.coop_taskbook_only and not args.verify_refs:
        # 第一段的全部意义就是「出任务书然后暂停」；不勾 --verify-refs 也把它当作勾了。
        args.verify_refs = True
        print("  （--coop-taskbook-only 隐含 --verify-refs：本次只出核实任务书，不自动调用 API）")
    if args.coop_taskbook_only:
        # 外部 chat 通道：**绝不**自动预跑 API（用户明确要的「暂停」语义）。
        args.no_ai_merge = True

    # ── 第五十四批：第二段先定位暂存状态，再把 ph1 的关键参数继承下来 ──────────
    # 为什么必须在这里做：主流程会重新推导输出名/语种/模板，ph2 的命令行通常只给
    # `--source/--out/--coop-replies`。若拿重新推导的值去对指纹，正常的续跑会被误拒；
    # 反过来，用户**显式**改 `--name/--metadata` 时仍要能检出差异 → 只填空缺。
    if args.coop_replies:
        _stage0, _state0, _matches = _coop_locate_state(args)
        if _state0 is None:
            die("找不到可续跑的协同初排暂存目录（没有 `_AI协同状态.json`）。\n"
                f"  已查：{'、'.join(_matches) if _matches else '（无）'}\n"
                "  请先运行第一段：--verify-refs --no-ai-merge --coop-taskbook-only；"
                "或用 --coop-stage 指定暂存目录。")
        args.coop_stage = _stage0
        _sp = _state0.get("params") or {}
        if not args.name and _sp.get("stem"):
            args.name = _sp["stem"]
        if not args.metadata and _sp.get("metadata"):
            args.metadata = [_sp["metadata"]]
        if args.lang is None and _sp.get("lang"):
            args.lang = _sp["lang"]
        if args.sty is None and _sp.get("sty"):
            args.sty = _sp["sty"]
        if args.skeleton is None and _sp.get("skeleton"):
            args.skeleton = _sp["skeleton"]

    if args.search_refs and not (args.verify_refs or args.verify_plan):
        # 显式提示、继续跑：`--search-refs` 只在生成任务书时有落点（逐条清单）。
        print("⚠ --search-refs 只配合 --verify-refs（没有任务书就没有逐条清单可挂证据）；"
              "本次忽略检索，转换照常进行。", file=sys.stderr, flush=True)

    # 第 128 条：成本闸门的参数校验/提示。默认值（None）= 120，显式传 -1 是用法错误。
    if args.verify_max is not None and args.verify_max < 0:
        ap.error("--verify-max 必须 ≥ 0（0 = 不限）")
    if (args.verify_all_refs or args.verify_max is not None) \
            and not (args.verify_refs or args.verify_plan):
        print("⚠ --verify-all-refs / --verify-max 只配合 --verify-refs（或 --verify-plan）；"
              "本次忽略。", file=sys.stderr, flush=True)
    verify_max = (_refmark.DEFAULT_VERIFY_MAX if args.verify_max is None
                  else args.verify_max)

    # 第 129 条：引用库开关。与核实范围闸门同一口径——只配合核实入口；不核实时报一句就忽略。
    if args.no_bibdb and (args.bibdb or args.bibdb_path):
        ap.error("--no-bibdb 与 --bibdb/--bibdb-path 不能同时给（要关就别指库）")
    if (args.bibdb or args.bibdb_path or args.no_bibdb) \
            and not (args.verify_refs or args.verify_plan):
        print("⚠ --bibdb / --bibdb-path / --no-bibdb 只配合 --verify-refs（或 --verify-plan）；"
              "本次忽略。", file=sys.stderr, flush=True)

    # --scan：只列出源文件角色就退出（不需要 --out）
    # 非 --scan 时也扫一遍：自动挑出参考文献附件（全库 28% 的稿件把它单独放一个文件）
    from slcn import multisource

    _src_dir = (
        args.source if os.path.isdir(args.source)
        else os.path.dirname(os.path.abspath(args.source))
    )
    refs_auto = False  # 第 134 条：附件是自动挑的还是用户 --refs 显式给的
    _inventory = multisource.scan(_src_dir)
    # ── 第 76 条：把「英文摘要 docx」从【未识别】改判为明确角色 en_abstract ──
    # ⚠️ `--scan` 走的是 `multisource.scan()`（不是 `sources.identify()`），而
    #    `multisource.py` 不在本条的改动范围（只读件），所以角色在这里补。
    #    判据与 Word 通道的第二源发现**共用** `sources.en_metadata_sheet()`。
    mark_en_abstract_roles(_inventory)
    if args.scan:
        print(multisource.format_inventory(_inventory, "源稿清单"))
        return 0
    if _inventory:
        _mains = [x for x in _inventory if x.role == "main"]
        if len(_mains) > 1:
            print("  ⚠ 发现多个「完整稿件」版本，建议用 --source 明确指定：")
            for x in _mains:
                print(f"      · {x.name}（{x.chars} 字符）")
        if not args.refs:
            # 第 134 条：附件选择保守化——
            #   · 已有「真 .bib」（--bib / 源内 \addbibresource / 同目录 .bib）时**不自动**
            #     合并附件（需要合并的人会显式 --refs）；71/04 就属于这种：正文同目录本来
            #     就有 reference.bib，再去并 `预处理/用于上传text2bib.txt` 是纯帮倒忙。
            #   · 没有真 bib 时才挑文本/BibTeX 附件，且候选要过结构校验；
            #     每个被跳过的候选都写明原因（日志可解释）。
            _src_is_tex = os.path.splitext(args.source)[1].lower() in TEX_EXT
            _real_bib = real_bib_path(args.source, args.bib)
            if _real_bib:
                # 已有「真 .bib」：直接使用，**不自动**合并附件（第 134 条）。
                # 日志逐个列出被跳过的候选附件，便于编辑理解「为什么这次没并」。
                _rel_real = _paths.rel(_real_bib, _src_dir)
                _origin = bib_origin(args.source, _real_bib)
                print(f"  · 参考文献：{_rel_real}（{_origin}；真 .bib 直接使用；自动附件不合并）")
                for _c in _inventory:
                    if _c.role != "refs":
                        continue
                    if os.path.realpath(_c.path) == os.path.realpath(_real_bib):
                        continue
                    print(f"      - 跳过参考文献附件：{_c.name}（已有真 .bib，需合并请显式 --refs）")
            else:
                _rf, _adopt, _skipped_refs = multisource.select_refs_attachment(
                    _inventory, allow_bibtex=_src_is_tex
                )
                for _sk in _skipped_refs:
                    print(f"  · 跳过参考文献附件：{_sk}")
                if _rf:
                    args.refs = _rf.path
                    refs_auto = True
                    print("  → 自动采用参考文献附件：" + "；".join(_adopt or [_rf.name]))

    if args.list_templates:
        for lang, t in T.PRESETS.items():
            print(f"\n【{'中文' if lang == 'zh' else '英文'}稿件】默认")
            print(f"  版面样式  {t.sty}")
            print(f"  骨架      {t.skeleton}")
        print(f"\n可覆盖的 .sty：{'、'.join(T.AVAILABLE_STY)}")
        return 0

    # ① 强制校验源文件
    source = require_source(args.source)
    _meta_paths = [os.path.abspath(p) for p in (args.metadata or [])]
    for _mp in _meta_paths:
        if not os.path.exists(_mp):
            die(f"--metadata 指定的文件不存在：{_mp}")
    # B-223 FAIL-2：单份 `.json` 与 `--metadata-json` 同语义（人工元数据，最高优先），
    # 不再当「Word/LaTeX 补充文件」静默忽略；坏 JSON 一律 exit 2。
    _meta_json_paths = [p for p in _meta_paths if os.path.splitext(p)[1].lower() == ".json"]
    _meta_paths = [p for p in _meta_paths if p not in _meta_json_paths]
    metadata = _meta_paths[0] if _meta_paths else None
    # 批63b：GUI 人工元数据（结构化 JSON）——优先级最高；坏 JSON/不存在 exit 2。
    # B-287：两段式也接受：第一段写进暂存 `.tex` 并把归一化值记进状态，第二段只
    # 校验 sha256（交付物来自暂存 `.tex`，不重新排版）；旧状态兼容见 check_structured_metadata。
    structured_meta: dict | None = None
    if args.metadata_json:
        try:
            structured_meta, _mj_warnings = load_metadata_json(args.metadata_json)
        except MetadataJSONError as e:
            die(str(e))
        for _w in _mj_warnings:
            print(f"  ⚠ {_w}")
    for _jp in _meta_json_paths:
        try:
            _st, _jw = load_metadata_json(_jp)
        except MetadataJSONError as e:
            die(f"--metadata 指向的 JSON 不可用：{e}")
        for _w in _jw:
            print(f"  ⚠ {_w}")
        if not _st:
            continue
        if structured_meta is None:
            structured_meta = dict(_st)
            print(f"人工元数据（--metadata {os.path.basename(_jp)}）："
                  f"{len(_st.get('authors') or [])} 位作者")
        else:  # 先到先得：--metadata-json / 前一份 JSON 已给的字段优先，这里只补空
            _filled = 0
            for _k, _v in _st.items():
                if _k != "authors" and not structured_meta.get(_k):
                    structured_meta[_k] = _v
                    _filled += 1
            if not structured_meta.get("authors") and _st.get("authors"):
                structured_meta["authors"] = _st["authors"]
                _filled += 1
            print(f"人工元数据（--metadata {os.path.basename(_jp)}）：补入 {_filled} 项"
                  "（与 --metadata-json 重复时以先给的为准）")
    # B-287：人工元数据来源（GUI 表单临时 JSON / 命令行 JSON）——只进状态记录。
    _structured_origin = ""
    if structured_meta:
        _structured_origin = _coop.metadata_origin(
            args.metadata_json or (_meta_json_paths[0] if _meta_json_paths else ""))
    # B-223：元数据/补充信息合并预览（dry-run 只读）。给 GUI「预览」按钮用：只读源稿
    # 与补充源，输出预览 JSON；**不建产物目录、不写产物、不写交接单、不留临时文件**。
    if args.metadata_preview_out:
        import json
        from slcn import metadata_source as _msrc_prev
        _prev = _msrc_prev.preview(
            source, _meta_paths, form=structured_meta, lang=args.lang,
            channel="latex" if os.path.splitext(source)[1].lower() not in WORD_EXT
            else "word")
        _pout = os.path.abspath(args.metadata_preview_out)
        with open(_pout, "w", encoding="utf-8", newline="\n") as _pf:
            json.dump(_prev, _pf, ensure_ascii=False, indent=1)
            _pf.write("\n")
        print(f"\n元数据预览已写入：{_pout}")
        print("（--metadata-preview-out：只读预览，未写入任何产物）")
        return 0
    # 批63 E2（用户 Q3）：LaTeX 通道的 --metadata 接受 Word 元数据文件——.docx 用现有
    # Word 通道解析器抽字段、合成**临时 .tex**（\my* 宏），后续流程完全不变；
    # .doc / 二进制明确报错 exit 2（Mac 界面会先转 .docx）；.tex 原样、逐字节零变化。
    # B-223：--metadata 可给多份（.docx/.tex/.json）→ 合并成一份临时 .tex；单值路径逐字节不变。
    _meta_word_tmp = ""
    if _meta_paths and os.path.splitext(source)[1].lower() not in WORD_EXT:
        from slcn import metadocx as _metadocx

        if len(_meta_paths) > 1:
            from slcn import metadata_source as _msrc_many

            if args.coop_taskbook_only or args.coop_replies:
                die("多份元数据/补充信息暂不支持协同两段式"
                    "（合并出的临时 .tex 在进程结束会清理，第二段无法复用）："
                    "请改用单份 .tex 元数据文件，或先跑一次普通转换。")
            try:
                _meta_lang = args.lang or detect_lang(source, None)[0]
                metadata, _meta_fields, _meta_warns = _msrc_many.compose_latex(
                    _meta_paths, lang=_meta_lang)
            except Exception as e:  # noqa: BLE001
                die(f"元数据/补充信息合并失败：{type(e).__name__}: {e}")
            _meta_word_tmp = metadata
            print("元数据源（补充信息 " + str(len(_meta_paths)) + " 份）  "
                  + "、".join(os.path.basename(p) for p in _meta_paths))
            print(f"  提取字段 {len(_meta_fields)} 个：{'、'.join(_meta_fields)}")
            print(f"  临时 .tex：{_meta_word_tmp}（运行结束自动清理）")
            for _w in _meta_warns:
                print(f"  ⚠ {_w}")
        else:
            if (args.coop_taskbook_only or args.coop_replies) and \
                    os.path.splitext(metadata)[1].lower() in (".docx", ".doc"):
                die("--metadata 指向 Word 文件时暂不支持协同两段式"
                    "（第一段的临时元数据文件在进程结束会清理，第二段无法复用）："
                    "请改用 .tex 元数据文件，或先跑一次普通转换。")
            try:
                _meta_lang = args.lang or detect_lang(source, None)[0]
                metadata, _meta_fields, _meta_word_tmp = _metadocx.adapt_metadata(
                    metadata, lang=_meta_lang)
            except _metadocx.MetadataError as e:
                die(str(e))
            if _meta_word_tmp:
                print(f"元数据源（Word）  {os.path.abspath(_meta_paths[0])}")
                print(f"  提取字段 {len(_meta_fields)} 个：{'、'.join(_meta_fields)}")
                print(f"  临时 .tex：{_meta_word_tmp}（运行结束自动清理）")
    # 第四十二批 A2：LaTeX 多文件稿自动配「前言源」（正文片段 + 含 \documentclass/宏定义的
    # 前言文件，如期 90/03 的 main.tex + example puzzle.tex）。判据与可解释说明见
    # `slcn.sources.discover_latex_sources()`；显式 --metadata 永远优先；判不准只 warning、保持旧行为。
    if (not metadata and not args.coop_replies
            and os.path.splitext(source)[1].lower() == ".tex"):
        _plan = _sources.discover_latex_sources(source)
        for _w in _plan.warnings:
            print(f"  ⚠ {_w}")
        if _plan.metadata is not None and os.path.abspath(_plan.metadata.path) != source:
            metadata = os.path.abspath(_plan.metadata.path)
            print(f"前言源    {metadata}")
            print(f"          （自动发现：{_plan.note}）")

    # 第 76 条：Word 通道的英文元数据第二源（仅同目录；LaTeX 通道不用这个）。
    # 在**转换之前**就定下来，好让下面「源文件 / 第二源」两行日志把来源交代清楚。
    _en_explicit = metadata
    if len(_meta_paths) > 1 and os.path.splitext(source)[1].lower() in WORD_EXT:
        # B-223：多份补充源里挑第一份「有英文元数据表」的 .docx 当第二源；都没有则沿用旧口径。
        for _mp in _meta_paths:
            if os.path.splitext(_mp)[1].lower() == ".docx" and _sources.en_metadata_sheet(_mp):
                _en_explicit = _mp
                break
    en_meta = (find_en_metadata(source, _en_explicit)
               if os.path.splitext(source)[1].lower() in WORD_EXT else None)

    print("═" * 74)
    print("SLCN 稿件转换器")
    print("═" * 74)
    print(f"源文件    {source}")
    if en_meta:
        print(f"第二源    {en_meta}")
        print("          （英文题名/摘要等仅当正文自身读不出时才取；仅同目录，见第 76 条）")

    # ② 语种
    if args.lang:
        lang, why = args.lang, "用户用 --lang 指定"
    else:
        lang, why = detect_lang(source, metadata)
    print(f"语种      {lang}（{why}）")

    # ③ 模板
    sel = T.resolve(lang, sty=args.sty, skeleton=args.skeleton)
    problems = T.validate(sel)
    if problems:
        die("模板不可用：" + "；".join(problems))
    print(f"模板      {T.describe(sel)}")

    # ④ 输出名（强制：拿不到就问用户，不猜）
    author, author_why = author_from_source(source, metadata)
    try:
        name, name_why = N.resolve_output_name(
            explicit_name=args.name,
            explicit_author=args.author,
            author_from_source=author,
            source_desc=os.path.basename(source),
        )
    except N.AuthorNameError as e:
        die(str(e))
    print(f"第一作者  {author or '（未从源读到）'}  ← {author_why or '用户指定'}")
    print(f"输出名    {name}  ← {name_why}")

    for h in N.homographs(author or args.author or ""):
        print(f"  ⚠ 多音字需确认：{h}  如读音不对，请用 --name 显式指定")

    out_root = os.path.abspath(args.out)
    # 若用户选的目录**本身就叫这个名字**（如 …/Desktop/Lei_Li），就用它，
    # 不再套一层。否则会生成 …/Desktop/Lei_Li/Lei_Li/，
    # 且第二次运行时会因为「目标已存在」直接拒绝——实测踩到过。
    if os.path.basename(out_root) == name:
        out_dir = out_root
    else:
        out_dir = os.path.join(out_root, name)
    stem = name
    final_dir = out_dir

    # ── 第五十四批：两段式 AI 协同初排的目录分流（默认路径一个字节都不改）──────
    #   · 第一段（--coop-taskbook-only）：本次转换写**暂存目录**，最终目录不碰；
    #   · 第二段（--coop-replies）：不在主流程里重新转换，直接走协同收尾
    #     `run_coop_replies()`（读状态 → 解析回填 → 并入/建议 → 编译 → 原子 promote）。
    coop_stage = ""
    if args.coop_taskbook_only or args.coop_replies:
        coop_stage = (os.path.abspath(args.coop_stage) if args.coop_stage
                      else os.path.join(out_root, f"{stem}_待核"))
    if args.coop_replies:
        return run_coop_replies(
            args, source=source, metadata=metadata or "", out_root=out_root,
            final_dir=final_dir, stage_dir=coop_stage, stem=stem,
            lang=lang, sty=sel.sty, skeleton=sel.skeleton,
            structured=structured_meta, structured_origin=_structured_origin,
        )
    if args.coop_taskbook_only:
        out_dir = coop_stage
        print(f"输出到    {out_dir}{os.sep}{stem}.tex")
        print("          （**暂存**：协同初排第一段；最终交付目录本次不写）")
    else:
        print(f"输出到    {out_dir}{os.sep}{stem}.tex")

    # 正文信息手工覆盖（选填）：填了就以人工值为准，空值不覆盖。
    # 声明在 dry-run 之前，方便「先看一眼会怎么做」时也能确认覆盖值收对了。
    overrides = collect_overrides(args)
    if overrides:
        print("正文覆盖  " + "、".join(_OVERRIDE_FIELDS[k] for k in overrides)
              + f"（{len(overrides)} 项，人工值优先）")
    if structured_meta:
        _n_auth = len(structured_meta.get("authors") or [])
        _n_items = (len([k for k in structured_meta if k != "authors"])
                    + sum(1 for a in structured_meta.get("authors") or []
                          if any(a.values())))
        _prio = []
        if _meta_paths:
            _prio.append("优先于 --metadata 元数据文件")
        if overrides:
            _prio.append("重复字段以本参数为准")
        print("人工元数据（--metadata-json）："
              + f"{_n_auth} 位作者、{_n_items} 项输入字段"
              + (f"（{'；'.join(_prio)}）" if _prio else ""))

    def _coop_own_artifact(name_: str) -> bool:
        """暂存目录里的协同文件也算「本工具产物」（只用于协同模式的重跑判断）。"""
        if name_ in ("_AI协同状态.json", "_AI协同记录.md"):
            return True
        return (name_.startswith(f"{stem}_AI建议.")
                or name_ in (f"{stem}_文献核实.md",))

    if os.path.exists(out_dir) and not args.force:
        contents = sorted(os.listdir(out_dir))
        # 用户**显式选了这个目录**（名字与输出名一致）时，意图明确，不必再拦。
        # 但仍要保护「里面装着别的文章」的情况——那才是真的可能覆盖到编辑部成果。
        # 允许的情况：空的、只有系统垃圾文件（.DS_Store）、或只含本工具自己的产物。
        # 判定规则见文件头的 `is_own_product()`（本工具产物 + 编译中间文件）。
        foreign = [c for c in contents
                   if not is_own_product(c, stem) and not c.startswith(".")
                   and not (args.coop_taskbook_only and _coop_own_artifact(c))]
        # 判据只有一条：**目录里有没有不属于本工具的东西**。
        #   · 只有本工具的产物 → 直接覆盖，这就是「重跑一次」的常见情形，不该拦。
        #   · 混着别人的文件 → 拦住，那才可能是编辑部已有的稿件目录。
        #
        # ⚠️ 这里曾经还加过一个「用户选的是不是同名目录」的条件，是错的：
        #    正常用法是 `--out ~/Desktop` → `~/Desktop/Lei_Li`，两者不同名，
        #    于是**每次重跑都被拦**，哪怕目录里全是自己的产物。
        if foreign:
            die(
                f"目标文件夹已存在，且含非本工具的文件，拒绝覆盖：{out_dir}\n"
                f"  不属于本工具产物的 {len(foreign)} 项："
                f"{'、'.join(foreign[:6])}{' …' if len(foreign) > 6 else ''}\n"
                "  这可能是编辑部已有的稿件目录。确认要覆盖请加 --force，"
                "或改用 --name 指定别的输出名。"
            )

    if args.dry_run:
        print("\n（--dry-run：以上为将要执行的操作，未写入任何文件）")
        return 0

    # ⑤ 生成
    print()
    meta = {
        "vol": args.vol, "issue": args.issue, "year": args.year, "pages": args.pages,
        "received": args.received, "editor": args.editor, "grants": args.grants,
    }
    ext = os.path.splitext(source)[1].lower()
    # B-220 N6：短引库解析用的 Store（Word 通道才用；只读；开关同默认回填）。
    # 与 ⑦ 的 bibdb_readonly_scan 共用——扫描端通过 `res._bibdb_store_shared` 复用。
    _bibdb_store = resolve_bibdb_apply_store(args) if ext in WORD_EXT else None
    try:
        if ext in WORD_EXT:
            res = build_docx(
                source, out_dir, basename=stem, lang=lang,
                sty=sel.sty, skeleton=sel.skeleton, verbose=True,
                quote_threshold=args.quote_threshold, meta=meta,
                # 第 76 条：英文元数据第二源（同目录的「英文摘要」docx）。
                # None = 不接第二源 = 与从前逐字节相同。
                metadata_path=en_meta,
                # B-223：补充信息（多份 .docx，只补正文空值；空列表 = 旧行为）
                supplements=_meta_paths,
                # overrides 只在 build.py 已支持该关键字时才传（见 overrides_kwargs），
                # 旧引擎配新界面不会崩，只是这次覆盖不生效并在日志里说明。
                **overrides_kwargs(build_docx, overrides),
                **structured_kwargs(build_docx, structured_meta),
                # 第四十六批 C 类可选开关（默认关）：命令行 --end-list-priority 才生效
                end_list_priority=getattr(args, "end_list_priority", False),
                number_cite_map=getattr(args, "number_cite_map", False),
                c_class_union=resolve_c_class_union(args),
                # B-220 N6：短引库解析（引用库默认开；--no-bibdb-apply 等关闭）
                bibdb_store=_bibdb_store,
                # B-221 C1：初排交接单默认生成；--no-handoff 关闭（目录逐字节回旧）
                handoff=_handoff_enabled(args),
            )
        else:
            _bib_path = find_bib(source, args.bib)
            if _bib_path:
                print(f"  · 参考文献：{os.path.basename(_bib_path)}"
                      f"（{bib_origin(source, _bib_path)}）")
            res = build_latex(
                source, out_dir,
                metadata_path=metadata,
                bib_path=_bib_path,
                basename=stem, lang=lang,
                sty=sel.sty, skeleton=sel.skeleton, verbose=True, meta=meta,
                **overrides_kwargs(build_latex, overrides),
                **structured_kwargs(build_latex, structured_meta),
            )
    except Exception as e:  # noqa: BLE001
        die(f"生成失败：{type(e).__name__}: {e}")
    # B-220 N6：让 ⑦ 的引用库比对复用同一份只读 Store（避免二次加载 7043 条）。
    if _bibdb_store is not None:
        try:
            res._bibdb_store_shared = _bibdb_store
        except Exception:  # noqa: BLE001
            pass

    # ⑦ 合并参考文献附件（全库 28% 的稿件把参考文献放在单独文件里）
    #
    # 实测 02 Lili_Zhao：正文 docx 里没有参考文献，全在 `附件一 完整参考文献.docx`。
    # 不合并的话引用全部无法替换（22 处）。
    #
    # 第 134 条（2026-10-18）：附件不能「扫到就并」——
    #   · 有真 .bib（--bib / 源内 \addbibresource / 同目录 .bib）时，**自动挑的附件跳过**
    #     （71/04 正文目录本来就有 reference.bib，再并 text2bib 中间产物只会帮倒忙）；
    #   · 没有真 bib 时才并：BibTeX 附件按原文直入，纯文本清单先过结构校验；
    #   · 合并结果逐条再做结构校验，坏条目（嵌套花括号等）不写进产物（biber 会整份报废）。
    if args.refs and os.path.exists(args.refs):
        res = run_refs_attach(args, res, source=source, out_dir=out_dir,
                              stem=stem, refs_auto=refs_auto)

    # ⑧ 源稿归档到 original/（与往期文件夹命名一致）
    if not args.no_original:
        print("\n⑧ 归档源稿到 original/")
        # 第 120 条：多文件 LaTeX 稿（展开了 \input）的正文来自整个文件夹，只归档主文件
        # 会丢分节稿 —— 这种情况自动按整目录归档（等价 --copy-dir）。
        auto_copy_dir = bool(getattr(res, "expanded_inputs", 0)) and not args.copy_dir
        if auto_copy_dir:
            print("   （正文源是多文件稿：已按整目录归档，同 --copy-dir）")
        stage_original(
            out_dir,
            [(source, "正文源")] + ([(metadata, "元数据源")] if metadata else [])
            + [(p, "补充信息源") for p in _meta_paths[1:]],
            copy_dir=args.copy_dir or auto_copy_dir,
        )

    if res.warnings:
        print("\n⚠ 注意事项：")
        for w in res.warnings:
            print(f"  · {w}")

    # 第五十八批：默认初排的引用库比对（只打一行汇总）；B-217 ⑧ 起默认**回填**：
    # 安全命中只补缺失字段（--no-bibdb-apply 回到旧只读口径、逐字节相同）。
    # 用户反馈「初排感觉没查引用库」：此前只有 --verify-refs/--verify-plan 分支才查库。
    # `--verify-refs` 分支自己会查库并打印明细（第 129 条），这里不重复跑；无库时静默。
    # ⚠️ B-217 修复（41 篇两遍确定性）：`--verify-plan` **也照常写出产物**（既有承诺），
    #    回填必须同样生效——否则同一源「compile 模式 vs --verify-plan 模式」的
    #    reference.bib 不同（两遍 sha 不一致 → NONDETERMINISTIC）。
    if not args.verify_refs:
        bibdb_info = bibdb_readonly_scan(args, res)
        # B-221 C1：交接单在「全部产物写完」之后刷新一次——build_docx 生成时看不到 ⑦
        # 引用库回填（它改的是 build 之后才写回的 reference.bib），这里用 ⑦ 的现成返回值
        # 补上「引用库回填命中/应用条数」，并把最终 .tex 作为 ③ 的汇总对象。只重渲染汇总，
        # 不重算判据、不改任何 .tex/.bib；失败不阻断出稿。
        if _handoff_enabled(args) and getattr(res, "handoff_path", None):
            try:
                from slcn import handoff as _handoff

                _handoff.write_from_result(res, bibdb=bibdb_info, log=print)
            except Exception as e:  # noqa: BLE001  附加产物，不许连累出稿
                print(f"   ⚠ 初排交接单刷新失败（不影响出稿）：{type(e).__name__}: {e}")

    # ⚠️ ⑨ 编译 / ⑩ 导出原稿 PDF **挪到了 ⑪ 之后**（2026-10-15，见下面第 ⑪ 节末尾）：
    #    用户决定「AI 结果要进产物」，而并进去的条目必须体现在 PDF 里——所以顺序必须是
    #    「写 bib → 并入 AI 结果 → 编译」。非 AI 路径（没配 LLM）产物逐字节不变，
    #    只是控制台上 ⑪ 的几行现在打在 ⑨ 之前。

    # ⑪ 参考文献核实任务书（交给编辑各自的 AI）+ 把 AI 结果**并入 reference.bib**
    #
    # ⚠️ 2026-10-14 按编辑部要求**移除了 CrossRef 通道**：中文文献覆盖差，
    #    而且会给跨类型错配的「建议」（书被判成期刊论文，照着改就是照错改）。
    #    改为生成一份 markdown：写明编辑部规范、查证来源优先级、每条缺什么、
    #    以及固定回答格式——编辑把它交给自己的 AI（谁的模型都行）。
    #    `SLCN_LLM_*` 仍保留：配了就按**同一份规范**自动跑一遍，结果标为「AI 建议，须核实」。
    # ⚠️ 2026-10-15 用户决定：**AI 结果要进产物**（取代 README 第 41 条的「AI 结果永不进产物」）。
    #    配了 LLM 时，通过安全校验的条目由 `slcn/aibib.py` 并入 `reference.bib`
    #    （回滚副本 `reference.bib.ai前` + 离线回读校验报告 + 任务书「六、合并记录」）。
    #    因此这一节必须在**编译之前**跑，PDF 才会反映并入结果——见上面 ⑨/⑩ 挪走的说明。
    #    要退回旧行为：`--no-ai-merge`（只出任务书）。没配 LLM 时行为与从前一字不差。
    # ⚠️ `--verify-plan` 也进这一块：它要算「本次会核实多少条」，而引用键只有在**转换之后**
    #    （build 已把正文引用替换成 `\parencite`、写进产物 .tex）才拿得准。所以它是
    #    「照常转换、只算账」而不是 --dry-run：--dry-run 在 ⑤ 之前就退出，那时还没有 bib/tex。
    # 第五十四批：第一段的任务书路径/文献键/低置信项在这里收集，块外写状态。
    coop_taskbook_path = ""
    coop_bib_keys: list[str] = []
    coop_low_items: list[dict] = []
    # 批79（B-131）：核实通道的引用库分类计数（只读汇总；给交付记录「库命中 H」用）。
    # 未比对引用库时 checked=False → 记录写「未测」，不估算。
    coop_bibdb: dict = {"checked": False, "safe": None, "missed": None}

    if args.verify_refs or args.verify_plan:
        _verify = run_verify_refs(args, res, out_dir=out_dir, stem=stem, verify_max=verify_max)
        if isinstance(_verify, int):
            return _verify
        coop_taskbook_path = _verify["taskbook_path"]
        coop_bib_keys = _verify["bib_keys"]
        coop_bibdb = _verify["bibdb"]

    # ── 第五十四批：第一段收尾——任务书追加「初排低置信项」+ 写状态 JSON ────────
    if args.coop_taskbook_only:
        run_coop_taskbook(args, res, source=source, metadata=metadata or "",
                          out_root=out_root, out_dir=out_dir, final_dir=final_dir,
                          stem=stem, lang=lang, sel=sel, coop_taskbook_path=coop_taskbook_path,
                          coop_bib_keys=coop_bib_keys, coop_bibdb=coop_bibdb,
                          structured_metadata=structured_meta,
                          structured_origin=_structured_origin)

    # ⑨ 编译（**放在 ⑪ 之后**：并进 reference.bib 的 AI 结果要体现在 PDF 里）
    compile_failed = ""
    if args.compile:
        print("\n⑨ 编译")
        ok, info = compile_pdf(out_dir, stem)
        if ok:
            size = os.path.getsize(info) // 1024
            print(f"   ✅ {os.path.basename(info)}（{size} KB）")
            n_pages = pdf_page_count(info)
            if n_pages:
                if args.pages:
                    print(f"   PDF 共 {n_pages} 页（--pages 给的是 {args.pages}）")
                else:
                    print(f"   PDF 共 {n_pages} 页 → 补 --pages <首页>-<末页> 可自动填好页眉与页数")
        else:
            print(f"   ❌ 编译未通过：{info}")
            print("      产物 .tex 已保留，可人工排查后重编。")
            compile_failed = info

    # ⑩ 导出原稿 PDF（供矢量形状人工截图）
    if args.source_pdf and os.path.splitext(source)[1].lower() in (".docx", ".doc"):
        from slcn import office

        print("\n⑩ 导出原稿 PDF（LibreOffice）")
        pdf, msg = office.docx_to_pdf(source, out_dir)
        if pdf:
            final = os.path.join(out_dir, f"{stem}_原稿.pdf")
            if os.path.abspath(pdf) != os.path.abspath(final):
                os.replace(pdf, final)
            print(f"   ✅ {os.path.basename(final)}")
            n_vec = res.warnings and sum(
                1 for w in res.warnings if "矢量形状" in w
            )
            if n_vec:
                print(f"   ⚠ 正文含 {n_vec} 处矢量形状占位，请对照该 PDF 截图或重画")
        else:
            print(f"   ⚠ {msg}")
        office.cleanup_profile(out_dir)

    # ⑫ 清理编译中间文件
    if args.clean:
        print("\n⑫ 清理编译中间文件")
        n, _ = _clean.clean(out_dir, log=lambda s: print(f"   {s}"))
        if n == 0:
            print("   （没有需要清理的文件）")

    # ── 第五十四批：第一段收口——**不打印交付成功**，最后一行指向暂存目录 ──────
    if args.coop_taskbook_only:
        print()
        print("═" * 74)
        if compile_failed:
            print(f"\n❌ 协同初排已完成转换，但 ⑨ 编译未通过（退出码 2）：{compile_failed}",
                  file=sys.stderr)
            print(f"暂存目录：{out_dir}")
            return 2
        print("   协同初排第一段完成（**尚未交付**）：产物与任务书都在暂存目录。")
        print("   下一步：编辑把任务书交给任意 chat 模型核实 → 回填后运行 --coop-replies。")
        print("═" * 74)
        print(f"暂存目录：{out_dir}")
        return 0

    print()
    print("═" * 74)
    if args.clean:
        print("   已清理编译中间文件；.log 按 EditToolBox 惯例保留，便于排查问题。")
    print("═" * 74)
    # ⚠️ 契约行必须是 **stdout 的最后一行**（INVARIANTS 三.3 / ENGINE_API 的表格）——
    #    从前它后面还跟着一条收尾横幅，于是"最后一行"实际是 `═`（外壳真按最后一行取数就会
    #    取到横幅；mac 外壳目前是自己记着 `--out` 才没炸）。2026-10-17 逐页体检发现。
    print(f"完成。产物目录：{out_dir}")
    if compile_failed:
        # ⚠️ 契约（`docs/ENGINE_API.md`「退出码」）：**没有 1**；中途某一步失败也算失败、
        #    一律 2 —— 外壳判「成功」只看 0。从前这里无条件 `return 0`，于是"编译没通过"
        #    在界面上照样是 ✅ 完成（2026-10-17 逐页体检实测：LaTeX 通道 `--compile`
        #    xelatex 失败、`PIPESTATUS=0`）。消息按 `die()` 的口径走 stderr；
        #    stdout 的**最后一行仍是契约行**（INVARIANTS 三.3）。
        print(f"\n❌ 转换已完成，但 ⑨ 编译未通过（退出码 2）：{compile_failed}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
