#!/usr/bin/env python3.14
r"""V20：「成对截断两侧差异不可辨」——十个成对点的回归测试。

缺陷原样（独立验收 §3.1 第 7 项）
-------------------------------
报告/核对单/交接单里凡是「旧 → 新」**成对**打印的地方，两侧各自 `_clip(s, n)`。
两个值的前 n 个字符完全相同时，两侧 clip 结果**逐字节相同**，于是印出：

    title：`AAAA…（52 个 A）` → `AAAA…（52 个 A）`

编辑看见「有改动」，却看不出改了哪里。定量证据就是本文件 ① 组：

    _clip("A"*52 + "ZZZ", 50) == _clip("A"*52 + "QQQ", 50)   # True

本卡（V20）把 5 个产品文件的 **10 个成对点**全部换成
`slcn.bibparse.pair_summary(old, new, n)`（保留共同前缀/后缀锚 + 差异窗口），
并清掉三处随之失效的死代码。

断言分四组：
  ① **旧行为不可辨**（反例证据）：`bibdb_core._clip` / `bibparse._clip` 与三个
     已删的自建截断（`_short_value` / `metadata_source._clip` / `_clip_sha`）的
     等价内联复现，对同一对值产出**相等**字符串 —— 这就是缺陷的定量证明。
  ② **十个使用点逐个跑到真实文案上**：`('A'*52+'ZZZ', 'A'*52+'QQQ')` 这一对
     在每一处产出的摘要**两侧必须不同**（`ZZZ`/`QQQ` 两个差异标记都在同一行）。
     另用 200 字符共同前缀再验一遍「真截断」路径。
  ③ **短值完整可读**：`"张三"` / `"李四"` 原样输出，不加 `…` / `⋯`。
  ④ **Markdown 转义保留**：`_md_escape` 点（作者报告/编辑器报告）里的 `|` 仍转义成
     `\|`、表格未多列；`_md_code` 点的既有行为（管道符不转义，属本卡范围外）未变；
     `*` / `_` 仍在代码跨度内，不破坏强调。

不写真实 bib 库（全部 tempfile 合成库）、不联网、不调模型。

用法：
    cd Engine && PYTHONPATH=.:vendor PYTHONDONTWRITEBYTECODE=1 \
        /opt/homebrew/bin/python3.14 tests/test_pair_summary_v20_20261117.py
"""

import contextlib
import io
import os
import re
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibdb  # noqa: E402
from slcn import bibdb_core, bibdb_import, bibdb_reports, bibdbapply, handoff  # noqa: E402
from slcn import metadata_source  # noqa: E402
from slcn.bibparse import pair_summary  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ── 旧实现的等价内联复现（对应三处已删死代码 + 两个仍在的 `_clip`）────────────
# 为什么内联：`bibdbapply._short_value` / `metadata_source._clip` 已被本卡删掉，
# `bibdb_reports._clip` 的 import 也已删；要证明「旧行为不可辨」只能复现其实现。
def old_bibdb_reports_clip(text, n=50):
    """旧 `bibdb_reports` 用的 `bibdb_core._clip` 形状（截 n-1 + `…`）。"""
    return bibdb_core._clip(text, n)


def old_short_value(value, n=40):
    """已删 `bibdbapply._short_value`（截 n-1 + `…`）。"""
    v = " ".join(str(value or "").split())
    return v if len(v) <= n else v[: n - 1] + "…"


def old_short(value, n=40):
    """`handoff._short`（仍在用，未删；形状同 `_short_value`）。"""
    v = " ".join(str(value or "").split())
    return v if len(v) <= n else v[: n - 1] + "…"


def old_metadata_clip(text, n=50):
    """已删 `metadata_source._clip`（压空白后截 n + `…`）。"""
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n] + "…"


def old_clip_sha(sha):
    """`bibdb_import._clip_sha`（前 12 位 + `…`；仍在用，被 bibdb.py 导入）。"""
    text = str(sha or "")
    return f"{text[:12]}…" if len(text) > 12 else (text or "（空）")


HEAD = "A" * 52
OLD = HEAD + "ZZZ"
NEW = HEAD + "QQQ"
LONG_OLD = "B" * 200 + "ZZZ"
LONG_NEW = "B" * 200 + "QQQ"
SHORT_OLD, SHORT_NEW = "张三", "李四"


def _line_with(text, needle):
    for line in text.splitlines():
        if needle in line:
            return line
    return ""


def _unescaped_pipes(line):
    return len(re.findall(r"(?<!\\)\|", line))


# ─────────────────────────────────────────────────────────────────────────
# ① 旧行为不可辨：定量证明（反例证据）
# ─────────────────────────────────────────────────────────────────────────
print("\n── ① 旧行为不可辨：定量证明 ──")

_clip_old = old_bibdb_reports_clip(OLD, 50)
_clip_new = old_bibdb_reports_clip(NEW, 50)
print(f"     bibdb_core._clip(OLD,50) = {_clip_old!r}")
print(f"     bibdb_core._clip(NEW,50) = {_clip_new!r}")
check("① 定量证明：`_clip('A'*52+'ZZZ', 50) == _clip('A'*52+'QQQ', 50)` 为 True"
      "（两侧被截成同一个前缀，用户看不出差异）",
      _clip_old == _clip_new,
      f"{_clip_old!r} == {_clip_new!r}")
check("① 旧行为连差异标记都不剩：两侧都不含 ZZZ / QQQ",
      "ZZZ" not in _clip_old and "QQQ" not in _clip_old,
      repr(_clip_old))

check("① 已删的 bibdbapply._short_value（n=40）同样不可辨",
      old_short_value(OLD, 40) == old_short_value(NEW, 40),
      repr(old_short_value(OLD, 40)))
check("① handoff._short（仍在用，未删）在成对用法下同样不可辨",
      old_short(OLD, 40) == old_short(NEW, 40),
      repr(old_short(OLD, 40)))
check("① 已删的 metadata_source._clip（n=50）同样不可辨",
      old_metadata_clip(OLD, 50) == old_metadata_clip(NEW, 50),
      repr(old_metadata_clip(OLD, 50)))
check("① 旧 rehash 用的 _clip_sha（前 12 位相同）同样不可辨",
      old_clip_sha("a1b2c3d4e5f6" + "0" * 52) == old_clip_sha("a1b2c3d4e5f6" + "f" * 52),
      repr(old_clip_sha("a1b2c3d4e5f6" + "0" * 52)))

# 新函数：同一对值两侧必须不同
for _n in (12, 40, 50, 60):
    _was, _should = pair_summary(OLD, NEW, _n)
    check(f"① 新 pair_summary(n={_n})：两侧不同且各自带着自己的差异标记",
          _was != _should and "ZZZ" in _was and "QQQ" in _should,
          f"{_was!r} / {_should!r}")
check("① 三处死代码确实已从产品文件消失",
      not hasattr(bibdb_reports, "_clip")
      and not hasattr(bibdbapply, "_short_value")
      and not hasattr(metadata_source, "_clip"))
check("① 仍被外部依赖的两个保留（不许误删）",
      hasattr(handoff, "_short") and hasattr(bibdb_import, "_clip_sha"))


# ─────────────────────────────────────────────────────────────────────────
# ② 十个使用点：真实文案上两侧必须可辨
# ─────────────────────────────────────────────────────────────────────────
print("\n── ② 十个使用点：成对摘要两侧可辨 ──")

# --- P1 build_normalize_apply_md（bibdb_reports，n=50）-------------------
APPLIED = [{"key": "k1", "entry_type": "article", "semantic": True, "rules": ["r"],
            "diff": {"title": {"old": OLD, "new": NEW}}}]
VERIFICATION = {"records": 1, "semantic": 0, "semantic_excluding_skipped": 0,
                "format_only": 0, "errors": 0, "remaining_keys": []}
md_p1 = bibdb_reports.build_normalize_apply_md(
    "/tmp/v20/bibliography.jsonl", {"records": 1}, APPLIED, [], VERIFICATION,
    "a", "b", "", "")
p1_line = _line_with(md_p1, "ZZZ")
p1_was, _, p1_should = p1_line.partition(" → ")
check("② P1 bibdb_reports.py:714-716（build_normalize_apply_md, n=50）两侧可辨",
      bool(p1_line) and p1_was != p1_should and "QQQ" in p1_should,
      p1_line[:160])

# --- P2 build_normalize_md（bibdb_reports，n=60）-------------------------
NORMALIZE_STATS = {
    "records": 1, "semantic": 1, "format_only": 0, "errors": 0, "risky_records": 0,
    "rules": {"r": 1}, "format_rules": {}, "fields": {"title": 1}, "risks": {},
    "semantic_rows": [{"key": "k1", "entry_type": "article", "rules": ["r"],
                       "risks": [], "issues": [], "slug": "s",
                       "diff": {"title": {"old": LONG_OLD, "new": LONG_NEW}}}],
    "error_rows": [],
}
md_p2 = bibdb_reports.build_normalize_md(
    None, "/tmp/v20/bibliography.jsonl", [], NORMALIZE_STATS, "a", "b")
p2_line = _line_with(md_p2, "ZZZ")
p2_was, _, p2_should = p2_line.partition(" → ")
check("② P2 bibdb_reports.py:863-865（build_normalize_md, n=60）两侧可辨"
      "（200 字符共同前缀 → 真截断路径）",
      bool(p2_line) and p2_was != p2_should and "QQQ" in p2_should
      and "⋯" in p2_line,
      p2_line[:160])

# --- P3 build_author_report_md（bibdb_reports，n=60）---------------------
AUTHOR_STATS = {"records": 1, "with_author": 1, "changes": 1, "skips": 0,
                "reasons": {}, "skip_rows": [],
                "change_rows": [{"key": "k1", "old": OLD, "new": NEW, "sources": []}]}
md_p3 = bibdb_reports.build_author_report_md(
    "/tmp/v20/bibliography.jsonl", AUTHOR_STATS, "a", "b", applied=False)
p3_line = _line_with(md_p3, "ZZZ")
check("② P3 bibdb_reports.py:1106-1107（build_author_report_md, n=60）两侧可辨",
      bool(p3_line) and "QQQ" in p3_line,
      p3_line[:160])

# --- P4 _editor_table_row（bibdb_reports，n=60）--------------------------
EDITOR_ROW = {"key": "k1", "old": OLD, "new": NEW, "sources": [], "flags": [],
              "entry_type": "article", "title": "T", "reason": "", "issues": [],
              "slug": "s", "status": "change", "n_editors": 1, "has_author": True}
p4_line = bibdb_reports._editor_table_row(1, EDITOR_ROW)
check("② P4 bibdb_reports.py:1365-1366（_editor_table_row, n=60）两侧可辨",
      "ZZZ" in p4_line and "QQQ" in p4_line, p4_line[:200])
p4_skip = bibdb_reports._editor_table_row(
    1, dict(EDITOR_ROW, status="skip", new=""))
check("② P4 跳过行仍写「（保持原样）」（未被成对规则改写）",
      "（保持原样）" in p4_skip and "QQQ" not in p4_skip, p4_skip[:200])

# --- P5/P6 build_editor_report_md（bibdb_reports，n=60）------------------
EDITOR_ROWS = [dict(EDITOR_ROW, flags=["(eds.) 形态"])]
EDITOR_STATS = bibdb_reports._editor_stats(EDITOR_ROWS, 1)
EDITOR_VERIFICATION = {"remaining": 0, "skips": 0, "with_author": 0,
                       "remaining_keys": []}
md_p56 = bibdb_reports.build_editor_report_md(
    "/tmp/v20/bibliography.jsonl", EDITOR_STATS, "a", "b", EDITOR_VERIFICATION)
_p56_hits = [line for line in md_p56.splitlines() if "ZZZ" in line]
check("② P5 bibdb_reports.py:1491-1492（editor 报告 (eds.) 表, n=60）两侧可辨",
      any("QQQ" in line and "同规则" not in line for line in _p56_hits)
      and any("QQQ" in line for line in _p56_hits),
      (_p56_hits[0][:160] if _p56_hits else "MISSING"))
check("② P6 bibdb_reports.py:1531-1532（editor 报告倒装样例, n=60）两侧可辨",
      len(_p56_hits) >= 2 and all("QQQ" in line for line in _p56_hits[:2]),
      " / ".join(line[:80] for line in _p56_hits[:2]))

# --- P7 bibdbapply.format_conflicts（n=40）------------------------------
APPLIED_CONFLICT = [{"key": "k1", "replaced": {"title": OLD},
                     "filled": {"title": NEW}}]
p7_lines = bibdbapply.format_conflicts(APPLIED_CONFLICT)
p7_line = next((line for line in p7_lines if "样本" in line), "")
check("② P7 bibdbapply.py:622-623（format_conflicts, n=40）两侧可辨",
      "ZZZ" in p7_line and "QQQ" in p7_line, p7_line[:160])

# --- P8 handoff._conflict_lines（n=40）---------------------------------
p8_lines = handoff._conflict_lines({"done": True, "applied": APPLIED_CONFLICT})
p8_line = next((line for line in p8_lines if "ZZZ" in line), "")
check("② P8 handoff.py:161（_conflict_lines, n=40）两侧可辨",
      "ZZZ" in p8_line and "QQQ" in p8_line, p8_line[:160])

# --- P9 metadata_source.apply_word_supplements（n=50）------------------
FAKE_DOC = types.SimpleNamespace(
    author=OLD, author_en="", affiliation_cn="", affiliation_en="", email="",
    abstract_cn="", abstract_en="", keywords_cn="", keywords_en="",
    title_cn="原题", title_en="", subtitle="", subtitle_en="")
_FAKE_EXTRACTED = metadata_source.Extracted(path="/tmp/v20/fake.docx", detail="测试")
_FAKE_EXTRACTED.fields = {"author_cn": NEW}
_orig_extract = metadata_source.extract
metadata_source.extract = lambda path, **kw: _FAKE_EXTRACTED
try:
    p9_used, p9_conflicts = metadata_source.apply_word_supplements(
        types.SimpleNamespace(**vars(FAKE_DOC)), ["/tmp/v20/fake.docx"])
finally:
    metadata_source.extract = _orig_extract
p9_line = next((line for line in p9_conflicts if "第一作者" in line), "")
check("② P9 metadata_source.py:415-416（apply_word_supplements, n=50）两侧可辨",
      "ZZZ" in p9_line and "QQQ" in p9_line, p9_line[:200])

# --- P10 bibdb_import.cmd_rehash（n=12）--------------------------------
# 合成库：把库存 sha 改写成「真实 sha 的前 12 位 + 全 0 尾巴」→ 旧值/新值前 12 位
# 相同，旧 `_clip_sha` 会把两侧印成同一串（这就是第 10 处缺陷）。
_tmp_holder = tempfile.TemporaryDirectory(prefix="v20_pair_")
_tmp = _tmp_holder.name
_db = os.path.join(_tmp, "bibliography.jsonl")
_store = bibdb.Store.load(_db, create=True)
_store.import_bib_text(
    "@article{Same12A, author={Alpha, Ann}, title={T}, year={2001}}\n"
    "@article{Same12B, author={Beta, Bob}, title={U}, year={2002}}\n",
    {"kind": bibdb.KIND_PUBLISHED, "issue": "2026年第2期(87)",
     "slug": "01 Test_Author", "file": "reference.bib"},
    verified=True)
_store.save()
_stale_pairs = []
for _record in _store.records():
    _actual = bibdb.content_sha256(str(_record.get("entry_type") or ""),
                                   _record.get("fields") or {})
    _record["content_sha256"] = _actual[:12] + "0" * 52
    _stale_pairs.append((_record["content_sha256"], _actual))
_store.dirty = True
_store.save()

_p10_buf = io.StringIO()
with contextlib.redirect_stdout(_p10_buf):
    _p10_rc = bibdb_import.cmd_rehash(
        types.SimpleNamespace(db=_db, json=False, apply=False))
p10_out = _p10_buf.getvalue()
p10_lines = [line for line in p10_out.splitlines() if line.strip().startswith("✗")]
check("② P10 bibdb_import.py:386（cmd_rehash, n=12）退出 0 且报出两条 stale",
      _p10_rc == 0 and len(p10_lines) == 2, p10_out.strip().replace("\n", " | ")[:200])
check("② P10 前提成立：旧值/新值前 12 位相同 → 旧 `_clip_sha` 两侧印成同一串",
      len(_stale_pairs) == 2
      and all(old_sha[:12] == actual[:12] and old_sha != actual
              for old_sha, actual in _stale_pairs)
      and all(old_clip_sha(old_sha) == old_clip_sha(actual)
              for old_sha, actual in _stale_pairs),
      " / ".join(f"{old_clip_sha(o)} == {old_clip_sha(a)}"
                 for o, a in _stale_pairs))
check("② P10 两侧现在可辨（差异在尾部，pair_summary 保住了）",
      len(p10_lines) == 2
      and all("→" in line for line in p10_lines)
      and all(line.split("→")[0].strip() != line.split("→")[1].strip()
              for line in p10_lines),
      " / ".join(line.strip()[:70] for line in p10_lines))
_tmp_holder.cleanup()


# ─────────────────────────────────────────────────────────────────────────
# ③ 短值完整可读：不加省略号
# ─────────────────────────────────────────────────────────────────────────
print("\n── ③ 短值完整可读 ──")

for _n in (12, 40, 50, 60):
    check(f"③ pair_summary(n={_n})：短值原样返回、不带任何省略号",
          pair_summary(SHORT_OLD, SHORT_NEW, _n) == (SHORT_OLD, SHORT_NEW),
          repr(pair_summary(SHORT_OLD, SHORT_NEW, _n)))

_md_short_author = bibdb_reports.build_author_report_md(
    "/tmp/v20/bibliography.jsonl",
    {"records": 1, "with_author": 1, "changes": 1, "skips": 0, "reasons": {},
     "skip_rows": [],
     "change_rows": [{"key": "k1", "old": SHORT_OLD, "new": SHORT_NEW,
                      "sources": []}]},
    "a", "b", applied=False)
_short_line = _line_with(_md_short_author, SHORT_OLD)
check("③ 作者报告（P3）：短值「张三」/「李四」完整可读、不加省略号",
      SHORT_OLD in _short_line and SHORT_NEW in _short_line
      and "…" not in _short_line and "⋯" not in _short_line,
      _short_line[:160])

_short_editor = bibdb_reports._editor_table_row(
    1, dict(EDITOR_ROW, old=SHORT_OLD, new=SHORT_NEW))
check("③ 编辑器明细行（P4）：短值完整可读、不加省略号",
      SHORT_OLD in _short_editor and SHORT_NEW in _short_editor
      and "…" not in _short_editor and "⋯" not in _short_editor,
      _short_editor[:200])

_short_apply = bibdb_reports.build_normalize_apply_md(
    "/tmp/v20/bibliography.jsonl", {"records": 1},
    [{"key": "k1", "entry_type": "article", "semantic": True, "rules": ["r"],
      "diff": {"title": {"old": SHORT_OLD, "new": SHORT_NEW}}}],
    [], VERIFICATION, "a", "b", "", "")
_short_apply_line = _line_with(_short_apply, SHORT_OLD)
check("③ normalize-apply（P1）：短值完整可读、不加省略号",
      SHORT_OLD in _short_apply_line and SHORT_NEW in _short_apply_line
      and "…" not in _short_apply_line and "⋯" not in _short_apply_line,
      _short_apply_line[:160])

_short_conflicts = bibdbapply.format_conflicts(
    [{"key": "k2", "replaced": {"title": SHORT_OLD},
      "filled": {"title": SHORT_NEW}}])
check("③ 回填冲突（P7）：短值完整可读、不加省略号",
      any(SHORT_OLD in line and SHORT_NEW in line and "…" not in line
          and "⋯" not in line for line in _short_conflicts),
      str(_short_conflicts)[:200])

_short_handoff = handoff._conflict_lines(
    {"done": True, "applied": [{"key": "k2", "replaced": {"title": SHORT_OLD},
                                "filled": {"title": SHORT_NEW}}]})
check("③ 交接单冲突（P8）：短值完整可读、不加省略号",
      any(SHORT_OLD in line and SHORT_NEW in line and "…" not in line
          and "⋯" not in line for line in _short_handoff),
      str(_short_handoff)[:200])


# ─────────────────────────────────────────────────────────────────────────
# ④ Markdown 转义保留
# ─────────────────────────────────────────────────────────────────────────
print("\n── ④ Markdown 转义保留 ──")

# `_md_escape` 点：管道符必须转义成 `\|`，表格列数不能因值里的 `|` 变多。
PIPEY_OLD = "a|b" * 20 + "ZZZ"
PIPEY_NEW = "a|b" * 20 + "QQQ"
_pipe_author = bibdb_reports.build_author_report_md(
    "/tmp/v20/bibliography.jsonl",
    {"records": 1, "with_author": 1, "changes": 1, "skips": 0, "reasons": {},
     "skip_rows": [],
     "change_rows": [{"key": "k1", "old": PIPEY_OLD, "new": PIPEY_NEW,
                      "sources": []}]},
    "a", "b", applied=False)
_pipe_author_line = _line_with(_pipe_author, "ZZZ")
check("④ 作者报告（P3，_md_escape 点）：值里的 `|` 仍转义为 `\\|`",
      "\\|" in _pipe_author_line and PIPEY_OLD not in _pipe_author_line,
      _pipe_author_line[:200])
check("④ 作者报告（P3）：转义后表格列数未变（仍是 5 个未转义 `|`）",
      _unescaped_pipes(_pipe_author_line) == 5,
      f"{_unescaped_pipes(_pipe_author_line)} 个")

_pipe_editor = bibdb_reports._editor_table_row(
    1, dict(EDITOR_ROW, old=PIPEY_OLD, new=PIPEY_NEW))
check("④ 编辑器明细行（P4，_md_escape 点）：`|` 仍转义、列数未变（11 个未转义 `|`）",
      "\\|" in _pipe_editor and _unescaped_pipes(_pipe_editor) == 11,
      f"{_unescaped_pipes(_pipe_editor)} 个 / {_pipe_editor[:120]}")

# `*` / `_` 走 `_md_code` → 反引号代码跨度内，不破坏强调。
EMPH_OLD = "x*y_z" * 12 + "ZZZ"
EMPH_NEW = "x*y_z" * 12 + "QQQ"
_emph_md = bibdb_reports.build_normalize_apply_md(
    "/tmp/v20/bibliography.jsonl", {"records": 1},
    [{"key": "k1", "entry_type": "article", "semantic": True, "rules": ["r"],
      "diff": {"title": {"old": EMPH_OLD, "new": EMPH_NEW}}}],
    [], VERIFICATION, "a", "b", "", "")
_emph_line = _line_with(_emph_md, "ZZZ")
check("④ normalize-apply（P1，_md_code 点）：`*`/`_` 落在反引号代码跨度内",
      "`" in _emph_line and "*" in _emph_line and "_" in _emph_line
      and "`⋯" in _emph_line,
      _emph_line[:160])

check("④ pair_summary 自身不加任何转义（转义权仍归调用方）",
      "\\|" not in pair_summary(PIPEY_OLD, PIPEY_NEW, 50)[0]
      and "|" in pair_summary(PIPEY_OLD, PIPEY_NEW, 50)[0],
      repr(pair_summary(PIPEY_OLD, PIPEY_NEW, 50)[0]))


# ─────────────────────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if FAILS:
    print(f"失败 {len(FAILS)} 项：" + "；".join(FAILS))
    sys.exit(1)
print("失败 0 项（全部通过）")
sys.exit(0)
