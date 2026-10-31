#!/usr/bin/env python3
r"""第五十九批：全文 AI 核查通道①（`--coop-fulltext {context,full}` + 任务书附三）判据测试。

Lead 2026-10-20 拍板（全部离线：不联网、不调付费 API、不读稿件库、不跑 TeX）：
  · 默认关：不带 flag 时任务书/产物/日志与 HEAD（`git archive HEAD`）**逐字节相同**
    （B-288 起任务书默认新增「任务卡 + 三卷标题 + 卷末模板」；这些**只由 B-288 新增**，
    本测试用 `_b288_mask()` 精确剥掉后再对照 → 其余正文仍逐字节相同，不是放松断言）；
  · `context`：引用/脚注锚点 ±300 字窗口合并；约 4 万字符起只提示（批63：不截断）；
  · `full`：正文全文；约 8 万字符起只提示（批63：**不截断、整篇发送**，保留 tokens_est）；
  · 仅 `--coop-taskbook-only` 有效，非法组合退出 2；
  · 任务书附二之后追加附三；state 增 fulltext {mode,chars,truncated,tokens_est}
    （schema 仍 1、不入指纹）；协同记录只记元信息、不落正文；回复契约/--coop-apply 不变。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch59_coop_fulltext.py
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


_B288_META_RE = re.compile(
    r"\n\n## 任务卡[^\n]*\n")
_B288_VOLA_RE = re.compile(r"\n\n## 卷 A 文献核实(?=\n)")
_B288_FOOTA_RE = re.compile(r"\n\n### 卷 A · 回填模板")


def _b288_mask(text: str) -> str:
    """B-288：剥掉任务书 v2 新增的**任务卡/卷 A 标题/卷末模板**（含 B/C 两卷）。

    只动 B-288 新增的行；剥完剩下的正文必须与 HEAD 的 v1 任务书逐字节相同。
    位置是写死的（卡在首个 `#` 之后、`---` 之前；卷 A 标题在 `---` 与 `## 一` 之间；
    卷末模板从 `### 卷 A · 回填模板` 到 EOF），所以不用宽松正则。
    """
    m = _B288_META_RE.search(text)
    if m:
        end = text.find("\n\n---\n\n## 卷 A 文献核实", m.end())
        if end >= 0:
            text = text[:m.start()] + text[end:]
    text = _B288_VOLA_RE.sub("", text, count=1)
    m = _B288_FOOTA_RE.search(text)
    if m:
        text = text[:m.start()] + "\n"
    return text


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
    # ⑥ B-288：任务书 v2 的卡/三卷/卷末模板剥掉；任务书行数随卡增长 → 归一
    text = _b288_mask(text)
    text = re.sub(r"(文献核实\.md（)\d+( 行）)", r"\1N\2", text)
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
    """B-278：初排默认自动修一校子集；.tex 先归一（对新/旧产物同规则）再哈希。"""
    from slcn.build import apply_proof_autofix
    out: dict[str, str] = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            text = _b276_mask(read(p))
            if name.endswith(".tex"):
                text = apply_proof_autofix(text, "zh")[0]
            out[os.path.relpath(p, folder)] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return out


def make_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES")):
            env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    # HEAD 引擎是 `git archive` 到临时目录的副本：它从自己的路径找不到稿件库（同级目录
    # 不在那里），于是「五、库内已核实」只有新引擎有 → ③ 的逐字节对照假红。显式把
    # **同一份**稿件库（若本机有）钉进 ENV，两边看到同一个库，对照才是真的。
    try:
        from slcn import paths as _paths
        _root = _paths.manuscripts_root()
        if _root:
            env[_paths.ENV_VAR] = _root
        # 引用库（任务书「五、库内已核实」）在**笔记仓** `bib_db/` 下；HEAD 副本从自己的
        # 路径同样找不到笔记仓。两边钉同一个笔记仓，库命中才一致。
        _notes = _paths.notes_root()
        if _notes:
            env[_paths.NOTES_ENV] = _notes
    except Exception:  # noqa: BLE001  稿件库/笔记仓都是可选依赖；没有时两边都查不到，仍一致
        pass
    return env


ENV = make_env()

# 正文：两个锚点 + 远处「只有全文才含」的标记（context 窗口应看不到它）。
_FAR = "全文独有标记-FAR-不应出现在context窗口里"
_TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
开头一段正文。这里引用\parencite{Quine1970}，并带一个脚注。\footnote{脚注说明文字。}
中间一段普通正文，用于观察窗口合并与去重。
""" + "填充文字。" * 120 + "\\parencite{Other2020}\n" + "后填充。" * 120 + _FAR + """
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


def _run(args: list[str], out: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(_ENGINE, "convert.py"), *args]
    if out:
        cmd += ["--out", out]
    return subprocess.run(cmd, cwd=_ENGINE, env=ENV, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=240)


def _fixture(tmp: str, tag: str, body: str | None = None, bib: str = _BIB):
    srcdir = os.path.join(tmp, tag, "src")
    tex = _TEX if body is None else body
    source = write(os.path.join(srcdir, "main.tex"), tex)
    bibp = write(os.path.join(srcdir, "refs.bib"), bib)
    return source, bibp, os.path.join(tmp, tag, "out")


def _ph1(src: str, bibp: str, out: str, extra: list[str] | None = None,
         engine: str = _ENGINE) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(engine, "convert.py"),
         "--source", src, "--bib", bibp, "--out", out, "--author", "张三",
         "--no-original", "--coop-taskbook-only"] + list(extra or []),
        cwd=_ENGINE, env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)


def _stage(out: str) -> str:
    return os.path.join(out, "San_Zhang_待核")


def _taskbook(out: str) -> str:
    return os.path.join(_stage(out), "San_Zhang_文献核实.md")


def _state(out: str) -> dict:
    p = os.path.join(_stage(out), coop.STATE_NAME)
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else {}


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


# ── ① extract_fulltext（纯逻辑）──────────────────────────────────────────


def test_extract_unit() -> None:
    body = ("A" * 1000 + r"\parencite{k1}" + "B" * 100 + r"\footnote{注}" + "C" * 1000)
    info = coop.extract_fulltext(body, "context")
    check("①-1 context 含锚点本身", r"\parencite{k1}" in info["text"]
          and r"\footnote{注}" in info["text"])
    check("①-2 context 只取窗口：远处 1000 字的 A/C 不全含",
          info["text"].count("A") < 1000 and info["text"].count("C") < 1000)
    check("①-3 相邻锚点窗口合并成 1 段", info["windows"] == 1)
    far = "D" * 5000 + r"\parencite{k1}" + "E" * 5000 + r"\parencite{k2}"
    info2 = coop.extract_fulltext(far, "context")
    check("①-4 远离锚点窗口分成 2 段且中间有节选标记",
          info2["windows"] == 2 and "…" in info2["text"]
          and "D" * 5000 not in info2["text"])
    check("①-5 窗口节选不是连续正文（D 段只保留锚点前 300 字）",
          info2["text"].count("D") == 300)
    full = coop.extract_fulltext(far, "full")
    check("①-6 full 含全文", "D" * 5000 in full["text"] and "E" * 5000 in full["text"])
    check("①-7 整行 % 注释被剥掉",
          "% 本行是注释" not in coop.extract_fulltext("% 本行是注释\n正文\\parencite{k1}",
                                                    "context")["text"])
    check("①-8 围栏 ``` 被替换（不破坏任务书代码块）",
          "```" not in coop.extract_fulltext("正文```代码```\\parencite{k1}", "context")["text"])
    big = ("填充。" * 200 + r"\parencite{k1}") * 80      # 锚点密集 → 窗口合并成超长
    bi = coop.extract_fulltext(big, "context")
    check("①-9 context 超 4 万字 → 不截断（批63），只标 over_limit",
          bi["truncated"] is False and "已截断" not in bi["text"]
          and bi["over_limit"] is True
          and bi["chars"] == bi["orig_chars"] > coop.FULLTEXT_LIMITS["context"])
    bf = coop.extract_fulltext("Z" * (coop.FULLTEXT_LIMITS["full"] + 5000), "full")
    check("①-10 full 超 8 万字 → 不截断、内容完整（整篇发送）",
          bf["truncated"] is False and "已截断" not in bf["text"]
          and bf["over_limit"] is True and bf["chars"] > coop.FULLTEXT_LIMITS["full"]
          and bf["text"].startswith("Z"))
    check("①-11 tokens_est == estimate_tokens(text)",
          bi["tokens_est"] == coop.estimate_tokens(bi["text"])
          and info["tokens_est"] == coop.estimate_tokens(info["text"]))
    try:
        coop.extract_fulltext("x", "huge")
        bad = False
    except ValueError:
        bad = True
    check("①-12 未知模式 → ValueError", bad)
    no_anchor = coop.extract_fulltext("没有任何引用锚点的正文。", "context")
    check("①-13 无锚点时 context 退回全文（不产出空附三）",
          no_anchor["windows"] == 0 and "没有任何引用锚点的正文" in no_anchor["text"])
    empty = coop.extract_fulltext("", "context")
    check("①-14 空正文 → chars=0/tokens_est>0 不崩",
          empty["chars"] == 0 and empty["tokens_est"] >= 1)


# ── ② render_fulltext_section ───────────────────────────────────────────


def test_render_unit() -> None:
    info = coop.extract_fulltext(
        "正文\\parencite{k1}" + "x" * 100, "context")
    sec = coop.render_fulltext_section(info)
    check("②-1 含附三标题", coop.FULLTEXT_TITLE in sec)
    check("②-2 写明只按附一 JSON 回复、不要改写正文",
          "附一规定的 JSON" in sec and "不要改写" in sec and "正文" in sec)
    check("②-3 含 text 围栏与正文内容",
          "```text" in sec and r"\parencite{k1}" in sec and sec.rstrip().endswith("```"))
    check("②-4 context 模式注明窗口", "窗口" in sec)
    check("②-5 full 模式注明全文", "正文全文" in coop.render_fulltext_section(
        coop.extract_fulltext("正文", "full")))
    trunc = coop.extract_fulltext("T" * (coop.FULLTEXT_LIMITS["full"] + 1000), "full")
    _sec = coop.render_fulltext_section(trunc)
    check("②-6 超阈值时任务书有「超长但不截断」提示",
          "不截断" in _sec and "整篇发送" in _sec and "已截断" not in _sec)


# ── ③ CLI：无 flag 与 HEAD 逐字节相同（默认关零变化）─────────────────────

# B-004（2026-10-28 ZH-08 默认启用）预期日志差异：过滤 ZH-08 相关行后比较
import re as _re
_ZH08_LOG_NOISE = _re.compile(r"(ZH-08|判断指引：|· 第\d+\s*行：|自动修复 |一校可自动改|· ZH-\d+)")


def _strip_zh08_log(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not _ZH08_LOG_NOISE.search(ln))



def test_no_flag_zero_change(tmp: str, head: str) -> None:
    src, bibp, out = _fixture(tmp, "noflag")
    new = _ph1(src, bibp, out)
    tb_new = read(_taskbook(out))
    st_new = _state(out)
    snap_new = {k: v for k, v in snapshot(os.path.join(_stage(out))).items()
                if k != coop.STATE_NAME}
    shutil.rmtree(out, ignore_errors=True)
    old = _ph1(src, bibp, out, engine=head)
    tb_old = read(_taskbook(out))
    snap_old = {k: v for k, v in snapshot(os.path.join(_stage(out))).items()
                if k != coop.STATE_NAME}
    check("③-1 无 flag ph1 退出 0", new.returncode == 0, new.stderr[-300:])
    check("③-2 无 flag 任务书去掉 B-288 卡/卷结构后与 HEAD 逐字节相同",
          _b276_mask(tb_new) == _b276_mask(tb_old))
    check("③-3 无 flag 产物树（tex/bib/任务书）与 HEAD 逐字节相同（任务书按 B-288 掩码归一）",
          snap_new == snap_old)
    check("③-4 无 flag 日志与 HEAD 逐字节相同（B-004 ZH-08 行过滤；B-288 任务书行数归一）",
      _strip_zh08_log(_b276_mask(new.stdout))
          == _strip_zh08_log(_b276_mask(old.stdout)))
    check("③-5 无 flag 任务书没有「附三」", coop.FULLTEXT_TITLE not in tb_new)
    check("③-6 无 flag state 没有 fulltext 键", "fulltext" not in st_new)
    check("③-7 HEAD 引擎没有该功能（state 也无 fulltext）", "fulltext" not in _state(out))
    check("③-8 schema 仍 1", st_new.get("schema") == 1)


# ── ④ CLI：context 档 ───────────────────────────────────────────────────


def test_cli_context(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "ctx")
    p0 = _ph1(src, bibp, out)
    st0 = _state(out)
    _prod = {"San_Zhang.tex", "reference.bib", "SLCN-MIX.sty"}
    snap_plain = {k: v for k, v in snapshot(os.path.join(_stage(out))).items()
                  if k in _prod}
    shutil.rmtree(out, ignore_errors=True)
    p = _ph1(src, bibp, out, ["--coop-fulltext", "context"])
    tb = read(_taskbook(out))
    st = _state(out)
    ft = st.get("fulltext") or {}
    snap_flag = snapshot(os.path.join(_stage(out)))
    check("④-1 context ph1 退出 0", p.returncode == 0 and p0.returncode == 0,
          p.stderr[-300:])
    check("④-2 任务书含附三且顺序 附一 < 附二 < 附三",
          coop.SECTION_TITLE in tb and coop.SECTION_BACKFILL_TITLE in tb
          and coop.FULLTEXT_TITLE in tb
          and tb.find(coop.SECTION_TITLE) < tb.find(coop.SECTION_BACKFILL_TITLE)
          < tb.find(coop.FULLTEXT_TITLE))
    check("④-3 附三正文含稿件里的引用锚点", r"\parencite{Quine1970}" in tb)
    check("④-4 附三只含窗口：不含远处「全文独有标记」", _FAR not in tb)
    check("④-5 state.fulltext 四键齐全且 mode=context",
          set(ft) == {"mode", "chars", "truncated", "tokens_est"}
          and ft["mode"] == "context" and ft["chars"] > 0
          and ft["truncated"] is False and ft["tokens_est"] > 0, str(ft))
    check("④-6 日志有一行附三摘要",
          re.search(r"任务书附三：正文.*?(\d+) 字（约 \d+ tokens）", p.stdout) is not None,
          p.stdout[-500:])
    check("④-7 启用附三不改产物（tex/bib/sty 与无 flag 逐字节相同）",
          {k: v for k, v in snap_flag.items() if k in _prod}
          == {k: v for k, v in snap_plain.items() if k in _prod})
    check("④-8 附三信息不参与指纹（与无 flag 的 fingerprint 相同）",
          st.get("fingerprint") == st0.get("fingerprint") and bool(st.get("fingerprint")))
    check("④-9 state schema 仍 1", st.get("schema") == 1)


# ── ⑤ CLI：full 档 / 截断 ───────────────────────────────────────────────


def test_cli_full_and_truncate(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "full")
    p = _ph1(src, bibp, out, ["--coop-fulltext", "full"])
    tb = read(_taskbook(out))
    ft = _state(out).get("fulltext") or {}
    check("⑤-1 full ph1 退出 0", p.returncode == 0, p.stderr[-300:])
    check("⑤-2 full 附三含远处「全文独有标记」", _FAR in tb)
    check("⑤-3 state.fulltext.mode=full 且未截断",
          ft.get("mode") == "full" and ft.get("truncated") is False, str(ft))
    check("⑤-4 full 日志摘要为「全文」", "任务书附三：正文全文" in p.stdout)

    # context 超上限（锚点密集 → 窗口合并超 4 万）
    big_ctx = ("\\documentclass{article}\\title{测试题名}\\author{张三}\\begin{document}\n"
               + ("填充正文。" * 100 + r"\parencite{Quine1970} ") * 120
               + "\n\\printbibliography\\end{document}\n")
    s2, b2, o2 = _fixture(tmp, "bigctx", body=big_ctx)
    p2 = _ph1(s2, b2, o2, ["--coop-fulltext", "context"])
    tb2 = read(_taskbook(o2))
    ft2 = _state(o2).get("fulltext") or {}
    check("⑤-5 context 超 4 万 → state.truncated=False、chars>4 万（不截断）",
          p2.returncode == 0 and ft2.get("truncated") is False
          and ft2.get("chars", 0) > coop.FULLTEXT_LIMITS["context"], str(ft2))
    check("⑤-6 任务书提示超长但明确「不截断」（不再有截断标记）",
          "不截断" in tb2 and "已截断：原" not in tb2)
    check("⑤-7 不截断时 chars 反映真实正文（不丢内容）",
          ft2["chars"] > coop.FULLTEXT_LIMITS["context"])

    big_full = ("\\documentclass{article}\\title{测试题名}\\author{张三}\\begin{document}\n"
                + "长文填充。" * 20000 + "\n\\end{document}\n")
    s3, b3, o3 = _fixture(tmp, "bigfull", body=big_full)
    p3 = _ph1(s3, b3, o3, ["--coop-fulltext", "full"])
    ft3 = _state(o3).get("fulltext") or {}
    check("⑤-8 full 超 8 万 → 不截断（truncated=False），state 仍四键",
          p3.returncode == 0 and ft3.get("truncated") is False
          and ft3.get("chars", 0) > coop.FULLTEXT_LIMITS["full"]
          and set(ft3) == {"mode", "chars", "truncated", "tokens_est"}, str(ft3))


# ── ⑥ CLI：非法组合退出 2 ───────────────────────────────────────────────


def test_invalid(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "bad")
    r = _run(["--source", src, "--bib", bibp, "--out", out,
              "--coop-fulltext", "context"], out=None)
    err = r.stdout + r.stderr
    check("⑥-1 普通转换带 --coop-fulltext → 退出 2", r.returncode == 2, err[-300:])
    check("⑥-2 明确提示只配合 --coop-taskbook-only", "只配合 --coop-taskbook-only" in err)
    r2 = _run(["--source", src, "--bib", bibp, "--out", out,
               "--coop-taskbook-only", "--coop-fulltext", "huge"])
    check("⑥-3 非法档位值 → 退出 2", r2.returncode == 2)
    check("⑥-4 错误信息列出合法档位", "context" in (r2.stdout + r2.stderr)
          and "full" in (r2.stdout + r2.stderr))


# ── ⑦ ph2：记录元信息、不落正文；无 flag 记录与 HEAD 逐字节相同 ──────────


def _reply_for_first_item(st: dict) -> str:
    item = (st.get("low_confidence") or [{}])[0]
    return json.dumps({"id": item.get("id", ""), "verdict": "unresolved",
                       "span": (item.get("evidence") or "")[:10]},
                      ensure_ascii=False)


def test_ph2_record(tmp: str, head: str) -> None:
    # (a) 无 flag：新/HEAD ph2 记录逐字节相同（state 无 fulltext 时行为不变）
    src, bibp, out = _fixture(tmp, "ph2plain")
    _ph1(src, bibp, out)
    st = _state(out)
    reply = write(os.path.join(tmp, "ph2plain", "reply.json"), _reply_for_first_item(st))
    sp = os.path.join(_stage(out))
    cp = os.path.join(tmp, "ph2plain", "stage.bak")
    shutil.copytree(sp, cp)
    r_new = _run(["--source", src, "--out", out, "--coop-replies", reply,
                  "--no-original"])
    rec_new = read(os.path.join(out, "San_Zhang", coop.RECORD_NAME))
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    shutil.copytree(cp, sp)
    r_old = subprocess.run(
        [sys.executable, os.path.join(head, "convert.py"),
         "--source", src, "--out", out, "--coop-replies", reply, "--no-original"],
        cwd=_ENGINE, env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)
    rec_old = read(os.path.join(out, "San_Zhang", coop.RECORD_NAME))
    check("⑦-1 无 flag：新 ph2 与 HEAD ph2 记录逐字节相同",
          r_new.returncode == 0 and r_old.returncode == 0 and _b276_mask(rec_new) == _b276_mask(rec_old))
    check("⑦-2 无 flag 记录没有附三行", "任务书附三" not in rec_new)

    # (b) 有 flag：记录多一行元信息、不落正文
    src2, bib2, out2 = _fixture(tmp, "ph2flag")
    _ph1(src2, bib2, out2, ["--coop-fulltext", "full"])
    st2 = _state(out2)
    reply2 = write(os.path.join(tmp, "ph2flag", "reply.json"), _reply_for_first_item(st2))
    sp2 = os.path.join(_stage(out2))
    cp2 = os.path.join(tmp, "ph2flag", "stage.bak")
    shutil.copytree(sp2, cp2)
    r2 = _run(["--source", src2, "--out", out2, "--coop-replies", reply2,
               "--no-original"])
    rec2 = read(os.path.join(out2, "San_Zhang", coop.RECORD_NAME))
    check("⑦-3 有 flag：ph2 退出 0", r2.returncode == 0, r2.stderr[-300:])
    check("⑦-4 记录含附三元信息（mode/字数/tokens）",
          "任务书附三（正文）" in rec2 and "`full`" in rec2
          and re.search(r"约 \d+ tokens", rec2) is not None)
    check("⑦-5 记录不落正文（含「全文独有标记」的正文不入记录）", _FAR not in rec2)
    shutil.rmtree(out2, ignore_errors=True)
    os.makedirs(out2, exist_ok=True)
    shutil.copytree(cp2, sp2)
    r3 = subprocess.run(
        [sys.executable, os.path.join(head, "convert.py"),
         "--source", src2, "--out", out2, "--coop-replies", reply2, "--no-original"],
        cwd=_ENGINE, env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)
    rec3 = read(os.path.join(out2, "San_Zhang", coop.RECORD_NAME))
    def strip(text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        skip_blank = False
        for ln in lines:
            if "任务书附三" in ln:
                skip_blank = True
                continue
            if skip_blank and ln == "":
                skip_blank = False
                continue
            out.append(ln)
        return "\n".join(out)
    check("⑦-6 有 flag 时除附三行外记录与 HEAD 逐字节相同", r3.returncode == 0
          and strip(rec2) == strip(rec3))
    check("⑦-7 记录元信息与 state.fulltext 一致（字数/tokens）",
          f"{st2.get('fulltext', {}).get('chars')} 字" in rec2
          and f"约 {st2.get('fulltext', {}).get('tokens_est')} tokens" in rec2)


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
    tmp = tempfile.mkdtemp(prefix="slcn_b59_ft ")
    try:
        notes = os.path.join(tmp, "notes")
        os.makedirs(os.path.join(notes, "cases"), exist_ok=True)  # 无 bib_db/ → 不查库
        ENV["SLCN_NOTES"] = notes
        test_extract_unit()
        test_render_unit()
        head = ensure_head_engine(tmp)
        test_b276_frontmatter_fill(tmp)
        if head:
            test_no_flag_zero_change(tmp, head)
        else:
            skip("③ 无 flag 与 HEAD 对照", "git archive HEAD 失败")
        test_cli_context(tmp)
        test_cli_full_and_truncate(tmp)
        test_invalid(tmp)
        if head:
            test_ph2_record(tmp, head)
        else:
            skip("⑦ ph2 记录 HEAD 对照", "git archive HEAD 失败")
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
