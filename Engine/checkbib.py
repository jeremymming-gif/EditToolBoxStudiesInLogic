#!/usr/bin/env python3
r"""退回版 .bib 核对（**纯离线**）。

缺口
----
编辑部按《文献核实任务书》（`slcn/refmark.py` 生成）把 bib 交给各自的 AI（或自己）改，
拿回来的 `.bib` **此前没有任何工具检查**：key 被改名、字段被删、类型被改、
`doi` 又写回来了——这些都要等编译报错或人工发现。

本工具做的就是这件事：把「拿回来的 bib」按**同一条编辑部规范**核对一遍，出人可读报告。

四类核对
--------
  ① 可解析性：条目能不能拆开、花括号/引号配不配平、key 有没有重复。
     **损坏的文件也要出报告，不能让工具自己崩掉。**
  ② 编辑部规范：必填字段（`slcn/bibcheck.py` 的 REQUIRED 表）、`doi`/`isbn` 不收
     （编辑部 2026-10-14）、中文条目 `userf = {cn}`、页码 `--`、期号去前导零、
     月份三字母、**英文 `author`/`editor` 未倒装**（编辑部 2026-10-15，**问题**）、
     **中文译著缺「（著）/（译）」标记**（**提示**）——值层面的规则一律调
     `slcn/bibnorm.py`，**不另写一套**。
     缺字段按任务书第三节第 5 条分档：条目上方**点名**了 `% ⚠ 待核实：<字段>` → 提示
     （查不到允许省略，编辑仍需补）；没点名或干脆没注释 → 问题（看不出是查不到还是漏写）。
  ③ 引用键闭合（`--tex`）：正文 `\parencite{…}` 引的键必须在 bib 里有；
     bib 里从没被引用的键报提示。**这是最危险的一类**：改 key 会让全文引用悬空。
  ④ 与 `--original` 的 diff（`--original`）：逐键比字段，**key 改名/删除、字段被删、
     类型被改**按问题报；未改动的条目只给一行汇总，不逐条刷屏。
  ⑤ **并回**（`--merge-into <产物 reference.bib>`，2026-10-15 加，phase 2b）：把
     `--returned`（编辑拿给**自己的联网 AI** 改完交回的**整份** .bib）并回产物里的 bib。
     合并核心与「初排转换」的自动通道**同一份**（`slcn/aibib.py: merge_returned_bib()`）：
     四条硬保障（key 不可改 / 恰好一条可解析 / 不增删条目 / 未返回的逐字节不动）、
     作者倒装、字段顺序重排、回滚副本 `<目标>.ai前` 全部一致。并完再跑一遍 ①②
     （对合并后的 bib）并把**结论一行**打出来。
  ⑥ **粘贴模式**（`--returned-stdin`，2026-10-15 加，phase 2c）：交回的 bib 从**标准输入**
     读进来，不落任何中间文件。它只是 `--returned <文件>` 的**另一种来源**——①②③④⑤
     的判据一条都不变（体检与并回两种模式都支持）。为什么要有它：编辑常常是把 AI 的回复
     直接**粘进界面**，为了体检/并回先写一个临时 .bib 再删掉，既多一次落盘又多一次
     「删干净了吗」的心智负担；而且临时文件名会进报告头与合并记录，事后对不上账。
     空输入（一个非空白字符都没有）算**用法错误** → 退出码 2，附中文说明。
  ⑦ **列表模式**（`--list-keys`，2026-10-15 加，phase 2c）：只打印**一行**
     「`20 条：Ayer1946, Burge1984, …`」（前 8 个键，多的用 `…` 收尾），让界面在
     **跑体检/并回之前**就能告诉编辑「你粘的这段到底被识别成几条、键是什么」。
     它只调 `slcn.bibparse.parse_entries()`，**永不写文件**——哪怕同时给了 `--out` /
     `--merge-into` 也只打印这一行、绝不落盘（预览不许有副作用）。

**本文件现在只是一个薄壳**（2026-10-15 起）：解析器在 `slcn/bibparse.py`，
核对与报告在 `slcn/bibreport.py`，合并核心在 `slcn/aibib.py`。抽出去的原因见那几个
文件的头注释——同一份报告 `convert.py` 也要出（把 AI 结果并入 `reference.bib` 之后
立刻自检），两份实现迟早会分叉，所以留**一份**。**行为一字未变**，
`tests/test_checkbib.py` 的断言就是这次搬迁的保护网。

硬性约束（本文件必须一直满足）
------------------------------
  · **不 import `slcn.llm`**、不联网、不调任何模型；
  · 不改任何既有仓库文件——只**复用** `slcn.bibcheck` / `slcn.bibnorm` 的规则与函数；
  · 不写 `out/`、`dist/`，不跑 `corpus.py` / `build.sh`，不做 git 写操作。

退出码
------
体检模式（默认）：`0` = 无问题；`1` = 至少一处问题；`2` = 用法 / IO 错误。
**并回模式**（`--merge-into`）：`0` = **未改动**（没有任何条目并进去，且一个文件都没写）；
`1` = **已并入 ≥1 条**（与「有没有问题」无关，报告结论另打在控制台）；
`2` = 用法 / IO 错误。⚠️ 同一个退出码在两种模式下含义不同，脚本调用请按模式判读。
**列表模式**（`--list-keys`）：`0` = 打印了那一行；`2` = 用法 / IO 错误（含 stdin 为空）——
它不判断 bib 好不好，所以**不会**返回 1（预览不是体检）。
"""

from __future__ import annotations

import argparse
import re
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ⚠️ 只允许这几处复用：都是纯离线模块（解析 = `slcn.bibparse`，报告 = `slcn.bibreport`，
#    规则 = `slcn.bibcheck` / `slcn.bibnorm`，合并核心 = `slcn.aibib`）。
#    `tests/test_checkbib.py` 的 ⑤ 组会用 AST 扫本文件的 import，把这几个模块**逐个**钉住
#    （并同样扫描新模块自己的 import）——这条纪律的目的是「核实文献真伪是编辑的事」，
#    不能让工具偷偷联网或调模型。
#    `slcn.aibib` 是 2026-10-15 加「并回」（`--merge-into`）时进来的：手动通道必须走
#    与自动通道**同一个**合并核心，否则「自动挡下了改名、手动却放过去」这种分叉迟早
#    会出现。它同样是纯离线的（只 import 标准库 + bibcheck/bibnorm/bibparse/bibreport），
#    ⑤ 组因此把它加进了允许清单，并**单独**对它扫一遍「不联网 / 不调模型」。
from slcn import aibib, bibreport  # noqa: E402
from slcn import platform as _plat  # noqa: E402
from slcn.bibparse import (  # noqa: E402,F401  （薄壳：旧名字全部原样导出，测试与下游不受影响）
    LEVEL_INFO,
    LEVEL_PROBLEM,
    Entry,
    Finding,
    NON_ENTRY_TYPES,
    parse_entries,
    parse_one,
)
from slcn.bibreport import (  # noqa: E402,F401
    BULLET_LIMIT,
    SECTION_ORDER,
    SECTION_TITLES,
    SOURCE_NOTE_CHECKBIB,
    SPEC_FIELD_ORDER,
    DiffRow,
    Report,
    analyze,
    build_report,
    build_report_and_conclusion,
    check_citations,
    check_diff,
    check_spec,
    conclusion_line,
    read_text,
    render,
)

# 旧名字：从前是 `checkbib._read_text()`，测试与别的会话可能还按这个名字调。
_read_text = read_text


# ── ⑥ 粘贴模式：交回的 bib 从 stdin 读（不落中间文件）─────────────────────

#: `--list-keys` 一行里最多点几个键（多了用 `…` 收尾）——预览要短，不能把界面撑开。
LIST_KEYS_PREVIEW = 8


def _read_stdin() -> tuple[str, str]:
    """从标准输入读交回的 bib（粘贴模式）。返回 `(文本, 编码提示)`。

    **为什么走 stdin 而不是写临时文件**：编辑常常把 AI 的回复直接粘进界面。若先写
    `/tmp/xxx.bib` 再删，就多一次落盘、多一次「删干净了吗」，而且那个临时文件名会
    印进报告头与合并记录，事后对不上账。stdin 把「文本」当成与「文件」平级的一种来源，
    体检/并回的判据（①②③④⑤）一条都不用变。

    **编码口径与读文件完全一致**（`slcn/bibreport.read_text()`）：UTF-8 → GB18030 降级。
    这里读 `sys.stdin.buffer`（原始字节）而不是文本流——文本流的编码取决于终端 locale，
    同一段 UTF-8 在有的机器上会被按别的编码解成乱码。
    """
    buf = getattr(sys.stdin, "buffer", None)
    raw = buf.read() if buf is not None else sys.stdin.read().encode("utf-8")
    for enc in ("utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        note = "" if enc == "utf-8" else f"标准输入不是 UTF-8，已按 {enc} 读入（请确认没有乱码）"
        return text, note
    return raw.decode("utf-8", errors="replace"), "标准输入的编码认不出来，已用替换字符读入（部分字段可能看不准）"


def _list_keys_line(text: str) -> str:
    """`--list-keys` 的那**一行**：`20 条：Ayer1946, Burge1984, …`。

    只调 `parse_entries()`（不跑规范检查、不联网、**不写文件**），所以它便宜到可以在
    编辑**每敲一下**之后重跑一次——界面拿它当「粘贴预览」：先让编辑确认
    「这段文本被识别成几条、键是什么」，再决定要不要体检/并回。

    0 条时给一句人话而不是空行：粘贴时最常见的错就是只粘了半截（没粘到 `@` 开头那行）。
    """
    entries, _findings = parse_entries(text)
    if not entries:
        return "0 条：（没解析出任何条目——请检查粘贴的内容是不是完整的 .bib 文本）"
    keys = [e.key or "（无键）" for e in entries]
    shown = keys[:LIST_KEYS_PREVIEW]
    tail = "…" if len(keys) > len(shown) else ""
    return f"{len(entries)} 条：{', '.join(shown)}{tail}"


# ── ⑤ 并回模式：把交回的整份 .bib 并回产物（与自动通道同一份核心）─────────


def _resolve_out(out: str, returned_path: str, *, merged: bool) -> str:
    """把 `--out` 解析成真正的报告文件路径：**文件路径原样用；已存在的目录则放默认文件名**。

    为什么要有这条（2026-10-17 第十五批，与两个外壳的输入框对齐）：
      Windows 页从 U7 起把它叫「产物目录」，mac 页新增的「报告输出（可选）」也允许填目录；
      而引擎原来的 `--out` 只认文件路径 —— 编辑填一个目录会直接报「写不了报告」。
    目录不存在时**不猜**：按文件路径交给 `open()`，失败照实报错（静默换落点更难查）。
    """
    if not out or not os.path.isdir(out):
        return out
    base = os.path.splitext(os.path.basename((returned_path or "").rstrip("/\\")))[0]
    if not base:
        base = "交回"
    suffix = "合并后回读校验报告.md" if merged else "回读校验报告.md"
    return os.path.join(out, f"{base}_{suffix}")


def _merge_main(a, texts: dict[str, str | None], notes: list[str]) -> int:
    """`--merge-into` 的实现。

    ⚠️ 这里的每一条判据都在 `slcn/aibib.py`（`merge_returned_bib()` → `merge()`）：
    key 必须逐字符相同、回复必须恰好一条可解析、绝不增删条目、未返回的逐字节不动。
    本函数只负责**落盘顺序**（先声明将覆盖什么 → 先备份后覆盖 → 写记录 → 复检）
    与**告知**。落盘的「先备份、后覆盖」也是共用的
    （`aibib.apply_merge_to_file()`），不存在第二份 copy+write。
    """
    target = a.merge_into
    target_text = texts.get("merge_into") or ""
    returned_text = texts.get("returned") or ""

    for nt in notes:
        print(f"（{nt}）")
    # 粘贴模式没有文件路径：这一行要说清「交回的文本从哪儿来」，不能打印一个 None。
    src = "（标准输入/粘贴的文本）" if a.returned_stdin else a.returned
    print(f"并回：{src} → {target}")
    if a.original:
        # 并回模式的 diff 基准是「合并前的目标本身」（回滚副本），不是 --original；
        # 说清楚，别让编辑以为另一个文件参与了比对。
        print(f"（--merge-into 模式下 --original 不作为 diff 基准：基准是合并前的目标本身，"
              f"即回滚副本 {os.path.basename(target)}{aibib.ROLLBACK_SUFFIX}）")

    result = aibib.merge_returned_bib(target_text, returned_text)

    if result.n_merged == 0:
        # ⚠️ 「0 条并入」绝不能长得像成功：不写 bib、不写回滚副本、不写记录、不写报告。
        print(f"未改动：没有任何条目通过安全校验——`{target}` **一个字节都没动**"
              "（不写回滚副本、不写合并记录、不写报告）。原因逐条如下：")
        for r in result.records:
            if r.key:
                print(f"  · `{r.key}`：{r.reason}")
        return 0

    print(f"   并入 {result.n_merged} 条 / 跳过 {result.n_skipped} 条"
          "（逐条原因见合并记录）")
    # 危险动作**先声明**：下一行之后目标文件才会被覆盖，编辑能看见覆盖了谁、备份在哪。
    print(f"⚠ 将覆盖 {target}，原文件备份为 {target}{aibib.ROLLBACK_SUFFIX}")
    rollback = aibib.apply_merge_to_file(target, result)
    print(f"   回滚副本：{rollback}（= 本次合并**之前**的那份）")

    rec_path, rec_note = aibib.write_merge_record(
        target, result, rollback_name=os.path.basename(rollback))
    print(f"   合并记录：{rec_path} —— {rec_note}")

    # 并完立刻做**离线**回读校验（与体检/自动通道同一份报告实现）。
    md, conclusion = bibreport.build_report_and_conclusion(
        result.text, target_text, texts.get("tex"),
        "合并后参考文献核对报告",
        returned_path=target,
        original_path=rollback,
        tex_path=a.tex or "",
        source_note=bibreport.SOURCE_NOTE_MERGE,
    )
    if a.out:
        out_path = _resolve_out(a.out, a.returned, merged=True)
        try:
            with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(md + "\n")
        except OSError as e:
            print(f"❌ 写不了报告 {out_path}：{e.strerror or e}（bib 已并回，报告可重跑）", file=sys.stderr)
            return 2
        print(f"   合并后回读校验报告：{out_path}")
    print(conclusion)
    print("⚠ 这份 bib 已含 AI 结果，请人工核对；原文件见上面的回滚副本。")
    return 1


# ── CLI ───────────────────────────────────────────────────────────────────


_PAGE_LIKE_KEY = re.compile(r"^pp?\d{1,4}$")


def _batch45_notices(rep: "Report", texts: dict[str, str | None]) -> None:
    r"""第 45 批发现性提示（**只加提示，不改任何数据**）。

    这三类现象在 5 期基准里都已被人工确认，但修复方向需要用户拍板或源稿改动：
      ① 页码被写进引用键（90-03：`\cite{Yalcin2007, p985}`）→ 悬空引用其实是笔误；
      ② 正文用编号引用 `[N]` 但没有任何 `\cite` 命令（86-02/87-5/87-7/90-04/90-08，
         P0-1 体例）→ 参考文献区会是空的；工具**不猜映射**；
      ③ 脚注/编号清单被逐条提升为 bib（90-06/90-07，C 类口径）+ 文末清单与脚注
         双通道产生的内容重复条目 → 只报数量，不自动改。
    """
    from slcn import bibcheck
    from slcn.bibparse import LEVEL_INFO

    bib = texts.get("returned") or ""
    tex = texts.get("tex") or ""
    if not bib:
        return
    keys = bibcheck.entry_keys(bib)
    entries = list(bibcheck.split_entries(bib))
    # ① 页码型悬空键
    if tex:
        cited = bibcheck.cited_keys(tex)
        dangling = sorted(cited - keys)
        page_like = [k for k in dangling if _PAGE_LIKE_KEY.match(k)]
        if page_like:
            rep.add("", "cite", LEVEL_INFO,
                    "疑似把页码范围写进引用键：" + "、".join(f"`{k}`" for k in page_like)
                    + "（LaTeX 会把 `\\cite{X, p985}` 解析成两个键；建议作者改成"
                    " `\\cite[985]{X}`。源稿笔误，工具不自动改）")
        # ② 正文有编号引用但没有 cite 命令
        n_cite = int(getattr(rep, "n_cite_cmds", 0) or 0)
        numbered = re.findall(r"\[\s*\d{1,3}\s*(?:[，,–-]\s*\d{1,3}\s*)?\]", tex)
        if n_cite == 0 and numbered:
            rep.add("", "cite", LEVEL_INFO,
                    f"正文有 {len(numbered)} 处编号引用（如 `{numbered[0]}`）但没有任何 "
                    "`\\cite` 命令 → 参考文献区会是空的（P0-1 体例：编号↔文献对照缺失，"
                    "工具**不猜映射**，需编辑按原稿补对照或改成人年制）")
    # ③ note-only 条目（脚注/编号清单提升）与内容重复条目
    note_only = 0
    fingerprints: dict[tuple, int] = {}
    for etype, key, body in entries:
        fields = bibcheck.fields_of(body)
        if not any(fields.get(name) for name in ("author", "editor", "title")):
            if fields.get("note", "").strip():
                note_only += 1
            continue
        author = bibcheck.first_author_surname(fields) if hasattr(bibcheck, "first_author_surname") else ""
        title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "",
                       (fields.get("title") or "").lower())
        year = re.sub(r"\D", "", fields.get("year") or "")
        if title and year:
            fp = (author, year, title)
            fingerprints[fp] = fingerprints.get(fp, 0) + 1
    dup_groups = [n for n in fingerprints.values() if n > 1]
    if note_only:
        rep.add("", "spec", LEVEL_INFO,
                f"{note_only} 条「只有 note、没有结构化 author/title」条目：脚注/编号清单"
                "引注被逐条提升为 bib 时常见（C 类口径，需用户确认是否保留为参考文献）")
    if dup_groups:
        rep.add("", "spec", LEVEL_INFO,
                f"{len(dup_groups)} 组文献内容重复（同作者+年份+题名出现多条，共 "
                f"{sum(dup_groups)} 条）：可能同一文献从「文末清单」与「脚注引注」两条通道"
                "各生成一条；工具不自动合并（C 类口径）")


def main(argv: list[str] | None = None) -> int:
    # 第 86 条：Windows 上把 stdout 切到 UTF-8（否则中文报告经管道会乱码/报错）。
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="checkbib.py",
        description="核对编辑交回来的 .bib（纯离线：不联网、不调用任何模型）；"
                    "加 --merge-into 则把交回的 .bib **并回**产物（同一份合并核心）；"
                    "交回的文本可以来自文件（--returned）或标准输入（--returned-stdin，粘贴模式）",
        epilog=(
            "退出码（体检模式）：0 = 未发现问题；1 = 至少发现一处问题；2 = 用法 / IO 错误\n"
            "退出码（并回模式，加了 --merge-into）：0 = 未改动（一个文件都没写）；\n"
            "                                  1 = 已并入 ≥1 条；2 = 用法 / IO 错误\n"
            "退出码（列表模式，--list-keys）：0 = 打印了那一行；2 = 用法 / IO 错误\n"
            "例（体检）：python3.14 checkbib.py --returned 新.bib "
            "--original out/corpus/2026_10_Bo_Chen/reference.bib "
            "--tex out/corpus/2026_10_Bo_Chen/Bo_Chen.tex\n"
            "例（并回）：python3.14 checkbib.py --returned 新.bib "
            "--merge-into out/corpus/2026_10_Bo_Chen/reference.bib\n"
            "例（粘贴并回，不落中间文件）：pbpaste | python3.14 checkbib.py --returned-stdin "
            "--merge-into out/corpus/2026_10_Bo_Chen/reference.bib\n"
            "例（粘贴预览）：pbpaste | python3.14 checkbib.py --returned-stdin --list-keys"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # ⚠️ `--returned` 与 `--returned-stdin` 是**同一种东西的两种来源**，必须二选一：
    #    同给两个会让人分不清体检的到底是哪一份文本（argparse 会当场报用法错误 → 退出码 2）。
    src = ap.add_mutually_exclusive_group(required=True)
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {_plat.engine_version()} ｜ {_plat.describe()}")
    src.add_argument("--returned", metavar="退回.bib",
                     help="编辑交回来的 .bib（文件来源；与 --returned-stdin 二选一）")
    src.add_argument("--returned-stdin", action="store_true",
                     help="【粘贴模式】交回的 bib 文本从**标准输入**读（不落中间文件，"
                          "与 --returned 二选一）；体检与并回两种模式都支持，"
                          "空输入算用法错误（退出码 2）")
    ap.add_argument("--original", metavar="原件.bib", help="任务书发出前的那份 .bib；给了就做字段级 diff")
    ap.add_argument("--tex", metavar="正文.tex", help="正文 .tex；给了就核对引用键闭合（悬空引用 / 未被引用）")
    ap.add_argument("--out", metavar="报告.md", help="报告写到这个文件（默认打到 stdout）")
    ap.add_argument("--list-keys", action="store_true",
                    help="【列表模式】只打印一行「N 条：key1, key2, …」（前几个键），"
                         "供界面做粘贴预览；只解析、**永不写文件**（同时给了 --out / --merge-into 也不写）")
    ap.add_argument("--merge-into", metavar="产物.bib",
                    help="【并回模式】把交回的**整份** .bib（--returned 或 --returned-stdin）"
                         "并回这个产物 bib（通常是稿件目录里的 reference.bib）。"
                         "与自动通道同一份合并核心：key 不可改、不增删条目、未返回的条目不碰；"
                         "先写回滚副本 `<产物>.ai前` 再覆盖，并追加合并记录；"
                         "并完重跑离线校验、打印结论。退出码见下方说明")
    a = ap.parse_args(argv)

    texts: dict[str, str | None] = {}
    notes: list[str] = []
    # 粘贴模式：先把 stdin 读进来（编码提示排在最前，与「读文件」的提示同一种形状）。
    if a.returned_stdin:
        stdin_text, stdin_note = _read_stdin()
        if not stdin_text.strip():
            # 空输入不是「这份 bib 没问题」，而是「界面没把文本递过来」——必须报用法错误，
            # 否则编辑会看到一个「0 条、未发现问题」的绿灯，以为粘的东西验过了。
            print("❌ --returned-stdin 收到的是空输入：请把交回的 .bib 文本通过标准输入传进来"
                  "（界面粘贴模式下就是粘贴框里那段文字）。", file=sys.stderr)
            return 2
        texts["returned"] = stdin_text
        if stdin_note:
            notes.append(stdin_note)
    else:
        # 文件来源：读法与从前**逐字节相同**（UTF-8 → GB18030 降级、错误信息同格式）。
        try:
            text, note = read_text(a.returned)
        except OSError as e:
            print(f"❌ 读不到退回文件 {a.returned}：{e.strerror or e}", file=sys.stderr)
            return 2
        texts["returned"] = text
        if note:
            notes.append(note)
    for attr, label in (("original", "原件"), ("tex", "正文"), ("merge_into", "并回目标")):
        path = getattr(a, attr, None)
        if not path:
            texts[attr] = None
            continue
        try:
            text, note = read_text(path)
        except OSError as e:
            print(f"❌ 读不到{label} {path}：{e.strerror or e}", file=sys.stderr)
            return 2
        texts[attr] = text
        if note:
            notes.append(note)

    if a.list_keys:
        # ⚠️ 列表模式**永不写文件**：--out / --merge-into 一律不生效（预览有副作用就不叫预览了）。
        #    故意不打印 notes：stdout 必须**恰好一行**，界面直接把这一行当预览行。
        if a.merge_into:
            print("（--list-keys：只预览键，不并回、不写任何文件）", file=sys.stderr)
        print(_list_keys_line(texts.get("returned") or ""))
        return 0

    if a.merge_into:
        return _merge_main(a, texts, notes)

    # 粘贴模式没有路径：传空串让报告头显示 `slcn/bibreport` 的「(stdin/字符串)」，
    # 而不是一个假的文件名（报告是要留档的，不能写一个不存在的路径）。
    try:
        rep = analyze(
            texts["returned"] or "",
            texts["original"],
            texts["tex"],
            returned_path=a.returned or "",
            original_path=a.original or "",
            tex_path=a.tex or "",
        )
    except Exception as e:  # 防御：任何意外都要出报告，不能甩 traceback
        rep = Report(returned=a.returned or "", original=a.original or "", tex=a.tex or "")
        rep.add(
            "",
            "syntax",
            LEVEL_PROBLEM,
            f"核对过程内部出错（{type(e).__name__}: {e}）——报告可能不完整，请把这条和 bib 文件一起回报",
        )
    rep.notes = notes + rep.notes
    _batch45_notices(rep, texts)

    text = render(rep)
    if a.out:
        out_path = _resolve_out(a.out, a.returned, merged=False)
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError as e:
            print(f"❌ 写不了报告 {out_path}：{e.strerror or e}", file=sys.stderr)
            return 2
        print(f"结论：{rep.n_problem} 处问题 / {rep.n_info} 处提示 → {out_path}")
    else:
        print(text)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
