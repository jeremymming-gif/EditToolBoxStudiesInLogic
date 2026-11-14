#!/usr/bin/env python3
r"""B-375 round2：目录自动单页适配（slcn.tocdelivery.fit_single_page）纯文本回归。

真实 xelatex 由验收方在合成夹具上跑（CI/Linux 无 TeX）；这里用假编译按
「总压缩量 >= 该份需要值 ⇒ 1 页」模拟，验证搜索/下限/失败/回滚/明细字段与重复运行不累计。
本文件是独立脚本（同 Engine/tests 其余套件风格），失败退出码非 0。
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "vendor"))

from slcn import tocdelivery as TD  # noqa: E402

BS = chr(92)
VD = BS + "vdistance"
ZH = "Chinese Content"
EN = "contents"

_passed = 0
_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}  {detail}")


# ── 夹具 ──────────────────────────────────────────────────────────────────
def write_tex(path: Path, base: str = "4.8ex", gaps: int = 8, macro: bool = True) -> None:
    """最小 ctexart：\vdistance 宏 + A..A+gaps 之间每个间隔一行 \vdistance。"""
    body: list[str] = []
    for i in range(gaps + 1):
        body.append(chr(65 + i))
        if i < gaps:
            body.append(VD)
    head = [BS + "documentclass{ctexart}"]
    if macro:
        head.append(BS + "newcommand{" + VD + "}{" + BS + BS + "[" + base + "]}%调整行间隔")
    text = ("\n".join(head) + "\n" + BS + "begin{document}\n"
            + "\n\n".join(body) + "\n" + BS + "end{document}\n")
    path.write_text(text, encoding="utf-8")


def write_log(folder: Path, stem: str, pages: int) -> None:
    (folder / f"{stem}.log").write_text(
        "This is XeTeX, Version 3.14\n"
        f"Output written on {stem}.pdf ({pages} page{'s' if pages != 1 else ''}).\n",
        encoding="utf-8")


def mk_dir(zh_base: str = "4.8ex", en_base: str = "2.5mm",
           zh_gaps: int = 8, en_gaps: int = 8,
           zh_pages: int = 2, en_pages: int = 2) -> Path:
    d = Path(tempfile.mkdtemp(prefix="slcn_fit_b375_"))
    write_tex(d / f"{ZH}.tex", zh_base, zh_gaps)
    write_tex(d / f"{EN}.tex", en_base, en_gaps)
    write_log(d, ZH, zh_pages)
    write_log(d, EN, en_pages)
    return d


def tex_text(d: Path, stem: str) -> str:
    return (d / f"{stem}.tex").read_text(encoding="utf-8")


def snapshot(folder: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(folder):
        for name in filenames:
            full = Path(dirpath) / name
            out[str(full.relative_to(folder))] = full.stat().st_size, full.read_bytes().hex()
    return dict(sorted(out.items()))


_DIM_RE = re.compile(re.escape(BS + "dimexpr") + r" ([^ ]+) - ([\d.]+)pt" + re.escape(BS + "relax"))


class FakeCompile:
    r"""假编译：从 tex 抓 \dimexpr 的每处压缩量，总压缩 >= need 时写 1 页 log，否则 2 页。"""

    def __init__(self, need_map: dict[str, float], gaps: int = 8,
                 fail_stems: tuple[str, ...] = ()) -> None:
        self.need = dict(need_map)
        self.gaps = gaps
        self.fail_stems = set(fail_stems)
        self.calls: list[str] = []

    def _one(self, folder: Path, stem: str) -> tuple[bool, str]:
        self.calls.append(stem)
        if stem in self.fail_stems:
            return False, "模拟 xelatex 编译失败"
        text = (folder / f"{stem}.tex").read_text(encoding="utf-8")
        m = _DIM_RE.search(text)
        total = float(m.group(2)) * self.gaps if m else 0.0
        need = self.need.get(stem)
        pages = 1 if (need is not None and total + 1e-9 >= need) else 2
        write_log(folder, stem, pages)
        return True, str(folder / f"{stem}.pdf")

    def __call__(self, folder: str, stems):
        """fit 传单 stem（返回二元组）；deliver 的 compile_all 传 ((key, stem),)（返回 dict）。"""
        if isinstance(stems, str):
            return self._one(Path(folder), stems)
        out: dict[str, tuple[bool, str]] = {}
        for key, stem in stems:
            out[key] = self._one(Path(folder), stem)
        return out


# ── 用例 ──────────────────────────────────────────────────────────────────
def case_en_success(root: Path) -> None:
    print("\n── (a) 英文目录超长：二分找最小压缩量后 1 页，明细完整 ──")
    d = mk_dir(en_base="4mm", en_gaps=8, zh_pages=1, en_pages=2)
    zh_before = tex_text(d, ZH)
    fake = FakeCompile({EN: 126.0}, gaps=8)
    logs: list[str] = []
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)),
                                          fake, log=logs.append)
    detail = fit.get(EN) or {}
    total = detail.get("totalShrinkPt")
    check("(a1) 成功无错误", err == "", err[:160])
    check("(a2) 页数：中 1 / 英 1", counts.get("zh") == 1 and counts.get("en") == 1,
          str(counts))
    check("(a3) 明细字段固定 9 项", set(detail) == {
        "pagesBefore", "pagesAfter", "gaps", "oldMacro", "newMacro", "applied",
        "attempts", "totalShrinkPt", "perGapShrinkPt"}, str(sorted(detail)))
    check("(a4) 明细 before/after/gaps/applied",
          detail.get("pagesBefore") == 2 and detail.get("pagesAfter") == 1
          and detail.get("gaps") == 8 and detail.get("applied") is True, str(detail)[:200])
    check("(a5) attempts == 编译调用次数且 2..8",
          2 <= int(detail.get("attempts") or 0) == len(fake.calls) <= 8,
          f"attempts={detail.get('attempts')} calls={len(fake.calls)}")
    check("(a6) 最小通过量落在 [126, 129.5]pt",
          total is not None and 126.0 <= float(total) <= 129.5, str(total))
    per = float(detail.get("perGapShrinkPt") or 0)
    check("(a7) totalShrinkPt == perGap*8",
          total is not None and abs(float(total) - round(per * 8, 2)) < 1e-6,
          f"total={total} per={per}")
    text = tex_text(d, EN)
    check("(a8) 最终宏单一 dimexpr、只压一次", text.count(BS + "dimexpr") == 1
          and (BS + "dimexpr 4mm - ") in text, text[:400])
    check("(a9) 最终宏就是明细的 newMacro",
          str(detail.get("newMacro") or "") in text, str(detail.get("newMacro")))
    check("(a10) 1 页的中文目录不重编不改宏", tex_text(d, ZH) == zh_before)
    check("(a11) 日志有页数变化与最终复核", any("2 页 → 1 页" in s for s in logs)
          and any("最终复核" in s for s in logs), "\n".join(logs)[:300])
    shutil.rmtree(d, ignore_errors=True)


def case_zh_success(root: Path) -> None:
    print("\n── (b) 中文目录超长：base 4.8ex、最小压缩量 1 页 ──")
    d = mk_dir(zh_base="4.8ex", zh_gaps=8, zh_pages=2, en_pages=1)
    fake = FakeCompile({ZH: 80.0}, gaps=8)
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)), fake)
    detail = fit.get(ZH) or {}
    total = float(detail.get("totalShrinkPt") or -1)
    check("(b1) 成功无错误、页数 1/1", err == "" and counts.get("zh") == 1
          and counts.get("en") == 1, f"err={err[:120]} counts={counts}")
    check("(b2) 压缩量落在 [80, 83]pt", 80.0 <= total <= 83.0, str(total))
    check("(b3) 宏基于 4.8ex 且只一层 dimexpr", (BS + "dimexpr 4.8ex - ") in tex_text(d, ZH)
          and tex_text(d, ZH).count(BS + "dimexpr") == 1)
    shutil.rmtree(d, ignore_errors=True)


def case_max_still_two_pages(root: Path) -> None:
    print("\n── (c) 压到下限仍 2 页：明确失败、恢复原文、只试上限一次 ──")
    d = mk_dir(zh_pages=2, en_pages=1)
    before = tex_text(d, ZH)
    fake = FakeCompile({ZH: 999.0}, gaps=8)
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)), fake)
    check("(c1) 失败原因含下限/仍 2 页/自动适配失败/人工",
          all(s in err for s in ("压缩到自动适配下限", "仍是 2 页",
                                 "自动单页适配失败", "人工")), err[:240])
    check("(c2) 明细为空、页数不动", fit == {} and counts.get("zh") == 2, str(fit))
    check("(c3) tex 恢复原文逐字节", tex_text(d, ZH) == before)
    check("(c4) 只在失败尝试编译 1 次", len(fake.calls) == 1, str(fake.calls))
    shutil.rmtree(d, ignore_errors=True)


def case_no_macro(root: Path) -> None:
    print("\n── (d) 没有 vdistance 宏：明确失败、不编译 ──")
    d = mk_dir(zh_pages=2, en_pages=1)
    write_tex(d / f"{ZH}.tex", macro=False)
    before = tex_text(d, ZH)
    fake = FakeCompile({ZH: 80.0}, gaps=8)
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)), fake)
    check("(d1) 原因指明没有可改写的宏", "没有可改写的" in err and VD in err, err[:200])
    check("(d2) 未编译、原文不变", fake.calls == [] and tex_text(d, ZH) == before)
    shutil.rmtree(d, ignore_errors=True)


def case_unparsable(root: Path) -> None:
    print("\n── (e) 宏值无法解析（未知单位）：明确失败 ──")
    d = mk_dir(zh_base="1.2" + BS + "baselineskip", zh_pages=2, en_pages=1)
    before = tex_text(d, ZH)
    fake = FakeCompile({ZH: 80.0}, gaps=8)
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)), fake)
    check("(e1) 原因含无法解析", "无法解析" in err, err[:200])
    check("(e2) 未编译、原文不变", fake.calls == [] and tex_text(d, ZH) == before)
    shutil.rmtree(d, ignore_errors=True)


def case_below_floor(root: Path) -> None:
    print("\n── (f) 行距已低于下限：明确失败提示人工 ──")
    d = mk_dir(zh_base="-3.5mm", zh_pages=2, en_pages=1)
    fake = FakeCompile({ZH: 80.0}, gaps=8)
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)), fake)
    check("(f1) 原因含已越过自动适配下限（当前更紧）/人工",
          "已越过自动适配下限" in err and "当前更紧" in err and "人工" in err, err[:200])
    check("(f2) 未编译", fake.calls == [])
    shutil.rmtree(d, ignore_errors=True)


def case_below_floor_r3_en(root: Path) -> None:
    print("\n── (f2) R3 英文形态：继承 -3.05mm 已越过 -6pt 下限，拒绝且不编译 ──")
    d = mk_dir(en_base="-3.05mm", zh_pages=1, en_pages=2)
    before = tex_text(d, EN)
    fake = FakeCompile({EN: 80.0}, gaps=8)
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)), fake)
    check("(f2a) 原因方向正确：已越过/当前更紧/每处 -8.68pt/-6.0pt",
          "已越过自动适配下限" in err and "当前更紧" in err
          and "-8.68pt" in err and "-6.0pt" in err, err[:220])
    check("(f2b) 不编译、英文仍 2 页、原文逐字节不变、明细空",
          fake.calls == [] and counts.get("en") == 2 and tex_text(d, EN) == before
          and fit == {}, f"calls={fake.calls} counts={counts} fit={fit}")
    shutil.rmtree(d, ignore_errors=True)


def case_rerun_no_accumulate(root: Path) -> None:
    print("\n── (g) 已压过的目录重跑：不累计、仍以原 base 为基准 ──")
    d = mk_dir(zh_base=BS + "dimexpr 4.8ex - 5.00pt" + BS + "relax",
               zh_pages=2, en_pages=1)
    fake = FakeCompile({ZH: 80.0}, gaps=8)
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)), fake)
    text = tex_text(d, ZH)
    check("(g1) 成功", err == "" and counts.get("zh") == 1, err[:160])
    check("(g2) 仍只一层 dimexpr、base 未变", text.count(BS + "dimexpr") == 1
          and (BS + "dimexpr 4.8ex - ") in text, text[:400])
    check("(g3) 旧压缩量 5.00 不残留", "5.00" not in (fit.get(ZH) or {}).get("newMacro", ""))
    parsed = TD._parse_vdistance_base(TD.read_vdistance(str(d / f"{ZH}.tex")) or "")
    check("(g4) 重跑后解析 base 仍 4.8ex", parsed is not None and parsed[0] == "4.8ex",
          str(parsed))
    check("(g5) 压缩量仍够 1 页", float((fit.get(ZH) or {}).get("totalShrinkPt") or 0) >= 80.0)
    shutil.rmtree(d, ignore_errors=True)


def case_compile_fail_restore(root: Path) -> None:
    print("\n── (h) 适配中编译失败：恢复原文并明确失败 ──")
    d = mk_dir(zh_pages=2, en_pages=1)
    before = tex_text(d, ZH)
    fake = FakeCompile({ZH: 80.0}, gaps=8, fail_stems=(ZH,))
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)), fake)
    check("(h1) 原因含重新编译/失败", "重新编译" in err and "失败" in err, err[:200])
    check("(h2) tex 恢复原文、明细空", tex_text(d, ZH) == before and fit == {})
    check("(h3) 编译失败只尝试 1 次", len(fake.calls) == 1, str(fake.calls))
    shutil.rmtree(d, ignore_errors=True)


def case_multi_stem(root: Path) -> None:
    print("\n── (i) 中英分别判断：中文可压、英文不可压 → 整体失败并恢复英文 ──")
    d = mk_dir(zh_pages=2, en_pages=2, en_base="4mm")
    en_before = tex_text(d, EN)
    fake = FakeCompile({ZH: 80.0, EN: 200.0}, gaps=8)
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)), fake)
    check("(i1) 整体失败且原因指明英文", err != "" and "仍是 2 页" in err, err[:200])
    check("(i2) 明细只有中文", list(fit) == [ZH], str(list(fit)))
    check("(i3) 中文已压缩、英文恢复原文", (BS + "dimexpr 4.8ex - ") in tex_text(d, ZH)
          and tex_text(d, EN) == en_before)
    check("(i4) 页数：中 1 / 英 2", counts.get("zh") == 1 and counts.get("en") == 2,
          str(counts))
    shutil.rmtree(d, ignore_errors=True)


def case_all_single_page(root: Path) -> None:
    print("\n── (j) 两份都是 1 页：不重编不改宏 ──")
    d = mk_dir(zh_pages=1, en_pages=1)
    zh_before, en_before = tex_text(d, ZH), tex_text(d, EN)
    fake = FakeCompile({}, gaps=8)
    logs: list[str] = []
    counts, err, fit = TD.fit_single_page(str(d), (("zh", ZH), ("en", EN)),
                                          fake, log=logs.append)
    check("(j1) 成功、无明细、无编译", err == "" and fit == {} and fake.calls == [])
    check("(j2) 两份 tex 都不变", tex_text(d, ZH) == zh_before
          and tex_text(d, EN) == en_before)
    check("(j3) 日志说明无需适配", any("无需单页适配" in s for s in logs), "\n".join(logs))
    shutil.rmtree(d, ignore_errors=True)


def case_deliver_rollback(root: Path) -> None:
    print("\n── (k) deliver 单页适配失败：rc2 语义、000 快照回滚、无备份残留 ──")
    root = root / "issue"
    work = root / ".toolbox-tmp"
    work.mkdir(parents=True)
    write_tex(work / "Chinese Content_20260101_000000.tex", "4.8ex", 8)
    write_tex(work / "contents_20260101_000000.tex", "4mm", 8)
    toc_dir = root / "000 Table of Contents"
    toc_dir.mkdir()
    old = {
        "Chinese Content.tex": "OLD ZH TEX\n",
        "contents.tex": "OLD EN TEX\n",
        "Chinese Content.pdf": "OLD ZH PDF\n",
        "contents.pdf": "OLD EN PDF\n",
    }
    for name, text in old.items():
        (toc_dir / name).write_text(text, encoding="utf-8")
    before = snapshot(toc_dir)
    # 假编译永远写 2 页 log：让 fit 走到「压到下限仍 2 页」的失败路径。
    fake = FakeCompile({}, gaps=8)

    def always_two_pages(folder, stems):
        out = {}
        for key, stem in stems:
            write_log(Path(folder), stem, 2)
            out[key] = (True, str(Path(folder) / f"{stem}.pdf"))
        return out

    fake = always_two_pages  # type: ignore[assignment]
    original_font_check = TD.check_windows_fonts
    TD.check_windows_fonts = lambda: (True, "")
    try:
        res = TD.deliver(str(root), do_compile=True, compile_func=fake)
    finally:
        TD.check_windows_fonts = original_font_check
    after = snapshot(toc_dir)
    leaks = [str(p) for p in root.rglob("toc-backup-*")]
    check("(k1) 失败原因含自动单页适配失败", "自动单页适配失败" in res.error, res.error[:240])
    check("(k2) 000 文件集合与内容与本轮开始前完全一致", after == before,
          f"before={sorted(before)} after={sorted(after)}")
    check("(k3) 无 toc-backup 残留", leaks == [], str(leaks))
    check("(k4) 失败时 single_page_fit 为空", res.single_page_fit == {}, str(res.single_page_fit))
    shutil.rmtree(root, ignore_errors=True)


def case_result_json(root: Path) -> None:
    print("\n── (l) 结果 JSON：singlePageFit 仅 compiled 时出现 ──")
    spec = importlib.util.spec_from_file_location("toc_cli_b375", ENGINE / "toc.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    orig_list = mod._toc.list_articles
    orig_manual = mod._toc.manual_edits
    mod._toc.list_articles = lambda report: [("a", "t"), ("b", "t")]
    mod._toc.manual_edits = lambda report, settings: []
    settings = mod._toc.TocSettings()
    delivery = types.SimpleNamespace(
        toc_dir="/tmp/x", placed={"zh": "/tmp/x/Chinese Content.tex",
                                  "en": "/tmp/x/contents.tex"},
        pdfs={"zh": "/tmp/x/Chinese Content.pdf", "en": "/tmp/x/contents.pdf"},
        pages={"zh": 1, "en": 1}, compiled=False,
        single_page_fit={"contents": {"pagesBefore": 2, "pagesAfter": 1}})
    try:
        payload = mod._result_json("r.txt", "/tmp", settings, delivery, [],
                                   outputs=[], source="内置模板", note="n")
        check("(l1) 未编译：无 singlePageFit、articles 2/2、compiled False",
              "singlePageFit" not in payload and payload["articles"] == {"total": 2, "inToc": 2}
              and payload["compiled"] is False, str(payload.get("articles")))
        delivery.compiled = True
        payload2 = mod._result_json("r.txt", "/tmp", settings, delivery, [],
                                    outputs=[], source="内置模板", note="n")
        check("(l2) 编译成功：singlePageFit 等于交付明细",
              payload2.get("singlePageFit") == {"contents": {"pagesBefore": 2,
                                                             "pagesAfter": 1}},
              str(payload2.get("singlePageFit")))
        no_delivery = mod._result_json("r.txt", "/tmp", settings, None, [],
                                       outputs=[], source="内置模板", note="n")
        check("(l3) 纯 tex（delivery None）：无 singlePageFit",
              "singlePageFit" not in no_delivery and no_delivery["compiled"] is False,
              str(no_delivery.get("compiled")))
    finally:
        mod._toc.list_articles = orig_list
        mod._toc.manual_edits = orig_manual


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="slcn_fit_b375_root_"))
    try:
        case_en_success(root)
        case_zh_success(root)
        case_max_still_two_pages(root)
        case_no_macro(root)
        case_unparsable(root)
        case_below_floor(root)
        case_below_floor_r3_en(root)
        case_rerun_no_accumulate(root)
        case_compile_fail_restore(root)
        case_multi_stem(root)
        case_all_single_page(root)
        case_deliver_rollback(root)
        case_result_json(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if _fails:
        print(f"\n失败 {len(_fails)} 项：{_fails}")
        sys.exit(1)
    print(f"\n全部 {_passed} 项断言通过")


if __name__ == "__main__":
    main()