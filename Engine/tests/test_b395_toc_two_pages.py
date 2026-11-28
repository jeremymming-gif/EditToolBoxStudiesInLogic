#!/usr/bin/env python3
r"""B-395：目录允许 1–2 页（可勾选生成单页目录）引擎回归。

默认（singlePageToc 缺失/true → max_pages=1）逐字节沿用 B-375 压缩判据；
false → max_pages=2，跳过行距压缩、只校验 ≤2 页；≥3 页沿用既有失败风格并
由 deliver 的 000 快照回滚。独立脚本（同 Engine/tests 其余套件风格），失败非 0。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "vendor"))

from slcn import toc as _toc          # noqa: E402
from slcn import tocdelivery as TD    # noqa: E402

BS = chr(92)
VD = BS + "vdistance"
ZH = "Chinese Content"
EN = "contents"
STEMS = (("zh", ZH), ("en", EN))

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
def write_tex(path: Path, base: str = "4.8ex", gaps: int = 8) -> None:
    body: list[str] = []
    for i in range(gaps + 1):
        body.append(chr(65 + i))
        if i < gaps:
            body.append(VD)
    text = (BS + "documentclass{ctexart}\n"
            + BS + "newcommand{" + VD + "}{" + BS + BS + "[" + base + "]}\n"
            + BS + "begin{document}\n" + "\n\n".join(body) + "\n"
            + BS + "end{document}\n")
    path.write_text(text, encoding="utf-8")


def write_log(folder: Path, stem: str, pages: int) -> None:
    (folder / f"{stem}.log").write_text(
        "This is XeTeX, Version 3.14\n"
        f"Output written on {stem}.pdf ({pages} page{'s' if pages != 1 else ''}).\n",
        encoding="utf-8")


def mk_dir(zh_pages: int = 2, en_pages: int = 2) -> Path:
    d = Path(tempfile.mkdtemp(prefix="slcn_b395_"))
    write_tex(d / f"{ZH}.tex")
    write_tex(d / f"{EN}.tex", "4mm")
    write_log(d, ZH, zh_pages)
    write_log(d, EN, en_pages)
    return d


def tex_text(d: Path, stem: str) -> str:
    return (d / f"{stem}.tex").read_text(encoding="utf-8")


def snapshot(folder: Path) -> dict:
    out = {}
    for dirpath, _dirs, filenames in os.walk(folder):
        for name in filenames:
            full = Path(dirpath) / name
            out[str(full.relative_to(folder))] = full.read_bytes().hex()
    return dict(sorted(out.items()))


class NoCompile:
    """两页模式不应触发任何编译；被误调用就会记下来并报错。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, folder, stems):
        self.calls.append(str(stems))
        raise AssertionError("跳过压缩的 1–2 页模式不应调用编译函数")


class AlwaysTwoPages:
    """默认单页压缩路径用：每次编译都写 2 页 log（模拟压到下限仍失败），不改 tex。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, folder, stems):
        stem = stems if isinstance(stems, str) else stems[-1][1]
        self.calls.append(stem)
        write_log(Path(folder), stem, 2)
        return True, str(Path(folder) / f"{stem}.pdf")


class FakePages:
    """deliver 用的假编译：直接按给定页数写 log，不模拟压缩。"""

    def __init__(self, pages: dict[str, int]) -> None:
        self.pages = dict(pages)
        self.calls: list[str] = []

    def __call__(self, folder, stems):
        out: dict[str, tuple[bool, str]] = {}
        for key, stem in stems:
            self.calls.append(stem)
            write_log(Path(folder), stem, self.pages.get(key, 1))
            out[key] = (True, str(Path(folder) / f"{stem}.pdf"))
        return out


def make_issue() -> tuple[Path, Path, dict[str, str]]:
    root = Path(tempfile.mkdtemp(prefix="slcn_b395_issue_"))
    work = root / ".toolbox-tmp"
    work.mkdir()
    write_tex(work / "Chinese Content_20260101_000000.tex")
    write_tex(work / "contents_20260101_000000.tex", "4mm")
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
    return root, toc_dir, old


def deliver_with(root: Path, fake, settings: dict | None = None):
    if settings is not None:
        (root / _toc.TocSettings.fileName).write_text(
            json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    original_font = TD.check_windows_fonts
    TD.check_windows_fonts = lambda: (True, "")
    try:
        return TD.deliver(str(root), do_compile=True, compile_func=fake)
    finally:
        TD.check_windows_fonts = original_font


# ── 用例 ──────────────────────────────────────────────────────────────────
def case_default_unchanged() -> None:
    print("\n── (a) 默认 max_pages=1：旧压缩判据逐字节不变 ──")
    d = mk_dir(zh_pages=2, en_pages=1)
    before_zh = tex_text(d, ZH)
    fake = AlwaysTwoPages()
    counts, err, fit = TD.fit_single_page(str(d), STEMS, fake)
    check("(a1) 默认仍报单页适配失败", "压缩到自动适配下限" in err
          and "自动单页适配失败" in err, err[:200])
    check("(a2) 尝试过压缩、但失败后还原宏，页数仍 2 页",
          len(fake.calls) >= 1 and tex_text(d, ZH) == before_zh
          and counts.get("zh") == 2 and fit == {}, str(counts))
    shutil.rmtree(d, ignore_errors=True)


def case_two_pages_success() -> None:
    print("\n── (b) max_pages=2：中英各 2 页成功，跳过压缩 ──")
    d = mk_dir(zh_pages=2, en_pages=2)
    before_zh, before_en = tex_text(d, ZH), tex_text(d, EN)
    no = NoCompile()
    counts, err, fit = TD.fit_single_page(str(d), STEMS, no, max_pages=2)
    check("(b1) 成功、无错误、无压缩明细", err == "" and fit == {}, err[:160])
    check("(b2) 页数中英各 2", counts.get("zh") == 2 and counts.get("en") == 2,
          str(counts))
    check("(b3) 两份 tex 逐字节不变、未触发编译",
          tex_text(d, ZH) == before_zh and tex_text(d, EN) == before_en
          and no.calls == [], str(no.calls))
    shutil.rmtree(d, ignore_errors=True)


def case_two_pages_one_page() -> None:
    print("\n── (b2) max_pages=2：已 1 页的目录同样跳过压缩 ──")
    d = mk_dir(zh_pages=1, en_pages=1)
    no = NoCompile()
    counts, err, fit = TD.fit_single_page(str(d), STEMS, no, max_pages=2)
    check("(b2a) 成功、页数 1/1、无明细", err == "" and fit == {}
          and counts.get("zh") == 1 and counts.get("en") == 1, str(counts))
    shutil.rmtree(d, ignore_errors=True)


def case_three_pages_rejected() -> None:
    print("\n── (c) max_pages=2：任一份 ≥3 页报错、不编译不改宏 ──")
    d = mk_dir(zh_pages=3, en_pages=2)
    before = tex_text(d, ZH)
    no = NoCompile()
    counts, err, fit = TD.fit_single_page(str(d), STEMS, no, max_pages=2)
    check("(c1) 错误指明 3 页与 2 页上限", "3 页" in err and "2 页" in err
          and "请人工调整" in err, err[:200])
    check("(c2) 页数仍 3、明细空、原文不变、未编译",
          counts.get("zh") == 3 and fit == {} and tex_text(d, ZH) == before
          and no.calls == [], f"counts={counts} calls={no.calls}")
    shutil.rmtree(d, ignore_errors=True)


def case_deliver_default() -> None:
    print("\n── (d) deliver 缺省字段：max_pages=1，91 期样式仍失败回滚 ──")
    root, toc_dir, _old = make_issue()
    try:
        res = deliver_with(root, FakePages({"zh": 2, "en": 1}))
        check("(d1) 默认失败、沿用单页适配失败文案",
              "自动单页适配失败" in res.error, res.error[:200])
        check("(d2) 结果字段默认 max_pages=1/未跳过压缩",
              res.max_pages == 1 and res.compression_skipped is False,
              f"max_pages={res.max_pages} skipped={res.compression_skipped}")
        check("(d3) 无备份残留", list(root.rglob("toc-backup-*")) == [])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_deliver_false_two_pages() -> None:
    print("\n── (e) deliver singlePageToc=false：2 页交付成功、000 落新稿 ──")
    root, toc_dir, old = make_issue()
    try:
        fake = FakePages({"zh": 2, "en": 2})
        res = deliver_with(root, fake, {"singlePageToc": False})
        check("(e1) 成功、无错误、编译页数 2/2",
              res.error == "" and res.pages == {"zh": 2, "en": 2},
              f"error={res.error[:120]} pages={res.pages}")
        check("(e2) 结果字段 max_pages=2/跳过压缩",
              res.max_pages == 2 and res.compression_skipped is True,
              f"max_pages={res.max_pages} skipped={res.compression_skipped}")
        check("(e3) 000 里是新 tex（不是旧稿）、无备份残留",
              "OLD ZH TEX" not in (toc_dir / f"{ZH}.tex").read_text(encoding="utf-8")
              and list(root.rglob("toc-backup-*")) == [])
        check("(e4) 只初始编译一轮、无压缩重编", len(fake.calls) == 2,
              str(fake.calls))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_deliver_false_three_pages_rollback() -> None:
    print("\n── (f) deliver singlePageToc=false：3 页报错+快照回滚 ──")
    root, toc_dir, old = make_issue()
    before = snapshot(toc_dir)
    try:
        res = deliver_with(root, FakePages({"zh": 3, "en": 2}),
                           {"singlePageToc": False})
        check("(f1) 失败原因含 3 页与 2 页上限、无单页适配失败误报",
              "3 页" in res.error and "2 页" in res.error
              and "单页适配失败" not in res.error, res.error[:200])
        check("(f2) 000 逐字节回滚、无备份残留",
              snapshot(toc_dir) == before and list(root.rglob("toc-backup-*")) == [],
              f"before={sorted(before)} after={sorted(snapshot(toc_dir))}")
        check("(f3) 结果字段仍标 max_pages=2/跳过压缩",
              res.max_pages == 2 and res.compression_skipped is True,
              f"max_pages={res.max_pages} skipped={res.compression_skipped}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_toc_settings_roundtrip() -> None:
    print("\n── (g) TocSettings 字段往返：缺省 true、false verbatim ──")
    root = Path(tempfile.mkdtemp(prefix="slcn_b395_settings_"))
    try:
        s = _toc.TocSettings(singlePageToc=False)
        s.save(str(root))
        loaded = _toc.TocSettings.load(str(root))
        check("(g1) false 往返保留", loaded.singlePageToc is False,
              f"loaded={loaded.singlePageToc}")
        (root / _toc.TocSettings.fileName).write_text(
            json.dumps({"columnKind": ""}), encoding="utf-8")
        check("(g2) 旧文件缺字段默认 true",
              _toc.TocSettings.load(str(root)).singlePageToc is True)
        data = json.loads((root / _toc.TocSettings.fileName).read_text(encoding="utf-8"))
        data["singlePageToc"] = True
        (root / _toc.TocSettings.fileName).write_text(
            json.dumps(data), encoding="utf-8")
        check("(g3) true 往返保留",
              _toc.TocSettings.load(str(root)).singlePageToc is True)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_result_json() -> None:
    print("\n── (h) 结果 JSON：maxPages/compressionSkipped 仅编译时 additive ──")
    spec = importlib.util.spec_from_file_location("toc_cli_b395", ENGINE / "toc.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    orig_list, orig_manual = mod._toc.list_articles, mod._toc.manual_edits
    mod._toc.list_articles = lambda report: [("a", "t")]
    mod._toc.manual_edits = lambda report, settings: []
    settings = mod._toc.TocSettings()
    delivery = types.SimpleNamespace(
        toc_dir="/tmp/x", placed={"zh": "/tmp/x/Chinese Content.tex",
                                  "en": "/tmp/x/contents.tex"},
        pdfs={"zh": "/tmp/x/Chinese Content.pdf", "en": "/tmp/x/contents.pdf"},
        pages={"zh": 2, "en": 2}, compiled=True, single_page_fit={},
        max_pages=2, compression_skipped=True)
    try:
        p = mod._result_json("r.txt", "/tmp", settings, delivery, [],
                             outputs=[], source="内置模板", note="n")
        check("(h1) 两页模式字段正确",
              p.get("maxPages") == 2 and p.get("compressionSkipped") is True,
              f"maxPages={p.get('maxPages')} skipped={p.get('compressionSkipped')}")
        check("(h2) 旧字段仍在（articles/compiled/pages/singlePageFit）",
              p["articles"] == {"total": 1, "inToc": 1}
              and p["compiled"] is True and p["pages"] == {"zh": 2, "en": 2}
              and p["singlePageFit"] == {}, str(p)[:240])
        delivery.max_pages, delivery.compression_skipped = 1, False
        p2 = mod._result_json("r.txt", "/tmp", settings, delivery, [],
                              outputs=[], source="内置模板", note="n")
        check("(h3) 默认模式字段为 1/false",
              p2.get("maxPages") == 1 and p2.get("compressionSkipped") is False,
              str(p2.get("maxPages")))
        delivery.compiled = False
        p3 = mod._result_json("r.txt", "/tmp", settings, delivery, [],
                              outputs=[], source="内置模板", note="n")
        check("(h4) 未编译不出现三个 B-375/395 明细字段",
              all(k not in p3 for k in ("singlePageFit", "maxPages",
                                        "compressionSkipped")),
              str(sorted(p3))[:240])
        p4 = mod._result_json("r.txt", "/tmp", settings, None, [],
                              outputs=[], source="内置模板", note="n")
        check("(h5) 纯 tex（delivery None）也不出现",
              all(k not in p4 for k in ("maxPages", "compressionSkipped")),
              str(sorted(p4))[:240])
    finally:
        mod._toc.list_articles = orig_list
        mod._toc.manual_edits = orig_manual


def main() -> None:
    case_default_unchanged()
    case_two_pages_success()
    case_two_pages_one_page()
    case_three_pages_rejected()
    case_deliver_default()
    case_deliver_false_two_pages()
    case_deliver_false_three_pages_rollback()
    case_toc_settings_roundtrip()
    case_result_json()
    if _fails:
        print(f"\n失败 {len(_fails)} 项：{_fails}")
        sys.exit(1)
    print(f"\n全部 {_passed} 项断言通过")


if __name__ == "__main__":
    main()
