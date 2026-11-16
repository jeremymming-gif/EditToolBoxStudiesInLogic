#!/usr/bin/env python3.14
r"""`slcn/build.py` 的**第一作者英文槽位**判据（案例 72，2026-10-15 用户实测）。

缺陷
----
`REQUIRED_FIELDS["zh"]` 列了 `mytitleEN` / `myabstractEN`，却**没有**
`myfirstauthorEN` / `myfirstaffiliationEN`。占位符循环只遍历
`REQUIRED_FIELDS + coauthor_fields()`，于是这两个槽位解析失败时**既不写占位符、
也不报「未识别」**，CN 模板 1.5 节的示例文字 `First Author` / `Affiliation 1`
被静默搬进产物、印到 PDF 英文摘要页（实测 2026_05 Lei_Li 第 20 页）。
对照：第二作者的英文槽位在 `coauthor_fields()` 里，所以第二作者是正确的占位符。

四组断言
--------
  ① 模板默认值判据（本缺陷能发生的前提）：`template_defaults()` 里
     `myfirstauthorEN` 的原值就是 `First Author` → `is_parsed()` 判**假**；
     空串 / `null` / None 也判假；真值判真。
  ② 正例：源稿**没有**英文作者 → 写 `【未识别：…】` 而**不是** `First Author`。
  ③ 反例 1：源稿**有**英文作者（真实稿件 `当代逻辑论题鸟瞰英文摘要.docx` 解析出
     `author_en == 'Li Lei'`）→ **不许**写占位符。
  ④ 反例 2：手工覆盖（`--title-en/--abstract-en/--affiliation-cn/--email`）的字段
     不许被占位符覆盖（覆盖优先级见 `build.py` 的 `override_fields()`）。

另外：新增的两个键**只**加到 `zh` 那张表，`en` 表逐字未动（英文稿第一作者走
`myfirstauthor`，没有 EN 槽位）。

不联网、不写仓库（产物一律进临时目录）、不跑 corpus.py / build.sh。
缺真实稿件时 `SKIP` 而不是失败（与 `test_coauthor_en.py` 的 ③ 组同一口径）。

用法：
    cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_en_slot_placeholder.py
"""

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import build, templates as T  # noqa: E402
from slcn.latex import parse_macros  # noqa: E402

FAILS = []
SKIPS = []

import test_corpus_fixture_paths as FP  # noqa: E402  「期号+篇序号」取样，不写作者名

# 以前这里写死 `2026年第5期(90)/10 Lei_Li/original/李雷 …docx`：期号与篇序号是真
# 的，作者名是文档化名，真语料那一篇对不上 → ②③④ 只能 SKIP。取样交给 FP，
# 断言仍在本文件；英文摘要 sibling 用同一篇里的文件名片段定位。
_REF = "2026年第5期(90)/10"
_BO = FP.resolve_main(FP.default_root(), _REF)
_EN = FP.resolve(FP.default_root(), _REF, "英文摘要")
BO_CHEN = _BO.path if _BO.ok else ""
EN_ABS = _EN.path if _EN.ok else ""


def _skipd(_r):
    return FP.skip_line(_r)

_TPL_ZH = open(T.resolve("zh").skeleton, encoding="utf-8").read()
_TPL_DEFAULTS = build.template_defaults(_TPL_ZH)


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


# 取样器把「多命中 / 重号 / ref 写坏」判成 fatal —— 那是取样代码自己的错，必须当场
# 记 FAIL，不许被下面的 `if not BO_CHEN: skip(...)` 吞成 SKIP。放在这里是因为
# `check()` 在 `_BO/_EN` 之后才定义（模块级顺序所限）。
for _r in (_BO, _EN):
    if _r.fatal:
        check(f"取样代码的错（{_r.status}）：{_r.ref}", False, _r.detail)


def build_case(src, out_dir, *, overrides=None, patch_doc=None):
    """跑一次 `build_docx`，返回 (`BuildResult`, `.tex` 文本)。"""
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
                               lang="zh", overrides=overrides, verbose=False)
        return res, open(res.tex_path, encoding="utf-8").read()
    finally:
        docxsource.parse_docx = real_parse


def macro_value(tex, name):
    """产物 `.tex` 里某字段的最终值——用**引擎自己的** `parse_macros()` 取，
    免得测试自带一套正则（模板里 `\\newcommand{\\myfirstaffiliationEN}{%` 这种
    带注释的多行值，手写正则极易取错）。"""
    m = parse_macros(tex).get(name)
    return None if m is None else m.value


# ── ① 模板默认值判据 ─────────────────────────────────────────────────────
print("── ① 模板默认值判据：`First Author` 必须被判定为「没填过」──")

check("① 模板默认值表里 `myfirstauthorEN` 的原值就是示例文字 `First Author`",
      _TPL_DEFAULTS.get("myfirstauthorEN") == "First Author",
      f"{_TPL_DEFAULTS.get('myfirstauthorEN')!r}")
check("① `is_parsed('First Author', 'myfirstauthorEN', tpl_defaults)` 为**假**",
      not build.is_parsed("First Author", "myfirstauthorEN", _TPL_DEFAULTS), "")
check("① 真值 `Li Lei` 判真（已解析出的真值不会被覆盖成占位符）",
      build.is_parsed("Li Lei", "myfirstauthorEN", _TPL_DEFAULTS), "")
check("① 空串 / None / `null` 都判假",
      not build.is_parsed("", "myfirstauthorEN", _TPL_DEFAULTS)
      and not build.is_parsed(None, "myfirstauthorEN", _TPL_DEFAULTS)
      and not build.is_parsed("null", "myfirstauthorEN", _TPL_DEFAULTS)
      and not build.is_parsed("", "myfirstaffiliationEN", _TPL_DEFAULTS), "")
# ⚠ 如实记录一处**判据不完整**：`myfirstaffiliationEN` 的模板原值被 `parse_macros`
#   解析成一大段（含 `%` 注释行与 `{Affiliation 1}`），**不等于**裸的 `Affiliation 1`，
#   所以「与模板示例文字逐字相同」这条判据对这个字段**不生效**。能抓住本缺陷的是
#   另一条判据（值为空）——本组 ② 的实测就是走的这条。这个解析怪癖是既有行为，
#   不在本次改动范围内（本函数只负责说清楚它，不擅自改 parse_macros）。
check("① 【如实记录】`myfirstaffiliationEN` 的模板原值是含注释的多行块，"
      "不等于裸的 `Affiliation 1` → 该字段只靠「值为空」判未识别",
      _TPL_DEFAULTS.get("myfirstaffiliationEN") != "Affiliation 1"
      and "{Affiliation 1}" in _TPL_DEFAULTS.get("myfirstaffiliationEN", ""),
      f"{_TPL_DEFAULTS.get('myfirstaffiliationEN', '')[:60]!r}…")

check("① `zh` 表含两个新键（第一作者英文姓名 / 第一作者英文单位）",
      ("myfirstauthorEN", "第一作者英文姓名") in build.REQUIRED_FIELDS["zh"]
      and ("myfirstaffiliationEN", "第一作者英文单位") in build.REQUIRED_FIELDS["zh"], "")
check("① `en` 表**逐字未动**（英文稿第一作者走 `myfirstauthor`，没有 EN 槽位）",
      build.REQUIRED_FIELDS["en"] == (
          ("mytitle", "英文题名"),
          ("myrunningtitle", "页眉标题"),
          ("myfirstauthor", "第一作者"),
          ("myfirstaffiliation", "第一作者单位"),
          ("myfirstemail", "第一作者邮箱"),
          ("myabstract", "英文摘要"),
          ("mytitleCN", "中文题名"),
          ("myabstractCN", "中文摘要"),
      ), f"{build.REQUIRED_FIELDS['en']}")
check("① `override_fields()` 不会把任何手工覆盖键映射到这两个槽位（覆盖保护走 is_parsed）",
      all(k not in build.override_fields({k: "x"}, False)[0]
          for k in ("myfirstauthorEN", "myfirstaffiliationEN")), "")
check("① 占位符文案不含模板示例文字（`First Author` / `Affiliation 1` 都不会留在产物里）",
      build.missing_placeholder("myfirstauthorEN", "第一作者英文姓名")
      == "【未识别：第一作者英文姓名，请手工填写】"
      and build.missing_placeholder("myfirstaffiliationEN", "第一作者英文单位")
      == "【未识别：第一作者英文单位，请手工填写】", "")


# ── ② 正例：没有英文作者 → 占位符，而不是模板示例文字 ────────────────────
print("\n── ② 正例：源稿没有英文作者 → 写占位符，而不是 `First Author` ──")

if not BO_CHEN:
    skip("② 正例（真实稿正文）", _skipd(_BO))
else:
    def _clear_en(doc):
        # 把「源稿没给英文作者/单位」这一前提**钉死**（不依赖解析器随版本变化）
        doc.author_en = ""
        doc.affiliation_en = ""
    with tempfile.TemporaryDirectory() as tmp:
        res, tex = build_case(BO_CHEN, os.path.join(tmp, "pos"), patch_doc=_clear_en)
        check("② `\\myfirstauthorEN` 写的是可见占位符",
              macro_value(tex, "myfirstauthorEN") == "【未识别：第一作者英文姓名，请手工填写】",
              f"{macro_value(tex, 'myfirstauthorEN')!r}")
        check("② `\\myfirstaffiliationEN` 写的是可见占位符",
              macro_value(tex, "myfirstaffiliationEN") == "【未识别：第一作者英文单位，请手工填写】",
              f"{macro_value(tex, 'myfirstaffiliationEN')!r}")
        check("② 模板示例文字 `First Author` **不再**出现在产物里",
              "First Author" not in tex, "")
        check("② 模板示例文字 `Affiliation 1` **不再**出现在产物里",
              "Affiliation 1" not in tex, "")
        check("② 「未识别」汇总里**报出**了这两个字段（不再是静默失败）",
              any("第一作者英文姓名" in w and "第一作者英文单位" in w for w in res.warnings)
              or ("第一作者英文姓名" in " ".join(res.warnings)
                  and "第一作者英文单位" in " ".join(res.warnings)),
              f"{res.warnings[:1]}")


# ── ③ 反例 1：源稿有英文作者 → 不许写占位符 ──────────────────────────────
print("\n── ③ 反例 1：源稿**有**英文作者 → 不许写占位符 ──")

_EN_REAL = None
if EN_ABS:
    from slcn import docxsource  # noqa: E402
    try:
        _EN_REAL = docxsource.parse_docx(EN_ABS).author_en
    except Exception as e:  # noqa: BLE001
        _EN_REAL = None
        skip("③ 真实英文摘要 docx 解析", f"{type(e).__name__}: {e}")
    if _EN_REAL:
        # 期望值**不写死人名**：那是 B-269 的匿名化字面量，而真实姓名不许进公开代码
        # （§15）。这里只钉「解析出了罗马字母姓名」这个结构事实，值本身由稿件决定。
        check("③ 真实英文摘要 docx 解析出了罗马字母英文作者",
              bool(_EN_REAL) and all(c.isascii() for c in _EN_REAL)
              and len(_EN_REAL.split()) >= 2, f"{_EN_REAL!r}")
else:
    skip("③ 真实英文摘要 docx 解析", _skipd(_EN))

if not BO_CHEN:
    skip("③ 反例 1（注入英文作者）", _skipd(_BO))
else:
    def _inject_en(doc):
        doc.author_en = _EN_REAL or "Li Lei"
        doc.affiliation_en = "School of Philosophy, Wuhan University"
    with tempfile.TemporaryDirectory() as tmp:
        res, tex = build_case(BO_CHEN, os.path.join(tmp, "neg1"), patch_doc=_inject_en)
        check("③ `\\myfirstauthorEN` 填的是源稿英译名，**不是**占位符",
              macro_value(tex, "myfirstauthorEN") == (_EN_REAL or "Li Lei")
              and "未识别：第一作者英文姓名" not in tex,
              f"{macro_value(tex, 'myfirstauthorEN')!r}")
        check("③ `\\myfirstaffiliationEN` 填的是源稿英译单位，**不是**占位符",
              macro_value(tex, "myfirstaffiliationEN") == "School of Philosophy, Wuhan University"
              and "未识别：第一作者英文单位" not in tex,
              f"{macro_value(tex, 'myfirstaffiliationEN')!r}")
        check("③ 占位符清单里没有这两项",
              "第一作者英文姓名" not in " ".join(res.warnings)
              and "第一作者英文单位" not in " ".join(res.warnings), "")


# ── ④ 反例 2：手工覆盖优先，不许被占位符覆盖 ─────────────────────────────
print("\n── ④ 反例 2：手工覆盖的字段不许被占位符覆盖 ──")

if not BO_CHEN:
    skip("④ 反例 2（真实稿 + overrides）", _skipd(_BO))
else:
    def _clear_all(doc):
        # 让这几个字段在源稿里都解析不出来，确保「不是占位符」只能归功于覆盖
        doc.author_en = ""
        doc.affiliation_en = ""
        doc.affiliation_cn = ""
        doc.title_en = ""
        doc.abstract_en = ""
        doc.email = ""
    _OV = {
        "title_en": "A Survey of Contemporary Philosophy of Logic",
        "abstract_en": "Three research models can be generalized…",
        "affiliation_cn": "武汉大学哲学学院",
        "email": "li-lei@sample.edu.cn",
    }
    with tempfile.TemporaryDirectory() as tmp:
        res, tex = build_case(BO_CHEN, os.path.join(tmp, "neg2"),
                              overrides=_OV, patch_doc=_clear_all)
        check("④ `--title-en` 的字段保留了覆盖值（没被写成占位符）",
              macro_value(tex, "mytitleEN") == _OV["title_en"]
              and "未识别：英文题名" not in tex, f"{macro_value(tex, 'mytitleEN')!r}")
        check("④ `--abstract-en` 的字段保留了覆盖值",
              macro_value(tex, "myabstractEN") == _OV["abstract_en"]
              and "未识别：英文摘要" not in tex, "")
        check("④ `--affiliation-cn` 的字段保留了覆盖值",
              macro_value(tex, "myfirstaffiliation") == _OV["affiliation_cn"]
              and "未识别：第一作者单位" not in tex, "")
        check("④ `--email` 的字段保留了覆盖值",
              macro_value(tex, "myfirstemail") == _OV["email"]
              and "未识别：第一作者邮箱" not in tex, "")
        check("④ 覆盖后**没有**这两个英文槽位的占位符（仍由源稿/空值决定，与覆盖无关）",
              macro_value(tex, "myfirstauthorEN") == "【未识别：第一作者英文姓名，请手工填写】",
              f"{macro_value(tex, 'myfirstauthorEN')!r}")


print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
