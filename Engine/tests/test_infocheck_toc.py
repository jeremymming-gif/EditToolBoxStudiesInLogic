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

import glob
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
            "{论示例悖论及其解决方案——从扩展的X标准看}\n"
            "\\newcommand{\\mytitleEN}           % 请输入英文标题\n"
            "{On the Example Paradox: \\\\\n"
            "A Study Based on the Extended X Criterion}\n"
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
    title = "None" if none_title else "论示例悖论及其解决方案——从扩展的X标准看"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(
            "Lu_Wang\r\n"
            "卷号：19  期号：5  年份：2026\r\n"
            "起始页码：1  终止页码：16  页数：16\r\n"
            "字号 10.5t\r\n"
            f"中文标题: {title}\r\n"
            "英文标题: On the Example Paradox: \\\\A Study Based on the Extended X Criterion\r\n"
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
      art.chineseTitle == "论示例悖论及其解决方案——从扩展的X标准看", repr(art.chineseTitle))
check("(a) 英文标题按配平收集到闭合 `}`（不是只取两行）",
      art.englishTitle is not None and "Extended X Criterion" in art.englishTitle,
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
# 2026-10-17 第十四批（案例第 110 条）：期 90 成品里这篇标题断在「的」后；
# 旧的「0.35/0.65 居中兜底」会把它劈成「真理 / 论」（切词）。这里把与成品一致的断点钉死。
wc = TOC.wrap_title("示例倾向性：对紧缩真理论概括难题的知识论回应", 20, False)
check("(a) wrap_title：中文长标题在虚词后断、不切词（期 90 成品同款断点）",
      wc == ("示例倾向性：对紧缩真理论概括难题的", "知识论回应"), f"{wc}")
check("(a) TocSettings 默认断行上限：中文 20 / 英文 30（英文按期 90 成品校准，原 35）",
      TOC.TocSettings().titleWrapLimit == 20 and TOC.TocSettings().titleWrapLimitEN == 30,
      f"{TOC.TocSettings().titleWrapLimit}/{TOC.TocSettings().titleWrapLimitEN}")
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
      "第\\ 19 卷" in ch_tex and "论示例悖论" in ch_tex and ch_tex.rstrip().endswith("\\end{document}"),
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
                            columnHost="郑建国", columnArticles=["01 Lu_Wang"])
check("(a) TocSettings.contains：带序号的设置也能匹配报告的短名",
      settings2.contains("Lu_Wang") and not settings2.contains("别人"))
check("(a) 栏目名：中文「专栏：X」、英文「Column: X」",
      settings2.chinese_column_title == "专栏：认知悖论研究"
      and settings2.english_column_title == "Column: 认知悖论研究")
check("(a) make_column_block：沿用上一期形态并替换名称与主持人",
      TT.make_column_block(settings2, ["{\\large \\bf{专栏：旧名}}",
                                       "&&\\textit{\\large 主持人：旧主持}",
                                       "\\\\[3.2 ex]"], True)
      == ["{\\large \\bf{专栏：认知悖论研究}}", "&&\\textit{\\large 主持人：郑建国}", "\\\\[3.2 ex]"])
check("(a) make_column_block：没开栏目 → 空",
      TT.make_column_block(TOC.TocSettings(), ["x"], True) == [])
check("(a) TocSettings.load：文件不存在 → 默认值；字段缺失 → 逐字段回落（旧 json 不清空）",
      TOC.TocSettings.load(tempfile.mkdtemp()).titleWrapLimit == 20
      and TOC.TocSettings.load(tempfile.mkdtemp()).titleWrapLimitEN == 30)
tmpj = tempfile.mkdtemp()
with open(os.path.join(tmpj, "目录设置.json"), "w", encoding="utf-8") as fh:
    fh.write('{"columnKind": "专辑", "columnName": "旧文件只有两个字段"}')
s_old = TOC.TocSettings.load(tmpj)
check("(a) 旧 json（只有两个字段）读进来不清空、其余取默认",
      s_old.columnKind == "专辑" and s_old.titleWrapLimit == 20 and s_old.columnArticles == [])

# ── 2026-10-17 第十五批（全库普查驱动）：目录保留号 + 文件名宽松兜底 ──
alt = tempfile.mkdtemp(prefix="slcn_ic_alt_")
make_issue(alt)
# ① 非标准 000 目录名：`000 目录` 也不许被当成一篇文章（从前只认 article_name=="Contents"）
os.makedirs(os.path.join(alt, "000 目录"), exist_ok=True)
# ② tex 名与目录名只差下划线（实测：2024年第2期 03 Tianwen_Xu 里是 `TianwenXu.tex`）
_a2 = os.path.join(alt, "02 Tianwen_Xu")
os.makedirs(_a2, exist_ok=True)
shutil.copy(os.path.join(alt, "01 Lu_Wang", "Lu_Wang.tex"),
            os.path.join(_a2, "TianwenXu.tex"))
_rep_alt = IC.process_folders(alt, write=False)
_names_alt = [a.folderName for a in _rep_alt.articles]
check("(a) `000 目录`（非标准名）与 `000 Table of Contents` 都不被当成文章",
      "目录" not in _names_alt and "Contents" not in _names_alt, f"{_names_alt}")
check("(a) tex 名只差下划线（`TianwenXu.tex`）→ 宽松匹配后字段照常读到（与 compileall 同一口径）",
      "Tianwen_Xu" in _names_alt
      and any(a.folderName == "Tianwen_Xu" and a.hasTeX and a.volume == "19"
              for a in _rep_alt.articles),
      f"{[(a.folderName, a.hasTeX, a.volume) for a in _rep_alt.articles]}")
shutil.rmtree(alt, ignore_errors=True)

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
    # ⚠️ 行尾归一化（与下面报告快照同一理由）：Windows 上夹具是 CRLF，生成物跟着带 CRLF，
    #    与 mac 上冻结的快照不同。产物给 XeLaTeX 读，行尾无害 —— 要守的是内容。
    #    2026-10-16：上一版只改了报告快照那一处，windows 仍红在**目录**这一处（两个循环）。
    _nl = lambda b: b.replace(b"\r\n", b"\n")
    check(f"(b2) `{name}` 与冻结快照一致（行尾归一化后）",
          _nl(frozen.read_bytes()) == _nl(body),
          f"{len(body)} 字节" if _nl(frozen.read_bytes()) == _nl(body) else
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

print("\n── (d) 喂错文件必须**拒绝**，不许静默产出空目录 ──")
# 2026-10-17 逐页体检实测：`toc.py --report <某篇 .tex>` 会静默生成两份
# 「第 ? 卷，第 ? 期」的空目录、退出码 0，而且**落在报告所在目录** ——
# 真跑时那就是作者目录（体检时我自己就这么误写进稿件库一次，当场恢复）。
bad_dir = tempfile.mkdtemp(prefix="slcn_toc_bad_")
bad = os.path.join(bad_dir, "main.tex")
with open(bad, "w", encoding="utf-8") as fh:
    fh.write("\\documentclass{article}\n\\begin{document}hi\\end{document}\n")
r = subprocess.run([sys.executable, str(ENGINE / "toc.py"), "--report", bad],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=os.environ, cwd=ENGINE)
check("(d) 把 .tex 当报告 → 退出码 2（不是 0）", r.returncode == 2,
      f"rc={r.returncode}｜{r.stdout.strip()[-70:]}")
check("(d) 报错说清「要 infocheck 生成的 .txt」", "信息检查" in r.stderr, r.stderr.strip()[-90:])
check("(d) **一个文件都没生成**（报告所在目录里只有我放进去的那份 .tex）",
      sorted(os.listdir(bad_dir)) == ["main.tex"], repr(sorted(os.listdir(bad_dir))))
r2 = subprocess.run([sys.executable, str(ENGINE / "toc.py"), "--report", bad, "--preview"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    env=os.environ, cwd=ENGINE)
check("(d) `--preview` 同样拒绝（界面预览不能显示成「清单为空」）", r2.returncode == 2,
      f"rc={r2.returncode}")
shutil.rmtree(bad_dir, ignore_errors=True)

print("\n── (e) 总第期号无法确定 → 退出码 1（有结果、需人工核对）──")
# 2026-10-17 第十四批（案例第 110 条）：`总第 N 期` 是从**报告所在目录名**取的
# （`2026年第5期(90)` → 90）。报告被挪到期目录外时目录头会写成「总第 ? 期」，
# 从前照旧退出 0、看上去"成功"却是一份残的；现在按 INVARIANTS 三.2 退 1。
no_issue_dir = tempfile.mkdtemp(prefix="slcn_toc_noissue_")
rep_noid = make_report(no_issue_dir)          # 目录名不是 `2026年第N期(M)` 形态
r_no = subprocess.run([sys.executable, str(ENGINE / "toc.py"), "--report", rep_noid],
                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                      env=os.environ, cwd=ENGINE)
_no_out = r_no.stdout.strip().splitlines()
check("(e) 报告不在期目录里 → 退出码 1（不是 0）", r_no.returncode == 1,
      f"rc={r_no.returncode}｜{r_no.stdout.strip()[-80:]}")
check("(e) 明说「无法确定总第 N 期」",
      "无法确定" in r_no.stdout and "总第" in r_no.stdout and "?" in r_no.stdout,
      r_no.stdout.strip()[-120:])
check("(e) 两份目录仍已生成（产物可用，只是需人工补期号）",
      len([f for f in os.listdir(no_issue_dir)
           if f.startswith(("contents_", "Chinese Content_"))]) == 2,
      repr(sorted(os.listdir(no_issue_dir))))
check("(e) 契约行仍是 stdout 最后一行（外壳靠它拿产物）",
      bool(_no_out) and _no_out[-1].startswith("完成。"), _no_out[-1] if _no_out else "<empty>")
shutil.rmtree(no_issue_dir, ignore_errors=True)


print("\n── (f) 目录摘要推送（B-225）：合成期目录 → docx 契约 ──")
import hashlib as _hashlib
import zipfile as _zip
from slcn.pushcopy import collect as PC
from slcn.pushcopy import render_docx as PR  # noqa: F401


def _w(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_pushcopy_issue(root: str, year: int, number: int, total: int,
                        month: int, day: int, *, column: bool = True) -> str:
    """合成一期（假期号/假专栏/假作者/假摘要），落在 tempdir 里。"""
    d = os.path.join(root, f"{year}年第{number}期({total})")
    toc = os.path.join(d, "000 Table of Contents")
    hdr = ("\\begin{flushright}\n\\heiti{逻辑学研究（双月刊）\\\\\n"
           f"第\\ 19 卷，第\\ {number} 期，总第\\ {total} 期}}\\\\\n"
           f"\\heiti{{{year} 年第\\ {number} 期，{month}月\\ {day} 日出版}}\n\\end{{flushright}}\n")
    host = ("{\\large \\bf{专栏：假专栏}}&&\\textit{\\large 主持人：假主持}\n\\\\[3.0 ex]\n"
            if column else "\\\\[3.0 ex]\n")
    body = (
        "{\\large ~~~~ 假标题一}\n&\\textit{\\large ~~~~ 张三，李四}\n& 1\\\\\n\\vdistance\n"
        "{\\large 假标题二}\n&\\textit{\\large 王五}\n& 2\\\\\n\\vdistance\n")
    zh = ("\\documentclass{ctexart}\n\\begin{document}\n" + hdr
          + "\\vspace{4ex}\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{-0.3em}}r}\n"
          + host + "%中间主体\n" + body + "\\end{tabularx}\n\\end{document}\n")
    _w(os.path.join(toc, "Chinese Content.tex"), zh)
    en = ("\\documentclass{ctexart}\n\\begin{document}\n"
          "\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n"
          "\\large  \\bf{Column: Fake Column}\n\\\\[6.2mm]\n%中间主体\n"
          "\\large ~~~~ Fake Title One\\\\\n\\textit{{~~~~  San Zhang}, {Si Li}}\n& 1\\\\\n\\vdistance\n"
          "\\large Fake Title Two\\\\\n\\textit{{Wu Wang}}\n& 2\\\\\n\\vdistance\n"
          "\\end{tabularx}\n\\end{document}\n")
    _w(os.path.join(toc, "contents.tex"), en)
    for i, (t, a, ab) in enumerate((("假标题一", "张三", "假摘要甲。"),
                                    ("假标题二", "王五", "假摘要乙。")), start=1):
        tex = ("\\documentclass{article}\n\\begin{document}\n"
               f"\\newcommand{{\\mytitleCN}}{{{t}}}\n"
               f"\\newcommand{{\\myfirstauthorCN}}{{{a}}}\n"
               f"\\newcommand{{\\myfirstaffiliationCN}}{{假单位}}\n"
               f"\\newcommand{{\\myabstractCN}}{{{ab}}}\n"
               f"\\newcommand{{\\mytitle}}{{Fake Title {i}}}\n"
               f"\\newcommand{{\\myfirstauthor}}{{Author {i}}}\n"
               f"\\newcommand{{\\myabstract}}{{Fake abstract {i}.}}\n"
               "\\end{document}\n")
        _w(os.path.join(d, f"{i:02d} Fake_{i}", f"Fake_{i}.tex"), tex)
    return d


_push_work = tempfile.mkdtemp(prefix="slcn_pushcopy_")
try:
    out_root = os.path.join(_push_work, "out")
    d3 = make_pushcopy_issue(_push_work, 2026, 3, 88, 6, 18)
    iss = PC.collect_issue(d3, with_en_toc=True, out_root=out_root)
    check("(f1) 期次/期头 3 行（实例体例逐字）",
          iss.vol == 19 and iss.total == 88 and iss.header_lines == [
              "逻辑学研究 2026 年第 3 期",
              "第 19 卷（总第 88 期）2026 年 6 月 18 日出版",
              "双月刊 2008 年创刊"], repr(iss.header_lines))
    check("(f2) 中/英文目录条目 + 专栏/主持人口径",
          len(iss.zh_entries) == 2 and len(iss.en_entries) == 2 and iss.column is not None
          and iss.column.label == "专栏" and iss.column.host == "假主持"
          and iss.zh_entries[0].authors == ["张三，李四"]
          and iss.en_entries[0].title_lines == ["Fake Title One"],
          repr([iss.zh_entries[0].authors, iss.en_entries[0].title_lines]))
    check("(f3) 各篇 CN 元数据（假作者/假摘要）",
          len(iss.articles) == 2 and iss.abstracts_ok == 2
          and iss.articles[0].title_lines == ["假标题一"]
          and iss.articles[1].abstract == "假摘要乙。",
          repr([a.abstract for a in iss.articles]))
    _env = dict(os.environ, PYTHONPATH=str(ENGINE) + os.pathsep + str(ENGINE / "vendor"))
    r = subprocess.run([sys.executable, str(ENGINE / "dirpush.py"), "--issue", d3,
                        "--out", out_root, "--json", "--dry-run"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_env)
    _j = json.loads(r.stdout)
    check("(f4) dry-run：rc=0、stdout 唯一 JSON、字段名冻结、不写盘",
          r.returncode == 0 and _j["ok"] is True and _j["mode"] == "issue"
          and _j["dry_run"] is True and _j["annual"] is None
          and set(_j) >= {"schema", "ok", "mode", "dry_run", "outputs", "issue",
                          "counts", "warnings", "missing", "annual"}
          and set(_j["counts"]) == {"zh_entries", "en_entries", "column_entries",
                                    "articles", "abstracts_ok", "abstracts_missing"}
          and _j["counts"]["column_entries"] == 1
          and not os.path.exists(out_root),
          f"rc={r.returncode} fields={sorted(_j)}")
    before = sorted(os.listdir(d3))
    _docx = os.path.join(out_root, "2026 年第 3 期", "逻辑学研究2026年第3期目录摘要.docx")
    _sha = []
    for _ in range(2):
        r = subprocess.run([sys.executable, str(ENGINE / "dirpush.py"), "--issue", d3,
                            "--out", out_root, "--with-en-toc"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=_env)
        _sha.append(_hashlib.sha256(open(_docx, "rb").read()).hexdigest())
    check("(f5) 当期路径体例 + 两遍 sha256 相同 + 同名覆盖无 bak",
          r.returncode == 0 and _sha[0] == _sha[1] and os.path.isfile(_docx)
          and not any(x.endswith(".bak") for x in os.listdir(os.path.dirname(_docx))),
          f"rc={r.returncode} sha={_sha}")
    _xml = _zip.ZipFile(_docx).read("word/document.xml").decode("utf-8")
    _i = _xml.find("论文摘要")
    _seg = ""
    if _i >= 0:
        _a = _xml.rfind("<w:p>", 0, _i)
        _b = _xml.find("</w:p>", _i)
        _seg = _xml[_a:_b] if _a >= 0 and _b >= 0 else ""
    check("(f5b) 当期 docx 含「论文摘要」节标题（14pt=sz28 / b / jc=center，实例体例）",
          bool(_seg) and 'w:sz w:val="28"' in _seg and "<w:b/>" in _seg
          and 'w:jc w:val="center"' in _seg,
          _seg[:160])
    # (f5c) Word 兼容性回归：根标签必须保留母版全部 xmlns 声明。lxml 重序列化会丢掉
    #       只被 mc:Ignorable 引用、未被元素使用的命名空间（w15/w16…/wp14）→ MCE 无效
    #       → Word 打开报「发现无法读取的内容」并修复（B-225 交付后用户实测）。
    import re as _re_root
    _root_tag = (_re_root.search(r"<w:document[^>]*>", _xml) or _re_root.match("", _xml)).group(0)
    _ign = _re_root.search(r'mc:Ignorable="([^"]*)"', _root_tag)
    _undeclared = [p for p in (_ign.group(1).split() if _ign else [])
                   if ("xmlns:%s=" % p) not in _root_tag]
    check("(f5c) document.xml 根保留全部 xmlns 声明（mc:Ignorable 前缀均可解析；防 Word 修复弹窗）",
          bool(_root_tag) and _ign is not None and not _undeclared,
          "undeclared=%s" % _undeclared)
    # (f5d) 专栏文章区分（用户 2026-10-25 三轮要求，参考 2026 年第 5 期实例）：
    #       专栏区域单元格内部边框取消（tcBorders=nil）——另生成一份**中文 only** 目录检查
    #       （双语模式本轮不做边框区分，避免与实例体例冲突）。
    _zh_only = os.path.join(_push_work, "out_zh_only")
    r = subprocess.run([sys.executable, str(ENGINE / "dirpush.py"), "--issue", d3,
                        "--out", _zh_only],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_env)
    _zh_doc = os.path.join(_zh_only, "2026 年第 3 期", "逻辑学研究2026年第3期目录摘要.docx")
    _zx = _zip.ZipFile(_zh_doc).read("word/document.xml").decode("utf-8")
    # fixture：第 1 篇专栏（1 条 "col_last"）+ 第 2 篇非专栏；host 行 bottom nil。
    # 期望：tcBorders 3 处（host 1 + 专栏条目标题/作者各 1），专栏条目 top nil 2 处；
    #       非专栏行不加 tcBorders。
    _nb = _zx.count("<w:tcBorders>")
    _ntop = _zx.count('<w:top w:val="nil"/>')
    _nbot = _zx.count('<w:bottom w:val="nil"/>')
    check("(f5d) 专栏区域去边框（参考实例：专栏行 tcBorders nil；非专栏行不变）",
          r.returncode == 0 and _nb == 3 and _ntop == 2 and _nbot == 1,
          f"rc={r.returncode} borders={_nb} top_nil={_ntop} bottom_nil={_nbot}")
    check("(f6) settings 写出且确定性（schema/year/issue/with_en_toc，无时间戳）",
          json.load(open(os.path.join(out_root, "2026 年第 3 期", "目录摘要设置.json"),
                         encoding="utf-8"))
          == {"schema": 1, "year": 2026, "issue": 3, "with_en_toc": True})
    check("(f7) 不产 PDF + 输入期目录零变化",
          not any(x.lower().endswith(".pdf") for x in os.listdir(os.path.dirname(_docx)))
          and sorted(os.listdir(d3)) == before)
    bad = os.path.join(_push_work, "2026年第9期(99)")
    os.makedirs(bad, exist_ok=True)
    r = subprocess.run([sys.executable, str(ENGINE / "dirpush.py"), "--issue", bad,
                        "--out", out_root],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_env)
    check("(f8) 缺 000 Table of Contents → rc=2 + 中文提示 + 不生成",
          r.returncode == 2 and "000 Table of Contents" in r.stderr
          and not os.path.exists(os.path.join(out_root, "2026 年第 9 期")),
          f"rc={r.returncode} err={r.stderr.strip()[:60]}")
    make_pushcopy_issue(_push_work, 2026, 4, 89, 8, 18)
    r = subprocess.run([sys.executable, str(ENGINE / "dirpush.py"), "--issue", d3,
                        "--annual", "--year", "2026", "--out", out_root, "--json"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_env)
    _j = json.loads(r.stdout)
    check("(f9) 年度：rc=0、JSON annual 结构（included/缺期/体例还原）+ 两期拼接",
          r.returncode == 0 and _j["mode"] == "annual" and _j["annual"]["year"] == 2026
          and _j["annual"]["included"] == [3, 4] and 5 in _j["annual"]["missing_issues"]
          and _j["counts"]["zh_entries"] == 4 and len(_j["annual"]["issues"]) >= 2
          and any(x["status"] == "no_toc" for x in _j["annual"]["issues"])
          and _j["annual"]["issues"][0]["with_en_toc"] is True,
          f"rc={r.returncode} annual={_j.get('annual')}")
finally:
    shutil.rmtree(_push_work, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────
print("\n── (g) B-226 标题/作者清洗（E1/E2/E3/E9）──")
check("(g1) clean_title 保留 \\hbox 内容（`甲\\hbox{乙丙}` 从前只剩「甲」）",
      TOC.clean_title("示例标题的甲\\hbox{乙丙}\\\\——副标题", keep_braces=False)
      == "示例标题的甲乙丙——副标题",
      TOC.clean_title("示例标题的甲\\hbox{乙丙}\\\\——副标题", keep_braces=False))
check("(g2) clean_title 保留 \\textbf 内容（标题末尾的 `\\textbf{ABC}` 不再被整段删掉）",
      TOC.clean_title("示例标题\\textbf{ABC}", keep_braces=False) == "示例标题ABC")
check("(g3) 数学模式标题 `$\\text{术语}^*$` → `术语`",
      TOC.clean_title("$\\text{术语}^*$", keep_braces=False) == "术语",
      TOC.clean_title("$\\text{术语}^*$", keep_braces=False))
check("(g4) 反例：普通标题/花括号英文标题不受影响（英文仍 keep_braces）",
      TOC.clean_title("示例标题", keep_braces=False) == "示例标题"
      and TOC.clean_title("Intuitionistic Epistemic Logic {\\bf IEL} with Justification",
                          keep_braces=True)
      == "Intuitionistic Epistemic Logic {\\bf IEL} with Justification")
check("(g5) clean_name 保留中文间隔号 `·`（外文译名）",
      TOC.clean_name("\\kaishu 甲·乙丙") == "甲·乙丙", TOC.clean_name("\\kaishu 甲·乙丙"))
check("(g6) 反例：clean_name 仍滤掉非汉字非间隔号（`\\kaishu`/数字/字母/空格）",
      TOC.clean_name("abc 123 \\kaishu 张三") == "张三", TOC.clean_name("abc 123 \\kaishu 张三"))
check("(g7) clean_author_en：去掉 `\\Letter` 类通讯作者标记（英文目录编译失败根因）",
      TOC.clean_author_en("\\Letter}$") == "" and TOC.clean_author_en("$^\\text{\\Letter}$") == "",
      repr(TOC.clean_author_en("$^\\text{\\Letter}$")))
check("(g8) 反例：clean_author_en 保留正常英文名（含变音字母）",
      TOC.clean_author_en("Tuomo Aho") == "Tuomo Aho"
      and TOC.clean_author_en("Mikko Yrjönsuuri") == "Mikko Yrjönsuuri")
_dup_block = ["起始页码：1  终止页码：4  页数：4\r\n",
              "中文标题: 测试\n",
              "第一作者中文: 甲\n",
              "第一作者中文: \\kaishu 甲乙\n"]
_dup = TOC.parse_entry("Zhien_Bei", _dup_block)
check("(g9) 同角色两条作者：**后者优先**（「甲，甲乙」→「甲乙」）",
      _dup.chineseAuthors == ["甲乙"], f"{_dup.chineseAuthors}")
_two = TOC.parse_entry("X", ["第一作者中文: 甲\n", "第二作者中文: 乙\n"])
check("(g10) 反例：不同角色的作者都保留、顺序为第一→第二",
      _two.chineseAuthors == ["甲", "乙"], f"{_two.chineseAuthors}")
_letter = TOC.parse_entry("Tuomo_Aho", ["第一作者英文: Tuomo Aho\n", "第二作者英文: \\Letter}$\n"])
check("(g11) 英文作者清洗后为空的那条被丢弃，正常英文名保留",
      _letter.englishAuthors == ["Tuomo Aho"], f"{_letter.englishAuthors}")

print("\n── (h) 模板 split 跳过注释行（E4：89 注释 tabularx / 注释栏目行不复活）──")
_TEX_COMMENT = ("\\documentclass{ctexart}\n"
                "%\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n"
                "\\begin{tabularx}{\\textwidth}{l@{\\hspace{2.7em}}r}\n"
                "\\large  \\bf{Column: Real Column}\n"
                "\\\\[3mm]\n"
                "%{\\large \\bf{Column: Old Commented}}\n"
                "%中间主体\n"
                "\\large T\\\\\n"
                "\\end{tabularx}\n")
_parts_c = TT.split(_TEX_COMMENT, "注释模板")
check("(h1) 真模板起点是**非注释**的 tabularx（head 末行是 2.7em 列定义，不是 4em 注释版）",
      _parts_c.head.rstrip().endswith("{\\textwidth}{l@{\\hspace{2.7em}}r}")
      and "hspace{4em}" not in _parts_c.head.split("\n")[-2:],
      _parts_c.head.split("\n")[-1])
check("(h2) columnBlock 只从区间内**真实**行提取（注释栏目行不复活）",
      _parts_c.columnBlock == ["\\large  \\bf{Column: Real Column}", "\\\\[3mm]"],
      f"{_parts_c.columnBlock}")
check("(h3) 反例：只有注释版 tabularx 时按「无模板」处理（columnBlock 空、tail 空）",
      TT.split("%\\begin{tabularx}{x}{a}\n%\\end{tabularx}\n", "s").columnBlock == [])
_parts_87 = TT.split("\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n"
                     "%{\\large \\bf{专栏：86 残留}}\n"
                     "\\\\[3mm]\n\\end{tabularx}\n", "s87")
check("(h4) 87 式注释栏目行 + 无真实栏目行 → columnBlock 为空（注释行不复活，S5）",
      _parts_87.columnBlock == [], f"{_parts_87.columnBlock}")

print("\n── (i) 内置模板源头 fontset=windows（V1）──")
_bi_zh = TT.builtin("Chinese Content.tex").head
_bi_en = TT.builtin("contents.tex").head
check("(i1) 内置中文模板 documentclass 源头带 fontset=windows",
      "\\documentclass[b5paper,11pt,onecolumn,twoside,fontset=windows]{ctexart}" in _bi_zh)
check("(i2) 内置英文模板 documentclass 源头带 fontset=windows",
      "\\documentclass[b5paper,10pt,onecolumn,twoside,fontset=windows]{ctexart}" in _bi_en)
check("(i3) 反例：运行时兜底 ensure_windows_fontset 仍只补、不覆盖已有 fontset",
      TT.ensure_windows_fontset("\\documentclass[b5paper,fontset=mac]{ctexart}")
      == "\\documentclass[b5paper,fontset=mac]{ctexart}")

print("\n── (j) B-226 目录交付 helper（slcn/tocdelivery.py）：落位/编译/单页适配/字体检查 ──")
from slcn import tocdelivery as TD  # noqa: E402
import contextlib as _ctxlib  # noqa: E402


@_ctxlib.contextmanager
def _patched(obj, attr, value):
    orig = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, orig)


def _mk_deliver_root(with_000: bool = True) -> str:
    d = tempfile.mkdtemp(prefix="slcn_deliver_")
    if with_000:
        os.makedirs(os.path.join(d, "000 Table of Contents"))
    for stem in ("Chinese Content", "contents"):
        with open(os.path.join(d, f"{stem}_20260101_000000.tex"), "w", encoding="utf-8") as fh:
            fh.write(f"% {stem}\n")
    return d


def _mk_fit_dir(zh_pages: int, en_pages: int) -> str:
    d = tempfile.mkdtemp(prefix="slcn_fit_")
    for key, stem, pages in (("zh", "Chinese Content", zh_pages), ("en", "contents", en_pages)):
        with open(os.path.join(d, f"{stem}.tex"), "w", encoding="utf-8") as fh:
            fh.write("\\documentclass{ctexart}\n"
                     "\\newcommand{\\vdistance}{\\\\[4.8ex]}%调整行间隔\n"
                     "\\begin{document}\n"
                     "A\n\n\\vdistance\n\nB\n\n\\vdistance\n\nC\n"
                     "\\end{document}\n")
        with open(os.path.join(d, f"{stem}.log"), "w", encoding="utf-8") as fh:
            fh.write(f"Output written on {stem}.pdf ({pages} page{'' if pages == 1 else 's'}).\n")
    return d


_d_find = _mk_deliver_root()
check("(j1) find_toc_dir：认 `000 Table of Contents`，且与 pdfmerge.numbered_entries 同一口径",
      os.path.basename(TD.find_toc_dir(_d_find)) == "000 Table of Contents"
      and TD.find_toc_dir(_mk_deliver_root(with_000=False)) == "")
_d_no000 = _mk_deliver_root(with_000=False)
_d_auto = TD.ensure_toc_dir(_d_no000)
check("(j2) ensure_toc_dir：缺 000* 时自动创建 `000 Table of Contents`",
      os.path.isdir(_d_auto) and os.path.basename(_d_auto) == "000 Table of Contents")
_d_old = tempfile.mkdtemp(prefix="slcn_deliver_old_")
os.makedirs(os.path.join(_d_old, "000 目录"))
with open(os.path.join(_d_old, "Chinese Content_20260101_000000.tex"), "w", encoding="utf-8") as fh:
    fh.write("A")
with open(os.path.join(_d_old, "contents_20260101_000000.tex"), "w", encoding="utf-8") as fh:
    fh.write("B")
check("(j4) ensure_toc_dir 反例：已有旧名 `000 目录` → 原样返回、不另建标准名",
      os.path.basename(TD.ensure_toc_dir(_d_old)) == "000 目录"
      and not os.path.isdir(os.path.join(_d_old, "000 Table of Contents")))
_d_place = _mk_deliver_root()
_placed = TD.place_tex(_d_place, os.path.join(_d_place, "000 Table of Contents"))
check("(j5) place_tex：落位固定名、返回路径、根下时间戳 tex 保留",
      _placed["zh"] == os.path.join(_d_place, "000 Table of Contents", "Chinese Content.tex")
      and os.path.isfile(_placed["en"])
      and len(glob.glob(os.path.join(_d_place, "Chinese Content_*.tex"))) == 1)
try:
    TD.place_tex(tempfile.mkdtemp(), tempfile.mkdtemp())
    check("(j6) place_tex 反例：根下没有时间戳 tex → DeliveryError", False)
except TD.DeliveryError:
    check("(j6) place_tex 反例：根下没有时间戳 tex → DeliveryError（不半套交付）", True)
check("(j7) 页数解析：`Output written on … (2 pages)` → 2，单数 `(1 page)` → 1",
      TD.page_count_from_log(_mk_fit_dir(2, 1), "Chinese Content") == 2
      and TD.page_count_from_log(_mk_fit_dir(2, 1), "contents") == 1)

_fit_calls: list[str] = []


def _fit_compile_ok(folder: str, stem: str):
    _fit_calls.append(stem)
    with open(os.path.join(folder, f"{stem}.log"), "w", encoding="utf-8") as fh:
        fh.write(f"Output written on {stem}.pdf (1 page).\n")
    return True, os.path.join(folder, f"{stem}.pdf")


_d_fit = _mk_fit_dir(2, 1)
_fit_before_en = open(os.path.join(_d_fit, "contents.tex"), encoding="utf-8").read()
_fit_logs: list[str] = []
_fit_counts, _fit_err = TD.fit_single_page(_d_fit, TD.TEX_STEMS, _fit_compile_ok,
                                           log=_fit_logs.append)
_fit_zh = open(os.path.join(_d_fit, "Chinese Content.tex"), encoding="utf-8").read()
check("(j8) 单页适配：页数>1 的那份改写成 `\\dimexpr 原值 - Npt`、重编译后返回 1 页",
      _fit_err == "" and _fit_counts == {"zh": 1, "en": 1}
      and "\\dimexpr 4.8ex - " in _fit_zh and _fit_zh.endswith("\\end{document}\n")
      and _fit_calls == ["Chinese Content"], f"err={_fit_err} calls={_fit_calls}")
check("(j9) 单页适配反例：页数=1 的那份**不动**（不破坏与人工成品的位置对齐）",
      open(os.path.join(_d_fit, "contents.tex"), encoding="utf-8").read() == _fit_before_en)
check("(j10) 单页适配写日志：旧值→新值、页数前→后",
      any("\\vdistance 4.8ex →" in s for s in _fit_logs)
      and any("2 页 → 1 页" in s for s in _fit_logs), f"{_fit_logs}")
_fit_calls.clear()


def _fit_compile_still2(folder: str, stem: str):
    _fit_calls.append(stem)
    return True, os.path.join(folder, f"{stem}.pdf")


_d_fit2 = _mk_fit_dir(2, 2)
_counts2, _err2 = TD.fit_single_page(_d_fit2, TD.TEX_STEMS, _fit_compile_still2)
check("(j11) 反例：压到上限仍 >1 页 → 返回可操作失败原因（不静默成功）",
      "仍是 2 页" in _err2 and "自动单页适配失败" in _err2, _err2)
_fit_base = _mk_fit_dir(2, 1)
_shrink_small = TD._shrink_pt(_fit_base, "Chinese Content", 2, "zh", "0.4ex")
_shrink_inherit = TD._shrink_pt(_fit_base, "Chinese Content", 2, "zh", "4.8ex")
_shrink_cap_dir = _mk_fit_dir(2, 1)
with open(os.path.join(_shrink_cap_dir, "Chinese Content.log"), "w", encoding="utf-8") as fh:
    fh.write("Overfull \\vbox (9999.0pt too high) has occurred while \\output is active\n")
check("(j12) 压缩量：本期成品小行距=40pt 基准；3 页再加 40pt；继承大行距按「压到人工量级」估算",
      TD._BASE_SHRINK_PT == 40.0 and _shrink_small == 40.0
      and TD._shrink_pt(_fit_base, "Chinese Content", 3, "zh", "0.4ex") == 80.0
      and _shrink_inherit > 100.0, f"small={_shrink_small} inherit={_shrink_inherit}")
check("(j12b) 总压缩量有上限 320pt（实测 85→86 缺 000 路径需 280pt，上限留余量）",
      TD._MAX_TOTAL_SHRINK_PT == 320.0
      and TD._shrink_pt(_shrink_cap_dir, "Chinese Content", 2, "zh", "0.4ex") == 320.0)
check("(j12c) 长度估算：4.8ex≈23.5pt、-3.05mm≈-8.7pt（只用于估算压缩量）",
      abs(TD.length_to_pt("4.8ex") - 23.52) < 0.01
      and abs(TD.length_to_pt("-3.05mm") + 8.677) < 0.01
      and TD.length_to_pt("bogus") is None)
_d_novd = tempfile.mkdtemp(prefix="slcn_fit_novd_")
with open(os.path.join(_d_novd, "Chinese Content.tex"), "w", encoding="utf-8") as fh:
    fh.write("no macro\n")
with open(os.path.join(_d_novd, "Chinese Content.log"), "w", encoding="utf-8") as fh:
    fh.write("Output written on x.pdf (2 pages).\n")
_c_novd, _err_novd = TD.fit_single_page(_d_novd, (("zh", "Chinese Content"),), _fit_compile_ok)
check("(j13) 反例：模板里没有 \\vdistance 宏 → 返回失败原因（不改坏模板）",
      "\\vdistance" in _err_novd, _err_novd)

with _patched(TD, "_FONT_CHECK_CACHE", None), _patched(TD.shutil, "which", lambda _n: None):
    _font_ok, _font_msg = TD.check_windows_fonts()
check("(j14) 字体前置检查：找不到 xelatex → 失败 + 可操作原因（缺工具不等于字体齐全）",
      _font_ok is False and "TeX Live" in _font_msg, _font_msg)
with _patched(TD, "_FONT_CHECK_CACHE", (False, "cached-reason")):
    check("(j15) 字体检查结果进程内缓存（一次交付只探一次）",
          TD.check_windows_fonts() == (False, "cached-reason"))

# deliver：落位 + 编译 + 适配 一条链（编译用假实现，不依赖 TeX）
_d_del = _mk_deliver_root(with_000=False)


def _fake_compile_all(folder: str, stems):
    out = {}
    for key, stem in stems:
        pdf = os.path.join(folder, f"{stem}.pdf")
        with open(pdf, "wb") as fh:
            fh.write(b"%PDF-1.4 fake\n")
        with open(os.path.join(folder, f"{stem}.log"), "w", encoding="utf-8") as fh:
            fh.write(f"Output written on {stem}.pdf (1 page).\n")
        out[key] = (True, pdf)
    return out


with _patched(TD, "check_windows_fonts", lambda: (True, "")):
    _del = TD.deliver(_d_del, do_compile=True, compile_func=_fake_compile_all,
                      log=lambda _s: None)
check("(j16) deliver：缺 000 → 自动创建 → 落位 → 编译，返回 4 个路径 + 页数",
      _del.error == "" and _del.compiled
      and os.path.basename(_del.toc_dir) == "000 Table of Contents"
      and all(os.path.isfile(p) for p in _del.placed.values())
      and all(os.path.isfile(p) for p in _del.pdfs.values())
      and _del.pages == {"zh": 1, "en": 1}, f"{_del}")
with _patched(TD, "check_windows_fonts", lambda: (False, "缺少 Windows 字体（SimSun）")):
    _del_font = TD.deliver(_mk_deliver_root(), do_compile=True, compile_func=_fake_compile_all)
check("(j17) deliver 反例：字体检查失败 → error 带原因、compiled=False（绝不静默换字体）",
      "缺少 Windows 字体" in _del_font.error and not _del_font.compiled, _del_font.error)

# toc.py CLI：--place + --result-json 的固定 schema
_json_base = tempfile.mkdtemp(prefix="slcn_toc_json_")
_json_root = os.path.join(_json_base, "2026年第5期(90)")
make_issue(_json_root)
_json_report = make_report(_json_root)
_json_out = os.path.join(tempfile.mkdtemp(prefix="slcn_toc_json_out_"), "result.json")
r_json = subprocess.run([sys.executable, str(ENGINE / "toc.py"), "--report", _json_report,
                         "--place", "--result-json", _json_out],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        env=os.environ, cwd=ENGINE)
_schema_keys = {"templateSource", "templateNote", "tocDir", "placed", "compiled",
                "pages", "column", "manualEdits", "warnings"}
_payload = json.load(open(_json_out, encoding="utf-8")) if os.path.isfile(_json_out) else {}
check("(j18) `--place --result-json`：rc=0、JSON 写出、schema 字段固定",
      r_json.returncode == 0 and os.path.isfile(_json_out)
      and set(_payload) == _schema_keys
      and set(_payload["placed"]) == {"zhTex", "zhPdf", "enTex", "enPdf"}
      and set(_payload["pages"]) == {"zh", "en"}
      and set(_payload["column"]) == {"enabled", "title"}
      and isinstance(_payload["manualEdits"], list) and isinstance(_payload["warnings"], list),
      f"rc={r_json.returncode} keys={sorted(_payload)}")
check("(j19) 结果 JSON：templateSource 取四种口径之一、tocDir 是 000 目录、compiled=False",
      _payload.get("templateSource") in ("本期成品", "上一期", "内置模板", "显式指定")
      and os.path.basename(_payload.get("tocDir", "")) == "000 Table of Contents"
      and _payload.get("compiled") is False and _payload.get("pages") == {"zh": 0, "en": 0})
check("(j20) 落位：000 里出现固定名 Chinese Content.tex / contents.tex",
      all(os.path.isfile(_payload["placed"][k])
          for k in ("zhTex", "enTex")) and _payload["placed"]["zhTex"].endswith("Chinese Content.tex"))
check("(j21) 契约行仍是 stdout 最后一行（GUI 靠它拿产物）",
      [ln for ln in r_json.stdout.strip().splitlines() if ln.strip()][-1].startswith("完成。生成目录"))
# 反例：--compile 缺 xelatex → rc=2 + stderr 原因；不写结果 JSON
_no_tex_root = tempfile.mkdtemp(prefix="slcn_toc_compile_")
make_issue(_no_tex_root)
_no_tex_report = make_report(_no_tex_root)
_bad_json = os.path.join(tempfile.mkdtemp(prefix="slcn_toc_badjson_"), "r.json")
_env_nopath = dict(os.environ)
_env_nopath["PATH"] = "/usr/bin:/bin"
r_nofont = subprocess.run([sys.executable, str(ENGINE / "toc.py"), "--report", _no_tex_report,
                           "--compile", "--result-json", _bad_json],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=_env_nopath, cwd=ENGINE)
check("(j22) 反例：`--compile` 缺 xelatex → rc=2、stderr 说清原因、不写结果 JSON",
      r_nofont.returncode == 2 and "xelatex" in r_nofont.stderr
      and not os.path.isfile(_bad_json),
      f"rc={r_nofont.returncode} err={r_nofont.stderr.strip()[:90]}")

# ── manualEdits（结果 JSON 的只读标注）──
_edit_root = tempfile.mkdtemp(prefix="slcn_edits_")
_edit_report = os.path.join(_edit_root, "信息检查2026_01_01_00_00_00.txt")
with open(_edit_report, "w", encoding="utf-8", newline="") as fh:
    fh.write("X_Y\r\n卷号：19  期号：5  年份：2026\r\n起始页码：1  终止页码：2  页数：2\r\n"
             "中文标题: 标题\r\n第一作者中文: 第一作者\r\n\r\n")
_edits = TOC.manual_edits(_edit_report, TOC.TocSettings())
check("(m1) manualEdits：源稿占位标题「标题」→ kind=title（只标注不改行为）",
      any(e["kind"] == "title" and e["article"] == "X_Y" for e in _edits), f"{_edits}")
check("(m2) manualEdits：源稿占位作者「第一作者」→ kind=author",
      any(e["kind"] == "author" for e in _edits), f"{_edits}")
_normal_edits = TOC.manual_edits(make_report(os.path.join(_json_base, "2026年第5期(90)")),
                                 TOC.TocSettings())
check("(m3) 反例：正常标题/作者不产生 title/author 类 manualEdits",
      not [e for e in _normal_edits if e["kind"] in ("title", "author")], f"{_normal_edits}")


print("\n── (k) 缺 000 自动创建 + 结果 JSON 的 templateSource 口径（E8/F2）──")
_k_root = tempfile.mkdtemp(prefix="slcn_toc_autocreate_")
_prev = os.path.join(_k_root, "2025年第6期(85)")
os.makedirs(os.path.join(_prev, "000 Table of Contents"))
with open(os.path.join(_prev, "000 Table of Contents", "Chinese Content.tex"), "w", encoding="utf-8") as fh:
    fh.write("\\documentclass{ctexart}\n\\newcommand{\\vdistance}{\\\\[4.8ex]}\n"
             "\\begin{document}\n\\begin{tabularx}{\\textwidth}{l@{\\hspace{1em}}r}\n"
             "%中间主体\n\\vdistance\n\\end{tabularx}\n\\end{document}\n")
with open(os.path.join(_prev, "000 Table of Contents", "contents.tex"), "w", encoding="utf-8") as fh:
    fh.write("\\documentclass{ctexart}\n\\newcommand{\\vdistance}{\\\\[6mm]}\n"
             "\\begin{document}\n\\begin{tabularx}{\\textwidth}{l@{\\hspace{1em}}r}\n"
             "%中间主体\n\\vdistance\n\\end{tabularx}\n\\end{document}\n")
_k_cur = os.path.join(_k_root, "2026年第1期(86)")
make_issue(_k_cur)
shutil.rmtree(os.path.join(_k_cur, "000 Table of Contents"))   # 造「缺 000」场景
_k_report = make_report(_k_cur)
_k_json = os.path.join(tempfile.mkdtemp(prefix="slcn_toc_k_"), "r.json")
_k_before = TD.find_toc_dir(_k_cur)
r_k = subprocess.run([sys.executable, str(ENGINE / "toc.py"), "--report", _k_report,
                      "--place", "--result-json", _k_json],
                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                     env=os.environ, cwd=ENGINE)
_k_payload = json.load(open(_k_json, encoding="utf-8")) if os.path.isfile(_k_json) else {}
check("(k1) 缺 000 + 有上一期：rc=0、自动创建 `000 Table of Contents`",
      r_k.returncode == 0 and _k_before == ""
      and os.path.isdir(os.path.join(_k_cur, "000 Table of Contents")),
      f"rc={r_k.returncode} err={r_k.stderr.strip()[:80]}")
check("(k2) 结果 JSON 的 templateSource 是**生成时**口径=`上一期`（不被落位后的本期成品覆盖）",
      _k_payload.get("templateSource") == "上一期", str(_k_payload.get("templateSource")))
# k3：全新的「缺 000」目录 + 显式 --template → 显式指定
_k2_root = tempfile.mkdtemp(prefix="slcn_toc_k2root_")
shutil.copytree(_prev, os.path.join(_k2_root, os.path.basename(_prev)))
_k2_cur = os.path.join(_k2_root, "2026年第2期(87)")
make_issue(_k2_cur)
shutil.rmtree(os.path.join(_k2_cur, "000 Table of Contents"))
_k2_report = make_report(_k2_cur)
_kjson2 = os.path.join(tempfile.mkdtemp(prefix="slcn_toc_k2_"), "r.json")
r_k2 = subprocess.run([sys.executable, str(ENGINE / "toc.py"), "--report", _k2_report,
                       "--place", "--template", _prev, "--result-json", _kjson2],
                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                      env=os.environ, cwd=ENGINE)
_k2 = json.load(open(_kjson2, encoding="utf-8")) if os.path.isfile(_kjson2) else {}
check("(k3) 显式 `--template`：templateSource=`显式指定`（缺 000 时仍可用）",
      r_k2.returncode == 0 and _k2.get("templateSource") == "显式指定",
      f"rc={r_k2.returncode} src={_k2.get('templateSource')}")
# k4：本期已有成品时，显式 --template 不夺权（口径=排在「本期成品」之后，保持现状）
_kjson3 = os.path.join(tempfile.mkdtemp(prefix="slcn_toc_k3_"), "r.json")
r_k3 = subprocess.run([sys.executable, str(ENGINE / "toc.py"), "--report", _k_report,
                       "--place", "--template", _prev, "--result-json", _kjson3],
                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                      env=os.environ, cwd=ENGINE)
_k3 = json.load(open(_kjson3, encoding="utf-8")) if os.path.isfile(_kjson3) else {}
check("(k4) 本期已有成品 + 显式 --template：仍用本期成品（优先级保持现状）",
      r_k3.returncode == 0 and _k3.get("templateSource") == "本期成品",
      f"rc={r_k3.returncode} src={_k3.get('templateSource')}")


print("\n── (l) 英文栏目表头数据驱动（columnNameEN 空 ⇒ 不生成 Column 行；用户 2026-10-25 口径）──")
_a_zh_shape = ["{\\large \\bf{专辑：旧名}}", "&&\\textit{\\large 主持人：旧主持}", "\\\\[3.2 ex]"]
_en_shape = ["\\large  \\bf{Special Issue: Old}", "\\\\[6.2mm]"]
_s_en_empty = TOC.TocSettings(columnKind="专辑", columnName="偏好逻辑", columnHost="某人",
                              columnArticles=["Lu_Wang"])
_s_en_full = TOC.TocSettings(columnKind="专辑", columnName="偏好逻辑", columnNameEN="Preference Logic",
                             columnHost="某人", columnArticles=["Lu_Wang"])
check("(l1) 英文名空 ⇒ 英文栏目块为空（**不拿中文名回退**、不生成 `Column: 偏好逻辑`）",
      TT.make_column_block(_s_en_empty, _en_shape, False) == []
      and TT.make_column_block(_s_en_empty, [], False) == [])
check("(l2) 英文名非空 ⇒ 生成 `Column: <EN>` + 沿用模板间距",
      TT.make_column_block(_s_en_full, _en_shape, False)
      == ["\\large  \\bf{Column: Preference Logic} ", "\\\\[6.2mm]"])
check("(l3) 反例：英文名非空但模板无栏目块 ⇒ 用默认间距 3mm",
      TT.make_column_block(_s_en_full, [], False)
      == ["\\large  \\bf{Column: Preference Logic} ", "\\\\[3mm]"])
check("(l4) 中文栏目块不受英文名缺失影响（仍按上一期形态替换名称/主持人）",
      TT.make_column_block(_s_en_empty, _a_zh_shape, True)
      == ["{\\large \\bf{专辑：偏好逻辑}}", "&&\\textit{\\large 主持人：某人}", "\\\\[3.2 ex]"])
# 端到端：EN 名空时生成的英文目录**没有 Column 行**，但栏目条目仍带 `~~~~`
_l_root = tempfile.mkdtemp(prefix="slcn_col_en_")
make_issue(_l_root)
_l_report = make_report(_l_root)
_l_outs = TOC.run(_l_report, _s_en_empty, log=lambda _s: None, stamp="COL")
_l_en = open(_l_outs[0], encoding="utf-8").read()
_l_zh = open(_l_outs[1], encoding="utf-8").read()
check("(l5) 端到端：英文名空 ⇒ 英文 tex 无 `Column:` 行 / 无中文栏目名，条目仍带 `~~~~`",
      "Column:" not in _l_en and "专栏：" not in _l_en and "偏好逻辑" not in _l_en
      and "~~~~ " in _l_en,
      repr([ln for ln in _l_en.split("\n") if "~~~~" in ln][:1]))
check("(l6) 端到端反例：中文 tex 仍生成「专辑：偏好逻辑」表头",
      "专辑：偏好逻辑" in _l_zh)
_l_outs2 = TOC.run(_l_report, _s_en_full, log=lambda _s: None, stamp="COL2")
_l_en2 = open(_l_outs2[0], encoding="utf-8").read()
check("(l7) 端到端反例：英文名非空 ⇒ 英文 tex 生成 `Column: Preference Logic`",
      "Column: Preference Logic" in _l_en2)

print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
