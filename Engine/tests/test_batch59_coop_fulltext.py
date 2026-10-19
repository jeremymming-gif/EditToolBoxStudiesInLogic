#!/usr/bin/env python3
r"""第五十九批：全文 AI 核查通道①（`--coop-fulltext {context,full}` + 任务书附三）判据测试。

Lead 2026-10-20 拍板（全部离线：不联网、不调付费 API、不读稿件库、不跑 TeX）：
  · 默认关：不带 flag 时任务书/产物/日志与 HEAD（`git archive HEAD`）**逐字节相同**；
  · `context`：引用/脚注锚点 ±300 字窗口合并，上限 4 万字符，超限显式截断；
  · `full`：正文全文，上限 8 万字符，超限显式截断；
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


def snapshot(folder: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            out[os.path.relpath(p, folder)] = sha(p)
    return out


def make_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES")):
            env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
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
    check("①-9 context 超 4 万字 → 显式截断且留标记",
          bi["truncated"] and "已截断：原" in bi["text"]
          and bi["chars"] <= coop.FULLTEXT_LIMITS["context"] + 120
          and bi["orig_chars"] > coop.FULLTEXT_LIMITS["context"])
    bf = coop.extract_fulltext("Z" * (coop.FULLTEXT_LIMITS["full"] + 5000), "full")
    check("①-10 full 超 8 万字 → 显式截断且留标记",
          bf["truncated"] and "已截断：原" in bf["text"]
          and bf["chars"] <= coop.FULLTEXT_LIMITS["full"] + 120)
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
    check("②-6 截断时任务书有醒目提示", "已截断" in coop.render_fulltext_section(trunc))


# ── ③ CLI：无 flag 与 HEAD 逐字节相同（默认关零变化）─────────────────────


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
    check("③-2 无 flag 任务书与 HEAD 逐字节相同", tb_new == tb_old)
    check("③-3 无 flag 产物树（tex/bib/任务书）与 HEAD 逐字节相同", snap_new == snap_old)
    check("③-4 无 flag 日志与 HEAD 逐字节相同（除输出路径相同外）", new.stdout == old.stdout)
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
    check("⑤-5 context 超 4 万 → state.truncated=True", p2.returncode == 0
          and ft2.get("truncated") is True and ft2.get("chars", 0) > 0, str(ft2))
    check("⑤-6 任务书有显式截断标记（原 X 字/保留前 Y 字）",
          "已截断：原" in tb2 and "保留前" in tb2)
    check("⑤-7 context chars 不超过上限+标记长度",
          ft2["chars"] <= coop.FULLTEXT_LIMITS["context"] + 200)

    big_full = ("\\documentclass{article}\\title{测试题名}\\author{张三}\\begin{document}\n"
                + "长文填充。" * 20000 + "\n\\end{document}\n")
    s3, b3, o3 = _fixture(tmp, "bigfull", body=big_full)
    p3 = _ph1(s3, b3, o3, ["--coop-fulltext", "full"])
    ft3 = _state(o3).get("fulltext") or {}
    check("⑤-8 full 超 8 万 → 截断（显式标记）", p3.returncode == 0
          and ft3.get("truncated") is True and ft3.get("chars", 0) > 0, str(ft3))


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
          r_new.returncode == 0 and r_old.returncode == 0 and rec_new == rec_old)
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


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="slcn_b59_ft ")
    try:
        notes = os.path.join(tmp, "notes")
        os.makedirs(os.path.join(notes, "cases"), exist_ok=True)  # 无 bib_db/ → 不查库
        ENV["SLCN_NOTES"] = notes
        test_extract_unit()
        test_render_unit()
        head = ensure_head_engine(tmp)
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
