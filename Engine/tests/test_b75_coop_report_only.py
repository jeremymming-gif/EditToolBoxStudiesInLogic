#!/usr/bin/env python3
r"""批75（B-132 + B-139 + B-140①）：协同链 **report-only 收尾** 判据测试。

全部离线、hermetic（tempfile/合成稿；不读真实库/真稿、不联网、不调付费 API、不跑 TeX）：
  · **B-132 附一 note-only**：`verdict=unresolved` 且无 `span` 时不再整条拒绝——收下并
    保留 `note`、进「待人工」；`corrected` 仍强制 span/field/value + 白名单（不放宽）；
    note-only 的 note 必填/≤300 字符，field 仍按白名单校验；
  · **B-132 清单缺条节**：`_AI协同记录.md` 新增可见「清单缺条」节（数据只用现有 C 类
    `FN-*` 低置信项，不新写解析器）；无该类项时记录逐字节不变；
  · **B-139 附四输入补齐**：`--coop-fulltext` 时任务书在附三之后/附四之前追加「脚注清单
    （fid+文本摘要）」/「占位符清单（类型+位置）」两块（只在有数据时出现）；
  · **B-140① evidence 收紧**：附四 evidence 基准 = 任务书里**附四之前**的内容（附三正文 +
    附一原文 + 附二/附三说明），抄附四 schema 示例/指令当 evidence → 整条拒绝；
  · **默认零变化**：无 `--coop-fulltext` 时任务书/产物/日志与 HEAD（`git archive HEAD`）
    逐字节相同；有 flag 时任务书相对 HEAD 只多两块输入清单 + 附四 evidence 说明一行
    （**提交前 HEAD=批74 旧代码时**；批75 提交后 HEAD=工作树 → 任务书应逐字节相同，
    两条分支由 `head_is_worktree()` 自判，提交前后都自洽）。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_b75_coop_report_only.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import coop  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print(f"SKIP  {name}  {why}")


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()



# ── B-276（标准 LaTeX 前言自动识别）───────────────────────────────────────
# 合成稿 `\title{测试题名}` + `\author{张三}` 没有 \my* 模板宏，新引擎会从标准
# LaTeX 前言回填 mytitle/myfirstauthor 等字段——「无 flag 默认零变化」的对照
# 断言改为「除 B-276 前言回填字段外逐字节相同」，回填取值另有显式断言（见文件末尾）。

_B276_FIELDS = (
    "mytitle", "myrunningtitle", "myfirstauthor", "myfirstaffiliation",
    "myfirstemail", "myabstract", "mykeywords", "mygrants", "mythanks",
)
_B276_LABELS = frozenset((
    "中文题名", "页眉标题", "第一作者", "第一作者单位", "第一作者邮箱",
    "中文摘要", "关键词", "基金", "致谢",
))
_B276_META_RE = re.compile(
    r"### `META-LABEL-\d+` — 元数据缺失\n\n"
    r"- 原文片段：`([^`]+)`\n"
    r"- 需要确认：[^\n]*\n"
    r"- 允许的 field：`[^`]*`\n"
    r"\n{0,2}")


def _b276_mask(text):
    """把 B-276 会回填的字段值/条目归一（其余内容原样，便于逐字节对照）。"""
    if isinstance(text, dict):
        return {k: _b276_mask(v) for k, v in text.items()}
    if not text:
        return text
    # ① 产物 .tex：my* 模板宏取值 → 统一占位（模板值在下一行 `{…}`）
    for name in _B276_FIELDS:
        text = re.sub(
            r"(?m)^([ \t]*)%\s*(\\(?:newcommand|renewcommand|providecommand)\{\\"
            + name + r"\})", r"\1\2", text)
        text = re.sub(
            r"(\\(?:newcommand|renewcommand|providecommand)\{\\" + name
            + r"\})[^\n]*\n\{[^\n]*\}",
            r"\1{<B276 前言回填>}", text)
        text = re.sub(
            r"(\\(?:newcommand|renewcommand|providecommand)\{\\" + name
            + r"\})\{[^\n]*?\}",
            r"\1{<B276 前言回填>}", text)
    # ② 任务书：删掉「B-276 可回填标签」的元数据缺失块，标号与条数归一
    text = _B276_META_RE.sub(
        lambda m: "" if m.group(1) in _B276_LABELS else m.group(0), text)
    text = re.sub(r"### `META-LABEL-\d+`", "### `META-LABEL-N`", text)
    text = re.sub(r"共 \*\*\d+\*\* 项", "共 **N** 项", text)
    # ③ 日志：前言识别行归一；未识别字段清单去掉 B-276 标签、计数归一；
    #    初排低置信项计数归一（其余行原样）
    text = re.sub(r"^[ \t]*标准 LaTeX 前言识别 \d+ 个字段：[^\n]*\n?",
                  "", text, flags=re.M)
    out_lines = []
    for line in text.splitlines(keepends=True):
        if ("未识别" in line and "个字段" in line) or "以下字段未能从源稿解析" in line:
            m = re.search(r"：([^：\n]+)(\n?)$", line)
            if m:
                items = [x for x in m.group(1).split("、")
                         if x.strip() not in _B276_LABELS]
                line = line[:m.start(1)] + "、".join(items) + m.group(2)
            line = re.sub(r"未识别 \d+ 个字段", "未识别 N 个字段", line)
        out_lines.append(line)
    text = "".join(out_lines)
    text = re.sub(r"(含「初排低置信项」)\d+( 项)", r"\1N\2", text)
    # ⑤ token 预算/低置信计数随前言回填字段数浮动：归一化
    text = re.sub(r"(预计 \d+ 次调用 / )\d+( tokens)", r"\1N\2", text)
    text = re.sub(r"prompt \d+ \+ completion \d+ = \d+ tokens",
                  "prompt N + completion N = N tokens", text)
    text = re.sub(r"(\d+) 条低置信 ID", r"N 条低置信 ID", text)
    text = re.sub(r"tokens \d+\+\d+", "tokens N+N", text)
    # ④ 任务书「占位符清单」表格：删掉 B-276 可回填标签的行
    kept = []
    for line in text.splitlines(keepends=True):
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 3 and cells[1] in _B276_LABELS:
            continue
        kept.append(line)
    text = "".join(kept)
    return text


def _b276_prod_text(folder: str) -> str:
    """产物目录里第一份带 my* 模板宏定义的 .tex 文本（其余 .tex 返回空）。"""
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if name.endswith(".tex"):
                t = read(os.path.join(root, name))
                if "\\newcommand{\\mytitle}" in t:
                    return t
    return ""


def _b276_assert_filled(check, stdout: str, prod: str) -> None:
    """显式断言：B-276 前言回填了哪些字段、取值是什么（不靠 mask 过滤充数）。"""
    _m = re.search(r"标准 LaTeX 前言识别 (\d+) 个字段：([^\n]*)", stdout)
    check("B-276-1 日志报告前言识别字段：mytitle、myfirstauthor（可含后续字段）",
          _m is not None and _m.group(2).startswith("mytitle、myfirstauthor"),
          stdout[-400:])
    check("B-276-2 产物回填取值：mytitle=测试题名、myfirstauthor=张三",
          re.search(r"\\newcommand\{\\mytitle\}[^\n]*\n\s*\{测试题名\}", prod) is not None
          and re.search(r"\\newcommand\{\\myfirstauthor\}[^\n]*\n\s*\{张三\}", prod)
          is not None,
          prod[:400])
    check("B-276-3 未识别清单仍保留其余字段、不再含中文题名",
          "页眉标题" in stdout and "中文题名" not in stdout,
          stdout[-400:])

def snapshot(folder: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            out[os.path.relpath(p, folder)] = hashlib.sha256(_b276_mask(read(p)).encode("utf-8")).hexdigest()
    return out


def make_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES")):
            env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    return env


ENV = make_env()

_TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
开头一段正文。这里引用\parencite{Quine1970}，并带一个脚注。\footnote{脚注说明文字。}
文末清单里有一条未被引用的条目。
\printbibliography
\end{document}
"""

_BIB = """@article{Quine1970,
  author = {Quine, W. V. O.},
  title = {Philosophy of Logic},
  journal = {Mind},
  year = {1970},
}
@article{Other2020,
  author = {Other, Olivia},
  title = {An Unrelated Work on Logic},
  publisher = {Some Press},
  year = {2020},
}
"""

_STEM = "San_Zhang"


def _run(args: list[str], out: str | None = None,
         engine: str = _ENGINE) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(engine, "convert.py"), *args]
    if out:
        cmd += ["--out", out]
    return subprocess.run(cmd, cwd=_ENGINE, env=ENV, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=240)


def _fixture(tmp: str, tag: str) -> tuple[str, str, str]:
    srcdir = os.path.join(tmp, tag, "src")
    source = write(os.path.join(srcdir, "main.tex"), _TEX)
    bibp = write(os.path.join(srcdir, "refs.bib"), _BIB)
    return source, bibp, os.path.join(tmp, tag, "out")


def _ph1(src: str, bibp: str, out: str, extra: list[str] | None = None,
         engine: str = _ENGINE) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(engine, "convert.py"),
         "--source", src, "--bib", bibp, "--out", out, "--author", "张三",
         "--no-original", "--coop-taskbook-only"] + list(extra or []),
        cwd=_ENGINE, env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)


def _ph2(src: str, out: str, reply: str, extra: list[str] | None = None,
         engine: str = _ENGINE) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(engine, "convert.py"),
         "--source", src, "--out", out, "--coop-replies", reply, "--no-original"]
        + list(extra or []),
        cwd=_ENGINE, env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)


def _stage(out: str) -> str:
    return os.path.join(out, _STEM + "_待核")


def _taskbook(out: str) -> str:
    return os.path.join(_stage(out), f"{_STEM}_文献核实.md")


def _final(out: str) -> str:
    return os.path.join(out, _STEM)


def _state(out: str) -> dict:
    p = os.path.join(_stage(out), coop.STATE_NAME)
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else {}


def _restore_stage(out: str, backup: str) -> None:
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    shutil.copytree(backup, _stage(out))


def head_is_worktree(head: str) -> bool:
    """`git archive HEAD` 提取的引擎是否与工作树**同一份代码**（提交后/CI 态 = True）。

    75-1 的 ④-12/④-13/④-15 是「相对旧 HEAD 的增量」断言：开发态（HEAD=批74 旧代码）成立；
    批75 提交后 HEAD 已含本卡代码（CI 就是这样），故这三条按同一份文件判定改判等价的
    「HEAD 已是新口径」断言，保证提交前后都自洽、断言总数不变。
    判据只看本卡改过的两个产品文件（`slcn/coop.py` / `convert.py`），不受其他提交噪声影响。
    """
    for rel in (os.path.join("slcn", "coop.py"), "convert.py"):
        a = os.path.join(head, rel)
        b = os.path.join(_ENGINE, rel)
        if not (os.path.isfile(a) and os.path.isfile(b)) or sha(a) != sha(b):
            return False
    return True


def ensure_head_engine(tmp: str) -> str:
    dest = os.path.join(tmp, "head")
    try:
        tar = subprocess.run(["git", "-C", _REPO, "archive", "HEAD"],
                             capture_output=True, timeout=120)
        if tar.returncode != 0:
            return ""
        os.makedirs(dest, exist_ok=True)
        p = os.path.join(tmp, "head.tar")
        with open(p, "wb") as f:
            f.write(tar.stdout)
        with tarfile.open(p) as tf:
            tf.extractall(dest)
        eng = os.path.join(dest, "Engine")
        return eng if os.path.isfile(os.path.join(eng, "convert.py")) else ""
    except Exception:  # noqa: BLE001
        return ""


# ── ① B-132：附一 `unresolved` note-only（校验 + 建议.md 呈现）─────────────

_LOW_ITEMS = [
    {"id": "FN-0", "kind": "footnote", "evidence": "张三 2020 论逻辑",
     "need": "核对该脚注引用与文献表是否对应", "fields": ["note"], "key": "",
     "title": "脚注 #0（mixed_manual）", "subkind": "mixed_manual",
     "target": ".tex", "macro": "", "current": ""},
    {"id": "BIB-k1", "kind": "bib_entry", "evidence": "@article{k1}",
     "need": "该条目缺必填字段（title）；请只根据原文/来源补", "fields": ["title", "year"],
     "key": "k1", "title": "文献条目 `k1` 缺字段", "subkind": "",
     "target": "reference.bib", "macro": "", "current": ""},
    {"id": "META-myvolnumber", "kind": "metadata", "evidence": "myvolnumber",
     "need": "元数据字段 myvolnumber 缺失", "fields": ["myvolnumber", "value"],
     "key": "", "title": "元数据 `myvolnumber`", "subkind": "",
     "target": ".tex", "macro": "myvolnumber", "current": ""},
]

_NOTE_ONLY = ("源稿脚注 0 引用了 Hacking, Ian. (1999) The Social Construction of What?，"
              "但源稿文末清单里没有这一条——属源稿清单缺条，请人工核对。")


def _parse_low(replies: list[str]):
    return coop.parse_low_confidence(
        [(f"r{i}.md", t) for i, t in enumerate(replies)], _LOW_ITEMS)


def test_note_only_validate(tmp: str) -> None:
    r = _parse_low([json.dumps({"id": "FN-0", "verdict": "unresolved",
                                "note": _NOTE_ONLY}, ensure_ascii=False)])
    sug = (r.suggestions.get("FN-0") or [{}])[0]
    check("①-1 unresolved 无 span + note → 接受（note-only，不再整条拒绝）",
          r.n_ok == 1 and not r.rejected and r.status.get("FN-0") == "ok",
          r.summary() + str(r.rejected))
    check("①-2 note 原样保留", sug.get("note") == _NOTE_ONLY, str(sug.get("note")))
    check("①-3 note-only 记录带标记（普通回复无该键）",
          sug.get("note_only") is True and sug.get("span") == "")

    r_norm = _parse_low([json.dumps({"id": "FN-0", "verdict": "confirmed",
                                     "span": "张三 2020", "note": "复核过"})])
    check("①-4 普通（带 span）回复记录结构不变",
          r_norm.n_ok == 1 and "note_only" not in r_norm.suggestions["FN-0"][0])

    r2 = _parse_low([json.dumps({"id": "BIB-k1", "verdict": "corrected",
                                 "field": "title", "value": "新题名"})])
    check("①-5 corrected 无 span → 仍整条拒绝（不放宽）",
          "BIB-k1" in r2.rejected and "span" in r2.rejected.get("BIB-k1", ""))

    r3 = _parse_low([json.dumps({"id": "FN-0", "verdict": "unresolved"})])
    check("①-6 unresolved 既无 span 也无 note → 拒绝（没有可记录的内容）",
          "FN-0" in r3.rejected and "span" in r3.rejected.get("FN-0", ""))

    r4 = _parse_low([json.dumps({"id": "FN-0", "verdict": "unresolved",
                                 "field": "title", "note": "想补题名"})])
    check("①-7 unresolved note-only 但 field 不在白名单 → 拒绝",
          "FN-0" in r4.rejected and "field" in r4.rejected.get("FN-0", ""))

    r5 = _parse_low([json.dumps({"id": "FN-0", "verdict": "unresolved",
                                 "note": "x" * 301})])
    check("①-8 unresolved note-only 但 note 超长（>300）→ 拒绝",
          "FN-0" in r5.rejected and "300" in r5.rejected.get("FN-0", ""))

    r6 = _parse_low([json.dumps({"id": "FN-0", "verdict": "unresolved",
                                 "span": "张三 2021", "note": "n"})])
    check("①-9 unresolved 带 span 但 span 不是原文子串 → 仍拒绝（校验未放宽）",
          "FN-0" in r6.rejected and "span" in r6.rejected.get("FN-0", ""))

    r7 = _parse_low([json.dumps({"id": "BIB-k1", "verdict": "corrected",
                                 "field": "title", "span": "@article{k1}"})])
    check("①-10 corrected 有 span 但缺 value → 仍拒绝",
          "BIB-k1" in r7.rejected and "value" in r7.rejected.get("BIB-k1", ""))

    # 落盘：建议.md / .jsonl（note-only 进「三、待人工」，不进「一、已接受建议」）
    reply = "\n".join([
        json.dumps({"id": "FN-0", "verdict": "unresolved", "note": _NOTE_ONLY},
                   ensure_ascii=False),
        json.dumps({"id": "META-myvolnumber", "verdict": "confirmed",
                    "span": "myvolnumber", "note": "已核对"}),
    ])
    r8 = _parse_low([reply])
    stage = os.path.join(tmp, "sug")
    md_p, jsonl_p = coop.write_suggestions(stage, _STEM, _LOW_ITEMS, r8)
    md = read(md_p)
    sec1 = md.split("## 一、已接受建议")[1].split("## 二、被拒绝")[0]
    sec3 = md.split("## 三、待人工")[1].split("## 四、")[0] if "## 四、" in md else \
        md.split("## 三、待人工")[1]
    check("①-11 note-only 不进「一、已接受建议」（避免误以为可应用）",
          "`FN-0`" not in sec1 and "`META-myvolnumber`" in sec1, sec1[:300])
    check("①-12 note-only 落到「三、待人工」并保留说明",
          "note-only" in sec3 and "`FN-0`" in sec3 and _NOTE_ONLY in sec3, sec3[:500])
    check("①-13 头部点出 note-only 条数",
          re.search(r"其中 note-only `unresolved`（保留说明、待人工）：\*\*1\*\* 条", md)
          is not None, md[:500])
    lines = [json.loads(ln) for ln in read(jsonl_p).splitlines() if ln.strip()]
    fn = next(rec for rec in lines if rec["id"] == "FN-0")
    check("①-14 jsonl 里 note-only 仍记 status=accepted、带 note/note_only",
          fn.get("status") == "accepted" and fn.get("note_only") is True
          and fn.get("note") == _NOTE_ONLY, str(fn)[:300])


# ── ② B-132：协同记录「清单缺条」可见节 ──────────────────────────────────


def _record(low_items, low_report=None, analysis_report=None) -> str:
    return coop.render_record(
        stem=_STEM, phase="done", parse_report=coop.ParseReport(),
        low_report=low_report if low_report is not None else coop.LowConfReport(),
        merge_summary=None, apply_summary=None, final_dir=os.path.join("/tmp", "final"),
        no_ai_merge=True, compiled="未请求 --compile",
        analysis_report=analysis_report, low_items=low_items)


def test_record_missing_entry() -> None:
    rec = _record(_LOW_ITEMS)
    check("②-1 有 C 类脚注项 → 记录出现「清单缺条」节",
          "清单缺条（脚注引用未匹配到文末清单；report-only）" in rec)
    check("②-2 节内列出 FN id / 脚注 fid / 原文摘要",
          "`FN-0`" in rec and "脚注 #0" in rec and "张三 2020 论逻辑" in rec)
    no = _record([it for it in _LOW_ITEMS if not str(it["id"]).startswith("FN-")])
    check("②-3 无 C 类脚注项 → 不出现该节（记录与旧版一致）",
          "清单缺条" not in no)
    default = coop.render_record(
        stem=_STEM, phase="done", parse_report=coop.ParseReport(),
        low_report=coop.LowConfReport(), merge_summary=None, apply_summary=None,
        final_dir=os.path.join("/tmp", "final"), no_ai_merge=True,
        compiled="未请求 --compile")
    check("②-4 low_items 缺省（旧调用方）→ 记录无该节、且与空表逐字节相同",
          default == no and "清单缺条" not in default)

    r = _parse_low([json.dumps({"id": "FN-0", "verdict": "unresolved",
                                "note": _NOTE_ONLY}, ensure_ascii=False)])
    rec_note = _record(_LOW_ITEMS, low_report=r)
    check("②-5 note-only 的 note 进「清单缺条」节（note 不丢）",
          "回填说明：" in rec_note and "Hacking, Ian. (1999)" in rec_note)

    rec_ana = _record(_LOW_ITEMS, analysis_report=coop.FulltextAnalysisReport())
    check("②-6 有附四计数节 → 清单缺条节编号为「七」",
          "## 六、全文分析（附四，report-only）" in rec_ana
          and "## 七、清单缺条（脚注引用未匹配到文末清单；report-only）" in rec_ana,
          rec_ana[-900:])
    check("②-7 无附四节 → 清单缺条节编号为「六」",
          "## 六、清单缺条（脚注引用未匹配到文末清单；report-only）" in rec)
    mixed = [dict(it, subkind="citation_unmatched") if it["id"] == "FN-0" else it
             for it in _LOW_ITEMS]
    check("②-8 citation_unmatched 也列入（C 类两类子类）",
          "`FN-0`" in _record(mixed) and "citation_unmatched" in _record(mixed))


# ── ③ B-139：附四输入清单（收集 / 渲染）──────────────────────────────────

_WARN_FN = ("混合型脚注 2 条（说明文字+引用，已保留脚注）：请人工核对引用与说明文字"
            "是否对应——#0「张三 2020 论逻辑」；#3「李四 2019 另一本书…」")


def test_inputs_unit() -> None:
    fn, ph = coop.collect_analysis_inputs(
        warnings=[_WARN_FN],
        low_items=_LOW_ITEMS,
        placeholders=[("第一作者", "myfirstauthor"), ("第一作者", "myfirstauthor"),
                      ("英文摘要", "myabstractEN"), ("空宏", "")])
    check("③-1 脚注清单：从 warning 的 `#fid「片段」` 收集、按 fid 升序",
          [x["fid"] for x in fn] == [0, 3] and fn[0]["id"] == "FN-0"
          and "张三 2020" in fn[0]["text"], str(fn))
    check("③-2 占位符清单：去重（类型,宏）对、允许空宏",
          len(ph) == 3 and ph[0] == {"label": "第一作者", "macro": "myfirstauthor"}
          and any(p_["macro"] == "" for p_ in ph), str(ph))
    fn2, _ph2 = coop.collect_analysis_inputs(
        low_items=[{"id": "FN-7", "kind": "footnote", "subkind": "mixed_manual",
                    "evidence": "王五 2018"}])
    check("③-3 没有 warning 时从 FN-<fid> 低置信项兜底",
          [x["fid"] for x in fn2] == [7] and fn2[0]["text"] == "王五 2018", str(fn2))
    check("③-4 无数据 → 渲染空串（调用方不追加任何内容）",
          coop.render_analysis_inputs_section() == ""
          and coop.render_analysis_inputs_section(footnotes=[], placeholders=[]) == "")
    sec = coop.render_analysis_inputs_section(footnotes=fn, placeholders=ph)
    check("③-5 两块齐全：标题/表头/fid/宏名（宏名带反斜杠）",
          sec.startswith("\n---\n\n" + coop.ANALYSIS_INPUTS_TITLE)
          and coop.ANALYSIS_FOOTNOTE_TITLE in sec
          and coop.ANALYSIS_PLACEHOLDER_TITLE in sec
          and "| `0` |" in sec and "`\\myfirstauthor`" in sec, sec[:400])
    only_fn = coop.render_analysis_inputs_section(footnotes=fn)
    check("③-6 只有脚注数据 → 不出现占位符块（反之亦然）",
          coop.ANALYSIS_FOOTNOTE_TITLE in only_fn
          and coop.ANALYSIS_PLACEHOLDER_TITLE not in only_fn)
    only_ph = coop.render_analysis_inputs_section(placeholders=ph)
    check("③-7 只有占位符数据 → 不出现脚注块",
          coop.ANALYSIS_PLACEHOLDER_TITLE in only_ph
          and coop.ANALYSIS_FOOTNOTE_TITLE not in only_ph)
    check("③-8 文本摘要压一行、表格竖线转义",
          "|" not in "".join(x["text"] for x in fn)
          and "\\|" in coop.render_analysis_inputs_section(
              footnotes=[{"fid": 1, "id": "FN-1", "text": "a|b"}]))
    check("③-9 渲染确定性（同输入同字符串）", sec ==
          coop.render_analysis_inputs_section(footnotes=fn, placeholders=ph))


# ── ④ CLI ph1：输入清单位置 / 只在有数据时出现 / 默认零变化 ───────────────


def _strip_inputs(text: str) -> str:
    """删掉后加的「附四输入清单」整块（用于证明相对 HEAD 只是插入）。"""
    i = text.find(coop.ANALYSIS_INPUTS_TITLE)
    if i < 0:
        return text
    k = text.rfind("\n\n---\n\n", 0, i)          # 插入串起点
    j = text.find("\n\n---\n\n" + coop.ANALYSIS_TITLE, i)
    if k < 0 or j < 0:
        return text
    return text[:k] + text[j:]


_EVID_BULLET_RE = re.compile(
    r"- 每条必须带\*\*逐字 `evidence`\*\*.*?可用 `note` 代替。", re.S)


def test_cli_ph1(tmp: str, head: str) -> None:
    src, bibp, out = _fixture(tmp, "ph1plain")
    p0 = _ph1(src, bibp, out)
    tb_plain = read(_taskbook(out))
    snap_plain = {k: v for k, v in snapshot(_stage(out)).items()
                  if k != coop.STATE_NAME}
    shutil.rmtree(out, ignore_errors=True)
    p1 = _ph1(src, bibp, out, ["--coop-fulltext", "full"])
    tb = read(_taskbook(out))
    st = _state(out)
    check("④-1 ph1（--coop-fulltext）退出 0", p1.returncode == 0, p1.stderr[-300:])
    order = [coop.SECTION_TITLE, coop.SECTION_BACKFILL_TITLE,
             coop.FULLTEXT_TITLE, coop.ANALYSIS_INPUTS_TITLE, coop.ANALYSIS_TITLE]
    check("④-2 顺序 附一 < 附二 < 附三 < 附四输入清单 < 附四",
          all(tb.find(a) < tb.find(b) for a, b in zip(order, order[1:]))
          and all(tb.find(x) > 0 for x in order))
    check("④-3 占位符清单出现（合成稿有未识别字段）且带类型+宏名",
          coop.ANALYSIS_PLACEHOLDER_TITLE in tb and "第一作者单位" in tb
          and "`\\myfirstaffiliation`" in tb)
    check("④-4 无 C 类脚注数据 → 脚注清单块不出现（只在有数据时）",
          coop.ANALYSIS_FOOTNOTE_TITLE not in tb)
    check("④-5 附四仍是任务书最后一节",
          tb.rstrip().endswith("你的建议只供编辑人工裁决。"))
    m = re.search(r"任务书附四输入清单：脚注 (\d+) 条 / 占位符 (\d+) 个"
                  r"（\+(\d+) 字符）", p1.stdout)
    check("④-6 日志报告输入清单增量（条数/字符）",
          m is not None and int(m.group(1)) == 0 and int(m.group(2)) > 0
          and int(m.group(3)) > 0, p1.stdout[-600:])
    check("④-7 任务书确有增量（chars 增加）", len(tb) - len(tb_plain) > 0,
          f"{len(tb_plain)} -> {len(tb)}")
    prods = {"San_Zhang.tex", "reference.bib", "SLCN-MIX.sty"}
    snap_flag = {k: v for k, v in snapshot(_stage(out)).items() if k in prods}
    check("④-8 输入清单只进任务书，不改编排产物",
          snap_flag == {k: v for k, v in snap_plain.items() if k in prods})
    check("④-9 state.fulltext 四键不变、analysis 不变",
          set(st.get("fulltext") or {}) == {"mode", "chars", "truncated", "tokens_est"}
          and st.get("analysis", {}).get("enabled") is True)

    # 与 HEAD 逐字节对照
    shutil.rmtree(out, ignore_errors=True)
    old = _ph1(src, bibp, out, engine=head)
    tb_head_plain = read(_taskbook(out))
    snap_head_plain = {k: v for k, v in snapshot(_stage(out)).items()
                       if k != coop.STATE_NAME}
    check("④-10 无 flag：任务书与 HEAD 逐字节相同", old.returncode == 0
          and _b276_mask(tb_plain) == _b276_mask(tb_head_plain))
    check("④-11 无 flag：产物树与 HEAD 逐字节相同", snap_plain == snap_head_plain)
    shutil.rmtree(out, ignore_errors=True)
    oldf = _ph1(src, bibp, out, ["--coop-fulltext", "full"], engine=head)
    tb_head = read(_taskbook(out))
    if head_is_worktree(head):
        # 提交后/CI 态：HEAD 已含 75-1 代码 → 等价断言（HEAD 也是新口径，任务书应逐字节相同）
        check("④-12 HEAD 已含输入清单（HEAD=工作树；提交后/CI 态）",
              oldf.returncode == 0 and coop.ANALYSIS_INPUTS_TITLE in tb_head)
        check("④-13 提交后：有 flag 的新任务书与 HEAD 逐字节相同", _b276_mask(tb) == _b276_mask(tb_head))
        head_bullet = _EVID_BULLET_RE.search(tb_head)
        new_bullet = _EVID_BULLET_RE.search(tb)
        check("④-15 evidence 说明为新口径且与 HEAD 一致（均含「附四之前」）",
              head_bullet is not None and new_bullet is not None
              and "附四之前" in new_bullet.group(0)
              and new_bullet.group(0) == head_bullet.group(0))
    else:
        # 开发态（HEAD=批74 旧代码）：HEAD 无输入清单、旧 evidence 说明 → 增量断言
        check("④-12 HEAD 的 fulltext 任务书没有输入清单",
              oldf.returncode == 0 and coop.ANALYSIS_INPUTS_TITLE not in tb_head)
        stripped = _strip_inputs(tb)
        a_new, a_head = stripped.split("\n"), tb_head.split("\n")
        p = 0
        while p < min(len(a_new), len(a_head)) and a_new[p] == a_head[p]:
            p += 1
        suf = 0
        while (suf < min(len(a_new), len(a_head)) - p
               and a_new[-1 - suf] == a_head[-1 - suf]):
            suf += 1
        new_mid = a_new[p:len(a_new) - suf]
        head_mid = a_head[p:len(a_head) - suf]
        old_joined, new_joined = "\n".join(head_mid), "\n".join(new_mid)
        old_bullet = _EVID_BULLET_RE.search(old_joined)
        new_bullet = _EVID_BULLET_RE.search(new_joined)
        head_rest = _EVID_BULLET_RE.sub("", old_joined).strip() if old_bullet else "?"
        new_rest = _EVID_BULLET_RE.sub("", new_joined).strip() if new_bullet else "?"
        check("④-13 删掉输入清单后，相对 HEAD 只剩附四 evidence 说明一处差异",
              coop.ANALYSIS_INPUTS_TITLE not in stripped
              and old_bullet is not None and new_bullet is not None
              and head_rest == "" and new_rest == "", old_joined[:300])
        check("④-15 evidence 说明已改为「附四之前的内容」（B-140① 口径）",
              new_bullet is not None and "附四之前" in new_bullet.group(0)
              and "附四之前" not in (old_bullet.group(0) if old_bullet else ""))


# ── ⑤ CLI ph2：note-only 落地 / evidence 收紧 / report-only / 幂等 ────────

_SCHEMA_EVIDENCE = "裸编号 [12] 未转引用；对应文末清单第 12 条"   # 附四 schema 示例里的文本
_INSTR_EVIDENCE = "正文/脚注引用覆盖与漏链"                      # 附四七项指令里的文本


def _reply_plain() -> str:
    return json.dumps({"id": "META-myvolnumber", "verdict": "confirmed",
                       "span": "myvolnumber", "note": "已核对"})


_META_NOTE = "卷号源稿未给出，建议人工按版权页核对。"


def _reply_combo() -> str:
    objs = [
        {"id": "META-myvolnumber", "verdict": "unresolved", "note": _META_NOTE},
        {"schema": coop.ANALYSIS_SCHEMA, "id": "AN-0001", "kind": "cite_missing_entry",
         "target": "report", "action": "report", "confidence": "high",
         "evidence": "文末清单里有一条未被引用的条目", "note": "清单缺条场景"},
        {"schema": coop.ANALYSIS_SCHEMA, "id": "AN-0002", "kind": "style_fix",
         "target": "report", "action": "report", "confidence": "high",
         "evidence": _SCHEMA_EVIDENCE, "note": "抄 schema 示例，应拒绝"},
        {"schema": coop.ANALYSIS_SCHEMA, "id": "AN-0003", "kind": "lowconf_review",
         "target": "report", "action": "report", "confidence": "medium",
         "evidence": _INSTR_EVIDENCE, "note": "抄指令示例，应拒绝"},
    ]
    return "\n".join(json.dumps(o, ensure_ascii=False) for o in objs)


def test_cli_ph2(tmp: str, head: str) -> None:
    src, bibp, out = _fixture(tmp, "ph2")
    p1 = _ph1(src, bibp, out, ["--coop-fulltext", "full"])
    check("⑤-0 ph1 退出 0", p1.returncode == 0, p1.stderr[-300:])
    cp = os.path.join(tmp, "ph2", "stage.bak")
    shutil.copytree(_stage(out), cp)
    reply_plain = write(os.path.join(tmp, "ph2", "plain.json"), _reply_plain())
    r0 = _ph2(src, out, reply_plain)
    rec_plain = read(os.path.join(_final(out), coop.RECORD_NAME))
    prod_plain = {k: v for k, v in snapshot(_final(out)).items()
                  if k not in (coop.RECORD_NAME,)}
    check("⑤-1 无附四对象的 ph2 退出 0", r0.returncode == 0, r0.stderr[-300:])
    check("⑤-2 无附四对象 → 记录无「六/七」节，也无「清单缺条」",
          "## 六、" not in rec_plain and "清单缺条" not in rec_plain)
    _restore_stage(out, cp)
    reply_c = write(os.path.join(tmp, "ph2", "combo.json"), _reply_combo())
    r1 = _ph2(src, out, reply_c)
    md = read(os.path.join(_final(out), f"{_STEM}_AI全文分析.md"))
    sug_md = read(os.path.join(_final(out), f"{_STEM}_AI建议.md"))
    rec = read(os.path.join(_final(out), coop.RECORD_NAME))
    check("⑤-3 合成回复 ph2 退出 0", r1.returncode == 0, (r1.stderr + r1.stdout)[-500:])
    check("⑤-4 附一 note-only：接受并保留 note（同一条也进「三、待人工」）",
          "其中 note-only 1 条" in r1.stdout and _META_NOTE in sug_md
          and "note-only" in sug_md, sug_md[-1200:])
    check("⑤-5 附四：1 条接受 / 2 条拒绝（抄 schema 示例与指令文本都被拒）",
          "- 已接受：**1** 条" in md and "- 被拒绝：**2** 条" in md
          and "编造" in md and md.count("附四之前内容") >= 2, md[:900])
    check("⑤-6 正确 evidence（附三正文）仍通过",
          "AN-0001" in md and "文末清单里有一条未被引用的条目" in md)
    check("⑤-7 记录有「六」计数节、无「清单缺条」节（本稿无 C 类脚注项）",
          "## 六、全文分析（附四，report-only）" in rec
          and "清单缺条" not in rec)
    _reports = {coop.RECORD_NAME, f"{_STEM}_AI建议.md", f"{_STEM}_AI建议.jsonl",
                f"{_STEM}_AI全文分析.md", f"{_STEM}_AI全文分析.jsonl",
                f"{_STEM}_文献核实.md"}
    _skip_tb = {"SLCN-MIX.sty"}
    combo_products = {k: v for k, v in snapshot(_final(out)).items()
                      if k not in _reports}
    base_products = {k: v for k, v in prod_plain.items() if k not in _reports}
    check("⑤-8 report-only：.tex/.bib/参考文献核对 与基线逐字节相同、无 .ai前",
          {k: v for k, v in combo_products.items() if k not in _skip_tb}
          == {k: v for k, v in base_products.items() if k not in _skip_tb}
          and not [k for k in snapshot(_final(out)) if k.endswith(".ai前")],
          str({k for k in set(combo_products) | set(base_products)
               if combo_products.get(k) != base_products.get(k)}))
    # B-211：交付成功后暂存目录（含状态）已清理 → 重跑必须明确拒绝，交付产物不动。
    _final_before = snapshot(_final(out))
    r2 = _ph2(src, out, reply_c)
    check("⑤-9 交付后重跑：明确拒绝（退出 2、指向暂存目录/第一段）且交付产物不变",
          r2.returncode == 2
          and ("找不到可续跑的协同初排暂存目录" in r2.stderr
               or "请先运行第一段" in r2.stderr)
          and snapshot(_final(out)) == _final_before,
          (r2.stderr + r2.stdout)[-500:])

    # ⑤-10/11：B-140① 新旧基准对照（旧口径=整份任务书，新口径=附四之前）
    # B-211：暂存目录已随交付清理；任务书随 promote 进入最终目录，从那里读。
    tb = read(os.path.join(_final(out), f"{_STEM}_文献核实.md"))
    body_ev = "开头一段正文"
    for tag, ev, want in (("schema", _SCHEMA_EVIDENCE, "拒绝"),
                          ("instr", _INSTR_EVIDENCE, "拒绝"),
                          ("body", body_ev, "接受")):
        obj = {"schema": coop.ANALYSIS_SCHEMA, "id": "AN-9", "kind": "style_fix",
               "target": "report", "action": "report", "confidence": "high",
               "evidence": ev}
        texts = [("r.md", json.dumps(obj, ensure_ascii=False))]
        old_r = coop.parse_fulltext_analysis(texts, tb)
        new_r = coop.parse_fulltext_analysis(texts, coop.analysis_evidence_base(tb))
        if want == "拒绝":
            check(f"⑤-10{tag} 抄附四示例当 evidence：旧基准会收、新基准拒绝",
                  len(old_r.accepted) == 1 and len(new_r.rejected) == 1
                  and "编造" in new_r.rejected[0]["reason"], str(new_r.rejected))
        else:
            check("⑤-11 附三正文的 evidence 新旧基准都通过（不误拒）",
                  len(old_r.accepted) == 1 and len(new_r.accepted) == 1)

    # ⑤-12：无 flag ph2 记录与 HEAD 逐字节相同（默认零变化）
    src2, bib2, out2 = _fixture(tmp, "ph2plain")
    _ph1(src2, bib2, out2)
    cp2 = os.path.join(tmp, "ph2plain", "stage.bak")
    shutil.copytree(_stage(out2), cp2)
    reply2 = write(os.path.join(tmp, "ph2plain", "reply.json"), _reply_plain())
    r_new = _ph2(src2, out2, reply2)
    rec_new = read(os.path.join(_final(out2), coop.RECORD_NAME))
    prod_new = snapshot(_final(out2))
    shutil.rmtree(out2, ignore_errors=True)
    os.makedirs(out2, exist_ok=True)
    shutil.copytree(cp2, _stage(out2))
    r_old = _ph2(src2, out2, reply2, engine=head)
    rec_old = read(os.path.join(_final(out2), coop.RECORD_NAME))
    prod_old = snapshot(_final(out2))
    check("⑤-12 无 flag：新 ph2 记录与 HEAD 逐字节相同",
          r_new.returncode == 0 and r_old.returncode == 0 and _b276_mask(rec_new) == _b276_mask(rec_old),
          (r_new.stderr + r_old.stderr)[-300:])
    check("⑤-13 无 flag：ph2 产物树（tex/bib/sty/建议）与 HEAD 逐字节相同",
          _b276_mask(prod_new) == _b276_mask(prod_old),
          str({k for k in set(prod_new) | set(prod_old)
               if prod_new.get(k) != prod_old.get(k)}))


# ── main ────────────────────────────────────────────────────────────────



# ── B-276：标准 LaTeX 前言回填的显式取值断言（合成稿无 \my*）──────────────
def test_b276_frontmatter_fill(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "b276")
    cmd = [sys.executable, os.path.join(_ENGINE, "convert.py"),
           "--source", src]
    if bibp:
        cmd += ["--bib", bibp]
    cmd += ["--out", out, "--author", "张三",
            "--no-original", "--coop-taskbook-only"]
    r = subprocess.run(cmd, cwd=_ENGINE, env=make_env(), capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=240)
    _b276_assert_filled(check, r.stdout, _b276_prod_text(out))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="slcn_b75_coop_")
    try:
        notes = os.path.join(tmp, "notes")
        os.makedirs(os.path.join(notes, "cases"), exist_ok=True)  # 无 bib_db/ → 不查库
        ENV["SLCN_NOTES"] = notes
        test_note_only_validate(tmp)
        test_record_missing_entry()
        test_inputs_unit()
        head = ensure_head_engine(tmp)
        test_b276_frontmatter_fill(tmp)
        if head:
            test_cli_ph1(tmp, head)
            test_cli_ph2(tmp, head)
        else:
            skip("④⑤ CLI 与 HEAD 逐字节对照", "git archive HEAD 失败")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项"
          + (f"，SKIP {len(SKIPS)} 项" if SKIPS else ""))
    for f in FAILS:
        print("  FAIL:", f)
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
