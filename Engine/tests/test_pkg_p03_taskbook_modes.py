#!/usr/bin/env python3
"""P03 R01/R03：三卷（--coop-split）/条目分片（--coop-slice）与回填检查的引擎契约测试。

测试口径（独立断言，不镜像 coop.py / convert_stage.py 的实现细节）：
  · 只走公开 CLI（convert.py 子进程）+ 读 `_AI协同状态.json` + 读产物文件字节；
  · 默认与 `--coop-slice 0`：任务书逐字节相等、state 去掉 created_at 后相等、
    不出现卷/分片文件、不出现新 state 键、PH1 给外壳的契约行不变；
  · `--coop-split`：state.taskbook_volumes = 三个存在的绝对路径，卷 A/B/C 文件可读，
    任务书本身仍单文件；默认零变化；
  · `--coop-slice N`：分片数/条目区间/键/片头尾标记/路径/state 字段；
  · 非法输入：非法文本/负数都由 argparse 在转换开始前退出 2，输出目录完全不建
    （CONTRACT-P03 §2 零写；P03-ISS-E1 修复后由本测试钉住）；
  · 旧 state（缺 taskbook_version/id/volumes/slices 四个新键）：第二段照常可用并交付；
  · 第二段回填检查：缺片只 ⚠ 提示且交付成功；片标记越界/串稿整份拒收（rc2、state
    零变化、不交付）；串错片只 ⚠ 提示且交付成功；
  · 成功交付：暂存目录被清理（state 消失），最终目录有产物与 _AI协同记录.md，
    stdout 报「暂存目录已清理」，不误报「未清理」。

夹具全部为合成 LaTeX/bib（不读真实稿件库、不联网、不调外部模型）。
运行：cd Engine && PYTHONPATH=.:vendor python tests/test_pkg_p03_taskbook_modes.py
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (ENGINE, os.path.join(ENGINE, "vendor")):
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.insert(0, _d)

FAILS: list[str] = []
PASS = 0

BIB_KEYS = ["k1", "k2", "k3", "k4"]


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


# ── 夹具与通用工具 ───────────────────────────────────────────────────────

_TEX = r"""\documentclass{article}
\title{三卷分片模式测试}
\author{张三}
\begin{document}
\maketitle
正文测试段落，只用于合成夹具。

\begin{thebibliography}{9}
\bibitem{k1} 张三. 甲[J]. 逻辑学研究, 2020.
\bibitem{k2} 李四. 乙[J]. 逻辑学研究, 2021.
\bibitem{k3} 王五. 丙[J]. 逻辑学研究, 2022.
\bibitem{k4} 赵六. 丁[J]. 逻辑学研究, 2023.
\end{thebibliography}
\end{document}
"""


def _entry(key: str) -> str:
    return (f"@article{{{key},\n"
            f"  author = {{作者{key}}},\n"
            f"  title = {{标题{key}}},\n"
            f"  journal = {{逻辑学研究}},\n"
            f"  year = {{2020}},\n"
            f"}}\n")


_BIB = "\n".join(_entry(k).rstrip() for k in BIB_KEYS) + "\n"


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _listing(path: str) -> list[str]:
    return sorted(os.listdir(path)) if os.path.isdir(path) else []


def _env(work: str) -> dict:
    """隔离环境：不读真实稿件库/设置，不写平台默认启动日志。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [ENGINE] + ([os.path.join(ENGINE, "vendor")] if os.path.isdir(os.path.join(ENGINE, "vendor")) else []))
    env["SLCN_MANUSCRIPTS"] = os.path.join(work, "no_manuscripts")
    env["SLCN_STARTUP_LOG"] = os.path.join(work, "startup.log")
    env["P2_SETTINGS"] = os.path.join(work, "settings.json")
    for k in list(env):
        if k.startswith("SLCN_LLM_") or k.startswith("SLCN_SEARCH_"):
            env.pop(k, None)
    return env


def _run(work: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, os.path.join(ENGINE, "convert.py")] + args,
                          cwd=ENGINE, env=_env(work), capture_output=True, text=True,
                          timeout=300)


def _fixture(work: str, name: str) -> tuple[str, str]:
    src = _write(os.path.join(work, "fixtures", name, "main.tex"), _TEX)
    bib = _write(os.path.join(work, "fixtures", name, "refs.bib"), _BIB)
    return src, bib


def _ph1(work: str, src: str, bib: str, out: str, *extra: str) -> subprocess.CompletedProcess:
    return _run(work, ["--source", src, "--bib", bib, "--out", out,
                       "--author", "张三", "--no-original", "--verify-refs",
                       "--no-ai-merge", "--coop-taskbook-only"] + list(extra))


def _ph2(work: str, src: str, out: str, reply: str) -> subprocess.CompletedProcess:
    return _run(work, ["--source", src, "--out", out,
                       "--coop-replies", reply, "--no-original"])


def _stage(out: str) -> str:
    hits = glob.glob(os.path.join(out, "*_待核"))
    check("找到唯一暂存目录", len(hits) == 1, str(hits))
    return hits[0] if len(hits) == 1 else ""


def _state(stage: str) -> dict:
    p = os.path.join(stage, "_AI协同状态.json")
    if not os.path.isfile(p):
        return {}
    return json.loads(_read(p))


def _write_reply(work: str, name: str, keys: list[str], marker: str = "") -> str:
    text = (marker + "\n\n" if marker else "") + "\n".join(_entry(k) for k in keys)
    return _write(os.path.join(work, "replies", name + ".md"), text + "\n")


def _compact(state: dict) -> dict:
    """state 对账口径：去掉逐次运行天然不同的 created_at。"""
    st = json.loads(json.dumps(state, ensure_ascii=False))
    st.pop("created_at", None)
    return st


def _volume_files(out: str) -> list[str]:
    return glob.glob(os.path.join(out, "*", "*_文献核实_卷*.md"))


def _slice_files(out: str) -> list[str]:
    return glob.glob(os.path.join(out, "*", "*_文献核实_分片*.md"))


def _no_new_modes(out: str, state: dict) -> bool:
    return (not _volume_files(out) and not _slice_files(out)
            and "taskbook_volumes" not in state and "taskbook_slices" not in state)


# ── ① 默认与 --coop-slice 0：零变化 + PH1 契约输出 ───────────────────────


def test_default_zero_change(work: str) -> None:
    src, bib = _fixture(work, "default")
    out = os.path.join(work, "out_default")
    p = _ph1(work, src, bib, out)
    check("①-1 默认 PH1 退出 0", p.returncode == 0, p.stderr[-400:])
    stage = _stage(out)
    state = _state(stage)
    check("①-2 schema 仍为 1", state.get("schema") == 1, str(state.get("schema")))
    check("①-3 默认不写 taskbook_volumes/taskbook_slices",
          "taskbook_volumes" not in state and "taskbook_slices" not in state)
    check("①-4 默认不写卷/分片文件", _no_new_modes(out, state),
          str(_listing(stage)))
    tb = os.path.join(stage, os.path.basename(state.get("taskbook", "")))
    check("①-5 默认任务书存在", os.path.isfile(tb), tb)
    check("①-6 默认任务书无 B311-SLICE 标记", "B311-SLICE" not in _read(tb))
    check("①-7 默认 stdout 无分片提示", "任务书分片" not in p.stdout)
    stem = state.get("params", {}).get("stem", "")
    check("①-8 默认未写最终交付目录",
          bool(stem) and not os.path.isdir(os.path.join(out, stem)))
    # PH1 给外壳的契约输出（P02 既有行，P03 不得改变）。
    low_n = len(state.get("keys", {}).get("low_confidence") or [])
    contract = [
        "⑬ AI 协同初排·第一段（已暂停，等待回填）",
        "   暂存目录：" + stage,
        f"   核实任务书：{os.path.basename(tb)}（含「初排低置信项」{low_n} 项）",
        "   状态文件：_AI协同状态.json",
        "   最终交付目录本次**未写**；把任务书交给任意 chat 模型，"
        "回填后运行 --coop-replies <文件或目录> 继续。",
        "   协同初排第一段完成（**尚未交付**）：产物与任务书都在暂存目录。",
        "   下一步：编辑把任务书交给任意 chat 模型核实 → 回填后运行 --coop-replies。",
    ]
    missing = [line for line in contract if line not in p.stdout]
    check("①-9 PH1 契约输出行不变", not missing, repr(missing))
    last = p.stdout.rstrip("\n").splitlines()[-1] if p.stdout.strip() else ""
    check("①-10 PH1 末尾一行仍是暂存目录", last == "暂存目录：" + stage, last)

    # 同一 out 根、先删再跑 --coop-slice 0：两次任务书逐字节相等、state 去 created_at 相等。
    tb_bytes_default = open(tb, "rb").read()
    state_default = state
    shutil.rmtree(out)
    p0 = _ph1(work, src, bib, out, "--coop-slice", "0")
    check("①-11 --coop-slice 0 退出 0", p0.returncode == 0, p0.stderr[-400:])
    stage0 = _stage(out)
    state0 = _state(stage0)
    tb0 = os.path.join(stage0, os.path.basename(state0.get("taskbook", "")))
    check("①-12 --coop-slice 0 与默认任务书逐字节相等",
          open(tb0, "rb").read() == tb_bytes_default)
    check("①-13 --coop-slice 0 与默认 state 相等（忽略 created_at）",
          _compact(state0) == _compact(state_default))
    check("①-14 --coop-slice 0 不写卷/分片文件",
          _no_new_modes(out, state0), str(_listing(stage0)))
    check("①-15 --coop-slice 0 stdout 无分片提示", "任务书分片" not in p0.stdout)


# ── ② --coop-split：三卷文件 + state.taskbook_volumes ────────────────────


def test_split_volumes(work: str) -> None:
    src, bib = _fixture(work, "split")
    out = os.path.join(work, "out_split")
    p = _ph1(work, src, bib, out, "--coop-split")
    check("②-1 --coop-split PH1 退出 0", p.returncode == 0, p.stderr[-400:])
    stage = _stage(out)
    state = _state(stage)
    vols = state.get("taskbook_volumes") or []
    stem = state.get("params", {}).get("stem", "")
    check("②-2 state.taskbook_volumes 是 3 个绝对路径",
          isinstance(vols, list) and len(vols) == 3
          and all(isinstance(v, str) and os.path.isabs(v) for v in vols), str(vols))
    check("②-3 三卷文件都存在且非空",
          all(os.path.isfile(v) and os.path.getsize(v) > 0 for v in vols), str(vols))
    check("②-4 卷文件名是 <stem>_文献核实_卷A/B/C.md",
          [os.path.basename(v) for v in vols]
          == [f"{stem}_文献核实_卷{n}.md" for n in ("A", "B", "C")])
    check("②-5 卷文件都在暂存目录内", all(os.path.dirname(v) == stage for v in vols))
    a, b, c = (_read(v) if os.path.isfile(v) else "" for v in vols)
    check("②-6 卷 A 以任务书标题开头并含任务卡 TASK-ID",
          a.startswith(f"# 参考文献核实任务书 · {stem}")
          and str(state.get("taskbook_task_id", "")) in a)
    check("②-7 卷 B/C 带「同一份任务书的第 i/3 卷」说明",
          "同一份任务书的第 2/3 卷" in b and "同一份任务书的第 3/3 卷" in c)
    check("②-8 卷 A/B/C 各含本卷标题",
          "## 卷 A" in a and "## 卷 B" in b and "## 卷 C" in c)
    check("②-9 任务书本身仍是单文件（卷 A/B/C 内容并入其中）",
          os.path.isfile(os.path.join(stage, f"{stem}_文献核实.md"))
          and "## 卷 A" in _read(os.path.join(stage, f"{stem}_文献核实.md")))
    check("②-10 --coop-split 不写分片文件、state 无 taskbook_slices",
          not _slice_files(out) and "taskbook_slices" not in state)
    # 对照：不传 --coop-split 时无卷文件（默认单文件语义不变）。
    out2 = os.path.join(work, "out_nosplit")
    p2 = _ph1(work, src, bib, out2)
    check("②-11 不传 --coop-split 时不写卷文件",
          p2.returncode == 0 and not _volume_files(out2)
          and "taskbook_volumes" not in _state(_stage(out2)))


# ── ③ --coop-slice N 与 split×slice ─────────────────────────────────────


def test_slice_modes(work: str) -> None:
    src, bib = _fixture(work, "slice")
    out = os.path.join(work, "out_slice")
    p = _ph1(work, src, bib, out, "--coop-slice", "2")
    check("③-1 --coop-slice 2 PH1 退出 0", p.returncode == 0, p.stderr[-400:])
    stage = _stage(out)
    state = _state(stage)
    stem = state.get("params", {}).get("stem", "")
    tid = state.get("taskbook_task_id", "")
    slices = state.get("taskbook_slices") or []
    check("③-2 state.taskbook_slices 为 2 片", len(slices) == 2, str(slices))
    check("③-3 分片字段 index/total/volume 正确",
          [(s.get("index"), s.get("total"), s.get("volume")) for s in slices]
          == [(1, 2, "A"), (2, 2, "A")], str([(s.get("index"), s.get("total"), s.get("volume")) for s in slices]))
    expect_keys = [BIB_KEYS[0:2], BIB_KEYS[2:4]]
    check("③-4 分片键与条目区间连续",
          [s.get("keys") for s in slices] == expect_keys
          and [(s.get("first"), s.get("last")) for s in slices] == [(1, 2), (3, 4)],
          str([(s.get("first"), s.get("last"), s.get("keys")) for s in slices]))
    check("③-5 分片路径为绝对路径且文件存在",
          all(os.path.isabs(s.get("path", "")) and os.path.isfile(s.get("path", ""))
              for s in slices))
    check("③-6 分片文件名 <stem>_文献核实_分片{k}-{n}.md",
          [os.path.basename(s["path"]) for s in slices]
          == [f"{stem}_文献核实_分片{k}-2.md" for k in (1, 2)])
    for s in slices:
        k, n = s["index"], s["total"]
        text = _read(s["path"])
        head = text.splitlines()[0] if text.splitlines() else ""
        check(f"③-7.{k} 片头标记含片号/TASK-ID/区间",
              head.startswith(f"<!-- B311-SLICE {k}/{n}")
              and f"TASK-ID {tid}" in head
              and f"第 {s['first']}–{s['last']} 条" in head, head)
        check(f"③-8.{k} 片标题可见片号",
              f"（分片 {k}/{n}）" in text.splitlines()[1] if len(text.splitlines()) > 1 else False)
        tail = [ln for ln in text.splitlines() if ln.strip()][-1]
        check(f"③-9.{k} 片尾标记含片号与键",
              tail.startswith(f"<!-- B311-SLICE-END {k}/{n}")
              and ",".join(s["keys"]) in tail, tail)
    check("③-10 单文件任务书仍存在且不含 B311-SLICE 标记",
          os.path.isfile(os.path.join(stage, f"{stem}_文献核实.md"))
          and "B311-SLICE" not in _read(os.path.join(stage, f"{stem}_文献核实.md")))
    check("③-11 stdout 报告分片数",
          "任务书分片：每片 ≤ 2 条，共 2 片" in p.stdout, p.stdout[-500:])
    check("③-12 不写卷文件", not _volume_files(out))

    # split × slice：两种显式选项可组合，分片仍取卷 A 的逐条清单。
    out2 = os.path.join(work, "out_split_slice")
    p2 = _ph1(work, src, bib, out2, "--coop-split", "--coop-slice", "2")
    check("③-13 split×slice PH1 退出 0", p2.returncode == 0, p2.stderr[-400:])
    st2 = _state(_stage(out2))
    check("③-14 split×slice 同时写 volumes 与 slices",
          len(st2.get("taskbook_volumes") or []) == 3
          and len(st2.get("taskbook_slices") or []) == 2)
    check("③-15 split×slice 的分片仍来自卷 A 且键一致",
          [(s.get("volume"), s.get("keys")) for s in st2.get("taskbook_slices") or []]
          == [("A", BIB_KEYS[0:2]), ("A", BIB_KEYS[2:4])],
          str([(s.get("volume"), s.get("keys")) for s in st2.get("taskbook_slices") or []]))


# ── ④ 非法分片条数：退出 2 且不写状态/新产物/不交付 ──────────────────────


def test_invalid_slice_size(work: str) -> None:
    src, bib = _fixture(work, "invalid")
    # 负数：argparse 在转换开始前退出 2 → out 目录不存在（零写，CONTRACT-P03 §2）。
    out = os.path.join(work, "out_neg")
    p = _ph1(work, src, bib, out, "--coop-slice", "-1")
    check("④-1 负数 --coop-slice 退出 2", p.returncode == 2, str(p.returncode))
    check("④-2 负数报错可见（含「不能为负」）", "不能为负" in (p.stdout + p.stderr),
          (p.stdout + p.stderr)[-300:])
    check("④-3 负数零写：out 目录不存在或为空",
          not os.path.exists(out) or _listing(out) == [], str(_listing(out)))
    check("④-4 负数不写状态文件/分片/卷",
          glob.glob(os.path.join(out, "*", "_AI协同状态.json")) == []
          and _slice_files(out) == [] and _volume_files(out) == [],
          str(_listing(out)))
    check("④-5 负数不交付（无最终产物目录）",
          not any(os.path.isdir(os.path.join(out, n)) and not n.endswith("_待核")
                  for n in _listing(out)), str(_listing(out)))
    # 非法文本：同样由 argparse 在转换前退出 2，输出目录完全不建。
    out_bad = os.path.join(work, "out_bad_slice")
    pb = _ph1(work, src, bib, out_bad, "--coop-slice", "abc")
    check("④-6 非法文本 --coop-slice 退出 2", pb.returncode == 2, str(pb.returncode))
    check("④-7 非法文本报 argparse invalid int value",
          "invalid int value" in pb.stderr.lower())
    check("④-8 非法文本零写：不创建输出目录", not os.path.exists(out_bad))


# ── ⑤ 旧 state（缺新键）第二段照常可用 ───────────────────────────────────


def test_old_state_ph2(work: str) -> None:
    src, bib = _fixture(work, "oldstate")
    out = os.path.join(work, "out_old")
    p = _ph1(work, src, bib, out)
    check("⑤-1 旧 state 场景 PH1 退出 0", p.returncode == 0, p.stderr[-400:])
    stage = _stage(out)
    state_path = os.path.join(stage, "_AI协同状态.json")
    state = _state(stage)
    old_keys = ("taskbook_version", "taskbook_task_id",
                "taskbook_volumes", "taskbook_slices")
    removed = [k for k in old_keys if k in state]
    for k in removed:
        state.pop(k, None)
    _write(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    check("⑤-2 已移除全部 P03 新键（模拟旧 state）",
          not (set(old_keys) & set(state)) and "taskbook_version" in removed
          and "taskbook_task_id" in removed, str(removed))
    reply = _write_reply(work, "old_state_all", BIB_KEYS)
    pg = _ph2(work, src, out, reply)
    allout = pg.stdout + pg.stderr
    check("⑤-3 旧 state 第二段退出 0", pg.returncode == 0, allout[-500:])
    check("⑤-4 旧 state 第二段正常解析回填", "文献字段：解析到 4 条" in pg.stdout)
    check("⑤-5 旧 state 无新键不产生分片告警", "⚠ 分片" not in pg.stdout)
    check("⑤-6 旧 state 第二段正常交付", "完成。产物目录：" in pg.stdout)
    check("⑤-7 交付后暂存目录与 state 消失",
          not os.path.isdir(stage)
          and not os.path.isfile(os.path.join(stage, "_AI协同状态.json")))
    final = os.path.join(out, state.get("params", {}).get("stem", ""))
    check("⑤-8 最终目录有产物与协同记录",
          os.path.isfile(os.path.join(final, "reference.bib"))
          and os.path.isfile(os.path.join(final, "_AI协同记录.md")),
          str(_listing(final)))
    check("⑤-9 stdout 报暂存已清理且不误报未清理",
          "暂存目录已清理：" + stage in pg.stdout
          and "⚠ 暂存目录未清理" not in pg.stdout)


# ── ⑥ 第二段回填检查：缺片 / 串错片 / 整份拒收 / 交付清理 ────────────────


def _sliced_ph1(work: str, name: str) -> tuple[str, str, str, dict]:
    src, bib = _fixture(work, name)
    out = os.path.join(work, "out_" + name)
    p = _ph1(work, src, bib, out, "--coop-slice", "2")
    check(f"⑥-{name} 分片 PH1 退出 0", p.returncode == 0, p.stderr[-400:])
    stage = _stage(out)
    state = _state(stage)
    return src, out, stage, state


def test_ph2_checks(work: str) -> None:
    # (a) 缺片：只 ⚠ 提示，交付照常完成。
    src, out, stage, state = _sliced_ph1(work, "missing")
    slices = state.get("taskbook_slices") or []
    check("⑥-1 缺片场景确有 2 片", len(slices) == 2, str(slices))
    reply1 = _write_reply(work, "missing_slice1", slices[0]["keys"])
    p = _ph2(work, src, out, reply1)
    check("⑥-2 缺片时第二段退出 0 并交付", p.returncode == 0
          and "完成。产物目录：" in p.stdout, (p.stdout + p.stderr)[-500:])
    check("⑥-3 缺片给出「第 2/2 片未收到任何回填」告警",
          "⚠ 分片：第 2/2 片未收到任何回填（本片 2 条）" in p.stdout, p.stdout[-600:])
    check("⑥-4 缺片不阻断回填解析", "文献字段：解析到 2 条" in p.stdout)
    check("⑥-5 缺片交付后暂存目录被清理", not os.path.isdir(stage))

    # (b) 片标记越界（串稿/串错片）：整份拒收 rc2，state 零变化、不交付。
    src, out, stage, state = _sliced_ph1(work, "wrong")
    tid = state.get("taskbook_task_id", "")
    state_path = os.path.join(stage, "_AI协同状态.json")
    sha_before = _sha(state_path)
    reply_bad = _write_reply(work, "wrong_slice", slices[0]["keys"] if slices else ["k1", "k2"],
                             marker=f"<!-- B311-SLICE 9/2 · TASK-ID {tid} -->")
    pw = _ph2(work, src, out, reply_bad)
    allout = pw.stdout + pw.stderr
    check("⑥-6 片标记越界整份拒收并退出 2", pw.returncode == 2, str(pw.returncode))
    check("⑥-7 拒收行可见「分片拒收」", "⚠ 分片拒收：" in allout, allout[-600:])
    check("⑥-8 整份拒收说明「都因分片标记被拒收」", "都因分片标记被拒收" in allout)
    check("⑥-9 整份拒收不改 state（SHA 不变、phase 仍 ph1）",
          _sha(state_path) == sha_before and _state(stage).get("phase") == "ph1")
    stem = state.get("params", {}).get("stem", "")
    check("⑥-10 整份拒收不交付（最终目录不存在）",
          not os.path.isdir(os.path.join(out, stem)))

    # (c) 串错片（片号在范围内但内容属于别的片）：只 ⚠ 提示，不拒收。
    src, out, stage, state = _sliced_ph1(work, "cross")
    tid = state.get("taskbook_task_id", "")
    slices = state.get("taskbook_slices") or []
    reply_cross = _write_reply(work, "cross_slice",
                               slices[0]["keys"] if slices else ["k1", "k2"],
                               marker=f"<!-- B311-SLICE 2/2 · TASK-ID {tid} -->")
    pc = _ph2(work, src, out, reply_cross)
    check("⑥-11 串错片只告警、交付成功", pc.returncode == 0
          and "完成。产物目录：" in pc.stdout, (pc.stdout + pc.stderr)[-600:])
    check("⑥-12 串错片给出 ⚠ 分片提示",
          "⚠ 分片：" in pc.stdout and "标为第 2 片，但含第 1 片的键" in pc.stdout,
          pc.stdout[-700:])
    check("⑥-13 串错片不阻断回填解析", "文献字段：解析到 2 条" in pc.stdout)
    check("⑥-14 串错片交付后暂存目录被清理", not os.path.isdir(stage))

    # (d) 全部片正常回填：交付 + 暂存/state 清理 + 不误报未清理。
    src, out, stage, state = _sliced_ph1(work, "whole")
    all_keys = [k for s in state.get("taskbook_slices") or [] for k in s["keys"]]
    reply_all = _write_reply(work, "whole_all", all_keys)
    pd = _ph2(work, src, out, reply_all)
    check("⑥-15 全片回填退出 0 并交付", pd.returncode == 0
          and "完成。产物目录：" in pd.stdout, (pd.stdout + pd.stderr)[-500:])
    check("⑥-16 全片回填无缺片告警", "⚠ 分片" not in pd.stdout)
    check("⑥-17 交付后 state 文件消失且暂存目录被清理",
          not os.path.isfile(os.path.join(stage, "_AI协同状态.json"))
          and not os.path.isdir(stage))
    check("⑥-18 stdout 报暂存已清理且不误报未清理",
          "暂存目录已清理：" + stage in pd.stdout
          and "⚠ 暂存目录未清理" not in pd.stdout)
    stem = state.get("params", {}).get("stem", "")
    final = os.path.join(out, stem)
    check("⑥-19 最终目录保留产物与协同记录",
          os.path.isfile(os.path.join(final, "reference.bib"))
          and os.path.isfile(os.path.join(final, "_AI协同记录.md"))
          and os.path.isfile(os.path.join(final, f"{stem}.tex")),
          str(_listing(final)))


def main() -> int:
    work = tempfile.mkdtemp(prefix="p03_taskbook_modes_")
    os.makedirs(os.path.join(work, "no_manuscripts"), exist_ok=True)
    try:
        test_default_zero_change(work)
        test_split_volumes(work)
        test_slice_modes(work)
        test_invalid_slice_size(work)
        test_old_state_ph2(work)
        test_ph2_checks(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print()
    print(f"通过 {PASS} 项" + (f"，失败 {len(FAILS)} 项" if FAILS else "，失败 0 项"))
    for name in FAILS:
        print("FAILED: " + name)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
