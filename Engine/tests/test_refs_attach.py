#!/usr/bin/env python3.14
r"""参考文献附件的选择 / 校验 / 合并（**第 134 条**，2026-10-18）。

缺陷
----
`convert.py` ⑦ 从前「扫描到 refs 候选就并」：71/04 的正文目录里放着 text2bib 的
中间产物 `预处理/用于上传text2bib.txt`（两条文献挤一行、含 `\href{a}{b}`），
footref 把 `\href` 拆进字段 → 产物 `title = {...}{...}` 嵌套花括号 → biber 行级
语法错、**整份 bib 报废**（19 页 vs 人工 20 页）。同目录其实有 `reference.bib`。

本套件钉住修复后的判据（纯离线、合成夹具；不读稿件库）：
  ① `multisource` 的附件识别/结构校验：BibTeX 附件识别、`\href`/`}{`/一行两条拒绝、
     正常编号清单放行；
  ② `select_refs_attachment()`：真 BibTeX 优先、Word 通道不吃 BibTeX、坏候选跳过并给原因；
  ③ `convert` 的「真 .bib」判定与合并辅助：`real_bib_path()` 优先级、
     `bib_attachment_entries()` / `merge_bib_attachment()` 的去重与结构校验、
     `bib_entry_structurally_safe()` 反例；
  ④ `convert.py` 端到端（子进程，输出全在临时目录）：
     有 reference.bib 时不自动并附件；无真 bib 时文本附件可用、坏候选被跳过；
     无真 bib 时 BibTeX 附件按条目原文并进去重。

只写临时目录；不联网、不读稿件库、不跑 corpus.py / build.sh。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_refs_attach.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import convert  # noqa: E402
from slcn import multisource  # noqa: E402
from slcn.multisource import SourceFile  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


TMP = tempfile.mkdtemp(prefix="slcn_refs_attach_")

# ══════════════════════════════════════════════════════════════════════════
# ① multisource：BibTeX 识别 + 文本附件结构校验
# ══════════════════════════════════════════════════════════════════════════
BIB_OK = """@article{a1,
  author = {Zhang, San},
  title  = {A Paper},
  year   = {2020}
}
@book{a2,
  author = {Li, Si},
  title  = {A Book},
  year   = {2021}
}
"""
check("① 真 BibTeX 识别：非空 key 的 @entry 计数",
      multisource.count_bibtex_entries(BIB_OK) == 2
      and multisource.looks_like_bibtex(BIB_OK)
      and not multisource.looks_like_bibtex("没有条目的一段话"))
check("① 附件类型：.bib 扩展名即真 BibTeX；有 @entry 的 .txt 也算",
      multisource.attachment_kind("x/refs.bib", "") == "bib"
      and multisource.attachment_kind("x/refs.txt", BIB_OK) == "bib"
      and multisource.attachment_kind("x/bib.txt", "[1] 张三, 2020, 题名。") == "text")

def _numbered_list(n=8):
    return "".join(
        f'[{i}] 作者{i}, 20{10 + i:02d}, "题名{i}", 逻辑学报, 1{i}({i}): {i * 10}-{i * 10 + 9}.\n'
        for i in range(1, n + 1)
    )


TEXT_OK = _numbered_list()
TEXT_CN = """弗雷格，2006：《弗雷格论著选辑》，王路译，北京：商务印书馆。
[2] 雷磊，2016：《走出“约根森困境”?》，载《法制与社会发展》，2016年第2期。
"""
TEXT_TWO_IN_LINE = (
    "Henderson, R. 2010. Non-defeasible counterfactuality [OL]. "
    "(accessed 4/8/2022) Hsu, Ching-Fen. 2014. Semantic representation [J]. "
    "Language and Linguistics. 15(3): 391-410."
)

TEXT_HREF = (
    "Henderson, R. 2010. X. [OL]. "
    "\\href{https://example.org/a.pdf}{https://example.org/a.pdf}.\n"
)
TEXT_NESTED = "author = {A}{B}\n"
# 像 71/04 的 text2bib 中间产物：多数行正常、个别行含 \href / 两条挤一行
TEXT_BAD_ATTACH = _numbered_list(6) + TEXT_HREF + TEXT_TWO_IN_LINE + "\n"
check("① 正常编号清单 / 中文清单不被拒（不误伤）",
      multisource.text_attachment_issue(TEXT_OK) == ""
      and multisource.text_attachment_issue(TEXT_CN) == "")
check("① 拒绝含 \\href{...} 的候选（71/04 的 text2bib 中间产物）",
      "href" in multisource.text_attachment_issue(TEXT_HREF).lower())
check("① 拒绝含相邻花括号 }{ 的候选",
      "相邻花括号" in multisource.text_attachment_issue(TEXT_NESTED))
check("① 拒绝疑似「一行两条文献」的候选",
      "一行两条" in multisource.text_attachment_issue(TEXT_TWO_IN_LINE),
      multisource.text_attachment_issue(TEXT_TWO_IN_LINE))
check("① 正常 BibTeX 附件通过结构校验",
      multisource.bib_attachment_issue(BIB_OK) == "")
check("① BibTeX 附件结构校验反例：空 key / 花括号不配平 / 相邻 }{",
      "非空 key" in multisource.bib_attachment_issue("@article{, title={X}}\n")
      and "不配平" in multisource.bib_attachment_issue("@article{a, title={X}\n")
      and "相邻花括号" in multisource.bib_attachment_issue("@article{a, title={A}{B}}\n"))

# ══════════════════════════════════════════════════════════════════════════
# ② select_refs_attachment：优先真 BibTeX、Word 旧口径、坏候选跳过
# ══════════════════════════════════════════════════════════════════════════
_items = [
    SourceFile(path="/x/notes.txt", name="notes.txt", role="refs", score=0.9,
               fields={"_text": TEXT_OK, "refs_kind": "text"}),
    SourceFile(path="/x/refs.bib", name="refs.bib", role="refs", score=0.95,
               fields={"_text": BIB_OK, "refs_kind": "bib"}),
]
_pick, _adopt, _skip = multisource.select_refs_attachment(_items)
check("② 候选里同时有 BibTeX 与文本清单 → 选 BibTeX（真 .bib 优先）",
      _pick is not None and _pick.path == "/x/refs.bib" and not _skip,
      str(_adopt))
_pick_w, _adopt_w, _skip_w = multisource.select_refs_attachment(
    _items, allow_bibtex=False)
check("② Word 通道（allow_bibtex=False）：不把 BibTeX 当附件，退到文本清单",
      _pick_w is not None and _pick_w.path == "/x/notes.txt"
      and any("Word 通道" in s for s in _skip_w), str(_skip_w))
_bad_only = [
    SourceFile(path="/x/用于上传text2bib.txt", name="用于上传text2bib.txt",
               role="refs", score=0.9,
               fields={"_text": TEXT_HREF + TEXT_TWO_IN_LINE, "refs_kind": "text"}),
]
_pick_b, _adopt_b, _skip_b = multisource.select_refs_attachment(_bad_only)
check("② 只有坏文本候选 → 不采用，并写明跳过原因",
      _pick_b is None and _skip_b and "href" in _skip_b[0].lower(),
      str(_skip_b))
_bib_bad = [SourceFile(path="/x/BAD.BIB", name="BAD.BIB", role="refs", score=0.95,
                       fields={"_text": "@article{, title={X}}\n", "refs_kind": "bib"})]
_pick_b2, _a2, _skip_b2 = multisource.select_refs_attachment(_bib_bad)
check("② 坏 BibTeX 候选也不采用（空 key 不硬并）",
      _pick_b2 is None and _skip_b2, str(_skip_b2))

# scan 级：.bib 文件此前根本不在扫描范围，第 134 条起要能被认出来
_scan_dir = os.path.join(TMP, "scan")
os.makedirs(os.path.join(_scan_dir, "sub"))
with open(os.path.join(_scan_dir, "main.tex"), "w", encoding="utf-8") as f:
    f.write("\\documentclass{article}\n\\begin{document}\n"
            "这是正文段落。" * 40 + "\n\\end{document}\n")
with open(os.path.join(_scan_dir, "sub", "refs.bib"), "w", encoding="utf-8") as f:
    f.write(BIB_OK)
_inv = multisource.scan(_scan_dir)
_refs_inv = [s for s in _inv if s.role == "refs"]
check("② scan：.bib 文件被识别为参考文献（BibTeX 附件，非「未识别」）",
      len(_refs_inv) == 1 and _refs_inv[0].fields.get("refs_kind") == "bib"
      and "BibTeX" in _refs_inv[0].reason,
      str([(s.name, s.role, s.reason) for s in _refs_inv]))

# ══════════════════════════════════════════════════════════════════════════
# ③ convert：真 .bib 判定 + 合并辅助
# ══════════════════════════════════════════════════════════════════════════
_fixture = os.path.join(TMP, "helper")
os.makedirs(_fixture)
_tex = os.path.join(_fixture, "main.tex")
with open(_tex, "w", encoding="utf-8") as f:
    f.write("\\documentclass{article}\n\\addbibresource{reference.bib}\n"
            "\\begin{document}\nx\n\\end{document}\n")
with open(os.path.join(_fixture, "reference.bib"), "w", encoding="utf-8") as f:
    f.write(BIB_OK)
check("③ declared_bib_path：读 \\addbibresource 且文件存在",
      convert.declared_bib_path(_tex) == os.path.join(_fixture, "reference.bib"))
check("③ real_bib_path：LaTeX 有声明 → 真 bib；Word 源 → None（旧行为不变）",
      convert.real_bib_path(_tex) == os.path.join(_fixture, "reference.bib")
      and convert.real_bib_path(os.path.join(_fixture, "x.docx")) is None)
_explicit = os.path.join(_fixture, "explicit.bib")
with open(_explicit, "w", encoding="utf-8") as f:
    f.write(BIB_OK)
check("③ real_bib_path：--bib 显式优先",
      convert.real_bib_path(_tex, _explicit) == _explicit)
check("③ bib_entry_structurally_safe：正常条目过、`}{`/不配平不过",
      convert.bib_entry_structurally_safe("@misc{a, title={X}}")
      and not convert.bib_entry_structurally_safe("@misc{a, title={A}{B}}")
      and not convert.bib_entry_structurally_safe("@misc{a, title={X}"))
_ents = convert.bib_attachment_entries(BIB_OK)
check("③ bib_attachment_entries：类型/键/原文，顺序不变",
      [(e, k) for e, k, _f, _r in _ents] == [("article", "a1"), ("book", "a2")])
_merge_dir = os.path.join(TMP, "merge")
os.makedirs(_merge_dir)
_merge_bib = os.path.join(_merge_dir, "reference.bib")
with open(_merge_bib, "w", encoding="utf-8") as f:
    f.write("@misc{a1,\n  title = {已有}\n}\n")
_added, _dup, _bad = convert.merge_bib_attachment(
    _merge_bib, open(_merge_bib, encoding="utf-8").read(), BIB_OK)
_merged = open(_merge_bib, encoding="utf-8").read()
check("③ merge_bib_attachment：按键去重（a1 已存在）、a2 原文并入",
      _added == 1 and _dup == 1 and _bad == 0
      and "@book{a2," in _merged and _merged.count("@misc{a1,") == 1,
      f"added={_added} dup={_dup} bad={_bad}")
_bad_bib = "@misc{a3,\n  title = {坏条目}{嵌套}\n}\n"
_bad_path = os.path.join(_merge_dir, "bad.bib")
with open(_bad_path, "w", encoding="utf-8") as f:
    f.write("% 由参考文献附件生成\n\n")
_added2, _dup2, _bad2 = convert.merge_bib_attachment(_bad_path, "", _bad_bib)
check("③ merge_bib_attachment：结构不合格条目（嵌套花括号）不写进产物",
      _added2 == 0 and _bad2 == 1
      and "a3" not in open(_bad_path, encoding="utf-8").read())

# ══════════════════════════════════════════════════════════════════════════
# ④ convert.py 端到端（子进程；全部写在临时目录）
# ══════════════════════════════════════════════════════════════════════════
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _run_convert(srcdir, outdir):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(_ENGINE, "vendor")
    env["PYTHONUNBUFFERED"] = "1"
    p = subprocess.run(
        [sys.executable, os.path.join(_ENGINE, "convert.py"),
         "--source", os.path.join(srcdir, "main.tex"),
         "--out", outdir, "--name", "Test", "--lang", "zh", "--no-original"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=_ENGINE,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


_MAIN = ("\\documentclass{article}\n"
         "\\begin{document}\n\\section{测试}\n正文。\n\\end{document}\n")
_MAIN_DECL = ("\\documentclass{article}\n"
              "\\addbibresource{reference.bib}\n"
              "\\begin{document}\n\\section{测试}\n正文。\n\\end{document}\n")

# ④-1 有 reference.bib（真 bib）→ 不再自动并附件；两个候选附件被逐个跳过并写原因
_case1 = os.path.join(TMP, "case1")
_write(os.path.join(_case1, "main.tex"), _MAIN_DECL)
_write(os.path.join(_case1, "reference.bib"), BIB_OK)
_write(os.path.join(_case1, "预处理", "用于上传text2bib.txt"), TEXT_BAD_ATTACH)
_write(os.path.join(_case1, "原稿", "bib.txt"), TEXT_OK)
_rc, _log = _run_convert(_case1, os.path.join(TMP, "out1"))
_out1 = os.path.join(TMP, "out1", "Test", "reference.bib")
_tex1 = os.path.join(TMP, "out1", "Test", "Test.tex")
check("④-1 有真 .bib：convert 成功、日志写明「真 .bib 直接使用/不合并附件」",
      _rc == 0 and "真 .bib 直接使用" in _log, _log[-300:])
check("④-1 有真 .bib：坏候选与好候选都被跳过并写明原因",
      "跳过参考文献附件：预处理/用于上传text2bib.txt" in _log
      and "跳过参考文献附件：原稿/bib.txt" in _log)
check("④-1 有真 .bib：产物 reference.bib 没有「由参考文献附件合并」段",
      os.path.isfile(_out1)
      and "由参考文献附件合并" not in open(_out1, encoding="utf-8").read(),
      _out1)

# ④-2 无真 bib + 坏 text2bib + 正常文本附件 → 采用正常文本附件并合并
_case2 = os.path.join(TMP, "case2")
_write(os.path.join(_case2, "main.tex"), _MAIN)
_write(os.path.join(_case2, "预处理", "用于上传text2bib.txt"), TEXT_BAD_ATTACH)
_write(os.path.join(_case2, "原稿", "bib.txt"), TEXT_OK)
_rc2, _log2 = _run_convert(_case2, os.path.join(TMP, "out2"))
_out2dir = os.path.join(TMP, "out2", "Test")
_merged_bib = None
for _f in sorted(os.listdir(_out2dir)) if os.path.isdir(_out2dir) else []:
    if _f.endswith(".bib"):
        _merged_bib = os.path.join(_out2dir, _f)
check("④-2 无真 bib：自动采用正常文本附件、跳过坏候选（含原因）",
      _rc2 == 0 and "自动采用参考文献附件" in _log2
      and "跳过参考文献附件：预处理/用于上传text2bib.txt" in _log2, _log2[-400:])
check("④-2 无真 bib：文本附件条目真的并进了 bib（有「由参考文献附件合并」段）",
      _merged_bib is not None
      and "由参考文献附件合并" in open(_merged_bib, encoding="utf-8").read(),
      str(_merged_bib))

# ④-3 无真 bib + 只有坏文本附件 → 不硬并、日志写明跳过原因；convert 仍成功
_case3 = os.path.join(TMP, "case3")
_write(os.path.join(_case3, "main.tex"), _MAIN)
_write(os.path.join(_case3, "预处理", "用于上传text2bib.txt"), TEXT_BAD_ATTACH)
_rc3, _log3 = _run_convert(_case3, os.path.join(TMP, "out3"))
check("④-3 只有坏附件：不采用、日志写明跳过原因；convert 不失败",
      _rc3 == 0 and "跳过参考文献附件" in _log3
      and "href" in _log3.lower(), _log3[-400:])
_out3dir = os.path.join(TMP, "out3", "Test")
_out3_bibs = ([f for f in os.listdir(_out3dir) if f.endswith(".bib")]
              if os.path.isdir(_out3dir) else [])
check("④-3 只有坏附件：产物里没有「由参考文献附件合并」段",
      all("由参考文献附件合并"
          not in open(os.path.join(_out3dir, f), encoding="utf-8").read()
          for f in _out3_bibs), str(_out3_bibs))

# ④-4 无真 bib + 正常 BibTeX 文本附件（.txt 里有 @entry）→ 按原文直入
_case4 = os.path.join(TMP, "case4")
_write(os.path.join(_case4, "main.tex"), _MAIN)
_write(os.path.join(_case4, "附件", "refs.txt"), BIB_OK)
_rc4, _log4 = _run_convert(_case4, os.path.join(TMP, "out4"))
_out4dir = os.path.join(TMP, "out4", "Test")
_bib4 = None
for _f in sorted(os.listdir(_out4dir)) if os.path.isdir(_out4dir) else []:
    if _f.endswith(".bib"):
        _bib4 = os.path.join(_out4dir, _f)
_txt4 = open(_bib4, encoding="utf-8").read() if _bib4 else ""
check("④-4 无真 bib：BibTeX 文本附件被采用（而非当纯文本清单瞎拆）",
      _rc4 == 0 and "BibTeX 附件" in _log4 and "从附件合并 2 条（BibTeX 直入）" in _log4,
      _log4[-400:])
check("④-4 BibTeX 附件：条目按键原文并入（a1/a2 都在）",
      "@article{a1," in _txt4 and "@book{a2," in _txt4 and "由参考文献附件合并" in _txt4)

shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
