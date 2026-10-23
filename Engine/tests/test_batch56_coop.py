#!/usr/bin/env python3
r"""第五十六批：两段式 AI 协同初排的**回填体验修复**离线判据。

背景（真实用户 2026-10-19 22:00 首测暴露的问题链）：
  · DeepSeek 按任务书返回两个文件（json 低置信回复 + txt 完整 bibtex），mac GUI 第二段
    只能导入一个路径（多选被折叠成父目录），用户只导入 json → 文献回填 0/9；
  · ph2 忽略 `--clean` → 交付目录多出 .aux/.bbl/.bcf/.blg/.out/.run.xml 六个中间文件；
  · 状态/摘要没有「回填 x/y」口径 → GUI 把 jsonl 的 accepted 22 显示成「待人工 0」；
  · 任务书第三节第 10 条「只输出那个 bibtex 代码块」与附一 JSON 要求打架。

覆盖（全部离线：不联网、不调付费 API、不读稿件库、不跑 TeX）：
  · `--coop-replies` 可重复：多路径按命令行顺序、目录内按文件名排序、同真实路径去重、
    digest 覆盖全部文本；**单路径与批55 逐字节一致**（名称/顺序/digest）；
  · 状态 JSON `stats`（schema 仍 1，向后兼容）：7 个字段的算法与写入；
  · ph2 `--clean`：交付目录不含中间文件，保留 .tex/.bib/.pdf/.log/`.ai前`/建议/记录/
    状态/`original/`；不开 --clean 时保持批55 行为（中间文件随交付）；
  · 0 文献回填醒目 warning + 统一最终摘要「回填 x（未回填 y）；低置信回复 z（可应用 a /
    仅记录待人工 b）」；
  · 任务书「附二、回填说明」与第三节第 10 条限定措辞；非协同任务书逐字节不变。

用法：
    cd Engine && PYTHONPATH=vendor python3 tests/test_batch56_coop.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import coop  # noqa: E402
from slcn import refmark  # noqa: E402

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


def _entry(key: str, title: str = "测试文献", author: str = "张三",
           year: str = "2020") -> str:
    return (f"@article{{{key},\n"
            f"  author = {{{author}}},\n"
            f"  title = {{{title}}},\n"
            f"  journal = {{逻辑学研究}},\n"
            f"  year = {{{year}}},\n"
            f"}}\n")


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# ── ① 多路径回填：读取 / 顺序 / 去重 / digest ────────────────────────────


def test_multi_reply_paths(tmp: str) -> None:
    base = os.path.join(tmp, "①")
    dir_a = os.path.join(base, "replies-a")
    os.makedirs(dir_a)
    a = _write(os.path.join(dir_a, "a.md"), "hello")
    b = _write(os.path.join(base, "b.json"), "{}")

    # 单路径：str 与「单元素列表」必须完全一致（批55 行为逐字节不变）。
    single_str = coop.load_reply_texts(a)
    single_list = coop.load_reply_texts([a])
    check("①-1 单路径 str 与单元素列表读取结果完全一致", single_str == single_list,
          f"{single_str!r} vs {single_list!r}")
    # digest 的编码是「名称\\0文本\\0」——硬编码批55 的算法结果，钉住不回归。
    batch55_digest = hashlib.sha256(b"a.md\x00hello\x00").hexdigest()
    check("①-2 单路径 digest 与批55 编码逐字节一致",
          coop.replies_digest(single_str) == batch55_digest,
          coop.replies_digest(single_str))
    check("①-3 单文件显示名仍是文件名（不带目录前缀）",
          [n for n, _t in single_str] == ["a.md"])

    # 同一真实路径去重（含 `.`/`..` 变体，realpath 归一）。
    dup = coop.load_reply_texts([b, b, os.path.join(base, ".", "b.json")])
    check("①-4 同一真实路径重复出现只读一次",
          [n for n, _t in dup] == ["b.json"]
          and coop.replies_digest(dup) == coop.replies_digest(coop.load_reply_texts(b)))

    # 多路径：按命令行顺序；显式文件保持文件名（不像旧 GUI 那样折叠成父目录）。
    multi = coop.load_reply_texts([b, a])
    check("①-5 多路径按命令行顺序读入", [n for n, _t in multi] == ["b.json", "a.md"],
          str([n for n, _t in multi]))
    check("①-6 多路径里显式文件不折叠成父目录（保持文件名）",
          [n for n, _t in multi] == ["b.json", "a.md"])

    # 多路径 digest 覆盖全部文本（少一个就变）且稳定。
    d_both = coop.replies_digest(multi)
    d_swap = coop.replies_digest(coop.load_reply_texts([a, b]))
    check("①-7 多路径 digest 覆盖全部文本（与单路径都不同）",
          d_both != coop.replies_digest(single_str)
          and d_both != coop.replies_digest(coop.load_reply_texts(b)))
    check("①-8 多路径 digest 稳定（顺序敏感、重复调用相同）",
          d_both == coop.replies_digest(coop.load_reply_texts([b, a])) and d_both != d_swap)

    # 目录内仍按文件名排序（批55 规则）。
    dir_c = os.path.join(base, "dir-c")
    os.makedirs(dir_c)
    _write(os.path.join(dir_c, "b.md"), "B")
    _write(os.path.join(dir_c, "a.txt"), "A")
    check("①-9 目录内按文件名排序", [n for n, _t in coop.load_reply_texts(dir_c)] == ["a.txt", "b.md"])
    multi_dir = coop.load_reply_texts([dir_c, b])
    check("①-10 目录 + 文件混合读取顺序与来源名正确",
          [n for n, _t in multi_dir] == ["dir-c/a.txt", "dir-c/b.md", "b.json"],
          str([n for n, _t in multi_dir]))

    try:
        coop.load_reply_texts([a, os.path.join(base, "不存在.txt")])
        missing_raised = False
    except FileNotFoundError:
        missing_raised = True
    check("①-11 多路径里有一个不存在 → 明确报错（不静默吞掉）", missing_raised)

    check("①-12 format_reply_paths 单/多路径都可读",
          coop.format_reply_paths(a) == a
          and coop.format_reply_paths([a, b]).count("、") == 1)


# ── ② stats / 状态（schema 仍 1，向后兼容）──────────────────────────────


def test_stats(tmp: str) -> None:
    st = coop.make_stats(bib_total=9, bib_replied=0, low_total=16,
                         low_replied=22, low_applied=9)
    check("②-1 stats 七字段齐全",
          set(st) == {"bib_total", "bib_replied", "bib_pending", "low_total",
                      "low_replied", "low_applied", "low_pending_manual"}, str(sorted(st)))
    check("②-2 未回填 = 总数 - 回填数；待人工 = 回复数 - 应用数",
          st["bib_pending"] == 9 and st["low_pending_manual"] == 13, str(st))
    check("②-3 应用数不超过回复数、负数被夹到 0",
          coop.make_stats(bib_total=1, bib_replied=2, low_total=0, low_replied=1,
                          low_applied=5) == {
              "bib_total": 1, "bib_replied": 2, "bib_pending": 0, "low_total": 0,
              "low_replied": 1, "low_applied": 1, "low_pending_manual": 0})

    src = _write(os.path.join(tmp, "②", "src.tex"), "\\documentclass{article}\n")
    meta = _write(os.path.join(tmp, "②", "meta.tex"), "\\title{X}\n")
    stage = os.path.join(tmp, "②", "_待核")
    os.makedirs(stage)
    params = coop.build_params(
        source=src, metadata=meta, out_root=tmp, stem="Out", lang="zh", sty="",
        skeleton="", verify_refs=True, no_ai_merge=True, stage_dir=stage)
    state = coop.build_state(
        params=params, taskbook_path="", stage_dir=stage, final_dir=os.path.join(tmp, "②", "Out"),
        bib_keys=["k1", "k2"], low_items=[{"id": "META-x", "kind": "metadata",
                                           "evidence": "x", "fields": ["value"], "need": ""}],
        budget_article=30000, budget_batch=1000000)
    st2 = coop.mark_state_ph2(state, replies_digest="d", replied=["k1"], applied=0,
                              rejected=0, pending=["k2"], stats=st)
    check("②-4 mark_state_ph2 写入 stats 且 schema 仍是 1",
          st2["stats"] == st and st2["schema"] == coop.SCHEMA_VERSION and st2["phase"] == "ph2")
    coop.save_state(stage, st2)
    loaded = coop.load_state(stage)
    check("②-5 stats 随状态 JSON 往返", loaded["stats"] == st)

    old = coop.mark_state_ph2(state, replies_digest="d", replied=[], applied=0,
                              rejected=0, pending=[])
    check("②-6 不传 stats 的旧调用不写该字段（向后兼容）", "stats" not in old)
    check("②-7 旧状态（无 stats）仍通过 ph2 指纹校验",
          coop.check_ph2(old, params)[0])


# ── ③ 任务书：附二回填说明 + 第三节第 10 条限定措辞 ──────────────────────


def test_taskbook(tmp: str) -> None:
    bib = _entry("k1")
    plain = refmark.build(bib, "S")
    check("③-1 非协同任务书逐字节不变（coop=False == 默认）",
          refmark.build(bib, "S", coop=False) == plain
          and refmark._ITEM10_PLAIN in plain
          and "只约束" not in plain)
    coop_md = refmark.build(bib, "S", coop=True)
    check("③-2 协同任务书第 10 条限定为「文献条目部分」",
          refmark._ITEM10_COOP in coop_md and "文献条目部分" in coop_md
          and "不受本条限制" in coop_md and "附一" in coop_md)
    check("③-3 协同任务书只改第 10 条，第三节其余内容仍在",
          "## 三、请交付**完整、可直接编译的 .bib 文本**" in coop_md
          and "**key 一律不改**" in coop_md and "只输出那个 bibtex 代码块" not in coop_md)

    sec = coop.render_backfill_section()
    check("③-4 附二小节标题与「两块都要交」",
          coop.SECTION_BACKFILL_TITLE in sec and "两块都要交" in sec)
    check("③-5 附二写明多文件/目录/剪贴板导入与 --coop-apply",
          "多个文件/目录" in sec and "剪贴板" in sec and "--coop-apply" in sec
          and "一键修改产物" in sec)

    # CLI：ph1 任务书里附一在前、附二在后，且第 10 条已限定。
    src, bibp, out = _fixture(os.path.join(tmp, "③"), _BIB)
    p1 = _run(["--source", src, "--bib", bibp, "--out", out, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    taskbook = _read(os.path.join(out, "San_Zhang_待核", "San_Zhang_文献核实.md"))
    check("③-6 ph1 任务书含附二回填说明", p1.returncode == 0
          and coop.SECTION_BACKFILL_TITLE in taskbook, p1.stderr[-300:])
    check("③-7 附一在附二之前",
          0 < taskbook.find(coop.SECTION_TITLE) < taskbook.find(coop.SECTION_BACKFILL_TITLE))
    check("③-8 任务书第 10 条已限定（不再绝对只说「只输出那个代码块」）",
          "文献条目部分" in taskbook and "不受本条限制" in taskbook)


# ── ④ CLI：多路径回填 + stats + 统一摘要 ─────────────────────────────────


def test_cli_multi_paths(tmp: str) -> None:
    src, bibp, out = _fixture(os.path.join(tmp, "④"), _BIB)
    p1 = _run(["--source", src, "--bib", bibp, "--out", out, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    stage = os.path.join(out, "San_Zhang_待核")
    final = os.path.join(out, "San_Zhang")
    check("④-1 ph1 成功", p1.returncode == 0, p1.stderr[-300:])

    r1 = _write(os.path.join(tmp, "④", "r1.md"), "解释\n" + _entry("k1", "甲核实版"))
    r2 = _write(os.path.join(tmp, "④", "r2.json"),
                "```bibtex\n" + _entry("k2", "乙核实版") + "```\n")
    p2 = _run(["--source", src, "--out", out, "--no-original",
               "--coop-replies", r1, "--coop-replies", r2])
    check("④-2 多个 --coop-replies 一次传入，ph2 成功", p2.returncode == 0, p2.stderr[-400:])
    fbib = _read(os.path.join(final, "reference.bib"))
    check("④-3 两个文件的回填都并入 reference.bib",
          "甲核实版" in fbib and "乙核实版" in fbib)
    check("④-4 日志写明「2 个文本 / 2 个路径」", "2 个文本 / 2 个路径" in p2.stdout)

    # B-211：promote 成功后暂存目录被清理；状态文件不随交付，stats/阶段改由
    # 交付目录里的协同记录与 stdout 摘要见证（state 本身已不可读）。
    rec = _read(os.path.join(final, coop.RECORD_NAME))
    check("④-5 交付成功：记录写明阶段 done 且暂存目录已清理",
          "阶段：`done`" in rec and not os.path.exists(stage))
    check("④-6 stats：文献 2/2、未回填 0、低置信回复 0",
          "- 解析到 2 条，拒绝 0 条，未回填 0 条" in rec
          and "- 接受 0 条，" in rec, rec[-700:])
    check("④-7 两个路径的回填都进入交付记录（已接受键 k1、k2）",
          "已接受键：k1、k2" in rec)
    check("④-8 最终摘要统一为「回填 x（未回填 y）；低置信回复 z（…）」",
          "回填：2 条（未回填 0 条）；低置信回复：0 条"
          "（可应用 0 条 / 仅记录待人工 0 条）" in p2.stdout, p2.stdout[-500:])
    check("④-9 有文献回填时不打 0 回填 warning",
          "本次未收到任何文献条目回填" not in p2.stdout)
    check("④-10 协同记录随交付进入最终目录",
          os.path.isfile(os.path.join(final, coop.RECORD_NAME)))

    # B-211：交付成功后暂存目录（含状态）已清理 → 重跑必须明确拒绝，
    # 绝不静默重交付或改写既有交付产物。
    p3 = _run(["--source", src, "--out", out, "--no-original",
               "--coop-replies", r1, "--coop-replies", r2])
    check("④-11 交付后重跑：明确拒绝（退出 2、指向暂存目录/第一段）且交付产物不变",
          p3.returncode == 2
          and ("找不到可续跑的协同初排暂存目录" in p3.stderr
               or "请先运行第一段" in p3.stderr)
          and _read(os.path.join(final, "reference.bib")) == fbib,
          p3.stderr[-500:])


# ── ⑤ CLI：0 文献回填 warning / stats / --coop-apply 计数 ─────────────────


def test_cli_zero_bib_warning(tmp: str) -> None:
    src, bibp, out = _fixture(os.path.join(tmp, "⑤"), _BIB_ONE_LOW)
    p1 = _run(["--source", src, "--bib", bibp, "--out", out, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    stage = os.path.join(out, "San_Zhang_待核")
    final = os.path.join(out, "San_Zhang")
    pret = _read(os.path.join(stage, "reference.bib"))
    reply = _write(os.path.join(tmp, "⑤", "low.json"),
                   '{"id":"BIB-k1","verdict":"corrected","field":"title",'
                   '"value":"AI 补的题名","span":"@article{k1"}\n')
    p2 = _run(["--source", src, "--out", out, "--no-original",
               "--coop-replies", reply])
    check("⑤-1 只有低置信 JSON、没有 bibtex 时 ph2 仍能交付（退出 0）",
          p1.returncode == 0 and p2.returncode == 0, p2.stderr[-400:])
    check("⑤-2 0 文献回填时打醒目 warning（明说 reference.bib 不会并入、可能漏导入）",
          "本次未收到任何文献条目回填" in p2.stdout
          and "reference.bib 不会并入 AI 修订" in p2.stdout
          and "可能漏导入" in p2.stdout
          and "--coop-replies 可重复传多个文件/目录" in p2.stdout, p2.stdout[-700:])
    rec = _read(os.path.join(final, coop.RECORD_NAME))
    check("⑤-3 记录：文献 0/2 回填、低置信回复 1 条仅记录待人工",
          "- 解析到 0 条，拒绝 0 条，未回填 2 条" in rec
          and "- 接受 1 条，" in rec, rec[-700:])
    check("⑤-4 交付的 reference.bib 未被回填改动（0 条并入）",
          _read(os.path.join(final, "reference.bib")) == pret)
    check("⑤-5 最终摘要「回填：0 条（未回填 2 条）；低置信回复：1 条（可应用 0 / 待人工 1）」",
          "回填：0 条（未回填 2 条）；低置信回复：1 条"
          "（可应用 0 条 / 仅记录待人工 1 条）" in p2.stdout, p2.stdout[-600:])

    # 勾 --coop-apply：同一条低置信建议落到产物 → stats 的 low_applied 必须是 1。
    src2, bib2, out2 = _fixture(os.path.join(tmp, "⑤b"), _BIB_ONE_LOW)
    p4 = _run(["--source", src2, "--bib", bib2, "--out", out2, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    stage2 = os.path.join(out2, "San_Zhang_待核")
    p5 = _run(["--source", src2, "--out", out2, "--no-original",
               "--coop-replies", reply, "--coop-apply"])
    final2 = os.path.join(out2, "San_Zhang")
    rec2 = _read(os.path.join(final2, coop.RECORD_NAME))
    check("⑤-6 --coop-apply 时 stats.low_applied=1、待人工 0",
          "AI 补的题名" in _read(os.path.join(final2, "reference.bib"))
          and "低置信回复：1 条（可应用 1 条 / 仅记录待人工 0 条）" in p5.stdout
          and "- `--coop-apply`：应用 **1** 条，待人工 0 条" in rec2
          and not os.path.exists(stage2), p5.stdout[-500:])
    check("⑤-7 --coop-apply 后文献回填仍是 0（低置信应用不算文献回填）",
          p4.returncode == 0 and p5.returncode == 0
          and "- 解析到 0 条，拒绝 0 条，未回填 2 条" in rec2)


# ── ⑥ CLI：ph2 --clean（交付目录不含中间文件；保留用户可见产物）────────────


_INTERMEDIATE_SUFFIXES = (".aux", ".bbl", ".bcf", ".blg", ".out", ".run.xml", ".synctex.gz")


def _plant_intermediates(stage: str, stem: str) -> None:
    for suf in _INTERMEDIATE_SUFFIXES:
        _write(os.path.join(stage, f"{stem}{suf}"), "x")
    _write(os.path.join(stage, f"{stem}.log"), "LOG")
    _write(os.path.join(stage, f"{stem}.pdf"), "PDF")
    _write(os.path.join(stage, "reference.bib.ai前"), "ROLLBACK")
    # 作者来稿归档目录（红线：清理器必须跳过）：里面即使有 .aux 也不许删。
    _write(os.path.join(stage, "original", "keep.aux"), "作者原文")


def test_cli_clean(tmp: str) -> None:
    src, bibp, out = _fixture(os.path.join(tmp, "⑥"), _BIB)
    p1 = _run(["--source", src, "--bib", bibp, "--out", out, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    stage = os.path.join(out, "San_Zhang_待核")
    final = os.path.join(out, "San_Zhang")
    _plant_intermediates(stage, "San_Zhang")
    reply = _write(os.path.join(tmp, "⑥", "r.md"), _entry("k1", "清理测试版"))
    p2 = _run(["--source", src, "--out", out, "--no-original",
               "--coop-replies", reply, "--clean"])
    check("⑥-1 ph2 --clean 退出 0", p1.returncode == 0 and p2.returncode == 0, p2.stderr[-400:])
    check("⑥-2 日志含清理段", "清理编译中间文件" in p2.stdout and "删除完成" in p2.stdout)
    names = sorted(os.listdir(final)) if os.path.isdir(final) else []
    leftovers = [n for n in names if any(n.endswith(s) for s in _INTERMEDIATE_SUFFIXES)]
    check("⑥-3 交付目录不含 .aux/.bbl/.bcf/.blg/.out/.run.xml/.synctex.gz",
          leftovers == [], str(leftovers))
    check("⑥-4 交付目录保留 .tex/.bib/.log/.pdf/.ai前/记录/建议/任务书/状态不随交付",
          all(n in names for n in ("San_Zhang.tex", "reference.bib", "San_Zhang.log",
                                   "San_Zhang.pdf", "reference.bib.ai前",
                                   coop.RECORD_NAME, "San_Zhang_AI建议.md",
                                   "San_Zhang_AI建议.jsonl", "San_Zhang_文献核实.md"))
          and coop.STATE_NAME not in names, str(names))
    check("⑥-5 original/（作者来稿归档）里的 .aux 一个都不删",
          os.path.isfile(os.path.join(final, "original", "keep.aux")))

    # 不开 --clean：保持批55 行为（中间文件随暂存目录一起交付），默认零变化。
    src2, bib2, out2 = _fixture(os.path.join(tmp, "⑥b"), _BIB)
    q1 = _run(["--source", src2, "--bib", bib2, "--out", out2, "--author", "张三",
               "--no-original", "--verify-refs", "--no-ai-merge", "--coop-taskbook-only"])
    stage2 = os.path.join(out2, "San_Zhang_待核")
    final2 = os.path.join(out2, "San_Zhang")
    _plant_intermediates(stage2, "San_Zhang")
    q2 = _run(["--source", src2, "--out", out2, "--no-original",
               "--coop-replies", reply])
    names2 = sorted(os.listdir(final2)) if os.path.isdir(final2) else []
    check("⑥-6 不开 --clean 时行为与批55 一致（中间文件照旧随交付）",
          q1.returncode == 0 and q2.returncode == 0
          and "San_Zhang.aux" in names2 and "San_Zhang.bbl" in names2
          and "清理编译中间文件" not in q2.stdout, str(names2))


# ── ⑦ 默认路径零变化（不传任何新参数 → 旧行为/旧产物）────────────────────


def test_default_unchanged(tmp: str) -> None:
    src, bibp, out = _fixture(os.path.join(tmp, "⑦"), _BIB)
    a = _run(["--source", src, "--bib", bibp, "--out", out, "--author", "张三",
              "--no-original"])
    final = os.path.join(out, "San_Zhang")
    check("⑦-1 默认转换退出 0 且产物齐全", a.returncode == 0
          and os.path.isfile(os.path.join(final, "San_Zhang.tex"))
          and os.path.isfile(os.path.join(final, "reference.bib")))
    check("⑦-2 默认路径不产生任何协同状态/记录/建议",
          not os.path.exists(os.path.join(final, coop.STATE_NAME))
          and not os.path.exists(os.path.join(final, coop.RECORD_NAME))
          and not any(n.startswith("San_Zhang_AI建议") for n in os.listdir(final)))
    check("⑦-3 默认路径日志不出现任何 coop 参数/新措辞",
          "--coop-" not in a.stdout and "本次未收到任何文献条目回填" not in a.stdout
          and "清理编译中间文件" not in a.stdout)


# ── CLI 辅助（与批54 同款；离线、临时目录、无 API 环境变量）────────────────


_TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文测试。

\begin{thebibliography}{9}
\bibitem{k1} 张三. 测试文献一[J]. 逻辑学研究, 2020, 13(2): 1--10.
\bibitem{k2} 李四. 测试文献二[J]. 逻辑学研究, 2021, 14(3): 11--20.
\end{thebibliography}
\end{document}
"""

_BIB = """@article{k1,
  author = {张三},
  title = {测试文献一},
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

#: k1 缺 title → ph1 会收集到低置信项 `BIB-k1`（用于 0 文献回填 warning 用例）。
_BIB_ONE_LOW = """@article{k1,
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


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SLCN_LLM_") and not k.startswith("SLCN_SEARCH_")}
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    return subprocess.run([sys.executable, os.path.join(_ENGINE, "convert.py"), *args],
                          cwd=cwd or _ENGINE, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180, env=env)


def _fixture(tmp: str, bib: str = _BIB) -> tuple[str, str, str]:
    # 源稿放 `src/`、产物放 `out/`（与真实用法一致；混一起会触发自动前言发现）。
    srcdir = os.path.join(tmp, "src")
    os.makedirs(srcdir, exist_ok=True)
    src = _write(os.path.join(srcdir, "main.tex"), _TEX)
    bibp = _write(os.path.join(srcdir, "refs.bib"), bib)
    out = os.path.join(tmp, "out")
    return src, bibp, out


# ── main ─────────────────────────────────────────────────────────────────


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="slcn_b56_") as tmp:
        test_multi_reply_paths(tmp)
        test_stats(tmp)
        test_taskbook(tmp)
        test_cli_multi_paths(tmp)
        test_cli_zero_bib_warning(tmp)
        test_cli_clean(tmp)
        test_default_unchanged(tmp)
    print()
    print(f"断言通过 {PASS} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  FAIL:", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
