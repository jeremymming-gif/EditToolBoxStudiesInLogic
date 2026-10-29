"""SLCN convert 拆分（B-229）：元数据/语种/作者/输出守卫。纯搬运自 v1.4.0 `Engine/convert.py`，判据/文案/输出未改。"""
from __future__ import annotations

import argparse, inspect, os, re, sys

from slcn import clean as _clean, coop as _coop, naming as N, sources as _sources
from slcn.footnotes import c_class_union_enabled
from slcn.latex import parse_macros

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
    # B-221 C1：Word→初排默认生成的「初排交接单」。不认它的话，第二次跑同一篇稿子
    # 会被自己上次的交接单拦下（「含非本工具的文件」）。
    "_初排交接单.txt",
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


_BACKUP_RE = re.compile(r"^.+\.\d{8}-\d{6}-\d{6}\.bak$")


def is_own_product(name: str, stem: str) -> bool:
    """判断目标目录里的一个条目是不是「本工具产物 / 编译中间文件」。"""
    if name in _OWN_FIXED:
        return True
    # 第 121 条：原子写留下的 `<文件名>.<YYYYmmdd-HHMMSS-ffffff>.bak` 是**工具自己**写的；
    # 不认它的话，第二次跑同一篇稿子会被自己的备份文件拦下（「含非本工具的文件」）。
    if _BACKUP_RE.match(name):
        return True
    # 并入 AI 结果时留下的回滚副本 `reference.bib.ai前`（2026-10-15，第 64 条）：
    # 不认它的话，**第二次跑同一篇稿子会被自己的回滚副本拦下**（「含非本工具的文件」）。
    if name.endswith(".ai前"):
        # 第五十七批：低置信 META/FN 建议改写输出 .tex 前也留 `.ai前`（与 bib 同构）。
        return name[: -len(".ai前")] in ("reference.bib", f"{stem}.bib", f"{stem}.tex")
    if name.lower().endswith(".sty"):
        return True
    if any(name == f"{stem}{suf}" for suf in _OWN_STEM_SUFFIXES):
        return True
    if name == f"{stem}{LLM_FULLTEXT_REPORT_SUFFIX}":
        return True
    # 批74（B-133）：附四 report-only 报告（`<stem>_AI全文分析.md/.jsonl`）也是本工具产物；
    # 不认它的话，第二次跑 `--coop-replies` 会被自己上次交付的报告拦下（「含非本工具的文件」）。
    if any(name == f"{stem}{suf}" for suf in _coop.ANALYSIS_REPORT_NAMES):
        return True
    base, dot, ext = name.rpartition(".")
    if dot and base.lower().startswith("fig") and base[3:].isdigit():
        return ("." + ext.lower()) in _IMG_SUFFIXES
    return name.lower().endswith(_MID_SUFFIXES)


# ── 输入校验 ──────────────────────────────────────────────────────────────


def require_source(path: str) -> str:
    r"""强制校验源文件。这是「不替用户默认填充」的第一道关口。

    口径（第四十二批 A2 起）：本工具**不做源文件挑选**（`--source` 必须显式给出，
    绝不按文件名/大小猜哪份是正文）；但 **LaTeX 前言源可以自动配**——正文片段
    （无 `\documentclass`）且同目录恰有 1 个可信前言文件时，自动当 `--metadata` 用，
    判不准就 warning + 保持旧行为（见 `slcn.sources.discover_latex_sources`）。
    """
    if not path:
        die("必须用 --source 显式指定源文件（.docx 或 .tex）。"
            "本工具不做源文件挑选（LaTeX 前言源可自动配，见 --metadata 说明）。")
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
            #   取前 6 段会把中文块算进去，把英文稿误判成中文稿（实测 02 Ning_Jiang）。
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
            # 第 2 段常是「姓名, 单位, 邮编」（实测 Ning Jiang, Department of Philosophy,
            # Sun Yat-sen University, 510275 共 76 字）。必须**先去单位再判断长度**，
            # 否则整段超长被挡掉，作者就读不出来了。
            # ⚠️ 原稿常见排布是「题名 → 副标题 → 作者」（实测 07 Jiayi_He：
            #   [0] 题名  [1] ——评示例归纳论者…  [2] 何嘉怡 徐强）。
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
                # 多作者用空格分隔（`何嘉怡 徐强`）时取第一个；
                # 但中文姓名内部也可能带空格（`陈  松`），用长度区分：
                # 折叠空白后不足 5 字的一律视为「一个名字」。
                collapsed = re.sub(r"\s+", " ", a).strip()
                if _has_cjk(a) and " " in collapsed and len(collapsed.replace(" ", "")) >= 5:
                    a = collapsed.split(" ")[0]
                else:
                    a = collapsed
                a = a.strip(" .、—-")
                # 英文作者行常带单位序号（`Jing Niu 1`、`Chen Xu1`）：尾部数字不是姓名
                # 的一部分，先去掉再做可信度判断（否则会被误判成「不像人名」而继续找，
                # 反而落到下一段正文上）。
                a = re.sub(r"[\s\d]+$", "", a).strip()
                # 第 141 条：加人名可信度闸门——旧判据只看长度/句尾标点，会把正文句
                # `贝叶斯主义认为…` 或摘要行剩下的单字 `摘` 当作者（期 87 子案例 2/3）。
                # 不是人名就继续找下一段；全都不是 → 返回空串，由上层报错要求 --name。
                if (a and len(a) <= 40 and not a.endswith(("。", "：", ":"))
                        and N.looks_like_person_name(a)):
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


# ── 第 76 条：英文元数据第二源定位（**同样只在同目录下查找**）──────────────
#
# 用户实测（2026年第5期(90)/10 Lei_Li）：正文 docx 里只有中文，英文题名/摘要/作者
# 就在同目录的 `当代逻辑论题鸟瞰英文摘要.docx` 里，引擎一直读得出来，却从来没接进
# Word 通道 → 编辑只能在界面「正文信息覆盖」里手工粘贴。
#
# 纪律（与 `find_bib()` 一字不差的口径）：
#   · **只看源文件所在的那一个目录**，不递归、不跨目录搜索；找不到返回 None，
#     照旧写占位符，绝不猜。
#   · 判据是**内容**（`sources.en_metadata_sheet()`：有英文字段 + 没有叙述正文），
#     不是文件名——所以同目录的另一篇英文全文稿、中文稿、缩写表都不会被选中。
#
# ── B-240②（2026-10-27）：同目录 refs/bibitem 附件不许当英文元数据第二源 ──────
#
# 实测（B-240 p0，B-233 E2E 复现件）：main 与参考文献附件同目录时，附件
# `body=95`（< 200，过了「没有叙述正文」判据）、`title_en` 由 `\bibitem{a1}` 那一行
# 误解析（`'\bibitem{a1} Smith J, …'`），而 `parse_docx(附件).refs` 抽出 **0 条**
# → 旧反判据「文献结构非空」**打不中**，附件被当成「英文摘要」选中。
#
# 补三条反判据（**宁漏不误**：判不了就不选、照旧写占位符，绝不硬猜）：
#   (a) `title_en` 可辨识——以 `\bibitem` / `[n]` / `(n)` 这类文献行标记开头的，
#       不是英文题名；
#   (b) 英文字段 ≥2 项，或含 `abstract_en`/`keywords_en`（**只有一项 `title_en` 不选**）；
#   (c) 同目录里被 `multisource.scan()` 判为参考文献角色（`role == "refs"`）的文件
#       ——`attachment_kind()` / `select_refs_attachment()` 消费的就是这套角色，
#       故 refs/bibitem 附件在这里被一律排除。
_REFS_LINE_MARK = re.compile(r"^\s*(?:\\bibitem\b|\[\s*\d+\s*\]|\(\s*\d+\s*\))")


def _en_metadata_fields_ok(fields: dict[str, str]) -> bool:
    """B-240② 反判据 (a)(b)：这份 docx 的英文字段像不像「一张英文元数据表」。"""
    title = (fields.get("title_en") or "").strip()
    if title and _REFS_LINE_MARK.match(title):
        return False                      # (a) 题名其实是 `\bibitem{a1} …` 之类的文献行
    if len(fields) >= 2:
        return True                       # (b) 至少两项英文字段
    return bool((fields.get("abstract_en") or "").strip()
                or (fields.get("keywords_en") or "").strip())  # (b) 单项但够硬


def _refs_role_paths(directory: str) -> set[str]:
    """B-240② 反判据 (c)：同目录里被 `multisource.scan()` 判成参考文献的文件（绝对路径）。

    角色判定失败（异常/目录不可读）时返回空集：**不阻断**第二源发现，
    内容判据 (a)(b) 仍然生效。
    """
    try:
        from . import multisource as _multisource

        return {os.path.abspath(s.path) for s in _multisource.scan(directory)
                if s.role == "refs"}
    except Exception:  # noqa: BLE001
        return set()


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
    refs_roles: set[str] | None = None
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".docx") or f.startswith("~$"):
            continue
        p = os.path.join(d, f)
        if not os.path.isfile(p) or os.path.abspath(p) == src:
            continue
        fields = _sources.en_metadata_sheet(p)
        if not fields or not _en_metadata_fields_ok(fields):
            continue
        # (c) 角色反判据：refs/bibitem 附件即便「像」英文表也不选（B-240②）。
        if refs_roles is None:
            refs_roles = _refs_role_paths(d)
        if os.path.abspath(p) in refs_roles:
            continue
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


# ── 结构化人工元数据（--metadata-json，批63b）──────────────────────────────
#
# GUI「填写元数据」表单（用户 2026-10-20 拍板：取消 .doc/.docx 自动识别，改人工
# 填写）→ 临时 JSON → 本模块校验/归一化 → build 侧 `structured=` 关键字。结构
# （全部可选；空串/空数组 = 未提供 = 不覆盖）::
#
#     {"schema_version": 1,
#      "title_cn": "", "title_en": "",
#      "running_title_cn": "", "running_title_en": "",
#      "abstract_cn": "", "abstract_en": "",
#      "keywords_cn": "", "keywords_en": "",
#      "authors": [{"name_cn": "", "name_en": "",
#                   "affiliations_cn": [""], "affiliations_en": [""],
#                   "email": ""}]}
#
# 作者 1–5（模板 myfirstauthor…myfifthauthor 上限）；多单位用数组，build 侧按
# 模板口径用 `\\` 连接（sty 把单位排在 minipage/footnote 里）。缺字段仍由 build
# 写「【未识别…】」占位；优先级**高于** --metadata 元数据文件与旧 7 个覆盖参数。
# 坏 JSON / 不存在 / 类型错 = exit 2（die），绝不静默继续。
class MetadataJSONError(ValueError):
    """`--metadata-json` 读取/校验失败：调用方 `die()` → exit 2。"""


#: 文章级标量键（值只收字符串；空串=未提供）。
_METADATA_JSON_SCALARS = (
    "title_cn", "title_en", "running_title_cn", "running_title_en",
    "abstract_cn", "abstract_en", "keywords_cn", "keywords_en",
)
#: 单个作者的键。
_METADATA_JSON_AUTHOR_KEYS = (
    "name_cn", "name_en", "email", "affiliations_cn", "affiliations_en",
)
#: 模板作者槽位上限（myfirstauthor…myfifthauthor）。
_MAX_JSON_AUTHORS = 5


def _json_clean(value) -> str:
    """折叠空白（含换行）：`\\newcommand` 参数不能带空行。"""
    return " ".join(str(value or "").split())


def _json_affil_list(value, where: str) -> list[str]:
    """单位字段：字符串/字符串数组/None → 非空字符串列表；其它类型明确报错。"""
    if value is None:
        return []
    if isinstance(value, str):
        s = _json_clean(value)
        return [s] if s else []
    if not isinstance(value, (list, tuple)):
        raise MetadataJSONError(f"--metadata-json 的 {where} 必须是字符串数组")
    out: list[str] = []
    for k, item in enumerate(value):
        if item is None:
            continue
        if not isinstance(item, str):
            raise MetadataJSONError(f"--metadata-json 的 {where}[{k}] 必须是字符串")
        s = _json_clean(item)
        if s:
            out.append(s)
    return out


def _normalize_json_author(raw, idx: int) -> tuple[dict, list[str]]:
    """一位作者 → (归一化 dict, 未识别键列表)。"""
    if not isinstance(raw, dict):
        raise MetadataJSONError(f"--metadata-json 的 authors[{idx}] 必须是对象")
    unknown = sorted(k for k in raw if k not in _METADATA_JSON_AUTHOR_KEYS)
    author: dict = {}
    for key in ("name_cn", "name_en", "email"):
        v = raw.get(key)
        if v is None:
            author[key] = ""
        elif isinstance(v, str):
            author[key] = _json_clean(v)
        else:
            raise MetadataJSONError(
                f"--metadata-json 的 authors[{idx}].{key} 必须是字符串")
    author["affiliations_cn"] = _json_affil_list(
        raw.get("affiliations_cn"), f"authors[{idx}].affiliations_cn")
    author["affiliations_en"] = _json_affil_list(
        raw.get("affiliations_en"), f"authors[{idx}].affiliations_en")
    return author, unknown


def load_metadata_json(path: str) -> tuple[dict | None, list[str]]:
    """读 + 校验 + 归一化 `--metadata-json`；返回 (归一化 dict 或 None, 警告列表)。

    None = 没有可写入的字段（空对象/全空）→ 调用方按「未提供」处理（产物与
    没有这个参数一致，只在日志里多一行提示）。格式问题一律抛
    `MetadataJSONError`（调用方 die → exit 2）。
    """
    import json as _json

    p = os.path.abspath(path)
    if not os.path.exists(p):
        raise MetadataJSONError(f"--metadata-json 指定的文件不存在：{p}")
    try:
        with open(p, encoding="utf-8-sig") as f:
            raw_text = f.read()
    except OSError as e:
        raise MetadataJSONError(
            f"--metadata-json 读取失败：{type(e).__name__}: {e}") from e
    try:
        data = _json.loads(raw_text)
    except _json.JSONDecodeError as e:
        raise MetadataJSONError(
            f"--metadata-json 不是合法 JSON"
            f"（{os.path.basename(p)}:{e.lineno}:{e.colno}）：{e.msg}") from e
    if not isinstance(data, dict):
        raise MetadataJSONError("--metadata-json 顶层必须是 JSON 对象（{…}）")

    warnings: list[str] = []
    _known = _METADATA_JSON_SCALARS + ("schema_version", "authors")
    unknown = sorted(k for k in data if k not in _known)
    if unknown:
        warnings.append(
            "--metadata-json 里有未识别的键（本次忽略）：" + "、".join(unknown))
    if data.get("schema_version") is not None and data.get("schema_version") != 1:
        warnings.append(
            f"--metadata-json 的 schema_version={data.get('schema_version')!r} "
            "不是 1（仍按 1 解析）")

    out: dict = {}
    for key in _METADATA_JSON_SCALARS:
        v = data.get(key)
        if v is None:
            continue
        if not isinstance(v, str):
            raise MetadataJSONError(f"--metadata-json 的 {key} 必须是字符串")
        if _json_clean(v):
            out[key] = _json_clean(v)

    raw_authors = data.get("authors")
    if raw_authors is None:
        raw_authors = []
    if not isinstance(raw_authors, (list, tuple)):
        raise MetadataJSONError("--metadata-json 的 authors 必须是数组")
    authors: list[dict] = []
    for idx, item in enumerate(raw_authors):
        author, a_unknown = _normalize_json_author(item, idx)
        if a_unknown:
            warnings.append(
                f"--metadata-json 的 authors[{idx}] 有未识别的键（本次忽略）："
                + "、".join(a_unknown))
        authors.append(author)
    while authors and not any(authors[-1].values()):
        authors.pop()
    for idx, author in enumerate(authors):
        if not any(author.values()):
            raise MetadataJSONError(
                f"--metadata-json 的第 {idx + 1} 位作者为空，但后面还有作者："
                "作者槽位不能跳号（请删除空行或补全前一位作者）")
    if len(authors) > _MAX_JSON_AUTHORS:
        raise MetadataJSONError(
            f"--metadata-json 给了 {len(authors)} 位作者：模板最多 "
            f"{_MAX_JSON_AUTHORS} 位（myfirstauthor…myfifthauthor），请删减")
    if authors:
        out["authors"] = authors
    if not out:
        warnings.append(
            "--metadata-json 没有可写入的字段 / 作者（按未提供处理）")
        return None, warnings
    return out, warnings


def supports_structured(fn) -> bool:
    """探测 build 入口是否接受 `structured=` 关键字（老引擎兼容，同 overrides）。"""
    try:
        return "structured" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def structured_kwargs(fn, structured: dict | None) -> dict:
    """按入口是否支持 `structured=` 组装关键字；不支持就提示后不传（不崩）。"""
    if not structured:
        return {}
    if supports_structured(fn):
        return {"structured": structured}
    print(f"  ⚠ {getattr(fn, '__name__', 'build')} 尚不支持 structured 参数，"
          "本次人工元数据未生效（需 build.py 增加 structured: dict | None = None）")
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


def resolve_c_class_union(args) -> bool:
    """C 类新口径最终取值（第五十二批 phaseB）：None/缺省 → 默认 ON；False → 旧行为。

    `--c-class-union`（store_true，default=None）显式开；`--no-c-class-union`
    （store_false）回退。解析交给 `footnotes.c_class_union_enabled()`（唯一总开关），
    命令行这一层**不硬编码默认值**。
    """
    return c_class_union_enabled(getattr(args, "c_class_union", None))
# 兼容垫片：`_ensure_vendor()` 以 `__file__` 定位 Engine/vendor（原文件在 Engine/ 下）。
globals()["__file__"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "convert.py")
