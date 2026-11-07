#!/usr/bin/env python3
r"""B-375 round2 OBS-HOST-2：栏目主持人行防孤字/裁切（toctemplate.make_column_block）。

背景：主持人行落在栏目表第 2 列 —— 栏目版式是作者列 p{0.26\textwidth}（130mm 版心
约 96.2pt），正常版式（专栏勾选 0 篇时正文按普通条目排版）是 r 右对齐列。
「主持人：」+「本刊编辑部」估算 108pt 超过列宽：p 列里 CJK 任意断行只剩孤字「部」，
r 列里一路向左撑可能压到栏目名。用户拍板：版心与字号不变，超列宽 90% 时格内分两行。

实现形态：嵌套 tabular[t]（\newline 只在 p 列段落模式生效，r 列里无效）：
    &\textit{\large \begin{tabular}[t]{@{}l@{}}主持人：\\本刊编辑部\end{tabular}}
另防回归：re.sub 必须函数式 replacement —— 字符串模板会把 \\ 折叠成 \、把 \n 当
真换行（Lead 实测 bug），这里断言字面双反斜杠与无真换行。

本文件是独立脚本（同 Engine/tests 其余套件风格），失败退出码非 0。
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "vendor"))

from slcn import toc as _toc        # noqa: E402
from slcn import toctemplate as _tt  # noqa: E402

BS = chr(92)
DB = BS + BS
OPEN = BS + "begin{tabular}[t]{@{}l@{}}"
CLOSE = BS + "end{tabular}"
LONG = "本刊编辑部"
SHORT = "刘奋荣"

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


def make_settings(kind: str = "专栏", name: str = "偏好逻辑",
                  name_en: str = "Preference Logic", host: str = "刘奋荣"):
    return _toc.TocSettings(columnKind=kind, columnName=name,
                            columnNameEN=name_en, columnHost=host)


#: 上一期的中文栏目形态（旧名/旧主持人 + 栏目表格的间距行）。
SHAPE = [
    "{\\large \\bf{专栏：旧名}}",
    "&\\textit{\\large 主持人：旧主持人}",
    "\\\\[3.2 ex]",
]


def long_cell(host: str = LONG) -> str:
    return OPEN + "主持人：" + DB + host + CLOSE


LONG_LINE = "&" + BS + "textit{" + BS + "large " + long_cell() + "}"
SHORT_LINE = "&" + BS + "textit{" + BS + "large 主持人：刘奋荣}"


def host_line(lines: list[str]) -> str:
    return next((ln for ln in lines if "主持人" in ln), "")


def main() -> None:
    print("── (a) 长主持人（本刊编辑部）：builtin 与 shadow 两路都格内两行 ──")
    long_settings = make_settings(host=LONG)
    builtin = _tt.make_column_block(long_settings, [], True)
    shadow = _tt.make_column_block(long_settings, SHAPE, True)
    b_line, s_line = host_line(builtin), host_line(shadow)
    check("(a1) builtin 主持人格是嵌套 tabular[t] 两行", b_line == LONG_LINE, b_line)
    check("(a2) shadow 主持人格同样是嵌套 tabular[t] 两行", s_line == LONG_LINE, s_line)
    check("(a3) 两路主持人行完全一致", b_line == s_line,
          f"builtin={b_line!r} shadow={s_line!r}")
    check("(a4) 行内没有真换行（re.sub 把反斜杠当转义时会拆行）",
          "\n" not in b_line and "\n" not in s_line)
    check("(a5) 不含被转义坏的 ewline，也没有无效的 newline 命令",
          "ewline" not in b_line and (BS + "newline") not in b_line,
          f"{b_line!r}")
    check("(a6) 嵌套 tabular 的换行保留字面两个反斜杠", DB in b_line and b_line.count(DB) == 1,
          f"{b_line!r}")
    check("(a7) 主持人行仍在作者列（以 & 开头）", b_line.startswith("&"), b_line)

    print("\n── (b) 短主持人（刘奋荣）：保持单行 ──")
    short_settings = make_settings(host=SHORT)
    short_builtin = _tt.make_column_block(short_settings, [], True)
    short_shadow = _tt.make_column_block(short_settings, SHAPE, True)
    sb, ss = host_line(short_builtin), host_line(short_shadow)
    check("(b1) builtin 单行且等于标准行", sb == SHORT_LINE, sb)
    check("(b2) shadow 单行且等于标准行", ss == SHORT_LINE, ss)
    check("(b3) 单行不含嵌套 tabular / 换行命令",
          "tabular" not in sb and (BS + "newline") not in sb, sb)

    print("\n── (c) 阈值：4 字 host 触发、3 字不触发（列宽 90%） ──")
    check("(c1) 列宽约 96.17pt、阈值约 86.55pt",
          abs(_tt._HOST_COLUMN_WIDTH_PT - 96.17) < 0.05
          and abs(_tt._HOST_LINE_BREAK_THRESHOLD_PT - 86.553) < 0.1,
          f"{_tt._HOST_COLUMN_WIDTH_PT} / {_tt._HOST_LINE_BREAK_THRESHOLD_PT}")
    check("(c2) 本刊编辑部（5 字 108pt）触发", _tt._host_needs_line_break(LONG))
    check("(c3) 4 字 96pt 触发", _tt._host_needs_line_break("刘奋荣刘"))
    check("(c4) 3 字 84pt 不触发", not _tt._host_needs_line_break(SHORT))
    check("(c5) 空主持人按标签宽 48pt 不触发", not _tt._host_needs_line_break(""))

    print("\n── (d) 函数式替换单测：分隔符不转义、旧主持人被整体替换 ──")
    direct = _tt._replace_host_with_optional_break(
        "&" + BS + "textit{" + BS + "large 主持人：旧主持人}", LONG)
    check("(d1) 长主持人输出等于标准两行行", direct == LONG_LINE, direct)
    direct_short = _tt._replace_host_with_optional_break(
        "&" + BS + "textit{" + BS + "large 主持人：旧主持人}", SHORT)
    check("(d2) 短主持人替换后仍单行", direct_short == SHORT_LINE, direct_short)
    check("(d3) 空主持人输出仅标签", _tt._host_cell_content("") == "主持人：",
          _tt._host_cell_content(""))

    print("\n── (e) 中文栏目名称/专辑变体：名称替换、主持人行规则不变 ──")
    zh_lines = _tt.make_column_block(make_settings(name="动态认知逻辑"), SHAPE, True)
    check("(e1) 专栏中文名替换", any("专栏：动态认知逻辑" in ln for ln in zh_lines),
          str(zh_lines))
    album_lines = _tt.make_column_block(
        make_settings(kind="专辑", name="偏好逻辑专辑", host=LONG), [], True)
    check("(e2) 专辑中文名用 专辑：X",
          any(ln == "{\\large \\bf{专辑：偏好逻辑专辑}}" for ln in album_lines),
          str(album_lines))
    check("(e3) 专辑主持人行与专栏同规则（长 host 格内两行）",
          host_line(album_lines) == LONG_LINE, str(album_lines))

    print("\n── (f) 英文栏目标签（B-364）：Column / Special Issue，只用英文名 ──")
    en_col = _tt.make_column_block(make_settings(), [], False)
    en_album = _tt.make_column_block(make_settings(kind="专辑"), [], False)
    check("(f1) 专栏 → Column: Preference Logic",
          len(en_col) >= 1 and "Column: Preference Logic" in en_col[0], str(en_col))
    check("(f2) 专辑 → Special Issue: Preference Logic",
          len(en_album) >= 1 and "Special Issue: Preference Logic" in en_album[0],
          str(en_album))
    check("(f3) 英文行不含中文栏目名/主持人",
          not any("主持人" in ln or "偏好逻辑" in ln for ln in en_col + en_album),
          str(en_col + en_album))
    check("(f4) 英文默认间距 3mm", en_col[-1] == "\\\\[3mm]", str(en_col))
    spacing_shape = ["\\\\[3.2 ex]"]
    en_spaced = _tt.make_column_block(make_settings(), spacing_shape, False)
    check("(f5) 英文间距沿用上一期模板", en_spaced[-1] == "\\\\[3.2 ex]", str(en_spaced))

    print("\n── (g) 英文名空白：不回退中文、不产出残行 ──")
    blank_en = make_settings(name_en="   ")
    check("(g1) 英文名空白 → is_column_enabled False、校验报缺英文名称",
          blank_en.is_column_enabled is False
          and "英文名称" in blank_en.column_validation_error(),
          blank_en.column_validation_error())
    out_blank = _tt.make_column_block(blank_en, [], False)
    check("(g2) 英文块返回空列表且不含中文回退", out_blank == [], str(out_blank))

    print("\n── (h) 栏目未启用：中英都不输出栏目块 ──")
    off = _toc.TocSettings()
    check("(h1) 中文空", _tt.make_column_block(off, [], True) == [])
    check("(h2) 英文空", _tt.make_column_block(off, [], False) == [])

    print("\n── (i) 本期成品里已是嵌套 tabular 的旧格：整格替换，不被花括号截断 ──")
    nested_old = ("&" + BS + "textit{" + BS + "large " + OPEN + "主持人：" + DB
                  + "旧主持人" + CLOSE + "}")
    inherited_long = _tt.make_column_block(
        make_settings(host=LONG), [nested_old], True)
    inherited_short = _tt.make_column_block(
        make_settings(host=SHORT), [nested_old], True)
    check("(i1) 继承嵌套旧格 + 长 host ⇒ 仍是完整两行格",
          host_line(inherited_long) == LONG_LINE, str(inherited_long))
    check("(i2) 继承嵌套旧格 + 短 host ⇒ 折回单行格",
          host_line(inherited_short) == SHORT_LINE, str(inherited_short))
    check("(i3) 替换后不残留旧主持人、也没有多余花括号",
          "旧主持人" not in host_line(inherited_long)
          and host_line(inherited_long).count("{") == host_line(inherited_long).count("}"),
          str(inherited_long))

    if _fails:
        print(f"\n失败 {len(_fails)} 项：{_fails}")
        sys.exit(1)
    print(f"\n全部 {_passed} 项断言通过")


if __name__ == "__main__":
    main()