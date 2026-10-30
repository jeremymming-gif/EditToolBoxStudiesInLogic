#!/usr/bin/env python3
r"""批74（B-133/T3）：附四「初排分析」并入《文献核实任务书》判据测试（严格 report-only）。

用户 2026-10-21 晚拍板（全部离线：不联网、不调付费 API、不读稿件库、不跑 TeX）：
  · 附四只在 `--coop-fulltext {context,full}` 且附三写出后出现在任务书末尾；
  · 七项指令 + `slcn.fulltext.v1` schema + 硬约束齐全；不传开关时任务书逐字节不变；
  · 解析：schema 分区（附四对象不进附一 unknown）、11 kind×target/action 白名单、
    未知 kind 记录不丢弃、evidence 逐字子串（防编造）、低置信/`unresolved` 待人工、
    同 id / 同 (kind,target,locator) 冲突拒绝、>30 条 / 每 kind >10 条拒绝；
  · 落盘 `<名>_AI全文分析.md/.jsonl`（四节 + 确定性 jsonl）+ 协同记录「六、全文分析」计数；
  · **report-only 硬边界**：不写 `reference.bib`/`.tex`、不产生 `.ai前`、`--coop-apply` 不碰附四；
  · 重跑幂等（报告是本工具产物，不被「非本工具文件」拦下）；旧 state/旧回复行为不变。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_b74_fulltext_analysis.py
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


def _run(args: list[str], out: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(_ENGINE, "convert.py"), *args]
    if out:
        cmd += ["--out", out]
    return subprocess.run(cmd, cwd=_ENGINE, env=ENV, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=240)


def _fixture(tmp: str, tag: str, body: str | None = None) -> tuple[str, str, str]:
    srcdir = os.path.join(tmp, tag, "src")
    source = write(os.path.join(srcdir, "main.tex"), _TEX if body is None else body)
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


def _md_path(out: str) -> str:
    return os.path.join(_final(out), f"{_STEM}_AI全文分析.md")


def _jsonl_path(out: str) -> str:
    return os.path.join(_final(out), f"{_STEM}_AI全文分析.jsonl")


def _restore_stage(out: str, backup: str) -> None:
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    shutil.copytree(backup, _stage(out))


def _products(final_dir: str) -> dict[str, str]:
    """交付目录里「非报告」的产物（逐字节对照用）。"""
    reports = {f"{_STEM}_AI全文分析.md", f"{_STEM}_AI全文分析.jsonl", coop.RECORD_NAME}
    return {k: v for k, v in snapshot(final_dir).items() if k not in reports}


def _strip_section6(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    skip = False
    for ln in lines:
        if ln.startswith("## 六、全文分析"):
            skip = True
            continue
        if skip:
            if ln == "---":
                skip = False
                out.append(ln)
            continue
        out.append(ln)
    return "\n".join(out)


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


# ── 构造附四 JSON ────────────────────────────────────────────────────────


def _item(**over) -> dict:
    base = {
        "schema": coop.ANALYSIS_SCHEMA,
        "id": "AN-0001",
        "kind": "cite_missing_entry",
        "target": "report",
        "action": "report",
        "confidence": "high",
        "evidence": "开头一段正文",
    }
    base.update(over)
    return base


def _text(*objs: dict) -> list[tuple[str, str]]:
    return [("reply.md", "\n".join(json.dumps(o, ensure_ascii=False) for o in objs))]


_TB_TEXT = (r"开头一段正文。这里引用\parencite{Quine1970}，并带一个脚注。"
            r"\footnote{参见[12]。}" + "\n文末清单：@article{Gilbert2002,")


# ── ① render_analysis_section ────────────────────────────────────────────


def test_render_unit() -> None:
    sec = coop.render_analysis_section()
    check("①-1 含附四标题（与附三同一层级）", coop.ANALYSIS_TITLE in sec
          and sec.startswith("\n---\n\n## 附四"))
    seven = ["正文/脚注引用覆盖与漏链", "脚注→引用转换建议", "参考文献字段/体例补全",
             "元数据补全", "表题/图题/缺引文出处占位符", "CJK 译名 bib key 规范",
             "低置信项复核"]
    check("①-2 七项指令齐全", all(k in sec for k in seven),
          [k for k in seven if k not in sec])
    check("①-3 七项按 1.–7. 顺序编号",
          all(f"{i}. **" in sec for i in range(1, 8)))
    check("①-4 含 schema 名与 JSON 示例",
          coop.ANALYSIS_SCHEMA in sec and "```json" in sec
          and '"kind": "footnote_cite_link"' in sec and '"evidence"' in sec)
    check("①-5 含字段表（schema/id/kind/target/action/evidence/confidence）",
          all(f"| `{k}` |" in sec for k in
              ("schema", "id", "kind", "target", "action", "evidence", "confidence")))
    check("①-6 含 11 类 kind 全表", all(k in sec for k in coop.ANALYSIS_KIND_RULES))
    check("①-7 硬约束：只按附四 JSON / 不得改写正文 / 逐字 evidence",
          "只按上面附四 JSON 格式回复" in sec and "不得改写、复述或补写正文" in sec
          and "逐字 `evidence`" in sec)
    check("①-8 硬约束：拿不准 low/unresolved", "`\"confidence\": \"low\"`" in sec
          and "unresolved" in sec)
    check("①-9 硬约束：上限 ≤30 / 每 kind ≤10",
          f"总条数 ≤{coop.ANALYSIS_MAX_TOTAL}" in sec
          and f"每 kind ≤{coop.ANALYSIS_MAX_PER_KIND}" in sec)
    check("①-10 明写 report-only / 绝不自动写产物",
          "report-only" in sec and "**不会**写进 `reference.bib`" in sec
          and "只供编辑人工裁决" in sec)
    low = coop.render_analysis_section()
    check("①-11 渲染确定性（同输入同字符串）", sec == low)


# ── ② parse_fulltext_analysis：校验分支 ──────────────────────────────────


def _parse(*objs: dict) -> coop.FulltextAnalysisReport:
    return coop.parse_fulltext_analysis(_text(*objs), _TB_TEXT)


def test_parse_accept() -> None:
    r = _parse(_item(id="AN-0001"),
               _item(id="AN-0002", kind="footnote_cite_link", target="tex",
                     action="link_cite", value="\\parencite{Gilbert2002}",
                     field="", locator={"footnote_id": 5}, evidence="参见[12]。"))
    check("②-1 合法高置信条目进「已接受」",
          len(r.accepted) == 2 and len(r.rejected) == 0 and r.total == 2, r.summary())
    check("②-2 规范化记录字段同 schema",
          set(r.accepted[0]) == {"schema", "id", "kind", "target", "action", "field",
                                 "value", "locator", "anchor", "evidence", "confidence",
                                 "source", "note"})
    check("②-3 evidence 保留逐字原文", r.accepted[0]["evidence"] == "开头一段正文")
    check("②-4 summary 计数口径正确",
          r.summary() == "接受 2 条，拒绝 0 条，待人工 0 条，未知 kind 0 个")

    r2 = _parse(_item(kind="lowconf_review", confidence="medium", evidence="",
                      note="附一 FN-1 我拿不准，建议人工核对"))
    check("②-5 kind=lowconf_review + note 允许 note-only（无 evidence）",
          len(r2.accepted) == 1 and len(r2.rejected) == 0, r2.summary())

    r3 = _parse(_item(confidence="low"))
    check("②-6 confidence=low 进「待人工」（不算已接受）",
          len(r3.manual) == 1 and r3.manual[0]["id"] == "AN-0001"
          and len(r3.accepted) == 0, r3.summary())
    r4 = _parse(_item(id="AN-0002", verdict="unresolved", confidence="medium"))
    check("②-7 verdict=unresolved 进「待人工」",
          len(r4.manual) == 1 and r4.manual[0]["id"] == "AN-0002", r4.summary())


def test_parse_reject() -> None:
    r = _parse(_item(kind="bib_field_fill", target="report", action="report",
                     value="x", field="doi"))
    check("②-8 kind 与 target/action 不匹配 → 拒绝",
          len(r.rejected) == 1 and "不匹配" in r.rejected[0]["reason"], str(r.rejected))
    r = _parse(_item(target="docx"))
    check("②-9 target 不在白名单 → 拒绝",
          len(r.rejected) == 1 and "target" in r.rejected[0]["reason"])
    r = _parse(_item(action="auto_fix"))
    check("②-10 action 不在白名单 → 拒绝",
          len(r.rejected) == 1 and "action" in r.rejected[0]["reason"])
    r = _parse(_item(confidence="certain"))
    check("②-11 confidence 缺失/非法 → 拒绝",
          len(r.rejected) == 1 and "confidence" in r.rejected[0]["reason"])
    r = _parse(_item(evidence=""))
    check("②-12 缺 evidence（非 lowconf_review）→ 拒绝",
          len(r.rejected) == 1 and "evidence" in r.rejected[0]["reason"])
    r = _parse(_item(evidence="任务书里根本没有这句话"))
    check("②-13 evidence 编造（不是任务书逐字子串）→ 拒绝",
          len(r.rejected) == 1 and "编造" in r.rejected[0]["reason"])
    r = _parse(_item(value="x" * 1201))
    check("②-14 value >1200 字符 → 拒绝",
          len(r.rejected) == 1 and "1200" in r.rejected[0]["reason"])
    r = _parse(_item(note="n" * 301))
    check("②-15 note >300 字符 → 拒绝",
          len(r.rejected) == 1 and "300" in r.rejected[0]["reason"])
    r = _parse(_item(kind="meta_fill", target="tex", action="fill_field", field="",
                     value="某值"))
    check("②-16 action=fill_field 缺 field → 拒绝",
          len(r.rejected) == 1 and "field" in r.rejected[0]["reason"])
    r = _parse(_item(kind="footnote_cite_link", target="tex", action="link_cite",
                     value=""))
    check("②-17 写入类 action 缺 value → 拒绝",
          len(r.rejected) == 1 and "value" in r.rejected[0]["reason"])
    r = _parse(_item(kind="lowconf_review", evidence="", note=""))
    check("②-18 lowconf_review 但既无 evidence 也无 note → 拒绝",
          len(r.rejected) == 1 and "evidence" in r.rejected[0]["reason"])
    r = _parse(dict(schema=coop.ANALYSIS_SCHEMA, kind="style_fix", target="report",
                    action="report", confidence="high", evidence="开头一段正文"))
    check("②-19 缺 id → 拒绝", len(r.rejected) == 1 and "`id`" in r.rejected[0]["reason"])
    r = _parse({"schema": coop.ANALYSIS_SCHEMA, "id": "AN-0009",
                "target": "report", "action": "report",
                "confidence": "high", "evidence": "开头一段正文"})
    check("②-20 缺 kind → 拒绝", len(r.rejected) == 1 and "`kind`" in r.rejected[0]["reason"])


def test_parse_evidence_whitespace() -> None:
    tb = "正文：参见 [12] 。\n下一行"
    objs = [_item(evidence="正文：参见\n[12]\t。")]
    r = coop.parse_fulltext_analysis([("r.md", json.dumps(objs[0], ensure_ascii=False))], tb)
    check("②-21 evidence 只差空白（规范化后=逐字子串）→ 接受",
          len(r.accepted) == 1, str(r.rejected))
    r2 = coop.parse_fulltext_analysis(
        [("r.md", json.dumps(_item(evidence="正文：参见 [13] 。"), ensure_ascii=False))], tb)
    check("②-22 evidence 数字被改（文字不同）→ 拒绝",
          len(r2.rejected) == 1 and "编造" in r2.rejected[0]["reason"])


def test_parse_unknown_kind() -> None:
    r = _parse(_item(kind="future_new_kind", target="report", action="report",
                     evidence=""))
    check("②-23 未知 kind 进「未知 kind」节、不丢弃",
          len(r.unknown_kinds) == 1 and r.unknown_kinds[0]["kind"] == "future_new_kind"
          and len(r.rejected) == 0 and len(r.accepted) == 0, r.summary())


def test_parse_conflicts_and_limits() -> None:
    r = _parse(_item(id="AN-0001"),
               _item(id="AN-0001", confidence="medium"))
    check("②-24 同 id 重复 → 该 id 全部拒绝",
          len(r.accepted) == 0 and len(r.rejected) == 1
          and "重复 id" in r.rejected[0]["reason"], str(r.rejected))
    r = _parse(_item(id="AN-0001", kind="footnote_cite_link", target="tex",
                     action="link_cite", value="\\parencite{A}",
                     locator={"footnote_id": 5}, evidence="参见[12]。"),
               _item(id="AN-0002", kind="footnote_cite_link", target="tex",
                     action="link_cite", value="\\parencite{B}",
                     locator={"footnote_id": 5}, evidence="参见[12]。"))
    check("②-25 同 (kind,target,locator) 冲突 → 涉事条全部拒绝",
          len(r.accepted) == 0 and len(r.rejected) == 2
          and all("冲突" in x["reason"] for x in r.rejected), str(r.rejected))
    # 每 kind 上限：11 条同 kind → 第 11 条拒绝
    objs = [_item(id=f"AN-{i:04d}", kind="style_fix", target="report",
                  action="report") for i in range(1, 12)]
    r = _parse(*objs)
    check("②-26 每 kind >10 条 → 第 11 条拒绝",
          len(r.accepted) == 10 and len(r.rejected) == 1
          and "每 kind" in r.rejected[0]["reason"], r.summary())
    # 总上限：30 条内合法、第 31 条拒绝（分散 kind 避免撞每 kind 上限）
    kinds = (["style_fix"] * 10 + ["dangling_marker"] * 10
             + ["uncited_entry"] * 10 + ["cite_missing_entry"])
    objs = []
    for i, k in enumerate(kinds, 1):
        action = "report"
        target = "tex" if k == "dangling_marker" else "report"
        objs.append(_item(id=f"AN-{i:04d}", kind=k, target=target, action=action))
    r = _parse(*objs)
    check("②-27 每篇 >30 条 → 第 31 条拒绝",
          len(r.accepted) == 30 and len(r.rejected) == 1
          and "30" in r.rejected[0]["reason"], r.summary())


def test_schema_partition() -> None:
    f4 = json.dumps(_item(id="AN-0001"), ensure_ascii=False)
    f1 = json.dumps({"id": "FN-1", "verdict": "unresolved", "span": "x"},
                    ensure_ascii=False)
    low = coop.parse_low_confidence([("r.md", f1 + "\n" + f4)],
                                    [{"id": "FN-1", "evidence": "x"}])
    check("②-28 附四对象不进附一 unknown（AN-* 不得混进附一报告）",
          low.unknown == [] and low.n_ok == 1, str(low.unknown) + str(low.status))
    r = coop.parse_fulltext_analysis([("r.md", f1 + "\n" + f4)], "开头一段正文")
    check("②-29 附四解析器只认 schema=slcn.fulltext.v1",
          r.total == 1 and len(r.accepted) == 1)
    bad = json.dumps(dict(_item(), schema="slcn.fulltext.v2"), ensure_ascii=False)
    r2 = coop.parse_fulltext_analysis([("r.md", bad)], "开头一段正文")
    check("②-30 schema 不匹配（v2）不进附四解析器", r2.total == 0)
    r3 = coop.parse_fulltext_analysis([], "x")
    check("②-31 无附四对象 → total=0、不报错",
          r3.total == 0 and r3.summary() == "接受 0 条，拒绝 0 条，待人工 0 条，未知 kind 0 个")


def test_report_render_unit(tmp: str) -> None:
    r = _parse(_item(id="AN-0002"),
               _item(id="AN-0001", kind="footnote_cite_link", target="tex",
                     action="link_cite", value="\\parencite{Gilbert2002}",
                     evidence="参见[12]。"),
               _item(id="AN-0003", confidence="low"),
               _item(id="AN-0004", evidence="编造的句子"),
               _item(id="AN-0005", kind="future_kind"))
    md = coop.render_analysis_md(stem=_STEM, report=r)
    check("②-32 报告四节标题齐全",
          "## 一、已接受（校验通过；report-only 不写产物）" in md
          and "## 二、被拒绝（逐条原因）" in md
          and "## 三、待人工（`unresolved` 或 `confidence=low`）" in md
          and "## 四、未知 kind" in md)
    check("②-33 报告明写 report-only / 不写产物",
          "report-only" in md and "不写任何产物" in md)
    check("②-34 被拒条写明原因", "`AN-0004`" in md and "编造" in md)
    check("②-35 待人工条在第三节", "`AN-0003`" in md and "confidence=low" in md)
    check("②-36 未知 kind 在第四节", "`AN-0005`" in md and "future_kind" in md)
    p1 = coop.write_analysis_report(os.path.join(tmp, "ana1"), _STEM, r)
    p2 = coop.write_analysis_report(os.path.join(tmp, "ana2"), _STEM, r)
    check("②-37 报告落盘名 <名>_AI全文分析.md/.jsonl",
          os.path.basename(p1[0]) == f"{_STEM}_AI全文分析.md"
          and os.path.basename(p1[1]) == f"{_STEM}_AI全文分析.jsonl")
    check("②-38 同输入同报告（确定性 sha）",
          sha(p1[0]) == sha(p2[0]) and sha(p1[1]) == sha(p2[1]))
    lines = [ln for ln in read(p1[1]).splitlines() if ln.strip()]
    ids = [json.loads(ln)["id"] for ln in lines]
    check("②-39 jsonl 每行一条已接受项、按 id 排序",
          ids == ["AN-0001", "AN-0002"], str(ids))
    r0 = _parse(_item(confidence="low"))
    p0 = coop.write_analysis_report(os.path.join(tmp, "ana0"), _STEM, r0)
    check("②-40 0 条已接受 → jsonl 为空文件", read(p0[1]) == "")
    check("②-41 _is_coop_artifact_name 认附四报告（目录回填不自己吃自己）",
          coop._is_coop_artifact_name(f"{_STEM}_AI全文分析.md")
          and coop._is_coop_artifact_name(f"{_STEM}_AI全文分析.jsonl"))


# ── ③ CLI ph1：附四只在开关下出现 ───────────────────────────────────────


def test_cli_ph1(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "ph1")
    p = _ph1(src, bibp, out, ["--coop-fulltext", "full"])
    tb = read(_taskbook(out))
    st = _state(out)
    check("③-1 ph1 退出 0", p.returncode == 0, p.stderr[-300:])
    check("③-2 任务书顺序 附一 < 附二 < 附三 < 附四",
          tb.find(coop.SECTION_TITLE) < tb.find(coop.SECTION_BACKFILL_TITLE)
          < tb.find(coop.FULLTEXT_TITLE) < tb.find(coop.ANALYSIS_TITLE)
          and tb.find(coop.ANALYSIS_TITLE) > 0)
    check("③-3 附四在附三之后且是任务书最后一节",
          tb.rstrip().endswith("你的建议只供编辑人工裁决。") or
          "## 附四" in tb[tb.find(coop.FULLTEXT_TITLE):])
    check("③-4 附四七项指令+schema+硬约束齐全",
          all(k in tb for k in ("正文/脚注引用覆盖与漏链", "脚注→引用转换建议",
                                "CJK 译名 bib key 规范", coop.ANALYSIS_SCHEMA,
                                "每 kind ≤10", "逐字 `evidence`")))
    check("③-5 state.analysis 新键（schema/enabled/policy/whitelist 留痕）",
          st.get("analysis") == {"schema": coop.ANALYSIS_SCHEMA, "enabled": True,
                                 "default_policy": "report-only",
                                 "auto_whitelist_future": ["bib_field_fill", "meta_fill",
                                                           "footnote_cite_link"]},
          str(st.get("analysis")))
    check("③-6 state schema 仍 1", st.get("schema") == 1)
    check("③-7 ph1 日志有一行附四摘要", "任务书附四：初排分析指令" in p.stdout)
    st_plain = {}
    shutil.rmtree(out, ignore_errors=True)
    p0 = _ph1(src, bibp, out)
    st_plain = _state(out)
    check("③-8 analysis 不入参数指纹（与无 flag 的 fingerprint 相同）",
          st.get("fingerprint") == st_plain.get("fingerprint") and bool(st.get("fingerprint")))
    # context 档同样出附四
    src2, bib2, out2 = _fixture(tmp, "ph1ctx")
    p2 = _ph1(src2, bib2, out2, ["--coop-fulltext", "context"])
    tb2 = read(_taskbook(out2))
    check("③-9 context 档也出附四（且附三在前）",
          p2.returncode == 0 and coop.ANALYSIS_TITLE in tb2
          and tb2.find(coop.FULLTEXT_TITLE) < tb2.find(coop.ANALYSIS_TITLE))
    # 无可用正文 → 附三/附四一起跳过
    empty = ("\\documentclass{article}\n\\title{T}\n\\author{张三}\n"
             "\\begin{document}\n\\end{document}\n")
    src3, bib3, out3 = _fixture(tmp, "ph1empty", body=empty)
    p3 = _ph1(src3, bib3, out3, ["--coop-fulltext", "full"])
    tb3 = read(_taskbook(out3)) if os.path.isfile(_taskbook(out3)) else ""
    st3 = _state(out3)
    check("③-10 无可用正文 → 附三/附四一起跳过（state 无 analysis）",
          p3.returncode == 0 and "附四" not in tb3 and "analysis" not in st3
          and "附三跳过" in p3.stdout, p3.stdout[-300:])


# ── ④ CLI 默认零变化（与 HEAD 逐字节）────────────────────────────────────


def test_default_zero_change(tmp: str, head: str) -> None:
    src, bibp, out = _fixture(tmp, "noflag")
    new = _ph1(src, bibp, out)
    tb_new = read(_taskbook(out))
    snap_new = {k: v for k, v in snapshot(_stage(out)).items()
                if k != coop.STATE_NAME}
    st_new = _state(out)
    shutil.rmtree(out, ignore_errors=True)
    old = _ph1(src, bibp, out, engine=head)
    tb_old = read(_taskbook(out))
    snap_old = {k: v for k, v in snapshot(_stage(out)).items()
                if k != coop.STATE_NAME}
    check("④-1 无 flag ph1 退出 0", new.returncode == 0)
    check("④-2 无 flag 任务书与 HEAD 逐字节相同", _b276_mask(tb_new) == _b276_mask(tb_old))
    check("④-3 无 flag 产物树（tex/bib/任务书）与 HEAD 逐字节相同", snap_new == snap_old)
    check("④-4 无 flag 日志与 HEAD 逐字节相同", _b276_mask(new.stdout) == _b276_mask(old.stdout))
    check("④-5 无 flag 任务书没有「附四」", "附四" not in tb_new)
    check("④-6 无 flag state 没有 analysis/fulltext 键",
          "analysis" not in st_new and "fulltext" not in st_new)
    # ph2 记录对照：旧 state（无 analysis）+ 新引擎 = 旧引擎逐字节
    st = _state(out)
    reply = write(os.path.join(tmp, "noflag", "reply.json"), _reply_for_first_item(st))
    sp = _stage(out)
    cp = os.path.join(tmp, "noflag", "stage.bak")
    shutil.copytree(sp, cp)
    r_new = _ph2(src, out, reply)
    rec_new = read(os.path.join(_final(out), coop.RECORD_NAME))
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    shutil.copytree(cp, sp)
    r_old = _ph2(src, out, reply, engine=head)
    rec_old = read(os.path.join(_final(out), coop.RECORD_NAME))
    check("④-7 无 flag：新 ph2 与 HEAD 记录逐字节相同",
          r_new.returncode == 0 and r_old.returncode == 0 and _b276_mask(rec_new) == _b276_mask(rec_old),
          r_new.stderr[-200:] + r_old.stderr[-200:])
    check("④-8 无 flag 记录没有「全文分析（附四」节", "全文分析（附四" not in rec_new)
    check("④-9 无 flag 不生成附四报告",
          not os.path.exists(_md_path(out)) and not os.path.exists(_jsonl_path(out)))


def _reply_for_first_item(st: dict) -> str:
    item = (st.get("low_confidence") or [{}])[0]
    return json.dumps({"id": item.get("id", ""), "verdict": "unresolved",
                       "span": (item.get("evidence") or "")[:10]},
                      ensure_ascii=False)


# ── ⑤ CLI ph2：附四解析/落盘/记录（report-only）──────────────────────────


def _analysis_reply(st: dict) -> str:
    tb = read(str(st.get("taskbook") or ""))
    # 从任务书里逐字取 evidence（任务书含附三正文：`\footnote{脚注说明文字。}`）。
    ev_foot = "脚注说明文字"
    if ev_foot not in tb:
        ev_foot = "开头一段正文"
    objs = [
        {"schema": coop.ANALYSIS_SCHEMA, "id": "AN-0002",
         "kind": "footnote_cite_link", "target": "tex", "action": "link_cite",
         "field": "", "value": "\\parencite{Quine1970}",
         "locator": {"footnote_id": 1}, "anchor": ev_foot,
         "evidence": ev_foot, "confidence": "high", "source": "源稿脚注",
         "note": "脚注是纯引用，可提升为正文引用"},
        {"schema": coop.ANALYSIS_SCHEMA, "id": "AN-0001",
         "kind": "cite_missing_entry", "target": "report", "action": "report",
         "field": "", "value": "", "locator": "",
         "evidence": "文末清单里有一条未被引用的条目", "confidence": "high",
         "source": "源稿正文", "note": "清单条目未被正文引用"},
        {"schema": coop.ANALYSIS_SCHEMA, "id": "AN-0003",
         "kind": "style_fix", "target": "report", "action": "report",
         "evidence": "这句话任务书里没有，属于编造", "confidence": "high"},
        {"schema": coop.ANALYSIS_SCHEMA, "id": "AN-0004",
         "kind": "lowconf_review", "target": "report", "action": "report",
         "confidence": "low", "note": "附一某条我拿不准"},
        {"schema": coop.ANALYSIS_SCHEMA, "id": "AN-0005",
         "kind": "brand_new_kind", "target": "report", "action": "report",
         "confidence": "high", "evidence": "开头一段正文"},
    ]
    body = _reply_for_first_item(st)
    body += "\n" + "\n".join(json.dumps(o, ensure_ascii=False) for o in objs)
    return body


def test_cli_ph2(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "ph2")
    p1 = _ph1(src, bibp, out, ["--coop-fulltext", "full"])
    st = _state(out)
    check("⑤-0 ph1（附四启用）退出 0 且 state.analysis 在", p1.returncode == 0
          and (st.get("analysis") or {}).get("enabled") is True, p1.stderr[-300:])
    sp = _stage(out)
    cp = os.path.join(tmp, "ph2", "stage.bak")
    shutil.copytree(sp, cp)
    base_reply = write(os.path.join(tmp, "ph2", "base.json"), _reply_for_first_item(st))
    r0 = _ph2(src, out, base_reply)
    base_products = _products(_final(out))
    check("⑤-1 基线 ph2（仅附一回复）退出 0", r0.returncode == 0, r0.stderr[-300:])
    # 无附四对象：报告文件不生成；记录「六」节出现（0 计数）
    rec_base = read(os.path.join(_final(out), coop.RECORD_NAME))
    check("⑤-2 附四启用但回复无附四对象 → 不生成报告文件",
          not os.path.exists(_md_path(out)) and not os.path.exists(_jsonl_path(out)))
    check("⑤-3 附四启用但回复无附四对象 → 记录不新增「六」节（与旧版逐字节同）",
          "## 六、全文分析（附四，report-only）" not in rec_base)

    # ② 附四回复 → 报告四节分流 + 产物逐字节不变
    _restore_stage(out, cp)
    ana_reply = write(os.path.join(tmp, "ph2", "analysis.txt"), _analysis_reply(st))
    r1 = _ph2(src, out, ana_reply)
    check("⑤-4 含附四回复 ph2 退出 0", r1.returncode == 0, r1.stderr[-500:])
    ana_products = _products(_final(out))
    check("⑤-5 report-only：.tex/.bib 等产物与基线逐字节相同", ana_products == base_products,
          str({k: (base_products.get(k), ana_products.get(k))
               for k in set(base_products) | set(ana_products)
               if base_products.get(k) != ana_products.get(k)}))
    check("⑤-6 不产生 .ai前", not [k for k in ana_products if k.endswith(".ai前")])
    check("⑤-7 交付目录有两份附四报告（随 promote 进入）",
          os.path.isfile(_md_path(out)) and os.path.isfile(_jsonl_path(out)))
    md = read(_md_path(out))
    check("⑤-8 报告四节：已接受/被拒绝/待人工/未知 kind",
          "## 一、已接受" in md and "## 二、被拒绝" in md
          and "## 三、待人工" in md and "## 四、未知 kind" in md)
    check("⑤-9 报告分流：2 条接受 / 1 条拒绝（编造 evidence）/ 1 条待人工 / 1 个未知 kind",
          "- 已接受：**2** 条" in md and "- 被拒绝：**1** 条" in md
          and "- 待人工：**1** 条" in md and "- 未知 kind：**1** 个" in md, md[:600])
    check("⑤-10 编造 evidence 逐条拒绝原因写明", "编造" in md)
    check("⑤-11 jsonl 只含已接受项、按 id 排序",
          [json.loads(ln)["id"] for ln in read(_jsonl_path(out)).splitlines() if ln.strip()]
          == ["AN-0001", "AN-0002"])
    check("⑤-12 stdout 有附四摘要行",
          re.search(r"全文分析（附四，report-only）：接受 2 条，拒绝 1 条，待人工 1 条，"
                    r"未知 kind 1 个", r1.stdout) is not None, r1.stdout[-800:])
    check("⑤-13 stdout 打印附四报告文件名",
          "全文分析报告（report-only，不写产物）：San_Zhang_AI全文分析.md" in r1.stdout)

    # 记录：六节计数正确；除六节外与基线记录逐字节相同
    rec_ana = read(os.path.join(_final(out), coop.RECORD_NAME))
    check("⑤-14 协同记录六节计数正确",
          "## 六、全文分析（附四，report-only）" in rec_ana
          and "接受 2 条，拒绝 1 条，待人工 1 条，未知 kind 1 个" in rec_ana
          and "`San_Zhang_AI全文分析.md` / `.jsonl`" in rec_ana)
    check("⑤-15 记录除「六」节外与无附四回复逐字节相同",
          _strip_section6(rec_ana) == rec_base, "§6 stripped mismatch")

    # ③ --coop-apply 不碰附四（report-only 硬边界）
    _restore_stage(out, cp)
    r2 = _ph2(src, out, ana_reply, ["--coop-apply"])
    applied_products = _products(_final(out))
    check("⑤-16 --coop-apply + 附四 → 退出 0 且产物仍逐字节不变",
          r2.returncode == 0 and applied_products == base_products,
          r2.stderr[-300:])
    check("⑤-17 --coop-apply + 附四 → 仍不产生 .ai前",
          not [k for k in applied_products if k.endswith(".ai前")])

    # ④ B-211：交付成功后暂存目录（含状态）已清理 → 重跑明确拒绝，
    # 不静默重交付、不改交付产物（报告本身已是最终目录里的本工具产物）。
    _final_before = snapshot(_final(out))
    r3 = _ph2(src, out, ana_reply)
    check("⑤-18 交付后重跑：明确拒绝（退出 2、指向暂存目录/第一段）且交付产物一个字节不动",
          r3.returncode == 2
          and ("找不到可续跑的协同初排暂存目录" in r3.stderr
               or "请先运行第一段" in r3.stderr)
          and snapshot(_final(out)) == _final_before, (r3.stderr + r3.stdout)[-500:])
    check("⑤-19 promote 成功后暂存目录已清理（失败才保留）",
          not os.path.exists(_stage(out)))


# ── ⑥ CLI：仅附四回复不触发「回复全部无效」──────────────────────────────


def test_only_analysis_reply(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "onlyana")
    p1 = _ph1(src, bibp, out, ["--coop-fulltext", "context"])
    st = _state(out)
    check("⑥-1 ph1 退出 0", p1.returncode == 0, p1.stderr[-300:])
    reply = write(os.path.join(tmp, "onlyana", "ana.json"), _analysis_reply(st))
    r = _ph2(src, out, reply, ["--clean"])   # --clean 不得误删附四报告（本工具产物）
    check("⑥-2 仅附四回复 → 不判「全部无效」、退出 0",
          r.returncode == 0, (r.stderr + r.stdout)[-600:])
    check("⑥-3 仅附四回复也能交付并生成报告",
          os.path.isfile(_final(out) + f"/{coop.RECORD_NAME}")
          and os.path.isfile(_md_path(out)))
    check("⑥-4 仅附四回复不改 reference.bib / .tex（无 .ai前）",
          not [k for k in snapshot(_final(out)) if k.endswith(".ai前")])
    check("⑥-5 --clean 不误删附四报告（报告仍是本工具产物）",
          os.path.isfile(_md_path(out)) and os.path.isfile(_jsonl_path(out)))


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
    tmp = tempfile.mkdtemp(prefix="slcn_b74_ana_")
    try:
        notes = os.path.join(tmp, "notes")
        os.makedirs(os.path.join(notes, "cases"), exist_ok=True)  # 无 bib_db/ → 不查库
        ENV["SLCN_NOTES"] = notes
        test_render_unit()
        test_parse_accept()
        test_parse_reject()
        test_parse_evidence_whitespace()
        test_parse_unknown_kind()
        test_parse_conflicts_and_limits()
        test_schema_partition()
        test_report_render_unit(tmp)
        test_cli_ph1(tmp)
        head = ensure_head_engine(tmp)
        test_b276_frontmatter_fill(tmp)
        if head:
            test_default_zero_change(tmp, head)
        else:
            skip("④ 默认零变化（与 HEAD 逐字节）", "git archive HEAD 失败")
        test_cli_ph2(tmp)
        test_only_analysis_reply(tmp)
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
