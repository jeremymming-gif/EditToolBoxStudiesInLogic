#!/usr/bin/env python3.14
"""AI 工作流 7 个操作的 CLI 回归测试（DESIGN §8/§9）。

被测：`Engine/slcn/ai_workflow.py` + `Engine/slcn/ai_workflow_cli.py` +
`Engine/convert.py --ai-workflow` 这一跳。**全部匿名夹具**，都在临时目录里跑，
不读真实稿件/作者信息，不联网（API 通道用 `FakeClient`，绝不真调模型）。

分组（对应 DESIGN 的条款）：
  ① 门面一跳：`convert.py --ai-workflow …` 不必给 `--source`（§8 操作不依赖它），
     而 `--version` 仍照旧可用（既有 CLI 不回退）。
  ② prepare：工作区/任务书/TASK-ID/指纹；任务书里带 TASK-ID、规则版本、源指纹、
     引用键；**不写目标 bib**（§8 prepare「不写目标」）。
  ③ import：只认本任务 TASK-ID（外来的整份拒收，§5）；纯 bib 回复可导入。
  ④ preview：给 preview_id、写工作区候选、**仍不写目标**；三类结论分开且
     「能编译」不许声称通过（§6 ⑩）；阻断时不产出候选文件（§6 ⑦）。
  ⑤ apply：指纹绑定 + 备份 + 原子替换；preview_id 过期/目标被外部改动 → 拒绝覆盖
     （§9「外部改动 → 失效，不覆盖」）。
  ⑥ undo：只在目标仍是 apply 后的指纹时才回滚；之后又被改过 → 拒绝（§9）。
  ⑦ status：没有任务也如实说，不抛异常（§9 续跑）。
  ⑧ 幂等：同一 preview 连按两次 apply 不会重复写（第二次指纹已不符 → 拒绝），
     失败路径一律退出码 2 且**不打印**成功字样（§8「失败不写成功结论」）。
  ⑨ API Key 只走 env：`request` 的 argv 里绝不出现 Key（§8）。

用法：
    cd Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 \
        tests/test_ai_workflow_cli_c_20261117.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import ai_workflow as W          # noqa: E402
from slcn import ai_workflow_cli as CLI    # noqa: E402
import convert as CV                       # noqa: E402 — 门面在 Engine/ 根，不在 slcn 包里

FAILS: list[str] = []
PY = sys.executable or "/opt/homebrew/bin/python3.14"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        print(f"FAIL  {name}  {detail}")
        FAILS.append(name)


ALPHA = """@article{alpha,
  author = {Lee, A.},
  title = {Alpha Title},
  journal = {J. Logic},
  year = {2001}
}
"""
TEX = "See \\cite{alpha} for details.\n"
REPLY = """这里是核实结果。

```bibtex
@article{alpha,
  author = {Lee, Alice},
  title = {Alpha Title (Revised)},
  journal = {J. Logic},
  year = {2001}
}
```
"""


def _workspace() -> tuple[str, str, str, str]:
    """建一套匿名夹具：返回 (tmp, bib, tex, reply)。"""
    tmp = tempfile.mkdtemp(prefix="aiwcli_c_")
    bib = os.path.join(tmp, "reference.bib")
    with open(bib, "w", encoding="utf-8", newline="") as f:
        f.write(ALPHA)
    tex = os.path.join(tmp, "main.tex")
    with open(tex, "w", encoding="utf-8", newline="") as f:
        f.write(TEX)
    reply = os.path.join(tmp, "reply.md")
    with open(reply, "w", encoding="utf-8", newline="") as f:
        f.write(REPLY)
    return tmp, bib, tex, reply


def _run(args: list[str]) -> tuple[int, str, str]:
    """直接调 CLI.main（不经子进程）：返回 (rc, out, err)。"""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = CLI.main(args)
    return rc, out.getvalue(), err.getvalue()


def _sha(path: str) -> str:
    import hashlib
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _read(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def main() -> int:
    print("被测模块：", W.SCHEMA_VERSION, "| 状态文件：", W.STATE_NAME)
    tmp, bib, tex, reply = _workspace()
    ws = os.path.join(tmp, "ws")
    try:
        # ── ① 门面一跳 ────────────────────────────────────────────────
        r = subprocess.run([PY, "-X", "utf8", os.path.join(_ENGINE, "convert.py"),
                            "--version"], capture_output=True, text=True, timeout=120,
                           cwd=_ENGINE,
                           env={**os.environ, "PYTHONPATH": _ENGINE + os.pathsep
                                + os.path.join(_ENGINE, "vendor")})
        check("①a convert.py --version 仍可用（既有 CLI 不回退）",
              r.returncode == 0 and "SLCN 引擎" in r.stdout,
              f"rc={r.returncode} out={r.stdout.strip()[:60]!r}")

        rc, out, err = _run(["--work-dir", ws, "status"])
        check("①b 空工作区的 status 不抛异常、不假装有任务",
              rc == 0 and "还没有 AI 任务" in out,
              f"rc={rc} out={out.strip()[:80]!r} err={err.strip()[:80]!r}")

        # --ai-workflow 由 convert.main 在 --source 必填校验**之前**接住
        import inspect
        src_main = inspect.getsource(CV.main)
        check("①c convert.main 里 --ai-workflow 早于 --source 校验",
              "--ai-workflow" in src_main
              and src_main.index("--ai-workflow") < src_main.index("add_cli_args"),
              "dispatch 在 add_cli_args/build_arg_parser 之前")

        # ── ② prepare ───────────────────────────────────────────────
        before = _read(bib)
        rc, out, err = _run(["--work-dir", ws, "prepare", "--target-bib", bib,
                             "--tex", tex])
        st = W.load_state(ws) or {}
        tb_path = (st.get("taskbook") or {}).get("path", "")
        tb_text = _read(tb_path) if tb_path and os.path.exists(tb_path) else ""
        check("②a prepare 建工作区/任务书/指纹，rc=0",
              rc == 0 and bool(tb_path) and bool(st.get("fingerprint")),
              f"rc={rc} taskbook={os.path.basename(tb_path)} fp={str(st.get('fingerprint'))[:8]}")
        check("②b 任务书带 TASK-ID（§5 契约）",
              "TASK-ID" in tb_text and st.get("task_id", "") in tb_text,
              f"task_id={st.get('task_id','')}")
        check("②c 任务书带规则版本 / 源 bib 指纹 / 引用键",
              str(st.get("rule_version", "")) in tb_text
              and str(st.get("bib_sha256", ""))[:12] in tb_text
              and "alpha" in tb_text,
              f"rule={st.get('rule_version')} bib_sha={str(st.get('bib_sha256',''))[:8]}")
        check("②d prepare **不写目标 bib**（§8：prepare 不写目标）",
              _read(bib) == before and st.get("phase") == W.PH_PREPARED,
              f"phase={st.get('phase')} unchanged={_read(bib) == before}")
        check("②e 执行范围按 §4 缺省（文字纠错默认关）",
              W.CAT_TEXT not in set((st.get("scope") or {}).get("exec") or []),
              f"exec={sorted((st.get('scope') or {}).get('exec') or [])}")

        # ── ③ import ───────────────────────────────────────────────
        rc, out, err = _run(["--work-dir", ws, "import", "--reply", reply])
        check("③a 纯 bib 回复可导入（§5「纯 Bib 粘贴也接受」）",
              rc == 0 and "bib 块 1 个" in out, f"rc={rc} out={out.strip()[:70]!r}")

        foreign = os.path.join(tmp, "foreign.md")
        with open(foreign, "w", encoding="utf-8", newline="") as f:
            f.write("> **TASK-ID**：`AIW-DEADBEEF0000`\n\n" + REPLY)
        rc, out, err = _run(["--work-dir", ws, "import", "--reply", foreign])
        check("③b 外来 TASK-ID 整份拒收（§5「不匹配 → 拒绝」）",
              "TASK-ID 不符" in out and "整份按拒收" in out,
              f"rc={rc} out={out.strip()[:90]!r}")

        # ── ④ preview ──────────────────────────────────────────────
        before = _read(bib)
        rc, out, err = _run(["--work-dir", ws, "preview", "--reply", reply])
        st = W.load_state(ws) or {}
        pv = st.get("preview") or {}
        cand = pv.get("candidate_bib", "")
        check("④a preview 给 preview_id 且写工作区候选（§8「确定性候选」）",
              rc == 0 and bool(pv.get("preview_id")) and bool(cand) and os.path.exists(cand),
              f"preview_id={pv.get('preview_id','')} cand={os.path.basename(cand)}")
        check("④b preview 仍不写目标 bib",
              _read(bib) == before,
              f"unchanged={_read(bib) == before}")
        o = out
        check("④c 三类结论分开打印（引用闭合 / AI 核实完成 / 能编译）",
              "**引用闭合**" in o and "**AI 核实完成**" in o and "**能编译**" in o,
              "三段都在")
        check("④d 「能编译」不许声称已通过（§6 ⑩）",
              ("不在此处声称" in o or "不判断" in o)
              and "编译通过" not in o and "全绿" not in o and "全部核实" not in o,
              "无成功宣称")

        # 阻断时不许产出候选文件（§6 ⑦）
        bad = os.path.join(tmp, "bad.md")
        with open(bad, "w", encoding="utf-8", newline="") as f:
            f.write("> **TASK-ID**：`" + str(st.get("task_id", "")) + "`\n\n"
                    "```bibtex\n@article{alpha,\n  title = {T},\n```\n")  # 残缺
        rc, out, err = _run(["--work-dir", ws, "preview", "--reply", bad])
        st2 = W.load_state(ws) or {}
        pv2 = st2.get("preview") or {}
        check("④e 残缺回复 → 阻断，且不产出候选文件（§6 ⑦）",
              pv2.get("blocked") and not pv2.get("candidate_bib"),
              f"blocked={len(pv2.get('blocked') or [])} cand={pv2.get('candidate_bib')!r}")

        # 回到可应用的预览
        rc, out, err = _run(["--work-dir", ws, "preview", "--reply", reply])
        st = W.load_state(ws) or {}
        pv = st.get("preview") or {}
        pid = pv.get("preview_id", "")

        # ── ⑤ apply ────────────────────────────────────────────────
        rc, out, err = _run(["--work-dir", ws, "apply", "--preview-id", pid])
        after = _read(bib)
        st = W.load_state(ws) or {}
        u = st.get("undo") or {}
        check("⑤a apply 用预览候选写入目标 + 备份（备份内容=原文）",
              rc == 0 and "Lee, Alice" in after and u.get("kind") == "bib"
              and os.path.exists(u.get("backup", ""))
              and _read(u["backup"]) == ALPHA,
              f"rc={rc} backup={os.path.basename(u.get('backup',''))}")
        check("⑤b apply 后 phase=applied",
              st.get("phase") == W.PH_APPLIED, f"phase={st.get('phase')}")

        # 幂等 / 过期：同一 preview 再 apply 一次必须拒绝，不重复写。
        # §3.1-10（验收）：这里的目标变动是**同一工作流自己写的**，不能再报
        # 「预览之后被改动（外部改动）」并置 stale —— 那是给外部改动的结论。
        snapshot = _read(bib)
        rc, out, err = _run(["--work-dir", ws, "apply", "--preview-id", pid])
        st_dup = W.load_state(ws) or {}
        check("⑤c 同一 preview 重复 apply 被拒（不重复写，且不误判成外部改动）",
              rc == 2 and _read(bib) == snapshot
              and "已经应用过" in err and "指纹不符" not in err
              and st_dup.get("phase") != W.PH_STALE,
              f"rc={rc} unchanged={_read(bib) == snapshot} "
              f"phase={st_dup.get('phase')} err={err.strip()[:60]!r}")

        # 外部改动 → 预览作废，不许覆盖
        tmp2, bib2, tex2, reply2 = _workspace()
        ws2 = os.path.join(tmp2, "ws")
        _run(["--work-dir", ws2, "prepare", "--target-bib", bib2, "--tex", tex2])
        _run(["--work-dir", ws2, "preview", "--reply", reply2])
        st2 = W.load_state(ws2) or {}
        pid2 = (st2.get("preview") or {}).get("preview_id", "")
        with open(bib2, "w", encoding="utf-8", newline="") as f:
            f.write(ALPHA.replace("Alpha Title", "Hand Edited"))
        rc, out, err = _run(["--work-dir", ws2, "apply", "--preview-id", pid2])
        check("⑤d 目标被外部改动 → 拒绝覆盖（§9 外部改动失效）",
              rc == 2 and "Hand Edited" in _read(bib2) and "指纹不符" in err,
              f"rc={rc} kept={('Hand Edited' in _read(bib2))} err={err.strip()[:60]!r}")

        # ── ⑥ undo ────────────────────────────────────────────────
        rc, out, err = _run(["--work-dir", ws, "undo"])
        check("⑥a undo 还原到 apply 前（逐字节）",
              rc == 0 and _read(bib) == ALPHA, f"rc={rc} restored={_read(bib) == ALPHA}")

        rc, out, err = _run(["--work-dir", ws, "undo"])
        check("⑥b 没有可撤销记录时如实失败（退出码 2，不报成功）",
              rc == 2 and "没有可撤销的写入记录" in err,
              f"rc={rc} err={err.strip()[:60]!r}")

        # 应用后又被改过 → 拒绝撤销
        _run(["--work-dir", ws, "preview", "--reply", reply])
        st = W.load_state(ws) or {}
        pid = (st.get("preview") or {}).get("preview_id", "")
        _run(["--work-dir", ws, "apply", "--preview-id", pid])
        with open(bib, "w", encoding="utf-8", newline="") as f:
            f.write(ALPHA.replace("J. Logic", "Later Edit"))
        rc, out, err = _run(["--work-dir", ws, "undo"])
        check("⑥c 应用后目标又被改 → 拒绝撤销（不覆盖用户新改动，§9）",
              rc == 2 and "Later Edit" in _read(bib) and "又被改动" in err,
              f"rc={rc} kept={('Later Edit' in _read(bib))} err={err.strip()[:60]!r}")

        # ── ⑤e–⑤g 验收 §3.1-6/10：text-only 写 0 字节时状态必须如实 ──
        tmp_x, bib_x, tex_x, reply_x = _workspace()
        ws_x = os.path.join(tmp_x, "ws")
        tex_target = os.path.join(tmp_x, "paper_delivery.tex")
        with open(tex_x, "w", encoding="utf-8", newline="") as f:
            f.write("They was late. The result holds.\n")
        rc, out, err = _run(["--work-dir", ws_x, "prepare", "--target-bib", bib_x,
                             "--tex", tex_x, "--text", "--tex-out", tex_target])
        st_x = W.load_state(ws_x) or {}
        tid_x = str(st_x.get("task_id") or "")
        sugg = ('> **TASK-ID**：`' + tid_x + '`\n\n```json\n'
                '[{"category":"typo","old":"They was late.","new":"They were late."}]\n```\n')
        with open(os.path.join(tmp_x, "reply_x.md"), "w", encoding="utf-8") as f:
            f.write(sugg)
        src_sha_before = _sha(tex_x)
        rc, out, err = _run(["--work-dir", ws_x, "preview",
                             "--reply", os.path.join(tmp_x, "reply_x.md"),
                             "--adopt", "text"])
        pv_x = (W.load_state(ws_x) or {}).get("preview") or {}
        check("⑤e text-only 预览产出正文候选，且源正文一个字节没变（§7 源只读）",
              rc == 0 and pv_x.get("candidate_text")
              and os.path.exists(str(pv_x.get("candidate_text")))
              and _sha(tex_x) == src_sha_before,
              f"rc={rc} cand={bool(pv_x.get('candidate_text'))}")
        check("⑤f text-only 预览不产 bib 候选（采纳范围没勾文献核实）",
              not str(pv_x.get("candidate_bib") or ""),
              f"cand_bib={repr(pv_x.get('candidate_bib'))}")
        rc, out, err = _run(["--work-dir", ws_x, "apply",
                             "--preview-id", str(pv_x.get("preview_id") or "")])
        st_x2 = W.load_state(ws_x) or {}
        wrote = (_read(tex_target) if os.path.exists(tex_target) else "")
        check("⑤g 正文写回落到 --tex-out 指定的交付件（不动源正文），undo 可用",
              wrote.startswith("They were late.")
              and _sha(tex_x) == src_sha_before
              and (st_x2.get("undo") or {}).get("writes"),
              f"wrote={wrote.strip()[:24]!r} undo={bool(st_x2.get('undo'))}")
        rc, out, err = _run(["--work-dir", ws_x, "undo"])
        check("⑤h undo 后交付件回到写前状态（逐字节）",
              rc == 0 and (not os.path.exists(tex_target)
                           or _read(tex_target) == "They was late. The result holds.\n"),
              f"rc={rc} removed={not os.path.exists(tex_target)}")

        # ── ⑦ status ──────────────────────────────────────────────
        rc, out, err = _run(["--work-dir", ws, "status"])
        check("⑦a status 打印阶段/TASK-ID/下一步（key=value 口径）",
              rc == 0 and "phase=" in out and "task_id=" in out,
              f"rc={rc}")
        rc, out, err = _run(["--work-dir", os.path.join(tmp, "nope"), "status"])
        check("⑦b 不存在的工作区 status 不抛异常、说明「还没有任务」",
              rc == 0 and "还没有 AI 任务" in out, f"rc={rc}")

        # ── ⑧ 失败不写成功结论 ─────────────────────────────────────
        rc, out, err = _run(["--work-dir", ws, "import", "--reply",
                             os.path.join(tmp, "missing.md")])
        check("⑧a 回填文件不存在 → rc=2 且没有成功字样",
              rc == 2 and not any(w in out for w in ("已导入", "成功", "完成")),
              f"rc={rc} out={out.strip()[:60]!r}")

        rc, out, err = _run(["--work-dir", ws, "apply", "--preview-id", "pv-nope"])
        check("⑧b 不存在的 preview_id → rc=2，不写文件",
              rc == 2 and "预览已过期或不存在" in err,
              f"rc={rc} err={err.strip()[:60]!r}")

        rc, out, err = _run(["--work-dir", ws, "prepare", "--target-bib",
                             os.path.join(tmp, "missing.bib")])
        check("⑧c 目标 bib 不存在 → rc=2（不假装出了任务书）",
              rc == 2, f"rc={rc} err={err.strip()[:60]!r}")

        rc, out, err = _run(["--work-dir", ws, "prepare", "--target-bib", bib,
                             "--execute", "nonsense"])
        check("⑧d 不认识的类别 → rc=2 并点名（不静默降级）",
              rc == 2 and "不认识的类别" in err, f"rc={rc} err={err.strip()[:70]!r}")

        # ── ⑨ API Key 只走 env ────────────────────────────────────
        client = W.FakeClient(REPLY)
        req = W.request(work_dir=ws, taskbook_text="tb", client=client)
        check("⑨a FakeClient 走同一条 request 通道（不联网、不自动采纳）",
              req.get("ok") and req.get("chars", 0) > 0
              and (W.load_state(ws) or {}).get("phase") == W.PH_IMPORTED,
              f"ok={req.get('ok')} chars={req.get('chars')}")

        key = "sk-THIS-MUST-NEVER-LEAK-0123456789"
        os.environ["SLCN_LLM_KEY"] = key
        try:
            call = None
            # 桥的 request 构造不在本文件测（引擎侧只保证 CLI 不接受 Key 参数）
            ap = CLI.build_parser()
            op = ap.parse_args(["--work-dir", ws, "request"])
            check("⑨b CLI 没有接收 Key 的参数（只能走 env/配置）",
                  not any("key" in str(s).lower() for s in vars(op)),
                  f"args={sorted(vars(op))[:6]}")
            src = open(os.path.join(_ENGINE, "slcn", "ai_workflow_cli.py"),
                       encoding="utf-8").read()
            check("⑨c CLI 源码不打印/落盘 Key（只从 env 读）",
                  "SLCN_LLM_KEY" not in src and "--api-key" not in src,
                  "无 Key 参数/无 Key 明文")
        finally:
            os.environ.pop("SLCN_LLM_KEY", None)

        # ── ⑨d–⑨g 验收 §3.1-4/5/9：request → preview 直连、缺键阻断、adopt 门控 ──
        tmp_r, bib_r, tex_r, _r2 = _workspace()
        ws_r = os.path.join(tmp_r, "ws")
        rc, _o, _e = _run(["--work-dir", ws_r, "prepare",
                           "--target-bib", bib_r, "--tex", tex_r])
        st_r = W.load_state(ws_r) or {}
        tid_r = str(st_r.get("task_id") or "")
        reply_r = (f"> **TASK-ID**：`{tid_r}`\n\n```bibtex\n"
                   "@article{alpha, author={Lee, Alice}, title={Alpha Title (Revised)}, "
                   "journal={J. Logic}, year={2001}}\n```\n")
        req_r = W.request(work_dir=ws_r, taskbook_text="tb",
                          client=W.FakeClient(reply_r))
        st_r = W.load_state(ws_r) or {}
        check("⑨d request 后立刻记入 state.replies（API 与 Chat 同一套解析，§3.1-9）",
              req_r.get("ok") and len(st_r.get("replies") or []) == 1,
              f"ok={req_r.get('ok')} replies={len(st_r.get('replies') or [])}")
        rc, out, err = _run(["--work-dir", ws_r, "preview", "--adopt", "bib_verify"])
        pv_r = (W.load_state(ws_r) or {}).get("preview") or {}
        check("⑨e request → preview 可直跑（不必再手工 import，§3.1-9）",
              rc == 0 and pv_r.get("preview_id") and not pv_r.get("blocked"),
              f"rc={rc} pid={pv_r.get('preview_id')} blocked={len(pv_r.get('blocked') or [])}")

        # §3.1-4：正文引用了 bib 里没有的键 → 必须阻断，不许「引用闭合 ✅」
        tmp_m, bib_m, tex_m, reply_m = _workspace()
        ws_m = os.path.join(tmp_m, "ws")
        with open(tex_m, "w", encoding="utf-8", newline="") as f:
            f.write("See \\cite{alpha,zeta} for details.\n")
        _run(["--work-dir", ws_m, "prepare", "--target-bib", bib_m, "--tex", tex_m])
        rc, out, err = _run(["--work-dir", ws_m, "preview", "--reply", reply_m,
                             "--adopt", "bib_verify"])
        pv_m = (W.load_state(ws_m) or {}).get("preview") or {}
        check("⑨f 正文引用了 bib 里没有的键 → 阻断并点名（§3.1-4，不许谎报闭合）",
              rc == 2 and pv_m.get("blocked") and "zeta" in " ".join(pv_m["blocked"]),
              f"rc={rc} blocked={len(pv_m.get('blocked') or [])}")

        # §3.1-5：采纳范围不含「文献核实」→ 不合成、不写 bib 候选
        tmp_a, bib_a, tex_a, reply_a = _workspace()
        ws_a = os.path.join(tmp_a, "ws")
        _run(["--work-dir", ws_a, "prepare", "--target-bib", bib_a, "--tex", tex_a])
        rc, out, err = _run(["--work-dir", ws_a, "preview", "--reply", reply_a,
                             "--adopt", "typeset"])
        pv_a = (W.load_state(ws_a) or {}).get("preview") or {}
        check("⑨g 采纳范围不含文献核实 → 不写 bib 候选、不报可应用（§3.1-5）",
              not str(pv_a.get("candidate_bib") or "")
              and not pv_a.get("bib_new_sha256")
              and "文献核实" in out,
              f"cand={repr(pv_a.get('candidate_bib'))} rc={rc}")

        # ── ⑩ 文字纠错（§7）：默认不采纳、基线只读、候选另存 ─────────
        tmp_t, bib_t, tex_t, _reply_t = _workspace()
        ws_t = os.path.join(tmp_t, "ws")
        try:
            rc, out, err = _run(["--work-dir", ws_t, "prepare",
                                 "--target-bib", bib_t, "--tex", tex_t, "--text"])
            check("⑩a --text 把「文字纠错」加进执行范围", rc == 0,
                  f"rc={rc} err={err.strip()[:90]}")
            st = W.load_state(ws_t) or {}
            check("⑩b state 冻结正文路径与指纹（preview 才能按同一基线回算）",
                  str((st.get("tex") or {}).get("path")) == os.path.abspath(tex_t)
                  and len(str((st.get("tex") or {}).get("sha256") or "")) == 64,
                  str((st.get("tex") or {}).get("path")))

            reply_t = os.path.join(tmp_t, "reply_text.md")
            tid = str(st.get("task_id") or "")
            body = ("> **TASK-ID**：`" + tid + "`\n\n"
                    "```json\n"
                    '[{"old": "They was late.", "new": "They were late.", "kind": "语法"}]'
                    "\n```\n")
            with open(reply_t, "w", encoding="utf-8", newline="") as f:
                f.write(body)
            # 正文里补一句待改的文本，否则没有可定位的原文
            with open(tex_t, "w", encoding="utf-8", newline="") as f:
                f.write("See \\cite{alpha} for details.\nThey was late.\n")

            rc, out, err = _run(["--work-dir", ws_t, "preview", "--reply", reply_t,
                                 "--adopt", "text"])
            # 这一份回填只有结构化建议、没有 bib 块 → 整体 ok=False（不写目标），
            # 因此 rc=2 是**正确的诚实失败**；关键是别静默跳过，要把原因说出来。
            check("⑩c 正文在 prepare 之后被改过 → 拒绝采纳并说明指纹不符（不静默跳过）",
                  rc == 2 and "指纹不符" in out, f"rc={rc} out={out.strip()[-140:]}")

            # 复原正文后重新 prepare，再走一次（同一基线）
            with open(tex_t, "w", encoding="utf-8", newline="") as f:
                f.write("See \\cite{alpha} for details.\nThey was late.\n")
            shutil.rmtree(ws_t, ignore_errors=True)
            _run(["--work-dir", ws_t, "prepare", "--target-bib", bib_t,
                  "--tex", tex_t, "--text"])
            st = W.load_state(ws_t) or {}
            tid = str(st.get("task_id") or "")
            with open(reply_t, "w", encoding="utf-8", newline="") as f:
                f.write("> **TASK-ID**：`" + tid + "`\n\n```json\n"
                        '[{"old": "They was late.", "new": "They were late.", "kind": "语法"}]'
                        "\n```\n")
            tex_before = _read(tex_t)
            rc, out, err = _run(["--work-dir", ws_t, "preview", "--reply", reply_t])
            cand = os.path.join(ws_t, "reference_AI候选.tex")
            check("⑩d 采纳范围不含 text → 不产出 .tex 候选，且说明「默认不采纳」（§4）",
                  rc == 2 and not os.path.exists(cand)
                  and "默认**不采纳**" in out,
                  f"rc={rc} exists={os.path.exists(cand)} out={out.strip()[-90:]}")

            rc, out, err = _run(["--work-dir", ws_t, "preview", "--reply", reply_t,
                                 "--adopt", "text"])
            check("⑩e --adopt text → 产出候选且摘要单列「文字纠错」",
                  rc == 0 and os.path.exists(cand) and "**文字纠错**" in out,
                  f"rc={rc} exists={os.path.exists(cand)}")
            check("⑩f 源文只读：候选写了改动，源文件一个字节没变",
                  os.path.exists(cand) and "They were late." in _read(cand)
                  and _read(tex_t) == tex_before,
                  "源 tex 未改动")
        finally:
            shutil.rmtree(tmp_t, ignore_errors=True)

        # ── ⑪ B1（终验阻断）：正文粘贴（--stdin）→ preview → apply 全链通 ──
        # 缺陷原状：import 只记 `path`，粘贴时 path 为空 → preview 自动回填跳过它 →
        # rc2「未提供回填 bib 块」，Chat 主通道（N04）整条链路断在第二步。
        tmp_p, bib_p, tex_p, _reply_p = _workspace()
        ws_p = os.path.join(tmp_p, "ws")
        try:
            rc, out, err = _run(["--work-dir", ws_p, "prepare",
                                 "--target-bib", bib_p, "--tex", tex_p])
            st_p = W.load_state(ws_p) or {}
            tid_p = str(st_p.get("task_id") or "")
            paste = (f"> **TASK-ID**：`{tid_p}`\n\n```bibtex\n"
                     "@article{alpha, author={Lee, Alice}, title={Alpha Title (Revised)}, "
                     "journal={J. Logic}, year={2001}}\n```\n")
            r = subprocess.run([PY, "-X", "utf8",
                                os.path.join(_ENGINE, "convert.py"),
                                "--ai-workflow", "--work-dir", ws_p,
                                "import", "--stdin"],
                               input=paste, capture_output=True, text=True,
                               encoding="utf-8", timeout=120, cwd=_ENGINE,
                               env={**os.environ, "PYTHONPATH": _ENGINE + os.pathsep
                                    + os.path.join(_ENGINE, "vendor")})
            st_p = W.load_state(ws_p) or {}
            rep = (st_p.get("replies") or [{}])[-1]
            check("⑪a 粘贴导入后 state.replies 记下**真实路径**（不是空串）",
                  r.returncode == 0 and bool(rep.get("path"))
                  and os.path.exists(str(rep.get("path"))),
                  f"rc={r.returncode} path={os.path.basename(str(rep.get('path') or ''))}")
            check("⑪b 粘贴正文落成工作区文件，且**不在**交付清理的删除名单上",
                  bool(rep.get("path")) and ".aiw" in str(rep.get("path"))
                  and not str(rep.get("path")).endswith(".ai前")
                  and "_AI" not in os.path.basename(str(rep.get("path"))),
                  str(rep.get("path")))
            rc, out, err = _run(["--work-dir", ws_p, "preview", "--adopt", "bib_verify"])
            pv_p = (W.load_state(ws_p) or {}).get("preview") or {}
            check("⑪c 粘贴 → preview 直跑（不必再给 --reply，也不报「未提供回填 bib 块」）",
                  rc == 0 and bool(pv_p.get("preview_id")) and "未提供回填 bib 块" not in out,
                  f"rc={rc} pid={pv_p.get('preview_id')} out={out.strip()[-70:]}")
            rc, out, err = _run(["--work-dir", ws_p, "apply",
                                 "--preview-id", str(pv_p.get("preview_id") or "")])
            check("⑪d 粘贴 → preview → apply 真的写进目标（Chat 主通道端到端）",
                  rc == 0 and "Lee, Alice" in _read(bib_p),
                  f"rc={rc} wrote={('已写入' in out)}")
        finally:
            shutil.rmtree(tmp_p, ignore_errors=True)

        # ── ⑫ B2（终验阻断）：交付清理后状态与撤销仍可用 ──────────────────
        # 缺陷原状：状态在根目录（`_AI工作流状态.json`）、备份是 `*.ai前`，两者都在
        # clean.py 的删除名单上 → 交付清理后 undo 只能报「找不到可续跑的状态」。
        tmp_c, bib_c, tex_c, _reply_c = _workspace()
        ws_c = os.path.join(tmp_c, "ws")
        try:
            os.makedirs(ws_c, exist_ok=True)
            # 交付目录必须含 .tex（delivery_closure 要求）；目标 bib 也放进工作区，
            # 否则 `.ai前` 落在清理根之外，这条回归就白跑了。
            tex_c2 = os.path.join(ws_c, "paper.tex")
            with open(tex_c2, "w", encoding="utf-8", newline="") as f:
                f.write(TEX)
            bib_c2 = os.path.join(ws_c, "reference.bib")
            with open(bib_c2, "w", encoding="utf-8", newline="") as f:
                f.write(ALPHA)
            _run(["--work-dir", ws_c, "prepare", "--target-bib", bib_c2, "--tex", tex_c2])
            tid_c = str((W.load_state(ws_c) or {}).get("task_id") or "")
            reply_c = (f"> **TASK-ID**：`{tid_c}`\n\n```bibtex\n"
                       "@article{alpha, author={Lee, Alice}, title={Alpha Title (Revised)}, "
                       "journal={J. Logic}, year={2001}}\n```\n")
            W.import_reply(work_dir=ws_c, text=reply_c)
            pv_c = W.preview(work_dir=ws_c)
            ap = W.apply(work_dir=ws_c, preview_id=pv_c.preview_id)
            w0 = (ap.get("writes") or [{}])[0]
            check("⑫a apply 除 `.ai前` 外还留一份撤销副本（`.aiw/undo/…`）",
                  bool(w0.get("undo_copy")) and os.path.exists(str(w0.get("undo_copy"))),
                  os.path.basename(str(w0.get("undo_copy") or "")))
            check("⑫b 状态落在隐藏目录，文件名不命中交付清理名单（无 `_AI`、非 `.ai前`）",
                  ".aiw" in W.state_path(ws_c)
                  and "_AI" not in os.path.basename(W.state_path(ws_c))
                  and not W.state_path(ws_c).endswith(".ai前"),
                  W.state_path(ws_c))
            from slcn import clean as _clean  # noqa: PLC0415
            _clean.delivery_clean(ws_c, delete=True)
            check("⑫c 交付清理确实删掉了 `.ai前`（证明这条回归不是白跑）",
                  not os.path.exists(str(w0.get("backup") or "")),
                  f"backup_exists={os.path.exists(str(w0.get('backup') or ''))}")
            check("⑫d 清理后状态仍读得到（不是「找不到可续跑的状态」）",
                  (W.load_state(ws_c) or {}).get("phase") == W.PH_APPLIED,
                  str((W.load_state(ws_c) or {}).get("phase")))
            u_c = W.undo(work_dir=ws_c)
            check("⑫e 清理后 undo 仍能还原原文（用 `.aiw/undo` 副本）",
                  u_c.get("ok") and _read(bib_c2) == ALPHA
                  and ".aiw" in str(u_c.get("restored_from") or ""),
                  f"from={os.path.basename(str(u_c.get('restored_from') or ''))}")
        finally:
            shutil.rmtree(tmp_c, ignore_errors=True)

        # ── 其他契约 ──────────────────────────────────────────────
        check("· 状态文件带 schema_version（§9）",
              str((W.load_state(ws) or {}).get("schema_version", "")) == W.SCHEMA_VERSION,
              W.SCHEMA_VERSION)
        bad_state = os.path.join(tmp, "ws_bad")
        os.makedirs(os.path.dirname(W.state_path(bad_state)), exist_ok=True)
        with open(W.state_path(bad_state), "w", encoding="utf-8") as f:
            json.dump({"schema_version": "slcn.ai_workflow/99"}, f)
        check("· 不认识的 schema_version → 当作没有状态（不误读）",
              W.load_state(bad_state) is None, "None")
        check("· 原子替换：临时文件不留在工作区",
              not [n for n in os.listdir(ws) if ".tmp-aiwf" in n],
              ",".join(sorted(os.listdir(ws))[:5]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 66)
    if FAILS:
        print(f"失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("失败 0 项（全部通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
