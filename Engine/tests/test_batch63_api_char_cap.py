#!/usr/bin/env python3
r"""第六十三批 E1：通道②（直连 API）新增 `--llm-fulltext-max-chars` 可配字符上限。

覆盖（全部离线：只用 `tools/mock_llm_server.py`，不联网、零费用）：
  · 默认 40000：正文超上限 → **显式截断**、日志/报告醒目告警（不静默、不 exit2）；
    分片 chunk=min(30000, 上限)；只有前 40000 字发给端点（尾部标记不进 prompt）；
  · `0 = 不限`：整篇发送、报告无「字符上限」行；分片仍按 30000/片；
  · 小上限（5000）：chunk 跟随上限 → 只有 1 片；
  · 负值 → argparse exit 2；`--ai-budget-tokens` 费用闸不变（超限 exit2 零请求）；
  · `--help` 写明默认/0 语义。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch63_api_char_cap.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Mock:
    def __init__(self, *, mode: str = "normal"):
        self.port = _free_port()
        self.proc = subprocess.Popen(
            [sys.executable, _MOCK, "--port", str(self.port), "--mode", mode],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


_START = "起点标记-CAP-START-777"
_END = "尾部独有标记-CAP-END-888"
# 正文 ≈ 4.5 万字（> 默认上限 40000，且尾部标记在上限之后）。
_TEX_LONG = (r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
""" + _START + "\n\n" + ("填充正文。" * 9000) + "\n\n" + _END + "\n\\end{document}\n")

_TEX_SHORT = r"""\documentclass{article}
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


def fixture(tmp: str, tag: str, tex: str,
            bib: str | None = None) -> tuple[str, str | None, str]:
    # 字符上限测试不需要参考文献（避免 ⑪ 逐条请求混进 mock 的 n_requests）。
    d = os.path.join(tmp, tag)
    src = write(os.path.join(d, "src", "main.tex"), tex)
    bibp = write(os.path.join(d, "src", "refs.bib"), bib) if bib else None
    return src, bibp, os.path.join(d, "out")


def run_convert(src: str, out: str, bibp: str | None, extra: list[str], *,
                env: dict) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(_ENGINE, "convert.py"),
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


def _prompts(st: dict) -> str:
    return "\n".join(str(p.get("user") or "") for p in st.get("last_user_prompts") or [])


# 统一关掉自动应用：本组只验字符上限（与批60 report-only 报告格式对齐）。
_RO = ["--no-llm-fulltext-apply", "--ai-budget-tokens", "0"]


# ── ① 默认 40000：显式截断 + 告警，只发前 40000 字 ──────────────────────


def test_default_cap(tmp: str) -> None:
    src, bibp, out = fixture(tmp, "cap", _TEX_LONG)
    with Mock() as mock:
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext"] + _RO,
                        env=make_env(mock))
        st = mock.stats()
    rep = read(report_path(out))
    users = _prompts(st)
    check("①-1 默认上限截断时退出 0（不 exit2）", r.returncode == 0,
          (r.stdout + r.stderr)[-400:])
    check("①-2 日志醒目告警「只发送前 40000 字」",
          "只发送前 40000 字" in r.stdout and "显式截断" in r.stdout,
          r.stdout[-500:])
    check("①-3 报告写明「正文 X 字 > 上限 40000」「只覆盖前 40000 字」",
          "字符上限" in rep and "40000" in rep and "只覆盖前 40000 字" in rep)
    check("①-4 报告片数=端点收到次数（正文按上限截断后共 3 片）",
          "**3 片**" in rep and st.get("n_requests") == 3, st.get("n_requests"))
    check("①-5 起点标记（前 40000 字内）确实发给了端点", _START in users)


# ── ② 0 = 不限：整篇发送、无截断告警 ────────────────────────────────────


def test_zero_unlimited(tmp: str) -> None:
    src, bibp, out = fixture(tmp, "cap0", _TEX_LONG)
    with Mock() as mock:
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext",
                         "--llm-fulltext-max-chars", "0"] + _RO,
                        env=make_env(mock))
        st = mock.stats()
    rep = read(report_path(out))
    users = _prompts(st)
    check("②-1 0=不限：退出 0、无截断告警", r.returncode == 0
          and "只发送前" not in r.stdout)
    check("②-2 0=不限：报告没有「字符上限」行、正文完整 3 片",
          "字符上限" not in rep and "**3 片**" in rep)
    check("②-3 0=不限：端点收到次数=片数（整篇发送，未被截成更少片）",
          st.get("n_requests") == 3, st.get("n_requests"))


# ── ③ 小上限：chunk 跟随上限（min(30000, N)）────────────────────────────


def test_small_cap(tmp: str) -> None:
    src, bibp, out = fixture(tmp, "cap5k", _TEX_LONG)
    with Mock() as mock:
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext",
                         "--llm-fulltext-max-chars", "5000"] + _RO,
                        env=make_env(mock))
        st = mock.stats()
    rep = read(report_path(out))
    check("③-1 上限 5000：只发 1 片（chunk=min(30000,5000)）",
          r.returncode == 0 and st.get("n_requests") == 1
          and "**1 片**" in rep, st.get("n_requests"))
    check("③-2 报告写明只覆盖前 5000 字", "只覆盖前 5000 字" in rep)


# ── ④ 非法值 / 预算闸不变 ───────────────────────────────────────────────


def test_invalid_and_budget(tmp: str) -> None:
    src, bibp, out = fixture(tmp, "bad", _TEX_SHORT)
    env = make_env()
    r = run_convert(src, out, bibp,
                    ["--verify-refs", "--llm-fulltext-max-chars", "-1"], env=env)
    check("④-1 负值 → exit 2 且写明必须 ≥ 0",
          r.returncode == 2 and "必须 ≥ 0" in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-300:])

    with Mock() as mock:
        r2 = run_convert(src, out, bibp,
                         ["--verify-refs", "--llm-fulltext",
                          "--llm-fulltext-max-chars", "5000",
                          "--ai-budget-tokens", "1"], env=make_env(mock))
        n = mock.stats().get("n_requests")
    check("④-2 预算闸不变：超预算 exit 2、零请求、不出报告",
          r2.returncode == 2 and n == 0 and not os.path.isfile(report_path(out)),
          f"rc={r2.returncode} n={n}")

    r3 = subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"), "--help"],
                        cwd=_ENGINE, env=env, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=120)
    out_h = r3.stdout + r3.stderr
    check("④-3 --help 写明 --llm-fulltext-max-chars（默认 40000；0=不限）",
          "--llm-fulltext-max-chars" in out_h and "40000" in out_h
          and "0 = 不限" in out_h)


# ── ⑤ 未超上限（上限大于正文）：无截断标记 ───────────────────────────────


def test_no_truncation_when_under(tmp: str) -> None:
    src, bibp, out = fixture(tmp, "under", _TEX_SHORT)
    with Mock() as mock:
        r = run_convert(src, out, bibp,
                        ["--verify-refs", "--llm-fulltext",
                         "--llm-fulltext-max-chars", "100000"] + _RO,
                        env=make_env(mock))
    rep = read(report_path(out))
    check("⑤-1 上限大于正文：正常 0 退出、无「字符上限」行",
          r.returncode == 0 and "字符上限" not in rep and "只发送前" not in r.stdout)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="slcn_b63_cap ")
    try:
        global _NOTES
        notes = os.path.join(tmp, "notes")
        os.makedirs(os.path.join(notes, "cases"), exist_ok=True)
        _NOTES = notes
        test_default_cap(tmp)
        test_zero_unlimited(tmp)
        test_small_cap(tmp)
        test_invalid_and_budget(tmp)
        test_no_truncation_when_under(tmp)
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
