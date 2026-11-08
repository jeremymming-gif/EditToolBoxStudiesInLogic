#!/usr/bin/env python3
r"""P02 包（2.2-E）：初排与 AI 回填「产物安全」交付回归测试。

覆盖（全部离线：不联网、不调付费 API、不读稿件库、不跑 TeX）：
  ① ph1 只写暂存、最终交付目录不存在、stdout 明说「最终交付目录本次未写」；
  ② 人工元数据 / 参数指纹进 state（与 `coop.parameter_fingerprint` 对账）；
  ③ ph2 指纹不符 exit 2 且产物不动、暂存保留、state 仍 ph1；
  ④ 已交付重跑 exit 2（找不到可续跑暂存）且不重复并入 reference.bib；
  ⑤ 部分有效回填：有效条目并入、非法/未知键给拒绝原因、未回填条目的原字段保留；
  ⑥ E-1（R09）：无锚点 + `--coop-fulltext context` 的任务书必须披露「退回全文」，
     不得再写「节选…共 0 段」；
  ⑦ `--coop-fulltext full` 不受影响；有锚点 context 仍是窗口合并文案；
  ⑨ R04：回复自带 TASK-ID 与本次任务书不符 → 整份拒收（含低置信项），最终目录不写、暂存 bib 不变；
  ⑩ 混合回复：匹配 TASK-ID 的合法文件照常并入，错 TASK-ID 文件整份不采用且给出拒收原因；
  ⑪ 旧格式回复（没有 TASK-ID 行）仍被接受（旧格式兼容边界不收紧）。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_pkg_p02_delivery.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import coop  # noqa: E402

FAILS: list[str] = []
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def write_json(path: str, obj) -> str:
    return write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def make_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_")):
            env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    env["SLCN_MANUSCRIPTS"] = os.environ.get("SLCN_MANUSCRIPTS", "")
    env["SLCN_NOTES"] = os.environ.get("SLCN_NOTES", "")
    return env


def run_convert(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(_ENGINE, "convert.py")] + argv,
        cwd=_ENGINE, env=make_env(), capture_output=True, text=True, timeout=300)


def last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# ── 夹具（合成语料，不是真实投稿）────────────────────────────────────────

_TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文引用\parencite{k1}，另一处脚注。\footnote{李四 2021。}
\begin{thebibliography}{9}
\bibitem{k1} 张三. 测试文献一[J]. 逻辑学研究, 2020.
\bibitem{k2} 李四. 测试文献二[J]. 逻辑学研究, 2021.
\end{thebibliography}
\end{document}
"""

#: E-1 夹具：正文里**没有任何** \cite/\parencite/\footnote 锚点。
_TEX_NOANCHOR = r"""\documentclass{article}
\title{无锚点稿件}
\author{王五}
\begin{document}
\section{引言}
反事实条件句的语义依赖于相似性排序，这一节只讨论语义，不引用任何文献，也没有脚注。
第二段继续讨论相似性排序与球体模型的差别，同样不含任何文献引用命令。
\end{document}
"""

_BIB = """@article{k1,
  author = {张三},
  journal = {逻辑学研究},
  year = {2020},
}
@article{k2,
  author = {李四},
  title = {测试文献二},
  journal = {逻辑学研究},
  year = {2021},
}
"""

_REPLY = """% 来源：合成夹具（P02-2.2E 回归测试）
```bibtex
@article{k1,
  author  = {张三},
  title   = {测试文献一（回填版）},
  journal = {逻辑学研究},
  year    = {2020},
}
```
"""

_REPLY_PARTIAL = """```bibtex
@article{k1,
  author  = {张三},
  title   = {测试文献一（回填版）},
  journal = {逻辑学研究},
  year    = {2020},
}
```

```bibtex
@article{k99,
  author  = {不在任务书里的人},
  title   = {未知键条目},
  year    = {1999},
}
```

```bibtex
@article{k2,
  author  = {李四},
  title   = {花括号没闭合
```
"""

_FORM = {
    "schema_version": 1,
    "title_cn": "人工题名",
    "keywords_cn": "甲；乙",
    "authors": [
        {"name_cn": "张三", "affiliations_cn": ["甲大学"], "email": "z@x.cn"},
    ],
}


def _fixture(tmp: str, tag: str, tex: str = _TEX, bib: str = _BIB) -> tuple[str, str, str]:
    d = os.path.join(tmp, tag)
    src = write(os.path.join(d, "src", "main.tex"), tex)
    bibp = write(os.path.join(d, "src", "refs.bib"), bib)
    return src, bibp, os.path.join(d, "out")


def ph1_args(src: str, bib: str, out: str, extra: list[str] | None = None) -> list[str]:
    args = ["--source", src, "--bib", bib, "--out", out, "--author", "张三",
            "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"]
    return args + list(extra or [])


def ph2_args(src: str, out: str, reply: str, extra: list[str] | None = None) -> list[str]:
    args = ["--source", src, "--out", out, "--coop-replies", reply, "--no-original"]
    return args + list(extra or [])


def stage_dir(out: str) -> str:
    cands = [os.path.join(out, n) for n in sorted(os.listdir(out))
             if n.endswith("_待核")] if os.path.isdir(out) else []
    return cands[0] if cands else ""


# ── ① ph1 只写暂存 ───────────────────────────────────────────────────────


def test_ph1_staging_only(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "t1")
    r = run_convert(ph1_args(src, bib, out))
    blob = r.stdout + r.stderr
    check("①-1 ph1 退出 0", r.returncode == 0, blob[-400:])
    final = os.path.join(out, "San_Zhang")
    check("①-2 最终交付目录不存在", not os.path.exists(final), final)
    check("①-3 stdout 明说最终目录未写", "最终交付目录本次**未写**" in r.stdout)
    stage = stage_dir(out)
    check("①-4 暂存目录存在且有状态文件",
          bool(stage) and os.path.isfile(os.path.join(stage, coop.STATE_NAME)), stage)
    check("①-5 stdout 最后一行是暂存目录",
          last_line(r.stdout).startswith("暂存目录："), last_line(r.stdout))
    check("①-6 ph1 不打印交付契约行", "完成。产物目录" not in r.stdout)
    check("①-7 state.phase=ph1",
          json.loads(read(os.path.join(stage, coop.STATE_NAME)))["phase"] == "ph1")


# ── ② 人工元数据 / 指纹进 state ──────────────────────────────────────────


def test_state_metadata(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "t2")
    form = write_json(os.path.join(tmp, "t2", "form.json"), _FORM)
    r = run_convert(ph1_args(src, bib, out, ["--metadata-json", form]))
    check("②-1 ph1 + --metadata-json 退出 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    stage = stage_dir(out)
    st = json.loads(read(os.path.join(stage, coop.STATE_NAME)))
    check("②-2 state 有 params.fingerprint",
          isinstance(st.get("params", {}).get("fingerprint"), str)
          and len(st["params"]["fingerprint"]) == 64)
    check("②-3 state 顶层 fingerprint 与 params 指纹一致",
          st.get("fingerprint") == st["params"]["fingerprint"])
    check("②-4 参数指纹可重算",
          coop.parameter_fingerprint(st["params"]) == st["params"]["fingerprint"])
    sm = st.get("structured_metadata") or {}
    check("②-5 structured_metadata 记录来源与 sha256",
          sm.get("schema") == "metadata.v1" and len(sm.get("sha256") or "") == 64,
          sm)
    check("②-6 sha256 与值对账",
          sm.get("sha256") == coop.structured_metadata_sha256(sm.get("values") or {}))


# ── ③ 指纹不符 ───────────────────────────────────────────────────────────


def test_fingerprint_mismatch(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "t3")
    r1 = run_convert(ph1_args(src, bib, out))
    check("③-1 ph1 退出 0", r1.returncode == 0, (r1.stdout + r1.stderr)[-300:])
    stage = stage_dir(out)
    bib_in_stage = os.path.join(stage, "reference.bib")
    before = sha(bib_in_stage)
    # 源稿改一个字符（模拟第一段后有人动了源稿）
    with open(src, "a", encoding="utf-8", newline="\n") as f:
        f.write("% 源稿改动\n")
    reply = write(os.path.join(tmp, "t3", "reply.md"), _REPLY)
    r2 = run_convert(ph2_args(src, out, reply))
    blob = r2.stdout + r2.stderr
    check("③-2 指纹不符 exit 2", r2.returncode == 2, r2.returncode)
    check("③-3 明说参数/源稿不一致", "不一致" in blob, blob[-400:])
    check("③-4 最终交付目录仍不存在", not os.path.exists(os.path.join(out, "San_Zhang")))
    check("③-5 暂存保留且 state 仍 ph1",
          json.loads(read(os.path.join(stage, coop.STATE_NAME)))["phase"] == "ph1")
    check("③-6 暂存 reference.bib 未被改", sha(bib_in_stage) == before)


# ── ④ 已交付后重跑：拒绝 + 不重复并入 ────────────────────────────────────


def test_rerun_after_delivery(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "t4")
    r1 = run_convert(ph1_args(src, bib, out))
    check("④-1 ph1 退出 0", r1.returncode == 0, (r1.stdout + r1.stderr)[-300:])
    reply = write(os.path.join(tmp, "t4", "reply.md"), _REPLY)
    r2 = run_convert(ph2_args(src, out, reply))
    final = os.path.join(out, "San_Zhang")
    check("④-2 ph2 交付成功 exit 0", r2.returncode == 0, (r2.stdout + r2.stderr)[-500:])
    check("④-3 最终目录存在且含记录/回读报告",
          os.path.isfile(os.path.join(final, coop.RECORD_NAME))
          and os.path.isfile(os.path.join(final, "San_Zhang_回读校验报告.md")))
    check("④-4 stdout 最后一行是交付契约行",
          last_line(r2.stdout).startswith("完成。产物目录："), last_line(r2.stdout))
    delivered_bib = os.path.join(final, "reference.bib")
    bib_sha = sha(delivered_bib)
    check("④-5 回填已并入交付 bib", "回填版" in read(delivered_bib))
    check("④-6 暂存已清理", not stage_dir(out))
    # 同一份回填再跑一次：必须拒绝，且交付 bib 一个字节都不变（不重复并入）
    r3 = run_convert(ph2_args(src, out, reply))
    blob3 = r3.stdout + r3.stderr
    check("④-7 已交付重跑 exit 2", r3.returncode == 2, r3.returncode)
    check("④-8 明说找不到可续跑暂存/请先运行第一段",
          ("找不到可续跑的协同初排暂存目录" in blob3) or ("请先运行第一段" in blob3),
          blob3[-400:])
    check("④-9 交付 bib 未被重复并入", sha(delivered_bib) == bib_sha)


# ── ⑤ 部分有效回填 ───────────────────────────────────────────────────────


def test_partial_replies(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "t5")
    r1 = run_convert(ph1_args(src, bib, out))
    check("⑤-1 ph1 退出 0", r1.returncode == 0, (r1.stdout + r1.stderr)[-300:])
    stage = stage_dir(out)
    orig_bib = read(os.path.join(stage, "reference.bib"))
    reply = write(os.path.join(tmp, "t5", "reply.md"), _REPLY_PARTIAL)
    r2 = run_convert(ph2_args(src, out, reply))
    blob = r2.stdout + r2.stderr
    final = os.path.join(out, "San_Zhang")
    check("⑤-2 部分有效仍交付 exit 0", r2.returncode == 0, blob[-500:])
    got = read(os.path.join(final, "reference.bib"))
    check("⑤-3 有效条目并入", "回填版" in got)
    check("⑤-4 未知键被拒且给了原因",
          ("任务书外" in blob) or ("白名单" in blob) or ("已忽略，不猜" in blob), blob[-400:])
    check("⑤-5 非法块被拒且给了原因", "拒绝" in blob, blob[-400:])
    # k2 未回填（回复里 k2 是坏块）：交付 bib 里 k2 必须保持初排原字段
    k2_orig = re.search(r"@article\{k2,.*?\n\}", orig_bib, re.S)
    k2_got = re.search(r"@article\{k2,.*?\n\}", got, re.S)
    check("⑤-6 未回填条目的原字段保留",
          bool(k2_orig) and bool(k2_got)
          and "测试文献二" in k2_got.group(0)
          and "花括号没闭合" not in k2_got.group(0))


# ── ⑧ P02E-2：`.ai前` 只代表「本次之前」（回滚点不被中间态覆盖）──────────


def test_aiqian_keeps_original(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "t9")
    r1 = run_convert(ph1_args(src, bib, out))
    check("⑧-1 ph1 退出 0", r1.returncode == 0, (r1.stdout + r1.stderr)[-300:])
    stage = stage_dir(out)
    stage_bib = os.path.join(stage, "reference.bib")
    orig = open(stage_bib, "rb").read()
    orig_sha = hashlib.sha256(orig).hexdigest()
    st = json.loads(read(os.path.join(stage, coop.STATE_NAME)))
    item = next((it for it in st.get("low_confidence") or [] if it.get("id") == "BIB-k1"), None)
    check("⑧-2 初排给了 BIB-k1 低置信项", bool(item), [i.get("id") for i in st.get("low_confidence") or []])
    if not item:
        return
    evidence = str(item.get("evidence") or "")
    span = evidence[:4] if evidence else "k1"
    reply = write(os.path.join(tmp, "t9", "reply.md"), _REPLY + "\n```json\n" + json.dumps({
        "id": "BIB-k1", "verdict": "corrected", "field": "title",
        "value": "测试文献一（回填版·低置信）", "span": span,
    }, ensure_ascii=False) + "\n```\n")
    r2 = run_convert(ph2_args(src, out, reply, ["--coop-apply"]))
    blob = r2.stdout + r2.stderr
    check("⑧-3 ph2 + --coop-apply 退出 0", r2.returncode == 0, blob[-600:])
    final = os.path.join(out, "San_Zhang")
    aiqian = os.path.join(final, "reference.bib.ai前")
    check("⑧-4 交付目录有 reference.bib.ai前", os.path.isfile(aiqian))
    got = hashlib.sha256(open(aiqian, "rb").read()).hexdigest() if os.path.isfile(aiqian) else ""
    check("⑧-5 .ai前 仍是「本次之前」的原件（= ph1 暂存 bib 原字节）", got == orig_sha,
          f"want {orig_sha[:12]} got {got[:12]}")
    check("⑧-6 交付 bib 已应用回填与低置信建议",
          "回填版·低置信" in read(os.path.join(final, "reference.bib")))
    ok = coop.restore_bib_backup(os.path.join(final, "reference.bib"))
    check("⑧-7 restore_bib_backup 可复原到原件",
          ok and open(os.path.join(final, "reference.bib"), "rb").read() == orig)


# ── ⑥/⑦ E-1：附三「退回全文」披露 ───────────────────────────────────────


def test_fulltext_fallback_label(tmp: str) -> None:
    # 单元层：fallback 只增键，windows 语义不变。
    info_no = coop.extract_fulltext("正文没有锚点", mode="context")
    check("⑥-0a 无锚点 context：windows 仍为 0", info_no.get("windows") == 0, info_no.get("windows"))
    check("⑥-0b 无锚点 context：fallback=True", info_no.get("fallback") is True)
    info_anchor = coop.extract_fulltext("正文\\parencite{k1} 之后", mode="context")
    check("⑥-0c 有锚点 context：fallback=False", info_anchor.get("fallback") is False)
    check("⑥-0d 空正文 context：fallback=False",
          coop.extract_fulltext("", mode="context").get("fallback") is False)
    check("⑥-0e full 档：fallback=False",
          coop.extract_fulltext("正文", mode="full").get("fallback") is False)

    # 端到端：无锚点稿件 + --coop-fulltext context → 任务书必须说「退回全文」。
    src, bib, out = _fixture(tmp, "t6", tex=_TEX_NOANCHOR)
    r = run_convert(ph1_args(src, bib, out, ["--coop-fulltext", "context"]))
    check("⑥-1 ph1(context, 无锚点) 退出 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    check("⑥-2 日志披露整篇退回",
          any("整篇" in ln and "无锚点" in ln for ln in r.stdout.splitlines()),
          [ln for ln in r.stdout.splitlines() if "附三" in ln])
    stage = stage_dir(out)
    tb = read(os.path.join(stage, "San_Zhang_文献核实.md"))
    ft = tb[tb.find("## 附三"):]
    check("⑥-3 任务书披露「退回全文」", "退回全文" in ft)
    check("⑥-4 任务书不再写「共 0 段」", "共 0 段" not in ft)
    check("⑥-5 整篇正文确实在附三里", "反事实条件句的语义" in ft)
    st = json.loads(read(os.path.join(stage, coop.STATE_NAME)))
    check("⑥-6 state.fulltext 不新增 fallback 键（schema 不变）",
          "fallback" not in (st.get("fulltext") or {}))

    # ⑦ full 档不受影响；有锚点 context 仍是窗口合并。
    src2, bib2, out2 = _fixture(tmp, "t7", tex=_TEX_NOANCHOR)
    r2 = run_convert(ph1_args(src2, bib2, out2, ["--coop-fulltext", "full"]))
    check("⑦-1 ph1(full) 退出 0", r2.returncode == 0, (r2.stdout + r2.stderr)[-300:])
    tb2 = read(os.path.join(stage_dir(out2), "San_Zhang_文献核实.md"))
    ft2 = tb2[tb2.find("## 附三"):]
    check("⑦-2 full 档写「正文全文」", "正文全文" in ft2)
    check("⑦-3 full 档不写「退回全文」", "退回全文" not in ft2)

    src3, bib3, out3 = _fixture(tmp, "t8")
    r3 = run_convert(ph1_args(src3, bib3, out3, ["--coop-fulltext", "context"]))
    check("⑦-4 ph1(有锚点 context) 退出 0", r3.returncode == 0, (r3.stdout + r3.stderr)[-300:])
    tb3 = read(os.path.join(stage_dir(out3), "San_Zhang_文献核实.md"))
    ft3 = tb3[tb3.find("## 附三"):]
    check("⑦-5 有锚点 context 仍是窗口合并文案",
          "窗口合并" in ft3 and "共 1 段" in ft3 and "退回全文" not in ft3)


# ── ⑨/⑩/⑪ R04：回复自带的 TASK-ID 与本次任务书不符 → 整份拒收 ────────────


def _stage_state(out: str) -> dict:
    return json.loads(read(os.path.join(stage_dir(out), coop.STATE_NAME)))


def _write_wrong_taskid(tmp: str, tag: str, low_json: str = "",
                        name: str = "reply-wrong.md") -> str:
    """造一份「别稿」回复：首行 TASK-ID 不是本次任务书，正文带 k1 的 bib 块（+ 可选低置信 JSON）。"""
    body = ("**TASK-ID**：`BIB-Other_Author-deadbeef`\n\n```bibtex\n@article{k1,\n"
            "  author = {别稿作者},\n  title = {别稿的标题},\n  journal = {别刊},\n"
            "  year = {1999},\n}\n```\n")
    if low_json:
        body += "\n```json\n" + low_json + "\n```\n"
    return write(os.path.join(tmp, tag, name), body)


def test_wrong_task_id_rejected(tmp: str) -> None:
    # 单元层：只有「自带 TASK-ID 且不符」才拒；无身份行/expected 为空 → 原样保留。
    # 修复前 coop 没有这个函数：显式记 FAIL（而不是让断言半途崩掉），好留「修前失败」证据。
    _filter = getattr(coop, "filter_task_id_replies", None)
    if _filter is None:
        check("⑨-0a 错 TASK-ID 整份拒收、无身份行保留", False,
              "coop.filter_task_id_replies 不存在（修复前）")
        check("⑨-0b expected 为空 → 原样返回且零拒收", False, "同上")
    else:
        kept, rej = _filter([("a", "**TASK-ID**：BIB-1-x\n"), ("b", "无身份")], "BIB-2-y")
        check("⑨-0a 错 TASK-ID 整份拒收、无身份行保留",
              [n for n, _ in kept] == ["b"] and len(rej) == 1 and "TASK-ID" in rej[0][1], rej)
        check("⑨-0b expected 为空 → 原样返回且零拒收",
              _filter([("a", "**TASK-ID**：BIB-1-x\n")], "")[1] == [])

    src, bib, out = _fixture(tmp, "t10")
    r1 = run_convert(ph1_args(src, bib, out))
    check("⑨-1 ph1 退出 0", r1.returncode == 0, (r1.stdout + r1.stderr)[-300:])
    st = _stage_state(out)
    tid = str(st.get("taskbook_task_id") or "")
    check("⑨-2 state 记录了任务书 TASK-ID", tid.startswith("BIB-"), tid)
    stage = stage_dir(out)
    stage_bib = os.path.join(stage, "reference.bib")
    before = sha(stage_bib)
    # 错 TASK-ID 的回复里附一条**本来可应用**的低置信建议（BIB-k1 的 title）。
    item = next((it for it in st.get("low_confidence") or [] if it.get("id") == "BIB-k1"), None)
    low = ""
    if item:
        low = json.dumps({"id": "BIB-k1", "verdict": "corrected", "field": "title",
                          "value": "别稿的低置信值",
                          "span": str(item.get("evidence") or "k1")[:4]}, ensure_ascii=False)
    reply = _write_wrong_taskid(tmp, "t10", low)
    r2 = run_convert(ph2_args(src, out, reply, ["--coop-apply"]))
    blob = r2.stdout + r2.stderr
    check("⑨-3 全份错 TASK-ID → 非 0 退出", r2.returncode == 2, r2.returncode)
    check("⑨-4 stdout 有「回填拒收」且点明 TASK-ID 不符",
          ("回填拒收" in blob) and ("TASK-ID" in blob), blob[-500:])
    check("⑨-5 最终交付目录未写", not os.path.exists(os.path.join(out, "San_Zhang")))
    check("⑨-6 暂存 reference.bib 逐字节不变",
          os.path.isfile(stage_bib) and sha(stage_bib) == before)


def test_mixed_task_id_replies(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "t11")
    r1 = run_convert(ph1_args(src, bib, out))
    check("⑩-1 ph1 退出 0", r1.returncode == 0, (r1.stdout + r1.stderr)[-300:])
    tid = str(_stage_state(out).get("taskbook_task_id") or "")
    check("⑩-2 有任务书 TASK-ID", tid.startswith("BIB-"), tid)
    good = write(os.path.join(tmp, "t11", "a-good.md"),
                 f"**TASK-ID**：`{tid}`\n\n```bibtex\n@article{{k1,\n"
                 "  author  = {张三},\n  title   = {测试文献一（回填版）},\n"
                 "  journal = {逻辑学研究},\n  year    = {2020},\n}\n```\n")
    bad = _write_wrong_taskid(tmp, "t11", name="b-bad.md")
    r2 = run_convert(ph2_args(src, out, good, ["--coop-replies", bad]))
    blob = r2.stdout + r2.stderr
    final = os.path.join(out, "San_Zhang")
    check("⑩-3 混合回复仍交付 exit 0", r2.returncode == 0, blob[-600:])
    _fin_bib = os.path.join(final, "reference.bib")
    got = read(_fin_bib) if os.path.isfile(_fin_bib) else ""
    check("⑩-4 合法文件照常并入", "回填版" in got)
    check("⑩-5 错稿文件整份不采用", "别稿的标题" not in got and "别稿作者" not in got)
    check("⑩-6 stdout 给出拒收原因（TASK-ID）",
          ("回填拒收" in r2.stdout) and ("TASK-ID" in r2.stdout),
          [ln for ln in r2.stdout.splitlines() if "拒收" in ln])


def test_legacy_reply_accepted(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "t12")
    r1 = run_convert(ph1_args(src, bib, out))
    check("⑪-1 ph1 退出 0", r1.returncode == 0, (r1.stdout + r1.stderr)[-300:])
    check("⑪-2 旧回复（无 TASK-ID 行）",
          "TASK-ID" not in _REPLY)
    reply = write(os.path.join(tmp, "t12", "reply-legacy.md"), _REPLY)
    r2 = run_convert(ph2_args(src, out, reply))
    blob = r2.stdout + r2.stderr
    check("⑪-3 旧格式回复仍被接受 exit 0", r2.returncode == 0, blob[-500:])
    check("⑪-4 旧回复照常并入", "回填版" in read(os.path.join(out, "San_Zhang", "reference.bib")))
    check("⑪-5 没有「回填拒收」噪声", "回填拒收" not in blob)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="p02_delivery_")
    try:
        test_ph1_staging_only(tmp)
        test_state_metadata(tmp)
        test_fingerprint_mismatch(tmp)
        test_rerun_after_delivery(tmp)
        test_partial_replies(tmp)
        test_aiqian_keeps_original(tmp)
        test_fulltext_fallback_label(tmp)
        test_wrong_task_id_rejected(tmp)
        test_mixed_task_id_replies(tmp)
        test_legacy_reply_accepted(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项")
    if FAILS:
        for f in FAILS:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
