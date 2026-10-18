#!/usr/bin/env python3.14
r"""Word 通道「英文元数据第二源」判据测试（**第 76 条**，2026-10-15 用户实测）。

缺陷
----
正文 docx 里只有中文时，英文题名/摘要/作者其实就在**同一个 `original/` 目录**的
另一份「英文摘要」docx 里，`parse_docx()` 一直读得出来（实测 10 Bo_Chen：

    title_en='A Survey of Contemporary Philosophy of Logic'、author_en='Chen Bo'、
    abstract_en 1529 字符、keywords_en='contemporary philosophy of logic; …'

），但 `convert.py` 的 Word 分支只把 `source` 交给 `build_docx()`、`metadata_path`
只喂给 LaTeX 分支 → Word 通道永远拿不到第二源，编辑只能在界面「正文信息覆盖」里
手工粘贴。`--scan` 还把那份文件报成【未识别】「2227 字符，特征不明显」。

五组断言
--------
  ① 判据：`sources.en_metadata_sheet()` —— 英文元数据表命中；正文 docx、纯中文稿、
     缩写/汉语摘要、**另一篇英文全文稿**（有叙述正文）都不命中。
  ② **反例 1（最重要）**：正文 docx **自带**英文块（案例 52 的文末英文块，真实稿件
     `07 Guangwu_Feng`）→ 第二源存在也不许改它的 `title_en`/`abstract_en`，
     产物与不接第二源**逐字节相同**。
  ③ 反例 2：同目录**没有**英文摘要文件 → 结果与改动前逐字节相同（占位符照旧），
     且**绝不跨目录**去仓库原目录里找那份真实存在的姊妹文件。
  ④ 反例 3：同目录有同名但不是英文摘要的文件（真实语料 `04 YingJin_Xu` 的
     `…汉语摘要.docx`，以及现造的纯中文 docx）→ 不许把它的中文摘要当英文摘要塞进去。
  ⑤ 正例：10 Bo_Chen 那对文件 → `title_en`/`abstract_en`/`author_en` 正确补上，
     且与那份英文摘要 docx 的 `parse_docx()` 取值**逐字相同**；`affiliation_en`
     两份都没有 → 照旧走占位符（如实记录）。
  ⑥ 反例 4：LaTeX 通道行为不变（`build_latex` 的 metadata 语义不许被动到）。
  ⑦ 角色：`sources.identify()` 给英文元数据表 `en_abstract` 角色，
     `convert.mark_en_abstract_roles()`（`--scan` 走的 multisource 清单）同样改判，
     输出不再有【未识别】；而 `_docx_score()` 的扣分保住 → 不会被选成正文档。

只读原稿；产物一律进临时目录；不联网、不跑 corpus.py / build.sh。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_en_abstract_sibling.py
"""
import glob
import inspect
import os
import shutil
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
# 第四十四批③c（O11）：真实稿一律用引擎自己的**稿件库锚点**解析（与其它套件一致）——
# `slcn.paths.manuscripts_root()`：SLCN_MANUSCRIPTS → 向上找 → 同级目录（三仓并排默认命中）。
# 从前这里用 `_REPO`（工具箱仓的上一级）拼 `2026年第5期(90)/…`，实际稿在
# `<GitHub>/Studies_in_Logic_Database/…` → 9 条真实语料断言全 SKIP。CI/公开仓没有
# 稿件库时 `manuscripts_root()` 返回 None → 真实语料断言仍**优雅 SKIP**（与以前一致）。
# 注：真正的赋值在 import 之后（此时 `_paths` 还不可用）。
_MANUSCRIPTS = None
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build, multisource, sources  # noqa: E402
from slcn import paths as _paths  # noqa: E402
from slcn.docxsource import parse_docx  # noqa: E402
from slcn.latex import parse_macros  # noqa: E402

import convert  # noqa: E402

# 稿件库锚点（import 后再取；无稿件库 = None → 真实语料断言 SKIP）
_MANUSCRIPTS = _paths.manuscripts_root()

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def _corpus_dir(rel_dir: str) -> str:
    """稿件库锚点下的相对目录；没有稿件库返回 ""（调用方据此 SKIP）。"""
    return os.path.join(_MANUSCRIPTS, rel_dir) if _MANUSCRIPTS else ""


def find(rel_dir, needle):
    """在语料目录下按文件名片段找稿子；跳过 Word 的 `~$` 锁文件/坏 zip。"""
    root = _corpus_dir(rel_dir)
    if not root:
        return ""
    hits = [x for x in glob.glob(os.path.join(root, "**", "*.docx"), recursive=True)
            if needle in os.path.basename(x) and zipfile.is_zipfile(x)]
    return hits[0] if hits else ""


def one(rel_dir, needle, ext="*.docx"):
    root = _corpus_dir(rel_dir)
    if not root:
        return ""
    hits = [x for x in glob.glob(os.path.join(root, "**", ext), recursive=True)
            if needle in os.path.basename(x)]
    return hits[0] if hits else ""


def macro_value(tex, name):
    m = parse_macros(tex).get(name)
    return None if m is None else m.value


def build_tex(src, out_dir, *, metadata_path=None, patch_doc=None, lang="zh"):
    """跑一次 `build_docx()`，返回 (`BuildResult`, `.tex` 文本)。"""
    from slcn import docxsource

    real_parse = docxsource.parse_docx
    if patch_doc is not None:
        def fake(path, **kw):
            doc = real_parse(path, **kw)
            patch_doc(doc)
            return doc
        docxsource.parse_docx = fake
    try:
        res = build.build_docx(docx_path=src, out_dir=out_dir, basename="Case",
                               lang=lang, metadata_path=metadata_path, verbose=False)
        return res, open(res.tex_path, encoding="utf-8").read()
    finally:
        docxsource.parse_docx = real_parse


def _build_legacy(src, out_dir):
    """**不传** `metadata_path`（第 76 条之前 `corpus.py` 等既有调用方的调法）。"""
    res = build.build_docx(docx_path=src, out_dir=out_dir, basename="Case",
                           lang="zh", verbose=False)
    return res, open(res.tex_path, encoding="utf-8").read()


# 现造最小 docx（与 test_tail_block.py 同一写法：只含 word/document.xml）
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(paras):
    ps = "".join(f"<w:p><w:r><w:t>{escape(t)}</w:t></w:r></w:p>" for t in paras)
    xml = f'<w:document xmlns:w="{_W_NS}"><w:body>{ps}</w:body></w:document>'
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return path


# ── 真实语料 ─────────────────────────────────────────────────────────────
BC_BODY = find("2026年第5期(90)/10 Bo_Chen/original", "陈波 当代逻辑哲学鸟瞰")
BC_EN = find("2026年第5期(90)/10 Bo_Chen/original", "英文摘要")
GF_BODY = find("2026年第2期(87)/07 Guangwu_Feng/original", "格赖斯和布兰顿")
GD_BODY = find("2024年第4期(77)/05 Guoping_Du/original", "排中律-LJXYJ")
YX_CN = find("2025年第6期(85)/04 YingJin_Xu/original", "汉语摘要")
LZ_DIR = _corpus_dir("2026年第5期(90)/03 Liang_Zhang/original")
LZ_META = one("2026年第5期(90)/03 Liang_Zhang/original", "yalcin puzzle", "*.tex")
LZ_BODY = one("2026年第5期(90)/03 Liang_Zhang/original", "main", "*.tex")

_TMP = []


def tmp_docx(paras):
    p = make_docx(paras)
    _TMP.append(p)
    return p


# ══ ① 判据：什么算「英文元数据表」════════════════════════════════════════
print("── ① 判据 `sources.en_metadata_sheet()` ──")

_CN_ONLY = ["论数据库逻辑", "张三", "摘  要：本文讨论数据库逻辑。", "关键词：逻辑；数据库"]
_EN_SHEET = ["Towards a Logic of Databases", "Zhang San", "Abstract",
             "We study the logic of databases.", "Key words: logic; database"]
_EN_FULL = (_EN_SHEET[:1] + ["Zhang San", "Abstract: We study the logic of databases.",
                             "Key words: logic; database"]
            + ["Body paragraph %d. " % i + "x" * 90 for i in range(40)])

p_cn = tmp_docx(_CN_ONLY)
p_sheet = tmp_docx(_EN_SHEET)
p_full = tmp_docx(_EN_FULL)
check("① 纯中文元数据 docx（有摘要/关键词、没英文）→ 不命中",
      sources.en_metadata_sheet(p_cn) == {}, f"{sources.en_metadata_sheet(p_cn)}")
check("① 纯英文元数据表（题名/作者/Abstract/Keywords、无叙述正文）→ 命中",
      sorted(sources.en_metadata_sheet(p_sheet))
      == ["abstract_en", "author_en", "keywords_en", "title_en"],
      f"{sorted(sources.en_metadata_sheet(p_sheet))}")
check("① 英文摘要 + 40 段叙述正文（= 同目录的另一篇英文全文稿）→ **不命中**"
      "（否则会把另一篇文章的英文元数据塞进本篇）",
      sources.en_metadata_sheet(p_full) == {}, f"{sources.en_metadata_sheet(p_full)}")

if BC_EN:
    _en_real = parse_docx(BC_EN)
    _sheet_real = sources.en_metadata_sheet(BC_EN)
    check("① 真实英文摘要 docx 命中，且 title_en/abstract_en 与 parse_docx 逐字相同",
          _sheet_real.get("title_en") == _en_real.title_en
          and _sheet_real.get("abstract_en") == _en_real.abstract_en
          and _sheet_real.get("author_en") == "Chen Bo",
          f"{_sheet_real.get('title_en')!r}")
    check("① 真实正文 docx（有叙述正文、无英文块）→ 不命中",
          sources.en_metadata_sheet(BC_BODY) == {}, "")
else:
    skip("① 真实英文摘要 docx", "缺真实语料 10 Bo_Chen")

if GF_BODY:
    check("① 自带文末英文块的真实正文稿（07 Guangwu_Feng）→ 不命中"
          "（它是正文，不是元数据表）",
          sources.en_metadata_sheet(GF_BODY) == {}, "")
else:
    skip("① Guangwu_Feng 正文判据", "缺真实语料")

if YX_CN:
    check("① 真实「汉语摘要.docx」（纯中文）→ 不命中",
          sources.en_metadata_sheet(YX_CN) == {}, "")
else:
    skip("① YingJin_Xu 汉语摘要判据", "缺真实语料")


# ══ ② 反例 1（最重要）：正文自带英文块 → 第二源一个字都不许改 ═══════════════
print("\n── ② 反例 1：正文自带英文块（案例 52）→ 第二源不许改 ──")

if not (GF_BODY and GD_BODY and BC_EN):
    skip("② 反例 1", "缺真实语料（Guangwu_Feng / Guoping_Du 正文 / Bo_Chen 英文摘要）")
else:
    # ②-a 真实样本：正文稿自带**完整**英文块（05 Guoping_Du 文末英文块）→
    #      同目录放一份**取值完全不同**的真实英文摘要（Bo_Chen 那份）也不许改一个字。
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "orig")
        os.makedirs(d)
        body = os.path.join(d, os.path.basename(GD_BODY))
        en = os.path.join(d, os.path.basename(BC_EN))
        shutil.copy2(GD_BODY, body)
        shutil.copy2(BC_EN, en)

        gd = parse_docx(body)
        en_doc = parse_docx(en)
        check("② 前提：真实正文稿自带完整英文块（题名/摘要/作者/单位/关键词）",
              all((getattr(gd, f) or "").strip() for f in
                  ("title_en", "abstract_en", "author_en", "affiliation_en", "keywords_en")),
              f"{gd.title_en!r} / {gd.author_en!r}")
        check("② 前提：第二源的英文题名与正文稿**不同**（它本来会改掉正文的值）",
              en_doc.title_en.strip() != gd.title_en.strip(),
              f"{en_doc.title_en!r} vs {gd.title_en!r}")

        _res0, tex0 = build_tex(body, os.path.join(tmp, "no2"))
        res1, tex1 = build_tex(body, os.path.join(tmp, "with2"), metadata_path=en)
        check("② 接了第二源，产物与不接第二源**逐字节相同**", tex0 == tex1, "")
        check("② `\\mytitleEN` 仍是正文稿自己的英文题名",
              macro_value(tex1, "mytitleEN") == gd.title_en, f"{macro_value(tex1, 'mytitleEN')!r}")
        check("② `\\myabstractEN` 仍是正文稿自己的英文摘要",
              macro_value(tex1, "myabstractEN") == gd.abstract_en, "")
        check("② `\\myfirstauthorEN` / `\\myfirstaffiliationEN` 仍是正文稿自己的值",
              macro_value(tex1, "myfirstauthorEN") == gd.author_en
              and macro_value(tex1, "myfirstaffiliationEN") == gd.affiliation_en, "")
        check("② 第二源的英文题名/摘要**都没有**出现在产物里",
              en_doc.title_en not in tex1 and en_doc.abstract_en not in tex1, "")
        check("② 日志说「正文自带的英文块优先，第二源未改动任何字段」，且没有「补入」",
              any("正文自带的英文块优先" in s for s in res1.log)
              and "英文元数据补入" not in " ".join(res1.log), f"{res1.log[1:4]}")

    # ②-b 补空（不是覆盖）：正文稿英文块**缺** author_en 时，缺的那个才补。
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "orig")
        os.makedirs(d)
        body = os.path.join(d, os.path.basename(GF_BODY))
        en = os.path.join(d, "英文摘要.docx")
        shutil.copy2(GF_BODY, body)
        shutil.copy2(tmp_docx(["A Completely Different English Title", "Someone Else",
                               "Abstract: A completely different abstract.",
                               "Key words: different; keywords"]), en)
        gf = parse_docx(body)
        en_doc = parse_docx(en)
        check("②-b 前提：正文稿英文块**没有** author_en（它是空的）",
              bool(gf.title_en.strip()) and not gf.author_en.strip(), f"{gf.author_en!r}")
        _res2, tex2 = build_tex(body, os.path.join(tmp, "out"), metadata_path=en)
        check("②-b 正文已有的 `\\mytitleEN` / `\\myabstractEN` 一个字都没动",
              macro_value(tex2, "mytitleEN") == gf.title_en
              and macro_value(tex2, "myabstractEN") == gf.abstract_en, "")
        check("②-b 正文**没有**的 `\\myfirstauthorEN`（原为占位符）才补上第二源的值",
              macro_value(tex2, "myfirstauthorEN") == en_doc.author_en == "Someone Else",
              f"{macro_value(tex2, 'myfirstauthorEN')!r}")


# ══ ③ 反例 2：同目录没有英文摘要 → 逐字节相同 + 绝不跨目录 ══════════════════
print("\n── ③ 反例 2：同目录没有英文摘要 → 逐字节相同、不跨目录 ──")

if not BC_BODY:
    skip("③ 反例 2", "缺真实语料 10 Bo_Chen 正文")
else:
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "孤稿")          # 只放正文 docx，**不放**英文摘要
        os.makedirs(d)
        body = os.path.join(d, os.path.basename(BC_BODY))
        shutil.copy2(BC_BODY, body)

        found = convert.find_en_metadata(body, None)
        check("③ 同目录找不到英文摘要 → 返回 None（照旧写占位符）", found is None, f"{found}")
        check("③ **绝不跨目录**：仓库原目录里那份真实姊妹文件没有被找到",
              found is None and os.path.isfile(BC_EN), "")

        res_a, tex_a = build_tex(body, os.path.join(tmp, "a"), metadata_path=None)
        res_b, tex_b = build_tex(body, os.path.join(tmp, "b"), metadata_path=found)
        # 「改动前的调用方式」= **不传这个新参数**（corpus.py / run_poc.py 就是这么调的）
        res_old, tex_old = _build_legacy(body, os.path.join(tmp, "old"))
        check("③ `metadata_path=None` 与接第二源的结果逐字节相同", tex_a == tex_b, "")
        check("③ 与**不传新参数**的老调用方式产物逐字节相同"
              "（`corpus.py` 等既有调用方不受影响）", tex_a == tex_old, "")
        check("③ 两条路径的运行日志里都没有「第二源」/「补入」（第二源根本没介入）",
              not any("第二源" in s or "补入" in s for s in res_a.log + res_old.log), "")
        check("③ 英文题名/摘要/英文作者仍旧是占位符（照旧，没被第二源碰到）",
              macro_value(tex_a, "mytitleEN") == "【未识别：英文题名，请手工填写】"
              and macro_value(tex_a, "myabstractEN") == "【未识别：英文摘要，请手工填写】"
              and macro_value(tex_a, "myfirstauthorEN") == "【未识别：第一作者英文姓名，请手工填写】",
              "")
        check("③ 产物里【未识别 占位符处数与老调用方式**相同**（解析器改版也不受影响）",
              tex_a.count("【未识别") == tex_old.count("【未识别") > 0,
              f"{tex_a.count('【未识别')} vs {tex_old.count('【未识别')}")
        # 现造一份**没有**任何元数据的陌生 docx 放同目录 → 也不许当第二源
        shutil.copy2(BC_EN, os.path.join(d, "无关文件.bak"))
        check("③ 同目录只有非 .docx 的无关文件 → 仍旧找不到", convert.find_en_metadata(body, None) is None, "")


# ══ ④ 反例 3：同目录有「同名但不是英文摘要」的文件 → 不许拿中文冒充 ═════════
print("\n── ④ 反例 3：同目录的中文/非英文摘要文件不许当中文当英文 ──")

if not BC_BODY:
    skip("④ 反例 3", "缺真实语料 10 Bo_Chen 正文")
else:
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "orig")
        os.makedirs(d)
        body = os.path.join(d, os.path.basename(BC_BODY))
        shutil.copy2(BC_BODY, body)
        # 现造一份「同名但不是英文摘要」的纯中文 docx（有摘要/关键词，没英文）
        cn_sheet = os.path.join(d, os.path.basename(BC_BODY).replace(".docx", "-英文摘要.docx"))
        make_docx_path = tmp_docx(_CN_ONLY)
        shutil.copy2(make_docx_path, cn_sheet)

        check("④ 同目录的纯中文「英文摘要.docx」不被发现（返回 None）",
              convert.find_en_metadata(body, None) is None,
              f"{convert.find_en_metadata(body, None)}")

        res, tex = build_tex(body, os.path.join(tmp, "out"), metadata_path=cn_sheet)
        check("④ 显式传它当第二源 → `\\myabstractEN` 仍是占位符",
              macro_value(tex, "myabstractEN") == "【未识别：英文摘要，请手工填写】",
              f"{macro_value(tex, 'myabstractEN')!r}")
        check("④ 它的中文摘要**没有**被当成英文摘要塞进产物",
              "本文讨论数据库逻辑" not in tex, "")
        check("④ 运行日志如实说明「没有可用的英文字段，本次忽略」",
              any("没有可用的英文字段" in w for w in res.warnings), f"{res.warnings[:2]}")

        # 真实语料：04 YingJin_Xu 的「汉语摘要.docx」
        if YX_CN:
            shutil.copy2(YX_CN, os.path.join(d, os.path.basename(YX_CN)))
            check("④ 真实「汉语摘要.docx」放同目录也不被发现",
                  convert.find_en_metadata(body, None) is None, "")
            res2, tex2 = build_tex(body, os.path.join(tmp, "out2"),
                                   metadata_path=os.path.join(d, os.path.basename(YX_CN)))
            _yx = parse_docx(YX_CN)
            check("④ 真实汉语摘要 docx：英文摘要仍是占位符，且它的中文摘要没进英文槽位",
                  macro_value(tex2, "myabstractEN") == "【未识别：英文摘要，请手工填写】"
                  and (not (_yx.abstract_cn or "").strip() or (_yx.abstract_cn or "") not in tex2),
                  f"abstract_cn={(_yx.abstract_cn or '')[:20]!r}")
        else:
            skip("④ 真实汉语摘要 docx", "缺真实语料 04 YingJin_Xu")

        # 显式指到不存在 / 非 .docx 的路径 → 记 warning，不崩、不改字段
        res3, tex3 = build_tex(body, os.path.join(tmp, "out3"),
                               metadata_path=os.path.join(tmp, "没有这个文件.docx"))
        check("④ 第二源不存在 → 记 warning 且字段照旧是占位符",
              any("第二源不存在" in w for w in res3.warnings)
              and macro_value(tex3, "myabstractEN") == "【未识别：英文摘要，请手工填写】",
              f"{res3.warnings[:1]}")
        fake_tex = os.path.join(tmp, "meta.tex")
        with open(fake_tex, "w", encoding="utf-8") as f:
            f.write("\\newcommand{\\mytitle}{不该被 Word 通道读}\\n")
        res4, tex4 = build_tex(body, os.path.join(tmp, "out4"), metadata_path=fake_tex)
        check("④ 第二源不是 .docx（.tex）→ 记 warning、字段照旧",
              any("不是 .docx" in w for w in res4.warnings)
              and macro_value(tex4, "myabstractEN") == "【未识别：英文摘要，请手工填写】",
              f"{res4.warnings[:1]}")


# ══ ⑤ 正例：10 Bo_Chen 那对文件 ═══════════════════════════════════════════
print("\n── ⑤ 正例：10 Bo_Chen 正文 + 英文摘要 → 英文题名/摘要/作者补上 ──")

if not (BC_BODY and BC_EN):
    skip("⑤ 正例", "缺真实语料 10 Bo_Chen")
else:
    en_doc = parse_docx(BC_EN)
    check("⑤ 同目录自动发现：`find_en_metadata()` 找到那份英文摘要",
          convert.find_en_metadata(BC_BODY, None) == os.path.abspath(BC_EN),
          f"{convert.find_en_metadata(BC_BODY, None)}")
    with tempfile.TemporaryDirectory() as tmp:
        res, tex = build_tex(BC_BODY, os.path.join(tmp, "pos"), metadata_path=BC_EN)
        check("⑤ `\\mytitleEN` 与英文摘要 docx 的 `title_en` **逐字相同**",
              macro_value(tex, "mytitleEN") == en_doc.title_en, f"{macro_value(tex, 'mytitleEN')!r}")
        check("⑤ `\\myabstractEN` 与英文摘要 docx 的 `abstract_en` **逐字相同**",
              macro_value(tex, "myabstractEN") == en_doc.abstract_en,
              f"len={len(macro_value(tex, 'myabstractEN') or '')}")
        check("⑤ `\\myfirstauthorEN` 补成 `Chen Bo`",
              macro_value(tex, "myfirstauthorEN") == en_doc.author_en == "Chen Bo", "")
        check("⑤ 未识别清单被第二源**清掉**了英文题名/摘要/作者三项"
              "（补不了的项仍是占位符：两份文件都没有 affiliation_en）",
              "英文题名" not in " ".join(res.warnings)
              and "英文摘要" not in " ".join(res.warnings)
              and "第一作者英文姓名" not in " ".join(res.warnings)
              and "第一作者英文单位" in " ".join(res.warnings), "")
        check("⑤ `\\myfirstaffiliationEN` 照旧写占位符（两份文件都没有 affiliation_en）",
              macro_value(tex, "myfirstaffiliationEN") == "【未识别：第一作者英文单位，请手工填写】"
              and not en_doc.affiliation_en.strip(), "")
        check("⑤ 运行日志写明「第二源是哪个文件、补了哪几项」",
              any(os.path.basename(BC_EN) in s and "第二源" in s for s in res.log)
              and any("英文元数据补入 4 项" in s for s in res.log), "")

        # keywords_en：中文关键词存在时仍旧中文优先（既有优先级不变）；
        # 把中文关键词清空后，`\mykeywords` 才用第二源的英文关键词。
        _res2, tex2 = build_tex(BC_BODY, os.path.join(tmp, "kw"),
                                metadata_path=BC_EN,
                                patch_doc=lambda doc: setattr(doc, "keywords_cn", ""))
        check("⑤ 中文关键词存在时 `\\mykeywords` 仍是中文（既有优先级不变）",
              macro_value(tex, "mykeywords") == parse_docx(BC_BODY).keywords_cn, "")
        check("⑤ 中文关键词为空时，`\\mykeywords` 用第二源的 `keywords_en`（确实被补入了）",
              macro_value(tex2, "mykeywords") == en_doc.keywords_en,
              f"{macro_value(tex2, 'mykeywords')!r}")


# ══ ⑥ 反例 4：LaTeX 通道行为不变 ══════════════════════════════════════════
print("\n── ⑥ 反例 4：LaTeX 通道（build_latex 的 metadata 语义）不变 ──")

# 第四十三批 ZH-08 日志提示给 `build_latex` **尾部**新增了 keyword-only 参数
# `dash_hints: bool = True` —— 属 INVARIANTS §六.1「只增不改」的**合法扩展**：
# 既有参数的位置/顺序/默认值逐字不变，新参数一律 keyword-only 且有默认值
# （老调用方不传也能跑；案例 156）。所以本断言从「整张形参表逐字未动」改为
# 「**前缀**逐字未动 + 尾部只允许新增 keyword-only 带默认值的参数」。
_LATEX_BASE_PARAMS = ["body_path", "out_dir", "metadata_path", "bib_path", "basename",
                      "lang", "sty", "skeleton", "meta", "overrides", "verbose"]
_LATEX_BASE_DEFAULTS = {  # 既有参数的默认值必须逐字保留（INVARIANTS §六.1）
    "body_path": inspect.Parameter.empty, "out_dir": inspect.Parameter.empty,
    "metadata_path": None, "bib_path": None, "basename": "output", "lang": "zh",
    "sty": None, "skeleton": None, "meta": None, "overrides": None, "verbose": True,
}
_sig_latex_all = inspect.signature(build.build_latex).parameters
_sig_docx = inspect.signature(build.build_docx).parameters
_sig_latex = list(_sig_latex_all)
check("⑥ `build_latex` 形参表**前缀**逐字未动（既有 11 个参数顺序/默认不变；"
      "第四十三批仅尾部新增 keyword-only `dash_hints=True`，属只增不改，案例 156）",
      _sig_latex[:len(_LATEX_BASE_PARAMS)] == _LATEX_BASE_PARAMS
      and all(_sig_latex_all[n].default == d for n, d in _LATEX_BASE_DEFAULTS.items()),
      f"{_sig_latex}")
_extra_latex = _sig_latex[len(_LATEX_BASE_PARAMS):]
check("⑥ 守卫：`build_latex` 的新增参数必须 **keyword-only 且有默认值**"
      "（防止将来加必填参数破坏老调用方；INVARIANTS §六.1；案例 156）",
      all(_sig_latex_all[n].kind is inspect.Parameter.KEYWORD_ONLY
          and _sig_latex_all[n].default is not inspect.Parameter.empty
          for n in _extra_latex),
      f"新增={_extra_latex} 详情="
      f"{ {n: (_sig_latex_all[n].kind.name, _sig_latex_all[n].default) for n in _extra_latex} }")
check("⑥ 新参数只加在 `build_docx` 上，且**带默认值 None**（老调用方行为不变）",
      "metadata_path" in _sig_docx
      and _sig_docx["metadata_path"].default is None
      and _sig_docx["metadata_path"].kind is inspect.Parameter.KEYWORD_ONLY,
      f"{_sig_docx['metadata_path'].default!r}")

if not (os.path.isfile(LZ_BODY) and os.path.isfile(LZ_META)):
    skip("⑥ build_latex 行为", "缺真实语料 03 Liang_Zhang")
else:
    with tempfile.TemporaryDirectory() as tmp:
        res = build.build_latex(LZ_BODY, os.path.join(tmp, "lz"),
                                metadata_path=LZ_META, bib_path=None,
                                basename="Case", lang="zh", verbose=False)
        tex = open(res.tex_path, encoding="utf-8").read()
        meta_tex = open(LZ_META, encoding="utf-8", errors="replace").read()
        _mv, _ = build.clean_field_value("mytitle", parse_macros(meta_tex)["mytitle"].value)
        check("⑥ LaTeX 元数据源照旧被搬运、照旧走同一套字段清理（`\\mytitle` 来自旧模板稿）",
              macro_value(tex, "mytitle") == _mv, f"{macro_value(tex, 'mytitle')!r}")
        # 真正跑一遍：metadata_path = Word 英文摘要 docx
        if BC_EN:
            res2 = build.build_latex(LZ_BODY, os.path.join(tmp, "lz2"),
                                     metadata_path=BC_EN, bib_path=None,
                                     basename="Case", lang="zh", verbose=False)
            tex2 = open(res2.tex_path, encoding="utf-8").read()
            check("⑥ LaTeX 通道不认 Word 第二源：英文题名不是那份 docx 的 `title_en`",
                  macro_value(tex2, "mytitle") != parse_docx(BC_EN).title_en
                  and "A Survey of Contemporary Philosophy of Logic" not in tex2, "")
            check("⑥ LaTeX 通道的元数据源就是它给的那一个（没有偷偷去同目录找 .tex）",
                  any("元数据源" in s for s in res2.log), "")
        else:
            skip("⑥ build_latex + Word 文件", "缺真实语料 10 Bo_Chen 英文摘要")


# ══ ⑦ 角色：identify() 与 --scan 的清单都不再写【未识别】 ═════════════════
print("\n── ⑦ 角色：en_abstract（identify / --scan）+ 扣分逻辑保住 ──")

if not (BC_BODY and BC_EN):
    skip("⑦ 角色", "缺真实语料 10 Bo_Chen")
else:
    art_dir = os.path.dirname(os.path.dirname(BC_BODY))          # …/10 Bo_Chen
    ss = sources.identify(art_dir)
    ear = [c for c in ss.candidates if c.role == "en_abstract"]
    check("⑦ `identify()` 给英文摘要 docx 明确角色 `en_abstract`",
          len(ear) == 1 and os.path.abspath(ear[0].path) == os.path.abspath(BC_EN),
          f"{[(c.role, os.path.basename(c.path)) for c in ss.candidates]}")
    check("⑦ `identify()` 仍然把正文 docx 选成正文/Word 源（第二源没被顶上来）",
          ss.docx and os.path.abspath(ss.docx) == os.path.abspath(BC_BODY),
          f"{ss.docx}")
    check("⑦ 扣分逻辑保住：`sources._docx_score(英文摘要) < 0`（不会被当成正文档）",
          sources._docx_score(BC_EN)[0] < 0, f"{sources._docx_score(BC_EN)[0]:.0f}")
    check("⑦ 它在 `pick_main_docx()` 里评不上正文档（单独一份也不认）",
          sources.pick_main_docx([BC_EN])[0] == "", f"{sources.pick_main_docx([BC_EN])}")

    inv = multisource.scan(os.path.dirname(BC_BODY))
    before = [s.role for s in inv if os.path.abspath(s.path) == os.path.abspath(BC_EN)]
    n = convert.mark_en_abstract_roles(inv)
    after = [s.role for s in inv if os.path.abspath(s.path) == os.path.abspath(BC_EN)]
    txt = multisource.format_inventory(inv, "源稿清单")
    check("⑦ `multisource.scan()` 原本把它判成 unknown（这就是【未识别】的来源）",
          before == ["unknown"], f"{before}")
    check("⑦ `convert.mark_en_abstract_roles()` 把它改判为 en_abstract（改判 1 个）",
          n == 1 and after == ["en_abstract"], f"n={n} after={after}")
    check("⑦ `--scan` 的清单文本里不再有【未识别】，且出现明确角色（中文名 `英文元数据`）",
          "【未识别" not in txt and "[英文元数据" in txt,
          " / ".join(txt.strip().splitlines()[-2:]))
    check("⑦ 正文档那一项的角色没被动（仍是 main）",
          any(s.role == "main" and os.path.abspath(s.path) == os.path.abspath(BC_BODY)
              for s in inv), "")

for _p in _TMP:
    try:
        os.unlink(_p)
    except OSError:
        pass

# ══════════════════════════════════════════════════════════════════════════
# ⑬ 第四十五批 A：连写英文作者串按作者数等分（90-01 实测）
#    源稿英文作者是 `Jingru Kou Yetao Liu`（无 and/&/;），旧逻辑整串进不了第二作者
#    槽位 → 模板印占位符；人工成品是 `\mysecondauthorEN{Yetao Liu}`。
# ══════════════════════════════════════════════════════════════════════════
from slcn.build import _split_en_names_by_count  # noqa: E402

check("⑬ 2 个作者、4 个词（每人 2 词）→ 逐字定位拆分",
      _split_en_names_by_count("Jingru Kou Yetao Liu", 2) == ["Jingru Kou", "Yetao Liu"],
      repr(_split_en_names_by_count("Jingru Kou Yetao Liu", 2)))
check("⑬ 反例：词数不匹配作者数 → 不猜（返回空）",
      _split_en_names_by_count("Jingru Kou Yetao Liu", 3) == []
      and _split_en_names_by_count("Lu Jiang", 2) == [])
check("⑬ 反例：含小写/虚词 → 不猜（保守判据）",
      _split_en_names_by_count("Jingru Kou and Yetao Liu", 2) == [])

# ══════════════════════════════════════════════════════════════════════════
# ⑭ 第四十五批 A：正文环境宏包写在本地 .sty 时补迁移（86-05 algorithm 实测）
# ══════════════════════════════════════════════════════════════════════════
import tempfile as _tf  # noqa: E402

from slcn import preamble as _PRE  # noqa: E402

_sty_dir = _tf.mkdtemp(dir="/tmp")
open(os.path.join(_sty_dir, "SLCN.sty"), "w", encoding="utf-8").write(
    "\\usepackage{algorithm}\n\\usepackage{algpseudocode}\n")
_main = "\\documentclass{article}\n\\usepackage{SLCN}\n\\begin{document}\nX\n\\end{document}\n"
_body = ("\\begin{algorithm}[H]\n\\caption{测试}\n\\begin{algorithmic}[1]\n"
         "\\State X\n\\end{algorithmic}\n\\end{algorithm}\n")
_lines_env = _PRE.env_package_lines(_main, _sty_dir, _body)
check("⑭ 正文用 algorithm/algorithmic、包行在本地 .sty → 找到 algorithm+algpseudocode",
      "\\usepackage{algorithm}" in _lines_env
      and "\\usepackage{algpseudocode}" in _lines_env, repr(_lines_env))
check("⑭ 反例：源稿没写过该包行 → 返回空（不无脑加包）",
      _PRE.env_package_lines(_main, _sty_dir, "正文没有任何算法环境") == [])

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
