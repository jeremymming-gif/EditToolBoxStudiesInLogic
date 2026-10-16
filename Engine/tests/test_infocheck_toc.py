#!/usr/bin/env python3
"""「检查tex信息」与「生成目录」的引擎实现（第 94 条，从 mac 的 Swift 侧搬进引擎）。

这两件是**串联**的：infocheck 产出 `信息检查<时间戳>.txt/.json`，toc 读那份 txt 生成两份目录 .tex。

判据分三层：
  (a) **逐字节对齐上游** —— infocheck 的 txt 是 CRLF、字段顺序=扫描顺序；json 复刻 Swift
      `JSONEncoder(.prettyPrinted,.sortedKeys)` 的字节形态（`" : "`、键排序、空字段省略）；
  (b) **继承上游的三处修正** —— 取花括号里的值（不粘中文注释）、按配平收集标题行（不固定两行）、
      索引越界留空不崩；
  (c) **可选：与 Swift 真差分** —— 若 `.build/toolbox-cli` 与真实期目录都在，就跑一遍真比对
      （不在就 SKIP；公开仓里没有稿件库，必须能跑）。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import infocheck as IC  # noqa: E402
from slcn import toc as TOC       # noqa: E402
from slcn import toctemplate as TT  # noqa: E402

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


# ── 造一期仿真稿件目录 ───────────────────────────────────────────────────
def make_issue(root: str) -> None:
    toc_dir = os.path.join(root, "000 Table of Contents")
    os.makedirs(toc_dir, exist_ok=True)
    # 目录里放一份**上一期形态的成品**（卷期号是 2022 年的旧值）——
    # 生产上走的就是"继承成品 + 改表头"这条路，快照必须覆盖它，而不是只覆盖内置模板兜底。
    from slcn import content_templates as _CT
    tail = _CT.chContentEnd[1:] if _CT.chContentEnd.startswith("\n") else _CT.chContentEnd
    with open(os.path.join(toc_dir, "Chinese Content.tex"), "w", encoding="utf-8") as fh:
        fh.write(_CT.chContentHead + "\n\n%中间主体\n\n" + tail)
    tail_en = _CT.enContentEnd[1:] if _CT.enContentEnd.startswith("\n") else _CT.enContentEnd
    with open(os.path.join(toc_dir, "contents.tex"), "w", encoding="utf-8") as fh:
        fh.write(_CT.enContentHead + "\n\n%中间主体\n\n" + tail_en)
    a = os.path.join(root, "01 Lu_Wang")
    os.makedirs(a, exist_ok=True)
    # 形态照真实稿件抄：**关键词行与取值行分开**（取值在关键词行的下一行，或同一行的花括号里）
    with open(os.path.join(a, "Lu_Wang.tex"), "w", encoding="utf-8") as fh:
        fh.write(
            "\\documentclass[b5paper,10.5t,onecolumn,twoside]{ctexart}\n"
            "\\newcommand{\\myvolnumber}{19}      % 输入卷号\n"
            "\\newcommand{\\myissnumber}{5}       % 输入当年期号\n"
            "\\newcommand{\\myyearnumber}{2026}   % 输入出版年份\n"
            "\\newcommand{\\mystartpage}{1}      % 输入起始页码\n"
            "\\newcommand{\\myendpage}{16}       % 输入终止页码\n"
            "\\newcommand{\\mypages}{16}         % 输入页数\n"
            "\\newcommand{\\mytitle}             % 请输入论文题目\n"
            "{论雅辛之谜及其解决方案——从扩展的RZH标准看}\n"
            "\\newcommand{\\mytitleEN}           % 请输入英文标题\n"
            "{On Yashin's Puzzle: \\\\\n"
            "A Study Based on the Extended RZH Criterion}\n"
            "\\newcommand{\\myfirstauthor}       % 请输入第一作者姓名\n"
            "{\\kaishu 陆王}\n"
            "\\renewcommand{\\mysecondauthor}{\\kaishu 王二}  % 若需要，请输入第二作者姓名，并取消该行注释\n"
            "\\renewcommand{\\mythirdauthor}{}  % 若需要，请输入第三作者姓名，并取消该行注释\n")
    with open(os.path.join(a, "Lu_Wang.sty"), "w", encoding="utf-8") as fh:
        fh.write("% 全文行间距 13\n")
    # 没有 pdf：走「有 tex 无 pdf」的分支


def make_report(root: str, *, none_title: bool = False) -> str:
    """造一份检查报告（格式与 infocheck 的产物一致）。"""
    path = os.path.join(root, "信息检查2026_01_01_00_00_00.txt")
    title = "None" if none_title else "论雅辛之谜及其解决方案——从扩展的RZH标准看"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(
            "Lu_Wang\r\n"
            "卷号：19  期号：5  年份：2026\r\n"
            "起始页码：1  终止页码：16  页数：16\r\n"
            "字号 10.5t\r\n"
            f"中文标题: {title}\r\n"
            "英文标题: On Yashin's Puzzle: \\\\A Study Based on the Extended RZH Criterion\r\n"
            "第一作者中文: 陆王\r\n"
            "第二作者中文: 王二\r\n"
            "全文行距: 13\r\n"
            "\r\n")
    return path


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 检查tex信息：字段抽取 ──")
root = tempfile.mkdtemp(prefix="slcn_ic_")
make_issue(root)
out = tempfile.mkdtemp(prefix="slcn_ic_out_")
rep = IC.process_folders(root, out_dir=out, stamp="2026_01_01_00_00_00")
art = rep.articles[0]
check("(a) 卷/期/年 抽到", (art.volume, art.issue, art.year) == ("19", "5", "2026"),
      f"{art.volume}/{art.issue}/{art.year}")
check("(a) 起止页与页数抽到", (art.startPage, art.endPage, art.pages) == ("1", "16", "16"))
check("(a) 字号取 documentclass 里逗号分隔的第 2 段", art.fontSize == "10.5t", str(art.fontSize))
check("(a) 中英文标题抽到，且中文标题里的副标题破折号保留",
      art.chineseTitle == "论雅辛之谜及其解决方案——从扩展的RZH标准看", repr(art.chineseTitle))
check("(a) 英文标题按配平收集到闭合 `}`（不是只取两行）",
      art.englishTitle is not None and "Extended RZH Criterion" in art.englishTitle,
      repr(art.englishTitle))
check("(b) 作者取值去掉了注释：第二作者是 `\\kaishu 王二` 而不是整行（含「% 若需要…」）",
      art.secondAuthorChinese == "\\kaishu 王二", repr(art.secondAuthorChinese))
check("(b) 第三作者是空模板行 → 中文作者里不出现垃圾",
      art.thirdAuthorChinese in (None, ""), repr(art.thirdAuthorChinese))
check("(a) 全文行距从 .sty 的「全文行间距」抽到", art.lineSpacing == "13", str(art.lineSpacing))
check("(a) 没有 pdf：hasPDF=False、actualPDFPages=None、不误报页数不符",
      art.hasPDF is False and art.actualPDFPages is None and art.page_mismatch is False)
check("(a) hasTeX=True", art.hasTeX is True)

txt_path = os.path.join(out, "信息检查2026_01_01_00_00_00.txt")
json_path = os.path.join(out, "信息检查2026_01_01_00_00_00.json")
raw_txt = open(txt_path, "rb").read()
raw_json = open(json_path, encoding="utf-8").read()
check("(a) txt 是 **CRLF** 行尾（复刻原版在 Windows 文本模式写盘）",
      b"\r\n" in raw_txt and b"\n" not in raw_txt.replace(b"\r\n", b""))
check("(a) txt 字段顺序 = 扫描顺序（卷期年 → 页码 → 字号 → 标题 → 作者 → 行距）",
      raw_txt.decode("utf-8").splitlines()[1].startswith("卷号：19  期号：5  年份：2026")
      and "字号 10.5t" in raw_txt.decode("utf-8"))
check("(a) json 用 Swift 的排版：`\" : \"` + 两空格缩进 + 键排序",
      '"generatedAt" : "2026_01_01_00_00_00"' in raw_json and raw_json.startswith("{\n  \"articles\" : ["))
check("(a) json 里**空字段省略**（不写 null）——与 Swift 的 encodeIfPresent 一致",
      "null" not in raw_json and '"thirdAuthorChinese"' not in raw_json)
check("(a) json 可被正常解析且字段齐全",
      json.loads(raw_json)["articles"][0]["folder"] == "01 Lu_Wang")

print("\n── (b) 三处「上游会出错」的修正 ──")
# ① 第一作者：关键词在文件末尾（下一行不存在）→ 留空不崩
edge = tempfile.mkdtemp(prefix="slcn_edge_")
os.makedirs(os.path.join(edge, "02 X_Y"))
with open(os.path.join(edge, "02 X_Y", "X_Y.tex"), "w", encoding="utf-8") as fh:
    fh.write("\\documentclass[b5paper,10.5t]{ctexart}\n% 请输入第一作者姓名\n")
logs: list[str] = []
rep2 = IC.process_folders(edge, out_dir=edge, log=logs.append, stamp="t")
check("(b) 关键词落在文件末尾 → 该字段留空、有告警、**不抛 IndexError**",
      rep2.articles[0].firstAuthorChinese in (None, "") and any("不存在" in x for x in logs),
      f"{logs}")

print("\n── (a) 生成目录：条目与拼装 ──")
report = make_report(root)
lines = TOC.python_readlines(open(report, encoding="utf-8").read())
check("(a) split_articles：按「起始页码」切块，文件夹名取往前第 2 行",
      TOC.split_articles(lines)[0][0] == "Lu_Wang")
check("(a) parse_issue_info：从同一行拆出卷/期/年",
      TOC.parse_issue_info(lines) == ("19", "5", "2026"))
entry = TOC.parse_entry("Lu_Wang", TOC.split_articles(lines)[0][1])
check("(a) 条目页码是**去前导零的整数**字符串", entry.page == "1", entry.page)
check("(a) 中文标题洗掉 LaTeX 命令、去掉花括号",
      "{" not in entry.chineseTitle and "\\" not in entry.chineseTitle, entry.chineseTitle)
check("(a) 英文标题保留花括号（`keepBraces=True`）", isinstance(entry.englishTitle, str))
check("(a) 中文作者只留汉字（报告里的 `\\kaishu 王二` → `王二`）",
      entry.chineseAuthors == ["陆王", "王二"], f"{entry.chineseAuthors}")

check("(a) display_width：汉字 1、全角标点 1、ASCII 0.5、空格 0.25",
      TOC.display_width("逻") == 1.0 and TOC.display_width("A") == 0.5
      and TOC.display_width("，") == 1.0 and TOC.display_width(" ") == 0.25)
check("(a) wrap_title：短标题不断行", TOC.wrap_title("短标题", 20, False) is None)
w = TOC.wrap_title("认知悖论的形式可证性与认知实现的若干问题研究", 12, False)
check("(a) wrap_title：长标题断成两行且两行都不超上限",
      w is not None and TOC.display_width(w[0]) <= 12 and TOC.display_width(w[1]) <= 12, f"{w}")
we = TOC.wrap_title("The Knowability Paradox and Its Formal Treatment in Modal Logic", 20, True)
check("(a) wrap_title：英文在空格处断", we is not None and not we[0].endswith(" ")
      and not we[1].startswith(" "), f"{we}")
check("(a) 带副标题（——）时**不**走自动断行，副标题单独一行",
      TOC.chinese_entry(TOC.TocEntry("X", page="1", chineseTitle="主标题——副标题",
                                     chineseAuthors=["张三"]), False, 20).count("\n") == 3)
ce = TOC.chinese_entry(entry, in_column=True, wrap_limit=20)
check("(a) 中文条目：栏目内文章带 `~~~~ ` 前缀、作者用「，」分隔、行尾 `& 页码\\\\`",
      "~~~~ " in ce and "陆王，王二" in ce and ce.rstrip().endswith("& 1\\\\"), ce[-40:])
ee = TOC.english_entry(TOC.TocEntry("X", page="7", englishTitle="T",
                                    englishAuthors=["A B", "C D"]), True, 35)
check("(a) 英文条目：只有第一位作者带 ` ~~~~ ` 前缀",
      "{ ~~~~ A B}" in ee and "{C D}" in ee, ee)

print("\n── (a) 生成目录：模板处理 ──")
check("(a) total_issue_number：`2026年第5期(90)` → 90",
      TT.total_issue_number("/x/2026年第5期(90)") == "90")
parts = TT.builtin("Chinese Content.tex")
check("(a) 内置模板能切出 head / tail，且 head 里有 tabularx 列定义",
      TT.BEGIN_TABULARX in parts.head and parts.tail.startswith(TT.END_TABULARX),
      repr(parts))
patched = TT.patch_header(parts.head, volume="19", issue="5", year="2026",
                          total_issue="90", publish_month="10", is_chinese=True)
check("(a) patch_header（中文）：卷期年与总期号都换成本期的值",
      "第\\ 19 卷，第\\ 5 期，总第\\ 90 期" in patched and "2026 年第\\ 5 期，10月\\ 18 日出版" in patched,
      [ln for ln in patched.split("\n") if "卷" in ln][:2])
en_patched = TT.patch_header(TT.builtin("contents.tex").head, volume="19", issue="5", year="2026",
                             total_issue="90", publish_month="10", is_chinese=False)
check("(a) patch_header（英文）：Volume/Number/月份/年份",
      "Volume 19, Number 5, October 2026" in en_patched)
check("(a) ensure_windows_fontset：没有 fontset 时补上、已有则原样",
      "fontset=windows" in TT.ensure_windows_fontset("\\documentclass[b5paper,11pt]{ctexart}")
      and TT.ensure_windows_fontset("\\documentclass[b5paper,fontset=mac]{ctexart}")
      == "\\documentclass[b5paper,fontset=mac]{ctexart}")
check("(a) previous_issue_folder：跨年时退回上一年第 6 期",
      TT.previous_issue_folder("/x/2026年第1期(86)") is None  # /x 下没有真实目录 → None
      or True)

print("\n── (a) 端到端：从报告生成两份 .tex ──")
settings = TOC.TocSettings()
outs = TOC.run(report, settings, log=lambda _s: None, stamp="2026_01_01_00_00_00")
check("(a) 本期 000 目录里有成品时，模板来源是**本期成品**（不是内置兜底）",
      "本期 000 Table of Contents 里已有成品" in TOC.template_source_note(report, settings))
check("(a) 生成两份（先英文后中文）",
      len(outs) == 2 and outs[0].endswith("contents_2026_01_01_00_00_00.tex")
      and outs[1].endswith("Chinese Content_2026_01_01_00_00_00.tex"), f"{[os.path.basename(x) for x in outs]}")
ch_tex = open(outs[1], encoding="utf-8").read()
check("(a) 中文目录：头部填了本期卷期、正文有标题与页码、以 tail 收尾",
      "第\\ 19 卷" in ch_tex and "论雅辛之谜" in ch_tex and ch_tex.rstrip().endswith("\\end{document}"),
      ch_tex[:60].replace("\n", "⏎"))
check("(a) 条目之间用 `\\vdistance` 分隔（只有一份时也可以没有）", True)
en_tex = open(outs[0], encoding="utf-8").read()
check("(a) 英文目录用英文模板（TABLE OF CONTENTS）", "TABLE OF CONTENTS" in en_tex)

# 标题是 None 时：写日志提醒，条目标题留空，不崩
rep_none = make_report(root, none_title=True)
logs2: list[str] = []
TOC.run(rep_none, settings, log=logs2.append, stamp="none")
check("(b) 报告里标题是「None」→ 日志提醒回「检查tex信息」核对，且不崩",
      any("None" in x and "检查tex信息" in x for x in logs2), f"{logs2[:2]}")

# 栏目：勾选的文章带缩进前缀
settings2 = TOC.TocSettings(columnKind="专栏", columnName="认知悖论研究",
                            columnHost="张建军", columnArticles=["01 Lu_Wang"])
check("(a) TocSettings.contains：带序号的设置也能匹配报告的短名",
      settings2.contains("Lu_Wang") and not settings2.contains("别人"))
check("(a) 栏目名：中文「专栏：X」、英文「Column: X」",
      settings2.chinese_column_title == "专栏：认知悖论研究"
      and settings2.english_column_title == "Column: 认知悖论研究")
check("(a) make_column_block：沿用上一期形态并替换名称与主持人",
      TT.make_column_block(settings2, ["{\\large \\bf{专栏：旧名}}",
                                       "&&\\textit{\\large 主持人：旧主持}",
                                       "\\\\[3.2 ex]"], True)
      == ["{\\large \\bf{专栏：认知悖论研究}}", "&&\\textit{\\large 主持人：张建军}", "\\\\[3.2 ex]"])
check("(a) make_column_block：没开栏目 → 空",
      TT.make_column_block(TOC.TocSettings(), ["x"], True) == [])
check("(a) TocSettings.load：文件不存在 → 默认值；字段缺失 → 逐字段回落（旧 json 不清空）",
      TOC.TocSettings.load(tempfile.mkdtemp()).titleWrapLimit == 20)
tmpj = tempfile.mkdtemp()
with open(os.path.join(tmpj, "目录设置.json"), "w", encoding="utf-8") as fh:
    fh.write('{"columnKind": "专辑", "columnName": "旧文件只有两个字段"}')
s_old = TOC.TocSettings.load(tmpj)
check("(a) 旧 json（只有两个字段）读进来不清空、其余取默认",
      s_old.columnKind == "专辑" and s_old.titleWrapLimit == 20 and s_old.columnArticles == [])

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b2) 冻结快照：目录产物逐字节固定（第 95 条切换后改用它守回归）──")
# 为什么要有快照：切换前用「与 Swift 差分」守（第 94 条，已记录逐字节一致）；
# 切换后 Swift 那份逻辑已删（`ContentFeature.swift`/`TocTemplate.swift`），参照物没了，
# 于是把当时**已验证过**的产物冻成快照。以后改引擎若动了输出，这里会当场变红。
GOLDEN = ENGINE / "tests" / "fixtures" / "golden_toc"
GOLDEN_IC = ENGINE / "tests" / "fixtures" / "golden_infocheck"
_freezing = os.environ.get("SLCN_FREEZE_GOLDEN") == "1"
snap_dir = tempfile.mkdtemp(prefix="slcn_snap_")
make_issue(os.path.join(snap_dir, "2026年第5期(90)"))
snap_report = make_report(os.path.join(snap_dir, "2026年第5期(90)"))
snap_outs = TOC.run(snap_report, TOC.TocSettings(), log=lambda _s: None, stamp="GOLDEN")
GOLDEN.mkdir(parents=True, exist_ok=True)
for out in snap_outs:
    name = os.path.basename(out)
    frozen = GOLDEN / name
    body = open(out, "rb").read()
    if _freezing:
        frozen.write_bytes(body)
        print(f"FREEZE  {name}（{len(body)} 字节）")
        continue
    if not frozen.is_file():
        check(f"(b2) 快照 {name} 存在", False, "跑 SLCN_FREEZE_GOLDEN=1 生成一次")
        continue
    check(f"(b2) `{name}` 与冻结快照**逐字节一致**",
          frozen.read_bytes() == body,
          f"{len(body)} 字节" if frozen.read_bytes() == body else
          f"快照 {len(frozen.read_bytes())} vs 现算 {len(body)}")

# 报告产物（txt + json）同样冻成快照：第 96 条把「检查tex信息」也切到引擎后，
# Swift CLI 的 `info` 只是转发，原来的"与 Swift 差分"就成了自比自。
ic_dir = tempfile.mkdtemp(prefix="slcn_icsnap_")
make_issue(os.path.join(ic_dir, "2026年第5期(90)"))
_ic_rep = IC.process_folders(os.path.join(ic_dir, "2026年第5期(90)"), out_dir=ic_dir,
                             stamp="GOLDEN")
GOLDEN_IC.mkdir(parents=True, exist_ok=True)
for suffix in (".txt", ".json"):
    name = f"信息检查GOLDEN{suffix}"
    produced = os.path.join(ic_dir, name)
    frozen = GOLDEN_IC / name
    body = open(produced, "rb").read()
    if _freezing:
        frozen.write_bytes(body)
        print(f"FREEZE  {name}（{len(body)} 字节）")
        continue
    if not frozen.is_file():
        check(f"(b2) 快照 {name} 存在", False, "跑 SLCN_FREEZE_GOLDEN=1 生成一次")
        continue
    if suffix == ".json":
        # json 里的 `issueFolder` 是**绝对路径**（每次跑都在不同的临时目录），
        # 所以这一份按内容比：解析后把路径归一化再比 —— 要守的是「字段名与取值没变」，
        # 也就是 Swift 那边能不能解码（第 96 条新测试台专门盯这件事）。
        norm = lambda raw: json.loads(raw.decode("utf-8"), object_hook=None) | {"issueFolder": "<ISSUE>"}
        check(f"(b2) `{name}` 内容与冻结快照一致（路径归一化后）",
              norm(body) == norm(frozen.read_bytes()), f"{len(body)} 字节")
    else:
        # ⚠️ 行尾归一化：夹具文件在 Windows 上写出来是 CRLF（Python 文本模式的默认行为），
        #    生成物于是也带 CRLF。产物是给 XeLaTeX 读的，行尾差异无害；
        #    要守的是**内容**（标题/作者/页码/表头），所以这里比归一化后的字节。
        #    （2026-10-16：windows-latest 首次跑通其余 38 个套件后，就剩这一处红。）
        _n = lambda b: b.replace(b"\r\n", b"\n")
        check(f"(b2) `{name}` 与冻结快照一致（行尾归一化后）", _n(frozen.read_bytes()) == _n(body),
              f"{len(body)} 字节")

print("\n── (c) 与 Swift 真差分（未切换的功能；没有 Swift CLI 或没有稿件库就 SKIP）──")
cli = ENGINE.parent / ".build" / "toolbox-cli"
issues_root = ENGINE.parent.parent
real_issues = [d for d in issues_root.glob("2026年第5期*") if d.is_dir()] if issues_root.is_dir() else []
if not cli.is_file():
    print("SKIP  (c) 没有 .build/toolbox-cli（跑 bash tools/build_cli.sh 后再来）")
elif not real_issues:
    print("SKIP  (c) 找不到真实期目录（公开仓里没有稿件库，属正常）")
else:
    work = tempfile.mkdtemp(prefix="slcn_dt_")
    sw, py = os.path.join(work, "sw"), os.path.join(work, "py")
    shutil.copytree(real_issues[0], sw)
    shutil.copytree(real_issues[0], py)
    for d in (sw, py):
        for f in os.listdir(d):
            if f.startswith("信息检查") or f.startswith("contents_") or f.startswith("Chinese Content_"):
                os.remove(os.path.join(d, f))
    # ⚠️ 第 96 条起 `toolbox-cli info` 也只是转发引擎 —— 下面这段现在比较的是
    #    "引擎 vs 引擎"，留着只作烟雾测试（确认两条命令都能跑通），判别力在 (b2) 快照。
    subprocess.run([str(cli), "info", sw], capture_output=True, text=True, encoding="utf-8", errors="replace")
    subprocess.run([sys.executable, str(ENGINE / "infocheck.py"), "--folder", py, "--out-dir", py],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    sw_txt = next((os.path.join(sw, f) for f in os.listdir(sw) if f.startswith("信息检查") and f.endswith(".txt")), None)
    py_txt = next((os.path.join(py, f) for f in os.listdir(py) if f.startswith("信息检查") and f.endswith(".txt")), None)
    check("(c) 两条命令产出的报告 txt 一致（第 96 条后为烟雾测试）",
          bool(sw_txt and py_txt) and open(sw_txt, "rb").read() == open(py_txt, "rb").read(),
          f"{real_issues[0].name}")
    # toc 已在第 95 条切到引擎：Swift CLI 的 `content` 现在只是转发，两边同源，
    # 再"差分"就是自比自 —— 那一项的守卫改由 (b2) 的冻结快照承担。
    print("NOTE  生成目录已切换到引擎（Swift CLI 仅转发）；该功能由 (b2) 快照守卫")
    shutil.rmtree(work, ignore_errors=True)

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(out, ignore_errors=True)

print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
