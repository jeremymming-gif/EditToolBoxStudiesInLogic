#!/usr/bin/env python3
r"""第六十三批 E1：通道①（外部 chat 任务书附三）**取消 4/8 万字符截断**。

覆盖（全部离线：不联网、不调付费 API、不跑 TeX）：
  · `coop.extract_fulltext` 批63 起不截断：`truncated` 恒 False，`chars==orig_chars`，
    超阈值只标 `over_limit`；`tokens_est` 体积估算保留；
  · 任务书附三超长时给「不截断、整篇发送」提示（不再出现「已截断」标记）；
  · CLI：`--coop-fulltext full` 超 8 万字的正文 → state 仍四键、truncated=False、
    正文完整进任务书；日志有「超长（未截断，整篇发送）」；
  · 无 flag 时默认路径与 HEAD（`git archive HEAD`）逐字节相同（默认零变化）。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch63_chat_no_limit.py
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

_TAIL = "全文尾部独有标记-FAR-END-2026"
_TEX_BIG = (r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
""" + ("长文填充。" * 20000) + _TAIL + "\n\\end{document}\n")

_TEX_SMALL = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文引用\parencite{Quine1970}。
\end{document}
"""

_BIB = """@article{Quine1970,
  author = {Quine, W. V. O.},
  title = {Philosophy of Logic},
  journal = {Mind},
  year = {1970},
}
"""


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


def _ph1(src: str, bibp: str, out: str, extra: list[str], *,
         engine: str = _ENGINE) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(engine, "convert.py"),
         "--source", src, "--bib", bibp, "--out", out, "--author", "张三",
         "--no-original", "--coop-taskbook-only"] + extra,
        cwd=_ENGINE, env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)


def _fixture(tmp: str, tag: str, tex: str) -> tuple[str, str, str]:
    d = os.path.join(tmp, tag)
    src = write(os.path.join(d, "src", "main.tex"), tex)
    bibp = write(os.path.join(d, "src", "refs.bib"), _BIB)
    return src, bibp, os.path.join(d, "out")


def _stage(out: str) -> str:
    return os.path.join(out, "San_Zhang_待核")


def _state(out: str) -> dict:
    p = os.path.join(_stage(out), coop.STATE_NAME)
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else {}


# ── ① extract_fulltext：批63 不截断（纯逻辑）──────────────────────────────


def test_extract_unit() -> None:
    body = "Z" * (coop.FULLTEXT_LIMITS["full"] + 5000)
    info = coop.extract_fulltext(body, "full")
    check("①-1 full 超 8 万字 → 不截断、内容完整",
          info["truncated"] is False and "已截断" not in info["text"]
          and info["text"] == body and info["chars"] == len(body)
          and info["orig_chars"] == len(body))
    check("①-2 超阈值只标 over_limit", info["over_limit"] is True)
    check("①-3 tokens_est 体积估算保留（== estimate_tokens(text)）",
          info["tokens_est"] == coop.estimate_tokens(info["text"])
          and info["tokens_est"] > 0)
    big_ctx = ("正文。" * 100 + r"\parencite{k1}") * 400
    ctx = coop.extract_fulltext(big_ctx, "context")
    check("①-4 context 超 4 万字 → 不截断",
          ctx["truncated"] is False and ctx["chars"] == ctx["orig_chars"]
          and ctx["orig_chars"] > coop.FULLTEXT_LIMITS["context"]
          and ctx["over_limit"] is True)
    small = coop.extract_fulltext(r"正文\parencite{k1}" + "x" * 100, "context")
    check("①-5 未超阈值 → over_limit=False", small["over_limit"] is False)
    check("①-6 state 兼容键仍在（truncated 恒 False）",
          small["truncated"] is False and "truncated" in small)


# ── ② render_fulltext_section：超长提示（不截断）─────────────────────────


def test_render_unit() -> None:
    info = coop.extract_fulltext("T" * (coop.FULLTEXT_LIMITS["full"] + 1000), "full")
    sec = coop.render_fulltext_section(info)
    check("②-1 超阈值任务书提示「不截断、整篇发送」",
          "不截断" in sec and "整篇发送" in sec, sec[-300:])
    check("②-2 任务书不再出现「已截断」标记", "已截断" not in sec)
    tiny = coop.extract_fulltext(r"正文\parencite{k1}", "context")
    sec2 = coop.render_fulltext_section(tiny)
    check("②-3 未超阈值不提示超长",
          "不截断" not in sec2 and "已截断" not in sec2)


# ── ③ CLI：超 8 万字 full 档 → 整篇进任务书、state 四键不变 ──────────────


def test_cli_full_big(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "bigfull", _TEX_BIG)
    p = _ph1(src, bibp, out, ["--coop-fulltext", "full"])
    check("③-1 超长 full ph1 退出 0", p.returncode == 0, p.stderr[-400:])
    tb = read(os.path.join(_stage(out), "San_Zhang_文献核实.md"))
    ft = _state(out).get("fulltext") or {}
    check("③-2 state 仍四键、truncated=False、chars>8 万",
          set(ft) == {"mode", "chars", "truncated", "tokens_est"}
          and ft.get("truncated") is False
          and ft.get("chars", 0) > coop.FULLTEXT_LIMITS["full"], str(ft))
    check("③-3 任务书附三含正文尾部（整篇发送、没有丢后半截）",
          coop.FULLTEXT_TITLE in tb and _TAIL in tb)
    check("③-4 任务书提示超长但明确「不截断」",
          "不截断" in tb and "已截断：原" not in tb)
    check("③-5 日志有「超长（未截断，整篇发送）」",
          "超长（未截断，整篇发送）" in p.stdout, p.stdout[-400:])


# ── ④ 默认零变化：无 flag 与 HEAD 逐字节相同 ─────────────────────────────


def test_no_flag_zero_change(tmp: str, head: str) -> None:
    src, bibp, out = _fixture(tmp, "noflag", _TEX_SMALL)
    new = _ph1(src, bibp, out, [])
    tb_new = read(os.path.join(_stage(out), "San_Zhang_文献核实.md"))
    snap_new = {k: v for k, v in snapshot(_stage(out)).items()
                if k != coop.STATE_NAME}
    if not head:
        skip("④-1/2 无 flag 与 HEAD 对照", "git archive HEAD 失败")
        return
    shutil.rmtree(out, ignore_errors=True)
    old = _ph1(src, bibp, out, [], engine=head)
    tb_old = read(os.path.join(_stage(out), "San_Zhang_文献核实.md"))
    snap_old = {k: v for k, v in snapshot(_stage(out)).items()
                if k != coop.STATE_NAME}
    check("④-1 无 flag 任务书与 HEAD 逐字节相同", _b276_mask(tb_new) == _b276_mask(tb_old))
    check("④-2 无 flag 产物与 HEAD 逐字节相同（默认零变化）", snap_new == snap_old)
    check("④-3 无 flag 任务书无附三", coop.FULLTEXT_TITLE not in tb_new)



# ── B-276：标准 LaTeX 前言回填的显式取值断言（合成稿无 \my*）──────────────
def test_b276_frontmatter_fill(tmp: str) -> None:
    src, bibp, out = _fixture(tmp, "b276", _TEX_SMALL)
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
    tmp = tempfile.mkdtemp(prefix="slcn_b63_chat ")
    try:
        notes = os.path.join(tmp, "notes")
        os.makedirs(os.path.join(notes, "cases"), exist_ok=True)  # 无 bib_db/ → 不查库
        ENV["SLCN_NOTES"] = notes
        test_extract_unit()
        test_render_unit()
        head = ensure_head_engine(tmp)
        test_b276_frontmatter_fill(tmp)
        test_cli_full_big(tmp)
        test_no_flag_zero_change(tmp, head)
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
