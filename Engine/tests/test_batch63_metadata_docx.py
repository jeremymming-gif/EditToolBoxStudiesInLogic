#!/usr/bin/env python3
r"""第六十三批 E2：LaTeX 通道 `--metadata` 支持 Word 元数据文件（`.docx` → 临时 `.tex`）。

覆盖（全部离线：不联网、不调付费 API、不跑 TeX；fixtures 全为虚构样例）：
  · `.docx` → `slcn.metadocx.adapt_metadata` 抽取题名/作者/单位/邮箱/摘要/关键词，
    按目标模板语种写 `\my*` 宏的临时 `.tex`；CLI 端到端产物字段正确、临时文件被清理；
  · 0 字段 `.docx` → exit 2 + 明确报错（不许静默产出无元数据稿）；
  · 老式 `.doc` → exit 2 + 「另存为 .docx/.tex」文案；未知二进制同样 exit 2；
  · 协同两段式 + Word 元数据 → exit 2 + 明确限制（临时文件无法跨进程复用）；
  · 默认零变化：无 `--metadata` / `.tex` 元数据 → 产物/日志与 HEAD（`git archive HEAD`）
    逐字节相同；`.tex` 元数据一个字节都不被改写；
  · macOS 有 `textutil` 时附加「.docx→.doc→.docx 再喂引擎」的 GUI 等价路径（否则 SKIP）。

用法：
    cd Engine && PYTHONPATH=.:vendor python3 tests/test_batch63_metadata_docx.py
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import metadocx  # noqa: E402
from slcn import latex as _latex  # noqa: E402

# 批106（B-146 波2-2）dogfood：内联 make_docx 改为共享夹具生成器（hermetic/确定性）。
# 共享工具只写调用方给的路径，不联网、不读稿件库；本文件其余断言一字未动。
if os.path.join(_REPO, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO, "tools"))
from docx_fixture_gen import make_docx as _make_fixture_docx  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []
PASS = 0


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


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def snapshot(folder: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            out[os.path.relpath(p, folder)] = sha(p)
    return out


def make_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("SLCN_LLM_", "SLCN_SEARCH_", "SLCN_NOTES")):
            env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    return env


ENV = make_env()


def ensure_head_engine(tmp: str) -> str:
    dest = os.path.join(tmp, "head")
    try:
        tar = subprocess.run(["git", "-C", _REPO, "archive", "HEAD"],
                             capture_output=True, timeout=120)
        if tar.returncode != 0:
            return ""
        os.makedirs(dest, exist_ok=True)
        p = os.path.join(tmp, "head.tar")
        with open(p, "wb") as f:
            f.write(tar.stdout)
        with tarfile.open(p) as tf:
            tf.extractall(dest)
        eng = os.path.join(dest, "Engine")
        return eng if os.path.isfile(os.path.join(eng, "convert.py")) else ""
    except Exception:  # noqa: BLE001
        return ""


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def escape_xml(t: str) -> str:
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def make_docx(paras: list[str], path: str) -> str:
    """共享夹具（`tools/docx_fixture_gen.py`；批106 B-146 波2-2 dogfood）。

    原实现只写 `word/document.xml`（手拼最小 zip）；现由共享工具生成完整最小
    OOXML 包（`[Content_Types].xml`/`_rels/.rels`/`word/document.xml`/关系），
    引擎仍只读 `word/document.xml`，所以本文件既有断言口径不变。
    """
    return _make_fixture_docx(path, paras=list(paras), numbering=False)


# 虚构样例（不来自任何真实作者稿件）：中文元数据 + 文末英文那套。
_CN_HEAD = ["论测试逻辑", "张三", "摘要：本文讨论测试逻辑。", "关键词：测试；逻辑"]
_BODY = ["正文第 %d 段。" % i for i in range(40)]
_EN_TAIL = ["A Test of Logic", "Zhang San",
            "Abstract: We study test logic.", "Key words: test; logic"]

# LaTeX 正文（单独一份；元数据全交给 .docx）
_TEX_SRC = r"""\documentclass{article}
\begin{document}
这里是中文正文测试。
\end{document}
"""

_TEX_META = r"""\newcommand{\mytitle}{测试题名}
\newcommand{\mytitleEN}{A Test Title}
\begin{document}
\end{document}
"""


def run_convert(src: str, out: str, extra: list[str], *,
                engine: str = _ENGINE) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(engine, "convert.py"),
           "--source", src, "--out", out, "--name", "Meta_Doc", "--no-original"]
    cmd += extra
    return subprocess.run(cmd, cwd=_ENGINE, env=ENV, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=240)


def out_tex(out: str) -> str:
    return os.path.join(out, "Meta_Doc", "Meta_Doc.tex")


# ── ① adapt_metadata 单元：docx → 临时 .tex 的字段映射 ───────────────────


def test_unit_adapt(tmp: str) -> str:
    docx = make_docx(_CN_HEAD + _BODY + _EN_TAIL, os.path.join(tmp, "meta.docx"))
    path, fields, tmp_tex = metadocx.adapt_metadata(docx, lang="zh")
    text = read(path)
    check("①-1 docx → 临时 .tex（返回第三个值=临时路径）",
          tmp_tex and os.path.isfile(tmp_tex) and tmp_tex.endswith(".tex"))
    check("①-2 日志字段清单含 题名/作者/摘要/关键词/英文题名/英文摘要",
          {"题名", "作者", "摘要", "关键词", "英文题名", "英文摘要"} <= set(fields),
          str(fields))
    check("①-3 CN 模板：mytitle=中文题名、mytitleEN=英文题名",
          _latex.get_macro_value(text, "mytitle") == "论测试逻辑"
          and _latex.get_macro_value(text, "mytitleEN") == "A Test of Logic",
          f"{_latex.get_macro_value(text, 'mytitle')!r}")
    check("①-4 CN 模板：中文摘要/关键词/作者写入 \\my*",
          "测试逻辑" in (_latex.get_macro_value(text, "myabstract") or "")
          and "测试；逻辑" in (_latex.get_macro_value(text, "mykeywords") or "")
          and _latex.get_macro_value(text, "myfirstauthor") == "张三")
    check("①-5 CN 模板：英文摘要写入 myabstractEN",
          "We study test logic" in (_latex.get_macro_value(text, "myabstractEN") or ""))
    check("①-6 临时 .tex 只写 \\my* 宏（不含正文，不做 Word→LaTeX 全文转换）",
          "正文第" not in text and "\\my" in text and "\\documentclass" not in text)

    path_en, _f_en, tmp_en = metadocx.adapt_metadata(docx, lang="en")
    text_en = read(path_en)
    check("①-7 EN 模板：mytitle=英文题名、mytitleCN=中文题名",
          _latex.get_macro_value(text_en, "mytitle") == "A Test of Logic"
          and _latex.get_macro_value(text_en, "mytitleCN") == "论测试逻辑")
    os.unlink(tmp_tex)
    os.unlink(tmp_en)

    # .tex 文本文件原样返回（一个字节都不动）
    mt = write(os.path.join(tmp, "meta.tex"), _TEX_META)
    before = sha(mt)
    p2, f2, t2 = metadocx.adapt_metadata(mt, lang="zh")
    check("①-8 .tex 元数据原样返回、不改写、无临时文件",
          p2 == mt and f2 == [] and t2 == "" and sha(mt) == before)
    return docx


# ── ② 错误路径：0 字段 / .doc / 未知二进制 ───────────────────────────────


def test_errors(tmp: str) -> None:
    empty = make_docx([], os.path.join(tmp, "empty.docx"))
    try:
        metadocx.adapt_metadata(empty, lang="zh")
        bad = ""
    except metadocx.MetadataError as e:
        bad = str(e)
    check("②-1 0 字段 docx → MetadataError 且写明「没有提取到任何元数据字段」",
          "没有提取到任何元数据字段" in bad, bad[:120])

    doc = os.path.join(tmp, "old.doc")
    with open(doc, "wb") as f:
        f.write(metadocx.OLE_MAGIC + b"\x00" * 64)
    try:
        metadocx.adapt_metadata(doc, lang="zh")
        bad2 = ""
    except metadocx.MetadataError as e:
        bad2 = str(e)
    check("②-2 老式 .doc → MetadataError 且提示另存 .docx/.tex",
          "老式 .doc" in bad2 and "另存为 .docx 或 .tex" in bad2, bad2[:120])

    binp = os.path.join(tmp, "meta.bin")
    with open(binp, "wb") as f:
        f.write(b"PK\x03\x04\x00\x00\x01binary")
    try:
        metadocx.adapt_metadata(binp, lang="zh")
        bad3 = ""
    except metadocx.MetadataError as e:
        bad3 = str(e)
    check("②-3 未知二进制元数据 → MetadataError（消灭静默乱码）",
          "二进制" in bad3, bad3[:120])

    # 坏 docx（zip 损坏）→ MetadataError（不抛原始异常）
    broken = os.path.join(tmp, "broken.docx")
    write(broken, "not a zip")
    try:
        metadocx.adapt_metadata(broken, lang="zh")
        bad4 = ""
    except metadocx.MetadataError as e:
        bad4 = str(e)
    check("②-4 损坏 .docx → MetadataError（exit 2 由 CLI 负责）",
          "读取 Word 文件失败" in bad4, bad4[:120])


# ── ③ CLI 端到端：docx 元数据 → 产物字段 + 日志 + 临时文件清理 ───────────


def test_cli_e2e(tmp: str) -> None:
    docx = make_docx(_CN_HEAD + _BODY + _EN_TAIL, os.path.join(tmp, "cli", "meta.docx"))
    src = write(os.path.join(tmp, "cli", "src", "main.tex"), _TEX_SRC)
    out = os.path.join(tmp, "cli", "out")
    r = run_convert(src, out, ["--metadata", docx])
    allout = r.stdout + r.stderr
    m = re.search(r"临时 \.tex：(.+?)（运行结束自动清理）", r.stdout)
    tmp_path = m.group(1).strip() if m else ""
    tex = read(out_tex(out)) if os.path.isfile(out_tex(out)) else ""
    check("③-1 docx 元数据端到端 exit 0", r.returncode == 0, allout[-400:])
    check("③-2 日志写明「元数据源（Word）」+ 提取字段数 + 临时文件路径",
          "元数据源（Word）" in r.stdout and "提取字段" in r.stdout
          and "临时 .tex：" in r.stdout, r.stdout[-500:])
    check("③-3 产物 \\mytitle / \\mytitleEN 来自 Word 元数据",
          _latex.get_macro_value(tex, "mytitle") == "论测试逻辑"
          and _latex.get_macro_value(tex, "mytitleEN") == "A Test of Logic",
          f"{_latex.get_macro_value(tex, 'mytitle')!r}")
    check("③-4 产物中文摘要/关键词/作者正确",
          "测试逻辑" in (_latex.get_macro_value(tex, "myabstract") or "")
          and "测试；逻辑" in (_latex.get_macro_value(tex, "mykeywords") or "")
          and _latex.get_macro_value(tex, "myfirstauthor") == "张三")
    check("③-5 临时 .tex 在进程结束后已清理", bool(tmp_path)
          and not os.path.exists(tmp_path), tmp_path)

    # 直接跑一次（不带 --name）证明作者推导也走新流程：不 panic 即可（产物名可推导）
    out2 = os.path.join(tmp, "cli", "out2")
    r2 = subprocess.run(
        [sys.executable, os.path.join(_ENGINE, "convert.py"), "--source", src,
         "--out", out2, "--metadata", docx, "--no-original"],
        cwd=_ENGINE, env=ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240)
    check("③-6 无 --name 时作者/输出名推导照常（不因 Word 元数据改动而崩）",
          r2.returncode == 0 and "第一作者" in r2.stdout, (r2.stdout + r2.stderr)[-300:])


def test_cli_errors(tmp: str) -> None:
    src = write(os.path.join(tmp, "bad", "src", "main.tex"), _TEX_SRC)
    out = os.path.join(tmp, "bad", "out")
    empty = make_docx([], os.path.join(tmp, "bad", "empty.docx"))
    r = run_convert(src, out, ["--metadata", empty])
    check("③-7 CLI：0 字段 docx → exit 2 + 明确报错",
          r.returncode == 2 and "没有提取到任何元数据字段" in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-300:])

    doc = os.path.join(tmp, "bad", "old.doc")
    with open(doc, "wb") as f:
        f.write(metadocx.OLE_MAGIC + b"\x00" * 64)
    r2 = run_convert(src, out, ["--metadata", doc])
    check("③-8 CLI：.doc → exit 2 + 「另存为 .docx/.tex」",
          r2.returncode == 2 and "老式 .doc" in (r2.stdout + r2.stderr)
          and "另存为 .docx 或 .tex" in (r2.stdout + r2.stderr))

    r3 = run_convert(src, out, ["--metadata", empty, "--coop-taskbook-only"])
    check("③-9 CLI：协同两段式 + Word 元数据 → exit 2 + 明确限制",
          r3.returncode == 2 and "暂不支持协同两段式" in (r3.stdout + r3.stderr),
          (r3.stdout + r3.stderr)[-300:])


# ── ④ 默认零变化：无 metadata / .tex metadata 与 HEAD 逐字节相同 ─────────


def test_zero_change(tmp: str, head: str) -> None:
    src = write(os.path.join(tmp, "zero", "src", "main.tex"), _TEX_SRC)
    # 新/HEAD 用**同一个 --out 路径**（跑完删掉再跑），日志才可能逐字节相同。
    out = os.path.join(tmp, "zero", "out")
    new = run_convert(src, out, [])
    snap_new = snapshot(os.path.join(out, "Meta_Doc"))
    if not head:
        skip("④ 无 metadata/tex metadata 与 HEAD 对照", "git archive HEAD 失败")
        return
    shutil.rmtree(out, ignore_errors=True)
    old = run_convert(src, out, [], engine=head)
    snap_old = snapshot(os.path.join(out, "Meta_Doc"))
    check("④-1 无 --metadata：产物与 HEAD 逐字节相同", new.returncode == 0
          and old.returncode == 0 and snap_new == snap_old)
    check("④-2 无 --metadata：日志与 HEAD 逐字节相同", new.stdout == old.stdout)

    mt = write(os.path.join(tmp, "zero", "meta.tex"), _TEX_META)
    shutil.rmtree(out, ignore_errors=True)
    new2 = run_convert(src, out, ["--metadata", mt])
    snap_new2 = snapshot(os.path.join(out, "Meta_Doc"))
    shutil.rmtree(out, ignore_errors=True)
    old2 = run_convert(src, out, ["--metadata", mt], engine=head)
    check("④-3 .tex 元数据：产物与 HEAD 逐字节相同（原样透传）",
          new2.returncode == 0 and old2.returncode == 0
          and snap_new2 == snapshot(os.path.join(out, "Meta_Doc")))
    check("④-4 .tex 元数据：日志与 HEAD 逐字节相同（无 Word 相关新日志）",
          new2.stdout == old2.stdout
          and "元数据源（Word）" not in new2.stdout)


# ── ⑤ macOS GUI 等价路径：textutil .docx→.doc→.docx → 引擎（可用才测）───


def test_textutil_doc_roundtrip(tmp: str) -> None:
    textutil = shutil.which("textutil")
    if not textutil:
        skip("⑤ textutil .doc 转换后 e2e", "本机无 textutil（非 macOS）")
        return
    d = os.path.join(tmp, "tu")
    os.makedirs(d, exist_ok=True)
    docx = make_docx(_CN_HEAD + _BODY + _EN_TAIL, os.path.join(d, "meta.docx"))
    doc = os.path.join(d, "meta.doc")
    docx2 = os.path.join(d, "meta2.docx")
    for out_p, args in ((doc, ["-convert", "doc"]),
                        (docx2, ["-convert", "docx"])):
        r = subprocess.run([textutil, *args, "-output", out_p, docx if out_p == doc
                            else doc], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not os.path.isfile(out_p):
            skip("⑤ textutil .doc 转换后 e2e", f"textutil 转换失败：{r.stderr[-120:]}")
            return
    src = write(os.path.join(d, "main.tex"), _TEX_SRC)
    out = os.path.join(d, "out")
    r = run_convert(src, out, ["--metadata", docx2])
    tex = read(out_tex(out)) if os.path.isfile(out_tex(out)) else ""
    check("⑤-1 GUI 等价路径（.docx→.doc→.docx）→ exit 0",
          r.returncode == 0, (r.stdout + r.stderr)[-300:])
    check("⑤-2 转换后的 docx 元数据仍写入产物",
          _latex.get_macro_value(tex, "mytitle") == "论测试逻辑",
          f"{_latex.get_macro_value(tex, 'mytitle')!r}")

_AUTHOR_INFO_BLOCK = ["作者名字：李四", "邮箱：lisi@example.edu.cn", "单位信息：某大学哲学系"]


def test_b223_supplements(tmp: str) -> None:
    src = make_docx(_CN_HEAD + _BODY, os.path.join(tmp, "supp_src.docx"))
    supp = make_docx(_AUTHOR_INFO_BLOCK, os.path.join(tmp, "supp_info.docx"))
    out = os.path.join(tmp, "supp_out")
    r = run_convert(src, out, ["--metadata", supp])
    tex = read(out_tex(out)) if os.path.isfile(out_tex(out)) else ""
    check("⑥-1 Word 通道：标签作者信息块补空（单位/邮箱），不覆盖正文作者",
          r.returncode == 0
          and _latex.get_macro_value(tex, "myfirstaffiliation") == "某大学哲学系"
          and _latex.get_macro_value(tex, "myfirstemail") == "lisi@example.edu.cn"
          and "张三" in (_latex.get_macro_value(tex, "myfirstauthor") or ""),
          (r.stdout + r.stderr)[-300:])
    check("⑥-2 日志写明补充信息与补入项数",
          "补充信息" in r.stdout and "补充信息补入 2 项" in r.stdout,
          r.stdout[-400:])
    src_tex = write(os.path.join(tmp, "supp_src.tex"), _TEX_SRC)
    a = write(os.path.join(tmp, "supp_a.tex"),
              "\\newcommand{\\mytitle}{甲题名}\n")
    b = write(os.path.join(tmp, "supp_b.tex"),
              "\\newcommand{\\mytitle}{乙题名}\n\\newcommand{\\mytitleEN}{Yi Title}\n")
    out2 = os.path.join(tmp, "supp_out2")
    r2 = run_convert(src_tex, out2, ["--metadata", a, "--metadata", b])
    tex2 = read(out_tex(out2)) if os.path.isfile(out_tex(out2)) else ""
    check("⑥-3 LaTeX 多份补充信息：顺序 = 合并顺序（先到先得）",
          r2.returncode == 0
          and _latex.get_macro_value(tex2, "mytitle") == "甲题名"
          and _latex.get_macro_value(tex2, "mytitleEN") == "Yi Title",
          (r2.stdout + r2.stderr)[-300:])
    check("⑥-4 多份时日志写明份数与来源文件名",
          "元数据源（补充信息 2 份）" in r2.stdout and "supp_a.tex" in r2.stdout,
          r2.stdout[-400:])
    prev = os.path.join(tmp, "preview.json")
    out3 = os.path.join(tmp, "preview_out")
    r3 = run_convert(src, out3, ["--metadata", supp, "--metadata-preview-out", prev])
    data = {}
    if os.path.isfile(prev):
        with open(prev, encoding="utf-8") as f:
            data = json.load(f)
    rows = data.get("rows") or []
    statuses = {row.get("status") for row in rows}
    check("⑥-5 预览 dry-run：JSON 行结构含 字段/生效值/来源/状态",
          r3.returncode == 0 and data.get("schema") == 1 and bool(rows)
          and {"field", "label", "value", "source", "status"} <= set(rows[0]),
          (r3.stdout + r3.stderr)[-300:])
    check("⑥-6 预览状态区分「已填（正文解析）」与「已填（补充文件）」",
          "已填（正文解析）" in statuses
          and any(str(st).startswith("已填（补充文件）") for st in statuses),
          str(sorted(str(st) for st in statuses)))
    check("⑥-7 预览只读：不建产物目录、不写交接单、无临时残留",
          not os.path.isdir(out3)
          and "只读预览，未写入任何产物" in r3.stdout
          and not glob.glob(os.path.join(tmp, "slcn-meta-*")),
          (r3.stdout + r3.stderr)[-300:])


_EN_SHEET = ["Towards a Logic of Databases", "Zhang San", "Abstract",
             "We study the logic of databases.", "Key words: logic; database"]


def test_b223_preview_auto_en(tmp: str) -> None:
    d = os.path.join(tmp, "prev_auto")
    src = make_docx(_CN_HEAD + _BODY, os.path.join(d, "稿.docx"))
    make_docx(_EN_SHEET, os.path.join(d, "稿英文摘要.docx"))
    prev, out = os.path.join(d, "preview.json"), os.path.join(d, "out")
    r = run_convert(src, out, ["--metadata-preview-out", prev])
    with open(prev, encoding="utf-8") as f:
        data = json.load(f) if os.path.isfile(prev) else {}
    rows = {row.get("field"): row for row in (data.get("rows") or [])}
    t = rows.get("title_en", {})
    check("⑥-8 预览复现 Word 同目录自动英文第二源（来源标注可读）",
          r.returncode == 0 and t.get("value") == "Towards a Logic of Databases"
          and "同目录自动第二源" in (t.get("source") or "")
          and "稿英文摘要.docx" in (t.get("source") or ""), str(t))
    check("⑥-9 预览：仍缺的英文槽位（英文单位）列为「缺项」，已补的作者英文姓名不误报",
          rows.get("affiliation_en", {}).get("status") == "缺项"
          and str(rows.get("author_en", {}).get("status", "")).startswith("已填"),
          str({k: v.get("status") for k, v in rows.items()}))


def test_b223_compose_escape(tmp: str) -> None:
    d = os.path.join(tmp, "compose_esc")
    src = write(os.path.join(d, "paper.tex"), _TEX_SRC)
    tex_meta = write(os.path.join(d, "meta.tex"), "\\newcommand{\\mytitle}{甲题名}\n")
    supp = make_docx(["单位信息：A&B 科技 100% 有限公司_研发#部"],
                     os.path.join(d, "supp.docx"))
    out = os.path.join(d, "out")
    r = run_convert(src, out, ["--metadata", tex_meta, "--metadata", supp])
    tex = read(out_tex(out)) if os.path.isfile(out_tex(out)) else ""
    aff = _latex.get_macro_value(tex, "myfirstaffiliation") or ""
    check("⑥-10 LaTeX 多源：.docx 明文值先转义（% & _ # 不破坏编译）",
          r.returncode == 0 and "100\\%" in aff and "A\\&B" in aff
          and "\\_研发" in aff and "\\#" in aff,
          (r.stdout + r.stderr)[-300:] + " | " + aff)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="slcn_b63_meta ")
    try:
        test_unit_adapt(tmp)
        test_errors(tmp)
        test_cli_e2e(tmp)
        test_cli_errors(tmp)
        test_b223_supplements(tmp)
        test_b223_preview_auto_en(tmp)
        test_b223_compose_escape(tmp)
        head = ensure_head_engine(tmp)
        test_zero_change(tmp, head)
        test_textutil_doc_roundtrip(tmp)
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
