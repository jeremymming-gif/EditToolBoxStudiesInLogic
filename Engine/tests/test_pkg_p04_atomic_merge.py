#!/usr/bin/env python3
"""P04：合并文件「安全写出 + 完整反馈」的引擎侧回归（R01–R06）。

这套件钉的是**可观测行为**，不看实现细节字符串：

  (R01) 原子写出 `Merger.write(path)`：先写同目录 `<target>.tmp-merge`，
        flush + fsync + close 后才 `os.replace`。写一半失败 / replace 失败时，
        旧目标**逐字节不变**（SHA 不变），新目标**不存在**，且不留伪成品；
        成功替换后目标可被 PdfReader 读、页数等于合并页数，临时文件清理干净。
        CLI 失败 rc=2、成功 rc=0 且末行仍是「完成。合并文件：<绝对路径>（共 N 页）」。
  (R02) 输出路径 = 本次读入的某个输入 PDF 时**零写入拒绝**（中文/空格、相对路径、
        符号链接、损坏输入、--editors-page/--back-cover 检测候选都不得绕过），
        抛可读中文原因（含文件名）；CLI 上 rc=2 且 stderr 可见。
  (R03) 顺序（扉页 → 编委页 → 000 目录 → 各篇 → 封底）、页数 = 各部件实际页数之和、
        顶层书签与 nest 语义不变；**不按页数去重**（同页数不同内容都要保留）。
  (R04) 缺件/损坏继续合并，missing 与 warnings 必须指明实际文件名/原因，不静默。
  (R06) 不扫描/不改写历史 `合并*.pdf`；新产物是标准 PDF，旧版 75824a3 的
        `PdfReader`/`read_outline` 读取语义（本文件用同构实现模拟）可读、页数一致。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from unittest import mock

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from pypdf import PdfReader, PdfWriter  # noqa: E402
from slcn import pdfmerge as M  # noqa: E402

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


# ── 小工具 ──────────────────────────────────────────────────────────────

def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def mk_pdf(path, pages: int, bookmarks=None, width: int = 200) -> str:
    """造一个 N 页的 PDF；bookmarks = [(标题, 页序)]；width 用来区分同页数不同内容。"""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=300)
    for title, idx in (bookmarks or []):
        w.add_outline_item(title, idx)
    with open(path, "wb") as fh:
        w.write(fh)
    return str(path)


def flat_outline(path) -> list[tuple[str, int]]:
    """完整大纲（含层级，用缩进表示）为 [(缩进标题, 页码)]。"""
    reader = PdfReader(str(path))
    out: list[tuple[str, int]] = []

    def walk(items, depth=0):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
                continue
            out.append(("  " * depth + (it.title or ""),
                        reader.get_destination_page_number(it)))

    with contextlib.redirect_stderr(io.StringIO()):  # pypdf 对老 PDF 的告警与断言无关
        walk(list(reader.outline))
    return out


def legacy_read_outline(path) -> list[tuple[str, int]]:
    """75824a3 (`slcn/pdfmerge.py`) 里 `read_outline` 的同构实现（R06：旧版可读）。"""
    reader = PdfReader(str(path))
    out: list[tuple[str, int]] = []
    for it in list(reader.outline):
        if isinstance(it, list):
            continue
        title = (getattr(it, "title", "") or "").strip()
        try:
            n = reader.get_destination_page_number(it)
        except Exception:  # noqa: BLE001
            n = -1
        out.append((title, n if isinstance(n, int) else -1))
    return out


def snapshot(root) -> dict[str, tuple[str, int, int]]:
    """目录下每个文件的 (SHA, 大小, mtime_ns)：用来断言「一个字节都没动」。"""
    out = {}
    for p in sorted(pathlib.Path(root).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (sha256(p), st.st_size, st.st_mtime_ns)
    return out


def leftovers(root) -> list[str]:
    """目标旁残留的临时文件（固定后缀 `.tmp-merge`，崩溃后最多一份）。"""
    return sorted(str(p) for p in pathlib.Path(root).rglob("*")
                  if "tmp-merge" in p.name)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ENGINE), str(ENGINE / "vendor")])
    return subprocess.run(
        [sys.executable, str(ENGINE / "mergepdf.py"), *args],
        cwd=str(ENGINE), env=env, capture_output=True, text=True, timeout=180)


def last_line(stdout: str) -> str:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# ── 期目录夹具（与既有 test_pdfmerge.py 的目录约定一致）────────────────────

def build_issue(root, *, a_pages=2, b_pages=3, a_width=200, b_width=200,
                articles_label="A_Author", corrupt_article=False,
                editors=False, back=False) -> pathlib.Path:
    root = pathlib.Path(root)
    mk_pdf(root / "扉页.pdf", 1)
    mk_pdf(root / "000 目录" / "Chinese Content.pdf", 1, [("Chinese Content", 0)])
    mk_pdf(root / "000 目录" / "contents.pdf", 1)
    a = root / f"01 {articles_label}" / f"{articles_label}.pdf"
    if corrupt_article:
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_bytes(b"%PDF-1.4\n% P04 fault-injected corrupt input (no valid EOF)\n")
    else:
        mk_pdf(a, a_pages, [("小节一", 0), ("小节二", a_pages - 1)], width=a_width)
    mk_pdf(root / "02 B_Author" / "B_Author.pdf", b_pages,
           [("B_Author 详情", 0)], width=b_width)
    if editors:
        mk_pdf(root / "编委页.pdf", 1, [("陈旧的编委页书签", 0)])
        mk_pdf(root / "cneditor_legacy.pdf", 1)          # 同档 others 候选
    if back:
        mk_pdf(root / "cover+bottom+Englishi_2024.pdf", 2, [("封底自带书签", 0)])
    return root


def quiet(*_args, **_kwargs):
    return None


# ── R01：原子写出 ───────────────────────────────────────────────────────

def test_r01_atomic_write(tmp: pathlib.Path) -> None:
    root = build_issue(tmp / "r01")
    outdir = tmp / "r01-out"

    # 1) 写一半失败：已有目标逐字节不变、无残留
    target = outdir / "合并 已有.pdf"
    mk_pdf(target, 1, [("旧成品", 0)])
    before = sha256(target)
    size_before = target.stat().st_size

    m = M.Merger()
    m.append(str(root / "000 目录" / "Chinese Content.pdf"), "Chinese Content")

    def boom(self, stream):  # PdfWriter.write 写一半后抛错
        stream.write(b"%PDF-1.7\n% P04 partial write then raise\n")
        stream.flush()
        raise OSError("P04 fault-injected write failure")

    raised = False
    with mock.patch.object(PdfWriter, "write", boom):
        try:
            m.write(str(target))
        except OSError as e:
            raised = "fault-injected" in str(e)
    check("R01 写一半失败会抛错", raised)
    check("R01 写一半失败后旧目标 SHA 不变", sha256(target) == before,
          f"{before[:12]} vs {sha256(target)[:12]}")
    check("R01 写一半失败后旧目标大小不变", target.stat().st_size == size_before)
    check("R01 写一半失败后无临时残留", leftovers(outdir) == [], str(leftovers(outdir)))
    check("R01 写一半失败后旧目标仍是合法 PDF",
          len(PdfReader(str(target)).pages) == 1)

    # 2) 目标原本不存在：失败后仍不存在，也不留伪成品
    target2 = outdir / "新目标.pdf"
    with mock.patch.object(PdfWriter, "write", boom):
        try:
            m.write(str(target2))
        except OSError:
            pass
    check("R01 新目标失败后不存在", not target2.exists())
    check("R01 新目标失败后无临时残留", leftovers(outdir) == [], str(leftovers(outdir)))

    # 3) 成功替换：目标是新的合法 PDF，临时文件清理干净
    target3 = outdir / "替换.pdf"
    mk_pdf(target3, 1, [("旧内容", 0)])
    old_sha = sha256(target3)
    m.write(str(target3))
    check("R01 成功替换后 SHA 变化", sha256(target3) != old_sha)
    check("R01 成功替换后页数一致",
          len(PdfReader(str(target3)).pages) == m.page_count == 1)
    check("R01 成功替换后无临时残留", leftovers(outdir) == [], str(leftovers(outdir)))
    check("R01 成功替换后书签是新的",
          flat_outline(target3)[0] == ("Chinese Content", 0),
          str(flat_outline(target3)))

    # 4) 上次崩溃残留的固定名临时文件：先清掉再写，成功后不残留
    stale = pathlib.Path(str(target3) + M._TMP_SUFFIX)
    stale.write_bytes(b"stale half-written junk from a previous crash")
    m.write(str(target3))
    check("R01 旧残留临时文件被清理", not stale.exists() and leftovers(outdir) == [],
          str(leftovers(outdir)))
    check("R01 清理残留后产物仍合法", len(PdfReader(str(target3)).pages) == 1)

    # 5) os.replace 失败：旧目标不变、临时文件清理、异常照原样抛
    target5 = outdir / "replace失败.pdf"
    mk_pdf(target5, 1)
    sha5 = sha256(target5)
    err = False
    with mock.patch.object(M.os, "replace", side_effect=OSError("P04 replace boom")):
        try:
            m.write(str(target5))
        except OSError as e:
            err = "replace boom" in str(e)
    check("R01 replace 失败会抛错", err)
    check("R01 replace 失败后旧目标 SHA 不变", sha256(target5) == sha5)
    check("R01 replace 失败后无临时残留", leftovers(outdir) == [], str(leftovers(outdir)))

    # 6) 目标目录不存在：自动建目录，产物合法、无残留
    deep = outdir / "新建 目录" / "更深 一层" / "结果.pdf"
    m.write(str(deep))
    check("R01 目标目录不存在会创建", deep.exists()
          and len(PdfReader(str(deep)).pages) == 1)
    check("R01 深层目录无临时残留", leftovers(outdir) == [], str(leftovers(outdir)))

    # 7) CLI 成功：rc=0，末行契约不变，真实目录扫描无残留
    root_cli = build_issue(tmp / "r01-cli")
    out_cli = tmp / "r01-cli-out" / "合并 结果.pdf"
    p = run_cli("--folder", str(root_cli), "--out", str(out_cli))
    check("R01 CLI 成功 rc=0", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr[-300:]}")
    check("R01 CLI 成功末行契约不变",
          last_line(p.stdout) == f"完成。合并文件：{out_cli}（共 8 页）",
          last_line(p.stdout))
    check("R01 CLI 成功后产物存在且 8 页",
          out_cli.exists() and len(PdfReader(str(out_cli)).pages) == 8)
    check("R01 CLI 成功后目录无临时残留", leftovers(root_cli) == []
          and leftovers(tmp / "r01-cli-out") == [],
          str(leftovers(root_cli) + leftovers(tmp / "r01-cli-out")))

    # 8) CLI 失败：rc=2，stderr 有中文原因（用「输出=输入」触发零写入失败）
    article = root_cli / "01 A_Author" / "A_Author.pdf"
    sha_in = sha256(article)
    before_snap = snapshot(root_cli)
    p2 = run_cli("--folder", str(root_cli), "--out", str(article))
    check("R01 CLI 失败 rc=2", p2.returncode == 2,
          f"rc={p2.returncode} stderr={p2.stderr[-300:]}")
    check("R01 CLI 失败 stderr 有中文原因",
          "❌ 合并失败" in p2.stderr and "同一个文件" in p2.stderr, p2.stderr[-300:])
    check("R01 CLI 失败后输入 SHA 不变", sha256(article) == sha_in)
    check("R01 CLI 失败后源目录快照不变", snapshot(root_cli) == before_snap)
    check("R01 CLI 失败后无临时残留", leftovers(root_cli) == [],
          str(leftovers(root_cli)))


# ── R02：输出=输入零写入拒绝 ────────────────────────────────────────────

def test_r02_identity(tmp: pathlib.Path) -> None:
    # 1) 同目录中文文件名：out 指向文章输入
    root = build_issue(tmp / "r02")
    article = root / "01 A_Author" / "A_Author.pdf"
    before = snapshot(root)
    raised = None
    try:
        M.merge_issue(str(root), out=str(article), log=quiet)
    except M.OutputIsInputError as e:
        raised = str(e)
    check("R02 输出=输入会拒绝", raised is not None, str(raised))
    check("R02 拒绝原因含文件名与中文说明",
          raised is not None and "同一个文件" in raised and "A_Author.pdf" in raised,
          str(raised))
    check("R02 拒绝后输入 SHA 不变", sha256(article) == before["01 A_Author/A_Author.pdf"][0])
    check("R02 拒绝后源目录快照不变", snapshot(root) == before)
    check("R02 拒绝后无临时残留", leftovers(root) == [], str(leftovers(root)))

    # 2) 中文 + 空格路径（期目录名、文章名、输出目录名都含中文/空格）
    root_cn = build_issue(tmp / "第 5 期 测试", articles_label="王_小")
    art_cn = root_cn / "01 王_小" / "王_小.pdf"
    sha_cn = sha256(art_cn)
    raised = None
    try:
        M.merge_issue(str(root_cn), out=str(art_cn), log=quiet)
    except M.OutputIsInputError as e:
        raised = str(e)
    check("R02 中文+空格路径也会拒绝",
          raised is not None and "王_小.pdf" in raised, str(raised))
    check("R02 中文+空格路径输入 SHA 不变", sha256(art_cn) == sha_cn)
    check("R02 中文+空格路径无临时残留", leftovers(root_cn) == [])

    # 3) 符号链接指向输入：samefile 识破
    link = root / "看起来像输出的链接.pdf"
    os.symlink(article, link)
    sha_art = sha256(article)
    raised = None
    try:
        M.merge_issue(str(root), out=str(link), log=quiet)
    except M.OutputIsInputError as e:
        raised = str(e)
    check("R02 符号链接不能绕过", raised is not None, str(raised))
    check("R02 符号链接指向的原稿 SHA 不变", sha256(article) == sha_art)
    check("R02 符号链接仍指向原稿且无临时残留",
          link.is_symlink() and os.readlink(link) == str(article)
          and leftovers(root) == [])

    # 4) 相对路径：chdir 进期目录，out 用相对路径指向文章
    cwd = os.getcwd()
    try:
        os.chdir(root_cn)
        raised = None
        try:
            M.merge_issue(".", out="01 王_小/王_小.pdf", log=quiet)
        except M.OutputIsInputError as e:
            raised = str(e)
    finally:
        os.chdir(cwd)
    check("R02 相对路径不能绕过", raised is not None, str(raised))
    check("R02 相对路径输入 SHA 不变", sha256(art_cn) == sha_cn)

    # 5) 损坏/打不开的输入也属于「本次要读的文件」（登记在打开之前）
    root_bad = build_issue(tmp / "r02-corrupt", corrupt_article=True)
    bad = root_bad / "01 A_Author" / "A_Author.pdf"
    sha_bad = sha256(bad)
    before_bad = snapshot(root_bad)
    raised = None
    try:
        M.merge_issue(str(root_bad), out=str(bad), log=quiet)
    except M.OutputIsInputError as e:
        raised = str(e)
    check("R02 损坏输入也不能被输出覆盖", raised is not None, str(raised))
    check("R02 损坏输入 SHA 不变", sha256(bad) == sha_bad)
    check("R02 损坏输入拒绝后目录快照不变", snapshot(root_bad) == before_bad)
    check("R02 损坏输入拒绝后无临时残留", leftovers(root_bad) == [])

    # 6) --editors-page 检测候选（选中 + 同档 others）都纳入保护集
    root_ed = build_issue(tmp / "r02-editors", editors=True)
    chosen = root_ed / "编委页.pdf"
    other = root_ed / "cneditor_legacy.pdf"
    sha_chosen, sha_other = sha256(chosen), sha256(other)
    raised_chosen = raised_other = None
    try:
        M.merge_issue(str(root_ed), out=str(chosen), editors_page=True, log=quiet)
    except M.OutputIsInputError as e:
        raised_chosen = str(e)
    try:
        M.merge_issue(str(root_ed), out=str(other), editors_page=True, log=quiet)
    except M.OutputIsInputError as e:
        raised_other = str(e)
    check("R02 编委页选中候选不能被覆盖", raised_chosen is not None
          and "编委页.pdf" in raised_chosen, str(raised_chosen))
    check("R02 编委页同档 others 候选也不能被覆盖", raised_other is not None
          and "cneditor_legacy.pdf" in raised_other, str(raised_other))
    check("R02 编委页候选 SHA 不变", sha256(chosen) == sha_chosen
          and sha256(other) == sha_other)
    check("R02 编委页候选拒绝后无临时残留", leftovers(root_ed) == [])

    # 7) --back-cover 检测候选同样受保护
    root_back = build_issue(tmp / "r02-back", back=True)
    back = root_back / "cover+bottom+Englishi_2024.pdf"
    sha_back = sha256(back)
    raised = None
    try:
        M.merge_issue(str(root_back), out=str(back), back_cover=True, log=quiet)
    except M.OutputIsInputError as e:
        raised = str(e)
    check("R02 封底候选不能被覆盖", raised is not None
          and "Englishi_2024.pdf" in raised, str(raised))
    check("R02 封底候选 SHA 不变", sha256(back) == sha_back)
    check("R02 封底候选拒绝后无临时残留", leftovers(root_back) == [])

    # 8) 库级 write() 的底账：append 过的输入即受保护
    m = M.Merger()
    m.append(str(article), "A_Author")
    raised = None
    try:
        m.write(str(article))
    except M.OutputIsInputError as e:
        raised = str(e)
    check("R02 Merger.write 直接传输入路径会拒绝", raised is not None, str(raised))

    # 9) CLI：--out 指向输入（中文/空格路径）→ rc=2、stderr 可见、SHA 不变、零写入
    root_cli = build_issue(tmp / "第 5 期 CLI", articles_label="王_小")
    art_cli = root_cli / "01 王_小" / "王_小.pdf"
    sha_cli = sha256(art_cli)
    before_cli = snapshot(root_cli)
    p = run_cli("--folder", str(root_cli), "--out", str(art_cli))
    check("R02 CLI 输出=输入 rc=2", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr[-300:]}")
    check("R02 CLI stderr 有可读中文原因",
          "❌ 合并失败" in p.stderr and "同一个文件" in p.stderr
          and "王_小.pdf" in p.stderr, p.stderr[-300:])
    check("R02 CLI 后输入 SHA 不变", sha256(art_cli) == sha_cli)
    check("R02 CLI 后源目录快照不变（零写入）", snapshot(root_cli) == before_cli)
    check("R02 CLI 后无临时残留", leftovers(root_cli) == [],
          str(leftovers(root_cli)))

    # 10) CLI：--out 指向损坏输入 → rc=2、SHA 不变
    root_bad_cli = build_issue(tmp / "r02-corrupt-cli", corrupt_article=True)
    bad_cli = root_bad_cli / "01 A_Author" / "A_Author.pdf"
    sha_bad_cli = sha256(bad_cli)
    before_bad_cli = snapshot(root_bad_cli)
    p = run_cli("--folder", str(root_bad_cli), "--out", str(bad_cli))
    check("R02 CLI 损坏输入不可被覆盖 rc=2", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr[-300:]}")
    check("R02 CLI 损坏输入 SHA 不变", sha256(bad_cli) == sha_bad_cli)
    check("R02 CLI 损坏输入后快照不变", snapshot(root_bad_cli) == before_bad_cli)
    check("R02 CLI 损坏输入后无临时残留", leftovers(root_bad_cli) == [],
          str(leftovers(root_bad_cli)))


# ── R03：顺序 / 页数 / 书签 / 不按页数去重 ──────────────────────────────

BASE_FLAT = [
    ("cover", 0),
    ("Chinese Content", 1),
    ("contents", 2),
    ("A_Author", 3),
    ("B_Author", 5),
    ("Chinese Content", 1),     # 源文件自带大纲：默认平铺在程序登记项之后（上游行为）
    ("小节一", 3),
    ("小节二", 4),
    ("B_Author 详情", 5),
]


def test_r03_order_and_outline(tmp: pathlib.Path) -> None:
    root = build_issue(tmp / "r03")
    out = tmp / "r03-out" / "合并.pdf"
    res = M.merge_issue(str(root), out=str(out), log=quiet)
    check("R03 页数 = 各部件实际页数之和", res.page_count == 8 == len(PdfReader(str(out)).pages))
    check("R03 顶层书签与页码不变",
          res.outline == [("cover", 0), ("Chinese Content", 1), ("contents", 2),
                          ("A_Author", 3), ("B_Author", 5)], str(res.outline))
    check("R03 默认平铺大纲不变（顶层 + 源书签平铺在后）",
          flat_outline(out) == BASE_FLAT, str(flat_outline(out)))
    check("R03 返回值 output 是绝对路径", res.output == str(out))

    # nest 语义：小节挂到 A_Author 之下，顶层条目集合不变
    out_nest = tmp / "r03-out" / "嵌套.pdf"
    res_nest = M.merge_issue(str(root), out=str(out_nest), nest_outlines=True, log=quiet)
    nest_flat = flat_outline(out_nest)
    check("R03 --nest-outlines 顶层书签不变", res_nest.outline == res.outline,
          str(res_nest.outline))
    check("R03 --nest-outlines 小节挂到文章之下", nest_flat[:6] == [
        ("cover", 0), ("Chinese Content", 1), ("  Chinese Content", 1),
        ("contents", 2), ("A_Author", 3), ("  小节一", 3)], str(nest_flat))
    check("R03 --nest-outlines 完整层级",
          nest_flat == [("cover", 0), ("Chinese Content", 1),
                        ("  Chinese Content", 1), ("contents", 2),
                        ("A_Author", 3), ("  小节一", 3), ("  小节二", 4),
                        ("B_Author", 5), ("  B_Author 详情", 5)], str(nest_flat))

    # 编委页 + 封底：顺序 扉页→编委页→000→各篇→封底，页数相加
    root_extra = build_issue(tmp / "r03-extra", editors=True, back=True)
    out_extra = tmp / "r03-out" / "含附加页.pdf"
    res_extra = M.merge_issue(str(root_extra), out=str(out_extra),
                              editors_page=True, back_cover=True, log=quiet)
    check("R03 编委页+封底页数 = 1+1+1+1+2+3+2",
          res_extra.page_count == 11 == len(PdfReader(str(out_extra)).pages),
          str(res_extra.page_count))
    check("R03 编委页/封底顺序与页码",
          res_extra.outline == [("cover", 0), ("编委页", 1), ("Chinese Content", 2),
                                ("contents", 3), ("A_Author", 4), ("B_Author", 6),
                                ("封底", 9)], str(res_extra.outline))
    check("R03 编委页/封底只并页面不导自带大纲",
          "陈旧的编委页书签" not in [t for t, _ in flat_outline(out_extra)]
          and "封底自带书签" not in [t for t, _ in flat_outline(out_extra)],
          str(flat_outline(out_extra)))

    # 不按页数去重：A、B 页数相同（各 2 页）但内容（页面宽度）不同，都要保留
    root_dup = build_issue(tmp / "r03-dup", a_pages=2, b_pages=2,
                           a_width=200, b_width=380)
    out_dup = tmp / "r03-out" / "同页数不同内容.pdf"
    res_dup = M.merge_issue(str(root_dup), out=str(out_dup), log=quiet)
    reader = PdfReader(str(out_dup))
    widths = [float(round(page.mediabox.width)) for page in reader.pages]
    check("R03 同页数不同内容页数=1+1+1+2+2", res_dup.page_count == 7, str(res_dup.page_count))
    check("R03 同页数不同内容都被保留（不按页数去重）",
          widths == [200, 200, 200, 200, 200, 380, 380], str(widths))


# ── R04：缺件/损坏完整反馈 ──────────────────────────────────────────────

def test_r04_feedback(tmp: pathlib.Path) -> None:
    # 1) 缺扉页与英文目录：继续合并，missing 指明文件
    root = build_issue(tmp / "r04-missing")
    (root / "扉页.pdf").unlink()
    (root / "000 目录" / "contents.pdf").unlink()
    out = tmp / "r04-missing.pdf"
    res = M.merge_issue(str(root), out=str(out), log=quiet)
    check("R04 缺件仍继续合并（中文目录 1 + A 2 + B 3 共 6 页）",
          res.page_count == 6, str(res.page_count))
    check("R04 missing 指明缺失文件", res.missing == ["扉页.pdf", "contents.pdf"],
          str(res.missing))
    check("R04 缺件不算 warning，产物存在", res.warnings == [] and out.exists())

    # 2) 损坏文章：warning 含实际文件名与原因，不静默、不中断
    root_bad = build_issue(tmp / "r04-corrupt", corrupt_article=True)
    out_bad = tmp / "r04-corrupt.pdf"
    res_bad = M.merge_issue(str(root_bad), out=str(out_bad), log=quiet)
    check("R04 损坏文章仍继续合并（扉页 1 + 中文目录 1 + 英文目录 1 + B 3 = 6 页）",
          res_bad.page_count == 6, str(res_bad.page_count))
    check("R04 损坏文章只产生 1 条 warning", len(res_bad.warnings) == 1,
          str(res_bad.warnings))
    check("R04 损坏文章 warning 含实际文件名与原因",
          bool(res_bad.warnings)
          and "A_Author.pdf" in res_bad.warnings[0]
          and "处理 A_Author 时出错" in res_bad.warnings[0],
          str(res_bad.warnings))
    check("R04 损坏文章不误报 missing", res_bad.missing == [], str(res_bad.missing))
    check("R04 损坏文章产物合法", len(PdfReader(str(out_bad)).pages) == 6,
          str(len(PdfReader(str(out_bad)).pages)))

    # 3) 损坏编委页：warning 含文件名，页面按实际跳过
    root_ed = build_issue(tmp / "r04-corrupt-editors")
    (root_ed / "编委页.pdf").write_bytes(b"%PDF-1.4\n% corrupt editors page\n")
    out_ed = tmp / "r04-corrupt-editors.pdf"
    res_ed = M.merge_issue(str(root_ed), out=str(out_ed),
                           editors_page=True, log=quiet)
    check("R04 损坏编委页 warning 含文件名",
          len(res_ed.warnings) == 1 and "编委页.pdf" in res_ed.warnings[0],
          str(res_ed.warnings))
    check("R04 损坏编委页不再算页数", res_ed.page_count == 8, str(res_ed.page_count))

    # 4) 损坏扉页：warning 含文件名，missing 不误报
    root_cover = build_issue(tmp / "r04-corrupt-cover")
    (root_cover / "扉页.pdf").write_bytes(b"%PDF-1.4\n% corrupt cover\n")
    out_cover = tmp / "r04-corrupt-cover.pdf"
    res_cover = M.merge_issue(str(root_cover), out=str(out_cover), log=quiet)
    check("R04 损坏扉页 warning 含文件名",
          len(res_cover.warnings) == 1 and "扉页.pdf" in res_cover.warnings[0],
          str(res_cover.warnings))
    check("R04 损坏扉页按实际页数计", res_cover.page_count == 7, str(res_cover.page_count))


# ── R06：历史产物只读 / 新产物可被基线读取语义读取 ───────────────────────

def test_r06_legacy(tmp: pathlib.Path) -> None:
    root = build_issue(tmp / "r06")
    historical = root / "合并2026_01_01_00_00_00.pdf"
    mk_pdf(historical, 1, [("历史成品", 0)])
    sha_hist = sha256(historical)
    snap_hist = snapshot(root)[historical.relative_to(root).as_posix()]

    out = tmp / "r06-out" / "新产物.pdf"
    res = M.merge_issue(str(root), out=str(out), log=quiet)
    after_hist = snapshot(root)[historical.relative_to(root).as_posix()]
    check("R06 历史 合并*.pdf 逐字节不变（SHA/大小/mtime）",
          after_hist == snap_hist, f"{snap_hist} -> {after_hist}")
    check("R06 历史 合并*.pdf 未被当作输入",
          res.missing == [] and "合并2026_01_01_00_00_00.pdf" not in str(res.outline))
    check("R06 历史 合并*.pdf 仍可读", len(PdfReader(str(historical)).pages) == 1)

    # 不传 --out 的默认命名：新产物与历史文件并存，历史文件逐字节不变
    p = run_cli("--folder", str(root))
    default_products = sorted(str(q) for q in root.glob("合并*.pdf")
                              if q.name != "合并2026_01_01_00_00_00.pdf")
    check("R06 默认命名产物生成且不覆盖历史文件",
          p.returncode == 0 and len(default_products) == 1
          and sha256(historical) == sha_hist
          and len(PdfReader(default_products[0]).pages) == 8,
          f"rc={p.returncode} products={default_products}")
    check("R06 默认命名成功后无临时残留", leftovers(root) == [],
          str(leftovers(root)))
    check("R06 产物是标准 PDF（%PDF- 头）",
          pathlib.Path(out).read_bytes()[:5] == b"%PDF-")

    # 旧版 75824a3 的 PdfReader/read_outline 语义可读、页数一致
    legacy = legacy_read_outline(out)
    check("R06 旧版 read_outline 可读新产物（顶层封面/目录/文章都在）",
          [t for t, _ in legacy][:5] == ["cover", "Chinese Content", "contents",
                                         "A_Author", "B_Author"], str(legacy))
    check("R06 旧版读取的顶层页码与引擎返回值一致",
          legacy[:5] == res.outline, str(legacy[:5]))
    check("R06 旧版 PdfReader 页数一致",
          len(PdfReader(str(out)).pages) == res.page_count == 8)

    # 历史 合并*.pdf 不参与编委页识别（_pdfs_in 排除以「合并」开头的文件）
    root2 = tmp / "r06-detect"
    mk_pdf(root2 / "合并编委页.pdf", 1)
    mk_pdf(root2 / "扉页.pdf", 1)
    chosen, others = M.detect_editors_page(str(root2))
    check("R06 历史 合并*.pdf 不参与编委页识别", chosen is None and others == [],
          f"{chosen} {others}")


# ── 主流程 ──────────────────────────────────────────────────────────────

def main() -> int:
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="slcn_p04_") as tmpdir:
        tmp = pathlib.Path(tmpdir)
        test_r01_atomic_write(tmp)
        test_r02_identity(tmp)
        test_r03_order_and_outline(tmp)
        test_r04_feedback(tmp)
        test_r06_legacy(tmp)
    if fails:
        print(f"FAIL 共 {len(fails)} 项：")
        for name in fails:
            print(f"      · {name}")
        print(f"失败 {len(fails)} 项（{len(fails)} failed）")
        return 1
    print(f"全部 {pass_n} 项断言通过")
    print(f"失败 0 项（全部通过）  耗时 {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
