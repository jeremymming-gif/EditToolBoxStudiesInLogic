#!/usr/bin/env python3
r"""B-375「生成目录」的栏目判据（Engine 单点，纯文本不编译）。

判据（用户 2026-11-06 拍板，见 Engine/docs/RULES.md 生成目录一节）：
  (a) 选了栏目（种类非空）就必须四项齐全：种类只能是「专栏/专辑」、中文名/英文名/主持人
      strip 后非空（纯空格算缺）。不完整 → column_validation_error() 报缺项，
      generate_tex 返回 None、不建 .toolbox-tmp、不写任何 .tex；CLI 退出 2、不写结果 JSON；
      --preview 仍退出 0 并把 columnValidationError/columnEnabled 给外壳。无栏目时四个字段忽略。
  (b) 目录永远列出当前报告全部篇目，columnArticles 只决定是否带栏目标记（不删条目）：
      专栏 = 勾选篇目带 ~~~~、**允许 0 篇**（没有「至少一篇」规则）；专辑 = 全部稿件都带 ~~~~，
      设置里 stale/empty/subset 的 columnArticles 一律忽略。
  (c) 英文栏目标签只用 columnNameEN：专栏 Column: X / 专辑 Special Issue: X，绝不回退中文名。
  (d) B-363：栏目内中文两行标题的续行用 \phantom{~~~~ } 复制首行真实前缀
      （宽度由 TeX 渲染期量出，不写死数字）；非栏目续行保持原样、无缩进盒。
  (e) 报告里解析不到任何稿件条目（喂错文件）：明确报错（❌ 日志 / 返回 None / CLI 退出 2），
      不静默产出空目录、不写任何 .tex / 结果 JSON。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import toc as TOC          # noqa: E402
from slcn import toctemplate as TT    # noqa: E402

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


# ── 造期目录 + 《信息检查》报告（三篇；第三篇长标题会触发两行断行）──────
ARTICLES = [
    ("Ning_Wang", "论示例悖论及其解决方案——从扩展的X标准看",
     "On the Example Paradox", "1", "陆王", "Wang Ning"),
    ("Jingru_Kou", "可知性悖论、形式可证与认知实现",
     "Knowability Paradox, Formal Provability and Cognitive Realization", "17", "寇静茹", "Kou Jingru"),
    ("Hao_Wu", "结构性规范与动态偏好的公理化研究及其元理论性质",
     "Axiomatization of Structural Norms and Dynamic Preferences", "33", "吴浩", "Wu Hao"),
]
LONG_ZH = "结构性规范与动态偏好的公理化研究及其元理论性质"


def make_issue(root: str) -> str:
    """建一期目录（目录名带总期号，CLI 才能退出 0），返回期目录路径。"""
    issue = os.path.join(root, "2026年第5期(90)")
    os.makedirs(issue, exist_ok=True)
    return issue


def make_report(issue_dir: str) -> str:
    path = os.path.join(issue_dir, "信息检查2026_01_01_00_00_00.txt")
    chunks = []
    for name, zh, en, page, a_zh, a_en in ARTICLES:
        chunks.append(
            f"{name}\r\n"
            "卷号：19  期号：5  年份：2026\r\n"
            f"起始页码：{page}  终止页码：{int(page) + 15}  页数：16\r\n"
            "字号 10.5t\r\n"
            f"中文标题: {zh}\r\n"
            f"英文标题: {en}\r\n"
            f"第一作者中文: {a_zh}\r\n"
            f"第一作者英文: {a_en}\r\n"
            "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(chunks))
    return path


def base_fields(**over: str) -> dict:
    fields = {"columnKind": "专栏", "columnName": "认知悖论研究",
              "columnNameEN": "Cognitive Paradox Studies", "columnHost": "郑建国",
              "columnArticles": []}
    fields.update(over)
    return fields


def tex_files(root: str) -> list[str]:
    found = []
    for dirpath, _dirs, names in os.walk(root):
        found.extend(os.path.join(dirpath, n) for n in names if n.endswith(".tex"))
    return found


# ── (a) 四项齐备才算启用；缺项/纯空格明确拒绝，不写文件 ────────────────
print("── (a) 栏目设置校验：四项缺一不可，纯空格算缺 ──")

root = tempfile.mkdtemp(prefix="slcn_b375_a_")
issue = make_issue(root)
report = make_report(issue)

_missing_cases = [
    # 缺项名称与 column_validation_error() 的人话标签一致（栏目名称/英文名称/主持人）
    ("栏目名称", {"columnName": "   "}),
    ("英文名称", {"columnNameEN": "\t "}),
    ("主持人", {"columnHost": ""}),
]
for label, over in _missing_cases:
    s = TOC.TocSettings(**base_fields(**over))
    err = s.column_validation_error()
    check(f"(a1) 缺{label} ⇒ 不启用 + 明确缺项 + 不发栏目块",
          (not s.is_column_enabled) and label in err
          and TT.make_column_block(s, ["{\\large \\bf{旧}}"], True) == []
          and TT.make_column_block(s, ["\\large  \\bf{Old}"], False) == [],
          err)
    outs = [TOC.generate_tex(report, flag, s, log=lambda _s: None, stamp="A") for flag in ("ch", "en")]
    check(f"(a2) 缺{label} ⇒ generate_tex 返回 None、不建工作目录、不写 .tex",
          outs == [None, None]
          and not os.path.exists(TOC.toc_work_dir(issue))
          and tex_files(issue) == [])

_bad_kind = TOC.TocSettings(**base_fields(columnKind="栏目"))
check("(a3) 种类不是 专栏/专辑 ⇒ 不启用 + 「无法识别」提示",
      not _bad_kind.is_column_enabled and "无法识别" in _bad_kind.column_validation_error(),
      _bad_kind.column_validation_error())

_blank_kind = TOC.TocSettings(**base_fields(columnKind="  "))
check("(a4) 种类纯空格 = 无栏目：不报错、不启用（其余栏目字段忽略）",
      _blank_kind.column_validation_error() == "" and not _blank_kind.is_column_enabled)

_ok = TOC.TocSettings(**base_fields())
check("(a5) 四项齐全 ⇒ 启用、无错误、中英栏目块都发",
      _ok.is_column_enabled and _ok.column_validation_error() == ""
      and TT.make_column_block(_ok, ["{\\large \\bf{旧}}"], True) != []
      and TT.make_column_block(_ok, ["\\large  \\bf{OLD}"], False) != [])


# ── (b) 专辑全收 / 专栏勾选（允许 0 篇）────────────────────────────────
print("\n── (b) 栏目成员口径：专辑全收（忽略旧勾选）/ 专栏勾选、允许 0 篇 ──")

_names = [n for n, _t in TOC.list_articles(report)]
check("(b0) 报告解析出三篇", _names == ["Ning_Wang", "Jingru_Kou", "Hao_Wu"], repr(_names))

for label, stored in (("空列", []),
                      ("只存一篇（subset）", ["01 Ning_Wang"]),
                      ("旧版残留篇名", ["01 Old_Article"])):
    s = TOC.TocSettings(**base_fields(columnKind="专辑", columnName="偏好逻辑",
                                      columnNameEN="Preference Logic",
                                      columnHost="刘奋荣", columnArticles=stored))
    check(f"(b1) 专辑 {label} ⇒ in_toc 仍是全部三篇",
          all(s.in_toc(n, _names) for n in _names), repr(stored))

_kong = TOC.TocSettings(**base_fields(columnArticles=[]))
check("(b2) 专栏空勾选 ⇒ 一篇都不带栏目标记（没有「至少一篇」规则）、仍然合法",
      not any(_kong.in_toc(n, _names) for n in _names)
      and _kong.column_validation_error() == "" and _kong.is_column_enabled)

_one = TOC.TocSettings(**base_fields(columnArticles=["01 Ning_Wang"]))
check("(b3) 专栏勾选带序号的旧写法「01 Ning_Wang」也能对上",
      _one.in_toc("Ning_Wang", _names)
      and not _one.in_toc("Jingru_Kou", _names))

_no_col = TOC.TocSettings(columnKind="", columnName="旧栏目名", columnHost="旧主持",
                          columnArticles=["Ning_Wang"])
check("(b4) 无栏目 ⇒ 四个栏目字段一律忽略，收录口径为全部篇目",
      _no_col.column_validation_error() == ""
      and all(_no_col.in_toc(n, _names) for n in _names))


# ── (c) 英文栏目标签（B-364）：只用 columnNameEN ────────────────────────
print("\n── (c) 英文栏目标签：Column / Special Issue，只用 columnNameEN ──")

_col = TOC.TocSettings(**base_fields())
_alb = TOC.TocSettings(**base_fields(columnKind="专辑", columnName="偏好逻辑",
                                     columnNameEN="Preference Logic", columnHost="刘奋荣"))
check("(c1) 专栏 ⇒ Column: <columnNameEN>，不含中文名",
      _col.english_column_title == "Column: Cognitive Paradox Studies"
      and "认知悖论研究" not in _col.english_column_title,
      _col.english_column_title)
check("(c2) 专辑 ⇒ Special Issue: <columnNameEN>，不含中文名",
      _alb.english_column_title == "Special Issue: Preference Logic"
      and "偏好逻辑" not in _alb.english_column_title,
      _alb.english_column_title)
check("(c3) make_column_block 英文分支单点使用该标签（沿用模板间距 6.2mm）",
      TT.make_column_block(_alb, ["\\large  \\bf{Special Issue: Old}", "\\\\[6.2mm]"], False)
      == ["\\large  \\bf{Special Issue: Preference Logic} ", "\\\\[6.2mm]"])
check("(c4) 英文分支缺模板栏目块 ⇒ 名称行 + 默认间距 3mm",
      TT.make_column_block(_alb, [], False)
      == ["\\large  \\bf{Special Issue: Preference Logic} ", "\\\\[3mm]"])
check("(c5) 中文栏目块不受影响：专辑：X + 主持人：Y",
      TT.make_column_block(_alb, ["{\\large \\bf{专辑：旧}}", "&\\textit{\\large 主持人：旧}", "\\\\[3.2 ex]"], True)
      == ["{\\large \\bf{专辑：偏好逻辑}}", "&\\textit{\\large 主持人：刘奋荣}", "\\\\[3.2 ex]"])


# ── (d) B-363：栏目内两行标题的续行缩进 ────────────────────────────────
print("\n── (d) B-363：栏目内中文两行标题续行用行首安全缩进 ──")

_e = TOC.TocEntry(folderName="Hao_Wu", page="33", chineseTitle=LONG_ZH,
                  chineseAuthors=["吴浩"], englishAuthors=["Wu Hao"],
                  englishTitle="Axiomatization of Structural Norms")
_col_lines = TOC._zh_title_lines(_e, 10, True)
_plain_lines = TOC._zh_title_lines(_e, 10, False)
check("(d1) 合成标题确实触发两行分支（栏目/非栏目都一样断）",
      len(_col_lines) == 2 and len(_plain_lines) == 2, repr(_col_lines))

_col_tex = TOC.chinese_entry(_e, True, 10)
_plain_tex = TOC.chinese_entry(_e, False, 10)
check("(d2) 栏目两行：第二行用 \\phantom{~~~~ } 渲染期复制首行前缀（不写死 19.56001）",
      ("{\\large \\phantom{~~~~ }" + _col_lines[1] + "}") in _col_tex
      and "\\hspace*" not in _col_tex and "19.56001" not in _col_tex,
      _col_tex)
check("(d3) 栏目两行：首行仍是 ~~~~ 前缀；第二行不再是 \\newline 后跟 ~~~~",
      ("{\\large ~~~~ " + _col_lines[0] + "}") in _col_tex
      and "\\newline{\\large ~~~~ " not in _col_tex
      and "\\newline{\\large \\phantom{~~~~ }" in _col_tex)
check("(d4) 非栏目两行：续行保持原样（无 \\phantom/\\hspace*、无 ~~~~），首行无前缀",
      "\\phantom" not in _plain_tex and "\\hspace*" not in _plain_tex
      and "~~~~" not in _plain_tex
      and ("{\\large " + _plain_lines[0] + "}") in _plain_tex
      and ("\\newline{\\large " + _plain_lines[1] + "}") in _plain_tex,
      _plain_tex)

# 副标题固定拆行（——）走同一条续行缩进，不能只在超长标题折行时对
_sub = TOC.TocEntry(folderName="Jingru_Kou", page="17",
                    chineseTitle="偏好逻辑的动态语义——基于动态认知逻辑的扩展",
                    chineseAuthors=["寇静茹"], englishAuthors=["Kou Jingru"],
                    englishTitle="Dynamic Semantics for Preference Logic")
_sub_col = TOC.chinese_entry(_sub, True, 20)
_sub_plain = TOC.chinese_entry(_sub, False, 20)
check("(d6) 副标题续行（——）同样用 \\phantom{~~~~ } 对齐；非栏目无缩进盒",
      "\\newline{\\large \\phantom{~~~~ }——基于动态认知逻辑的扩展}" in _sub_col
      and "\\newline{\\large ——基于动态认知逻辑的扩展}" in _sub_plain
      and "\\phantom" not in _sub_plain,
      _sub_col)

# 英文条目结构不同：栏目内 ~~~~ 只在第一条作者前，标题行不带（避免二次折断）
_en_tex = TOC.english_entry(_e, True, 30)
check("(d5) 英文条目不受影响：标题行无 ~~~~，作者前有 ~~~~ 前缀",
      "\\large ~~~~" not in _en_tex
      and any((" ~~~~ " + a) in _en_tex for a in _e.englishAuthors[:1])
      and "\\hspace*" not in _en_tex
      and "\\phantom" not in _en_tex, _en_tex)


# ── (e) 端到端：专辑/专栏/无栏目/空勾选 ────────────────────────────────
print("\n── (e) 端到端生成：专辑全收 / 专栏勾选 / 空勾选 / 无栏目 ──")


def run_pair(settings: TOC.TocSettings, stamp: str) -> tuple[str, dict]:
    root2 = tempfile.mkdtemp(prefix="slcn_b375_e_")
    issue2 = make_issue(root2)
    rep2 = make_report(issue2)
    logs: list[str] = []
    outs = TOC.run(rep2, settings, log=logs.append, stamp=stamp)
    texts = {"en": "", "ch": ""}
    names = []
    if len(outs) == 2:
        names = [os.path.basename(p) for p in outs]
        texts["en"] = open(outs[0], encoding="utf-8").read()
        texts["ch"] = open(outs[1], encoding="utf-8").read()
    return texts, {"root": root2, "issue": issue2, "report": rep2, "outs": outs,
                   "logs": logs, "names": names}


_alb_full = TOC.TocSettings(**base_fields(columnKind="专辑", columnName="偏好逻辑",
                                          columnNameEN="Preference Logic",
                                          columnHost="刘奋荣",
                                          columnArticles=["Ning_Wang"]))
_txt, _st = run_pair(_alb_full, "ALB")
check("(e1) 专辑：生成两目录、全部三篇进目录且都带 ~~~~（stale subset 被忽略）",
      len(_st["outs"]) == 2 and _st["names"] == ["contents_ALB.tex", "Chinese Content_ALB.tex"]
      and all(t in _txt["ch"] for t in ("论示例悖论", "可知性悖论", "结构性规范"))
      and all(f"& {p}" in _txt["ch"] for p in ("1", "17", "33"))
      and sum(1 for _ln in _txt["ch"].split("\n") if _ln.startswith("{\\large ~~~~")) == 3,
      _txt["ch"][-800:])
check("(e2) 专辑：英文目录含 Special Issue 标签与全部三篇（每篇首位作者都带 ~~~~）",
      "Special Issue: Preference Logic" in _txt["en"]
      and "Knowability" in _txt["en"] and "Axiomatization" in _txt["en"]
      and all((" ~~~~ " + a) in _txt["en"] for a in ("Wang Ning", "Kou Jingru", "Wu Hao")),
      _txt["en"][-800:])
check("(e3) 专辑：日志报「（3 篇）」且不报缺项",
      any("（3 篇）" in ln for ln in _st["logs"])
      and not any("信息不完整" in ln for ln in _st["logs"]))
check("(e4) B-363：专辑（栏目）内长标题续行带 \\phantom{~~~~ }，非栏目不带",
      "{\\large \\phantom{~~~~ }" in _txt["ch"]
      and "\\hspace*" not in _txt["ch"])

_kong_txt, _kong_st = run_pair(_kong, "KONG")
check("(e5) 专栏空勾选：照样出两份文件、三篇都在目录里但都不带 ~~~~、无报错（无 min-1）",
      len(_kong_st["outs"]) == 2
      and all(t in _kong_txt["ch"] for t in ("论示例悖论", "可知性悖论", "结构性规范"))
      and "~~~~" not in _kong_txt["ch"]
      and "\\hspace*" not in _kong_txt["ch"]
      and "\\phantom" not in _kong_txt["ch"]
      and all("❌" not in ln for ln in _kong_st["logs"]))
check("(e6) 专栏空勾选：日志报「（0 篇）」且栏目标签仍在",
      any("（0 篇）" in ln for ln in _kong_st["logs"])
      and "Column: Cognitive Paradox Studies" in _kong_txt["en"])

_one_txt, _one_st = run_pair(_one, "ONE")
check("(e7) 专栏勾选一篇：三篇都列进目录，只有勾选的 Ning_Wang 带 ~~~~（英文同样）",
      len(_one_st["outs"]) == 2
      and all(t in _one_txt["ch"] for t in ("论示例悖论", "可知性悖论", "结构性规范"))
      and sum(1 for _ln in _one_txt["ch"].split("\n") if _ln.startswith("{\\large ~~~~")) == 1
      and "{\\large ~~~~ 论示例悖论" in _one_txt["ch"]
      and "~~~~ 可知性悖论" not in _one_txt["ch"]
      and all(t in _one_txt["en"] for t in
              ("On the Example Paradox", "Knowability", "Axiomatization"))
      and " ~~~~ Wang Ning" in _one_txt["en"]
      and " ~~~~ Kou Jingru" not in _one_txt["en"]
      and " ~~~~ Wu Hao" not in _one_txt["en"],
      _one_txt["ch"][-800:])

_no_txt, _no_st = run_pair(_no_col, "NONE")
check("(e8) 无栏目：全部三篇进目录、无 ~~~~ 前缀、栏目字段被忽略",
      len(_no_st["outs"]) == 2
      and all(t in _no_txt["ch"] for t in ("论示例悖论", "可知性悖论", "结构性规范"))
      and "~~~~" not in _no_txt["ch"]
      and "\\hspace*" not in _no_txt["ch"])


# ── (e8/e9) 专栏 stale 勾选：有效标记数 = 0 ⇒ 正文普通版式（Lead 复核边界）──
print("\n── (e8) 专栏 stale 勾选不算标记：正文普通版式、栏目行照旧 ──")


def _body(text: str) -> str:
    """取 %中间主体 之后的正文段（头部/栏目行不参与版式比对）。"""
    marker = "%中间主体"
    return text.split(marker, 1)[1] if marker in text else text


_plain_txt, _plain_st = run_pair(TOC.TocSettings(), "PLAIN")
_stale = TOC.TocSettings(**base_fields(columnArticles=["Not_In_This_Report"]))
_stale_txt, _stale_st = run_pair(_stale, "STALE")
check("(e8) 专栏 stale 勾选（别的报告篇名）⇒ 三篇全列、无 ~~~~ / hspace*、栏目行照旧",
      len(_stale_st["outs"]) == 2
      and all(t in _stale_txt["ch"] for t in ("论示例悖论", "可知性悖论", "结构性规范"))
      and "~~~~" not in _stale_txt["ch"]
      and "\\hspace*" not in _stale_txt["ch"]
      and "\\phantom" not in _stale_txt["ch"]
      and "~~~~" not in _stale_txt["en"]
      and "Column: Cognitive Paradox Studies" in _stale_txt["en"]
      and all("❌" not in ln for ln in _stale_st["logs"]),
      repr(_stale_st["logs"][-6:]))
check("(e8b) 专栏 stale 勾选 ⇒ 正文断行/缩进与无栏目完全一致（普通版式）",
      _body(_stale_txt["ch"]) == _body(_plain_txt["ch"])
      and _body(_stale_txt["en"]) == _body(_plain_txt["en"]),
      "stale body != plain body")
check("(e9) 对照：专栏勾中 1 篇 ⇒ 该篇带 ~~~~ + 栏目版式（正文与无栏目不同、续行 phantom）",
      sum(1 for _ln in _one_txt["ch"].split("\n")
          if _ln.startswith("{\\large ~~~~")) == 1
      and "\\phantom{~~~~ }" in _one_txt["ch"]
      and _body(_one_txt["ch"]) != _body(_plain_txt["ch"]),
      "hit-one body unexpectedly same as plain")


# ── (f) CLI：不完整设置 = 退出 2、不写结果 JSON；preview 带校验错误 ────
print("\n── (f) CLI：栏目不完整一律退出 2（不假成功），preview 仍 rc0 ──")

_cli_root = tempfile.mkdtemp(prefix="slcn_b375_f_")
_cli_issue = make_issue(_cli_root)
_cli_report = make_report(_cli_issue)
_settings_path = os.path.join(_cli_issue, TOC.TocSettings.fileName)
with open(_settings_path, "w", encoding="utf-8") as fh:
    json.dump(base_fields(columnNameEN=""), fh, ensure_ascii=False)
_out_json = os.path.join(_cli_root, "结果.json")
_proc = subprocess.run(
    [sys.executable, str(ENGINE / "toc.py"), "--report", _cli_report,
     "--result-json", _out_json],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
check("(f1) CLI 缺英文名 ⇒ 退出 2、stderr 报「信息不完整」、不写结果 JSON、无 .tex",
      _proc.returncode == 2 and "信息不完整" in _proc.stderr
      and not os.path.exists(_out_json)
      and not os.path.exists(TOC.toc_work_dir(_cli_issue))
      and tex_files(_cli_issue) == [],
      f"rc={_proc.returncode} err={_proc.stderr[-200:]!r}")

_prev = subprocess.run(
    [sys.executable, str(ENGINE / "toc.py"), "--report", _cli_report, "--preview"],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
_prev_payload = {}
try:
    _prev_payload = json.loads(_prev.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    pass
check("(f2) CLI preview ⇒ 退出 0 且带 columnEnabled=false + columnValidationError",
      _prev.returncode == 0 and _prev_payload.get("columnEnabled") is False
      and "信息不完整" in str(_prev_payload.get("columnValidationError", "")),
      repr(_prev.stdout[-200:]))

with open(_settings_path, "w", encoding="utf-8") as fh:
    json.dump(base_fields(columnKind="专辑", columnName="偏好逻辑",
                          columnNameEN="Preference Logic", columnHost="刘奋荣",
                          columnArticles=[]), fh, ensure_ascii=False)
_proc2 = subprocess.run(
    [sys.executable, str(ENGINE / "toc.py"), "--report", _cli_report,
     "--result-json", _out_json],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
_payload = {}
if os.path.exists(_out_json):
    _payload = json.load(open(_out_json, encoding="utf-8"))
check("(f3) CLI 专辑四项齐全 ⇒ 退出 0、结果 JSON articles={total:3,inToc:3}、两产物存在",
      _proc2.returncode == 0
      and _payload.get("articles") == {"total": 3, "inToc": 3}
      and _payload.get("column", {}).get("enabled") is True
      and os.path.isfile(_payload.get("outputs", {}).get("zhTex", ""))
      and os.path.isfile(_payload.get("outputs", {}).get("enTex", "")),
      f"rc={_proc2.returncode} payload={_payload.get('articles')}")

with open(_settings_path, "w", encoding="utf-8") as fh:
    json.dump(base_fields(columnArticles=["01 Ning_Wang"]), fh, ensure_ascii=False)
_out_json2 = os.path.join(_cli_root, "结果2.json")
_proc3 = subprocess.run(
    [sys.executable, str(ENGINE / "toc.py"), "--report", _cli_report,
     "--result-json", _out_json2],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
_payload2 = json.load(open(_out_json2, encoding="utf-8")) if os.path.exists(_out_json2) else {}
_zh_tex = str(_payload2.get("outputs", {}).get("zhTex", ""))
_zh_text = open(_zh_tex, encoding="utf-8").read() if os.path.isfile(_zh_tex) else ""
check("(f4) CLI 专栏勾选一篇 ⇒ 退出 0、结果 JSON articles={total:3,inToc:3}（全部列出、只标记）",
      _proc3.returncode == 0 and _payload2.get("articles") == {"total": 3, "inToc": 3}
      and all(t in _zh_text for t in ("论示例悖论", "可知性悖论", "结构性规范"))
      and sum(1 for _ln in _zh_text.split("\n") if _ln.startswith("{\\large ~~~~")) == 1,
      f"rc={_proc3.returncode} payload={_payload2.get('articles')}")


# ── (g) 宽度守卫告警不回归 ─────────────────────────────────────────────
print("\n── (g) 宽度守卫：超版心/右侧留白/负间距告警仍发出 ──")

_guard_settings = TOC.TocSettings()
_logs: list[str] = []
_long = TOC.TocEntry(folderName="x", page="1", chineseTitle="结" * 40,
                     chineseAuthors=["吴浩"], englishAuthors=["Wu Hao"])
TOC._log_layout_guard("head", [("x", _long)], _guard_settings, True, False, 200,
                      log=_logs.append)
check("(g1) 超版心 1.02× ⇒ 「超过版心」告警（口径不变）",
      any("超过版心" in ln for ln in _logs), repr(_logs))

_logs2: list[str] = []
_short = TOC.TocEntry(folderName="y", page="1", chineseTitle="短",
                      chineseAuthors=["吴浩"], englishAuthors=["Wu Hao"])
TOC._log_layout_guard("head", [("y", _short)], _guard_settings, True, False, 20,
                      log=_logs2.append)
check("(g2) 不足版心 0.92× ⇒ 「右侧可能留白」告警（口径不变）",
      any("右侧可能留白" in ln for ln in _logs2), repr(_logs2))

_logs3: list[str] = []
TOC._log_layout_guard("\\begin{tabularx}{\\textwidth}{XX} \\hspace{-2pt}",
                      [("y", _short)], _guard_settings, True, False, 20,
                      log=_logs3.append)
check("(g3) 模板含负间距 ⇒ 负间距告警（口径不变）",
      any("负间距" in ln for ln in _logs3), repr(_logs3))


# ── (h) 解析出 0 篇的报告：明确报错、不生成任何文件（B-375 验收）────────
print("\n── (h) 空报告/非报告：明确报错、不写文件 ──")

_zero_root = tempfile.mkdtemp(prefix="slcn_b375_e0_")
_zero_issue = make_issue(_zero_root)
_zero_report = os.path.join(_zero_issue, "信息检查2026_01_01_00_00_00.txt")
with open(_zero_report, "w", encoding="utf-8") as fh:
    fh.write("这不是一份信息检查报告\r\n随便几行文本\r\n")

_zero_logs: list[str] = []
_zero_out = TOC.generate_tex(_zero_report, "ch", TOC.TocSettings(), log=_zero_logs.append)
check("(h1) generate_tex 解析出 0 篇 ⇒ 返回 None、日志 ❌ 明确报错",
      _zero_out is None
      and any("没有找到任何稿件条目" in ln and "❌" in ln for ln in _zero_logs),
      repr(_zero_logs))
check("(h2) generate_tex 解析出 0 篇 ⇒ 不建工作目录、不写任何 .tex",
      not os.path.isdir(os.path.join(_zero_issue, ".toolbox-tmp"))
      and not list(pathlib.Path(_zero_issue).rglob("*.tex")),
      repr([str(p) for p in pathlib.Path(_zero_issue).rglob("*.tex")]))

_zero_json = os.path.join(_zero_root, "r.json")
_proc_zero = subprocess.run(
    [sys.executable, str(ENGINE / "toc.py"), "--report", _zero_report,
     "--result-json", _zero_json],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
check("(h3) CLI 解析出 0 篇 ⇒ 退出 2、stderr 报错、不写结果 JSON/不写 .tex",
      _proc_zero.returncode == 2
      and "信息检查" in _proc_zero.stderr
      and not os.path.exists(_zero_json)
      and not list(pathlib.Path(_zero_issue).rglob("*.tex")),
      f"rc={_proc_zero.returncode} stderr={_proc_zero.stderr[-300:]!r}")

_proc_zero_pv = subprocess.run(
    [sys.executable, str(ENGINE / "toc.py"), "--report", _zero_report, "--preview"],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
check("(h4) CLI --preview 解析出 0 篇 ⇒ 退出 2、不输出假 JSON",
      _proc_zero_pv.returncode == 2 and not _proc_zero_pv.stdout.strip(),
      f"rc={_proc_zero_pv.returncode} stdout={_proc_zero_pv.stdout[:120]!r}")


# ── (i) D3：把 .tex 复制成报告 ⇒ rc2、明确报错、零写入（验收冻结 1）────
print("\n── (i) 假报告（.tex 复制成 .txt）：rc2、明确报错、零写入 ──")

_i_root = tempfile.mkdtemp(prefix="slcn_b375_i_")
_i_issue = make_issue(_i_root)
_i_report = os.path.join(_i_issue, "fake_report.txt")
with open(_i_report, "w", encoding="utf-8") as fh:
    fh.write("\\documentclass{article}\n"
             "\\newcommand{\\myissnumber}{19}\n"
             "\\newcommand{\\mystartpage}{1} % 输入起始页码\n"
             "\\begin{document}\n\\end{document}\n")
_i_err = TOC.report_validation_error(_i_report)
check("(i1) 假报告的报告校验返回明确错误（不是空串）",
      "信息检查" in _i_err and "没有生成任何文件" in _i_err, _i_err)
_i_logs: list[str] = []
_i_out = TOC.generate_tex(_i_report, "ch", TOC.TocSettings(), log=_i_logs.append)
check("(i2) generate_tex 拒绝假报告 ⇒ 返回 None、日志 ❌ 报错、不写 .tex/工作目录",
      _i_out is None
      and any("❌" in ln and "信息检查" in ln for ln in _i_logs)
      and not os.path.exists(TOC.toc_work_dir(_i_issue))
      and tex_files(_i_issue) == [],
      repr(_i_logs[-3:]))
_i_json = os.path.join(_i_root, "结果.json")
_i_proc = subprocess.run(
    [sys.executable, str(ENGINE / "toc.py"), "--report", _i_report,
     "--result-json", _i_json],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
check("(i3) CLI 假报告 ⇒ rc2、stderr 明确报错、不写结果 JSON/不写 .tex",
      _i_proc.returncode == 2 and "信息检查" in _i_proc.stderr
      and not os.path.exists(_i_json) and tex_files(_i_issue) == []
      and not os.path.exists(TOC.toc_work_dir(_i_issue)),
      f"rc={_i_proc.returncode} err={_i_proc.stderr[-300:]!r}")
_i_good = make_report(_i_issue)
check("(i4) 合法《信息检查》报告仍通过校验（新守卫不误伤）",
      TOC.report_validation_error(_i_good) == "")
_i_good_logs: list[str] = []
_i_good_out = TOC.generate_tex(_i_good, "ch", TOC.TocSettings(), log=_i_good_logs.append)
check("(i5) 合法报告仍能生成中文目录（返回路径存在）",
      _i_good_out is not None and os.path.isfile(_i_good_out), repr(_i_good_logs[-3:]))

# ── (i6) 老夹具兼容：目录名后隔空行 ⇒ split_articles 取到空目录名，字段齐全放行（B-115 口径）──
_i_blank = os.path.join(_i_root, "blank_folder_report.txt")
with open(_i_blank, "w", encoding="utf-8") as fh:
    fh.write("WeiNong_Li\n"
             "起始页码：1  终止页码：10  页数：10\n"
             "字号 10.5t\n"
             "第一作者中文: \\kaishu 邱德钧\n"
             "中文标题: 测试中文题名一\n"
             "\n"
             "Hao_Wu\n"
             "起始页码：11  终止页码：20  页数：10\n"
             "字号 11pt\n"
             "第一作者中文: \\kaishu 吴昊\n"
             "中文标题: 测试中文题名二\n")
_i_blank_logs: list[str] = []
check("(i6) 老式夹具（目录名后空行⇒空目录名）不被误判：校验通过且能出目录",
      TOC.report_validation_error(_i_blank) == ""
      and TOC.generate_tex(_i_blank, "ch", TOC.TocSettings(),
                           log=_i_blank_logs.append) is not None,
      TOC.report_validation_error(_i_blank) + repr(_i_blank_logs[-2:]))

# ── (j) D4：columnKind 纯空白 = 本期无栏目，但必须明确提示（不静默）────
print("\n── (j) columnKind 纯空白：按无栏目处理并打印提示（不静默）──")

_j_settings = TOC.TocSettings(**base_fields(columnKind="   "))
_j_note = _j_settings.column_kind_note()
check("(j1) kind 纯空白 ⇒ enabled=false、column_editing=false、校验无错、提示文案非空",
      not _j_settings.is_column_enabled and not _j_settings.column_editing
      and _j_settings.column_validation_error() == ""
      and "栏目类型" in _j_note and "本期无栏目" in _j_note, _j_note)
_j_root = tempfile.mkdtemp(prefix="slcn_b375_j_")
_j_issue = make_issue(_j_root)
_j_report = make_report(_j_issue)
with open(os.path.join(_j_issue, TOC.TocSettings.fileName), "w", encoding="utf-8") as fh:
    json.dump(base_fields(columnKind="   "), fh, ensure_ascii=False)
_j_json = os.path.join(_j_root, "结果.json")
_j_proc = subprocess.run(
    [sys.executable, str(ENGINE / "toc.py"), "--report", _j_report,
     "--result-json", _j_json],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
_j_stdout = _j_proc.stdout
_jp = json.load(open(_j_json, encoding="utf-8")) if os.path.exists(_j_json) else {}
_jp_path = str(_jp.get("outputs", {}).get("zhTex", ""))
_j_tex = open(_jp_path, encoding="utf-8").read() if os.path.isfile(_jp_path) else ""
check("(j2) CLI kind 纯空白 ⇒ rc0 按无栏目出目录、产物无 ~~~~/phantom、stdout 有明确提示",
      _j_proc.returncode == 0
      and "栏目类型" in _j_stdout and "本期无栏目" in _j_stdout
      and "~~~~" not in _j_tex and "\\phantom" not in _j_tex
      and "❌" not in _j_proc.stderr,
      f"rc={_j_proc.returncode} out={_j_stdout[-160:]!r} err={_j_proc.stderr[-160:]!r}")
with open(os.path.join(_j_issue, TOC.TocSettings.fileName), "w", encoding="utf-8") as fh:
    json.dump(base_fields(columnName="   "), fh, ensure_ascii=False)
_j2_json = os.path.join(_j_root, "结果2.json")
_j2 = subprocess.run(
    [sys.executable, str(ENGINE / "toc.py"), "--report", _j_report,
     "--result-json", _j2_json],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
check("(j3) 专栏 + 空白栏目名称 ⇒ rc2、stderr 信息不完整、不写结果 JSON",
      _j2.returncode == 2 and "信息不完整" in _j2.stderr
      and not os.path.exists(_j2_json),
      f"rc={_j2.returncode} err={_j2.stderr[-200:]!r}")


print()
if fails:
    print(f"失败 {len(fails)} 项：" + "；".join(fails))
    sys.exit(1)
print(f"全部通过：{pass_n} 项")
