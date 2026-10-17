#!/usr/bin/env python3
"""SLCN 稿件转换器 · 正式命令行入口。

与 run_poc.py / corpus.py 的区别
--------------------------------
那两者是**测试台**，会自动扫描 original/ 去找源文件——那对回归测试是必要的。
本脚本是**给编辑用的正式工具**，按用户要求：

  1. **源文件必须由用户显式提供**（--source），不做任何自动发现，也不默认填充。
     找不到、读不出作者时**直接报错**，把选择权交回用户，绝不猜。
  2. **输出目录按规范命名**：作者「陈波」→ 生成 `Bo_Chen/Bo_Chen.tex`
     （多作者只取第一个）。见 slcn/naming.py。

     同时建立 `Bo_Chen/original/` 并把源稿归档进去，命名与往期各期一致
     （实测全库 92 篇文章都用 `original/`）。可用 --no-original 关闭。

     本工具**只负责初排环节**，因此目录名**不带序号前缀**。按编辑部流程，
     序号（如 `10 Bo_Chen` 的 `10`）是在三校 / 蓝样校对阶段、文章目录顺序
     确定之后才由人工加上去的——那一步不归本工具管。

用法
----
    # Word 文稿
    python3 convert.py \\
        --source "…/10 Bo_Chen/original/陈波 当代逻辑哲学鸟瞰.docx" \\
        --out    "…/2026年第5期(90)" \\
        --compile

    # LaTeX 源文件（多源合并：正文 + 上一轮模板稿的元数据）
    python3 convert.py \\
        --source   "…/03雅辛之谜（定稿）/main.tex" \\
        --metadata "…/03雅辛之谜（定稿）/yalcin puzzle.tex" \\
        --out      "…/2026年第5期(90)" \\
        --compile

    # 解析不对时当场手工覆盖正文信息（空值 = 不覆盖）
    python3 convert.py --source X.docx --out Y \\
        --title-cn "中文题名" --author-cn "张三" --affiliation-cn "中山大学" \\
        --email "a@b.cn" --abstract-cn "…" --title-en "…" --abstract-en "…"

    # 先看会做什么，不落盘
    python3 convert.py --source X.docx --out Y --dry-run
"""

from __future__ import annotations

import argparse
import inspect
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slcn import __version__ as ENGINE_VERSION  # noqa: E402
from slcn import clean as _clean  # noqa: E402
from slcn import platform as _plat  # noqa: E402
from slcn import refmark as _refmark  # noqa: E402
from slcn import naming as N  # noqa: E402
from slcn import sources as _sources  # noqa: E402
from slcn import templates as T  # noqa: E402
from slcn.build import build_docx, build_latex  # noqa: E402
from slcn.latex import parse_macros  # noqa: E402

WORD_EXT = {".docx", ".doc"}
TEX_EXT = {".tex"}

# ── 输出目录守卫：「这算不算本工具的东西」 ────────────────────────────────
#
# 重跑同一篇稿子时，目标目录里本来就躺着上一次的产物，不该拦；但如果目录里
# 混着别的东西，那更可能是编辑部已有的稿件目录，必须拦下（除非 --force）。
#
# ⚠️ 名单必须**完整**，否则第二次运行会被自己的产物拦住。实测漏过两类：
#      ① `SLCN-MIX.sty`（模板原名，不是 `<stem>.sty`）
#      ② `fig1.png` 这类从 docx 提取出的图片
#    也都别**过宽**，否则会把编辑部成果当成可覆盖的垃圾。
_OWN_FIXED = {".DS_Store", "reference.bib", "original"}
_OWN_STEM_SUFFIXES = (
    ".tex", ".pdf", ".bib", ".log",
    "_参考文献核对.txt", "_参考文献联网核实.txt", "_原稿.pdf",
    # 2026-10-15 新增：并入 AI 结果之后的离线回读校验报告（`apply_ai_merge()` 写）
    "_回读校验报告.md",
)
_IMG_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".eps", ".svg", ".pdf")

# 编译中间文件：本工具 `--clean` 会删的那批，外加 latexmk 的三样。
#
# 为什么单独列在这里、而不是加进 `slcn/clean.py`：那份清单必须与 EditToolBox
# `DeleteTempFeature.suffixes` **逐项一致**（「删除临时文件」两边行为不能分叉），
# 而 latexmk 的 `.fdb_latexmk` / `.fls` / `.xdv` 并不在那边。
# 这里只用于「要不要拦」这个判断——编辑部手工跑过一次 latexmk，不该因此
# 让重跑被拦下（实测 2026-10-13 就踩到了）。
_MID_SUFFIXES = tuple(s.lower() for s in _clean.CLEAN_SUFFIXES) + (
    ".fdb_latexmk", ".fls", ".xdv",
)

# ⑪ 里 `finish_reason` → 中文说明。官方文档（2026-10，
# https://api-docs.deepseek.com/zh-cn/api/create-chat-completion ）给的取值集合是
# `stop` / `length` / `content_filter` / `tool_calls` / `insufficient_system_resource`
# / `aborted`。
#
# 为什么整本字典都要有：只有 `stop`（以及老端点「没给这个字段」= 空串）算干净收尾，
# 其余每一个都意味着**这次回复不是完整答案**。从前只盯 `length`，于是被内容过滤、
# 服务端资源不足、被中断的半截 bibtex 会**照样**追加进《文献核实任务书》，编辑看不出
# 它不完整——假完整比明显缺失更危险。`length` 的文案在 ⑪ 里单独保留（沿用既有说法，
# 编辑已经认识那句，且它带着「调大 SLCN_LLM_MAX_TOKENS」这条可操作建议）。
_FINISH_WHY = {
    "content_filter": "被内容过滤",
    "insufficient_system_resource": "服务端资源不足被中断",
    "aborted": "生成被中断",
    "tool_calls": "意外返回了工具调用",
    # responses 通道（`slcn/llm.py: _responses_finish()`）多出来的两种：端点的
    # `status` 直接就是失败/不明不完整，同样**不算完整答案**，必须标注（第 54/59 条）。
    "failed": "端点报告本次响应失败（status=failed）",
    "incomplete": "端点报告本次响应不完整（status=incomplete，未给原因）",
}


def channel_provenance(channel: str, protocol: str, model: str,
                       web_search: bool, n_search: int) -> str:
    """「六、合并记录」里那行**本次核实通道**说明（纯函数，便于逐字断言）。

    为什么要把「检索证据」数进去、而不只是写「已启用联网检索」：开关开着**不等于**
    端点真的搜了（DeepSeek 的 `deepseek-flash` 会把 `web_search` 静默忽略；
    `deepseek-v4-pro` 才会真检索）。如果这行只说「已启用联网检索」，
    编辑事后翻合并记录会以为这批条目是联网核实的——那正是本项目最反对的「静默假装」。
    所以：`n_search == 0` 时必须**明写**「没有检索证据、按凭记忆对待」。
    """
    prov = f"本次核实通道：{channel}（协议 `{protocol}`，模型 `{model}`）"
    if not web_search:
        return prov
    if n_search:
        return prov + f"；**检索证据**：收到 {n_search} 个 `web_search_call`，确属联网核实"
    return (prov + "；⚠ **检索证据 0 个 `web_search_call`** —— 端点忽略了这个工具，"
            "本次结果按**凭记忆**对待（仍须人工抽查卷/期/页码/出版地）")


def is_own_product(name: str, stem: str) -> bool:
    """判断目标目录里的一个条目是不是「本工具产物 / 编译中间文件」。"""
    if name in _OWN_FIXED:
        return True
    # 并入 AI 结果时留下的回滚副本 `reference.bib.ai前`（2026-10-15，第 64 条）：
    # 不认它的话，**第二次跑同一篇稿子会被自己的回滚副本拦下**（「含非本工具的文件」）。
    if name.endswith(".ai前"):
        return name[: -len(".ai前")] in ("reference.bib", f"{stem}.bib")
    if name.lower().endswith(".sty"):
        return True
    if any(name == f"{stem}{suf}" for suf in _OWN_STEM_SUFFIXES):
        return True
    base, dot, ext = name.rpartition(".")
    if dot and base.lower().startswith("fig") and base[3:].isdigit():
        return ("." + ext.lower()) in _IMG_SUFFIXES
    return name.lower().endswith(_MID_SUFFIXES)


# ── 参考文献「内容指纹」：跨来源去重用 ────────────────────────────────────
#
# 同一份文献可能从两条通道进来（正文文末的编号清单 / 参考文献附件），**键却不同**
# （`Beall2000` vs `a1`），只按键去重挡不住。实测 02 Zixin_Luo：正文自带 19 条
# 编号清单、附件又是同一份，于是 bib 翻倍成 38 条。
_NORM_RE = re.compile(r"[\W_]+")


def _ref_fingerprint(author: str, title: str, year: str) -> tuple[str, str, str]:
    """作者 + 年份 + 题名的归一化指纹（去掉全部标点与空白，大小写无关）。"""
    def norm(s: str) -> str:
        return _NORM_RE.sub("", (s or "").lower())

    return (norm(author), norm(year), norm(title))


def _bib_fingerprints(bib_text: str) -> set[tuple[str, str, str]]:
    """从已有 .bib 文本里取出每条目的内容指纹。"""
    if not bib_text.strip():
        return set()
    from slcn.bibcheck import fields_of, split_entries

    out = set()
    for _etype, _key, body in split_entries(bib_text):
        f = fields_of(body)
        fp = _ref_fingerprint(f.get("author", ""), f.get("title", ""), f.get("year", ""))
        if fp != ("", "", ""):
            out.add(fp)
    return out


# ── 输入校验 ──────────────────────────────────────────────────────────────


def require_source(path: str) -> str:
    """强制校验源文件。这是「不替用户默认填充」的第一道关口。"""
    if not path:
        die("必须用 --source 显式指定源文件（.docx 或 .tex）。本工具不做自动发现。")
    p = os.path.abspath(path)
    if not os.path.exists(p):
        die(f"源文件不存在：{p}")
    if os.path.isdir(p):
        cands = [
            os.path.join(p, f)
            for f in sorted(os.listdir(p))
            if os.path.splitext(f)[1].lower() in (WORD_EXT | TEX_EXT)
        ]
        msg = f"--source 给的是文件夹：{p}\n请显式指定具体文件。"
        if cands:
            msg += "\n该文件夹下的候选：\n  " + "\n  ".join(cands)
        die(msg)
    ext = os.path.splitext(p)[1].lower()
    if ext not in (WORD_EXT | TEX_EXT):
        die(f"不支持的文件类型「{ext}」。目前支持 .docx / .doc / .tex。")
    if os.path.getsize(p) == 0:
        die(f"源文件是空文件：{p}")
    return p


def die(msg: str) -> None:
    print(f"\n❌ {msg}\n", file=sys.stderr)
    raise SystemExit(2)


# ── 语种判断 ──────────────────────────────────────────────────────────────


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def detect_lang(source: str, metadata: str | None = None) -> tuple[str, str]:
    """判断稿件语种，返回 (lang, 依据说明)。

    只看稿件本身，不看文件名。
    """
    ext = os.path.splitext(source)[1].lower()
    if ext in WORD_EXT:
        try:
            import sys as _s

            _ensure_vendor()
            # 用自己的 XML 直读，而不是 python-docx 的 paragraph.text——
            # 后者拿不到 OMML 公式，且会引入一个 app 打包时不必要的依赖。
            from slcn.docxxml import Para, read_blocks, segs_to_text

            paras = [segs_to_text(b.segs).strip() for b in read_blocks(source)
                     if isinstance(b, Para) and segs_to_text(b.segs).strip()]
            # ⚠️ 只看**题名**，不要看前几段。英文稿的 docx 顺序常是
            #   英文题名 → 英文摘要 → 英文关键词 → 中文题名 → 中文摘要 → 中文关键词 → 正文，
            #   取前 6 段会把中文块算进去，把英文稿误判成中文稿（实测 02 Lu_Jiang）。
            title = paras[0] if paras else ""
            if _has_cjk(title):
                return "zh", f"题名以中文为主：{title[:24]}"
            # 题名是外文时，再看正文确认（防止封面页是英文、正文是中文的怪稿）
            body_head = " ".join(paras[:40])
            cjk = sum(1 for c in body_head if "\u4e00" <= c <= "\u9fff")
            ratio = cjk / max(len(body_head), 1)
            if ratio > 0.30:
                return "zh", f"题名为外文但正文汉字占比 {ratio:.0%}"
            return "en", f"题名为外文：{title[:24]}"
        except Exception as e:  # noqa: BLE001
            die(f"无法读取 Word 文稿：{type(e).__name__}: {e}")

    # tex：EN 骨架有 mytitleCN，CN 骨架有 mytitleEN
    for p in (metadata, source):
        if not p or not os.path.exists(p):
            continue
        try:
            names = set(parse_macros(open(p, encoding="utf-8", errors="replace").read()))
        except Exception:  # noqa: BLE001
            continue
        if "mytitleCN" in names:
            return "en", f"{os.path.basename(p)} 用的是英文模板字段（mytitleCN）"
        if "mytitleEN" in names:
            return "zh", f"{os.path.basename(p)} 用的是中文模板字段（mytitleEN）"
    # 退路：看正文有没有汉字
    try:
        text = open(source, encoding="utf-8", errors="replace").read()[:4000]
        return ("zh" if _has_cjk(text) else "en"), "按正文汉字比例判断"
    except OSError:
        return "zh", "无法判断，回退中文"


def author_from_source(source: str, metadata: str | None = None) -> tuple[str, str]:
    """从源文件里读第一作者，返回 (作者, 来源说明)。读不到就返回空串，由上层报错。"""
    ext = os.path.splitext(source)[1].lower()
    if ext in WORD_EXT:
        try:
            _ensure_vendor()
            # 用自己的 XML 直读，而不是 python-docx 的 paragraph.text——
            # 后者拿不到 OMML 公式，且会引入一个 app 打包时不必要的依赖。
            from slcn.docxxml import Para, read_blocks, segs_to_text

            paras = [segs_to_text(b.segs).strip() for b in read_blocks(source)
                     if isinstance(b, Para) and segs_to_text(b.segs).strip()]
            # 第 2 段常是「姓名, 单位, 邮编」（实测 Lu Jiang, Department of Philosophy,
            # Sun Yat-sen University, 510275 共 76 字）。必须**先去单位再判断长度**，
            # 否则整段超长被挡掉，作者就读不出来了。
            # ⚠️ 原稿常见排布是「题名 → 副标题 → 作者」（实测 07 Liangying_Zhao：
            #   [0] 题名  [1] ——评赖兴巴赫…  [2] 赵亮英 陈晓平）。
            #   直接取第 2 段会把副标题当作者，输出名变成一串乱拼音。
            for i in range(1, min(6, len(paras))):
                line = paras[i]
                if line.startswith(("—", "——", "-", "–", "《", "（")):
                    continue  # 副标题
                if re.match(r"^[\u4e00-\u9fff]{0,4}[：:]", line):
                    continue  # 摘要/关键词行
                if re.match(r"^(Abstract|Keywords?|Key\s*words?|摘要|关键词)\b", line, re.I):
                    continue  # 英文稿的 Abstract / Keywords 行
                a = re.split(r"[,，、]", line)[0].strip()
                a = re.sub(r"\b(Distinguished|Professor|Prof|Dr|Mr|Ms|Mrs)\.?\b.*$", "", a, flags=re.I)
                # 多作者用空格分隔（`赵亮英 陈晓平`）时取第一个；
                # 但中文姓名内部也可能带空格（`陈  波`），用长度区分：
                # 折叠空白后不足 5 字的一律视为「一个名字」。
                collapsed = re.sub(r"\s+", " ", a).strip()
                if _has_cjk(a) and " " in collapsed and len(collapsed.replace(" ", "")) >= 5:
                    a = collapsed.split(" ")[0]
                else:
                    a = collapsed
                a = a.strip(" .、—-")
                if a and len(a) <= 40 and not a.endswith(("。", "：", ":")):
                    return a, f"Word 文稿第 {i + 1} 段：{line[:44]}"
        except Exception:  # noqa: BLE001
            pass
        return "", ""

    for p in (metadata, source):
        if not p or not os.path.exists(p):
            continue
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        a = N.author_from_tex_metadata(text)
        if a:
            return a, f"{os.path.basename(p)} 的 \\myfirstauthor：{a}"
    return "", ""


# ── 参考文献定位（仅在同目录下查找，不做跨目录搜索） ──────────────────────


def find_bib(source: str, explicit: str | None) -> str | None:
    if explicit:
        p = os.path.abspath(explicit)
        if not os.path.exists(p):
            die(f"--bib 指定的文件不存在：{p}")
        return p
    d = os.path.dirname(source)
    cands = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(".bib")]
    if not cands:
        return None
    ref = [c for c in cands if os.path.basename(c).lower() == "reference.bib"]
    return (ref or cands)[0]


# ── 第 76 条：英文元数据第二源定位（**同样只在同目录下查找**）──────────────
#
# 用户实测（2026年第5期(90)/10 Bo_Chen）：正文 docx 里只有中文，英文题名/摘要/作者
# 就在同目录的 `当代逻辑哲学鸟瞰英文摘要.docx` 里，引擎一直读得出来，却从来没接进
# Word 通道 → 编辑只能在界面「正文信息覆盖」里手工粘贴。
#
# 纪律（与 `find_bib()` 一字不差的口径）：
#   · **只看源文件所在的那一个目录**，不递归、不跨目录搜索；找不到返回 None，
#     照旧写占位符，绝不猜。
#   · 判据是**内容**（`sources.en_metadata_sheet()`：有英文字段 + 没有叙述正文），
#     不是文件名——所以同目录的另一篇英文全文稿、中文稿、缩写表都不会被选中。
def find_en_metadata(source: str, explicit: str | None = None) -> str | None:
    """给 Word 正文找一份同目录的「英文摘要」docx 当英文元数据第二源。

    explicit（--metadata）指到 .docx 时以它为准；指到别的类型（.tex 等，那是 LaTeX
    通道的语义）时本通道忽略它、仍按同目录查找。
    """
    if explicit and os.path.splitext(explicit)[1].lower() == ".docx":
        return os.path.abspath(explicit)
    if explicit:
        print(f"  （--metadata 指向的不是 .docx，Word 通道改按同目录查找英文摘要）")
    d = os.path.dirname(os.path.abspath(source))
    src = os.path.abspath(source)
    cands: list[tuple[int, int, str]] = []
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".docx") or f.startswith("~$"):
            continue
        p = os.path.join(d, f)
        if not os.path.isfile(p) or os.path.abspath(p) == src:
            continue
        fields = _sources.en_metadata_sheet(p)
        if fields:
            cands.append((len(fields), len(fields.get("abstract_en", "")), p))
    if not cands:
        return None
    # 多份候选：字段更全的优先，其次英文摘要更长的那份（同分时按文件名排序，确定性）
    cands.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return cands[0][2]


def mark_en_abstract_roles(inventory) -> int:
    """第 76 条：给 `multisource.scan()` 清单里的「英文元数据表」一个明确角色。

    为什么在这里补：`--scan` 与自动挑参考文献附件走的是 `multisource.scan()`
    （**不是** `sources.identify()`），而 `multisource.py` 不在本条的改动范围。
    判据与 Word 通道的第二源发现**共用** `sources.en_metadata_sheet()`，
    保证「清单里标的角色」和「转换时真正会用到的文件」是同一套判据。

    只改 `unknown` 的项（正文/参考文献/脚注/图等已有角色的不动），返回改判个数。
    角色名的中文显示（`英文元数据`）在 `multisource.ROLE_CN` 里，`--scan` 那一行不再写【未识别】。
    """
    n = 0
    for sf in inventory:
        if sf.role == "unknown" and os.path.splitext(sf.name)[1].lower() == ".docx":
            fields = _sources.en_metadata_sheet(sf.path)
            if fields:
                sf.role = "en_abstract"
                sf.reason = (f"英文元数据表（{len(fields)} 项英文字段："
                             "英文题名/摘要/关键词，可用作第二源）")
                n += 1
    return n


# ── 源稿归档到 original/ ──────────────────────────────────────────────────


def stage_original(
    out_dir: str,
    files: list[tuple[str, str]],
    copy_dir: bool = False,
    verbose: bool = True,
) -> tuple[str, list[str]]:
    """把本次用到的源文件复制到 `<out_dir>/original/`。

    目录名 `original` 与往期各期保持一致（实测全库 92 篇文章用的都是这个名字；
    仓库根 README 早期写作「原稿」，现行为 `original/`）。

    files     [(路径, 角色说明), ...]，即 --source / --metadata / --bib
    copy_dir  True 时把源文件所在**整个目录**也复制进来。
              LaTeX 投稿常是「一个文件夹里放 main.tex + 旧稿 + bib + sty + pdf」，
              往期就是这么整包归档的（如 03 Liang_Zhang/original/03雅辛之谜（定稿）/）。
    """
    dest = os.path.join(out_dir, "original")
    os.makedirs(dest, exist_ok=True)
    dest_abs = os.path.abspath(dest)
    copied: list[str] = []
    copied_dir = ""

    # 先复制整目录（若启用）——这样后面的单文件复制就能判断「是否已包含在内」，
    # 避免同一个文件既平铺一份、又随目录一份。
    if copy_dir and files:
        parent = os.path.dirname(os.path.abspath(files[0][0]))
        if parent and os.path.isdir(parent) and not dest_abs.startswith(parent + os.sep):
            sub = os.path.join(dest, os.path.basename(parent))
            if os.path.abspath(sub) != dest_abs:
                if os.path.exists(sub):
                    shutil.rmtree(sub)
                shutil.copytree(parent, sub)
                copied_dir = os.path.abspath(parent)
                n = sum(len(f) for _, _, f in os.walk(sub))
                copied.append(f"{os.path.basename(parent)}/")
                if verbose:
                    print(f"   · {os.path.basename(parent)}/  （整目录，{n} 个文件）")

    for path, role in files:
        if not path or not os.path.exists(path):
            continue
        src = os.path.abspath(path)
        # ① 已随整目录复制进去 → 不重复
        if copied_dir and src.startswith(copied_dir + os.sep):
            continue
        # ② 源文件本来就在目标 original/ 里（重跑已有稿件目录）→ 不重复
        if src.startswith(dest_abs + os.sep):
            if verbose:
                print(f"   · {os.path.basename(src)}（已在 original/ 内，跳过）")
            continue
        target = os.path.join(dest, os.path.basename(src))
        if os.path.exists(target) and os.path.samefile(src, target):
            continue
        shutil.copy2(src, target)
        copied.append(os.path.basename(src))
        if verbose:
            print(f"   · {os.path.basename(src)}  ← {role}")

    return dest, copied


# ── 编译 ──────────────────────────────────────────────────────────────────


def _find_db_root(source: str | None) -> str | None:
    """从源文件路径往上找到「数据库根目录」（即含 `2026年第5期(90)/` 这类目录的那一层）。"""
    if source:
        d = os.path.abspath(os.path.dirname(source))
        for _ in range(6):
            try:
                if any(re.match(r"^\d{4}年第", n) and os.path.isdir(os.path.join(d, n))
                       for n in os.listdir(d)):
                    return d
            except OSError:
                pass
            up = os.path.dirname(d)
            if up == d:
                break
            d = up
    try:  # 退回引擎自己所在的仓库（repo 布局）
        from slcn.templates import REPO_ROOT

        return REPO_ROOT
    except Exception:  # noqa: BLE001
        return None


def list_editors(source: str | None = None) -> list[tuple[str, int]]:
    """采集全库出现过的「责任编辑：XXX」姓名与篇数（给界面下拉用）。

    实测全库只有 3 个名字（执子 58 篇、袁之 52 篇、映之 36 篇），所以做成枚举最省事。
    """
    from collections import Counter

    root = _find_db_root(source)
    if not root or not os.path.isdir(root):
        return []
    c: Counter = Counter()
    for issue in sorted(os.listdir(root)):
        p = os.path.join(root, issue)
        if not (os.path.isdir(p) and re.match(r"^\d{4}年第", issue)):
            continue
        for slug in sorted(os.listdir(p)):
            d = os.path.join(p, slug)
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if not f.endswith(".tex"):
                    continue
                try:
                    t = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for m in re.finditer(r"责任编辑[：:]\s*([^）)]{1,12})[）)]", t):
                    n = m.group(1).strip()
                    if n and n != "某某某":
                        c[n] += 1
    return c.most_common()


def pdf_page_count(pdf_path: str) -> int:
    """数 PDF 页数（用于提示 --pages）。数不出来就返回 0，绝不因此报错。"""
    try:
        import sys

        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
        from pypdf import PdfReader

        return len(PdfReader(pdf_path).pages)
    except Exception:  # noqa: BLE001
        return 0


def compile_pdf(out_dir: str, stem: str) -> tuple[bool, str]:
    """xelatex → biber -l=zh_pinyin → xelatex ×2。"""
    steps = [
        ["xelatex", "-interaction=nonstopmode", f"{stem}.tex"],
        ["biber", "-l=zh_pinyin", stem],
        ["xelatex", "-interaction=nonstopmode", f"{stem}.tex"],
        ["xelatex", "-interaction=nonstopmode", f"{stem}.tex"],
    ]
    for i, cmd in enumerate(steps, 1):
        if shutil.which(cmd[0]) is None:
            return False, f"找不到命令 {cmd[0]}，请确认已安装 TeX Live"
        r = subprocess.run(
            cmd, cwd=out_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
        if r.returncode != 0 and i != 2:  # biber 无参考文献时可能非零，容忍
            log = os.path.join(out_dir, f"{stem}.log")
            detail = ""
            if os.path.exists(log):
                hits = [
                    ln.strip()
                    for ln in open(log, encoding="utf-8", errors="replace")
                    if ln.startswith("!")
                ][:3]
                detail = "；".join(hits)
            return False, f"{cmd[0]} 第 {i} 步失败（退出码 {r.returncode}）{('：' + detail) if detail else ''}"
    pdf = os.path.join(out_dir, f"{stem}.pdf")
    if not os.path.exists(pdf):
        return False, "编译结束但未生成 PDF"
    return True, pdf


# ── 正文信息手工覆盖（编辑兜底） ──────────────────────────────────────────
#
# 工具解析 Word 靠的是版面启发式（题名=首段、作者=第 2 段、摘要=「摘要：」行……），
# 总有解析不出来或解析错的稿子。编辑的诉求是**当场改掉、接着干活**，
# 而不是把稿子压在那里等程序修。这 7 个参数就是那条兜底输入路径：
# 填了就以人工值为准。
#
# 接口约定（界面 / convert.py / build.py 三方一致）：
#     overrides = {title_cn, author_cn, affiliation_cn, email,
#                  abstract_cn, title_en, abstract_en}
# 由 build 入口（build_docx / build_latex）以**关键字参数** `overrides=` 接收；
# None 或空 dict 都表示「不做任何覆盖」，由 build 侧实现具体替换。
#
# ⚠️ 界面与引擎不一定同时更新（用户可能只换了 app，也可能只换了 Engine/）：
#    老引擎配新界面**必须不崩**。所以这里用 inspect 探测签名，旧签名就不传，
#    只在日志里说明「本次覆盖未生效」。见 supports_overrides()。
_OVERRIDE_FIELDS: dict[str, str] = {
    "title_cn": "中文题名",
    "author_cn": "第一作者",
    "affiliation_cn": "单位",
    "email": "邮箱",
    "abstract_cn": "中文摘要",
    "title_en": "英文题名",
    "abstract_en": "英文摘要",
}


def collect_overrides(args: argparse.Namespace) -> dict[str, str]:
    """把 7 个「正文信息覆盖」参数收成一个 dict；**空值一律不进来**。

    「空值不进来」很关键：只有 overrides 非空才谈得上传参，
    于是「一个都不填」的运行与旧版**逐字等价**（build 侧连关键字都收不到）。
    """
    out: dict[str, str] = {}
    for key in _OVERRIDE_FIELDS:
        val = (getattr(args, key, None) or "").strip()
        if val:
            out[key] = val
    return out


def supports_overrides(fn) -> bool:
    """探测 build 入口是否接受 `overrides=` 关键字（向后兼容的关键一步）。"""
    try:
        return "overrides" in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # 取不到签名（内建/C 实现）→ 当作不支持
        return False


def overrides_kwargs(fn, overrides: dict[str, str]) -> dict:
    """按入口是否支持 `overrides=` 组装关键字；不支持就提示后不传（不崩）。"""
    if not overrides:
        return {}
    if supports_overrides(fn):
        return {"overrides": overrides}
    print(f"  ⚠ {getattr(fn, '__name__', 'build')} 尚不支持 overrides 参数，"
          "本次手工覆盖未生效（需 build.py 增加 overrides: dict | None = None）")
    return {}


# ── 主流程 ────────────────────────────────────────────────────────────────



def _ensure_vendor() -> None:
    """把项目本地的 vendor/ 挂到 sys.path —— **只在依赖确实缺失时**。

    ⚠️ vendor 里的 lxml 带**编译扩展**，是为某个特定 Python 版本装的。若无条件插到
    sys.path 最前面，另一个版本的 Python 会加载到不兼容的二进制——实测
    Python 3.12 加载了为 3.14 编译的 lxml，报
    `ImportError: cannot import name 'etree' from 'lxml'`，整条转换链断掉。
    所以先看已装的能不能用，不能用才回退 vendor。
    """
    import importlib.util
    import sys

    if importlib.util.find_spec("lxml") is not None:
        return
    v = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if os.path.isdir(v) and v not in sys.path:
        sys.path.append(v)   # append 而非 insert(0)，理由见 slcn/naming.py


# ── ⑪ 的收尾：把 AI 结果并入 reference.bib（2026-10-15 用户决定）───────────
#
# 旧口径是「AI 结果永不进产物」（README 第 41 条）：工具只出任务书，建议由编辑自己搬。
# 用户 2026-10-15 决定改掉它 —— AI 返回的条目要**直接并进 reference.bib**，这样
# `--compile` 出来的 PDF 就已经反映了核实结果。代价是「机器直接改学术事实」，所以：
#
#   · 合并的全部安全判据在 `slcn/aibib.py`（四条硬保障：key 不可改 / 恰好一条 /
#     不增删条目 / 未返回的条目逐字节不动），这里只负责落盘与告知；
#   · **回滚副本 `reference.bib.ai前`** = 本次合并之前的那一份（重跑会覆盖它）；
#   · 并完立刻跑一遍**离线**回读校验（`slcn/bibreport.py`，与 checkbib.py 同一份实现），
#     报告落在 `<stem>_回读校验报告.md`，结论一行打进控制台；
#   · 任务书追加「六、合并记录」：哪几条并了、改了什么、哪几条被挡下、为什么；
#   · **一条都没并进去时不动 bib、不写回滚副本**，并明说「未改动」——
#     「0 条并入」绝不能长得像成功。
#
# ⚠️ 整个函数**不许抛异常**：AI 通道是附加功能，它出问题不能连累出稿。


def apply_ai_merge(bib_path: str, taskbook_path: str, replies: dict[str, str],
                   stem: str, out_dir: str,
                   *, rejected: dict[str, str] | None = None,
                   provenance: str = "") -> dict:
    """并入 + 回滚副本 + 回读校验报告 + 任务书「合并记录」。

    `replies` 只放**正常收尾**的回复；`rejected` 放「AI 确实回了、但返回不完整
    （截断/内容过滤/中断）」的键 → 中文原因，它们**不并入**，但原因要写进合并记录。
    `provenance` = 本次核实通道的一行说明（协议 + 是否联网检索 + 模型名），写进
    「六、合并记录」——**留档**要让编辑事后能分清「联网查过的」与「凭记忆写的」。

    返回一份中文摘要 dict（`n_merged` / `n_skipped` / `rollback` / `report` /
    `conclusion` / `error`），由调用方决定怎么打印（0 条并入时不许像成功）。
    """
    from slcn import aibib, bibreport

    out: dict = {"n_merged": 0, "n_skipped": 0, "rollback": "", "report": "",
                 "conclusion": "", "error": ""}
    try:
        if not bib_path or not os.path.exists(bib_path):
            out["error"] = "本次没有 reference.bib，无从并入"
            return out
        original = open(bib_path, encoding="utf-8", errors="replace").read()
        result = aibib.merge(original, replies)
        for k, why in (rejected or {}).items():
            rec = result.by_key(k)
            if rec is not None and not rec.merged:
                rec.reason = why
        out["n_merged"], out["n_skipped"] = result.n_merged, result.n_skipped

        # 任务书的「合并记录」：**不管并进去几条都写**——被挡下来的原因必须留下来给人看。
        if taskbook_path and os.path.exists(taskbook_path):
            with open(taskbook_path, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n" + aibib.render_merge_section(result, provenance=provenance) + "\n")

        if result.n_merged == 0:
            out["error"] = "没有任何条目通过安全校验，reference.bib **未改动**"
            return out

        # ① + ② 回滚副本（先）与写回（后）：与手动「并回」通道（`checkbib.py --merge-into`）
        #    **共用**同一次落盘 —— `slcn/aibib.py: apply_merge_to_file()`。
        #    「先备份、后覆盖」这条安全轨只留一份实现，两条通道不许各写一遍。
        rollback = aibib.apply_merge_to_file(bib_path, result)
        out["rollback"] = rollback

        # ③ 立刻离线回读校验（与 checkbib.py 同一份报告实现）
        tex_path = os.path.join(out_dir, f"{stem}.tex")
        tex_text = None
        if os.path.exists(tex_path):
            tex_text = open(tex_path, encoding="utf-8", errors="replace").read()
        md, conclusion = bibreport.build_report_and_conclusion(
            result.text, original, tex_text,
            "合并后参考文献核对报告",
            returned_path=bib_path,
            original_path=rollback,
            tex_path=tex_path if tex_text is not None else "",
            source_note=bibreport.SOURCE_NOTE_MERGE,
        )
        report_path = os.path.join(out_dir, f"{stem}_回读校验报告.md")
        with open(report_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(md + "\n")
        out["report"], out["conclusion"] = report_path, conclusion
        return out
    except Exception as e:  # noqa: BLE001  附加通道，任何意外都不许连累出稿
        out["error"] = f"{type(e).__name__}: {e}"
        return out


def main() -> int:
    # 第 86 条：Windows 的 stdout 默认是控制台代码页（cp936），中文日志会被管道接走后
    # 乱码甚至 UnicodeEncodeError 崩在半路。这里**第一步**就切到 UTF-8（mac 上是空操作）。
    _plat.utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="convert.py",
        description="SLCN 稿件转换器：源文件 → 符合规范的初排稿（源文件必须显式提供）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # 引擎版本 + 运行环境：界面「关于」、Release 说明、同事报错时都要这一行。
    # 版本号只有一处（`slcn/__init__.py`），App 的 Info.plist 也从那里取。
    ap.add_argument("--version", action="version",
                    version=f"SLCN 引擎 {ENGINE_VERSION} ｜ {_plat.describe()}")
    ap.add_argument("--source", required=True, help="【必填】源文件（.docx / .doc / .tex）")
    ap.add_argument("--out", default=None, help="【必填】输出根目录，将在其下建立 <作者名>/ 文件夹")
    ap.add_argument("--metadata", default=None, help="LaTeX 多源合并时的元数据源（上一轮模板稿）")
    ap.add_argument("--bib", default=None, help="参考文献库 .bib（默认取源文件同目录下的）")
    ap.add_argument("--author", default=None, help="第一作者姓名（源里读不到时必须提供）")
    ap.add_argument("--name", default=None, help="直接指定输出名，如 Bo_Chen（跳过姓名推导）")
    ap.add_argument("--lang", choices=["zh", "en"], default=None, help="稿件语种（默认自动判断）")
    ap.add_argument("--sty", default=None, help="版面样式文件（默认中文 SLCN-MIX.sty / 英文 SLEN.sty）")
    ap.add_argument("--skeleton", default=None, help="骨架模板 .tex（默认 CN_template / EN_template）")
    # 编辑侧出版信息：作者稿里没有，缺了 PDF 页眉会是「xxxx 年第 x 期，1–10」
    ap.add_argument("--vol", default=None, help="卷号（编辑侧，如 19）")
    ap.add_argument("--issue", default=None, help="期号（编辑侧，如 5）")
    ap.add_argument("--year", default=None, help="出版年（编辑侧，如 2026）")
    ap.add_argument("--pages", default=None,
                    help="起止页（如 137-152，自动算出页数 16；也可只给首页 137）")
    ap.add_argument("--received", default=None, help="收稿日期（如 2025-09-19）")
    ap.add_argument("--editor", default=None, help="责任编辑姓名（自动包成「（责任编辑：XXX）」）")
    ap.add_argument("--grants", default=None, help="基金项目（写入模板被注释的 \\renewcommand 行）")
    # 正文信息手工覆盖（编辑兜底）：解析失败或解析不对时当场覆盖，不必等程序修。
    # 空值 = 不覆盖 —— 与「压根没填」完全等价，7 个全空时行为与旧版一字不差。
    ap.add_argument("--title-cn", default=None, help="【覆盖】中文题名")
    ap.add_argument("--author-cn", default=None,
                    help="【覆盖】第一作者（正文信息；输出名推导仍看 --author / --name）")
    ap.add_argument("--affiliation-cn", default=None, help="【覆盖】作者单位")
    ap.add_argument("--email", default=None, help="【覆盖】通信邮箱")
    ap.add_argument("--abstract-cn", default=None, help="【覆盖】中文摘要")
    ap.add_argument("--title-en", default=None, help="【覆盖】英文题名")
    ap.add_argument("--abstract-en", default=None, help="【覆盖】英文摘要")
    ap.add_argument("--list-editors", action="store_true",
                    help="只列出全库出现过的「责任编辑」姓名（每行「姓名<TAB>篇数」），供界面下拉用")
    ap.add_argument("--compile", action="store_true", help="生成后立即编译出 PDF")
    ap.add_argument("--scan", action="store_true",
                    help="只扫描源文件所在目录，列出各文件角色（正文/参考文献/脚注/图）后退出")
    ap.add_argument("--refs", default=None,
                    help="参考文献附件（docx/txt）。全库 28%% 的稿件把参考文献放在单独文件里，"
                         "用这个参数挂上才能替换正文引用")
    ap.add_argument("--verify-refs", action="store_true",
                    help="生成《文献核实任务书》markdown（写明编辑部规范、查证来源优先级与回答格式）。"
                         "若另配了 --llm-base/--llm-key/--llm-model（或环境变量 SLCN_LLM_*），"
                         "再按同一份规范自动跑一遍：结果除追加为「AI 建议（须人工核实）」外，"
                         "还会把通过安全校验的条目**并入 reference.bib**（回滚副本 reference.bib.ai前）"
                         "并出一份《回读校验报告》；要只出任务书、不并入，加 --no-ai-merge")
    # ⚠️ 2026-10-15 用户决定：AI 结果**要进产物**（改掉 README 第 41 条「AI 结果永不进产物」）。
    #    「并入」是改学术事实的动作，所以留一个显式的闸门：加了它，本次就只出任务书
    #    （与旧行为逐字节相同）。界面（Swift）也加了同一个开关：
    #    「保留原 reference.bib（不并入 AI 结果）」→ 只勾了 AI 时才显示。
    ap.add_argument("--no-ai-merge", action="store_true",
                    help="只出《文献核实任务书》，**不**把 AI 结果并入 reference.bib"
                         "（旧行为；界面「保留原 reference.bib（不并入 AI 结果）」= 这个开关）")
    ap.add_argument("--llm-base", default=None, help="OpenAI 兼容 API 的 base URL（也可用环境变量 SLCN_LLM_BASE）")
    ap.add_argument("--llm-key", default=None, help="API 密钥（环境变量 SLCN_LLM_KEY）")
    ap.add_argument("--llm-model", default=None, help="模型名（环境变量 SLCN_LLM_MODEL）")
    # 两条通道 + 联网检索（2026-10-15）：**默认值与不传这个参数时，请求体与从前逐字节
    # 相同**（chat、无 tools）——老调用方与既有套件都钉着这条。缺省（None）时回落到环境
    # 变量 SLCN_LLM_PROTOCOL / SLCN_LLM_WEB_SEARCH，所以命令行与环境变量两条入口等价。
    ap.add_argument("--llm-protocol", choices=["chat", "responses"], default=None,
                    help="通道：chat（默认，{base}/chat/completions）| "
                         "responses（{base}/responses，标准 Responses 协议）；"
                         "也可用环境变量 SLCN_LLM_PROTOCOL。不传=chat。")
    ap.add_argument("--llm-web-search", action="store_true",
                    help="本次**启用服务端联网检索**（只在 responses 通道生效：加 "
                         "tools:[{type:<--llm-search-tool>}]）；也可用环境变量 SLCN_LLM_WEB_SEARCH=1。"
                         "⚠️ 端点是否真的支持要跑检索探针（python -m slcn.llm --probe）——"
                         "不支持时模型只能凭记忆，本工具会如实报告，绝不假装已联网。")
    # 第 70 条的两个「未来兼容」旋钮：把硬编码的工具名与路径换成配置。
    # **默认 = 今天的取值**，不传这两个参数时命令行与 URL 与从前逐字节相同。
    # 为什么做成文本而不是布尔开关：将来服务商怎么命名、路径怎么改，现在无从预知
    # （各家实现并不一致：DeepSeek 的 flash 仍忽略内置检索工具、v4-pro 已支持；
    # 名字也未必都叫 web_search）。
    ap.add_argument("--llm-search-tool", default=None,
                    help="responses 通道 tools[].type 的工具名（默认 web_search；也可用环境变量 "
                         "SLCN_LLM_SEARCH_TOOL）。将来服务商换了名字（如 web_search_preview）"
                         "只改这一项，不必改代码。")
    ap.add_argument("--llm-responses-path", default=None,
                    help="responses 的路径，拼在 base 之后（默认 /responses；也可用环境变量 "
                         "SLCN_LLM_RESPONSES_PATH）。缺前导 / 会自动补上。")
    ap.add_argument("--quote-threshold", type=int, default=105,
                    help="引文超过多少字才放进 quotation 环境（默认 105 ≈ 三行）。"
                         "调低会把短引文也转换。")
    ap.add_argument("--clean", action="store_true",
                    help="清理编译中间文件（.aux/.bbl/.bcf/.blg/.out/.run.xml/.synctex.gz/"
                         ".DS_Store），规则与 EditToolBox「删除临时文件」完全一致；"
                         "故意不删 .log、.tex、.pdf、.sty、.bib")
    ap.add_argument("--source-pdf", action="store_true",
                    help="用 LibreOffice 把 Word 原稿转成 PDF 一并输出，"
                         "供「矢量形状」类对象人工截图（全库 235 个，需截图或重画）")
    ap.add_argument("--no-original", action="store_true",
                    help="不把源稿归档到 original/（默认会归档）")
    ap.add_argument("--copy-dir", action="store_true",
                    help="连源文件所在整个目录一起归档（LaTeX 投稿常是一个文件夹）")
    ap.add_argument("--dry-run", action="store_true", help="只显示将要做什么，不写任何文件")
    ap.add_argument("--force", action="store_true", help="目标文件夹已存在时覆盖（默认拒绝）")
    ap.add_argument("--list-templates", action="store_true", help="列出可用模板后退出")
    args = ap.parse_args()
    if not args.scan and not args.out and not args.list_editors:
        ap.error("除 --scan / --list-editors 外，必须指定 --out")
    if args.list_editors:
        for name, n in list_editors(args.source):
            print(f"{name}\t{n}")
        return 0

    # --scan：只列出源文件角色就退出（不需要 --out）
    # 非 --scan 时也扫一遍：自动挑出参考文献附件（全库 28% 的稿件把它单独放一个文件）
    from slcn import multisource

    _src_dir = (
        args.source if os.path.isdir(args.source)
        else os.path.dirname(os.path.abspath(args.source))
    )
    _inventory = multisource.scan(_src_dir)
    # ── 第 76 条：把「英文摘要 docx」从【未识别】改判为明确角色 en_abstract ──
    # ⚠️ `--scan` 走的是 `multisource.scan()`（不是 `sources.identify()`），而
    #    `multisource.py` 不在本条的改动范围（只读件），所以角色在这里补。
    #    判据与 Word 通道的第二源发现**共用** `sources.en_metadata_sheet()`。
    mark_en_abstract_roles(_inventory)
    if args.scan:
        print(multisource.format_inventory(_inventory, "源稿清单"))
        return 0
    if _inventory:
        _mains = [x for x in _inventory if x.role == "main"]
        if len(_mains) > 1:
            print("  ⚠ 发现多个「完整稿件」版本，建议用 --source 明确指定：")
            for x in _mains:
                print(f"      · {x.name}（{x.chars} 字符）")
        if not args.refs:
            _rf = multisource.pick(_inventory, "refs")
            if _rf:
                args.refs = _rf.path
                print(f"  → 自动采用参考文献附件：{_rf.name}（{_rf.reason}）")

    if args.list_templates:
        for lang, t in T.PRESETS.items():
            print(f"\n【{'中文' if lang == 'zh' else '英文'}稿件】默认")
            print(f"  版面样式  {t.sty}")
            print(f"  骨架      {t.skeleton}")
        print(f"\n可覆盖的 .sty：{'、'.join(T.AVAILABLE_STY)}")
        return 0

    # ① 强制校验源文件
    source = require_source(args.source)
    metadata = os.path.abspath(args.metadata) if args.metadata else None
    if metadata and not os.path.exists(metadata):
        die(f"--metadata 指定的文件不存在：{metadata}")

    # 第 76 条：Word 通道的英文元数据第二源（仅同目录；LaTeX 通道不用这个）。
    # 在**转换之前**就定下来，好让下面「源文件 / 第二源」两行日志把来源交代清楚。
    en_meta = (find_en_metadata(source, metadata)
               if os.path.splitext(source)[1].lower() in WORD_EXT else None)

    print("═" * 74)
    print("SLCN 稿件转换器")
    print("═" * 74)
    print(f"源文件    {source}")
    if en_meta:
        print(f"第二源    {en_meta}")
        print("          （英文题名/摘要等仅当正文自身读不出时才取；仅同目录，见第 76 条）")

    # ② 语种
    if args.lang:
        lang, why = args.lang, "用户用 --lang 指定"
    else:
        lang, why = detect_lang(source, metadata)
    print(f"语种      {lang}（{why}）")

    # ③ 模板
    sel = T.resolve(lang, sty=args.sty, skeleton=args.skeleton)
    problems = T.validate(sel)
    if problems:
        die("模板不可用：" + "；".join(problems))
    print(f"模板      {T.describe(sel)}")

    # ④ 输出名（强制：拿不到就问用户，不猜）
    author, author_why = author_from_source(source, metadata)
    try:
        name, name_why = N.resolve_output_name(
            explicit_name=args.name,
            explicit_author=args.author,
            author_from_source=author,
            source_desc=os.path.basename(source),
        )
    except N.AuthorNameError as e:
        die(str(e))
    print(f"第一作者  {author or '（未从源读到）'}  ← {author_why or '用户指定'}")
    print(f"输出名    {name}  ← {name_why}")

    for h in N.homographs(author or args.author or ""):
        print(f"  ⚠ 多音字需确认：{h}  如读音不对，请用 --name 显式指定")

    out_root = os.path.abspath(args.out)
    # 若用户选的目录**本身就叫这个名字**（如 …/Desktop/Bo_Chen），就用它，
    # 不再套一层。否则会生成 …/Desktop/Bo_Chen/Bo_Chen/，
    # 且第二次运行时会因为「目标已存在」直接拒绝——实测踩到过。
    if os.path.basename(out_root) == name:
        out_dir = out_root
    else:
        out_dir = os.path.join(out_root, name)
    stem = name
    print(f"输出到    {out_dir}{os.sep}{stem}.tex")

    # 正文信息手工覆盖（选填）：填了就以人工值为准，空值不覆盖。
    # 声明在 dry-run 之前，方便「先看一眼会怎么做」时也能确认覆盖值收对了。
    overrides = collect_overrides(args)
    if overrides:
        print("正文覆盖  " + "、".join(_OVERRIDE_FIELDS[k] for k in overrides)
              + f"（{len(overrides)} 项，人工值优先）")

    if os.path.exists(out_dir) and not args.force:
        contents = sorted(os.listdir(out_dir))
        # 用户**显式选了这个目录**（名字与输出名一致）时，意图明确，不必再拦。
        # 但仍要保护「里面装着别的文章」的情况——那才是真的可能覆盖到编辑部成果。
        # 允许的情况：空的、只有系统垃圾文件（.DS_Store）、或只含本工具自己的产物。
        # 判定规则见文件头的 `is_own_product()`（本工具产物 + 编译中间文件）。
        foreign = [c for c in contents
                   if not is_own_product(c, stem) and not c.startswith(".")]
        # 判据只有一条：**目录里有没有不属于本工具的东西**。
        #   · 只有本工具的产物 → 直接覆盖，这就是「重跑一次」的常见情形，不该拦。
        #   · 混着别人的文件 → 拦住，那才可能是编辑部已有的稿件目录。
        #
        # ⚠️ 这里曾经还加过一个「用户选的是不是同名目录」的条件，是错的：
        #    正常用法是 `--out ~/Desktop` → `~/Desktop/Bo_Chen`，两者不同名，
        #    于是**每次重跑都被拦**，哪怕目录里全是自己的产物。
        if foreign:
            die(
                f"目标文件夹已存在，且含非本工具的文件，拒绝覆盖：{out_dir}\n"
                f"  不属于本工具产物的 {len(foreign)} 项："
                f"{'、'.join(foreign[:6])}{' …' if len(foreign) > 6 else ''}\n"
                "  这可能是编辑部已有的稿件目录。确认要覆盖请加 --force，"
                "或改用 --name 指定别的输出名。"
            )

    if args.dry_run:
        print("\n（--dry-run：以上为将要执行的操作，未写入任何文件）")
        return 0

    # ⑤ 生成
    print()
    meta = {
        "vol": args.vol, "issue": args.issue, "year": args.year, "pages": args.pages,
        "received": args.received, "editor": args.editor, "grants": args.grants,
    }
    ext = os.path.splitext(source)[1].lower()
    try:
        if ext in WORD_EXT:
            res = build_docx(
                source, out_dir, basename=stem, lang=lang,
                sty=sel.sty, skeleton=sel.skeleton, verbose=True,
                quote_threshold=args.quote_threshold, meta=meta,
                # 第 76 条：英文元数据第二源（同目录的「英文摘要」docx）。
                # None = 不接第二源 = 与从前逐字节相同。
                metadata_path=en_meta,
                # overrides 只在 build.py 已支持该关键字时才传（见 overrides_kwargs），
                # 旧引擎配新界面不会崩，只是这次覆盖不生效并在日志里说明。
                **overrides_kwargs(build_docx, overrides),
            )
        else:
            res = build_latex(
                source, out_dir,
                metadata_path=metadata,
                bib_path=find_bib(source, args.bib),
                basename=stem, lang=lang,
                sty=sel.sty, skeleton=sel.skeleton, verbose=True, meta=meta,
                **overrides_kwargs(build_latex, overrides),
            )
    except Exception as e:  # noqa: BLE001
        die(f"生成失败：{type(e).__name__}: {e}")

    # ⑦ 合并参考文献附件（全库 28% 的稿件把参考文献放在单独文件里）
    #
    # 实测 02 Zixin_Luo：正文 docx 里没有参考文献，全在 `附件一 完整参考文献.docx`。
    # 不合并的话引用全部无法替换（22 处）。
    if args.refs and os.path.exists(args.refs):
        from slcn import footref

        print("\n⑦ 合并参考文献附件")
        txt = ""
        if args.refs.lower().endswith((".docx", ".doc")):
            from slcn.multisource import _docx_text

            txt, _ = _docx_text(args.refs)
        else:
            txt = open(args.refs, encoding="utf-8", errors="replace").read()

        # 编号清单（`[1] 作者, 年, "题名", 刊名, 卷(期): 页`）优先；
        # 否则按「整条 in note」的脚注体例处理
        items = footref.parse_numbered_list(txt)
        if not items:
            items = footref.parse_note(txt)
        if items:
            # 正文没解析出参考文献时工具不会建 bib，这里补建一个
            bibp = res.bib_path or os.path.join(out_dir, f"{stem}.bib")
            have = ""
            if os.path.exists(bibp):
                have = open(bibp, encoding="utf-8", errors="replace").read()
            elif not res.bib_path:
                with open(bibp, "w", encoding="utf-8", newline="\n") as f:
                    f.write("% 由参考文献附件生成\n\n")
                res = res.__class__(**{**res.__dict__, "bib_path": bibp})
            added = []
            # 去重不能只比键：附件条目用 `a1…`，而正文文末那条编号清单解析出来的是
            # `Beall2000` 这种键——**同一份文献、两个键**。
            # 实测 02 Zixin_Luo：正文自带 19 条编号清单，附件又是同一份，
            # 只按键去重会让参考文献翻倍成 38 条。这里按「作者+年份+题名」的内容指纹去重。
            seen = _bib_fingerprints(have)
            skipped_dup = 0
            for i, r in enumerate(items, 1):
                key = f"a{i}"
                if f"{{{key}," in have:
                    continue
                fp = _ref_fingerprint(r.author, r.title, r.year)
                if fp in seen:
                    skipped_dup += 1
                    continue
                seen.add(fp)
                fields = "\n".join(
                    f"  {n.ljust(9)} = {{{getattr(r, n)}}},"
                    for n in ("author", "title", "journal", "booktitle", "volume",
                              "number", "pages", "publisher", "address", "year")
                    if getattr(r, n)
                )
                added.append(f"@{r.etype}{{{key},\n{fields}\n}}")
            if added:
                with open(bibp, "a", encoding="utf-8", newline="\n") as f:
                    f.write("\n% ── 由参考文献附件合并 ──\n" + "\n".join(added) + "\n")
                print(f"   从附件合并 {len(added)} 条 → {os.path.basename(bibp)}")
                if skipped_dup:
                    print(f"   （另有 {skipped_dup} 条与正文已解析出的条目重复，已跳过）")
                print(f"   ⚠ 引用键需人工对齐：附件条目键为 a1、a2…，正文引用需相应调整")
            elif skipped_dup:
                print(f"   附件 {skipped_dup} 条与正文已解析出的条目重复，无需合并")
            else:
                print("   附件条目与现有 bib 无新增")
        else:
            print(f"   ⚠ 未能从 {os.path.basename(args.refs)} 解析出条目")

    # ⑧ 源稿归档到 original/（与往期文件夹命名一致）
    if not args.no_original:
        print("\n⑧ 归档源稿到 original/")
        stage_original(
            out_dir,
            [(source, "正文源")] + ([(metadata, "元数据源")] if metadata else []),
            copy_dir=args.copy_dir,
        )

    if res.warnings:
        print("\n⚠ 注意事项：")
        for w in res.warnings:
            print(f"  · {w}")

    # ⚠️ ⑨ 编译 / ⑩ 导出原稿 PDF **挪到了 ⑪ 之后**（2026-10-15，见下面第 ⑪ 节末尾）：
    #    用户决定「AI 结果要进产物」，而并进去的条目必须体现在 PDF 里——所以顺序必须是
    #    「写 bib → 并入 AI 结果 → 编译」。非 AI 路径（没配 LLM）产物逐字节不变，
    #    只是控制台上 ⑪ 的几行现在打在 ⑨ 之前。

    # ⑪ 参考文献核实任务书（交给编辑各自的 AI）+ 把 AI 结果**并入 reference.bib**
    #
    # ⚠️ 2026-10-14 按编辑部要求**移除了 CrossRef 通道**：中文文献覆盖差，
    #    而且会给跨类型错配的「建议」（书被判成期刊论文，照着改就是照错改）。
    #    改为生成一份 markdown：写明编辑部规范、查证来源优先级、每条缺什么、
    #    以及固定回答格式——编辑把它交给自己的 AI（谁的模型都行）。
    #    `SLCN_LLM_*` 仍保留：配了就按**同一份规范**自动跑一遍，结果标为「AI 建议，须核实」。
    # ⚠️ 2026-10-15 用户决定：**AI 结果要进产物**（取代 README 第 41 条的「AI 结果永不进产物」）。
    #    配了 LLM 时，通过安全校验的条目由 `slcn/aibib.py` 并入 `reference.bib`
    #    （回滚副本 `reference.bib.ai前` + 离线回读校验报告 + 任务书「六、合并记录」）。
    #    因此这一节必须在**编译之前**跑，PDF 才会反映并入结果——见上面 ⑨/⑩ 挪走的说明。
    #    要退回旧行为：`--no-ai-merge`（只出任务书）。没配 LLM 时行为与从前一字不差。
    if args.verify_refs:
        from slcn import llm
        from slcn.bibcheck import split_entries

        bib_text = (
            open(res.bib_path, encoding="utf-8", errors="replace").read()
            if res.bib_path else ""
        )
        # 文献区**原文**：解析器一条都没认出来时，把这些行原样交给编辑的 AI 去结构化
        # （实测 14 篇稿件有文献区却 `n_bib = 0`，其中 9 篇确实有 10–27 行候选条目：
        #  8 篇人年制无编号 + 1 篇日文 `[N]` 清单。编辑部已把文献核实交给编辑各自的 AI，
        #  见 README 第 41 条——所以工具**不再写第二种清单解析器**，「编错的卷号比空缺更糟」）。
        #
        # ⚠️ 第 68 条起，这两份材料由 `build_docx` 直接带出来（`BuildResult.ref_raw_lines` /
        #    `ref_raw_by_key`），**不再**在这里重新解析一遍源稿：从前那样做的理由是
        #    「build_docx 只返回 BuildResult，原文行拿不到」，现在挂在 BuildResult 上了；
        #    而且是**逐条**原文（`ref_raw_by_key`），正常出条目的稿子也拿得到。
        #    LaTeX 通道没有源稿原文，两份都是空的（下游按「没有原文」处理）。
        raw_ref_lines: list[str] = list(res.ref_raw_lines)
        raw_by_key: dict[str, str] = dict(res.ref_raw_by_key)

        # LLM 配置（含通道与检索开关）在这里先算出来：任务书头部那行**通道说明**要用它。
        # ⚠️ `--llm-protocol` / `--llm-web-search` 缺省是 None / False，分别回落到环境变量
        #    SLCN_LLM_PROTOCOL / SLCN_LLM_WEB_SEARCH；两个入口等价（见 argparse 的 help）。
        #    `web_search=False` 与「没传」**不能**混：前者会**覆盖**环境变量，所以在命令行
        #    没给这个开关时必须显式回落（`None`），这就是下面那个三元表达式的全部理由。
        #    第 70 条的两个新参数同理：None → 环境变量 → 默认（`/responses` / `web_search`）。
        cfg = llm.config_from_env(
            args.llm_base, args.llm_key, args.llm_model,
            protocol=args.llm_protocol,
            web_search=(True if args.llm_web_search else None),
            search_tool=args.llm_search_tool,
            responses_path=args.llm_responses_path,
        )
        # 本次核实通道（**一行说清「联网了没有」**）：写进控制台、任务书头部、合并记录。
        # 为什么三种取值都写「未启用检索」：编辑真正要知道的是「模型能不能看到网络」，
        # 而不是我们发的是哪个 HTTP 路径。
        # 第 70 条：工具名也写进去（配置成什么就写什么）——默认值是 `web_search`，
        # 所以 f-string 出来的字符串与从前的字面量**逐字节相同**（既有断言不受影响）。
        if cfg.uses_web_search:
            channel = f"responses + {cfg.search_tool}（已启用联网检索）"
        elif cfg.protocol == "responses":
            channel = "responses（未启用检索：模型只能凭记忆）"
        else:
            channel = "chat（未启用检索：模型只能凭记忆）"

        # 任务书头部：配了 LLM 才替换掉那句「工具不做联网核实」（那时它不成立）。
        # 没配 LLM 时**不传** search_note → 头部与从前逐字节相同。
        search_note = ""
        if cfg.ready:
            search_note = (f"> 本次核实通道：{channel}；协议 `{cfg.protocol}`，"
                           f"模型 `{cfg.model}`。\n"
                           "> 逐条来源标注见每条上方的 `% 来源：…`；"
                           "本次是否真的联网核实，以下面的通道说明与合并记录为准。")

        # ⚠️ 旧条件是 `res.bib_path`：这批「有文献区、0 条」的稿子根本没有 bib 文件，
        #    于是**连任务书都不生成**——而它们正是最需要把原文交出去的一批。改为「有 bib
        #    或有原文」都生成；两种旧情形的产物逐字节不变（第五节只在「0 条 + 有原文」时追加）。
        if res.bib_path or raw_ref_lines:
            print("\n⑪ 生成参考文献核实任务书", flush=True)
            md = _refmark.build(bib_text, stem, raw_ref_lines=raw_ref_lines,
                                raw_by_key=raw_by_key, search_note=search_note)
            path = os.path.join(out_dir, f"{stem}_文献核实.md")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(md)
            print(f"   {os.path.basename(path)}（{md.count(chr(10))} 行）")
            # 逐条原文的覆盖情况当场说清楚：编辑要能看出「这份任务书里有几条是带原文的」。
            n_with_raw = sum(1 for _et, k, _b in split_entries(bib_text) if raw_by_key.get(k))
            if raw_by_key:
                print(f"   其中 {n_with_raw} 条附上了源稿原文"
                      "（解析可能有遗漏，任务书里已写明以原文为准）")
            else:
                print("   本次没有可用的源稿原文（LaTeX 通道 / 作者自带的 .bib）")
            print("   交给编辑各自的 AI：文件里已写明规范、来源优先级与回答格式")

        entries = list(split_entries(bib_text))
        if not entries:
            # 「文献区一条都没解析出来」的稿子没有可预跑的条目：必须在这里单独收口，
            # 否则会掉进下面 `n_got == 0` 的失败分支，报一次**根本没发生**的「AI 调用失败」。
            print("   （bib 里没有可核实的条目：只出任务书，由编辑交给自己的 AI）")
        elif cfg.ready:
            # 只打印地址与模型名（诊断「地址填错」全靠它）。**绝不打印 cfg.key。**
            # `flush=True`：这一节最慢（逐条联网），进度必须**当场**出现在运行日志里，
            # 不能等 Python 的块缓冲（GUI 用管道接 stdout，默认 8KB 才刷一次）。
            print(f"   已配置 LLM（{cfg.model} @ {cfg.base}）：按同一份规范自动跑 {len(entries)} 条…"
                  f"（每条一次请求，默认超时 60 秒；每条**开始前**会打一行 `[i/N] 键名`，"
                  f"看到它就知道卡在哪一条）", flush=True)
            # **本次核实通道**：编辑看日志第一眼要能回答「这次到底联网了没有」。
            # 这一行与任务书头部、合并记录用的是同一个 `channel` 字符串（单一出处）。
            print(f"   本次核实通道：{channel}")
            # 检索开关只对 responses 通道有意义（chat 通道的 tools 是 function calling，
            # 要在客户端自己执行，不是检索）——见 `slcn/llm.py: LLMConfig.uses_web_search`。
            system = _refmark.SPEC_MD + "\n" + _refmark.SOURCES_MD + "\n" + _refmark.FORMAT_MD
            if cfg.uses_web_search:
                # 只有**确实带上了检索工具**时才告诉模型「你能联网」；关着时说这句就是撒谎。
                system += "\n" + _refmark.SEARCH_MD
                print("   ⚠ 已于本次提示词中加入「必须先用检索工具核对官方来源」一节；"
                      "端点是否真的检索，跑完看下面的检索证据（0 次 = 凭记忆作答）")
            advice = ["", "---", "", "# AI 建议（**须人工核实**，工具不保证正确）", ""]
            # 请求参数集中由 llm.request_params_from_env() 给（只用 SLCN_LLM_MAX_TOKENS 与
            # SLCN_LLM_THINKING）：给输出封顶、并按用户意愿决定要不要思维链——
            # **别把额度耗在推理上被截半句**，截断只会表现为「bibtex 代码块残缺」，看不出真因。
            # 官方文档（2026-10）写明 `thinking` **默认 enabled**，而思考模式下
            # `temperature` 没有效果（本通道发 temperature=0 求确定性，只在非思考模式成立）、
            # 推理 token **按输出计费**且能吃掉整份 max_tokens——而 ⑪ 是纯抽取/规范化，
            # 本来就不需要思维链，所以 `SLCN_LLM_THINKING=0` 是推荐配置。
            # ⚠️ 这条通道**显式**传 response_format=None，**不读 SLCN_LLM_JSON**：
            # ⑪ 要的是**整份 bibtex 文本**（`refmark.FORMAT_MD` 第 9 条），不是 JSON；
            # 逼成 JSON 对象既违规范、会让任务书里的建议变形，又会被端点以
            # 「prompt 里没有 json 字样」400 掉（DeepSeek）。JSON 模式（SLCN_LLM_JSON）
            # 默认关闭，留给将来需要结构化返回的调用。
            _, max_tokens, thinking = llm.request_params_from_env()
            # 本次运行**实际**发送的 thinking：端点若拒收这个字段（400），本条去掉它重试
            # 一次后置 None —— 剩下的条目也不再发它，避免每条都白撞一次 400。
            send_thinking = thinking
            thinking_warned = False
            n_got = 0
            n_try = 0
            fail_streak = 0
            n_calls = 0       # 真正发出去的请求数，用于收尾的用量汇总
            tokens_in = 0
            tokens_out = 0
            tokens_reason = 0   # 推理 token（`usage.completion_tokens_details.reasoning_tokens`）
            # 检索证据累计（responses + web_search 时才有意义）：`LAST_SEARCH` 是**逐次**
            # 调用的，收尾要的是「整轮跑下来到底检索了几次、引了哪些页面」。
            # 为什么非数不可：`tools:[{"type":"web_search"}]` 只是请求，有的端点/模型（如
            # DeepSeek 的 flash）会**静默忽略**它并照样回 200 —— 不数这个，我们就会把
            # 「凭记忆」当「已联网核实」汇报。
            n_search = 0
            search_urls: list[str] = []
            # 本次要**并入 reference.bib** 的回复（key → 原文）。只收「正常收尾」的：
            # 被截断 / 被内容过滤 / 被中断的那几条**一律不并入**——它们只是半条条目，
            # 并进去等于把残缺当完整（「假完整比明显缺失更危险」，第 54/59 条）。
            replies: dict[str, str] = {}
            unclean_replies: dict[str, str] = {}
            for et, k, body in entries:
                n_try += 1
                # 进度：**发起请求之前**先打一行，这样「卡住」时能一眼看出卡在哪一条
                # （界面上一条请求最长可能等 60 秒，中间没有任何输出会很像是死掉了）。
                # `flush=True` 是硬要求：GUI 用管道收 stdout，块缓冲会把这一行压到后面去。
                print(f"   [{n_try}/{len(entries)}] {k}：正在核实…", flush=True)
                # 第 68 条：把这一条的**源稿原文**一并交给模型（没有原文时 `entry_prompt`
                # 返回的字符串与从前逐字节相同）。第 66 条的教训是：解析器丢字段时，
                # 模型只能「如实」报告我们丢的信息——看上去像模型不行，其实喂的是残缺件。
                # `search=` 只在开了联网检索时追加一行引导（关着时逐字节不变）。
                prompt = _refmark.entry_prompt(et, k, body, raw_by_key.get(k, ""),
                                               search=cfg.uses_web_search)
                n_calls += 1
                # 统一入口：chat（默认，逐字节旧行为）/ responses（见 slcn/llm.py）。
                got = llm.complete(cfg, system, prompt, response_format=None,
                                   max_tokens=max_tokens, thinking=send_thinking)
                # `thinking` 是**可选扩展**：老端点/自建服务不认识它时会 400
                # （文案里带 thinking）。不能为一个可选参数把整条通道打灭，所以
                # **本条**去掉该字段重试一次；成功则记住「本次运行不再发它」
                # （与当年 response_format 的降级同一个思路）。
                # ⚠️ 只认这一种 400（判据见 llm.thinking_rejected）：别的 400（上下文
                # 超限之类）与这个字段无关，重试只会每条白跑一次。重试**也**失败时照旧
                # 走下面的失败分支（fail_streak / 提前收手 / 只出任务书），绝不吞掉真失败。
                if got is None and send_thinking is not None and llm.thinking_rejected():
                    if not thinking_warned:
                        print("   ⚠ 端点不支持 thinking 字段（HTTP 400），本条改为不带该字段重试；"
                              "本次运行其余条目也不再发送它")
                        thinking_warned = True
                    send_thinking = None
                    n_calls += 1
                    got = llm.complete(cfg, system, prompt, response_format=None,
                                       max_tokens=max_tokens, thinking=None)
                if got:
                    n_got += 1
                    fail_streak = 0
                    # 检索证据累计（responses + web_search）：`LAST_SEARCH` 每次调用都会重置，
                    # 必须**当场**收走，否则收尾时只剩最后一条的数字。
                    _ev = llm.LAST_SEARCH or {}
                    n_search += int(_ev.get("n_calls", 0))
                    search_urls += [u for u in _ev.get("urls", []) if u not in search_urls]
                    # ⚠️ 端点**不报错**也不代表是完整答案：`max_tokens` 截断（length）、
                    # 被内容过滤、服务端资源不足、生成被中断…… 都照样回 200 和一段半截内容
                    # （判据见 slcn/llm.py 的 LAST_FINISH；取值集合来自官方文档 2026-10）。
                    # 提示词要的是**整份 bibtex 代码块**，所以这些结果都是「半条条目」——
                    # 若不标注就照原样追加，编辑会把残缺条目当成完整建议照抄
                    # （假完整比缺失更糟）。判据 = **非 stop 且非空**；空串是「老端点没给
                    # 这个字段」，不能当异常（会误报）。
                    # 两处都要标：**stdout 一句告警**（当场可见）+ **任务书里一行引用**
                    # （离开这次运行、别人单独看 markdown 时也看得出这条不完整）。
                    finish = llm.LAST_FINISH
                    unclean = finish not in ("", "stop")
                    head = f"## `{k}`\n\n"
                    if unclean:
                        why = _FINISH_WHY.get(finish, finish)   # 不认识的取值原样带上
                        if finish == "length":
                            # 沿用既有文案（编辑已认识这句，且它带着「调大
                            # SLCN_LLM_MAX_TOKENS」这条可操作建议）。
                            # 引用行与被警告的内容之间留一个空行：markdown 里引用行紧跟代码
                            # 围栏会被并进同一个引用块（缩进/归属出错），空行让两者各自成块。
                            head += "> ⚠️ 本条 AI 返回被截断（finish_reason=length），内容不完整。\n\n"
                        else:
                            head += (f"> ⚠️ 本条 AI 返回未正常结束——{why}"
                                     f"（finish_reason={finish}），内容可能不完整。\n\n")
                    advice.append(head + f"{got.strip()}\n")
                    if unclean:
                        # 半条条目**不并入**（见 replies / unclean_replies 的说明），
                        # 但原因要留下来——任务书「六、合并记录」里会逐条写清楚。
                        unclean_replies[k] = (
                            f"AI 返回未正常结束（finish_reason={finish}"
                            f"{'，即被 max_tokens 截断' if finish == 'length' else ''}），"
                            "按「假完整比明显缺失更危险」的纪律**不并入**；原文里的这一条逐字节未动"
                        )
                        if finish == "length":
                            print(f"   ⚠ {k}：返回被 max_tokens 截断（finish_reason=length），"
                                  "本条**不完整**，建议调大 SLCN_LLM_MAX_TOKENS 或人工核实")
                        else:
                            print(f"   ⚠ {k}：AI 返回未正常结束——{why}"
                                  f"（finish_reason={finish}），本条可能**不完整**，请人工核实")
                    else:
                        replies[k] = got
                    # 用量逐条打出来：额度是编辑部的真金白银，跑完只给总数看不出是哪条贵。
                    # 推理 token 单独点出来（官方文档 2026-10：它按**输出**计费，混在
                    # completion 里看不出钱烧在思维链上），>0 才写，免得正常跑刷一串 0。
                    use = llm.LAST_USAGE
                    tokens_in += use.get("prompt_tokens", 0)
                    tokens_out += use.get("completion_tokens", 0)
                    reason = use.get("reasoning_tokens", 0)
                    tokens_reason += reason
                    line = (f"   {k}：tokens {use.get('prompt_tokens', 0)}"
                            f"+{use.get('completion_tokens', 0)}")
                    if reason > 0:
                        line += f"（含推理 {reason}）"
                    print(line)
                else:
                    fail_streak += 1
                    # 连续失败多半不是「这一条内容的问题」，而是网络/地址/密钥/模型名的问题
                    # （`llm.chat` 对失败一律返回 None）。与其把剩下的条目各超时一遍
                    # （默认 60 秒/条，一期几十条就是几十分钟），不如早点收手并说清楚。
                    if fail_streak >= 3:
                        break
            if n_calls:
                # 只在真的调用过时才报（未配置 / 条目为空时不必刷一行 0）。
                # 推理 token 合计只在 >0 时附上：它是输出计费的一部分，不点出来
                # 编辑只会看到 completion 偏高而不知钱花在思维链上。
                reason_note = f"（含推理 {tokens_reason}）" if tokens_reason > 0 else ""
                print(f"   共 {n_calls} 次调用，{tokens_in} prompt + {tokens_out} completion tokens"
                      f"{reason_note}")
            # ── 联网检索的**证据**（只有开了检索开关才说）─────────────────────────
            #
            # 这一节是「不许静默假装」的落点：`tools:[{"type":"web_search"}]` 只是**请求**，
            # 有的端点/模型（如 DeepSeek 的 flash）会静默忽略它并照样回 200。
            # 所以这里报的是**端点的行为**（收到几个 web_search_call），而不是我们的开关状态。
            if cfg.uses_web_search and n_calls:
                if n_search:
                    print(f"   ✅ 检索证据：本次共收到 {n_search} 个 web_search_call"
                          f"（{len(search_urls)} 个来源 URL）——模型确实联网查过")
                    for u in search_urls[:5]:
                        print(f"      · {u}")
                else:
                    print("   ⚠ **本次没有任何检索证据**（0 个 web_search_call）："
                          "端点忽略了这个工具，模型只能凭记忆作答。")
                    print("     注意：内置检索工具是否真的生效由端点与模型决定——"
                          "如 DeepSeek 的 deepseek-flash 会忽略它、deepseek-v4-pro 才真检索。")
                    print("     请把本次结果当成**凭记忆**的修订：卷/期/页码/出版地务必人工抽查，"
                          "或把《文献核实任务书》交给编辑自己的联网 AI。")
            n_left = len(entries) - n_try
            if n_got:
                with open(path, "a", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(advice) + "\n")
                print(f"   已追加 {n_got} 条 AI 建议到同一个 markdown（标为「须人工核实」）")
                if fail_streak:
                    print(f"   ⚠ 另有 {fail_streak + n_left} 条没有返回内容"
                          f"（连续 {fail_streak} 条失败后已提前停止，未跑完 {len(entries)} 条）")

                # ── 把 AI 结果**并入 reference.bib**（2026-10-15 用户决定；第 64 条）──
                #
                # 只有拿到了「正常收尾」的回复才谈得上并入（replies 里只收那些）。
                # 一条都没收上（全是截断/内容过滤/结构坏）→ 不碰 bib，并**明说未改动**：
                # 「0 条并入」绝不能长得像成功。
                if args.no_ai_merge:
                    print("   （--no-ai-merge：本次**只出任务书**，AI 结果不并入 reference.bib）")
                elif not replies:
                    print("   ⚠ 没有任何一条 AI 回复可用（截断/内容过滤/没返回内容）："
                          "reference.bib **未改动**，本次只出任务书。")
                else:
                    print("\n   把 AI 结果并入 reference.bib…")
                    prov = channel_provenance(channel, cfg.protocol, cfg.model,
                                              cfg.uses_web_search, n_search)
                    mg = apply_ai_merge(res.bib_path or "", path, replies,
                                        stem, out_dir, rejected=unclean_replies,
                                        provenance=prov)
                    if mg["n_merged"]:
                        print(f"   ✅ 已并入 {mg['n_merged']} 条 / 跳过 {mg['n_skipped']} 条"
                              f"（逐条原因见任务书「六、合并记录」）")
                        if unclean_replies:
                            print(f"   ⚠ 另有 {len(unclean_replies)} 条因返回不完整**未并入**"
                                  "（已在合并记录里注明）")
                        print(f"   回滚副本：{os.path.basename(mg['rollback'])}"
                              "（= 本次合并**之前**的那份 reference.bib）")
                        if mg["report"]:
                            print(f"   {os.path.basename(mg['report'])}：{mg['conclusion']}")
                        print("   ⚠ **这份 reference.bib 已含 AI 结果，请人工核对；"
                              f"原文件见 {os.path.basename(mg['rollback'])}**")
                    else:
                        why = mg["error"] or "没有任何条目通过安全校验"
                        print(f"   ⚠ {why}——本次 reference.bib **未改动**，"
                              "原因已逐条写进任务书「六、合并记录」。")
            else:
                # ⚠️ AI 失败**不影响出稿**：任务书在调用 AI 之前就已经写盘了，
                #    这里只把原因说清楚（编辑器/界面日志都要能看出是「连不上」还是「没配好」）。
                print("   ⚠ AI 调用失败：没有任何一条返回内容。本次**只出任务书**。")
                print(f"     请检查：地址 {cfg.base} 是否可达、密钥是否正确、模型名 {cfg.model} 是否存在。")
                if fail_streak >= 3:
                    print(f"     （连续 {fail_streak} 条失败后已提前停止，共尝试 {n_try}/{len(entries)} 条；"
                          "默认超时 60 秒/条，不再逐条空等）")
                print(f"     《文献核实任务书》已生成：{path}")
                print("     可照常交给编辑自己的 AI 核实；转换产物不受影响。")
        else:
            print("   （未配置 LLM：只出任务书，由编辑交给自己的 AI）")
            print("   （未配置时**不会**并入 reference.bib：bib 与从前逐字节相同）")
            # 检索状态也要说清：没配 LLM 就谈不上本工具联网核实，任务书里也写着这句。
            print("   （未启用联网检索：本工具不联网核实，任务书由编辑的联网 AI 或人工复核）")
            print("   （要自动预跑：界面「设置」页填 API 地址/密钥/模型，"
                  "或命令行 --llm-base/--llm-key/--llm-model、环境变量 SLCN_LLM_*）")

    # ⑨ 编译（**放在 ⑪ 之后**：并进 reference.bib 的 AI 结果要体现在 PDF 里）
    compile_failed = ""
    if args.compile:
        print("\n⑨ 编译")
        ok, info = compile_pdf(out_dir, stem)
        if ok:
            size = os.path.getsize(info) // 1024
            print(f"   ✅ {os.path.basename(info)}（{size} KB）")
            n_pages = pdf_page_count(info)
            if n_pages:
                if args.pages:
                    print(f"   PDF 共 {n_pages} 页（--pages 给的是 {args.pages}）")
                else:
                    print(f"   PDF 共 {n_pages} 页 → 补 --pages <首页>-<末页> 可自动填好页眉与页数")
        else:
            print(f"   ❌ 编译未通过：{info}")
            print("      产物 .tex 已保留，可人工排查后重编。")
            compile_failed = info

    # ⑩ 导出原稿 PDF（供矢量形状人工截图）
    if args.source_pdf and os.path.splitext(source)[1].lower() in (".docx", ".doc"):
        from slcn import office

        print("\n⑩ 导出原稿 PDF（LibreOffice）")
        pdf, msg = office.docx_to_pdf(source, out_dir)
        if pdf:
            final = os.path.join(out_dir, f"{stem}_原稿.pdf")
            if os.path.abspath(pdf) != os.path.abspath(final):
                os.replace(pdf, final)
            print(f"   ✅ {os.path.basename(final)}")
            n_vec = res.warnings and sum(
                1 for w in res.warnings if "矢量形状" in w
            )
            if n_vec:
                print(f"   ⚠ 正文含 {n_vec} 处矢量形状占位，请对照该 PDF 截图或重画")
        else:
            print(f"   ⚠ {msg}")
        office.cleanup_profile(out_dir)

    # ⑫ 清理编译中间文件
    if args.clean:
        print("\n⑫ 清理编译中间文件")
        n, _ = _clean.clean(out_dir, log=lambda s: print(f"   {s}"))
        if n == 0:
            print("   （没有需要清理的文件）")

    print()
    print("═" * 74)
    if args.clean:
        print("   已清理编译中间文件；.log 按 EditToolBox 惯例保留，便于排查问题。")
    print("═" * 74)
    # ⚠️ 契约行必须是 **stdout 的最后一行**（INVARIANTS 三.3 / ENGINE_API 的表格）——
    #    从前它后面还跟着一条收尾横幅，于是"最后一行"实际是 `═`（外壳真按最后一行取数就会
    #    取到横幅；mac 外壳目前是自己记着 `--out` 才没炸）。2026-10-17 逐页体检发现。
    print(f"完成。产物目录：{out_dir}")
    if compile_failed:
        # ⚠️ 契约（`docs/ENGINE_API.md`「退出码」）：**没有 1**；中途某一步失败也算失败、
        #    一律 2 —— 外壳判「成功」只看 0。从前这里无条件 `return 0`，于是"编译没通过"
        #    在界面上照样是 ✅ 完成（2026-10-17 逐页体检实测：LaTeX 通道 `--compile`
        #    xelatex 失败、`PIPESTATUS=0`）。消息按 `die()` 的口径走 stderr；
        #    stdout 的**最后一行仍是契约行**（INVARIANTS 三.3）。
        print(f"\n❌ 转换已完成，但 ⑨ 编译未通过（退出码 2）：{compile_failed}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
