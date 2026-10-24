"""Word 文稿（.docx）解析。

实测 10 Bo_Chen 的原稿：《陈波 当代逻辑哲学鸟瞰.docx》104 个非空段落，
**全部是 Normal 样式**，0 表格 0 图片，14 处 OMML 公式。所以章节层级不能靠
样式判断，只能靠「一、」「（一）」这种编号模式。

正文引用是人年制 `（Haack 1974）`、`（Martin 2025，31-44）`、`（参见陈波 2022）`。

关于引用键的一个重要认识
------------------------
本刊惯例其实**自相矛盾**：中文作者取拼音（陈波→Chen、刘靖贤→LiuJingxian），
译名作者却取回英文原名（罗素→Russell、克里普克→Kripke），而哈瑞又用了拼音
（→HaRui，而非 Harre）。硬猜「正确的键」命中率只有 48%。

但工具**同时产出 .tex 和 .bib**，所以键只需**内部自洽**——正文替换与 bib 生成
共用同一个键生成器即可。这把最难的一步变成了最简单的一步。
"""

from __future__ import annotations

import os
import re
import zlib
from dataclasses import dataclass, field

# B-217：新判据一律落新模块（本文件已越线，LEAD-LESSONS §4），这里只做薄接线：
#   · wordcite   —— 正文/脚注共用的引用引导语判据 + 正文裸编号通道（B-217①）；
#   · wordcaption—— 题注识别与唯一相邻关联（B-217③）；
#   · wordrefs   —— 文献字段扩展判据（B-217②，供 refs_to_bib 兜底）。
from .wordcaption import caption_candidate as _caption_candidate
from .wordcaption import find_captions as _find_captions
from .wordcaption import is_figure_para as _is_figure_para
from .wordcite import CUE_LEAD_FAR_RE as _FN_LEAD_FAR_RE
from .wordcite import CUE_LEAD_NEAR_RE as _FN_LEAD_NEAR_RE
from .wordcite import cue_evidence as _cue_evidence
from .wordcite import fn_in_math as _fn_in_math
from .wordcite import fn_in_paren_depths as _fn_in_paren_depths
from .wordcite import link_body_cue_citations as _link_body_cue_citations

# ── B-207：docxsource 已纯搬运拆为 6 个模块；这里按旧命名空间逐一 re-export ──
from .wordcore import (_ZERO_WIDTH, _strip_zero_width, SURNAME_PINYIN, TRANSLATED_NAMES, TYPE_MAP,
                       PUNCT_TO_STRIP, _LATEX_ESCAPES, _ESCAPE_RE, escape_latex, _footnote_latex,
                       escape_bib_value, RefEntry, DocxDoc, _has_cjk)
from .wordentry import (_REF_RE, parse_reference_line, _REF_CN_RE, _REF_EN_RE, _QUOTED,
                        _parse_reference_line_en, _looks_short_bibliographic_note,
                        _entry_is_note_only, _src_line_key, _citation_title_year,
                        _src_line_keys_for_works, _REF_EN_FULLNAME_RE, _EN_AUTHOR_SPLIT, _initials,
                        _parse_reference_line_en_fullname, _REF_CN2_RE, _parse_reference_line_cn2,
                        _parse_reference_line_cn, _NUMBERED_PREFIX_RE, _APA_YEAR_RE,
                        _APA_YEAR_RANGE_RE, _EN_SENTENCE_END, _clean_apa_authors, _apa_kind,
                        _parse_reference_line_apa, _CN_COLON_RE, _parse_reference_line_cn_colon,
                        _EN_YEAR_END_RE, _parse_reference_line_year_end, _CN_APA_RE,
                        _parse_reference_line_cn_apa, _QUOTED_TITLE_RE,
                        _parse_reference_line_loose, _numbered_refs, _make_key_core, make_key,
                        _dedupe_keys, _EN_JOURNAL_RE, _EN_JOURNAL_RE2, _KIND_BY_ETYPE,
                        _match_en_journal, _clean_journal_name, _match_incollection,
                        _en_entry_fields, _EN_ORG_RE, _finalize_en_entry, refs_to_bib,
                        _split_publisher, _bib_author, _extract_publisher, _BT_STOP_RE,
                        _EDITOR_MARK_RE, _BT_NON_TITLE_RE, _PUB_COLON_RE, _bt_candidate_ok,
                        _editor_candidate_ok, _incollection_book_editor, _incollection_pub_addr)
from .wordcitebody import (_CITE_RE, _normalize_locator, _FN_BARE_NUM_RE, _FN_SENT_END,
                           _FN_GAP_ASCII_OK, _FN_GAP_TOKEN_RE, _FN_GAP_PUNCT,
                           _FN_NONCITE_SUFFIX_RE, _FN_INHERIT_MAX_GAP, _fn_inherit_gap_ok,
                           link_footnote_bare_citations, _NUM_GROUP, _cite_nums, _GRP_CN, _GRP_EN,
                           link_numbered_citations, _en_locator_cmd, _LATEX_CMD_RE, _NAME_TOKEN_RE,
                           _cite_name_tokens, _author_surname_token, _year_fallback_evidence,
                           link_citations)
from .wordmeta import (_HEAD1, _HEAD_CN_SPACE, _HEAD2, _HEAD_CN_ARABIC, _HEAD_CN_DOT,
                       _HEAD_CN_BAD_TAIL, _CN_HEAD_PUNCT, _is_cn_numbered_heading,
                       _is_cn_dot_heading, _ABS_LEAD, _ABS_RE, _KW_RE, _REF_HEADS, _ABS_LOOKAHEAD,
                       _ABS_MIN_CHARS, _ABS_END_CHARS, _META_LABEL_RE, _CLC_META_RE,
                       _is_clc_meta_only, MIN_BODY_CHARS, _HEAD_EN1, _HEAD_EN2, _HEAD_EN2_HALF,
                       _HEAD_EN3, META_SCAN, _SUB_RE, _EMAIL_RE, _EMAIL_ANY_RE, _AFFIL_HEAD_RE,
                       _TAIL_BLOCK_SCAN, _TAIL_LABEL_RE, _ADDR, _ADDR_ALONE_RE, _ADDRS_ONLY_RE,
                       _NAME_ADDR_RE, _PAREN_ADDR_RE, _AUTHOR_MARK_RE, _TRAIL_PUNCT,
                       _PLACEHOLDER_EMAIL_RE, _EMAIL_TAIL_FALLBACK, _AFFIL_WORD_RE,
                       _AFFIL_HEAD_WORDS, _AFFIL_LABEL_RE, _AFFIL_NEVER_RE, _AFFIL_PROSE_RE,
                       _AFFIL_CITY_ZIP_RE, _AFFIL_LIST_ITEM_RE, _AFFIL_UNIT_START_RE,
                       _AFFIL_UNIT_TAIL, _AFFIL_UNIT_END_RE, _AFFIL_UNIT_END_ANY_RE,
                       _AFFIL_TITLE_TAIL_RE, _AFFIL_NEVER_START_RE, _LATIN_STOPWORDS,
                       _AFFIL_LATIN_START_RE, AFFIL_EN_JOIN, _clean_affil_line,
                       _clean_affil_line_strict, _clean_affil_by_sentence, _clean_affil_cjk,
                       _clean_affil_latin, _affil_same_unit, _affil_lines_to_units,
                       _strip_author_names, _affil_block_hits, _affil_head_zone,
                       _looks_like_name_line, collect_affiliations, _norm_email, _emails_in_text,
                       collect_emails, _known_cn_authors, _known_en_authors, _is_subtitle,
                       _title_with_break, _looks_like_person, _label_body, _looks_like_abs_prose,
                       _abs_body_cn, _backtrack_block_start, _skip_en_refs_to_title,
                       locate_tail_block, _tail_abstract_cn, parse_tail_metadata)
from .wordrender import (_TITLE_SMALL_WORDS, _looks_plain_en_title, classify_en_heading,
                         find_char_diagrams, SHAPE_PLACEHOLDER, table_env, render_cell, figure_env,
                         hoist_quote_fallback, render_para)
from .wordbody import (_AUTO_NUM_FMT_OK, _AUTO_NUM_LVL_RE, _PRE_NUM_RE, _xml_para_text,
                       _docx_numbering_defs, _auto_numbered_ref_items,
                       _materialize_auto_numbered_refs, _footnote_num2key_gate,
                       _footnote_bare_cite_gate_warning, _THEOREM_ENV, _NUM_CHARS,
                       _THEOREM_HEAD_RE, _PROOF_HEAD_RE, _REMARK_HEAD_RE, _EQ_REF_RE,
                       _drop_text_prefix, _theorem_env_of, _QED_TOKENS, _strip_qed_tail,
                       _proof_env_of, _remark_prefix, parse_docx)
