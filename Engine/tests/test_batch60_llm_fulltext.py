#!/usr/bin/env python3
r"""第六十批：全文 AI 核查通道②（`--llm-fulltext` 直连 API，report-only）判据测试。

Lead 2026-10-20 拍板（全部离线：只用 `tools/mock_llm_server.py`，不联网、零费用）：
  · 默认关：无 flag 时 ⑪ 行为/产物/日志与 HEAD（`git archive HEAD`）逐字节相同、零全文请求；
  · 启用：正文分片 ≤30000 字符/片，逐片请求（prompt 含片文本 + 该片低置信 ID），
    解析沿用 `coop.parse_low_confidence`，生成 `<stem>_AI全文复核.md`（**不落正文**）；
  · **report-only**：reference.bib/.tex 与关闭时逐字节相同；自动应用不开放；
  · 预算闸：发请求前估算，超 `--ai-budget-tokens` → exit 2 且**零请求**；
  · 未配置端点 → exit 2；非法组合（无 --verify-refs / --verify-plan / 协同两段）→ exit 2；
  · fail-soft：HTTP 500 / 截断 / 非 JSON / thinking 400 → 该片标不可用、退出码 0、产物照常。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch60_llm_fulltext.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
_MOCK = os.path.join(_REPO, "tools", "mock_llm_server.py")
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import coop, llm  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0
#: 受控空笔记仓（避免新旧引擎因本机真实 bib_db 命中差异而 ⑪ 行为不同）。
_NOTES = ""


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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Mock:
    """`tools/mock_llm_server.py` 子进程（只绑 127.0.0.1；可切剧本/夹具回放）。"""

    def __init__(self, *, mode: str = "normal", replies: str | None = None,
                 slow_seconds: float | None = None):
        self.port = _free_port()
        cmd = [sys.executable, _MOCK, "--port", str(self.port), "--mode", mode]
        if replies:
            cmd += ["--replies", replies]
        if slow_seconds is not None:
            cmd += ["--slow-seconds", str(slow_seconds)]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self.base = f"http://127.0.0.1:{self.port}/v1"
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                self.stats()
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
        raise RuntimeError("mock 未在 10s 内就绪")

    def stats(self) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/__stats",
                                    timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def make_env(mock: Mock | None = None) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES")):
            env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    if _NOTES:
        env["SLCN_NOTES"] = _NOTES
    if mock is not None:
        env["SLCN_LLM_BASE"] = mock.base
        env["SLCN_LLM_KEY"] = "mock-key-not-printed"
        env["SLCN_LLM_MODEL"] = "mock-model"
        env["SLCN_LLM_THINKING"] = "0"
    return env


# ── 夹具 ────────────────────────────────────────────────────────────────

_SENTINEL = "正文私有标记-BODY-12345"      # 只应出现在发给 mock 的请求里，不进报告
_BODY_ONLY = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
""" + _SENTINEL + r"""
第一段正文：这里没有任何引用命令，只用来测试全文复核的正文分片与摘要。
第二段正文：再写几句，确保正文超过两百字，便于观察分片与报告元信息。
\printbibliography
\end{document}
"""

_TEX_BIB = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文引用\parencite{Quine1970}，用于对照既有 ⑪ 逐条 AI 路径。

\printbibliography
\end{document}
"""

_BIB = """@article{Quine1970,
  author = {Quine, W. V. O.},
  title = {Philosophy of Logic},
  journal = {Mind},
  year = {1970},
}
"""

_JSON_REPLY = "```json\n" + json.dumps(
    {"id": "META-myvolnumber", "verdict": "corrected",
     "field": "myvolnumber", "value": "19", "span": "myvolnumber"},
    ensure_ascii=False) + "\n```\n"


def fixture(tmp: str, tag: str, tex: str, bib: str | None = None) -> tuple[str, str | None, str]:
    d = os.path.join(tmp, tag)
    src = write(os.path.join(d, "src", "main.tex"), tex)
    bibp = write(os.path.join(d, "src", "refs.bib"), bib) if bib is not None else None
    return src, bibp, os.path.join(d, "out")


def run_convert(src: str, out: str, bibp: str | None, extra: list[str], *,
                env: dict, engine: str = _ENGINE) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(engine, "convert.py"),
           "--source", src, "--out", out, "--author", "张三", "--lang", "zh",
           "--no-original"]
    if bibp:
        cmd += ["--bib", bibp]
    cmd += extra
    return subprocess.run(cmd, cwd=_ENGINE, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=240)


def out_dir(out: str) -> str:
    return os.path.join(out, "San_Zhang")


def report_path(out: str) -> str:
    return os.path.join(out_dir(out), "San_Zhang_AI全文复核.md")


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


# ── ① 帮助/参数校验 ─────────────────────────────────────────────────────


def test_help_and_invalid(tmp: str) -> None:
    r = subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"), "--help"],
                       cwd=_ENGINE, env=make_env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    out = r.stdout + r.stderr
    check("①-1 --help 退出 0", r.returncode == 0)
    check("①-2 --help 含 --llm-fulltext/--no-llm-fulltext-apply/--llm-fulltext-max-chars，"
          "写明 report-only 与预算闸",
          all(k in out for k in ("--llm-fulltext", "--no-llm-fulltext-apply",
                                 "--llm-fulltext-max-chars"))
          and "report-only" in out and "预算闸" in out)

    src, bibp, o = fixture(tmp, "bad", _BODY_ONLY)
    env = make_env()
    r1 = run_convert(src, o, bibp, ["--llm-fulltext"], env=env)
    check("①-3 缺 --verify-refs → exit 2 且提示只配合 --verify-refs",
          r1.returncode == 2 and "只配合 --verify-refs" in (r1.stdout + r1.stderr))
    r2 = run_convert(src, o, bibp, ["--verify-refs", "--verify-plan", "--llm-fulltext"],
                     env=dict(env, SLCN_LLM_BASE="http://127.0.0.1:9/v1",
                              SLCN_LLM_MODEL="m", SLCN_LLM_KEY="k"))
    check("①-4 与 --verify-plan 同用 → exit 2",
          r2.returncode == 2 and "verify-plan" in (r2.stdout + r2.stderr))
    r3 = run_convert(src, o, bibp, ["--coop-taskbook-only", "--llm-fulltext"],
                     env=dict(env, SLCN_LLM_BASE="http://127.0.0.1:9/v1",
                              SLCN_LLM_MODEL="m", SLCN_LLM_KEY="k"))
    check("①-5 与协同两段同用 → exit 2",
          r3.returncode == 2 and "协同两段式" in (r3.stdout + r3.stderr))
    r4 = run_convert(src, o, bibp, ["--verify-refs", "--llm-fulltext"], env=env)
    check("①-6 未配置可用端点 → exit 2 且明说未发起请求",
          r4.returncode == 2 and "未发起任何请求" in (r4.stdout + r4.stderr),
          (r4.stdout + r4.stderr)[-300:])
    check("①-7 未配置端点时不出报告/不写产物目录", not os.path.exists(report_path(o)))


# ── ② 默认关：零全文请求 + 与 HEAD 逐字节相同 ───────────────────────────

# B-004（2026-10-28 ZH-08 默认启用）预期日志差异：过滤 ZH-08 相关行后比较
import re as _re
_ZH08_LOG_NOISE = _re.compile(r"(ZH-08|判断指引：|· 第\d+\s*行：|自动修复 |一校可自动改|· ZH-\d+)")


def _strip_zh08_log(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not _ZH08_LOG_NOISE.search(ln))



def test_default_off(tmp: str, head: str) -> None:
    src, bibp, out = fixture(tmp, "off", _BODY_ONLY)
    with Mock(mode="normal") as mock:
        env = make_env(mock)
        new = run_convert(src, out, bibp, ["--verify-refs"], env=env)
        snap_new = snapshot(out_dir(out))
        stdout_new = new.stdout
        n_new = mock.stats()["n_requests"]
        prompts_new = mock.stats()["last_user_prompts"]
        shutil.rmtree(out, ignore_errors=True)
        old = run_convert(src, out, bibp, ["--verify-refs"], env=env, engine=head)
        snap_old = snapshot(out_dir(out))
        prompts_old = mock.stats()["last_user_prompts"]
        n_old_extra = mock.stats()["n_requests"] - n_new
    check("②-1 默认关转换退出 0", new.returncode == 0 and old.returncode == 0,
          new.stderr[-300:])
    check("②-2 默认关不生成全文复核报告", not os.path.exists(report_path(out)))
    check("②-3 默认关与 HEAD 产物逐字节相同", snap_new == snap_old)
    check("②-4 默认关与 HEAD 日志逐字节相同（B-004 ZH-08 行过滤）",
      _strip_zh08_log(_b276_mask(stdout_new))
          == _strip_zh08_log(_b276_mask(old.stdout)))
    check("②-5 默认关没有全文请求（请求里无「本片正文」）",
          not any("本片正文" in str(p.get("user") or "") for p in prompts_new))
    check("②-6 HEAD 引擎同样没有全文请求",
          not any("本片正文" in str(p.get("user") or "") for p in prompts_old))
    check("②-7 默认关请求数与 HEAD 相同（都=既有 ⑪ 行为，0 条 bib 时 0 请求）",
          n_new == 0 and n_old_extra == 0)


# ── ③ 启用成功路径：分片/请求/报告/report-only ──────────────────────────


def test_active_success(tmp: str) -> None:
    replies = write(os.path.join(tmp, "replies_ok.json"),
                    json.dumps({"": _JSON_REPLY}, ensure_ascii=False))
    src, bibp, out = fixture(tmp, "ok", _BODY_ONLY)
    # 先跑一次「关闭」拿基线产物
    with Mock(mode="normal") as mock_off:
        env_off = make_env(mock_off)
        run_convert(src, os.path.join(tmp, "ok", "out_off"), bibp, ["--verify-refs"],
                    env=env_off)
    base = snapshot(out_dir(os.path.join(tmp, "ok", "out_off")))
    with Mock(mode="replay", replies=replies) as mock:
        env = make_env(mock)
        # 第六十三批：默认自动应用 → 本组断言的是批60 report-only 口径，显式关掉。
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext", "--no-llm-fulltext-apply"],
                        env=env)
        st = mock.stats()
        rep = read(report_path(out)) if os.path.isfile(report_path(out)) else ""
    check("③-1 启用后退出 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    check("③-2 生成报告文件且非空", os.path.isfile(report_path(out)) and len(rep) > 200)
    check("③-3 报告写明 report-only、不改产物", "report-only" in rep
          and "不自动修改" in rep and "reference.bib" in rep)
    check("③-4 报告含端点/模型/分片/预算估算", "端点" in rep and "mock-model" in rep
          and "片" in rep and "预算估算" in rep)
    check("③-5 报告含逐条建议（ID/verdict/field/value/来源分片）",
          "`META-myvolnumber`" in rep and "verdict" in rep and "来源分片" in rep
          and "myvolnumber" in rep)
    check("③-6 报告不落正文全文（正文私有标记不出现）", _SENTINEL not in rep)
    _flag_snap = {k: v for k, v in snapshot(out_dir(out)).items()
                  if k != "San_Zhang_AI全文复核.md"}
    check("③-7 report-only：除报告外产物与关闭时逐字节相同", _flag_snap == base)
    check("③-8 请求数 = 分片数（小正文 1 片 → 1 次）",
          st["n_requests"] == 1, st["n_requests"])
    users = [str(p.get("user") or "") for p in st["last_user_prompts"]]
    check("③-9 请求 prompt 含片文本（正文标记发给了端点）",
          any(_SENTINEL in u for u in users))
    check("③-10 请求 prompt 含本片低置信 ID（META-myvolnumber）",
          any("META-myvolnumber" in u for u in users))
    check("③-11 请求 prompt 标注「本片正文」与片序号",
          any("本片正文（第 1/1 片" in u for u in users))
    check("③-12 日志有全文复核摘要行与报告文件名",
          "AI 全文复核" in r.stdout and "San_Zhang_AI全文复核.md" in r.stdout)
    check("③-13 日志/报告不含 API 密钥", "mock-key-not-printed" not in r.stdout
          and "mock-key-not-printed" not in rep)
    check("③-14 日志报告「0 片不可用」且给出建议条数",
          "0 片不可用" in r.stdout and "1 条建议" in r.stdout)


# ── ④ 多片：正文 40k 字 → 2 片、2 次请求、报告写两片 ─────────────────────


def test_multi_chunk(tmp: str) -> None:
    big = ("长段落用于分片测试。" * 300 + "\n\n") * 15   # 约 5 万字 → 至少 2 片
    tex = (r"\documentclass{article}\title{测试题名}\author{张三}\begin{document}"
           r"\maketitle" + "\n\n" + big + "\n\\end{document}\n")
    src, bibp, out = fixture(tmp, "multi", tex)
    replies = write(os.path.join(tmp, "replies_multi.json"),
                    json.dumps({"": _JSON_REPLY}, ensure_ascii=False))
    with Mock(mode="replay", replies=replies) as mock:
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext", "--no-llm-fulltext-apply"],
                        env=make_env(mock))
        st = mock.stats()
    rep = read(report_path(out)) if os.path.isfile(report_path(out)) else ""
    check("④-1 多片转换退出 0", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    check("④-2 片数 ≥ 2 且请求数 = 片数",
          st["n_requests"] >= 2 and "**3 片**" not in rep, st["n_requests"])
    check("④-3 报告分片明细列出每一片", rep.count("分片 ") >= 2 and "2/2" in rep)
    users = [str(p.get("user") or "") for p in st["last_user_prompts"]]
    check("④-4 每个请求带各自片序号（第 1/2 片、第 2/2 片）",
          any("第 1/2 片" in u for u in users) and any("第 2/2 片" in u for u in users))


# ── ⑤ 预算闸：超限 exit 2 且零请求 ──────────────────────────────────────


def test_budget(tmp: str) -> None:
    src, bibp, out = fixture(tmp, "budget", _BODY_ONLY)
    with Mock(mode="normal") as mock:
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext", "--ai-budget-tokens", "1"],
                        env=make_env(mock))
        n = mock.stats()["n_requests"]
    out_all = r.stdout + r.stderr
    check("⑤-1 超预算 → exit 2", r.returncode == 2, out_all[-300:])
    check("⑤-2 打印预计用量与「没有发起任何请求」",
          "全文复核预计用量" in out_all and "没有发起任何请求" in out_all)
    check("⑤-3 零请求（端点一次都没被调用）", n == 0, n)
    check("⑤-4 不出报告", not os.path.isfile(report_path(out)))


# ── ⑥ fail-soft：HTTP500 / 截断 / 非 JSON / thinking 400 ────────────────


def test_fail_soft(tmp: str) -> None:
    for tag, mode, kw in (("f500", "http500", {}),
                          ("flen", "length", {}),
                          ("fnormal", "normal", {})):
        src, bibp, out = fixture(tmp, tag, _BODY_ONLY)
        with Mock(mode=mode, **kw) as mock:
            r = run_convert(src, out, bibp, ["--verify-refs", "--llm-fulltext"],
                            env=make_env(mock))
            st = mock.stats()
        rep = read(report_path(out)) if os.path.isfile(report_path(out)) else ""
        check(f"⑥-{tag}-1 {mode} 下退出 0（fail-soft 继续出稿）", r.returncode == 0,
              (r.stdout + r.stderr)[-300:])
        check(f"⑥-{tag}-2 报告标出该片 AI 建议不可用",
              "AI 建议不可用" in rep and "0** 条通过校验建议" in rep)
        check(f"⑥-{tag}-3 报告列为分片失败原因", "## 二、分片明细" in rep
              and "⚠ AI 建议不可用" in rep)
        check(f"⑥-{tag}-4 端点确实收到请求（fail-soft 不是没调用）",
              st["n_requests"] >= 1)
        check(f"⑥-{tag}-5 产品 tex 仍在且未被 report-only 改动",
              os.path.isfile(os.path.join(out_dir(out), "San_Zhang.tex"))
              and "AI 全文复核" not in read(os.path.join(out_dir(out), "San_Zhang.tex")))

    # thinking 400：自动去字段重试一次（第二次成功则照常解析）
    src, bibp, out = fixture(tmp, "fthink", _BODY_ONLY)
    with Mock(mode="reject_thinking") as mock:
        env = make_env(mock)
        env["SLCN_LLM_THINKING"] = "1"
        r = run_convert(src, out, bibp, ["--verify-refs", "--llm-fulltext"], env=env)
        st = mock.stats()
    out_all = r.stdout + r.stderr
    check("⑥-think-1 thinking 400 → 自动去字段重试（2 次请求）",
          st["n_requests"] == 2, st["n_requests"])
    check("⑥-think-2 明说「端点不支持 thinking 字段」", "不支持 thinking 字段" in out_all)
    check("⑥-think-3 重试后仍 fail-soft（非 JSON → 不可用），退出 0",
          r.returncode == 0 and "AI 建议不可用" in read(report_path(out)))


# ── ⑦ 既有 ⑪ 逐条 AI 路径不变（有 bib 时与 HEAD 逐字节相同）─────────────


def test_existing_verify_path(tmp: str, head: str) -> None:
    src, bibp, out = fixture(tmp, "verify", _TEX_BIB, _BIB)
    with Mock(mode="normal") as mock:
        env = make_env(mock)
        new = run_convert(src, out, bibp, ["--verify-refs", "--no-ai-merge"], env=env)
        snap_new = snapshot(out_dir(out))
        _st_new = mock.stats()
        n_new = _st_new["n_requests"]
        shutil.rmtree(out, ignore_errors=True)
        old = run_convert(src, out, bibp, ["--verify-refs", "--no-ai-merge"],
                          env=env, engine=head)
        snap_old = snapshot(out_dir(out))
        n_old = mock.stats()["n_requests"] - n_new
    check("⑦-1 既有 --verify-refs 路径新/HEAD 均退出 0",
          new.returncode == 0 and old.returncode == 0)
    check("⑦-2 无 flag 时产物树与 HEAD 逐字节相同（含 AI 建议段）", snap_new == snap_old)
    check("⑦-3 无 flag 时日志与 HEAD 逐字节相同", _strip_zh08_log(_b276_mask(new.stdout)) == _strip_zh08_log(_b276_mask(old.stdout)))
    check("⑦-4 既有逐条 AI 请求数新/HEAD 相同（各 1 条 bib → 1 次）",
          n_new == 1 and n_old == 1, f"{n_new}/{n_old}")
    check("⑦-5 无 flag 时请求里没有全文分片标记（与全文通道互不影响）",
          not any("本片正文" in str(p.get("user") or "")
                  for p in _st_new["last_user_prompts"]))


# ── ⑨ 超时 fail-soft（直调 helper，timeout=1s；mock 慢 2s）────────────────


def test_timeout(tmp: str) -> None:
    try:
        import convert  # noqa: E402
    except Exception as e:  # noqa: BLE001
        skip("⑨ 超时 fail-soft", f"convert 不可导入：{type(e).__name__}: {e}")
        return
    with Mock(mode="slow", slow_seconds=2) as mock:
        cfg = llm.config_from_env(mock.base, "k", "mock-model")
        item = {"id": "META-myvolnumber", "kind": "metadata", "title": "",
                "evidence": "myvolnumber", "need": "", "key": "", "status": "pending",
                "fields": ["myvolnumber", "value"], "subkind": "",
                "target": ".tex", "macro": "myvolnumber", "current": ""}
        t0 = time.time()
        res = convert._llm_fulltext_review(
            cfg, ["测试正文片段"], [[item]], max_tokens=64, thinking=None, timeout=1)
        dt = time.time() - t0
    ch = (res.get("chunks") or [{}])[0]
    check("⑨-1 超时 → 该片标不可用（不抛异常）",
          res["n_requests"] == 1 and ch.get("status") == "unavailable"
          and bool(ch.get("error")), ch.get("error"))
    check("⑨-2 超时按指定 timeout 生效（<5s，不受默认 60s 影响）", dt < 5)
    check("⑨-3 失败分片进 failures 且建议为 0",
          len(res.get("failures") or []) == 1 and res.get("n_suggestions") == 0)


# ── ⑧ 报告归属：is_own_product 认它（第二次转换不被当外来文件）──────────


def test_report_is_own_product(tmp: str) -> None:
    try:
        import convert  # noqa: E402
    except Exception as e:  # noqa: BLE001
        skip("⑧-1 convert 可导入", f"{type(e).__name__}: {e}")
        return
    check("⑧-1 全文复核报告被认作本工具产物",
          convert.is_own_product("San_Zhang_AI全文复核.md", "San_Zhang") is True)
    check("⑧-2 其他未知 md 仍被当外来文件（不误收）",
          convert.is_own_product("San_Zhang_随便.md", "San_Zhang") is False)
    replies = write(os.path.join(tmp, "replies_own.json"),
                    json.dumps({"": _JSON_REPLY}, ensure_ascii=False))
    src, bibp, out = fixture(tmp, "own", _BODY_ONLY)
    with Mock(mode="replay", replies=replies) as mock:
        env = make_env(mock)
        r1 = run_convert(src, out, bibp, ["--verify-refs", "--llm-fulltext"], env=env)
        r2 = run_convert(src, out, bibp, ["--verify-refs", "--llm-fulltext"], env=env)
    both = r1.stdout + r2.stdout
    check("⑧-3 同目录第二次带 --llm-fulltext 转换不因报告文件被拒（无需 --force）",
          r1.returncode == 0 and r2.returncode == 0 and "拒绝覆盖" not in both,
          both[-400:])
    check("⑧-4 第二次转换后报告仍在", os.path.isfile(report_path(out)))


# ── main ────────────────────────────────────────────────────────────────



# ── B-276：标准 LaTeX 前言回填的显式取值断言（合成稿无 \my*）──────────────
def test_b276_frontmatter_fill(tmp: str) -> None:
    src, bibp, out = fixture(tmp, "b276", _BODY_ONLY)
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
    tmp = tempfile.mkdtemp(prefix="slcn_b60_ft ")
    try:
        global _NOTES
        notes = os.path.join(tmp, "notes")
        os.makedirs(os.path.join(notes, "cases"), exist_ok=True)  # 受控空笔记仓
        _NOTES = notes
        test_help_and_invalid(tmp)
        head = ensure_head_engine(tmp)
        test_b276_frontmatter_fill(tmp)
        if head:
            test_default_off(tmp, head)
            test_existing_verify_path(tmp, head)
        else:
            skip("②/⑦ HEAD 对照", "git archive HEAD 失败")
        test_active_success(tmp)
        test_multi_chunk(tmp)
        test_budget(tmp)
        test_fail_soft(tmp)
        test_report_is_own_product(tmp)
        test_timeout(tmp)
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
