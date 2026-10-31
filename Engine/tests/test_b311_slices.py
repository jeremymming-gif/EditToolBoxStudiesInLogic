#!/usr/bin/env python3
r"""B-311（B-006）：任务书自动分片 + 第二段缺片/串稿提示的离线判据测试。

覆盖（全部离线：不联网、不调 API、不跑 TeX）：
  · `--coop-slice N` 口径：缺省/0=关、负数退出 2；
  · 卷 A 逐条清单按 N 条切片：文件名/片头标记/TASK-ID/条目区间/键清单/页脚；
  · 默认路径零变化：不分片时 state 不出现 `taskbook_slices`、目录里没有分片文件；
  · 第二段：缺片只提示（回填/并入口径不变）、TASK-ID 不符按串稿拒收；
  · `parse_replies` 的既有拒绝文案与语义**不变**（重复键仍整条拒绝）。

用法：
    cd Engine && PYTHONPATH=vendor /opt/homebrew/bin/python3.14 tests/test_b311_slices.py
"""

from __future__ import annotations

import glob
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
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if cond:
        PASS += 1
    else:
        FAILS.append(name)
    return bool(cond)


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _entry(key: str) -> str:
    return (f"@article{{{key},\n  author = {{作者}},\n  title = {{标题}},\n"
            f"  journal = {{逻辑学研究}},\n  year = {{2020}},\n}}\n")


def _taskbook(n: int = 5, *, task_id: str = "BIB-20261031-test",
              stem: str = "测试稿") -> str:
    """仿 refmark 产物的三卷任务书（卷 A 有 `## 四、逐条清单` + n 条）。"""
    lines = [
        f"# 参考文献核实任务书 · {stem}", "",
        f"- **TASK-ID**：`{task_id}`｜**任务书版本**：v2", "",
        f"- 条目总数：**{n}**", "", "---", "", "## 一、规范", "",
        "## 四、逐条清单", "",
    ]
    for i in range(n):
        lines += [f"### `k{i}` — `@article`", "", "```",
                  f"@article{{k{i},", f"  title = {{标题{i}}},", "}", "```", "",
                  "- 离线核对未发现问题", ""]
    lines += ["## 附一、初排低置信项（AI 协同核实；机器可读 ID）", "", "- FN-1", "",
              "## 附二、回填说明（AI 协同初排专用）", "", "回填两部分。", ""]
    return ("\n".join(lines) + "\n---\n\n## 卷 B 全文核查\n\n空\n"
            "\n---\n\n## 卷 C 元数据与脚注\n\n空\n")


def _n_entries(body: str) -> int:
    return len(re.findall(r"(?m)^### `", body))


# ── ① 切片口径与文件内容 ─────────────────────────────────────────────────


def test_slice_units(tmp: str) -> None:
    text = _taskbook(5)
    out = os.path.join(tmp, "units")
    os.makedirs(out)

    check("①-1 缺省 None=关", coop.normalize_slice_size(None) == 0)
    check("①-2 0=关（任务书口径，不报错）", coop.normalize_slice_size(0) == 0)
    check("①-3 正数原样", coop.normalize_slice_size(3) == 3)
    try:
        coop.normalize_slice_size(-1)
        neg_ok = False
    except ValueError:
        neg_ok = True
    check("①-4 负数 → ValueError（CLI 层退出 2）", neg_ok)

    check("①-5 size=0 不落盘、返回空清单",
          coop.write_taskbook_slices(text, stem="测试稿", out_dir=out, size=0) == []
          and os.listdir(out) == [])
    check("①-6 条目数 ≤ size → 不分片",
          coop.write_taskbook_slices(text, stem="测试稿", out_dir=out, size=5) == []
          and os.listdir(out) == [])

    slices = coop.write_taskbook_slices(text, stem="测试稿", out_dir=out, size=2)
    check("①-7 5 条按 2 条/片 → 3 片",
          [s["index"] for s in slices] == [1, 2, 3]
          and all(s["total"] == 3 and s["volume"] == "A" for s in slices))
    check("①-8 文件名 `<stem>_文献核实_分片{k}-{n}.md`",
          [os.path.basename(s["path"]) for s in slices]
          == [f"测试稿_文献核实_分片{k}-3.md" for k in (1, 2, 3)])
    check("①-9 区间/键清单连续",
          [(s["first"], s["last"], s["keys"]) for s in slices]
          == [(1, 2, ["k0", "k1"]), (3, 4, ["k2", "k3"]), (5, 5, ["k4"])])

    bodies = [_read(s["path"]) for s in slices]
    check("①-10 每片都有 B311-SLICE 头 + 结尾标记",
          all(b.startswith(f"<!-- B311-SLICE {k}/3 · TASK-ID BIB-20261031-test")
              and f"<!-- B311-SLICE-END {k}/3 · 本片" in b
              for k, b in zip((1, 2, 3), bodies)))
    check("①-11 片头 TASK-ID 可解析且与任务书一致",
          [coop.parse_slice_marker(b)["task_id"] for b in bodies]
          == ["BIB-20261031-test"] * 3)
    check("①-12 每片都有可见标题（分片 k/n）与整片回传说明",
          all(f"# 参考文献核实任务书 · 测试稿（分片 {k}/3）" in b
              and "回填时请**整片**回传" in b
              for k, b in zip((1, 2, 3), bodies)))
    check("①-13 每片都重复公共头部（任务卡/规范/逐条清单标题）",
          all("**TASK-ID**" in b and "## 一、规范" in b
              and "## 四、逐条清单" in b for b in bodies))
    check("①-14 每片只含本片条目 + 卷 A 页脚",
          [_n_entries(b) for b in bodies] == [2, 2, 1]
          and all("### 卷 A · 回填模板" in b for b in bodies))
    check("①-15 分片不夹带附一/附二小节与卷 B/C",
          all("\n## 附一、" not in b and "\n## 附二、" not in b
              and "## 卷 B" not in b and "## 卷 C" not in b for b in bodies))
    check("①-16 任务书本体没有被切片改写（函数只读文本）",
          _read(os.path.join(out, "测试稿_文献核实_分片1-3.md")).count("### `") == 2)

    # 无 TASK-ID 的兜底任务书：片头省略 TASK-ID 段，仍能解析片号。
    no_id = _taskbook(3, task_id="").replace(
        "- **TASK-ID**：``｜**任务书版本**：v2\n\n", "")
    out2 = os.path.join(tmp, "units2")
    s2 = coop.write_taskbook_slices(no_id, stem="无卡", out_dir=out2, size=2)
    check("①-17 无 TASK-ID 时片头省略该段且仍可解析",
          len(s2) == 2 and coop.parse_slice_marker(_read(s2[0]["path"]))["index"] == 1
          and "TASK-ID" not in _read(s2[0]["path"]).splitlines()[0])

    # 空清单（没有 `## 四、逐条清单`）→ 不分片。
    out3 = os.path.join(tmp, "units3")
    os.makedirs(out3)
    check("①-18 没有逐条清单 → 不分片（兜底任务书路径）",
          coop.write_taskbook_slices("# 任务书\n\n> 只有低置信项\n",
                                     stem="兜底", out_dir=out3, size=2) == []
          and os.listdir(out3) == [])

    check("①-19 卷 A 提取不越过卷 B 标题",
          "## 卷 B" not in coop.taskbook_volume_a(text))

    good = coop.check_slice_set(slices, "BIB-20261031-test")
    check("①-20 正常分片清单自检 0 告警", good == [], str(good))


# ── ② 分片校验：缺片 / 重复 / 片标记与文件名不符 ─────────────────────────


def test_slice_checks(tmp: str) -> None:
    out = os.path.join(tmp, "checks")
    os.makedirs(out)
    slices = coop.write_taskbook_slices(_taskbook(5), stem="校验", out_dir=out, size=2)

    def paths_copy():
        c = os.path.join(tmp, "checks_copy")
        if os.path.isdir(c):
            shutil.rmtree(c)
        shutil.copytree(out, c)
        return [dict(s, path=os.path.join(c, os.path.basename(s["path"])))
                for s in slices]

    # 缺片（文件不在）
    cp = paths_copy()
    os.remove(cp[1]["path"])
    warns = coop.check_slice_set(cp, "BIB-20261031-test")
    check("②-1 分片文件缺失 → 告警",
          any("分片文件缺失" in w for w in warns), str(warns))

    # 缺片（编号不连续，文件都在）
    cp = paths_copy()
    warns = coop.check_slice_set([cp[0], cp[2]], "BIB-20261031-test")
    check("②-2 编号不连续（缺第 2 片）→ 告警",
          any("缺第 2 片" in w for w in warns), str(warns))

    # 重复编号
    warns = coop.check_slice_set([cp[0], cp[0], cp[2]], "BIB-20261031-test")
    check("②-3 分片编号重复 → 告警",
          any("分片编号重复" in w for w in warns), str(warns))

    # 片标记与文件名不符（改文件名）
    cp = paths_copy()
    wrong_name = os.path.join(os.path.dirname(cp[0]["path"]), "乱名.md")
    os.replace(cp[0]["path"], wrong_name)
    warns = coop.check_slice_set([dict(cp[0], path=wrong_name), cp[1], cp[2]],
                                 "BIB-20261031-test")
    check("②-4 片标记与文件名不符 → 告警",
          any("片标记与文件名不符" in w for w in warns), str(warns))

    # TASK-ID 不符（串稿）
    warns = coop.check_slice_set(slices, "BIB-OTHER")
    check("②-5 分片 TASK-ID 与任务书不符 → 告警",
          any("TASK-ID 不符" in w for w in warns), str(warns))

    check("②-6 空清单/坏项不炸",
          coop.check_slice_set([], "x") == []
          and coop.check_slice_set([{"index": "x"}], "x"))


# ── ③ 第二段：缺片只提示、串稿按标记拒收、既有拒绝文案不变 ───────────────


def test_reply_side(tmp: str) -> None:
    out = os.path.join(tmp, "replies")
    os.makedirs(out)
    slices = coop.write_taskbook_slices(_taskbook(4), stem="回填", out_dir=out, size=2)
    body1 = _read(slices[0]["path"])
    body2 = _read(slices[1]["path"])

    kept, rejected = coop.filter_slice_replies(
        [("a.md", body1), ("plain.md", _entry("k9"))],
        taskbook_task_id="BIB-20261031-test", slices=slices)
    check("③-1 片标记正确 → 保留；没有片标记的旧回复原样保留",
          [n for n, _t in kept] == ["a.md", "plain.md"] and rejected == [])

    bad = body1.replace("TASK-ID BIB-20261031-test", "TASK-ID BIB-OTHER")
    kept, rejected = coop.filter_slice_replies(
        [("bad.md", bad)], taskbook_task_id="BIB-20261031-test", slices=slices)
    check("③-2 片标记 TASK-ID 不符 → 按串稿拒收",
          kept == [] and len(rejected) == 1 and "串稿" in rejected[0][1])

    bad_idx = body1.replace("B311-SLICE 1/2", "B311-SLICE 9/9")
    kept, rejected = coop.filter_slice_replies(
        [("bad.md", bad_idx)], taskbook_task_id="BIB-20261031-test", slices=slices)
    check("③-3 片号不在本次清单 → 按串稿拒收",
          kept == [] and "串稿" in rejected[0][1])

    rep = coop.parse_replies([("a.md", body1)], ["k0", "k1", "k2", "k3"])
    lines = coop.slice_reply_lines(slices, rep)
    check("③-4 缺片只提示：第 2 片 0 条 → 一行提示",
          lines == ["第 2/2 片未收到任何回填（本片 2 条）"], str(lines))
    rep2 = coop.parse_replies([("a.md", body1), ("b.md", body2)],
                              ["k0", "k1", "k2", "k3"])
    check("③-5 两片都回填 → 不提示缺片", coop.slice_reply_lines(slices, rep2) == [])

    cross = coop.slice_cross_lines(
        slices, [("a.md", body1.replace("@article{k1,", "@article{k3,"))])
    check("③-6 错片（第 1 片文件里出现第 2 片的键）只提示、不拒收",
          len(cross) == 1 and "第 2 片" in cross[0], str(cross))

    # 既有拒绝文案/语义不变：同一键出现两次 → 整条拒绝，原因仍是「重复键」。
    dup = coop.parse_replies(
        [("dup.md", body1 + "\n" + body1)], ["k0", "k1"])
    check("③-7 分片文本里的重复键仍走既有拒绝文案",
          "重复键：同一条回复里出现 2 次" in dup.rejected.get("k0", ""),
          dup.rejected.get("k0", ""))

    check("③-8 目录输入跳过任务书/分片/卷文件",
          coop._is_coop_artifact_name("稿_文献核实.md")
          and coop._is_coop_artifact_name("稿_文献核实_分片1-2.md")
          and coop._is_coop_artifact_name("稿_文献核实_卷A.md")
          and not coop._is_coop_artifact_name("我的回复.md")
          and not coop._is_coop_artifact_name("文献核实_回复.md"))

    check("③-9 state_slices 只收对象、旧状态返回空",
          coop.state_slices({}) == []
          and len(coop.state_slices({"taskbook_slices": [{"index": 1}, "坏"]})) == 1)


# ── ④ CLI：默认路径零变化 + 分片 ph1/ph2 闭环 ─────────────────────────────

_TEX = r"""\documentclass{article}
\title{切片测试}
\author{张三}
\begin{document}
\maketitle
正文测试。

\begin{thebibliography}{9}
\bibitem{k1} 张三. 甲[J]. 逻辑学研究, 2020.
\bibitem{k2} 李四. 乙[J]. 逻辑学研究, 2021.
\bibitem{k3} 王五. 丙[J]. 逻辑学研究, 2022.
\bibitem{k4} 赵六. 丁[J]. 逻辑学研究, 2023.
\end{thebibliography}
\end{document}
"""

_BIB = "".join(_entry(k) for k in ("k1", "k2", "k3", "k4"))


def _run(args: list[str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SLCN_LLM_") and not k.startswith("SLCN_SEARCH_")}
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    return subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"), *args],
                          cwd=_ENGINE, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180, env=env)


def _fixture(tmp: str, name: str) -> tuple[str, str, str]:
    srcdir = os.path.join(tmp, name, "src")
    src = _write(os.path.join(srcdir, "main.tex"), _TEX)
    bib = _write(os.path.join(srcdir, "refs.bib"), _BIB)
    return src, bib, os.path.join(tmp, name, "out")


def _ph1(src: str, bib: str, out: str, *extra: str) -> subprocess.CompletedProcess:
    return _run(["--source", src, "--bib", bib, "--out", out, "--author", "张三",
                 "--no-original", "--verify-refs", "--no-ai-merge",
                 "--coop-taskbook-only", *extra])


def _stage(out: str) -> str:
    hits = glob.glob(os.path.join(out, "*_待核"))
    check("④-0 找到暂存目录", len(hits) == 1, str(hits))
    return hits[0] if hits else ""


def test_cli(tmp: str) -> None:
    src, bib, out = _fixture(tmp, "plain")
    p = _ph1(src, bib, out)
    check("④-1 默认（不带 --coop-slice）ph1 退出 0", p.returncode == 0, p.stderr[-300:])
    stage = _stage(out)
    state = json.loads(_read(os.path.join(stage, "_AI协同状态.json")))
    check("④-2 默认状态没有 taskbook_slices（schema 不 bump、默认零变化）",
          "taskbook_slices" not in state and coop.SCHEMA_VERSION == 1)
    check("④-3 默认不写分片文件",
          glob.glob(os.path.join(stage, "*_文献核实_分片*.md")) == [])
    check("④-4 默认任务书里没有 B311-SLICE 标记",
          "B311-SLICE" not in _read(os.path.join(stage, "San_Zhang_文献核实.md")))
    check("④-5 默认 stdout 没有分片行", "任务书分片" not in p.stdout)

    src0, bib0, out0 = _fixture(tmp, "zero")
    p0 = _ph1(src0, bib0, out0, "--coop-slice", "0")
    st0 = _stage(out0)
    check("④-6 --coop-slice 0 = 关（与默认同样不落盘）",
          p0.returncode == 0 and glob.glob(os.path.join(st0, "*_分片*.md")) == []
          and "taskbook_slices" not in
          json.loads(_read(os.path.join(st0, "_AI协同状态.json")))
          and "任务书分片" not in p0.stdout)

    srcn, bibn, outn = _fixture(tmp, "neg")
    pn = _ph1(srcn, bibn, outn, "--coop-slice", "-1")
    check("④-7 负数 --coop-slice → 退出 2 且说明",
          pn.returncode == 2 and "不能为负" in (pn.stdout + pn.stderr), pn.stderr[-200:])

    src2, bib2, out2 = _fixture(tmp, "sliced")
    p2 = _ph1(src2, bib2, out2, "--coop-slice", "2")
    check("④-8 分片 ph1 退出 0", p2.returncode == 0, p2.stderr[-300:])
    st2 = _stage(out2)
    files = sorted(os.path.basename(x)
                   for x in glob.glob(os.path.join(st2, "*_文献核实_分片*.md")))
    check("④-9 2 条/片 → 2 个分片文件", files
          == ["San_Zhang_文献核实_分片1-2.md", "San_Zhang_文献核实_分片2-2.md"],
          str(files))
    st = json.loads(_read(os.path.join(st2, "_AI协同状态.json")))
    check("④-10 state 写入 taskbook_slices（路径/区间/键）",
          [(s["index"], s["total"], s["first"], s["last"], s["keys"])
           for s in st.get("taskbook_slices") or []]
          == [(1, 2, 1, 2, ["k1", "k2"]), (2, 2, 3, 4, ["k3", "k4"])])
    check("④-11 stdout 报告分片数",
          "任务书分片：每片 ≤ 2 条，共 2 片" in p2.stdout)

    # ② 串稿：拿第 1 片、改掉片头 TASK-ID → ph2 按标记拒收。
    bad_path = _write(os.path.join(tmp, "sliced", "wrong.md"),
                      _read(os.path.join(st2, "San_Zhang_文献核实_分片1-2.md"))
                      .replace("TASK-ID BIB-San_Zhang-", "TASK-ID BIB-OTHER-"))
    pw = _run(["--source", src2, "--out", out2, "--coop-replies", bad_path,
               "--no-original"])
    _pw_all = pw.stdout + pw.stderr
    check("④-12 串稿回复 → 退出 2（按标记拒收、不并入）",
          pw.returncode == 2 and "分片拒收" in _pw_all
          and "都因分片标记被拒收" in _pw_all, _pw_all[-400:])

    # ③ 正确回填第 1 片：第 2 片缺片只提示，交付照常完成。
    good = os.path.join(st2, "San_Zhang_文献核实_分片1-2.md")
    pg = _run(["--source", src2, "--out", out2, "--coop-replies", good,
               "--no-original"])
    check("④-13 正常分片回填 → 退出 0 并交付", pg.returncode == 0, pg.stderr[-300:])
    check("④-14 缺片只提示（不改变回填/并入口径）",
          "第 2/2 片未收到任何回填（本片 2 条）" in pg.stdout
          and "文献字段：解析到 2 条" in pg.stdout, pg.stdout[-500:])
    check("④-15 交付完成", "完成。产物目录" in pg.stdout)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="b311_slices_")
    try:
        test_slice_units(tmp)
        test_slice_checks(tmp)
        test_reply_side(tmp)
        test_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"通过 {PASS} 项" + (f"，失败 {len(FAILS)} 项" if FAILS else "，失败 0 项"))
    for name in FAILS:
        print("FAILED: " + name)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
