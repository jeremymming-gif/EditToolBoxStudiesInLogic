#!/usr/bin/env python3
r"""第六十三批 E1：通道②（`--llm-fulltext`）自动应用（默认开）+ `.ai前` 守护。

覆盖（全部离线：只用 `tools/mock_llm_server.py`，不联网、零费用）：
  · 默认开：`--llm-fulltext` 不附加任何开关 → 通过校验的 BIB/META/FN 建议自动应用到产物
    （复用批57 `apply_bib_suggestions` / `apply_tex_suggestions`：0 条不写、写前 `.ai前`、
    冲突拒绝、逐条记录）；`_AI全文复核.md` 仍生成并注明应用数/备份名；
  · `--no-llm-fulltext-apply`（及 `--no-ai-merge`）= 批60 report-only 口径：产物/日志/
    报告与 HEAD（`git archive HEAD`）逐字节相同；`--llm-fulltext-apply` 显式开=默认；
  · 守护：`.ai前` 已存在（模拟 ⑪ 逐条 AI 合并留下的回滚点）时**不覆盖**，报告注明；
  · 0 条可写不写盘 / 冲突拒绝 / fail-soft（HTTP 500）rc 仍 0、产物不动；
  · 无 `--llm-fulltext` 时默认路径与 HEAD 逐字节相同；新 apply flag 单独给 → 忽略+警告。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch63_fulltext_apply.py
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

from slcn import coop  # noqa: E402
from slcn import latex as _latex  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0
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
    out: dict[str, str] = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            out[os.path.relpath(p, folder)] = hashlib.sha256(_b276_mask(read(p)).encode("utf-8")).hexdigest()
    return out


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Mock:
    """`tools/mock_llm_server.py` 子进程（只绑 127.0.0.1；可切剧本/夹具回放）。"""

    def __init__(self, *, mode: str = "normal", replies: str | None = None):
        self.port = _free_port()
        cmd = [sys.executable, _MOCK, "--port", str(self.port), "--mode", mode]
        if replies:
            cmd += ["--replies", replies]
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


# ── 夹具：正文引用一个缺 year 的条目 → 低置信项 BIB-Key2000 ──────────────

_TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文引用\parencite{Key2000}，用于触发 BIB 条目的低置信项。
\printbibliography
\end{document}
"""

_BIB = """@article{Key2000,
  author = {Zhang, San},
  title = {A Test Article},
  journal = {Journal of Tests},
}
"""

_SUG = {"id": "BIB-Key2000", "verdict": "corrected", "field": "year",
        "value": "2000", "span": "Key2000"}
_REPLY_OK = "```json\n" + json.dumps(_SUG, ensure_ascii=False) + "\n```\n"
_REPLY_UNRESOLVED = "```json\n" + json.dumps(
    {"id": "BIB-Key2000", "verdict": "unresolved", "span": "Key2000"},
    ensure_ascii=False) + "\n```\n"


def fixture(tmp: str, tag: str) -> tuple[str, str, str]:
    d = os.path.join(tmp, tag)
    src = write(os.path.join(d, "src", "main.tex"), _TEX)
    bibp = write(os.path.join(d, "src", "refs.bib"), _BIB)
    return src, bibp, os.path.join(d, "out")


def replies_file(tmp: str, tag: str, text: str) -> str:
    # ⑪ 逐条 prompt 与全文分片 prompt 都带 `@article{Key2000,` → 都会取到 Key2000 键。
    return write(os.path.join(tmp, tag, "replies.json"),
                 json.dumps({"Key2000": text}, ensure_ascii=False))


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


def bib_out(out: str) -> str:
    return os.path.join(out_dir(out), "reference.bib")


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


def _head_supports_flag(engine: str, flag: str) -> bool:
    """HEAD 旧引擎的 convert.py 是否支持某个 flag（按源码判断，不执行引擎）。

    为什么要判断能力而不是写死命令行：`git archive HEAD` 取到的是**当前提交**——
    批63 提交前 HEAD=批62（裸 `--llm-fulltext` 就是 report-only），提交后 HEAD=批63
    （裸旗标=默认自动应用，必须显式 `--no-llm-fulltext-apply` 才是同一口径）。
    按能力选对照命令，两种 HEAD 下 ②-4/②-5 都是「report-only 与旧引擎逐字节相同」的
    有效守卫；HEAD==工作树时它退化为**同代码回归守卫**（仍能挡住后续改动悄悄改掉
    report-only 行为），所以不 skip。
    """
    # B-229：convert.py 已拆成门面 + slcn/convert_*.py 四个静态模块；按「HEAD 引擎
    # 当前是否支持该 flag」的**同一判据**拼读门面与 4 个模块（旧 HEAD 没有这些模块时
    # 行为与从前逐字节相同：只读 convert.py）。
    try:
        paths = [os.path.join(engine, "convert.py")]
        slcn_dir = os.path.join(engine, "slcn")
        if os.path.isdir(slcn_dir):
            paths += [os.path.join(slcn_dir, n)
                      for n in sorted(os.listdir(slcn_dir))
                      if n.startswith("convert_") and n.endswith(".py")]
        text = ""
        for path in paths:
            with open(path, encoding="utf-8", errors="replace") as f:
                text += f.read()
        return flag in text
    except OSError:
        return False


# ── ① 默认开：BIB 自动应用 + 报告 + .ai前 可逆 ───────────────────────────


def test_default_apply(tmp: str) -> None:
    replies = replies_file(tmp, "apply", _REPLY_OK)
    src, bibp, out = fixture(tmp, "apply")
    baseline = out + "_base"
    with Mock(mode="replay", replies=replies) as mock:
        env = make_env(mock)
        # report-only 基线：拿到「应用前」的输出 reference.bib（build 会把字段对齐，
        # 与源 refs.bib 逐字节不同，所以必须用产物基线比较）。
        run_convert(src, baseline, bibp,
                    ["--verify-refs", "--llm-fulltext", "--no-llm-fulltext-apply"],
                    env=env)
        pre = read(os.path.join(out_dir(baseline), "reference.bib"))
        r = run_convert(src, out, bibp, ["--verify-refs", "--llm-fulltext"], env=env)
    bo, backup = bib_out(out), bib_out(out) + ".ai前"
    rep = read(report_path(out)) if os.path.isfile(report_path(out)) else ""
    check("①-1 默认（无 apply 开关）退出 0", r.returncode == 0,
          (r.stdout + r.stderr)[-400:])
    check("①-2 默认自动应用：reference.bib 写入 year", os.path.isfile(bo)
          and "year = {2000}" in read(bo))
    check("①-3 写前 .ai前 存在且=应用前产物", os.path.isfile(backup)
          and read(backup) == pre)
    check("①-4 报告标题=自动应用、注明应用 1/跳过 0",
          "自动应用" in rep and "本次自动应用 **1** 条" in rep, rep[:200])
    check("①-5 报告第四节逐条记录 id/target/field/value",
          "## 四、自动应用记录" in rep and "`BIB-Key2000`" in rep
          and "year" in rep)
    check("①-6 日志有自动应用行与备份名",
          "自动应用 1 条建议" in r.stdout and "ai前" in r.stdout, r.stdout[-400:])
    check("①-7 报告不落正文全文", "正文引用" not in rep)
    ok = coop.restore_bib_backup(bo)
    check("①-8 .ai前 可逆（restore 后=应用前产物）", ok and read(bo) == pre)


def test_explicit_apply_flag(tmp: str) -> None:
    replies = replies_file(tmp, "explicit", _REPLY_OK)
    src, bibp, out = fixture(tmp, "explicit")
    with Mock(mode="replay", replies=replies) as mock:
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext", "--llm-fulltext-apply"],
                        env=make_env(mock))
    check("①′-1 显式 --llm-fulltext-apply 同样应用",
          r.returncode == 0 and "year = {2000}" in read(bib_out(out)),
          (r.stdout + r.stderr)[-300:])


# ── ② report-only 兼容：--no-llm-fulltext-apply 与 HEAD 逐字节相同 ───────


def test_report_only_compat(tmp: str, head: str) -> None:
    replies = replies_file(tmp, "ro", _REPLY_OK)
    src, bibp, out = fixture(tmp, "ro")
    if not head:
        skip("②-4/5 与 HEAD 逐字节对照", "git archive HEAD 失败")
    with Mock(mode="replay", replies=replies) as mock:
        env = make_env(mock)
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext", "--no-llm-fulltext-apply"],
                        env=env)
        snap = snapshot(out_dir(out))
        rep = read(report_path(out)) if os.path.isfile(report_path(out)) else ""
        check("②-1 --no-llm-fulltext-apply 退出 0", r.returncode == 0,
              (r.stdout + r.stderr)[-300:])
        check("②-2 不写 .ai前、不改 reference.bib",
              not os.path.exists(bib_out(out) + ".ai前")
              and "year = {2000}" not in read(bib_out(out)))
        check("②-3 报告仍为批60 report-only 口径",
              "report-only" in rep and "不自动修改" in rep
              and "自动应用记录" not in rep)
        if not head:
            return
        shutil.rmtree(out, ignore_errors=True)
        # 同一个 mock 进程（端口/端点 URL 相同）才谈得上逐字节对比。
        # 旧引擎的对照命令**按 HEAD 能力选**：批60 用裸 --llm-fulltext（=report-only），
        # 批63+ 必须显式 --no-llm-fulltext-apply 才是同一口径（见 _head_supports_flag）。
        _old_args = ["--verify-refs", "--llm-fulltext"]
        if _head_supports_flag(head, "--no-llm-fulltext-apply"):
            _old_args.append("--no-llm-fulltext-apply")
        r_old = run_convert(src, out, bibp, _old_args, env=env, engine=head)
    check("②-4 产物/报告与 HEAD（report-only 口径）逐字节相同",
          snapshot(out_dir(out)) == snap, sorted(set(snapshot(out_dir(out))) ^ set(snap)))
    check("②-5 日志与 HEAD 逐字节相同", _b276_mask(r.stdout) == _b276_mask(r_old.stdout))


def test_no_ai_merge_forces_report_only(tmp: str) -> None:
    replies = replies_file(tmp, "nam", _REPLY_OK)
    src, bibp, out = fixture(tmp, "nam")
    with Mock(mode="replay", replies=replies) as mock:
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext", "--no-ai-merge"],
                        env=make_env(mock))
    check("②′-1 --no-ai-merge → 全文通道只出报告、不写 .ai前",
          r.returncode == 0 and not os.path.exists(bib_out(out) + ".ai前")
          and "year = {2000}" not in read(bib_out(out)))
    check("②′-2 日志说明「--no-ai-merge：全文复核只出报告」",
          "--no-ai-merge：全文复核只出报告" in r.stdout, r.stdout[-300:])


# ── ③ 守护：已存在的 .ai前 不被覆盖 ─────────────────────────────────────


def test_backup_guard(tmp: str) -> None:
    replies = replies_file(tmp, "guard", _REPLY_OK)
    src, bibp, out = fixture(tmp, "guard")
    with Mock(mode="replay", replies=replies) as mock:
        r1 = run_convert(src, out, bibp, ["--verify-refs", "--llm-fulltext"],
                         env=make_env(mock))
    backup = bib_out(out) + ".ai前"
    check("③-1 首次应用生成 .ai前", r1.returncode == 0 and os.path.isfile(backup))
    sentinel = "SENTINEL-⑪-先写下的回滚点\n"
    write(backup, sentinel)                    # 模拟 ⑪ 逐条 AI 合并先写过
    with Mock(mode="replay", replies=replies) as mock:
        # 第二次跑同一目录：里面已有上一次 ⑪ 的《文献核实任务书》→ 目录守卫要求 --force
        r2 = run_convert(src, out, bibp,
                         ["--verify-refs", "--llm-fulltext", "--force"],
                         env=make_env(mock))
    rep = read(report_path(out))
    check("③-2 第二次运行退出 0 且仍应用", r2.returncode == 0
          and "year = {2000}" in read(bib_out(out)), (r2.stdout + r2.stderr)[-300:])
    check("③-3 已存在的 .ai前 未被覆盖（守护生效）", read(backup) == sentinel,
          read(backup)[:80].replace("\n", "\\n"))
    check("③-4 报告注明「沿用已存在的 .ai前（未覆盖）」",
          "沿用已存在的" in rep and "未覆盖" in rep, rep[:260])


# ── ④ 0 条可写 / 冲突拒绝 / fail-soft：都不写盘、rc 仍 0 ─────────────────


def test_zero_and_conflict_and_failsoft(tmp: str) -> None:
    # (a) unresolved → 0 条可写（0 条不写盘）
    replies = replies_file(tmp, "zero", _REPLY_UNRESOLVED)
    src, bibp, out = fixture(tmp, "zero")
    with Mock(mode="replay", replies=replies) as mock:
        r = run_convert(src, out, bibp, ["--verify-refs", "--llm-fulltext"],
                        env=make_env(mock))
    rep = read(report_path(out))
    check("④-1 unresolved → 0 条可写、不写盘、rc0",
          r.returncode == 0 and not os.path.exists(bib_out(out) + ".ai前")
          and "year = {2000}" not in read(bib_out(out)))
    check("④-2 日志/报告说明 0 条可写（0 条不写盘）",
          "0 条可写" in r.stdout and "（无：0 条不写盘）" in rep)

    # (b) 冲突：同一个 BIB 项按同一 field 回两个不同值 → parse 阶段整条拒绝
    bad = ("```json\n"
           + json.dumps({"id": "BIB-Key2000", "verdict": "corrected",
                         "field": "year", "value": "2000", "span": "Key2000"},
                        ensure_ascii=False) + "\n"
           + json.dumps({"id": "BIB-Key2000", "verdict": "corrected",
                         "field": "year", "value": "2001", "span": "Key2000"},
                        ensure_ascii=False) + "\n```\n")
    replies2 = replies_file(tmp, "conflict", bad)
    src2, bib2, out2 = fixture(tmp, "conflict")
    with Mock(mode="replay", replies=replies2) as mock:
        r2 = run_convert(src2, out2, bib2, ["--verify-refs", "--llm-fulltext"],
                         env=make_env(mock))
    rep2 = read(report_path(out2))
    check("④-3 重复 (id, field) → 整条拒绝、不写盘、rc0",
          r2.returncode == 0 and not os.path.exists(bib_out(out2) + ".ai前")
          and "year = {2000}" not in read(bib_out(out2)))
    check("④-4 报告「三、被拒绝」写明重复回复原因",
          "被拒绝 / 白名单外" in rep2 and "重复回复" in rep2, rep2[-500:])

    # (c) fail-soft：端点 HTTP 500 → 该片不可用、rc0、不写盘
    src3, bib3, out3 = fixture(tmp, "fail")
    with Mock(mode="http500") as mock:
        r3 = run_convert(src3, out3, bib3, ["--verify-refs", "--llm-fulltext"],
                         env=make_env(mock))
    rep3 = read(report_path(out3))
    check("④-5 HTTP 500 → rc0（fail-soft）、标 AI 建议不可用",
          r3.returncode == 0 and "AI 建议不可用" in rep3,
          (r3.stdout + r3.stderr)[-300:])
    check("④-6 fail-soft 不写产物/不留 .ai前",
          not os.path.exists(bib_out(out3) + ".ai前")
          and "year = {2000}" not in read(bib_out(out3)))


# ── ⑤ 复用批57安全闸（BIB/META/FN 分派，unit）────────────────────────────


_MIN_TEX = r"""\documentclass{article}
\newcommand{\myvolnumber}{x}
\newcommand{\mypubyear}{xxxx}
\begin{document}
正文脚注：\footnote{见张三 2020 的论述}。
\end{document}
"""


def _item(id_: str, kind: str, *, evidence: str = "x", fields=None, key: str = "",
          subkind: str = "", macro: str = "", current: str = "") -> dict:
    return {"id": id_, "kind": kind, "title": id_, "evidence": evidence,
            "need": "", "fields": list(fields or []), "key": key,
            "status": "pending", "subkind": subkind, "macro": macro,
            "current": current}


def _sug(id_: str, field_: str, value: str, *, verdict: str = "corrected",
         span: str = "x", **extra) -> dict:
    return dict({"id": id_, "verdict": verdict, "field": field_,
                 "value": value, "span": span, "note": ""}, **extra)


def test_unit_dispatch(tmp: str) -> dict:
    d = os.path.join(tmp, "unit")
    os.makedirs(d, exist_ok=True)
    bib = write(os.path.join(d, "reference.bib"), _BIB)
    tp = write(os.path.join(d, "p.tex"), _MIN_TEX)
    items = [
        _item("BIB-Key2000", "bib_entry", evidence=_BIB, fields=["year"],
              key="Key2000"),
        _item("META-myvolnumber", "metadata", evidence="myvolnumber",
              fields=["myvolnumber", "value"], macro="myvolnumber", current="x"),
        _item("FN-12", "footnote", evidence="见", fields=["note", "match_key"],
              subkind="citation_unmatched"),
        _item("BIB-LINE-01", "bib_line", evidence="x", fields=["author"]),
    ]
    accepted = [
        _sug("BIB-Key2000", "year", "2000", span="Key2000"),
        _sug("META-myvolnumber", "myvolnumber", "19", span="myvolnumber"),
        _sug("FN-12", "match_key", "Key2000", span="见"),
        _sug("BIB-LINE-01", "author", "张三", span="x"),
    ]
    out = coop.apply_fulltext_suggestions(
        accepted=accepted, items=items, bib_path=bib, tex_path=tp,
        taskbook_keys=["Key2000"])
    text = read(tp)
    check("⑤-1 三类分派：BIB/META/FN 共应用 3 条", out.get("n_applied") == 3,
          str(out.get("n_applied")))
    check("⑤-2 BIB 字段写入 reference.bib+备份", "year = {2000}" in read(bib)
          and os.path.isfile(bib + ".ai前"))
    check("⑤-3 META 写入输出 .tex 的 \\my*+备份",
          _latex.get_macro_value(text, "myvolnumber") == "19"
          and os.path.isfile(tp + ".ai前"), text[:160])
    check("⑤-4 FN 脚注提升为 \\parencite",
          "\\parencite{Key2000}" in text and "\\footnote" not in text)
    check("⑤-5 非 BIB/META/FN 只记录、不写盘",
          any(s.get("id") == "BIB-LINE-01" for s in out.get("skipped") or [])
          and out.get("n_skipped") == 1, str(out.get("skipped")))

    # 0 条建议 → 一个文件都不碰
    bib2 = write(os.path.join(d, "reference2.bib"), _BIB)
    tp2 = write(os.path.join(d, "p2.tex"), _MIN_TEX)
    out2 = coop.apply_fulltext_suggestions(
        accepted=[], items=items, bib_path=bib2, tex_path=tp2,
        taskbook_keys=["Key2000"])
    check("⑤-6 0 条建议 → 不写盘、不留 .ai前",
          out2.get("n_applied") == 0 and not os.path.exists(bib2 + ".ai前")
          and not os.path.exists(tp2 + ".ai前")
          and read(bib2) == _BIB and read(tp2) == _MIN_TEX)

    # META 同字段多值冲突 → 全部拒绝（批57 判据）
    tp3 = write(os.path.join(d, "p3.tex"), _MIN_TEX)
    items3 = [
        _item("META-myvolnumber", "metadata", fields=["myvolnumber", "value"],
              macro="myvolnumber", current="x"),
        _item("META-LABEL-9", "metadata", fields=["myvolnumber", "value"],
              macro="myvolnumber", current="x"),
    ]
    out3 = coop.apply_fulltext_suggestions(
        accepted=[_sug("META-myvolnumber", "myvolnumber", "19"),
                  _sug("META-LABEL-9", "myvolnumber", "20")],
        items=items3, bib_path="", tex_path=tp3, taskbook_keys=[])
    check("⑤-7 META 同字段多值 → 全部拒绝、.tex 未改",
          out3.get("n_applied") == 0 and read(tp3) == _MIN_TEX
          and any("多个不同值" in str(s.get("reason") or "")
                  for s in out3.get("skipped") or []), str(out3.get("skipped")))
    return out


# ── ⑥ 默认路径零变化（无 --llm-fulltext 与 HEAD 逐字节相同）───────────────


def test_default_zero_change(tmp: str, head: str) -> None:
    src, bibp, out = fixture(tmp, "zerochange")
    env = make_env()
    new = run_convert(src, out, bibp, ["--verify-refs", "--no-ai-merge"], env=env)
    snap_new = snapshot(out_dir(out))
    if not head:
        skip("⑥-1/2 默认零变化 HEAD 对照", "git archive HEAD 失败")
        return
    shutil.rmtree(out, ignore_errors=True)
    old = run_convert(src, out, bibp, ["--verify-refs", "--no-ai-merge"],
                      env=env, engine=head)
    check("⑥-1 无 --llm-fulltext：产物与 HEAD 逐字节相同",
          new.returncode == 0 and old.returncode == 0
          and snapshot(out_dir(out)) == snap_new)
    check("⑥-2 无 --llm-fulltext：日志与 HEAD 逐字节相同",
          _b276_mask(new.stdout) == _b276_mask(old.stdout))


def test_apply_flag_without_llm_fulltext(tmp: str) -> None:
    src, bibp, out = fixture(tmp, "misuse")
    env = make_env()
    r = run_convert(src, out, bibp, ["--verify-refs", "--no-ai-merge",
                                     "--no-llm-fulltext-apply"], env=env)
    check("⑥′-1 单独给 --no-llm-fulltext-apply（无 --llm-fulltext）→ 忽略+警告、rc0",
          r.returncode == 0 and "只配合 --llm-fulltext" in r.stderr,
          (r.stdout + r.stderr)[-300:])



# ── B-276：标准 LaTeX 前言回填的显式取值断言（合成稿无 \my*）──────────────
def test_b276_frontmatter_fill(tmp: str) -> None:
    src, bibp, out = fixture(tmp, "b276")
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
    tmp = tempfile.mkdtemp(prefix="slcn_b63_apply ")
    try:
        global _NOTES
        notes = os.path.join(tmp, "notes")
        os.makedirs(os.path.join(notes, "cases"), exist_ok=True)  # 受控空笔记仓
        _NOTES = notes
        head = ensure_head_engine(tmp)
        test_b276_frontmatter_fill(tmp)
        test_default_apply(tmp)
        test_explicit_apply_flag(tmp)
        test_report_only_compat(tmp, head)
        test_no_ai_merge_forces_report_only(tmp)
        test_backup_guard(tmp)
        test_zero_and_conflict_and_failsoft(tmp)
        test_unit_dispatch(tmp)
        test_default_zero_change(tmp, head)
        test_apply_flag_without_llm_fulltext(tmp)
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
