#!/usr/bin/env python3
r"""批 355（B-120 车道 E）回归：中文目录横向几何 + 两行标题 `\newline`。

背景（证据：`evidence/b120-toc-analysis/report.md` §3–§5）：
  · 继承模板的 tabularx 列定义是逐期手调的负胶（90/89 `-7.5em/-4.5em`；88 `-0.3em/-4.2em`），
    普通期没有 `~~~~ ` 前缀抵消负胶，作者列/页码列被左拉压进标题；
  · `l` 列自然宽合计小于版心，无 X 列时右侧留白约 48mm；
  · 两行标题的第二行用 `\\`（结束表格行）而不是 `\newline`（同格软换行），
    tabularx 把第二行当新行参与列宽计算，负胶下前半截被裁。

判据：
  (a) 中文 90 期式（注释旧列定义 + 下行生效负胶）→ 规范列格式，旧注释行删除，幂等；
  (b) 英文 89 期式（注释 `\begin{tabularx}` + 同行负/正胶）→ CANONICAL_EN，注释行删除；
  (c) 栏目期 → CANONICAL_ZH_COLUMN（作者列 `p{0.26\textwidth}`）；4 列 / 自定义 X / 列数不符 → 抛错；
  (d) 两行标题第二行用 `\newline`，行尾 `\\` 仍保留；
  (e) `generate_tex`：坏模板回退内置模板并告警；正常模板输出规范列格式；
  (f) D4 告警：断行上限反推、负间距指纹、估算宽不足 0.92×（只告警，不改版式）；
  (g) 英文 contents 也走规范列格式。

本文件是独立脚本（与 Engine/tests 其余套件同一风格），失败退出码非 0。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import toc  # noqa: E402
from slcn import toctemplate as tt  # noqa: E402

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


# ── 夹具：报告与模板 ────────────────────────────────────────────────────

REPORT = """\
Jingru_Kou
卷号：19  期号：5  年份：2026
起始页码：1  终止页码：16  页数：16
第一作者英文: Jingru Kou
第二作者英文: Yetao Liu
中文标题: 可知性悖论、形式可证与认知实现
第一作者中文: \\kaishu 寇静茹
第二作者中文: \\kaishu 刘叶涛
英文标题: The Knowability Paradox, Formal Provability, and Cognitive Realization

Liang_Zhang
卷号：19  期号：5  年份：2026
起始页码：17  终止页码：30  页数：14
第一作者英文: Liang Zhang
中文标题: 论雅辛之谜及其解决方案\\\\——从扩展的RZH标准看
第一作者中文: \\kaishu 张亮
英文标题: An Investigation of Yalcin's Puzzle and Its Solutions

Hao_Wu
卷号：19  期号：5  年份：2026
起始页码：31  终止页码：40  页数：10
第一作者英文: Hao Wu
中文标题: 结构性规范与动态偏好的公理化研究及其元理论性质
第一作者中文: \\kaishu 吴昊
英文标题: Structural Norms and Axiomatization of Dynamic Preference
"""

HEAD_90 = (
    "\\documentclass[11pt]{ctexart}\n"
    "第\\ 19 卷，第\\ 5 期，总第\\ 90 期\n"
    "\\begin{tabularx}{\\textwidth}\n"
    "%{l@{\\hspace{-12.7em}}r@{\\hspace{0.45em}}r}\n"
    "{l@{\\hspace{-7.5em}}r@{\\hspace{-4.5em}}r}"
)

HEAD_88 = (
    "\\documentclass[11pt]{ctexart}\n"
    "第\\ 19 卷，第\\ 3 期，总第\\ 88 期\n"
    "\\begin{tabularx}{\\textwidth}\n"
    "{l@{\\hspace{-0.3em}}r@{\\hspace{-4.2em}}r}"
)

HEAD_EN_89 = (
    "\\documentclass[11pt]{ctexart}\n"
    "Volume 19, Number 4, August 2026\n"
    "%\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n"
    "\\begin{tabularx}{\\textwidth}{l@{\\hspace{2.7em}}r}"
)


def template_zh(head: str, column_lines: tuple[str, ...] = ()) -> str:
    body = head + "\n"
    body += "\n".join(column_lines) + "\n" if column_lines else ""
    return body + "\\end{tabularx}\n\\end{document}\n"


def template_en(head: str) -> str:
    return head + "\n\\end{tabularx}\n\\end{document}\n"


def _issue(tmp: Path, *, zh: str | None = None, en: str | None = None,
           report: str = REPORT) -> Path:
    (tmp / "000 Table of Contents").mkdir(parents=True, exist_ok=True)
    (tmp / "信息检查TEST.txt").write_text(report, encoding="utf-8")
    if zh is not None:
        (tmp / "000 Table of Contents" / "Chinese Content.tex").write_text(
            zh, encoding="utf-8")
    if en is not None:
        (tmp / "000 Table of Contents" / "contents.tex").write_text(
            en, encoding="utf-8")
    return tmp / "信息检查TEST.txt"


def _read(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8")


# ── (a) 中文列定义规范化 ────────────────────────────────────────────────

def test_canonicalize_zh() -> None:
    print("\n── (a) 中文列定义规范化：替换 / 删旧注释 / 幂等 ──")
    got = tt.canonicalize_head(HEAD_90, "Chinese Content.tex")
    check("(a) 换成 CANONICAL_ZH", tt.CANONICAL_ZH in got, got[-200:])
    check("(a) 生效负胶已删除", "-7.5em" not in got and "-4.5em" not in got, got[-200:])
    check("(a) 旧注释列定义行已删除",
          "-12.7em" not in got and "%{l@" not in got, got[-200:])
    check("(a) 卷期行原样保留", "第\\ 19 卷" in got, got[:120])
    again = tt.canonicalize_head(got, "Chinese Content.tex")
    check("(a) 幂等（第二次不改）", again == got, again[-160:])

    got88 = tt.canonicalize_head(HEAD_88, "Chinese Content.tex")
    check("(a) 88 期无注释模板同样规范化", tt.CANONICAL_ZH in got88, got88[-200:])


# ── (b) 英文列定义规范化（89 期注释 \begin 行）────────────────────────────

def test_canonicalize_en() -> None:
    print("\n── (b) 英文 89 期式：注释行删除、同行列定义替换 ──")
    got = tt.canonicalize_head(HEAD_EN_89, "contents.tex")
    check("(b) 换成 CANONICAL_EN", tt.CANONICAL_EN in got, got[-200:])
    check("(b) 旧注释 \\begin 行已删除", "%\\begin{tabularx}" not in got, got[-240:])
    check("(b) 旧列距 2.7em/4em 已删除",
          "2.7em" not in got and "4em" not in got, got[-200:])

    # 栏目期：历史 l@1em 与上一版规范形 X@2em 都归一到 X@1em（人工成品同宽）
    got_col = tt.canonicalize_head(HEAD_EN_89, "contents.tex", is_column=True)
    check("(b) 英文栏目期换成 CANONICAL_EN_COLUMN",
          tt.CANONICAL_EN_COLUMN in got_col, got_col[-200:])
    legacy = ("\\begin{tabularx}{\\textwidth}{@{}>{\\raggedright\\arraybackslash}"
              "X@{\\hspace{2em}}r@{}}")
    got_legacy = tt.canonicalize_head(legacy, "contents.tex", is_column=True)
    check("(b) 上一版 X@2em 栏目模板可升级为 X@1em",
          tt.CANONICAL_EN_COLUMN in got_legacy
          and "2em" not in got_legacy, got_legacy[-200:])
    try:
        tt.canonicalize_head(legacy.replace("r@{}", "r@{\\hspace{1em}}r@{}"),
                             "contents.tex", is_column=True)
        check("(b) 英文栏目期自定义 3 列 X 仍抛错", False, "未抛错")
    except tt.TemplateLayoutError:
        check("(b) 英文栏目期自定义 3 列 X 仍抛错", True)


# ── (b2) 英文栏目表行高 ────────────────────────────────────────────────

def test_column_row_stretch() -> None:
    print("\n── (b2) 英文栏目表 0.99 行高：插入 / 幂等 / 不覆盖人工值 ──")
    head = "\\begin{tabularx}{\\textwidth}{X r}\n"
    got = tt.ensure_column_row_stretch(head)
    check("(b2) 行高定义插在生效 tabularx 前",
          got.startswith("\\renewcommand{\\arraystretch}{0.99}\n\\begin{tabularx}"),
          got[:120])
    check("(b2) 幂等", tt.ensure_column_row_stretch(got) == got, got[:120])
    commented = "%\\begin{tabularx}{\\textwidth}{X r}\n" + head
    got_commented = tt.ensure_column_row_stretch(commented)
    check("(b2) 只看未注释的 tabularx",
          got_commented.startswith("%\\begin{tabularx}")
          and "\n\\renewcommand{\\arraystretch}{0.99}\n\\begin{tabularx}" in got_commented,
          got_commented[:160])
    custom = "\\renewcommand{\\arraystretch}{1.1}\n" + head
    check("(b2) 人工模板已有行高时不覆盖",
          tt.ensure_column_row_stretch(custom) == custom, custom[:120])
    check("(b2) 非栏目时移除本工具插入的行",
          tt.ensure_column_row_stretch(got, enabled=False) == head,
          tt.ensure_column_row_stretch(got, enabled=False)[:120])
    check("(b2) 非栏目时保留人工行高",
          tt.ensure_column_row_stretch(custom, enabled=False) == custom, custom[:120])
    check("(b2) 没有 tabularx 时原样返回",
          tt.ensure_column_row_stretch("\\documentclass{ctexart}\n") ==
          "\\documentclass{ctexart}\n")


# ── (c) 栏目期 / 识别失败 ───────────────────────────────────────────────

def test_column_and_errors() -> None:
    print("\n── (c) 栏目期列格式 / 无法识别时抛错 ──")
    got = tt.canonicalize_head(
        HEAD_90.replace("第\\ 5 期", "第\\ 4 期"),
        "Chinese Content.tex", is_column=True)
    check("(c) 栏目期用 CANONICAL_ZH_COLUMN", tt.CANONICAL_ZH_COLUMN in got, got[-240:])
    check("(c) 作者列 p{0.26\\textwidth}", "p{0.26\\textwidth}" in got, got[-240:])

    bad_cases = {
        "4 列": ("\\begin{tabularx}{\\textwidth}\n"
                 "{l@{\\hspace{-1em}}r@{\\hspace{1em}}r@{\\hspace{1em}}r}", 3),
        "自定义 X": ("\\begin{tabularx}{\\textwidth}\n"
                     "{X@{\\hspace{1em}}r@{\\hspace{1em}}r}", 3),
        "列数不符": ("\\begin{tabularx}{\\textwidth}\n"
                     "{l@{\\hspace{1em}}r}", 3),
    }
    for name, (head, expected) in bad_cases.items():
        try:
            tt.canonicalize_head(head, "Chinese Content.tex")
            check(f"(c) {name} → 抛 TemplateLayoutError", False, "未抛错")
        except tt.TemplateLayoutError:
            check(f"(c) {name} → 抛 TemplateLayoutError", True)

    # 列数解析器直接验证（规范形自身 = 3 列）
    check("(c) count_columns(CANONICAL_ZH) == 3",
          tt.count_columns(tt.CANONICAL_ZH) == 3,
          str(tt.count_columns(tt.CANONICAL_ZH)))
    check("(c) count_columns(CANONICAL_EN) == 2",
          tt.count_columns(tt.CANONICAL_EN) == 2,
          str(tt.count_columns(tt.CANONICAL_EN)))
    check("(c) 规范形与栏目形不同", tt.CANONICAL_ZH != tt.CANONICAL_ZH_COLUMN)


# ── (d) 两行标题 \newline（D1）─────────────────────────────────────────

def test_cell_newline() -> None:
    print("\n── (d) 两行标题第二行 `\\newline`（同格软换行）──")
    entry = toc.TocEntry(folderName="Liang_Zhang", page="17",
                         chineseTitle="论雅辛之谜及其解决方案——从扩展的RZH标准看",
                         chineseAuthors=["张亮"])
    text = toc.chinese_entry(entry, False, 20)
    check("(d) 输出含 \\newline", "\\newline" in text, text.replace("\n", "⏎")[:240])
    check("(d) 第二行仍是同一单元格（新行紧跟 `{`）",
          "\\newline{\\large" in text, text.replace("\n", "⏎")[:240])
    check("(d) 行尾 `\\\\` 仍保留（结束表格行）",
          text.rstrip().endswith("\\\\"), text.replace("\n", "⏎")[-80:])
    check("(d) 第二行以 —— 开头", "{\\large ——" in text.replace("\n", ""),
          text.replace("\n", "⏎")[:240])


# ── (e)(f) generate_tex：规范列 + 告警 + 回退 ───────────────────────────

def test_generate_normal() -> None:
    print("\n── (e)(f) generate_tex：正常模板 → 规范列 + D4 告警 ──")
    tmp = Path(tempfile.mkdtemp(prefix="b355_toc_norm_"))
    report = _issue(tmp, zh=template_zh(HEAD_90))
    logs: list[str] = []
    settings = toc.TocSettings()  # titleWrapLimit 默认 20
    out = toc.generate_tex(str(report), "ch", settings, log=logs.append, stamp="TEST")
    text = _read(out)
    check("(e) 产物存在", bool(out) and Path(out).is_file(), str(out))
    check("(e) 产物列定义是 CANONICAL_ZH", tt.CANONICAL_ZH in text, text[-300:])
    check("(e) 产物无负胶", "-7.5em" not in text and "\\hspace{-" not in text, text[-300:])
    check("(e) 两行标题用 \\newline", "\\newline" in text, text[-400:])
    check("(f) 断行上限反推告警", any("标题断行上限" in s for s in logs), str(logs))
    check("(f) 模板来源日志", any("模板来源：" in s for s in logs), str(logs))
    check("(e) 中文/普通目录不加英文栏目行高", "\\arraystretch" not in text, text[:300])


def test_generate_fallback() -> None:
    print("\n── (e) 坏模板（4 列）→ 告警 + 回退内置模板 ──")
    tmp = Path(tempfile.mkdtemp(prefix="b355_toc_fb_"))
    bad = HEAD_90.replace("{l@{\\hspace{-7.5em}}r@{\\hspace{-4.5em}}r}",
                          "{l@{\\hspace{-7.5em}}r@{\\hspace{-4.5em}}r@{\\hspace{1em}}r}")
    report = _issue(tmp, zh=template_zh(bad))
    logs: list[str] = []
    out = toc.generate_tex(str(report), "ch", toc.TocSettings(),
                           log=logs.append, stamp="TEST")
    text = _read(out)
    check("(e) 告警回退内置模板", any("回退内置模板" in s for s in logs), str(logs)[:400])
    check("(e) 回退后仍输出规范列", tt.CANONICAL_ZH in text, text[-300:])
    check("(e) 4 列旧定义未进产物", "@{\\hspace{1em}}r@{\\hspace{-4.5em}}" not in text,
          text[-300:])


def test_generate_column() -> None:
    print("\n── (c) 栏目期 generate_tex：p{0.26} + `~~~~ ` + 栏目块 ──")
    tmp = Path(tempfile.mkdtemp(prefix="b355_toc_col_"))
    column_lines = (
        "专辑：偏好逻辑 &&\\\\textit{\\large 主持人：刘奋荣}\\\\",
        "\\\\[3.2 ex]",
    )
    report = _issue(tmp, zh=template_zh(HEAD_90, column_lines))
    settings = toc.TocSettings(
        columnKind="专辑", columnName="偏好逻辑", columnNameEN="Preference Logic",
        columnHost="刘奋荣", columnArticles=["Jingru_Kou", "Liang_Zhang"])
    logs: list[str] = []
    out = toc.generate_tex(str(report), "ch", settings, log=logs.append, stamp="TEST")
    text = _read(out)
    check("(c) 产物用 CANONICAL_ZH_COLUMN", tt.CANONICAL_ZH_COLUMN in text, text[-400:])
    # B-375（2026-11-06 用户拍板）：专辑 = 当前报告全部稿件都带栏目标记；设置里
    # columnArticles 只存两篇也不删未存的那篇 —— 三篇全部列出、三篇都带 ~~~~。
    check("(c) 专辑：三篇全部列出且都带 ~~~~ 前缀",
          sum(1 for _ln in text.split("\n")
              if _ln.startswith("{\\large ~~~~")) == 3, text[-600:])
    check("(c) 专辑：旧 columnArticles 子集不删条目（未存的 Hao_Wu 也在）",
          "结构性规范" in text, text[-600:])
    check("(c) 栏目块名称/主持人替换",
          "专辑：偏好逻辑" in text and "主持人：刘奋荣" in text, text[:1200])
    check("(f) 栏目期断行上限收窄告警", any("标题断行上限" in s for s in logs), str(logs))
    check("(c) 中文目录不加英文栏目行高", "\\arraystretch" not in text, text[:300])


def test_guards() -> None:
    print("\n── (f) 守卫：负间距指纹 / 估算宽不足 0.92× ──")
    settings = toc.TocSettings()
    entry = toc.TocEntry(folderName="A", page="9",
                         chineseTitle="短题", chineseAuthors=["甲"])
    logs: list[str] = []
    # 负间距指纹（head 里另外一行负胶，规范化不会动它）
    head_bad = "\\newcommand{\\neg}{\\hspace{-1em}}\n" + tt.CANONICAL_ZH
    toc._log_layout_guard(head_bad, [("A", entry)], settings, True, False, 20,
                          log=logs.append)
    check("(f) 负间距指纹告警", any("负间距" in s for s in logs), str(logs))

    logs2: list[str] = []
    toc._log_layout_guard(tt.CANONICAL_ZH, [("A", entry)], settings, True, False, 6,
                          log=logs2.append)
    check("(f) 估算宽不足 0.92× 告警", any("右侧可能留白" in s for s in logs2), str(logs2))

    # 超过 1.02×：作者串极长（普通期作者列按自然宽计入）
    big = toc.TocEntry(folderName="B", page="9", chineseTitle="短题",
                       chineseAuthors=["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛",
                                       "壬", "癸", "子", "丑", "寅", "卯", "辰", "巳",
                                       "午", "未", "申", "酉", "戌", "亥"])
    logs3: list[str] = []
    toc._log_layout_guard(tt.CANONICAL_ZH, [("B", big)], settings, True, False, 20,
                          log=logs3.append)
    check("(f) 估算宽超 1.02× 告警", any("超过版心" in s for s in logs3), str(logs3))


def test_generate_en() -> None:
    print("\n── (g) 英文 contents：规范列 + 断行上限反推 ──")
    tmp = Path(tempfile.mkdtemp(prefix="b355_toc_en_"))
    report = _issue(tmp, en=template_en(HEAD_EN_89))
    logs: list[str] = []
    out = toc.generate_tex(str(report), "en", toc.TocSettings(),
                           log=logs.append, stamp="TEST")
    text = _read(out)
    check("(g) 产物存在", bool(out) and Path(out).is_file(), str(out))
    check("(g) 产物列定义是 CANONICAL_EN", tt.CANONICAL_EN in text, text[-300:])
    check("(g) 英文断行上限反推告警", any("标题断行上限" in s for s in logs), str(logs))
    check("(g) 无栏目英文不加行高", "\\arraystretch" not in text, text[:300])

    # 普通英文继承上一期栏目源：移除本工具插入的 0.99 行高，保持既有版式
    tmp2 = Path(tempfile.mkdtemp(prefix="b355_toc_en_legacy_"))
    report2 = _issue(tmp2, en=template_en(
        "\\renewcommand{\\arraystretch}{0.99}\n" + HEAD_EN_89))
    out2 = toc.generate_tex(str(report2), "en", toc.TocSettings(),
                            log=lambda _s: None, stamp="TEST")
    text2 = _read(out2)
    check("(g) 普通英文继承栏目源时移除工具行高",
          "\\arraystretch" not in text2, text2[:400])


def test_generate_en_column_stretch() -> None:
    print("\n── (g2) 英文栏目：0.99 行高只加在栏目正文表 ──")
    tmp = Path(tempfile.mkdtemp(prefix="b355_toc_en_col_"))
    report = _issue(tmp, en=template_en(HEAD_EN_89))
    settings = toc.TocSettings(
        columnKind="专辑", columnName="偏好逻辑", columnNameEN="Preference Logic",
        columnHost="刘奋荣")
    logs: list[str] = []
    out = toc.generate_tex(str(report), "en", settings,
                           log=logs.append, stamp="TEST")
    text = _read(out)
    check("(g2) 英文栏目产物存在", bool(out) and Path(out).is_file(), str(out))
    check("(g2) 英文栏目表带 0.99 行高",
          "\\renewcommand{\\arraystretch}{0.99}\n\\begin{tabularx}" in text,
          text[:500])
    check("(g2) 列定义仍是 CANONICAL_EN_COLUMN",
          tt.CANONICAL_EN_COLUMN in text, text[:500])
    check("(g2) 栏目行仍在",
          "Special Issue: Preference Logic" in text, text[:600])


def main() -> int:
    test_canonicalize_zh()
    test_canonicalize_en()
    test_column_row_stretch()
    test_column_and_errors()
    test_cell_newline()
    test_generate_normal()
    test_generate_fallback()
    test_generate_column()
    test_guards()
    test_generate_en()
    test_generate_en_column_stretch()

    print("\n" + "=" * 60)
    if _fails:
        print(f"FAILED  {len(_fails)} 项：")
        for name in _fails:
            print(f"  - {name}")
        print(f"通过 {_passed} 项，失败 {len(_fails)} 项")
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
